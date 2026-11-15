# Active front calibrated on burning-edge hotspots (MODELLING_LOG.md entry 18)

Script: scripts/front_calibrate.py (method in its docstring). elmfire_case.py gained FRONT_KM=D (detections within
D km of the edge), FRONT_RFILE=path + FRONT_R=R (edge cells where the hindcast correction r >= R also burn) and
FRONT=whole; default INIT=feds front unchanged (d6_375m reproduces physics3 adj1 on Widemouth exactly: growth IoU
0.082, area ratio 1.29). Per-fire results: data/perimeter_batch/front/FIRE/front_FIRE.json (ELMFIRE cases and r
rasters there are local only). Figures: data/perimeter_batch/figures/front_FIRE.png.

Rerun (cached cases are reused): uv run --script scripts/front_calibrate.py map20260802-66510 2021-038 2021-031 2020-008

Rules (10): d6_375m (physics3 default), dH_D for H in {6,12,24} h and D in {0.75,1.5} km, d6_0.75+r0.8,
d6_0.75+r1.0, whole. A rule that selects no edge cell falls back to the whole band.
Calibration (past only): hindcast T-12 h -> T from the FEDS perimeter at T-12 h with the forecast's start mode;
score = hotspot CSI (VIIRS cells in (T-12, T] centred outside the start perimeter, one 375 m cell tolerance, no
cloud mask) + growth IoU vs FEDS T. r-rules are calibrated with r_prev from a whole-edge hindcast (T-24, T-12],
forecast with r from the whole-edge (T-12, T] hindcast; not eligible on Widemouth (no FEDS at T-24 h).
Oracle = best rule by the same score on the forecast window (in-sample).

Fires: Widemouth 66510 (UT, T 2026-08-02 12Z, physics3 inputs: sub-hourly HRRR, EMC/lagged/gridMET moisture);
pilot fires with FEDS at T-24/-12/0/+12 h, local inputs and real growth: 2021-038 AZ grass/timber (T 2021-06-27
12Z, 245 -> 312 km2), 2021-031 CA shrub (2021-07-09 12Z, 213 -> 304 km2), 2020-008 CA grass/shrub (2020-08-19
12Z, 464 -> 569 km2). Pilot weather is the older hourly HRRR (wrfsfc, m10 = m1 + 1, m100 = m1 + 2), not physics3;
hindcast weather = previous day's 12Z run, leads 12-24 h. 2020-040 OR timber skipped (52.0 -> 52.7 km2).

Chosen (calibration) / oracle: Widemouth d6_375m (tie: all rules except d24 fall back to whole edge at 00Z, no
detections in the 6-12 h before; tie goes to the default) / d6_1.5; 2021-038 d6_0.75+r0.8 / d6_1.5;
2021-031 d6_0.75+r1.0 / d6_0.75+r0.8 (identical scores); 2020-008 whole / whole.

+12 h vs FEDS (growth IoU / whole IoU / area ratio / boundary mean m / hotspot CSI):
| fire | chosen | physics3 front d6_375m | whole edge | no growth | oracle (in-sample) |
| --- | --- | --- | --- | --- | --- |
| Widemouth | 0.082 / 0.701 / 1.29 / 1071 / 0.031 | same | 0.185 / 0.639 / 3.14 / 1412 / 0.015 | 0 / 0.814 / 0 / 743 | 0.194 / 0.720 / 1.78 / 1028 / 0.025 |
| 2021-038 | 0.086 / 0.715 / 0.79 / 1119 / 0.008 | 0.044 / 0.718 / 0.59 / 981 / 0.010 | 0.089 / 0.673 / 1.22 / 1202 / 0.005 | 0 / 0.784 / 0 / 555 | 0.094 / 0.716 / 0.82 / 1194 / 0.007 |
| 2021-031 | 0.130 / 0.707 / 0.34 / 1063 / 0.102 | 0.079 / 0.703 / 0.20 / 1040 / 0.093 | 0.124 / 0.696 / 0.40 / 1050 / 0.089 | 0 / 0.699 / 0 / 1251 | = chosen |
| 2020-008 | 0.162 / 0.510 / 5.38 / 5695 / 0.054 | 0.060 / 0.515 / 3.40 / 5990 / 0.084 | = chosen | 0 / 0.813 / 0 / 531 | = chosen |
| mean | 0.115 / 0.658 / 1.95 / 2237 / 0.048 | 0.066 / 0.659 / 1.37 / 2270 / 0.054 | 0.140 / 0.629 / 2.53 / 2340 / 0.041 | 0 / 0.778 / 0 / 770 | 0.145 / 0.663 / 2.08 / 2245 / 0.047 |
Fixed d6_1.5 (no calibration) for comparison: mean 0.129 / 0.660 (growth / whole IoU).

Reading: a wider front than the physics3 default raises growth IoU on every fire (mean 0.066 -> 0.115 chosen,
0.129 fixed d6_1.5, 0.145 oracle) at unchanged whole IoU; calibration matched the oracle on 2 fires, was close on
2021-038, and missed on Widemouth only because the 00Z hindcast cannot tell the rules apart. The r-rules were
picked on both fires where eligible, but beat plain d6_0.75 by little. No rule beats no growth on whole IoU (0.66
vs 0.78), and 2020-008 overshoots (area ratio 3.4-5.4) whatever the front. Widemouth's N/S breakouts are still
mostly missed (figure): even the whole-edge and r >= 0.8 fronts (growth IoU 0.185 / 0.168) spread little there.

Caveats: 4 fires, one issue time each; hotspot targets are few on Widemouth (4 calibration, 29 forecast) and on
2020-008 dominated by a separate complex to the west (outside the FEDS polygon, burned by all rules as outside hot
cells), so CSI there says little about the front; no cloud mask; 375 m observations; score weights untuned.
