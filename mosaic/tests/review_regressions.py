"""Regression checks for bugs found in the 2026-09-27 reviews (synthetic data, a few seconds).

    python3 tests/review_regressions.py          exit status 1 if any check fails
"""
import itertools
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import cv2
import numpy as np

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
import mosaic_common as mc  # noqa: E402
import mosaic_stages as ms  # noqa: E402
import mosaic_builder as mb  # noqa: E402
import lunar_finish as lf  # noqa: E402
import preview_style as ps  # noqa: E402

HERE = tempfile.mkdtemp(prefix="mosaic_regress_")
FAILED = []


def test(fn):
    print(f"TEST {fn.__name__}", flush=True)
    try:
        fn()
        print("  ok", flush=True)
    except BaseException as e:           # noqa: B902 (report every failure, keep going)
        FAILED.append(fn.__name__)
        print(f"  FAILED {type(e).__name__}: {e}", flush=True)


def bridge_drop():
    """A panel whose only tile pair is dropped keeps its place via the keypoint fallback (not the origin)."""
    nodes = list("abcde")
    sizes = {n: (2000, 2000) for n in nodes}
    idx = {n: i for i, n in enumerate(nodes)}
    sim = np.array([[1.0, 0, i * 100.0, i * 50.0] for i in range(5)])
    keys = list(itertools.combinations(nodes[:4], 2)) + [("d", "e")]
    p = np.array([[x, y] for y in (600.0, 1000, 1400) for x in (600.0, 1000, 1400)])
    orig = ms.tiles_for_pair

    def fake(a, b, *args, **kwargs):
        shift = sim[idx[a], 2:] - sim[idx[b], 2:]
        noise = np.zeros_like(p)
        if b == "e":
            noise[:, 0] = [5, -5, 5, -5, 5, -5, 5, -5, 5]
        return [(pa, pb, 0.9) for pa, pb in zip(p, p + shift + noise)]
    coarse = {(a, b): (ms.centred(p, sizes[a]), ms.centred(p, sizes[a]) + sim[idx[a], 2:] - sim[idx[b], 2:]) for a, b in keys}
    ms.tiles_for_pair = fake
    try:
        for model in (ms.Model("similarity"), ms.make_model("poly3", sizes)):
            with ThreadPoolExecutor(1) as pool:
                th, ix, st = ms.fine_align(nodes, keys, sizes, {}, {}, {}, 4, sim, idx, "a", model, coarse, pool, iterations=1)
            c = np.array([[1000.0, 1000]])
            e = ms.to_canvas(model, th[ix["e"]], sizes["e"], c) - ms.to_canvas(model, th[ix["a"]], sizes["a"], c)
            assert np.allclose(e, [[400, 200]], atol=0.5), f"{model.name}: panel e at {e} instead of [400, 200]"
    finally:
        ms.tiles_for_pair = orig


def fine_all_fallback():
    p = np.array([[-30.0, -30], [30, -30], [-30, 30], [30, 30]])
    sz = {n: (256, 256) for n in "ab"}
    one = {n: np.ones((256, 256), np.float32) for n in sz}
    with ThreadPoolExecutor(1) as pool:
        th, ix, st = ms.fine_align(["a", "b"], [("a", "b")], sz, one, {n: np.ones((256, 256), bool) for n in sz},
                                   {n: np.ones((64, 64), np.float32) for n in sz}, 4, np.array([[1.0, 0, 0, 0], [1, 0, 30, 0]]),
                                   {"a": 0, "b": 1}, "a", ms.Model("similarity"), {("a", "b"): (p, p - [30, 0])}, pool, iterations=1)
    assert st["tile_pairs"] == 0 and np.allclose(th[ix["b"]], [1, 0, 30, 0], atol=1e-6), th[ix["b"]]


def photometry_no_overlap():
    names = ["a", "b"]
    g, o, st = ms.photometry(names, [("a", "b")], {n: np.eye(3) for n in names}, {n: np.full((32, 32, 3), 0.2, np.float32) for n in names},
                             {n: np.ones((32, 32), bool) for n in names}, {n: np.zeros((32, 32), bool) for n in names}, 4, "a")
    assert st["pairs"] == 0 and all(np.allclose(g[n], 1) and np.allclose(o[n], 0) for n in names)


