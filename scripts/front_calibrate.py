#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Choose the active burning front per fire by calibrating on burning-edge hotspots (MODELLING_LOG.md entry 18).

ELMFIRE INIT=feds starts from the FEDS perimeter known at issue T; which edge segments burn is a front rule
(elmfire_case.py FRONT_KM / ACTIVE_HOURS / FRONT_RFILE+FRONT_R / FRONT=whole). Rules (RULES below):
    d6_375m        detections in the last 6 h within one 375 m state cell (the physics3 default)
    dH_D           detections in the last H h (6, 12, 24) within D km (0.75, 1.5) of the edge
    d6_0.75+rR     d6_0.75, plus edge cells where the hindcast correction r (selfcal_adj.py) >= R (0.8, 1.0)
    whole          the whole edge band
Every detection rule falls back to the whole band if it selects no edge cell (elmfire_case.py).

Calibration (past only): hindcast each rule from the FEDS perimeter at T-12 h to T, same inputs and start mode as
the forecast. Targets = VIIRS cells detected in (T-12 h, T] whose centre lies outside the start perimeter (the
burning-edge hotspots; no cloud mask). On the 375 m state grid: a target is hit if a modelled newly burned cell
lies within one cell (3x3); a modelled cell with no target within one cell is a false alarm; CSI = hits / (targets
+ false alarms). Score = hotspot CSI + growth IoU vs FEDS at T; the best rule is chosen (ties: earlier rule).
The r-rules need r from before the calibration window: a whole-edge hindcast (T-24 h, T-12 h] from the FEDS
perimeter at T-24 h gives r_prev for calibrating them, and the whole-edge calibration hindcast gives r for the
forecast. Without a FEDS perimeter at T-24 h the r-rules are not eligible (their forecast is still run).
Forecast: every rule from T for 12 h; scores vs FEDS at T+12 h (perimeter_batch.scores) and hotspots in
(T, T+12 h] outside the T perimeter; no growth; oracle = best rule on the forecast window (in-sample).

