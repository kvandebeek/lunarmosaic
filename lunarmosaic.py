#!/usr/bin/env python3
"""LunarMosaic desktop interface and reproducible LRGB project commands."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'mosaic'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    ui = sub.add_parser('app', help='open the local interface (default)')
    ui.add_argument('project', nargs='?')
    ui.add_argument('--port', type=int, default=8768)
    ui.add_argument('--no-browser', action='store_true')
    ui.add_argument('--history-file', default=str(Path.home()/'.lunarmosaic'/'recent-projects.json'),
                    help='local recent-project history file')
    ui.add_argument('--no-history', action='store_true', help='do not retain recent project paths')
    new = sub.add_parser('new', help='create a non-destructive LRGB project')
    new.add_argument('project')
    new.add_argument('--name', default='Untitled Moon')
    new.add_argument('--luminance', nargs='+', required=True)
    new.add_argument('--rgb', nargs='+')
    for name in ('build', 'match', 'check-match', 'north-up', 'status'):
        cmd = sub.add_parser(name)
        cmd.add_argument('project')
    export = sub.add_parser('export')
    export.add_argument('project')
    export.add_argument('-o', '--output', required=True)
    export.add_argument('--scale', type=float, choices=(1, .5, .25), default=1)
    export.add_argument('--layer', choices=('composite', 'luminance', 'colour'), default='composite')
    export.add_argument('--coverage', choices=('full', 'common'), help='override coverage for this export only')
    args = parser.parse_args()
    from app import Application, serve
    app = Application(history_path=getattr(args, 'history_file', str(Path.home()/'.lunarmosaic'/'recent-projects.json'))
                      if args.command in (None, 'app') and not getattr(args, 'no_history', False) else None)
    try:
        if args.command in (None, 'app'):
            if getattr(args, 'project', None):
                app.select(args.project)
            serve(app, getattr(args, 'port', 8768), not getattr(args, 'no_browser', False))
            return 0
        if args.command == 'new':
            app.select(args.project, create=True, name=args.name)
            app.inputs('L', args.luminance)
            if args.rgb:
                app.inputs('RGB', args.rgb)
            result = app.status()['project']
        else:
            app.select(args.project)
            if args.command == 'build':
                result = app.build(['L', 'RGB'])
                print(json.dumps(result, indent=2))
                return 1 if any(v != 'built' for v in result.values()) else 0
            elif args.command == 'match':
                result = app.match()
            elif args.command == 'check-match':
                result = app.check_alignment()
            elif args.command == 'north-up':
                result = app.north_up()
            elif args.command == 'status':
                result = app.status()['project']
            else:
                edits, layer, factor = app.export_options(app.project.data['edits'],
                    dict(coverage=args.coverage, layer=args.layer, factor=args.scale))
                result = app.get_scene().export(args.output, edits, factor, layer,
                                               progress=lambda f: print(f'Export {f:.0%}', flush=True))
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as e:
        print(f'LunarMosaic: {e}', file=sys.stderr)
        return 1
    finally:
        app.close()


if __name__ == '__main__':
    raise SystemExit(main())
