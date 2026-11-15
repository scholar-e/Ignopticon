#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Live-moisture (LIVE_MOISTURE=gsi) and 20-ft wind factor (WIND_20FT) test, data/physics_mw.

    physics_mw.py grid      4 development fires (2021-038, 2021-031, 2020-008, Widemouth 66510) x live {const, gsi} x
                            wind factor {0.87, 0.6}; control physics (no learned front), 12 h from the issue
    physics_mw.py select    score the grid (growth IoU, area ratio vs the FEDS +12 h perimeter), pick, freeze
                            data/physics_mw/manifest.json (refuses if it exists)
    physics_mw.py heldout   run the frozen choice on the 12 frozen held-out fires (front_weighted manifest), reusing
                            their cases' landscape/weather; arm physics_mw next to the existing control (symlink)
Env JOBS (default 8) = parallel ELMFIRE cases. Rule: best mean growth IoU over the 4 fires; ties within 0.002 go to
the mean |log area ratio| nearest 1. The 12 held-out fires are not read before the manifest is frozen.
"""
import json
import logging
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from edge_front_weighted import run_case

ROOT = Path('data/physics_mw')
F = 'data/training/fires/'
log = logging.getLogger(__name__)
LIVE, WIND = ['const', 'gsi'], [0.87, 0.6]


def setup(name):
    ROOT.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / f'{name}.log')], force=True)


def arm_name(live, wind):
    return f'live-{live}_w{wind}'


def extra(live, wind):
    x = []
    if live == 'gsi':
        x.append('LIVE_MOISTURE=gsi')
    if wind != 0.87:
        x.append(f'WIND_20FT={wind}')
    return x


def dev_fires():
    from front_calibrate import FIRES
    return {k: FIRES[k] for k in ['2021-038', '2021-031', '2020-008', 'map20260802-66510']}


def grid():
    setup('grid')
    jobs = []
    for fire, cfg in dev_fires().items():
        for live in LIVE:
            for wind in WIND:
                jobs.append((F + fire + '/landscape.tif', cfg['fc'][0], cfg['fc'][1], F + fire + '/fire-state.tif',
                             cfg['issue'], ROOT / 'grid' / fire / arm_name(live, wind), cfg['observed'], extra(live, wind)))
    with ThreadPoolExecutor(int(os.environ.get('JOBS', 8))) as ex:
        futs = [ex.submit(run_case, *j) for j in jobs]
        for j, f in zip(jobs, futs):
            try:
                f.result()
                log.info('%s done', j[5])
            except Exception as e:
                log.error('%s failed: %s', j[5], str(e)[-800:])
    (ROOT / 'GRID_DONE').write_text('done\n')


def score_root(root, fires, arms):
    """fires: name -> (issue, observed, state). Returns {fire: {arm: score}}."""
    import datetime as dt
    import rasterio
    from edge_pilot_score import score, utc, targets
    out = {}
    for fire, (issue, observed, state) in fires.items():
        T = utc(issue)
        obs = json.loads(Path(observed).read_text())
        per = {utc(p['time']): p['geometry'] for p in obs['perimeters']}
        start, end = per[max(t for t in per if t <= T)], T + dt.timedelta(hours=12)
        pts = targets(obs, T, end, start)
        with rasterio.open(state) as src:
            tf = src.transform
        out[fire] = {}
        for arm in arms:
            o = Path(root) / fire / arm / 'outputs'
            if (o / 'run.json').exists():
                out[fire][arm] = score(o, start, per[end], pts, tf)
    return out


def select():
    setup('select')
    path = ROOT / 'manifest.json'
    if path.exists():
        sys.exit(f'{path} exists: frozen')
    fires = {f: (c['issue'], c['observed'], F + f + '/fire-state.tif') for f, c in dev_fires().items()}
    arms = [arm_name(l, w) for l in LIVE for w in WIND]
    res = score_root(ROOT / 'grid', fires, arms)
    table = {}
    for l in LIVE:
        for w in WIND:
            a = arm_name(l, w)
            g = [res[f][a]['growth_iou'] for f in fires if a in res[f]]
            r = [res[f][a]['area_ratio'] for f in fires if a in res[f]]
            table[a] = dict(live=l, wind=w, n=len(g), mean_growth_iou=float(np.mean(g)),
                            mean_abs_log_area_ratio=float(np.mean([abs(math.log(x)) for x in r if x and x > 0])),
                            per_fire={f: dict(growth_iou=res[f][a]['growth_iou'], area_ratio=res[f][a]['area_ratio'])
                                      for f in fires if a in res[f]})
    full = {a: t for a, t in table.items() if t['n'] == len(fires)}
    best = max(t['mean_growth_iou'] for t in full.values())
    close = [a for a, t in full.items() if best - t['mean_growth_iou'] <= 0.002]
    pick = min(close, key=lambda a: full[a]['mean_abs_log_area_ratio'])
    m = {'purpose': 'frozen physics choice (live fuel moisture, 20-ft wind factor) before any held-out run',
         'dev_fires': list(fires), 'grid': table, 'rule': __doc__.split('Rule:')[1].strip(),
         'chosen': pick, 'live_moisture': table[pick]['live'], 'wind_20ft': table[pick]['wind'],
         'overrides': extra(table[pick]['live'], table[pick]['wind']),
         'default_for_comparison': arm_name('const', 0.87)}
    path.write_text(json.dumps(m, indent=1) + '\n')
    for a, t in table.items():
        log.info('%s mean growth IoU %.4f |log area| %.3f n=%d', a, t['mean_growth_iou'], t['mean_abs_log_area_ratio'], t['n'])
    log.info('chosen %s', pick)


def heldout():
    setup('heldout')
    m = json.loads((ROOT / 'manifest.json').read_text())
    fw = Path('data/edge_causal/front_weighted')
    mf = json.loads((fw / 'manifest.json').read_text())
    jobs = []
    for x in mf['fires']:
        fire = x['fire']
        out = ROOT / 'heldout' / fire
        out.mkdir(parents=True, exist_ok=True)
        link = out / 'control'
        if not link.exists():
            link.symlink_to((fw / 'heldout' / fire / 'control').resolve())
        d, h = Path(F) / fire, fw / 'heldout' / fire
        jobs.append((str(d / 'landscape.tif'), str(h / 'wx'), 0, str(d / 'fire-state.tif'), '2026-08-02T12:00',
                     out / 'physics_mw', x['observed'], m['overrides']))
    with ThreadPoolExecutor(int(os.environ.get('JOBS', 8))) as ex:
        futs = [ex.submit(run_case, *j) for j in jobs]
        for j, f in zip(jobs, futs):
            try:
                f.result()
                log.info('%s done', j[5])
            except Exception as e:
                log.error('%s failed: %s', j[5], str(e)[-800:])
    (ROOT / 'HELDOUT_DONE').write_text('done\n')


if __name__ == '__main__':
    {'grid': grid, 'select': select, 'heldout': heldout}[sys.argv[1]]()
