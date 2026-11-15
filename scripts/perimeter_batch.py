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
physics3 (third batch): ADJ 1, ADJ 0.5, ADJ 1 with WSMULT 0.7 over HOURS=12, with INIT=feds (FEDS perimeter at
issue on the fuel grid, active edge band; elmfire_case.py) and WX=subhourly (hrrr_weather.py --subhourly: 15-min
HRRR wind, EMC 1-h, lagged 10-h, previous-day gridMET fm100 from FM100_TIF). Scores at FEDS outlines within HOURS,
the fuelwind16 runs (if present) rescored on the same start footprint and truth, and heat_frames.py VIIRS CSI
for overpasses within HOURS. An earlier member set's status/scores/envelopes are kept as NAME.SET.json first.
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
def _physics3():
    return {'adj1':{'ADJ':1.},'adj0.5':{'ADJ':.5},'adj1-wind0.7':{'ADJ':1.,'WSMULT':.7}}
def _worstcase():
    # Worst-case ensemble around the physics3 control adj1: wind direction -20/0/+20 deg x wind speed x0.75/1/1.25
    # (the 0 / x1 cell is the control itself), plus dead fuel moisture -2/+2 points on the control. 11 members.
    m={'adj1':{'ADJ':1.}}
    for d in (-20,0,20):
        for w in (.75,1,1.25):
            if d==0 and w==1: continue
            m[f'wd{d:+d}-ws{w:g}']={'ADJ':1.,'WDROT':d,'WSMULT':w}
    for p in (-2,2): m[f'moist{p:+d}']={'ADJ':1.,'MOIST':p}
    return m
MEMBER_SETS={'uniform10':_uniform10,'fuelwind16':_fuelwind16,'physics3':_physics3,'worstcase':_worstcase}
MEMBER_SET=os.environ.get('PERIMETER_MEMBERS','fuelwind16')
PHYS=MEMBER_SET in ('physics3','worstcase')
HOURS=int(os.environ.get('HOURS','12' if PHYS else '24'))
INIT=os.environ.get('INIT','feds' if PHYS else 'hotcells')
WX=os.environ.get('WX','subhourly' if PHYS else 'hourly')
FM100_TIF=os.environ.get('FM100_TIF','data/perimeter_batch/gridmet_fm100_20260801.tif')
HORIZONS=(1,3,6,12) if HOURS<=12 else (6,12,24)
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
    info=Path(wxdir)/'wx.json'
    step=json.loads(info.read_text()).get('dt_s',3600) if info.exists() else 3600
    with rasterio.open(Path(wxdir)/'ws.tif') as src: ws=src.read()[:int(hours*3600/step)+1]
    return {'mean_wind_mph':float(ws.mean()),'max_hourly_domain_mean_wind_mph':float(ws.mean((1,2)).max())}

