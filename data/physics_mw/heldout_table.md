# Learned front experiments: paired 12-hour ELMFIRE forecasts vs exact +12 h FEDS

Held-out: 12 frozen 2026-08-02T12 fires (manifest.json). dev: 3 development fires (in-sample for the front rules). Metrics from scripts/edge_pilot_score.py.

## heldout

Failures: none. Arms: no_growth, control, physics_mw. Uncovered fires (covered_fuel_cells = 0, learned arms = control): map20260802-66433, map20260802-65395, map20260802-65682, map20260802-65080, map20260802-20036, map20260802-66055, map20260802-64816, map20260802-340, map20260802-19, map20260802-62041, map20260802-53363, map20260802-53095.

### heldout: all fires (n=12)

| Arm | Growth IoU mean / med | Whole IoU mean / med | Area ratio mean / med | Boundary m mean / med | False growth mean / med | Hotspot hits | Growth IoU vs control (better/worse, mean diff) |
|---|---|---|---|---|---|---|---|
| no_growth | 0.0000 / 0.0000 | 0.9098 / 0.9761 | 0.00 / 0.00 | 156 / 21 | - | 0/23 | 0/8, -0.0580 |
| control | 0.0580 / 0.0238 | 0.7601 / 0.8965 | 36.97 / 2.71 | 1307 / 418 | 0.911 / 0.960 | 19/23 | - |
| physics_mw | 0.0522 / 0.0147 | 0.7483 / 0.8894 | 64.13 / 2.74 | 1415 / 386 | 0.879 / 0.976 | 20/23 | 2/6, -0.0058 |

### heldout: uncovered fires (n=12)

| Arm | Growth IoU mean / med | Whole IoU mean / med | Area ratio mean / med | Boundary m mean / med | False growth mean / med | Hotspot hits | Growth IoU vs control (better/worse, mean diff) |
|---|---|---|---|---|---|---|---|
| no_growth | 0.0000 / 0.0000 | 0.9098 / 0.9761 | 0.00 / 0.00 | 156 / 21 | - | 0/23 | 0/8, -0.0580 |
| control | 0.0580 / 0.0238 | 0.7601 / 0.8965 | 36.97 / 2.71 | 1307 / 418 | 0.911 / 0.960 | 19/23 | - |
| physics_mw | 0.0522 / 0.0147 | 0.7483 / 0.8894 | 64.13 / 2.74 | 1415 / 386 | 0.879 / 0.976 | 20/23 | 2/6, -0.0058 |

| Fire | Covered fuel cells | Edge cov | Band750 cov | Arm | Growth IoU | Whole IoU | Area ratio | Boundary m | False growth | Hotspots |
|---|---|---|---|---|---|---|---|---|---|---|
| map20260802-66433 | None | - | - | no_growth | 0.0000 | 0.9035 | 0.00 | 234 | - | 0/12 |
| map20260802-66433 | None | - | - | control | 0.1138 | 0.7937 | 2.13 | 464 | 0.850 | 10/12 |
| map20260802-66433 | None | - | - | physics_mw | 0.1108 | 0.7257 | 3.61 | 589 | 0.873 | 11/12 |
| map20260802-65395 | None | - | - | no_growth | 0.0000 | 0.9878 | 0.00 | 23 | - | 0/3 |
| map20260802-65395 | None | - | - | control | 0.0375 | 0.8994 | 9.44 | 205 | 0.960 | 3/3 |
| map20260802-65395 | None | - | - | physics_mw | 0.0231 | 0.8195 | 19.05 | 413 | 0.976 | 3/3 |
| map20260802-65682 | None | - | - | no_growth | - | 1.0000 | - | 0 | - | 0/1 |
| map20260802-65682 | None | - | - | control | 0.0000 | 0.8664 | - | 227 | 1.000 | 1/1 |
| map20260802-65682 | None | - | - | physics_mw | 0.0000 | 0.8846 | - | 186 | 1.000 | 1/1 |
| map20260802-65080 | None | - | - | no_growth | 0.0000 | 0.7342 | 0.00 | 214 | - | 0/2 |
| map20260802-65080 | None | - | - | control | 0.1871 | 0.5072 | 4.16 | 498 | 0.804 | 2/2 |
| map20260802-65080 | None | - | - | physics_mw | 0.1796 | 0.5958 | 2.22 | 344 | 0.779 | 2/2 |
| map20260802-20036 | None | - | - | no_growth | 0.0000 | 0.9858 | 0.00 | 12 | - | 0/0 |
| map20260802-20036 | None | - | - | control | 0.0028 | 0.3101 | 343.43 | 1321 | 0.997 | 0/0 |
| map20260802-20036 | None | - | - | physics_mw | 0.0016 | 0.2031 | 605.60 | 1975 | 0.998 | 0/0 |
| map20260802-66055 | None | - | - | no_growth | 0.0000 | 0.9685 | 0.00 | 78 | - | 0/3 |
| map20260802-66055 | None | - | - | control | 0.0650 | 0.8936 | 3.30 | 371 | 0.920 | 1/3 |
| map20260802-66055 | None | - | - | physics_mw | 0.0655 | 0.8943 | 3.27 | 358 | 0.920 | 1/3 |
| map20260802-64816 | None | - | - | no_growth | 0.0000 | 0.4382 | 0.00 | 1222 | - | 0/1 |
| map20260802-64816 | None | - | - | control | 0.0101 | 0.1135 | 3.88 | 8886 | 0.987 | 1/1 |
| map20260802-64816 | None | - | - | physics_mw | 0.0063 | 0.0889 | 6.06 | 9443 | 0.993 | 1/1 |
| map20260802-340 | None | - | - | no_growth | 0.0000 | 0.9993 | 0.00 | 0 | - | 0/0 |
| map20260802-340 | None | - | - | control | 0.0000 | 0.9779 | 0.00 | 1532 | - | 0/0 |
| map20260802-340 | None | - | - | physics_mw | 0.0000 | 0.9779 | 0.00 | 1532 | - | 0/0 |
| map20260802-19 | None | - | - | no_growth | - | 1.0000 | - | 0 | - | 0/0 |
| map20260802-19 | None | - | - | control | 0.0000 | 0.9042 | - | 2030 | 1.000 | 0/0 |
| map20260802-19 | None | - | - | physics_mw | 0.0000 | 0.9042 | - | 2030 | 1.000 | 0/0 |
| map20260802-62041 | None | - | - | no_growth | 0.0000 | 0.9542 | 0.00 | 60 | - | 0/1 |
| map20260802-62041 | None | - | - | control | 0.2204 | 0.9381 | 1.20 | 109 | 0.669 | 1/1 |
| map20260802-62041 | None | - | - | physics_mw | 0.1627 | 0.9512 | 0.50 | 81 | 0.580 | 1/1 |
| map20260802-53363 | None | - | - | no_growth | 0.0000 | 0.9622 | 0.00 | 19 | - | 0/0 |
| map20260802-53363 | None | - | - | control | 0.0595 | 0.9549 | 0.51 | 22 | 0.833 | 0/0 |
| map20260802-53363 | None | - | - | physics_mw | 0.0769 | 0.9619 | 0.19 | 19 | 0.545 | 0/0 |
| map20260802-53095 | None | - | - | no_growth | 0.0000 | 0.9837 | 0.00 | 7 | - | 0/0 |
| map20260802-53095 | None | - | - | control | 0.0000 | 0.9623 | 1.63 | 15 | 1.000 | 0/0 |
| map20260802-53095 | None | - | - | physics_mw | 0.0000 | 0.9727 | 0.83 | 11 | 1.000 | 0/0 |

