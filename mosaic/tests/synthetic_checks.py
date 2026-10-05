"""Verify fixture ground truth before the implementation relies on it."""
import unittest

import cv2
import numpy as np

if __package__:
    from .synthetic import band_limited_texture, cut_panels, lunar_disk, panel_coordinates, white_balance_probe
else:
    from synthetic import band_limited_texture, cut_panels, lunar_disk, panel_coordinates, white_balance_probe


class Synthetic(unittest.TestCase):
    def test_disk_has_black_space_limb_darkening_and_enclosed_shadows(self):
        scene = lunar_disk(384)
        rgb, disk, shadows = (scene[k] for k in ('image', 'disk', 'deep_shadows'))
        self.assertEqual(rgb.dtype, np.uint16)
        self.assertEqual(rgb.shape, (384, 384, 3))
        self.assertTrue(np.all(rgb[~disk] == 0))
        self.assertGreater(shadows.sum(), 50)
        self.assertTrue(np.all((rgb[shadows] > 0) & (rgb[shadows] < 1022)))
        low = (rgb[..., 1] < 1022).astype(np.uint8)
        _, labels = cv2.connectedComponents(low)
        edge_labels = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        self.assertFalse(np.isin(labels[shadows], edge_labels).any())
        y, x = np.mgrid[:384, :384]
        r = np.hypot(x-scene['centre_xy'][0], y-scene['centre_xy'][1])/scene['radius_px']
        self.assertGreater(np.median(rgb[..., 1][(r < .4) & ~shadows]),
                           np.median(rgb[..., 1][(r > .85) & (r < .98)]))
        mono = lunar_disk(384, mono=True)['image']
        np.testing.assert_array_equal(mono, rgb[..., 1])

    def test_crescent_keeps_a_known_lit_region_and_dark_night_side(self):
        scene = lunar_disk(384, crescent=True)
        lit, disk = scene['lit'], scene['disk']
        self.assertAlmostEqual(lit.sum()/disk.sum(), .25, delta=.01)
        self.assertTrue(np.all(scene['image'][~lit] == 0))
        self.assertGreater(np.count_nonzero(disk & ~lit), np.count_nonzero(lit))
        self.assertGreater(scene['deep_shadows'].sum(), 20)
        self.assertTrue(np.all(lit[scene['deep_shadows']]))
        _, labels = cv2.connectedComponents((scene['image'][..., 1] < 1022).astype(np.uint8))
        self.assertTrue(np.all(labels[scene['deep_shadows']] != labels[0, 0]))

    def test_repeatability_and_independence_of_global_rng(self):
        before = np.random.get_state()
        for generate in (lunar_disk, white_balance_probe, band_limited_texture):
            first, second, changed = generate(128), generate(128), generate(128, seed=123)
            np.testing.assert_array_equal(first['image'], second['image'])
            self.assertFalse(np.array_equal(first['image'], changed['image']))
        after = np.random.get_state()
        self.assertEqual(before[0], after[0])
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[2:], after[2:])

    def test_overlapping_variable_panels_cover_each_scale_and_preserve_pixel_centres(self):
        y, x = np.mgrid[:384, :512]
        source = (1000+30*x+20*y).astype(np.uint16)
        before = source.copy()
        panels = cut_panels(source, 128, scales=(1, 2))
        for set_id, scale in [('s0', 1), ('s1', 2)]:
            coverage = np.zeros((384//scale, 512//scale), np.int32)
            sizes = set()
            for panel in (p for p in panels if p['set'] == set_id):
                pixels = panel['image']
                h, w = pixels.shape
                sizes.add((w, h))
                self.assertTrue(109 <= min(w, h) <= max(w, h) <= 128)
                self.assertTrue(panel['valid'].all())
                x0, y0 = panel['origin_xy']
                coverage[y0:y0+h, x0:x0+w] += 1
                px, py = panel_coordinates(panel)
                # Area reduction of a linear ramp equals its value at the output pixel centre.
                np.testing.assert_allclose(pixels, 1000+30*px+20*py, atol=.51, rtol=0)
                self.assertEqual(panel['scale_xy'], (scale, scale))
            self.assertGreater(len(sizes), 1)
            self.assertTrue((coverage >= 1).all())
            self.assertGreater(np.mean(coverage > 1), .2)
        np.testing.assert_array_equal(source, before)
        repeat = cut_panels(source, 128, scales=(1, 2))
        for a, b in zip(panels, repeat):
            np.testing.assert_array_equal(a['image'], b['image'])
            np.testing.assert_array_equal(a['panel_to_canvas'], b['panel_to_canvas'])

    def test_noninteger_sampling_records_actual_scale_after_dimension_rounding(self):
        panels = cut_panels(lunar_disk(385)['image'], 96, scales=(2.07,))
        scale = 385/round(385/2.07)
        self.assertNotEqual(scale, 2.07)
        for panel in panels:
            self.assertEqual(panel['requested_scale'], 2.07)
            self.assertEqual(panel['scale_xy'], (scale, scale))
            x0, y0 = panel['origin_xy']
            np.testing.assert_allclose(panel['panel_to_canvas'] @ [0, 0, 1],
                                       [scale*(x0+.5)-.5, scale*(y0+.5)-.5, 1])

    def test_radial_panels_retain_known_mapping_and_do_not_mutate_sources(self):
        y, x = np.mgrid[:256, :256]
        source = (2000+20*x+30*y).astype(np.uint16)
        before = source.copy()
        panels = cut_panels(source, 96, radial=.035)
        coefficients = [p['radial_k1'] for p in panels]
        self.assertGreater(max(coefficients), 0)
        self.assertLess(min(coefficients), 0)
        for panel in panels:
            px, py = panel_coordinates(panel)
            interior = panel['valid'] & (px > 5) & (px < 250) & (py > 5) & (py < 250)
            self.assertGreater(interior.sum(), 0)
            np.testing.assert_allclose(panel['image'][interior], (2000+20*px+30*py)[interior], atol=3, rtol=0)
        for a, b in zip(panels, cut_panels(source, 96, radial=.035)):
            self.assertEqual(a['radial_k1'], b['radial_k1'])
            np.testing.assert_array_equal(a['image'], b['image'])
        np.testing.assert_array_equal(source, before)

    def test_white_balance_probe_matches_the_measured_pedestal_and_gain_ratios(self):
        probe = white_balance_probe()
        rgb = probe['image']
        self.assertEqual(rgb.dtype, np.float32)
        background = np.median(rgb[probe['background']], axis=0)
        np.testing.assert_allclose(background, [.03, .05, .04], atol=1e-8, rtol=0)
        white = np.mean(rgb[probe['surface']].astype(np.float64)-background, axis=0)
        gains = white.max()/white
        np.testing.assert_allclose(gains, [1.1369, 1.0800, 1.0000], rtol=.002)
        np.testing.assert_allclose(gains, probe['gain_ratios'], rtol=1e-7)
        self.assertGreater(float(rgb[probe['surface']].min()), .1)
        self.assertLess(float(rgb.max()), .99)

    def test_spectral_probes_have_known_support_rms_and_white_noise(self):
        for cutoff in (.15, .25, .40):
            probe = band_limited_texture(512, cutoff)
            signal, noise = probe['signal'], probe['noise']
            self.assertAlmostEqual(float(signal.std()), .08, delta=1e-7)
            self.assertAlmostEqual(float(noise.std()), .002, delta=1e-8)
            np.testing.assert_allclose(probe['image'], .5+signal+noise, atol=1e-7, rtol=0)
            freq = np.fft.fftfreq(512)
            radius = np.hypot(freq[:, None], freq[None, :])
            power = np.abs(np.fft.fft2(signal))**2
            self.assertLess(power[radius > cutoff].sum()/power.sum(), 1e-12)
            self.assertGreater(power[(radius > cutoff-.01) & (radius <= cutoff)].sum()/power.sum(), .01)
            spectrum = np.abs(np.fft.fft2(noise))**2
            low = spectrum[(radius > .05) & (radius < .15)].mean()
            high = spectrum[(radius > .4) & (radius < .5)].mean()
            self.assertAlmostEqual(low/high, 1, delta=.08)
            self.assertTrue(np.all((probe['image'] >= 0) & (probe['image'] <= 1)))

    def test_invalid_fixture_requests_fail_explicitly(self):
        source = np.zeros((128, 128), np.uint16)
        for call in (lambda: lunar_disk(16), lambda: white_balance_probe(16),
                     lambda: cut_panels(source, 96, scales=(2,)),
                     lambda: cut_panels(source, 96, scales=(float('nan'),)),
                     lambda: cut_panels(source, 96, size_variation=.2),
                     lambda: cut_panels(source, 96, overlap=1),
                     lambda: band_limited_texture(64, .001),
                     lambda: band_limited_texture(64, .6),
                     lambda: band_limited_texture(64, noise_rms=-1)):
            with self.assertRaises(ValueError):
                call()


if __name__ == '__main__':
    unittest.main(verbosity=2)
