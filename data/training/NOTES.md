# Training ELMFIRE corrections: running notes

**Status (2026-11-15): shelved.** Tillicum will not be used for ELMFIRE runs and the many-fire ensemble is on hold.
What exists: the catalog (kept: its 2021-2024 US events are the evaluation set for comparing models trained on
harmonized vs non-harmonized fire data), the per-fire pipeline (tested on 8 fires), members, scoring, and a
pilot on 3 fires (below). Nothing was submitted to Tillicum.

ELMFIRE is the physics; we learn its corrections. For many past fires and several 12Z issue times per fire, run
an ensemble of ELMFIRE settings, score each run against VIIRS per overpass (pooled CSI, cloud-masked where masks
exist) and FEDS IoU where perimeters exist, then fit a model from conditions (fuel, weather, terrain, season,
region, fire size, harmonized activity) to the best correction. The correction is spatial: ADJ(cell) =
exp(f(local fuel group, canopy, slope, wind, moisture, ...)); the member grid includes per-fuel-group multipliers
and a wind term so the training stage can fit f per cell later.

## Pipeline
| Step | Script | Output |
|---|---|---|
| Catalog | `scripts/fire_catalog.py data/training/catalog.csv` | events 2014-2025 (10 s) |
| Inputs per fire | `scripts/build_fire_case.py FIRE [--max-issues N] [--geo]` | `data/training/fires/FIRE/` |
| Members | `scripts/make_training_members.py OUT.txt FIRE... \| --ready` | lines for `elmfire_forecast.sh` |
| Runs | `xargs -P 12 -L 1 scripts/elmfire_forecast.sh < OUT.txt` (Tillicum: `hyak/submit_members.sh`) | `data/training/runs/FIRE/ISSUE/MEMBER` |
| Scores + features | `scripts/score_training_runs.py data/training/scores.csv [--prune]` | one row per run |
| Inputs for many fires | `scripts/build_training_inputs.sh` | see below |

Bulky outputs (`data/training/fires/`, `runs/`, `raw/`) are gitignored; catalog, scores and these notes are not.

## Catalog (scripts/fire_catalog.py)
- VNP14A1 fire cell-days on the 0.1 deg grid, 2014-2025; space-time connected components (8 neighbours, days at
  most 3 apart = gap tolerance 2 days). Persistent heat removed first: cells with fire in >= 20 distinct weeks over
  the 12 years or >= 8 weeks in one year (5,884 cells). A first version removed cells burning in >= 6 of 12 years:
  that deleted Sonoma/Napa (6-10 fire years per cell) and lost Kincade 2019.
- Kept: >= 4 fire days, >= 20 fire cell-days, peak day >= 30 VIIRS fire pixels, <= 120 days long, >= 2 fire pixels
  per cell-day, non-burnable fraction < 0.5. Rejections: 190,556 small, 270 weak peak, 44 non-burnable, 6 sparse.
