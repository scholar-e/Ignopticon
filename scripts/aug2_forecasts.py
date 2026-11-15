#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "rasterio", "shapely>=2.1", "pyproj", "pygrib"]
# ///
"""Ops-page ELMFIRE forecasts for the Aug 2 2026 12Z FEDS fires, run locally: the physics3 "adj1" member exactly
as perimeter_batch.py ran it on Hyak (INIT=feds, HRRR 12Z wrfsubhf 15-min wind via hrrr_weather.py --subhourly,
EMC m1 / lagged m10 / previous-day gridMET fm100, ADJ=1, LH 90, 12 h), then forecast_to_geojson.py (3,6,12 h bands
plus per-hour extents) into earth/public/data/forecasts. No scoring, envelopes or VIIRS CSI.

Skips fires already in earth/public/data/forecasts/index.json. Reuses data/training/fires/FIRE/ inputs; builds what
is missing with build_fire_case.py (--issue fixed, --feds-fire-id). HRRR 12Z hourly (f00-f24, for the build's
weather step) and sub-hourly (f00-f12) GRIB are fetched once up front and shared: hourly files are hard-linked into
each fire's build raw dir (the build deletes that dir, the shared copy stays).
Per-fire products and logs: data/perimeter_batch/aug2_local/FIRE/ (associated-observed.json, adj1/, *.log).
Status: data/perimeter_batch/aug2_local/status.json; DONE written at the end.
Usage: scripts/aug2_forecasts.py [FIRE ...]   (default: all of data/perimeter_batch/fires.txt)  env WORKERS=6
"""
import concurrent.futures as cf
import csv, datetime as dt, json, logging, os, subprocess, sys, threading
from pathlib import Path
import numpy as np
import rasterio
import shapely
from shapely.geometry import shape
from pyproj import Transformer

sys.path.insert(0, 'scripts')
from elmfire_perimeters import utc
import hrrr_weather

ISSUE = '2026-08-02T12:00'
HOURS = 12
OUT = Path('data/perimeter_batch/aug2_local')
FC = Path('earth/public/data/forecasts')
SNAP = Path('earth/public/data/fires/feds/2026/08')
FM100 = 'data/perimeter_batch/gridmet_fm100_20260801.tif'
HRRR_HOURLY = Path('data/raw/hrrr_aug2_12z')
HRRR_SUBH = 'data/raw/hrrr_subh'
MODEL = 'ELMFIRE physics (FEDS-perimeter start, HRRR 12Z 15-min wind, HRRR/gridMET fuel moisture, no tuning)'
PY = (os.environ.get('PY') or 'uv run --script').split()
log = logging.getLogger('aug2')
lock = threading.Lock()
status = {}


class Skip(Exception):
    def __init__(self, kind, detail=''):
        super().__init__(detail); self.kind = kind; self.detail = detail


def save():
    with lock:
        (OUT/'status.json').write_text(json.dumps(status, indent=2)+'\n')


def run(cmd, logfile):
    with open(logfile, 'a') as f:
        subprocess.run([str(c) for c in cmd], stdout=f, stderr=subprocess.STDOUT, check=True)


def snapshot_place(feds_id):
    for name in ('02-1200.json', '02-0000.json'):
        d = json.loads((SNAP/name).read_text())
        for f in d['features']:
            if str(f['properties'].get('id')) == str(feds_id):
                return SNAP/name, f['properties'].get('place') or f'FEDS {feds_id}'
    return None, f'FEDS {feds_id}'


