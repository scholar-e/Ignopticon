#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "matplotlib", "pillow"]
# ///
"""Evaluate and plot next-day fire predictions for the test years 2021-2024 (docs/COMPARISON.md).

Each PREDS file is an .npz with `p` (1461, 260, 590): day i = 2021-01-01 + i, the probability that VIIRS S-NPP sees
fire on day i+1; `arm` (raw-pre, harm-pre, raw-all, harm-all, viirs-only); and `threshold`, the decision threshold
chosen on validation 2020 (or `p_val` (366, 260, 590) for 2020, from which the max-CSI threshold is computed). The
model name is the file name before the first "_" (gbm_raw-pre.npz -> gbm). Truth: data/compare/tensors/viirs.

Outputs (data/compare/): results/{model}.json and .md (overall and new-activity metrics, reliability, per fire),
figures/{model}/ (per-fire day panels and daily series, overview bars), ui/{model}_{arm}/{date}.png and
ui/{model}_fires/{fire}.json (payload format: docs/COMPARISON.md, "UI payloads").

Usage: compare_eval.py PREDS [PREDS ...]       e.g. compare_eval.py data/compare/preds/gbm_*.npz
Env: COMPARE_NO_UI=1 skips the UI export; COMPARE_NO_FIGS=1 skips figures.

Metrics are computed exactly on the full grid from histograms over float16 values (predictions are float16).
"""
import csv
import json
import logging
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT = "data/compare"
TENSORS = os.path.join(OUT, "tensors")
TEST_YEARS = [2021, 2022, 2023, 2024]
T0 = np.datetime64("2021-01-01")
NDAYS = 1461
ARM_ORDER = ["raw-pre", "harm-pre", "raw-all", "harm-all", "viirs-only"]
ARM_COLOR = {"raw-pre": "#d95f02", "harm-pre": "#1b9e77", "raw-all": "#e7298a", "harm-all": "#7570b3", "viirs-only": "#444444"}
MARGIN = 0.3  # degrees around a fire's bbox
NBIN = 1 << 16
_VALS = np.arange(NBIN, dtype=np.uint32).astype(np.uint16).view(np.float16).astype(np.float64)
_OK = np.isfinite(_VALS) & (_VALS >= 0) & (_VALS <= 1)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- metrics from float16 histograms

def hist_counts(p, y, mask):
    """Counts of positive and negative masked cells per float16 value of p (bit pattern bins)."""
    b = np.ascontiguousarray(p, dtype=np.float16).view(np.uint16)
    m = np.asarray(mask, bool)
    yy = np.asarray(y, bool)
    pos = np.bincount(b[m & yy], minlength=NBIN).astype(np.float64)
    neg = np.bincount(b[m & ~yy], minlength=NBIN).astype(np.float64)
    pos[~_OK] = 0
    neg[~_OK] = 0
    return pos, neg


def _desc(pos, neg):
    """Bins with data, in descending order of p: values, cumulative TP, FP."""
    order = np.argsort(_VALS[_OK])[::-1]
    v, P, N = _VALS[_OK][order], pos[_OK][order], neg[_OK][order]
    k = (P + N) > 0
    return v[k], np.cumsum(P[k]), np.cumsum(N[k]), P[k], N[k]


def csi_threshold(pos, neg):
    v, tp, fp, _, _ = _desc(pos, neg)
    if not len(v):
        return 0.5, 0.0
    csi = tp / (tp[-1] + fp)  # TP / (TP + FP + FN), FN = P - TP
    j = int(np.argmax(csi))
    return float(v[j]), float(csi[j])


def metrics(pos, neg, thr=None):
    v, tp, fp, P, N = _desc(pos, neg)
    npos, nneg = float(pos.sum()), float(neg.sum())
    out = {"cells": npos + nneg, "positives": npos, "base_rate": npos / max(npos + nneg, 1)}
    if npos == 0 or nneg == 0:
        return out | {"ap": None, "auc": None}
    rec = tp / npos
    prec = tp / (tp + fp)
    out["ap"] = float(np.sum(np.diff(np.concatenate([[0], rec])) * prec))
    fpr = np.concatenate([[0], fp / nneg])
    tpr = np.concatenate([[0], tp / npos])
    out["auc"] = float(np.trapezoid(tpr, fpr))
    out["brier"] = float((P * (1 - v) ** 2 + N * v ** 2).sum() / (npos + nneg))
    vc = np.clip(v, 1e-7, 1 - 1e-7)
    out["log_loss"] = float(-(P * np.log(vc) + N * np.log(1 - vc)).sum() / (npos + nneg))
    out["expected"] = float(((P + N) * v).sum())
    out["observed"] = npos
    bins = np.array([0, .005, .01, .02, .05, .1, .2, .3, .5, .7, .9, 1.0001])
    w = np.digitize(v, bins) - 1
    out["reliability"] = [[float(bins[k]), float(bins[k + 1]), float(((P + N) * v)[w == k].sum() / (P + N)[w == k].sum()),
                           float(P[w == k].sum() / (P + N)[w == k].sum()), float((P + N)[w == k].sum())]
                          for k in range(len(bins) - 1) if (w == k).any()]
    if thr is not None:
        s = v >= thr
        TP, FP = float(P[s].sum()), float(N[s].sum())
        out |= {"threshold": thr, "csi": TP / max(npos + FP, 1e-9), "precision": TP / max(TP + FP, 1e-9),
                "recall": TP / npos, "flagged": TP + FP}
    return out


