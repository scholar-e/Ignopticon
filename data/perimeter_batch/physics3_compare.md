# physics3 vs fuelwind16 vs no growth (Aug 2 2026 12Z issue)

Fires with observed growth only; mean over fires (area ratio: median). fw16 = fuelwind16 rescored on the FEDS start footprint; fw16orig = as originally scored (375 m start). best_member is chosen in-sample.

## +12 h: 3 fires scored (5 with an outline)

| method | growth IoU | whole IoU | area ratio | boundary mean m |
|---|---|---|---|---|
| p3:adj0.5 | 0.023 | 0.765 | 0.516 | 474 |
| p3:adj1 | 0.028 | 0.670 | 1.287 | 797 |
| p3:adj1-wind0.7 | 0.030 | 0.709 | 0.886 | 672 |
| fw16:mean_of_members | 0.003 | 0.709 | 149 | 449 |
| fw16:best_member_in_sample(gr0.4-sh0.4-tm0.2-we0) | 0.005 | 0.801 | 74 | 197 |
| fw16:oracle_per_fire | 0.005 | - | - | - |
| fw16orig:mean_of_members | 0.023 | 0.634 | 18 | 479 |
| fw16orig:best_member_in_sample(gr0.4-sh0.4-tm0.6-we-1) | 0.034 | 0.712 | 8.158 | 271 |
| fw16orig:oracle_per_fire | 0.038 | - | - | - |
| no_growth | 0.000 | 0.933 | - | - |

## VIIRS CSI (heat_frames.py, overpasses within 12 h; mean over fires, n fires)

- p3:adj0.5: 0.041 (n=2)
- p3:adj1: 0.025 (n=2)
- p3:adj1-wind0.7: 0.027 (n=2)
- fw16 mean of members: 0.031, best member 0.053

## Per fire

| fire | h | init / front | FEDS acres | obs growth cells | no-growth IoU | growth IoU adj1 / adj0.5 / adj1-w0.7 | adj1 area ratio | fw16 best | 
|---|---|---|---|---|---|---|---|---|
| 11887 | 12 | feds/detections | 2563 | 0 | 1.000 | 0.000 / 0.000 / 0.000 | - | 0.000 |
| 20036 | 12 | feds/detections | 3871 | 72 | 0.986 | 0.003 / 0.007 / 0.004 | 343 | 0.010 |
| 2143 | 12 | feds/detections | 1707 | 1 | 1.000 | 0.000 / 0.000 / 0.000 | 0.000 | 0.000 |
| 331 | 12 | feds/whole_edge | 2996 | 0 | 1.000 | 0.000 / 0.000 / 0.000 | - | 0.000 |
| 66510 | 12 | feds/detections | 73058 | 47903 | 0.814 | 0.082 / 0.061 / 0.085 | 1.287 | - |
