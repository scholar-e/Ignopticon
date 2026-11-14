#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["h5py", "numpy", "scipy", "pyproj", "rasterio", "shapely>=2.1"]
# ///
"""Turn fire model runs into the heat VIIRS would see at each overpass, and score them against what it saw.

A run is ELMFIRE's output rasters: time_of_arrival_*.tif (s since ISSUE; nodata, negative, or missing = not burned
in the run) and, if dumped, flin_*.tif (fireline intensity, kW/m). Cells burned before ISSUE come from the observed
fire state (fire_observations.py), so their smoldering still shows. For each observed overpass at or after ISSUE:

  1. Heat release. A cell burned at t_a releases E = HPA x area: (1 - SMOLDER_FRACTION) of it while flaming, for
     the time the front takes to cross the cell (at least FLAME_SECONDS), the rest smoldering, decaying with
     e-folding time SMOLDER_SECONDS. HPA (heat per unit area, kJ/m2) is flin / spread rate, the rate being
     1 / |grad t_a|; without flin, DEFAULT_HPA.
  2. Radiation. FRP = RADIATIVE_FRACTION x heat release (0.146: Wooster et al. 2005's 0.368 kg of fuel per MJ of
     fire radiative energy, at 18.6 MJ/kg, the heat of combustion ELMFIRE uses).
  3. The sensor. FRP is summed into 375 m cells and becomes I4 brightness over a background of BACKGROUND_K by
     inverting the MIR method (FRP = A sigma / a x dL), A being that overpass's mean pixel footprint (scan x track)
     over the fire. A cell is predicted to be detected if that is DETECT_K above the background (91-100% of VIIRS
     detections are at least that much hotter than ground 3 km away, PLAN.md).

Cells the fire mask shows the satellite could not see on that pass (frame "obscured": cloud, glint, not processed)
are left out of the score, so a predicted fire under cloud is not a false alarm.
Scores per overpass compare predicted detections with observed hot cells (detections, or fire band only), allowing
one cell (375 m) of offset for geolocation; and at each later FEDS snapshot, the run's burned area against the
perimeter (IoU). All constants are uncalibrated first guesses: fitting them is part of PLAN.md M4.

Usage: heat_frames.py OBSERVED.json STATE.tif ISSUE OUT.json RUN_DIR [RUN_DIR ...]
       e.g. heat_frames.py data/fires/widemouth-2026-08-02-observed.json data/fires/widemouth-2026-08-02-state.tif \
                2026-08-02T12:00 earth/public/data/fires/frames/2026/08/02.json data/fires/runs/widemouth-ros*
Writes OUT.json for earth's "Heat frames" layer: the observed frames, each run's predicted frames and scores, and
the FEDS perimeters. A RUN_DIR's run.json, if any, names the run.
"""
import datetime
import glob
import json
import logging
import math
import os
import sys

import numpy as np
import rasterio
import shapely
from rasterio.features import rasterize
from scipy.ndimage import binary_dilation
from shapely.geometry import mapping, shape

from fire_observations import CELL, SIGMA, Grid, inverse_planck, mir_constant, planck

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

DEFAULT_HPA = 8000.0          # kJ/m2 where a run has no flin (and for cells burned before ISSUE)
FLAME_SECONDS = 60.0          # least flaming time at a point
SMOLDER_FRACTION = 0.3        # of a cell's heat, released after the front passes
SMOLDER_SECONDS = 2 * 3600.0  # e-folding time of that release
RADIATIVE_FRACTION = 0.146
DETECT_K = 10.0
BACKGROUND_K = {"day": 305.0, "night": 290.0}
STATE_SPREAD = 0.05           # m/s, for the flaming time of cells burned before ISSUE (375 m cells)


def parse_time(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "")).replace(tzinfo=datetime.timezone.utc)


def heat_release(dt, energy_kj, flame_s):
    """kW released dt seconds after arrival by a cell holding energy_kj."""
    flaming = np.where((dt >= 0) & (dt < flame_s), (1 - SMOLDER_FRACTION) / flame_s, 0.0)
    smolder = np.where(dt >= 0, SMOLDER_FRACTION / SMOLDER_SECONDS * np.exp(-np.maximum(dt, 0) / SMOLDER_SECONDS),
                       0.0)
    return energy_kj * (flaming + smolder)


