#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "pandas", "tabulate", "pyarrow", "lightgbm"]
# ///
"""Size-calibrated worst case (MODELLING_LOG.md entry 31). Post-processes existing ELMFIRE runs; no new runs.

  worstcase_calibrate.py classes    freeze size classes from fire size at issue only (no recall computed)  -> data/worstcase2/classes.json
  worstcase_calibrate.py calibrate  power-law growth scale G(size) + per-class (w, m) on CALIBRATION fires -> data/worstcase2/calibration.{json,md}
  worstcase_calibrate.py heldout    one evaluation on the frozen held-out 12                              -> data/worstcase2/heldout.{csv,md}
  worstcase_calibrate.py export     new upper GeoJSON for the 73 Aug 2 fires + index.json                 -> earth/public/data/forecasts_bounds

Footprint: candidate cells = burnable (FBFM40 > 100) cells outside the issue-time active perimeter (FEDS at issue | toa == 0).
Score s = w * rank(ELMFIRE arrival, earliest over local members; unburned last, ties by distance) + (1-w) * rank(distance to the
issue perimeter); ranks are fractions 0..1 over the candidates; take lowest s until area A. Area A = m * G(size),
G = a * size_km2**b (log-log OLS on calibration fires with observed +12 h growth), no single-cell cap.
Per size class, (w, m): for each w the smallest m on a frozen grid whose pooled recall (sum captured / sum observed growth
area) >= TARGET, then the w with the least pooled false-alarm area. Calibration = Aug 2 uncontained incident-matched fires not in the
frozen held-out 12 + the dev/front-test fires (where_skill manifest), with FEDS +12 h growth.
"""
import datetime as dt
import json
import logging
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from scipy.ndimage import distance_transform_edt

sys.path.insert(0, str(Path(__file__).parent))
import where_skill as ws
import growth_bounds as gb
from selfcal_adj import load_toa, raster_of

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data/worstcase2'
OUT.mkdir(parents=True, exist_ok=True)
TARGET = 0.80
WS = [0.0, 0.25, 0.5, 0.75, 1.0]
MGRID = np.round(np.geomspace(0.25, 64, 60), 4)   # frozen multiplier grid
MIN_FIRES = 5                                       # fewer calibration fires in a class -> pool with a neighbour
CLASS_KM2 = [10.0, 100.0]                           # small < 1,000 ha, medium 1,000-10,000 ha, large > 10,000 ha (adjusted in `classes` if needed)
CLASS_NAMES = ['small', 'medium', 'large']
log = logging.getLogger('worstcase')


def hit_dirs(fire, base):
    """base run + every local ensemble member (fuelwind16, physics3, worstcase) with the same grid -> earliest arrival is used."""
    mem = [ROOT / m for m in map(str, gb.members(fire))] if (ROOT / 'data/perimeter_batch' / fire / 'runs').exists() else []
    return [base] + [m for m in mem if (m / 'run.json').exists() and m.resolve() != Path(base).resolve()]


def load(fire, base, truth=True):
    dirs = hit_dirs(fire, ROOT / base if not Path(base).is_absolute() else Path(base))
    toa, valid, tf, crs, res, _ = load_toa(dirs[0])
    toa = np.where(valid, toa, np.inf)
    used = [str(dirs[0])]
    for d in dirs[1:]:
        t2, v2, *_ = load_toa(d)
        if t2.shape == toa.shape:
            toa = np.minimum(toa, np.where(v2, t2, np.inf)); used.append(str(d))
    valid = np.isfinite(toa)
    toa = np.where(valid, toa, 0.0)
    start = valid & (toa == 0)
    tr = None
    try:
        ts = ws.truth_start(fire, toa.shape, tf, crs) if truth else None
    except Exception as ex:
        log.warning('%s truth: %s', fire, ex); ts = None
    if truth and ts is None:
        return None
    if not truth:  # export: start = FEDS perimeter at issue (as in calibration), no +12 h perimeter needed
        try:
            issue, obs_path, _ = ws.fire_inputs(fire)
            if fire == 'map20260802-66510':
                obs_path = ROOT / 'data/perimeter_batch/map20260802-66510/associated-observed.json'
            obs = json.loads(Path(obs_path).read_text())
            T = ws.utc(issue)
            per = {ws.utc(p['time']): p['geometry'] for p in obs['perimeters']}
            pre = [t for t in per if t <= T]
            if pre:
                ts = (raster_of(per[max(pre)], toa.shape, tf, crs), None)
        except Exception as ex:
            log.warning('%s issue perimeter: %s', fire, ex)
    if ts is not None:
        start = start | ts[0]; tr = ts[1]
    try:
        with rasterio.open(ws.landscape_path(fire)) as src:
            burnable = src.read(4) > 100 if src.shape == toa.shape else None
    except Exception:
        burnable = None
    if burnable is None:
        burnable = np.ones(toa.shape, bool)
    cell = res[0] * res[1] / 1e6
    return dict(toa=toa, valid=valid, start=start, truth=tr, burnable=burnable, res=res, cell=cell, tf=tf, crs=crs, used=used,
                size_km2=float(start.sum() * cell))


