# Head-to-head vs PyreCast, issue 2026-08-13T12:00 (quarter run)

Prepared 2026-11-15 ~18:30. Frozen before any scoring and before our runs exist: `manifest_20260813.json` (rule inside), made by
`scripts/h2h_manifest.py 2026-08-13T12:00` (log `manifest.log`). Nothing is tuned on results.

## Counts (each filter)
- FEDS fires known at issue (snapshots <= 13-1200; the 08-12 00Z and 08-13 00Z FEDS snapshots do not exist, API 500): 169
- WFIGS: 74 uncontained+matched, 17 contained, 78 unmatched (same rule as the multiday manifest)
- PyreCast fires with a complete run (5 pct) with run_dt_utc in [issue-18 h, issue]: 43 fires (71 runs in window; latest per fire used)
- FEDS fire (uncontained+matched) overlapping one of them (a p50 arrival cell, ToA>0, within 1 km of the FEDS polygon): **28 fires**, 28 distinct slugs
  (mt-bobcat-lakes and mt-sand-creek overlap each other's FEDS fire; each FEDS fire took the slug with most overlap cells).
- Lag of the run before issue: 0.65 h to 15.5 h. Caveat: the archive first saw many of these runs at 16:42-21:00Z on 08-13 (`first_seen_archive`), i.e. after the
  issue; run_dt is the model start time, so they are scored as if available at issue (as asked).

## Truth, definitions (see docstring of scripts/h2h_score.py)
Truth = FEDS(T+12h) from `earth/public/data/fires/feds/2026/08/14-0000.json` (large-fire collection fallback, fires > ~5 km2: same for both models).
Growth = FEDS(T+12h) & ~start & burnable on our 37.5 m grid. PyreCast ToA tifs are hours since run_dt (UTM 30 m, nodata 0), predicted by T+12h =
toa_h <= (T+12h - run_dt). Their run may start up to 15.5 h before T; cells burned in their forecast before T but outside FEDS(T) count as predicted growth.

## Files
- `tasks_20260813.txt` (28 lines, kind new), `catalog_20260813.csv`, `manifest_20260813.json`, `pyrecast/{slug}/{run_ts}/{10..90}.tif` (downloaded, `dl_pyre.log`, `PYRE.DONE`)
- `hyak/h2h.slurm` (control `adj1` only, MEMBERS=adj1; TASKS/CATALOG passed as env; 8 CPUs, 30 min), `scripts/multiday.py` got env TASKS / CATALOG / MEMBERS
- `data/perimeter_batch/gridmet_fm100_20260812.tif` made (gridMET nc has it).
- FEDS snapshots written: 06-0000 .. 11-1200, 12-1200, 13-1200, 14-0000 (local copies).
- Local test: `localtest.log` / `LOCALTEST.DONE`, fire map20260813-66244 (task line 14).

## Commands for the main session
Sync (repo root):
```
R=/gpfs/projects/stf/elinzi/ignopticon; H=elinzi@tillicum.hyak.uw.edu; SSH="ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes"
rsync -aR -e "$SSH" scripts/ hyak/h2h.slurm data/h2h/tasks_20260813.txt data/h2h/catalog_20260813.csv data/h2h/manifest_20260813.json \
  data/perimeter_batch/gridmet_fm100_20260812.tif earth/public/data/fires/feds/2026/08/ $H:$R/
$SSH $H "mkdir -p $R/hyak/logs $R/data/raw"
```
Submit (28 tasks, 16 at a time; resubmit is safe):
```
$SSH $H "cd $R && sbatch --array=0-27%16 hyak/h2h.slurm"
$SSH $H "squeue -u elinzi; ls $R/data/multiday/status/20260813/ | wc -l"
```
Failed ones: `jq -r 'select(.status!="complete")|.fire' $R/data/multiday/status/20260813/*.json`.
Collect (outputs of the adj1 control + landscape/fire-observed/case per fire):
```
bash data/h2h/collect.sh
```
Score (once, after collect; ~1-3 min):
```
uv run --script scripts/h2h_score.py 2026-08-13T12:00      # writes data/h2h/results_20260813.{md,json}
```
After scoring add a dated MODELLING_LOG.md entry (not done: no results yet).

## Local test (pipeline only, not results)
- map20260813-66244 (2 km2): failed in the task, "no associated VIIRS hot cells in 6 h before issue" (same rule as multiday; such fires will fail on Hyak too).
- map20260813-69004, map20260813-65080: complete locally, 405 s / 429 s with 8 workers (whole task from nothing, incl. ~6.5 min build), adj1 only.
  Local outputs are in data/perimeter_batch/map20260813-{69004,65080}/ and data/training/fires/ (the Hyak collect overwrites them).
- h2h_score.py ran end to end on 69004 (scored) and 65080 (excluded: "no truth at T+12h", no FEDS perimeter at 14-0000 in its fire-observed.json).
  Fires failing either way are reported as excluded in results (failure of our run is counted against us in the manifest count, not hidden).
- Expected Hyak cost: ~7 min per task, 28 tasks -> ~3.3 GPU-h; wall ~15 min with %16 (landscape download dominates).

## Issues 2026-08-12T12 and 2026-08-14T12 (same frozen pipeline, no method change)
Manifests (frozen by h2h_manifest.py before any run): 08-12: 168 FEDS known, 75 uncontained+matched, 25 PyreCast fires with a run in window, **21 matched**.
08-14: 174 known, 74 uncontained+matched, 35 PyreCast fires, **28 matched**. Combined `tasks_1214.txt` (49 lines: 0-20 Aug 12, 21-48 Aug 14), `catalog_1214.csv`,
`hyak/h2h1214.slurm`. gridMET 20260811 and 20260813 made. FEDS 12-0000/13-0000 snapshots were failing (HTTP 500 for limit=10000 on those 00Z snapshots);
fixed in scripts/feds_to_earth_json.py by paging 100 at a time; now present with 14-1200, 15-0000, 15-1200. (Aug 13 manifest was frozen before 12-0000/13-0000 existed; not regenerated.)
FileNotFoundError on 63962/61855 (Aug 13): failed after 11 s, before HRRR finished, no inputs missing (catalog rows fine). Most likely both landed on different nodes
at the same moment and the HRRR prefetch's flock (not enforced across nodes on GPFS) let two downloaders race on the temp file. Not an input/catalog bug: just resubmit
(`--array=` their line numbers; the cache is warm now). Same risk for 1214: resubmit failures after the first wave.
Scoring: `uv run --script scripts/h2h_score.py 2026-08-12T12:00 2026-08-13T12:00 2026-08-14T12:00` -> results_DAY.* each + results_pooled_20260812_20260813_20260814.{md,json}
(fire-days pooled, bootstrap over fire-days).
