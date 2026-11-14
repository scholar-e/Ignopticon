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

Cells outside gridMET (ocean, Canada, Mexico) are NaN.

### 2d. Harmonized record: `data/harmonize/out/harmonized_YEAR.npz` (in progress)
Built by `scripts/harmonize.py fit|apply`.
- **Output:** per cell-day, `p_viirs`, the probability that VIIRS S-NPP would have detected fire, given what MODIS Terra and Aqua saw. That puts 2000–2011 on VIIRS's more sensitive footing, so the record has no step at 2012.
- **Also stored:** `observed` (0 no clear look, 1 MODIS only, 2 VIIRS), `modis_fire`, and `viirs_fire` (2012 on).
- **Training:** the 2012–2026 overlap, using only cell-days VIIRS saw clearly, so cloud is never read as "no fire".
- **Testing:** whole years held out, against simply rescaling MODIS.
- **Feature sets** (`HARMONIZE_FEATURES`): `base` (MODIS only), `elev` (adds elevation), `all` (adds elevation and weather). Each set is kept only if it helps on the held-out years.
- Results will be logged in MODELLING_LOG.md.

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
