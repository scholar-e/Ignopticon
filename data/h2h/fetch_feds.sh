#!/bin/bash
cd /home/elinzi/Coding/ignopticon
for d in 06 07 08 09 10 11 12 13 14; do for h in 0000 1200; do
 f=earth/public/data/fires/feds/2026/08/$d-$h.json
 [ -s $f ] && continue
 [ $d = 14 ] && [ $h = 1200 ] && continue
 uv run --script scripts/feds_to_earth_json.py $f 2026-08-${d}T${h:0:2}:00:00 
done; done
touch data/h2h/FEDS.DONE
