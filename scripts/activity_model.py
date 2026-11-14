#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scikit-learn", "lightgbm"]
# ///
"""National next-day fire activity model on the 0.1 degree daily grid (lower 48, 590 x 260).

Question per cell-day t: will VIIRS S-NPP (VNP14A1) see fire in the cell on day t+1? Only cell-days that VIIRS
S-NPP sees clearly on t+1 count (cloud is not "no fire"), so the probability is conditional on a clear look.
Sub-target "new activity": the same label, restricted to cells with no fire from any sensor (S-NPP, NOAA-20,
Terra, Aqua) in their 3x3 neighbourhood on days t-2..t (spread into new cells, or new ignitions).

Features per cell-day (stage `build` caches the dynamic ones per year; static ones are added at train time):
    hotspots: fire pixels, nominal/high fire pixels (t only), summed FRP and clear looks for each sensor on t, t-1,
        t-2 (NaN when the sensor did not exist); VIIRS (S-NPP + NOAA-20) and MODIS (Terra + Aqua) fire summed over
        the 3x3 and 5x5 neighbourhood on t, t-1, t-2 and FRP over the 3x3 on t; days since any sensor saw fire in
        the 3x3 (capped at 30; carried over from the previous year).
    context (kept in every feature set): day of year, month, cell latitude and longitude.
    weather t: gridMET wind, u, v, vpd, erc, fm100, pr on day t, and upwind-neighbour fire on t and t-1 (fire in
        the 8 neighbours weighted by how directly day-t wind blows from them into the cell).
    weather t+1: the same gridMET variables on day t+1 and upwind fire with day t+1 wind. This is PERFECT-FORECAST
        weather (observed, standing in for a next-day forecast); an operational model would use a forecast.
    elevation: mean elevation and its standard deviation (elevation.npz).
    fuel: every 260 x 590 array in fuel.npz, if that file exists.

Sampling (like harmonize.py): every eligible cell-day with fire from any sensor in its 5x5 neighbourhood on
t-2..t, or with the label positive, plus a random NEG_SAMPLE fraction of the rest weighted by 1 / NEG_SAMPLE.
Metrics are weighted, so they estimate the full population of clear land cell-days.
Split by whole years: TEST_YEARS held out, VAL_YEAR for early stopping, the rest train.

Usage:
    activity_model.py build [YEAR ...]     cache samples for the given (default: all available) years
    activity_model.py train                baselines, LightGBM ablation, evaluation, permutation importance
    activity_model.py status               list cached years and inputs
Outputs in data/harmonize/activity/ (env ACTIVITY_OUT): samples/YEAR.npz, results.json, results.md, models,
test predictions. Threads: env ACTIVITY_THREADS (default 6).
"""
import json
import logging
import os
import pickle
import sys
import time

import numpy as np

AUX = os.environ.get("ACTIVITY_AUX", "data/harmonize")
GRID = os.path.join(AUX, "grid")
OUT = os.environ.get("ACTIVITY_OUT", os.path.join(AUX, "activity"))
THREADS = int(os.environ.get("ACTIVITY_THREADS", "6"))
os.makedirs(os.path.join(OUT, "samples"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(os.path.join(OUT, "activity.log"))],
    force=True,
)
for h in logging.getLogger().handlers:
    h.flush()
log = logging.getLogger(__name__)

NY, NX = 260, 590
PRE = 30                   # days loaded from the previous year (lags and days-since carry over Jan 1)
NEG_SAMPLE = 0.005         # fraction of far-from-fire cell-days kept, weighted by 1 / NEG_SAMPLE
SMOKE = float(os.environ.get("ACTIVITY_SMOKE", "0"))  # >0: keep this fraction of rows (quick code test only)
TRAIN_NEAR_NEG = float(os.environ.get("ACTIVITY_TRAIN_NEAR_NEG", "0.3"))  # near-fire negatives kept for fitting only
SENSORS = {"vnp": "VNP14A1", "vj1": "VJ114A1", "mod": "MOD14A1", "myd": "MYD14A1"}
WVARS = ["wind", "u", "v", "vpd", "erc", "fm100", "pr"]
TEST_YEARS = [2016, 2020, 2023]
VAL_YEAR = 2018
NB8 = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]


