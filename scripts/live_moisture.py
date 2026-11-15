#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Seasonal live fuel moisture from a Growing-Season-Index-style proxy, from data available before issue.

Jolly et al. (2005) GSI = iPhoto * iTmin * iVPD, each a 0-1 linear ramp, averaged over 21 days. gridMET here has no
Tmin in data/harmonize/weather, so the cold-limit term is replaced by a dryness term on the dead 100 h fuel moisture
(a drought / curing indicator). Terms (21-day means of the days BEFORE the issue day, so nothing from the issue day):
    iPhoto  photoperiod at the fire latitude on the issue day: 0 at <= 10 h, 1 at >= 11 h   (Jolly)
    iVPD    1 at <= 0.9 kPa, 0 at >= 4.1 kPa (Jolly's 900-4100 Pa)
    iFM     0 at fm100 <= 5 %, 1 at >= 15 %   (stand-in for Tmin; dry fuels = cured/stressed vegetation)
    GSI     iPhoto * iVPD * iFM   in [0, 1]
    LH = 30 + 90 * GSI  (%, herbaceous: cured 30 .. fully green 120)
    LW = 60 + 90 * GSI  (%, woody: 60 .. 150)
The ramps are literature values, not fitted. The old constant (LH 90, LW from the ELMFIRE default) stays the default;
select this with LIVE_MOISTURE=gsi in scripts/elmfire_case.py (one value per case: the fire's centre cell).
Weather grid: data/harmonize/weather/weather_YEAR.npz, 0.1 degree, row = (50 - lat)/0.1, col = (lon + 125)/0.1,
day axis = day of year - 1.
"""
import datetime as dt
import math
import numpy as np

WINDOW = 21


def photoperiod_h(lat, day):
    decl = 0.409 * math.sin(2 * math.pi * day / 365 - 1.39)
    x = -math.tan(math.radians(lat)) * math.tan(decl)
    return 24 / math.pi * math.acos(max(-1.0, min(1.0, x)))


def ramp(x, lo, hi):
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def gsi_live_moisture(lon, lat, issue):
    """(LH %, LW %, info) for a fire at lon/lat with issue a datetime; uses days issue_day-21 .. issue_day-1."""
    day = issue.date()
    d = day.timetuple().tm_yday - 1
    w = np.load(f"data/harmonize/weather/weather_{day.year}.npz")
    r, c = int((50 - lat) // 0.1), int((lon + 125) // 0.1)
    lo = max(0, d - WINDOW)
    vpd = float(np.nanmean(w["vpd"][lo:d, r, c].astype(float)))
    fm = float(np.nanmean(w["fm100"][lo:d, r, c].astype(float)))
    ph = photoperiod_h(lat, d + 1)
    terms = {"photo": ramp(ph, 10, 11), "vpd": 1 - ramp(vpd, 0.9, 4.1), "fm100": ramp(fm, 5, 15)}
    gsi = terms["photo"] * terms["vpd"] * terms["fm100"]
    info = {"gsi": gsi, "terms": terms, "vpd_kpa_21d": vpd, "fm100_21d": fm, "photoperiod_h": ph,
            "window_days": [str(day - dt.timedelta(days=d - lo)), str(day - dt.timedelta(days=1))]}
    return 30 + 90 * gsi, 60 + 90 * gsi, info
