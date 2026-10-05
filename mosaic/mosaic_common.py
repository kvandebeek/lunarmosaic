"""Shared code for panel_classifier.py and mosaic_builder.py.

Image I/O, luminance, subject masks, sky statistics, proxy features, pair discovery
and progress logging. Not meant to be run directly.
"""
import argparse
import glob
import itertools
import os
import re
import sys
import time

import cv2
import numpy as np

FULL = 65535.0          # all metrics are reported on a 0–65535 luminance scale, whatever the bit depth
SAT_LEVEL = 0.98        # fraction of full scale counted as saturated
RNG_SEED = 20260925     # fixed seed: identical input gives identical output
DEFAULT_EXTS = ("tif", "tiff", "png", "jpg", "jpeg")


def physical_memory():
    """Physical RAM in bytes on the supported desktop platforms."""
    if sys.platform == 'win32':
        import ctypes
        class Status(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total', 'avail', 'page', 'avail_page', 'virtual', 'avail_virtual', 'extended')]
        s = Status()
        s.length = ctypes.sizeof(s)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
            raise OSError('Cannot query physical memory.')
        return int(s.total)
    return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')


class HelpFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    """--help keeps the description's line breaks and shows every option's default."""


# ---------------------------------------------------------------- progress logging

class Progress:
    """Line-based progress log: stage headers, throttled progress bars, WARN lines, optional log file."""

    def __init__(self, n_stages, log_path=None):
        self.n_stages = n_stages
        self.stage = 0
        self.t_run = time.time()
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        self.listeners = []        # callables(kind, text): kind in {"header", "line"}; used by the movie recorder
        self.title = ""

    def restart(self):
        """Restart the stage clock (for a second phase inside one stage, so the ETA stays meaningful)."""
        self.t_stage = time.time()
        self._last = 0.0
        self._last_frac = -1.0

    def _emit(self, line, kind="line"):
        print(line, flush=True)
        for cb in self.listeners:
            cb(kind, line)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def header(self, title, number=None):
        self.stage = self.stage + 1 if number is None else number
        self.t_stage = time.time()
        self._last = 0.0
        self._last_frac = -1.0
        self.title = f"[{self.stage}/{self.n_stages}] {title}"
        self._emit(self.title, "header")

    def info(self, text):
        self._emit("      " + text)

    def warn(self, text):
        self._emit("      WARN " + text)

    def tick(self, done, total, extra=""):
        """Progress line at least every 2 % or 5 s (and at the end)."""
        if total <= 0:                                 # nothing to report for an empty job
            return
        now = time.time()
        frac = done / total
        if done < total and frac - self._last_frac < 0.02 and now - self._last < 5:
            return
        self._last, self._last_frac = now, frac
        el = now - self.t_stage
        eta = el / frac - el if frac > 0 else 0
        bar = "#" * int(frac * 20) + "." * (20 - int(frac * 20))
        self._emit(f"      {done}/{total}  [{bar}]  {fmt_t(el)} · ETA {fmt_t(eta)}" + (f"  {extra}" if extra else ""))

    def elapsed(self):
        return fmt_t(time.time() - self.t_run)


def fmt_t(s):
    s = int(round(s))
    return f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- files and metadata

def find_images(inputs, recursive=False, exts=DEFAULT_EXTS):
    """Expand files/folders into a sorted, de-duplicated list of image paths."""
    exts = tuple("." + e.lower().lstrip(".") for e in exts)
    out = []
    for item in inputs:
        if os.path.isdir(item):
            pattern = os.path.join(item, "**", "*") if recursive else os.path.join(item, "*")
            out += [p for p in glob.glob(pattern, recursive=recursive) if p.lower().endswith(exts)]
        elif os.path.isfile(item):
            out.append(item)
        else:
            raise FileNotFoundError(item)
    return sorted({os.path.abspath(p) for p in out}, key=lambda p: (os.path.basename(p), p))


_META = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})_(\d)-(.*?)_+lapl(\d+)_ap(\d+)")


def parse_meta(path):
    """Best-effort metadata from capture/stacker filenames (e.g. FireCapture + AutoStakkert)."""
    name = os.path.splitext(os.path.basename(path))[0]
    m = _META.search(name)
    if not m:
        return name, {}
    date, hh, mm, tenth, label, lapl, ap = m.groups()
    return f"{hh}{mm}_{tenth}", {"date": date, "time": f"{hh}:{mm}.{tenth}", "label": label.strip(),
                                 "quality_estimator": f"lapl{lapl}", "ap_count": int(ap)}


