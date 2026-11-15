# Area-constrained ELMFIRE: growth model notes

Pipeline: `scripts/growth_dataset.py [viirs raw harm]` (rows_*.parquet, ~1.5 min) -> `scripts/growth_train.py ARM --seed N --variant t1|t0`
-> `scripts/growth_report.py` (test_table.md) -> `scripts/area_constrain.py fit | issue | run` -> `scripts/edge_pilot_score.py
data/growth/heldout_eval --arms control,areacon_t1,areacon_t0,areacon_oracle --manifest data/edge_causal/front_weighted/manifest.json`
(comparison.md there). Method and numbers: MODELLING_LOG.md entry 24.

## Hyak/Tillicum (NOT submitted by this agent: ssh and rsync to Tillicum were denied by the permission classifier)

The local run already covers 3 seeds x 3 arms x 2 variants x 4 grid points in ~2 min; Hyak adds only more seeds. Cost: array of 9 x <= 0.5 h x $0.90 = <= $4.05
(real use ~2 min per task, a few cents); ceiling $20. From the repo root, in the main session:
```
S="ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes"
$S elinzi@tillicum.hyak.uw.edu 'hyakusage | tail -5; ls /gpfs/projects/stf/elinzi/ignopticon/hyak/ignopticon-model.sif'   # check first
rsync -aR -e "$S" scripts/growth_train.py hyak/growth_train.slurm data/growth/rows_viirs.parquet data/growth/rows_raw.parquet data/growth/rows_harm.parquet elinzi@tillicum.hyak.uw.edu:/gpfs/projects/stf/elinzi/ignopticon/
$S elinzi@tillicum.hyak.uw.edu 'cd /gpfs/projects/stf/elinzi/ignopticon && mkdir -p hyak/logs data/growth/runs && sbatch --array=0-8 hyak/growth_train.slurm'   # note the job id
# watch: squeue -u elinzi ; done when hyak/logs/growth-JOB_*.out all end with the t0 lines
rsync -a -e "$S" elinzi@tillicum.hyak.uw.edu:/gpfs/projects/stf/elinzi/ignopticon/data/growth/runs/ data/growth/runs_hyak/
```
Task i: arm = (harm, raw, viirs-only)[i%3], seed = i/3, so seeds 0-2 repeat the local runs (same code; LightGBM thread count can
change results slightly). For new seeds use `--array=0-17` after changing nothing (seeds 0-5). To merge, copy the runs_hyak/*.json
into data/growth/runs/ and rerun growth_report.py. The container needs lightgbm, pandas, pyarrow only.
