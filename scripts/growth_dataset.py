#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pandas", "pyarrow"]
# ///
"""Event-day dataset for the fire-growth model (data/growth/NOTES.md).

Events are space-time clusters of fire cell-days on the 0.1 degree daily grid, as scripts/fire_catalog.py, but on the
harmonized tensors (data/compare/tensors, its README): conventions viirs (2012-2024), raw (MODIS as is, 2003-2011) and
harm (strict p_viirs >= 0.5, 2003-2011). Arms are assembled in growth_train.py: harm = harm + viirs, raw = raw + viirs,
viirs-only = viirs. The tensors have no pixel counts, so the peak filter is MIN_PEAK_CELLS cells instead of 30 pixels,
and the pixel-density filter is dropped. Persistent heat (weeks of fire per cell) uses the catalog's rule, with the
total-weeks limit scaled to the convention's number of years.

One row per event and day t, from the event's first day to GAP_DAYS after its last fire day (so the end of an event is
not selected on). Target: n_new = fire cells on t+1 that are clear-looked on t+1, had no fire in t-29..t, and lie
within 2 cells of the event's footprint so far. Features use only days <= t, plus gridMET weather on t and t+1 (the
t+1 columns are the perfect-forecast stand-in; columns *_t1 are dropped for the day-t-only variant).
Usage: growth_dataset.py [CONV ...]   (default viirs raw harm) -> data/growth/rows_CONV.parquet
"""
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from scipy import ndimage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

T = "data/compare/tensors"
CACHE = "data/growth/cache"
YEARS = {"viirs": range(2012, 2025), "raw": range(2003, 2012), "harm": range(2003, 2012)}
GAP_DAYS, MIN_DAYS, MIN_CELLDAYS, MIN_PEAK_CELLS, MAX_DAYS, MAX_NB = 2, 4, 20, 5, 120, 0.5
PERSIST_WEEKS, PERSIST_WEEKS_YEAR = 20, 8
WX = ["wind", "u", "v", "vpd", "erc", "fm100", "pr"]
FUEL = ["fuel_frac_nb", "fuel_frac_gr", "fuel_frac_gs", "fuel_frac_sh", "fuel_frac_tu", "fuel_frac_tl", "fuel_frac_sb",
        "fuel_cc", "fuel_ch", "fuel_cbh", "fuel_cbd", "elev", "elev_sd"]
ST4 = np.ones((3, 3), bool)


def cache_year(conv, y):
    """bool fire presence (days,260,590) and bool clear look, cached from the tensor's channels 0 and 10."""
    p = f"{CACHE}/{conv}_{y}.npz"
    if os.path.exists(p):
        return
    with np.load(f"{T}/{conv}/{y}.npz") as z:
        x = z["x"]
        thr = 0.5 if conv == "harm" else 0.0
        fire = x[:, 0].astype(np.float32) > thr if conv == "harm" else x[:, 0] > 0
        clear = x[:, 10] > 0
    np.savez(p, fire=fire, clear=clear)
    log.info("cached %s %d", conv, y)


def load_conv(conv):
    ys = list(YEARS[conv])
    with ProcessPoolExecutor(4) as ex:
        list(ex.map(cache_year, [conv] * len(ys), ys))
    F, C, start = [], [], {}
    n = 0
    for y in ys:
        z = np.load(f"{CACHE}/{conv}_{y}.npz")
        start[y] = n
        F.append(z["fire"]); C.append(z["clear"])
        n += len(F[-1])
    return ys, np.concatenate(F), np.concatenate(C), start


def persistent_mask(F, ys, start):
    tot = np.zeros(F.shape[1:], np.int16)
    mx = np.zeros_like(tot)
    for i, y in enumerate(ys):
        s = start[y]
        e = start[ys[i + 1]] if i + 1 < len(ys) else len(F)
        f = F[s:e]
        wk = np.arange(len(f)) // 7
        inyr = sum(f[wk == w].any(0).astype(np.int16) for w in np.unique(wk))
        tot += inyr
        mx = np.maximum(mx, inyr)
    return (tot >= PERSIST_WEEKS * len(ys) / 12) | (mx >= PERSIST_WEEKS_YEAR)


