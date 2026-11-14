#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Save the colors LANDFIRE's FBFM40 image service uses for each fuel model, for earth's fuel legend.

Reads the service's legend swatches and keeps the color at the center of each.

Usage: landfire_legend.py OUT.json
Output: {"GR1": [r, g, b], ..., "NB9": [r, g, b]}
"""
import base64
import io
import json
import logging
import sys
import urllib.request

from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LEGEND = ("https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_FBFM40_CONUS/ImageServer"
          "/legend?f=json")


def main(dst):
    with urllib.request.urlopen(LEGEND, timeout=60) as response:
        legend = json.load(response)
    colors = {}
    for entry in legend["layers"][0]["legend"]:
        label = entry["label"]
        if label.startswith("Fill"):
            continue
        swatch = Image.open(io.BytesIO(base64.b64decode(entry["imageData"]))).convert("RGB")
        colors[label] = list(swatch.getpixel((swatch.width // 2, swatch.height // 2)))
    with open(dst, "w") as f:
        json.dump(colors, f, separators=(",", ":"))
    log.info("wrote %s: %d fuel models", dst, len(colors))


if __name__ == "__main__":
    main(*sys.argv[1:])
