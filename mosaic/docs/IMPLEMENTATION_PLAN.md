# LunarMosaic: implementation plan

Written 2026-10-05 for the model or engineer who implements the design in [DESIGN.md](DESIGN.md). It assumes
no access to the conversations that produced the design. Everything needed is either in this file, in
DESIGN.md, or in the code it points to.

Revised the same day after a verification review. Rules that carry a number were measured — on the code, on
PixInsight's own output, or on synthetic data — and the measurement is quoted where it explains a rule that
would otherwise look arbitrary (Appendix F collects them). Do not simplify such a rule without measuring again.

## 0. How to use this document

- **DESIGN.md decides *what*; this file decides *how* and *in what order*.** If they disagree on behaviour,
  DESIGN.md is right; stop and record the conflict in DESIGN.md §8 rather than picking silently.
- Work the phases **in order**. Each phase ends with every test green — old and new — and the application
  still usable. Do not start a phase on a red tree.
- Each phase lists its goal, steps, files touched, acceptance tests and the traps that would make a first
  attempt fail. The traps are there because the code makes them easy to fall into; read them before coding.
- Section 3 lists the **decisions this plan takes** where DESIGN.md was silent. They are reasoned defaults,
  not owner decisions; keep each in one place so it can be changed.
- When a phase is done, update DESIGN.md §9 (the divergence table and "Changes made") in the same commit.

---

## 1. Ground rules

**Environment.** macOS (Apple Silicon) primary; Linux and Windows must not break. Python virtual environment
at `.venv/` in the repository root. Dependencies in `requirements.txt`: numpy, opencv-python, pillow,
tifffile. **No scipy and no imagecodecs.** Do not add heavy dependencies; if one is truly needed, ask first.
Consequences: FFT sizes come from `cv2.getOptimalDFTSize`; `tifffile` reads uncompressed and zlib TIFFs but
not LZW (OpenCV reads LZW), so write test fixtures with zlib compression.

**Settings precedence.** Command line > environment variable > `.env` > built-in default (`tool_settings.py`).
The owner's `.env` sets `LUNAR_FINISH_WAVELETS=pixinsight`, and north-up, the preview window and the display
TIFF default to on. Every test that runs a command-line tool passes all relevant flags explicitly.

**Running the tests** (from the repository root; all pass as of phase 0):

```
.venv/bin/python mosaic/tests/run_all.py                # every suite, one process each; non-zero exit on failure
.venv/bin/python mosaic/tests/run_all.py golden lrgb    # only suites whose file name contains a word
.venv/bin/python mosaic/tests/golden.py --report        # golden fixtures: rms / p99.9 / max in ADU
```

Suites today: `review_regressions` (14 builder/finish regressions), `lrgb_workflow` (24 application checks),
`synthetic_checks` (fixture ground truth), `ui_autosave` (needs Node.js), `golden`, `determinism` (about 20 s).

New tests go in `mosaic/tests/` as `unittest` modules with an `if __name__ == '__main__':` guard, runnable the
same way. Never put work at module top level (see BUGS.md bug 1).

**Code style.** Match the surrounding code: dense, few comments, comments explain *why* or state a measured
fact. Module-level functions over classes unless state is needed. Units in names or docstrings
(`radius_px`, `sigma`, `levels`). User-facing text is plain English in full sentences, never internal jargon.

**Invariants that must survive every phase.**

1. Source panels are never written, moved or modified. Derived data lives in the project's `derived/`.
2. Project saves are atomic (temp file + `os.replace`) with a revision counter; one writer per project
   (`file_lock` lease). Keep `Project.save()`'s pattern.
3. Alignment is reported as measured numbers ("Match 0.6 px · worst 3.1 px"), **never** as the word
   "aligned". Unknown regions are shown as unknown.
4. No fabricated numbers anywhere in the interface: every figure shown comes from a measurement or a stored
   value.
5. Exports contain no observing-site metadata unless ticked for that export.
6. Outputs are staged and published only on success (`mc.Staging`, `os.link` exclusive publication).
7. Anything that can take more than a second runs as a cancellable job with progress.

**Repository hygiene.** The working tree contains changes by others (`moon_atlas/`, `mosaic/experiments/`,
`BUGS.md`). Do not modify `moon_atlas/` or `mosaic/experiments/` unless a phase says so. Commit at the end of
each phase on a branch named `impl/phase-N`; never push or open a pull request without the owner's go-ahead.

---

## 2. The code today

| File | Lines | Role | Fate |
|---|---|---|---|
| `lunarmosaic.py` | — | CLI entry: `app`, `new`, `build`, `match`, `check-match`, `north-up`, `status`, `export` | extended (phase 8) |
| `mosaic/app.py` | 613 | Local HTTP app: `Application` (project lease, one job slot, build subprocess, match, north-up), `get`/`post` routes | reworked (phases 4, 6, 7) |
| `mosaic/project.py` | 118 | Project schema 3: `sets{L,RGB}`, `match`, `orientation`, flat `edits` dict | schema 4 (phase 4) |
| `mosaic/lrgb.py` | 463 | `Image` (lazy 16-bit reader, `warp`), `register` (one global L↔RGB similarity), `Scene` (`geometry`, `render`, `preview`, `export`) | `Scene` rebuilt on chains (phase 4); `register` replaced (phase 6) |
| `mosaic/sharpen.py` | 75 | App sharpening on CIE L*, imports filters from `lunar_finish` | removed in phase 4 |
| `mosaic/alignment.py` | 64 | Independent match checks (`measure`) | adapted (phase 6) |
| `mosaic/lunar_finish.py` | 737 | Finishing CLI: wavelets, highlights, saturation, contrast, USM, north-up, movie. Filters already match PixInsight/Photoshop (2026-10-05). Statistics on a proxy at a fixed factor q = 4 | filters move out (phase 1); becomes a chain runner (phase 2) |
| `mosaic/mosaic_builder.py` | 1701 | 9-stage panel mosaic builder (`[0/8]`…`[8/8]` log lines, parsed by `app.py`). **`--manifest` already exists**: the panel classifier's manifest (rejects, `--exclude-tier`, movie ranking) | mixed scales (phase 5) |
| `mosaic/mosaic_stages.py` | 1089 | Builder stages 4–8: poses (similarity, affine, poly2/3, mesh), fine alignment, photometry, `sharpness_map`, `select_regions`, blending, `finish` | tiered selection (phase 5) |
| `mosaic/mosaic_common.py` | 480 | Shared helpers: `read_image`, `verify_pair` (scale gate 0.97–1.03), `phase_shift`, proxies, SIFT, `proxy_to_full` | `verify_pair` gate (phase 5) |
| `mosaic/ui/` | 307 | `index.html`, `mosaic.css`, `mosaic.js` (vanilla JS, no build step) on LunarAtlas's stylesheet | rebuilt (phase 7) |

How a render works today: `Scene.geometry(edits)` gives a 3×3 matrix *luminance canvas → output* (rotation,
mirror, common-coverage crop, export factor). `Scene.render(matrix, size, edits, view)` asks `Image.warp` for
the canvas region an output tile needs. At full precision the sharpening runs **on that native canvas region
first** (the `processor` hook) and only then is the region resampled into the output with `INTER_LINEAR`.
Then a shoulder and an S-curve on CIE L*, and RGB chroma on L lightness in CIE Lab. Export tiles the output
and calls `render` per tile with a halo. Keep the filter-then-resample order (D13).

