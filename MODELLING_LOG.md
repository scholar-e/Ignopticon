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

### 10. MODIS/VIIRS harmonization, first fit
- **Question per 0.1° cell-day:** would VIIRS S-NPP (VNP14A1) have detected fire, given what MODIS Terra + Aqua (MOD14A1 + MYD14A1) saw? The answer, `p_viirs`, puts 2000–2011 on VIIRS's footing (PLAN.md M7, `scripts/harmonize.py`).
- **Label:** VIIRS saw ≥ 1 fire pixel in the cell that day. Only cell-days VIIRS saw clearly count, so cloud is never read as "no fire". `p_viirs` is therefore conditional on a clear VIIRS look.
- **Features (MODIS only):**
  - fire pixels, nominal/high-confidence fire pixels, summed MaxFRP and clear looks;
  - fire and FRP summed over the 3×3 cells;
  - fire the day before and the day after, and their 3×3 sums;
  - days since MODIS fire within the 3×3 (capped at 30);
  - MODIS-clear flag, month, latitude and longitude.
  - `elev` adds elevation and roughness. `all` adds gridMET wind, u, v, VPD, ERC, FM100 and precipitation.
  - "Next day" features need the day after, so this is a retrospective record, not a real-time one. Without them (previous day only), 2013 held-out log loss was 0.00399 vs 0.00414 on a 3-year sample, so they aren't what carries the skill.
- **Model:** boosted trees (HistGradientBoosting). Kept: every cell-day with MODIS or VIIRS fire within one cell and one day, plus 1% of the rest, weighted ×100.
- **Evaluation:** every third overlap year is held out whole. Two baselines:
  - MODIS fire as-is (p = 1 if MODIS saw fire);
  - per-month logistic rescaling of log(1 + MODIS fire pixels) plus the clear flag.
- **Bugs fixed before the first run:**
  - The original settings (rate 0.08, 63 leaves, early stopping) diverged on a 0.1%-positive problem: 10 trees, held-out AUC 0.52 (base) / 0.89 (elev), and elev expected 75,872 fire cell-days against 43,799.
  - Rate 0.03 with L2 = 1 ran, but gave p ≈ 0.99 where VIIRS saw fire 71–76% of the time, in the training years as well. Newton steps from p ≈ 0.001 overshoot.
  - Fixed with L2 = 50, 600 trees and no early stopping. These settings were chosen on 2017–2018 as validation, with 2012/14/15/20/21 as training (no test year used).
  - `apply` built a 56 M × 15 float32 matrix per year (~3.5 GB). It now predicts 16 days at a time.
  - `apply` now writes annual totals both over all cell-days and over clear looks only. Its `observed` field (0 none, 1 MODIS-clear, 2 VIIRS-clear) was right.
  - The fit now saves the held-out model (`model_holdout.pkl`) before refitting on all years for `apply`.
  - The 3×3 sum replaced `uniform_filter` with exact shifted sums. On checking, `uniform_filter` had no residue on these data, so this was a no-op.

Held-out results, 13 overlap years 2012–2024: train on 9 years, test on 2013, 2016, 2019 and 2022 (sampled cell-days, weighted back up; observed 183,677 VIIRS fire cell-days):

| Model | Brier | Log loss | AUC | Expected fire cell-days |
|---|---|---|---|---|
| MODIS fire as-is | 0.001255 | 0.01733 | 0.739 | 235,121 |
| MODIS rescaled (logistic) | 0.000809 | 0.00543 | 0.783 | 180,007 |
| Harmonized, base | 0.000709 | 0.00393 | 0.961 | 179,844 |
| Harmonized, elev | 0.000707 | 0.00389 | 0.963 | 179,667 |
| Harmonized, all (weather) | 0.000707 | 0.00386 | 0.966 | 178,762 |

- **First fit, 3 years** (train 2012 + 2014, test 2013, final settings; observed 43,799):
  - rescaled: Brier 0.000802, log loss 0.00546, AUC 0.772, expected 47,263;
  - base: 0.000671, 0.0037, 0.961, 43,771;
  - elev: 0.000669, 0.0037, 0.964, 43,723;
  - all: 0.000670, 0.0037, 0.965, 42,900.
- **Reliability (13-year base, held out; mean p → observed rate per bin):** 0.145→0.142, 0.249→0.239, 0.349→0.349, 0.447→0.455, 0.546→0.560, 0.647→0.662, 0.747→0.774, 0.845→0.864, 0.963→0.960. Calibrated.
  - The rescaling baseline is not: 0.976→0.704 and 0.168→0.363.
  - MODIS as-is: a MODIS fire cell-day is a VIIRS fire cell-day only 37% of the time.
- **Annual totals of held-out years (base vs observed):**
  - 2013: 43,877 vs 43,799.
  - 2016: 49,435 vs 49,729.
  - 2019: 41,972 vs 41,912.
  - 2022: 44,558 vs 48,237 (−7.6%). The rescaling gives 44,494, so 2022 had more VIIRS-only fire than MODIS implies.
- **Where the skill comes from** (2013 held out, 3-year sample, trees with the same settings):

  | Features | Log loss | AUC |
  |---|---|---|
  | fire, month, clear flag (a flexible rescaling) | 0.00513 | 0.767 |
  | + same-cell FRP and confidence | 0.00477 | 0.852 |
  | + lat/lon | 0.00421 | 0.936 |
  | + previous day | 0.00399 | 0.952 |

  Location is the largest single gain: VIIRS-only small fires (agricultural burning, the Southeast) recur in the same places.
- **Small fires (VIIRS fire, no MODIS fire in the cell; 2013 held out, full grid):**
  - 22,861 such cell-days among 48.8 M. The model puts 22,625 expected fire cell-days on MODIS-no-fire cells in total, so the count is right, but only 407 (1.8%) lands on the actual cells.
  - Ranking: AUC 0.92. The top-N cells hold 8% of the hits, the top-10N 27%.
  - So small fires are recovered as a calibrated, spread-out probability in the right regions and seasons, not as located detections. Daily maps of `p_viirs` without MODIS fire are a smeared likelihood. Totals over regions and months are what is trustworthy.
  - Where MODIS saw fire in an adjacent cell or day, it recovers 8,913 expected against 9,247 observed. The rescaling gets 527.
- **Elevation and weather:** each helps by under 1% of log loss on held-out years (13-year: 0.00393 → 0.00389 → 0.00386).
  - Weather is not learning a strong fire-weather signal where MODIS saw nothing. In the "no MODIS fire near" stratum (2013 held out), the expected count with weather is 13,328 vs 13,760 without (observed 13,614). corr(log p, ERC) is −0.04 to −0.09 for all sets.
  - By ERC class, weather moves expectations towards observed (ERC 0–20: elev 2,135, all 1,388, observed 1,375).
  - Even so, a harmonized sensor record shouldn't depend on weather, and the gain is tiny. **Ship base.**
- **Seam at 2012** (base, 13-year model; expected fire cell-days on MODIS-clear cell-days against raw MODIS fire cell-days):

  | Year | 2003 | 2005 | 2007 | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 |
  |---|---|---|---|---|---|---|---|---|---|
  | Harmonized (expected) | 47,779 | 51,429 | 54,616 | 45,046 | 48,549 | 58,000 | 54,159 | 44,377 | 44,535 |
  | Raw MODIS | 61,154 | 68,754 | 71,628 | 58,313 | 63,664 | 74,565 | 69,070 | 56,033 | 57,233 |
  | Observed VIIRS (clear looks) | | | | | | | 50,294 | 43,799 | 44,443 |

  - On VIIRS-clear cell-days the expected counts are 51,164 (2012), 44,165 (2013) and 44,227 (2014). These overlap years are in the model's training data (in-sample).
  - Harmonized / raw MODIS is 0.75–0.80 in every year from 2003 to 2013, so there is no step at 2012 by construction. At annual national scale the harmonized series is nearly a constant rescaling of MODIS fire cell-days (about ×0.78); the model's value is in where and when, not the annual total.
  - Raw MODIS overstates VIIRS fire cell-days by ~27% in every year, because MODIS fire pixels smear across 0.1° cells (below).
- **Rim Fire check: why MODIS has more fire pixels than VIIRS in big fires.**
  - At col 52, row 121 on 2013-08-28: Aqua 51 fire pixels and 20.4 GW summed MaxFRP, Terra 55, VIIRS 39 and 8.4 GW.
  - Nationally per fire cell-day: MODIS 3.4–3.6 pixels, VIIRS 2.0. Per fire pixel: MODIS 77–88 MW, VIIRS 49 MW.
  - Not a gridding bug, as far as checked: the clear counts agree (114 per cell for every product), and the 0.1° cells hold up to 132 pixels (144·cos lat).
  - The likely cause is the L3 gridding of the products. MOD14A1 maps each swath pixel, which grows off-nadir to about 2 × 5 km, onto every 1 km grid cell it covers, so one large hot pixel paints several cells. Each of those cells also carries the same MaxFRP, so summed MaxFRP double counts.
  - VIIRS pixels grow much less across the scan.
  - VNP14A1 C2 may come from the 750 m M-band fire product rather than 375 m. Its InputPointer is VNP14GD/VNP14GN, which needs checking against the user guide. docs/DATASETS.md says "from 375 m".
  - So "fire pixels" and summed FRP are not comparable across sensors for large fires. Fire cell-days (presence) are. That is why the label is presence, not counts.
- **Caveats:**
  - Each year is built separately, so "previous day" and "days since fire" restart on Jan 1.
  - 2012 VIIRS starts on Jan 19.
  - The weather sets used gridMET for all years.
  - `p_viirs` sums over every cell-day include cloudy ones, where VIIRS would have counted nothing. Use the clear-look sums for comparisons.
  - VJ114A1 (NOAA-20) is gridded but unused. The model targets S-NPP only.
  - VNP14A1 2025–2026 were gridded after these fits and are not used yet. The model for `apply` is refitted on all of 2012–2024; the held-out scores come from the fit without 2013, 2016, 2019 and 2022.


### 11. National next-day fire activity model
- **Question:** for each 0.1° cell-day t in the lower 48, will VIIRS S-NPP (VNP14A1) see fire in the cell on day t+1?
  - Only cell-days S-NPP sees clearly on t+1 (and land cells) count. Cloud is not "no fire", so the probability assumes S-NPP gets a clear look on t+1.
  - **New-activity sub-target:** the same label, restricted to cells with no fire from any sensor (S-NPP, NOAA-20, Terra, Aqua) in their 3×3 neighbourhood on days t-2..t. That is spread into new cells, or new ignitions.
  - 57% of positives are new activity: most VIIRS fire cell-days at this scale are fires that last a single day, such as agricultural or prescribed burns.
  - Persistence is weak. Of cells with VIIRS fire on t, 13% have fire on t+1; for cells with 10 or more fire pixels on t, it's 69%.
