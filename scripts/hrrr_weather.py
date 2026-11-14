#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pyproj", "rasterio", "pygrib"]
# ///
"""ELMFIRE weather from one HRRR forecast run (PLAN.md M0): hourly 20-ft wind and dead fuel moisture on a template
raster's grid, using only what was available at the run's issue time (forecast hours, not later analyses).

Downloads, by byte range from NOAA's HRRR archive on AWS (no key), the 10 m wind and 2 m temperature and humidity
of hrrr.tRUNz.wrfsfcfFF.grib2 for FF = 0..HOURS, then for each hour:
    ws    20-ft wind speed (mph) = 10 m speed x 0.87 (log profile, ~0.03 m roughness), as ELMFIRE expects
    wd    direction the wind blows from (deg), HRRR's grid-relative components rotated to true north
    m1    1-h dead fuel moisture (%) = equilibrium moisture content (Simard 1968) from 2 m T and RH
    m10   m1 + 1, m100 m1 + 2: rough lags; the fuel moisture parameters are to be fitted (PLAN.md M4)
interpolated bilinearly from the 3 km grid.

Usage: hrrr_weather.py TEMPLATE.tif RUN HOURS OUTDIR [RAWDIR]
       e.g. hrrr_weather.py data/fires/widemouth-2026-08-02-state.tif 2026-08-02T12 24 data/elmfire/widemouth/wx
Writes OUTDIR/{ws,wd,m1,m10,m100}.tif, one band per hour from RUN, and OUTDIR/wx.json (run, hours, domain means).
RAWDIR (default data/raw/hrrr) keeps the downloaded GRIB messages.
"""
import datetime
import json
import logging
import os
import sys
import urllib.request

import numpy as np
import pygrib
import rasterio
from pyproj import CRS, Transformer
from scipy.ndimage import map_coordinates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
FIELDS = ["UGRD:10 m above ground", "VGRD:10 m above ground", "TMP:2 m above ground", "RH:2 m above ground"]
WIND_20FT = 0.87
MS_TO_MPH = 2.23694


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ignopticon", **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def download(run, hour, rawdir):
    """The FIELDS messages of one HRRR surface file, by byte range from its .idx."""
    path = os.path.join(rawdir, "hrrr_%s_t%02dz_f%02d.grb2" % (run.strftime("%Y%m%d"), run.hour, hour))
    if os.path.exists(path):
        return path
    url = "%s/hrrr.%s/conus/hrrr.t%02dz.wrfsfcf%02d.grib2" % (BUCKET, run.strftime("%Y%m%d"), run.hour, hour)
    lines = get(url + ".idx").decode().splitlines()
    parts = []
    for i, line in enumerate(lines):
        f = line.split(":")
        if ":".join(f[3:5]) in FIELDS:
            start = int(f[1])
            end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
            parts.append(get(url, {"Range": "bytes=%d-%s" % (start, end)}))
    if len(parts) != len(FIELDS):
        raise RuntimeError("%s: found %d of %d fields" % (url, len(parts), len(FIELDS)))
    with open(path + ".part", "wb") as f:
        f.write(b"".join(parts))
    os.replace(path + ".part", path)
    log.info("downloaded %s", os.path.basename(path))
    return path


def emc(temp_c, rh):
    """Equilibrium moisture content (%) of wood, Simard (1968), T in deg C, RH in %."""
    t = temp_c * 9 / 5 + 32
    return np.where(rh < 10, 0.03229 + 0.281073 * rh - 0.000578 * rh * t,
                    np.where(rh < 50, 2.22749 + 0.160107 * rh - 0.01478 * t,
                             21.0606 + 0.005565 * rh ** 2 - 0.00035 * rh * t - 0.483199 * rh))