def dyn_names():
    n = []
    for s in SENSORS:
        for L in range(3):
            n += ["%s_fire_%d" % (s, L), "%s_frp_%d" % (s, L), "%s_clear_%d" % (s, L)]
        n.append("%s_firenh_0" % s)
    for g in ("vi", "mo"):
        for L in range(3):
            n += ["%s_f3_%d" % (g, L), "%s_f5_%d" % (g, L)]
        n.append("%s_frp3_0" % g)
    n += ["since", "doy", "month"]
    n += ["upw_t_0", "upw_t_1"] + ["%s_t" % v for v in WVARS]
    n += ["upw_t1_0"] + ["%s_t1" % v for v in WVARS]
    return n


DYN = dyn_names()
GROUPS = {
    "hotspots": [n for n in DYN if n.split("_")[0] in SENSORS or n.split("_")[0] in ("vi", "mo") or n == "since"],
    "context": ["doy", "month", "lat", "lon"],
    "weather_t": ["upw_t_0", "upw_t_1"] + ["%s_t" % v for v in WVARS],
    "weather_t1": ["upw_t1_0"] + ["%s_t1" % v for v in WVARS],
    "elevation": ["elev", "elev_sd"],
}


def days_in(year):
    return int((np.datetime64("%d-01-01" % (year + 1)) - np.datetime64("%d-01-01" % year)).astype(int))


def load_year(prod, year):
    """Full-year arrays (placed by day of year) or None."""
    path = os.path.join(GRID, "%s_%d.npz" % (prod, year))
    if not os.path.exists(path):
        return None
    z = np.load(path)
    n = days_in(year)
    out = {}
    for k in ("fire", "fire_nh", "clear", "frp"):
        a = np.zeros((n, NY, NX), np.float32)
        a[z["day"] - 1] = z[k]
        out[k] = a
    out["have"] = np.zeros(n, bool)
    out["have"][z["day"] - 1] = True
    return out


def load_window(prod, year):
    """Year with PRE days of the previous year before it and 1 day of the next after it. None if year missing."""
    cur = load_year(prod, year)
    if cur is None:
        return None
    prev, nxt = load_year(prod, year - 1), load_year(prod, year + 1)
    out = {}
    for k in ("fire", "fire_nh", "clear", "frp"):
        a = prev[k][-PRE:] if prev is not None else np.zeros((PRE, NY, NX), np.float32)
        b = nxt[k][:1] if nxt is not None else np.zeros((1, NY, NX), np.float32)
        out[k] = np.concatenate([a, cur[k], b])
    hp = prev["have"][-PRE:] if prev is not None else np.zeros(PRE, bool)
    hn = nxt["have"][:1] if nxt is not None else np.zeros(1, bool)
    out["have"] = np.concatenate([hp, cur["have"], hn])
    return out


def box(a, k):
    """Sum over the k x k neighbourhood per day (separable shifted sums)."""
    h = k // 2
    p = np.pad(a, ((0, 0), (h, h), (0, 0)))
    s = np.zeros_like(a)
    for d in range(k):
        s += p[:, d:d + NY]
    p = np.pad(s, ((0, 0), (0, 0), (h, h)))
    s = np.zeros_like(a)
    for d in range(k):
        s += p[:, :, d:d + NX]
    return s


def load_weather(year):
    """Weather for the year plus the first day of the next (for t+1 on Dec 31)."""
    w = np.load(os.path.join(AUX, "weather", "weather_%d.npz" % year))
    n = days_in(year)
    nxt_path = os.path.join(AUX, "weather", "weather_%d.npz" % (year + 1))
    wn = np.load(nxt_path) if os.path.exists(nxt_path) else None
    out = {}
    for k in WVARS:
        a = np.full((n + 1, NY, NX), np.nan, np.float16)
        x = w[k][:n]
        a[:x.shape[0]] = x
        if wn is not None:
            a[n] = wn[k][0]
        out[k] = a
    return out


