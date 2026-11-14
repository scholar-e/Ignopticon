#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Download VIIRS granules from NASA LAADS with the Earthdata token in ~/.config/ignopticon/earthdata-token.

Lists the PRODUCT's daytime granules (day and night with --all) over BBOX on DATE, keeps those starting between FROM and TO (UTC, HHMM), and
downloads them to OUTDIR, skipping files already there. The token is only ever sent in a request header.

Usage: laads_download.py [--all] OUTDIR PRODUCT DATE FROM TO [BBOX]
       e.g. laads_download.py data/raw/viirs_l1b VJ202IMG 2026-10-29 1730 2110 -125,24,-66,50
            laads_download.py --all data/raw/viirs_fire VJ114IMG 2026-08-02 0000 2359   (active fires, all passes)
"""
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LAADS = "https://ladsweb.modaps.eosdis.nasa.gov"
TOKEN_FILE = os.path.expanduser("~/.config/ignopticon/earthdata-token")
# The listing's archive set is not always where the file is; a wrong one answers 303 (to a licence page).
COLLECTIONS = ["5201", "5110", "5200"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)


def granules(product, date, bbox, night=False):
    w, s, e, n = bbox.split(",")
    region = "[BBOX]W%s N%s E%s S%s" % (w, n, e, s)
    params = {"products": product, "temporalRanges": date, "regions": region}
    if not night:
        params["illuminations"] = "D"
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(LAADS + "/api/v2/content/details?" + query, timeout=120) as response:
        return json.load(response)["content"]


def download(name, product, year, doy, dst, token, attempt=0):
    for collection in COLLECTIONS:
        url = "%s/archive/allData/%s/%s/%s/%s/%s" % (LAADS, collection, product, year, doy, name)
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with opener.open(request, timeout=600) as response, open(dst + ".part", "wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
        except urllib.error.HTTPError as err:
            if err.code in (301, 302, 303):
                continue
            if err.code >= 500 and attempt < 4:  # LAADS answers 502 now and then
                log.warning("%s: HTTP %d, retrying", name, err.code)
                time.sleep(10 * (attempt + 1))
                return download(name, product, year, doy, dst, token, attempt + 1)
            raise
        os.replace(dst + ".part", dst)
        return collection
    raise RuntimeError("%s not found in collections %s" % (name, COLLECTIONS))


def main(outdir, product, date, start, end, bbox="-125,24,-66,50", night=False):
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    os.makedirs(outdir, exist_ok=True)
    year, doy = date[:4], "%03d" % (__import__("datetime").date.fromisoformat(date).timetuple().tm_yday)
    chosen = [g for g in granules(product, date, bbox, night) if start <= g["name"].split(".")[2] <= end]
    log.info("%s %s %s-%s: %d granules, %.2f GB", product, date, start, end, len(chosen),
             sum(int(g["size"]) for g in chosen) / 1e9)
    for g in sorted(chosen, key=lambda g: g["name"]):
        dst = os.path.join(outdir, g["name"])
        if os.path.exists(dst) and os.path.getsize(dst) == int(g["size"]):
            log.info("have %s", g["name"])
            continue
        collection = download(g["name"], product, year, doy, dst, token)
        log.info("got %s (collection %s, %.0f MB)", g["name"], collection, os.path.getsize(dst) / 1e6)


if __name__ == "__main__":
    args = sys.argv[1:]
    night = bool(args) and args[0] == "--all"
    main(*args[night:], night=night)
