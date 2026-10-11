# LunarMosaic browser bug audit — 2026-10-06

Scope: the already-running local app at `http://localhost:8768` in Safari, using the existing `04102026` luminance-only project. I opened/closed a preview, navigated the three workflow pages, inspected the export dialog and non-persistent canvas views, then restored the composite view. I did **not** delete, save, import, rebuild, or overwrite project content.

## Findings

| ID | Severity | Status | Summary |
|---|---|---|---|
| CODEX-01 | Critical | **Fixed 2026-10-07** | A quoted absolute project path is treated as a relative path and creates the project inside the application checkout. |
| CODEX-02 | High | **Fixed 2026-10-07** | Loading a chain replaces all processing settings immediately, with no confirmation, backup, diff, or undo. |
| CODEX-03 | Medium | **Fixed 2026-10-07** | The `×` controls delete previews and processing blocks immediately and permanently, with no confirmation or undo. |
| CODEX-04 | Medium | **Fixed 2026-10-07** | A failed colour re-placement leaves the old colour/match active but marks the stream failed, so the workspace can misrepresent stale output as the current placement. |

**Fixed 2026-10-07** (`impl/phase-8`, not pushed — see BUGS.md's own "Fix notes" section for the fuller
write-up these four share with its bugs 14–34):

- **CODEX-01.** A pasted quoted path, or a relative or empty one, is now refused at the HTTP boundary
  (`mosaic/mosaic_common.py`'s `require_absolute_path`), before the project folder is touched. The CLI, which
  legitimately resolves relative paths against the shell's own working directory, is untouched — the check
  sits only in `app.py`'s `/api/new` and `/api/open` handling, not in `Application.select()` itself. **The
  project this finding describes is still on disk inside the checkout** and was left in place rather than
  moved without being asked.
- **CODEX-02.** Loading a chain file now applies at once (no blocking confirm — the import control sits
  inside the export dialog, which was already open, and a native `confirm()` would have stacked unseen
  behind it, and would have frozen the whole page on any import), but a toast names how many blocks were
  replaced and carries its own Undo button for about 8 seconds, the same pattern CODEX-03 uses for a deleted
  block. No on-disk backup file is written; the undo is in memory in the page.
- **CODEX-03.** Deleting a block or a preview now offers the same kind of Undo toast. A restored preview
  gets a new id from the server (it is re-added, not revived), so anything that referred to the old id by
  name elsewhere would not follow it — nothing in this app does.
- **CODEX-04.** The layers panel and the match chip now show "⚠ the last placement attempt failed; showing
  the previous result" when this happens, and export requires an explicit checkbox before it will send that
  stale colour result out. The server-side data (`mosaic`, `report`, `placement`, `match`) is left exactly as
  this finding described — untouched on failure — since showing it as "what you've got, clearly labelled
  stale" seemed more useful than discarding it.

### CODEX-01 — quoted absolute path silently creates a large project tree in the app repository

**Live evidence.** The open project's location and the export dialog both displayed:

```text
/Volumes/Astro/LunarMosaic/'/Users/kristofvandebeek/Desktop/session 04102026-stacked'
```

The checkout now has an untracked directory at that literal path, containing `project.json`, 14 copied luminance panels, derived mosaics and an export. It is not the intended Desktop location. This can pollute the source checkout and makes accidental `git add -A` capable of staging astronomical source data.

**Cause.** `Application.select()` resolves whatever string it receives (`mosaic/app.py:129-156`). A leading quote makes `Path("'/Users/…'")` relative, so `resolve()` uses the server's working directory and the create flow then calls `mkdir(parents=True)`. `Project.create()` repeats the same unvalidated conversion (`mosaic/project.py:216-231`). Empty input also resolves to the working directory before an attempted open takes a `project.json` lock.

**Reproduction.** Create a project with the folder text `'/Users/name/Desktop/My Moon.lunarmosaic'` (including the matching quotes), as commonly pasted by shell-oriented tools.

**Expected.** The app either strips one matching pair of surrounding quotes and creates `/Users/name/Desktop/My Moon.lunarmosaic`, or rejects the value before writing anything. Relative and empty project paths should be refused.

**Minimal fix.** Centralise path normalisation for new/open/import/export paths: trim whitespace, strip one matching quote pair, expand `~`, then require an absolute non-empty path. Validate before acquiring/creating the lock and before `mkdir`; remove any lock made when opening fails.

### CODEX-02 — loading a chain irreversibly replaces the current processing recipe

**Impact.** A user can select a valid but unintended chain file and lose all current luminance, colour, merge and final-chain settings in one click. The active project has multiple configured luminance blocks, so this is not theoretical data-free UI state.

**Evidence.** The live Export dialog states that importing “replaces this project's chain”. The handler parses the selected file and immediately calls `mutate('chain-file', {chain: doc})` (`mosaic/ui/mosaic.js:607-615`). On the server, `import_chain()` only gives imported blocks new IDs (`mosaic/app.py:827-835`), then `Project.set_chain()` overwrites `self.data['chain']` (`mosaic/project.py:387-396`); the request handler persists it straight away (`mosaic/app.py:1119-1139`). No confirmation, backup, diff, revision history or undo route exists.

**Expected.** Import should first show a summary/diff of the affected chains and offer Cancel. Before replacing settings, retain an automatic timestamped chain snapshot; an Undo action should restore it after import.

**Minimal fix.** Export the current `chain_doc()` into a per-project backup location before applying the imported document, retain at least the last few snapshots, and add a confirmation dialog with counts/types of blocks that will be replaced. A short-lived Undo toast can use the snapshot immediately.

### CODEX-03 — delete buttons remove previews and blocks with no recovery path

**Impact.** A misclick on the compact `×` beside a preview or a processing block immediately persists the loss. Preview positions are manual inspection work; a block deletion can discard tuned parameters that are expensive to reconstruct.

**Evidence.** The live workspace displays six `Delete Preview` controls (P01, P02, P04–P07) directly beside their thumbnails and each chain block has a neighbouring `Delete` `×`. Preview deletion sends the API request directly (`mosaic/ui/previews.js:134-137`). Block deletion directly splices the block from the chain (`mosaic/ui/chains.js:379-385`) and is autosaved by the shared chain saver. Neither control asks for confirmation or provides Undo; `Project.preview_op()` removes the preview from the persisted document (`mosaic/app.py:795-825`).

**Expected.** At minimum, an Undo toast that restores the exact preview/block and its original index; confirmation is appropriate for a full chain replacement or destructive multi-item action.

**Minimal fix.** Hold the deleted object and insertion position client-side until the undo window expires; only then let the durable state become the sole copy. Alternatively persist a small per-project edit history. Keep keyboard focus on the restored/deleted item's neighbour for accessibility.

### CODEX-04 — failed colour re-placement can leave stale colour output presented as placed

**Impact.** When a previously placed RGB set is re-placed and the new attempt fails or is cancelled, the old `colour_canvas.tif`, placement report and match remain in the project. The stream is marked `failed`/`interrupted`, but the editor derives its “Placed” state from the retained mosaic/match rather than a successful current state. A user can keep editing or exporting colour they reasonably believe came from the latest placement attempt.

**Evidence.** Re-placement sets only `state='building'` and clears only the error (`mosaic/app.py:650-659`). On failure it records `state='failed'` or `interrupted` but does not clear or mark stale `mosaic`, `report`, `placement` or the project's previous match (`mosaic/app.py:701-708`). In the workspace, `renderWorkspace()` reports RGB as placed whenever `cs.mosaic` and `p.match` remain (`mosaic/ui/mosaic.js:340-374`); it does not surface `cs.error`. The Build page shows the failure, but the user can remain in the editor.

**Expected.** The old result may be retained safely, but it must be explicitly labelled “previous placement — latest attempt failed/cancelled” and export should require an acknowledgement or successful re-placement.

**Minimal fix.** Preserve the previous result under explicit `last_successful_*` fields and clear the active match before an attempt. On failure, either restore the stream to `built` with a visible stale-result warning or retain `failed` while making `renderWorkspace()` show the error and disable/export-label the stale colour result.

## Sound areas observed

- The existing project opened and rendered normally; the composite, luminance, colour and overlay views responded without a crash.
- Opening and closing a 1:1 preview worked, and the export dialog correctly refused to enable Export until its preview was ready.
- The server prevents overwriting an existing export file (`mosaic/lrgb.py:604-610`), but that safeguard does not address project-path parsing or destructive editor actions above.

## Code inspection — 2026-10-07

This pass was read-only: the following are newly recorded findings and were deliberately **not** fixed.

| ID | Severity | Status | Summary |
|---|---|---|---|
| CODEX-05 | Critical | **Fixed 2026-10-07** | Colour re-placement now publishes a generation-specific canvas/report pair through one atomic project save. |
| CODEX-06 | High | **Fixed 2026-10-07** | Upload bodies now stream to a private temporary file without holding the application-wide lock. |
| CODEX-07 | Low | **Fixed 2026-10-07** | Superseded preview responses immediately revoke their unreferenced blob URLs. |

### CODEX-05 — colour canvas and report are not published atomically

**Evidence (confirmed by code path).** `Application.place_colour()` checks only that the partial TIFF exists, then replaces `derived/colour_canvas.tif` first and `derived/colour_place.json` second ([app.py](mosaic/app.py#L774-L779)). If the report is absent, its replace fails, its JSON is malformed, or the process fails before `Project.save()`, the exception handler marks the colour stream failed but removes only the remaining partial files ([app.py](mosaic/app.py#L788-L794)). The previous `colour_canvas.tif` has already been overwritten while the stored match/report still describe the old placement.

**Impact.** The stale-result warning introduced for CODEX-04 can now label a mixture of old metadata and new pixels as the previous result; previews and exports may therefore use a canvas that does not match its placement diagnostics.

**Minimal fix.** Validate both temporary artifacts and parse the report before publishing either. Publish a generation-specific canvas/report pair, then atomically switch a single project pointer to that pair (or retain the old pair until both replacements and the project save have succeeded).

**Fixed 2026-10-07.** `place_colour()` validates the partial TIFF and JSON report, derives the placement summary, and then moves both into UUID-generation filenames. The project document atomically switches to the complete pair only after both moves succeed. Any failure removes only the unpublished generation and restores the prior canvas/report references; the regression test simulates a report-publication failure.

### CODEX-06 — upload I/O monopolises the application lock

**Evidence (confirmed by code path).** The upload handler enters `with self.lock` before reading the HTTP body ([app.py](mosaic/app.py#L1155-L1173)) and holds it through the entire loop that reads up to 8 GiB ([app.py](mosaic/app.py#L1174-L1185)). `status()`, cancellation, render-status updates, project changes, and job progress all use that same lock.

**Impact.** A slow or stalled multi-gigabyte upload makes the live UI appear frozen and prevents cancelling a currently running job until the client finishes or disconnects.

**Minimal fix.** Validate and reserve the destination under the lock, release it while streaming to a uniquely named temporary file, then reacquire it to re-check the project/set state and register the completed upload. Remove the temporary file if the project changed meanwhile.

**Fixed 2026-10-07.** The handler performs its project/set validation under the lock, streams into a dot-prefixed temporary file outside it, then reacquires the lock to validate the still-open project and register the final file. A regression test holds the body reader and proves the application lock remains obtainable while the upload is waiting.

### CODEX-07 — superseded preview fetches retain unreferenced blob URLs

**Evidence (confirmed by code path).** Switching a viewer mode starts a new request and overwrites `this.images[id]` ([previews.js](mosaic/ui/previews.js#L169-L192)). If the first fetch subsequently resolves, it creates an object URL, then returns at the stale-entry guard ([previews.js](mosaic/ui/previews.js#L194-L201)) without storing or revoking that URL. `forget()` cannot revoke it because it was never added to `shown`.

**Impact.** Repeatedly toggling modes on large 1:1 previews consumes browser memory for the remainder of the tab.

**Minimal fix.** Revoke `url` before the stale-entry return, or key pending requests by `(preview id, mode, stamp)` and cancel/revoke superseded entries explicitly.

**Fixed 2026-10-07.** `Previews.load()` revokes the newly created object URL whenever its request has been superseded before the URL enters `shown`.
