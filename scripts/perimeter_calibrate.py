#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Leave-one-fire-out test of fuel- and wind-dependent spread corrections (perimeter_batch.py members).

For each scored fire (FEDS growth observed by the scoring horizon), a member is chosen using only the OTHER fires:
  knn    the member with the best mean growth IoU over the k nearest other fires in feature space
         (near-edge GR/SH/TIMBER fractions, mean HRRR wind, log initial burning area; z-scored on the other fires)
  ridge  per member, ridge regression of growth IoU on the same features; pick the highest prediction
Compared with: no growth (persistence), the single fixed member best on the other fires, and the oracle member
(best on the fire itself, in-sample by construction).
Usage: perimeter_calibrate.py [--horizon 24] [--k 3] [--alpha 1] [--min-observed 1]
Writes data/perimeter_batch/calibration.json and prints a markdown table.
"""
import argparse, json, logging
from pathlib import Path
import numpy as np

BASE = Path('data/perimeter_batch')
log = logging.getLogger(__name__)


def load(horizon, min_observed):
    fires = []
    for path in sorted(BASE.glob('map*/scores.json')):
        d = json.loads(path.read_text())
        if 'scores' not in d or d.get('member_set') != 'fuelwind16':
            continue
        sc = d['scores']
        # Score at the latest FEDS outline at or before the horizon (no interpolation of truth).
        row = {}
        for name, obs in sc.items():
            obs = [o for o in obs if o['hours'] <= horizon]
            if obs:
                row[name] = max(obs, key=lambda o: o['hours'])
        if not row:
            continue
        o = next(iter(row.values()))
        if o['observed_growth_cells'] < min_observed:
            continue
        f = d['features']
        ff = f['fuel_fraction_near_edge']
        if ff['GR'] is None:
            continue
        x = [ff['GR'], ff['SH'], ff['TIMBER'], f['mean_wind_mph'], np.log10(f['initial_burning_acres'] + 1)]
        fires.append({'fire': path.parent.name, 'x': x, 'hours': o['hours'], 'observed_growth_cells':
                      o['observed_growth_cells'], 'scores': row})
    return fires


def metric(o):
    g = o['growth_iou'] or 0.0
    return g, o['area_ratio'], o['iou']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--horizon', type=float, default=24)
    ap.add_argument('--k', type=int, default=3)
    ap.add_argument('--alpha', type=float, default=1.0)
    ap.add_argument('--min-observed', type=int, default=1)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s')
    fires = load(a.horizon, a.min_observed)
    members = sorted(set.intersection(*[set(f['scores']) for f in fires])) if fires else []
    if len(fires) < 3:
        raise SystemExit(f'{len(fires)} scored fires; need at least 3')
    X = np.array([f['x'] for f in fires], float)
    G = np.array([[metric(f['scores'][m])[0] for m in members] for f in fires])
    rows = []
    for i, f in enumerate(fires):
        o = np.arange(len(fires)) != i
        mu, sd = X[o].mean(0), X[o].std(0) + 1e-9
        Z, z = (X[o] - mu) / sd, (X[i] - mu) / sd
        nn = np.argsort(((Z - z) ** 2).sum(1))[:a.k]
        knn = members[int(np.argmax(G[o][nn].mean(0)))]
        A = np.c_[np.ones(len(Z)), Z]
        reg = a.alpha * np.eye(A.shape[1]); reg[0, 0] = 0
        W = np.linalg.solve(A.T @ A + reg, A.T @ G[o])
        ridge = members[int(np.argmax(np.r_[1, z] @ W))]
        fixed = members[int(np.argmax(G[o].mean(0)))]
        oracle = members[int(np.argmax(G[i]))]
        r = {'fire': f['fire'], 'hours': f['hours'], 'observed_growth_cells': f['observed_growth_cells'],
             'features': dict(zip(['GR', 'SH', 'TIMBER', 'wind_mph', 'log10_init_acres'], f['x']))}
        for label, m in [('knn', knn), ('ridge', ridge), ('fixed', fixed), ('oracle', oracle)]:
            g, ar, iou = metric(f['scores'][m])
            r[label] = {'member': m, 'growth_iou': g, 'area_ratio': ar, 'iou': iou}
        r['no_growth'] = {'growth_iou': 0.0, 'area_ratio': 0.0,
                          'iou': next(iter(f['scores'].values()))['no_growth_iou']}
        rows.append(r)
    out = {'horizon_h': a.horizon, 'k': a.k, 'ridge_alpha': a.alpha, 'members': members, 'fires': rows, 'summary': {}}
    print(f'| method (n={len(rows)} fires, <= +{a.horizon:g} h) | mean growth IoU | median area ratio | '
          'median abs log2 area ratio | mean whole IoU |')
    print('|---|---|---|---|---|')
    for label in ['no_growth', 'fixed', 'knn', 'ridge', 'oracle']:
        g = np.array([r[label]['growth_iou'] for r in rows])
        ar = np.array([r[label]['area_ratio'] for r in rows], float)
        iou = np.array([r[label]['iou'] for r in rows], float)
        lar = np.abs(np.log2(np.maximum(ar, 1e-3)))
        s = {'mean_growth_iou': float(g.mean()), 'median_area_ratio': float(np.median(ar)),
             'median_abs_log2_area_ratio': float(np.median(lar)), 'mean_iou': float(np.nanmean(iou)),
             'held_out': label in ('fixed', 'knn', 'ridge')}
        out['summary'][label] = s
        tag = {'oracle': ' (in-sample)', 'no_growth': ''}.get(label, ' (held-out)')
        print(f"| {label}{tag} | {s['mean_growth_iou']:.3f} | {s['median_area_ratio']:.2f} | "
              f"{s['median_abs_log2_area_ratio']:.2f} | {s['mean_iou']:.3f} |")
    (BASE / 'calibration.json').write_text(json.dumps(out, indent=1) + '\n')


if __name__ == '__main__':
    main()