def order_for(P, w):
    """candidate flat indices sorted best-first for blend weight w, plus the candidate mask."""
    cand = P['burnable'] & ~P['start']
    if 'dist' not in P:
        P['dist'] = distance_transform_edt(~P['start'], sampling=P['res'])
    idx = np.flatnonzero(cand.ravel())
    d = P['dist'].ravel()[idx]
    n = len(idx)
    reach = (P['valid'] & (P['toa'] > 0)).ravel()[idx]
    toa = P['toa'].ravel()[idx]
    ra = np.empty(n)
    ra[np.lexsort((d, np.where(reach, toa, np.inf)))] = np.arange(n) / max(n - 1, 1)
    rd = np.empty(n)
    rd[np.argsort(d, kind='stable')] = np.arange(n) / max(n - 1, 1)
    s = w * ra + (1 - w) * rd
    return idx[np.lexsort((d, s))]


def pick(P, w, area_km2):
    o = order_for(P, w)
    n = int(min(len(o), max(0, round(area_km2 / P['cell']))))
    return o[:n]


def observed(P):
    return (P['truth'] & ~P['start']) if P['truth'] is not None else None


def class_of(size_km2, bounds=None):
    b = bounds or json.loads((OUT / 'classes.json').read_text())['bounds_km2']
    return sum(size_km2 >= x for x in b)


def calib_fires():
    man = json.loads((ws.OUT / 'manifest.json').read_text())
    held = {f['fire'] for f in json.loads(Path(ROOT / gb.MAN).read_text())['fires']}
    return [(e['fire'], e['run'], e['source']) for e in man['primary'] + man['dev'] if e['fire'] not in held]


def cmd_classes():
    rows = []
    for fire, run, src in calib_fires():
        P = load(fire, run)
        if P is None:
            continue
        O = observed(P)
        rows.append(dict(fire=fire, size_km2=P['size_km2'], obs_km2=float(O.sum() * P['cell']), grid=list(P['toa'].shape)))
    d = pd.DataFrame(rows)
    d['cls'] = [class_of(s, CLASS_KM2) for s in d.size_km2]
    print(d.sort_values('size_km2').to_string())
    cnt = d.groupby('cls').size().to_dict()
    log.info('class counts with bounds %s km2: %s', CLASS_KM2, cnt)
    (OUT / 'classes.json').write_text(json.dumps(dict(
        frozen_by='scripts/worstcase_calibrate.py classes: fire size at issue only, before any recall was computed',
        bounds_km2=CLASS_KM2, names=CLASS_NAMES, calibration_counts={CLASS_NAMES[int(k)]: int(v) for k, v in cnt.items()},
        calibration_sizes={r.fire: r.size_km2 for r in d.itertuples()}), indent=1))