def build(year, rng):
    t0 = time.time()
    path = os.path.join(OUT, "samples", "%d.npz" % year)
    sens = {s: load_window(p, year) for s, p in SENSORS.items()}
    vnp = sens["vnp"]
    if vnp is None:
        log.info("%d: no VNP14A1, skipped", year)
        return
    n = days_in(year)
    # Target on t+1 needs a VIIRS S-NPP day; the last day needs Jan 1 of next year.
    tdays = np.arange(n)
    tdays = tdays[vnp["have"][PRE + tdays + 1]]
    present = [s for s in SENSORS if sens[s] is not None]
    log.info("%d: sensors %s, %d target days", year, present, len(tdays))
    zeros = np.zeros_like(vnp["fire"])
    vi = vnp["fire"] + (sens["vj1"]["fire"] if sens["vj1"] is not None else 0)
    mo = sum(sens[s]["fire"] for s in ("mod", "myd") if sens[s] is not None) if (sens["mod"] or sens["myd"]) else zeros
    anyf = ((vi + mo) > 0).astype(np.float32)
    any3 = box(anyf, 3)
    any5 = box(anyf, 5)
    near = np.zeros((n, NY, NX), bool)
    recent = np.zeros((n, NY, NX), bool)
    for L in range(3):
        near |= any5[PRE - L:PRE - L + n] > 0
        recent |= any3[PRE - L:PRE - L + n] > 0
    del any5
    w = load_weather(year)
    land = np.isfinite(w["erc"][:n].astype(np.float32)).mean(0) > 0.5
    label = vnp["fire"][PRE + 1:PRE + 1 + n] > 0
    seen = vnp["clear"][PRE + 1:PRE + 1 + n] > 0
    okday = np.zeros(n, bool)
    okday[tdays] = True
    elig = seen & land[None] & okday[:, None, None]
    kpos = elig & (near | label)
    kneg = elig & ~(near | label) & (rng.random(elig.shape, dtype=np.float32) < NEG_SAMPLE)
    keep = kpos | kneg
    d, r, c = np.nonzero(keep)
    wt = np.where(kneg[d, r, c], 1 / NEG_SAMPLE, 1.0).astype(np.float32)
    y = label[d, r, c].astype(np.int8)
    new = ~recent[d, r, c]
    del near, recent, kpos, kneg, keep, elig, label, seen
    i = PRE + d  # index of day t in the window arrays
    X = np.full((len(d), len(DYN)), np.nan, np.float32)
    col = {k: j for j, k in enumerate(DYN)}
    for s in SENSORS:
        S = sens[s]
        if S is None:
            continue
        for L in range(3):
            hv = S["have"][i - L]
            for k in ("fire", "frp", "clear"):
                X[:, col["%s_%s_%d" % (s, k, L)]] = np.where(hv, S[k][i - L, r, c], np.nan)
        X[:, col["%s_firenh_0" % s]] = np.where(S["have"][i], S["fire_nh"][i, r, c], np.nan)
    for g, F in (("vi", vi), ("mo", mo)):
        for k in (3, 5):
            B = box(F, k)
            for L in range(3):
                X[:, col["%s_f%d_%d" % (g, k, L)]] = B[i - L, r, c]
            del B
    for g, ss in (("vi", ("vnp", "vj1")), ("mo", ("mod", "myd"))):
        frp = sum(sens[s]["frp"] for s in ss if sens[s] is not None)
        X[:, col["%s_frp3_0" % g]] = box(frp, 3)[i, r, c]
    since = np.full((NY, NX), 30.0, np.float32)
    sall = np.empty((n, NY, NX), np.float16)
    for k in range(PRE + n):
        since = np.where(any3[k] > 0, 0, np.minimum(since + 1, 30))
        if k >= PRE:
            sall[k - PRE] = since
    X[:, col["since"]] = sall[d, r, c]
    del sall, any3
    dates = np.datetime64("%d-01-01" % year) + d.astype("timedelta64[D]")
    X[:, col["doy"]] = d + 1
    X[:, col["month"]] = dates.astype("datetime64[M]").astype(int) % 12 + 1
    for v in WVARS:
        X[:, col["%s_t" % v]] = w[v][d, r, c]
        X[:, col["%s_t1" % v]] = w[v][d + 1, r, c]
    # Upwind-neighbour fire: sum over the 8 neighbours of fire (all sensors) x max(0, cos(angle between the
    # vector from the neighbour to the cell and the wind)). Rows run south, so a neighbour at (+dy, +dx) lies dx
    # east and -dy north of the cell; the vector from it to the cell is (-dx east, +dy north).
    allf = vi + mo
    for name, wd, L in (("upw_t_0", d, 0), ("upw_t_1", d, 1), ("upw_t1_0", d + 1, 0)):
        u = w["u"][wd, r, c].astype(np.float32)
        v = w["v"][wd, r, c].astype(np.float32)
        sp = np.hypot(u, v) + 1e-6
        acc = np.zeros(len(d), np.float32)
        for dy, dx in NB8:
            rr, cc = r + dy, c + dx
            ok = (rr >= 0) & (rr < NY) & (cc >= 0) & (cc < NX)
            f = np.where(ok, allf[i - L, np.clip(rr, 0, NY - 1), np.clip(cc, 0, NX - 1)], 0)
            cos = (-dx * u + dy * v) / (np.hypot(dx, dy) * sp)
            acc += f * np.maximum(cos, 0)
        X[:, col[name]] = np.where(np.isfinite(u), acc, np.nan)
    np.savez(path, X=X, names=np.array(DYN), d=d.astype(np.int16), r=r.astype(np.int16), c=c.astype(np.int16),
             y=y, w=wt, new=new, sensors=np.array(present))
    log.info("%d: %d rows (%d weighted negatives), %d positives (%d new-activity), %.0f s", year, len(y),
             (wt > 1).sum(), y.sum(), (y.astype(bool) & new).sum(), time.time() - t0)


