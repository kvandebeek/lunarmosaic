"""Phase 8 acceptance (IMPLEMENTATION_PLAN.md): every command in docs/COMMANDS.md runs on synthetic data.

One session folder, as the documentation assumes: `panels/` (fine mono panels over the whole synthetic Moon),
`wide/` (2× coarser ones) and `panels_rgb/` (colour panels, 2× coarser). Each command runs as a subprocess with
the arguments of its documented examples, adapted only where an example names real files (panel ids, the session's
own paths); previews are switched off through their settings (MOSAIC_PREVIEW, LUNAR_FINISH_PREVIEW), never by
changing the command line. No command may print a traceback. The last test checks that every command the
reference lists was run, and the reference itself is checked to be current (update_docs.py --check).
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import tifffile

from mixed_checks import noisy, sharp_disk
from synthetic import cut_panels

MOSAIC = Path(__file__).resolve().parents[1]
ROOT = MOSAIC.parent
RAN = set()


def session(folder, size=1536):
    """The synthetic session: fine mono panels over the whole disk, 2× coarser mono ones, 2× coarser colour ones."""
    scene = sharp_disk(size)
    lum = np.rint(scene.astype(np.float32).mean(axis=2)).astype(np.uint16)
    rng = np.random.default_rng(8)
    counts = {}
    for name, img, px, scale in (('panels', lum, 640, 1), ('wide', lum, 448, 2), ('panels_rgb', scene, 400, 2)):
        (folder/name).mkdir()
        cut = cut_panels(img, panel_px=px, overlap=.35, scales=(scale,), seed=940 + len(counts))
        for i, p in enumerate(cut):
            tifffile.imwrite(folder/name/f'{name}-{i:02d}.tif', noisy(p['image'], rng),
                             photometric='rgb' if p['image'].ndim == 3 else 'minisblack')
        counts[name] = len(cut)
    return counts


class Commands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='cli-')
        cls.dir = Path(cls.work.name)
        cls.counts = session(cls.dir)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_cmd(self, name, *args, ok=True, timeout=900, settings=None):
        """python3 <command> ARGS… in the session folder, as COMMANDS.md shows it; `settings` as environment variables."""
        script = ROOT/name if name == 'lunarmosaic.py' else MOSAIC/name
        done = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=self.dir, capture_output=True, text=True,
                              timeout=timeout, env=dict(os.environ, MOSAIC_PREVIEW='0', LUNAR_FINISH_PREVIEW='0', **(settings or {})))
        RAN.add(name)
        out = done.stdout + done.stderr
        self.assertNotIn('Traceback', out, f'{name} {" ".join(map(str, args))}:\n{out[-4000:]}')
        if ok:
            self.assertEqual(done.returncode, 0, f'{name} {" ".join(map(str, args))}:\n{out[-4000:]}')
        return done

    def test_01_panel_classifier(self):
        self.run_cmd('panel_classifier.py', 'panels', '-o', 'panels/panel_manifest.json', '--relative')
        manifest = json.loads((self.dir/'panels'/'panel_manifest.json').read_text())
        self.assertEqual(len(manifest['images']), self.counts['panels'])
        self.assertTrue(manifest['pairs'])

    def test_02_mosaic_builder(self):
        self.run_cmd('mosaic_builder.py', 'panels', '-o', 'check.tif', '--stop-after', '3')      # layout only
        self.assertTrue((self.dir/'check_layout.json').is_file())
        self.assertFalse((self.dir/'check.tif').exists())
        self.run_cmd('mosaic_builder.py', 'panels', '-o', 'mosaic.tif')
        self.assertEqual(tifffile.imread(self.dir/'mosaic.tif').dtype, np.uint16)
        self.run_cmd('mosaic_builder.py', 'panels', '-o', 'movie.tif', '--no-preview', '--record', 'build.mp4',
                     '--record-vertical', 'build_9x16.mp4')
        for movie in ('build.mp4', 'build_9x16.mp4'):
            self.assertGreater((self.dir/movie).stat().st_size, 100_000)

    def test_03_colour_place(self):
        self.run_cmd('colour_place.py', 'panels_rgb', '--canvas', 'mosaic.tif', '-o', 'colour_canvas.tif', '--scale-ratio', '2')
        report = json.loads((self.dir/'colour_place.json').read_text())
        self.assertGreater(report['summary']['placed'], self.counts['panels_rgb'] // 2)
        self.assertEqual(tifffile.imread(self.dir/'colour_canvas.tif').shape[2], 4)     # RGBA: alpha is coverage

    def test_04_lunar_finish(self):
        done = self.run_cmd('lunar_finish.py', 'mosaic.tif', '-o', 'mosaic_finished.tif')
        self.assertIn('orientation kept as captured', done.stdout)        # a synthetic Moon has no real terrain to find
        self.assertTrue((self.dir/'mosaic_finished.tif').is_file())
        # north-up was tried above; its 20 s search of a synthetic Moon is not repeated
        self.run_cmd('lunar_finish.py', 'mosaic.tif', '-o', 'mosaic_finished.tif', '--saturation', '3', '--record', 'finish.mp4',
                     settings=dict(LUNAR_FINISH_NORTH_UP='0'))
        self.assertGreater((self.dir/'finish.mp4').stat().st_size, 100_000)

    def test_05_lunarmosaic_new_and_build(self):
        done = self.run_cmd('lunarmosaic.py', 'new', 'moon.lunarmosaic', '--name', 'CLI Moon',
                            '--luminance', 'panels', '--optics', '2.9,2000', '--label', 'Fine',
                            '--luminance', 'wide', '--optics', '2,9;1000', '--label', 'Wide field',
                            '--colour', 'panels_rgb', '--optics', '2.9,1000,1,250')
        project = json.loads(done.stdout)
        self.assertEqual([(s['id'], s['stream'], s['label'], len(s['inputs'])) for s in project['sets']],
                         [('L', 'luminance', 'Fine', self.counts['panels']), ('RGB', 'colour', 'Colour', self.counts['panels_rgb']),
                          ('s2', 'luminance', 'Wide field', self.counts['wide'])])
        self.assertEqual(project['sets'][1]['optics'], dict(pixel_um=2.9, focal_mm=1000, barlow=1, aperture_mm=250))
        self.assertEqual(project['sets'][2]['optics'], dict(pixel_um=2.9, focal_mm=1000))      # "2,9;1000": decimal comma
        done = self.run_cmd('lunarmosaic.py', 'build', 'moon.lunarmosaic')
        self.assertEqual(json.loads(done.stdout[done.stdout.rindex('{'):]), {'L': 'built', 'RGB': 'built'})
        self.assertIn('[8/8]', done.stdout)                              # the build is shown as it runs
        status = json.loads(self.run_cmd('lunarmosaic.py', 'status', 'moon.lunarmosaic').stdout)
        self.assertEqual(status['colour']['summary']['placed'], status['match']['inliers'])
        self.run_cmd('lunarmosaic.py', 'match', 'moon.lunarmosaic')
        checked = json.loads(self.run_cmd('lunarmosaic.py', 'check-match', 'moon.lunarmosaic').stdout)
        self.assertGreater(checked['measured'], 0)
        done = self.run_cmd('lunarmosaic.py', 'north-up', 'moon.lunarmosaic', ok=False)
        if done.returncode:                                              # no real terrain: a clear refusal
            self.assertIn('LunarMosaic: North-up unavailable', done.stderr)

    def test_06_lunarmosaic_chain_and_export(self):
        self.run_cmd('lunarmosaic.py', 'chain', 'export', 'moon.lunarmosaic', 'moon.chain.json')
        doc = json.loads((self.dir/'moon.chain.json').read_text())
        self.assertEqual(doc['format'], 'lunarmosaic-chain')
        doc['chains']['luminance'].append(dict(id='0123abcd', type='wavelets', enabled=True, limb_fade_px=0,
                                               params=dict(biases=[0, 1.0, 0, 0], kernel='linear', denoise=[0, 0, 0, 0])))
        doc['chains']['final'].append(dict(id='4567cdef', type='contrast', enabled=True, limb_fade_px=0, params=dict(strength=.2)))
        (self.dir/'moon.chain.json').write_text(json.dumps(doc))
        done = self.run_cmd('lunarmosaic.py', 'chain', 'import', 'moon.lunarmosaic', 'moon.chain.json')
        self.assertEqual({k: v for k, v in json.loads(done.stdout).items() if k != 'file'},
                         dict(luminance=['wavelets'], colour=[], final=['contrast']))
        stored = json.loads((self.dir/'moon.lunarmosaic'/'project.json').read_text())['chain']
        self.assertNotIn(stored['chains']['luminance'][0]['id'], ('0123abcd',))     # imported blocks get fresh ids
        self.run_cmd('lunarmosaic.py', 'export', 'moon.lunarmosaic', '-o', 'Moon-LRGB.tif')
        image = tifffile.imread(self.dir/'Moon-LRGB.tif')
        self.assertEqual((image.dtype, image.shape[2]), (np.uint16, 3))
        self.run_cmd('lunarmosaic.py', 'export', 'moon.lunarmosaic', '-o', 'luminance.png', '--layer', 'luminance', '--scale', '0.5')
        self.assertTrue((self.dir/'luminance.png').is_file())

    def test_07_lunar_finish_runs_the_chain(self):
        done = self.run_cmd('lunar_finish.py', 'mosaic.tif', '-o', 'mosaic_chain.tif', '--chain', 'moon.chain.json',
                            settings=dict(LUNAR_FINISH_NORTH_UP='0'))
        self.assertIn('chain: contrast', done.stdout)
        self.assertIn('luminance chain: wavelets', done.stdout)

    def test_08_lunarmosaic_scales(self):
        self.run_cmd('lunarmosaic.py', 'new', 'mixed.lunarmosaic', '--luminance', 'panels', '--optics', '2.9,2000',
                     '--luminance', 'wide')
        done = self.run_cmd('lunarmosaic.py', 'build', 'mixed.lunarmosaic', ok=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn('needs its optics (pixel size and focal length) or an estimated scale', done.stderr)
        estimate = json.loads(self.run_cmd('lunarmosaic.py', 'scales', 'mixed.lunarmosaic').stdout)
        self.assertAlmostEqual(estimate['s2']['ratio'], 2.0, delta=.02)
        self.assertFalse(estimate['s2']['confirmed'])
        confirmed = json.loads(self.run_cmd('lunarmosaic.py', 'scales', 'mixed.lunarmosaic', '--confirm').stdout)
        self.assertTrue(confirmed['s2']['confirmed'])
        done = self.run_cmd('lunarmosaic.py', 'build', 'mixed.lunarmosaic', '--max-canvas-mpx', '50')
        self.assertEqual(json.loads(done.stdout[done.stdout.rindex('{'):]), {'L': 'built'})
        project = json.loads((self.dir/'mixed.lunarmosaic'/'project.json').read_text())
        self.assertEqual(project['luminance']['max_canvas_mpx'], 50)      # the confirmed size, kept with the stream

    def test_09_lunarmosaic_app(self):
        proc = subprocess.Popen([sys.executable, str(ROOT/'lunarmosaic.py'), 'moon.lunarmosaic', '--no-browser', '--no-history',
                                 '--port', '18768'], cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        RAN.add('lunarmosaic.py')
        try:
            line, t0 = '', time.time()
            while 'LunarMosaic: http' not in line and time.time() - t0 < 60:
                line = proc.stdout.readline()
            url = re.search(r'http://\S+', line)[0]
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
            with opener.open(url, timeout=30):                            # the token link sets the session cookie
                pass
            with opener.open(url.split('?')[0], timeout=30) as r:          # and its page moves on to the interface
                self.assertIn(b'<title>LunarMosaic</title>', r.read())
            with opener.open(url.split('?')[0] + 'api/state', timeout=30) as r:
                self.assertEqual(json.load(r)['project']['name'], 'CLI Moon')
        finally:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                proc.kill()
        out = proc.stdout.read()
        self.assertNotIn('Traceback', out)

    def test_10_compare_crops(self):
        manifest = json.loads((self.dir/'panels'/'panel_manifest.json').read_text())
        best = max(manifest['pairs'], key=lambda p: p['inliers'])
        # synthetic panels share less than the default 400 px crop needs: --size, the documented option, says so
        self.run_cmd('tools/compare_crops.py', 'panels/panel_manifest.json', '--pair', f"{best['a']},{best['b']}", '-o', 'compare.png',
                     '--size', '160')
        self.assertTrue((self.dir/'compare.png').is_file())
        usable = sum(p['inliers'] > 300 for p in manifest['pairs'])
        self.run_cmd('tools/compare_crops.py', 'panels/panel_manifest.json', '--blind', str(min(6, usable)), '-o', 'blind/',
                     '--size', '160')
        self.assertTrue((self.dir/'blind'/'key.json').is_file())

    def test_11_edge_falloff(self):
        self.run_cmd('tools/edge_falloff.py', 'panels/panel_manifest.json', '--by', 'side')

    def test_12_render_mockups(self):
        layout = json.loads((self.dir/'mosaic_layout.json').read_text())
        ids = sorted(layout['panels'])
        self.run_cmd('design_mockups/render_mockups.py', '--layout', 'mosaic_layout.json', '--view', 'natural',
                     '--stage1-panel', ids[0], '--stage2-pair', f'{ids[0]},{ids[1]}', '--out', 'mockups')
        self.assertTrue(any((self.dir/'mockups').glob('*.png')))

    def test_13_update_docs(self):
        self.run_cmd('tools/update_docs.py', '--check')                  # the reference matches every --help today

    def test_99_every_documented_command_ran(self):
        table = re.findall(r'^\| \[`([^`]+)`\]', (MOSAIC/'docs'/'COMMANDS.md').read_text(), re.M)
        self.assertGreaterEqual(len(table), 9)
        self.assertEqual(sorted(set(table) - RAN), [], 'documented commands this suite did not run')


if __name__ == '__main__':
    unittest.main(verbosity=2)
