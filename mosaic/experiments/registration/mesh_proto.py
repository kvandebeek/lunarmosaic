"""Prototype: poly3 (builder) + per-panel bilinear displacement mesh, fitted on dense tiles; held-out evaluation."""
import sys, time, pickle, numpy as np, cv2
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, '.')
from harness import capture
import mosaic_stages as ms

def dense_tiles(C, theta, idx, tile=128, max_tiles=600):
    model = C.model; win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
    def job(ab):
        a, b = ab; ta, tb = theta[idx[a]], theta[idx[b]]
        mp = lambda P: ms.from_canvas(model, tb, C.sizes[b], ms.to_canvas(model, ta, C.sizes[a], P))
        return ab, ms.tiles_for_pair(a, b, mp, C.sizes, C.L, C.M, C.tex4, C.f, win, tile, max_tiles, resp_min=0.15, max_shift=4.0)
    with ThreadPoolExecutor(9) as pool:
        return dict(pool.map(job, C.pair_keys))

class Mesh:
    def __init__(self, nodes, sizes, s):
        self.s, self.base, self.shape = s, {}, {}
        k = 0
        for n in nodes:
            w, h = sizes[n]; nx, ny = int(np.ceil((w - 1) / s)) + 1, int(np.ceil((h - 1) / s)) + 1
            self.base[n], self.shape[n] = k, (ny, nx); k += nx * ny
        self.N = k
    def weights(self, n, p):
        """4 node indices and bilinear weights for panel-pixel points p (N×2)."""
        ny, nx = self.shape[n]; u, v = p[:, 0] / self.s, p[:, 1] / self.s
        i, j = np.clip(np.floor(u).astype(int), 0, nx - 2), np.clip(np.floor(v).astype(int), 0, ny - 2)
        fu, fv = u - i, v - j
        cols = self.base[n] + np.stack([j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1], 1)
        vals = np.stack([(1 - fu) * (1 - fv), fu * (1 - fv), (1 - fu) * fv, fu * fv], 1)
        return cols, vals
    def smooth_rows(self):
        r = []
        for n, (ny, nx) in self.shape.items():
            g = self.base[n] + np.arange(ny * nx).reshape(ny, nx)
            r += list(zip(g[:, :-1].ravel(), g[:, 1:].ravel())) + list(zip(g[:-1].ravel(), g[1:].ravel()))
        return np.array(r)

def fit_mesh(mesh, obs, lam_s=4.0, lam_0=0.05, iters=400, huber=1.0, irls=3):
    """obs: list of (cols_a, vals_a, cols_b, vals_b, c (N×2 current canvas misfit a−b), w). Returns D (N_nodes×2)."""
    ca = np.vstack([o[0] for o in obs]); va = np.vstack([o[1] for o in obs])
    cb = np.vstack([o[2] for o in obs]); vb = np.vstack([o[3] for o in obs])
    c = np.vstack([o[4] for o in obs]); w0 = np.concatenate([o[5] for o in obs])
    cols = np.hstack([ca, cb]); vals = np.hstack([va, -vb])            # residual = c + A D
    sm = mesh.smooth_rows(); N = mesh.N
    D = np.zeros((N, 2)); w = w0.copy()
    Ax = lambda x: (vals * x[cols]).sum(1)
    ATy = lambda y: np.bincount(cols.ravel(), (vals * y[:, None]).ravel(), minlength=N)
    def Sx(x):                                                         # smoothness normal operator
        d = x[sm[:, 0]] - x[sm[:, 1]]
        return np.bincount(sm[:, 0], d, minlength=N) - np.bincount(sm[:, 1], d, minlength=N)
    for _ in range(irls):
        for k in range(2):
            op = lambda x: ATy(w * Ax(x)) + lam_s * Sx(x) + lam_0 * x
            rhs = -ATy(w * c[:, k]); x = D[:, k].copy(); r = rhs - op(x); p = r.copy(); rs = r @ r
            for _ in range(iters):
                Ap = op(p); a = rs / (p @ Ap); x += a * p; r -= a * Ap; rn = r @ r
                if rn < 1e-10 * (rhs @ rhs + 1e-12): break
                p = r + rn / rs * p; rs = rn
            D[:, k] = x
        e = np.hypot(c[:, 0] + Ax(D[:, 0]), c[:, 1] + Ax(D[:, 1]))
        w = w0 * np.where(e <= huber, 1.0, huber / np.maximum(e, 1e-9))
    return D

