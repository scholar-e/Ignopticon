# Scores split by containment status at issue (2026-08-02T12:00Z, dates only)

Status from WFIGS dates (scripts/containment_aug2.py); no forecast was changed. Means are per-fire; n = fires. Area ratio = predicted / observed growth area (median of fires with observed growth). Growth IoU / recall are over fires with observed growth only (n_g).

## A. Frozen 12 held-out fires (+12 h vs FEDS)

| status      | case       |   n |   n_g |   growth_iou |   recall |   whole_iou |   area_ratio_med |   pred_km2 |   obs_km2 |
|:------------|:-----------|----:|------:|-------------:|---------:|------------:|-----------------:|-----------:|----------:|
| uncontained | no_growth  |   7 |     6 |        0.000 |    0.000 |       0.933 |            0.000 |      0.000 |    65.550 |
| uncontained | control    |   7 |     6 |        0.104 |    0.524 |       0.744 |            3.728 |    266.915 |    65.550 |
| uncontained | lower      |   7 |     6 |        0.113 |    0.198 |       0.897 |            0.642 |     40.927 |    65.550 |
| uncontained | upper_harm |   7 |     6 |        0.109 |    0.478 |       0.761 |            2.711 |    151.173 |    65.550 |
| uncontained | upper_raw  |   7 |     6 |        0.107 |    0.493 |       0.758 |            3.728 |    174.000 |    65.550 |
| unmatched   | no_growth  |   5 |     4 |        0.000 |    0.000 |       0.877 |            0.000 |      0.000 |     1.011 |
| unmatched   | control    |   5 |     4 |        0.017 |    0.033 |       0.815 |            1.071 |      3.502 |     1.011 |
| unmatched   | lower      |   5 |     4 |        0.000 |    0.000 |       0.877 |            0.000 |      0.000 |     1.011 |
| unmatched   | upper_harm |   5 |     4 |        0.017 |    0.033 |       0.815 |            1.071 |      3.502 |     1.011 |
| unmatched   | upper_raw  |   5 |     4 |        0.017 |    0.033 |       0.815 |            1.071 |      3.502 |     1.011 |

Fires: contained [], uncontained ['66433', '65395', '65682', '65080', '20036', '66055', '62041'], unmatched ['64816', '2-340', '02-19', '53363', '53095']

## B. Aug 2 forecasts with FEDS truth at +12 h (perimeter_batch/*/scores.json)

Control = member adj1 (physics3 fires) or gr1-sh1-tm0.6-we0 (fuelwind16 fires; closest to default). Lower/upper bounds and recall are not stored for these, only for section A. Includes the held-out fires that were run in perimeter_batch.

| status      |   n |   n_g |   control growth_iou |   control whole_iou |   control area_ratio_med |   no_growth whole_iou |   no_growth growth_iou |
|:------------|----:|------:|---------------------:|--------------------:|-------------------------:|----------------------:|-----------------------:|
| uncontained |   7 |     7 |                0.095 |               0.779 |                    1.287 |                 0.920 |                  0.000 |
| unmatched   |  12 |    10 |                0.066 |               0.741 |                    0.748 |                 0.778 |                  0.000 |

Caveats: status is from dates only (a fire with a real line but no date counts as uncontained); WFIGS match is by origin within ~5 km of the FEDS outline; small n, so differences are descriptive, not tested.
