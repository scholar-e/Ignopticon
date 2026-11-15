# August 2 map perimeter batch

Scope: all 76 distinct FEDS IDs in the map's August 2 00Z/12Z snapshots.
Fixed issue: 2026-08-02 12:00 UTC. Each fire uses its own FEDS ID, including
initial footprint and scoring outlines. No selection by later fire activity.

Inputs: S-NPP/NOAA-20/NOAA-21 375 m detections when available, LANDFIRE LF2025
fuels/canopy, 3DEP terrain, HRRR forecast wind/dead-fuel moisture. Reuse matching
03IMG geolocation already downloaded for cloud flags; full-domain 03IMG and
L1B imagery are not downloaded blindly. Missing inputs are recorded.
Initial hotspots must lie within 3 km of the latest issue-known fire outline;
this is an association assumption to prevent neighboring fires seeding the run.

Member sets (PERIMETER_MEMBERS). Default `fuelwind16` (second batch, job 473848):
ADJ_GR {0.4,1} x ADJ_SH {0.4,1} x ADJ_TIMBER {0.2,0.6} x ADJ_WIND_EXP {-1,0}
(spread multiplier per fuel group, scaled by (cell mean wind / 10 mph)**exp),
with a minimum initial footprint MIN_START_CELLS=20 (hot 375 m cells grown by
one 375 m cell, at most twice, when fewer than 20 burnable cells start burning;
recorded as status.json `min_start`). Per-fire features (near-edge fuel-group
fractions, mean HRRR wind, initial size) are in status/scores `features`;
scripts/perimeter_calibrate.py does leave-one-fire-out member selection
(calibration.json, CALIB_NOTES.md). The first batch's `uniform10`: spread
multipliers .02/.1/.5/1 crossed with wind multipliers .8/1.2, plus (.02,1) and
(1,1); its results are kept as *.uniform10.json. Live herbaceous moisture 90%.
No new tuning. Member agreement 10/50/90% is **not calibrated probability**.
37.5 m cells for smaller domains, 75 m for larger domains. Truncated domains
are refused. Dormant/no-active-observation cases are not given invented fronts.

Outputs per fire on Hyak: status.json, runs/, scores.json, envelopes.geojson.
Scores at available FEDS times: whole-area IoU, growth-only IoU relative to the
issue footprint, no-growth baseline IoU, symmetric mean and p95 raster-boundary
distance. No time interpolation of truth. Missing outlines mean no score, not
zero skill. FEDS is derived from satellites, not independent airborne truth.

fuelwind16 batch: 20-minute limit, eight concurrent tasks, eight ELMFIRE runs at
a time; ceiling 76 x 20 min x $0.90/h = $22.80. Inputs reused when a ready
case.json with weather exists (REBUILD=1 forces). First batch below.
One Slurm task per fire, one billed GPU allocation with 8 CPUs/64GB, four
ELMFIRE CPU runs at a time. 45-minute limit each; all 76 tasks cost at most
$51.30 at $0.90/GPU-hour, excluding any explicitly submitted retries. Array
concurrency four. Known Widemouth task 54 is submitted first as an integration
check. No automatic retries or new budget commitments.

Historical extension: 342 catalog events next, then older MODIS-era cases.
Historical fuel vintages, shorter early HRRR horizons, missing mapped
perimeters, and causal MODIS-derived initialization require separate treatment.

Submitted jobs: integration `473163_54`; remaining 75 fires `473166`, with
`afterok:473163` and invalid-dependency cancellation. The site submission
banner undercounts the disjoint array expression; the explicit 76-task budget
above is the governing ceiling. Credentials remain in the user's private
Earthdata token file and are never stored in the repository or logs.

Collect compact products with `hyak/collect_perimeters.sh` on this machine.
It leaves raw inputs and ELMFIRE rasters on Hyak and writes summary.json.
Check Slurm accounting as well: interrupted/time-limited jobs may retain a
building/running status.json, which the collector counts as pending.
