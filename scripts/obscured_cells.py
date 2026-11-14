#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["h5py", "numpy", "pyproj", "rasterio", "scipy", "shapely>=2.1"]
# ///
"""Adds to each overpass of an observed-heat file the grid cells the satellite could not see, from the VIIRS active
fire product's per-pixel fire mask placed with the matching geolocation granule (VNP03IMG / VJ103IMG / VJ203IMG;
the fire product only geolocates its fire pixels).

Fire mask classes (VIIRS 375 m active fire ATBD): 0 not processed, 1 bow-tie deletion, 2 sun glint, 3 water,
4 cloud, 5 land (no fire), 6 unclassified land, 7-9 fire (low, nominal, high confidence). A 375 m grid cell is
"obscured" on a pass if it held pixels of that pass and none of them was clear: no 3, 5 or 7-9 pixel (water counts
as seen; bow-tie deleted pixels are covered by the neighbouring scan, so a cell they alone fill is not counted).
heat_frames.py leaves obscured cells out of the score: a fire predicted under cloud is neither hit nor false alarm.

Usage: obscured_cells.py OBSERVED.json FIRE_DIR GEO_DIR
       e.g. obscured_cells.py data/fires/widemouth-0802-0806-observed.json data/raw/viirs_fire data/raw/viirs_geo
Rewrites OBSERVED.json in place, adding "obscured": [[row, col], ...] and "seen_cells" to every frame whose
granules are present, and "obscured_source" to the file.
"""
import datetime
import glob
import json
import logging
import os
import re
import sys

import h5py
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fire_observations import Grid  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

PREFIX = {"N": "VNP", "N20": "VJ1", "N21": "VJ2"}
CLEAR = (3, 5, 7, 8, 9)
GRANULE_S = 6 * 60


def granules(directory, prefix, product):
    """{start datetime: path} of PREFIX+PRODUCT granules in DIRECTORY."""
    out = {}
    for path in glob.glob(os.path.join(directory, "%s%s.A*.nc" % (prefix, product))):
        m = re.search(r"\.A(\d{4})(\d{3})\.(\d{2})(\d{2})\.", path)
        start = (datetime.datetime(int(m[1]), 1, 1) + datetime.timedelta(days=int(m[2]) - 1, hours=int(m[3]),
                                                                          minutes=int(m[4])))
        out[start] = path
    return out


def read_geo(path):
    with h5py.File(path) as h:
        g = h["geolocation_data"]
        return g["latitude"][:], g["longitude"][:]


def main(observed_json, fire_dir, geo_dir):
    obs = json.load(open(observed_json))
    lon0, lat0 = obs["center"]
    grid = Grid(lon0, lat0, obs["radius_km"] * 1000)
    w, s, e, n = grid.bounds
    for frame in obs["frames"]:
        prefix = PREFIX[frame["satellite"]]
        t = datetime.datetime.fromisoformat(frame["time"].replace("Z", ""))
        fires, geos = granules(fire_dir, prefix, "14IMG"), granules(geo_dir, prefix, "03IMG")
        # Granules overlapping the pass (its detections span a few minutes, possibly two granules).
        starts = [g for g in fires if g - datetime.timedelta(seconds=GRANULE_S + 300) <= t <= g + datetime.timedelta(
            seconds=GRANULE_S + 300) and g in geos]
        if not starts:
            log.warning("%s %s: no fire + geolocation granules", frame["time"], frame["satellite"])
            continue
        seen = np.zeros((grid.n, grid.n), int)
        clear = np.zeros((grid.n, grid.n), int)
        for g in starts:
            lat, lon = read_geo(geos[g])
            box = (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)
            if not box.any():
                continue
            with h5py.File(fires[g]) as h:
                mask = h["fire mask"][:]
            if mask.shape != lat.shape:
                log.warning("%s: fire mask %s vs geolocation %s", fires[g], mask.shape, lat.shape)
                continue
            rows, cols = grid.cell(lon[box], lat[box])
            cls = mask[box]
            ok = (rows >= 0) & (cls != 1)
            np.add.at(seen, (rows[ok], cols[ok]), 1)
            np.add.at(clear, (rows[ok], cols[ok]), np.isin(cls[ok], CLEAR).astype(int))
        obscured = (seen > 0) & (clear == 0)
        r, c = np.nonzero(obscured)
        frame["obscured"] = [[int(i), int(j)] for i, j in zip(r, c)]
        frame["seen_cells"] = int((seen > 0).sum())
        log.info("%s %s: %d granule(s), %d of %d cells observed, %d obscured (%.0f%%)", frame["time"],
                 frame["satellite"], len(starts), int((seen > 0).sum()), grid.n ** 2, len(r),
                 100 * len(r) / max(1, int((seen > 0).sum())))
    obs["obscured_source"] = "VIIRS fire mask (VNP14IMG/VJ114IMG/VJ214IMG) with 03IMG geolocation"
    json.dump(obs, open(observed_json, "w"), separators=(",", ":"))
    log.info("wrote %s", observed_json)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
