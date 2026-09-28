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

## Requirements

macOS, Linux or Windows. Python 3.14 with numpy, OpenCV and Pillow. Developed and measured on macOS
(Apple silicon); Linux and Windows should work but are untested, so do tell me if they don't.

About 1.2 GB of disk per 16-bit output TIFF;
peak RAM around 9 GB for a 200 Mpx mosaic.

## LunarAtlas

This repository depends on [LunarAtlas](https://github.com/kvandebeek/lunaratlas), which supplies the shared
settings registry (`tool_settings.py`, `.env`) and the positioning used to turn a finished mosaic north
up. Clone it next to this one:

```sh
git clone https://github.com/kvandebeek/lunaratlas.git
git clone https://github.com/kvandebeek/lunarmosaic.git
```

`mosaic/atlas_link.py` finds it automatically as a sibling directory. If you keep it somewhere else,
set `LUNARATLAS_PATH` to its path.

## Settings

Defaults live in LunarAtlas's `.env`; see its README. Command-line options always override them, and
`--help` shows the value in effect.

## Licence

[Apache License 2.0](LICENSE). Use it, change it, build on it, commercially or not; keep the copyright
notice and say where it came from.

If it saved you time, [sponsoring](https://github.com/sponsors/kvandebeek) is welcome but never expected.
