#!/usr/bin/env bash
# Fetch compact perimeter products, leaving raw inputs and run rasters on Hyak.
set -euo pipefail
cd "$(dirname "$0")/.."
remote=elinzi@tillicum.hyak.uw.edu
cm=${CM:-$HOME/.ssh/cm-%r@%h:%p}
rsync -a -e "ssh -o ControlPath=$cm -o BatchMode=yes" \
  --include='map*/' --include='status.json' --include='scores.json' --include='envelopes.geojson' --exclude='*' \
  "$remote:/gpfs/projects/stf/elinzi/ignopticon/data/perimeter_batch/" data/perimeter_batch/
node <<'JS'
const fs=require('fs'); const base='data/perimeter_batch';
const ids=fs.readFileSync(base+'/fires.txt','utf8').trim().split('\n');
const summary={issued:'2026-08-02T12:00:00Z',total:ids.length,complete:0,failed:0,pending:0,fires:[]};
for(const id of ids){const p=base+'/'+id+'/status.json';const s=fs.existsSync(p)?JSON.parse(fs.readFileSync(p)):{fire:id,status:'pending'};
if(s.status==='complete')summary.complete++;else if(s.status==='failed')summary.failed++;else summary.pending++;
summary.fires.push(s);}
fs.writeFileSync(base+'/summary.json',JSON.stringify(summary,null,2)+'\n');
console.log(JSON.stringify({total:summary.total,complete:summary.complete,failed:summary.failed,pending:summary.pending}));
JS
