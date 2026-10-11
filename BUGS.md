# Bug Report — LunarMosaic

## Found in use

Bugs found while using the app, newest last. Numbering continues from the review below.

| Bug | Status | Found | Summary |
|---|---|---|---|
| 14 | Fixed 2026-10-07 | 2026-10-06 | Path boxes are text-only; clicking one should open a folder or file chooser |
| 15 | Fixed 2026-10-07 | 2026-10-06 | The up/down arrows on number fields are too small |
| 16 | Fixed 2026-10-07 | 2026-10-06 | Pixel size arrows step by 1 µm instead of 0.1 |
| 17 | Fixed 2026-10-07 | 2026-10-06 | Secondary text is too small to read comfortably |
| 18 | Fixed 2026-10-07 | 2026-10-06 | Editing shows no progress while a block is being calculated (feature request) |
| 19 | Fixed 2026-10-07 | 2026-10-06 | With a preview open, a change still recalculates the main image and every other preview |
| 20 | Fixed 2026-10-07 | 2026-10-06 | The preview viewer cannot zoom in (feature request) |
| 21 | Fixed 2026-10-07 | 2026-10-06 | Sliders span far more than the useful range (USM radius, Fade near the limb) |
| 22 | Fixed 2026-10-07 | 2026-10-06 | Unsharp mask offers an rgb / luminosity choice on the luminance chain, where it changes nothing |
| 23 | Fixed 2026-10-07 | 2026-10-06 | Number boxes show a decimal comma (1,4) next to summaries with a point (1.4 px) |
| 24 | Fixed 2026-10-07 | 2026-10-06 | Levels cannot detect the black and white points itself (feature request) |
| 25 | Fixed 2026-10-07 | 2026-10-06 | A luminance-only (grey) project still shows colour controls (Merge, export dialog) |
| 26 | Fixed 2026-10-07 | 2026-10-06 | The export file name drops the capture date and time, which LunarAtlas reads |
| 27 | Fixed 2026-10-07 | 2026-10-06 | A quoted or relative path creates the project inside the app's own folder |
| 28 | Fixed 2026-10-07 | 2026-10-06 | The export preview can be dragged, and its drag thumbnail is stretched |
| 29 | Fixed 2026-10-07 | 2026-10-06 | Selecting text tints the whole app brown; almost everything is selectable |
| 30 | Fixed 2026-10-07 | 2026-10-06 | The preview viewer cannot be moved (feature request) |
| 31 | Fixed 2026-10-07 | 2026-10-06 | Only one preview can be viewed at a time (feature request) |
| 32 | Fixed 2026-10-07 | 2026-10-06 | A preview cannot switch between luminance, colour and combined (feature request) |
| 33 | Fixed 2026-10-07 | 2026-10-06 | Exporting jumps back to step 2 (Build) |
| 34 | Fixed 2026-10-07 | 2026-10-06 | The app icon in the header is a font character that does not read as a Moon |
| 35 | Fixed 2026-10-07 | 2026-10-07 | A zoomed-in preview cannot be panned by dragging |
| 36 | Fixed 2026-10-07 | 2026-10-07 | Closing a preview after panning/zooming the main view leaves the main view's zoomed detail stale |
| 37 | Fixed 2026-10-07 | 2026-10-07 | Switching a preview's mode back and forth quickly discards in-flight renders instead of reusing them |
| 38 | Fixed 2026-10-07 | 2026-10-07 | The main view sits on a blue background, not black, making it hard to see the image's own edge (feature request: default to black, let user choose) |
| 39 | Fixed 2026-10-07 | 2026-10-07 | Fonts still too small in places: leftover 10–12.5 px sizes and the bundled theme's buttons and tags |
| 40 | Fixed 2026-10-11 | 2026-10-11 | *Open project →* sits jammed under the path box, smaller than the buttons around it |
| 41 | Fixed 2026-10-11 | 2026-10-11 | Levels never shows its histogram (nor its "set from image" buttons) |
| 42 | Fixed 2026-10-11 | 2026-10-11 | Export canvas "Moon centred" centres the mosaic's bounding box, not the lunar disk |
| 43 | Fixed 2026-10-11 | 2026-10-11 | Export refuses to replace an existing file, with no way to say yes |

### Fix notes — 2026-10-07

Fixed in place on `impl/phase-8`, not pushed. Each bug's own section above still records the problem as
found; this section records what the fix actually did, and is honest about what is only partial.

- **14.** A macOS folder/file/save chooser (`osascript`, new `/api/choose` route) behind a *Browse…* button
  beside every path box: new project folder, open project, panel folder, export output, deconvolution PSF.
  Typing still works; on another OS the button says so and the box still takes typed text.
- **15/16.** A shared pair of larger ± buttons (`spinner()` in `chains.js`) replaces the native arrows on every
  number field in the app, each stepping by that field's own amount — 0.1 for pixel size and Barlow, 0.1° for
  rotation, each chain parameter's own step.
- **17.** The app now uses named readable sizes throughout its secondary copy, compact controls and microcopy;
  the previously tiny editor labels, table text and status text share the same type scale.
- **18.** Every canvas, detail and 1:1 preview render has a short-lived server status record. The toolbar shows
  the actual current statistics block, then `completed/total` chain-block work across render tiles, before image
  encoding; thumbnails retain their own loading cue.
- **19.** While one or more preview viewers are open, the main canvas and detail layer wait; only the open
  previews re-render. Closing the final viewer refreshes the canvas once at the current chain state.
- **20.** Wheel-zoom, +/− buttons and a reset-to-100% label are now independent for every preview viewer.
- **21.** The two sliders named (Unsharp mask radius, Fade near the limb) now cover a useful span; so do
  Gaussian blur σ and Levels gamma, found while fixing the first two. The typed box still accepts the full
  range in every case.
- **22.** Unsharp mask's Mode no longer appears on the luminance chain.
- **23.** Numeric boxes are explicit decimal text fields: they display a point consistently with summaries and
  measurements, accept a typed comma as a friendly input spelling, and retain each field's custom ± controls,
  range and validation. Rotation is rounded to 0.01° when shown.
- **24.** Levels gets two "set from image" buttons: black from the sky background or the darkest shadow
  detail, white from the brightest 0.1 % of the disk, each computed on the image arriving at the block.
- **25.** Merge is hidden with no colour placed; the export dialog's title, its colour-coverage and layer
  rows, and the default export layer all follow whether colour is placed.
- **26.** The export file's default name is stamped with the middle input panel's own SharpCap-style capture
  time, when the panel names carry one, and drops "-LRGB" with no colour placed.
