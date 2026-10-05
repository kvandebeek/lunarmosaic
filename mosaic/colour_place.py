#!/usr/bin/env python3
"""Colour panels placed one by one onto the luminance mosaic (plan phase 6; DESIGN.md §3; D8, D15, D16).

    python3 colour_place.py PANELS… --canvas derived/L.tif -o derived/colour_canvas.tif [--scale-ratio R]

Each panel is found at up to three levels: global (SIFT on the canvas reduced to about 2048 px, the panel
resampled to the same pixel scale), intermediate (512 px tiles on the canvas halved — only when the global level
was reduced more than 2×, where its 1 px RANSAC threshold spans more than the fine window) and fine (256 px
canvas tiles, phase correlation against the panel warped into each tile). It then gets the simplest pose model
its held-out tiles justify — similarity, affine or poly3 — or is rejected with the reason. Red and blue are moved
onto green when both RGB-shift estimators agree (D16). Panels are matched in brightness and colour, then blended
into the colour canvas at the colour data's own sampling: canvas = K · colour canvas, K = [[k, 0, (k−1)/2],
[0, k, (k−1)/2], [0, 0, 1]] with k = max(1, colour px ÷ canvas px) as a float (D8).

Writes the colour canvas as 16-bit RGBA (alpha = coverage) and a report (default: colour_place.json beside it).
Progress: one line per panel, "[k/N] name · …".
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import sys
import time

import cv2
import numpy as np
import tifffile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measurements as ms  # noqa: E402
import mosaic_common as mc  # noqa: E402
import mosaic_stages as mst  # noqa: E402

GLOBAL_SIDE = 2048            # the global level's canvas, long side in px
TILE_MID, TILE_FINE = 512, 256
RESPONSE_MIN, SHIFT_MAX = 0.05, 6.0
MIN_TILES, REJECT_RMS = 4, 2.0
MODELS = (('similarity', 3), ('affine', 6), ('poly3', 12))      # pose model, fitted tiles it needs (D15)
FEATHER = 64                  # colour-canvas px from a panel's edge to full weight
ROUNDS = 2                    # fine level: correlate, fit, correlate again on the refined pose


# ---------------------------------------------------------------- the canvas and the panels

class Canvas:
    """The luminance mosaic, memory-mapped; luminance (channel mean, 0–1) by region and reduced."""

    def __init__(self, path):
        try:
            raw = tifffile.memmap(path, mode='r')
        except (ValueError, OSError):
            raw = tifffile.imread(path)
        if raw.ndim == 3 and raw.shape[0] in (3, 4) and raw.shape[-1] not in (3, 4):
            raw = np.moveaxis(raw, 0, -1)
        self.raw, (self.h, self.w) = raw, raw.shape[:2]
        self.top = 65535.0 if raw.dtype == np.uint16 else 255.0 if raw.dtype == np.uint8 else 1.0

    def lum(self, y0, x0, h, w):
        """Luminance of a region; zero where it leaves the canvas."""
        out = np.zeros((h, w), np.float32)
        ya, xa, yb, xb = max(0, y0), max(0, x0), min(self.h, y0 + h), min(self.w, x0 + w)
        if yb > ya and xb > xa:
            a = np.asarray(self.raw[ya:yb, xa:xb], np.float32)
            out[ya - y0:yb - y0, xa - x0:xb - x0] = (a[..., :3].mean(axis=2) if a.ndim == 3 else a) / self.top
        return out

    def reduced(self, g):
        """Luminance reduced by the integer factor g: whole g × g blocks, so pixel s is canvas g·s + (g − 1)/2."""
        H, W = self.h // g * g, self.w // g * g
        out = np.empty((H // g, W // g), np.float32)
        band = max(g, (1 << 22) // max(1, W) // g * g)
        for y in range(0, H, band):
            a = self.lum(y, 0, min(band, H - y), W)
            out[y // g:(y + len(a)) // g] = cv2.resize(a, (W // g, len(a) // g), interpolation=cv2.INTER_AREA)
        return out


def read_panel(path):
    """RGB float32 (0–1), its luminance, the subject mask for features and the sky mask (None without sky)."""
    x, _, ch = mc.read_image(path)
    if ch < 3:
        raise ValueError('a colour panel needs three channels')
    L = mc.luminance(x)
    disk, m, sky, _, _, _ = mc.subject_and_sky(L, x, border=16, erode=8)
    return np.ascontiguousarray(x[..., 2::-1]), L, m, sky, disk


def K_matrix(k):
    return np.array([[k, 0, (k - 1) / 2], [0, k, (k - 1) / 2], [0, 0, 1.0]])


# ---------------------------------------------------------------- pose models (D15)

def make_model(name, size):
    if name == 'poly3':
        return mst.PolyModel(3, R=max(size) / 2, ref_size=tuple(size))
    return mst.Model(name, ref_size=tuple(size))


def solve(model, p, X, target=None, huber=1.0, iters=12):
    """Weighted least squares with Huber IRLS for one panel against fixed canvas points: centred panel px p -> canvas
    px X. With `target`, the model's priors pull towards it (poly3: bending towards zero, the linear part towards the
    affine fit); without, no prior — the scale is k, not 1."""
    J = model.J(p)
    pw = model.prior_w if target is not None else np.zeros(model.d)
    tgt = target if target is not None else np.zeros(model.d)
    hw = np.ones(len(p))
    th = tgt
    for it in range(iters):
        H = np.einsum('nki,nkj,n->ij', J, J, hw) + np.diag(pw)
        g = np.einsum('nki,nk,n->i', J, X, hw) + pw * tgt
        th = np.linalg.solve(H + 1e-9 * np.eye(model.d), g)
        e = np.hypot(*(model.apply(th, p) - X).T)
        new = np.where(e <= huber, 1.0, huber / np.maximum(e, 1e-9))
        if it and np.abs(new - hw).max() < 1e-3:
            break
        hw = new
    return th


def affine_target(model, p, X, size):
    """A poly3's prior target: the affine fit, in the polynomial's parameter layout."""
    aff = make_model('affine', size)
    A = aff.mat(solve(aff, p, X))
    th, k = np.zeros(model.d), model.k
    th[0], th[1], th[2] = A[0, 2] / model.R, A[0, 0], A[0, 1]
    th[k], th[k + 1], th[k + 2] = A[1, 2] / model.R, A[1, 0], A[1, 1]
    return th


def fit_pose(P, X, size):
    """D15: hold out every fourth tile, fit each model the remaining tiles support, keep the simplest whose held-out
    RMS is within 10 % of the best; refit it on all tiles. Returns (name, params, held-out RMS, all held-out
    residuals, per-model RMS) or None with too few tiles."""
    p = mst.centred(P, size)
    held = np.arange(len(p)) % 4 == 3
    if not held.any():
        return None
    scores = {}
    for name, need in MODELS:
        if (~held).sum() < need:
            continue
        model = make_model(name, size)
        target = affine_target(model, p[~held], X[~held], size) if name == 'poly3' else None
        th = solve(model, p[~held], X[~held], target)
        e = np.hypot(*(model.apply(th, p[held]) - X[held]).T)
        scores[name] = (float(np.sqrt(np.mean(e ** 2))), e)
    if not scores:
        return None
    best = min(v[0] for v in scores.values())
    name = next(n for n, _ in MODELS if n in scores and scores[n][0] <= 1.1 * best)
    model = make_model(name, size)
    th = solve(model, p, X, affine_target(model, p, X, size) if name == 'poly3' else None)
    return name, th, scores[name][0], scores[name][1], {n: v[0] for n, v in scores.items()}


class Pose:
    """A panel's placement: panel px <-> canvas px, for any model (or a plain 3×3 matrix)."""

    def __init__(self, size, name='matrix', params=None, matrix=None):
        self.size, self.name = tuple(size), name
        self.matrix = None if matrix is None else np.asarray(matrix, float)
        self.params = None if params is None else np.asarray(params, float)
        self.model = make_model(name, size) if params is not None else None

    def to_canvas(self, P):
        P = np.asarray(P, float)
        if self.model is None:
            return P @ self.matrix[:2, :2].T + self.matrix[:2, 2]
        return mst.to_canvas(self.model, self.params, self.size, P)

    def to_panel(self, X):
        X = np.asarray(X, float)
        if self.model is None:
            M = np.linalg.inv(self.matrix)
            return X @ M[:2, :2].T + M[:2, 2]
        return mst.from_canvas(self.model, self.params, self.size, X)

    def footprint(self):
        w, h = self.size
        t = np.linspace(0, 1, 9)
        edge = np.vstack([np.stack([t * w, 0 * t], 1), np.stack([t * w, 0 * t + h], 1),
                          np.stack([0 * t, t * h], 1), np.stack([0 * t + w, t * h], 1)]) - 0.5
        return self.to_canvas(edge)

    def scale_rot(self):
        if self.model is None:
            A = self.matrix[:2, :2]
        else:
            A = self.model.mat(self.params, self.size) if isinstance(self.model, mst.PolyModel) else self.model.mat(self.params)
            A = np.asarray(A)[:, :2]
        return float(np.sqrt(abs(np.linalg.det(A)))), float(np.degrees(np.arctan2(A[1, 0] - A[0, 1], A[0, 0] + A[1, 1])))

    def as_json(self):
        if self.model is None:
            return dict(model='matrix', matrix=self.matrix.tolist(), size=list(self.size))
        return dict(model=self.name, params=self.params.tolist(), size=list(self.size))


