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

LunarMosaic runs entirely from its own files. Upstream changes are intentionally
reviewed and copied into a new LunarMosaic revision, rather than downloaded as
executable code while a user runs a mosaic. Reference maps, elevation, and the
IAU catalogue are downloaded as data on first use; `LUNARATLAS_DATA` can point
to an existing cache.
