#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely"]
# ///
"""Download NASA FEDS active fire perimeters (VIIRS-derived, via the VEDA features API) for earth to outline.

FEDS groups VIIRS detections into fires and outlines each one, twice a day. This takes the latest snapshot of active
fires in a region (or the one at TIME), simplifies the outlines, and writes them as GeoJSON. Each fire is named after
the NIFC WFIGS wildfire incident whose point of origin lies in or near it (the largest one, if several do) among
those burning at TIME; FEDS has only numeric ids. WFIGS's year-to-date list covers this year only, so an older TIME
gets no names. Fires without an incident are placed by county instead (FCC Census area API, no key).

The snapshot collection only reaches back a few weeks. For an older TIME this falls back to FEDS's large-fire
collection, which keeps every snapshot since March 2025 but only fires that grew past ~5 km².

Usage: feds_to_earth_json.py OUT.json [TIME]     TIME like 2026-10-29T12:00:00; default: the latest
       feds_to_earth_json.py --index FEDS_DIR      list the snapshots in FEDS_DIR/YYYY/MM/DD-HHMM.json, oldest
                                                   first, in FEDS_DIR/index.json, for earth's fire-area transitions
Output: {"type": "FeatureCollection", "time": TIME, "features": [{"type": "Feature", "geometry": ...,
         "properties": {"id", "area_km2", "perimeter_km", "mean_frp", "days", "new_pixels", "pixels",
                        "place" (county, state), and if named: "name", "state", "acres", "contained"}}]}
"""
import glob
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

FEATURES = "https://openveda.cloud/api/features/collections/"
SNAPSHOTS = FEATURES + "public.eis_fire_snapshot_perimeter_nrt/items"   # every fire, the last few weeks
LARGE_FIRES = FEATURES + "public.eis_fire_lf_perimeter_nrt/items"       # large fires, since March 2025
REGION = "CONUS"
INCIDENTS = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
             "WFIGS_Incident_Locations_YearToDate/FeatureServer/0/query")
INCIDENT_PAGE = 2000
COUNTIES = "https://geo.fcc.gov/api/census/area"
NAME_DISTANCE = 0.05  # degrees (~5 km): an origin this close to a perimeter names it, as fires grow away from it
TOLERANCE = 0.001  # degrees (~100 m) of outline simplification; VIIRS pixels are 375 m
DIGITS = 4         # coordinate decimals (~10 m)


def get(params, collection=SNAPSHOTS):
    url = collection + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=300) as response:
        return json.load(response)


def incidents(time):
    """NIFC wildfire incidents found by TIME and not declared out before it, as [(origin point, properties)],
    largest first. Containment is as of now, not TIME."""
    stamp = time.replace("T", " ")[:19]
    where = ("IncidentTypeCategory='WF' AND IncidentSize >= 10 AND FireDiscoveryDateTime <= TIMESTAMP '%s' AND "
             "(FireOutDateTime IS NULL OR FireOutDateTime >= TIMESTAMP '%s')" % (stamp, stamp))
    found = []
    while True:
        params = {"where": where, "f": "geojson", "orderByFields": "IncidentSize DESC", "resultOffset": len(found),
                  "outFields": "IncidentName,IncidentSize,POOState,PercentContained",
                  "resultRecordCount": INCIDENT_PAGE}
        with urllib.request.urlopen(INCIDENTS + "?" + urllib.parse.urlencode(params), timeout=300) as response:
            page = json.load(response)["features"]
        found += page
        if len(page) < INCIDENT_PAGE:
            break
    return [(shape(f["geometry"]), f["properties"]) for f in found if f["geometry"]]


def incident_name(name):
    """"Crosswhite" for "0445 CROSSWHITE": title case, without the leading number some dispatch centers add."""
    words = name.strip().split()
    if len(words) > 1 and words[0].isdigit():
        words = words[1:]
    return " ".join(words).title()


def name_of(geometry, named):
    """Properties naming the fire: the largest incident whose origin is within NAME_DISTANCE of it, or {}."""
    for origin, p in named:
        if geometry.distance(origin) <= NAME_DISTANCE and p["IncidentName"]:
            result = {"name": incident_name(p["IncidentName"]), "state": (p["POOState"] or "").replace("US-", "")}
            if p["IncidentSize"] is not None:
                result["acres"] = round(p["IncidentSize"])
            if p["PercentContained"] is not None:
                result["contained"] = round(p["PercentContained"])
            return result
    return {}