- **365 events, 342 in the US** (`in_us` = 1; the other 23 are in British Columbia, mostly 2017/2018/2021, plus
  northern Mexico: the 0.1 deg grid reaches 50 N and LANDFIRE CONUS also covers a strip of Canada, so the fuel
  filter doesn't catch them). US events per year: 2014 16, 2015 32, 2016 22, 2017 48, 2018 41, 2019 4, 2020 50,
  2021 45, 2022 22, 2023 9, 2024 33, 2025 20; **2021-2024: 109** (the evaluation fire set for the harmonized vs
  non-harmonized comparison). All 365 per year: 2014 17, 2015 32, 2016 23, 2017 52, 2018 46, 2019 4, 2020 50, 2021 53, 2022 22,
  2023 11, 2024 34, 2025 21. Fire cell-days: median 46 (quartiles 30 / 85, 90th pct 166, max 851); fire days median
  15 (10 / 23, max 67); bbox longest side median 41 km (90th pct 69, max 162). 20-39 cell-days: 153 events,
  40-99: 136, 100-299: 64, 300+: 12. Regions: Northwest 108, California 90, Northern Rockies 69, Southwest 39,
  Great Basin 25, Rocky Mountain 24, Plains 5, East 3, Southeast 2 (SE/East fires are short and ag-dominated;
  mostly filtered). Dominant burnable group: timber-understory 150, grass-shrub 107, grass 44, timber-litter 36,
  shrub 28.
- Cross-checks (found; bbox contains the fire): Camp 2018 (2018-011, Nov 8-20), August Complex 2020 (2020-001,
  Aug 17-Oct 17, 849 cell-days), Creek 2020 (2020-004), SCU Lightning Complex 2020 (2020-008), Dixie 2021 (2021-001,
  Jul 14-Sep 17, 851), Caldor 2021 (2021-004), Carr 2018 (2018-001), Mendocino Complex 2018 (2018-002), Bootleg
  2021 (2021-009), Cameron Peak 2020 (split: 2020-036 Sep 2-7 and 2020-007 Sep 14-Oct 24; its August part is
  missing or below thresholds), Hermits Peak 2022 (2022-001), Smokehouse Creek 2024 (2024-010, Plains grass), Park
  2024 (2024-008), Walker 2019 (2019-002), Decker 2019 (2019-003), Kincade 2019 (2019-004). 2019 and 2023 were
  quiet in the lower 48 (4 and 11 events). York 2023 (Mojave desert) is not found. Ids are by size within a year
  and change if the catalog is rebuilt with other thresholds.
- Caveats: big complexes merge neighbouring fires into one event (August Complex); a long fire with a > 2-day
  detection gap (smoke, cloud) splits. Region is a lon/lat box approximation of GACCs.

## Inputs per fire (scripts/build_fire_case.py)
- Box: event bbox + 8 km margin, half-side 20-75 km; 37.5 m ELMFIRE cells up to a 60 km box, 75 m beyond
  (<= 2000 x 2000 cells). Larger events are cut to the 150 km box around the bbox centre ("truncated").
- VIIRS: VNP14IMG (+ VJ114IMG from 2018, VJ214IMG from 2024) granules day before start to day after end, from LAADS
  with IPv4 forced; shared in `data/training/raw/viirs_fire`. The granules cover a whole swath segment, so the CSV
  holds every lower-48 detection; fire_observations.py keeps the box.
- FEDS perimeters: the NRT collection covers 2025 on; the **FEDS archive collection
  (`public.eis_fire_lf_perimeter_archive`) covers 2018-2021** (checked: data on 2018-01-15..2021-12-01, none in
  2017 or 2022-2024). fire_observations.py now uses it, and degrades to detections only otherwise (2014-2017,
  2022-2024) or when FEDS is unreachable. So IoU exists for 2018-2021 and 2025 fires only.
- Cloud masks (obscured_cells.py, 03IMG geolocation ~180 MB/granule) are off by default (`--geo`). Pilot runs are
  scored without masks; on Widemouth the effect was small (MODELLING_LOG entry 9).
- **Fuels anachronism:** every fire uses LANDFIRE LF2025 fuels, so pre-2025 fires burn today's landscape, in which
  their own scars (and later fires') are already mapped as burned fuel. To fix later with LANDFIRE historical
  versions (LF 2014/2016 Remap, LF2019, LF2020, LF2022, LF2023) matched to the fire year.
- HRRR: 12Z run per issue, f00-f24 (HRRRv3, from 2018-07-12). Before that 12Z runs end at f15 (2014-2016) / f18
  (2016-2018): the last hour is held to +24 h (no later data used); flagged as hrrr_hours in case.json. Raw GRIB
  (~130 MB per issue) is deleted after each issue.
- Issues: 12Z on event days with >= 3 cells hot in the 6 h before and >= 10 observed hot cells on passes within
  24 h. `--max-issues N` keeps the N with most observed heat after issue.

## Members (scripts/make_training_members.py)
- Every run: 12Z issue, burning-edge leak-free start (OBSERVED=, ACTIVE_BUFFER 2), that issue's HRRR run, 24 h.
- `u-` uniform ADJ {0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0} x WSMULT {0.6, 1.0} x MOIST {+0, +4}, LH 90%: 28, the
  baseline (plain ELMFIRE with one tuned multiplier). 1.0 was added after the first pilot fire (2020-040, timber)
  had its optimum at the old top, 0.5.
- `lh30-adj0.1`: live herbaceous 30%.
- `g-` per-fuel-group multipliers (new elmfire_case.py overrides ADJ_GR / ADJ_SH / ADJ_TIMBER, absolute values; GR
  includes grass-shrub GS): levels 0.03 and 0.3 for each group on >= 10% of the fire's burned fine cells, other
  cells 0.1. 2, 4 or 8 members.
- `w-` wind term (new override ADJ_WIND_EXP=b, ADJ_WIND_REF default 10 mph): ADJ x (cell's mean 20-ft wind over
  the run / 10 mph)^b, factor clipped to 0.1-10; b = -1, +1 at ADJ 0.03 and 0.3. ELMFIRE's adj raster is static,
  so the run's mean wind stands for the cell's wind.
- 33-41 members per fire-issue. Case dirs `data/training/runs/FIRE/YYYYMMDDTHH/MEMBER`.

## Scoring (scripts/score_training_runs.py)
- heat_frames.py's observation operator and score, on overpasses in (issue, issue + 24 h] from the issue's cut
  observation file; pooled CSI per run; IoU at the last FEDS perimeter within 24 h; a `nogrowth` row per issue.
- Features f_* per fire-issue (fuel/canopy/terrain within 3 km of the hot cells, HRRR 24 h summary, gridMET day t
  and t+1 (t+1 is perfect-forecast), calendar, location, size at issue, hot cells, harmonized activity in the 5 x 5
  cells over 4 days).
- Runtime: elmfire.log mtime minus build.log mtime = ELMFIRE's own time on one core (the case build in Python adds
  ~2-3 s per run on top).

## Pilot (local, 2026-11-14/15; in-sample: settings judged on the same passes)
- Inputs built for 8 fires: 2020-040 (OR, timber-litter, Sep 2020), 2021-038 (AZ, grass, Jun 2021), 2021-031 (CA
  Beckwourth area, shrub, Jul 2021), 2020-029 (CA, timber-understory), 2018-029 (CA, grass-shrub), with 3 issues
  each; 2018-015 (WA, timber), 2020-008 (SCU Complex, 75 m cells), 2018-011 (Camp) with 2 issues each.
- Cost per fire (3 issues, 37.5 m, 20-26 km half-side): ~7 min wall (VIIRS 45-55 s, ~215-265 MB of granules for
  ~10-15 days; landscape 70-120 s, 11 MB; HRRR ~80 s and ~130 MB per issue, deleted after; at 7 builds at once).
  build_fire_case.py now prefetches the HRRR hours 8 at a time (not yet timed).
- Runs: 330 finished (2020-040: 3 issues x 35 members; 2021-038: 3 x 41; 2021-031: 2 x 41 + 20 of 41 on Jul 10).
  2020-029 and 2018-029 (members written, data/training/members_pilot.txt) and the 3 two-issue fires were not run
  when the work stopped.
- ELMFIRE time per run on one core (12 at once on this Ryzen): median 8.5 s, p90 35 s, max 114 s, mean 15 s. By
  fire: 2020-040 (1080^2 cells) mean 6 s; 2021-031 (1360^2) 15 s; 2021-038 (1400^2) 23 s. High ADJ (0.5-1.0) on
  big active fronts are the slow ones. Python case build adds ~2-3 s. Tillicum is ~1.7x slower per core (11 s vs
  6-7 s on the Widemouth Aug 4 case, measured in the container).
- **Best uniform multiplier, pooled CSI over each fire's issues** (curve at WSMULT 1, MOIST 0):

  | Fire | Fuel | Best uniform | CSI | ADJ 0.01 / 0.02 / 0.05 / 0.1 / 0.2 / 0.5 / 1.0 |
  |---|---|---|---|---|
  | 2020-040 | timber litter (82-91%) | ADJ 1.0, wind x0.6 | 0.427 | 0.01 / 0.03 / 0.13 / 0.20 / 0.35 / 0.40 / 0.40 |
  | 2021-031 | shrub/timber/grass mix | ADJ 0.5 | 0.270 | 0.00 / 0.04 / 0.08 / 0.18 / 0.24 / 0.27 / 0.24 |
  | 2021-038 | grass + timber litter | ADJ 0.5, moist +4 | 0.286 | 0.01 / 0.04 / 0.10 / 0.20 / 0.27 / 0.27 / 0.23 |

  Per issue the best uniform ADJ was 0.5 or 1.0 in 8 of 9 issues (0.2 once). No-growth scores 0 (state cells'
  smouldering has decayed by the first pass).
- **The signal for training is there, but it's between fires more than within them:** all three pilot fires
  want ADJ 0.2-1.0, while Widemouth 2 (2026, grass/shrub, MODELLING_LOG entries 5 and 8) wanted 0.02-0.05, a 10-50x
  difference. Before reading this as fuel or weather, check the pipeline difference: Widemouth's observations
  include NOAA-21 and its I4 fire band and FEDS NRT perimeters, the pilot's only S-NPP + NOAA-20 detections (2018-2021
  archive perimeters). Running Widemouth through build_fire_case.py (2026 is outside the catalog) would settle it.
