"""Phase 4 acceptance checks (IMPLEMENTATION_PLAN.md): schema 4, the application rendering through chains.

T-MIGRATE  a schema-3 project with every adjustment changed migrates to its chain and reopens without a save
T-PREVIEW  a 700 × 500 preview at 23.7° — in process and through /api/render, 16-bit — equals the export there
T-COVER    an export with common coverage equals the same region of a full-coverage export
T-STALE    of two clients, the one posting with an old revision gets 409 and the other's edit stands
T-WORKER   moving a slider during an export neither fails nor changes the export; the newest statistics win
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
import tifffile

import chain
from app import Application, start_server
from lrgb import manual_match
from project import DEFAULT_EDITS, Project, edits_to_chain
from synthetic import lunar_disk

SHARP = dict(DEFAULT_EDITS, wavelets=[1.5, .5, 0, 0], wavelet_kernel='b3', wavelet_denoise=1.0, usm_amount=.4,
             usm_radius=1.3, usm_threshold=.005, highlights=.5, contrast=.3, smoothing=2, saturation=1.7, strength=.9)


def colour_match():
    """Colour px -> luminance px: twice as coarse, turned 3°, offset so the colour misses the canvas's top and left."""
    a = np.radians(-3)
    return np.array([[2*np.cos(a), -2*np.sin(a), 40], [2*np.sin(a), 2*np.cos(a), 70], [0, 0, 1]])


class Fixture:
    """A built project: a 1024 px mono luminance disk and a colour mosaic of it at half the scale, matched."""

    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        lum = lunar_disk(1024, mono=True)['image']
        rgb = lunar_disk(1024)['image']
        M = colour_match()
        colour = cv2.warpAffine(rgb, np.linalg.inv(M)[:2], (480, 440), flags=cv2.INTER_LINEAR)
        tifffile.imwrite(self.root/'L.tif', lum)
        tifffile.imwrite(self.root/'RGB.tif', colour, photometric='rgb')
        self.app = Application()
        self.app.select(self.root/'moon.lunarmosaic', True, 'Scene checks')
        self.app.inputs('L', [str(self.root/'L.tif')])
        self.app.inputs('RGB', [str(self.root/'RGB.tif')])
        assert self.app.build(['L', 'RGB']) == {'L': 'built', 'RGB': 'built'}
        src = np.array([[20, 20], [460, 20], [20, 420], [460, 420]], float)
        dst = (np.c_[src, np.ones(4)] @ M.T)[:, :2]
        p = self.app.project
        p.match = manual_match(src, dst)
        p.save()

    def close(self):
        self.app.close()
        self.temp.cleanup()


