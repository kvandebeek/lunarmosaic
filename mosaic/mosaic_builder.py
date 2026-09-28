#!/usr/bin/env python3
"""Build a mosaic from overlapping panels.

Implemented so far (build phase 2): stages 0–3. Preflight, proxies + features, pair discovery and
global similarity alignment, with a live preview and a layout report. Stages 4–8 (fine alignment,
photometry, region selection, blending, background/colour, output) follow in phases 3–4.

    python3 mosaic_builder.py <folder|manifest.json> [...] -o mosaic.tif [--no-preview]
"""
import argparse
import json
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_link  # noqa: E402   (puts the LunarAtlas checkout on sys.path)
import tool_settings as ts  # noqa: E402
import mosaic_common as mc  # noqa: E402
import mosaic_stages as mst  # noqa: E402
import preview_style as ps  # noqa: E402

N_STAGES = 8
IMPLEMENTED_UP_TO = 8
# movie length per step (seconds, including the step's title card), your pacing of 2026-09-27
FEATURE_REVEAL_SECONDS = 1.8   # step 2, per panel: features appear small -> large,
FEATURE_HOLD_SECONDS = 0.7     #   then the complete view holds,
FEATURE_FADE_SECONDS = 0.5     #   then the panel fades to black
FEATURE_PANELS = 2
CARD_SECONDS = 1.2
VIDEO_SECONDS = {"opening": 2, 0: 2,
                 1: CARD_SECONDS + FEATURE_PANELS * (FEATURE_REVEAL_SECONDS + FEATURE_HOLD_SECONDS + FEATURE_FADE_SECONDS),
                 2: 9, 3: 8, 4: 5, 5: 5, 6: 5, 7: 5, 8: 5, "final": 4}      # 57.2 s (social media ≤ 60)
PAIR_SHOWCASE = 9      # step 3 shows 9 pairs, each animated: keypoints -> matches -> where they overlap
FADE_SECONDS = 1.0     # the final result fades in from the finishing step (part of the "final" budget)
MOVIE_TARGET_SECONDS = 60      # a fade-out to black fills the movie up to this length (min 0.5 s)


def fadeout_seconds():
    return max(0.5, MOVIE_TARGET_SECONDS - sum(VIDEO_SECONDS.values()))
STEP1_CARD_SECONDS = 0.8    # step 1 (inputs): short card, then a contact sheet of all panels
SHOWCASE = 30          # steps 2 and 3 show only the 30 highest-quality panels / pairs
FPS = 30               # movies: 30 fps, so 0.5 s / 0.7 s timings are whole frames and fades are smooth


def step_frames(stage):
    """Frames available for a step's content (its budget minus the title card)."""
    return int(round(VIDEO_SECONDS[stage] * FPS)) - int(round(CARD_SECONDS * FPS))


def split_frames(total, weights):
    """Integer frame counts proportional to weights that sum exactly to total (largest remainder)."""
    exact = [total * w / sum(weights) for w in weights]
    out = [int(e) for e in exact]
    for i in sorted(range(len(exact)), key=lambda i: exact[i] - out[i], reverse=True)[:total - sum(out)]:
        out[i] += 1
    return out


def spread_frames(total, n):
    """Frames per item for n items in a budget of total frames: spread evenly, or sample evenly when n > total."""
    if n <= 0:
        return []
    out = [0] * n
    if n <= total:
        base, extra = divmod(total, n)
        out = [base] * n
        for j in range(extra):
            out[int((j + 0.5) * n / extra)] += 1
    else:
        for j in range(total):
            out[int((j + 0.5) * n / total)] += 1
    return out


def hold(frames):
    """Frames -> seconds for Preview.show(record=...); None when nothing is recorded."""
    return frames / FPS if frames > 0 else None


FRIENDLY = {0: "Checking the inputs", 1: "Finding features", 2: "Matching overlaps", 3: "Placing panels",
            4: "Fine alignment", 5: "Matching brightness", 6: "Choosing the sharpest regions", 7: "Blending", 8: "Finishing"}


class Abort(Exception):
    pass


# ---------------------------------------------------------------- movie recording

class Recorder:
    """Writes the styled preview frames to an H.264 MP4 (1920×1080, 15 fps) on a background thread.

    hold = seconds a frame stays on screen, so the clip is paced for a viewer rather than wall-clock time."""
    FPS = FPS

    def __init__(self, path, size=(ps.W, ps.H)):
        self.path = path
        self.W, self.H = size
        self.q = queue.Queue(maxsize=64)
        self.frames = 0
        self.step = "opening"
        self.per_step = Counter()                  # frames written per step (for the timing summary)
        self.vw = None
        self.error = None
        for fourcc in ("avc1", "mp4v"):
            vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc), self.FPS, (self.W, self.H))
            if vw.isOpened():
                self.vw, self.codec = vw, fourcc
                break
        if self.vw is None:
            raise IOError(f"cannot open a video writer for {path}")
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def add(self, frame, hold=0.2):
        if frame.shape[:2] != (self.H, self.W):
            frame = cv2.resize(frame, (self.W, self.H), interpolation=cv2.INTER_AREA)
        n = max(1, int(round(hold * self.FPS)))
        self.per_step[self.step] += n
        self.q.put((frame, n))

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            if self.error is not None:          # keep draining, so add() never blocks on a dead writer
                continue
            try:
                frame, n = item
                for _ in range(n):
                    self.vw.write(frame)
                self.frames += n
            except Exception as e:
                self.error = e

    def close(self):
        self.q.put(None)
        self.t.join()
        self.vw.release()
        if self.error is not None:
            raise IOError(f"movie {self.path}: {self.error}")
        return self.frames / self.FPS


def open_recorders(stg, args):
    """[(Recorder, style)] for --record / --record-vertical; if one cannot open, the other is closed and discarded."""
    outputs = []
    try:
        if args.record:
            outputs.append((Recorder(stg.path(args.record), (1920, 1080)), ps.Observatory(size=(1920, 1080))))
        if args.record_vertical:
            outputs.append((Recorder(stg.path(args.record_vertical), (1080, 1920)), ps.Observatory(size=(1080, 1920))))
    except IOError:
        for rec, _ in outputs:
            rec.close()
        stg.discard()
        sys.exit(f"cannot write a movie to {args.record_vertical if outputs else args.record}")
    return outputs


# ---------------------------------------------------------------- preview (main thread only on macOS)

class Preview:
    """cv2 window driven from the main thread; the pipeline posts frames from a worker thread.

    Keys: p pause/resume · q abort cleanly · 1/2 switch view (composite / overlay) · any key closes at the end.
    """

    def __init__(self, enabled, window_style, outputs=(), max_side=1600):
        """outputs: [(Recorder, style)], e.g. a 16:9 and a 9:16 movie, each rendered in its own format."""
        self.enabled = enabled
        self.style = window_style
        self.outputs = list(outputs)
        self.max_side = max_side
        self.frames = queue.Queue(maxsize=4)
        self.views = {}
        self.view = "2"
        self.paused = threading.Event()
        self.abort = threading.Event()
        self._last = 0.0
        self.pending_card = None
        self.worker = None

    def styles(self):
        out = [self.style]
        for _, st in self.outputs:
            if st not in out:
                out.append(st)
        return out

    def set_all(self, attr, value):
        for st in self.styles():
            setattr(st, attr, value)

    def active(self):
        return self.enabled or bool(self.outputs)

    def show(self, render, record=None, min_interval=0.0, key="1", replace=True, window=True):
        """render(style) -> frame. record: seconds the frame is held in every movie (None = not recorded).
        The window gets a frame when min_interval has passed. Rendering only happens when something needs it."""
        cache = {}

        def frame_for(st):
            if id(st) not in cache:
                cache[id(st)] = render(st)
            return cache[id(st)]
        if record is not None:
            for rec, st in self.outputs:
                rec.add(frame_for(st), record)
        if window and self.enabled and time.time() - self._last >= min_interval:
            self._last = time.time()
            try:
                self.frames.put_nowait((key, frame_for(self.style), replace))
            except queue.Full:
                pass

    def exit(self, code):
        """sys.exit, unless the pipeline thread is still running (second Ctrl-C, or no checkpoint reached in time):
        then leave without interpreter shutdown, which would crash on that thread's pending output."""
        if self.worker is not None and self.worker.is_alive():
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(code)
        sys.exit(code)

    def checkpoint(self):
        """Called by the pipeline between work items: honours pause and abort."""
        while self.paused.is_set() and not self.abort.is_set():
            time.sleep(0.1)
        if self.abort.is_set():
            raise Abort()

    def _fit(self, img):
        h, w = img.shape[:2]
        s = min(1.0, self.max_side / max(h, w))
        return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img

    def run(self, target):
        """Run target() in a worker thread while this (main) thread drives the window."""
        result = {}

        def work():
            try:
                result["value"] = target()
            except BaseException as e:  # re-raised on the main thread
                result["error"] = e
        t = threading.Thread(target=work, daemon=True)
        self.worker = t
        t.start()
        try:
            if not self.enabled:
                t.join()
            else:
                cv2.namedWindow("Lunar Mosaic", cv2.WINDOW_AUTOSIZE)
                while t.is_alive():
                    self._pump()
                self._pump()
                hold_until = None if sys.stdin.isatty() else time.time() + 3
                print("      preview: press any key in the window to close" if hold_until is None else "", end="", flush=True)
                while True:
                    k = self._pump(wait=50)
                    if (k != -1 and k not in (ord("1"), ord("2"))) or (hold_until and time.time() > hold_until):
                        break
                cv2.destroyAllWindows()
                cv2.waitKey(1)
                print()
        except KeyboardInterrupt:
            # Ctrl-C lands in this (main) thread: stop the pipeline at its next checkpoint rather than leaving it
            # running, because a thread still printing while Python shuts down crashes the interpreter
            self.abort.set()
            self.paused.clear()
            print("\n      stopping at the next safe point (Ctrl-C again to quit at once)", flush=True)
            try:
                t.join(60)
            except KeyboardInterrupt:
                pass
            raise
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _pump(self, wait=30):
        try:
            while True:
                key, img, replace = self.frames.get_nowait()
                self.views = {key: img} if replace else {**self.views, key: img}
        except queue.Empty:
            pass
        if self.views:
            img = self.views.get(self.view) if self.view in self.views else next(iter(self.views.values()))
            cv2.imshow("Lunar Mosaic", self._fit(img))
        k = cv2.waitKey(wait)
        if k == ord("p"):
            if self.paused.is_set():
                self.paused.clear()
            else:
                self.paused.set()
            print("      " + ("paused (p to resume)" if self.paused.is_set() else "resumed"), flush=True)
        elif k == ord("q"):
            self.abort.set()
        elif k in (ord("1"), ord("2")):
            self.view = chr(k)
        return k


# ---------------------------------------------------------------- inputs

def gather_inputs(args):
    """Paths from folders and/or manifests; rejects and --exclude-tier are applied when a manifest is given."""
    paths, info = [], {}
    manifests = [p for p in args.inputs if p.lower().endswith(".json")]
    others = [p for p in args.inputs if not p.lower().endswith(".json")]
    if args.manifest:
        manifests.append(args.manifest)
    excl = {t.strip() for t in (args.exclude_tier or "").split(",") if t.strip()}
    for mpath in manifests:
        man = json.load(open(mpath))
        for im in man["images"]:
            info[im["path"]] = im
            if mpath in args.inputs:
                paths.append(im["path"])
    if others:
        paths += mc.find_images(others, recursive=False)
    paths = sorted(set(paths), key=lambda p: (os.path.basename(p), p))
    kept, dropped = [], []
    for p in paths:
        im = info.get(p)
        if im and im.get("reject"):
            dropped.append((p, f"manifest reject: {im.get('reject_reason')}"))
        elif im and im.get("tier") in excl:
            dropped.append((p, f"excluded tier {im['tier']}"))
        else:
            kept.append(p)
    return kept, dropped, info