- **27.** A pasted path with surrounding quotes, or a relative or empty one, is refused with a clear message
  before anything is written, for the project folder and the export output file. This only changed the HTTP
  path (the browser's text boxes); the CLI and the test suite still pass relative paths from their own
  working directory, exactly as before. **The project this bug was found in is still sitting inside the
  repository** (`'/Users/kristofvandebeek/Desktop/session 04102026-stacked'` under the repo root) — left in
  place; move it by hand.
- **28.** `draggable="false"` on the export preview, both layer thumbnails, the preview strip thumbnails, the
  preview viewer's image, and the manual-match images.
- **29.** The interface itself is no longer selectable (`user-select:none` on `body`); text boxes, the build
  log, `#job-result`, hints, warnings and the placement table stay selectable. The selection colour is now
  set in `mosaic.css` only, so LunarAtlas's own colour is untouched.
- **30, 31.** Each preview opens in its own movable, resizable desktop-style panel. Dragging the title bar
  cannot pan the canvas underneath; position and size are remembered per project/preview in local storage, and
  new viewers cascade rather than stacking exactly on top of each other.
- **32.** The preview viewer got its own Combined / Luminance / Colour toggle (`/api/render`'s existing
  `stage` parameter), shown once colour is placed — independent of the canvas's own L/C buttons, the choice
  this bug's notes left open.
- **33.** Export no longer switches to the Build page from the workspace; its progress and a Cancel button
  show in the workspace's own status bar, and a toast names the output file once it finishes.
- **34.** The header now shows the same drawn Moon LunarAtlas already serves as the browser tab's icon,
  instead of a text character — the plainer of the two options this bug's notes offered.

### Fix notes — 2026-10-07, bugs 35–39

Bugs 36 and 37 came from a review of the uncommitted fixes for 14–34 (regressions in those fixes); 35, 38
and 39 were found in use.

- **35.** Dragging inside a preview viewer's image scrolls it like a hand tool (`Previews.pan()`); a press on
  the viewer's own scrollbars is left alone. The wheel now zooms about the cursor and the ± buttons about the
  centre, so zooming lands where you point. Journey check: zoom in four steps, drag, the body has scrolled.
- **36.** `loadDetail()` used to skip whenever any viewer was open, and nothing reloaded the sharp tile when
  the viewer closed, so a pan or zoom done meanwhile stayed blurry. Viewers are movable panels now (30/31),
  not a cover over the canvas, so a pan or zoom always loads detail; only a chain change actually deferred
  behind an open viewer (bug 19's `mainStale`) holds it back, and the canvas refresh that closing triggers
  reloads it. `refreshPreview()` clears `mainStale` whenever it runs, whatever triggered it. Also: deleting a
  preview from the strip while its viewer is open now counts as a close, so a deferred refresh is not stranded.
- **37.** Preview render requests are cached per preview *and* mode (`"id:mode"`), so switching modes back and
  forth reuses a render still in flight; only a newer chain stamp supersedes one. The loading cue and the
  strip thumbnail follow the mode the viewer shows.
- **38.** The canvas, preview viewers and strip thumbnails sit on `--canvas-bg`, black by default, with a
  *Background* colour picker in the canvas toolbar, kept in this browser (localStorage, like viewer layout).
  For the colour to show anywhere but around the image, `/api/preview` and `/api/viewport` now send RGBA PNGs
  whose alpha is the mosaic's coverage (`canvas_png()` in `app.py`; `render_view` reports `coverage`): off
  the canvas and in gaps there is no data, so the background shows. Before, the viewport tile was opaque
  black over the whole stage, so the blue only showed while that tile was not loaded — which bug 36 made
  common with a viewer open. `/api/render` (viewers) and the export preview stay opaque: they show what an
  export contains. `lrgb_workflow` checks the RGBA tile is transparent off the canvas; the journey checks the
  default, a change, and that the choice survives a reload.
- **39.** Every hard-coded 10–12.5 px size left in `mosaic.css` now uses the named type scale, and the scale
  went up 1 px (micro 13, compact 14, secondary 15, body 16). The bundled LunarAtlas theme's `.btn`
  (12.5–13 px), `.tag`, `.chip`, `kbd` and `.sub` are overridden onto the same scale. LunarAtlas's own CSS is
  unchanged.

### Bug 14 — path boxes are text-only; clicking one should open a folder or file chooser

**Where:** the start screen, `impl/phase-8` at `cc9f86f`.

**Actual:** clicking *New project folder* only places a text cursor. The user must type or paste a
full path such as `/path/to/My Moon.lunarmosaic`.

**Expected:** clicking the box opens the macOS chooser, and the chosen path fills the box.

**Same problem elsewhere:** every path the interface asks for is a plain text input:

| Box | Chooser needed | Code |
|---|---|---|
| New project folder | new folder (name and location) | `mosaic/ui/index.html:11` |
| Project folder or project.json | existing folder or `project.json` | `mosaic/ui/index.html:12` |
| Export → Output file | save as | `mosaic/ui/index.html:66` |
| Panel folder ("Use folder") | existing folder | `mosaic/ui/mosaic.js:139` |
| Deconvolution PSF file | existing file | `mosaic/ui/chains.js:202` |

The panel card also has *Choose a folder of panels* (`webkitdirectory`, `mosaic/ui/mosaic.js:137`),
but that uploads copies of the files, not their path. The other boxes have no equivalent.

**Notes for the fix:** a browser page never sees an absolute path, so the page cannot open the
chooser itself. The local server has to open it, for example with `osascript` (`choose folder`,
`choose file`, `choose file name`) behind a new `/api/choose` route. The page then calls that route
and fills the box with the answer. Typing a path should still work. Opening the chooser on every
click would get in the way of editing a typed path, so a *Browse…* button beside the box, or opening
it only when the box is empty, may suit better.

### Bug 15 — the up/down arrows on number fields are too small

**Where:** first seen on *Camera & optics → Pixel size · µm* in a panel set card, while loading
`~/Desktop/session 04102026-stacked`. Screenshot: `~/Desktop/Screenshots/Schermafbeelding
2026-10-06 om 21.54.12.png`.

**Actual:** the arrows are the browser's native spin buttons. They are a few pixels high inside a
38 px field and hard to hit.

**Expected:** arrows large enough to click easily.

**Cause:** the stylesheet never touches the spin buttons, so every `type=number` input shows the
browser default:

- the four optics fields, `mosaic/ui/mosaic.js:142`
- *Shared rotation · degrees*, `mosaic/ui/index.html:51`
- every chain parameter built by `numberField()`, `mosaic/ui/chains.js:62` (these are 78 px wide,
  `mosaic/ui/style.css:68`)

**Notes for the fix:** the native spin buttons cannot be resized with CSS. A fix hides them
(`::-webkit-inner-spin-button { appearance: none }`, plus `appearance: textfield` for Firefox) and
draws its own − / + buttons that call `stepUp()` / `stepDown()` and fire `input` and `change`.
Doing it once in a shared helper covers all three places.

### Bug 16 — Pixel size arrows step by 1 µm instead of 0.1

**Where:** *Camera & optics → Pixel size · µm*, same session as Bug 15.

**Actual:** an arrow click changes the value by 1, for example 2.9 → 3.9.

**Expected:** steps of 0.1 µm.

**Cause:** all four optics fields are created with `step: 'any'` (`mosaic/ui/mosaic.js:142`). With
`any`, the arrows fall back to a step of 1. That suits focal length and aperture in mm. It is too
coarse for pixel size. Barlow has the same 1 step, where 1.5× and 2.5× are common.

**Notes for the fix:** give each optics field its own step. A plain `step: 0.1` makes the browser
flag a value such as 3.76 µm as invalid and snap it to 3.8 on the next arrow click. Keeping `any`
and stepping by 0.1 in the custom buttons from Bug 15 avoids both.

### Bug 17 — secondary text is too small to read comfortably

**Where:** seen on three screens, but the cause is app-wide.

- Setup, below the sets: the *A set is one camera…* hint, the *Canvas size* estimate, and *Colour is
  optional…* (`mosaic/ui/index.html:23`, `:28`, `:29`).
- Build and place: the set summary (*14 panels from 1 set · building*), the stage lists with their
  numbers, and the *Luminance builds first…* hint (`mosaic/ui/index.html:33`, stage lists from
  `mosaic/ui/mosaic.js`).
- The chain editor (screenshot `Schermafbeelding 2026-10-06 om 22.01.44.png`):
  - each block's settings summary under its name (*L1 1.55*, *43 % · 1.0 px · 0*), 11 px,
    `.block-title small` (`mosaic.css:57`);
  - the chain's caption *on the luminance, before the merge*, 11 px, `.chain-head small` (`:44`);
  - the curve readout (*0→0 · 45→59 · …*) and the explanations, 12 px `.hint`
    (`mosaic/ui/chains.js:160`);
  - the units after a setting's name (*px (0 = off)*, *× noise*), a plain `<small>` inside the 12 px
    `.param-name` (`:64`). The browser shrinks it again, to about 10 px.

  The block names themselves are only 13 px (`.block-title strong`, `:56`).

**Actual:** the body text is 14 px, but nearly all explanatory text is smaller. `mosaic/ui/mosaic.css`
has 27 rules at 12 px (including `.hint`, `label`, `.set-summary`, `.action-strip p`), 24 at
11 px (including `.eyebrow` and the stage numbers), one at 11.5 px and one at 10 px. The stage lists
are 13 px. On a dark background in the dimmer `--text-3` colour, this text is hard to read.

**Expected:** comfortable reading size for every sentence the user is meant to read.

**Notes for the fix:** neither `DESIGN.md` nor `DESIGN_DECISIONS.md` sets a type scale, so there is
no record to keep in step. A small set of size tokens on `:root` (for example 15 px body, 13–14 px
secondary, 12 px only for eyebrows and badges), applied across `mosaic.css`, fixes all of it in one
pass. Moving hints from `--text-3` to `--text-2` would also help contrast. The fix should then be
written into `DESIGN.md` as the record. The editor panel (`--editor-w`) and the 78 px chain
parameter fields need a check afterwards, because larger text may no longer fit them.

### Bug 18 — editing shows no progress while a block is being calculated (feature request)

**Where:** the editing screen, after changing a block's settings.

**Actual:** nothing shows that work is going on. The mosaic and the preview rectangles keep the old
image until the new one arrives, then swap. A slow block (deconvolution, wavelets on a large canvas)
looks the same as a change that did nothing.

**Expected:** the block being calculated shows a progress bar in the chain list, and the bar disappears
when the image is updated.

**Cause:** each image is a single blocking request that returns only the finished PNG:
`/api/viewport` for the mosaic (`mosaic/ui/mosaic.js:471`), `/api/preview` (`mosaic.js:437`) and
`/api/render` for preview rectangles (`mosaic/ui/previews.js:146`). The server (`mosaic/app.py`, the
`/api/viewport`, `/api/preview` and `/api/render` handlers) gives the page no way to ask what is
running.

**Notes for the fix:**

- The server already has a place to hook in: `run_chain()` calls `hook(entry, block, x, domain)`
  before each block runs (`mosaic/chain.py:330`). Recording the block id, its index, and the block
  count there, per render, is enough for an "N of M" bar.
- The page then needs a small status route, `/api/render-status`, which it polls while a request is
  open. Or the server could stream progress over a long-lived connection. Polling matches how
  build jobs report progress today (`#job-progress`, `#status-job`).
- Statistics are computed once per chain on the whole canvas (DESIGN.md §2, "Rendering"). That pass
  may be the slow part, so it should show in the bar too, not only the block steps.
- Several renders can run at once (the mosaic plus each preview rectangle). The bar should follow
  the render the user is waiting for, or show the slowest one.
- A bar per block needs per-block progress inside the slow blocks (deconvolution iterations,
  wavelet layers). Without that, the bar moves block by block, which is still far better than
  nothing.

### Bug 19 — with a preview open, a change still recalculates the main image and every other preview

**Where:** the editing screen, with a preview rectangle open in the 1:1 viewer.

**Actual:** changing a block re-renders everything:

1. the whole-mosaic image, through `scheduleRefresh()` → `refreshPreview()` → `/api/geometry` and
   `/api/preview` (`mosaic/ui/mosaic.js:402`, `:428`), triggered when a chain change is saved (`:59`)
   and again when the state poll sees the new revision (`:508`);
2. the sharp overlay for the current zoom, through `positionImage()` → `loadDetail()` →
   `/api/viewport` (`mosaic.js:462`, `:466`);
3. every preview rectangle, not just the open one: `Previews.render()` calls `load()` for each
   (`mosaic/ui/previews.js:121`), and each is an `/api/render` at 1:1.

The server handles requests on parallel threads (`ThreadingHTTPServer`, reused from
`moon_atlas/atlas_view.py:684`), so these renders compete for the CPU. The preview the user is
looking at arrives later than it would on its own.

**Expected:** while a preview is open, a change renders only that preview. The main image, and the
other previews, catch up once when the preview is closed.

**Notes for the fix:**

- In `mosaic.js`, skip `scheduleRefresh()` and `loadDetail()` while `previews.selected` is set. Keep a
  "main image is stale" flag, and refresh once from `Previews.close()`. The viewer covers the canvas
  anyway, so nothing visible is lost.
- In `Previews.render()`, load only the selected preview while one is open. Load the rest when it
  closes, or when the user selects them.
- Whole-canvas statistics are still needed (DESIGN.md §2, "Rendering": statistics are computed once
  per chain on the whole canvas). That is what lets a preview equal the same pixels of a full export
  exactly, so only the main image's pixels should be skipped, not the statistics.
- The geometry and coverage line (`#status-dimensions`, `#coverage-info`) is updated inside
  `refreshPreview()`. Geometry changes (rotation, mirror, coverage) move the preview rectangles too, so
  those should probably still refresh everything.
- Pairs with Bug 18: the progress bar then has a single render to follow.

### Bug 20 — the preview viewer cannot zoom in (feature request)

**Where:** the editing screen, a preview rectangle open in the viewer.

**Actual:** the viewer shows the preview at 1:1 and can only be scrolled
(`#preview-viewer`, `mosaic/ui/index.html:55`; `.viewer-body` is `overflow:auto`,
`mosaic/ui/mosaic.css:121`). Wheel and toolbar zoom act only on the main mosaic: `stage.onwheel` and
`#zoom-in` / `#zoom-out` / `#one-to-one` / `#fit` (`mosaic/ui/mosaic.js:568`, `:572`). Small detail —
the result of a sharpening or deconvolution change — is hard to judge.

**Expected:** zoom in on the preview (200 %, 400 %, …), pan around it, and return to 1:1.

**Notes for the fix:**

- This needs no new render. The preview already arrives at full resolution, and the image is drawn
  with `image-rendering:pixelated` (`mosaic.css:122`), so enlarging it in the page shows true pixels.
  Set the `<img>` width to `naturalWidth × zoom`, and keep the point under the cursor fixed by
  adjusting `.viewer-body`'s scroll position.
- Use the same controls as the main canvas: the wheel over the viewer, plus the toolbar buttons and
  keys when the viewer is open. The title (`Previews.title()`, `mosaic/ui/previews.js:175`) can carry
  the zoom: `… · 400 %`. Dragging to pan would match the canvas, where scrolling alone is awkward.
- On a Retina screen, today's "1:1" is really 2 × 2 screen pixels per image pixel, because the image
  is sized in CSS pixels. A true 100 % there is `naturalWidth / devicePixelRatio`. The zoom levels
  should count image pixels per screen pixel so that 100 % means the same on every display.
- Keep the zoom when the preview re-renders after a change (`Previews.load()` swaps `src`), so the
  same spot can be compared before and after.

### Bug 21 — sliders span far more than the useful range (USM radius, Fade near the limb)

`numberField()` gives the slider and the typed box the same min and max (`mosaic/ui/chains.js:57`–`63`),
so each slider runs linearly over everything the parameter accepts. Two cases reported:

**Unsharp mask → Radius** (screenshot `Schermafbeelding 2026-10-06 om 22.02.16.png`). The range is
0.1–250 px (`'radius': Param('float', 1.0, 0.1, 250)`, `mosaic/blocks.py:146`). Every radius used on
lunar data (about 0.3–3 px) lies in the first 1 % of the track, so the slider cannot set one.
*Expected:* a slider from 0 to about 8 px.

**Fade near the limb**, on every block (screenshot of 2026-10-06, Levels open). The range is
0–2000 px (`chains.js:431`; the server only requires ≥ 0, `mosaic/chain.py:85`). The value is the
distance inside the limb over which the block's effect ramps from nothing to full
(`_fade()`, `chain.py:321`). On this session's canvas the Moon's radius is about 2,900 px, so most of
the track fades the block over most of the disk. Limb artefacts from sharpening span a few to tens
of pixels. *Expected:* a much smaller range. 0–200 px is a reasonable start, to confirm on real data.

**Notes for the fix:**

- Give `Param` a separate slider range, for example `slider=(0.1, 8)`, sent to the page with the
  block catalogue. `numberField()` then uses it for the `<input type=range>` only. The typed box and
  server validation keep 0.1–250, so a larger radius can still be typed (the slider then sits at its
  end). Projects and chain files saved with a large radius stay valid.
- A slider minimum of exactly 0 is unsafe. `usm_sigma(0)` takes `log(0)`, which gives NaN
  (`mosaic/filters.py:109`). The slider should start at 0.1, or the block should treat 0 as "off".
- The limb fade is not a block `Param` but a field on every chain entry (`limb_fade_px`), so its
  slider range is set in `chains.js:431`. Lowering only the slider keeps typed values up to 2000 px.
- Other sliders may have the same mismatch, and the same `slider` range would fix them: Gaussian blur
  σ 0–50 px (`blocks.py:331`) and *Threshold* 0–255. Check them with the user before changing.
- Levels *Gamma* (0.1–9.99, `blocks.py:248`) is linear too, so the neutral 1.0 sits at 9 % of the
  track (screenshot of 2026-10-06, Levels open). A gamma slider wants a log scale centred on 1, as in
  Photoshop, where the midtone handle starts in the middle.

### Bug 22 — Unsharp mask offers an rgb / luminosity choice on the luminance chain, where it changes nothing

**Where:** the chain editor, *Luminance chain → Unsharp mask → Mode*. Screenshot `Schermafbeelding
2026-10-06 om 22.02.47.png`.

**Actual:** the block offers *rgb* or *luminosity*, with no hint of what they mean. On the luminance
chain the choice makes almost no difference. Measured with `filters.unsharp()` on a grey test image
(amount 96 %, radius 1.4 px): the two modes differ by at most 2.1 levels out of 255, and by 0.1 on
average. Luminosity sharpens darks slightly less and brights slightly more, because L* is a slightly
different tone curve from the stored sRGB values. Both stay exactly grey. The luminance layer also
contributes only lightness to the merge (`mosaic/lrgb.py:576`).

**Why it is there:** Unsharp mask is one block on all three chains (`PATHS = ('luminance', 'colour',
'final')`, `mosaic/blocks.py:20`), and the editor shows every parameter on every chain
(`mosaic/ui/chains.js:421`). On the colour and final chains the choice does matter: luminosity
sharpens lightness (L*) only and leaves colour alone, so it adds no coloured halos. RGB sharpens each
channel separately.

**Expected:** no Mode choice on the luminance chain. Where it does appear, its meaning should be
clear.

**Notes for the fix:**

- Hide *Mode* when the block sits on the luminance chain. A chain file moved from the luminance chain
  to another chain keeps its saved value, so nothing is lost.
- `choiceField()` shows the raw value as the label (`chains.js:85`). Give choices labels, for
  example *Each colour channel* and *Brightness only*, with a one-line hint.
- DESIGN.md already covers whole blocks: "Blocks that make no sense on a path are shown greyed with
  the reason rather than hidden." It says nothing about single parameters. Hiding a parameter that
  makes no sense on a chain should be recorded there as a decision.

### Bug 23 — number boxes show a decimal comma (1,4) next to summaries with a point (1.4 px)

**Where:** every `type=number` box, for example *Unsharp mask → Radius* in the same screenshot. Spotted
in the screenshot, not reported by the user.

**Actual:** the box shows *1,4*, while the block summary right above it shows *1.4 px*. The browser
formats number inputs in the system's locale (Dutch here).

**Also:** *Shared rotation · degrees* shows `-6,989386413572576` after *Find lunar north* (screenshot of
the whole workspace, 2026-10-06). The value is written into the box unrounded
(`$('#rotation').value = p.geometry.rotation`, `mosaic/ui/mosaic.js:377`). Show it rounded to the
box's 0.1° step, or to 0.01°, and keep the full value in the project.

**Expected:** DESIGN.md item 10, "Numbers are written the English way (6,721 px; 45.2 Mpx), like the
text around them", which the summaries follow.

**Notes for the fix:** setting `lang="en"` on the page or the inputs may not be enough. Browsers
differ in whether number inputs follow the page's language or the system's. A reliable fix is
`type=text` with `inputmode=decimal` and the app's own parsing, which accepts a typed comma as
well. That fits with Bug 15, which already replaces the native arrows.

### Bug 24 — Levels cannot detect the black and white points itself (feature request)

**Where:** the chain editor, *Levels* block (`mosaic/blocks.py:246`).

**Actual:** *Black* and *White* can only be set by hand, by slider or typed value. The block computes
no statistics at all: it has no `prepare`, unlike Curves, whose `_curves_prepare()` builds a histogram
of the image arriving at the block (`blocks.py:309`).

**Expected:** a button beside *Black* that detects the black level and sets it, and the same beside
*White*.

**Notes for the fix:**

- The statistics pass already has what is needed: `ctx.stats.disk` (the eroded lit disk) and
  `ctx.stats.space` (the sky), on the proxy grid (`mosaic/chain.py:195`, `:204`). Highlights already
  takes percentiles over the disk (`_highlights_prepare()`, `blocks.py:155`). A `_levels_prepare()`
  can return candidate black and white levels as readouts. These are measured on the image *arriving
  at this block*, after every block above it. The page shows them through `/api/analysis`, and the
  buttons copy them into the parameters.
- What "black" means needs choosing. It could be the sky background (a high percentile of the
  `space` region, so the sky noise goes to 0), or the darkest lunar detail (a low percentile of the
  `disk`, e.g. terminator shadows). White is naturally a high percentile of the disk, e.g. 99.9 or
  99.99 % (Highlights already reports p99.99 as `top`). Photoshop's *Auto* clips 0.1 % at each end.
- A button sets the value once. If a block above Levels changes later, the levels do not follow.
  The button can be clicked again. An "auto" mode that tracks upstream changes would be a larger
  change, and would make saved chains depend on the data.
- The same `prepare` could also give Levels a histogram like Curves, so the detected points can be
  checked by eye.

### Bug 25 — a luminance-only (grey) project still shows colour controls (Merge, export dialog)

**Where:** the chain editor, *Merge → Colour strength*, in a project with no colour placed (this
session: luminance only, the layer panel reads "Luminance only; the colour chain is kept.").

**Actual:** the Merge section and its *Colour strength* slider are shown, though there is no colour
to merge and the slider changes nothing. The editor always draws all four sections (luminance chain,
colour chain, merge, final chain) whatever the project holds
(`mosaic/ui/chains.js:354`–`361`).

**Also in the export dialog** (screenshot `Schermafbeelding 2026-10-06 om 22.09.42.png` and the one sent
after it), in the same luminance-only project:

- the title is always *Export from both layers* (`mosaic/ui/index.html:63`);
- *Colour coverage for this export* is shown, only disabled (`index.html:68`, `mosaic.js:584`). A
  disabled select hardly looks different;
- *Image* offers *Combined LRGB* and *Luminance layer*, which give the same picture without colour,
  and *Colour layer*, greyed out (`index.html:69`, `mosaic.js:585`);
- the default name is `Moon-LRGB` (`mosaic.js:582`).

The workspace's own *Colour coverage* select (`index.html:51`) is in the same position.

**Expected:** no colour slider or colour options when the image is grey. The export dialog for a
luminance-only project offers the luminance image only, with a title and default name to match.

**Cause:** the page knows whether colour is placed and shown (`m` and `p.view.rgb_visible`, used for
the layer note at `mosaic/ui/mosaic.js:356`), but `editor.show(doc, analysis)` is never told.

**Notes for the fix:**

- Pass the colour state to `editor.show()`, and include it in the key that decides when the
  editor redraws (`shownChain`, `mosaic.js:394`). Otherwise the editor won't redraw when colour is
  placed later.
- With no colour placed, hide *Merge*. The *Colour chain* section has nothing to act on either. The
  layer note says "the colour chain is kept", so its blocks stay saved; they need not be shown, or can
  be shown collapsed with that note.
- With colour placed but the Colour layer switched off, the slider still has no visible effect.
  Greying it out, with a hint to show the Colour layer, keeps it reachable.
- On a grey image the *Colour* group in the final chain's *Add a block* menu (saturation, white
  balance, RGB shift) does nothing either. DESIGN.md's rule — blocks that make no sense are "shown
  greyed with the reason rather than hidden" — would apply there. Check with the user.
- The export dialog already tests `state.project.match` (`mosaic.js:584`–`586`). Use it to hide the
  coverage and *Image* rows, set the title, and drop "LRGB" from the default name, not just disable
  options.

### Bug 26 — the export file name drops the capture date and time, which LunarAtlas reads

**Where:** the export dialog, *Output file*. Screenshot `Schermafbeelding 2026-10-06 om 22.09.42.png`.

**Actual:** the default is `<project folder>/exports/Moon-LRGB.<ext>` (`mosaic/ui/mosaic.js:582`).
The panels carry their capture time in SharpCap / WinJUPOS form (`2026-10-04-0846_6-Moon2_5_…`),
but the export name does not. The TIFF carries no metadata either: the dialog says "Pixels only, with
no observing site or source metadata."

**Does it hurt LunarAtlas?** LunarAtlas looks for a capture time in three places
(`capture_time()`, `moon_atlas/atlas_ephem.py:235`):

1. a SharpCap stamp in the file name (`YYYY-MM-DD-HHMM_T`, `atlas_ephem.py:232`);
2. a time given earlier with `--time`, kept in the sidecar;
3. `<image>_layout.json`, or `mosaic_layout.json`, next to the image. LunarAtlas takes the middle of
   the panel times found there (`_layout_time()`, `:281`).

The built mosaic in the project folder, `derived/L.tif`, has `derived/L_layout.json` beside it, so
LunarAtlas finds its time. The export, `exports/Moon-LRGB.tif`, has none of the three. For this
export, a full Moon with its limb:

- **Still works:** locating, from the limb fit. Night-side labels are hidden using the image's own
  brightness (`light_levels()`), not the time (`moon_atlas/lunaratlas.py:539`).
- **Lost:**
  - the date in the info block (`lunaratlas.py:627`);
  - the Sun direction for the globe view's lit disk (`moon_atlas/atlas_view.py:656`–`664`).
- **In the LunarAtlas app:** a name without a stamp makes the page ask for the time. It pre-fills the
  time from the camera's EXIF (`moon_atlas/viewer/app.js:154`–`156`), but the export has none, so it
  must be typed by hand.
- **Fails outright:** an export that is no longer a full disk, such as a crop or *Common covered
  rectangle*. LunarAtlas treats it as a close-up, and a close-up without a time stops with "a close-up
  needs its capture time" (`lunaratlas.py:123`–`126`, `moon_atlas/atlas_closeup.py:346`).

**Expected:** the export keeps the capture time where LunarAtlas finds it.

**Notes for the fix:** the simplest is a stamped default name, using the middle of the panel times
as `_layout_time()` does. For this session that gives `2026-10-04-0855_0-Moon.tif` or similar. Every
LunarAtlas path already reads that, and it is not "observing site or source metadata". A user who
renames the file loses it, so a `<export>_layout.json`, or a small `_layout.json` with the panel
names beside the export, would also cover a renamed file. Also not "LRGB" when there is no colour
(Bug 25).

### Bug 27 — a quoted or relative path creates the project inside the app's own folder

**Where:** the start screen, *New project folder*. Found in the export dialog's Output file
(screenshot after `22.09.42`):
`/Volumes/Astro/LunarMosaic/'/Users/kristofvandebeek/Desktop/session C…`.

**Actual:** the path was pasted with quotes, `'/Users/kristofvandebeek/Desktop/session
04102026-stacked'` (the way VS Code and Claude Code paste a path with a space). `App.select()`
takes it as is (`mosaic/app.py:132`). `Path(folder).expanduser().resolve()` treats a string starting
with `'` as a *relative* path, resolves it against the server's working directory (the repository,
where `lunarmosaic.py` was started), and `mkdir(parents=True)` creates the whole tree without a word.
The session's project — 14 copied panels and the built luminance, 218 MB — now sits at
`/Volumes/Astro/LunarMosaic/'/Users/kristofvandebeek/Desktop/session 04102026-stacked'/`, untracked
in git (`?? '/`). A `git add -A` would commit it.