- Per-fuel-group members: on the mixed fires, raising every group to 0.3 is best and lowering any one group costs
  CSI roughly in proportion to its share; no group wants a different level from the others at these two levels.
  On 2021-031, lowering shrub or timber to 0.03 costs more than lowering grass.
- Wind term: at ADJ 0.3, b = -1 (damping windy cells) beat b = +1 on 2020-040 (0.433 vs 0.336, the best member of
  the fire), tied on 2021-038 (0.279 vs 0.278) and was better on 2021-031 (0.264 vs 0.214). Wind x0.6 was best on
  2020-040 too. Consistent with entry 7 (HRRR 20-ft wind too strong), but three fires.
- IoU (FEDS archive perimeters, last one within 24 h): small differences between members (2020-040 0.58-0.86,
  barely above no growth's 0.52-0.91), as on Widemouth: CSI is the score that separates runs.
- Caveats: in-sample (settings judged on the passes they're scored on); no cloud masks; detections only (no fire
  band); 2021-031's Jul 10 issue has only 20 members; three fires.

## Full-scale estimate (not run)
- 342 US events; issues per fire capped at 6 (~5 on average after the hot-before / heat-after rule) ~ 1,700
  fire-issues x ~37 members ~ 63,000 runs. At the pilot's mean 15 s ELMFIRE + 3 s build = 18 CPU-s here, x1.7 on
  Tillicum = 31 CPU-s: ~540 CPU-h = 68 GPU-hours at 8 CPUs each = ~$61. Bigger fires (75 m, up to 2000^2 cells,
  long fronts) will cost more per run than the pilot's 20-26 km boxes; with a 2x allowance ~$120, under $200.
- Inputs: ~7 min and ~0.6 GB transferred per fire (3 issues); 342 fires x 6 issues ~ 70 GB through, ~10-15 GB kept
  (granules + landscapes + weather), ~6-8 h at 8 fires in parallel.
- Run storage: 14 MB per run as written (an 11 MB copy of the landscape in scratch/; score_training_runs.py
  --prune deletes it): ~0.9 TB unpruned, ~0.2 TB pruned for 63,000 runs.
- Not written: data/training/members_full.txt and fires_full.txt (shelved before the full list was made).
  `scripts/build_training_inputs.sh FIRES.txt` builds a list and writes the member list for every ready fire.

## Open issues
- The pilot's high optimum vs Widemouth's low one (above).
- Fuels anachronism (LF2025 for all years).
- HRRR 24 h only from 2018-07-12; earlier fires hold the last forecast hour (f15/f18) to +24 h.
- FEDS perimeters (IoU) only for 2018-2021 and 2025 on.
- heat_frames' smouldering state uses the state raster's guessed arrivals (a slight look-ahead, as before).
- fire_observations picks the largest FEDS fire in the box per snapshot, which in a big box can be a neighbour.
