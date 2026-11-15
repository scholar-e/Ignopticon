#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "rasterio", "shapely", "pillow"]
# ///
"""Population density layer and fire exposure from GHSL GHS-POP R2023A (2025 epoch, 30 arcsec, ~1 km; CC BY 4.0, EC JRC).

  Data: curl -L the URL below into data/population/raw/pop.zip (484 MB; unzipped on first use).
  population_layer.py layer      clip to CONUS, write earth/public/data/population/density.png (+ meta.json)
  population_layer.py exposure   people inside each Aug 2 fire's perimeter / most-likely / worst-case footprint
                                 -> earth/public/data/population/exposure_20260802.json (re-run after forecasts_bounds change)
"""
import json, logging, sys, subprocess, zipfile
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/population/raw"
OUT = ROOT / "earth/public/data/population"
BOUNDS = ROOT / "earth/public/data/forecasts_bounds"
FC = ROOT / "earth/public/data/forecasts"
URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_4326_30ss/"
       "V1-0/GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip")
W, E, S, N = -125.0, -66.0, 24.0, 50.0
log = logging.getLogger("population")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "data/population/population.log")])
for h in logging.getLogger().handlers: h.flush = h.flush


def tif():
    t = sorted(RAW.glob("*.tif"))
    if not t:
        z = RAW / "pop.zip"
        log.info("unzipping %s", z)
        with zipfile.ZipFile(z) as f: f.extractall(RAW)
        t = sorted(RAW.glob("*.tif"))
    return t[0]


def conus():
    """People per cell (float32) over CONUS, plus affine transform; nodata and negatives -> 0."""
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(tif()) as ds:
        win = from_bounds(W, S, E, N, ds.transform).round_offsets().round_lengths()
        a = ds.read(1, window=win).astype("float32")
        tr = ds.window_transform(win)
        a[(a < 0) | ~np.isfinite(a) | (a == ds.nodata)] = 0
    log.info("CONUS grid %s, total %.0f people", a.shape, a.sum())
    return a, tr


def cell_km2(tr, rows):
    lat = tr.f + (np.arange(rows) + 0.5) * tr.e
    return (111.32 * abs(tr.a)) * np.cos(np.radians(lat)) * (110.57 * abs(tr.e))


def layer():
    from PIL import Image
    a, tr = conus()
    # 2 x 2 blocks (~2 km) keep the PNG small; density = summed people / summed area
    km2 = np.broadcast_to(cell_km2(tr, a.shape[0])[:, None], a.shape)
    blk = lambda x: x.reshape(x.shape[0] // 2, 2, x.shape[1] // 2, 2).sum((1, 3))
    a = a[: a.shape[0] // 2 * 2, : a.shape[1] // 2 * 2]; km2 = km2[: a.shape[0], : a.shape[1]]
    dens = blk(a) / blk(km2)
    from rasterio.transform import Affine
    tr = Affine(tr.a * 2, 0, tr.c, 0, tr.e * 2, tr.f)
    a = dens
    t = np.clip(np.log10(np.maximum(dens, 1e-9)) / 4.0, 0, 1)   # 1 -> 0, 10,000 -> 1
    stops = np.array([[255, 237, 160], [254, 178, 76], [240, 59, 32], [165, 15, 90], [60, 0, 90]], float)
    pos = np.linspace(0, 1, len(stops))
    lv = np.round(t * 47).astype(np.uint8) + 1                  # 48 palette levels, index 0 = transparent
    lv[dens < 1] = 0
    pal = np.zeros((256, 3), np.uint8)
    for i in range(48): pal[i + 1] = [np.interp(i / 47, pos, stops[:, c]) for c in range(3)]
    img = Image.fromarray(lv, "P"); img.putpalette(pal.flatten().tolist()); img.info["transparency"] = bytes([0] + [205] * 255)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "density.png"
    img.save(p, optimize=True, transparency=img.info["transparency"])
    meta = {"west": tr.c, "north": tr.f, "east": tr.c + tr.a * a.shape[1], "south": tr.f + tr.e * a.shape[0],
            "width": a.shape[1], "height": a.shape[0], "scale": "log10 people/km2, 1..10000", "stops": stops.tolist(),
            "source": "GHS-POP R2023A 2025, 30 arcsec, European Commission JRC (CC BY 4.0)"}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    log.info("wrote %s (%.2f MB) %s", p, p.stat().st_size / 1e6, meta)


def footprint(path, kinds):
    """Union of the 'start' polygon and the largest-hour 'extent' polygon in a forecast GeoJSON."""
    from shapely.geometry import shape
    from shapely.ops import unary_union
    d = json.loads(Path(path).read_text())
    fs = d["features"]
    ext = [f for f in fs if f["properties"].get("kind") == "extent"]
    parts = [f for f in fs if f["properties"].get("kind") == "start"]
    if "extent" in kinds and ext: parts.append(max(ext, key=lambda f: f["properties"]["hours"]))
    return unary_union([shape(f["geometry"]) for f in parts]) if parts else None


def people(geom, a, tr, sub=10):
    """People inside geom: each cell's count times the fraction of the cell covered (sub x sub sampling)."""
    if geom is None or geom.is_empty: return 0.0
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    x0, y0, x1, y1 = geom.bounds
    c0 = max(int(np.floor((x0 - tr.c) / tr.a)), 0); c1 = min(int(np.ceil((x1 - tr.c) / tr.a)), a.shape[1])
    r0 = max(int(np.floor((y1 - tr.f) / tr.e)), 0); r1 = min(int(np.ceil((y0 - tr.f) / tr.e)), a.shape[0])
    if c1 <= c0 or r1 <= r0: return 0.0
    t = Affine(tr.a / sub, 0, tr.c + c0 * tr.a, 0, tr.e / sub, tr.f + r0 * tr.e)
    m = rasterize([(geom, 1)], out_shape=((r1 - r0) * sub, (c1 - c0) * sub), transform=t, dtype="uint8")
    frac = m.reshape(r1 - r0, sub, c1 - c0, sub).mean((1, 3))
    return float((frac * a[r0:r1, c0:c1]).sum())


def exposure():
    a, tr = conus()
    idx = json.loads((BOUNDS / "index.json").read_text())["forecasts"]
    res = {}
    for e in idx:
        i = e["fire_id"]
        lo, up = BOUNDS / e["path_lower"], BOUNDS / e["path_upper"]
        if not lo.exists(): lo = next(iter(sorted((FC / str(i)).glob("*.json"))), None)
        cur = footprint(up, ())
        likely = footprint(lo, ("extent",)) if lo else None
        worst = footprint(up, ("extent",))
        res[str(i)] = {"name": e["name"], "issued": e["issued"], "current": round(people(cur, a, tr)),
                       "likely": round(people(likely, a, tr)), "worst": round(people(worst, a, tr))}
        log.info("%s %s %s", i, e["name"], res[str(i)])
    out = {"source": "GHS-POP R2023A 2025, ~1 km, European Commission JRC (CC BY 4.0)", "issued": "2026-08-02T12:00Z",
           "note": "people in cells weighted by covered fraction; unrounded", "fires": res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "exposure_20260802.json").write_text(json.dumps(out, indent=1))
    log.info("wrote %d fires", len(res))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"layer": layer, "exposure": exposure}.get(cmd, lambda: sys.exit(__doc__))()