A 0-byte `project.json.lock` also sits in the repository root (21:48, before the project was created at
21:53). Probably from *Open project* with an empty box: `Path('')` resolves to the working directory,
`select()` takes the lock there (`app.py:143`), and opening then fails with the lock file left behind.

**Expected:** the project is created where the user meant. A path the app cannot use is refused with
a clear message, before anything is written.

**Notes for the fix:**

- Strip one pair of matching surrounding quotes (`'…'` or `"…"`) and whitespace from every path the
  page sends: `/api/new`, `/api/open`, `/api/inputs`, export output, PSF file.
- Refuse relative paths and empty ones ("Use a full path, starting with / or ~"). The server's working
  directory means nothing to the user.
- Create the lock only after the folder is known to be a project, or remove it when opening fails.
- Bug 14's folder chooser removes most of the typing, but typed paths stay possible.
- Moving the stray project: `project.json` holds only relative paths, so the folder can be moved as a
  whole. `derived/L_layout.json` lists the panels by absolute path, but LunarAtlas reads only their
  names. Left in place for the user to decide.

### Bug 28 — the export preview can be dragged, and its drag thumbnail is stretched

**Where:** the export dialog, the preview image on the right.

**Actual:** pressing and dragging the preview starts a browser image drag. The drag thumbnail is
distorted.

