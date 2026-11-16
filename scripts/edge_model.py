#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm", "scikit-learn"]
# ///
"""Train and score the edge-advance model for every arm (MODELLING_LOG.md entry 19), on edge_dataset.py samples.

Arms as docs/COMPARISON.md: raw-pre / harm-pre (2003-2011 raw MODIS / harmonized), raw-all / harm-all (those plus
VIIRS 2012-2019), viirs-only (2012-2019). Validation 2020 (early stopping on cross-entropy, CSI threshold), test
2021-2024 (VIIRS, hard labels; never used for choices). Same LightGBM settings for every arm; objective
cross-entropy, which takes harm's soft labels. Baselines: training base rate (VIIRS 2012-2019) and a wind-only
logistic on the t+1 tailwind component (fitted on VIIRS 2012-2019 edges).

Outputs: data/edge/models/{arm}.txt, data/edge/preds/{val,test}.npz (rows + p per arm), data/edge/results.{json,md}.
Usage: edge_model.py [ARM ...]      Env: EDGE_THREADS (default 10).
"""
import json
import logging
import os
import sys

import numpy as np
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edge_dataset import CATEGORICAL, FEATS  # noqa: E402

ROOT = os.environ.get("EDGE_OUT", "data/edge")
SAMPLES = os.environ.get("EDGE_SAMPLES", "data/edge/samples")
SOURCE_FEATS = list(FEATS)
DAY_T_ONLY = os.environ.get("EDGE_DAY_T_ONLY", "0") == "1"
if DAY_T_ONLY:
    FEATS = [f for f in FEATS if f not in {"tail_t1", "cross_t1", "wind_t1", "vpd_t1", "erc_t1", "fm100_t1", "pr_t1"}]
SELECT = [SOURCE_FEATS.index(f) for f in FEATS]
THREADS = int(os.environ.get("EDGE_THREADS", "10"))
ARMS = {"raw-pre": [("raw", y) for y in range(2003, 2012)],
        "harm-pre": [("harm", y) for y in range(2003, 2012)],
        "raw-all": [("raw", y) for y in range(2003, 2012)] + [("viirs", y) for y in range(2012, 2020)],
        "harm-all": [("harm", y) for y in range(2003, 2012)] + [("viirs", y) for y in range(2012, 2020)],
        "viirs-only": [("viirs", y) for y in range(2012, 2020)]}
VAL, TEST = [("viirs", 2020)], [("viirs", y) for y in range(2021, 2025)]
PARAMS = dict(objective="cross_entropy", learning_rate=0.05, num_leaves=63, min_data_in_leaf=200,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, num_threads=THREADS,
              verbose=-1, seed=int(os.environ.get("EDGE_SEED", "0")))
MAX_ROUNDS, PATIENCE = 3000, 100
REGIONS = ["Northwest", "Far Southwest", "Rockies-Southwest", "Plains", "East"]
FUELS = ["grass", "grass-shrub", "shrub", "timber-understory", "timber-litter", "slash-blowdown"]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)
FI = {k: i for i, k in enumerate(FEATS)}


def load(parts):
    zs = [np.load(os.path.join(SAMPLES, "%s_%d.npz" % p)) for p in parts]
    for z in zs:
        if list(z['feats']) != SOURCE_FEATS:
            raise ValueError('Sample feature order mismatch')
    out = {k: np.concatenate([z[k] for z in zs]) for k in ("X", "d", "r", "c", "new", "y")}
    out['X'] = out['X'][:, SELECT]
    out["year"] = np.concatenate([np.full(len(z["y"]), int(z["year"]), np.int16) for z in zs])
    return out


def region(X):
    lat, lon = X[:, FI["lat"]], X[:, FI["lon"]]
    g = np.full(len(X), 4)
    g[lon < -90] = 3
    g[lon < -100] = 2
    g[(lon < -111) & (lat < 42)] = 1
    g[(lon < -111) & (lat >= 42)] = 0
    return g


def fuel(X):
    return np.nan_to_num(X[:, [FI["frac_" + k] for k in ("gr", "gs", "sh", "tu", "tl", "sb")]], nan=-1).argmax(1)


