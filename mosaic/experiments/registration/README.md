# Registration experiments (2026-09-27): dense tiles + per-panel mesh

Integrated on 2026-09-28 as `--model poly3+mesh` (the default; `refine_mesh` in `mosaic_stages.py`). These are the prototypes. Measured on the 25 Sep set (101 panels). Run from this folder; outputs land here.

| Script | What it does |
|---|---|
| `harness.py` | Runs builder stages 0–3 in-process and captures what `fine_align` receives (≈ 50 s, ~3 GB RAM). |
| `subpix.py` | Sub-pixel accuracy of the tile shift estimators on real texture with known shifts: `phaseCorrelate` 0.10 px RMS, 3× iterated 0.05 px. |
| `resid_structure.py` | Held-out tile residuals of the builder's poly3 fit and their spatial correlation (correlated < 250 px, none > 500 px). |
| `seams.py LAYOUT.json` | Misregistration measured at the real seams of a finished build (region map switches), iterated 128 px tiles. Writes `<stem>_seams.json`. |
| `mesh_proto.py` | Dense 128 px tiles (≈ 109000), bilinear displacement mesh per panel on top of poly3, NumPy conjugate-gradient solver (no scipy). Writes `mesh_inputs.pkl`. |
| `mesh_round2.py` | Weighted fit (response⁴), strong/medium held-out tiles, poly3-dense control vs mesh. Needs `mesh_inputs.pkl`. |
| `seam_eval.py` | Same variants scored on the seam points from `seams.py`. |
| `noise_floor.py` | Per-tile measurement noise from neighbouring tiles (≤ 0.45 px at response ≥ 0.7, ≈ 1.6 px at 0.3–0.5). |

Results (seam points with response ≥ 0.5, n = 3516):

| Variant | median | > 1 px | > 2 px |
|---|---|---|---|
| builder poly3 (12 tiles of 256 px per pair) | 0.42 px | 12.3 % | 1.7 % |
| poly3 refitted on dense weighted 128 px tiles | 0.35 px | 8.4 % | 0.7 % |
| dense poly3 + 256 px mesh (smoothness 1) | 0.27 px | 6.5 % | 0.4 % |

Checkerboard held-out, strong tiles: RMS 0.60 → 0.54 → 0.49 px, > 1 px 7.0 % → 4.6 % → 3.2 %.
Mesh displacement RMS 0.32 px, max 3.4 px. Extra runtime ≈ 40–60 s per build.
Visually subtle: the worst current seams (3–4 px measured) showed no visible kink at 100 %.
