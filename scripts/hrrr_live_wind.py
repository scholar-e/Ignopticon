#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pyproj", "pygrib"]
# ///
"""HRRR 10 m wind frames for earth's live view of one UTC day, every 15 minutes, as each would have been seen then.

The frame valid at T comes from the newest HRRR run issued at least an hour before T's hour began, i.e. the run at
floor_hour(T) - 1 h (HRRR runs hourly and its f01-f02 are out within about an hour), at lead 60-105 min. Leads are
taken from the run's sub-hourly file (wrfsubhf01 for 60 min, wrfsubhf02 for 75/90/105 min, instantaneous 15-min
output); if that file is missing, from the hourly wrfsfcf01/02 of the same run at the nearest whole hour, which is
recorded. Every frame uses only its own run, so a bad run spoils at most 4 frames.

Only UGRD/VGRD at 10 m are downloaded, by byte range from NOAA's AWS bucket via the .idx (as scripts/hrrr_weather.py
does). HRRR's grid-relative components are rotated to true north (NCEP's Lambert conformal rotation, lon_0 normalised
from 262.5) and bilinearly resampled from the 3 km grid to a regular lat/lon grid (default 0.1 deg over the lower 48);
points outside the HRRR domain are left undefined.

Output, in earth's grib2json layout but compact: OUT/YYYY/MM/DD/HHMM-wind-surface-level-hrrr-0.1.json holds
[U record, V record]; each has the usual header (refTime = valid time, forecastTime 0, plus runTime, leadMinutes,
source) and, instead of a "data" list, "b64": base64 of int8 values in units of "scale" m/s (row-major, north to
south, -128 = undefined). OUT/YYYY/MM/DD/index.json lists the frames with their run, lead and file kind.

Usage: hrrr_live_wind.py [DATE=2026-08-02] [--out earth/public/data/weather/hrrr] [--res 0.1] [--raw data/raw/hrrr_live]
"""
import argparse
import base64
import datetime
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pygrib
from pyproj import CRS, Transformer
from scipy.ndimage import map_coordinates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
WIND = ["UGRD:10 m above ground", "VGRD:10 m above ground"]
BOUNDS = (-125.0, 24.0, -66.0, 50.0)  # west, south, east, north
SCALE = 0.25  # m/s per int8 step: +-31.75 m/s
MISSING = -128
LEADS = (60, 75, 90, 105)  # minutes after the run of the frames it serves


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ignopticon", **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def fetch_wind(run, kind, fhour, rawdir):
    """U/V 10 m messages of hrrr.tHHz.{wrfsubhf|wrfsfcf}FF.grib2 by byte range; returns (path, [minute per U/V pair])
    or None if the file is not in the archive. Sub-hourly files keep only instantaneous steps."""
    name = "hrrr.t%02dz.%s%02d.grib2" % (run.hour, kind, fhour)
    path = os.path.join(rawdir, "%s_%s" % (run.strftime("%Y%m%d"), name.replace(".grib2", ".uv.grb2")))
    url = "%s/hrrr.%s/conus/%s" % (BUCKET, run.strftime("%Y%m%d"), name)
    try:
        lines = get(url + ".idx").decode().splitlines()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise
    picks = []
    for i, line in enumerate(lines):
        f = line.split(":")
        step = f[5]
        if ":".join(f[3:5]) not in WIND:
            continue
        if kind == "wrfsubhf":
            if not (step.endswith(" min fcst") and "-" not in step):
                continue
            minute = int(step.split()[0])
            if minute not in LEADS:
                continue
        else:
            if step != "%d hour fcst" % fhour:
                continue
            minute = 60 * fhour
        end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
        picks.append((minute, f[3], int(f[1]), end))
    if not os.path.exists(path):
        parts = [get(url, {"Range": "bytes=%d-%s" % (start, end)}) for _, _, start, end in picks]
        with open(path + ".part", "wb") as fh:
            fh.write(b"".join(parts))
        os.replace(path + ".part", path)
        log.info("downloaded %s (%d messages)", os.path.basename(path), len(picks))
    return path, sorted({m for m, _, _, _ in picks})


class Resampler:
    """HRRR grid -> regular lat/lon grid: fractional (row, col) of each target point and the rotation angle there."""

    def __init__(self, msg, res):
        west, south, east, north = BOUNDS
        self.nx = int(round((east - west) / res)) + 1
        self.ny = int(round((north - south) / res)) + 1
        self.lons = west + res * np.arange(self.nx)
        self.lats = north - res * np.arange(self.ny)
        lon, lat = np.meshgrid(self.lons, self.lats)
        p = msg.projparams
        lcc = CRS.from_dict(p)
        to_lcc = Transformer.from_crs("EPSG:4326", lcc, always_xy=True)
        lx, ly = to_lcc.transform(lon, lat)
        lat0, lon0 = msg.latitudeOfFirstGridPointInDegrees, msg.longitudeOfFirstGridPointInDegrees
        x0, y0 = to_lcc.transform(lon0 - 360 * (lon0 > 180), lat0)
        self.coords = np.array([(ly - y0) / msg.DyInMetres, (lx - x0) / msg.DxInMetres])
        lon_0 = (p["lon_0"] + 180) % 360 - 180  # HRRR gives 262.5, i.e. -97.5
        self.angle = np.radians(np.sin(np.radians(p["lat_1"])) * (lon - lon_0))
        self.shape = (msg.Ny, msg.Nx)
        self.res = res

    def wind(self, ug, vg):
        sample = lambda g: map_coordinates(g, self.coords, order=1, mode="constant", cval=np.nan)
        ug, vg = sample(ug), sample(vg)
        u = np.cos(self.angle) * ug + np.sin(self.angle) * vg
        v = -np.sin(self.angle) * ug + np.cos(self.angle) * vg
        return u, v


