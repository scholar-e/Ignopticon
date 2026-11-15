# Fuel/wind spread-correction calibration notes (Aug 2 2026 12Z batch)

- Old uniform10 results for 66510/11887/18872 kept as status.uniform10.json / scores.uniform10.json.
- New default member set fuelwind16 (perimeter_batch.py PERIMETER_MEMBERS): ADJ_GR {0.4,1} x ADJ_SH {0.4,1} x
  ADJ_TIMBER {0.2,0.6} x ADJ_WIND_EXP {-1,0}, ADJ_WIND_REF 10 mph (default). Covers all burnable FBFM40 groups
  (GR+GS, SH, TU+TL+SB), so the base ADJ (1) never applies to burnable cells.
- MIN_START_CELLS=20: if < 20 burnable 37.5 m cells start burning, the 375 m hot state cells are dilated by one
  375 m cell (max 2). Hot detections are already 375 m state cells (100 fine cells each), so this only triggers
  when detections sit on non-burnable fuel (likely 11887: 8 hot cells, no growth even at ADJ 1).
- Features (scores.json/status.json 'features'): fuel-group fractions of burnable unburned cells within 2 km of the
  initial burning cells, burnable fraction there, domain-mean HRRR 20-ft wind over 0-24 h, initial burning and
  burned acres. Scores gain area_ratio = predicted/observed growth cells.
- Inputs reused when data/training/fires/FIRE/case.json is ready and wx/ws.tif exists (REBUILD=1 forces).
- Slurm: 20 min limit, 8 members in parallel; 76 x 20 min x $0.90/h = $22.80 ceiling.
- Local Widemouth 66510 end-to-end OK (inputs rebuilt locally; 16 members; best 24 h growth IoU 0.140 gr1-sh1-tm0.6-we-1, area ratio 1.83; area ratio ~1 at gr0.4-sh1-tm0.6-we-1, IoU 0.095).
- Submitted Tillicum array 473848 (0-75%8, fuelwind16, MIN_START_CELLS=20); printed ceiling $22.80, spent before $2.49.
