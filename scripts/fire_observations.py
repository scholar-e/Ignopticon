#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["h5py", "numpy", "scipy", "pyproj", "rasterio", "shapely>=2.1"]
# ///
"""Combine every heat source we have for one fire into a timeline of observed heat, and the fire state it implies.

Sources, all put on one 375 m grid (azimuthal equidistant, centered on the fire):
  - VIIRS active fire detections from every satellite (S-NPP, NOAA-20, NOAA-21; FIRMS-style CSV), grouped into
    overpasses. Each detection adds its FRP to its cell.
  - The fire band (VIIRS I4, 3.7 µm) of each NOAA-21 overpass, read from the raw L1B granules (the "Fire band"
    tiles mosaic several passes, so they can't be split back into overpasses). Pixels clearly warmer than their
    surroundings are turned into FRP with the MIR radiance method (Wooster et al. 2003), so heat below the detection
    threshold is counted too. A cell with a detection keeps the detection's FRP, which the fire algorithm takes
    from the unsaturated 4 µm M13 band when I4 saturates.
  - NASA FEDS perimeters (VEDA features API) at every 12-hourly snapshot from the day before to the day after: the
    area already burned, and later perimeters to verify against. FEDS covers 2025 on (NRT) and 2018-2021 (archive);
    other years, or FEDS unreachable, give detections only.

The fire state is each cell's arrival time: the first overpass that saw it hot, or, for cells inside a FEDS
perimeter but never seen hot, midway between the snapshot that first contains them and the one before. A model is
started from the state at its issue time and its predicted heat is compared with these frames (heat_frames.py).

Usage: fire_observations.py OUT_PREFIX DETECTIONS.csv GRANULE_DIR DATE LON LAT [RADIUS_KM] [END_DATE]
       e.g. fire_observations.py data/fires/widemouth-2026-08-02 \
                data/raw/firms/VIIRS_LAADS_USA_contiguous_and_Hawaii_20260802.csv data/raw/viirs_l1b \
                2026-08-02 -112.37 38.79 30
END_DATE (default DATE) extends the window to several days: detections from DATE to END_DATE, FEDS snapshots from the
day before DATE to the day after END_DATE. The fire band is read for DATE only.
Writes OUT_PREFIX-observed.json (frames, perimeters) and OUT_PREFIX-state.tif (arrival, seconds since DATE 00Z;
nodata where never burned in the window).
"""
import csv
import datetime
import glob
import json
import logging
import math
import os
import sys
import urllib.parse
import urllib.request

import h5py
import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import median_filter
from shapely.geometry import mapping, shape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

CELL = 375.0                 # m, the VIIRS I-band pixel at nadir
PASS_GAP = 15 * 60           # s: detections closer in time than this from one satellite are one overpass
I4_WARM_K = 5.0              # an I4 pixel at least this much warmer than its surroundings counts as fire heat...
I4_NOISE_SIGMAS = 3.5        # ...and at least this many robust standard deviations of its surroundings
I4_BACKGROUND_PX = 21        # background window, in swath pixels (~8 km)
I4_MAX = 367.0               # K, about where I4 saturates
SATURATION, OUT_OF_RANGE = 4, 2  # I04_quality_flags bits, as in viirs_i4_tiles.py
WARM_I5 = 270.0              # an out-of-range I4 pixel is a fire if I5 is warmer than this...
CLOUD_I5 = 265.0             # ...and a pixel whose I5 is colder than this is a cloud top, not ground
FEDS = "https://openveda.cloud/api/features/collections/public.eis_fire_lf_perimeter_nrt/items"
# FEDS's archive of large-fire perimeters (CONUS, 2018-2021 as far as checked; fire ids like "F13066"). 2022-2024
# and anything before 2018 have no FEDS perimeters: the fire state then comes from detections only.
FEDS_ARCHIVE = "https://openveda.cloud/api/features/collections/public.eis_fire_lf_perimeter_archive/items"
NODATA = -9999.0
LAMBDA_I4 = 3.74             # µm
C1, C2 = 1.191042e8, 1.4387752e4  # W µm^4 m^-2 sr^-1, µm K
SIGMA = 5.670374e-8


