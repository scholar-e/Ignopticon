# Running ensembles on Hyak (Tillicum)

The same three pieces run locally and on Hyak:

| Step | Here (16 cores) | Hyak |
|---|---|---|
| Make members | `scripts/make_ensemble.py OUTDIR ENGINE KEY=V1,V2 ...` | same, here or there |
| Run members | `scripts/run_ensemble.sh OUTDIR 16` (xargs) | `sbatch hyak/tillicum.slurm OUTDIR` (members packed onto the job's CPUs) |
| Score | `run_ensemble.sh ... -- OBSERVED STATE ISSUE OUT` | same job: `hyak/tillicum.slurm OUTDIR -- OBSERVED STATE ISSUE OUT` |

Each member is a directory with `member.json` (engine and settings). `scripts/run_member.sh` runs a member, writes `outputs/` and `member.log`, and marks it `done`. Members marked done are skipped, so a preempted or partly failed array can simply be resubmitted. The engines are `ellipse` (the stand-in) and `elmfire`. ELMFIRE members copy a template `elmfire.data` with namelist overrides; the inputs in the template directory are symlinked, not copied.

## Software

There's one image for both machines, `hyak/Containerfile`: Ubuntu 24.04 with gfortran, Open MPI, ELMFIRE, GDAL's command-line tools, and the scripts' Python packages. Compute nodes need no network and nothing installed in your Hyak home. Hyak has Apptainer, not Docker or podman, so the image is built here with podman and converted there.

```
podman build -t ignopticon-model -f hyak/Containerfile hyak      # here
podman save -o hyak/ignopticon-model.tar ignopticon-model
# copy hyak/ignopticon-model.tar over (sync.sh push sends hyak/), then on a compute node:
apptainer build hyak/ignopticon-model.sif docker-archive://hyak/ignopticon-model.tar
```

The image hasn't been built yet. The ELMFIRE build step follows its repo's `build/linux/make_gnu.sh` and is unchecked. Once it builds, pin `ELMFIRE_REF` to that commit.

## Tillicum (what we have)

Tillicum is UW's GPU cluster: 24 nodes, each with 8 H200 GPUs, 64 CPUs and ~1.6 TB RAM. Billing is **$0.90 per GPU hour**, and a job's CPUs and memory come with its GPUs (at most 8 CPUs and ~200 GB RAM per GPU). The first 100 GPU hours are free. There's no preemptible tier and no `ckpt` partition. The `normal` QoS allows 24 h, and up to 16 GPUs / 128 CPUs per job. Home is `/gpfs/home/NETID` (10 GB), the project is `/gpfs/projects/GROUP` (1 TB), and scratch is `/gpfs/scrubbed/` (files unread for 60 days are deleted). Log in with `ssh NETID@tillicum.hyak.uw.edu` (password plus Duo). Do transfers on the login nodes. `hyakusage` shows hours and cost. Source: hyak.uw.edu/docs/systems/tillicum, read 2026-11-12.

ELMFIRE never uses the GPU, so `hyak/tillicum.slurm` packs members onto a job's CPUs: one job runs `run_ensemble.sh` with one member per CPU. A job array would bill a GPU hour for each one-core member. In core-hours, 1 GPU (8 cores) is half of this machine, and a full node (8 GPUs, 64 cores, $7.20/h) is 4x this machine. Tillicum is good value for M5 training and for a GPU spread model. For CPU-only ELMFIRE it helps only with ensembles too big for here.

The docs disagree on whether a job can request no GPU (`--cpus-per-task=N` with no `--gres`), and they don't say how such a job is billed. Test this with a small job and check `hyakusage`. If CPU-only jobs work cheaply, use them for ELMFIRE.

## One run, end to end (Tillicum)

```
# 1. here: observations, fire state, members
scripts/fire_observations.py data/fires/FIRE-DATE DETECTIONS.csv data/raw/viirs_l1b DATE LON LAT 30
scripts/make_ensemble.py data/ensembles/FIRE elmfire template=data/elmfire/FIRE WIND_SPEED_MULTIPLIER=0.8,1,1.2 ...

# 2. copy over (HYAK_HOST defaults to tillicum)
export HYAK_USER=netid HYAK_DIR=/gpfs/projects/stf/elinzi/ignopticon
hyak/sync.sh push data/elmfire/FIRE

# 3. on Tillicum, from $HYAK_DIR: run every member and score them in one job (1 GPU = 8 members at a time;
#    a full node: --gres=gpu:8 --cpus-per-task=64 --mem=400G)
sbatch hyak/tillicum.slurm data/ensembles/FIRE -- data/fires/FIRE-DATE-observed.json \
       data/fires/FIRE-DATE-state.tif ISSUE earth/public/data/fires/frames/YYYY/MM/DD.json

# 4. here: logs and the scored frames (rasters stay on Tillicum unless asked for)
hyak/sync.sh pull
```

The container is built on Tillicum the same way as above. Run `apptainer build` from `/tmp` with `APPTAINER_CACHEDIR=/tmp`, so the 10 GB home doesn't fill up.

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
- **Per-member cost.** A 24-hour ELMFIRE run on the demo fire's area (PLAN.md "Before the event", step 3) needs timing here first. That sets `--time` and `--mem`, and whether Tillicum is needed at all.
