#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas", "tabulate"]
# ///
"""Split existing scores by containment status at issue (data/containment/aug2_containment.json). Evaluation only."""
import json, glob, os
import numpy as np, pandas as pd

C = json.load(open("data/containment/aug2_containment.json"))["fires"]
status = lambda fire: C.get(fire.split("-")[-1], {}).get("status", "unmatched")
ORDER = ["contained", "uncontained", "unmatched"]
out = ["# Scores split by containment status at issue (2026-08-02T12:00Z, dates only)", "",
       "Status from WFIGS dates (scripts/containment_aug2.py); no forecast was changed. Means are per-fire; n = fires. "
       "Area ratio = predicted / observed growth area (median of fires with observed growth). "
       "Growth IoU / recall are over fires with observed growth only (n_g).", ""]

# A. held-out 12
d = pd.read_csv("data/bounds/heldout_per_fire.csv")
d = d[d.case != "case"].copy()
for c in d.columns[1:]:
    if c != "case":
        d[c] = pd.to_numeric(d[c], errors="coerce")
d["status"] = d.fire.map(status)
d["ratio"] = np.where(d.obs_km2 > 0, d.pred_km2 / d.obs_km2, np.nan)
rows = []
for st in ORDER:
    for case in ["no_growth", "control", "lower", "upper_harm", "upper_raw"]:
        g = d[(d.status == st) & (d.case == case)]
        if not len(g):
            continue
        gg = g[g.obs_km2 > 0]
        rows.append({"status": st, "case": case, "n": len(g), "n_g": len(gg), "growth_iou": gg.growth_iou.mean(),
                     "recall": gg.recall.mean(), "whole_iou": g.whole_iou.mean(), "area_ratio_med": gg.ratio.median(),
                     "pred_km2": g.pred_km2.sum(), "obs_km2": g.obs_km2.sum()})
out += ["## A. Frozen 12 held-out fires (+12 h vs FEDS)", "",
        pd.DataFrame(rows).to_markdown(index=False, floatfmt=".3f"), "",
        "Fires: " + ", ".join(f"{s} {d[(d.status == s) & (d.case == 'control')].fire.str[-5:].tolist()}" for s in ORDER), ""]

# B. Aug 2 batch ensembles with +12 h truth (control member only; recall not stored in scores.json)
rows = []
for p in glob.glob("data/perimeter_batch/map*/scores.json"):
    fire = os.path.basename(os.path.dirname(p))
    s = json.load(open(p))["scores"]
    key = "adj1" if "adj1" in s else "gr1-sh1-tm0.6-we0"
    r = [x for x in s[key] if x["hours"] == 12.0]
    if not r:
        continue
    r = r[0]
    obs = r["observed_growth_cells"]
    rows.append({"fire": fire, "status": status(fire), "obs": obs, "growth_iou": r["growth_iou"], "whole_iou": r["iou"],
                 "area_ratio": r["area_ratio"], "no_growth_iou": r["no_growth_iou"]})
b = pd.DataFrame(rows)
res = []
for st in ORDER:
    g = b[b.status == st]
    if not len(g):
        continue
    gg = g[g.obs > 0]
    res.append({"status": st, "n": len(g), "n_g": len(gg), "control growth_iou": gg.growth_iou.mean(),
                "control whole_iou": g.whole_iou.mean(), "control area_ratio_med": gg.area_ratio.median(),
                "no_growth whole_iou": g.no_growth_iou.mean(), "no_growth growth_iou": 0.0 if len(gg) else np.nan})
out += ["## B. Aug 2 forecasts with FEDS truth at +12 h (perimeter_batch/*/scores.json)", "",
        "Control = member adj1 (physics3 fires) or gr1-sh1-tm0.6-we0 (fuelwind16 fires; closest to default). "
        "Lower/upper bounds and recall are not stored for these, only for section A. Includes the held-out fires that "
        "were run in perimeter_batch.", "", pd.DataFrame(res).to_markdown(index=False, floatfmt=".3f"), ""]
out += ["Caveats: status is from dates only (a fire with a real line but no date counts as uncontained); WFIGS match is by "
        "origin within ~5 km of the FEDS outline; small n, so differences are descriptive, not tested.", ""]
open("data/containment/split_table.md", "w").write("\n".join(out))
print("\n".join(out))
