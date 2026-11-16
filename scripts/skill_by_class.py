#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Does the best "where" model depend on the kind of fire? Splits the held-out Aug 3-5 fire-days (where_skill results)
by quantities known at issue time only: fire size at issue (worst-case classes, 10 / 100 km2), mean HRRR wind, and
ELMFIRE's own predicted 12 h spread (reached cells / fire cells). Per group: mean recall at observed area for ELMFIRE,
buffer, downwind, ELMFIRE - buffer with a fire-day bootstrap 95% CI. Writes data/skill_by_class/results.{md,json}.
Usage: scripts/skill_by_class.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/skill_by_class"
DAYS = ["20260803", "20260804", "20260805"]


def one_day(day):
    sys.path.insert(0, str(ROOT / "scripts"))
    import where_skill as ws
    res = json.loads((ROOT / f"data/where_skill/{day}/results.json").read_text())["per_fire"]["primary"]
    man = {e["fire"]: e for e in json.loads((ROOT / f"data/where_skill/{day}/manifest.json").read_text())["primary"]}
    rows = []
    for fire, r in res.items():
        if not r:
            continue
        P = ws.prepare(fire, ROOT / man[fire]["run"])
        cell_km2 = (r["cell_m"] / 1000) ** 2
        size = float(P["start"].sum() * cell_km2)
        rows.append(dict(fire=fire, day=day, size_km2=size, wind_mph=r["wind"]["mean_wind_mph"],
                         spread_ratio=r["elmfire_reach_cells"] * cell_km2 / max(size, 1e-6),
                         **{m: r["methods"][m]["recall"] for m in ("elmfire", "buffer", "downwind")}))
    (OUT / f"rows_{day}.json").write_text(json.dumps(rows))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for day in DAYS:
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}T12:00"
        subprocess.run([sys.executable, __file__, "day", day], check=True, env=dict(os.environ, WHERE_ISSUE=iso))
        rows += json.loads((OUT / f"rows_{day}.json").read_text())
    rng = np.random.default_rng(0)
    A = {k: np.array([r[k] for r in rows]) for k in rows[0] if k not in ("fire", "day")}
    wq = np.quantile(A["wind_mph"], [1 / 3, 2 / 3])
    sq = np.quantile(A["spread_ratio"], [1 / 3, 2 / 3])
    splits = {
        "all": [("all", np.ones(len(rows), bool))],
        "fire size at issue": [("small < 10 km2", A["size_km2"] < 10), ("medium 10-100 km2", (A["size_km2"] >= 10) & (A["size_km2"] < 100)),
                               ("large >= 100 km2", A["size_km2"] >= 100)],
        "mean wind": [(f"calm < {wq[0]:.1f} mph", A["wind_mph"] < wq[0]), (f"mid", (A["wind_mph"] >= wq[0]) & (A["wind_mph"] < wq[1])),
                      (f"windy >= {wq[1]:.1f} mph", A["wind_mph"] >= wq[1])],
        "ELMFIRE predicted spread (reach / size)": [(f"low < {sq[0]:.2f}", A["spread_ratio"] < sq[0]),
                                                   ("mid", (A["spread_ratio"] >= sq[0]) & (A["spread_ratio"] < sq[1])),
                                                   (f"high >= {sq[1]:.2f}", A["spread_ratio"] >= sq[1])],
    }
    md = ["# Where skill by fire type (held-out Aug 3-5, uncontained), split on issue-time quantities only", "",
          "Mean recall at observed area. Wind and spread split at tertiles of these fire-days.", "",
          "| split | group | n | ELMFIRE | buffer | downwind | ELMFIRE - buffer (95% CI) | ELMFIRE - downwind (95% CI) | best |", "|---|---|---|---|---|---|---|---|---|"]
    js = []
    for name, groups in splits.items():
        for label, m in groups:
            n = int(m.sum())
            if n < 3:
                md.append(f"| {name} | {label} | {n} | | | | | | |")
                continue
            e, b, d = A["elmfire"][m], A["buffer"][m], A["downwind"][m]
            def ci(x):
                bs = [x[rng.integers(0, n, n)].mean() for _ in range(5000)]
                return np.percentile(bs, [2.5, 97.5])
            cb, cd = ci(e - b), ci(e - d)
            best = max((("ELMFIRE", e.mean()), ("buffer", b.mean()), ("downwind", d.mean())), key=lambda t: t[1])[0]
            md.append(f"| {name} | {label} | {n} | {e.mean():.3f} | {b.mean():.3f} | {d.mean():.3f} | {(e - b).mean():+.3f} [{cb[0]:+.3f}, {cb[1]:+.3f}] "
                      f"| {(e - d).mean():+.3f} [{cd[0]:+.3f}, {cd[1]:+.3f}] | {best} |")
            js.append(dict(split=name, group=label, n=n, elmfire=e.mean(), buffer=b.mean(), downwind=d.mean(),
                           elm_minus_buffer_ci=list(cb), elm_minus_downwind_ci=list(cd)))
    (OUT / "results.md").write_text("\n".join(md) + "\n")
    (OUT / "results.json").write_text(json.dumps({"groups": js, "rows": rows}, indent=1))
    print("\n".join(md))


if __name__ == "__main__":
    one_day(sys.argv[2]) if sys.argv[1:2] == ["day"] else main()