# ---------------------------------------------------------------- finding a panel

class Finder:
    def __init__(self, canvas):
        self.canvas = canvas
        g = self.g = max(1, math.ceil(max(canvas.h, canvas.w) / GLOBAL_SIDE))
        self.small = canvas.reduced(g)
        mask = cv2.GaussianBlur(self.small, (0, 0), 2) > 0.02
        self.P8, self.pm = mc.make_proxy(self.small, mask, 1)
        self.feats = mc.detect_features(self.P8, self.pm, 16000)
        self.texture = ms.texture(self.small)

    def resampled_features(self, L, mask, f):
        h, w = L.shape
        size = (max(16, round(w * f)), max(16, round(h * f)))
        Lr = cv2.resize(L, size, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
        mr = cv2.resize(mask.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0
        P8, pm = mc.make_proxy(Lr, mr, 1)
        A = np.array([[size[0] / w, 0, 0.5 * size[0] / w - 0.5], [0, size[1] / h, 0.5 * size[1] / h - 0.5], [0, 0, 1.0]])
        return mc.detect_features(P8, pm, 4000), pm, A

    def scale_estimate(self, L, mask):
        """The panel's pixel in canvas px from a wide-gate match (D10), or None."""
        f = min(1.0, 1000 / max(L.shape))
        feats, pm, A = self.resampled_features(L, mask, f)
        r, _ = mc.verify_pair(feats, self.feats, pm, self.pm, scale_range=(0.15, 7), min_inliers=40, min_ratio=0.3)
        return None if r is None else r['scale'] * self.g / A[0, 0]

    def find_global(self, L, mask, ratio):
        """Panel px -> canvas px (3×3) from SIFT on the panel resampled to the reduced canvas's pixel scale, with the
        scale gated to 0.95–1.05 and rotation free."""
        feats, pm, A1 = self.resampled_features(L, mask, ratio / self.g)
        r, why = mc.verify_pair(feats, self.feats, pm, self.pm, scale_range=(0.95, 1.05))
        if r is None:
            return None, why
        g = self.g
        A2 = np.array([[g, 0, (g - 1) / 2], [0, g, (g - 1) / 2], [0, 0, 1.0]])
        return A2 @ np.vstack([r['T'], [0, 0, 1]]) @ A1, dict(inliers=int(r['inliers']), rotation_deg=r['rot_deg'])

    def tiles(self, L, valid, pose, ratio, reduce=1, tile=TILE_FINE, max_tiles=48, border=16):
        """Correspondences (canvas px, panel px) from textured canvas tiles phase-correlated against the panel
        warped into each tile's frame; reduce 2 works on the halved canvas (the intermediate level). The canvas
        tile is first blurred to the panel's sampling, whose pixels are `ratio` canvas px."""
        c = self.canvas
        frame = np.zeros(valid.shape, bool)
        frame[border:-border, border:-border] = True
        foot = pose.footprint()
        x0, y0 = np.floor(foot.min(axis=0) / reduce).astype(int)
        x1, y1 = np.ceil(foot.max(axis=0) / reduce).astype(int)
        half, stride = tile // 2, tile // 2
        xs = np.arange(x0 + half, x1 - half + 1, stride)
        ys = np.arange(y0 + half, y1 - half + 1, stride)
        if not len(xs) or not len(ys):
            return np.zeros((0, 2)), np.zeros((0, 2))
        cx, cy = [v.ravel().astype(float) for v in np.meshgrid(xs, ys)]
        # keep tiles whose corners, shifted by the search window, stay inside the panel's frame, with the centre on
        # the lit disk (tiles across the limb correlate best: they hold its edge)
        ok = np.ones(len(cx), bool)
        h, w = valid.shape
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, 0)):
            X = np.stack([cx + dx * (half + SHIFT_MAX), cy + dy * (half + SHIFT_MAX)], 1) * reduce + (reduce - 1) / 2
            q = np.rint(pose.to_panel(X)).astype(int)
            inside = (q[:, 0] >= 0) & (q[:, 0] < w) & (q[:, 1] >= 0) & (q[:, 1] < h)
            ok &= inside
            ok[inside] &= frame[q[inside, 1], q[inside, 0]] if dx or dy else valid[q[inside, 1], q[inside, 0]]
        if not ok.any():
            return np.zeros((0, 2)), np.zeros((0, 2))
        cx, cy = cx[ok], cy[ok]
        ts = self.texture
        g = self.g
        score = ts[np.clip((cy * reduce / g).astype(int), 0, ts.shape[0] - 1), np.clip((cx * reduce / g).astype(int), 0, ts.shape[1] - 1)]
        sigma = 0.5 * math.sqrt(max(0.0, (ratio / reduce) ** 2 - 1))
        win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
        out_X, out_P = [], []
        # spread over the panel: the most textured tile of every 2 × 2-tile cell first, then the rest by texture
        order = np.argsort(-score, kind='stable')
        cell = (cx[order] // (2 * tile)) * 100003 + cy[order] // (2 * tile)
        _, first = np.unique(cell, return_index=True)
        chosen = list(order[np.sort(first)]) + [i for j, i in enumerate(order) if j not in set(first)]
        for i in chosen[:max_tiles]:
            ux, uy = cx[i] - half, cy[i] - half            # tile origin, reduced px
            if reduce == 1:
                A = c.lum(int(uy), int(ux), tile, tile)
            else:
                A = cv2.resize(c.lum(int(uy) * reduce, int(ux) * reduce, tile * reduce, tile * reduce), (tile, tile),
                               interpolation=cv2.INTER_AREA)
            if float(A.std()) < 1e-4:
                continue
            if sigma > 0.05:
                A = cv2.GaussianBlur(A, (0, 0), sigma)
            Xr = np.stack([(ux + np.arange(tile))[None, :].repeat(tile, 0).ravel(),
                           (uy + np.arange(tile))[:, None].repeat(tile, 1).ravel()], 1).astype(float)
            q = pose.to_panel(Xr * reduce + (reduce - 1) / 2)
            W = cv2.remap(L, q[:, 0].reshape(tile, tile).astype(np.float32), q[:, 1].reshape(tile, tile).astype(np.float32),
                          cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            (sx, sy), r = mc.phase_shift(A - A.mean(), W - W.mean(), win)
            if r < RESPONSE_MIN or math.hypot(sx, sy) > SHIFT_MAX:
                continue
            centre = np.array([ux + (tile - 1) / 2, uy + (tile - 1) / 2])
            Xc = centre * reduce + (reduce - 1) / 2
            out_X.append(Xc)
            out_P.append(pose.to_panel(((centre + [sx, sy]) * reduce + (reduce - 1) / 2)[None])[0])
        return np.array(out_X).reshape(-1, 2), np.array(out_P).reshape(-1, 2)

    def place(self, L, mask, valid, ratio, initial=None):
        """The pose of one panel and how it was found; (Pose, info) or (None, info with the reason)."""
        info = {}
        size = L.shape[::-1]
        if initial is None:
            T, why = self.find_global(L, mask, ratio)
            if T is None:
                return None, dict(reason=f'not found on the canvas ({why})')
            info['global'] = why
        else:
            T, info['global'] = np.asarray(initial, float), 'given'
        pose = Pose(size, matrix=T)
        if initial is None and self.g > 2:
            X, P = self.tiles(L, valid, pose, ratio, reduce=2, tile=TILE_MID, max_tiles=24)
            info['intermediate_tiles'] = len(X)
            if len(X) >= 3:
                pose = Pose(size, 'similarity', solve(make_model('similarity', size), mst.centred(P, size), X))
        fit = None
        for _ in range(ROUNDS):
            X, P = self.tiles(L, valid, pose, ratio)
            if len(X) < MIN_TILES:
                return None, dict(info, tiles=len(X), reason=f'only {len(X)} tiles correlated (at least {MIN_TILES} needed)')
            fit = fit_pose(P, X, size)
            if fit is None:
                return None, dict(info, tiles=len(X), reason='too few tiles to hold any out')
            pose = Pose(size, fit[0], fit[1])
        name, th, rms, held_e, scores = fit
        info.update(tiles=len(X), model=name, held_out_rms=rms, models=scores)
        held = np.arange(len(X)) % 4 == 3
        info['samples'] = [[float(x), float(y), float(e)] for (x, y), e in zip(X[held], held_e)]
        if rms > REJECT_RMS:
            return None, dict(info, reason=f'held-out residual {rms:.2f} px (at most {REJECT_RMS:.1f} allowed)')
        return pose, info


# ---------------------------------------------------------------- photometry and blending

def sky_medians(panels):
    """Per-channel sky median of each panel; a panel without sky uses the set's median."""
    own = {n: (np.median(v['rgb'][v['sky']], axis=0) if v['sky'] is not None else None) for n, v in panels.items()}
    found = [m for m in own.values() if m is not None]
    common = np.median(found, axis=0) if found else np.zeros(3)
    return {n: (m if m is not None else common) for n, m in own.items()}


def sample(img, q):
    """Bilinear samples of img at points q (N × 2): laid out as a 2-D map, since remap's maps stay under 32767 rows."""
    n, width = len(q), 1024
    pad = np.zeros((-(-n // width) * width, 2), np.float32)
    pad[:n] = q
    mx, my = pad[:, 0].reshape(-1, width), pad[:, 1].reshape(-1, width)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR).reshape(-1, img.shape[2])[:n]


def overlap_ratio(a, b, k):
    """Median channel ratios b / a over the lit area both panels cover, sampled on an 8 px colour-canvas grid."""
    fa, fb = a['pose'].footprint(), b['pose'].footprint()
    inter, poly = cv2.intersectConvexConvex(cv2.convexHull(fa.astype(np.float32)), cv2.convexHull(fb.astype(np.float32)))
    if inter <= 0 or poly is None:
        return None
    poly = poly.reshape(-1, 2)
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    step = 8 * k
    gx, gy = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
    X = np.stack([gx.ravel(), gy.ravel()], 1)
    vals = []
    for v in (a, b):
        q = v['pose'].to_panel(X)
        h, w = v['L'].shape
        ok = (q[:, 0] >= 16) & (q[:, 0] < w - 17) & (q[:, 1] >= 16) & (q[:, 1] < h - 17)
        s = np.zeros((len(X), 3), np.float32)
        if ok.any():
            s[ok] = sample(v['rgb'], q[ok])
        vals.append((s, ok))
    (sa, oa), (sb, ob) = vals
    use = oa & ob & (sa.min(axis=1) > 0.02) & (sb.min(axis=1) > 0.02)
    if use.sum() < 200:
        return None
    return np.median(sb[use] / sa[use], axis=0)


def photometry(panels, k, log):
    """Sky medians subtracted; log-gains per channel solved within each overlap component with one panel fixed;
    every further component matched to the largest by its median channels on the disk (stated in the report)."""
    sky = sky_medians(panels)
    for n, v in panels.items():
        v['rgb'] = np.clip(v['rgb'] - sky[n], 0, None)
        v['sky_median'] = sky[n].tolist()
    names = sorted(panels)
    pairs = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = overlap_ratio(panels[a], panels[b], k)
            if r is not None and np.all(r > 0):
                pairs[(a, b)] = r
    parent = {n: n for n in names}

    def root(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for a, b in pairs:
        parent[root(a)] = root(b)
    comps = {}
    for n in names:
        comps.setdefault(root(n), []).append(n)
    comps = sorted(comps.values(), key=lambda c: (-len(c), c))
    gains = {n: np.ones(3) for n in names}
    for comp in comps:
        if len(comp) < 2:
            continue
        free = comp[1:]                     # the first panel of the component keeps gain 1
        col = {n: j for j, n in enumerate(free)}
        rows = [(a, b, r) for (a, b), r in pairs.items() if a in comp and b in comp]
        for ch in range(3):
            Asys, y = [], []
            for a, b, r in rows:
                row = np.zeros(len(free))     # log g_a − log g_b = log(b / a)
                if a in col:
                    row[col[a]] += 1
                if b in col:
                    row[col[b]] -= 1
                Asys.append(row)
                y.append(math.log(r[ch]))
            sol, *_ = np.linalg.lstsq(np.array(Asys).reshape(-1, len(free)), np.array(y), rcond=None)
            for n in free:
                gains[n][ch] = math.exp(sol[col[n]])
    notes = []
    if len(comps) > 1:
        def disk_median(comp):
            vals = [panels[n]['rgb'][panels[n]['disk']] * gains[n] for n in comp]
            return np.median(np.concatenate(vals), axis=0)
        ref = disk_median(comps[0])
        for comp in comps[1:]:
            m = disk_median(comp)
            adj = np.where(m > 1e-4, ref / np.maximum(m, 1e-4), 1.0)
            for n in comp:
                gains[n] = gains[n] * adj
            notes.append(f'{", ".join(comp)} share no overlap with {", ".join(comps[0][:3])}' +
                         (' …' if len(comps[0]) > 3 else '') + ': matched by their median colour on the disk')
            log(notes[-1])
    for n in names:
        panels[n]['rgb'] = panels[n]['rgb'] * gains[n].astype(np.float32)
        panels[n]['gain'] = gains[n].tolist()
    return dict(overlaps=len(pairs), components=[c for c in comps], notes=notes)


def blend(panels, k, size_canvas):
    """Each panel warped with K⁻¹·T into the colour canvas, weighted by its distance to the panel edge (FEATHER px
    to full weight); returns RGBA float32 with alpha = coverage (the summed weight, at most 1)."""
    Wc, Hc = math.ceil(size_canvas[0] / k), math.ceil(size_canvas[1] / k)
    acc = np.zeros((Hc, Wc, 3), np.float32)
    wacc = np.zeros((Hc, Wc), np.float32)
    K = K_matrix(k)
    for n in sorted(panels):
        v = panels[n]
        foot = (v['pose'].footprint() - [(k - 1) / 2, (k - 1) / 2]) / k
        x0, y0 = np.maximum(0, np.floor(foot.min(axis=0)).astype(int) - 2)
        x1, y1 = np.minimum([Wc, Hc], np.ceil(foot.max(axis=0)).astype(int) + 3)
        if x1 <= x0 or y1 <= y0:
            continue
        g = 16
        gx, gy = np.meshgrid(np.arange(x0, x1 + g, g, dtype=float), np.arange(y0, y1 + g, g, dtype=float))
        C = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)], 1)
        q = v['pose'].to_panel((C @ K.T)[:, :2])
        bw, bh = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
        mx = cv2.remap(q[:, 0].reshape(gx.shape).astype(np.float32), xx / g, yy / g, cv2.INTER_LINEAR)
        my = cv2.remap(q[:, 1].reshape(gx.shape).astype(np.float32), xx / g, yy / g, cv2.INTER_LINEAR)
        rgb = cv2.remap(v['rgb'], mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
        h, w = v['L'].shape
        inside = np.zeros((h, w), np.uint8)
        b = v.get('border', 16)
        inside[b:h - b, b:w - b] = 1
        dist = cv2.distanceTransform(inside, cv2.DIST_L2, 5) * (v['ratio'] / k)       # colour-canvas px
        weight = np.clip(cv2.remap(dist, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) / FEATHER, 0, 1)
        acc[y0:y1, x0:x1] += np.clip(rgb, 0, None) * weight[..., None]
        wacc[y0:y1, x0:x1] += weight
    rgb = np.divide(acc, wacc[..., None], out=np.zeros_like(acc), where=wacc[..., None] > 1e-6)
    return np.dstack([np.clip(rgb, 0, 1), np.clip(wacc, 0, 1)])


# ---------------------------------------------------------------- the run

def run(args, log=print):
    t0 = time.time()
    canvas = Canvas(args.canvas)
    paths = mc.find_images(args.panels, recursive=False)
    if not paths:
        raise ValueError('no colour panels found')
    initial = json.load(open(args.initial, encoding='utf-8')) if args.initial else {}
    log(f'colour_place  {len(paths)} panel(s) onto {os.path.basename(args.canvas)} ({canvas.w} × {canvas.h} px)')
    finder = Finder(canvas)
    panels = {}
    for path in paths:
        rgb, L, mask, sky, disk = read_panel(path)
        name = os.path.splitext(os.path.basename(path))[0]
        b = 16
        valid = np.zeros(L.shape, bool)
        valid[b:-b, b:-b] = True
        panels[name] = dict(path=path, rgb=rgb, L=L, mask=mask, sky=sky, disk=disk, valid=valid & (disk > 0), border=b)
    ratio, source = args.scale_ratio, 'given'
    if not ratio:
        found = [r for r in (finder.scale_estimate(v['L'], v['mask']) for v in list(panels.values())[:5]) if r]
        if not found:
            raise ValueError('the colour panels could not be matched to the luminance canvas at any scale; give --scale-ratio')
        ratio, source = float(np.median(found)), f'estimated on {len(found)} panel(s)'
    k = max(1.0, ratio)
    log(f'colour pixel = {ratio:.4f} canvas px ({source}) · colour canvas k = {k:.4f}')

    def place(item):
        name, v = item
        shift = ms.rgb_shift_decision(v['rgb'])
        if any(shift[c]['apply'] != [0.0, 0.0] for c in 'rb'):
            v['rgb'] = ms.shift_channels(v['rgb'], shift)
        pose, info = finder.place(v['L'], v['mask'], v['valid'], ratio, initial.get(name))
        return name, pose, info, shift
    report = {}
    with ThreadPoolExecutor(max(1, args.workers)) as ex:
        for i, (name, pose, info, shift) in enumerate(ex.map(place, sorted(panels.items())), 1):
            v = panels[name]
            v['pose'], v['ratio'] = pose, ratio
            entry = dict(path=v['path'], state='placed' if pose else 'rejected', **{x: info[x] for x in info if x != 'samples'},
                         rgb_shift={c: dict(shift[c], estimates=shift['estimates'][c]) for c in 'rb'})
            if pose:
                s, rot = pose.scale_rot()
                entry.update(transform=pose.as_json(), scale=s, rotation_deg=rot, samples=info.get('samples', []))
            report[name] = entry
            what = (f'{info["model"]} · {info["tiles"]} tiles · held-out {info["held_out_rms"]:.2f} px' if pose
                    else f'rejected: {info["reason"]}')
            shifts = ' · '.join(f'{c.upper()} {shift[c]["why"]}' for c in 'rb')
            log(f'[{i}/{len(panels)}] {name} · {what} · {shifts}')
    placed = {n: v for n, v in panels.items() if v['pose'] is not None}
    if not placed:
        raise ValueError('no colour panel could be placed: ' + '; '.join(f'{n}: {e["reason"]}' for n, e in report.items()))
    phot = photometry(placed, k, log)
    rgba = blend(placed, k, (canvas.w, canvas.h))
    tifffile.imwrite(args.output, np.round(rgba * 65535).astype(np.uint16), photometric='rgb', extrasamples=['unassalpha'],
                     metadata=None)
    for n, v in placed.items():
        report[n].update(sky_median=v['sky_median'], gain=v['gain'])
    held = [e for v in report.values() if v['state'] == 'placed' for _, _, e in v['samples']]
    rms = float(np.sqrt(np.mean(np.square(held)))) if held else None
    worst = max((v['held_out_rms'] for v in report.values() if v['state'] == 'placed'), default=None)
    summary = dict(rms_px=rms, worst_px=worst, placed=len(placed), rejected=len(panels) - len(placed),
                   text=f'Match {rms:.2f} px · worst {worst:.2f} px' if rms is not None else 'Match —')
    doc = dict(schema='colour-place/1', canvas=os.path.abspath(args.canvas), canvas_size=[canvas.w, canvas.h],
               colour_canvas=os.path.abspath(args.output), colour_canvas_size=list(rgba.shape[1::-1]), k=k,
               scale_ratio=ratio, scale_source=source, summary=summary, photometry=phot, panels=report,
               seconds=round(time.time() - t0, 1))
    with open(args.report, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1, default=float)
    log(f'{summary["text"]} · {len(placed)} placed, {len(panels) - len(placed)} rejected · {doc["seconds"]:.0f} s')
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('panels', nargs='+', help='colour panel files or folders')
    ap.add_argument('--canvas', required=True, help='the luminance mosaic (16-bit TIFF)')
    ap.add_argument('-o', '--output', required=True, help='the colour canvas to write (16-bit RGBA TIFF)')
    ap.add_argument('--report', help='placement report (default: colour_place.json beside the output)')
    ap.add_argument('--scale-ratio', type=float, help='a colour pixel in canvas px (colour arcsec/px ÷ canvas arcsec/px); '
                                                     'estimated from the panels when not given')
    ap.add_argument('--initial', help='JSON {panel name: 3×3 panel px -> canvas px}: start from these poses '
                                      '(manual control points), skipping the global search')
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()
    if args.scale_ratio is not None and not args.scale_ratio > 0:
        ap.error('--scale-ratio must be positive')
    args.report = args.report or os.path.join(os.path.dirname(os.path.abspath(args.output)), 'colour_place.json')
    try:
        run(args, log=lambda s: print(s, flush=True))
    except (ValueError, OSError) as e:
        print(f'FAIL: {e}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
