"""Durable, versioned LRGB project (schema 4); source images are never edited.

Schema 4 (plan §4.6): a list of panel sets, each feeding a stream — any number of luminance sets, built jointly
into one luminance mosaic on the finest set's grid (phase 5), and the colour set; the build state of each stream;
geometry (rotation, mirror, coverage) and view (layer visibility) kept apart from the editing chain. Until the
interface moves to chains (plan phase 7) it still edits the flat adjustments of schema 3: they live in
`legacy_edits`, and every change regenerates the chain from them.

Projects of schema 1–3 are migrated once, on opening, and the original file is kept as project.v<N>.json.
"""
from pathlib import Path
import copy
import hashlib
import json
import math
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

import chain

SCHEMA = 4
STREAMS = ('luminance', 'colour')
GEOMETRY_KEYS, VIEW_KEYS = ('rotation', 'mirror', 'coverage'), ('l_visible', 'rgb_visible')
DEFAULT_EDITS = dict(saturation=1.0, smoothing=0.0, strength=1.0,
                     highlights=0.0, contrast=0.0, rotation=0.0, coverage='full', mirror=False,
                     wavelets=[0.0, 0.0, 0.0, 0.0], wavelet_kernel='linear', wavelet_denoise=2.0,
                     usm_amount=0.0, usm_radius=1.0, usm_threshold=.01,
                     l_visible=True, rgb_visible=True)
RANGES = dict(saturation=(0, 4), smoothing=(0, 8), strength=(0, 1),
              highlights=(0, 1), contrast=(-1, 1), rotation=(-180, 180), wavelet_denoise=(0, 5),
              usm_amount=(0, 1), usm_radius=(.3, 5), usm_threshold=(0, .05))
# what a set holds; everything else a schema-3 set held is its stream's build state
SET_KEYS = ('id', 'stream', 'label', 'inputs', 'optics', 'skipped', 'headers', 'scale_estimate')
OPTICS = ('pixel_um', 'focal_mm', 'barlow', 'aperture_mm', 'wavelength_nm')
BUILD_STATES = ('empty', 'ready', 'queued', 'building', 'built', 'failed', 'interrupted', 'missing')
CANVAS_CAP_MPX = 400          # above this the canvas needs confirming (plan phase 5)


def now():
    return datetime.now(timezone.utc).isoformat()


def new_set(key, stream, label):
    return dict(id=key, stream=stream, label=label, inputs=[], optics={})


def new_stream():
    return dict(state='empty', mosaic=None, error=None)


def split_set(old):
    """A schema-3 set (or a phase-4 schema-4 one) -> (its set fields, its build state)."""
    return ({k: copy.deepcopy(v) for k, v in old.items() if k in SET_KEYS},
            {k: copy.deepcopy(v) for k, v in old.items() if k not in SET_KEYS})


def clean_optics(optics):
    """Optics as entered: positive numbers (µm, mm, ×, mm, nm), blanks dropped; raises ValueError."""
    if not isinstance(optics, dict) or set(optics) - set(OPTICS):
        raise ValueError('Optics are ' + ', '.join(OPTICS) + '.')
    clean = {}
    for name in OPTICS:
        v = optics.get(name)
        if v in (None, ''):
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError('Optics values must be positive numbers.') from None
        if not math.isfinite(v) or not 0 < v < 100000:
            raise ValueError('Optics values must be positive numbers.')
        clean[name] = v
    return clean


def arcsec_px(optics):
    """The pixel scale from optics, 206.265 × pixel µm ÷ (focal mm × Barlow), or None without them."""
    if optics.get('pixel_um') and optics.get('focal_mm'):
        return 206.265 * optics['pixel_um'] / (optics['focal_mm'] * optics.get('barlow', 1.0))
    return None


