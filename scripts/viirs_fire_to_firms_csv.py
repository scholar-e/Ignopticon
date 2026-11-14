#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["netCDF4", "numpy"]
# ///
"""Convert VIIRS 375 m active fire granules (VNP14IMG, VJ114IMG, VJ214IMG from LAADS) into FIRMS-style CSV.

FIRMS's keyless downloads only reach back about a week, but LAADS keeps every granule (with the Earthdata token,
see laads_download.py --all). The CSV has the columns firms_to_earth_json.py and firms_heat_grid.py read, for
detections inside the lower 48 or Hawaii, like FIRMS's "USA contiguous and Hawaii" files.

Usage: viirs_fire_to_firms_csv.py OUT.csv IN.nc [IN.nc ...]

The granules have no pixel sizes, so scan and track (km) come from the view zenith angle: track grows with the
slant range, scan with the slant range over the cosine of the zenith angle, and VIIRS aggregates 3, 2 or 1 raw
samples across the scan in its inner, middle and outer zones. This matches FIRMS's 0.32-0.8 km to within a few
hundredths.
"""
import csv
import logging
import math
import sys

import netCDF4
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

SATELLITES = {"NPP": "N", "JPSS1": "N20", "JPSS2": "N21"}
CONFIDENCE = {7: "low", 8: "nominal", 9: "high"}
REGIONS = [(-125.0, 24.0, -66.0, 50.0), (-161.0, 18.5, -154.0, 22.5)]  # (W, S, E, N): lower 48, Hawaii
EARTH_RADIUS = 6371.0     # km
ORBIT_HEIGHT = 829.0      # km, S-NPP and NOAA-20/21
NADIR_PIXEL = 0.375       # km
ZONES = [(31.72, 3), (44.86, 2), (90.0, 1)]  # scan angle limit (degrees), raw samples aggregated
FIELDS = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time", "satellite",
          "confidence", "version", "bright_ti5", "frp", "daynight"]


def pixel_size(view_zenith):
    """(scan, track) in km of the 375 m pixel seen at view_zenith degrees."""
    zenith = math.radians(view_zenith)
    orbit = EARTH_RADIUS + ORBIT_HEIGHT
    scan_angle = math.asin(EARTH_RADIUS / orbit * math.sin(zenith))
    arc = zenith - scan_angle
    slant = math.sqrt(EARTH_RADIUS ** 2 + orbit ** 2 - 2 * EARTH_RADIUS * orbit * math.cos(arc))
    samples = next(k for limit, k in ZONES if math.degrees(scan_angle) <= limit)
    scan = slant / math.cos(zenith) * samples / 3 * NADIR_PIXEL / ORBIT_HEIGHT
    track = slant / ORBIT_HEIGHT * NADIR_PIXEL
    return round(scan, 2), round(track, 2)


def inside(lon, lat):
    return any(w <= lon <= e and s <= lat <= n for w, s, e, n in REGIONS)


def rows(path):
    d = netCDF4.Dataset(path)
    satellite = SATELLITES[d.getncattr("Satellite")]
    start = d.getncattr("StartTime")  # "2026-08-02 16:06:00.000"
    date, hhmm = start[:10], start[11:13] + start[14:16]
    if d.variables["FP_latitude"].shape[0] == 0:
        return []
    v = {k: np.asarray(d.variables[k][:]) for k in ("FP_latitude", "FP_longitude", "FP_T4", "FP_T5", "FP_power",
                                                     "FP_confidence", "FP_day", "FP_ViewZenAng")}
    result = []
    for i in range(len(v["FP_latitude"])):
        lat, lon = float(v["FP_latitude"][i]), float(v["FP_longitude"][i])
        if not inside(lon, lat):
            continue
        scan, track = pixel_size(float(v["FP_ViewZenAng"][i]))
        result.append({
            "latitude": round(lat, 5), "longitude": round(lon, 5), "bright_ti4": round(float(v["FP_T4"][i]), 2),
            "scan": scan, "track": track, "acq_date": date, "acq_time": hhmm, "satellite": satellite,
            "confidence": CONFIDENCE.get(int(v["FP_confidence"][i]), "nominal"), "version": "2.0",
            "bright_ti5": round(float(v["FP_T5"][i]), 2), "frp": round(float(v["FP_power"][i]), 2),
            "daynight": "D" if int(v["FP_day"][i]) == 1 else "N",
        })
    return result


def main(dst, *srcs):
    detections = []
    for src in sorted(srcs):
        found = rows(src)
        detections += found
        if found:
            log.info("%s: %d detections", src, len(found))
    with open(dst, "w", newline="") as out:
        writer = csv.DictWriter(out, FIELDS)
        writer.writeheader()
        writer.writerows(detections)
    counts = {}
    for row in detections:
        counts[row["satellite"]] = counts.get(row["satellite"], 0) + 1
    log.info("wrote %s: %d detections from %d granules, %s", dst, len(detections), len(srcs), counts)


if __name__ == "__main__":
    main(*sys.argv[1:])
