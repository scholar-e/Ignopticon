#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Compares plain ELMFIRE forecasts (A) with forecasts updated at a satellite pass (B), on the same overpasses.

For each forecast window (an issue time with a 24 h horizon), B restarts at the window's update time from the
burning edges and burned area known then, and re-picks its parameters: the member whose A run scored best (pooled
CSI) on the overpasses between issue and update. A's parameters are fixed per window by leave-one-window-out: the
member with the best pooled CSI over the other windows' full horizons. Both, and no growth from the update time,
are scored on the overpasses after the update and within the horizon: pooled CSI = hits / (hits + misses + false
alarms) over those passes, plus IoU with the FEDS perimeter at +24 h.

Inputs are heat_frames.py OUT.json files (their per-overpass hits/misses/false alarms):
    A_PATTERN, B_PATTERN with {day}, e.g. data/elmfire/widemouth/score-a2-08{day}.json, one per window, whose runs
    are named by member (e.g. adj0.05); the B file also holds a "nogrowth" run.
Usage: compare_ab.py PLAN.json A_PATTERN B_PATTERN [MEMBER ...]
       PLAN.json from the cycle setup: {day: {issue, update, scored_passes}}. MEMBERs restrict A's and B's choice
       (default: the members present in every window's A and B files).
"""
import datetime
import json
import sys


def T(s):
    return datetime.datetime.fromisoformat(s.replace("Z", ""))


def pooled(scores, keep):
    h = m = f = 0
    for s in scores:
        if keep(T(s["time"])):
            h, m, f = h + s["hits"], m + s["misses"], f + s["false_alarms"]
    return h, m, f


def csi(hmf):
    h, m, f = hmf
    return h / (h + m + f) if h + m + f else float("nan")


def main(plan_json, a_pattern, b_pattern, *members):
    plan = json.load(open(plan_json))
    A = {d: {r["name"]: r for r in json.load(open(a_pattern.format(day=d)))["fires"][0]["runs"]} for d in plan}
    B = {d: {r["name"]: r for r in json.load(open(b_pattern.format(day=d)))["fires"][0]["runs"]} for d in plan}
    members = list(members) or sorted(set.intersection(*[set(A[d]) & set(B[d]) for d in plan]) - {"nogrowth"})
    print("members:", " ".join(members))
    totals = {"A": [0, 0, 0], "B": [0, 0, 0], "no growth": [0, 0, 0], "A, same member as B": [0, 0, 0]}
    rows = []
    for d, p in plan.items():
        issue, update = T(p["issue"]), T(p["update"])
        end = issue + datetime.timedelta(hours=24)
        horizon = lambda t: issue < t <= end
        early = lambda t: issue < t <= update
        late = lambda t: update < t <= end
        # A: leave-one-window-out choice on the other windows' full horizons.
        def other_csi(m):
            tot = [0, 0, 0]
            for d2, p2 in plan.items():
                if d2 == d:
                    continue
                i2 = T(p2["issue"])
                hmf = pooled(A[d2][m]["scores"], lambda t: i2 < t <= i2 + datetime.timedelta(hours=24))
                tot = [x + y for x, y in zip(tot, hmf)]
            return csi(tot)
        a_pick = max(members, key=other_csi)
        # B: the member whose A run did best on the passes before the update.
        b_pick = max(members, key=lambda m: csi(pooled(A[d][m]["scores"], early)))
        res = {
            "A": pooled(A[d][a_pick]["scores"], late),
            "B": pooled(B[d][b_pick]["scores"], late),
            "no growth": pooled(B[d]["nogrowth"]["scores"], late),
            "A, same member as B": pooled(A[d][b_pick]["scores"], late),
        }
        iou = {k: next((q["iou"] for q in run["perimeters"] if T(q["time"]) == end), float("nan"))
               for k, run in (("A", A[d][a_pick]), ("B", B[d][b_pick]), ("no growth", B[d]["nogrowth"]))}
        for k, v in res.items():
            totals[k] = [x + y for x, y in zip(totals[k], v)]
        n = sum(1 for s in A[d][a_pick]["scores"] if late(T(s["time"])))
        rows.append((d, a_pick, b_pick, n, {k: csi(v) for k, v in res.items()}, iou))
    print("%-4s %-10s %-10s %3s  %6s %6s %6s %8s   IoU+24h A / B / none" % (
        "day", "A member", "B member", "n", "A", "B", "none", "A(Bmem)"))
    for d, ap, bp, n, c, iou in rows:
        print("%-4s %-10s %-10s %3d  %6.3f %6.3f %6.3f %8.3f   %.2f / %.2f / %.2f" % (
            d, ap, bp, n, c["A"], c["B"], c["no growth"], c["A, same member as B"], iou["A"], iou["B"],
            iou["no growth"]))
    print("pooled: " + ", ".join("%s %.3f" % (k, csi(v)) for k, v in totals.items()))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
