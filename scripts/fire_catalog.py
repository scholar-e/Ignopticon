#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy"]
# ///
"""Catalog of large multi-day US fire events for training ELMFIRE corrections (data/training/NOTES.md).

Events are found on the 0.1 degree daily grid (docs/DATASETS.md 2a) from VIIRS S-NPP fire cell-days (VNP14A1
fire > 0), by space-time clustering: two fire cell-days belong to one event if their cells touch (8 neighbours, or
the same cell) and their days are at most GAP_DAYS + 1 apart (so up to GAP_DAYS days without fire, e.g. cloud or
smoke, don't split an event). Before clustering, persistent heat is removed: cells with fire in >= PERSIST_WEEKS
distinct weeks over the catalog's years, or >= PERSIST_WEEKS_YEAR weeks of one year (gas flares, industry, fields
burned every season). Counting years instead removed real fire country (Sonoma/Napa burns in 6-10 of 12 years).

Kept: events with >= MIN_DAYS distinct fire days, >= MIN_CELLDAYS fire cell-days, a peak day with >= MIN_PEAK_PIXELS
VIIRS fire pixels, at most MAX_DAYS long, mean fire pixels per cell-day >= MIN_DENSITY (ag/prescribed burning is
many sparse single-pixel cells), and non-burnable fuel (LANDFIRE FBFM40 91-99, fuel.npz frac_nb) under
MAX_NONBURNABLE of the event's cells (cropland, urban, water dominant).

Usage: fire_catalog.py OUT.csv [FIRST LAST]   (years, default 2014 2025)
       e.g. fire_catalog.py data/training/catalog.csv
Writes OUT.csv, one row per event: id (YYYY-NNN, by size within the year), start/end/peak date, days with fire,
fire cell-days, unique cells, fire pixels, peak-day pixels, summed FRP (GW, VNP14A1 MaxFRP sums: a relative size,
not an energy), bbox (W,S,E,N degrees, cell edges), centre (cell-day weighted), cell-weighted fuel group fractions,
dominant group and FBFM40 mode, canopy cover, landfire_valid (share of the event's cells with LANDFIRE fuel; LF2025
CONUS also covers a strip of Canada), in_us (centre in the lower 48 by in_us(); 0 for events in British Columbia
or northern Mexico, which the grid reaches), and an approximate region.
"""
import csv
import logging
import os
import sys

import numpy as np
from scipy import ndimage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

GRID = "data/harmonize/grid"
FUEL = "data/harmonize/fuel.npz"
GAP_DAYS = 2
PERSIST_WEEKS = 20       # distinct weeks with fire over all the years...
PERSIST_WEEKS_YEAR = 8   # ...or in any single year
MIN_DAYS = 4
MIN_CELLDAYS = 20
MIN_PEAK_PIXELS = 30
MAX_DAYS = 120
MIN_DENSITY = 2.0
MAX_NONBURNABLE = 0.5
GROUPS = ["gr", "gs", "sh", "tu", "tl", "sb", "nb"]
GROUP_NAMES = {"gr": "grass", "gs": "grass-shrub", "sh": "shrub", "tu": "timber-understory",
               "tl": "timber-litter", "sb": "slash-blowdown", "nb": "non-burnable"}


def region(lon, lat):
    """Approximate region (GACC-like) of a point in the lower 48."""
    if lat >= 42 and lon < -116.5:
        return "Northwest"
    ca_east = -120.0 if lat > 39 else -120.0 + (39 - lat) * 5.4 / 4
    if 32.5 <= lat < 42 and lon < ca_east:
        return "California"
    if lat >= 44 and lon < -104:
        return "Northern Rockies"
    if lon < -111 and lat >= 36.5:
        return "Great Basin"
    if lon < -103 and lat < 37:
        return "Southwest"
    if lon < -102:
        return "Rocky Mountain"
    if lon < -94:
        return "Plains"
    return "Southeast" if lat < 37 else "East"


def in_us(lon, lat):
    """Rough test that a point is in the lower 48, not Canada or Mexico (the grid reaches 50 N and 24 N): the
    49th parallel west of Lake of the Woods, and the Sonora/Chihuahua line at 31.33 N west of El Paso. The Great
    Lakes border and the Rio Grande are not tested (few events there)."""
    return not ((lat >= 49.0 and lon < -95.15) or (lat < 31.33 and lon < -106.5))


def cell_lonlat(row, col):
    return -125 + 0.1 * (col + 0.5), 50 - 0.1 * (row + 0.5)


