# Growth model test table (2021-2024, same rows for every arm; grid point picked by validation MAE; mean over seeds)

| Variant | Arm | Config | Seeds | Test MAE | MAE of predicting 0 | Spearman (sd) | Zero-growth AUC | Mean pred / obs |
|---|---|---|---|---|---|---|---|---|
| t0 | harm | log1p/15 | 3 | 0.5603 | 0.5191 | 0.3942 (0.0017) | 0.7584 | 0.37 / 0.52 |
| t0 | raw | log1p/15 | 3 | 0.5628 | 0.5191 | 0.4095 (0.0042) | 0.7640 | 0.35 / 0.52 |
| t0 | viirs-only | log1p/15 | 3 | 0.5728 | 0.5191 | 0.3964 (0.0024) | 0.7545 | 0.35 / 0.52 |
| t1 | harm | log1p/15 | 3 | 0.5564 | 0.5191 | 0.3986 (0.0033) | 0.7596 | 0.37 / 0.52 |
| t1 | raw | log1p/15 | 3 | 0.5600 | 0.5191 | 0.4137 (0.0017) | 0.7673 | 0.35 / 0.52 |
| t1 | viirs-only | log1p/15 | 3 | 0.5713 | 0.5191 | 0.3946 (0.0029) | 0.7572 | 0.35 / 0.52 |

Single-feature Spearman baselines on test: g0 0.261, n_t 0.262, size_cells -0.039
Test rows 2903, events 146; rows per arm train+val: see runs/*.json.