**Cause:** `#export-preview` (`mosaic/ui/index.html:71`) is laid out as `width:100%; height:180px;
object-fit:contain` (`mosaic/ui/mosaic.css:5`). `object-fit` keeps the visible image in proportion,
but the browser builds the drag thumbnail from the element's box, so the image is stretched to the
box. The canvas images already have `draggable="false"` (`index.html:42`); this one does not. Dropped
somewhere, it would also give a small preview PNG, not the export.

**Expected:** the preview does not drag.

**Notes for the fix:** `draggable="false"` on `#export-preview`. Check the other images for the same:
the preview viewer's image and the preview strip thumbnails (`mosaic/ui/previews.js`), the layer
thumbnails, and the point-picking images in the manual match dialog.

### Bug 29 — selecting text tints the whole app brown; almost everything is selectable

**Where:** everywhere. Screenshot of the workspace, 2026-10-06, after a select-all: the toolbars, the
editor panel, the buttons, and the canvas with the Moon all sit under a brown tint.

**Actual:**

- **The colour** comes from the shared theme: `::selection { background: rgba(245, 167, 66, 0.35) }`
  (`moon_atlas/viewer/theme.css:116`). The amber accent at 35 % over the dark navy background reads
  as brown.
- **The extent:** only the mosaic image and the block drag grips are `user-select: none`
  (`mosaic/ui/mosaic.css:4`, `:54`). Toolbars, buttons, headings, labels, the status bar, the
  `#detail` image and the stage are all selectable. Cmd+A, or a drag that starts beside a control,
  selects the whole interface.

**Expected:** the interface itself is not selectable, like a desktop app. Text worth copying stays
selectable, in a colour that suits the dark theme.

**Notes for the fix:**

- `user-select: none` on the app shell (`body`, or the mast, `#workspace` and the panels). Turn it back
  on with `user-select: text` for text worth copying: inputs (always selectable), the build log
  (`pre`), `#job-result`, readouts and error messages.
- A calmer selection colour in `mosaic.css`, for example the accent at about 20 %, or a neutral
  blue-grey. Overriding it there leaves LunarAtlas's `theme.css`, which both apps share, alone.
  Changing `theme.css` would change LunarAtlas too.

### Bug 30 — the preview viewer cannot be moved (feature request)

**Where:** the editing screen, the preview viewer (`#preview-viewer`, `mosaic/ui/index.html:55`).

**Actual:** the viewer is pinned by CSS, to the left of the editor panel at the top
(`.preview-viewer{right:calc(var(--editor-w) + 48px);top:20px;…}`, `mosaic/ui/mosaic.css:118`). Its
title bar has no drag handling; the only pointer handlers are the stage's pan
(`mosaic/ui/mosaic.js:574`) and rectangle drawing (`mosaic/ui/previews.js:64`). It covers part of the
mosaic, which cannot be uncovered except by closing it.

**Expected:** drag the viewer by its title bar to anywhere on the workspace.

**Notes for the fix:**

- Pointer events on `.viewer-head` with `setPointerCapture`, moving the panel with `left` / `top`
  (dropping `right`). Keep it inside the workspace when the window is resized.
- Stop the drag from reaching the stage, or it pans the mosaic underneath.
- Resizing the viewer from a corner (`resize: both`, or a handle) fits naturally with Bug 20's zoom.
- Remember the position per preview in the browser (`localStorage`), not in the project: it is a
  matter of screen layout, not of the image.

### Bug 31 — only one preview can be viewed at a time (feature request)

**Where:** the editing screen, the preview strip and viewer.

**Actual:** there is one viewer (`#preview-viewer`, `mosaic/ui/index.html:55`), and `Previews` keeps a
single `selected` id (`mosaic/ui/previews.js:11`, `select()` at `:180`). Opening a second preview
replaces the first, so two regions (a limb and the terminator, say) cannot be judged side by side
after a change.

**Expected:** several previews open at once, each in its own viewer.

**Notes for the fix:**

- Turn `selected` into a set of open ids and build one viewer per open preview from a template,
  each with its own title, close button and image. `this.selected` is used 11 times in
  `previews.js`, and `mosaic.js` wires the single close button (`:570`).
- Depends on Bug 30: several viewers need to be movable, or they stack on top of one another. New
  viewers can open offset from the last one.
- Bug 19 then reads "render only the open previews", not "the selected one". Bug 20's zoom becomes
  per viewer. Linking the zoom and scroll position of two viewers of the same size would let the
  same detail be compared exactly; worth asking the user.

### Bug 32 — a preview cannot switch between luminance, colour and combined (feature request)

**Where:** the preview viewer, in a project with both luminance and colour placed. Not yet tested by
the user; requested as an idea.

**Actual:** a preview always shows the combined result. `Previews.load()` requests
`/api/render?…&preview=<id>&depth=8` with no `stage` (`mosaic/ui/previews.js:146`), so the server
uses `final` (`mosaic/app.py`, the `/api/render` handler). The canvas's view buttons
(*Composite*, *Luminance chain only*, *Colour chain only*; `data-view`, `mosaic/ui/index.html:44`)
change the main image only.

**Expected:** a toggle in the viewer, *Combined* / *Luminance* / *Colour*, shown only when both
layers are available.

**Notes for the fix:**

- The server needs no change: `/api/render` already takes `stage=final|luminance|colour`
  (`chain.STAGES`, `mosaic/chain.py:32`) and renders it at 1:1, exactly.
- Add the stage to the request and to the cache key (`` `${id}:${stamp}` `` in `load()`), or a
  switch shows the previous mode's image.
- Whether the preview follows the canvas's view buttons or has its own toggle is a choice. Its own
  toggle, together with Bug 31, would allow the same region side by side in luminance and colour.
  That needs a viewer per (preview, mode), not per preview.
- Hide the toggle when no colour is placed, using the same colour state as Bug 25.
- Render only the mode on screen, in line with Bug 19.

### Bug 33 — exporting jumps back to step 2 (Build)

**Where:** the export dialog, *Export image*. Screenshot `Schermafbeelding 2026-10-06 om 22.15.06.png`.

**Actual:** the dialog closes and the app switches to *02 Build*, scrolled to the top of the page. The
export runs there as a job (*Writing image · 100%*, with the output path in the log card at the
bottom). Getting back to editing takes *Open workspace →* or *03 Match & edit*. The jump is in the
code: `action('export', …)` followed by `showPage('build')` (`mosaic/ui/mosaic.js:593`), because the
job card with its progress bar, log and *Cancel* button lives on the Build page
(`mosaic/ui/index.html:36`).

**Expected:** stay in the workspace. Show the export's progress there, and say where the file went
when it is done.

**Notes for the fix:**

- The workspace already shows job progress in its status bar: `#status-job` reads
  "`<title>` · N %" while a job runs (`mosaic.js:381`, `index.html:57`). Dropping `showPage('build')`
  leaves that in place. A *Cancel* next to it is still needed: today the only one is on the Build
  page.
- When the job ends, the poll shows a message only on failure (`mosaic.js:510`). Add a success
  message with the output path, and perhaps *Show in Finder*.
- Staying in the workspace is safe for the chain: the export takes a deep copy of it when it starts
  (`doc = p.chain_doc()`, `mosaic/app.py:1145`; `copy.deepcopy`, `mosaic/project.py:174`), so edits
  made while it runs do not reach the file. `busy` already disables matching, north-up and a second
  export (`mosaic.js:323`, `:360`–`:365`).
- The path in this screenshot is the stray project inside the repository (Bug 27).

### Bug 34 — the app icon in the header is a font character that does not read as a Moon

**Where:** the top bar, left of *LunarMosaic*.

**Actual:** the mark is the text character `◒`, U+25D2 CIRCLE WITH LOWER HALF BLACK
(`<button class="brand" id="home">◒ <span>LunarMosaic</span>`, `mosaic/ui/index.html:5`), set at
28 px in the accent colour (`.brand`, `mosaic/ui/mosaic.css:4`). It shows a circle with its lower half
filled: closer to a gauge or a sunset than a Moon. IBM Plex Sans has no such glyph, so a fallback
font draws it, and it looks different on each system and browser.

The browser tab uses another icon again: `/favicon.svg` is LunarAtlas's drawn icon, a lit crescent on
a navy rounded square (`moon_atlas/viewer/lunaratlas.svg`, served for both apps by
`moon_atlas/atlas_view.py:943`). LunarAtlas uses that same SVG as its header mark
(`moon_atlas/viewer/index.html:42`). So in LunarMosaic the header and the tab do not match, and a
LunarMosaic tab looks like a LunarAtlas tab.

**Expected:** a proper LunarMosaic mark, drawn, the same everywhere, and matching the tab icon.

**Notes for the fix:**

- Draw a LunarMosaic SVG in the style of `lunaratlas.svg`: the same navy square and lit Moon, but
  with the disk divided into panels, for example, so the two read as companions (the start page says
  "A LunarAtlas companion"). Use it as an `<img>` in `.brand`, as LunarAtlas does, and serve it as
  LunarMosaic's own `/favicon.svg`.
- Decide with the user: a new mark, or simply LunarAtlas's icon in the header too.
- DESIGN.md does not mention the brand mark. Record the choice there.

### Bug 40 — *Open project →* sits jammed under the path box, smaller than the buttons around it

**Where:** the start page, *Continue your work* card. Screenshot `Schermafbeelding 2026-10-11 om 15.49.25.png`
(the circled area).

**Actual:** *Open project →* touches the bottom of the path box with almost no gap, and it is drawn in the
small editor-button style (micro type, 7 × 10 px padding), so it looks smaller and less important than the
*Browse…* beside the box and the *Create project* button in the next card. Two rules combine:
`.path-row input{margin:0}` removes the input's usual 16 px bottom margin, and the button sits in a
`.button-row`, whose `.btn` is sized for the workspace editor (`.button-row .btn{padding:7px 10px;font-size:var(--type-micro)}`,
`mosaic/ui/mosaic.css:4`). Markup: `mosaic/ui/index.html:12`.

**Expected:** the same spacing as the *Start a project* card (field, gap, primary button), with *Open project →*
the card's primary action at the size of *Create project*.

**Notes for the fix:**

- Give `.path-row` the bottom margin its input lost (e.g. `.path-row{margin:7px 0 16px}`), which also fixes the
  other path rows (panel folder, export output) if they show the same crowding.
- Make the start-page button row use full-size buttons, e.g. scope the compact rule to the editor
  (`.editor .button-row .btn`) or give `#open-project` the primary class like *Create project*.
- The *Recent projects* list in the same screenshot still shows the stray project inside the repository
  (Bug 27's leftover, `/Volumes/Astro/LunarMosaic/'/Users/…/session 04102026-stacked'`).

### Bug 41 — Levels never shows its histogram (nor its "set from image" buttons)

**Where:** the Levels block in the chain editor. Screenshot `histogram never comes.png`.

**Actual:** the graph shows only the transfer line, and the hint keeps saying "The histogram appears once the
statistics are ready." It never does. The *Set black from* / *Set white from* buttons added for Bug 24 never
appear either.

**Cause:** Levels' prepare step does compute them — `histogram`, `sky_level`, `shadow_level`,
`highlight_level` (`_levels_prepare`, `mosaic/blocks.py:245`) — and they are stored in `stats.blocks[id]`
(`mosaic/chain.py:479`). But `/api/analysis` forwards only whitelisted keys per block type, and the whitelist
has `curves=('histogram',)` and no `levels` entry at all (`App.analysis`, `mosaic/app.py:962`). So the editor's
`read` for Levels never carries the histogram (`levelsEditor`, `mosaic/ui/chains.js:236`) nor the levels
(`chains.js:499`–`503`).

**Expected:** the histogram of the image arriving at the block appears once statistics finish, with the
set-from-image buttons beneath black and white.

**Notes for the fix:**

- Add `levels=('histogram', 'sky_level', 'shadow_level', 'highlight_level')` to the `keys` dict in
  `App.analysis`.
- Add a regression check that `/api/analysis` returns a histogram for a Levels block; the Bug 24 fix seems to
  have been tested against the block function, not through the API.
- The hint's "once the statistics are ready" never says if they failed or never come; worth distinguishing
  "computing" from "unavailable".

### Bug 42 — export canvas "Moon centred" centres the mosaic's bounding box, not the lunar disk

**Where:** the export dialog, *Canvas* 16:9 landscape · Moon centred, *Moon position* Centre. Screenshot
`not really centered (centered would be full moon disk in center).png`.

**Actual:** for a crescent, the lit crescent sits right of centre and the full disk's centre is well to the
right of the frame's centre. `Scene.canvas_geometry` (`mosaic/lrgb.py:455`) pads the export view's bounding
box: `left = round(spare * {'left': .1, 'centre': .5, 'right': .9}[position])`, `top = (oh-h)//2`
(`lrgb.py:486`–`487`). The bounding box is the mosaic, which for a crescent covers only the lit part of the
disk, so "centre" centres the crescent's box, and the canvas size (`units = max(ceil(w/rw), ceil(h/rh))`) is
sized to that box too.

**Expected:** "Moon centred" puts the centre of the whole lunar disk (the fitted limb circle) at the centre of
the canvas, as if the Moon were full; the dark limb side gets the same room as the lit side.

**Notes for the fix:**

- A limb fit already exists: `fit_limb(gray, tol, arc=True)` in `moon_atlas/atlas_geo.py:400` (RANSAC circle
  through the sunlit limb, terminator-proof, works on a partial arc). Run it once on the luminance proxy,
  store the disk centre and radius on the scene, and map the centre through the export matrix.
- Size the canvas so the whole disk circle (plus the existing 10 % breathing room) fits, then place the disk
  centre at the canvas centre; the image may then need padding on one side only, and if the disk would leave
  the frame the canvas grows rather than cropping.
- Decide what *Left* / *Right* mean with a disk centre: probably the disk centre at a third / two thirds of the
  width, rather than the 10 %/90 % slack split.
- Fall back to today's bounding-box behaviour, with a note in the dialog, when the limb fit fails (e.g. a
  mosaic of the terminator only).
