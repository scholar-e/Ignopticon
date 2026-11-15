# Analog q90 calibration on the growth test split (2021-24, 2903 event-days, 146 events, 312 in the largest-growth decile)

Analogs: K=100 nearest event-days in the arm's training pool (<= 2019) on standardized day-t features; q=0.9.
Target: coverage 0.90 (fraction of days with actual growth <= predicted q90). Lower pinball is better. CI: 1000 event-block bootstraps.

| Predictor | Coverage (CI) | Pinball q0.9 | Tail coverage | Tail sum pred/obs | Mean q90 pred |
|---|---|---|---|---|---|
| harm | 0.939 (0.929-0.949) | 0.2190 | 0.452 | 0.76 | 1.49 |
| harm@const | 0.950 (0.935-0.963) | 0.2914 | 0.538 | 0.60 | 2.00 |
| raw | 0.941 (0.928-0.951) | 0.2273 | 0.481 | 0.72 | 1.50 |
| raw@const | 0.950 (0.935-0.963) | 0.2914 | 0.538 | 0.60 | 2.00 |
| viirs-only | 0.932 (0.918-0.944) | 0.2329 | 0.391 | 0.65 | 1.41 |
| viirs-only@const | 0.950 (0.935-0.963) | 0.2914 | 0.538 | 0.60 | 2.00 |
| zero | 0.732 (0.707-0.755) | 0.4672 | 0.000 | 0.00 | 0.00 |

Paired pinball difference (negative = first arm better), event-block bootstrap:

- harm_minus_raw: -0.0082 (95% CI -0.0263 to +0.0034)
- harm_minus_viirs-only: -0.0139 (95% CI -0.0362 to +0.0003)
- raw_minus_viirs-only: -0.0056 (95% CI -0.0127 to +0.0008)
