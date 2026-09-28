# Mosaic Tool: Specification (v0.3, verified against the data)

Status: phases 1–4 built and run: classifier and all nine builder stages (0–8); see §14 for measured results · Date: 2026-09-27 · Target machine: Apple M4, macOS, native arm64 · Location: `/Volumes/Astro/scripts/mosaic/`

Two standalone Python scripts plus one shared module:

| File | Job |
|---|---|
| `panel_classifier.py` | Scores the quality of every image in one or more folders and writes a JSON manifest. Works on any image set, not only lunar images. When the images overlap, it can also rank them on shared terrain, which is far more reliable. |
| `mosaic_builder.py` | Registers overlapping panels, matches their brightness, picks the sharpest panel for each region, blends them, neutralises the sky background to black, colour-balances the disk and writes one uncompressed 16-bit TIFF, in a few minutes. Shows live progress in the console and in a preview window. |
| `mosaic_common.py` | Shared code for image I/O, luminance, masks, feature matching and progress logging. Both scripts import it; it is not run directly. |

Priorities, in order: **output quality → visibility into what the tool is doing → reusability → speed.** Speed is last, but a first run must still finish in **≤ 3 min** (§8).

---

## 1. What changed

### 1a. v0.3: measured against the actual panels

Every claim in v0.2 was checked on the 101 panels with prototype code (numbers in §2 and in `panel_quality_report.md`, section *Verification and corrections*).

| # | v0.2 said | v0.3 | Evidence |
|---|---|---|---|
| 1 | v1 Laplacian variance is badly limb-biased; a brightness-normalised band-pass score fixes it | **Reversed.** There is no limb inflation to fix, and brightness normalisation does not help. The authoritative quality measure is the **same-terrain comparison** of overlapping panels. For a single image, **Tenengrad inside the subject mask** is best | Rank correlation with same-terrain detail: masked Tenengrad 0.84, raw band-pass 0.82, v1 Tenengrad 0.74, masked Laplacian 0.73, v2 local-normalised band-pass 0.66, v1 Laplacian 0.62. Limb panels really are sharper on shared terrain (median 1.14 vs 0.97), and v1 ranked them no higher than that |
| 2 | Subject mask from "darkest pixels = background" | Only when a sky mode is actually present; otherwise the whole frame (minus border) is subject | 34 panels have no sky; their darkest 0.1 % is ≥ 3,444 ADU, so the old rule would have cut dark maria |
| 3 | `noise_sigma` from the flattest tiles flags noisy stacks | Measure noise from sky pixels when present; otherwise report `null` | On these unsharpened stacks the flat-tile value tracks texture, not noise (rank correlation 0.77 with sharpness); max/median is only 1.46, so the reject rule could never fire |
| 4 | Filename `apNNNN` = stack count | It is AutoStakkert's **alignment-point count**; field renamed `ap_count` | r = 0.98 with lunar coverage |
| 5 | Global solve with the reference panel "fixed as identity" | The reference must be a **hard** constraint (its unknowns eliminated), not a weighted prior | A soft anchor shrank every other panel by about 1 % (scales 0.988–0.992); the hard anchor gives 0.997–1.003 |
| 6 | Gain compensation: per-channel gain | Per-channel **gain + offset** | Sky pedestals differ per channel (R ≈ 230, G ≈ 370, B ≈ 300 ADU) and per panel; a gain-only model leaves steps in dark areas |
| 7 | Background: pure black fill | **Background neutralisation**, then black outside the disk (your decision) | See Stage 8 |
| 8 | Deflate compression | **Uncompressed** (your decision) | — |
| 9 | Benchmark against `…_stitch.tiff` | Dropped. Both stitch files (Microsoft ICE 2.0.3, LZW) were moved to the Trash at your request | Acceptance criteria rewritten (§10) |
| 10 | Disk is the blocker (727 MiB free) | **Resolved**: 42 GiB free on the internal disk, 1.1 TiB on `/Volumes/Astro` | `df`, 2026-09-27 |
| 11 | Mount type unknown | **EQ mount**. Rotation between panels is still non-zero (0.42° spread) and scale varies 0.56 %, so the similarity model stays | Global solve over 693 pairs |
| 12 | Per-channel alignment out of scope | Confirmed unnecessary | R–G and B–G offsets < 0.11 px (17 panels, rechecked) |
| 13 | First run ~20–35 min | **Target ≤ 3 min** on the M4 (ICE does this set in a few minutes on an older PC). Stage estimates are now benchmarks | The v0.2 estimates were far too pessimistic: the full-resolution blend measured 0.31 s per panel. The only slow step, all-pairs matching (111 s), is replaced by a global feature index (~10 s) with the same recall. See §8 |
| 14 | Fine alignment by full-resolution SIFT | **Tile-based phase correlation** at full resolution, guided by the coarse solve | Sub-pixel, and a few ms per tile instead of re-running SIFT |
| 15 | Blend every panel | **Skip panels that win no region** in stage 6; they would contribute zero weight anyway | Same output, less work |
| 16 | Background only | Also a **disk colour balance** (your decision): neutral highlands by default | Measured cast after pedestal removal: B/G ≈ 1.055, R/G ≈ 0.966 |
| 17 | Panels split over two folders | **Merged**: all 101 panels are back in `panels/`; tiers live in the manifest, not in folders | The split followed the v1 tiers, which were wrong in both directions |
| 18 | Script location open | `/Volumes/Astro/scripts/mosaic/`, outside any dated session folder | Your choice of a `scripts` folder; the drive root keeps it reusable across sessions |

### 1b. v0.2: changes from the chat version (kept for history)

| # | Earlier plan | v0.2 | Why |
|---|---|---|---|
| 1 | Rank panels once, globally | Global scores are only a **pre-filter**; the authoritative comparison is **per region**, on the same terrain | Confirmed by measurement: sharpness varies *within* panels (pairwise ratios deviate from a single per-panel score by 6 % RMS in amplitude, about 3× the per-panel uncertainty), so per-region choice matters |
| 2 | Where panels overlap, prefer the higher tier | **Per-pixel sharpness map**; each region comes from its locally sharpest panel, multi-band seams join the regions | Averaging a sharp panel with a soft one gives a soft result |
| 3 | Homography / OpenCV `Stitcher` | **Similarity transform** (4 degrees of freedom) per panel, solved **globally** by linear least squares | Orthographic projection at telescope focal lengths; global solve avoids drift over ~100 panels |
| 4 | Match features once | **Coarse to fine**: find pairs on proxies, refine accepted pairs at full resolution | Proxy-only alignment measured 0.98 px RMS; full-resolution refinement is needed for the < 0.5 px target |
| 5 | "16-bit throughout" | 16-bit data, float32 processing; only feature detection on 8-bit proxies | OpenCV SIFT needs 8-bit input; OpenCV's multi-band blender only takes signed 16-bit, so the tool has its own float32 blender |
| 6 | Preview after each panel | Preview built from coarse pyramid levels (the real blend at low resolution), plus registration views | Visibility during long stages |
| 7 | (not covered) | Cache features, matches and transforms on disk | Re-run blending in minutes |
| 8 | Move files into tier folders | Tools **never move files**; they accept several folders or a manifest | The existing split is wrong in both directions (see report) |
| 9 | (not covered) | Preflight checks for disk, RAM and display | — |
| 10 | "16-bit TIFF + 8-bit preview" | Output is 16-bit TIFF only | Your choice |
| 11 | Panel set undecided | Default **all 101 panels**; weaker panels only win where nothing sharper covers the area | Coverage depth median 5; see §4 Stage 6 |

