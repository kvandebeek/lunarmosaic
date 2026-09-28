"""Stages 4–8 of mosaic_builder.py: fine alignment, photometry, region selection, blending, finishing.

All coordinates: a panel pose maps panel-centred full-resolution pixel coordinates to canvas pixels.
P[n] (3×3) maps a panel's full-resolution pixel (x, y) to canvas pixels (offset included).
"""
import os

import cv2
import numpy as np

import mosaic_common as mc


# ================================================================ geometry models

class Model:
    """Pose models, linear in their parameters (so all panels can be solved in one least-squares system)."""

    def __init__(self, name, sigma_lin_px=6.0, ref_size=(3768, 2104)):
        self.name = name
        self.d = 4 if name == "similarity" else 6
        self.ident = np.array([1.0, 0, 0, 0] if name == "similarity" else [1.0, 0, 0, 0, 1, 0])
        rx, ry = ref_size[0] / np.sqrt(12), ref_size[1] / np.sqrt(12)        # RMS of centred coordinates
        wl = lambda r: (r / sigma_lin_px) ** 2
        self.prior_w = np.zeros(self.d)
        lin = [0, 1] if name == "similarity" else [0, 1, 3, 4]
        for j in lin:
            self.prior_w[j] = wl(np.hypot(rx, ry) / np.sqrt(2)) if name == "similarity" else wl(rx if j in (0, 3) else ry)
        self.prior_idx = lin

    def J(self, p):
        n = len(p)
        J = np.zeros((n, 2, self.d))
        if self.name == "similarity":           # x' = a x − b y + tx ; y' = b x + a y + ty
            J[:, 0, 0], J[:, 0, 1], J[:, 0, 2] = p[:, 0], -p[:, 1], 1
            J[:, 1, 0], J[:, 1, 1], J[:, 1, 3] = p[:, 1], p[:, 0], 1
        else:                                   # x' = a11 x + a12 y + tx ; y' = a21 x + a22 y + ty
            J[:, 0, 0], J[:, 0, 1], J[:, 0, 2] = p[:, 0], p[:, 1], 1
            J[:, 1, 3], J[:, 1, 4], J[:, 1, 5] = p[:, 0], p[:, 1], 1
        return J

    def mat(self, th):
        if self.name == "similarity":
            a, b, tx, ty = th
            return np.array([[a, -b, tx], [b, a, ty]])
        return np.array([[th[0], th[1], th[2]], [th[3], th[4], th[5]]])

    def apply(self, th, p):
        M = self.mat(th)
        return p @ M[:, :2].T + M[:, 2]

    def from_similarity(self, th4):
        if self.name == "similarity":
            return np.array(th4, float)
        a, b, tx, ty = th4
        return np.array([a, -b, tx, b, a, ty], float)

    def scale_rot(self, th):
        M = self.mat(th)[:, :2]
        return float(np.sqrt(abs(np.linalg.det(M)))), float(np.degrees(np.arctan2(M[1, 0] - M[0, 1], M[0, 0] + M[1, 1])))


class PolyModel:
    """Per-panel cubic warp: x' = R·Σ a_ij u^i v^j, y' = R·Σ b_ij u^i v^j (i + j ≤ order), u, v = centred px / R.
    Still linear in its parameters, so all panels are solved together. Non-linear coefficients are regularised
    towards zero (towards the affine solution) with strength lam. Measured on the 2026-09-25 set (held-out tiles):
    similarity 1.19 px RMS, affine 0.97, cubic 0.55 (median 0.25)."""
    EXP = {2: [(0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2)],
           3: [(0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2), (3, 0), (2, 1), (1, 2), (0, 3)]}

    def __init__(self, order=3, sigma_px=1.0, sigma_lin_px=6.0, R=2150.0, ref_size=(3768, 2104)):
        self.name = f"poly{order}"
        self.e = self.EXP[order]
        self.k = len(self.e)
        self.d = 2 * self.k
        self.R = R
        self.ident = np.zeros(self.d)
        self.ident[1] = 1
        self.ident[self.k + 2] = 1
        nonlin = [i for i, (a, b) in enumerate(self.e) if a + b >= 2]
        self.prior_idx = nonlin + [self.k + i for i in nonlin]
        # prior in pixel units: RMS displacement of each non-linear basis term over a typical frame, so that
        # 1 px of bending costs as much as a 1 px error at one tile (sigma_px sets that exchange rate)
        w, h = ref_size
        g = np.stack(np.meshgrid(np.linspace(-w / 2, w / 2, 33), np.linspace(-h / 2, h / 2, 19)), -1).reshape(-1, 2)
        rms = np.sqrt((self.phi(g) ** 2).mean(axis=0))
        self.prior_w = np.zeros(self.d)
        for i in nonlin:
            self.prior_w[i] = self.prior_w[self.k + i] = (self.R * rms[i] / sigma_px) ** 2
        # linear part (scale/rotation/shear) held near the coarse solve: same optics for every panel. Without this,
        # panels far from the reference can shrink the canvas and make residuals look smaller than they are.
        for i in (1, 2):
            self.prior_w[i] = self.prior_w[self.k + i] = (self.R * rms[i] / sigma_lin_px) ** 2

    def phi(self, p):
        u = np.asarray(p, float) / self.R
        return np.stack([u[:, 0] ** i * u[:, 1] ** j for i, j in self.e], 1)

    def J(self, p):
        F = self.phi(p) * self.R
        J = np.zeros((len(p), 2, self.d))
        J[:, 0, :self.k] = F
        J[:, 1, self.k:] = F
        return J

    def apply(self, th, p):
        F = self.phi(p)
        return self.R * np.stack([F @ th[:self.k], F @ th[self.k:]], 1)

    def from_similarity(self, th4):
        a, b, tx, ty = th4
        th = np.zeros(self.d)
        th[0], th[1], th[2] = tx / self.R, a, -b
        th[self.k], th[self.k + 1], th[self.k + 2] = ty / self.R, b, a
        return th

    def mat(self, th, size=(3768, 2104)):
        """Best-fit affine approximation over the panel (for stages that do not need sub-pixel geometry)."""
        w, h = size
        g = np.stack(np.meshgrid(np.linspace(-(w - 1) / 2, (w - 1) / 2, 9), np.linspace(-(h - 1) / 2, (h - 1) / 2, 7)), -1).reshape(-1, 2)
        Y = self.apply(th, g)
        A = np.hstack([g, np.ones((len(g), 1))])
        sol, *_ = np.linalg.lstsq(A, Y, rcond=None)
        return sol.T

    def scale_rot(self, th, size=(3768, 2104)):
        M = self.mat(th, size)[:, :2]
        return float(np.sqrt(abs(np.linalg.det(M)))), float(np.degrees(np.arctan2(M[1, 0] - M[0, 1], M[0, 0] + M[1, 1])))


def mesh_shape(size, spacing):
    w, h = size
    return int(np.ceil((w - 1) / spacing)) + 1, int(np.ceil((h - 1) / spacing)) + 1


def mesh_weights(p, spacing, nx, ny):
    """Bilinear weights of panel pixels p (N×2, not centred) on a node grid with the given spacing:
    4 node indices (row-major, local to the panel) and their weights."""
    u, v = p[:, 0] / spacing, p[:, 1] / spacing
    i, j = np.clip(np.floor(u).astype(int), 0, nx - 2), np.clip(np.floor(v).astype(int), 0, ny - 2)
    fu, fv = u - i, v - j
    cols = np.stack([j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1], 1)
    vals = np.stack([(1 - fu) * (1 - fv), fu * (1 - fv), (1 - fu) * fv, fu * fv], 1)
    return cols, vals