- **Script:** `scripts/activity_model.py` (`build`, then `train`). Outputs are in `data/harmonize/activity/` (`results.md`, `results.json`, models, test predictions).
- **Data:**
  - VNP14A1 2012–2024 (2025–26 weren't gridded yet), VJ114A1 2018–2024, MOD14A1 and MYD14A1 for the same years.
  - gridMET weather, elevation.npz, and fuel.npz (7 fuel-group fractions, canopy cc/ch/cbh/cbd, FBFM40 mode, valid fraction).
- **Features (91):**
  - **Hotspots:** each sensor's fire pixels, FRP and clear looks on t, t-1 and t-2 (NaN before the sensor existed). VIIRS and MODIS fire summed over the 3×3 and 5×5 on each of those days. Days since any sensor saw fire in the 3×3.
  - **Weather:** gridMET on t and t+1. Upwind-neighbour fire (fire in the 8 neighbours, weighted by the cosine of the wind direction).
  - **Static and calendar:** elevation, fuel, day of year, month, lat/lon.
  - **Weather on t+1 is perfect-forecast weather:** the observed gridMET stands in for a next-day forecast, so those rows overstate what an operational model, which would use a forecast, can do. The "day-t weather only" row has no look-ahead.
- **Sampling and weights:**
  - Every clear cell-day with fire from any sensor in its 5×5 on t-2..t, or with a positive label, plus 0.5% of the rest, weighted ×200.
  - For fitting only, 30% of the near-fire negatives are kept, weighted ×1/0.3. Test rows are never thinned.
  - Metrics are weighted, so they estimate the whole population of clear land cell-days.
- **Split by whole years:**
  - Train: 2012–15, 2017, 2019, 2021, 2022, 2024 (8.8M rows).
  - 2018 is used for early stopping only.
  - **Test, held out: 2016, 2020, 2023** (9.35M rows).
  - Every number below is on the held-out test years. The feature-set choices (which ablation rows to read as "best") were made after seeing them.
- **Model:** LightGBM (63 leaves, learning rate 0.05, early stopping on validation log loss). Baselines are mapped to probabilities by their training-year frequencies.

Held-out test years. AP = average precision; "new" = new-activity cell-days (base rate 0.00084).

| Model | AUC | AP | Brier | AUC new | AP new |
|---|---|---|---|---|---|
| Persistence (VIIRS fire on t) | 0.585 | 0.024 | 0.001493 | 0.500 | 0.0008 |
| Persistence + neighbourhood | 0.746 | 0.028 | 0.001492 | 0.552 | 0.0013 |
| Logistic regression, all features | 0.881 | 0.105 | 0.001441 | 0.816 | 0.0060 |
| LightGBM: hotspots (+ calendar, lat/lon) | 0.897 | 0.111 | 0.001436 | 0.853 | 0.0095 |
| + weather t | 0.902 | 0.117 | 0.001432 | 0.861 | 0.0127 |
| + weather t+1 (perfect forecast) | 0.910 | 0.126 | 0.001425 | 0.872 | 0.0143 |
| + elevation | 0.904 | 0.118 | 0.001438 | 0.875 | 0.0136 |
| **+ fuel (all features)** | **0.919** | **0.132** | 0.001420 | **0.887** | **0.0179** |
| All, day-t weather only (no look-ahead) | 0.911 | 0.124 | 0.001429 | 0.879 | 0.0169 |
| LightGBM fitted on new-activity rows only | | | | 0.885 | 0.0161 |

- **AP for the full model by test year:** 2016 0.106, 2020 0.185, 2023 0.098 (persistence: 0.019, 0.037, 0.017). It beats persistence in every year.
- **It beats persistence about 5 times over on AP.** On new activity, persistence can't predict at all by definition, and the model's AP is about 21 times the base rate.
  - Absolute skill on new activity is still low. At P ≥ 0.2, new-activity precision is 0.25 but recall is 0.7%. At P ≥ 0.05, precision is 0.08 and recall 3.4%.
- **The operational-style model already gets most of the gain.**
  - The all-features model with day-t weather only reaches AP 0.124 and new AP 0.0169.
  - Perfect-forecast t+1 weather adds +0.008 AP and +0.001 new AP on top of it.
- **Calibration:** good below P = 0.2. Above it the model is overconfident: predicted 0.38, 0.58 and 0.80 against observed 0.29, 0.42 and 0.48.
- **Permutation importance** (full model, 600k test rows, drop in AP when a group is shuffled; all / new):
  - S-NPP cell history (t..t-2): 0.091 / 0.013. Its FRP on t alone: 0.083.
  - Weather t+1: 0.066 / 0.008, mostly ERC and VPD.
  - Elevation: 0.041 / 0.008.
  - Fuel: 0.036 / 0.013.
  - Days since fire: 0.021 / 0.009.
  - Lat/lon: 0.014 / 0.012.
  - Day of year: 0.014 / 0.007.
  - VIIRS neighbourhood: 0.015 / 0.001.
  - NOAA-20: 0.011 / 0.004.
  - Weather t: 0.007 / 0.003. It overlaps with t+1.
  - MODIS Terra and Aqua (cell and neighbourhood): 0.002–0.006 / ~0. MODIS adds almost nothing once VIIRS is in.
  - Upwind fire: ~0.
- **Caveats:**
  - One fit (one seed) per row. The step from + weather t+1 to + elevation went down (0.126 → 0.118), which is within what I'd expect from fit-to-fit variation plus location memorisation. Read differences under ~0.01 AP as noise. The fuel gain (+0.014 AP, +0.004 new AP over + elevation) is larger.
  - Static features (elevation, fuel, lat/lon) partly act as "where fires usually happen" priors, not physics.
  - The hyperparameters are not tuned.
  - Upwind fire has no measurable value at 0.1° daily. Spread across a 10 km cell boundary in a day is rare compared with new single-day fires.
- **GPU / Tillicum:** not prepared and not recommended. The trees get most of the signal, logistic regression gets 80% of the tree's AP, and the hand-made spatial features (neighbourhood, upwind) add little. A patch CNN would mostly re-learn those same features. The remaining error is dominated by new small fires that these layers can't foresee.

### 11a. Many-fire ELMFIRE pilot (in-sample; the full ensemble is shelved)
- **Fire catalog** (`scripts/fire_catalog.py`, `data/training/catalog.csv`):
  - Large multi-day events 2014–2025 from VIIRS S-NPP fire cell-days on the 0.1° grid: touching cells, gaps up to 2 days.
  - Persistent industrial/agricultural heat removed first: cells burning in ≥ 20 weeks overall or ≥ 8 weeks in one year.
  - An event needs ≥ 4 fire days, ≥ 20 cell-days, a peak day of ≥ 30 pixels, and < 50% non-burnable fuel.
  - Result: 365 events, 342 in the US. 109 US events in 2021–2024 form the evaluation set of docs/COMPARISON.md.
  - It finds the known large fires (Camp, Carr, August Complex, Creek, Dixie, Caldor, Bootleg, Hermits Peak, Smokehouse Creek, Park). Cameron Peak 2020 is split in two, and York 2023 is missed.
- **Per-fire pipeline:** `scripts/build_fire_case.py` (VIIRS 375 m granules, observations, LANDFIRE, HRRR 12Z). `fire_observations.py` now falls back to the FEDS archive collection (2018–2021), else to detections only.
- **Spatial spread multipliers:** `elmfire_case.py` gains per-fuel-group multipliers (`ADJ_GR`, `ADJ_SH`, `ADJ_TIMBER`) and a wind term (`ADJ_WIND_EXP`, `ADJ_WIND_REF`). Existing cases still build byte-identically.
- **Pilot:** 330 runs on three fires (OR timber litter 2020-040, AZ grass 2021-038, CA shrub 2021-031), 3 issue times each, 35–41 settings per issue. In-sample, no cloud masks.
  - **Best uniform multiplier:** 0.5–1.0 in 8 of 9 issues. Best pooled CSI 0.43 / 0.29 / 0.27. No growth scores 0.
  - **Damping wind helps:** damping the spread in windy cells beat amplifying it on 2 of 3 fires, consistent with entry 7's suspicion that HRRR wind is too strong.
  - **Fuel groups:** raising all groups together was best. On the shrub fire, lowering shrub or timber cost more than lowering grass.
- **Open question:** these best multipliers are 10–50x Widemouth's 0.02–0.05. Widemouth used NOAA-21, its fire band and FEDS live perimeters; the pilot used S-NPP and NOAA-20 detections only. So the gap may come from the pipeline rather than fuel or weather. Running Widemouth through `build_fire_case.py` would settle it.
- **Status:** the full-scale run was estimated at about 63k runs, roughly $60–120 of Tillicum time. It was not run: the user chose to use Tillicum for training on data, not for ELMFIRE prediction runs.

### 12. Raw versus harmonized next-day activity comparison (2026-11-14, resumed)
- Recovered Claude's interrupted experiment: five arms (raw-pre, harm-pre,
  raw-all, harm-all, viirs-only), training 2003–2011 and/or 2012–2019;
  validation 2020, evaluation 2021–2024. Strict harmonization was fitted
  on 2012–2020 and applied to 2003–2011. See docs/COMPARISON.md.
- Nine U-Net seed runs completed on Tillicum (471814); six harmonized runs
  await their newly completed inputs. Same settings for every arm: seeds
  0/1/2, 16 epochs, 500 steps/epoch, batch 128, patience 16. Mean of the
  three calibrated probabilities; threshold selected on 2020 only.
- Fixed ensemble CSI threshold selection to include all equal probabilities
  and select on the stored float16 probabilities. Reject missing seeds,
  mismatched dates/arms, invalid shapes, and nonfinite/out-of-range values.
  A tied-probability regression check passed (CSI 2/3).
- Caveats: prior Claude notes inspected first-round test results before
  choosing the three-seed second round. Thus test years were excluded from
  fitting, but this is exploratory evaluation, not an untouched confirmatory
  test. Weather on t+1 is observed gridMET (perfect-forecast assumption).
  New activity in this comparison means no S-NPP fire in the 3×3 on t−2..t,
  not absence across all sensors. This predicts coarse daily activity,
  not a fire perimeter. Results are pending; no superiority claim yet.

### 12a. First daily GBM export (2026-11-14)
- Exported all five trained arms for target 2026-08-02 using S-NPP inputs
  through 2026-08-01, with the same 44 feature names/order and float16 input
  representation as training. No refitting. Script: scripts/compare_day.py.
- Output: data/compare/daily/2026-08-02/{predictions.npz,index.json,gbm_*.png}.
  Grid 260×590 at 0.1°, 84,722 land cells. Checked feature order, input-day
  coverage, finite weather, and finite probabilities in [0,1].
- Retrospective activity probabilities conditional on clear S-NPP viewing,
  not a perimeter or an operational forecast: observed Aug 2 gridMET is an
  input. PNG green/blue channels are zero (no observed truth exported),
  red is probability×255, alpha is land. Metadata includes model hashes.

### 12b. GitHub daily prediction sample (2026-11-14)
- Packaged August 2, 2026 in data/samples/predictions using the comparison
  RGBA schema and model_arm/YYYY-MM-DD.png paths. R is float16 probability
  quantized to 8 bits; G/B now include observed target-day S-NPP fire/clear
  flags for retrospective evaluation only. A is land.
- Verified PNG round trips for all five arms (590×260); 82,757 clear land
  cells and 155 observed fire cells. Metadata records model hashes,
  thresholds, input/target dates, grid and the perfect-weather caveat.

### 12c. Completed U-Net comparison (2026-11-14)
- All 15 seed runs succeeded; final six were Slurm job 472421, 8m22s–9m24s
  each. Total recorded Hyak usage $2.25 including earlier setup/pilot jobs.
