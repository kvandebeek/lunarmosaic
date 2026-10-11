"""PyInstaller spec for the standalone LunarMosaic app."""
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
MOSAIC = os.path.join(ROOT, 'mosaic')
ATLAS = os.path.join(ROOT, 'moon_atlas')

a = Analysis(
    [os.path.join(SPECPATH, 'launcher.py')],
    pathex=[ROOT, MOSAIC, ATLAS],
    datas=[
        (os.path.join(MOSAIC, 'ui'), 'mosaic/ui'),
        (os.path.join(ATLAS, 'viewer'), 'moon_atlas/viewer'),
        (os.path.join(ATLAS, 'fonts'), 'moon_atlas/fonts'),
    ],
    hiddenimports=['app', 'project', 'lrgb', 'chain', 'blocks', 'filters', 'measurements', 'mosaic_common',
                   'atlas_view', 'atlas_app', 'atlas_closeup', 'atlas_ephem', 'atlas_geo', 'atlas_names',
                   'atlas_quality', 'atlas_render', 'atlas_paths', 'tool_settings'],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'IPython', 'pytest', 'unittest.mock'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LunarMosaic', console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='LunarMosaic', upx=False)

if sys.platform == 'darwin':
    icon = os.path.join(SPECPATH, 'icon', 'lunaratlas-macos.icns')
    app = BUNDLE(coll, name='LunarMosaic.app', bundle_identifier='io.github.kvandebeek.lunarmosaic',
                 icon=icon,
                 info_plist={'CFBundleDisplayName': 'LunarMosaic', 'CFBundleShortVersionString': os.environ.get('LUNARMOSAIC_VERSION', '0.1.0'),
                             'LSMinimumSystemVersion': '12.0', 'NSHighResolutionCapable': True})
