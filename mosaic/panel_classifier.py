#!/usr/bin/env python3
"""Score the quality of every image in one or more folders and write a JSON manifest + Markdown report.

Single-image metrics work on any image set. With --relative, overlapping images (mosaic panels) are
also compared on shared terrain, which is the authoritative quality ranking. Files are never moved.

    python3 panel_classifier.py <folder|file> [...] -o manifest.json [--relative]
"""
import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atlas_link  # noqa: E402   (puts the LunarAtlas checkout on sys.path)
import tool_settings as ts  # noqa: E402
import mosaic_common as mc  # noqa: E402

SCHEMA = "panel-manifest/2"


# ---------------------------------------------------------------- stage 1: per image

def measure(path, args, keep_for_relative):
    pid, meta = mc.parse_meta(path)
    rec = {"path": path, "id": pid, "meta": meta, "reject": False, "reject_reason": None}
    try:
        x, dtype, ch = mc.read_image(path)
    except Exception as e:  # unreadable or unsupported
        rec.update(reject=True, reject_reason=f"unreadable: {e}", metrics={}, width=None, height=None)
        return rec, None
    h, w = x.shape[:2]
    L = mc.luminance(x)
    disk, m, sky, coverage, sat, info = mc.subject_and_sky(L, x, args.border, args.erode)
    met = mc.sharpness_metrics(L, m)
    met.update(coverage_pct=round(coverage, 2),
               saturated_pct=round(float(sat.mean() * 100), 4),
               mean=float(L[m].mean() * mc.FULL) if m.any() else None,
               std=float(L[m].std() * mc.FULL) if m.any() else None,
               mask_pct=round(float(m.mean() * 100), 2),
               noise_sigma=None, sky_rgb=None, sky_zero_pct_rgb=None)
    if sky is not None:
        Ls = L[sky]
        met["noise_sigma"] = float(np.median(np.abs(Ls - np.median(Ls))) * 1.4826 * mc.FULL)
        if ch == 3:
            met["sky_rgb"] = [round(float(np.median(x[..., c][sky]) * mc.FULL), 1) for c in (2, 1, 0)]
            met["sky_zero_pct_rgb"] = [round(float((x[..., c][sky] == 0).mean() * 100), 1) for c in (2, 1, 0)]
    rec.update(width=w, height=h, channels=ch, dtype=dtype, metrics=met, sky_mode=info["sky_mode"])
    extra = None
    if keep_for_relative:
        extra = {"L": L.astype(np.float16), "m": m}
    return rec, extra


# ---------------------------------------------------------------- stage 3: same-terrain comparison

BANDS = ((1.0, 2.5), (2.5, 6.0))   # fine and coarse detail; σ 0.5–1.0 was measured to be mostly noise


