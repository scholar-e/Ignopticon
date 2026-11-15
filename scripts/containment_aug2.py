#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely"]
# ///
"""Containment status of FEDS fires at an issue time, from NIFC WFIGS incident DATES (evaluation/display only).

Usage: containment_aug2.py FIRES.txt FEDS_DIR ISSUE OUT.json     ISSUE like 2026-08-02T12:00
Matching reuses feds_to_earth_json.py: an incident whose origin is within 0.05 deg (~5 km) of the FEDS outline
(largest incident wins); the match distance is recorded. Status: contained if any of ContainmentDateTime,
ControlDateTime, FireOutDateTime <= ISSUE; uncontained if matched without such a date; unmatched otherwise.
PercentContained is the LATEST value, not as of ISSUE."""
import datetime as dt, glob, json, logging, os, socket, sys, urllib.parse, urllib.request
from shapely.geometry import shape

_g = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *a, **k: _g(host, port, socket.AF_INET, *a, **k)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)
URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
       "WFIGS_Incident_Locations_YearToDate/FeatureServer/0/query")
DATES = ["FireDiscoveryDateTime", "ContainmentDateTime", "ControlDateTime", "FireOutDateTime"]
NAME_DISTANCE = 0.05


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def incidents(issue):
    where = "IncidentTypeCategory='WF' AND FireDiscoveryDateTime <= TIMESTAMP '%s'" % issue.replace("T", " ")[:19]
    found = []
    while True:
        p = {"where": where, "f": "geojson", "orderByFields": "IncidentSize DESC", "resultOffset": len(found),
             "outFields": "*", "resultRecordCount": 2000}
        with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(p), timeout=300) as r:
            page = json.load(r)["features"]
        found += page
        if len(page) < 2000:
            break
    return [(shape(f["geometry"]), f["properties"]) for f in found if f["geometry"]]


def main(fires_txt, feds_dir, issue, out):
    ids = [l.strip().split("-")[-1] for l in open(fires_txt) if l.strip()]
    geoms = {}
    last = issue[8:10] + "-" + issue[11:13] + issue[14:16] + ".json"
    for path in sorted(glob.glob(os.path.join(feds_dir, "[0-9][0-9]-[0-9][0-9][0-9][0-9].json"))):
        if os.path.basename(path) > last:       # snapshots up to the issue only; later ones overwrite earlier
            continue
        try:
            d = json.load(open(path))
        except ValueError:
            continue
        for f in d.get("features", []):
            geoms[str(f["properties"]["id"])] = shape(f["geometry"])
    inc = incidents(issue)
    log.info("%d WFIGS incidents discovered by %s", len(inc), issue)
    res = {}
    for i in ids:
        g = geoms.get(i)
        if g is None:
            res[i] = {"status": "unmatched", "reason": "no FEDS geometry"}
            continue
        c = [(g.distance(o), p) for o, p in inc if g.distance(o) <= NAME_DISTANCE and p.get("IncidentName")]
        if not c:
            res[i] = {"status": "unmatched", "reason": "no WFIGS incident within 0.05 deg"}
            continue
        d, p = c[0] if len(c) == 1 else max(c, key=lambda x: x[1].get("IncidentSize") or 0)
        t = {k: iso(p.get(k)) for k in DATES}
        done = sorted(x for x in (t["ContainmentDateTime"], t["ControlDateTime"], t["FireOutDateTime"]) if x)
        cutoff = issue + "Z"
        since = done[0] if done and done[0] <= cutoff else None
        res[i] = {"status": "contained" if since else "uncontained", "contained_since": since,
                  "incident": p["IncidentName"].strip(), "incident_id": p.get("IrwinID") or p.get("GlobalID"),
                  "state": (p.get("POOState") or "").replace("US-", ""),
                  "discovery": t["FireDiscoveryDateTime"], "containment": t["ContainmentDateTime"],
                  "control": t["ControlDateTime"], "out": t["FireOutDateTime"],
                  "percent_contained_latest": p.get("PercentContained"),
                  "percent_contained_note": "latest, not as of issue", "size_acres": p.get("IncidentSize"),
                  "match": "origin within %.4f deg of FEDS outline, largest of %d candidate(s)" % (d, len(c)),
                  "match_distance_deg": round(d, 4), "n_candidates": len(c)}
    json.dump({"issue": issue + "Z", "source": "NIFC WFIGS Incident Locations YearToDate",
               "status_rule": "contained if containment/control/out date <= issue; dates only",
               "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "fires": res},
              open(out, "w"), indent=1)
    from collections import Counter
    log.info("%s", Counter(r["status"] for r in res.values()))


if __name__ == "__main__":
    main(*sys.argv[1:5])
