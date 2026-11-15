#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Edge-advance samples on the 0.1 deg daily grid (MODELLING_LOG.md entry 19).

An edge cell on day t: land, burnable (fuel valid, non-burnable share < 0.9), not burned in the previous 30 days
(no fire in the cell on t-29..t) and 8-adjacent to a cell with fire on t or t-1. Label: fire in the cell on t+1, kept
only where the label's sensor saw it clearly on t+1 (the tensor mask). Fire per convention (data/compare/tensors):
viirs = VNP14A1 presence (2012+), raw = MODIS Terra/Aqua presence, harm = strict harmonized p_viirs (soft label).
In harm, "fire" for the edge/burned/neighbour rules = p_viirs >= HARM_FIRE (0.35: the threshold at which the number
of cells equals the expected number sum(p) in 2004/2007/2011); neighbour intensities use p itself.

Features (FEATS): burning-neighbour counts and summed fire on t, t-1, t-2; 5x5 fire on t; days with fire in the 5x5
over t-29..t (active days); direction from the burning neighbours to the cell (weights = fire on t, or t-1 if none on
t) and its coherence; wind on t and t+1 projected on it (tail = component blowing from the fire to the cell, cross =
|perpendicular|), speed; elevation of the cell minus the weighted neighbour elevation (dz, upslope > 0); vpd, erc,
fm100, pr on t and t+1 (gridMET; t+1 = perfect forecast); cell fuel shares, FBFM40 mode, canopy; elev, elev_sd;
season; lat, lon; clear on t. No FRP. The cell's own fire value on t is not a feature (in harm it uses MODIS t+1).

`new` = the neighbouring fire is new: no fire in the cell's 5x5 on t-29..t-2 (it appeared on t-1 or t).

Usage:
    edge_dataset.py build CONV YEAR [YEAR ...]     -> data/edge/samples/{conv}_{year}.npz
