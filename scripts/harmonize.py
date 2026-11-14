#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scikit-learn"]
# ///
"""MODIS/VIIRS harmonization (PLAN.md M7): a VIIRS-equivalent daily fire record for the lower 48 from 2000 on.

Question per 0.1 degree cell-day: would VIIRS S-NPP have detected fire there, given what MODIS (Terra + Aqua) saw?
VIIRS's 375 m pixels see far smaller fires than MODIS's 1 km ones, so a raw MODIS-then-VIIRS series has a step at
2012. Learning P(VIIRS fire | MODIS) in the overlap and applying it before 2012 removes the step, and gives the
time scroll one consistent quantity from 2000 to now.

Training data (overlap years): cell-days that VIIRS S-NPP saw clearly (its fire mask has a clear look; cloud is not
"no fire"). Label: VIIRS saw >= 1 fire pixel. Kept: every cell-day with MODIS or VIIRS fire within one cell and one
day, plus a random sample of the rest (weighted back up), since most cell-days are fire-free.
Features, all from MODIS (Terra and Aqua combined; 0 where neither saw the cell clearly):
    fire pixels, nominal/high-confidence fire pixels, summed MaxFRP, clear looks; the same fire and FRP summed over
    the 3x3 neighbourhood; fire in the cell the day before and after (and their neighbourhoods); days since MODIS
    last saw fire within the 3x3 (capped at 30); MODIS clear look flag; month; cell latitude and longitude.
    "next" and "next3" use the day AFTER: fine for a retrospective record, not available in real time. Each year
    is built on its own, so "prev"/"since" restart on Jan 1 (no fire carried over from Dec 31).
p_viirs is conditional on VIIRS having a clear look; it is not discounted for cloud (apply reports both sums).
Model: gradient-boosted trees (scikit-learn HistGradientBoostingClassifier).
Evaluation: whole years held out (every third overlap year), against two baselines: MODIS fire itself
(P = 1 if MODIS saw fire) and a per-month logistic rescaling of MODIS fire pixels. Brier score, log loss, AUC,
reliability bins, and annual totals of expected VIIRS fire cell-days against the observed.

Feature sets (environment HARMONIZE_FEATURES): base = the MODIS features above; elev = + mean elevation and its
standard deviation per cell (elevation_grid.py); all (default) = + daily gridMET weather (gridmet_grid.py): wind speed,
east and north wind components, vapour pressure deficit, energy release component, 100-h fuel moisture,
precipitation. Missing values are left to the trees (NaN).

Usage:
    harmonize.py fit GRID_DIR OUT_DIR FIRST LAST       fit on overlap years FIRST..LAST, evaluate, save model
    harmonize.py apply GRID_DIR OUT_DIR FIRST LAST     write the harmonized daily grid for FIRST..LAST
GRID_DIR holds gridded_fire_grid.py outputs (PRODUCT_YEAR.npz). OUT_DIR gets model.pkl, evaluation.json, and
harmonized_YEAR.npz: p_viirs (P(VIIRS S-NPP fire), float16, days x 260 x 590), observed (0 neither sensor clear,
1 MODIS only, 2 VIIRS), viirs_fire (observed VIIRS fire pixels, 2012 on), modis_fire.
"""
import json
import logging
import os
import pickle
import sys

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

NY, NX = 260, 590
NEG_SAMPLE = 0.01       # fraction of fire-free cell-days kept for training (weighted by 1 / NEG_SAMPLE)
BASE = ["fire", "fire_nh", "frp", "clear", "fire3", "frp3", "prev", "next", "prev3", "next3", "since",
        "seen", "month", "lat", "lon"]
ELEV = ["elev", "elev_sd"]
WEATHER = ["wind", "u", "v", "vpd", "erc", "fm100", "pr"]
# Feature set from HARMONIZE_FEATURES: base (MODIS only), elev (+ elevation), all (+ elevation and gridMET weather).
FEATURE_SET = os.environ.get("HARMONIZE_FEATURES", "all")
FEATURES = BASE + (ELEV if FEATURE_SET in ("elev", "all") else []) + (WEATHER if FEATURE_SET == "all" else [])
AUX_DIR = os.environ.get("HARMONIZE_AUX", "data/harmonize")  # elevation.npz and weather/weather_YEAR.npz


