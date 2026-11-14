#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pyproj", "rasterio"]
# ///
"""Wind-driven ellipse spread (PLAN.md M2 baseline), started from an observed fire state, written as ELMFIRE
writes its outputs, so heat_frames.py treats it as it will treat ELMFIRE runs. A stand-in until ELMFIRE is built.

Each cell's arrival is the earliest over the fire's active cells (seen burning in the ACTIVE_HOURS before ISSUE) of
the time an ellipse from that cell takes to reach it: head rate HEAD_ROS downwind, the ellipse's rear focus at the
cell, its length-to-breadth ratio from the 10 m wind (Canadian FBP System, Forestry Canada 1992). Cells already
burned at ISSUE don't burn again. No fuel, terrain, or wind change over time: everything is in HEAD_ROS.

Usage: ellipse_spread.py STATE.tif ISSUE OUTDIR WIND.json [HEAD_ROS_M_S] [HOURS] [HPA_KJ_M2]
       e.g. ellipse_spread.py data/fires/widemouth-2026-08-02-state.tif 2026-08-02T12:00 data/fires/runs/ros0.2 \
                earth/public/data/weather/2026/08/02/1800-wind-surface-level-gfs-1.0.json 0.2 24
Writes OUTDIR/time_of_arrival_0000001_TTTTTTT.tif (s since ISSUE, -9999 unburned) and flin_...tif (kW/m), on a
grid 5x finer than the state's, and OUTDIR/run.json with the settings. HEAD_ROS 0 is the no-growth baseline.
"""
import datetime
import json
import logging
import math
import os
import sys

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

REFINE = 5            # model cells per state cell, each way (375 m -> 75 m)
ACTIVE_HOURS = 4.0    # cells first seen burning this long before ISSUE are the fire front
NODATA = -9999.0


def wind_at(path, lon, lat):
    """(u, v) m/s of earth's GFS wind JSON at the nearest grid point."""
    records = json.load(open(path))
    h = records[0]["header"]
    i = int(round(((lon - h["lo1"]) % 360) / h["dx"])) % h["nx"]
    j = int(round((h["la1"] - lat) / h["dy"]))
    k = j * h["nx"] + i
    u = next(r for r in records if r["header"]["parameterNumber"] == 2)["data"][k]
    v = next(r for r in records if r["header"]["parameterNumber"] == 3)["data"][k]
    return u, v


def length_to_breadth(wind_kmh):
    """Canadian FBP System (Forestry Canada 1992) for 10 m open wind in km/h."""
    return 1.0 + 8.729 * (1 - math.exp(-0.030 * wind_kmh)) ** 2.155


def main(state_tif, issue, outdir, wind_json, head_ros="0.2", hours="24", hpa="8000"):
    head_ros, hours, hpa = float(head_ros), float(hours), float(hpa)
    with rasterio.open(state_tif) as src:
        state = src.read(1)
        crs, transform, nodata = src.crs, src.transform, src.nodata
        reference = datetime.datetime.strptime(src.tags()["reference"], "%Y-%m-%dT%H:%MZ")
    issue_t = datetime.datetime.fromisoformat(issue)
    issue_s = (issue_t - reference).total_seconds()  # in the state's clock
    burned = (state != nodata) & (state <= issue_s)
    active = burned & (state >= issue_s - ACTIVE_HOURS * 3600)
    cell = transform.a
    rows, cols = np.nonzero(active)
    sx = transform.c + (cols + 0.5) * cell
    sy = transform.f - (rows + 0.5) * cell

    to_lonlat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    lon, lat = to_lonlat(float(sx.mean()), float(sy.mean()))
    u, v = wind_at(wind_json, lon, lat)
    speed_kmh = math.hypot(u, v) * 3.6
    lb = length_to_breadth(speed_kmh)
    e = math.sqrt(1 - 1 / lb ** 2)
    toward = math.atan2(v, u)  # direction the wind blows toward, radians from east
    log.info("%d burned cells, %d active at %s; wind %.1f km/h toward %.0f° (compass), L/B %.2f, e %.2f",
             int(burned.sum()), len(rows), issue, speed_kmh, (90 - math.degrees(toward)) % 360, lb, e)

    fine = cell / REFINE
    n = state.shape[0] * REFINE
    fx = transform.c + (np.arange(n) + 0.5) * fine
    fy = transform.f - (np.arange(n) + 0.5) * fine
    xx, yy = np.meshgrid(fx, fy)
    toa = np.full((n, n), np.inf)
    rate = np.zeros((n, n))
    cos_w, sin_w = math.cos(toward), math.sin(toward)
    for x, y in zip(sx, sy) if head_ros > 0 else ():  # ~hundreds of sources over ~0.5 M cells; 0 = no growth
        dx, dy = xx - x, yy - y
        distance = np.hypot(dx, dy)
        cos_theta = np.where(distance > 0, (dx * cos_w + dy * sin_w) / np.maximum(distance, 1e-9), 1.0)
        r = head_ros * (1 - e) / (1 - e * cos_theta)  # polar ellipse from its rear focus, m/s
        t = distance / r
        better = t < toa
        toa[better] = t[better]
        rate[better] = r[better]
    already = np.repeat(np.repeat(burned, REFINE, axis=0), REFINE, axis=1)
    ok = np.isfinite(toa) & (toa <= hours * 3600) & ~already
    toa_out = np.where(ok, toa, NODATA).astype(np.float32)
    flin_out = np.where(ok, hpa * rate, NODATA).astype(np.float32)  # kW/m = kJ/m2 * m/s

    os.makedirs(outdir, exist_ok=True)
    profile = dict(driver="GTiff", width=n, height=n, count=1, dtype="float32", crs=crs, nodata=NODATA,
                   transform=Affine(fine, 0, transform.c, 0, -fine, transform.f), compress="deflate")
    stamp = "0000001_%07d" % int(hours * 3600)
    for name, data in (("time_of_arrival", toa_out), ("flin", flin_out)):
        with rasterio.open(os.path.join(outdir, "%s_%s.tif" % (name, stamp)), "w", **profile) as out:
            out.write(data, 1)
    json.dump({"model": "ellipse (stand-in for ELMFIRE)" if head_ros > 0 else "no growth", "issue": issue, "head_ros_m_s": head_ros,
               "hours": hours, "hpa_kj_m2": hpa, "wind_kmh": round(speed_kmh, 1),
               "wind_toward_deg": round((90 - math.degrees(toward)) % 360), "length_to_breadth": round(lb, 2)},
              open(os.path.join(outdir, "run.json"), "w"), indent=1)
    log.info("wrote %s: %.0f km2 newly burned in %g h", outdir, ok.sum() * fine ** 2 / 1e6, hours)


if __name__ == "__main__":
    main(*sys.argv[1:8])
