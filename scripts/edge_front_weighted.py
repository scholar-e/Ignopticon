#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm", "rasterio", "pyproj", "scipy", "shapely>=2.1", "matplotlib"]
# ///
"""Weighted learned front (FRONT_PMODE=weighted) experiments under data/edge_causal/front_weighted.

    edge_front_weighted.py verify     raw daily VNP14A1/weather window == tensor inputs (2021 days of the dev fires)
    edge_front_weighted.py dev        3 development fires: control, binary, binary_pos, weighted (same inputs)
    edge_front_weighted.py manifest   freeze the held-out 2026-08-02T12 manifest (pre-issue selection) BEFORE runs
    edge_front_weighted.py heldout    landscape + HRRR weather once per manifest fire, then all arms
    edge_front_weighted.py band       outward-band arms (manifest band_variant) on dev + held-out fires, reusing the
                                      existing probability rasters, landscapes and weather

Edge features come from the previous complete UTC day, read straight from data/harmonize/grid/VNP14A1_YEAR.npz and
data/harmonize/weather/weather_YEAR.npz with the tensor conventions (fire > 0, clear > 0, gridMET day row), so 2026
needs no tensor year. Probabilities: mean of three harm-all seeds, copied to fuel cells whose centre lies in the
0.1-degree footprint (no interpolation; other cells nodata).
"""
import datetime as dt
import hashlib
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

ROOT = Path('data/edge_causal/front_weighted')
MODELS = [f'data/edge_causal/runs/harm-all-seed{s}/models/harm-all.txt' for s in range(3)]
RESULTS = [f'data/edge_causal/runs/harm-all-seed{s}/results.json' for s in range(3)]
WVARS = ['wind', 'u', 'v', 'vpd', 'erc', 'fm100', 'pr']
ISSUE = dt.datetime(2026, 8, 2, 12)
FM100 = 'data/perimeter_batch/gridmet_fm100_20260801.tif'
PHYSICS = ['ADJ=1.0', 'INIT=feds', 'LH_MOISTURE_CONTENT=90']
log = logging.getLogger(__name__)


def setup(name):
    ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / f'{name}.log')], force=True)


def threshold():
    return float(np.mean([json.loads(Path(p).read_text())['arms']['harm-all']['threshold'] for p in RESULTS]))


def arms(pfile):
    """Arm -> extra overrides. binary_pos is a diagnostic: weighted's exact ignition set at multiplier 1."""
    return {'control': [], 'binary': ['FRONT_PFILE=' + pfile, 'FRONT_PTHRESH=%r' % threshold()],
            'binary_pos': ['FRONT_PFILE=' + pfile, 'FRONT_PTHRESH=1e-9'],
            'weighted': ['FRONT_PMODE=weighted', 'FRONT_PFILE=' + pfile]}


def raw_window(day):
    """(history (31,260,590) fire presence t-30..t, weather (7,260,590) on t, clear (260,590) on t) for date day."""
    d = day.timetuple().tm_yday - 1
    if d < 30:
        raise ValueError('cross-year history not implemented')
    z = np.load(f'data/harmonize/grid/VNP14A1_{day.year}.npz')
    index = {int(k) - 1: i for i, k in enumerate(z['day'])}
    history = np.zeros((31, 260, 590), np.float32)
    fire = z['fire']
    for j, k in enumerate(range(d - 30, d + 1)):
        if k in index:
            history[j] = fire[index[k]] > 0
    clear = (z['clear'][index[d]] > 0) if d in index else np.zeros((260, 590), bool)
    w = np.load(f'data/harmonize/weather/weather_{day.year}.npz')
    weather = np.stack([w[k][d] for k in WVARS]).astype(np.float16).astype(np.float32)
    return history, weather, clear


def tensor_window(day):
    d = day.timetuple().tm_yday - 1
    z = np.load(f'data/compare/tensors/viirs/{day.year}.npz')
    f = z['x'][:, 0].astype(np.float32)
    history = f[d - 30:d + 1]
    w = np.load(f'data/compare/tensors/weather/{day.year}.npz')['w'][d].astype(np.float32)
    return history, w, z['x'][d, 10] > 0