def metrics(y, p):
    y = (y > 0.5).astype(np.int8)
    m = {"n": int(len(y)), "pos": int(y.sum()), "base": float(y.mean()) if len(y) else None}
    if len(y) == 0 or y.sum() == 0 or y.sum() == len(y):
        return m
    p = p.astype(np.float64)
    m.update(ap=float(average_precision_score(y, p)), auc=float(roc_auc_score(y, p)),
             brier=float(np.mean((p - y) ** 2)), mean_p=float(p.mean()),
             logloss=float(-np.mean(y * np.log(np.clip(p, 1e-7, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-7, 1)))))
    return m


def csi_threshold(y, p):
    y = y > 0.5
    o = np.argsort(-p, kind="stable")
    ps, ys = p[o], y[o]
    tp = np.cumsum(ys); fp = np.cumsum(~ys)
    last = np.r_[ps[1:] != ps[:-1], True]                         # take whole ties
    csi = np.where(last, tp / (fp + y.sum()), -1)
    i = int(np.argmax(csi))
    return float(ps[i]), float(csi[i])


def csi(y, p, t):
    y = y > 0.5; f = p >= t
    tp = (y & f).sum(); den = (y | f).sum()
    return float(tp / den) if den else None


def reliability(y, p, nb=10):
    e = np.quantile(p, np.linspace(0, 1, nb + 1)); e[-1] += 1e-9
    i = np.clip(np.searchsorted(e, p, side="right") - 1, 0, nb - 1)
    return [[float(p[i == k].mean()), float((y[i == k] > 0.5).mean()), int((i == k).sum())] for k in range(nb) if (i == k).any()]


def main(arms):
    os.makedirs(os.path.join(ROOT, "models"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "preds"), exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(ROOT, 'train.log'))
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'))
    log.addHandler(file_handler)
    va, te = load(VAL), load(TEST)
    res = {"feats": FEATS, "arms": {}, "sizes": {}, "seed": PARAMS['seed'],
           "day_t_weather_only": DAY_T_ONLY, "samples": SAMPLES}
    pv, pt = {}, {}
    for arm in arms:
        tr = load(ARMS[arm])
        res["sizes"][arm] = {"rows": int(len(tr["y"])), "label_sum": float(tr["y"].sum()),
                             "mean_label": float(tr["y"].mean()),
                             "pre2012_rows": int((tr["year"] < 2012).sum())}
        log.info("%s: %d train rows, mean label %.4f", arm, len(tr["y"]), tr["y"].mean())
        dtr = lgb.Dataset(tr["X"], tr["y"], feature_name=FEATS, categorical_feature=CATEGORICAL, free_raw_data=True)
        dva = lgb.Dataset(va["X"], va["y"], reference=dtr)
        b = lgb.train(PARAMS, dtr, MAX_ROUNDS, valid_sets=[dva],
                      callbacks=[lgb.early_stopping(PATIENCE, verbose=False), lgb.log_evaluation(200)])
        b.save_model(os.path.join(ROOT, "models", "%s.txt" % arm), num_iteration=b.best_iteration)
        pv[arm] = b.predict(va["X"], num_iteration=b.best_iteration).astype(np.float32)
        pt[arm] = b.predict(te["X"], num_iteration=b.best_iteration).astype(np.float32)
        thr, vcsi = csi_threshold(va["y"], pv[arm])
        gain = b.feature_importance("gain")
        res["arms"][arm] = {"trees": int(b.best_iteration), "threshold": thr, "val": metrics(va["y"], pv[arm]),
                            "val_csi": vcsi,
                            "gain": {FEATS[i]: float(gain[i] / gain.sum()) for i in np.argsort(-gain)[:15]}}
        log.info("%s: %d trees, val %s", arm, b.best_iteration, res["arms"][arm]["val"])
        del tr, dtr
    # baselines (VIIRS 2012-2019 edges)
    vt = load(ARMS["viirs-only"])
    base = float(vt["y"].mean())
    wind_feature = 'tail_t' if DAY_T_ONLY else 'tail_t1'
    lr = LogisticRegression().fit(np.nan_to_num(vt["X"][:, [FI[wind_feature]]]), vt["y"] > 0.5)
    res["baselines"] = {"base_rate": base, "wind_logit": [float(lr.intercept_[0]), float(lr.coef_[0, 0])]}
    for name, f in (("base-rate", lambda X: np.full(len(X), base, np.float32)),
                    ("wind-logistic", lambda X: lr.predict_proba(np.nan_to_num(X[:, [FI[wind_feature]]]))[:, 1].astype(np.float32))):
        pv[name], pt[name] = f(va["X"]), f(te["X"])
        res["arms"][name] = {"threshold": csi_threshold(va["y"], pv[name])[0], "val": metrics(va["y"], pv[name])}
    # test scoring
    reg, fu = region(te["X"]), fuel(te["X"])
    for name in res["arms"]:
        p = pt[name]; a = res["arms"][name]
        a["test"] = metrics(te["y"], p)
        a["test_new"] = metrics(te["y"][te["new"]], p[te["new"]])
        a["test_old"] = metrics(te["y"][~te["new"]], p[~te["new"]])
        a["test_csi"] = csi(te["y"], p, a["threshold"])
        a["reliability"] = reliability(te["y"], p)
        a["region"] = {REGIONS[g]: metrics(te["y"][reg == g], p[reg == g]) for g in range(5)}
        a["fuel"] = {FUELS[g]: metrics(te["y"][fu == g], p[fu == g]) for g in range(6)}
        a["year"] = {str(y): metrics(te["y"][te["year"] == y], p[te["year"] == y]) for y in range(2021, 2025)}
    res["test_size"] = {"rows": int(len(te["y"])), "pos": int(te["y"].sum()), "new_rows": int(te["new"].sum())}
    keep = {k: v for k, v in te.items() if k != "X"}
    np.savez_compressed(os.path.join(ROOT, "preds", "test.npz"), **keep, region=reg, fuel=fu,
                        **{"p_" + k: v.astype(np.float16) for k, v in pt.items()})
    np.savez_compressed(os.path.join(ROOT, "preds", "val.npz"), **{k: v for k, v in va.items() if k != "X"},
                        **{"p_" + k: v.astype(np.float16) for k, v in pv.items()})
    with open(os.path.join(ROOT, "results.json"), "w") as f:
        json.dump(res, f, indent=1)
    write_md(res)


