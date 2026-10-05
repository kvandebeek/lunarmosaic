"""Misregistration measured AT the seams of a finished build: where the region map switches from panel A to B,
cut the same terrain from both panels (full resolution, final poses) and phase-correlate."""
import os, sys, json, numpy as np, cv2
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import mosaic_common as mc, mosaic_stages as ms
lay = stem = lab = q = names = bx = by = off = sizes = model = th = pts = L = None
T = 128
win = None


def load(n):
    x, _, _ = mc.read_image(lay['panels'][n]['path']); return n, mc.luminance(x).astype(np.float32)

def measure(p):
    x, y, i, j = p; a, b = names[i], names[j]
    X = np.array([[x * q + (q - 1) / 2 + bx, y * q + (q - 1) / 2 + by]], float)   # canvas px
    pa = ms.from_canvas(model, th[a], sizes[a], X, off)[0]
    mp = lambda P: ms.from_canvas(model, th[b], sizes[b], ms.to_canvas(model, th[a], sizes[a], P, off), off)
    x0, y0 = int(pa[0]) - T // 2, int(pa[1]) - T // 2
    if x0 < 16 or y0 < 16 or x0 + T > sizes[a][0] - 16 or y0 + T > sizes[a][1] - 16: return None
    A = L[a][y0:y0 + T, x0:x0 + T]
    if A.std() < 0.004: return None
    Tab = ms.local_affine(mp, np.array([x0 + T / 2, y0 + T / 2]))
    cor = np.array([[x0, y0], [x0 + T, y0], [x0, y0 + T], [x0 + T, y0 + T]], float) @ Tab[:2, :2].T + Tab[:2, 2]
    bx0, by0 = int(cor[:, 0].min()) - 12, int(cor[:, 1].min()) - 12
    bx1, by1 = int(cor[:, 0].max()) + 13, int(cor[:, 1].max()) + 13
    if bx0 < 0 or by0 < 0 or bx1 > sizes[b][0] or by1 > sizes[b][1]: return None
    Mt = np.hstack([Tab[:2, :2], (Tab[:2, :2] @ [x0, y0] + Tab[:2, 2] - [bx0, by0])[:, None]])
    B = cv2.warpAffine(L[b][by0:by1, bx0:bx1], Mt, (T, T), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP)
    s = np.zeros(2)
    for _ in range(3):                                   # iterated: ~0.05 px measurement accuracy
        Bw = cv2.warpAffine(B, np.float32([[1, 0, -s[0]], [0, 1, -s[1]]]), (T, T), flags=cv2.INTER_LANCZOS4)
        (dx, dy), r = mc.phase_shift(A - A.mean(), Bw - Bw.mean(), win); s += (dx, dy)
    pa_c = np.array([x0 + (T - 1) / 2, y0 + (T - 1) / 2]); pb_c = Tab[:2, :2] @ (pa_c + s) + Tab[:2, 2]
    return (x * q + bx - 0, y * q + by, a, b, s[0], s[1], r, pa_c[0], pa_c[1], pb_c[0], pb_c[1])


def main():
    global lay, stem, lab, q, names, bx, by, off, sizes, model, th, pts, L, win
    lay = json.load(open(sys.argv[1])); stem = sys.argv[1][:-len('_layout.json')]
    lab = cv2.imread(stem + '_labels.png', -1); q = lay['label_scale']; names = lay['label_index']
    bx, by = lay['output_bbox'][:2]; off = lay['canvas']['offset']
    sizes = {n: tuple(v['size']) for n, v in lay['panels'].items()}
    model = ms.make_model(lay['model'], sizes); th = {n: np.array(v['pose_params']) for n, v in lay['panels'].items()}
    # seam points: label boundaries between two panels, on the lunar disk
    disk = cv2.imread(stem + '.tif', cv2.IMREAD_REDUCED_COLOR_4)
    lum = disk.max(2) > (12 if disk.dtype == np.uint8 else 3000)
    h4, w4 = lab.shape; lum = cv2.resize(lum.astype(np.uint8), (w4, h4), interpolation=cv2.INTER_NEAREST) > 0
    pts = []
    for dy, dx in ((0, 1), (1, 0)):
        a = lab[:h4 - dy, :w4 - dx]; b = lab[dy:, dx:]
        m = (a != b) & (a < len(names)) & (b < len(names)) & lum[:h4 - dy, :w4 - dx]
        ys, xs = np.nonzero(m)
        for y, x in zip(ys, xs): pts.append((x, y, int(a[y, x]), int(b[y, x])))
    pts = np.array(pts)
    # thin out: one point per 64 px of seam (16 label cells), random subset
    keep = {}
    for x, y, i, j in pts:
        keep.setdefault((min(i, j), max(i, j), x // 16, y // 16), (x, y, i, j))
    pts = list(keep.values()); print('seam sample points', len(pts), flush=True)
    need = sorted({names[p[2]] for p in pts} | {names[p[3]] for p in pts})
    with ThreadPoolExecutor(8) as ex: L = dict(ex.map(load, need))
    win = cv2.createHanningWindow((T, T), cv2.CV_32F)
    with ThreadPoolExecutor(8) as ex: out = [o for o in ex.map(measure, pts) if o is not None]
    S = np.array([(o[4], o[5], o[6]) for o in out]); good = S[:, 2] > 0.1; e = np.hypot(S[good, 0], S[good, 1])
    print(f'seam points measured {good.sum()} (of {len(out)}; response > 0.1): misregistration RMS {np.sqrt((e**2).mean()):.3f} px, '
          f'median {np.median(e):.3f}, p90 {np.percentile(e,90):.3f}, p99 {np.percentile(e,99):.3f}, share > 1 px {(e>1).mean()*100:.1f} %, > 2 px {(e>2).mean()*100:.1f} %')
    json.dump([list(map(lambda v: v if isinstance(v, str) else float(v), o)) for o in out], open(stem.split('/')[-1] + '_seams.json', 'w'))


if __name__ == '__main__':
    main()