def planck(kelvin, lam=LAMBDA_I4):
    """Spectral radiance, W m^-2 sr^-1 µm^-1."""
    return C1 / (lam ** 5 * (np.exp(C2 / (lam * np.asarray(kelvin, dtype=np.float64))) - 1))


def inverse_planck(radiance, lam=LAMBDA_I4):
    return C2 / (lam * np.log(1 + C1 / (lam ** 5 * np.asarray(radiance, dtype=np.float64))))


def mir_constant(lam=LAMBDA_I4):
    """a in L(T) ~ a T^4 over flaming and smoldering temperatures (650-1350 K), least squares, so that
    FRP = A sigma / a * (L - L_background) for a pixel of area A (Wooster et al. 2003)."""
    t = np.linspace(650, 1350, 200)
    return float((planck(t, lam) * t ** 4).sum() / (t ** 8).sum())


class Grid:
    """CELL-sized cells, azimuthal equidistant about the fire, RADIUS on each side."""

    def __init__(self, lon, lat, radius_m):
        self.crs = "+proj=aeqd +lat_0=%f +lon_0=%f +datum=WGS84 +units=m" % (lat, lon)
        self.n = int(math.ceil(2 * radius_m / CELL))
        self.x0 = self.y0 = -self.n * CELL / 2  # west, south edges
        self.to_xy = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True).transform
        self.to_lonlat = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True).transform
        self.transform = from_origin(self.x0, self.y0 + self.n * CELL, CELL, CELL)
        edge = np.linspace(self.x0, -self.x0, 50)
        lons, lats = self.to_lonlat(np.concatenate([edge, edge, np.full(50, self.x0), np.full(50, -self.x0)]),
                                    np.concatenate([np.full(50, self.y0), np.full(50, -self.y0), edge, edge]))
        self.bounds = (lons.min(), lats.min(), lons.max(), lats.max())  # west, south, east, north

    def cell(self, lon, lat):
        """(row, col) arrays, row 0 at the north edge; -1 outside."""
        x, y = self.to_xy(np.asarray(lon, float), np.asarray(lat, float))
        col = np.floor((x - self.x0) / CELL).astype(int)
        row = self.n - 1 - np.floor((y - self.y0) / CELL).astype(int)
        inside = (col >= 0) & (col < self.n) & (row >= 0) & (row < self.n)
        return np.where(inside, row, -1), np.where(inside, col, -1)

    def centers(self):
        """lon, lat of every cell center, as n x n arrays."""
        i = np.arange(self.n)
        x = self.x0 + (i + 0.5) * CELL
        y = self.y0 + (self.n - 1 - i + 0.5) * CELL
        xx, yy = np.meshgrid(x, y)
        return self.to_lonlat(xx, yy)


def epoch(date, hhmm):
    return datetime.datetime.strptime(date + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=datetime.timezone.utc).timestamp()


def iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def overpasses(rows, grid):
    """Detections in the grid grouped by satellite and time: [{satellite, time, rows}], in time order."""
    found = []
    for r in rows:
        row, col = grid.cell(float(r["longitude"]), float(r["latitude"]))
        if row < 0:
            continue
        found.append((r["satellite"], epoch(r["acq_date"], r["acq_time"]), int(row), int(col), r))
    found.sort(key=lambda f: (f[0], f[1]))
    passes = []
    for sat, t, row, col, r in found:
        if not passes or passes[-1]["satellite"] != sat or t - passes[-1]["last"] > PASS_GAP:
            passes.append({"satellite": sat, "first": t, "last": t, "cells": []})
        p = passes[-1]
        p["last"] = t
        p["cells"].append((row, col, float(r["frp"]), float(r["scan"]) * float(r["track"])))
    for p in passes:
        p["time"] = (p["first"] + p["last"]) / 2
    return sorted(passes, key=lambda p: p["time"])


