#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pandas", "tabulate", "pyarrow", "lightgbm", "rasterio", "shapely>=2.1", "pyproj"]
# ///
"""Re-polygonize the Aug 2 most-likely (lower) and worst-case (upper) forecast files with display smoothing.

Reads the existing earth/public/data/forecasts_bounds/{id}/2026-08-02T1200-{lower,upper}.json and their cut rasters
(data/bounds/runs/map20260802-{id}/lower/outputs, upper_v2/outputs), reruns scripts/forecast_to_geojson.py with
FORECAST_SMOOTH_M (default 120 m) and writes the new geometry, bands, acres and summary back into the same files,
keeping every other key (model text, bound metadata). Area per band is preserved by the smoothing; rasters and
scores are untouched. index.json is not changed.
Usage: scripts/smooth_bounds.py [FIRE_ID ...]
"""
import json
import logging
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "earth/public/data/forecasts_bounds"
RUNS = ROOT / "data/bounds/runs"
TMP = ROOT / "data/bounds/tmp_smooth"
FEDS = ROOT / "earth/public/data/fires/feds/2026/08"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)
GEOM_KEYS = ("features", "summary", "hours", "perimeter_time", "grid_resolution_m")


def one(fid, case):
    fire = f"map20260802-{fid}"
    run = RUNS / fire / ("lower" if case == "lower" else "upper_v2") / "outputs"
    dst = OUT / fid / f"2026-08-02T1200-{case}.json"
    if not run.exists() or not dst.exists():
        return f"{fid} {case}: skipped (no raster or file)"
    old = json.loads(dst.read_text())
    sys.path.insert(0, str(ROOT / "scripts"))
    import growth_bounds as gb
    snap = gb.snapshot_place(int(fid))
    tmp = TMP / fid / case
    shutil.rmtree(tmp, ignore_errors=True)
    cmd = ["uv", "run", "--script", "scripts/forecast_to_geojson.py", str(run), str(gb.fire_file(fire, "fire-state.tif")),
           "2026-08-02T12:00", fid, old["fire"]["name"], old["model"], str(tmp), "3,6,12"] + (["--perimeter", str(snap)] if snap else [])
    env = dict(os.environ, FORECAST_SMOOTH_M=os.environ.get("FORECAST_SMOOTH_M", "120"))
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode:
        return f"{fid} {case}: FAILED {r.stdout[-300:]} {r.stderr[-300:]}"
    new = json.loads((tmp / fid / "2026-08-02T1200.json").read_text())
    for k in GEOM_KEYS:
        if k in new:
            old[k] = new[k]
    dst.write_text(json.dumps(old, separators=(",", ":"), ensure_ascii=False))
    a = lambda d: sum(f["properties"]["acres"] for f in d["features"] if f["properties"]["kind"] == "band")
    return f"{fid} {case}: ok, growth {a(old)} acres"


def main(ids):
    os.chdir(ROOT)
    ids = ids or sorted(p.name for p in OUT.iterdir() if p.is_dir())
    jobs = [(i, c) for i in ids for c in ("lower", "upper")]
    with ThreadPoolExecutor(int(os.environ.get("WORKERS", "8"))) as ex:
        for msg in ex.map(lambda j: one(*j), jobs):
            log.info(msg)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
