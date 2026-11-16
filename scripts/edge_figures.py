#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "matplotlib", "scikit-learn"]
# ///
"""Fire-level view of the edge-advance model (MODELLING_LOG.md entry 19), from edge_model.py outputs.

For each evaluation fire in data/compare/fires.csv (test 2021-2024): edge rows inside the bbox (+0.3 deg) with t+1 in
the fire's dates; AP and CSI (threshold chosen on 2020 per arm) by arm -> data/edge/fires.json/.md. Figures in
data/edge/figures/: {fire}.png (day t VIIRS fire, P(advance) on edge cells for harm-all and raw-all, observed t+1,
t+1 wind arrow) on the fire's day with the most advancing edge cells; overview.png (AP by arm, overall / new fire /
per fire); reliability.png.
Usage: edge_figures.py
"""
import csv
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edge_model import csi, metrics  # noqa: E402

ROOT = "data/edge"
FIG = os.path.join(ROOT, "figures")
TENSORS = "data/compare/tensors"
ARMS = ["raw-pre", "harm-pre", "raw-all", "harm-all", "viirs-only", "wind-logistic", "base-rate"]
COL = {"raw-pre": "#d95f02", "harm-pre": "#1b9e77", "raw-all": "#e7298a", "harm-all": "#7570b3",
       "viirs-only": "#444444", "wind-logistic": "#a6761d", "base-rate": "#bbbbbb"}
MARGIN = 0.3


def rc_box(w, s, e, n):
    return (int((50 - n - MARGIN) / 0.1), int(np.ceil((50 - s + MARGIN) / 0.1)),
            int((w - MARGIN + 125) / 0.1), int(np.ceil((e + MARGIN + 125) / 0.1)))


def main():
    os.makedirs(FIG, exist_ok=True)
    T = np.load(os.path.join(ROOT, "preds", "test.npz"))
    res = json.load(open(os.path.join(ROOT, "results.json")))
    arms = [a for a in ARMS if "p_" + a in T.files]
    y, yr, d, r, c = T["y"], T["year"], T["d"].astype(int), T["r"].astype(int), T["c"].astype(int)
    P = {a: T["p_" + a].astype(np.float32) for a in arms}
    tday = np.array([np.datetime64("%d-01-01" % a) for a in range(2021, 2025)])[yr - 2021] + d   # day t
    fires = list(csv.DictReader(open("data/compare/fires.csv")))
    out = []
    for f in fires:
        w, s, e, n = (float(f[k]) for k in ("west", "south", "east", "north"))
        r0, r1, c0, c1 = rc_box(w, s, e, n)
        t0, t1 = np.datetime64(f["start"]) - 1, np.datetime64(f["end"]) - 1
        m = (r >= r0) & (r < r1) & (c >= c0) & (c < c1) & (tday >= t0) & (tday <= t1)
        row = {"id": f["id"], "name": f["name"], "region": f["region"], "fuel": f["fuel_group"],
               "edges": int(m.sum()), "pos": int((y[m] > 0.5).sum()), "arms": {}}
        for a in arms:
            mm = metrics(y[m], P[a][m])
            row["arms"][a] = {"ap": mm.get("ap"), "auc": mm.get("auc"), "mean_p": mm.get("mean_p"),
                              "csi": csi(y[m], P[a][m], res["arms"][a]["threshold"]) if m.any() else None}
        out.append(row)
        if row["pos"] == 0:
            continue
        days, cnt = np.unique(tday[m][y[m] > 0.5], return_counts=True)
        td = days[np.argmax(cnt)]
        figure(f, row, td, (r0, r1, c0, c1), m & (tday == td), r, c, y, P)
    json.dump(out, open(os.path.join(ROOT, "fires.json"), "w"), indent=1)
    L = ["# Edge-advance AP / CSI per evaluation fire (test, held out)", "",
         "| Fire | Edges | Adv. | " + " | ".join(arms) + " |", "|---" * (len(arms) + 3) + "|"]
    for row in out:
        L.append("| %s %s | %d | %d | " % (row["id"], row["name"][:28], row["edges"], row["pos"]) + " | ".join(
            "%.3f / %.3f" % (row["arms"][a]["ap"], row["arms"][a]["csi"] or 0) if row["arms"][a]["ap"] is not None else "-"
            for a in arms) + " |")
    L.append("\nCells: AP / CSI at the arm's 2020 max-CSI threshold.")
    open(os.path.join(ROOT, "fires.md"), "w").write("\n".join(L) + "\n")
    overview(res, out, arms)