class MeshModel(PolyModel):
    """poly3 plus a smooth per-panel displacement mesh (canvas px at nodes `spacing` px apart, bilinear between).
    A pose row is [poly3 parameters, nx, ny, w, h, mesh dx/dy per node]; rows are zero-padded to one length.
    Not solved by solve_poses (see refine_mesh); everything that only applies a pose works unchanged.
    Measured on the 2026-09-25 set: seam misfit median 0.42 -> 0.27 px, > 1 px 12.3 % -> 6.5 %."""

    def __init__(self, poly, spacing=256):
        self.__dict__.update(poly.__dict__)
        self.name = poly.name + "+mesh"
        self.spacing = spacing

    def split(self, th):
        th = np.asarray(th, float)
        if len(th) <= self.d + 4:
            return th[:self.d], None
        nx, ny, w, h = (int(round(v)) for v in th[self.d:self.d + 4])
        D = th[self.d + 4:self.d + 4 + 2 * nx * ny].reshape(nx * ny, 2)
        return th[:self.d], (nx, ny, w, h, D)

    def pack(self, base, size, D):
        nx, ny = mesh_shape(size, self.spacing)
        return np.concatenate([base, [nx, ny, size[0], size[1]], np.asarray(D, float).ravel()])

    def apply(self, th, p):
        base, mesh = self.split(th)
        X = PolyModel.apply(self, base, p)
        if mesh is None:
            return X
        nx, ny, w, h, D = mesh
        cols, vals = mesh_weights(np.asarray(p, float) + [(w - 1) / 2, (h - 1) / 2], self.spacing, nx, ny)
        return X + (vals[..., None] * D[cols]).sum(1)


def reorient(model, theta, M):
    """Pose rows after a linear map M (2 × 2: rotation, optionally with a mirror) of the canvas, exactly: every model
    here is linear in canvas coordinates, so the x and y parameter sets (and mesh displacements) mix through M."""
    M = np.asarray(M, float)
    out = []
    for th in np.asarray(theta, float):
        th = th.copy()
        if isinstance(model, PolyModel):
            k = model.k
            a, b = th[:k].copy(), th[k:2 * k].copy()
            th[:k], th[k:2 * k] = M[0, 0] * a + M[0, 1] * b, M[1, 0] * a + M[1, 1] * b
            if isinstance(model, MeshModel) and len(th) > model.d + 4:
                nx, ny = int(round(th[model.d])), int(round(th[model.d + 1]))
                D = th[model.d + 4:model.d + 4 + 2 * nx * ny].reshape(-1, 2)
                th[model.d + 4:model.d + 4 + 2 * nx * ny] = (D @ M.T).ravel()
        else:
            A = np.vstack([model.mat(th), [0, 0, 1]])
            R = np.eye(3); R[:2, :2] = M
            B = (R @ A)[:2]
            if model.name == "similarity":
                if np.linalg.det(M) < 0:
                    raise ValueError("a similarity pose cannot be mirrored")
                th = np.array([B[0, 0], B[1, 0], B[0, 2], B[1, 2]])
            else:
                th = np.array([B[0, 0], B[0, 1], B[0, 2], B[1, 0], B[1, 1], B[1, 2]])
        out.append(th)
    return np.array(out)


def make_model(name, sizes=None):
    ref = tuple(int(v) for v in np.median(np.array(list(sizes.values())), axis=0)) if sizes else (3768, 2104)
    if name == "poly3+mesh":
        return MeshModel(PolyModel(3, ref_size=ref))
    return PolyModel(3, ref_size=ref) if name == "poly3" else PolyModel(2, ref_size=ref) if name == "poly2" else Model(name, ref_size=ref)


def to_canvas(model, th, size, pts, offset=(0, 0)):
    """Panel full-resolution pixels -> canvas pixels (exact, for any model)."""
    return model.apply(th, centred(pts, size)) - np.asarray(offset, float)


def from_canvas(model, th, size, X, offset=(0, 0), iters=6):
    """Canvas pixels -> panel pixels: invert the pose by fixed-point iteration on its affine approximation."""
    M = model.mat(th, size) if isinstance(model, PolyModel) else model.mat(th)
    Ai = np.linalg.inv(M[:, :2])
    Xc = np.asarray(X, float) + np.asarray(offset, float)
    p = (Xc - M[:, 2]) @ Ai.T
    if isinstance(model, PolyModel):
        for _ in range(iters):
            p = p - (model.apply(th, p) - Xc) @ Ai.T
    w, h = size
    return p + [(w - 1) / 2, (h - 1) / 2]


def centred(p, size):
    w, h = size
    return np.asarray(p, float) - [(w - 1) / 2, (h - 1) / 2]


def panel_matrix(model, th, size, offset=(0, 0)):
    """3×3: panel full-resolution pixel -> canvas pixel (best-fit affine for a polynomial model)."""
    w, h = size
    C = np.array([[1, 0, -(w - 1) / 2], [0, 1, -(h - 1) / 2], [0, 0, 1.0]])
    M = np.vstack([model.mat(th, size) if isinstance(model, PolyModel) else model.mat(th), [0, 0, 1]])
    O = np.array([[1, 0, -offset[0]], [0, 1, -offset[1]], [0, 0, 1.0]])
    return O @ M @ C


def solve_poses(nodes, pairs, ref, model, huber=1.0, iters=10, force_affine=(), target=None):
    """Pose per node from correspondences {a, b, xa, xb, w}; reference fixed by elimination; IRLS (Huber).
    Priors (model.prior_w) pull parameters towards target (N×d; default: identity), e.g. the linear part towards
    the coarse solve and bending towards zero. force_affine: panels whose non-linear terms are pinned to zero."""
    idx = {n: k for k, n in enumerate(nodes)}
    N, d = len(nodes), model.d
    theta = np.tile(model.ident, (N, 1)).astype(float)
    free = [k for k in range(N) if k != idx[ref]]
    col = {k: j for j, k in enumerate(free)}
    for p in pairs:
        p.setdefault("Ja", model.J(p["xa"]))
        p.setdefault("Jb", model.J(p["xb"]))
        p.setdefault("w", np.ones(len(p["xa"])))
    hub = [np.ones(len(p["xa"])) for p in pairs]
    for it in range(iters):
        H = np.zeros((d * (N - 1), d * (N - 1)))
        g = np.zeros(d * (N - 1))
        for p, hw in zip(pairs, hub):
            ia, ib = idx[p["a"]], idx[p["b"]]
            w = p["w"] * hw
            blocks = {(ia, ia): np.einsum("nki,nkj,n->ij", p["Ja"], p["Ja"], w),
                      (ib, ib): np.einsum("nki,nkj,n->ij", p["Jb"], p["Jb"], w),
                      (ia, ib): -np.einsum("nki,nkj,n->ij", p["Ja"], p["Jb"], w)}
            blocks[(ib, ia)] = blocks[(ia, ib)].T
            for (r, c), B in blocks.items():
                if r in col and c in col:
                    H[d * col[r]:d * col[r] + d, d * col[c]:d * col[c] + d] += B
                elif r in col:
                    g[d * col[r]:d * col[r] + d] -= B @ theta[c]
        if getattr(model, "prior_w", None) is not None:
            tgt = target if target is not None else np.tile(model.ident, (N, 1))
            nonlin = set(getattr(model, "prior_idx", [])) - {1, 2, getattr(model, "k", 0) + 1, getattr(model, "k", 0) + 2} \
                if isinstance(model, PolyModel) else set()
            for k in free:
                pinned = nodes[k] in force_affine
                for j in range(d):
                    wj = 1e12 if (pinned and j in nonlin) else model.prior_w[j]
                    if wj:
                        H[d * col[k] + j, d * col[k] + j] += wj
                        g[d * col[k] + j] += wj * tgt[k, j]
        sol = np.linalg.solve(H + np.eye(len(H)) * 1e-9, g)
        for k in free:
            theta[k] = sol[d * col[k]:d * col[k] + d]
        new = []
        for p in pairs:
            e = residual(model, theta, idx, p)
            new.append(np.where(e <= huber, 1.0, huber / np.maximum(e, 1e-9)))
        done = it > 0 and max(np.abs(a - b).max() for a, b in zip(new, hub)) < 1e-3
        hub = new
        if done:
            break
    return theta, idx


def unreachable(nodes, pairs, ref):
    """Nodes with no chain of pairs to the reference (their pose would be set by the priors alone)."""
    nb = {n: set() for n in nodes}
    for p in pairs:
        nb[p["a"]].add(p["b"])
        nb[p["b"]].add(p["a"])
    seen, todo = {ref}, [ref]
    while todo:
        for m in nb[todo.pop()] - seen:
            seen.add(m)
            todo.append(m)
    return set(nodes) - seen


