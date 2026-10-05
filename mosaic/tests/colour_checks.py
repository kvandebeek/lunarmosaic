"""Phase 6 acceptance checks (IMPLEMENTATION_PLAN.md): colour placed panel by panel onto the luminance canvas.

T-PLACE   colour panels at half scale, each displaced by a drift growing to 3 px across the run (dispersion),
          are recovered within 0.25 canvas px; with 1.5 px of radial distortion at the panel corners the pose
          model escalates beyond similarity and held-out residuals stay within 0.35 px
T-DE      placement errors leave "visibly wrong" colour (ΔE*ab > 2.3, lightness weighted; DESIGN.md §3) on under
          0.05 % of the disk
T-COARSE  colour 6× coarser than the canvas still places, within T-PLACE's tolerance × 6
          and: the scale is estimated when no optics give it; per-panel gains and sky are matched; a colour set
          feeding both streams needs no placement
"""
from pathlib import Path
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
import tifffile

import colour_place as cp
import filters as F
from mixed_checks import sharp_disk
from synthetic import _panel_grid, cut_panels


def scene_panels(folder, size=2048, k=2.0, panel_px=400, radial=0.0, gains=None, seed=11):
    """A luminance canvas (L.tif) and colour panels of the same scene at 1/k, each displaced by a drift growing to
    (3, 1.5) canvas px across the run and given `radial` canvas px of distortion at its corners. Returns the scene
    (uint16 RGB), the truth per panel {matrix, k1} and the canvas path."""
    folder.mkdir(parents=True, exist_ok=True)
    scene = sharp_disk(size)
    lum = np.rint(scene.astype(np.float32).mean(axis=2)).astype(np.uint16)
    tifffile.imwrite(folder/'L.tif', np.repeat(lum[..., None], 3, axis=2), photometric='rgb')
    (folder/'colour').mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)
    cut = lambda img: cut_panels(img, panel_px=panel_px, overlap=.3, scales=(k,))
    panels = cut(scene)
    truth = {}
    for i, p in enumerate(panels):
        d = np.array([3.0, 1.5]) * i / max(1, len(panels) - 1)
        shifted = cv2.warpAffine(scene, np.float32([[1, 0, -d[0]], [0, 1, -d[1]]]), (size, size), flags=cv2.INTER_LANCZOS4)
        q = cut(shifted)[i]
        img = q['image'].astype(np.float32) / 65535
        k1 = 0.0
        if radial:
            h, w = img.shape[:2]
            corner = np.hypot((w - 1) / 2, (h - 1) / 2)
            k1 = radial / k / (corner * (corner / (max(w, h) / 2)) ** 2)
            mx, my = _panel_grid(w, h, k1)
            layer = cv2.resize(shifted, (round(size / k), round(size / k)), interpolation=cv2.INTER_AREA).astype(np.float32) / 65535
            x0, y0 = q['origin_xy']
            img = cv2.remap(layer, (mx + x0).astype(np.float32), (my + y0).astype(np.float32), cv2.INTER_LANCZOS4,
                            borderMode=cv2.BORDER_REFLECT)
        if gains is not None:
            img = img * gains[i] + .006                                 # unequal exposures over one sky background
        img = np.clip(img + rng.normal(0, .002, img.shape), 0, 1)
        tifffile.imwrite(folder/'colour'/f"{p['id']}.tif", np.rint(img * 65535).astype(np.uint16), photometric='rgb')
        M = p['panel_to_canvas'].copy()
        M[:2, 2] += d
        truth[p['id']] = dict(matrix=M, k1=k1)
    return scene, truth, folder/'L.tif'


def place(folder, ratio=None, **kw):
    args = types.SimpleNamespace(panels=[str(folder/'colour')], canvas=str(folder/'L.tif'), output=str(folder/'cc.tif'),
                                 report=str(folder/'cc.json'), scale_ratio=ratio, initial=None, workers=4, **kw)
    return cp.run(args, log=lambda s: None)


