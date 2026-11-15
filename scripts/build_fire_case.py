#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "pyproj", "pygrib"]
# ///
"""Everything ELMFIRE needs for one catalog fire (scripts/fire_catalog.py), for training runs (data/training/NOTES.md).

Steps, each skipped when its output exists (so the script is idempotent and resumable):
  1. Grid: a square box around the event's bbox (fire_catalog.py, 0.1 degree cell edges) plus MARGIN_KM, half-side
     clamped to MIN_RADIUS_KM..MAX_RADIUS_KM (events larger than that are cut to the box around their centre;
     recorded as "truncated"). ELMFIRE cells: 37.5 m (REFINE 10 of the 375 m state grid) up to a 60 km box, 75 m
     (REFINE 5) beyond, so cases stay <= ~2000 x 2000 cells.
  2. VIIRS 375 m active fire granules over the box from the day before the event to the day after: VNP14IMG
     (S-NPP), VJ114IMG (NOAA-20, 2018 on), VJ214IMG (NOAA-21, 2024 on), from LAADS (laads_download.py's functions,
     IPv4 forced as in gridded_fire_download.py), shared across fires in data/training/raw/viirs_fire; LAADS
     listings are cached per fire. Converted to FIRMS-style CSV (viirs_fire_to_firms_csv.py).
  3. Observations and fire state (fire_observations.py) over the window, detections only (no NOAA-21 L1B fire
     band); FEDS perimeters where FEDS has them (2018-2021 archive, 2025 on). With --geo, the matching 03IMG
     geolocation granules (~180 MB each) are fetched and cloud masks added (obscured_cells.py); off by default.
  4. LANDFIRE LF2025 landscape + 3DEP (landscape_download.py) on the state grid refined REFINE times. Older fires
     get today's fuels (an anachronism; see NOTES.md).
  5. Issue times: 12Z on each event day with >= MIN_HOT cells hot in the 6 h before (burning edges to start from)
     and >= MIN_AFTER observed hot cells on overpasses within the next 24 h (something to score). For each, HRRR
     weather from that 12Z run (hrrr_weather.py), 24 h. Before HRRRv3 (July 2018) 12Z runs end at f15/f18: the
     last forecast hour is held to +24 h (no later information used). Raw GRIB is deleted after each issue unless
     --keep-raw.
Per issue, an observation file cut to frames and perimeters up to issue + 24 h (obs/ISSUE-observed.json) keeps
scoring small; it holds every frame before issue, as elmfire_case.py's leak-free start needs.

Environment: PY (default "uv run --script") runs the repo scripts, e.g. PY=python3 in the container.
Usage: build_fire_case.py FIRE_ID [--catalog CSV] [--geo] [--keep-raw] [--max-issues N] [--steps 1,2,...]
       e.g. build_fire_case.py 2020-035
Writes data/training/fires/FIRE_ID/: case.json (grid, refine, issues with their overpass counts, timings, download
volumes), detections.csv, fire-observed.json, fire-state.tif, landscape.tif, wx/YYYYMMDDTHH/, obs/.
"""
import argparse
import concurrent.futures
import csv
import datetime
import glob
import json
import logging
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import numpy as np
import rasterio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# LAADS publishes IPv6 addresses that some networks can't route (gridded_fire_download.py): IPv4 only.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *a, **kw: _getaddrinfo(host, port, socket.AF_INET, *a, **kw)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import laads_download  # noqa: E402

TRAINING = "data/training"
RAW_FIRE = "data/training/raw/viirs_fire"
RAW_GEO = "data/training/raw/viirs_geo"
RAW_HRRR = "data/training/raw/hrrr"
MARGIN_KM = 8.0
MIN_RADIUS_KM = 20.0
MAX_RADIUS_KM = 75.0
FINE_RADIUS_KM = 30.0   # up to this half-side, 37.5 m ELMFIRE cells; beyond, 75 m
PRODUCTS = [("VNP14IMG", "2012-01-19"), ("VJ114IMG", "2018-01-05"), ("VJ214IMG", "2024-01-17")]
GEO = {"VNP14IMG": "VNP03IMG", "VJ114IMG": "VJ103IMG", "VJ214IMG": "VJ203IMG"}
HRRR = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
ACTIVE_HOURS = 6
MIN_HOT = 3
MIN_AFTER = 10
# How repo scripts are run (their PEP 723 headers name their packages); PY=python3 inside the container, which has
# them installed (the same variable as elmfire_forecast.sh).
PYRUN = (os.environ.get("PY") or "uv run --script").split()


