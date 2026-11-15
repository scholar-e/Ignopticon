#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["torch>=2.5", "numpy"]
# ///
"""U-Net next-day fire model for the harmonized vs non-harmonized comparison (docs/COMPARISON.md, model 2).

Per 0.1 degree cell and day t: logit that VIIRS S-NPP sees fire on t+1, from the per-year tensors of
data/compare/tensors/ (see its README.md for channels, label and mask). Fully convolutional, trained on 64 x 64
patches, predicted on the whole (zero-padded) 260 x 590 grid at once, which equals tiling with every possible
overlap.

Arms (docs/COMPARISON.md): raw-pre, harm-pre (2003-2011), raw-all, harm-all (2003-2019), viirs-only (2012-2019).
Validation 2020 (early stopping on masked AP, Platt calibration of the logits, threshold at max CSI); test
2021-2024 is only predicted, never used for a choice.

Sampling: a share POS_FRAC of patches is centred (with jitter) on a random positive training cell-day, the rest
are uniform over day and position (patches with under 5% domain cells redrawn). Loss: masked BCE with soft labels
and pos_weight POS_WEIGHT. Both bias the raw probabilities upwards; the Platt fit on all of 2020 undoes that.

Usage:
    unet_train.py train ARM        train, calibrate, write data/compare/preds/unet_ARM.npz and models/
    unet_train.py smoke            synthetic data of the README shapes, a few epochs (code test)
Environment: TENSORS (data/compare/tensors), OUT (data/compare), EPOCHS (40), STEPS (500 per epoch), BATCH (128),
    PATIENCE (8), MAX_MIN (75, training wall minutes), WIDTH (32), POS_FRAC (0.5), POS_WEIGHT (5), LR (2e-3),
    SEED (0), SEED_TAG (0; 1 writes preds/seeds/unet_ARM_sSEED.npz), YEARS_LIMIT (keep only the last N training years: smoke tests), ON_GPU (1: keep tensors on GPU).
"""
import json
import logging
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

TENSORS = os.environ.get("TENSORS", "data/compare/tensors")
OUT = os.environ.get("OUT", "data/compare")
NY, NX = 260, 590
PY, PX = 272, 592  # padded to a multiple of 16 for the 4 poolings
P = 64
ARMS = {
    "raw-pre": ("raw", list(range(2003, 2012))),
    "harm-pre": ("harm", list(range(2003, 2012))),
    "raw-all": ("raw", list(range(2003, 2020))),
    "harm-all": ("harm", list(range(2003, 2020))),
    "viirs-only": ("viirs", list(range(2012, 2020))),
}
VAL_YEAR = 2020
TEST_YEARS = [2021, 2022, 2023, 2024]
E = os.environ.get
EPOCHS, STEPS, BATCH = int(E("EPOCHS", 40)), int(E("STEPS", 500)), int(E("BATCH", 128))
PATIENCE, MAX_MIN, WIDTH = int(E("PATIENCE", 8)), float(E("MAX_MIN", 75)), int(E("WIDTH", 32))
POS_FRAC, POS_WEIGHT, LR, SEED = float(E("POS_FRAC", 0.5)), float(E("POS_WEIGHT", 5)), float(E("LR", 2e-3)), int(E("SEED", 0))
YEARS_LIMIT = int(E("YEARS_LIMIT", 0))
SEED_TAG = E("SEED_TAG", "0") == "1"  # 1: outputs named unet_ARM_sSEED (for seed ensembles, scripts/unet_ensemble.py)
ON_GPU = E("ON_GPU", "1") == "1"

os.makedirs(os.path.join(OUT, "preds"), exist_ok=True)
os.makedirs(os.path.join(OUT, "models"), exist_ok=True)
os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
log = logging.getLogger("unet")


def setup_log(name):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(OUT, "logs", f"unet_{name}.log"))], force=True)


# ---------------------------------------------------------------- data
# The loader is the only place that knows the tensor file layout (data/compare/tensors/README.md).