def luminance_scales(sets):
    """{set id: {rel, arcsec_px, source}} for the luminance sets with panels: rel = the set's pixel scale ÷ the
    finest set's (≥ 1). From optics; otherwise from a confirmed estimate against another set (D10). One set needs
    neither. Raises ValueError naming a set whose scale is unknown or unconfirmed: a mixed stream is never built on
    a guess."""
    sets = [s for s in sets if s['stream'] == 'luminance' and s.get('inputs')]
    if len(sets) == 1:
        a = arcsec_px(sets[0].get('optics', {}))
        return {sets[0]['id']: dict(rel=1.0, arcsec_px=a, source='optics' if a else 'single set')}
    known = {s['id']: (arcsec_px(s.get('optics', {})), 'optics') for s in sets if arcsec_px(s.get('optics', {}))}
    ratios = {}
    for s in sets:
        e = s.get('scale_estimate')
        if s['id'] not in known and e:
            if not e.get('confirmed'):
                raise ValueError(f'Confirm the estimated scale of set {s.get("label") or s["id"]} '
                                 f'({e["ratio"]:.3f}× {e["relative_to"]}) before building.')
            ratios[s['id']] = (e['relative_to'], e['ratio'])
    # resolve estimates against sets of known scale; with no optics at all, against an arbitrary unit
    scale = {k: v[0] for k, v in known.items()}
    source = {k: v[1] for k, v in known.items()}
    unit = None
    for _ in range(len(sets)):
        for sid, (ref, r) in ratios.items():
            if sid not in scale and ref in scale:
                scale[sid], source[sid] = r * scale[ref], 'estimated'
        if not known and unit is None and ratios:
            unit = next(iter(ratios.values()))[0]
            scale.setdefault(unit, 1.0)
            source.setdefault(unit, 'estimated')
    missing = [s for s in sets if s['id'] not in scale]
    if missing:
        s = missing[0]
        raise ValueError(f'Set {s.get("label") or s["id"]} needs its optics (pixel size and focal length) or an '
                         'estimated scale before a mixed build.')
    finest = min(scale.values())
    absolute = bool(known)
    return {sid: dict(rel=scale[sid] / finest, arcsec_px=scale[sid] if absolute else None, source=source[sid])
            for sid in scale}


def _entry(path, type, params):
    """A block whose id follows from where it sits: the same adjustments always give the same chain document, so
    the statistics cached for it stay valid (random ids would make every slider move look like a new chain)."""
    e = chain.entry(type, params)
    e['id'] = hashlib.sha1(f'{path}/{type}'.encode()).hexdigest()[:8]
    return e


def edits_to_chain(edits):
    """The schema-3 adjustments as a chain (plan Appendix D), and the notes on what changed in meaning.

    Two departures from Appendix D, both because the current interface keeps editing these sliders through this
    translation until phase 7 (DESIGN.md §9): highlights and contrast join the luminance chain, where the old
    renderer applied them (before the merge, so the luminance view still shows them); and their strength follows
    the slider (0.49 × value; the old contrast had midtone slope 1 + value/2, the block 1 + strength) instead of
    being switched on at the block's default — negative contrast included, which the block accepts."""
    doc, notes = chain.empty(), []
    lum, col = doc['chains']['luminance'], doc['chains']['colour']
    if any(edits['wavelets']):
        # the same layer gains (1 + amount = 1 + 10 × bias); they now act on stored values, as in PixInsight
        lum.append(_entry('luminance', 'wavelets', {'biases': [a / 10 for a in edits['wavelets']],
                                                    'kernel': edits['wavelet_kernel'], 'denoise': [edits['wavelet_denoise']] * 4}))
        notes.append('Wavelet sharpening now works on the stored values, as PixInsight does, with the same layer gains.')
    if edits['usm_amount'] > 0:
        lum.append(_entry('luminance', 'unsharp_mask', {'amount': edits['usm_amount'] * 100, 'radius': edits['usm_radius'],
                                                        'threshold': edits['usm_threshold'] * 255, 'mode': 'luminosity'}))
        notes.append('The unsharp mask now behaves as Photoshop\'s: its threshold is soft and its radius slightly wider.')
    if edits['highlights'] > 0:
        lum.append(_entry('luminance', 'highlights', {'strength': 0.49 * edits['highlights']}))
    if edits['contrast'] != 0:
        lum.append(_entry('luminance', 'contrast', {'strength': 0.49 * edits['contrast']}))
    if edits['highlights'] > 0 or edits['contrast'] != 0:
        notes.append('Highlights and contrast now use the finishing tool\'s curves; check their strength.')
    if edits['smoothing'] > 0:
        col.append(_entry('colour', 'gaussian_blur', {'sigma': edits['smoothing']}))
        notes.append('Colour smoothing now blurs the colour image, whose lightness the merge does not use.')
    if edits['saturation'] != 1:
        col.append(_entry('colour', 'saturation', {'factor': max(edits['saturation'], 0.01), 'passes': 1, 'lunar_hue': False}))
    doc['chains']['merge']['strength'] = float(edits['strength'])
    return doc, notes


