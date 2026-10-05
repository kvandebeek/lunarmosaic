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
import block_catalog
import chain
import filters
import measurements as ms
from project import Project, STREAMS, CANVAS_CAP_MPX, clean_optics, luminance_scales
import project as projects
import lrgb
from lrgb import Image, Scene, manual_match
from colour_place import K_matrix
from alignment import measure
from recent import RecentProjects
import mosaic_common as mc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'moon_atlas'))
from atlas_view import start_server
from atlas_paths import file_lock

UI = ROOT / 'mosaic' / 'ui'
UI_FILES = {'mosaic.css': 'text/css', 'mosaic.js': 'text/javascript', 'chains.js': 'text/javascript',
            'previews.js': 'text/javascript', 'save.js': 'text/javascript'}


def json_safe(o):
    """Readouts for the interface: numpy arrays and scalars as lists and numbers."""
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    if isinstance(o, np.ndarray):
        return json_safe(o.tolist())
    if isinstance(o, np.generic):
        return o.item()
    return o


class StatisticsWorker:
    """The statistics worker of plan §4.5 — a thread, not a job: Application.start() runs one job at a time and
    refuses others, so a slider moved during an export would fail. The newest request wins; an older computation
    stops at its next block boundary. Renders wait in Scene.stats() for the statistics they need; an export holds
    the statistics it started with. The thread ends when there is nothing left to do."""

    def __init__(self):
        self.cond = threading.Condition()
        self.generation, self.request, self.thread, self.error = 0, None, None, None

    def submit(self, scene, doc):
        with self.cond:
            self.generation += 1
            self.request = (self.generation, scene, copy.deepcopy(doc))
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()

    def cancel(self):
        """Abandon pending and running work (closing the project)."""
        with self.cond:
            self.generation += 1
            self.request = None

    def stale(self, generation):
        if generation != self.generation:
            raise chain.Stale()

    def run(self):
        while True:
            with self.cond:
                if self.request is None:
                    self.thread = None
                    return
                generation, scene, doc = self.request
                self.request = None
            try:
                scene.stats(doc, check=lambda: self.stale(generation))
                self.error = None
            except chain.Stale:
                pass
            except Exception as e:      # a render needing these statistics computes them itself and reports this
                self.error = str(e)


