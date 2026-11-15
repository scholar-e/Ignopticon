#!/usr/bin/env bash
# Runs a list of scripts/elmfire_forecast.sh calls JOBS at a time; one line per run in LIST (the format of
# data/elmfire/widemouth/ab3-queue.txt). Run from the repo root, normally inside the container by
# hyak/tillicum.slurm, but it works anywhere elmfire_forecast.sh does. A failed run doesn't stop the others.
# Prints one line per run: exit code, wall seconds, case, and elmfire_forecast.sh's last line (the fire area).
#
# Usage: hyak/run_list.sh LIST [JOBS]
set -euo pipefail
list=$1
jobs=${2:-$(nproc)}
# One process each: keep Open MPI from probing InfiniBand/UCX for single-rank runs.
export OMPI_MCA_pml=ob1 OMPI_MCA_btl=self,vader OMPI_MCA_rmaps_base_oversubscribe=1

one() {
    local s=$SECONDS out rc=0
    out=$(scripts/elmfire_forecast.sh "$@" 2>&1) || rc=$?
    echo "rc=$rc $((SECONDS - s))s $6 $(tail -1 <<< "$out")"
}
export -f one
start=$SECONDS
xargs -a "$list" -L 1 -P "$jobs" bash -c 'one "$@"' _ || true
total=$(grep -c . "$list")
ok=$(while read -r -a a; do [[ ${#a[@]} -ge 6 && -e ${a[5]}/outputs/run.json ]] && echo; done < "$list" | wc -l)
echo "$ok of $total runs done in $((SECONDS - start)) s"
