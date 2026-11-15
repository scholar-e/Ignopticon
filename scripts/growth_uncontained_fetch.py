#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow"]
# ///
"""Fetch WFIGS incident locations (wildfires discovered 2021-01-01..2024-12-31) with their containment/control/out dates.
Output data/growth_uncontained/wfigs_2021_2024.csv (lat, lon, size, dates as UTC). Used by growth_uncontained.py (test labels)."""
import json, logging, sys, urllib.parse, urllib.request
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)
URL = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations/FeatureServer/0/query"
FIELDS = "IncidentName,IncidentTypeCategory,IncidentSize,FinalAcres,FireDiscoveryDateTime,ContainmentDateTime,ControlDateTime,FireOutDateTime,POOState"
rows = []
while True:
    p = {"where": "IncidentTypeCategory='WF' AND FireDiscoveryDateTime >= TIMESTAMP '2020-12-01 00:00:00' AND FireDiscoveryDateTime < TIMESTAMP '2025-01-01 00:00:00'",
         "f": "geojson", "outFields": FIELDS, "orderByFields": "OBJECTID", "resultOffset": len(rows), "resultRecordCount": 2000}
    with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(p), timeout=300) as r:
        page = json.load(r)["features"]
    for f in page:
        g = f.get("geometry") or {}
        c = g.get("coordinates") or [None, None]
        rows.append({**f["properties"], "lon": c[0], "lat": c[1]})
    log.info("%d rows", len(rows))
    if len(page) < 2000:
        break
df = pd.DataFrame(rows)
for c in ["FireDiscoveryDateTime", "ContainmentDateTime", "ControlDateTime", "FireOutDateTime"]:
    df[c] = pd.to_datetime(df[c], unit="ms", utc=True)
df.to_csv("data/growth_uncontained/wfigs_2021_2024.csv", index=False)
log.info("saved %d; with containment/control/out date: %d", len(df), int(df[["ContainmentDateTime", "ControlDateTime", "FireOutDateTime"]].notna().any(axis=1).sum()))
