#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio"]
# ///
"""Fuel features for the harmonization grid (PLAN.md M7): per 0.1 degree cell over the lower 48 (lat 24-50, lon -125
to -66, 260 x 590 cells, north up, row 0 at lat 50, as gridded_fire_grid.py), the share of the cell in each Scott &
Burgan FBFM40 group, the dominant FBFM40 code, and mean LANDFIRE canopy cover, height, base height and bulk density.

Source: LANDFIRE LF2025 CONUS layers FBFM40, CC, CH, CBH, CBD through their public ArcGIS ImageServers (no key):
https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_{LAYER}_CONUS/ImageServer/exportImage .
Each layer is sampled nearest-neighbour on a 0.004 degree EPSG:4326 lattice (25 x 25 = 625 samples per cell, ~350-450
m apart), fetched in 2 x 2 degree tiles (500 x 500 px, under a second each) by a thread pool with retries. The
server answers these coarse requests from its pyramids; on a test half-degree the class shares agreed with a
near-native (~30 m) request to within 2.5 points. Raw tiles are cached in TILEDIR/LAYER/ and reused.

Service scalings (checked against the service metadata, max values CC 95, CH 510, CBH 100, CBD 45): CC is %, CH and
CBH are metres x 10, CBD is kg/m3 x 100; they are divided out here. -9999 is no data (outside LANDFIRE CONUS).

Usage: fuel_grid.py TILEDIR OUT.npz
       e.g. fuel_grid.py data/raw/landfire_tiles data/harmonize/fuel.npz
Writes OUT.npz, each 260 x 590 float32 unless noted; NaN where the cell has no valid LANDFIRE sample:
  frac_nb, frac_gr, frac_gs, frac_sh, frac_tu, frac_tl, frac_sb: share of the cell's valid samples in non-burnable
      (91-99), grass (101-109), grass-shrub (121-124), shrub (141-149), timber-understory (161-165), timber litter
      (181-189), slash-blowdown (201-204)
  fbfm40_mode: int16, commonest FBFM40 code in the cell (-1 where none)
  cc (%), ch (m), cbh (m), cbd (kg/m3): cell means over valid samples (0 for non-forest samples, so these mix
      cover and stand structure)
  valid: share of the cell's 625 samples with a valid FBFM40 code
"""
import concurrent.futures
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import rasterio

# Some networks here can't route IPv6: resolve to IPv4 only (as gridded_fire_download.py).
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *args, **kw: _getaddrinfo(host, port, socket.AF_INET, *args, **kw)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LAT0, LON0, NY, NX, CELL = 50.0, -125.0, 260, 590, 0.1
SUB = 25                      # samples per cell side
STEP = CELL / SUB             # 0.004 degree
TILE_CELLS = 20               # 2 degree tiles
URL = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_{}_CONUS/ImageServer/exportImage"
NODATA = -9999
THREADS = 12
SCALE = {"FBFM40": 1, "CC": 1, "CH": 10, "CBH": 10, "CBD": 100}
GROUPS = [("nb", 91, 99), ("gr", 101, 109), ("gs", 121, 124), ("sh", 141, 149), ("tu", 161, 165),
          ("tl", 181, 189), ("sb", 201, 204)]


def tiles():
    """(row0, col0, nrows, ncols) in cells for every tile of the grid."""
    return [(r, c, min(TILE_CELLS, NY - r), min(TILE_CELLS, NX - c))
            for r in range(0, NY, TILE_CELLS) for c in range(0, NX, TILE_CELLS)]


def fetch_tile(layer, r0, c0, nr, nc, path):
    """One exportImage request as a GeoTIFF, retried (ArcGIS ImageServers answer 504 when large or busy)."""
    if os.path.exists(path):
        return False
    w, s = LON0 + c0 * CELL, LAT0 - (r0 + nr) * CELL
    e, n = LON0 + (c0 + nc) * CELL, LAT0 - r0 * CELL
    params = {
        "bbox": "%.4f,%.4f,%.4f,%.4f" % (w, s, e, n), "bboxSR": 4326, "imageSR": 4326,
        "size": "%d,%d" % (nc * SUB, nr * SUB), "format": "tiff", "pixelType": "S16", "noData": NODATA,
        "noDataInterpretation": "esriNoDataMatchAny", "interpolation": "RSP_NearestNeighbor", "f": "image",
    }
    req = urllib.request.Request(URL.format(layer) + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "ignopticon"})
    for attempt in range(7):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            if body.startswith((b"II*\x00", b"MM\x00*")):
                with open(path + ".tmp", "wb") as f:
                    f.write(body)
                os.replace(path + ".tmp", path)
                return True
            log.warning("%s: not a GeoTIFF: %r", os.path.basename(path), body[:200])
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            log.warning("%s %s: %s (attempt %d)", layer, os.path.basename(path), err, attempt + 1)
        time.sleep(5 * 2 ** attempt)
    raise RuntimeError("could not fetch " + path)


