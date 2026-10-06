# LunarMosaic browser bug audit — 2026-10-06

Scope: the already-running local app at `http://localhost:8768` in Safari, using the existing `04102026` luminance-only project. I opened/closed a preview, navigated the three workflow pages, inspected the export dialog and non-persistent canvas views, then restored the composite view. I did **not** delete, save, import, rebuild, or overwrite project content.

## Findings

| ID | Severity | Status | Summary |
|---|---|---|---|
| CODEX-01 | Critical | Confirmed live | A quoted absolute project path is treated as a relative path and creates the project inside the application checkout. |
| CODEX-02 | High | Source-traced; destructive journey intentionally not run | Loading a chain replaces all processing settings immediately, with no confirmation, backup, diff, or undo. |
| CODEX-03 | Medium | Source-traced from the live controls | The `×` controls delete previews and processing blocks immediately and permanently, with no confirmation or undo. |
| CODEX-04 | Medium | Source-traced recovery defect | A failed colour re-placement leaves the old colour/match active but marks the stream failed, so the workspace can misrepresent stale output as the current placement. |

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
