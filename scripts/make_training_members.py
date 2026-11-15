#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio"]
# ///
"""ELMFIRE training members for built fires (build_fire_case.py): one line per run, the arguments of
scripts/elmfire_forecast.sh (as in data/elmfire/widemouth/ab3-queue.txt), for xargs locally or
hyak/submit_members.sh on Tillicum.

Every run starts at a 12Z issue from the VIIRS burning edges, leak-free (OBSERVED=, elmfire_case.py), with that
issue's HRRR run, for 24 h. Members per fire-issue (data/training/NOTES.md):
  u-*   uniform spread multiplier (today's plain ELMFIRE, the baseline): ADJ x WSMULT x MOIST
        (ADJ 0.01 0.02 0.05 0.1 0.2 0.5 1.0, WSMULT 0.6 1.0, MOIST +0 +4 points), live herbaceous moisture 90%
  lh30-* live herbaceous 30% at ADJ 0.1
  g-*   per-fuel-group multipliers, two levels (0.03, 0.3) for each of grass/grass-shrub, shrub and timber present
        on >= GROUP_MIN of the fire's burned cells (other cells at the base 0.1): up to 8 members
  w-*   a wind term, ADJ x (cell mean wind / 10 mph) ** b, b = -1 and +1, at ADJ 0.03 and 0.3
The pilot's first fire had its best uniform ADJ at the top of 0.01-0.5, hence 1.0 and the 0.03/0.3 levels.

Usage: make_training_members.py OUT.txt FIRE_ID [FIRE_ID ...]     (or --ready: every fire whose case.json is ready)
Writes OUT.txt (case dirs data/training/runs/FIRE/YYYYMMDDTHH/MEMBER, parent dirs created) and prints counts.
"""
import glob
import json
import os
import sys

import numpy as np
import rasterio

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRES = "data/training/fires"
RUNS = "data/training/runs"
ADJS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
WSMULTS = [0.6, 1.0]
MOISTS = [0, 4]
LH = 90
GROUP_LEVELS = [0.03, 0.3]
GROUP_BASE = 0.1
GROUP_MIN = 0.10
GROUPS = {"ADJ_GR": list(range(101, 110)) + list(range(121, 125)), "ADJ_SH": list(range(141, 150)),
          "ADJ_TIMBER": list(range(161, 166)) + list(range(181, 190)) + list(range(201, 205))}
SHORT = {"ADJ_GR": "gr", "ADJ_SH": "sh", "ADJ_TIMBER": "tb"}


def groups_present(fire_dir, refine):
    """Fuel groups on >= GROUP_MIN of the burnable fine cells inside the fire's burned state cells."""
    with rasterio.open(os.path.join(fire_dir, "fire-state.tif")) as src:
        state = src.read(1)
        burned = state != src.nodata
    with rasterio.open(os.path.join(fire_dir, "landscape.tif")) as src:
        fuel = src.read(4)
    mask = np.kron(burned, np.ones((refine, refine), bool))[:fuel.shape[0], :fuel.shape[1]]
    codes = fuel[mask]
    codes = codes[(codes >= 100) & (codes < 300)]
    if not len(codes):
        return [], {}
    frac = {g: float(np.isin(codes, v).mean()) for g, v in GROUPS.items()}
    return [g for g, f in frac.items() if f >= GROUP_MIN], frac


def members(present):
    out = []
    for adj in ADJS:
        for ws in WSMULTS:
            for m in MOISTS:
                out.append(("u-adj%g-ws%g-m%g" % (adj, ws, m),
                            ["ADJ=%g" % adj, "WSMULT=%g" % ws, "MOIST=%g" % m, "LH_MOISTURE_CONTENT=%d" % LH]))
    for adj in (0.1,):
        out.append(("lh30-adj%g" % adj, ["ADJ=%g" % adj, "LH_MOISTURE_CONTENT=30"]))
    if present:
        levels = np.array(np.meshgrid(*[GROUP_LEVELS] * len(present))).reshape(len(present), -1).T
        for combo in levels:
            name = "g-" + "-".join("%s%g" % (SHORT[g], v) for g, v in zip(present, combo))
            out.append((name, ["ADJ=%g" % GROUP_BASE, "LH_MOISTURE_CONTENT=%d" % LH]
                        + ["%s=%g" % (g, v) for g, v in zip(present, combo)]))
    for adj in (0.03, 0.3):
        for b in (-1, 1):
            out.append(("w-adj%g-e%+d" % (adj, b), ["ADJ=%g" % adj, "ADJ_WIND_EXP=%d" % b,
                                                   "LH_MOISTURE_CONTENT=%d" % LH]))
    return out


def main(out_txt, *fires):
    os.chdir(REPO)
    if fires == ("--ready",):
        fires = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(FIRES, "*", "case.json"))
                       if json.load(open(p)).get("ready"))
    lines, summary = [], []
    for fire in fires:
        fire_dir = os.path.join(FIRES, fire)
        meta = json.load(open(os.path.join(fire_dir, "case.json")))
        if not meta.get("ready"):
            print("%s: not ready, skipped" % fire)
            continue
        present, frac = groups_present(fire_dir, meta["refine"])
        mem = members(present)
        meta["fuel_groups_burned"] = {k: round(v, 3) for k, v in frac.items()}
        meta["groups_varied"] = present
        json.dump(meta, open(os.path.join(fire_dir, "case.json"), "w"), indent=1)
        for item in meta["issues"]:
            stamp = item["issue"][:13].replace("-", "").replace(":", "")
            for name, overrides in mem:
                case = os.path.join(RUNS, fire, stamp, name)
                os.makedirs(os.path.dirname(case), exist_ok=True)
                lines.append(" ".join([os.path.join(fire_dir, "landscape.tif"), item["wx"],
                                       os.path.join(fire_dir, "fire-state.tif"), item["issue"], "24", case,
                                       "OBSERVED=" + item["observed"]] + overrides))
        summary.append("%s: %d issues x %d members (groups varied: %s)" % (fire, len(meta["issues"]), len(mem),
                                                                            ",".join(present) or "none"))
    with open(out_txt, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    print("\n".join(summary))
    print("%s: %d runs" % (out_txt, len(lines)))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
