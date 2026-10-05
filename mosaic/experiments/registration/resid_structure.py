"""Held-out tile residuals of the builder's own fine alignment, and how they are structured."""
import sys, time, pickle, numpy as np, cv2
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, '.')
from harness import capture
import mosaic_stages as ms
C = model = win = None


def measure(theta, idx, offset_frac, max_tiles, tile=256):
    """Tiles on a grid shifted by offset_frac of the step (0 = the builder's own grid)."""
    def job(ab):
        a, b = ab; ta, tb = theta[idx[a]], theta[idx[b]]
        mp = lambda pts: ms.from_canvas(model, tb, C.sizes[b], ms.to_canvas(model, ta, C.sizes[a], pts))
        wa, ha = C.sizes[a]; step = int(tile * 0.75)
        off = int(step * offset_frac)
        # panel a is handed in cropped by off, so tiles_for_pair lays out its own grid -- same half-tile
        # margins and 0.75-tile step as the builder used -- over the shifted origin
        out = ms.tiles_for_pair(a, b, lambda p: mp(p + off), {a: (wa - off, ha - off), b: C.sizes[b]},
                                {a: C.L[a][off:, off:], b: C.L[b]}, {a: C.M[a][off:, off:], b: C.M[b]},
                                {a: C.tex4[a][off // C.f:, off // C.f:]}, C.f, win, tile, max_tiles)
        return ab, [(pa + off, pb, r) for pa, pb, r in out]
    with ThreadPoolExecutor(9) as pool:
        return dict(pool.map(job, C.pair_keys))


def main():
    global C, model, win
    t0 = time.time(); C = capture(); print(f'captured stages 0-3 in {time.time()-t0:.0f} s', flush=True)
    model, win = C.model, cv2.createHanningWindow((256, 256), cv2.CV_32F)
    theta, idx, st = ms.fine_align(C.nodes, C.pair_keys, C.sizes, C.L, C.M, C.tex4, C.f, C.theta_sim, C.idx_sim, C.ref,
                                   model, C.coarse, C.pool, log=print)
    print('builder fine alignment in-sample RMS', round(st['rms'], 3), flush=True)
    test = measure(theta, idx, 0.5, 24)
    res = []
    for (a, b), tl in test.items():
        if len(tl) < 1: continue
        xa = ms.centred(np.array([t[0] for t in tl]), C.sizes[a]); xb = ms.centred(np.array([t[1] for t in tl]), C.sizes[b])
        X = model.apply(theta[idx[a]], xa)
        pb = ms.from_canvas(model, theta[idx[b]], C.sizes[b], X) - [(C.sizes[b][0]-1)/2, (C.sizes[b][1]-1)/2]
        for pa_, v in zip([t[0] for t in tl], pb - xb): res.append((a, b, pa_[0], pa_[1], v[0], v[1]))
    R = np.array([(r[2], r[3], r[4], r[5]) for r in res])
    e = np.hypot(R[:, 2], R[:, 3])
    print(f'held-out tiles {len(e)}: RMS {np.sqrt((e**2).mean()):.3f} px, median {np.median(e):.3f}, p90 {np.percentile(e,90):.3f}')
    # spatial correlation of residual vectors between neighbouring tiles of the same pair
    pairs = {}
    for r in res: pairs.setdefault((r[0], r[1]), []).append(r)
    cor_n, cor_d = [], []
    for k, rr in pairs.items():
        P = np.array([(r[2], r[3]) for r in rr]); V = np.array([(r[4], r[5]) for r in rr])
        for i in range(len(rr)):
            for j in range(i + 1, len(rr)):
                d = np.hypot(*(P[i] - P[j]))
                cor_d.append(d); cor_n.append((V[i] @ V[j]) / (np.linalg.norm(V[i]) * np.linalg.norm(V[j]) + 1e-9))
    cor_d, cor_n = np.array(cor_d), np.array(cor_n)
    for lo, hi in [(0, 250), (250, 500), (500, 1000), (1000, 4000)]:
        s = (cor_d >= lo) & (cor_d < hi)
        print(f'  tiles {lo}-{hi} px apart: mean cosine of residual directions {cor_n[s].mean():+.2f} (n={s.sum()})')
    pickle.dump(dict(res=res, theta=theta, idx=idx), open('resid.pkl', 'wb'))


if __name__ == '__main__':
    main()
