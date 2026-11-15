# Comparison tensors (docs/COMPARISON.md)

Built by `scripts/compare_dataset.py`. Grid: 0.1°, 260 rows × 590 cols, row 0 = northern edge (lat 50 − 0.1·(row+0.5)), col 0 = western edge (lon −125 + 0.1·(col+0.5)); docs/DATASETS.md section 2.

All files are `np.savez_compressed`. Day index `d` = day of year − 1 (so `d = 0` is Jan 1). Every year file covers the whole calendar year (365 or 366 days). Inputs are for day t, labels and masks for t+1.

## Files

| Path | Years | Contents |
|---|---|---|
| `static.npz` | | `x` float16 (17, 260, 590), `names` (17,), `land` uint8 (260, 590) |
| `weather/YEAR.npz` | 2003–2024 | `w` float16 (days+1, 7, 260, 590), `names` = wind u v vpd erc fm100 pr. Index `d` = day t; index `days` = Jan 1 of the next year (so t+1 always exists) |
| `viirs/YEAR.npz` | 2012–2024 | fire file, VIIRS S-NPP convention (every arm uses these from 2012) |
| `raw/YEAR.npz` | 2003–2011 | fire file, raw MODIS convention (`raw-pre`, `raw-all`) |
| `harm/YEAR.npz` | 2003–2011 | fire file, harmonized convention (`harm-pre`, `harm-all`) |

Which fire file an arm reads: year ≥ 2012 → `viirs/`; before 2012 → `raw/` for `raw-*`, `harm/` for `harm-*` (`viirs-only` has no pre-2012 data).

## Fire file (`viirs|raw|harm/YEAR.npz`)

| Array | dtype | Shape | Meaning |
|---|---|---|---|
| `x` | float16 | (days, 11, 260, 590) | fire channels for day t, order below |
| `label` | float16 | (days, 260, 590) | fire on t+1 in the file's convention: 0/1 presence (viirs, raw), `p_viirs` in [0, 1] (harm, a soft label) |
| `mask` | uint8 | (days, 260, 590) | 1 = the label's sensor saw the cell clearly on t+1 and the cell is land. Train and score only where mask = 1 |
| `names` | str | (11,) | channel names |
| `year` | int | | |

`x` channels (the "fire" quantity f is VIIRS S-NPP presence in `viirs/`, MODIS Terra-or-Aqua presence in `raw/`, harmonized `p_viirs` in `harm/`):

| # | Name | Meaning |
|---|---|---|
| 0 | `fire_0` | f on t |
| 1 | `fire_1` | f on t-1 |
| 2 | `fire_2` | f on t-2 |
| 3–5 | `f3_0`, `f3_1`, `f3_2` | sum of f over the 3×3 cells on t, t-1, t-2 (0–9) |
| 6–8 | `f5_0`, `f5_1`, `f5_2` | sum of f over the 5×5 cells on t, t-1, t-2 (0–25) |
| 9 | `since` | days since fire in the 3×3 (0 = on t, capped at 30). Fire = 3×3 sum > 0 (viirs, raw), ≥ 0.5 (harm) |
| 10 | `clear` | 1 if the period's sensor saw the cell clearly on t (S-NPP in viirs/, Terra or Aqua in raw/ and harm/) |

Lags and days-since carry over Jan 1 from the previous year of the same convention when it exists (so not into 2003 or 2012: those start empty). Dec 31 of 2011 has mask 0 in raw/ and harm/ (no 2012 in those conventions); Dec 31 of 2024 uses VIIRS 2025.

Labels: viirs/ = VNP14A1 fire pixels > 0; raw/ = MOD14A1 or MYD14A1 fire > 0; harm/ = `p_viirs` from `data/harmonize/out-base-strict` (harmonization fitted on 2012–2020 only, so it never saw the 2021–2024 test years). Mask: viirs/ = VNP14A1 clear > 0; raw/ and harm/ = MODIS (Terra or Aqua) clear > 0, the stand-in for "VIIRS would have looked" (`p_viirs` is conditional on a clear VIIRS look). No FRP or pixel counts anywhere (not comparable across sensors).

## Assembled input stack (what `load_year` returns)

`scripts/compare_dataset.py` has `load_year(arm, year)` → `dyn` float16 (days, 27, 260, 590), `label` float32, `mask` bool, and `load_static()` → `x` float16 (17, 260, 590), names, land. Full input = 27 dynamic channels then 17 static channels (44 total):

| # | Name | Source |
|---|---|---|
| 0–10 | fire channels above | fire file `x` |
| 11–17 | `wind_t u_t v_t vpd_t erc_t fm100_t pr_t` | `weather w[d]` (gridMET on t) |
| 18–24 | `wind_t1 ... pr_t1` | `weather w[d+1]` (gridMET on t+1: perfect-forecast stand-in) |
| 25, 26 | `doy_sin`, `doy_cos` | sin, cos of 2π·doy/days, doy = d+1 |
| 27–28 | `elev`, `elev_sd` | static, m |
| 29–35 | `fuel_frac_nb gr gs sh tu tl sb` | static, LANDFIRE group shares |
| 36 | `fuel_fbfm40_mode` | static, dominant FBFM40 code (categorical; −1 none) |
| 37–40 | `fuel_cc ch cbh cbd` | static, canopy means |
| 41 | `fuel_valid` | static |
| 42, 43 | `lat`, `lon` | static, cell centre |

NaN: weather, elevation and fuel are NaN off land (ocean, Canada, Mexico, Great Lakes). The fire channels are never NaN. For a CNN: fill NaN with 0 (after normalizing) and use `land` / `mask`. Units: wind m/s, vpd kPa, erc index, fm100 %, pr mm/day.

## Arms (docs/COMPARISON.md)

| Arm | Train years | Files |
|---|---|---|
| `raw-pre` | 2003–2011 | raw/ |
| `harm-pre` | 2003–2011 | harm/ |
| `raw-all` | 2003–2019 | raw/ 2003–11, viirs/ 2012–19 |
| `harm-all` | 2003–2019 | harm/ 2003–11, viirs/ 2012–19 |
| `viirs-only` | 2012–2019 | viirs/ |

Validation 2020, test 2021–2024 (viirs/, hard labels, mask = S-NPP clear on t+1).

## Predictions for evaluation

`data/compare/preds/{model}_{arm}.npz` with `p` float16 (days, 260, 590), days = every day of 2021–2024 in order (1461 = 365+365+365+366; day i = 2021-01-01 + i, the prediction for fire on day i+1), and `arm`. `scripts/compare_eval.py PREDS...` scores and plots them.
