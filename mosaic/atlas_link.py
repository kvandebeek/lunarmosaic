"""Locate the LunarAtlas checkout, which supplies `tool_settings` (the shared settings registry and `.env`)
and the `lunaratlas` package (used for `--north-up` and for locating close-up mosaics).

Importing this module puts that checkout on `sys.path`, so `import tool_settings` works afterwards.
`MOON_ATLAS` is the path to add when the lunaratlas modules themselves (atlas_geo, atlas_closeup, ...) are
needed; the name predates a rename of that package from `moon_atlas` to `lunaratlas` and is kept so callers
don't need to change.

Where it looks, in order:
  1. $LUNARATLAS_PATH
  2. a sibling of this repository named LunarAtlas / lunar-atlas / lunaratlas
  3. this repository itself (a single combined checkout still works)
"""
import os
import sys

ENV_VAR = 'LUNARATLAS_PATH'
_HERE = os.path.dirname(os.path.abspath(__file__))          # <repo>/mosaic
_REPO = os.path.dirname(_HERE)                              # <repo>
_PACKAGE = 'lunaratlas'                                      # the subfolder holding atlas_geo.py etc.


def _candidates():
    p = os.environ.get(ENV_VAR)
    if p:
        yield os.path.abspath(os.path.expanduser(p))
    parent = os.path.dirname(_REPO)
    for name in ('LunarAtlas', 'lunar-atlas', 'lunaratlas'):
        yield os.path.join(parent, name)
    yield _REPO


def _is_atlas(path):
    return (os.path.isfile(os.path.join(path, 'tool_settings.py'))
            and os.path.isfile(os.path.join(path, _PACKAGE, 'atlas_geo.py')))


def find():
    for c in _candidates():
        if _is_atlas(c):
            return c
    raise SystemExit(
        f'cannot find the LunarAtlas checkout (it supplies tool_settings.py and {_PACKAGE}/).\n'
        f'Looked in: {", ".join(_candidates())}\n'
        f'Clone it next to this repository, or set {ENV_VAR} to its path.')


ROOT = find()
MOON_ATLAS = os.path.join(ROOT, _PACKAGE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