- The dialog's preview and size readout use the same `canvas_geometry`, so they follow automatically.

### Bug 43 — export refuses to replace an existing file, with no way to say yes

**Where:** the export dialog, *Export image*, when the output file is already there (e.g. exporting again after
a change). Reported with the same export-dialog screenshot as Bug 42.

**Actual:** "The export file already exists. Choose another name." (`Scene.export`, `mosaic/lrgb.py`). Export
deliberately never replaced a file (`os.link` publication), so re-exporting meant renaming every time.

**Expected:** replace the file after an explicit yes.

### Fix notes — 2026-10-11

Fixed in place on `impl/phase-8`, not pushed.

- **40.** `.path-row` now carries the 7/16 px margins its input gives up, so every path box has the usual gap
  below it. The start page's *Open project →* is out of the compact editor `.button-row` (new `.open-actions`)
  and is a primary button, the same size as *Create project*. The stray in-repo project in *Recent projects*
  is still there (Bug 27); it is data, not code.
- **41.** `App.analysis` forwards `levels=('histogram', 'sky_level', 'shadow_level', 'highlight_level')`, so
  the Levels graph gets its histogram and the Bug 24 set-from-image buttons finally appear. New check
  `test_levels_readouts_reach_the_editor` goes through `analysis()`, not the block function.
- **42.** `Scene.disk()` runs LunarAtlas's `fit_limb(arc=True)` once on a 1800 px preview and keeps the disk
  (centre, radius in canvas px) when the limb arc spans ≥ 90°, ≥ 20 % of the lit outline lies on the circle,
  and the radius is ≤ the mosaic's longer side. (The plain-texture test fixture yields a sparse 4 % "circle":
  without the share test it inflated a square export to 866 px.) For a ratio canvas, `canvas_geometry` maps the
  disk centre through the export matrix and sizes the frame so the whole disk and every mosaic pixel fit, with
  the disk centre at ½ (Centre) or ⅓ / ⅔ (now labelled *Left third* / *Right third*) of the width and ½ of the
  height. Without a fit it falls back to the old box placement, and the dialog summary says so
  (`moon_placed` in `/api/export-preview`). On Moon_04102026 the fit takes 0.2 s and puts the disk centre at
  0.500 × 0.500 of a 10,752 × 6,048 frame. New check `test_export_canvas_centres_the_whole_disk` (a cropped
  synthetic crescent).
- **43.** `Scene.export(…, overwrite=False)`: an existing file raises `ExportExists` (a `ValueError`), which
  `/api/export` checks before starting the job and reports with `exists: true`. The dialog then shows a
  *"<name> already exists. Replace it."* checkbox; ticking it and exporting again sends `overwrite: true`.
  Editing the path hides the row again. The replacement is written to the temp file first and swapped in with
  `os.replace`, so a failed or cancelled export leaves the old file intact. A folder as output is refused.
  New check `test_export_replaces_a_file_only_when_asked`. The macOS *Browse…* save chooser asks its own
  Replace question, so a browsed-to existing file is confirmed twice.

## Phase 0 triage — 2026-10-05

Checked the committed baseline `3c3fba3` on `impl/phase-0`. This section supersedes the
historical statuses below; the original findings are retained for context. The baseline already
contains most of the reported fixes. Subsequent Phase 0 repairs are recorded below.

| Bug | Current status | Evidence (baseline unless noted) |
|---|---|---|
| 1 | Fixed | `mosaic/tests/review_regressions.py:239` exposes 13 unittest cases; the script runner is guarded at line 255. Discovery runs all 37 checks successfully. |
| 2 | Fixed | `mosaic/mosaic_stages.py:660` uses `col` in `photometry()`; the unused `idx` assignment is gone. |
| 3 | Dead code fixed; allocation claim unsupported | `fs_prev` is gone. At `mosaic/mosaic_builder.py:1221`, each fit scale is bounded by its preview area's width and height; the maximum selects one of those fitted sizes. `canvas_preview()` allocates that fitted size plus two pixels per dimension. A larger scale for a smaller source does not imply an unbounded output allocation. |
| 4 | Fixed | The discarded `after8` resize is gone from the stage-8 movie block in `mosaic/mosaic_builder.py`. |
| 5 | Fixed before Phase 0; outside modification scope | `mosaic/experiments/registration/harness.py:2` imports `os` once. Its current import block passes pyflakes. |
| 6 | Reported dead code fixed; numerical result unverified | `mosaic/experiments/registration/resid_structure.py:10` no longer defines the unused locals. It crops the inputs and translates returned coordinates, with a comment explaining the internal margins. Numerical validity of the held-out residuals was not revalidated; this experiment remains outside Phase 0 modification scope. |
| 7 | Fixed | `mosaic/lunar_finish.py:418` uses a plain literal for the layer-gain prefix; pyflakes passes. The historical hypothetical guard divergence is not a reproduced crash. |
| 8 | Fixed | `mosaic/panel_classifier.py:261` uses a plain literal for the tier-count prefix; pyflakes passes. |
| 9 | Fixed before Phase 0; outside modification scope | The three unused imports are absent from `moon_atlas/atlas_closeup.py:27` and `moon_atlas/atlas_view.py:33`; pyflakes passes. |
| 10 | Reported import-time execution fixed; outside modification scope | Importing `resid_structure`, `mesh_round2`, `mesh_proto`, `noise_floor`, `seam_eval`, `seams`, and `subpix` succeeds with `harness.capture` patched to fail if called. Their capture/processing entry points are guarded. This does not validate their real-data output. |
| 11 | Fixed in Phase 0; JavaScript regression checks pass | The baseline restored single failed edits but lost earlier values when another control changed during a save. `currentEdits()` now includes the submitted snapshot for ordinary controls and wavelet layers. Seven new regression checks pass; browser verification remains pending. |
| 12 | Fixed | `mosaic/lrgb.py:20` defines the JPEG limits, `Scene.export()` enforces them, `mosaic/app.py:437` publishes them, and `mosaic/ui/mosaic.js:235` consumes them. The existing export-preview workflow test checks the response against the constants. |
| 13 | Fixed | `mosaic/mosaic_common.py:85` returns for `total <= 0`. Runtime probes of `tick(0, 0)` and `tick(0, -1)` both emit no output. |

`moon_atlas/` and `mosaic/experiments/` were inspected only. Their files were not changed by
this triage, as required by IMPLEMENTATION_PLAN.md Phase 0, step 2.

### Bug 11: autosave race reproduced at baseline and repaired in Phase 0

Baseline locations: `mosaic/ui/mosaic.js:145` (`changeEdit`) and `:150–153` (`flushEdits`).

1. Begin with server edits `{saturation: 1, smoothing: 0}`.
2. Change saturation to `2` and start the save, keeping its response pending.
3. While that request is in flight, change smoothing to `3`.
4. Reject the first save (for example, a revision conflict), then let the state refresh return
   the unchanged server edits.
5. Inspect the pending edit that will be retried.

Observed by executing the actual `changeEdit` and `flushEdits` function bodies in a Node VM,
with a deferred API promise and stubbed rendering/timers:

```text
Failed save alone:                 pending {saturation: 2, smoothing: 0}, saveFailed true
Failed save with newer smoothing:  pending {saturation: 1, smoothing: 3}, saveFailed true
Expected after both adjustments:   pending {saturation: 2, smoothing: 3}
```

Root cause: `flushEdits()` clears `pendingEdits` while keeping the submitted snapshot only in
its local `edits`. A concurrent `changeEdit()` therefore starts a new full snapshot from the
old `state.project.edits`. The catch handler overlays that full snapshot onto the failed one,
so unchanged stale fields win alongside the genuinely newer field. The next retry can save the
old saturation value even though the user never reverted it.

Correction implemented: keep the submitted snapshot in `savingEdits` until the request and its
preview refresh finish. `currentEdits()` returns pending edits, then the submitted snapshot,
then the server state. Both ordinary controls and individual wavelet layers use it. Newer
values for the same field still win, including an intentional return to the original value.
The snapshot is cleared in `finally`, so subsequent edits can use refreshed server values.

The checks in `mosaic/tests/ui_autosave.py` run the complete shipped JavaScript in Node's VM
through its input handlers, rendering, and fetch parsing. DOM elements, timers, and server
responses are simulated. Coverage includes failed and successful saves with a second field,
same-field changes and reversions, disconnection during state refresh, two wavelet layers,
retry payloads, saved indicators, and a later edit after refreshing server state.
Four of the seven checks failed before the repair; all seven pass after it. This is JavaScript
behavior verification, not a browser journey. Automatic retry after a failed save is unchanged:
the UI retains the edits and offers retry by another adjustment or the save shortcut.

### Verification for this triage

- `.venv/bin/python -m unittest discover -s mosaic/tests -p '*.py' -v`: **37 passed** in 4.362 s.
  The missing-file fixture emits an OpenCV warning; the HTTP error paths also emit resource
  warnings. Neither causes a test failure.
- Pyflakes passes for all 11 Python files directly implicated by the original findings.
- Targeted probes verified empty-job progress and guarded experiment imports. The initial
  JavaScript probe reproduced the autosave race that the original Python suites missed.
- After the repair, `.venv/bin/python mosaic/tests/ui_autosave.py`: **7 passed**. This suite
  requires Node.js on PATH, with no npm packages, and participates in Python unittest discovery.
- Full discovery after the repair: **44 passed** in 4.657 s. JavaScript syntax checks and
  pyflakes for the new Python suite also pass.
- Synthetic generators subsequently added with nine fixture checks; full discovery now passes
  **53 tests** in 4.536 s. See DESIGN.md §9 for Phase 0 progress.
- Phase 0 remains incomplete: add golden fixtures, builder determinism evidence, and the
  combined test runner. *(Completed — see the next section.)*

### Phase 0 completion

Golden fixtures (`golden.py`), the builder-determinism record (`determinism.py`) and the combined
runner (`run_all.py`) were added; DESIGN.md §9 has the details. One further repair:

**Mesh refinement crashed the build when no dense tile survived** (`mosaic/mosaic_stages.py`,
`refine_mesh`). On small or low-texture panels every pair's tile list is empty, so the normal
equations were assembled with `np.vstack([])` and the whole build died with
`ValueError: need at least one array to concatenate` at stage 4. Found by building synthetic panels.
The mesh only refines `fine_align`'s poly3 poses, so `refine_mesh` now returns those poses with
`{"skipped": …}` and logs why; `mosaic_builder.py` also catches the mesh's own `RuntimeError`
("dense tiles left panels without a constraint") and keeps the fine-alignment poses with a warning
instead of a traceback. Regression `mesh_no_tiles_fallback` in `review_regressions.py`; it was run
against a copy of the code with the guard removed and fails there with the original `ValueError`.

Verification: full discovery **60 passed** in 23 s; `run_all.py` 6 of 6 suites; pyflakes clean on
`mosaic/` apart from the deliberate `layer_noise` re-export in `sharpen.py` (now commented).

