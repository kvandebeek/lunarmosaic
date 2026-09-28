#!/usr/bin/env python3
"""Same-terrain 100 % crops of overlapping panels, to check quality scores by eye.

Crops come from the overlap interior at about equal distance from both frame centres (so the measured
centre-to-edge falloff cancels), both stretched identically.

    python3 compare_crops.py panel_manifest.json --pair 2103_9,2109_2 [-o out.png]
    python3 compare_crops.py panel_manifest.json --blind 6 [-o blind_dir]

--blind N picks N pairs (4 with a large score difference, the rest near-equal), randomises left/right,
labels them X/Y only, and writes the answer key to key.json. Judge first, then open the key.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mosaic_common as mc  # noqa: E402

cache = {}


def load(path):
    if path not in cache:
        x, _, _ = mc.read_image(path)
        L = mc.luminance(x)
        _, m, _, _, _, _ = mc.subject_and_sky(L, x, 32, 120)   # 120 px from the limb: keeps crops off the limb
        cache[path] = (L, m)
    return cache[path]


def crop_pair(man, a, b, S=400, band=200):
    path = {i["id"]: i["path"] for i in man["images"]}
    for n in (a, b):
        if n not in path:
            sys.exit(f"no panel with id {n} in the manifest")
    p = next((q for q in man["pairs"] if {q["a"], q["b"]} == {a, b}), None)
    if p is None:
        sys.exit(f"{a} and {b} are not an overlapping pair in the manifest")
    T = np.array(p["T_full"])
    if p["a"] != a:
        T = cv2.invertAffineTransform(T)
    La, ma = load(path[a])
    Lb, mb = load(path[b])
    h, w = La.shape
    hb, wb = Lb.shape
    Ti = cv2.invertAffineTransform(T)
    Lbw = cv2.warpAffine(Lb, Ti, (w, h), flags=cv2.INTER_LANCZOS4)
    mbw = cv2.warpAffine(mb.astype(np.uint8), Ti, (w, h), flags=cv2.INTER_NEAREST) > 0
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    ra = np.hypot(X - (w - 1) / 2, Y - (h - 1) / 2)
    rb = np.hypot(T[0, 0] * X + T[0, 1] * Y + T[0, 2] - (wb - 1) / 2, T[1, 0] * X + T[1, 1] * Y + T[1, 2] - (hb - 1) / 2)
    inside = cv2.distanceTransform((ma & mbw).astype(np.uint8), cv2.DIST_L2, 5)
    for need, bd in ((S * 0.72, band), (S * 0.6, 2 * band), (S * 0.5, 4 * band)):
        ok = (inside > need) & (np.abs(ra - rb) < bd)
        if ok.any():
            break
    else:
        return None
    tex = cv2.GaussianBlur((cv2.GaussianBlur(La, (0, 0), 2.5) - cv2.GaussianBlur(La, (0, 0), 6)) ** 2, (0, 0), 40)
    tex[~ok] = 0
    y, x = np.unravel_index(np.argmax(tex), tex.shape)
    y, x = int(np.clip(y, S // 2, h - S // 2)), int(np.clip(x, S // 2, w - S // 2))
    A = La[y - S // 2:y + S // 2, x - S // 2:x + S // 2]
    B = Lbw[y - S // 2:y + S // 2, x - S // 2:x + S // 2]
    return A / A.mean(), B / B.mean(), (x, y)


def amp_ratio(A, B, s1, s2):
    return float(np.sqrt(mc.band_energy(A, s1, s2)[16:-16, 16:-16].mean() / mc.band_energy(B, s1, s2)[16:-16, 16:-16].mean()))


def tile(A, B, left_label, right_label):
    lo, hi = np.percentile(np.r_[A.ravel(), B.ravel()], [0.5, 99.5])
    st = lambda I: (np.clip((I - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    S = A.shape[0]
    t = np.hstack([st(A), np.full((S, 10), 255, np.uint8), st(B)])
    t = cv2.copyMakeBorder(t, 32, 6, 0, 0, cv2.BORDER_CONSTANT, value=255)
    cv2.putText(t, left_label, (6, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 0, 2)
    cv2.putText(t, right_label, (S + 16, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 0, 2)
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=mc.HelpFormatter)
    ap.add_argument("manifest")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pair", help="two panel ids, comma-separated")
    g.add_argument("--blind", type=int, metavar="N", help="blind test with N pairs")
    ap.add_argument("-o", "--out", default=".", help="output PNG (with --pair) or folder (with --blind)")
    ap.add_argument("--size", type=int, default=400, help="crop size in px (100 %%)")
    ap.add_argument("--seed", type=int, default=7, help="random seed for the blind selection and left/right order")
    args = ap.parse_args()
    man = json.load(open(args.manifest))
    if not man.get("pairs"):
        sys.exit("manifest has no pairs: run panel_classifier.py with --relative")
    rd = {i["id"]: i["metrics"].get("relative_detail") for i in man["images"]}

    if args.pair:
        a, b = args.pair.split(",")
        r = crop_pair(man, a, b, args.size)
        if r is None:
            sys.exit("no interior overlap found for this pair")
        A, B, xy = r
        out = args.out if args.out.endswith(".png") else os.path.join(args.out, f"compare_{a}_{b}.png")
        mc.check_outputs([out], [i["path"] for i in man["images"]])
        if not cv2.imwrite(out, tile(A, B, f"{a} ({rd.get(a) or 0:.2f})", f"{b} ({rd.get(b) or 0:.2f})")):
            sys.exit(f"could not write {out} (does the folder exist?)")
        print(f"{out}  ·  crop centre {xy} in {a}")
        print(f"detail ratio {a}/{b}: fine {amp_ratio(A, B, 1.0, 2.5):.2f} · coarse {amp_ratio(A, B, 2.5, 6.0):.2f}")
        return

    rng = np.random.default_rng(args.seed)
    ratio = lambda p: max(rd[p["a"]], rd[p["b"]]) / min(rd[p["a"]], rd[p["b"]])
    usable = [p for p in man["pairs"] if rd.get(p["a"]) and rd.get(p["b"]) and p["inliers"] > 300]
    big = [p for p in usable if ratio(p) > 1.5]
    small = [p for p in usable if ratio(p) < 1.1]
    n_big = min(len(big), max(1, round(args.blind * 2 / 3)))
    chosen = [big[k] for k in rng.choice(len(big), n_big, replace=False)]
    chosen += [small[k] for k in rng.choice(len(small), min(len(small), args.blind - n_big), replace=False)]
    rng.shuffle(chosen)
    os.makedirs(args.out, exist_ok=True)
    key = []
    for n, p in enumerate(chosen, 1):
        r = crop_pair(man, p["a"], p["b"], args.size)
        if r is None:
            continue
        A, B, _ = r
        flip = bool(rng.integers(2))
        X, Y = (p["b"], p["a"]) if flip else (p["a"], p["b"])
        cv2.imwrite(os.path.join(args.out, f"blind_{n}.png"), tile(B, A, f"Pair {n}: X", "Y") if flip else tile(A, B, f"Pair {n}: X", "Y"))
        key.append({"pair": n, "X": X, "Y": Y, "relative_detail_X": rd[X], "relative_detail_Y": rd[Y],
                    "score_prefers": "X" if rd[X] > rd[Y] else "Y"})
    json.dump(key, open(os.path.join(args.out, "key.json"), "w"), indent=1)
    print(f"{len(key)} blind pairs written to {args.out} (blind_N.png). Judge them first, then open key.json.")


if __name__ == "__main__":
    main()
