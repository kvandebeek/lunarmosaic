"""Phase 3 acceptance (plan §5, phase 3): T-WB, T-CUT, T-SHIFT, T-PSF, T-DECONV, T-LEVELS, T-CURVES, T-ORDER, and
T-TILE for the new blocks."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE))
import blocks as B  # noqa: E402
import chain  # noqa: E402
import deconv as D  # noqa: E402
import measurements as ms  # noqa: E402
from synthetic import band_limited_texture, lunar_disk, white_balance_probe  # noqa: E402


def doc_of(path, *entries):
    d = chain.empty()
    d['chains'][path] = list(entries)
    return d


def run(img16, doc):
    source = chain.ArraySource(img16)
    stats = chain.prepare(source, doc)
    return chain.render(source, stats, doc, 0, 0, source.h, source.w), stats


def texture(n=512, seed=3):
    rng = np.random.default_rng(seed)
    t = np.zeros((n, n), np.float32)
    for s, a in ((1.0, .04), (2.5, .07), (8, .10), (25, .12)):
        g = cv2.GaussianBlur(rng.standard_normal((n, n)).astype(np.float32), (0, 0), s)
        t += a * g / g.std()
    return 0.4 + t


def shifted(img, dx, dy):
    return cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), img.shape[1::-1], flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_REFLECT)


class WhiteBalance(unittest.TestCase):
    """T-WB on the Appendix F probe: PixInsight ColorCalibration with the background as the fixed point."""

    @classmethod
    def setUpClass(cls):
        cls.probe = white_balance_probe(256)
        cls.img = np.round(cls.probe['image'] * 65535).astype(np.uint16)
        cls.x = cls.img.astype(np.float32) / 65535

    def balance(self, **params):
        e = chain.entry('white_balance', params)
        out, stats = run(self.img, doc_of('final', e))
        return out, stats.blocks[e['id']]

    def test_gains_neutral_white_and_untouched_sky(self):
        out, st = self.balance()
        np.testing.assert_allclose(st['ratios'], [1.1369, 1.0800, 1.0], rtol=2e-3)
        surface, sky = self.probe['surface'], self.probe['background']
        core = cv2.erode(surface.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        net = out[core].mean(axis=0) - st['B']                    # background-subtracted surface
        self.assertLess(float(np.abs(net / net.mean() - 1).max()), 1e-3)
        self.assertLess(float(np.abs(out[sky] - self.x[sky]).max()), 1e-5)
        before = self.x[core].mean(axis=0) - st['B']
        self.assertLess(abs(float(net.mean() / before.mean()) - 1), 1e-3)

    def test_neutralised_background_reaches_the_target(self):
        out, _ = self.balance(neutralize_background=True, background_target=0.02)
        self.assertLess(float(np.abs(out[self.probe['background']] - 0.02).max()), 1e-4)

    def test_structure_detection_is_refused(self):
        with self.assertRaises(ValueError):
            chain.entry('white_balance', {'structure_detection': True})


class Measurements(unittest.TestCase):
    def test_cutoff_and_suggested_layer(self):
        """T-CUT."""
        for fc in (0.15, 0.25, 0.40):
            t = band_limited_texture(1024, fc)
            self.assertLess(abs(ms.cutoff(t['image']) - fc), 0.03, fc)
        self.assertEqual(ms.suggest_layer(0.443), 2)
        self.assertEqual(ms.suggest_layer(0.173), 3)

    def test_rgb_shift_estimators(self):
        """T-SHIFT: both estimators find a sub-pixel shift, with equal blur and with a softer, flatter red."""
        base = texture()
        for blur, gain, tol in ((0.0, 1.0, 0.05), (1.2, 0.85, 0.10)):
            r = shifted(base, 0.30, -0.20)
            if blur:
                r = cv2.GaussianBlur(r, (0, 0), blur)
            r = 0.4 + gain * (r - 0.4)
            est = ms.rgb_shift(np.dstack([r, base, shifted(base, -0.25, 0.15)]))
            for ch, truth in (('r', (0.30, -0.20)), ('b', (-0.25, 0.15))):
                for name, e in zip(('phase correlation', 'Lucas-Kanade'), est[ch]):
                    self.assertLess(float(np.abs(e - truth).max()), tol, f'{ch} {name} blur {blur}: {e}')

    def test_rgb_shift_block_corrects_and_refuses_without_texture(self):
        base = texture(1024)
        img = np.dstack([shifted(base, 0.30, -0.20), base, shifted(base, -0.25, 0.15)])
        blk = B.BLOCKS['rgb_shift']
        p = blk.normalise({})
        stats = chain.Stats(4, 1024, 1024, None, None, np.zeros((256, 256), np.float32), None)
        ctx = chain.Ctx(stats, 0, 0, 1024, 1024)
        st = blk.prepare('probe', img, p, ctx)
        out = blk.apply(img, p, st, ctx)
        res = ms.rgb_shift(out[256:768, 256:768])
        for ch in 'rb':
            self.assertLess(float(np.abs(res[ch][1]).max()), 0.10, f'{ch} residual {res[ch][1]} ({st[ch]["why"]})')
        flat = np.full((600, 600, 3), 0.4, np.float32)
        st = blk.prepare('probe', flat, p, ctx)
        self.assertIn('fewer than two crops', st['r']['why'])

    def test_textured_crops_land_on_the_lit_crescent(self):
        scene = lunar_disk(768, crescent=True)
        g = scene['image'].astype(np.float32).mean(axis=2) / 65535
        boxes = ms.textured_boxes(g, 3, 128)
        self.assertTrue(boxes)
        for y, x in boxes:
            self.assertTrue(scene['lit'][y + 64, x + 64], (y, x))


class Deconvolution(unittest.TestCase):
    def test_psf_matches_luckystackworker(self):
        """T-PSF: Appendix B's vectors, and the min-max scaling LuckyStackWorker applies."""
        vec = {(7, 'r'): (0.0200227, 0.2656974), (7, 'g'): (0.2691215, 0.0061769), (7, 'b'): (0.0072105, 0.0965668),
               (15, 'r'): (0.0200227, 1.4786523), (15, 'g'): (0.2691215, 0.6288030), (15, 'b'): (0.0072105, 0.0475072)}
        for (R, c), (at_r, at_40) in vec.items():
            img, _ = D.generate_channel(D.WAVELENGTHS[c], R, 5, 200)
            self.assertAlmostEqual(img[32, 32], np.log(201), places=6)
            self.assertAlmostEqual(img[32, 32 + R], at_r, places=6)
            self.assertAlmostEqual(img[32, 40], at_40, places=6)
        for p in D.synthetic_psf(29, 150, 3, mono=False):
            self.assertEqual(float(p.min()), 0.0)
            self.assertAlmostEqual(float(p.sum()), 1.0, places=9)
        mono = D.synthetic_psf(7, 200, 5, mono=True)
        np.testing.assert_array_equal(mono[0], mono[2])            # mono: all channels at 532 nm

    def test_wpl_deblurs(self):
        """T-DECONV: 10 iterations cut the error against the sharp image by at least 30 %, in range, no NaN."""
        rng = np.random.default_rng(3)
        sharp = np.clip(0.4 + 0.5 * cv2.GaussianBlur(rng.standard_normal((512, 512)).astype(np.float32), (0, 0), 1.2), 0, 1)
        psf = D.synthetic_psf(4, 100, 4, mono=True)[1].astype(np.float32)
        blurred = np.clip(cv2.filter2D(sharp, -1, psf, borderType=cv2.BORDER_REFLECT)
                          + 0.002 * rng.standard_normal(sharp.shape), 0, 1).astype(np.float32)
        out = D.wpl(blurred, psf, 10)
        c = slice(64, -64)
        err = lambda a: float(np.sqrt(((a - sharp)[c, c] ** 2).mean()))
        self.assertTrue(np.isfinite(out).all() and out.min() >= 0 and out.max() <= 1)
        self.assertLess(err(out), 0.7 * err(blurred))

    def test_position_warning(self):
        d = doc_of('final', chain.entry('contrast'), chain.entry('deconvolution', {'iterations': 2}))
        _, stats = run(lunar_disk(256)['image'], d)
        self.assertIn('first in its chain', stats.readouts[d['chains']['final'][1]['id']]['warning'])


