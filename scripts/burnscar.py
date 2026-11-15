#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "pyproj", "scipy", "shapely>=2.1", "matplotlib"]
# ///
"""Burn-scar fuel correction experiment (data/burnscar). ELMFIRE should not spread freely into ground that burned
recently if LANDFIRE fuels may not yet reflect it (elmfire_case.py BURNSCAR_FILE / BURNSCAR_YEARS / BURNSCAR_MULT).

    burnscar.py rasters   years-since-last-fire GeoTIFFs (0.1 deg, EPSG:4326, nodata -1) per arm x issue date
    burnscar.py dev       3 dev fires + Widemouth 66510 (harm arm): control + N in {1,2,3,5} x m in {0, 0.3}
    burnscar.py select    pick N, m by the rule in this docstring, freeze data/burnscar/manifest.json
    burnscar.py heldout   frozen N, m with harm / raw / viirs-only histories on the 12 held-out fires
    burnscar.py score     edge_pilot_score.py + edge_heldout_table.py on data/burnscar/heldout

Burn history. Fire in a 0.1 degree cell on a day: before 2012, harm = strict p_viirs >= 0.5
(data/harmonize/out-base-strict/harmonized_YEAR.npz), raw = MODIS Terra/Aqua presence (modis_fire > 0, same files),
viirs-only = nothing; 2012 on, every arm = VNP14A1 fire > 0 (data/harmonize/grid). From 2003 to the day before the
issue date (never the issue day). Raster value = (issue date - last fire day) / 365.25 years; -1 = no fire since 2003.
A whole 0.1 degree cell (~85 km2) counts as burned if any pixel burned in it: coarse and over-inclusive. A finer
variant from VIIRS 375 m detections is not built: data/raw/viirs_fire holds only 2026 days 211-218.
Because VIIRS covers every year from 2012, the three arms are identical for any window that starts after 2011
(N <= 5 for issue years 2020+): the harm / raw / viirs-only difference can only appear for N > issue year - 2012.

Selection rule (dev only, harm arm): highest mean growth IoU over the 4 dev fires; combos within 0.002 of the best
are ties, broken toward the smaller perturbation (larger m, then smaller N). If no combo beats control's mean growth
IoU by more than 0.002 the rule still freezes the tie-break choice, and the manifest says so (no_dev_benefit).
"""
import datetime as dt
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

ROOT = Path('data/burnscar')
FW = Path('data/edge_causal/front_weighted')
HARM = Path('data/harmonize/out-base-strict')
GRID = Path('data/harmonize/grid')
ARMS = ['harm', 'raw', 'viirs_only']
NS, MS = [1, 2, 3, 5], [0.0, 0.3]
log = logging.getLogger(__name__)


def setup(name):
    ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / f'{name}.log')], force=True)


def dev_fires():
    from front_calibrate import FIRES
    return {k: FIRES[k] for k in ['2021-038', '2021-031', '2020-008', 'map20260802-66510']}


def issue_dates():
    ds = {dt.datetime.fromisoformat(c['issue']).date() for c in dev_fires().values()}
    ds |= {dt.date(2026, 8, 2)}
    return sorted(ds)


def day_fire(arm, year):
    """(ordinal day numbers (D,), fire bool (D, 260, 590)) or None when the arm has nothing that year."""
    if year >= 2012:
        z = np.load(GRID / f'VNP14A1_{year}.npz')
        days = np.array([dt.date(year, 1, 1).toordinal() + int(d) - 1 for d in z['day']])
        return days, z['fire'] > 0
    if arm == 'viirs_only' or year < 2003:
        return None
    z = np.load(HARM / f'harmonized_{year}.npz')
    days = dt.date(year, 1, 1).toordinal() + np.arange(z['p_viirs'].shape[0])
    return days, (z['p_viirs'].astype(np.float32) >= 0.5) if arm == 'harm' else z['modis_fire'] > 0


def last_fire(arm, issues):
    """{issue date: last fire ordinal day (260, 590) int32, 0 = none} using days strictly before the issue date."""
    last, out, applied = np.zeros((260, 590), np.int32), {}, 0
    for year in range(2003, max(issues).year + 1):
        got = day_fire(arm, year)
        if got is None:
            continue
        days, fire = got
        for i in sorted(d for d in issues if d.year == year) + [None]:
            lim = i.toordinal() if i else 10 ** 9
            for k in np.nonzero((days < lim) & (days > applied))[0]:   # days are ascending
                np.maximum(last, np.where(fire[k], days[k], 0).astype(np.int32), out=last)
                applied = int(days[k])
            if i:
                out[i] = last.copy()
        log.info('%s %d', arm, year)
    return out


