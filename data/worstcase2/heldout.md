# Held-out 12 (frozen), new size-calibrated worst case vs old upper vs control, +12 h vs FEDS

Calibration: {"small": {"w": 0.0, "m": 18.8604, "calib_recall": 0.8087420042643924, "calib_fa_km2": 68.06390624999999, "target_reached": true, "pooled_with": ["small"], "n_calib": 7}, "medium": {"w": 0.0, "m": 22.7607, "calib_recall": 0.8310005467468562, "calib_fa_km2": 246.99515625, "target_reached": true, "pooled_with": ["medium"], "n_calib": 6}, "large": {"w": 0.0, "m": 33.1479, "calib_recall": 0.8065310119111493, "calib_fa_km2": 3645.5414062499995, "target_reached": true, "pooled_with": ["large"], "n_calib": 11}}

## Uncontained (headline)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   7 |     65.55 |           0.889 |                1412.53  |                201.79  |         1470.81  |            0.473 |
| control   |   7 |     65.55 |           0.366 |                 242.932 |                 34.705 |          266.915 |            0.744 |
| old_upper |   7 |     65.55 |           0.273 |                 133.31  |                 19.044 |          151.173 |            0.761 |

## All 12

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |  12 |    66.561 |           0.878 |                1462.59  |                121.883 |         1521.04  |            0.446 |
| control   |  12 |    66.561 |           0.361 |                 246.385 |                 20.532 |          270.416 |            0.76  |
| old_upper |  12 |    66.561 |           0.269 |                 136.762 |                 11.397 |          154.675 |            0.784 |

## class small, all (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |     1.011 |           0.16  |                  22.996 |                  5.749 |           23.158 |            0.424 |
| control   |   4 |     1.011 |           0.049 |                   3.444 |                  0.861 |            3.493 |            0.752 |
| old_upper |   4 |     1.011 |           0.049 |                   3.444 |                  0.861 |            3.493 |            0.769 |

## class medium, uncontained (n=3)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   3 |     4.438 |           0.798 |                  91.37  |                 30.457 |           94.911 |            0.414 |
| control   |   3 |     4.438 |           0.817 |                  54.526 |                 18.175 |           58.151 |            0.561 |
| old_upper |   3 |     4.438 |           0.817 |                  54.526 |                 18.175 |           58.151 |            0.561 |

## class medium, all (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |     4.438 |           0.798 |                 118.436 |                 29.609 |          121.977 |            0.395 |
| control   |   4 |     4.438 |           0.817 |                  54.534 |                 13.634 |           58.16  |            0.647 |
| old_upper |   4 |     4.438 |           0.817 |                  54.534 |                 13.634 |           58.16  |            0.671 |

## class large, uncontained (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |    61.111 |           0.896 |                1321.16  |                330.29  |         1375.9   |            0.518 |
| control   |   4 |    61.111 |           0.333 |                 188.407 |                 47.102 |          208.763 |            0.881 |
| old_upper |   4 |    61.111 |           0.233 |                  78.784 |                 19.696 |           93.022 |            0.911 |

## class large, all (n=4)

| case      |   n |   obs_km2 |   recall_pooled |   false_alarm_km2_total |   false_alarm_km2_mean |   pred_km2_total |   whole_iou_mean |
|:----------|----:|----------:|----------------:|------------------------:|-----------------------:|-----------------:|-----------------:|
| new_upper |   4 |    61.111 |           0.896 |                1321.16  |                330.29  |         1375.9   |            0.518 |
| control   |   4 |    61.111 |           0.333 |                 188.407 |                 47.102 |          208.763 |            0.881 |
| old_upper |   4 |    61.111 |           0.233 |                  78.784 |                 19.696 |           93.022 |            0.911 |

## Per fire