def main(fire):
    dest=OUT/fire;dest.mkdir(parents=True,exist_ok=True)
    try:  # keep an earlier member set's products (e.g. scores.fuelwind16.json) before overwriting
        old=json.loads((dest/'status.json').read_text()).get('member_set')
        if old and old!=MEMBER_SET:
            for n in ('status.json','scores.json','envelopes.geojson'):
                f=dest/n;keep=dest/f'{f.stem}.{old}{f.suffix}'
                if f.exists() and not keep.exists(): keep.write_bytes(f.read_bytes())
    except (OSError,ValueError): pass
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(dest/'worker.log')],force=True)
    row=next(r for r in csv.DictReader(open(OUT/'catalog.csv')) if r['id']==fire)
    status={'fire':fire,'feds_id':row['feds_id'],'issue':ISSUE,'status':'building','member_set':MEMBER_SET,
            'members_planned':MEMBERS,'min_start_cells':MIN_START,'hours':HOURS,'init':INIT,'wx_mode':WX}
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
        wxdir=item['wx']
        if WX=='subhourly':
            sub=root/'wx-subh'/(ISSUE[:10].replace('-','')+'T'+ISSUE[11:13])
            try:
                if not (sub/'wx.json').exists():
                    run([*PYRUN,'scripts/hrrr_weather.py',str(root/'fire-state.tif'),ISSUE[:13],str(HOURS),str(sub),
                         'data/raw/hrrr_subh','--subhourly',f'--fm100={FM100_TIF}'],dest/'wx-subh.log')
                wxdir=str(sub);status['wx_used']=json.loads((sub/'wx.json').read_text())
                for k in ('mean_ws_mph','mean_m1_pct','mean_m10_pct'): status['wx_used'].pop(k,None)
            except (subprocess.CalledProcessError,OSError,ValueError) as e:
                status['wx_used']={'source':'hourly fallback','error':str(e)}
        status.update(status='running',grid_resolution_m=case['elmfire_cell_m'],hot_cells_before=hot,
                      data={'satellites':'S-NPP, NOAA-20, NOAA-21 when available','weather':item['wx'],
                            'hrrr_hours':item.get('hrrr_hours',24),'wx_dir':wxdir,'fuels':'LANDFIRE LF2025',
                            'terrain':'3DEP elevation/slope/aspect','cloud_masks':'matching available 03IMG granules only'})
        save()
        def member(name):
            folder=dest/'runs'/MEMBER_SET/name
            if (folder/'outputs'/'run.json').exists() and not os.environ.get('REBUILD'): return name,folder/'outputs'
            extra=[f'{k}={v}' for k,v in MEMBERS[name].items()]+([f'MIN_START_CELLS={MIN_START}'] if MIN_START else [])
            extra+=[f'INIT={INIT}'] if INIT!='hotcells' else []
            run(['bash','scripts/elmfire_forecast.sh',str(root/'landscape.tif'),wxdir,str(root/'fire-state.tif'),
                 ISSUE,str(HOURS),str(folder),'OBSERVED='+item['observed'],*extra,'LH_MOISTURE_CONTENT=90'],
                dest/(MEMBER_SET+'-'+name+'.log'))
            return name,folder/'outputs'
        workers=int(os.environ.get('WORKERS',os.environ.get('SLURM_CPUS_PER_TASK',4)))
        with cf.ThreadPoolExecutor(workers) as pool: completed=list(pool.map(member,MEMBERS))
        try:
            first=json.loads((dest/'runs'/MEMBER_SET/completed[0][0]/'case.json').read_text())
            status['min_start']=first.get('min_start');status['init_used']=first.get('init')
        except (OSError,ValueError): status['min_start']=None
        feds_start=(status.get('init_used') or {}).get('mode')=='feds'
        counts={h:None for h in HORIZONS};all_scores={};metadata=None;fixed=[]
        def score_run(folder,horizon):
            meta=json.loads((folder/'run.json').read_text())
            with rasterio.open(sorted(folder.glob('time_of_arrival_*.tif'))[-1]) as src:
                toa=src.read(1);valid=(src.read_masks(1)>0)&(toa>=0)&np.isfinite(toa)
                transform,crs,res=src.transform,src.crs,src.res
            project=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
            if fixed and fixed[0][0].shape==toa.shape:
                start,provenance=fixed[0]  # every run (incl. rescored baselines) scored from the first member's footprint
            else:
                if feds_start:  # the FEDS perimeter known at issue on this fine grid
                    gp=shapely.transform(shape(outline['geometry']),lambda xy:np.column_stack(project.transform(xy[:,0],xy[:,1])))
                    start=rasterize([(mapping(gp),1)],out_shape=toa.shape,transform=transform,dtype='uint8').astype(bool)
                    provenance={'start':'FEDS perimeter at issue on the fuel grid','latest_perimeter':outline['time']}
                else:
                    start,provenance=known_start(meta,toa.shape,transform,crs)
                start|=valid&(toa==0)
                fixed.append((start,provenance))
            observed=[]
            for p in obs['perimeters']:
                hours=(utc(p['time'])-utc(ISSUE)).total_seconds()/3600
                if not 0<hours<=horizon: continue
                if str(p['fireid'])!=row['feds_id']: raise ValueError('Perimeter fire identity mismatch')
                g=shapely.transform(shape(p['geometry']),lambda xy:np.column_stack(project.transform(xy[:,0],xy[:,1])))
                truth=rasterize([(mapping(g),1)],out_shape=toa.shape,transform=transform,dtype='uint8').astype(bool)
                observed.append({'hours':hours,'time':p['time'],**scores(start|(valid&(toa<=hours*3600)),truth,start,res)})
            return observed,toa,valid,start,res,transform,crs,provenance
        for name,folder in completed:
            observed,toa,valid,start,res,transform,crs,provenance=score_run(folder,HOURS)
            all_scores[name]=observed
            for h in counts:
                pred=start|(valid&(toa<=h*3600))
                counts[h]=pred.astype(np.uint16) if counts[h] is None else counts[h]+pred
            metadata=provenance
            if 'features' not in status:
                status['features']={**fire_features(root,toa,valid,start,res),**mean_wind(wxdir,HOURS)}
        baseline={};base_dirs=sorted((dest/'runs'/'fuelwind16').glob('*/outputs/run.json')) if PHYS else []
        for d in base_dirs:  # fuelwind16 runs (24 h) rescored at the same horizon, start footprint and truth
            try: baseline[d.parent.parent.name]=score_run(d.parent,HOURS)[0]
            except Exception as e: log.warning('baseline %s: %s',d,e)
        viirs={}
        try:  # heat_frames.py VIIRS detection CSI at overpasses within HOURS, new members and baseline runs
            dirs=[str(f) for _,f in completed]+[str(d.parent) for d in base_dirs]
            run([*PYRUN,'scripts/heat_frames.py',item['observed'],str(root/'fire-state.tif'),ISSUE,str(dest/'heat.json'),*dirs],
                dest/'heat.log')
            end=utc(ISSUE)+dt.timedelta(hours=HOURS)
            for r in json.loads((dest/'heat.json').read_text())['fires'][-1]['runs']:
                viirs[r['name']]=[{k:s.get(k) for k in ('time','csi','hits','misses','false_alarms','predicted','observed','obscured')}
                                  for s in r['scores'] if utc(s['time'])<=end]
            (dest/'heat.json').unlink()
        except Exception as e: log.warning('heat_frames: %s',e);viirs={'error':str(e)}
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
        (dest/'scores.json').write_text(json.dumps({'member_set':MEMBER_SET,'members':MEMBERS,'hours':HOURS,
            'init':status.get('init_used'),'features':status['features'],'scores':all_scores,
            'baseline_fuelwind16_rescored':baseline,'viirs':viirs},indent=2)+'\n')
        status.update(status='complete',members=len(completed),perimeter_observations=len(next(iter(all_scores.values()))),
                      scoring_note='FEDS satellite-derived outlines; not independent airborne ground truth')
        save();log.info('COMPLETE %s',fire)
    except Exception as e:
        status.update(status='failed',error=str(e));save();log.exception('FAILED %s',fire);raise

if __name__=='__main__': main(sys.argv[1])
