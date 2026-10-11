"""Isolated, cancellable LunarAtlas north-up solve for a LunarMosaic project."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'moon_atlas'))
from atlas_geo import NoUsableLimb, locate, north_up_matrix
from atlas_ephem import capture_time
from lrgb import Image
from project import Project


def solve(project, log=print):
    s = project.stream('luminance')
    if not s.get('mosaic'):
        raise ValueError('Build luminance before finding lunar north.')
    source = Image(project.path(s['mosaic']))
    rgb, spacing = source.preview(1800)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    times = [t for x in project.sets_of('luminance') for p in project.input_paths(x['id'])
             if (t := capture_time(p)) is not None]
    ordered = sorted(times)
    gaps = [(b-a).total_seconds() for a, b in zip(ordered, ordered[1:])]
    if gaps and max(gaps) > 90 * 60:
        raise ValueError('North-up is ambiguous: luminance panels contain capture sessions separated by more than 90 minutes. Separate the sessions or use shared rotation manually.')
    if times and (max(times) - min(times)).total_seconds() > 12 * 3600:
        dates = ', '.join(sorted({t.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') for t in times}))
        raise ValueError('North-up is ambiguous: luminance panels span more than 12 hours '
                         f'({dates}). Separate the sessions or use shared rotation manually.')
    when = datetime.fromtimestamp(float(np.median([t.timestamp() for t in times])), timezone.utc) if times else None
    try:
        geo, quality = locate(gray*65535, log=log, when=when)
        method = 'lunar disk and terrain'
    except NoUsableLimb as e:
        if when is None:
            raise ValueError('North-up needs a visible lunar limb or a capture time in the panel filenames. Use shared rotation manually.') from e
        import atlas_closeup
        # Use only project equipment. The standalone app must not read or change
        # a separately installed LunarAtlas application's equipment registry.
        atlas_closeup.setups = lambda: []
        # the canvas's own pixel scale (the finest luminance set's, after any canvas reduction), as equivalent optics
        if s.get('canvas_arcsec_px'):
            optics = dict(focal_mm=1000.0, pixel_um=s['canvas_arcsec_px'] * 1000.0 / 206.265)
        else:
            scales = [x.get('canvas_arcsec_px') for x in project.sets_of('luminance') if x.get('canvas_arcsec_px')]
            optics = dict(focal_mm=1000.0, pixel_um=min(scales) * 1000.0 / 206.265) if scales else project.set('L')['optics']
        prior = None
        if optics.get('pixel_um') and optics.get('focal_mm'):
            prior = dict(focal_mm=optics['focal_mm']*optics.get('barlow', 1),
                         pixel_um=optics['pixel_um']*spacing[0, 0], text='This project’s luminance optics')
        log('No usable limb; searching the close-up using the panels’ capture time.')
        with tempfile.TemporaryDirectory(prefix='.north-', dir=project.folder/'derived') as folder:
            image = Path(folder)/'preview.tif'
            if not cv2.imwrite(str(image), np.round(gray*65535).astype(np.uint16)):
                raise OSError('Could not write the orientation preview.')
            geo, quality = atlas_closeup.locate_closeup(str(image), log=log, optics=prior, when=when, any_scale=True)
        method = 'close-up terrain and capture time'
    matrix, rotation, mirrored = north_up_matrix(geo)
    if quality.get('matches', 0) < 12 or not np.isfinite(matrix).all():
        raise ValueError('Too little terrain evidence to trust north-up. Use shared rotation manually.')
    return dict(rotation=float(-rotation), mirror=bool(mirrored), matrix=matrix.tolist(),
                method=method, matches=int(quality['matches']),
                rms_px=float(quality['rms_px'])*float(spacing[0, 0]),
                proxy_step=float(spacing[0, 0]), capture_utc=when.isoformat() if when else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--result', required=True)
    args = parser.parse_args()
    folder = Path(args.project).resolve()
    project = Project(folder, json.loads((folder/'project.json').read_text('utf-8')))
    try:
        result = solve(project, log=lambda text: print(text, flush=True))
    except (ValueError, OSError, SystemExit) as e:
        print(f'North-up unavailable: {e}', flush=True)
        return 1
    Path(args.result).write_text(json.dumps(result, allow_nan=False), 'utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