# ---------------------------------------------------------------- truth and predictions

def load_truth():
    lab, msk, rec = [], [], []
    for y in TEST_YEARS:
        z = np.load(os.path.join(TENSORS, "viirs", "%d.npz" % y))
        lab.append(z["label"] > 0.5)
        msk.append(z["mask"] > 0)
        x = z["x"]
        rec.append((x[:, 3] + x[:, 4] + x[:, 5]) > 0)  # VIIRS fire in the 3x3 on t-2..t
        obs_t = x[:, 0] > 0.5
        del x
        if y == TEST_YEARS[0]:
            obs = [obs_t]
        else:
            obs.append(obs_t)
    T = {"label": np.concatenate(lab), "mask": np.concatenate(msk), "recent": np.concatenate(rec), "obs_t": np.concatenate(obs)}
    assert T["label"].shape[0] == NDAYS
    return T


def load_pred(path):
    z = np.load(path)
    p = z["p"].astype(np.float16)
    assert p.shape == (NDAYS, 260, 590), p.shape
    arm = str(z["arm"])
    if "threshold" in z.files:
        thr = float(z["threshold"])
    elif "p_val" in z.files:
        zv = np.load(os.path.join(TENSORS, "viirs", "2020.npz"))
        thr = csi_threshold(*hist_counts(z["p_val"], zv["label"] > 0.5, zv["mask"] > 0))[0]
    else:
        sys.exit("%s has neither threshold nor p_val" % path)
    model = os.path.basename(path).split("_")[0]
    return model, arm, p, thr


def evaluate(arm, p, thr, T):
    """Overall and new-activity metrics for one (days, 260, 590) prediction array of the test years."""
    res = {"arm": arm, "threshold": thr,
           "all": metrics(*hist_counts(p, T["label"], T["mask"]), thr),
           "new": metrics(*hist_counts(p, T["label"], T["mask"] & ~T["recent"]), thr), "per_year": {}}
    s = 0
    for y in TEST_YEARS:
        n = 366 if y % 4 == 0 else 365
        sl = slice(s, s + n)
        a = metrics(*hist_counts(p[sl], T["label"][sl], T["mask"][sl]), thr)
        res["per_year"][str(y)] = {k: a.get(k) for k in ("ap", "auc", "csi", "expected", "observed")}
        s += n
    return res


# ---------------------------------------------------------------- fires

def read_fires():
    fires = list(csv.DictReader(open(os.path.join(OUT, "fires.csv"))))
    for f in fires:
        w, s, e, n = (float(f[k]) for k in ("west", "south", "east", "north"))
        f["c0"], f["c1"] = max(int(np.floor((w - MARGIN + 125) * 10)), 0), min(int(np.ceil((e + MARGIN + 125) * 10)), 590)
        f["r0"], f["r1"] = max(int(np.floor((50 - n - MARGIN) * 10)), 0), min(int(np.ceil((50 - s + MARGIN) * 10)), 260)
        # day index i predicts day i+1: targets from start to end
        f["i0"] = int((np.datetime64(f["start"]) - T0).astype(int)) - 1
        f["i1"] = int((np.datetime64(f["end"]) - T0).astype(int))
        f["i0"] = max(f["i0"], 0)
    return fires


def fire_eval(f, p, thr, T):
    sl = (slice(f["i0"], f["i1"]), slice(f["r0"], f["r1"]), slice(f["c0"], f["c1"]))
    pp, yy, mm = p[sl], T["label"][sl], T["mask"][sl]
    m = metrics(*hist_counts(pp, yy, mm), thr)
    pf = pp.astype(np.float32)
    daily = {"expected": [float(x) for x in (pf * mm).sum((1, 2))],
             "flagged": [int(x) for x in ((pf >= thr) & mm).sum((1, 2))]}
    return {k: m.get(k) for k in ("ap", "auc", "csi", "precision", "recall", "expected", "observed", "flagged")}, daily