def write_tif(path, last, issue):
    import rasterio
    from rasterio.transform import from_origin
    years = np.where(last > 0, (issue.toordinal() - last) / 365.25, -1).astype(np.float32)
    with rasterio.open(path, 'w', driver='GTiff', height=260, width=590, count=1, dtype='float32', crs='EPSG:4326',
                       transform=from_origin(-125, 50, .1, .1), nodata=-1, compress='deflate') as dst:
        dst.write(years, 1)
        dst.update_tags(issue=str(issue), fire_days_before_issue_only='true')
    return years


def rasters():
    setup('rasters')
    out = ROOT / 'rasters'
    out.mkdir(parents=True, exist_ok=True)
    issues = issue_dates()
    stats, arrays = {}, {}
    for arm in ARMS:
        for issue, last in last_fire(arm, issues).items():
            y = write_tif(out / f'{arm}_{issue}.tif', last, issue)
            arrays[arm, issue] = y
            stats[f'{arm}_{issue}'] = {f'cells_within_{n}y': int(((y >= 0) & (y <= n)).sum()) for n in (1, 2, 3, 5, 10, 23)}
            log.info('%s %s %s', arm, issue, stats[f'{arm}_{issue}'])
    diff = {}
    for issue in issues:
        for n in (5, 10, 15, 23):
            for a, b in [('harm', 'raw'), ('harm', 'viirs_only'), ('raw', 'viirs_only')]:
                ma = (arrays[a, issue] >= 0) & (arrays[a, issue] <= n)
                mb = (arrays[b, issue] >= 0) & (arrays[b, issue] <= n)
                diff[f'{issue} N={n} {a} vs {b}'] = int((ma != mb).sum())
    (ROOT / 'rasters.json').write_text(json.dumps({'issues': [str(i) for i in issues], 'stats': stats,
                                                    'cells_differing_in_window': diff}, indent=1) + '\n')


def extra(arm, issue, n, m):
    return ['BURNSCAR_FILE=' + str((ROOT / 'rasters' / f'{arm}_{issue}.tif').resolve()),
            f'BURNSCAR_YEARS={n}', f'BURNSCAR_MULT={m}']


def combo(n, m):
    return f'N{n}_m{m}'


def dev_jobs():
    from front_calibrate import F
    jobs = []
    for fire, cfg in dev_fires().items():
        issue = dt.datetime.fromisoformat(cfg['issue']).date()
        base = (F + fire + '/landscape.tif', cfg['fc'][0], cfg['fc'][1], F + fire + '/fire-state.tif', cfg['issue'])
        out = ROOT / 'dev' / fire
        jobs.append((fire, 'control', (*base, out / 'control', cfg['observed'], [])))
        for n in NS:
            for m in MS:
                jobs.append((fire, combo(n, m), (*base, out / combo(n, m), cfg['observed'], extra('harm', issue, n, m))))
    return jobs


def run_all(jobs, name):
    from edge_front_weighted import run_case
    status = {}
    with ThreadPoolExecutor(int(os.environ.get('JOBS', 8))) as ex:
        futs = {(f, a): ex.submit(run_case, *args) for f, a, args in jobs}
        for k, j in futs.items():
            try:
                j.result()
                status['/'.join(k)] = 'complete'
            except Exception as e:  # recorded, not dropped
                status['/'.join(k)] = 'failed: ' + str(e)[-1500:]
                log.error('%s: %s', k, status['/'.join(k)])
    return status


def dev():
    setup('dev')
    status = run_all(dev_jobs(), 'dev')
    (ROOT / 'dev_status.json').write_text(json.dumps(status, indent=1) + '\n')
    (ROOT / 'DEV_DONE').write_text('done\n')


def score_dev():
    from physics_mw import score_root
    from front_calibrate import F
    fires = {f: (c['issue'], c['observed'], F + f + '/fire-state.tif') for f, c in dev_fires().items()}
    return score_root(ROOT / 'dev', fires, ['control'] + [combo(n, m) for n in NS for m in MS])


