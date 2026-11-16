#!/usr/bin/env bash
#SBATCH --job-name=h2h
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
# One fire-issue per array task (line SLURM_ARRAY_TASK_ID of data/h2h/tasks_20260813.txt): HRRR (shared, flock'd cache),
# build the case when its inputs were not sent, then the 11-member worst-case ensemble (adj1 control + wind direction x
# speed + dead moisture) on the 8 CPUs. Submit: sbatch --array=0-27%16 hyak/h2h.slurm
# Needs ~/.config/ignopticon/earthdata-token (mode 600) for the LAADS VIIRS download. Resubmittable: finished members skip.
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
[[ -s data/h2h/tasks_20260813.txt ]]
[[ -s "$HOME/.config/ignopticon/earthdata-token" ]]   # LAADS; apptainer binds $HOME by default
exec apptainer exec --cleanenv --bind "$PWD" --pwd "$PWD" \
  --env PY=python3 --env RUN_PY=python3 \
  --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 --env WORKERS="${SLURM_CPUS_PER_TASK:-8}" \
  --env LANDSCAPE_TILE="${LANDSCAPE_TILE:-256}" --env LANDSCAPE_ATTEMPTS="${LANDSCAPE_ATTEMPTS:-10}" \
  --env TASKS=data/h2h/tasks_20260813.txt --env CATALOG=data/h2h/catalog_20260813.csv --env MEMBERS=adj1 \
  --env LANDSCAPE_DEM="${LANDSCAPE_DEM:-landfire}" \
  hyak/ignopticon-model.sif python3 scripts/multiday.py task "$SLURM_ARRAY_TASK_ID"
