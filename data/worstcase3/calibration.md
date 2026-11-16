# Worst-case calibration (calibration fires only)

G(size) = 0.171 * size_km2^0.696 km2 (log-log OLS, n=24 fires with growth). Target pooled recall 80%.
Class bounds km2: [10.0, 100.0]; calibration fires per class: [7, 6, 11]. Groups (classes pooled for calibration): [[0], [1], [2]]

## classes ['small'] (n=7 fires, observed growth 6.6 km2)

| w | m | pooled recall | false alarm km2 | target reached |
|---|---|---|---|---|
| 0.0 | 18.9 | 0.809 | 68.1 | True |
| 0.25 | 18.9 | 0.809 | 68.1 | True |
| 0.5 | 20.7 | 0.817 | 75.2 | True |
| 0.75 | 20.7 | 0.813 | 75.3 | True |
| 1.0 | 20.7 | 0.812 | 75.3 | True |

chosen: w=1.0, m=20.7

## classes ['medium'] (n=6 fires, observed growth 25.7 km2)

| w | m | pooled recall | false alarm km2 | target reached |
|---|---|---|---|---|
| 0.0 | 22.8 | 0.831 | 247.0 | True |
| 0.25 | 22.8 | 0.822 | 247.2 | True |
| 0.5 | 22.8 | 0.809 | 247.6 | True |
| 0.75 | 25 | 0.826 | 273.6 | True |
| 1.0 | 25 | 0.820 | 273.7 | True |

chosen: w=1.0, m=25

## classes ['large'] (n=11 fires, observed growth 419.4 km2)

| w | m | pooled recall | false alarm km2 | target reached |
|---|---|---|---|---|
| 0.0 | 33.1 | 0.807 | 3645.5 | True |
| 0.25 | 33.1 | 0.803 | 3647.1 | True |
| 0.5 | 36.4 | 0.836 | 4025.7 | True |
| 0.75 | 36.4 | 0.825 | 4030.3 | True |
| 1.0 | 36.4 | 0.821 | 4032.2 | True |

chosen: w=1.0, m=36.4

## Calibration fires

| fire              |   size_km2 |   obs_km2 | cls    |
|:------------------|-----------:|----------:|:-------|
| map20260802-63374 |     1178.6 |     17.24 | large  |
| map20260802-63683 |      260.8 |      5.79 | large  |
| map20260802-63962 |       86.6 |      0.9  | medium |
| map20260802-63969 |       18.9 |      0.13 | medium |
| map20260802-65258 |       16.4 |      0.31 | medium |
| map20260802-65401 |      316.9 |      0.41 | large  |
| map20260802-65486 |      505   |     50.79 | large  |
| map20260802-65489 |      151.9 |      4.19 | large  |
| map20260802-65718 |        0.3 |      0.06 | small  |
| map20260802-65726 |      826.1 |      0.1  | large  |
| map20260802-65995 |        5.6 |      0.94 | small  |
| map20260802-66555 |      128.4 |      9.25 | large  |
| map20260802-66737 |        6.7 |      0.44 | small  |
| map20260802-67569 |       47   |     10.02 | medium |
| map20260802-67572 |        9.5 |      3.41 | small  |
| map20260802-67614 |        4.8 |      1.38 | small  |
| map20260802-67765 |        7.6 |      0.3  | small  |
| map20260802-67766 |        5.7 |      0.07 | small  |
| map20260802-67815 |       25.9 |      9.75 | medium |
| map20260802-67816 |       18.7 |      4.63 | medium |
| 2020-008          |      511.4 |    106.01 | large  |
| 2021-031          |      213.1 |     91.25 | large  |
| 2021-038          |      244.9 |     67.17 | large  |
| map20260802-66510 |      296.7 |     67.15 | large  |
