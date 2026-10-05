"""T-LAB: exact CIE Lab (plan D17) — lossless round trips, neutral greys, and agreement with OpenCV's constants."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import filters as F  # noqa: E402

ADU = 65535


class Lab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(7)
        n = 200000
        cls.sets = {
            'greys': np.repeat(rng.random((n, 1, 1), dtype=np.float32), 3, axis=2),
            'lunar colours': np.clip(rng.random((n, 1, 1), dtype=np.float32) * 0.9
                                     * np.array([1.04, 1.0, 0.95], np.float32), 0, 1).astype(np.float32),
            'near-black': (rng.random((n, 1, 3), dtype=np.float32) * 0.03).astype(np.float32),
            'random colours': rng.random((n, 1, 3), dtype=np.float32),
        }

    def test_round_trip_within_a_tenth_of_an_adu(self):
        for name, x in self.sets.items():
            d = np.abs(np.clip(F.lab_to_rgb(F.rgb_to_lab(x)), 0, 1) - x).max() * ADU
            self.assertLessEqual(d, 0.1, f'{name}: round trip off by {d:.3f} ADU')

    def test_neutral_grey_has_no_chroma(self):
        # OpenCV's float conversion gives greys |a*|, |b*| up to 0.125, which tinted mono output by 34 ADU.
        lab = F.rgb_to_lab(self.sets['greys'])
        self.assertLess(np.abs(lab[..., 1:]).max(), 1e-6)

    def test_matches_opencv_constants(self):
        # Within OpenCV's own table-interpolation error: the constants are the same, only the arithmetic differs.
        x = self.sets['lunar colours']
        d = np.abs(F.rgb_to_lab(x) - cv2.cvtColor(x, cv2.COLOR_RGB2Lab))
        self.assertLessEqual(d[..., 0].max(), 0.25)
        self.assertLessEqual(d[..., 1:].max(), 0.4)

    def test_anchors(self):
        lab = F.rgb_to_lab(np.array([[[1, 1, 1], [0, 0, 0]]], np.float32))
        np.testing.assert_allclose(lab[0, 0], [100, 0, 0], atol=1e-4)
        np.testing.assert_allclose(lab[0, 1], [0, 0, 0], atol=1e-6)

    def test_out_of_gamut_is_not_clipped(self):
        # Like cv2.cvtColor: the caller clips, so a saturated Lab colour may leave 0–1.
        rgb = F.lab_to_rgb(np.array([[[50, 120, 0], [50, 0, -120]]], np.float32))
        self.assertTrue((rgb < 0).any() or (rgb > 1).any())

    def test_shapes_order_and_layout(self):
        img = np.random.default_rng(3).random((37, 53, 3), dtype=np.float32)
        lab = F.rgb_to_lab(img)
        self.assertEqual((lab.shape, lab.dtype), ((37, 53, 3), np.float32))
        self.assertEqual(F.rgb_to_lab(img.reshape(-1, 3)).shape, (37 * 53, 3))
        np.testing.assert_array_equal(F.bgr_to_lab(img), F.rgb_to_lab(img[..., ::-1]))
        bgr = F.lab_to_bgr(lab)
        self.assertTrue(bgr.flags['C_CONTIGUOUS'])
        np.testing.assert_array_equal(bgr, F.lab_to_rgb(lab)[..., ::-1])


if __name__ == '__main__':
    unittest.main()
