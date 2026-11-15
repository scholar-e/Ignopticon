# Modelling log

What changed in the fire modelling science, in order: the assumption or method, why, the evidence (numbers), and what is still uncertain. Code and data plumbing that doesn't change the science stays out (see PLAN.md and git). Newest entries at the bottom. Scores are from `scripts/heat_frames.py`:
- **CSI:** predicted against observed VIIRS hot cells per overpass, with one 375 m cell of offset allowed.
- **IoU:** burned area against the FEDS perimeter.

Higher is better for both. "In-sample" means the setting was chosen on the same observations it's scored on.

## Test case
Widemouth 2, Utah (centre -112.37, 38.79). The grid is a 60 km box, 375 m cells for the observations, azimuthal equidistant. FEDS area by date:

| Time (UTC) | Burned |
|---|---|
| Aug 1 00Z | 19 km² |
| Aug 2 00Z | 255 km² (blow-up on Aug 1, with almost no VIIRS detections that day) |
| Aug 2 12Z | 296 km² |
| Aug 3 00Z | 363 km² |
| Aug 3 12Z | 373 km² |
| Aug 4 00Z | 445 km² |
| Aug 5 00Z | 484 km² |
| Aug 6 00Z | 508 km² |
| Aug 6 12Z | 526 km² |

Growth happens almost entirely in the afternoon burning periods. Overnight it's 1–10 km².

---

## 2026-11-14 (event day 1)

### 1. Baseline before ELMFIRE: wind-driven ellipse (M2)
- **Method:** constant head rate of spread downwind from cells seen burning in the 4 h before issue. Length-to-breadth from the GFS 10 m wind (FBP). No fuel, terrain, or wind change over time.
- **Result:** issued Aug 2 12Z. The best head rate was 0.02 m/s: mean CSI 0.30, IoU 0.68 at Aug 3 00Z. No growth at all gives CSI 0.00 but IoU 0.66.
- **Lesson:** perimeter IoU barely separates runs, because the starting area dominates it. Overpass CSI does separate them, so CSI is the main score.
- **Caveat:** its FRP was ~20x below observed.

### 2. Plain ELMFIRE forecast (model A), first version
- **Engine:** ELMFIRE 1.1, upstream f4927b7. Rothermel surface spread with Scott & Burgan FBFM40 fuel models, a level-set front, and crown fire by its defaults. No spotting or suppression.
- **Fuels and terrain:**
  - LANDFIRE LF2025 FBFM40, canopy cover/height/base height/bulk density, and USGS 3DEP elevation.
  - Fetched at 30 m in UTM 12N, then resampled to 37.5 m on the observation grid (each 375 m cell split 10×10). Nearest neighbour for categorical and canopy layers, bilinear for elevation.
  - Slope and aspect from elevation by Horn's method.
  - Dominant fuels: GS2 (122) 21%, GR2 (102) 18%, GS1 (121) 13%, SH5 (145) 11%.
- **Weather:** from the HRRR forecast run at the issue time (f00–f24), not later analyses, so the forecast uses only what was known then.
  - Wind: 10 m wind × 0.87 gives the 20-ft wind ELMFIRE expects (log profile, ~0.03 m roughness). HRRR's grid-relative components are rotated to true north.
  - A first version had a rotation error: HRRR gives lon_0 as 262.5. It is fixed, and wind now agrees with GFS at Aug 2 18Z (241° vs 242°).
  - Dead fuel moisture: 1-h = Simard (1968) equilibrium moisture from 2 m T and RH, clipped to 2–35%. 10-h = 1-h + 1, 100-h = 1-h + 2. These are crude lags, to be fitted.
  - Live moisture is constant: herbaceous 30%, woody 60%.
  - Weather lives on the 375 m grid; ELMFIRE interpolates it bilinearly.