def do_year(args):
    conv, y, F, C, s, e, pers, static, names = args[:9]
    PRE, POST = 30, GAP_DAYS + 2
    a, b = max(0, s - PRE), min(len(F), e + POST)
    G, GC = F[a:b], C[a:b]
    off = s - a                       # index of Jan 1 of year y in G
    f = G.copy()
    f &= ~pers
    f[:off] = False
    f[off + (e - s):] = False
    fd = f.copy()
    for k in range(1, GAP_DAYS + 1):
        fd[k:] |= f[:-k]
    lab, n = ndimage.label(fd, structure=np.ones((3, 3, 3), bool))
    lab = np.where(f, lab, 0)
    # (days+1, 7, 260, 590) float16; area_constrain.py passes its own 2026 stack as args[9]
    wx = args[9] if len(args) > 9 else np.load(f"{T}/weather/{y}.npz")["w"]
    sidx = {k: i for i, k in enumerate(names)}
    fuel = np.stack([static[sidx[k]] for k in FUEL]).astype(np.float32)
    valid = static[sidx["fuel_valid"]] > 0
    latg, long = static[sidx["lat"]], static[sidx["lon"]]
    objs = ndimage.find_objects(lab)
    rows = []
    ndays_y = e - s
    for li, sl in enumerate(objs, 1):
        if sl is None:
            continue
        sd, sr, sc = sl
        m = lab[sl] == li
        celldays = int(m.sum())
        if celldays < MIN_CELLDAYS:
            continue
        perday = m.sum((1, 2))
        fdays = np.flatnonzero(perday)
        if len(fdays) < MIN_DAYS or perday.max() < MIN_PEAK_CELLS or fdays[-1] - fdays[0] + 1 > MAX_DAYS:
            continue
        cells = m.any(0)
        rr, cc = np.nonzero(cells)
        rr = rr + sr.start; cc = cc + sc.start
        nb = np.nanmean(np.where(valid[rr, cc], fuel[0][rr, cc], np.nan)) if valid[rr, cc].any() else 0.0
        if not nb < MAX_NB:
            continue
        r0, r1 = max(sr.start - 4, 0), min(sr.stop + 4, 260)
        c0, c1 = max(sc.start - 4, 0), min(sc.stop + 4, 590)
        d0 = sd.start
        # crop time window: from 30 days before the event start to the last row's t+1
        t_first, t_last = d0 + fdays[0], d0 + fdays[-1] + GAP_DAYS
        t_last = min(t_last, b - a - 2)
        w0 = max(t_first - PRE, 0)
        Fc = G[w0:t_last + 2, r0:r1, c0:c1]
        Cc = GC[w0:t_last + 2, r0:r1, c0:c1]
        Ev = np.zeros(Fc.shape, bool)
        Ev[d0 - w0 + np.arange(m.shape[0]), sr.start - r0:sr.stop - r0, sc.start - c0:sc.stop - c0] = False
        Ev[d0 - w0:d0 - w0 + m.shape[0], sr.start - r0:sr.stop - r0, sc.start - c0:sc.stop - c0] = m
        T_ = len(Fc)
        idx = np.arange(T_)[:, None, None]
        last = np.maximum.accumulate(np.where(Fc, idx, -10**6), axis=0)   # index of last fire <= t
        recent = (idx - last) < 30                                          # fire within t-29..t
        cum = np.maximum.accumulate(Ev, axis=0)
        evday = Ev.any((1, 2))
        pers_c = pers[r0:r1, c0:c1]
        lat_c, lon_c = latg[r0:r1, c0:c1], long[r0:r1, c0:c1]

        def new_cells(tt):
            """fire cells on tt, not burned in tt-30..tt-1, within 2 cells of the event footprint through tt-1"""
            zone = ndimage.binary_dilation(cum[tt - 1], ST4, 2)
            return Fc[tt] & ~recent[tt - 1] & zone & ~pers_c
        last_ev = None
        for tt in range(t_first - w0, t_last - w0 + 1):
            if not cum[tt].any():
                continue
            if evday[tt]:
                last_ev = tt
            zone_t = ndimage.binary_dilation(cum[tt], ST4, 2)
            y_new = Fc[tt + 1] & ~recent[tt] & zone_t & ~pers_c & Cc[tt + 1]
            act = ndimage.binary_dilation(Ev[max(tt - 2, 0):tt + 1].any(0) if evday[max(tt - 2, 0):tt + 1].any() else cum[tt], ST4, 1)
            ar, ac = np.nonzero(act)
            gr, gc = ar + r0, ac + c0
            td = tt + w0 + a - s                                           # day of year index in year y
            if td < 0 or td >= ndays_y:
                continue
            row = {"conv": conv, "year": y, "event": f"{y}-{li:04d}", "doy": td + 1,
                   "n_new": int(y_new.sum()), "zone_cells": int(zone_t.sum()),
                   "clear_t1": float(Cc[tt + 1][zone_t].mean()),
                   "size_cells": int(cum[tt].sum()), "act_cells": int(act.sum()),
                   "n_t": int(Ev[tt].sum()), "n_t1": int(Ev[tt - 1].sum()) if tt >= 1 else 0,
                   "n_t2": int(Ev[tt - 2].sum()) if tt >= 2 else 0,
                   "g0": int(new_cells(tt).sum()) if tt >= 1 else 0, "g1": int(new_cells(tt - 1).sum()) if tt >= 2 else 0,
                   "g2": int(new_cells(tt - 2).sum()) if tt >= 3 else 0,
                   "days_active": int(tt - (d0 + fdays[0] - w0) + 1), "days_since_fire": int(tt - last_ev),
                   "clear_t": float(Cc[tt][act].mean()),
                   "doy_sin": float(np.sin(2 * np.pi * (td + 1) / ndays_y)), "doy_cos": float(np.cos(2 * np.pi * (td + 1) / ndays_y)),
                   "lat": float(lat_c[act].mean()), "lon": float(lon_c[act].mean())}
            w_t, w_t1 = wx[td][:, gr, gc].astype(np.float32), wx[td + 1][:, gr, gc].astype(np.float32)
            for k, nm in enumerate(WX):
                row[nm + "_t"] = float(w_t[k].mean())
                row[nm + "_t1"] = float(w_t1[k].mean())
            fv = fuel[:, gr, gc]
            vv = valid[gr, gc]
            for k, nm in enumerate(FUEL):
                row[nm] = float(fv[k][vv].mean()) if vv.any() else np.nan
            rows.append(row)
    log.info("%s %d: %d events scanned, %d rows", conv, y, len(objs), len(rows))
    return rows


def main(convs):
    s = np.load(f"{T}/static.npz")
    static, names = s["x"].astype(np.float32), [str(n) for n in s["names"]]
    for conv in convs:
        ys, F, C, start = load_conv(conv)
        pers = persistent_mask(F, ys, start)
        log.info("%s: %d persistent cells", conv, int(pers.sum()))
        jobs = []
        for i, y in enumerate(ys):
            e = start[ys[i + 1]] if i + 1 < len(ys) else len(F)
            jobs.append((conv, y, F, C, start[y], e, pers, static, names))
        rows = []
        for j in jobs:                                    # serial: each year holds ~1 GB of weather
            rows += do_year(j)
        df = pd.DataFrame(rows)
        df.to_parquet(f"data/growth/rows_{conv}.parquet")
        log.info("%s: %d rows, %d events, zero-growth share %.3f", conv, len(df), df.event.nunique(), (df.n_new == 0).mean())


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    main(sys.argv[1:] or ["viirs", "raw", "harm"])
