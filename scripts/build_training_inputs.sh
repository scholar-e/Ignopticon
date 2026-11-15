#!/usr/bin/env bash
# Builds ELMFIRE inputs (scripts/build_fire_case.py) for many catalog fires, PARALLEL at a time, skipping fires
# already ready; then writes the member list for every ready fire. Network-bound (LAADS, LANDFIRE, AWS HRRR), so
# it can run here or on a Tillicum compute node (they have internet; copy ~/.config/ignopticon/earthdata-token
# there first, mode 600). Each fire's log is data/training/fires/FIRE/build.log; a failed fire is listed in
# data/training/build_failed.txt and the rest carry on (rerun to retry: finished steps are skipped).
#
# Usage: scripts/build_training_inputs.sh [FIRE_LIST.txt]
#        FIRE_LIST.txt: one catalog id per line (default data/training/fires_full.txt)
# Environment: PARALLEL (default 4), MAX_ISSUES (default 6), PY (default "uv run --script"; "python3" in the
#              container), MEMBERS_OUT (default data/training/members_full.txt)
set -uo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
list=${1:-data/training/fires_full.txt}
parallel=${PARALLEL:-4}
max_issues=${MAX_ISSUES:-6}
pyrun=${PY:-uv run --script}
out=${MEMBERS_OUT:-data/training/members_full.txt}
mkdir -p data/training/fires
: > data/training/build_failed.txt
build() {
  local fire=$1
  if [[ -f data/training/fires/$fire/case.json ]] && grep -q '"ready": true' "data/training/fires/$fire/case.json"; then
    echo "$fire: ready already"; return 0
  fi
  if $pyrun scripts/build_fire_case.py "$fire" --max-issues "$max_issues" > "data/training/fires/$fire.out" 2>&1; then
    echo "$fire: $(tail -1 "data/training/fires/$fire.out" | cut -c60-)"
  else
    echo "$fire" >> data/training/build_failed.txt
    echo "$fire: FAILED (data/training/fires/$fire.out)"
  fi
}
export -f build
export max_issues pyrun
grep -v '^\s*#' "$list" | grep . | xargs -P "$parallel" -I{} bash -c 'build {}'
$pyrun scripts/make_training_members.py "$out" --ready
echo "failed: $(wc -l < data/training/build_failed.txt) (data/training/build_failed.txt)"