def available_years():
    return sorted(int(f[8:12]) for f in os.listdir(GRID) if f.startswith("VNP14A1_") and f.endswith(".npz"))


def cmd_build(years):
    years = [int(y) for y in years] or available_years()
    for y in years:
        if os.path.exists(os.path.join(OUT, "samples", "%d.npz" % y)) and not os.environ.get("ACTIVITY_REBUILD"):
            log.info("%d: cached", y)
            continue
        build(y, np.random.default_rng(y))


# ---------------------------------------------------------------- training and evaluation

def static_features():
    lat = (50 - 0.1 * (np.arange(NY) + 0.5)).astype(np.float32)
    lon = (-125 + 0.1 * (np.arange(NX) + 0.5)).astype(np.float32)
    st = {"lat": np.broadcast_to(lat[:, None], (NY, NX)), "lon": np.broadcast_to(lon[None], (NY, NX))}
    e = np.load(os.path.join(AUX, "elevation.npz"))
    st |= {k: e[k].astype(np.float32) for k in ("elev", "elev_sd")}
    fuel = []
    fp = os.path.join(AUX, "fuel.npz")
    if os.path.exists(fp):
        z = np.load(fp)
        for k in z.files:
            if z[k].shape == (NY, NX):
                st["fuel_" + k] = z[k].astype(np.float32)
                fuel.append("fuel_" + k)
    return st, fuel


def load_samples(years, test_years=()):
    """All cached years with static features. In fitting years (not test), only TRAIN_NEAR_NEG of the unweighted
    (near-fire) negatives are kept, weighted back up; test rows are never thinned."""
    st, fuel = static_features()
    parts = []
    for y in years:
        p = os.path.join(OUT, "samples", "%d.npz" % y)
        if not os.path.exists(p):
            continue
        z = np.load(p)
        assert list(z["names"]) == DYN, "stale cache %s: rebuild" % p
        yy, ww = z["y"], z["w"]
        keep = np.ones(len(yy), bool) if not SMOKE else np.random.default_rng(y).random(len(yy)) < SMOKE
        if y not in test_years:
            thin = (yy == 0) & (ww == 1)
            keep &= ~(thin & (np.random.default_rng(y + 100).random(len(yy)) >= TRAIN_NEAR_NEG))
            ww = np.where(thin, ww / TRAIN_NEAR_NEG, ww).astype(np.float32)
        r, c = z["r"][keep].astype(int), z["c"][keep].astype(int)
        Xs = np.column_stack([st[k][r, c] for k in st]).astype(np.float32)
        parts.append((np.hstack([z["X"][keep], Xs]), yy[keep], ww[keep], z["new"][keep], np.full(keep.sum(), y, np.int16)))
        del z
    names = DYN + list(st)
    X, y, w, new, yr = (np.concatenate([p[k] for p in parts]) for k in range(5))
    return X, y, w, new, yr, names, fuel


