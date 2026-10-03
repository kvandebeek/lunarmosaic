# Implementation status — 2026-10-03

## Implemented

- Local LunarAtlas family interface with separate native L and RGB mosaic builds.
- Two non-destructive adjustment layers, with L lightness and RGB chroma, real thumbnails and saved visibility switches.
- Mixed camera/Barlow scale, rotation and translation fitting, plus manual control points.
- Independent local match checks, native inspection, visible unknown regions and a canvas match explanation.
- Shared reversible north-up, rotation and mirroring.
- Chroma smoothing, strength and saturation; L highlights, contrast, wavelets and USM.
- Full L canvas or conservative common coverage, including internal holes.
- Atomic project saves, version migration, process leases and stale-browser edit protection.
- Recent projects with clearable local path history; finished projects reopen in the editor.
- Recovery showing saved mosaics and the last recorded stage; missing colour keeps L usable.
- Input header summaries, optics sampling ratio and rejected-file reasons for direct imports.
- Actual builder contribution counts and omitted-panel reasons.
- Export preview, dimensions, raw TIFF size estimate and export-only coverage choice.
- 16-bit TIFF/PNG or bounded 8-bit JPEG; individual layers use the shared canvas.
- Matching CLI commands. Original panels are never written; exported images omit source/site metadata.
- Bundled LunarAtlas runtime, independent of a sibling checkout. Remote main checked at
  `7f18388081ff65f55c739aab8b85feeae2046cf6`; newer upstream commits contained no runtime changes.

## Verified locally

24 LRGB workflow checks and 13 existing mosaic regressions pass with the repository's virtual
environment. JavaScript syntax, Python compilation and diff whitespace checks pass.

macOS browser journeys cover real-frame-derived L/RGB panel builds, matching, native inspection,
advanced sharpening, autosave/reopening, recovery, recent-project opening, contribution reporting,
export preview changes and TIFF export. The latest half-size full-canvas TIFF is 592 × 324, uint16;
the saved project retains its common crop. A real lunar-frame north-up solve produced 310 terrain
matches with a reported 1.23 native-pixel residual, and its PNG export was visually inspected.

## Remaining validation and differences from the proposal

- Independent mono/RGB capture sets are still required for imaging acceptance. Derived fixtures do
  not establish performance with different seeing, optical distortion or real Barlow differences.
- Linux and Windows browser journeys and native packaging are pending.
- Interrupted builds restart; completed mosaics are reused. Intermediate stage resume is absent.
- FITS, elastic cross-layer warping, advanced selective colour/white-balance controls and a dedicated equipment database picker remain follow-up work. Diagnostic inspection
  uses the view rail; optics use optional numeric fields.
- Colour saturation is a chroma multiplier, rather than the finishing CLI's selective hue weighting.
  The blend uses CIE Lab lightness/chroma and does not assert Photoshop blend-mode parity.
- The builder uses its existing largest-connected-group behavior and reports omitted panels.
- History and recovery use real state. There are no fabricated time savings, quality results or
  promises that every image location aligns perfectly.

See [design decisions](DESIGN_DECISIONS.md)
and [workflow documentation](LRGB.md) for behavior and commands.

The latest browser verification also covers saved visibility, both layer thumbnails, the match
popover and a colour-only combined TIFF export matching native colour rendering within 16-bit rounding.
