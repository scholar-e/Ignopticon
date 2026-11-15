#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pandas", "pyarrow", "lightgbm", "tabulate"]
# ///
"""Does an UNSUPPRESSED growth model benefit from harmonized MODIS fire-days, when trained on uncontained fire-days only?
(MODELLING_LOG.md entry 28; data/growth_uncontained/NOTES.md)

  growth_uncontained.py label   containment status per growth event-day -> data/growth_uncontained/labels.parquet
  growth_uncontained.py run     retrain arms harm/raw/viirs-only on subsets all / unc / notcont, evaluate on test rows

Frozen rules (set before any scoring):
 * Row = event-day t of data/growth/rows_*.parquet (year, doy, lat, lon of the active zone). Date = Jan 1 + doy - 1 (UTC).
 * Link: incident point of origin within R_DEG = 0.3 degrees (lon scaled by cos lat; ~30 km) of the row, and the date within the
   incident's window [discovery - 1 d, last report / last containment-control-out date + 14 d]; nearest incident wins.
   Train/val rows (2003-2020): ICS-209-PLUS wildfire incidents (St. Denis et al. 2023, figshare 19858927) with sitrep percent
   contained (unit PCT, 0-100). Test rows (2021-2024): WFIGS incident locations (wildfires >= 300 acres) with dates.
 * ICS status on day t: percent contained of the latest sitrep with REPORT_TO_DATE <= t (0 before the first report). Training labels: unc80 (main, percent < 80 = uncontained or semi-contained, includes the free-burning early period of later-contained fires) and unc50 (sensitivity, < 50). Both frozen before scoring.
 * WFIGS status on day t: contained (100%) if min(ContainmentDateTime, ControlDateTime, FireOutDateTime) falls on or before day t, else
   uncontained (incidents with no such date: window ends discovery + 90 d).
 * Test (WFIGS has no daily percent): unc = matched and before the incident containment-control-out date. Training subsets: all, unc80, unc50.
Training: as growth_train.py (LightGBM, same features/params, grid tweedie|log1p x 15|63, train years <= 2019, early stopping on
2020 rows of the same label set as evaluation (unc)), 3 seeds, ensemble mean of seeds; grid point picked by val MAE on unc val rows.
Evaluation on unc TEST rows (identical rows for every arm): Spearman, zero-growth AUC, q90 pinball and coverage of the analog-q90
predictor of growth_bounds.py with its pool restricted to the training subset; paired event-block bootstrap (1000).
"""
import json
import logging
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)
D = Path("data/growth_uncontained")
G = Path("data/growth")
ICS = D / "ics209/ics209plus-wildfire"
R_DEG, PCT_UNC, WIN_PAD, MIN_ACRES_WFIGS = 0.3, 50.0, 14, 300
ARMS = {"harm": ("harm", "viirs"), "raw": ("raw", "viirs"), "viirs-only": ("viirs",)}


def ics_incidents():
    s = pd.read_csv(ICS / "ics209-plus-wf_sitreps_1999to2020.csv", low_memory=False,
                    usecols=["INCIDENT_ID", "REPORT_TO_DATE", "PCT_CONTAINED_COMPLETED", "PCT_CONT_COMPL_UOM_ABBREV"])
    s = s[(s.PCT_CONT_COMPL_UOM_ABBREV.fillna("PCT") == "PCT") & s.PCT_CONTAINED_COMPLETED.between(0, 100)].copy()
    s["d"] = pd.to_datetime(s.REPORT_TO_DATE, format="ISO8601").dt.normalize()
    s = s.groupby(["INCIDENT_ID", "d"], as_index=False).PCT_CONTAINED_COMPLETED.max().sort_values(["INCIDENT_ID", "d"])
    inc = pd.read_csv(ICS / "ics209-plus-wf_incidents_1999to2020.csv", low_memory=False,
                      usecols=["INCIDENT_ID", "POO_LATITUDE", "POO_LONGITUDE", "DISCOVERY_DATE"]).dropna()
    inc["disc"] = pd.to_datetime(inc.DISCOVERY_DATE, format="ISO8601").dt.normalize()
    out = []
    reps = {k: g for k, g in s.groupby("INCIDENT_ID")}
    for r in inc.itertuples():
        g = reps.get(r.INCIDENT_ID)
        if g is None:
            continue
        out.append(dict(id=r.INCIDENT_ID, lat=r.POO_LATITUDE, lon=r.POO_LONGITUDE, start=r.disc - pd.Timedelta(days=1),
                        end=g.d.max() + pd.Timedelta(days=WIN_PAD), rep_d=g.d.to_numpy("datetime64[D]"), rep_p=g.PCT_CONTAINED_COMPLETED.to_numpy()))
    return out


