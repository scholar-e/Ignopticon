#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["h5py", "numpy", "scipy", "pyproj", "rasterio", "shapely>=2.1"]
# ///
"""Scores every finished training run (make_training_members.py, elmfire_forecast.sh) against VIIRS, and joins the
fire-issue conditions as features: one row per run, for learning ELMFIRE's corrections (data/training/NOTES.md).

Scores (heat_frames.py's observation operator and scoring, so numbers compare with MODELLING_LOG.md entries 5-9):
  - per overpass in (issue, issue + 24 h]: hits, misses, false alarms with one 375 m cell of offset allowed, cells
    obscured on that pass (if obscured_cells.py ran) left out; pooled CSI = sum hits / sum (hits + misses + false
    alarms) over those passes. The issue's cut observation file has only data up to issue + 24 h.
  - IoU of the run's burned area with the last FEDS perimeter in (issue, issue + 24 h], where FEDS has one.
  - a "nogrowth" row per fire-issue (the state at issue persisted, smouldering only), the floor to beat.
  - runtime_s: elmfire.log's mtime minus build.log's (ELMFIRE's own run time on one core).
Features per fire-issue (prefixed f_): fuel group fractions, canopy cover/height, elevation, slope and their spread
in the 3 km around the cells hot at issue (landscape.tif); HRRR 24 h means/extremes there (20-ft wind, 1-h
moisture); gridMET (data/harmonize/weather) at the fire's 0.1 degree cell on the issue day and the next (wind, VPD,
ERC, FM100, precipitation; the next day's is perfect-forecast weather, flag f_gm1_*); month, day of year, lat/lon,
region and fuel group from the catalog, days since the event started; fire size at issue (state cells known
burned, km2) and hot cells in the 6 h before; harmonized activity (data/harmonize/out-base-13y) p_viirs and VIIRS
fire pixels summed over the 5 x 5 cells around the fire on the issue day and the 3 days before.
Settings parsed from each member's overrides (adj, wsmult, moist, lh, adj_gr, adj_sh, adj_timber, adj_wind_exp).

Usage: score_training_runs.py OUT.csv [FIRE_ID ...]   (default: every fire with runs) [--prune] [-j N]
       --prune deletes each scored run's scratch/landscape.tif copy (11 MB of a 14 MB run).
Writes OUT.csv; rows of other fire-issues already in OUT.csv are kept, the fire-issues scored now replaced.
"""
import csv
import datetime
import glob
import json
import logging
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import rasterio
import shapely
from rasterio.features import rasterize
from scipy.ndimage import binary_dilation
from shapely.geometry import mapping, shape

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import heat_frames as hf  # noqa: E402
from fire_observations import CELL, Grid  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
logging.getLogger("heat_frames").setLevel(logging.WARNING)
log = logging.getLogger("score_training_runs")

FIRES = "data/training/fires"
RUNS = "data/training/runs"
WEATHER = "data/harmonize/weather/weather_%d.npz"
HARMONIZED = "data/harmonize/out-base-13y/harmonized_%d.npz"
GROUPS = {"gr": (101, 110), "gs": (121, 125), "sh": (141, 150), "tu": (161, 166), "tl": (181, 190),
          "sb": (201, 205), "nb": (91, 100)}
SETTINGS = {"ADJ": ("adj", 1.0), "WSMULT": ("wsmult", 1.0), "MOIST": ("moist", 0.0),
            "LH_MOISTURE_CONTENT": ("lh", 30.0), "ADJ_GR": ("adj_gr", None), "ADJ_SH": ("adj_sh", None),
            "ADJ_TIMBER": ("adj_timber", None), "ADJ_WIND_EXP": ("adj_wind_exp", 0.0)}


def T(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=datetime.timezone.utc)


def settings_of(overrides):
    out = {v[0]: v[1] for v in SETTINGS.values()}
    for item in overrides:
        k, _, v = item.partition("=")
        if k.upper() in SETTINGS:
            out[SETTINGS[k.upper()][0]] = float(v)
    for g in ("adj_gr", "adj_sh", "adj_timber"):  # groups not set keep ADJ
        if out[g] is None:
            out[g] = out["adj"]
    return out