def display_quality(paths, args, tenengrad):
    """Quality used to choose what the movie shows (never to exclude panels): the classifier's same-terrain
    relative detail when a manifest is given or found next to the panels, otherwise single-image Tenengrad."""
    cands = [args.manifest] if args.manifest else []
    for d in {os.path.dirname(p) for p in paths}:
        cands += [os.path.join(d, "panel_manifest.json"), os.path.join(d, "manifest.json")]
    for c in cands:
        if c and os.path.exists(c):
            try:
                rd = {os.path.abspath(i["path"]): i["metrics"].get("relative_detail") for i in json.load(open(c))["images"]}
            except Exception:
                continue
            q = {p: rd.get(p) for p in paths}
            if sum(v is not None for v in q.values()) >= 0.9 * len(paths):
                return {p: (v if v is not None else 0.0) for p, v in q.items()}, f"relative detail from {os.path.basename(c)}"
    return {p: tenengrad.get(p, 0.0) for p in paths}, "single-image Tenengrad"


def header_size(path):
    """(width, height, channels, dtype) without decoding pixels where possible (Pillow if present)."""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            bits = im.tag_v2.get(258) if hasattr(im, "tag_v2") else None
            bits = bits[0] if isinstance(bits, tuple) else bits
            ch = len(im.getbands())
            return im.size[0], im.size[1], ch, {8: "uint8", 16: "uint16", 32: "float32"}.get(bits, im.mode)
    except Exception:
        im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if im is None:
            raise IOError("unreadable image")
        return im.shape[1], im.shape[0], 1 if im.ndim == 2 else im.shape[2], str(im.dtype)


def preview_possible():
    """Checked before the window is created (opening it without a display fails in the main thread)."""
    if has_display():
        return True
    print("      WARN no display detected: continuing without preview", flush=True)
    return False


def has_display():
    return sys.platform == "darwin" and not os.environ.get("SSH_CONNECTION") or bool(os.environ.get("DISPLAY"))


# ---------------------------------------------------------------- stage 3: global similarity solve

def centred(pts, size):
    w, h = size
    return pts - np.array([(w - 1) / 2, (h - 1) / 2])


def J(pts):
    """Jacobian of P(x) = [[a,-b],[b,a]] x + t with respect to (a, b, tx, ty), for n points: (n, 2, 4)."""
    n = len(pts)
    Jm = np.zeros((n, 2, 4))
    Jm[:, 0, 0], Jm[:, 0, 1], Jm[:, 0, 2] = pts[:, 0], -pts[:, 1], 1
    Jm[:, 1, 0], Jm[:, 1, 1], Jm[:, 1, 3] = pts[:, 1], pts[:, 0], 1
    return Jm


def apply_pose(theta, pts):
    a, b, tx, ty = theta
    return np.stack([a * pts[:, 0] - b * pts[:, 1] + tx, b * pts[:, 0] + a * pts[:, 1] + ty], axis=1)


def solve_global(nodes, pairs, ref, huber=1.5, iters=8):
    """Similarity pose per node from point correspondences, reference fixed by elimination.

    Residual r = P_a(x_a) − P_b(x_b). Normal equations are accumulated per pair (4×4 blocks), so the
    system is 4(N−1) unknowns regardless of the number of points. IRLS with Huber weights."""
    idx = {n: k for k, n in enumerate(nodes)}
    N = len(nodes)
    theta = np.tile([1.0, 0, 0, 0], (N, 1))
    free = [k for k in range(N) if k != idx[ref]]
    col = {k: j for j, k in enumerate(free)}
    weights = [np.ones(len(p["xa"])) for p in pairs]
    for it in range(iters):
        H = np.zeros((4 * (N - 1), 4 * (N - 1)))
        g = np.zeros(4 * (N - 1))
        for p, w in zip(pairs, weights):
            ia, ib = idx[p["a"]], idx[p["b"]]
            Ja, Jb = p["Ja"], p["Jb"]
            blocks = {(ia, ia): np.einsum("nki,nkj,n->ij", Ja, Ja, w),
                      (ib, ib): np.einsum("nki,nkj,n->ij", Jb, Jb, w),
                      (ia, ib): -np.einsum("nki,nkj,n->ij", Ja, Jb, w)}
            blocks[(ib, ia)] = blocks[(ia, ib)].T
            for (r, c), B in blocks.items():
                if r in col and c in col:
                    H[4 * col[r]:4 * col[r] + 4, 4 * col[c]:4 * col[c] + 4] += B
                elif r in col:          # c is the fixed reference: move to the right-hand side
                    g[4 * col[r]:4 * col[r] + 4] -= B @ theta[c]
        sol = np.linalg.solve(H, g)
        for k in free:
            theta[k] = sol[4 * col[k]:4 * col[k] + 4]
        # Huber reweighting on residual norms
        new_w = []
        for p in pairs:
            r = apply_pose(theta[idx[p["a"]]], p["xa"]) - apply_pose(theta[idx[p["b"]]], p["xb"])
            e = np.hypot(r[:, 0], r[:, 1])
            new_w.append(np.where(e <= huber, 1.0, huber / np.maximum(e, 1e-9)))
        if it > 0 and max(np.abs(a - b).max() for a, b in zip(new_w, weights)) < 1e-3:
            weights = new_w
            break
        weights = new_w
    return theta, idx


def pair_residuals(theta, idx, pairs, key="xb"):
    """Per-pair residual norms. key="xb": matched keypoints (includes keypoint jitter);
    key="xb_fit": the pair's own fitted transform (layout error only)."""
    out = []
    for p in pairs:
        r = apply_pose(theta[idx[p["a"]]], p["xa"]) - apply_pose(theta[idx[p["b"]]], p[key])
        out.append(np.hypot(r[:, 0], r[:, 1]))
    return out


def largest_component(nodes, edges):
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    groups = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return sorted(groups.values(), key=len, reverse=True)


def footprint(theta, size):
    w, h = size
    c = centred(np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64) - 0.5, size)
    return apply_pose(theta, c)


# ---------------------------------------------------------------- drawing

