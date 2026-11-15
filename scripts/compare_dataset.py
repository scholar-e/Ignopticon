#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Per-year input/label tensors for the harmonized vs non-harmonized experiment (docs/COMPARISON.md).

Three fire conventions ("conv"), each a directory of per-year files under data/compare/tensors/:
    raw   2003-2011  MODIS Terra+Aqua fire presence, used as if it were VIIRS (the naive join)
    harm  2003-2011  harmonized p_viirs (strict fit, data/harmonize/out-base-strict: never saw 2021-2024)
    viirs 2012-2024  VIIRS S-NPP (VNP14A1) fire presence; every arm uses this from 2012 on
plus weather/YEAR.npz (gridMET, arm-independent) and static.npz. The exact format is in
data/compare/tensors/README.md; load_year() and load_static() below assemble the channel stack described there.

Usage:
    compare_dataset.py static                    write static.npz
    compare_dataset.py build CONV YEAR [YEAR..]  write CONV/YEAR.npz (CONV raw|harm|viirs) and weather/YEAR.npz
Env: COMPARE_HARM (default data/harmonize/out-base-strict), COMPARE_OUT (default data/compare/tensors).
"""
import logging
import os
import sys
import time

import numpy as np

AUX = "data/harmonize"
GRID = os.path.join(AUX, "grid")
HARM = os.environ.get("COMPARE_HARM", os.path.join(AUX, "out-base-strict"))
OUT = os.environ.get("COMPARE_OUT", "data/compare/tensors")
NY, NX = 260, 590
PRE = 30
CONV_YEARS = {"raw": range(2003, 2012), "harm": range(2003, 2012), "viirs": range(2012, 2025)}
FIRE_CH = ["fire_0", "fire_1", "fire_2", "f3_0", "f3_1", "f3_2", "f5_0", "f5_1", "f5_2", "since", "clear"]
WVARS = ["wind", "u", "v", "vpd", "erc", "fm100", "pr"]
DYN_CH = FIRE_CH + ["%s_t" % v for v in WVARS] + ["%s_t1" % v for v in WVARS] + ["doy_sin", "doy_cos"]
HARM_SINCE_THRESHOLD = 0.5  # harm: "fire" for days-since = 3x3 sum of p_viirs >= 0.5 (expected fire cells)
HARM_NEAR_THRESHOLD = 0.05  # harm: "fire" for the GBM near-fire sampling rule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)


def days_in(year):
    return int((np.datetime64("%d-01-01" % (year + 1)) - np.datetime64("%d-01-01" % year)).astype(int))


def box(a, k):
    """Sum over the k x k neighbourhood per day (separable shifted sums, zero outside the grid)."""
    h = k // 2
    p = np.pad(a, ((0, 0), (h, h), (0, 0)))
    s = np.zeros_like(a)
    for d in range(k):
        s += p[:, d:d + NY]
    p = np.pad(s, ((0, 0), (0, 0), (h, h)))
    s = np.zeros_like(a)
    for d in range(k):
        s += p[:, :, d:d + NX]
    return s


def grid_year(prod, year):
    """(fire > 0, clear > 0) as float32 full-year arrays placed by day of year, or None."""
    path = os.path.join(GRID, "%s_%d.npz" % (prod, year))
    if not os.path.exists(path):
        return None
    z = np.load(path)
    n = days_in(year)
    f, c = np.zeros((n, NY, NX), np.float32), np.zeros((n, NY, NX), np.float32)
    f[z["day"] - 1] = z["fire"] > 0
    c[z["day"] - 1] = z["clear"] > 0
    return f, c


def series(conv, year):
    """(fire, clear) for one convention-year: fire is presence (raw, viirs) or p_viirs (harm). None if missing."""
    if year not in CONV_YEARS[conv]:
        return None
    if conv == "viirs":
        return grid_year("VNP14A1", year)
    parts = [p for p in (grid_year("MOD14A1", year), grid_year("MYD14A1", year)) if p is not None]
    fire = np.maximum.reduce([p[0] for p in parts])
    clear = np.maximum.reduce([p[1] for p in parts])
    if conv == "harm":
        z = np.load(os.path.join(HARM, "harmonized_%d.npz" % year))
        p = z["p_viirs"].astype(np.float32)
        assert p.shape[0] == days_in(year), (year, p.shape)
        fire = p
    return fire, clear


def window(conv, year):
    """PRE days of the previous year (same convention, else empty), the year, and 1 day of the next year.
    have marks days with data (lags and labels outside the convention's years are empty / masked)."""
    cur = series(conv, year)
    prev, nxt = series(conv, year - 1), series(conv, year + 1)
    z = lambda k: np.zeros((k, NY, NX), np.float32)
    fire = np.concatenate([prev[0][-PRE:] if prev else z(PRE), cur[0], nxt[0][:1] if nxt else z(1)])
    clear = np.concatenate([prev[1][-PRE:] if prev else z(PRE), cur[1], nxt[1][:1] if nxt else z(1)])
    return fire, clear


def land_mask():
    w = np.load(os.path.join(AUX, "weather", "weather_2020.npz"))
    return np.isfinite(w["erc"][:366].astype(np.float32)).mean(0) > 0.5


def build_weather(year):
    path = os.path.join(OUT, "weather", "%d.npz" % year)
    if os.path.exists(path):
        return
    n = days_in(year)
    w = np.load(os.path.join(AUX, "weather", "weather_%d.npz" % year))
    wn = np.load(os.path.join(AUX, "weather", "weather_%d.npz" % (year + 1)))
    out = np.full((n + 1, len(WVARS), NY, NX), np.nan, np.float16)
    for j, k in enumerate(WVARS):
        x = w[k][:n]
        out[:x.shape[0], j] = x
        out[n, j] = wn[k][0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path + ".%d.tmp.npz" % os.getpid(), w=out, names=np.array(WVARS))
    os.replace(path + ".%d.tmp.npz" % os.getpid(), path)
    log.info("wrote %s", path)


def build(conv, year):
    t0 = time.time()
    path = os.path.join(OUT, conv, "%d.npz" % year)
    if os.path.exists(path) and not os.environ.get("COMPARE_REBUILD"):
        log.info("%s exists", path)
        return
    n = days_in(year)
    fire, clear = window(conv, year)
    f3, f5 = box(fire, 3), box(fire, 5)
    event = (f3 >= HARM_SINCE_THRESHOLD) if conv == "harm" else (f3 > 0)
    since = np.full((NY, NX), 30.0, np.float32)
    sall = np.empty((n, NY, NX), np.float32)
    for k in range(PRE + n):
        since = np.where(event[k], 0, np.minimum(since + 1, 30))
        if k >= PRE:
            sall[k - PRE] = since
    x = np.empty((n, len(FIRE_CH), NY, NX), np.float16)
    t = slice(PRE, PRE + n)
    for L in range(3):
        s = slice(PRE - L, PRE - L + n)
        x[:, L], x[:, 3 + L], x[:, 6 + L] = fire[s], f3[s], f5[s]
    x[:, 9], x[:, 10] = sall, clear[t]
    s1 = slice(PRE + 1, PRE + 1 + n)
    land = land_mask()
    label = fire[s1].astype(np.float16)
    mask = ((clear[s1] > 0) & land[None]).astype(np.uint8)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path + ".%d.tmp.npz" % os.getpid(), x=x, label=label, mask=mask, names=np.array(FIRE_CH), year=year)
    os.replace(path + ".%d.tmp.npz" % os.getpid(), path)
    log.info("wrote %s: label sum %.0f on %d clear land cell-days, %.0f s", path,
             float(label[mask > 0].astype(np.float64).sum()), int(mask.sum()), time.time() - t0)