def features(fire_dir, meta, item, obs, grid, issue, hot):
    """Conditions of one fire-issue (see the module docstring)."""
    f = {}
    row = meta["catalog"]
    lon, lat = float(row["lon"]), float(row["lat"])
    t = T(item["issue"])
    f.update(f_month=t.month, f_doy=t.timetuple().tm_yday, f_lat=lat, f_lon=lon, f_region=row["region"],
             f_fuel_group_catalog=row["fuel_group"], f_year=t.year,
             f_days_since_start=(t.date() - datetime.date.fromisoformat(row["start"])).days,
             f_event_celldays=int(row["fire_celldays"]), f_hot_cells=int(hot.sum()))
    # Fire size at issue: state cells burned by issue (as heat_frames' smouldering state).
    with rasterio.open(os.path.join(fire_dir, "fire-state.tif")) as src:
        state = src.read(1).astype(np.float64)
        nodata = src.nodata
    reference = T(obs["reference"]).timestamp()
    f["f_burned_km2"] = round(float(((state != nodata) & (state + reference <= issue)).sum()) * CELL ** 2 / 1e6, 2)
    # Landscape around the hot cells (3 km = 8 state cells).
    near = binary_dilation(hot, iterations=8) if hot.any() else np.ones_like(hot)
    refine = meta["refine"]
    with rasterio.open(os.path.join(fire_dir, "landscape.tif")) as src:
        land = src.read()
    m = np.kron(near, np.ones((refine, refine), bool))[:land.shape[1], :land.shape[2]]
    fuel = land[3][m]
    valid = fuel > 0
    for g, (a, b) in GROUPS.items():
        f["f_frac_" + g] = round(float(((fuel >= a) & (fuel < b)).sum() / max(valid.sum(), 1)), 3)
    for i, name in ((0, "elev"), (1, "slope"), (4, "cc"), (5, "ch")):
        v = land[i][m].astype(float)
        v = v[v > -1000]
        scale = 0.1 if name == "ch" else 1.0
        f["f_" + name] = round(float(v.mean()) * scale, 1) if len(v) else np.nan
        if name in ("elev", "slope"):
            f["f_%s_sd" % name] = round(float(v.std()) * scale, 1) if len(v) else np.nan
    # HRRR over the same area (weather on the state grid).
    for name in ("ws", "m1"):
        with rasterio.open(os.path.join(item["wx"], name + ".tif")) as src:
            wx = src.read()[:25]
        series = wx[:, near].mean(1)
        f["f_hrrr_%s_mean" % name] = round(float(series.mean()), 2)
        f["f_hrrr_%s_%s" % (name, "max" if name == "ws" else "min")] = round(
            float(series.max() if name == "ws" else series.min()), 2)
    # gridMET and harmonized activity at the fire's 0.1 degree cell.
    r, c = int((50 - lat) / 0.1), int((lon + 125) / 0.1)
    doy = t.timetuple().tm_yday - 1
    if os.path.exists(WEATHER % t.year):
        with np.load(WEATHER % t.year) as w:
            for k in ("wind", "vpd", "erc", "fm100", "pr"):
                a = w[k]
                f["f_gm0_" + k] = round(float(a[doy, r, c]), 2)
                f["f_gm1_" + k] = round(float(a[min(doy + 1, a.shape[0] - 1), r, c]), 2)
    if os.path.exists(HARMONIZED % t.year):
        with np.load(HARMONIZED % t.year) as h:
            box = (slice(max(doy - 3, 0), doy + 1), slice(r - 2, r + 3), slice(c - 2, c + 3))
            f["f_act_p_viirs"] = round(float(h["p_viirs"][box].astype(np.float32).sum()), 2)
            f["f_act_viirs_fire"] = int(h["viirs_fire"][box].sum())
            f["f_act_modis_fire"] = int(h["modis_fire"][box].sum())
    return f


