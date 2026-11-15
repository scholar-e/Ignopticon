#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm", "pillow"]
# ///
"""Export trained GBM probabilities for a target date (retrospective weather).
Usage: uv run scripts/compare_day.py 2026-08-02
Writes NPZ, RGBA PNGs and metadata under data/compare/daily/DATE.
"""
import hashlib
import json
import logging
import sys
from pathlib import Path
import numpy as np
import lightgbm as lgb
from PIL import Image
import compare_dataset as D


def main(target):
    date = np.datetime64(target, 'D')
    issue = date - np.timedelta64(1, 'D')
    year = int(str(issue)[:4])
    day = int((issue - np.datetime64(f'{year}-01-01')).astype(int))
    if day < 30 or int(str(date)[:4]) != year:
        raise ValueError('This initial exporter requires 30 prior days in the same year')
    out = Path('data/compare/daily') / str(date)
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(out / 'export.log')], force=True)
    z = np.load(f'data/harmonize/grid/VNP14A1_{year}.npz')
    days = z['day'].astype(int)
    required = np.arange(day - 30, day + 1) + 1
    if not np.isin(required, days).all():
        raise ValueError('Missing satellite input days')
    idx = np.array([np.flatnonzero(days == d)[0] for d in required])
    fire = (z['fire'][idx] > 0).astype(np.float32)
    clear = z['clear'][idx][-1] > 0
    f3, f5 = D.box(fire, 3), D.box(fire, 5)
    since = np.full((D.NY, D.NX), 30, np.float32)
    for a in f3:
        since = np.where(a > 0, 0, np.minimum(since + 1, 30))
    channels = [fire[-1-i] for i in range(3)] + [f3[-1-i] for i in range(3)] + [f5[-1-i] for i in range(3)] + [since, clear]
    weather = np.load(f'data/harmonize/weather/weather_{year}.npz')
    channels += [weather[k][d] for d in (day, day + 1) for k in D.WVARS]
    angle = 2 * np.pi * (day + 1) / D.days_in(year)
    channels += [np.full((D.NY,D.NX), np.sin(angle)), np.full((D.NY,D.NX), np.cos(angle))]
    dynamic = np.stack(channels).astype(np.float16)
    static, names, land = D.load_static()
    # Match the float16 feature representation used during training.
    X = np.concatenate([dynamic, static])[:, land].T.astype(np.float32)
    if not np.isfinite(dynamic[11:25, land]).all():
        raise ValueError('Weather missing on land for requested dates')
    feature_names = D.DYN_CH + names
    meta = {'target_date': str(date), 'input_date': str(issue), 'model': 'gbm',
            'quantity': 'Probability of S-NPP fire presence conditional on a clear look',
            'weather': 'Observed gridMET on input and target dates; retrospective, perfect-forecast weather',
            'grid': {'width': 590, 'height': 260, 'west': -125, 'north': 50, 'resolution_degrees': .1, 'row_order': 'north to south'},
            'png_encoding': 'R=round(probability*255), G=0, B=0, A=255 land else 0; no truth channels',
            'arms': {}}
    arrays = {'land': land, 'input_date': str(issue), 'target_date': str(date)}
    for arm in ['raw-pre','harm-pre','raw-all','harm-all','viirs-only']:
        path = Path(f'data/compare/models/gbm_{arm}.txt')
        model = lgb.Booster(model_file=str(path))
        if model.feature_name() != feature_names:
            raise ValueError('Model feature order mismatch')
        p = np.zeros(land.shape, np.float32)
        p[land] = model.predict(X, num_threads=8)
        assert np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()
        arrays[arm] = p.astype(np.float16)
        rgba = np.zeros((*land.shape,4), np.uint8)
        rgba[:,:,0] = np.rint(p * 255).astype(np.uint8)
        rgba[:,:,3] = land * 255
        Image.fromarray(rgba).save(out / f'gbm_{arm}.png')
        threshold = json.loads(path.with_suffix('.json').read_text())['threshold']
        meta['arms'][arm] = {'png': f'gbm_{arm}.png', 'threshold': threshold,
                             'model_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        logging.info('Exported %s, %d land cells, maximum probability %.4f', arm, land.sum(), p.max())
    np.savez_compressed(out / 'predictions.npz', **arrays)
    (out / 'index.json').write_text(json.dumps(meta, indent=2) + '\n')
    logging.info('Complete: %s', out)

if __name__ == '__main__':
    main(sys.argv[1])
