"""Today's lunar_finish output on synthetic disks, frozen as the reference for the chain refactor.

Phase 0 of mosaic/docs/IMPLEMENTATION_PLAN.md. The three cases are run through the real command line with
every setting pinned twice — an explicit flag and an environment variable — because `.env` would otherwise
reach the run (the owner's sets LUNAR_FINISH_WAVELETS=pixinsight) and `--usm` has no "off" flag.

    python mosaic/tests/golden.py                  compare the current code with the stored fixtures
    python mosaic/tests/golden.py --report         the same, printing rms / p99.9 / max in ADU
    python mosaic/tests/golden.py --capture        (re)write the fixtures from the current code

Since phase 1 (exact Lab, D17) the fixtures are `golden_<case>_exact.tif`; `--capture` writes that set (`--exact`
is accepted as an alias). The phase-0 set, made with OpenCV's Lab, lives in the phase-0 commit; the exact set
differed from it by 68–79 ADU rms, which is OpenCV's conversion error leaving.

Fixtures are 16-bit RGB, zlib-compressed. The test applies T-GOLD (phase 2 onwards); `--report` gives
the T-GOLD statistics over the disk, excluding interior pixels whose input L* < 1, which the old code froze
and the chain engine processes (plan, phase 2).
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np
import tifffile

if __package__:
    from .synthetic import lunar_disk
else:
    from synthetic import lunar_disk
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import filters  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent/'fixtures'
DISK_PX = 768

# Every finishing setting, pinned so the run cannot inherit .env. Empty is "off" for the three str keys.
PINNED = dict(LUNAR_FINISH_WAVELETS='', LUNAR_FINISH_WAVELET_KERNEL='linear', LUNAR_FINISH_WAVELET_DENOISE='',
              LUNAR_FINISH_USM='', LUNAR_FINISH_USM_MODE='luminosity', LUNAR_FINISH_HIGHLIGHTS='0.3',
              LUNAR_FINISH_KNEE_PERCENTILE='90', LUNAR_FINISH_SATURATION='2',
              LUNAR_FINISH_SATURATION_PASSES='2', LUNAR_FINISH_CONTRAST='0.12',
              LUNAR_FINISH_CHROMA_DENOISE='3', LUNAR_FINISH_LIMB_TAPER='0',
              LUNAR_FINISH_NORTH_UP='0', LUNAR_FINISH_PREVIEW='0', LUNAR_FINISH_DISPLAY_TIF='0')

TONE = ['--highlights', '0.3', '--knee-percentile', '90', '--saturation', '2', '--saturation-passes', '2',
        '--contrast', '0.12', '--chroma-denoise', '3', '--limb-taper', '0',
        '--keep-orientation', '--no-preview', '--no-display-tif']
SHARPEN = ['--wavelets', '0.6,1.1,0.2,0', '--wavelet-kernel', 'linear',
           '--usm', '60,1.3,0', '--usm-mode', 'luminosity']

CASES = {'a_tone': dict(flags=TONE+['--no-wavelets'], mono=False),
         'b_sharpened': dict(flags=TONE+SHARPEN, mono=False),
         'c_mono': dict(flags=TONE+['--no-wavelets'], mono=True)}


def scene(mono=False):
    return lunar_disk(DISK_PX, mono=mono)


def run_case(name, workdir):
    """Run lunar_finish on the case's synthetic disk; returns its 16-bit output in RGB order."""
    case = CASES[name]
    source = scene(case['mono'])
    work = Path(workdir)
    src, out = work/f'{name}_in.tif', work/f'{name}_out.tif'
    image = source['image']
    tifffile.imwrite(src, image, photometric='minisblack' if image.ndim == 2 else 'rgb')
    env = dict(**{k: v for k, v in os.environ.items() if not k.startswith('LUNAR_FINISH_')}, **PINNED)
    done = subprocess.run([sys.executable, str(ROOT/'mosaic'/'lunar_finish.py'), str(src), '-o', str(out), *case['flags']],
                          capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=900)
    if done.returncode != 0 or not out.is_file():
        raise RuntimeError(f'lunar_finish failed for {name}:\n{done.stdout}\n{done.stderr}')
    written = cv2.imread(str(out), cv2.IMREAD_UNCHANGED)
    if written is None or written.dtype != np.uint16 or written.ndim != 3:
        raise RuntimeError(f'{name}: expected a 16-bit colour TIFF from lunar_finish.')
    return written[..., ::-1].copy(), source, done.stdout


def metrics(result, reference, source):
    """T-GOLD statistics in ADU over the disk, excluding interior pixels the old code froze (input L* < 1, exact
    Lab as the phase-1 fixtures' old rule used it): the chain engine processes them (plan D7)."""
    img = source['image'] if source['image'].ndim == 3 else np.repeat(source['image'][..., None], 3, axis=2)
    frozen = source['disk'] & (filters.lstar(img.astype(np.float32)/65535) < 1)
    mask = source['disk'] & ~frozen
    d = np.abs(result[mask].astype(np.float64)-reference[mask].astype(np.float64))
    f = np.abs(result[frozen].astype(np.float64)-reference[frozen].astype(np.float64))
    return dict(rms=float(np.sqrt((d**2).mean())) if d.size else 0.0,
                p999=float(np.percentile(d, 99.9)) if d.size else 0.0,
                worst=float(d.max()) if d.size else 0.0,
                excluded=int(frozen.sum()), compared=int(mask.sum()),
                frozen_worst=float(f.max()) if f.size else 0.0)


