#!/usr/bin/env bash
#SBATCH --job-name=edge-causal
#SBATCH --account=stf
#SBATCH --partition=gpu-h200
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=hyak/logs/%x-%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
ARMS=(raw-all viirs-only raw-pre harm-all harm-pre)
task=${SLURM_ARRAY_TASK_ID:?}
arm=${ARMS[$((task%5))]}
seed=$((task/5))
out=data/edge_causal/runs/${arm}-seed${seed}
mkdir -p "$out"
exec apptainer exec --cleanenv --bind "$PWD" --pwd "$PWD" \
 --env EDGE_OUT="$out" --env EDGE_SAMPLES=data/edge_causal/samples \
 --env EDGE_DAY_T_ONLY=1 --env EDGE_SEED="$seed" --env EDGE_THREADS=8 \
 --env OMP_NUM_THREADS=8 --env OPENBLAS_NUM_THREADS=1 \
 hyak/ignopticon-model.sif python3 -u scripts/edge_model.py "$arm"