def fire_truth_daily(f, T):
    sl = (slice(f["i0"], f["i1"]), slice(f["r0"], f["r1"]), slice(f["c0"], f["c1"]))
    return {"observed": [int(x) for x in (T["label"][sl] & T["mask"][sl]).sum((1, 2))],
            "clear_frac": [float(x) for x in T["mask"][sl].mean((1, 2))],
            "dates": [str(T0 + np.timedelta64(i + 1, "D")) for i in range(f["i0"], f["i1"])]}


# ---------------------------------------------------------------- figures

def fig_panels(f, model, preds, T, path, max_rows=8):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    sl_sp = (slice(f["r0"], f["r1"]), slice(f["c0"], f["c1"]))
    days = np.arange(f["i0"], f["i1"])
    obs = np.array([(T["label"][i][sl_sp] & T["mask"][i][sl_sp]).sum() for i in days])
    act = days[obs > 0] if (obs > 0).any() else days
    pick = act[np.linspace(0, len(act) - 1, min(max_rows, len(act))).round().astype(int)]
    pick = np.unique(pick)
    arms = [a for a in ARM_ORDER if a in preds]
    cols = ["observed t", "observed t+1"] + ["%s t+1" % a for a in arms]
    ext = [-125 + 0.1 * f["c0"], -125 + 0.1 * f["c1"], 50 - 0.1 * f["r1"], 50 - 0.1 * f["r0"]]
    fig, axes = plt.subplots(len(pick), len(cols), figsize=(1.9 * len(cols), 1.9 * len(pick) * (f["r1"] - f["r0"]) / max(f["c1"] - f["c0"], 1) + 0.9),
                             squeeze=False)
    obs_cmap = ListedColormap(["#f2f2f2", "#bdbdbd", "#d7301f"])  # clear no fire, not seen, fire
    for r, i in enumerate(pick):
        date = T0 + np.timedelta64(int(i), "D")
        # observed t: day i; cloud on t = mask of day i-1 (S-NPP clear on day i)
        ot = T["obs_t"][i][sl_sp].astype(int)
        seen_t = T["mask"][i - 1][sl_sp] if i > 0 else np.ones_like(ot, bool)
        o1 = (T["label"][i][sl_sp] & T["mask"][i][sl_sp]).astype(int)
        imgs = [np.where(ot > 0, 2, np.where(seen_t, 0, 1)), np.where(o1 > 0, 2, np.where(T["mask"][i][sl_sp], 0, 1))]
        for c in range(len(cols)):
            ax = axes[r, c]
            if c < 2:
                ax.imshow(imgs[c], cmap=obs_cmap, vmin=0, vmax=2, extent=ext, interpolation="nearest")
            else:
                im = ax.imshow(preds[arms[c - 2]][1][i][sl_sp].astype(np.float32), cmap="inferno", vmin=0, vmax=1, extent=ext,
                               interpolation="nearest")
                if o1.any():
                    lons = -125 + 0.1 * (np.arange(f["c0"], f["c1"]) + 0.5)
                    lats = 50 - 0.1 * (np.arange(f["r0"], f["r1"]) + 0.5)
                    ax.contour(lons, lats, o1, levels=[0.5], colors="cyan", linewidths=0.6)
            ax.set_xticks([]); ax.set_yticks([])
            if r == 0:
                ax.set_title(cols[c], fontsize=8)
            if c == 0:
                ax.set_ylabel(str(date), fontsize=7)
    fig.suptitle("%s %s (%s, %s): %s next-day predictions\nred = S-NPP fire, dark grey = no clear S-NPP look, cyan = observed fire on t+1" %
                 (f["id"], f["name"], f["region"], f["fuel_group"], model), fontsize=8)
    fig.subplots_adjust(right=0.9, top=0.9, wspace=0.05, hspace=0.12)
    cax = fig.add_axes([0.92, 0.15, 0.012, 0.7])
    fig.colorbar(im, cax=cax).set_label("P(VIIRS fire on t+1)", fontsize=7)
    fig.savefig(path, dpi=80)
    plt.close(fig)