def residual_in_panel(model, theta, idx, p, sizes):
    """Residual in panel b's own pixels: a's point mapped into the canvas and back into b (cannot be reduced
    by shrinking the canvas)."""
    a, b = p["a"], p["b"]
    X = model.apply(theta[idx[a]], p["xa"])
    pb = from_canvas(model, theta[idx[b]], sizes[b], X) - [(sizes[b][0] - 1) / 2, (sizes[b][1] - 1) / 2]
    return np.hypot(*(pb - p["xb"]).T)


def residual(model, theta, idx, p):
    r = model.apply(theta[idx[p["a"]]], p["xa"]) - model.apply(theta[idx[p["b"]]], p["xb"])
    return np.hypot(r[:, 0], r[:, 1])


# ================================================================ stage 4: fine alignment

def texture_map(lum4):
    """Where the terrain has texture worth correlating (1/4 scale)."""
    d = cv2.GaussianBlur(lum4, (0, 0), 0.7) - cv2.GaussianBlur(lum4, (0, 0), 2.0)
    return cv2.GaussianBlur(d * d, (0, 0), 4) / np.maximum(cv2.GaussianBlur(lum4, (0, 0), 4) ** 2, 1e-4)


def local_affine(map_ab, p, d=64.0):
    """3×3 affine that matches map_ab around point p (for warping one tile)."""
    q = np.array([p, p + [d, 0], p + [0, d]])
    r = map_ab(q)
    A = np.stack([(r[1] - r[0]) / d, (r[2] - r[0]) / d], 1)
    t = r[0] - A @ p
    return np.vstack([np.hstack([A, t[:, None]]), [0, 0, 1]])


def tiles_for_pair(a, b, map_ab, sizes, L, M, tex4, f, win, tile=256, max_tiles=12, resp_min=0.05, max_shift=6.0):
    """Phase-correlate textured tiles of panel a against panel b warped by the current prediction map_ab
    (a pixel -> b pixel). Returns [(pa, pb, response)] full-resolution pixel correspondences."""
    wa, ha = sizes[a]
    wb, hb = sizes[b]
    half, margin = tile // 2, tile // 2 + 48
    xs = np.arange(margin, wa - margin, int(tile * 0.75))
    ys = np.arange(margin, ha - margin, int(tile * 0.75))
    if len(xs) == 0 or len(ys) == 0:
        return []
    cx, cy = [g.ravel() for g in np.meshgrid(xs, ys)]
    c = np.stack([cx, cy], 1).astype(float)
    cb = map_ab(c)
    ok = (cb[:, 0] > margin) & (cb[:, 0] < wb - margin) & (cb[:, 1] > margin) & (cb[:, 1] < hb - margin)
    for dx, dy in ((0, 0), (-0.4, -0.4), (0.4, -0.4), (-0.4, 0.4), (0.4, 0.4)):
        pa = c + [dx * tile, dy * tile]
        pb = map_ab(pa)
        ia = np.clip(pa.astype(int), 0, [wa - 1, ha - 1])
        ib = np.clip(pb.astype(int), 0, [wb - 1, hb - 1])
        ok &= M[a][ia[:, 1], ia[:, 0]] & M[b][ib[:, 1], ib[:, 0]]
    if not ok.any():
        return []
    c = c[ok]
    t4 = tex4[a]
    score = t4[np.clip((c[:, 1] / f).astype(int), 0, t4.shape[0] - 1), np.clip((c[:, 0] / f).astype(int), 0, t4.shape[1] - 1)]
    out = []
    for k in np.argsort(-score)[:max_tiles]:
        x0, y0 = int(c[k, 0]) - half, int(c[k, 1]) - half
        A = L[a][y0:y0 + tile, x0:x0 + tile].astype(np.float32)
        Tab = local_affine(map_ab, np.array([x0 + half, y0 + half], float))
        cor = np.array([[x0, y0], [x0 + tile, y0], [x0, y0 + tile], [x0 + tile, y0 + tile]], float) @ Tab[:2, :2].T + Tab[:2, 2]
        bx0, by0 = max(0, int(cor[:, 0].min()) - 12), max(0, int(cor[:, 1].min()) - 12)
        bx1, by1 = min(wb, int(cor[:, 0].max()) + 13), min(hb, int(cor[:, 1].max()) + 13)
        Bc = L[b][by0:by1, bx0:bx1].astype(np.float32)
        Mt = np.hstack([Tab[:2, :2], (Tab[:2, :2] @ [x0, y0] + Tab[:2, 2] - [bx0, by0])[:, None]])
        Bw = cv2.warpAffine(Bc, Mt, (tile, tile), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP)
        (sx, sy), r = mc.phase_shift(A - A.mean(), Bw - Bw.mean(), win)
        if r < resp_min or np.hypot(sx, sy) > max_shift:
            continue
        pa = np.array([x0 + (tile - 1) / 2, y0 + (tile - 1) / 2])
        pb = Tab[:2, :2] @ (pa + [sx, sy]) + Tab[:2, 2]      # content at pa in A appears at pa + s in the warped B
        out.append((pa, pb, float(r)))
    return out


def pose_problem(model, th, size, th0, max_bend_px=8.0):
    """Reason string if a pose is implausible (scale, rotation vs the coarse solve, or bending), else None."""
    if isinstance(model, PolyModel):
        sc, rot = model.scale_rot(th, size)
        w, h = size
        t = np.linspace(-0.5, 0.5, 9)
        edge = np.vstack([np.stack([t * w, 0 * t - h / 2], 1), np.stack([t * w, 0 * t + h / 2], 1),
                          np.stack([0 * t - w / 2, t * h], 1), np.stack([0 * t + w / 2, t * h], 1)])
        A = model.mat(th, size)
        bend = float(np.abs(model.apply(th, edge) - (edge @ A[:, :2].T + A[:, 2])).max())
    else:
        sc, rot = model.scale_rot(th)
        bend = 0.0
    sc0, rot0 = float(np.hypot(th0[0], th0[1])), float(np.degrees(np.arctan2(th0[1], th0[0])))
    if not np.isfinite(sc) or abs(sc / sc0 - 1) > 0.01:
        return f"scale {sc:.4f} vs {sc0:.4f}"
    if abs(rot - rot0) > 0.5:
        return f"rotation {rot:+.2f}° vs {rot0:+.2f}°"
    if bend > max_bend_px:
        return f"bending {bend:.1f} px"
    return None


