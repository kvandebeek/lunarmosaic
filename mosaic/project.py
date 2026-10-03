"""Durable, versioned LRGB project; source images are never edited."""
from pathlib import Path
import copy
import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone

DEFAULT_EDITS = dict(saturation=1.0, smoothing=0.0, strength=1.0,
                     highlights=0.0, contrast=0.0, rotation=0.0, coverage='full', mirror=False,
                     wavelets=[0.0, 0.0, 0.0, 0.0], wavelet_kernel='linear', wavelet_denoise=2.0,
                     usm_amount=0.0, usm_radius=1.0, usm_threshold=.01,
                     l_visible=True, rgb_visible=True)
RANGES = dict(saturation=(0, 4), smoothing=(0, 8), strength=(0, 1),
              highlights=(0, 1), contrast=(-1, 1), rotation=(-180, 180), wavelet_denoise=(0, 5),
              usm_amount=(0, 1), usm_radius=(.3, 5), usm_threshold=(0, .05))


def now():
    return datetime.now(timezone.utc).isoformat()


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
        p = cls(folder, dict(version=3, id=uuid.uuid4().hex, name=str(name)[:120],
                            created=now(), updated=now(), revision=0,
                            sets={k: dict(inputs=[], optics={}, state='empty', mosaic=None,
                                          error=None) for k in ('L', 'RGB')},
                            match=None, orientation=None, edits=copy.deepcopy(DEFAULT_EDITS)))
        (folder / 'derived').mkdir()
        p.save()
        return p

    @classmethod
    def open(cls, folder):
        folder = Path(folder).expanduser().resolve()
        if folder.name == 'project.json':
            folder = folder.parent
        data = json.loads((folder / 'project.json').read_text('utf-8'))
        if data.get('version') not in (1, 2, 3):
            raise ValueError('This project version is not supported.')
        if not isinstance(data.get('sets'), dict) or set(data['sets']) != {'L', 'RGB'}:
            raise ValueError('The project has invalid panel sets.')
        p = cls(folder, data)
        if data['version'] in (1, 2):
            data['edits'] = dict(copy.deepcopy(DEFAULT_EDITS), **data['edits'])
            data.setdefault('orientation', None)
            data['version'] = 3
        p.validate_edits(data['edits'])
        for s in data['sets'].values():
            if s['state'] in ('building', 'queued'):
                s.update(state='interrupted', error='Build interrupted. Restart this set; completed mosaics were kept.')
            if s.get('mosaic') and not p.path(s['mosaic']).is_file():
                s.update(state='missing', error='The built mosaic is missing.', mosaic=None)
                data['match'] = None
                data['edits']['coverage'] = 'full'
                data['edits']['l_visible'] = True
        p.save()
        return p

    def path(self, relative):
        result = (self.folder / relative).resolve()
        if not result.is_relative_to(self.folder):
            raise ValueError('Project asset is outside the project folder.')
        return result

    def input_paths(self, key):
        return [str(Path(v).resolve()) if Path(v).is_absolute() else str(self.path(v))
                for v in self.data['sets'][key]['inputs']]

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
        return copy.deepcopy(dict(self.data, folder=str(self.folder)))