def read_run(run_dir, grid, issue):
    """Burned cells of a run, as arrays: frame cell (row, col), arrival (epoch s), energy (kJ), flaming time (s);
    and its name."""
    toa_file = sorted(glob.glob(os.path.join(run_dir, "time_of_arrival_*.tif")))[-1]
    flin_files = sorted(glob.glob(os.path.join(run_dir, "flin_*.tif")))
    with rasterio.open(toa_file) as src:
        toa = src.read(1).astype(np.float64)
        nodata, transform, crs = src.nodata, src.transform, src.crs
    burned = np.isfinite(toa) & (toa >= 0) & ((toa != nodata) if nodata is not None else True)
    cell = abs(transform.a)
    gy, gx = np.gradient(np.where(burned, toa, np.nan), cell)
    slowness = np.hypot(gx, gy)  # s/m
    rate = np.where(np.isfinite(slowness) & (slowness > 0), 1 / np.maximum(slowness, 1e-6), np.nan)
    if flin_files:
        with rasterio.open(flin_files[-1]) as src:
            flin = src.read(1).astype(np.float64)
        hpa = np.where(np.isfinite(rate) & (flin > 0), flin / rate, DEFAULT_HPA)
    else:
        hpa = np.full(toa.shape, DEFAULT_HPA)
    rows, cols = np.nonzero(burned)
    x = transform.c + (cols + 0.5) * transform.a
    y = transform.f + (rows + 0.5) * transform.e
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(x, y)
    r, c = grid.cell(lon, lat)
    keep = r >= 0
    spread = np.nan_to_num(rate[rows, cols], nan=STATE_SPREAD)
    flame = np.maximum(cell / np.maximum(spread, 1e-3), FLAME_SECONDS)
    name = os.path.basename(os.path.normpath(run_dir))
    if name == "outputs":  # an ensemble member's (run_member.sh): named after the member
        name = os.path.basename(os.path.dirname(os.path.normpath(run_dir)))
    settings = {}
    if os.path.exists(os.path.join(run_dir, "run.json")):
        settings = json.load(open(os.path.join(run_dir, "run.json")))
    return {"name": name, "settings": settings, "row": r[keep], "col": c[keep],
            "arrival": issue + toa[rows, cols][keep], "energy": (hpa[rows, cols] * cell ** 2)[keep],
            "flame": flame[keep], "fine_burned": (rows[keep], cols[keep], cell)}


def predicted(run, state_cells, t, pixel_km2, background_k, n):
    """FRP (MW) and predicted detection per frame cell at epoch t."""
    frp = np.zeros((n, n))
    for cells in (state_cells, run):
        power_kw = heat_release(t - cells["arrival"], cells["energy"], cells["flame"])
        np.add.at(frp, (cells["row"], cells["col"]), RADIATIVE_FRACTION * power_kw / 1000)
    excess = mir_constant() * frp * 1e6 / (SIGMA * pixel_km2 * 1e6)
    kelvin = inverse_planck(planck(background_k) + excess)
    return frp, kelvin - background_k >= DETECT_K


def score(pred, observed):
    """Hits, misses and false alarms, allowing one cell of offset; CSI = hits / (hits + misses + false alarms)."""
    near_obs = binary_dilation(observed, np.ones((3, 3), bool))
    near_pred = binary_dilation(pred, np.ones((3, 3), bool))
    hits = int((observed & near_pred).sum())
    misses = int((observed & ~near_pred).sum())
    false = int((pred & ~near_obs).sum())
    return {"hits": hits, "misses": misses, "false_alarms": false,
            "csi": round(hits / max(hits + misses + false, 1), 3)}


