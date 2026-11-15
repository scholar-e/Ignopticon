#!/usr/bin/env python3
"""Does harmonizing MODIS and VIIRS change what a burning calendar says about a region? (PLAN.md M7, demo story)

Reads FIRMS's yearly per-country archives (no key: https://firms.modaps.eosdis.nasa.gov/data/country/
{modis,viirs-snpp}/YEAR/..._United_States.csv), keeps presumed vegetation fires (type 0), and counts fire cell-days on
the project's 0.1 degree grid (a cell-day has fire if the sensor detected any fire in it; MODIS = Terra + Aqua,
VIIRS = S-NPP 375 m), per region and month.

Harmonization here is deliberately simple and region-specific: VIIRS-equivalent = MODIS x r(region, calendar month),
r = sum VIIRS / sum MODIS over the overlap years. It is scored leave-one-year-out on 2012..LAST, against MODIS as-is
and against one national factor. (harmonize.py's p_viirs is the full cell-day model; this is the calendar-level check.)

Usage: firms_region_check.py CSV_DIR [OUT.json]
"""
import csv
import glob
import json
import os
import sys
from collections import defaultdict

# lat_min, lat_max, lon_min, lon_max
REGIONS = {
    "flint_hills": (36.9, 39.3, -97.2, -96.0),   # Kansas/Oklahoma tallgrass prairie, spring rangeland burning
    "southeast": (29.5, 33.5, -86.0, -81.0),     # GA/FL/AL coastal plain, prescribed burning
    "sierra": (35.5, 41.0, -122.5, -118.5),      # California Sierra Nevada / NorCal, large wildfires
    "lower48": (24.0, 50.0, -125.0, -66.0),
}
FIRST, PLAN_YEAR, SWITCH = 2003, 2011, 2012  # MODIS Terra+Aqua full years from 2003; Flint Hills plan; VIIRS from 2012


def cell_days(path):
    """{region: {(year, month): set of (cell, day)}} for type-0 detections."""
    out = {r: defaultdict(set) for r in REGIONS}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("type", "0") not in ("0", ""):
                continue
            lat, lon, day = float(row["latitude"]), float(row["longitude"]), row["acq_date"]
            key, cell = (int(day[:4]), int(day[5:7])), (int(lat * 10 // 1), int(lon * 10 // 1))
            for r, (a, b, c, d) in REGIONS.items():
                if a <= lat < b and c <= lon < d:
                    out[r][key].add((cell, day))
    return {r: {k: len(v) for k, v in m.items()} for r, m in out.items()}


def load(csv_dir, prefix):
    counts = {r: defaultdict(int) for r in REGIONS}
    for path in sorted(glob.glob(os.path.join(csv_dir, prefix + "_*_United_States.csv"))):
        for r, m in cell_days(path).items():
            for k, n in m.items():
                counts[r][k] += n
        print("read", os.path.basename(path), file=sys.stderr)
    return counts


def main(csv_dir, out_path=None):
    modis, viirs = load(csv_dir, "modis"), load(csv_dir, "viirs-snpp")
    last = max(y for (y, _) in viirs["lower48"])
    overlap = range(SWITCH, last + 1)
    M = lambda r, y, m: modis[r].get((y, m), 0)
    V = lambda r, y, m: viirs[r].get((y, m), 0)

    def ratio(r, months, years):
        sm = sum(M(r, y, m) for y in years for m in months)
        return sum(V(r, y, m) for y in years for m in months) / sm if sm else 1.0

    res = {"regions": REGIONS, "last_year": last, "scores": {}, "series": {}}
    for r in REGIONS:
        # Leave-one-year-out: monthly VIIRS predicted from MODIS, per method; error over all held-out months.
        err = {"modis_as_is": 0.0, "national_factor": 0.0, "regional_monthly": 0.0}
        total_v = 0
        for test in overlap:
            train = [y for y in overlap if y != test]
            nat = ratio("lower48", range(1, 13), train)
            for m in range(1, 13):
                rm = ratio(r, [m], train) if sum(M(r, y, m) for y in train) >= 20 else ratio(r, range(1, 13), train)
                v, mo = V(r, test, m), M(r, test, m)
                total_v += v
                err["modis_as_is"] += abs(mo - v)
                err["national_factor"] += abs(mo * nat - v)
                err["regional_monthly"] += abs(mo * rm - v)
        res["scores"][r] = {k: round(e / total_v, 3) for k, e in err.items()} | {
            "overlap_ratio_viirs_over_modis": round(ratio(r, range(1, 13), overlap), 2),
            "national_ratio": round(ratio("lower48", range(1, 13), overlap), 2)}
        # Annual and spring series under each calendar.
        rmon = {m: ratio(r, [m], overlap) for m in range(1, 13)}
        ser = {}
        for y in range(FIRST, last + 1):
            ser[y] = {"modis": {m: M(r, y, m) for m in range(1, 13)},
                      "splice": {m: (M(r, y, m) if y < SWITCH else V(r, y, m)) for m in range(1, 13)},
                      "harmonized": {m: (round(M(r, y, m) * rmon[m], 1) if y < SWITCH else V(r, y, m)) for m in range(1, 13)}}
        res["series"][r] = ser

    # The Flint Hills decision numbers: spring (Feb-Apr) burning and April's share, before vs after the 2011 plan.
    fh = res["series"]["flint_hills"]
    res["flint_hills_plan"] = {}
    for cal in ("modis", "splice", "harmonized"):
        def period(years):
            spring = [sum(fh[y][cal][m] for m in (2, 3, 4)) for y in years]
            april = [fh[y][cal][4] for y in years]
            return {"spring_mean": round(sum(spring) / len(spring), 1), "april_share": round(sum(april) / sum(spring), 3)}
        res["flint_hills_plan"][cal] = {"2003-2010": period(range(FIRST, PLAN_YEAR)), f"2011-{last}": period(range(PLAN_YEAR, last + 1))}
    print(json.dumps({k: res[k] for k in ("last_year", "scores", "flint_hills_plan")}, indent=1))
    if out_path:
        json.dump(res, open(out_path, "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
