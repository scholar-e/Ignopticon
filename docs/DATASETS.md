# MODIS / VIIRS fire datasets: what they are, how we process them, what the UI gets

This describes every fire dataset behind the harmonized MODIS/VIIRS record (PLAN.md M7): the NASA products, our processing, the formats, and the payloads the UI will receive. The bulk data (~100 GB) is not in the repo. Every file here can be rebuilt with the scripts listed below.

## Where the UI gets its data: the back end, not the front end

**The final data will be served by a back-end service. Nothing is bundled into the front end.** The UI requests grids, daily layers and fire lists from the back end over HTTP, and the back end reads the processed files described below.

The files in `data/samples/ui/` are stand-ins with exactly the payload format the back end will return. They exist so the UI can be built now, on another computer, without the bulk data. Treat them as mock API responses: point the UI at an API base URL, and serve `data/samples/ui/` under it during development.

Proposed endpoints (subject to change; the payloads are fixed, see "UI export format"):

| Endpoint | Returns | Sample stand-in |
|---|---|---|
| `GET {API}/index.json` | grid definition, products, available dates | `data/samples/ui/index.json` |
| `GET {API}/{product}/{YYYY-MM-DD}.png` | one day's layer for the whole grid (encoding below) | `data/samples/ui/MYD14A1/2013-08-*.png` |
| `GET {API}/{product}/{YYYY-MM-DD}.json` | the cells with fire that day, with values | `data/samples/ui/VNP14A1/2013-08-*.json` |
| `GET {API}/elevation.png` | static elevation and roughness layer | `data/samples/ui/elevation.png` |

`product` is one of `MOD14A1`, `MYD14A1`, `VNP14A1`, `VJ114A1`, and `harmonized` once the harmonization is fitted. `harmonized` will use the same encoding, with its R channel holding the probability that VIIRS saw fire.

## 1. Source products (NASA LAADS, Earthdata login)

