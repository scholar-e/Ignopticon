#!/usr/bin/env bash
# Authorized deadline training: wait for working authentication and causal samples.
# Runs locally. Does not start or release any perimeter-array jobs.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/edge_causal
exec 9>data/edge_causal/launch.lock
flock -n 9 || exit 1
socket=/home/elinzi/.ssh/ignopticon-edge.sock
remote=elinzi@tillicum.hyak.uw.edu
repo=/gpfs/projects/stf/elinzi/ignopticon
sshcmd=(ssh -S "$socket" -o BatchMode=yes -o ConnectTimeout=8)
# Authentication is supplied by the user; no password/token handling here.
for attempt in $(seq 1 720); do
  if [[ -S $socket ]] && timeout 15 "${sshcmd[@]}" "$remote" true; then break; fi
  if [[ $attempt == 720 ]]; then echo 'No usable Hyak connection within one hour'; exit 1; fi
  sleep 5
done
"${sshcmd[@]}" "$remote" "cd $repo && hyakusage && squeue -u elinzi -h -o '%i %T %M %R'"
rsync -aR -e "ssh -S $socket -o BatchMode=yes" scripts/edge_model.py scripts/edge_dataset.py scripts/compare_dataset.py \
  hyak/train_edge.slurm data/edge_causal/README.md "$remote:$repo/"
"${sshcmd[@]}" "$remote" "mkdir -p $repo/data/edge_causal/samples $repo/hyak/logs"
rsync -aL --include='raw_*.npz' --include='viirs_*.npz' --exclude='*' \
  -e "ssh -S $socket -o BatchMode=yes" data/edge_causal/samples/ "$remote:$repo/data/edge_causal/samples/"
if [[ ! -s data/edge_causal/controls-job.txt ]]; then
  "${sshcmd[@]}" "$remote" "cd $repo && sbatch --parsable --array=0,1,2,5,6,7,10,11,12%3 hyak/train_edge.slurm" > data/edge_causal/controls-job.txt
  echo "Control training submitted: $(cat data/edge_causal/controls-job.txt)"
fi
for attempt in $(seq 1 720); do
  [[ -f data/edge_causal/ready ]] && break
  [[ $attempt != 720 ]] || { echo 'Causal sample build not ready within 2 hours'; exit 1; }
  sleep 10
done
rsync -aL --include='harm_*.npz' --exclude='*' -e "ssh -S $socket -o BatchMode=yes" \
  data/edge_causal/samples/ "$remote:$repo/data/edge_causal/samples/"
rsync -aR -e "ssh -S $socket -o BatchMode=yes" data/edge_causal/manifest.json "$remote:$repo/"
if [[ ! -s data/edge_causal/harmonized-job.txt ]]; then
  "${sshcmd[@]}" "$remote" "cd $repo && sbatch --parsable --array=3,4,8,9,13,14%3 hyak/train_edge.slurm" > data/edge_causal/harmonized-job.txt
  echo "Harmonized training submitted: $(cat data/edge_causal/harmonized-job.txt)"
fi
