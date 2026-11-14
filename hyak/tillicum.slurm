#!/usr/bin/env bash
# Runs a whole ensemble on Tillicum in one job: scripts/run_ensemble.sh in the container, one member per CPU, then
# scores the members if the scoring arguments are given. Tillicum bills GPU hours ($0.90 each) and every GPU
# brings 8 CPUs and up to ~200 GB RAM, so members are packed onto the CPUs a job gets rather than run as a job
# array (an array task per member would pay a GPU hour for one core). ELMFIRE never touches the GPU.
# Members already done are skipped, so a job that hit its time limit or failed can simply be submitted again.
#
# Usage (from the repo copy in /gpfs/projects/GROUP/... or /gpfs/scrubbed/...):
#   sbatch hyak/tillicum.slurm ENSEMBLE [-- OBSERVED.json STATE.tif ISSUE OUT.json]
#   A full node: sbatch --gres=gpu:8 --cpus-per-task=64 --mem=400G hyak/tillicum.slurm ENSEMBLE ...
#   (8 CPUs per GPU at most; the normal QoS allows 24 h and 16 GPUs / 128 CPUs per job.)
#   Log: slurm-JOBID.out where sbatch was run, and each member's member.log.
#
#SBATCH --job-name=ignopticon-ensemble
#SBATCH --partition=gpu-h200
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
set -euo pipefail

ensemble=$1
shift
repo=${SLURM_SUBMIT_DIR:-$(pwd)}
sif=${SIF:-$repo/hyak/ignopticon-model.sif}
jobs=${SLURM_CPUS_PER_TASK:-$(nproc)}

command -v apptainer >/dev/null || { echo "no apptainer on this node (no module on Tillicum); see hyak/README.md"; exit 1; }
apptainer exec --bind "$repo" --pwd "$repo" "$sif" scripts/run_ensemble.sh "$ensemble" "$jobs" "$@"