def fig_series(f, model, daily, truth, metr, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = np.array(truth["dates"], dtype="datetime64[D]")
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(x, truth["observed"], color="#cccccc", label="observed fire cells (S-NPP, clear)")
    for a in ARM_ORDER:
        if a in daily:
            m = metr[a]
            ax.plot(x, daily[a]["expected"], color=ARM_COLOR[a], lw=1.4,
                    label="%s expected (AP %.2f, CSI %.2f)" % (a, m["ap"] or 0, m["csi"] or 0))
    ax.set_ylabel("fire cells (0.1°)")
    ax.set_title("%s %s: daily observed vs expected (sum of P on clear cells), %s" % (f["id"], f["name"], model), fontsize=9)
    ax.legend(fontsize=7, frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def fig_overview(fires, per_fire, model, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    arms = [a for a in ARM_ORDER if a in per_fire[fires[0]["id"]]]
    fig, axes = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True)
    x = np.arange(len(fires))
    wd = 0.8 / len(arms)
    for k, key in enumerate(("ap", "csi")):
        for j, a in enumerate(arms):
            axes[k].bar(x + (j - (len(arms) - 1) / 2) * wd, [per_fire[f["id"]][a][key] or 0 for f in fires], wd,
                        color=ARM_COLOR[a], label=a)
        axes[k].set_ylabel(key.upper())
    axes[0].legend(ncol=len(arms), fontsize=8, frameon=False)
    axes[0].set_title("Per-fire skill on held-out 2021-2024 fires (%s); CSI at each arm's 2020-validation threshold" % model, fontsize=10)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(["%s\n%s" % (f["name"][:22], f["id"]) for f in fires], rotation=60, ha="right", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def fig_reliability(results, model, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, key in zip(axes, ("all", "new")):
        ax.plot([1e-4, 1], [1e-4, 1], color="#999999", lw=0.8)
        for a in ARM_ORDER:
            if a in results:
                rel = results[a][key]["reliability"]
                ax.plot([r[2] for r in rel], [max(r[3], 1e-4) for r in rel], "o-", ms=3, color=ARM_COLOR[a], label=a)
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("mean predicted P"); ax.set_ylabel("observed rate")
        ax.set_title({"all": "all clear cell-days", "new": "new activity"}[key] + ", test 2021-2024 (%s)" % model, fontsize=9)
    axes[0].legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


# ---------------------------------------------------------------- UI payloads

def export_ui(model, preds, fires, per_fire, daily, truths, T):
    from PIL import Image
    land = np.load(os.path.join(TENSORS, "static.npz"))["land"] > 0
    days = sorted({i for f in fires for i in range(f["i0"], f["i1"])})
    for arm, (_, p, thr) in preds.items():
        d = os.path.join(OUT, "ui", "%s_%s" % (model, arm))
        os.makedirs(d, exist_ok=True)
        for i in days:
            date = str(T0 + np.timedelta64(i + 1, "D"))  # the day predicted
            rgba = np.zeros((260, 590, 4), np.uint8)
            rgba[..., 0] = np.clip(np.round(p[i].astype(np.float32) * 255), 0, 255)
            rgba[..., 1] = np.where(T["label"][i] & T["mask"][i], 255, 0)
            rgba[..., 2] = np.where(T["mask"][i], 255, 0)
            rgba[..., 3] = np.where(land, 255, 0)
            Image.fromarray(rgba, "RGBA").save(os.path.join(d, "%s.png" % date), optimize=True)
    d = os.path.join(OUT, "ui", "%s_fires" % model)
    os.makedirs(d, exist_ok=True)
    index = []
    for f in fires:
        fid = f["id"]
        js = {"id": fid, "name": f["name"], "region": f["region"], "fuel_group": f["fuel_group"],
              "bbox": [float(f[k]) for k in ("west", "south", "east", "north")],
              "crop": {"col0": f["c0"], "col1": f["c1"], "row0": f["r0"], "row1": f["r1"]},
              "model": model, "dates": truths[fid]["dates"], "observed": truths[fid]["observed"],
              "clear_frac": [round(x, 3) for x in truths[fid]["clear_frac"]],
              "arms": {a: {"threshold": preds[a][2], "metrics": per_fire[fid][a],
                           "expected": [round(x, 2) for x in daily[fid][a]["expected"]], "flagged": daily[fid][a]["flagged"],
                           "png": "%s_%s/{date}.png" % (model, a)} for a in preds}}
        json.dump(js, open(os.path.join(d, "%s.json" % fid), "w"))
        index.append({"id": fid, "name": f["name"], "start": truths[fid]["dates"][0], "end": truths[fid]["dates"][-1]})
    json.dump({"model": model, "arms": list(preds), "fires": index,
               "encoding": "R = P(VIIRS S-NPP fire on date)*255; G = 255 observed S-NPP fire on date; B = 255 S-NPP clear on date; A = 255 land"},
              open(os.path.join(d, "index.json"), "w"), indent=1)


# ---------------------------------------------------------------- main

def write_md(model, results, per_fire, fires, path):
    L = ["# %s: held-out test 2021-2024 (truth VIIRS S-NPP on t+1, clear land cell-days, full grid)" % model, "",
         "| Arm | AP | AUC | Brier | expected / observed | CSI @ val thr (thr) | AP new | AUC new | exp / obs new |",
         "|---|---|---|---|---|---|---|---|---|"]
    for a in ARM_ORDER:
        if a not in results:
            continue
        r, n = results[a]["all"], results[a]["new"]
        L.append("| %s | %.4f | %.4f | %.6f | %.0f / %.0f | %.4f (%.3f) | %.4f | %.4f | %.0f / %.0f |" % (
            a, r["ap"], r["auc"], r["brier"], r["expected"], r["observed"], r["csi"], r["threshold"], n["ap"], n["auc"],
            n["expected"], n["observed"]))
    L += ["", "Per year AP: " + "; ".join("%s %s" % (a, ", ".join("%s %.3f" % (y, v["ap"]) for y, v in results[a]["per_year"].items()))
                                      for a in ARM_ORDER if a in results), "",
          "Per fire (AP / CSI):", "", "| Fire | " + " | ".join(a for a in ARM_ORDER if a in results) + " |",
          "|---|" + "---|" * len([a for a in ARM_ORDER if a in results])]
    for f in fires:
        L.append("| %s %s | " % (f["id"], f["name"]) + " | ".join("%.3f / %.3f" % (per_fire[f["id"]][a]["ap"] or 0, per_fire[f["id"]][a]["csi"] or 0)
                                                       for a in ARM_ORDER if a in results) + " |")
    L += ["", "Reliability (mean P -> observed rate, all):", ""]
    for a in ARM_ORDER:
        if a in results:
            L.append("- %s: " % a + ", ".join("%.3f→%.3f" % (r[2], r[3]) for r in results[a]["all"]["reliability"]))
    open(path, "w").write("\n".join(L) + "\n")


def main(paths):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)], force=True)
    T = load_truth()
    fires = read_fires()
    by_model = {}
    for p in paths:
        model, arm, pred, thr = load_pred(p)
        by_model.setdefault(model, {})[arm] = (p, pred, thr)
    for model, preds in by_model.items():
        results, per_fire, daily = {}, {f["id"]: {} for f in fires}, {f["id"]: {} for f in fires}
        truths = {f["id"]: fire_truth_daily(f, T) for f in fires}
        for arm, (_, pred, thr) in preds.items():
            results[arm] = evaluate(arm, pred, thr, T)
            r = results[arm]
            log.info("%s %-10s AP %.4f AUC %.4f Brier %.6f exp %.0f obs %.0f CSI %.4f | new AP %.4f AUC %.4f exp %.0f obs %.0f",
                     model, arm, r["all"]["ap"], r["all"]["auc"], r["all"]["brier"], r["all"]["expected"], r["all"]["observed"],
                     r["all"]["csi"], r["new"]["ap"], r["new"]["auc"], r["new"]["expected"], r["new"]["observed"])
            for f in fires:
                per_fire[f["id"]][arm], daily[f["id"]][arm] = fire_eval(f, pred, thr, T)
        os.makedirs(os.path.join(OUT, "results"), exist_ok=True)
        json.dump({"overall": results, "per_fire": per_fire, "daily": daily, "truth": truths},
                  open(os.path.join(OUT, "results", "%s.json" % model), "w"))
        write_md(model, results, per_fire, fires, os.path.join(OUT, "results", "%s.md" % model))
        log.info("wrote results/%s.md", model)
        if not os.environ.get("COMPARE_NO_FIGS"):
            fd = os.path.join(OUT, "figures", model)
            os.makedirs(fd, exist_ok=True)
            fig_overview(fires, per_fire, model, os.path.join(fd, "overview_per_fire.png"))
            fig_reliability(results, model, os.path.join(fd, "reliability.png"))
            pmap = {a: (v[0], v[1]) for a, v in preds.items()}
            for f in fires:
                fig_panels(f, model, pmap, T, os.path.join(fd, "%s_panels.png" % f["id"]))
                fig_series(f, model, daily[f["id"]], truths[f["id"]], per_fire[f["id"]], os.path.join(fd, "%s_series.png" % f["id"]))
            log.info("wrote figures/%s", model)
        if not os.environ.get("COMPARE_NO_UI"):
            export_ui(model, preds, fires, per_fire, daily, truths, T)
            log.info("wrote ui/%s_*", model)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
