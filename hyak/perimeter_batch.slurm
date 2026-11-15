#!/usr/bin/env bash
#SBATCH --job-name=perimeters
#SBATCH --account=stf
#SBATCH --partition=gpu-h200
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:20:00
#SBATCH --output=hyak/logs/%x-%A_%a.out
# One fire per array task. PERIMETER_MEMBERS (fuelwind16 | uniform10) and MIN_START_CELLS pass through;
# eight ELMFIRE members run at once on the eight CPUs.
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
fire=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" data/perimeter_batch/fires.txt)
[[ -n $fire ]]
members=${PERIMETER_MEMBERS:-fuelwind16}
minstart=${MIN_START_CELLS:-20}
exec apptainer exec --cleanenv --bind "$PWD" --pwd "$PWD" --env PY=python3 --env RUN_PY=python3 \
  --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 --env WORKERS="${SLURM_CPUS_PER_TASK:-8}" \
  --env PERIMETER_MEMBERS="$members" --env MIN_START_CELLS="$minstart" \
  hyak/ignopticon-model.sif python3 scripts/perimeter_batch.py "$fire"
