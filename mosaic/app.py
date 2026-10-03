"""Local LunarMosaic application, using the bundled LunarAtlas theme and HTTP guard."""
import copy
import base64
import html
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser

import cv2
import numpy as np
import tifffile
from project import Project
from lrgb import Image, Scene, register, manual_match
from alignment import measure
from recent import RecentProjects
import mosaic_common as mc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'moon_atlas'))
from atlas_view import start_server
from atlas_paths import file_lock

UI = ROOT / 'mosaic' / 'ui'


class Application:
    def __init__(self, history_path=None):
        self.lock = threading.RLock()
        self.project = None
        self.lease = None
        self.scene = None
        self.job = dict(state='idle')
        self.thread = None
        self.process = None
        self.cancel = threading.Event()
        self.history = RecentProjects(history_path) if history_path else None
        self.history_error = None

    def close(self):
        self.cancel_job()
        if self.thread:
            self.thread.join(timeout=15)
        if self.lease and not (self.thread and self.thread.is_alive()):
            self.lease.__exit__(None, None, None)
            self.lease = None

    def select(self, folder, create=False, name='Untitled Moon'):
        with self.lock:
            self.idle()
            path = Path(folder).expanduser().resolve()
            if path.name == 'project.json':
                path = path.parent
            if self.project and self.lease and path == self.project.folder:
                return self.project.public()
            if create:
                if path.exists() and any(x.name != 'project.json.lock' for x in path.iterdir()):
                    raise ValueError('Choose a new or empty project folder.')
                path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise ValueError('This project folder does not exist.')
            lease = file_lock(str(path / 'project.json'), timeout=0)
            try:
                lease.__enter__()
            except TimeoutError:
                raise ValueError('This project is already open in another LunarMosaic process.') from None
            try:
                if create:
                    p = Project.create(path, name)
                else:
                    p = Project.open(path)
            except BaseException:
                lease.__exit__(None, None, None)
                raise
            if self.lease:
                self.lease.__exit__(None, None, None)
            self.project, self.lease, self.scene = p, lease, None
            for key in ('L', 'RGB'):
                if p.data['sets'][key].get('mosaic'):
                    self.build_facts(p, key)
            self.cancel.clear()
            self.job = dict(state='idle')
            if self.history:
                try:
                    self.history.path.parent.mkdir(parents=True, exist_ok=True)
                    with file_lock(str(self.history.path), timeout=1):
                        self.history.remember(p.folder)
                    self.history_error = None
                except (OSError, TimeoutError) as e:
                    self.history_error = f'Project opened; recent history could not be saved: {e}'
            return p.public()

    def idle(self):
        if self.job['state'] == 'running':
            raise ValueError('Wait for the current operation or cancel it first.')

    @staticmethod
    def build_facts(project, key):
        s = project.data['sets'][key]
        layout = project.path(str(Path(s['mosaic']).with_suffix(''))+'_layout.json')
        facts = dict(placed=1, contributing=1, not_placed=[]) if len(s['inputs']) == 1 else None
        if layout.is_file():
            try:
                report = json.loads(layout.read_text('utf-8'))
                facts = dict(placed=len(report['panels']),
                             contributing=sum(v > 0 for v in report.get('disk_share', {}).values()),
                             not_placed=report.get('not_placed', []), rms_px=report['rms_px'])
            except (ValueError, KeyError, TypeError, OSError):
                # An optional report must not prevent reopening a usable mosaic.
                facts = None
        if facts is not None and s.get('build_facts') != facts:
            s['build_facts'] = facts
            project.save()

    def require(self, data):
        if not self.project or data.get('id') != self.project.data['id']:
            raise ValueError('This project is no longer open. Refresh the page.')
        return self.project

    def status(self):
        with self.lock:
            return dict(project=self.project.public() if self.project else None, job=copy.deepcopy(self.job))

    def update_job(self, **values):
        with self.lock:
            self.job.update(values)

    def start(self, action, work):
        with self.lock:
            self.idle()
            self.cancel.clear()
            self.job = dict(state='running', action=action, progress=0, stage=0, title=action,
                            log=[], started=time.time(), error=None)
            def run():
                try:
                    result = work()
                    self.update_job(state='cancelled' if self.cancel.is_set() else 'done', result=result,
                                    progress=1 if not self.cancel.is_set() else self.job.get('progress', 0))
                except InterruptedError as e:
                    self.update_job(state='cancelled', error=str(e))
                except Exception as e:
                    self.update_job(state='failed', error=str(e))
            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()

    def cancel_job(self):
        self.cancel.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                if os.name == 'nt':
                    self.process.terminate()
                else:
                    self.process.send_signal(signal.SIGINT)

    def inputs(self, key, paths, optics=None, allow_partial=False):
        self.idle()
        if key not in ('L', 'RGB'):
            raise ValueError('Unknown panel set.')
        if not isinstance(paths, list) or not paths or any(not isinstance(x, str) or not x.strip() for x in paths):
            raise ValueError('Choose one or more source folders or files.')
        if optics is not None and not isinstance(optics, dict):
            raise ValueError('Invalid optics values.')
        s = self.project.data['sets'][key]
        if s.get('mosaic'):
            raise ValueError('This set is already built. Create a new project to replace its panels.')
        paths = [str(self.project.path(x)) if not Path(x).is_absolute() and Path(x).parts[0] == 'sources' else x for x in paths]
        files = mc.find_images(paths, recursive=True)
        if not files:
            raise ValueError('No TIFF, PNG or JPEG images found. FITS is not supported by this builder yet.')
        from mosaic_builder import header_size
        valid, skipped, sizes, depths = [], [], [], []
        for file in files:
            try:
                w, h, channels, dtype = header_size(file)
                if channels < 3 and key == 'RGB':
                    raise ValueError('Mono panel; use the luminance set.')
                valid.append(file); sizes.append((w, h)); depths.append(str(dtype))
            except (ValueError, OSError, cv2.error) as e:
                if not allow_partial:
                    raise ValueError(f'{Path(file).name}: {e}') from e
                skipped.append(dict(name=Path(file).name, reason=str(e)))
        if not valid:
            raise ValueError('No readable panels in this set. ' + '; '.join(v['name']+': '+v['reason'] for v in skipped[:4]))
        files = valid
        if optics is not None:
            clean = {}
            for name in ('pixel_um', 'focal_mm', 'barlow'):
                v = optics.get(name)
                if v not in (None, ''):
                    v = float(v)
                    if not np.isfinite(v) or not 0 < v < 100000:
                        raise ValueError('Optics values must be positive numbers.')
                    clean[name] = v
            s['optics'] = clean
        resolved = [Path(x).resolve() for x in files]
        s.update(inputs=[str(x.relative_to(self.project.folder)) if x.is_relative_to(self.project.folder) else str(x) for x in resolved],
                 state='ready', error=None, skipped=skipped,
                 headers=dict(min_width=min(v[0] for v in sizes), max_width=max(v[0] for v in sizes),
                              min_height=min(v[1] for v in sizes), max_height=max(v[1] for v in sizes),
                              depths=sorted(set(depths))))
        self.project.save()

    def build(self, keys):
        p = self.project
        result = {}
        for key in keys:
            if self.cancel.is_set():
                break
            s = p.data['sets'][key]
            if s.get('mosaic'):
                continue
            if not s['inputs']:
                continue
            with self.lock:
                s.update(state='building', error=None, started=time.time(), last_stage=1)
                p.save()
            self.update_job(set=key, progress=0, stage=0, title='Checking panels')
            output = p.folder / 'derived' / f'{key}.tif'
            try:
                if len(s['inputs']) == 1:
                    # A one-panel mosaic still gets a retained native 16-bit TIFF.
                    source = Image(p.input_paths(key)[0])
                    with self.lock:
                        s['last_stage'] = 2
                        p.save()
                    temp = output.with_name(f'.{key}.partial.tif')
                    target = tifffile.memmap(temp, shape=(source.h, source.w, 3), dtype=np.uint16, photometric='rgb', metadata=None)
                    try:
                        for y in range(0, source.h, 128):
                            if self.cancel.is_set():
                                raise InterruptedError('Build cancelled.')
                            pixels = source.pixels(source.raw[y:y+128])
                            target[y:y+128] = np.round(pixels*65535).astype(np.uint16)
                            self.update_job(progress=min(1, (y+128)/source.h), title='Keeping native panel', stage=2)
                        target.flush()
                    finally:
                        del target
                    os.replace(temp, output)
                else:
                    command = [sys.executable, '-u', str(ROOT/'mosaic'/'mosaic_builder.py'), *p.input_paths(key),
                               '-o', str(output), '--no-preview', '--no-display-tif', '--no-north-up']
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                            cwd=ROOT, encoding='utf-8', errors='replace')
                    with self.lock:
                        self.process = proc
                        if self.cancel.is_set():
                            self.cancel_job()
                    for line in proc.stdout:
                        line = line.strip()
                        with self.lock:
                            self.job['log'] = (self.job['log']+[line])[-80:]
                        m = re.match(r'\[(\d+)/8\]\s+(.+)', line)
                        if m:
                            stage = int(m[1])+1
                            self.update_job(stage=stage, title=m[2], progress=(stage-1)/9)
                            with self.lock:
                                s['last_stage'] = stage
                                p.save()
                        tick = re.match(r'(\d+)/(\d+)\s+\[', line)
                        if tick:
                            stage = self.job.get('stage', 1)
                            self.update_job(progress=((stage-1)+int(tick[1])/max(1, int(tick[2])))/9)
                    code = proc.wait()
                    with self.lock:
                        self.process = None
                    if self.cancel.is_set():
                        raise InterruptedError('Build cancelled. Restart this set to build it again.')
                    if code != 0 or not output.exists():
                        raise ValueError('Mosaic build failed. See the build log below; the other set can still be built.')
                if self.cancel.is_set():
                    raise InterruptedError('Build cancelled.')
                with self.lock:
                    image = Image(output)
                    s.update(state='built', mosaic=f'derived/{key}.tif', width=image.w, height=image.h,
                             duration=time.time()-s['started'])
                    self.build_facts(p, key)
                    p.data['match'] = None
                    p.data['edits']['coverage'] = 'full'
                    p.data['edits']['l_visible'] = True
                    self.scene = None
                    p.save()
                result[key] = 'built'
            except Exception as e:
                output.with_name(f'.{key}.partial.tif').unlink(missing_ok=True)
                with self.lock:
                    s.update(state='interrupted' if self.cancel.is_set() else 'failed', error=str(e),
                             duration=time.time()-s['started'])
                    p.save()
                result[key] = s['state']
                if self.cancel.is_set():
                    break
        return result

    def match(self):
        p = self.project
        paths = []
        for key in ('L', 'RGB'):
            s = p.data['sets'][key]
            if not s.get('mosaic'):
                raise ValueError('Build both mosaics before matching.')
            paths.append(p.path(s['mosaic']))
        optics = [p.data['sets'][k]['optics'] for k in ('L', 'RGB')]
        prior = None
        if all(o.get('pixel_um') and o.get('focal_mm') for o in optics):
            sampling = [o['pixel_um']/(o['focal_mm']*o.get('barlow', 1)) for o in optics]
            prior = sampling[1]/sampling[0]
        self.update_job(title='Finding corresponding lunar detail')
        result = register(*paths, scale_prior=prior)
        result['diagnostics'] = self.measure_scene(Scene(p, match=result))
        if self.cancel.is_set():
            raise InterruptedError('Match cancelled; the previous transform was kept.')
        with self.lock:
            p.data['match'] = result
            self.scene = None
            p.save()
        return result

    def measure_scene(self, scene):
        self.update_job(title='Checking independent overlapping areas', progress=0)
        return measure(scene, progress=lambda f: self.update_job(progress=f), cancelled=self.cancel.is_set)

    def check_alignment(self):
        with self.lock:
            p = self.project
            result = copy.deepcopy(p.data['match'])
            if not result:
                raise ValueError('Match the colour layer first.')
            scene = Scene(p, match=result)
        result['diagnostics'] = self.measure_scene(scene)
        if self.cancel.is_set():
            raise InterruptedError('Alignment check cancelled; previous measurements were kept.')
        with self.lock:
            p.data['match'] = result
            self.scene = None
            p.save()
        return result['diagnostics']

    def north_up(self):
        p = self.project
        if not p.data['sets']['L'].get('mosaic'):
            raise ValueError('Build luminance before finding lunar north.')
        baseline = (p.data['edits']['rotation'], p.data['edits']['mirror'])
        with tempfile.TemporaryDirectory(prefix='.north-job-', dir=p.folder/'derived') as folder:
            result_path = Path(folder)/'result.json'
            proc = subprocess.Popen([sys.executable, '-u', str(ROOT/'mosaic'/'orient.py'), str(p.folder),
                '--result', str(result_path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', cwd=ROOT)
            with self.lock:
                self.process = proc
                if self.cancel.is_set():
                    self.cancel_job()
            try:
                for line in proc.stdout:
                    line = line.strip()
                    with self.lock:
                        self.job['log'] = (self.job.get('log', [])+[line])[-80:]
                    self.update_job(title=line[:120])
                code = proc.wait()
            finally:
                with self.lock:
                    self.process = None
            if self.cancel.is_set():
                raise InterruptedError('North-up cancelled. The previous view was kept.')
            if code != 0 or not result_path.exists():
                raise ValueError(next((s for s in reversed(self.job.get('log', [])) if s.startswith('North-up unavailable:')),
                                      'North-up failed. See the processing log; the previous view was kept.'))
            result = json.loads(result_path.read_text('utf-8'))
        with self.lock:
            if baseline != (p.data['edits']['rotation'], p.data['edits']['mirror']):
                raise ValueError('Rotation changed during the search. Your newer orientation was kept; run north-up again to apply it.')
            p.data['edits'].update(rotation=result['rotation'], mirror=result['mirror'])
            Project.validate_edits(p.data['edits'])
            p.data['orientation'] = result
            p.save()
        return result

    def get_scene(self):
        if self.scene is None:
            if not self.project or not self.project.data['sets']['L'].get('mosaic'):
                raise ValueError('Build the luminance mosaic first.')
            self.scene = Scene(self.project)
        return self.scene

    def export_options(self, edits, options):
        edits = copy.deepcopy(edits)
        if options.get('coverage') is not None:
            edits['coverage'] = options['coverage']
        Project.validate_edits(edits)
        layer = options.get('layer', 'composite')
        factor = float(options.get('factor', 1))
        if layer not in ('composite', 'luminance', 'colour'):
            raise ValueError('Unknown export layer.')
        if factor not in (1, .5, .25):
            raise ValueError('Export scale must be 1, 1/2 or 1/4.')
        if layer == 'colour' and self.get_scene().match is None:
            raise ValueError('Match the colour mosaic before exporting this layer.')
        if layer == 'composite' and not edits['l_visible'] and (not edits['rgb_visible'] or self.get_scene().match is None):
            raise ValueError('Enable a layer in the workspace before exporting the combined image.')
        return edits, layer, factor

    def get(self, h, path):
        parsed = urlsplit(h.path)
        query = parse_qs(parsed.query)
        try:
            if path == '/':
                h.file(str(UI/'index.html'), 'text/html')
            elif path in ('/mosaic.css', '/mosaic.js'):
                h.file(str(UI/path[1:]), 'text/css' if path.endswith('css') else 'text/javascript')
            elif path == '/api/state':
                h.json(self.status())
            elif path == '/api/recent':
                h.json(dict(projects=self.history.summaries() if self.history else [],
                            warning=self.history_error, enabled=self.history is not None))
            elif path == '/api/export-preview':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    options = {key:query[key][0] for key in ('coverage', 'layer', 'factor') if key in query}
                    edits, layer, factor = self.export_options(p.data['edits'], options)
                    scene = self.get_scene()
                rgb, info = scene.preview(edits, layer)
                _, (w, height), _ = scene.geometry(edits, factor)
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.json(dict(width=w, height=height, raw_bytes=w*height*6,
                            missing_colour=info['missing_colour'],
                            composition=layer if layer != 'composite' else 'LRGB' if edits['l_visible'] and edits['rgb_visible'] and scene.match is not None else 'luminance' if edits['l_visible'] else 'colour',
                            preview='data:image/png;base64,'+base64.b64encode(png).decode('ascii')))
            elif path == '/api/preview':
                with self.lock:
                    self.require(dict(id=query.get('id', [''])[0]))
                    scene = self.get_scene()
                    edits = copy.deepcopy(self.project.data['edits'])
                view = query.get('view', ['composite'])[0]
                if view not in ('composite', 'luminance', 'colour', 'overlay', 'coverage', 'errors'):
                    raise ValueError('Unknown view.')
                rgb, info = scene.preview(edits, view)
                ok, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.reply(png.tobytes(), 'image/png')
            elif path == '/api/geometry':
                with self.lock:
                    self.require(dict(id=query.get('id', [''])[0]))
                    _, info = self.get_scene().preview(copy.deepcopy(self.project.data['edits']))
                    h.json(info)
            elif path == '/api/viewport':
                with self.lock:
                    self.require(dict(id=query.get('id', [''])[0]))
                    scene = self.get_scene()
                    edits = copy.deepcopy(self.project.data['edits'])
                x, y, scale = [float(query.get(k, ['0'])[0]) for k in ('x', 'y', 'scale')]
                w, height = [int(query.get(k, ['0'])[0]) for k in ('w', 'h')]
                if not all(np.isfinite(v) for v in (x, y, scale)) or not .001 <= scale <= 8 or not 1 <= w <= 4096 or not 1 <= height <= 4096 or w*height > 6_000_000:
                    raise ValueError('Invalid viewport.')
                view = query.get('view', ['composite'])[0]
                if view not in ('composite', 'luminance', 'colour', 'overlay', 'coverage', 'errors'):
                    raise ValueError('Unknown view.')
                matrix, _, _ = scene.geometry(edits)
                transform = np.array([[scale, 0, -x], [0, scale, -y], [0, 0, 1]]) @ matrix
                rgb, _ = scene.render(transform, (w, height), edits, view, precise=scale >= .75)
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.reply(png.tobytes(), 'image/png')
            elif path in ('/api/native-preview', '/api/layer-thumbnail'):
                with self.lock:
                    self.require(dict(id=query.get('id', [''])[0]))
                    key = query.get('set', [''])[0]
                    if key not in ('L', 'RGB'):
                        raise ValueError('Unknown panel set.')
                    source = self.project.data['sets'][key].get('mosaic')
                    if not source:
                        raise ValueError('Build this layer first.')
                    image = Image(self.project.path(source))
                rgb, _ = image.preview(96 if path == '/api/layer-thumbnail' else 1200)
                if key == 'L' and path == '/api/layer-thumbnail':
                    rgb = cv2.cvtColor(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.reply(png.tobytes(), 'image/png')
            else:
                return False
        except (ValueError, OSError, TypeError, KeyError, cv2.error) as e:
            h.json(dict(error=str(e)), 400)
        return True

    def post(self, h, path):
        if not path.startswith('/api/'):
            return False
        try:
            if path == '/api/upload':
                q = parse_qs(urlsplit(h.path).query)
                with self.lock:
                    p = self.require(dict(id=q.get('id', [''])[0]))
                    self.idle()
                    key = q.get('set', [''])[0]
                    if key not in ('L', 'RGB') or p.data['sets'][key].get('mosaic'):
                        raise ValueError('Choose an unbuilt panel set.')
                    name = Path(q.get('name', ['panel'])[0]).name
                    if Path(name).suffix.lower().lstrip('.') not in mc.DEFAULT_EXTS:
                        raise ValueError('Upload TIFF, PNG or JPEG panels.')
                    n = h.length(8 << 30)
                    folder = p.folder/'sources'/key
                    folder.mkdir(parents=True, exist_ok=True)
                    if n < 1 or shutil.disk_usage(folder).free < n+(128 << 20):
                        raise ValueError('Empty upload or insufficient free disk space.')
                    target = folder/(uuid.uuid4().hex[:12]+'-'+name)
                    try:
                        with open(target, 'xb') as f:
                            left = n
                            while left:
                                data = h.rfile.read(min(left, 1 << 20))
                                if not data:
                                    raise ValueError('Upload interrupted.')
                                f.write(data)
                                left -= len(data)
                        self.inputs(key, p.data['sets'][key]['inputs']+[str(target)])
                    except BaseException:
                        target.unlink(missing_ok=True)
                        raise
                    h.json(self.status())
                return True
            o = h.body()
            if not isinstance(o, dict):
                raise ValueError('Expected a JSON object.')
            with self.lock:
                if path == '/api/recent-clear':
                    if self.history:
                        self.history.path.parent.mkdir(parents=True, exist_ok=True)
                        with file_lock(str(self.history.path), timeout=1):
                            self.history.write([])
                    self.history_error = None
                elif path == '/api/close':
                    self.require(o)
                    self.idle()
                    self.lease.__exit__(None, None, None)
                    self.project = self.lease = self.scene = None
                    self.job = dict(state='idle')
                elif path in ('/api/new', '/api/open'):
                    self.select(o['folder'], path == '/api/new', o.get('name', 'Untitled Moon'))
                else:
                    p = self.require(o)
                    if path == '/api/inputs':
                        self.inputs(o['set'], o['paths'], o.get('optics'), allow_partial=True)
                    elif path == '/api/build':
                        keys = o.get('sets', ['L', 'RGB'])
                        if not isinstance(keys, list) or not keys or any(k not in ('L', 'RGB') for k in keys):
                            raise ValueError('Choose luminance or colour sets.')
                        self.start('Build mosaics', lambda: self.build(keys))
                    elif path == '/api/match':
                        self.start('Match colour to luminance', self.match)
                    elif path == '/api/check-match':
                        self.start('Check alignment', self.check_alignment)
                    elif path == '/api/north-up':
                        self.start('Find lunar north', self.north_up)
                    elif path == '/api/manual':
                        self.idle()
                        for key in ('L', 'RGB'):
                            if not p.data['sets'][key].get('mosaic'):
                                raise ValueError('Build both mosaics first.')
                        p.data['match'] = manual_match(o['rgb_points'], o['l_points'])
                        self.scene = None
                        p.save()
                        self.start('Check manual match', self.check_alignment)
                    elif path == '/api/edits':
                        if o.get('revision') != p.data['revision']:
                            h.json(dict(error='The project changed. Refresh and try again.', conflict=True), 409)
                            return True
                        Project.validate_edits(o['edits'])
                        self.get_scene().geometry(o['edits'])
                        p.data['edits'] = o['edits']
                        p.save()
                    elif path == '/api/cancel':
                        self.cancel_job()
                    elif path == '/api/export':
                        scene, edits = self.get_scene(), copy.deepcopy(p.data['edits'])
                        edits, layer, factor = self.export_options(edits, o)
                        self.start('Export image', lambda: scene.export(o['output'], edits, factor, layer,
                            progress=lambda f: self.update_job(progress=f, title=f'Writing image · {f:.0%}'),
                            cancelled=self.cancel.is_set))
                    else:
                        h.json(dict(error='Unknown operation.'), 404)
                        return True
                h.json(self.status())
        except (ValueError, OSError, TypeError, KeyError, cv2.error) as e:
            h.json(dict(error=str(e)), 400)
        return True


def serve(app, port=8768, browser=True):
    srv = start_server(port, app=app, token=secrets.token_urlsafe(32))
    # Keep the redirect private and separate from LunarAtlas's own launcher state.
    fd, handoff = tempfile.mkstemp(prefix='lunarmosaic-', suffix='.html')
    with os.fdopen(fd, 'w') as f:
        f.write('<!doctype html><meta http-equiv="refresh" content="0;url='+html.escape(srv.url(secret=True), quote=True)+'">')
    print('LunarMosaic: '+srv.url(secret=not browser), flush=True)
    if browser:
        threading.Timer(.3, lambda: webbrowser.open(Path(handoff).as_uri())).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        app.close()
        Path(handoff).unlink(missing_ok=True)