def main(out_csv, first="2014", last="2025"):
    years = range(int(first), int(last) + 1)
    fuel = dict(np.load(FUEL))
    fires = {}
    for y in years:
        with np.load(os.path.join(GRID, "VNP14A1_%d.npz" % y)) as g:
            fires[y] = (g["fire"], g["frp"], g["day"])
        log.info("%d: %d fire cell-days", y, int((fires[y][0] > 0).sum()))

    # Persistent heat: cells burning in many years or many separate weeks.
    weeks_with = np.zeros((260, 590), np.int16)
    weeks_max = np.zeros((260, 590), np.int16)
    for y, (fire, _, day) in fires.items():
        f = fire > 0
        week = (day - 1) // 7
        in_year = sum(f[week == w].any(0).astype(np.int16) for w in np.unique(week))
        weeks_with += in_year
        weeks_max = np.maximum(weeks_max, in_year)
    persistent = (weeks_with >= PERSIST_WEEKS) | (weeks_max >= PERSIST_WEEKS_YEAR)
    log.info("persistent cells removed: %d (%d by total weeks, %d by weeks in one year)", int(persistent.sum()),
             int((weeks_with >= PERSIST_WEEKS).sum()), int((weeks_max >= PERSIST_WEEKS_YEAR).sum()))

    rows = []
    rejected = {}
    for y, (fire, frp, day) in fires.items():
        f = (fire > 0) & ~persistent
        # Days at most GAP_DAYS + 1 apart connect: dilate forward in time, label, keep the original cell-days.
        fd = f.copy()
        for k in range(1, GAP_DAYS + 1):
            fd[k:] |= f[:-k]
        labels, n = ndimage.label(fd, structure=np.ones((3, 3, 3), bool))
        labels = np.where(f, labels, 0)
        d, r, c = np.nonzero(labels)
        lab = labels[d, r, c]
        order = np.argsort(lab, kind="stable")
        d, r, c, lab = d[order], r[order], c[order], lab[order]
        bounds = np.flatnonzero(np.diff(lab)) + 1
        events = []
        for idx in np.split(np.arange(len(lab)), bounds):
            if len(idx) < MIN_CELLDAYS:
                rejected["small"] = rejected.get("small", 0) + 1
                continue
            dd, rr, cc = d[idx], r[idx], c[idx]
            days = np.unique(dd)
            pix = fire[dd, rr, cc].astype(float)
            stats = {"ndays": len(days), "celldays": len(idx), "pixels": pix.sum(), "length": dd.max() - dd.min() + 1}
            daily = np.bincount(dd - dd.min(), weights=pix)
            stats["peak_pixels"] = daily.max()
            stats["density"] = pix.sum() / len(idx)
            cells = np.unique(rr * 590 + cc)
            cr, cc_ = cells // 590, cells % 590
            with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
                __import__("warnings").simplefilter("ignore")
                frac = {g: float(np.nanmean(fuel["frac_" + g][cr, cc_])) for g in GROUPS}
            reason = None
            if stats["ndays"] < MIN_DAYS or stats["celldays"] < MIN_CELLDAYS:
                reason = "small"
            elif stats["peak_pixels"] < MIN_PEAK_PIXELS:
                reason = "weak peak"
            elif stats["length"] > MAX_DAYS:
                reason = "too long"
            elif stats["density"] < MIN_DENSITY:
                reason = "sparse (ag/prescribed)"
            elif not frac["nb"] < MAX_NONBURNABLE:
                reason = "non-burnable fuel"
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            w = pix
            lon, lat = cell_lonlat(np.average(rr, weights=w), np.average(cc, weights=w))
            modes = fuel["fbfm40_mode"][cr, cc_]
            modes = modes[modes > 0]
            burnable = {g: frac[g] for g in GROUPS if g != "nb"}
            peak_day = int(day[dd.min() + int(daily.argmax())])
            date = lambda doy: (np.datetime64("%d-01-01" % y) + np.timedelta64(int(doy) - 1, "D")).astype(str)
            events.append({
                "start": date(day[dd.min()]), "end": date(day[dd.max()]), "peak_date": date(peak_day),
                "fire_days": stats["ndays"], "fire_celldays": stats["celldays"], "cells": len(cells),
                "fire_pixels": int(stats["pixels"]), "peak_pixels": int(stats["peak_pixels"]),
                "frp_gw": round(float(frp[dd, rr, cc].sum()) / 1000, 2),
                "west": round(-125 + 0.1 * cc.min(), 1), "south": round(50 - 0.1 * (rr.max() + 1), 1),
                "east": round(-125 + 0.1 * (cc.max() + 1), 1), "north": round(50 - 0.1 * rr.min(), 1),
                "lon": round(float(lon), 3), "lat": round(float(lat), 3),
                **{"frac_" + g: round(frac[g], 3) for g in GROUPS},
                "fuel_group": GROUP_NAMES[max(burnable, key=burnable.get)],
                "fbfm40_mode": int(np.bincount(modes).argmax()) if len(modes) else -1,
                "cc": round(float(np.nanmean(fuel["cc"][cr, cc_])), 1),
                "landfire_valid": round(float(np.nan_to_num(fuel["valid"][cr, cc_]).mean()), 2),
                "in_us": int(in_us(lon, lat)),
                "region": region(lon, lat),
            })
        events.sort(key=lambda e: -e["fire_celldays"])
        for i, e in enumerate(events):
            rows.append({"id": "%d-%03d" % (y, i + 1), "year": y, **e})
        log.info("%d: %d components, %d events kept", y, n, len(events))
    log.info("rejected: %s", rejected)

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    log.info("wrote %s: %d events", out_csv, len(rows))


if __name__ == "__main__":
    main(*sys.argv[1:])
