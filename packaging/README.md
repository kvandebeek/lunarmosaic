# LunarMosaic app

Build on macOS with the repository's Python environment:

```sh
python3 -m pip install pyinstaller
python3 -m PyInstaller --clean packaging/lunarmosaic.spec
```

The result is `dist/LunarMosaic/LunarMosaic.app` on macOS. The app runs the local browser interface and bundles the
LunarAtlas runtime used for north-up calculations. The **Open in LunarAtlas** action hands the exported TIFF to the
installed `LunarAtlas.app` using its `--ingest` protocol; it does not depend on a checkout of LunarAtlas.
