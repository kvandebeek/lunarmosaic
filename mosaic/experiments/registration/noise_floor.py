import json, os, sys, pickle, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import mosaic_stages as ms


def main():
    d = pickle.load(open('mesh_inputs.pkl', 'rb')); theta, idx, tiles = d['theta'], d['idx'], d['tiles']
    lay = json.load(open('/private/tmp/claude-501/-Volumes-Astro-25092026-moon-panels/aff634c5-9249-4931-bfd7-7d973ace31e1/scratchpad/lines/build4_layout.json'))
    sizes = {n: tuple(v['size']) for n, v in lay['panels'].items()}; model = ms.make_model('poly3', sizes)
    diffs, resp = [], []
    for (a, b), tl in tiles.items():
        if len(tl) < 2: continue
        pa = np.array([t[0] for t in tl]); pb = np.array([t[1] for t in tl]); r = np.array([t[2] for t in tl])
        v = ms.to_canvas(model, theta[idx[a]], sizes[a], pa) - ms.to_canvas(model, theta[idx[b]], sizes[b], pb)
        for i in range(len(tl)):
            dd = np.hypot(*(pa - pa[i]).T); j = np.nonzero((dd > 90) & (dd < 100))[0]
            for k in j[j > i]: diffs.append(v[i] - v[k]); resp.append(min(r[i], r[k]))
    D = np.array(diffs); resp = np.array(resp)
    for lo, hi in [(0.15, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 2)]:
        s = (resp >= lo) & (resp < hi)
        sig = np.sqrt((D[s] ** 2).sum(1).mean() / 2)
        print(f'response {lo}-{hi}: neighbour pairs {s.sum():6d} · per-tile noise (2-D RMS) ≤ {sig:.3f} px · median |diff|/√2 {np.median(np.hypot(*D[s].T))/np.sqrt(2):.3f}')


if __name__ == '__main__':
    main()