def server(app):
    srv = start_server(0, app=app, token='scene-token')
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def request(path, data=None):
        headers = {'X-LA-Token': 'scene-token'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(srv.url()+path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        return urllib.request.urlopen(req)

    def stop():
        srv.shutdown()
        srv.server_close()
        t.join()
    return request, stop


class Migration(unittest.TestCase):
    def test_t_migrate_schema3_to_chain_and_second_open_is_a_noop(self):
        f = Fixture()
        try:
            p = f.app.project
            edits = dict(saturation=1.7, smoothing=3.0, strength=.8, highlights=.4, contrast=-.3, rotation=12.0,
                         coverage='common', mirror=True, wavelets=[.5, 1.0, 0.0, .2], wavelet_kernel='b3',
                         wavelet_denoise=1.5, usm_amount=.3, usm_radius=1.4, usm_threshold=.02, l_visible=False,
                         rgb_visible=False)
            self.assertTrue(all(edits[k] != DEFAULT_EDITS[k] for k in DEFAULT_EDITS))
            sets = {s['id']: {k: v for k, v in s.items() if k not in ('id', 'stream', 'label')} for s in p.data['sets']}
            old = dict(version=3, id=p.data['id'], name=p.data['name'], created=p.data['created'],
                       updated=p.data['updated'], revision=7, sets=sets, match=p.match, orientation=None, edits=edits)
            folder = p.folder
            f.app.close()
            (folder/'project.json').write_text(json.dumps(old), 'utf-8')
            original = (folder/'project.json').read_bytes()

            q = Project.open(folder)
            d = q.data
            self.assertEqual(d['version'], 4)
            self.assertEqual((folder/'project.v3.json').read_bytes(), original)
            self.assertEqual(d['revision'], 8)                       # saved once
            self.assertEqual(q.edits, edits)
            self.assertEqual(q.match, old['match'])
            self.assertEqual(d['geometry'], dict(rotation=12.0, mirror=True, coverage='common'))
            self.assertEqual(d['view'], dict(l_visible=False, rgb_visible=False))
            self.assertEqual([(s['id'], s['stream'], s['mosaic']) for s in d['sets']],
                             [('L', 'luminance', 'derived/L.tif'), ('RGB', 'colour', 'derived/RGB.tif')])
            ch = d['chain']['chains']
            self.assertTrue(d['chain']['protect_space'])
            self.assertEqual([e['type'] for e in ch['luminance']], ['wavelets', 'unsharp_mask', 'highlights', 'contrast'])
            self.assertEqual([e['type'] for e in ch['colour']], ['gaussian_blur', 'saturation'])
            self.assertEqual(ch['final'], [])
            self.assertEqual(ch['merge'], {'strength': .8})
            lum = {e['type']: e['params'] for e in ch['luminance']}
            np.testing.assert_allclose(lum['wavelets']['biases'], [.05, .1, 0, .02])     # same gains 1 + a
            self.assertEqual((lum['wavelets']['kernel'], lum['wavelets']['denoise']), ('b3', [1.5]*4))
            self.assertEqual(lum['unsharp_mask']['mode'], 'luminosity')
            np.testing.assert_allclose([lum['unsharp_mask'][k] for k in ('amount', 'radius', 'threshold')], [30, 1.4, 5.1])
            np.testing.assert_allclose([lum['highlights']['strength'], lum['contrast']['strength']], [.196, -.147])
            col = {e['type']: e['params'] for e in ch['colour']}
            self.assertEqual(col['gaussian_blur'], {'sigma': 3.0})
            self.assertEqual(col['saturation'], dict(factor=1.7, passes=1, lunar_hue=False, chroma_blur_px=0.0))
            self.assertEqual(len(d['notes']), 4)
            self.assertEqual(chain.validate(q.chain_doc()), [])

            after_first = (folder/'project.json').read_bytes()
            again = Project.open(folder)
            self.assertEqual(again.data, d)
            self.assertEqual((folder/'project.json').read_bytes(), after_first)     # no save, same revision
            # the translation is deterministic: the same adjustments give the same chain, so statistics stay cached
            self.assertEqual(edits_to_chain(edits)[0], edits_to_chain(copy.deepcopy(edits))[0])
        finally:
            f.close()


class Rendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.f.close()

    def export(self, name, edits, factor=1):
        out = self.f.root/name
        self.f.app.get_scene().export(out, edits, factor)
        return tifffile.imread(out).astype(np.int32)

    def test_t_preview_equals_export_in_process_and_over_http(self):
        p, scene = self.f.app.project, self.f.app.get_scene()
        edits = dict(SHARP, rotation=23.7)
        p.edits = edits
        p.save()
        full = self.export('preview-full.tif', edits)
        x, y, w, h = 310, 420, 700, 500
        self.assertGreaterEqual(min(full.shape[0]-y-h, full.shape[1]-x-w), 0)
        matrix, _, _ = scene.geometry(edits)
        rgb, _ = scene.render_view(matrix, (w, h), p.chain_doc(), origin=(x, y))
        region = full[y:y+h, x:x+w]
        self.assertLessEqual(int(np.abs(np.round(rgb*65535).astype(np.int32)-region).max()), 1)
        request, stop = server(self.f.app)
        try:
            with request(f'api/render?id={p.data["id"]}&x={x}&y={y}&w={w}&h={h}&depth=16') as r:
                png = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertEqual((png.dtype, png.shape), (np.dtype(np.uint16), (h, w, 3)))
            self.assertLessEqual(int(np.abs(png[..., ::-1].astype(np.int32)-region).max()), 1)
            # a preview rectangle of the chain document renders the same pixels
            p.data['chain']['previews'] = [dict(id='p01', name='Copernicus', x=x, y=y, w=w, h=h)]
            p.save()
            with request(f'api/render?id={p.data["id"]}&preview=p01') as r:
                again = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertTrue(np.array_equal(again, png))
            with request(f'api/render?id={p.data["id"]}&x={x}&y={y}&w=64&h=64&stage=luminance&depth=8') as r:
                grey = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertEqual((grey.dtype, int(np.ptp(grey.astype(int), axis=2).max())), (np.dtype(np.uint8), 0))
        finally:
            stop()
            p.data['chain']['previews'] = []
        # sharpening and tone took effect: this is not the unprocessed view
        plain, _ = scene.render_view(matrix, (w, h), edits_to_chain(DEFAULT_EDITS)[0], origin=(x, y))
        self.assertGreater(float(np.abs(plain*65535-region).mean()), 200)

    def test_t_cover_common_crop_equals_full_export_region(self):
        scene = self.f.app.get_scene()
        edits = dict(SHARP, rotation=9.0, mirror=True)
        common = dict(edits, coverage='common')
        _, (w, h), box = scene.geometry(common)
        self.assertGreater(box[0]+box[1], 0)
        full = self.export('cover-full.tif', edits)
        crop = self.export('cover-common.tif', common)
        self.assertEqual(crop.shape[:2], (h, w))
        self.assertLessEqual(int(np.abs(full[box[1]:box[1]+h, box[0]:box[0]+w]-crop).max()), 1)

    def test_half_scale_export_is_the_reduced_full_export(self):
        edits = dict(SHARP, rotation=23.7)
        full = self.export('scale-full.tif', edits).astype(np.float64)
        half = self.export('scale-half.tif', edits, .5)
        h, w = half.shape[:2]
        reduced = full[:2*h, :2*w].reshape(h, 2, w, 2, 3).mean(axis=(1, 3))
        self.assertLessEqual(float(np.abs(reduced-half).max()), 1.0)

    def test_approximate_view_runs_the_chain_at_the_zoom(self):
        scene = self.f.app.get_scene()
        doc = edits_to_chain(SHARP)[0]
        matrix, size, _ = scene.geometry(SHARP)
        m = np.diag([.3, .3, 1]) @ matrix
        small = (round(size[0]*.3), round(size[1]*.3))
        exact, _ = scene.render_view(m, small, doc, precise=True)
        rough, _ = scene.render_view(m, small, doc, precise=False)
        self.assertEqual(rough.shape, exact.shape)
        self.assertLess(abs(float(rough.mean())-float(exact.mean())), .01)
        self.assertLess(float(np.abs(rough-exact).mean()), .02)


class Clients(unittest.TestCase):
    def test_t_stale_second_client_gets_409_and_first_edit_stands(self):
        f = Fixture()
        request, stop = server(f.app)
        try:
            with request('api/state') as r:
                seen = json.load(r)['project']
            a = dict(seen['edits'], saturation=2.0)
            b = dict(seen['edits'], smoothing=3.0)
            with request('api/edits', dict(id=seen['id'], revision=seen['revision'], edits=a)) as r:
                self.assertEqual(r.status, 200)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('api/edits', dict(id=seen['id'], revision=seen['revision'], edits=b))
            self.assertEqual(caught.exception.code, 409)
            with caught.exception as answer:
                self.assertTrue(json.load(answer)['conflict'])
            with request('api/state') as r:
                now = json.load(r)['project']
            self.assertEqual((now['edits']['saturation'], now['edits']['smoothing']), (2.0, 0.0))
            self.assertEqual(now['schema4']['chain']['chains']['colour'][0]['params']['factor'], 2.0)
            # the second client keeps its edit (ui_autosave checks the shipped JavaScript does) and retries
            retry = dict(now['edits'], smoothing=3.0)
            with request('api/edits', dict(id=now['id'], revision=now['revision'], edits=retry)) as r:
                final = json.load(r)['project']
            self.assertEqual((final['edits']['saturation'], final['edits']['smoothing']), (2.0, 3.0))
        finally:
            stop()
            f.close()


class Worker(unittest.TestCase):
    def test_t_worker_slider_during_export_neither_fails_nor_changes_it(self):
        f = Fixture()
        request, stop = server(f.app)
        try:
            p, scene = f.app.project, f.app.get_scene()
            edits = dict(SHARP, rotation=23.7)
            p.edits = edits
            p.save()
            reference = f.root/'reference.tif'
            scene.export(reference, edits)
            paused, release = threading.Event(), threading.Event()
            update = f.app.update_job

            def gated(**values):
                if str(values.get('title', '')).startswith('Writing image') and not paused.is_set():
                    paused.set()
                    release.wait(60)
                update(**values)
            f.app.update_job = gated
            with request('api/export', dict(id=p.data['id'], output=str(f.root/'during.tif'))) as r:
                self.assertEqual(json.load(r)['job']['state'], 'running')
            self.assertTrue(paused.wait(60))                       # the export has written its first band
            moved = dict(edits, saturation=2.6, wavelets=[2.0, .5, 0, 0])
            with request('api/edits', dict(id=p.data['id'], revision=p.data['revision'], edits=moved)) as r:
                self.assertEqual(r.status, 200)
            new_key = scene.stats_key(chain.normalised(p.chain_doc()))
            for _ in range(600):                                   # the worker computes the new statistics meanwhile
                if new_key in scene._stats:
                    break
                time.sleep(.05)
            self.assertIn(new_key, scene._stats)
            release.set()
            for _ in range(1200):
                if f.app.job['state'] != 'running':
                    break
                time.sleep(.05)
            self.assertEqual(f.app.job['state'], 'done', f.app.job.get('error'))
            self.assertTrue(np.array_equal(tifffile.imread(f.root/'during.tif'), tifffile.imread(reference)))
        finally:
            release.set()
            stop()
            f.close()

    def test_newest_request_wins_and_a_waiting_render_reuses_the_result(self):
        f = Fixture()
        try:
            scene, worker = f.app.get_scene(), f.app.worker
            slow = chain.normalised(dict(chain.empty(), chains=dict(
                luminance=[chain.entry('deconvolution', {'iterations': 40}), chain.entry('contrast', {})],
                colour=[], merge={'strength': 1.0}, final=[])))
            newest = edits_to_chain(dict(DEFAULT_EDITS, saturation=1.4))[0]
            worker.submit(scene, slow)
            slow_key = scene.stats_key(slow)
            for _ in range(600):
                if slow_key in scene._running:
                    break
                time.sleep(.01)
            self.assertIn(slow_key, scene._running)
            worker.submit(scene, newest)
            calls = []
            prepare = chain.prepare
            chain.prepare = lambda *a, **k: calls.append(1) or prepare(*a, **k)
            try:
                stats = scene.stats(newest)          # waits for the worker if it is on it, else computes it once
            finally:
                chain.prepare = prepare
            for _ in range(1200):
                if worker.thread is None:
                    break
                time.sleep(.05)
            self.assertIsNone(worker.thread)
            self.assertIsNone(worker.error)                              # stopped as stale, not failed
            self.assertNotIn(slow_key, scene._stats)                     # abandoned at a block boundary
            self.assertIs(scene._stats[scene.stats_key(newest)], stats)
            self.assertLessEqual(len(calls), 1)
        finally:
            f.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