def chain_part(doc):
    """The chain document as a project stores it (without format and version)."""
    return {k: copy.deepcopy(doc[k]) for k in ('protect_space', 'chains', 'previews')}


def chain_doc(part):
    return dict(format=chain.FORMAT, version=chain.VERSION, **copy.deepcopy(part))


def migrate(data):
    """Schema 1–3 -> 4, without changing the values of what carries over; returns (data, notes)."""
    edits = dict(copy.deepcopy(DEFAULT_EDITS), **data.get('edits', {}))
    sets, streams = [], {}
    for key, stream, label in (('L', 'luminance', 'Luminance'), ('RGB', 'colour', 'Colour')):
        part, state = split_set(data['sets'][key])
        sets.append(dict(new_set(key, stream, label), **part, id=key, stream=stream))
        streams[stream] = dict(new_stream(), **state)
    streams['colour']['legacy_match'] = data.get('match')
    doc, notes = edits_to_chain(edits)
    out = dict(version=SCHEMA, id=data['id'], name=data.get('name', 'Untitled Moon'), created=data.get('created', now()),
               updated=data.get('updated', now()), revision=data.get('revision', 0), sets=sets, **streams,
               orientation=data.get('orientation'),
               geometry={k: edits[k] for k in GEOMETRY_KEYS}, view={k: edits[k] for k in VIEW_KEYS},
               legacy_edits={k: v for k, v in edits.items() if k not in GEOMETRY_KEYS + VIEW_KEYS},
               chain=chain_part(doc), notes=notes)
    return out, notes


def from_phase4(data):
    """The first schema-4 layout (phase 4, never released) kept build state in each set: move it to the streams."""
    if 'luminance' in data or not any('state' in s for s in data.get('sets', [])):
        return data
    data = copy.deepcopy(data)
    for s in list(data['sets']):
        part, state = split_set(s)
        data['sets'][data['sets'].index(s)] = part
        if s.get('stream') in STREAMS:
            data[s['stream']] = dict(new_stream(), **data.get(s['stream'], {}), **state)
    return data