def metrics(p, y, w, thresholds=()):
    from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
    p = np.clip(np.asarray(p, np.float64), 1e-7, 1 - 1e-7)
    out = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": float(np.average(y, weights=w)),
           "auc": float(roc_auc_score(y, p, sample_weight=w)),
           "ap": float(average_precision_score(y, p, sample_weight=w)),
           "brier": float(brier_score_loss(y, p, sample_weight=w)),
           "log_loss": float(log_loss(y, p, sample_weight=w, labels=[0, 1]))}
    bins = np.array([0, .005, .01, .02, .05, .1, .2, .3, .5, .7, .9, 1.0001])
    which = np.digitize(p, bins) - 1
    out["reliability"] = [[float(bins[k]), float(bins[k + 1]), float(np.average(p[which == k], weights=w[which == k])),
                           float(np.average(y[which == k], weights=w[which == k])), int((which == k).sum())]
                          for k in range(len(bins) - 1) if (which == k).any()]
    pr = {}
    for t in thresholds:
        sel = p >= t
        tp = float((w * y * sel).sum())
        pr[str(t)] = {"precision": tp / max(float((w * sel).sum()), 1e-9), "recall": tp / max(float((w * y).sum()), 1e-9),
                      "flagged_cell_days": float((w * sel).sum())}
    out["pr"] = pr
    return out


def evaluate(p, y, w, new, yr, test_years):
    thr = (0.02, 0.05, 0.1, 0.2, 0.3, 0.5)
    res = {"all": metrics(p, y, w, thr), "new": metrics(p[new], y[new], w[new], thr)}
    res["per_year"] = {str(t): {"ap": metrics(p[yr == t], y[yr == t], w[yr == t])["ap"],
                                "ap_new": metrics(p[(yr == t) & new], y[(yr == t) & new], w[(yr == t) & new])["ap"]}
                       for t in test_years}
    return res


def lgb_fit(Xtr, ytr, wtr, Xva, yva, wva, names):
    import lightgbm as lgb
    params = dict(objective="binary", learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, num_threads=THREADS, verbose=-1,
                  metric=["binary_logloss", "average_precision"], seed=0)
    cat = [k for k in names if k == "fuel_fbfm40_mode"]  # categorical fuel model code
    dtr = lgb.Dataset(Xtr, ytr, weight=wtr, feature_name=names, categorical_feature=cat or "auto", free_raw_data=True)
    dva = lgb.Dataset(Xva, yva, weight=wva, reference=dtr)
    m = lgb.train(params, dtr, num_boost_round=1500 if not SMOKE else 30, valid_sets=[dva],
                  callbacks=[lgb.early_stopping(50, first_metric_only=True, verbose=False), lgb.log_evaluation(100)])
    log.info("best iteration %d", m.best_iteration)
    return m


def heuristic_level(X, names):
    """Persistence + neighbourhood: 3 = VIIRS or MODIS fire in the cell on t, 2 = fire in the 3x3 on t,
    1 = fire in the 3x3 on t-1/t-2 or the 5x5 on t, 0 = none."""
    g = lambda k: np.nan_to_num(X[:, names.index(k)])
    cell = g("vnp_fire_0") + g("vj1_fire_0") + g("mod_fire_0") + g("myd_fire_0")
    lvl = np.zeros(len(X), np.int8)
    lvl[(g("vi_f5_0") + g("mo_f5_0") + g("vi_f3_1") + g("mo_f3_1") + g("vi_f3_2") + g("mo_f3_2")) > 0] = 1
    lvl[(g("vi_f3_0") + g("mo_f3_0")) > 0] = 2
    lvl[cell > 0] = 3
    return lvl