def load(grid_dir, product, year):
    path = os.path.join(grid_dir, "%s_%d.npz" % (product, year))
    if not os.path.exists(path):
        return None
    z = np.load(path)
    return {k: z[k] for k in ("fire", "fire_nh", "clear", "frp")}


def modis(grid_dir, year):
    """Terra + Aqua combined for one year (either may be missing: Aqua starts mid-2002)."""
    parts = [p for p in (load(grid_dir, "MOD14A1", year), load(grid_dir, "MYD14A1", year)) if p is not None]
    if not parts:
        return None
    return {k: sum(p[k].astype(np.float32) for p in parts) for k in parts[0]}


def box3(a):
    """Sum over the 3x3 neighbourhood of every cell, per day (plain shifted sums: exact for counts, and zero stays zero)."""
    a = np.asarray(a, np.float32)
    p = np.pad(a, ((0, 0), (1, 1), (1, 1)))
    out = np.zeros_like(a)
    for dy in range(3):
        for dx in range(3):
            out += p[:, dy:dy + NY, dx:dx + NX]
    return out


def features(m, year):
    days = m["fire"].shape[0]
    fire, frp = m["fire"], m["frp"]
    fire3, frp3 = box3(fire), box3(frp)
    shift = lambda a, k: np.roll(a, k, axis=0) * (np.arange(days) >= k if k > 0 else np.arange(days) < days + k)[:, None, None]
    prev, nxt = shift(fire, 1), shift(fire, -1)
    prev3, next3 = shift(fire3, 1), shift(fire3, -1)
    since = np.full((NY, NX), 30.0, np.float32)
    since_all = np.empty_like(fire)
    for d in range(days):
        since = np.where(fire3[d] > 0, 0, np.minimum(since + 1, 30))
        since_all[d] = since
    month = np.array([np.datetime64("%d-01-01" % year) + np.timedelta64(d, "D") for d in range(days)])
    month = (month.astype("datetime64[M]").astype(int) % 12 + 1).astype(np.float32)
    lat = (50 - 0.1 * (np.arange(NY) + 0.5)).astype(np.float32)
    lon = (-125 + 0.1 * (np.arange(NX) + 0.5)).astype(np.float32)
    out = {}
    if any(k in FEATURES for k in ELEV):
        e = np.load(os.path.join(AUX_DIR, "elevation.npz"))
        for k in ELEV:
            out[k] = np.broadcast_to(e[k][None], fire.shape)
    if any(k in FEATURES for k in WEATHER):
        w = np.load(os.path.join(AUX_DIR, "weather", "weather_%d.npz" % year))
        for k in WEATHER:
            a = w[k][:days].astype(np.float32) if k in w else np.full(fire.shape, np.nan, np.float32)
            if a.shape[0] < days:
                a = np.concatenate([a, np.full((days - a.shape[0], NY, NX), np.nan, np.float32)])
            out[k] = a
    return out | {"fire": fire, "fire_nh": m["fire_nh"], "frp": frp, "clear": m["clear"], "fire3": fire3, "frp3": frp3,
            "prev": prev, "next": nxt, "prev3": prev3, "next3": next3, "since": since_all,
            "seen": (m["clear"] > 0).astype(np.float32),
            "month": np.broadcast_to(month[:, None, None], fire.shape),
            "lat": np.broadcast_to(lat[None, :, None], fire.shape),
            "lon": np.broadcast_to(lon[None, None, :], fire.shape)}


