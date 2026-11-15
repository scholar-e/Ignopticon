#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pyproj", "rasterio", "scipy", "shapely>=2.1"]
# ///
"""Writes an ELMFIRE case directory without the GDAL command-line tools.

ELMFIRE reads its rasters as ENVI band-sequential files (NAME.bsq + NAME.hdr) in its SCRATCH directory and makes
them from NAME.tif with gdal_translate when they are missing. This writes the .bsq/.hdr pairs directly through
rasterio's bundled GDAL (the same ENVI driver gdal_translate uses), so ELMFIRE never calls gdal_translate. Outputs
stay as .bil (CONVERT_TO_GEOTIFF = .FALSE.) and are read with rasterio; elmfire_outputs_to_tif() converts them.

Layout of a case:
    CASE/elmfire.data     the namelists
    CASE/scratch/         landscape, adj, phi, ws, wd, m1, m10, m100 (.bsq/.hdr)
    CASE/outputs/         ELMFIRE's outputs

ELMFIRE also runs gdalinfo and gdalsrsinfo on scratch/landscape.tif to read the grid and CRS; without GDAL installed,
put scripts/gdal_shim on the PATH (rasterio stand-ins for just those calls).

Rasters (all on one grid, a projected CRS in metres, north up):
    landscape  Int16, 8 bands: DEM (m), slope (deg), aspect (deg), FBFM40 code, canopy cover (%),
               canopy height (10*m), canopy base height (10*m), canopy bulk density (100*kg/m3)
    ws, wd     Float32, one band per weather time step DT_METEOROLOGY apart: 20-ft wind speed (mph), direction (deg,
               from)
    m1,m10,m100 Float32, bands as ws: dead fuel moisture (%)
    adj        Float32: spread rate multiplier
    phi        Float32: initial level set, negative where already burned, positive elsewhere

Usage: elmfire_case.py tutorial CASE    rebuilds ELMFIRE's tutorials/01-constant-wind case (a check of this script)
       elmfire_case.py fire LANDSCAPE.tif WXDIR STATE.tif ISSUE HOURS CASE [KEY=VALUE ...]
           a forecast from an observed fire state: LANDSCAPE.tif from landscape_download.py, WXDIR from
           hrrr_weather.py (its first band at ISSUE), STATE.tif from fire_observations.py (cells burned by ISSUE
           start burned). KEY=VALUE are namelist overrides, GROUP:KEY=VALUE or KEY (looked up in the groups
           written), e.g. LH_MOISTURE_CONTENT=60 ADJ=1.2 (ADJ scales the spread rate everywhere).
           OBSERVED=OBS.json (fire_observations.py) starts the fire from its hotspots instead: cells seen hot in the
           ACTIVE_HOURS (default 6) before ISSUE burn, the rest of the area burned by ISSUE is non-burnable; the
           burned area is then rebuilt from what was known at ISSUE (hot cells, latest FEDS perimeter).
           WSMULT=x scales wind speed, MOIST=p adds p percentage points to dead fuel moisture, WXSTART=k starts at
           the weather's band k (for an ISSUE k hours after the weather run). ACTIVE_BUFFER=n (default 2): burned
           cells within n state cells of a hot cell keep their fuel; only the rest become non-burnable.
           ADJ_GR=, ADJ_SH=, ADJ_TIMBER= set the spread multiplier of grass + grass-shrub, shrub and timber fuels
           (other cells keep ADJ); ADJ_WIND_EXP=b then multiplies it by (the cell's mean 20-ft wind over the run /
           ADJ_WIND_REF, default 10 mph) ** b, clipped to x0.1-x10.
           MIN_START_CELLS=n (OBSERVED only, default 0 = off): if fewer than n burnable fuel cells start burning,
           grow the hot cells by one state (375 m VIIRS) cell, up to twice, so tiny or non-burnable starts can spread.
       elmfire_case.py outputs CASE [NAME]
           after ELMFIRE has run in CASE: its .bil outputs to GeoTIFF (with the case's CRS, which ELMFIRE leaves out)
           and CASE/outputs/run.json naming the run, for heat_frames.py CASE/outputs.
"""
import glob
import logging
import os
import sys

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

NODATA = -9999
LANDSCAPE_BANDS = ["dem", "slp", "asp", "fbfm40", "cc", "ch", "cbh", "cbd"]