def run(cmd, logfile):
    """Run a repo script (relative paths from the repo root), output to logfile; raise on failure."""
    t = time.time()
    with open(logfile, "a") as f:
        f.write("$ %s\n" % " ".join(cmd))
        f.flush()
        p = subprocess.run(PYRUN + cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT)
    if p.returncode:
        raise RuntimeError("%s failed (see %s)" % (cmd[0], logfile))
    return time.time() - t


def dates(first, last):
    d = datetime.date.fromisoformat(first)
    while d <= datetime.date.fromisoformat(last):
        yield d
        d += datetime.timedelta(days=1)


def du(paths):
    return sum(os.path.getsize(p) for p in paths if os.path.exists(p))


def granules(fire_dir, bbox, first, last, geo=False):
    """Download the window's fire granules (and with geo their 03IMG); returns the fire granule paths."""
    token = open(laads_download.TOKEN_FILE).read().strip()
    os.makedirs(os.path.join(fire_dir, "listings"), exist_ok=True)
    jobs = []
    for d in dates(first, last):
        for product, since in PRODUCTS:
            if d.isoformat() < since:
                continue
            for prod, outdir in [(product, RAW_FIRE)] + ([(GEO[product], RAW_GEO)] if geo else []):
                cache = os.path.join(fire_dir, "listings", "%s_%s.json" % (prod, d))
                if os.path.exists(cache):
                    listing = json.load(open(cache))
                else:
                    for attempt in range(5):
                        try:
                            listing = laads_download.granules(prod, d.isoformat(), bbox, night=True)
                            break
                        except Exception as err:
                            log.warning("listing %s %s: %s, retrying", prod, d, err)
                            time.sleep(5 * (attempt + 1))
                    else:
                        raise RuntimeError("LAADS listing failed for %s %s" % (prod, d))
                    json.dump([{"name": g["name"], "size": g["size"]} for g in listing], open(cache, "w"))
                for g in listing:
                    jobs.append((g["name"], int(g["size"]), prod, d, outdir))
    os.makedirs(RAW_FIRE, exist_ok=True)
    if geo:
        os.makedirs(RAW_GEO, exist_ok=True)

    def fetch(job):
        name, size, prod, d, outdir = job
        dst = os.path.join(outdir, name)
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            return 0
        for attempt in range(4):
            try:
                laads_download.download(name, prod, d.year, "%03d" % d.timetuple().tm_yday, dst, token)
                return size
            except Exception as err:
                log.warning("%s: %s (attempt %d)", name, err, attempt + 1)
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("could not download " + name)

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        got = sum(pool.map(fetch, jobs))
    fire = [os.path.join(RAW_FIRE, j[0]) for j in jobs if j[4] == RAW_FIRE]
    log.info("%d granules listed (%d fire), %.0f MB downloaded now", len(jobs), len(fire), got / 1e6)
    return fire, got, du(os.path.join(j[4], j[0]) for j in jobs)


def grid_for(row):
    """(lon, lat, radius_km, refine, truncated) of the case box around the catalog row's bbox."""
    w, s, e, n = (float(row[k]) for k in ("west", "south", "east", "north"))
    lon, lat = (w + e) / 2, (s + n) / 2
    side = max((e - w) * 111.32 * math.cos(math.radians(lat)), (n - s) * 111.32)
    radius = side / 2 + MARGIN_KM
    truncated = radius > MAX_RADIUS_KM
    radius = min(max(radius, MIN_RADIUS_KM), MAX_RADIUS_KM)
    radius = math.ceil(radius / 0.375 / 2) * 0.375 * 2 / 1  # whole state cells
    refine = 10 if radius <= FINE_RADIUS_KM else 5
    return round(lon, 4), round(lat, 4), radius, refine, truncated


def parse_t(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=datetime.timezone.utc)


def choose_issues(obs, first, last):
    """12Z issues with burning edges before and observed heat within 24 h after."""
    frames = [(parse_t(f["time"]), len(f["cells"])) for f in obs["frames"]]
    issues = []
    for d in dates(first, last):
        issue = datetime.datetime(d.year, d.month, d.day, 12, tzinfo=datetime.timezone.utc)
        before = sum(n for t, n in frames if issue - datetime.timedelta(hours=ACTIVE_HOURS) <= t <= issue)
        after = [(t, n) for t, n in frames if issue < t <= issue + datetime.timedelta(hours=24)]
        if before >= MIN_HOT and sum(n for _, n in after) >= MIN_AFTER:
            issues.append({"issue": issue.strftime("%Y-%m-%dT%H:%M"), "hot_cells_before": before,
                           "passes_after": len(after), "hot_cells_after": sum(n for _, n in after)})
    return issues


