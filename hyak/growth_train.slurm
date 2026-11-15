#!/usr/bin/env bash
# Fire-growth model (scripts/growth_train.py): array = arms x seeds, each task runs the 4-point hyperparameter grid
# (objective tweedie|log1p x leaves 15|63) for both weather variants. CPU-bound LightGBM on the 8 CPUs that come with
# the one GPU Tillicum requires (the GPU is unused). Cost: 9 tasks x <= 0.5 h x $0.90 = <= $4.05 at the limit
# (each task really takes ~2 min); ceiling $20. Submit: sbatch --array=0-8 hyak/growth_train.slurm
#SBATCH --job-name=growth
#SBATCH --account=stf
#SBATCH --partition=gpu-h200
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=hyak/logs/%x-%A_%a.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
ARMS=(harm raw viirs-only)
task=${SLURM_ARRAY_TASK_ID:?}
arm=${ARMS[$((task%3))]}
seed=$((task/3))
echo "cost estimate: 1 GPU x <= 0.5 h x \$0.90 = <= \$0.45 for this task (array of 9: <= \$4.05, ceiling \$20)"
for variant in t1 t0; do
  apptainer exec --cleanenv --bind "$PWD" --pwd "$PWD" --env OMP_NUM_THREADS=8 hyak/ignopticon-model.sif \
    python3 -u scripts/growth_train.py "$arm" --seed "$seed" --variant "$variant" --grid small --threads 8 --out data/growth/runs
done