def fine_align(nodes, pair_keys, sizes, L, M, tex4, f, theta_sim, idx_sim, ref, model, coarse, pool,
               iterations=3, tile=256, max_tiles=12, log=None, on_iteration=None):
    """Full-resolution refinement. coarse[(a, b)] = (xa, xb) stage-3 keypoints (centred), used only as a weak
    fallback for pairs with too few good tiles."""
    theta = np.array([model.from_similarity(theta_sim[idx_sim[n]]) for n in nodes])
    idx = {n: k for k, n in enumerate(nodes)}
    win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
    stats = {}
    force = set()
    prev = None
    for it in range(iterations):
        def job(ab):
            a, b = ab
            ta, tb = theta[idx[a]], theta[idx[b]]
            map_ab = lambda pts: from_canvas(model, tb, sizes[b], to_canvas(model, ta, sizes[a], pts))
            return ab, tiles_for_pair(a, b, map_ab, sizes, L, M, tex4, f, win, tile, max_tiles)
        results = dict(pool.map(job, pair_keys))
        pairs, fallback, n_tiles = [], 0, 0
        for (a, b), tl in results.items():
            if len(tl) >= 3:
                xa = centred(np.array([t[0] for t in tl]), sizes[a])
                xb = centred(np.array([t[1] for t in tl]), sizes[b])
                pairs.append({"a": a, "b": b, "xa": xa, "xb": xb, "kind": "tiles"})
                n_tiles += len(tl)
            elif (a, b) in coarse:
                xa, xb = coarse[(a, b)]
                pairs.append({"a": a, "b": b, "xa": xa, "xb": xb, "w": np.full(len(xa), 3.0 / len(xa)), "kind": "fallback"})
                fallback += 1
        dropped = []
        while True:
            target = np.array([model.from_similarity(theta_sim[idx_sim[n]]) for n in nodes])
            theta_new, idx = solve_poses(nodes, pairs, ref, model, force_affine=force, target=target)
            bad_pose = {n: pose_problem(model, theta_new[idx[n]], sizes[n], theta_sim[idx_sim[n]]) for n in nodes}
            bad_pose = {n: r for n, r in bad_pose.items() if r}
            if bad_pose and isinstance(model, PolyModel) and not set(bad_pose) <= force:
                if log:
                    log(f"round {it + 1}: {len(set(bad_pose) - force)} panel(s) kept affine (too little data for a cubic warp): " +
                        ", ".join(f"{n} ({r})" for n, r in list(bad_pose.items())[:4]) + (" …" if len(bad_pose) > 4 else ""))
                force |= set(bad_pose)
                continue
            theta = theta_new
            prms = np.array([np.sqrt(np.mean(residual(model, theta, idx, p) ** 2)) for p in pairs])
            tile_rms = prms[[p["kind"] == "tiles" for p in pairs]]
            limit = max(1.0, 3 * float(np.median(tile_rms))) if len(tile_rms) else 1.0
            bad = [k for k, p in enumerate(pairs) if p["kind"] == "tiles" and prms[k] > limit]
            if not bad:
                break
            dropped += [(pairs[k]["a"], pairs[k]["b"], float(prms[k])) for k in bad]
            removed = [pairs[k] for k in sorted(bad, key=lambda k: prms[k])]
            pairs = [p for k, p in enumerate(pairs) if k not in set(bad)]
            # a panel must stay tied to the reference: when dropping a pair cuts it off, that pair's stage-3
            # keypoints come back as a weak fallback (otherwise the solver would park it at the canvas origin)
            for p in removed:
                cut = unreachable(nodes, pairs, ref)
                if not cut:
                    break
                if (p["a"] in cut or p["b"] in cut) and (p["a"], p["b"]) in coarse:
                    xa, xb = coarse[(p["a"], p["b"])]
                    pairs.append({"a": p["a"], "b": p["b"], "xa": xa, "xb": xb, "w": np.full(len(xa), 3.0 / len(xa)),
                                  "kind": "fallback"})
                    fallback += 1
        cut = unreachable(nodes, pairs, ref)
        if cut:
            raise RuntimeError("fine alignment left panels without any constraint to the reference: " + ", ".join(sorted(cut)))
        per = {n: [] for n in nodes}
        for p in pairs:
            if p["kind"] == "tiles":
                e = residual_in_panel(model, theta, idx, p, sizes)
                per[p["a"]].append(e)
                per[p["b"]].append(e)
        per_rms = {n: float(np.sqrt(np.mean(np.concatenate(v) ** 2))) if v else float("nan") for n, v in per.items()}
        # RMS over the tile measurements; with none at all (small or featureless overlaps) the keypoint fallback
        # pairs are all there is, and the result is the coarse placement at full resolution
        basis = [p for p in pairs if p["kind"] == "tiles"] or pairs
        allr = np.concatenate([residual_in_panel(model, theta, idx, p, sizes) for p in basis])
        stats = {"iteration": it + 1, "rms": float(np.sqrt(np.mean(allr ** 2))), "median": float(np.median(allr)),
                 "tiles": n_tiles, "pairs": len(pairs), "fallback": fallback, "dropped": dropped, "per_rms": per_rms,
                 "tile_pairs": sum(p["kind"] == "tiles" for p in pairs), "fit_pairs": [p for p in pairs if p["kind"] == "tiles"]}
        if prev is not None and stats["rms"] > prev[2]["rms"] * 1.02:
            if log:
                log(f"round {it + 1}: RMS {stats['rms']:.3f} px did not improve on {prev[2]['rms']:.3f} px; keeping round {it}")
            theta, idx, stats = prev
            break
        prev = (theta.copy(), dict(idx), stats)
        if log and not stats["tile_pairs"]:
            log(f"round {it + 1}: no textured tiles in any overlap: fine alignment unavailable, keeping the coarse "
                f"(keypoint) placement · keypoint RMS {stats['rms']:.3f} px")
        elif log:
            finite = [v for v in per_rms.values() if np.isfinite(v)]
            log(f"round {it + 1}: {n_tiles} tiles on {stats['tile_pairs']} pairs · RMS {stats['rms']:.3f} px · "
                f"median {stats['median']:.3f} px · worst panel {max(finite):.2f} px" +
                (f" · {fallback} pairs on keypoint fallback" if fallback else "") +
                (f" · dropped {len(dropped)} inconsistent" if dropped else ""))
        if on_iteration:
            on_iteration(it, theta, idx, stats)
    return theta, idx, stats


def _cg_mesh(n_nodes, cols, vals, c, w0, smooth, lam_s, lam_0, iters=400, huber=1.0, irls=3):
    """Mesh displacements D (n_nodes × 2) minimising Σ w |c + A D|² + lam_s Σ|D_i − D_j|² + lam_0 |D|², with A the
    bilinear weights of both sides of each observation (vals already signed); conjugate gradients, Huber IRLS."""
    D = np.zeros((n_nodes, 2))
    w = w0.copy()
    Ax = lambda x: (vals * x[cols]).sum(1)
    ATy = lambda y: np.bincount(cols.ravel(), (vals * y[:, None]).ravel(), minlength=n_nodes)

    def Sx(x):
        d = x[smooth[:, 0]] - x[smooth[:, 1]]
        return np.bincount(smooth[:, 0], d, minlength=n_nodes) - np.bincount(smooth[:, 1], d, minlength=n_nodes)
    for _ in range(irls):
        for k in range(2):
            op = lambda x: ATy(w * Ax(x)) + lam_s * Sx(x) + lam_0 * x
            rhs = -ATy(w * c[:, k])
            x = D[:, k].copy()
            r = rhs - op(x)
            p = r.copy()
            rs = r @ r
            for _ in range(iters):
                Ap = op(p)
                a = rs / (p @ Ap)
                x += a * p
                r -= a * Ap
                rn = r @ r
                if rn < 1e-10 * (rhs @ rhs + 1e-12):
                    break
                p = r + rn / rs * p
                rs = rn
            D[:, k] = x
        e = np.hypot(c[:, 0] + Ax(D[:, 0]), c[:, 1] + Ax(D[:, 1]))
        w = w0 * np.where(e <= huber, 1.0, huber / np.maximum(e, 1e-9))
    return D