def predict(day, models, S):
    import edge_dataset as E
    history, w, clear = raw_window(day)
    d = day.timetuple().tm_yday - 1
    ndays = 366 if day.year % 4 == 0 else 365
    X, r, c, *_ = E.day_rows(history, w, w, S, 1e-6, clear_t=clear, doy=d + 1, ndays=ndays)
    p = np.mean([m.predict(X[:, [E.FEATS.index(n) for n in m.feature_name()]], num_threads=8) for m in models], axis=0)
    grid = np.full((E.NY, E.NX), -9999, np.float32)
    grid[r, c] = p
    return grid, len(r)


def write_probability(grid, landscape, out, day):
    import rasterio
    from pyproj import Transformer
    with rasterio.open(landscape) as src:
        profile = src.profile.copy()
        rows, cols = np.indices(src.shape)
        xx, yy = rasterio.transform.xy(src.transform, rows, cols)
        lon, lat = Transformer.from_crs(src.crs, 4326, always_xy=True).transform(xx, yy)
        rr = np.floor((50 - np.asarray(lat).reshape(src.shape)) / .1).astype(int)
        cc = np.floor((np.asarray(lon).reshape(src.shape) + 125) / .1).astype(int)
        valid = (rr >= 0) & (rr < grid.shape[0]) & (cc >= 0) & (cc < grid.shape[1])
        fine = np.full(src.shape, -9999, np.float32)
        fine[valid] = grid[rr[valid], cc[valid]]
    profile.update(count=1, dtype='float32', nodata=-9999, compress='deflate')
    with rasterio.open(out, 'w', **profile) as dst:
        dst.write(fine, 1)
        dst.update_tags(model_identifiers=json.dumps([f'harm-all-seed{s}' for s in range(3)]),
                        model_sha256=json.dumps([sha(m) for m in MODELS]), feature_date=str(day),
                        binary_threshold=str(threshold()), coarse_resolution_degrees='0.1',
                        inputs='VNP14A1 daily grid + gridMET, read directly from data/harmonize',
                        mapping='fuel cell centre within coarse candidate footprint; no interpolation')
    return int((fine != -9999).sum())


def band_arms(pfile, m):
    v = m['band_variant']
    return {'band%d' % b: ['ADJ_BAND_PFILE=' + pfile, 'ADJ_BAND_M=%d' % b, 'ADJ_BAND_PREF=%r' % v['p_ref'],
                           'ADJ_BAND_FLOOR=%r' % v['floor'], 'ADJ_BAND_CAP=%r' % v['cap']] for b in v['band_m']}