Known defects to keep in mind: `BUGS.md` (13 items, from a review of `0151e68`). Bug 1 is already fixed in the
working tree. Bug 11 (a failed autosave discards the user's edits) must not be reproduced in the new editor.

---

## 3. Decisions this plan takes

Each is a default where DESIGN.md is silent. Implement each in exactly one place so it can be changed.

| # | Decision | Where it lives |
|---|---|---|
| D1 | **Global statistics are computed once per chain configuration, frozen, and never inside a tile.** They come from the **full canvas** — canvas orientation, uncropped, independent of rotation, coverage and export scale — reduced by a **fixed factor 4** (`stats_q`, as `lunar_finish`), passed through the chain prefix **with the detail blocks skipped** (wavelets, unsharp mask, Gaussian blur, deconvolution and RGB shift are identity in this pass). Measured: changing only the factor moves `lunar_finish`'s tone 120–324 ADU rms; a cropped export moves the same pixels 24 ADU rms; letting highlights measure wavelet-sharpened data moves the result 1,982 ADU rms (3 % of full scale). | `chain.prepare()` |
| D2 | White balance reproduces PixInsight ColorCalibration with the **background as the fixed point**, `out = B + g·(x − B)` (fitted on PixInsight's own output, Appendix F). Gains are normalised so the background-subtracted white keeps its mean brightness. PixInsight's own normalisation can brighten; this block never does, and it does not pretend to predict PixInsight's occasional overall rescale (trigger unknown, DESIGN.md §5.3). | `blocks.white_balance` |
| D3 | White balance ships **without structure detection** (parameter accepted only as `false`), pending DESIGN.md §8.4. | `blocks.white_balance` |
| D4 | Curves interpolate with a **natural cubic spline**, clamped; Levels use the formula in Appendix A. Neither has been measured against Photoshop yet (optional validation V2). | `blocks.curves`, `blocks.levels` |
| D5 | Deconvolution follows the structure of Wendykier's WPL (Appendix C); its numerical parity with LuckyStackWorker is **unverified**. Dering and blend-raw are this project's own definitions. The PSF is min-max scaled exactly as LuckyStackWorker does before it is normalised (Appendix B). | `deconv.py` |
| D6 | "Fade near the limb" is generic: `out = in + w·(block(in) − in)`, `w = clip(limb_distance / fade_px, 0, 1)`. Limb distance is the distance to the edge of the lit, hole-filled disk, so it also fades along the terminator (as `--limb-taper` does today). This differs slightly from the old `--limb-taper` when saturation has several passes. | `chain.run_block()` |
| D7 | **Space is protected, softly, and only space.** After every block the output is pulled back towards the chain input by `p = s·t`: `s` is the space region (the part of the proxy's L* < 1 connected to the canvas edge, dilated 8 canvas px and feathered — crater shadows enclosed by the lit disk are *not* space); `t = 1 − clip((L* − 0.5)/1.0, 0, 1)` of the chain input. Chain option `protect_space`, default `true`. Measured: the old rule (hard L* < 1 anywhere) freezes every value under 1022 ADU, crater shadows included; under a shadow-lifting curve, inputs 917 and 1049 ADU came out 917 and 3707. | `chain.run_chain()` |
| D8 | The colour stream is placed into a **colour canvas** at the colour data's own sampling: `canvas = K · colour canvas`, `K = [[k, 0, (k−1)/2], [0, k, (k−1)/2], [0, 0, 1]]`, `k` = finest colour arcsec/px ÷ canvas arcsec/px as a **float** ≥ 1 (not rounded: rounding either upsamples or discards). For a `both` set the luminance mosaic itself is the colour canvas (`k = 1`, no placement). | `colour_place.py` |
| D9 | A set may feed `luminance`, `colour` or `both` (a single colour mosaic "fed instead and split"). The command line treats its single input image as a `both` set, so all three chains of a chain file run. | schema 4, `lunar_finish.py` |
| D10 | When a stream holds sets of different scales, optics are required for each set, or the scale ratio is estimated from image evidence and shown for confirmation before building. | phase 5 |
| D11 | The merge stays CIE Lab: luminance-chain L*, colour-chain a*b* × strength × coverage. It does not claim Photoshop "Color" blend-mode parity (Photoshop uses a different lightness). | `chain.merge()` |
| D12 | Migration of schema-3 projects converts values exactly where the meaning is the same (wavelet gains) and approximately elsewhere, and shows a one-time note listing what changed. | `project.migrate_3_to_4()` |
| D13 | **Chains run on the canvas grid, in canvas orientation; rotation, mirror, coverage crop and export scale are applied once, to the chain output** — rotation with Lanczos-4, reduction with INTER_AREA. This is what `lunar_finish` (north-up last) and today's app (`Image.warp`'s processor runs before the remap) already do, and it keeps blocks on stored values. Measured: rotating first (23.7°, bilinear) keeps 78 % of wavelet layer-1 detail, and the owner's wavelet setting then differs by 576 ADU rms. | `lrgb.Scene` |
| D14 | **Mixed-scale luminance is selected in tiers, ordered by measured resolution.** Before building, each set's finest real detail is measured (`measurements.cutoff` on up to three textured panels; detail = arcsec/px ÷ cutoff); sets within 10 % form one tier. A canvas cell takes the best-resolving tier that validly covers it; sharpness competes only inside a tier; worse-resolving tiers fill only cells no better tier covers. Pixel scale alone is not enough: an oversampled small telescope has the finest pixels and coarse detail. Measured (synthetic): per-pixel scoring let a 2× coarser panel with a 100 % unsharp mask beat a finer one in 100 % of the overlap; measured resolution kept the order (fine 3.3, fine pre-sharpened 3.6, coarse 4.7 units) and put an oversampled set at the finest pixel scale last (7.6 units). | `measurements.cutoff`, `mosaic_stages.select_regions` |
| D15 | **Colour panels escalate their pose model** — similarity → affine → poly3 (the existing `mosaic_stages` models, poly3 regularised towards affine) — when held-out tiles justify it. No displacement mesh (DESIGN.md §3). Measured in the code (`mosaic_stages.py:66`): between panels of one set, held-out residuals are similarity 1.19 px RMS, affine 0.97, cubic 0.55. | `colour_place.py` |
| D16 | **RGB shift uses two estimators that work on lunar texture**: phase correlation of high-passed, unit-rms channels iterated three times, and a global Lucas–Kanade translation fit, over three textured probe crops. Measured: a gradient-weighted centroid is blind to translation on texture (−0.006/+0.046 px for a true +0.30/−0.20); iterated phase correlation and Lucas–Kanade agree within 0.07 px even with red blurred by σ 1.2 px. | `measurements.rgb_shift` |
| D17 | **CIE Lab is computed with exact float math** (`filters.rgb_to_lab` / `lab_to_rgb`, OpenCV's sRGB and D65 constants), never with `cv2.cvtColor`, in every new module and in `lunar_finish`. Adjacent Lab-domain blocks share one conversion. Measured: OpenCV's float Lab round trip is off by 66 ADU rms (p99.9 126 ADU, max 291) even on greys, and its forward L* by up to 0.23; exact math round-trips to 0.0000 ADU. | `filters.py` |

---

## 4. Target architecture

### 4.1 Modules

```
mosaic/
  filters.py        NEW  pure numpy filters moved out of lunar_finish.py, plus exact CIE Lab (D17)
  blocks.py         NEW  block registry: schema, validate, prepare (stats), apply, margin, readouts
  chain.py          NEW  chain document, validation, statistics pass, tiled execution, merge, default chain
  deconv.py         NEW  LuckyStackWorker PSF port + WPL deconvolution
  measurements.py   NEW  space mask, limb distance, probe selection, information cutoff, layer suggestion,
                         RGB-shift estimators  (not "measure.py": app.py imports a function named measure)
  colour_place.py   NEW  per-panel colour registration onto the luminance canvas (CLI, run as a job)
  project.py        schema 4, migration from 1–3
  lrgb.py           Image (kept, learns an alpha channel), Scene on chains, register() removed in phase 6
  app.py            routes for sets, chains, previews, analysis, colour placement; the statistics worker
  lunar_finish.py   CLI runner of a chain file; flags compile to the default chain; movie from block snapshots
  mosaic_builder.py / mosaic_stages.py / mosaic_common.py   mixed-scale luminance builds, tiered selection
  ui/               index.html, mosaic.css, mosaic.js + chains.js, previews.js (ES modules, no build step)
```

Dependency direction (no cycles): `filters` ← `deconv`, `measurements` ← `blocks` ← `chain` ← `lrgb` ← `app`;
`lunar_finish` → `chain`. `sharpen.py` is deleted in phase 4.

### 4.2 Coordinate spaces

| Space | Definition |
|---|---|
| panel px | a source panel's own pixels |
| **canvas px** | the luminance mosaic grid (`derived/luminance.tif`), at the **finest luminance scale** (arcsec/px). **Chains run here** (D13) |
| colour-canvas px | `derived/colour_canvas.tif`; `canvas = K · colour canvas` (D8). Colour-canvas pixel *i* covers canvas pixels [k·i, k·(i+1)); its centre is at canvas k·i + (k−1)/2 — the convention of `mc.proxy_to_full`. Build it by warping each colour panel with K⁻¹·T (T: panel → canvas), never by resizing a canvas-scale image under another convention: at k = 6 a mismatch is 2.5 px |
| stats px | canvas px reduced by `stats_q` = 4 (D1) |
| view px | the canvas after rotation and mirror (the "oriented full view"); **previews live here** |
| output px | view px after the coverage crop and the export scale |

Rendering a preview: map its corners to canvas px, take the bounding box plus the chain halo plus 4 px for
Lanczos, run the chains there, then rotate the result into the preview rectangle. When rotation or mirror
changes, map each preview's centre through the change and keep its size. Coverage and export scale never move
previews.

### 4.3 Pixel conventions (one place, enforced everywhere)

- Blocks take and return **float32 RGB, H×W×3, values in [0, 1]**. Mono data is three equal channels.
- **RGB order.** `lrgb.Image` returns RGB; OpenCV I/O and `lunar_finish` use BGR. Convert at the boundary
  (`lunar_finish` reads BGR → RGB before the chain, back to BGR to write). A swapped order silently breaks
  white balance, RGB shift, the chromatic PSF and lunar-hue weighting — test it (T-ORDER).
- CIE Lab via `filters.rgb_to_lab` / `filters.lab_to_rgb` (D17): L* 0–100, a*, b* signed, exact float math with
  OpenCV's constants (sRGB transfer, the matrix `[[0.412453, 0.357580, 0.180423], [0.212671, 0.715160, 0.072169],
  [0.019334, 0.119193, 0.950227]]`, white point X 0.950456, Z 1.088754), so values stay comparable with today's
  code. Luminosity-mode operations work on L*/100 (0–1), as `lunar_finish` does. A run of adjacent Lab-domain
  blocks (highlights, contrast, saturation, luminosity-mode unsharp mask) converts once: L*/100 is clipped to
  [0, 1] after each block, as `lunar_finish` does, and RGB is clipped at the end of the run.
- Every block's output is clipped to [0, 1] (Photoshop and PixInsight store bounded values): in RGB, or inside a
  Lab run in L*/100 after each block and in RGB at the end of the run (above).
- Parameters use the units the owner sees in PixInsight and Photoshop (Appendix A). **Pixel parameters are
  canvas px.** Every `apply()` receives `scale`: 1 on the canvas grid, 1/4 in the statistics pass, the zoom
  factor in approximate zoomed-out viewport renders. Export scale never reaches the blocks (D13).
- Blocks are pure: no statistic is computed inside `apply()`. A percentile over a tile is a statistic — it
  makes the result depend on where the tile falls.

### 4.4 The chain document

One JSON format shared by the project file, the CLI and chain import/export:

```json
{
  "format": "lunarmosaic-chain", "version": 1,
  "protect_space": true,
  "chains": {
    "luminance": [ {"id": "a1f3c2d4", "type": "wavelets", "enabled": true, "limb_fade_px": 0,
                    "params": {"biases": [0.6, 1.1, 0.2, 0.0], "kernel": "linear", "denoise": [0, 0, 0, 0]}} ],
    "colour":    [ {"id": "b7e2…", "type": "saturation", "enabled": true, "limb_fade_px": 40,
                    "params": {"factor": 2.0, "passes": 2, "lunar_hue": true, "chroma_blur_px": 0}} ],
    "merge":     {"strength": 1.0},
    "final":     [ ]
  },
  "previews": [ {"id": "p01", "name": "Copernicus", "x": 8120, "y": 4410, "w": 1024, "h": 768} ]
}
```

Preview coordinates are view px. Blocks may repeat, be disabled, reordered and duplicated (a duplicate gets a
new `id`). Validation rejects unknown types, out-of-range parameters and blocks on a path where they are not
allowed (Appendix A). The interface shows such blocks greyed with the reason rather than hiding them, so the
validator returns a reason string per block.

### 4.5 Execution model

```
prepare(sources, doc) — once per (sources, chain prefix); frozen; independent of rotation, coverage, export scale
  proxy  = luminance canvas and colour canvas on one grid, reduced 4× with INTER_AREA   (stats_q = 4)
  probes = full-resolution canvas crops of 1024², ranked by texture inside the lit disk (and inside the finest
           tier once phase 5 exists — the best-resolving one): one for noise and cutoff, three spread apart for RGB shift
  space  = filled lit-disk mask, limb-distance map and space region (D7), from the luminance proxy
  for chain in (luminance, colour, merge, final):
      for block in chain (enabled only):
          stats[block.id]    = block.prepare(proxy_in, probes_in, ctx)
          proxy_in           = proxy_in if block is a detail block else block.apply(proxy_in, stats, scale=1/4)
          probes_in          = block.apply(probes_in, stats, scale=1)      # every block, exact
          readouts[block.id] = block.readouts(...)
          keep (stats, proxy_in, probes_in) for this prefix

render_canvas(rect in canvas px, stage) — previews, viewport, export tiles
  halo = chain.margin(doc)
  lum  = luminance canvas[rect + halo]
  col  = colour canvas resampled through K onto rect + halo
  lum  = run_chain(luminance, lum);  col = run_chain(colour, col)
  out  = run_chain(final, merge(lum, col, coverage, strength))
  return out[rect]

view(out) — rotation and mirror with Lanczos-4, then the coverage crop, then reduction with INTER_AREA
```

**Detail blocks** — wavelets, unsharp mask, Gaussian blur, deconvolution, RGB shift — are identity in the proxy
pass, so statistics see only the tone and colour blocks. With them skipped, `lunar_finish` is reproduced
(its statistics come from the unsharpened image) and tweaking sharpening never re-tones the image. The other
blocks apply to the proxy as at full resolution; their only pixel radius, `chroma_blur_px`, is multiplied by
1/4. The probes go through every block exactly, so probe-based statistics (wavelet layer noise, RGB shift,
cutoff, the deconvolution dering threshold) see everything upstream.

Rules: `apply()` is a pure function of (pixels, params, stats, scale). The cache key is a SHA-1 of: source
mosaics (path, size, mtime), colour-placement revision, and the chain prefix up to that block — never
rotation, coverage or export scale. Per block, keep the stats **and** the proxy/probe pixels after that block,
so a parameter change restarts from that block; drop the oldest prefixes beyond a 2 GB budget.

**The statistics worker** is not a job: `Application.start()` allows one job at a time and refuses others, so a
slider move during an export would fail. Use one background thread with a generation counter; the newest
request wins and stale computations stop at the next block boundary. Renders wait for the current generation;
an export captures the generation it started with and finishes on it.

Viewport renders below 75 % zoom are approximate, as today: the canvas is decimated first and every block runs
with `scale` = the zoom (detail blocks with scaled kernels, as `sharpen.apply` does now). At 75 % and above, and
for every preview and export, rendering is exact. The fit view says "approximate — open a preview at 1:1 to
judge detail".

### 4.6 Project schema 4

```json
{
  "version": 4, "id": "…", "name": "…", "created": "…", "updated": "…", "revision": 12,
  "sets": [
    {"id": "s1", "stream": "luminance", "label": "IR · 2.5× Barlow",
     "inputs": ["/abs/or/sources/rel/paths.tif"], "skipped": [], "headers": {…},
     "optics": {"pixel_um": 2.9, "focal_mm": 1200, "barlow": 2.5, "aperture_mm": 250},
     "scale_arcsec_px": 0.199, "scale_source": "optics|estimated|unknown"},
    {"id": "s2", "stream": "colour", "label": "Colour · prime focus", "...": "…"}
  ],
  "luminance": {"state": "empty|ready|building|built|failed|interrupted|missing", "error": null,
                "mosaic": "derived/luminance.tif", "sets_report": "derived/luminance_sets.json",
                "canvas_scale_arcsec_px": 0.199, "build_facts": {…}, "last_stage": 9},
  "colour":    {"state": "…", "error": null, "canvas": "derived/colour_canvas.tif", "k": 2.07,
                "report": "derived/colour_place.json", "revision": 3,
                "summary": {"rms_px": 0.6, "worst_px": 3.1, "placed": 17, "rejected": 1}},
  "orientation": null,
  "geometry": {"rotation": 0.0, "mirror": false, "coverage": "full"},
  "view": {"l_visible": true, "rgb_visible": true},
  "chain": { "…the chain document of 4.4 without format/version…" },
  "notes": ["Highlights and contrast now use the finishing tool's curves; check their strength."]
}
```

Migration 1/2/3 → 4 is in Appendix D. `Project.open` migrates in memory and saves once, as today.

### 4.7 HTTP API (additions and replacements)

| Route | Method | Purpose |
|---|---|---|
| `/api/sets` | POST | create / update / delete a set: `{op, set: {id?, stream, label, optics}}` |
| `/api/inputs` | POST | as today, keyed by set `id` instead of `L`/`RGB` |
| `/api/canvas-estimate` | GET | finest scale, the canvas lower bound (px, Mpx, GB at 16-bit RGB), per-set scale ratios |
| `/api/build` | POST | build luminance (all luminance sets jointly), then place colour |
| `/api/place-colour` | POST | (re)run colour placement only; replaces `/api/match` |
| `/api/chain` | POST | `{chain, revision}` → validate, save; 409 on revision conflict |
| `/api/geometry` | POST | rotation, mirror, coverage (moved out of `edits`) |
| `/api/previews` | POST | add / rename / move / delete preview rectangles (view px) |
| `/api/render` | GET | `preview=<id>` or `x,y,w,h` (view px), `stage=final`, `luminance` or `colour`, `depth=16` (default) or `8` → PNG |
| `/api/analysis` | GET | per-block readouts and measurements (Appendix A "readouts") |
| `/api/chain-file` | GET / POST | export the chain document / import one (validated, ids regenerated) |

Keep `/api/state`, `/api/recent`, `/api/viewport`, `/api/export`, `/api/export-preview`, `/api/north-up`,
`/api/cancel`, `/api/close`, `/api/new`, `/api/open`, `/api/upload`. The old `/api/edits` and `/api/match`
remain working through the compatibility layer of phase 4 until phase 7 removes them.

---

## 5. Phases

### Phase 0 — Baseline and safety net

**Goal.** Freeze today's behaviour as reference outputs before anything moves.

1. Confirm with the owner that the working tree is committed (it holds others' uncommitted changes). Start
   `impl/phase-0` from that commit.
2. Triage `BUGS.md`: for each bug, check whether it is still present. Fix the ones in files this plan touches
   (`lunar_finish.py`, `panel_classifier.py`, `mosaic_stages.py`, `mosaic_builder.py`, `ui/mosaic.js`). Leave
   `moon_atlas/` and `mosaic/experiments/` items alone and say so in BUGS.md.
3. Add `mosaic/tests/synthetic.py`: deterministic generators (fixed seeds) for
   - a lunar disk with crater-like texture, limb darkening, a black sky, a mild colour cast and **enclosed deep
     crater shadows below 1022 ADU**, 16-bit RGB and mono variants, plus a **crescent** variant (terminator and
     night side);
   - a cut of that disk into overlapping panels (sizes varying by up to 15 %), optionally at a second scale and
     with optional per-panel radial distortion;
   - the white-balance probe of Appendix F (sky pedestal and surface cast with known values);
   - a band-limited texture with a known cutoff frequency plus white noise (for `measurements.cutoff`).
   Reuse `lrgb_workflow.detail()` where it fits.
4. Add `mosaic/tests/golden.py`, which runs today's `lunar_finish.py` on a 768² synthetic disk with **every flag
   explicit**, so `.env` cannot leak in:
   - (a) `--no-wavelets --keep-orientation --no-preview --no-display-tif --highlights 0.3 --knee-percentile 90
     --saturation 2 --saturation-passes 2 --contrast 0.12 --chroma-denoise 3 --limb-taper 0`;
   - (b) the same with `--wavelets 0.6,1.1,0.2,0 --wavelet-kernel linear --usm 60,1.3,0 --usm-mode luminosity`
     in place of `--no-wavelets`;
   - (c) case (a) on the mono variant.
   Store the outputs as `mosaic/tests/fixtures/golden_*.tif`, 16-bit, zlib-compressed (a 768² RGB case is
   3.5 MB uncompressed; keep the three under 10 MB). Commit the fixtures. They carry OpenCV's Lab error (D17); phase 1 records it and re-captures them.
5. Record builder determinism: build the synthetic panel set twice with identical flags and note in the test
   output whether the mosaics are bit-identical. Phase 5's single-scale check depends on the answer.
   **Recorded (`determinism.py`): three full builds were bit-identical; the layout reports differed only in
   their `created` timestamp.** The fixture must be big enough to exercise fine alignment — see phase 5's traps.
6. Add a test runner script `mosaic/tests/run_all.py` that runs every suite and exits non-zero on any failure.

**Accept.** All existing tests green; golden fixtures committed; determinism recorded; `run_all.py` green.

### Phase 1 — Filters into their own module, then exact Lab

**Goal.** One home for the numerical filters, imported by everything else; then the one deliberate numerical
change, exact Lab (D17), isolated so it is measured on its own.

1. Create `mosaic/filters.py` and **move** (not copy) from `lunar_finish.py`: `KERNELS`, `atrous`,
   `wavelet_margin`, `wavelet_gain`, `wavelet_sharpen`, `usm_sigma`, `unsharp`, `layer_noise`, `lunar_weight`,
   `LUNAR_BANDS`, `guided_self`, `shoulder`, `s_curve`. `lunar_finish.py` imports them back
   (`from filters import …`) so its public names still resolve.
2. Point `sharpen.py` at `filters` (keep `layer_noise` importable through it: `lrgb.py` calls
   `sharpen.layer_noise`).
3. Add `rgb_to_lab` and `lab_to_rgb` to `filters.py` (float32, the constants of §4.3) and switch every Lab
   conversion in `lunar_finish.py` to them. Run `golden.py --report` to record rms / p99.9 / max against the
   phase-0 fixtures (expect tens of ADU rms: that is OpenCV's error leaving), then `golden.py --capture` to
   write `golden_*_exact.tif`. Phase 2 compares against these, and `golden.py` switches to them by itself.
   The builder keeps its own conversions. **Done:** 68–79 ADU rms; mono spread 34 → 0 ADU; float64 in chunks was
   needed (float32 left saturated colours 0.49 ADU off per round trip).

**Accept.** After step 2, golden outputs reproduced bit-exactly; after step 3, T-LAB passes and the measured
difference is recorded; both suites green.
- T-LAB: float32 round trip within 0.1 ADU on greys, lunar colours, near-black and random colours; the forward
  conversion within 0.25 L* and 0.4 a*b* of OpenCV's on lunar colours (the size of OpenCV's own error).
**Trap.** `review_regressions.py` reaches some of these through `lunar_finish` (`lf.`); keep the re-exports.

### Phase 2 — The chain engine and the existing blocks

**Goal.** `chain.py` + `blocks.py` running the five blocks that exist today (wavelets, unsharp mask,
highlights, contrast, saturation), with `lunar_finish.py` as a runner and matching results.

1. `blocks.py`: a registry `BLOCKS[type] = Block(params_schema, paths, detail, margin, prepare, apply,
   readouts)`. Parameter schemas, ranges, defaults and formulas are in Appendix A — implement them exactly.
   `detail` marks the blocks skipped in the proxy pass (D1). `validate` returns `(ok, reason)`.
2. `chain.py`:
   - `validate(doc) → list[(block_id, reason)]`
   - `default_chain(args)` — compile `lunar_finish` flags into a chain document (Appendix E).
   - `prepare(sources, doc) → Stats` per §4.5 (proxy at 1/4, probes, space and limb maps, per-block stats,
     readouts).
   - `run_block(block, rgb, stats, scale, limb)` — applies, then limb fade (D6), then clip.
   - `run_chain(blocks, rgb, stats, scale, limb, space)` — D7 after every block; optional `snapshots` list of
     `(label, image)` for the movie.
   - `merge(lum, col, cov_l, cov_c, strength)` — D11; with no colour, the luminance result as grey.
   - `margin(doc)` — the halo.
3. Rewrite `lunar_finish.run()`: after reading and the mono check, build the default chain (or load
   `--chain FILE`) and run the single input image as a **`both` set** (D9) — the luminance chain on it, the
   colour chain on it, the merge, then the final chain. The flag-compiled default chain puts every block in
   the final chain and leaves the other two empty, so the merge sees identical inputs (T-MERGE-ID). Keep strips,
   north-up as the last step, the display TIFF and the printed facts.
4. The movie and the facts: `movie()` and the facts read fixed stage keys today (`small_st["orig"]`,
   `["contrast"]`, `f"colour{k}"`, `["final"]`, `["turned"]`, `info["C_pass"]`). Replace them with the ordered
   snapshots from `run_chain`, labelled by block type ("wavelets", "highlights", "colour pass 1", …), and compute
   each fact only for the block types present. Take the snapshots **on the reduced copy**: converting every
   stage at full resolution only to downsample it costs about 0.35 s per Mpx with exact Lab (measured in phase 1:
   2 s → 8 s on 16.8 Mpx).
5. Add `--chain FILE.json` and `--write-chain FILE.json` (dump the chain the flags would run).

**Accept.**
- T-GOLD against the phase-1 fixtures (`golden_*_exact.tif`), over the disk, **excluding interior pixels whose input L* < 1** (the old
  rule froze them; D7 processes them, and the S-curve's slope near black is 1 − 2c, so they come out up to 24 %
  darker at c = 0.12 — report their count): cases (a) and (b) rms ≤ 1 ADU and 99.9th-percentile |Δ| ≤ 16 ADU;
  case (c) channels equal within 1 ADU. If a case exceeds this, find the cause; do not raise the tolerance.
  Case (c) fails today by 34 ADU — measured in phase 0, and caused by OpenCV's Lab conversion (one float round
  trip gives a perfectly grey image a 70 ADU channel spread). `golden.py` tightens its mono limit from 80 to 1 ADU
  automatically once the exact-Lab fixtures exist.
- T-MERGE-ID: merging identical luminance and colour inputs at strength 1 returns the input within 1e-5 (exact
  Lab; with OpenCV's conversion this would be off by up to 291 ADU).
- T-TILE: for every block and for the default chain, rendering a 1500² region in 256² tiles with the declared
  margin equals rendering it whole: max |Δ| ≤ 1e-5.
- T-ATWT: for random biases in −1…15 (denoise 0, no clipping in the test image), the output equals the input's
  à-trous residual plus Σ `wavelet_gain(b_j)` × layer_j: max error ≤ 1e-5 (measured 3.6e-7). **Do not
  re-decompose the output and compare layer by layer**: the à-trous transform is not idempotent, and a correct
  implementation fails that comparison by 11–268 %. Gains for 0.1, 0.5, 1, 2.3, 5 and −0.5 are 2, 6, 11, 24, 51,
  1/6.
- T-USM: an impulse of 0.02 on backgrounds of 0.25 and 0.50 (no clipping even at 200 %): peak response exactly
  linear in amount (50/100/200 % → 1:2:4 within 1e-6) and equal on both backgrounds; a staircase with threshold
  T matches `I + A·sign(d)·max(abs(d) − T/255, 0)`.
- T-SPACE: on the synthetic disk with a Curves block that lifts shadows (gamma 2 below 0.2): enclosed crater
  shadows are processed exactly like the rest of the disk (output = curve(input) within 1 ADU); a sky ramp from
  0 to 3000 ADU comes out monotonic; sky pixels with L* < 0.5 are unchanged within 1 ADU.
- T-MOVIE: a chain with no contrast block and two saturation blocks records the movie and prints the facts
  without error.
- `lunar_finish --help` still lists every flag; `.env` settings still apply.

**Traps.**
- The highlights statistics must come from the proxy **with the wavelets skipped** — that is what
  `lunar_finish` does (it measures the original). Running the wavelets in the proxy pass moved the result
  1,982 ADU rms in measurement.
- `stats_q` is 4 everywhere, as `lunar_finish`. Changing it moved the tone 120–324 ADU rms.
- The old saturation converts once to Lab and multiplies chroma by `f^k`; a chain of RGB-clipped blocks can
  differ where colours leave the gamut. That is why T-GOLD bounds the 99.9th percentile, not the maximum.
- The old `--usm-mode rgb` sharpened the *sky-restored* contrast output. Restoring after every block (D7)
  reproduces that.

### Phase 3 — New blocks and measurements

**Goal.** Levels, curves, Gaussian blur, white balance, sub-pixel RGB shift, deconvolution, and the
measurements that drive the readouts. Pure functions with tests; no interface yet.

1. `blocks.levels`, `blocks.curves`, `blocks.gaussian_blur` — Appendix A.
2. `blocks.white_balance` — Appendix A (D2, D3). References: `auto` (background = the space region of D7, away
   from no-data; white = disk pixels whose channels all lie within the white limits) or a rectangle in view px
   mapped to canvas px. Measured on the statistics proxy (means and medians survive INTER_AREA).
3. `measurements.py`:
   - `space(lum_proxy)` and `limb_distance(lum_proxy)` — lift the filled-disk logic from `lunar_finish.run()`;
     the space region is the canvas-edge-connected part of L* < 1 (D7).
   - `probes(canvas, proxy, n)` — 1024² crops ranked by texture (fine-band energy over brightness) inside the
     lit disk, and inside the best-resolving tier once phase 5's set report exists; the three for RGB shift are spread
     apart. On a crescent they must land in the lit part.
   - `cutoff(gray, box=1024)` — DESIGN.md §7 method exactly: bright interior patch, Hann window, radially
     averaged power, noise floor = median over 0.42–0.5 cycles/px, cutoff = first frequency above 0.02 where
     power − floor ≤ 0.1 × floor. Returns cycles/px.
   - `suggest_layer(cutoff)` — the finest (lowest-numbered) layer whose whole band `[1/(4s), 1/(2s)]`,
     s = 2^(n−1), lies below the cutoff: the smallest s with `1/(2s) ≤ cutoff`. 0.443 → layer 2; 0.173 → layer 3.
   - `rgb_shift(probes)` (D16) — per crop, each channel minus a Gaussian (σ 8) and divided by its rms. Estimator
     (a): phase correlation with a Hann window, iterated three times (shift the channel by the running estimate
     with Lanczos-4, correlate again, accumulate). Estimator (b): global Lucas–Kanade — pre-smooth σ 1, Sobel
     gradients of green, six Gauss–Newton iterations of the 2×2 normal equations over the crop inset by 32 px.
     Returns every estimate per crop.
   - `fine_detail_amplitude(before, after)` — `lunar_finish`'s measure: ratio of rms of `G(0.8) − G(2.0)` on the
     channel mean.
4. `blocks.rgb_shift` — auto applies the shift only when, per channel and axis, the two estimators agree within
   0.15 px on at least two of the three crops and the mean shift is ≥ 0.1 px; the applied shift is the mean of
   the agreeing estimates. A crop without enough texture is skipped; with fewer than two usable crops the block
   changes nothing and says why. Manual dx/dy per channel overrides. Shift R and B by `cv2.warpAffine`
   translation, `INTER_LANCZOS4`, `BORDER_REFLECT`.
5. `deconv.py` — the PSF port of Appendix B and WPL of Appendix C; `blocks.deconvolution` wraps it. Uploaded
   PSFs: a 16-bit grey or RGB TIFF/PNG, copied into `derived/`, min-max scaled and normalised to unit sum per
   channel.

**Accept.**
- T-WB: on the Appendix F probe, gain ratios R:G:B = 1.1369 : 1.0800 : 1.0000 within 0.2 %; the
  background-subtracted surface comes out neutral within 0.1 %; the sky is unchanged within 1e-5 (the background
  is the fixed point); the background-subtracted white keeps its mean within 0.1 %. With `neutralize_background`,
  sky channels equal the target within 1e-4.
- T-CUT: on band-limited textures with true cutoffs 0.15, 0.25, 0.40, `cutoff()` is within ±0.03 (measured:
  within 0.001); `suggest_layer(0.443) == 2`, `suggest_layer(0.173) == 3`.
- T-SHIFT: texture with R +0.30/−0.20 px and B −0.25/+0.15 px: with equal blur both estimators within 0.05 px;
  with red blurred by σ 1.2 px and 15 % less contrast, both within 0.10 px and agreeing; the block's residual
  ≤ 0.10 px. On the crescent, all probes lie in the lit part. A textureless crop is skipped with its reason.
- T-PSF: Appendix B vectors reproduced to 1e-6 (mono flag: all channels use 532 nm); for R 29, S 3, D 150 the
  normalised PSF's minimum is exactly 0 (its raw min/max is 0.0103), proving the min-max scaling.
- T-DECONV: a sharp synthetic texture blurred by a synthetic PSF plus 0.2 % noise: 10 iterations reduce RMSE
  against the sharp image by ≥ 30 % and never produce NaN or values outside [0, 1] (a PSF left off-origin
  shifts the result by 32 px and fails this); T-TILE holds with the declared margin at **100 iterations with
  dering strength 0.5** (max |Δ| ≤ 1e-3).
- T-LEVELS/T-CURVES: identity settings are exact identities; Levels with black 10, white 245, gamma 1.2 match
  the Appendix A formula; a Curves spline passes through its control points.
- T-ORDER: a pure-red input stays red through every block (catches RGB/BGR swaps).

### Phase 4 — Schema 4 and the application on chains

**Goal.** The application stores and renders chains; the current interface keeps working through a
compatibility layer.

1. `project.py`: schema 4 (§4.6), `migrate_3_to_4` (Appendix D), validation of sets, geometry, view, chain.
   Old projects open, migrate in memory, save once.
2. `lrgb.Scene` per §4.5 and D13: `render_canvas(rect, stage)` runs the chains on the canvas grid;
   `render_view(matrix, size, view, precise)` maps a view or output tile to its canvas bounding box (+ halo +
   4 px), renders it, rotates with Lanczos-4 and reduces with INTER_AREA. Build the rotation from
   `Image.warp`'s explicit inverse maps, quantised to OpenCV's 1/32 interpolation grid *before* the tile origin
   is subtracted — the current code does this because `warpAffine`'s tile-dependent rounding otherwise makes
   neighbouring tiles disagree. Views `composite`, `luminance`,
   `colour`, `overlay`, `coverage`, `errors` as today, plus `resolution` (phase 5). `export()` keeps its streaming
   writers; an export at ½ or ¼ runs the chains at full resolution and reduces afterwards, the only way it
   equals a downsampled full export.
3. **Compatibility layer — budget real work for it.** `lrgb_workflow.py` holds 56 references to the schema-3
   shape (`p.data['sets']['L']`, `DEFAULT_EDITS`, `p.data['match']`, `scene.render(…, edits, …)`), and `mosaic.js`
   reads `p.match` 32 times, `p.sets.L` 8 times and `p.edits` 7 times. So: `Project.public()` serves the
   schema-3 shape at the top level (computed from schema 4) for the current interface, with the schema-4 data
   under `schema4`; `/api/edits` and `/api/match` write through it; `Scene.render_edits(matrix, size, edits,
   view)` keeps the old signature by translating edits into a chain. Update `lrgb_workflow.py` to schema 4
   where it touches `Project` directly (mechanical), and keep its rendering assertions through
   `render_edits`. Phase 7 removes the layer.
4. The statistics worker of §4.5 — not a job.
5. Delete `sharpen.py`; its callers use the chain.

**Accept.**
- `lrgb_workflow.py` green (list every assertion that changed and why).
- T-MIGRATE: a schema-3 project with every edit set to a non-default value migrates to the chain of Appendix D,
  reopens identically, and a second open is a no-op (revision unchanged apart from the first save).
- T-PREVIEW: a 700×500 preview at a rotation of 23.7°, rendered in process or through `/api/render` with
  `depth=16`, equals the same region of a full-scale TIFF export within 1 ADU at 16-bit. This proves D1 and D13.
- T-COVER: an export with common coverage equals the same region of a full-coverage export within 1 ADU
  (statistics do not depend on the crop).
- T-STALE: two clients — the second posting with an old revision gets 409 and its edit is **kept** on its side
  (the inverse of BUGS.md bug 11).
- T-WORKER: moving a slider during an export neither fails nor changes the export.

### Phase 5 — Mixed-scale luminance

**Goal.** Several luminance sets at different scales build into one mosaic on the finest grid, the best-resolving
data winning wherever it reaches. DESIGN.md §1 and §8.3.

1. **Scale per set.** From optics: `arcsec/px = 206.265 × pixel_um / (focal_mm × barlow)`. Without optics (D10):
   find candidate pairs across two sets with `mc.candidate_pairs` on the union of their proxies, match up to
   five of them with `verify_pair` using a wide gate (0.15–7), requiring ≥ 60 inliers and inlier ratio ≥ 0.4;
   take the median ratio; show it for confirmation. Refuse to build a mixed stream with an unconfirmed ratio.
2. **Panel scales file.** `mosaic_builder.py --panel-scales FILE` with
   `{"panels": [{"path", "set", "scale_rel"}], "canvas_set": "s1"}`, `scale_rel` = panel arcsec/px ÷ finest
   arcsec/px (≥ 1). **Do not reuse `--manifest`**: it is the panel classifier's manifest (rejects,
   `--exclude-tier`, movie ranking). Without the file every panel has `scale_rel = 1` (today's behaviour).
3. **Pair gate.** `verify_pair(..., expected=1.0)` gates on `0.97·expected < s < 1.03·expected`. The proxy
   transform maps panel a → panel b, so a coarse a (`scale_rel` 2) against a fine b (1) has `expected = 2`:
   `expected = scale_rel[a] / scale_rel[b]`. Update both call sites (`mosaic_builder.py` around lines 969 and
   1111). Write the unit test first; the direction is easy to invert.
4. **Reference panel** from the finest set only (smallest arcsec/px — it fixes the canvas scale; it need not be
   the best-resolving set): `ref = max(main ∩ finest, key=degree)` (today
   `max(main, key=…)` around line 1065). If no finest-set panel joined the main group, fail with "The finest set
   did not join the mosaic", naming the set.
5. **Tiered selection (D14).** Before building, measure each luminance set's resolution: `measurements.cutoff` on
   up to three textured panels (bright interior crops up to 1024²); finest detail = the set's arcsec/px ÷ cutoff,
   median over the panels. When no panel has enough texture, fall back to max(2 × arcsec/px, the diffraction FWHM
   1.028 λ/D when the aperture is known, λ = 550 nm unless the set's filter says otherwise) and say so. Sets within
   10 % form a tier. In `select_regions`, run the existing two passes — best score, then the speckle clean-up — tier
   by tier, best-resolving first, each restricted to cells that no better tier validly covers (validity is the panel's existing `valid` mask, so the `border` logic
   is unchanged). Within a tier, `sharpness_map` runs on native panel pixels as today: same scale, so the
   comparison is fair. Worse-resolving tiers meet better ones at the better tier's coverage edge; the multi-band blend
   softens the seam and the `resolution` view shows it.
6. **Set report.** Write `<out>_sets.json`: set ids, each set's measured resolution, tiers, `scale_rel`, panels per set, and a `label → set`
   table, so the application can draw the `resolution` view (each region tinted by the set that supplied it,
   with a legend giving each set's arcsec/px).
7. **Canvas size.** Before building, `/api/canvas-estimate` reports a **lower bound**: the Moon's diameter at
   perigee (about 2010″; NASA's mean is 1896″) ÷ the finest arcsec/px, in px, Mpx and GB at 16-bit RGB — panels
   overhang the limb, so the real canvas is larger. Above a configurable threshold (default 400 Mpx) the import
   screen asks before building and offers a reduced canvas. When the finest set's pixels are much smaller than its
   measured detail (more than about 3 px per finest detail), the prompt says so and suggests detail ÷ 2.5 as the
   canvas scale; the default stays the finest pixel scale (DESIGN.md §1). The reduction is `--canvas-scale c` (< 1): every panel is downsampled
   by c (INTER_AREA) when read, in memory only. Panels are read at two sites (`mosaic_builder.py` stage 1 and
   `blend_panel` in `mosaic_stages.py`, both through `mc.read_image`, which gains a `scale` argument);
   `header_size` only feeds the preflight report. After layout (stage 3) the real canvas size is known: log it
   and stop before blending if it exceeds the confirmed cap.
8. App: build all luminance sets in one builder run with the panel scales file; parse progress as today.

**Accept.**
- T-MIX: synthetic disk cut into 12 fine panels (scale 1) and 6 coarse panels (scale 2, covering more area)
  → one joined mosaic; canvas scale = fine; ≥ 95 % of the fine-covered area labelled fine; seam residual
  ≤ 0.5 canvas px.
- T-GATE: `expected` direction verified on a known pair (coarse→fine accepted, fine→coarse with the wrong
  ratio rejected).
- T-SHARP: a fine panel and a 2× coarser panel of the same area — the coarse one unsharpened, then re-sharpened
  with a 100 % and a 200 % unsharp mask: the coarse panel wins **0 %** of the cells the fine panel validly covers
  in every case. And a set at the finest pixel scale but blurred by σ 3 px (an oversampled small telescope) loses
  every overlap to a 2× coarser, sharper set, because tiers follow measured resolution.
- All regressions green; a single-scale build without the panel scales file equals the old output **bit for
  bit** (phase 0 recorded that full builds are bit-reproducible).

**Traps.** Do not try to make per-pixel sharpness comparable across scales by resampling the panels: measured,
it does not survive pre-sharpened coarse data (D14). Do not order tiers by pixel scale either: an oversampled set
would then win everywhere it reaches. `proxy_factor` is one integer for all panels; with large
scale ratios coarse proxies get small — acceptable up to about 4×, beyond that warn in the log. `fine_align`'s
`tiles_for_pair` already warps B into A's frame before correlating, so it is scale-aware — do not "fix" it.
**Size synthetic test panels for the stage under test.** Fine-alignment tiles are 256 px and their centres must
sit `tile/2 + 48` = 176 px inside *both* panels, so an overlap below about 352 px yields no tiles and the builder
quietly falls back to keypoint placement — a test would then pass without exercising fine alignment. And the
default proxy factor of 4 leaves too few SIFT keypoints on small panels (below the 150 minimum). Measured in
phase 0: 2048 px disk, 1024 px panels, overlap 0.45, `--proxy-factor 1` runs every stage in about 10 s;
assert from the log that tiles were found and the mesh ran (`determinism.py` shows how).

### Phase 6 — Colour placed per panel onto the luminance canvas

**Goal.** Replace the single global colour match with per-panel registration onto the luminance mosaic
(DESIGN.md §3), producing a colour canvas (D8).

`colour_place.py` (CLI, run by the app as a job, progress lines `[k/N] panel name`):

1. **Find each panel, at three levels.** (i) Global: the luminance canvas reduced so its long side is about
   2048 px, the colour panel's luminance resampled to the same arcsec/px; SIFT, RANSAC similarity, gate
   0.95–1.05 (pre-scaled), rotation free. (ii) Intermediate: at canvas/2 around the global footprint, phase
   correlation of overlapping 512-px tiles, then a similarity fit. (iii) Fine: 256-px canvas tiles with texture;
   warp the panel's luminance into each tile's frame and phase-correlate (`mc.phase_shift`, which works on
   copies — see `mosaic_tool_spec.md`); keep tiles with response ≥ 0.05 and |shift| ≤ 6 px. The intermediate
   level exists because on a 14,792-px mosaic the global level is reduced about 7×: its 1-px RANSAC threshold
   is ±7 canvas px, outside the fine window.
2. **Pose model (D15).** Hold out every fourth tile. Fit similarity (needs ≥ 4 tiles), affine (≥ 6) and poly3
   (≥ 12; `mst.PolyModel(3)` with its affine prior), robustly (Huber). Keep the simplest model whose held-out
   RMS is within 10 % of the best; report the model and its held-out RMS.
3. **Reject** a panel with fewer than 4 tiles or held-out RMS > 2.0 canvas px; record the reason. Never drop
   silently.
4. **RGB shift at build** (DESIGN.md §6, D16): estimate per panel with `measurements.rgb_shift`; correct when the
   estimators agree and the shift is ≥ 0.1 px; always log every estimate.
5. **Photometry.** Subtract each panel's per-channel sky median (outside the disk; a panel with no sky uses the
   set's median). Group panels into overlap components (union–find over overlapping pairs). Within a component,
   take per-channel median ratios in each overlap and solve log-gains by least squares with one panel fixed.
   Match each further component to the largest by its median channel ratios on the disk, and say so in the
   report.
6. **Blend** into the colour canvas with K (D8): warp each panel with K⁻¹·T; weights = distance to the panel
   edge (feather 64 colour-canvas px); stored as 16-bit RGBA, alpha = coverage. `lrgb.Image` accepts only 1 or 3
   channels today — teach it the alpha channel, or store coverage as a separate 16-bit PNG. For a `both` set,
   skip placement: the luminance mosaic is the colour canvas.
7. **Report** `colour_place.json`: per panel transform (panel → canvas), model, tiles, held-out RMS, state,
   reason; summary "Match {rms of all held-out tile residuals} px · worst {largest per-panel held-out RMS} px";
   sample points for the `errors` view.

Then `Scene` uses the colour canvas with K instead of the global match; `alignment.measure` runs on the placed
result as an independent check; `lrgb.register` and the `/api/match` path are removed. A single-image colour set
goes through the same path (one panel). Manual control points remain available for single-image colour sets
only (multi-panel manual rescue is out of scope).

**Accept.**
- T-PLACE: synthetic luminance mosaic; colour panels made from the same scene at half scale with a per-panel
  translation drifting 0–3 px across the run (simulated dispersion) → recovered within 0.25 canvas px (held
  out); with 1.5 px of radial distortion added at the panel corners → affine or poly3 chosen, held-out RMS
  ≤ 0.35 px.
- T-DE: the colour placement error on the synthetic scene keeps "visibly wrong" (ΔE*ab > 2.3, lightness
  weighted, DESIGN.md §3) below 0.05 % of the disk.
- T-COARSE: colour 6× coarser than the canvas still places (T-PLACE tolerances scaled by k).
- `lrgb_workflow.py` green after replacing match-specific assertions.

### Phase 7 — The interface

**Goal.** The screens of `interface-proposal.html` on the new backend. Vanilla JS ES modules, no framework,
no build step, LunarAtlas's stylesheet (`mosaic/ui/` today shows the pattern). The server serves only listed
files, so add routes for the new modules; its authentication is a same-origin cookie, which module imports
carry.

1. **Import.** Two streams, each holding any number of sets; per set a label, optics (LunarAtlas equipment
   fields plus aperture) and its files. Scale ratios stated in words ("colour samples 2.1× coarser than
   luminance"). Canvas lower bound with the cap prompt (phase 5). A single colour mosaic can be marked `both`.
2. **Build.** The nine builder steps, then "Place colour" with per-panel progress. Interrupted and failed states
   as today.
3. **Match.** The persistent chip "Match 0.6 px · worst 3.1 px"; popover with a per-panel table (placed or
   rejected with the reason, pose model, held-out RMS); `errors` view.
4. **Editor.** View rail (adds `resolution`); layer rows with visibility; the chain panel: three chains plus
   the merge strength; block cards with the parameters of Appendix A in the owner's units; PixInsight gain
   shown beside each wavelet bias; add-block menu (blocks not allowed on a path are greyed with the reason);
   reorder by drag and by keyboard; enable toggle; duplicate; delete; limb fade field on every block. Readouts
   per block from `/api/analysis`: wavelets — suggested layer from the cutoff, fine-detail amplitude; white
   balance — measured gains and ratios, plus the fixed sentence "PixInsight sometimes also rescales overall
   brightness; this block never does"; RGB shift — every estimate and whether it was applied; deconvolution — a
   warning when it is not first in its chain, and the first dark ring's real radius. Wavelets and unsharp mask
   stay collapsed until selected (the owner's earlier instruction).
5. **Previews.** Draw named rectangles in the view; list them; selecting one renders it at 1:1 through
   `/api/render` (§4.2); they save with the project.
6. **Save.** Debounced autosave with revision checks; on any failure the pending edit is **kept** and re-sent,
   and the user is told (BUGS.md bug 11 is the anti-pattern).
7. **Export** as today (TIFF/PNG 16-bit, JPEG bounded, layers separately), plus chain export/import.
8. Remove the compatibility layer, `/api/edits` and `Scene.render_edits`; update `lrgb_workflow.py` to the new
   API.

**Accept.** A browser journey on synthetic sets: import two luminance sets at different scales and one colour
set → build → placement chip shows measured numbers → add wavelets, white balance and curves → draw a preview →
change a parameter (every preview re-renders) → rotate the view (previews follow) → reload the page (state
identical) → export TIFF → reopen project. Keyboard reordering works. No console errors.

### Phase 8 — CLI parity and documentation

1. `lunarmosaic.py new` accepts repeated `--luminance PATH… [--optics P,F,B]` and `--colour PATH… [--optics …]`
   groups (pairing each `--optics` with the preceding group needs a custom argparse action); `build` builds and
   places; `chain export|import PROJECT FILE`; `export` unchanged.
2. `lunar_finish.py --chain` documented; `tool_settings.py` descriptions updated; regenerate `.env.example`
   and `mosaic/docs/COMMANDS.md` with `tool_settings.py --example` as today.
3. Update `mosaic/README.md`, `mosaic/docs/LRGB.md`; DESIGN.md §9 divergence table emptied of finished rows.
   Mark DESIGN_DECISIONS.md and IMPLEMENTATION_STATUS.md as historical.

**Accept.** Every command in COMMANDS.md runs on the synthetic data; `run_all.py` green.

### Phase 9 — Later (not part of "first try")

- Build resume from completed stages (DESIGN.md §9 table): needs per-stage caches in the builder.
- External parity checks, run only where the applications and evidence exist:
  - V1 PixInsight ATWT against the calibration outputs in `/Volumes/Astro/moontest_04102026/atwt_calibration/`
    (already 0.51 % rms).
  - V2 Photoshop Levels and Curves through the `osascript` harness (DESIGN.md §10).
  - V3 LuckyStackWorker WPL output on one panel.
  - V4 white balance against PixInsight's three outputs in `/Volumes/Astro/moontest_04102026/cc_calibration/`
    (LZW: read with OpenCV): with the gains rescaled to PixInsight's normalisation (smallest gain 1), within
    1 ADU.
- Open design questions (DESIGN.md §8): daylight sky model, the 0.2 px red edge excess, structure detection.
  Do not implement these without an owner decision.

---

## 6. Test matrix

| Test | Phase | Proves |
|---|---|---|
| T-GOLD (a)(b)(c), determinism record | 0 → 2, 5 | the refactor changed nothing it should not |
| T-TILE | 2, 3 | tiles, previews and export agree |
| T-ATWT, T-USM | 2 | the PixInsight and Photoshop laws as implemented (DESIGN.md §5.1, §5.2) |
| T-LAB | 1 | exact Lab, OpenCV's error measured and removed (D17) |
| T-MERGE-ID, T-MOVIE | 2 | the CLI runs whole chain files; the movie survives any chain |
| T-SPACE | 2 | space stays black without posterising shadows (D7) |
| T-WB | 3 | ColorCalibration with the background as fixed point (DESIGN.md §5.3) |
| T-PSF, T-DECONV | 3 | LuckyStackWorker PSF (§5.4) and a seam-free deconvolution |
| T-CUT, T-SHIFT | 3 | the measurements behind readouts (§6, §7) |
| T-LEVELS, T-CURVES, T-ORDER | 3 | identities and formulas; no RGB/BGR swap |
| T-MIGRATE, T-PREVIEW, T-COVER, T-STALE, T-WORKER | 4 | schema; D1 and D13; no lost edits; statistics never block work |
| T-MIX, T-GATE, T-SHARP | 5 | mixed-scale luminance in tiers ordered by measured resolution (§1, D14) |
| T-PLACE, T-DE, T-COARSE | 6 | per-panel colour placement (§3, D15) |
| browser journey | 7 | the interface end to end |

---

## Appendix A — Blocks

Common to every block: `id` (8 hex chars), `type`, `enabled` (bool), `limb_fade_px` (≥ 0, 0 = off), `params`.
"Margin" is the halo a tile needs, in canvas px. "Paths": L = luminance, C = colour, F = final. "Detail" blocks
are identity in the statistics pass (D1).

| Type | Params (units, range, default) | Paths | Formula | Margin | Stats | Readouts |
|---|---|---|---|---|---|---|
| `levels` | `black` levels 0–254 (0), `white` 1–255 (255, > black), `gamma` 0.10–9.99 (1.00), `out_black` 0–255 (0), `out_white` 0–255 (255) | L C F | `ob + (ow−ob)·clip((x−b)/(w−b), 0, 1)^(1/γ)`, all levels ÷ 255, same on each channel | 0 | — | — |
| `curves` | `points` [[in, out]…] in levels, 2–16 points, `in` strictly increasing | L C F | natural cubic spline through the points, flat beyond the end points, clamped 0–255, applied by a 65 536-entry LUT on each channel | 0 | histogram (256 bins, channel mean) of the block input on the proxy, for display | — |
| `highlights` | `strength` 0–0.49 (0.30), `knee_percentile` 0.1–99.8 (90) | L F | `lunar_finish`, on L*/100: `base = A·L + B`, `L1 = shoulder(base, knee, top, h) + (L − base)` | 0 | A, B on the proxy (`guided_self`, radius `max(4, 64 // 4)` = 16 proxy px = 64 canvas px, eps 0.03²); knee and top = the knee-percentile and 99.99th percentile of base on the disk | share of disk above the knee |
| `contrast` | `strength` 0–0.49 (0.12) | L F | `lunar_finish` `s_curve(L, m, c)` on L*/100 | 0 | m = median L*/100 of the disk | brightness spread before → after |
| `wavelets` (detail) | `biases` 1–8 values, −1…15 (all 0), `kernel` linear or b3 (linear), `denoise` k per layer ≥ 0 (0) | L C F | per channel on stored values: `filters.wavelet_sharpen` with gain `1 + 10b` (b ≥ 0) and `1/(1 + 10·abs(b))` (b < 0) | `wavelet_margin(n)` | layer noise from the full-resolution probe (channel mean) | gains; suggested layer (cutoff); fine-detail amplitude |
| `unsharp_mask` (detail) | `amount` % 0–500 (0), `radius` px 0.1–250 (1.0), `threshold` levels 0–255 (0), `mode` rgb or luminosity (rgb) | L C F | `I + (A/100)·sign(d)·max(abs(d) − T/255, 0)`, `d = I − G_σ*I`, `σ = r·max(1, 1.080 − 0.0339 ln r)` (measured at r = 1, 2, 4; extrapolated elsewhere); rgb = each channel, luminosity = on L*/100 | `ceil(4σ) + 1` | — | fine-detail amplitude |
| `gaussian_blur` (detail) | `sigma` px 0–50 (1.0) | L C F | `cv2.GaussianBlur`, BORDER_REFLECT_101; on the colour path it acts as chroma denoise | `ceil(4σ) + 1` | — | — |
| `deconvolution` (detail) | `psf`: `{kind: synthetic, airy_radius` px 1–100 (3), `diffraction_intensity` 10–200 (100), `seeing_index` 1–5 (3)`}` or `{kind: file, path}`; `iterations` 1–100 (10); `dering_strength` 0–1 (0), `dering_radius` px 1–20 (3); `blend_raw` 0–1 (0) | L C F (best first) | Appendix C, per channel with that channel's PSF | 128, validated at 100 iterations (Appendix C) | mono flag (data mono → 532 nm for all); dering gradient threshold from the probes | warning if not first in its chain; first dark ring radius = 0.284 / 0.324 / 0.384 × R (465 / 532 / 630 nm) |
| `white_balance` | `background` `{mode: auto or rect, rect}`, `white` `{mode, rect}`, `white_low` 0–1 (0.10), `white_high` 0–1 (0.99), `structure_detection` false only, `neutralize_background` (false), `background_target` 0–0.2 (0) | C F | `B_c` = median of the background reference; `W_c` = mean of the white reference − `B_c`; `g_c = ΣW / (3·W_c)` (D2); `out_c = B_c + g_c·(x_c − B_c)`; neutralising then adds `target − B_c` | 0 | B, W, g (proxy) | gains; ratios; "PixInsight sometimes also rescales overall brightness; this block never does" |
| `rgb_shift` (detail) | `mode` auto or manual (auto), `manual` `{r: [dx, dy], b: [dx, dy]}` px, ±5 | C (and per panel at build) | translate R and B, Lanczos-4, BORDER_REFLECT | 9 (5 px maximum shift + Lanczos 4) | every estimate per crop and estimator; agreement (D16) | estimates; applied or why not |
| `saturation` | `factor` 0.1–8 (2.0), `passes` 0–5 (2), `lunar_hue` (true), `chroma_blur_px` 0–20 (0) | C F | `lunar_finish`: blur a*b* by `chroma_blur_px`, `f = 1 + (s−1)·w(hue)`, `w = lunar_weight` (or 1 when `lunar_hue` is false), chroma × `f^passes` | `ceil(4·chroma_blur) + 2` | — | chroma on lunar hues before → after |

Limb fade (any block): `w = clip(limb_distance_px / limb_fade_px, 0, 1)`, `out = in + w·(block(in) − in)`; the
limb distance is measured to the edge of the lit, hole-filled disk, terminator included.

Space protection (chain level, D7): after every block, `out = p·chain_input + (1 − p)·out` with `p = s·t`.

## Appendix B — LuckyStackWorker 7.0.0 PSF (verified port)

Verified 2026-10-05 against the real code (24 parameter sets; Airy and log stages to 1e-7). Copy into
`deconv.py`.

```python
def bessel_j1(x):
    """J1 via Abramowitz & Stegun 9.4.4/9.4.6 polynomial approximations (|err| < 1.3e-8)."""
    x = np.asarray(x, float); ax = np.abs(x)
    small = ax < 8.0
    y = np.where(small, x*x, 0.0)
    num = x*(72362614232.0 + y*(-7895059235.0 + y*(242396853.1 + y*(-2972611.439 + y*(15704.48260 + y*(-30.16036606))))))
    den = 144725228442.0 + y*(2300535178.0 + y*(18583304.74 + y*(99447.43394 + y*(376.9991397 + y*1.0))))
    res_small = np.divide(num, den, out=np.zeros_like(num), where=den!=0)
    z = np.where(small, 1.0, 8.0/np.where(ax==0, 1.0, ax)); y2 = z*z
    xx = ax - 2.356194491
    p1 = 1.0 + y2*(0.183105e-2 + y2*(-0.3516396496e-4 + y2*(0.2457520174e-5 + y2*(-0.240337019e-6))))
    p2 = 0.04687499995 + y2*(-0.2002690873e-3 + y2*(0.8449199096e-5 + y2*(-0.88228987e-6 + y2*0.105787412e-6)))
    res_big = np.sqrt(0.636619772/np.where(ax==0, 1.0, ax))*(np.cos(xx)*p1 - z*np.sin(xx)*p2)*np.sign(x)
    return np.where(small, res_small, res_big)

SIZE = 64
WAVELENGTHS = dict(r=630.0, g=532.0, b=465.0)   # nm; all 532 when the data is mono

def _airy(r, k, radius):
    v = k * (r / radius)
    return np.where(r == 0, 1.0, (2.0 * bessel_j1(np.where(v == 0, 1e-12, v)) / np.where(v == 0, 1e-12, v)) ** 2)

def _gauss_sigma(seeing_index, airy_radius):
    return ((4.0 - (seeing_index - 1.0)) / 8.0 / 2.0) * airy_radius     # = R·(5 − S)/16

def generate_channel(wavelength_nm, airy_radius, seeing_index, diffraction_intensity, size=SIZE, blur=None):
    k = 2.0 * np.pi / (wavelength_nm / 1000.0)                          # wavelength in micrometres
    c = size / 2.0                                                      # centre at 32, not 31.5
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    r = np.hypot(x - c, y - c)
    img = np.where(r <= airy_radius * 3.0, _airy(r, k, airy_radius), 0.0)
    if img.max() > 0:
        img = img / img.max()
    img = np.log1p(img * diffraction_intensity)                         # "diffraction intensity"
    sigma = _gauss_sigma(seeing_index, airy_radius)
    if blur is not None and sigma > 0:                                  # "seeing index"
        img = blur(img, sigma)                                          # cv2.GaussianBlur, BORDER_REPLICATE fits best
    return img, sigma
```

Test vectors (seeing index 5, so no blur; `img[32, 32]` = ln 201 = 5.3033049 in every case):

| R, D | channel | `img[32, 32+R]` | `img[32, 40]` |
|---|---|---|---|
| 7, 200 | r 630 nm | 0.0200227 | 0.2656974 |
| 7, 200 | g 532 nm | 0.2691215 | 0.0061769 |
| 7, 200 | b 465 nm | 0.0072105 | 0.0965668 |
| 15, 200 | r | 0.0200227 | 1.4786523 |
| 15, 200 | g | 0.2691215 | 0.6288030 |
| 15, 200 | b | 0.0072105 | 0.0475072 |

LuckyStackWorker then min-max scales each channel to 16-bit, `(p − min)/(max − min)`. Do the same before
normalising to unit sum. When the PSF has no zero inside its 64×64 frame (a large radius, or seeing blur), the
minimum is a real floor and skipping it changes the normalised PSF: min/max 0.0103 at R 29, S 3, D 150 gives an
8.7 % difference; 0.0988 at R 50, S 1, D 110 gives 15.0 % (with OpenCV's Gaussian, which matches ImageJ's to
7.8e-3 relative).

Note that the "Airy disk radius" R is not the radius of the first dark ring: with `v = k·r/R` and the first zero
of the Airy pattern at v = 3.8317, the ring sits at 3.8317·λ_µm/(2π) × R — 0.324 R at 532 nm.

## Appendix C — WPL deconvolution (this project's definition)

Wiener-filter-preconditioned Landweber, after Wendykier's Parallel Iterative Deconvolution (the library
LuckyStackWorker subclasses). Per channel c:

```
pad      reflect-pad the tile (it already carries the chain halo) to N = cv2.getOptimalDFTSize(size + 2·64)
         per axis (no scipy)
PSF      LuckyStackWorker's scaling first, h = (p − min)/(max − min); then h /= h.sum()
         place the 64×64 PSF in an N×N zero array and np.roll it by (−32, −32) on both axes: its peak (index
         32, 32) must sit at (0, 0), or the result is shifted by 32 px
H  = FFT(h_c);  B = FFT(b)
W  = conj(H) / (|H|² + γ)                 γ = 1e-3 (fixed; not exposed)
H' = W·H   (real, 0…1);   B' = W·B          the preconditioned system
x0 = b
repeat iterations:
    x  = x + IFFT( H'·(B' − H'·FFT(x)) )   Landweber step, relaxation τ = 1 (|H'| ≤ 1 keeps it stable)
    x  = clip(x, 0, 1)                      projection onto the valid range
dering   m = GaussianBlur(dilate((|∇b| > G) or (b > 0.98), ρ), ρ/2);   x = x·(1 − s·m) + b·s·m
         G = the 99.5th percentile of |∇| on the full-resolution probes, measured in prepare() — never on the tile
blend    x = (1 − β)·x + β·b
crop the padding
```

Numerical parity with LuckyStackWorker is not established (phase 9, V3). Keep γ, τ, the dering definition and
the margin in named constants with a comment saying so. The declared margin (128 px) must pass T-TILE at 100
iterations with dering on; if it does not, make the margin a function of the iterations, measure it, and write
that function into Appendix A.

## Appendix D — Migration from schema 3

| Schema 3 | Schema 4 |
|---|---|
| `sets.L` | set `{id: "L", stream: "luminance"}`; its `mosaic` → `luminance.mosaic` |
| `sets.RGB` | set `{id: "RGB", stream: "colour"}`; until phase 6 the old global `match` is kept as `colour.legacy_match` and used by the renderer |
| `edits.rotation`, `mirror`, `coverage` | `geometry` |
| `edits.l_visible`, `rgb_visible` | `view` |
| `wavelets` [a1…a4] (gain 1 + a, on CIE L*) | luminance-chain `wavelets` with biases `a/10` — same gains; the domain changes from L* to stored values (note) |
| `wavelet_kernel`, `wavelet_denoise` | same block: `kernel`, `denoise` = [value]×4 |
| `usm_amount` 0–1, `usm_radius`, `usm_threshold` (fraction, hard cut-off) | luminance-chain `unsharp_mask`, amount × 100 %, radius unchanged, threshold × 255 levels, `mode: luminosity` — threshold becomes soft and σ follows the measured law (note) |
| `highlights`, `contrast` (the app's own curves) | final-chain `highlights` / `contrast` at their defaults, enabled iff the old value was non-zero (note: different curves) |
| `smoothing` σ px | colour-chain `gaussian_blur` with that σ, if > 0 (note: it blurred only a*b* before, weighted by coverage; now it blurs the colour image, whose lightness the merge discards) |
| `saturation`, `strength` | colour-chain `saturation` `{factor: saturation, passes: 1, lunar_hue: false}` (the old one was uniform) if ≠ 1; `merge.strength = strength` |

Every "(note)" adds one sentence to `notes`, shown once in the editor. Migrated chains get `protect_space: true`.

## Appendix E — The default chain compiled from `lunar_finish` flags

Final chain, in this order, each block present only when its flag is active; the luminance and colour chains
stay empty:

1. `wavelets` ← `--wavelets` (biases or the `pixinsight` preset: `[2.3, 0, 0, 0]`, linear), `--wavelet-kernel`,
   `--wavelet-denoise`
2. `highlights` ← `--highlights` (> 0), `--knee-percentile`
3. `saturation` ← `--saturation`, `--saturation-passes` (> 0 and the image is not mono), `chroma_blur_px` ←
   `--chroma-denoise`, `limb_fade_px` ← `--limb-taper`
4. `contrast` ← `--contrast` (> 0)
5. `unsharp_mask` ← `--usm A,R,T` (amount %, radius, threshold levels), `mode` ← `--usm-mode`

`protect_space: true`. The single input image runs as a `both` set (D9). North-up, the display TIFF and the
movie stay outside the chain.

## Appendix F — Reference numbers

| Quantity | Value | Source |
|---|---|---|
| ATWT gains for biases 0.1, 0.5, 1, 2.3, 5, −0.5 | 2, 6, 11, 24, 51, 1/6 | DESIGN.md §5.1 |
| USM σ at radius 1, 2, 4 | 1.080, 2.113, 4.132 | §5.2 law |
| White-balance probe | sky pedestal R .030 G .050 B .040; surface cast R ×0.95 G ×1.00 B ×1.08 → ColorCalibration gains 1.1369 / 1.0800 / 1.0000 | PixInsight 1.9.5, measured |
| PixInsight ColorCalibration on that probe | R `1.13688·x − 0.004106`, G `1.08001·x − 0.004000`, B unchanged (residual 0.29 ADU rms) = `B + g·(x − B)`; sky medians unchanged | fitted on its three outputs, 2026-10-05 |
| Colour tolerance | target 1–2 px; ≤ 6× coarser colour costs nothing | §3 |
| Information cutoffs | 4 Oct IR 0.443 cycles/px → layer 2; 25 Sep 0.173 → layer 3 | §7 |
| Sharpening ceiling (one observer, guidance only) | fine-detail amplitude ≤ ~6×; limb rise ≤ ~3.5 % | §7 |
| Moon apparent diameter | mean 1896″ (NASA), perigee ~2010″ | §11 |
| RGB shift on the 25 Sep colour set | 0.04 px (AutoStakkert RGB align on) → block correctly does nothing | §6 |
| Statistics sensitivity (D1) | proxy factor 1 / 2 / 8 instead of 4: 324 / 173 / 121 ADU rms; cropped export: 24 ADU rms (p99.9 344); highlights measuring wavelet-sharpened data: 1,982 ADU rms (p99.9 10,121) | review, 2026-10-05 |
| Old space rule (D7) | L* = 1 at stored 0.0156 (1022 ADU) through OpenCV's conversion, as the old code computes it (exact math: 0.0143, 937 ADU); shadow lift: 917 → 917 but 1049 → 3707 ADU | review |
| OpenCV float Lab (D17) | round trip rms 66 ADU, p99.9 126, max 291 (greys rms 66, near-black 71); forward L* off by up to 0.23, a*b* by 0.40 on lunar colours; exact math 0.0000 ADU | review |
| Rotation before sharpening (D13) | bilinear at 23.7° keeps 78 % of layer-1 energy (Lanczos-4: 98 %); wavelets 0.6/1.1/0.2 differ by order: 576 ADU rms (p99 1,509) | review |
| Tiers (D14) | per-pixel scoring at canvas scale: fine wins 92 % against a 2× coarser panel unsharpened, 0 % after a 100 % or 200 % unsharp mask. Measured resolution (scale ÷ cutoff): fine 3.3, fine pre-sharpened 3.6, 2× coarser 4.7, oversampled at the finest pixel scale (blur σ 3) 7.6 units | review |
| Pose residuals (D15) | similarity 1.19, affine 0.97, cubic 0.55 px RMS held out, 25 Sep | `mosaic_stages.py:66` |
| RGB shift (D16) | truth +0.30/−0.20: centroid −0.006/+0.046; with red blurred σ 1.2: phase correlation ×3 0.375/−0.221, Lucas–Kanade 0.311/−0.173 | review |
| T-ATWT | re-decomposition 11–268 % error on correct code; reconstruction 3.6e-7 | review |
| Mono through `lunar_finish` | phase 0: output channel spread 34 ADU on a perfectly mono input (one OpenCV float Lab round trip alone: 70.5 ADU, \|a*\|, \|b*\| up to 0.125 on grey); phase 1, exact Lab: 0 ADU | phases 0, 1 |
| Exact Lab (D17) | round trip ≤ 0.004 ADU on greys, lunar colours, near-black; 0.05 on saturated colours (float32: 0.49); grey \|a*\|, \|b*\| < 1e-13; output vs the OpenCV-era code 68–79 ADU rms | phase 1 |
| Builder determinism | three full builds (fine alignment 50 tiles, mesh 876 tiles) bit-identical; layouts equal apart from `created` | phase 0 |
| Fine-alignment tile placement | 256 px tiles, centres ≥ 176 px inside both panels → overlaps ≥ 352 px; default proxy factor 4 → too few keypoints below ~1000 px panels | phase 0 |