def load_static():
    """(names, array C x NY x NX float32 with NaN off land, land bool NY x NX)."""
    z = np.load(os.path.join(TENSORS, "static.npz"))
    return [str(n) for n in z["names"]], z["x"].astype(np.float32), z["land"].astype(bool)


def fire_src(src, year):
    return "viirs" if year >= 2012 else src


def load_year(src, year):
    """The README's assembled stack for one year. src = pre-2012 convention (raw, harm, viirs).
    dict: x (D, 27, NY, NX) float16 inputs for day t, y (D, NY, NX) float32 label for t+1 (soft for harm),
    m (D, NY, NX) bool label mask, names, dates (D,) of day t."""
    z = np.load(os.path.join(TENSORS, fire_src(src, year), f"{year}.npz"))
    fx = z["x"]
    D = fx.shape[0]
    w = np.load(os.path.join(TENSORS, "weather", f"{year}.npz"))
    ww, wn = w["w"], [str(n) for n in w["names"]]
    doy = 2 * np.pi * np.arange(1, D + 1) / D
    cal = np.stack([np.sin(doy), np.cos(doy)], 1).astype(np.float16)[:, :, None, None]
    x = np.concatenate([fx, ww[:D], ww[1:D + 1], np.broadcast_to(cal, (D, 2, NY, NX))], 1)
    names = [str(n) for n in z["names"]] + [f"{n}_t" for n in wn] + [f"{n}_t1" for n in wn] + ["doy_sin", "doy_cos"]
    dates = np.arange(np.datetime64(f"{year}-01-01"), np.datetime64(f"{year + 1}-01-01"))[:D]
    return {"x": x, "y": z["label"].astype(np.float32), "m": z["mask"].astype(bool), "names": names, "dates": dates}


def synth_year(year, C=27, rng=None):
    rng = rng or np.random.default_rng(year)
    D = 366 if year % 4 == 0 else 365
    fire = (rng.random((D, NY, NX)) < 0.002).astype(np.float32)
    x = rng.standard_normal((D, C, NY, NX)).astype(np.float16)
    x[:, 0] = fire
    x[:, 11:, :20, :30] = np.nan  # a fake ocean corner
    y = fire.copy()
    m = rng.random((D, NY, NX)) < 0.8
    return {"x": x, "y": y, "m": m, "names": [f"c{i}" for i in range(C)],
            "dates": np.arange(np.datetime64(f"{year}-01-01"), np.datetime64(f"{year + 1}-01-01"))[:D]}


def synth_static():
    rng = np.random.default_rng(1)
    s = rng.standard_normal((17, NY, NX)).astype(np.float32)
    s[:, :20, :30] = np.nan
    return [f"s{i}" for i in range(17)], s, ~np.isnan(s[0])


