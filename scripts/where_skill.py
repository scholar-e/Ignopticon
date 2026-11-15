#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Does ELMFIRE know WHERE a fire grows, beyond naive rules? Ranks candidate cells, ignores how much grows.

Usage: where_skill.py manifest   (freezes the fire list from eligibility only; no ranking, no skill computed)
       where_skill.py score      (needs manifest.json; writes tables + figure in data/where_skill/)
Candidates: burnable (FBFM40 > 100) cells outside the issue-time start footprint (FEDS perimeter at issue | ELMFIRE
toa == 0) inside DOMAIN = (cells ELMFIRE reached in its 12 h run) | (distance to start <= 2 x max observed advance).
Observed = FEDS growth cells at issue + 12 h (truth & ~start & candidate). Methods rank the same candidate set:
  elmfire  arrival time (earlier first); unburned by +12 h last; ties by distance to start
  buffer   distance to start (nearest first)
  downwind distance * (1 - e*cos(theta)), theta = angle between (nearest start cell -> candidate) and the mean
           downwind direction (domain mean HRRR 20-ft wind vector over 12 h), e = sqrt(1 - 1/LB^2), LB from the
           Canadian FBP length-to-breadth ratio of the mean wind speed (the polar form of an ellipse with the
           ignition at its rear focus: spread rate in direction theta ~ 1/(1 - e cos theta)). Frozen, no tuning.
  ellipse  scripts/ellipse_spread.py needs a GFS wind JSON and a state file in a different pipeline; the downwind
           method is its distance geometry, so it is not run separately.
