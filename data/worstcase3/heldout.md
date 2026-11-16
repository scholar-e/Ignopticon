# Held-out 12 (frozen), new size-calibrated worst case vs old upper vs control, +12 h vs FEDS

Calibration: {"small": {"w": 1.0, "m": 20.719, "calib_recall": 0.8123667377398721, "calib_fa_km2": 75.27515625, "target_reached": true, "pooled_with": ["small"], "n_calib": 7}, "medium": {"w": 1.0, "m": 25.0037, "calib_recall": 0.8200656096227447, "calib_fa_km2": 273.72375, "target_reached": true, "pooled_with": ["medium"], "n_calib": 6}, "large": {"w": 1.0, "m": 36.4145, "calib_recall": 0.8207358622169759, "calib_fa_km2": 4032.1687500000003, "target_reached": true, "pooled_with": ["large"], "n_calib": 11}}

## Uncontained (headline)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   7 |    65.514 |           0.905 |                 1556.81 |                222.401 |         1616.08  |            0.451 |
| control   |   7 |    65.514 |           0.366 |                  242.73 |                 34.676 |          266.678 |            0.744 |
| old_upper |   7 |    65.55  |           0.273 |                  133.31 |                 19.044 |          151.173 |            0.761 |

## All 12

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |  12 |    66.504 |           0.894 |                1612.63  |                134.386 |         1672.06  |            0.425 |
| control   |  12 |    66.504 |           0.361 |                 246.181 |                 20.515 |          270.177 |            0.751 |
| old_upper |  12 |    66.561 |           0.269 |                 136.762 |                 11.397 |          154.675 |            0.784 |

## class small, all (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |     0.99  |           0.162 |                  26.09  |                  6.523 |           26.25  |            0.406 |
| control   |   4 |     0.99  |           0.048 |                   3.442 |                  0.861 |            3.49  |            0.726 |
| old_upper |   4 |     1.011 |           0.049 |                   3.444 |                  0.861 |            3.493 |            0.769 |

## class medium, uncontained (n=3)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   3 |     4.403 |           0.894 |                 100.645 |                 33.548 |          104.583 |            0.394 |
| control   |   3 |     4.403 |           0.816 |                  54.323 |                 18.108 |           57.915 |            0.561 |
| old_upper |   3 |     4.438 |           0.817 |                  54.526 |                 18.175 |           58.151 |            0.561 |

## class medium, all (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |     4.403 |           0.894 |                 130.378 |                 32.594 |          134.315 |            0.376 |
| control   |   4 |     4.403 |           0.816 |                  54.332 |                 13.583 |           57.923 |            0.647 |
| old_upper |   4 |     4.438 |           0.817 |                  54.534 |                 13.634 |           58.16  |            0.671 |

## class large, uncontained (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |    61.111 |           0.905 |                1456.16  |                364.041 |         1511.5   |            0.494 |
| control   |   4 |    61.111 |           0.333 |                 188.407 |                 47.102 |          208.763 |            0.881 |
| old_upper |   4 |    61.111 |           0.233 |                  78.784 |                 19.696 |           93.022 |            0.911 |

## class large, all (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |    61.111 |           0.905 |                1456.16  |                364.041 |         1511.5   |            0.494 |
| control   |   4 |    61.111 |           0.333 |                 188.407 |                 47.102 |          208.763 |            0.881 |
| old_upper |   4 |    61.111 |           0.233 |                  78.784 |                 19.696 |           93.022 |            0.911 |

## Per fire