def encode(a):
    q = np.round(a / SCALE)
    clipped = int(np.sum(np.abs(q[np.isfinite(q)]) > 127))
    q = np.where(np.isfinite(q), np.clip(q, -127, 127), MISSING).astype(np.int8)
    return base64.b64encode(q.tobytes()).decode(), clipped


def record(rs, number, values, valid, run, lead, kind):
    b64, clipped = encode(values)
    header = {
        "discipline": 0, "center": "hrrr", "refTime": valid.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "forecastTime": 0,
        "runTime": run.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "leadMinutes": lead, "file": kind,
        "centerName": "HRRR (NOAA), run %02dZ +%s%s" % (
            run.hour, "%dh" % (lead // 60) + ("%02dm" % (lead % 60) if lead % 60 else ""),
            "" if kind == "wrfsubhf" else " (hourly)"),
        "parameterCategory": 2, "parameterNumber": number, "parameterUnit": "m.s-1",
        "surface1Type": 103, "surface1Value": 10,
        "nx": rs.nx, "ny": rs.ny, "lo1": float(rs.lons[0]), "la1": float(rs.lats[0]),
        "lo2": float(rs.lons[-1]), "la2": float(rs.lats[-1]), "dx": rs.res, "dy": rs.res,
        "numberPoints": rs.nx * rs.ny,
    }
    return {"header": header, "scale": SCALE, "missing": MISSING, "b64": b64}, clipped


def plan(day):
    """(valid time, run, lead minutes) for every 15 minutes of the day."""
    out = []
    for k in range(96):
        valid = day + datetime.timedelta(minutes=15 * k)
        run = valid.replace(minute=0) - datetime.timedelta(hours=1)
        out.append((valid, run, int((valid - run).total_seconds() // 60)))
    return out


def sources(run, rawdir):
    """{lead minute: (path, kind)} for leads 60-120 of one run: sub-hourly where available, else hourly."""
    found = {}
    for fh in (1, 2):
        got = fetch_wind(run, "wrfsubhf", fh, rawdir)
        if got:
            for m in got[1]:
                found[m] = (got[0], "wrfsubhf")
    if not all(m in found for m in LEADS):
        log.warning("run %s: sub-hourly leads %s, falling back to hourly files", run, sorted(found))
        for fh in (1, 2):
            got = fetch_wind(run, "wrfsfcf", fh, rawdir)
            if got:
                for m in got[1]:
                    found.setdefault(m, (got[0], "wrfsfcf"))
    return found


def minutes(g):
    step = str(g.endStep)
    return int(step[:-1]) if step.endswith("m") else int(step) * 60


def read_uv(path, minute):
    with pygrib.open(path) as grbs:
        msgs = [g for g in grbs if minutes(g) == minute]
    u = next(g for g in msgs if g.shortName == "10u")
    v = next(g for g in msgs if g.shortName == "10v")
    return u, v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default="2026-08-02")
    ap.add_argument("--out", default="earth/public/data/weather/hrrr")
    ap.add_argument("--res", type=float, default=0.1)
    ap.add_argument("--raw", default="data/raw/hrrr_live")
    a = ap.parse_args()
    day = datetime.datetime.strptime(a.date, "%Y-%m-%d")
    os.makedirs(a.raw, exist_ok=True)
    outdir = os.path.join(a.out, day.strftime("%Y/%m/%d"))
    os.makedirs(outdir, exist_ok=True)

    frames = plan(day)
    runs = sorted({r for _, r, _ in frames})
    with ThreadPoolExecutor(8) as pool:
        found = dict(zip(runs, pool.map(lambda r: sources(r, a.raw), runs)))

    rs, index = None, []
    for valid, run, lead in frames:
        avail = found[run]
        if not avail:
            log.error("%s: run %s missing entirely, frame skipped", valid, run)
            continue
        use = lead if lead in avail else min(avail, key=lambda m: (abs(m - lead), -m))
        path, kind = avail[use]
        u, v = read_uv(path, use)
        if rs is None:
            rs = Resampler(u, a.res)
        ue, ve = rs.wind(u.values.astype("float64"), v.values.astype("float64"))
        ru, cu = record(rs, 2, ue, valid, run, use, kind)
        rv, cv = record(rs, 3, ve, valid, run, use, kind)
        name = "%s-wind-surface-level-hrrr-%g.json" % (valid.strftime("%H%M"), a.res)
        with open(os.path.join(outdir, name), "w") as fh:
            json.dump([ru, rv], fh, separators=(",", ":"))
        speed = np.hypot(ue, ve)
        log.info("%s <- run %02dZ +%d min (%s%s): mean %.1f m/s, max %.1f, %.0f%% defined, %d clipped", name,
                 run.hour, use, kind, "" if use == lead else ", wanted %d" % lead, np.nanmean(speed),
                 np.nanmax(speed), 100 * np.mean(np.isfinite(speed)), cu + cv)
        index.append({"time": valid.strftime("%H%M"), "valid": valid.strftime("%Y-%m-%dT%H:%MZ"), "file": name,
                      "run": run.strftime("%Y-%m-%dT%HZ"), "lead_min": use, "wanted_lead_min": lead, "source": kind})
    with open(os.path.join(outdir, "index.json"), "w") as fh:
        json.dump({"date": a.date, "model": "HRRR CONUS (NOAA, AWS archive)", "res_deg": a.res,
                   "bounds": BOUNDS, "scale_ms": SCALE, "frames": index}, fh, indent=1)
    log.info("wrote %d frames to %s", len(index), outdir)


if __name__ == "__main__":
    main()