class Data:
    """Normalized (per-channel mean/sd from training years), NaN -> 0, zero-padded to PY x PX. Static inputs get
    two extra channels: land, and 'static inputs finite'. Weather is NaN only off land, so land is its validity."""

    def __init__(self, src, train_years, synth=False):
        self.src, self.synth = src, synth
        self.dev = torch.device("cuda" if torch.cuda.is_available() and ON_GPU else "cpu")
        self.snames, s, land = synth_static() if synth else load_static()
        # pass 1: stats from up to 4 evenly spaced training years, every 5th day
        pick = sorted({train_years[int(round(i))] for i in np.linspace(0, len(train_years) - 1, min(4, len(train_years)))})
        s1, s2, n = 0.0, 0.0, 0.0
        for y in pick:
            r = self._load(y)
            xs = r["x"][::5].astype(np.float32)
            ok = ~np.isnan(xs)
            s1 = s1 + np.nansum(xs, axis=(0, 2, 3))
            s2 = s2 + np.nansum(xs.astype(np.float64) ** 2, axis=(0, 2, 3))
            n = n + ok.sum(axis=(0, 2, 3))
            self.dnames = r["names"]
            del r, xs, ok
        self.mu = s1 / np.maximum(n, 1)
        sd = np.sqrt(np.maximum(s2 / np.maximum(n, 1) - self.mu ** 2, 0))
        self.sd = np.where(sd > 1e-6, sd, 1.0)
        smu, ssd = np.nanmean(s, axis=(1, 2)), np.nanstd(s, axis=(1, 2))
        ssd = np.where(ssd > 1e-6, ssd, 1.0)
        sfin = np.isfinite(s).all(axis=0)
        s = np.nan_to_num((s - smu[:, None, None]) / ssd[:, None, None])
        stat = np.concatenate([s, land[None].astype(np.float32), sfin[None].astype(np.float32)])
        self.static = self._pad(torch.from_numpy(stat).to(torch.float16)).to(self.dev)  # Cs x PY x PX
        self.domain = self._pad(torch.from_numpy(land))
        self.names = self.dnames + self.snames + ["land", "static_finite"]
        self.n_in = len(self.names)
        self.years = {}
        for y in train_years:  # pass 2
            t = time.time()
            self.years[y] = self._prep(self._load(y))
            log.info("loaded %d (%s) in %.0f s", y, fire_src(src, y), time.time() - t)

    def _load(self, year):
        return synth_year(year) if self.synth else load_year(self.src, year)

    @staticmethod
    def _pad(t):
        return F.pad(t, (0, PX - t.shape[-1], 0, PY - t.shape[-2]))

    def _prep(self, r, keep_cpu=False):
        dev = torch.device("cpu") if keep_cpu else self.dev
        D, C = r["x"].shape[:2]
        out = torch.empty((D, C, PY, PX), dtype=torch.float16, device=dev)
        mu = torch.tensor(self.mu, dtype=torch.float32)[:, None, None]
        sd = torch.tensor(self.sd, dtype=torch.float32)[:, None, None]
        for d0 in range(0, D, 16):
            x = torch.from_numpy(np.asarray(r["x"][d0:d0 + 16], np.float32))
            out[d0:d0 + 16] = self._pad(torch.nan_to_num((x - mu) / sd).half()).to(dev)
        y = self._pad(torch.from_numpy(np.nan_to_num(r["y"])).half()).to(dev)
        m = self._pad(torch.from_numpy(r["m"])).to(dev)
        return {"x": out, "y": y, "m": m, "dates": r["dates"]}

    def load_eval(self, year):
        return self._prep(self._load(year), keep_cpu=True)


# ---------------------------------------------------------------- model

class Block(nn.Module):
    def __init__(self, a, b):
        super().__init__()
        self.f = nn.Sequential(nn.Conv2d(a, b, 3, padding=1, bias=False), nn.GroupNorm(8, b), nn.SiLU(),
                               nn.Conv2d(b, b, 3, padding=1, bias=False), nn.GroupNorm(8, b), nn.SiLU())

    def forward(self, x):
        return self.f(x)


class UNet(nn.Module):
    def __init__(self, cin, w=32):
        super().__init__()
        ch = [w, 2 * w, 4 * w, 8 * w, 8 * w]
        self.stem = nn.Sequential(nn.Conv2d(cin, w, 1), nn.SiLU())  # per-cell mixing first
        self.down = nn.ModuleList([Block(w if i == 0 else ch[i - 1], ch[i]) for i in range(5)])
        self.up = nn.ModuleList([Block(ch[i + 1] + ch[i], ch[i]) for i in range(4)])
        self.skip = nn.Conv2d(cin, w, 1)  # direct per-cell path (persistence matters most)
        self.head = nn.Conv2d(2 * w, 1, 1)

    def forward(self, x):
        h, feats = self.stem(x), []
        for i, blk in enumerate(self.down):
            h = blk(h if i == 0 else F.max_pool2d(h, 2))
            feats.append(h)
        h = feats[-1]
        for i in reversed(range(4)):
            h = F.interpolate(h, scale_factor=2, mode="nearest")
            h = self.up[i](torch.cat([h, feats[i]], 1))
        return self.head(torch.cat([h, self.skip(x)], 1))[:, 0]


