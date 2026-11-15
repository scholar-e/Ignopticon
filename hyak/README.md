# Running ELMFIRE on Tillicum

ELMFIRE is CPU-only, so Tillicum jobs pack runs onto the CPUs that come with a GPU (8 per GPU, $0.90 per GPU-hour): one job runs a list of `scripts/elmfire_forecast.sh` calls `xargs -P 8` inside the container. A list is one line of `elmfire_forecast.sh` arguments per run, the format of `data/elmfire/widemouth/ab3-queue.txt`. Cases already run (`CASE/outputs/run.json`) are skipped, so any job can simply be resubmitted.

| File | What |
|---|---|
| `Containerfile` | Ubuntu 24.04, gfortran + Open MPI, ELMFIRE f4927b7 (`/opt/elmfire/build/linux/bin/elmfire`, built `-march=x86-64-v3` so it runs on Intel nodes), GDAL 3.8 tools, uv, and a venv with every package `scripts/` needs (incl. pygrib, pyhdf, lightgbm, scikit-learn, pandas, pyarrow). Sets `ELMFIRE`, `PY=python3`, `RUN_PY=python3`, OMPI run-as-root. Scripts are not baked in: bind-mount the repo. |
| `run_list.sh LIST [JOBS]` | runs a list, JOBS at a time; one line per run (exit code, seconds, case, fire area) |
| `tillicum.slurm LIST` or `CHUNKDIR` (array) or `ENSEMBLE_DIR` | runs `run_list.sh` (or `run_ensemble.sh`) in apptainer; defaults 1 GPU, 8 CPUs, 64G, 4 h; log `hyak/logs/` |
| `submit_members.sh LIST` | splits a big list into chunks of ~`TARGET_H` (3) hours, prints GPU-hours and cost, refuses above `COST_LIMIT` ($200), submits a job array. `PER_RUN_S` (60), `SLOWDOWN` (1.5), `GPUS`, `DRY_RUN=1` |
| `sync.sh push\|pull` | rsync over an ssh ControlMaster (`CM`, default `~/.ssh/cm-%r@%h:%p`); push sends scripts/, hyak/ (with the image tar), data/fires/, the Widemouth landscape and wx*, data/harmonize/{fuel,elevation}.npz |
| `tillicum_check.sh` | read-only checks over the master: host, hyakusage, hyakstorage, apptainer, sinfo, squeue, project dir |

`scripts/elmfire_forecast.sh` reads `PY` (interpreter for the Python scripts; unset = their uv shebangs, as here) and `ELMFIRE` (binary). It runs ELMFIRE with `mpirun --bind-to none` (without it every run was pinned to core 0).

**Checked here (2026-11-14):** the image is 3.4 GB (`hyak/ignopticon-model.tar`, gitignored). The Widemouth Aug 4 12Z, ADJ=0.02, 24 h case in the container gives 15496.0 acres, with time of arrival identical to the native run; fireline intensity and spread rate differ in the last float digits (x86-64-v3 vs native). 5.8 s in the container, 7.0 s natively (uv startup). Uncontended Widemouth runs take 6-7 s; under 16-way load the median was 24 s.

## Steps

```
# here: build and export the image (only when the Containerfile changes)
podman build -t ignopticon-model -f hyak/Containerfile hyak
podman save --format docker-archive -o hyak/ignopticon-model.tar ignopticon-model

# here: open the master once (password + Duo), check, push
ssh -o ControlMaster=yes -o ControlPersist=24h -o ControlPath=~/.ssh/cm-%r@%h:%p -fN elinzi@tillicum.hyak.uw.edu
hyak/tillicum_check.sh
hyak/sync.sh push

# Tillicum: build the .sif in /tmp on a compute node (one short job), into the project dir
ssh -o ControlPath=~/.ssh/cm-%r@%h:%p elinzi@tillicum.hyak.uw.edu
cd /gpfs/projects/stf/elinzi/ignopticon
srun -A stf -p gpu-h200 --qos=normal --gres=gpu:1 --cpus-per-task=8 --mem=32G --time=00:30:00 bash -c \
  'd=$PWD; export APPTAINER_CACHEDIR=/tmp/$USER-apptainer APPTAINER_TMPDIR=/tmp/$USER-apptainer
   mkdir -p $APPTAINER_TMPDIR && cd /tmp && apptainer build /tmp/$USER-model.sif docker-archive://$d/hyak/ignopticon-model.tar &&
   cp /tmp/$USER-model.sif $d/hyak/ignopticon-model.sif; rm -rf /tmp/$USER-model.sif $APPTAINER_TMPDIR'

# Tillicum: one real run, to measure per-run time there (seconds in the log line; compare with 6-7 s here)
mkdir -p hyak/logs; w=data/elmfire/widemouth; echo "$w/landscape.tif $w/wx-080412 data/fires/widemouth-0802-0806-state.tif 2026-08-04T12:00 24 $w/tillicum-test/adj0.02 OBSERVED=data/fires/widemouth-0802-0806-observed.json ADJ=0.02 LH_MOISTURE_CONTENT=90" > hyak/test-list.txt
sbatch --time=00:20:00 hyak/tillicum.slurm hyak/test-list.txt; squeue -u $USER; cat hyak/logs/elmfire-*.out

# Tillicum: a big list (set PER_RUN_S from your runs, SLOWDOWN from the test)
DRY_RUN=1 hyak/submit_members.sh LIST.txt     # plan and cost only
hyak/submit_members.sh LIST.txt
hyakusage

# here: logs, run.json, fire_size_stats.csv (rasters stay there unless named)
hyak/sync.sh pull [PATH ...]
```