| fire              | status      | cls    |   size_km2 | case      |   w |       m |   area_km2 |   obs_km2 |   pred_km2 |   hit_km2 |   false_alarm_km2 |   whole_iou |   members |
|:------------------|:------------|:-------|-----------:|:----------|----:|--------:|-----------:|----------:|-----------:|----------:|------------------:|------------:|----------:|
| map20260802-66433 | uncontained | large  |    368.252 | new_upper |   1 |  36.414 |    380.267 |    38.886 |    380.267 |    35.699 |           344.568 |       0.535 |        12 |
| map20260802-66433 | uncontained | large  |    368.252 | control   |   1 |  36.414 |    nan     |    38.886 |     82.641 |    12.421 |            70.22  |       0.794 |        12 |
| map20260802-66433 | uncontained | large  |    368.252 | old_upper | nan | nan     |    nan     |    38.886 |     45.745 |     8.024 |            37.721 |       0.845 |       nan |
| map20260802-65395 | uncontained | large  |    936.939 | new_upper |   1 |  36.414 |    728.346 |    10.783 |    728.348 |     8.792 |           719.556 |       0.567 |        12 |
| map20260802-65395 | uncontained | large  |    936.939 | control   |   1 |  36.414 |    nan     |    10.783 |    101.756 |     4.067 |            97.689 |       0.899 |        12 |
| map20260802-65395 | uncontained | large  |    936.939 | old_upper | nan | nan     |    nan     |    10.783 |     22.911 |     2.346 |            20.565 |       0.969 |       nan |
| map20260802-65682 | uncontained | medium |     34.639 | new_upper |   1 |  25.004 |     50.394 |     0     |     50.394 |     0     |            50.394 |       0.407 |        12 |
| map20260802-65682 | uncontained | medium |     34.639 | control   |   1 |  25.004 |    nan     |     0     |      5.341 |     0     |             5.341 |       0.866 |        12 |
| map20260802-65682 | uncontained | medium |     34.639 | old_upper | nan | nan     |    nan     |     0     |      5.341 |     0     |             5.341 |       0.866 |       nan |
| map20260802-65080 | uncontained | medium |     12.551 | new_upper |   1 |  25.004 |     24.863 |     4.337 |     24.862 |     3.873 |            20.99  |       0.43  |        14 |
| map20260802-65080 | uncontained | medium |     12.551 | control   |   1 |  25.004 |    nan     |     4.337 |     18.038 |     3.527 |            14.511 |       0.507 |        14 |
| map20260802-65080 | uncontained | medium |     12.551 | old_upper | nan | nan     |    nan     |     4.337 |     18.038 |     3.527 |            14.511 |       0.507 |       nan |
| map20260802-20036 | uncontained | medium |     15.912 | new_upper |   1 |  25.004 |     29.327 |     0.066 |     29.326 |     0.065 |            29.261 |       0.346 |        30 |
| map20260802-20036 | uncontained | medium |     15.912 | control   |   1 |  25.004 |    nan     |     0.066 |     34.536 |     0.065 |            34.471 |       0.31  |        30 |
| map20260802-20036 | uncontained | medium |     15.912 | old_upper | nan | nan     |    nan     |     0.101 |     34.772 |     0.098 |            34.674 |       0.31  |       nan |
| map20260802-66055 | uncontained | large  |    160.379 | new_upper |   1 |  36.414 |    213.234 |     5.06  |    213.234 |     5.06  |           208.174 |       0.442 |        12 |
| map20260802-66055 | uncontained | large  |    160.379 | control   |   1 |  36.414 |    nan     |     5.06  |     16.681 |     1.328 |            15.353 |       0.894 |        12 |
| map20260802-66055 | uncontained | large  |    160.379 | old_upper | nan | nan     |    nan     |     5.06  |     16.681 |     1.328 |            15.353 |       0.894 |       nan |
| map20260802-64816 | unmatched   | small  |      2.82  | new_upper |   1 |  20.719 |      7.288 |     0.866 |      7.287 |     0.125 |             7.162 |       0.081 |        12 |
| map20260802-64816 | unmatched   | small  |      2.82  | control   |   1 |  20.719 |    nan     |     0.866 |      3.357 |     0.042 |             3.315 |       0.113 |        12 |
| map20260802-64816 | unmatched   | small  |      2.82  | old_upper | nan | nan     |    nan     |     0.866 |      3.357 |     0.042 |             3.315 |       0.158 |       nan |
| map20260802-340   | unmatched   | small  |      6.549 | new_upper |   1 |  20.719 |     13.101 |     0.004 |      0     |     0     |             0     |       0.978 |        12 |
| map20260802-340   | unmatched   | small  |      6.549 | control   |   1 |  20.719 |    nan     |     0.004 |      0     |     0     |             0     |       0.978 |        12 |
| map20260802-340   | unmatched   | small  |      6.549 | old_upper | nan | nan     |    nan     |     0.004 |      0     |     0     |             0     |       0.999 |       nan |
| map20260802-19    | unmatched   | medium |     16.23  | new_upper |   1 |  25.004 |     29.733 |     0     |     29.732 |     0     |            29.732 |       0.319 |        12 |
| map20260802-19    | unmatched   | medium |     16.23  | control   |   1 |  25.004 |    nan     |     0     |      0.008 |     0     |             0.008 |       0.904 |        12 |
| map20260802-19    | unmatched   | medium |     16.23  | old_upper | nan | nan     |    nan     |     0     |      0.008 |     0     |             0.008 |       0.999 |       nan |
| map20260802-62041 | uncontained | large  |    135.519 | new_upper |   1 |  36.414 |    189.649 |     6.383 |    189.648 |     5.781 |           183.867 |       0.433 |        12 |
| map20260802-62041 | uncontained | large  |    135.519 | control   |   1 |  36.414 |    nan     |     6.383 |      7.685 |     2.541 |             5.144 |       0.938 |        12 |
| map20260802-62041 | uncontained | large  |    135.519 | old_upper | nan | nan     |    nan     |     6.383 |      7.685 |     2.541 |             5.144 |       0.938 |       nan |
| map20260802-53363 | unmatched   | small  |      4.054 | new_upper |   1 |  20.719 |      9.384 |     0.062 |      9.384 |     0.034 |             9.35  |       0.264 |        30 |
| map20260802-53363 | unmatched   | small  |      4.054 | control   |   1 |  20.719 |    nan     |     0.062 |      0.039 |     0.006 |             0.034 |       0.851 |        30 |
| map20260802-53363 | unmatched   | small  |      4.054 | old_upper | nan | nan     |    nan     |     0.083 |      0.042 |     0.007 |             0.035 |       0.955 |       nan |
| map20260802-53095 | unmatched   | small  |      4.177 | new_upper |   1 |  20.719 |      9.58  |     0.058 |      9.579 |     0.001 |             9.578 |       0.302 |        30 |
| map20260802-53095 | unmatched   | small  |      4.177 | control   |   1 |  20.719 |    nan     |     0.058 |      0.094 |     0     |             0.094 |       0.962 |        30 |
| map20260802-53095 | unmatched   | small  |      4.177 | old_upper | nan | nan     |    nan     |     0.058 |      0.094 |     0     |             0.094 |       0.962 |       nan |