Usage: front_calibrate.py FIRE [FIRE ...]   (fires configured in FIRES below)
Writes data/perimeter_batch/front/FIRE/{prev,hind,fc}/RULE (ELMFIRE cases, not committed), front_FIRE.json there
and data/perimeter_batch/figures/front_FIRE.png. Environment: JOBS (12) parallel ELMFIRE runs.
"""
import datetime as dt, json, logging, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import rasterio
from scipy.ndimage import binary_dilation
from shapely.geometry import shape, Point
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from perimeter_batch import scores  # noqa: E402
from selfcal_adj import correction_map, load_toa, raster_of, utc, iso  # noqa: E402

log = logging.getLogger(__name__)
JOBS = int(os.environ.get('JOBS', '12'))
OUT = Path('data/perimeter_batch/front')
FIG = Path('data/perimeter_batch/figures')
F = 'data/training/fires/'
# wx: (dir, WXSTART band) for the window starting at T-24 (prev), T-12 (hind), T (fc).
FIRES = {
    'map20260802-66510': dict(label='Widemouth 66510 (UT)', issue='2026-08-02T12:00',
                              observed='data/perimeter_batch/map20260802-66510/associated-observed.json',
                              prev=None, hind=('data/perimeter_batch/selfcal/66510/wx-hind', 0),
                              fc=(F + 'map20260802-66510/wx-subh/20260802T12', 0)),
    '2021-038': dict(label='2021-038 (AZ grass/timber)', issue='2021-06-27T12:00', observed=F + '2021-038/fire-observed.json',
                     prev=(F + '2021-038/wx/20210626T12', 0), hind=(F + '2021-038/wx/20210626T12', 12),
                     fc=(F + '2021-038/wx/20210627T12', 0)),
    '2021-031': dict(label='2021-031 (CA shrub)', issue='2021-07-09T12:00', observed=F + '2021-031/fire-observed.json',
                     prev=(F + '2021-031/wx/20210708T12', 0), hind=(F + '2021-031/wx/20210708T12', 12),
                     fc=(F + '2021-031/wx/20210709T12', 0)),
    '2020-008': dict(label='2020-008 (CA grass/shrub)', issue='2020-08-19T12:00', observed=F + '2020-008/fire-observed.json',
                     prev=(F + '2020-008/wx/20200818T12', 0), hind=(F + '2020-008/wx/20200818T12', 12),
                     fc=(F + '2020-008/wx/20200819T12', 0)),
}
RULES = {
    'd6_375m': [],
    'd6_0.75': ['ACTIVE_HOURS=6', 'FRONT_KM=0.75'], 'd6_1.5': ['ACTIVE_HOURS=6', 'FRONT_KM=1.5'],
    'd12_0.75': ['ACTIVE_HOURS=12', 'FRONT_KM=0.75'], 'd12_1.5': ['ACTIVE_HOURS=12', 'FRONT_KM=1.5'],
    'd24_0.75': ['ACTIVE_HOURS=24', 'FRONT_KM=0.75'], 'd24_1.5': ['ACTIVE_HOURS=24', 'FRONT_KM=1.5'],
    'd6_0.75+r0.8': ['ACTIVE_HOURS=6', 'FRONT_KM=0.75', 'FRONT_R=0.8'],
    'd6_0.75+r1.0': ['ACTIVE_HOURS=6', 'FRONT_KM=0.75', 'FRONT_R=1.0'],
    'whole': ['FRONT=whole'],
}
R_RULES = [k for k, v in RULES.items() if any(a.startswith('FRONT_R=') for a in v)]


def elmfire(fire, cfg, wx, issue, case, extra):
    root = Path(F) / fire
    if (case / 'outputs' / 'run.json').exists(): return case / 'outputs'
    case.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['bash', 'scripts/elmfire_forecast.sh', str(root / 'landscape.tif'), wx[0], str(root / 'fire-state.tif'),
                        iso(issue), '12', str(case), 'OBSERVED=' + cfg['observed'], 'ADJ=1.0', 'INIT=feds',
                        'LH_MOISTURE_CONTENT=90', 'WXSTART=%d' % wx[1], *extra], capture_output=True, text=True)
    if r.returncode: raise RuntimeError('%s: %s %s' % (case, r.stdout[-2000:], r.stderr[-2000:]))
    return case / 'outputs'


def targets(obs, t0, t1, start_geom_ll):
    """VIIRS cells detected in (t0, t1] with centre outside the start perimeter: [(lon, lat)]."""
    ref, poly, pts = utc(obs['reference']), shape(start_geom_ll), set()
    for f in obs['frames']:
        t = ref + dt.timedelta(seconds=f['seconds'])
        if t0 < t <= t1:
            for lon, lat, *_ in f['cells']:
                if not poly.contains(Point(lon, lat)): pts.add((round(lon, 5), round(lat, 5)))
    return sorted(pts)


def hotspot_scores(pred_growth, pts, transform, crs, state_tf):
    """Tolerant (one 375 m cell) hit rate and CSI of modelled new burning vs target hotspot cells."""
    k = int(round(state_tf.a / transform.a))
    rr, cc = np.nonzero(pred_growth)
    shp = (pred_growth.shape[0] // k + 2, pred_growth.shape[1] // k + 2)
    model = np.zeros(shp, bool); model[rr // k, cc // k] = True
    tgt = np.zeros(shp, bool)
    p = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    for lon, lat in pts:
        x, y = p.transform(lon, lat)
        r, c = int((y - transform.f) / transform.e) // k, int((x - transform.c) / transform.a) // k
        if 0 <= r < shp[0] and 0 <= c < shp[1]: tgt[r, c] = True
    st = np.ones((3, 3), bool)
    hits = int((tgt & binary_dilation(model, st)).sum()); n = int(tgt.sum())
    fa = int((model & ~binary_dilation(tgt, st)).sum())
    return {'targets': n, 'hits': hits, 'false_alarm_cells': fa, 'model_cells': int(model.sum()),
            'hit_rate': hits / n if n else None, 'csi': hits / (n + fa) if (n + fa) else None}


def evaluate(outputs, start_geom, truth_geom, pts, state_tf):
    toa, valid, transform, crs, res, _ = load_toa(outputs)
    start = raster_of(start_geom, toa.shape, transform, crs) | (valid & (toa == 0))
    pred = start | (valid & (toa <= 12 * 3600))
    s = scores(pred, raster_of(truth_geom, toa.shape, transform, crs), start, res)
    s['boundary_mean_m'] = (s.pop('boundary') or {}).get('mean_m')
    s['hotspots'] = hotspot_scores(pred & ~start, pts, transform, crs, state_tf)
    s['score'] = (s['hotspots']['csi'] or 0) + (s['growth_iou'] or 0)
    init = json.loads((outputs.parent / 'case.json').read_text()).get('init', {})
    s['active_mode'] = init.get('active_mode'); s['active_edge_cells'] = init.get('active_edge_cells')
    return s


def run(fire):
    cfg = FIRES[fire]; out = OUT / fire
    T = utc(cfg['issue']); h = dt.timedelta(hours=12)
    obs = json.loads(Path(cfg['observed']).read_text())
    per = {utc(p['time']): p['geometry'] for p in obs['perimeters']}
    with rasterio.open(Path(F) / fire / 'fire-state.tif') as src: state_tf = src.transform
    has_prev = cfg['prev'] is not None and (T - 2 * h) in per
    jobs = {}
    with ThreadPoolExecutor(JOBS) as ex:
        if has_prev: jobs['prev'] = ex.submit(elmfire, fire, cfg, cfg['prev'], T - 2 * h, out / 'prev' / 'whole', RULES['whole'])
        for k, v in RULES.items():
            if k not in R_RULES: jobs['hind', k] = ex.submit(elmfire, fire, cfg, cfg['hind'], T - h, out / 'hind' / k, v)
        res = {k: j.result() for k, j in jobs.items()}
    # r maps: r_prev from (T-24, T-12] for calibrating the r-rules, r from (T-12, T] for the forecast.
    rinfo = {}
    if has_prev:
        rinfo['prev'] = correction_map(res['prev'], obs, T - 2 * h, T - h, per[T - 2 * h], out / 'r_prev.tif')
    rinfo['cur'] = correction_map(res['hind', 'whole'], obs, T - h, T, per[T - h], out / 'r_cur.tif')
    with ThreadPoolExecutor(JOBS) as ex:
        jobs = {}
        for k, v in RULES.items():
            if k in R_RULES:
                if has_prev:
                    jobs['hind', k] = ex.submit(elmfire, fire, cfg, cfg['hind'], T - h, out / 'hind' / k,
                                                v + ['FRONT_RFILE=' + str((out / 'r_prev.tif').resolve())])
                v = v + ['FRONT_RFILE=' + str((out / 'r_cur.tif').resolve())]
            jobs['fc', k] = ex.submit(elmfire, fire, cfg, cfg['fc'], T, out / 'fc' / k, v)
        res.update({k: j.result() for k, j in jobs.items()})
    tg_h = targets(obs, T - h, T, per[T - h]); tg_f = targets(obs, T, T + h, per[T])
    hind = {k: evaluate(res['hind', k], per[T - h], per[T], tg_h, state_tf) for k in RULES if ('hind', k) in res}
    fc = {k: evaluate(res['fc', k], per[T], per[T + h], tg_f, state_tf) for k in RULES}
    chosen = max(hind, key=lambda k: (hind[k]['score'], -list(RULES).index(k)))
    oracle = max(fc, key=lambda k: (fc[k]['score'], -list(RULES).index(k)))
    toa, valid, transform, crs, rs, _ = load_toa(res['fc', 'whole'])
    s0 = raster_of(per[T], toa.shape, transform, crs)
    ng = scores(s0, raster_of(per[T + h], toa.shape, transform, crs), s0, rs)
    ng['boundary_mean_m'] = (ng.pop('boundary') or {}).get('mean_m')
    result = {'fire': fire, 'label': cfg['label'], 'issue': cfg['issue'], 'weather': {k: cfg[k] for k in ('prev', 'hind', 'fc')},
              'r_rules_eligible': has_prev, 'calibration_targets': len(tg_h), 'forecast_targets': len(tg_f),
              'r_sectors': {k: [x['r'] for x in v['sectors']] for k, v in rinfo.items()},
              'hindcast': hind, 'forecast': fc, 'no_growth': ng, 'chosen': chosen, 'oracle': oracle}
    (out / f'front_{fire}.json').write_text(json.dumps(result, indent=1) + '\n')
    figure(fire, cfg, res, per, T, h, chosen, oracle, tg_h, tg_f, hind, fc)
    return result


def figure(fire, cfg, res, per, T, h, chosen, oracle, tg_h, tg_f, hind, fc):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    toa, valid, transform, crs, rs, _ = load_toa(res['fc', chosen])
    m = lambda g: raster_of(g, toa.shape, transform, crs)
    grown = lambda key, s: s | (lambda t, v: v & (t <= 12 * 3600))(*load_toa(res[key])[:2])
    sT = m(per[T])
    panels = [
        ('Calibration hindcast T-12 h -> T (past)', [
            ('FEDS T-12 h (start)', m(per[T - h]), '#888888', '--'), ('FEDS T', sT, '#000000', '-'),
            ('physics3 front (d6_375m) hindcast', grown(('hind', 'd6_375m'), m(per[T - h])), '#ff7f0e', '-'),
            ('chosen %s hindcast' % chosen, grown(('hind', chosen), m(per[T - h])), '#1f77b4', '-')], tg_h),
        ('Forecast T -> T+12 h', [
            ('FEDS T (start)', sT, '#000000', '--'), ('FEDS T+12 h (observed)', m(per[T + h]), '#d62728', '-'),
            ('physics3 front (d6_375m) forecast', grown(('fc', 'd6_375m'), sT), '#ff7f0e', '-'),
            ('chosen %s forecast' % chosen, grown(('fc', chosen), sT), '#1f77b4', '-'),
            ('oracle %s forecast (in-sample)' % oracle, grown(('fc', oracle), sT), '#9467bd', ':')], tg_f)]
    allm = np.any([x[1] for p in panels for x in p[1]], axis=0)
    rr, cc = np.nonzero(allm); pad = 20
    r0, r1, c0, c1 = max(rr.min() - pad, 0), rr.max() + pad, max(cc.min() - pad, 0), cc.max() + pad
    km = abs(transform.a) / 1000
    ext = [0, (c1 - c0) * km, 0, (r1 - r0) * km]
    p = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    fig, ax = plt.subplots(1, 2, figsize=(15, 7))
    for a, (title, layers, pts) in zip(ax, panels):
        hs = []
        for name, mk, col, ls in layers:
            a.contour(mk[r0:r1, c0:c1][::-1].astype(float), levels=[.5], colors=[col], linestyles=ls, linewidths=1.3, extent=ext)
            hs.append(plt.Line2D([], [], color=col, ls=ls, label=name))
        if pts:
            xy = np.array([p.transform(lon, lat) for lon, lat in pts])
            px = (xy[:, 0] - transform.c) / 1000 - c0 * km
            py = (r1 - (xy[:, 1] - transform.f) / transform.e) * km
            a.scatter(px, py, s=4, c='#2ca02c', zorder=3)
            hs.append(plt.Line2D([], [], color='#2ca02c', marker='.', ls='', label='target hotspots (outside start)'))
        a.legend(handles=hs, loc='lower left', fontsize=8); a.set_aspect('equal'); a.set_xlabel('km')
        a.set_xlim(ext[0], ext[1]); a.set_ylim(ext[2], ext[3])
        d = hind if 'hindcast' in title else fc
        a.set_title('%s\nchosen %s: growth IoU %.3f, hotspot CSI %.3f; d6_375m: %.3f, %.3f' % (
            title, chosen, d[chosen]['growth_iou'] or 0, d[chosen]['hotspots']['csi'] or 0,
            d['d6_375m']['growth_iou'] or 0, d['d6_375m']['hotspots']['csi'] or 0), fontsize=9)
    fig.suptitle('%s, T = %s' % (cfg['label'], cfg['issue']))
    fig.tight_layout(); FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / f'front_{fire}.png', dpi=110); plt.close(fig)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s')
    if len(sys.argv) < 2: sys.exit(__doc__)
    for fire in sys.argv[1:]:
        r = run(fire)
        log.info('%s chosen %s oracle %s', fire, r['chosen'], r['oracle'])