def cmd_train():
    import lightgbm as lgb  # noqa: F401
    from sklearn.linear_model import LogisticRegression
    years = [y for y in available_years() if os.path.exists(os.path.join(OUT, "samples", "%d.npz" % y))]
    test_years = [t for t in TEST_YEARS if t in years]
    X, y, w, new, yr, names, fuel = load_samples(years, test_years)
    te, va = np.isin(yr, test_years), yr == VAL_YEAR
    tr = ~te & ~va
    log.info("years %s; train %s, val %d, test %s; rows %d / %d / %d; features %d (fuel: %s)", years,
             sorted(set(yr[tr].tolist())), VAL_YEAR, test_years, tr.sum(), va.sum(), te.sum(), len(names), fuel or "none")
    res = {"years": years, "train_years": sorted(set(yr[tr].tolist())), "val_year": VAL_YEAR, "test_years": test_years,
           "neg_sample": NEG_SAMPLE, "train_near_neg": TRAIN_NEAR_NEG, "fuel": fuel, "baselines": {}, "models": {}}
    yt, wt, nt, yrt = y[te], w[te], new[te], yr[te]
    preds = {}
    # Baseline 1: persistence, VIIRS (S-NPP or NOAA-20) fire in the cell on t, calibrated as the training frequency.
    vfire = (np.nan_to_num(X[:, names.index("vnp_fire_0")]) + np.nan_to_num(X[:, names.index("vj1_fire_0")])) > 0
    lvl = heuristic_level(X, names)
    for name, lev in (("persistence (VIIRS fire on t)", vfire.astype(np.int8)), ("persistence + neighbourhood", lvl)):
        freq = np.array([np.average(y[tr][lev[tr] == k], weights=w[tr][lev[tr] == k]) if (lev[tr] == k).any() else 0
                         for k in range(lev.max() + 1)])
        p = freq[lev[te]]
        preds[name] = p
        res["baselines"][name] = evaluate(p, yt, wt, nt, yrt, test_years) | {"level_freq": freq.tolist()}
        log.info("%-34s AP %.4f AUC %.4f | new AP %.4f AUC %.4f", name, res["baselines"][name]["all"]["ap"],
                 res["baselines"][name]["all"]["auc"], res["baselines"][name]["new"]["ap"], res["baselines"][name]["new"]["auc"])
    # Baseline 3: logistic regression on all features (log1p counts, NaN -> 0 with missing flags, standardized),
    # fitted on a 3M-row subsample of the training years.
    def lr_x(Z):
        Z = Z.copy()
        cnt = [j for j, k in enumerate(names) if any(s in k for s in ("fire", "frp", "_f3", "_f5", "upw"))]
        Z[:, cnt] = np.log1p(np.maximum(Z[:, cnt], 0))
        miss = np.isnan(Z[:, [names.index(k) for k in ("vj1_fire_0", "vnp_clear_0", "mod_clear_0")]]).astype(np.float32)
        return np.hstack([np.nan_to_num(Z), miss])
    rng = np.random.default_rng(0)
    sub = rng.choice(np.flatnonzero(tr), size=min(3_000_000, tr.sum()), replace=False)
    Ztr = lr_x(X[sub])
    mu, sd = Ztr.mean(0), Ztr.std(0) + 1e-6
    lr = LogisticRegression(max_iter=300, C=1.0)
    lr.fit((Ztr - mu) / sd, y[sub], sample_weight=w[sub])
    del Ztr
    p = lr.predict_proba((lr_x(X[te]) - mu) / sd)[:, 1]
    preds["logistic regression (all features)"] = p
    res["baselines"]["logistic regression (all features)"] = evaluate(p, yt, wt, nt, yrt, test_years)
    log.info("logistic regression AP %.4f | new AP %.4f", res["baselines"]["logistic regression (all features)"]["all"]["ap"],
             res["baselines"]["logistic regression (all features)"]["new"]["ap"])
    # Ablation (cumulative). Context (day of year, month, lat, lon) is in every set.
    H = GROUPS["hotspots"] + GROUPS["context"]
    sets = {"hotspots": H, "+ weather t": H + GROUPS["weather_t"],
            "+ weather t+1 (perfect forecast)": H + GROUPS["weather_t"] + GROUPS["weather_t1"],
            "+ elevation": H + GROUPS["weather_t"] + GROUPS["weather_t1"] + GROUPS["elevation"]}
    if fuel:
        sets["+ fuel (all)"] = sets["+ elevation"] + fuel
    sets["all, day-t weather only"] = H + GROUPS["weather_t"] + GROUPS["elevation"] + fuel
    only = [s for s in os.environ.get("ACTIVITY_SETS", "").split(",") if s]
    best = list(sets)[-2]
    for sname, feats in sets.items():
        if only and sname not in only:
            continue
        idx = [names.index(k) for k in feats]
        t0 = time.time()
        m = lgb_fit(X[np.ix_(tr, idx)], y[tr], w[tr], X[np.ix_(va, idx)], y[va], w[va], feats)
        p = m.predict(X[np.ix_(te, idx)], num_iteration=m.best_iteration)
        preds["lgbm: " + sname] = p
        res["models"][sname] = evaluate(p, yt, wt, nt, yrt, test_years) | {
            "features": feats, "best_iteration": m.best_iteration, "fit_s": time.time() - t0,
            "gain_importance": dict(sorted(zip(feats, m.feature_importance("gain").tolist()), key=lambda kv: -kv[1])[:25])}
        log.info("lgbm %-36s AP %.4f AUC %.4f Brier %.6f | new AP %.4f AUC %.4f (%.0f s)", sname,
                 res["models"][sname]["all"]["ap"], res["models"][sname]["all"]["auc"], res["models"][sname]["all"]["brier"],
                 res["models"][sname]["new"]["ap"], res["models"][sname]["new"]["auc"], time.time() - t0)
        m.save_model(os.path.join(OUT, "lgbm_%s.txt" % sname.replace(" ", "_").replace("+", "p").replace(",", "").replace("(", "").replace(")", "")))
        if sname == best:
            best_model, best_idx, best_feats = m, idx, feats
    # A model trained only on new-activity cell-days, with the full feature set.
    if not only or best in only:
        idx = best_idx
        m = lgb_fit(X[np.ix_(tr & new, idx)], y[tr & new], w[tr & new], X[np.ix_(va & new, idx)], y[va & new],
                    w[va & new], best_feats)
        p = m.predict(X[np.ix_(te & new, idx)], num_iteration=m.best_iteration)
        res["new_activity_specialist"] = {"new": metrics(p, yt[nt], wt[nt], (0.02, 0.05, 0.1, 0.2, 0.3, 0.5)),
                                          "features": best, "best_iteration": m.best_iteration}
        log.info("new-activity specialist: new AP %.4f AUC %.4f", res["new_activity_specialist"]["new"]["ap"],
                 res["new_activity_specialist"]["new"]["auc"])
        res["importance"] = permutation_importance(best_model, X[np.ix_(te, best_idx)], yt, wt, nt, best_feats)
        res["importance_model"] = best
    np.savez_compressed(os.path.join(OUT, "test_predictions.npz"), y=yt, w=wt, new=nt, year=yrt,
                        **{k.replace(" ", "_"): v.astype(np.float32) for k, v in preds.items()})
    prev = os.path.join(OUT, "results.json")
    if only and os.path.exists(prev):
        old = json.load(open(prev))
        old["models"] |= res["models"]
        for k in ("new_activity_specialist", "importance", "importance_model", "baselines", "fuel"):
            if k in res:
                old[k] = res[k]
        res = old
    json.dump(res, open(prev, "w"), indent=1)
    write_md(res)
    log.info("wrote %s and results.md", prev)