def fixture_path(name, exact=False):
    return FIXTURES/f'golden_{name}{"_exact" if exact else ""}.tif'


def stored_set():
    """Prefer the phase-1 exact-Lab fixtures once the whole set exists; else the phase-0 set."""
    if all(fixture_path(n, True).is_file() for n in CASES):
        return True
    return False


def capture(exact=False):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='golden-') as work:
        for name in CASES:
            result, _, _ = run_case(name, work)
            path = fixture_path(name, exact)
            tifffile.imwrite(path, result, photometric='rgb', compression='zlib')
            print(f'{path.name}  {result.shape[1]}x{result.shape[0]}  {path.stat().st_size/1e6:.2f} MB')
    total = sum(fixture_path(n, exact).stat().st_size for n in CASES)/1e6
    print(f'{len(CASES)} fixtures, {total:.2f} MB total')


def report():
    exact = stored_set()
    print(f'comparing against the {"exact-Lab (phase 1)" if exact else "phase-0"} fixtures')
    with tempfile.TemporaryDirectory(prefix='golden-') as work:
        for name in CASES:
            result, source, _ = run_case(name, work)
            reference = tifffile.imread(fixture_path(name, exact))
            m = metrics(result, reference, source)
            same = 'identical' if np.array_equal(result, reference) else 'DIFFERS'
            print(f'{name:14s} {same:9s} rms {m["rms"]:8.2f}  p99.9 {m["p999"]:8.1f}  max {m["worst"]:8.1f} ADU'
                  f'   ({m["compared"]} px compared, {m["excluded"]} frozen px excluded, which moved up to '
                  f'{m["frozen_worst"]:.0f} ADU)')


class Golden(unittest.TestCase):
    """The refactor must not change these outputs; phase 1 re-captures them once, for exact Lab."""

    @classmethod
    def setUpClass(cls):
        cls.exact = stored_set()
        if not cls.exact and not all(fixture_path(n).is_file() for n in CASES):
            raise unittest.SkipTest('No golden fixtures yet. Run: python mosaic/tests/golden.py --capture')
        cls.work = tempfile.TemporaryDirectory(prefix='golden-')

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def check(self, name):
        """T-GOLD (plan, phase 2): rms ≤ 1 ADU and p99.9 ≤ 16 ADU over the disk, excluding the interior shadows
        the old code froze. If it fails, find the cause; do not raise the tolerance."""
        result, source, _ = run_case(name, self.work.name)
        reference = tifffile.imread(fixture_path(name, self.exact))
        self.assertEqual(result.shape, reference.shape, f'{name}: output size changed')
        m = metrics(result, reference, source)
        self.assertTrue(m['rms'] <= 1 and m['p999'] <= 16,
                        f'{name}: rms {m["rms"]:.2f}, p99.9 {m["p999"]:.1f}, max {m["worst"]:.1f} ADU over '
                        f'{m["compared"]} disk px (limits rms 1, p99.9 16).')

    def test_a_tone(self):
        self.check('a_tone')

    def test_b_sharpened(self):
        self.check('b_sharpened')

    def test_c_mono_stays_mono(self):
        """A mono input should come out mono. Today it does not, and the cause is OpenCV's Lab conversion:
        one float BGR->Lab->BGR round trip gives a perfectly grey image a 70 ADU channel spread, because it
        puts |a*|,|b*| up to 0.125 on a neutral colour instead of 0. Exact Lab (plan D17, phase 1) fixes it,
        so the bound here tightens to 1 ADU as soon as the exact-Lab fixtures exist."""
        self.check('c_mono')
        result, _, _ = run_case('c_mono', self.work.name)
        spread = int(np.abs(result.astype(np.int32)-result[..., 1:2].astype(np.int32)).max())
        limit = 1 if self.exact else 80
        self.assertLessEqual(spread, limit,
                             f'Mono input came out with a {spread} ADU channel spread (limit {limit}).')
        if not self.exact:
            print(f'\n  note: mono channel spread is {spread} ADU; exact Lab (D17) must bring it to 0–1.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--capture', action='store_true', help='(re)write the fixtures from the current code')
    ap.add_argument('--exact', action='store_true', help='alias kept for the plan text; --capture always writes the exact set')
    ap.add_argument('--report', action='store_true', help='print rms / p99.9 / max against the stored fixtures')
    args = ap.parse_args()
    if args.capture:
        capture(True)
    elif args.report:
        report()
    else:
        unittest.main(argv=[sys.argv[0]])
