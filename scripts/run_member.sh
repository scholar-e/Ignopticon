#!/usr/bin/env bash
# Runs one ensemble member made by scripts/make_ensemble.py: the one entry point for local runs (run_ensemble.sh)
# and Hyak array tasks (hyak/ensemble.slurm). Outputs go to MEMBER/outputs, the log to MEMBER/member.log.
# A member with a "done" file is skipped, so a preempted or failed array can simply be resubmitted.
#
# Usage: scripts/run_member.sh MEMBER_DIR
# Environment: RUN_PY, how to run the Python scripts: default python3, with their dependencies installed as in the
#              container; on a machine with uv, "uv run -q --script" fetches them. ELMFIRE, the ELMFIRE executable
#              (default elmfire).
set -euo pipefail

member=$(realpath "$1")
repo=$(cd "$(dirname "$0")/.." && pwd)
python=python3  # for reading member.json; the standard library is enough
read -ra run_py <<< "${RUN_PY:-python3}"
elmfire=${ELMFIRE:-elmfire}

if [[ -e "$member/done" ]]; then
    echo "$member: done already"
    exit 0
fi
exec > "$member/member.log" 2>&1
echo "$(date -u +%FT%TZ) start on $(hostname)"
start=$(date +%s)
engine=$("$python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["engine"])' "$member/member.json")
setting() {
    "$python" -c 'import json, sys; print(json.load(open(sys.argv[1]))["settings"].get(sys.argv[2], sys.argv[3]))' \
        "$member/member.json" "$1" "$2"
}
mkdir -p "$member/outputs"

case "$engine" in
    ellipse)
        cd "$repo"
        "${run_py[@]}" scripts/ellipse_spread.py "$(setting state '')" "$(setting issue '')" "$member/outputs" \
            "$(setting wind '')" "$(setting head_ros 0.02)" "$(setting hours 24)" "$(setting hpa 8000)"
        ;;
    elmfire)
        cd "$member"
        # ELMFIRE needs OUTPUTS_DIRECTORY to exist; elmfire.data should set it to ./outputs.
        "$elmfire" elmfire.data
        "$python" -c 'import json, sys; m = json.load(open(sys.argv[1]))
json.dump(dict(m["settings"], model="ELMFIRE"), open(sys.argv[2], "w"), indent=1)' \
            "$member/member.json" "$member/outputs/run.json"
        ;;
    *)
        echo "unknown engine $engine"
        exit 2
        ;;
esac

echo "$(date -u +%FT%TZ) done in $(( $(date +%s) - start )) s"
touch "$member/done"