| fire              | status      | cls    |   size_km2 | case      |   w |       m |   area_km2 |   obs_km2 |   pred_km2 |   hit_km2 |   false_alarm_km2 |   whole_iou |   members |
|:------------------|:------------|:-------|-----------:|:----------|----:|--------:|-----------:|----------:|-----------:|----------:|------------------:|------------:|----------:|
| map20260802-66433 | uncontained | large  |    368.252 | new_upper |   0 |  33.148 |    346.155 |    38.886 |    346.154 |    35.245 |           310.909 |       0.559 |         1 |
| map20260802-66433 | uncontained | large  |    368.252 | control   |   0 |  33.148 |    nan     |    38.886 |     82.641 |    12.421 |            70.22  |       0.794 |         1 |
| map20260802-66433 | uncontained | large  |    368.252 | old_upper | nan | nan     |    nan     |    38.886 |     45.745 |     8.024 |            37.721 |       0.845 |       nan |
| map20260802-65395 | uncontained | large  |    936.939 | new_upper |   0 |  33.148 |    663.009 |    10.783 |    663.007 |     8.657 |           654.351 |       0.59  |         1 |
| map20260802-65395 | uncontained | large  |    936.939 | control   |   0 |  33.148 |    nan     |    10.783 |    101.756 |     4.067 |            97.689 |       0.899 |         1 |
| map20260802-65395 | uncontained | large  |    936.939 | old_upper | nan | nan     |    nan     |    10.783 |     22.911 |     2.346 |            20.565 |       0.969 |       nan |
| map20260802-65682 | uncontained | medium |     34.639 | new_upper |   0 |  22.761 |     45.873 |     0     |     45.873 |     0     |            45.873 |       0.43  |         1 |
| map20260802-65682 | uncontained | medium |     34.639 | control   |   0 |  22.761 |    nan     |     0     |      5.341 |     0     |             5.341 |       0.866 |         1 |
| map20260802-65682 | uncontained | medium |     34.639 | old_upper | nan | nan     |    nan     |     0     |      5.341 |     0     |             5.341 |       0.866 |       nan |
| map20260802-65080 | uncontained | medium |     12.551 | new_upper |   0 |  22.761 |     22.632 |     4.337 |     22.632 |     3.442 |            19.19  |       0.439 |         1 |
| map20260802-65080 | uncontained | medium |     12.551 | control   |   0 |  22.761 |    nan     |     4.337 |     18.038 |     3.527 |            14.511 |       0.507 |         1 |
| map20260802-65080 | uncontained | medium |     12.551 | old_upper | nan | nan     |    nan     |     4.337 |     18.038 |     3.527 |            14.511 |       0.507 |       nan |
| map20260802-20036 | uncontained | medium |     15.663 | new_upper |   0 |  22.761 |     26.404 |     0.101 |     26.405 |     0.098 |            26.307 |       0.372 |         1 |
| map20260802-20036 | uncontained | medium |     15.663 | control   |   0 |  22.761 |    nan     |     0.101 |     34.772 |     0.098 |            34.674 |       0.31  |         1 |
| map20260802-20036 | uncontained | medium |     15.663 | old_upper | nan | nan     |    nan     |     0.101 |     34.772 |     0.098 |            34.674 |       0.31  |       nan |
| map20260802-66055 | uncontained | large  |    160.379 | new_upper |   0 |  33.148 |    194.105 |     5.06  |    194.105 |     5.06  |           189.045 |       0.466 |         1 |
| map20260802-66055 | uncontained | large  |    160.379 | control   |   0 |  33.148 |    nan     |     5.06  |     16.681 |     1.328 |            15.353 |       0.894 |         1 |
| map20260802-66055 | uncontained | large  |    160.379 | old_upper | nan | nan     |    nan     |     5.06  |     16.681 |     1.328 |            15.353 |       0.894 |       nan |
| map20260802-64816 | unmatched   | small  |      2.82  | new_upper |   0 |  18.86  |      6.634 |     0.866 |      6.635 |     0.125 |             6.51  |       0.086 |         1 |
| map20260802-64816 | unmatched   | small  |      2.82  | control   |   0 |  18.86  |    nan     |     0.866 |      3.357 |     0.042 |             3.315 |       0.113 |         1 |
| map20260802-64816 | unmatched   | small  |      2.82  | old_upper | nan | nan     |    nan     |     0.866 |      3.357 |     0.042 |             3.315 |       0.158 |       nan |
| map20260802-340   | unmatched   | small  |      6.549 | new_upper |   0 |  18.86  |     11.926 |     0.004 |      0     |     0     |             0     |       0.978 |         1 |
| map20260802-340   | unmatched   | small  |      6.549 | control   |   0 |  18.86  |    nan     |     0.004 |      0     |     0     |             0     |       0.978 |         1 |
| map20260802-340   | unmatched   | small  |      6.549 | old_upper | nan | nan     |    nan     |     0.004 |      0     |     0     |             0     |       0.999 |       nan |
| map20260802-19    | unmatched   | medium |     16.23  | new_upper |   0 |  22.761 |     27.066 |     0     |     27.066 |     0     |            27.066 |       0.339 |         1 |
| map20260802-19    | unmatched   | medium |     16.23  | control   |   0 |  22.761 |    nan     |     0     |      0.008 |     0     |             0.008 |       0.904 |         1 |
| map20260802-19    | unmatched   | medium |     16.23  | old_upper | nan | nan     |    nan     |     0     |      0.008 |     0     |             0.008 |       0.999 |       nan |
| map20260802-62041 | uncontained | large  |    135.519 | new_upper |   0 |  33.148 |    172.636 |     6.383 |    172.637 |     5.781 |           166.856 |       0.457 |         1 |
| map20260802-62041 | uncontained | large  |    135.519 | control   |   0 |  33.148 |    nan     |     6.383 |      7.685 |     2.541 |             5.144 |       0.938 |         1 |
| map20260802-62041 | uncontained | large  |    135.519 | old_upper | nan | nan     |    nan     |     6.383 |      7.685 |     2.541 |             5.144 |       0.938 |       nan |
| map20260802-53363 | unmatched   | small  |      3.561 | new_upper |   0 |  18.86  |      7.804 |     0.083 |      7.803 |     0.035 |             7.768 |       0.31  |         1 |
| map20260802-53363 | unmatched   | small  |      3.561 | control   |   0 |  18.86  |    nan     |     0.083 |      0.042 |     0.007 |             0.035 |       0.955 |         1 |
| map20260802-53363 | unmatched   | small  |      3.561 | old_upper | nan | nan     |    nan     |     0.083 |      0.042 |     0.007 |             0.035 |       0.955 |       nan |
| map20260802-53095 | unmatched   | small  |      4.177 | new_upper |   0 |  18.86  |      8.72  |     0.058 |      8.72  |     0.001 |             8.719 |       0.322 |         1 |
| map20260802-53095 | unmatched   | small  |      4.177 | control   |   0 |  18.86  |    nan     |     0.058 |      0.094 |     0     |             0.094 |       0.962 |         1 |
| map20260802-53095 | unmatched   | small  |      4.177 | old_upper | nan | nan     |    nan     |     0.058 |      0.094 |     0     |             0.094 |       0.962 |       nan |
