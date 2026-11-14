#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Convert FIRMS VIIRS/MODIS hotspot CSVs into a compact JSON file that earth draws as points.

Usage: firms_to_earth_json.py [--hours=N] OUT.json IN.csv [IN.csv ...]
       --hours=N keeps only detections within N hours of the newest one: FIRMS's "24h" files can reach back further.

Output: {"start": ISO, "end": ISO, "fields": [...],
         "sensors": {"N": [[lon, lat, frp, epoch_s, conf, scan_km, track_km], ...], ...}}
Detections are grouped by satellite code (N = S-NPP, N20 = NOAA-20, N21 = NOAA-21, Terra/Aqua for MODIS).
"""
import csv
import json
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

FIELDS = ["lon", "lat", "frp", "time", "confidence", "scan", "track"]  # scan/track: pixel size in km


def acquired(row):
    hhmm = row["acq_time"].zfill(4)
    return datetime.strptime(row["acq_date"] + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)


def main(dst, *srcs, hours=None):
    sensors, times = {}, []
    rows_by_src = {}
    for src in srcs:
        with open(src, newline="") as f:
            rows_by_src[src] = list(csv.DictReader(f))
    newest = max(acquired(row) for rows in rows_by_src.values() for row in rows)
    for src, rows in rows_by_src.items():
        if hours is not None:
            rows = [row for row in rows if (newest - acquired(row)).total_seconds() <= hours * 3600]
        for row in rows:
            t = acquired(row)
            times.append(t)
            sensors.setdefault(row["satellite"], []).append([
                round(float(row["longitude"]), 4),
                round(float(row["latitude"]), 4),
                float(row["frp"]),
                int(t.timestamp()),
                row["confidence"],
                float(row["scan"]),
                float(row["track"]),
            ])
        log.info("%s: %d detections", src, len(rows))
    out = {
        "start": min(times).strftime("%Y-%m-%dT%H:%MZ"),
        "end": max(times).strftime("%Y-%m-%dT%H:%MZ"),
        "fields": FIELDS,
        "sensors": sensors,
    }
    with open(dst, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    log.info("wrote %s: %s, %s to %s", dst, {k: len(v) for k, v in sensors.items()}, out["start"], out["end"])


if __name__ == "__main__":
    args = sys.argv[1:]
    hours = float(args.pop(0).split("=")[1]) if args and args[0].startswith("--hours=") else None
    main(*args, hours=hours)
