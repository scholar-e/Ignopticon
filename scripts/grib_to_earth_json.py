#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pygrib", "numpy"]
# ///
"""Convert GFS U/V wind GRIB2 files into the JSON layout earth reads (the grib2json format).

Usage: grib_to_earth_json.py IN.grb2 OUT.json             10 m wind
       grib_to_earth_json.py IN.grb2 OUT_PATTERN.json HPA  wind on that isobaric level; OUT_PATTERN may hold {hpa},
                                                           and HPA may be a comma separated list, e.g. 1000,850,700
"""
import json
import logging
import sys

import numpy as np
import pygrib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

PARAMETER_NUMBERS = {"10u": 2, "10v": 3, "u": 2, "v": 3}  # GRIB2 momentum category: 2 = U, 3 = V


def record(msg):
    lats, lons = msg.latlons()
    values = np.round(msg.values.astype(float), 2)
    ny, nx = values.shape
    if lats[0, 0] < lats[-1, 0]:  # earth expects rows from north to south
        lats, values = lats[::-1], values[::-1]
    dx = float(lons[0, 1] - lons[0, 0])
    dy = float(lats[0, 0] - lats[1, 0])
    ref = msg.analDate
    header = {
        "discipline": 0,
        "center": 7,
        "centerName": "US National Weather Service - NCEP(WMC)",
        "refTime": ref.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "forecastTime": int(msg.forecastTime),
        "parameterCategory": 2,
        "parameterNumber": PARAMETER_NUMBERS[msg.shortName],
        "parameterUnit": "m.s-1",
        # 103 = height above ground (m), 100 = isobaric surface (Pa)
        "surface1Type": 100 if msg.typeOfLevel == "isobaricInhPa" else 103,
        "surface1Value": msg.level * 100 if msg.typeOfLevel == "isobaricInhPa" else 10,
        "nx": nx,
        "ny": ny,
        "lo1": float(lons[0, 0]),
        "la1": float(lats[0, 0]),
        "lo2": float(lons[0, -1]),
        "la2": float(lats[-1, 0]),
        "dx": dx,
        "dy": dy,
        "numberPoints": nx * ny,
    }
    log.info("%s ref=%s +%sh grid %dx%d dx=%.2f", msg.shortName, header["refTime"], header["forecastTime"], nx, ny, dx)
    return {"header": header, "data": values.ravel().tolist()}


def write(dst, u, v):
    with open(dst, "w") as f:
        json.dump([record(u), record(v)], f, separators=(",", ":"))
    log.info("wrote %s", dst)


def main(src, dst, levels=None):
    with pygrib.open(src) as grbs:
        if levels is None:
            msgs = {m.shortName: m for m in grbs if m.shortName in ("10u", "10v")}
            return write(dst, msgs["10u"], msgs["10v"])
        msgs = {(m.shortName, m.level): m for m in grbs
                if m.shortName in ("u", "v") and m.typeOfLevel == "isobaricInhPa"}
    for hpa in (int(level) for level in levels.split(",")):
        write(dst.format(hpa=hpa), msgs[("u", hpa)], msgs[("v", hpa)])


if __name__ == "__main__":
    main(*sys.argv[1:4])
