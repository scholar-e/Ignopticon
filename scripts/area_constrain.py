#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pandas", "pyarrow", "lightgbm", "rasterio", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Area-constrained ELMFIRE (MODELLING_LOG.md entry 23): the growth model says HOW MUCH, ELMFIRE's arrival map WHERE.

  area_constrain.py fit                  cells -> km2 conversion from FEDS growth (data/growth/area_conversion.json)
  area_constrain.py issue                growth-model features and predicted new cells for the 12 frozen held-out fires
                                         (data/growth/heldout_pred.json); features from Aug 1 history and gridMET weather
                                         (Aug 1; t1 variant also Aug 2 gridMET, a perfect-forecast stand-in, flagged)
  area_constrain.py run                  cut the control arrival maps at the predicted area, write the arms under
                                         data/growth/heldout_eval/FIRE/ARM (control symlinked); score with
                                         scripts/edge_pilot_score.py and write data/growth/heldout_table.md
Predicted area (km2) = K_KM2_PER_CELL * predicted new cells (24 h, day t+1) * HORIZON_SHARE (0.5: the 12 h forecast
is half the day; growth taken as uniform over the day, an assumption).
"""
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import growth_dataset as gd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)
MAN = "data/edge_causal/front_weighted/manifest.json"
HELD = "data/edge_causal/front_weighted/heldout"
OUT = Path("data/growth")
EVAL = OUT / "heldout_eval"
HORIZON_SHARE = 0.5
ISSUE_DOY = 213   # 2026-08-01, the last complete UTC day at issue (Aug 2 12:00)


def utc(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def feds_cum_area(perimeters):
    """cumulative FEDS area (km2) vs time: sum of areas at each perimeter time"""
    by = {}
    for p in perimeters:
        by[utc(p["time"])] = by.get(utc(p["time"]), 0.0) + p["area_km2"]
    t = sorted(by)
    return t, np.array([by[x] for x in t])


def fit():
    """k = sum(FEDS growth km2 on day t+1) / sum(observed new cells on t+1) over matched event-days (through origin)."""
    v = pd.read_parquet(OUT / "rows_viirs.parquet")
    recs = []
    for p in sorted(Path("data/training/fires").glob("20[12]*/fire-observed.json")):
        o = json.loads(p.read_text())
        lon, lat = o["center"]
        t, a = feds_cum_area(o["perimeters"])
        sec = np.array([(x - t[0]).total_seconds() for x in t])
        y = int(p.parent.name[:4])
        rr = v[(v.year == y) & ((v.lon - lon) ** 2 + (v.lat - lat) ** 2 < (o["radius_km"] / 111.0 * 1.5) ** 2)]
        for _, r in rr.iterrows():
            d0 = dt.datetime(y, 1, 1) + dt.timedelta(days=int(r.doy) - 1)          # start of day t+1 is d0 + 1 d
            s0, s1 = [(d0 + dt.timedelta(days=k) - t[0]).total_seconds() for k in (1, 2)]
            if s0 < sec[0] or s1 > sec[-1]:
                continue
            recs.append({"fire": p.parent.name, "event": r.event, "date": str((d0 + dt.timedelta(days=1)).date()),
                         "n_new": int(r.n_new), "feds_km2": float(np.interp(s1, sec, a) - np.interp(s0, sec, a))})
    d = pd.DataFrame(recs)
    k = d.feds_km2.sum() / max(d.n_new.sum(), 1)
    from scipy.stats import spearmanr
    out = {"k_km2_per_cell": float(k), "n_event_days": len(d), "n_fires": int(d.fire.nunique()),
           "spearman_cells_vs_km2": float(spearmanr(d.n_new, d.feds_km2)[0]),
           "r2_through_origin": float(1 - ((d.feds_km2 - k * d.n_new) ** 2).sum() / ((d.feds_km2 - d.feds_km2.mean()) ** 2).sum()),
           "mean_feds_km2": float(d.feds_km2.mean()), "mean_cells": float(d.n_new.mean()),
           "per_fire": {f: {"k": float(g.feds_km2.sum() / max(g.n_new.sum(), 1)), "days": len(g)} for f, g in d.groupby("fire")},
           "note": "in-sample: 8 FEDS-archive fires 2018-2021 (the three 2020-21 ones are also the front-rule dev fires)"}
    d.to_csv(OUT / "area_conversion_pairs.csv", index=False)
    (OUT / "area_conversion.json").write_text(json.dumps(out, indent=1))
    log.info("%s", json.dumps({k_: v_ for k_, v_ in out.items() if k_ != "per_fire"}))


def issue():
    import lightgbm as lgb
    man = json.loads(Path(MAN).read_text())
    ys, F, C, start = gd.load_conv("viirs")
    pers = gd.persistent_mask(F, ys, start)
    z5 = np.load("data/harmonize/grid/VNP14A1_2025.npz")
    z6 = np.load("data/harmonize/grid/VNP14A1_2026.npz")
    f6 = z6["fire"] > 0
    f6[ISSUE_DOY:] = False                       # nothing after Aug 1 (issue is Aug 2 12:00)
    Fs = np.concatenate([z5["fire"] > 0, f6])
    Cs = np.concatenate([z5["clear"] > 0, z6["clear"] > 0])
    wz = np.load("data/harmonize/weather/weather_2026.npz")
    wx = np.stack([wz[k] for k in gd.WX], 1)     # (316, 7, 260, 590); the training weather is gridMET the same way
    s = np.load(f"{gd.T}/static.npz")
    static, names = s["x"].astype(np.float32), [str(n) for n in s["names"]]
    gd.MIN_DAYS = gd.MIN_CELLDAYS = gd.MIN_PEAK_CELLS = 1     # relaxed: tiny clusters must still get a row
    rows = pd.DataFrame(gd.do_year(("viirs", 2026, Fs, Cs, 365, 730, pers, static, names, wx)))
    rows = rows[rows.doy == ISSUE_DOY]
    log.info("clusters with a row on Aug 1: %d", len(rows))
    models = {v: lgb.Booster(model_file=f"data/growth/runs/harm_v{v}_log1p15.txt") for v in ("t1", "t0")}
    out = {}
    for f in man["fires"]:
        d2 = (rows.lon - f["lon"]) ** 2 + (rows.lat - f["lat"]) ** 2
        ok = rows[d2 < 0.4 ** 2]
        e = {"cluster_found": bool(len(ok))}
        if len(ok):
            r = ok.loc[(d2[ok.index]).idxmin()]
            e.update(event=r.event, size_cells=int(r.size_cells), n_t=int(r.n_t), g0=int(r.g0))
            for v, m in models.items():
                e["n_new_" + v] = float(np.clip(np.expm1(m.predict(pd.DataFrame([r])[m.feature_name()])[0]), 0, None))
        else:
            e.update(n_new_t1=0.0, n_new_t0=0.0)
        out[f["fire"]] = e
        log.info("%s %s", f["fire"], e)
    (OUT / "heldout_pred.json").write_text(json.dumps(out, indent=1))


def constrain(src_dir, dst_dir, area_km2):
    import rasterio
    from selfcal_adj import load_toa
    toa, valid, tf, crs, res, prof = load_toa(src_dir)
    cell_km2 = res[0] * res[1] / 1e6
    grow = valid & (toa > 0)
    n_target = area_km2 / cell_km2
    times = np.sort(toa[grow])
    if n_target < 1:
        tau = 0.0
    elif n_target >= len(times):
        tau = np.inf
    else:
        tau = times[int(n_target) - 1]
    out = np.where(valid & (toa <= tau), toa, -1).astype(toa.dtype)
    Path(dst_dir).mkdir(parents=True, exist_ok=True)
    tif = sorted(Path(src_dir).glob("time_of_arrival_*.tif"))[-1]
    with rasterio.open(Path(dst_dir) / tif.name, "w", **prof) as dst:
        dst.write(out, 1)
    return {"target_cells": float(n_target), "available_growth_cells": int(grow.sum()), "tau_h": float(tau / 3600) if np.isfinite(tau) else None,
            "kept_growth_cells": int((out > 0).sum())}


def run():
    import shutil
    from edge_pilot_score import utc as _u
    import rasterio
    from selfcal_adj import load_toa, raster_of
    man = json.loads(Path(MAN).read_text())
    pred = json.loads((OUT / "heldout_pred.json").read_text())
    k = json.loads((OUT / "area_conversion.json").read_text())["k_km2_per_cell"]
    info = {}
    for f in man["fires"]:
        fire = f["fire"]
        base = Path(HELD) / fire / "control"
        d = EVAL / fire
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "control").exists():
            (d / "control").symlink_to(base.resolve())
        obs = json.loads(Path(f["observed"]).read_text())
        T = _u(man["issue"])
        per = {_u(p["time"]): p["geometry"] for p in obs["perimeters"]}
        st = per[max(t for t in per if t <= T)]
        toa, valid, tf, crs, res, _ = load_toa(base / "outputs")
        s_r = raster_of(st, toa.shape, tf, crs)
        t_r = raster_of(per[T + dt.timedelta(hours=12)], toa.shape, tf, crs)
        cell_km2 = res[0] * res[1] / 1e6
        areas = {"areacon_t1": k * pred[fire]["n_new_t1"] * HORIZON_SHARE, "areacon_t0": k * pred[fire]["n_new_t0"] * HORIZON_SHARE,
                 "areacon_oracle": float((t_r & ~s_r).sum() * cell_km2)}
        info[fire] = {"pred_area_km2": areas}
        for arm, a in areas.items():
            dst = d / arm
            info[fire][arm] = constrain(base / "outputs", dst / "outputs", a)
            for fn in ("run.json",):
                shutil.copy(base / "outputs" / fn, dst / "outputs" / fn)
            shutil.copy(base / "case.json", dst / "case.json")
    (OUT / "heldout_constrain.json").write_text(json.dumps(info, indent=1))


if __name__ == "__main__":
    {"fit": fit, "issue": issue, "run": run}[sys.argv[1]]()
