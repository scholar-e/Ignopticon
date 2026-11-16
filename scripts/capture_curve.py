#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Worst case graded on capture: share of the observed +12 h growth inside the marked area, as the marked area grows.

For each uncontained fire-day in data/where_skill/{DAY}/manifest.json (frozen; Aug 3, 4, 5 issues, held out), cells are
ranked by where_skill.py's rules (buffer = distance, downwind = wind-stretched distance, elmfire = control arrival
then distance) plus elmfire_union = earliest arrival over the 11 ensemble members then distance. Capture is computed at
marked areas of K x the observed growth area (oracle-sized, so only the ranking is compared), K in 1, 2, 3, 5, 10.
Candidates are where_skill's domain widened to the union's reach. Pooled capture = sum hits / sum observed.
Writes data/capture_curve/results.{json,md}. Usage: scripts/capture_curve.py
"""
import json
import logging
import sys
from pathlib import Path

import os

import numpy as np

# where_skill reads WHERE_ISSUE at import: one day per process (main() re-runs itself per day, then pools)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import where_skill as ws  # noqa: E402
from selfcal_adj import load_toa  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/capture_curve"
DAYS = ["20260803", "20260804", "20260805"]
KS = [1, 2, 3, 5, 10]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)


def union_toa(run, shape):
    toa = np.full(shape, np.inf)
    for d in sorted(Path(run).parent.parent.glob("*/outputs")):
        t, v, *_ = load_toa(d)
        if t.shape == shape:
            toa = np.minimum(toa, np.where(v & (t > 0), t, np.inf))
    return toa


def fire_day(e):
    P = ws.prepare(e["fire"], ROOT / e["run"])
    if P is None or not P["obs"].any():
        return None
    ut = union_toa(ROOT / e["run"], P["toa"].shape)
    ureach = np.isfinite(ut) & (ut <= 12 * 3600) & ~P["start"]
    base = P["domain"] | (ureach & (P["dist"] > 0))
    P["domain"] = base
    keys, _ = ws.rank_keys(P, e["fire"])
    m = base
    d = P["dist"][m]
    t = np.where(ureach[m], ut[m], np.inf)
    order = np.lexsort((d, t))
    r = np.empty(len(d)); r[order] = -np.arange(len(d), dtype=float)
    keys["elmfire_union"] = r
    obs = (P["obs"] | (P["truth"] & ~P["start"] & base))[m]
    n = int(obs.sum())
    out = {"fire": e["fire"], "obs": n, "cand": int(len(obs))}
    for k, s in keys.items():
        top = np.argsort(-s, kind="stable")
        out[k] = [int(obs[top[:min(len(top), K * n)]].sum()) for K in KS]
    return out


def one_day(day):
    rows = []
    man = json.loads((ROOT / "data/where_skill" / day / "manifest.json").read_text())
    for e in man["primary"]:
        r = fire_day(e)
        if r:
            r["day"] = day
            rows.append(r)
            log.info("%s %s", e["fire"], {k: round(r[k][2] / r["obs"], 2) for k in ("buffer", "elmfire", "elmfire_union")})
    (OUT / f"rows_{day}.json").write_text(json.dumps(rows))


def main():
    import subprocess
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for day in DAYS:
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}T12:00"
        subprocess.run([sys.executable, __file__, "day", day], check=True, env=dict(os.environ, WHERE_ISSUE=iso))
        rows += json.loads((OUT / f"rows_{day}.json").read_text())
    methods = ["buffer", "downwind", "elmfire", "elmfire_union"]
    rng = np.random.default_rng(0)
    md = ["# Capture of observed +12 h growth vs marked area (K x observed area), held-out Aug 3-5 issues", "",
          f"n = {len(rows)} fire-days. Pooled capture = hits / observed; mean = per-fire mean. 95% CI by fire-day bootstrap.", "",
          "| K | " + " | ".join(f"{m} pooled / mean" for m in methods) + " | union - buffer pooled (95% CI) | fires union > buffer |",
          "|---|" + "---|" * (len(methods) + 2)]
    res = {}
    obs = np.array([r["obs"] for r in rows], float)
    for i, K in enumerate(KS):
        h = {m: np.array([r[m][i] for r in rows], float) for m in methods}
        pooled = {m: h[m].sum() / obs.sum() for m in methods}
        mean = {m: float(np.mean(h[m] / obs)) for m in methods}
        bs = []
        for _ in range(5000):
            j = rng.integers(0, len(rows), len(rows))
            bs.append((h["elmfire_union"][j].sum() - h["buffer"][j].sum()) / obs[j].sum())
        lo, hi = np.percentile(bs, [2.5, 97.5])
        wins = int((h["elmfire_union"] > h["buffer"]).sum()), int((h["elmfire_union"] < h["buffer"]).sum())
        res[K] = dict(pooled=pooled, mean=mean, union_minus_buffer_ci=[lo, hi], wins_losses=wins)
        md.append(f"| {K} | " + " | ".join(f"{pooled[m]:.3f} / {mean[m]:.3f}" for m in methods)
                  + f" | {pooled['elmfire_union'] - pooled['buffer']:+.3f} [{lo:+.3f}, {hi:+.3f}] | {wins[0]} / {wins[1]} (ties {len(rows) - sum(wins)}) |")
    (OUT / "results.md").write_text("\n".join(md) + "\n")
    (OUT / "results.json").write_text(json.dumps({"ks": KS, "summary": res, "rows": rows}, indent=1))
    print("\n".join(md))


if __name__ == "__main__":
    one_day(sys.argv[2]) if sys.argv[1:2] == ["day"] else main()
