# physics3 batch notes (Aug 2 2026 12Z, +1 to +12 h)

Member set `physics3` (perimeter_batch.py): ADJ 1; ADJ 0.5; ADJ 1 with WSMULT 0.7. HOURS=12 (SIMULATION_TSTOP
43200), DTDUMP 3600. LH_MOISTURE_CONTENT 90 and LW 60 (defaults) unchanged: no defensible live-fuel source at hand.

Start (elmfire_case.py INIT=feds): latest FEDS perimeter known at issue rasterised on the 37.5/75 m fuel grid.
Interior non-burnable (FBFM 99). Burning front = inner edge band EDGE_CELLS=2 fuel cells wide, restricted to cells
within one 375 m state cell of a VIIRS cell hot in the 6 h before issue (`active_mode: detections`); if none is
near the edge, the whole band burns (`whole_edge`). Recent hot cells more than one state cell outside the
perimeter also burn, as 375 m cells (`outside_hot_cells_375m`). No FEDS perimeter at issue: falls back to the
hot-cell start (`hotcells_fallback`, MIN_START_CELLS=20 as before). Recorded per fire in status.json `init_used`.
Scoring start = the same FEDS raster (plus any cell with arrival 0); fuelwind16 runs are rescored on it.

Weather (hrrr_weather.py --subhourly): HRRR 12Z run, wrfsubhf f00-f12, instantaneous 10 m U/V, 2 m T and dew
point every 15 min (49 bands), same grid rotation and 0.87 20-ft factor, bilinear from 3 km onto the 375 m state
grid; DT_METEOROLOGY 900 s from wx.json. wrfsubhf was available on AWS for 2026-08-02.
Moisture: m1 = Simard EMC from T and RH (RH from dew point, Magnus) per step. m10 = lagged EMC, exact
exponential update with tau 10 h, initial value at issue = previous-day gridMET fm100 (unscaled; a choice: at 12Z
dawn the 10-h fuels are near their daily maximum, and fm100 is a daily-mean-like value). m100 = gridMET fm100 for
2026-08-01 (4 km, data/raw/gridmet/fm100_2026.nc, extracted to gridmet_fm100_20260801.tif), bilinear, constant
over 12 h. No same-day gridMET (would leak).

Widemouth 66510 local check: FEDS polygon 73,057.9 acres vs fuel-grid raster 73,053.9 acres (-0.005%); edge band
4,830 cells, 2,036 active (detections mode, 1,922 burnable), 0 outside hot cells. Sub-hourly ws/wd at :00 equal the
hourly wrfsfc rasters exactly (max direction difference 0 deg at +6 h); domain mean wind 8.5 -> 14.7 mph, direction
194 -> 241 -> 228 deg; m1 5.0 -> 2.4 %, m10 5.4 -> 3.6 %, m100 5.4 % (hourly method gave m100 = m1 + 2 = 6.4 -> 4.5).
+12 h (FEDS 2026-08-03 00Z), growth IoU adj1 0.082 / adj0.5 0.061 / adj1-w0.7 0.085 (area ratio 1.29 / 0.52 / 0.89);
fuelwind16 rescored best 0.072, mean 0.052; whole IoU no growth 0.814 beats all (0.70-0.77).
VIIRS CSI is low for physics3 (0.03 vs 0.19 for fuelwind16): VIIRS still sees heat inside the perimeter, which a
start with a non-burnable interior and a 75 m front does not emit (heat_frames.py gives the interior only the
state raster's smoldering). It measures the start's heat footprint more than spread skill.

Hyak: integration 474488 (task 54, Widemouth), remaining 474489 (0-53,55-75%8, afterok). 12-min limit:
ceiling 76 x 12 min x $0.90/h = $13.68 (spent before $6.31). Site banner undercounts the disjoint array (55).

Collect and compare once 474488/474489 finish (check `sacct -j 474488,474489 -X -o JobID,State,Elapsed` on Hyak;
time-limited tasks keep a building/running status.json):
    hyak/collect_perimeters.sh            # pulls status*/scores*/envelopes* (incl. *.fuelwind16.json backups)
    uv run --script scripts/perimeter_compare.py   # writes data/perimeter_batch/physics3_compare.{md,json}
The comparison covers physics3 members, fuelwind16 rescored on the same FEDS start, fuelwind16 as originally scored,
and no growth at each FEDS outline within 12 h, along with VIIRS CSI and a per-fire table. The fuelwind16 baselines are
kept as map*/{status,scores,envelopes}.fuelwind16.* (102 files backed up locally and on Hyak before submission).
MODELLING_LOG.md entry 16 is still to be written (entry 15 is missing; 15 = the fuelwind16 batch).