def layer_grid(layer, tiledir):
    """Fetch (or reuse) every tile of LAYER and mosaic them into one (NY*SUB, NX*SUB) int16 sample array."""
    d = os.path.join(tiledir, layer)
    os.makedirs(d, exist_ok=True)
    jobs = [(t, os.path.join(d, "r%03d_c%03d.tif" % t[:2])) for t in tiles()]
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(THREADS) as pool:
        new = sum(pool.map(lambda j: fetch_tile(layer, *j[0], j[1]), jobs))
    log.info("%s: %d tiles (%d fetched) in %.0f s", layer, len(jobs), new, time.time() - t0)
    out = np.full((NY * SUB, NX * SUB), NODATA, dtype=np.int16)
    for (r0, c0, nr, nc), path in jobs:
        with rasterio.open(path) as src:
            a = src.read(1)
            t = src.transform
            nd = src.nodata
        if a.shape != (nr * SUB, nc * SUB) or abs(t.c - (LON0 + c0 * CELL)) > 1e-6 or abs(t.f - (LAT0 - r0 * CELL)) > 1e-6:
            raise RuntimeError("%s: unexpected shape %s or origin (%f, %f)" % (path, a.shape, t.c, t.f))
        if nd is not None and nd != NODATA:
            a = np.where(a == nd, NODATA, a)
        out[r0 * SUB:(r0 + nr) * SUB, c0 * SUB:(c0 + nc) * SUB] = a
    return out


def blocks(a):
    """(NY*SUB, NX*SUB) samples -> (NY, NX, SUB*SUB)."""
    return a.reshape(NY, SUB, NX, SUB).transpose(0, 2, 1, 3).reshape(NY, NX, SUB * SUB)


def main(tiledir, out):
    t_start = time.time()
    res = {}

    f = blocks(layer_grid("FBFM40", tiledir))
    ok = (f >= 91) & (f <= 204)
    n_ok = ok.sum(axis=2)
    valid = n_ok / (SUB * SUB)
    has = n_ok > 0
    # counts of each code per cell: bincount over (cell index, code - 91)
    K = 204 - 91 + 1
    cell = np.broadcast_to(np.arange(NY * NX).reshape(NY, NX, 1), f.shape)
    counts = np.bincount((cell[ok].astype(np.int64) * K + (f[ok] - 91)), minlength=NY * NX * K).reshape(NY, NX, K)
    del cell, f, ok
    with np.errstate(invalid="ignore", divide="ignore"):
        for name, lo, hi in GROUPS:
            frac = counts[:, :, lo - 91:hi - 91 + 1].sum(axis=2) / n_ok
            res["frac_" + name] = np.where(has, frac, np.nan).astype(np.float32)
    res["fbfm40_mode"] = np.where(has, counts.argmax(axis=2) + 91, -1).astype(np.int16)
    unknown = n_ok.sum() - sum(counts[:, :, lo - 91:hi - 91 + 1].sum() for _, lo, hi in GROUPS)
    if unknown:
        log.warning("%d valid samples have codes outside the seven groups", unknown)

    for layer in ("CC", "CH", "CBH", "CBD"):
        a = blocks(layer_grid(layer, tiledir))
        good = a >= 0
        n = good.sum(axis=2)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(good, a, 0).sum(axis=2, dtype=np.float64) / n / SCALE[layer]
        res[layer.lower()] = np.where((n > 0) & has, mean, np.nan).astype(np.float32)
        log.info("%s: %.1f%% of samples valid, cell means %.2f..%.2f", layer, 100 * good.mean(),
                 np.nanmin(res[layer.lower()]), np.nanmax(res[layer.lower()]))
        del a, good

    res["valid"] = np.where(has, valid, np.nan).astype(np.float32)
    np.savez_compressed(out, **res)
    w = np.where(has, valid * np.cos(np.radians(LAT0 - CELL * (np.arange(NY) + 0.5)))[:, None], 0)
    shares = ", ".join("%s %.1f%%" % (name.upper(), 100 * np.nansum(res["frac_" + name] * w) / w.sum())
                       for name, _, _ in GROUPS)
    log.info("wrote %s in %.0f s: %.1f%% of cells with LANDFIRE data; area shares %s", out, time.time() - t_start,
             100 * has.mean(), shares)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
