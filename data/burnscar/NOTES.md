# Burn-scar fuel correction notes (2026-11-15)
Code done: elmfire_case.py BURNSCAR_FILE/YEARS/MULT (tests/test_burnscar.py OK, front tests OK), scripts/burnscar.py,
rasters built (data/burnscar/rasters, rasters.json). Harm/raw/viirs-only rasters are IDENTICAL for N<=10 at 2026 and
N<=5 at 2020/21 (VIIRS from 2012 for all arms); differ only for N>=15 (616 harm-vs-raw cells at N=15, 2026).
Fine 375 m variant not built: data/raw/viirs_fire only covers 2026 days 211-218.
Flow: dev (DONE marker data/burnscar/DEV_DONE) -> `uv run scripts/burnscar.py select` (freezes manifest.json)
-> `JOBS=8 nohup uv run scripts/burnscar.py heldout` (HELDOUT_DONE) -> `uv run scripts/burnscar.py score`
-> MODELLING_LOG entry (re-read for next number), commit explicit paths, push.
Commit: scripts/elmfire_case.py scripts/burnscar.py scripts/edge_heldout_table.py tests/test_burnscar.py
data/burnscar/{NOTES.md,manifest.json,rasters.json,dev_status.json,heldout_status.json,heldout/{scores.json,comparison.md,heldout_table.md,heldout_table.json}} MODELLING_LOG.md