All four are the daily gridded active fire products. Each 1 km sinusoidal tile cell gets a daily class, already geolocated, which is what makes "not observed" possible (FIRMS point lists can't say where a sensor looked and saw nothing).

| Product | Sensor / satellite | Collection | Starts | Days per file | Pixel |
|---|---|---|---|---|---|
| MOD14A1 | MODIS / Terra | 6.1 | 2000-02-18 | 8 | 1 km |
| MYD14A1 | MODIS / Aqua | 6.1 | 2002-07-04 | 8 | 1 km |
| VNP14A1 | VIIRS / Suomi NPP | 2 | 2012-01-19 | 1 | 1 km (from 375 m) |
| VJ114A1 | VIIRS / NOAA-20 | 2 | 2018-01-05 | 1 | 1 km (from 375 m) |

- **Tiles:** the lower 48 are covered by 17 sinusoidal tiles in h07–h13, v04–v06. Each tile is 1200 × 1200 cells of 926.6 m on the MODIS sphere (R = 6,371,007.181 m).
- **FireMask classes (both sensors):**

  | Class | Meaning |
  |---|---|
  | 0–2 | not processed (missing, obsolete, or other reasons) |
  | 3 | water |
  | 4 | cloud |
  | 5 | land, no fire |
  | 6 | unknown |
  | 7 | fire, low confidence |
  | 8 | fire, nominal confidence |
  | 9 | fire, high confidence |

- **MaxFRP:** the maximum fire radiative power in the cell that day, in MW (stored ×10).
- **Download:** `scripts/gridded_fire_download.py OUTDIR PRODUCT FIRST LAST [THREADS]`.
  - LAADS lists these under collection 61 (MODIS) or 5200 (VIIRS), but serves the files from archive sets 5200, 5201 or 5110; the script tries them in turn.
  - It forces IPv4, because LAADS publishes IPv6 addresses some networks can't route.
  - It retries quickly, because about one connection in eight drops.
- **Volume over the lower 48:**
  - MODIS: about 0.75 GB and 782 files per satellite-year.
  - VIIRS: about 1.2 GB and 6,000 files per satellite-year (about 16 minutes each to download).

## 2. Processed grid (our format)

Everything is put on one 0.1° lat/lon grid over the lower 48:

| | |
|---|---|
| Size | 590 columns × 260 rows |
| Extent | lon −125 to −66, lat 24 to 50 |
| Cell (row, col) | lat 50 − 0.1·(row+1) to 50 − 0.1·row; lon −125 + 0.1·col to −125 + 0.1·(col+1) |
| Orientation | north up; row 0 is the northern edge |
| Assignment | each 1 km pixel goes to the cell containing its centre (~92–132 pixels per cell: about 144·cos(lat), so fewest in the north) |

### 2a. Fire grid: `data/harmonize/grid/PRODUCT_YEAR.npz`
Built by `scripts/gridded_fire_grid.py INDIR PRODUCT YEAR OUTDIR`. Arrays are days × 260 × 590:

| Array | Type | Meaning |
|---|---|---|
| `fire` | uint16 | 1 km pixels classed fire (7–9) in the cell that day |
| `fire_nh` | uint16 | pixels classed nominal or high confidence fire (8–9) |
| `clear` | uint16 | pixels seen clearly: fire, land or water (3, 5, 7–9) |
| `frp` | float32 | summed MaxFRP of the fire pixels, MW |
| `day` | int | day of year of each slice |

Per cell and day this gives three states: **fire** (`fire > 0`), **clear, no fire** (`clear > 0`, `fire = 0`), and **not observed** (`clear = 0`: cloud, or no overpass).

A useful normalised rate is `fire / clear`, fire pixels per clear look. It removes differences in how often each sensor got a clear view.

Example: MODIS Terra 2020 has 164,703 fire pixels, and 82% of cell-days have a clear look. VIIRS S-NPP 2013 has 88,965 fire pixels, and 87% of cell-days have a clear look.

### 2b. Elevation: `data/harmonize/elevation.npz` (in the repo as a sample)
Built by `scripts/elevation_grid.py ETOPO.nc OUT.npz` from NOAA ETOPO 2022, 60 arc-second (about 1.8 km, 6 × 6 samples per cell). Arrays are 260 × 590:
- `elev`: mean elevation, m
- `elev_sd`: its standard deviation (terrain roughness), m

Seafloor below −100 m is NaN.

### 2c. Weather and wind: `data/harmonize/weather/weather_YEAR.npz`
Built by `scripts/gridmet_grid.py GRIDMET_DIR YEAR OUT` from gridMET (University of Idaho: daily, 1/24°, lower 48, 1979 on, no key), averaged into the 0.1° cells. Arrays are float16, days × 260 × 590:

| Array | Meaning |
|---|---|
| `wind` | mean 10 m wind speed, m/s |
| `u`, `v` | mean east and north wind components, m/s, averaged as vectors so opposing winds cancel |
| `vpd` | vapour pressure deficit, kPa |
| `erc` | energy release component (NFDRS fuel model G) |
| `fm100` | 100-hour dead fuel moisture, % |
| `pr` | precipitation, mm/day |

Cells outside gridMET (ocean, Canada, Mexico, the Great Lakes) are NaN, the same cells on every day. Values are rounded before the float16 cast (wind, u, v and vpd to 0.01; erc, fm100 and pr to 0.1). All 27 years 2000–2026 are gridded (2026 runs to Nov 12, 316 days), about 200 MB a year, 5.7 GB in total.

### 2d. Harmonized record: `data/harmonize/out/harmonized_YEAR.npz` (in progress)
Built by `scripts/harmonize.py fit|apply`.
- **Output:** per cell-day, `p_viirs`, the probability that VIIRS S-NPP would have detected fire, given what MODIS Terra and Aqua saw. That puts 2000–2011 on VIIRS's more sensitive footing, so the record has no step at 2012.
- **Also stored:** `observed` (0 no clear look, 1 MODIS only, 2 VIIRS), `modis_fire`, and `viirs_fire` (2012 on).
- **Training:** the 2012–2026 overlap, using only cell-days VIIRS saw clearly, so cloud is never read as "no fire".
- **Testing:** whole years held out, against simply rescaling MODIS.
- **Feature sets** (`HARMONIZE_FEATURES`): `base` (MODIS only), `elev` (adds elevation), `all` (adds elevation and weather). Each set is kept only if it helps on the held-out years.
- Results will be logged in MODELLING_LOG.md.

### 2e. Fuel: `data/harmonize/fuel.npz`
Built by `scripts/fuel_grid.py TILEDIR OUT.npz` from LANDFIRE LF2025 (CONUS, 30 m) through its public ArcGIS ImageServers (`https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_{FBFM40,CC,CH,CBH,CBD}_CONUS/ImageServer`, no key). Each layer is sampled nearest-neighbour on a 0.004° lat/lon lattice, which gives 25 × 25 = 625 samples per cell, about 350–450 m apart. The script fetches 2° × 2° tiles (500 × 500 px, under a second each) on 12 threads and caches them in `data/raw/landfire_tiles/LAYER/` (620 MB). A full build takes about 100 s, and a rerun reuses the tiles. At this sample spacing the server reads from its pyramids. On a test half-degree in the Sierra foothills, the class shares came within 2.5 points of a near-native (~30 m) request.

Arrays are 260 × 590. They are float32 unless noted, and NaN where the cell has no LANDFIRE sample (ocean, Canada, Mexico):

| Array | Meaning |
|---|---|
| `frac_nb` | share of the cell's valid samples that are non-burnable, FBFM40 91–99 (urban 91, snow 92, agriculture 93, water 98, bare 99; the Great Lakes count as water) |
| `frac_gr`, `frac_gs`, `frac_sh` | grass (101–109), grass-shrub (121–124), shrub (141–149) |
| `frac_tu`, `frac_tl`, `frac_sb` | timber-understory (161–165), timber litter (181–189), slash-blowdown (201–204) |
| `fbfm40_mode` | int16, the commonest FBFM40 code in the cell (−1 where there is none) |
| `cc` | mean canopy cover, % |
| `ch` | mean canopy height, m |
| `cbh` | mean canopy base height, m |
| `cbd` | mean canopy bulk density, kg/m³ |
| `valid` | share of the cell's 625 samples with a valid FBFM40 code (below 1 on coasts and borders) |

The seven group shares add up to 1 wherever a cell is valid. LANDFIRE stores CH and CBH as m × 10 and CBD as kg/m³ × 100. The script divides these out, and the service metadata agrees (maximums: CC 95, CH 510, CBH 100, CBD 45). Non-forest samples have zero canopy values, so the canopy means combine how much of the cell is forest with how the stands are built.

National area shares (cos-latitude weighted): non-burnable 27.5%, grass 24.8%, grass-shrub 12.5%, shrub 9.3%, timber-understory 7.5%, timber litter 18.3%, slash-blowdown under 0.1%. By dominant code, 18.5% of cells are mostly agriculture (93) and 6.8% mostly water (98).

## 3. UI export format (what the back end returns)

Built by `scripts/export_ui.py`. One PNG per product and day covers the whole grid:
- 590 × 260 px, plate carrée, north up.
- Pixel (x, y) is cell (col x, row y), so lon = −125 + 0.1·(x + 0.5) and lat = 50 − 0.1·(y + 0.5) at its centre.
- Drawn as one image over lon −125…−66, lat 24…50.

| Channel | Meaning | Decode |
|---|---|---|
| R | fire pixels in the cell | value (capped at 255) |
| G | clear looks (1 km pixels seen clearly) | value (at most 132 on this grid; `R / G` is fire pixels per clear look) |
| B | summed FRP | FRP_MW = 10^(B / 40) − 1 (B 40 ≈ 9 MW, 120 ≈ 1 GW) |
| A | observed | 255 = the sensor saw the cell clearly, 0 = not observed (cloud or no pass) |

A day's JSON lists only the cells with fire:

```json
{"product": "VNP14A1", "date": "2013-08-20",
 "fires": [[col, row, fire_pixels, fire_pixels_nominal_or_high, clear, frp_mw], ...]}
```

Elevation PNG, static, same grid:
- R·256 + G = elevation in metres + 1000.
- B = roughness ÷ 4, in metres.
- A = 0 where there is no data.

`index.json` gives the grid definition, the products and the available dates.

**Samples in the repo** (`data/samples/ui/`): MODIS Aqua and VIIRS S-NPP for August 2013, the same days from both sensors so their difference shows, plus elevation and the index. August 2013 includes the Rim Fire (Yosemite, from Aug 17).

**Example reader:** `docs/ui-example.html` is a dependency-free page that loads `index.json` and a day's PNG from a configurable API base URL, decodes the channels, and has a date stepper and a product toggle. Serve the repo root (`python3 -m http.server 8000`) and open `http://localhost:8000/docs/ui-example.html`.

## 4. Rebuilding

```
scripts/gridded_fire_download.py data/raw/gridded MOD14A1 2000 2026 16     # and MYD14A1 2002.., VNP14A1 2012.., VJ114A1 2018..
scripts/gridded_fire_grid.py data/raw/gridded VNP14A1 2013 data/harmonize/grid
curl -o data/raw/dem/etopo2022_60s.nc https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/60s_surface_elev_netcdf/ETOPO_2022_v1_60s_N90W180_surface.nc
scripts/elevation_grid.py data/raw/dem/etopo2022_60s.nc data/harmonize/elevation.npz
# gridMET: https://thredds.northwestknowledge.net/thredds/fileServer/MET/{vs,th,vpd,erc,fm100,pr}/{var}_{YEAR}.nc
scripts/gridmet_grid.py data/raw/gridmet 2013 data/harmonize/weather
scripts/fuel_grid.py data/raw/landfire_tiles data/harmonize/fuel.npz
HARMONIZE_FEATURES=all scripts/harmonize.py fit data/harmonize/grid data/harmonize/out 2012 2026
scripts/export_ui.py fires data/harmonize/grid VNP14A1 2013 213 243 data/samples/ui
```

## 5. Status (2026-11-14, event day 1)

| Item | State |
|---|---|
| MODIS downloads | Terra 2000–2012 and 2020, Aqua 2002–2015, continuing |
| VIIRS downloads | S-NPP 2012–2014, 2017 and 2021; NOAA-20 2018–2020 and 2023; continuing (the slow part) |
| Gridding to 0.1° | Terra 2000–2004 and 2020, Aqua 2013, S-NPP 2013; continuing as downloads finish |
| Elevation | done (in the repo) |
| gridMET | downloaded (6 variables × 2000–2026); gridding to 0.1° next |
| UI samples and example page | done: Aqua and S-NPP for August 2013, elevation, index (6.9 MB, in the repo); checked against the source grids |
| Harmonization | script written, fitted once the overlap years are gridded |
| Fire-spread modelling (ELMFIRE, Widemouth 2) | separate track: see MODELLING_LOG.md |

## 6. Dataset catalog and serving

Every dataset is listed in [`data/catalog.json`](../data/catalog.json), one entry per dataset. Each entry has a stable id, source (URL, collection, DOI, licence), producing script and command, path pattern, format, schema (pointing back to the sections above), units, grid, temporal coverage, size on disk, status, whether it is in git, and a serving tier. `scripts/catalog_check.py` checks the catalog against the disk and prints this table. Run it after adding data. Sizes below were measured on 2026-11-14.

Tiers:
- **public-static:** payloads the UI loads. The `{API}` paths match the endpoint table at the top of this file; `/data/...` paths are earth's map layers.
- **backend-only:** processed grids the back end reads to make payloads. Never public.
- **raw-archive:** downloads and intermediates. Rebuildable from public sources with the listed script, and never served.

| id | tier | size | status | serving path |
|---|---|---|---|---|
| ui/index | public-static | 1.6 KB | sample (Aug 2013) | `{API}/index.json` |
| ui/fire-daily-layers | public-static | 6.7 MB (full export ~4 GB) | sample (Aug 2013, MYD14A1 + VNP14A1) | `{API}/{product}/{YYYY-MM-DD}.png`, `.json` |
| ui/elevation-png | public-static | 0.2 MB | complete | `{API}/elevation.png` |
| map/fires-viirs | public-static | 4.1 MB | 2026-08-02, 2026-10-29 | `/data/fires/viirs/{YYYY}/{MM}/{DD}[-heat].json` |
| map/fires-feds | public-static | 1.4 MB | Aug and Oct 2026 | `/data/fires/feds/{YYYY}/{MM}/{DD}-{HHMM}.json` |
| map/fire-frames | public-static | 0.7 MB | Widemouth 2 | `/data/fires/frames/{YYYY}/{MM}/{DD}.json` |
| map/forecasts | public-static | 0.2 MB | Widemouth 2 | `/data/forecasts/{fire_id}/{issue}.json` |
| map/fuels-legend | public-static | <1 KB | complete | `/data/fuels/fbfm40-colors.json` |
| map/thermal-i4 | public-static | 745 MB | 2 days (zooms 8-9 not in git) | `/data/thermal/i4/{date}/N21-{day,night}/{z}/{x}/{y}.png` |
| map/weather-gfs-wind | public-static | 71 MB | 2 days + current | `/data/weather/{YYYY}/{MM}/{DD}/...-gfs-1.0.json` |
| map/earth-topo | public-static | 0.7 MB | complete (upstream) | `/data/earth-topo.json` |
| map/oscar-sample | public-static | 5.2 MB | upstream sample | `/data/oscar/...` |
| fire-grid/MODIS-Terra | backend-only | 812 MB | 2000–2026 | |
| fire-grid/MODIS-Aqua | backend-only | 756 MB | 2002–2026 | |
| fire-grid/VIIRS-SNPP | backend-only | 368 MB | 2012–2024 (2025–26 waiting on download) | |
| fire-grid/VIIRS-NOAA20 | backend-only | 252 MB | 2018–2026 | |
| weather/gridmet-0.1deg | backend-only | 6.0 GB | 2000–2026 (to Nov 12) | |
| terrain/etopo-0.1deg | backend-only | 0.9 MB | complete (in git) | |
| fuel/landfire-0.1deg | backend-only | 1.8 MB | complete (in git) | |
| harmonized/viirs-equivalent | backend-only | (not yet written) | planned | later `{API}/harmonized/...` |
| harmonized/models | backend-only | 13 MB | fits in progress | |
| fire/widemouth-observations | backend-only | 0.9 MB | complete | |
| fire/widemouth-landscape | backend-only | 31 MB | complete | |
| forecast/widemouth-elmfire-runs | backend-only | 3.0 GB | complete | |
| forecast/widemouth-ensembles, forecast/widemouth-ellipse-runs | raw-archive | 3.3 MB | complete | |
| raw/MOD14A1, raw/MYD14A1, raw/VNP14A1, raw/VJ114A1 | raw-archive | 19.6 + 18.2 + 18.3 + 11.1 GB | VNP14A1 2026 still downloading | |
| raw/gridmet, raw/etopo2022, raw/landfire-tiles | raw-archive | 12.8 GB, 0.5 GB, 0.6 GB | complete | |
| raw/viirs-afp-375m, raw/viirs-l1b, raw/viirs-geo | raw-archive | 0.3, 12.9, 5.6 GB | event granules | |
| raw/firms-csv, raw/gfs, raw/hrrr | raw-archive | 19 MB, 7 MB, 0.76 GB | event snapshots | |

**Storage totals (2026-11-14):**
- **public-static:** 0.83 GB now, mostly the I4 thermal tiles. With the full daily fire layers for all products plus `harmonized` (~37,000 product-days × ~108 KB), expect about 5 GB.
- **backend-only:** 11.2 GB. Of that, the serving inputs (fire grids, weather, terrain, fuel) are 8.2 GB and the ELMFIRE runs are 3.0 GB.
- **raw-archive:** 101 GB. Keep it on this machine, or in cold storage if it must be shared. It never needs to be online.

**Hosting recommendation:**
- **Public-static payloads go on static object storage behind a CDN:** S3 + CloudFront, or Cloudflare R2 (no egress fees). The payloads are immutable files keyed by product and date, so they cache forever, cost cents a month at ~5 GB, and stay up without a server. The UI's `{API}` base URL becomes the bucket's CDN domain. Add CORS headers for the UI origin and long `Cache-Control` on dated files, and short caching on `index.json` and `current*.json`.
- **Backend-only grids go in the same bucket under a private prefix** (for example `private/`, with no public read and no CDN), or simply stay on the machine that regenerates the payloads. The back end here is a batch exporter (`export_ui.py`, `forecast_to_geojson.py`, ...), not a live service: it reads these grids, writes payloads, and uploads them.
- **This PC is for local development only.** It sits behind a home network, so serving from it would need a tunnel, and it is down whenever it is off. Serve `data/samples/ui/` or `earth/public/` locally with `python3 -m http.server` while building the UI.
- **EC2 (or another always-on VM or container) only if a live API is added later,** for example on-demand forecasts that run ELMFIRE for a new ignition. Even then, keep the static payloads on the CDN.
- **Tillicum is an HPC batch cluster (Slurm, GPU-hours), not a web host.** Use it to produce data (ensembles, training) and copy the results to the bucket.
