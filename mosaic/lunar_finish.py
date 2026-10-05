#!/usr/bin/env python3
"""Phase 2: finishing touches for a lunar mosaic, with an optional before/after movie.

0. Optional wavelet sharpening (--wavelets), for unsharpened stacks, reproducing PixInsight's ATrousWaveletTransform
   (target RGB/K, no noise reduction or deringing): dyadic layers of 1, 2, 4, 8 px (linear-interpolation or B3-spline
   scaling function) on each channel's stored values, layer k multiplied by PixInsight's gain for its bias:
   1 + 10 · bias, or 1 / (1 + 10 · |bias|) below zero. The biases are PixInsight's own numbers.
   --wavelets pixinsight = 4 layers, linear (3), layer 1 bias +2.3. Optional per-layer noise protection
   (coefficients within DENOISE × the layer's noise keep weight 1).
1. Highlights: brightness is split into a smooth base and fine detail (edge-aware guided filter); a gentle
   shoulder lowers only the brightest part of the base, and the detail goes back unchanged. Bright rays and
   rims come down while keeping their texture.
2. Lunar colour: colour-only noise smoothing (3 px: the same idea as a sharp luminance layer coloured by a blurred
   colour layer in Photoshop), then more saturation along the Moon's natural hues (blue and orange-yellow). Greens, purples and pinks get only a small share of the boost (usually noise or fringing).
   Applied in passes (default 2): each pass multiplies saturation again.
3. Contrast: a gentle S-curve on brightness; black stays 0 and white stays 1.
4. Optional sharpening, reproducing Photoshop's Unsharp Mask: d = original − Gaussian(σ), result = original +
   amount × sign(d) × max(|d| − threshold, 0), threshold in 0–255 levels (Photoshop's threshold is soft: it is
   subtracted, not a cut-off). σ is slightly wider than the radius, as measured in Photoshop 2026: 1.08 × radius at
   1 px, 1.05 × at 2 px, 1.03 × at 4 px. Photoshop sharpens each RGB channel separately in an RGB image; the default
   here sharpens brightness only (like Fade → Luminosity), which avoids colour fringes. --usm-mode rgb gives
   Photoshop's per-channel behaviour.

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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tool_settings as ts  # noqa: E402
import mosaic_common as mc  # noqa: E402
import preview_style as ps  # noqa: E402
import mosaic_builder as mb  # noqa: E402   (Recorder, Preview, frame helpers)

from filters import bgr_to_lab, lab_to_rgb, lunar_weight, rgb_to_lab, wavelet_gain  # noqa: E402
import chain  # noqa: E402

MOVIE = {"opening": 2.0, "card": 1.2, "wipe": 2.0, "hold": 0.8, "fade": 1.0, "final": 2.0, "turn": 2.6}

# the user's PixInsight setting for unsharpened stacks, as saved on 2026-10-05 (ATrousWaveletTransform, dyadic,
# 4 layers, linear (3), layer 1 bias +2.3, target RGB/K, no noise reduction or deringing)
WAVELET_PRESETS = {"pixinsight": dict(biases=(2.3, 0.0, 0.0, 0.0), kernel="linear")}


# ---------------------------------------------------------------- north up

def north_up(small_final, F, input_path=None):
    """Where lunar north is in the finished image (moon_atlas on the reduced copy), and the turn that puts it up:
    dict(M, rot, mirror, north_was, identity, T full-resolution affine, size), or a reason string."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moon_atlas"))
    try:
        import atlas_geo
    except ImportError as e:
        return f"moon_atlas not available ({e})"
    grey = small_final.mean(axis=2).astype(np.float32)
    try:
        geo, _ = atlas_geo.locate(grey, log=lambda *a: None)
    except (SystemExit, atlas_geo.NoUsableLimb) as e:
        # no limb (a close-up mosaic): search the input blind, lit by the Sun at its panels' capture time
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
    m = 0.03 * float((hi - lo).max())          # a little black space around the Moon
    size = tuple(int(-(-(v + 2 * m) // 8) * 8) for v in (hi - lo))
    o = lo - m + ((np.array(size) - (hi - lo + 2 * m)) / 2)
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

STEP_NAMES = {"wavelets": "wavelets", "highlights": "highlights", "saturation": "lunar colour",
              "contrast": "contrast", "unsharp_mask": "sharpening"}


def lab_stats(bgr):
    """Brightness and chroma facts of a reduced copy, on the disk only. None: no such pixels (one-hue images)."""
    lab = bgr_to_lab(bgr)
    Ld = cv2.erode((lab[..., 0] > 5).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    C = np.hypot(lab[..., 1], lab[..., 2])
    hue = (np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) + 360) % 360
    onaxis = lunar_weight(hue) > 0.9
    med = lambda sel: float(np.median(C[sel])) if sel.any() else None
    Lv = lab[..., 0][Ld]
    return {"C_axis": med(Ld & onaxis), "C_off": med(Ld & ~onaxis),
            "p999": float(np.percentile(Lv, 99.9)) if Lv.size else 0.0, "std": float(Lv.std()) if Lv.size else 0.0}


class Snapshots:
    """Reduced copies (F × smaller, BGR, as the movie draws) of the chain input, every block's output — each
    saturation pass too — and the result, stitched from the strips. Lab outputs are converted on the reduced copy:
    converting every stage at full resolution only to shrink it cost 0.35 s per Mpx."""

    def __init__(self, width, F):
        self.width, self.F, self.parts, self.order = width, F, {}, []

    def add(self, key, arr, dom="rgb", prot=None, entry=None, step=None):
        if key not in self.parts:
            self.parts[key] = []
            self.order.append((key, entry, step))
        size = (self.width // self.F, max(1, arr.shape[0] // self.F))
        r = cv2.resize(arr, size, interpolation=cv2.INTER_AREA)
        if dom == "lab":                      # the protection the run applies at its end (D7), on the copy
            p, inp = prot
            inp_s = cv2.resize(inp, size, interpolation=cv2.INTER_AREA)
            lab_in = rgb_to_lab(inp_s)
            c = chain.shadow_gate(lab_in[..., 0])[..., None]
            r[..., 1:] = c * lab_in[..., 1:] + (1 - c) * r[..., 1:]
            r = np.clip(lab_to_rgb(r), 0, 1)
            if p is not None:
                ps = cv2.resize(p, size, interpolation=cv2.INTER_AREA)[..., None]
                r = ps * inp_s + (1 - ps) * r
        self.parts[key].append(np.ascontiguousarray(r[..., ::-1], dtype=np.float32))

    def __call__(self, label, entry, arr, dom, prot, step):
        self.add(label if step is None else f"{label}:{step}", arr, dom, prot, entry, step)

    def stitched(self):
        return {k: np.vstack(v) for k, v in self.parts.items()}


def block_facts(order, small_st, stats, args):
    """One printed line per block, measured on the reduced copies; also returns the facts per snapshot."""
    facts = {k: lab_stats(v) for k, v in small_st.items()}
    lines, prev, mono_said = [], "input", False
    fmt = lambda c: "—" if c is None else f"{c:.2f}"
    groups = []                                       # (entry, [(key, step)]) in chain order
    for key, e, step in order:
        if e is None:
            continue
        if groups and groups[-1][0] is e:
            groups[-1][1].append((key, step))
        else:
            groups.append((e, [(key, step)]))
    for e, keys in groups:
        t, p = e["type"], e["params"]
        st, ro = stats.blocks.get(e["id"], {}), stats.readouts.get(e["id"], {})
        last = keys[-1][0]
        if t == "wavelets":
            lines.append(f"wavelets ({p['kernel']}, {len(p['biases'])} layers, biases " + ", ".join(f"{v:+g}" for v in p["biases"]) +
                         (", noise protection " + ", ".join(f"{v:g}σ" for v in p["denoise"]) if any(p["denoise"]) else "") +
                         "): layer gains " + ", ".join(f"{wavelet_gain(v):.3g}" for v in p["biases"]) +
                         f" · fine-detail amplitude × {ro.get('fine_detail', 1):.2f} (probe) · layer noise " +
                         ", ".join(f"{v * 100:.3f}" for v in st.get("noise", [])) + " % of full scale")
        elif t == "highlights":
            lines.append(f"highlights: brightest 0.1 % L {facts[prev]['p999']:.1f} → {facts[last]['p999']:.1f} "
                         f"({st['affected']:.1f} % of the disk above the knee; detail kept)")
            if args.mono:
                lines.append("colour: a mono image, so there is no colour to saturate (colour stages skipped)")
                mono_said = True
        elif t == "saturation":
            chroma = [facts[prev]["C_axis"]] + [facts[k]["C_axis"] for k, _ in keys if _ is not None]
            off = (facts[prev]["C_off"], facts[last]["C_off"])
            lines.append(f"colour ({p['passes']} passes × {p['factor']:g}"
                         + (f", colour smoothing {p['chroma_blur_px']:g} px" if p["chroma_blur_px"] else "") + "): chroma on lunar hues " +
                         " → ".join(fmt(c) for c in chroma) +
                         (" · no pixels off the lunar hues" if None in off else f" · elsewhere {off[0]:.2f} → {off[1]:.2f}") + " (Lab units)")
        elif t == "contrast":
            lines.append(f"contrast: brightness spread (std) L {facts[prev]['std']:.2f} → {facts[last]['std']:.2f}")
        elif t == "unsharp_mask":
            lines.append(f"sharpening (unsharp mask {p['amount']:.0f} %, {p['radius']:g} px, {p['threshold']:g} levels, "
                         f"{p['mode']}): fine-detail amplitude × {ro.get('fine_detail', 1):.2f} (probe, full resolution)")
        prev = last
    if args.mono and not mono_said:
        lines.append("colour: a mono image, so there is no colour to saturate (colour stages skipped)")
    return lines, facts, groups


def run(args, pv, stg):
    t0 = time.time()
    pv.set_all("log_line", "")
    print(f"lunar_finish  {args.input}", flush=True)
    img = cv2.imread(args.input, cv2.IMREAD_UNCHANGED)
    if img is not None and img.ndim == 2:          # a mono image: three equal channels from here on
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img is None or img.ndim != 3 or img.shape[2] < 3:
        sys.exit("need a mono or a 3-channel (RGB) image")
    if img.dtype not in (np.uint8, np.uint16):
        sys.exit(f"need an 8- or 16-bit image, not {img.dtype}")
    rgb = np.ascontiguousarray(img[..., 2::-1])    # the chain works in RGB; OpenCV reads BGR
    del img
    H, W = rgb.shape[:2]
    q = chain.STATS_Q
    if min(H, W) < 4 * q:
        sys.exit(f"image too small ({W} × {H} px)")
    source = chain.ArraySource(rgb)
    small = source.proxy(q)
    # a mono image (three equal channels, e.g. a mosaic built from mono panels) has no colour to saturate
    args.mono = bool(np.abs(small - small[..., 1:2]).max() < 2e-3)
    chain_file, write_chain = getattr(args, "chain", None), getattr(args, "write_chain", None)
    try:
        doc = chain.load(chain_file) if chain_file else chain.default_chain(args, args.mono)
    except (OSError, ValueError) as e:
        sys.exit(f"--chain {chain_file}: {e}")
    if write_chain:
        chain.dump(doc, write_chain)
        print(f"      chain written to {write_chain}", flush=True)
        if not args.output:
            return 0
    try:
        stats = chain.prepare(source, doc)
    except ValueError as e:
        sys.exit(str(e))
    blocks = chain.enabled(doc, "final")
    hl = next((stats.blocks[e["id"]] for e in blocks if e["type"] == "highlights"), None)
    m0 = float(np.median((rgb_to_lab(small)[..., 0] / 100)[stats.disk]))
    print(f"      disk {stats.disk.sum() * q * q / 1e6:.0f} Mpx · brightness median L {m0 * 100:.1f}" +
          (f" · highlight knee L {hl['knee'] * 100:.1f} · top L {hl['top'] * 100:.1f}" if hl else "") +
          f" · chain: {', '.join(e['type'].replace('_', ' ') for e in blocks) or 'nothing to do'}", flush=True)

    # full resolution, in strips with the chain's halo; reduced copies of every stage for the movie and the facts
    F = max(1, int(np.ceil(max(H, W) / 1100)))
    rows = F * max(1, 1024 // F)
    out16 = np.empty((H, W, 3), np.uint16)                 # BGR for OpenCV; always 16-bit, also from an 8-bit input
    snaps = Snapshots(W, F)
    for y in range(0, H, rows):
        pv.checkpoint()
        n = min(rows, H - y)
        res = chain.render(source, stats, doc, y, 0, n, W, snapshot=snaps)
        out16[y:y + n] = (res[..., ::-1] * 65535 + 0.5).astype(np.uint16)
        snaps.add("final", res)
        print(f"      {y + n}/{H} rows", end="\r", flush=True)
    print()
    small_st = snaps.stitched()
    pv.checkpoint()
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
            kept = detail_kept(rgb, turn["M"])
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
    wp_disp = float(np.percentile(small_st["input"][small_st["input"].max(axis=2) > 0.02], 99.99))
    disp_note = ""
    if args.display_tif:
        disp_path = os.path.splitext(os.path.abspath(args.output))[0] + "_display.tif"
        share = mc.write_display_tiff(stg.path(disp_path), out16, wp_disp, 1.1, in_scale=65535.0)
        disp_note = (f"\n      {os.path.basename(disp_path)}  as in the movie: white point {wp_disp * 65535:.0f} ADU, gamma 1.1 · "
                     f"{share * 100:.2f} % of the Moon at full white")
    lines, facts, groups = block_facts(snaps.order, small_st, stats, args)
    for line in lines:
        print("      " + line)
    print(f"      {os.path.basename(args.output)}  {W} × {H} px · 16-bit · uncompressed · {os.path.getsize(stg.path(args.output)) / 2**30:.2f} GiB · "
          f"{time.time() - t0:.0f} s" + disp_note, flush=True)
    if pv.active():
        movie(args, pv, small_st, facts, groups, stats, doc, source,
              dict(wp_disp=wp_disp, turn=turn, out_size=(W, H)))
    pv.checkpoint()
    return 0


def movie(args, pv, small_st, facts, groups, stats, doc, source, info):
    """Opening -> one step per block (card, before/after wipe, hold) -> north up -> the finished image."""
    FPS = mb.FPS
    f = lambda s_: int(round(s_ * FPS))
    H, W = source.h, source.w
    # the highlight effect is best seen close up: the brightest area (e.g. Tycho's rays), at higher resolution
    Ls = small_st["input"].mean(axis=2)
    Fx = W / Ls.shape[1]
    hot = cv2.GaussianBlur((Ls > np.percentile(Ls[Ls > 0.05], 99.5)).astype(np.float32), (0, 0), 12)
    cy, cx = np.unravel_index(np.argmax(hot), hot.shape)
    S = min(1600, H, W)                                  # square, and inside the image also for small inputs
    X0 = int(np.clip(cx * Fx - S // 2, 0, W - S))
    Y0 = int(np.clip(cy * Fx - S // 2, 0, H - S))
    cs = min(520, S)                                     # sharpening only shows at 100 %: a smaller crop, enlarged
    Yc, Xc = Y0 + (S - cs) // 2, X0 + (S - cs) // 2
    crops = {}

    def crop(kind):
        if kind not in crops:
            y0, x0, n, interp = (Y0, X0, S, cv2.INTER_AREA) if kind == "bright" else (Yc, Xc, cs, cv2.INTER_CUBIC)
            sn = Snapshots(n, 1)
            sn.add("final", chain.render(source, stats, doc, y0, x0, n, n, snapshot=sn))
            crops[kind] = {k: cv2.resize(v, (1000, 1000), interpolation=interp) for k, v in sn.stitched().items()}
        return crops[kind]
    wp = info["wp_disp"]
    tone = lambda im: (np.clip(im / wp, 0, 1) ** (1 / 1.1) * 255).astype(np.uint8)       # same mapping as the _display TIFF
    date = pv.style.session
    for st in pv.styles():
        st.name = "LUNAR MOSAIC · FINISHING"
    steps, prev, closeup_last = [], "input", False
    for e, keys in groups:
        t, p = e["type"], e["params"]
        sb, ro = stats.blocks.get(e["id"], {}), stats.readouts.get(e["id"], {})
        last = keys[-1][0]
        if t in ("wavelets", "unsharp_mask"):
            c = crop("detail")
            if t == "wavelets":
                steps.append(("Wavelet sharpening", f"à trous, {p['kernel']} · close up, enlarged · as in PixInsight",
                              tone(c[prev]), tone(c[last]),
                              [("layers", f"{len(p['biases'])} (1–{2 ** (len(p['biases']) - 1)} px)"),
                               ("biases", " ".join(f"{v:+g}" for v in p["biases"])),
                               ("noise protection", ", ".join(f"{v:g}σ" for v in p["denoise"]) if any(p["denoise"]) else "off"),
                               ("fine detail", f"× {ro.get('fine_detail', 1):.2f}")]))
            else:
                steps.append(("Sharpening", f"unsharp mask as in Photoshop · close up, enlarged · {p['mode']}",
                              tone(c[prev]), tone(c[last]),
                              [("amount", f"{p['amount']:.0f} %"), ("radius", f"{p['radius']:g} px"),
                               ("threshold", f"{p['threshold']:g} levels"), ("fine detail", f"× {ro.get('fine_detail', 1):.2f}")]))
            closeup_last = True
        elif t == "highlights":
            c = crop("bright")
            steps.append(("Recovering highlights", "the brightest area, close up · highlights lowered, detail kept",
                          tone(c[prev]), tone(c[last]),
                          [("knee", f"L {sb['knee'] * 100:.0f}"), ("brightest 0.1 %", f"L {facts[prev]['p999']:.1f} → {facts[last]['p999']:.1f}"),
                           ("affected", f"{sb['affected']:.1f} % of the disk"), ("strength", f"{p['strength']:.2f}")]))
            closeup_last = False
        elif t == "saturation":
            before = prev
            for key, step in keys:
                c0, c1 = facts[before]["C_axis"], facts[key]["C_axis"]
                title = f"Lunar colour · pass {step} of {p['passes']}" if step else "Colour smoothing"
                steps.append((title, "more saturation on the Moon's blue and orange hues only" if step else
                              "colour-only smoothing; brightness keeps all detail",
                              tone(small_st[before]), tone(small_st[key]),
                              [("saturation", f"× {p['factor']:g} on lunar hues" if p["lunar_hue"] else f"× {p['factor']:g}"),
                               ("greens · purples", f"× {1 + (p['factor'] - 1) * 0.15:.2f}" if p["lunar_hue"] else "same"),
                               ("chroma", "—" if None in (c0, c1) else f"{c0:.2f} → {c1:.2f}"),
                               ("limb", f"protected {e.get('limb_fade_px', 0):g} px")]))
                before = key
            closeup_last = False
        elif t == "contrast":
            steps.append(("A touch of contrast", "a gentle S-curve on brightness", tone(small_st[prev]), tone(small_st[last]),
                          [("contrast", f"+{p['strength'] * 100:.0f} % at midtones"), ("pivot", f"L {sb['m'] * 100:.0f}"),
                           ("black · white", "kept"), ("spread", f"{facts[prev]['std']:.2f} → {facts[last]['std']:.2f}")]))
            closeup_last = False
        prev = last
    turn = info.get("turn")
    n_steps = len(steps) + (1 if turn else 0)
    for st in pv.styles():
        st.n_steps = max(1, n_steps)
    pv.show(lambda st: st.card(None, "Lunar Mosaic", f"finishing touches · {date}" if date else "finishing touches"),
            record=MOVIE["opening"] if pv.outputs else None)
    fin = tone(small_st["turned"] if turn else small_st["final"])
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
    ow, oh = info.get("out_size", (W, H))
    first = lambda typ: next((e for e, _ in groups if e["type"] == typ), None)
    wav, hl, sat, con, usm = (first(t) for t in ("wavelets", "highlights", "saturation", "contrast", "unsharp_mask"))
    facts_card = [("size · wavelets", f"{ow} × {oh} px · layer biases " + " ".join(f"{v:+g}" for v in wav["params"]["biases"]))
                  if wav else ("size", f"{ow} × {oh} px"),
                  ("highlights", f"{hl['params']['strength']:.2f}" if hl else "off"),
                  ("colour", "mono image") if args.mono else
                  ("saturation", f"× {sat['params']['factor']:g}, {sat['params']['passes']} passes" if sat else "off"),
                  ("contrast · sharpening" if usm else "contrast",
                   (f"+{con['params']['strength'] * 100:.0f} %" if con else "off") +
                   (f" · USM {usm['params']['amount']:.0f} % {usm['params']['radius']:g} px" if usm else ""))]
    if steps and closeup_last and not turn:
        # the last step ends on a close-up: show the full disk once more so the fade to the final shot is seamless
        full_after = {}

        def last_full(st):
            if id(st) not in full_after:
                full_after[id(st)] = st.layout(fin, [], n_steps - 1, steps[-1][0], "the whole disk", 1.0, steps[-1][4])
            return full_after[id(st)]
        pv.show(last_full, record=0.6 if pv.outputs else None)
        last = last_full
    names = list(dict.fromkeys(STEP_NAMES[e["type"]] for e, _ in groups)) + (["north up"] if turn else [])
    final_frame = {}

    def render_final(st):
        if id(st) not in final_frame:
            final_frame[id(st)] = st.final(fin, date or "finished", " · ".join(names) or "unchanged", facts_card)
        return final_frame[id(st)]
    pv.checkpoint()
    nf = f(MOVIE["fade"])
    if last is not None:
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
    ap.add_argument("-o", "--output", help="output TIFF (16-bit, uncompressed); the input is never changed")
    ap.add_argument("--chain", metavar="FILE.json", help="run this chain file (made in the application or with --write-chain) "
                    "instead of the finishing flags below; north-up, the display TIFF and the movie still apply")
    ap.add_argument("--write-chain", metavar="FILE.json", help="write the chain this run would use (the flags compiled, or "
                    "--chain); without -o, stop there")
    ap.add_argument("--highlights", type=float, default=ts.get("LUNAR_FINISH_HIGHLIGHTS", 0.3, float), help="0 = off; fraction by which the knee-to-top span is lowered (< 0.5)")
    ap.add_argument("--knee-percentile", type=float, default=ts.get("LUNAR_FINISH_KNEE_PERCENTILE"), help="highlight roll-off starts at this brightness percentile of the disk")
    ap.add_argument("--saturation", type=float, default=ts.get("LUNAR_FINISH_SATURATION", 2.0, float), help="saturation factor on the lunar hues (1 = off)")
    ap.add_argument("--saturation-passes", type=int, default=ts.get("LUNAR_FINISH_SATURATION_PASSES", 2, int), help="how many times the saturation boost is applied")
    ap.add_argument("--contrast", type=float, default=ts.get("LUNAR_FINISH_CONTRAST", 0.12, float), help="midtone contrast increase (0.12 = +12 %%), 0 to < 0.5")
    ap.add_argument("--usm", default=ts.get("LUNAR_FINISH_USM") or None, metavar="AMOUNT,RADIUS,THRESHOLD",
                    help="sharpen like Photoshop's Unsharp Mask, e.g. 100,1.5,0 or \"100;1,5;0\" (amount %%, radius px, "
                         "threshold in 0–255 levels, all as Photoshop shows them); off by default")
    ap.add_argument("--usm-mode", choices=("luminosity", "rgb"), default=ts.get("LUNAR_FINISH_USM_MODE"),
                    help="luminosity = brightness only (no colour fringes); rgb = every channel, as Photoshop does in RGB mode")
    ap.add_argument("--wavelets", default=ts.get("LUNAR_FINISH_WAVELETS") or None, metavar="BIASES|pixinsight",
                    help="wavelet sharpening first, as PixInsight's ATrousWaveletTransform: comma-separated layer biases in "
                         "PixInsight's own units (gain 1 + 10 × bias), e.g. 0.6,1.1,0.2,0; 'pixinsight' = 4 layers, linear (3), "
                         "layer 1 +2.3; off by default")
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
                sys.exit("--wavelets expects comma-separated biases, e.g. 0.6,1.1,0.2,0, or 'pixinsight'")
            if not 1 <= len(args.wavelets) <= 8 or any(not -1 <= v <= 15 for v in args.wavelets):
                sys.exit("--wavelets: 1 to 8 layers, each bias from -1 to 15 "
                         "(0 keeps a layer, 1 multiplies it by 11, -1 divides it by 11)")
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
    if not args.output and not args.write_chain:
        ap.error("-o/--output is required (or --write-chain FILE.json to only write the chain)")
    if not args.output and (args.record or args.record_vertical):
        ap.error("--record needs an output: add -o")
    if args.output and os.path.splitext(args.output)[1].lower() not in (".tif", ".tiff"):
        sys.exit(f"-o {args.output}: the output is a 16-bit TIFF, use a .tif name")
    planned = [args.output, args.record, args.record_vertical]
    if args.display_tif and args.output:
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