# ---------------------------------------------------------------- training

def ap_gpu(p, y):
    """Average precision (ties broken arbitrarily), p and y flat; y may be soft (treated as weights of positives)."""
    o = torch.argsort(p, descending=True)
    y = y[o].double()
    tp = torch.cumsum(y, 0)
    prec = tp / torch.arange(1, len(y) + 1, device=y.device, dtype=torch.float64)
    return float((prec * y).sum() / y.sum().clamp_min(1e-9))


def auc_gpu(p, y):
    o = torch.argsort(p)
    r = torch.empty_like(o, dtype=torch.float64)
    r[o] = torch.arange(1, len(p) + 1, device=p.device, dtype=torch.float64)
    yb = y > 0.5
    n1, n0 = yb.sum().double(), (~yb).sum().double()
    return float((r[yb].sum() - n1 * (n1 + 1) / 2) / (n1 * n0).clamp_min(1))


@torch.no_grad()
def predict(model, xs, static, dev, bs=8):
    """Logits for every day of xs (D, C, PY, PX) (cpu or gpu), cropped to NY x NX, on cpu float32."""
    model.eval()
    out = torch.empty((xs.shape[0], NY, NX), dtype=torch.float32)
    for d0 in range(0, xs.shape[0], bs):
        x = xs[d0:d0 + bs].to(dev, non_blocking=True)
        x = torch.cat([x, static.expand(x.shape[0], -1, -1, -1)], 1)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=dev.type == "cuda"):
            out[d0:d0 + bs] = model(x)[:, :NY, :NX].float().cpu()
    return out


def masked_metrics(logit, y, m, dev):
    """AP, AUC, BCE (of sigmoid(logit)), Brier, on masked cells; inputs cpu (D, NY, NX)."""
    mm = m[:, :NY, :NX].bool()
    lg, yy = logit[mm].to(dev), y[:, :NY, :NX][mm].float().to(dev)
    p = torch.sigmoid(lg)
    return {"ap": ap_gpu(lg, yy), "auc": auc_gpu(lg, yy),
            "bce": float(F.binary_cross_entropy_with_logits(lg, yy)), "brier": float(((p - yy) ** 2).mean()),
            "n": int(mm.sum()), "pos": float(yy.sum())}


