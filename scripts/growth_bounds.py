#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pandas", "tabulate", "pyarrow", "lightgbm", "rasterio", "shapely>=2.1", "pyproj"]
# ///
"""Lower / upper forecast cases from the growth record (MODELLING_LOG.md entry 25).

  growth_bounds.py tail        analog-q90 calibration on the growth test split (2021-24), arms harm / raw / viirs-only
  growth_bounds.py features    Aug 1 2026 features for the 12 held-out and all Aug 2 forecast fires (data/bounds/fires_feat.json)
  growth_bounds.py heldout     lower / upper / control / no-growth on the 12 frozen held-out fires at +12 h (data/bounds/heldout_table.md)
  growth_bounds.py export      lower and upper GeoJSON for every Aug 2 forecast fire -> earth/public/data/forecasts_bounds

Lower (most likely) = the area-constrained forecast of entry 24: growth model (harm, t0 = day-t features only) predicts
new cells, x k_km2_per_cell x HORIZON_SHARE, cut ELMFIRE's arrival map at that area.
Upper (worst case) = same conversion, but cells = Q (0.9, frozen) quantile of next-day growth (n_new) among the K (100, frozen)
nearest ANALOG event-days of the training pool (standardized size/recent growth/season/place/fire weather/fuel features,
day-t only), pool = the harm, raw or viirs-only training rows (years <= 2019); upper area = max(lower, that). Footprint = cut of the
UNION of the available ensemble members' arrival maps (earliest arrival over data/perimeter_batch/FIRE/runs/{fuelwind16,physics3}/*
where they exist, else the control run alone) at that area. The footprint source is recorded in each file and in index.json.
"""
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import growth_dataset as gd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)
G = Path("data/growth")
B = Path("data/bounds")
MAN = "data/edge_causal/front_weighted/manifest.json"
HELD = Path("data/edge_causal/front_weighted/heldout")
EVAL = G / "heldout_eval"
Q, K = 0.9, 100
HORIZON_SHARE = 0.5
ISSUE_DOY = 213
FEAT = ["size_cells", "n_t", "g0", "days_active", "doy_sin", "doy_cos", "lat", "lon", "wind_t", "vpd_t", "erc_t", "fm100_t",
        "fuel_frac_nb", "fuel_frac_gr", "fuel_frac_gs", "fuel_frac_sh", "fuel_frac_tu", "fuel_frac_tl", "fuel_frac_sb"]
LOGF = {"size_cells", "n_t", "g0", "days_active"}
ARMS = {"harm": ("harm", "viirs"), "raw": ("raw", "viirs"), "viirs-only": ("viirs",)}
OUTDIR = Path("earth/public/data/forecasts_bounds")


def pool(arm):
    d = pd.concat([pd.read_parquet(G / f"rows_{c}.parquet") for c in ARMS[arm]])
    return d[d.year <= 2019].reset_index(drop=True)


def mat(d, mu=None, sd=None):
    X = np.column_stack([np.log1p(d[c].astype(float)) if c in LOGF else d[c].astype(float) for c in FEAT])
    if mu is None:
        mu, sd = np.nanmean(X, 0), np.nanstd(X, 0) + 1e-9
    X = (X - mu) / sd
    return np.where(np.isnan(X), 0.0, X), mu, sd


class Analog:
    def __init__(self, arm):
        from scipy.spatial import cKDTree
        self.p = pool(arm)
        self.X, self.mu, self.sd = mat(self.p)
        self.tree, self.y = cKDTree(self.X), self.p.n_new.to_numpy(float)

    def q(self, d, k=K, q=Q):
        _, ix = self.tree.query(mat(d, self.mu, self.sd)[0], k=k)
        return np.quantile(self.y[ix], q, axis=1)


def pinball(y, p, q=Q):
    e = y - p
    return np.maximum(q * e, (q - 1) * e)