---

## 2. Measured environment and data facts

| Item | Value |
|---|---|
| Machine | Apple M4, 4 performance + 6 efficiency cores, 24 GB RAM |
| Python | 3.14.5 (Homebrew), native arm64 |
| OpenCV | 4.13.0: NEON baseline, Accelerate LAPACK, GCD threading, Cocoa GUI, libtiff 4.7.1; SIFT available; `IMWRITE_TIFF_COMPRESSION` and `IMWRITE_TIFF_PREDICTOR` supported |
| Other libraries | numpy 2.4.6, Pillow 12.2 installed; scipy, tifffile, scikit-image **not** installed and not needed |
| Free disk | 42 GiB internal · 1.1 TiB on `/Volumes/Astro` |
| Mount | Equatorial |
| Panels | 101 TIFFs from AutoStakkert (`_lapl4_apNNNN`: Laplace quality estimator, noise robustness 4, NNNN alignment points). Uncompressed, 16-bit, 3-channel true colour, sRGB ICC profile embedded, no Software tag |
| Sharpening | **Wavelet-sharpened** (confirmed by you, 2026-09-27; my earlier "none detected" was wrong). The mosaic is therefore already sharp; a light unsharp mask is added by you in Photoshop at the very end |
| Panel sizes | 3720–3816 × 2088–2112 px, 28 distinct sizes |
| Saturation | 87 of 101 panels have pixels ≥ 98 % of full scale; at most 9,099 px (0.12 %) per panel. Channel maxima range 43,289–65,535 (stacks not normalised to a common level) |
| Stacking-edge artefact | AutoStakkert leaves a 2–6 px band on every edge of every panel: the outer 1–2 px are green only (R = B = 0), then a 1–2 px bright band (1.4–2.4× the interior). Measured automatically per panel (`mosaic_common.edge_artifact_widths`); the builder crops the widest edge + 3 px (6–9 px here). Found after your observation, 2026-09-27 |
| Colour registration | R–G and B–G offsets < 0.11 px (17 panels, rechecked) |
| Field falloff | Detail drops from frame centre to edge: 0.95× at 600–750 px from the edge down to 0.72× within 150 px; left/right edges (farther from centre) 0.63–0.72×, top/bottom 0.84–0.87×. The finest scale (σ 0.5–1 px) is noise-dominated |
| Disk colour | After sky-pedestal removal, blue cast: B/G ≈ 1.055 (1.02–1.07), R/G ≈ 0.966 (0.95–0.99), the same on highlands and maria; varies ~3 % between panels |
| Location | All 101 panels in `moon/panels/` (merged 2026-09-27) |
| Sky background | 12 panels contain sky. Not neutral: median R ≈ 230, G ≈ 370, B ≈ 300 ADU (MAD ≈ 150–220). R and B clipped at 0 on 5–25 % of sky pixels (G never). Sky brightens towards the limb: quadrant medians 250–600 ADU, and the 3–12 px band outside the limb is 210–280 ADU brighter than far sky (scattered light) |
| Overlap graph | Brute force at 0.25× proxy: 704 of 5,050 pairs accepted. `panel_classifier.py` (global index + the ≥ 5 % overlap rule): 686 pairs, 7–20 partners per panel. One connected component of 101 |
| Geometry (proxy-level solve) | Rotation −0.10° to +0.32°, scale 0.9971–1.0027 relative to the reference; 0.98 px RMS after dropping 11 inconsistent pairs |
| Canvas | ≈ 14,661 × 13,309 px (≈ 195 Mpx) |
| Coverage depth | ≥ 2 panels on 99.7 % of the lunar area, ≥ 3 on 96.6 %, median 5, max 13 |
| Filename "target" field | `HIP 113781 (82 Aqr)` is a star designation, so it is treated as a free-text label and never as the subject |

**New dependencies: none.** If a dependency is ever added, it goes into a venv, because Homebrew Python is externally managed.

---

## 3. `panel_classifier.py`

### Purpose
Scores the quality of any folder of images, without assuming the images are lunar. Produces a manifest that is useful on its own (for example, to cull a bad batch) and that `mosaic_builder.py` can optionally use.

It has two scoring modes:
- **Single-image** (always available). Fast, but it compares different content, so treat it as a rough pre-filter. On this dataset Tenengrad agrees with the same-terrain ranking at a rank correlation of 0.84.
- **Relative** (`--relative`, when the images overlap). Finds overlapping pairs with the same code as builder stages 1–2 and compares detail on shared terrain. This is the authoritative mode for mosaic panels.

### CLI
```
python3 panel_classifier.py <folder|file> [...] -o manifest.json
    [--recursive] [--ext tif,tiff,png,fits]
    [--border 32]        # px trimmed from every edge (stacking-edge artefacts)
    [--erode 40]         # px removed inside the subject mask (drops limb/edge gradients)
    [--tiers 10,25,75]   # percentile cut-points → Poor / Marginal / Good / Excellent
    [--relative]         # also rank by same-terrain detail (needs overlapping images)
    [--radius-tol 200]   # px: compare only terrain at about equal distance from both frame centres
    [--workers N]
```

### Per-image processing
1. Load the image in its native bit depth and convert it to luminance (Y = 0.2126 R + 0.7152 G + 0.0722 B), as float32 in the range 0–1. The panels carry an sRGB profile but come straight from the stacker, so the values are treated as-is; this does not affect ranking.
2. **Subject mask.**
   - Look for a sky mode: a histogram peak within the bottom 2 % of full scale that holds ≥ 5 % of pixels. If one exists, the background is its median + k·MAD, and the threshold is background + 5σ, applied to a lightly smoothed image (σ 8 px) so that sky noise does not break up the mask. Keep the largest connected component.
   - If there is no sky mode, the whole frame is subject.
   - Then remove the trim border, a band `--erode` px wide along the subject edge (only where the edge is a real limb, not the frame edge), and saturated pixels (≥ 0.98 of full scale in any channel), dilated by 5 px.
3. Compute the metrics inside the mask:

| Metric | Definition | Why |
|---|---|---|
| `tenengrad` | Mean Sobel (3×3) gradient energy inside the subject mask, raw (unnormalised) values | Single-image tier metric: rank correlation 0.84 with same-terrain detail |
| `laplacian_var` | Variance of the 3×3 Laplacian inside the mask | Continuity with v1 (0.73) |
| `bandpass_energy` | Mean DoG energy (σ 1.0 → 2.5), **not** brightness-normalised | Diagnostic (0.82). Normalising by brightness made it worse (≈ 0.66), so that variant is dropped |
| `relative_detail` | `--relative` only: detail amplitude relative to the batch median, from the pairwise least-squares solve; with a bootstrap standard error | Authoritative. Uncertainty ±2 % typical, ±6 % worst on this dataset. Agreed with a blind visual check on 5 of 6 pairs |
| `relative_detail_fine`, `_coarse` | The same solve per scale (σ 1.0 → 2.5 and 2.5 → 6 px); `relative_detail` is their geometric mean | A large fine/coarse gap flags grain or streaks |
| `noise_sigma` | Robust σ of sky pixels (MAD × 1.4826) when a sky mode exists; otherwise `null` | The flat-tile method measured texture, not noise |
| `coverage_pct` | Mask area / frame area (before erosion) | Composition. Not a defect by itself |
| `saturated_pct`, `mean`, `std`, `sky_rgb` | — | Exposure sanity checks; `sky_rgb` feeds the background neutralisation diagnostics |

