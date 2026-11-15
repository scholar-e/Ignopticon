# Size-calibrated worst case (entry 31)
Run order (all local, ~1-2 min each): `uv run --script scripts/worstcase_calibrate.py classes|calibrate|heldout|export`.
Outputs: classes.json (frozen before recall), calibration.{json,md}, heldout.{csv,md}, export.out. DONE markers: *_DONE.
Class bounds 10 and 100 km2 (1,000 / 10,000 ha) by fire size at issue (FEDS perimeter | toa==0); calibration n = 7 / 6 / 11.
Export re-wrote only the 73 *-upper.json files and index.json; lower files untouched. Intermediate rasters: data/bounds/runs/*/upper_v2.
Technical page (earth/public/technical/index.html) still describes the old analog-q90 worst case: not touched (outside scope).
