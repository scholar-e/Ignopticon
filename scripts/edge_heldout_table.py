#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Summary tables for the front_weighted experiments, split by edge-model coverage.

Usage: edge_heldout_table.py ROOT/heldout [ROOT/dev]
Reads SPLIT/scores.json (edge_pilot_score.py) and, for held-out, SPLIT/status.json (covered_fuel_cells). Fires with
covered_fuel_cells = 0 get no model probability anywhere, so every learned arm equals control there; they are
reported as their own subset. Writes ROOT/heldout_table.md and ROOT/heldout_table.json.
"""
import json
import sys
from pathlib import Path
import numpy as np

ORDER = ['no_growth', 'control', 'binary', 'binary_pos', 'weighted', 'band750', 'band2000', 'physics_mw', 'scar_harm', 'scar_raw', 'scar_viirs_only']
METRICS = [('growth_iou', 'Growth IoU', '%.4f'), ('iou', 'Whole IoU', '%.4f'), ('area_ratio', 'Area ratio', '%.2f'),
           ('boundary_mean_m', 'Boundary m', '%.0f'), ('false_growth_fraction', 'False growth', '%.3f')]


def coverage(split, fire, arms):
    f = arms.get('binary', {}).get('front') or {}
    b = (arms.get('band750', {}).get('front') or {}).get('outward_band') or {}
    out = {'edge_coverage': f.get('coverage_fraction'), 'band750_coverage': b.get('band_coverage_fraction')}
    st = split / 'status.json'
    if st.exists():
        out['covered_fuel_cells'] = json.loads(st.read_text())['fires'][fire].get('covered_fuel_cells')
    else:
        out['covered_fuel_cells'] = f.get('covered_fuel_cells')
    return out


def summarise(results, fires, arms):
    rows = {}
    for arm in arms:
        r = {}
        for k, _, _ in METRICS:
            x = [results[f][arm][k] for f in fires if results[f][arm].get(k) is not None]
            r[k] = {'mean': float(np.mean(x)), 'median': float(np.median(x)), 'n': len(x)} if x else None
        r['hotspot_hits'] = sum(results[f][arm]['hotspots']['hits'] for f in fires)
        r['hotspot_targets'] = sum(results[f][arm]['hotspots']['targets'] for f in fires)
        if arm != 'control':
            d = [(results[f][arm]['growth_iou'] or 0) - (results[f]['control']['growth_iou'] or 0) for f in fires]
            r['growth_iou_vs_control'] = {'better': sum(v > 1e-6 for v in d), 'worse': sum(v < -1e-6 for v in d),
                                          'mean_diff': float(np.mean(d)) if d else None}
        rows[arm] = r
    return rows


def md_summary(title, rows, n):
    head = '| Arm | ' + ' | '.join(f'{t} mean / med' for _, t, _ in METRICS) + ' | Hotspot hits | Growth IoU vs control (better/worse, mean diff) |'
    out = [f'### {title} (n={n})', '', head, '|' + '---|' * (len(METRICS) + 3)]
    for arm, r in rows.items():
        cells = [('-' if r[k] is None else f"{f % r[k]['mean']} / {f % r[k]['median']}") for k, _, f in METRICS]
        g = r.get('growth_iou_vs_control')
        gs = '-' if not g else f"{g['better']}/{g['worse']}, {g['mean_diff']:+.4f}"
        out.append(f'| {arm} | ' + ' | '.join(cells) + f" | {r['hotspot_hits']}/{r['hotspot_targets']} | {gs} |")
    return out + ['']


def table(split):
    s = json.loads((split / 'scores.json').read_text())
    results = s['fires']
    arms = [a for a in ORDER if all(a in v for v in results.values())]
    cov = {f: coverage(split, f, v) for f, v in results.items()}
    covered = [f for f in results if (cov[f]['covered_fuel_cells'] or 0) > 0]
    uncovered = [f for f in results if f not in covered]
    subsets = {'all': list(results), 'covered': covered, 'uncovered': uncovered}
    summ = {k: summarise(results, v, arms) for k, v in subsets.items() if v}
    lines = [f'## {split.name}', '', f'Failures: {json.dumps(s["failures"]) if s["failures"] else "none"}. '
             f'Arms: {", ".join(arms)}. Uncovered fires (covered_fuel_cells = 0, learned arms = control): '
             f'{", ".join(uncovered) or "none"}.', '']
    for k, v in summ.items():
        lines += md_summary(f'{split.name}: {k} fires', v, len(subsets[k]))
    lines += ['| Fire | Covered fuel cells | Edge cov | Band750 cov | Arm | ' + ' | '.join(t for _, t, _ in METRICS) + ' | Hotspots |',
              '|' + '---|' * (len(METRICS) + 6)]
    fmt = lambda f, v: '-' if v is None else f % v
    for f, v in results.items():
        c = cov[f]
        for arm in arms:
            x = v[arm]
            lines.append(f"| {f} | {c['covered_fuel_cells']} | {fmt('%.2f', c['edge_coverage'])} | "
                         f"{fmt('%.2f', c['band750_coverage'])} | {arm} | " +
                         ' | '.join(fmt(ff, x.get(k)) for k, _, ff in METRICS) +
                         f" | {x['hotspots']['hits']}/{x['hotspots']['targets']} |")
    return lines + [''], {'arms': arms, 'coverage': cov, 'subsets': subsets, 'summary': summ}


def main():
    splits = [Path(p) for p in sys.argv[1:]]
    root = splits[0].parent
    md, js = ['# Learned front experiments: paired 12-hour ELMFIRE forecasts vs exact +12 h FEDS', '',
              'Held-out: 12 frozen 2026-08-02T12 fires (manifest.json). dev: 3 development fires (in-sample for '
              'the front rules). Metrics from scripts/edge_pilot_score.py.', ''], {}
    for sp in splits:
        l, j = table(sp)
        md += l
        js[sp.name] = j
    (root / 'heldout_table.md').write_text('\n'.join(md) + '\n')
    (root / 'heldout_table.json').write_text(json.dumps(js, indent=1) + '\n')


if __name__ == '__main__':
    main()
