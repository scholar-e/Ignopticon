#!/usr/bin/env bash
# Scores a finished ensemble against the observed heat frames (scripts/heat_frames.py) on Hyak, so only the small
# frames JSON needs copying back. Submit it to start when the array ends:
#
#   array=$(sbatch --parsable ... hyak/ensemble.slurm ENSEMBLE)
#   sbatch --account=GROUP --partition=PARTITION --dependency=afterany:$array \
#          hyak/score.slurm ENSEMBLE OBSERVED.json STATE.tif ISSUE OUT.json
#
# afterany, not afterok: members that failed are left out (heat_frames.py only reads members with outputs), and
# their member.log says why.
#
#SBATCH --job-name=ignopticon-score
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
set -euo pipefail

ensemble=$1
shift
repo=${SLURM_SUBMIT_DIR:-$(pwd)}
sif=${SIF:-$repo/hyak/ignopticon-model.sif}
runs=$(for m in $(cat "$ensemble/members.txt"); do [[ -e "$m/done" ]] && echo "$m/outputs"; done)
echo "$(echo "$runs" | wc -w) of $(wc -l < "$ensemble/members.txt") members finished"

module load apptainer 2>/dev/null || true
apptainer exec --bind "$repo" --pwd "$repo" "$sif" python3 scripts/heat_frames.py "$@" $runs
