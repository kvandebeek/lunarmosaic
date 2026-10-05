# LunarMosaic: design decisions, measured behaviour and status

The single record of what LunarMosaic is meant to be, what its filters must do, and where the code
stands. Written 2026-10-05. Supersedes anything older it contradicts; [DESIGN_DECISIONS.md] and
[IMPLEMENTATION_STATUS.md] are historical: they describe the 3 October implementation, which phases 0–8 replaced.

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
| Contrast | strength −0.5 to <0.5; negative flattens the midtones (the old application's contrast went both ways) | luminance, final | current `--contrast` |
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
3. **Two consequences of mixing scales, both needing a guard rather than a decision.** *(Both guarded in phase 5:
   the pair gate expects each pair's declared ratio, and the import page states the canvas, asks above 400 Mpx and
   offers a smaller one. Kept for the reasoning.)*
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
`mosaic/ui/`, with 24 workflow checks and 13 regressions passing. It predated most of §4–§7. Phases 0–8
(below, 5 October 2026) brought it to this design: chains of blocks, colour placed panel by panel, luminance sets
of several scales, the measured filter laws and exact Lab, in the engine, the interface and the commands. What
still differs is left for later (IMPLEMENTATION_PLAN.md, phase 9):

| Area | Implemented | Decided |
|---|---|---|
| Daylight sky | The builder subtracts one constant per channel | A smooth sky model per panel with a limb-distance term (§8, still open) |
| Build recovery | An interrupted build restarts from its first step; completed mosaics are kept | Resume from completed stages |

Projects from before keep their meaning when opened: schema 1–3 adjustments become an equivalent chain
(IMPLEMENTATION_PLAN.md, Appendix D), and a project with the old global colour match keeps it until colour is
placed again.

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

### Phase 4 — 2026-10-05

The application stores and renders chains; the current interface keeps working through a compatibility layer.

1. **Project schema 4.** Sets are a list (`L` feeds the luminance stream, `RGB` the colour stream); the old global
   match is kept as `colour.legacy_match` until phase 6 places colour per panel; rotation, mirror and coverage are
   `geometry`, the layer switches `view`; the chain document lives in the project. Schema 1–3 projects migrate on
   opening, and the original file is kept as `project.v<N>.json`. Opening now saves only when it changed
   something, so a second open leaves the revision alone. Build state stays in each set until phase 5 gives each
   stream several sets.
2. **Rendering (D1, D13).** Statistics are computed once per chain on the whole canvas; the chains run on the
   luminance canvas; rotation and mirror come after, with Lanczos-4. The colour stream is the colour mosaic
   sampled onto the canvas through the match, every pixel from one inverse, so tiles agree. Exact renders below
   1:1 sample a finer grid and average it down; renders below 75 % zoom in the workspace run the chains on every
   n-th canvas pixel, with kernels shrunk alike (deconvolution is left out there). Measured: a 700 × 500 preview
   at 23.7° equals the same pixels of a full export exactly (0 ADU); a common-coverage export equals its region of
   a full one exactly; a half-scale export is the averaged full export within 0.75 ADU.
3. **Export scale changed its pixel grid.** Output pixel j at scale f now covers view pixels [j/f, (j+1)/f); the old
   code sampled the view at j/f, a quarter of a pixel off at ½. Only this makes a reduced export equal the
   averaged full one.
4. **Compatibility layer, until phase 7.** `/api/state` serves the schema-3 shape the interface reads, with the
   schema-4 data under `schema4`; `/api/edits` regenerates the chain from the sliders; `Scene.render_edits()` keeps
   the old call. `/api/render` (plan §4.7) returns a region of the oriented view at full scale as a 16-bit PNG.
   `sharpen.py` is gone.
5. **Statistics worker.** One thread, not a job, so moving a slider during an export works: the newest chain wins,
   an older computation stops at its next block, a render waits for the statistics it needs, and an export keeps
   the statistics it started with. Checked with an export paused mid-way while the chain changed: the file equals
   one made without the change.
6. **Three departures from plan Appendix D**, because the interface keeps editing the sliders through this
   translation until phase 7. Highlights and contrast join the luminance chain, where the old renderer applied
   them, so the luminance view still shows them. Their strength follows the slider (0.49 × value; the old contrast
   had a midtone slope of 1 + value/2, the block has 1 + strength) instead of being switched on at the block's
   default. And the contrast block accepts negative strength, which keeps the old slider's lower half; the curve
   stays monotonic down to −1. Block ids follow from their place, so the same sliders always give the same chain
   and its statistics stay cached.
7. **Speed, measured on the 4 October mosaic** (5918 × 6974 px view at 23.7°): a 1:1 viewport 0.73 s, the fit
   preview 0.42 s, a full export 9.0 s, statistics 1.9 s once per chain. The first version took 2.7 s, 1.7 s,
   33 s and 3.9 s. Four changes got there, all bit-identical: exact Lab on a thread pool (6× faster on 10 cores),
   L* alone where the merge needs only lightness, space restored by a masked copy instead of indexing, and view
   tiles rendered four at a time. The old renderer did the viewport in 0.11 s and the export in 3.3 s: it sharpened
   one channel and used OpenCV's table-based Lab, where the chains sharpen each channel as PixInsight does, convert
   Lab exactly and protect space. Evidence: `/Volumes/Astro/moontest_04102026/phase4/`.
8. **Not done yet:** restarting the statistics from the first changed block (plan §4.5) is left for phase 7, where
   single-block edits arrive. Until then a changed chain recomputes its statistics whole, and the source proxies,
   the slowest part, are reused. The interface does not yet show the migration notes.

Tests: `scene_checks.py` (T-MIGRATE, T-PREVIEW in process and through `/api/render`, T-COVER, T-STALE, T-WORKER,
half-scale export, approximate view, newest statistics win). `lrgb_workflow.py` passes unchanged in what it checks:
its 24 tests now reach the project through the schema-4 accessors, call `render_edits`, and write the version-1
and version-2 files in their real shape (they relabelled a schema-3 file before); no tolerance changed. All ten
suites pass; 102 tests.

### Phase 5 — 2026-10-05

Luminance sets at different pixel scales build into one mosaic on the finest set's grid.

1. **Builder.** `--panel-scales FILE` gives each panel its set and its scale relative to the finest set. The pair
   gate expects that ratio, the reference panel comes from the finest set, and region selection runs tier by
   tier: sets are ordered by their measured resolution, and each tier competes only where no better tier
   validly covers. `<out>_sets.json` reports each set's detail, its tier and its share of the disk, and maps every
   region label to its set. `--canvas-scale` reduces the panels as they are read; `--max-canvas-mpx` stops the
   build before blending when the canvas is larger than confirmed. Without the file the builder is unchanged:
   checked bit for bit against the phase-4 builder, layout included.
2. **Measured on a 2× mixed build** (9 fine and 4 coarse panels with terrain-like detail and noise): every panel
   placed within 0.07 px rms of the truth, the fine set supplying 99.98 % of the area it covers.
3. **Tiers are necessary, as D14 says.** With one fine and one 2× coarser panel of the same area, choosing by
   sharpness per native pixel — the old rule — gave the coarse panel 97 % of the fine panel's cells, 99 % once
   unsharp-masked. With tiers it gets none, and a set at the finest scale but blurred by σ 3 px yields to the
   coarser, sharper one.
4. **Two corrections found while building.** Brightness matching compared intensity percentiles, and a sharper
   panel has a wider spread: on the mixed build the overlaps got worse (median mismatch 0.72 % → 1.09 %, gains up
   to 1.22). The finer side is now blurred to the coarser one's sampling first: 0.18 % → 0.11 %, gains within 1 %
   of the true 1. And the information cutoff assumes the highest frequencies hold only noise: a spectrum that stays
   flat to the sampling limit made a 2× coarser panel read 78 px blurry. Such a panel is now "not measurable" and
   counts as resolving 2 of its own pixels. Terrain's power falls steeply with frequency, so real panels are
   measured as before.
5. **Application.** Build state moved from the sets to the streams. `/api/sets` adds, edits and deletes luminance
   sets; a set without optics gets its scale estimated from matched panels (measured 2.00 for a true 2), and a
   mixed build waits until the estimate is confirmed. `/api/canvas-estimate` gives the canvas lower bound and
   suggests a smaller canvas when the finest set is oversampled. The luminance stream builds every set in one run;
   a mixed stream stops at 400 Mpx unless a larger canvas is confirmed, while a single set builds as before. The
   `resolution` view tints each region by the set that supplied it, with a legend of pixel scales.

Tests: `mixed_checks.py` (T-GATE, T-SHARP, T-MIX against the truth, the one-set file, canvas scale and cap, the
application journey); a phase-4 project opens with its build state moved. All eleven suites pass; 114 tests.

### Phase 6 — 2026-10-05

Colour is placed panel by panel onto the luminance mosaic; there is no colour mosaic any more.

1. **`mosaic/colour_place.py`.** Each panel is found in up to three steps. First a global SIFT match on the canvas
   reduced to about 2048 px, with the panel resampled to the same scale. Then, when that reduction is more than
   2×, 512 px tiles on the halved canvas. Finally 256 px canvas tiles, phase-correlated against the panel warped
   into each tile. The canvas tile is first blurred to the panel's sampling.
2. **Pose model (D15).** Every fourth tile is held out, and the simplest model within 10 % of the best held-out
   residual is kept: similarity, affine or poly3, the last pulled towards the affine fit. A panel with too few
   tiles or a held-out residual above 2 px is rejected, and the reason is recorded.
3. **Per panel.** Red and blue are moved onto green when both RGB-shift estimators agree (D16). Panels are matched
   in brightness after subtracting each one's sky: log-gains are solved per overlap component, and further
   components are matched by their median colour on the disk, which the report says. Panels are blended into the
   colour canvas at the colour data's own sampling (D8, float k): 16-bit RGBA, alpha = coverage, feathered 64 px
   from each panel edge. The pixel scale comes from optics, or is estimated by a wide-gate match (2.0000 for a
   true 2).
4. **Measured, synthetic.**

   | Case | Placed | Error against the truth |
   |---|---|---|
   | Drift 0–3 px across the run, colour 2× coarser | 14 of 16 | 0.068 px rms (target 0.25) |
   | Colour 6× coarser | 14 of 16 | 0.25 px rms (target 1.5) |
   | 1.5 px radial distortion at the panel corners | 9 of 9 | 0.20 px rms; up to 0.94 px in the corners |

   The unplaced panels are image corners with almost no disk. With distortion, poly3 was chosen for 7 of 9 panels,
   and held-out residuals stayed within 0.12 px. The corners stay worse because tiles must lie wholly inside a
   panel, so they reach only about 65 % of its radius, where cubic distortion is small; with 400 px panels the
   distortion was not even measurable there. T-DE: no part of the disk is visibly wrong (ΔE*ab median 0.17,
   99.9th percentile 0.63, against 2.3).
5. **Measured, real data.** Twelve of the 25 September colour panels were placed back onto that night's mosaic
   (the same scale; no separate infrared luminance exists). 11 placed, mostly with poly3, as D15 measured;
   held-out residuals 0.17–1.24 px, 0.58 px rms over all — inside the 1–2 px target. Red was moved 0.11–0.16 px
   on five panels. It took 36 s, with 6.7 GB peak for the full-size colour canvas at k = 1. Evidence:
   `/Volumes/Astro/moontest_04102026/phase6/`.
6. **Application.** Building colour now places it, which needs the luminance mosaic. `/api/match` places colour
   again, then `alignment.measure` checks the placed result independently. Control points place a single-image
   colour set. A colour set may feed both streams (D9), and is then its own colour. The placement's summary
   stands in for the old match in the interface: matrix K, held-out residuals as the statistics, tile samples for
   the errors view. `lrgb.register` is gone. A project from before keeps its global match and colour mosaic until
   colour is placed again.

Tests: `colour_checks.py` (T-PLACE with drift and with distortion, T-DE, T-COARSE, scale estimate and photometry,
a set feeding both streams). In `lrgb_workflow.py`: the global-registration test now places colour onto a
luminance 2.29× finer and turned 8°; "a failed set keeps the other mosaic" checks both new cases (no luminance:
colour cannot start; colour fails: luminance stays); the local-error check runs `alignment.measure` on a colour
mosaic matched the old way, because placement now corrects that error. All twelve suites pass; 119 tests.

### Phase 7 — 2026-10-05

The interface edits chains: streams of sets, building and placing colour, the chain editor, previews at 1:1.

1. **Pages.** Vanilla ES modules, no build step, on LunarAtlas's stylesheet: `mosaic.js` (the pages, requests and
   the canvas), `chains.js` (the chain editor), `previews.js` (preview rectangles) and `save.js` (autosave). The
   server serves exactly these files. Import shows the two streams. The luminance stream takes any number of sets,
   each with a name, optics (pixel size, focal length, Barlow, aperture) and its panels. A set without optics gets
   a measured scale to confirm (D10). The sampling of colour against luminance is said in words ("The colour
   panels sample 2.0× coarser than the luminance"). The canvas lower bound comes with the 400 Mpx prompt and a
   smaller canvas when the finest set is oversampled. The colour set can feed both streams.
2. **Build and match.** The build page lists the nine builder steps, then colour placement with progress per
   panel. The status bar's chip reads "Match 0.11 px · worst 0.12 px" from the placement report, followed by the
   independent check. Its popover lists every panel: placed, or the reason it was not; the pose model, the tiles,
   the held-out residual and the RGB-shift decision. The build card lists the luminance panels the builder left
   out, with the reason. The `resolution` view has a legend: each set's tier, pixel scale, measured detail and
   share of the disk.
3. **Chain editor.** It shows the luminance and colour chains, the merge strength, the final chain and "keep space
   black". Blocks show Appendix A's parameters in the owner's units. These come from `/api/blocks`
   (`block_catalog.py`), which derives them from the engine's own ranges, so the two cannot disagree. Wavelets show
   each PixInsight bias with its gain beside it; the unsharp mask uses Photoshop's percent, pixels and levels;
   Curves are drawn over the histogram of the image arriving at the block. The add menu greys out a block that a
   chain cannot take, with the reason. Blocks can be reordered by dragging the grip or with Alt+↑/↓ (the focus
   stays on the block), switched off, duplicated and deleted. Every block has a limb fade. Readouts come from
   `/api/analysis`:
   - wavelets: the layer the detail reaches, and the change in fine detail;
   - white balance: the gains and ratios, and the sentence that PixInsight sometimes also rescales brightness
     where this block never does;
   - RGB shift: every estimate and the decision;
   - deconvolution: the first dark ring, and a warning when it is not first in its chain.

   White-balance references are drawn on the mosaic and stored in canvas pixels. New blocks open; the others stay
   collapsed until chosen.
4. **Previews.** P or the toolbar draws a rectangle on the view; it is named, saved with the project, listed
   under the canvas and shown at 1:1 through `/api/render`, in a viewer sized to it beside the layers panel. A
   preview renders again once whenever what it shows changes (the chain, the view's geometry, the mosaics or the
   placement), not on a rename; a superseded render is dropped. Turning the view moves each preview with its
   centre. Below 75 % zoom the canvas says it is approximate.
5. **Saving (BUGS.md bug 11).** Every change goes out through one queue, carrying the revision it was made on.
   A chain edit shows at once and is saved 300 ms after the last one. On a conflict or a lost connection the edit
   stays on screen and is sent again, newer edits winning, and the header says so. A chain the server refuses (an
   impossible combination of values) also stays on screen with the reason, but waits for the next edit instead of
   going out every 2 s.
6. **Export** as before, plus the chain as a file: saved from the export dialog, or loaded into a project, with
   fresh block ids and the project's own previews.
7. **Compatibility layer removed (item 8).** `/api/edits`, `Scene.render_edits` and `legacy_edits` are gone, and
   `lrgb_workflow.py` drives the chain API. The migration notes show once, in the layers panel.
8. **Statistics restart at the first changed block (item 9).** For each block, the proxy and probe pixels after
   it are kept, keyed by the chain up to that block, in a 2 GB cache that drops the least recently used. Measured
   on a synthetic 2048 px disk with a 30-iteration deconvolution first in the luminance chain:

   | Pass | Time | Blocks run |
   |---|---|---|
   | First statistics pass | 7.49 s | all |
   | Contrast edit (final chain) | 0.66 s | contrast only |
   | Wavelets edit | 1.62 s | wavelets and the blocks after it; the deconvolution is not run again |

   Both edits give the same statistics as a fresh pass. On the 4 October mosaic, with a 100-iteration
   deconvolution first, the first pass took 38.1 s. A contrast edit then took 1.9 s (a fresh pass 39.2 s), and a
   wavelets edit 3.4 s (fresh 32.9 s). The proxy pass is reused whole there, because detail blocks are skipped in
   it. The statistics were identical, and peak memory was 2.1 GB. Evidence:
   `/Volumes/Astro/moontest_04102026/phase7/`.
9. **Three faults the journey found.**
   - A quarter turn made the view 2 px too large. Cos 90° is 6·10⁻¹⁷ in floating point, which tipped the
     bounding box's floor and ceil. The corners are now rounded to 10⁻⁶ px, and a 1008 px square turns into
     1008 × 1008.
   - The autosave called the browser's `setTimeout` as one of its own methods, which browsers refuse.
   - When a page went away (a reload, a superseded preview), the server answered the dead connection with an
     error and printed two tracebacks. It now lets the connection go.
10. **Numbers are written the English way** (6,721 px; 45.2 Mpx), like the text around them; the browser's
    locale had mixed "6.721 px" with "0.27 GB".

Tests: `ui_journey.py` is the plan's browser journey, in headless Chrome over the DevTools protocol, using Node's
own WebSocket and no packages. On synthetic data it imports two luminance sets at different scales and a colour
set, builds, reads the chip and the per-panel table, and adds wavelets, white balance, curves and contrast. It
draws two previews (the viewer is at 1:1); a parameter change renders both again; a quarter turn moves both with
their centres, to within a pixel's rounding. It checks that the resolution view is tinted by set and that its
legend names both tiers, reorders with Alt+↓/↑, reloads (server state, editor and previews identical), loads a
chain file, exports a TIFF, then closes and reopens; no console errors. It takes about 20 s; with
`JOURNEY_SHOTS=<folder>` it saves screenshots. Its sets are as small as the builder accepts: smaller panels fell
below 150 features (luminance) or four tiles (colour) and were left out, as they would be in real data. `ui_autosave.py` was rewritten for `save.js`:
10 scenarios, including refusals. `scene_checks.py` covers the routes and `chain_checks.py` the prefix cache.
`run_all.py` now prints each suite's test count. All thirteen suites pass: 111 unittest tests, and the 14
regressions of `review_regressions.py`.

### Phase 8 — 2026-10-05

The commands catch up with the interface, and the documentation with the code.

1. **`lunarmosaic.py`.** These commands now do what the interface does:
   - `new` takes repeated `--luminance PATH…` groups and one `--colour PATH…` group. The `--optics
     PIXEL,FOCAL[,BARLOW[,APERTURE]]` and `--label NAME` after a group belong to it (a custom argparse action).
     `--colour-both` makes the colour set feed both streams.
   - `scales` measures the scale of sets without optics, and `scales --confirm` accepts it.
   - `build` builds the luminance and places the colour. It takes the canvas options the import page confirms, and
     shows the build as it runs.
   - `chain export|import PROJECT FILE` saves or loads the chain.
   - `export` works as before, now on the chain API. It had been broken since phase 7 removed the old adjustments,
     and no test ran it.
   - A bare project path opens the interface.
2. **Documentation.**
   - `lunar_finish.py --help` describes chains, and its settings groups say they make lunar_finish's default
     chain.
   - `.env.example` and COMMANDS.md are regenerated. COMMANDS.md now lists `lunarmosaic.py`, with each
     subcommand, and `colour_place.py`.
   - `tools/update_docs.py` documents the built-in defaults (a `LUNAR_TOOLS_DEFAULTS=1` switch in
     `tool_settings.py`), so the reference no longer shows one machine's `.env`: it had shown `--wavelets
     pixinsight` as the default. Its `--check` only compares.
   - README.md, mosaic/README.md and LRGB.md describe the application as it now is.
   - DESIGN_DECISIONS.md and IMPLEMENTATION_STATUS.md are marked historical, and the table at the top of this
     section keeps only what remains open.
3. **Two faults the acceptance run found.**
   - lunar_finish's north-up never ended on a mosaic whose panel names carry no capture time, when run from the
     session folder. moon_atlas's `capture_time` looked such a panel up in the working folder's layout, which is
     the mosaic's own, again and again. Fixed in moon_atlas at the owner's request: a panel's time now comes from
     its name alone (recorded in `moon_atlas/UPSTREAM.md`, to report upstream; regression check
     `capture_time_from_a_layout_ends`).
   - `design_mockups/render_mockups.py` assumed the 101-panel night: 62 panels in its placement frame, and a
     residual for every panel.

   `compare_crops.py` also needs `--size` on panels this small; that option is documented, so it is not a fault.
4. **Acceptance: `cli_checks.py`** runs every command in COMMANDS.md on a synthetic session:
   - the classifier;
   - the builder: layout only, full, and with both movies;
   - colour placement;
   - lunar_finish: with flags, with a movie, and with a chain file;
   - every `lunarmosaic.py` command, including the interface over HTTP;
   - the diagnostic tools and the mockup renderer;
   - `update_docs.py --check`.

   It fails if a documented command was not run or printed a traceback, and takes about 5 minutes.

All fourteen suites pass: 125 unittest tests, and the 15 regressions of `review_regressions.py`.

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
| The application on chains: renders and timings against the old renderer | `/Volumes/Astro/moontest_04102026/phase4/` |
| Colour placed per panel: real 25 September panels, report and composite | `/Volumes/Astro/moontest_04102026/phase6/` |
| The interface: statistics restart on the 4 October mosaic, browser-journey screenshots | `/Volumes/Astro/moontest_04102026/phase7/` |
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
