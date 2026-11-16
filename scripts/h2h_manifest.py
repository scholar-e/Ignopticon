#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "rasterio", "shapely>=2.1", "pyproj"]
# ///
"""Freeze the head-to-head fire list (ours vs PyreCast) for one issue, BEFORE any scoring. Writes data/h2h/manifest_DAY.json,
tasks_DAY.txt, catalog_DAY.csv. No forecast of ours is looked at.

Filters, in order, each counted:
 1 FEDS fires known at issue (latest geometry in snapshots <= issue; scripts/multiday.snapshot_geoms)
 2 uncontained at issue and matched to a WFIGS incident (scripts/containment_aug2.py, same rule as multiday manifest)
 3 spatially overlapping a PyreCast fire that has a complete run (5 percentiles) with run_dt_utc in [issue-18 h, issue]:
   some cell with PyreCast p50 time-of-arrival > 0 (= not burned at its start) within 1 km of the FEDS polygon.
   Per PyreCast fire the latest such run is used; a FEDS fire overlapping several PyreCast fires takes the one with most overlap cells.
Usage: h2h_manifest.py 2026-08-13T12:00
"""
import csv, datetime as dt, json, logging, os, sys, urllib.request
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(Path(__file__).resolve().parent.parent)
import multiday as M
B = 'https://f005.backblazeb2.com/file/fire-forecast-archive'
H = Path('data/h2h')
log = logging.getLogger('h2h_manifest')


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_size == 0:
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, 'wb') as f:
            f.write(r.read())
    return dest


def main(issue):
    import containment_aug2, rasterio
    from rasterio.features import rasterize
    import shapely
    from pyproj import Transformer
    from shapely.ops import transform
    d = M.day(issue); T = dt.datetime.fromisoformat(issue)
    geoms = M.snapshot_geoms(issue)
    ids = H / f'fires_{d}.txt'; ids.write_text('\n'.join(sorted(geoms, key=int)) + '\n')
    cfile = H / f'containment_{d}.json'
    containment_aug2.main(str(ids), str(M.SNAP), issue, str(cfile))
    cont = json.load(open(cfile))['fires']
    counts = {}
    unc = []
    for i, c in cont.items():
        counts[c['status']] = counts.get(c['status'], 0) + 1
        if c['status'] == 'uncontained':
            unc.append(i)
    log.info('FEDS known %d, containment %s, uncontained+matched %d', len(geoms), counts, len(unc))
    pm = json.load(open(H / 'pyre_manifest.json'))
    runs = {}
    for v in pm['runs'].values():
        rt = dt.datetime.strptime(v['run_ts'], '%Y%m%d_%H%M%S')
        lag = (T - rt).total_seconds() / 3600
        if 0 <= lag <= 18 and v.get('complete') and all(v['files'].get(p, {}).get('ok') for p in ('10', '30', '50', '70', '90')):
            runs.setdefault(v['slug'], []).append((rt, v, lag))
    latest = {s: max(r, key=lambda x: x[0]) for s, r in runs.items()}
    log.info('PyreCast fires with a complete run in window: %d (runs in window %d)', len(latest), sum(len(r) for r in runs.values()))
    ov = {}   # feds id -> list of (cells, slug)
    for s, (rt, v, lag) in latest.items():
        p = fetch(f'{B}/forecast_archive/{s}/{v["run_ts"]}/50.tif', H / 'pyrecast' / s / v['run_ts'] / '50.tif')
        with rasterio.open(p) as src:
            toa = src.read(1); ok = np.isfinite(toa) & (toa > 0)
            tr = Transformer.from_crs('EPSG:4326', src.crs, always_xy=True).transform
            for i in unc:
                g = transform(tr, geoms[i][0]).buffer(1000)
                if not g.intersects(shapely.box(*src.bounds)):
                    continue
                m = rasterize([(g, 1)], out_shape=toa.shape, transform=src.transform, dtype='uint8').astype(bool)
                n = int((m & ok).sum())
                if n: ov.setdefault(i, []).append((n, s))
    keep, rows, tasks = [], [], []
    for i in sorted(ov, key=int):
        n, s = max(ov[i])
        rt, v, lag = latest[s]
        g, props = geoms[i]; w, so, e, no = g.bounds
        fire = M.fire_dir(issue, i)
        dd = dt.date.fromisoformat(issue[:10])
        keep.append({'feds_id': i, 'fire': fire, 'incident': cont[i]['incident'], 'area_km2': props.get('area_km2'),
                     'place': props.get('place'), 'slug': s, 'run_ts': v['run_ts'], 'run_dt_utc': rt.strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'lag_h': round(lag, 2), 'first_seen_archive': v['first_seen'], 'overlap_cells_30m': n,
                     'other_slugs': sorted({x[1] for x in ov[i] if x[1] != s})})
        rows.append({'id': fire, 'west': round(w, 4), 'south': round(so, 4), 'east': round(e, 4), 'north': round(no, 4),
                     'start': (dd - dt.timedelta(days=1)).isoformat(), 'end': (dd + dt.timedelta(days=1)).isoformat(), 'feds_id': i})
        tasks.append(f'new\t{issue}\t{fire}\t{i}')
    man = {'frozen_by': 'scripts/h2h_manifest.py, before any scoring and before our runs exist', 'issue': issue,
           'frozen': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'rule': __doc__.split('Filters')[1].split('Usage')[0].strip(),
           'counts': {'feds_known_at_issue': len(geoms), 'containment': counts, 'uncontained_matched': len(unc),
                      'pyrecast_fires_complete_run_in_window': len(latest), 'matched_to_pyrecast': len(keep),
                      'distinct_pyrecast_fires_used': len({k['slug'] for k in keep})},
           'fires': keep}
    (H / f'manifest_{d}.json').write_text(json.dumps(man, indent=1) + '\n')
    (H / f'tasks_{d}.txt').write_text('\n'.join(tasks) + '\n')
    with open(H / f'catalog_{d}.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, ['id', 'west', 'south', 'east', 'north', 'start', 'end', 'feds_id']); w.writeheader(); w.writerows(rows)
    log.info('counts %s', man['counts'])


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
    main(sys.argv[1])