def main(observed_json, state_tif, issue_text, out_json, *run_dirs):
    obs = json.load(open(observed_json))
    lon0, lat0 = obs["center"]
    grid = Grid(lon0, lat0, obs["radius_km"] * 1000)
    n = grid.n
    reference = parse_time(obs["reference"]).timestamp()
    issue = parse_time(issue_text).timestamp()

    with rasterio.open(state_tif) as src:
        state = src.read(1).astype(np.float64)
        state_nodata = src.nodata
    before = (state != state_nodata) & (state + reference <= issue)
    sr, sc = np.nonzero(before)
    state_cells = {"row": sr, "col": sc, "arrival": state[sr, sc] + reference,
                   "energy": np.full(len(sr), DEFAULT_HPA * CELL ** 2),
                   "flame": np.full(len(sr), max(CELL / STATE_SPREAD, FLAME_SECONDS))}
    burned_before = before.copy()

    lons, lats = grid.centers()
    perimeters = []
    for p in obs["perimeters"]:
        projected = shapely.transform(shape(p["geometry"]),
                                      lambda xy: np.column_stack(grid.to_xy(xy[:, 0], xy[:, 1])))
        mask = rasterize([(mapping(projected), 1)], out_shape=(n, n), transform=grid.transform,
                         dtype=np.uint8).astype(bool)
        perimeters.append(dict(p, mask=mask, seconds=parse_time(p["time"]).timestamp()))

    forecast_frames = [f for f in obs["frames"] if parse_time(f["time"]).timestamp() >= issue]
    runs = []
    for run_dir in run_dirs:
        run = read_run(run_dir, grid, issue)
        frames, scores = [], []
        for f in forecast_frames:
            t = parse_time(f["time"]).timestamp()
            solar = (parse_time(f["time"]).hour + parse_time(f["time"]).minute / 60 + lon0 / 15) % 24
            background = BACKGROUND_K["day" if 6 <= solar < 18 else "night"]
            frp, detected = predicted(run, state_cells, t, f["pixel_km2"], background, n)
            observed = np.zeros((n, n), bool)
            for lo, la, heat, src in f["cells"]:
                r, c = grid.cell(lo, la)
                observed[r, c] = True
            # Cells the satellite couldn't see on this pass (cloud, glint, not processed; fire_observations.py's
            # "obscured", from the fire mask) can't verify a prediction either way: left out of the score.
            unseen = np.zeros((n, n), bool)
            for r, c in f.get("obscured", []):
                unseen[r, c] = True
            unseen &= ~observed
            s = score(detected & ~unseen, observed)
            s.update(time=f["time"], predicted=int(detected.sum()), observed=int(observed.sum()),
                     obscured=int(unseen.sum()), predicted_obscured=int((detected & unseen).sum()),
                     frp_observed_mw=f["frp_mw"], frp_model_mw=round(float(frp.sum()), 1))
            scores.append(s)
            r, c = np.nonzero(frp >= 0.5)  # MW: below this a cell is not worth drawing
            frames.append({"time": f["time"], "cells": [
                [round(float(lons[i, j]), 5), round(float(lats[i, j]), 5), round(float(frp[i, j]), 2),
                 int(detected[i, j])] for i, j in zip(r, c)]})
            log.info("%s %s: predicted %d detections, %.0f MW (observed %d hot cells, %.0f MW); CSI %.2f",
                     run["name"], f["time"], int(detected.sum()), frp.sum(), int(observed.sum()), f["frp_mw"],
                     s["csi"])
        verification = []
        for p in perimeters:
            if p["seconds"] <= issue:
                continue
            burned = burned_before.copy()
            by_then = run["arrival"] <= p["seconds"]
            burned[run["row"][by_then], run["col"][by_then]] = True
            iou = (burned & p["mask"]).sum() / max((burned | p["mask"]).sum(), 1)
            verification.append({"time": p["time"], "iou": round(float(iou), 3),
                                 "model_km2": round(float(burned.sum()) * CELL ** 2 / 1e6, 1),
                                 "feds_km2": p["area_km2"]})
            log.info("%s at %s: burned %.0f km2 vs FEDS %.0f km2, IoU %.2f", run["name"], p["time"],
                     verification[-1]["model_km2"], p["area_km2"], iou)
        runs.append({"name": run["name"], "settings": run["settings"], "frames": frames, "scores": scores,
                     "perimeters": verification,
                     "mean_csi": round(float(np.mean([s["csi"] for s in scores])), 3) if scores else None})
    runs.sort(key=lambda r: -(r["mean_csi"] or 0))

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    fires = []
    if os.path.exists(out_json):  # one file per day holds every fire's frames; replace this fire's
        fires = [f for f in json.load(open(out_json))["fires"] if f["center"] != obs["center"]]
    fires.append({"center": obs["center"], "radius_km": obs["radius_km"], "cell_m": CELL,
                  "issue": datetime.datetime.fromtimestamp(issue, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                  "observed": obs["frames"], "runs": runs,
                  "perimeters": [{k: v for k, v in p.items() if k not in ("mask", "seconds")} for p in perimeters],
                  "settings": {"default_hpa_kj_m2": DEFAULT_HPA, "smolder_fraction": SMOLDER_FRACTION,
                               "smolder_hours": SMOLDER_SECONDS / 3600, "radiative_fraction": RADIATIVE_FRACTION,
                               "detect_k": DETECT_K}})
    json.dump({"fires": fires}, open(out_json, "w"), separators=(",", ":"))
    log.info("wrote %s (%d runs; best mean CSI %s: %s)", out_json, len(runs),
             runs[0]["mean_csi"] if runs else None, runs[0]["name"] if runs else None)


if __name__ == "__main__":
    main(*sys.argv[1:])
