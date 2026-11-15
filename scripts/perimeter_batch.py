#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj"]
# ///
"""One map fire: build causal issue inputs, run ELMFIRE sensitivity members,
score cumulative/growth perimeters and boundary distances, export agreement envelopes.
Run in modelling container: python3 scripts/perimeter_batch.py FIRE
Environment: PERIMETER_MEMBERS=fuelwind16 (default; 2x2x2x2 fuel-group/wind-exponent spread corrections) or
uniform10 (the first batch's uniform ADJ x WSMULT set); MIN_START_CELLS=20 (0 = off) minimum burnable initial
footprint, see elmfire_case.py; REBUILD=1 rebuilds inputs even when a ready case.json exists; WORKERS=n.
"""
import concurrent.futures as cf
import csv, datetime as dt, hashlib, json, logging, os, subprocess, sys
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from scipy.ndimage import binary_erosion, distance_transform_edt
import shapely
from shapely.geometry import shape, mapping
from pyproj import Transformer
from elmfire_perimeters import known_start, utc

ISSUE='2026-08-02T12:00'
OUT=Path('data/perimeter_batch')
def _uniform10():
    return {f'adj{a:g}-wind{w:g}':{'ADJ':a,'WSMULT':w} for a,w in
            [(a,w) for a in (.02,.1,.5,1.) for w in (.8,1.2)]+[(.02,1.),(1.,1.)]}
def _fuelwind16():
    # Absolute multipliers per fuel group (GR = grass + grass-shrub, SH = shrub, TIMBER = timber understory,
    # litter, slash); ADJ_WIND_EXP b scales them by (cell mean wind / 10 mph)**b. ADJ_WIND_REF stays at its default.
    return {f'gr{g:g}-sh{h:g}-tm{t:g}-we{e:g}':{'ADJ_GR':g,'ADJ_SH':h,'ADJ_TIMBER':t,'ADJ_WIND_EXP':e}
            for g in (.4,1.) for h in (.4,1.) for t in (.2,.6) for e in (-1,0)}
MEMBER_SETS={'uniform10':_uniform10,'fuelwind16':_fuelwind16}
MEMBER_SET=os.environ.get('PERIMETER_MEMBERS','fuelwind16')
MEMBERS=MEMBER_SETS[MEMBER_SET]()
MIN_START=int(os.environ.get('MIN_START_CELLS','20' if MEMBER_SET=='fuelwind16' else '0'))
GROUPS={'GR':list(range(101,110))+list(range(121,125)),'SH':list(range(141,150)),
        'TU':list(range(161,166)),'TL':list(range(181,190)),'SB':list(range(201,205))}
log=logging.getLogger(__name__)
PYRUN=(os.environ.get('PY') or 'uv run --script').split()  # PY=python3 in the container

def run(command, logfile):
    with open(logfile,'a') as f:
        subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)

def scores(pred,truth,start,res):
    def iou(a,b):
        union=(a|b).sum()
        return float((a&b).sum()/union) if union else None
    pb=pred&~binary_erosion(pred);tb=truth&~binary_erosion(truth)
    distance=None
    if pb.any() and tb.any():
        distances=np.r_[distance_transform_edt(~tb,sampling=res)[pb],distance_transform_edt(~pb,sampling=res)[tb]]
        distance={'mean_m':float(distances.mean()),'p95_m':float(np.quantile(distances,.95))}
    return {'iou':iou(pred,truth),'growth_iou':iou(pred&~start,truth&~start),
            'no_growth_iou':iou(start,truth),'boundary':distance,
            'predicted_growth_cells':int((pred&~start).sum()),'observed_growth_cells':int((truth&~start).sum()),
            'area_ratio':float((pred&~start).sum()/(truth&~start).sum()) if (truth&~start).any() else None}

def fire_features(root,toa,valid,start,res,hours=24,zone_m=2000):
    """Per-fire predictors known at issue: fuel-group fractions of burnable unburned area within zone_m of the
    initial burning cells, mean HRRR 20-ft wind over the forecast, initial size."""
    from scipy.ndimage import binary_dilation
    hot=valid&(toa==0)
    with rasterio.open(root/'landscape.tif') as src: fbfm=src.read(4)
    zone=binary_dilation(hot,iterations=max(1,int(zone_m/res[0])))&~start
    burn=zone&(fbfm>100)
    n=int(burn.sum())
    frac={k:(float(np.isin(fbfm[burn],v).sum()/n) if n else None) for k,v in GROUPS.items()}
    frac['TIMBER']=None if not n else frac['TU']+frac['TL']+frac['SB']
    return {'fuel_fraction_near_edge':frac,'burnable_fraction_near_edge':float(n/zone.sum()) if zone.any() else None,
            'edge_zone_m':zone_m,'initial_burning_acres':float(hot.sum()*res[0]*res[1]/4046.8564224),
            'initial_burned_acres':float(start.sum()*res[0]*res[1]/4046.8564224)}