def cut_observations(obs, issue, path):
    """obs with frames and perimeters up to issue + 24 h only."""
    end = parse_t(issue) + datetime.timedelta(hours=24)
    out = dict(obs, frames=[f for f in obs["frames"] if parse_t(f["time"]) <= end],
               perimeters=[p for p in obs["perimeters"] if parse_t(p["time"]) <= end])
    json.dump(out, open(path, "w"), separators=(",", ":"))


def hrrr_hours(run):
    """The longest forecast (24, 18 or 15 h) the 12Z HRRR run has in the AWS archive."""
    for hours in (24, 18, 15):
        url = "%s/hrrr.%s/conus/hrrr.t%02dz.wrfsfcf%02d.grib2.idx" % (HRRR, run.strftime("%Y%m%d"), run.hour, hours)
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60)
            return hours
        except Exception:
            continue
    return None


def prefetch_hrrr(run, hours, rawdir):
    """Download the run's forecast hours 8 at a time (hrrr_weather.py then finds them and fetches none)."""
    import hrrr_weather
    os.makedirs(rawdir, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda h: hrrr_weather.download(run, h, rawdir), range(hours + 1)))


def pad_weather(wxdir, hours, want=24):
    """Hold the last forecast hour to +want h (bands hours+1 .. want+1 repeat the last)."""
    for name in ("ws", "wd", "m1", "m10", "m100"):
        path = os.path.join(wxdir, name + ".tif")
        with rasterio.open(path) as src:
            data, profile = src.read(), src.profile
        data = np.concatenate([data, np.repeat(data[-1:], want - hours, axis=0)])
        profile.update(count=data.shape[0])
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data)
    info = json.load(open(os.path.join(wxdir, "wx.json")))
    info.update(hours=want, padded_from_hours=hours)
    json.dump(info, open(os.path.join(wxdir, "wx.json"), "w"), indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fire")
    ap.add_argument("--catalog", default=os.path.join(TRAINING, "catalog.csv"))
    ap.add_argument("--geo", action="store_true")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--max-issues", type=int, default=0, help="keep the N issues with most heat after (0: all)")
    ap.add_argument("--steps", default="1,2,3,4,5")
    a = ap.parse_args()
    os.chdir(REPO)
    steps = set(a.steps.split(","))
    row = next(r for r in csv.DictReader(open(a.catalog)) if r["id"] == a.fire)
    fire_dir = os.path.join(TRAINING, "fires", a.fire)
    os.makedirs(fire_dir, exist_ok=True)
    logfile = os.path.join(fire_dir, "build.log")
    meta_path = os.path.join(fire_dir, "case.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    meta.setdefault("timing_s", {})
    save = lambda: json.dump(meta, open(meta_path, "w"), indent=1)

    lon, lat, radius, refine, truncated = grid_for(row)
    first = (datetime.date.fromisoformat(row["start"]) - datetime.timedelta(days=1)).isoformat()
    last = (datetime.date.fromisoformat(row["end"]) + datetime.timedelta(days=1)).isoformat()
    meta.update(fire=a.fire, catalog=row, lon=lon, lat=lat, radius_km=radius, refine=refine,
                elmfire_cell_m=375.0 / refine, cells=int(round(2 * radius / 0.375)) * refine, truncated=truncated,
                window=[first, last])
    save()
    log.info("%s: %s..%s, box %.1f km half-side at %.3f, %.3f, %g m cells (%d x %d)%s", a.fire, first, last, radius,
             lon, lat, 375.0 / refine, meta["cells"], meta["cells"], " TRUNCATED" if truncated else "")
    prefix = os.path.join(fire_dir, "fire")
    obs_json, state_tif = prefix + "-observed.json", prefix + "-state.tif"

    if "2" in steps and not os.path.exists(os.path.join(fire_dir, "detections.csv")):
        t = time.time()
        # Granule listing box: the case box in degrees.
        dlat = radius / 111.32
        dlon = dlat / math.cos(math.radians(lat))
        bbox = "%.3f,%.3f,%.3f,%.3f" % (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
        files, got, total = granules(fire_dir, bbox, first, last, geo=a.geo)
        meta["timing_s"]["viirs_download"] = round(time.time() - t, 1)
        meta["viirs_granules"] = len(files)
        meta["viirs_mb"] = round(du(files) / 1e6, 1)
        meta["download_mb_total_listed"] = round(total / 1e6, 1)
        csv_tmp = os.path.join(fire_dir, "detections.csv.part")
        # Many granules: the converter takes them all on one command line.
        meta["timing_s"]["viirs_csv"] = round(run(["scripts/viirs_fire_to_firms_csv.py", csv_tmp] + sorted(files),
                                                  logfile), 1)
        os.replace(csv_tmp, os.path.join(fire_dir, "detections.csv"))
        save()

    if "3" in steps and not os.path.exists(obs_json):
        empty = os.path.join(fire_dir, "no_l1b")
        os.makedirs(empty, exist_ok=True)
        meta["timing_s"]["observations"] = round(run(
            ["scripts/fire_observations.py", prefix, os.path.join(fire_dir, "detections.csv"), empty, first,
             str(lon), str(lat), str(radius), last], logfile), 1)
        if a.geo:
            meta["timing_s"]["obscured"] = round(run(["scripts/obscured_cells.py", obs_json, RAW_FIRE, RAW_GEO],
                                                     logfile), 1)
        save()
    obs = json.load(open(obs_json)) if os.path.exists(obs_json) else None
    if obs:
        meta["frames"] = len(obs["frames"])
        meta["perimeters"] = len(obs["perimeters"])

    landscape = os.path.join(fire_dir, "landscape.tif")
    if "4" in steps and not os.path.exists(landscape):
        meta["timing_s"]["landscape"] = round(run(["scripts/landscape_download.py", state_tif, str(refine),
                                                   landscape], logfile), 1)
        meta["landscape_raw_mb"] = round(du(glob.glob(os.path.join(fire_dir, "landscape_raw", "*"))) / 1e6, 1)
        save()

    if "5" in steps and obs:
        if "issues" not in meta:
            issues = choose_issues(obs, row["start"], row["end"])
            if a.max_issues and len(issues) > a.max_issues:
                issues = sorted(sorted(issues, key=lambda i: -i["hot_cells_after"])[:a.max_issues],
                                key=lambda i: i["issue"])
            meta["issues"] = issues
            save()
        os.makedirs(os.path.join(fire_dir, "obs"), exist_ok=True)
        for item in meta["issues"]:
            issue = item["issue"]
            stamp = issue[:13].replace("-", "").replace(":", "")
            cut = os.path.join(fire_dir, "obs", stamp + "-observed.json")
            if not os.path.exists(cut):
                cut_observations(obs, issue, cut)
            item["observed"] = cut
            wxdir = os.path.join(fire_dir, "wx", stamp)
            item["wx"] = wxdir
            if os.path.exists(os.path.join(wxdir, "wx.json")):
                continue
            runtime = datetime.datetime.fromisoformat(issue)
            hours = hrrr_hours(runtime)
            if hours is None:
                log.warning("%s: no HRRR 12Z run in the archive; issue skipped", issue)
                item["wx"] = None
                continue
            t = time.time()
            rawdir = os.path.join(RAW_HRRR, a.fire)
            prefetch_hrrr(runtime, hours, rawdir)
            run(["scripts/hrrr_weather.py", state_tif, issue[:13], str(hours), wxdir + ".part", rawdir], logfile)
            if hours < 24:
                pad_weather(wxdir + ".part", hours)
            item["hrrr_hours"] = hours
            item["hrrr_mb"] = round(du(glob.glob(os.path.join(rawdir, "*"))) / 1e6, 1)
            os.replace(wxdir + ".part", wxdir)
            if not a.keep_raw:
                shutil.rmtree(rawdir, ignore_errors=True)
            meta["timing_s"].setdefault("hrrr", 0)
            meta["timing_s"]["hrrr"] = round(meta["timing_s"]["hrrr"] + time.time() - t, 1)
            save()
        meta["issues"] = [i for i in meta["issues"] if i.get("wx")]
        meta["ready"] = os.path.exists(landscape) and bool(meta["issues"])
        save()
    log.info("%s: %s; %d issues; timings %s", a.fire, "ready" if meta.get("ready") else "not ready",
             len(meta.get("issues", [])), meta["timing_s"])


if __name__ == "__main__":
    main()
