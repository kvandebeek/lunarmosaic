#!/usr/bin/env python3
"""Phase 2: finishing touches for a lunar mosaic, with an optional before/after movie.

0. Optional wavelet sharpening (--wavelets), for unsharpened stacks, as PixInsight's ATrousWaveletTransform on
   lightness: dyadic layers of 1, 2, 4, 8 px (linear-interpolation or B3-spline scaling function), layer k weighted
   by 1 + bias. --wavelets pixinsight = 4 layers, linear (3), layer 1 bias +3. Optional per-layer noise
   protection (coefficients within DENOISE × the layer's noise keep weight 1). Brightness only: no colour fringes.
1. Highlights: brightness is split into a smooth base and fine detail (edge-aware guided filter); a gentle
   shoulder lowers only the brightest part of the base, and the detail goes back unchanged. Bright rays and
   rims come down while keeping their texture.
2. Lunar colour: colour-only noise smoothing (3 px: the same idea as a sharp luminance layer coloured by a blurred
   colour layer in Photoshop), then more saturation along the Moon's natural hues (blue and orange-yellow). Greens, purples and pinks get only a small share of the boost (usually noise or fringing).
   Applied in passes (default 2): each pass multiplies saturation again.
3. Contrast: a gentle S-curve on brightness; black stays 0 and white stays 1.
4. Optional sharpening, reproducing Photoshop's Unsharp Mask: result = original + amount × (original − Gaussian(σ =
   radius)), only where |original − blurred| ≥ threshold (in 0–255 levels). Photoshop sharpens each RGB channel
   separately in an RGB image; the default here sharpens brightness only (like Fade → Luminosity), which avoids
   colour fringes. --usm-mode rgb gives Photoshop's per-channel behaviour.

5. North up (default; --keep-orientation to skip): moon_atlas locates the finished image, which is then turned
   so lunar north is up and east is right (never mirrored), in one Lanczos resample, and cropped to the Moon.

All adjustments work on brightness or colour separately (CIE Lab), so they do not shift each other.
The input is never modified. Measured on the 2026-09-25 mosaic: colourful pixels lie 46 % in blue
(240–300°), 30 % in orange–yellow (60–120°), 0.9 % in green, ~7 % in purple/pink.

    python3 lunar_finish.py mosaic.tif -o mosaic_finished.tif [--usm 100,1.5,0] [--record finish.mp4] [--record-vertical f.mp4]
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_link  # noqa: E402   (puts the LunarAtlas checkout on sys.path)
import tool_settings as ts  # noqa: E402
import mosaic_common as mc  # noqa: E402
import preview_style as ps  # noqa: E402
import mosaic_builder as mb  # noqa: E402   (Recorder, Preview, frame helpers)

LUNAR_BANDS = ((45, 115), (225, 295))      # orange–yellow and blue hue bands (Lab hue angle, degrees)
MOVIE = {"opening": 2.0, "card": 1.2, "wipe": 2.0, "hold": 0.8, "fade": 1.0, "final": 2.0, "turn": 2.6}


# ---------------------------------------------------------------- adjustments

def guided_self(I, r, eps):
    """Self-guided filter (He et al.): smoothing that keeps strong edges (limb, crater rims) sharp."""
    k = (2 * r + 1, 2 * r + 1)
    mean = lambda x: cv2.boxFilter(x, -1, k, normalize=True, borderType=cv2.BORDER_REFLECT)
    mI = mean(I)
    var = mean(I * I) - mI * mI
    a = var / (var + eps)
    b = mI - a * mI
    return mean(a), mean(b)


def shoulder(x, knee, top, h):
    """Soft highlight roll-off: identity below knee; the knee-to-top span is lowered by a fraction h at the top.
    Smooth (slope 1 at the knee), monotonic for h < 0.5, and continues with slope 1 - 2h above top."""
    span = max(top - knee, 1e-6)
    u = np.clip((x - knee) / span, 0, None)
    y = np.where(u <= 1, x - h * span * u * u, (top - h * span) + (1 - 2 * h) * (x - top))
    return np.where(x > knee, y, x)


def s_curve(x, m, c):
    """Very gentle S-curve pivoting at m: 0 -> 0, m -> m, 1 -> 1; midtone slope 1 + c."""
    lo = np.clip(x / max(m, 1e-6) - 1, -1, 0)
    hi = np.clip((x - m) / max(1 - m, 1e-6), 0, 1)
    y_lo = m * (1 + lo + c * lo * (1 - lo * lo))
    y_hi = m + (1 - m) * (hi + c * hi * (1 - hi * hi))
    return np.where(x <= m, y_lo, y_hi)


KERNELS = {"linear": np.array([1, 2, 1], np.float32) / 4,          # PixInsight "Linear Interpolation (3)"
           "b3": np.array([1, 4, 6, 4, 1], np.float32) / 16}     # PixInsight "B3 Spline (5)"
# the user's PixInsight setting for unsharpened stacks (ATrousWaveletTransform, dyadic, 4 layers, linear (3),
# layer 1 bias +3, target lightness, no noise reduction)
WAVELET_PRESETS = {"pixinsight": dict(biases=(3.0, 0.0, 0.0, 0.0), kernel="linear")}


def atrous(L, n, kernel="linear"):
    """À-trous wavelet layers (finest first, dyadic scales 1, 2, 4, … px) and the smooth residual;
    L = sum(layers) + residual."""
    k0 = KERNELS[kernel]
    layers, c = [], L.astype(np.float32)
    for k in range(n):
        s = 2 ** k
        ker = np.zeros((len(k0) - 1) * s + 1, np.float32)
        ker[::s] = k0
        c2 = cv2.sepFilter2D(c, -1, ker, ker, borderType=cv2.BORDER_REFLECT)
        layers.append(c - c2)
        c = c2
    return layers, c


def wavelet_margin(n):
    return 2 * (2 ** n - 1) + 2          # support of the coarsest layer (B3: 2 · s per level), in px


def wavelet_sharpen(L, biases, noise, denoise, kernel="linear"):
    """PixInsight-style: layer k is weighted by 1 + biases[k]. With denoise[k] > 0 the part of each coefficient
    within denoise[k] × noise[k] keeps weight 1, so the grain is not amplified."""
    layers, out = atrous(L, len(biases), kernel)
    for w, bias, sd, d in zip(layers, biases, noise, denoise):
        t = d * sd
        core = np.sign(w) * np.maximum(np.abs(w) - t, 0) if t > 0 else w
        out = out + w + bias * core
    return out


def layer_noise(L, n, kernel="linear", tile=32):
    """Noise level (robust σ) of each wavelet layer: the median over the 10 % flattest tiles of a crop."""
    layers, _ = atrous(L, n, kernel)
    sz = (max(1, L.shape[1] // tile), max(1, L.shape[0] // tile))
    act = cv2.resize(np.abs(layers[min(2, n - 1)]), sz, interpolation=cv2.INTER_AREA)
    lum = cv2.resize(L, sz, interpolation=cv2.INTER_AREA)
    on = lum > 0.05
    flat = on & (act <= np.percentile(act[on], 10)) if on.any() else np.ones_like(act, bool)
    return [float(np.median(cv2.resize(np.abs(w), sz, interpolation=cv2.INTER_AREA)[flat])) * 1.2533 for w in layers]


def lunar_weight(hue, floor=0.15, ramp=30.0):
    """1 on the lunar hue bands, falling (cosine) to `floor` over `ramp` degrees outside them."""
    w = np.zeros_like(hue)
    for lo, hi in LUNAR_BANDS:
        inside = ((hue - lo) % 360) <= ((hi - lo) % 360)
        d = np.minimum(np.abs(((hue - lo + 180) % 360) - 180), np.abs(((hue - hi + 180) % 360) - 180))
        wb = np.where(inside, 1.0, np.where(d < ramp, 0.5 * (1 + np.cos(np.pi * np.minimum(d, ramp) / ramp)), 0.0))
        w = np.maximum(w, wb)
    return floor + (1 - floor) * w


def process_region(bgr, y0, x0, ctx, p):
    """Apply all three adjustments to a region (float32 BGR 0–1) whose top-left is (y0, x0) in the full image.
    Returns {'orig', 'high', 'colour', 'final'} as float32 BGR."""
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    L0 = lab[..., 0] / 100.0
    L = wavelet_sharpen(L0, p.wavelets, ctx["wnoise"], p.wavelet_denoise, p.wavelet_kernel) if p.wavelets else L0
    q = ctx["q"]
    X, Y = np.meshgrid(np.arange(x0, x0 + w, dtype=np.float32), np.arange(y0, y0 + h, dtype=np.float32))
    mx, my = (X - (q - 1) / 2) / q, (Y - (q - 1) / 2) / q
    A = cv2.remap(ctx["A"], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    limb = cv2.remap(ctx["limb_dist"], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    B = cv2.remap(ctx["B"], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    base = A * L + B
    L1 = shoulder(base, ctx["knee"], ctx["top"], p.highlights) + (L - base)
    a, b = lab[..., 1], lab[..., 2]
    if p.chroma_denoise > 0:
        a = cv2.GaussianBlur(a, (0, 0), p.chroma_denoise)
        b = cv2.GaussianBlur(b, (0, 0), p.chroma_denoise)
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    # no extra saturation right at the limb: residual colour fringing lives there (measured: an orange rim)
    f = 1 + (p.saturation - 1) * lunar_weight(hue) * np.clip(limb / max(p.limb_taper, 1), 0, 1)
    L2 = s_curve(np.clip(L1, 0, 1), ctx["m"], p.contrast)
    sky = L0 < 0.01                                         # black space stays exactly black
    out = {"orig": bgr}
    # a saturation pass does not change hue, so pass k simply multiplies the chroma by f once more
    stages = ([("wave", L, lab[..., 1], lab[..., 2])] if p.wavelets else []) + [("high", L1, lab[..., 1], lab[..., 2])]
    for k in range(1, p.saturation_passes + 1):
        stages.append((f"colour{k}", L1, a * f ** k, b * f ** k))
    fk = f ** p.saturation_passes
    stages.append(("contrast", L2, a * fk, b * fk))
    L3 = L2
    if p.usm and p.usm_mode == "luminosity":
        amt, rad, thr = p.usm
        diff = L2 - cv2.GaussianBlur(L2.astype(np.float32), (0, 0), rad)
        L3 = L2 + amt * diff * (np.abs(diff) * 255 >= thr)
        stages.append(("sharp", L3, a * fk, b * fk))
    for name, Lx, ax, bx in stages:
        o = cv2.cvtColor(np.dstack([np.clip(Lx, 0, 1) * 100, ax, bx]).astype(np.float32), cv2.COLOR_Lab2BGR)
        o = np.clip(o, 0, 1)
        o[sky] = bgr[sky]
        out[name] = o
    if p.usm and p.usm_mode == "rgb":                       # Photoshop in RGB mode: every channel on its own
        amt, rad, thr = p.usm
        c = out["contrast"]
        diff = c - cv2.GaussianBlur(c, (0, 0), rad)
        o = np.clip(c + amt * diff * (np.abs(diff) * 255 >= thr), 0, 1)
        o[sky] = bgr[sky]
        out["sharp"] = o
    out["final"] = out["sharp"] if "sharp" in out else out["contrast"]
    return out


# ---------------------------------------------------------------- north up

def centred_crop(lo, hi, margin_frac=0.03, block=8):
    """A canvas that holds [lo, hi] (per axis) with an equal margin on every side, rounded up to a multiple
    of `block` px (cv2 likes even sizes). Returns (size, o): size is the canvas (w, h)-style tuple, and o is
    the offset such that a source point at position r lands at r - o on the canvas.

    Rounding size up to a multiple of `block` leaves 0 to block-1 spare px per axis beyond the Moon and its
    margin; that slack must be split evenly between both sides, by moving the window's start outward by half
    of it (o -= slack / 2). Moving it the other way (o += slack / 2) was tried once and is wrong: it shrinks
    the margin on one side by half the slack while growing the other by one and a half times it, which is
    what put more black space on one side of the Moon than the other."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = margin_frac * float((hi - lo).max())
    ideal = hi - lo + 2 * m
    size = tuple(int(-(-v // block) * block) for v in ideal)
    o = lo - m - (np.array(size) - ideal) / 2
    return size, o


def fold_in_disk(lo, hi, centre, radius):
    """Expand the lit-pixel crop box [lo, hi] to also hold the Moon's full circle (centre, radius), but only
    when the lit content already spans at least half the disk's diameter -- a genuine close-up, which covers
    much less than that, is left on its own tight crop rather than framed on a mostly-black whole Moon."""
    if np.min(hi - lo) >= radius:
        return np.minimum(lo, centre - radius), np.maximum(hi, centre + radius)
    return lo, hi


def north_up(small_final, F, input_path=None):
    """Where lunar north is in the finished image (moon_atlas on the reduced copy), and the turn that puts it up:
    dict(M, rot, mirror, north_was, identity, T full-resolution affine, size), or a reason string."""
    sys.path.insert(0, atlas_link.MOON_ATLAS)
    try:
        import atlas_geo
    except ImportError as e:
        return f"moon_atlas not available ({e})"
    grey = small_final.mean(axis=2).astype(np.float32)
    closeup = False
    try:
        geo, _ = atlas_geo.locate(grey, log=lambda *a: None)
    except SystemExit as e:
        # no limb (a close-up mosaic): search the input blind, lit by the Sun at its panels' capture time
        closeup = True
        from atlas_ephem import capture_time
        when = capture_time(input_path) if input_path else None
        if when is None:
            return f"the Moon could not be located ({e}) and there is no capture time for a close-up search"
        from atlas_closeup import locate_closeup
        print("      no limb in view: searching the image as a close-up (about 1–2 min)", flush=True)
        try:
            geo, _ = locate_closeup(input_path, log=lambda *a: None, when=when)
        except SystemExit as e2:
            return f"the Moon could not be located ({e2})"
    M, rot, mirror = atlas_geo.north_up_matrix(geo)
    hs, ws = grey.shape
    H, W = hs * F, ws * F                      # the reduced copy covers whole F × F blocks of the image
    c = np.array([(W - 1) / 2, (H - 1) / 2])
    ys, xs = np.nonzero(grey > 0.004)
    step = max(1, len(xs) // 200000)
    p = (np.stack([xs[::step], ys[::step]], 1) + 0.5) * F - 0.5
    r = (p - c) @ M.T
    lo, hi = r.min(0) - F, r.max(0) + F

    # geo.t and geo.radius_px are the Moon's true centre and radius -- known even for a crescent, since
    # locate()/locate_closeup() fit against the actual lunar terrain, not against brightness. The lit-pixel
    # bbox above has no such guarantee: the unlit limb casts no pixels to threshold on, so for anything less
    # than a full Moon it is centred on the sunlit shape, not on the disk (2026-10-11: a 14-panel near-full
    # mosaic came out with 221 px of black above the crescent and 48 px below it -- same code, no rounding
    # bug, just a crescent that isn't lit symmetrically). fold_in_disk() fixes that by folding the known
    # disk circle into the crop -- skipped for a genuine close-up (a crater crop covering a small, usually
    # off-centre, part of the disk), where framing *that* on the whole Moon would be mostly black.
    centre = (geo.t + 0.5) * (1.0 if closeup else F) - 0.5    # locate_closeup works on the full-res input;
    radius = geo.radius_px * (1.0 if closeup else F)          # atlas_geo.locate works on the reduced copy
    lo, hi = fold_in_disk(lo, hi, (centre - c) @ M.T, radius)

    size, o = centred_crop(lo, hi)
    T = np.hstack([M, (-M @ c - o)[:, None]])
    return dict(M=M, rot=rot, mirror=mirror, north_was=geo.north_angle, T=T, size=size, c=c, o=o, F=F,
                pivot_s=np.array([xs.mean(), ys.mean()]), identity=abs(rot) < 0.05 and not mirror)


def apply_turn(img, turn, F=None):
    """The turn on the full-resolution image (Lanczos), or on a copy reduced F × (linear)."""
    M = turn["M"]
    if F is None:
        return cv2.warpAffine(img, turn["T"], turn["size"], flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
    t = (M @ (0.5 * F - 0.5 - turn["c"]) - turn["o"] + 0.5) / F - 0.5
    size = tuple(int(np.ceil(v / F)) for v in turn["size"])
    return cv2.warpAffine(img, np.hstack([M, t[:, None]]), size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def detail_kept(img16, M, R=512):
    """Share of the fine-detail amplitude one Lanczos turn keeps: a centre crop turned there and back (two
    resamples), square-rooted."""
    H, W = img16.shape[:2]
    cy, cx = H // 2, W // 2
    L = img16[cy - R:cy + R, cx - R:cx + R].astype(np.float32).mean(axis=2)
    c = np.array([R - 0.5, R - 0.5])
    fwd = np.hstack([M, (c - M @ c)[:, None]])
    Mi = np.linalg.inv(M)
    back = np.hstack([Mi, (c - Mi @ c)[:, None]])
    t = cv2.warpAffine(cv2.warpAffine(L, fwd, (2 * R, 2 * R), flags=cv2.INTER_LANCZOS4), back, (2 * R, 2 * R), flags=cv2.INTER_LANCZOS4)
    band = lambda im: cv2.GaussianBlur(im, (0, 0), 0.8) - cv2.GaussianBlur(im, (0, 0), 2.0)
    k = slice(R // 2, 3 * R // 2)
    return float(np.sqrt(np.sqrt(np.mean(band(t)[k, k] ** 2) / max(np.mean(band(L)[k, k] ** 2), 1e-12))))


# ---------------------------------------------------------------- main work

def run(args, pv, stg):
    t0 = time.time()
    pv.set_all("log_line", "")
    print(f"lunar_finish  {args.input}", flush=True)
    img16 = cv2.imread(args.input, cv2.IMREAD_UNCHANGED)
    if img16 is None or img16.ndim != 3 or img16.shape[2] < 3:
        sys.exit("need a 3-channel (RGB) image")
    if img16.dtype not in (np.uint8, np.uint16):
        sys.exit(f"need an 8- or 16-bit image, not {img16.dtype}")
    img16 = img16[..., :3]
    scale = 65535.0 if img16.dtype == np.uint16 else 255.0
    H, W = img16.shape[:2]
    q = 4
    # whole q × q blocks only, so the small copy maps exactly as the remap in process_region assumes
    if min(H, W) < 4 * q:
        sys.exit(f"image too small ({W} × {H} px)")
    src = mc.proxy_crop(img16, q)
    if src.dtype == np.uint8:                   # 8-bit: average in float, not in 1/255 steps
        src = src.astype(np.float32)
    small = cv2.resize(src, (W // q, H // q), interpolation=cv2.INTER_AREA).astype(np.float32) / scale
    Ls = cv2.cvtColor(small, cv2.COLOR_BGR2Lab)[..., 0] / 100.0
    raw = (Ls > 0.05).astype(np.uint8)
    n_, lab_, st_, _ = cv2.connectedComponentsWithStats(raw)
    if n_ > 1:
        raw = (lab_ == 1 + int(np.argmax(st_[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    if not raw.any():
        sys.exit("no Moon found (nothing brighter than the sky)")
    # distance to the limb, not to crater shadows inside the disk: holes that do not touch the frame edge are filled
    n2, lab2 = cv2.connectedComponents((1 - raw).astype(np.uint8), connectivity=4)
    edge = np.unique(np.concatenate([lab2[0], lab2[-1], lab2[:, 0], lab2[:, -1]]))
    filled = (~np.isin(lab2, edge[edge > 0])).astype(np.uint8) if n2 > 1 else raw
    limb_dist = cv2.distanceTransform(filled, cv2.DIST_L2, 5) * q         # full-resolution px inside the limb
    disk = cv2.erode(raw, np.ones((9, 9), np.uint8)) > 0
    A, B = guided_self(Ls, r=max(4, 64 // q), eps=0.03 ** 2)
    base_s = A * Ls + B
    ctx = {"q": q, "A": A.astype(np.float32), "B": B.astype(np.float32), "limb_dist": limb_dist.astype(np.float32),
           "knee": float(np.percentile(base_s[disk], args.knee_percentile)),
           "top": float(np.percentile(base_s[disk], 99.99)), "m": float(np.median(Ls[disk])), "wnoise": []}
    if args.wavelets:
        # layer noise from a full-resolution crop at the disk's centre of mass (the grain is at full resolution)
        ys_, xs_ = np.nonzero(disk)
        cy_, cx_, R_ = int(ys_.mean() * q), int(xs_.mean() * q), min(1024, H // 2, W // 2)
        y0_, x0_ = int(np.clip(cy_ - R_, 0, H - 2 * R_)), int(np.clip(cx_ - R_, 0, W - 2 * R_))
        crop_ = img16[y0_:y0_ + 2 * R_, x0_:x0_ + 2 * R_].astype(np.float32) / scale
        ctx["wnoise"] = layer_noise(cv2.cvtColor(crop_, cv2.COLOR_BGR2Lab)[..., 0] / 100.0, len(args.wavelets), args.wavelet_kernel)
    print(f"      disk {disk.sum() * q * q / 1e6:.0f} Mpx · brightness median L {ctx['m'] * 100:.1f} · highlight knee L "
          f"{ctx['knee'] * 100:.1f} (p{args.knee_percentile:g}) · top L {ctx['top'] * 100:.1f}", flush=True)

    # full resolution, in strips (margin for the colour-noise blur); keep small copies of every stage for the movie
    F = max(1, int(np.ceil(max(H, W) / 1100)))
    rows = F * max(1, 1024 // F)
    margin = int(np.ceil(4 * max(args.chroma_denoise, 0.5, args.usm[1] if args.usm else 0))) + 2 + \
        (wavelet_margin(len(args.wavelets)) if args.wavelets else 0)
    out16 = np.empty(img16.shape, np.uint16)                # always 16-bit, also from an 8-bit input
    keys = ["orig"] + (["wave"] if args.wavelets else []) + ["high"] + [f"colour{k}" for k in range(1, args.saturation_passes + 1)] + ["contrast"] + \
        (["sharp"] if args.usm else []) + ["final"]
    prev = {k: [] for k in keys}
    for y in range(0, H, rows):
        pv.checkpoint()
        ya, yb = max(0, y - margin), min(H, y + rows + margin)
        st = process_region(img16[ya:yb].astype(np.float32) / scale, ya, 0, ctx, args)
        core = slice(y - ya, y - ya + min(rows, H - y))
        out16[y:y + min(rows, H - y)] = (st["final"][core] * 65535 + 0.5).astype(np.uint16)
        for k in prev:
            c = st[k][core]
            prev[k].append(cv2.resize(c, (W // F, max(1, c.shape[0] // F)), interpolation=cv2.INTER_AREA))
        print(f"      {min(H, y + rows)}/{H} rows", end="\r", flush=True)
    print()
    small_st = {k: np.vstack(v) for k, v in prev.items()}
    pv.checkpoint()
    wave_gain = None
    if args.wavelets:           # fine-detail amplitude, full-resolution centre crop: a direct measure of the sharpening
        cy, cx, R = H // 2, W // 2, min(600, H // 2, W // 2)
        cc = process_region(img16[cy - R:cy + R, cx - R:cx + R].astype(np.float32) / scale, cy - R, cx - R, ctx, args)
        e = lambda im: float(np.mean((cv2.GaussianBlur(im.mean(2), (0, 0), 0.8) - cv2.GaussianBlur(im.mean(2), (0, 0), 2.0)) ** 2))
        wave_gain = (e(cc["wave"]) / max(e(cc["orig"]), 1e-12)) ** 0.5
    turn = None
    if args.north_up:
        turn = north_up(small_st["final"], F, args.input)
        if isinstance(turn, str):
            print(f"      orientation kept as captured: {turn}", flush=True)
            turn = None
        elif turn["identity"]:
            print("      orientation: lunar north is already up", flush=True)
            turn = None
        else:
            t_ = time.time()
            kept = detail_kept(img16, turn["M"])
            out16 = apply_turn(out16, turn)
            H, W = out16.shape[:2]
            small_st["turned"] = apply_turn(small_st["final"], turn, F)
            print(f"      north up: lunar north was {turn['north_was']:.1f}° from up" + (", mirrored" if turn["mirror"] else "") +
                  f" · turned {turn['rot']:+.1f}° (one Lanczos resample, fine detail kept {kept * 100:.1f} %) · "
                  f"cropped to the Moon: {W} × {H} px · {time.time() - t_:.0f} s", flush=True)
    ok = cv2.imwrite(stg.path(args.output), out16, [cv2.IMWRITE_TIFF_COMPRESSION, cv2.IMWRITE_TIFF_COMPRESSION_NONE])
    if not ok:
        sys.exit(f"could not write {args.output}")
    # the movie shows every stage with one tone mapping (white point from the original mosaic, gamma 1.1);
    # the _display TIFF is the finished result with exactly that mapping
    wp_disp = float(np.percentile(small_st["orig"][small_st["orig"].max(axis=2) > 0.02], 99.99))
    info_turn = turn
    disp_note = ""
    if args.display_tif:
        disp_path = os.path.splitext(os.path.abspath(args.output))[0] + "_display.tif"
        share = mc.write_display_tiff(stg.path(disp_path), out16, wp_disp, 1.1, in_scale=65535.0)
        disp_note = (f"\n      {os.path.basename(disp_path)}  as in the movie: white point {wp_disp * 65535:.0f} ADU, gamma 1.1 · "
                     f"{share * 100:.2f} % of the Moon at full white")

    # what changed (measured on the reduced copies, disk only)
    def lab_stats(bgr):
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
        Ld = cv2.erode((lab[..., 0] > 5).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        C = np.hypot(lab[..., 1], lab[..., 2])
        hue = (np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) + 360) % 360
        onaxis = lunar_weight(hue) > 0.9
        return {"L": lab[..., 0][Ld], "C_axis": float(np.median(C[Ld & onaxis])), "C_off": float(np.median(C[Ld & ~onaxis])),
                "p999": float(np.percentile(lab[..., 0][Ld], 99.9)), "std": float(lab[..., 0][Ld].std())}
    S = {k: lab_stats(small_st[k]) for k in keys}
    last_col = f"colour{args.saturation_passes}"
    affected = float((base_s[disk] > ctx["knee"]).mean() * 100)
    info = {"affected": affected, "p999": (S["orig"]["p999"], S["high"]["p999"]),
            "C_pass": [S["high"]["C_axis"]] + [S[f"colour{k}"]["C_axis"] for k in range(1, args.saturation_passes + 1)],
            "C_off": (S["high"]["C_off"], S[last_col]["C_off"]), "std": (S[last_col]["std"], S["contrast"]["std"])}
    if args.wavelets:
        print(f"      wavelets ({args.wavelet_kernel}, {len(args.wavelets)} layers, biases " + ", ".join(f"{v:+g}" for v in args.wavelets) +
              (", noise protection " + ", ".join(f"{v:g}σ" for v in args.wavelet_denoise) if any(args.wavelet_denoise) else "") +
              f"): fine-detail amplitude × {wave_gain:.2f} (centre crop) · layer noise " + ", ".join(f"{v * 100:.2f}" for v in ctx["wnoise"]) + " L units")
    print(f"      highlights: brightest 0.1 % L {S['orig']['p999']:.1f} → {S['high']['p999']:.1f} "
          f"({affected:.1f} % of the disk above the knee; detail kept)")
    print(f"      colour ({args.saturation_passes} passes × {args.saturation:g}): chroma on lunar hues " +
          " → ".join(f"{c:.2f}" for c in info["C_pass"]) + f" · elsewhere {info['C_off'][0]:.2f} → {info['C_off'][1]:.2f} (Lab units)")
    print(f"      contrast: brightness spread (std) L {info['std'][0]:.2f} → {info['std'][1]:.2f}")
    if args.usm:
        # fine-detail energy on the full-resolution centre crop: a direct measure of the sharpening
        cy, cx = img16.shape[0] // 2, img16.shape[1] // 2
        R = min(600, cy, cx)
        cc = process_region(img16[cy - R:cy + R, cx - R:cx + R].astype(np.float32) / scale, cy - R, cx - R, ctx, args)
        e = lambda im: float(np.mean((cv2.GaussianBlur(im.mean(2), (0, 0), 0.8) - cv2.GaussianBlur(im.mean(2), (0, 0), 2.0)) ** 2))
        info["usm_gain"] = (e(cc["sharp"]) / max(e(cc["contrast"]), 1e-12)) ** 0.5
        print(f"      sharpening (unsharp mask {args.usm[0] * 100:.0f} %, {args.usm[1]:g} px, {args.usm[2]:g} levels, {args.usm_mode}): "
              f"fine-detail amplitude × {info['usm_gain']:.2f} (centre crop, full resolution)")
    print(f"      {os.path.basename(args.output)}  {W} × {H} px · 16-bit · uncompressed · {os.path.getsize(stg.path(args.output)) / 2**30:.2f} GiB · "
          f"{time.time() - t0:.0f} s" + disp_note, flush=True)
    info["wp_disp"] = wp_disp
    info["turn"] = info_turn
    if args.wavelets:
        info["wave_gain"] = wave_gain

    if pv.active():
        info["out_size"] = (W, H)
        movie(args, pv, small_st, ctx, info, img16, scale, img16.shape[1], img16.shape[0])
    pv.checkpoint()
    return 0


def movie(args, pv, small_st, ctx, info, img16, scale, W, H):
    """Opening -> steps (card, before/after wipe, hold) -> the finished image. 21 s (25.6 s with sharpening)."""
    FPS = mb.FPS
    f = lambda s_: int(round(s_ * FPS))
    # the highlight effect is best seen close up: the brightest area (e.g. Tycho's rays), at higher resolution
    Ls = cv2.resize(small_st["orig"], None, fx=1, fy=1).mean(axis=2)
    Fx = W / Ls.shape[1]
    hot = cv2.GaussianBlur((Ls > np.percentile(Ls[Ls > 0.05], 99.5)).astype(np.float32), (0, 0), 12)
    cy, cx = np.unravel_index(np.argmax(hot), hot.shape)
    S = min(1600, H, W)                                  # square, and inside the image also for small inputs
    X0 = int(np.clip(cx * Fx - S // 2, 0, W - S))
    Y0 = int(np.clip(cy * Fx - S // 2, 0, H - S))
    crop = process_region(img16[Y0:Y0 + S, X0:X0 + S].astype(np.float32) / scale, Y0, X0, ctx, args)
    crop = {k: cv2.resize(v, (1000, 1000), interpolation=cv2.INTER_AREA) for k, v in crop.items()}
    wp = info["wp_disp"]
    tone = lambda im: (np.clip(im / wp, 0, 1) ** (1 / 1.1) * 255).astype(np.uint8)       # same mapping as the _display TIFF
    date = pv.style.session
    for st in pv.styles():
        st.name = "LUNAR MOSAIC · FINISHING"
    steps = []
    if args.wavelets:
        # wavelet sharpening only shows at 100 %: a small crop, shown enlarged
        cs = min(520, S)
        Yc, Xc = Y0 + (S - cs) // 2, X0 + (S - cs) // 2
        cw = process_region(img16[Yc:Yc + cs, Xc:Xc + cs].astype(np.float32) / scale, Yc, Xc, ctx, args)
        cw = {k: cv2.resize(v, (1000, 1000), interpolation=cv2.INTER_CUBIC) for k, v in cw.items()}
        steps.append(("Wavelet sharpening", f"à trous, {args.wavelet_kernel} · close up, enlarged · lightness only",
                      tone(cw["orig"]), tone(cw["wave"]),
                      [("layers", f"{len(args.wavelets)} (1–{2 ** (len(args.wavelets) - 1)} px)"),
                       ("biases", " ".join(f"{v:+g}" for v in args.wavelets)),
                       ("noise protection", ", ".join(f"{v:g}σ" for v in args.wavelet_denoise) if any(args.wavelet_denoise) else "off"),
                       ("fine detail", f"× {info.get('wave_gain') or 1:.2f}")]))
    steps += [
        ("Recovering highlights", "the brightest area, close up · highlights lowered, detail kept",
         tone(crop["orig"]), tone(crop["high"]),
         [("knee", f"L {ctx['knee'] * 100:.0f}"), ("brightest 0.1 %", f"L {info['p999'][0]:.1f} → {info['p999'][1]:.1f}"),
          ("affected", f"{info['affected']:.1f} % of the disk"), ("strength", f"{args.highlights:.2f}")])]
    prev_key = "high"
    for k in range(1, args.saturation_passes + 1):
        c0, c1 = info["C_pass"][k - 1], info["C_pass"][k]
        steps.append((f"Lunar colour · pass {k} of {args.saturation_passes}", "more saturation on the Moon's blue and orange hues only",
                      tone(small_st[prev_key]), tone(small_st[f"colour{k}"]),
                      [("saturation", f"× {args.saturation:g} on lunar hues"), ("greens · purples", f"× {1 + (args.saturation - 1) * 0.15:.2f}"),
                       ("chroma", f"{c0:.2f} → {c1:.2f}"), ("limb", f"protected {args.limb_taper:g} px")]))
        prev_key = f"colour{k}"
    steps.append(("A touch of contrast", "a gentle S-curve on brightness",
                  tone(small_st[prev_key]), tone(small_st["contrast"]),
                  [("contrast", f"+{args.contrast * 100:.0f} % at midtones"), ("pivot", f"L {ctx['m'] * 100:.0f}"),
                   ("black · white", "kept"), ("spread", f"{info['std'][0]:.2f} → {info['std'][1]:.2f}")]))
    if args.usm:
        # sharpening only shows at 100 %: a smaller crop, shown enlarged
        cs = min(520, S)
        Yc, Xc = Y0 + (S - cs) // 2, X0 + (S - cs) // 2
        cz = process_region(img16[Yc:Yc + cs, Xc:Xc + cs].astype(np.float32) / scale, Yc, Xc, ctx, args)
        cz = {k: cv2.resize(v, (1000, 1000), interpolation=cv2.INTER_CUBIC) for k, v in cz.items()}
        steps.append(("Sharpening", f"unsharp mask as in Photoshop · close up, enlarged · {args.usm_mode}",
                      tone(cz["contrast"]), tone(cz["sharp"]),
                      [("amount", f"{args.usm[0] * 100:.0f} %"), ("radius", f"{args.usm[1]:g} px"), ("threshold", f"{args.usm[2]:g} levels"),
                       ("fine detail", f"× {info['usm_gain']:.2f}")]))
    turn = info.get("turn")
    n_steps = len(steps) + (1 if turn else 0)
    for st in pv.styles():
        st.n_steps = n_steps
    pv.show(lambda st: st.card(None, "Lunar Mosaic", f"finishing touches · {date}" if date else "finishing touches"),
            record=MOVIE["opening"] if pv.outputs else None)
    last = None
    for k, (title, detail, before, after, ro) in enumerate(steps):
        pv.checkpoint()
        pv.show(lambda st, k=k, title=title: st.card(k, title, detail), record=MOVIE["card"] if pv.outputs else None)
        n = f(MOVIE["wipe"])
        for i in range(1, n + 1):
            pv.checkpoint()
            t = i / n
            t = t * t * (3 - 2 * t)
            pv.show(lambda st, t=t, before=before, after=after, title=title, detail=detail, ro=ro, k=k:
                    st.compare(before, after, t, k, title, detail, (k + t) / n_steps, ro),
                    record=mb.hold(1) if pv.outputs else None, min_interval=0.03)
        hold_frame = {}

        def render_after(st, after=after, title=title, detail=detail, ro=ro, k=k):
            if id(st) not in hold_frame:
                hold_frame[id(st)] = st.layout(after, [], k, title, detail, (k + 1) / n_steps, ro)
            return hold_frame[id(st)]
        pv.show(render_after, record=MOVIE["hold"] if pv.outputs else None)
        last = render_after
    if turn:
        # last step: the finished disk turns (and flips, if mirrored) to lunar north up, east right
        k = n_steps - 1
        title = "North up"
        detail = "lunar north up, east right, as in lunar atlases"
        ro = [("north was", f"{turn['north_was']:.1f}° from up"), ("turn", f"{turn['rot']:+.1f}°"),
              ("mirrored", "yes, flipped" if turn["mirror"] else "no"), ("resample", "one, Lanczos")]
        before8 = tone(small_st["final"])
        after8 = tone(small_st["turned"])
        hs2, ws2 = after8.shape[:2]
        M = turn["M"]
        Fs = turn["F"]
        c_s = turn["pivot_s"]                              # the Moon turns about its own centre, staying in view
        t_s = (M @ (0.5 * Fs - 0.5 - turn["c"]) - turn["o"] + 0.5) / Fs - 0.5
        d = M @ c_s + t_s                                  # where the Moon's centre ends up
        pv.checkpoint()
        pv.show(lambda st: st.card(k, title, detail), record=MOVIE["card"] if pv.outputs else None)
        n = f(MOVIE["turn"])
        for i in range(0, n + 1):
            pv.checkpoint()
            t = i / n
            t = t * t * (3 - 2 * t)
            a = np.radians(turn["rot"] * t)
            Mt = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
            if turn["mirror"]:
                Mt = np.diag([1 - 2 * t, 1.0]) @ Mt              # a flip through edge-on
            T = np.hstack([Mt, (d - Mt @ c_s)[:, None]])
            frame = after8 if i == n else cv2.warpAffine(before8, T, (ws2, hs2), flags=cv2.INTER_LINEAR)
            pv.show(lambda st, frame=frame, t=t: st.layout(frame, [], k, title, detail, (k + t) / n_steps, ro),
                    record=mb.hold(1) if pv.outputs else None, min_interval=0.03)
        hold_turn = {}

        def render_turned(st):
            if id(st) not in hold_turn:
                hold_turn[id(st)] = st.layout(after8, [], k, title, detail, 1.0, ro)
            return hold_turn[id(st)]
        pv.show(render_turned, record=MOVIE["hold"] if pv.outputs else None)
        last = render_turned
    fin = tone(small_st["turned"] if turn else small_st["final"])
    ow, oh = info.get("out_size", (W, H))
    facts = [("size", f"{ow} × {oh} px"), ("highlights", f"{args.highlights:.2f}"),
             ("saturation", f"× {args.saturation:g}, {args.saturation_passes} passes"), ("contrast", f"+{args.contrast * 100:.0f} %")]
    if args.wavelets:
        facts[0] = ("size · wavelets", f"{ow} × {oh} px · layer biases " + " ".join(f"{v:+g}" for v in args.wavelets))
    if args.usm:
        facts[-1] = ("contrast · sharpening", f"+{args.contrast * 100:.0f} % · USM {args.usm[0] * 100:.0f} % {args.usm[1]:g} px")
    if args.usm and not turn:
        # the sharpening step ends on a close-up: show the full disk once more so the fade to the final shot is seamless
        full_after = {}

        def last_full(st):
            if id(st) not in full_after:
                full_after[id(st)] = st.layout(fin, [], n_steps - 1, steps[-1][0], "the whole disk", 1.0, steps[-1][4])
            return full_after[id(st)]
        pv.show(last_full, record=0.6 if pv.outputs else None)
        last = last_full
    final_frame = {}

    def render_final(st):
        if id(st) not in final_frame:
            final_frame[id(st)] = st.final(fin, date or "finished", ("wavelets · " if args.wavelets else "") +
                                           "highlights · lunar colour · contrast" + (" · sharpening" if args.usm else "") +
                                           (" · north up" if turn else ""), facts)
        return final_frame[id(st)]
    pv.checkpoint()
    nf = f(MOVIE["fade"])
    # the finishing steps' last frame shows the full disk in the same place as the final shot: crossfade
    for i in range(1, nf + 1):
        t = i / (nf + 1)
        t = t * t * (3 - 2 * t)
        pv.show(lambda st, t=t: cv2.addWeighted(last(st), 1 - t, render_final(st), t, 0),
                record=mb.hold(1) if pv.outputs else None, window=False)
    pv.show(render_final, record=MOVIE["final"] if pv.outputs else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=mc.HelpFormatter)
    ap.add_argument("input", help="16-bit RGB TIFF (e.g. mosaic.tif from mosaic_builder.py)")
    ap.add_argument("-o", "--output", required=True, help="output TIFF (16-bit, uncompressed); the input is never changed")
    ap.add_argument("--highlights", type=float, default=ts.get("LUNAR_FINISH_HIGHLIGHTS", 0.3, float), help="0 = off; fraction by which the knee-to-top span is lowered (< 0.5)")
    ap.add_argument("--knee-percentile", type=float, default=ts.get("LUNAR_FINISH_KNEE_PERCENTILE"), help="highlight roll-off starts at this brightness percentile of the disk")
    ap.add_argument("--saturation", type=float, default=ts.get("LUNAR_FINISH_SATURATION", 2.0, float), help="saturation factor on the lunar hues (1 = off)")
    ap.add_argument("--saturation-passes", type=int, default=ts.get("LUNAR_FINISH_SATURATION_PASSES", 2, int), help="how many times the saturation boost is applied")
    ap.add_argument("--contrast", type=float, default=ts.get("LUNAR_FINISH_CONTRAST", 0.12, float), help="midtone contrast increase (0.12 = +12 %%), 0 to < 0.5")
    ap.add_argument("--usm", default=ts.get("LUNAR_FINISH_USM") or None, metavar="AMOUNT,RADIUS,THRESHOLD",
                    help="sharpen like Photoshop's Unsharp Mask, e.g. 100,1.5,0 or \"100;1,5;0\" (amount %%, radius px = Gaussian σ, "
                         "threshold in 0–255 levels); off by default")
    ap.add_argument("--usm-mode", choices=("luminosity", "rgb"), default=ts.get("LUNAR_FINISH_USM_MODE"),
                    help="luminosity = brightness only (no colour fringes); rgb = every channel, as Photoshop does in RGB mode")
    ap.add_argument("--wavelets", default=ts.get("LUNAR_FINISH_WAVELETS") or None, metavar="BIASES|pixinsight",
                    help="wavelet sharpening first, as PixInsight's ATrousWaveletTransform on lightness: comma-separated layer "
                         "biases (layer weight 1 + bias), e.g. 3,0,0,0; 'pixinsight' = 4 layers, linear (3), layer 1 +3; off by default")
    ap.add_argument("--wavelet-kernel", choices=("linear", "b3"), default=ts.get("LUNAR_FINISH_WAVELET_KERNEL", "linear"),
                    help="scaling function: linear = PixInsight 'Linear Interpolation (3)', b3 = 'B3 Spline (5)'")
    ap.add_argument("--wavelet-denoise", default=ts.get("LUNAR_FINISH_WAVELET_DENOISE") or None, metavar="K1,K2,…",
                    help="per layer: coefficients within K × the layer's noise level keep weight 1 (0 = off, the default)")
    ap.add_argument("--keep-orientation", dest="north_up", action="store_false",
                    help="keep the orientation as captured (default: last step turns the image to lunar north up, east right)")
    ap.add_argument("--north-up", dest="north_up", action="store_true", help="turn north up even if .env says LUNAR_FINISH_NORTH_UP=0")
    ap.add_argument("--no-wavelets", action="store_true", help="skip the wavelet step even if .env sets LUNAR_FINISH_WAVELETS")
    ap.add_argument("--chroma-denoise", type=float, default=ts.get("LUNAR_FINISH_CHROMA_DENOISE"),
                    help="px (Gaussian σ): colour-only smoothing before the saturation boost; brightness keeps all detail. "
                         "Like a luminance layer coloured by a blurred colour layer in Photoshop (Color blend mode)")
    ap.add_argument("--limb-taper", type=float, default=ts.get("LUNAR_FINISH_LIMB_TAPER"), help="px: no extra saturation at the limb, full boost this far inside")
    ap.add_argument("--date", default=None, help="caption for the movie (default: taken from the input's folder)")
    ap.add_argument("--preview", action=argparse.BooleanOptionalAction, default=ts.get("LUNAR_FINISH_PREVIEW"), help="live preview window")
    ap.add_argument("--display-tif", action=argparse.BooleanOptionalAction, default=ts.get("LUNAR_FINISH_DISPLAY_TIF"),
                    help="also write <out>_display.tif (the result with the movie's tone mapping)")
    ap.add_argument("--record", metavar="MOVIE.mp4", help="record the before/after movie, 16:9 (1920×1080)")
    ap.add_argument("--record-vertical", metavar="MOVIE.mp4", help="the same movie in 9:16 (1080×1920), e.g. Instagram Reels")
    ap.set_defaults(north_up=ts.get("LUNAR_FINISH_NORTH_UP"))
    args = ap.parse_args()
    if args.no_wavelets:
        args.wavelets = None
    if args.usm:
        # "100,1.5,0", or with a decimal comma "100;1,5;0" (as Photoshop shows it in a Dutch/Belgian locale)
        raw = args.usm.strip()
        parts = [v.replace(",", ".") for v in raw.split(";")] if ";" in raw else raw.replace(" ", ",").split(",")
        try:
            amt, rad, thr = (float(v) for v in parts if v != "")
        except ValueError:
            sys.exit("--usm expects AMOUNT,RADIUS,THRESHOLD, e.g. 100,1.5,0 or \"100;1,5;0\"")
        if not (amt >= 0 and rad > 0 and thr >= 0):
            sys.exit("--usm: amount and threshold must be ≥ 0 and the radius > 0")
        args.usm = (amt / 100.0, rad, thr)
    if args.wavelets:
        preset = WAVELET_PRESETS.get(args.wavelets.strip().lower())
        if preset:
            args.wavelets, args.wavelet_kernel = list(preset["biases"]), preset["kernel"]
        else:
            try:
                args.wavelets = [float(v) for v in args.wavelets.replace(";", ",").split(",") if v.strip()]
            except ValueError:
                sys.exit("--wavelets expects comma-separated biases, e.g. 3,0,0,0, or 'pixinsight'")
            if not 1 <= len(args.wavelets) <= 8 or any(v < -1 for v in args.wavelets):
                sys.exit("--wavelets: 1 to 8 layers, each bias ≥ -1 (-1 removes a layer, 0 keeps it)")
        try:
            d = [float(v) for v in (args.wavelet_denoise or "").split(",") if v.strip()]
        except ValueError:
            sys.exit("--wavelet-denoise expects comma-separated numbers, e.g. 1,0.5")
        args.wavelet_denoise = (d + [0.0] * len(args.wavelets))[:len(args.wavelets)]
        if any(v < 0 for v in args.wavelet_denoise):
            sys.exit("--wavelet-denoise cannot be negative")
    else:
        args.wavelet_denoise = []
    if not 0 <= args.highlights < 0.5:
        sys.exit("--highlights must be in [0, 0.5)")
    if not 0 <= args.contrast < 0.5:
        sys.exit("--contrast must be in [0, 0.5): larger values make the curve fall at the ends (brighter input, darker output)")
    if not 0 < args.knee_percentile < 99.9:
        sys.exit("--knee-percentile must be between 0 and 99.9 (the shoulder ends at the 99.99th percentile)")
    if not args.saturation > 0 or args.saturation_passes < 0:
        sys.exit("--saturation must be > 0 and --saturation-passes ≥ 0")
    if args.chroma_denoise < 0 or args.limb_taper < 0:
        sys.exit("--chroma-denoise and --limb-taper cannot be negative")
    if os.path.splitext(args.output)[1].lower() not in (".tif", ".tiff"):
        sys.exit(f"-o {args.output}: the output is a 16-bit TIFF, use a .tif name")
    planned = [args.output, args.record, args.record_vertical]
    if args.display_tif:
        planned.append(os.path.splitext(os.path.abspath(args.output))[0] + "_display.tif")
    mc.check_outputs(planned, [args.input])
    stg = mc.Staging()                   # outputs appear only when the run succeeds
    outputs = mb.open_recorders(stg, args)
    finals = {id(rec): p for (rec, _), p in zip(outputs, [p for p in (args.record, args.record_vertical) if p])}
    land = [st for _, st in outputs if st.fmt == "landscape"]
    pv = mb.Preview(args.preview and mb.preview_possible(), land[0] if land else ps.Observatory(), outputs)
    date = args.date
    if date is None:
        m = [mc.parse_meta(p)[1].get("date") for p in mc.find_images([os.path.join(os.path.dirname(os.path.abspath(args.input)), "panels")])] \
            if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(args.input)), "panels")) else []
        date = next((d for d in m if d), "")
    pv.set_all("session", date)
    code = 1
    try:
        code = pv.run(lambda: run(args, pv, stg))
    except (mb.Abort, KeyboardInterrupt):
        print("aborted: no output written (earlier results are unchanged)", flush=True)
        code = 130
    finally:
        for rec, _ in outputs:
            try:
                secs = rec.close()
            except IOError as e:
                print(f"ERROR {e}: nothing published", flush=True)
                code = code or 1
                continue
            if code == 0:
                print(f"movie ({rec.W}×{rec.H}): {os.path.abspath(finals[id(rec)])} · {secs:.1f} s", flush=True)
        if code == 0:
            stg.publish()
        else:
            stg.discard()
    pv.exit(code or 0)


if __name__ == "__main__":
    main()
