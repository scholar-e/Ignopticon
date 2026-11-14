#!/usr/bin/env bash
# Copies the repo's code and model inputs to Hyak, or the results back. Raw satellite data, the earth page's tiles
# and node_modules stay here; only what the runs read goes over.
#
# Usage: HYAK_USER=netid HYAK_DIR=/gpfs/projects/stf/elinzi/ignopticon hyak/sync.sh push|pull [PATH ...]
#   HYAK_HOST: tillicum (default) or klone.
#   push: scripts/, hyak/ (with the .sif if built here), data/fires/, data/ensembles/, the GFS wind files, and any
#         extra PATHs (e.g. ELMFIRE input directories).
#   pull: data/ensembles/ member logs and run.json files, plus earth/public/data/fires/frames/; the rasters stay on
#         Hyak unless PATHs ask for them.
# Logging in asks for your UW password and Duo; set up an ssh ControlMaster to be asked once.
set -euo pipefail

: "${HYAK_USER:?set HYAK_USER to your UW NetID}"
: "${HYAK_DIR:?set HYAK_DIR, e.g. /gpfs/projects/GROUP/NETID/ignopticon on Tillicum (home is 10 GB)}"
host=$HYAK_USER@${HYAK_HOST:-tillicum}.hyak.uw.edu
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
action=$1
shift

case "$action" in
    push)
        ssh "$host" mkdir -p "$HYAK_DIR"
        rsync -avR --exclude '__pycache__' scripts hyak data/fires data/ensembles \
            earth/public/data/weather "$@" "$host:$HYAK_DIR/"
        ;;
    pull)
        rsync -avR --include '*/' --include 'member.log' --include 'member.json' --include 'run.json' \
            --include 'done' --exclude '*' "$host:$HYAK_DIR/./data/ensembles" "$repo/"
        rsync -avR "$host:$HYAK_DIR/./earth/public/data/fires/frames" "$repo/"
        for path in "$@"; do rsync -avR "$host:$HYAK_DIR/./$path" "$repo/"; done
        ;;
    *)
        echo "usage: hyak/sync.sh push|pull [PATH ...]"
        exit 2
        ;;
esac
