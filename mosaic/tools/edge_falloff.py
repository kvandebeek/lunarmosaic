#!/usr/bin/env python3
"""How does image detail change towards the frame edge? (optics / tilt / stacking diagnostic)

For overlapping panel pairs from a panel manifest (panel_classifier.py --relative), each panel's detail is
compared with its partner's on the same terrain, using only partner pixels deep inside the partner frame.
Results are relative to the panel's own interior, so terrain and panel quality cancel out.

    python3 edge_falloff.py panel_manifest.json [--pairs 200] [--by distance|side]

--by distance : detail vs distance from the nearest frame edge
--by side     : detail within 32–300 px of the left / right / top / bottom edge (asymmetry hints at tilt)
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mosaic_common as mc  # noqa: E402

BANDS = [(0.5, 1.0), (1.0, 2.5), (2.5, 6.0)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=mc.HelpFormatter)
    ap.add_argument("manifest")
    ap.add_argument("--pairs", type=int, default=200, help="number of overlapping pairs to sample")
    ap.add_argument("--by", choices=("distance", "side"), default="distance",
                    help="distance = by distance from the nearest frame edge; side = per frame edge (tilt check)")
    ap.add_argument("--seed", type=int, default=1, help="random seed for the pair sample")
    args = ap.parse_args()

    man = json.load(open(args.manifest))
    if not man.get("pairs"):
        sys.exit("manifest has no pairs: run panel_classifier.py with --relative")
    path = {i["id"]: i["path"] for i in man["images"]}
    rng = np.random.default_rng(args.seed)
    pairs = [man["pairs"][k] for k in rng.choice(len(man["pairs"]), min(args.pairs, len(man["pairs"])), replace=False)]
    if args.by == "distance":
        bins = [(32, 150), (150, 300), (300, 450), (450, 600), (600, 750)]
        names = [f"{a}-{b} px" for a, b in bins]
        interior, partner_min = 750, 650
    else:
        names = ["left", "right", "top", "bottom"]
        interior, partner_min = 700, 650
    cache = {}

    def load(i):
        if i not in cache:
            if len(cache) > 30:
                cache.clear()
            x, _, _ = mc.read_image(path[i])
            L = mc.luminance(x)
            _, m, _, _, _, _ = mc.subject_and_sky(L, x, 32, 40)
            h, w = L.shape
            yy, xx = np.mgrid[0:h, 0:w]
            d = np.stack([xx, w - 1 - xx, yy, h - 1 - yy]).astype(np.float32)   # distance to left/right/top/bottom
            cache[i] = (L, m, d, [mc.band_energy(L, s1, s2) for s1, s2 in BANDS])
        return cache[i]

    def one(a, b, T):
        La, ma, da, Ea = load(a)
        Lb, mb, db, Eb = load(b)
        h, w = La.shape
        Ti = cv2.invertAffineTransform(T)
        wb = lambda I, fl=cv2.INTER_LINEAR: cv2.warpAffine(I, Ti, (w, h), flags=fl)
        Lbw = wb(Lb)
        base = ma & (wb(mb.astype(np.uint8), cv2.INTER_NEAREST) > 0) & (wb(db.min(0), cv2.INTER_NEAREST) >= partner_min)
        Ebw = [wb(e) for e in Eb]
        dmin, side = da.min(0), da.argmin(0)

        def ratio(sel):
            if sel.sum() < 20000:
                return None
            na, nb = La[sel].mean() ** 2, Lbw[sel].mean() ** 2
            return [float(np.log((Ea[j][sel].mean() / na) / (Ebw[j][sel].mean() / nb))) for j in range(len(BANDS))]
        ref = ratio(base & (dmin >= interior))
        if ref is None:
            return None
        if args.by == "distance":
            sels = [base & (dmin >= lo) & (dmin < hi) for lo, hi in bins]
        else:
            sels = [base & (dmin >= 32) & (dmin < 300) & (side == k) for k in range(4)]
        return [None if r is None else [x - y for x, y in zip(r, ref)] for r in (ratio(s) for s in sels)]

    res = []
    for n, p in enumerate(pairs, 1):
        T = np.array(p["T_full"])
        for a, b, TT in ((p["a"], p["b"], T), (p["b"], p["a"], cv2.invertAffineTransform(T))):
            o = one(a, b, TT)
            if o:
                res.append(o)
        if n % 20 == 0:
            print(f"  {n}/{len(pairs)} pairs", flush=True)

    print(f"\nDetail relative to the same panel's interior (≥ {interior} px from the edge), same terrain as the partner:")
    print(f"{'':14s}{'n':>5s}   " + "   ".join(f"σ {a}–{b} px".ljust(12) for a, b in BANDS))
    for k, name in enumerate(names):
        v = np.array([r[k] for r in res if r[k] is not None])
        if len(v):
            amp = np.exp(np.median(v, 0) / 2)
            print(f"{name:14s}{len(v):5d}   " + "   ".join(f"{x:.2f}".ljust(12) for x in amp))
    print("\n(σ 0.5–1 px is mostly noise; the other two columns are fine and coarse detail. 1.00 = as sharp as the interior.)")


if __name__ == "__main__":
    main()
