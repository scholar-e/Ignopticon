#!/usr/bin/env python3
"""Place-name labels for the ops map: states, cities and rivers from Natural Earth (public domain), as one GeoJSON.

Inputs (https://github.com/nvkelso/natural-earth-vector/tree/master/geojson), in IN_DIR:
    ne_10m_populated_places_simple, ne_10m_admin_1_label_points, ne_10m_rivers_lake_centerlines,
    ne_10m_rivers_north_america (.geojson)
Keeps the lower 48 and the edges of Canada and Mexico. Each feature has k (state | city | river), n (name) and
z (the zoom from which Natural Earth suggests labelling it); cities also have p (population, for priority).
Points are rounded to 3 decimals (~100 m), river lines to 2 (~1 km).

State label anchors: the centre of each state's largest piece (ne_50m_admin_1_states_provinces), or the official
label point nearest it when the shape is concave. Optional BORDERS.json: state/province lines and the US land borders
(ne_10m_admin_1_states_provinces_lines, ne_10m_admin_0_boundary_lines_land).

Usage: make_labels.py IN_DIR OUT.json [BORDERS.json]
"""
import json
import os
import sys

BOX = (-130.0, 22.0, -60.0, 55.0)  # west, south, east, north
COUNTRIES = {"US", "CA", "MX"}


def inside(lon, lat):
    return BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]


def load(in_dir, name):
    with open(os.path.join(in_dir, name + ".geojson"), encoding="utf-8") as f:
        return json.load(f)["features"]


def rnd(c):
    return [round(c[0], 3), round(c[1], 3)]


def area(ring):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:])) / 2


def centroid(ring):
    a = area(ring) or 1e-12
    cx = sum((x0 + x1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(ring, ring[1:])) / (6 * a)
    cy = sum((y0 + y1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(ring, ring[1:])) / (6 * a)
    return [cx, cy]


def contains(ring, pt):
    x, y, inside_ = pt[0], pt[1], False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside_ = not inside_
    return inside_


def borders(in_dir, out):
    """State/province lines (US, Canada, Mexico) and the US land borders, as lines at ~100 m precision."""
    feats = []
    for f in load(in_dir, "ne_10m_admin_1_states_provinces_lines"):
        p, g = f["properties"], f["geometry"]
        if p.get("ADM0_A3") in ("USA", "CAN", "MEX") and g:
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            lines = [l for l in (thin(line, 3) for line in lines if any(inside(*c[:2]) for c in line)) if len(l) >= 2]
            if lines:
                feats.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines}, "properties": {"k": "state"}})
    for f in load(in_dir, "ne_10m_admin_0_boundary_lines_land"):
        p, g = f["properties"], f["geometry"]
        if "USA" in (p.get("ADM0_A3_L"), p.get("ADM0_A3_R")) and g:
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            lines = [l for l in (thin(line, 3) for line in lines) if len(l) >= 2]
            feats.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines}, "properties": {"k": "country"}})
    with open(out, "w", encoding="utf-8") as fo:
        json.dump({"type": "FeatureCollection", "features": feats}, fo, separators=(",", ":"))
    print(out, len(feats), "features", os.path.getsize(out) // 1024, "KB")


def thin(line, digits=2):
    out = []
    for c in line:
        c = [round(c[0], digits), round(c[1], digits)]
        if not out or c != out[-1]:
            out.append(c)
    return out


def main(in_dir, out):
    feats, states, candidates = [], {}, {}
    for f in load(in_dir, "ne_10m_admin_1_label_points"):  # one per piece (islands, parts); a fallback only
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        if p.get("iso_a2") in COUNTRIES and p.get("name"):
            candidates.setdefault((p["iso_a2"], p["name"]), []).append([lon, lat])
    for f in load(in_dir, "ne_50m_admin_1_states_provinces"):
        p, g = f["properties"], f["geometry"]
        if p.get("iso_a2") not in COUNTRIES or not p.get("name") or not g:
            continue
        rings = [poly[0] for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])]
        ring = max(rings, key=lambda r: abs(area(r)))  # the mainland
        c = centroid(ring)
        if not contains(ring, c):  # concave shape: use the official label point nearest the centre instead
            pts = candidates.get((p["iso_a2"], p["name"])) or [c]
            c = min(pts, key=lambda q: (q[0] - c[0]) ** 2 + (q[1] - c[1]) ** 2)
        if inside(*c):
            states[(p["iso_a2"], p["name"])] = (0, c)
    for (iso, name), (_, ll) in states.items():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": rnd(ll)},
                      "properties": {"k": "state", "n": name, "z": 3 if iso == "US" else 4}})
    for f in load(in_dir, "ne_10m_populated_places_simple"):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        if p.get("iso_a2") in COUNTRIES and inside(lon, lat):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": rnd([lon, lat])},
                          "properties": {"k": "city", "n": p["name"], "z": float(p.get("min_zoom") or 6),
                                         "p": int(p.get("pop_max") or 0)}})
    rivers = {}
    for src in ("ne_10m_rivers_lake_centerlines", "ne_10m_rivers_north_america"):
        for f in load(in_dir, src):
            p, g = f["properties"], f["geometry"]
            name = p.get("name_en") or p.get("name")
            if not name or not g or "River" not in (p.get("featurecla") or "River"):
                continue
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            # Rivers only carry names along them: ~1 km precision, repeated points dropped.
            lines = [thin(line) for line in lines if any(inside(*c[:2]) for c in line)]
            lines = [line for line in lines if len(line) >= 2]
            if lines:
                key = (name, src)
                rivers.setdefault(key, {"lines": [], "z": float(p.get("min_label") or p.get("min_zoom") or 7)})
                rivers[key]["lines"] += lines
    seen = set()
    for (name, src), r in rivers.items():
        if name in seen:  # both sources name the big rivers: keep the first (world centerlines)
            continue
        seen.add(name)
        feats.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": r["lines"]},
                      "properties": {"k": "river", "n": name, "z": r["z"]}})
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, separators=(",", ":"), ensure_ascii=False)
    kinds = {}
    for x in feats:
        kinds[x["properties"]["k"]] = kinds.get(x["properties"]["k"], 0) + 1
    print(out, kinds, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
    if len(sys.argv) > 3:
        borders(sys.argv[1], sys.argv[3])
