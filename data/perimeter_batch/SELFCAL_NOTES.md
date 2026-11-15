# Self-calibration from the fire's recent progression (Widemouth 66510, T = 2026-08-02 12Z)

Script: scripts/selfcal_adj.py (method in its docstring); elmfire_case.py gained ADJ_FILE=path (multiplies ADJ,
group and wind terms; default unchanged). Outputs: data/perimeter_batch/selfcal/66510/selfcal.json (rasters and
runs kept locally, not committed), figure data/perimeter_batch/figures/selfcal_66510.png.

Rerun:
    uv run --script scripts/selfcal_adj.py map20260802-66510 2026-08-02T12:00 \
      data/perimeter_batch/map20260802-66510/associated-observed.json data/perimeter_batch/selfcal/66510 \
      --physics3=data/perimeter_batch/map20260802-66510/runs/physics3/adj1/outputs \
      --figure=data/perimeter_batch/figures/selfcal_66510.png

Start: no FEDS perimeter at T-24 h (2026-08-01 12Z snapshot lacks 66510), so T-12 h (2026-08-02 00Z, 254.8 km2).
No VIIRS detections in the 6 h before 00Z, so INIT=feds burns the whole edge band (`whole_edge`).
Hindcast weather: HRRR wrfsubhf hourly runs 00Z-11Z, f00 + 15/30/45 min each (+60 min of 11Z), m10 lag re-run
over the stitched series from gridMET fm100 2026-08-01, m100 = that fm100. Observed in (00Z, 12Z]: FEDS 12Z
perimeter + 1,658 VIIRS cells (4 overpasses 08:48-10:30Z), 375 m squares.

Hindcast (ADJ=1) vs FEDS 12Z: modelled growth 42,459 ac vs observed 10,211 ac (area ratio 4.16, overshoot),
growth IoU 0.157, whole IoU 0.646 (no growth 0.860), boundary mean 1,332 m / p95 3,567 m.

Per-sector r (smoothed; sector 0 = N, 22.5 deg steps clockwise):
1.05 1.55 0.69 0.23 0.17 0.19 0.35 1.05 1.22 0.62 0.34 0.17 0.13 0.19 0.25 0.38; fire-wide median 0.35;
raster range 0.13-1.55. Model under-runs only N/NNE and S; overshoots strongly E and W.

+12 h vs FEDS 2026-08-03 00Z (single case, out-of-sample for the correction: it uses data up to T only):
| run | growth IoU | whole IoU | area ratio | boundary mean / p95 (m) |
| --- | --- | --- | --- | --- |
| selfcal | 0.057 | 0.799 | 0.25 | 715 / 3,505 |
| physics3 adj1 | 0.082 | 0.701 | 1.29 | 1,071 / 3,416 |
| no growth | 0 | 0.814 | 0 | 743 / 3,711 |

Diagnosis (sector advance from the T perimeter, p90): observed T->T+12 growth went NNE (sector 1, 16,273 cells,
4.8 km) and SE-S (sectors 6-8, 24,400 cells, 2.4-4.0 km), the sectors where the hindcast under-ran (r 1.05-1.55)
and none E (3-4) or W (12-14) where r was smallest. So the direction signal persisted. But both forecasts barely
move N or S (physics3 272 / 528 / 30 cells in sectors 1/7/8; selfcal 274 / 324 / 0): with the INIT=feds
detections start, the edge there is not burning or not spreading, and a spread multiplier of at most 1.55 cannot
create growth. The fire-wide shrink (median 0.35) then removes most of the E/W false growth (whole IoU up 0.10,
boundary mean down 356 m) and some of the small true growth with it (growth IoU down).
Asymmetry: the hindcast started from a whole burning edge (no recent detections) while the forecast starts from
detection-limited active edges, so part of the hindcast overshoot (and the low r) is a start effect that the
forecast start already removes.

Verdict: does not clearly help (growth IoU lower, still below no growth on whole IoU). Not wired into
perimeter_batch.py; no Hyak run. More promising next step: use the hindcast's direction signal for the start
(which edge segments burn) rather than only the spread rate, and match the hindcast start mode to the forecast's.
