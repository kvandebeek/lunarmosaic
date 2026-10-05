"""Measurements behind the statistics and readouts (plan §4.1): the lit disk and space, limb distance, textured crops,
the information cutoff and the wavelet layer it suggests, and sub-pixel channel shifts.

Named `measurements`, not `measure`: app.py imports a function called `measure` from alignment.py.
"""
import cv2
import numpy as np

import filters as F


def disk_and_space(proxy, q):
    """On the proxy grid: the lit disk (largest component above L 5 %, eroded for statistics), the hole-filled disk,
    the distance to its edge in canvas px, and the soft space region of D7."""
    Ls = F.rgb_to_lab(proxy)[..., 0] / 100.0
    raw = (Ls > 0.05).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(raw)
    if n > 1:
        raw = (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    if not raw.any():
        raise ValueError('No Moon found: nothing is brighter than the sky.')
    # distance to the limb, not to crater shadows inside the disk: holes that do not touch the frame edge are filled
    n2, lab2 = cv2.connectedComponents((1 - raw).astype(np.uint8), connectivity=4)
    edge = np.unique(np.concatenate([lab2[0], lab2[-1], lab2[:, 0], lab2[:, -1]]))
    filled = (~np.isin(lab2, edge[edge > 0])).astype(np.uint8) if n2 > 1 else raw
    limb = (cv2.distanceTransform(filled, cv2.DIST_L2, 5) * q).astype(np.float32)
    disk = cv2.erode(raw, np.ones((9, 9), np.uint8)) > 0
    # space: the part of L* < 1 connected to the frame edge, widened 8 canvas px (2 proxy px) and feathered
    dark = (Ls * 100 < 1).astype(np.uint8)
    n3, lab3 = cv2.connectedComponents(dark, connectivity=4)
    edge3 = np.unique(np.concatenate([lab3[0], lab3[-1], lab3[:, 0], lab3[:, -1]]))
    space = np.isin(lab3, edge3[edge3 > 0]) & (dark > 0)
    space = cv2.dilate(space.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    space = np.clip(cv2.GaussianBlur(space.astype(np.float32), (0, 0), 1.0) * 2, 0, 1).astype(np.float32)
    return disk, filled > 0, limb, space


def fine_detail(before, after):
    """lunar_finish's fine-detail amplitude: rms ratio of G(0.8) − G(2.0) on the channel mean."""
    e = lambda im: float(np.mean((cv2.GaussianBlur(im.mean(2), (0, 0), 0.8) - cv2.GaussianBlur(im.mean(2), (0, 0), 2.0)) ** 2))
    return (e(after) / max(e(before), 1e-12)) ** 0.5


def texture(gray):
    """Fine-band energy over brightness² (where correlation has something to hold on to)."""
    d = cv2.GaussianBlur(gray, (0, 0), 0.7) - cv2.GaussianBlur(gray, (0, 0), 2.0)
    return cv2.GaussianBlur(d * d, (0, 0), 4) / np.maximum(cv2.GaussianBlur(gray, (0, 0), 4) ** 2, 1e-4)


def textured_boxes(gray, n, size):
    """Up to n square boxes (y0, x0) of `size` px inside `gray`, ranked by texture and spread apart (each next box
    is the most textured one at least `size` px from those chosen). Boxes with almost no texture are not returned:
    below 5 % of the best box, or below 1e-6 at all (a flat image scores 6e-15 from rounding; the synthetic lunar
    disk's least textured tenth scores 2.5e-4)."""
    h, w = gray.shape
    if min(h, w) < size:
        return []
    t = texture(gray.astype(np.float32))
    score = cv2.boxFilter(t, -1, (size, size), normalize=True)
    half = size // 2
    valid = np.zeros_like(score, bool)
    valid[half:h - (size - half) + 1, half:w - (size - half) + 1] = True
    floor = max(0.05 * float(score[valid].max()), 1e-6) if valid.any() else np.inf
    boxes = []
    for _ in range(n):
        s = np.where(valid, score, -1)
        k = int(np.argmax(s))
        if s.flat[k] <= floor:
            break
        cy, cx = divmod(k, w)
        boxes.append((cy - half, cx - half))
        yy, xx = np.ogrid[:h, :w]
        valid &= (np.abs(yy - cy) >= size) | (np.abs(xx - cx) >= size)
    return boxes


def power_profile(gray, box=1024):
    """(frequencies in cycles/px, radially averaged power, noise floor) of the central box × box patch, Hann
    windowed; the floor is the median power over 0.42–0.5 cycles/px."""
    h, w = gray.shape
    box = min(box, h, w)
    y0, x0 = (h - box) // 2, (w - box) // 2
    a = gray[y0:y0 + box, x0:x0 + box].astype(np.float64)
    win = np.outer(np.hanning(box), np.hanning(box))
    P = np.fft.fftshift(np.abs(np.fft.fft2((a - a.mean()) * win)) ** 2)
    yy, xx = np.mgrid[0:box, 0:box] - box // 2
    r = np.hypot(xx, yy).astype(int)
    prof = np.bincount(r.ravel(), P.ravel()) / np.maximum(np.bincount(r.ravel()), 1)
    f = np.arange(len(prof)) / box
    return f, prof, float(np.median(prof[(f > 0.42) & (f <= 0.5)]))


def cutoff(gray, box=1024, profile=None):
    """DESIGN.md §7: the highest frequency (cycles/px) with real signal. Bright central patch, Hann window, radially
    averaged power; the noise floor is the median power over 0.42–0.5 cycles/px; the cutoff is the first frequency
    above 0.02 where power − floor ≤ 0.1 × floor. Recovers true cutoffs 0.15–0.45 within 0.001 on synthetic data."""
    f, prof, floor = profile or power_profile(gray, box)
    k = (f > 0.02) & (f <= 0.5)
    idx = np.nonzero(prof[k] - floor <= 0.1 * floor)[0]
    return float(f[k][idx[0]]) if len(idx) else 0.5


def suggest_layer(c):
    """The finest (lowest-numbered) à-trous layer whose whole band [1/(4s), 1/(2s)], s = 2^(n−1), lies below the
    cutoff: the smallest s with 1/(2s) ≤ c. 0.443 → layer 2; 0.173 → layer 3."""
    n = 1
    while 1 / (2 * 2 ** (n - 1)) > c:
        n += 1
    return n


# ---------------------------------------------------------------- sub-pixel channel shift (D16)

def _prep(c):
    h = c - cv2.GaussianBlur(c, (0, 0), 8)
    return (h / max(float(h.std()), 1e-9)).astype(np.float32)


def _shift(img, dx, dy):
    return cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), img.shape[1::-1], flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_REFLECT)