class Application:
    def __init__(self, history_path=None):
        self.lock = threading.RLock()
        self.worker = StatisticsWorker()
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
        self.worker.cancel()
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
            self.worker.cancel()
            self.project, self.lease, self.scene = p, lease, None
            for name in STREAMS:
                if p.stream(name).get('mosaic'):
                    self.build_facts(p, name)
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
    def build_facts(project, name):
        s = project.stream(name)
        layout = project.path(str(Path(s['mosaic']).with_suffix(''))+'_layout.json')
        panels = sum(len(x['inputs']) for x in project.sets_of(name))
        facts = dict(placed=1, contributing=1, not_placed=[]) if panels == 1 else None
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
        try:
            s = self.project.set(key)
        except KeyError:
            raise ValueError('Unknown panel set.') from None
        if not isinstance(paths, list) or not paths or any(not isinstance(x, str) or not x.strip() for x in paths):
            raise ValueError('Choose one or more source folders or files.')
        if optics is not None and not isinstance(optics, dict):
            raise ValueError('Invalid optics values.')
        stream = self.project.stream_of(key)
        if stream.get('mosaic'):
            raise ValueError('This set is already built. Create a new project to replace its panels.')
        if s['stream'] == 'luminance' and self.project.set('RGB')['stream'] == 'both':
            raise ValueError('The colour set feeds both streams, so there are no luminance panels. Let it feed colour only first.')
        paths = [str(self.project.path(x)) if not Path(x).is_absolute() and Path(x).parts[0] == 'sources' else x for x in paths]
        files = mc.find_images(paths, recursive=True)
        if not files:
            raise ValueError('No TIFF, PNG or JPEG images found. FITS is not supported by this builder yet.')
        from mosaic_builder import header_size
        valid, skipped, sizes, depths = [], [], [], []
        for file in files:
            try:
                w, h, channels, dtype = header_size(file)
                if channels < 3 and s['stream'] in ('colour', 'both'):
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
            # the fields sent replace theirs (a blank one removes it); fields not sent, such as the aperture, stay
            s['optics'] = dict({k: v for k, v in s.get('optics', {}).items() if k not in optics}, **clean_optics(optics))
        resolved = [Path(x).resolve() for x in files]
        s.update(inputs=[str(x.relative_to(self.project.folder)) if x.is_relative_to(self.project.folder) else str(x) for x in resolved],
                 skipped=skipped,
                 headers=dict(min_width=min(v[0] for v in sizes), max_width=max(v[0] for v in sizes),
                              min_height=min(v[1] for v in sizes), max_height=max(v[1] for v in sizes),
                              depths=sorted(set(depths))))
        s.pop('scale_estimate', None)                 # other panels: an estimate made on the old ones no longer holds
        stream.update(state='ready', error=None)
        if s['stream'] == 'both':
            self.project.stream('luminance').update(state='ready', error=None)
        self.project.save()

    def build(self, keys, canvas=None):
        """Build the streams the given sets feed: all luminance sets jointly into one mosaic (phase 5), then the
        colour set. Results are keyed L and RGB, as the interface reads them."""
        p = self.project
        result = {}
        wanted = set()
        for k in keys:                    # a set feeding both streams builds both: its mosaic is also the colour
            stream = p.set(k)['stream']
            wanted |= set(STREAMS) if stream == 'both' else {stream}
        for name in [n for n in STREAMS if n in wanted]:
            if self.cancel.is_set():
                break
            s = p.stream(name)
            key = 'L' if name == 'luminance' else 'RGB'
            if s.get('mosaic'):
                continue
            if name == 'colour':
                # phase 6: no colour mosaic — the panels are placed one by one onto the luminance mosaic
                if p.input_paths('RGB'):
                    if not p.stream('luminance').get('mosaic'):
                        with self.lock:
                            s.update(state='failed', error='Build the luminance mosaic first: colour is placed onto it.')
                            p.save()
                    else:
                        self.update_job(set='RGB', progress=0, stage=0, title='Placing colour panels')
                        try:
                            self.place_colour()
                        except Exception:
                            pass                           # recorded in the stream's state and error
                    result[key] = p.stream('colour')['state']
                continue
            paths = [x for st in p.feeding(name) for x in p.input_paths(st['id'])]
            if not paths:
                continue
            with self.lock:
                if name == 'luminance' and canvas:
                    s.update({k: canvas[k] for k in ('canvas_scale', 'max_canvas_mpx') if k in canvas})
                s.update(state='building', error=None, started=time.time(), last_stage=1)
                p.save()
            self.update_job(set=key, progress=0, stage=0, title='Checking panels')
            output = p.folder / 'derived' / f'{key}.tif'
            try:
                if len(paths) == 1:
                    # A one-panel mosaic still gets a retained native 16-bit TIFF.
                    source = Image(paths[0])
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
                    command = [sys.executable, '-u', str(ROOT/'mosaic'/'mosaic_builder.py'), *paths,
                               '-o', str(output), '--no-preview', '--no-display-tif', '--no-north-up']
                    if name == 'luminance':
                        command += self.luminance_options(p, s)
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                            cwd=ROOT, encoding='utf-8', errors='replace')
                    with self.lock:
                        self.process = proc
                        if self.cancel.is_set():
                            self.cancel_job()
                    failure = None
                    for line in proc.stdout:
                        line = line.strip()
                        with self.lock:
                            self.job['log'] = (self.job.get('log', [])+[line])[-80:]
                        if line.startswith('FAIL:'):
                            failure = line[5:].strip()
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
                    proc.stdout.close()
                    code = proc.wait()
                    with self.lock:
                        self.process = None
                    if self.cancel.is_set():
                        raise InterruptedError('Build cancelled. Restart this set to build it again.')
                    if code != 0 or not output.exists():
                        raise ValueError((failure[0].upper() + failure[1:] + '.' if failure else
                                          'Mosaic build failed. See the build log below; the other set can still be built.'))
                if self.cancel.is_set():
                    raise InterruptedError('Build cancelled.')
                with self.lock:
                    image = Image(output)
                    s.update(state='built', mosaic=f'derived/{key}.tif', width=image.w, height=image.h,
                             duration=time.time()-s['started'])
                    if name == 'luminance':
                        report = output.with_name(f'{key}_sets.json')
                        s['sets_report'] = f'derived/{report.name}' if report.is_file() else None
                        s['canvas_arcsec_px'] = self.canvas_arcsec_px(p, s)
                    self.build_facts(p, name)
                    p.match = None
                    p.data['geometry']['coverage'] = 'full'
                    p.data['view']['l_visible'] = True
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

    @staticmethod
    def luminance_options(p, stream):
        """Builder options for the luminance stream: the panel scales file — one set or several, so the mosaic
        always comes with its set report — and, for a mixed stream, the size cap the user confirmed (400 Mpx unless
        they confirmed more; a single set keeps today's unlimited build)."""
        scales = luminance_scales(p.data['sets'])               # refuses unknown or unconfirmed scales
        canvas_set = min(scales, key=lambda k: (scales[k]['rel'], k))
        doc = dict(canvas_set=canvas_set, panels=[], sets={})
        for st in p.feeding('luminance'):
            if st['id'] not in scales:
                continue
            doc['panels'] += [dict(path=x, set=st['id'], scale_rel=scales[st['id']]['rel']) for x in p.input_paths(st['id'])]
            meta = {k: v for k, v in st.get('optics', {}).items() if k in ('aperture_mm', 'wavelength_nm')}
            if scales[st['id']]['arcsec_px']:
                meta['arcsec_px'] = scales[st['id']]['arcsec_px']
            doc['sets'][st['id']] = meta
        path = p.folder/'derived'/'L_panel_scales.json'
        path.write_text(json.dumps(doc, indent=1), 'utf-8')
        options = ['--panel-scales', str(path)]
        cap = stream.get('max_canvas_mpx') or (CANVAS_CAP_MPX if len(scales) > 1 else None)
        if cap:
            options += ['--max-canvas-mpx', str(cap)]
        if stream.get('canvas_scale', 1) != 1:
            options += ['--canvas-scale', str(stream['canvas_scale'])]
        return options

    @staticmethod
    def canvas_arcsec_px(p, stream):
        """The luminance canvas's pixel scale: the finest set's, divided by the canvas reduction; None when no set's
        optics give an absolute scale."""
        try:
            scales = luminance_scales(p.data['sets'])
        except ValueError:
            return None
        finest = min((v['arcsec_px'] for v in scales.values() if v['arcsec_px']), default=None)
        return finest / stream.get('canvas_scale', 1) if finest else None

    @staticmethod
    def canvas_options(canvas):
        """The canvas settings confirmed with a build: canvas_scale (0–1] and max_canvas_mpx (> 0)."""
        if canvas is None:
            return None
        if not isinstance(canvas, dict) or set(canvas) - {'canvas_scale', 'max_canvas_mpx'}:
            raise ValueError('The canvas settings are canvas_scale and max_canvas_mpx.')
        out = {}
        for k, ok in (('canvas_scale', lambda v: 0 < v <= 1), ('max_canvas_mpx', lambda v: v > 0)):
            v = canvas.get(k)
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not ok(v):
                    raise ValueError('The canvas scale is above 0 and at most 1; the size limit is positive.')
                out[k] = float(v)
        return out

    def sets_op(self, o):
        """/api/sets: create, update (label, optics) or delete a luminance set; estimate the scales of sets without
        optics (a job, D10) or confirm an estimate. Colour keeps its one set until phase 6."""
        p, op, data = self.project, o.get('op'), o.get('set') or {}
        if not isinstance(data, dict):
            raise ValueError('Describe the set as an object.')
        if op == 'estimate':
            self.start('Estimate set scales', self.estimate_scales)
            return
        self.idle()
        if op == 'create':
            key = p.add_set(data.get('stream', 'luminance'), data.get('label'))
            if data.get('optics'):
                p.set(key)['optics'] = clean_optics(data['optics'])
        else:
            try:
                s = p.set(data.get('id'))
            except KeyError:
                raise ValueError('Unknown panel set.') from None
            if op == 'update':
                if 'label' in data:
                    s['label'] = str(data['label'])[:120]
                if 'stream' in data and data['stream'] != s['stream']:
                    # D9: the colour set may feed both streams — one colour mosaic, nothing else
                    if s['id'] != 'RGB' or data['stream'] not in ('colour', 'both'):
                        raise ValueError('Only the colour set can feed both streams.')
                    if any(p.stream(n).get('mosaic') for n in STREAMS):
                        raise ValueError('Change what the colour set feeds before building.')
                    if data['stream'] == 'both' and any(x['inputs'] for x in p.sets_of('luminance')):
                        raise ValueError('A colour set feeds both streams only when there are no luminance panels.')
                    s['stream'] = data['stream']
                    p.refresh_state('luminance')
                if 'optics' in data:
                    if p.stream_of(s['id']).get('mosaic'):
                        raise ValueError('This set is already built. Create a new project to change its optics.')
                    s['optics'] = clean_optics(data['optics'])
                    s.pop('scale_estimate', None)
            elif op == 'delete':
                if s['id'] in ('L', 'RGB'):
                    raise ValueError('The first luminance set and the colour set cannot be deleted.')
                if p.stream_of(s['id']).get('mosaic'):
                    raise ValueError('The luminance mosaic was built with this set. Create a new project to change its sets.')
                p.data['sets'].remove(s)
                for other in p.data['sets']:               # an estimate made against this set no longer resolves
                    if (other.get('scale_estimate') or {}).get('relative_to') == s['id']:
                        other.pop('scale_estimate')
                p.refresh_state('luminance')
            elif op == 'confirm':
                if not s.get('scale_estimate'):
                    raise ValueError('Estimate this set\'s scale first.')
                s['scale_estimate']['confirmed'] = True
            else:
                raise ValueError('Unknown set operation.')
        p.save()

    def estimate_scales(self):
        """D10: each luminance set without optics gets its pixel scale relative to a set with optics (or to the first
        set), measured on matched panels; stored unconfirmed, for the user to check before a mixed build."""
        p = self.project
        with self.lock:
            sets = [s for s in p.feeding('luminance') if s['inputs']]
            paths = {s['id']: p.input_paths(s['id']) for s in sets}
        if len(sets) < 2:
            raise ValueError('Estimating scales needs two luminance sets with panels.')
        ref = next((s for s in sets if projects.arcsec_px(s.get('optics', {}))), sets[0])
        result = {}
        for s in sets:
            if s is ref or projects.arcsec_px(s.get('optics', {})):
                continue
            if self.cancel.is_set():
                raise InterruptedError('Scale estimate cancelled.')
            self.update_job(title=f'Matching {s.get("label") or s["id"]} against {ref.get("label") or ref["id"]}')
            ratio, used, spread = mc.estimate_scale(paths[s['id']], paths[ref['id']], cancelled=self.cancel.is_set)
            with self.lock:
                s['scale_estimate'] = dict(relative_to=ref['id'], ratio=ratio, pairs=used, spread=spread, confirmed=False)
                p.save()
            result[s['id']] = s['scale_estimate']
        return result

    def finest_detail(self, key, paths):
        """The measured detail of a set in its own px (D14), on up to three panels; cached per set and panels."""
        cache = self.__dict__.setdefault('_details', {})
        sample = tuple(paths[::max(1, len(paths)//3)][:3])
        if (key, sample) not in cache:
            grays = []
            for path in sample:
                x, _, _ = mc.read_image(path)
                grays.append(mc.luminance(x if x.shape[2] == 3 else np.repeat(x, 3, axis=2)))
            cache[(key, sample)] = ms.set_detail(grays, 1.0)[0]
        return cache[(key, sample)]

    def canvas_estimate(self, sets, paths, stream):
        """/api/canvas-estimate (plan phase 5): each luminance set's scale, the finest pixel scale and a lower bound
        for the canvas — the Moon at perigee, about 2010″ across, at that scale; panels overhang the limb, so the
        real canvas is larger — in px, Mpx and GB at 16-bit RGB, against the 400 Mpx confirmation threshold; and
        the finest set's measured detail, with a smaller canvas suggested when its pixels are far finer than that."""
        sets = [s for s in sets if paths.get(s['id'])]
        if not sets:
            raise ValueError('Add luminance panels first.')
        try:
            scales, problem = luminance_scales(sets), None
        except ValueError as e:
            scales, problem = {}, str(e)
        c = stream.get('canvas_scale', 1)
        out = dict(threshold_mpx=CANVAS_CAP_MPX, problem=problem, canvas_scale=c,
                   max_canvas_mpx=stream.get('max_canvas_mpx'),
                   sets=[dict(id=s['id'], label=s.get('label'), panels=len(paths[s['id']]),
                              estimate=copy.deepcopy(s.get('scale_estimate')), **(
                                  {k: scales[s['id']][k] for k in ('rel', 'arcsec_px', 'source')} if s['id'] in scales
                                  else dict(rel=None, arcsec_px=None, source=None))) for s in sets])
        if not scales:
            return out
        finest = min(scales, key=lambda k: (scales[k]['rel'], k))
        detail = self.finest_detail(finest, paths[finest])
        out['finest'] = dict(id=finest, arcsec_px=scales[finest]['arcsec_px'], detail_px=detail)
        if scales[finest]['arcsec_px']:
            side = 2010 / (scales[finest]['arcsec_px'] / c)
            mpx = side * side / 1e6
            out['lower_bound'] = dict(px=int(round(side)), mpx=round(mpx, 1), gb=round(side * side * 6 / 1e9, 2))
            out['needs_confirmation'] = mpx > (stream.get('max_canvas_mpx') or CANVAS_CAP_MPX)
        if detail and detail > 3:
            out['oversampled'] = dict(px_per_detail=round(detail, 2), suggested_canvas_scale=round(2.5 / detail, 3))
        return out

    def match(self):
        """/api/match since phase 6: place the colour panels again (there is no global match any more)."""
        return self.place_colour()

    def run_process(self, command, on_line=lambda line: None):
        """A child process, its output in the job log; cancelling stops it. Returns (exit code, its FAIL: line)."""
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT,
                                encoding='utf-8', errors='replace')
        with self.lock:
            self.process = proc
            if self.cancel.is_set():
                self.cancel_job()
        failure = None
        try:
            for line in proc.stdout:
                line = line.strip()
                with self.lock:
                    self.job['log'] = (self.job.get('log', [])+[line])[-80:]
                if line.startswith('FAIL:'):
                    failure = line[5:].strip()
                on_line(line)
        finally:
            proc.stdout.close()
            code = proc.wait()
            with self.lock:
                self.process = None
        return code, failure

    @staticmethod
    def placement_summary(doc):
        """The placement as the interface reads a match until phase 7: matrix K (colour canvas -> canvas), held-out
        tile residuals as the statistics and the sample points of the errors view."""
        placed = [v for v in doc['panels'].values() if v['state'] == 'placed']
        held = np.array([e for v in placed for _, _, e in v.get('samples', [])])
        return dict(matrix=K_matrix(doc['k']).tolist(), method='placement', scale=doc['k'],
                    rotation=float(np.median([v['rotation_deg'] for v in placed])) if placed else 0.0,
                    inliers=len(placed), candidates=len(doc['panels']),
                    median=float(np.median(held)) if len(held) else 0.0,
                    p95=float(np.percentile(held, 95)) if len(held) else 0.0,
                    worst=float(held.max()) if len(held) else 0.0,
                    points=[[x, y, e] for v in placed for x, y, e in v.get('samples', [])],
                    scale_prior=doc['scale_ratio'] if doc['scale_source'] == 'given' else None,
                    warning=f'Colour placed panel by panel: {len(placed)} of {len(doc["panels"])} panels. Residuals are '
                            'held-out tiles; unmeasured areas may differ, so inspect the overlay.')

    def place_colour(self, initial=None):
        """Phase 6: the colour set's panels placed one by one onto the luminance mosaic (colour_place.py), giving the
        colour canvas and its report, then checked independently (alignment.measure). A set feeding both streams
        needs no placement: its own mosaic is the colour. `initial` ({panel name: 3×3}) starts from given poses
        (manual control points of a single-image colour set)."""
        p = self.project
        with self.lock:
            lum, s = p.stream('luminance'), p.stream('colour')
            if not lum.get('mosaic'):
                raise ValueError('Build the luminance mosaic first: colour is placed onto it.')
            paths = p.input_paths('RGB')
            if not paths:
                raise ValueError('Add colour panels first.')
            both = p.set('RGB')['stream'] == 'both'
            s.update(state='building', error=None, started=time.time())
            p.save()
        derived = p.folder/'derived'
        partial, partial_report = derived/'.colour_canvas.partial.tif', derived/'.colour_place.partial.json'
        try:
            if both:
                doc, mosaic, report = None, lum['mosaic'], None
                placement = dict(matrix=np.eye(3).tolist(), method='both', scale=1.0, rotation=0.0, inliers=1, candidates=1,
                                 median=0.0, p95=0.0, worst=0.0, points=[], scale_prior=None,
                                 warning='One colour set feeds both streams: its own mosaic is the colour; nothing is placed.')
            else:
                command = [sys.executable, '-u', str(ROOT/'mosaic'/'colour_place.py'), *paths, '--canvas', str(p.path(lum['mosaic'])),
                           '-o', str(partial), '--report', str(partial_report)]
                lum_scale = lum.get('canvas_arcsec_px') or projects.arcsec_px(p.set('L')['optics'])
                rgb_scale = projects.arcsec_px(p.set('RGB')['optics'])
                if lum_scale and rgb_scale:
                    command += ['--scale-ratio', repr(rgb_scale/lum_scale)]
                if initial:
                    (derived/'.colour_initial.json').write_text(json.dumps(initial), 'utf-8')
                    command += ['--initial', str(derived/'.colour_initial.json')]

                def progress(line):
                    m = re.match(r'\[(\d+)/(\d+)\]\s+(\S+)', line)
                    if m:
                        self.update_job(progress=int(m[1])/max(1, int(m[2])), title=f'Placing colour · {m[3]}')
                self.update_job(title='Placing colour panels', progress=0)
                code, failure = self.run_process(command, progress)
                if self.cancel.is_set():
                    raise InterruptedError('Colour placement cancelled; the previous colour was kept.')
                if code != 0 or not partial.is_file():
                    raise ValueError((failure[0].upper()+failure[1:]+'.') if failure else 'Colour placement failed. See the log below.')
                os.replace(partial, derived/'colour_canvas.tif')
                os.replace(partial_report, derived/'colour_place.json')
                doc = json.loads((derived/'colour_place.json').read_text('utf-8'))
                mosaic, report, placement = 'derived/colour_canvas.tif', 'derived/colour_place.json', self.placement_summary(doc)
            size = doc['colour_canvas_size'] if doc else [lum.get('width'), lum.get('height')]
            with self.lock:
                s.update(state='built', mosaic=mosaic, report=report, k=doc['k'] if doc else 1.0, width=size[0], height=size[1],
                         revision=s.get('revision', 0)+1, summary=doc['summary'] if doc else None, placement=placement,
                         legacy_match=None, duration=time.time()-s['started'])
                p.data['geometry']['coverage'] = 'full'            # the colour coverage changed
                self.scene = None
                p.save()
        except Exception as e:
            for f in (partial, partial_report):
                f.unlink(missing_ok=True)
            with self.lock:
                s.update(state='interrupted' if self.cancel.is_set() else 'failed', error=str(e),
                         duration=time.time()-s['started'])
                p.save()
            raise
        try:                                                       # the independent check, on the placed result
            diagnostics = self.measure_scene(Scene(p))
            with self.lock:
                p.match = dict(p.match, diagnostics=diagnostics)
                self.scene = None
                p.save()
        except (ValueError, cv2.error) as e:
            self.update_job(title=f'Colour placed; the independent check could not run ({e})')
        return p.match

    def measure_scene(self, scene):
        self.update_job(title='Checking independent overlapping areas', progress=0)
        return measure(scene, progress=lambda f: self.update_job(progress=f), cancelled=self.cancel.is_set)

    def check_alignment(self):
        with self.lock:
            p = self.project
            result = copy.deepcopy(p.match)
            if not result:
                raise ValueError('Match the colour layer first.')
            scene = Scene(p, match=result)
        result['diagnostics'] = self.measure_scene(scene)
        if self.cancel.is_set():
            raise InterruptedError('Alignment check cancelled; previous measurements were kept.')
        with self.lock:
            p.match = result
            self.scene = None
            p.save()
        return result['diagnostics']

    def north_up(self):
        p = self.project
        if not p.stream('luminance').get('mosaic'):
            raise ValueError('Build luminance before finding lunar north.')
        baseline = (p.data['geometry']['rotation'], p.data['geometry']['mirror'])
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
                proc.stdout.close()
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
            if baseline != (p.data['geometry']['rotation'], p.data['geometry']['mirror']):
                raise ValueError('Rotation changed during the search. Your newer orientation was kept; run north-up again to apply it.')
            old = dict(p.data['geometry'])
            p.set_geometry(dict(rotation=result['rotation'], mirror=result['mirror']))
            self.move_previews(old, p.data['geometry'])
            p.data['orientation'] = result
            p.save()
        return result

    def move_previews(self, old, new):
        """When rotation or mirror change, each preview keeps its size and follows its centre (plan §4.2)."""
        p = self.project
        previews = p.data['chain']['previews']
        if not previews or (old['rotation'], old['mirror']) == (new['rotation'], new['mirror']):
            return
        scene = self.get_scene()
        A = scene.geometry(dict(old, coverage='full'))[0]
        B = scene.geometry(dict(new, coverage='full'))[0]
        _, (w, h), _ = scene.geometry(dict(new, coverage='full'))
        M = B @ np.linalg.inv(A)
        for v in previews:
            c = M @ [v['x'] + v['w'] / 2, v['y'] + v['h'] / 2, 1]
            v['x'] = int(np.clip(round(c[0] - v['w'] / 2), 0, max(0, w - v['w'])))
            v['y'] = int(np.clip(round(c[1] - v['h'] / 2), 0, max(0, h - v['h'])))

    def preview_op(self, o):
        """/api/previews: add, update (name, position, size) or delete a preview rectangle, in view px."""
        p = self.project
        previews = p.data['chain']['previews']
        op, v = o.get('op'), o.get('preview') or {}
        if not isinstance(v, dict):
            raise ValueError('Describe the preview as an object.')
        if op == 'delete':
            p.data['chain']['previews'] = [x for x in previews if x['id'] != v.get('id')]
            return
        if op == 'add':
            taken = {x['id'] for x in previews}
            v = dict(v, id=next(f'p{n:02d}' for n in range(1, 1000) if f'p{n:02d}' not in taken))
            v.setdefault('name', f'Preview {len(previews) + 1}')
        elif op == 'update':
            old = next((x for x in previews if x['id'] == v.get('id')), None)
            if old is None:
                raise ValueError('Unknown preview.')
            v = dict(old, **v)
        else:
            raise ValueError('Unknown preview operation.')
        name = str(v.get('name') or '').strip()[:60] or 'Preview'
        try:
            x, y, w, h = (int(v[k]) for k in ('x', 'y', 'w', 'h'))
        except (KeyError, TypeError, ValueError):
            raise ValueError('A preview is x, y, w and h in view pixels.') from None
        if x < 0 or y < 0 or not 16 <= w <= 4096 or not 16 <= h <= 4096:
            raise ValueError('A preview is 16 to 4096 pixels on each side, inside the view.')
        clean = dict(id=v['id'], name=name, x=x, y=y, w=w, h=h)
        p.data['chain']['previews'] = [clean if x_['id'] == clean['id'] else x_ for x_ in previews] if op == 'update' \
            else previews + [clean]

    def analysis(self):
        """/api/analysis: every enabled block's readouts (Appendix A) and the probe they came from."""
        with self.lock:
            scene, doc = self.get_scene(), chain.normalised(self.project.chain_doc())
        st = scene.stats(doc)
        keys = dict(highlights=('knee', 'top', 'affected'), contrast=('m',), curves=('histogram',),
                    white_balance=('B', 'W', 'g', 'ratios', 'pixels', 'note'), rgb_shift=('crops', 'estimates', 'r', 'b'),
                    deconvolution=('mono', 'dering_threshold', 'first_dark_ring_px'),
                    wavelets=('noise', 'cutoff', 'suggested_layer'))
        out = {}
        for path in chain.PATHS:
            for e in chain.enabled(doc, path):
                b = st.blocks.get(e['id'], {})
                info = dict(st.readouts.get(e['id'], {}), **{k: b[k] for k in keys.get(e['type'], ()) if k in b})
                if e['type'] == 'wavelets':
                    info['gains'] = [filters.wavelet_gain(x) for x in e['params']['biases']]
                out[e['id']] = json_safe(info)
        return dict(blocks=out, probe=list(st.probe) if st.probe else None)

    def placement(self):
        """/api/placement: the colour placement, panel by panel, for the match popover."""
        with self.lock:
            st = self.project.stream('colour')
            report = st.get('report')
        if not report:
            return dict(panels=[], summary=None)
        doc = json.loads(self.project.path(report).read_text('utf-8'))
        rows = [dict(name=n, state=e['state'], reason=e.get('reason'), model=e.get('model'), tiles=e.get('tiles'),
                     held_out_rms=e.get('held_out_rms'), rgb_shift={c: e['rgb_shift'][c]['why'] for c in 'rb'})
                for n, e in sorted(doc['panels'].items())]
        return dict(panels=rows, summary=doc['summary'], k=doc['k'], scale_source=doc['scale_source'],
                    photometry=dict(overlaps=doc['photometry']['overlaps'], notes=doc['photometry']['notes']))

    def get_scene(self):
        if self.scene is None:
            if not self.project or not self.project.stream('luminance').get('mosaic'):
                raise ValueError('Build the luminance mosaic first.')
            self.scene = Scene(self.project)
        return self.scene

    def export_options(self, geometry, layers, options):
        geometry = dict(geometry)
        if options.get('coverage') is not None:
            geometry['coverage'] = options['coverage']
        Project.validate_geometry(geometry)
        layer = options.get('layer', 'composite')
        factor = float(options.get('factor', 1))
        if layer not in ('composite', 'luminance', 'colour'):
            raise ValueError('Unknown export layer.')
        if factor not in (1, .5, .25):
            raise ValueError('Export scale must be 1, 1/2 or 1/4.')
        if layer == 'colour' and self.get_scene().match is None:
            raise ValueError('Match the colour mosaic before exporting this layer.')
        if layer == 'composite' and not layers['l_visible'] and (not layers['rgb_visible'] or self.get_scene().match is None):
            raise ValueError('Enable a layer in the workspace before exporting the combined image.')
        return geometry, layer, factor

    def get(self, h, path):
        parsed = urlsplit(h.path)
        query = parse_qs(parsed.query)
        try:
            if path == '/':
                h.file(str(UI/'index.html'), 'text/html')
            elif path[1:] in UI_FILES:
                h.file(str(UI/path[1:]), UI_FILES[path[1:]])
            elif path == '/api/blocks':
                h.json(dict(blocks=block_catalog.catalog(), paths=list(chain.PATHS)))
            elif path == '/api/analysis':
                self.require(dict(id=query.get('id', [''])[0]))
                h.json(self.analysis())
            elif path == '/api/placement':
                self.require(dict(id=query.get('id', [''])[0]))
                h.json(self.placement())
            elif path == '/api/chain-file':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    doc = p.chain_doc()
                h.reply(json.dumps(doc, indent=2).encode(), 'application/json')
            elif path == '/api/state':
                h.json(self.status())
            elif path == '/api/recent':
                h.json(dict(projects=self.history.summaries() if self.history else [],
                            warning=self.history_error, enabled=self.history is not None))
            elif path == '/api/export-preview':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    options = {key:query[key][0] for key in ('coverage', 'layer', 'factor') if key in query}
                    layers, doc = p.layers, p.chain_doc()
                    geometry, layer, factor = self.export_options(p.data['geometry'], layers, options)
                    scene = self.get_scene()
                rgb, info = scene.preview(doc, geometry, layers, layer)
                _, (w, height), _ = scene.geometry(geometry, factor)
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.json(dict(width=w, height=height, raw_bytes=w*height*6,
                            missing_colour=info['missing_colour'],
                            jpeg_max_side=lrgb.JPEG_MAX_SIDE, jpeg_max_pixels=lrgb.JPEG_MAX_PIXELS,
                            composition=layer if layer != 'composite' else 'LRGB' if layers['l_visible'] and layers['rgb_visible'] and scene.match is not None else 'luminance' if layers['l_visible'] else 'colour',
                            preview='data:image/png;base64,'+base64.b64encode(png).decode('ascii')))
            elif path == '/api/preview':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    scene, doc, geometry, layers = self.get_scene(), p.chain_doc(), dict(p.data['geometry']), p.layers
                view = query.get('view', ['composite'])[0]
                if view not in lrgb.VIEWS:
                    raise ValueError('Unknown view.')
                rgb, info = scene.preview(doc, geometry, layers, view)
                ok, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.reply(png.tobytes(), 'image/png')
            elif path == '/api/geometry':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    scene, geometry = self.get_scene(), dict(p.data['geometry'])
                matrix, (w, height), box = scene.geometry(geometry)
                full, (fw, fh), _ = scene.geometry(dict(geometry, coverage='full'))
                h.json(dict(width=w, height=height, matrix=matrix.tolist(), full=dict(matrix=full.tolist(), width=fw, height=fh),
                            box=list(box), matched=scene.match is not None, missing_colour=scene.missing_colour(geometry),
                            sets=json_safe(scene.set_legend)))
            elif path == '/api/viewport':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    scene, doc, geometry, layers = self.get_scene(), p.chain_doc(), dict(p.data['geometry']), p.layers
                x, y, scale = [float(query.get(k, ['0'])[0]) for k in ('x', 'y', 'scale')]
                w, height = [int(query.get(k, ['0'])[0]) for k in ('w', 'h')]
                if not all(np.isfinite(v) for v in (x, y, scale)) or not .001 <= scale <= 8 or not 1 <= w <= 4096 or not 1 <= height <= 4096 or w*height > 6_000_000:
                    raise ValueError('Invalid viewport.')
                view = query.get('view', ['composite'])[0]
                if view not in lrgb.VIEWS:
                    raise ValueError('Unknown view.')
                matrix, _, _ = scene.geometry(geometry)
                transform = np.array([[scale, 0, -x], [0, scale, -y], [0, 0, 1]]) @ matrix
                rgb, _ = scene.render(transform, (w, height), doc, layers, view, precise=scale >= .75)
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.reply(png.tobytes(), 'image/png')
            elif path == '/api/canvas-estimate':
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    sets = copy.deepcopy(p.feeding('luminance'))
                    paths = {x['id']: p.input_paths(x['id']) for x in sets if x['inputs']}
                    stream = copy.deepcopy(p.stream('luminance'))
                h.json(self.canvas_estimate(sets, paths, stream))
            elif path == '/api/render':
                # plan §4.7: a region of the oriented full view (view px, before the coverage crop) at full
                # scale, exact — what a preview rectangle shows; 16-bit PNG by default
                with self.lock:
                    p = self.require(dict(id=query.get('id', [''])[0]))
                    scene = self.get_scene()
                    geometry, doc = dict(p.data['geometry']), p.chain_doc()
                stage = query.get('stage', ['final'])[0]
                depth = query.get('depth', ['16'])[0]
                if stage not in chain.STAGES or depth not in ('8', '16'):
                    raise ValueError('Choose stage final, luminance or colour, and depth 8 or 16.')
                if 'preview' in query:
                    box = next((v for v in doc['previews'] if v['id'] == query['preview'][0]), None)
                    if box is None:
                        raise ValueError('Unknown preview.')
                    x, y, w, height = (int(box[k]) for k in ('x', 'y', 'w', 'h'))
                else:
                    x, y, w, height = (int(query.get(k, ['0'])[0]) for k in ('x', 'y', 'w', 'h'))
                if not 1 <= w <= 4096 or not 1 <= height <= 4096 or w*height > 8_000_000:
                    raise ValueError('Invalid render region.')
                matrix, _, _ = scene.geometry(dict(geometry, coverage='full'))
                rgb, _ = scene.render_view(matrix, (w, height), doc, dict(final='composite').get(stage, stage),
                                           origin=(x, y))
                top = 65535 if depth == '16' else 255
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*top).astype(np.uint16 if depth == '16' else np.uint8))
                h.reply(png.tobytes(), 'image/png')
            elif path in ('/api/native-preview', '/api/layer-thumbnail'):
                with self.lock:
                    self.require(dict(id=query.get('id', [''])[0]))
                    key = query.get('set', [''])[0]
                    if key not in ('L', 'RGB'):
                        raise ValueError('Unknown panel set.')
                    source = self.project.stream_of(key).get('mosaic')
                    single = self.project.input_paths('RGB') if key == 'RGB' else []
                    if len(single) == 1 and path == '/api/native-preview':
                        image = Image(single[0])     # control points are clicked on the colour image itself
                    elif not source:
                        raise ValueError('Build this layer first.')
                    else:
                        image = Image(self.project.path(source))
                rgb, _ = image.preview(96 if path == '/api/layer-thumbnail' else 1200)
                if key == 'L' and path == '/api/layer-thumbnail':
                    rgb = cv2.cvtColor(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
                _, png = cv2.imencode('.png', np.round(rgb[..., ::-1]*255).astype(np.uint8))
                h.reply(png.tobytes(), 'image/png')
            else:
                return False
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                              # the page went away (a reload, a superseded preview): no one to answer
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
                    if key not in {x['id'] for x in p.data['sets']} or p.stream_of(key).get('mosaic'):
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
                        self.inputs(key, p.set(key)['inputs']+[str(target)])
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
                    self.worker.cancel()
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
                        if not isinstance(keys, list) or not keys or any(k not in {x['id'] for x in p.data['sets']} for k in keys):
                            raise ValueError('Choose luminance or colour sets.')
                        canvas = self.canvas_options(o.get('canvas'))
                        self.start('Build mosaics', lambda: self.build(keys, canvas))
                    elif path == '/api/sets':
                        self.sets_op(o)
                    elif path == '/api/match':
                        self.start('Match colour to luminance', self.match)
                    elif path == '/api/check-match':
                        self.start('Check alignment', self.check_alignment)
                    elif path == '/api/north-up':
                        self.start('Find lunar north', self.north_up)
                    elif path == '/api/manual':
                        self.idle()
                        if not p.stream('luminance').get('mosaic'):
                            raise ValueError('Build the luminance mosaic first.')
                        colour = p.input_paths('RGB')
                        if len(colour) != 1:
                            raise ValueError('Control points place a single-image colour set; this set has several panels.')
                        T = manual_match(o['rgb_points'], o['l_points'])['matrix']
                        initial = {Path(colour[0]).stem: T}
                        self.start('Place colour from control points', lambda: self.place_colour(initial))
                    elif path in ('/api/chain', '/api/geometry', '/api/previews', '/api/chain-file'):
                        # edits carry the revision they were made on: a stale one is refused, not merged (T-STALE)
                        if o.get('revision') != p.data['revision']:
                            h.json(dict(error='The project changed elsewhere. Your change was kept here; it is sent '
                                              'again on top of the newer project.', conflict=True), 409)
                            return True
                        if path == '/api/chain':
                            p.set_chain(o['chain'])
                        elif path == '/api/chain-file':
                            doc = o['chain']                  # an imported chain: fresh ids, this project's previews
                            if isinstance(doc, dict) and isinstance(doc.get('chains'), dict):
                                for name in chain.PATHS:
                                    for e in doc['chains'].get(name) or []:
                                        if isinstance(e, dict):
                                            e['id'] = secrets.token_hex(4)
                            p.set_chain(doc)
                        elif path == '/api/geometry':
                            old = dict(p.data['geometry'])
                            if 'geometry' in o:
                                p.set_geometry(o['geometry'])
                                if p.stream('luminance').get('mosaic'):
                                    self.get_scene().geometry(p.data['geometry'])   # a common crop must exist
                            if 'view' in o:
                                p.set_view(o['view'])
                            if p.stream('luminance').get('mosaic'):
                                self.move_previews(old, p.data['geometry'])
                        else:
                            self.preview_op(o)
                        p.save()
                        if path != '/api/previews' and self.scene is not None:
                            # statistics for the new chain start now, before the previews ask for them
                            self.worker.submit(self.scene, p.chain_doc())
                    elif path == '/api/notes':
                        p.data['notes'] = []                  # the migration notes, shown once
                        p.save()
                    elif path == '/api/cancel':
                        self.cancel_job()
                    elif path == '/api/export':
                        scene, doc, layers = self.get_scene(), p.chain_doc(), p.layers
                        geometry, layer, factor = self.export_options(p.data['geometry'], layers, o)
                        self.start('Export image', lambda: scene.export(o['output'], doc, geometry, layers, factor, layer,
                            progress=lambda f: self.update_job(progress=f, title=f'Writing image · {f:.0%}'),
                            cancelled=self.cancel.is_set))
                    else:
                        h.json(dict(error='Unknown operation.'), 404)
                        return True
                h.json(self.status())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                              # the page went away before the answer
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