def build_obs(C, theta, idx, mesh, tiles, sel):
    model = C.model; obs = []
    for (a, b), tl in tiles.items():
        t = [x for x, keep in zip(tl, sel[(a, b)]) if keep]
        if not t: continue
        pa = np.array([x[0] for x in t]); pb = np.array([x[1] for x in t])
        Xa = ms.to_canvas(model, theta[idx[a]], C.sizes[a], pa); Xb = ms.to_canvas(model, theta[idx[b]], C.sizes[b], pb)
        c_a, v_a = mesh.weights(a, pa); c_b, v_b = mesh.weights(b, pb)
        obs.append((c_a, v_a, c_b, v_b, Xa - Xb, np.array([x[2] for x in t]) ** 0))
    return obs

def residuals(C, theta, idx, mesh, D, pts):
    """pts: list of (a, b, pa, pb) → canvas misfit magnitude with poly3 + mesh."""
    out = []
    for a, b, pa, pb in pts:
        Xa = ms.to_canvas(C.model, theta[idx[a]], C.sizes[a], pa[None]); Xb = ms.to_canvas(C.model, theta[idx[b]], C.sizes[b], pb[None])
        if D is not None:
            ca, va = mesh.weights(a, pa[None]); cb, vb = mesh.weights(b, pb[None])
            Xa = Xa + (va[..., None] * D[ca]).sum(1); Xb = Xb + (vb[..., None] * D[cb]).sum(1)
        out.append(float(np.hypot(*(Xa - Xb)[0])))
    return np.array(out)

def stats(e, tag):
    print(f'  {tag:34s} n={len(e):6d}  RMS {np.sqrt((e**2).mean()):.3f}  median {np.median(e):.3f}  p90 {np.percentile(e,90):.3f}  '
          f'p99 {np.percentile(e,99):.3f}  >1 px {(e>1).mean()*100:5.1f} %  >2 px {(e>2).mean()*100:4.1f} %', flush=True)

if __name__ == '__main__':
    t0 = time.time(); C = capture()
    theta, idx, st = ms.fine_align(C.nodes, C.pair_keys, C.sizes, C.L, C.M, C.tex4, C.f, C.theta_sim, C.idx_sim, C.ref, C.model, C.coarse, C.pool)
    print(f'poly3 fine alignment ready ({time.time()-t0:.0f} s), in-sample RMS {st["rms"]:.3f}', flush=True)
    t1 = time.time(); tiles = dense_tiles(C, theta, idx)
    ntile = sum(len(v) for v in tiles.values()); print(f'dense 128 px tiles: {ntile} on {sum(1 for v in tiles.values() if v)} pairs in {time.time()-t1:.0f} s', flush=True)
    # checkerboard split on the 96 px tile grid: train on one colour, test on the other
    sel_train = {k: [((int(p[0][0]) // 96 + int(p[0][1]) // 96) % 2 == 0) for p in v] for k, v in tiles.items()}
    sel_test = {k: [not s for s in v] for k, v in sel_train.items()}
    test = [(a, b, p[0], p[1]) for (a, b), v in tiles.items() for p, s in zip(v, sel_test[(a, b)]) if s]
    stats(residuals(C, theta, idx, None, None, test), 'held-out tiles · poly3 (builder)')
    for s in (384, 256, 192):
        for lam_s in (1.0, 4.0, 16.0):
            mesh = Mesh(C.nodes, C.sizes, s); t2 = time.time()
            D = fit_mesh(mesh, build_obs(C, theta, idx, mesh, tiles, sel_train), lam_s=lam_s)
            stats(residuals(C, theta, idx, mesh, D, test), f'held-out · mesh {s} px, smooth {lam_s:g} ({time.time()-t2:.0f} s)')
    pickle.dump(dict(theta=theta, idx=idx, tiles=tiles), open('mesh_inputs.pkl', 'wb'))
