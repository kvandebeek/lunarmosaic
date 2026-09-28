"""Locate the LunarAtlas checkout, which supplies `tool_settings` (the shared settings registry and `.env`)
and the `moon_atlas` package (used for `--north-up` and for locating close-up mosaics).

Importing this module puts that checkout on `sys.path`, so `import tool_settings` works afterwards.
`MOON_ATLAS` is the path to add when the moon_atlas modules themselves are needed.

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
            and os.path.isdir(os.path.join(path, 'moon_atlas')))


def find():
    for c in _candidates():
        if _is_atlas(c):
            return c
    raise SystemExit(
        'cannot find the LunarAtlas checkout (it supplies tool_settings.py and moon_atlas/).\n'
        f'Looked in: {", ".join(_candidates())}\n'
        f'Clone it next to this repository, or set {ENV_VAR} to its path.')


ROOT = find()
MOON_ATLAS = os.path.join(ROOT, 'moon_atlas')
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
