#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Downloads the daily gridded active fire products (PLAN.md M7) over the contiguous US from NASA LAADS, with the
Earthdata token in ~/.config/ignopticon/earthdata-token (sent only in a request header).

Products (1 km sinusoidal tiles, a daily FireMask per cell: 0-2 not processed, 3 water, 4 cloud, 5 land, 6 unknown,
7-9 fire low/nominal/high, plus MaxFRP):
    MOD14A1, MYD14A1  MODIS Terra / Aqua, collection 6.1, 8 days per file (2000 / 2002 onward)
    VNP14A1           VIIRS S-NPP, collection 2, one day per file (2012 onward)
    VJ114A1           VIIRS NOAA-20, collection 2, one day per file (2018 onward)
Tiles h07-h13, v04-v06 cover the lower 48 (17 tiles exist there; others are ocean).

Usage: gridded_fire_download.py OUTDIR PRODUCT FIRST_YEAR LAST_YEAR [THREADS]
       e.g. gridded_fire_download.py data/raw/gridded MOD14A1 2000 2026 16
Writes OUTDIR/PRODUCT/YEAR/NAME, skipping files already there with the listed size.
"""
import concurrent.futures
import json
import logging
import os
import re
import sys
import time
import socket
import urllib.error
import urllib.request

# LAADS publishes IPv6 addresses this machine can't route: Python then fails with "Network is unreachable" on
# some requests. Resolve to IPv4 only.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *args, **kw: _getaddrinfo(host, port, socket.AF_INET, *args, **kw)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from laads_download import LAADS, TOKEN_FILE, opener  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

COLLECTION = {"MOD14A1": "61", "MYD14A1": "61", "VNP14A1": "5200", "VJ114A1": "5200"}
TILE = re.compile(r"\.h(\d\d)v(\d\d)\.")


def listing(path, attempt=0):
    try:
        with urllib.request.urlopen(LAADS + "/archive/allData/" + path + ".json", timeout=20) as r:
            return json.load(r)["content"]
    except Exception as err:  # LAADS drops ~1 connection in 8 and answers 5xx now and then: retry quickly
        if attempt < 8:
            time.sleep(1 + attempt)
            return listing(path, attempt + 1)
        log.warning("%s: %s", path, err)
        return []


ARCHIVE_SETS = ["5200", "5201", "5110"]  # where LAADS serves these files from (the listing's 61 answers 303)


def fetch(f, product, year, ydir, token):
    """One listed file, trying the archive sets in turn; quick timeouts and retries, since LAADS drops ~1
    connection in 8."""
    dst = os.path.join(ydir, f["name"])
    size = int(f["size"])
    if os.path.exists(dst) and os.path.getsize(dst) == size:
        return 0
    doy = f["downloadsLink"].rstrip("/").split("/")[-2]
    for attempt in range(10):
        for archive in ARCHIVE_SETS:
            url = "%s/archive/allData/%s/%s/%s/%s/%s" % (LAADS, archive, product, year, doy, f["name"])
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
            try:
                with opener.open(req, timeout=60) as r:
                    body = r.read()
            except urllib.error.HTTPError as err:
                if err.code in (301, 302, 303):
                    continue  # not in this archive set
                break  # server error: retry after a pause
            except Exception:
                break  # dropped connection or timeout: retry after a pause
            if len(body) != size:
                break
            with open(dst + ".part", "wb") as out:
                out.write(body)
            os.replace(dst + ".part", dst)
            return size
        time.sleep(1 + attempt)
    log.warning("%s: not fetched", f["name"])
    return 0


def main(outdir, product, first, last, threads="16"):
    token = open(TOKEN_FILE).read().strip()
    coll = COLLECTION[product]
    pool = concurrent.futures.ThreadPoolExecutor(int(threads))
    for year in range(int(first), int(last) + 1):
        days = [d["name"] for d in listing("%s/%s/%d" % (coll, product, year))]
        if not days:
            continue
        ydir = os.path.join(outdir, product, str(year))
        os.makedirs(ydir, exist_ok=True)
        files = []
        for day_files in pool.map(lambda d: listing("%s/%s/%d/%s" % (coll, product, year, d)), days):
            for f in day_files:
                m = TILE.search(f["name"])
                if m and 7 <= int(m[1]) <= 13 and 4 <= int(m[2]) <= 6:
                    files.append(f)
        start = time.time()
        log.info("%s %d: %d days listed, %d tiles to fetch", product, year, len(days), len(files))
        got = sum(pool.map(lambda f: fetch(f, product, year, ydir, token), files))
        log.info("%s %d: %d days, %d files, %.2f GB fetched in %.0f s", product, year, len(days), len(files),
                 got / 1e9, time.time() - start)


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        sys.exit(__doc__)
    main(*sys.argv[1:])
