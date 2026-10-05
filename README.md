# LunarMosaic

Build a sharp, seamless gigapixel mosaic of the Moon from overlapping stacked panels (AutoStakkert
output), score the panels' quality, add finishing touches, and render explanatory movies of every step.

```sh
python3 mosaic/panel_classifier.py panels -o panels/panel_manifest.json --relative
python3 mosaic/mosaic_builder.py   panels -o mosaic.tif --record build.mp4
python3 mosaic/lunar_finish.py     mosaic.tif -o mosaic_finished.tif --record finish.mp4
```

Measured on a 101-panel full Moon (Apple M4): all 101 panels placed at 0.54 px RMS, brightness mismatch
on shared terrain cut from 1.06 % to 0.14 %, 84 panels contributing to a 14792 x 13432 px result, in
under 4 minutes with both movies.

What makes it more than a stitcher: panels are ranked by comparing them **on the same terrain at equal
distance from both frame centres**, and then, for every region of the disk, only the sharpest panel
covering that terrain is used. A weaker panel adds nothing where a better one already covers the ground.

See [`mosaic/README.md`](mosaic/README.md) for the nine build steps, every output file, the finishing
stages and the movies, and [`mosaic/docs/COMMANDS.md`](mosaic/docs/COMMANDS.md) for every option.

## Install and requirements

macOS, Linux or Windows. Python 3.14 with numpy, OpenCV, Pillow and tifffile. Developed and measured on macOS
(Apple silicon); Linux and Windows should work but are untested, so do tell me if they don't.

```sh
git clone https://github.com/kvandebeek/lunarmosaic.git
cd lunarmosaic
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

About 1.2 GB of disk per 16-bit output TIFF;
peak RAM around 9 GB for a 200 Mpx mosaic.

## The LRGB application

```sh
.venv/bin/python lunarmosaic.py
```

Create a project and import the panel sets: any number of luminance sets, from different cameras, Barlows and
pixel scales, and an RGB colour set. The luminance sets build into one mosaic on the finest set's pixel grid,
each region taken from the set that resolves it best. The colour panels are then placed one by one onto that
mosaic, each with its own pose, checked on tiles it was not fitted on. Edit the result as chains of blocks that
reproduce PixInsight (à-trous wavelets, ColorCalibration), Photoshop (Unsharp Mask, Levels, Curves) and
LuckyStackWorker (deconvolution), and judge them in named previews at 1:1. Export the combined image or each layer
as 16-bit TIFF/PNG or 8-bit JPEG, and the chain itself, which `lunar_finish.py --chain` also runs.

Everything runs locally in your browser, on LunarAtlas's bundled theme and fonts; no separate LunarAtlas
installation is needed. Edits save themselves; original panels and built mosaics stay untouched. Every step is also
a command: `lunarmosaic.py new`, `build`, `chain export`, `export` and the rest.

See [the LRGB guide](mosaic/docs/LRGB.md) for the workflow, the project format, the commands and current limits,
and [DESIGN.md](mosaic/docs/DESIGN.md) for the design and its measurements. Colour placement is measured on
synthetic sets and on one night's real colour panels (0.58 px held-out rms). Independent mono and colour capture
sets still need real-world validation, especially when seeing or optical distortion differs between them.

On Windows, use `.venv\Scripts\python` instead of `.venv/bin/python`.

## Self-contained tools

The mosaic commands, their settings registry (`tool_settings.py`), and a copy of LunarAtlas's current
runtime (`moon_atlas/`) are all in this repository. No separate LunarAtlas checkout is required.
The positioning code supplies the north-up step. The full atlas can also locate, view, and annotate
images through `python3 moon_atlas/lunaratlas.py app` or its command-line options. See
[`moon_atlas/README.md`](moon_atlas/README.md) and [`moon_atlas/UPSTREAM.md`](moon_atlas/UPSTREAM.md).

Lunar reference maps, elevation data, and the IAU catalogue are downloaded into `moon_atlas/data/`
on first use and are not committed to Git. The standard fonts are bundled. An existing data cache can
be copied into that directory for offline use.

## Settings

Defaults live in this repository's `.env`; copy `.env.example` to `.env` to customize them. Command-line
options always override them, and `--help` shows the value in effect (COMMANDS.md shows the built-in defaults). Check the active settings with
`python3 tool_settings.py --check`. LunarAtlas's observing site and equipment now live in its Settings
page or `equipment.json`, as described in `moon_atlas/README.md`.

## Licence

[Apache License 2.0](LICENSE). Use it, change it, build on it, commercially or not; keep the copyright
notice and say where it came from.

If it saved you time, [sponsoring](https://github.com/sponsors/kvandebeek) is welcome but never expected.
