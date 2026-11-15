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