class Tone(unittest.TestCase):
    def test_levels_and_curves(self):
        """T-LEVELS / T-CURVES."""
        x = np.random.default_rng(5).random((64, 64, 3), dtype=np.float32)
        lev, cur = B.BLOCKS['levels'], B.BLOCKS['curves']
        np.testing.assert_array_equal(lev.apply(x, lev.normalise({}), {}, None), x)
        self.assertLess(float(np.abs(cur.apply(x, cur.normalise({}), {}, None) - x).max()), 1e-6)
        p = lev.normalise({'black': 10, 'white': 245, 'gamma': 1.2})
        np.testing.assert_allclose(lev.apply(x, p, {}, None), np.clip((x - 10 / 255) / (235 / 255), 0, 1) ** (1 / 1.2),
                                   atol=1e-6)
        pts = [[0, 0], [64, 40], [128, 150], [200, 230], [255, 255]]
        out = cur.apply(np.array([[[v / 255] * 3 for v, _ in pts]], np.float32), cur.normalise({'points': pts}), {}, None)
        np.testing.assert_allclose(out[0, :, 0] * 255, [o for _, o in pts], atol=0.02)
        for bad in ({'black': 200, 'white': 100}, ):
            with self.assertRaises(ValueError):
                lev.normalise(bad)
        with self.assertRaises(ValueError):
            cur.normalise({'points': [[0, 0], [100, 50], [90, 60]]})

    def test_pure_red_stays_red(self):
        """T-ORDER: a red disk stays red through every block (catches an RGB/BGR swap)."""
        scene = lunar_disk(256, mono=True)
        g = scene['image'].astype(np.float32)
        red = np.clip(np.dstack([g, 0.08 * g, 0.08 * g]), 0, 65535).astype(np.uint16)
        for t in sorted(B.BLOCKS):
            path = 'colour' if 'final' not in B.BLOCKS[t].paths else 'final'
            params = {'iterations': 2} if t == 'deconvolution' else {}
            out, _ = run(red, doc_of(path, chain.entry(t, params)))
            on = scene['lit'] & ~scene['deep_shadows']
            r, gg, b = (out[..., k][on].mean() for k in range(3))
            with self.subTest(block=t):
                self.assertGreater(r, 2 * max(gg, b), f'{t}: R {r:.3f} G {gg:.3f} B {b:.3f}')


