#!/usr/bin/env bash
# Packed ELMFIRE runs on Tillicum: one job runs a list of scripts/elmfire_forecast.sh calls inside the container,
# one per CPU (xargs -P), since ELMFIRE is CPU-only and every GPU billed brings 8 CPUs. Cases already run
# (CASE/outputs/run.json) are skipped, so a job that hit its time limit can simply be submitted again.
#
# Usage (from the repo copy, e.g. /gpfs/projects/stf/elinzi/ignopticon):
#   sbatch hyak/tillicum.slurm LIST.txt     LIST: one line of elmfire_forecast.sh arguments per run
#                                            (the format of data/elmfire/widemouth/ab3-queue.txt)
#   sbatch --array=0-N hyak/tillicum.slurm CHUNKDIR   task i runs CHUNKDIR/chunk-00i.txt (hyak/submit_members.sh
#                                            makes the chunks and submits; use it for big lists)
#   sbatch hyak/tillicum.slurm ENSEMBLE_DIR [-- OBSERVED STATE ISSUE OUT]   a make_ensemble.py ensemble
#                                            (has members.txt): scripts/run_ensemble.sh, as before
#   More CPUs: sbatch --gres=gpu:2 --cpus-per-task=16 --mem=128G ... (8 CPUs per GPU at most; normal QoS: 24 h,
#   16 GPUs / 128 CPUs per job). Environment: SIF (default hyak/ignopticon-model.sif).
#   Log: hyak/logs/JOBNAME-JOBID_TASK.out; one line per run: exit code, wall seconds, case, fire area.
#
#SBATCH --job-name=elmfire
#SBATCH --account=stf
#SBATCH --partition=gpu-h200
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=hyak/logs/%x-%A_%a.out
set -euo pipefail

target=$1
shift
repo=${SLURM_SUBMIT_DIR:-$(pwd)}
cd "$repo"
sif=${SIF:-$repo/hyak/ignopticon-model.sif}
jobs=${SLURM_CPUS_PER_TASK:-$(nproc)}
command -v apptainer >/dev/null || { echo "no apptainer on $(hostname) (it is /usr/bin/apptainer on compute nodes)"; exit 1; }

# --cleanenv keeps Slurm's variables out, so Open MPI's mpirun inside starts a plain local singleton instead of
# trying to launch through srun; the image sets ELMFIRE, PY=python3 and the OMPI root variables.
run=(apptainer exec --cleanenv --bind "$repo" --pwd "$repo" --env PY=python3 --env RUN_PY=python3 "$sif")

if [[ -d $target && -e $target/members.txt ]]; then
    exec "${run[@]}" scripts/run_ensemble.sh "$target" "$jobs" "$@"
fi
list=$target
if [[ -d $target ]]; then
    list=$(printf '%s/chunk-%03d.txt' "$target" "${SLURM_ARRAY_TASK_ID:?a chunk directory needs --array}")
fi
[[ -s $list ]] || { echo "empty or missing list $list"; exit 1; }
echo "$(date -u +%FT%TZ) $(hostname) job ${SLURM_JOB_ID:-} task ${SLURM_ARRAY_TASK_ID:-} list $list:" \
     "$(grep -c . "$list") runs, $jobs at a time; $(lscpu | grep -m1 'Model name' | sed 's/.*: *//')"
"${run[@]}" hyak/run_list.sh "$list" "$jobs"
