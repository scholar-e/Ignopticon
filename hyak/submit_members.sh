#!/usr/bin/env bash
# Splits a big list of ELMFIRE runs into chunks sized to a target wall time and submits them as one Slurm job
# array of hyak/tillicum.slurm, after printing the estimated GPU-hours and cost. Refuses above COST_LIMIT.
# Run on a Tillicum login node from the repo copy (/gpfs/projects/stf/elinzi/ignopticon).
#
# Usage: hyak/submit_members.sh LIST.txt     LIST: one line of scripts/elmfire_forecast.sh arguments per run
#                                             (the format of data/elmfire/widemouth/ab3-queue.txt)
# Environment (defaults):
#   PER_RUN_S=60     CPU seconds per run on this project's Ryzen 9800X3D. Measured 2026-11-14: Widemouth 24 h
#                    runs take 6-7 s alone; under 16-way load the median was 24 s, p90 116 s, with rare runs
#                    of hours (high ADJ). Set it from your own list's runs.
#   SLOWDOWN=1.5     Tillicum core time / Ryzen core time (assumed until measured; see hyak/README.md)
#   TARGET_H=3       wall hours a chunk should take
#   GPUS=1           GPUs per array task (8 CPUs each; the job's CPUs come with its GPUs)
#   COST_LIMIT=200   refuse if the estimate exceeds this many dollars ($0.90 per GPU-hour)
#   MAX_PARALLEL=8   array tasks running at once (%N)
#   DRY_RUN=1        print the plan, write the chunks, submit nothing
# Cases already run (CASE/outputs/run.json) are left out. Chunks: hyak/chunks/NAME-TIMESTAMP/chunk-NNN.txt;
# logs: hyak/logs/. A chunk that hit its time limit can be resubmitted as is (done cases are skipped).
set -euo pipefail

list=${1:?usage: hyak/submit_members.sh LIST.txt}
per_run=${PER_RUN_S:-60}
slow=${SLOWDOWN:-1.5}
target_h=${TARGET_H:-3}
gpus=${GPUS:-1}
limit=${COST_LIMIT:-200}
cpus=$((8 * gpus))
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

todo=$(mktemp)
trap 'rm -f "$todo"' EXIT
# Leave out cases already run, then shuffle (fixed seed) so slow runs spread across chunks.
while read -r -a a; do
    (( ${#a[@]} >= 6 )) && [[ ! -e ${a[5]}/outputs/run.json ]] && echo "${a[*]}"
done < "$list" | shuf --random-source=<(yes) > "$todo" || true
n=$(wc -l < "$todo")
(( n > 0 )) || { echo "nothing to run: every case in $list is done"; exit 0; }

read -r per_chunk chunks wall_h time_s gpu_h cost worst <<< "$(awk -v n="$n" -v pr="$per_run" -v sl="$slow" \
    -v th="$target_h" -v c="$cpus" -v g="$gpus" 'BEGIN {
    t = pr * sl                                    # Tillicum seconds per run
    pc = int(th * 3600 * c / t); if (pc < c) pc = c; if (pc > n) pc = n
    k = int((n + pc - 1) / pc)
    w = int((pc + c - 1) / c) * t / 3600          # wall hours of a full chunk
    lim = int(w * 1.5 * 3600 + 900); if (lim > 86400) lim = 86400
    gh = (k - 1) * g * w + g * int(((n - (k - 1) * pc) + c - 1) / c) * t / 3600
    printf "%d %d %.2f %d %.1f %.2f %.2f", pc, k, w, lim, gh, gh * 0.9, k * g * lim / 3600 * 0.9 }')"
time_limit=$(printf '%02d:%02d:00' $((time_s / 3600)) $((time_s % 3600 / 60)))

echo "$n runs to do (of $(grep -c . "$list") in $list); ${per_run} s each here x ${slow} = Tillicum estimate"
echo "$chunks array tasks of up to $per_chunk runs, $gpus GPU / $cpus CPUs each, ~${wall_h} h each" \
     "(time limit $time_limit)"
echo "estimate: $gpu_h GPU-hours = \$$cost; worst case (every task hits its limit): \$$worst; limit \$$limit"
if awk -v c="$cost" -v l="$limit" 'BEGIN { exit !(c > l) }'; then
    echo "refusing: estimate \$$cost is above COST_LIMIT=\$$limit"
    exit 1
fi

name=$(basename "$list" .txt)-$(date +%Y%m%d-%H%M%S)
dir=hyak/chunks/$name
mkdir -p "$dir" hyak/logs
split -d -a 3 -l "$per_chunk" --additional-suffix=.txt "$todo" "$dir/chunk-"
echo "chunks in $dir"
if [[ -n ${DRY_RUN:-} ]]; then
    echo "DRY_RUN: not submitted. To submit:"
    echo "  sbatch --array=0-$((chunks - 1))%${MAX_PARALLEL:-8} --gres=gpu:$gpus --cpus-per-task=$cpus" \
         "--mem=$((64 * gpus))G --time=$time_limit --job-name=$name hyak/tillicum.slurm $dir"
    exit 0
fi
sbatch --array=0-$((chunks - 1))%"${MAX_PARALLEL:-8}" --gres=gpu:"$gpus" --cpus-per-task="$cpus" \
    --mem=$((64 * gpus))G --time="$time_limit" --job-name="$name" hyak/tillicum.slurm "$dir"