def cmd_calibrate():
    cl = json.loads((OUT / 'classes.json').read_text())
    recs, store = [], []
    for fire, run, src in calib_fires():
        P = load(fire, run)
        if P is None:
            continue
        O = observed(P)
        if not O.any():
            continue
        store.append((fire, P['size_km2'], float(O.sum() * P['cell']), len(P['used'])))
        orders = {w: order_for(P, w) for w in WS}
        Of = O.ravel()
        cum = {w: np.cumsum(Of[orders[w]]) for w in WS}
        recs.append(dict(fire=fire, size=P['size_km2'], obs=float(O.sum() * P['cell']), cell=P['cell'], N=len(orders[0.0]), cum=cum,
                         n_obs_all=int(O.sum())))
        log.info('%s size %.1f km2 obs %.2f km2 members %d', fire, P['size_km2'], recs[-1]['obs'], len(P['used']))
    sz = np.array([r['size'] for r in recs]); ob = np.array([r['obs'] for r in recs])
    ok = (ob > 0) & (sz > 0)
    b, a = np.polyfit(np.log(sz[ok]), np.log(ob[ok]), 1)
    a = math.exp(a)
    log.info('G = %.4g * size_km2^%.3f (n=%d)', a, b, ok.sum())
    cls = np.array([class_of(s, cl['bounds_km2']) for s in sz])

    def table(sel):
        out = {}
        for w in WS:
            best = None
            for m in MGRID:
                tp = fp = 0.0
                for i in np.flatnonzero(sel):
                    r = recs[i]
                    n = int(min(r['N'], max(0, round(m * a * r['size'] ** b / r['cell']))))
                    hit = int(r['cum'][w][n - 1]) if n > 0 else 0
                    tp += hit * r['cell']; fp += (n - hit) * r['cell']
                rec = tp / sum(recs[i]['obs'] for i in np.flatnonzero(sel))
                if rec >= TARGET:
                    best = dict(w=w, m=float(m), recall=rec, fa_km2=fp, reached=True); break
            if best is None:  # target not reachable: take the largest m
                m = MGRID[-1]; tp = fp = 0.0
                for i in np.flatnonzero(sel):
                    r = recs[i]; n = int(min(r['N'], max(0, round(m * a * r['size'] ** b / r['cell']))))
                    hit = int(r['cum'][w][n - 1]) if n > 0 else 0
                    tp += hit * r['cell']; fp += (n - hit) * r['cell']
                best = dict(w=w, m=float(m), recall=tp / sum(recs[i]['obs'] for i in np.flatnonzero(sel)), fa_km2=fp, reached=False)
            out[w] = best
        return out
    # pooling: classes with < MIN_FIRES calibration fires are merged with the nearest neighbour
    groups = [[0], [1], [2]]
    cnt = [int((cls == c).sum()) for c in range(3)]
    for c in range(3):
        if cnt[c] < MIN_FIRES:
            nb = 1 if c == 0 else (1 if c == 2 else (0 if cnt[0] <= cnt[2] else 2))
            groups = [g for g in groups if c not in g]
            for g in groups:
                if nb in g:
                    g.append(c)
    # simple fix-up so every class belongs to exactly one group
    cover = {c: g for g in groups for c in g}
    for c in range(3):
        if c not in cover:
            groups.append([c])
    res = {}
    md = ['# Worst-case calibration (calibration fires only)', '', f'G(size) = {a:.4g} * size_km2^{b:.3f} km2 (log-log OLS, n={ok.sum()} fires with growth). Target pooled recall {TARGET:.0%}.',
          f'Class bounds km2: {cl["bounds_km2"]}; calibration fires per class: {cnt}. Groups (classes pooled for calibration): {groups}', '']
    for g in groups:
        sel = np.isin(cls, g)
        t = table(sel)
        reach = [v for v in t.values() if v['reached']]
        best = min(reach, key=lambda v: (v['fa_km2'], v['m'])) if reach else max(t.values(), key=lambda v: v['recall'])
        md += [f'## classes {[CLASS_NAMES[c] for c in g]} (n={int(sel.sum())} fires, observed growth {sum(recs[i]["obs"] for i in np.flatnonzero(sel)):.1f} km2)', '',
               '| w | m | pooled recall | false alarm km2 | target reached |', '|---|---|---|---|---|']
        md += [f"| {w} | {v['m']:.3g} | {v['recall']:.3f} | {v['fa_km2']:.1f} | {v['reached']} |" for w, v in t.items()]
        md += ['', f"chosen: w={best['w']}, m={best['m']:.3g}", '']
        for c in g:
            res[CLASS_NAMES[c]] = dict(w=best['w'], m=best['m'], calib_recall=best['recall'], calib_fa_km2=best['fa_km2'],
                                       target_reached=best['reached'], pooled_with=[CLASS_NAMES[x] for x in g], n_calib=int((cls == c).sum()))
    md += ['## Calibration fires', '', pd.DataFrame([dict(fire=r['fire'], size_km2=round(r['size'], 1), obs_km2=round(r['obs'], 2),
                                                          cls=CLASS_NAMES[int(cls[i])]) for i, r in enumerate(recs)]).to_markdown(index=False)]
    (OUT / 'calibration.md').write_text('\n'.join(md) + '\n')
    (OUT / 'calibration.json').write_text(json.dumps(dict(a=a, b=b, target=TARGET, bounds_km2=cl['bounds_km2'], classes=res, n_fit=int(ok.sum())), indent=1))
    print('\n'.join(md))