## Historical review

Full review of the repository at commit `0151e68` (working tree included).

Method: full read of every Python module, a `pyflakes` static pass, byte-compilation of the
whole tree, execution of both test suites, targeted runtime probes of the numeric helpers,
and a consistency check of `tool_settings.py --example` against the committed `.env.example`.

Every bug below was confirmed by reading the code and, where behaviour could be observed
without a gigapixel lunar dataset, by running it.

---

---

## Bug 1 — `review_regressions.py` calls `sys.exit()` at import time, breaking test discovery

**Severity: High** (blocks the whole regression suite from running under any test runner)

### Short Description
`mosaic/tests/review_regressions.py` executes its checks and then calls `sys.exit()` as soon as
the module is *imported*, so `unittest discover` records a spurious failure for the whole run.

### Long Description
The file is written as a standalone script: its regression functions are invoked in a
`for fn in (...)` loop at module top level (line 231) and the module finishes with
`sys.exit(1 if FAILED else 0)` (line 236). There is no `if __name__ == '__main__':` guard.

This makes the file poisonous to any tool that imports it, which is exactly what test
discovery does. `unittest discover -s mosaic/tests -p '*.py'` imports every module matching
the pattern; importing `review_regressions` raises `SystemExit`, which unittest records as a
`_FailedTest` error for that module.

The failure is worse than a single failing test, because the output contradicts itself:

```
Ran 25 tests in 4.216s

FAILED (errors=1)

ALL OK
```

unittest reports a failed run, while the last line of stdout is the script's own `ALL OK`.
The 13 real regression checks (`bridge_drop`, `fine_all_fallback`, `photometry_no_overlap`,
`mesh_model_round_trip`, `sky_pedestal_neutralised`, …) do run and print `ok`, but they run
as an *import side effect*, not as tests. Their pass/fail status never reaches the test runner:
a failing check only appends to the script's local `FAILED` list and sets its own exit code,
which unittest discards.

The practical consequence: if any of those 13 checks regresses in the future, CI stays green
on the substance while the suite still reports `FAILED (errors=1)` for an unrelated reason —
and nobody can tell which is which.

### Steps To Reproduce
1. `cd /Volumes/Astro/LunarMosaic`
2. `.venv/bin/python -m unittest discover -s mosaic/tests -p '*.py' -v`
3. Observe `Ran 25 tests`, then `FAILED (errors=1)`
4. Observe the traceback ending in `File ".../review_regressions.py", line 236, in <module> / sys.exit(1 if FAILED else 0)`
5. Observe the final stdout line is `ALL OK`

### Actual Result
The run reports `FAILED (errors=1)` with an `ImportError`/`SystemExit` for
`review_regressions`, and the regression checks report success through stdout instead of
through the test protocol.

### Expected Result
The regression checks should be real `unittest` test cases, or the file should guard its
script behaviour:

```python
if __name__ == '__main__':
    for fn in (bridge_drop, fine_all_fallback, ...):
        test(fn)
    print(f"\n{'ALL OK' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)
```

Then `unittest discover` imports the module cleanly, and a genuine failure surfaces as a
genuine failing test instead of an import error masked by an `ALL OK` line.

---

---

## Bug 2 — `mosaic_stages.py` `photometry()` builds an index map it never uses

**Severity: Medium** (dead code that hides a real omission)

### Short Description
In `mosaic/mosaic_stages.py`, `photometry()` computes `idx = {n: k for k, n in enumerate(nodes)}`
and never reads it. The panel-index map the function actually uses (`col`) is built from a
*different* variable, so the dead `idx` is a leftover from a refactor.

### Long Description
`photometry()` needs to map panel ids to column positions in its normal-equation matrix. It
does that correctly at lines 681–682:

```python
free = [n for n in nodes if n != ref]
col = {n: j for j, n in enumerate(free)}
```

Two lines earlier it also builds:

```python
idx = {n: k for k, n in enumerate(nodes)}
```

Nothing between the definition (line 680) and the end of the function (line 726) reads `idx`.
`pyflakes` reports it as `local variable 'idx' is assigned to but never used`.

This is not merely cosmetic. It shows two competing id→position mappings existed at some point
and only one (`col`) survived. Any future reader who trusts `idx` — because it is defined and
named exactly like the `idx` used by `mosa_builder.solve_global()` at line 428 — will silently
compute wrong columns. `col` skips the reference panel and is offset from 0; `idx` includes the
reference and counts every node. They disagree for every panel except by coincidence.

### Steps To Reproduce
1. Open `mosaic/mosaic_stages.py` and go to line 680
2. Search the remainder of `photometry()` (lines 680–726) for `idx`
3. Observe there are no reads
4. Or run `.venv/bin/python -m pyflakes mosaic/mosaic_stages.py` and read the warning

### Actual Result
`mosaic/mosaic_stages.py:680:5: local variable 'idx' is assigned to but never used`

### Expected Result
The line should be removed, so there is exactly one id→position mapping in the function and
the next reader cannot reach for the wrong one.

---

---

## Bug 3 — `mosaic_builder.py` computes `fs_prev` and never uses it, leaving the preview scale unbounded

**Severity: Medium** (dead code; the scale clamp it looks like it belongs to is missing)

### Short Description
At `mosaic/mosaic_builder.py:1216`, `fs_prev` is computed as a preview fit scale and then
never referenced. The variable name and placement suggest it was meant to clamp the
per-iteration fit scale computed at line 1223.

### Long Description
Inside `run_stages_4_to_8()`, the fine-alignment preview callback computes a fit scale twice,
from two different sources:

```python
1216:    fs_prev = max(min(aw / 15000, ah / 13600) for aw, ah in (st.area for st in pv.styles()))
...
1223:        fs = max(min(aw / cv_[0], ah / cv_[1]) for aw, ah in (st_.area for st_ in pv.styles()))
```

`fs_prev` hard-codes a `15000 × 13600` reference canvas and is never read again. `fs` at
line 1223 is derived from the *actual* warped canvas extent `cv_`.

The dead variable matters because `on_iter` (lines 1218–1233) drives both the live preview
window and the recorded movie frames — `fs` is what scales the composite and the outlines.
Because `fs_prev` is inert, nothing bounds `fs` at all: if a run produces a canvas much smaller
than `15000 × 13600` (a handful of panels, or a tightly cropped set), `cv_[0]` and `cv_[1]` are
small, so `fs` becomes large, and `canvas_preview()` is asked to allocate at that large scale.
A small canvas can therefore drive the preview — and every recorded movie format — to allocate
far more than the 1920×1080 output can ever show.

### Steps To Reproduce
1. Open `mosaic/mosaic_builder.py` at line 1216
2. Search lines 1216–1636 for `fs_prev`
3. Observe there are no reads
4. Or run `.venv/bin/python -m pyflakes mosaic/mosaic_builder.py`

### Actual Result
`mosaic/mosaic_builder.py:1216:5: local variable 'fs_prev' is assigned to but never used`

### Expected Result
Either remove the line, or — if it was the intended clamp — actually apply it, e.g.
`fs = min(fs, fs_prev)`, so recorded movie frames are not computed at an unbounded scale.

---

---

## Bug 4 — `mosaic_builder.py` resizes the full mosaic into `after8` and never uses it

**Severity: Medium** (wasted full-resolution work on the critical path)

### Short Description
At `mosaic/mosaic_builder.py:1490`, `after8` is computed as a full-canvas resize of the mosaic
and then never used. The stage-8 movie block goes on to use `before8` and `hero8` instead.

### Long Description
At the end of stage 8, inside the `if pv.active():` block:

```python
1490:        after8 = cv2.resize(out, (int(canvas[0] * fs), int(canvas[1] * fs)), interpolation=cv2.INTER_AREA)
```

`out` is the full-resolution mosaic and `fs` is the unbounded fit scale described in Bug 3.
This is a full-canvas `INTER_AREA` resample performed per recorded movie format, and the
result is discarded.

The stage-8 sequence that follows actually uses:
- `before8` with `wpc` for the "before" card (line 1494)
- `hero8 = tone_img(hero, wp_disp)` for the finished result (line 1497)

so the resize is pure waste. On a 200 Mpx canvas this is a multi-hundred-megabyte allocation
plus a full `INTER_AREA` resample, on the critical path, for nothing. It is also the widest
allocation in the stage-8 block, so on a memory-constrained run it can be the difference between
finishing and being killed.

### Steps To Reproduce
1. Open `mosaic/mosaic_builder.py` at line 1490
2. Search the remainder of the stage-8 block for `after8`
3. Observe there are no reads
4. Or run `.venv/bin/python -m pyflakes mosaic/mosaic_builder.py`

### Actual Result
`mosaic/mosaic_builder.py:1490:9: local variable 'after8' is assigned to but never used`

### Expected Result
The line should be removed, saving a full-resolution resize per recorded movie format.

---

---

## Bug 5 — `registration/harness.py` imports `os` twice on one line

**Severity: Low** (harmless today, but a real defect in an experiment that patches builder internals)

### Short Description
`mosaic/experiments/registration/harness.py` line 2 reads `import os, sys, os, types` — `os` is
imported twice. `pyflakes` reports `redefinition of unused 'os' from line 2`.

### Long Description
Line 2 is:

```python
import os, sys, os, types
```

Python silently accepts this and binds `os` once, so nothing breaks at import time. It is
flagged because this module is the most invasive file in the repository: `harness.capture()`
builds a fake `args` namespace, monkey-patches `ms.fine_align` with a function that raises to
intercept the builder mid-pipeline (lines 19–30), and is imported by `resid_structure.py` and
`noise_floor.py` to drive the real alignment code against captured stage 0–3 state.

A duplicated import in the one module whose whole purpose is to substitute other modules'
internals is a warning sign about how much of this code was written without linting. It is
also a hint of line-merging by an automated tool, which is exactly the class of edit that can
drop or duplicate a *statement* rather than just a name. The rest of this file bears that out:
it declares four unused imports on lines 4–5 (`numpy as np`, `cv2`, `mc`, `mb`) which are
never referenced, so the module's import block is not a reliable description of its
dependencies.

### Steps To Reproduce
1. Open `mosaic/experiments/registration/harness.py` at line 2
2. Observe the text `import os, sys, os, types`
3. Or run `.venv/bin/python -m pyflakes mosaic/experiments/registration/harness.py`

### Actual Result
```
mosaic/experiments/registration/harness.py:2:1: redefinition of unused 'os' from line 2
mosaic/experiments/registration/harness.py:4:1: 'numpy as np' imported but unused
mosaic/experiments/registration/harness.py:4:1: 'cv2' imported but unused
```

### Expected Result
`import os, sys, types` — one import per name, and the unused names removed.

---

---

## Bug 6 — `resid_structure.py` keeps dead locals that make the "shifted grid" measurement wrong

**Severity: Medium** (the measurement it computes is not the measurement its comments claim)

### Short Description
In `mosaic/experiments/registration/resid_structure.py`, `measure()` computes four locals that
are never read — including `orig`, the saved original of `ms.tiles_for_pair` — and a fifth,
`shifted`, that is defined but shadowed immediately by an identical lambda passed inline. The
grid-shift logic it is built around is therefore not the logic that runs.

### Long Description
`measure()` is supposed to evaluate fine-alignment residuals on tiles the builder did *not*
train on, by shifting the tile grid off the builder's own grid. Lines 12–24:

```python
12:    orig = ms.tiles_for_pair                      # never read; no restore either
...
16:        wa, ha = C.sizes[a]; half, margin = tile // 2, tile // 2 + 48; step = int(tile * 0.75)
17:        off = int(step * offset_frac)
...
20:        shifted = lambda pts: mp(pts + off)      # defined, never used
21:        out = ms.tiles_for_pair(a, b, lambda p: mp(p + off), {a: (wa - off, ha - off), b: C.sizes[b]}, ...)
```

Three separate problems:

1. **`orig` is captured but never restored.** `ms.tiles_for_pair` is not monkey-patched here,
   so nothing leaks — but saving a "before" value and never using it is the exact shape of a
   patch/restore pair that was half-written.

2. **`shifted` is dead.** Line 20 defines `lambda pts: mp(pts + off)`; line 21 passes a
   *textually identical* inline lambda instead. One of the two is a leftover.