- Three-seed averaged predictions evaluated on 2021–2024, clear S-NPP land
  cell-days (168,343 positives):

| Arm | AP | AUC | CSI at 2020 threshold |
|---|---|---|---|
| raw-pre | 0.1343 | 0.9137 | 0.1045 |
| harm-pre | 0.0939 | 0.8703 | 0.0871 |
| raw-all | 0.1391 | 0.9181 | 0.1112 |
| harm-all | 0.1376 | 0.9179 | 0.1098 |
| viirs-only | 0.1396 | 0.9175 | 0.1123 |

- This experiment does not show a benefit from harmonization. The pre-only
  harmonized arm scores lower; combined arms and VIIRS-only are close.
  No significance claim: uncertainty across fires/seeds is not estimated.
- Full results and 14-fire visual comparisons: data/compare/results/unet.*
  and data/compare/figures/unet/. Earlier entry 12 caveats apply, especially
  exploratory test inspection and observed target-day weather.

### 12d. Investigation of harmonized-only degradation (2026-11-14)
- Saved-model/sample diagnostics, no retraining: harm-pre GBM has one tree
  (raw-pre 406); on sampled 2020 validation data it emits six probabilities,
  with 99.8489% of inverse-weighted cells at 0.00144317.
- Cached 2009 harm fire_0 is fractional on 100% of weighted rows; raw 2009
  and VIIRS 2020 inputs are binary. Representation mismatch is a leading
  hypothesis, not yet isolated by a controlled experiment.
- Found temporal look-ahead: harmonizer next/next3 uses MODIS t+1, so
  harmonized p(t) training inputs are retrospective. This compromises the
  interpretation as a causal forecasting comparison despite year holdouts.
- U-Net label>0.5 positive centres: harm-pre 137,992 vs raw-pre 520,593 over
  the same 246,752,966 masked cells. Inputs, targets and sampling all change.
- Detailed evidence and proposed controlled tests: docs/COMPARISON_DIAGNOSIS.md.
  Keep the current results exploratory; do not conclude harmonization itself
  is ineffective from these confounded arms.

### 12e. Controlled causal summer pilot started (2026-11-14)
- scripts/compare_controlled.py isolates inputs: raw MODIS, causal harmonized
  MODIS, and VIIRS reference; all predict the same binary next-day VIIRS
  labels on identical sampled rows, with identical weights and downstream
  training settings. A mismatched harm-model/VIIRS-input diagnostic is added.
- Harmonizer fitted on 2012–2014 only, dropping next/next3. Uses LightGBM
  binary loss, 300 trees, rate .03, 31 leaves, L2=50; this is a new diagnostic
  harmonizer, not the original sklearn model. Predictor train years
  2015/2017/2019, validation 2020, July–August only with 30-day history.
  Disjoint stages avoid fitted harmonizer labels leaking into predictor
  training. No 2021–2024 reads. Confirmed complete calendar alignment for
  all 21 satellite input files.
- Population: land with MODIS and VIIRS clear on t, VIIRS clear on t+1.
  Sampling: keep near-fire/positive rows plus 1% background, weighted ×100;
  same rows for every arm. Weather t only. No soft predictor labels or
  arm-specific sampling. Each arm has max 600 trees; early stop on 2020
  log loss, patience 50, saving validation AP and loss curves.
- This narrower seasonal/common-clear population and changed harmonizer
  cannot be directly compared numerically with the earlier national scores.
  Results isolated under data/compare/controlled; existing models untouched.

### 12f. Controlled summer pilot results (2026-11-14; validation only)
- Finished locally, no Hyak spend. 1,469,529 shared predictor training rows;
  505,033 shared validation rows, 6,549 positives. All arms use the same
  inverse-sampling weights. Future-perturbation test passed: changing MODIS
  at t+1 does not change selected causal features through t, while the
  original next feature changes. Existing published models were untouched.

| Inputs | AP | AUC | Log loss | Selected trees |
|---|---|---|---|---|
| raw MODIS | 0.1401 | 0.8224 | 0.009747 | 131 |
| causal harmonized MODIS | 0.1459 | 0.8271 | 0.010314 | 122 |
| harm model given binary VIIRS | 0.1520 | 0.8099 | 0.010621 | same harm model |
| VIIRS reference | 0.1617 | 0.8545 | 0.009377 | 165 |

- Harmonization raises AP by 0.00584 (~4.2% relative) and slightly raises
  AUC, but worsens log loss. No uncertainty estimate or significance claim.
  Harm no longer collapses. These are validation diagnostics on a narrower
  population, not comparable directly to the old national test AP.
- Contrary to the initial leading hypothesis, binary VIIRS input substitution
  does not lower AP here; it lowers AUC and worsens log loss. Representation
  mismatch alone is insufficient to explain the original collapse. Soft
  targets, sampling changes, temporal look-ahead, and fitting choices remain
  candidate causes; this pilot changed several together and cannot identify
  the individual cause.
- Learning curves: validation AP peaks very early (raw round 3, harm round 1,
  VIIRS round 1), whereas log-loss selection picks rounds 131/122/165.
  Choice of ranking versus probability-quality objective matters. Preserve
  current selection rule; do not pick new models from these results silently.
- Artifacts: data/compare/controlled/results.{json,md} and script
  scripts/compare_controlled.py. No 2021–2024 evaluation was performed.

### 13. Return to sub-daily ELMFIRE perimeter forecasting (2026-11-14)
- User prioritized perimeter spread forecasts over coarse daily activity.
  Re-ran Widemouth Aug 2 12Z for 24 h locally with existing ADJ=.02,
  LH_MOISTURE_CONTENT=90, observed active edges, LANDFIRE and HRRR forecast
  weather. New directory perimeter-0802/adj0.02. Arrival times reproduce
  a3-0802/adj0.02 bit-for-bit; no new tuning or training.
- New scripts/elmfire_perimeters.py exports cumulative polygons at +0/6/12/24 h
  on the native 37.5 m grid, retaining disconnected components and holes.
  Initial footprint is rebuilt from hotspots and the latest FEDS polygon
  available at issue, not interpolated state arrival times. Fixed the existing
  growth-band exporter to use that same reconstruction for OBSERVED runs.
- Cumulative acres: 74,988.76 / 75,336.60 / 75,779.65 / 76,583.05;
  growth from issue: 0 / 347.84 / 790.89 / 1,594.29. Includes disconnected
  hotspots; these figures are not just the UI's front-only growth.
- Checked all polygons valid, nested across horizons, and projected area
  equal to raster cell count; fresh and old arrival rasters exactly match.
  Public artifacts: earth/public/data/perimeters/, existing ops growth bands
  refreshed under earth/public/data/forecasts/.
- Caveats: deterministic case-study forecast, no uncertainty band; 37.5 m
  computation is not 37.5 m accuracy. Coarser satellite-derived initialization,
  large fitted spread reduction, and no suppression model limit interpretation.

### 14. August 2 map-wide Hyak perimeter batch (2026-11-14)
- User authorized parallel ELMFIRE work on Hyak and confirmed current Aug 2
  fires first; 342 historical catalog events and older MODIS-era fires later.
- Scope: 76 distinct FEDS IDs from the 00Z/12Z map snapshots. Fixed 12Z issue,
  24 h forecasts. Added --issue (no future-based date selection) and explicit
  FEDS ID matching; use locally archived FEDS snapshots for this batch.
- New worker scripts/perimeter_batch.py and hyak/perimeter_batch.slurm build
  inputs, run 10 sensitivity members, score later available perimeters, and
  export +6/+12/+24 h member-agreement envelopes. Settings and data caveats in
  data/perimeter_batch/README.md. This is an uncalibrated sensitivity study.
- Initial detections associated within 3 km of the issue-known FEDS outline;
  future frames do not influence initialization. No active detections or
  incomplete/truncated domains are flagged instead of inventing predictions.
- Corrected shared-granule download race with per-file filesystem locks.
  Reuse available geolocation for cloud flags; not all fires have cloud masks.
- Metric tests passed: identical perimeters IoU=1 and boundary error=0;
  one-cell displacement yields nonzero error <=37.5 m at p95; no-growth
  versus no-growth has undefined growth IoU rather than fabricated skill.
- Budget: 76 tasks ×45min ×$0.90/h = $51.30 scheduled ceiling, four tasks
  concurrently after Widemouth integration check. No automatic retries.
- Submitted integration 473163_54 and dependent remaining array 473166
  (`0-53,55-75%4`, afterok:473163). Integration failure cancels the dependent
  batch. Site banner reports 55 tasks for this disjoint range; actual requested
  range contains 75. Retain the conservative 76-task $51.30 total ceiling.

### 17. Self-calibration from the fire's recent progression (Widemouth test) (2026-11-14; single case)
- User-approved idea: correct spread per direction from how the model did over the fire's last 12-24 h.
  New scripts/selfcal_adj.py: hindcast ELMFIRE (physics3 inputs, ADJ=1, INIT=feds) from the FEDS perimeter at
  T-24 h, else T-12 h, to T, with HRRR sub-hourly stitched from the hourly runs in the window (f00 + 15/30/45 min
  leads); compare with observed growth (FEDS perimeters in the window + VIIRS cells, cut at T); per 16 bearing
  sectors r = (obs p90 advance + 187.5 m)/(model p90 advance + 187.5 m), clamped [0.1, 5], smoothed, blended to
  the fire-wide median with exp(-d/3 km). elmfire_case.py: ADJ_FILE=path multiplies the spread raster (default
  off; earlier runs unchanged). Only data available at T is used.
- Widemouth 66510, T = 2026-08-02 12Z: no FEDS at T-24 h, used T-12 h (00Z, whole-edge start, no recent
  detections). Hindcast overshot: 42,459 vs 10,211 acres growth (area ratio 4.16), growth IoU 0.157.
  Sector r 0.13-1.55, median 0.35; >1 only N/NNE and S.
- +12 h vs FEDS Aug 3 00Z, growth IoU / whole IoU / area ratio / boundary mean m: selfcal 0.057 / 0.799 / 0.25
  / 715; physics3 adj1 0.082 / 0.701 / 1.29 / 1,071; no growth 0 / 0.814 / 0 / 743.
- Direction signal did persist (T->T+12 growth was NNE and S, where r > 1; none E/W where r was smallest), but
  neither forecast's detection-limited start spreads N or S, so a multiplier cannot add that growth; the global
  shrink trims false E/W growth. Not adopted; not added to the batch; no Hyak run.
- Caveats: one fire, one issue time; the hindcast start (whole edge) differs from the forecast start (active
  edges), inflating the overshoot; FEDS/VIIRS 375 m observations; constants (EPS, decay, sectors) untuned.
  Details: data/perimeter_batch/SELFCAL_NOTES.md.

