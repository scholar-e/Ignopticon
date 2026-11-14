#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Download past GFS 0.5° wind analyses from NOAA's open data on AWS (no key), for grib_to_earth_json.py.

NOMADS keeps only the last ~10 days; the AWS bucket keeps every run. Only the U/V wind messages are fetched, by
byte range from the file's .idx, into the same two files a NOMADS download made: 10 m wind, and the isobaric
levels earth's Height menu offers.

Usage: gfs_archive_download.py OUTDIR DATE HOUR     e.g. gfs_archive_download.py data/raw/gfs 2026-08-02 18
Output: OUTDIR/gfs_YYYYMMDD_HHz_f000_wind10m.grb2, OUTDIR/gfs_YYYYMMDD_HHz_f000_wind_isobaric.grb2
"""
import logging
import os
import sys
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
LEVELS = ["1000", "850", "700", "500", "250", "70", "10"]  # hPa


def ranges(index, wanted):
    """Byte ranges (start, end inclusive or None) of the messages whose "VAR:LEVEL" is in wanted."""
    lines = [line.split(":") for line in index.strip().splitlines()]
    result = []
    for i, parts in enumerate(lines):
        if parts[3] + ":" + parts[4] in wanted:
            end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
            result.append((int(parts[1]), end))
    return result


def fetch(url, byte_ranges, dst):
    with open(dst + ".part", "wb") as out:
        for start, end in byte_ranges:
            request = urllib.request.Request(url, headers={"Range": "bytes=%d-%s" % (start, "" if end is None else end)})
            with urllib.request.urlopen(request, timeout=300) as response:
                out.write(response.read())
    os.replace(dst + ".part", dst)
    log.info("wrote %s: %d messages, %.1f MB", dst, len(byte_ranges), os.path.getsize(dst) / 1e6)


def main(outdir, date, hour):
    ymd, hh = date.replace("-", ""), "%02d" % int(hour)
    url = "%s/gfs.%s/%s/atmos/gfs.t%sz.pgrb2.0p50.f000" % (BUCKET, ymd, hh, hh)
    with urllib.request.urlopen(url + ".idx", timeout=120) as response:
        index = response.read().decode()
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.join(outdir, "gfs_%s_%sz_f000_" % (ymd, hh))
    fetch(url, ranges(index, {"UGRD:10 m above ground", "VGRD:10 m above ground"}), stem + "wind10m.grb2")
    fetch(url, ranges(index, {v + ":" + p + " mb" for v in ("UGRD", "VGRD") for p in LEVELS}),
          stem + "wind_isobaric.grb2")


if __name__ == "__main__":
    main(*sys.argv[1:])