def _side(LA, mA, LB, mB, T_AB, bbox, min_px, radius_tol):
    """Log energy ratio A/B per band on common terrain, B warped into A's frame.

    Detail falls off with distance from the frame centre (measured on this set: 0.95× at 600–750 px
    from the frame edge, down to 0.72× at the edge). Only pixels at about the same distance from
    both frame centres are compared, so that falloff cancels."""
    x0, y0, x1, y1 = bbox
    A = LA[y0:y1, x0:x1].astype(np.float32)
    ch, cw = A.shape
    ha, wa = LA.shape
    hb, wb = LB.shape
    cor = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], np.float64) @ T_AB.T
    bx0, by0 = max(0, int(cor[:, 0].min()) - 8), max(0, int(cor[:, 1].min()) - 8)
    bx1, by1 = min(wb, int(cor[:, 0].max()) + 9), min(hb, int(cor[:, 1].max()) + 9)
    if bx1 - bx0 < 16 or by1 - by0 < 16:
        return None
    Bc = LB[by0:by1, bx0:bx1].astype(np.float32)
    mBc = mB[by0:by1, bx0:bx1].astype(np.uint8)
    M = T_AB.copy()
    M[:, 2] = T_AB[:, :2] @ [x0, y0] + T_AB[:, 2] - [bx0, by0]   # crop-of-A coords -> crop-of-B coords
    Bw = cv2.warpAffine(Bc, M, (cw, ch), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP)
    mBw = cv2.warpAffine(mBc, M, (cw, ch), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
    common = (mA[y0:y1, x0:x1] & (mBw > 0)).astype(np.uint8)
    common = cv2.distanceTransform(common, cv2.DIST_L2, 3) > 8     # keep away from crop/mask edges
    Y, X = np.mgrid[0:ch, 0:cw].astype(np.float32)
    ra = np.hypot(X + x0 - (wa - 1) / 2, Y + y0 - (ha - 1) / 2)
    xb = M[0, 0] * X + M[0, 1] * Y + M[0, 2] + bx0
    yb = M[1, 0] * X + M[1, 1] * Y + M[1, 2] + by0
    dr = np.abs(ra - np.hypot(xb - (wb - 1) / 2, yb - (hb - 1) / 2))
    for tol in (radius_tol, 2 * radius_tol):
        sel = common & (dr <= tol)
        if sel.sum() >= min_px:
            break
    else:
        return None
    na, nb = A[sel].mean() ** 2, Bw[sel].mean() ** 2
    logs = [float(np.log((mc.band_energy(A, s1, s2)[sel].mean() / na) / (mc.band_energy(Bw, s1, s2)[sel].mean() / nb)))
            for s1, s2 in BANDS]
    return logs, int(sel.sum())


def _overlap_bbox(pm_self, pm_other, T_self_to_other, f, shape):
    """Full-res bbox (in 'self') of the proxy overlap with 'other'."""
    other_in_self = cv2.warpAffine(pm_other, cv2.invertAffineTransform(T_self_to_other),
                                   (pm_self.shape[1], pm_self.shape[0]), flags=cv2.INTER_NEAREST)
    ov = (pm_self > 0) & (other_in_self > 0)
    ys, xs = np.where(ov)
    if len(ys) == 0:
        return None
    h, w = shape
    return (max(0, xs.min() * f - 16), max(0, ys.min() * f - 16),
            min(w, (xs.max() + 1) * f + 16), min(h, (ys.max() + 1) * f + 16))


def compare_pair(a, b, pair, data, prox, f, min_px, radius_tol):
    Tp = pair["T"]
    T = mc.proxy_to_full(Tp, f)
    Ti = cv2.invertAffineTransform(T)
    La, ma = data[a]["L"], data[a]["m"]
    Lb, mb = data[b]["L"], data[b]["m"]
    ba = _overlap_bbox(prox[a], prox[b], Tp, f, La.shape)
    bb = _overlap_bbox(prox[b], prox[a], cv2.invertAffineTransform(Tp), f, Lb.shape)
    if ba is None or bb is None:
        return None
    r1 = _side(La, ma, Lb, mb, T, ba, min_px, radius_tol)      # a native vs b warped (interpolation favours a)
    r2 = _side(Lb, mb, La, ma, Ti, bb, min_px, radius_tol)     # b native vs a warped (interpolation favours b)
    if r1 is None or r2 is None:
        return None
    fine, coarse = [(r1[0][j] - r2[0][j]) / 2 for j in range(2)]
    return {"logr": (fine + coarse) / 2, "logr_fine": fine, "logr_coarse": coarse,
            "interp_bias": (r1[0][0] + r2[0][0]) / 2, "overlap_px": min(r1[1], r2[1])}


def solve_scores(n, comps, rng, n_boot=200, key="logr"):
    """Least-squares log-energy score per image from pairwise log ratios, with bootstrap SE."""
    rows = np.zeros((len(comps), n))
    y = np.array([c[key] for c in comps])
    w = np.sqrt(np.array([c["overlap_px"] for c in comps]) / 1e6)
    for k, c in enumerate(comps):
        rows[k, c["a"]], rows[k, c["b"]] = 1, -1

    def solve(idx):
        A = np.vstack([rows[idx] * w[idx, None], np.ones(n) * 10])
        s, *_ = np.linalg.lstsq(A, np.append(y[idx] * w[idx], 0), rcond=None)
        return s - np.median(s)

    s = solve(np.arange(len(comps)))
    resid = rows @ s - y
    # resamples that leave a panel without any comparison say nothing about it: draw again
    boot, tries = [], 0
    while len(boot) < n_boot and tries < 10 * n_boot:
        tries += 1
        idx = rng.integers(0, len(comps), len(comps))
        if len(components(n, [(comps[k]["a"], comps[k]["b"]) for k in idx])) == 1:
            boot.append(solve(idx))
    sd = np.array(boot).std(axis=0) if len(boot) > 1 else np.full(n, np.nan)
    return s, sd, float(np.sqrt(np.mean(resid ** 2)))


def components(n, edges):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


# ---------------------------------------------------------------- tiers and report

def main_group(recs):
    """relative_group of the largest set of panels with a relative score (each group has its own scale)."""
    g = Counter(r["metrics"].get("relative_group") for r in recs
                if not r["reject"] and r["metrics"].get("relative_detail") is not None)
    return g.most_common(1)[0][0] if g else None


def assign_tiers(recs, cuts):
    """Tiers by relative detail within the main comparison group (scores of separate groups are not on one scale);
    every other usable panel gets a Tenengrad tier, flagged by tier_source."""
    p10, p25, p75 = cuts
    ok = [r for r in recs if not r["reject"]]
    mg = main_group(recs)
    rel = [r for r in ok if r["metrics"].get("relative_detail") is not None and r["metrics"].get("relative_group") == mg]
    use_rel = len(rel) >= 0.9 * len(ok) and len(rel) >= 4
    key = "relative_detail" if use_rel else "tenengrad"
    for r in recs:
        r["tier"], r["tier_source"] = None, None

    def tier(group, metric):
        vals = np.array([r["metrics"][metric] for r in group if r["metrics"].get(metric) is not None])
        if len(vals) < 4:
            return
        q10, q25, q75 = np.percentile(vals, [p10, p25, p75])
        for r in group:
            v = r["metrics"].get(metric)
            if v is not None:
                r["tier"] = "Excellent" if v >= q75 else "Good" if v >= q25 else "Marginal" if v >= q10 else "Poor"
                r["tier_source"] = metric
    if use_rel:
        tier(rel, "relative_detail")
        rest = [r for r in ok if r not in rel]
        if rest:                                  # outside the main group: single-image metric, on the whole set
            tier(ok, "tenengrad")
            for r in rel:
                r["tier"], r["tier_source"] = None, None
            tier(rel, "relative_detail")
    else:
        tier(ok, "tenengrad")
    return key


def minutes(r):
    t = r["meta"].get("time")
    if not t:
        return None
    hh, rest = t.split(":")
    return int(hh) * 60 + float(rest)


def write_report(path, man, recs, key, stats):
    ok = [r for r in recs if not r["reject"]]
    fmt = lambda v, d=0: "—" if v is None else f"{v:.{d}f}"
    L = []
    L.append("# Panel quality report\n")
    L.append(f"Created {man['created']} by `panel_classifier.py` · {len(recs)} images · "
             f"mode: {'relative (same-terrain) + single-image' if man['params']['relative'] else 'single-image'} · "
             f"tiers from `{key}`\n")
    labels = Counter(r["meta"].get("label") for r in recs if r["meta"].get("label"))
    if labels:
        L.append(f"Filename label(s): {', '.join(f'`{k}`' for k in labels)} (free text; not interpreted)\n")
    L.append("## Method\n")
    L.append(f"- Subject mask: sky detected when ≥ 5 % of the smoothed frame is below 2 % of full scale; otherwise the whole frame is subject. "
             f"Border trim {man['params']['border']} px; {man['params']['erode']} px removed along a real subject edge; saturated pixels (≥ 98 %) excluded.")
    L.append("- Single-image metrics (0–65535 luminance scale, inside the mask): Tenengrad (Sobel 3×3; the single-image tier metric) and Laplacian variance (3×3). "
             "They compare different content, so treat them as a rough pre-filter.")
    if man["params"]["relative"]:
        L.append("- **Relative detail** (authoritative): overlapping pairs found on 1/{f}× proxies (global feature index, then RANSAC similarity check). "
                 "For each pair, detail energy is compared on the shared terrain at full resolution, brightness-normalised, in both warp directions so interpolation softening cancels, "
                 "and only where the terrain is at about the same distance (±{tol} px) from both frame centres, because detail falls off towards the frame edge. "
                 "Two scales are used and averaged in log space: fine (difference of Gaussians σ 1.0 → 2.5 px) and coarse (σ 2.5 → 6 px), so grain cannot dominate. "
                 "A least-squares solve over all pairs gives one score per image; 1.00 = batch median, 2.00 = twice the detail amplitude. Uncertainty from a bootstrap over pairs.".format(
                     f=stats.get("proxy_factor", "?"), tol=man["params"].get("radius_tol", "?")))
    L.append(f"- Tiers: percentiles {man['params']['tiers']} of `{key}` → Poor / Marginal / Good / Excellent. "
             "Low sharpness never causes a reject; the mosaic builder decides per region.\n")

    L.append("## Summary\n")
    tiers = Counter(r["tier"] for r in ok if r["tier"])
    L.append(f"- Tier counts: " + ", ".join(f"{t}: {tiers.get(t, 0)}" for t in ("Excellent", "Good", "Marginal", "Poor")))
    L.append(f"- Rejected: {sum(r['reject'] for r in recs)}")
    if "pairs" in stats:
        L.append(f"- Overlapping pairs: {stats['pairs']} accepted from {stats['candidates']} candidates; "
                 f"groups: {stats['groups']}; partners per image {stats['deg_min']}–{stats['deg_max']} (median {stats['deg_med']})")
        L.append(f"- Relative detail range {stats['rd_min']:.2f}–{stats['rd_max']:.2f}; typical uncertainty ±{stats['se_med']:.0f} % (worst ±{stats['se_max']:.0f} %); "
                 f"interpolation bias {stats['interp_bias']:+.1f} %")
        L.append(f"- Within-image variation: pairwise ratios deviate from a single per-image score by {stats['resid_pct']:.0f} % RMS. "
                 "Sharpness differs across each frame, so per-region selection matters.")
        if stats.get("rho_single") is not None:
            L.append(f"- Agreement of single-image metrics with relative detail (rank correlation): "
                     f"Laplacian {stats['rho_single']['laplacian_var']:.2f}, Tenengrad {stats['rho_single']['tenengrad']:.2f}")
    times = [(minutes(r), r) for r in ok]
    times = [(t, r) for t, r in times if t is not None]
    if len(times) >= 20 and key == "relative_detail":
        tv = np.array([t for t, _ in times]); sv = np.array([r["metrics"]["relative_detail"] for _, r in times])
        rk = lambda a: np.argsort(np.argsort(a)).astype(float)
        L.append(f"- Trend with capture time: rank correlation {np.corrcoef(rk(tv), rk(sv))[0, 1]:+.2f}\n")
        L.append("**Relative detail by capture time** (median per 10-minute block)\n")
        t0 = int(tv.min() // 10 * 10)
        blocks = [(b, sv[(tv >= b) & (tv < b + 10)]) for b in range(t0, int(tv.max()) + 1, 10)]
        blocks = [(b, v) for b, v in blocks if len(v)]
        L.append("| Block | " + " | ".join(f"{int(b // 60) % 24:02d}:{int(b % 60):02d}" for b, _ in blocks) + " |")
        L.append("|---|" + "---|" * len(blocks))
        L.append("| Images | " + " | ".join(str(len(v)) for _, v in blocks) + " |")
        L.append("| Relative detail | " + " | ".join(f"{np.median(v):.2f}" for _, v in blocks) + " |")
    L.append("")

    L.append("## Flags\n")
    rej = [r for r in recs if r["reject"]]
    L.append(f"**Rejected ({len(rej)})**" + (":" if rej else ": none"))
    for r in rej:
        L.append(f"- `{os.path.basename(r['path'])}`: {r['reject_reason']}")
    order = sorted([r for r in ok if r["metrics"].get(key) is not None], key=lambda r: r["metrics"][key])
    nb = max(1, len(order) // 10)
    L.append(f"\n**Bottom decile by `{key}` ({nb})**, worst first. Not rejected: they still win wherever nothing sharper covers the terrain.")
    for r in order[:nb]:
        L.append(f"- `{r['id']}` {r['meta'].get('time', '')}: {key} {fmt(r['metrics'][key], 2 if key == 'relative_detail' else 0)}, coverage {r['metrics']['coverage_pct']:.1f} %")
    low = [r for r in ok if r["metrics"]["coverage_pct"] < 50]
    if low:
        L.append(f"\n**Low coverage (< 50 %, {len(low)})**: edge/limb frames; not a defect.")
        for r in sorted(low, key=lambda r: r["metrics"]["coverage_pct"]):
            L.append(f"- `{r['id']}`: coverage {r['metrics']['coverage_pct']:.1f} %, partners {r.get('partners', '—')}")
    ungrouped = [r for r in ok if man["params"]["relative"] and r["metrics"].get("relative_detail") is None]
    if ungrouped:
        L.append(f"\n**No relative score ({len(ungrouped)})**: no overlap with the main group.")
        for r in ungrouped:
            L.append(f"- `{r['id']}`")
    L.append("")

    def table(rows, title):
        L.append(f"## {title}\n")
        hdr = "| Rank | ID | Time | AP | Coverage % |"
        if man["params"]["relative"]:
            hdr += " Relative detail | ± | Fine | Coarse |"
        hdr += " Laplacian var | Tenengrad | Tier | Reject | File |"
        L.append(hdr)
        L.append("|" + "---|" * (hdr.count("|") - 1))
        for k, r in enumerate(rows, 1):
            me = r["metrics"]
            line = f"| {k} | `{r['id']}` | {r['meta'].get('time', '—')} | {r['meta'].get('ap_count', '—')} | {fmt(me.get('coverage_pct'), 1)} |"
            if man["params"]["relative"]:
                se = me.get("relative_detail_se")
                line += (f" {fmt(me.get('relative_detail'), 2)} | {'—' if se is None else f'{se * 100:.0f} %'} |"
                         f" {fmt(me.get('relative_detail_fine'), 2)} | {fmt(me.get('relative_detail_coarse'), 2)} |")
            line += f" {fmt(me.get('laplacian_var'))} | {fmt(me.get('tenengrad'))} | {r['tier'] or '—'} | {r['reject_reason'] or ''} | `{os.path.basename(r['path'])}` |"
            L.append(line)
        L.append("")

    ranked = sorted(recs, key=lambda r: (r["reject"], r["tier_source"] != key,
                                         -(r["metrics"].get(r["tier_source"] or key) or -1e30)))
    table(ranked, f"Ranked by `{key}` (best → worst)")
    if any(minutes(r) is not None for r in recs):
        table(sorted(recs, key=lambda r: (r["meta"].get("date", ""), minutes(r) or 0)), "Chronological order (as captured)")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=mc.HelpFormatter)
    ap.add_argument("inputs", nargs="+", help="image files and/or folders")
    ap.add_argument("-o", "--output", required=True, help="manifest JSON path (report written next to it as .md)")
    ap.add_argument("--recursive", action="store_true", help="also scan subfolders")
    ap.add_argument("--ext", default=",".join(mc.DEFAULT_EXTS), help="file extensions to include, comma-separated")
    ap.add_argument("--border", type=int, default=ts.get("MOSAIC_CLASSIFIER_BORDER"), help="px trimmed from every edge")
    ap.add_argument("--erode", type=int, default=ts.get("MOSAIC_CLASSIFIER_ERODE"), help="px removed along a real subject edge")
    ap.add_argument("--tiers", default=ts.get("MOSAIC_CLASSIFIER_TIERS"), help="percentile cut points")
    ap.add_argument("--relative", action="store_true", help="also rank overlapping images on shared terrain")
    ap.add_argument("--radius-tol", type=int, default=ts.get("MOSAIC_CLASSIFIER_RADIUS_TOL"),
                    help="px: compare only pixels at about equal distance from both frame centres")
    ap.add_argument("--workers", type=int, default=ts.get("MOSAIC_CLASSIFIER_WORKERS"), help="parallel worker threads")
    ap.add_argument("--log", help="append the console log to this file")
    args = ap.parse_args()
    cuts = [float(v) for v in args.tiers.split(",")]
    exts = [e.strip() for e in args.ext.split(",") if e.strip()]

    files = mc.find_images(args.inputs, args.recursive, exts)
    if not files:
        sys.exit("no images found")
    if args.border < 0:
        sys.exit("--border cannot be negative")
    mc.check_outputs([args.output, os.path.splitext(os.path.abspath(args.output))[0] + ".md", args.log], files)
    pr = mc.Progress(4 if args.relative else 2, args.log)
    print(f"panel_classifier  {len(files)} images · {args.workers} workers · "
          f"{'relative + single-image' if args.relative else 'single-image'}", flush=True)
    if args.relative:
        est = sum(os.path.getsize(p) for p in files) / 3 * 1.1 / 1e9   # float16 luminance ≈ 1/3 of 16-bit RGB
        pr_ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        if est > 0.6 * pr_ram:
            print(f"      WARN relative mode keeps luminance in RAM: ~{est:.1f} GB of {pr_ram:.0f} GB", flush=True)

    # stage 1
    pr.header("Measure images")
    recs, data = [None] * len(files), [None] * len(files)
    with ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(measure, p, args, args.relative) for p in files]
        for k, fu in enumerate(futs):
            recs[k], data[k] = fu.result()
            pr.tick(k + 1, len(files), f"{recs[k]['id']}  lapvar {recs[k]['metrics'].get('laplacian_var') or 0:.0f}")
    ids = Counter(r["id"] for r in recs)
    for r in recs:        # ids must be unique; fall back to file stem on clashes
        if ids[r["id"]] > 1:
            r["id"] = os.path.splitext(os.path.basename(r["path"]))[0]
    for r in recs:
        me = r["metrics"]
        if r["reject"]:
            continue
        if me["coverage_pct"] < 5:
            r.update(reject=True, reject_reason="coverage < 5 %")
        elif me["saturated_pct"] > 2:
            r.update(reject=True, reject_reason="saturation > 2 %")
    noise = [r["metrics"]["noise_sigma"] for r in recs if not r["reject"] and r["metrics"].get("noise_sigma")]
    if len(noise) >= 5:
        med = float(np.median(noise))
        for r in recs:
            ns = r["metrics"].get("noise_sigma")
            if not r["reject"] and ns and ns > 3 * med:
                r.update(reject=True, reject_reason=f"noise {ns:.0f} > 3× median {med:.0f}")
    sky_n = sum(1 for r in recs if r.get("sky_mode"))
    pr.info(f"{sum(not r['reject'] for r in recs)} measured · {sum(r['reject'] for r in recs)} rejected · sky found in {sky_n}")

    stats = {}
    pairs_out = []
    if args.relative:
        live = [k for k, r in enumerate(recs) if not r["reject"] and data[k] is not None]
        if len(live) < 2:
            sys.exit(f"--relative needs at least 2 usable images ({len(live)} left after rejection)")
        f = mc.proxy_factor([(recs[k]["height"], recs[k]["width"]) for k in live])
        stats["proxy_factor"] = f

        # stage 2
        pr.header(f"Pair discovery (proxy 1/{f}×)")
        feats, prox = {}, {}

        def feat(k):
            P8, pm = mc.make_proxy(data[k]["L"].astype(np.float32), data[k]["m"], f)
            return k, mc.detect_features(P8, pm), pm
        with ThreadPoolExecutor(args.workers) as ex:
            for n_done, (k, fe, pm) in enumerate(ex.map(feat, live), 1):
                feats[k], prox[k] = fe, pm
                pr.tick(n_done, len(live), "features")
        nkp = [len(feats[k][0]) for k in live]
        pr.info(f"keypoints per image {min(nkp)}–{max(nkp)} (median {int(np.median(nkp))})")
        loc = {k: j for j, k in enumerate(live)}
        cand = mc.candidate_pairs([feats[k][1] for k in live])
        cand = [(live[a], live[b]) for a, b in cand]
        pr.info(f"{len(cand)} candidate pairs from the global feature index")
        reasons, accepted = Counter(), []

        def ver(ab):
            a, b = ab
            return ab, mc.verify_pair(feats[a], feats[b], prox[a], prox[b])
        with ThreadPoolExecutor(args.workers) as ex:
            for n_done, ((a, b), (res, why)) in enumerate(ex.map(ver, cand), 1):
                if res:
                    accepted.append(dict(a=a, b=b, **res))
                else:
                    reasons[why] += 1
                pr.tick(n_done, len(cand), f"{len(accepted)} accepted")
        pr.info(f"accepted {len(accepted)} · rejected " + " · ".join(f"{v} {k}" for k, v in reasons.most_common()))
        groups = components(len(recs), [(p["a"], p["b"]) for p in accepted])
        groups = [g for g in groups if all(k in loc for k in g)]
        big = [g for g in groups if len(g) > 1]
        singles = [g[0] for g in groups if len(g) == 1]
        pr.info(f"overlap groups: {[len(g) for g in big]}" + (f" · isolated: {', '.join(recs[k]['id'] for k in singles)}" if singles else ""))
        deg = Counter([p["a"] for p in accepted] + [p["b"] for p in accepted])
        for k in live:
            recs[k]["partners"] = deg.get(k, 0)

        # stage 3
        pr.header("Same-terrain comparison (full resolution)")
        min_px = int(min(200000, 0.02 * np.median([recs[k]["width"] * recs[k]["height"] for k in live])))
        comps = []
        with ThreadPoolExecutor(args.workers) as ex:
            futs = [(p, ex.submit(compare_pair, p["a"], p["b"], p, data, prox, f, min_px, args.radius_tol)) for p in accepted]
            for n_done, (p, fu) in enumerate(futs, 1):
                c = fu.result()
                if c:
                    comps.append(dict(a=p["a"], b=p["b"], **c))
                pr.tick(n_done, len(futs), f"{len(comps)} usable")
        rng = np.random.default_rng(0)
        resid_all, se_all, bias = [], [], []
        # a group was connected by feature matches; after the same-terrain comparisons it may fall apart. Only
        # panels linked by usable comparisons share a scale, so each connected part is scored on its own.
        solved = []
        for g0 in big:
            usable = [c for c in comps if c["a"] in g0 and c["b"] in g0]
            parts = [[g0[j] for j in part] for part in components(len(g0), [(g0.index(c["a"]), g0.index(c["b"])) for c in usable])]
            left = [k for part in parts if len(part) == 1 for k in part]
            if left:
                pr.warn(f"group of {len(g0)}: no usable comparison for {', '.join(recs[k]['id'] for k in left)} (no relative score)")
            if sum(len(part) > 1 for part in parts) > 1:
                pr.warn(f"group of {len(g0)} splits into {sum(len(part) > 1 for part in parts)} parts after the comparisons; "
                        f"each is scored separately (see relative_group)")
            solved += [part for part in parts if len(part) > 1]
        for gid, g in enumerate(solved):
            gi = {k: j for j, k in enumerate(g)}
            gc = [dict(c, a=gi[c["a"]], b=gi[c["b"]]) for c in comps if c["a"] in gi and c["b"] in gi]
            s, sd, rms = solve_scores(len(g), gc, rng)
            s_f, _, _ = solve_scores(len(g), gc, rng, n_boot=1, key="logr_fine")
            s_c, _, _ = solve_scores(len(g), gc, rng, n_boot=1, key="logr_coarse")
            resid_all.append(rms)
            for k, j in gi.items():
                recs[k]["metrics"]["relative_detail"] = round(float(np.exp(s[j] / 2)), 4)
                recs[k]["metrics"]["relative_detail_se"] = round(float(np.exp(sd[j] / 2) - 1), 4)
                recs[k]["metrics"]["relative_detail_fine"] = round(float(np.exp(s_f[j] / 2)), 4)
                recs[k]["metrics"]["relative_detail_coarse"] = round(float(np.exp(s_c[j] / 2)), 4)
                recs[k]["metrics"]["relative_group"] = gid
                se_all.append(float(np.exp(sd[j] / 2) - 1))
            bias += [c["interp_bias"] for c in gc]
        for r in recs:
            r["metrics"].setdefault("relative_detail", None)
            r["metrics"].setdefault("relative_detail_se", None)
            r["metrics"].setdefault("relative_detail_fine", None)
            r["metrics"].setdefault("relative_detail_coarse", None)
        rd = [r["metrics"]["relative_detail"] for r in recs if r["metrics"].get("relative_detail")]
        if rd:
            stats.update(pairs=len(accepted), candidates=len(cand), groups=[len(g) for g in solved] or [0],
                         deg_min=min(deg.get(k, 0) for k in live), deg_max=max(deg.get(k, 0) for k in live),
                         deg_med=int(np.median([deg.get(k, 0) for k in live])),
                         rd_min=min(rd), rd_max=max(rd), se_med=np.median(se_all) * 100, se_max=max(se_all) * 100,
                         interp_bias=float(np.median(bias)) * 100 if bias else 0.0,
                         resid_pct=(np.exp(np.mean(resid_all) / 2) - 1) * 100 if resid_all else 0.0)
            both = [r for r in recs if r["metrics"].get("relative_detail") and r["metrics"].get("laplacian_var")]
            rk = lambda a: np.argsort(np.argsort(a)).astype(float)
            if len(both) >= 5:
                ref = [r["metrics"]["relative_detail"] for r in both]
                stats["rho_single"] = {m: float(np.corrcoef(rk([r["metrics"][m] for r in both]), rk(ref))[0, 1])
                                       for m in ("laplacian_var", "tenengrad")}
            pr.info(f"{len(comps)} comparisons · relative detail {min(rd):.2f}–{max(rd):.2f} · "
                    f"uncertainty ±{stats['se_med']:.0f} % typical · within-image variation {stats['resid_pct']:.0f} % RMS")
        for p in accepted:
            pairs_out.append({"a": recs[p["a"]]["id"], "b": recs[p["b"]]["id"], "inliers": p["inliers"],
                              "scale": round(p["scale"], 6), "rot_deg": round(p["rot_deg"], 4),
                              "T_full": np.round(mc.proxy_to_full(p["T"], f), 6).tolist()})

    # final stage: tiers + write
    pr.header("Write manifest and report")
    key = assign_tiers(recs, cuts)
    man = {"schema": SCHEMA, "created": dt.datetime.now().isoformat(timespec="seconds"),
           "params": {"border": args.border, "erode": args.erode, "tiers": cuts, "relative": args.relative,
                      "radius_tol": args.radius_tol,
                      "tier_metric": key},
           "images": [{k: r.get(k) for k in ("path", "id", "width", "height", "channels", "dtype", "metrics",
                                             "tier", "tier_source", "reject", "reject_reason", "meta", "partners")}
                      for r in recs]}
    if args.relative:
        man["pairs"] = pairs_out
    out = os.path.abspath(args.output)
    tmp = os.path.join(os.path.dirname(out), "." + os.path.basename(out) + ".partial")
    with open(tmp, "w", encoding="utf-8") as fh:            # never a half-written manifest
        json.dump(man, fh, indent=1)
    os.replace(tmp, out)
    rep = os.path.splitext(out)[0] + ".md"
    write_report(rep, man, recs, key, stats)
    pr.info(f"{out}")
    pr.info(f"{rep}")
    tiers = Counter(r["tier"] for r in recs if r["tier"])
    print(f"Done in {pr.elapsed()} · " + " · ".join(f"{t} {tiers.get(t, 0)}" for t in ("Excellent", "Good", "Marginal", "Poor"))
          + f" · rejected {sum(r['reject'] for r in recs)}", flush=True)


if __name__ == "__main__":
    main()
