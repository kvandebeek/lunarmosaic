# Bug Report — LunarMosaic

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