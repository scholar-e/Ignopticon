#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "h5py"]
# ///
"""Daily weather and wind on the harmonization grid (PLAN.md M7), from gridMET (University of Idaho; daily, 1/24
degree, lower 48, 1979 on; https://www.climatologylab.org/gridmet.html), averaged into the 0.1 degree cells of
gridded_fire_grid.py (lat 24-50, lon -125 to -66, 260 x 590).

Variables (gridMET file prefix -> output):
    vs, th   wind speed (m/s, 10 m) and the direction it blows from -> wind (mean speed), u, v (mean east and north
             components of the wind, m/s: averaged as vectors so opposing winds cancel)
    vpd      mean vapour pressure deficit (kPa) -> vpd
    erc      energy release component (NFDRS fuel model G) -> erc
    fm100    100-hour dead fuel moisture (%) -> fm100
    pr       precipitation (mm/day) -> pr
Cells with no gridMET data (ocean, Canada, Mexico) are NaN.

Usage: gridmet_grid.py GRIDMET_DIR YEAR OUT_DIR
       e.g. gridmet_grid.py data/raw/gridmet 2020 data/harmonize/weather
Writes OUT_DIR/weather_YEAR.npz with the outputs above (float16, days x 260 x 590).
"""
import logging
import os
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
DECIMALS = {"wind": 2, "u": 2, "v": 2, "vpd": 2, "erc": 1, "fm100": 1, "pr": 1}


def read(path):
    with h5py.File(path) as f:
        name = [k for k in f if f[k].ndim == 3][0]
        d = f[name]
        raw = d[:]
        scale = float(np.ravel(d.attrs.get("scale_factor", [1.0]))[0])
        offset = float(np.ravel(d.attrs.get("add_offset", [0.0]))[0])
        fill = np.ravel(d.attrs.get("_FillValue", [None]))[0]
        lat, lon = f["lat"][:], f["lon"][:]
    data = raw.astype(np.float32) * scale + offset
    if fill is not None:
        data[raw == fill] = np.nan
    return data, lat, lon


def cell_index(lat, lon):
    row = np.floor((LAT0 - lat) / CELL).astype(int)
    col = np.floor((lon - LON0) / CELL).astype(int)
    rr, cc = np.meshgrid(row, col, indexing="ij")
    ok = (rr >= 0) & (rr < NY) & (cc >= 0) & (cc < NX)
    return np.where(ok, rr * NX + cc, -1).ravel()


def block_mean(data, idx):
    """Per-day mean of DATA (days x lat x lon) over each grid cell; NaN where no valid samples."""
    days = data.shape[0]
    out = np.full((days, NY * NX), np.nan, np.float32)
    ok = idx >= 0
    for d in range(days):
        v = data[d].ravel()[ok]
        i = idx[ok]
        good = np.isfinite(v)
        s = np.bincount(i[good], weights=v[good], minlength=NY * NX)
        n = np.bincount(i[good], minlength=NY * NX)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[d] = np.where(n > 0, s / n, np.nan)
    return out.reshape(days, NY, NX)


def main(gridmet_dir, year, out_dir):
    path = lambda v: os.path.join(gridmet_dir, "%s_%s.nc" % (v, year))
    out = {}
    vs, lat, lon = read(path("vs"))
    th, _, _ = read(path("th"))
    idx = cell_index(lat, lon)
    rad = np.radians(th)
    out["wind"] = block_mean(vs, idx)
    out["u"] = block_mean(-vs * np.sin(rad), idx)  # wind blowing FROM th: its velocity points the other way
    out["v"] = block_mean(-vs * np.cos(rad), idx)
    del vs, th, rad
    for var in ("vpd", "erc", "fm100", "pr"):
        data, _, _ = read(path(var))  # a missing input is an error: never write a year with arrays left out
        if data.shape[0] != out["wind"].shape[0]:
            sys.exit("%s has %d days, vs has %d" % (path(var), data.shape[0], out["wind"].shape[0]))
        out[var] = block_mean(data, idx)
        del data
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "weather_%s.npz" % year)
    # Rounded to the source precision or finer before float16: the extra digits of a mean are noise that only
    # stops the file compressing. Written under another name, then renamed, so readers never see a partial file.
    tmp = os.path.join(out_dir, ".weather_%s.tmp.npz" % year)
    np.savez_compressed(tmp, **{k: np.round(v, DECIMALS[k]).astype(np.float16) for k, v in out.items()})
    os.replace(tmp, dst)
    log.info("%s: %s; wrote %s", year, ", ".join("%s mean %.2f" % (k, np.nanmean(v)) for k, v in out.items()), dst)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
