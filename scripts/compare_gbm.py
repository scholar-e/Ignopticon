#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm"]
# ///
"""LightGBM next-day fire model for every arm of docs/COMPARISON.md, on the tensors of compare_dataset.py.

Sampling as scripts/activity_model.py: every clear land cell-day (mask = 1) with fire in its 5x5 on t-2..t or a
positive label, plus NEG_SAMPLE of the rest weighted by 1 / NEG_SAMPLE; for fitting only, TRAIN_NEAR_NEG of the
near-fire negatives are kept and weighted back up. "Fire" is presence > 0 (viirs, raw) or p_viirs >= 0.05 (harm).
Learning rate 0.1, at most 600 trees (prediction cost). Objective: cross-entropy, which takes the harmonized arms' soft labels (p_viirs) and equals log loss on 0/1 labels.
Validation 2020: early stopping (sample) and the decision threshold (max CSI on the full 2020 grid).
Test 2021-2024: full-grid predictions saved to data/compare/preds/gbm_{arm}.npz for scripts/compare_eval.py.

Usage: compare_gbm.py ARM [ARM ...]    (raw-pre harm-pre raw-all harm-all viirs-only)
Env: COMPARE_THREADS (default 10).
"""
import json
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_dataset import DYN_CH, NX, NY, conv_for, days_in, load_static, load_year  # noqa: E402
from compare_eval import T0, csi_threshold, hist_counts, read_fires  # noqa: E402

OUT = "data/compare"
THREADS = int(os.environ.get("COMPARE_THREADS", "10"))
NEG_SAMPLE = 0.005
TRAIN_NEAR_NEG = 0.3
ARMS = {"raw-pre": range(2003, 2012), "harm-pre": range(2003, 2012), "raw-all": range(2003, 2020),
        "harm-all": range(2003, 2020), "viirs-only": range(2012, 2020)}
VAL_YEAR, TEST_YEARS = 2020, [2021, 2022, 2023, 2024]
for d in ("samples", "models", "preds", "logs"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)

_static = None


def static():
    global _static
    if _static is None:
        x, names, land = load_static()
        _static = (x.astype(np.float32), names, land)
    return _static


def feature_names():
    return DYN_CH + static()[1]


def sample(conv, year):
    """Cached sampled rows for one convention-year: X (n, 44) float32, y (soft or hard label), w, near flag."""
    path = os.path.join(OUT, "samples", "%s_%d.npz" % (conv, year))
    if os.path.exists(path):
        z = np.load(path)
        return z["X"], z["y"], z["w"], z["near"]
    t0 = time.time()
    arm = {"raw": "raw-pre", "harm": "harm-pre", "viirs": "viirs-only"}[conv]
    dyn, label, mask = load_year(arm, year)
    thr = 0.05 if conv == "harm" else 1e-6
    near = (dyn[:, 6:9].max(1) >= thr) | (label >= thr)
    rng = np.random.default_rng(year * 7 + len(conv))
    kpos = mask & near
    kneg = mask & ~near & (rng.random(mask.shape, dtype=np.float32) < NEG_SAMPLE)
    d, r, c = np.nonzero(kpos | kneg)
    st = static()[0]
    X = np.hstack([dyn[d, :, r, c].astype(np.float32), st[:, r, c].T])
    y = label[d, r, c].astype(np.float32)
    w = np.where(kneg[d, r, c], 1 / NEG_SAMPLE, 1.0).astype(np.float32)
    nr = kpos[d, r, c]
    np.savez(path, X=X, y=y, w=w, near=nr)
    log.info("%s %d: %d rows (%d near), label sum %.0f, %.0f s", conv, year, len(y), nr.sum(), y.sum(), time.time() - t0)
    return X, y, w, nr


def training_set(years, fit=True):
    parts = []
    for yr in years:
        X, y, w, near = sample(conv_for(ARM, yr), yr)
        if fit:
            # near-fire negatives thinned for fitting (soft labels: rows with label < 0.05 count as negative)
            neg = near & (y < 0.05)
            keep = ~neg | (np.random.default_rng(yr + 100).random(len(y)) < TRAIN_NEAR_NEG)
            w = np.where(neg, w / TRAIN_NEAR_NEG, w)
            X, y, w = X[keep], y[keep], w[keep]
        parts.append((X, y, w))
    return [np.concatenate([p[k] for p in parts]) for k in range(3)]


