#!/usr/bin/env python3
"""LunarMosaic: the local interface, and the same project steps as commands.

    python3 lunarmosaic.py [PROJECT]                       open the interface (the default command, `app`)
    python3 lunarmosaic.py new PROJECT --luminance FINE/ --optics 2.9,2000 --luminance WIDE/ --optics 2.9,1000 \\
                           --colour RGB/ --optics 3.76,1000
    python3 lunarmosaic.py build PROJECT                   build the luminance, then place the colour
    python3 lunarmosaic.py chain export PROJECT FILE.json   the editing chain as a file (also for lunar_finish --chain)
    python3 lunarmosaic.py export PROJECT -o Moon.tif

A project is a folder; the source panels are never changed. `new` takes any number of luminance groups and at most
one colour group. Each group is --luminance or --colour with its files or folders, then optionally --optics
PIXEL_UM,FOCAL_MM[,BARLOW[,APERTURE_MM]] and --label NAME for that group. Sets of different pixel scales need
optics, or `scales` to measure and `scales --confirm` to accept their scale. Results are printed as JSON.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'mosaic'))

HISTORY = Path.home()/'.lunarmosaic'/'recent-projects.json'
COMMANDS = ('app', 'new', 'scales', 'build', 'match', 'check-match', 'north-up', 'status', 'chain', 'export')
TICK = re.compile(r'\d+/\d+\s+\[')                # a progress bar's line: the title says the same, more briefly


class Group(argparse.Action):
    """--luminance / --colour PATH…: starts a panel set (they repeat, in order)."""

    def __call__(self, parser, ns, values, option_string=None):
        groups = getattr(ns, 'groups', None) or []
        groups.append(dict(stream=self.const, paths=list(values), optics=None, label=None, flag=option_string))
        ns.groups = groups


class OfGroup(argparse.Action):
    """--optics / --label: belongs to the group just before it."""

    def __call__(self, parser, ns, values, option_string=None):
        groups = getattr(ns, 'groups', None) or []
        if not groups:
            parser.error(f'{option_string} follows the --luminance or --colour group it describes')
        group = groups[-1]
        if group[self.dest] is not None:
            parser.error(f'{option_string} is given twice for the group {group["flag"]} {" ".join(group["paths"])}')
        group[self.dest] = optics(parser, values) if self.dest == 'optics' else values


def optics(parser, text):
    """PIXEL_UM,FOCAL_MM[,BARLOW[,APERTURE_MM]] -> the project's optics fields; a semicolon also separates, so a
    decimal comma can be typed as in "2,9;2000"."""
    parts = [p.strip() for p in (text.split(';') if ';' in text else text.split(','))]
    if ';' in text:
        parts = [p.replace(',', '.') for p in parts]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if not 2 <= len(values) <= 4 or any(v <= 0 for v in values):
        parser.error(f'--optics {text}: give PIXEL_UM,FOCAL_MM[,BARLOW[,APERTURE_MM]], positive numbers')
    return dict(zip(('pixel_um', 'focal_mm', 'barlow', 'aperture_mm'), values))


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command')
    ui = sub.add_parser('app', help='open the local interface (the default command)')
    ui.add_argument('project', nargs='?', help='a project folder to open')
    ui.add_argument('--port', type=int, default=8768, help='first port to try (default: 8768)')
    ui.add_argument('--no-browser', action='store_true', help='print the address instead of opening a browser')
    ui.add_argument('--history-file', default=str(HISTORY), help='recent-project history (default: ~/.lunarmosaic/recent-projects.json)')
    ui.add_argument('--no-history', action='store_true', help='do not keep recent project paths')

    new = sub.add_parser('new', help='create a project from panel sets', formatter_class=argparse.RawDescriptionHelpFormatter,
                         description='Create a project. Each --luminance or --colour starts a panel set; the --optics and '
                                     '--label after it describe that set.')
    new.add_argument('project', help='a new folder (or an empty one)')
    new.add_argument('--name', default='Untitled Moon', help='the project name (default: Untitled Moon)')
    new.add_argument('--luminance', nargs='+', action=Group, const='luminance', metavar='PATH', default=argparse.SUPPRESS,
                     help='a luminance set: panel files or folders (repeat for sets at other scales)')
    new.add_argument('--colour', '--rgb', nargs='+', action=Group, const='colour', metavar='PATH', default=argparse.SUPPRESS,
                     help='the colour set: RGB panel files or folders')
    new.add_argument('--optics', action=OfGroup, metavar='P,F[,B[,A]]', default=argparse.SUPPRESS,
                     help='the preceding set\'s pixel size µm, focal length mm, Barlow, aperture mm')
    new.add_argument('--label', action=OfGroup, metavar='NAME', default=argparse.SUPPRESS, help='the preceding set\'s name')
    new.add_argument('--colour-both', action='store_true',
                     help='the colour set is all the data: it is built as the luminance too (no --luminance then)')

    scales = sub.add_parser('scales', help='measure the scale of luminance sets without optics (D10)')
    scales.add_argument('project')
    scales.add_argument('--confirm', action='store_true', help='accept the measured scales, so a mixed build may run')

    build = sub.add_parser('build', help='build the luminance mosaic, then place the colour panels onto it')
    build.add_argument('project')
    build.add_argument('--canvas-scale', type=float, help='reduce the canvas (0–1]: see the canvas estimate in the interface')
    build.add_argument('--max-canvas-mpx', type=float, help='allow a mixed-scale canvas this large (default: 400 Mpx)')

    for name, text in (('match', 'place the colour panels again'), ('check-match', 'measure the placed colour independently'),
                       ('north-up', 'turn the view lunar north up (reference maps of LunarAtlas)'),
                       ('status', 'print the project')):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument('project')

    chain = sub.add_parser('chain', help='the editing chain as a file')
    how = chain.add_subparsers(dest='action', required=True)
    for name, text in (('export', 'write the project\'s chain to FILE'), ('import', 'replace the project\'s chain with FILE '
                                                                                     '(its previews are kept)')):
        cmd = how.add_parser(name, help=text)
        cmd.add_argument('project')
        cmd.add_argument('file')

    export = sub.add_parser('export', help='write the finished image')
    export.add_argument('project')
    export.add_argument('-o', '--output', required=True, help='.tif, .png (16-bit) or .jpg (8-bit)')
    export.add_argument('--scale', type=float, choices=(1, .5, .25), default=1, help='1, 0.5 or 0.25 (default: 1)')
    export.add_argument('--layer', choices=('composite', 'luminance', 'colour'), default='composite',
                        help='the combined image or one layer, on the same canvas (default: composite)')
    export.add_argument('--coverage', choices=('full', 'common'), help='override the project\'s coverage for this export')
    return ap


def create(app, args, ap):
    """`new`: the project and its sets, in the order given."""
    groups = getattr(args, 'groups', [])
    lum = [g for g in groups if g['stream'] == 'luminance']
    colour = [g for g in groups if g['stream'] == 'colour']
    if len(colour) > 1:
        ap.error('give at most one --colour group: the colour stream has one set')
    if args.colour_both and (lum or not colour):
        ap.error('--colour-both needs a --colour group and no --luminance group')
    if not lum and not args.colour_both:
        ap.error('give at least one --luminance group (or --colour … --colour-both)')
    app.select(args.project, create=True, name=args.name)
    if args.colour_both:
        app.sets_op(dict(op='update', set=dict(id='RGB', stream='both')))
    for i, g in enumerate(lum):
        key = 'L' if i == 0 else app.sets_op(dict(op='create', set=dict(stream='luminance')))
        if g['label']:
            app.sets_op(dict(op='update', set=dict(id=key, label=g['label'])))
        app.inputs(key, g['paths'], g['optics'])
    for g in colour:
        if g['label']:
            app.sets_op(dict(op='update', set=dict(id='RGB', label=g['label'])))
        app.inputs('RGB', g['paths'], g['optics'])
    return app.status()['project']


def main(argv=None):
    ap = parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ('-h', '--help'):
        argv.insert(0, 'app')                     # `lunarmosaic.py PROJECT` and `lunarmosaic.py --port N` open the interface
    args = ap.parse_args(argv)
    from app import Application, serve
    command = args.command or 'app'
    app = Application(history_path=getattr(args, 'history_file', str(HISTORY))
                      if command == 'app' and not getattr(args, 'no_history', False) else None)
    if command != 'app':
        app.echo = lambda line: None if TICK.match(line) else print(line, flush=True)
    try:
        if command == 'app':
            if getattr(args, 'project', None):
                app.select(args.project)
            serve(app, getattr(args, 'port', 8768), not getattr(args, 'no_browser', False))
            return 0
        if command == 'new':
            result = create(app, args, ap)
        else:
            app.select(args.project)
            p = app.project
            if command == 'scales':
                if args.confirm:
                    pending = [s['id'] for s in p.data['sets'] if (s.get('scale_estimate') or {}).get('confirmed') is False]
                    if not pending:
                        raise ValueError('No measured scale is waiting: run `scales` first.')
                    for key in pending:
                        app.sets_op(dict(op='confirm', set=dict(id=key)))
                    result = {s['id']: s['scale_estimate'] for s in p.data['sets'] if s['id'] in pending}
                else:
                    result = app.estimate_scales()
            elif command == 'build':
                canvas = {k: v for k, v in (('canvas_scale', args.canvas_scale), ('max_canvas_mpx', args.max_canvas_mpx))
                          if v is not None}
                result = app.build(['L', 'RGB'], app.canvas_options(canvas) if canvas else None)
                print(json.dumps(result, indent=2))
                for name in ('luminance', 'colour'):
                    if p.stream(name).get('error'):
                        print(f'LunarMosaic: {name}: {p.stream(name)["error"]}', file=sys.stderr)
                return 1 if any(v != 'built' for v in result.values()) else 0
            elif command == 'match':
                result = app.match()
            elif command == 'check-match':
                result = app.check_alignment()
            elif command == 'north-up':
                result = app.north_up()
            elif command == 'status':
                result = app.status()['project']
            elif command == 'chain':
                if args.action == 'export':
                    Path(args.file).write_text(json.dumps(p.chain_doc(), indent=2) + '\n', 'utf-8')
                else:
                    app.import_chain(json.loads(Path(args.file).read_text('utf-8')))
                    p.save()
                result = {path: [e['type'] for e in p.chain_doc()['chains'][path]] for path in ('luminance', 'colour', 'final')}
                result['file'] = str(Path(args.file).resolve())
            else:
                geometry, layer, factor = app.export_options(p.data['geometry'], p.layers,
                                                             dict(coverage=args.coverage, layer=args.layer, factor=args.scale))
                result = app.get_scene().export(args.output, p.chain_doc(), geometry, p.layers, factor, layer,
                                                progress=lambda f: print(f'Export {f:.0%}', flush=True))
        print(json.dumps(result, indent=2, default=str))
        return 0
    except (ValueError, OSError, KeyError) as e:
        print(f'LunarMosaic: {e}', file=sys.stderr)
        return 1
    finally:
        app.close()


if __name__ == '__main__':
    raise SystemExit(main())