Metrics: AUC (observed vs other candidates); with N = observed cells, recall of top-N (= precision) and IoU
I/(2N-I). Paired bootstrap (10000 resamples of fires) of ELMFIRE minus baseline.
"""
import json
import logging
import math
import sys
from pathlib import Path
import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt
from scipy.stats import rankdata
sys.path.insert(0, str(Path(__file__).parent))
from front_calibrate import FIRES, utc
from selfcal_adj import load_toa, raster_of
import datetime as dt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data/where_skill'
ISSUE = '2026-08-02T12:00'
DEV = ['2021-038', '2021-031', '2020-008', 'map20260802-66510']
METHODS = ['elmfire', 'buffer', 'downwind']
log = logging.getLogger('where_skill')


def run_dir(fire):
    """(outputs dir, source label) of the adj1 / control ELMFIRE run, or None."""
    c = [(ROOT / f'data/edge_causal/front_weighted/dev/{fire}/control/outputs', 'dev control')] if fire in DEV[:3] else [
        (ROOT / f'data/demo_pull/runs/{fire}/runs/physics3/adj1/outputs', 'demo_pull adj1'),
        (ROOT / f'data/perimeter_batch/aug2_local/{fire}/adj1/outputs', 'aug2_local adj1')]
    for p, s in c:
        if (p / 'run.json').exists():
            return p, s
    return None


def heldout_dir(fire):
    p = ROOT / f'data/edge_causal/front_weighted/heldout/{fire}/control/outputs'
    return p if (p / 'run.json').exists() else None


def fire_inputs(fire):
    if fire in FIRES and fire in DEV:
        f = FIRES[fire]
        return f['issue'], ROOT / f['observed'], ROOT / f['fc'][0]
    wx = ROOT / f'data/training/fires/{fire}/wx-subh/20260802T12'
    if not (wx / 'ws.tif').exists():  # same HRRR 2026-08-02T12 sub-hourly forecast, rebuilt for the held-out batch
        wx = ROOT / f'data/edge_causal/front_weighted/heldout/{fire}/wx'
    return ISSUE, ROOT / f'data/training/fires/{fire}/fire-observed.json', wx


def landscape_path(fire):
    return ROOT / f'data/training/fires/{fire}/landscape.tif'


def truth_start(fire, toa_shape, transform, crs):
    issue, obs_path, _ = fire_inputs(fire)
    if fire == 'map20260802-66510':
        obs_path = ROOT / 'data/perimeter_batch/map20260802-66510/associated-observed.json'
    obs = json.loads(obs_path.read_text())
    T = utc(issue)
    per = {utc(p['time']): p['geometry'] for p in obs['perimeters']}
    pre = [t for t in per if t <= T]
    end = T + dt.timedelta(hours=12)
    if not pre or end not in per:
        return None
    return raster_of(per[max(pre)], toa_shape, transform, crs), raster_of(per[end], toa_shape, transform, crs)


def prepare(fire, outputs):
    """Everything about a fire that does not involve ranking: grids, candidates, observed, wind."""
    toa, valid, transform, crs, res, _ = load_toa(outputs)
    ts = truth_start(fire, toa.shape, transform, crs)
    if ts is None:
        return None
    start0, truth = ts
    start = start0 | (valid & (toa == 0))
    with rasterio.open(landscape_path(fire)) as src:
        if src.shape != toa.shape:
            raise ValueError('landscape grid differs from run grid')
        burnable = src.read(4) > 100
    dist, idx = distance_transform_edt(~start, sampling=res, return_indices=True)
    growth = truth & ~start
    reach = valid & (toa > 0) & (toa <= 12 * 3600)
    base = burnable & ~start
    obs_all = growth & base
    maxadv = float(dist[obs_all].max()) if obs_all.any() else 0.0
    domain = base & (reach | (dist <= 2 * maxadv))
    return dict(toa=toa, valid=valid, res=res[0], start=start, truth=truth, dist=dist, idx=idx, reach=reach,
                domain=domain, obs=obs_all & domain, obs_growth_cells=int(growth.sum()), maxadv=maxadv)


def wind(fire):
    _, _, wxdir = fire_inputs(fire)
    with rasterio.open(Path(wxdir) / 'ws.tif') as s:
        ws = s.read()[:49]
    with rasterio.open(Path(wxdir) / 'wd.tif') as s:
        wd = s.read()[:49]
    th = np.deg2rad(wd)  # from-direction; the air moves toward th+180: u(east) = -ws sin, v(north) = -ws cos
    u, v = float((-ws * np.sin(th)).mean()), float((-ws * np.cos(th)).mean())
    return u, v, float(ws.mean())


def length_to_breadth(wind_kmh):
    return 1.0 + 8.729 * (1 - math.exp(-0.030 * wind_kmh)) ** 2.155


def rank_keys(P, fire):
    """Returns {method: score array over domain cells, higher = more likely}; deterministic, no fitted parameters."""
    m = P['domain']
    rr, cc = np.nonzero(m)
    d = P['dist'][m]
    out = {'buffer': -d}
    toa = P['toa'][m].astype(float)
    burned = P['reach'][m]
    # elmfire: burned cells by arrival (earlier = higher), then unburned by distance; ties by distance
    order = np.lexsort((d, np.where(burned, toa, np.inf)))
    r = np.empty(len(d)); r[order] = -np.arange(len(d), dtype=float)
    out['elmfire'] = r
    u, v, ws_mph = wind(fire)
    LB = length_to_breadth(ws_mph * 1.609)
    e = math.sqrt(1 - 1 / LB ** 2)
    nr, nc = P['idx'][0][m], P['idx'][1][m]
    dx, dy = (cc - nc).astype(float), -(rr - nr).astype(float)  # east, north
    nrm = np.hypot(dx, dy); nrm[nrm == 0] = 1
    sp = math.hypot(u, v) or 1.0
    cos = (dx * u + dy * v) / nrm / sp
    out['downwind'] = -(d * (1 - e * cos))
    return out, dict(mean_wind_mph=ws_mph, wind_u=u, wind_v=v, downwind_bearing=(math.degrees(math.atan2(u, v)) % 360), LB=LB, e=e)


def metrics(score, obs):
    n = int(obs.sum()); tot = len(obs)
    rk = rankdata(score)
    auc = float((rk[obs].sum() - n * (n + 1) / 2) / (n * (tot - n)))
    top = np.argsort(-score, kind='stable')[:n]
    I = int(obs[top].sum())
    return dict(auc=auc, recall=I / n, iou=I / (2 * n - I))


def evaluate(fire, outputs):
    P = prepare(fire, outputs)
    if P is None or not P['obs'].any():
        return None
    keys, wi = rank_keys(P, fire)
    obs = P['obs'][P['domain']]
    res = {k: metrics(v, obs) for k, v in keys.items()}
    return dict(methods=res, n_obs=int(obs.sum()), n_candidates=int(len(obs)), obs_fraction=float(obs.mean()),
                max_advance_m=P['maxadv'], elmfire_reach_cells=int(P['reach'].sum()),
                elmfire_hits=int((P['reach'] & P['obs']).sum()), wind=wi, cell_m=P['res'])


def eligible(fire, cont):
    if fire in DEV:
        return True, 'dev'
    st = cont.get(fire.split('-')[-1], {}).get('status')
    return st == 'uncontained', st


def cmd_manifest():
    cont = json.loads((ROOT / 'data/containment/aug2_containment.json').read_text())['fires']
    fires = sorted(p.name for p in (ROOT / 'data/training/fires').glob('map20260802-*')) + DEV[:3]
    man = dict(frozen_by='scripts/where_skill.py manifest, before any ranking or skill was computed',
               issue=ISSUE, horizon_h=12, primary=[], dev=[], excluded={})
    for f in sorted(set(fires)):
        ok, why = eligible(f, cont)
        if not ok:
            man['excluded'][f] = f'containment status {why}'
            continue
        rd = run_dir(f)
        if rd is None:
            man['excluded'][f] = 'no adj1 run'
            continue
        try:
            P = prepare(f, rd[0])
        except Exception as ex:
            man['excluded'][f] = f'prepare failed: {ex}'
            continue
        if P is None:
            man['excluded'][f] = 'no exact +12 h FEDS perimeter or none at/before issue'
            continue
        if not P['obs'].any():
            man['excluded'][f] = 'no observed burnable growth in domain at +12 h'
            continue
        if fire_inputs(f)[2] is None or not (fire_inputs(f)[2] / 'ws.tif').exists():
            man['excluded'][f] = 'no HRRR weather files'
            continue
        e = dict(fire=f, run=str(rd[0].relative_to(ROOT)), source=rd[1], observed_cells=int(P['obs'].sum()),
                 candidate_cells=int(P['domain'].sum()), cell_m=P['res'])
        h = heldout_dir(f)
        if h:
            e['heldout_control_run'] = str(h.relative_to(ROOT))
        (man['dev'] if f in DEV else man['primary']).append(e)
    (OUT / 'manifest.json').write_text(json.dumps(man, indent=1) + '\n')
    log.info('primary %d dev %d excluded %d', len(man['primary']), len(man['dev']), len(man['excluded']))


def boot(diff, rng, B=10000):
    diff = np.asarray(diff)
    if len(diff) < 2:
        return None
    m = rng.choice(diff, (B, len(diff))).mean(1)
    return float(diff.mean()), float(np.quantile(m, .025)), float(np.quantile(m, .975))


def summarize(res, label, rng):
    lines = [f'### {label} (n={len(res)})', '', '| Metric | ELMFIRE mean/med | buffer mean/med | downwind mean/med | ELM-buffer (95% CI) | ELM-downwind (95% CI) | fires ELM>buffer / >downwind |', '|---|---|---|---|---|---|---|']
    js = {}
    for met, name in [('auc', 'AUC'), ('recall', 'Recall at observed area'), ('iou', 'Growth IoU at observed area')]:
        cell, row = {}, {}
        for m in METHODS:
            x = np.array([r['methods'][m][met] for r in res.values()])
            cell[m] = x
            row[m] = f'{x.mean():.3f}/{np.median(x):.3f}'
        cis = {}
        for b in ('buffer', 'downwind'):
            c = boot(cell['elmfire'] - cell[b], rng)
            cis[b] = c
        f = lambda c: '-' if c is None else f'{c[0]:+.3f} [{c[1]:+.3f}, {c[2]:+.3f}]'
        wins = [int((cell['elmfire'] > cell[b]).sum()) for b in ('buffer', 'downwind')]
        lines.append(f"| {name} | {row['elmfire']} | {row['buffer']} | {row['downwind']} | {f(cis['buffer'])} | {f(cis['downwind'])} | {wins[0]} / {wins[1]} of {len(res)} |")
        js[met] = dict(means={m: float(cell[m].mean()) for m in METHODS}, ci=cis)
    return lines + [''], js


def cmd_score():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    man = json.loads((OUT / 'manifest.json').read_text())
    rng = np.random.default_rng(0)
    out = {'primary': {}, 'dev': {}, 'heldout_control': {}}
    for grp in ('primary', 'dev'):
        for e in man[grp]:
            r = evaluate(e['fire'], ROOT / e['run'])
            out[grp][e['fire']] = r
            if 'heldout_control_run' in e:
                out['heldout_control'][e['fire']] = evaluate(e['fire'], ROOT / e['heldout_control_run'])
            log.info('%s %s', e['fire'], {m: round(v['recall'], 3) for m, v in r['methods'].items()})
    md = ['# Where skill: does ELMFIRE rank growth locations better than a buffer or a downwind rule?', '',
          'Candidates and observed growth defined in scripts/where_skill.py (docstring). 12 h, issue 2026-08-02T12 (or the dev issue).', '']
    js = {}
    for grp, label in [('primary', 'Uncontained, incident-matched Aug 2 fires (adj1 run)'), ('dev', 'Dev / front-test fires (in-sample-ish for method choices)'),
                       ('heldout_control', 'Sensitivity: same Aug 2 fires that have a held-out control-arm run, scored with that run')]:
        res = {k: v for k, v in out[grp].items() if v}
        if len(res) < 1:
            continue
        l, j = summarize(res, label, rng)
        md += l; js[grp] = j
        md += ['| Fire | obs cells | candidates | obs frac | max adv km | mean wind mph | LB | AUC E/B/D | Recall E/B/D | IoU E/B/D |', '|---|---|---|---|---|---|---|---|---|---|']
        for k, r in res.items():
            f = lambda met: '/'.join(f"{r['methods'][m][met]:.2f}" for m in METHODS)
            md.append(f"| {k} | {r['n_obs']} | {r['n_candidates']} | {r['obs_fraction']:.3f} | {r['max_advance_m']/1000:.1f} | {r['wind']['mean_wind_mph']:.1f} | {r['wind']['LB']:.2f} | {f('auc')} | {f('recall')} | {f('iou')} |")
        md.append('')
    (OUT / 'results.md').write_text('\n'.join(md) + '\n')
    (OUT / 'results.json').write_text(json.dumps(dict(per_fire=out, summary=js), indent=1) + '\n')
    # figure
    cols = {'elmfire': '#d95f02', 'buffer': '#7570b3', 'downwind': '#1b9e77'}
    groups = [(g, out[g]) for g in ('primary', 'dev') if out[g]]
    fig, axes = plt.subplots(2, 1, figsize=(max(9, 0.5 * sum(len(v) for _, v in groups) + 3), 8), gridspec_kw=dict(height_ratios=[3, 1.4]))
    ax = axes[0]
    names, x = [], 0
    for g, d in groups:
        for k, r in d.items():
            for i, m in enumerate(METHODS):
                ax.bar(x + (i - 1) * 0.27, r['methods'][m]['recall'], 0.27, color=cols[m], label=m if x == 0 else None)
            names.append(k.replace('map20260802-', '') + ('*' if g == 'dev' else ''))
            x += 1
        x += 0.6
    pos = []
    x = 0
    for g, d in groups:
        for _ in d:
            pos.append(x); x += 1
        x += 0.6
    ax.set_xticks(pos); ax.set_xticklabels(names, rotation=60, ha='right')
    ax.set_ylabel('Recall of observed growth at observed area'); ax.legend(); ax.set_title('Per fire (* = dev / front-test fires)')
    ax = axes[1]
    labels, k = [], 0
    for g, d in groups:
        for met, off in (('recall', 0), ('auc', 1)):
            for i, m in enumerate(METHODS):
                v = [r['methods'][m][met] for r in d.values()]
                ax.bar(k + i * 0.27, np.mean(v), 0.27, color=cols[m])
                ax.errorbar(k + i * 0.27, np.mean(v), yerr=np.std(v) / math.sqrt(len(v)), color='k', capsize=2)
            labels.append(f'{g} {met} (n={len(d)})'); k += 1.2
    ax.set_xticks([i * 1.2 + 0.27 for i in range(len(labels))]); ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel('Mean (bar: +-1 s.e.)')
    fig.tight_layout(); fig.savefig(OUT / 'figures/where_skill.png', dpi=110)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(OUT / 'where_skill.log')], force=True)
    {'manifest': cmd_manifest, 'score': cmd_score}[sys.argv[1]]()
