# LunarAtlas runtime source

This directory vendors the runtime from [LunarAtlas](https://github.com/kvandebeek/lunaratlas),
`main` commit `7f18388081ff65f55c739aab8b85feeae2046cf6` (2026-10-02),
verified against the remote on 2026-10-03. Its runtime, settings and default-settings
files are unchanged from `435103078d7f76bacad35b7b642888c9f97552ce`, the initial copy.
The three newer commits change only wiki/release documentation and the upstream
release workflow; they require no runtime replacement here.

The top-level Python modules, `viewer/` assets, `fonts/`, and
`data/quality_thresholds.json` came from upstream `lunaratlas/` unchanged.
`tool_settings.py` and `.env.example` in this repository came from the same
upstream commit; they also declare the mosaic tools' settings. The wavelet
preset description was corrected locally to match `lunar_finish.py`. This repository's
`moon_atlas.py` is a compatibility entry point for the previous filename.
The previous local `moon_atlas/` experiments and design mockups are historical.

Local changes to the runtime, and where upstream stands on them:

- `atlas_ephem.py` (2026-10-05): a mosaic's capture time from its layout now reads each panel's time from the
  panel's name alone (`_name_time`). Upstream looked a panel name without a time up as a file, relative to the
  working folder; when that folder held the mosaic's own layout, `capture_time` recursed until Python's limit
  (RecursionError), for example `lunar_finish.py` run from the session folder on panels named without SharpCap
  times. `capture_time` itself is unchanged. Regression check: `capture_time_from_a_layout_ends` in
  `mosaic/tests/review_regressions.py`. The same fix is committed in the LunarAtlas checkout on the local branch
  `fix-capture-time-recursion` (`ae53f42`, with a test in `test_functional_geometry.CaptureTime`), not yet merged
  or pushed; once upstream has it, the next vendored copy carries it and this note can go.

LunarMosaic runs entirely from its own files. Upstream changes are intentionally
reviewed and copied into a new LunarMosaic revision, rather than downloaded as
executable code while a user runs a mosaic. Reference maps, elevation, and the
IAU catalogue are downloaded as data on first use; `LUNARATLAS_DATA` can point
to an existing cache.