def select():
    setup('select')
    path = ROOT / 'manifest.json'
    if path.exists():
        sys.exit(f'{path} exists: frozen')
    res = score_dev()
    fires = list(res)
    names = ['control'] + [combo(n, m) for n in NS for m in MS]
    table = {}
    for a in names:
        if not all(a in res[f] for f in fires):
            continue
        g = [res[f][a]['growth_iou'] or 0 for f in fires]
        table[a] = {'mean_growth_iou': float(np.mean(g)),
                    'mean_iou': float(np.mean([res[f][a]['iou'] for f in fires])),
                    'per_fire': {f: {k: res[f][a][k] for k in ('growth_iou', 'iou', 'area_ratio', 'false_growth_fraction')}
                                 for f in fires}}
        info = [json.loads((ROOT / 'dev' / f / a / 'case.json').read_text()).get('burnscar') for f in fires]
        table[a]['recent_cells'] = [i['recent_cells'] if i else None for i in info]
        table[a]['own_excluded_cells'] = [i['own_excluded_cells'] if i else None for i in info]
    cand = {a: t for a, t in table.items() if a != 'control'}
    best = max(t['mean_growth_iou'] for t in cand.values())
    ties = [a for a, t in cand.items() if best - t['mean_growth_iou'] <= 0.002]
    parse = lambda a: (float(a.split('_m')[1]), int(a[1:].split('_')[0]))
    pick = sorted(ties, key=lambda a: (-parse(a)[0], parse(a)[1]))[0]
    n, m = int(pick[1:].split('_')[0]), float(pick.split('_m')[1])
    benefit = table[pick]['mean_growth_iou'] - table['control']['mean_growth_iou']
    man = {'purpose': 'frozen burn-scar choice before any held-out run; development fires only, harm arm',
           'dev_fires': fires, 'issue_dates': [str(i) for i in issue_dates()],
           'rule': __doc__.split('Selection rule')[1].strip(),
           'grid': table, 'ties_with_best': ties, 'chosen': pick, 'years': n, 'mult': m,
           'dev_growth_iou_gain_vs_control': benefit, 'no_dev_benefit': benefit <= 0.002,
           'heldout_arms': {a: 'control + BURNSCAR_FILE=rasters/%s_2026-08-02.tif BURNSCAR_YEARS=%d BURNSCAR_MULT=%s' % (a, n, m)
                            for a in ARMS},
           'heldout_manifest': str(FW / 'manifest.json'),
           'weather_cases': 'same landscape, weather, state, observed and PHYSICS overrides as front_weighted held-out control'}
    path.write_text(json.dumps(man, indent=1) + '\n')
    for a, t in table.items():
        log.info('%s mean growth IoU %.4f', a, t['mean_growth_iou'])
    log.info('chosen %s (gain %.4f, no_dev_benefit=%s)', pick, benefit, man['no_dev_benefit'])


def heldout():
    setup('heldout')
    man = json.loads((ROOT / 'manifest.json').read_text())
    mf = json.loads((FW / 'manifest.json').read_text())
    issue = dt.date(2026, 8, 2)
    jobs = []
    for x in mf['fires']:
        d, src, out = Path('data/training/fires') / x['fire'], FW / 'heldout' / x['fire'], ROOT / 'heldout' / x['fire']
        out.mkdir(parents=True, exist_ok=True)
        if not (out / 'control').exists():  # the front_weighted control: same case, same weather, scored in place
            os.symlink((src / 'control').resolve(), out / 'control')
        for arm in ARMS:
            jobs.append((x['fire'], 'scar_' + arm, (str(d / 'landscape.tif'), str(src / 'wx'), 0, str(d / 'fire-state.tif'),
                                                    '2026-08-02T12:00', out / ('scar_' + arm), x['observed'],
                                                    extra(arm, issue, man['years'], man['mult']))))
    status = run_all(jobs, 'heldout')
    cov = {}
    for f, a in [(f, a) for f, a, _ in jobs]:
        p = ROOT / 'heldout' / f / a / 'case.json'
        if p.exists():
            cov.setdefault(f, {})[a] = json.loads(p.read_text()).get('burnscar')
    (ROOT / 'heldout_status.json').write_text(json.dumps({'runs': status, 'burnscar': cov}, indent=1) + '\n')
    (ROOT / 'HELDOUT_DONE').write_text('done\n')


def score():
    setup('score')
    out = ROOT / 'heldout'
    arms = 'control,' + ','.join('scar_' + a for a in ARMS)
    r = subprocess.run(['uv', 'run', 'scripts/edge_pilot_score.py', str(out), '--manifest', str(FW / 'manifest.json'),
                        '--arms', arms], capture_output=True, text=True)
    log.info('%s %s', r.stdout[-1500:], r.stderr[-1500:])
    if r.returncode:
        sys.exit('score failed')
    st = json.loads((ROOT / 'heldout_status.json').read_text())['burnscar']
    (out / 'status.json').write_text(json.dumps({'fires': {f: {'covered_fuel_cells': (v.get('scar_harm') or {}).get('recent_cells')}
                                                  for f, v in st.items()}}) + '\n')
    r = subprocess.run(['uv', 'run', 'scripts/edge_heldout_table.py', str(out)], capture_output=True, text=True)
    log.info('%s %s', r.stdout[-1500:], r.stderr[-1500:])


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    {'rasters': rasters, 'dev': dev, 'select': select, 'heldout': heldout, 'score': score}[sys.argv[1]]()
