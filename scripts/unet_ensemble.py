#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Average the seed runs of scripts/unet_train.py (data/compare/preds/seeds/unet_ARM_sN.npz) into
data/compare/preds/unet_ARM.npz: mean of the per-seed Platt-calibrated probabilities for the test days (`p`) and
for validation 2020 (`p_val`); `threshold` = max CSI of the averaged p_val on 2020 (masked, viirs/2020.npz).
Test years are not looked at.

Usage: unet_ensemble.py [ARM ...]      (default: every arm that has seed files)
"""
import logging
import os
import sys

import numpy as np

OUT = "data/compare"
ARMS = ["raw-pre", "harm-pre", "raw-all", "harm-all", "viirs-only"]
log = logging.getLogger(__name__)


def max_csi(p, y):
    # A >= threshold decision includes every tie, including ties introduced by
    # storing the ensemble as float16. Only score the end of each tied group.
    if not len(p):
        raise ValueError("No validation observations")
    o = np.argsort(-p, kind="stable")
    ys = y[o]
    tp = np.cumsum(ys, dtype=np.float64)
    csi = tp / (np.arange(1, len(ys) + 1) + ys.sum() - tp)
    ends = np.flatnonzero(np.r_[p[o][1:] != p[o][:-1], True])
    i = ends[int(np.argmax(csi[ends]))]
    return float(p[o][i]), float(csi[i])


def main(arms):
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(OUT, "logs", "unet_ensemble.log"))], force=True)
    v = np.load(os.path.join(OUT, "tensors", "viirs", "2020.npz"))
    m, y = v["mask"].astype(bool), v["label"][v["mask"].astype(bool)] > 0.5
    for arm in arms:
        files = [os.path.join(OUT, "preds", "seeds", f"unet_{arm}_s{s}.npz") for s in range(3)]
        missing = [f for f in files if not os.path.isfile(f)]
        if missing:
            raise FileNotFoundError(f"Incomplete three-seed ensemble: {missing}")
        dates = np.arange(np.datetime64("2021-01-01"), np.datetime64("2025-01-01"))
        p = pv = None
        for f in files:
            with np.load(f) as z:
                if str(z["arm"]) != arm or not np.array_equal(z["dates"], dates):
                    raise ValueError(f"Arm or date mismatch: {f}")
                for key, shape in (("p", (len(dates), *m.shape[1:])), ("p_val", m.shape)):
                    a = z[key]
                    if a.shape != shape or not np.isfinite(a).all() or np.any((a < 0) | (a > 1)):
                        raise ValueError(f"Invalid {key} in {f}: {a.shape}, expected {shape}")
                    if key == "p":
                        if p is None:
                            p = a.astype(np.float32)
                        else:
                            p += a
                    elif pv is None:
                        pv = a.astype(np.float32)
                    else:
                        pv += a
        p, pv = (p / len(files)).astype(np.float16), (pv / len(files)).astype(np.float16)
        thr, csi = max_csi(pv[m], y)
        np.savez_compressed(os.path.join(OUT, "preds", f"unet_{arm}.npz"), p=p.astype(np.float16), arm=arm,
                            p_val=pv.astype(np.float16), threshold=np.float32(thr), dates=dates, model="unet",
                            seeds=np.array([os.path.basename(f) for f in files]))
        log.info("%s: %d seeds, val threshold %.4f (CSI %.4f)", arm, len(files), thr, csi)


if __name__ == "__main__":
    main(sys.argv[1:] or ARMS)
