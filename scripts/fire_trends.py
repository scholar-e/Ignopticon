#!/usr/bin/env python3
"""Fire-season trends 2003-2025, raw vs harmonized, per History-page region.

Input: earth/public/data/calendar/{region}.json (daily raw / harm cell-day series from scripts/history_calendar.py).
Per region and year: annual fire-active cell-days, season start/end (day of year when cumulative activity passes 10% / 90%),
season length, active days (daily value >= 1 cell). Days with no satellite data count as zero for timing; annual totals and
active days are scaled to a full year like the calendar does. Per metric: Theil-Sen slope + Mann-Kendall p over 2003-2025,
and a 2012 step check: mean difference 2012-2025 minus 2003-2011 (permutation p over year labels, 20000 draws) and the
Pettitt change point (year, approximate p).
Outputs: earth/public/data/trends/{region}.json, data/trends/trends_table.md.  Usage: scripts/fire_trends.py
"""
import json, logging
from pathlib import Path
import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
CAL = ROOT / "earth/public/data/calendar"
OUT = ROOT / "earth/public/data/trends"
YEARS = list(range(2003, 2026))
METRICS = ["activity", "start", "end", "length", "active_days"]
LABEL = {"activity": "annual activity (cell-days/yr)", "start": "season start (doy)", "end": "season end (doy)",
         "length": "season length (days)", "active_days": "active days (/yr)"}
log = logging.getLogger("trends")
rng = np.random.default_rng(12)


def year_metrics(a, of_days):
    """a: daily values with None for missing days."""
    v = np.array([np.nan if x is None else x for x in a], float)
    have = np.isfinite(v); n = int(have.sum())
    k = len(v) / n
    z = np.where(have, v, 0.0)
    tot = z.sum()
    c = np.cumsum(z) / tot
    start = int(np.searchsorted(c, 0.10)) + 1; end = int(np.searchsorted(c, 0.90)) + 1
    return {"activity": tot * k, "start": start, "end": end, "length": end - start, "active_days": float((z >= 1).sum() * k)}


def pettitt(y):
    n = len(y); s = np.sign(y[:, None] - y[None, :])
    U = np.array([s[:t + 1, t + 1:].sum() for t in range(n - 1)])
    t = int(np.abs(U).argmax()); K = abs(U[t])
    return t, float(min(1.0, 2 * np.exp(-6 * K ** 2 / (n ** 3 + n ** 2))))


def analyse(y):
    y = np.asarray(y, float); x = np.array(YEARS, float)
    sl, ic, lo, hi = stats.theilslopes(y, x)
    tau, p = stats.kendalltau(x, y)
    pre = x < 2012
    d = y[~pre].mean() - y[pre].mean()
    perm = np.empty(20000)
    for i in range(len(perm)):
        q = rng.permutation(y); perm[i] = q[~pre].mean() - q[pre].mean()
    pp = float((np.abs(perm) >= abs(d)).mean())
    t, pk = pettitt(y)
    # step at 2012 after removing the Theil-Sen trend fitted on 2012-2025 only is not used: keep it simple and honest.
    return {"slope": float(sl), "slope_lo": float(lo), "slope_hi": float(hi), "intercept": float(ic), "tau": float(tau), "p": float(p),
            "step_2012": float(d), "step_p": pp, "pettitt_year": YEARS[t + 1], "pettitt_p": pk}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(filename)s:%(lineno)d %(message)s")
    OUT.mkdir(parents=True, exist_ok=True)
    regions = json.loads((CAL / "regions.json").read_text())
    rows, res = [], {}
    for r in regions:
        d = json.loads((CAL / f"{r['id']}.json").read_text())
        doc = {"id": r["id"], "name": r["name"], "years": YEARS, "metrics": {}}
        for mode in ("raw", "harm"):
            per = [year_metrics(d["daily"][str(y)][mode], None) for y in YEARS]
            for m in METRICS:
                vals = [p[m] for p in per]
                doc["metrics"].setdefault(m, {})[mode] = {"values": [round(v, 1) for v in vals], **{k: round(v, 5) if isinstance(v, float) else v for k, v in analyse(vals).items()}}
        (OUT / f"{r['id']}.json").write_text(json.dumps(doc, separators=(",", ":")))
        res[r["id"]] = doc
        log.info("wrote %s", r["id"])
    L = ["# Fire trends 2003-2025, raw vs harmonized", "",
         "Source: daily fire-active 0.1 degree cell-days from earth/public/data/calendar (scripts/fire_trends.py). Slope = Theil-Sen per year, p = Mann-Kendall. "
         "Step = mean(2012-2025) minus mean(2003-2011), permutation p. Pettitt = most likely change year (approximate p). "
         "Raw = MODIS Terra+Aqua to 2011, VIIRS after; harm = expected VIIRS-equivalent cells before 2012, real VIIRS after.", ""]
    for m in METRICS:
        L += [f"## {LABEL[m]}", "", "| region | series | slope/yr (95% CI) | MK p | step 2012 | step p | Pettitt year (p) |", "|---|---|---|---|---|---|---|"]
        for r in regions:
            for mode in ("raw", "harm"):
                a = res[r["id"]]["metrics"][m][mode]
                f = (lambda v: f"{v:,.1f}") if m != "activity" else (lambda v: f"{v:,.0f}")
                L.append(f"| {r['id']} | {mode} | {f(a['slope'])} ({f(a['slope_lo'])} to {f(a['slope_hi'])}) | {a['p']:.3f} | {f(a['step_2012'])} | {a['step_p']:.3f} | {a['pettitt_year']} ({a['pettitt_p']:.3f}) |")
        L.append("")
    (ROOT / "data/trends/trends_table.md").write_text("\n".join(L))
    # figure
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    us = res["us"]["metrics"]
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
    for mode, col in (("raw", "#d98600"), ("harm", "#1b8fc4")):
        for k, (m, a) in enumerate((("activity", ax[0]), ("length", ax[1]))):
            s = us[m][mode]; a.plot(YEARS, s["values"], "o-", color=col, label=f"{'Raw' if mode == 'raw' else 'Harmonized'}: {s['slope']:+,.1f}/yr (p={s['p']:.3f})")
            a.plot(YEARS, s["intercept"] + s["slope"] * np.array(YEARS), "--", color=col, alpha=.7)
    for a, t in zip(ax, ("US annual fire-active cell-days", "US fire-season length (10% to 90% of activity), days")):
        a.axvline(2011.5, color="#555", ls=":"); a.set_title(t); a.set_xlabel("year (dotted: MODIS | VIIRS, 2012)"); a.legend(fontsize=8); a.grid(alpha=.25)
    fig.suptitle("Raw vs harmonized, 2003-2025 (dashed: Theil-Sen trend)"); fig.tight_layout()
    fig.savefig(ROOT / "data/trends/figures/raw_vs_harmonized_us.png", dpi=130)


if __name__ == "__main__":
    main()
