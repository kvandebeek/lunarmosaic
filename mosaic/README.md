# Lunar Mosaic tools

Build a sharp, seamless 16-bit mosaic of the Moon from overlapping stacked panels (AutoStakkert output, already
wavelet-sharpened), score the panels' quality, add finishing touches, and render explanatory movies (16:9 and 9:16) of
every step. Developed and measured on the 2026-09-25 full Moon (101 panels, Apple M4).

**Documentation**

| File | What it contains |
|---|---|
| this README | overview, workflow, outputs, movies, tips, limitations |
| [`docs/COMMANDS.md`](docs/COMMANDS.md) | **every command with all options and defaults** (generated from `--help`; refresh with `python3 tools/update_docs.py`) |

Every command also explains itself: `python3 <command> --help`.

---

## Requirements

- macOS on Apple silicon; Homebrew Python 3.14 with numpy and OpenCV (4.13). No other packages are required.
- Pillow (installed) is used for good-looking text in the preview and movies; without it, OpenCV's font is used.
- Fonts: Avenir Next and SF Mono (macOS system fonts).
- Disk: about 1.2 GB per 16-bit output TIFF; RAM: peak about 9 GB for a 200 Mpx mosaic.

## Folder layout

```
<LunarMosaic>/mosaic/
  panel_classifier.py        quality scoring (single-image and same-terrain)
  mosaic_builder.py          the mosaic, in nine steps, with preview window and movies
  mosaic_stages.py           steps 5–9 (fine alignment … finishing)       [module]
  mosaic_common.py           shared I/O, masks, metrics, feature matching [module]
  preview_style.py           the "Observatory" look of window and movies  [module]
  lunar_finish.py            phase 2: optional wavelets, highlights, lunar colour, contrast, optional unsharp mask, north up
  tools/compare_crops.py     same-terrain 100 % crops, blind tests
  tools/edge_falloff.py      detail vs distance from the frame edge (optics/tilt check)
  tools/update_docs.py       regenerates docs/COMMANDS.md
  design_mockups/            the chosen design (Observatory) and its renderer
  docs/                      COMMANDS.md
```

## Workflow

Run from the session folder, for example `<drive>/<date>/moon`, with the panels in `panels/`:

```sh
S=<LunarMosaic>/mosaic          # path to this repository

# 1. score the panels (≈ 2 min for 101 panels); the manifest is also used to pick what the movie shows
python3 $S/panel_classifier.py panels -o panels/panel_manifest.json --relative

# 2. build the mosaic (≈ 2.5 min; ≈ 3 min with both movies)
python3 $S/mosaic_builder.py panels -o mosaic.tif --record mosaic_build.mp4 --record-vertical mosaic_build_9x16.mp4

# 3. finishing touches (≈ 40 s; ≈ 1.5 min with both movies); never changes mosaic.tif
python3 $S/lunar_finish.py mosaic.tif -o mosaic_finished.tif --record finish.mp4 --record-vertical finish_9x16.mp4
#    add  --wavelets pixinsight  for unsharpened stacks (PixInsight ATrousWaveletTransform style, see below)
#    add  --usm "100;1,5;0"  for Photoshop-style sharpening (see "Sharpening" below)
#    the result is turned north up, east right as the last step (--keep-orientation to skip)
```

Defaults can be set per machine in LunarAtlas's `.env` (see its `.env.example`):
- **lunar_finish:** `LUNAR_FINISH_WAVELETS=pixinsight`, `LUNAR_FINISH_NORTH_UP`, highlights, saturation and contrast.
- **mosaic_builder:** `MOSAIC_MODEL`, `MOSAIC_WORKERS` and `MOSAIC_NORTH_UP`.

Every option with a sensible default is a setting (60 in all, with ranges: `python3 <LunarAtlas>/tool_settings.py --check`). Command-line options override `.env`. On/off options have both forms (`--preview` / `--no-preview`, `--display-tif` / `--no-display-tif`), and lunar_finish has `--north-up` and `--no-wavelets` to override its `.env` choices.

The tools never move, rename or modify input files: an output path (including the derived `_display.tif`, layout files,
movies and log) that names an input is refused before any work starts. Outputs are written under hidden temporary names
(`.NAME.partial.EXT`) and renamed into place only when the run succeeds, so an aborted or failed run leaves no partial
files and earlier results stay as they were (this needs free space for the new files next to the old ones).
All results are deterministic (identical input → identical output).

### 1. `panel_classifier.py`: panel quality

- Single-image metrics (Tenengrad, Laplacian variance) for any image set.
- `--relative`: finds overlapping panels and compares fine detail **on the same terrain**, at equal distance from both
  frame centres (detail falls off towards the frame edge), in two detail scales. This is the authoritative ranking
  (uncertainty about ±2 %).
- Outputs: `panel_manifest.json` (all metrics, tiers, pairs) and `panel_manifest.md` (a readable report).

### 2. `mosaic_builder.py`: the mosaic