def write_bsq(path_stub, bands, transform, crs, dtype):
    """Writes PATH_STUB.bsq/.hdr (ENVI, band sequential) from a list of 2-D arrays or a 3-D array."""
    data = np.asarray(bands, dtype=dtype)
    if data.ndim == 2:
        data = data[None]
    with rasterio.open(path_stub + ".bsq", "w", driver="ENVI", INTERLEAVE="BSQ", width=data.shape[2],
                       height=data.shape[1], count=data.shape[0], dtype=dtype, crs=crs, transform=transform,
                       nodata=NODATA) as dst:
        dst.write(data)
    # GDAL may leave an .aux.xml beside it; ELMFIRE only needs the .hdr unless USE_BSQ_XML_HEADER is set.


def namelist_value(value):
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, str):
        return "'" + value + "'"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def write_namelists(path, groups):
    """groups: {"INPUTS": {"KEY": value, ...}, ...}; keys like "X_IGN(1)" pass through."""
    with open(path, "w") as f:
        for group, entries in groups.items():
            f.write("&" + group + "\n")
            for key, value in entries.items():
                f.write("%-30s = %s\n" % (key, namelist_value(value)))
            f.write("/\n\n")


def write_case(case, transform, crs, landscape, weather, adj, phi, dt_met, tstop, extra=None):
    """Writes an ELMFIRE case. landscape: dict of the 8 LANDSCAPE_BANDS arrays; weather: dict of ws, wd, m1, m10,
    m100, each a list of 2-D arrays (one per DT_METEOROLOGY step) or one 2-D array; extra: namelist overrides,
    {"GROUP": {"KEY": value}}."""
    scratch = os.path.join(case, "scratch")
    os.makedirs(scratch, exist_ok=True)
    os.makedirs(os.path.join(case, "outputs"), exist_ok=True)
    bands = np.asarray([landscape[b] for b in LANDSCAPE_BANDS], dtype="int16")
    write_bsq(os.path.join(scratch, "landscape"), bands, transform, crs, "int16")
    # ELMFIRE reads the grid and CRS from landscape.tif with gdalinfo/gdalsrsinfo (scripts/gdal_shim stands in).
    with rasterio.open(os.path.join(scratch, "landscape.tif"), "w", driver="GTiff", width=bands.shape[2],
                       height=bands.shape[1], count=8, dtype="int16", crs=crs, transform=transform, nodata=NODATA,
                       compress="deflate") as dst:
        dst.write(bands)
    for name, bands in weather.items():
        write_bsq(os.path.join(scratch, name), bands, transform, crs, "float32")
    write_bsq(os.path.join(scratch, "adj"), adj, transform, crs, "float32")
    write_bsq(os.path.join(scratch, "phi"), phi, transform, crs, "float32")
    groups = {
        "INPUTS": {
            "FUELS_AND_TOPOGRAPHY_DIRECTORY": "./scratch", "LANDSCAPE_FILENAME": "landscape",
            "ADJ_FILENAME": "adj", "PHI_FILENAME": "phi", "DT_METEOROLOGY": float(dt_met),
            "WEATHER_DIRECTORY": "./scratch", "WS_FILENAME": "ws", "WD_FILENAME": "wd", "M1_FILENAME": "m1",
            "M10_FILENAME": "m10", "M100_FILENAME": "m100", "LH_MOISTURE_CONTENT": 30.0,
            "LW_MOISTURE_CONTENT": 60.0,
        },
        "OUTPUTS": {
            "OUTPUTS_DIRECTORY": "./outputs", "DTDUMP": 3600.0, "DUMP_FLIN": True, "DUMP_SPREAD_RATE": True,
            "DUMP_TIME_OF_ARRIVAL": True, "CONVERT_TO_GEOTIFF": False,
        },
        "TIME_CONTROL": {"SIMULATION_DT": 30.0, "SIMULATION_TSTOP": float(tstop)},
        "SIMULATOR": {"WX_BILINEAR_INTERPOLATION": True, "WSMFEFF_LOW_MULT": 0.011364},
        "MISCELLANEOUS": {"PATH_TO_GDAL": "auto", "SCRATCH": "./scratch"},
    }
    for group, entries in (extra or {}).items():
        groups.setdefault(group, {}).update(entries)
    write_namelists(os.path.join(case, "elmfire.data"), groups)
    log.info("wrote %s (%d x %d cells)", case, phi.shape[1], phi.shape[0])