def ics_status(inc, day):
    i = np.searchsorted(inc["rep_d"], np.datetime64(day, "D"), side="right")
    pct = 0.0 if i == 0 else inc["rep_p"][i - 1]
    return float(pct)


def wfigs_incidents():
    w = pd.read_csv(D / "wfigs_2021_2024.csv", parse_dates=["FireDiscoveryDateTime", "ContainmentDateTime", "ControlDateTime", "FireOutDateTime"])
    w = w.dropna(subset=["lat", "lon", "FireDiscoveryDateTime"])
    w = w[w[["IncidentSize", "FinalAcres"]].max(axis=1) >= MIN_ACRES_WFIGS]
    out = []
    for r in w.itertuples():
        done = [x for x in (r.ContainmentDateTime, r.ControlDateTime, r.FireOutDateTime) if pd.notna(x)]
        disc = r.FireDiscoveryDateTime.tz_localize(None).normalize()
        d = min(done).tz_localize(None).normalize() if done else None
        end = (d + pd.Timedelta(days=WIN_PAD)) if d is not None else disc + pd.Timedelta(days=90)
        out.append(dict(id=r.IncidentName, lat=r.lat, lon=r.lon, start=disc - pd.Timedelta(days=1), end=end, done=d))
    return out


def link(rows, incs, status_fn):
    """nearest incident within R_DEG whose window holds the row date; returns label list, distance"""
    from scipy.spatial import cKDTree
    lat0 = 40.0
    xy = lambda lat, lon: np.column_stack([lon * np.cos(np.radians(lat0)), lat])
    tree = cKDTree(xy(np.array([i["lat"] for i in incs]), np.array([i["lon"] for i in incs])))
    q = tree.query_ball_point(xy(rows.lat.to_numpy(), rows.lon.to_numpy()), R_DEG * 1.0)
    lab, dist, iid = [], [], []
    P = xy(rows.lat.to_numpy(), rows.lon.to_numpy())
    I = tree.data
    for k, (cands, y, doy) in enumerate(zip(q, rows.year.to_numpy(), rows.doy.to_numpy())):
        day = pd.Timestamp(int(y), 1, 1) + pd.Timedelta(days=int(doy) - 1)
        best = None
        for c in cands:
            inc = incs[c]
            if inc["start"] <= day <= inc["end"]:
                dd = float(np.hypot(*(P[k] - I[c])))
                if dd <= R_DEG and (best is None or dd < best[0]):
                    best = (dd, c)
        if best is None:
            lab.append(np.nan); dist.append(np.nan); iid.append("")
        else:
            lab.append(status_fn(incs[best[1]], day)); dist.append(best[0]); iid.append(str(incs[best[1]]["id"]))
    return lab, dist, iid


def wf_status(inc, day):
    return 100.0 if inc["done"] is not None and inc["done"] <= day else 0.0     # WFIGS has no daily percent


def label():
    ics, wf = ics_incidents(), wfigs_incidents()
    log.info("ICS incidents with sitreps+coords %d, WFIGS >= %d acres %d", len(ics), MIN_ACRES_WFIGS, len(wf))
    out = []
    for conv in ("harm", "raw", "viirs"):
        d = pd.read_parquet(G / f"rows_{conv}.parquet", columns=["conv", "year", "event", "doy", "lat", "lon", "n_new"])
        for part, fn, incs in (("ics", ics_status, ics), ("wfigs", wf_status, wf)):
            m = d.year <= 2020 if part == "ics" else d.year >= 2021
            if not m.any():
                continue
            x = d[m].copy()
            x["pct"], x["dist_deg"], x["incident"] = link(x, incs, fn)
            x["source"] = part
            out.append(x)
            log.info("%s %s rows %d matched %d", conv, part, len(x), x.pct.notna().sum())
    L = pd.concat(out, ignore_index=True)
    for T in (80, 50):
        L[f"lab{T}"] = np.where(L.pct.isna(), "unmatched", np.where(L.pct < T, "unc", "cont"))
    L.to_parquet(D / "labels.parquet")
    t = pd.concat({T: L.groupby(["conv", "source"])[f"lab{T}"].value_counts().unstack(fill_value=0) for T in (80, 50)}, names=["threshold"])
    t["rows"] = t.sum(axis=1)
    t["match_rate"] = 1 - t.unmatched / t.rows
    (D / "match_rates.md").write_text(t.to_markdown(floatfmt=".3f") + "\n")
    log.info("\n%s", t.to_string())


