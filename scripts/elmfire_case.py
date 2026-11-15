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
           ADJ_FILE=path: a one-band raster on the landscape grid (same shape) that multiplies the spread multiplier
           after ADJ, ADJ_GR/SH/TIMBER and the wind term (e.g. selfcal_adj.py's correction map); default none.
           MIN_START_CELLS=n (OBSERVED only, default 0 = off): if fewer than n burnable fuel cells start burning,
           grow the hot cells by one state (375 m VIIRS) cell, up to twice, so tiny or non-burnable starts can spread.
           INIT=feds (OBSERVED only; default INIT=hotcells, the above): start from the latest FEDS perimeter known at
           ISSUE, rasterised on the fuel grid (not 375 m blocks). Its interior is non-burnable; the burning front is
           the inner edge band EDGE_CELLS (default 2) fuel cells wide, where it lies within one state cell of a cell
           seen hot in the ACTIVE_HOURS before ISSUE ("detections"), or the whole band if no recent detection is
           near the edge ("whole_edge"). Recent hot cells farther outside the perimeter (growth after it was drawn)
           also burn, as 375 m cells. Without a FEDS perimeter at ISSUE it falls back to hotcells. case.json "init".
           Front rules for INIT=feds (default unchanged: detections within one state cell, else whole edge):
           FRONT_KM=D: an edge cell burns if a cell hot in the ACTIVE_HOURS before ISSUE lies within D km of it (distance
           on the fuel grid from the hot 375 m squares); FRONT_RFILE=path FRONT_R=R: edge cells where that raster (on
           the landscape grid, e.g. selfcal_adj.py's adj_selfcal.tif) is >= R also burn (union with detections);
           FRONT_PFILE=path FRONT_PTHRESH=P: opt-in aligned probability raster; nodata retains baseline
           selection, covered cells burn above P or with recent hotspot support. No spread multiplier changes.
           FRONT_PMODE=weighted FRONT_PFILE=path (no FRONT_PTHRESH; not with FRONT=whole or FRONT_RFILE/FRONT_R):
           covered inner-edge cells without recent hotspot support burn if p > 0 and their spread multiplier is
           multiplied by max(p, FRONT_PFLOOR=0.01) after every other ADJ term; p = 0 cells do not start burning;
           hotspot-supported, uncovered (baseline) and outside-perimeter heat cells keep x1; whole-edge fallback
           keeps x1. Only initial edge cells are weighted. FRONT_PMODE=binary (default) is the threshold rule.
           ADJ_BAND_PFILE=path ADJ_BAND_M=B ADJ_BAND_PREF=P [ADJ_BAND_FLOOR=0.25 ADJ_BAND_CAP=2] (INIT=feds): outward
           band. Unburned cells outside the start FEDS perimeter whose centre lies within B m of a perimeter cell
           centre, and that the aligned probability raster covers, get spread multiplier clip(p / P, FLOOR, CAP)
           after every other ADJ term; cells beyond the band, inside the perimeter, burning at t=0 or uncovered
           keep x1. The start front is unchanged (whatever the other options select).
           case.json init.front records the effective weighting. A deterministic weighting, not a probability.
           FRONT=whole: the whole edge band burns. If a rule selects no edge cell the whole band burns (as before).
           DT_METEOROLOGY is WXDIR/wx.json "dt_s" (3600 if absent; 900 for hrrr_weather.py --subhourly); WXSTART
           counts weather bands.
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


def read_front_probability(path, shape, transform, crs):
    """Read aligned probabilities; masked nodata represents absent model coverage."""
    with rasterio.open(path) as src:
        if src.count != 1 or src.shape != shape or src.transform != transform or src.crs != crs:
            raise ValueError("FRONT_PFILE must match landscape shape, transform and CRS, with one band")
        field = src.read(1, masked=True)
        tags = src.tags()
    covered = ~np.ma.getmaskarray(field)
    p = field.data
    if np.any(covered & (~np.isfinite(p) | (p < 0) | (p > 1))):
        raise ValueError("FRONT_PFILE covered probabilities must be finite and in [0,1]")
    return p, covered, tags


def probability_front(band, baseline, hotspot, probability, covered, threshold):
    """Replace covered edge selection; retain hotspots and uncovered baseline cells."""
    uncovered_baseline = baseline if baseline.any() else band
    selected = band & ((uncovered_baseline & ~covered) | hotspot | (covered & (probability >= threshold)))
    info = {"covered_edge_cells": int((band & covered).sum()),
            "coverage_fraction": float((band & covered).sum() / max(1, band.sum())),
            "hotspot_supported_cells": int((band & hotspot).sum()),
            "model_added_cells": int((band & covered & (probability >= threshold) & ~baseline).sum()),
            "uncovered_baseline_whole_edge": bool(not baseline.any() and (band & ~covered).any()),
            "whole_edge_fallback": not bool(selected.any())}
    return selected, info


FRONT_PFLOOR = 0.01  # smallest weighted multiplier: ELMFIRE resets adj < 0 to 1 and never tags or spreads adj = 0


def probability_front_weighted(band, baseline, hotspot, probability, covered, floor=FRONT_PFLOOR):
    """Weighted mode: (active cells, per-cell multiplier on the landscape grid, info). Covered edge cells without
    hotspot support burn if p > 0, with multiplier max(p, floor); everything else keeps multiplier 1."""
    uncovered_baseline = baseline if baseline.any() else band
    supported = band & hotspot
    weighted = band & covered & ~hotspot & (probability > 0)
    selected = (band & uncovered_baseline & ~covered) | supported | weighted
    fallback = not bool(selected.any())
    weight = np.ones(band.shape)
    if not fallback:
        weight[weighted] = np.maximum(probability[weighted], floor)
    w = weight[weighted] if weighted.any() and not fallback else np.array([])
    info = {"mode": "weighted", "floor": floor,
            "covered_edge_cells": int((band & covered).sum()),
            "coverage_fraction": float((band & covered).sum() / max(1, band.sum())),
            "hotspot_supported_cells": int(supported.sum()),
            "hotspot_override_cells": int((supported & covered).sum()),
            "weighted_cells": int(weighted.sum()) if not fallback else 0,
            "zero_probability_inactive_cells": int((band & covered & ~hotspot & (probability <= 0)).sum()),
            "floored_cells": int((weighted & (probability < floor)).sum()) if not fallback else 0,
            "weight_min": float(w.min()) if w.size else None, "weight_mean": float(w.mean()) if w.size else None,
            "weight_max": float(w.max()) if w.size else None,
            "uncovered_baseline_whole_edge": bool(not baseline.any() and (band & ~covered).any()),
            "whole_edge_fallback": fallback}
    return selected, weight, info


def outward_band_adj(start, burning, probability, covered, band_m, cell_m, p_ref, floor, cap):
    """Outward band: (multiplier grid, info). Unburned, covered cells outside the start perimeter with centre within
    band_m of a start-perimeter cell centre get clip(p / p_ref, floor, cap); all other cells x1."""
    from scipy.ndimage import distance_transform_edt
    weight = np.ones(start.shape)
    if not start.any():
        return weight, {"band_m": band_m, "p_ref": p_ref, "floor": floor, "cap": cap, "band_cells": 0,
                        "covered_band_cells": 0, "reason": "empty start perimeter"}
    dist = distance_transform_edt(~start, sampling=cell_m)
    in_band = ~start & ~burning & (dist <= band_m)
    zone = in_band & covered
    m = np.clip(probability[zone] / p_ref, floor, cap)
    weight[zone] = m
    info = {"band_m": band_m, "p_ref": p_ref, "floor": floor, "cap": cap, "band_cells": int(in_band.sum()),
            "covered_band_cells": int(zone.sum()),
            "band_coverage_fraction": float(zone.sum() / max(1, in_band.sum())),
            "floored_cells": int((m <= floor).sum()), "capped_cells": int((m >= cap).sum()),
            "weight_min": float(m.min()) if m.size else None, "weight_mean": float(m.mean()) if m.size else None,
            "weight_max": float(m.max()) if m.size else None}
    return weight, info


def fire(landscape_tif, wxdir, state_tif, issue, hours, case, *overrides):
    import datetime
    with rasterio.open(landscape_tif) as src:
        crs, transform, bands = src.crs, src.transform, src.read()
    landscape = dict(zip(LANDSCAPE_BANDS, bands))
    weather = {}
    import json as _json
    wx_info = os.path.join(wxdir, "wx.json")
    dt_met = float(_json.load(open(wx_info)).get("dt_s", 3600)) if os.path.exists(wx_info) else 3600.0
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
    init_mode, edge_cells, init_info = "hotcells", 2, None
    adj_file = None
    front_pfile, front_pthresh, front_pmode = None, None, None
    front_weight = None
    band_pfile, band_m, band_pref, band_floor, band_cap, band_weight = None, None, None, 0.25, 2.0, None
    front_km, front_rfile, front_r, front_whole = None, None, None, False
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
        if key.upper() == "ADJ_FILE":
            adj_file = value
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
        if key.upper() == "INIT":
            init_mode = value.lower()
            continue
        if key.upper() == "FRONT_PFILE":
            front_pfile = value
            continue
        if key.upper() == "FRONT_PMODE":
            front_pmode = value.lower()
            continue
        if key.upper() == "ADJ_BAND_PFILE":
            band_pfile = value
            continue
        if key.upper() in ("ADJ_BAND_M", "ADJ_BAND_PREF", "ADJ_BAND_FLOOR", "ADJ_BAND_CAP"):
            v = float(value)
            if not np.isfinite(v):
                raise ValueError("%s must be finite" % key)
            band_m, band_pref, band_floor, band_cap = {"ADJ_BAND_M": (v, band_pref, band_floor, band_cap),
                                                       "ADJ_BAND_PREF": (band_m, v, band_floor, band_cap),
                                                       "ADJ_BAND_FLOOR": (band_m, band_pref, v, band_cap),
                                                       "ADJ_BAND_CAP": (band_m, band_pref, band_floor, v)}[key.upper()]
            continue
        if key.upper() == "FRONT_PTHRESH":
            front_pthresh = float(value)
            continue
        if key.upper() == "FRONT_KM":
            front_km = float(value)
            continue
        if key.upper() == "FRONT_RFILE":
            front_rfile = value
            continue
        if key.upper() == "FRONT_R":
            front_r = float(value)
            continue
        if key.upper() == "FRONT":
            front_whole = value.lower() == "whole"
            continue
        if key.upper() == "EDGE_CELLS":
            edge_cells = int(value)
            continue
        if key.upper() == "OBSERVED":
            observed = value
            continue
        if key.upper() == "ACTIVE_HOURS":
            active_hours = float(value)
            continue
        group, _, key = key.rpartition(":")
        extra.setdefault(group or "?", {})[key] = parse_value(value)
    front_probability = None
    if front_pmode not in (None, "binary", "weighted"):
        raise ValueError("FRONT_PMODE must be binary or weighted")
    if front_pmode == "weighted":
        if front_pfile is None:
            raise ValueError("FRONT_PMODE=weighted requires FRONT_PFILE")
        if front_pthresh is not None:
            raise ValueError("FRONT_PMODE=weighted takes no FRONT_PTHRESH")
        if front_whole or front_rfile is not None or front_r is not None:
            raise ValueError("FRONT_PMODE=weighted cannot be combined with FRONT=whole or FRONT_RFILE/FRONT_R")
        if init_mode != "feds" or not observed:
            raise ValueError("FRONT_PFILE requires INIT=feds and OBSERVED")
        front_probability = read_front_probability(front_pfile, bands.shape[1:], transform, crs)
    elif front_pmode == "binary" and front_pfile is None:
        raise ValueError("FRONT_PMODE=binary requires FRONT_PFILE")
    elif front_pfile is not None or front_pthresh is not None:
        if front_pfile is None or front_pthresh is None or not np.isfinite(front_pthresh) or not 0 <= front_pthresh <= 1:
            raise ValueError("FRONT_PFILE requires FRONT_PTHRESH in [0,1]")
        if init_mode != "feds" or not observed:
            raise ValueError("FRONT_PFILE requires INIT=feds and OBSERVED")
        front_probability = read_front_probability(front_pfile, bands.shape[1:], transform, crs)
    band_probability = None
    if band_pfile is not None or band_m is not None or band_pref is not None:
        if band_pfile is None or band_m is None or band_pref is None:
            raise ValueError("ADJ_BAND_PFILE needs ADJ_BAND_M and ADJ_BAND_PREF")
        if band_m <= 0 or band_pref <= 0 or not 0 < band_floor <= band_cap:
            raise ValueError("ADJ_BAND_M, ADJ_BAND_PREF must be > 0 and 0 < ADJ_BAND_FLOOR <= ADJ_BAND_CAP")
        if init_mode != "feds" or not observed:
            raise ValueError("ADJ_BAND_PFILE requires INIT=feds and OBSERVED")
        band_probability = read_front_probability(band_pfile, bands.shape[1:], transform, crs)
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
        with open(observed) as observed_file:
            obs = json.load(observed_file)
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
        feds_fine = None
        if known:
            geom = shapely.transform(shape(known[-1]["geometry"]),
                                     lambda xy: np.column_stack(to_grid.transform(xy[:, 0], xy[:, 1])))
            burned |= rasterize([(mapping(geom), 1)], out_shape=burned.shape, transform=st, dtype="uint8").astype(bool)
            log.info("latest FEDS perimeter known at issue: %s", known[-1]["time"])
            if init_mode == "feds":
                feds_fine = rasterize([(mapping(geom), 1)], out_shape=bands.shape[1:], transform=transform,
                                      dtype="uint8").astype(bool)
                feds_poly_m2 = float(geom.area)
                feds_time = known[-1]["time"]
        burned_fine = np.kron(burned, np.ones((refine, refine), dtype=bool))[:bands.shape[1], :bands.shape[2]]
        from scipy.ndimage import binary_dilation
        fine = lambda a: np.kron(a, np.ones((refine, refine), dtype=bool))[:bands.shape[1], :bands.shape[2]]
        burnable = lambda h: int((fine(h) & ~np.isin(landscape["fbfm40"], list(range(91, 100)))).sum())
        if init_mode == "feds" and feds_fine is not None and feds_fine.any():
            from scipy.ndimage import binary_erosion
            band = feds_fine & ~binary_erosion(feds_fine, iterations=edge_cells)
            if front_km is not None:
                from scipy.ndimage import distance_transform_edt
                near_hot_fine = (distance_transform_edt(~fine(hot), sampling=abs(transform.a)) <= front_km * 1000
                                 if hot.any() else np.zeros_like(band))
            else:
                near_hot_fine = fine(binary_dilation(hot)) if hot.any() else np.zeros_like(band)
            active = band & near_hot_fine
            active_mode = "detections"
            front_info = {"front_km": front_km, "active_hours": active_hours, "detection_edge_cells": int(active.sum())}
            if front_rfile and front_r is not None:
                with rasterio.open(front_rfile) as src:
                    rfield = src.read(1)
                if rfield.shape != band.shape:
                    raise ValueError("FRONT_RFILE %s is %s, landscape grid is %s" % (front_rfile, rfield.shape, band.shape))
                active = active | (band & (rfield >= front_r))
                active_mode = "detections+r"
                front_info.update(front_r=front_r, r_edge_cells=int((band & (rfield >= front_r)).sum()))
            if front_whole:
                active, active_mode = band, "front_whole"
            if front_probability is not None and front_pmode == "weighted":
                probability, covered, provenance = front_probability
                active, front_weight, learned_info = probability_front_weighted(band, active, near_hot_fine,
                                                                                probability, covered)
                front_info.update(learned_info, probability_file=front_pfile,
                                  covered_fuel_cells=int(covered.sum()), provenance=provenance)
                active_mode = "detections+weighted"
            elif front_probability is not None:
                probability, covered, provenance = front_probability
                active, learned_info = probability_front(band, active, near_hot_fine, probability,
                                                         covered, front_pthresh)
                front_info.update(learned_info, probability_file=front_pfile, threshold=front_pthresh,
                                  covered_fuel_cells=int(covered.sum()), provenance=provenance)
                active_mode = "detections+probability"
            front_info["whole_edge_fallback"] = not bool(active.any())
            if not active.any():
                active, active_mode = band, "whole_edge"
            # Recent heat clearly outside the perimeter (beyond one state cell): growth since it was drawn.
            feds_coarse = np.zeros_like(hot)
            fr, fc = np.nonzero(feds_fine)
            feds_coarse[np.minimum(fr // refine, hot.shape[0] - 1), np.minimum(fc // refine, hot.shape[1] - 1)] = True
            outside = hot & ~binary_dilation(feds_coarse)
            burning = active | fine(outside)
            if front_weight is not None:
                front_weight[fine(outside)] = 1.0  # observed heat outside the perimeter is never down-weighted
            phi = np.where(burning, -1.0, 1.0)
            if band_probability is not None:
                band_weight, band_info = outward_band_adj(feds_fine, burning, band_probability[0], band_probability[1],
                                                          band_m, abs(transform.a), band_pref, band_floor, band_cap)
                front_info["outward_band"] = dict(band_info, probability_file=band_pfile,
                                                  provenance=band_probability[2])
            interior = feds_fine & ~burning
            landscape["fbfm40"] = np.where(interior, 99, landscape["fbfm40"]).astype("int16")
            cell_m2 = abs(transform.a * transform.e)
            nb = np.isin(landscape["fbfm40"], list(range(91, 100)))
            init_info = {"mode": "feds", "perimeter_time": feds_time, "edge_cells": edge_cells,
                         "active_mode": active_mode, "feds_polygon_acres": feds_poly_m2 / 4046.8564224,
                         "feds_raster_acres": float(feds_fine.sum() * cell_m2 / 4046.8564224),
                         "edge_band_cells": int(band.sum()), "active_edge_cells": int(active.sum()),
                         "burning_cells": int(burning.sum()), "burning_burnable_cells": int((burning & ~nb).sum()),
                         "outside_hot_cells_375m": int(outside.sum()), "hot_cells_375m": int(hot.sum()),
                         "fine_cell_m": abs(transform.a), "front": front_info}
            log.info("INIT=feds: %s", init_info)
        elif min_start > 0:
            # Minimum initial footprint: a few detections over non-burnable cells (roads, fields, water) cannot
            # spread; grow them by one 375 m VIIRS cell at a time (at most two) until n burnable cells burn.
            before, dilations = burnable(hot), 0
            while burnable(hot) < min_start and dilations < 2 and hot.any():
                hot = binary_dilation(hot)
                dilations += 1
            min_start_info = {"threshold_cells": min_start, "burnable_start_cells_before": before,
                              "burnable_start_cells_after": burnable(hot), "dilations_375m": dilations}
            log.info("minimum initial footprint: %s", min_start_info)
        if init_info is None:
          if init_mode == "feds":
            init_info = {"mode": "hotcells_fallback", "reason": "no FEDS perimeter known at issue"}
          else:
            init_info = {"mode": "hotcells"}
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
        ws_mean = weather["ws"][: int(float(hours) * 3600 / dt_met) + 1].mean(0)
        k = int(round(wx_transform.a / transform.a))
        ws_fine = np.kron(ws_mean, np.ones((k, k)))[:phi.shape[0], :phi.shape[1]]
        factor = np.clip((np.maximum(ws_fine, 0.1) / adj_wind_ref) ** adj_wind_exp, 0.1, 10.0)
        adj = adj * factor
        log.info("ADJ wind term: exponent %g, factor %.2f-%.2f", adj_wind_exp, factor.min(), factor.max())
    if adj_file:
        with rasterio.open(adj_file) as src:
            correction = src.read(1).astype("float64")
        if correction.shape != adj.shape:
            raise ValueError("ADJ_FILE %s is %s, landscape grid is %s" % (adj_file, correction.shape, adj.shape))
        adj = adj * correction
        log.info("ADJ_FILE %s: factor %.2f-%.2f (median %.2f)", adj_file, correction.min(), correction.max(),
                 float(np.median(correction)))
    if front_weight is not None:
        # Weighted front: multiply after the physical terms and ADJ_FILE, so they stay composable.
        base = adj.copy()
        adj = adj * front_weight
        changed = front_weight != 1
        init_info["front"]["effective_adj"] = {
            "weighted_cells": int(changed.sum()),
            "base_adj_min": float(base[changed].min()) if changed.any() else None,
            "adj_min": float(adj[changed].min()) if changed.any() else None,
            "adj_mean": float(adj[changed].mean()) if changed.any() else None,
            "adj_max": float(adj[changed].max()) if changed.any() else None}
        log.info("FRONT_PMODE=weighted effective adj: %s", init_info["front"]["effective_adj"])
    if band_probability is not None:
        if band_weight is None:
            raise ValueError("ADJ_BAND_PFILE needs a FEDS perimeter known at issue")
        base = adj.copy()
        adj = adj * band_weight
        zone = band_weight != 1
        init_info["front"]["outward_band"]["effective_adj"] = {
            "changed_cells": int(zone.sum()),
            "adj_min": float(adj[zone].min()) if zone.any() else None,
            "adj_mean": float(adj[zone].mean()) if zone.any() else None,
            "adj_max": float(adj[zone].max()) if zone.any() else None,
            "base_adj_mean": float(base[zone].mean()) if zone.any() else None}
        log.info("ADJ_BAND outward band: %s", init_info["front"]["outward_band"])
    for k in ("m1", "m10", "m100"):
        weather[k] = weather[k] + moist_add
    # Keys given without a group go to the group write_case already uses them in, else SIMULATOR.
    loose = extra.pop("?", {})
    hours = float(hours)
    write_case(case, transform, crs, landscape, {}, adj, phi, dt_met, hours * 3600, extra=extra)
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
                   "overrides": list(overrides), "min_start": min_start_info,
                   "init": init_info, "dt_meteorology_s": dt_met}, f, indent=1)
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