def truth_errors(doc, truth):
    """Canvas-px error of every placed panel against the truth, on a grid 20 px inside the panel."""
    out = {}
    for name, e in doc['panels'].items():
        if e['state'] != 'placed':
            continue
        pose = cp.Pose(e['transform']['size'], e['transform']['model'], e['transform']['params'])
        w, h = e['transform']['size']
        gx, gy = np.meshgrid(np.linspace(20, w - 21, 9), np.linspace(20, h - 21, 9))
        P = np.stack([gx.ravel(), gy.ravel()], 1)
        k1 = truth[name]['k1']
        if k1:
            c = np.array([(w - 1) / 2, (h - 1) / 2])
            r2 = ((P - c) ** 2).sum(axis=1) / (max(w, h) / 2) ** 2
            P_src = P + k1 * (P - c) * r2[:, None]
        else:
            P_src = P
        T = truth[name]['matrix']
        out[name] = np.hypot(*(pose.to_canvas(P) - (P_src @ T[:2, :2].T + T[:2, 2])).T)
    return out


def on_canvas(rgba, k, shape):
    """A colour image at 1/k sampled onto the canvas grid through K (colour px i at canvas k·i + (k − 1)/2)."""
    X, Y = np.meshgrid(np.arange(shape[1], dtype=np.float32), np.arange(shape[0], dtype=np.float32))
    return cv2.remap(rgba, (X - (k - 1) / 2) / k, (Y - (k - 1) / 2) / k, cv2.INTER_LINEAR)


class Placement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='colour-')
        cls.root = Path(cls.work.name)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_t_place_drift_and_t_de(self):
        folder = self.root/'drift'
        scene, truth, _ = scene_panels(folder)
        doc = place(folder, ratio=2.0)
        placed = [e for e in doc['panels'].values() if e['state'] == 'placed']
        self.assertGreaterEqual(len(placed), .8 * len(truth))
        for e in doc['panels'].values():
            if e['state'] != 'placed':
                self.assertTrue(e['reason'])                               # never dropped silently
        err = np.concatenate(list(truth_errors(doc, truth).values()))
        self.assertLessEqual(float(np.sqrt(np.mean(err ** 2))), .25)
        self.assertLessEqual(doc['summary']['rms_px'], .25)
        self.assertRegex(doc['summary']['text'], r'^Match \d+\.\d\d px · worst \d+\.\d\d px$')
        # T-DE: chroma blurred 3 px and saturated ×2, lightness from the luminance, against perfectly placed colour
        rgba = tifffile.imread(folder/'cc.tif').astype(np.float32) / 65535
        k, shape = doc['k'], scene.shape[:2]
        placed_rgb = on_canvas(np.ascontiguousarray(rgba), k, shape)
        ideal = cv2.resize(scene.astype(np.float32) / 65535, (round(shape[1] / k), round(shape[0] / k)), interpolation=cv2.INTER_AREA)
        ideal_rgb = on_canvas(ideal, k, shape)
        L = F.lstar(np.repeat(scene.astype(np.float32).mean(axis=2, keepdims=True) / 65535, 3, axis=2))

        def chroma(rgb):
            lab = F.rgb_to_lab(np.clip(rgb[..., :3], 0, 1))
            return np.dstack([cv2.GaussianBlur(lab[..., c], (0, 0), 3) * 2 for c in (1, 2)])
        dE = np.hypot(*(chroma(placed_rgb) - chroma(ideal_rgb)).transpose(2, 0, 1))
        disk = (L > 5) & (placed_rgb[..., 3] > .99)
        visible = float((L * (dE > 2.3))[disk].sum() / L[disk].sum())
        self.assertLess(visible, .0005, f'{visible * 100:.3f} % of the disk visibly wrong')
        self.assertGreater(disk.mean(), .4)

    def test_t_place_distortion_escalates_the_model(self):
        folder = self.root/'radial'
        _, truth, _ = scene_panels(folder, size=3072, panel_px=1000, radial=1.5)
        doc = place(folder, ratio=2.0)
        placed = [e for e in doc['panels'].values() if e['state'] == 'placed']
        self.assertEqual(len(placed), len(truth))
        self.assertGreater(sum(e['model'] in ('affine', 'poly3') for e in placed), len(placed) / 2)
        self.assertLessEqual(max(e['held_out_rms'] for e in placed), .35)

    def test_t_coarse_colour_six_times_coarser(self):
        folder = self.root/'coarse'
        _, truth, _ = scene_panels(folder, k=6.0, panel_px=133)
        doc = place(folder, ratio=6.0)
        placed = [e for e in doc['panels'].values() if e['state'] == 'placed']
        self.assertGreaterEqual(len(placed), .8 * len(truth))
        err = np.concatenate(list(truth_errors(doc, truth).values()))
        self.assertLessEqual(float(np.sqrt(np.mean(err ** 2))), .25 * 6)
        with tifffile.TiffFile(folder/'cc.tif') as t:
            self.assertEqual(t.pages[0].shape[:3], (342, 342, 4))      # ⌈2048 / 6⌉, RGBA

    def test_scale_estimate_gains_and_sky(self):
        folder = self.root/'gains'
        rng = np.random.default_rng(4)
        gains = [np.array([1, 1, 1]) * (1 + rng.uniform(-.12, .12)) * (1 + rng.uniform(-.04, .04, 3)) for _ in range(16)]
        scene, truth, _ = scene_panels(folder, gains=gains)
        doc = place(folder)                                             # no ratio given: estimated
        self.assertIn('estimated', doc['scale_source'])
        self.assertAlmostEqual(doc['scale_ratio'], 2.0, delta=.02)
        # matched panels: each channel's ratio to the scene is the same all over the disk. (Its level is the reference
        # panel's: placement makes panels agree with each other; absolute colour is white balance's job.)
        rgba = tifffile.imread(folder/'cc.tif').astype(np.float32) / 65535
        ideal = cv2.resize(scene.astype(np.float32) / 65535, rgba.shape[1::-1], interpolation=cv2.INTER_AREA)
        on = (rgba[..., 3] > .99) & (ideal.mean(axis=2) > .1) & (rgba[..., :3].min(axis=2) > .02)
        inner = on & (cv2.erode(on.astype(np.uint8), np.ones((33, 33), np.uint8)) > 0)
        for c in range(3):
            ratio = cv2.GaussianBlur(rgba[..., c], (0, 0), 8) / np.maximum(cv2.GaussianBlur(ideal[..., c], (0, 0), 8), 1e-3)
            lo, hi = np.percentile(ratio[inner], [5, 95])
            self.assertLess(float(hi / lo), 1.05, f'channel {c}: gains of ±12 % left a {hi / lo - 1:.1%} spread')
        self.assertGreater(doc['photometry']['overlaps'], 10)