def tail():
    test = pd.read_parquet(G / "rows_viirs.parquet")
    test = test[test.year >= 2021].reset_index(drop=True)
    y = test.n_new.to_numpy(float)
    top = y >= np.quantile(y, 0.9)           # largest-growth decile (ties at the cut included)
    ev = test.event.to_numpy()
    rng = np.random.default_rng(0)
    ue = np.unique(ev)
    ix_by = {e: np.flatnonzero(ev == e) for e in ue}
    boots = [np.concatenate([ix_by[e] for e in rng.choice(ue, len(ue))]) for _ in range(1000)]
    preds, res = {}, {}
    for arm in ARMS:
        a = Analog(arm)
        preds[arm] = a.q(test)
        preds[arm + "@const"] = np.full(len(y), np.quantile(a.y, Q))
        log.info("%s pool %d", arm, len(a.p))
    preds["zero"] = np.zeros(len(y))
    for nm, p in preds.items():
        cov = lambda ii: float((y[ii] <= p[ii]).mean())
        allx = np.arange(len(y))
        bc = [cov(b) for b in boots]
        res[nm] = {"coverage": cov(allx), "cov_ci": [float(np.quantile(bc, .025)), float(np.quantile(bc, .975))],
                   "pinball": float(pinball(y, p).mean()), "tail_coverage": float((y[top] <= p[top]).mean()),
                   "tail_sum_pred_over_obs": float(p[top].sum() / y[top].sum()), "mean_pred": float(p.mean()),
                   "n": len(y), "n_tail": int(top.sum()), "n_events": len(ue)}
    for a, b in (("harm", "raw"), ("harm", "viirs-only"), ("raw", "viirs-only")):
        d = [(pinball(y[bb], preds[a][bb]).mean() - pinball(y[bb], preds[b][bb]).mean()) for bb in boots]
        res[f"pinball_{a}_minus_{b}"] = {"mean": float(np.mean(d)), "ci95": [float(np.quantile(d, .025)), float(np.quantile(d, .975))]}
    (B / "tail_calibration.json").write_text(json.dumps(res, indent=1))
    L = ["# Analog q90 calibration on the growth test split (2021-24, %d event-days, %d events, %d in the largest-growth decile)" % (
        len(y), len(ue), top.sum()), "", f"Analogs: K={K} nearest event-days in the arm's training pool (<= 2019) on standardized day-t features; q={Q}.",
         "Target: coverage 0.90 (fraction of days with actual growth <= predicted q90). Lower pinball is better. CI: 1000 event-block bootstraps.", "",
         "| Predictor | Coverage (CI) | Pinball q0.9 | Tail coverage | Tail sum pred/obs | Mean q90 pred |", "|---|---|---|---|---|---|"]
    for nm in preds:
        r = res[nm]
        L.append(f"| {nm} | {r['coverage']:.3f} ({r['cov_ci'][0]:.3f}-{r['cov_ci'][1]:.3f}) | {r['pinball']:.4f} | {r['tail_coverage']:.3f} | "
                 f"{r['tail_sum_pred_over_obs']:.2f} | {r['mean_pred']:.2f} |")
    L += ["", "Paired pinball difference (negative = first arm better), event-block bootstrap:", ""]
    for k_, v in res.items():
        if k_.startswith("pinball_"):
            L.append(f"- {k_[8:]}: {v['mean']:+.4f} (95% CI {v['ci95'][0]:+.4f} to {v['ci95'][1]:+.4f})")
    (B / "tail_calibration.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


def fire_file(fire, name):
    for r in ("data/training/fires", "data/demo_pull/fires"):
        if (Path(r) / fire / name).exists():
            return Path(r) / fire / name
    raise FileNotFoundError(f"{fire}/{name}")


def fire_list():
    man = json.loads(Path(MAN).read_text())
    out = {f["fire"]: {"lon": f["lon"], "lat": f["lat"], "heldout": True} for f in man["fires"]}
    for f in json.loads(Path("earth/public/data/forecasts/index.json").read_text())["forecasts"]:
        if not f["issued"].startswith("2026-08-02T12"):
            continue
        fire = f"map20260802-{f['fire_id']}"
        if fire not in out:
            c = json.loads(fire_file(fire, "fire-observed.json").read_text())["center"]
            out[fire] = {"lon": c[0], "lat": c[1], "heldout": False}
        out[fire].update(name=f["name"], fire_id=f["fire_id"])
    return out


def features():
    """day-t features for each fire: cluster row (size/growth history) when a VIIRS cluster is within 0.4 deg on Aug 1, zeros else;
    weather and fuel looked up on the 0.1 deg grid (3x3 mean) so every fire has them. Also the harm t0 growth-model prediction."""
    import lightgbm as lgb
    ys, F, C, start = gd.load_conv("viirs")
    pers = gd.persistent_mask(F, ys, start)
    z5, z6 = np.load("data/harmonize/grid/VNP14A1_2025.npz"), np.load("data/harmonize/grid/VNP14A1_2026.npz")
    f6 = z6["fire"] > 0
    f6[ISSUE_DOY:] = False
    Fs = np.concatenate([z5["fire"] > 0, f6])
    Cs = np.concatenate([z5["clear"] > 0, z6["clear"] > 0])
    wz = np.load("data/harmonize/weather/weather_2026.npz")
    wx = np.stack([wz[k] for k in gd.WX], 1)
    s = np.load(f"{gd.T}/static.npz")
    static, names = s["x"].astype(np.float32), [str(n) for n in s["names"]]
    gd.MIN_DAYS = gd.MIN_CELLDAYS = gd.MIN_PEAK_CELLS = 1
    rows = pd.DataFrame(gd.do_year(("viirs", 2026, Fs, Cs, 365, 730, pers, static, names, wx)))
    rows = rows[rows.doy == ISSUE_DOY]
    sidx = {k: i for i, k in enumerate(names)}
    latg, long = static[sidx["lat"]], static[sidx["lon"]]
    m = lgb.Booster(model_file="data/growth/runs/harm_vt0_log1p15.txt")
    out = {}
    for fire, f in fire_list().items():
        d2 = (latg - f["lat"]) ** 2 + (long - f["lon"]) ** 2
        r0, c0 = np.unravel_index(np.argmin(d2), d2.shape)
        rr, cc = slice(max(r0 - 1, 0), r0 + 2), slice(max(c0 - 1, 0), c0 + 2)
        e = {"lat": float(f["lat"]), "lon": float(f["lon"]), "doy_sin": float(np.sin(2 * np.pi * ISSUE_DOY / 365)),
             "doy_cos": float(np.cos(2 * np.pi * ISSUE_DOY / 365))}
        for k, nm in enumerate(gd.WX):
            e[nm + "_t"] = float(wx[ISSUE_DOY - 1][k][rr, cc].astype(np.float32).mean())
        for nm in gd.FUEL:
            v = static[sidx[nm]][rr, cc]
            e[nm] = float(np.nanmean(v[static[sidx["fuel_valid"]][rr, cc] > 0])) if (static[sidx["fuel_valid"]][rr, cc] > 0).any() else np.nan
        dd = (rows.lon - f["lon"]) ** 2 + (rows.lat - f["lat"]) ** 2
        ok = rows[dd < 0.4 ** 2]
        e.update(size_cells=0, n_t=0, g0=0, days_active=0, cluster_found=bool(len(ok)), n_new_t0=0.0)
        if len(ok):
            r = ok.loc[dd[ok.index].idxmin()]
            e.update(size_cells=int(r.size_cells), n_t=int(r.n_t), g0=int(r.g0), days_active=int(r.days_active), event=r.event)
            e["n_new_t0"] = float(np.clip(np.expm1(m.predict(pd.DataFrame([r])[m.feature_name()])[0]), 0, None))
        out[fire] = e
    (B / "fires_feat.json").write_text(json.dumps(out, indent=1))
    pred = json.loads((G / "heldout_pred.json").read_text())
    diff = max(abs(out[f]["n_new_t0"] - pred[f]["n_new_t0"]) for f in pred)
    log.info("%d fires; max |n_new_t0 - heldout_pred| over the 12 = %.2e; clusters found %d", len(out), diff, sum(v["cluster_found"] for v in out.values()))


def q90_cells(feat):
    fires = list(feat)
    d = pd.DataFrame([feat[f] for f in fires])
    return {arm: dict(zip(fires, Analog(arm).q(d).tolist())) for arm in ARMS}


def members(fire):
    """ensemble member output dirs that exist locally: fuelwind16 + physics3 runs, else the control (adj1) run"""
    pb = Path("data/perimeter_batch")
    mem = sorted(p.parent for p in list((pb / fire / "runs" / "fuelwind16").glob("*/outputs/run.json")) +
                 list((pb / fire / "runs" / "physics3").glob("*/outputs/run.json")) +
                 list((pb / fire / "runs" / "physics3").glob("outputs/run.json")))
    return mem


def control_dir(fire):
    for p in (HELD / fire / "control" / "outputs", Path("data/perimeter_batch/aug2_local") / fire / "adj1" / "outputs",
              Path("data/demo_pull/runs") / fire / "runs" / "physics3" / "adj1" / "outputs",
              Path("data/perimeter_batch") / fire / "runs" / "physics3" / "adj1" / "outputs"):
        if (p / "run.json").exists():
            return p
    raise FileNotFoundError(fire)


def cut(src_dirs, area_km2, dst=None):
    """cut the union (earliest arrival) of the arrival maps at the area; returns (toa_cut, info). Writes a tif if dst."""
    import rasterio
    from selfcal_adj import load_toa
    ts = [load_toa(d) for d in src_dirs]
    toa, valid, tf, crs, res, prof = ts[0]
    used = [str(src_dirs[0])]
    toa = np.where(valid, toa, np.inf)
    for d, (t2, v2, *_ ) in zip(src_dirs[1:], ts[1:]):
        if t2.shape != toa.shape:
            log.warning("skip member %s: grid differs", d)
            continue
        toa = np.minimum(toa, np.where(v2, t2, np.inf))
        used.append(str(d))
    valid = np.isfinite(toa)
    toa = np.where(valid, toa, 0)
    cell_km2 = res[0] * res[1] / 1e6
    grow = valid & (toa > 0)
    n_t = area_km2 / cell_km2
    times = np.sort(toa[grow])
    tau = 0.0 if n_t < 1 else (np.inf if n_t >= len(times) else times[int(n_t) - 1])
    out = np.where(valid & (toa <= tau), toa, -1).astype(prof["dtype"])
    if dst is not None:
        Path(dst).mkdir(parents=True, exist_ok=True)
        tif = sorted(Path(src_dirs[0]).glob("time_of_arrival_*.tif"))[-1]
        with rasterio.open(Path(dst) / tif.name, "w", **prof) as o:
            o.write(out, 1)
        shutil.copy(Path(src_dirs[0]) / "run.json", Path(dst) / "run.json")
    return out, {"members": used, "target_cells": float(n_t), "available_growth_cells": int(grow.sum()),
                 "kept_growth_cells": int((out > 0).sum())}


def k_conv():
    return json.loads((G / "area_conversion.json").read_text())["k_km2_per_cell"]


def heldout():
    from edge_pilot_score import utc
    from selfcal_adj import load_toa, raster_of
    feat = json.loads((B / "fires_feat.json").read_text())
    man = json.loads(Path(MAN).read_text())
    q = q90_cells({f["fire"]: feat[f["fire"]] for f in man["fires"]})
    k = k_conv()
    pred = json.loads((G / "heldout_pred.json").read_text())
    T = utc(man["issue"])
    recs = []
    for f in man["fires"]:
        fire = f["fire"]
        obs = json.loads(Path(f["observed"]).read_text())
        per = {utc(p["time"]): p["geometry"] for p in obs["perimeters"]}
        base = HELD / fire / "control" / "outputs"
        toa0, valid0, tf, crs, res, _ = load_toa(base)
        cell = res[0] * res[1] / 1e6
        s_r = raster_of(per[max(t for t in per if t <= T)], toa0.shape, tf, crs)
        t_r = raster_of(per[T + dt.timedelta(hours=12)], toa0.shape, tf, crs)
        O = t_r & ~s_r
        lower_a = k * feat[fire]["n_new_t0"] * HORIZON_SHARE
        cases = {"no_growth": np.zeros_like(O), "control": (valid0 & (toa0 > 0)) & ~s_r}
        cases["lower"] = cut([base], lower_a)[0] > 0
        cases["lower_t1"] = load_toa(EVAL / fire / "areacon_t1" / "outputs")[0] > 0
        for arm in ARMS:
            ua = max(lower_a, k * q[arm][fire] * HORIZON_SHARE)
            cases["upper_" + arm] = cut([base], ua)[0] > 0
            cases["upper_" + arm] &= True
        for nm, P in cases.items():
            P = P & ~s_r
            inter, uni = (P & O).sum(), (P | O).sum()
            fin, tru = s_r | P, t_r
            recs.append({"fire": fire, "case": nm, "pred_km2": float(P.sum() * cell), "obs_km2": float(O.sum() * cell),
                         "growth_iou": float(inter / uni) if uni else np.nan, "recall": float(inter / O.sum()) if O.sum() else np.nan,
                         "false_alarm_km2": float((P & ~O).sum() * cell), "whole_iou": float((fin & tru).sum() / (fin | tru).sum()),
                         "q90_cells": {a: q[a][fire] for a in ARMS}[nm[6:]] if nm.startswith("upper_") else np.nan})
    d = pd.DataFrame(recs)
    d.to_csv(B / "heldout_per_fire.csv", index=False)
    g = d.groupby("case", sort=False)
    t = pd.DataFrame({"n_fires": g.fire.count(), "growth_iou_mean": g.growth_iou.mean(), "growth_iou_median": g.growth_iou.median(),
                      "recall_mean": g.recall.mean(), "recall_pooled": g.apply(lambda x: (x.recall * x.obs_km2).sum() / x.obs_km2[x.recall.notna()].sum()),
                      "false_alarm_km2_mean": g.false_alarm_km2.mean(), "false_alarm_km2_median": g.false_alarm_km2.median(),
                      "pred_km2_total": g.pred_km2.sum(), "whole_iou_mean": g.whole_iou.mean()})
    obs_tot = d[d.case == "control"].obs_km2.sum()
    L = ["# Lower / upper / control / no growth on the 12 frozen held-out fires (+12 h vs FEDS)", "",
         f"Observed growth total {obs_tot:.0f} km2 over {int(d[d.case=='control'].obs_km2.gt(0).sum())} fires with growth. recall = share of observed growth area captured "
         "(mean of per-fire; pooled = area-weighted); false alarm = predicted growth outside observed growth. lower = areacon_t0 (honest, day-t features); lower_t1 = entry 24's t1.", "",
         t.round(3).to_markdown()]
    (B / "heldout_table.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


def snapshot_place(feds_id):
    SNAP = Path("earth/public/data/fires/feds/2026/08")
    for name in ("02-1200.json", "02-0000.json"):
        for f in json.loads((SNAP / name).read_text())["features"]:
            if str(f["properties"].get("id")) == str(feds_id):
                return SNAP / name
    return None


def export():
    feat = json.loads((B / "fires_feat.json").read_text())
    fl = fire_list()
    q = q90_cells(feat)["harm"]
    k = k_conv()
    idx, cat = [], {}
    for fire, f in fl.items():
        if "fire_id" not in f:
            continue
        ctl = control_dir(fire)
        mem = members(fire) or [ctl]
        la = k * feat[fire]["n_new_t0"] * HORIZON_SHARE
        ua = max(la, k * q[fire] * HORIZON_SHARE)
        ent = {"fire_id": f["fire_id"], "name": f["name"], "issued": "2026-08-02T12:00:00Z", "lower_area_km2": la, "upper_area_km2": ua,
               "analog_q90_cells": q[fire], "predicted_cells_t0": feat[fire]["n_new_t0"], "cluster_found": feat[fire]["cluster_found"],
               "footprint_source": "ensemble_union" if len(mem) > 1 else "control", "n_members": len(mem)}
        for case, a, src in (("lower", la, [ctl]), ("upper", ua, mem)):
            run = B / "runs" / fire / case
            _, info = cut(src, a, run / "outputs")
            model = ("ELMFIRE physics, area-constrained: LOWER case (most likely): growth model (harmonized 2003-2019 + VIIRS record) predicted area" if case == "lower"
                     else f"ELMFIRE physics, area-constrained: UPPER case (worst case): q{int(Q*100)} of next-day growth of {K} analog past fire-days "
                          f"(harmonized record), footprint = {'union of ' + str(len(mem)) + ' ensemble members' if len(mem) > 1 else 'control run'}")
            tmp = B / "tmp" / fire / case
            shutil.rmtree(tmp, ignore_errors=True)
            snap = snapshot_place(f["fire_id"])
            cmd = ["uv", "run", "--script", "scripts/forecast_to_geojson.py", str(run / "outputs"), str(fire_file(fire, "fire-state.tif")),
                   "2026-08-02T12:00", str(f["fire_id"]), f["name"], model, str(tmp), "3,6,12"] + (["--perimeter", str(snap)] if snap else [])
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            dst = OUTDIR / str(f["fire_id"]) / f"2026-08-02T1200-{case}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            doc = json.loads((tmp / str(f["fire_id"]) / "2026-08-02T1200.json").read_text())
            doc["bound"] = {"case": case, "area_km2": a, "footprint_members": info["members"] if case == "upper" else [str(ctl)],
                            "analog_q90_cells": q[fire], "predicted_cells_t0": feat[fire]["n_new_t0"]}
            dst.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
            ent["path_" + case] = f"{f['fire_id']}/{dst.name}"
        idx.append(ent)
        log.info("%s lower %.1f km2 upper %.1f km2 (%s)", fire, la, ua, ent["footprint_source"])
    OUTDIR.mkdir(parents=True, exist_ok=True)
    idx.sort(key=lambda e: str(e["fire_id"]))
    (OUTDIR / "index.json").write_text(json.dumps({"forecasts": idx, "q": Q, "k_analogs": K, "k_km2_per_cell": k,
                                                    "note": "lower = area-constrained growth model; upper = analog q90 area, ensemble-union footprint where available"}, indent=1))
    shutil.rmtree(B / "tmp", ignore_errors=True)
    log.info("exported %d fires", len(idx))


if __name__ == "__main__":
    B.mkdir(parents=True, exist_ok=True)
    {"tail": tail, "features": features, "heldout": heldout, "export": export}[sys.argv[1]]()
