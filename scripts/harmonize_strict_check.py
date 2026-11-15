#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scikit-learn"]
# ///
"""Score harmonization models on the years 2021-2024 (MODELLING_LOG.md entry 12): the strict fit
(out-base-strict/model.pkl, trained on 2012-2020, so 2021-2024 are out of time) against the 13-year fit's held-out
model (out-base-13y/model_holdout.pkl: trained on 2012/14/15/17/18/20/21/23/24, so only 2022 is held out for it).
Usage: HARMONIZE_FEATURES=base harmonize_strict_check.py"""
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harmonize as H  # noqa: E402

res = {}
rng = np.random.default_rng(0)
models = {"strict (2012-2020)": pickle.load(open("data/harmonize/out-base-strict/model.pkl", "rb"))["model"],
          "13y holdout": pickle.load(open("data/harmonize/out-base-13y/model_holdout.pkl", "rb"))["model"]}
for year in (2021, 2022, 2023, 2024):
    X, y, w, _ = H.year_sample("data/harmonize/grid", year, rng)
    for name, m in models.items():
        res["%s %d" % (name, year)] = H.evaluate(m.predict_proba(X)[:, 1], y, w, "%s %d" % (name, year))
json.dump(res, open("data/harmonize/out-base-strict/check_2021_2024.json", "w"), indent=1)