def year_sample(grid_dir, year, rng):
    m, v = modis(grid_dir, year), load(grid_dir, "VNP14A1", year)
    if m is None or v is None:
        return None
    n = min(m["fire"].shape[0], v["fire"].shape[0])
    m = {k: a[:n] for k, a in m.items()}
    f = features(m, year)
    vfire = v["fire"][:n].astype(np.float32)
    seen = v["clear"][:n] > 0
    near = (box3(f["fire"]) + box3(f["prev"]) + box3(f["next"]) + box3(vfire)) > 0
    keep_pos = seen & near
    keep_neg = seen & ~near & (rng.random(seen.shape) < NEG_SAMPLE)
    keep = keep_pos | keep_neg
    X = np.column_stack([f[k][keep] for k in FEATURES]).astype(np.float32)
    y = (vfire[keep] > 0).astype(np.int8)
    w = np.where(keep_neg[keep], 1 / NEG_SAMPLE, 1.0).astype(np.float32)
    log.info("%d: %d training cell-days (%d near fire, VIIRS fire in %d)", year, keep.sum(), keep_pos.sum(), y.sum())
    return X, y, w, np.full(len(y), year)


def evaluate(p, y, w, label):
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    p = np.clip(p, 1e-6, 1 - 1e-6)
    bins = np.linspace(0, 1, 11)
    which = np.clip(np.digitize(p, bins) - 1, 0, 9)
    rel = [[float(bins[i]), float(np.average(p[which == i], weights=w[which == i])) if (which == i).any() else None,
            float(np.average(y[which == i], weights=w[which == i])) if (which == i).any() else None,
            int((which == i).sum())] for i in range(10)]
    out = {"model": label, "brier": float(brier_score_loss(y, p, sample_weight=w)),
           "log_loss": float(log_loss(y, p, sample_weight=w)), "auc": float(roc_auc_score(y, p, sample_weight=w)),
           "expected_fire_cell_days": float((p * w).sum()), "observed_fire_cell_days": float((y * w).sum()),
           "reliability": rel}
    log.info("%-28s Brier %.5f  log loss %.4f  AUC %.4f  expected %.0f vs observed %.0f VIIRS fire cell-days",
             label, out["brier"], out["log_loss"], out["auc"], out["expected_fire_cell_days"],
             out["observed_fire_cell_days"])
    return out


def fit(grid_dir, out_dir, first, last):
    log.info("feature set %s: %s", FEATURE_SET, " ".join(FEATURES))
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(0)
    parts = [s for s in (year_sample(grid_dir, y, rng) for y in range(int(first), int(last) + 1)) if s]
    X = np.concatenate([p[0] for p in parts]); y = np.concatenate([p[1] for p in parts])
    w = np.concatenate([p[2] for p in parts]); yr = np.concatenate([p[3] for p in parts])
    years = sorted(set(yr.tolist()))
    test_years = years[1::3]
    test = np.isin(yr, test_years)
    log.info("train years %s, test years %s: %d / %d cell-days", [y_ for y_ in years if y_ not in test_years],
             test_years, (~test).sum(), test.sum())
    gb = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.08, max_leaf_nodes=63, l2_regularization=1.0,
                                        categorical_features=[FEATURES.index("month")], random_state=0)
    gb.fit(X[~test], y[~test], sample_weight=w[~test])
    i_fire, i_month = FEATURES.index("fire"), FEATURES.index("month")
    # Baseline 2: per-month logistic on log(1 + MODIS fire pixels) and MODIS clear flag.
    def base_x(Z):
        oh = np.eye(12)[Z[:, i_month].astype(int) - 1]
        return np.column_stack([oh, oh * np.log1p(Z[:, i_fire])[:, None], Z[:, FEATURES.index("seen")]])
    lr = LogisticRegression(max_iter=2000, C=10)
    lr.fit(base_x(X[~test]), y[~test], sample_weight=w[~test])
    results = {"feature_set": FEATURE_SET, "train_years": [y_ for y_ in years if y_ not in test_years], "test_years": test_years,
               "features": FEATURES, "neg_sample": NEG_SAMPLE, "test": [
                   evaluate((X[test, i_fire] > 0).astype(float), y[test], w[test], "MODIS fire as-is"),
                   evaluate(lr.predict_proba(base_x(X[test]))[:, 1], y[test], w[test], "MODIS rescaled (logistic)"),
                   evaluate(gb.predict_proba(X[test])[:, 1], y[test], w[test], "harmonized (boosted trees)")]}
    per_year = {}
    for ty in test_years:
        s = yr == ty
        per_year[str(ty)] = {"observed": float((y[s] * w[s]).sum()),
                             "harmonized": float((gb.predict_proba(X[s])[:, 1] * w[s]).sum()),
                             "modis_rescaled": float((lr.predict_proba(base_x(X[s]))[:, 1] * w[s]).sum()),
                             "modis_raw": float(((X[s, i_fire] > 0) * w[s]).sum())}
        log.info("%d: VIIRS fire cell-days observed %.0f, harmonized %.0f, MODIS rescaled %.0f, MODIS raw %.0f",
                 ty, *per_year[str(ty)].values())
    results["annual_test"] = per_year
    # The held-out model (trained on the training years only), for honest follow-up analysis of the test years.
    pickle.dump({"model": gb, "rescale": lr, "features": FEATURES, "test_years": test_years},
                open(os.path.join(out_dir, "model_holdout.pkl"), "wb"))
    from sklearn.base import clone
    gb = clone(gb)
    # Refit on all overlap years for applying.
    gb.fit(X, y, sample_weight=w)
    pickle.dump({"model": gb, "features": FEATURES}, open(os.path.join(out_dir, "model.pkl"), "wb"))
    json.dump(results, open(os.path.join(out_dir, "evaluation.json"), "w"), indent=1)
    log.info("wrote %s/model.pkl and evaluation.json", out_dir)


