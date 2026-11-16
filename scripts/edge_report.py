#!/usr/bin/env python3
"""Summarize isolated day-t edge runs; missing runs remain explicit."""
import json
import logging
import statistics
from pathlib import Path

ROOT = Path('data/edge_causal')
ARMS = ['raw-pre', 'harm-pre', 'raw-all', 'harm-all', 'viirs-only']


def main():
    ROOT.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / 'report.log')])
    lines = ['# Edge-model comparison', '',
             'Exploratory 2021–2024 test; previously inspected. AP is classifier average precision,',
             'not perimeter accuracy. Seed ranges measure training variability, not confidence intervals.', '',
             '| Arm | Completed seeds | Mean AP | AP range | Mean validation AP |',
             '|---|---|---|---|---|']
    completed = 0
    for arm in ARMS:
        rows = []
        for seed in range(3):
            run = ROOT / 'runs' / f'{arm}-seed{seed}'
            if not (run / 'results.json').exists():
                continue
            data = json.loads((run / 'results.json').read_text())
            assert data['day_t_weather_only'] and data['seed'] == seed, run
            assert (run / 'models' / f'{arm}.txt').is_file(), run
            rows.append(data['arms'][arm])
        completed += len(rows)
        if rows:
            ap = [r['test']['ap'] for r in rows]
            val = [r['val']['ap'] for r in rows]
            lines.append(f'| {arm} | {len(rows)}/3 | {statistics.mean(ap):.5f} | '
                         f'{min(ap):.5f}–{max(ap):.5f} | {statistics.mean(val):.5f} |')
        else:
            lines.append(f'| {arm} | 0/3 | pending | pending | pending |')
    lines += ['', f'{completed}/15 model artifacts and result files present.', '',
              'Soft labels and arm-specific candidate populations remain differences between raw and harmonized arms.',
              'Day-t observed daily weather and next-day clear-sky evaluation masks limit operational interpretation.']
    (ROOT / 'comparison.md').write_text('\n'.join(lines) + '\n')
    logging.info('Wrote %s; %d/15 runs complete', ROOT / 'comparison.md', completed)


if __name__ == '__main__':
    main()