def band():
    setup('band')
    from front_calibrate import FIRES, F
    m = json.loads((ROOT / 'manifest.json').read_text())
    jobs = []
    for fire in ['2021-038', '2021-031', '2020-008']:
        cfg, out = FIRES[fire], ROOT / 'dev' / fire
        pfile = str((out / 'edge_probability.tif').resolve())
        for a, x in band_arms(pfile, m).items():
            jobs.append((fire, a, (str(Path(F) / fire / 'landscape.tif'), cfg['fc'][0], cfg['fc'][1],
                                   str(Path(F) / fire / 'fire-state.tif'), cfg['issue'], out / a, cfg['observed'], x)))
    for f in m['fires']:
        d, out = Path('data/training/fires') / f['fire'], ROOT / 'heldout' / f['fire']
        pfile = str((out / 'edge_probability.tif').resolve())
        for a, x in band_arms(pfile, m).items():
            jobs.append((f['fire'], a, (str(d / 'landscape.tif'), str(out / 'wx'), 0, str(d / 'fire-state.tif'),
                                        '2026-08-02T12:00', out / a, f['observed'], x)))
    status = {}
    with ThreadPoolExecutor(int(os.environ.get('JOBS', 4))) as ex:
        futs = {(fire, a): ex.submit(run_case, *args) for fire, a, args in jobs}
        for k, j in futs.items():
            try:
                j.result()
                status['/'.join(k)] = 'complete'
            except Exception as e:  # recorded, not dropped
                status['/'.join(k)] = 'failed: ' + str(e)[-1500:]
            log.info('%s: %s', k, status['/'.join(k)])
    (ROOT / 'band_status.json').write_text(json.dumps(status, indent=1) + '\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_case(landscape, wx, wxstart, state, issue, case, observed, extra):
    """One ELMFIRE forecast in a fresh case directory (refuses to reuse an existing one)."""
    case = Path(case)
    if (case / 'outputs' / 'run.json').exists():
        prev = json.loads((case / 'case.json').read_text())['overrides']
        want = ['OBSERVED=' + observed, *PHYSICS, 'WXSTART=%d' % wxstart, *extra]
        if prev != want:
            raise RuntimeError(f'{case}: existing case has different overrides')
        return case / 'outputs'
    r = subprocess.run(['bash', 'scripts/elmfire_forecast.sh', landscape, wx, state, issue, '12', str(case),
                        'OBSERVED=' + observed, *PHYSICS, 'WXSTART=%d' % wxstart, *extra],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('%s: %s %s' % (case, r.stdout[-2000:], r.stderr[-2000:]))
    return case / 'outputs'


def verify():
    setup('verify')
    from front_calibrate import FIRES, utc
    out = {}
    for fire in ['2021-038', '2021-031', '2020-008']:
        day = (utc(FIRES[fire]['issue']) - dt.timedelta(days=1)).date()
        a, b = raw_window(day), tensor_window(day)
        out[fire] = {'day': str(day), 'history_equal': bool(np.array_equal(a[0], b[0])),
                     'weather_equal': bool(np.array_equal(np.nan_to_num(a[1], nan=-1), np.nan_to_num(b[1], nan=-1))),
                     'clear_equal': bool(np.array_equal(a[2], b[2]))}
        log.info('%s', out[fire])
    day = dt.date(2026, 8, 1)
    h, w, c = raw_window(day)
    out['2026-08-01'] = {'fire_cells': int(h[-1].sum()), 'clear_cells': int(c.sum()),
                         'weather_finite_fraction': float(np.isfinite(w).mean())}
    log.info('%s', out['2026-08-01'])
    (ROOT / 'verify.json').write_text(json.dumps(out, indent=1) + '\n')


def dev():
    setup('dev')
    import lightgbm as lgb
    import edge_dataset as E
    from front_calibrate import FIRES, utc, F
    models, S = [lgb.Booster(model_file=m) for m in MODELS], E.static_grid()
    summary = {}
    for fire in ['2021-038', '2021-031', '2020-008']:
        cfg = FIRES[fire]
        issue = utc(cfg['issue'])
        day = (issue - dt.timedelta(days=1)).date()
        out = ROOT / 'dev' / fire
        out.mkdir(parents=True, exist_ok=True)
        landscape = str(Path(F) / fire / 'landscape.tif')
        grid, n = predict(day, models, S)
        pfile = str((out / 'edge_probability.tif').resolve())
        covered = write_probability(grid, landscape, pfile, day)
        with ThreadPoolExecutor(4) as ex:
            jobs = {a: ex.submit(run_case, landscape, cfg['fc'][0], cfg['fc'][1], str(Path(F) / fire / 'fire-state.tif'),
                                 cfg['issue'], out / a, cfg['observed'], x) for a, x in arms(pfile).items()}
            [j.result() for j in jobs.values()]
        summary[fire] = {'feature_day': str(day), 'issue': cfg['issue'], 'candidate_cells': n,
                         'covered_fuel_cells': covered, 'binary_threshold': threshold()}
        log.info('%s: %s', fire, summary[fire])
    (ROOT / 'dev' / 'pilot.json').write_text(json.dumps(summary, indent=2) + '\n')


REGIONS = [('pacific_northwest', lambda lo, la: lo < -116 and la >= 42),
           ('northern_rockies', lambda lo, la: -116 <= lo < -104 and la >= 42),
           ('great_basin_california', lambda lo, la: lo < -104 and la < 42),
           ('southern_plains', lambda lo, la: -104 <= lo < -94 and la < 37),
           ('north_plains_midwest', lambda lo, la: lo >= -104 and la >= 37),
           ('southeast', lambda lo, la: lo >= -94 and la < 37)]


def observed_file(fire):
    a = Path('data/perimeter_batch') / fire / 'associated-observed.json'
    return a if a.exists() else Path('data/training/fires') / fire / 'fire-observed.json'


def manifest():
    """Pre-issue selection. Uses only: centroid, FEDS perimeters at or before issue, VIIRS fire cells in the 6 h
    before issue, plus availability (not value) of the +12 h FEDS perimeter needed to score."""
    setup('manifest')
    path = ROOT / 'manifest.json'
    if path.exists():
        sys.exit(f'{path} exists: the manifest is frozen')
    u = lambda s: dt.datetime.fromisoformat(s.replace('Z', ''))
    cands, excluded = [], {}
    for d in sorted(Path('data/training/fires').glob('map20260802-*')):
        fire = d.name
        if fire == 'map20260802-66510':
            excluded[fire] = 'prior development case (selfcal/front calibration)'
            continue
        obs_path = observed_file(fire)
        if not (d / 'fire-state.tif').exists() or not obs_path.exists() or not (d / 'case.json').exists():
            excluded[fire] = 'missing state/observations/case.json'
            continue
        obs = json.loads(obs_path.read_text())
        ref, times = u(obs['reference']), [u(p['time']) for p in obs['perimeters']]
        hot6 = sum(len(f['cells']) for f in obs['frames']
                   if ISSUE - dt.timedelta(hours=6) <= ref + dt.timedelta(seconds=f['seconds']) <= ISSUE)
        if not any(t <= ISSUE for t in times):
            excluded[fire] = 'no FEDS perimeter at or before issue'
        elif ISSUE + dt.timedelta(hours=12) not in times:
            excluded[fire] = 'no FEDS perimeter exactly at issue + 12 h (cannot score)'
        elif hot6 < 5:
            excluded[fire] = 'fewer than 5 VIIRS fire cells in the 6 h before issue'
        else:
            c = json.loads((d / 'case.json').read_text())
            region = next(n for n, f in REGIONS if f(c['lon'], c['lat']))
            cands.append(dict(fire=fire, lon=c['lon'], lat=c['lat'], region=region, hot_cells_6h=hot6,
                              radius_km=c.get('radius_km'), observed=str(obs_path)))
    rank = sorted(cands, key=lambda x: (-x['hot_cells_6h'], x['fire']))
    chosen = []
    for name, _ in REGIONS:
        chosen += [x for x in rank if x['region'] == name][:2]
    chosen += [x for x in rank if x not in chosen][:max(0, 12 - len(chosen))]
    for x in cands:
        if x not in chosen:
            excluded[x['fire']] = 'eligible, not selected (region quota / rank)'
    m = {'created': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
         'purpose': 'held-out evaluation of learned front activation/weighting; frozen before any held-out run',
         'issue': ISSUE.isoformat(timespec='minutes'), 'horizon_h': 12, 'truth': 'FEDS perimeter at issue + 12 h',
         'models': [{'file': f, 'sha256': sha(f)} for f in MODELS],
         'feature_day': '2026-08-01 (previous complete UTC day)',
         'feature_inputs': ['data/harmonize/grid/VNP14A1_2026.npz', 'data/harmonize/weather/weather_2026.npz'],
         'feature_inputs_sha256': {p: sha(p) for p in ['data/harmonize/grid/VNP14A1_2026.npz',
                                                        'data/harmonize/weather/weather_2026.npz']},
         'binary_threshold': threshold(),
         'weighting_rule': 'FRONT_PMODE=weighted: covered inner-edge cells without recent hotspot support start '
                           'burning if p > 0 with spread multiplier max(p, 0.01); hotspot-supported, uncovered '
                           'and outside heat cells x1; p = 0 not ignited; empty selection -> whole edge x1',
         'arms': {'no_growth': 'initial FEDS perimeter as forecast', 'control': PHYSICS,
                  'binary': 'control + FRONT_PFILE + FRONT_PTHRESH=binary_threshold',
                  'weighted': 'control + FRONT_PMODE=weighted + FRONT_PFILE',
                  'binary_pos': 'diagnostic only: control + FRONT_PFILE + FRONT_PTHRESH=1e-9 (weighted ignition, x1)'},
         'weather': 'HRRR 2026-08-02T12 sub-hourly 12 h, rebuilt once per fire (hrrr_weather.py --subhourly '
                    f'--fm100={FM100}); identical for all arms',
         'landscape': 'LANDFIRE LF2025 + 3DEP (landscape_download.py, refine from case.json)',
         'selection': 'eligible = FEDS perimeter <= issue, FEDS perimeter exactly at issue+12h exists, >= 5 VIIRS '
                      'fire cells in 6 h before issue, not 66510; up to 2 per region ranked by pre-issue 6 h '
                      'detections, filled to 12 by the same rank',
         'metrics': ['growth_iou', 'iou', 'area_ratio', 'boundary_mean_m', 'false_growth_fraction',
                     'hotspot_hits/targets (post-issue VIIRS outside start perimeter, one 375 m cell tolerance)'],
         'aggregation': 'per-fire table; median and mean over fires completing all arms; failures listed',
         'status': 'held out from learned-model training (2003-2019) and from front-weighting development; the '
                   '2026-08-02 date was used for earlier physics-only batch evaluation',
         'fires': chosen, 'excluded': excluded}
    path.write_text(json.dumps(m, indent=1) + '\n')
    log.info('manifest: %d fires: %s', len(chosen), [(x['fire'], x['region']) for x in chosen])


def heldout_fire(x, models, S, grid):
    fire = x['fire']
    d = Path('data/training/fires') / fire
    out = ROOT / 'heldout' / fire
    out.mkdir(parents=True, exist_ok=True)
    landscape = d / 'landscape.tif'
    if not landscape.exists():
        refine = json.loads((d / 'case.json').read_text()).get('refine', 10)
        r = subprocess.run(['scripts/landscape_download.py', str(d / 'fire-state.tif'), str(refine), str(landscape)],
                           capture_output=True, text=True)
        (out / 'landscape.log').write_text(r.stdout[-4000:] + r.stderr[-4000:])
        if r.returncode:
            raise RuntimeError(f'{fire}: landscape download failed')
    wx = out / 'wx'
    if not (wx / 'wx.json').exists():
        r = subprocess.run(['scripts/hrrr_weather.py', str(d / 'fire-state.tif'), '2026-08-02T12', '12', str(wx),
                            'data/raw/hrrr', '--subhourly', '--fm100=' + FM100], capture_output=True, text=True)
        (out / 'wx.log').write_text(r.stdout[-4000:] + r.stderr[-4000:])
        if r.returncode:
            raise RuntimeError(f'{fire}: weather failed')
    pfile = str((out / 'edge_probability.tif').resolve())
    covered = write_probability(grid, str(landscape), pfile, dt.date(2026, 8, 1))
    for a, extra in arms(pfile).items():
        run_case(str(landscape), str(wx), 0, str(d / 'fire-state.tif'), '2026-08-02T12:00', out / a, x['observed'], extra)
    return {'covered_fuel_cells': covered}


def heldout():
    setup('heldout')
    import lightgbm as lgb
    import edge_dataset as E
    m = json.loads((ROOT / 'manifest.json').read_text())
    for f in m['models']:
        if sha(f['file']) != f['sha256']:
            raise RuntimeError(f'model changed since manifest: {f}')
    models, S = [lgb.Booster(model_file=f['file']) for f in m['models']], E.static_grid()
    grid, n = predict(dt.date(2026, 8, 1), models, S)
    log.info('2026-08-01 edge candidates: %d', n)
    status = {}
    with ThreadPoolExecutor(int(os.environ.get('JOBS', 4))) as ex:
        jobs = {x['fire']: ex.submit(heldout_fire, x, models, S, grid) for x in m['fires']}
        for fire, j in jobs.items():
            try:
                status[fire] = {'status': 'complete'} | j.result()
            except Exception as e:  # recorded as a failure, not dropped
                status[fire] = {'status': 'failed', 'error': str(e)[-1500:]}
            log.info('%s: %s', fire, status[fire])
    (ROOT / 'heldout' / 'status.json').write_text(json.dumps({'candidates': n, 'fires': status}, indent=1) + '\n')


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    {'verify': verify, 'dev': dev, 'manifest': manifest, 'heldout': heldout, 'band': band}[sys.argv[1]]()
