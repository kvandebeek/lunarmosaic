# LunarMosaic: design decisions, measured behaviour and status

The single record of what LunarMosaic is meant to be, what its filters must do, and where the code
stands. Written 2026-10-05. Supersedes anything older it contradicts; [DESIGN_DECISIONS.md] and
[IMPLEMENTATION_STATUS.md] describe the 3 October implementation and remain accurate about that.

Three kinds of statement appear, and they carry different weight:

- **Measured** — a number from a run on real or synthetic data, with the method stated.
- **Decided** — a choice the project owner made.
- **Open** — not settled; the leaning is given.

This file is the only record of the decisions. [interface-proposal.html](interface-proposal.html) holds the
screen mockups, rendered from LunarAtlas's own stylesheet; open it from disk in a browser. It illustrates
this document and decides nothing — where the two disagree, this file is right. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
says how and in what order to build it; it decides only what this file leaves open, and lists those choices.

[DESIGN_DECISIONS.md]: DESIGN_DECISIONS.md
[IMPLEMENTATION_STATUS.md]: IMPLEMENTATION_STATUS.md

---

## 1. What the application is

Panel sets in — with different panel counts, sizes, coverage, pixel scales and orientations — and one
editable two-layer image out. A local web interface in a native window, built from LunarAtlas's design
system so the two read as one family.

**Decided — two streams, any number of sets, any pixel scales.** Every imported set is declared as
feeding one of two streams: **luminance** (detail) or **colour** (hue). A stream may hold several sets at
different pixel scales — with and without a Barlow, different Barlows, different telescopes. Nothing is
refused for being a different scale; it is routed to the stream it belongs in.

- **Luminance** may be any mono capture: L, R, IR or other.
- **Colour** supplies hue only, and never supplies detail where luminance is missing.
- **The canvas is the finest pixel scale among the luminance inputs** — the fewest arcseconds per pixel.
  Everything else is resampled into that grid, so no resolution is thrown away.
- Source panels are never modified; the project stores paths, transforms and adjustments.

This is why colour from a smaller telescope drops into a larger telescope's luminance without trouble:
colour is blurred anyway, and §3 measures that it may be about 6× coarser than the canvas before any
error becomes visible — a 2× Barlow difference uses barely a third of that headroom.

Mixing scales **within the luminance stream** is allowed too, and the sharper data must win wherever it
reaches. That cannot be left to a sharpness contest. The builder's region selection scores detail per
pixel, and **measured** (2026-10-05, synthetic), a 2× coarser panel given a 100 % unsharp mask out-scored a
genuinely finer panel in **100 %** of their overlap — even with both scored at canvas scale; unsharpened,
the fine panel still won only 92 %. Stacks are routinely sharpened, so **selection works in tiers, ordered by measured resolution**: a region
takes the set that resolves the finest detail — measured on its panels before building, not read from its pixel
scale, because an oversampled small telescope has the finest pixels and coarse detail (synthetic: 7.6 units
against 4.7 for a 2× coarser but sharper set; pre-sharpening barely moves the measure, 3.3 → 3.6). Sharpness
competes only within a tier, and worse-resolving sets fill only what no better set covers. The pose solver needs no change (similarity poses carry a
free scale, and SIFT matching is scale-invariant), but the reference panel must come from the finest set,
because it fixes the canvas scale. The result then carries different true resolution in different places.
That does not
contradict the colour rule above, because a coarse luminance panel still carries real detail, only less of
it, whereas colour carries none by definition. It does mean the application must **show** where the fine
data reaches rather than imply a uniform image — the same principle as never claiming "aligned".

Capture context this design is built around: a 250 mm Newtonian; luminance usually through an IR
filter; AutoStakkert stacking with its RGB align always on, output unsharpened; an ADC used only with a
Barlow or focal extender, so in an L+RGB session the Barlowed luminance has it and a prime-focus colour
set does not. Panel sizes often vary within a run because the ROI changes.

## 2. Workflow

1. **Import.** Two drop zones labelled by what took them. Each set carries its own optics (telescope,
   Barlow, camera) through LunarAtlas's equipment dialog; the resulting scale ratio is stated in words
   and treated as a hint, not a requirement. A single colour mosaic may be fed instead and split.
2. **Build the luminance mosaic** with the existing nine builder steps.
3. **Place colour** — see §3.
4. **Edit** through chains — see §4.
5. **Coverage** — keep the full luminance canvas (grey where colour does not reach) or crop to common
   coverage, previewed with live output sizes.
6. **Save and export.** The project (chains, previews, transforms, built mosaics) is distinct from an
   export (flattened 16-bit TIFF or PNG, or the layers as separate files). Neither assumes PSD or
   layered TIFF.

Alignment quality is always shown as a measured number ("Match 0.6 px · worst 3.1 px"), never as the
word "aligned". Observing location stays local and is written into an export only when ticked for that
export.

## 3. Placing colour on the luminance mosaic

**Decided.** Build the luminance mosaic first, then register **each colour panel directly onto it** and
blend colour on the luminance canvas. There is no separate colour mosaic and no displacement mesh.

