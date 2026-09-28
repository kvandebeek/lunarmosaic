import sys, json, pickle, numpy as np
sys.path.insert(0, '.')
from harness import capture
import mosaic_stages as ms
from mesh_proto import Mesh, fit_mesh, residuals, stats
C = capture()
d = pickle.load(open('mesh_inputs.pkl', 'rb')); theta, idx, tiles = d['theta'], d['idx'], d['tiles']
seam = [o for o in json.load(open('build4_seams.json')) if o[6] >= 0.5]
pts = [(o[2], o[3], np.array([o[7], o[8]]), np.array([o[9], o[10]])) for o in seam]
W = lambda r: np.clip(r, 0, 1) ** 4 / 0.8 ** 4
stats(residuals(C, theta, idx, None, None, pts), 'seams · poly3 builder')
allsel = {k: [True] * len(v) for k, v in tiles.items()}          # seam points are independent: fit on all tiles
pairs = []
for (a, b), t in tiles.items():
    if len(t) >= 3:
        pairs.append({'a': a, 'b': b, 'xa': ms.centred(np.array([x[0] for x in t]), C.sizes[a]),
                      'xb': ms.centred(np.array([x[1] for x in t]), C.sizes[b]), 'w': W(np.array([x[2] for x in t])) * 12 / len(t)})
    elif (a, b) in C.coarse:
        xa, xb = C.coarse[(a, b)]; pairs.append({'a': a, 'b': b, 'xa': xa, 'xb': xb, 'w': np.full(len(xa), 3.0 / len(xa))})
target = np.array([C.model.from_similarity(C.theta_sim[C.idx_sim[n]]) for n in C.nodes])
th2, ix2 = ms.solve_poses(C.nodes, pairs, C.ref, C.model, target=target)
stats(residuals(C, th2, ix2, None, None, pts), 'seams · poly3 dense-weighted')
for s in (256, 192):
    mesh = Mesh(C.nodes, C.sizes, s); obs = []
    for (a, b), t in tiles.items():
        if not t: continue
        pa = np.array([x[0] for x in t]); pb = np.array([x[1] for x in t])
        Xa = ms.to_canvas(C.model, th2[ix2[a]], C.sizes[a], pa); Xb = ms.to_canvas(C.model, th2[ix2[b]], C.sizes[b], pb)
        ca, va = mesh.weights(a, pa); cb, vb = mesh.weights(b, pb); obs.append((ca, va, cb, vb, Xa - Xb, W(np.array([x[2] for x in t]))))
    D = fit_mesh(mesh, obs, lam_s=1.0, lam_0=0.02)
    stats(residuals(C, th2, ix2, mesh, D, pts), f'seams · dense poly3 + mesh {s}')
    print('  mesh displacement: RMS %.2f px, max %.2f px' % (np.sqrt((D ** 2).sum(1).mean()), np.hypot(*D.T).max()))
