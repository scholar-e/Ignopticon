#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["h5py", "numpy", "scipy", "pillow"]
# ///
"""Turn VIIRS I-band L1B swaths (02IMG with their 03IMG geolocation) into map tiles of I4 (3.7 µm) brightness
temperature, the band VIIRS finds fires with, for earth's "Fire band" underlay.

Every swath pixel is placed on the zoom-9 Web Mercator grid (~230 m at US latitudes); where swaths overlap the hotter
value is kept, so no fire is lost, and the small gaps between pixels toward the scan edges are filled with the mean
of their neighbors. Coarser zooms average 2x2 blocks, except that a block with a pixel above FIRE_K keeps its hottest.
Tiles are 8-bit palette PNGs whose palette index is the value (0 no data, index i ~ KELVIN_MIN + (i - 0.5) * STEP),
colored by SCALE, as dev-server.js reads them for click lookups.

Usage: viirs_i4_tiles.py SCALE.json OUTDIR GRANULE_DIR PRODUCT_PREFIX DATE [FROM TO]
       e.g. viirs_i4_tiles.py earth/public/data/thermal/i4-scale.json earth/public/data/thermal/i4/2026-10-29/N21-day \
                data/raw/viirs_l1b VJ2 2026-10-29
       Uses the PRODUCT_PREFIX (VJ2 = NOAA-21) granules in GRANULE_DIR that start on DATE, between FROM and TO
       (UTC, HHMM) if given, e.g. 0520 1040 for a night pass over the lower 48.
"""
import datetime
import glob
import json
import logging
import math
import os
import sys

import h5py
import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

WEST, SOUTH, EAST, NORTH = -126.0, 23.0, -65.0, 51.0  # lower 48
ZOOM = 9                    # finest tiles, ~230 m pixels at 40° N; VIIRS I-band pixels are 375 m at nadir
TILE = 256
KELVIN_MIN, KELVIN_MAX = 200.0, 365.0
STEP = (KELVIN_MAX - KELVIN_MIN) / 255  # kelvin per palette index
FIRE_K = 330.0              # coarser zooms keep a block's hottest pixel above this, so fires stay visible
GAP_FILL = 3                # passes of filling empty pixels from their neighbors
SATURATION = 4              # I04_quality_flags: the sensor maxed out (fires)
OUT_OF_RANGE = 2            # I04_quality_flags: beyond the band's range either way, with a meaningless count (often
                            # 0): a fire whose count "folded over", or at night, cloud tops too cold for the band.
WARM_I5 = 270.0             # an out-of-range I4 pixel is a fire if the I5 (11 µm) band reads warmer than this (K)


def mercator(lon, lat, zoom):
    """Global pixel coordinates of lon/lat at the zoom."""
    n = TILE * 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1 - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) / np.pi) / 2 * n
    return x, y


def palette(scale_file):
    stops = json.load(open(scale_file))["stops"]
    rgb, alpha = [0, 0, 0], [0]
    for i in range(1, 256):
        k = KELVIN_MIN + (i - 0.5) * STEP
        j = 1
        while j < len(stops) - 1 and stops[j][0] < k:
            j += 1
        a, b = stops[j - 1], stops[j]
        t = min(1.0, max(0.0, (k - a[0]) / (b[0] - a[0])))
        rgb += [round(a[c] + t * (b[c] - a[c])) for c in (1, 2, 3)]
        alpha.append(round(255 * (a[4] + t * (b[4] - a[4]))))
    return rgb, bytes(alpha)


def place(grid, x0, y0, l1b, geo):
    """Puts one granule's I4 pixels on the grid, keeping the hotter value where pixels meet."""
    with h5py.File(l1b, "r") as f:
        dn = f["observation_data/I04"][:]
        lut = f["observation_data/I04_brightness_temperature_lut"][:]
        flags = f["observation_data/I04_quality_flags"][:]
        dn5 = f["observation_data/I05"][:]
        lut5 = f["observation_data/I05_brightness_temperature_lut"][:]
    i5 = np.where(dn5 <= 65527, lut5[np.minimum(dn5, 65535)], 0)
    out_of_range = (flags & OUT_OF_RANGE) != 0
    saturated = ((flags & SATURATION) != 0) | (out_of_range & (i5 > WARM_I5))
    too_cold = out_of_range & ~saturated
    with h5py.File(geo, "r") as g:
        lat = g["geolocation_data/latitude"][:]
        lon = g["geolocation_data/longitude"][:]
    ok = (dn <= 65527) & (lat >= SOUTH) & (lat <= NORTH) & (lon >= WEST) & (lon <= EAST)
    kelvin = lut[dn[ok]]
    kelvin[saturated[ok]] = KELVIN_MAX  # at least the band's maximum
    kelvin[too_cold[ok]] = KELVIN_MIN
    ok_kelvin = kelvin > 0  # the table holds -999.9 past its range
    index = np.clip(np.floor((kelvin[ok_kelvin] - KELVIN_MIN) / STEP) + 1, 1, 255).astype(np.uint8)
    x, y = mercator(lon[ok][ok_kelvin], lat[ok][ok_kelvin], ZOOM)
    col = x.astype(np.int64) - x0
    row = y.astype(np.int64) - y0
    inside = (col >= 0) & (col < grid.shape[1]) & (row >= 0) & (row < grid.shape[0])
    flat = row[inside] * grid.shape[1] + col[inside]
    values = index[inside]
    order = np.argsort(values, kind="stable")  # assigning in rising order leaves the hottest at each pixel
    view = grid.reshape(-1)
    view[flat[order]] = np.maximum(view[flat[order]], values[order])
    log.info("%s: %d pixels in the lower 48, %d saturated (fires)", os.path.basename(l1b), len(values),
             int(saturated[ok].sum()))
    return len(values)