def build_static():
    e = np.load(os.path.join(AUX, "elevation.npz"))
    f = np.load(os.path.join(AUX, "fuel.npz"))
    lat = (50 - 0.1 * (np.arange(NY) + 0.5)).astype(np.float32)
    lon = (-125 + 0.1 * (np.arange(NX) + 0.5)).astype(np.float32)
    ch = {"elev": e["elev"], "elev_sd": e["elev_sd"]}
    for k in f.files:
        if f[k].shape == (NY, NX):
            ch["fuel_" + k] = f[k].astype(np.float32)
    ch["lat"] = np.broadcast_to(lat[:, None], (NY, NX))
    ch["lon"] = np.broadcast_to(lon[None], (NY, NX))
    x = np.stack([np.asarray(v, np.float32) for v in ch.values()]).astype(np.float16)
    os.makedirs(OUT, exist_ok=True)
    np.savez_compressed(os.path.join(OUT, "static.npz"), x=x, names=np.array(list(ch)), land=land_mask().astype(np.uint8))
    log.info("wrote static.npz: %s", list(ch))


# ---------------------------------------------------------------- loader (used by GBM, evaluation, U-Net)

def conv_for(arm, year):
    if year >= 2012:
        return "viirs"
    if arm.startswith("raw"):
        return "raw"
    if arm.startswith("harm"):
        return "harm"
    raise ValueError("arm %s has no data for %d" % (arm, year))


def doy_channels(year):
    n = days_in(year)
    a = 2 * np.pi * np.arange(1, n + 1) / n
    return np.sin(a).astype(np.float32), np.cos(a).astype(np.float32)


def load_year(arm, year, root=OUT):
    """dyn (days, len(DYN_CH), 260, 590) float16, label (days, 260, 590) float32, mask (days, 260, 590) bool.
    Day index d is day t = Jan 1 + d; label and mask are for t+1."""
    z = np.load(os.path.join(root, conv_for(arm, year), "%d.npz" % year))
    w = np.load(os.path.join(root, "weather", "%d.npz" % year))["w"]
    n = days_in(year)
    dyn = np.empty((n, len(DYN_CH), NY, NX), np.float16)
    dyn[:, :len(FIRE_CH)] = z["x"]
    dyn[:, len(FIRE_CH):len(FIRE_CH) + 7] = w[:n]
    dyn[:, len(FIRE_CH) + 7:len(FIRE_CH) + 14] = w[1:n + 1]
    s, c = doy_channels(year)
    dyn[:, -2] = s[:, None, None]
    dyn[:, -1] = c[:, None, None]
    return dyn, z["label"].astype(np.float32), z["mask"] > 0


def load_static(root=OUT):
    """x (C_static, 260, 590) float16, names, land (260, 590) bool."""
    z = np.load(os.path.join(root, "static.npz"))
    return z["x"], [str(k) for k in z["names"]], z["land"] > 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "static":
        build_static()
    elif len(sys.argv) >= 4 and sys.argv[1] == "build" and sys.argv[2] in CONV_YEARS:
        for y in sys.argv[3:]:
            build_weather(int(y))
            build(sys.argv[2], int(y))
    else:
        sys.exit(__doc__)
