#!/usr/bin/env python3
"""Precompute the History page's burning-activity calendar: earth/public/data/calendar/{region}.json.

Reads data/harmonize/grid (raw 0.1 degree fire grids) and data/harmonize/out-base-13y (p_viirs, 2003-2011).
Per region and day:
  raw   cells with fire (MOD14A1 or MYD14A1, 2000-2011; VNP14A1 2012+)
  harm  VIIRS-equivalent fire cells: sum of p_viirs over clear cells (2003-2011); VNP14A1 cells 2012+
        (2000-2002 have no Aqua, so no harmonized value: null)
  clear percent of the region's land cells seen clearly that day by the sensor(s) in use
Then year x week aggregates (week = 7 days from Jan 1, weekly value = mean daily * 7), the 2003-2025
weekly climatology (median, P10, P90) and, for 2026, each week's percentile against it.
Usage: scripts/history_calendar.py [OUTDIR]
"""
import json, logging, sys, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GRID = ROOT / "data/harmonize/grid"
HARM = ROOT / "data/harmonize/out-base-13y"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "earth/public/data/calendar"
YEARS = list(range(2000, 2027))
CLIM = range(2003, 2026)
W, N, CELL, COLS, ROWS = -125.0, 50.0, 0.1, 590, 260

# Regions as unions of lon/lat rectangles (west, east, south, north). A cell goes to the first region that
# contains it, so the list order matters; US-wide is every land cell.
REGIONS = [
    ("california", "California", [(-124.5, -120.0, 39.0, 42.0), (-124.5, -118.4, 35.0, 39.0), (-121.0, -114.3, 32.4, 35.0)]),
    ("pacific-nw", "Pacific Northwest (WA, OR)", [(-125.0, -116.5, 42.0, 49.6)]),
    ("great-basin", "Great Basin (NV, W UT)", [(-120.0, -111.0, 36.5, 42.0)]),
    ("southwest", "Southwest (AZ, NM, S UT)", [(-120.0, -103.0, 31.0, 37.0)]),
    ("rockies", "Northern Rockies and Rockies (ID, MT, WY, CO)", [(-117.0, -104.0, 37.0, 49.6)]),
    ("southeast", "Southeast", [(-94.6, -75.0, 24.0, 36.5), (-82.0, -75.0, 36.5, 37.5)]),
    ("plains", "Plains (Dakotas to Texas)", [(-104.0, -93.0, 26.0, 49.6)]),
    ("northeast", "Northeast and Great Lakes", [(-93.0, -66.0, 36.5, 49.6)]),
]
log = logging.getLogger("hist")


def masks():
    land = np.isfinite(np.load(ROOT / "data/harmonize/weather/weather_2020.npz")["wind"][0].astype("float32"))
    lon = W + CELL * (np.arange(COLS) + 0.5)
    lat = N - CELL * (np.arange(ROWS) + 0.5)
    LON, LAT = np.meshgrid(lon, lat)
    out, taken = {"us": land.copy()}, np.zeros_like(land)
    for key, _, rects in REGIONS:
        m = np.zeros_like(land)
        for w, e, s, n in rects:
            m |= (LON >= w) & (LON < e) & (LAT >= s) & (LAT < n)
        m &= land & ~taken
        taken |= m
        out[key] = m
    return out