| Step | On screen | What it does |
|---|---|---|
| 1 | Checking the inputs | headers, disk and RAM checks |
| 2 | Finding features | reads panels, measures and crops AutoStakkert's 2–6 px edge artefact, SIFT keypoints on 1/4 proxies |
| 3 | Matching overlaps | candidate pairs from one global feature index, RANSAC-verified |
| 4 | Placing panels | global similarity solve (reference fixed, consistency loop, layout closure) |
| 5 | Fine alignment | phase correlation on textured tiles at full resolution; a gentle cubic warp per panel (priors keep scale/rotation physical), then a smooth 256 px displacement mesh per panel fitted on dense 128 px tiles (`--model poly3+mesh`, the default; `--model poly3` skips it) |
| 6 | Matching brightness | gain + offset per panel and colour channel from shared terrain |
| 7 | Choosing the sharpest regions | per-region sharpest panel (with an edge taper); lower-quality panels add no detail where a better one covers the terrain |
| 8 | Blending | 7-level multi-band blend, each panel only inside its won region |
| 9 | Finishing | sky pedestal removed (also when the stacks carry a pedestal above 2 % of full scale), highlands colour-balanced, black space with a feathered limb, 16-bit uncompressed TIFF |

The mosaic keeps the camera's orientation; `lunar_finish.py` turns it north up as its last step. `--north-up` turns the
mosaic itself instead, folded into the panel poses (no extra resample).

Outputs next to `-o`:

| File | Content |
|---|---|
| `mosaic.tif` | the mosaic, 16-bit RGB, uncompressed, **unstretched master** (data as stacked: the Moon's brightest 0.1 % at about 56 % of full scale) |
| `mosaic_display.tif` | the same mosaic with **exactly the movie's tone mapping** (white point at the Moon's brightest 0.2 % in the movie frame, gamma 1.1); about 0.4 % of the Moon reaches full white. `--no-display-tif` skips it |
| `mosaic_contributions.png` | which file supplied which region: tinted regions, file names, shares of the disk |
| `mosaic_labels.png` | region map (16-bit PNG at 1/4 scale; index = `label_index` in the layout JSON) |
| `mosaic_layout.json` | poses, residuals, canvas, crops, dropped pairs, not-placed panels |
| `mosaic_layout.png`, `mosaic_layout_composite.png` | step-4 diagnostics (outlines by residual; mean of all panels) |
| `--log FILE` | the console log, including per-step timing |

Measured on the 2026-09-25 set (rebuilt 2026-09-28): 101/101 panels placed, alignment 0.54 px RMS (median 0.21 px) on the
fine-alignment tiles, brightness mismatch on shared terrain 1.06 % → 0.14 %, 87 panels contribute, 14792 × 13432 px,
3 min 48 s with both movies. At the real seams (3650 points) the mesh brings the misregistration median from 0.44 to
0.28 px, the share above 1 px from 15.2 % to 9.0 % and above 2 px from 3.5 % to 2.2 %, for about 44 s extra.

### 3. `lunar_finish.py`: finishing touches (phase 2)

Works in CIE Lab, so brightness and colour are adjusted independently:

0. **Wavelet sharpening** (optional, first): as PixInsight's ATrousWaveletTransform on lightness (CIE L*): dyadic
   layers of 1, 2, 4, 8 px, each weighted by 1 + bias. `--wavelets pixinsight` is the user's setting (4 layers, linear
   interpolation (3), layer 1 bias +3); `--wavelets 3,0.5,0,0` gives your own biases, `--wavelet-kernel b3` the B3-spline
   scaling function, and `--wavelet-denoise 1,0.5` protects against grain (coefficients within K × the layer's noise keep
   weight 1). Measured on the unsharpened 27 Sep 2026 mosaic: fine detail × 1.27, limb 7.3 → 5.5 px.
1. **Highlights** (`--highlights 0.3`): the brightest ~10 % of the disk is gently lowered on an edge-aware base layer;
   the fine detail is added back unchanged (no halos).
2. **Lunar colour** (`--saturation 2`, `--saturation-passes 2`, `--chroma-denoise 3`): the image is split into brightness
   (all the detail, untouched) and colour, which is blurred by 3 px, the same as a luminance layer coloured by a
   Gaussian-blurred colour layer in Photoshop's Color blend mode. Measured: colour noise −31 % (mare) to −40 % (bright
   rays) against 1.2 px, with brightness detail unchanged. Then saturation × 2 per pass on
   the Moon's own hues (blue, orange–yellow). Greens, purples and pinks get 15 % of the boost, and there is no boost
   within 80 px of the limb (`--limb-taper`), where residual colour fringing lives.
3. **Contrast** (`--contrast 0.12`, 0 to < 0.5): a gentle S-curve on brightness; black and white stay fixed. Above 0.5 the
   curve would fall at the ends, so larger values are refused. 8-bit input gives the same 16-bit output.
