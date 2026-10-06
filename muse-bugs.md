# Muse bug report — LunarMosaic

Found by playing with the web app running at `http://localhost:8768` on 2026-10-06, repo at
`impl/phase-8` (`cc9f86f`), with the `04102026` luminance-only project open.

**How it was tested.** The page was driven in headless Chrome over the DevTools protocol — clicking the
three workflow pages, view rail, match popover, export dialog, chain editor, preview strip and the
build/cancel flow — while collecting console errors and failed requests, and the same HTTP API the page
uses was probed directly with edge-case requests. Flows that change state ran on a **second instance on
port 8899** holding a copy of the project and later two throwaway projects, so the live project was never
modified: its `revision` was **163 before and after** (14 inputs, 6 previews, 7 blocks, rotation −6.989°
intact). No console errors or uncaught exceptions were seen in any run; the only 4xx responses were the
intentional ones below.

Screenshots: `/tmp/muse-shots/*.png` (temporary; recreate with `/tmp/muse_cdp.js`, probes in
`/tmp/probe.py`, `/tmp/probe2.py`).

## Findings

| ID | Severity | Status | Summary |
|---|---|---|---|
| MUSE-01 | High | Reproduced in the browser, twice | *03 Match & edit* opens a blank page (only the marketing hero) until the luminance mosaic exists |
| MUSE-02 | Medium | Reproduced in the browser, live project | Drag-and-drop onto a panel set ignores the set's lock and still tries to import |
| MUSE-03 | Medium | Reproduced in the browser and via the API | Server errors reach the user as raw Python text (`[Errno 1] Operation not permitted: …`, a bare path) |
| MUSE-04 | Low | Reproduced in the browser | The marketing hero stays above *01 Import* and *02 Build* in every project |
| MUSE-05 | Low | Reproduced in the browser | Overlapping previews stack their canvas labels on top of each other |
| MUSE-06 | Low | Reproduced in the browser | The colour views and *Show the residuals on the mosaic* stay live although no colour is placed |
| MUSE-07 | Low | Reproduced in the browser, polled to a settled state | After Cancel, the job strip keeps the cancelled stage's title and frozen progress |

---

### MUSE-01 — *03 Match & edit* opens a blank page until the luminance is built

**Where:** header nav (`mosaic/ui/index.html:5`), `showVisibility()` (`mosaic/ui/mosaic.js:84`–`91`).

**Steps.** Create or open a project that has no built luminance mosaic (fresh project), or click *03*
while the very first build is still running → click **03 Match & edit**.

