# Two-layer implementation decisions

> **Historical.** These are the choices behind the 3 October 2026 implementation, kept as they were. Phases 0–8
> (5 October 2026) replaced that implementation with the one [DESIGN.md](DESIGN.md) describes; DESIGN.md is the
> design record, and the application's guide is [LRGB.md](LRGB.md).

These are the initial implementation choices, made with the project owner's authorization.
They can be revised after trying real mono and colour capture sets.

| Choice | Implementation and reason |
| --- | --- |
| Build scale | Build each panel set at its native scale. Retain both native mosaics, then sample colour onto the L canvas. This preserves resolution and avoids rebuilding when edits change. |
| Layer visibility | Save L/RGB switches with real mosaic thumbnails. They affect the combined image and export; diagnostic views and explicit individual layer exports can inspect retained layers independently. Both rows stay visible while controls scroll. |
| Match explanation | Open a canvas popover from the persistent match chip, with actual checked/unknown counts and the raw native-grid transform. Avoid narratives about unnamed regions that were not measured. |
| Layer model | L supplies Lab lightness; RGB supplies chroma. Colour strength and smoothing affect chroma. Each layer has its own adjustments and can be exported on the shared canvas. |
| Different optics | Support scale, rotation and translation, including differing Barlow factors. Optional optics are a prior; image evidence determines the match. |
| Match assurance | Run separate local patch checks after automatic or manual fitting. Report measured errors and unknown patches. Keep the global similarity transform until independent camera data justifies local warping. |
| Missing colour | Offer full L with neutral gaps and a conservative common covered rectangle. Include internal coverage holes when finding that rectangle. |
| North-up | Solve from native L through the bundled LunarAtlas runtime, then rotate/mirror both layers together. Store the solution separately from current editable orientation. Preserve the view on failure or cancellation. |
| Sharpening | Four L-only wavelet bands and USM under Advanced, disabled by default. Apply native filters before export resampling; smaller interactive previews approximate them. Use 1:1 to judge detail and grain. |
| Processing | Build sets sequentially and preserve whichever finishes. Reuse completed mosaics after reopening; interrupted builds restart. |
| Reopening | Open finished L projects in the editor; show interrupted/failed/missing sets on the build page. Keep a clearable local list of 12 recent paths, with no photos or observing-site information. |
| Export preview | Show the actual selected layer and coverage, output dimensions and approximate raw TIFF size. Export-only coverage changes leave the saved canvas choice intact. |
| Panel omissions | Keep the builder's existing largest-connected-group behavior. Surface its placed/contributing counts and omitted-panel reasons, rather than imply all selected panels were used. |
| Projects | Atomic local saves, revision checks and one writer per project. Schema 3 migrates existing schema 1/2 edits without changing their values. |
| LunarAtlas relationship | Bundle its runtime and design assets with provenance. Users need no sibling checkout. Upstream changes should be reviewed and integrated as repository updates; do not silently fetch executable code at launch. |

## Validation boundary

24 workflow checks and 13 existing regressions pass on macOS. The browser journey covers real-frame
derived L/RGB panels, native inspection, editing and export. North-up was exercised on a real lunar
frame. Independent mono/RGB camera sets, especially differing Barlow factors and seeing, still need
imaging validation. Linux and Windows UI journeys have not been run.

## Deferred choices

FITS input, elastic cross-layer warping, intermediate stage resume and native application packaging
remain follow-up work. A global match cannot correct differential seeing or
optical distortion. The existing residual checks expose measurable disagreement without claiming
that every location is aligned.