def read_image(path):
    """Read in native bit depth. Returns (BGR float32 in 0–1, dtype name, channels)."""
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise IOError("unreadable image")
    dtype = str(im.dtype)
    if im.dtype == np.uint8:
        x = im.astype(np.float32) / 255.0
    elif im.dtype == np.uint16:
        x = im.astype(np.float32) / 65535.0
    elif im.dtype in (np.float32, np.float64):
        x = im.astype(np.float32)
    else:
        raise IOError(f"unsupported pixel type {dtype}")
    if x.ndim == 2:
        x = x[..., None]
    if x.shape[2] == 4:
        x = x[..., :3]
    return x, dtype, x.shape[2]


def luminance(x):
    """Rec. 709 luminance of a BGR (or 1-channel) float image."""
    if x.shape[2] == 1:
        return x[..., 0].copy()
    return 0.0722 * x[..., 0] + 0.7152 * x[..., 1] + 0.2126 * x[..., 2]


# ---------------------------------------------------------------- output safety

def same_path(a, b):
    """True when two paths name the same file (existing files by identity, others by normalised path)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def check_outputs(outputs, inputs):
    """Refuse to start when a planned output would overwrite an input, or two outputs would share one file."""
    outs = [o for o in outputs if o]
    for i, o in enumerate(outs):
        for p in inputs:
            if same_path(o, p):
                sys.exit(f"refusing to overwrite the input {p}: choose another output name")
        for o2 in outs[:i]:
            if same_path(o, o2):
                sys.exit(f"two outputs of this run would be the same file: {o}")


class Staging:
    """Every output of a run is written under a hidden temporary name next to its final path and renamed into
    place only when the run succeeds: an aborted or failed run leaves no partial files and replaces nothing."""

    def __init__(self):
        self.pending = {}

    def path(self, final):
        final = os.path.abspath(final)
        if final not in self.pending:
            d, name = os.path.split(final)
            root, ext = os.path.splitext(name)
            self.pending[final] = os.path.join(d, f".{root}.partial{ext}")    # keeps the extension for cv2
        return self.pending[final]

    def publish(self):
        for final, tmp in self.pending.items():
            if os.path.exists(tmp):
                os.replace(tmp, final)
        self.pending = {}

    def discard(self):
        for tmp in self.pending.values():
            if os.path.exists(tmp):
                os.remove(tmp)
        self.pending = {}


# ---------------------------------------------------------------- display version of a result

def write_display_tiff(path, img, wp, gamma=1.1, bbox=None, in_scale=1.0):
    """Write the same tone mapping the movies use (white point wp, then gamma) as a 16-bit uncompressed TIFF.
    img: float (0–1 × in_scale) or uint16 BGR; bbox crops (x0, y0, x1, y1). Returns the share of Moon pixels that
    reach full white (the movie's white point clips the very brightest)."""
    x0, y0, x1, y1 = bbox if bbox else (0, 0, img.shape[1], img.shape[0])
    crop = img[y0:y1, x0:x1]
    u16 = np.empty(crop.shape, np.uint16)
    clipped = moon = 0
    for r in range(0, crop.shape[0], 1024):
        c = crop[r:r + 1024].astype(np.float32) / in_scale
        v = c / wp
        clipped += int((v >= 1).any(axis=2).sum())
        moon += int((c.mean(axis=2) > 0.03).sum())
        u16[r:r + 1024] = (np.clip(v, 0, 1) ** (1 / gamma) * 65535 + 0.5).astype(np.uint16)
    if not cv2.imwrite(path, u16, [cv2.IMWRITE_TIFF_COMPRESSION, cv2.IMWRITE_TIFF_COMPRESSION_NONE]):
        raise IOError(f"could not write {path}")
    return clipped / max(moon, 1)


# ---------------------------------------------------------------- stacking-edge artefacts

def edge_artifact_widths(x, max_px=12, ref_px=(6, 11)):
    """Width in px of the stacker's edge artefact on each side (AutoStakkert: 1–2 px green-only line, then a
    1–2 px bright band). Each outer row/column is compared with the rows/columns just inside (ref_px), using
    only the stretch of edge that is not sky; flagged when brightness deviates > 15 % or the colour ratio > 10 %.
    Measured on the 2026-09-25 lunar set: 2–5 px on every edge. Returns {side: width or None (all sky)}."""
    out = {}
    views = {"top": x[:max_px + ref_px[1]], "bottom": x[::-1][:max_px + ref_px[1]],
             "left": x[:, :max_px + ref_px[1]].transpose(1, 0, 2), "right": x[:, ::-1][:, :max_px + ref_px[1]].transpose(1, 0, 2)}
    for side, v in views.items():
        ref = v[ref_px[0]:ref_px[1]].mean(0)                 # (length, channels)
        keep = ref.mean(1) > 0.02
        keep[:64] = keep[-64:] = False                       # corners
        if keep.sum() < 100:
            out[side] = None
            continue
        prof, r = v[:max_px, keep].mean(1), ref[keep].mean(0)
        rel = prof.mean(1) / max(float(r.mean()), 1e-6)
        bad = np.abs(rel - 1) > 0.15
        if x.shape[2] == 3:
            colr = (prof[:, 1] / np.maximum((prof[:, 0] + prof[:, 2]) / 2, 1e-6)) / (r[1] / max((r[0] + r[2]) / 2, 1e-6))
            bad |= np.abs(colr - 1) > 0.10
        idx = np.nonzero(bad)[0]
        out[side] = int(idx.max() + 1) if len(idx) else 0
    return out


def artifact_border(x, safety=3, minimum=6):
    """Per-panel crop that removes the stacking-edge artefact: widest measured edge + safety margin."""
    w = [v for v in edge_artifact_widths(x).values() if v is not None]
    return max(minimum, (max(w) if w else 0) + safety), w


# ---------------------------------------------------------------- masks and sky

def subject_and_sky(L, x, border=32, erode=40, sky_margin=60):
    """Subject mask, sky mask and diagnostics.

    A sky mode exists when ≥ 5 % of the (σ 8 smoothed) image lies below 2 % of full scale.
    Without one, the whole frame is subject; this avoids cutting dark terrain on full-frame panels.
    Erosion only acts on a real subject edge, never on the frame edge.
    """
    h, w = L.shape
    Ls = cv2.GaussianBlur(L, (0, 0), 8)
    dark = Ls < 0.02
    info = {"sky_mode": bool(dark.mean() >= 0.05)}
    if info["sky_mode"]:
        bg = float(np.median(Ls[dark]))
        sig = float(np.median(np.abs(Ls[dark] - bg)) * 1.4826)
        thr = bg + max(5 * sig, 0.002)
        disk = (Ls > thr).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(disk, connectivity=8)
        disk = (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8) if n > 1 else disk
        # fill holes: dark regions that do not touch the frame edge belong to the subject
        inv = (1 - disk).astype(np.uint8)
        n2, lab2 = cv2.connectedComponents(inv, connectivity=4)
        if n2 > 1:
            edge = np.unique(np.concatenate([lab2[0], lab2[-1], lab2[:, 0], lab2[:, -1]]))
            disk = (~np.isin(lab2, edge[edge > 0])).astype(np.uint8)
    else:
        disk = np.ones((h, w), np.uint8)
    coverage = float(disk.mean() * 100)

    sat = (x >= SAT_LEVEL).any(axis=2).astype(np.uint8)
    sat_d = cv2.dilate(sat, np.ones((11, 11), np.uint8))
    # distance to the nearest non-subject pixel; the frame edge does not count as an edge
    dist = cv2.distanceTransform(disk, cv2.DIST_L2, 5)
    m = (dist > erode) & (sat_d == 0)
    m[:border] = m[h - border:] = False               # explicit ends: border 0 must trim nothing
    m[:, :border] = m[:, w - border:] = False

    sky = None
    if info["sky_mode"]:
        far = cv2.distanceTransform(1 - disk, cv2.DIST_L2, 5) > sky_margin
        far[:border] = far[h - border:] = False
        far[:, :border] = far[:, w - border:] = False
        if far.sum() >= 20000:
            sky = far
    return disk.astype(bool), m, sky, coverage, sat, info


# ---------------------------------------------------------------- metrics

def sharpness_metrics(L, m):
    """Single-image metrics on the 0–65535 scale, inside the subject mask.

    Against same-terrain relative detail on the 2026-09-25 lunar set (rank correlation):
    Tenengrad 0.89, band-pass energy 0.88, Laplacian variance 0.75. Brightness-normalised
    variants did worse (≈ 0.70) and are not used. Tenengrad is the single-image tier metric.
    """
    if m.sum() < 1000:
        return {"laplacian_var": None, "tenengrad": None, "bandpass_energy": None}
    Y = L * FULL
    lap = cv2.Laplacian(Y, cv2.CV_32F, ksize=3)
    gx = cv2.Sobel(Y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(Y, cv2.CV_32F, 0, 1, ksize=3)
    dog = cv2.GaussianBlur(Y, (0, 0), 1.0) - cv2.GaussianBlur(Y, (0, 0), 2.5)
    return {"laplacian_var": float(lap[m].var()),
            "tenengrad": float((gx[m] ** 2 + gy[m] ** 2).mean()),
            "bandpass_energy": float((dog[m] ** 2).mean())}


def phase_shift(a, b, window=None):
    """Sub-pixel shift of b relative to a. Works on copies: cv2.phaseCorrelate overwrites its inputs
    (it applies the window in place), which silently corrupts arrays that are reused afterwards."""
    return cv2.phaseCorrelate(np.array(a, np.float32, copy=True), np.array(b, np.float32, copy=True), window)


def band_energy(L, s1=1.0, s2=2.5):
    return (cv2.GaussianBlur(L, (0, 0), s1) - cv2.GaussianBlur(L, (0, 0), s2)) ** 2


# ---------------------------------------------------------------- proxies and features

def proxy_crop(img, factor):
    """The image cut to whole factor × factor blocks: only then is a 1/factor INTER_AREA proxy exactly
    p_full = factor · p_proxy + (factor − 1) / 2, the mapping every proxy conversion assumes."""
    h, w = img.shape[:2]
    return img[:h // factor * factor, :w // factor * factor]


def make_proxy(L, m, factor):
    """8-bit, contrast-stretched, CLAHE-enhanced proxy at 1/factor size, plus its feature mask."""
    L, m = proxy_crop(L, factor), proxy_crop(m, factor)
    h, w = L.shape
    size = (w // factor, h // factor)
    P = cv2.resize(L, size, interpolation=cv2.INTER_AREA) if factor > 1 else L.copy()
    pm = cv2.resize(m.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) if factor > 1 else m.astype(np.uint8)
    vals = P[pm > 0] if pm.any() else P.ravel()
    lo, hi = np.percentile(vals, [0.5, 99.5])
    P8 = np.clip((P - lo) / (hi - lo + 1e-9) * 255, 0, 255).astype(np.uint8)
    P8 = cv2.createCLAHE(2.0, (8, 8)).apply(P8)
    # the subject mask was eroded for metrics; features may come closer to the limb
    pm = cv2.dilate(pm, np.ones((3, 3), np.uint8), iterations=max(1, 24 // factor))
    return P8, pm


def detect_features(P8, pm, n_features=4000):
    sift = cv2.SIFT_create(nfeatures=n_features, contrastThreshold=0.02)
    kp, d = sift.detectAndCompute(P8, pm)
    pts = np.float32([k.pt for k in kp]) if kp else np.zeros((0, 2), np.float32)
    return pts, d


def proxy_factor(sizes, target_long_side=1000):
    long_side = float(np.median([max(h, w) for h, w in sizes]))
    return max(1, int(round(long_side / target_long_side)))


def proxy_to_full(T, f):
    """Convert a 2×3 transform between proxies (INTER_AREA, integer factor f) to full resolution."""
    C = np.array([[1.0 / f, 0, 0.5 / f - 0.5], [0, 1.0 / f, 0.5 / f - 0.5], [0, 0, 1]])
    return (np.linalg.inv(C) @ np.vstack([np.asarray(T, np.float64), [0, 0, 1]]) @ C)[:2]


# ---------------------------------------------------------------- pair discovery

def candidate_pairs(descs, min_votes=4, top_k=20):
    """Candidate overlapping pairs from one global FLANN index over all descriptors.

    Each descriptor votes for the other image holding its nearest neighbour (ratio 0.8 against the
    next other-image neighbour). Candidate if ≥ min_votes, or among an image's top_k partners.
    """
    n = len(descs)
    valid = [i for i, d in enumerate(descs) if d is not None and len(d) >= 2]
    if len(valid) < 2:
        return set()
    alld = np.vstack([descs[i] for i in valid]).astype(np.float32)
    owner = np.concatenate([np.full(len(descs[i]), i) for i in valid])
    cv2.setRNGSeed(RNG_SEED)
    fl = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
    fl.add([alld])
    fl.train()
    votes = np.zeros((n, n), np.int32)
    for i in valid:
        for q in fl.knnMatch(descs[i].astype(np.float32), k=8):
            o = [x for x in q if owner[x.trainIdx] != i]
            if len(o) >= 2 and o[0].distance < 0.8 * o[1].distance:
                votes[i, owner[o[0].trainIdx]] += 1
    V = votes + votes.T
    cand = {(int(a), int(b)) for a, b in zip(*np.where(np.triu(V, 1) >= min_votes))}
    for i in valid:
        for j in np.argsort(-V[i])[:top_k]:
            if V[i, j] > 0 and i != j:
                cand.add((min(i, int(j)), max(i, int(j))))
    return cand


def all_pairs(n):
    return set(itertools.combinations(range(n), 2))


def _knn(query, train, k, method):
    if method == "bf":
        return cv2.BFMatcher(cv2.NORM_L2).knnMatch(query, train, k=k)
    # a fresh FLANN index per call: thread-safe, and ~10× faster than brute force at 4,000 descriptors
    fl = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
    return fl.knnMatch(query.astype(np.float32), train.astype(np.float32), k=k)


def verify_pair(fa, fb, pma, pmb, ransac_px=1.0, min_inliers=30, min_ratio=0.25,
                scale_range=(0.97, 1.03), min_overlap=0.05, min_hull=0.02, matcher="flann"):
    """Match two proxies and verify a similarity transform. Returns (result dict | None, reject reason).

    RANSAC draws from OpenCV's thread-local RNG; seeding it per pair makes results independent of which
    worker thread handles the pair (without this, ~30 % of pairs varied between identical runs)."""
    cv2.setRNGSeed(RNG_SEED)
    (pa, da), (pb, db) = fa, fb
    if da is None or db is None or len(da) < 20 or len(db) < 20:
        return None, "few-features"
    m1 = _knn(da, db, 2, matcher)
    m2 = _knn(db, da, 1, matcher)
    back = {m[0].queryIdx: m[0].trainIdx for m in m2 if m}
    good = [m[0] for m in m1 if len(m) == 2 and m[0].distance < 0.75 * m[1].distance
            and back.get(m[0].trainIdx) == m[0].queryIdx]
    if len(good) < 20:
        return None, "no-overlap"
    A = pa[[g.queryIdx for g in good]]
    B = pb[[g.trainIdx for g in good]]
    T, inl = cv2.estimateAffinePartial2D(A, B, method=cv2.RANSAC, ransacReprojThreshold=ransac_px,
                                         maxIters=5000, confidence=0.999)
    if T is None:
        return None, "few-inliers"
    inl = inl.ravel().astype(bool)
    n = int(inl.sum())
    if n < min_inliers:
        return None, "few-inliers"
    if n / len(good) < min_ratio:
        return None, "low-inlier-ratio"
    s = float(np.hypot(T[0, 0], T[1, 0]))
    if not scale_range[0] < s < scale_range[1]:
        return None, "scale"
    hb, wb = pmb.shape
    mb_in_a = cv2.warpAffine(pmb, cv2.invertAffineTransform(T), (pma.shape[1], pma.shape[0]), flags=cv2.INTER_NEAREST)
    ov = (pma > 0) & (mb_in_a > 0)
    ov_frac = ov.sum() / max(1, min((pma > 0).sum(), (pmb > 0).sum()))
    if ov_frac < min_overlap:
        return None, "small-overlap"
    hull = cv2.convexHull(A[inl].astype(np.float32))
    if cv2.contourArea(hull) < min_hull * ov.sum():
        return None, "degenerate"
    return {"T": T, "inliers": n, "ratio": n / len(good), "scale": s,
            "rot_deg": float(np.degrees(np.arctan2(T[1, 0], T[0, 0]))), "overlap": float(ov_frac),
            "pts_a": A[inl], "pts_b": B[inl]}, None