def main(template, run, hours, outdir, rawdir="data/raw/hrrr"):
    run = datetime.datetime.strptime(run, "%Y-%m-%dT%H")
    hours = int(hours)
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(rawdir, exist_ok=True)
    with rasterio.open(template) as src:
        profile, crs, t, (ny, nx) = src.profile, src.crs, src.transform, src.shape
    xs = t.c + (np.arange(nx) + 0.5) * t.a
    ys = t.f + (np.arange(ny) + 0.5) * t.e
    gx, gy = np.meshgrid(xs, ys)

    lcc = None
    out = {k: [] for k in ("ws", "wd", "m1", "m10", "m100")}
    for hour in range(hours + 1):
        grbs = pygrib.open(download(run, hour, rawdir))
        u10, v10 = grbs.select(name="10 metre U wind component")[0], grbs.select(name="10 metre V wind component")[0]
        if lcc is None:  # the template's cells in HRRR's grid coordinates, and the wind rotation there
            p = u10.projparams
            lcc = CRS.from_dict(p)
            to_lcc = Transformer.from_crs(crs, lcc, always_xy=True)
            lx, ly = to_lcc.transform(gx, gy)
            lat0, lon0 = u10.latitudeOfFirstGridPointInDegrees, u10.longitudeOfFirstGridPointInDegrees
            x0, y0 = Transformer.from_crs("EPSG:4326", lcc, always_xy=True).transform(lon0 - 360 * (lon0 > 180), lat0)
            dx, dy = u10.DxInMetres, u10.DyInMetres
            coords = np.array([(ly - y0) / dy, (lx - x0) / dx])  # (row, col) in the GRIB array, south to north
            lon = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(gx, gy)[0]
            lon_0 = (p["lon_0"] + 180) % 360 - 180  # HRRR gives 262.5, i.e. -97.5
            angle = np.radians(np.sin(np.radians(p["lat_1"])) * (lon - lon_0))
            log.info("template cells at HRRR rows %.0f-%.0f, cols %.0f-%.0f", coords[0].min(), coords[0].max(),
                     coords[1].min(), coords[1].max())
        sample = lambda g: map_coordinates(g.values.astype("float64"), coords, order=1, mode="nearest")
        ug, vg = sample(u10), sample(v10)
        temp = sample(grbs.select(name="2 metre temperature")[0]) - 273.15
        rh = np.clip(sample(grbs.select(name="2 metre relative humidity")[0]), 0, 100)
        # Grid-relative to true-north components (NCEP's rotation for Lambert conformal grids).
        u = np.cos(angle) * ug + np.sin(angle) * vg
        v = -np.sin(angle) * ug + np.cos(angle) * vg
        m1 = np.clip(emc(temp, rh), 2, 35)
        out["ws"].append(np.hypot(u, v) * MS_TO_MPH * WIND_20FT)
        out["wd"].append((np.degrees(np.arctan2(-u, -v)) + 360) % 360)
        out["m1"].append(m1)
        out["m10"].append(m1 + 1)
        out["m100"].append(m1 + 2)
        log.info("f%02d %s: wind %.1f mph from %.0f deg, T %.1f C, RH %.0f%%, m1 %.1f%%", hour,
                 (run + datetime.timedelta(hours=hour)).strftime("%m-%d %HZ"), out["ws"][-1].mean(),
                 np.degrees(np.arctan2(-u.mean(), -v.mean())) % 360, temp.mean(), rh.mean(), m1.mean())
        grbs.close()

    profile.update(count=hours + 1, dtype="float32", nodata=-9999, compress="deflate")
    for key in ("blockxsize", "blockysize", "tiled"):
        profile.pop(key, None)
    for name, bands in out.items():
        with rasterio.open(os.path.join(outdir, name + ".tif"), "w", **profile) as dst:
            dst.write(np.asarray(bands, dtype="float32"))
    with open(os.path.join(outdir, "wx.json"), "w") as f:
        json.dump({"source": "HRRR wrfsfc forecast", "run": run.isoformat() + "Z", "hours": hours,
                   "dt_s": 3600, "mean_ws_mph": [round(float(b.mean()), 2) for b in out["ws"]],
                   "mean_m1_pct": [round(float(b.mean()), 2) for b in out["m1"]]}, f, indent=1)
    log.info("wrote %s", outdir)


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        sys.exit(__doc__)
    main(*sys.argv[1:])
