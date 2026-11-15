# Multi-day verification (A) and worst-case ensembles (B), one Hyak array

Driver `scripts/multiday.py` (docstring has the details), array file `hyak/multiday.slurm`. Prepared 2026-11-15; nothing has run on Hyak yet.

**Manifest frozen before any run** (`data/multiday/manifest.json`, rule in it): FEDS fires known at issue (latest geometry in
`earth/public/data/fires/feds/2026/08/` snapshots up to the issue), matched to WFIGS incidents with `scripts/containment_aug2.py`,
kept if uncontained at issue and incident-matched. 2026-08-03T12: 54 fires (of 113 known; 4 contained, 55 unmatched), 08-04T12: 54
(of 117; 6 contained), 08-05T12: 59 (of 124; 7 contained). Per-issue containment: `containment_DAY.json`. A 2025 big day is
skipped: the FEDS snapshots, containment match and `fire_observations.py` snapshot path exist only for Aug 2026 here.
B also covers the 73 Aug 2 fires with a forecast (all, incl. contained/unmatched; the score restricts to uncontained).
`tasks.txt`: 240 lines, 0-53 Aug 3, 54-107 Aug 4, 108-166 Aug 5, 167-239 Aug 2 (kind `aug2`, 54 have inputs locally that are sent; 19 build on Hyak).

**Member set** `PERIMETER_MEMBERS=worstcase` (`scripts/perimeter_batch.py _worstcase`, 11 members): `adj1` control (Aug 2 physics exactly: FEDS start, HRRR 12Z
sub-hourly wind, EMC m1 / lagged m10 / previous-day gridMET fm100, ADJ 1, LH 90, 12 h), wind direction -20/0/+20 deg (new `WDROT` key in
`elmfire_case.py`) x wind speed x0.75/1/1.25 (0/x1 is the control, dropped), dead moisture -2/+2 points (`MOIST`, floored at 1 %) on the control.
Members are written to `data/perimeter_batch/FIRE/runs/physics3/NAME/`, which `growth_bounds.py members()` already unions. Per-issue HRRR 12Z
(hourly f00-f24, sub-hourly f00-f12) goes to a flock'd shared cache `data/raw/hrrr_hourly`, `data/raw/hrrr_subh`. gridMET fm100 of the previous day
(`data/perimeter_batch/gridmet_fm100_2026080{2,3,4}.tif`, made by `scripts/gridmet_fm100_tif.py`; 0801 reproduced to 2e-6) is sent. Landscape (LANDFIRE/3DEP) and VIIRS (LAADS)
are built inside the task for new fires (needs the Earthdata token on Tillicum, see below).

**Local test** (Aug 3 fire map20260803-5450, 8 workers, whole task from nothing): 308 s = HRRR 44 s, build (landscape download dominates, ~3-4 min) +
weather 256 s, 11 members 8 s (81 burned cells start, tiny fire). Wind rotation and moisture change the result (28 grown cells control; 19 at moist +2, 58 at moist -2).
Earlier local builds: landscape median 3 min, mean 3.2, max 28 min (6 at once).

**Cost** ($0.90 per GPU-hour, billed on run time): a new-fire task ~ 5 min build + 2-5 min members -> ~8 min; 167 new x 8 min = 22 GPU-h = $20; 54 Aug 2 tasks with
sent inputs ~ 3 min = 2.7 GPU-h = $2.4; 19 Aug 2 builds ~ 8 min = 2.5 GPU-h = $2.3. Expected ~ $25 (inside $30). Hard ceiling if every task ran the full 30 min limit:
240 x 0.5 h x $0.90 = $108 (not expected). To cut build time on the GPU nodes, optionally prebuild locally (free) before syncing, see "Optional prebuild".
The first 100 GPU hours on Tillicum are free per hyak/README.md (and the memory note says it is free for us); check `hyakusage` after.

## Commands for the main session

Needs the Earthdata token on Tillicum at the same path with mode 600 (use the existing copy there if present): check with
`ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes elinzi@tillicum.hyak.uw.edu 'ls -l ~/.config/ignopticon/earthdata-token'`; if missing:
```
ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes elinzi@tillicum.hyak.uw.edu 'mkdir -p ~/.config/ignopticon && chmod 700 ~/.config/ignopticon'
scp -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes ~/.config/ignopticon/earthdata-token elinzi@tillicum.hyak.uw.edu:.config/ignopticon/earthdata-token
ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes elinzi@tillicum.hyak.uw.edu 'chmod 600 ~/.config/ignopticon/earthdata-token'
```

