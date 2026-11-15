#!/usr/bin/env bash
# Non-destructive checks of Tillicum access over an open ssh ControlMaster (no Duo): login node, budget,
# storage, apptainer, the gpu-h200 partition, and what's already in the project dir.
# Open the master first:
#   ssh -o ControlMaster=yes -o ControlPersist=24h -o ControlPath=~/.ssh/cm-%r@%h:%p -fN elinzi@tillicum.hyak.uw.edu
# Usage: hyak/tillicum_check.sh     (HYAK_USER, HYAK_HOST, HYAK_DIR, CM as for hyak/sync.sh)
set -uo pipefail
user=${HYAK_USER:-elinzi}
dir=${HYAK_DIR:-/gpfs/projects/stf/elinzi/ignopticon}
host=$user@${HYAK_HOST:-tillicum}.hyak.uw.edu
cm=${CM:-~/.ssh/cm-%r@%h:%p}
ssh -o ControlPath="$cm" -O check "$host" || { echo "no ssh master open for $host at $cm"; exit 1; }
ssh -o ControlPath="$cm" -o ControlMaster=no -o BatchMode=yes "$host" bash -s -- "$dir" <<'EOF'
dir=$1
section() { echo; echo "== $*"; }
section host; hostname; date -u
section hyakusage; hyakusage 2>&1 | tail -15
section "hyakstorage -g"; hyakstorage -g 2>&1 | tail -10
section apptainer; which apptainer singularity 2>&1; apptainer --version 2>&1
section "sinfo -p gpu-h200"; sinfo -p gpu-h200 -o '%P %a %l %D %t' 2>&1 | head -10
section "your jobs"; squeue -u "$USER" 2>&1 | head -10
section "$dir"; ls -la "$dir" 2>&1 | head -15; ls -la "$dir/hyak" 2>&1 | grep -E 'tar|sif' ; df -h "$dir" 2>&1 | tail -1
EOF
