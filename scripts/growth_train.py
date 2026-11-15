#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas", "pyarrow", "lightgbm"]
# ///
"""Train the fire-growth model (scripts/growth_dataset.py rows) for one arm.

Usage: growth_train.py ARM [--seed N] [--variant t1|t0] [--grid small|one] [--out data/growth/runs] [--threads 8]
ARM: harm (harmonized 2003-2011 + VIIRS 2012+), raw (MODIS as is + VIIRS), viirs-only (2012+). Train years <= 2019,
validation 2020 (early stopping), test 2021-2024 (the same rows for every arm). Per grid point (objective x leaves) a
regressor (tweedie on counts, or l2 on log1p) and a zero-growth classifier (n_new == 0). Variant t0 drops the day t+1
weather. Writes OUT/ARM_vVARIANT_sSEED.json (metrics per grid point) and test predictions .parquet.
Metrics: MAE of counts, Spearman rho, zero-growth AUC, plus mean predicted vs observed (calibration).
"""
import argparse
import json
import os
import numpy as np
import pandas as pd
import lightgbm as lgb

ID = ["conv", "year", "event", "n_new", "clear_t1"]
GRIDS = {"one": [("tweedie", 31)],
         "small": [(o, l) for o in ("tweedie", "log1p") for l in (15, 63)]}


def auc(score, pos):
    r = pd.Series(score).rank().to_numpy()
    n1, n0 = pos.sum(), (~pos).sum()
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def spearman(a, b):
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def load(arm):
    v = pd.read_parquet("data/growth/rows_viirs.parquet")
    if arm == "viirs-only":
        return v
    return pd.concat([pd.read_parquet(f"data/growth/rows_{arm}.parquet"), v], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arm")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--variant", default="t1")
    ap.add_argument("--grid", default="small")
    ap.add_argument("--out", default="data/growth/runs")
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df = load(a.arm)
    feats = [c for c in df.columns if c not in ID and not (a.variant == "t0" and c.endswith("_t1"))]
    tr, va = df[df.year <= 2019], df[df.year == 2020]
    te = df[df.year >= 2021]
    if a.arm == "viirs-only":
        pass
    res = {"arm": a.arm, "seed": a.seed, "variant": a.variant, "n_train": len(tr), "n_val": len(va), "n_test": len(te),
           "events_train": int(tr.event.nunique()), "features": feats, "grid": []}
    preds = te[["conv", "year", "event", "n_new"]].copy()
    for obj, leaves in GRIDS[a.grid]:
        base = dict(num_leaves=leaves, learning_rate=0.03, min_child_samples=20, feature_fraction=0.8,
                    bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, seed=a.seed, num_threads=a.threads, verbose=-1)
        if obj == "tweedie":
            P = dict(base, objective="tweedie", tweedie_variance_power=1.5)
            f = lambda y: y
            inv = lambda p: p
        else:
            P = dict(base, objective="regression")
            f, inv = np.log1p, np.expm1
        dtr, dva = lgb.Dataset(tr[feats], f(tr.n_new)), lgb.Dataset(va[feats], f(va.n_new))
        m = lgb.train(P, dtr, 2000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
        p = np.clip(inv(m.predict(te[feats], num_iteration=m.best_iteration)), 0, None)
        Pc = dict(base, objective="binary")
        c = lgb.train(Pc, lgb.Dataset(tr[feats], (tr.n_new == 0).astype(int)), 2000,
                      valid_sets=[lgb.Dataset(va[feats], (va.n_new == 0).astype(int))],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        pz = c.predict(te[feats], num_iteration=c.best_iteration)
        y = te.n_new.to_numpy()
        g = {"objective": obj, "num_leaves": leaves, "iters_reg": m.best_iteration, "iters_clf": c.best_iteration,
             "mae": float(np.abs(p - y).mean()), "mae_const_median": float(np.abs(np.median(tr.n_new) - y).mean()),
             "spearman": spearman(p, y), "zero_auc": auc(pz, y == 0), "mean_pred": float(p.mean()), "mean_obs": float(y.mean()),
             "val_mae": float(np.abs(np.clip(inv(m.predict(va[feats], num_iteration=m.best_iteration)), 0, None) - va.n_new).mean())}
        res["grid"].append(g)
        preds[f"p_{obj}_{leaves}"] = p
        preds[f"pz_{obj}_{leaves}"] = pz
        if a.seed == 0:
            m.save_model(f"{a.out}/{a.arm}_v{a.variant}_{obj}{leaves}.txt")
    tag = f"{a.arm}_v{a.variant}_s{a.seed}"
    json.dump(res, open(f"{a.out}/{tag}.json", "w"), indent=1)
    preds.to_parquet(f"{a.out}/{tag}.parquet")
    for g in res["grid"]:
        print(tag, json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in g.items()}), flush=True)


if __name__ == "__main__":
    main()