def downsample(grid):
    """Halves the grid: the mean of a block's data, or its hottest pixel if that is above FIRE_K."""
    h, w = grid.shape[0] // 2 * 2, grid.shape[1] // 2 * 2
    blocks = grid[:h, :w].reshape(h // 2, 2, w // 2, 2).transpose(0, 2, 1, 3).reshape(h // 2, w // 2, 4)
    data = blocks > 0
    count = data.sum(axis=2)
    mean = np.where(count > 0, (blocks.astype(np.float32) * data).sum(axis=2) / np.maximum(count, 1), 0)
    hottest = blocks.max(axis=2)
    fire_index = (FIRE_K - KELVIN_MIN) / STEP + 1
    return np.where(hottest >= fire_index, hottest, np.round(mean)).astype(np.uint8)


def write_tiles(grid, zoom, x0, y0, outdir, rgb, alpha):
    """Writes the grid (whose top left is global pixel x0, y0 at zoom) as tiles; returns how many."""
    written = 0
    for ty in range(y0 // TILE, (y0 + grid.shape[0] - 1) // TILE + 1):
        for tx in range(x0 // TILE, (x0 + grid.shape[1] - 1) // TILE + 1):
            tile = np.zeros((TILE, TILE), np.uint8)
            gy, gx = ty * TILE - y0, tx * TILE - x0
            src = grid[max(gy, 0):gy + TILE, max(gx, 0):gx + TILE]
            if not src.any():
                continue
            tile[max(-gy, 0):max(-gy, 0) + src.shape[0], max(-gx, 0):max(-gx, 0) + src.shape[1]] = src
            image = Image.fromarray(tile, "P")
            image.putpalette(rgb)
            path = os.path.join(outdir, str(zoom), str(tx), "%d.png" % ty)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            image.save(path, transparency=alpha, optimize=True)
            written += 1
    return written


def main(scale_file, outdir, granule_dir, prefix, date, start="0000", end="2359"):
    day = datetime.date.fromisoformat(date).strftime("A%Y%j")  # as granule names give it, e.g. A2026302
    pairs = []
    for l1b in sorted(glob.glob(os.path.join(granule_dir, prefix + "02IMG." + day + ".*.nc"))):
        if not start <= os.path.basename(l1b).split(".")[2] <= end:
            continue
        stamp = ".".join(os.path.basename(l1b).split(".")[1:3])  # A2026302.1918
        geo = glob.glob(os.path.join(granule_dir, prefix + "03IMG." + stamp + ".*.nc"))
        if geo:
            pairs.append((l1b, geo[0]))
    log.info("%d granules with geolocation", len(pairs))

    x_west, y_north = mercator(np.array(WEST), np.array(NORTH), ZOOM)
    x_east, y_south = mercator(np.array(EAST), np.array(SOUTH), ZOOM)
    x0, y0 = int(x_west) // TILE * TILE, int(y_north) // TILE * TILE  # whole tiles
    width = int(math.ceil((x_east - x0) / TILE)) * TILE
    height = int(math.ceil((y_south - y0) / TILE)) * TILE
    grid = np.zeros((height, width), np.uint8)
    log.info("zoom %d grid %dx%d (%.0f MB)", ZOOM, width, height, grid.nbytes / 1e6)
    pairs = [(l1b, geo) for l1b, geo in pairs if place(grid, x0, y0, l1b, geo) > 0]  # those over the lower 48

    for _ in range(GAP_FILL):
        empty = grid == 0
        if not empty.any():
            break
        # The mean of a pixel's filled neighbors (taking their maximum would spread warm pixels into a speckle).
        filled = (~empty).astype(np.float32)
        total = uniform_filter(grid.astype(np.float32), size=3) * 9
        count = uniform_filter(filled, size=3) * 9
        fill = np.where(count > 0.5, np.round(total / np.maximum(count, 1)), 0).astype(np.uint8)
        grid[empty] = fill[empty]

    rgb, alpha = palette(scale_file)
    zoom = ZOOM
    while zoom >= 3:
        n = write_tiles(grid, zoom, x0, y0, outdir, rgb, alpha)
        log.info("zoom %d: %d tiles", zoom, n)
        grid, x0, y0, zoom = downsample(grid), x0 // 2, y0 // 2, zoom - 1
    starts = sorted(os.path.basename(p[0]).split(".")[2] for p in pairs)  # HHMM
    json.dump({"kelvin_min": KELVIN_MIN, "kelvin_step": STEP, "max_zoom": ZOOM, "date": date,
               "first_granule": starts[0], "last_granule": starts[-1],
               "granules": [os.path.basename(p[0]) for p in pairs]},
              open(os.path.join(outdir, "info.json"), "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:8])
