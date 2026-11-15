# AI handoff — 2026-11-15

## Latest user request

Score the held-out weighted/binary run and add the outward-band ADJ variant.
**Done** (MODELLING_LOG.md entry 22): FRONT_PMODE=weighted and ADJ_BAND_* in
scripts/elmfire_case.py, tests, dev + 12-fire held-out runs, tables in
data/edge_causal/front_weighted/heldout_table.md. Held out: binary +0.009
growth IoU on the 7 model-covered fires (2 better / 0 worse), weighted -0.006,
band750/2000 ~+0.0003 (2/3); no-growth best whole IoU. Not significant.

## Project and machine instructions

Read AGENTS.md. Run the machine check it specifies; the machine skill currently
disables local inference, so do not start LM Studio or local employees. Every
science change needs a dated MODELLING_LOG.md entry. Python scripts use uv/PEP
723 and logging to console and file with filename/line information. ELMFIRE is
CPU-only. Work locally; no Hyak spending is needed.

The working tree is extensively dirty from existing work. Preserve it. In
particular many relevant scripts and data/edge_causal are untracked, not absent.
Do not discard changes or overwrite previous experiment outputs.

## Completed work and evidence

1. Original spread-weighting pilot:
   `data/edge_causal/elmfire/{pilot.json,scores.json,comparison.md}`.
   Averaged three harm-all models and used clip(p / mean validation threshold,
   0.5, 2) over mapped coarse candidate footprints across the landscape, with
   multiplier 1 elsewhere. Kept baseline initial activation. Growth IoU improved
   on one of three fires; whole-perimeter IoU worsened on all three. This is NOT
   the new requested continuous front-weighting experiment.
2. Approved binary activation plan was implemented and evaluated:
   `data/edge_causal/activation/{pilot.json,scores.json,comparison.md}`.
   `scripts/elmfire_case.py` now accepts FRONT_PFILE + FRONT_PTHRESH for
   INIT=feds. Validates one band, matching shape/transform/CRS and finite covered
   probabilities in [0,1]; nodata means no coverage. Selects inner edge cells
   with recent hotspot support OR p >= threshold. Uncovered cells preserve
   baseline activation, including the whole-edge baseline when no detections
   support an edge. Empty selection falls back to whole edge. Recent heat
   outside the perimeter is preserved. No learned ADJ_FILE in these runs.
3. `scripts/edge_elmfire.py` currently runs that binary pilot. Three model files:
   `data/edge_causal/runs/harm-all-seed{0,1,2}/models/harm-all.txt`.
   Existing validation thresholds are in the corresponding results.json files;
   their mean is 0.049366665383179985. Inputs are previous complete UTC day's
   daily VIIRS history and weather. Candidate probabilities are mapped by fuel
   cell centre into 0.1-degree geographic footprints without interpolation;
   other cells are nodata -9999. Raster tags are copied into case provenance.
4. `scripts/edge_pilot_score.py [ROOT]` scores the three fixed pilot fires,
   including no-growth and active-edge fraction. It currently infers binary vs
   spread arms from ROOT.name; refactor/generalize for the next experiment.
5. `tests/test_front_probability.py` covers selection, hotspot preservation,
   nodata, outside heat, grid/probability validation, and changed phi with equal
   adj in binary mode. Run with `uv run tests/test_front_probability.py`.

Binary pilot results (control → activation):

| Fire | Growth IoU | Whole IoU | Boundary mean m | Hotspot hits | Active edge fraction |
|---|---|---|---|---|---|
| 2021-038 | .04355 → .04340 | .71767 → .71700 | 980.8 → 982.3 | 5/7 → 5/7 | .27187 → .28512 |
| 2021-031 | .07934 → .12675 | .70289 → .71598 | 1040.0 → 1086.9 | 30/70 → 46/70 | .40761 → .73562 |
| 2020-008 | .06047 → .10634 | .51531 → .49962 | 5989.6 → 5450.5 | 249/264 → 249/264 | .23374 → .36314 |