def one(fire, feds_id):
    st = status[fire]; dest = OUT/fire; dest.mkdir(parents=True, exist_ok=True)
    root = Path('data/training/fires')/fire
    def built():
        try:
            c = json.loads((root/'case.json').read_text())
            return c.get('ready') and any(i['issue'] == ISSUE and Path(i['wx'], 'ws.tif').exists() for i in c['issues'])
        except (OSError, ValueError, KeyError, TypeError): return False
    if not built():
        st['stage'] = 'build'; save()
        raw = Path('data/training/raw/hrrr')/fire; raw.mkdir(parents=True, exist_ok=True)
        for g in HRRR_HOURLY.glob('*.grb2'):
            if not (raw/g.name).exists(): os.link(g, raw/g.name)
        try:
            run([*PY, 'scripts/build_fire_case.py', fire, '--catalog', 'data/perimeter_batch/catalog.csv',
                 '--issue', ISSUE, '--feds-fire-id', feds_id], dest/'build.log')
        except subprocess.CalledProcessError: raise Skip('build failed', 'build_fire_case.py; see build.log')
        st['inputs'] = 'built'
        if not built(): raise Skip('build failed', 'case not ready after build')
    else: st['inputs'] = 'reused'
    case = json.loads((root/'case.json').read_text())
    if case.get('truncated'): raise Skip('build failed', 'bounding box truncated')
    item = next(i for i in case['issues'] if i['issue'] == ISSUE)
    # Associate initial heat with this fire (3 km around the latest FEDS perimeter at issue), as perimeter_batch.py.
    obs = json.loads(Path(item['observed']).read_text())
    known = [p for p in obs['perimeters'] if utc(p['time']) <= utc(ISSUE)]
    if not known: raise Skip('no FEDS start', 'no fire-specific FEDS perimeter at issue')
    outline = max(known, key=lambda p: utc(p['time']))
    with rasterio.open(root/'fire-state.tif') as src: crs = src.crs
    pr = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    g = shapely.transform(shape(outline['geometry']), lambda xy: np.column_stack(pr.transform(xy[:, 0], xy[:, 1]))).buffer(3000)
    for f in obs['frames']:
        if utc(f['time']) > utc(ISSUE): continue
        f['cells'] = [c for c in f['cells'] if g.covers(shapely.Point(*pr.transform(c[0], c[1])))]
    hot = sum(len(f['cells']) for f in obs['frames'] if utc(ISSUE)-dt.timedelta(hours=6) <= utc(f['time']) <= utc(ISSUE))
    st['hot_cells_before'] = hot; st['latest_perimeter'] = outline['time']
    if hot == 0: raise Skip('no FEDS start', 'no associated VIIRS hot cells in 6 h before issue')
    assoc = dest/'associated-observed.json'; assoc.write_text(json.dumps(obs, separators=(',', ':')))
    st['stage'] = 'weather'; save()
    sub = root/'wx-subh'/'20260802T12'
    if not (sub/'wx.json').exists():
        run([*PY, 'scripts/hrrr_weather.py', root/'fire-state.tif', ISSUE[:13], HOURS, sub, HRRR_SUBH,
             '--subhourly', f'--fm100={FM100}'], dest/'wx-subh.log')
    st['stage'] = 'elmfire'; save()
    folder = dest/'adj1'
    if not (folder/'outputs'/'run.json').exists():
        try:
            run(['bash', 'scripts/elmfire_forecast.sh', root/'landscape.tif', sub, root/'fire-state.tif', ISSUE, HOURS,
                 folder, f'OBSERVED={assoc}', 'ADJ=1.0', 'INIT=feds', 'LH_MOISTURE_CONTENT=90'], dest/'elmfire.log')
        except subprocess.CalledProcessError: raise Skip('elmfire failed', 'see elmfire.log')
    try: st['init_used'] = json.loads((folder/'case.json').read_text()).get('init')
    except (OSError, ValueError): pass
    toa_file = sorted((folder/'outputs').glob('time_of_arrival_*.tif'))[-1]
    with rasterio.open(toa_file) as src:
        toa = src.read(1); ok = (src.read_masks(1) > 0) & np.isfinite(toa)
    grown = int((ok & (toa > 0)).sum()); st['grown_cells'] = grown
    perim, name = snapshot_place(feds_id)
    if feds_id == '66510': name = 'Widemouth 2'
    st.update(stage='convert', name=name, perimeter_snapshot=str(perim)); save()
    cmd = [*PY, 'scripts/forecast_to_geojson.py', folder/'outputs', root/'fire-state.tif', ISSUE, feds_id, name, MODEL,
           FC, '3,6,12'] + (['--perimeter', perim] if perim else [])
    with lock:  # forecast_to_geojson.py rewrites index.json
        run(cmd, dest/'convert.log')
    return 'ok' if grown else 'no growth'


def worker(fire, feds_id):
    try: s = one(fire, feds_id); status[fire].update(status=s)
    except Skip as e: status[fire].update(status=e.kind, detail=e.detail)
    except Exception as e: status[fire].update(status='failed', detail=repr(e)); log.exception(fire)
    status[fire]['finished'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    log.info('%s %s', fire, status[fire]['status']); save()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(OUT/'driver.log')], force=True)
    os.environ['FEDS_SNAPSHOT_DIR'] = str(SNAP)
    (OUT/'DONE').unlink(missing_ok=True)
    cat = {r['id']: r['feds_id'] for r in csv.DictReader(open('data/perimeter_batch/catalog.csv'))}
    done = {str(x['fire_id']) for x in json.loads((FC/'index.json').read_text())['forecasts']
            if x.get('issued', '').startswith('2026-08-02T12')}
    fires = sys.argv[1:] or Path('data/perimeter_batch/fires.txt').read_text().split()
    if (OUT/'status.json').exists(): status.update(json.loads((OUT/'status.json').read_text()))
    todo = []
    for f in fires:
        if cat[f] in done and status.get(f, {}).get('status') not in ('ok', 'no growth'):
            status[f] = {'feds_id': cat[f], 'status': 'existing forecast (skipped)'}
        elif cat[f] not in done:
            status[f] = {'feds_id': cat[f], 'status': 'queued'}; todo.append(f)
    save()
    run_t = dt.datetime(2026, 8, 2, 12)
    HRRR_HOURLY.mkdir(parents=True, exist_ok=True); os.makedirs(HRRR_SUBH, exist_ok=True)
    with cf.ThreadPoolExecutor(8) as pool:  # shared HRRR, once
        list(pool.map(lambda h: hrrr_weather.download(run_t, h, str(HRRR_HOURLY)), range(25)))
        list(pool.map(lambda h: hrrr_weather.download_subhourly(run_t, h, HRRR_SUBH), range(HOURS+1)))
    log.info('HRRR cached; %d fires to run', len(todo))
    with cf.ThreadPoolExecutor(int(os.environ.get('WORKERS', 6))) as pool:
        list(pool.map(lambda f: worker(f, cat[f]), todo))
    counts = {}
    for v in status.values(): counts[v['status']] = counts.get(v['status'], 0)+1
    (OUT/'DONE').write_text(json.dumps(counts)+'\n'); log.info('DONE %s', counts)


if __name__ == '__main__':
    main()