def county(geometry):
    """"Butler Co., OH" for the county at the fire's center, or "" if the lookup fails (e.g. offshore)."""
    point = geometry.representative_point()
    url = COUNTIES + "?" + urllib.parse.urlencode({"lat": point.y, "lon": point.x, "format": "json"})
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            result = json.load(response)["results"][0]
    except (OSError, ValueError, IndexError, KeyError):
        log.warning("no county for %.3f, %.3f", point.x, point.y)
        return ""
    name = result["county_name"].replace(" County", " Co.").replace(" Parish", " Par.")
    return name + ", " + result["state_code"]


def clockwise(geometry):
    """d3 (v3) reads a ring going the other way as the rest of the globe, so outer rings must run clockwise."""
    if isinstance(geometry, Polygon):
        return orient(geometry, sign=-1.0)
    return MultiPolygon([orient(polygon, sign=-1.0) for polygon in geometry.geoms])


def rounded(coords):
    if isinstance(coords[0], (int, float)):
        return [round(c, DIGITS) for c in coords]
    return [rounded(c) for c in coords]


def main(dst, time=None):
    if time is None:
        time = get({"region": REGION, "sortby": "-t", "limit": 1})["features"][0]["properties"]["t"]
    result, source = get({"region": REGION, "t": time, "limit": 10000}), "NASA FEDS (VIIRS), " + REGION
    if not result["features"]:
        result, source = get({"region": REGION, "t": time, "limit": 10000}, LARGE_FIRES), \
            "NASA FEDS large fires (VIIRS), " + REGION
    named = incidents(time)
    shapes = [(f["properties"], shape(f["geometry"]).simplify(TOLERANCE, preserve_topology=True))
              for f in result["features"]]
    shapes = [(p, geometry) for p, geometry in shapes if not geometry.is_empty]
    with ThreadPoolExecutor(8) as pool:
        places = list(pool.map(county, [geometry for _, geometry in shapes]))
    features, vertices, names = [], 0, 0
    for (p, geometry), place in zip(shapes, places):
        name = name_of(geometry, named)
        names += bool(name)
        geometry = mapping(clockwise(geometry))
        geometry = {"type": geometry["type"], "coordinates": rounded(geometry["coordinates"])}
        vertices += json.dumps(geometry["coordinates"]).count("[") - 1
        features.append({"type": "Feature", "geometry": geometry, "properties": {
            "id": int(p["fireid"]),
            "area_km2": round(p["farea"], 2),
            "perimeter_km": round(p["fperim"], 2),
            "mean_frp": None if p["meanfrp"] is None else round(p["meanfrp"], 2),
            "days": round(p["duration"], 1),
            "new_pixels": p["n_newpixels"],
            "pixels": p["n_pixels"],
            "place": place,
            **name,
        }})
    with open(dst, "w") as out:
        json.dump({"type": "FeatureCollection", "time": time, "source": source,
                   "features": features}, out, separators=(",", ":"))
    log.info("wrote %s: %s, %d fires at %s (%d named from %d NIFC incidents burning then), ~%d coordinate arrays",
             dst, source, len(features), time, names, len(named), vertices)


def index(root):
    """Writes ROOT/index.json: {"snapshots": [TIME, ...]}, oldest first, from the snapshot files' own times. Earth
    finds a snapshot's file from its time (YYYY/MM/DD-HHMM.json); a day's DD.json is the one it loads for that day."""
    times = []
    for path in glob.glob(os.path.join(root, "[0-9]" * 4, "[0-9]" * 2, "[0-9]" * 2 + "-" + "[0-9]" * 4 + ".json")):
        with open(path) as f:
            time = json.load(f)["time"]
        expected = time[:4] + "/" + time[5:7] + "/" + time[8:10] + "-" + time[11:13] + time[14:16] + ".json"
        if not path.replace(os.sep, "/").endswith(expected):
            log.warning("skipping %s: its snapshot time is %s", path, time)
            continue
        times.append(time)
    times.sort()
    with open(os.path.join(root, "index.json"), "w") as out:
        json.dump({"snapshots": times}, out, indent=1)
    log.info("wrote %s: %d snapshots, %s to %s", os.path.join(root, "index.json"), len(times),
             times[0] if times else "-", times[-1] if times else "-")


if __name__ == "__main__":
    if sys.argv[1] == "--index":
        index(sys.argv[2])
    else:
        main(*sys.argv[1:3])
