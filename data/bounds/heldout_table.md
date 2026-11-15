# Lower / upper / control / no growth on the 12 frozen held-out fires (+12 h vs FEDS)

Observed growth total 67 km2 over 10 fires with growth. recall = share of observed growth area captured (mean of per-fire; pooled = area-weighted); false alarm = predicted growth outside observed growth. lower = areacon_t0 (honest, day-t features); lower_t1 = entry 24's t1.

| case             |   n_fires |   growth_iou_mean |   growth_iou_median |   recall_mean |   recall_pooled |   false_alarm_km2_mean |   false_alarm_km2_median |   pred_km2_total |   whole_iou_mean |
|:-----------------|----------:|------------------:|--------------------:|--------------:|----------------:|-----------------------:|-------------------------:|-----------------:|-----------------:|
| no_growth        |        12 |             0     |               0     |         0     |           0     |                  0     |                    0     |            0     |            0.91  |
| control          |        12 |             0.058 |               0.024 |         0.328 |           0.361 |                 20.532 |                    5.242 |          270.416 |            0.774 |
| lower            |        12 |             0.062 |               0     |         0.119 |           0.135 |                  2.659 |                    1.205 |           40.927 |            0.889 |
| lower_t1         |        12 |             0.061 |               0     |         0.123 |           0.136 |                  2.87  |                    1.523 |           43.502 |            0.888 |
| upper_harm       |        12 |             0.06  |               0.035 |         0.3   |           0.269 |                 11.397 |                    5.242 |          154.675 |            0.784 |
| upper_raw        |        12 |             0.059 |               0.035 |         0.309 |           0.283 |                 13.221 |                    5.242 |          177.501 |            0.782 |
| upper_viirs-only |        12 |             0.059 |               0.035 |         0.309 |           0.283 |                 13.221 |                    5.242 |          177.501 |            0.782 |
