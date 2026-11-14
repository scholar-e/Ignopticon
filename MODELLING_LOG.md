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
