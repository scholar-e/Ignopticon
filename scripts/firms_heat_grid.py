#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy"]
# ///
"""Grid FIRMS VIIRS hotspots into a smoothed fire-heat field that earth shows as an overlay.

Each detection adds its fire radiative power (FRP, MW) to the grid cell it falls in; the sum is then smoothed with
a Gaussian kernel, so a value reads as "FRP detected nearby" and a lone detection peaks at roughly its own FRP.
Output uses earth's grib2json layout (one scalar record) over a fixed box covering the lower 48 and Hawaii.

Usage: firms_heat_grid.py [--hours=N] OUT.json IN.csv [IN.csv ...]
       --hours=N keeps only detections within N hours of the newest one, as in firms_to_earth_json.py.
"""
import csv
import json
import logging
import math
import sys
from datetime import datetime, timezone

import numpy as np
from scipy.ndimage import gaussian_filter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LON_MIN, LON_MAX = -161.0, -66.0
LAT_MIN, LAT_MAX = 18.0, 50.0
STEP = 0.1           # degrees per cell (~10 km)
SIGMA_KM = 20.0      # smoothing radius: a regional heat map, with individual detections left to the glows
MIN_VALUE = 0.05     # smaller values are written as 0 to keep the file small


def acquired(row):
    return datetime.strptime(row["acq_date"] + row["acq_time"].zfill(4), "%Y-%m-%d%H%M")


def main(dst, *srcs, hours=None):
    nx = round((LON_MAX - LON_MIN) / STEP) + 1
    ny = round((LAT_MAX - LAT_MIN) / STEP) + 1
    grid = np.zeros((ny, nx))
    rows = []
    for src in srcs:
        with open(src, newline="") as f:
            rows += list(csv.DictReader(f))
    newest = max(acquired(row) for row in rows)
    if hours is not None:
        rows = [row for row in rows if (newest - acquired(row)).total_seconds() <= hours * 3600]
    count = 0
    for row in rows:
        i = round((float(row["longitude"]) - LON_MIN) / STEP)
        j = round((LAT_MAX - float(row["latitude"])) / STEP)  # rows run north to south
        if 0 <= i < nx and 0 <= j < ny:
            grid[j, i] += float(row["frp"])
            count += 1

    # Gaussian smoothing with σ in cells; longitude cells are narrower than latitude cells away from the equator.
    km_per_cell_lat = STEP * 111.2
    km_per_cell_lon = km_per_cell_lat * math.cos(math.radians((LAT_MIN + LAT_MAX) / 2))
    sigma = (SIGMA_KM / km_per_cell_lat, SIGMA_KM / km_per_cell_lon)
    # gaussian_filter normalizes the kernel; rescale so a single detection keeps a peak near its FRP.
    smoothed = gaussian_filter(grid, sigma) * (2 * math.pi * sigma[0] * sigma[1])
    smoothed[smoothed < MIN_VALUE] = 0

    data = [0 if v == 0 else round(float(v), 2) for v in smoothed.ravel()]
    header = {
        "centerName": "NASA FIRMS (VIIRS)",
        "refTime": newest.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "forecastTime": 0,
        "parameterUnit": "MW",
        "nx": nx,
        "ny": ny,
        "lo1": LON_MIN,
        "la1": LAT_MAX,
        "lo2": LON_MAX,
        "la2": LAT_MIN,
        "dx": STEP,
        "dy": STEP,
        "numberPoints": nx * ny,
    }
    with open(dst, "w") as f:
        json.dump([{"header": header, "data": data}], f, separators=(",", ":"))
    log.info("wrote %s: %d detections on %dx%d grid, max %.1f MW, %d nonzero cells",
             dst, count, nx, ny, smoothed.max(), int((smoothed > 0).sum()))


if __name__ == "__main__":
    args = sys.argv[1:]
    hours = float(args.pop(0).split("=")[1]) if args and args[0].startswith("--hours=") else None
    main(*args, hours=hours)
