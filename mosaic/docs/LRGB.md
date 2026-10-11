# Luminance + colour projects

`lunarmosaic.py` builds one lunar image from panel sets of different cameras and scales: the detail from the
luminance sets, the colour from an RGB set, edited as chains of blocks and checked in previews at 1:1. Launch the
interface with `python3 lunarmosaic.py` (or `python3 lunarmosaic.py Moon.lunarmosaic` to open a project). It runs on
localhost with LunarAtlas's bundled theme and fonts; no sibling repository is needed, and no photos are sent to an
online service. [DESIGN.md](DESIGN.md) is the design record behind this guide.

## Workflow

1. **Create or open a project**: a folder such as `Moon.lunarmosaic`. Projects from earlier versions are converted
   once on opening; the original file is kept as `project.v<N>.json`, and what changed is listed once in the
   workspace.
2. **Import the panel sets.** The luminance stream takes any number of sets — one per camera and optical setup —
   and the colour stream one RGB set. Per set: a name, the optics (pixel size, focal length, Barlow, aperture) and
   its panels. A local folder path references the originals without copying them; the browser's folder picker
   copies the images into the project's `sources/`. TIFF, PNG and JPEG are read (8- or 16-bit, mono or RGB); FITS
   is not. Unreadable or unsuitable files are listed with the reason.
   - Sets of different pixel scales need their scale: from the optics, or **Measure the scale** (matched panels
     against another set) and then **Confirm**. A mixed build never runs on an unconfirmed guess.
   - The page states the colour/luminance sampling in words ("The colour panels sample 2.0× coarser than the
     luminance") and a lower bound for the canvas: the Moon at perigee at the finest set's scale. Above 400 Mpx the
     build waits for a confirmed size or a smaller canvas; when the finest set's pixels are much smaller than the
     detail it resolves, a smaller canvas that keeps all of it is offered.
   - A colour set that is all the data can feed both streams ("build them as the luminance too").
3. **Build.** The luminance sets build into one mosaic on the finest set's pixel grid, in the builder's nine steps.
   Where sets overlap, tiers ordered by measured resolution decide: the best-resolving set wins wherever it has
   data. The build card lists any panel left out, with the reason. Colour is then placed **panel by panel** onto
   the luminance: each panel is found (globally, then on tiles), given the simplest pose its held-out tiles
   justify (similarity, affine or a cubic warp), corrected for a sub-pixel red/blue shift when two estimators
   agree, matched in brightness, and blended into a colour canvas at the colour data's own sampling.
4. **Check the match.** The status chip reads, for example, "Match 0.28 px · worst 0.80 px · checked 0.07 px": held-out
   tile residuals in canvas pixels, then an independent check of overlapping patches. Its popover lists every
   colour panel: placed or the reason not, pose model, tiles, held-out residual, and the red/blue decision. The
   `errors` view marks the measured places; the red/cyan `overlay` view is for inspection at 1:1. **Place colour
   again** re-runs the placement; a single-image colour set can instead be placed from three or more control
   points.
5. **Edit the chains.** The luminance and colour chains run before the merge, the final chain after it; the merge
   takes lightness from the luminance and colour from the colour chain, at the colour strength set there. **+ Add a
   block** offers levels, curves, highlights, contrast, à-trous wavelets, unsharp mask, Gaussian blur,
   deconvolution, white balance, sub-pixel RGB shift and saturation; a block a chain cannot take is greyed with the
   reason. Parameters are in the units of the program each block reproduces: PixInsight biases with the gain
   beside each, Photoshop percent, pixels and levels, Curves over the histogram arriving at the block. Blocks are
   reordered by dragging the grip or with Alt+↑/↓, switched off, duplicated and deleted; every block can fade
   towards the limb. Readouts below each block come from the image itself: the wavelet layer the detail reaches,
   white-balance gains, every RGB-shift estimate, the deconvolution's first dark ring. White-balance references
   can be drawn on the mosaic. **Keep space black** restores the sky after every block.
6. **Preview at 1:1.** Press **P** (or ▭ Preview) and drag a rectangle: it is named, saved with the project, and
   rendered at 1:1 with the whole chain. Every change renders the previews again; they follow a turn of the view.
   Below 75 % zoom the canvas itself is approximate (each block at reduced scale) and says so; judge detail in a
   preview.
7. **Canvas and orientation.** Coverage is the full luminance canvas (colour gaps stay grey) or the largest
   rectangle covered by both layers. Rotation and mirroring turn both layers together; **Find lunar north** solves
   the orientation from the limb or, for a close-up, from terrain and the panels' capture time, with LunarAtlas's
   bundled reference maps. The `resolution` view tints each region by the luminance set that supplied it, with a
   legend of tiers, scales and shares of the disk.
   After building, **Open in LunarAtlas** writes a new TIFF beside the original luminance panels (or in the project
   folder when the sources have no common folder) and opens the bundled LunarAtlas viewer. LunarAtlas keeps its
   annotation sidecar and annotated exports beside that handoff image; source panels and the LunarMosaic project
   remain unchanged. Existing handoff files are never replaced.
8. **Export** a 16-bit TIFF or PNG, or an 8-bit JPEG (quality 92), at native, half or quarter size; the combined
   image or one layer on the same canvas. Every block runs at full resolution. The chain can be saved as a file —
   for another project, or for `lunar_finish.py --chain` — and loaded into a project, which keeps its own
   previews.

Edits save themselves 300 ms after the last change, each with the project revision it was made on. If another
window changed the project meanwhile, or the connection drops, the edit stays on screen and is sent again, and the
header says so. A chain the server refuses (an impossible combination of values) stays on screen with the reason
until the next edit.

## Project contract

`project.json` (schema 4) holds the sets (`L`, `RGB`, then `s2`, `s3`… for further luminance sets, each with its
stream, name, inputs, optics and any measured scale), the build state of each stream, the colour placement, the
geometry (rotation, mirror, coverage) and view (layer visibility), the chain (`protect_space`, the chains, the
preview rectangles) and the migration notes. Saves are atomic and a process lease keeps a second process out; a
stale browser revision cannot overwrite newer edits. No credentials are stored.

`derived/L.tif` is the luminance mosaic, with the builder's layout, label, contribution and set reports beside it.
Each successful colour placement writes an immutable generation-specific `derived/colour_canvas.<generation>.tif`
(16-bit RGBA, alpha = coverage) and matching `colour_place.<generation>.json`; `project.json` atomically selects
the complete pair. Original files are never written. Direct source paths must still exist to rebuild; finished mosaics
can be edited and exported after the panels have moved. Completed builds are reused on reopening; an interrupted
build restarts from its first step (there is no resume from intermediate stages yet).

## Commands on the same project

```sh
python3 lunarmosaic.py new Moon.lunarmosaic --name 'My Moon' \
    --luminance /data/ir --optics 2.9,2000 --label 'C14 + IR' \
    --luminance /data/wide --optics 2.9,1000 \
    --colour /data/rgb --optics 3.76,1000
python3 lunarmosaic.py scales Moon.lunarmosaic             # sets without optics: measure, then --confirm
python3 lunarmosaic.py build Moon.lunarmosaic              # luminance, then the colour placed onto it
python3 lunarmosaic.py check-match Moon.lunarmosaic
python3 lunarmosaic.py north-up Moon.lunarmosaic
python3 lunarmosaic.py chain export Moon.lunarmosaic moon.chain.json
python3 lunarmosaic.py chain import Moon.lunarmosaic moon.chain.json
python3 lunarmosaic.py export Moon.lunarmosaic -o Moon-LRGB.tif
python3 lunarmosaic.py export Moon.lunarmosaic -o Moon-colour.tif --layer colour --scale 0.5
python3 lunarmosaic.py status Moon.lunarmosaic
```

Each `--optics` (pixel µm, focal mm, then optionally Barlow and aperture mm; `"2,9;2000"` also works) and `--label`
belongs to the `--luminance` or `--colour` group before it. `build` accepts `--max-canvas-mpx` and `--canvas-scale`
as confirmed on the import page. Results print as JSON; a build shows its steps as it runs. Every option is in
[COMMANDS.md](COMMANDS.md). Close a project in the interface before changing it from another process.

## Rendering and limits

Colour is merged in CIE Lab, converted exactly: L* from the luminance chain, a*/b* from the colour chain, scaled by
the colour strength and the colour coverage. The chains run on the luminance canvas; rotation and mirroring follow
with Lanczos-4. Statistics a block needs (histograms, noise, white-balance references) are measured once per chain,
on a quarter-scale proxy and a full-resolution crop of the disk, and a change recomputes them only from the
changed block on. A preview equals the same pixels of a full export, and a half-size export is the averaged full
one.

Native uncompressed TIFFs are memory-mapped and exports are written in tiles; an existing file is never
overwritten, and a cancelled export publishes nothing. JPEG is limited to 80 million pixels and 65,000 pixels a
side. The colour canvas is built in memory: about 6.7 GB at 200 Mpx when colour and luminance share a scale.
Not yet: FITS input, resuming a build from intermediate stages, the daylight sky model (DESIGN.md §8).

## Verification

```sh
python3 mosaic/tests/run_all.py
```

Among the suites: `ui_journey.py` drives the interface in headless Chrome through the whole workflow on synthetic
sets (two luminance sets at different scales and a colour set: import, build, chip and per-panel table, blocks,
previews, rotation, reload, chain file, export, reopen; no console errors). `cli_checks.py` runs every command in
COMMANDS.md on synthetic data. `colour_checks.py` measures placement against the truth, `mixed_checks.py`
mixed-scale builds, and `scene_checks.py` the renderer's geometry and the routes. Real measurements are in
DESIGN.md §9.

## Reopening and local history

Completed projects open in the workspace; interrupted, failed or missing builds open on the build page, which names
the saved mosaics and the last recorded step. The recent-project list keeps up to 12 folder paths and the time
each was last opened, in `~/.lunarmosaic/recent-projects.json`, and reads current names and states from the projects
without opening them for writing. No photos, credentials or observing-site fields are copied into it. **Clear
history** removes only these references; `--no-history` disables it and `--history-file PATH` moves it. The
processing commands keep no history.

## Layers

Both layer rows have thumbnails and saved visibility switches. Colour off gives the luminance alone; luminance off
uses the colour's own lightness. Both off leaves the combined image empty; enable a layer before exporting it.
Exporting a single layer ignores the switches. The view rail (composite, luminance, colour, overlay, coverage,
errors, resolution) changes what the canvas shows, not the switches.
