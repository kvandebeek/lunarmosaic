"""Phase 2 acceptance (plan §5, phase 2): T-TILE, T-ATWT, T-USM, T-MERGE-ID, T-SPACE, T-MOVIE, validation and
the chain compiled from lunar_finish's flags. T-GOLD lives in golden.py."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

import cv2
import numpy as np
import tifffile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT/'mosaic'))
sys.path.insert(0, str(HERE))
import blocks as B  # noqa: E402
import chain  # noqa: E402
import filters as F  # noqa: E402
from synthetic import lunar_disk  # noqa: E402

FLAGS = dict(wavelets=None, wavelet_kernel='linear', wavelet_denoise=[], highlights=0.3, knee_percentile=90.0,
             saturation=2.0, saturation_passes=2, chroma_denoise=3.0, limb_taper=0.0, contrast=0.12, usm=None,
             usm_mode='luminosity')


def doc_of(*entries):
    d = chain.empty()
    d['chains']['final'] = list(entries)
    return d


class Tiles(unittest.TestCase):
    """T-TILE: a 1500² region rendered in 256² tiles equals the same region rendered whole (max |Δ| ≤ 1e-5)."""

    @classmethod
    def setUpClass(cls):
        cls.source = chain.ArraySource(lunar_disk(2048)['image'])

    def check(self, doc):
        stats = chain.prepare(self.source, doc)
        y0 = x0 = 274
        n = 1500
        whole = chain.render(self.source, stats, doc, y0, x0, n, n)
        tiled = np.empty_like(whole)
        for ty in range(0, n, 256):
            for tx in range(0, n, 256):
                th, tw = min(256, n - ty), min(256, n - tx)
                tiled[ty:ty + th, tx:tx + tw] = chain.render(self.source, stats, doc, y0 + ty, x0 + tx, th, tw)
        self.assertLessEqual(float(np.abs(tiled - whole).max()), 1e-5)

    def test_each_block(self):
        for e in (chain.entry('wavelets', {'biases': [0.6, 1.1, 0.2, 0.0], 'denoise': [1.0, 1.0, 0.0, 0.0]}),
                  chain.entry('unsharp_mask', {'amount': 100, 'radius': 2.0, 'threshold': 2, 'mode': 'rgb'}),
                  chain.entry('unsharp_mask', {'amount': 100, 'radius': 2.0, 'threshold': 2, 'mode': 'luminosity'}),
                  chain.entry('highlights', {'strength': 0.3}),
                  chain.entry('contrast', {'strength': 0.12}),
                  chain.entry('saturation', {'factor': 2, 'passes': 2, 'chroma_blur_px': 3}, limb_fade_px=40),
                  chain.entry('highlights', {'strength': 0.3}, limb_fade_px=60)):
            with self.subTest(block=e['type'], fade=e['limb_fade_px']):
                self.check(doc_of(e))

    def test_default_chain_with_sharpening(self):
        args = NS(**dict(FLAGS, wavelets=[0.6, 1.1, 0.2, 0.0], wavelet_denoise=[0.0] * 4, usm=(0.6, 1.3, 0.0)))
        self.check(chain.default_chain(args, False))


class Filters(unittest.TestCase):
    def test_atwt_is_a_reweighting_of_the_input_layers(self):
        """T-ATWT. Not a re-decomposition of the output: the à-trous transform is not idempotent, and a correct
        implementation fails that comparison by 11–268 %."""
        rng = np.random.default_rng(1)
        img = np.clip(0.3 + 0.05 * rng.standard_normal((400, 400)), 0, 1).astype(np.float32)
        for biases in ([0.6, 1.1, 0.2, 0.0], [2.3, 0, 0, 0], [-0.5, 5.0, 0.1, 1.0], list(rng.uniform(-1, 15, 4))):
            layers, residual = F.atrous(img, 4, 'linear')
            expected = residual + sum(F.wavelet_gain(b) * w for b, w in zip(biases, layers))
            out = F.wavelet_sharpen(img, biases, [0] * 4, [0] * 4, 'linear')
            self.assertLessEqual(float(np.abs(out - expected).max()), 1e-5, biases)
        for b, g in ((0.1, 2), (0.5, 6), (1, 11), (2.3, 24), (5, 51), (-0.5, 1 / 6)):
            self.assertAlmostEqual(F.wavelet_gain(b), g, places=9)

    def test_unsharp_mask_photoshop_law(self):
        """T-USM: amount exactly linear, level-independent, soft threshold."""
        def impulse(bg, amount):
            x = np.full((65, 65), bg, np.float32)
            x[32, 32] += 0.02
            return F.unsharp(x, amount, 2.0, 0)[32, 32] - x[32, 32]
        r = [impulse(0.25, a) for a in (0.5, 1.0, 2.0)]
        self.assertAlmostEqual(r[1] / r[0], 2, places=5)
        self.assertAlmostEqual(r[2] / r[0], 4, places=5)
        self.assertAlmostEqual(impulse(0.25, 1.0), impulse(0.50, 1.0), places=6)
        step = np.tile(np.repeat(np.linspace(0.2, 0.6, 8, dtype=np.float32), 16), (32, 1))
        for t in (0, 2, 8):
            d = step - cv2.GaussianBlur(step, (0, 0), F.usm_sigma(2.0))
            expected = step + 1.0 * np.sign(d) * np.maximum(np.abs(d) - t / 255, 0)
            self.assertLessEqual(float(np.abs(F.unsharp(step, 1.0, 2.0, t) - expected).max()), 1e-6)

    def test_merge_of_identical_inputs_is_the_input(self):
        """T-MERGE-ID (exact Lab; OpenCV's conversion would be off by up to 291 ADU)."""
        x = np.random.default_rng(4).random((200, 300, 3), dtype=np.float32)
        self.assertLessEqual(float(np.abs(chain.merge(x, x) - x).max()), 1e-5)


class Space(unittest.TestCase):
    """T-SPACE (D7). Until phase 3 brings Curves, a test block lifts shadows (gamma 2 below 0.2)."""

    @classmethod
    def setUpClass(cls):
        lift = lambda v: np.where(v < 0.2, np.sqrt(np.clip(v, 0, None) / 0.2) * 0.2, v)
        B.register(B.Block('test_lift', B.PATHS, {}, lambda x, p, st, ctx: lift(x).astype(np.float32), lambda p: 0))
        cls.lift = staticmethod(lift)
        scene = lunar_disk(768)
        img = scene['image'].copy()
        ramp = np.linspace(0, 3000, 120).astype(np.uint16)            # a sky ramp, connected to the frame edge
        img[2:6, 10:130] = ramp[None, :, None]
        cls.scene, cls.img = scene, img
        cls.doc = doc_of(chain.entry('test_lift'))
        source = chain.ArraySource(img)
        cls.out = chain.render(source, chain.prepare(source, cls.doc), cls.doc, 0, 0, 768, 768)

    @classmethod
    def tearDownClass(cls):
        B.BLOCKS.pop('test_lift', None)

    def test_enclosed_shadows_are_processed_like_the_disk(self):
        sh = self.scene['deep_shadows']
        inp = self.img[sh].astype(np.float32) / 65535
        lifted = self.lift(inp)
        # lightness follows the lift; colour stays the input's (they are near-black)
        dL = np.abs(F.lstar(self.out[sh]) - F.lstar(lifted))
        self.assertLess(float(dL.max()), 0.05)
        self.assertLess(float(np.hypot(*F.rgb_to_lab(self.out[sh])[:, 1:].T).max()), 0.2)

    def test_sky_ramp_stays_monotonic_and_black_sky_unchanged(self):
        row = self.out[3, 10:130].mean(axis=1)
        self.assertTrue(np.all(np.diff(row) >= -1e-7), 'the sky ramp must not fold back')
        sky = ~self.scene['disk']
        sky[:8] = False
        black = sky & (F.lstar(self.img.astype(np.float32) / 65535) < 0.5)
        self.assertLessEqual(float(np.abs(self.out[black] - self.img[black] / 65535).max()) * 65535, 1)


class Documents(unittest.TestCase):
    def test_validation_names_the_problem(self):
        d = chain.empty()
        d['chains']['final'] = [{'id': 'abc', 'type': 'wavelets', 'params': {}},
                                {'id': '0123abcd', 'type': 'saturation', 'params': {'factor': 99}},
                                {'id': '0123abce', 'type': 'nonsense', 'params': {}}]
        d['chains']['luminance'] = [{'id': '0123abcf', 'type': 'saturation', 'params': {}}]
        reasons = ' '.join(r for _, r in chain.validate(d))
        for fragment in ('hexadecimal id', 'factor must be', 'Unknown block type', 'does not work on the luminance'):
            self.assertIn(fragment, reasons)

    def test_round_trip_and_fresh_ids(self):
        d = chain.default_chain(NS(**FLAGS), False)
        with tempfile.TemporaryDirectory() as t:
            chain.dump(d, Path(t)/'c.json')
            self.assertEqual(chain.load(Path(t)/'c.json'), chain.normalised(d))
        a, b = chain.entry('contrast'), chain.entry('contrast')
        self.assertNotEqual(a['id'], b['id'])

    def test_default_chain_mirrors_the_old_flags(self):
        types = lambda d: [e['type'] for e in d['chains']['final']]
        self.assertEqual(types(chain.default_chain(NS(**FLAGS), False)), ['highlights', 'saturation', 'contrast'])
        self.assertEqual(types(chain.default_chain(NS(**FLAGS), True)), ['highlights', 'contrast'])
        sat = chain.default_chain(NS(**dict(FLAGS, limb_taper=0.0)), False)['chains']['final'][1]
        self.assertEqual(sat['limb_fade_px'], 1.0)          # the old taper divided by max(taper, 1)
        smooth_only = chain.default_chain(NS(**dict(FLAGS, saturation_passes=0)), False)['chains']['final'][1]
        self.assertEqual((smooth_only['type'], smooth_only['params']['passes']), ('saturation', 0))


class Prefix(unittest.TestCase):
    """Plan §4.5 (phase 7): a changed block restarts the statistics there; the result equals a fresh pass."""

    @staticmethod
    def equal(a, b):
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(Prefix.equal(a[k], b[k]) for k in a)
        if isinstance(a, (list, tuple)):
            return len(a) == len(b) and all(Prefix.equal(x, y) for x, y in zip(a, b))
        if isinstance(a, np.ndarray):
            return np.array_equal(a, b)
        return a == b

    def test_resumed_statistics_equal_a_fresh_pass_and_skip_what_did_not_change(self):
        source = chain.ArraySource(lunar_disk(1024)['image'])
        doc = chain.empty()
        doc['chains']['luminance'] = [chain.entry('deconvolution', {'iterations': 10}), chain.entry('highlights', {}),
                                      chain.entry('wavelets', {'biases': [.3, .1]})]
        doc['chains']['colour'] = [chain.entry('saturation', {'factor': 1.5})]
        doc['chains']['final'] = [chain.entry('curves', {'points': [[0, 0], [128, 140], [255, 255]]}), chain.entry('contrast', {})]
        calls = []
        applied = {t: B.BLOCKS[t]._apply for t in ('deconvolution', 'highlights', 'wavelets', 'curves', 'contrast')}
        try:
            for t, f in applied.items():
                B.BLOCKS[t]._apply = (lambda f, t: lambda *a, **k: (calls.append(t), f(*a, **k))[1])(f, t)
            cache = chain.PrefixCache()
            chain.prepare(source, doc, cache=cache, tag='t')
            for change, recomputed in (
                    (lambda d: d['chains']['final'][1]['params'].update(strength=.2), {'contrast'}),
                    (lambda d: d['chains']['luminance'][2]['params'].update(biases=[.5, .1]), {'wavelets', 'curves', 'contrast'})):
                changed = copy.deepcopy(doc)
                change(changed)
                calls.clear()
                resumed = chain.prepare(source, changed, cache=cache, tag='t')
                self.assertEqual(set(calls), recomputed)            # the deconvolution never runs again
                fresh = chain.prepare(source, changed)
                self.assertTrue(self.equal(resumed.blocks, fresh.blocks))
                self.assertTrue(self.equal(resumed.readouts, fresh.readouts))
        finally:
            for t, f in applied.items():
                B.BLOCKS[t]._apply = f


class CommandLine(unittest.TestCase):
    def run_finish(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith('LUNAR_FINISH_')}
        e.update(env or {})
        return subprocess.run([sys.executable, str(ROOT/'mosaic'/'lunar_finish.py'), *args], capture_output=True,
                              text=True, cwd=str(ROOT), env=e, timeout=900)

    def test_help_lists_every_flag(self):
        out = self.run_finish('--help').stdout
        for flag in ('--chain', '--write-chain', '--wavelets', '--usm', '--highlights', '--saturation', '--contrast',
                     '--chroma-denoise', '--limb-taper', '--record', '--keep-orientation', '--display-tif'):
            self.assertIn(flag, out)

    def test_environment_settings_still_reach_the_chain(self):
        with tempfile.TemporaryDirectory() as t:
            src = Path(t)/'in.tif'
            tifffile.imwrite(src, lunar_disk(256)['image'], photometric='rgb')
            done = self.run_finish(str(src), '--write-chain', str(Path(t)/'c.json'),
                                   env={'LUNAR_FINISH_SATURATION_PASSES': '1', 'LUNAR_FINISH_WAVELETS': '0.5,0'})
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            fin = json.loads((Path(t)/'c.json').read_text())['chains']['final']
            self.assertEqual([e['type'] for e in fin], ['wavelets', 'highlights', 'saturation', 'contrast'])
            self.assertEqual(fin[2]['params']['passes'], 1)

    def test_movie_and_facts_for_an_unusual_chain(self):
        """T-MOVIE: no contrast block and two saturation blocks."""
        d = doc_of(chain.entry('highlights'), chain.entry('saturation', {'passes': 1}),
                   chain.entry('saturation', {'passes': 2, 'factor': 1.5}, limb_fade_px=20))
        with tempfile.TemporaryDirectory() as t:
            src, cfile = Path(t)/'in.tif', Path(t)/'chain.json'
            tifffile.imwrite(src, lunar_disk(768)['image'], photometric='rgb')
            chain.dump(d, cfile)
            done = self.run_finish(str(src), '-o', str(Path(t)/'out.tif'), '--chain', str(cfile), '--record',
                                   str(Path(t)/'movie.mp4'), '--keep-orientation', '--no-preview', '--no-display-tif')
            self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-3000:])
            self.assertTrue((Path(t)/'movie.mp4').stat().st_size > 10000)
            self.assertEqual(done.stdout.count('colour ('), 2, done.stdout)
            self.assertNotIn('contrast:', done.stdout)


if __name__ == '__main__':
    unittest.main()