4. **Sharpening** (optional, `--usm AMOUNT,RADIUS,THRESHOLD`): see below.
5. **North up** (default; `--keep-orientation` skips it): moon_atlas locates the finished image (a close-up mosaic by the
   blind close-up search, from its panels' capture times) and turns it lunar north up, east right, never mirrored, in
   one Lanczos resample (fine detail kept 99.4–99.9 %), cropped to the Moon. In the movie the disk turns about its own
   centre, the short way round; a mirrored image flips over.

Outputs: `mosaic_finished.tif` (the finished result, same brightness scale as the master) and
`mosaic_finished_display.tif` (the finished result with **exactly the finishing movie's tone mapping**: white point from
the master's brightest 0.01 %, gamma 1.1; about 0.03 % of the Moon at full white). `--no-display-tif` skips the latter.

### Sharpening: how Photoshop's Unsharp Mask works, and how `--usm` reproduces it

- Photoshop: result = original + **Amount** × (original − Gaussian blur). **Radius** is the Gaussian's standard deviation
  σ in pixels. **Threshold** (0–255 levels) leaves pixels alone whose difference from the blurred copy is smaller; 0 means
  everything is sharpened. In an RGB image Photoshop sharpens each colour channel separately, which can create colour
  fringes; Edit → Fade → Luminosity avoids that.
- `--usm 100,1.5,0` (or with a decimal comma: `--usm "100;1,5;0"`) applies exactly this formula. `--usm-mode luminosity`
  (default) sharpens brightness only; `--usm-mode rgb` sharpens each channel, as Photoshop does in RGB mode.
- Measured on this mosaic, which is already wavelet-sharpened: 100 % / 1.5 px / 0 roughly **doubles the grain in smooth
  mare areas** (grain 969 → 1927). Threshold 3 hardly helps (1875), threshold 6 a little (1636), because the grain
  itself is about 7 levels strong. Sharpening is therefore **off by default**. If you use it, do it here *or* in
  Photoshop, not both.

## Movies

Both tools render movies with `--record` (16:9, 1920×1080) and `--record-vertical` (9:16, 1080×1920, for Instagram
Reels / Stories), at 30 fps, in the "Observatory" style: the image fills the frame on black, centred captions, amber
accents, a natural tone rendering (never the detector's contrast-enhanced proxies). The movies work without a display.

**Why the movies look brighter than `mosaic.tif`:** the stacks use only part of the 16-bit range (the Moon's brightest
0.1 % at about 56 %), and the movies stretch that for the screen (white point + gamma 1.1). The `_display.tif` files
carry exactly that stretch (medians within 3–4 % of the movie frames; the rest is video encoding and the frame's slight
vignette), while `mosaic.tif` and `mosaic_finished.tif` keep the full headroom for further processing.

**Build movie (60.0 s):** opening 2 s · 1 inputs 2 s (contact sheet of all panels: rows balanced for the frame, each row centred, panels in their own shape) · 2 features 7.2 s (two panels:
features appear small → large 1.8 s, hold 0.7 s, fade to black 0.5 s) · 3 overlaps 9 s (9 pairs: keypoints → matches →
overlap zone) · 4 placing 8 s · 5–9 5 s each · final shot 4 s (crossfade from the contributions map) · fade-out to
black filling up to 60 s. Change the timings at the top of `mosaic_builder.py` (`VIDEO_SECONDS`, `FEATURE_*_SECONDS`,
`PAIR_SHOWCASE`, `MOVIE_TARGET_SECONDS`); every run prints the measured seconds per step.

**Finishing movie (21 s; about 26 s with sharpening):** opening, then per adjustment a title card, a before/after wipe and a
short hold, ending in a crossfade to the finished image. Timings: `MOVIE` at the top of `lunar_finish.py`.

**Preview window** (both tools, unless `--no-preview`): `p` pause/resume · `q` abort cleanly (no output is written) ·
`1`/`2` switch views where offered · any key closes the window at the end.

## Diagnostics

```sh
python3 $S/tools/compare_crops.py panels/panel_manifest.json --pair 2103_9,2109_2 -o compare.png   # same terrain, 100 %
python3 $S/tools/compare_crops.py panels/panel_manifest.json --blind 6 -o blind/                   # judge first, then key.json
python3 $S/tools/edge_falloff.py panels/panel_manifest.json --by side                              # optics / tilt
```

## Known limitations

- Grain and diagonal streaks in a panel can be scored as detail; in a blind check 5 of 6 pairs agreed with the
  ranking, and the one miss was a streaky panel.
- The alignment's remaining error (0.54 px RMS, median 0.21 px) is dominated by tile noise between differently blurred
  panels and seeing warps below the mesh's 256 px spacing. It does not show at the seams.
- A thin colour fringe along the limb comes from the stacks; phase 2 protects it from extra saturation but does not
  remove it.

## Maintaining the documentation

After changing any option: `python3 tools/update_docs.py` (rewrites `docs/COMMANDS.md`).