def fmt(x, k):
    return "%.4f" % x[k] if x.get(k) is not None else "-"


def write_md(res):
    L = ["# Edge-advance model results (test 2021-2024, VIIRS edges; held out)", "",
         "Test rows %(rows)d, positives %(pos)d, new-fire rows %(new_rows)d." % res["test_size"], "",
         "| Model | Train rows | Trees | AP | AUC | Brier | CSI@val thr | AP new | AUC new | AP old |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, a in res["arms"].items():
        t, n, o = a["test"], a["test_new"], a["test_old"]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            k, res["sizes"].get(k, {}).get("rows", "-"), a.get("trees", "-"), fmt(t, "ap"), fmt(t, "auc"),
            fmt(t, "brier"), "%.4f" % a["test_csi"] if a["test_csi"] is not None else "-", fmt(n, "ap"),
            fmt(n, "auc"), fmt(o, "ap")))
    for grp in ("region", "fuel", "year"):
        names = list(next(iter(res["arms"].values()))[grp])
        L += ["", "AP by %s (test)" % grp, "", "| Model | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
        L.append("| positives | " + " | ".join(str(next(iter(res["arms"].values()))[grp][g]["pos"]) for g in names) + " |")
        for k, a in res["arms"].items():
            L.append("| %s | " % k + " | ".join(fmt(a[grp][g], "ap") for g in names) + " |")
    L += ["", "Validation 2020 AP: " + ", ".join("%s %s" % (k, fmt(a["val"], "ap")) for k, a in res["arms"].items())]
    for k, a in res["arms"].items():
        if "gain" in a:
            L.append("- %s top gain: " % k + ", ".join("%s %.2f" % kv for kv in list(a["gain"].items())[:8]))
    with open(os.path.join(ROOT, "results.md"), "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:] or list(ARMS))