def layout_views(nodes, theta, idx, sizes, proxies, f, offset, canvas, per_panel_rms, scale, label="proxy-level"):
    """Composite (mean of all panels: misregistration shows as doubled craters) and outline overlay."""
    Wc, Hc = int(canvas[0] * scale) + 2, int(canvas[1] * scale) + 2
    acc = np.zeros((Hc, Wc), np.float32)
    wsum = np.zeros((Hc, Wc), np.float32)
    for n in nodes:
        th = theta[idx[n]]
        w, h = sizes[n]
        a, b, tx, ty = th
        # proxy px -> full px: x_f = f x_p + (f-1)/2 ; full px -> centred ; pose ; + offset ; * scale
        Cp = np.array([[f, 0, (f - 1) / 2 - (w - 1) / 2], [0, f, (f - 1) / 2 - (h - 1) / 2], [0, 0, 1]])
        Pm = np.array([[a, -b, tx - offset[0]], [b, a, ty - offset[1]], [0, 0, 1]])
        M = (np.diag([scale, scale, 1]) @ Pm @ Cp)[:2]
        L = proxies[n]["lum"]
        wm = np.zeros_like(L)
        t = max(1, proxies[n]["crop"] // f)      # measured stacking-edge artefact crop
        wm[t:-t, t:-t] = 1
        acc += cv2.warpAffine(L * wm, M, (Wc, Hc), flags=cv2.INTER_LINEAR)
        wsum += cv2.warpAffine(wm, M, (Wc, Hc), flags=cv2.INTER_LINEAR)
    comp = acc / np.maximum(wsum, 1e-6)
    hi = np.percentile(comp[wsum > 0], 99.7) if (wsum > 0).any() else 1
    comp8 = np.clip(comp / max(hi, 1e-6) * 255, 0, 255).astype(np.uint8)
    base = cv2.cvtColor(comp8, cv2.COLOR_GRAY2BGR)
    over = (base * 0.55).astype(np.uint8)
    for n in nodes:
        q = (footprint(theta[idx[n]], sizes[n]) - offset) * scale
        e = per_panel_rms.get(n, 0)
        col = (60, 200, 60) if e < 0.5 else (0, 190, 255) if e < 1.0 else (60, 60, 230)
        cv2.polylines(over, [q.astype(np.int32)], True, col, 1, cv2.LINE_AA)
        c = q.mean(axis=0)
        cv2.putText(over, n, (int(c[0]) - 22, int(c[1]) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA)
    txt = f"{len(nodes)} panels - layout residual: green < 0.5 px, amber < 1 px, red >= 1 px ({label})"
    for img in (base, over):
        cv2.putText(img, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
    return {"1": base, "2": over}


def show_layout(pv, main, pairs, ref, theta, idx, sizes, proxies, f, offset, canvas, per_rms, rot, rms):
    """Stage 3 in the preview/movie: panels appear breadth-first from the reference, then the final layout."""
    # composite resolution: enough for the largest output format (each style scales it into its own area)
    fs = max(min(aw / canvas[0], ah / canvas[1]) for aw, ah in (st.area for st in pv.styles()))
    Wc, Hc = int(canvas[0] * fs) + 2, int(canvas[1] * fs) + 2
    nbrs = {n: set() for n in main}
    for p in pairs:
        nbrs[p["a"]].add(p["b"])
        nbrs[p["b"]].add(p["a"])
    order, seen, frontier = [], {ref}, [ref]
    while frontier:
        order += frontier
        nxt = sorted({m for n in frontier for m in nbrs[n]} - seen)
        seen |= set(nxt)
        frontier = nxt
    # one white point for the whole animation, so brightness does not jump as panels arrive
    wp = float(np.median([np.percentile(proxies[n]["lum"][proxies[n]["lum"] > 0.01], 99.8) for n in main]))
    tone = lambda c: (np.clip(c / wp, 0, 1) ** (1 / 1.1) * 255).astype(np.uint8)

    def comp(acc, wsum):
        c = acc / np.maximum(wsum, 1e-6)
        c[wsum < 0.5] = 0                  # slivers at coverage edges would show as bright dashes
        return tone(c)
    quads = {n: (footprint(theta[idx[n]], sizes[n]) - offset) * fs for n in main}
    readouts = lambda k: [("placed", f"{k} / {len(order)}"), ("layout rms", f"{rms:.2f} px"), ("pairs", f"{len(pairs)}"),
                          ("rotation", f"{min(rot.values()):+.2f}…{max(rot.values()):+.2f}°")]
    acc = np.zeros((Hc, Wc), np.float32)
    wsum = np.zeros((Hc, Wc), np.float32)
    f_anim, f_clean, f_resid = split_frames(step_frames(3), [5.8, 1.5, 1.5])
    plan = spread_frames(f_anim, len(order))
    for k, n in enumerate(order, 1):
        t = max(1, proxies[n]["crop"] // f)               # measured stacking-edge artefact crop
        w, h = sizes[n]
        a, b, tx, ty = theta[idx[n]]
        Cp = np.array([[f, 0, (f - 1) / 2 - (w - 1) / 2], [0, f, (f - 1) / 2 - (h - 1) / 2], [0, 0, 1]])
        Pm = np.array([[a, -b, tx - offset[0]], [b, a, ty - offset[1]], [0, 0, 1]])
        M = (np.diag([fs, fs, 1]) @ Pm @ Cp)[:2]
        L = proxies[n]["lum"]
        wm = np.zeros_like(L)
        wm[t:-t, t:-t] = 1
        # shrink with area filtering first (warpAffine has no area mode; linear alone would alias and sparkle)
        r = min(1.0, fs * f * 1.5)
        Ls = cv2.resize(L * wm, None, fx=r, fy=r, interpolation=cv2.INTER_AREA)
        ws = cv2.resize(wm, (Ls.shape[1], Ls.shape[0]), interpolation=cv2.INTER_AREA)
        Mr = M @ np.array([[L.shape[1] / Ls.shape[1], 0, 0], [0, L.shape[0] / Ls.shape[0], 0], [0, 0, 1]])
        acc += cv2.warpAffine(Ls, Mr, (Wc, Hc), flags=cv2.INTER_LINEAR)
        wsum += cv2.warpAffine(ws, Mr, (Wc, Hc), flags=cv2.INTER_LINEAR)
        outl = [(quads[m], (46, 46, 46), 1) for m in order[:k - 1]] + [(quads[n], ps.AMBER, 2)]
        comp_k = {}

        def render(st):
            if "c" not in comp_k:
                comp_k["c"] = comp(acc, wsum)
            return st.layout(comp_k["c"], outl, 3, FRIENDLY[3], f"{k} of {len(order)} · newest {n}", k / len(order), readouts(k))
        pv.show(render, record=hold(plan[k - 1]) if pv.outputs else None, min_interval=0.05)
        pv.checkpoint()
    comp8 = comp(acc, wsum)
    legend = [("< 0.5 px", ps.RESID["good"]), ("0.5–1 px", ps.RESID["mid"]), ("≥ 1 px", ps.RESID["bad"])]
    resid = [(quads[m], ps.residual_colour(per_rms[m]), 1) for m in main]
    pv.show(lambda st: st.layout(comp8, [], 3, "Layout", f"{len(order)} panels placed", 1.0, readouts(len(order))),
            record=hold(f_clean) if pv.outputs else None, key="1")
    pv.show(lambda st: st.layout(comp8, resid, 3, "Layout residuals", f"{len(order)} panels · proxy level", 1.0,
                                 readouts(len(order)), legend=legend),
            record=hold(f_resid) if pv.outputs else None, key="2", replace=False)     # window: keys 1 / 2 switch views


def north_up(main, theta, idx, sizes, proxies, f, geometry, log):
    """Canvas map M (2 × 2) that turns the mosaic to lunar north up, east right (the IAU view, never mirrored),
    measured by moon_atlas on a small preview composite. None (with a reason) when the Moon cannot be located."""
    sys.path.insert(0, atlas_link.MOON_ATLAS)
    try:
        import atlas_geo
    except ImportError as e:
        return None, f"moon_atlas not available ({e})"
    P0, _, cv0 = geometry(theta, idx)
    fs = min(1.0, 2400 / max(cv0))
    grey = canvas_preview(main, P0, sizes, proxies, f, cv0, fs)
    try:
        geo, q = atlas_geo.locate(grey, log=lambda *a: None)
    except SystemExit as e:
        return None, str(e)
    M, rot, mirror = atlas_geo.north_up_matrix(geo)
    return M, dict(north_was_deg=round(geo.north_angle, 2), was_mirrored=mirror, rotation_deg=round(rot, 2),
                   matches=int(q["matches"]))


def contact_grid(n, cell_aspect, area_aspect):
    """Row lengths for n thumbnails: the sheet's shape close to the frame's, the panels spread evenly over the rows
    (13 panels in a 16:9 frame: 5 + 4 + 4, not 12 + 1). Returns (cols, [panels per row])."""
    best = None
    for rows in range(1, n + 1):
        cols = -(-n // rows)
        if (rows - 1) * cols >= n and rows > 1:
            continue
        base, extra = divmod(n, rows)
        per_row = [base + (1 if r < extra else 0) for r in range(rows)]
        score = abs(math.log(cols * cell_aspect / rows / area_aspect))
        if min(per_row) < 0.66 * max(per_row):             # 2 + 1: a lone panel on its row looks strange
            score += 1.0
        if best is None or score < best[0]:
            best = (score, cols, per_row)
    return best[1], best[2]


def canvas_preview(nodes, P, sizes, proxies, f, canvas, fs, colour=False, gains=None, offs=None):
    """Mean composite of the 1/f proxies at preview scale fs (of the full canvas), using pose matrices P."""
    Wc, Hc = int(canvas[0] * fs) + 2, int(canvas[1] * fs) + 2
    ch = 3 if colour else 1
    acc = np.zeros((Hc, Wc, ch), np.float32)
    wsum = np.zeros((Hc, Wc), np.float32)
    Sp = np.array([[f, 0, (f - 1) / 2], [0, f, (f - 1) / 2], [0, 0, 1.0]])
    for n in nodes:
        src = proxies[n]["rgb4"].astype(np.float32) if colour else proxies[n]["lum"][..., None]
        if colour and gains is not None:
            src = src * gains[n].astype(np.float32) + offs[n].astype(np.float32)
        t = max(1, proxies[n]["crop"] // f)
        wm = np.zeros(src.shape[:2], np.float32)
        wm[t:-t, t:-t] = 1
        r = min(1.0, fs * f * 1.5)
        s_ = cv2.resize(src * wm[..., None], None, fx=r, fy=r, interpolation=cv2.INTER_AREA)
        if s_.ndim == 2:
            s_ = s_[..., None]
        w_ = cv2.resize(wm, (s_.shape[1], s_.shape[0]), interpolation=cv2.INTER_AREA)
        Rm = np.array([[src.shape[1] / s_.shape[1], 0, 0], [0, src.shape[0] / s_.shape[0], 0], [0, 0, 1.0]])
        M = (np.diag([fs, fs, 1]) @ P[n] @ Sp @ Rm)[:2]
        wimg = cv2.warpAffine(s_, M, (Wc, Hc), flags=cv2.INTER_LINEAR)
        acc += wimg.reshape(Hc, Wc, ch)
        wsum += cv2.warpAffine(w_, M, (Wc, Hc), flags=cv2.INTER_LINEAR)
    c = acc / np.maximum(wsum, 1e-6)[..., None]
    c[wsum < 0.5] = 0
    return c if colour else c[..., 0]


def mismatch_overlay(img8, quads, per_pair, which, alpha=0.42):
    """Tint each overlap zone (intersection of two panel outlines) by the average brightness mismatch of the pairs
    compared there.
    which = 2: before correction, 3: after. Teal < 0.3 %, amber ~1 %, coral >= 2 %."""
    h, w = img8.shape[:2]
    hsum = np.zeros((h, w), np.float32)
    hcnt = np.zeros((h, w), np.float32)
    for pr_ in per_pair:
        a, b, m = pr_[0], pr_[1], pr_[which] * 100
        area, poly = cv2.intersectConvexConvex(quads[a].astype(np.float32), quads[b].astype(np.float32))
        if poly is None or area < 4:
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [poly.reshape(-1, 2).astype(np.int32)], 1)
        hsum += mask * m
        hcnt += mask
    heat = np.where(hcnt > 0, hsum / np.maximum(hcnt, 1), -1.0)     # average disagreement of the pairs compared here
    stops = [(0.0, ps.RESID["good"]), (0.3, ps.RESID["good"]), (1.0, ps.RESID["mid"]), (2.0, ps.RESID["bad"])]
    col = np.zeros((h, w, 3), np.float32)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        sel = (heat >= t0) & (heat < t1)
        u = ((heat - t0) / (t1 - t0))[..., None]
        col[sel] = (np.array(c0) * (1 - u) + np.array(c1) * u)[sel]
    col[heat >= 2.0] = ps.RESID["bad"]
    g = img8.mean(axis=2) if img8.ndim == 3 else img8
    on = (heat >= 0) & (g > 18)                       # the lunar disk only: sky carries no brightness to match
    al = (0.18 + 0.34 * np.clip(heat / 2.0, 0, 1))[..., None]   # faint where matched, stronger where they disagree
    out = np.where(on[..., None], img8 * (1 - al) + col * al, img8)
    return np.clip(out, 0, 255).astype(np.uint8)


def tone_img(c, wp, gamma=1.1):
    img = (np.clip(c / max(wp, 1e-6), 0, 1) ** (1 / gamma) * 255).astype(np.uint8)
    return img


def quads_at(nodes, P, sizes, scale):
    out = {}
    for n in nodes:
        w, h = sizes[n]
        c = np.array([[-0.5, -0.5, 1], [w - 0.5, -0.5, 1], [w - 0.5, h - 0.5, 1], [-0.5, h - 0.5, 1]]) @ P[n].T
        out[n] = c[:, :2] * scale
    return out


def label_overlay(grey8, lab, canvas, q, n_nodes, alpha=0.38):
    """Tint each region by the panel that owns it; thin light lines on region borders."""
    Hc, Wc = grey8.shape[:2]
    sub = lab[:int(np.ceil(canvas[1] / q)), :int(np.ceil(canvas[0] / q))]
    small = cv2.resize(sub, (Wc, Hc), interpolation=cv2.INTER_NEAREST)
    lut = np.array([mst.panel_colour(i) for i in range(n_nodes)] + [(0, 0, 0)], np.uint8)
    g = grey8 if grey8.ndim == 2 else cv2.cvtColor(grey8, cv2.COLOR_BGR2GRAY)
    small = small.copy()
    small[g < 14] = 65535                      # only the lunar disk: sky pixels supply nothing visible
    tint = lut[np.minimum(small, n_nodes)]
    base = cv2.cvtColor(grey8, cv2.COLOR_GRAY2BGR) if grey8.ndim == 2 else grey8
    img = cv2.addWeighted(base, 1 - alpha * 0.5, tint, alpha, 0)
    img[small == 65535] = base[small == 65535]
    edges = cv2.morphologyEx(small, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    img[edges & (small != 65535)] = (img[edges & (small != 65535)] * 0.4 + 170 * 0.6).astype(np.uint8)
    return img


# ---------------------------------------------------------------- pipeline

def pool_results(ex, results, pv):
    """Pool results with an abort check between them; an abort (or a failed job) cancels the jobs not yet started."""
    try:
        for r in results:
            pv.checkpoint()
            yield r
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)
        raise


def json_safe(o):
    """NaN / inf -> null (JSON has no NaN; strict readers reject it)."""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    return o


def bounded_map(pool, fn, items, ahead):
    """pool.map with at most `ahead` jobs queued or running, results in order (bounded memory, also when paused)."""
    items = list(items)
    futs = [pool.submit(fn, x) for x in items[:ahead]]
    for k in range(len(items)):
        r = futs[k].result()
        futs[k] = None
        if k + ahead < len(items):
            futs.append(pool.submit(fn, items[k + ahead]))
        yield r


def stopped(pr, stage):
    print(f"Stopped after stage {stage} (--stop-after) in {pr.elapsed()}", flush=True)
    return 0


def pipeline(args, pr, pv, stg):
    out = os.path.abspath(args.output)
    out_dir = os.path.dirname(out)
    stem = os.path.splitext(out)[0]

    # ---- stage 0
    pr.header("Preflight", 0)
    paths, dropped, _ = gather_inputs(args)
    for p, why in dropped:
        pr.info(f"skip {os.path.basename(p)}: {why}")
    heads, unreadable = {}, []
    for p in paths:
        try:
            heads[p] = header_size(p)
        except Exception as e:
            unreadable.append((p, str(e)))
    for p, e in unreadable:
        pr.warn(f"unreadable: {os.path.basename(p)} ({e})")
    paths = [p for p in paths if p in heads]
    if not paths:
        pr.info("nothing to place")
        return 2
    ws = [h[0] for h in heads.values()]
    hs = [h[1] for h in heads.values()]
    kinds = Counter(f"{h[3]} {'RGB' if h[2] >= 3 else 'mono'}" for h in heads.values())
    pr.info(f"{len(paths)} readable · {', '.join(f'{v}× {k}' for k, v in kinds.items())} · {min(ws)}–{max(ws)} × {min(hs)}–{max(hs)}")
    if len(kinds) > 1:
        pr.warn("mixed bit depths / channel counts: all data is converted to 16-bit RGB")
    total_px = sum(w * h for w, h, _, _ in heads.values())
    ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    est_ram = total_px * 2 * 1.15 + 2e9           # float16 luminance of all panels + working sets (stages 1–3)
    free = shutil.disk_usage(out_dir).free
    need_disk = total_px * 6 / 3 + 0.5e9            # rough: canvas ≈ 1/3 of the summed panel area; rechecked after stage 3
    pr.info(f"peak RAM (stages 1–3) ~{est_ram / 2**30:.1f} GiB of {ram / 2**30:.0f} GiB · free disk {free / 2**30:.0f} GiB · need ~{need_disk / 2**30:.1f} GiB")
    if est_ram > 0.6 * ram:
        pr.warn("estimated RAM above 60 % of physical memory")
    if free < need_disk:
        pr.info("FAIL: not enough free disk space for the output; free space or write elsewhere with -o")
        return 3
    dates = Counter(mc.parse_meta(p)[1].get("date") for p in paths)
    date = dates.most_common(1)[0][0] if dates else None
    pv.set_all("session", f"{date + ' · ' if date else ''}{len(paths)} panels")
    if pv.enabled and not has_display():
        pr.warn("no display detected: continuing without preview")
        pv.enabled = False
    if args.stop_after < 1:
        return stopped(pr, 0)

    # ---- stage 1
    pr.header(f"Coarse features (proxy 1/{args.proxy_factor}×)", 1)
    f = args.proxy_factor
    data, feats, proxies, sizes, failed = {}, {}, {}, {}, []

    def stage1(p):
        x, _, _ = mc.read_image(p)
        if x.shape[2] == 1:
            x = np.repeat(x, 3, axis=2)
        L = mc.luminance(x)
        crop = mc.artifact_border(x)[0] if args.border == "auto" else int(args.border)
        _, m, _, _, sat, _ = mc.subject_and_sky(L, x, border=max(32, crop), erode=8)
        P8, pm = mc.make_proxy(L, m, f)
        pts, d = mc.detect_features(P8, pm, n_features=args.features)
        sz = (L.shape[1] // f, L.shape[0] // f)
        pc = lambda a: mc.proxy_crop(a, f)
        lum = cv2.resize(pc(L), sz, interpolation=cv2.INTER_AREA)
        extra = {"crop": crop, "tenengrad": mc.sharpness_metrics(L, m)["tenengrad"] or 0.0,
                 "rgb4": cv2.resize(pc(x), sz, interpolation=cv2.INTER_AREA).astype(np.float16),
                 "m4": cv2.resize(pc(m).astype(np.uint8), sz, interpolation=cv2.INTER_NEAREST) > 0,
                 "sat4": cv2.resize(pc(sat), sz, interpolation=cv2.INTER_AREA) > 0, "tex": mst.texture_map(lum)}
        return p, L.astype(np.float16), m, P8, pm, lum, pts, d, extra
    ids = {}
    with ThreadPoolExecutor(args.workers) as ex:
        for k, res in enumerate(pool_results(ex, ex.map(stage1, paths), pv), 1):
            p, L16, m, P8, pm, lum, pts, d, extra = res
            pid, _ = mc.parse_meta(p)
            pid = pid if pid not in ids.values() else os.path.splitext(os.path.basename(p))[0]
            base, n_ = pid, 2
            while pid in ids.values():                       # same file name in two folders: keep both panels
                pid, n_ = f"{base}~{n_}", n_ + 1
            if pid != base:
                pr.warn(f"{os.path.basename(p)}: id {base} already used, this panel is {pid}")
            ids[p] = pid
            sizes[pid] = (L16.shape[1], L16.shape[0])
            if len(pts) < args.min_keypoints:
                failed.append((pid, f"unplaceable (too few features: {len(pts)})"))
                pr.warn(f"{pid}: only {len(pts)} keypoints")
            else:
                data[pid] = {"L": L16, "m": m, "path": p}
                feats[pid] = (pts, d)
                proxies[pid] = {"P8": P8, "pm": pm, "lum": lum, "nat": ps.tone(lum), **extra}
            pr.tick(k, len(paths), f"{pid}  {len(pts)} kp  (mask {m.mean() * 100:.0f} %)")
            if pv.active():
                ro = [("panel", f"{k} / {len(paths)}"), ("keypoints", f"{len(pts)}"), ("elapsed", mc.fmt_t(time.time() - pr.t_stage))]
                pv.show(lambda st: st.keypoints(ps.tone(lum), pts, 1, FRIENDLY[1], f"panel {pid} · {len(pts)} keypoints",
                                                k / len(paths), ro), min_interval=0.15)          # window only
    nodes = sorted(feats)
    if len(nodes) < 2:
        pr.info(f"nothing can be placed: {len(nodes)} panel(s) with enough features")
        return 2
    qual, qsrc = display_quality([data[n]["path"] for n in nodes], args,
                                 {data[n]["path"]: proxies[n]["tenengrad"] for n in nodes})
    quality = {n: qual[data[n]["path"]] for n in nodes}
    ranked = sorted(nodes, key=lambda n: -quality[n])
    if pv.outputs:
        # step 1 (inputs): all panels in capture order, popping in; then step 2's title card
        for rec, _ in pv.outputs:
            rec.step = 0
        times = [mc.parse_meta(data[n]["path"])[1].get("time") for n in nodes]
        times = [t for t in times if t]
        fmt_kind = ", ".join(sorted(kinds)).replace("uint16", "16-bit").replace("uint8", "8-bit").replace("float32", "32-bit float")
        ro0 = [("files", f"{len(paths)}"), ("format", f"{fmt_kind} TIFF"),
               ("frame size", f"{min(ws)}–{max(ws)} × {min(hs)}–{max(hs)} px"),
               ("captured", f"{times[0][:5]}–{times[-1][:5]}" if times else "–")]
        sheets = {}

        def sheet(st):
            if id(st) not in sheets:
                ph, pw = np.median([proxies[n]["nat"].shape[:2] for n in nodes], axis=0)
                cell = pw / ph                                   # the panels' own shape, not a fixed 16:9
                aw, ah = st.area
                cols, per_row = contact_grid(len(nodes), cell, aw / ah)
                tw, gap = 160, 6
                th = max(1, int(round(tw / cell)))
                grid = np.zeros((len(per_row) * (th + gap) - gap, cols * (tw + gap) - gap, 3), np.uint8)
                slots = [(r_, c_, k_) for r_, k_ in enumerate(per_row) for c_ in range(k_)]
                cells = []
                for n, (r_, c_, in_row) in zip(nodes, slots):          # every row centred
                    t_ = cv2.resize(proxies[n]["nat"], (tw, th), interpolation=cv2.INTER_AREA)
                    y_, x_ = r_ * (th + gap), c_ * (tw + gap) + (cols - in_row) * (tw + gap) // 2
                    cells.append((y_, x_, cv2.cvtColor(t_, cv2.COLOR_GRAY2BGR)))
                sheets[id(st)] = (grid, cells)
            return sheets[id(st)]
        f0 = int(round(VIDEO_SECONDS[0] * FPS)) - int(round(STEP1_CARD_SECONDS * FPS))
        f0_anim, f0_hold = split_frames(f0, [1, 1])
        for i in range(1, f0_anim + 1):
            upto = int(round(len(nodes) * i / f0_anim))

            def render0(st, upto=upto):
                grid, cells = sheet(st)
                g = grid.copy()
                for y_, x_, t_ in cells[:upto]:
                    g[y_:y_ + t_.shape[0], x_:x_ + t_.shape[1]] = t_
                return st.layout(g, [], 0, FRIENDLY[0], f"the inputs: {len(nodes)} panels in capture order",
                                 upto / len(nodes), ro0)
            pv.show(render0, record=hold(1 + (f0_hold if i == f0_anim else 0)), window=False)
        for rec, _ in pv.outputs:
            rec.step = 1
        if pv.pending_card is not None:
            pv.show(pv.pending_card, record=CARD_SECONDS, window=False)
            pv.pending_card = None
        # feature detection explained on two panels: features appear from small to large scale
        show = ranked[:FEATURE_PANELS]
        sift = cv2.SIFT_create(nfeatures=args.features, contrastThreshold=0.02)
        f_rev, f_hold, f_fade = (int(round(x * FPS)) for x in (FEATURE_REVEAL_SECONDS, FEATURE_HOLD_SECONDS, FEATURE_FADE_SECONDS))
        for j, n in enumerate(show):
            kp = sift.detect(proxies[n]["P8"], proxies[n]["pm"])
            kp = sorted(kp, key=lambda k: -k.response)[:700]            # the strongest, so the view stays readable
            sizes_kp = np.array([k.size for k in kp])
            lo, hi = np.log(sizes_kp.min()), np.log(sizes_kp.max())
            kps = sorted([(k.pt[0], k.pt[1], k.size / 2, (np.log(k.size) - lo) / max(hi - lo, 1e-6)) for k in kp],
                         key=lambda t: t[2])
            for i in range(1, f_rev + f_fade + 1):
                upto = max(1, int(round(len(kps) * min(i, f_rev) / f_rev)))
                fade = (i - f_rev) / f_fade if i > f_rev else 0.0
                fr = 1 + (f_hold if i == f_rev else 0)             # the complete view holds, then fades to black
                t = kps[upto - 1][3]
                phase = "small features: pits and small craters" if t < 0.34 else \
                    "medium: crater rims and ridges" if t < 0.67 else "large: craters and mare edges"
                ro = [("panel", n), ("features", f"{upto} of {len(kps)} strongest"),
                      ("feature size", f"up to {kps[upto - 1][2] * 2 * f:.0f} px"), ("detector", f"SIFT on 1/{f} proxy")]
                pv.show(lambda st, upto=upto, phase=phase, ro=ro, fade=fade: st.features(
                            proxies[n]["nat"], kps[:upto], 1, FRIENDLY[1], f"panel {n} · {phase}",
                            (j + upto / len(kps)) / len(show), ro, fade=fade),
                        record=hold(fr), window=False)
    nkp = [len(feats[n][0]) for n in nodes]
    if nkp:
        pr.info(f"keypoints per panel {min(nkp)}–{max(nkp)} (median {int(np.median(nkp))}) · movie ranking: {qsrc}")
        crops = Counter(proxies[n]["crop"] for n in nodes)
        pr.info("stacking-edge artefact crop per panel: " + " · ".join(f"{v}× {k} px" for k, v in sorted(crops.items())) +
                ("" if args.border == "auto" else " (fixed by --border)"))
    if args.stop_after < 2:
        return stopped(pr, 1)

    # ---- stage 2
    pr.header("Pair discovery", 2)
    if args.pair_search == "all":
        cand = {(nodes[a], nodes[b]) for a, b in mc.all_pairs(len(nodes))}
    else:
        pr.info(f"building one feature index over {sum(len(feats[n][0]) for n in nodes)} descriptors …")
        cand = {(nodes[a], nodes[b]) for a, b in mc.candidate_pairs([feats[n][1] for n in nodes])}
    pr.info(f"{len(cand)} candidate pairs ({args.pair_search}) · verifying")
    pr.restart()
    reasons, accepted = Counter(), {}

    def ver(ab):
        a, b = ab
        return ab, mc.verify_pair(feats[a], feats[b], proxies[a]["pm"], proxies[b]["pm"])
    cand = sorted(cand)
    with ThreadPoolExecutor(args.workers) as ex:
        for k, ((a, b), (res, why)) in enumerate(pool_results(ex, ex.map(ver, cand), pv), 1):
            if res:
                accepted[(a, b)] = res
                if pv.enabled:
                    ro = [("candidates", f"{len(cand)}"), ("verified", f"{k}"), ("accepted", f"{len(accepted)}"),
                          ("rotation", f"{res['rot_deg']:+.2f}°"), ("scale", f"{res['scale']:.4f}")]
                    pv.show(lambda st: st.pair(proxies[a]["nat"], proxies[b]["nat"], res["pts_a"], res["pts_b"], 2, FRIENDLY[2],
                                               f"{a} – {b} · {res['inliers']} inliers", k / len(cand), ro),
                            min_interval=0.5)                                                 # window only
            else:
                reasons[why] += 1
            pr.tick(k, len(cand), f"{len(accepted)} accepted")
    pr.info(f"accepted {len(accepted)} · rejected " + " · ".join(f"{v} {k}" for k, v in reasons.most_common()))
    if pv.outputs and accepted:
        # pairs between high-quality panels, each panel at most twice, best first
        used, show_pairs = Counter(), []
        for (a, b) in sorted(accepted, key=lambda ab: -min(quality[ab[0]], quality[ab[1]])):
            if used[a] < 2 and used[b] < 2:
                show_pairs.append((a, b))
                used[a] += 1
                used[b] += 1
            if len(show_pairs) == PAIR_SHOWCASE:
                break
        for j, ((a, b), fr) in enumerate(zip(show_pairs, spread_frames(step_frames(2), len(show_pairs)))):
            r = accepted[(a, b)]
            T = np.vstack([r["T"], [0, 0, 1]])                       # proxy a -> proxy b
            ha, wa = proxies[a]["nat"].shape
            hb, wb = proxies[b]["nat"].shape
            rect = lambda w_, h_: np.array([[0, 0, 1], [w_, 0, 1], [w_, h_, 1], [0, h_, 1]], float)
            def clip(poly, w_, h_):                                  # overlap zone inside this panel
                own = np.array([[0, 0], [w_, 0], [w_, h_], [0, h_]], np.float32)
                area, inter = cv2.intersectConvexConvex(poly.astype(np.float32), own)
                return inter.reshape(-1, 2) if inter is not None and area > 0 else None
            poly_a = clip((rect(wb, hb) @ np.linalg.inv(T).T)[:, :2], wa, ha)   # b's footprint inside a
            poly_b = clip((rect(wa, ha) @ T.T)[:, :2], wb, hb)                  # a's footprint inside b
            ro = [("keypoints", f"{len(feats[a][0])} + {len(feats[b][0])}"), ("verified matches", f"{r['inliers']}"),
                  ("rotation", f"{r['rot_deg']:+.2f}°"), ("scale", f"{r['scale']:.4f}"), ("overlap", f"{r['overlap'] * 100:.0f} %")]
            f_kp, f_rev, f_hold = split_frames(fr, [0.22, 0.45, 0.33])
            beats = [(0.0, False, "keypoints on both panels")] * f_kp
            beats += [((i + 1) / max(1, f_rev), False, "matching the same features") for i in range(f_rev)]
            beats += [(1.0, True, f"{r['inliers']} verified matches: this is where they overlap")] * f_hold
            prev = None
            count = 0
            for k_, beat in enumerate(beats + [None]):
                if beat != prev and prev is not None:
                    frac, show_poly, phase = prev
                    pv.show(lambda st, frac=frac, show_poly=show_poly, phase=phase: st.pair(
                                proxies[a]["nat"], proxies[b]["nat"], r["pts_a"], r["pts_b"], 2, FRIENDLY[2],
                                f"{a} – {b} · {phase}", (j + frac) / len(show_pairs), ro, frac=frac,
                                kp_a=feats[a][0], kp_b=feats[b][0],
                                poly_a=poly_a if show_poly else None, poly_b=poly_b if show_poly else None),
                            record=hold(count), window=False)
                    count = 0
                prev = beat
                count += 1
    if args.log_pairs:
        for (a, b), r in sorted(accepted.items()):
            pr.info(f"+ {a} ↔ {b}  {r['inliers']} inliers ({r['ratio'] * 100:.0f} %)  overlap {r['overlap'] * 100:.0f} %  "
                    f"rot {r['rot_deg']:+.2f}°  scale {r['scale']:.4f}")
    if args.stop_after < 3:
        return stopped(pr, 2)

    # ---- stage 3
    pr.header("Global alignment", 3)

    def to_pair(a, b, r):
        pa_full = r["pts_a"].astype(np.float64) * f + (f - 1) / 2
        pb_full = r["pts_b"].astype(np.float64) * f + (f - 1) / 2
        if len(pa_full) > args.max_points:
            sel = np.random.default_rng(0).choice(len(pa_full), args.max_points, replace=False)
            pa_full, pb_full = pa_full[sel], pb_full[sel]
        # the pair's own fitted transform at its inliers: the layout residual without per-keypoint jitter
        Tf = mc.proxy_to_full(r["T"], f)
        xa = centred(pa_full, sizes[a])
        return {"a": a, "b": b, "xa": xa, "xb": centred(pb_full, sizes[b]),
                "xb_fit": centred(pa_full @ Tf[:, :2].T + Tf[:, 2], sizes[b]),
                "Ja": J(xa), "Jb": J(centred(pb_full, sizes[b])), "inliers": r["inliers"]}

    comps = largest_component(nodes, list(accepted))
    if not comps:
        pr.info("nothing can be placed")
        return 2
    main = sorted(comps[0])
    for g in comps[1:]:
        pr.warn(("not placed (island): " if len(g) > 1 else "not placed (no overlap found): ") + ", ".join(sorted(g)))
        failed += [(n, "not placed: no overlap with the main group" if len(g) == 1 else "not placed: separate island")
                   for n in g]
    if len(main) < 2:
        pr.info("nothing can be placed")
        return 2
    mainset = set(main)
    pairs = [to_pair(a, b, r) for (a, b), r in accepted.items() if a in mainset and b in mainset]
    deg = Counter([p["a"] for p in pairs] + [p["b"] for p in pairs])
    ref = max(main, key=lambda n: (deg[n], n))
    dropped_pairs = []
    closure_done = False
    while True:
        theta, idx = solve_global(main, pairs, ref)
        res = pair_residuals(theta, idx, pairs, "xb_fit")
        prms = np.array([np.sqrt(np.mean(e ** 2)) for e in res])
        allr = np.concatenate(res)
        rms_all = float(np.sqrt(np.mean(allr ** 2)))
        limit = max(1.5, 3 * float(np.median(prms)))
        bad = [k for k in range(len(pairs)) if prms[k] > limit]
        if bad:
            # the worst half only, then re-solve: one wrong match inflates the error of the good pairs that close
            # a loop with it, and dropping all of them at once could cut off a whole column of panels
            bad = sorted(bad, key=lambda k: -prms[k])[:max(1, -(-len(bad) // 2))]
            pr.info(f"layout RMS {rms_all:.2f} px → dropping the worst {len(bad)} pair(s) with pair RMS > {limit:.2f} px")
            for k in bad:
                dropped_pairs.append((pairs[k]["a"], pairs[k]["b"], float(prms[k])))
            pairs = [p for k, p in enumerate(pairs) if k not in set(bad)]
            # panels whose only links were those pairs have nothing left that places them
            comp = next(c for c in largest_component(main, [(p["a"], p["b"]) for p in pairs]) if ref in c)
            cut = sorted(set(main) - set(comp))
            if cut:
                pr.warn("not placed (its overlaps disagree with the layout): " + ", ".join(cut))
                failed += [(n, "not placed: its overlaps disagree with the layout") for n in cut]
                main, mainset = sorted(comp), set(comp)
                pairs = [p for p in pairs if p["a"] in mainset and p["b"] in mainset]
                if len(main) < 2:
                    pr.info("nothing can be placed")
                    return 2
            continue
        if not closure_done and args.pair_search == "index":
            closure_done = True
            # layout closure: verify predicted overlaps that were never candidates
            quads = {n: footprint(theta[idx[n]], sizes[n]).astype(np.float32) for n in main}
            tried = set(cand)
            extra = []
            for i, a in enumerate(main):
                for b in main[i + 1:]:
                    if (a, b) in tried:
                        continue
                    inter, _ = cv2.intersectConvexConvex(quads[a], quads[b])
                    if inter > 0.05 * min(sizes[a][0] * sizes[a][1], sizes[b][0] * sizes[b][1]):
                        extra.append((a, b))
            n_ok = 0
            for a, b in extra:
                r, why = mc.verify_pair(feats[a], feats[b], proxies[a]["pm"], proxies[b]["pm"])
                if r:
                    accepted[(a, b)] = r
                    pairs.append(to_pair(a, b, r))
                    n_ok += 1
            pr.info(f"layout closure: {len(extra)} predicted overlaps not among candidates · {n_ok} verified and added")
            if n_ok:
                continue
        break
    deg = Counter([p["a"] for p in pairs] + [p["b"] for p in pairs])        # after dropping and closure
    pr.info(f"component: {len(main)}/{len(nodes) + len([x for x in failed if 'features' in x[1]])} panels · "
            f"{len(pairs)} pair constraints · reference {ref} ({deg[ref]} partners)")
    if dropped_pairs:
        worst_d = sorted(dropped_pairs, key=lambda t: -t[2])
        pr.info(f"dropped {len(dropped_pairs)} inconsistent pairs; worst: " +
                ", ".join(f"{a}↔{b} {e:.1f} px" for a, b, e in worst_d[:5]) +
                (f" … full list in {os.path.basename(stem)}_layout.json" if len(worst_d) > 5 else ""))
    # per-panel residuals
    per = {n: [] for n in main}
    for p, e in zip(pairs, pair_residuals(theta, idx, pairs, "xb_fit")):
        per[p["a"]].append(e)
        per[p["b"]].append(e)
    per_rms = {n: float(np.sqrt(np.mean(np.concatenate(v) ** 2))) if v else float("nan") for n, v in per.items()}
    worst = sorted(per_rms.items(), key=lambda kv: -kv[1])
    allr = np.concatenate(pair_residuals(theta, idx, pairs, "xb_fit"))
    ptr = np.concatenate(pair_residuals(theta, idx, pairs, "xb"))
    pr.info(f"layout RMS {np.sqrt(np.mean(allr ** 2)):.2f} px · worst panel {worst[0][0]} {worst[0][1]:.2f} px · "
            f"keypoint RMS {np.sqrt(np.mean(ptr ** 2)):.2f} px (proxy-level; stage 4 refines at full resolution)")
    n_amber = sum(0.5 <= v < 1.0 for v in per_rms.values())
    n_red = sum(v >= 1.0 for v in per_rms.values())
    pr.info(f"panels by layout residual: {len(main) - n_amber - n_red} < 0.5 px · {n_amber} 0.5–1 px · {n_red} ≥ 1 px")
    sc = {n: float(np.hypot(*theta[idx[n]][:2])) for n in main}
    rot = {n: float(np.degrees(np.arctan2(theta[idx[n]][1], theta[idx[n]][0]))) for n in main}
    pr.info(f"scale {min(sc.values()):.4f}–{max(sc.values()):.4f} · rotation {min(rot.values()):+.3f}° to {max(rot.values()):+.3f}°")
    for n in main:
        if abs(rot[n]) > 1.0:
            pr.warn(f"{n}: rotation {rot[n]:+.2f}° relative to the reference (possible bad match)")
    corners = np.vstack([footprint(theta[idx[n]], sizes[n]) for n in main])
    offset = np.floor(corners.min(axis=0))
    canvas = tuple(int(v) for v in np.ceil(corners.max(axis=0) - offset))
    out_bytes = canvas[0] * canvas[1] * 6
    free = shutil.disk_usage(out_dir).free
    pr.info(f"canvas {canvas[0]} × {canvas[1]} px ({canvas[0] * canvas[1] / 1e6:.0f} Mpx) · "
            f"output ~{out_bytes / 2**30:.2f} GiB uncompressed · free disk {free / 2**30:.0f} GiB")
    if free < out_bytes + 0.5e9:
        pr.warn("free disk space is below the output size")

    scale = min(1.0, 1600 / max(canvas))
    views = layout_views(main, theta, idx, sizes, proxies, f, offset, canvas, per_rms, scale)   # diagnostic files
    if pv.active():
        show_layout(pv, main, pairs, ref, theta, idx, sizes, proxies, f, offset, canvas, per_rms, rot,
                    float(np.sqrt(np.mean(allr ** 2))))
    cv2.imwrite(stg.path(stem + "_layout.png"), views["2"])
    cv2.imwrite(stg.path(stem + "_layout_composite.png"), views["1"])
    layout = {
        "schema": "mosaic-layout/1", "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reference": ref, "canvas": {"width": canvas[0], "height": canvas[1], "offset": offset.tolist()},
        "model": "similarity (pose applied to panel-centred full-resolution pixel coordinates)",
        "rms_px": float(np.sqrt(np.mean(allr ** 2))), "keypoint_rms_px": float(np.sqrt(np.mean(ptr ** 2))), "level": "proxy",
        "panels": {n: {"path": data[n]["path"], "size": sizes[n], "pose_abtxty": theta[idx[n]].tolist(),
                       "scale": sc[n], "rot_deg": rot[n], "rms_px": per_rms[n], "partners": deg[n]} for n in main},
        "pairs": [{"a": p["a"], "b": p["b"], "inliers": p["inliers"]} for p in pairs],
        "dropped_pairs": [{"a": a, "b": b, "rms_px": e} for a, b, e in dropped_pairs],
        "not_placed": [{"id": n, "reason": why} for n, why in failed],
    }
    with open(stg.path(stem + "_layout.json"), "w") as fh:
        json.dump(json_safe(layout), fh, indent=1)
    pr.info(f"layout: {stem}_layout.json · {os.path.basename(stem)}_layout.png · {os.path.basename(stem)}_layout_composite.png")

    result = run_stages_4_to_8(args, pr, pv, stg, main, pairs, ref, theta, idx, sizes, data, proxies, f, stem, deg, failed, paths, layout)
    placed = len(main)
    total = len(paths)
    excl = ", ".join(f"{n} ({why})" for n, why in failed) or "none"
    print(f"Done in {pr.elapsed()} · placed {placed}/{total} · excluded: {excl}" + (f" · {result}" if result else ""), flush=True)
    return 0


def run_stages_4_to_8(args, pr, pv, stg, main, pairs, ref, theta, idx, sizes, data, proxies, f, stem, deg, failed, paths, layout):
    """Stages 4–8. Returns a short summary for the final line."""
    if args.stop_after < 4:
        return ""
    workers = args.workers
    pool = ThreadPoolExecutor(workers)
    model = mst.make_model(args.model, {n: sizes[n] for n in main})

    # ---- stage 4: fine alignment
    pr.header(f"Fine alignment (full resolution, {args.model})", 4)
    coarse = {(p["a"], p["b"]): (p["xa"], p["xb"]) for p in pairs}
    L = {n: data[n]["L"] for n in main}
    M = {n: data[n]["m"] for n in main}
    tex4 = {n: proxies[n]["tex"] for n in main}

    def geometry(th, ix):
        corners = []
        for n in main:
            w, h = sizes[n]
            t = np.linspace(0, 1, 17)
            edge = np.vstack([np.stack([t * w, 0 * t], 1), np.stack([t * w, 0 * t + h], 1),
                              np.stack([0 * t, t * h], 1), np.stack([0 * t + w, t * h], 1)]) - 0.5
            corners.append(mst.to_canvas(model, th[ix[n]], sizes[n], edge))     # exact warped outline
        allc = np.vstack(corners)
        off = np.floor(allc.min(axis=0))
        cv_ = tuple(int(v) for v in np.ceil(allc.max(axis=0) - off))
        return {n: mst.panel_matrix(model, th[ix[n]], sizes[n], off) for n in main}, off, cv_

    fs_prev = max(min(aw / 15000, ah / 13600) for aw, ah in (st.area for st in pv.styles()))

    def on_iter(it, th, ix, st):
        pv.checkpoint()
        if not pv.active():
            return
        Pi, off, cv_ = geometry(th, ix)
        fs = max(min(aw / cv_[0], ah / cv_[1]) for aw, ah in (st_.area for st_ in pv.styles()))
        grey = canvas_preview(main, Pi, sizes, proxies, f, cv_, fs)
        wp = float(np.percentile(grey[grey > 0], 99.8)) if (grey > 0).any() else 1
        qd = quads_at(main, Pi, sizes, fs)
        outl = [(qd[n], ps.residual_colour(st["per_rms"].get(n, 0) if np.isfinite(st["per_rms"].get(n, np.nan)) else 0), 1) for n in main]
        legend = [("< 0.5 px", ps.RESID["good"]), ("0.5–1 px", ps.RESID["mid"]), ("≥ 1 px", ps.RESID["bad"])]
        ro = [("round", f"{it + 1} of 3"), ("rms", f"{st['rms']:.3f} px"), ("tiles", f"{st['tiles']}"), ("pairs", f"{st['pairs']}")]
        render = lambda s_: s_.layout(tone_img(grey, wp), outl, 4, FRIENDLY[4], f"round {it + 1} · RMS {st['rms']:.2f} px",
                                      (it + 1) / 3, ro, legend=legend)
        round_frames.append(render)
        pv.show(render)                                   # window now; the movie gets the accepted rounds afterwards

    t4 = time.time()
    round_frames = []
    theta_f, idx_f, st4 = mst.fine_align(main, list(coarse), sizes, L, M, tex4, f, theta, idx, ref, model, coarse, pool,
                                         log=pr.info, on_iteration=on_iter)
    if isinstance(model, mst.MeshModel):
        pv.checkpoint()
        tm = time.time()
        theta_f, idx_f, stm = mst.refine_mesh(main, list(coarse), sizes, L, M, tex4, f, theta_f, idx_f, ref, model, coarse,
                                              theta, idx, pool, dropped=st4["dropped"], fit_pairs=st4["fit_pairs"],
                                              log=pr.info)
        pr.info(f"mesh refinement {time.time() - tm:.0f} s · on the fine-alignment tiles: RMS {st4['rms']:.3f} → "
                f"{stm.get('rms', st4['rms']):.3f} px, median {st4['median']:.3f} → {stm.get('median', st4['median']):.3f} px")
        st4.update({k: stm[k] for k in ("rms", "median", "per_rms") if k in stm})
        st4["mesh"] = stm
    orientation = {"turned": False}
    if args.north_up:
        pv.checkpoint()
        M, info = north_up(main, theta_f, idx_f, sizes, proxies, f, geometry, pr.info)
        if M is None:
            pr.warn(f"orientation kept as captured: the Moon could not be located on the preview ({info})")
            orientation["reason"] = info
        elif abs(info["rotation_deg"]) < 0.05 and not info["was_mirrored"]:
            pr.info("orientation: lunar north is already up")
            orientation.update(info)
        else:
            try:
                theta_f = mst.reorient(model, theta_f, M)
                orientation.update(info, turned=True, matrix=M.tolist())
                pr.info(f"orientation: lunar north was {info['north_was_deg']:.1f}° from up"
                        f"{', mirrored' if info['was_mirrored'] else ''} → turned to north up, east right "
                        f"(one resample, in the blend; {info['matches']} terrain matches on the preview)")
            except ValueError as e:
                pr.warn(f"orientation kept as captured: {e}")
                orientation["reason"] = str(e)
    P, offset, canvas = geometry(theta_f, idx_f)
    for render, fr in zip(round_frames[:st4["iteration"]], spread_frames(step_frames(4), min(len(round_frames), st4["iteration"]))):
        pv.show(render, record=hold(fr), window=False)
    per = st4["per_rms"]
    finite = {n: v for n, v in per.items() if np.isfinite(v)}
    n_good = sum(v < 0.5 for v in finite.values())
    n_mid = sum(0.5 <= v < 1.0 for v in finite.values())
    n_bad = sum(v >= 1.0 for v in finite.values())
    n_none = len(per) - len(finite)
    worst = max(finite.items(), key=lambda kv: kv[1]) if finite else ("-", float("nan"))
    sr = [model.scale_rot(theta_f[idx_f[n]], sizes[n]) if isinstance(model, mst.PolyModel) else model.scale_rot(theta_f[idx_f[n]])
          for n in main]
    pr.info(f"RMS {st4['rms']:.3f} px (target < 0.5) · panels {n_good} < 0.5 px · {n_mid} 0.5–1 px · {n_bad} ≥ 1 px · " +
            (f"{n_none} without tiles (keypoint placement) · " if n_none else "") +
            (f"worst {worst[0]} {worst[1]:.2f} px · " if finite else "") + f"{time.time() - t4:.0f} s")
    pr.info(f"scale {min(s for s, _ in sr):.4f}–{max(s for s, _ in sr):.4f} · rotation {min(r for _, r in sr):+.3f}° to "
            f"{max(r for _, r in sr):+.3f}° · canvas {canvas[0]} × {canvas[1]} px")
    for n, v in finite.items():
        if v >= 1.0:
            pr.warn(f"{n}: fine-alignment residual {v:.2f} px (placed anyway)")
    layout["orientation"] = orientation
    layout.update({"level": "full", "model": args.model, "rms_px": st4["rms"],
                   "canvas": {"width": canvas[0], "height": canvas[1], "offset": offset.tolist()}})
    for n in main:
        layout["panels"][n].update({"panel_to_canvas": P[n].tolist(), "rms_px": per.get(n), "crop_px": proxies[n]["crop"],
                                    "pose_params": theta_f[idx_f[n]].tolist()})
    layout["fine_dropped_pairs"] = [{"a": a, "b": b, "rms_px": e} for a, b, e in st4["dropped"]]
    if "mesh" in st4:
        layout["mesh"] = {k: v for k, v in st4["mesh"].items() if k != "per_rms"}
    with open(stg.path(stem + "_layout.json"), "w") as fh:
        json.dump(json_safe(layout), fh, indent=1)
    step = max(128, 2 ** args.levels)          # every pyramid level must halve exactly (and stay on the 1/q grid)
    canvas_pad = (-(-canvas[0] // step) * step, -(-canvas[1] // step) * step)
    fs = max(min(aw / canvas[0], ah / canvas[1]) for aw, ah in (st.area for st in pv.styles()))
    if args.stop_after < 5:
        pool.shutdown()
        return f"RMS {st4['rms']:.2f} px"

    # ---- stage 5: photometry
    pr.header("Brightness and colour matching (gain + offset per channel)", 5)
    rgb4 = {n: proxies[n]["rgb4"] for n in main}
    m4 = {n: proxies[n]["m4"] for n in main}
    sat4 = {n: proxies[n]["sat4"] for n in main}
    gains, offs, st5 = mst.photometry(main, list(coarse), P, rgb4, m4, sat4, f, ref)
    g = np.array([gains[n] for n in main])
    o = np.array([offs[n] for n in main]) * 65535
    if not st5["pairs"]:
        pr.warn("no overlap large enough to compare brightness: gains 1, offsets 0 (no brightness matching)")
    pr.info(f"{st5['pairs']} overlaps · gains R {g[:, 2].min():.3f}–{g[:, 2].max():.3f} · G {g[:, 1].min():.3f}–{g[:, 1].max():.3f} · "
            f"B {g[:, 0].min():.3f}–{g[:, 0].max():.3f} · offsets {o.min():+.0f}…{o.max():+.0f} ADU")
    pr.info(f"brightness mismatch on shared terrain: {st5['mismatch_before_pct']:.2f} % → {st5['mismatch_after_pct']:.2f} % (median)")
    pb_ = np.array([p_[2] for p_ in st5["per_pair"]]) * 100
    pa_ = np.array([p_[3] for p_ in st5["per_pair"]]) * 100
    if len(pb_):
        pr.info(f"per overlap: before median {np.median(pb_):.2f} % · 90 % of overlaps below {np.percentile(pb_, 90):.2f} % · "
                f"{(pb_ >= 2).mean() * 100:.0f} % at ≥ 2 %  →  after median {np.median(pa_):.2f} % · 90 % below {np.percentile(pa_, 90):.2f} %")
    if pv.active():
        before = canvas_preview(main, P, sizes, proxies, f, canvas, fs, colour=True)
        after = canvas_preview(main, P, sizes, proxies, f, canvas, fs, colour=True, gains=gains, offs=offs)
        wpc = float(np.percentile(after[after.max(axis=2) > 0], 99.8))
        ro5 = [("overlaps", f"{st5['pairs']}"), ("mismatch", f"{st5['mismatch_before_pct']:.2f} % -> {st5['mismatch_after_pct']:.2f} %"),
               ("gain G", f"{g[:, 1].min():.2f}–{g[:, 1].max():.2f}"), ("offsets", f"{o.min():+.0f}…{o.max():+.0f} ADU")]
        f_before, f_after = split_frames(step_frames(5), [1, 1])
        qd5 = quads_at(main, P, sizes, fs)
        leg5 = [("< 0.3 %", ps.RESID["good"]), ("1 %", ps.RESID["mid"]), ("≥ 2 %", ps.RESID["bad"])]
        img_b = mismatch_overlay(tone_img(before, wpc), qd5, st5["per_pair"], 2)
        img_a = mismatch_overlay(tone_img(after, wpc), qd5, st5["per_pair"], 3)
        pv.show(lambda s_: s_.layout(img_b, [], 5, FRIENDLY[5], "overlaps compared · colour = brightness difference before",
                                     0.5, ro5, legend=leg5), record=hold(f_before) if pv.outputs else None)
        pv.show(lambda s_: s_.layout(img_a, [], 5, FRIENDLY[5], "after gains and offsets: the same overlaps now agree",
                                     1.0, ro5, legend=leg5), record=hold(f_after) if pv.outputs else None)
    else:
        wpc = 1.0
    if args.stop_after < 6:
        pool.shutdown()
        return f"RMS {st4['rms']:.2f} px"

    # ---- stage 6: region selection
    pr.header("Choosing the sharpest regions", 6)
    q = 4
    grey = canvas_preview(main, P, sizes, proxies, f, canvas, fs) if pv.active() else None
    wpg = float(np.percentile(grey[grey > 0], 99.8)) if grey is not None else 1
    grey8 = tone_img(grey, wpg) if grey is not None else None
    t6 = time.time()
    f6_anim, f6_final = split_frames(step_frames(6), [2.3, 1.5])
    plan6 = spread_frames(f6_anim, len(main))

    def on_panel(k, lab_now):
        pv.checkpoint()
        pr.tick(k, len(main), "sharpness maps")
        if pv.active():
            ro6 = [("panels", f"{k} / {len(main)}"), ("rule", "sharpest view wins"), ("taper", "150 px"), ("grid", f"1/{q}")]
            pv.show(lambda s_: s_.layout(label_overlay(grey8, lab_now, canvas, q, len(main)), [], 6, FRIENDLY[6],
                                         f"{k} of {len(main)} panels compared", k / len(main), ro6),
                    record=hold(plan6[k - 1]) if pv.outputs else None, min_interval=0.1)
    lab, shares = mst.select_regions(main, sizes, L, P, canvas_pad, q, pool,
                                     borders={n: proxies[n]["crop"] for n in main}, on_panel=on_panel)
    contributing = [n for n in main if shares[n] > 0]
    top = sorted(shares.items(), key=lambda kv: -kv[1])[:3]
    pr.info(f"{len(contributing)} panels win at least one region · {len(main) - len(contributing)} dominated (skipped in blending) · "
            f"largest shares " + ", ".join(f"{n} {v * 100:.1f} %" for n, v in top) + f" · {time.time() - t6:.0f} s")
    if pv.active():
        ro6 = [("contributing", f"{len(contributing)} / {len(main)}"), ("dominated", f"{len(main) - len(contributing)}"),
               ("largest", f"{top[0][0]} {top[0][1] * 100:.1f} %"), ("grid", f"1/{q}")]
        pv.show(lambda s_: s_.layout(label_overlay(grey8, lab, canvas, q, len(main)), [], 6, FRIENDLY[6],
                                     "each colour = the panel that supplies that region", 1.0, ro6),
                record=hold(f6_final) if pv.outputs else None)
    for n in main:                      # full-resolution luminance is no longer needed
        data[n]["L"] = None
    L = None
    if args.stop_after < 7:
        pool.shutdown()
        return f"RMS {st4['rms']:.2f} px · {len(contributing)} panels contribute"

    # ---- stage 7: multi-band blending
    pr.header(f"Blending (multi-band, {args.levels} levels)", 7)
    acc, wacc = mst.new_pyramids(canvas_pad, args.levels)
    t7 = time.time()
    order = sorted(contributing, key=lambda n: -shares[n])
    blend_pool = ThreadPoolExecutor(3)
    job = lambda n: mst.blend_panel(n, main.index(n), data[n]["path"], sizes[n], P[n], gains[n], offs[n], lab, q,
                                    canvas_pad, args.levels, border=proxies[n]["crop"],
                                    model=model, th=theta_f[idx_f[n]], offset=offset)
    lv = min(3, args.levels)
    f7_anim, f7_final = split_frames(step_frames(7), [7.3, 1.5])
    plan7 = spread_frames(f7_anim, len(order))
    blended = bounded_map(blend_pool, job, order, 6)
    for k in range(1, len(order) + 1):
        try:
            pv.checkpoint()
            res = next(blended)
        except BaseException:
            blend_pool.shutdown(wait=False, cancel_futures=True)       # an abort does not wait for the queued panels
            raise
        if res is not None:
            mst.accumulate(acc, wacc, res)
        pr.tick(k, len(order), f"{order[k - 1]}  share {shares[order[k - 1]] * 100:.1f} %")
        if pv.active():
            def render(s_, k=k):
                part = mst.collapse(acc, wacc, down_to=lv)
                hh, ww = int(canvas[1] / 2 ** lv) + 1, int(canvas[0] / 2 ** lv) + 1
                part = tone_img(part[:hh, :ww], wpc)
                # not yet blended: show the dimmed layout underneath, so the blend visibly fills in
                done = (wacc[lv][:hh, :ww] > 0.3)[..., None]
                under = cv2.resize(grey8, (part.shape[1], part.shape[0]), interpolation=cv2.INTER_AREA)
                part = np.where(done, part, (cv2.cvtColor(under, cv2.COLOR_GRAY2BGR) * 0.3).astype(np.uint8))
                ro7 = [("blended", f"{k} / {len(order)}"), ("levels", f"{args.levels}"), ("newest", order[k - 1]),
                       ("share", f"{shares[order[k - 1]] * 100:.1f} %")]
                return s_.layout(part, [], 7, FRIENDLY[7], f"{k} of {len(order)} · the real blend at 1/{2 ** lv} scale",
                                 k / len(order), ro7)
            last = k == len(order)
            pv.show(render, record=hold(plan7[k - 1] + (f7_final if last else 0)) if pv.outputs else None, min_interval=0.3)
    blend_pool.shutdown()
    pr.info(f"{len(order)} panels blended in {time.time() - t7:.0f} s")
    if args.stop_after < 8:
        pool.shutdown()
        return f"RMS {st4['rms']:.2f} px · {len(contributing)} panels contribute"

    # ---- stage 8: finishing and output
    pr.header("Background, colour balance and output", 8)
    t8 = time.time()
    coverage = wacc[0] > 1e-3
    # a gap of a few px inside the covered area has no full-resolution source but is not outside the mosaic: keep it
    # (its value comes from the coarser levels) instead of painting it black with the space outside the disk
    closed = cv2.morphologyEx(coverage.view(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
    gaps = int(np.count_nonzero(closed & ~coverage))
    if gaps:
        pr.warn(f"{gaps} px inside the mosaic had no full-resolution source; filled from the coarser blend levels")
    coverage = closed
    out = mst.collapse(acc, wacc, down_to=0, free=True)
    acc = wacc = None
    np.maximum(out, 0, out=out)
    before8 = cv2.resize(out, (int(canvas[0] * fs), int(canvas[1] * fs)), interpolation=cv2.INTER_AREA) if pv.active() else None
    info = mst.finish(out, coverage, q=q, feather=args.limb_feather, background=args.background, colour=args.colour_balance,
                      log=pr.warn)
    if "pedestal_bgr" in info:
        pb = info["pedestal_bgr"]
        rs = info["residual_sky_bgr"]
        pr.info(f"sky pedestal R {pb[2]:.0f} · G {pb[1]:.0f} · B {pb[0]:.0f} ADU subtracted → residual sky R {rs[2]:+.1f} · "
                f"G {rs[1]:+.1f} · B {rs[0]:+.1f} ADU")
    if "colour_gains_bgr" in info:
        cg, cb = info["colour_gains_bgr"], info["colour_before_bgr"]
        pr.info(f"colour balance ({args.colour_balance}): cast R {cb[2]:.3f} · G {cb[1]:.3f} · B {cb[0]:.3f} → gains "
                f"R {cg[2]:.3f} · G {cg[1]:.3f} · B {cg[0]:.3f}")
    # every covered row and column is kept; the corner rounds outward to the 8 px grid (keeps the 1/q label grid aligned)
    ys, xs = np.nonzero(coverage.any(axis=1))[0], np.nonzero(coverage.any(axis=0))[0]
    bbox = (int(xs[0]) // 8 * 8, int(ys[0]) // 8 * 8, min(canvas[0], (int(xs[-1]) // 8 + 1) * 8), min(canvas[1], (int(ys[-1]) // 8 + 1) * 8))
    pv.checkpoint()
    shape = mst.write_tiff(stg.path(args.output), out, bbox)
    size_gb = os.path.getsize(stg.path(args.output)) / 2 ** 30
    pr.info(f"{os.path.basename(args.output)}  {shape[1]} × {shape[0]} · 16-bit RGB · uncompressed · {size_gb:.2f} GiB")
    # the movie's view of the result, and a TIFF with exactly that tone mapping (white point + gamma 1.1)
    box = 1100
    sc = min(box / canvas[0], box / canvas[1])
    hero = cv2.resize(out[:canvas[1], :canvas[0]], (int(canvas[0] * sc), int(canvas[1] * sc)), interpolation=cv2.INTER_AREA)
    wp_disp = float(np.percentile(hero[hero.max(axis=2) > 0.02], 99.8))
    if args.display_tif:
        disp_path = os.path.splitext(os.path.abspath(args.output))[0] + "_display.tif"
        clip_share = mc.write_display_tiff(stg.path(disp_path), out, wp_disp, 1.1, bbox)
        pr.info(f"{os.path.basename(disp_path)}  as in the movie: white point {wp_disp * 65535:.0f} ADU, gamma 1.1 · "
                f"{clip_share * 100:.2f} % of the Moon at full white")
    # contribution map
    final_q = cv2.resize(out, (canvas_pad[0] // q, canvas_pad[1] // q), interpolation=cv2.INTER_AREA)
    quads_q = quads_at(main, P, sizes, 1 / q)
    crop_q = (bbox[0] // q, bbox[1] // q, -(-bbox[2] // q), -(-bbox[3] // q))
    disk_q = cv2.resize(info["disk_small"].astype(np.uint8), (lab.shape[1], lab.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    contrib, disk_shares = mst.contribution_map(stg.path(stem + "_contributions.png"), final_q, lab, main, {n: data[n]["path"] for n in main},
                                                quads_q, shares, q, crop_q, font_path="/System/Library/Fonts/Avenir Next.ttc",
                                                disk=disk_q)
    # region map for later inspection/tools: 16-bit PNG at 1/q of the output, index into layout["label_index"]
    cv2.imwrite(stg.path(stem + "_labels.png"), lab[crop_q[1]:crop_q[3], crop_q[0]:crop_q[2]])
    layout.update({"label_index": main, "label_scale": q, "output_bbox": list(bbox),
                   "disk_share": {n: v for n, v in disk_shares.items() if v > 0}})
    with open(stg.path(stem + "_layout.json"), "w") as fh:
        json.dump(json_safe(layout), fh, indent=1)
    top_d = sorted(disk_shares.items(), key=lambda kv: -kv[1])[:3]
    pr.info(f"{sum(v > 0 for v in disk_shares.values())} panels supply part of the lunar disk · largest " +
            ", ".join(f"{n} {v * 100:.1f} %" for n, v in top_d))
    pr.info(f"contribution map: {os.path.basename(stem)}_contributions.png · region map: {os.path.basename(stem)}_labels.png · "
            f"{time.time() - t8:.0f} s")
    if pv.active():
        after8 = cv2.resize(out, (int(canvas[0] * fs), int(canvas[1] * fs)), interpolation=cv2.INTER_AREA)
        ro8 = [("size", f"{shape[1]} × {shape[0]} px"), ("format", "16-bit TIFF"), ("panels used", f"{len(contributing)}"),
               ("rms", f"{st4['rms']:.2f} px")]
        f8_before, f8_contrib = split_frames(step_frames(8), [1.3, 2.5])
        pv.show(lambda s_: s_.layout(tone_img(before8, wpc), [], 8, FRIENDLY[8], "before: sky pedestal and colour cast", 0.3, ro8),
                record=hold(f8_before) if pv.outputs else None)
        # hero image of the final result; the contributions view uses exactly the same size and position
        hero8 = tone_img(hero, wp_disp)                                # identical mapping to mosaic_display.tif
        hh, hw = hero8.shape[:2]
        lab_c = lab[:int(np.ceil(canvas[1] / q)), :int(np.ceil(canvas[0] / q))]
        disk_c = disk_q[:lab_c.shape[0], :lab_c.shape[1]]
        lab_d = np.where(disk_c, lab_c, 65535).astype(np.uint16)
        lab_h = cv2.resize(lab_d, (hw, hh), interpolation=cv2.INTER_NEAREST)
        lut = np.array([mst.panel_colour(i) for i in range(len(main))] + [(0, 0, 0)], np.uint8)
        tint = lut[np.minimum(lab_h, len(main))]
        on = (lab_h != 65535)[..., None]
        cimg = np.where(on, cv2.addWeighted(hero8, 0.62, tint, 0.38, 0), hero8)
        edges = (cv2.morphologyEx(lab_h, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0) & (lab_h != 65535)
        cimg[edges] = (cimg[edges] * 0.35 + 200 * 0.65).astype(np.uint8)
        labels = []
        for i, n in enumerate(main):
            if disk_shares.get(n, 0) <= 0:
                continue
            m_ = (lab_h == i).astype(np.uint8)
            nn, cc, st_, cen = cv2.connectedComponentsWithStats(m_)
            if nn < 2:
                continue
            k_ = 1 + int(np.argmax(st_[1:, cv2.CC_STAT_AREA]))
            if st_[k_, cv2.CC_STAT_AREA] > 900 and st_[k_, cv2.CC_STAT_WIDTH] > 44:
                labels.append((cen[k_][0], cen[k_][1], n, tuple(int(v) for v in lut[i])))
        rows = [(v * 100, os.path.basename(data[n]["path"]), tuple(int(c) for c in lut[main.index(n)]))
                for n, v in sorted(disk_shares.items(), key=lambda kv: -kv[1]) if v > 0]
        ro8c = [("files used", f"{len(rows)} of {len(main)}"), ("largest share", f"{rows[0][0]:.1f} %"),
                ("size", f"{shape[1]} × {shape[0]} px"), ("output", "16-bit TIFF")]
        contrib_frame = {}

        def render_contrib(s_):
            if id(s_) not in contrib_frame:
                contrib_frame[id(s_)] = s_.contributions(cimg, labels, rows, 8, "Contributions",
                                                         "each colour = the file that supplied that region", 1.0, ro8c)
            return contrib_frame[id(s_)]
        pv.show(render_contrib, record=hold(f8_contrib) if pv.outputs else None, window=False)
        date = pv.style.session.split(" · ")[0] if " · " in pv.style.session else pv.style.session
        facts = [("size", f"{shape[1]} × {shape[0]} px"), ("panels", f"{len(contributing)} of {len(main)} used"),
                 ("alignment", f"{st4['rms']:.2f} px RMS"), ("format", "16-bit TIFF")]
        final_frame = {}

        def render_final(s_):
            if id(s_) not in final_frame:
                final_frame[id(s_)] = s_.final(hero8, date, "the finished mosaic", facts)
            return final_frame[id(s_)]
        for rec, _ in pv.outputs:
            rec.step = "final"
        n_fade = int(round(FADE_SECONDS * FPS))
        n_final = int(round(VIDEO_SECONDS["final"] * FPS))
        for i in range(1, n_fade + 1):
            t = i / (n_fade + 1)
            t = t * t * (3 - 2 * t)                                    # ease in-out
            pv.show(lambda s_, t=t: cv2.addWeighted(render_contrib(s_), 1 - t, render_final(s_), t, 0),
                    record=hold(1) if pv.outputs else None, window=False)
        pv.show(render_final, record=hold(n_final - n_fade) if pv.outputs else None)
        # fade out to black, so the movie is exactly MOVIE_TARGET_SECONDS long
        for rec, _ in pv.outputs:
            rec.step = "fade-out"
        n_out = int(round(fadeout_seconds() * FPS))
        for i in range(1, n_out + 1):
            t = i / n_out
            k_ = 1 - t * t * (3 - 2 * t)                                # ease in-out, ends on pure black
            pv.show(lambda s_, k_=k_: (render_final(s_).astype(np.float32) * k_).astype(np.uint8),
                    record=hold(1) if pv.outputs else None, window=False)
    pool.shutdown()
    return f"RMS {st4['rms']:.2f} px · {len(contributing)} panels contribute · {os.path.basename(args.output)}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=mc.HelpFormatter)
    ap.add_argument("inputs", nargs="+", help="image folders/files and/or panel manifest JSON")
    ap.add_argument("-o", "--output", required=True, help="output TIFF path (layout files are written next to it)")
    ap.add_argument("--manifest", help="optional manifest: exclude rejects, apply --exclude-tier")
    ap.add_argument("--exclude-tier", default="", help="comma-separated tiers to exclude (default: none)")
    ap.add_argument("--proxy-factor", type=int, default=ts.get("MOSAIC_PROXY_FACTOR"), help="proxy downscale factor for pair discovery (default 4 = 0.25×)")
    ap.add_argument("--features", type=int, default=ts.get("MOSAIC_FEATURES"), help="SIFT keypoints per panel (on the proxy)")
    ap.add_argument("--min-keypoints", type=int, default=ts.get("MOSAIC_MIN_KEYPOINTS"), help="panels with fewer keypoints are not placed")
    ap.add_argument("--pair-search", choices=("index", "all"), default=ts.get("MOSAIC_PAIR_SEARCH"),
                    help="index = one feature index over all panels (fast); all = brute force over every pair")
    ap.add_argument("--max-points", type=int, default=ts.get("MOSAIC_MAX_POINTS"), help="correspondences per pair used in the global solve")
    ap.add_argument("--stop-after", type=int, default=8,
                    help="last stage to run, 0–8 as numbered in the log ([0/8] preflight … [8/8] output); "
                         "the movie numbers the same steps 1–9")
    ap.add_argument("--model", choices=("poly3+mesh", "poly3", "poly2", "affine", "similarity"), default=ts.get("MOSAIC_MODEL"),
                    help="pose model for fine alignment (poly3 = gentle cubic warp per panel; +mesh = plus a smooth "
                         "256 px displacement mesh fitted on dense 128 px tiles, ≈ 1 min extra; measured best)")
    ap.add_argument("--north-up", action=argparse.BooleanOptionalAction, default=ts.get("MOSAIC_NORTH_UP"),
                    help="turn the mosaic itself to lunar north up, east right (never mirrored), inside the blend; by default "
                         "lunar_finish.py does this as its last step")
    ap.add_argument("--levels", type=int, default=ts.get("MOSAIC_LEVELS"), help="multi-band pyramid levels")
    ap.add_argument("--border", default=ts.get("MOSAIC_BORDER"), help="px cropped at every frame edge; auto = measured stacking artefact + 3 px")
    ap.add_argument("--background", choices=("neutral-black", "neutral", "keep"), default=ts.get("MOSAIC_BACKGROUND"),
                    help="neutral-black = neutral sky, then black space; neutral = neutral sky only; keep = unchanged")
    ap.add_argument("--limb-feather", type=int, default=ts.get("MOSAIC_LIMB_FEATHER"), help="px cosine ramp from the limb to black")
    ap.add_argument("--colour-balance", choices=("highlands", "disk", "none"), default=ts.get("MOSAIC_COLOUR_BALANCE"),
                    help="highlands = neutral grey highlands; disk = neutral whole disk; none = no colour balance")
    ap.add_argument("--workers", type=int, default=ts.get("MOSAIC_WORKERS"), help="parallel worker threads")
    ap.add_argument("--preview", action=argparse.BooleanOptionalAction, default=ts.get("MOSAIC_PREVIEW"), help="live preview window")
    ap.add_argument("--display-tif", action=argparse.BooleanOptionalAction, default=ts.get("MOSAIC_DISPLAY_TIF"),
                    help="also write <out>_display.tif (the mosaic with the movie's tone mapping)")
    ap.add_argument("--record", metavar="MOVIE.mp4", help="also record what the preview shows to a 16:9 MP4 (works without a display)")
    ap.add_argument("--record-vertical", metavar="MOVIE.mp4", help="also record a 9:16 version (1080×1920, e.g. Instagram Reels)")
    ap.add_argument("--log", help="append the console log to this file")
    ap.add_argument("--log-pairs", action="store_true", help="log every accepted pair")
    args = ap.parse_args()
    check_args(ap, args)

    stem = os.path.splitext(os.path.abspath(args.output))[0]
    planned = [args.output, stem + "_layout.png", stem + "_layout_composite.png", stem + "_layout.json",
               stem + "_contributions.png", stem + "_labels.png", args.record, args.record_vertical, args.log]
    if args.display_tif:
        planned.append(stem + "_display.tif")
    try:
        inputs = gather_inputs(args)[0]
    except Exception:
        inputs = []                      # reported by the pipeline's preflight
    mc.check_outputs(planned, inputs + [p for p in args.inputs + [args.manifest] if p and os.path.isfile(p)])
    stg = mc.Staging()                   # outputs appear only when the run succeeds

    pr = mc.Progress(N_STAGES, args.log)
    outputs = open_recorders(stg, args)
    finals = {id(rec): p for (rec, _), p in zip(outputs, [p for p in (args.record, args.record_vertical) if p])}
    landscape = [st for _, st in outputs if st.fmt == "landscape"]
    pv = Preview(not (not args.preview) and preview_possible(), landscape[0] if landscape else ps.Observatory(), outputs)

    def on_log(kind, text):
        """Stage headers become title cards; every other line is shown faintly in the chrome ("what it is doing")."""
        if kind == "header":
            m = re.match(r"\[(\d+)/\d+\] (.*)", text.strip())
            stage, tech = (int(m.group(1)), m.group(2)) if m else (None, text.strip())
            for rec, _ in pv.outputs:
                rec.step = stage
            pv.set_all("log_line", "")
            if pv.active():
                card = lambda st, stage=stage, tech=tech: st.card(stage, FRIENDLY.get(stage, tech), tech)
                if stage == 1 and pv.outputs:
                    # the step-1 contact sheet needs the panels, which are read in step 2: record step 2's card later
                    pv.pending_card = card
                    pv.show(card)
                else:
                    secs = STEP1_CARD_SECONDS if stage == 0 else CARD_SECONDS
                    pv.show(card, record=secs if pv.outputs else None)
            pv.checkpoint()                   # every stage boundary honours pause and abort
        else:
            pv.set_all("log_line", text)
    pr.listeners.append(on_log)
    if pv.active():
        try:
            paths0, _, _ = gather_inputs(args)
            dates = Counter(mc.parse_meta(p)[1].get("date") for p in paths0)
            date0 = dates.most_common(1)[0][0] if dates else None
            subtitle = f"{date0 + ' · ' if date0 else ''}{len(paths0)} panels"
        except Exception:
            subtitle = " · ".join(args.inputs)
        pv.show(lambda st: st.card(None, "Lunar Mosaic", subtitle),
                record=VIDEO_SECONDS["opening"] if pv.outputs else None)
    print(f"mosaic_builder  {len(args.inputs)} input(s) · {args.workers} workers · preview {'on' if pv.enabled else 'off'}", flush=True)
    code = 1
    try:
        code = pv.run(lambda: pipeline(args, pr, pv, stg))
    except (Abort, KeyboardInterrupt):
        print("aborted: no output written (earlier results are unchanged)", flush=True)
        code = 130
    finally:
        for rec, st in outputs:
            try:
                secs = rec.close()
            except IOError as e:
                print(f"ERROR {e}: nothing published", flush=True)
                code = code or 1
                continue
            if code == 0:
                print(f"movie ({rec.W}×{rec.H}): {os.path.abspath(finals[id(rec)])} · {secs:.1f} s · {rec.codec}", flush=True)
        if outputs and code == 0:
            rec = outputs[0][0]
            steps = ["opening"] + [k for k in range(9) if k in rec.per_step] + [k for k in ("final", "fade-out") if k in rec.per_step]
            print("movie timing: " + " · ".join((k if isinstance(k, str) else f"step {k + 1}") +
                                                f" {rec.per_step[k] / rec.FPS:.1f} s" for k in steps), flush=True)
        if code == 0:
            stg.publish()
        else:
            stg.discard()
    pv.exit(code or 0)


def check_args(ap, args):
    """Reject option values the pipeline cannot honour, before anything is read or written."""
    if os.path.splitext(args.output)[1].lower() not in (".tif", ".tiff"):
        ap.error(f"-o {args.output}: the output is a 16-bit TIFF, use a .tif name")
    if not 0 <= args.stop_after <= 8:
        ap.error("--stop-after must be 0–8 (the stage numbers in the log: [0/8] preflight … [8/8] output)")
    if not 1 <= args.levels <= 10:
        ap.error("--levels must be 1–10")
    if args.border != "auto" and not (args.border.isdigit() and int(args.border) < 500):
        ap.error("--border must be auto or a whole number of px (0–499)")
    for name in ("proxy_factor", "features", "workers", "max_points"):
        if getattr(args, name) < 1:
            ap.error(f"--{name.replace('_', '-')} must be at least 1")
    if args.min_keypoints < 0 or args.limb_feather < 0:
        ap.error("--min-keypoints and --limb-feather cannot be negative")
    out_dir = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(out_dir):
        ap.error(f"output folder {out_dir} does not exist")
    if args.exclude_tier and not (args.manifest or any(p.lower().endswith(".json") for p in args.inputs)):
        ap.error("--exclude-tier needs the classifier's manifest: pass --manifest panels/panel_manifest.json")


if __name__ == "__main__":
    main()
