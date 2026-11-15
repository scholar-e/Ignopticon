#!/usr/bin/env bash
# U-Net next-day fire model (docs/COMPARISON.md, model 2): one array task per arm, one H200 each.
# Runs scripts/unet_train.py in the PyTorch container hyak/torch.sif (pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime,
# torch + numpy are all it needs). Reads data/compare/tensors/, writes data/compare/{preds,models,logs}/.
#
# Usage (from the repo copy, /gpfs/projects/stf/elinzi/ignopticon):
#   sbatch --array=0-4 hyak/train_unet.slurm         all five arms (task i = ARMS[i] below)
#   sbatch --array=4 hyak/train_unet.slurm           viirs-only only
#   SEED_TAG=1 EPOCHS=16 PATIENCE=16 sbatch --array=0-14 hyak/train_unet.slurm   3 seeds per arm -> preds/seeds/
#                                                    (then scripts/unet_ensemble.py averages them)
#   Environment passes through to the script (EPOCHS, STEPS, BATCH, MAX_MIN, WIDTH, ...; see its header).
#
#SBATCH --job-name=unet
#SBATCH --account=stf
#SBATCH --partition=gpu-h200
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=hyak/logs/%x-%A_%a.out
set -euo pipefail

ARMS=(raw-pre harm-pre raw-all harm-all viirs-only)
task=${SLURM_ARRAY_TASK_ID:-0}
arm=${ARM:-${ARMS[$((task % 5))]}}
# seed ensembles: SEED_TAG=1 sbatch --array=0-14 ... -> task i trains ARMS[i % 5] with seed i / 5
if [[ ${SEED_TAG:-0} == 1 ]]; then export SEED=$((task / 5)); fi
repo=${SLURM_SUBMIT_DIR:-$(pwd)}
cd "$repo"
sif=${SIF:-$repo/hyak/torch.sif}
echo "$(date -u +%FT%TZ) $(hostname) job ${SLURM_JOB_ID:-} task ${SLURM_ARRAY_TASK_ID:-} arm $arm seed ${SEED:-0}"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
exec apptainer exec --nv --bind "$repo" --pwd "$repo" "$sif" python3 -u scripts/unet_train.py train "$arm"