### 1. sync (from the repo root)
```
R=/gpfs/projects/stf/elinzi/ignopticon; H=elinzi@tillicum.hyak.uw.edu; SSH="ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes"
rsync -aR -e "$SSH" scripts/ hyak/multiday.slurm data/multiday/tasks.txt data/multiday/catalog.csv data/multiday/manifest.json \
  data/perimeter_batch/gridmet_fm100_2026080{1,2,3,4}.tif earth/public/data/fires/feds/2026/08/ $H:$R/
rsync -aR -e "$SSH" --files-from=data/multiday/aug2_inputs.txt ./ $H:$R/     # 592 files, ~0.8 GB: Aug 2 inputs (skips 19 builds)
$SSH $H "mkdir -p $R/hyak/logs $R/data/raw"
```
(`scripts/` includes the changed `elmfire_case.py` WDROT, `perimeter_batch.py`, `where_skill.py`, `growth_bounds.py`, new `multiday.py`.)

### 2. submit (30 min limit, 16 at a time, 240 tasks)
```
$SSH $H "cd $R && sbatch --array=0-239%16 hyak/multiday.slurm"
# safer first: one new and one aug2 task:  sbatch --array=0,167 hyak/multiday.slurm   (check hyak/logs/multiday-*.out and data/multiday/status/*/ then submit the rest with --array=1-166,168-239%16)
$SSH $H "squeue -u elinzi; ls $R/data/multiday/status/*/ | wc -l"
```
Resubmitting is safe (finished members and built inputs are skipped). Failed or timed-out tasks: `jq -r 'select(.status!="complete")|.fire' $R/data/multiday/status/*/*.json`, then `--array=` their line numbers (grep -n the fire in tasks.txt, minus 1).

### 3. collect (rasters needed for scoring, not raw inputs)
```
rsync -a -e "$SSH" $H:$R/data/multiday/status/ data/multiday/status/
rsync -a -e "$SSH" --prune-empty-dirs --include='*/' --include='time_of_arrival_*.tif' --include='run.json' --include='associated-observed.json' --exclude='*' \
  $H:$R/data/perimeter_batch/ data/perimeter_batch/
rsync -a -e "$SSH" --prune-empty-dirs --include='*/' --include='landscape.tif' --include='fire-state.tif' --include='fire-observed.json' --include='case.json' \
  --include='wx-subh/***' --exclude='*' $H:$R/data/training/fires/ data/training/fires/
```
(The perimeter_batch pull only brings `runs/physics3/*/outputs/` and `associated-observed.json`; the training pull only brings what `where_skill.py` reads. It can overwrite
the existing Aug 2 local files with identical-input results; the adj1 rerun should match the local control: spot-check `grown_cells.adj1` in a status file against the local run.)

### 4. scoring, local
```
scripts/multiday.py recall 2026-08-03T12:00     # A and B per day: recall of observed +12 h growth, control vs ensemble union (uncut) -> data/multiday/recall_DAY.json
scripts/multiday.py recall 2026-08-04T12:00; scripts/multiday.py recall 2026-08-05T12:00; scripts/multiday.py recall 2026-08-02T12:00
for d in 2026-08-03 2026-08-04 2026-08-05; do WHERE_ISSUE=${d}T12:00 scripts/where_skill.py manifest && WHERE_ISSUE=${d}T12:00 scripts/where_skill.py score; done   # -> data/where_skill/DAY/{manifest.json,results.md}
scripts/growth_bounds.py export      # Aug 2 lower/upper re-exported; upper footprint = union of the 11 members (+ any adj0.5 / adj1-wind0.7 / fuelwind16 runs already there) -> earth/public/data/forecasts_bounds
scripts/growth_bounds.py heldout     # held-out 12, now with the ensemble-union upper footprint -> data/bounds/heldout_table.md
```
`where_skill.py` with `WHERE_ISSUE` set (any day but Aug 2) reads `data/multiday/containment_DAY.json`, the runs in `data/perimeter_batch/FIRE/runs/physics3/adj1`, and writes `data/where_skill/DAY/`.
Its eligibility (uncontained) is the same rule as the frozen manifest. Per MODELLING_LOG.md the new days are held-out in the sense that no choice was made on them; the member
ranges (+-20 deg, x0.75/1.25, +-2 points) are fixed here, before any result.

### Optional prebuild (free local CPU instead of GPU-hours)
`for n in $(seq 0 166); do echo $n; done | xargs -P6 -I{} env WORKERS=8 scripts/multiday.py task {} --members none` builds the 167 new cases locally (about 9 h); then add their
inputs (`landscape.tif fire-state.tif fire-observed.json case.json wx/*/ws.tif obs/* wx-subh/*`) to the sync like `aug2_inputs.txt`. Not needed to meet the $30 target.