def refine_mesh(nodes, pair_keys, sizes, L, M, tex4, f, theta, idx, ref, model, coarse, theta_sim, idx_sim, pool,
                dropped=(), fit_pairs=(), tile=128, max_tiles=600, lam_s=1.0, lam_0=0.02, log=None):
    """After fine_align (poly3): dense 128 px tiles, poly3 refitted on them (weighted by phase-correlation response⁴,
    the measurement noise is ≤ 0.45 px at response ≥ 0.7 and ≈ 1.6 px at 0.3–0.5), then the per-panel mesh on top
    of that. Pairs fine_align dropped as inconsistent stay out. fit_pairs: fine_align's own tile pairs, on which
    'rms', 'median' and 'per_rms' are reported so they compare with a poly3 build. Returns (theta, idx, stats)."""
    win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
    skip = {(a, b) for a, b, _ in dropped}

    def job(ab):
        a, b = ab
        ta, tb = theta[idx[a]], theta[idx[b]]
        map_ab = lambda pts: from_canvas(model, tb, sizes[b], to_canvas(model, ta, sizes[a], pts))
        return ab, tiles_for_pair(a, b, map_ab, sizes, L, M, tex4, f, win, tile, max_tiles, resp_min=0.15, max_shift=4.0)
    tiles = dict(pool.map(job, [k for k in pair_keys if k not in skip]))
    weight = lambda r: np.clip(r, 0, 1) ** 4 / 0.8 ** 4
    pairs, n_tiles = [], 0
    for (a, b), tl in tiles.items():
        if len(tl) >= 3:
            pairs.append({"a": a, "b": b, "xa": centred(np.array([t[0] for t in tl]), sizes[a]),
                          "xb": centred(np.array([t[1] for t in tl]), sizes[b]),
                          "w": weight(np.array([t[2] for t in tl])) * 12 / len(tl), "kind": "tiles"})
            n_tiles += len(tl)
        elif (a, b) in coarse:
            xa, xb = coarse[(a, b)]
            pairs.append({"a": a, "b": b, "xa": xa, "xb": xb, "w": np.full(len(xa), 3.0 / len(xa)), "kind": "fallback"})
    if unreachable(nodes, pairs, ref):
        raise RuntimeError("dense tiles left panels without a constraint to the reference")
    target = np.array([model.from_similarity(theta_sim[idx_sim[n]]) for n in nodes])
    force = set()
    while True:
        th2, ix2 = solve_poses(nodes, pairs, ref, model, force_affine=force, target=target)
        bad = {n for n in nodes if pose_problem(model, th2[ix2[n]], sizes[n], theta_sim[idx_sim[n]])}
        if not bad - force:
            break
        force |= bad
    # mesh on top of the dense poly3
    base, shapes, k = {}, {}, 0
    for n in nodes:
        shapes[n] = mesh_shape(sizes[n], model.spacing)
        base[n] = k
        k += shapes[n][0] * shapes[n][1]
    smooth = []
    for n in nodes:
        nx, ny = shapes[n]
        g = base[n] + np.arange(ny * nx).reshape(ny, nx)
        smooth += list(zip(g[:, :-1].ravel(), g[:, 1:].ravel())) + list(zip(g[:-1].ravel(), g[1:].ravel()))
    smooth = np.array(smooth)
    cols, vals, cs, ws, obs = [], [], [], [], []
    for (a, b), tl in tiles.items():
        if not tl:
            continue
        pa, pb = np.array([t[0] for t in tl]), np.array([t[1] for t in tl])
        r = np.array([t[2] for t in tl])
        Xa, Xb = to_canvas(model, th2[ix2[a]], sizes[a], pa), to_canvas(model, th2[ix2[b]], sizes[b], pb)
        ca, va = mesh_weights(pa, model.spacing, *shapes[a])
        cb, vb = mesh_weights(pb, model.spacing, *shapes[b])
        cols.append(np.hstack([ca + base[a], cb + base[b]]))
        vals.append(np.hstack([va, -vb]))
        cs.append(Xa - Xb)
        ws.append(weight(r))
        obs.append((a, b, pa, pb, r))
    D = _cg_mesh(k, np.vstack(cols), np.vstack(vals), np.vstack(cs), np.concatenate(ws), smooth, lam_s, lam_0)
    rows = [model.pack(th2[ix2[n]], sizes[n], D[base[n]:base[n] + shapes[n][0] * shapes[n][1]]) for n in nodes]
    width = max(len(r) for r in rows)
    theta_m = np.array([np.pad(r, (0, width - len(r))) for r in rows])
    idx_m = {n: i for i, n in enumerate(nodes)}

    def misfit(th, ix):
        per = {n: [] for n in nodes}
        allr = []
        for a, b, pa, pb, r in obs:
            e = np.hypot(*(to_canvas(model, th[ix[a]], sizes[a], pa) - to_canvas(model, th[ix[b]], sizes[b], pb)).T)[r >= 0.5]
            per[a].append(e)
            per[b].append(e)
            allr.append(e)
        allr = np.concatenate(allr) if allr else np.zeros(0)
        return allr, {n: float(np.sqrt(np.mean(np.concatenate(v) ** 2))) if v and len(np.concatenate(v)) else float("nan")
                      for n, v in per.items()}
    before, _ = misfit(theta, idx)
    dense, _ = misfit(th2, ix2)
    after, _ = misfit(theta_m, idx_m)
    disp = np.hypot(D[:, 0], D[:, 1])
    st = {"tiles": n_tiles, "pairs": sum(p["kind"] == "tiles" for p in pairs), "forced_affine": sorted(force),
          "dense_rms_before": float(np.sqrt(np.mean(before ** 2))), "dense_rms_poly3": float(np.sqrt(np.mean(dense ** 2))),
          "dense_rms": float(np.sqrt(np.mean(after ** 2))),
          "dense_over_1px_before": float((before > 1).mean()), "dense_over_1px": float((after > 1).mean()),
          "mesh_rms_px": float(np.sqrt(np.mean(disp ** 2))), "mesh_max_px": float(disp.max()), "spacing": model.spacing}
    if fit_pairs:
        per = {n: [] for n in nodes}
        for p in fit_pairs:
            e = residual_in_panel(model, theta_m, idx_m, p, sizes)
            per[p["a"]].append(e)
            per[p["b"]].append(e)
        allr = np.concatenate([residual_in_panel(model, theta_m, idx_m, p, sizes) for p in fit_pairs])
        st.update(rms=float(np.sqrt(np.mean(allr ** 2))), median=float(np.median(allr)),
                  per_rms={n: float(np.sqrt(np.mean(np.concatenate(v) ** 2))) if v else float("nan") for n, v in per.items()})
    if log:
        log(f"mesh: {n_tiles} tiles of {tile} px on {st['pairs']} pairs · misfit on tiles with response ≥ 0.5: "
            f"RMS {st['dense_rms_before']:.3f} → {st['dense_rms_poly3']:.3f} (dense poly3) → {st['dense_rms']:.3f} px (mesh) · "
            f"> 1 px {st['dense_over_1px_before'] * 100:.1f} % → {st['dense_over_1px'] * 100:.1f} % · "
            f"mesh displacement RMS {st['mesh_rms_px']:.2f} px, max {st['mesh_max_px']:.1f} px")
    return theta_m, idx_m, st


# ================================================================ stage 5: photometry (gain + offset per channel)

def proxy_conv(f):
    """3×3 full-resolution pixel -> proxy pixel (INTER_AREA, integer factor f)."""
    return np.array([[1 / f, 0, 0.5 / f - 0.5], [0, 1 / f, 0.5 / f - 0.5], [0, 0, 1.0]])