class Tiles(unittest.TestCase):
    """T-TILE for the new blocks; deconvolution at 100 iterations with dering on (max |Δ| ≤ 1e-3)."""

    @classmethod
    def setUpClass(cls):
        cls.source = chain.ArraySource(lunar_disk(2048)['image'])

    def check(self, doc, n=1500, tol=1e-5):
        stats = chain.prepare(self.source, doc)
        y0 = x0 = (2048 - n) // 2
        whole = chain.render(self.source, stats, doc, y0, x0, n, n)
        tiled = np.empty_like(whole)
        for ty in range(0, n, 256):
            for tx in range(0, n, 256):
                th, tw = min(256, n - ty), min(256, n - tx)
                tiled[ty:ty + th, tx:tx + tw] = chain.render(self.source, stats, doc, y0 + ty, x0 + tx, th, tw)
        self.assertLessEqual(float(np.abs(tiled - whole).max()), tol)

    def test_tone_and_blur_blocks(self):
        for path, e in (('final', chain.entry('levels', {'black': 12, 'white': 230, 'gamma': 1.3})),
                        ('final', chain.entry('curves', {'points': [[0, 0], [60, 90], [255, 255]]})),
                        ('final', chain.entry('gaussian_blur', {'sigma': 2.5})),
                        ('final', chain.entry('white_balance', {'neutralize_background': True})),
                        ('colour', chain.entry('rgb_shift', {'mode': 'manual', 'manual': {'r': [0.4, -0.3], 'b': [-0.2, 0.25]}}))):
            with self.subTest(block=e['type']):
                self.check(doc_of(path, e))

    def test_deconvolution_at_the_most_iterations(self):
        self.check(doc_of('final', chain.entry('deconvolution', {'iterations': 100, 'dering_strength': 0.5})),
                   n=1024, tol=1e-3)



class CommandLine(unittest.TestCase):
    def test_lunar_finish_runs_every_block_with_its_movie(self):
        """Every phase-3 block through lunar_finish --chain, with the movie (facts and steps for each)."""
        import json, os, subprocess, tempfile, tifffile
        d = chain.empty()
        d['chains']['colour'] = [chain.entry('rgb_shift')]
        d['chains']['final'] = [chain.entry('deconvolution', {'iterations': 3}), chain.entry('levels', {'black': 5}),
                                chain.entry('curves', {'points': [[0, 0], [100, 120], [255, 255]]}),
                                chain.entry('gaussian_blur', {'sigma': 0.6}), chain.entry('white_balance'),
                                chain.entry('saturation', {'passes': 1})]
        with tempfile.TemporaryDirectory() as t:
            src, cfile = Path(t)/'in.tif', Path(t)/'c.json'
            tifffile.imwrite(src, lunar_disk(768)['image'], photometric='rgb')
            cfile.write_text(json.dumps(d))
            env = {k: v for k, v in os.environ.items() if not k.startswith('LUNAR_FINISH_')}
            done = subprocess.run([sys.executable, str(HERE.parents[0]/'lunar_finish.py'), str(src), '-o', str(Path(t)/'o.tif'),
                                   '--chain', str(cfile), '--record', str(Path(t)/'m.mp4'), '--keep-orientation',
                                   '--no-preview', '--no-display-tif'], capture_output=True, text=True, env=env, timeout=900)
            self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-3000:])
            for fragment in ('deconvolution (WPL', 'levels: black 5', 'curves: 0→0', 'Gaussian blur', 'white balance: gains',
                             'colour chain: rgb shift'):
                self.assertIn(fragment, done.stdout)
            self.assertGreater((Path(t)/'m.mp4').stat().st_size, 10000)


if __name__ == '__main__':
    unittest.main()