### 18. Active front calibrated on burning-edge hotspots (4 fires) (2026-11-14)
- User-approved follow-up to entry 17 (the hindcast's direction was right but the detection-limited start could not
  grow from the edges that broke out). elmfire_case.py INIT=feds front rules (default unchanged): FRONT_KM=D
  (detections in the last ACTIVE_HOURS within D km of the edge), FRONT_RFILE+FRONT_R (edge cells with hindcast
  correction r >= R also burn), FRONT=whole. New scripts/front_calibrate.py: 10 rules (physics3 default d6_375m;
  H in {6,12,24} h x D in {0.75,1.5} km; d6_0.75 with r >= 0.8 / 1.0; whole edge). Per fire, past only: hindcast
  each rule T-12 h -> T from the FEDS perimeter at T-12 h with the forecast's start mode, score = CSI vs VIIRS
  cells in (T-12, T] outside the start perimeter (one 375 m cell tolerance, no cloud mask) + growth IoU vs FEDS T;
  r-rules use r from a (T-24, T-12] whole-edge hindcast (not eligible without FEDS at T-24 h). Then forecast 12 h.
- Fires: Widemouth 66510 (UT, 2026-08-02 12Z, physics3 inputs) and pilot fires with FEDS every 12 h and real growth:
  2021-038 AZ grass/timber, 2021-031 CA shrub, 2020-008 CA grass/shrub (older hourly HRRR inputs, not physics3).
- Chosen (past-only) / oracle (in-sample): Widemouth d6_375m (tie, every non-d24 rule falls back to whole edge at
  00Z) / d6_1.5; 2021-038 d6_0.75+r0.8 / d6_1.5; 2021-031 d6_0.75+r1.0 / d6_0.75+r0.8 (equal scores); 2020-008
  whole / whole.
- +12 h vs FEDS, mean of 4 fires, growth IoU / whole IoU / area ratio / boundary mean m: chosen 0.115 / 0.658 /
  1.95 / 2,237; physics3 front 0.066 / 0.659 / 1.37 / 2,270; whole edge 0.140 / 0.629 / 2.53 / 2,340; no growth
  0 / 0.778 / 0 / 770; oracle (in-sample) 0.145 / 0.663 / 2.08 / 2,245; fixed d6_1.5 0.129 / 0.660. Chosen beats
  the physics3 front on growth IoU on 3 fires (tie on Widemouth) at equal whole IoU; nothing beats no growth on
  whole IoU; 2020-008 overshoots (area ratio 3.4-5.4) with any front. Per-fire table: FRONT_NOTES.md.
- Not adopted as the default yet: a fixed wider front (d6_1.5) does as well as the calibration here.
- Caveats: 4 fires, one issue time each; few hotspot targets on Widemouth (4 / 29); on 2020-008 they are dominated
  by a separate complex outside the FEDS polygon; pilot weather differs from physics3; score weights untuned.
  Details: data/perimeter_batch/FRONT_NOTES.md; figures data/perimeter_batch/figures/front_*.png.

### 19. 2026-11-15 — Direction-aware edge training on Hyak (in progress)

- Continued Claude's edge-advance LightGBM experiment: raw-pre / harm-pre (2003–2011),
  raw-all / harm-all (plus VIIRS 2012–2019), and VIIRS-only. Validation is 2020;
  exploratory test is 2021–2024, already inspected in earlier experiments.
- Preserved original `data/edge` results. New isolated outputs: `data/edge_causal`.
  Rebuilt harmonized inputs with the existing controlled causal harmonizer fitted on
  2012–2014, excluding `next` and `next3`. Removed seven next-day observed weather
  features from all arms; retained day-t weather and historical neighbor features.
- Retained the original 0.35 harmonized presence threshold, soft training labels,
  arm-specific candidate selection, LightGBM hyperparameters and 2020 early stopping.
  These remain experimental choices; this is not an isolated test of harmonization alone.
- Five arms, seeds 0/1/2, 8 CPU threads each. Hyak job 476136 starts the nine controls;
  six harmonized runs automatically submit after the rebuilt samples are ready.
  Each task reserves one H200 allocation but LightGBM uses CPU, not GPU acceleration.
  30-minute limits give a maximum requested batch cost of $6.75. Live recorded user
  spend at submission was $12.97 / $250. No perimeter jobs were released.
- The predictor operates on 0.1-degree edge candidates. Mapping its weights to the
  ELMFIRE perimeter and checking three perimeter forecasts remain separate validation
  steps; these classifier scores are not perimeter accuracy or evidence of improvement.
- Availability caveats: evaluation candidates use next-day clear-sky label masks;
  day-t daily observed weather supports day-end hindcasts, not demonstrated morning
  forecasts. Removing look-ahead columns does not remove every observational bias.
- Update 00:54 PST: all nine controls completed (43–118 seconds, exit 0).
  Mean test AP over three seeds: raw-pre 0.03873, raw-all 0.04340,
  VIIRS-only 0.04172. All nine harmonized years rebuilt successfully;
  six harmonized tasks submitted as job 476177, initially pending priority.
- Update 00:54 PST: verified harmonized training actually running on Hyak.
  harm-all loads 5,182,878 training rows; harm-pre loads 2,246,572.
  First harm-pre seed fitted 108 trees; harm-all passed 400 iterations.
- Update 01:09 PST: all 15/15 runs and model artifacts verified complete; all
  six harmonized tasks exited 0. Pulled models and compact results locally.
  Three-seed mean exploratory test AP: harm-all 0.04407, raw-all 0.04340,
  VIIRS-only 0.04172, harm-pre 0.03152, raw-pre 0.03873.
  Harm-all exceeds raw-all by 0.00067 AP; this small classifier difference does
  not establish a perimeter improvement. Summary: data/edge_causal/comparison.md.

### 20. 2026-11-15 — Recent MODIS corroboration and learned ELMFIRE pilot
- Started causal MODIS-to-VIIRS corroboration over 2015–2026, using the fixed
  2012–2014 harmonizer on jointly clear sensor cell-days; support threshold 0.35.
  Reports both supported VIIRS detections and disagreements. MODIS absence is
  not grounds to reject VIIRS: lower sensitivity, clouds and overpass timing differ.
  Harmonized MODIS predicts VIIRS and is not independent ground truth. This daily
  archive experiment does not yet implement multiple timestamped overpass updates.
- Started three paired 12-hour ELMFIRE forecasts (2021-038, 2021-031, 2020-008).
  Three harm-all seeds predict from the previous complete UTC day's VIIRS history
  and weather, without future clear-sky candidate filtering. Map coarse candidate
  probabilities onto the fuel grid; spread multiplier p / mean validation CSI
  threshold clipped to [0.5,2], default 1 outside candidate coverage. This is an
  experimental transfer rule, not a calibrated fine-resolution spread probability.
  Existing FEDS initial perimeter and active-front rules remain the starting state.
  Results under data/edge_causal/elmfire; no forecast improvement claimed yet.
- Three paired pilots completed: growth IoU control -> weighted: 2021-038
  0.04355 -> 0.04348; 2021-031 0.07934 -> 0.07537; 2020-008
  0.06053 -> 0.06802. Whole-perimeter IoU decreased on all three.
  The transfer rule is not adopted as a default. This integrates spread-rate
  weighting; learned ignition-segment activation and sequential overpass assimilation
  remain unimplemented. Summary: data/edge_causal/elmfire/comparison.md.
- Weather/hotspot audit: edge features already include day-t gridMET wind,
  VPD/ERC/fm100/precipitation and 30-day daily fire-neighbor history. ELMFIRE
  already uses forecast wind and 1/10/100-h moisture, with timestamped hotspots
  in the six hours before issue defining the active edge. Explicit provenance
  and timing limitations recorded in data/edge_causal/INPUTS.md. Historical edge
  training is S-NPP only, not an all-three-VIIRS training corpus.
- Recent corroboration completed for 2025 and available 2026: MODIS supports
  14,082/49,676 and 11,168/42,987 jointly-clear VIIRS fire cell-days, respectively.
  2026 is partial-year input. MODIS absence must not veto VIIRS detections.
- Completed all 2015–2026 corroboration years; per-year counts and summary saved
  under data/edge_causal/corroboration. All six ELMFIRE cases completed, and
  weighted case metadata confirms the learned ADJ_FILE was supplied.

### 21. 2026-11-15 — Opt-in learned perimeter activation
- Implemented FRONT_PFILE + FRONT_PTHRESH for INIT=feds. A one-band probability
  raster must match landscape shape, transform and CRS; covered values must be
  finite in [0,1]. Nodata means no model coverage. Within the inner edge band,
  recent six-hour hotspots always remain active; model probabilities at or above
  threshold add active cells. Uncovered cells retain baseline selection (including
  the baseline whole-edge rule when no detections support an edge). If selection
  is empty, retain and record whole-edge fallback. Outside-perimeter recent
  hotspots retain the existing initialization behavior.
- Three harm-all seeds are averaged; fixed threshold is the mean of their existing
  validation-selected thresholds: 0.049366665383179985. Features use the previous
  complete UTC day. Coarse 0.1-degree candidate probabilities are copied only to
  fuel cells whose centres lie within their geographic footprint; no interpolation
  across gaps or claim of fine-resolution predictive skill. Model identifiers,
  feature date, thresholds, coverage, hotspot support, model additions and fallback
  are recorded in each case. Forecast weather and physical settings match control;
  these runs use ADJ=1 and no learned ADJ_FILE.
- New paired 12-hour forecasts started for 2021-038, 2021-031 and 2020-008 under
  data/edge_causal/activation, separate from the completed spread-weighting pilot.
  Activation remains opt-in; no expansion to other fires or threshold tuning.
  Recent MODIS corroboration remains a separate diagnostic; sequential pass
  assimilation is outside this change.
- Completed all six forecasts and scored against exact +12 h FEDS truth and
  post-issue hotspots. Results below are selected historical pilot cases, not
  evidence of generalization; no threshold tuning on these outcomes.

  | Fire | Arm | Growth IoU | Whole IoU | Boundary mean m | Hotspot hits | Active edge % |
  |---|---|---|---|---|---|---|
  | 2021-038 | activation | 0.04340 | 0.71700 | 982.3 | 5/7 | 28.51 |
  | 2021-038 | control | 0.04355 | 0.71767 | 980.8 | 5/7 | 27.19 |
  | 2021-038 | no_growth | 0.00000 | 0.78423 | 555.1 | 0/7 | 0.00 |
  | 2021-031 | activation | 0.12675 | 0.71598 | 1086.9 | 46/70 | 73.56 |
  | 2021-031 | control | 0.07934 | 0.70289 | 1040.0 | 30/70 | 40.76 |
  | 2021-031 | no_growth | 0.00000 | 0.69946 | 1250.8 | 0/70 | 0.00 |
  | 2020-008 | activation | 0.10634 | 0.49962 | 5450.5 | 249/264 | 36.31 |
  | 2020-008 | control | 0.06047 | 0.51531 | 5989.6 | 249/264 | 23.37 |
  | 2020-008 | no_growth | 0.00000 | 0.81334 | 531.3 | 0/264 | 0.00 |

- Edge model coverage / hotspot-supported / model-added cells:
  2021-038: 107 (1.71%) / 1703 / 83; whole-edge fallback False.
  2021-031: 2156 (57.40%) / 1531 / 1232; whole-edge fallback False.
  2020-008: 920 (20.21%) / 1064 / 589; whole-edge fallback False.
- Growth IoU improved on two fires; whole-perimeter and boundary outcomes are
  mixed. No-growth has substantially better whole IoU on 2021-038 and 2020-008.
  Keep activation experimental and opt-in. The repeated 2020-008 control growth
  IoU is 0.06047 versus 0.06053 in the prior spread pilot; control inputs and
  physical overrides are unchanged, so this small run variation is retained,
  not substituted with the old result.