def platt(logit, y, m, dev):
    mm = m[:, :NY, :NX].bool()
    lg, yy = logit[mm].to(dev), y[:, :NY, :NX][mm].float().to(dev)
    ab = torch.tensor([1.0, 0.0], device=dev, requires_grad=True)
    opt = torch.optim.LBFGS([ab], lr=0.5, max_iter=200, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad()
        loss = F.binary_cross_entropy_with_logits(ab[0] * lg + ab[1], yy)
        loss.backward()
        return loss
    opt.step(closure)
    a, b = (float(v) for v in ab.detach())
    p = torch.sigmoid(a * lg + b)
    # threshold at max CSI = TP / (TP + FP + FN) over the calibrated probabilities
    o = torch.argsort(p, descending=True)
    ys = (yy[o] > 0.5).double()
    tp = torch.cumsum(ys, 0)
    k = torch.arange(1, len(ys) + 1, device=dev, dtype=torch.float64)
    csi = tp / (k + ys.sum() - tp)
    i = int(torch.argmax(csi))
    return a, b, float(p[o][i]), float(csi[i])


def sample_batch(data, pos_index, gen, n):
    """Indices (year, day, y0, x0) for n patches."""
    ys = list(data.years)
    out = []
    npos = int(round(n * POS_FRAC)) if len(pos_index) else 0
    if npos:
        k = torch.randint(len(pos_index), (npos,), generator=gen)
        for yi, d, r, c in pos_index[k].tolist():
            jy, jx = torch.randint(4, P - 4, (2,), generator=gen).tolist()
            out.append((ys[yi], d, min(max(r - jy, 0), PY - P), min(max(c - jx, 0), PX - P)))
    while len(out) < n:
        y = ys[int(torch.randint(len(ys), (1,), generator=gen))]
        d = int(torch.randint(data.years[y]["x"].shape[0], (1,), generator=gen))
        r, c = int(torch.randint(PY - P + 1, (1,), generator=gen)), int(torch.randint(PX - P + 1, (1,), generator=gen))
        if data.domain[r:r + P, c:c + P].float().mean() < 0.05:
            continue
        out.append((y, d, r, c))
    return out


def gather(data, idx):
    xs, ys, ms, ss = [], [], [], []
    for y, d, r, c in idx:
        t = data.years[y]
        xs.append(t["x"][d, :, r:r + P, c:c + P])
        ys.append(t["y"][d, r:r + P, c:c + P])
        ms.append(t["m"][d, r:r + P, c:c + P])
        ss.append(data.static[:, r:r + P, c:c + P])
    dev = data.dev if data.dev.type == "cuda" else torch.device("cuda" if torch.cuda.is_available() else "cpu")
    x = torch.cat([torch.stack(xs), torch.stack(ss)], 1).to(dev, non_blocking=True)
    return x, torch.stack(ys).to(dev).float(), torch.stack(ms).to(dev)


def train(arm, synth=False):
    tag = f"unet_{arm}" + (f"_s{SEED}" if SEED_TAG else "")
    setup_log(tag[5:])
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    src, years = ("synth", [2016, 2017]) if synth else ARMS[arm]
    if YEARS_LIMIT:
        years = years[-YEARS_LIMIT:]
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    data = Data(src, years, synth=synth)
    log.info("arm %s src %s train years %s; %d input channels (%d dynamic, %d static incl. land); data on %s; "
             "loaded in %.0f s", arm, src, years, data.n_in, data.years[years[0]]["x"].shape[1],
             data.static.shape[0], data.dev, time.time() - t0)
    if dev.type == "cuda":
        log.info("gpu %s, memory used %.1f GB", torch.cuda.get_device_name(0), torch.cuda.memory_allocated() / 1e9)
    # positive cells: label > 0.5 and mask; for soft labels (harm pre-2012) this keeps the confident ones
    pos = []
    for yi, y in enumerate(data.years):
        t = data.years[y]
        nz = torch.nonzero((t["y"] > 0.5) & t["m"]).cpu()
        pos.append(torch.cat([torch.full((len(nz), 1), yi), nz], 1))
    pos_index = torch.cat(pos)
    n_cells = sum(int(t["m"].sum()) for t in data.years.values())
    log.info("training positives (label > 0.5, masked) %d of %d masked cell-days (%.4f%%)", len(pos_index), n_cells,
             100 * len(pos_index) / max(n_cells, 1))
    val = data.load_eval(VAL_YEAR if not synth else 2018)
    model = UNet(data.n_in, WIDTH).to(dev).to(memory_format=torch.channels_last)
    log.info("model: U-Net width %d, %.2fM parameters", WIDTH, sum(p.numel() for p in model.parameters()) / 1e6)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=LR, total_steps=EPOCHS * STEPS, pct_start=0.1)
    gen = torch.Generator().manual_seed(SEED)
    pw = torch.tensor(POS_WEIGHT, device=dev)
    best, best_ep, hist = -1.0, -1, []
    ck = os.path.join(OUT, "models", f"{tag}.pt")
    tstart = time.time()
    for ep in range(EPOCHS):
        model.train()
        tl, te = 0.0, time.time()
        for _ in range(STEPS):
            x, y, m = gather(data, sample_batch(data, pos_index, gen, BATCH))
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=dev.type == "cuda"):
                lg = model(x.contiguous(memory_format=torch.channels_last))
            loss = F.binary_cross_entropy_with_logits(lg.float(), y, pos_weight=pw, reduction="none")
            loss = (loss * m).sum() / m.sum().clamp_min(1)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tl += float(loss.detach())
        vl = predict(model, val["x"], data.static, dev)
        vm = masked_metrics(vl, val["y"], val["m"], dev)
        hist.append({"epoch": ep, "train_loss": tl / STEPS, "val_bce_raw": vm["bce"], "val_ap": vm["ap"],
                     "val_auc": vm["auc"], "s": time.time() - te})
        log.info("epoch %2d train loss %.4f | val bce(raw) %.4f AP %.4f AUC %.4f | %.0f s", ep, tl / STEPS, vm["bce"],
                 vm["ap"], vm["auc"], time.time() - te)
        if vm["ap"] > best:
            best, best_ep = vm["ap"], ep
            torch.save(model.state_dict(), ck)
        if ep - best_ep >= PATIENCE:
            log.info("early stop: best epoch %d (val AP %.4f)", best_ep, best)
            break
        if (time.time() - tstart) / 60 > MAX_MIN:
            log.info("time limit %.0f min reached", MAX_MIN)
            break
    model.load_state_dict(torch.load(ck, map_location=dev))
    vl = predict(model, val["x"], data.static, dev)
    a, b, thr, csi = platt(vl, val["y"], val["m"], dev)
    vcal = masked_metrics(a * vl + b, val["y"], val["m"], dev)
    log.info("best epoch %d; Platt a %.3f b %.3f; val calibrated AP %.4f AUC %.4f Brier %.5f; threshold %.4f "
             "(val CSI %.4f)", best_ep, a, b, vcal["ap"], vcal["auc"], vcal["brier"], thr, csi)
    p_val = torch.sigmoid(a * vl + b).numpy().astype(np.float16)
    del val
    # test inference, 2021-2024, calibrated probabilities
    ps, dates, test = [], [], {}
    for ty in (TEST_YEARS if not synth else [2019]):
        t = data.load_eval(ty)
        lg = a * predict(model, t["x"], data.static, dev) + b
        test[ty] = masked_metrics(lg, t["y"], t["m"], dev)
        log.info("test %d (reported only): AP %.4f AUC %.4f Brier %.5f", ty, test[ty]["ap"], test[ty]["auc"],
                 test[ty]["brier"])
        p = torch.sigmoid(lg).numpy().astype(np.float16)
        ps.append(p)
        dates.append(t["dates"])
        del t
    meta = {"arm": arm, "src": src, "train_years": years, "val_year": VAL_YEAR, "best_epoch": best_ep,
            "platt_a": a, "platt_b": b, "threshold": thr, "val_csi": csi, "val": vcal, "test": test,
            "history": hist, "channels": data.names, "width": WIDTH, "batch": BATCH, "steps": STEPS,
            "pos_frac": POS_FRAC, "pos_weight": POS_WEIGHT, "lr": LR, "seed": SEED,
            "train_minutes": (time.time() - tstart) / 60}
    name = tag if not synth else "unet_smoke"
    pdir = os.path.join(OUT, "preds", "seeds") if SEED_TAG else os.path.join(OUT, "preds")
    os.makedirs(pdir, exist_ok=True)
    np.savez_compressed(os.path.join(pdir, f"{name}.npz"), p=np.concatenate(ps), arm=arm, p_val=p_val,
                        dates=np.concatenate(dates), threshold=np.float32(thr), model="unet")
    with open(os.path.join(OUT, "models", f"{name}.json"), "w") as f:
        json.dump(meta, f, indent=1, default=float)
    log.info("wrote preds/%s.npz and models/%s.json; total %.1f min", name, name, (time.time() - t0) / 60)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "train" and len(sys.argv) > 2 and sys.argv[2] in ARMS:
        train(sys.argv[2])
    elif cmd == "smoke":
        train("smoke", synth=True)
    else:
        print(__doc__)
        sys.exit(2)