def permutation_importance(m, X, y, w, new, feats, n=600_000 if not SMOKE else 20_000, groups=True):
    """Drop in weighted AP (all, new activity) on a test subsample when a feature or group is shuffled."""
    from sklearn.metrics import average_precision_score
    rng = np.random.default_rng(1)
    s = rng.choice(len(y), size=min(n, len(y)), replace=False)
    X, y, w, new = X[s], y[s], w[s], new[s]
    p0 = m.predict(X, num_iteration=m.best_iteration)
    ap0, apn0 = average_precision_score(y, p0, sample_weight=w), average_precision_score(y[new], p0[new], sample_weight=w[new])
    def drop(cols):
        Z = X.copy()
        perm = rng.permutation(len(y))
        Z[:, cols] = Z[perm][:, cols]
        p = m.predict(Z, num_iteration=m.best_iteration)
        return (ap0 - average_precision_score(y, p, sample_weight=w),
                apn0 - average_precision_score(y[new], p[new], sample_weight=w[new]))
    grp = {
        "VIIRS S-NPP (cell, t..t-2)": [f for f in feats if f.startswith("vnp_")],
        "VIIRS NOAA-20 (cell, t..t-2)": [f for f in feats if f.startswith("vj1_")],
        "MODIS Terra (cell, t..t-2)": [f for f in feats if f.startswith("mod_")],
        "MODIS Aqua (cell, t..t-2)": [f for f in feats if f.startswith("myd_")],
        "VIIRS neighbourhood 3x3/5x5": [f for f in feats if f.startswith("vi_")],
        "MODIS neighbourhood 3x3/5x5": [f for f in feats if f.startswith("mo_")],
        "days since fire (3x3)": ["since"],
        "upwind fire": [f for f in feats if f.startswith("upw")],
        "weather t": [f for f in feats if f.endswith("_t") and not f.startswith("upw")],
        "weather t+1": [f for f in feats if f.endswith("_t1") and not f.startswith("upw")],
        "elevation": [f for f in feats if f.startswith("elev")],
        "fuel": [f for f in feats if f.startswith("fuel_")],
        "day of year / month": ["doy", "month"], "lat / lon": ["lat", "lon"]}
    out = {"base_ap": ap0, "base_ap_new": apn0, "n": int(len(y)), "groups": {}, "features": {}}
    for g, fs in grp.items():
        fs = [f for f in fs if f in feats]
        if fs:
            a, b = drop([feats.index(f) for f in fs])
            out["groups"][g] = {"ap_drop": a, "ap_new_drop": b}
            log.info("perm %-30s AP drop %.4f  new AP drop %.4f", g, a, b)
    for j, f in enumerate(feats):
        a, b = drop([j])
        out["features"][f] = {"ap_drop": a, "ap_new_drop": b}
    return out


