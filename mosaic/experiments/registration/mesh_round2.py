import sys, pickle, numpy as np
sys.path.insert(0, '.')
from harness import capture
import mosaic_stages as ms
from mesh_proto import Mesh, fit_mesh, residuals, stats
C = theta = idx = tiles = sel_train = test_hi = test_mid = None
W = lambda r: np.clip(r, 0, 1) ** 4 / 0.8 ** 4


def obs_for(th, ix, mesh):
    out = []
    for (a, b), tl in tiles.items():
        t = [x for x, keep in zip(tl, sel_train[(a, b)]) if keep]
        if not t: continue
        pa = np.array([x[0] for x in t]); pb = np.array([x[1] for x in t])
        Xa = ms.to_canvas(C.model, th[ix[a]], C.sizes[a], pa); Xb = ms.to_canvas(C.model, th[ix[b]], C.sizes[b], pb)
        ca, va = mesh.weights(a, pa); cb, vb = mesh.weights(b, pb)
        out.append((ca, va, cb, vb, Xa - Xb, W(np.array([x[2] for x in t]))))
    return out
def report(th, ix, mesh, D, tag):
    stats(residuals(C, th, ix, mesh, D, test_hi), tag + ' · strong tiles')
    stats(residuals(C, th, ix, mesh, D, test_mid), tag + ' · medium tiles')

def main():
    global C, theta, idx, tiles, sel_train, test_hi, test_mid
    C = capture()
    d = pickle.load(open('mesh_inputs.pkl', 'rb')); theta, idx, tiles = d['theta'], d['idx'], d['tiles']
    sel_train = {k: [((int(p[0][0]) // 96 + int(p[0][1]) // 96) % 2 == 0) for p in v] for k, v in tiles.items()}
    test_hi = [(a, b, p[0], p[1]) for (a, b), v in tiles.items() for p, s in zip(v, sel_train[(a, b)]) if not s and p[2] >= 0.7]
    test_mid = [(a, b, p[0], p[1]) for (a, b), v in tiles.items() for p, s in zip(v, sel_train[(a, b)]) if not s and 0.5 <= p[2] < 0.7]
    report(theta, idx, None, None, 'poly3 builder (12 tiles/pair)')
    # control: poly3 refitted on the dense weighted training tiles (+ keypoint fallback for pairs without tiles)
    pairs = []
    for (a, b), tl in tiles.items():
        t = [x for x, keep in zip(tl, sel_train[(a, b)]) if keep]
        if len(t) >= 3:
            pairs.append({'a': a, 'b': b, 'xa': ms.centred(np.array([x[0] for x in t]), C.sizes[a]),
                          'xb': ms.centred(np.array([x[1] for x in t]), C.sizes[b]), 'w': W(np.array([x[2] for x in t])) * 12 / len(t)})
        elif (a, b) in C.coarse:
            xa, xb = C.coarse[(a, b)]; pairs.append({'a': a, 'b': b, 'xa': xa, 'xb': xb, 'w': np.full(len(xa), 3.0 / len(xa))})
    target = np.array([C.model.from_similarity(C.theta_sim[C.idx_sim[n]]) for n in C.nodes])
    th2, ix2 = ms.solve_poses(C.nodes, pairs, C.ref, C.model, target=target)
    report(th2, ix2, None, None, 'poly3 dense-weighted')
    for s in (256, 192):
        for lam in (0.25, 1.0, 4.0):
            mesh = Mesh(C.nodes, C.sizes, s); D = fit_mesh(mesh, obs_for(th2, ix2, mesh), lam_s=lam, lam_0=0.02)
            report(th2, ix2, mesh, D, f'dense poly3 + mesh {s}/{lam:g}')


if __name__ == '__main__':
    main()