def i4_granule(grid, l1b, geo):
    """FRP per cell from one VIIRS I-band L1B granule (02IMG with its 03IMG geolocation), or None if it misses the
    grid. A land pixel counts as fire heat if its I4 is above the median of a ~8 km window around it (in swath
    coordinates) by I4_WARM_K or I4_NOISE_SIGMAS robust standard deviations, whichever is more, and its I5 is not a
    cloud top; its FRP is its area times sigma / a times its I4 radiance above that median. Saturated and folded-over
    fire pixels (quality flags, as in viirs_i4_tiles.py) are set to the band's maximum, so theirs is a lower bound."""
    with h5py.File(geo, "r") as g:
        lat = g["geolocation_data/latitude"][:]
        lon = g["geolocation_data/longitude"][:]
        land = g["geolocation_data/land_water_mask"][:]
    w, s, e, n = grid.bounds
    near = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
    if not near.any():
        return None
    rs, cs = np.nonzero(near)
    pad = I4_BACKGROUND_PX
    r0, r1 = max(rs.min() - pad, 0), min(rs.max() + pad + 1, lat.shape[0])
    c0, c1 = max(cs.min() - pad, 0), min(cs.max() + pad + 1, lat.shape[1])
    crop = (slice(r0, r1), slice(c0, c1))
    lat, lon, land = lat[crop], lon[crop], land[crop]
    row, col = grid.cell(lon, lat)
    inside = row >= 0
    if not inside.any():
        return None
    with h5py.File(l1b, "r") as f:
        dn = f["observation_data/I04"][crop]
        lut = f["observation_data/I04_brightness_temperature_lut"][:]
        flags = f["observation_data/I04_quality_flags"][crop]
        dn5 = f["observation_data/I05"][crop]
        lut5 = f["observation_data/I05_brightness_temperature_lut"][:]
    i5 = np.where(dn5 <= 65527, lut5[np.minimum(dn5, 65535)], 0)
    kelvin = np.where(dn <= 65527, lut[np.minimum(dn, 65535)], np.nan).astype(np.float64)
    out_of_range = (flags & OUT_OF_RANGE) != 0
    saturated = ((flags & SATURATION) != 0) | (out_of_range & (i5 > WARM_I5))
    kelvin[saturated] = I4_MAX
    kelvin[out_of_range & ~saturated] = np.nan
    kelvin[kelvin <= 0] = np.nan
    usable = np.isfinite(kelvin) & (i5 > CLOUD_I5) & (land == 1)
    if not (usable & inside).any():
        return None
    # Background: median and median absolute deviation over the window, with unusable pixels (cloud, water, no
    # data) standing in as the median of the usable ones, so they neither raise nor lower it much.
    values = np.where(usable, kelvin, np.median(kelvin[usable]))
    background = median_filter(values, size=I4_BACKGROUND_PX, mode="nearest")
    spread = median_filter(np.abs(values - background), size=I4_BACKGROUND_PX, mode="nearest") * 1.4826
    threshold = np.maximum(I4_WARM_K, I4_NOISE_SIGMAS * spread)
    warm = usable & (kelvin - background >= threshold) & inside
    # Pixel footprint from the spacing of neighboring pixel centers (grows toward the scan edges).
    x, y = grid.to_xy(lon, lat)
    along_scan = np.hypot(np.gradient(x, axis=1), np.gradient(y, axis=1))
    along_track = np.hypot(np.gradient(x, axis=0), np.gradient(y, axis=0))
    area = along_scan * along_track
    excess = planck(kelvin[warm]) - planck(background[warm])
    frp_mw = area[warm] * SIGMA / mir_constant() * excess / 1e6
    frame = np.zeros((grid.n, grid.n))
    np.add.at(frame, (row[warm], col[warm]), frp_mw)
    log.info("%s: %d pixels over the fire area, %d warm (%d saturated), background %.1f K, %.0f MW",
             os.path.basename(l1b), int(inside.sum()), int(warm.sum()), int((saturated & warm).sum()),
             float(np.median(background[inside])), frp_mw.sum())
    return frame


