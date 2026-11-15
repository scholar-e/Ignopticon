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

Usage: hrrr_weather.py TEMPLATE.tif RUN HOURS OUTDIR [RAWDIR] [--subhourly [--fm100=FM100.tif]]
       e.g. hrrr_weather.py data/fires/widemouth-2026-08-02-state.tif 2026-08-02T12 24 data/elmfire/widemouth/wx
Writes OUTDIR/{ws,wd,m1,m10,m100}.tif, one band per hour from RUN, and OUTDIR/wx.json (run, hours, domain means).
RAWDIR (default data/raw/hrrr) keeps the downloaded GRIB messages.

--subhourly: the run's sub-hourly files hrrr.tRUNz.wrfsubhfFF.grib2 instead (instantaneous 10 m wind, 2 m T and
dew point every 15 min: f00 analysis, then 15/30/45/60 min of each hour), HOURS*4+1 bands 900 s apart (wx.json
dt_s=900). Same rotation, 20-ft factor and bilinear 3 km interpolation. RH from T and dew point (Magnus, Alduchov &
Eskridge 1996). Dead fuel moisture per step:
    m1    EMC (Simard) from that step's T and RH
    m10   lagged EMC: dm/dt = (EMC - m)/10 h (exact exponential update per step), starting at ISSUE from the
          previous day's gridMET fm100 (--fm100), else from m1 + 1
    m100  --fm100 FM100.tif: previous day's gridMET 100-h moisture (EPSG:4326 GeoTIFF), bilinear, constant over
          the run (never the same day's value: not known at issue); without it m1 + 2 as before.
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


SUBH_FIELDS = ["TMP:2 m above ground", "DPT:2 m above ground", "UGRD:10 m above ground", "VGRD:10 m above ground"]


def download_subhourly(run, hour, rawdir):
    """Instantaneous SUBH_FIELDS messages of one wrfsubhf file in idx order; returns (path, [(field, minute)])."""
    path = os.path.join(rawdir, "hrrr_%s_t%02dz_subhf%02d.grb2" % (run.strftime("%Y%m%d"), run.hour, hour))
    url = "%s/hrrr.%s/conus/hrrr.t%02dz.wrfsubhf%02d.grib2" % (BUCKET, run.strftime("%Y%m%d"), run.hour, hour)
    lines = get(url + ".idx").decode().splitlines()
    parts, meta = [], []
    for i, line in enumerate(lines):
        f = line.split(":")
        step = f[5]
        if ":".join(f[3:5]) not in SUBH_FIELDS or not (step == "anl" or (step.endswith(" min fcst") and "-" not in step)):
            continue
        meta.append((":".join(f[3:5]), 0 if step == "anl" else int(step.split()[0])))
        if not os.path.exists(path):
            start = int(f[1])
            end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
            parts.append(get(url, {"Range": "bytes=%d-%s" % (start, end)}))
    if len(meta) != (4 if hour == 0 else 16):
        raise RuntimeError("%s: found %d instantaneous messages" % (url, len(meta)))
    if not os.path.exists(path):
        part = "%s.%d.part" % (path, os.getpid())  # concurrent tasks share RAWDIR
        with open(part, "wb") as f:
            f.write(b"".join(parts))
        os.replace(part, path)
        log.info("downloaded %s", os.path.basename(path))
    return path, meta


def rh_from_dewpoint(temp_c, dew_c):
    magnus = lambda t: np.exp(17.625 * t / (t + 243.04))
    return np.clip(100 * magnus(dew_c) / magnus(temp_c), 0, 100)


def emc(temp_c, rh):
    """Equilibrium moisture content (%) of wood, Simard (1968), T in deg C, RH in %."""
    t = temp_c * 9 / 5 + 32
    return np.where(rh < 10, 0.03229 + 0.281073 * rh - 0.000578 * rh * t,
                    np.where(rh < 50, 2.22749 + 0.160107 * rh - 0.01478 * t,
                             21.0606 + 0.005565 * rh ** 2 - 0.00035 * rh * t - 0.483199 * rh))


def hrrr_geometry(u10, crs, gx, gy):
    """The template's cells in HRRR's grid coordinates (row, col), and the wind rotation angle there."""
    p = u10.projparams
    lcc = CRS.from_dict(p)
    lx, ly = Transformer.from_crs(crs, lcc, always_xy=True).transform(gx, gy)
    lat0, lon0 = u10.latitudeOfFirstGridPointInDegrees, u10.longitudeOfFirstGridPointInDegrees
    x0, y0 = Transformer.from_crs("EPSG:4326", lcc, always_xy=True).transform(lon0 - 360 * (lon0 > 180), lat0)
    dx, dy = u10.DxInMetres, u10.DyInMetres
    coords = np.array([(ly - y0) / dy, (lx - x0) / dx])  # (row, col) in the GRIB array, south to north
    lon = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(gx, gy)[0]
    lon_0 = (p["lon_0"] + 180) % 360 - 180  # HRRR gives 262.5, i.e. -97.5
    angle = np.radians(np.sin(np.radians(p["lat_1"])) * (lon - lon_0))
    log.info("template cells at HRRR rows %.0f-%.0f, cols %.0f-%.0f", coords[0].min(), coords[0].max(),
             coords[1].min(), coords[1].max())
    return coords, angle


def sample_fm100(path, crs, gx, gy):
    """Previous-day gridMET fm100 (%) at the template cells, bilinear on its 4 km lon/lat grid."""
    with rasterio.open(path) as src:
        a, t, nd = src.read(1).astype("float64"), src.transform, src.nodata
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(gx, gy)
    col, row = ~t * (lon, lat)
    a = np.where(a == nd, np.nan, a)
    fill = np.nanmean(a[max(int(row.min()) - 5, 0):int(row.max()) + 6, max(int(col.min()) - 5, 0):int(col.max()) + 6])
    out = map_coordinates(np.where(np.isnan(a), fill, a), np.array([row - 0.5, col - 0.5]), order=1, mode="nearest")
    return out


def main_subhourly(run, hours, outdir, rawdir, fm100, profile, crs, gx, gy, tau_h=10.0, dt=900):
    out = {k: [] for k in ("ws", "wd", "m1", "m10", "m100")}
    m100 = sample_fm100(fm100, crs, gx, gy) if fm100 else None
    coords = angle = None
    m10 = None
    decay = np.exp(-dt / (tau_h * 3600))
    for hour in range(hours + 1):
        path, meta = download_subhourly(run, hour, rawdir)
        grbs = pygrib.open(path)
        steps = {}
        for g, (field, minute) in zip(grbs, meta):
            steps.setdefault(minute, {})[field.split(":")[0]] = g
        for minute in sorted(steps):
            f = steps[minute]
            if coords is None:
                coords, angle = hrrr_geometry(f["UGRD"], crs, gx, gy)
            sample = lambda g: map_coordinates(g.values.astype("float64"), coords, order=1, mode="nearest")
            ug, vg = sample(f["UGRD"]), sample(f["VGRD"])
            temp, dew = sample(f["TMP"]) - 273.15, sample(f["DPT"]) - 273.15
            rh = rh_from_dewpoint(temp, dew)
            u = np.cos(angle) * ug + np.sin(angle) * vg
            v = -np.sin(angle) * ug + np.cos(angle) * vg
            m1 = np.clip(emc(temp, rh), 2, 35)
            if m10 is None:
                m10 = np.clip(m100, 2, 35) if m100 is not None else m1 + 1
            else:
                m10 = m1 + (m10 - m1) * decay
            out["ws"].append(np.hypot(u, v) * MS_TO_MPH * WIND_20FT)
            out["wd"].append((np.degrees(np.arctan2(-u, -v)) + 360) % 360)
            out["m1"].append(m1)
            out["m10"].append(m10.copy())
            out["m100"].append(np.clip(m100, 2, 40) if m100 is not None else m1 + 2)
            log.info("+%3d min: wind %.1f mph from %.0f deg, T %.1f C, RH %.0f%%, m1 %.1f m10 %.1f m100 %.1f%%",
                     minute, out["ws"][-1].mean(), np.degrees(np.arctan2(-u.mean(), -v.mean())) % 360, temp.mean(),
                     rh.mean(), m1.mean(), m10.mean(), out["m100"][-1].mean())
        grbs.close()
    n = len(out["ws"])
    if n != hours * 4 + 1:
        raise RuntimeError("expected %d sub-hourly steps, got %d" % (hours * 4 + 1, n))
    profile.update(count=n, dtype="float32", nodata=-9999, compress="deflate")
    for key in ("blockxsize", "blockysize", "tiled"):
        profile.pop(key, None)
    for name, bands in out.items():
        with rasterio.open(os.path.join(outdir, name + ".tif"), "w", **profile) as dst:
            dst.write(np.asarray(bands, dtype="float32"))
    with open(os.path.join(outdir, "wx.json"), "w") as f:
        json.dump({"source": "HRRR wrfsubhf sub-hourly forecast (instantaneous 15 min)", "run": run.isoformat() + "Z",
                   "hours": hours, "dt_s": dt, "steps": n,
                   "moisture": {"m1": "EMC (Simard 1968) from 2 m T and RH (from dew point) per step",
                                "m10": "lagged EMC, tau %g h, initial %s" % (tau_h, "previous-day gridMET fm100" if fm100
                                                                              else "m1 + 1"),
                                "m100": ("previous-day gridMET fm100, constant: " + str(fm100)) if fm100 else "m1 + 2"},
                   "mean_ws_mph": [round(float(b.mean()), 2) for b in out["ws"]],
                   "mean_m1_pct": [round(float(b.mean()), 2) for b in out["m1"]],
                   "mean_m10_pct": [round(float(b.mean()), 2) for b in out["m10"]],
                   "mean_m100_pct": round(float(out["m100"][0].mean()), 2)}, f, indent=1)
    log.info("wrote %s (%d sub-hourly steps)", outdir, n)


def main(template, run, hours, outdir, rawdir="data/raw/hrrr", subhourly=False, fm100=None):
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
    if subhourly:
        return main_subhourly(run, hours, outdir, rawdir, fm100, profile, crs, gx, gy)
    for hour in range(hours + 1):
        grbs = pygrib.open(download(run, hour, rawdir))
        u10, v10 = grbs.select(name="10 metre U wind component")[0], grbs.select(name="10 metre V wind component")[0]
        if lcc is None:
            lcc = True
            coords, angle = hrrr_geometry(u10, crs, gx, gy)
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = dict((a[2:].split("=", 1) + [True])[:2] for a in sys.argv[1:] if a.startswith("--"))
    if len(args) not in (4, 5) or set(flags) - {"subhourly", "fm100"}:
        sys.exit(__doc__)
    main(*args, subhourly=bool(flags.get("subhourly")), fm100=flags.get("fm100"))