3. **`half` and `margin` are dead**, and they are the ones that matter. `half = tile // 2` and
   `margin = tile // 2 + 48` are the intended tile half-width and edge margin. They are
   computed and then never used: the tile geometry handed to `tiles_for_pair` is instead
   `{a: (wa - off, ha - off), b: C.sizes[b]}` — a *size* override, with no `margin` inset and
   no `half`-derived centre. The caller's own comment on line 18 says "same selection as
   `tiles_for_pair`, on a shifted grid", but the margin that `tiles_for_pair` applies
   internally is not reproduced here.

The consequence is that the reported "held-out" residuals (`RMS ... px, median ...`, line 41)
are measured over a region that is *not* held out in the way the script claims. Half-`tile`
margins at every boundary are exactly where a wrong pose shows up most, and those are the
samples being dropped. The `pickle.dump` on line 56 persists these residuals as `resid.pkl`
for later analysis.

### Steps To Reproduce
1. Open `mosaic/experiments/registration/resid_structure.py` at line 16
2. Observe `half, margin = tile // 2, tile // 2 + 48` and search lines 17–24 for either name
3. Observe neither is referenced again
4. Or run `.venv/bin/python -m pyflakes mosaic/experiments/registration/resid_structure.py`

### Actual Result
```
mosaic/experiments/registration/resid_structure.py:12:5: local variable 'orig' is assigned to but never used
mosaic/experiments/registration/resid_structure.py:16:30: local variable 'half' is assigned to but never used
mosaic/experiments/registration/resid_structure.py:16:36: local variable 'margin' is assigned to but never used
mosaic/experiments/registration/resid_structure.py:20:9: local variable 'shifted' is assigned to but never used
```

### Expected Result
Either the half-tile margin is applied to the tile geometry (and `orig`/`shifted` are
removed), or — if the builder's internal margin already covers it — the misleading comment and
the dead `half`/`margin` should go, so the reported held-out numbers can be trusted.

---

---

## Bug 7 — `lunar_finish.py` builds the wavelet report with an f-string that has no placeholders

**Severity: Low** (cosmetic, but it sits next to a real crash risk)

### Short Description
At `mosaic/lunar_finish.py:418`, the string `f"): layer gains "` is marked as an f-string but
contains no replacement fields. `pyflakes` reports `f-string is missing placeholders`.

### Long Description
The wavelet summary line is assembled by concatenating literals and joins:

```python
416:        print(f"      wavelets ({args.wavelet_kernel}, {len(args.wavelets)} layers, biases " + ", ".join(...) +
417:              (", noise protection " + ", ".join(...) if any(args.wavelet_denoise) else "") +
418:              f"): layer gains " + ", ".join(f"{wavelet_gain(v):.3g}" for v in args.wavelets) +
419:              f" · fine-detail amplitude × {wave_gain:.2f} (centre crop) · layer noise " +
420:              ", ".join(f"{v * 100:.3f}" for v in ctx["wnoise"]) + " % of full scale")
```

Line 418's leading fragment is a plain string written as an f-string. The `f` prefix is
vestigial.

It is worth flagging because of what it sits next to: line 419 interpolates `wave_gain`, which
is initialised to `None` at line 362 and only assigned at line 367 — inside
`if args.wavelets:`. The print block at line 415 is guarded by the *same* condition, so today
the two agree. But the guard is duplicated rather than shared, so any future edit that widens
one condition and not the other makes line 419 raise
`TypeError: unsupported format string passed to NoneType.__format__` after a multi-minute
finish run has already completed its processing.

### Steps To Reproduce
1. Open `mosaic/lunar_finish.py` at line 418
2. Observe the `f` prefix on `f"): layer gains "` with no `{...}`
3. Or run `.venv/bin/python -m pyflakes mosaic/lunar_finish.py`

### Actual Result
`mosaic/lunar_finish.py:418:15: f-string is missing placeholders`

### Expected Result
`"): layer gains "` — drop the `f` prefix, and derive line 419's `wave_gain` from the same
guard that opens the block (or default it to 1.0 as line 483 already does with
`info.get('wave_gain') or 1`).

---

---

## Bug 8 — `panel_classifier.py` writes the tier-count summary with an f-string that has no placeholders

**Severity: Low** (cosmetic, same class as Bug 7)

### Short Description
At `mosaic/panel_classifier.py:261`, the string `f"- Tier counts: "` is marked as an f-string
but contains no replacement fields; the counts are appended by a `join` on the same line.

### Long Description

```python
260:    tiers = Counter(r["tier"] for r in ok if r["tier"])
261:    L.append(f"- Tier counts: " + ", ".join(f"{t}: {tiers.get(t, 0)}" for t in ("Excellent", "Good", "Marginal", "Poor")))
```

The `f` prefix on the first fragment is vestigial — the actual formatting happens in the
generator expression on the same line.

This is reported separately from Bug 7 because it lands in a *different* artefact: this one is
written into the Markdown report that `panel_classifier.py` emits for the user, alongside the
measured `Relative detail range …`, `Within-image variation: …` and `Agreement of
single-image metrics …` lines that the tier counts are meant to sit under. A report that is
partly machine-formatted and partly hand-concatenated is easy to get subtly wrong, and the
vestigial `f` prefix is the visible symptom of that.

### Steps To Reproduce
1. Open `mosaic/panel_classifier.py` at line 261
2. Observe the `f` prefix on `f"- Tier counts: "` with no `{...}`
3. Or run `.venv/bin/python -m pyflakes mosaic/panel_classifier.py`

### Actual Result
`mosaic/panel_classifier.py:261:14: f-string is missing placeholders`

### Expected Result
`"- Tier counts: "` — drop the `f` prefix.

---

---

## Bug 9 — `atlas_closeup.py` and `atlas_view.py` import names they never use

**Severity: Low** (dead imports; they inflate the public surface these modules appear to offer)

### Short Description
`moon_atlas/atlas_closeup.py` imports `fit_correction` and `sky_to_latlon` from `atlas_geo`,
and `moon_atlas/atlas_view.py` imports `load_geo`, but none of the three is referenced anywhere
in its own module. `pyflakes` reports all three as imported-but-unused.

### Long Description
`atlas_closeup.py`, lines 27–28:

```python
from atlas_geo import (DATA, R_MOON, Geometry, Reference, download, fit_correction, fit_global, load_gray, match_patches,
                       resize, sky_to_latlon, unit)
```

Each of `fit_correction` and `sky_to_latlon` occurs exactly once in the file — on the import
line itself. `atlas_view.py`, line 33:

```python
from atlas_geo import (DATA, PPD, R_MOON, Reference, image_signature, load_geo, resolve_sidecar, sidecar_path,
                       sky_to_latlon, unit, write_json_atomic)
```

`load_geo` likewise occurs exactly once, on the import line.

This matters more than ordinary dead imports for two reasons.

First, `moon_atlas/` is explicitly described in the repository README as a vendored copy of
LunarAtlas, with `moon_atlas/UPSTREAM.md` recording the upstream provenance. When that copy is
next re-synced with upstream, a hand-edited import list is a merge conflict waiting to happen,
and a conflict here is easy to resolve the wrong way.

Second, `mosaic/orient.py` monkey-patches this vendored copy at runtime:

```python
37:    atlas_closeup.setups = lambda: []
```

So the `mosaic` application depends on the exact contents of these import blocks. Stale names
in them make the coupling harder to reason about, and make it easy to misread which
`atlas_geo` functions are actually load-bearing for north-up.

### Steps To Reproduce
1. Run `.venv/bin/python -m pyflakes moon_atlas/atlas_closeup.py moon_atlas/atlas_view.py`
2. Observe the three unused-import warnings
3. Or `grep -n "fit_correction" moon_atlas/atlas_closeup.py` and observe a single hit (the import)

### Actual Result
```
moon_atlas/atlas_closeup.py:27:1: 'atlas_geo.fit_correction' imported but unused
moon_atlas/atlas_closeup.py:27:1: 'atlas_geo.sky_to_latlon' imported but unused
moon_atlas/atlas_view.py:33:1: 'atlas_geo.load_geo' imported but unused
```

### Expected Result
Remove `fit_correction` and `sky_to_latlon` from the `atlas_closeup.py` import, and `load_geo`
from the `atlas_view.py` import, so the vendored copy's dependency surface matches what it
actually uses.

---

---

## Bug 10 — `resid_structure.py` and `mesh_round2.py` sit at module top level and run on import

**Severity: Medium** (an experiment that captures real builder state executes merely by being imported)

### Short Description
The scripts under `mosaic/experiments/registration/` have no `main()` guard. Importing one runs
a full builder capture against a real `panels/` directory, so these modules cannot be imported
for inspection, cannot be collected by a test runner, and fail outright when the dataset is
absent.

### Long Description
`mosaic/experiments/registration/resid_structure.py` starts executing at line 4:

```python
4: sys.path.insert(0, '.')
5: from harness import capture
6: import mosaic_stages as ms
7: t0 = time.time(); C = capture(); print(f'captured stages 0-3 in {time.time()-t0:.0f} s', flush=True)
```

`capture()` runs `mb.pipeline(...)` against `panels/` (or `$MOSAIC_PANELS`) and intercepts
`ms.fine_align` mid-run. So merely importing the module to read `measure()` starts a multi-minute
alignment capture, and raises `FileNotFoundError` when `panels/` does not exist — which is the
normal state of a fresh clone, since `panels/` is not committed.

The sibling scripts in the same directory have the same shape. `mesh_round2.py` and
`mesh_proto.py` likewise execute at import, and `noise_floor.py` imports `capture` (reported
as unused by `pyflakes`, but the import itself is what drags the module in).

This is the same root cause as Bug 1 in the shipped test directory, and it is why
`mosaic/experiments/` was untracked in `git status` — the scripts are not importable, so they
cannot be linted, tested, or reused.

### Steps To Reproduce
1. From the repository root, with no `panels/` directory present, run
   `.venv/bin/python -c "import sys; sys.path.insert(0,'mosaic/experiments/registration'); import resid_structure"`
2. Observe the script immediately begins `captured stages 0-3 ...`
3. Observe it then fails on the missing `panels/` directory

### Actual Result
The import has side effects and raises `FileNotFoundError` on a clean checkout; there is no way
to import `measure()` for reuse or testing.

### Expected Result
Guard the entry points:

```python
if __name__ == '__main__':
    t0 = time.time(); C = capture(); ...
```

so the helpers (`measure`, `job`) are importable and the expensive work only runs when the
script is executed directly.

---

---

## Bug 11 — a failed autosave silently discards the user's layer adjustments

**Current status (2026-10-05): fixed in Phase 0, verified by seven JavaScript behavior checks.**
Single-edit recovery existed in `3c3fba3`; the subsequent repair preserves edits made while a
save is in flight. The Phase 0 section above records the evidence and browser-verification
limit. The code excerpt below describes the original implementation.

