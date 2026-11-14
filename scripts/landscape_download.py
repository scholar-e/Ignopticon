#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio"]
# ///
"""Downloads an ELMFIRE landscape (PLAN.md M0) for the area of a template raster: LANDFIRE LF2025 fuels and canopy
and USGS 3DEP elevation, through their public ArcGIS ImageServers (no key), resampled onto the template's grid
refined REFINE times each way, with slope and aspect computed from the elevation.

Each layer is fetched in the area's UTM zone at its native 30 m (LANDFIRE) / 10 m resampled to 30 m (3DEP), then
reprojected: nearest neighbour for the categorical and canopy layers, bilinear for elevation.

Usage: landscape_download.py TEMPLATE.tif REFINE OUT.tif
       e.g. landscape_download.py data/fires/widemouth-2026-08-02-state.tif 10 data/elmfire/widemouth/landscape.tif
Writes OUT.tif: Int16, 8 bands in ELMFIRE's order (scripts/elmfire_case.py LANDSCAPE_BANDS): elevation (m), slope
(deg), aspect (deg clockwise from north, the way the slope faces), FBFM40, canopy cover (%), canopy height (10*m),
canopy base height (10*m), canopy bulk density (100*kg/m3). The raw UTM downloads are kept beside it in
OUT_raw/ and reused.
"""
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform_bounds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LANDFIRE = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_{}_CONUS/ImageServer/exportImage"
DEP3 = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
FETCH_CELL = 30.0
TILE = 1024  # cells per request, each way
NODATA = -9999
# (band name, URL, pixel type, resampling)
LAYERS = [
    ("dem", DEP3, "F32", Resampling.bilinear),
    ("fbfm40", LANDFIRE.format("FBFM40"), "S16", Resampling.nearest),
    ("cc", LANDFIRE.format("CC"), "S16", Resampling.nearest),
    ("ch", LANDFIRE.format("CH"), "S16", Resampling.nearest),
    ("cbh", LANDFIRE.format("CBH"), "S16", Resampling.nearest),
    ("cbd", LANDFIRE.format("CBD"), "S16", Resampling.nearest),
]


def fetch_one(url, pixel_type, bbox, epsg, width, height, path):
    """One exportImage request as a GeoTIFF, retried: the services answer 504 to large or busy requests."""
    params = {
        "bbox": ",".join("%.3f" % v for v in bbox), "bboxSR": epsg, "imageSR": epsg, "size": "%d,%d" % (width, height),
        "format": "tiff", "pixelType": pixel_type, "noData": NODATA, "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_NearestNeighbor" if pixel_type == "S16" else "RSP_BilinearInterpolation", "f": "image",
    }
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "ignopticon"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            if body.startswith((b"II*\x00", b"MM\x00*")):
                with open(path, "wb") as f:
                    f.write(body)
                return
            log.warning("%s: not a GeoTIFF: %r", url, body[:200])
        except (urllib.error.URLError, TimeoutError) as err:
            log.warning("%s: %s (attempt %d)", os.path.basename(path), err, attempt + 1)
        time.sleep(5 * 2 ** attempt)
    raise RuntimeError("could not fetch " + path)


def fetch(url, pixel_type, bbox, epsg, width, height, path):
    """exportImage in tiles of at most TILE cells, mosaicked to PATH (kept and reused)."""
    if os.path.exists(path):
        return
    log.info("fetching %s", os.path.basename(path))
    nx, ny = math.ceil(width / TILE), math.ceil(height / TILE)
    data = np.full((height, width), NODATA, dtype="float32")
    profile = None
    for j in range(ny):
        for i in range(nx):
            c0, c1 = i * width // nx, (i + 1) * width // nx
            r0, r1 = j * height // ny, (j + 1) * height // ny
            box = [bbox[0] + c0 * FETCH_CELL, bbox[3] - r1 * FETCH_CELL, bbox[0] + c1 * FETCH_CELL,
                   bbox[3] - r0 * FETCH_CELL]
            part = "%s.%d_%d.part" % (path, j, i)
            fetch_one(url, pixel_type, box, epsg, c1 - c0, r1 - r0, part)
            with rasterio.open(part) as src:
                tile = src.read(1).astype("float32")
                if src.nodata is not None:
                    tile[tile == src.nodata] = NODATA
                profile = profile or src.profile
            data[r0:r1, c0:c1] = tile
            os.remove(part)
    profile.update(width=width, height=height, dtype="float32", nodata=NODATA, compress="deflate",
                   transform=Affine(FETCH_CELL, 0, bbox[0], 0, -FETCH_CELL, bbox[3]), crs=CRS.from_epsg(epsg))
    profile.pop("blockxsize", None), profile.pop("blockysize", None), profile.pop("tiled", None)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def slope_aspect(dem, cell):
    """Slope (deg) and aspect (deg clockwise from north, downslope direction) by Horn's method, on a north-up grid."""
    z = np.pad(dem.astype("float64"), 1, mode="edge")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * cell)   # towards east
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * cell)   # towards north
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360) % 360  # direction of steepest descent
    aspect[slope < 0.5] = 0
    return slope, aspect


