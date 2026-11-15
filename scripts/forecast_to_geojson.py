#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pyproj", "rasterio", "scipy", "shapely>=2.1"]
# ///
"""Turn one fire model run into the forecast file earth's ops page reads: the area burned at ISSUE, non-overlapping
growth bands (0-6 h, 6-12 h, ...), the extent at each hour (start + front bands so far, one shape), and arrows for the largest predicted advances, as GeoJSON in EPSG:4326.

A run is ELMFIRE's (or ellipse_spread.py's) time_of_arrival_*.tif: s since ISSUE; nodata, negative, -9999 or
missing = not burned; 0 = burning at ISSUE (ELMFIRE's ignition). The area burned at ISSUE is the state's cells
(fire_observations.py: s since the tif's "reference" tag) arrived at or before ISSUE, put on the run's grid, plus
TOA cells of 0. Acres come from cell counts x cell area; distances are measured in the run's projected CRS (metres).

Each connected region of growth (8-connected) is "front", or, given a FEDS perimeter (--perimeter, the snapshot's
feature with id FIRE_ID): "interior" if >= INTERIOR_FRACTION of its cells are inside the perimeter (burnout), or
"detached" if its nearest cell is > DETACHED_KM outside it (often stray hot cells, e.g. irrigated fields). Bands are
one feature per (hours, where); a band's empty "front" feature is kept so the UI can say "no growth predicted".

Usage: forecast_to_geojson.py RUN_DIR STATE.tif ISSUE FIRE_ID "FIRE NAME" "MODEL DESCRIPTION" OUT_DIR [HOURS]
                              [--perimeter FEDS.json]
       e.g. forecast_to_geojson.py data/fires/runs/ellipse-0.02 data/fires/widemouth-2026-08-02-state.tif \
                2026-08-02T12:00 66510 "Widemouth 2" "Wind ellipse baseline (head spread 0.02 m/s, GFS 10 m wind) \
                — not ELMFIRE" earth/public/data/forecasts 6,12,24 \
                --perimeter earth/public/data/fires/feds/2026/08/02-1200.json
       forecast_to_geojson.py --add-extents FORECAST.json...   (adds the per-hour "extent" features to older files)
Writes OUT_DIR/FIRE_ID/YYYY-MM-DDTHHMM.json and adds (or replaces) its entry in OUT_DIR/index.json.
"""
import datetime
import glob
import json
import logging
import math
import os
import sys

import numpy as np
import rasterio
import rasterio.features
import shapely
from scipy import ndimage
from pyproj import Geod, Transformer
from rasterio.warp import Resampling, reproject
from shapely.geometry import MultiPolygon, mapping, shape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

ACRE_M2 = 4046.8564224
CHAIN_M = 20.1168
SLIVER_CELLS = 2        # start-area polygons and holes smaller than this are dropped (never bands: they'd lose acres)
SIMPLIFY_CELLS = 0.5    # coverage_simplify tolerance in cell widths; more erodes the thin 0-6 h rims past 5%
ARROW_MIN_M = 375       # one VIIRS pixel: smaller advances are within the observation's own error
ARROW_MIN_ACRES = 50    # growth regions smaller than this get no arrow
ARROW_MAX = 6            # per class
ARROW_SPACING_KM = 3     # within a class, heads closer than this to a bigger region's arrow are skipped
MAX_WALK = 10000        # cells an arrow's path may walk before giving up
INTERIOR_FRACTION = 0.9
DETACHED_KM = 1.0
WHERE = ("front", "interior", "detached")


def polygons(mask, transform, min_area):
    """Polygons (run CRS) of a boolean mask, slivers and tiny holes dropped."""
    out = []
    for geom, _ in rasterio.features.shapes(mask.astype(np.uint8), mask=mask, transform=transform):
        p = shape(geom)
        if p.area >= min_area:
            out.append(shapely.Polygon(p.exterior, [h for h in p.interiors if shapely.Polygon(h).area >= min_area]))
    return out


def num(h):
    return int(h) if h == int(h) else h