def load(prod, year, ndays):
    """fire>0 and clear>0 boolean (ndays, ROWS, COLS) arrays placed by day of year, or None."""
    f = GRID / f"{prod}_{year}.npz"
    if not f.exists():
        return None
    d = np.load(f)
    day = d["day"] - 1
    fire = np.zeros((ndays, ROWS, COLS), bool); clear = np.zeros_like(fire)
    ok = day < ndays
    fire[day[ok]] = d["fire"][ok] > 0
    clear[day[ok]] = d["clear"][ok] > 0
    return fire, clear


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(filename)s:%(lineno)d %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "data/harmonize/history_calendar.log")])
    OUT.mkdir(parents=True, exist_ok=True)
    M = masks()
    keys = ["us"] + [r[0] for r in REGIONS]
    mat = np.stack([M[k].ravel() for k in keys], 1).astype("float32")  # cells x regions
    area = mat.sum(0)
    daily = {k: {} for k in keys}
    for y in YEARS:
        t0 = time.time()
        nd = 366 if (y % 4 == 0) else 365
        if y == 2026:
            nd = 365
        if y <= 2011:
            parts = [load(p, y, nd) for p in ("MOD14A1", "MYD14A1")]
            parts = [p for p in parts if p]
            fire = np.logical_or.reduce([p[0] for p in parts]); clear = np.logical_or.reduce([p[1] for p in parts])
        else:
            fire, clear = load("VNP14A1", y, nd)
        have = clear.reshape(nd, -1).any(1)  # days with any data (2026 stops mid-November)
        raw = fire.reshape(nd, -1).astype("float32") @ mat
        clr = clear.reshape(nd, -1).astype("float32") @ mat / area * 100
        if y >= 2012:
            harm = raw.copy()
        elif y >= 2003:
            h = np.load(HARM / f"harmonized_{y}.npz")
            p = h["p_viirs"].astype("float32"); p[h["observed"] == 0] = 0
            harm = p.reshape(p.shape[0], -1) @ mat
            if harm.shape[0] < nd:
                harm = np.vstack([harm, np.zeros((nd - harm.shape[0], len(keys)), "float32")])
        else:
            harm = np.full_like(raw, np.nan)
        for j, k in enumerate(keys):
            daily[k][y] = (raw[:, j], harm[:, j], clr[:, j], have)
        log.info("%d done in %.0fs, US raw %d harm %.0f", y, time.time() - t0, raw[:, 0].sum(), np.nansum(harm[:, 0]))
    names = {"us": "United States (lower 48)"} | {k: n for k, n, _ in REGIONS}
    for k in keys:
        write(k, names[k], int(area[keys.index(k)]), daily[k])
    (OUT / "regions.json").write_text(json.dumps([{"id": k, "name": names[k]} for k in keys]))
    log.info("done")


def annual(v, y):
    """Year total; years with download gaps are scaled to a full year, 2026 (to mid-November) is the sum so far."""
    n = int(v[3].sum()); k = 1.0 if y == 2026 else len(v[3]) / n
    return {"raw": int(round(np.nansum(v[0]) * k)), "harm": None if y < 2003 else round(float(np.nansum(v[1]) * k), 0),
            "days": n, "of": len(v[3])}


def r(a, nd):
    return [None if not np.isfinite(v) else (int(v) if nd == 0 else round(float(v), nd)) for v in a]


def write(key, name, cells, dy):
    weekly = {}
    # Days with no granule at all (download gaps, 2026 after the last day) are missing, not zero.
    dy = {y: tuple(np.where(v[3], v[i], np.nan) if i < 3 else v[3] for i in range(4)) for y, v in dy.items()}
    for y, (raw, harm, clr, have) in dy.items():
        nw = len(raw) // 7
        if y == 2026:
            nw = int(have.nonzero()[0].max() + 1) // 7  # complete weeks only
        def sl(a):  # mean over the week's days with data (at least 3), else missing
            out = []
            for i in range(nw):
                w = a[i * 7:i * 7 + 7]; ok = np.isfinite(w)
                out.append(w[ok].mean() if ok.sum() >= 3 else np.nan)
            return np.array(out)
        weekly[y] = {"raw": sl(raw) * 7, "harm": sl(harm) * 7, "clear": sl(clr)}
    clim, pct = {}, {}
    for mode in ("raw", "harm"):
        stack = np.array([weekly[y][mode][:52] for y in CLIM])
        clim[mode] = {"median": r(np.nanmedian(stack, 0), 1), "p10": r(np.nanpercentile(stack, 10, 0), 1), "p90": r(np.nanpercentile(stack, 90, 0), 1)}
        v = weekly[2026][mode]
        pct[mode] = [None if not np.isfinite(x) else round(float(((stack[:, i] < x).sum() + 0.5 * (stack[:, i] == x).sum()) / np.isfinite(stack[:, i]).sum() * 100), 0)
                     for i, x in enumerate(v[:52])]
    doc = {
        "id": key, "name": name, "land_cells": cells, "climatology_years": [CLIM[0], CLIM[-1]],
        "units": "fire-active 0.1 degree cells per week (cell-days); harm = expected VIIRS-equivalent cells",
        "weekly": {str(y): {m: r(weekly[y][m], 1 if m != "clear" else 0) for m in ("raw", "harm", "clear")} for y in weekly},
        "daily": {str(y): {"raw": r(raw, 0), "harm": r(harm, 1), "clear": r(clr, 0)} for y, (raw, harm, clr, _) in dy.items()},
        "annual": {str(y): annual(dy[y], y) for y in dy},
        "climatology": clim, "pct2026": pct,
    }
    (OUT / f"{key}.json").write_text(json.dumps(doc, separators=(",", ":")))
    log.info("wrote %s %.0f KB", key, (OUT / f"{key}.json").stat().st_size / 1e3)


if __name__ == "__main__":
    main()