def main(template, refine, out):
    refine = int(refine)
    with rasterio.open(template) as src:
        crs, bounds, t, shape = src.crs, src.bounds, src.transform, src.shape
    cell = t.a / refine
    dst_transform = Affine(cell, 0, t.c, 0, -cell, t.f)
    dst_shape = (shape[0] * refine, shape[1] * refine)
    lon0, lat0 = [v[0] for v in rasterio.warp.transform(crs, "EPSG:4326", [(bounds.left + bounds.right) / 2],
                                                         [(bounds.bottom + bounds.top) / 2])]
    epsg = (32600 if lat0 >= 0 else 32700) + int((lon0 + 180) // 6) + 1
    utm = transform_bounds(crs, CRS.from_epsg(epsg), *bounds, densify_pts=41)
    utm = [math.floor(utm[0] / FETCH_CELL - 2) * FETCH_CELL, math.floor(utm[1] / FETCH_CELL - 2) * FETCH_CELL,
           math.ceil(utm[2] / FETCH_CELL + 2) * FETCH_CELL, math.ceil(utm[3] / FETCH_CELL + 2) * FETCH_CELL]
    width, height = int(round((utm[2] - utm[0]) / FETCH_CELL)), int(round((utm[3] - utm[1]) / FETCH_CELL))
    log.info("%d x %d cells of %.1f m; fetching EPSG:%d %d x %d", dst_shape[1], dst_shape[0], cell, epsg, width, height)
    rawdir = os.path.splitext(out)[0] + "_raw"
    os.makedirs(rawdir, exist_ok=True)

    bands = {}
    for name, url, ptype, resampling in LAYERS:
        path = os.path.join(rawdir, name + ".tif")
        fetch(url, ptype, utm, epsg, width, height, path)
        with rasterio.open(path) as src:
            data = src.read(1).astype("float32")
            nodata = src.nodata
            if nodata is not None:
                data[data == nodata] = NODATA
            data[data < -1000] = NODATA
            dst = np.full(dst_shape, NODATA, dtype="float32")
            reproject(data, dst, src_transform=src.transform, src_crs=src.crs, src_nodata=NODATA,
                      dst_transform=dst_transform, dst_crs=crs, dst_nodata=NODATA, resampling=resampling)
        bands[name] = dst
        valid = dst != NODATA
        log.info("%-6s %5.1f%% valid, %g..%g", name, 100 * valid.mean(), dst[valid].min() if valid.any() else 0,
                 dst[valid].max() if valid.any() else 0)

    dem = bands["dem"]
    bands["slp"], bands["asp"] = slope_aspect(np.where(dem == NODATA, np.nan, dem), cell)
    for name in ("slp", "asp"):
        bands[name] = np.where(np.isfinite(bands[name]), bands[name], NODATA)
    order = ["dem", "slp", "asp", "fbfm40", "cc", "ch", "cbh", "cbd"]
    stack = np.stack([np.round(bands[n]) for n in order]).astype("int16")
    with rasterio.open(out, "w", driver="GTiff", width=dst_shape[1], height=dst_shape[0], count=8, dtype="int16",
                       crs=crs, transform=dst_transform, nodata=NODATA, compress="deflate", tiled=True) as dst:
        dst.write(stack)
        for i, n in enumerate(order, 1):
            dst.set_band_description(i, n)
    codes, counts = np.unique(stack[3], return_counts=True)
    top = sorted(zip(counts, codes), reverse=True)[:8]
    log.info("wrote %s; commonest fuel models: %s", out, ", ".join("%d (%.0f%%)" % (c, 100 * n / stack[3].size)
                                                                  for n, c in top))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
