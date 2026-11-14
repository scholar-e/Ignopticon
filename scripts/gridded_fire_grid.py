#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pyhdf", "h5py"]
# ///
"""Puts one product-year of the daily gridded active fire products (gridded_fire_download.py) on a 0.1 degree
lat/lon grid over the lower 48 (PLAN.md M7): for every day and cell, how many 1 km pixels the sensor saw as fire,
how many it saw clearly at all, and their summed MaxFRP. Detections per clear look then compare across sensors,
and cells never seen clearly are "not observed" rather than "no fire".

FireMask classes (MODIS C6.1 MOD14A1/MYD14A1 and VIIRS C2 VNP14A1/VJ114A1): 0-2 not processed, 3 water, 4 cloud,
5 land, 6 unknown, 7-9 fire (low/nominal/high confidence). Clear = 3, 5, 7, 8, 9. Fire = 7, 8, 9 (all confidences;
nominal and high only are kept separately as fire_nh).

Grid: cell (row, col) covers lat 50 - 0.1 (row + 1) .. 50 - 0.1 row and lon -125 + 0.1 col .. + 0.1, 260 x 590
cells. Sinusoidal pixels are assigned by their centre (MODIS sphere R = 6371007.181 m, tiles 1111950.5197 m,
1200 pixels).

Usage: gridded_fire_grid.py INDIR PRODUCT YEAR OUTDIR
       e.g. gridded_fire_grid.py data/raw/gridded MOD14A1 2020 data/harmonize/grid
Writes OUTDIR/PRODUCT_YEAR.npz: fire, fire_nh, clear (uint16) and frp (float32, MW), each days x 260 x 590,
plus day (day of year of each slice).
"""
import datetime
import glob
import logging
import os
import re
import sys

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

R = 6371007.181
TILE_M = 1111950.5197
N = 1200
PIX = TILE_M / N
LAT0, LON0, RES, NY, NX = 50.0, -125.0, 0.1, 260, 590
CLEAR = np.array([False, False, False, True, False, True, False, True, True, True] + [False] * 246)
FIRE = np.zeros(256, bool)
FIRE[7:10] = True
FIRE_NH = np.zeros(256, bool)
FIRE_NH[8:10] = True
_index = {}


def tile_index(h, v):
    """Flat grid-cell index of every pixel of tile (h, v), -1 outside the grid."""
    if (h, v) not in _index:
        j = np.arange(N) + 0.5
        x = -20015109.354 + h * TILE_M + j * PIX
        y = 10007554.677 - v * TILE_M - j * PIX
        lat = np.degrees(y / R)[:, None] * np.ones((1, N))
        lon = np.degrees(x[None, :] / (R * np.cos(np.radians(lat))))
        row = np.floor((LAT0 - lat) / RES).astype(int)
        col = np.floor((lon - LON0) / RES).astype(int)
        ok = (row >= 0) & (row < NY) & (col >= 0) & (col < NX) & (np.abs(lon) <= 180)
        _index[(h, v)] = np.where(ok, row * NX + col, -1).ravel()
    return _index[(h, v)]


def read_modis(path):
    """[(day of year, FireMask, MaxFRP MW)] for the 8 days of a MOD14A1/MYD14A1 file."""
    from pyhdf.SD import SD
    s = SD(path)
    mask, frp = s.select("FireMask").get(), s.select("MaxFRP").get() * 0.1
    start = int(re.search(r"\.A\d{4}(\d{3})\.", path)[1])
    return [(start + k, mask[k], frp[k]) for k in range(mask.shape[0])]


def read_viirs(path):
    """[(day of year, FireMask, MaxFRP MW)] for a VNP14A1/VJ114A1 file (one day)."""
    import h5py
    found = {}
    with h5py.File(path) as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset) and name.split("/")[-1] in ("FireMask", "MaxFRP"):
                found[name.split("/")[-1]] = obj
        f.visititems(visit)
        mask = found["FireMask"][:]
        frp_ds = found["MaxFRP"]
        frp = frp_ds[:].astype(np.float32) * float(np.ravel(frp_ds.attrs.get("scale_factor", [1.0]))[0])
    day = int(re.search(r"\.A\d{4}(\d{3})\.", path)[1])
    return [(day, mask, frp)]


def main(indir, product, year, outdir):
    year = int(year)
    files = sorted(glob.glob(os.path.join(indir, product, str(year), "*")))
    files = [f for f in files if not f.endswith(".part")]
    ndays = (datetime.date(year + 1, 1, 1) - datetime.date(year, 1, 1)).days
    shape = (ndays, NY * NX)
    fire = np.zeros(shape, np.uint16)
    fire_nh = np.zeros(shape, np.uint16)
    clear = np.zeros(shape, np.uint16)
    frp = np.zeros(shape, np.float32)
    reader = read_modis if product.startswith("M") else read_viirs
    for path in files:
        m = re.search(r"\.h(\d\d)v(\d\d)\.", path)
        idx = tile_index(int(m[1]), int(m[2]))
        ok = idx >= 0
        try:
            days = reader(path)
        except Exception as err:
            log.warning("%s: %s", os.path.basename(path), err)
            continue
        for doy, mask, mfrp in days:
            if not 1 <= doy <= ndays:
                continue
            d = doy - 1
            mk = mask.ravel()[ok]
            cells = idx[ok]
            size = NY * NX
            clear[d] += np.bincount(cells[CLEAR[mk]], minlength=size).astype(np.uint16)
            f = FIRE[mk]
            if f.any():
                fire[d] += np.bincount(cells[f], minlength=size).astype(np.uint16)
                fire_nh[d] += np.bincount(cells[FIRE_NH[mk]], minlength=size).astype(np.uint16)
                frp[d] += np.bincount(cells[f], weights=mfrp.ravel()[ok][f], minlength=size).astype(np.float32)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "%s_%d.npz" % (product, year))
    np.savez_compressed(out, fire=fire.reshape(ndays, NY, NX), fire_nh=fire_nh.reshape(ndays, NY, NX),
                        clear=clear.reshape(ndays, NY, NX), frp=frp.reshape(ndays, NY, NX),
                        day=np.arange(1, ndays + 1))
    log.info("%s %d: %d files, %d fire pixels (%d nominal/high), %.1f%% of cell-days with a clear look; wrote %s",
             product, year, len(files), int(fire.sum()), int(fire_nh.sum()), 100 * (clear > 0).mean(), out)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