## Tillicum (what we have)

Tillicum is UW's GPU cluster: 24 nodes, each with 8 H200 GPUs, 64 CPUs and ~1.6 TB RAM. Billing is **$0.90 per GPU hour**, and a job's CPUs and memory come with its GPUs (at most 8 CPUs and ~200 GB RAM per GPU). The first 100 GPU hours are free. There's no preemptible tier and no `ckpt` partition. The `normal` QoS allows 24 h, and up to 16 GPUs / 128 CPUs per job. Home is `/gpfs/home/NETID` (10 GB), the project is `/gpfs/projects/GROUP` (1 TB), and scratch is `/gpfs/scrubbed/` (files unread for 60 days are deleted). Log in with `ssh NETID@tillicum.hyak.uw.edu` (password plus Duo). Do transfers on the login nodes. `hyakusage` shows hours and cost. Source: hyak.uw.edu/docs/systems/tillicum, read 2026-11-12.

In core-hours, 1 GPU (8 cores) is half of this machine, and a full node (8 GPUs, 64 cores, $7.20/h) is 4x this machine. A job array task per single run would bill a GPU hour for one core, hence the packing.

## klone

`hyak/ensemble.slurm` and `hyak/score.slurm` are for klone, the CPU cluster: one array task per member, with `--account` and the preemptible `ckpt` partition. Use them with `HYAK_HOST=klone` and `/gscratch/GROUP/...` if we ever get a klone allocation.

## Checked on Tillicum (2026-11-12)

- **Account and budget:** NetID `elinzi` charges the Slurm account `stf` (the Student Technology Fee group, which has ~$10.6k of credit shared by all its users). Your own budget is $250 in total, enforced, ending 2027-05-02, about 280 GPU-hours. Unix groups: `stf tillicum tillicum_stf klone klone_stf`, so klone may be available too (see below).
- **Storage:** `/gpfs/projects/stf` is shared by the whole group (20 TB, 48% used). Put work in `/gpfs/projects/stf/elinzi/ignopticon`, or put bulk data under `/gpfs/scrubbed/`.
- **Software:** Apptainer and Singularity are at `/usr/bin` on compute nodes (there's no module for them). The other modules are comsol, conda (Miniforge3), gcc 11.5/13.4, jupyter, matlab and parallel.
- **Network:** compute nodes reach the internet, through a proxy ("Connection established").
- **No-GPU jobs:** they run. `salloc --cpus-per-task=1 --mem=4G` landed on g022 with 1 CPU and no GPU. Afterwards `hyakusage` still showed $0.00. The documented formula bills raw GPU hours × multipliers, which is 0 for a job with no GPU. That job hardly used anything, though, and usage is counted at job end and may show up late.

**2026-11-14:** the user says Tillicum is free for us; use it for large training jobs (M5, any GPU spread model). `hyakusage` still lists the $250 enforced budget, so keep an eye on it.

## Still unknown

- **Whether no-GPU jobs are allowed.** Slurm accepts them, and the cost estimate counts only GPU hours (8 CPUs, 40 min: "$0.00 (Est.)"). Two sources say they're not allowed, though. The get-started page says every job "requires at least 1 GPU", and the onboarding tutorial (github.com/UWrc/tillicum-onboarding, 05-slurm.md) says "CPU-only jobs are not allowed". The newer scheduling page only says a request with no options "will provide 1 CPU and no GPU". No page gives a CPU-hour rate. So $0 looks like a gap the scheduler doesn't enforce, not a policy. Don't build on it until help@uw.edu (subject "Tillicum") says it's fine. Until then `hyak/tillicum.slurm` keeps `--gres=gpu:1`, and ELMFIRE ensembles go to klone if we have it.
- **klone.** You're in `klone_stf`, so `ssh elinzi@klone.hyak.uw.edu` and run `hyakalloc`. klone is the CPU cluster and has the preemptible `ckpt` partition. That's what `hyak/ensemble.slurm` was written for, and it may be the free home for ELMFIRE.
- **Per-run time on Tillicum.** Here a Widemouth 24 h run takes 6-7 s alone. `submit_members.sh` assumes Tillicum cores are 1.5x slower until the one-run test in Steps measures it; also check `--mem` (64G for 8 runs) on bigger fires.