def photometry(nodes, pair_keys, P, rgb4, m4, sat4, f, ref, sigma_meas=0.003, sigma_g=0.2, sigma_o=0.003):
    """Solve g, o per panel and channel so that g_a·v_a + o_a = g_b·v_b + o_b on shared terrain (matched
    percentiles of the overlap). Reference panel fixed at g = 1, o = 0. Priors in physical units: gains loosely
    around 1 (sigma_g), offsets tightly around 0 (sigma_o, fraction of full scale; ~200 ADU), because gain and
    offset trade off against each other on a narrow brightness range. Returns gains, offsets (BGR), stats."""
    Cf = proxy_conv(f)
    qs = [10, 25, 50, 75, 90]
    obs = []
    for a, b in pair_keys:
        Tq = Cf @ np.linalg.inv(P[b]) @ P[a] @ np.linalg.inv(Cf)
        ha, wa = rgb4[a].shape[:2]
        Bw = cv2.warpAffine(rgb4[b].astype(np.float32), Tq[:2], (wa, ha), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        okb = cv2.warpAffine((m4[b] & ~sat4[b]).astype(np.uint8), Tq[:2], (wa, ha), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
        common = (m4[a] & ~sat4[a] & (okb > 0)).astype(np.uint8)
        common = cv2.erode(common, np.ones((7, 7), np.uint8)) > 0
        if common.sum() < 3000:
            continue
        va = np.percentile(rgb4[a][common].astype(np.float32), qs, axis=0)
        vb = np.percentile(Bw[common], qs, axis=0)
        obs.append((a, b, va, vb, np.sqrt(common.sum() / 1e4)))
    idx = {n: k for k, n in enumerate(nodes)}
    free = [n for n in nodes if n != ref]
    col = {n: j for j, n in enumerate(free)}
    N = len(free)
    gains = {n: np.ones(3) for n in nodes}
    offs = {n: np.zeros(3) for n in nodes}
    before, after = [], []
    for ch in range(3):
        rows, rhs, wts = [], [], []
        for a, b, va, vb, w in obs:
            for q in range(len(qs)):
                r = np.zeros(2 * N)
                y = 0.0
                for n, v, sgn in ((a, va[q, ch], 1), (b, vb[q, ch], -1)):
                    if n in col:
                        r[col[n]] += sgn * v
                        r[N + col[n]] += sgn
                    else:
                        y -= sgn * v          # reference: g = 1, o = 0
                rows.append(r)
                rhs.append(y)
                wts.append(w)
        # reshape: with no usable overlap at all only the priors remain (gains 1, offsets 0)
        rows, rhs, wts = np.array(rows).reshape(-1, 2 * N), np.array(rhs).reshape(-1), np.array(wts).reshape(-1) / sigma_meas
        prior = np.zeros((2 * N, 2 * N))
        prior[:N, :N] = np.eye(N) / sigma_g
        prior[N:, N:] = np.eye(N) / sigma_o
        prior_rhs = np.concatenate([np.full(N, 1 / sigma_g), np.zeros(N)])
        A = np.vstack([rows * wts[:, None], prior])
        y = np.concatenate([rhs * wts, prior_rhs])
        sol, *_ = np.linalg.lstsq(A, y, rcond=None)
        for n in free:
            gains[n][ch] = sol[col[n]]
            offs[n][ch] = sol[N + col[n]]
        for a, b, va, vb, w in obs:
            before.append(np.abs(va[:, ch] - vb[:, ch]) / np.maximum(va[:, ch], 1e-4))
            after.append(np.abs((gains[a][ch] * va[:, ch] + offs[a][ch]) - (gains[b][ch] * vb[:, ch] + offs[b][ch]))
                         / np.maximum(va[:, ch], 1e-4))
    per_pair = []                                           # where brightness was compared, and how much it disagreed
    for a, b, va, vb, w in obs:
        mb = float(np.mean(np.median(np.abs(va - vb) / np.maximum(va, 1e-4), axis=0)))
        ga, oa, gb, ob = gains[a], offs[a], gains[b], offs[b]
        ma = float(np.mean(np.median(np.abs((va * ga + oa) - (vb * gb + ob)) / np.maximum(va, 1e-4), axis=0)))
        per_pair.append((a, b, mb, ma))
    stats = {"pairs": len(obs), "mismatch_before_pct": float(np.median(np.concatenate(before)) * 100) if before else 0.0,
             "mismatch_after_pct": float(np.median(np.concatenate(after)) * 100) if after else 0.0, "per_pair": per_pair}
    return gains, offs, stats


# ================================================================ stage 6: region selection

def sharpness_map(L16, q, border=32, taper_px=150, floor=0.03, smooth_px=32):
    """Local sharpness at 1/q scale: geometric mean of fine and coarse detail energy / local brightness²,
    times an edge taper so seams never sit right at a frame edge. Energies are computed at full resolution,
    then reduced to 1/q before the wide (smooth_px) smoothing, which is ~q² cheaper and equivalent."""
    L = mc.proxy_crop(L16, q).astype(np.float32)           # whole q × q blocks: label_matrix assumes exactly 1/q
    h, w = L.shape
    sz = (w // q, h // q)
    g1, g25, g6 = (cv2.GaussianBlur(L, (0, 0), s) for s in (1.0, 2.5, 6.0))
    ef = cv2.resize((g1 - g25) ** 2, sz, interpolation=cv2.INTER_AREA)
    ec = cv2.resize((g25 - g6) ** 2, sz, interpolation=cv2.INTER_AREA)
    lq = cv2.resize(L, sz, interpolation=cv2.INTER_AREA)
    sq = smooth_px / q
    ef, ec, lq = (cv2.GaussianBlur(a, (0, 0), sq) for a in (ef, ec, lq))
    s4 = np.sqrt(ef * ec) / np.maximum(lq, floor) ** 2
    valid = np.zeros(s4.shape, np.uint8)
    # one extra cell: a label cell is warped to the canvas by nearest neighbour, which can reach ~2 px beyond the
    # cell, and blend_panel only uses pixels from `border` inwards. Without the margin a panel could win a sliver it
    # cannot fill: 1 px lines with no full-resolution source, painted black as "not covered" (measured: 4661 px).
    b = -(-border // q) + 1
    valid[b:-b, b:-b] = 1
    dist = cv2.distanceTransform(valid, cv2.DIST_L2, 3)
    return s4 * np.clip(dist * q / taper_px, 0, 1), valid


def label_matrix(P, q):
    """3×3: panel 1/q pixel -> label-canvas (1/q of the canvas) pixel."""
    Sp = np.array([[q, 0, (q - 1) / 2], [0, q, (q - 1) / 2], [0, 0, 1.0]])
    Sc = np.array([[1 / q, 0, 0.5 / q - 0.5], [0, 1 / q, 0.5 / q - 0.5], [0, 0, 1.0]])
    return Sc @ P @ Sp


def select_regions(nodes, sizes, L, P, canvas_pad, q, pool, borders=None, on_panel=None, smooth=6):
    Wl, Hl = canvas_pad[0] // q, canvas_pad[1] // q
    best = np.full((Hl, Wl), -1.0, np.float32)
    lab = np.full((Hl, Wl), 65535, np.uint16)
    geo = {}

    def job(n):
        return (n,) + sharpness_map(L[n], q, border=(borders or {}).get(n, 32))
    for k, (n, s4, valid) in enumerate(pool.map(job, nodes), 1):
        Mq = label_matrix(P[n], q)
        h4, w4 = s4.shape
        cor = np.array([[0, 0, 1], [w4, 0, 1], [0, h4, 1], [w4, h4, 1]], float) @ Mq.T
        x0, y0 = max(0, int(cor[:, 0].min()) - 2), max(0, int(cor[:, 1].min()) - 2)
        x1, y1 = min(Wl, int(cor[:, 0].max()) + 3), min(Hl, int(cor[:, 1].max()) + 3)
        Mb = (np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1.0]]) @ Mq)[:2]
        sw = cv2.warpAffine(s4, Mb, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR)
        vw = cv2.warpAffine(valid, Mb, (x1 - x0, y1 - y0), flags=cv2.INTER_NEAREST) > 0
        geo[n] = (Mb, (x0, y0, x1, y1), valid)
        sub, lsub = best[y0:y1, x0:x1], lab[y0:y1, x0:x1]
        upd = vw & (sw > sub)
        sub[upd] = sw[upd]
        lsub[upd] = nodes.index(n)
        if on_panel:
            on_panel(k, lab)
    # speckle cleanup: smoothed indicators compete again, so small islands merge into their surroundings
    best2 = np.full((Hl, Wl), -1.0, np.float32)
    lab2 = np.full((Hl, Wl), 65535, np.uint16)
    for i, n in enumerate(nodes):
        Mb, (x0, y0, x1, y1), valid = geo[n]
        ind = cv2.GaussianBlur((lab[y0:y1, x0:x1] == i).astype(np.float32), (0, 0), smooth)
        vw = cv2.warpAffine(valid, Mb, (x1 - x0, y1 - y0), flags=cv2.INTER_NEAREST) > 0
        ind[~vw] = -1
        sub, lsub = best2[y0:y1, x0:x1], lab2[y0:y1, x0:x1]
        upd = ind > sub
        sub[upd] = ind[upd]
        lsub[upd] = i
    counts = np.bincount(lab2[lab2 != 65535].ravel(), minlength=len(nodes))
    shares = {n: float(counts[i]) / max(1, counts.sum()) for i, n in enumerate(nodes)}
    return lab2, shares


# ================================================================ stage 7: multi-band blending

def pushpull(img, valid, min_size=16):
    """Fill invalid pixels smoothly from valid ones (pyramid push-pull), so pyramids never see black."""
    m = valid.astype(np.float32)
    levels = [(img * m[..., None], m)]
    while min(levels[-1][1].shape) > min_size:
        a, b = levels[-1]
        levels.append((cv2.pyrDown(a), cv2.pyrDown(b)))
    a, b = levels[-1]
    est = a / np.maximum(b, 1e-6)[..., None]
    for a, b in reversed(levels[:-1]):
        up = cv2.pyrUp(est, dstsize=(a.shape[1], a.shape[0]))
        good = (b > 0.5)[..., None]
        est = np.where(good, a / np.maximum(b, 1e-6)[..., None], up)
    return np.where(valid[..., None], img, est).astype(np.float32)


def blend_panel(n, i, path, size, P, gain, off, lab, q, canvas_pad, levels, border=32, model=None, th=None, offset=(0, 0)):
    """Warp one panel into the bounding box of its won region and build its pyramids. With a polynomial model
    the exact inverse mapping is computed on a 16 px grid and interpolated (cv2.remap); otherwise warpAffine."""
    ys, xs = np.nonzero(lab == i)
    if len(ys) == 0:
        return None
    step = 2 ** levels
    margin = 2 * step
    x0 = max(0, (xs.min() * q - margin) // step * step)
    y0 = max(0, (ys.min() * q - margin) // step * step)
    x1 = min(canvas_pad[0], -(-((xs.max() + 1) * q + margin) // step) * step)
    y1 = min(canvas_pad[1], -(-((ys.max() + 1) * q + margin) // step) * step)
    bw, bh = x1 - x0, y1 - y0
    img, _, _ = mc.read_image(path)
    if img.shape[2] == 1:
        img = np.repeat(img, 3, axis=2)
    img = img * gain.astype(np.float32) + off.astype(np.float32)
    w, h = size
    valid = np.zeros((h, w), np.float32)
    valid[border:h - border, border:w - border] = 1
    if isinstance(model, PolyModel):
        g = 16
        gx, gy = np.meshgrid(np.arange(0, bw + g, g, dtype=float), np.arange(0, bh + g, g, dtype=float))
        src = from_canvas(model, th, size, np.stack([gx.ravel() + x0, gy.ravel() + y0], 1), offset)
        # the grid is node-aligned (node k at k·g px): resample it with an explicit linear map
        yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
        mx = cv2.remap(src[:, 0].reshape(gx.shape).astype(np.float32), xx / g, yy / g, cv2.INTER_LINEAR)
        my = cv2.remap(src[:, 1].reshape(gx.shape).astype(np.float32), xx / g, yy / g, cv2.INTER_LINEAR)
        wimg = cv2.remap(img, mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
        wval = cv2.remap(valid, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) > 0.99
    else:
        Minv = (np.linalg.inv(P) @ np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1.0]]))[:2]
        wimg = cv2.warpAffine(img, Minv, (bw, bh), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP)
        wval = cv2.warpAffine(valid, Minv, (bw, bh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP) > 0.99
    region = cv2.resize((lab[y0 // q:y1 // q, x0 // q:x1 // q] == i).astype(np.float32), (bw, bh), interpolation=cv2.INTER_NEAREST)
    weight = region * wval
    if weight.sum() == 0:
        return None
    wimg = pushpull(wimg, wval)
    gi, gw = [wimg], [weight]
    for _ in range(levels):
        gi.append(cv2.pyrDown(gi[-1]))
        gw.append(cv2.pyrDown(gw[-1]))
    lap = [gi[k] - cv2.pyrUp(gi[k + 1], dstsize=(gi[k].shape[1], gi[k].shape[0])) for k in range(levels)] + [gi[levels]]
    return n, x0, y0, lap, gw


def new_pyramids(canvas_pad, levels):
    Wp, Hp = canvas_pad
    acc = [np.zeros((Hp >> k, Wp >> k, 3), np.float32) for k in range(levels + 1)]
    wacc = [np.zeros((Hp >> k, Wp >> k), np.float32) for k in range(levels + 1)]
    return acc, wacc


def accumulate(acc, wacc, res):
    n, x0, y0, lap, gw = res
    for k in range(len(lap)):
        h, w = gw[k].shape
        ys, xs = y0 >> k, x0 >> k
        acc[k][ys:ys + h, xs:xs + w] += lap[k] * gw[k][..., None]
        wacc[k][ys:ys + h, xs:xs + w] += gw[k]


def collapse(acc, wacc, down_to=0, free=False):
    """Normalise each level by its weights and collapse down to level `down_to`."""
    L = len(acc) - 1
    out = acc[L] / np.maximum(wacc[L], 1e-6)[..., None]
    for k in range(L - 1, down_to - 1, -1):
        out = cv2.pyrUp(out, dstsize=(acc[k].shape[1], acc[k].shape[0]))
        good = wacc[k] > 1e-4
        lvl = np.zeros_like(acc[k])
        np.divide(acc[k], wacc[k][..., None], out=lvl, where=good[..., None])
        out += lvl
        if free:
            acc[k + 1] = wacc[k + 1] = None
    return out


# ================================================================ stage 8: finishing

def finish(out, coverage, q=4, feather=12, background="neutral-black", colour="highlands", log=None):
    """Background neutralisation, colour balance, black space; in place on the float32 BGR canvas."""
    info = {}
    Hh, Wh = out.shape[0] // q, out.shape[1] // q
    small = cv2.resize(out, (Wh, Hh), interpolation=cv2.INTER_AREA)
    cov = cv2.resize(coverage.astype(np.uint8), (Wh, Hh), interpolation=cv2.INTER_NEAREST) > 0
    lum = 0.0722 * small[..., 0] + 0.7152 * small[..., 1] + 0.2126 * small[..., 2]
    ls = cv2.GaussianBlur(lum, (0, 0), 2)
    # the sky: below 0.02 when the stacks have no pedestal; otherwise a band just above the darkest covered level, as
    # long as that level is far below the bright Moon (the 95th percentile, whatever the share of sky in the frame;
    # maria stay above ~0.45 of it; measured on the 26 May 2026 set: sky 0.072 against 0.25 median on the disk)
    lo_ = float(np.percentile(ls[cov], 2)) if cov.any() else 0.0
    if lo_ < 0.02:
        dark = cov & (ls < 0.02)
    elif lo_ < 0.35 * float(np.percentile(ls[cov], 95)):
        dark = cov & (ls < lo_ + 0.02)
    else:
        dark = np.zeros_like(cov)
    bg = float(np.median(ls[dark])) if dark.any() else 0.0
    sig = float(np.median(np.abs(ls[dark] - bg)) * 1.4826) if dark.any() else 0.0
    thr = max(5 * sig, 0.004)
    disk = (cov & (ls > bg + thr)).astype(np.uint8)
    n, labc, st, _ = cv2.connectedComponentsWithStats(disk, connectivity=8)
    if n > 1:
        # the lit disk, plus every clearly lit island: sunlit peaks beyond the terminator are separate components
        # and must not be blackened with the sky (measured: 5568 px of 8600–9300 ADU peaks lost otherwise)
        peak = np.zeros(n)
        np.maximum.at(peak, labc.ravel(), ls.ravel())
        keep = (st[:, cv2.CC_STAT_AREA] >= 2) & (peak >= bg + 2 * thr)
        keep[0] = False
        keep[1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))] = True
        disk = keep[labc].astype(np.uint8)
    # small peaks (~10 px) are diluted below the threshold by the blur above: find them again on a lightly
    # smoothed copy, at 8 sigma of its own sky noise
    fine = cv2.GaussianBlur(lum, (0, 0), 0.7)
    sig_f = float(np.median(np.abs(fine[dark] - bg)) * 1.4826) if dark.any() else 0.0
    isl = (cov & (fine > bg + max(8 * sig_f, 0.004))).astype(np.uint8)
    n_i, lab_i, st_i, _ = cv2.connectedComponentsWithStats(isl, connectivity=8)
    if n_i > 1:
        # only separate islands: anything touching the lit area (e.g. the scattered-light band at the limb) stays out
        touch = np.zeros(n_i, bool)
        touch[np.unique(lab_i[cv2.dilate(disk, np.ones((3, 3), np.uint8)) > 0])] = True
        big = (st_i[:, cv2.CC_STAT_AREA] >= 2) & ~touch
        big[0] = False
        disk = (disk.astype(bool) | big[lab_i]).astype(np.uint8)
    inv = (1 - disk).astype(np.uint8)
    n2, lab2 = cv2.connectedComponents(inv, connectivity=4)
    edge = np.unique(np.concatenate([lab2[0], lab2[-1], lab2[:, 0], lab2[:, -1]]))
    disk = ~np.isin(lab2, edge[edge > 0]) if n2 > 1 else disk.astype(bool)
    outside = cv2.distanceTransform((~disk).astype(np.uint8), cv2.DIST_L2, 5)
    sky = cov & ~disk & (outside > 60 / q)
    info["sky_px"] = int(sky.sum()) * q * q
    if background != "keep" and sky.sum() > 50000 / (q * q):
        ped = np.array([float(np.median(small[..., c][sky])) for c in range(3)])
        for c in range(3):
            out[..., c] -= ped[c]
        small -= ped
        info["pedestal_bgr"] = (ped * 65535).round(1).tolist()
        info["residual_sky_bgr"] = [round(float(np.median(small[..., c][sky])) * 65535, 2) for c in range(3)]
    elif background != "keep" and log:
        log("too little sky for background neutralisation: skipped")
    if colour != "none":
        inner = cv2.distanceTransform(disk.astype(np.uint8), cv2.DIST_L2, 5) > 40 / q
        lum = 0.0722 * small[..., 0] + 0.7152 * small[..., 1] + 0.2126 * small[..., 2]
        sel = inner & (small.max(axis=2) < 0.98)
        if colour == "highlands" and sel.any():
            lo, hi = np.percentile(lum[sel], [40, 95])
            sel &= (lum >= lo) & (lum <= hi)
        if sel.sum() > 1000:
            m = np.array([float(np.median(small[..., c][sel])) for c in range(3)])
            target = 0.0722 * m[0] + 0.7152 * m[1] + 0.2126 * m[2]
            g = target / np.maximum(m, 1e-6)
            for c in range(3):
                out[..., c] *= g[c]
            small *= g
            info["colour_gains_bgr"] = g.round(4).tolist()
            info["colour_before_bgr"] = (m / target).round(4).tolist()
    if background == "neutral-black":
        w4 = np.clip(1 - outside / (feather / q), 0, 1)
        w4 = (0.5 - 0.5 * np.cos(np.pi * w4)).astype(np.float32)          # cosine ramp outward from the limb
        w4[disk] = 1
        w = cv2.resize(w4, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_LINEAR)
        for c in range(3):
            out[..., c] *= w
        out[~coverage] = 0
    sample = out[::7, ::7].reshape(-1, 3)
    p = float(np.percentile(sample, 99.99))
    if p > 1:
        out /= p
        info["rescaled_by"] = round(1 / p, 4)
    np.clip(out, 0, 1, out=out)
    info["clipped_px"] = int((sample >= 1).any(axis=1).sum()) * 49
    info["disk_small"] = disk
    return info


def write_tiff(path, out, bbox):
    x0, y0, x1, y1 = bbox
    crop = out[y0:y1, x0:x1]
    u16 = np.empty(crop.shape, np.uint16)
    for r in range(0, crop.shape[0], 1024):                    # chunked: avoids a second float copy
        u16[r:r + 1024] = (crop[r:r + 1024] * 65535 + 0.5).astype(np.uint16)
    ok = cv2.imwrite(path, u16, [cv2.IMWRITE_TIFF_COMPRESSION, cv2.IMWRITE_TIFF_COMPRESSION_NONE])
    if not ok:
        raise IOError(f"could not write {path}")
    return u16.shape


# ================================================================ contribution map

def panel_colour(i):
    h = (i * 0.618034) % 1.0
    hsv = np.uint8([[[int(h * 180), 150, 255]]])
    return tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])


def contribution_map(path_png, final_small, lab, nodes, paths, quads, shares, q, crop_q, font=None, disk=None):
    """Which panel supplied which region: tinted regions, outlines, file names, legend with shares.
    With disk (bool, same grid as lab), only the lunar disk is tinted and shares are of the disk area."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        Image = None
    x0, y0, x1, y1 = crop_q
    base = final_small[y0:y1, x0:x1]
    labc = lab[y0:y1, x0:x1].copy()
    if disk is not None:
        dsk = disk[y0:y1, x0:x1]
        labc[~dsk] = 65535                                  # sky ends up black: it supplies nothing visible
        cnt = np.bincount(labc[labc != 65535].ravel(), minlength=len(nodes))
        shares = {n: float(cnt[i]) / max(1, cnt.sum()) for i, n in enumerate(nodes)}
    lumimg = (np.clip(base, 0, 1) ** (1 / 1.1) * 255).astype(np.uint8)
    img = (lumimg * 0.5).astype(np.uint8)
    tint = np.zeros_like(img)
    for i in range(len(nodes)):
        if shares.get(nodes[i], 0) > 0:
            tint[labc == i] = panel_colour(i)
    img = cv2.addWeighted(img, 1.0, tint, 0.28, 0)
    edges = (cv2.morphologyEx(labc, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0) & (labc != 65535)
    img[edges] = (img[edges] * 0.3 + 200 * 0.7).astype(np.uint8)
    for i, n in enumerate(nodes):
        qd = (quads[n] - [x0, y0]).astype(np.int32)
        col = panel_colour(i) if shares.get(n, 0) > 0 else (90, 90, 90)
        cv2.polylines(img, [qd], True, col, 1, cv2.LINE_AA)
    legend_w = 900
    canvas = np.zeros((max(img.shape[0], 60 + 22 * len(nodes)), img.shape[1] + legend_w, 3), np.uint8)
    canvas[:img.shape[0], :img.shape[1]] = img
    labels = []
    for i, n in enumerate(nodes):
        if shares.get(n, 0) <= 0:
            continue
        ys, xs = np.nonzero(labc == i)
        if len(ys) < 50:
            continue
        m = (labc == i).astype(np.uint8)
        nn, cc, st, cen = cv2.connectedComponentsWithStats(m)
        j = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        cx, cy = cen[j]
        wbox = st[j, cv2.CC_STAT_WIDTH]
        labels.append((n, os.path.basename(paths[n]), cx, cy, wbox, i))
    font_path, font_index = font if font else (None, 0)
    if Image is not None and font_path and os.path.exists(font_path):
        pil = Image.fromarray(canvas[..., ::-1].copy())
        d = ImageDraw.Draw(pil)
        for n, fname, cx, cy, wbox, i in labels:
            rest = fname.replace(n, "…", 1) if n in fname else fname
            size = int(np.clip(wbox / max(len(rest), 1) * 1.6, 9, 22))
            f1 = ImageFont.truetype(font_path, int(size * 1.4), index=font_index)
            f2 = ImageFont.truetype(font_path, size, index=font_index)
            col = panel_colour(i)
            d.text((cx, cy - size * 0.6), n, font=f1, fill=(255, 255, 255), anchor="mm")
            d.text((cx, cy + size * 0.8), rest, font=f2, fill=(col[2], col[1], col[0]), anchor="mm")
        f3 = ImageFont.truetype(font_path, 26, index=font_index)
        f4 = ImageFont.truetype(font_path, 15, index=font_index)
        lx = img.shape[1] + 30
        d.text((lx, 20), "Contributions", font=f3, fill=(245, 245, 245))
        y = 64
        for i, n in sorted(enumerate(nodes), key=lambda t: -shares.get(t[1], 0)):
            col = panel_colour(i) if shares.get(n, 0) > 0 else (90, 90, 90)
            d.rectangle([lx, y + 3, lx + 12, y + 15], fill=(col[2], col[1], col[0]))
            share = shares.get(n, 0) * 100
            d.text((lx + 22, y), f"{share:5.1f} %  {os.path.basename(paths[n])}", font=f4,
                   fill=(230, 230, 230) if share > 0 else (120, 120, 120))
            y += 22
        canvas = np.asarray(pil)[..., ::-1].copy()
    else:
        for n, fname, cx, cy, wbox, i in labels:
            cv2.putText(canvas, n, (int(cx) - 30, int(cy)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path_png, canvas)
    return canvas, shares
