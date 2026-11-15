#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas", "pyarrow"]
# ///
"""Mean over seeds of the test metrics per arm/variant for the best-validation grid point; writes data/growth/test_table.md."""
import glob, json, collections
import numpy as np, pandas as pd
R = collections.defaultdict(list)
for p in glob.glob("data/growth/runs/*.json"):
    d = json.load(open(p))
    for g in d["grid"]:
        R[(d["variant"], d["arm"], g["objective"], g["num_leaves"])].append(g)
rows = []
for (v, a, o, l), gs in R.items():
    m = lambda k: float(np.mean([g[k] for g in gs]))
    s = lambda k: float(np.std([g[k] for g in gs]))
    rows.append(dict(variant=v, arm=a, obj=f"{o}/{l}", seeds=len(gs), val_mae=m("val_mae"), mae=m("mae"), mae_zero=m("mae_const_median"),
                     spearman=m("spearman"), sp_sd=s("spearman"), zero_auc=m("zero_auc"), mean_pred=m("mean_pred"), mean_obs=m("mean_obs")))
df = pd.DataFrame(rows)
best = df.loc[df.groupby(["variant", "arm"]).val_mae.idxmin()].sort_values(["variant", "arm"])
te = pd.read_parquet("data/growth/rows_viirs.parquet"); te = te[te.year >= 2021]
base = {k: float(pd.Series(te[k]).rank().corr(pd.Series(te.n_new).rank())) for k in ["g0", "n_t", "size_cells"]}
md = ["# Growth model test table (2021-2024, same rows for every arm; grid point picked by validation MAE; mean over seeds)", "",
      "| Variant | Arm | Config | Seeds | Test MAE | MAE of predicting 0 | Spearman (sd) | Zero-growth AUC | Mean pred / obs |", "|---|---|---|---|---|---|---|---|---|"]
for _, r in best.iterrows():
    md.append(f"| {r.variant} | {r.arm} | {r.obj} | {r.seeds} | {r.mae:.4f} | {r.mae_zero:.4f} | {r.spearman:.4f} ({r.sp_sd:.4f}) | {r.zero_auc:.4f} | {r.mean_pred:.2f} / {r.mean_obs:.2f} |")
md += ["", "Single-feature Spearman baselines on test: " + ", ".join(f"{k} {v:.3f}" for k, v in base.items()),
       f"Test rows {len(te)}, events {te.event.nunique()}; rows per arm train+val: see runs/*.json."]
open("data/growth/test_table.md", "w").write("\n".join(md) + "\n")
print("\n".join(md))
