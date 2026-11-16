#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm"]
# ///
"""Rebuild only pre-2012 edge samples with the existing causal harmonizer.
Leaves Claude's original data unchanged. Raw and VIIRS samples are reused.
"""
import hashlib,json,logging,os
from pathlib import Path
import numpy as np
import lightgbm as lgb
import harmonize as H
import compare_dataset as D
import edge_dataset as E

ROOT=Path('data/edge_causal')

def main():
 ROOT.mkdir(exist_ok=True);(ROOT/'samples').mkdir(exist_ok=True);(ROOT/'harmonized').mkdir(exist_ok=True)
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',handlers=[logging.StreamHandler(),logging.FileHandler(ROOT/'prepare.log')],force=True)
 for f in Path('data/edge/samples').glob('*.npz'):
  if not f.name.startswith('harm_') and not (ROOT/'samples'/f.name).exists(): (ROOT/'samples'/f.name).symlink_to(f.resolve())
 modelpath=Path('data/compare/controlled/causal_harmonizer.txt');model=lgb.Booster(model_file=str(modelpath))
 names=model.feature_name();assert 'next' not in names and 'next3' not in names
 H.FEATURES=names
 for year in range(2003,2012):
  target=ROOT/'harmonized'/f'harmonized_{year}.npz'
  if target.exists():continue
  m=H.modis('data/harmonize/grid',year);features=H.features(m,year)
  p=np.empty(m['fire'].shape,np.float16)
  for d in range(0,len(p),8):
   x=np.column_stack([features[k][d:d+8].ravel() for k in names]).astype(np.float32)
   p[d:d+8]=model.predict(x,num_threads=8).reshape(p[d:d+8].shape)
  np.savez_compressed(str(target)+'.tmp.npz',p_viirs=p)
  os.replace(str(target)+'.tmp.npz',target)
  logging.info('Causal harmonization %d complete',year)
  del features,m,p
 tensors=ROOT/'tensors';tensors.mkdir(exist_ok=True)
 for name in ['static.npz','weather']:
  path=tensors/name
  if not path.exists():path.symlink_to((Path('data/compare/tensors')/name).resolve())
 D.OUT=str(tensors);D.HARM=str(ROOT/'harmonized');E.TENSORS=str(tensors);E.OUT=str(ROOT/'samples')
 for year in range(2003,2012):
  D.build('harm',year);E.build('harm',year)
 manifest={'harmonizer':str(modelpath),'harmonizer_sha256':hashlib.sha256(modelpath.read_bytes()).hexdigest(),
  'harmonizer_training_years':[2012,2013,2014],'harmonizer_features':names,'weather':'day t only selected by EDGE_DAY_T_ONLY=1',
  'sample_fire_threshold':E.HARM_FIRE,'note':'Causal harmonization; soft labels and arm-specific edge selection remain experimental.'}
 (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');(ROOT/'ready').touch()
 logging.info('READY: all five causal comparison arms')

if __name__=='__main__':main()
