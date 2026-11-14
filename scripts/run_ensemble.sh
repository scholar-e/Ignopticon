#!/usr/bin/env bash
# Runs every member of an ensemble on this machine, JOBS at a time (default: one per core), then scores them
# against the observed heat frames if the scoring arguments are given. RUN_PY as for run_member.sh.
#
# Usage: scripts/run_ensemble.sh ENSEMBLE_DIR [JOBS] [-- OBSERVED.json STATE.tif ISSUE OUT.json]
#        e.g. scripts/run_ensemble.sh data/ensembles/widemouth 16 -- data/fires/widemouth-2026-08-02-observed.json \
#                 data/fires/widemouth-2026-08-02-state.tif 2026-08-02T12:00 earth/public/data/fires/frames/2026/08/02.json
set -euo pipefail

ensemble=$1
jobs=${2:-$(nproc)}
repo=$(cd "$(dirname "$0")/.." && pwd)
shift $(( $# >= 2 ? 2 : 1 ))
[[ "${1:-}" == "--" ]] && shift

start=$(date +%s)
xargs -a "$ensemble/members.txt" -P "$jobs" -I{} "$repo/scripts/run_member.sh" {}
total=$(wc -l < "$ensemble/members.txt")
done=$(ls "$ensemble"/m*/done 2>/dev/null | wc -l)
echo "$done of $total members done in $(( $(date +%s) - start )) s"

if (( $# == 4 )); then
    read -ra run_py <<< "${RUN_PY:-python3}"
    "${run_py[@]}" "$repo/scripts/heat_frames.py" "$@" $(sed 's|$|/outputs|' "$ensemble/members.txt")
fi
