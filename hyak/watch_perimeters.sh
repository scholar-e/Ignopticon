#!/usr/bin/env bash
# Watch the authorized submissions, then collect their compact artifacts.
set -euo pipefail
cd "$(dirname "$0")/.."
cm=${CM:-$HOME/.ssh/cm-%r@%h:%p}
while true; do
  active=$(ssh -o ControlPath="$cm" -o BatchMode=yes elinzi@tillicum.hyak.uw.edu 'squeue -h -u elinzi -o "%A" | sort -u')
  if ! grep -qE '^(473163|473166)$' <<< "$active"; then break; fi
  sleep 30
done
ssh -o ControlPath="$cm" -o BatchMode=yes elinzi@tillicum.hyak.uw.edu \
  'sacct -j 473163,473166 -X -n -P --format=JobID,State,Elapsed,ExitCode' > data/perimeter_batch/accounting.txt
hyak/collect_perimeters.sh
