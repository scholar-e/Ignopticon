#!/bin/bash
# run from the repo root after the Hyak array finished: bash data/h2h/collect.sh
R=/gpfs/projects/stf/elinzi/ignopticon; H=elinzi@tillicum.hyak.uw.edu; SSH="ssh -o ControlPath=$HOME/.ssh/cm-%r@%h:%p -o BatchMode=yes"
rsync -a -e "$SSH" $H:$R/data/multiday/status/20260813/ data/multiday/status/20260813/
for f in $(cut -f3 data/h2h/tasks_20260813.txt); do
  rsync -aR -e "$SSH" $H:$R/./data/perimeter_batch/$f/runs/physics3/adj1/outputs/ ./
  rsync -aR -e "$SSH" $H:$R/./data/training/fires/$f/landscape.tif $H:$R/./data/training/fires/$f/fire-observed.json $H:$R/./data/training/fires/$f/case.json ./
done