Why per panel, and it is not a small effect. An IR luminance set and a visible-light colour set are bent
differently by the atmosphere. Computing the refractivity of standard sea-level air from Edlén (1966),
the shift between 532 nm and 800 nm is Δn = 3.16 × 10⁻⁶, giving a displacement of Δn · tan z:

| Zenith distance | Shift | At 0.34 ″/px | At 0.108 ″/px |
|---|---|---|---|
| 30° | 0.38″ | 1.1 px | 3 px |
| 45° | 0.65″ | 1.9 px | 6 px |
| 60° | 1.13″ | 3.3 px | 10 px |

That is at or beyond the 1–2 px target on its own, and it scales with site pressure (an observatory at
altitude sees proportionally less). Published tables such as Filippenko (1982) give comparable magnitudes
for other wavelength pairs; the figures above are this document's own computation, not a quotation.

Per-panel registration is both necessary and sufficient here. Necessary, because the shift grows with
zenith distance and so drifts over a capture run — one global transform cannot follow it. Sufficient,
because a single panel spans a few arcminutes, over which the shift is constant, so it appears as a plain
translation that the panel's own transform absorbs exactly. That is also why no displacement mesh is
needed. Colour is resampled once, and differing panel counts and coverage resolve themselves.

**Measured tolerance.** A 2048 px colour crop from the 25 September 2026 mosaic, chroma shifted
diagonally, blurred 3 px and saturated ×2 (the current defaults), recombined with unshifted lightness
and compared in CIE Lab. "Visible" is ΔE > 2.3 weighted by lightness — the just-noticeable difference
commonly quoted for CIE76 ΔE*ab (Sharma, *Digital Color Imaging Handbook*, 2003). Caveat: this is the
plain Euclidean CIE76 metric, and CIELAB is known to be less perceptually uniform than intended,
especially for saturated colours, which is why the CIE now recommends CIEDE2000. Lunar colour is very
low in chroma, where CIE76 behaves better, but the exact percentages below would shift somewhat under
CIEDE2000.

| Colour error | Interior visibly wrong | Limb region visibly wrong |
|---|---|---|
| 0.5 px | 0.000 % | 0.000 % |
| 1 px | 0.000 % | 0.015 % |
| 2 px | 0.009 % | 0.30 % |
| 3 px | 0.05 % | 1.2 % |
| 5 px | 0.29 % | 4.4 % |
| 8 px | 0.92 % | 10.1 % |

So the target is **1–2 px**, which per-panel registration beats comfortably. Not yet measured: a
terminator-heavy phase.

**The colour set may be much coarser than the luminance canvas.** Same crop, colour downsampled by N and
brought back to the canvas, then given the standard 3 px blur and ×2 saturation; luminance untouched:

| Colour coarser by | Interior visibly wrong | Limb region visibly wrong |
|---|---|---|
| 2× | 0.000 % | 0.000 % |
| 4× | 0.000 % | 0.000 % |
| 6× | 0.000 % | 0.002 % |
| 8× | 0.000 % | 0.109 % |
| 12× | 0.065 % | 1.45 % |

Up to about **6× coarser costs nothing**, because the colour is blurred anyway. So colour from a short
focal length or a smaller telescope, against luminance through a Barlow, is comfortably inside the limit —
a 2× Barlow difference uses barely a third of the available headroom.

## 4. Editing chains

**Decided.** Editing is three ordered chains of blocks: one on luminance, one on colour, a merge, then
a final chain on the merged image — because curves and levels come last, and sometimes before the
unsharp mask and sometimes after.

- Blocks can be reordered, switched off, duplicated, and **may appear more than once**.
- Blocks operate on the **stored pixel values**, exactly as PixInsight and Photoshop do, so a setting
  copies across in both directions.
- **Whole image only**, with one computed exception: any block may carry a "fade near the limb" setting.
  Measurement regions (white-balance references) are not masks; they choose what is measured.
- **Previews** are user-drawn named rectangles, PixInsight-style, rendered at 1:1 and re-running the
  whole chain on just those pixels. The full image renders on demand or at export. Saved with the project.