class Application(unittest.TestCase):
    def test_a_set_feeding_both_streams_is_its_own_colour(self):
        from app import Application as App
        with tempfile.TemporaryDirectory(prefix='both-') as tmp:
            root = Path(tmp)
            rgb = sharp_disk(1024)
            tifffile.imwrite(root/'moon.tif', rgb, photometric='rgb')
            app = App()
            try:
                app.select(root/'moon.lunarmosaic', True, 'Both')
                app.sets_op(dict(op='update', set=dict(id='RGB', stream='both')))
                app.inputs('RGB', [str(root/'moon.tif')])
                with self.assertRaises(ValueError):
                    app.inputs('L', [str(root/'moon.tif')])               # one colour mosaic is all the data
                self.assertEqual(app.build(['RGB']), {'L': 'built', 'RGB': 'built'})
                p = app.project
                self.assertEqual(p.stream('colour')['mosaic'], p.stream('luminance')['mosaic'])
                self.assertEqual((p.match['method'], p.match['matrix']), ('both', np.eye(3).tolist()))
                scene = app.get_scene()
                out, _ = scene.render_edits(np.eye(3), (1024, 1024), dict(p.edits))
                lab_in, lab_out = F.rgb_to_lab(rgb.astype(np.float32) / 65535), F.rgb_to_lab(out)
                lit = lab_in[..., 0] > 20
                self.assertLess(float(np.abs(lab_in[..., 1:] - lab_out[..., 1:])[lit].mean()), .3)   # its own colour
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
