#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm", "scikit-learn"]
# ///
"""Recent MODIS corroboration of VIIRS on jointly clear cell-days; not independent truth."""
import json,logging,sys
from pathlib import Path
import numpy as np
import lightgbm as lgb
import harmonize as H
from compare_dataset import grid_year

OUT=Path('data/edge_causal/corroboration')
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',handlers=[logging.StreamHandler(),logging.FileHandler(OUT/'run.log')],force=True)
 model=lgb.Booster(model_file='data/compare/controlled/causal_harmonizer.txt');names=model.feature_name()
 assert not {'next','next3'}&set(names)
 H.FEATURES=names;results={}
 for year in map(int,sys.argv[1:] or range(2015,2027)):
  m=H.modis('data/harmonize/grid',year);features=H.features(m,year);v,clear=grid_year('VNP14A1',year)
  counts={'joint_clear':0,'viirs_fire':0,'modis_support':0,'viirs_without_modis_support':0,'modis_support_without_viirs':0,'both':0,'expected_viirs':0.0}
  for d in range(0,len(v),8):
   p=model.predict(np.column_stack([features[k][d:d+8].ravel() for k in names]).astype(np.float32),num_threads=8).reshape(v[d:d+8].shape)
   ok=(clear[d:d+8]>0)&(m['clear'][d:d+8]>0);fire=v[d:d+8]>0;support=p>=.35
   counts['joint_clear']+=int(ok.sum());counts['viirs_fire']+=int((ok&fire).sum());counts['modis_support']+=int((ok&support).sum())
   counts['both']+=int((ok&fire&support).sum());counts['viirs_without_modis_support']+=int((ok&fire&~support).sum());counts['modis_support_without_viirs']+=int((ok&~fire&support).sum());counts['expected_viirs']+=float(p[ok].sum())
  results[str(year)]=counts;(OUT/f'{year}.json').write_text(json.dumps(counts,indent=2)+'\n');(OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n');logging.info('%d %s',year,counts)
if __name__=='__main__':main()
