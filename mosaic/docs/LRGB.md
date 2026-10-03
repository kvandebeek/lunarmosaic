# Luminance + colour projects

Launch with `python3 lunarmosaic.py app`. The interface runs on localhost and uses the unchanged
theme, viewer panel styles and IBM Plex Sans fonts bundled from LunarAtlas. No sibling repository
is needed, and no photos are sent to an online service.

## Workflow

1. Create a new project folder (for example `Moon.lunarmosaic`) or open an existing one.
2. Select a folder of L panels and optionally an RGB folder. A direct local path references the
   originals without copying them. The browser folder picker copies selected images into the
   project's `sources/` directory. TIFF, PNG and JPEG are supported; FITS is not currently supported.
   Direct folder imports keep readable, suitable files and list rejected files with their reasons.
   Pixel dimensions and detected data types are shown after header inspection; 8-bit panels remain
   valid inputs. Optional optics show the estimated RGB/L sampling ratio.
3. Build each mosaic at its native resolution. Sets run sequentially to limit peak RAM. A successful
   mosaic stays on disk if the other set fails. Logs and nine build stages report real progress.
   Build cards also show elapsed time and the layout report's placed/contributing counts.
   Disconnected panels that the builder omits are listed with their actual reasons.
4. Match RGB onto L with a similarity transform (scale, rotation, translation). This supports different
   sensors and Barlow factors. SIFT features are matched mutually and fitted with RANSAC. Fits with
   too few points, too many outliers, or inadequate spatial spread are rejected. The reported median,
   p95 and worst fit residuals are **fitted feature residuals in native L pixels**. After fitting,
   separate local phase-correlation/ECC checks measure overlapping patches without changing the
   transform. The status chip reports those checks, including unknown regions in its details.
   Filled residual dots show measured locations; hollow grey rings mark unknown patches. These
   checks use a reduced image and report distances in native L pixels.
5. Inspect the red/cyan overlay at 1:1. Zoom and pan load actual native-resolution samples, rather than
   enlarging only a preview. If automatic matching fails, choose at least three corresponding points
   in the two mosaics. Independent patch checks also run after a manual fit. Areas outside
   successful checks remain unknown; a good global fit does not establish perfect alignment.
6. Adjust the layers: colour strength, saturation and chroma smoothing on RGB; highlights and contrast
   on L. Advanced L-only wavelets and unsharp masking are off by default. The four wavelet
   bands reuse the finishing CLI kernels, with grain protection. Inspect sharpening at 1:1;
   smaller previews approximate the filters. The sampled fine-scale variation indicator includes
   both detail and grain, so it is not a pure noise measurement. Select the full L canvas with grey colour gaps, or the largest conservative rectangular area
   with coverage in both layers. Internal holes participate in that crop calculation. Coverage uses
   the builder's label maps where available; one-panel images use their rectangular bounds.
7. Rotate or mirror both layers together if needed. “Find lunar north” runs the bundled
   LunarAtlas solver on L and applies one shared reversible transform. It uses the lunar limb
   or close-up terrain with capture time from panel filenames; optional project optics provide
   a prior. Reference maps may download on first use. Failure or cancellation preserves the view. Export 16-bit uncompressed TIFF, streaming 16-bit PNG, or JPEG (8-bit,
   quality 92). Native, half and quarter size are available. Individual layer exports share the same
   canvas and transform. Exports contain no observing-site or source metadata.

## Project contract

`project.json` schema version 3 stores a project identifier, revisions, panel paths, optional camera
sampling inputs, per-set build states, native TIFF paths, the RGB-to-L matrix, measured points and
layer adjustments and the north-up solution. Version 1 and 2 projects migrate automatically, retaining existing adjustments and enabling
both visibility switches. Version 1 also gains disabled sharpening defaults. Atomic replacement and a process lease protect saves. A stale browser revision
cannot overwrite newer edits. No credentials are stored in a project.

`derived/L.tif` and `derived/RGB.tif` retain the native builds. The existing builder also writes layout,
label and contribution files beside them. Single-panel sets are retained as native-size 16-bit RGB
TIFFs. Original files are never written. Direct source paths must still exist to restart a build;
finished mosaics can be edited and exported after the source panels have moved.

Completed builds are reused on reopening. There is **no intermediate-stage resume**: an interrupted
build is marked interrupted and restarts from the beginning. Native mosaics are independent of edit
sliders. A mono-only project works, and RGB can be added later without rebuilding L.

## CLI using the same project

```sh
python3 lunarmosaic.py new Moon.lunarmosaic --name 'My Moon' --luminance /data/L --rgb /data/RGB
python3 lunarmosaic.py build Moon.lunarmosaic
python3 lunarmosaic.py match Moon.lunarmosaic
python3 lunarmosaic.py check-match Moon.lunarmosaic
python3 lunarmosaic.py north-up Moon.lunarmosaic
python3 lunarmosaic.py app Moon.lunarmosaic
python3 lunarmosaic.py export Moon.lunarmosaic -o Moon-LRGB.tif
python3 lunarmosaic.py export Moon.lunarmosaic -o Moon-colour.tif --layer colour --scale 0.5
python3 lunarmosaic.py status Moon.lunarmosaic
```