def params():
    return json.loads((OUT / 'calibration.json').read_text())


def area_for(size_km2, cal):
    c = CLASS_NAMES[class_of(size_km2, cal['bounds_km2'])]
    p = cal['classes'][c]
    return c, p, p['m'] * cal['a'] * size_km2 ** cal['b']


def cmd_heldout():
    cal = params()
    man = json.loads(Path(ROOT / gb.MAN).read_text())
    cont = json.loads((ROOT / 'data/containment/aug2_containment.json').read_text())['fires']
    old = pd.read_csv(ROOT / 'data/bounds/heldout_per_fire.csv')
    rows = []
    for f in man['fires']:
        fire = f['fire']
        base = ROOT / 'data/edge_causal/front_weighted/heldout' / fire / 'control/outputs'
        P = load(fire, str(base))
        O = observed(P)
        cell = P['cell']
        c, p, A = area_for(P['size_km2'], cal)
        sel = np.zeros(O.size, bool); sel[pick(P, p['w'], A)] = True
        sel = sel.reshape(O.shape)
        for case, pred in (('new_upper', sel), ('control', P['valid'] & (P['toa'] > 0) & ~P['start'])):
            if case == 'control':
                # the old table's control = the single control run (not the union)
                t0, v0, *_ = load_toa(base); pred = (v0 & (t0 > 0)) & ~P['start']
            rows.append(dict(fire=fire, status=cont.get(fire.split('-')[-1], {}).get('status'), cls=c, size_km2=P['size_km2'], case=case,
                             w=p['w'], m=p['m'], area_km2=A if case == 'new_upper' else np.nan, obs_km2=float(O.sum() * cell),
                             pred_km2=float(pred.sum() * cell), hit_km2=float((pred & O).sum() * cell), false_alarm_km2=float((pred & ~O).sum() * cell),
                             whole_iou=float(((P['start'] | pred) & P['truth']).sum() / ((P['start'] | pred) | P['truth']).sum()),
                             members=len(P['used'])))
        o = old[(old.fire == fire) & (old.case == 'upper_harm')].iloc[0]
        rows.append(dict(fire=fire, status=cont.get(fire.split('-')[-1], {}).get('status'), cls=c, size_km2=P['size_km2'], case='old_upper',
                         obs_km2=o.obs_km2, pred_km2=o.pred_km2, hit_km2=o.recall * o.obs_km2 if o.obs_km2 > 0 else 0.0,
                         false_alarm_km2=o.false_alarm_km2, whole_iou=o.whole_iou))
    d = pd.DataFrame(rows)
    d.to_csv(OUT / 'heldout.csv', index=False)

    def summ(x):
        g = x.groupby('case', sort=False)
        t = pd.DataFrame({'n': g.fire.count(), 'obs_km2': g.obs_km2.sum(), 'recall_pooled': g.hit_km2.sum() / g.obs_km2.sum(),
                          'false_alarm_km2_total': g.false_alarm_km2.sum(), 'false_alarm_km2_mean': g.false_alarm_km2.mean(),
                          'pred_km2_total': g.pred_km2.sum(), 'whole_iou_mean': g.whole_iou.mean()})
        return t.round(3).to_markdown()
    md = ['# Held-out 12 (frozen), new size-calibrated worst case vs old upper vs control, +12 h vs FEDS', '',
          f'Calibration: {json.dumps(cal["classes"])}', '', '## Uncontained (headline)', '', summ(d[d.status == 'uncontained']), '',
          '## All 12', '', summ(d), '']
    for c in CLASS_NAMES:
        for lab, x in (('uncontained', d[(d.cls == c) & (d.status == 'uncontained')]), ('all', d[d.cls == c])):
            if len(x):
                md += [f'## class {c}, {lab} (n={x.fire.nunique()})', '', summ(x), '']
    md += ['## Per fire', '', d.round(3).to_markdown(index=False)]
    (OUT / 'heldout.md').write_text('\n'.join(md) + '\n')
    print('\n'.join(md[:40]))