Library: day_rows(...) builds the rows for one day from any fire history (see data/edge/NOTES.md).
"""
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_dataset import NX, NY, box, days_in, load_static  # noqa: E402

TENSORS = os.environ.get("EDGE_TENSORS", "data/compare/tensors")
OUT = os.environ.get("EDGE_SAMPLES", "data/edge/samples")
HIST = 30
HARM_FIRE = 0.35
OFFS = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)]
WV = ["wind", "u", "v", "vpd", "erc", "fm100", "pr"]
FEATS = (["nb_n_t", "nb_n_t1", "nb_sum_t", "nb_sum_t1", "nb_sum_t2", "f5_sum_t", "active_days", "coherence",
          "tail_t", "cross_t", "wind_t", "tail_t1", "cross_t1", "wind_t1", "dz",
          "vpd_t", "erc_t", "fm100_t", "pr_t", "vpd_t1", "erc_t1", "fm100_t1", "pr_t1",
          "elev", "elev_sd", "frac_nb", "frac_gr", "frac_gs", "frac_sh", "frac_tu", "frac_tl", "frac_sb",
          "fbfm40", "cc", "ch", "cbh", "cbd", "doy_sin", "doy_cos", "lat", "lon", "clear_t"])
CATEGORICAL = ["fbfm40"]
CONV_YEARS = {"raw": range(2003, 2012), "harm": range(2003, 2012), "viirs": range(2012, 2025)}
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)], force=True)
log = logging.getLogger(__name__)


def static_grid():
    """dict of static (260, 590) float32 arrays (elev, elev_sd, frac_*, fbfm40, cc, ch, cbh, cbd, lat, lon) plus
    `burnable` and `land` (bool)."""
    x, names, land = load_static(TENSORS)
    S = {n.replace("fuel_", "").replace("_mode", ""): x[i].astype(np.float32) for i, n in enumerate(names)}
    with np.errstate(invalid="ignore"):
        S["burnable"] = land & (np.nan_to_num(S["valid"]) > 0) & (np.nan_to_num(S["frac_nb"], nan=1.0) < 0.9)
    S["land"] = land
    return S


def threshold(conv):
    return HARM_FIRE if conv == "harm" else 1e-6


def day_rows(hist, wt, wt1, S, thr, ok=None, clear_t=None, doy=180, ndays=365, rc=None):
    """Edge rows for one day t.
    hist  (HIST+1, 260, 590) float32 fire values for t-30..t (last = t; presence 0/1 or p_viirs), zeros if unknown
    wt, wt1  (7, 260, 590) weather on t and t+1 (WV order: wind u v vpd erc fm100 pr; u, v = velocity, m/s)
    S     static_grid();  thr  fire threshold (threshold(conv));  ok  optional (260, 590) bool extra cell filter
          (e.g. label clear on t+1);  clear_t  optional (260, 590) clear-on-t;  rc  optional (rows, cols) restriction
    Returns (X float32 (n, len(FEATS)), r, c, new bool, ux, uy) where (ux, uy) is the unit fire->cell direction."""
    pres = hist >= thr
    burned = pres[-HIST:].any(0)
    p0, p1 = pres[-1].astype(np.float32), pres[-2].astype(np.float32)
    nb0 = box(p0[None], 3)[0] - p0
    nb1 = box(p1[None], 3)[0] - p1
    edge = S["burnable"] & ~burned & ((nb0 > 0) | (nb1 > 0))
    if ok is not None:
        edge &= ok
    r, c = np.nonzero(edge)
    if rc is not None:
        keep = np.isin(r * NX + c, rc[0] * NX + rc[1])
        r, c = r[keep], c[keep]
    n = len(r)
    if n == 0:
        return np.zeros((0, len(FEATS)), np.float32), r, c, np.zeros(0, bool), np.zeros(0), np.zeros(0)
    f5 = box(pres[-HIST:].astype(np.float32), 5) > 0           # (HIST, NY, NX): fire in 5x5 on t-29..t
    active = f5.sum(0)[r, c].astype(np.float32)
    new = ~f5[:-2].any(0)[r, c]
    fv = np.where(pres, hist, 0).astype(np.float32)             # fire values where "fire"
    pad = np.pad(fv[-3:], ((0, 0), (1, 1), (1, 1)))
    epad = np.pad(np.nan_to_num(S["elev"]), 1)
    W = np.zeros((3, n), np.float32)
    vx = np.zeros(n, np.float32); vy = np.zeros(n, np.float32); wsum0 = np.zeros(n, np.float32)
    vx1 = np.zeros(n, np.float32); vy1 = np.zeros(n, np.float32); wsum1 = np.zeros(n, np.float32)
    ez0 = np.zeros(n, np.float32); ez1 = np.zeros(n, np.float32)
    for dr, dc in OFFS:
        g = pad[:, r + 1 + dr, c + 1 + dc]                       # (3, n): t-2, t-1, t
        W += g
        e = epad[r + 1 + dr, c + 1 + dc]
        L = np.hypot(dr, dc)
        ex, ey = -dc / L, dr / L                                 # neighbour -> cell, east/north (row grows south)
        vx += g[2] * ex; vy += g[2] * ey; wsum0 += g[2]; ez0 += g[2] * e
        vx1 += g[1] * ex; vy1 += g[1] * ey; wsum1 += g[1]; ez1 += g[1] * e
    use0 = wsum0 > 0
    vx = np.where(use0, vx, vx1); vy = np.where(use0, vy, vy1)
    ws = np.where(use0, wsum0, wsum1); ez = np.where(use0, ez0, ez1)
    norm = np.hypot(vx, vy)
    coh = norm / np.maximum(ws, 1e-6)
    ux = np.where(norm > 1e-6, vx / np.maximum(norm, 1e-6), 0); uy = np.where(norm > 1e-6, vy / np.maximum(norm, 1e-6), 0)
    dz = np.nan_to_num(S["elev"])[r, c] - ez / np.maximum(ws, 1e-6)
    f5sum = box(fv[-1][None], 5)[0][r, c]

    def wind(w):
        u, v = w[1][r, c].astype(np.float32), w[2][r, c].astype(np.float32)
        return u * ux + v * uy, np.abs(-u * uy + v * ux), w[0][r, c].astype(np.float32)

    tl0, cr0, sp0 = wind(wt)
    tl1, cr1, sp1 = wind(wt1)
    cols = {"nb_n_t": nb0[r, c], "nb_n_t1": nb1[r, c], "nb_sum_t": W[2], "nb_sum_t1": W[1], "nb_sum_t2": W[0],
            "f5_sum_t": f5sum, "active_days": active, "coherence": coh,
            "tail_t": tl0, "cross_t": cr0, "wind_t": sp0, "tail_t1": tl1, "cross_t1": cr1, "wind_t1": sp1, "dz": dz,
            "doy_sin": np.full(n, np.sin(2 * np.pi * doy / ndays)), "doy_cos": np.full(n, np.cos(2 * np.pi * doy / ndays)),
            "clear_t": (clear_t[r, c] if clear_t is not None else np.ones(n))}
    for k, j in (("vpd", 3), ("erc", 4), ("fm100", 5), ("pr", 6)):
        cols[k + "_t"] = wt[j][r, c]
        cols[k + "_t1"] = wt1[j][r, c]
    for k in FEATS:
        if k not in cols:
            cols[k] = S[k][r, c]
    X = np.stack([np.asarray(cols[k], np.float32) for k in FEATS], 1)
    return X, r, c, new, ux.astype(np.float32), uy.astype(np.float32)


def fire_series(conv, year):
    """(fire values with HIST days of the previous year prepended (zeros if none), clear, label, mask)."""
    z = np.load(os.path.join(TENSORS, conv, "%d.npz" % year))
    f = z["x"][:, 0].astype(np.float32)
    prev = np.zeros((HIST, NY, NX), np.float32)
    pp = os.path.join(TENSORS, conv, "%d.npz" % (year - 1))
    if os.path.exists(pp):
        prev = np.load(pp)["x"][-HIST:, 0].astype(np.float32)
    return np.concatenate([prev, f]), z["x"][:, 10] > 0, z["label"], z["mask"] > 0


def build(conv, year):
    path = os.path.join(OUT, "%s_%d.npz" % (conv, year))
    if os.path.exists(path):
        return
    t0 = time.time()
    S = static_grid()
    f, clear, label, mask = fire_series(conv, year)
    w = np.load(os.path.join(TENSORS, "weather", "%d.npz" % year))["w"]
    n = days_in(year)
    thr = threshold(conv)
    parts = []
    for d in range(n):
        if not mask[d].any():
            continue
        k = HIST + d
        X, r, c, new, _, _ = day_rows(f[k - HIST:k + 1], w[d].astype(np.float32), w[d + 1].astype(np.float32), S, thr,
                                      ok=mask[d], clear_t=clear[d], doy=d + 1, ndays=n)
        if len(r):
            parts.append((X, np.full(len(r), d, np.int16), r.astype(np.int16), c.astype(np.int16), new,
                          label[d][r, c].astype(np.float32)))
    X, d, r, c, new, y = (np.concatenate([p[i] for p in parts]) for i in range(6))
    os.makedirs(OUT, exist_ok=True)
    np.savez_compressed(path + ".tmp.npz", X=X, d=d, r=r, c=c, new=new, y=y, feats=np.array(FEATS), year=year)
    os.replace(path + ".tmp.npz", path)
    log.info("%s %d: %d edge rows, mean label %.4f, new %.3f (%.0fs)", conv, year, len(y), y.mean(), new.mean(),
             time.time() - t0)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "build":
        for yy in sys.argv[3:]:
            build(sys.argv[2], int(yy))
    else:
        sys.exit(__doc__)
