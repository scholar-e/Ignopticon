#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "h5py"]
# ///
"""Elevation features for the harmonization grid (PLAN.md M7): mean and standard deviation (terrain roughness) of
elevation in every 0.1 degree cell over the lower 48 (lat 24-50, lon -125 to -66, 260 x 590 cells, as
gridded_fire_grid.py).

Source: NOAA ETOPO 2022, 60 arc-second surface elevation (one global file, ~1.8 km samples, 6 x 6 per cell):
https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/60s_surface_elev_netcdf/
ETOPO_2022_v1_60s_N90W180_surface.nc . (3DEP's ImageServer answers 504 to requests larger than ~1 degree, ~20 s
each, too slow for the whole country at this resolution.) Seafloor below -100 m is NaN (missing to the trees);
Death Valley's -86 m stays.

Usage: elevation_grid.py ETOPO.nc OUT.npz
       e.g. elevation_grid.py data/raw/dem/etopo2022_60s.nc data/harmonize/elevation.npz
Writes OUT.npz: elev (m), elev_sd (m), each 260 x 590.
"""
import logging
import sys

import h5py
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LAT0, LON0, NY, NX, CELL = 50.0, -125.0, 260, 590, 0.1


def main(etopo, out):
    with h5py.File(etopo) as f:
        lat, lon = f["lat"][:], f["lon"][:]
        step = abs(lat[1] - lat[0])
        sub = int(round(CELL / step))
        rows = np.nonzero((lat > LAT0 - NY * CELL) & (lat < LAT0))[0]
        cols = np.nonzero((lon > LON0) & (lon < LON0 + NX * CELL))[0]
        z = f["z"][rows.min():rows.max() + 1, cols.min():cols.max() + 1].astype(np.float32)
        lat = lat[rows.min():rows.max() + 1]
    if lat[0] < lat[-1]:  # south to north: flip to north-up like the grid
        z = z[::-1]
    z = z[:NY * sub, :NX * sub]
    z[z < -100] = np.nan
    blocks = z.reshape(NY, sub, NX, sub)
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore", RuntimeWarning)
        elev = np.nanmean(blocks, axis=(1, 3))
        sd = np.nanstd(blocks, axis=(1, 3))
    np.savez_compressed(out, elev=elev.astype(np.float32), elev_sd=sd.astype(np.float32))
    log.info("wrote %s: %d samples per cell side, %.0f%% of cells with elevation, %.0f..%.0f m, roughness up to %.0f m",
             out, sub, 100 * np.isfinite(elev).mean(), np.nanmin(elev), np.nanmax(elev), np.nanmax(sd))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