Click the application name to open/create another project or close the current project. Close it
before writing to it from another process. The export dialog previews the selected layer and coverage, displays dimensions and approximate
uncompressed TIFF size, and warns when JPEG is too large. Coverage selected there applies to that
export only. The CLI's `--coverage full|common` does the same. Existing export files are
never overwritten; choose a new name. Cancelled exports do not publish a partial image.

## Rendering and limits

L supplies perceptual lightness (CIE Lab L*), RGB supplies a*/b*. Colour strength scales chroma toward
neutral, with mask-normalized Gaussian chroma smoothing. Saturation is a plain chroma multiplier.
Colour strength, saturation and smoothing are separate LRGB controls, rather than aliases for
the finishing CLI's selective saturation. Advanced wavelets reuse its kernels and sharpening; USM
also operates only on L. Exports sharpen native L before resampling onto the shared canvas. RGB colours outside the output gamut are clipped, so extreme saturation can change apparent
lightness. Layer source pixels remain intact. This is not a promise of Photoshop blend-mode parity.

Native uncompressed interleaved TIFFs are memory mapped. Export renders tiles with blur halos and
writes TIFF/PNG incrementally. Compressed/non-mappable inputs may require full decoding. JPEG is
limited to 80 million pixels and 65,000 pixels per dimension; TIFF/PNG are preferable for large work.
Processing retains the existing mosaic builder's RAM requirements. Automatic cross-layer local mesh
warping, FITS import, intermediate stage caches and native packaging remain
follow-up work. A global fit cannot correct differential
seeing or lens distortion. No perfect alignment is asserted for different cameras.

## Verification

```sh
python3 mosaic/tests/lrgb_workflow.py
python3 mosaic/tests/review_regressions.py
```

Workflow checks cover a known 2.29× mixed-scale transform with rotation/translation, grey gaps and
internal-hole cropping, L-detail retention with chroma smoothing, 16-bit export and shared rotation,
cancelled/duplicate outputs, per-set failures, source preservation, reopening, process leases,
stale-edit rejection, HTTP origin/auth checks and native-resolution viewport rendering. Real
independent camera capture sets are needed for imaging acceptance beyond these checks.

The current macOS verification passed 24 LRGB workflow checks and all 13 existing mosaic review
regressions. A browser journey built four luminance panels and two half-scale colour panels derived
from one real lunar frame, fitted a 2.000× transform (1,667 inliers; median 0.17 L px), edited colour,
exported a 16-bit TIFF, reopened the saved adjustments, and selected a common rectangle with no
missing colour in the preview. A 16-bit PNG export through the CLI also completed. This derived
fixture exercises the application; it is not independent-camera imaging acceptance. Linux and
Windows UI journeys have not been run.

Additional checks cover version 1 migration, independent detection of a locally shifted region,
shared mirroring and sharpening across rotated export tile boundaries. Browser verification applied
advanced sharpening, inspected native detail and exported a TIFF. North-up completed on a real
lunar frame with 310 terrain matches and a reported 1.23 native-pixel residual; its rotated PNG
export was visually inspected. These results still do not establish independent-camera acceptance.

See [design decisions](DESIGN_DECISIONS.md) for the implementation choices and their rationale.

## Reopening and local history

Completed projects open directly in the editor. Interrupted, failed or missing sets open on the build
page, where the recovery card names saved mosaics and the last recorded stage. Restart begins from
stage 1; the interface does not imply intermediate stage caching. If colour is missing, a saved common
crop is reset to full coverage so retained luminance remains viewable.

The application's recent-project list stores up to 12 folder paths and last-opened timestamps in
`~/.lunarmosaic/recent-projects.json`. It reads current names/status and built TIFF sizes from those
projects without opening them for writing. No photos, credentials or observing-site fields are copied
into history. Clear history removes only these references. Use `app --no-history` to disable it or
`app --history-file /path/to/recent.json` to choose its location. CLI processing commands do not retain
history. Unavailable projects stay listed rather than being silently removed.

Additional macOS browser verification covered interrupted recovery, reopening from recent projects,
contribution counts, a changing export preview, and an export-only full-canvas override. The exported
half-size TIFF was 592 × 324 pixels, uint16; the project retained its common-coverage adjustment.

## Layer visibility and match explanation

Both rows have real native-mosaic thumbnails and saved visibility switches. The rows stay visible
while adjustments scroll beneath them. RGB off gives L-only lightness; L off uses the matched RGB
mosaic's own lightness and chroma on the same canvas. Colour strength, saturation and smoothing
remain available. L-only sharpening/brightness controls do not affect the RGB-only result. Both off
gives an empty combined preview; enable a layer before exporting a combined image. Explicit
individual layer exports ignore the switches so either retained layer can still be exported.

The inspection rail overrides visibility for its selected diagnostic view; it does not change the
saved switches. The export preview names the actual composition used by a combined export.

Click the match chip to open the canvas popover: measured/unknown patch counts, offsets, raw scale,
rotation, translation and point counts. Escape or its close button dismisses it; Show local checks
opens the error map. The explanation distinguishes independently checked patches from fitted points
and leaves other locations unmeasured.

The latest browser journey verified thumbnails, hiding RGB, retained visibility after reload, the
match explanation, hiding L and a colour-only combined export. Its 1140 × 605 uint16 TIFF matched
a full-canvas colour rendering with maximum normalized difference 0.00000769 (16-bit rounding).
