#!/usr/bin/env python3
"""Builds a fire's offline navigation package for the ops page (earth/public/ops/nav.js).

The package holds what the browser needs to route without a connection, for an area AREA_KM around the fire:
  - a slimmed road + trail network from OpenStreetMap (Overpass API): junctions and the segments between them,
    each with its length, driving speed (0 = not drivable), walking times both ways (Tobler's hiking function on
    the terrain), flags (walkable, private/restricted, gate, 4x4/rough, trail, oneway) and a simplified geometry;
  - an elevation grid (~150 m, from AWS Terrain Tiles through the server's tile cache) for the in-browser
    repeater coverage estimate and off-trail walking times;
  - cell coverage on the same grid: FCC cellular tower sites (HIFLD "Cellular Towers") each run through SPLAT!
    by the server (/api/coverage, coverage.js), merged by maximum received power and classed covered (>= -100 dBm),
    marginal (-110..-100) or none (< -110). Antenna power and pattern are not public: every site is assumed to be
    an 850 MHz omnidirectional 400 W ERP macro cell at 90% of its structure height, so coverage is an estimate.

Usage: nav_prep.py FIRE_ID [--time ISO] [--server http://localhost:8080] [--out earth/public/data/nav]
The server (earth/dev-server.js) must be running: it supplies the fire, terrain tiles and SPLAT! runs.
Raw downloads are cached in data/raw/nav/FIRE_ID/.
"""
import argparse
import base64
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

AREA_KM = 50            # network and grid extend this far beyond the fire's bounding box
CELL_M = 150            # grid cell size
TOWER_RADIUS_KM = 25    # SPLAT! radius per tower; towers up to this far outside the area are included
TERRAIN_ZOOM = 11
OVERPASS = "https://overpass-api.de/api/interpreter"
TOWERS = ("https://services.arcgis.com/0ZRg6WRC7mxSLyKX/ArcGIS/rest/services/Cellular_Towers/FeatureServer/0/query")
CELL = {"freq": 850, "erp": 400, "covered": -100, "marginal": -110}
UA = {"User-Agent": "ignopticon-nav-prep"}

# Driving speeds (km/h) by highway class; tracks by tracktype below. Classes not listed are not drivable.
DRIVE_KPH = {"motorway": 105, "motorway_link": 60, "trunk": 90, "trunk_link": 50, "primary": 80, "primary_link": 45,
             "secondary": 70, "secondary_link": 40, "tertiary": 60, "tertiary_link": 35, "unclassified": 45,
             "residential": 35, "living_street": 15, "service": 20, "road": 30, "track": 20}
TRACK_KPH = {"grade1": 30, "grade2": 25, "grade3": 18, "grade4": 10, "grade5": 8}
WALK_ONLY = {"path", "footway", "bridleway", "steps", "pedestrian", "cycleway", "trail"}
SKIP = {"proposed", "construction", "platform", "raceway", "bus_stop", "rest_area", "elevator", "corridor",
        "abandoned", "razed", "services", "emergency_bay", "busway", "via_ferrata"}
RESTRICTED = {"private", "forestry", "agricultural", "destination", "permit", "customers", "delivery", "official"}
ROUGH = {"very_bad", "horrible", "very_horrible", "impassable"}
F_WALK, F_PRIVATE, F_GATE, F_4X4, F_TRAIL, F_ONEWAY = 1, 2, 4, 8, 16, 32


def fetch(url, data=None, timeout=600, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):  # Python's CA bundle can lag the OS store: let curl verify instead
                return curl(url, data, timeout)
            if i == retries - 1:
                raise
            print("  retry after", e, file=sys.stderr)
            time.sleep(10 * (i + 1))
        except Exception as e:  # Overpass and ArcGIS throttle now and then
            if i == retries - 1:
                raise
            print("  retry after", e, file=sys.stderr)
            time.sleep(10 * (i + 1))


def curl(url, data=None, timeout=600):
    cmd = ["curl", "-sS", "--fail", "-m", str(timeout), "-A", UA["User-Agent"], url]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    return subprocess.run(cmd, input=data, capture_output=True, check=True).stdout


def cached(path, make):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    data = make()
    with open(path, "wb") as f:
        f.write(data)
    return data


def b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