def fire_crops(year):
    """Bool (days, 260, 590): every cell of each evaluation fire's crop on the days of its window (compare_eval)."""
    out = np.zeros((days_in(year), NY, NX), bool)
    off = int((np.datetime64("%d-01-01" % year) - T0).astype(int))
    for f in read_fires():
        i0, i1 = max(f["i0"] - off, 0), min(f["i1"] - off, days_in(year))
        if i1 > i0:
            out[i0:i1, f["r0"]:f["r1"], f["c0"]:f["c1"]] = True
    return out


def predict_year(model, arm, year, chunk=31):
    """Probability (days, 260, 590) float16 for one year, computed where it is scored (S-NPP clear on t+1, land)
    and in the evaluation fires' crops; 0 elsewhere. (Full-grid prediction costs ~17 us per row: too slow.)"""
    dyn, _, mask = load_year(arm, year)
    land = static()[2]
    where = mask | (fire_crops(year) & land[None]) if year in TEST_YEARS else mask
    st = static()[0]
    n = days_in(year)
    p = np.zeros((n, NY, NX), np.float16)
    for s in range(0, n, chunk):
        d, r, c = np.nonzero(where[s:s + chunk])
        X = np.hstack([dyn[s + d, :, r, c].astype(np.float32), st[:, r, c].T])
        p[s + d, r, c] = model.predict(X, num_iteration=model.best_iteration, num_threads=THREADS)
    return p


def run(arm):
    import lightgbm as lgb
    global ARM
    ARM = arm
    t0 = time.time()
    names = feature_names()
    Xtr, ytr, wtr = training_set(ARMS[arm])
    Xva, yva, wva = training_set([VAL_YEAR], fit=False)
    log.info("%s: train %d rows (label sum %.0f, weighted %.0f), val %d rows", arm, len(ytr), ytr.sum(), (ytr * wtr).sum(), len(yva))
    params = dict(objective="cross_entropy", learning_rate=0.1, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, num_threads=THREADS, verbose=-1,
                  metric=["cross_entropy", "average_precision"], seed=0)
    dtr = lgb.Dataset(Xtr, ytr, weight=wtr, feature_name=names, categorical_feature=["fuel_fbfm40_mode"], free_raw_data=True)
    dva = lgb.Dataset(Xva, yva, weight=wva, reference=dtr)
    m = lgb.train(params, dtr, num_boost_round=600, valid_sets=[dva],
                  callbacks=[lgb.early_stopping(50, first_metric_only=True, verbose=False), lgb.log_evaluation(100)])
    del Xtr, ytr, wtr, dtr
    m.save_model(os.path.join(OUT, "models", "gbm_%s.txt" % arm))
    log.info("%s: best iteration %d, fit %.0f s", arm, m.best_iteration, time.time() - t0)
    # threshold: max CSI on the full 2020 grid
    pv = predict_year(m, arm, VAL_YEAR)
    _, lv, mv = load_year(arm, VAL_YEAR)
    pos, neg = hist_counts(pv, lv > 0.5, mv)
    thr, csi = csi_threshold(pos, neg)
    log.info("%s: validation 2020 threshold %.4f (CSI %.4f)", arm, thr, csi)
    ptest = np.concatenate([predict_year(m, arm, y) for y in TEST_YEARS])
    np.savez_compressed(os.path.join(OUT, "preds", "gbm_%s.npz" % arm), p=ptest, arm=arm, threshold=thr, val_csi=csi,
                        best_iteration=m.best_iteration)
    imp = dict(sorted(zip(names, m.feature_importance("gain").tolist()), key=lambda kv: -kv[1]))
    json.dump({"arm": arm, "best_iteration": m.best_iteration, "threshold": thr, "val_csi": csi,
               "train_years": list(ARMS[arm]), "gain_importance": imp, "seconds": time.time() - t0},
              open(os.path.join(OUT, "models", "gbm_%s.json" % arm), "w"), indent=1)
    log.info("%s: done, %.0f s", arm, time.time() - t0)


if __name__ == "__main__":
    arms = sys.argv[1:]
    if not arms or any(a not in ARMS for a in arms):
        sys.exit(__doc__)
    for a in arms:
        run(a)
