#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Score paired 12-hour ELMFIRE arms against the exact +12 h FEDS perimeter and post-issue VIIRS hotspots.

Usage: edge_pilot_score.py ROOT [--arms a,b,...] [--manifest MANIFEST.json]
  ROOT/FIRE/ARM/outputs/run.json are the forecasts. Fires: the three development fires (front_calibrate.FIRES), or
  the manifest's fires (issue, observed file; state data/training/fires/FIRE/fire-state.tif). Arms: --arms, else
  every ARM directory with outputs. no_growth (initial FEDS perimeter as the forecast) is always added. Missing or
  failed arms are recorded, not dropped. Writes ROOT/scores.json and ROOT/comparison.md.
"""
import argparse
import datetime as dt
import json
import logging
from pathlib import Path
import numpy as np
import rasterio
from front_calibrate import FIRES, utc, targets, hotspot_scores
from selfcal_adj import load_toa, raster_of
from perimeter_batch import scores

METRICS = [('growth_iou', 'Growth IoU', '%.4f'), ('iou', 'Whole IoU', '%.4f'), ('area_ratio', 'Area ratio', '%.2f'),
           ('boundary_mean_m', 'Boundary mean m', '%.0f'), ('false_growth_fraction', 'False growth', '%.3f')]


def score(output, start_geom, truth_geom, pts, state_tf, no_growth=False):
    toa, valid, transform, crs, res, _ = load_toa(output)
    initial = raster_of(start_geom, toa.shape, transform, crs)
    truth = raster_of(truth_geom, toa.shape, transform, crs)
    start = initial if no_growth else initial | (valid & (toa == 0))
    pred = start if no_growth else start | (valid & (toa <= 12 * 3600))
    s = scores(pred, truth, start, res)
    s['boundary_mean_m'] = (s.pop('boundary') or {}).get('mean_m')
    growth = pred & ~start
    s['false_growth_cells'] = int((growth & ~truth).sum())
    s['false_growth_fraction'] = float((growth & ~truth).sum() / growth.sum()) if growth.any() else None
    s['hotspots'] = hotspot_scores(growth, pts, transform, crs, state_tf)
    if no_growth:
        s['active_edge_coverage'] = 0.0
    else:
        init = json.loads((Path(output).parent / 'case.json').read_text())['init']
        s['active_mode'] = init.get('active_mode')
        s['active_edge_coverage'] = init['active_edge_cells'] / init['edge_band_cells'] if 'edge_band_cells' in init else None
        s['front'] = init.get('front')
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--arms')
    ap.add_argument('--manifest')
    a = ap.parse_args()
    root = Path(a.root)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(root / 'scores.log')], force=True)
    if a.manifest:
        m = json.loads(Path(a.manifest).read_text())
        fires = {x['fire']: dict(issue=m['issue'], observed=x['observed'],
                                 state=f"data/training/fires/{x['fire']}/fire-state.tif") for x in m['fires']}
    else:
        fires = {f: dict(issue=FIRES[f]['issue'], observed=FIRES[f]['observed'],
                         state=f'data/training/fires/{f}/fire-state.tif') for f in ['2021-038', '2021-031', '2020-008']}
    results, failures = {}, {}
    for fire, cfg in fires.items():
        names = a.arms.split(',') if a.arms else sorted(p.parent.parent.name for p in (root / fire).glob('*/outputs/run.json'))
        try:
            T = utc(cfg['issue'])
            obs = json.loads(Path(cfg['observed']).read_text())
            per = {utc(p['time']): p['geometry'] for p in obs['perimeters']}
            start, end = per[max(t for t in per if t <= T)], T + dt.timedelta(hours=12)
            if end not in per:
                raise ValueError('no exact +12h truth')
            pts = targets(obs, T, end, start)
            with rasterio.open(cfg['state']) as src:
                tf = src.transform
        except Exception as e:
            failures[fire] = str(e)
            continue
        results[fire] = {}
        for arm in names:
            output = root / fire / arm / 'outputs'
            if not (output / 'run.json').exists():
                failures.setdefault(fire, {})
                if isinstance(failures[fire], dict):
                    failures[fire][arm] = 'forecast missing'
                continue
            results[fire][arm] = score(output, start, per[end], pts, tf)
        if results[fire]:
            ref = root / fire / next(iter(results[fire])) / 'outputs'
            results[fire]['no_growth'] = score(ref, start, per[end], pts, tf, no_growth=True)
        logging.info('%s scored: %s', fire, list(results[fire]))
    (root / 'scores.json').write_text(json.dumps({'fires': results, 'failures': failures}, indent=1) + '\n')

    arm_names = ['no_growth'] + sorted({k for v in results.values() for k in v if k != 'no_growth'})
    head = '| Fire | Arm | ' + ' | '.join(t for _, t, _ in METRICS) + ' | Hotspot hits | Active edge |'
    lines = [f'# Paired 12-hour forecasts: {root}', '', head, '|' + '---|' * (len(METRICS) + 4)]
    fmt = lambda f, v: '-' if v is None else f % v
    for fire, arms in results.items():
        for arm in arm_names:
            if arm in arms:
                v = arms[arm]
                h = v['hotspots']
                lines.append(f'| {fire} | {arm} | ' + ' | '.join(fmt(f, v.get(k)) for k, _, f in METRICS) +
                             f" | {h['hits']}/{h['targets']} | {fmt('%.3f', v.get('active_edge_coverage'))} |")
    full = [f for f, v in results.items() if all(x in v for x in arm_names)]
    lines += ['', f'Fires with every arm: {len(full)}/{len(fires)}. Failures: {json.dumps(failures) if failures else "none"}', '',
              '| Arm | ' + ' | '.join(f'{t} mean / median' for _, t, _ in METRICS) + ' | Hotspot hits (sum) | Growth IoU > control |',
              '|' + '---|' * (len(METRICS) + 3)]
    for arm in arm_names:
        cells = []
        for k, _, f in METRICS:
            x = [results[r][arm][k] for r in full if results[r][arm].get(k) is not None]
            cells.append(f'{f % np.mean(x)} / {f % np.median(x)} (n={len(x)})' if x else '-')
        hits = sum(results[r][arm]['hotspots']['hits'] for r in full)
        tg = sum(results[r][arm]['hotspots']['targets'] for r in full)
        better = (sum((results[r][arm]['growth_iou'] or 0) > (results[r]['control']['growth_iou'] or 0) for r in full)
                  if 'control' in arm_names and arm != 'control' else '-')
        lines.append(f'| {arm} | ' + ' | '.join(cells) + f' | {hits}/{tg} | {better} |')
    (root / 'comparison.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