def write_md(res):
    L = ["# Next-day fire activity model: results", "",
         "Train %s, validation %s, test (held out) %s. Weighted metrics on held-out test years." %
         (res["train_years"], res["val_year"], res["test_years"]), "",
         "| Model | AUC | AP | Brier | AUC new | AP new | base rate new |", "|---|---|---|---|---|---|---|"]
    rows = [(k, v) for k, v in res["baselines"].items()] + [("LightGBM: " + k, v) for k, v in res["models"].items()]
    for k, v in rows:
        L.append("| %s | %.4f | %.4f | %.6f | %.4f | %.4f | %.5f |" % (k, v["all"]["auc"], v["all"]["ap"], v["all"]["brier"],
                                                                     v["new"]["auc"], v["new"]["ap"], v["new"]["base_rate"]))
    if "new_activity_specialist" in res:
        v = res["new_activity_specialist"]["new"]
        L.append("| LightGBM trained on new-activity rows only | | | | %.4f | %.4f | %.5f |" % (v["auc"], v["ap"], v["base_rate"]))
    if "importance" in res:
        L += ["", "Permutation importance (%s), drop in AP:" % res["importance_model"], "", "| Group | AP drop | AP new drop |", "|---|---|---|"]
        for g, v in sorted(res["importance"]["groups"].items(), key=lambda kv: -kv[1]["ap_drop"]):
            L.append("| %s | %.4f | %.4f |" % (g, v["ap_drop"], v["ap_new_drop"]))
    open(os.path.join(OUT, "results.md"), "w").write("\n".join(L) + "\n")


def cmd_status():
    log.info("VNP years %s; cached %s; fuel.npz %s", available_years(),
             sorted(int(f[:4]) for f in os.listdir(os.path.join(OUT, "samples")) if f.endswith(".npz")),
             os.path.exists(os.path.join(AUX, "fuel.npz")))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("build", "train", "status"):
        sys.exit(__doc__)
    {"build": lambda: cmd_build(sys.argv[2:]), "train": cmd_train, "status": cmd_status}[sys.argv[1]]()
