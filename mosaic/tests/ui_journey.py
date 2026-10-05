"""Phase 7 acceptance (IMPLEMENTATION_PLAN.md): the browser journey, the interface end to end.

On synthetic sets — two luminance sets at different scales and one colour set — in headless Chrome: import →
build → the placement chip shows measured numbers → add wavelets, white balance and curves → draw previews →
change a parameter (every preview renders again) → rotate the view (previews follow) → reorder by keyboard →
reload (state identical) → load a chain file → export a TIFF → close and reopen the project; no console errors.

The page is driven through the Chrome DevTools protocol by ui_journey.js (Node's own WebSocket, no npm packages);
this file makes the data, starts the application and Chrome, and checks the files the journey leaves. Needs
Google Chrome (or Chromium) and Node.js 22 or later; skipped without them.
"""
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import tifffile

from app import Application, start_server
from mixed_checks import noisy, sharp_disk
from synthetic import cut_panels

HERE = Path(__file__).resolve().parent
CHROMES = ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
           '/Applications/Chromium.app/Contents/MacOS/Chromium',
           'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')


def chrome():
    for c in CHROMES:
        found = c if Path(c).is_file() else shutil.which(c)
        if found:
            return found
    return None


def journey_sets(root, size=1536):
    """Fine luminance panels (0.299″/px) over the upper left of one synthetic Moon, 2× coarser ones over all of it,
    and colour panels 2× coarser; returns the panel count of each folder. Smaller panels fall below the builder's
    150 features or colour placement's four tiles, and are left out, as they would be in real data."""
    scene = sharp_disk(size)
    lum = np.rint(scene.astype(np.float32).mean(axis=2)).astype(np.uint16)
    rng = np.random.default_rng(5)
    counts = {}
    for name, img, px, scale in (('fine', lum, 640, 1), ('coarse', lum, 448, 2), ('colour', scene, 400, 2)):
        folder = root/name
        folder.mkdir()
        n = 0
        for p in cut_panels(img, panel_px=px, overlap=.35, scales=(scale,), seed=930 + len(counts)):
            if name == 'fine' and max(p['origin_xy']) > size * .45:      # the coarse set supplies the rest
                continue
            tifffile.imwrite(folder/f"{name}-{n:02d}.tif", noisy(p['image'], rng),
                             photometric='rgb' if p['image'].ndim == 3 else 'minisblack')
            n += 1
        counts[name] = n
    return counts


class Journey(unittest.TestCase):
    def test_browser_journey(self):
        browser, node = chrome(), shutil.which('node')
        if not browser or not node:
            self.skipTest('The browser journey needs Google Chrome (or Chromium) and Node.js.')
        with tempfile.TemporaryDirectory(prefix='journey-') as tmp:
            root = Path(tmp)
            counts = journey_sets(root)
            app = Application(history_path=root/'recent.json')
            srv = start_server(0, app=app, token=secrets.token_urlsafe(16), log=lambda *a, **k: None)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            profile = root/'chrome'
            proc = subprocess.Popen([browser, '--headless=new', '--remote-debugging-port=0', f'--user-data-dir={profile}',
                                     '--no-first-run', '--no-default-browser-check', '--window-size=1600,1000',
                                     '--disable-background-timer-throttling', 'about:blank'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file, t0 = profile/'DevToolsActivePort', time.time()
                while not port_file.is_file() or not port_file.read_text().strip():
                    if time.time() - t0 > 30:
                        self.fail('Chrome did not start its DevTools endpoint.')
                    time.sleep(.1)
                port = port_file.read_text().split()[0]
                cfg = dict(folder=str(root/'Journey.lunarmosaic'), fine=str(root/'fine'), coarse=str(root/'coarse'),
                           colour=str(root/'colour'), counts=counts, export=str(root/'out'/'Moon-LRGB.tif'),
                           chain_file=str(root/'imported.chain.json'))
                done = subprocess.run([node, str(HERE/'ui_journey.js'), port, srv.url(secret=True), json.dumps(cfg)],
                                      capture_output=True, text=True, timeout=1800)
                print(done.stdout)
                self.assertEqual(done.returncode, 0, done.stdout[-8000:] + done.stderr[-4000:])
                self.assertIn('PASS journey', done.stdout)
                # what the journey left: the export, at the rotated canvas size, and the project as it was edited
                data = json.loads((Path(cfg['folder'])/'project.json').read_text('utf-8'))
                image = tifffile.imread(cfg['export'])
                self.assertEqual((image.dtype, image.ndim, image.shape[2]), (np.uint16, 3, 3))
                width, height = data['luminance']['width'], data['luminance']['height']
                self.assertEqual(image.shape[:2], (width, height))              # turned by 90°
                self.assertGreater(float(image.mean()), 1000)
                self.assertEqual(data['geometry']['rotation'], 90)
                chains = data['chain']['chains']
                self.assertEqual([e['type'] for e in chains['luminance']], ['wavelets'])
                self.assertEqual([e['type'] for e in chains['colour']], ['white_balance'])
                self.assertEqual([e['type'] for e in chains['final']], ['contrast', 'curves'])   # reordered by keyboard
                self.assertEqual(chains['final'][0]['params']['strength'], 0.2)                # from the chain file
                self.assertEqual(len(data['chain']['previews']), 2)
                self.assertEqual(sorted(s['id'] for s in data['sets']), ['L', 'RGB', 's2'])
                self.assertEqual(data['colour']['summary']['placed'], counts['colour'])
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                srv.shutdown()
                srv.server_close()
                app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