No-growth whole IoUs: .78423, .69946, .81334 respectively. Results are mixed,
three selected retrospective cases only; activation stays opt-in. Small
2020-008 control variation vs the earlier spread pilot (.06053) is recorded in
MODELLING_LOG.md rather than replacing the fresh result.

## Next

- Learned front rules stay opt-in; none is ready as a default. If continuing:
  more issue times/fires with real edge-model coverage (5/12 held-out fires had
  none), and a calibrated fine-scale probability before using p as a speed.
- Weighted multipliers on t=0 burning cells barely act (level-set limitation,
  data/edge_causal/front_weighted/NOTES.md); the outward band is the downstream
  form and showed no average effect.

## Broader evaluation: availability audit so far

Freeze model and weighting rule, fire IDs, issue times, eligibility and metrics
in a manifest BEFORE scoring. Split by fire, report coverage and failures,
include no-growth and false growth, and distinguish held out from model training
from untouched method-development cases. The original three are development
cases now. Record fuels anachronism and related reference limitations.

- Training years: 2003–2019; validation: 2020; model test: 2021–2024.
- data/training/catalog.csv has US region/fuel groups and events. Only two
  2021 fires currently have complete ready historical inputs, both in the
  development pilot. Existing 2018 fires are training-era, and other 2020 fires
  are validation-era: do not call those independent held-out examples.
- FEDS archive supports 2018–2021, not 2022–2024 according to local pipeline
  notes. New 2021 fires can be prepared with build_fire_case.py. Choose fixed
  issues with --issue; default issue selection ranks FUTURE hotspot counts and
  should not be used for a prospective-style evaluation.
- There are ~56 map20260802-* directories in data/training/fires with
  landscape.tif and fire-state.tif, and matching associated-observed.json under
  data/perimeter_batch/FIRE. Their old hourly wx/ and obs/ directories are mostly
  EMPTY (files were pruned). Do not assume an old ready case/status means weather
  is present. Only map20260802-66510 currently has complete weather and the
  original fire-observed.json. Others have associated observation files under
  data/perimeter_batch, which can be reused without redownloading VIIRS.
- 2026 map cases could provide a larger temporally held-out set with limited
  rebuilding: select a fixed geographically/fuel-varied subset using pre-issue
  information, exclude 66510 (prior development), rebuild forecast weather for
  2026-08-02T12:00, then compare all arms with that IDENTICAL weather. Existing
  physics controls were evaluated on this date, so describe them as held out
  from learned-transfer development, not wholly untouched system data.
- The current edge runner only reads tensors through 2024. However the raw
  2026 daily features already exist locally:
  data/harmonize/grid/VNP14A1_2026.npz: fire, clear, frp, etc., (365,260,590),
  day indices 1..365. data/harmonize/weather/weather_2026.npz has wind,u,v,vpd,
  erc,fm100,pr arrays (316,260,590). August 1 is available. Build ONLY the needed
  31-day history/clear/weather window directly, matching the existing tensor
  fire-presence and clear-presence conventions; no need for whole-year tensors
  or nonexistent 2027 weather. Test equivalence against an existing tensor year.
- static grid is E.static_grid(); E.day_rows(history,w,w,S,1e-6, clear_t=...)
  matches the current previous-day pilot. Models use feature-name selection.
- scripts/hrrr_weather.py can regenerate 12-hour forecast weather from the
  state raster; build_fire_case.py has download helpers. Retain forecast source
  consistency across arms; hourly rebuild is a different setup from old
  subhourly physics3 runs, so freshly pair controls rather than mixing them.
- Last observed disk availability was ~198 GB on the shared filesystem.
  Keep downloads bounded and avoid recreating unnecessary annual tensors.

No new multiplier code, data downloads, model loading, forecasts or science
log entries were made during the interrupted follow-up; only the above audit
and this handoff. Existing prior tests/results are as recorded above.
