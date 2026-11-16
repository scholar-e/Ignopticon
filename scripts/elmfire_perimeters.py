#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "shapely>=2.1", "pyproj"]
# ///
"""Export cumulative ELMFIRE perimeters using only observations known at issue.
Usage: uv run scripts/elmfire_perimeters.py RUN_OUTPUT_DIR OUTPUT.geojson
Requires run.json containing STATE and OBSERVED provenance. Outputs +0/6/12/24h.
"""
import datetime as dt
import hashlib
import json
import logging
from pathlib import Path
import sys
import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.warp import reproject, Resampling
from pyproj import Transformer
import shapely
from shapely.geometry import shape, mapping

log=logging.getLogger(__name__)

def utc(s):
    t=dt.datetime.fromisoformat(s.replace('Z','+00:00'))
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def known_start(run, grid_shape, transform, crs):
    issue=utc(run['issue'])
    options=dict(v.split('=',1) for v in run['overrides'] if '=' in v)
    observed=Path(options['OBSERVED'])
    obs=json.loads(observed.read_text())
    with rasterio.open(run['state']) as src:
        start=np.zeros(src.shape,np.uint8); st=src.transform; state_crs=src.crs
    project=Transformer.from_crs('EPSG:4326',state_crs,always_xy=True)
    reference=utc(obs['reference'])
    for frame in obs['frames']:
        if reference+dt.timedelta(seconds=frame['seconds']) > issue:
            continue
        for lon,lat,*_ in frame['cells']:
            x,y=project.transform(lon,lat)
            col,row=~st*(x,y); row,col=int(np.floor(row)),int(np.floor(col))
            if 0<=row<start.shape[0] and 0<=col<start.shape[1]: start[row,col]=1
    known=[p for p in obs['perimeters'] if utc(p['time'])<=issue]
    latest=max(known,key=lambda p:utc(p['time'])) if known else None
    if latest:
        geom=shapely.transform(shape(latest['geometry']),lambda xy:np.column_stack(project.transform(xy[:,0],xy[:,1])))
        start|=rasterize([(mapping(geom),1)],out_shape=start.shape,transform=st,dtype='uint8')
    result=np.zeros(grid_shape,np.uint8)
    reproject(start,result,src_transform=st,src_crs=state_crs,dst_transform=transform,dst_crs=crs,resampling=Resampling.nearest)
    return result.astype(bool), {'observations':str(observed),'latest_perimeter':latest['time'] if latest else None,
                                'coarse_start_cells':int(start.sum()),'observation_sha256':hashlib.sha256(observed.read_bytes()).hexdigest()}


def main(run_dir,out):
    run_dir=Path(run_dir);out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(str(out)+'.log')],force=True)
    run=json.loads((run_dir/'run.json').read_text())
    assert run['engine']=='elmfire' and run['hours']>=24
    toa_path=sorted(run_dir.glob('time_of_arrival_*.tif'))[-1]
    with rasterio.open(toa_path) as src:
        toa=src.read(1);transform=src.transform;crs=src.crs
        valid=(src.read_masks(1)>0)&np.isfinite(toa)&(toa>=0)
    start,provenance=known_start(run,toa.shape,transform,crs)
    start|=valid&(toa==0)
    area=abs(transform.a*transform.e)
    project=Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
    features=[];previous=None
    for h in (0,6,12,24):
        burned=start|(valid&(toa<=h*3600))
        geom=shapely.union_all([shape(g) for g,value in shapes(burned.astype(np.uint8),mask=burned,transform=transform)])
        assert geom.is_valid
        assert abs(geom.area-burned.sum()*area)<area*.001
        if previous is not None: assert previous.difference(geom).area<area*.001
        previous=geom
        ll=shapely.transform(geom,lambda xy:np.column_stack(project.transform(xy[:,0],xy[:,1])))
        assert ll.is_valid
        props={'kind':'perimeter','hours':h,'valid_time':(utc(run['issue'])+dt.timedelta(hours=h)).isoformat(),
               'acres':round(burned.sum()*area/4046.8564224,2),
               'growth_acres':round((burned&~start).sum()*area/4046.8564224,2)}
        features.append({'type':'Feature','properties':props,'geometry':mapping(ll)})
        log.info('+%dh: %.2f acres, growth %.2f acres',h,props['acres'],props['growth_acres'])
    doc={'type':'FeatureCollection','issued':utc(run['issue']).isoformat(),'model':run['model'],
         'grid_resolution_m':[abs(transform.a),abs(transform.e)],'grid_crs':str(crs),
         'provenance':provenance|{'arrival_raster':str(toa_path),'arrival_sha256':hashlib.sha256(toa_path.read_bytes()).hexdigest()},
         'description':'Cumulative modeled burned footprint, including disconnected detections. Grid resolution is not perimeter accuracy. Experimental retrospective case study.',
         'features':features}
    out.write_text(json.dumps(doc,separators=(',',':'))+'\n')
    log.info('Validated four nested cumulative perimeters; wrote %s',out)

if __name__=='__main__': main(*sys.argv[1:])