def i4_frames(grid, granule_dir, date):
    """[{time, frp}] for each NOAA-21 overpass of the grid in GRANULE_DIR's I-band L1B granules on DATE (granules
    within 6 minutes are one overpass)."""
    day = datetime.date.fromisoformat(date).strftime("A%Y%j")
    frames = []
    for l1b in sorted(glob.glob(os.path.join(granule_dir, "VJ202IMG." + day + ".*.nc"))):
        stamp = ".".join(os.path.basename(l1b).split(".")[1:3])
        geo = glob.glob(os.path.join(granule_dir, "VJ203IMG." + stamp + ".*.nc"))
        if not geo:
            continue
        frame = i4_granule(grid, l1b, geo[0])
        if frame is None:
            continue
        start = epoch(date, stamp.split(".")[1])
        if frames and start - frames[-1]["start"] <= 6 * 60:
            frames[-1]["frp"] += frame
            frames[-1]["start"] = start
        else:
            frames.append({"start": start, "time": start + 3 * 60, "frp": frame})
    return frames


def fire_id(value):
    """FEDS NRT fire ids are numbers, archive ones strings like "F13066"."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def perimeters(grid, lon, lat, radius_m, date, end_date=None):
    """FEDS large-fire perimeters within the grid from the day before DATE to the day after, as
    [{time, fireid, area_km2, geometry (lon/lat GeoJSON), mask (cells inside)}], the biggest fire per snapshot."""
    day = datetime.date.fromisoformat(date)
    last = datetime.date.fromisoformat(end_date or date)
    dlat = radius_m / 111320.0
    dlon = dlat / math.cos(math.radians(lat))
    params = {"bbox": "%f,%f,%f,%f" % (lon - dlon, lat - dlat, lon + dlon, lat + dlat), "limit": 1000,
              "datetime": "%sT00:00:00Z/%sT23:59:59Z" % (day - datetime.timedelta(days=1),
                                                       last + datetime.timedelta(days=1))}
    url = FEDS if day.year >= 2025 else FEDS_ARCHIVE if 2018 <= day.year <= 2021 else None
    if url is None:
        log.info("no FEDS perimeters for %s: detections only", date)
        return []
    try:
        if os.environ.get('FEDS_SNAPSHOT_DIR'):
            import glob
            features = []
            for path in sorted(glob.glob(os.path.join(os.environ['FEDS_SNAPSHOT_DIR'], '*-*.json'))):
                snapshot = json.load(open(path))
                stamp = snapshot['time'].rstrip('Z') + 'Z'
                if not params['datetime'].split('/')[0] <= stamp <= params['datetime'].split('/')[1]:
                    continue
                features.extend({'geometry': f['geometry'], 'properties': {
                    't': stamp.rstrip('Z'), 'fireid': f['properties']['id'],
                    'farea': f['properties']['area_km2']}} for f in snapshot['features'])
        else:
            with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=300) as response:
                features = json.load(response)["features"]
    except Exception as err:  # FEDS down or slow: carry on from detections only
        log.warning("FEDS perimeters unavailable (%s): detections only", err)
        return []
    biggest = {}
    for f in features:
        p = f["properties"]
        if os.environ.get("FEDS_FIRE_ID") and str(p["fireid"]) != os.environ["FEDS_FIRE_ID"]:
            continue
        if p["t"] not in biggest or p["farea"] > biggest[p["t"]]["properties"]["farea"]:
            biggest[p["t"]] = f
    result = []
    for t in sorted(biggest):
        f = biggest[t]
        geometry = shape(f["geometry"])
        projected = shapely.transform(geometry, lambda xy: np.column_stack(grid.to_xy(xy[:, 0], xy[:, 1])))
        mask = rasterize([(mapping(projected), 1)], out_shape=(grid.n, grid.n), transform=grid.transform,
                         all_touched=False, dtype=np.uint8).astype(bool)
        stamp = datetime.datetime.fromisoformat(t).replace(tzinfo=datetime.timezone.utc).timestamp()
        result.append({"time": stamp, "fireid": fire_id(f["properties"]["fireid"]),
                       "area_km2": round(f["properties"]["farea"], 1),
                       "geometry": mapping(geometry.simplify(0.001)), "mask": mask})
    return result


def main(prefix, detections_csv, granule_dir, date, lon, lat, radius_km="30", end_date=None):
    lon, lat, radius_m = float(lon), float(lat), float(radius_km) * 1000
    grid = Grid(lon, lat, radius_m)
    rows = [r for r in csv.DictReader(open(detections_csv)) if date <= r["acq_date"] <= (end_date or date)]
    passes = overpasses(rows, grid)
    i4 = i4_frames(grid, granule_dir, date)
    reference = epoch(date, "0000")

    frames = []
    for p in passes:
        frp = np.zeros((grid.n, grid.n))
        detected = np.zeros((grid.n, grid.n), bool)
        pixel_area = []
        for row, col, value, area in p["cells"]:
            frp[row, col] += value
            detected[row, col] = True
            pixel_area.append(area)
        band = None
        if p["satellite"] == "N21":
            band = next((f for f in i4 if abs(f["time"] - p["time"]) <= 8 * 60), None)
        heat = np.where(detected, frp, band["frp"] if band else 0.0)
        source = np.where(detected, 1, np.where(heat > 0, 2, 0))
        r, c = np.nonzero(heat > 0)
        lons, lats = grid.centers()
        frames.append({
            "time": iso(p["time"]), "seconds": p["time"] - reference, "satellite": p["satellite"],
            "sources": ["VIIRS detections"] + (["Fire band (I4)"] if band else []),
            "pixel_km2": round(float(np.mean(pixel_area)), 3),  # mean footprint (scan x track) over the fire
            "detections": int(detected.sum()), "fire_band_cells": int((source == 2).sum()),
            "frp_mw": round(float(heat.sum()), 1),
            "cells": [[round(float(lons[i, j]), 5), round(float(lats[i, j]), 5), round(float(heat[i, j]), 2),
                       int(source[i, j])] for i, j in zip(r, c)],
        })
        log.info("%s %s: %d detections, %d fire-band-only cells, %.0f MW", frames[-1]["time"], p["satellite"],
                 frames[-1]["detections"], frames[-1]["fire_band_cells"], frames[-1]["frp_mw"])

    perims = perimeters(grid, lon, lat, radius_m, date, end_date)
    log.info("FEDS: %s", ", ".join("%s %.0f km2" % (iso(p["time"]), p["area_km2"]) for p in perims))

    # Arrival: the first pass that saw the cell hot; else midway between the FEDS snapshot that first contains it
    # and the one before.
    arrival = np.full((grid.n, grid.n), np.inf)
    for f in frames:
        for lo, la, heat, src in f["cells"]:
            row, col = grid.cell(lo, la)
            arrival[row, col] = min(arrival[row, col], f["seconds"])
    previous = None
    for p in perims:
        seconds = p["time"] - reference
        guess = seconds if previous is None else (previous + seconds) / 2
        unseen = p["mask"] & np.isinf(arrival)
        arrival[unseen] = guess
        previous = seconds
    state = np.where(np.isinf(arrival), NODATA, arrival).astype(np.float32)
    with rasterio.open(prefix + "-state.tif", "w", driver="GTiff", width=grid.n, height=grid.n, count=1,
                       dtype="float32", crs=grid.crs, transform=grid.transform, nodata=NODATA,
                       compress="deflate") as out:
        out.write(state, 1)
        out.update_tags(reference=iso(reference), units="s since reference", cell_m=CELL)

    json.dump({"center": [lon, lat], "radius_km": float(radius_km), "reference": iso(reference),
               "cell_m": CELL, "crs": grid.crs, "frames": frames,
               "perimeters": [{k: v for k, v in p.items() if k != "mask"} | {"time": iso(p["time"])}
                              for p in perims]},
              open(prefix + "-observed.json", "w"), separators=(",", ":"))
    log.info("wrote %s-observed.json (%d frames) and %s-state.tif (%d cells burned by the last snapshot)",
             prefix, len(frames), prefix, int((state != NODATA).sum()))


if __name__ == "__main__":
    main(*sys.argv[1:9])