class Project:
    def __init__(self, folder, data):
        self.folder = Path(folder).expanduser().resolve()
        self.data = data

    @classmethod
    def create(cls, folder, name='Untitled Moon'):
        folder = Path(folder).expanduser().resolve()
        if folder.exists() and any(p.name != 'project.json.lock' for p in folder.iterdir()):
            raise ValueError('Choose a new or empty project folder.')
        folder.mkdir(parents=True, exist_ok=True)
        edits = copy.deepcopy(DEFAULT_EDITS)
        p = cls(folder, dict(version=SCHEMA, id=uuid.uuid4().hex, name=str(name)[:120], created=now(), updated=now(),
                             revision=0, sets=[new_set('L', 'luminance', 'Luminance'), new_set('RGB', 'colour', 'Colour')],
                             luminance=new_stream(), colour=dict(new_stream(), legacy_match=None), orientation=None,
                             geometry={k: edits[k] for k in GEOMETRY_KEYS}, view={k: edits[k] for k in VIEW_KEYS},
                             legacy_edits={k: v for k, v in edits.items() if k not in GEOMETRY_KEYS + VIEW_KEYS},
                             chain=chain_part(chain.empty()), notes=[]))
        (folder / 'derived').mkdir()
        p.save()
        return p

    @classmethod
    def open(cls, folder):
        """Open a project, migrating schema 1–3 in memory; it is saved only when opening changed something (a
        migration, an interrupted build, a missing mosaic), so opening twice leaves the revision alone."""
        folder = Path(folder).expanduser().resolve()
        if folder.name == 'project.json':
            folder = folder.parent
        path = folder / 'project.json'
        data = json.loads(path.read_text('utf-8'))
        version = data.get('version')
        if version in (1, 2, 3):
            if not isinstance(data.get('sets'), dict) or set(data['sets']) != {'L', 'RGB'}:
                raise ValueError('The project has invalid panel sets.')
            backup = folder / f'project.v{version}.json'
            if not backup.exists():                       # migration is one-way: keep the original
                shutil.copy2(path, backup)
            data, _ = migrate(data)
        elif version != SCHEMA:
            raise ValueError('This project version is not supported.')
        before = copy.deepcopy(data)
        data = from_phase4(data)
        p = cls(folder, data)
        p.validate()
        for name in STREAMS:
            st = data[name]
            if st['state'] in ('building', 'queued'):
                st.update(state='interrupted', error='Build interrupted. Restart this set; completed mosaics were kept.')
            if st.get('mosaic') and not p.path(st['mosaic']).is_file():
                st.update(state='missing', error='The built mosaic is missing.', mosaic=None)
                data['colour']['legacy_match'] = None
                data['geometry']['coverage'] = 'full'
                data['view']['l_visible'] = True
        if version != SCHEMA or data != before:
            p.save()
        return p

    def validate(self):
        """Sets, geometry, view and chain (plan phase 4); raises ValueError with the first problem."""
        d = self.data
        sets = d.get('sets')
        ids = [s.get('id') if isinstance(s, dict) else None for s in sets] if isinstance(sets, list) else []
        if (not ids or len(set(ids)) != len(ids) or 'L' not in ids or 'RGB' not in ids
                or any(not isinstance(i, str) or not i or len(i) > 32 for i in ids)
                or any(s.get('stream') not in STREAMS or not isinstance(s.get('inputs'), list)
                       or not isinstance(s.get('optics', {}), dict) for s in sets)
                or self.set('L')['stream'] != 'luminance' or self.set('RGB')['stream'] != 'colour'
                or sum(s['stream'] == 'colour' for s in sets) != 1):
            raise ValueError('The project has invalid panel sets.')
        for name in STREAMS:
            if not isinstance(d.get(name), dict) or d[name].get('state') not in BUILD_STATES:
                raise ValueError(f'The project\'s {name} build state is invalid.')
        for key, keys in (('geometry', GEOMETRY_KEYS), ('view', VIEW_KEYS)):
            if not isinstance(d.get(key), dict) or set(d[key]) != set(keys):
                raise ValueError(f'The project\'s {key} is invalid.')
        if not isinstance(d.get('colour'), dict) or not isinstance(d.get('legacy_edits'), dict):
            raise ValueError('The project is missing its colour or adjustment settings.')
        self.validate_edits(self.edits)
        problems = chain.validate(self.chain_doc()) if isinstance(d.get('chain'), dict) else [(None, 'It has none.')]
        if problems:
            raise ValueError('The project\'s chain is invalid: ' + ' '.join(r for _, r in problems))

    def path(self, relative):
        result = (self.folder / relative).resolve()
        if not result.is_relative_to(self.folder):
            raise ValueError('Project asset is outside the project folder.')
        return result

    def set(self, key):
        """The panel set with this id (schema 3 addressed them as sets['L'] and sets['RGB'])."""
        for s in self.data['sets']:
            if s['id'] == key:
                return s
        raise KeyError(f'No panel set {key!r}.')

    def input_paths(self, key):
        return [str(Path(v).resolve()) if Path(v).is_absolute() else str(self.path(v))
                for v in self.set(key)['inputs']]

    def stream(self, name):
        """The build state of a stream: state, mosaic, error, … (and, for colour until phase 6, legacy_match)."""
        return self.data[name]

    def sets_of(self, name):
        return [s for s in self.data['sets'] if s['stream'] == name]

    def stream_of(self, key):
        return self.data[self.set(key)['stream']]

    def refresh_state(self, name):
        """An unbuilt stream is ready when one of its sets has panels, empty otherwise."""
        st = self.data[name]
        if st['state'] in ('empty', 'ready'):
            st['state'] = 'ready' if any(s['inputs'] for s in self.sets_of(name)) else 'empty'

    def add_set(self, stream, label):
        """A new luminance set (colour gets more sets with per-panel placement, phase 6). Ids are s2, s3, …"""
        if stream != 'luminance':
            raise ValueError('Only luminance sets can be added for now; colour has one set until phase 6.')
        taken = {s['id'] for s in self.data['sets']}
        key = next(f's{n}' for n in range(2, 1000) if f's{n}' not in taken)
        self.data['sets'].append(new_set(key, stream, str(label or f'Luminance {key[1:]}')[:120]))
        return key

    # ---------------------------------------------------------------- the schema-3 view, until phase 7

    @property
    def match(self):
        return self.data['colour'].get('legacy_match')

    @match.setter
    def match(self, value):
        self.data['colour']['legacy_match'] = value

    @property
    def edits(self):
        """The flat schema-3 adjustments the current interface edits: legacy_edits plus geometry and view."""
        return dict(copy.deepcopy(self.data['legacy_edits']), **self.data['geometry'], **self.data['view'])

    @edits.setter
    def edits(self, edits):
        self.validate_edits(edits)
        self.data['legacy_edits'] = {k: copy.deepcopy(v) for k, v in edits.items() if k not in GEOMETRY_KEYS + VIEW_KEYS}
        self.data['geometry'] = {k: edits[k] for k in GEOMETRY_KEYS}
        self.data['view'] = {k: edits[k] for k in VIEW_KEYS}
        doc = edits_to_chain(edits)[0]
        old = self.data.get('chain') or {}
        doc.update(protect_space=old.get('protect_space', True), previews=copy.deepcopy(old.get('previews', [])))
        self.data['chain'] = chain_part(doc)

    def chain_doc(self):
        part = self.data['chain']
        if not all(k in part for k in ('protect_space', 'chains', 'previews')):
            return {}                                     # validate() then reports it
        return chain_doc(part)

    @staticmethod
    def validate_edits(edits):
        if not isinstance(edits, dict) or set(edits) != set(DEFAULT_EDITS):
            raise ValueError('Invalid layer adjustments.')
        for key, (lo, hi) in RANGES.items():
            v = edits[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f'{key} must be between {lo} and {hi}.')
        if edits['coverage'] not in ('full', 'common'):
            raise ValueError('Choose full or common coverage.')
        if not isinstance(edits['mirror'], bool):
            raise ValueError('Mirror must be on or off.')
        if any(not isinstance(edits[k], bool) for k in ('l_visible', 'rgb_visible')):
            raise ValueError('Layer visibility must be on or off.')
        if edits['wavelet_kernel'] not in ('linear', 'b3'):
            raise ValueError('Choose linear or B3 wavelets.')
        biases = edits['wavelets']
        if not isinstance(biases, list) or len(biases) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 3 for v in biases):
            raise ValueError('Use four wavelet amounts between 0 and 3.')

    def save(self):
        self.data['updated'] = now()
        self.data['revision'] += 1
        fd, name = tempfile.mkstemp(prefix='.project-', suffix='.json', dir=self.folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.folder / 'project.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def public(self):
        """For the interface: the schema-3 shape it reads today (sets by key, match, edits), with the schema-4 data
        alongside under `schema4`. Phase 7 drops the schema-3 part."""
        d = self.data

        def legacy(key):            # a schema-3 set: the set's own fields plus its stream's build state
            st = {k: v for k, v in d[self.set(key)['stream']].items() if k != 'legacy_match'}
            return copy.deepcopy(dict(st, **{k: v for k, v in self.set(key).items() if k not in ('id', 'stream', 'label')}))
        return dict(version=d['version'], id=d['id'], name=d['name'], created=d['created'], updated=d['updated'],
                    revision=d['revision'], folder=str(self.folder), orientation=copy.deepcopy(d['orientation']),
                    sets={'L': legacy('L'), 'RGB': legacy('RGB')},
                    match=copy.deepcopy(self.match), edits=self.edits, notes=list(d.get('notes', [])),
                    schema4=copy.deepcopy(d))
