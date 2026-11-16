#!/bin/bash
cd /home/elinzi/Coding/ignopticon
B=https://f005.backblazeb2.com/file/fire-forecast-archive
python3 -c "
import json
for f in json.load(open('data/h2h/manifest_20260813.json'))['fires']: print(f['slug'],f['run_ts'])" | while read s r; do
 mkdir -p data/h2h/pyrecast/$s/$r
 for p in 10 30 50 70 90; do [ -s data/h2h/pyrecast/$s/$r/$p.tif ] || curl -sf -o data/h2h/pyrecast/$s/$r/$p.tif $B/forecast_archive/$s/$r/$p.tif || echo FAIL $s $r $p; done
done
touch data/h2h/PYRE.DONE