def pyramid_levels_and_border0():
    path = os.path.join(HERE, "panel.tif")
    cv2.imwrite(path, np.full((384, 384, 3), 20000, np.uint16))
    lab = np.zeros((128, 128), np.uint16)
    for levels in (7, 8):
        step = max(128, 2 ** levels)
        pad = (-(-384 // step) * step,) * 2
        acc, wa = ms.new_pyramids(pad, levels)
        ms.accumulate(acc, wa, ms.blend_panel("a", 0, path, (384, 384), np.eye(3), np.ones(3), np.zeros(3),
                                              lab[:pad[1] // 4, :pad[0] // 4], 4, pad, levels, border=6))
        ms.collapse(acc, wa)
    assert ms.blend_panel("a", 0, path, (384, 384), np.eye(3), np.ones(3), np.zeros(3), lab[:96, :96], 4, (384, 384), 7, border=0) is not None


def proxy_non_divisible():
    for W in (3768, 3771):
        f = 4
        L = np.tile(np.arange(W, dtype=np.float32), (40, 1))
        P = cv2.resize(mc.proxy_crop(L, f), (W // f, 40 // f), interpolation=cv2.INTER_AREA)
        x = np.array([0, 100, W // f - 1])
        assert np.abs(P[0, x] - (x * f + (f - 1) / 2)).max() < 1e-3, W


def finish_cancel():
    im = np.zeros((256, 256, 3), np.uint16)
    im[64:192, 64:192] = 30000
    path, out = os.path.join(HERE, "finish_in.tif"), os.path.join(HERE, "cancelled_finish.tif")
    cv2.imwrite(path, im)
    par = NS(input=path, output=out, highlights=0.3, chroma_denoise=3.0, saturation=2.0, saturation_passes=2, limb_taper=80,
             contrast=0.12, usm=None, usm_mode="luminosity", knee_percentile=90, display_tif=False,
             wavelets=None, wavelet_denoise=[], wavelet_kernel="linear", north_up=True)
    pv, stg = mb.Preview(False, ps.Observatory()), mc.Staging()
    pv.abort.set()
    try:
        lf.run(par, pv, stg)
        raise AssertionError("run() ignored the abort")
    except mb.Abort:
        stg.discard()
    assert not os.path.exists(out) and not [f for f in os.listdir(HERE) if "partial" in f]


def contrast_curve_monotonic():
    z = np.linspace(0, 1, 10001)
    for c in (0.12, 0.49):
        assert np.diff(lf.s_curve(z, 0.4, c)).min() >= 0, c


def no_gaps_at_panel_edges():
    """Every pixel of a region must get full-resolution weight from its owner (the black 1 px lines had none)."""
    rng = np.random.default_rng(5)
    w, h, border, q = 640, 420, 7, 4
    sizes, L, paths = {"a": (w, h), "b": (w, h)}, {}, {}
    for n, s in (("a", 1.2), ("b", 0.8)):
        img = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), s) * 0.5 + 0.25
        L[n], paths[n] = img, os.path.join(HERE, f"gap_{n}.tif")
        cv2.imwrite(paths[n], (np.dstack([img] * 3) * 65535).astype(np.uint16))
    t = np.radians(0.3)
    P = {"a": np.array([[1, 0, 10.0], [0, 1, 10.0], [0, 0, 1]]),
         "b": np.array([[np.cos(t), -np.sin(t), 330.37], [np.sin(t), np.cos(t), 31.61], [0, 0, 1]])}
    canvas_pad = (1024, 512)
    with ThreadPoolExecutor(2) as pool:
        lab, _ = ms.select_regions(["a", "b"], sizes, L, P, canvas_pad, q, pool, borders={"a": border, "b": border})
    acc, wacc = ms.new_pyramids(canvas_pad, 4)
    for i, n in enumerate("ab"):
        r = ms.blend_panel(n, i, paths[n], sizes[n], P[n], np.ones(3), np.zeros(3), lab, q, canvas_pad, 4, border=border)
        if r is not None:
            ms.accumulate(acc, wacc, r)
    owned = cv2.resize((lab != 65535).astype(np.uint8), canvas_pad, interpolation=cv2.INTER_NEAREST) > 0
    gaps = int(np.count_nonzero(owned & (wacc[0] <= 1e-3)))
    assert gaps == 0, f"{gaps} px owned by a panel without full-resolution data"


def lit_peaks_beyond_terminator_kept():
    """Isolated sunlit peaks outside the main lit area must survive the black-space step."""
    H, W = 800, 800
    out = np.zeros((H, W, 3), np.float32)
    yy, xx = np.mgrid[:H, :W]
    out[np.hypot(xx - 300, yy - 400) < 250] = 0.4                      # the lit disk
    out[np.hypot(xx - 650, yy - 400) < 8] = 0.3                        # a peak beyond the terminator
    out += 0.002
    cov = np.ones((H, W), bool)
    ms.finish(out, cov, background="neutral-black", colour="none")
    assert out[400, 650].max() > 0.1, "peak blackened"


def json_safe_and_bounded_map():
    assert mb.json_safe({"a": [float("nan"), 1.0], "b": float("inf")}) == {"a": [None, 1.0], "b": None}
    with ThreadPoolExecutor(3) as pool:
        assert list(mb.bounded_map(pool, lambda x: x * x, range(20), 4)) == [x * x for x in range(20)]


def sky_pedestal_neutralised():
    """Stacks with a sky pedestal above 2 % of full scale (26 May 2026: 7 %) still get a neutral black sky."""
    H = W = 1200
    out = np.full((H, W, 3), 0.072, np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    out[np.hypot(xx - 600, yy - 600) < 420] = 0.30
    out += np.random.default_rng(1).normal(0, 0.002, out.shape).astype(np.float32)
    ms.finish(out, np.ones((H, W), bool), background="neutral-black", colour="none")
    assert out[20:100, 20:100].max() < 0.01, f"sky left at {out[20:100, 20:100].mean():.3f}"
    assert abs(out[600, 600].mean() - (0.30 - 0.072)) < 0.02, "disk not kept"


def north_up_crop_is_centred():
    """centred_crop(): the Moon keeps an equal margin on every side (2026-10-11: a sign error gave it 113 px
    above and 23 px below on a real mosaic -- the size-rounding slack was all pushed to one side instead of
    split evenly)."""
    rng = np.random.default_rng(7)
    for _ in range(50):
        lo = rng.uniform(-50, 50, 2)
        hi = lo + rng.uniform(100, 3000, 2)                 # an asymmetric, non-square, non-multiple-of-8 box
        size, o = lf.centred_crop(lo, hi)
        before, after = lo - o, np.array(size) - (hi - o)   # margin on the low side and the high side, per axis
        assert np.abs(before - after).max() < 1.0, f"lo={lo} hi={hi}: margins {before} vs {after}"
        assert all(s % 8 == 0 for s in size), size


def fold_in_disk_centres_a_crescent():
    """fold_in_disk(): a near-full mosaic (most of the disk's diameter already lit) is framed on the whole
    Moon, so a crescent/gibbous mosaic gets the same even margin a full Moon would (2026-10-11: a 14-panel
    near-full mosaic, lit content only, came out with 221 px above the crescent and 48 px below it -- the
    crop was centred on the sunlit shape, not on the disk). A genuine close-up (a crater crop, far smaller
    than the disk) is left exactly as it was: folding in a 2786 px lunar radius around a crop a few hundred
    px wide would be almost entirely black."""
    radius, centre = 2786.1, np.array([1926.0, 3048.0])
    # the real case (lunar_finish.py, 2026-10-11): lit content spans 60 % of the disk's width, 101 % of its
    # height -- a crescent whose horns reach pole to pole, off-centre only because the dark limb is unlit
    lo, hi = np.array([252.0, 222.0]), np.array([3600.0, 5874.0])
    flo, fhi = lf.fold_in_disk(lo, hi, centre, radius)
    size, o = lf.centred_crop(flo, fhi)
    before, after = flo - o, np.array(size) - (fhi - o)
    assert np.abs(before - after).max() < 1.0, f"not centred on the disk: margins {before} vs {after}"

    # a close-up: content under half the disk's diameter must be left untouched
    lo, hi = np.array([1700.0, 2900.0]), np.array([2100.0, 3200.0])
    flo, fhi = lf.fold_in_disk(lo, hi, centre, radius)
    assert np.array_equal(flo, lo) and np.array_equal(fhi, hi), "a close-up must not be framed on the whole Moon"


def mesh_model_round_trip():
    """poly3+mesh: a zero mesh equals poly3; with a smooth mesh, from_canvas inverts to_canvas; the layout row
    (padded to the longest panel) still decodes."""
    sizes = {"a": (3768, 2104), "b": (3720, 2088)}
    mm = ms.make_model("poly3+mesh", sizes)
    base = mm.from_similarity([1.0, 0.01, 120.0, -40.0])
    base[3] = 2e-3
    rng = np.random.default_rng(3)
    p = rng.uniform([0, 0], [3767, 2103], (200, 2))
    zero = mm.pack(base, sizes["a"], np.zeros((np.prod(ms.mesh_shape(sizes["a"], mm.spacing)), 2)))
    poly = ms.make_model("poly3", sizes)
    assert np.allclose(ms.to_canvas(mm, zero, sizes["a"], p), ms.to_canvas(poly, base, sizes["a"], p)), "zero mesh differs from poly3"
    nx, ny = ms.mesh_shape(sizes["a"], mm.spacing)
    gx, gy = np.meshgrid(np.arange(nx), np.arange(ny))
    D = np.stack([1.5 * np.sin(gx / 3.0), 1.2 * np.cos(gy / 2.5)], -1).reshape(-1, 2)
    row = np.pad(mm.pack(base, sizes["a"], D), (0, 50))
    X = ms.to_canvas(mm, row, sizes["a"], p)
    back = ms.from_canvas(mm, row, sizes["a"], X)
    assert np.abs(back - p).max() < 1e-3, f"round trip off by {np.abs(back - p).max():.4f} px"
    shift = X - ms.to_canvas(poly, base, sizes["a"], p)
    assert 0.1 < np.abs(shift).max() <= 1.51, f"mesh displacement {np.abs(shift).max():.2f} px not applied"


for fn in (bridge_drop, fine_all_fallback, photometry_no_overlap, pyramid_levels_and_border0, proxy_non_divisible, finish_cancel,
           contrast_curve_monotonic, no_gaps_at_panel_edges, lit_peaks_beyond_terminator_kept, json_safe_and_bounded_map,
           north_up_crop_is_centred, fold_in_disk_centres_a_crescent, mesh_model_round_trip, sky_pedestal_neutralised):
    test(fn)
print(f"\n{'ALL OK' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
sys.exit(1 if FAILED else 0)