def elmfire_outputs_to_tif(outdir, crs):
    """Converts ELMFIRE's .bil outputs in OUTDIR to GeoTIFF (the names heat_frames.py reads)."""
    for bil in sorted(glob.glob(os.path.join(outdir, "*.bil"))):
        with rasterio.open(bil) as src:
            profile = src.profile | {"driver": "GTiff", "compress": "deflate", "crs": src.crs or crs}
            for key in ("blockxsize", "blockysize", "tiled", "interleave"):
                profile.pop(key, None)
            data = src.read()
        with rasterio.open(bil[:-4] + ".tif", "w", **profile) as dst:
            dst.write(data)


def tutorial(case):
    """ELMFIRE tutorials/01-constant-wind: 12 km square of 30 m cells, fuel model 102, 15 mph wind from the north,
    ignition at (0, 3000), 5.5 h."""
    cell, size = 30.0, 12000.0
    n = int(size / cell)
    transform = from_origin(-size / 2, size / 2, cell, cell)
    crs = CRS.from_epsg(32610)
    full = lambda v: np.full((n, n), v)
    landscape = {b: full(0) for b in LANDSCAPE_BANDS} | {"fbfm40": full(102)}
    weather = {"ws": full(15.0), "wd": full(0.0), "m1": full(3.0), "m10": full(4.0), "m100": full(5.0)}
    write_case(case, transform, crs, landscape, weather, full(1.0), full(1.0), 3600.0, 19800.0, extra={
        "SIMULATOR": {"NUM_IGNITIONS": 1, "X_IGN(1)": 0.0, "Y_IGN(1)": 3000.0, "T_IGN(1)": 0.0}})


def parse_value(text):
    if text.upper() in (".TRUE.", "TRUE"):
        return True
    if text.upper() in (".FALSE.", "FALSE"):
        return False
    try:
        return int(text) if text.lstrip("-").isdigit() else float(text)
    except ValueError:
        return text


# Fuel groups for per-group spread multipliers (FBFM40 codes): grass and grass-shrub, shrub, timber (understory,
# litter, slash-blowdown).
ADJ_GROUPS = {"ADJ_GR": list(range(101, 110)) + list(range(121, 125)), "ADJ_SH": list(range(141, 150)),
              "ADJ_TIMBER": list(range(161, 166)) + list(range(181, 190)) + list(range(201, 205))}