- **Merge** is a Color blend with one strength slider and nothing more.
- North-up is not a block: it is geometry. Previews and export share one orientation, but the pixels are
  resampled **once, after the chains**: blocks run on the canvas grid exactly as PixInsight and Photoshop
  would on the unrotated mosaic, and the result is turned at the end, as `lunar_finish` already does.
  **Measured:** turning first (23.7°, bilinear, as the app's warp does) keeps 78 % of the finest wavelet
  layer's detail, and the owner's wavelet setting then comes out 576 ADU rms different.
- **One chain file** (JSON: block type, parameters, enabled, order, plus preview rectangles) is shared
  by the application and the command line; `lunar_finish.py` becomes a runner for it, and its current
  flags become a built-in default chain.

### Blocks

| Block | Parameters and units | Paths | Must match |
|---|---|---|---|
| Levels | black, white, gamma | all | Photoshop Levels on stored values |
| Curves | spline control points; histogram taken at that point in the chain | all | Photoshop Curves |
| Highlights | strength, knee percentile | luminance, final | current `--highlights` |
| Contrast | strength 0 to <0.5 | luminance, final | current `--contrast` |
| À-trous wavelets | bias per layer in PixInsight units, gain shown beside it | all | §5.1 |
| Unsharp mask | amount %, radius px, threshold levels | all | §5.2 |
| Gaussian blur | sigma px | all | on the colour path this is chroma denoise |
| Deconvolution | PSF (Airy disk radius, diffraction intensity, seeing index) or an uploaded PSF; iterations, dering strength and radius, blend raw | all; best first | §5.4 |
| White balance | background and white references, each auto or a drawn rectangle; white limits; structure detection | colour, final | §5.3 |
| Sub-pixel RGB shift | automatic detection, dx/dy readout, nudge pad per channel | colour; also before the split | §6 |
| Saturation | factor, passes, lunar-hue weighting | colour, final | current colour stage |

Blocks that make no sense on a path are shown greyed with the reason rather than hidden.

## 5. Measured filter behaviour

PixInsight 1.9.5 and Photoshop 2026 (27.10.0) were driven headless over synthetic probes and their
outputs fitted by least squares. LuckyStackWorker 7.0.0 was read from its bytecode and verified by
calling its own code. These are the contracts the blocks must satisfy.

### 5.1 PixInsight À-trous Wavelet Transform

With noise reduction, k-sigma and deringing off, ATWT is exactly a linear reweighting of the à-trous
layers. Nine runs fitted with 0.012–0.028 % unexplained, 0.000 % on an impulse:

    gain(b) = 1 + 10·b            for b >= 0
    gain(b) = 1 / (1 + 10·|b|)    for b <  0

Biases 0.1, 0.5, 1, 2.3, 5 give gains 2, 6, 11, 24, 51; −0.5 gives 1/6. Layers are independent and
follow the same law. With target RGB/K each channel is processed independently; with `toChrominance`
off only luminance is sharpened. On flat-luminance colour noise, chrominance on amplified chroma noise
to 7.67 Lab units against 0.14 with it off.

### 5.2 Photoshop Unsharp Mask

    d       = I - Gaussian(sigma) * I
    I_out   = I + (amount/100) · sign(d) · max(|d| - threshold/255, 0)
    sigma   = radius · (1.080 - 0.0339·ln radius)     [measured at radius 1, 2, 4]

Amount is exactly linear (50/100/200 % gave impulse peaks 4742/9484/18968 ADU) and the operator is
level-independent (background 0.25 and 0.50 gave 9483 and 9484). **The threshold is soft** — subtracted,
not a cut-off. Photoshop sharpens each RGB channel separately, which is what creates colour fringing.

### 5.3 PixInsight ColorCalibration and BackgroundNeutralization

Every run is an exact per-channel linear map (residual 0.3 ADU).

- **ColorCalibration** is `out = B + g·(x − B)` per channel: gains that make the background-subtracted
  white reference neutral, with the **background as the fixed point** — the sky is left exactly as it
  was, tint included. Fitted on its output for the synthetic probe: R `1.13688·x − 0.004106`, G
  `1.08001·x − 0.004000`, B unchanged; the offsets equal −B·(g − 1) to four digits (B = 0.030, 0.050), and
  the sky medians are unchanged.
- It **can also rescale overall brightness**: on a real raw panel the red peak went from 0.9000 to
  exactly 1.0000; on a synthetic image it did not. The trigger is not established.
- **BackgroundNeutralization** (default mode) neutralises the sky, leaves the cast, then multiplies
  every channel by a common factor (1.134 synthetic, 1.102 real) mapping the data range to 0–1;
  `targetBackground` had no effect.
- On a raw 25 September limb panel, background preview / white limits 0.10–0.99 / both gave the same
  colour within 0.1 %. **Structure detection on** left the highlands 1.5 % bluer.

The block reproduces ColorCalibration's colour exactly — same fixed point, same gain ratios — but never
changes brightness silently: its gains keep the white reference's brightness, where PixInsight's can
brighten. It offers background neutralisation as a separate visible step to a fixed target. Structure
detection off by default.

### 5.4 LuckyStackWorker deconvolution

Read from `PsfDiskGenerator` and verified by calling the real code over 24 parameter sets. The
synthetic PSF is 64×64 per channel:

1. Airy pattern `I(r) = (2·J1(v)/v)²`, `v = k·(r/R)`, `k = 2π/(λ_nm/1000)`, `I(0)=1`, zero beyond `r > 3R`.
2. Normalise by the max.
3. Log stretch — what "diffraction intensity" actually is: `I <- ln(1 + I·D)`.
4. Gaussian — what "seeing index" actually is: `sigma = R·(5 - S)/16`; S=5 gives sigma=0.
5. Each channel independently min-max scaled to 16-bit.

Wavelengths R 630 nm, G 532 nm, B 465 nm, all 532 in mono — so the PSF is genuinely chromatic. The
deconvolution is **WPL** (Wiener Filter Preconditioned Landweber) from Parallel Iterative
Deconvolution, not Richardson–Lucy, with per-channel iterations, dering strength and radius, blend raw.

Verified Python port, reference dump and Java harness: `/Volumes/Astro/moontest_04102026/lsw_psf/`.
Airy and log stages match to 1e-7; with blur, median 1.8e-4 and worst 7.8e-3 relative (ImageJ's
Gaussian versus OpenCV's).

Deconvolution is the one block that recovers detail rather than amplifying it, and a per-channel
chromatic PSF is the most promising lead on the coloured edges in §8.

## 6. Sub-pixel RGB shift

**Decided.** Detects the alignment automatically — per panel during the build (correcting only above
about 0.1 px, always logged) and again as a block with a manual override. Detection uses two
independent estimators and applies a shift only when they agree; otherwise it says so and leaves the
image alone.

**Measured, 25 September (17 of 101 panels across a 68-minute run).** Red and blue sit 0.04 px from
green on interior texture with no drift — AutoStakkert's RGB align, always on, has already done the
work. On that set the block would correctly do nothing. It earns its place on a prime-focus colour set
without the ADC, or a stack where RGB align was off.

**Saturn, same night**, against LuckyStackWorker on the same file: channel centroids reproduced its
correction within 0.2 px; phase correlation, which worked on lunar texture, was about 1 px wrong on a
smooth disk — hence the two-estimator rule. Saturn's red-to-blue separation was 0.71 px native against
0.04 px on the Moon.

**On lunar texture the centroid is the wrong second estimator** (synthetic, 2026-10-05). A
gradient-weighted centroid is blind to translation inside a fully textured crop: it returned
−0.006/+0.046 px for a true +0.30/−0.20 px, so with the agreement rule the block would only ever "agree"
when there was nothing to correct. Phase correlation iterated three times and a global Lucas–Kanade
translation fit both work, and agree within 0.07 px even with one channel blurred by σ 1.2 px; a single
pass of phase correlation read 35 % high in that case.

## 7. Sharpening: what the owner actually prefers

**Decided.** Sharpening is chosen interactively per mosaic. No universal default is required, because
the operating point is set by where noise becomes visible, which depends on seeing, stack depth,
sampling and how far the image will be stretched afterwards.

Owner's method: push wavelets in PixInsight until noise just appears, step back (sometimes by a tiny
amount), save; then unsharp mask in Photoshop until it looks sharper but not noisy; never denoise;
curves and levels before or after. What breaks an image: grain in the mare, crunchy texture, bright
speckle or clipping — **not halos**.

Four rounds of blind trials on the 2026-10-04 mosaic:

- **Round 1 (16 blind pairs).** Wavelet layer 2 at gain 11 undefeated 5–0; layer 2 beat layer 1 in all
  four matched-detail comparisons. Judged on the crater scene only.
- **Round 2 (ladders past breaking point).** Usable / broken: layer 2 **16 / 31**, layer 1 **11 / 51**,
  USM at radius 1.5 **500 % / never**, layer 2 g11 + USM **+100 % / +350 %**.
  **Detail-to-noise was refuted as a predictor**: usable 0.37–1.13, broken 0.34–1.12, complete overlap.
  Two measures split cleanly — detail amplitude (usable 3.29–5.92×, broken 9.32–14.48×) and limb rise
  (usable 2.5–3.5 %, broken 6.9–14.7 %). USM never broke only because at radius 1.5 even 500 % reaches
  just 3.29× detail.
- **Round 3 (slider bench, 12 saves).** Layer 2 engaged in all 12, median bias 0.88; threshold 0 in all 12.
- **Round 4 (own method, 3 runs).** Layer 1 came out at **0.59 ± 0.012** across all three runs,
  re-found each time — the most reproducible number in the study, overturning round 1's reading that
  layer 1 should be zero (round 1 never tested the layers combined).

**Current recommendation for that mosaic:** PixInsight layer 1 bias 0.60, layer 2 bias 1.10, layer 3
bias 0.20, then Photoshop unsharp mask about 60 % at radius 1.3 — `--wavelets "0.6,1.1,0.2,0"`.

Caveats: one observer, one mosaic, one phase. Round 1 used a single scene; round 4 had a carry-over
defect (only run 1 clean) and 8-bit crops whose unsharpened grain sits below one LSB, so the absolute
grain threshold is not yet transferable.

**The optics explain the preference, and a direct measurement confirms it.** The disk is 5616 px across;
against NASA's mean apparent diameter of 1896″ that is **0.334 ″/px**. For a 250 mm aperture at 800 nm the
diffraction cutoff D/λ falls at **0.506 cycles/px**, essentially at the Nyquist limit of 0.5 — so the
sampling is matched to the optics, with no spare resolution in the best case.

What the data actually delivers was measured from the radially averaged power spectrum of a 1024 px
interior patch, taking the cutoff as the frequency where signal drops to a tenth of the flat
high-frequency noise floor:

| Set | Scale | Information cutoff | Finest real detail | Against diffraction |
|---|---|---|---|---|
| 2026-10-04, IR, unsharpened | 0.334 ″/px | 0.443 cycles/px | 2.3 px = 0.75″ | 1.1× coarser |
| 2026-09-25, visible, pre-sharpened | 0.151 ″/px | 0.173 cycles/px | 5.8 px = 0.87″ | 1.9× coarser |

Two things follow. First, the IR set reaches within 1.1× of its own diffraction limit and resolves
**0.75″**, while the visible set — finer sampled, and already wavelet-sharpened, which inflates its high
frequencies — resolves only **0.87″**. The IR filter wins in practice despite its coarser diffraction
limit, because seeing dominates. Second, the 4 October set is genuinely close to critically sampled
(0.443 against Nyquist 0.5), whereas the 25 September set is oversampled by about 2.9×.

An à-trous layer at scale *s* px carries roughly 1/(4s) to 1/(2s) cycles/px. On 4 October that puts
**layer 1 at 0.25–0.5 cycles/px — half of it above the measured cutoff of 0.443**, where there is nothing
but noise, and **layer 2 at 0.125–0.25, entirely inside** the real information. The blind trials chose
layer 2 and rejected layer 1, which is exactly what the spectrum says. Layer 1's measured SNR being the
lowest of layers 1–4 (12.5 against 16.2, 16.0, 13.5) is the same fact seen another way.

**This generalises into a rule the application can apply.** The best layer to push is the highest one
whose band lies entirely below the measured information cutoff. On 4 October that is layer 2; on
25 September, with a cutoff of 0.173, layer 2 straddles the limit and **layer 3** would be the right
choice. So the correct setting is a property of the session's seeing and sampling, not a constant — which
is why §7 makes sharpening interactive per mosaic, and why the application should measure the cutoff and
propose the starting layer rather than leave the user to find it by trial.

Caveats: the cutoff depends on the threshold chosen against the noise floor (a tenth of it here), and the
limb cannot be used for this — lunar limb mountains are several kilometres, about 0.5″ per km, so an
averaged limb profile measures topography as much as the point-spread function.

## 8. Open questions

1. **Daylight sky removal.** The builder subtracts one constant per channel; a daytime sky has a
   gradient plus scattered light near the limb. On 2026-10-04 that left a glow wedge up to 11 % of the
   disk's peak. Leaning: a smooth sky model per panel before blending, with the Moon masked, **plus a
   limb-distance term**, subtracted everywhere including across the disk — then a black point just above
   the sky noise, which is safe only once the sky is flat.
2. **Cause of the coloured edges.** On 25 September every strong edge shows red reaching about 0.2 px
   further into the dark side, **in every direction** (fit: 0.198 px direction-independent, 0.076 px
   shift-like). Matching channel blur did not change it (0.21 → 0.22), and AutoStakkert's RGB align was
   on, so it is neither blur nor residual misalignment. Leaning: test a per-channel tone match, and a
   chromatic PSF (§5.4).
3. **Two consequences of mixing scales, both needing a guard rather than a decision.**
   *Code:* `verify_pair` in `mosaic_common.py` rejects any pair whose scale ratio falls outside
   **0.97–1.03**, so mixed-scale panels cannot pair at all today — they would split into disconnected
   groups and fail like the "panels could not be joined" case. The gate has to become a tolerance around
   the *expected* ratio, taken from the two sets' declared optics, instead of around 1.0.
   *Size:* the canvas is the widest coverage at the finest scale, which can be far larger than either set
   alone. A full disk is 5900 px across at 0.32″/px but 17600 px at 0.108″/px — 309 Mpx, about 1.9 GB as
   16-bit RGB — and 38000 px at 0.05″/px, some 8.7 GB. The import screen must state the resulting canvas
   before building and offer to cap it.
   Note also that equal pixel scale does not mean equal resolution. At 550 nm a 150 mm aperture resolves
   0.923″ by the Rayleigh criterion (1.22 λ/D, first null) against 0.554″ for a 250 mm, with FWHM
   (1.028 λ/D) of 0.777″ and 0.466″ and the empirical Dawes limit (116/D mm) at 0.773″ and 0.464″. In IR
   at 800 nm the same apertures give Rayleigh 1.342″ and 0.805″. Whichever criterion is used, a smaller
   aperture is softer however finely it is sampled — which is why tiers are ordered by measured resolution
   (§1) and, within a tier, panel quality is measured on shared terrain rather than assumed from the optics.
4. Smaller: what triggers ColorCalibration's brightness rescale; whether the owner leaves structure
   detection on; an absolute grain threshold measured on 16-bit data.

## 9. Implementation status

A two-layer LRGB application was committed on 3 October 2026 (`0151e68`), built from the first version
of this design: `lunarmosaic.py`, `mosaic/app.py`, `lrgb.py`, `project.py` (schema 3), `sharpen.py`,
`mosaic/ui/`, with 24 workflow checks and 13 regressions passing. It predates most of §4–§7 and
diverges:

| Area | Implemented 3 October | Decided since |
|---|---|---|
| Editing model | Two layers with fixed adjustments in the application; `lunar_finish` runs chains since phase 2 | Three chains of reorderable blocks |
| Colour placement | Colour built as its own mosaic, then sampled onto L by one global transform | Each colour panel registered directly onto the luminance mosaic |
| Wavelet units | Amount 0–3 meaning gain 1 + amount, on CIE lightness | PixInsight biases (gain 1 + 10b) on stored values |
| Unsharp mask | Amount 0–1, threshold 0–0.05 of full scale, hard cut-off, sigma = radius | Photoshop units, soft threshold, measured radius law |
| Saturation | Chroma multiplier | Lunar-hue weighting |
| Missing | In the application: all of these. As chain blocks (run by `lunar_finish --chain`) since phase 3: white balance, sub-pixel RGB shift, deconvolution, limb fade, levels, curves, Gaussian blur | White balance, sub-pixel RGB shift, deconvolution, limb fade, daylight sky model, named previews |
| Build recovery | Interrupted builds restart | Resume from completed stages |
| Luminance sets | One set; pairs outside a 0.97–1.03 scale ratio are rejected | Several sets and scales per stream, selected in tiers by measured resolution |
| Lab conversion | OpenCV's float conversion in the application (round trip off by 66 ADU rms); exact in `lunar_finish` since phase 1 | Exact float math with the same constants |

That table is the migration list for the chain editor.

### Phase 0 — 2026-10-05

Baseline `3c3fba3` is committed; work continues on `impl/phase-0`. The bug triage and autosave
repair are recorded in `BUGS.md`. **Phase 0 is complete apart from its closing commit.**

`mosaic/tests/synthetic.py` now provides in-memory, seeded fixtures: uint16 RGB/mono lunar
disks and crescents, enclosed crater shadows below 1022 ADU, overlapping panels with up to
15 % size variation, optional coarser sets and per-panel radial distortion, the Appendix F
white-balance probe, and textures with known spectral cutoffs plus white noise. Masks identify
space, the lit disk, and enclosed shadows. Panel metadata records the actual sampling and
pixel-centre mapping, including dimension rounding at noninteger scales; `panel_coordinates()`
also includes the specified radial distortion. These are synthetic test inputs, not evidence
of alignment quality on real captures.

The existing `lrgb_workflow.detail()` helper moved into `synthetic.py`; its default output
was checked byte-for-byte against the previous version. Nine new checks cover fixture truth,
repeatability, coverage, coordinate mappings, and calibration values. A contact sheet of the
generated disk, crescent, panels, and probes was inspected. Full discovery passes **53 tests**
(37 original, 7 autosave, 9 fixture checks), in 4.536 s on the development Mac.

Completed afterwards:

1. **Golden fixtures.** `mosaic/tests/golden.py` freezes today's `lunar_finish` output for three cases as
   16-bit zlib fixtures (4.9 MB), with every setting pinned twice (flag and environment variable) so `.env`
   cannot leak in; reruns are bit-identical. `--report` gives rms / p99.9 / max; `--capture --exact` writes the
   phase-1 set.
2. **Builder determinism recorded.** `determinism.py`: three full builds — fine alignment (50 tiles) and mesh
   (876 tiles) included — gave bit-identical mosaics; the layout reports differed only in `created`. The
   fixture had to be sized for this: fine-alignment tiles need overlaps of at least 352 px, and small panels
   need `--proxy-factor 1` (plan, phase 5 traps).
3. **Runner.** `run_all.py` runs every suite as its own process and exits non-zero on any failure.
4. **Crash fixed.** The mesh refinement crashed the whole build (`np.vstack` of nothing) when no dense tile
   survived, which happens on small or low-texture panels. It now keeps the fine-alignment poses and says so,
   and the builder survives the mesh's own "no constraint" error the same way. Regression
   `mesh_no_tiles_fallback`, checked to fail without the fix.
5. **Defect found, not yet fixed:** a mono image comes out of `lunar_finish` with a 34 ADU channel spread. The
   cause is OpenCV's float Lab conversion, which gives a neutral grey |a*|, |b*| up to 0.125 instead of 0;
   exact Lab (plan D17, phase 1) removes it, and `golden.py` then tightens its mono check from 80 to 1 ADU.

Full discovery now passes **60 tests** (53 above, one new regression, three golden, three determinism) in
23 s; `run_all.py` passes all six suites.

### Phase 1 — 2026-10-05

Filters moved into their own module, then exact Lab.

1. **`mosaic/filters.py`** holds the numerical filters, moved byte-for-byte from `lunar_finish.py` (checked against
   the committed file); the golden fixtures reproduced bit-exactly after the move. `lunar_finish.py`, `sharpen.py`
   and `lrgb.py` import from it, so the application no longer loads the finishing CLI — and the builder behind
   it — just to reach the wavelets.
2. **Exact CIE Lab** (`rgb_to_lab`, `lab_to_rgb`, plan D17) replaces all five OpenCV Lab conversions in
   `lunar_finish.py`: float64 arithmetic in chunks, OpenCV's constants. Round trips within 0.004 ADU on greys,
   lunar colours and near-black, 0.05 on saturated colours; a neutral grey's |a*|, |b*| is below 1e-13.
3. **Effect on output** (`golden.py --report`): 68–79 ADU rms against the phase-0 fixtures — OpenCV's conversion
   error leaving, not a change of method. **Mono output is now exactly mono: channel spread 34 ADU → 0.** The
   fixtures were re-captured as `golden_*_exact.tif`, and the mono check is now 1 ADU.
4. **Cost:** 2 s → 8 s on a 16.8 Mpx image, so about +70 s on a 200 Mpx mosaic. Most of it is six
   full-resolution Lab conversions per pixel made only to feed the movie's reduced frames; phase 2 takes those
   snapshots on the reduced copy instead.
5. The printed colour facts no longer show "nan" on one-hue images (a median over no pixels, which the phase-0
   code also printed); they say there are no pixels off the lunar hues.
6. The application (`lrgb.py`, `sharpen.py`) keeps OpenCV's Lab until phase 4 rebuilds its renderer on chains.

Tests: `lab_checks.py` (T-LAB) added; all seven suites pass.

### Phase 2 — 2026-10-05

The chain engine, and `lunar_finish` running on it.

1. **Engine.** `mosaic/blocks.py` holds the five blocks `lunar_finish` had — wavelets, unsharp mask, highlights,
   contrast, saturation — with the schemas of plan Appendix A; `mosaic/chain.py` the documents, validation, the
   statistics pass (once, on the 1/4 proxy with sharpening skipped, plus a full-resolution probe for the
   sharpening blocks' own statistics and readouts), tiled rendering and the merge.
2. **`lunar_finish` runs chains.** Its flags compile into a chain; `--chain FILE` runs a chain file and
   `--write-chain FILE` writes one (without `-o` it stops there). The movie and the printed facts are built per
   block, so any chain works — checked with no contrast and two saturation blocks. Snapshots are converted on the
   reduced copy, so the 4 October mosaic now takes 10 s instead of 14.
3. **Same pixels.** The golden cases match the old code within rms 0.02 ADU (max 1, rounding); on the real
   4 October mosaic the 8.6 Mpx disk matches the phase-1 code with rms 0.02 ADU, max 1.
4. **Two refinements of the plan, both measured.** Saturation fades its own *factor* near the limb, as
   `--limb-taper` always did: the old taper divided by max(taper, 1), so even `--limb-taper 0` tapered the outermost
   pixel, and blending the output instead differs whenever there are several passes. And protection (D7) now holds
   near-black colour: protecting only space let the 3 px chroma blur carry a crater rim's colour into its shadow,
   and saturation ×4 took it out of gamut — L* 0.76, chroma 0.10 became RGB (0, 1053, 7717), chroma 15.3; across the
   synthetic shadows the median chroma went from 0.08 to 3.42. Near-black pixels now keep their input colour while
   their lightness is processed.
5. **What changes on real images.** The old rule froze every pixel below L* 1. On the 4 October mosaic that left
   an unsharpened patch with a speckled edge on the dim terminator surface — 51 % of a 170 px square was simply the
   input — which the soft gate now processes continuously. Faint sky between L* 0.5 and 1.5 fades its processing in
   instead of switching it at L* 1 (163 ADU rms difference there, outside the disk). Crater shadows keep their colour
   and get their lightness processed, so sharpening now deepens them. Images:
   `/Volumes/Astro/moontest_04102026/phase2/`.

Tests: `chain_checks.py` (T-TILE for every block and the default chain — checked to catch a halo even 8 px short —
T-ATWT, T-USM, T-MERGE-ID, T-SPACE, T-MOVIE, validation, flags and environment); `golden.py` now applies T-GOLD.
All eight suites pass; 79 tests.

### Phase 3 — 2026-10-05

The remaining blocks, and the measurements behind them.

1. **Blocks.** Levels and Curves (natural cubic spline, applied through a 65 536-step table); Gaussian blur; white
   balance — PixInsight ColorCalibration's colour with the background as the fixed point, gains normalised to keep
   the white reference's brightness, structure detection refused for now; sub-pixel RGB shift; deconvolution —
   LuckyStackWorker's PSF and WPL. `lunar_finish --chain` runs and describes all of them, movie included.
2. **Measurements** (`mosaic/measurements.py`): the disk and space maps (moved out of `chain.py`), textured crops,
   the information cutoff and the wavelet layer it suggests (shown with the wavelets block), the two RGB-shift
   estimators, and the fine-detail amplitude.
3. **Corrections found while building, all measured.** Phase correlation must iterate to convergence: one OpenCV
   step captures only about 43 % of the remaining shift on noise-free texture, so the planned three steps stopped
   at 0.21–0.25 px for a true 0.30; converged, both estimators are within 0.04 px in every case tested. A flat
   image scores 6e-15 on the texture measure from rounding alone, against 2.5e-4 for the least textured tenth of
   the synthetic disk, so crops need an absolute floor (1e-6). The noise and cutoff probe stays at the disk's
   centre of mass, as `lunar_finish` always took it — noise is estimated on flat tiles, so a texture-picked crop
   would be the wrong one; RGB shift picks three textured crops inside it.
4. **Deconvolution, as defined, converges fast.** The PSF reproduces LuckyStackWorker's to 5e-8; 10 iterations
   cut the error against the sharp image by 62 %. But with the default PSF (Airy radius 3) the result is identical
   from the fifth iteration on, while a broad PSF (radius 8) keeps changing past 30 — the Wiener preconditioner
   nearly inverts the PSF in one step. Whether LuckyStackWorker's iterations behave alike is open (plan phase 9,
   V3); until it is checked, the iterations setting matters mostly for broad PSFs.

Tests: `block_checks.py` (T-WB, T-CUT, T-SHIFT, T-PSF, T-DECONV, T-LEVELS, T-CURVES, T-ORDER for all eleven blocks,
T-TILE for the new ones — deconvolution at 100 iterations, dering on — and every block through `lunar_finish`).
All nine suites pass; 94 tests.

### Changes made 2026-10-05 to `lunar_finish.py`

1. **Wavelets match PixInsight.** `wavelet_gain()` applies the measured law; sharpening runs on each
   channel's stored values, not CIE L*; the noise estimate uses stored values; biases outside −1…15 are
   refused. The `pixinsight` preset is now layer 1 bias 2.3.
2. **Unsharp mask matches Photoshop.** `usm_sigma()` and `unsharp()` with the soft threshold, used by
   both `--usm-mode` options.
3. **Mono images** skip the colour stages, and the movie no longer shows saturation steps on a mono image.
4. The application's own numbers are unchanged: `sharpen.py` passes its gains (1 + amount) explicitly.
   Moving the application to PixInsight units needs a schema migration (new bias = old amount ÷ 10).

Verified: 13 of 13 regressions and 24 of 24 workflow checks pass. Wavelets against a real PixInsight
output at bias 2.3: 0.51 % rms of image (44.5 ADU), where the old code was 7.00 % (612.7 ADU); recovered
layer-1 gain 23.72 against PixInsight's 23.99. USM against Photoshop: 3.6–7.1 ADU rms on the threshold
staircases where the old code was 33–468; 0.07–0.33 ADU on impulses.

**Behaviour change:** `LUNAR_FINISH_WAVELETS=pixinsight` now means layer-1 gain 24 (the real PixInsight
setting) instead of the old, unintended gain 4. Divide any pre-2026-10-05 bias by 10 to keep its effect.

## 10. Where the evidence lives

| Item | Location |
|---|---|
| Calibration harnesses | `/Volumes/Astro/moontest_04102026/{atwt,usm,colour,cc}_calibration/`, `cc_real/` |
| Sharpening study data | `/Volumes/Astro/moontest_04102026/sharpen_study/`, `limits_study/` |
| LuckyStackWorker PSF port and dump | `/Volumes/Astro/moontest_04102026/lsw_psf/` |
| 4 October test build | `/Volumes/Astro/moontest_04102026/` |
| Chain engine against the old code on real data (terminator patch, movie frames) | `/Volumes/Astro/moontest_04102026/phase2/` |
| LunarAtlas reference checkout | `/Volumes/Astro/LunarAtlas` |

Driving the applications headless: PixInsight with
`--automation-mode --no-splash -r=<script.js> --force-exit` (Console output does not reach stdout, so
log to a file; some process enums are undefined in 1.9.5 scripts and need their integer value).
Photoshop through `osascript` with the .jsx read as text; the DOM method is `applyUnSharpMask`, capital
S. LuckyStackWorker by disassembling its jar with `javap` and calling its private methods by reflection.

## 11. External sources

Checked 2026-10-05. Everything else in this document is measured here or decided by the project owner.

| Claim | Source |
|---|---|
| Rayleigh criterion 1.22 λ/D (first null); Dawes limit 116/D mm is empirical and tighter (4 % dip against 22 %) | [Rayleigh criterion, LibreTexts](https://phys.libretexts.org/Courses/Joliet_Junior_College/JJC_-_PHYS_110/College_Physics_for_Health_Professions/28%3A_Wave_Optics/28.07%3A_Limits_of_Resolution-_The_Rayleigh_Criterion) · [resolution criteria compared](https://www.eckop.com/resources/optics/resolution/) |
| Airy PSF FWHM = 1.028 λ/D, narrower than the first null | [SPIE Optipedia, Airy disk](https://lux.spie.org/publications/spie-publication-resources/optipedia-free-optics-information/fg01_p88_airy_disk) |
| Sampling: the Nyquist limit is 2 px per resolution element, not 2 px per FWHM as commonly stated; 3–4 px/FWHM for accurate work | Robertson, *Detector sampling of optical/IR spectra: how many pixels per FWHM?*, PASA 2017 — [arXiv:1707.06455](https://arxiv.org/pdf/1707.06455) |
| Diffraction cutoff of an incoherent system at D/λ, beyond which the MTF carries no information | [SPIE Optipedia, Airy disk](https://lux.spie.org/publications/spie-publication-resources/optipedia-free-optics-information/fg01_p88_airy_disk) |
| Moon mean apparent diameter 1896″ | [NASA NSSDC Moon Fact Sheet](https://nssdc.gsfc.nasa.gov/planetary/factsheet/moonfact.html) |
| ΔE*ab ≈ 2.3 as the CIE76 just-noticeable difference; CIELAB non-uniform for saturated colours; CIEDE2000 now recommended | Sharma, *Digital Color Imaging Handbook*, 2003, via [Color difference](https://en.wikipedia.org/wiki/Color_difference) |
| Differential atmospheric refraction varies with wavelength and as tan(zenith distance); blue refracted most | Filippenko, PASP 94, 715 (1982), as summarised in [Astronomical Spectroscopy](https://arxiv.org/pdf/1010.5270) · [pyWiFeS ADR notes](https://pywifes-pipeline.readthedocs.io/en/stable/modules/wifes_adr.html) |
| Refractivity of standard air, used to compute the 532/800 nm shift in §3 | Edlén (1966) dispersion formula |

The §3 dispersion figures are computed here from Edlén, not quoted. An attempt to verify them against
Filippenko's published table was inconclusive — the review paper's text could not be retrieved — so they
stand on their own method. They assume standard sea-level air and scale with site pressure.