def main(run_dir, state_tif, issue, fire_id, name, model, out_dir, hours="6,12,24", perimeter=None):
    hours, fire_id = [float(h) for h in hours.split(",")], int(fire_id)
    issue_t = datetime.datetime.fromisoformat(issue).replace(tzinfo=datetime.timezone.utc)
    toa_file = sorted(glob.glob(os.path.join(run_dir, "time_of_arrival_*.tif")))[-1]
    with rasterio.open(toa_file) as src:
        toa = src.read(1).astype(np.float64)
        crs, transform, nodata = src.crs, src.transform, src.nodata
    cell = abs(transform.a * transform.e)  # cell area, m2
    valid = np.isfinite(toa) & (toa >= 0) & ((toa != nodata) if nodata is not None else True) & (toa != -9999)

    with rasterio.open(state_tif) as src:
        state = src.read(1)
        reference = datetime.datetime.strptime(src.tags()["reference"], "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=datetime.timezone.utc)
        before = ((state != src.nodata) & (state <= (issue_t - reference).total_seconds())).astype(np.uint8)
        start = np.zeros(toa.shape, np.uint8)
        reproject(before, start, src_transform=src.transform, src_crs=src.crs, dst_transform=transform,
                  dst_crs=crs, resampling=Resampling.nearest)
    # Runs built with OBSERVED reconstruct their initial footprint from data
    # available at issue. The interpolated state raster can use later snapshots.
    provenance = None
    metadata_path = os.path.join(run_dir, "run.json")
    if os.path.exists(metadata_path):
        metadata = json.load(open(metadata_path))
        if any(v.startswith("OBSERVED=") for v in metadata.get("overrides", [])):
            from elmfire_perimeters import known_start
            start, provenance = known_start(metadata, toa.shape, transform, crs)
    start = start.astype(bool) | (valid & (toa <= 0))

    taken = valid & (toa <= hours[-1] * 3600) | start
    regions, n = ndimage.label(taken & ~start, structure=np.ones((3, 3)))
    sizes = np.bincount(regions.ravel())[1:] * cell / ACRE_M2
    where = np.zeros(n + 1, int)  # per region, index into WHERE; all "front" without a perimeter
    perimeter_time = None
    if perimeter:
        feds = json.load(open(perimeter, encoding="utf-8"))
        perimeter_time = feds["time"].rstrip("Z") + "Z"
        fire = next((f for f in feds["features"] if int(f["properties"]["id"]) == fire_id), None)
        if fire is None:  # FEDS leaves a fire out of snapshots without detections: point at the last one listing it
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(perimeter))))  # feds/YYYY/MM/DD-HHMM
            earlier = [p for p in sorted(glob.glob(os.path.join(root, "*", "*", "*.json")), reverse=True)
                       if os.path.relpath(p, root) < os.path.relpath(os.path.abspath(perimeter), root)]
            hit = next((p for p in earlier if any(int(f["properties"]["id"]) == fire_id
                                                  for f in json.load(open(p, encoding="utf-8"))["features"])), None)
            sys.exit("%s has no feature with id %d.%s" % (perimeter, fire_id, " The latest earlier snapshot listing it "
                     "is %s; pass that as --perimeter." % hit if hit else " No earlier snapshot lists it either."))
        from_ll = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
        outline = shapely.transform(shape(fire["geometry"]), lambda xy: np.column_stack(from_ll(xy[:, 0], xy[:, 1])))
        inside_perimeter = rasterio.features.rasterize([outline], toa.shape, transform=transform).astype(bool)
        to_perimeter = ndimage.distance_transform_edt(~inside_perimeter, sampling=(abs(transform.e), transform.a))
        idx = np.arange(1, n + 1)
        fraction_in = np.asarray(ndimage.mean(inside_perimeter, regions, idx))
        nearest = np.asarray(ndimage.minimum(to_perimeter, regions, idx))
        where[1:] = np.where(fraction_in >= INTERIOR_FRACTION, 1, np.where(nearest > DETACHED_KM * 1000, 2, 0))
    where_grid = where[regions]

    masks = [("start", 0, 0, None, start)]
    summary, lo = {}, 0.0
    for h in hours:
        band = valid & (toa > lo * 3600) & (toa <= h * 3600) & ~start
        summary[str(num(h))] = {}
        for w, label in enumerate(WHERE):
            m = band & (regions > 0) & (where_grid == w)
            summary[str(num(h))][label] = round(int(m.sum()) * cell / ACRE_M2)
            if m.any() or label == "front":
                masks.append(("band", h, lo, label, m))
        lo = h

    # One coverage, simplified together so neighbouring bands keep shared edges (no gaps or overlaps). The tolerance is
    # Visvalingam-Whyatt's sqrt(triangle area): one cell width removes stair steps, moving edges up to ~half a cell.
    pieces = [(i, p) for i, m in enumerate(masks) for p in polygons(m[4], transform, SLIVER_CELLS * cell if i == 0 else 0)]
    simple = shapely.coverage_simplify([p for _, p in pieces], SIMPLIFY_CELLS * math.sqrt(cell)) if pieces else []
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    features, projected = [], []
    for i, (kind, h, lo, label, m) in enumerate(masks):
        parts = [g for (j, _), g in zip(pieces, simple) if j == i and not g.is_empty]
        geom = shapely.make_valid(MultiPolygon([q for g in parts for q in getattr(g, "geoms", [g])]))
        projected.append(geom)
        ll = shapely.set_precision(shapely.transform(geom, lambda xy: np.column_stack(to_ll(xy[:, 0], xy[:, 1]))), 1e-5)
        ll = shapely.make_valid(ll)
        polys = [shapely.geometry.polygon.orient(q, 1.0) for q in getattr(ll, "geoms", [ll])
                 if q.geom_type == "Polygon" and not q.is_empty]
        props = {"kind": kind, "hours": num(h)}
        if kind == "band":
            props["from_hours"], props["where"] = num(lo), label
        props["acres"] = round(int(m.sum()) * cell / ACRE_M2)
        features.append({"type": "Feature", "properties": props,
                         "geometry": mapping(MultiPolygon(polys)) if polys else {"type": "MultiPolygon", "coordinates": []}})
        log.info("%s %s %g h: %d cells, %d acres, %d polygons", kind, label or "", h, int(m.sum()), props["acres"], len(polys))

    # Arrows: one per growth region, biggest first, up to ARROW_MAX per class, skipping burnouts (interior to the
    # perimeter or the start area). Head = the region's cell farthest from the start area; the path walks steepest
    # descent on arrival time back to a cell burned at issue (the tail), i.e. the way the fire came.
    inside = ndimage.binary_fill_holes(start)
    far = ndimage.distance_transform_edt(~start, sampling=(abs(transform.e), transform.a))
    arrival = np.where(start, -1.0, np.where(taken, toa, np.inf))
    steps = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]  # fixed order: ties go to the first
    geod, arrows = Geod(ellps="WGS84"), [[] for _ in WHERE]  # capped per class: detached never takes a front place
    for k in np.argsort(-sizes):
        if sizes[k] < ARROW_MIN_ACRES:
            break
        member = regions == k + 1
        if where[k + 1] == 1 or len(arrows[where[k + 1]]) == ARROW_MAX or inside[member].all():
            continue
        r, c = np.unravel_index(np.argmax(np.where(member, far, -1)), far.shape)
        head, path_m = (r, c), 0.0
        xy = transform * (c + 0.5, r + 0.5)
        if any(math.dist(xy, q) < ARROW_SPACING_KM * 1000 for q in arrows[where[k + 1]]):
            continue
        for _ in range(MAX_WALK):
            if start[r, c]:
                break
            nbrs = [(arrival[r + dr, c + dc], dr, dc) for dr, dc in steps
                    if 0 <= r + dr < toa.shape[0] and 0 <= c + dc < toa.shape[1]]
            t, dr, dc = min(nbrs, key=lambda x: x[0])
            if t >= arrival[r, c]:
                break
            r, c, path_m = r + dr, c + dc, path_m + math.hypot(dr * transform.e, dc * transform.a)
        if not start[r, c]:
            log.warning("arrow path from cell %s stopped at %s before the start area (flat or capped)", head, (r, c))
            continue
        (hx, hy), (tx, ty) = (transform * (cc + 0.5, rr + 0.5) for rr, cc in (head, (r, c)))
        d, t_h = math.hypot(hx - tx, hy - ty), float(toa[head]) / 3600
        if d < ARROW_MIN_M:
            continue
        (tx, ty), (hx, hy) = to_ll(tx, ty), to_ll(hx, hy)
        bearing = geod.inv(tx, ty, hx, hy)[0] % 360
        features.append({"type": "Feature", "properties": {
            "kind": "arrow", "hours": round(t_h, 1), "distance_m": round(d), "path_m": round(path_m),
            "bearing_deg": round(bearing), "rate_ch_per_h": round(d / CHAIN_M / t_h, 1) if t_h > 0 else None,
            "region_acres": round(sizes[k]), "where": WHERE[where[k + 1]]},
            "geometry": {"type": "LineString", "coordinates": [[round(tx, 5), round(ty, 5)],
                                                               [round(hx, 5), round(hy, 5)]]}})
        arrows[where[k + 1]].append(xy)
        log.info("arrow: %s region %.0f acres, head %.5f %.5f, %.0f m (path %.0f m) toward %.0f°, %.1f h, %.1f ch/h",
                 WHERE[where[k + 1]], sizes[k], hx, hy, d, path_m, bearing, t_h, d / CHAIN_M / t_h if t_h > 0 else float("nan"))

    features += extents(features, hours)
    issued = issue_t.strftime("%Y-%m-%dT%H:%M:%SZ")
    rel = "%d/%s.json" % (fire_id, issue_t.strftime("%Y-%m-%dT%H%M"))
    run_json = os.path.join(run_dir, "run.json")
    ellipse = os.path.exists(run_json) and "ellipse" in json.load(open(run_json)).get("model", "")
    doc = {"type": "FeatureCollection", "fire": {"id": fire_id, "name": name}, "issued": issued, "model": model,
           "hours": [h if h != int(h) else int(h) for h in hours],
           "source": ("scripts/ellipse_spread.py" if ellipse else "ELMFIRE") + " -> scripts/forecast_to_geojson.py",
           "perimeter_time": perimeter_time, "summary": summary, "features": features,
           "initial_state_provenance": provenance,
           "grid_resolution_m": [abs(transform.a), abs(transform.e)]}
    out = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(doc, open(out, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)

    index_path = os.path.join(out_dir, "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {"forecasts": []}
    index["forecasts"] = [f for f in index["forecasts"] if (f["fire_id"], f["issued"]) != (fire_id, issued)]
    index["forecasts"].append({"fire_id": fire_id, "name": name, "issued": issued, "model": model, "path": rel})
    index["forecasts"].sort(key=lambda f: (f["fire_id"], f["issued"]))
    json.dump(index, open(index_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log.info("wrote %s (%d bytes) and %s", out, os.path.getsize(out), index_path)

    # Self-check of what was written: valid geometry, and drawn band areas match their cell-count acres.
    for f in doc["features"]:
        g, props = shape(f["geometry"]), f["properties"]
        if not g.is_valid:
            log.warning("invalid %s %s geometry: %s", props["kind"], props["hours"], shapely.is_valid_reason(g))
        if props["kind"] == "band" and props["acres"]:
            drawn = abs(geod.geometry_area_perimeter(g)[0]) / ACRE_M2
            log.log(logging.WARNING if abs(drawn / props["acres"] - 1) > 0.05 else logging.INFO,
                    "band %s h %s: drawn %.0f acres vs %d from cells (%+.1f%%)", props["hours"], props["where"], drawn, props["acres"],
                    100 * (drawn / props["acres"] - 1))


def extents(features, hours):
    """The fire's predicted extent at each hour: start area plus front bands up to it, dissolved into one shape."""
    out = []
    for h in hours:
        parts = [f for f in features if f["properties"]["kind"] == "start" or
                 (f["properties"]["kind"] == "band" and f["properties"]["where"] == "front" and f["properties"]["hours"] <= h)]
        g = shapely.make_valid(shapely.union_all([shape(f["geometry"]) for f in parts], grid_size=1e-5))
        polys = [shapely.geometry.polygon.orient(q, 1.0) for q in getattr(g, "geoms", [g]) if q.geom_type == "Polygon" and not q.is_empty]
        out.append({"type": "Feature", "properties": {"kind": "extent", "hours": num(h), "acres": sum(f["properties"]["acres"] for f in parts)},
                    "geometry": mapping(MultiPolygon(polys))})
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--add-extents"]:  # older forecast files: add the extents in place
        for path in args[1:]:
            doc = json.load(open(path, encoding="utf-8"))
            doc["features"] = [f for f in doc["features"] if f["properties"]["kind"] != "extent"]
            doc["features"] += extents(doc["features"], doc["hours"])
            json.dump(doc, open(path, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
            log.info("added extents to %s", path)
        sys.exit()
    perimeter = args.pop(args.index("--perimeter") + 1) if "--perimeter" in args else None
    main(*[a for a in args if a != "--perimeter"][:8], perimeter=perimeter)