def fire(landscape_tif, wxdir, state_tif, issue, hours, case, *overrides):
    import datetime
    with rasterio.open(landscape_tif) as src:
        crs, transform, bands = src.crs, src.transform, src.read()
    landscape = dict(zip(LANDSCAPE_BANDS, bands))
    weather = {}
    for name in ("ws", "wd", "m1", "m10", "m100"):
        with rasterio.open(os.path.join(wxdir, name + ".tif")) as src:
            weather[name] = src.read()
            wx_transform = src.transform
    with rasterio.open(state_tif) as src:
        state, nodata, st = src.read(1), src.nodata, src.transform
        reference = datetime.datetime.fromisoformat(src.tags()["reference"].replace("Z", ""))
    issue_s = (datetime.datetime.fromisoformat(issue) - reference).total_seconds()
    burned = (state != nodata) & (state <= issue_s)
    refine = int(round(st.a / transform.a))
    burned_fine = np.kron(burned, np.ones((refine, refine), dtype=bool))[:bands.shape[1], :bands.shape[2]]
    adj_value, observed, active_hours, extra = 1.0, None, 6.0, {"SIMULATOR": {"NUM_IGNITIONS": 0}}
    ws_mult, moist_add, wx_start, active_buffer = 1.0, 0.0, 0, 2
    adj_group, adj_wind_exp, adj_wind_ref = {}, 0.0, 10.0
    min_start, min_start_info = 0, None
    for item in overrides:
        key, value = item.split("=", 1)
        if key.upper() == "ADJ":
            adj_value = float(value)
            continue
        if key.upper() in ADJ_GROUPS:
            adj_group[key.upper()] = float(value)
            continue
        if key.upper() == "ADJ_WIND_EXP":
            adj_wind_exp = float(value)
            continue
        if key.upper() == "ADJ_WIND_REF":
            adj_wind_ref = float(value)
            continue
        if key.upper() == "MIN_START_CELLS":
            min_start = int(value)
            continue
        if key.upper() == "WSMULT":
            ws_mult = float(value)
            continue
        if key.upper() == "MOIST":
            moist_add = float(value)
            continue
        if key.upper() == "ACTIVE_BUFFER":
            active_buffer = int(value)
            continue
        if key.upper() == "WXSTART":
            wx_start = int(value)
            continue
        if key.upper() == "OBSERVED":
            observed = value
            continue
        if key.upper() == "ACTIVE_HOURS":
            active_hours = float(value)
            continue
        group, _, key = key.rpartition(":")
        extra.setdefault(group or "?", {})[key] = parse_value(value)
    hot = np.zeros_like(burned)
    if observed:
        # Burning edges from hotspots: cells seen hot in the ACTIVE_HOURS before issue start burning. The rest of
        # the area burned by issue becomes non-burnable (fuel model 99), so edges that had gone quiet don't spread.
        from pyproj import Transformer
        import json
        from rasterio.features import rasterize
        from shapely.geometry import mapping, shape
        import shapely
        to_grid = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        obs = json.load(open(observed))
        offset = (datetime.datetime.fromisoformat(obs["reference"].replace("Z", "")) - reference).total_seconds()
        # The area burned by issue from what was known then only: every cell seen hot by issue, plus the latest
        # FEDS perimeter at or before issue. (The state raster's guessed arrivals use later snapshots.)
        burned = np.zeros_like(burned)
        for f in obs["frames"]:
            t = f["seconds"] + offset
            if t > issue_s:
                continue
            for lon, lat, heat, source in f["cells"]:
                x, y = to_grid.transform(lon, lat)
                col, row = int((x - st.c) / st.a), int((y - st.f) / st.e)
                if 0 <= row < hot.shape[0] and 0 <= col < hot.shape[1]:
                    burned[row, col] = True
                    if t >= issue_s - active_hours * 3600:
                        hot[row, col] = True
        known = [p for p in obs["perimeters"]
                 if (datetime.datetime.fromisoformat(p["time"].replace("Z", "")) - reference).total_seconds() <= issue_s]
        if known:
            geom = shapely.transform(shape(known[-1]["geometry"]),
                                     lambda xy: np.column_stack(to_grid.transform(xy[:, 0], xy[:, 1])))
            burned |= rasterize([(mapping(geom), 1)], out_shape=burned.shape, transform=st, dtype="uint8").astype(bool)
            log.info("latest FEDS perimeter known at issue: %s", known[-1]["time"])
        burned_fine = np.kron(burned, np.ones((refine, refine), dtype=bool))[:bands.shape[1], :bands.shape[2]]
        from scipy.ndimage import binary_dilation
        fine = lambda a: np.kron(a, np.ones((refine, refine), dtype=bool))[:bands.shape[1], :bands.shape[2]]
        burnable = lambda h: int((fine(h) & ~np.isin(landscape["fbfm40"], list(range(91, 100)))).sum())
        if min_start > 0:
            # Minimum initial footprint: a few detections over non-burnable cells (roads, fields, water) cannot
            # spread; grow them by one 375 m VIIRS cell at a time (at most two) until n burnable cells burn.
            before, dilations = burnable(hot), 0
            while burnable(hot) < min_start and dilations < 2 and hot.any():
                hot = binary_dilation(hot)
                dilations += 1
            min_start_info = {"threshold_cells": min_start, "burnable_start_cells_before": before,
                              "burnable_start_cells_after": burnable(hot), "dilations_375m": dilations}
            log.info("minimum initial footprint: %s", min_start_info)
        hot_fine = fine(hot)
        phi = np.where(hot_fine, -1.0, 1.0)
        # Burned cells far from recent heat are burned out (non-burnable). Cells within ACTIVE_BUFFER state cells of
        # a hot cell keep their fuel: FEDS perimeters are drawn around the detection footprints, so the zone the
        # fire is consuming lies inside them.
        near_hot = binary_dilation(hot, iterations=active_buffer) if active_buffer > 0 else hot
        out = burned & ~near_hot
        out_fine = np.kron(out, np.ones((refine, refine), dtype=bool))[:bands.shape[1], :bands.shape[2]]
        landscape["fbfm40"] = np.where(out_fine, 99, landscape["fbfm40"]).astype("int16")
        log.info("%d cells hot in the %g h before issue start burning; %d burned cells more than %d cells from them "
                 "made non-burnable", hot.sum(), active_hours, out.sum(), active_buffer)
    else:
        phi = np.where(burned_fine, -1.0, 1.0)
    adj = np.full(phi.shape, adj_value)
    # Per-fuel-group multipliers (absolute: ADJ_GR=0.03 sets grass and grass-shrub cells to 0.03; groups not given
    # keep ADJ), by the cell's FBFM40 code.
    for name, value in adj_group.items():
        adj[np.isin(landscape["fbfm40"], ADJ_GROUPS[name])] = value
    # Weather adjustments: start at band WXSTART (hours after the weather's first band), scale the wind speed,
    # add MOIST (percentage points) to the dead fuel moisture.
    weather = {k: v[wx_start:] for k, v in weather.items()}
    weather["ws"] = weather["ws"] * ws_mult
    if adj_wind_exp:
        # Wind term: ADJ x (cell's mean 20-ft wind over the run / ADJ_WIND_REF mph) ** ADJ_WIND_EXP, factor clipped
        # to [0.1, 10]. ELMFIRE's adj raster is static, so the run's mean wind stands for the cell's wind.
        ws_mean = weather["ws"][: int(float(hours)) + 1].mean(0)
        k = int(round(wx_transform.a / transform.a))
        ws_fine = np.kron(ws_mean, np.ones((k, k)))[:phi.shape[0], :phi.shape[1]]
        factor = np.clip((np.maximum(ws_fine, 0.1) / adj_wind_ref) ** adj_wind_exp, 0.1, 10.0)
        adj = adj * factor
        log.info("ADJ wind term: exponent %g, factor %.2f-%.2f", adj_wind_exp, factor.min(), factor.max())
    for k in ("m1", "m10", "m100"):
        weather[k] = weather[k] + moist_add
    # Keys given without a group go to the group write_case already uses them in, else SIMULATOR.
    loose = extra.pop("?", {})
    hours = float(hours)
    write_case(case, transform, crs, landscape, {}, adj, phi, 3600.0, hours * 3600, extra=extra)
    # Weather on its own (coarser) grid; ELMFIRE interpolates it onto the fuel grid.
    for name, data in weather.items():
        write_bsq(os.path.join(case, "scratch", name), data, wx_transform, crs, "float32")
    if loose:
        path = os.path.join(case, "elmfire.data")
        text = open(path).read()
        for key, value in loose.items():
            line = "%-30s = %s\n" % (key, namelist_value(value))
            lines = [l for l in text.splitlines(True) if l.split("=")[0].strip() == key]
            text = text.replace(lines[0], line) if lines else text.replace("&SIMULATOR\n", "&SIMULATOR\n" + line)
        open(path, "w").write(text)
    with open(os.path.join(case, "case.json"), "w") as f:
        import json
        json.dump({"landscape": landscape_tif, "weather": wxdir, "state": state_tif, "issue": issue, "hours": hours,
                   "burned_at_issue_cells_375m": int(burned.sum()), "hot_cells_375m": int(hot.sum()),
                   "overrides": list(overrides), "min_start": min_start_info}, f, indent=1)
    log.info("%d state cells burned at %s; weather %d bands", burned.sum(), issue, weather["ws"].shape[0])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "tutorial":
        tutorial(sys.argv[2])
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "outputs":
        import json
        case = sys.argv[2]
        with rasterio.open(os.path.join(case, "scratch", "landscape.tif")) as src:
            case_crs = src.crs
        elmfire_outputs_to_tif(os.path.join(case, "outputs"), case_crs)
        info = json.load(open(os.path.join(case, "case.json"))) if os.path.exists(os.path.join(case, "case.json")) else {}
        name = sys.argv[3] if len(sys.argv) == 4 else "ELMFIRE " + (" ".join(info.get("overrides", [])) or "base")
        with open(os.path.join(case, "outputs", "run.json"), "w") as f:
            json.dump({"model": name, "engine": "elmfire"} | info, f, indent=1)
    elif len(sys.argv) >= 8 and sys.argv[1] == "fire":
        fire(*sys.argv[2:])
    else:
        sys.exit(__doc__)