def apply(grid_dir, out_dir, first, last):
    saved = pickle.load(open(os.path.join(out_dir, "model.pkl"), "rb"))
    gb = saved["model"]
    totals = []
    for year in range(int(first), int(last) + 1):
        m = modis(grid_dir, year)
        if m is None:
            continue
        f = features(m, year)
        days = m["fire"].shape[0]
        p = np.zeros((days, NY, NX), np.float32)
        for s in range(0, days, 16):  # 16 days at a time keeps the feature matrix under 1 GB
            X = np.column_stack([f[k][s:s + 16].ravel() for k in saved["features"]]).astype(np.float32)
            p[s:s + 16] = gb.predict_proba(X)[:, 1].reshape(-1, NY, NX)
        v = load(grid_dir, "VNP14A1", year)
        observed = (m["clear"] > 0).astype(np.uint8)
        out = {"p_viirs": p.astype(np.float16), "modis_fire": m["fire"].astype(np.uint16)}
        if v is not None:
            n = min(days, v["fire"].shape[0])
            observed[:n][v["clear"][:n] > 0] = 2
            out["viirs_fire"] = v["fire"].astype(np.uint16)
        out["observed"] = observed
        np.savez_compressed(os.path.join(out_dir, "harmonized_%d.npz" % year), **out)
        # p is conditional on VIIRS having a clear look, so sums over all cell-days overstate what VIIRS would
        # have counted through cloud. Where MODIS was clear is the nearest pre-2012 stand-in for "VIIRS was clear".
        mclear = m["clear"] > 0
        tot = {"year": year, "expected_all": float(p.sum(dtype=np.float64)),
               "expected_modis_clear": float(p[mclear].sum(dtype=np.float64)),
               "modis_fire_cell_days": int((m["fire"] > 0).sum()), "modis_fire_pixels": int(m["fire"].sum()),
               "modis_clear_frac": float(mclear.mean())}
        if v is not None:
            vclear = v["clear"] > 0
            tot |= {"expected_viirs_clear": float(p[vclear].sum(dtype=np.float64)),
                    "viirs_fire_cell_days": int((v["fire"] > 0).sum()), "viirs_clear_frac": float(vclear.mean())}
        totals.append(tot)
        log.info("%d: %s", year, json.dumps(tot))
    path = os.path.join(out_dir, "annual_totals.json")
    old = {t["year"]: t for t in json.load(open(path))} if os.path.exists(path) else {}
    old |= {t["year"]: t for t in totals}
    json.dump([old[k] for k in sorted(old)], open(path, "w"), indent=1)


if __name__ == "__main__":
    if len(sys.argv) != 6 or sys.argv[1] not in ("fit", "apply"):
        sys.exit(__doc__)
    {"fit": fit, "apply": apply}[sys.argv[1]](*sys.argv[2:])