def phase_shift(ref, mov, tol=1e-3, max_iterations=20):
    """Phase correlation of high-passed, unit-rms channels with a Hann window, iterated until a step is below `tol`
    px: shift the channel back by the running estimate with Lanczos-4 and correlate again. Returns how far `mov`'s
    content sits from `ref`'s. Iterating to convergence matters: on noise-free texture one OpenCV step captures only
    about 43 % of the remaining shift (0.128 of 0.30 px), so three steps stop at 0.21–0.25; at zero shift the
    estimate is unbiased by symmetry, so the converged value is."""
    win = cv2.createHanningWindow(ref.shape[1::-1], cv2.CV_32F)
    a, total = _prep(ref), np.zeros(2)
    for _ in range(max_iterations):
        (sx, sy), _ = cv2.phaseCorrelate(a.copy(), _prep(_shift(mov, -total[0], -total[1])).copy(), win)
        total += (sx, sy)
        if abs(sx) < tol and abs(sy) < tol:
            break
    return total


def lucas_kanade(ref, mov, iterations=6):
    """Global Lucas–Kanade translation: Gauss–Newton on brightness constancy, pre-smoothed σ 1, Sobel gradients of
    the reference, over the crop inset by 32 px. Independent of phase correlation: spatial, not spectral."""
    G = _prep(cv2.GaussianBlur(ref, (0, 0), 1.0))
    gx = cv2.Sobel(G, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(G, cv2.CV_32F, 0, 1, ksize=3) / 8
    c = (slice(32, -32), slice(32, -32))
    A = np.array([[(gx[c] ** 2).sum(), (gx[c] * gy[c]).sum()], [(gx[c] * gy[c]).sum(), (gy[c] ** 2).sum()]], np.float64)
    if abs(np.linalg.det(A)) <= 1e-12 * max(float(np.trace(A)) ** 2, 1e-30):
        return np.full(2, np.nan)               # no gradient to hold on to: no estimate (it then never agrees)
    total = np.zeros(2)
    for _ in range(iterations):
        it = (_prep(cv2.GaussianBlur(_shift(mov, -total[0], -total[1]), (0, 0), 1.0)) - G)[c]
        total += -np.linalg.solve(A, [(gx[c] * it).sum(), (gy[c] * it).sum()])
    return total


def rgb_shift(rgb):
    """Both estimators for red and blue against green on one crop: {'r': (pc, lk), 'b': (pc, lk)}, each [dx, dy] —
    where the channel's content sits relative to green's, in px."""
    g = rgb[..., 1].astype(np.float32)
    return {name: (phase_shift(g, rgb[..., k].astype(np.float32)), lucas_kanade(g, rgb[..., k].astype(np.float32)))
            for name, k in (('r', 0), ('b', 2))}


def rgb_shift_decision(rgb):
    """D16 on an RGB image: both estimators on up to three textured crops, size min(512, side / 3); a channel is
    corrected only when, on each axis, they agree within 0.15 px on at least two crops, and the shift is at least
    0.1 px. Returns the crops, every estimate, and per channel {'apply': [dx, dy], 'why'} (apply moves the channel
    onto green)."""
    size = min(512, min(rgb.shape[:2]) // 3)          # three non-overlapping crops fit
    boxes = textured_boxes(rgb.mean(axis=2), 3, size)
    est = [rgb_shift(rgb[y:y + size, x0:x0 + size]) for y, x0 in boxes]
    out = {'crops': [[int(y), int(x0), size] for y, x0 in boxes],
           'estimates': {ch: [[list(map(float, e[ch][0])), list(map(float, e[ch][1]))] for e in est] for ch in 'rb'}}
    for ch in ('r', 'b'):
        if len(est) < 2:
            out[ch] = {'apply': [0.0, 0.0], 'why': 'fewer than two crops with enough texture'}
            continue
        mean = []
        for axis in (0, 1):
            agree = [(e[ch][0][axis] + e[ch][1][axis]) / 2 for e in est if abs(e[ch][0][axis] - e[ch][1][axis]) <= 0.15]
            mean.append(float(np.mean(agree)) if len(agree) >= 2 else None)
        if None in mean:
            out[ch] = {'apply': [0.0, 0.0], 'why': 'the two estimators disagree'}
        elif np.hypot(*mean) < 0.1:
            out[ch] = {'apply': [0.0, 0.0], 'why': f'only {np.hypot(*mean):.2f} px off: nothing to correct'}
        else:
            out[ch] = {'apply': [-mean[0], -mean[1]], 'why': f'{np.hypot(*mean):.2f} px corrected'}
    return out


def shift_channels(rgb, decision):
    """Red and blue moved by decision[ch]['apply'] (Lanczos-4, reflected border); green stays."""
    out = rgb.copy()
    for ch, k in (('r', 0), ('b', 2)):
        dx, dy = decision.get(ch, {}).get('apply', [0.0, 0.0])
        if dx or dy:
            out[..., k] = cv2.warpAffine(rgb[..., k], np.float32([[1, 0, dx], [0, 1, dy]]), rgb.shape[1::-1],
                                         flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    return out


# ---------------------------------------------------------------- resolution of a panel set (D14)

def bright_crop(gray, size=1024, inset=32):
    """(y0, x0, n): the brightest n × n crop of a panel, n = min(size, the panel inside `inset` px), where its signal
    stands furthest above the noise. Searched on an 8× reduced copy."""
    h, w = gray.shape
    n = min(size, h - 2 * inset, w - 2 * inset)
    if n < 64:
        return None
    k = 8
    small = cv2.resize(gray.astype(np.float32), (w // k, h // k), interpolation=cv2.INTER_AREA)
    m = max(1, n // k)
    score = cv2.boxFilter(small, -1, (m, m), normalize=True, borderType=cv2.BORDER_CONSTANT)
    half = m // 2
    lo = -(-inset // k) + half
    valid = np.zeros_like(score, bool)
    valid[lo:(h - inset - n) // k + half + 1, lo:(w - inset - n) // k + half + 1] = True
    if not valid.any():
        return None
    cy, cx = np.unravel_index(np.argmax(np.where(valid, score, -np.inf)), score.shape)
    y0 = int(np.clip((cy - half) * k, inset, h - inset - n))
    x0 = int(np.clip((cx - half) * k, inset, w - inset - n))
    return y0, x0, n


def panel_detail(gray, size=1024, inset=32):
    """The finest detail a panel holds, in its own px per cycle (1 / the information cutoff of §7), measured on its
    brightest interior crop; None when that crop has no texture to measure. Linear sharpening of the panel does
    not change it: it raises signal and noise alike at every frequency."""
    box = bright_crop(gray, size, inset)
    if box is None:
        return None
    y0, x0, n = box
    crop = gray[y0:y0 + n, x0:x0 + n].astype(np.float32)
    if float(texture(crop).mean()) < 1e-6:        # flat (the textured_boxes floor)
        return None
    f, prof, floor = profile = power_profile(crop, n)
    # the floor is noise only if the signal stands well above it lower down; terrain's power falls steeply with
    # frequency (400× from 0.04 to 0.45 cycles/px for f^-2.5), while a spectrum flat to the sampling limit — signal
    # reaching Nyquist, or no signal — leaves nothing to measure against (it would read as a cutoff near 0.02)
    if float(prof[(f > 0.02) & (f <= 0.06)].mean()) < 3 * floor:
        return None
    return 1.0 / cutoff(crop, n, profile)


def set_detail(grays, scale_rel, fallback_rel=None):
    """(detail, how) for one set: its finest detail in finest-set px = scale_rel × the median panel detail over the
    panels given (the builder passes up to three of its most textured). With no measurable panel (no texture, or
    signal flat to the sampling limit): `fallback_rel` (max of 2 px and the diffraction FWHM, when the optics are
    known), or 2 × scale_rel."""
    found = [d for d in (panel_detail(g) for g in grays) if d is not None]
    if found:
        return float(scale_rel * np.median(found)), f'measured on {len(found)} panel{"s" if len(found) > 1 else ""}'
    if fallback_rel:
        return float(fallback_rel), 'not measurable: from the pixel scale and the aperture'
    return float(2 * scale_rel), 'not measurable: 2 px of the set'


def tiers(details, tolerance=0.10):
    """Sets grouped by measured detail, best-resolving (smallest detail) first; a set joins the current tier while
    its detail is within `tolerance` of the tier's best (D14: sets within 10 % form a tier)."""
    out = []
    for s in sorted(details, key=lambda k: (details[k], k)):
        if out and details[s] <= details[out[-1][0]] * (1 + tolerance):
            out[-1].append(s)
        else:
            out.append([s])
    return out