# ---- Terrain: a z11 terrarium mosaic of the area, sampled onto the grid ----
def terrain_grid(server, west, south, east, north, w, h, dlon, dlat):
    n = 2 ** TERRAIN_ZOOM
    def px(lon):
        return (lon + 180) / 360 * n * 256
    def py(lat):
        s = math.sin(math.radians(lat))
        return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n * 256
    tx0, tx1 = int(px(west) // 256), int(px(east) // 256)
    ty0, ty1 = int(py(north) // 256), int(py(south) // 256)
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            png = fetch(f"{server}/tiles/terrain/{TERRAIN_ZOOM}/{tx}/{ty}")
            rgb = np.asarray(Image.open(BytesIO(png)).convert("RGB")).astype(np.float32)
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = \
                rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
    lons = west + (np.arange(w) + 0.5) * dlon
    lats = north - (np.arange(h) + 0.5) * dlat
    X = np.array([px(l) for l in lons]) - tx0 * 256 - 0.5
    Y = np.array([py(l) for l in lats]) - ty0 * 256 - 0.5
    yy, xx = np.meshgrid(Y, X, indexing="ij")
    return map_coordinates(mosaic, [yy, xx], order=1, mode="nearest").astype(np.float32)


# ---- Network ----
def parse_kph(v):
    try:
        if v.endswith("mph"):
            return float(v[:-3]) * 1.609
        return float(v)
    except Exception:
        return None


def build_graph(osm, elev, grid):
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in osm["elements"] if e["type"] == "node" and "lon" in e}
    gates = {e["id"] for e in osm["elements"] if e["type"] == "node" and e.get("tags", {}).get("barrier") in
             ("gate", "lift_gate", "swing_gate", "chain", "bollard")}
    ways = []
    for e in osm["elements"]:
        if e["type"] != "way":
            continue
        t = e.get("tags", {})
        hw = t.get("highway")
        if not hw or hw in SKIP or t.get("area") == "yes":
            continue
        access = t.get("access", "yes")
        kph = 0
        if hw in DRIVE_KPH and access != "no" and t.get("motor_vehicle", t.get("vehicle", "yes")) != "no":
            kph = TRACK_KPH.get(t.get("tracktype"), DRIVE_KPH[hw]) if hw == "track" else DRIVE_KPH[hw]
            kph = parse_kph(t.get("maxspeed", "")) or kph
        flags = 0
        if hw not in ("motorway", "motorway_link") and t.get("foot") != "no" and access != "no" or t.get("foot") in ("yes", "designated"):
            flags |= F_WALK
        if access in RESTRICTED or t.get("motor_vehicle") in RESTRICTED:
            flags |= F_PRIVATE
        if t.get("tracktype") in ("grade4", "grade5") or t.get("4wd_only") == "yes" or t.get("smoothness") in ROUGH:
            flags |= F_4X4
        if hw in WALK_ONLY:
            flags |= F_TRAIL
        oneway = t.get("oneway")
        refs = e["nodes"]
        if oneway == "-1":
            refs = refs[::-1]
        if oneway in ("yes", "1", "-1") or t.get("junction") == "roundabout":
            flags |= F_ONEWAY
        if not kph and not flags & F_WALK:
            continue
        ways.append((refs, kph, flags))
    uses = {}
    for refs, _, _ in ways:
        for i, r in enumerate(refs):
            uses[r] = uses.get(r, 0) + (2 if i in (0, len(refs) - 1) else 1)
    junction = {r for r, c in uses.items() if c > 1}

    def elev_at(lon, lat):
        c = min(grid["w"] - 1, max(0, int((lon - grid["west"]) / grid["dlon"])))
        r = min(grid["h"] - 1, max(0, int((grid["north"] - lat) / grid["dlat"])))
        return float(elev[r, c])

    def tobler_s(coords, sign):
        """Walking seconds along coords; slope measured over stretches of at least 60 m (the elevation grid is
        150 m, so shorter steps give noise) and capped at 45 degrees."""
        t, run, x0, y0 = 0.0, 0.0, *coords[0]
        for i, (x1, y1) in enumerate(coords[1:], 1):
            run += haversine(*coords[i - 1], x1, y1)
            if run < 60 and i < len(coords) - 1:
                continue
            if run > 0:
                slope = max(-1.0, min(1.0, sign * (elev_at(x1, y1) - elev_at(x0, y0)) / run))
                t += run / (6000 * math.exp(-3.5 * abs(slope + 0.05)) / 3600)
            run, x0, y0 = 0.0, x1, y1
        return t

    edges = []
    for refs, kph, flags in ways:
        start = 0
        gate = refs[0] in gates
        for i in range(1, len(refs)):
            gate = gate or refs[i] in gates
            if refs[i] in junction or i == len(refs) - 1:
                part = [nodes[r] for r in refs[start:i + 1] if r in nodes]
                if len(part) >= 2:
                    length = sum(haversine(*a, *b) for a, b in zip(part, part[1:]))
                    f = flags | (F_GATE if gate else 0)
                    edges.append((refs[start], refs[i], length, kph, f, tobler_s(part, 1), tobler_s(part, -1), simplify(part, 8)))
                start, gate = i, refs[i] in gates
    # Keep the largest connected piece: isolated bits of mapped road would only strand a route.
    parent = {}
    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, *_ in edges:
        parent[find(a)] = find(b)
    sizes = {}
    for a, *_ in edges:
        sizes[find(a)] = sizes.get(find(a), 0) + 1
    main = max(sizes, key=sizes.get)
    edges = [e for e in edges if find(e[0]) == main]
    ids = {}
    for a, b, *_ in edges:
        ids.setdefault(a, len(ids))
        ids.setdefault(b, len(ids))
    order = sorted(ids, key=ids.get)
    coords = np.array([nodes[i] for i in order])
    geom_start, geom = [0], []
    for e in edges:
        for lon, lat in e[7]:
            geom += [round(lon * 1e6), round(lat * 1e6)]
        geom_start.append(len(geom) // 2)
    return {
        "n": len(order), "m": len(edges),
        "nodes": b64(np.round(coords * 1e6).astype(np.int32)),
        "a": b64(np.array([ids[e[0]] for e in edges], np.uint32)),
        "b": b64(np.array([ids[e[1]] for e in edges], np.uint32)),
        "len": b64(np.array([e[2] for e in edges], np.float32)),
        "drive": b64(np.array([min(255, round(e[3])) for e in edges], np.uint8)),
        "flags": b64(np.array([e[4] for e in edges], np.uint8)),
        "walkF": b64(np.array([e[5] for e in edges], np.float32)),
        "walkB": b64(np.array([e[6] for e in edges], np.float32)),
        "geomStart": b64(np.array(geom_start, np.uint32)),
        "geom": b64(np.array(geom, np.int32)),
    }


def haversine(lon0, lat0, lon1, lat1):
    p0, p1 = math.radians(lat0), math.radians(lat1)
    a = math.sin((p1 - p0) / 2) ** 2 + math.cos(p0) * math.cos(p1) * math.sin(math.radians(lon1 - lon0) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(min(1, a)))


def simplify(pts, tol_m):
    """Ramer-Douglas-Peucker in local metres; keeps both ends."""
    if len(pts) < 3:
        return pts
    lat0 = math.radians(pts[0][1])
    xy = [((p[0]) * 111320 * math.cos(lat0), p[1] * 110540) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x0, y0), (x1, y1) = xy[i], xy[j]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1e-9
        best, k = 0, -1
        for m in range(i + 1, j):
            d = abs(dy * (xy[m][0] - x0) - dx * (xy[m][1] - y0)) / norm
            if d > best:
                best, k = d, m
        if best > tol_m:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, k in zip(pts, keep) if k]


def towers_near(west, south, east, north):
    out, offset = {}, 0
    while True:
        q = urllib.parse.urlencode({"where": "LicStatus='A'", "geometry": f"{west},{south},{east},{north}",
                                    "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                    "outFields": "Licensee,AllStruc,latdec,londec", "returnGeometry": "false",
                                    "resultOffset": offset, "resultRecordCount": 2000, "f": "json"})
        page = json.loads(fetch(f"{TOWERS}?{q}"))
        feats = page.get("features", [])
        for f in feats:
            a = f["attributes"]
            if a.get("latdec") is None or a.get("londec") is None:
                continue
            key = (round(a["latdec"], 4), round(a["londec"], 4))  # one structure, many licences
            h = float(a.get("AllStruc") or 30)
            if key not in out or h > out[key][2]:
                out[key] = (a["londec"], a["latdec"], h, a.get("Licensee") or "")
        if not page.get("exceededTransferLimit") or not feats:
            return list(out.values())
        offset += len(feats)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fire", type=int)
    ap.add_argument("--time", default=None)
    ap.add_argument("--server", default="http://localhost:8080")
    ap.add_argument("--out", default="earth/public/data/nav")
    args = ap.parse_args()
    raw = os.path.join("data", "raw", "nav", str(args.fire))
    os.makedirs(raw, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    started = time.time()

    q = f"?time={urllib.parse.quote(args.time)}" if args.time else ""
    detail = json.loads(fetch(f"{args.server}/api/fires/{args.fire}{q}"))
    fire = detail["fire"]
    w0, s0, e0, n0 = fire["bbox"]
    lat_c = (s0 + n0) / 2
    dlat_area, dlon_area = AREA_KM / 110.54, AREA_KM / (111.32 * math.cos(math.radians(lat_c)))
    west, south, east, north = w0 - dlon_area, s0 - dlat_area, e0 + dlon_area, n0 + dlat_area
    dlat = CELL_M / 110540
    dlon = CELL_M / (111320 * math.cos(math.radians(lat_c)))
    w, h = math.ceil((east - west) / dlon), math.ceil((north - south) / dlat)
    grid = {"west": west, "north": north, "dlon": dlon, "dlat": dlat, "w": w, "h": h}
    print(f"{fire['name'] or fire['id']}: area {west:.3f},{south:.3f},{east:.3f},{north:.3f}, grid {w}x{h} at {CELL_M} m")

    elev = terrain_grid(args.server, west, south, east, north, w, h, dlon, dlat)
    print(f"terrain: {elev.min():.0f}-{elev.max():.0f} m ({time.time() - started:.0f} s)")

    query = (f'[out:json][timeout:900];(way["highway"]({south},{west},{north},{east});'
             f'node["barrier"]({south},{west},{north},{east}););out body;>;out skel qt;')
    osm = json.loads(cached(os.path.join(raw, "osm.json"), lambda: fetch(OVERPASS, urllib.parse.urlencode({"data": query}).encode(), 1200)))
    graph = build_graph(osm, elev, grid)
    print(f"network: {graph['n']} junctions, {graph['m']} segments ({time.time() - started:.0f} s)")

    pad_lat, pad_lon = TOWER_RADIUS_KM / 110.54, TOWER_RADIUS_KM / (111.32 * math.cos(math.radians(lat_c)))
    towers = json.loads(cached(os.path.join(raw, "towers.json"), lambda: json.dumps(
        towers_near(west - pad_lon, south - pad_lat, east + pad_lon, north + pad_lat)).encode()))
    best = np.full(w * h, -128, np.int16)
    for i, (lon, lat, hgt, lic) in enumerate(towers):
        params = {"lat": lat, "lon": lon, "height": round(max(10, min(100, 0.9 * hgt)), 1), "freq": CELL["freq"], "erp": CELL["erp"],
                  "radius": TOWER_RADIUS_KM, **grid}
        res = json.loads(fetch(f"{args.server}/api/coverage?{urllib.parse.urlencode(params)}", timeout=900))
        best = np.maximum(best, np.frombuffer(base64.b64decode(res["dbm"]), np.int8).astype(np.int16))
        print(f"  tower {i + 1}/{len(towers)} {lic[:30]!r} h {hgt:.0f} m: {res.get('ms', 0) / 1000:.1f} s{' (cached)' if res.get('cached') else ''}")
    cell = np.where(best >= CELL["covered"], 2, np.where(best >= CELL["marginal"], 1, 0)).astype(np.uint8)
    print(f"cell coverage: covered {np.mean(cell == 2):.0%}, marginal {np.mean(cell == 1):.0%}, none {np.mean(cell == 0):.0%}")

    package = {
        "version": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fire": {"id": fire["id"], "name": fire["name"], "snapshot": detail["snapshot"], "time": detail["time"]},
        "area": [west, south, east, north],
        "grid": {**grid, "elev": b64(np.round(elev).astype(np.int16)), "cell": b64(cell)},
        "towers": [[round(t[0], 5), round(t[1], 5), round(t[2], 1), t[3]] for t in towers],
        "cell": CELL, "graph": graph,
        "sources": {"network": "OpenStreetMap contributors (ODbL), Overpass API",
                    "terrain": "AWS Terrain Tiles (terrarium)",
                    "towers": "FCC cellular tower sites via HIFLD Cellular Towers (structure height; power and pattern assumed)",
                    "coverage": "SPLAT! (ITWOM), receiver 1.5 m, 850 MHz, 400 W ERP assumed per site"},
    }
    out = os.path.join(args.out, f"{fire['id']}.json")
    with open(out, "w") as f:
        json.dump(package, f, separators=(",", ":"))
    print(f"wrote {out}: {os.path.getsize(out) / 1e6:.1f} MB in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