4. **Tier** uses the percentile of `relative_detail` when available, otherwise of `tenengrad`. A separate `reject` flag is set only for clear failures: < 5 % coverage, unreadable file, `noise_sigma` > 3× the batch median (only when measurable), or > 2 % saturation. Low sharpness alone never triggers a reject; §4 stage 6 explains why.
5. Filename metadata (date, time, label, alignment-point count) is parsed best-effort by regex. If a filename does not match, the image is still processed.

### Manifest (JSON)
```json
{
  "schema": "panel-manifest/2",
  "created": "2026-09-27T14:02:11",
  "params": { "border": 32, "erode": 40, "tiers": [10, 25, 75], "relative": true },
  "images": [
    {
      "path": "/abs/path/2026-09-25-2117_0-….tif",
      "id": "2117_0",
      "width": 3768, "height": 2104, "channels": 3, "dtype": "uint16",
      "metrics": { "tenengrad": 0.0, "laplacian_var": 0.0, "bandpass_energy": 0.0,
                   "relative_detail": 1.00, "relative_detail_se": 0.02,
                   "relative_detail_fine": 1.00, "relative_detail_coarse": 1.00,
                   "noise_sigma": null, "coverage_pct": 0.0, "saturated_pct": 0.0,
                   "mean": 0.0, "std": 0.0, "sky_rgb": null },
      "tier": "Good", "tier_source": "relative_detail", "reject": false, "reject_reason": null,
      "meta": { "date": "2026-09-25", "time": "21:17.0", "label": "HIP 113781 (82 Aqr)", "ap_count": 11597 }
    }
  ]
}
```
The classifier also writes a Markdown report next to the manifest, in the same layout as `panel_quality_report.md`.

---

## 4. `mosaic_builder.py`

### CLI
```
python3 mosaic_builder.py <folder|manifest.json> [...] -o mosaic.tif
    [--manifest manifest.json]   # optional: exclude rejects, break ties, label the log
    [--exclude-tier Poor]        # optional hard filter (default: none, use all panels)
    [--proxy-scale 0.25]         # pair discovery; 0.25 found all 704 pairs here
    [--model poly3|poly2|affine|similarity]    # default poly3 (gentle cubic warp per panel; §14)
    [--border auto|N]            # px cropped at every frame edge; auto = measured stacking artefact + 3 px
    [--gain scalar|blocks]       # default scalar (per channel, gain + offset)
    [--interp lanczos|cubic]     # default lanczos
    [--levels auto]              # multi-band pyramid levels
    [--pair-search index|all]    # default index (global feature index); all = brute force, 5,050 pairs
    [--background neutral-black|neutral|keep]   # default neutral-black
    [--limb-feather 12]          # px ramp from disk to black
    [--colour-balance highlands|disk|none]      # default highlands
    [--compression none|lzw|deflate]            # default none
    [--workers N]                # default: CPU count − 1
    [--cache-dir DIR] [--rebuild]
    [--no-preview] [--log run.log]
```

### Pipeline

**Stage 0: Preflight**
- Read every file header: size, bit depth, channels. Mixed bit depths are converted to 16-bit, with a warning.
- Check free disk space against the output size (uncompressed: width × height × 6 bytes, ≈ 1.2 GB here) plus the cache. **Abort before any work starts** if space is too low.
- Estimate peak RAM (see §9) and warn if it exceeds 60 % of physical RAM.
- Check that a display is available for the preview. If there is none, continue with a warning, as if `--no-preview` were set.

**Stage 1: Proxies and coarse features**
- Panels are read by a thread pool (OpenCV and numpy release the GIL). Measured: read + proxy + SIFT for all 101 panels in 15 s with 8 threads.
- Each panel's full-resolution luminance is kept in RAM as float16 (≈ 16 MB per panel, 1.6 GB in total), so stages 4 and 6 need no second read. The RGB pixels are read again only in stage 7.
- For each panel: luminance → border trim → resize to `--proxy-scale` (INTER_AREA) → robust stretch (0.5–99.5 percentile) → CLAHE → 8-bit.
- SIFT with the feature mask (light erosion, excluding saturated pixels). Settings: `contrastThreshold` 0.02, at most 4,000 features at 0.25× (8,000 at 0.5×).
- Panels with fewer than 150 keypoints are marked `unplaceable (too few features)` and skipped. Measured at 0.25×: minimum 819 (`2129_7`, 18 % coverage), median 3,983, so no panel is near the limit.
- Cache keypoints and descriptors. OpenCV's SIFT descriptors are float32; they can be cached as uint8 (values are ≤ 255 after SIFT's clamp) and converted back for matching.

**Stage 2: Pair discovery (proxy resolution)**
- **Candidates** (`--pair-search index`, default): put every panel's descriptors into one FLANN k-d index. Each panel queries it once, and each descriptor votes for the other panel holding its nearest neighbour (ratio 0.8 against the next other-panel neighbour). A pair becomes a candidate if it has ≥ 4 votes **or** is among the panel's 20 highest-voted partners. Measured: ~1,200 candidates in ~10 s, containing 702–704 of the 704 pairs that brute force finds; the misses are weak pairs (≤ 44 inliers).
- **Layout closure**: after the first global solve (stage 3), any panel pair whose predicted footprints overlap by ≥ 5 % but was not a candidate is verified as well, then the solve is repeated. This catches the last misses.
- `--pair-search all` tries all 5,050 pairs by brute force (measured 111 s with 8 threads); it is a fallback for unusual datasets.
- Match each candidate with a Lowe ratio of 0.75 and a mutual-best check, using a small FLANN index per pair (measured: stage 2 in 28 s instead of 57 s with brute force; 680 vs 681 pairs accepted).
- **Determinism:** RANSAC draws from OpenCV's thread-local random generator, so without seeding ~30 % of pairs gave different inlier counts between identical runs. The generator is seeded per pair (and before the global index): identical input gives identical output (verified: two runs, identical poses and pair sets).
- Verify geometry with RANSAC similarity (`estimateAffinePartial2D`), threshold 1 px at 0.25× proxy scale.
- **A pair is accepted only if all of these hold:** ≥ 30 inliers at 0.25× (≥ 40 at 0.5×) · inlier ratio ≥ 25 % · scale between 0.97 and 1.03 · overlap ≥ 5 % of the smaller panel · the inliers' convex hull covers ≥ 2 % of the overlap.
- Measured on this dataset: 704 accepted; the rest are almost all "no overlap" (4,186 had < 20 mutual matches, 160 too few inliers). Accepted pairs had scales 0.996–1.007 and rotations within ±0.3°.
- Every accepted pair is logged. Rejected pairs are counted by reason, and `--log` records each one.