- Verification: behavioral tests pass for high-probability activation, hotspot
  protection, nodata baseline preservation (including the no-hotspot whole-edge
  rule), empty-selection fallback, outside-perimeter hotspot preservation,
  shape/transform/CRS/band-count rejection, invalid probabilities, changed initial
  phi with identical spread multipliers. All three forecast pairs have identical
  weather, physical overrides, issue time and duration except the two opt-in
  activation arguments. Python compilation and diff whitespace checks pass.
- Reproduce: `uv run tests/test_front_probability.py`,
  `uv run scripts/edge_elmfire.py`, then
  `uv run scripts/edge_pilot_score.py data/edge_causal/activation`.
  Full scores and comparison: data/edge_causal/activation/{scores.json,comparison.md}.

### 22. 2026-11-15 — Weighted learned front, held-out test, outward-band ADJ
- **Weighted front (FRONT_PMODE=weighted).** Covered inner-edge cells without
  recent hotspot support start burning if p > 0 with spread multiplier
  max(p, 0.01) after all other ADJ terms; hotspot-supported, uncovered and
  outside-heat cells x1; p = 0 not ignited (ELMFIRE resets adj < 0 to 1 and
  never tags/spreads adj = 0, so no zero multiplier is emitted). Level-set
  limitation: ELMFIRE advances phi into an unburned cell at that cell's own
  speed, so a multiplier on cells already burning at t=0 barely acts; weighted
  == binary_pos (same ignitions, x1) to <= 0.0001 growth IoU on dev. Weighted is
  in effect "activate every covered edge cell with p > 0".
- **Outward band (ADJ_BAND_PFILE + ADJ_BAND_M + ADJ_BAND_PREF, user-approved).**
  Unburned, model-covered cells outside the start FEDS perimeter whose centre
  is within B m of a perimeter cell centre get clip(p / p_ref, 0.25, 2); all
  else x1; start front = control's. Frozen in manifest.json band_variant before
  any band run: B in {750, 2000} m, p_ref = 0.049366665 (mean validation
  threshold), floor 0.25, cap 2. Same landscapes, HRRR weather, probability
  rasters and physics as the held-out/dev runs. Tests cover emitted adj
  (cap, floor, p/p_ref, uncovered, burning, corner outside band, interior,
  composition with ADJ/ADJ_FILE, unchanged phi/weather/namelist, invalid options).
- **Held out:** 12 fires frozen in manifest.json (2026-08-02T12, +12 h FEDS
  truth, 6 regions), no failures. Five fires have covered_fuel_cells = 0 (no
  2026-08-01 edge candidate near them), so every learned arm equals control
  there; two more (66055, 53363) have ~0 edge coverage. Growth IoU mean / median:

  | Arm | All 12 | Covered 7 | vs control (better/worse, covered) | Whole IoU mean, covered | Hotspots (all) |
  |---|---|---|---|---|---|
  | no_growth | 0 / 0 | 0 / 0 | - | 0.9301 | 0/23 |
  | control | 0.0580 / 0.0238 | 0.0976 / 0.0650 | - | 0.8362 | 19/23 |
  | binary | 0.0633 / 0.0348 | 0.1066 / 0.0650 | 2/0 | 0.8373 | 19/23 |
  | weighted | 0.0545 / 0.0348 | 0.0916 / 0.0650 | 2/2 | 0.8110 | 19/23 |
  | band750 | 0.0581 / 0.0234 | 0.0978 / 0.0655 | 2/3 | 0.8357 | 19/23 |
  | band2000 | 0.0583 / 0.0206 | 0.0981 / 0.0717 | 2/3 | 0.8354 | 19/23 |

- **Development (in-sample, 3 dev fires):** growth IoU mean control 0.0611,
  binary 0.0922, weighted 0.0926, band750 0.0634, band2000 0.0642; band arms
  better on 1/3 fires (2020-008), worse on 2021-031.
- Reading: on held-out fires binary helped 2 fires (66433 +0.038, 65395 +0.025)
  and hurt none; weighted lost 0.10 on 62041 (activating the whole covered edge
  over-grew it). The band moves growth IoU by < 0.001 on average: +0.05 on 62041
  but -0.04 on 65080 and small losses on 66433/65395. No-growth still has the
  best whole IoU everywhere. Nothing here is significant on n = 7 influenced
  fires; keep all three opt-in. Binary remains the least harmful learned rule.
- Limitations: one issue time; FEDS truth; daily 0.1-degree next-day
  probabilities are not calibrated fine-cell spread rates; LF2025 fuels
  (anachronistic for the 2020-21 dev fires); the 2026-08-02 date was used
  earlier for physics-only batch evaluation; model coverage is sparse.
- Reproduce: `uv run tests/test_front_probability.py`;
  `uv run scripts/edge_front_weighted.py {dev,manifest,heldout,band}`;
  `uv run scripts/edge_pilot_score.py data/edge_causal/front_weighted/heldout --manifest data/edge_causal/front_weighted/manifest.json --arms control,binary,binary_pos,weighted,band750,band2000`
  (and `.../dev`); `uv run scripts/edge_heldout_table.py data/edge_causal/front_weighted/heldout data/edge_causal/front_weighted/dev`
  -> data/edge_causal/front_weighted/{heldout_table.md,heldout_table.json,growth_iou_vs_control.png}.