def run():
    import lightgbm as lgb
    from growth_train import ID, auc, spearman
    import growth_bounds as gb
    L = pd.read_parquet(D / "labels.parquet")[["conv", "year", "event", "doy", "lab80", "lab50"]]
    v = pd.read_parquet(G / "rows_viirs.parquet")
    frames = {"viirs": v}
    for c in ("harm", "raw"):
        frames[c] = pd.read_parquet(G / f"rows_{c}.parquet")
    for c in frames:
        frames[c] = frames[c].merge(L, on=["conv", "year", "event", "doy"], how="left")
        assert frames[c].lab80.notna().all(), c
    test = frames["viirs"][frames["viirs"].year >= 2021].reset_index(drop=True)
    tu = test[test.lab80 == "unc"].reset_index(drop=True)
    log.info("test rows %d, unc %d, events %d", len(test), len(tu), tu.event.nunique())
    subsets = {"all": lambda d: d, "unc80": lambda d: d[d.lab80 == "unc"], "unc50": lambda d: d[d.lab50 == "unc"]}
    counts, preds, q90 = [], {}, {}
    grids = [(o, l) for o in ("tweedie", "log1p") for l in (15, 63)]
    for arm, convs in ARMS.items():
        full = pd.concat([frames[c] for c in convs], ignore_index=True)
        feats0 = [c for c in full.columns if c not in ID + ["lab80", "lab50", "doy"] and not c.endswith("_t1")]
        feats1 = [c for c in full.columns if c not in ID + ["lab80", "lab50", "doy"]]
        for sub, fn in subsets.items():
            d = fn(full)
            tr, va = d[d.year <= 2019], d[d.year == 2020]
            vu = full[(full.year == 2020) & (full.lab80 == "unc")]      # selection rows: unc80 2020 for every training set
            va = va if sub == "all" else (vu if sub == "unc80" else full[(full.year == 2020) & (full.lab50 == "unc")])
            va_sel = vu
            counts.append(dict(arm=arm, subset=sub, train_rows=len(tr), train_events=tr.event.nunique(), val_rows=len(va),
                               modis_rows=int((tr.conv != "viirs").sum())))
            for variant, feats in (("t0", feats0), ("t1", feats1)):
                cand = {}
                for obj, leaves in grids:
                    P_, Z_, VM = [], [], []
                    for seed in range(3):
                        base = dict(num_leaves=leaves, learning_rate=0.03, min_child_samples=20, feature_fraction=0.8, bagging_fraction=0.8,
                                    bagging_freq=1, lambda_l2=1.0, seed=seed, num_threads=8, verbose=-1)
                        if obj == "tweedie":
                            P = dict(base, objective="tweedie", tweedie_variance_power=1.5); f = lambda y: y; inv = lambda p: p
                        else:
                            P = dict(base, objective="regression"); f, inv = np.log1p, np.expm1
                        m = lgb.train(P, lgb.Dataset(tr[feats], f(tr.n_new)), 2000, valid_sets=[lgb.Dataset(va[feats], f(va.n_new))],
                                      callbacks=[lgb.early_stopping(100, verbose=False)])
                        c = lgb.train(dict(base, objective="binary"), lgb.Dataset(tr[feats], (tr.n_new == 0).astype(int)), 2000,
                                      valid_sets=[lgb.Dataset(va[feats], (va.n_new == 0).astype(int))],
                                      callbacks=[lgb.early_stopping(100, verbose=False)])
                        gp = lambda X: np.clip(inv(m.predict(X[feats], num_iteration=m.best_iteration)), 0, None)
                        P_.append(gp(tu)); Z_.append(c.predict(tu[feats], num_iteration=c.best_iteration)); VM.append(np.abs(gp(va_sel) - va_sel.n_new).mean())
                    cand[(obj, leaves)] = (float(np.mean(VM)), np.mean(P_, 0), np.mean(Z_, 0))
                    log.info("%s %s %s %s/%d val_mae(unc) %.4f", arm, sub, variant, obj, leaves, cand[(obj, leaves)][0])
                bk = min(cand, key=lambda k: cand[k][0])
                preds[(arm, sub, variant)] = dict(cfg=f"{bk[0]}/{bk[1]}", p=cand[bk][1], pz=cand[bk][2])
            # analog q90 with pool = training subset (t0 features)
            X, mu, sd = gb.mat(tr)
            from scipy.spatial import cKDTree
            tree, ytr = cKDTree(X), tr.n_new.to_numpy(float)
            _, ix = tree.query(gb.mat(tu, mu, sd)[0], k=min(gb.K, len(tr)))
            q90[(arm, sub)] = np.quantile(ytr[ix], gb.Q, axis=1)
    pd.DataFrame(counts).to_csv(D / "train_counts.csv", index=False)
    y = tu.n_new.to_numpy(float)
    ev = tu.event.to_numpy()
    rng = np.random.default_rng(0)
    ue = np.unique(ev)
    byev = {e: np.flatnonzero(ev == e) for e in ue}
    boots = [np.concatenate([byev[e] for e in rng.choice(ue, len(ue))]) for _ in range(1000)]

    def met(kind, key, ii):
        if kind == "sp":
            return spearman(preds[key]["p"][ii], y[ii])
        if kind == "auc":
            return auc(preds[key]["pz"][ii], y[ii] == 0)
        p = q90[key[:2]]
        if kind == "pin":
            return float(gb.pinball(y[ii], p[ii]).mean())
        return float((y[ii] <= p[ii]).mean())
    allx = np.arange(len(y))
    kinds = ["sp", "auc", "pin", "cov"]
    res = {}
    for sub in subsets:
        for variant in ("t0", "t1"):
            for arm in ARMS:
                key = (arm, sub, variant)
                res[key] = {k: met(k, key, allx) for k in kinds}
                res[key]["cfg"] = preds[key]["cfg"]
            B = {(arm, k): np.array([met(k, (arm, sub, variant), b) for b in boots]) for arm in ARMS for k in kinds}
            for arm in ARMS:
                for k in kinds:
                    res[(arm, sub, variant)][k + "_ci"] = [float(np.quantile(B[(arm, k)], .025)), float(np.quantile(B[(arm, k)], .975))]
            for a, b in (("harm", "raw"), ("harm", "viirs-only")):
                for k in kinds:
                    dd = B[(a, k)] - B[(b, k)]
                    res[(f"{a}-{b}", sub, variant)] = res.get((f"{a}-{b}", sub, variant), {})
                    res[(f"{a}-{b}", sub, variant)][k] = [float(met(k, (a, sub, variant), allx) - met(k, (b, sub, variant), allx)),
                                                         float(np.quantile(dd, .025)), float(np.quantile(dd, .975))]
    cnt = pd.DataFrame(counts)
    L_ = [f"# Uncontained-trained growth model (entry 28), test = pre-containment 2021-24 event-days. Test = uncontained 2021-24 event-days: {len(tu)} rows, {len(ue)} events (of {len(test)} test rows)", "",
          "## Training rows (years <= 2019) per arm and subset", "", cnt.to_markdown(index=False), ""]
    for variant in ("t0", "t1"):
        L_ += [f"## Variant {variant}: test metrics on uncontained test rows (seed-ensemble, 95% event-block CI)", "",
               "| Train subset | Arm | Spearman | Zero AUC | q90 pinball | q90 coverage |", "|---|---|---|---|---|---|"]
        for sub in subsets:
            for arm in ARMS:
                r = res[(arm, sub, variant)]
                f = lambda k: f"{r[k]:.4f} ({r[k + '_ci'][0]:.4f} to {r[k + '_ci'][1]:.4f})"
                L_.append(f"| {sub} | {arm} | {f('sp')} | {f('auc')} | {f('pin')} | {f('cov')} |")
        L_ += ["", "Paired differences (event-block bootstrap; Spearman/AUC: positive favours the first arm; pinball: negative favours it):", "",
               "| Train subset | Difference | Spearman | Zero AUC | q90 pinball |", "|---|---|---|---|---|"]
        for sub in subsets:
            for dn in ("harm-raw", "harm-viirs-only"):
                r = res[(dn, sub, variant)]
                f = lambda k: f"{r[k][0]:+.4f} ({r[k][1]:+.4f} to {r[k][2]:+.4f})"
                L_.append(f"| {sub} | {dn} | {f('sp')} | {f('auc')} | {f('pin')} |")
        L_.append("")
    (D / "results.md").write_text("\n".join(L_) + "\n")
    json.dump({"|".join(k): v for k, v in res.items()}, open(D / "results.json", "w"), indent=1)
    print("\n".join(L_))


if __name__ == "__main__":
    {"label": label, "run": run}[sys.argv[1]]()
