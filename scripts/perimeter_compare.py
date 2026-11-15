#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compare the physics3 batch (FEDS-size start, sub-hourly HRRR, fuel moisture; 12 h) with the fuelwind16 batch and
no growth, from data/perimeter_batch/map*/scores.json (physics3) and scores.fuelwind16.json.

At each FEDS outline within HOURS of issue: physics3 members; fuelwind16 members rescored on the same FEDS start
footprint and truth (scores.json baseline_fuelwind16_rescored: mean of members, best single member over fires
[in-sample], per-fire oracle); fuelwind16 as originally scored (own 375 m start); no growth. VIIRS CSI: mean over
heat_frames.py overpasses within HOURS. Writes data/perimeter_batch/physics3_compare.{md,json}.
Usage: perimeter_compare.py
"""
import json, logging, statistics as st, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)
BASE = Path('data/perimeter_batch')
METRICS = ('growth_iou', 'iou', 'area_ratio', 'boundary_mean_m')


def flat(s):
    return {'growth_iou': s['growth_iou'], 'iou': s['iou'], 'area_ratio': s['area_ratio'],
            'no_growth_iou': s['no_growth_iou'], 'boundary_mean_m': (s['boundary'] or {}).get('mean_m'),
            'observed_growth_cells': s['observed_growth_cells']}


def mean(xs, median=False):
    xs = [x for x in xs if x is not None]
    return (st.median(xs) if median else st.mean(xs)) if xs else None


def csi(v):
    return mean([o['csi'] for o in v]) if isinstance(v, list) and v else None


def main():
    rows = {}  # (fire, hours) -> {method: metrics}
    fires, viirs = [], {}
    for d in sorted(BASE.glob('map*')):
        f = d / 'scores.json'
        if not f.exists():
            continue
        s = json.loads(f.read_text())
        if s.get('member_set') != 'physics3':
            continue
        status = json.loads((d / 'status.json').read_text())
        init = s.get('init') or {}
        old = json.loads((d / 'scores.fuelwind16.json').read_text()) if (d / 'scores.fuelwind16.json').exists() else None
        for name, obs in s['scores'].items():
            for o in obs:
                rows.setdefault((d.name, o['hours']), {})['p3:' + name] = flat(o)
        for name, obs in s.get('baseline_fuelwind16_rescored', {}).items():
            for o in obs:
                rows.setdefault((d.name, o['hours']), {})['fw16:' + name] = flat(o)
        if old:
            for name, obs in old['scores'].items():
                for o in obs:
                    if (d.name, o['hours']) in rows:
                        rows[(d.name, o['hours'])]['fw16orig:' + name] = flat(o)
        v = s.get('viirs') or {}
        if isinstance(v, dict) and 'error' not in v:
            viirs[d.name] = {k: csi(x) for k, x in v.items() if x}
        fires.append({'fire': d.name, 'init': init.get('mode'), 'active_mode': init.get('active_mode'),
                      'feds_acres_issue': init.get('feds_polygon_acres'), 'raster_acres': init.get('feds_raster_acres'),
                      'perimeter_hours': sorted({o['hours'] for obs in s['scores'].values() for o in obs}),
                      'wx': (status.get('wx_used') or {}).get('source')})
    table = {}
    for hours in sorted({h for _, h in rows}):
        keys = [k for k in rows if k[1] == hours]
        scored = [k for k in keys if rows[k][next(iter(rows[k]))]['observed_growth_cells'] > 0]
        methods = sorted({m for k in keys for m in rows[k]})
        out = {}
        for m in [m for m in methods if m.startswith('p3:')]:
            out[m] = {x: mean([rows[k][m][x] for k in scored if m in rows[k]], x == 'area_ratio') for x in METRICS}
        for prefix in ('fw16:', 'fw16orig:'):
            mm = [m for m in methods if m.startswith(prefix)]
            if not mm:
                continue
            have = [k for k in scored if any(m in rows[k] for m in mm)]
            out[prefix + 'mean_of_members'] = {x: mean([mean([rows[k][m][x] for m in mm if m in rows[k]]) for k in have],
                                                       x == 'area_ratio') for x in METRICS}
            best = max(mm, key=lambda m: mean([rows[k].get(m, {}).get('growth_iou') for k in have]) or -1)
            out[prefix + 'best_member_in_sample(' + best.split(':')[1] + ')'] = {
                x: mean([rows[k][best][x] for k in have if best in rows[k]], x == 'area_ratio') for x in METRICS}
            out[prefix + 'oracle_per_fire'] = {'growth_iou': mean([max((rows[k][m]['growth_iou'] or 0) for m in mm if m in rows[k])
                                                                   for k in have])}
        out['no_growth'] = {'iou': mean([rows[k][next(iter(rows[k]))]['no_growth_iou'] for k in scored]),
                            'growth_iou': 0.0}
        table[hours] = {'fires_scored': len(scored), 'fires_with_outline': len(keys), 'methods': out}
    vtab = {}
    for fire, v in viirs.items():
        for k, x in v.items():
            vtab.setdefault(('p3:' if k.startswith('adj') else 'fw16:') + k, []).append(x)
    vmean = {k: (round(mean(x), 3), len(x)) for k, x in sorted(vtab.items())}
    per_fire = []
    for f in fires:
        for (fire, hours), r in rows.items():
            if fire != f['fire']:
                continue
            p3 = {m.split(':')[1]: r[m]['growth_iou'] for m in r if m.startswith('p3:')}
            fw = [r[m]['growth_iou'] or 0 for m in r if m.startswith('fw16:')]
            per_fire.append({**f, 'hours': hours, 'observed_growth_cells': r[next(iter(r))]['observed_growth_cells'],
                             'no_growth_iou': r[next(iter(r))]['no_growth_iou'], 'p3_growth_iou': p3,
                             'p3_adj1_area_ratio': r.get('p3:adj1', {}).get('area_ratio'),
                             'fw16_rescored_best_growth_iou': max(fw) if fw else None,
                             'viirs_csi': viirs.get(fire)})
    (BASE / 'physics3_compare.json').write_text(json.dumps({'table': table, 'viirs_mean_csi': vmean, 'fires': fires,
                                                            'per_fire': per_fire}, indent=1, default=str) + '\n')
    r = lambda x: '-' if x is None else ('%.3f' % x if abs(x) < 10 else '%.0f' % x)
    lines = ['# physics3 vs fuelwind16 vs no growth (Aug 2 2026 12Z issue)', '',
             'Fires with observed growth only; mean over fires (area ratio: median). fw16 = fuelwind16 rescored on the '
             'FEDS start footprint; fw16orig = as originally scored (375 m start). best_member is chosen in-sample.', '']
    for hours, t in table.items():
        lines += ['## +%g h: %d fires scored (%d with an outline)' % (hours, t['fires_scored'], t['fires_with_outline']), '',
                  '| method | growth IoU | whole IoU | area ratio | boundary mean m |', '|---|---|---|---|---|']
        lines += ['| %s | %s | %s | %s | %s |' % (m, r(v.get('growth_iou')), r(v.get('iou')), r(v.get('area_ratio')),
                                                r(v.get('boundary_mean_m'))) for m, v in t['methods'].items()]
        lines.append('')
    lines += ['## VIIRS CSI (heat_frames.py, overpasses within 12 h; mean over fires, n fires)', '']
    lines += ['- %s: %s (n=%d)' % (k, v[0], v[1]) for k, v in vmean.items() if k.startswith('p3:')]
    fwv = [v[0] for k, v in vmean.items() if k.startswith('fw16:')]
    if fwv:
        lines.append('- fw16 mean of members: %.3f, best member %.3f' % (st.mean(fwv), max(fwv)))
    lines += ['', '## Per fire', '', '| fire | h | init / front | FEDS acres | obs growth cells | no-growth IoU | '
              'growth IoU adj1 / adj0.5 / adj1-w0.7 | adj1 area ratio | fw16 best | ', '|---|---|---|---|---|---|---|---|---|']
    for p in sorted(per_fire, key=lambda p: p['fire']):
        g = p['p3_growth_iou']
        lines.append('| %s | %g | %s/%s | %s | %d | %s | %s / %s / %s | %s | %s |' % (
            p['fire'].split('-')[1], p['hours'], p['init'], p['active_mode'], r(p['feds_acres_issue']),
            p['observed_growth_cells'], r(p['no_growth_iou']), r(g.get('adj1')), r(g.get('adj0.5')),
            r(g.get('adj1-wind0.7')), r(p['p3_adj1_area_ratio']), r(p['fw16_rescored_best_growth_iou'])))
    (BASE / 'physics3_compare.md').write_text('\n'.join(lines) + '\n')
    log.info('wrote %s', BASE / 'physics3_compare.md')


if __name__ == '__main__':
    main()