def figure(f, row, td, box, m, r, c, y, P):
    r0, r1, c0, c1 = box
    yy = int(str(td)[:4]); d = int((td - np.datetime64("%d-01-01" % yy)).astype(int))
    z = np.load(os.path.join(TENSORS, "viirs", "%d.npz" % yy))
    fire_t = z["x"][d, 0, r0:r1, c0:c1].astype(float); fire_t1 = z["x"][d, 1, r0:r1, c0:c1].astype(float)
    lab = z["label"][d, r0:r1, c0:c1].astype(float); msk = z["mask"][d, r0:r1, c0:c1] > 0
    wx = np.load(os.path.join(TENSORS, "weather", "%d.npz" % yy))["w"][d + 1, 1:3, r0:r1, c0:c1].astype(float)
    u, v = np.nanmean(wx[0]), np.nanmean(wx[1])
    ext = (c0 - 0.5, c1 - 0.5, r1 - 0.5, r0 - 0.5)
    fig, ax = plt.subplots(1, 4, figsize=(15, 4.2))
    base = np.where(fire_t > 0, 2, np.where(fire_t1 > 0, 1, 0)).astype(float)
    ax[0].imshow(base, cmap=matplotlib.colors.ListedColormap(["#f4f4f4", "#fdae6b", "#d7301f"]), vmin=0, vmax=2, extent=ext)
    ax[0].set_title("VIIRS fire on t=%s (red t, orange t-1)" % td, fontsize=9)
    for k, a in ((1, "harm-all"), (2, "raw-all")):
        g = np.full((r1 - r0, c1 - c0), np.nan)
        g[r[m] - r0, c[m] - c0] = P[a][m]
        ax[k].imshow(base, cmap=matplotlib.colors.ListedColormap(["#f4f4f4", "#e0e0e0", "#9e9e9e"]), vmin=0, vmax=2, extent=ext)
        im = ax[k].imshow(g, cmap="viridis", vmin=0, vmax=max(0.3, float(np.nanmax(g))), extent=ext)
        ax[k].set_title("P(advance) %s, AP %s" % (a, "%.3f" % row["arms"][a]["ap"] if row["arms"][a]["ap"] else "-"), fontsize=9)
        plt.colorbar(im, ax=ax[k], fraction=0.04)
    obs = np.where(~msk, 0.5, np.where(lab > 0, 2, np.where(base > 0, 1, 0)))
    ax[3].imshow(obs, cmap=matplotlib.colors.ListedColormap(["#f4f4f4", "#cccccc", "#9e9e9e", "#d7301f"]), vmin=0, vmax=2, extent=ext)
    ax[3].set_title("observed t+1 (red fire, dark grey cloud/no look)", fontsize=9)
    cy, cx = (r0 + r1) / 2, (c0 + c1) / 2
    sc = 0.6 * min(r1 - r0, c1 - c0) / max(np.hypot(u, v), 1e-3) / 4
    ax[3].arrow(cx, cy, u * sc, -v * sc, width=0.25, color="blue")
    ax[3].text(c0, r1 - 1, "t+1 wind %.1f m/s" % np.hypot(u, v), color="blue", fontsize=8)
    for a_ in ax:
        a_.set_xticks([]); a_.set_yticks([])
    fig.suptitle("%s %s: edge cells %d on this day, %d advanced" % (f["id"], f["name"], m.sum(), int((y[m] > 0.5).sum())), fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "%s.png" % f["id"]), dpi=80)
    plt.close(fig)


def overview(res, out, arms):
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5), gridspec_kw={"width_ratios": [1, 1, 2]})
    for k, key, title in ((0, "test", "Test AP, all edge cells"), (1, "test_new", "Test AP, new-fire edge cells")):
        vals = [res["arms"][a][key].get("ap", 0) for a in arms]
        ax[k].bar(range(len(arms)), vals, color=[COL[a] for a in arms])
        for i, v in enumerate(vals):
            ax[k].text(i, v, "%.3f" % v, ha="center", va="bottom", fontsize=7)
        ax[k].set_xticks(range(len(arms))); ax[k].set_xticklabels(arms, rotation=40, ha="right", fontsize=8)
        ax[k].set_title(title, fontsize=10)
    fs = [o for o in out if o["pos"] > 0]
    for j, a in enumerate(arms[:5]):
        ax[2].scatter(np.arange(len(fs)) + (j - 2) * 0.12, [o["arms"][a]["ap"] or 0 for o in fs], color=COL[a], s=14, label=a)
    ax[2].set_xticks(range(len(fs))); ax[2].set_xticklabels([o["name"][:16] for o in fs], rotation=45, ha="right", fontsize=7)
    ax[2].set_title("Per-fire edge-advance AP", fontsize=10); ax[2].legend(fontsize=7, ncol=3)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "overview.png"), dpi=90); plt.close(fig)
    fig, ax = plt.subplots(figsize=(5, 5))
    for a in arms:
        rl = np.array(res["arms"][a]["reliability"])
        ax.plot(rl[:, 0], rl[:, 1], "o-", color=COL[a], label=a, ms=3)
    lim = max(0.05, max(np.array(res["arms"][a]["reliability"])[:, :2].max() for a in arms))
    ax.plot([0, lim], [0, lim], "k:", lw=0.8); ax.set_xlabel("mean predicted"); ax.set_ylabel("observed frequency")
    ax.set_xscale("symlog", linthresh=0.01); ax.set_yscale("symlog", linthresh=0.01)
    ax.set_title("Reliability, test edge cells (deciles)", fontsize=10); ax.legend(fontsize=7)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "reliability.png"), dpi=90); plt.close(fig)


if __name__ == "__main__":
    main()
