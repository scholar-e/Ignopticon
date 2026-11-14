#!/usr/bin/env bash
# Runs an ensemble on Hyak (klone) as a Slurm job array: one task per member of ENSEMBLE/members.txt, each running
# scripts/run_member.sh in the container on one core. Members already done are skipped, so after a preemption
# (the ckpt partition) or failures, submit the same command again.
#
# Usage (from the repo copy in /gscratch):
#   sbatch --account=GROUP --partition=PARTITION --array=1-$(wc -l < ENSEMBLE/members.txt)%MAX \
#          hyak/ensemble.slurm ENSEMBLE
#   GROUP and PARTITION: see `hyakalloc` (an account's own partition, or ckpt for idle nodes, preemptible).
#   %MAX caps the members running at once. Logs: ENSEMBLE/slurm-*.out and each member's member.log.
#
#SBATCH --job-name=ignopticon-ensemble
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=02:00:00
#SBATCH --requeue
set -euo pipefail

ensemble=$1
repo=${SLURM_SUBMIT_DIR:-$(pwd)}
sif=${SIF:-$repo/hyak/ignopticon-model.sif}
member=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$ensemble/members.txt")
[[ -n "$member" ]] || { echo "no member $SLURM_ARRAY_TASK_ID in $ensemble/members.txt"; exit 1; }

module load apptainer 2>/dev/null || true  # on the PATH already on some nodes
apptainer exec --bind "$repo" --pwd "$repo" "$sif" scripts/run_member.sh "$member"
