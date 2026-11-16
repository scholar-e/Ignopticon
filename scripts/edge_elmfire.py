#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm", "rasterio", "pyproj", "scipy", "shapely>=2.1", "matplotlib"]
# ///
"""Opt-in three-fire learned perimeter activation pilot, previous complete UTC day.
Coarse candidate footprints retain constant probabilities; gaps remain nodata.
"""
import datetime as dt
import json
import logging
from pathlib import Path
import numpy as np
import lightgbm as lgb
import rasterio
from pyproj import Transformer
import edge_dataset as E
from front_calibrate import FIRES, elmfire, utc

ROOT=Path('data/edge_causal/activation')

def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
  handlers=[logging.StreamHandler(),logging.FileHandler(ROOT/'pilot.log')],force=True)
 models=[lgb.Booster(model_file=f'data/edge_causal/runs/harm-all-seed{s}/models/harm-all.txt') for s in range(3)]
 thresholds=[json.loads(Path(f'data/edge_causal/runs/harm-all-seed{s}/results.json').read_text())['arms']['harm-all']['threshold'] for s in range(3)]
 S=E.static_grid()
 summary={}
 for fire in ['2021-038','2021-031','2020-008']:
  cfg=FIRES[fire];issue=utc(cfg['issue']);day=issue.date()-dt.timedelta(days=1);year=day.year;d=day.timetuple().tm_yday-1
  z=np.load(f'data/compare/tensors/viirs/{year}.npz');f=z['x'][:,0].astype(np.float32)
  history=np.zeros((31,E.NY,E.NX),np.float32);start=max(0,d-30);history[-(d-start+1):]=f[start:d+1]
  if d<30:raise ValueError('Cross-year history not implemented')
  w=np.load(f'data/compare/tensors/weather/{year}.npz')['w'][d].astype(np.float32)
  X,r,c,*_=E.day_rows(history,w,w,S,1e-6,clear_t=z['x'][d,10]>0,doy=d+1,ndays=len(f))
  probabilities=np.mean([m.predict(X[:,[E.FEATS.index(n) for n in m.feature_name()]],num_threads=8) for m in models],axis=0)
  grid=np.full((E.NY,E.NX),-9999,np.float32);grid[r,c]=probabilities
  out=ROOT/fire;out.mkdir(exist_ok=True)
  landscape=Path('data/training/fires')/fire/'landscape.tif'
  with rasterio.open(landscape) as src:
   profile=src.profile.copy();rows,cols=np.indices(src.shape)
   xx,yy=rasterio.transform.xy(src.transform,rows,cols);lon,lat=Transformer.from_crs(src.crs,4326,always_xy=True).transform(xx,yy)
   rr=np.floor((50-np.asarray(lat).reshape(src.shape))/.1).astype(int);cc=np.floor((np.asarray(lon).reshape(src.shape)+125)/.1).astype(int)
   valid=(rr>=0)&(rr<E.NY)&(cc>=0)&(cc<E.NX);probability_fine=np.full(src.shape,-9999,np.float32);probability_fine[valid]=grid[rr[valid],cc[valid]]
   profile.update(count=1,dtype='float32',nodata=-9999)
   with rasterio.open(out/'edge_probability.tif','w',**profile) as dst:
    dst.write(probability_fine,1)
    dst.update_tags(model_identifiers=json.dumps([f'harm-all-seed{s}' for s in range(3)]),
                    feature_date=str(day), threshold=str(float(np.mean(thresholds))),
                    validation_thresholds=json.dumps(thresholds), coarse_resolution_degrees='0.1',
                    mapping='fuel cell centre within coarse candidate footprint; no interpolation')
  logging.info('%s: %d candidates, threshold %.6f',fire,len(r),np.mean(thresholds))
  elmfire(fire,cfg,cfg['fc'],issue,out/'activation',['FRONT_PFILE='+str((out/'edge_probability.tif').resolve()), 'FRONT_PTHRESH='+str(float(np.mean(thresholds)))])
  elmfire(fire,cfg,cfg['fc'],issue,out/'control',[])
  summary[fire]={'feature_day':str(day),'issue':cfg['issue'],'candidate_cells':len(r),'covered_fuel_cells':int((probability_fine!=-9999).sum()),'threshold':float(np.mean(thresholds))}
  (ROOT/'pilot.json').write_text(json.dumps(summary,indent=2)+'\n')
  logging.info('%s: activation and control forecasts complete',fire)

if __name__=='__main__':main()
