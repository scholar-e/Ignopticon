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

### 5. Multi-day evaluation set (pending)
- **Setup:** issue times at 12Z on Aug 2, 3, 4 and 5 (before each burning period), with 24 h forecasts. Each is scored on the afternoon and next-night overpasses and on the FEDS perimeters at +12 h and +24 h.
- **Sweep:** spread multiplier 0.1, 0.2, 0.35, 0.5, 0.75 or 1.0 with LH 90%, plus 0.2 and 0.5 with LH 30%. That's 32 runs, each with its issue time's HRRR run.
- **Plan:** choose settings on some days and test on the others, which is the first honest (out-of-sample) score of A.
- **Runtime note:** earlier sweeps ran with every run pinned to one CPU core (Open MPI's default binding), so their timings understate the speed. The ELMFIRE results themselves are unaffected.