### 23. 2026-11-15 — Seasonal live fuel moisture (GSI proxy) and 20-ft wind factor options
- Before: LH_MOISTURE_CONTENT=90 everywhere (LW ELMFIRE default). Added, both opt-in (defaults unchanged):
  `LIVE_MOISTURE=gsi` (scripts/live_moisture.py; per-case LH/LW constants, ELMFIRE's rasters not used): Jolly-style
  GSI = photoperiod x VPD ramp (0.9-4.1 kPa) x fm100 ramp (5-15 %, stand-in for Tmin, absent from our gridMET
  files), 21-day mean of the days before the issue day; LH = 30 + 90 GSI, LW = 60 + 90 GSI. Ramps are literature
  values, not fitted. Western-summer cases give GSI 0-0.1 (LH 30-39, i.e. cured), far below the old 90.
  `WIND_20FT=f` (elmfire_case.py rescales the weather's 0.87; hrrr_weather.py reads env WIND_20FT). Source check
  (tools/elmfire/build/source): ELMFIRE computes its own canopy/fuel WAF raster (elmfire_init.f90) and midflame wind
  = WS20 x WAF (elmfire_subs.f90), so 0.87 (10 m -> 20 ft) is a separate step and not double counted.
- Choice made on development data only (data/physics_mw/manifest.json, frozen before any held-out run): 4 fires
  (2021-038, 2021-031, 2020-008, Widemouth 66510) x live {const90, gsi} x wind {0.87, 0.6}, control physics. In-sample:
  mean growth IoU const/0.87 .0664, const/0.6 .0630, gsi/0.87 .0932, gsi/0.6 .0849 (area-ratio |log| .90/1.02/.92/.77).
  Chosen gsi + 0.87 (best growth IoU; 0.6 does not help). Dev area ratios still large (0-008 6.4x).
- Held-out (12 frozen 2026-08-02T12 fires, +12 h FEDS, data/physics_mw/heldout_table.md): growth IoU control .0580 ->
  physics_mw .0522 (2 better / 6 worse of 8 with truth growth; mean diff -0.006), whole IoU .760 -> .748, area ratio mean
  37 -> 64 (median 2.7 both), hotspot hits 19 -> 20 of 23. No held-out gain: the dev improvement (4 fires, in-sample,
  driven by cured fuels spreading faster) did not transfer; GSI-lowered LH raises spread and over-predicts area.
  Keep the old constant as default. Caveats: one issue date, 12 fires, per-case (not per-cell) moisture.
- Reproduce: `scripts/physics_mw.py {grid,select,heldout}`; `uv run scripts/edge_pilot_score.py data/physics_mw/heldout
  --manifest data/edge_causal/front_weighted/manifest.json --arms control,physics_mw`; `uv run scripts/edge_heldout_table.py data/physics_mw/heldout`.

### 24. 2026-11-15 — Area-constrained ELMFIRE: a growth model decides how much, ELMFIRE where
- Idea: ELMFIRE over-predicts area (control area ratio median 2.7, mean 37 on the 12 held-out fires). Train a model on the
  harmonized MODIS/VIIRS record to say how much a fire grows in a day, convert to km2, and cut ELMFIRE's arrival map at the
  isochrone that gives that area (start footprint kept). Code: `scripts/growth_dataset.py`, `growth_train.py`, `growth_report.py`,
  `area_constrain.py`; notes `data/growth/NOTES.md`.
- Dataset: events = space-time clusters on the 0.1 deg daily grid (gap 2 days, 8-neighbour, catalog persistent-heat rule), from the
  tensors (viirs 2012-24, raw and harm 2003-11; harm = strict p_viirs >= 0.5). No pixel counts exist, so the peak filter is >= 5
  cells (catalog: 30 pixels) and the density filter is dropped. One row per event-day t (first day to 2 days after the last fire
  day); target = new fire cells on t+1 (clear-looked, none burned in t-29..t, within 2 cells of the footprint so far). Features:
  size, last three days' footprint and new cells, days active/since fire, gridMET on t and t+1 (t0 variant drops t+1), fuel
  fractions/canopy/elevation/roughness over the recent edge, season, lat/lon. Rows (train 2003-19 / val 2020 / test 2021-24):
  harm 9009/934/2903 (events 491 in train), raw 10268/934/2903, viirs-only 5134/934/2903. Mean target 0.52 new cells per day, 71% zero
  (viirs); a cell is ~95 km2, so most growth is below one cell and the task is weak by construction.
- Model: LightGBM (tweedie or log1p regression; separate zero-growth classifier), 2x2 grid (objective, leaves 15/63), 3 seeds, picked by validation MAE.
  Test (2021-24, same rows every arm, t1 = Aug 2-style next-day weather as perfect forecast): see `data/growth/test_table.md`.
  harm MAE 0.556, Spearman 0.399, zero-growth AUC 0.760; raw 0.560 / 0.414 / 0.767; viirs-only 0.571 / 0.395 / 0.757 (t0 variant: 0.394-0.410,
  almost the same). Predicting zero has MAE 0.519, i.e. no arm beats it on MAE (zero-inflated target); the signal is in ranking (single-feature
  Spearman: g0 0.26, n_t 0.26). Extra history helps (both pre-2012 arms beat viirs-only by ~0.015 MAE), but raw >= harm on rank
  and AUC by 0.003-0.015 (seed sd 0.002-0.004): no benefit of harmonization here. Exploratory: test years were scored once but the grid was small.
- Area conversion (`data/growth/area_conversion.json`, in-sample, 8 FEDS-archive fires, 6 matched, 82 event-days): FEDS daily growth km2 / observed new cells =
  45.7 km2 per cell (r2 through the origin 0.22, Spearman 0.58). Forecast area = 45.7 x predicted cells x 0.5 (12 h of the day; uniform-growth assumption).
- Held-out (12 frozen fires, issue 2026-08-02T12, truth FEDS +12 h; features from Aug 1 VIIRS history and gridMET; t1 uses Aug 2 gridMET, a
  perfect-forecast stand-in; t0 does not; oracle = true FEDS growth area, upper bound, uses truth): `data/growth/heldout_eval/comparison.md`.
  Growth IoU mean/median: no growth 0 / 0 (n=10), control 0.058 / 0.024, areacon_t1 0.061 / 0.000, areacon_t0 0.062 / 0.000, oracle 0.082 / 0.067.
  Whole IoU: control 0.760, areacon_t1 0.858, t0 0.859, oracle 0.865, no growth 0.910. Area ratio median: control 2.71, t1 0.12, oracle 1.00.
  Better than control on growth IoU in 3 of 12 (t1, t0), 5 (oracle). Hotspot hits 15/23 vs control 19/23.
- Reading: the cut fixes ELMFIRE's over-area (whole IoU +0.10) but the predicted areas are tiny (0.0-12 km2) because the model predicts ~0.4
  cells for every fire, so it lands near no growth (whole IoU 0.91) and loses hotspot hits; even with the true area the growth IoU is only 0.08 (where, not how much, is the limit) .
  Caveats: 12 fires, one date; 6 of 12 fires have no VIIRS cluster at 0.1 deg on Aug 1 (zero predicted growth); k fitted in-sample on few fires.

### 25. 2026-11-15 — Lower/upper forecast cases; does harmonized history improve the tail? (`scripts/growth_bounds.py`)
- Cases: lower (most likely) = entry 24's area-constrained forecast (growth model harm, t0 = day-t features, x 45.7 km2/cell x 0.5). Upper (worst case) =
  the q0.9 (frozen) of next-day growth among the K=100 (frozen) nearest ANALOG event-days (standardized size, recent growth, days active, season, lat/lon, day-t
  wind/VPD/ERC/FM100, fuel fractions) in the arm's training rows (<= 2019), same conversion, max with lower; footprint = control (or, where local, the earliest-arrival
  UNION of the fuelwind16 + physics3 members) arrival map cut at that area. Only Widemouth 2 (66510) has members locally (19); the other 72 use the control run.
- Tail calibration, test 2021-24 (2903 event-days, 146 events; 312 in the largest-growth decile), `data/bounds/tail_calibration.md`. Coverage = share of days with growth
  <= predicted q90 (target 0.90; discrete target with 71% zeros, so ties push it above 0.90): harm 0.939, raw 0.941, viirs-only 0.932, constant pool q90 0.950.
  Pinball(0.9): harm 0.2190, raw 0.2273, viirs-only 0.2329, constant 0.2914, zero 0.467. Tail coverage (largest decile): harm 0.45, raw 0.48, viirs-only 0.39;
  sum pred / sum obs in that decile 0.76 / 0.72 / 0.65. Paired pinball (event-block bootstrap): harm - raw -0.008 (CI -0.026..+0.003), harm - viirs-only
  -0.014 (CI -0.036..+0.0003): the ordering favours harmonized history but is not significant; the analog (conditional) q90 beats a constant q90, and every arm
  under-covers the extreme days (the top decile is ~half covered). The ~0.9-0.94 coverage is mostly ties, not a calibrated tail.
- Held-out, 12 fires, +12 h vs FEDS (observed growth 67 km2 in total; `data/bounds/heldout_table.md`): growth IoU mean / recall (pooled) / mean false-alarm km2:
  no growth 0 / 0 / 0; lower 0.062 / 0.135 / 2.7; upper (harm) 0.060 / 0.269 / 11.4; control 0.058 / 0.361 / 20.5. Whole IoU 0.909 / 0.889 / 0.784 / 0.774. The upper case
  recovers about 3/4 of the control's recall at about half the false-alarm area; raw and viirs-only uppers are nearly the same (0.283 recall, 13.2 km2).
- Export: lower and upper GeoJSON for all 73 Aug 2 forecasts (`earth/public/data/forecasts_bounds`, index.json lists areas and footprint source). Caveats: 12 fires/one date;
  harm/raw/viirs-only differences within noise; the K-NN feature set and K were chosen once without tuning; weather/fuel for fires without a VIIRS cluster looked up on the 0.1 deg grid.

### 26. 2026-11-15 — Burn-scar fuel correction from the harmonized record (`scripts/burnscar.py`, `BURNSCAR_*` in `elmfire_case.py`)
- Idea: LANDFIRE fuels may not show recent fires, so ELMFIRE would spread freely into ground that burned lately. New opt-in (default unchanged)
  `BURNSCAR_FILE=path BURNSCAR_YEARS=N BURNSCAR_MULT=m`: fuel cells whose burn-history cell has years-since-fire <= N get spread multiplier m after every other ADJ
  term (m = 0: non-burnable, fbfm40 99). Burn-history cells that touch the fire's own start area (burned at issue, FEDS perimeter, burning cells) are excluded
  whole. Emitted adj/fuel tested in `tests/test_burnscar.py` (3 mask/adj/fuel/composition/validation checks, plus a coarse-raster exclusion check).
- History rasters (`data/burnscar/rasters/ARM_ISSUE.tif`, 0.1 deg, years since last fire, -1 = none, 2003 to the day before issue; `rasters.json`): pre-2012 harm =
  strict `p_viirs >= 0.5`, raw = MODIS Terra/Aqua presence, viirs-only = nothing; 2012+ VNP14A1 fire > 0 for all arms. Consequence: the three arms are
  identical in any window starting after 2011. Cells differing (2026-08-02): N <= 10: 0; N = 15: harm vs raw 616, harm vs viirs-only 235; N = 23 (all history): 7505 / 3548.
  So with N in {1,2,3,5} harm vs raw vs viirs-only cannot differ for these fires; the arms were still run separately and are identical.
- Finer variant (VIIRS 375 m): not built. `data/raw/viirs_fire` holds only 2026 days 211-218 (the days around the forecast), no multi-year archive. A whole 0.1 deg
  cell (~85 km2) counts as burned if any pixel fired: over-inclusive.
- Dev choice (harm arm, 3 dev fires + Widemouth 66510, N in {1,2,3,5} x m in {0, 0.3}; in-sample): mean growth IoU control 0.0664; m = 0 0.0678 and m = 0.3 0.0674
  for every N (N makes no difference; per-fire growth IoU control 0.044/0.079/0.060/0.082 (2021-038, 2021-031, 2020-008, 66510), m = 0: 0.044/0.080/0.065/0.082).
  Rule (frozen in `data/burnscar/manifest.json` first): best within 0.002 ties go to the smaller perturbation. Chosen N = 1, m = 0.3; gain +0.0009 is below 0.002 (`no_dev_benefit` true).
- Held-out (12 frozen 2026-08-02T12 fires, same cases/weather as control; `data/burnscar/heldout_table.md`): scar_harm = scar_raw = scar_viirs_only = control to every
  reported digit (growth IoU 0.0580 mean / 0.0238 median, whole IoU 0.7601, area ratio 36.97 mean / 2.71 median, false growth 0.911; no_growth growth IoU 0, whole IoU 0.9098). No fire changed (0 better / 0 worse).
- Coverage: 11 of 12 held-out landscapes contain scar cells (<= 1 y after exclusion; 4-72% of 1.0-1.6 M fuel cells; 66055 none; 66433 210 k, 65395 43 k, 340 603 k, 53095 843 k), but
  every history cell touching the start area is excluded (up to 640 k cells) and a 0.1 deg cell is 8-11 km, so the remaining scar starts well beyond what a 12 h spread reaches; the correction is inert here.
- Caveats: coarse history cells plus whole-cell own exclusion make the option nearly inert at 12 h; a useful test needs a fine (perimeter-based) scar map and fires that run into old scars. LF2025 fuels
  already reflect disturbance through about 2024, so only 2025-26 scars could matter for the held-out date. Dev numbers in-sample; N makes no difference. Not recommended for the demo.

### 27. 2026-11-15 — Containment status used for evaluation and display only (`scripts/containment_aug2.py`, `scripts/containment_split.py`)
- Decision: forecasts stay unsuppressed (free-burning potential, for planning containment). Containment changes no input, parameter or forecast; it only splits the scores and labels the display.
- Data: NIFC WFIGS Incident Locations YearToDate, matched to the 76 Aug 2 FEDS fires as in feds_to_earth_json.py (origin within 0.05 deg of the outline; largest candidate wins; distance recorded). Status at 2026-08-02T12:00Z from dates only: contained if ContainmentDateTime/ControlDateTime/FireOutDateTime <= issue. Result: 2 contained (48632 Shore, 62590 Elephant), 44 uncontained, 30 unmatched. Output data/containment/aug2_containment.json.
- Split (data/containment/split_table.md). Held-out 12: 0 contained, 7 uncontained, 5 unmatched. Uncontained (n=7, 6 with growth): control growth IoU 0.104, recall 0.52, whole IoU 0.744, median area ratio 3.7; lower 0.113 / 0.20 / 0.897 / 0.64; upper_harm 0.109 / 0.48 / 0.761 / 2.7; no growth whole IoU 0.933. Unmatched (n=5, 4 with growth, 1 km2 total observed): control 0.017 / 0.03 / 0.815. Aug 2 batch with +12 h truth: 7 uncontained, 12 unmatched, 0 contained (control growth IoU 0.095 vs 0.066).
- Reading: the held-out and batch sets contain no contained fire, so the split cannot yet show whether suppression explains over-prediction (area ratio ~3.7 on uncontained fires). The unmatched group is mostly small fires that are not in WFIGS (>= small incidents), where all cases are near-trivial.
- Caveats: dates only (a fire with a line but no date counts as uncontained); PercentContained is the latest value (e.g. 66510 shows 100% now but is uncontained at issue), not as of issue; matching is by proximity and largest incident, so a wrong incident is possible; n is tiny, differences descriptive. Batch recall and lower/upper are not stored outside the held-out 12.
- Display: /api/fires/:id gains `containment` (from earth/public/data/containment/*_containment.json by issue day); the ops detail shows "Containment as of forecast: ..." and "Unsuppressed spread potential — firefighting not modelled". sw.js v42.

### 28. 2026-11-15 — Growth model trained on uncontained / semi-contained fire-days: does harmonized 2003-2011 history help? (`scripts/growth_uncontained.py`)

Hypothesis (user): an unsuppressed growth model should be trained on fire-days not yet contained; harm adds many such days for 2003-2011, so harm should beat raw and viirs-only. Out of scope: contained fires.
- Labels (rules frozen before scoring). Train/val (2003-2020): ICS-209-PLUS wildfire sitreps (St. Denis et al. 2023, figshare 19858927; only the wildfire zip, 49 MB, in data/growth_uncontained/ics209/, not committed). Row = growth event-day; link = nearest incident point of origin within 0.3 deg (about 30 km) whose window [discovery-1 d, last sitrep+14 d] holds the date; status = percent contained of the latest sitrep on or before the day (0 before the first). Main label unc80 (< 80%: uncontained plus semi-contained, includes the free-burning early period of later-contained fires); sensitivity unc50 (< 50%). Test (2021-2024): WFIGS incident locations (wildfire, >= 300 acres; scripts/growth_uncontained_fetch.py), unc = before min(Containment, Control, FireOut) date (no daily percent exists), same rows for every arm: 2162 of 2903 test rows, 112 events.
- Match rates (data/growth_uncontained/match_rates.md): harm 64.7%, raw 64.1%, viirs 2012-2020 75.8% (ICS); test 77.9% (WFIGS). Unmatched rows are excluded from the unc subsets.
- Training rows (years <= 2019; MODIS rows in brackets): harm all 9009 (3875), unc80 5388 (2017), unc50 4518 (1613); raw all 10268 (5134), unc80 6065 (2694), unc50 5138 (2233); viirs-only all 5134, unc80 3371, unc50 2905. So harm adds 2017 unc80 fire-days over viirs-only (raw: 2694; harm retains 75% of raw's).
- Models as growth_train.py (LightGBM, same grid/features), 3-seed ensemble, grid chosen by val MAE on unc80 2020 rows, evaluated on the 2162 unc test rows. Table (variant t0, Spearman / zero AUC / analog-q90 pinball), results in data/growth_uncontained/results.md:
  - trained on unc80: harm 0.404 / 0.764 / 0.1877; raw 0.400 / 0.767 / 0.1884; viirs-only 0.398 / 0.754 / 0.1867.
  - all rows (reference): harm 0.395 / 0.762 / 0.1830; raw 0.406 / 0.767 / 0.1858; viirs-only 0.394 / 0.759 / 0.1865.
  - Paired event-block bootstrap, unc80, harm minus raw: Spearman +0.005 (-0.007 to +0.017), AUC -0.003 (-0.011 to +0.004), pinball -0.0007 (-0.006 to +0.005). Harm minus viirs-only: Spearman +0.006 (-0.006 to +0.019), AUC +0.010 (+0.001 to +0.020), pinball +0.001 (-0.004 to +0.006). unc50 and t1 agree in sign and are no stronger (unc50 t1 harm-raw AUC -0.011, CI -0.018 to -0.003, i.e. raw better).
- Conclusion: the hypothesis is not supported. Restricting training to uncontained/semi-contained rows does not make harm beat raw (differences are within noise, raw is nominally equal or better); harm vs viirs-only is a small, mostly non-significant gain (one AUC CI excludes zero at unc80, none at unc50 or t1). Restricting to unc rows did not improve any arm over all rows either (it shrinks the training set by about 40%). Caveats: 112 test events; matching is by proximity and misses about a quarter of rows; ICS stops in 2020 and WFIGS has no daily percent, so train and test labels differ in kind; in-sample selection only on 2020 val; the analog-q90 pool coverage is about 0.94 for every arm (over-covering), unchanged by this.

### 29. 2026-11-15 — Where does it grow? ELMFIRE arrival-time ranking vs a distance buffer and a downwind rule, "how much" removed (`scripts/where_skill.py`)
Question: does the ELMFIRE physics run (adj1, INIT=feds, 12 h) know which cells grow, beyond what a planner gets from "nearest to the fire edge" or "nearest, stretched downwind"? Only location is tested: every method is scored on the same candidate set and, for the area metrics, is given exactly the observed growth area.
- Fires (frozen in `data/where_skill/manifest.json` from eligibility only, before any ranking): 2026-08-02T12 fires that are uncontained and incident-matched (`data/containment/aug2_containment.json`), have an adj1 run, an exact FEDS perimeter at +12 h, observed burnable growth, and HRRR weather files locally. n = 26 (all from `data/perimeter_batch/aug2_local`; the Hyak-pulled `data/demo_pull` runs were mostly unusable here because their landscape files are not local: 5 fires dropped for that reason, plus 6 with no +12 h perimeter, 6 with no observed burnable growth in domain). Second group (in-sample-ish for method choices): 2021-038, 2021-031, 2020-008 (dev control arm) and 66510, n = 4. Sensitivity: the 6 primary fires that also have a held-out front_weighted control run (a different physics arm, LH moisture 90) rescored with it.
- Candidates: burnable (FBFM40 > 100) cells outside the start footprint (FEDS at issue | ELMFIRE toa = 0), in the union of ELMFIRE's 12 h reach and a 2 x (max observed advance) buffer; identical for every method. Observed = FEDS +12 h growth cells in that set. Methods: elmfire (arrival time, unburned last, ties by distance), buffer (distance to start), downwind (d * (1 - e cos theta), ellipse polar form from the FBP length-to-breadth of the mean HRRR wind, domain-mean 12 h wind vector; frozen, not tuned). The wind-ellipse model (`ellipse_spread.py`) was not run separately: it needs a GFS wind file and a different state input, and the downwind rule is its distance geometry.
- Result, primary n = 26 (mean; paired bootstrap 95% CI over fires, ELMFIRE minus baseline): AUC 0.863 vs buffer 0.880 vs downwind 0.825; ELMFIRE - buffer -0.017 [-0.045, +0.006], ELMFIRE - downwind +0.038 [+0.009, +0.067]. Recall at observed area (= precision) 0.277 vs 0.241 vs 0.220; ELMFIRE - buffer +0.037 [-0.018, +0.085] (wins 19/26), ELMFIRE - downwind +0.057 [+0.002, +0.110]. Growth IoU at observed area 0.170 / 0.148 / 0.133; differences +0.022 [-0.016, +0.055] and +0.036 [-0.004, +0.074].
- Dev group n = 4 (in-sample-ish): ELMFIRE is worse than both baselines (recall 0.217 vs buffer 0.341 vs downwind 0.352; ELMFIRE - buffer -0.124 [-0.204, -0.038]). Control-arm sensitivity n = 6: recall 0.220 vs buffer 0.156 vs downwind 0.219, all CIs include zero.
- Verdict as-is: on the Aug 2 fires ELMFIRE's arrival ordering is no better than a plain distance buffer in ranking quality (AUC -0.017, CI includes 0), slightly better when forced to pick the observed area (+0.037 recall, CI includes 0), and better than the frozen downwind rule only by a small margin. So the evidence does not show that ELMFIRE knows where beyond proximity; the dev fires point the other way. The downwind rule is worse than the buffer, i.e. a domain-mean wind direction is a poor guide here.
- Caveats: FEDS boundary uncertainty about 375 m is comparable to the max advance of many fires (0.1-0.5 km); many fires are small; n is 26 and 4 with heavy per-fire variance; observed growth includes FEDS infill and perimeter smoothing that no method models; the buffer is evaluated from the same footprint ELMFIRE used (an advantage that ELMFIRE's own growth is judged against); the domain mean wind is crude; ELMFIRE cells never reached are ranked by distance, so ELMFIRE is a buffer hybrid beyond its reach. Outputs: `data/where_skill/results.md`, `results.json`, `figures/where_skill.png`.

### 30. 2026-11-15 — Fire-season trends 2003-2025, raw vs harmonized, and the 2012 step (`scripts/fire_trends.py`)
Question: does the raw MODIS-to-VIIRS record carry an artificial 2012 step / trend that the harmonized series removes?
- Method: from the History calendar's daily series (same 9 region boxes, same NOAA-20 gap fill), per region and year 2003-2025: annual fire-active cell-days (gap years scaled to a full year), season start/end (day of year when cumulative activity passes 10% / 90%, missing days counted as zero), season length, active days (daily value >= 1 cell). Trend: Theil-Sen slope + Mann-Kendall p. Step: mean 2012-2025 minus 2003-2011 (permutation p, 20000 draws) and a Pettitt change point. Outputs: `earth/public/data/trends/*.json`, `data/trends/trends_table.md`, `data/trends/figures/raw_vs_harmonized_us.png`.
- US annual activity: raw slope -870 cell-days/yr (MK p 0.004), step 2012 -15,915 (perm p <0.001, Pettitt 2012 p 0.001); harmonized -55/yr (p 0.75), step -1,313 (p 0.37), Pettitt 2013 p 0.74. The raw record's apparent decline is almost entirely the sensor change (MODIS Terra+Aqua union gives more cells than VIIRS); harmonized shows no trend and no step.
- US season length: raw +0.7 d/yr (p 0.007), step +7 d (p 0.04); harmonized +0.4 d/yr (p 0.19), step +2 d (p 0.50). Season start/end: no significant US trend in either (end raw +0.3 d/yr p 0.04, harmonized p 0.19).
- Regions: Southeast activity raw -343/yr (p 0.03, step p <0.001, Pettitt 2012) vs harmonized +26/yr (p 0.84, step p 0.42). Southwest activity raw -26/yr (p 0.001, step p <0.001) vs harmonized -14/yr (p 0.09), a step of -262 that stays significant (p 0.013), so harmonization does not remove everything there. Pacific NW, Rockies, California: no significant trend or step in either series.
- Not clean: active days (>= 1 cell) is uninformative for the US (every day active) and the harmonized series shows a spurious negative 2012 step in the Southwest (-42 d, p <0.001) and Southeast (-3.7 d), because an expected count below 1 cell on quiet days is not the same as VIIRS flagging a cell; treat that metric as unreliable for harmonized data. Season length is noisy year to year (sd about 7 d), so a 23-year trend has low power.
- Caveats: counts are 0.1 degree (about 10 km) cell-days, not area burned; the record starts in 2003 because Aqua starts in 2002; regions are lon/lat boxes so edges and first-match assignment are approximate; the mean-difference step test confounds a real trend with a step (Pettitt agrees only when the break year is 2012-2013); 2012-2025 harmonized is VIIRS itself, so the harmonized "no step" result relies on the 2003-2011 correction fitted on 2012-2026 overlap (in-sample for the correction, though trends are not fitted to it).
- Display: History page "Trends 2003-2025" card; fire watch detail shows "This week vs 2003-2025: Nth percentile (region)" from the calendar's harmonized pct2026.

### 31. 2026-11-15 — Size-calibrated worst case: ELMFIRE/distance blend footprint, power-law growth scale (`scripts/worstcase_calibrate.py`)
Problem: the entry-25 upper case captured only 27% (pooled, uncontained held-out 7) of +12 h growth because its footprint is a cut of one ELMFIRE run and its area (analog q90 x 45.7 km2/cell x 0.5) is often one cell regardless of fire size. Post-processing only; no new runs.
- Footprint: candidates = burnable cells outside the issue-time active perimeter (FEDS at issue | toa = 0); score s = w * rank(ELMFIRE arrival, earliest over local fuelwind16/physics3 members, unburned last) + (1-w) * rank(distance to the perimeter), w in {0, .25, .5, .75, 1}; lowest s until area A.
- Area: A = m * G(size), G = 0.171 * size_km2^0.696 (log-log OLS of observed +12 h growth on fire size at issue, 24 calibration fires; no single-cell cap). Chosen over recent-growth-based scales to keep one fitted law that every fire has (73 export fires, not all have 12-24 h of FEDS history).
- Size classes frozen from sizes only, before any recall: small < 10 km2 (1,000 ha), medium 10-100 km2, large > 100 km2; calibration n = 7 / 6 / 11 (>= 5 each, no pooling needed).
- Calibration (fires = where_skill manifest uncontained matched Aug 2 fires NOT in the frozen held-out 12, plus dev 2020-008, 2021-031, 2021-038, 66510; n = 24, FEDS +12 h growth): per class, smallest m on a frozen grid with pooled recall >= 80% for each w, then least false-alarm area. Result (`data/worstcase2/calibration.md`): small w 0, m 18.9 (recall 0.81, false alarm 68 km2 vs 6.6 km2 observed); medium w 0, m 22.8 (0.83, 247 vs 25.7); large w 0, m 33.1 (0.81, 3,646 vs 419). The w rows differ little (areas are large enough to cover most of the reachable ring); w = 0 (pure distance) has the least false alarm, i.e. no evidence ELMFIRE arrival ordering helps once the area is this big (consistent with entry 29).
- Held-out 12, evaluated once (`data/worstcase2/heldout.md`), uncontained n = 7, observed growth 65.6 km2, pooled recall / false-alarm km2 total (mean) / whole IoU mean: new worst case 0.889 / 1,413 (202) / 0.473; old upper 0.273 / 133 (19) / 0.761; control run 0.366 / 243 (35) / 0.744. All 12: 0.878 / 1,463 / 0.446 vs 0.269 / 137 / 0.784 vs 0.361 / 246 / 0.760. Per class (uncontained): small n=0 (all 4 held-out small fires are unmatched; all 4: recall 0.16, FA 23 km2 vs observed 1.0 km2), medium n=3 recall 0.80 (old 0.82, FA 91 vs 55 km2), large n=4 recall 0.90 (old 0.23, FA 1,321 vs 79 km2).
- Reading: the 80% target is reached on held-out (0.89 pooled, driven by the large fires), at the price of about 10-20 km2 of false alarm per km2 of observed growth in the large class. Heavy-tailed growth means pooled 80% is bought with area that the typical fire does not burn; the whole-IoU falls from 0.76 to 0.47. This is a worst-case footprint, not a forecast.
- Caveats: few fires per class (6-11 calibration, 3-4 held-out per class, 0 uncontained small); one date; FEDS edges ~375 m so small-fire growth is mostly noise (small-class recall 0.16 held-out); pooled recall is dominated by a few fast fires; G is a size-only law (no weather/recent growth), m absorbs the heavy tail; a 4-fire dev group is in the calibration set; candidate cells are burnable only, so growth on non-burnable cells (FEDS infill) cannot be captured. The ops label now reads "Worst case: sized to capture ~80% of growth for fires of this size"; the technical page still describes the old method.
- Export: all 73 `*-upper.json` re-written (`bound` block: method, size_class, target_capture, w, m, fire_size_km2, growth_scale_km2, area_km2, old_area_km2); index.json upper areas sum 7,025 km2 vs 413 km2 before; lower files unchanged.

### 32. 2026-11-15 — Display smoothing of the most-likely and worst-case outlines (display only)
- What: the Aug 2 lower and upper files were re-polygonized (`scripts/smooth_bounds.py`, `FORECAST_SMOOTH_M=120` in `scripts/forecast_to_geojson.py`). Each cumulative footprint (start, <= 3 h, <= 6 h, <= 12 h) is upsampled 3x, Gaussian-blurred (sigma 120 m), thresholded at the level that keeps its cell count, holes filled, specks under 5 ha dropped, then re-nested.
- Why: the size-calibrated worst case (entry 31) selects scattered cells from a blended score, so the map showed hundreds of fragments per fire (Sinlahekin 12 h band: 555 polygons -> 4).
- Numbers: worst-case growth acres new/old over 71 fires with growth: median 1.000, range 0.993-1.090 (filled holes add area). Population exposure re-run on the smoothed files.
- Caveats: rasters, recall and all scores are unchanged (computed on cells, not on these outlines); the drawn outline now includes small unburned islands inside the footprint, and the start polygon is smoothed by up to ~1 cell.

### 33. 2026-11-15 — Multi-day verification: Aug 3, 4, 5 issues on Hyak (held-out days), and the 11-member worst-case ensemble
- What: the frozen manifest (`data/multiday/manifest.json`, uncontained + WFIGS-matched at issue, set before any run) was run on Tillicum (jobs 481215/481383/481412; Hyak elevation from LANDFIRE 30 m, `LANDSCAPE_DEM=landfire`). Model = Aug 2 demo physics (adj1) unchanged; no choice was made on these days. 240 tasks: 187 complete; 43 excluded by rule (39 no VIIRS hot cells in the 6 h before issue, 4 no FEDS start), 10 network failures not retried. Total Hyak spend $27.62.
- Where skill (`scripts/where_skill.py`, `data/where_skill/2026080{3,4,5}/results.md`), recall of observed +12 h growth at the observed area, ELMFIRE / buffer / downwind:
  - Aug 3 (n=31): 0.273 / 0.197 / 0.172; ELM-buffer +0.075 [+0.025, +0.124], ELM-downwind +0.100 [+0.054, +0.148]
  - Aug 4 (n=29): 0.331 / 0.252 / 0.224; +0.079 [+0.015, +0.142], +0.106 [+0.042, +0.170]
  - Aug 5 (n=37): 0.361 / 0.282 / 0.252; +0.079 [+0.021, +0.143], +0.110 [+0.053, +0.170]
  - Pooled 97 fire-days: 0.324 / 0.246 / 0.218. Growth IoU at observed area beats both baselines on every day with CIs above zero. AUC stays a tie with the buffer (-0.007 to -0.031, CIs span 0).
- Reading: on held-out days ELMFIRE locates the next 12 h of growth better than a distance buffer or a downwind rule, about one third more captured growth at the same area. This is stronger than Aug 2 (entry 29: 0.277 / 0.241 / 0.220, CI on ELM-buffer spanning 0) and replicates across three independent issue days.
- Worst-case ensemble (`scripts/multiday.py recall`, uncut arrival maps): mean recall control vs union of 11 members (wind dir +-20 deg, speed x0.75/1.25, dead moisture +-2): Aug 2 0.531 -> 0.604 (n=31), Aug 3 0.529 -> 0.593, Aug 4 0.568 -> 0.627, Aug 5 0.564 -> 0.634. The union is larger than the control, so this is not an equal-area gain.
- Caveats: fire-days are not independent (the same fire appears on up to 3 days), so the pooled CI is optimistic; per-day CIs are the honest unit. Mostly slow days (advance < 2 km, ~5 mph). FEDS +12 h edges ~375 m. The 10 network failures are random, not outcome-related.

### 34. 2026-11-15 — Worst case graded on capture: capture vs area marked, held-out Aug 3-5 (`scripts/capture_curve.py`)
- What: the worst case is now graded on capture (share of the observed +12 h growth inside the marked area) rather than growth IoU, which penalises a deliberately generous envelope. To ask whether ELMFIRE's shape helps a capture-graded envelope, capture was computed at marked areas of K x the observed growth (oracle-sized, so only the ranking differs) on the 97 uncontained fire-days of entry 33, for the where_skill rules plus the 11-member union (earliest arrival over members, then distance). `data/capture_curve/results.md`.
- Pooled capture, buffer / downwind / ELMFIRE control / ELMFIRE union; union minus buffer (95% fire-day bootstrap CI); fire-days union > buffer / < buffer:
  - 1x: 0.242 / 0.240 / 0.309 / 0.323; +0.081 [+0.046, +0.116]; 63 / 31
  - 2x: 0.397 / 0.387 / 0.447 / 0.457; +0.059 [+0.023, +0.093]; 62 / 31
  - 3x: 0.512 / 0.494 / 0.543 / 0.555; +0.043 [+0.013, +0.073]; 58 / 34
  - 5x: 0.670 / 0.645 / 0.684 / 0.683; +0.013 [-0.010, +0.037]; 44 / 41
  - 10x: 0.867 / 0.840 / 0.874 / 0.873; +0.006 [-0.004, +0.019]; 25 / 34
- Reading: ELMFIRE's shape adds capture up to about 3x the observed area; from about 5x a distance buffer captures as much. The 80% worst case (entry 31: 0.889 pooled on 7 held-out, ~22x the observed growth area) sits in the regime where shape does not matter, consistent with its calibration choosing w = 0 (pure distance). The union adds ~0.01 over the control at equal area: the ensemble's gain in entry 33 came mostly from marking more area.
- Site: Technical page verification now grades the worst case on capture (table above), the most-likely table drops the stale old worst-case row.
- Caveats: oracle-sized areas (the real worst case sizes from fire size, not the observed growth); fire-days not independent; buffer candidates limited to 2x the observed max advance (where_skill domain), which favours the buffer; mostly slow days.

### 35. 2026-11-15 — Worst case ordered by ELMFIRE ensemble arrival (w = 1), re-calibrated to the same 80% target
- What: user decision after entry 34. `worstcase_calibrate.py` with `WORSTCASE_W=1.0 WORSTCASE_OUT=data/worstcase3` (classes.json copied unchanged from worstcase2): candidate cells are ordered by earliest arrival over the ensemble members, then by distance from the perimeter, instead of pure distance (w = 0). The area multiplier m was re-calibrated per class on the same calibration fires to reach 80% pooled capture: small 20.7, medium 25, large 36.4 (was 18.9 / 22.8 / 33.1).
- Held-out 12 (frozen), uncontained 7, graded on capture: pooled capture 0.905 (was 0.889 with w = 0), false alarm 1,557 km2 total (was 1,413), predicted 1,616 km2 (was 1,471). Re-exported 73 upper files, smoothed (entry 32), population exposure re-run.
- Reading: same capture regime as before (entry 34: shape matters little past ~5x area). Where ELMFIRE's 12 h ensemble reach is smaller than the worst-case area, the remainder is filled by distance, so large-fire worst cases are still mostly a band around the ELMFIRE footprint.
- Caveats: one issue date for calibration; 7 held-out uncontained fires; capture is pooled (dominated by the large fires).

### 36. 2026-11-15 — Does the best "where" model depend on fire type? (`scripts/skill_by_class.py`, exploratory)
- What: the 97 held-out uncontained fire-days of entry 33 split by issue-time quantities only (fire size at issue with the worst-case class bounds 10 / 100 km2; mean HRRR wind tertiles; ELMFIRE's predicted 12 h reach / fire size tertiles). Mean recall at observed area. `data/skill_by_class/results.md`.
- ELMFIRE - buffer (95% CI) by size: small < 10 km2 (n=31) +0.000 [-0.050, +0.054] (0.358 vs 0.357); medium (n=32) +0.084 [+0.028, +0.141]; large >= 100 km2 (n=34) +0.143 [+0.089, +0.202] (0.262 vs 0.119). ELMFIRE beats downwind in every size class.
- By wind: calm +0.063, mid +0.121, windy +0.051 [-0.016, +0.125]. By ELMFIRE predicted spread: low +0.136, mid +0.122, high (mostly small fires) -0.022 [-0.072, +0.029] (buffer nominally best).
- Reading: ELMFIRE is best or tied in every group; its edge grows with fire size. On small fires (and those ELMFIRE predicts will grow a lot relative to their size, mostly the same fires) it ties a buffer, plausibly because their 12 h advance is comparable to the FEDS edge uncertainty (~375 m). Routing small fires to a buffer would not raise skill (a tie), so the location model stays ELMFIRE for all; fire-type dependence is kept where it matters, in sizing (the worst case's per-class areas, entry 31/35).
- Caveats: exploratory, splits chosen after the multi-day results (9 groups, no multiple-comparison correction); tertile cut points from these same fire-days; fire-days not independent.
