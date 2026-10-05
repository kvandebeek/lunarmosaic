"""Phase 5 acceptance checks (IMPLEMENTATION_PLAN.md): luminance sets at different pixel scales in one mosaic.

T-GATE   the pair gate expects scale_rel[a] / scale_rel[b] for a transform a -> b, not the inverse
T-SHARP  tiers follow measured resolution: sharpening a coarse set never wins it the fine set's cells, and an
         oversampled, blurred set loses its overlaps to a coarser, sharper one
T-MIX    fine and 2× coarser panels build into one mosaic on the fine grid, placed within 0.5 px of the truth,
         the fine set supplying what it covers
         and a single-scale build is the same with or without a panel scales file; --canvas-scale, the size cap

The scenes carry terrain-like detail (power falling as f^-2.5, optics σ 0.7 px) and noise per panel. The plain
synthetic disk would not do: its texture stops near 0.24 cycles/px, so a 2× coarser copy resolves almost all of it
and rightly shares its tier. Nor would a flat-spectrum texture: reduced 2×, its spectrum stays flat to the sampling
limit, which leaves the information cutoff nothing to measure against (such a panel is reported as not measurable).
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
import tifffile

import measurements as ms
import mosaic_common as mc
import mosaic_stages as mst
from mosaic_builder import load_panel_scales
from synthetic import band_limited_texture, cut_panels, lunar_disk, terrain

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ['--model', 'poly3+mesh', '--proxy-factor', '1', '--no-preview', '--no-display-tif', '--no-north-up']


def sharp_disk(size=2048, seed=924):
    """The synthetic disk with terrain-like detail added (uint16 RGB)."""
    d = lunar_disk(size, seed=seed)
    base = d['image'].astype(np.float32) / 65535
    t = terrain(size, 2.5, .7, seed=seed + 7)
    return np.rint(np.clip(base * (1 + .3 * t[..., None]) * d['lit'][..., None], 0, 1) * 65535).astype(np.uint16)


def noisy(img, rng, sigma=.002):
    return np.rint(np.clip(img.astype(np.float32) / 65535 + rng.normal(0, sigma, img.shape), 0, 1) * 65535).astype(np.uint16)


def build(inputs, out, *extra):
    done = subprocess.run([sys.executable, str(ROOT/'mosaic'/'mosaic_builder.py'), *map(str, inputs), '-o', str(out), *FLAGS,
                           *map(str, extra)], capture_output=True, text=True, cwd=str(ROOT), timeout=900)
    return done.returncode, done.stdout + done.stderr


def scene_texture(size=1536, seed=41):
    """Lunar-like brightness: terrain-like detail on a gentle large-scale variation, float32 in 0–1."""
    yy, xx = np.mgrid[:size, :size] / size
    base = .4 + .15 * np.sin(5 * xx + 1) * np.cos(4 * yy)
    return np.clip(base * (1 + .35 * terrain(size, 2.5, .7, seed=seed)), 0, 1).astype(np.float32)


def usm(img, amount, sigma=1.0):
    return np.clip(img + amount * (img - cv2.GaussianBlur(img, (0, 0), sigma)), 0, 1)


class Gate(unittest.TestCase):
    def test_t_gate_expected_ratio_follows_the_transform_direction(self):
        disk = sharp_disk(1536).astype(np.float32).mean(axis=2) / 65535
        fine = disk[256:1280, 256:1280]                                  # 1024 px at scale 1
        coarse = cv2.resize(disk[128:1408, 128:1408], (640, 640), interpolation=cv2.INTER_AREA)    # scale 2

        def feats(L):
            P8, pm = mc.make_proxy(L, L > .02, 1)
            return mc.detect_features(P8, pm, 4000), pm
        (fc, pc), (ff, pf) = feats(coarse), feats(fine)
        r, why = mc.verify_pair(fc, ff, pc, pf, expected=2.0)            # coarse a -> fine b: scale 2
        self.assertIsNotNone(r, why)
        self.assertAlmostEqual(r['scale'], 2.0, delta=0.01)
        r, why = mc.verify_pair(ff, fc, pf, pc, expected=2.0)            # fine -> coarse with the coarse->fine ratio
        self.assertEqual((r, why), (None, 'scale'))
        r, why = mc.verify_pair(ff, fc, pf, pc, expected=0.5)
        self.assertIsNotNone(r, why)
        self.assertEqual(mc.verify_pair(fc, ff, pc, pf)[1], 'scale')     # the old gate (expected 1) refuses it

    def test_panel_scales_file_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'scales.json'

            def check(doc):
                path.write_text(json.dumps(doc))
                return load_panel_scales(str(path))
            ok = check(dict(panels=[dict(path='/a.tif', set='s1', scale_rel=1), dict(path='/b.tif', set='s2', scale_rel=2.07)],
                            canvas_set='s1'))
            self.assertEqual(ok[0][str(Path('/b.tif').resolve())], ('s2', 2.07))
            for bad in (dict(panels=[]), dict(panels=[dict(path='/a.tif', set='s1', scale_rel=.5)], canvas_set='s1'),
                        dict(panels=[dict(path='/a.tif', set='s1', scale_rel=2)], canvas_set='s1'),
                        dict(panels=[dict(path='/a.tif', set='s1', scale_rel=1)], canvas_set='s9'),
                        dict(panels=[dict(path='/a.tif', set='s1', scale_rel=1), dict(path='/b.tif', set='s1', scale_rel=2)],
                             canvas_set='s1')):
                with self.assertRaises(ValueError):
                    check(bad)


class Sharpness(unittest.TestCase):
    """select_regions with tiers from measured resolution, on one fine and one 2× coarser panel of the same area."""

    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(3)
        cls.scene = scene_texture()
        cls.add = staticmethod(lambda img: np.clip(img + rng.normal(0, .002, img.shape), 0, 1).astype(np.float32))
        cls.pool = ThreadPoolExecutor(2)

    @classmethod
    def tearDownClass(cls):
        cls.pool.shutdown()

    def compete(self, fine, coarse, use_tiers=True):
        """Labels of a 1536 px canvas: fine (1024 px at canvas 256…1280) against coarse (768 px covering it all)."""
        L = {'F': fine.astype(np.float16), 'C': coarse.astype(np.float16)}
        P = {'F': np.array([[1, 0, 256], [0, 1, 256], [0, 0, 1.]]), 'C': np.array([[2, 0, .5], [0, 2, .5], [0, 0, 1.]])}
        details = {'F': ms.set_detail([fine], 1)[0], 'C': ms.set_detail([coarse], 2)[0]}
        tiers = ms.tiers(details) if use_tiers else None
        sizes = {n: v.shape[::-1] for n, v in L.items()}
        lab, _ = mst.select_regions(['F', 'C'], sizes, L, P, (1536, 1536), 4, self.pool, tiers=tiers)
        fine_valid = np.zeros(lab.shape, bool)
        fine_valid[(256 + 40) // 4:(1280 - 40) // 4, (256 + 40) // 4:(1280 - 40) // 4] = True
        return lab, fine_valid, details, tiers

    def test_t_sharp_sharpening_a_coarse_panel_wins_it_nothing(self):
        fine = self.add(self.scene[256:1280, 256:1280])
        base = cv2.resize(self.scene, (768, 768), interpolation=cv2.INTER_AREA)
        shares = {}
        for name, amount in (('unsharpened', 0), ('USM 100 %', 1.0), ('USM 200 %', 2.0)):
            coarse = self.add(usm(base, amount)) if amount else self.add(base)
            lab, fine_valid, details, tiers = self.compete(fine, coarse)
            self.assertEqual(tiers, [['F'], ['C']], f'{name}: {details}')
            self.assertEqual(int((lab[fine_valid] == 1).sum()), 0, f'{name}: the coarse panel won fine cells')
            shares[name] = float((self.compete(fine, coarse, use_tiers=False)[0][fine_valid] == 1).mean())
        # without tiers, per-pixel sharpness hands the sharpened coarse panel a share of what the fine one covers
        self.assertGreater(shares['USM 200 %'], .05, shares)

    def test_t_sharp_oversampled_blurred_set_loses_to_a_coarser_sharper_one(self):
        blurred = self.add(cv2.GaussianBlur(self.scene[256:1280, 256:1280], (0, 0), 3))
        coarse = self.add(cv2.resize(self.scene, (768, 768), interpolation=cv2.INTER_AREA))
        lab, fine_valid, details, tiers = self.compete(blurred, coarse)
        self.assertEqual(tiers, [['C'], ['F']], details)
        self.assertEqual(int((lab[fine_valid] == 0).sum()), 0, 'the blurred set kept cells the coarser, sharper set covers')

    def test_a_spectrum_flat_to_the_sampling_limit_is_not_measurable(self):
        # white texture reduced 2×: no noise floor below the signal; read naively it would look 30× blurrier
        white = band_limited_texture(1536, .42, signal_rms=.05, noise_rms=0, seed=5)['image']
        coarse = self.add(cv2.resize(white, (768, 768), interpolation=cv2.INTER_AREA))
        self.assertIsNone(ms.panel_detail(coarse))
        self.assertEqual(ms.set_detail([coarse], 2), (4.0, 'not measurable: 2 px of the set'))
        self.assertAlmostEqual(ms.panel_detail(self.add(white)), 1 / .42, delta=.1)


def mixed_panels(folder):
    """Nine fine panels (set s0) and four 2× coarser ones (s1) reaching further; written as 16-bit TIFFs."""
    folder.mkdir()
    rng = np.random.default_rng(7)
    entries = []
    for p in cut_panels(sharp_disk(), panel_px=1024, overlap=.45, scales=(1, 2)):
        if p['set'] == 's0' and max(p['origin_xy']) > 1000:         # the coarse set reaches further than the fine one
            continue
        path = folder/f"{p['id']}.tif"
        tifffile.imwrite(path, noisy(p['image'], rng), photometric='rgb')
        entries.append(dict(path=str(path), set=p['set'], scale_rel=round(p['scale_rel'], 6), truth=p['panel_to_canvas']))
    return entries


class Mixed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='mixed-')
        root = cls.root = Path(cls.work.name)
        entries = mixed_panels(root/'panels')
        cls.truth = {Path(e['path']).stem: e['truth'] for e in entries}
        (root/'scales.json').write_text(json.dumps(dict(panels=[{k: e[k] for k in ('path', 'set', 'scale_rel')} for e in entries],
                                                         canvas_set='s0')))
        cls.code, cls.log = build([root/'panels'], root/'mix.tif', '--panel-scales', root/'scales.json')
        if cls.code == 0:
            cls.layout = json.loads((root/'mix_layout.json').read_text())
            cls.sets = json.loads((root/'mix_sets.json').read_text())
            cls.labels = cv2.imread(str(root/'mix_labels.png'), cv2.IMREAD_UNCHANGED)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        self.assertEqual(self.code, 0, self.log[-3000:])

    def test_t_mix_one_mosaic_on_the_fine_grid(self):
        lay = self.layout
        self.assertEqual(len(lay['panels']), len(self.truth))                  # every panel joined
        self.assertTrue(lay['reference'].startswith('s0'))                     # the canvas scale is the fine set's
        tiles = [int(n) for n in re.findall(r'round \d+: (\d+) tiles on \d+ pairs', self.log)]
        self.assertTrue(tiles and max(tiles) > 0 and re.search(r'mesh: [1-9]\d* tiles', self.log), self.log[-2000:])
        self.assertEqual(self.sets['tiers'], [['s0'], ['s1']])
        self.assertEqual(len(self.sets['label_to_set']), len(lay['label_index']))
        gains = [float(v) for v in re.findall(r'gains R ([\d.]+)–([\d.]+)', self.log)[0]]
        self.assertLess(max(abs(g - 1) for g in gains), .02)                   # the panels' brightness truly agrees

    def test_t_mix_placement_and_labels_against_the_truth(self):
        lay, sets = self.layout, self.sets
        setof = dict(zip(lay['label_index'], sets['label_to_set']))
        off = np.array(lay['canvas']['offset'])
        model = mst.make_model(lay['model'], {n: tuple(v['size']) for n, v in lay['panels'].items()})
        pts, src, owner = [], [], []
        for n, v in lay['panels'].items():
            w, h = v['size']
            gx, gy = np.meshgrid(np.linspace(40, w - 41, 12), np.linspace(40, h - 41, 12))
            p = np.stack([gx.ravel(), gy.ravel()], 1)
            pts.append(mst.to_canvas(model, np.array(v['pose_params']), tuple(v['size']), p, off))
            src.append((np.c_[p, np.ones(len(p))] @ np.asarray(self.truth[n]).T)[:, :2])
            owner += [setof[n]] * len(p)
        pts, src, owner = np.vstack(pts), np.vstack(src), np.array(owner)
        fine = owner == 's0'
        A, _ = cv2.estimateAffinePartial2D(pts[fine].astype(np.float32), src[fine].astype(np.float32), method=cv2.LMEDS)
        self.assertAlmostEqual(float(np.hypot(A[0, 0], A[1, 0])), 1.0, delta=1e-3)    # canvas px = fine px
        err = np.hypot(*((np.c_[pts, np.ones(len(pts))] @ np.vstack([A, [0, 0, 1]]).T)[:, :2] - src).T)
        for s in ('s0', 's1'):
            self.assertLessEqual(float(np.sqrt((err[owner == s] ** 2).mean())), .5, f'set {s}')   # seams within 0.5 px
        lab, q, bbox = self.labels, lay['label_scale'], lay['output_bbox']
        cy, cx = np.mgrid[:lab.shape[0], :lab.shape[1]]
        X, Y = (cx + bbox[0] // q) * q + (q - 1) / 2, (cy + bbox[1] // q) * q + (q - 1) / 2
        covered = np.zeros(lab.shape, bool)
        for n, v in lay['panels'].items():
            if setof[n] == 's0':
                M = np.linalg.inv(np.array(v['panel_to_canvas']))
                u, t = M[0, 0] * X + M[0, 1] * Y + M[0, 2], M[1, 0] * X + M[1, 1] * Y + M[1, 2]
                c, (w, h) = v['crop_px'] + 8, v['size']
                covered |= (u >= c) & (u <= w - 1 - c) & (t >= c) & (t <= h - 1 - c)
        fine_labels = [i for i, n in enumerate(lay['label_index']) if setof[n] == 's0']
        self.assertGreaterEqual(float(np.isin(lab[covered], fine_labels).mean()), .95)
        beyond = (lab != 65535) & ~covered
        self.assertGreater(float((~np.isin(lab[beyond], fine_labels)).mean()), .9)   # the coarse set fills the rest

    def test_canvas_scale_and_size_cap(self):
        code, log = build([self.root/'panels'], self.root/'cap.tif', '--panel-scales', self.root/'scales.json',
                          '--max-canvas-mpx', '1')
        self.assertEqual(code, 4, log[-1500:])
        self.assertIn('above the confirmed 1 Mpx', log)
        self.assertFalse((self.root/'cap.tif').exists())
        code, log = build([self.root/'panels'], self.root/'half.tif', '--panel-scales', self.root/'scales.json',
                          '--canvas-scale', '0.5')
        self.assertEqual(code, 0, log[-1500:])
        full, half = (json.loads((self.root/f'{n}_layout.json').read_text())['canvas'] for n in ('mix', 'half'))
        self.assertLessEqual(abs(half['width'] - full['width'] / 2), 3)
        self.assertLessEqual(abs(half['height'] - full['height'] / 2), 3)

    def test_a_panel_missing_from_the_scales_file_is_refused(self):
        doc = json.loads((self.root/'scales.json').read_text())
        doc['panels'] = doc['panels'][1:]
        (self.root/'partial.json').write_text(json.dumps(doc))
        code, log = build([self.root/'panels'], self.root/'partial.tif', '--panel-scales', self.root/'partial.json')
        self.assertNotEqual(code, 0)
        self.assertIn('missing from the panel scales file', log)


class Application(unittest.TestCase):
    """Phase 5 in the application: luminance sets, their scales, the canvas estimate, one joint build, the
    resolution view."""

    @classmethod
    def setUpClass(cls):
        from app import Application as App
        cls.work = tempfile.TemporaryDirectory(prefix='mixed-app-')
        root = cls.root = Path(cls.work.name)
        entries = mixed_panels(root/'panels')
        cls.fine = [e['path'] for e in entries if e['set'] == 's0']
        cls.coarse = [e['path'] for e in entries if e['set'] == 's1']
        cls.app = App()
        cls.app.select(root/'moon.lunarmosaic', True, 'Mixed')
        cls.app.inputs('L', cls.fine, dict(pixel_um=2.9, focal_mm=2000))      # 0.299″/px
        cls.app.sets_op(dict(op='create', set=dict(stream='luminance', label='Wide field')))
        cls.app.inputs('s2', cls.coarse)                                        # no optics: estimated (D10)

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls.work.cleanup()

    def test_sets_scales_estimate_canvas_and_a_joint_build(self):
        app, p = self.app, self.app.project
        self.assertEqual([(x['id'], x['stream']) for x in p.data['sets']], [('L', 'luminance'), ('RGB', 'colour'), ('s2', 'luminance')])
        self.assertEqual(p.stream('luminance')['state'], 'ready')
        with self.assertRaises(ValueError):
            app.sets_op(dict(op='delete', set=dict(id='L')))
        with self.assertRaises(ValueError):
            app.sets_op(dict(op='create', set=dict(stream='colour')))
        estimate = app.canvas_estimate(p.sets_of('luminance'), {x['id']: p.input_paths(x['id']) for x in p.sets_of('luminance')},
                                       p.stream('luminance'))
        self.assertIn('needs its optics', estimate['problem'])                  # s2's scale is not known yet
        # D10: s2 measured against L on matched panels, then confirmed before a mixed build may run
        result = app.estimate_scales()
        self.assertAlmostEqual(result['s2']['ratio'], 2.0, delta=0.02)
        self.assertGreaterEqual(result['s2']['pairs'], 2)
        self.assertEqual(app.build(['L']), {'L': 'failed'})
        self.assertIn('Confirm the estimated scale', p.stream('luminance')['error'])
        p.stream('luminance').update(state='ready', error=None)
        app.sets_op(dict(op='confirm', set=dict(id='s2')))
        estimate = app.canvas_estimate(p.sets_of('luminance'), {x['id']: p.input_paths(x['id']) for x in p.sets_of('luminance')},
                                       p.stream('luminance'))
        self.assertIsNone(estimate['problem'])
        rel = {x['id']: x['rel'] for x in estimate['sets']}
        self.assertEqual(rel['L'], 1.0)
        self.assertAlmostEqual(rel['s2'], 2.0, delta=0.02)
        self.assertEqual(estimate['finest']['id'], 'L')
        side = 2010 / (206.265 * 2.9 / 2000)
        self.assertEqual(estimate['lower_bound']['px'], round(side))
        self.assertFalse(estimate['needs_confirmation'])
        # the size cap, confirmed with the build, stops it before blending
        self.assertEqual(app.build(['L'], dict(max_canvas_mpx=1.0)), {'L': 'failed'})
        self.assertIn('above the confirmed 1 Mpx', p.stream('luminance')['error'])
        p.stream('luminance').update(state='ready', error=None, max_canvas_mpx=None)
        self.assertEqual(app.build(['L']), {'L': 'built'})
        st = p.stream('luminance')
        self.assertEqual((st['mosaic'], st['sets_report']), ('derived/L.tif', 'derived/L_sets.json'))
        self.assertAlmostEqual(st['canvas_arcsec_px'], 206.265 * 2.9 / 2000, places=6)
        report = json.loads(p.path(st['sets_report']).read_text())
        self.assertEqual(report['tiers'], [['L'], ['s2']])
        scales = json.loads(p.path('derived/L_panel_scales.json').read_text())
        self.assertEqual(scales['canvas_set'], 'L')
        self.assertEqual(sorted({e['set'] for e in scales['panels']}), ['L', 's2'])
        # the resolution view tints by set, with a legend giving each set's pixel scale
        scene = app.get_scene()
        matrix, size, _ = scene.geometry(p.edits)
        m = np.diag([.25, .25, 1]) @ matrix
        rgb, info = scene.render_view(m, (round(size[0] * .25), round(size[1] * .25)), p.chain_doc(), 'resolution')
        legend = {x['id']: x for x in info['sets']}
        self.assertEqual(set(legend), {'L', 's2'})
        self.assertAlmostEqual(legend['s2']['arcsec_px'], 2 * 206.265 * 2.9 / 2000, delta=0.01)
        plain, _ = scene.render_view(m, rgb.shape[1::-1], p.chain_doc(), 'composite')
        tinted = np.abs(rgb - plain).max(axis=2) > .01
        self.assertGreater(tinted.mean(), .2)
        # the interface's schema-3 view still describes one luminance set, with the stream's build state
        public = p.public()
        self.assertEqual((public['sets']['L']['state'], public['sets']['L']['mosaic']), ('built', 'derived/L.tif'))
        self.assertEqual(len(public['schema4']['sets']), 3)


class SingleScale(unittest.TestCase):
    def test_a_one_set_scales_file_changes_nothing(self):
        with tempfile.TemporaryDirectory(prefix='single-') as tmp:
            root = Path(tmp)
            (root/'panels').mkdir()
            paths = []
            for p in cut_panels(lunar_disk(2048)['image'], panel_px=1024, overlap=.45):
                paths.append(root/'panels'/f"{p['id']}.tif")
                tifffile.imwrite(paths[-1], p['image'], photometric='rgb')
            (root/'one.json').write_text(json.dumps(dict(panels=[dict(path=str(p), set='L', scale_rel=1) for p in paths],
                                                          canvas_set='L')))
            code_a, log_a = build([root/'panels'], root/'a.tif')
            code_b, log_b = build([root/'panels'], root/'b.tif', '--panel-scales', root/'one.json')
            self.assertEqual((code_a, code_b), (0, 0), (log_a + log_b)[-3000:])
            a, b = (cv2.imread(str(root/n), cv2.IMREAD_UNCHANGED) for n in ('a.tif', 'b.tif'))
            self.assertTrue(np.array_equal(a, b))
            self.assertTrue((root/'b_sets.json').is_file() and not (root/'a_sets.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