**Severity: High** (silent data loss in the app's headline "autosave" feature)

### Short Description
In `mosaic/ui/mosaic.js`, `flushEdits()` moves the pending edits out of `pendingEdits` *before*
the POST succeeds. If the request fails they are never put back, so the adjustments are lost and
the sliders visibly snap back to their previous values.

### Long Description
`flushEdits()` (lines 146–155) is the only path that persists the workspace sliders:

```javascript
146: async function flushEdits(){
147:   clearTimeout(saveTimer);
148:   if(saving){while(saving) await new Promise(r=>setTimeout(r,30)); if(pendingEdits) return flushEdits(); return;}
149:   if(!pendingEdits) return;
150:   const edits=pendingEdits; pendingEdits=null; saving=true;
151:   try {state=await api('edits',payload({edits,revision:state.project.revision})); await refreshPreview(false);}
152:   catch(e){state=await api('state'); render(); throw e;}
153:   finally {saving=false; render();}
154:   if(pendingEdits) await flushEdits();
155: }
```

Line 150 clears `pendingEdits` *before* the request is sent — a deliberate choice, so keystrokes
arriving during the in-flight request accumulate into a fresh `pendingEdits` rather than being
lost. But the `catch` on line 152 never restores the edits it just cleared. It re-fetches
`/api/state`, calls `render()`, and rethrows.

The consequence is the interaction between `pendingEdits` and `render()`. Line 139:

```javascript
139:  if(!pendingEdits && !saving) syncEdits(p.edits);
```

By the time `render()` runs from inside the `catch` (line 152) — and again from the `finally`
(line 153) — `pendingEdits` is `null` and `saving` is `false`. So the guard on line 139 passes
and `syncEdits(p.edits)` runs, writing the **server's** values back into every slider. The user's
unsaved adjustments are replaced on screen by the old values, and the only feedback is the toast
from the `.catch` in the `changeEdit` timer.

The conflict path makes this easy to hit. `/api/edits` returns HTTP 409 with `conflict=True`
whenever `o.get('revision') != p.data['revision'` (`mosaic/app.py:571`), and any other
server-side rejection — a range validation failure from `Project.validate_edits`, a `geometry()`
error, or a dropped connection — takes the same path. Every one of them silently discards the
edit.

`render()`'s `#saved` indicator shows `Saving…` while `saving||pendingEdits`, so after the failure
it correctly reads "Saved locally" — because as far as the UI is concerned, the saved state *is*
the server's state. Nothing ever tells the user their change was thrown away.

### Steps To Reproduce
1. Start the app and open a project with both mosaics built and matched
2. Open the **Adjust** page (the workspace) and drag any slider, e.g. Saturation or Smoothing
3. While the 180 ms debounce is pending or the POST is in flight, make the server reject the
   save. The simplest way is to stop the server so the request fails
4. Observe the toast appear, then observe the slider jump back to its previous value
5. Observe `#saved` returns to "Saved locally", not to an error state
6. Observe the previous adjustment value is still in `project.json` — the change is gone

### Actual Result
The adjustment is discarded without warning. The slider snaps back to the old value, the status
indicator reads "Saved locally", and the change is not in the project file.

### Expected Result
`pendingEdits` should be restored on failure so the user's intent survives a transient error and
can be retried, e.g.:

```javascript
catch(e){ pendingEdits = edits; state=await api('state'); render(); throw e; }
```

With that change the `if(!pendingEdits && !saving)` guard on line 139 correctly prevents
`render()` from clobbering the sliders, so the controls keep showing the value the user chose and
a retry can succeed. The status line should also reflect that the value is not yet saved rather
than claiming "Saved locally".

---

---

## Bug 12 — the export dialog's JPEG size guard duplicates the server limit and can drift

**Severity: Low** (two sources of truth for one limit)

### Short Description
`mosaic/ui/mosaic.js` line 234 re-implements the server's JPEG size ceiling as
`info.width*info.height<=80000000 && Math.max(...)<=65000`, duplicating the constants in
`mosaic/lrgb.py` lines 395–396. The two copies can drift, and nothing enforces that they agree.

### Long Description
The server refuses an oversized JPEG in `Scene.export()`:

```python
395:        if suffix in ('.jpg', '.jpeg') and (max(w, h) > 65000 or w*h > 80_000_000):
396:            raise ValueError('This JPEG is too large. Choose a smaller scale or TIFF/PNG.')
```

The client mirrors the same two numbers inline:

```javascript
234:    const jpegOK=format!=='jpg'||info.width*info.height<=80000000&&Math.max(info.width,info.height)<=65000;
```

The client check is a *pre-flight* nicety — it disables the run button and appends "Too large for
JPEG; choose TIFF/PNG or a smaller size." to the summary, which is good UX. The problem is that
the limit now lives in two files with no shared definition, and `mosaic/lrgb.py:395` is the copy
that actually decides.

A related detail sharpens this: the client's numbers come from `info.width`/`info.height`
returned by `/api/export-preview`, which reports `w`/`h` from `scene.geometry(edits, factor)`
(`mosaic/app.py:432`). The server computes `w`/`h` from `self.geometry(edits, factor)` in
`export()` as well, so they agree today. But `geometry()` ends with
`max(1, round(w*factor))` (`mosaic/lrgb.py:301`), so any future change to how the output size is
rounded could let the client enable the button for an export the server then rejects — turning a
clear inline message into a 400 after the click.

### Steps To Reproduce
1. Open `mosaic/ui/mosaic.js` at line 234 and note the literals `80000000` and `65000`
2. Open `mosaic/lrgb.py` at lines 395–396 and note `80_000_000` and `65000`
3. Change either one and observe the other does not follow

### Actual Result
The JPEG ceiling is defined twice; the client copy is not derived from the server copy.

### Expected Result
The server should expose the limit (for example as part of the `/api/export-preview` response,
alongside `width` and `height`) and the client should use it, so there is a single source of truth
for the JPEG ceiling.

---

---

## Bug 13 — `Progress.tick()` clamps `total` for the division but not for the comparison

**Severity: Low** (no crash, but an empty job reports a meaningless progress line)

### Short Description
In `mosaic/mosaic_common.py`, `Progress.tick()` computes `frac = done / max(total, 1)`, which is
safe against a zero total — then uses the *raw* `total` in the suppression test on the next line.
The two disagree, so a zero-`total` call emits a `0/0` progress line instead of being a no-op.

### Long Description

```python
83:    def tick(self, done, total, extra=""):
84:        """Progress line at least every 2 % or 5 s (and at the end)."""
85:        now = time.time()
86:        frac = done / max(total, 1)
87:        if done < total and frac - self._last_frac < 0.02 and now - self._last < 5:
88:            return
```

Line 86 defends the division with `max(total, 1)`. Line 87 then decides whether to *suppress* the
line using the un-clamped `total`.

When `total == 0` and `done == 0`: `frac = 0 / 1 = 0.0`. The `done < total` test is `0 < 0`, which
is `False`, so the early return is skipped and a progress line is emitted. The bar is rendered
from `frac = 0.0` (all dots), while the header text uses the raw values:

```python
93:        self._emit(f"      {done}/{total}  [{bar}]  {fmt_t(el)} · ETA {fmt_t(eta)}" + ...)
```

producing `0/0  [....................]  00:00 · ETA 00:00` — a line reporting progress over an
empty job. The `eta` on line 91 is itself guarded (`if frac > 0 else 0`), so there is no
`ZeroDivisionError`; the defect is that the zero case is defended inconsistently rather than not
at all.

This was confirmed by running it:

```
$ .venv/bin/python -c "import mosaic_common as mc; p=mc.Progress(1,None); p.header('Empty job'); p.tick(0,0)"
[1/1] Empty job
      0/0  [....................]  00:00 · ETA 00:00
```

In practice `tick()` is called from loops over `paths` / `nodes`, and both the builder and the
classifier return early when that list is empty (`mosaic_builder.py:792` and `:869`) — which is
why this has not been seen in normal runs. The defect is that the zero case is guarded on the
wrong side of the division, so the guard does not actually suppress the line it was added for.

### Steps To Reproduce
1. Run `.venv/bin/python -c "import sys; sys.path.insert(0,'mosaic'); sys.path.insert(0,'.'); import mosaic_common as mc; p=mc.Progress(1,None); p.header('Empty job'); p.tick(0,0)"`
2. Observe the emitted line `0/0  [....................]  00:00 · ETA 00:00`

### Actual Result
A `0/0` progress line is printed for an empty job, instead of the call being a no-op.

### Expected Result
Return early when `total <= 0`, since there is no progress to report — or clamp once into a local
`n = max(total, 1)` and use it consistently on both lines.

---

---

## Summary

| # | Area | Severity | Kind |
|---|------|----------|------|
| 1 | `mosaic/tests/review_regressions.py` | High | `sys.exit()` at import breaks test discovery |
| 2 | `mosaic/mosaic_stages.py` `photometry` | Medium | Dead `idx` beside the real map `col` |
| 3 | `mosaic/mosaic_builder.py` stage 4 | Medium | Dead `fs_prev`; preview scale left unbounded |
| 4 | `mosaic/mosaic_builder.py` stage 8 | Medium | Dead `after8`; wasted full-resolution resize |
| 5 | `mosaic/experiments/registration/harness.py` | Low | `import os, sys, os, types` |
| 6 | `mosaic/experiments/registration/resid_structure.py` | Medium | Dead `half`/`margin` make "held-out" measurement wrong |
| 7 | `mosaic/lunar_finish.py` | Low | f-string with no placeholders |
| 8 | `mosaic/panel_classifier.py` | Low | f-string with no placeholders |
| 9 | `moon_atlas/atlas_closeup.py`, `atlas_view.py` | Low | Three unused imports in the vendored copy |
| 10 | `mosaic/experiments/registration/*.py` | Medium | No `__main__` guard; import runs a full builder capture |
| 11 | `mosaic/ui/mosaic.js` `flushEdits` | High | Failed autosave silently discards edits |
| 12 | `mosaic/ui/mosaic.js` line 234 | Low | JPEG limit duplicated from `lrgb.py` |
| 13 | `mosaic/mosaic_common.py` `Progress.tick` | Low | Inconsistent zero-`total` handling |

### Cross-cutting observations

**Bugs 1 and 10 share one root cause.** Scripts that do real work at import time, with no
`if __name__ == '__main__':` guard, cannot be imported — therefore cannot be linted, collected by
a test runner, or reused. In `mosaic/tests/` this actively breaks `unittest discover` (Bug 1); in
`mosaic/experiments/` it makes the modules unimportable and raises `FileNotFoundError` on a clean
checkout (Bug 10). This is also why `mosaic/experiments/` and `moon_atlas/experiments/` appear as
untracked in `git status`: they were never runnable as part of an automated check.

**The dead-variable cluster is concentrated in the alignment experiment code** (Bugs 2, 3, 4, 6).
Each is individually harmless at runtime, but Bug 6 shows why they matter: there, a dead
`margin = tile // 2 + 48` means the "held-out" residual figures the script reports and pickles to
`resid.pkl` are not measured over the region its own comments claim. The same pattern — a variable
computed for a purpose, then bypassed by a second inline copy of the same computation — appears at
`mosaic_builder.py:1223` (Bug 3) and `resid_structure.py:20` (Bug 6).

**No linting is enforced.** Every bug that `pyflakes` can catch on its own (2, 3, 4, 5, 6, 7, 8,
9) is in this report, and each was found by running `pyflakes` once. The repository has a
substantial test suite — 25 tests, plus 13 standalone regression checks — but no CI workflow in
`.github/` (only `FUNDING.yml`) and no lint configuration anywhere in the tree. Adding a
`pyflakes` or `ruff` step, plus an `if __name__ == '__main__':` convention, would have prevented
10 of the 13 findings.

**The two High findings are the ones that cost real work.** Bug 1 means the project's own
regression suite cannot currently be run by a test runner at all. Bug 11 means a user who adjusts
a layer slider and hits a transient server-side rejection — a 409 revision conflict, a validation
failure, a dropped connection — silently loses that adjustment while the status line still reads
"Saved locally".

### What was checked and found sound

- `requirements.txt` matches the imports across the tree (`numpy`, `opencv-python`, `pillow`,
  `tifffile`); `Pillow` is correctly treated as optional in `preview_style.py` and
  `mosaic_common.py`.
- The whole tree byte-compiles cleanly (`.venv/bin/python -m compileall` over `mosaic`,
  `moon_atlas`, `tool_settings.py`, `lunarmosaic.py`).
- `.env.example` is byte-for-byte in sync with `tool_settings.py --example`, so the documented
  ranges and the enforced ranges agree.
- `lrgb.largest_rectangle()` — the largest-rectangle-in-histogram routine behind the
  `coverage: common` crop — was fuzz-tested against a brute-force search over 4000 random masks up
  to 7×7: **0 mismatches**. Its handling of internal holes is correct.
- `sharpness_map()`'s `valid[b:-b, b:-b]` margin can degenerate to an empty mask for very small
  panels at `q=1`, but `q` is hard-coded to 4 at `mosaic_builder.py:1348` and panels are
  full-resolution frames, so this is not reachable. Deliberately not reported as a bug.
- The local HTTP surface in `atlas_view.py` is defensively built: DNS-rebinding guard
  (`this_host`), `Sec-Fetch-Site`/`Origin` checks (`same_site`), a per-run token in an
  HttpOnly/SameSite=Strict cookie plus `X-LA-Token`, and a 0600 handoff file written before its
  rename specifically to close a `/proc` argv leak. `mosaic/app.py` additionally validates every
  `Project.path()` result with `is_relative_to`.
- `Project.save()` and `RecentProjects.write()` both use `mkstemp` + `fsync` + `os.replace`, so a
  crash mid-write cannot truncate an existing project or history file.
- `lunar_finish.unsharp()` correctly implements Photoshop's soft threshold (subtracting the
  threshold rather than gating on it), matching the uncommitted change that introduced it.
- 24 of the 25 tests in `mosaic/tests/lrgb_workflow.py` pass; the 25th is not a real failure but
  the discovery breakage from Bug 1.

---

---