**Actual.** The screen contains only the header and the marketing hero ("A LUNARATLAS COMPANION / Every
set. One Moon. …"). Everything else is hidden — measured in the page after the click:
`heroShown: true, welcomeShown: false, importShown: false, buildShown: false, recentShown: false,
workspaceShown: false`, with *03* highlighted as the active step. There is no message, no next step and
no progress indication; the user has to guess that *01* or *02* does something. Seen with a fresh project
(`/tmp/muse-shots/51-blank-edit-page.png`) and again during a first build, where `#empty-canvas` ("Build
your luminance mosaic to open the workspace.") is not shown either
(`/tmp/muse-shots/43-edit-during-build.png`).

**Cause.** `editing = page === 'edit' && built(luminance)` (`mosaic/ui/mosaic.js:85`). When the condition
is false, `#setup` is shown but the per-page rules (`:88`–`:90`) hide `#import-page` (`page !== 'import'`),
`#build-page` (`page !== 'build'`), `#welcome` (project open) and `#recent-projects` — and nothing ever
hides `.hero` (`mosaic/ui/index.html:8`). The nav buttons are only disabled when there is *no project*
(`mosaic/ui/mosaic.js:78`), so *03* stays clickable in exactly the states where it cannot render.

**Expected.** Either disable *03 Match & edit* until a luminance mosaic exists (the header already
disables *Export image* the same way, `mosaic/ui/mosaic.js:323`), or route the click through
`landingPage()` (`mosaic/ui/mosaic.js:93`–`98`) so it lands on *02 Build* with the build state visible.

**Fix notes.** Smallest change: in `showPage()`, send `page = landingPage()` when `next === 'edit'` and
the luminance is not built (plus a toast "Build the luminance mosaic first."). Alternatively disable the
button — `b.disabled = !state.project || (b.dataset.page === 'edit' && !built(state.project, 'luminance'))`
— which also covers the mid-first-build case.

---

### MUSE-02 — drag-and-drop onto a panel set ignores the set's lock

**Where:** the `.drop` label and its handlers (`mosaic/ui/mosaic.js:136`–`159`), the lock
(`mosaic/ui/mosaic.js:197`–`198`), `importFiles()` (`mosaic/ui/mosaic.js:248`–`266`).

**Steps.** On *01 Import* with a set whose stream is already built (the live project's luminance set), or
while a build is running, drag a `.tif` file onto **Choose a folder of panels**.

**Actual.** The drop is accepted and the import runs: `importFiles()` starts, the page POSTs
`/api/upload?…&name=dropped-panel.tif`, the server refuses with 400 and the user gets the toast
**"Choose an unbuilt panel set."** Measured on the live project: `dropLabelDisabled: false` while
`fileInputDisabled: true` and `scanDisabled: true`; inputs stayed at 14 (the server checks before
writing, so nothing was corrupted). The same drop during a running build produces an error toast instead
of the card simply being inert.

**Cause.** The lock disables `input,button` inside the card (`mosaic/ui/mosaic.js:198`) — the drop
*label* is neither, and `drop.ondrop` (`:159`) has no `locked` guard, so the drag path bypasses the lock.
(The file-picker path *is* blocked, because its `<input type=file>` is disabled.)

**Expected.** The drop zone inert while `locked`: no `dragover` highlight, no `importFiles()` call — the
same state the picker and *Use folder* are already in.

**Fix notes.** Guard both handlers (`if (locked) return;`) and re-evaluate them in `renderSets()` (they
are bound once in `setCard()`), or add a `.drop:has(input:disabled)`/locked class for the visual side.

---

### MUSE-03 — server errors reach the user as raw Python text

**Where:** `mosaic/app.py:1026`–`1027` (GET) and `:1156`–`1157` (POST) —
`except (ValueError, OSError, TypeError, KeyError, cv2.error) as e: h.json(dict(error=str(e)), 400)`;
surfaced by `action()` / `importFiles()` → `message(e.message)` (`mosaic/ui/mosaic.js:51`–`53`, `:264`).

**Reproduced in the UI:**

| Where | What the user sees |
|---|---|
| Start screen → *Create project* with folder `/System/muse-x` | toast: `[Errno 1] Operation not permitted: '/System/muse-x'` (screenshot `53-bad-create.png`) |
| *01 Import* → *Use folder* with `/no/such/panels/folder` | toast: `/no/such/panels/folder` — the path alone, no verb and no reason (screenshot `35-bad-folder-toast.png`) |

**Same handler, seen at the API** (identical strings the page would show): `/api/new` with a path under a
read-only root → `[Errno 30] Read-only file system: '/nonexistent-xyz'`; a request without `folder` or
`paths` → `'folder'` / `'paths'`; `/api/export-preview?…&factor=abc` → `could not convert string to
float: 'abc'`; a job started without `output` fails with the job error `'output'` (shown in
`#status-job`).

**Expected.** Sentence-style messages — "That folder can't be created: /System/muse-x (not permitted).",
"No readable panels found in …", "The export needs an output file." — with the raw exception left for
the job log or console.

**Fix notes.** Map `OSError`/`KeyError` to text at the two handlers (errno → phrase table, `KeyError` →
"missing <field>"), and keep `str(e)` for `ValueError`, which the codebase already raises with written
messages ("Choose full or common coverage.").

---

### MUSE-04 — the marketing hero stays above *01 Import* and *02 Build*

**Where:** `.hero` (`mosaic/ui/index.html:8`); `showVisibility()` hides `#welcome` for an open project
(`mosaic/ui/mosaic.js:88`) but never `.hero`.

**Steps.** Open any project → *01 Import* or *02 Build*.

**Actual.** Both pages begin with "A LUNARATLAS COMPANION / Every set. One Moon. / Keep the detail of
your sharpest panels … / Native mosaics · panel-by-panel colour · non-destructive chains" (~340 px at a
1440-wide window — screenshots `02-import.png`, `03-build.png`). On a 900 × 813 window the panel sets and
the build cards start below the fold, so the first screenful of the working page is marketing for the app
the user is already inside. The welcome cards are hidden in the same situation, which shows the hero was
meant to belong to the landing state.

**Expected.** The hero only on the landing/projects page, i.e. hidden together with `#welcome` in
`showVisibility()`, so an open project starts at *YOUR PANEL SETS* / *BUILD AND PLACE*.

---

### MUSE-05 — overlapping previews stack their canvas labels

**Where:** `Previews.place()` appends one `<span>` per rectangle (`mosaic/ui/previews.js:94`–`103`);
the label is anchored above the rectangle's top-left corner (`mosaic/ui/mosaic.css:105`:
`.preview-rect span{position:absolute;bottom:100%;left:-1px;…}`).

**Steps.** Use previews that sit close together (the `04102026` project already has P01/P02 and
P04/P05/P06 overlapping) → look at the canvas on *03 Match & edit*.

**Actual.** Every label is drawn at its own rectangle's corner with no collision handling, so labels
cover each other and the rectangles under them: "P01 · Pre…" is half-hidden behind "P02 · Preview 2",
and "P04 · …", "P06 · Preview 6", "P05 · Preview 5" pile into an unreadable stack (screenshots
`00-initial.png`, `04-edit.png`). Only one or two of six previews can be identified on the canvas.

**Expected.** Labels that stay legible: offset colliding labels vertically, show the full label only for
the selected/hovered preview (the strip already names all of them), or collapse to the id ("P04") when
the full name does not fit.

---

### MUSE-06 — colour views and *Show the residuals* stay live with no colour placed

**Where:** view rail (`mosaic/ui/index.html:44` — `colour`, `overlay`, `coverage`, `errors`);
`#show-error-map` (`mosaic/ui/mosaic.js:561`); the popover sentence (`mosaic/ui/mosaic.js:374`).

**Steps.** Use a luminance-only project (no colour set, as the open `04102026`) → *03 Match & edit* →
either click the **C**, **◎**, **▦** or **⋮** view buttons, or open the *Colour not placed* popover and
click **Show the residuals on the mosaic**.

**Actual.** All of it works: the C/overlay/coverage views switch (the status bar reads "… · colour /
overlay / coverage"), and the residuals view renders a dim image although nothing was ever placed —
every area is "unknown". The popover is aware of the situation ("Build both streams to measure how the
colour sits on the luminance.") yet its **Show the residuals on the mosaic** button is enabled and jumps
straight to that dim view. Nothing tells the user the image has no data behind it (besides the orange
*Colour not placed* chip in the footer).

**Related.** This is the view-rail/popover part of `BUGS.md` **Bug 25** (a grey project still shows
colour controls); filed separately because here the app actively *renders* a meaningless residuals image
rather than merely showing an extra control.

**Expected.** Disable `#show-error-map` and the colour-only view buttons until `state.project.match`
exists — the server-side equivalent is already done for *Check overlapping areas* (`#check-match` is
disabled via `mosaic/ui/mosaic.js:364`) — and use the popover sentence as their tooltip.

---

### MUSE-07 — after Cancel, the job strip keeps the cancelled stage's title and progress

**Where:** `renderBuild()` (`mosaic/ui/mosaic.js:305`–`310`); cancel wiring (`:554`, `:308`).

**Steps.** *01 Import* → *Build mosaics* → press **Cancel** while the build runs → stay on *02 Build*.

**Actual.** The recovery banner does the right thing ("Luminance: interrupted during stage 6. Completed
mosaics are kept.") and the cancel button disappears, but the job strip above it settles into a stale
state and stays there (polled for 15 s after the server reported `job.state: cancelled`):

```text
#job-title     "Choosing the sharpest regions"   ← the stage it was cancelled during
#job-progress  0.6666…                           ← frozen mid-bar, looks like it is still running
#job-result    ""                                ← empty, no "cancelled" wording
```

`#job-title` only special-cases `job.state === 'idle'` (`mosaic/ui/mosaic.js:305`); `cancelled` falls
through to `job.title`, and `job.result`/`job.error` are empty for a cancellation. Screenshot
`54-cancel-settled.png`.

**Expected.** "Build cancelled · stopped during stage 6 of 9" (or progress reset/marked), so the strip
matches the recovery banner instead of looking like a job still at 67 %.

---

## Checked and working (deliberately not filed as bugs)

- **No console errors, uncaught exceptions or unexpected network failures in any browser run** — the
  only 4xx seen were the intentional refusals above (plus the browser's own `/favicon.ico` probe, while
  `/favicon.svg` serves 200).
- **The page does scroll.** `html` is `overflow:hidden` (theme), `body` is the scroller, so
  `window.scrollTo()` is inert while wheel/trackpad scrolling works — verified before filing anything.
- **Chain editor:** add block (refusals greyed with their reason, e.g. "White balance changes colour…"),
  duplicate, delete, expand, parameter edit → autosave → "Saved locally"; a stale revision gets the
  documented 409 ("…kept here; it is sent again on top of the newer project.").
- **Previews:** `P` enters drawing mode, `Esc` cancels, drag creates a preview, thumbnail click opens
  the 1:1 viewer with the correct title and native size, delete works (6 → 7 → 6 in the test run).
- **Build:** stage list, log and progress update live, inputs lock while running, Cancel works, the
  recovery banner + *Restart luminance* appear, and a rebuild after cancel completed normally
  (3,864 × 5,920, export enabled).
- **Export:** dialog preview, size/coverage/composition summary, format switch rewrites the extension
  (`…Moon-LRGB.tif` → `…Moon-LRGB.jpg`), and a run wrote a correct 4556 × 6348 16-bit TIFF.
- **Validation messages are good in most places:** "Choose full or common coverage.", "A preview is
  x, y, w and h in view pixels.", "This set is already built. Create a new project to replace its
  panels.", "Build this layer first.", "Unknown view.", "Invalid viewport." (NaN, 0 and missing
  parameters all refused).
- **HTTP surface:** Host/Origin/token guards, static files restricted to `UI_FILES`, `DELETE`/`PUT` →
  405, unknown routes → 404; uploads are refused before anything is written.

## Relation to the other bug lists

Distinct from `BUGS.md` bugs 14–34 (checked individually: path chooser, spinner sizes, text sizes,
preview recalculation, viewer zoom/move, sliders, decimal comma, export naming and dialog, selection
tint, app icon) and from `bugs-codex.md` CODEX-01…04 (quoted path, chain import without undo, delete
without undo, stale colour after a failed re-placement). The only overlap is MUSE-06 with `BUGS.md`
Bug 25, noted in place.
