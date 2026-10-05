"""Builder determinism: the same panels and flags must give the same mosaic, bit for bit.

Phase 0 of mosaic/docs/IMPLEMENTATION_PLAN.md records the answer, because phase 5's single-scale check depends on
it. Recorded 2026-10-05: three full builds of this synthetic set were bit-identical, and their layout reports
differed only in the `created` timestamp — so phase 5 may require bit-identical output.

The set is sized so every stage that could introduce thread-order differences really runs: fine-alignment tiles
are 256 px and must sit 176 px inside both panels, so overlaps need at least 352 px (a smaller fixture silently
falls back to keypoint placement, which would make this test pass for the wrong reason). The test therefore also
asserts, from the log, that fine alignment found tiles and the mesh ran. About 20 s.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np
import tifffile

if __package__:
    from .synthetic import cut_panels, lunar_disk
else:
    from synthetic import cut_panels, lunar_disk

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ['--model', 'poly3+mesh', '--proxy-factor', '1', '--no-preview', '--no-display-tif', '--no-north-up']


def build(panels_dir, out):
    done = subprocess.run([sys.executable, str(ROOT/'mosaic'/'mosaic_builder.py'), str(panels_dir), '-o', str(out), *FLAGS],
                          capture_output=True, text=True, cwd=str(ROOT), timeout=900)
    if done.returncode != 0 or not Path(out).is_file():
        raise RuntimeError(f'mosaic_builder failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}')
    return done.stdout


class Determinism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='determinism-')
        root = Path(cls.work.name)
        panels = root/'panels'
        panels.mkdir()
        for p in cut_panels(lunar_disk(2048)['image'], panel_px=1024, overlap=.45):
            tifffile.imwrite(panels/f"{p['id']}.tif", p['image'], photometric='rgb')
        cls.logs = [build(panels, root/f'm{i}.tif') for i in (1, 2)]
        cls.images = [cv2.imread(str(root/f'm{i}.tif'), cv2.IMREAD_UNCHANGED) for i in (1, 2)]
        cls.layouts = [json.loads((root/f'm{i}_layout.json').read_text('utf-8')) for i in (1, 2)]

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_every_stage_ran(self):
        log = self.logs[0]
        tiles = [int(n) for n in re.findall(r'round \d+: (\d+) tiles on \d+ pairs', log)]
        self.assertTrue(tiles and max(tiles) > 0, 'Fine alignment found no tiles; the fixture no longer exercises it.')
        mesh = re.search(r'mesh: (\d+) tiles of 128 px', log)
        self.assertTrue(mesh and int(mesh[1]) > 0, 'The mesh did not run; the fixture no longer exercises it.')

    def test_mosaics_are_bit_identical(self):
        a, b = self.images
        self.assertEqual(a.shape, b.shape)
        if not np.array_equal(a, b):
            d = np.abs(a.astype(np.int64)-b.astype(np.int64))
            self.fail(f'Two builds differ: max {d.max()} ADU over {int((d.max(axis=2) > 0).sum())} px. Phase 5 must '
                      'then compare single-scale builds within 1 ADU instead of bit for bit.')
        print('\n  recorded: two full builds are bit-identical', file=sys.stderr)

    def test_layouts_match_apart_from_the_timestamp(self):
        a, b = ({k: v for k, v in lay.items() if k != 'created'} for lay in self.layouts)
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
