#!/usr/bin/env bash
# One ELMFIRE forecast, end to end: builds the case (elmfire_case.py fire), runs ELMFIRE on one core, converts its
# outputs to GeoTIFF for heat_frames.py, and deletes the bulky intermediate .bsq/.bil files. Skips a case already
# run. Runs from any directory; one per core under xargs -P for a sweep.
#
# Usage: scripts/elmfire_forecast.sh LANDSCAPE.tif WXDIR STATE.tif ISSUE HOURS CASE [KEY=VALUE ...]
#        (arguments as for elmfire_case.py fire)
# Environment: ELMFIRE (default tools/elmfire/build/linux/bin/elmfire). scripts/gdal_shim is put on the PATH
# unless GDAL's own tools are already there.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
elmfire=$(realpath "${ELMFIRE:-$repo/tools/elmfire/build/linux/bin/elmfire}")
command -v gdalinfo >/dev/null || export PATH="$repo/scripts/gdal_shim:$PATH"
case=$6
[[ -e "$case/outputs/run.json" ]] && { echo "$case: done already"; exit 0; }
"$repo/scripts/elmfire_case.py" fire "$@" > /dev/null 2>"$case.build.log" || { cat "$case.build.log"; exit 1; }
mv "$case.build.log" "$case/build.log"
(cd "$case" && mpirun --bind-to none -np 1 "$elmfire" elmfire.data > elmfire.log 2>&1) || { tail -20 "$case/elmfire.log"; exit 1; }
"$repo/scripts/elmfire_case.py" outputs "$case" "ELMFIRE ${*:7}" > /dev/null 2>&1
rm -f "$case"/scratch/*.bsq "$case"/outputs/*.bil
echo "$case: $(grep -o 'Fire area: *[0-9.]* acres' "$case/elmfire.log" | tail -1)"