- **Initial state:** every cell burned by issue (the fire state's arrival time ≤ issue) starts burning. Its whole perimeter is a front.
- **Result (Aug 2 12Z issue):**
  - Burned 904 km² by Aug 3 00Z against FEDS 363 (IoU 0.39), and 1,445 km² by Aug 3 12Z against 373 (IoU 0.26).
  - CSI 0.04–0.10, against 0.30 for the ellipse.
  - Its FRP is the right order of magnitude (91–110 GW against 7–42 GW observed). The ellipse was 20x low.
- **Interpretation:** a 2.5–4x overprediction of area.

### 3. Spread multiplier × live herbaceous moisture sweep (first calibration, in-sample)
- **Settings:** spread multiplier (ELMFIRE's `adj` raster) of 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6 or 1.0, against live herbaceous moisture of 30% or 90%. 16 members, Aug 2 12Z issue, same initial state as entry 2.
- **Result:**
  - Best: multiplier 0.05 with LH 90%. Mean CSI 0.34 (against the ellipse's 0.30), IoU 0.71 / 0.69, 360 km² against FEDS 363.
  - Scores improve monotonically toward the slowest setting, and LH 90% beats 30% at every multiplier.
  - Even the best run predicts 410–520 hot cells per overpass, where 121–310 were observed.
- **Interpretation:**
  - The optimum sits at the edge of the range, and a ×0.05 multiplier is too large to be a physical calibration constant. The model is systematically wrong elsewhere.
  - Leading hypothesis: the initial state. Igniting the whole ~250 km² perimeter makes edges spread that had long gone quiet.

### 4. Initial state from hotspots: burning edges (M3)
- **Change:** cells seen hot by any VIIRS overpass in the 6 h before issue start burning. The rest of the area burned by issue becomes non-burnable (fuel model 99), so quiet edges can't spread.
- **Detections:** S-NPP, NOAA-20 and NOAA-21 active fires, Jul 30–Aug 5. S-NPP has no granules Aug 3–5. There are 25 overpasses over the fire from Aug 2 to Aug 5.
- **At Aug 2 12Z:** 1,025 of the 1,644 burned 375 m cells were hot in the night passes (08:48–10:30Z), and 619 became non-burnable.
- **Caveats:**
  - Many hot cells are smouldering interior rather than fronts. They're harmless here because non-burnable cells surround them, but they inflate the predicted heat.
  - A 375 m hot cell ignites all 100 of its 37.5 m model cells, so the front starts up to ~190 m outward of where it really is.
  - A quiet edge stays non-burnable for the whole 24 h, even if it would have rekindled.
  - Results are pending (entry 5).

### 5. Multi-day evaluation: four 24 h forecasts, scored within their horizon
- **Setup:** issue times at 12Z on Aug 2, 3, 4 and 5 (before each burning period). Each forecast uses its issue time's HRRR run and starts from the burning edges (entry 4).
- **Scored on:**
  - Overpasses in (issue, issue + 24 h]: 7, 5, 6 and 3 per day. Scoring a 24 h forecast on later overpasses would count its silence after +24 h as misses, which an early pass did.
  - The FEDS perimeter at +24 h.
- **Comparison runs:** no growth (the area burned at issue, persisted), and ELMFIRE started from the whole burned area (the entry 2 start) at the same settings.
- **Live herbaceous moisture:** 90% everywhere below, except where noted.

| Run | CSI Aug 2 | Aug 3 | Aug 4 | Aug 5 | Mean CSI | IoU at +24 h (Aug 2 / 3 / 4 / 5) |
|---|---|---|---|---|---|---|
| No growth | 0 | 0 | 0 | 0 | 0 | 0.59 / 0.77 / 0.86 / 0.89 |
| Whole burned area, ×0.1 | 0.30 | 0.14 | 0.18 | 0.10 | 0.18 | 0.65 / 0.69 / 0.77 / 0.78 |
| Burning edges, ×0.02 | 0.33 | 0.28 | 0.36 | 0.30 | 0.32 | 0.66 / 0.79 / 0.89 / 0.90 |
| **Burning edges, ×0.05** | 0.31 | 0.34 | 0.45 | 0.32 | **0.35** | 0.65 / 0.80 / 0.89 / 0.90 |
| Burning edges, ×0.1 | 0.31 | 0.34 | 0.44 | 0.29 | 0.35 | 0.64 / 0.80 / 0.90 / 0.91 |
| Burning edges, ×0.2 | 0.26 | 0.32 | 0.44 | 0.26 | 0.32 | 0.61 / 0.82 / 0.91 / 0.91 |
| Burning edges, ×0.2, live herbaceous moisture 30% | 0.22 | 0.29 | 0.40 | 0.26 | 0.29 | 0.54 / 0.82 / 0.91 / 0.91 |
| Burning edges, ×0.5 | 0.18 | 0.21 | 0.27 | 0.18 | 0.21 | 0.52 / 0.82 / 0.88 / 0.87 |
| Burning edges, ×1.0 | 0.12 | 0.11 | 0.11 | 0.10 | 0.11 | 0.41 / 0.76 / 0.77 / 0.76 |

- **Out-of-sample score:**
  - Choose the spread multiplier on three days, by mean CSI, and score it on the fourth. Repeat for each day.
  - The choice is ×0.05 every time.
  - Held-out CSI: 0.31, 0.34, 0.45 and 0.32, mean **0.36**. This is the first honest number for plain ELMFIRE (model A) on this fire.
  - The no-growth baseline scores 0, and the ellipse baseline 0.30, but the ellipse was scored only on Aug 2 and was itself tuned on it.
- **Burning edges beat the whole-burned-area start:**
  - On Aug 3–5, CSI 0.34 / 0.45 / 0.32 against 0.14 / 0.18 / 0.10.
  - On Aug 2 the two are equal, when most of the edge was hot anyway.
  - The whole-area start overpredicts area by 18–25% at +24 h, the edge start by −2% to −12%.
- **The slowdown is still there:**
  - The best multiplier is still ×0.05–0.1. Starting from burning edges did not remove the 10–20x slowdown, so the leading hypothesis of entry 3 explains the overprediction on Aug 3–5, but not the slow spread.
  - Remaining suspects:
    - the 375 m hot cells igniting whole blocks, which puts the front too far out;
    - HRRR wind too strong at 20 ft over this terrain;
    - the crude dead-moisture lags, and constant live moisture (wetter is better everywhere tried, so cured-grass assumptions may be wrong);
    - fuel model mapping;
    - suppression, which isn't modelled.
- **Area versus hot cells:** IoU at +24 h is 0.80–0.91 on Aug 3–5, but no growth already reaches 0.77–0.89 there. The gain shows mostly in CSI, i.e. where the fire is burning, not in total area.
- **Runtime:** with each run allowed on any core (`mpirun --bind-to none`), 32 forecasts took 8.7 min on 16 cores (~2.6 CPU-min each).

## 2026-11-15 (event day 2)

### 6. Leak-free initial state, and what it exposed
- **The leak:** the fire-state raster gives cells that were never seen hot an arrival halfway between FEDS snapshots, and that uses the snapshot after the cell entered the perimeter. A forecast issued between snapshots therefore knew part of the next perimeter. It barely matters at 12Z issues (the 12Z snapshot is known then), but it would hand model B's mid-afternoon restarts the coming 00Z perimeter.
- **Change:** with OBSERVED given, the area burned at issue is rebuilt from what was known then only: every cell seen hot by issue, plus the latest FEDS perimeter at or before issue.
- **First version, and why it failed:**
  - It made everything in that perimeter non-burnable except the hot cells. CSI collapsed: within-horizon pooled CSI 0.06–0.09, against 0.3–0.45 with the old start.
  - Cause: FEDS perimeters are drawn around detection footprints, so they include the actively burning zone. Cells inside the 12Z perimeter but still burning that afternoon could no longer burn. Aug 4: 39–43 predicted detections per afternoon pass against 92–131 observed (the old start predicted ~110).
  - The old start avoided this only through the leak: its state gave those cells a post-issue arrival from their later detections, so they stayed fuel.
  - The scoring was checked and is not the cause: the same run scores identically against the Aug 5 and Aug 6 observation files.
- **Fix:** burned cells within ACTIVE_BUFFER = 2 state cells (750 m) of a cell hot in the 6 h before issue keep their fuel. Only burned cells farther from recent heat become non-burnable. At Aug 4 12Z: 367 hot cells, and 2,360 burned cells made non-burnable.

### 7. Can weather corrections replace the spread multiplier? (diagnosis, first leak-free version)
- **Sweep:** at spread ×1.0, wind ×0.5, ×0.7 or ×1.0, against dead fuel moisture +0, +3 or +6 percentage points. Four 12Z issues, pooled CSI within each 24 h horizon. These used the flawed start of entry 6, so absolute scores are low and only the ranking means anything.
- **Result:**
  - Wind ×0.5 with moisture +6 ties spread ×0.1 (pooled CSI 0.090 each), ahead of ×0.05 (0.077) and the uncorrected run (0.057).
  - Wetter beats drier at every wind tried, and lower wind beats higher wind at every moisture.
  - So about half the wind with dead fuels ~6 points wetter (1-h ≈ 8–10% instead of 2.5–4.5%) does what the arbitrary ×0.1 does.
- **Interpretation:** the slowdown may be physical rather than arbitrary:
  - HRRR's 20-ft wind may be too strong over this terrain.
  - The Simard EMC estimate is the equilibrium moisture of the air at 2 m, while real 1-h fuels lag, are shaded, and recover overnight.
  - These are hypotheses, not yet tested against a weather station.

### 8. Model B (updated at a satellite pass) against model A, first result
- **Setup:** four 24 h forecast windows from 12Z on Aug 2–5, all with the entry 6 fix (leak-free start, 750 m buffer).
- **Model A:** plain ELMFIRE from 12Z. Its spread multiplier is chosen by leaving one window out: best pooled CSI over the other three windows' full horizons.
- **Update time:** the first overpass after issue + 1 h of data latency, rounded up to the hour. That is 21Z on every day (first passes 19:18–19:54Z).
- **Model B:** a new ELMFIRE run at the update time, from the burning edges and burned area known then: hot cells of passes in the previous 6 h, plus the 12Z FEDS perimeter. Same HRRR 12Z run, from forecast hour 9.
- **Scored on:** only the overpasses after the update and within A's horizon, 3–4 per window and 14 in all. Mostly night passes (Aug 3–6, 08–10Z) plus late afternoon passes. Pooled CSI = Σhits / Σ(hits + misses + false alarms).
- **A's spread multiplier:** members 0.01, 0.02, 0.03, 0.05, 0.1, 0.2 and 0.35 (LH 90%). The optimum is now inside the range: pooled CSI over full horizons is 0.263 at ×0.02, 0.257 at ×0.03, 0.195 at ×0.01 and 0.172 at ×0.35. Leaving one window out chooses ×0.02 for every window.
- **Buffer width:** at ×0.1, 1 cell gives 0.243, 2 cells 0.222 and 3 cells 0.219. Not re-tuned.

Results on the 14 passes after the update:

| Run | Aug 2 | Aug 3 | Aug 4 | Aug 5 | Pooled CSI | Hits / misses / false alarms |
|---|---|---|---|---|---|---|
| A (×0.02, issued 12Z) | 0.255 | 0.096 | 0.341 | 0.325 | 0.255 | 1197 / 2031 / 1475 |
| **B: restart at 21Z, A's setting** | 0.249 | 0.209 | 0.430 | 0.413 | **0.305** | 1187 / 2041 / 666 |
| B: restart and re-pick the setting from passes in (issue, update] | 0.249 | 0.104 | 0.272 | 0.413 | 0.261 | |
| No growth from the 21Z state | 0.122 | 0.000 | 0.118 | 0.000 | 0.079 | 259 / 2969 / 58 |

- **The gain is from the state update, not the parameters:**
  - Restarting from the latest burning edges cuts false alarms by 55% (1,475 → 666) at the same hits.
  - Re-picking the multiplier from one afternoon pass is noisy: it chose ×0.01 on Aug 3 and 4 and lost most of the gain.
- **Honesty notes:**
  - The restart-only variant was chosen after seeing both. Both are reported.
  - n = 14 passes, one fire, so this is a case study.
  - The earlier B run with members ×0.03–0.35 only (before ×0.01–0.02 were added) gave B 0.343 against A 0.265. Its multipliers were all the slowest available, which motivated adding the slower ones.
- **Remaining biases:**
  - Both A and B miss ~2,000 observed hot cells, mostly on night passes where the observed heat is smouldering in the interior, which a front-only spread model doesn't produce.
  - The no-growth baseline uses the state raster's guessed arrivals (a slight look-ahead), which makes it stronger, not weaker.
  - Cloud and smoke aren't yet accounted for: predicted fire under cloud counts as a false alarm (entry 9).

### 9. Cloud-masked scoring from the VIIRS fire mask
- **Change:** scores now leave out cells the satellite couldn't see on that pass. A predicted fire under cloud is neither a hit nor a false alarm.
- **Where the mask comes from:**
  - The VIIRS active fire product's per-pixel fire mask (0 not processed, 1 bow-tie deleted, 2 glint, 3 water, 4 cloud, 5 land, 6 unclassified, 7–9 fire), placed with the matching 03IMG geolocation granule. The fire product geolocates only its fire pixels.
  - A 375 m cell is obscured on a pass if it held pixels and none was clear (no 3, 5 or 7–9). A cell held only by bow-tie-deleted pixels doesn't count.
  - Scripts: `scripts/obscured_cells.py`, then `heat_frames.py`.
- **A shortcut that failed:** geolocating the mask by fitting line/sample → lat/lon to the fire pixels, either across the granule (2–35 km error) or locally (0.1–5 km), is not accurate enough for 375 m cells. The real geolocation granules are needed: 33 for this fire, 5.3 GB.
- **Over Widemouth 2:** Aug 3–5 were nearly clear. Aug 2 afternoon passes had 4–9% of observed cells obscured, Aug 6 afternoon up to 20%. Each pass uses only the granule that held the fire, so cells in a neighbouring granule stay in the score (conservative).
- **Effect:** small. On the 14 passes after B's update, 14 of A's and 16 of B's predicted hot cells fell under cloud.
  - Pooled CSI: A 0.255 (unchanged), B 0.306 (0.305 unmasked).
  - A's full-horizon pooled CSI by multiplier rises slightly, and ×0.02 stays best (0.268).
  - The A < B result is not a cloud artifact on this fire. Smoke isn't flagged as cloud by the mask, so smoke-obscured fire is still not accounted for.

### 12. Does harmonizing change a decision? Flint Hills burning before and after the 2010 smoke plan
- **Question:** KDHE adopted the Flint Hills Smoke Management Plan on 2010-12-29, after April 2009 smoke contributed to ozone exceedances in Kansas City and Wichita, and is revising it in 2025–26. Did spring (Feb–Apr) burning change after it? The plan's first season (2011) falls one year before VIIRS (2012), so a raw MODIS-then-VIIRS calendar mixes the policy with the sensor change.
- **Data:** FIRMS yearly US country archives, no key (`data/raw/firms-country/`, 814 MB): MODIS C6.1 (Terra + Aqua) 2001–2025 and VIIRS S-NPP 375 m 2012–2025, type 0 only. These are FIRMS point detections, not the 1 km gridded products of entry 10, so ratios differ from it (here VIIRS sees 2.38x the MODIS fire cell-days nationally).
- **Measure:** fire cell-days on the 0.1° grid, per month, in a Flint Hills box (36.9–39.3° N, 97.2–96.0° W), with Southeast (29.5–33.5° N, 86–81° W), Sierra (35.5–41° N, 122.5–118.5° W) and lower 48 for contrast. Script `scripts/firms_region_check.py`.
- **Harmonization used here (calendar level, not entry 10's model):** VIIRS-equivalent = MODIS × r(region, calendar month), r fitted on the overlap. Scored leave-one-year-out on 2012–2025 monthly totals (sum |error| / sum VIIRS):

  | Region | MODIS as-is | One national factor | Regional monthly factor | VIIRS / MODIS in region |
  |---|---|---|---|---|
  | Flint Hills | 0.36 | 0.60 | **0.14** | 1.55 |
  | Southeast | 0.47 | 0.29 | **0.12** | 1.88 |
  | Sierra | 0.55 | 0.23 | **0.17** | 2.19 |
  | Lower 48 | 0.58 | 0.15 | **0.11** | 2.38 |

  A single national factor is worse than no correction in the Flint Hills (VIIRS/MODIS there is 1.55, nationally 2.38).
- **Result (spring fire cell-days per year, mean):**

  | Calendar | 2003–2010 | 2013–2025 | Change | Trend 2003–2025 |
  |---|---|---|---|---|
  | Raw splice (MODIS to 2011, VIIRS from 2012) | 857 | 1,092 | **+27%** | +18 per year |
  | MODIS only (one sensor throughout) | 857 | 763 | **−11%** | −3 per year |
  | Harmonized (regional monthly factor) | 1,227 | 1,092 | **−11%** | −5 per year |

  - With 2011 counted as after (2011–2025): splice +20%, MODIS only −14%, harmonized −14%.
  - The raw splice reverses the sign: it says burning rose after the plan; the consistent records say it fell.
  - April's share of spring burning fell from 0.85 (2003–2010) to 0.70 (2011–2025) in every calendar; share is not affected by the sensor change.
  - Robust to dropping low-confidence detections (VIIRS "l", MODIS < 30): splice +18%, MODIS only −14%, harmonized −14%.
- **Caveats:**
  - A monthly ratio rescales MODIS, so the harmonized before/after change equals MODIS-only's by construction. What harmonization adds is the level (about 1.4x more burning than MODIS sees, on VIIRS's footing) and a calendar continuous through 2012; the evidence against the splice is that a single sensor run through the whole period agrees with it.
  - The pre-2012 factor assumes the region's VIIRS/MODIS ratio stayed constant (untestable before 2012).
  - Before/after is not causal: 2012–2013 were very low years (cause not checked), and burning depends on weather and grazing economics.
  - Entry 10's p_viirs should be run on the same box to confirm with the cell-day model.
  - S-NPP stopped delivering data in November 2026 (reported by other teams; not checked), so the VIIRS reference needs NOAA-20 continuation.