def cmd_export():
    cal = params()
    feat = json.loads((ROOT / gb.B / 'fires_feat.json').read_text())
    fl = gb.fire_list()
    k_old = json.loads((ROOT / 'earth/public/data/forecasts_bounds/index.json').read_text())
    oldidx = {e['fire_id']: e for e in k_old['forecasts']}
    idx = []
    for fire, f in fl.items():
        if 'fire_id' not in f:
            continue
        ctl = gb.control_dir(fire)
        P = load(str(fire), str(ROOT / ctl), truth=False)
        c, p, A = area_for(P['size_km2'], cal)
        sel = pick(P, p['w'], A)
        flat = np.full(P['toa'].size, -1.0)
        flat[P['start'].ravel()] = 0.0
        reached = (P['valid'] & (P['toa'] > 0)).ravel()
        t = np.where(reached[sel], P['toa'].ravel()[sel], 12 * 3600.0)
        flat[sel] = t
        run = ROOT / gb.B / 'runs' / fire / 'upper_v2' / 'outputs'
        shutil.rmtree(run.parent, ignore_errors=True); run.mkdir(parents=True)
        tif = sorted(Path(P['used'][0]).glob('time_of_arrival_*.tif'))[-1]
        with rasterio.open(tif) as src:
            prof = src.profile
        with rasterio.open(run / tif.name, 'w', **prof) as o:
            o.write(flat.reshape(P['toa'].shape).astype(prof['dtype']), 1)
        shutil.copy(Path(P['used'][0]) / 'run.json', run / 'run.json')
        model = (f"ELMFIRE physics, area-constrained: UPPER case (worst case): sized to capture ~{int(cal['target']*100)}% of growth for fires of this size "
                 f"({c} class), footprint = blend of ELMFIRE arrival and distance from the perimeter (w={p['w']})")
        tmp = ROOT / gb.B / 'tmp' / fire / 'upper_v2'
        shutil.rmtree(tmp, ignore_errors=True)
        snap = gb.snapshot_place(f['fire_id'])
        cmd = ['uv', 'run', '--script', 'scripts/forecast_to_geojson.py', str(run), str(gb.fire_file(fire, 'fire-state.tif')),
               '2026-08-02T12:00', str(f['fire_id']), f['name'], model, str(tmp), '3,6,12'] + (['--perimeter', str(snap)] if snap else [])
        subprocess.run(cmd, check=True, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dst = ROOT / gb.OUTDIR / str(f['fire_id']) / '2026-08-02T1200-upper.json'
        doc = json.loads((tmp / str(f['fire_id']) / '2026-08-02T1200.json').read_text())
        doc['bound'] = {'case': 'upper', 'method': 'size-calibrated blend (worstcase_calibrate.py)', 'size_class': c,
                        'target_capture': cal['target'], 'w': p['w'], 'm': p['m'], 'fire_size_km2': P['size_km2'],
                        'growth_scale_km2': cal['a'] * P['size_km2'] ** cal['b'], 'area_km2': A, 'cells_selected': int(len(sel)),
                        'footprint_members': P['used'], 'old_area_km2': oldidx[f['fire_id']]['upper_area_km2']}
        dst.write_text(json.dumps(doc, separators=(',', ':'), ensure_ascii=False))
        e = dict(oldidx[f['fire_id']])
        e.update(upper_area_km2=A, size_class=c, w=p['w'], m=p['m'], fire_size_km2=P['size_km2'], n_members=len(P['used']),
                 footprint_source='ensemble_union' if len(P['used']) > 1 else 'control', old_upper_area_km2=oldidx[f['fire_id']]['upper_area_km2'])
        for k in ('analog_q90_cells',):
            e.pop(k, None)
        idx.append(e)
        log.info('%s %s size %.1f km2 -> A %.1f km2 (old %.1f)', fire, c, P['size_km2'], A, e['old_upper_area_km2'])
    idx.sort(key=lambda e: str(e['fire_id']))
    new = {k: v for k, v in k_old.items() if k not in ('q', 'k_analogs')}
    new.update(forecasts=idx, upper_method='size-calibrated worst case: sized to capture ~80% of growth for fires of this size; scripts/worstcase_calibrate.py',
               worst_case_params=cal, note='lower = area-constrained growth model; upper = size-calibrated worst case (entry 31)')
    (ROOT / gb.OUTDIR / 'index.json').write_text(json.dumps(new, indent=1))
    shutil.rmtree(ROOT / gb.B / 'tmp', ignore_errors=True)
    log.info('exported %d', len(idx))


if __name__ == '__main__':
    os.chdir(ROOT)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(OUT / 'worstcase.log')], force=True)
    {'classes': cmd_classes, 'calibrate': cmd_calibrate, 'heldout': cmd_heldout, 'export': cmd_export}[sys.argv[1]]()
