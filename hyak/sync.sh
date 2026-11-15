#!/usr/bin/env bash
# Copies the repo's code and model inputs to Tillicum, or the results back. Raw satellite data, the earth page's
# tiles and node_modules stay here; only what the runs read goes over. ELMFIRE itself is in the image.
#
# Usage: hyak/sync.sh push|pull [PATH ...]
#   push: scripts/, hyak/ (with ignopticon-model.tar if exported), data/fires/, the Widemouth landscape and
#         weather (data/elmfire/widemouth/{landscape.tif,wx*}), data/harmonize/{fuel,elevation}.npz, and any
#         extra PATHs (repo-relative).
#   pull: logs, run.json and fire_size_stats.csv of every case under data/elmfire/ and data/ensembles/, plus
#         earth/public/data/fires/frames/; rasters stay on Tillicum unless PATHs ask for them.
# Environment: HYAK_USER (elinzi), HYAK_HOST (tillicum), HYAK_DIR (/gpfs/projects/stf/elinzi/ignopticon),
#   CM, an ssh ControlPath (default ~/.ssh/cm-%r@%h:%p). Open the master once (password + Duo), then every
#   ssh/rsync here reuses it:
#     ssh -o ControlMaster=yes -o ControlPersist=24h -o ControlPath=~/.ssh/cm-%r@%h:%p -fN elinzi@tillicum.hyak.uw.edu
set -euo pipefail

user=${HYAK_USER:-elinzi}
dir=${HYAK_DIR:-/gpfs/projects/stf/elinzi/ignopticon}
host=$user@${HYAK_HOST:-tillicum}.hyak.uw.edu
cm=${CM:-~/.ssh/cm-%r@%h:%p}
ssh_cmd=(ssh -o ControlPath="$cm" -o ControlMaster=no -o BatchMode=yes)
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
action=${1:-}
shift || true

"${ssh_cmd[@]}" -O check "$host" 2>/dev/null \
    || { echo "no ssh master at $cm for $host; open one first (see the header of $0)"; exit 1; }
rs=(rsync -aRz --info=stats1,progress2 --partial -e "${ssh_cmd[*]}")

case "$action" in
    push)
        "${ssh_cmd[@]}" "$host" mkdir -p "$dir"
        paths=(scripts hyak data/fires data/elmfire/widemouth/landscape.tif data/elmfire/widemouth/wx*)
        for f in data/harmonize/fuel.npz data/harmonize/elevation.npz; do [[ -e $f ]] && paths+=("$f"); done
        "${rs[@]}" --exclude '__pycache__' --exclude '*.sif' "${paths[@]}" "$@" "$host:$dir/"
        ;;
    pull)
        for sub in data/elmfire data/ensembles; do
            "${rs[@]}" --include '*/' --include '*.log' --include 'run.json' --include 'member.json' \
                --include 'fire_size_stats.csv' --include 'done' --exclude '*' --prune-empty-dirs \
                "$host:$dir/./$sub" "$repo/" || true
        done
        "${rs[@]}" "$host:$dir/./earth/public/data/fires/frames" "$repo/" 2>/dev/null || true
        for path in "$@"; do "${rs[@]}" "$host:$dir/./$path" "$repo/"; done
        ;;
    *)
        echo "usage: hyak/sync.sh push|pull [PATH ...]"
        exit 2
        ;;
esac
