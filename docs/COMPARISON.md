# Harmonized vs non-harmonized training data: experiment spec

Goal: show whether harmonizing MODIS onto VIIRS (MODELLING_LOG.md entry 10) makes the pre-2012 record useful for training a fire model, by training the same next-day fire model on non-harmonized and on harmonized data and comparing them over a set of held-out fires, with visual comparisons.

## Task

Per 0.1° cell (the grid of docs/DATASETS.md section 2: 260 rows × 590 cols, lower 48) and day t: predict whether VIIRS S-NPP sees fire in the cell on day t+1. The target is always in VIIRS units, because the evaluation fires are judged by VIIRS.

## Training arms

The arms differ only in how the years before 2012 (MODIS only) enter the training data. From 2012 on, every arm uses VIIRS S-NPP (VNP14A1) directly.

| Arm | Years | Pre-2012 fire inputs and labels |
|---|---|---|
| `raw-pre` | 2003–2011 | MODIS Terra+Aqua fire presence, used as if it were VIIRS (the naive join) |
| `harm-pre` | 2003–2011 | harmonized `p_viirs` (out-base-13y): VIIRS-equivalent probability as input, soft label |
| `raw-all` | 2003–2019 | naive join: MODIS before 2012, VIIRS from 2012 |
| `harm-all` | 2003–2019 | harmonized before 2012, VIIRS from 2012 |
| `viirs-only` | 2012–2019 | VIIRS only (the reference: no pre-2012 data) |

The `-pre` arms isolate the harmonization effect: a model trained only on pre-2012 data, judged on VIIRS-era fires. The `-all` arms test whether the extra history helps, or hurts when not harmonized.

- **Validation:** 2020 (early stopping, threshold choice).
- **Test (excluded from fitting; exploratory evaluation):** 2021–2024, truth = VIIRS S-NPP fire presence on t+1, only on cell-days S-NPP saw clearly.

## Inputs

Inputs per cell-day t, the same for every arm except the fire channels:

- **Fire channels:** fire on t, t-1, t-2 in the arm's convention (VIIRS presence; MODIS presence for the raw arms before 2012; `p_viirs` for the harmonized arms before 2012), plus 3×3 and 5×5 neighbourhood sums, and days since fire within 3×3. **No FRP or pixel counts:** they aren't comparable across sensors (entry 10).
- **Clear-look flag** for day t, in the sensor of that period.
- **Weather (gridMET):**
  - day t: wind, u, v, vpd, erc, fm100, pr
  - day t+1: the same, as a perfect-forecast stand-in
- **Elevation:** elev, elev_sd.
- **Fuel:** the fuel.npz fractions, canopy means and dominant code.
- **Calendar and location:** day of year as sin and cos; lat and lon.

## Models

1. **Gradient-boosted trees** (LightGBM, as scripts/activity_model.py): fast, on this machine, every arm.
2. **U-Net / CNN** on 64×64-cell daily patches: same inputs and labels, trained on UW Tillicum GPUs, every arm.

Tillicum is for training only, not for running ELMFIRE predictions.

## Evaluation

- **Overall, test years:** AP, AUC, Brier, reliability, and expected vs observed fire cell-days. Also on "new activity" (fire on t+1 with no fire in the 3×3 on t-2..t).
- **Per fire:** the evaluation fire set is the large 2021–2024 events in data/training/catalog.csv, about 10–15 of them, chosen for spread of region and fuel and listed in data/compare/fires.csv. Per fire: AP and CSI at the validation-chosen threshold, and daily predicted vs observed fire area.
- **Visuals per fire:**
  - day-by-day panels: observed fire on t; observed on t+1; predicted probability on t+1 from `raw` and from `harm` (and `viirs-only`)
  - a summary chart of predicted vs observed daily fire cells
  - PNG files under data/compare/figures/, and UI payloads in the docs/DATASETS.md format, for the site
- **Honesty:**
  - The implemented harmonization fit uses 2012–2020 only. Test years 2021–2024 are excluded from fitting. First-round U-Net test scores were inspected before choosing the second-round three-seed setup, so this evaluation is exploratory rather than an untouched confirmatory test.
  - Everything is logged in MODELLING_LOG.md, marking held-out vs in-sample.

## Implementation (GBM arm, local)

- **Strict harmonization:** `data/harmonize/out-base-strict/`, `HARMONIZE_FEATURES=base`, fitted on 2012–2020 only and applied to 2003–2011. The `harm` arms use it, so no part of the pipeline saw 2021–2024. Scores: MODELLING_LOG.md entry 12.
- **Tensors:** `scripts/compare_dataset.py` writes `data/compare/tensors/` (not in git). The format is in [data/compare/tensors/README.md](../data/compare/tensors/README.md).
- **GBM:** `scripts/compare_gbm.py ARM...` trains LightGBM per arm, picks the max-CSI threshold on 2020 and writes the test-year predictions to `data/compare/preds/gbm_{arm}.npz`.
- **Evaluation:** `scripts/compare_eval.py data/compare/preds/{model}_*.npz` scores any model's predictions the same way (U-Net: `unet_{arm}.npz`, with `p` float16 (1461, 260, 590), `arm`, and `threshold` or `p_val`). Outputs:
  - `data/compare/results/{model}.md`, `.json`
  - `data/compare/figures/{model}/`: per fire `{id}_panels.png` and `{id}_series.png`, plus `overview_per_fire.png` and `reliability.png`
  - UI payloads (below)
- **Evaluation fires:** [data/compare/fires.csv](../data/compare/fires.csv). Names were matched by hand from bbox and dates, with a confidence column.

### UI payloads (predictions)

Same grid and PNG geometry as docs/DATASETS.md section 3 (590 × 260 px, pixel (x, y) = cell (col x, row y)). Written only for the days of the evaluation fires.

| Path | Contents |
|---|---|
| `data/compare/ui/{model}_{arm}/{YYYY-MM-DD}.png` | the prediction **for** that date, made on the day before |
| `data/compare/ui/{model}_fires/index.json` | model, arms, fires (id, name, first and last date), and the PNG encoding |
| `data/compare/ui/{model}_fires/{fire_id}.json` | one fire's daily series (format below) |

The PNG channels:

| Channel | Meaning |
|---|---|
| R | round(P(VIIRS S-NPP fire on the date) × 255) |
| G | 255 = S-NPP observed fire in the cell on the date (clear look), else 0 |
| B | 255 = S-NPP saw the cell clearly on the date, else 0 (where the prediction can be scored) |
| A | 255 = land, 0 = off grid (ocean, Canada, Mexico) |

Fire JSON:

```json
{"id": "2021-001", "name": "Dixie Fire", "region": "California", "fuel_group": "timber-understory",
 "bbox": [w, s, e, n], "crop": {"col0": .., "col1": .., "row0": .., "row1": ..}, "model": "gbm",
 "dates": ["2021-07-14", ...], "observed": [fire cells per date], "clear_frac": [...],
 "arms": {"raw-pre": {"threshold": 0.31, "metrics": {"ap": .., "csi": .., ...},
                      "expected": [sum of P over clear cells per date], "flagged": [cells with P >= threshold per date],
                      "png": "gbm_raw-pre/{date}.png"}, ...}}
```
