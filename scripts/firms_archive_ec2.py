#!/usr/bin/env python3
"""Download the NASA FIRMS yearly United States archives (no key) and split them into per-day files for the site.

Runs on the EC2 instance (stdlib only). Sources: https://firms.modaps.eosdis.nasa.gov/data/country/{sensor}/{year}/
{sensor}_{year}_United_States.csv for sensor in modis (2000+, 1 km), viirs-snpp (2012+, 375 m), viirs-noaa20 (2018+),
viirs-noaa21 (2024+). FIRMS publishes complete years only; the current year comes from near-real-time feeds instead.

Writes OUT/{sensor}/{year}.csv.gz (the full year, as published) and OUT/{sensor}/daily/{YYYY-MM-DD}.json:
{"sensor", "date", "columns": [...], "rows": [[lat, lon, frp, confidence, acq_time, daynight, scan, track], ...]}.
OUT/index.json lists sensors, years and days. Skips years already done. Logs to stdout.

Usage: firms_archive_ec2.py OUT [FIRST_YEAR] [LAST_YEAR]
"""
import csv
import gzip
import io
import json
import logging
import os
import sys
import time
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

BASE = "https://firms.modaps.eosdis.nasa.gov/data/country/%s/%d/%s_%d_United_States.csv"
SENSORS = {"modis": 2000, "viirs-snpp": 2012, "viirs-noaa20": 2018, "viirs-noaa21": 2024}
COLS = ["latitude", "longitude", "frp", "confidence", "acq_time", "daynight", "scan", "track"]


def fetch(url, attempts=5):
    for i in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ignopticon"}),
                                        timeout=300) as r:
                return r.read()
        except Exception as err:
            if getattr(err, "code", None) == 404:
                return None
            log.warning("%s: %s (attempt %d)", url, err, i + 1)
            time.sleep(5 * (i + 1))
    return None


def number(v):
    try:
        f = float(v)
        return round(f, 4) if abs(f) < 1000 else round(f, 1)
    except ValueError:
        return v


def main(out, first="2000", last="2025"):
    index = {}
    for sensor, start in SENSORS.items():
        for year in range(max(int(first), start), int(last) + 1):
            d = os.path.join(out, sensor)
            daily = os.path.join(d, "daily")
            os.makedirs(daily, exist_ok=True)
            done = os.path.join(d, "%d.csv.gz" % year)
            if not os.path.exists(done):
                body = fetch(BASE % (sensor, year, sensor, year))
                if not body:
                    log.info("%s %d: not available", sensor, year)
                    continue
                with gzip.open(done + ".part", "wb") as f:
                    f.write(body)
                os.replace(done + ".part", done)
                log.info("%s %d: %.1f MB", sensor, year, len(body) / 1e6)
            with gzip.open(done, "rt", newline="") as f:
                days = {}
                for row in csv.DictReader(f):
                    days.setdefault(row["acq_date"], []).append([number(row.get(c, "")) for c in COLS])
            for date, rows in days.items():
                p = os.path.join(daily, date + ".json")
                if not os.path.exists(p):
                    with open(p, "w") as g:
                        json.dump({"sensor": sensor, "date": date, "columns": COLS, "rows": rows}, g,
                                  separators=(",", ":"))
            index.setdefault(sensor, {})[str(year)] = sorted(days)
            with open(os.path.join(out, "index.json"), "w") as g:
                json.dump(index, g)
            log.info("%s %d: %d days, %d detections", sensor, year, len(days), sum(len(v) for v in days.values()))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
