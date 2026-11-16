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