def score_issue(job):
    fire, item, prune = job
    fire_dir = os.path.join(FIRES, fire)
    meta = json.load(open(os.path.join(fire_dir, "case.json")))
    stamp = item["issue"][:13].replace("-", "").replace(":", "")
    obs = json.load(open(item["observed"]))
    grid = Grid(obs["center"][0], obs["center"][1], obs["radius_km"] * 1000)
    n = grid.n
    reference = T(obs["reference"]).timestamp()
    issue = T(item["issue"]).timestamp()
    end = issue + 24 * 3600
    with rasterio.open(os.path.join(fire_dir, "fire-state.tif")) as src:
        state = src.read(1).astype(np.float64)
        nodata = src.nodata
    before = (state != nodata) & (state + reference <= issue)
    sr, sc = np.nonzero(before)
    state_cells = {"row": sr, "col": sc, "arrival": state[sr, sc] + reference,
                   "energy": np.full(len(sr), hf.DEFAULT_HPA * CELL ** 2),
                   "flame": np.full(len(sr), max(CELL / hf.STATE_SPREAD, hf.FLAME_SECONDS))}
    hot = np.zeros((n, n), bool)
    frames = []
    for fr in obs["frames"]:
        t = T(fr["time"]).timestamp()
        cells = np.zeros((n, n), bool)
        for lo, la, heat, src_ in fr["cells"]:
            r, c = grid.cell(lo, la)
            if r >= 0:
                cells[r, c] = True
        if issue - 6 * 3600 <= t <= issue:
            hot |= cells
        if issue < t <= end:
            unseen = np.zeros((n, n), bool)
            for r, c in fr.get("obscured", []):
                unseen[r, c] = True
            frames.append((t, fr, cells, unseen & ~cells))
    perim = [p for p in obs["perimeters"] if issue < T(p["time"]).timestamp() <= end]
    pmask = None
    if perim:
        p = perim[-1]
        projected = shapely.transform(shape(p["geometry"]), lambda xy: np.column_stack(grid.to_xy(xy[:, 0], xy[:, 1])))
        pmask = rasterize([(mapping(projected), 1)], out_shape=(n, n), transform=grid.transform,
                          dtype=np.uint8).astype(bool)
    feats = features(fire_dir, meta, item, obs, grid, issue, hot)
    empty = {"row": np.array([], int), "col": np.array([], int), "arrival": np.array([]), "energy": np.array([]),
             "flame": np.array([])}
    rows = []
    run_dirs = [("nogrowth", None)] + [(os.path.basename(d), d) for d in
                                       sorted(glob.glob(os.path.join(RUNS, fire, stamp, "*")))
                                       if os.path.exists(os.path.join(d, "outputs", "run.json"))]
    for name, d in run_dirs:
        if d is None:
            run, info, runtime = empty, {"overrides": []}, np.nan
        else:
            run = hf.read_run(os.path.join(d, "outputs"), grid, issue)
            info = json.load(open(os.path.join(d, "outputs", "run.json")))
            try:
                runtime = os.path.getmtime(os.path.join(d, "elmfire.log")) - os.path.getmtime(os.path.join(d, "build.log"))
            except OSError:
                runtime = np.nan
        h = m_ = fa = pred_n = obs_n = 0
        per_pass = []
        for t, fr, observed, unseen in frames:
            solar = (T(fr["time"]).hour + T(fr["time"]).minute / 60 + obs["center"][0] / 15) % 24
            bg = hf.BACKGROUND_K["day" if 6 <= solar < 18 else "night"]
            frp, detected = hf.predicted(run, state_cells, t, fr["pixel_km2"], bg, n)
            s = hf.score(detected & ~unseen, observed)
            h, m_, fa = h + s["hits"], m_ + s["misses"], fa + s["false_alarms"]
            pred_n += int(detected.sum())
            obs_n += int(observed.sum())
            per_pass.append(s["csi"])
        iou = model_km2 = np.nan
        if pmask is not None:
            burned = before.copy()
            by = run["arrival"] <= T(perim[-1]["time"]).timestamp()
            burned[run["row"][by], run["col"][by]] = True
            iou = round(float((burned & pmask).sum() / max((burned | pmask).sum(), 1)), 3)
            model_km2 = round(float(burned.sum()) * CELL ** 2 / 1e6, 1)
        burned24 = before.copy()
        burned24[run["row"], run["col"]] = True
        row = {"fire": fire, "issue": item["issue"], "member": name, "case": d or "",
               "family": name.split("-")[0], **settings_of(info.get("overrides", [])),
               "passes": len(frames), "hits": h, "misses": m_, "false_alarms": fa,
               "csi_pooled": round(h / max(h + m_ + fa, 1), 4),
               "csi_mean_pass": round(float(np.mean(per_pass)), 4) if per_pass else np.nan,
               "predicted_cells": pred_n, "observed_cells": obs_n,
               "burned_24h_km2": round(float(burned24.sum()) * CELL ** 2 / 1e6, 1),
               "iou": iou, "iou_time": perim[-1]["time"] if perim else "", "model_km2": model_km2,
               "feds_km2": perim[-1]["area_km2"] if perim else np.nan,
               "runtime_s": round(runtime, 1) if runtime == runtime else np.nan,
               "elmfire_cells": meta["cells"] ** 2, "elmfire_cell_m": meta["elmfire_cell_m"], **feats}
        if name == "nogrowth":
            row.update(adj=0.0, adj_gr=0.0, adj_sh=0.0, adj_timber=0.0)
        rows.append(row)
        if prune and d:
            try:
                os.remove(os.path.join(d, "scratch", "landscape.tif"))
            except OSError:
                pass
    log.info("%s %s: %d runs, %d passes, best %s", fire, item["issue"], len(rows) - 1, len(frames),
             max(rows, key=lambda r: r["csi_pooled"])["member"] if rows else None)
    return rows


def main(argv):
    prune = "--prune" in argv
    jobs_n = 8
    if "-j" in argv:
        jobs_n = int(argv[argv.index("-j") + 1])
        argv = argv[:argv.index("-j")] + argv[argv.index("-j") + 2:]
    argv = [a for a in argv if a != "--prune"]
    out_csv, fires = argv[0], argv[1:]
    os.chdir(REPO)
    if not fires:
        fires = sorted(os.listdir(RUNS)) if os.path.isdir(RUNS) else []
    old = []
    if os.path.exists(out_csv):
        old = [r for r in csv.DictReader(open(out_csv))]
    jobs = []
    for fire in fires:
        meta = json.load(open(os.path.join(FIRES, fire, "case.json")))
        for item in meta.get("issues", []):
            stamp = item["issue"][:13].replace("-", "").replace(":", "")
            if glob.glob(os.path.join(RUNS, fire, stamp, "*", "outputs", "run.json")):
                jobs.append((fire, item, prune))
    redo = {(f, i["issue"]) for f, i, _ in jobs}
    old = [r for r in old if (r["fire"], r["issue"]) not in redo]  # rescored whole issues replace old rows
    rows = []
    with ProcessPoolExecutor(jobs_n) as pool:
        for r in pool.map(score_issue, jobs):
            rows.extend(r)
    rows = old + rows
    if not rows:
        sys.exit("no runs")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    log.info("wrote %s: %d rows", out_csv, len(rows))


if __name__ == "__main__":
    main(sys.argv[1:])