def mean_wind(wxdir,hours=24):
    with rasterio.open(Path(wxdir)/'ws.tif') as src: ws=src.read()[:hours+1]
    return {'mean_wind_mph':float(ws.mean()),'max_hourly_domain_mean_wind_mph':float(ws.mean((1,2)).max())}

def main(fire):
    dest=OUT/fire;dest.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(dest/'worker.log')],force=True)
    row=next(r for r in csv.DictReader(open(OUT/'catalog.csv')) if r['id']==fire)
    status={'fire':fire,'feds_id':row['feds_id'],'issue':ISSUE,'status':'building','member_set':MEMBER_SET,
            'members_planned':MEMBERS,'min_start_cells':MIN_START}
    save=lambda: (dest/'status.json').write_text(json.dumps(status,indent=2)+'\n')
    save()
    try:
        os.environ['FEDS_SNAPSHOT_DIR']='earth/public/data/fires/feds/2026/08'
        root=Path('data/training/fires')/fire
        def built():
            try:
                c=json.loads((root/'case.json').read_text())
                return c.get('ready') and any(i['issue']==ISSUE and Path(i['wx'],'ws.tif').exists() for i in c['issues'])
            except (OSError,ValueError,KeyError): return False
        if os.environ.get('REBUILD') or not built():
            run([*PYRUN,'scripts/build_fire_case.py',fire,'--catalog',str(OUT/'catalog.csv'),
                 '--issue',ISSUE,'--feds-fire-id',row['feds_id']],dest/'build.log')
            status['inputs']='built'
        else: status['inputs']='reused'
        case=json.loads((root/'case.json').read_text())
        if case['truncated']: raise ValueError('Fire bounding box truncated; requires a larger-domain run')
        if not case.get('ready'): raise ValueError('No complete weather/initialization available')
        item=next(i for i in case['issues'] if i['issue']==ISSUE)
        obs=json.loads(Path(item['observed']).read_text())
        # Associate initial heat with this fire, not every fire in the box.
        # The 3 km association buffer is a declared sensitivity assumption.
        known=[p for p in obs['perimeters'] if utc(p['time'])<=utc(ISSUE)]
        if not known: raise ValueError('No fire-specific perimeter available at issue')
        outline=max(known,key=lambda p:utc(p['time']))
        with rasterio.open(root/'fire-state.tif') as src: state_crs=src.crs
        projection=Transformer.from_crs('EPSG:4326',state_crs,always_xy=True)
        g=shapely.transform(shape(outline['geometry']),lambda xy:np.column_stack(projection.transform(xy[:,0],xy[:,1]))).buffer(3000)
        removed=0
        for f in obs['frames']:
            if utc(f['time'])>utc(ISSUE): continue
            kept=[c for c in f['cells'] if g.covers(shapely.Point(*projection.transform(c[0],c[1])))]
            removed+=len(f['cells'])-len(kept);f['cells']=kept
        associated=dest/'associated-observed.json'
        associated.write_text(json.dumps(obs,separators=(',',':')))
        item['observed']=str(associated)
        status.update(initial_association_buffer_m=3000,unassociated_initial_detections_removed=removed)
        hot=sum(len(f['cells']) for f in obs['frames'] if utc(ISSUE)-dt.timedelta(hours=6)<=utc(f['time'])<=utc(ISSUE))
        if hot==0: raise ValueError('No observed active cells in six hours before issue')
        # Use matching geolocation already available; do not fabricate cloud masks.
        if Path('data/training/raw/viirs_geo').exists():
            run([*PYRUN,'scripts/obscured_cells.py',item['observed'],'data/training/raw/viirs_fire',
                 'data/training/raw/viirs_geo'],dest/'cloud.log')
        status.update(status='running',grid_resolution_m=case['elmfire_cell_m'],hot_cells_before=hot,
                      data={'satellites':'S-NPP, NOAA-20, NOAA-21 when available','weather':item['wx'],
                            'hrrr_hours':item.get('hrrr_hours',24),'fuels':'LANDFIRE LF2025',
                            'terrain':'3DEP elevation/slope/aspect','cloud_masks':'matching available 03IMG granules only'})
        save()
        def member(name):
            folder=dest/'runs'/MEMBER_SET/name
            if (folder/'outputs'/'run.json').exists() and not os.environ.get('REBUILD'): return name,folder/'outputs'
            extra=[f'{k}={v}' for k,v in MEMBERS[name].items()]+([f'MIN_START_CELLS={MIN_START}'] if MIN_START else [])
            run(['bash','scripts/elmfire_forecast.sh',str(root/'landscape.tif'),item['wx'],str(root/'fire-state.tif'),
                 ISSUE,'24',str(folder),'OBSERVED='+item['observed'],*extra,'LH_MOISTURE_CONTENT=90'],
                dest/(MEMBER_SET+'-'+name+'.log'))
            return name,folder/'outputs'
        workers=int(os.environ.get('WORKERS',os.environ.get('SLURM_CPUS_PER_TASK',4)))
        with cf.ThreadPoolExecutor(workers) as pool: completed=list(pool.map(member,MEMBERS))
        try: status['min_start']=json.loads((dest/'runs'/MEMBER_SET/completed[0][0]/'case.json').read_text()).get('min_start')
        except (OSError,ValueError): status['min_start']=None
        counts={h:None for h in (6,12,24)};all_scores={};metadata=None
        for name,folder in completed:
            meta=json.loads((folder/'run.json').read_text())
            with rasterio.open(sorted(folder.glob('time_of_arrival_*.tif'))[-1]) as src:
                toa=src.read(1);valid=(src.read_masks(1)>0)&(toa>=0)&np.isfinite(toa)
                transform,crs,res=src.transform,src.crs,src.res
            start,provenance=known_start(meta,toa.shape,transform,crs);start|=valid&(toa==0)
            project=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
            observed=[]
            for p in obs['perimeters']:
                hours=(utc(p['time'])-utc(ISSUE)).total_seconds()/3600
                if not 0<hours<=24: continue
                if str(p['fireid'])!=row['feds_id']: raise ValueError('Perimeter fire identity mismatch')
                g=shapely.transform(shape(p['geometry']),lambda xy:np.column_stack(project.transform(xy[:,0],xy[:,1])))
                truth=rasterize([(mapping(g),1)],out_shape=toa.shape,transform=transform,dtype='uint8').astype(bool)
                observed.append({'hours':hours,'time':p['time'],**scores(start|(valid&(toa<=hours*3600)),truth,start,res)})
            all_scores[name]=observed
            for h in counts:
                pred=start|(valid&(toa<=h*3600))
                counts[h]=pred.astype(np.uint16) if counts[h] is None else counts[h]+pred
            metadata=provenance
            if 'features' not in status:
                status['features']={**fire_features(root,toa,valid,start,res),**mean_wind(item['wx'])}
        to_ll=Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
        features=[]
        for h,count in counts.items():
            for fraction in (.1,.5,.9):
                mask=count>=int(np.ceil(fraction*len(completed)))
                polys=[shape(g) for g,_ in shapes(mask.astype(np.uint8),mask=mask,transform=transform)]
                g=shapely.union_all(polys);assert g.is_valid
                ll=shapely.transform(g,lambda xy:np.column_stack(to_ll.transform(xy[:,0],xy[:,1])))
                features.append({'type':'Feature','properties':{'hours':h,'member_agreement':fraction,
                    'acres':float(mask.sum()*res[0]*res[1]/4046.8564224)},'geometry':mapping(ll)})
        (dest/'envelopes.geojson').write_text(json.dumps({'type':'FeatureCollection','fire_id':row['feds_id'],
            'issued':ISSUE+'Z','description':'Parameter sensitivity; member agreement is not calibrated probability',
            'members':[n for n,_ in completed],'initial_state':metadata,'features':features},separators=(',',':'))+'\n')
        (dest/'scores.json').write_text(json.dumps({'member_set':MEMBER_SET,'members':MEMBERS,
            'features':status['features'],'scores':all_scores},indent=2)+'\n')
        status.update(status='complete',members=len(completed),perimeter_observations=len(next(iter(all_scores.values()))),
                      scoring_note='FEDS satellite-derived outlines; not independent airborne ground truth')
        save();log.info('COMPLETE %s',fire)
    except Exception as e:
        status.update(status='failed',error=str(e));save();log.exception('FAILED %s',fire);raise

if __name__=='__main__': main(sys.argv[1])
