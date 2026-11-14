#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow"]
# ///
"""Exports the 0.1 degree fire grids (gridded_fire_grid.py) and static layers as small browser-friendly files for the
UI: one PNG per product and day covering the whole grid, plus a JSON list of the cells with fire. See
docs/DATASETS.md ("UI export format") for the encoding.

Grid: 590 x 260 px, pixel (x, y) = cell (col x, row y): lon -125 + 0.1 x .. + 0.1, lat 50 - 0.1 y .. - 0.1 (north up,
plate carree). PNG channels (RGBA, 8 bit):
    R  fire pixels in the cell that day (1 km sensor pixels classed fire, any confidence; capped at 255)
    G  clear looks: 1 km pixels seen clearly (fire, land or water), the count itself (a 0.1 degree cell holds
       ~92-132 1 km pixels depending on latitude, so it fits; capped at 255)
    B  summed fire radiative power: round(40 * log10(1 + FRP_MW)), capped at 255 (B = 40 is ~9 MW, 120 is ~1 GW)
    A  255 where the sensor saw the cell clearly at all that day, 0 where it did not ("not observed": cloud or no pass)
JSON per day: {"product", "date", "fires": [[col, row, fire_pixels, fire_pixels_nominal_or_high, clear, frp_mw], ...]}.

Usage: export_ui.py fires GRID_DIR PRODUCT YEAR FIRST_DOY LAST_DOY OUT_DIR
       export_ui.py elevation ELEVATION.npz OUT_DIR
       export_ui.py index OUT_DIR           (writes OUT_DIR/index.json listing what is there)
"""
import datetime
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

NY, NX = 260, 590
GRID = {"cols": NX, "rows": NY, "cell_deg": 0.1, "west": -125.0, "north": 50.0, "east": -66.0, "south": 24.0,
        "crs": "EPSG:4326 (plate carree), north up; pixel (x, y) = cell (col, row)"}


def fires(grid_dir, product, year, first, last, out_dir):
    z = np.load(os.path.join(grid_dir, "%s_%s.npz" % (product, year)))
    fire, nh, clear, frp = z["fire"], z["fire_nh"], z["clear"], z["frp"]
    year = int(year)
    d_out = os.path.join(out_dir, product)
    os.makedirs(d_out, exist_ok=True)
    for doy in range(int(first), int(last) + 1):
        d = doy - 1
        date = datetime.date(year, 1, 1) + datetime.timedelta(days=d)
        rgba = np.zeros((NY, NX, 4), np.uint8)
        rgba[..., 0] = np.minimum(fire[d], 255)
        rgba[..., 1] = np.minimum(clear[d], 255)
        rgba[..., 2] = np.minimum(np.round(40 * np.log10(1 + frp[d])), 255)
        rgba[..., 3] = np.where(clear[d] > 0, 255, 0)
        stem = os.path.join(d_out, date.isoformat())
        Image.fromarray(rgba, "RGBA").save(stem + ".png", optimize=True)
        r, c = np.nonzero(fire[d])
        json.dump({"product": product, "date": date.isoformat(), "fires": [
            [int(cc), int(rr), int(fire[d, rr, cc]), int(nh[d, rr, cc]), int(clear[d, rr, cc]),
             round(float(frp[d, rr, cc]), 1)] for rr, cc in zip(r, c)]},
            open(stem + ".json", "w"), separators=(",", ":"))
    print("%s %d: days %s-%s written to %s" % (product, year, first, last, d_out))


def elevation(path, out_dir):
    z = np.load(path)
    elev, sd = z["elev"], z["elev_sd"]
    os.makedirs(out_dir, exist_ok=True)
    # R,G = elevation in metres + 1000 as a 16-bit number (R high byte); B = roughness (sd, m) / 4; A = 0 no data.
    e = np.where(np.isfinite(elev), np.round(elev + 1000), 0).astype(np.uint16)
    rgba = np.zeros((NY, NX, 4), np.uint8)
    rgba[..., 0], rgba[..., 1] = e >> 8, e & 255
    rgba[..., 2] = np.where(np.isfinite(sd), np.minimum(np.round(sd / 4), 255), 0)
    rgba[..., 3] = np.where(np.isfinite(elev), 255, 0)
    Image.fromarray(rgba, "RGBA").save(os.path.join(out_dir, "elevation.png"), optimize=True)
    print("elevation written to %s" % out_dir)


def index(out_dir):
    products = {}
    for d in sorted(glob.glob(os.path.join(out_dir, "*", ""))):
        dates = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(d, "*.png")))
        if dates:
            products[os.path.basename(d.rstrip("/"))] = {"first": dates[0], "last": dates[-1], "dates": dates}
    json.dump({"grid": GRID, "products": products,
               "static": [os.path.basename(p) for p in glob.glob(os.path.join(out_dir, "*.png"))],
               "encoding": "see docs/DATASETS.md, 'UI export format'"},
              open(os.path.join(out_dir, "index.json"), "w"), indent=1)
    print("index: %s" % ", ".join("%s %d days" % (k, len(v["dates"])) for k, v in products.items()))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "fires" and len(a) == 7:
        fires(*a[1:])
    elif a and a[0] == "elevation" and len(a) == 3:
        elevation(*a[1:])
    elif a and a[0] == "index" and len(a) == 2:
        index(a[1])
    else:
        sys.exit(__doc__)
