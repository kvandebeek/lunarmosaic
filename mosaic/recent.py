"""Local project history; listing never opens, changes or locks a project."""
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


class RecentProjects:
    def __init__(self, path):
        self.path = Path(path).expanduser()

    def read(self):
        try:
            data = json.loads(self.path.read_text('utf-8'))
            return [v for v in data[:12] if isinstance(v, dict) and isinstance(v.get('folder'), str)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def write(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.recent-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(entries[:12], f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)

    def remember(self, folder):
        folder = str(Path(folder).resolve())
        self.write([dict(folder=folder, opened=datetime.now(timezone.utc).isoformat())] +
                   [v for v in self.read() if v['folder'] != folder])

    def summaries(self):
        result = []
        for entry in self.read():
            item = dict(entry, name=Path(entry['folder']).stem, state='Unavailable', bytes=0, available=False)
            try:
                folder = Path(entry['folder'])
                p = json.loads((folder/'project.json').read_text('utf-8'))
                sets = p['sets']
                if isinstance(sets, list):
                    # schema 4: a list of sets, build state per stream, the match in the colour stream
                    streams = {k: v for k, v in p.items() if k in ('luminance', 'colour') and isinstance(v, dict)}
                    by_id = {s['id']: s for s in sets}
                    lum = [s for s in sets if s.get('stream') == 'luminance']
                    sets = {'L': dict(by_id['L'], **streams.get('luminance', {}), inputs=[x for s in lum for x in s['inputs']]),
                            'RGB': dict(by_id['RGB'], **streams.get('colour', {}))}
                    p['match'] = (p.get('colour') or {}).get('legacy_match')
                item.update(name=str(p['name']), available=True,
                            panels={k:len(sets[k]['inputs']) for k in ('L', 'RGB')})
                built = {}
                for key, value in sets.items():
                    asset = (folder/value['mosaic']).resolve() if value.get('mosaic') else None
                    # Ignore malformed external assets; history must not scan other folders.
                    built[key] = bool(asset and asset.is_relative_to(folder.resolve()) and asset.is_file())
                    if built[key]:
                        item['bytes'] += asset.stat().st_size
                if not built['L']:
                    item['state'] = 'Unfinished build' if sets['L']['inputs'] else 'No luminance panels'
                elif not built['RGB']:
                    item['state'] = 'Colour build unfinished' if sets['RGB']['inputs'] else 'Luminance only'
                elif p.get('match'):
                    checks = p['match'].get('diagnostics')
                    item['state'] = f"Checked {checks['median']:.2f} px" if checks and checks.get('measured') else 'Match needs checking'
                else:
                    item['state'] = 'Ready to match'
            except (OSError, ValueError, KeyError, TypeError):
                item.update(available=False, state='Unavailable')
            result.append(item)
        return result