**Stage 3: Global alignment**
- Build a graph with panels as nodes and accepted pairs as edges. Use the **largest connected component**; other components or isolated panels are reported by name as not placed.
- Reference panel: the one with the most connections, fixed as identity **by elimination** (its four unknowns are substituted, not penalised). A soft penalty lets all other panels shrink to reduce the residual; this was measured at about 1 %.
- Initial poses come from a maximum spanning tree weighted by inlier count.
- **Global solve.** A similarity transform is linear in (a, b, tx, ty): `x' = a·x − b·y + tx`, `y' = b·x + a·y + ty`. All inlier correspondences go into one linear least-squares system with 4 × (N − 1) unknowns, solved with IRLS using Huber weights.
- **Consistency loop.** Pairs whose residual RMS exceeds max(1.5 px, 3 × median pair RMS) are dropped and the system is solved again until stable. Dropped pairs are reported. (At proxy level, 11 of 704 pairs were dropped and the RMS went from 1.15 px to 0.98 px.)
- **Two residuals are reported.** The *keypoint* RMS includes the jitter of individual proxy keypoints (~1.6 px at full scale). The *layout* RMS compares each pair's own fitted transform with the global poses, so it measures layout error only; it drives the consistency loop and the outline colours. Measured (phase 2): layout RMS 0.62 px, 37 panels < 0.5 px, 63 at 0.5–1 px, 1 at 1.08 px; 59 of 681 pairs dropped (pair RMS 1.5–3.9 px); rotation −0.07° to +0.33°, scale 0.9982–1.0026; canvas 14,810 × 13,446 px. Stage 4 must bring the layout below 0.5 px.
- EQ mount: rotations are expected to be small but not zero (measured −0.10° to +0.32°). A rotation outside ±1° is logged as a warning, because it would suggest a bad match.

**Stage 4: Fine alignment (full resolution)**
- Only for accepted pairs: place 6–12 tiles of 256 × 256 px on textured, unsaturated terrain in the overlap. For each tile, warp the partner's luminance with the coarse transform and measure the residual shift by **phase correlation** (sub-pixel, a few ms per tile). Tiles with a weak correlation peak are discarded. Each tile yields one full-resolution correspondence (tile centre ↔ centre + shift).
  **Implementation note:** `cv2.phaseCorrelate` overwrites its input arrays (it applies the window in place). Always pass copies (`mosaic_common.phase_shift` does this); passing views into cached panels corrupts them silently. This bug invalidated the first prototype's same-terrain numbers.
- Repeat the global solve with these correspondences (IRLS, same consistency loop). Iterate once more if the RMS is still falling.
- Fallback for pairs with too few good tiles: guided SIFT on the overlap (candidates within 8 px of the prediction).
- **Target: RMS < 0.5 px overall, and no panel above 1.0 px.** Report the RMS per panel. Any panel above 1.0 px gets a warning (it is still placed). Proxy-level alignment reaches about 1 px, so this stage is required; the 0.5 px target is not yet demonstrated.

**Stage 5: Photometric matching (gain + offset)**
- For each overlapping pair and each channel, sample matched robust statistics in the overlap (the 10th, 50th and 90th percentiles over masked, non-saturated pixels on common terrain).
- Solve for per-panel, per-channel gain g and offset o by minimising
  `Σ ((g_i·v_ij + o_i) − (g_j·v_ji + o_j))² + λ_g Σ (g_i − 1)² + λ_o Σ o_i²`,
  with the reference panel fixed at g = 1, o = 0.
- `--gain blocks` fits an 8 × 8 grid of smoothed gains per panel, for vignetting and gradients. It is evaluated during validation and becomes the default only if clearly better.
- Gains are applied in float32. The final scaling keeps the 99.99th percentile ≤ 65535. The number of clipped pixels is reported.

**Stage 6: Region selection (the main quality lever)**
- For each panel, compute a **local sharpness map**: DoG band-pass energy (σ 1.0 → 2.5) on the full-resolution luminance held from stage 1, divided by local brightness², then reduced to 0.25× and smoothed with a Gaussian (σ 32 full-res px). Computing the band-pass at full resolution matters: on a proxy the band would measure coarser detail than the seeing-limited structure that differs between panels. Normalising by local brightness is correct here, because panels are compared on the same terrain; it cancels gain differences. (It is the cross-terrain comparison in the classifier that it distorts.)
- Use both scales, fine (σ 1.0 → 2.5) and coarse (σ 2.5 → 6), combined as a geometric mean, as in the classifier.
- **Field falloff** (measured, §2) is continuous from centre to edge, so no fixed taper width can capture it; the local sharpness map already sees it and will prefer a more central view of the same terrain (coverage depth is 5 at the median). An **edge taper** (distance transform to the panel's valid-region edge, ramping over 150 full-resolution px) is kept only so seams never sit right at a panel edge, where the stacks are 0.72× and grain and streaks are common.
- **Known limitation:** grain and diagonal streaks can be scored as detail; in the blind check, one of six pairs went the wrong way for this reason. If a streaky panel wins a region, it shows up in the label-map preview. A noise-robust measure (e.g. keeping only detail that is coherent across three or more overlapping panels) is a candidate for v2.
- Warp each map to the canvas and keep a **running maximum and argmax**, one panel at a time. Keeping all maps at once would need about 5 GB at 0.25× canvas (about 20 GB at 0.5×).
- The **label map** is the argmax, cleaned with a mode filter so no small speckle regions remain.
- **Rule (your requirement, 2026-09-27): where a higher-quality panel covers a piece of terrain, lower-quality panels contribute no detail there.** Quality is judged locally (the sharpness map), not per panel and not by placement order, so a panel that is weaker overall still wins where it happens to be the sharpest view (e.g. nearer its frame centre). At a seam, only broad brightness (the coarsest pyramid levels) is blended across a transition zone to hide the join; fine detail switches from one panel to the other without averaging. The stage 3 preview composite is a plain *average* of all panels. It exists only to show alignment and is not how the mosaic is made.
- The result: each region comes from its sharpest panel. A "Poor" panel wins only where it is the only panel, or clearly the best one, covering that terrain. Measured here: dropping the bottom quarter of panels would lose only 0.26 % of the area, so late panels will rarely win; including them costs runtime, not quality.

**Stage 7: Multi-band blending (full resolution, float32)**
- Panels that won no region in stage 6 are **skipped**: their label mask is empty, so they would add zero weight at every level. The log lists them as `dominated (no region)`.
- Process the remaining panels one at a time (measured: 0.31 s per panel, including the read):
  - Restrict the work to the bounding box of the panel's label region, dilated by the pyramid's support (2^levels px), rather than the whole panel.
  - Warp the full-resolution 16-bit panel **once**, straight into its bounding box on the canvas (transforms are composed, never chained), using Lanczos-4. Clamp negative overshoot at the limb.
  - Build its Laplacian pyramid. Bounding boxes are aligned to a 2^levels grid so the pyramids line up.
  - Weight it by the Gaussian pyramid of its label mask.
  - Accumulate into the global pyramids.
- Levels are set automatically from the typical width of the overlaps, usually 6–7.
- Result: low frequencies (brightness) blend across a wide zone and high frequencies (detail) switch sharply at the seam. No ghosting, no softening.
- **After each panel, the preview is updated** by collapsing only the coarse pyramid levels: the real multi-band result at about 1/8 scale.

**Stage 8: Background neutralisation and output**
1. Collapse the pyramid and divide by the accumulated weights (float32; not yet clamped).
2. **Disk mask**: threshold the smoothed luminance (σ 8 px) at the sky level + 5σ, keep the largest component, fill holes.
3. **Sky sample**: canvas pixels inside the panel union and more than 60 px outside the disk. This excludes the scattered-light band next to the limb, which was measured 210–280 ADU brighter than far sky.
4. **Neutralise**: per channel, estimate the sky level as the **median** of the sky sample. The median stays unbiased because fewer than 50 % of R and B sky pixels are clipped at 0 (measured 5–25 %); a mean would be biased high. Subtract that per-channel pedestal from the whole mosaic, disk included, so the sky becomes neutral and the lunar colour is corrected by the same offsets. A planar or low-order fit (`--background-model plane`) is optional if a residual sky gradient remains; it is only used outside the disk.
5. **Disk colour balance** (`--colour-balance`, default `highlands`), applied after the pedestal subtraction (offsets must be removed before multiplicative gains):
   - `highlands`: select disk pixels between the 40th and 95th luminance percentiles, excluding saturation and a 40 px band inside the limb. Compute one gain per channel so the medians of R, G and B there become equal, and normalise the gains so luminance is unchanged.
   - `disk`: the same, using the median of the whole disk.
   - `none`: skip.
   - Panel-to-panel colour differences (~3 %) are already removed by the per-channel gains in stage 5, so one global set of three gains is enough. Measured cast to correct: B/G ≈ 1.055, R/G ≈ 0.966. The gains are logged, and the result is re-scaled if needed so the 99.99th percentile stays ≤ 65535.
6. **Black space** (`neutral-black`, default): outside the disk, set pixels to 0, with a `--limb-feather` px cosine ramp from the disk edge outward so the limb is not cut hard. Pixels outside all panels are 0 as well.
7. Clamp to 0–65535, crop to the union of the panels, and write a **16-bit RGB TIFF, uncompressed**, with `cv2.imwrite` (`IMWRITE_TIFF_COMPRESSION = NONE`). About 1.2 GB; classic TIFF (< 4 GB) is enough. `cv2.imwrite` does not embed the panels' sRGB ICC profile; viewers assume sRGB for untagged RGB, so this is acceptable. Embedding it would need a small post-write TIFF tag patch.
8. Print the final summary (§5), including the per-channel pedestals that were subtracted, the residual sky level after neutralisation and the colour-balance gains.
9. **Contribution map** (`<out>_contributions.png`, plus a full-resolution variant on request). It shows which panels contributed to the final image: the outline of every panel on a dimmed copy of the mosaic, each region tinted by the panel that won it in stage 6, and inside each contributing outline the **file name** of that panel (id on the first line, the rest of the name below, font scaled to fit). Panels that won no region are drawn as thin grey outlines, labelled with their id only. A legend lists each file with its share of the mosaic area. Suggested by you, 2026-09-27; built in phase 4.

### Failure handling ("skip and report, keep going")

| Situation | Behaviour |
|---|---|
| Unreadable or corrupt file | Skipped and listed in the summary |
| Too few features | Skipped and listed |
| No accepted pairs | Listed as `not placed: no overlap found` |
| Separate island of panels | Largest component is used; the island's panels are listed together |
| Pair inconsistent with the global solve | Dropped, solve repeated, pair listed |
| Too little sky for neutralisation (< 50k px) | Warn, fall back to per-panel sky measurements where available, else skip neutralisation |
| Nothing can be placed | Exit code 2, no output written |
| `q` in the preview or Ctrl-C | Current step finishes, caches are kept, clean exit, **no partial output** |

---

## 5. Progress reporting (console)

The log is human-readable and line-based, so it also reads well in a log file. The rules:
- A header for every stage (`[n/8]`).
- A progress line at least every 2 % of the stage or every 5 s, with counts, elapsed time and ETA.
- Key numbers for every item.
- Warnings are prefixed `WARN`.
- A final summary.
- **No silent period longer than about 10 s.**

Example. Stage 3 figures, the stage 0 sizes, stage 8 pedestals and colour cast, and the stage timings (from §8 benchmarks) are measured values; other numbers are illustrative:
```
mosaic_builder  101 inputs · 9 workers · preview on · cache .mosaic_cache/
[0/8] Preflight
      101 readable · 16-bit RGB · 3720–3816 × 2088–2112
      est. canvas ~14.7k × 13.3k · peak RAM ~9 GB of 24 GB · OK
      free disk 1.1 TB · need ~1.5 GB · OK
[1/8] Coarse features (proxy 0.25×)  52/101  [##########..........]  00:07 · ETA 00:07
      2117_0   3,912 kp  (mask 91 %)   [measured range 819–4,000, median 3,983]
[2/8] Pair discovery (index)         1,184 candidates → 704 accepted · 00:11
      + 2117_0 ↔ 2117_6   812 inliers (61 %)  overlap 38 %  rot +0.08°  scale 1.0003
      rejected: 470 no-overlap · 10 few-inliers · 0 degenerate
[3/8] Global alignment
      component: 101/101 panels · 704 pair constraints · reference 2118_3 (22 partners)
      IRLS: RMS 1.15 px → dropped 11 inconsistent pairs → RMS 0.98 px (proxy level)
      layout closure: 3 extra pairs verified, 2 accepted
[4/8] Fine alignment (tiles)         695 pairs · 6,120 tiles · RMS 0.31 px · worst panel 0.72 px · 00:20
[5/8] Gain + offset                  gains R 0.93–1.08 · G 0.94–1.07 · B 0.92–1.09 · offsets −140…+95 ADU · clipped 0 px
[6/8] Region selection               71 panels win ≥1 region · 30 dominated (skipped) · largest share 2046_7 (4.1 %)
[7/8] Blending                       37/71  [##########..........]  00:11 · ETA 00:11
[8/8] Background + colour + output   sky pedestal R 230 · G 370 · B 300 ADU → residual |sky| < 3 ADU
                                     colour gains R 1.035 · G 1.000 · B 0.948 (highlands)
                                     mosaic.tif  14,652 × 13,300 · 16-bit RGB · uncompressed · 1.17 GB · 00:08
Done in 1:52 · placed 101/101 · blended 71 · excluded: none
```

---

## 6. Live preview window

- A native `cv2.imshow` window (Cocoa backend confirmed), scaled so its long side is at most 1,600 px.
- **macOS constraint:** OpenCV's GUI calls must run on the main thread. The main thread only runs the UI loop (`waitKey(30)`) and draws frames from a queue. The pipeline runs in a worker thread (numpy and OpenCV release the GIL for heavy operations), and stages 1–2 use a pool of worker processes. macOS uses the `spawn` start method, so workers receive file paths and cache keys, not arrays, and the entry point is guarded by `if __name__ == "__main__"`.

| Stage | Preview shows |
|---|---|
| 1 | The current panel's proxy with its keypoints overlaid |
| 2 | The latest accepted pair side by side with inlier matches (throttled to ≤ 2 fps) |
| 3–4 | Panel outlines on the canvas, coloured by residual (green < 0.5 px, amber < 1 px, red above), with IDs |
| 5–6 | Low-resolution composite after gains, then the label map as a colour overlay |
| 7 | **The mosaic growing panel by panel**: the real multi-band blend from the coarse pyramid levels |
| 8 | Before/after background neutralisation, then the final result until you press a key |

Keys: `p` pause/resume · `q` abort cleanly · `1`/`2` switch between the composite and the overlay (label map / outlines) · any key closes the window at the end.

**Stage 3 placement animation** (built): panels appear one by one in breadth-first order from the reference, as the layout "grows", on the averaged composite.

**Movie recording** (`--record FILE.mp4` for 16:9 and/or `--record-vertical FILE.mp4` for 9:16, built): everything the preview shows is also written to an H.264 MP4 (1920 × 1080, 15 fps) with a caption bar (stage title + latest log line) and a title card per stage, paced for a viewer rather than wall-clock time (stage 1 ≈ 7 s at one frame per panel, stage 2 ≈ 16 s of sampled pairs, stage 3 animation ≈ 10 s + final views; 43 s in total for stages 0–3, your pacing of 2026-09-27). On screen, stages are shown to viewers as **steps 1–9** ("STEP 4 OF 9") with a strip of nine dots (done = amber, current = ringed, upcoming = dim), so it is clear that more stages follow; the console keeps the technical 0–8 numbering (`[3/8]`). Encoding runs on a background thread; it works without a display. Measured: 60 s clip for stages 0–3, adding ~15 s to the run (~30 s with both formats). Suggested by you, 2026-09-27. The **9:16 version** (1080 × 1920, for Instagram Reels / Stories) has its own layout rather than a crop. Text stays inside the safe zone (below the platform's top bar, above the caption area, left of the button column), the stage 2 pair is stacked vertically, and readouts use a 2 × 2 grid.

**Visual design: Observatory** (chosen 2026-09-27, built in `preview_style.py`). Frames are 1920 × 1080 for both the window (scaled to 1600 px) and the movie. The image fills the window on black, with a vignette; captions sit on translucent bars (Avenir Next for titles, SF Mono for numbers) with amber accents. Each stage opens with a title card that uses a friendly name ("Finding features", "Matching overlaps", "Placing panels", …) and the technical name below it. The bottom bar holds a progress line, key readouts and, very faintly, the latest log line: what the machine is doing right now. Keypoints are small glowing amber dots; pair matches are faint threads with amber endpoints; the final residual view uses muted teal / amber / coral outlines with a legend. Images are shown in a **natural view** (linear luminance, one global white point, gamma 1.1). The detector's contrast-enhanced proxy (percentile stretch + CLAHE) is used internally only, because it looks harsh. Rendering is skipped when a frame is not due, so the preview costs little time; text is batched into one Pillow pass per frame, with OpenCV's Hershey font as fallback. The other two directions (A Mission Control, C Blueprint) and the natural/machine comparison remain in `design_mockups/`.

---

## 7. Caching and re-runs

- The cache lives in `--cache-dir`, next to the output by default. Its keys combine the file path, size, mtime and the parameters that matter for each stage.
- Cached items: coarse and fine features, pair matches, transforms, gains/offsets and label maps. Estimated size 150–250 MB for this dataset. Proxies and warped panels are **not** cached (about 5 GB).
- When only blending or output settings change (`--levels`, `--interp`, `--gain`, `--background`, `--limb-feather`, `--colour-balance`), a re-run skips stages 1–4 (target ≤ 1 min). `--rebuild` ignores the cache.

---

## 8. Performance

**Target: first run ≤ 3 min, blending-only re-run ≤ 1 min**, on the M4 with the preview on. ICE does this dataset in a few minutes on an older Windows PC; this tool does more per panel (per-region selection, sub-pixel global alignment, photometric matching) and must still be in the same range.

Benchmarks on this dataset (prototype code, 2026-09-27):

| Step | Measured | Notes |
|---|---|---|
| Read one panel (47 MB) | 0.15 s | From `/Volumes/Astro` |
| **Builder stages 0–3, measured (phase 2)** | **39 s** | stage 1 7 s · stage 2 28 s (index 10 s + FLANN verification) · stage 3 < 5 s; +15 s with `--record` |
| Stage 1: read + proxy + SIFT, 101 panels, 8 threads | 15 s | |
| Stage 2, global index candidates | ~10 s | 702–704 of 704 pairs recalled |
| Stage 2, brute force all 5,050 pairs, 8 threads | 111 s | Fallback only (`--pair-search all`) |
| Stage 7: warp + 7-level pyramid + accumulate, per panel | 0.31 s | Serial; includes the read |
| Stage 8: pyramid collapse, full canvas | 4.3 s | |
| Stage 8: write 1.18 GB uncompressed TIFF | 0.5 s internal · 2.7 s `/Volumes/Astro` | |

`panel_classifier.py --relative` on the 101 panels: **1:47** in total (measure 101 images, pair discovery, 649 full-resolution same-terrain comparisons).

Budget for a first run:

| Stage | Budget |
|---|---|
| 0 Preflight | < 2 s |
| 1 Read, proxies, features | 15 s |
| 2 Pair discovery (index + verify) | 15 s |
| 3 Global solve + layout closure | < 5 s |
| 4 Fine alignment (phase-correlation tiles) | 20–30 s |
| 5 Gain + offset | < 5 s |
| 6 Region selection (full-res band-pass, 0.25× maps) | 15 s |
| 7 Blending (≤ 101 panels × 0.31 s; fewer after skipping dominated panels) | 20–35 s |
| 8 Neutralisation, colour balance, collapse, write | 10 s |
| **Total** | **~2 min (≤ 3 min target)** |

What keeps it fast without costing quality: a global feature index instead of all-pairs matching; phase correlation instead of full-resolution SIFT; full-resolution luminance held in RAM; skipping dominated panels; and restricting each panel's pyramid to its label region. The preview adds little: stage 7 collapses only the coarse levels (about 1/8 scale). OpenCL/GPU is deliberately not used: Apple deprecated OpenCL, and CPU NEON is fast enough.

---

## 9. Memory and disk budget (canvas ≈ 14.7k × 13.3k ≈ 195 Mpx)

| Buffer | Size |
|---|---|
| Accumulated Laplacian + weight pyramids, float32 (7 levels) | 4.2 GB (measured) |
| Full-resolution luminance of all panels, float16 (stages 1–6) | 1.6 GB, freed before stage 7 |
| Collapsed float32 RGB result (stage 8) | ~2.3 GB |
| uint16 output buffer | ~1.2 GB |
| Per-panel working set (warp and pyramid) | ~0.5 GB |
| Region selection: running max + argmax at 0.25× | ~0.1 GB |
| **Estimated peak (stage 8)** | **~8–9 GB of 24 GB: fits, no tiling needed** |

Out-of-core or tiled blending (for mosaics above about 40k px) is out of scope for v1. Preflight warns if the estimate is exceeded.

**Disk:** output ≈ 1.2 GB uncompressed, cache ≈ 0.2 GB. Both disks have ample space (42 GiB internal, 1.1 TiB on `/Volumes/Astro`).

---

## 10. Acceptance criteria

1. **Alignment:** global RMS < 0.5 px at full resolution, and no placed panel above 1.0 px.
2. **Seams:** no visible seam or brightness step at 100 % zoom. The measured step across seam strips is < 1 %.
3. **No ghosting:** no doubled crater rims anywhere.
4. **Sharpness:** in every overlap, away from the seam transition band, the output's local sharpness is ≥ 95 % of the sharpest contributing panel. This proves region selection works.
5. **Coverage:** the output covers the union of all placed panels with no holes; every panel's footprint appears in the stage 3 outline preview.
6. **Background:** after neutralisation, the per-channel median of the sky sample is within ±5 ADU of 0 before black fill, so R, G and B are equal. Outside the feathered limb the output is exactly 0. No lunar pixels are clipped by the black fill (checked on the limb profile).
7. **Colour:** with `highlands`, the R, G and B medians of the highland selection are equal within 1 %.
8. **Speed:** first run ≤ 3 min and blending-only re-run ≤ 1 min on the M4, with the preview on.
9. **Transparency:** the console never goes silent for more than about 10 s. The preview updates after every panel in stage 7, and every excluded panel or pair has a stated reason.
10. **Reuse:** the classifier runs without errors on a non-lunar image folder, in single-image mode.
11. **Native:** runs as arm64 with no Rosetta and no new dependencies.

---

## 11. Build phases (each one ends with a check-in with you)

1. **Classifier v2. Done 2026-09-27.** `panel_classifier.py` + `mosaic_common.py` in `/Volumes/Astro/scripts/mosaic/`. Run on all 101 panels in `--relative` mode in 1:47 (radius-matched, two-scale comparison); blind visual check agreed on 5 of 6 pairs; output `panel_manifest.json` / `panel_manifest.md`; also tested in single-image mode on the Saturn folder (29 images, 2 s). Single-image metrics with the fixed subject mask, plus `--relative` mode. Check-in: does the relative ranking match what you see on screen for a few panels picked from the top and the bottom?
2. **Builder stages 0–3. Done 2026-09-27.** `mosaic_builder.py`: 101/101 panels placed, layout RMS 0.62 px (proxy level), 39 s without preview. Outputs `<out>_layout.json`, `_layout.png`, `_layout_composite.png`; live preview on the main thread (tested); movie recording; placement animation. Check-in: does the layout look right? Which preview design?
3. **Stages 4–6.** Fine alignment, gains/offsets and region selection, with a low-resolution composite preview.
4. **Stages 7–8.** Full-resolution blend, background neutralisation, colour balance and output. Check-in on seams, limb, sky, colour and runtime.
5. **Polish.** Caching, pause/abort, and a usage section in a README.

Scripts live in `/Volumes/Astro/scripts/mosaic/`. The prototype code used to verify v0.3 is in the session scratchpad and can seed phases 1–2.

---

## 12. Decisions (all settled)

| # | Question | Decision |
|---|---|---|
| 1 | Disk space | Resolved; output can go next to the panels on `/Volumes/Astro` |
| 2 | Reference stitch | Microsoft ICE 2.0.3; both copies moved to the Trash; no benchmark |
| 3 | Folder split | Merged: all 101 panels in `panels/`; quality lives in the manifest |
| 4 | Mount | EQ |
| 5 | Background | Black space with background colour neutralisation (Stage 8) |
| 6 | Runtime | ≤ 3 min first run (§8) |
| 7 | Compression | None |
| 8 | Script location | `/Volumes/Astro/scripts/mosaic/` |
| 9 | Sharpening | Yours, after mosaicking; not part of the tool |
| 10 | Disk colour balance | Yes; default `highlands` (neutral grey highlands) |

---

## 13. Out of scope for v1

GUI beyond the preview window · manual panel placement · per-channel alignment (measured unnecessary) · sharpening / deconvolution (done by you afterwards) · flat-field or vignetting correction beyond block gains · lens distortion correction · tiled/out-of-core blending for very large mosaics · HDR · packaging with pip.

---

## 14. Implementation status and measured results (2026-09-27)

All nine builder stages run end to end on the 101 panels: **1:53 without recording, 3:06 with both movies** (Apple M4).
Outputs: `mosaic.tif` (14,792 × 13,432 px, 16-bit RGB, uncompressed, 1.11 GiB), `mosaic_layout.json`,
`mosaic_contributions.png`, `mosaic_labels.png` (region map at 1/4, index = `label_index` in the layout JSON), movies.

| Stage | How it is built | Measured |
|---|---|---|
| 4 Fine alignment | Phase correlation on up to 12 textured 256 px tiles per overlap at full resolution; keypoint fallback for pairs with < 3 good tiles; global solve with a **cubic warp per panel** (`poly3`, 20 parameters, still linear). Priors in pixel units: 1 px of bending costs as much as a 1 px tile error; the scale/rotation part is held near the coarse solve (σ ≈ 6 px at the frame edge, same optics for all panels). Panels with implausible poses are pinned to affine; a round is kept only if it improves the RMS | 3 rounds, 6,081 tiles, 509 pairs; **RMS 0.54 px, median 0.25 px** (in-sample). Held-out tiles, measured in each panel's own pixels: similarity 1.21 / 0.80 px, affine 0.97 / 0.57, **cubic 0.79 / 0.41** (RMS / median). The < 0.5 px RMS target is met only by the median; the rest is tile noise between differently blurred panels plus local seeing warps too fine for a smooth model. **At 100 % no seam is visible** (6 seams checked) |
| 5 Photometry | Gain + offset per panel and channel from matched percentiles on shared terrain; priors in physical units (gain σ 0.2, offset σ 0.003 of full scale) | Mismatch on shared terrain **1.06 % → 0.14 %**; gains 0.86–1.08, offsets −920…+790 ADU |
| 6 Region selection | Sharpness map per panel (fine × coarse detail / brightness², edge taper 150 px), running best on a 1/4 grid, speckle cleanup | 90 of 101 panels win a region (88 on the disk); largest disk shares 4.2 %, 4.0 %, 3.8 %. The fast sharpness map (energies shrunk before smoothing) picks the same winner as full-resolution smoothing on 98.6–99.7 % of each overlap; 98 % of the few differences lie within 150 px of a frame edge, where the taper decides anyway |
| 7 Blending | 7-level multi-band per contributing panel, only inside its won region; exact inverse warp (cubic) via a remap grid; outside the frame filled by push-pull so pyramids never see black | 90 panels in 20 s |
| 8 Finishing | Disk mask, sky pedestal (median) removed, highland colour balance, black space with 12 px cosine feather, uncompressed TIFF, contribution map on the disk only | Pedestal R 469 · G 603 · B 540 ADU → residual 0; colour cast R 0.981 · G 1.002 · B 1.038 corrected |

**Corrections to earlier statements in this session.**
- "Cubic warp: 0.55 px held-out" was wrong. That solution shrank the canvas: panels far from the reference collapsed and residuals measured in canvas pixels fell with them. The honest numbers are in the table above (0.79 / 0.41 px). The fix was the scale/rotation prior plus measuring residuals in each panel's own pixels.
- The sharpness map took 0.8 s per panel at full resolution, not "~4 s"; the fast version takes 0.07 s and is equivalent (see stage 6).

**Visual checks.** The overview shows a clean, neutral full Moon on black. Seams at 100 % are invisible, with crater rims continuous across them. The bright diagonal band in Mare Serenitatis is the real Tycho ray through Bessel, not an artefact.

**Still open.** Phase 5 polish: caching for fast re-runs, README usage for stages 4–8 (partly done), the timing review you asked to do later. Grain/streak-prone panels can still win a region (known limitation, §4 stage 6).

**Movie pacing (your settings, 2026-09-27; `VIDEO_SECONDS` in `mosaic_builder.py`).** Opening card "Lunar Mosaic" (date · panel count) 2 s · step 1 2 s · steps 2 and 3 9 s each (step 2: feature detection on two panels, each: features appearing small → large in 1.8 s, the complete view held 0.7 s, then the panel fading to black in 0.5 s, so step 2 is 7.2 s; step 3: 9 pairs between top-quality panels, each animated in three beats: all keypoints as faint dots → the verified matches drawn in as a sweep of threads → the overlap zone inside each panel tinted and outlined, with matches, rotation, scale and overlap as readouts) (ranking from `panel_manifest.json` when present, else single-image Tenengrad; display only) · step 4 8 s · steps 5–9 5 s each · final shot 4 s, fading in over 1 s from the contributions screen, with the Moon in the same place. then a **fade-out to black that fills the movie to exactly 60.0 s** (2.8 s now; it adapts if step lengths change, minimum 0.5 s; `MOVIE_TARGET_SECONDS`). **Total 60.0 s in both formats, 30 fps**; every run prints the measured seconds per step. Step 9 shows: before (sky pedestal and colour cast) → contribution map → clean result. On-screen numbers use no thousands separator and carry units ("14792 × 13432 px"). Keypoints are drawn as small dots with a soft glow.

**Movie visuals (revised 2026-09-27).**
- **Step 2, feature detection explained.** Two top-quality panels, about 4.4 s each. Their 700 strongest features appear from small to large scale as thin rings sized by the feature's scale: pale for small, amber for large. The caption follows the scale: "pits and small craters" → "crater rims and ridges" → "craters and mare edges".
- **Step 6, where brightness is matched.** The overlap zones on the lunar disk are tinted by the average brightness disagreement of the panel pairs compared there, before and then after correction: teal < 0.3 %, amber ≈ 1 %, coral ≥ 2 %. Measured per overlap before correction: median 0.99 %, but 39 % of overlaps at ≥ 2 % and the worst tenth up to 11.5 % (a group of panels stacked noticeably brighter or darker). After: median 0.14 %, 90 % of overlaps below 0.31 %.
- **Step 9 → final.** Before (sky and colour) → contributions, with the Moon centred at exactly the final size and position, panel ids on their regions and the source file names listed beside it (landscape: right side, top 40 by share; 9:16: top 5 below). The final shot then crossfades in over 1 s: tints, ids and file names dissolve away and leave the finished Moon, with no movement.
- **Centred text (2026-09-27).** On every screen the step label, step dots, title, detail and legend are centred at the top; readouts, the progress line (with its percentage) and the log line are centred at the bottom. The same applies to the final shot. The date and panel count appear on the opening card and the final shot only. The contributions file list stays a left-aligned column (a table), with its heading centred over it; in 9:16 it is centred as a block.
- **Step 1, the inputs.** A 0.8 s title card, then a contact sheet of all panels popping in in capture order (12 columns in 16:9, 8 in 9:16), with file count, format, frame size range and capture span. The panels are only read in step 2, so the movie holds step 2's title card back until the contact sheet has been recorded; the window is unaffected.

---

## 15. Phase 2: finishing touches (`lunar_finish.py`, 2026-09-27)

Runs on the finished mosaic, which is already wavelet-sharpened (the input panels were), and writes a new 16-bit TIFF; the mosaic is never changed. It works in CIE Lab, so brightness and colour are adjusted independently.

| Step | Method | Default | Measured on this mosaic |
|---|---|---|---|
| Highlights | Brightness split into an edge-aware base (self-guided filter, 64 px) and detail; a soft shoulder lowers the base above the 90th percentile of the disk; the detail is added back unchanged (no halos at the limb) | strength 0.3 | brightest 0.1 % L 58.5 → 56.1; 10 % of the disk affected |
| Lunar colour | Colour-only smoothing (**σ 3 px**, your luminance-layer + blurred-colour-layer practice; measured colour noise −31 % in a mare, −40 % in bright rays against 1.2 px, brightness detail unchanged), then saturation × per pass on the lunar hue bands (orange–yellow 45–115°, blue 225–295°, 30° cosine ramps); greens, purples and pinks get 15 % of the boost; no extra saturation within 80 px of the limb | × 2, **2 passes** (your request) | colour strength on lunar hues 0.38 → 0.67 → 1.18 |
| Contrast | Gentle S-curve on brightness pivoting at the disk median (0 and 1 fixed) | **+12 %** at the midtones (your request) | brightness spread 9.51 → 10.52 |
| Sharpening (optional) | Photoshop's Unsharp Mask: original + amount × (original − Gaussian σ = radius), only where the difference ≥ threshold (0–255 levels); brightness only by default (`--usm-mode rgb` = per channel, as Photoshop in RGB mode) | off | with your settings 100 % / 1.5 px / 0: grain in a smooth mare area 969 → 1927 (× 2.0); threshold 3 → 1875, 6 → 1636. The grain is about 7 levels strong, so thresholds below that barely help |

Colour measurement that set the hue bands: of the colourful disk pixels, 46 % are blue (240–300°) and 30 % orange–yellow (60–120°), the Moon's titanium ↔ iron axis; greens 0.9 %, purples/pinks about 7 %. A single saturation pass does not change hue, so pass k multiplies the chroma by f once more (two passes of × 2 = × 4 on the lunar hues, × 1.32 elsewhere).

Photoshop Unsharp Mask facts used: Amount 1–500 %, Radius 0.1–250 px (Photoshop's radius behaves as the Gaussian's standard deviation), Threshold 0–255 levels (Adobe recommends 2–20). Sources: Adobe help "Sharpen images with Unsharp Mask", Cambridge in Colour "Sharpening using an unsharp mask", Wikipedia "Unsharp masking", Rétines "Definition of Gaussian blur".

**Movie** (Observatory style, 16:9 and 9:16, 30 fps): opening card → per step a title card (1.2 s), a before/after wipe (2 s, an amber line sweeping across; highlights close up on the brightest area; sharpening close up, enlarged) and the result held (0.8 s) → the whole disk once more (after a close-up) → a crossfade into the finished image (1 s + 2 s). 25.6 s with 5 steps. Runtime about 1 min including both movies.

**Open:** the pre-existing colour fringe along the limb (colour strength about 3.2, against a disk median of 0.35) could be neutralised with an option.

---

## 16. Display versions of the results (2026-09-27)

You noticed the movies are much brighter than the TIFFs. Measured: `mosaic.tif` has a Moon median of 70/255 and its brightest 0.1 % at 143/255 (about 56 % of full scale), against a median of 137/255 in the build movie (2.0×) and 112/255 in the finishing movie (1.6×). The files keep the data as stacked (no stretch anywhere in the pipeline), while the movie frames apply a display stretch: a white point at the Moon's brightest 0.2 % (build) or 0.01 % (finishing) plus gamma 1.1.

As you decided, both tools now write two TIFFs: the master as before, and `<out>_display.tif` with exactly the movie's tone mapping (the same white point value and gamma, at full resolution, 16-bit). Measured: display TIFF medians within 3–4 % of the movie frames (the rest is H.264 encoding and the frames' vignette). Full white: 0.42 % of the Moon in `mosaic_display.tif` (the movie's white point comes from a downscaled frame, so a few more full-resolution peaks exceed it), 0.03 % in `mosaic_finished_display.tif` (the highlight step lowered them first). `--no-display-tif` turns the display versions off.

