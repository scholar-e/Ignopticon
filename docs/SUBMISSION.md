# Ignopticon: NASA Space Apps 2026 submission

Challenge: Harmonization of MODIS and VIIRS Hot Spots. Site: https://ignopticon.earth (fallback https://3-151-70-40.sslip.io).
Every number below comes from MODELLING_LOG.md (entry numbers in brackets). "Held out" = fires and days frozen before any model ran on them.

## One-line summary

A free, open wildfire map that puts 26 years of consistent MODIS/VIIRS fire history behind every active fire, and gives each one a
verified 12-hour spread forecast with a most-likely case and a worst case sized to catch 80% of the growth.

## The problem

- MODIS (2000-) and VIIRS (2012-) see fire differently: VIIRS has 375 m pixels against MODIS's 1 km, so it detects far more fire.
  Joining the two records naively makes fire activity appear to jump in 2012, which hides real trends.
- Fire spread forecasts exist, but few are open and almost none publish how well they do over the next 12 hours on real fires.

## What we built

1. Harmonized record, 2000-2026 (lower 48, daily, 0.1 degrees): MOD14A1/MYD14A1/VNP14A1/VJ114A1 on one grid. A gradient-boosting
   model gives, for each MODIS-era cell-day, the probability that VIIRS S-NPP would have seen fire, so the whole record is in
   VIIRS units. [10, 30]
2. History page: burning calendar (raw vs harmonized), this year against 2003-2025 normals, any past day's map with the FIRMS
   archive (all MODIS/VIIRS detections since 2000), and fire-season trends.
3. Fire watch page: for each active fire, the NASA FEDS perimeter, VIIRS hotspots, HRRR wind, containment from WFIGS, population
   inside the forecast (GHS-POP 2025), and a 12 h forecast:
   - Physics: ELMFIRE (Rothermel spread on LANDFIRE fuels, 30 m terrain, 15-minute HRRR wind), started from the FEDS perimeter.
   - Most likely: ELMFIRE's arrival map cut at the area a growth model predicts. [24]
   - Worst case: an 11-run ELMFIRE ensemble (wind direction, speed, fuel moisture varied), ordered by earliest arrival and sized
     by fire size to capture 80% of growth. [31, 35]
4. Radio dead zones and routes (fire watch, for crews): radio and phone coverage near the fire, modelled with SPLAT! (ITWOM) on
   terrain from FCC cell towers and user-added incident repeaters (NIRSC VHF/UHF, vehicle); routes over OpenStreetMap roads and
   trails that avoid the active edge, offering Fastest and Best coverage (least time out of contact). Each fire's package is
   cached on the device, so routing works without signal. Advisory only.
5. Technical page: methods, every score, and what did not work.

## Results

Harmonization (the challenge):
- Raw record: US fire activity appears to fall by 870 cell-days per year (p = 0.004), with a step down of 15,915 at 2012
  (p < 0.001), an artefact of the sensor change.
- Harmonized: trend -55 per year (p = 0.75), 2012 step -1,313 (p = 0.37). The false trend and the step disappear. [30]
- Negative result, reported: harmonized history did not improve the forecasts in five tests. Its value is a consistent baseline
  ("is this week unusual?") and honest long-term trends, not spread prediction. [24, 26, 28]

Forecast skill, held out, uncontained fires, truth = FEDS perimeter 12 h later:
- Where the fire grows (each method given the true growth area), 97 fire-days on Aug 3, 4, 5 2026: ELMFIRE captures 0.324 of
  the growth, a distance buffer 0.246, a downwind rule 0.218. ELMFIRE is ahead of both on every day, 95% CIs above zero. [33]
- By fire size: tie with the buffer on small fires (< 10 km2), double the buffer on large fires (0.26 vs 0.12). [36]
- Worst case, graded on capture: 90.5% of observed growth inside it on 7 held-out fires. [35]
- Against an operational system: PyreCast/Pyregence (operational ELMFIRE), same fires, same truth, same valid time, Aug 12-14,
  33 fire-days: growth located at equal area 0.328 ours vs 0.262 PyreCast median vs 0.267 buffer; ours better on 25 of 33
  fire-days (per-fire mean difference +0.086, 95% CI +0.042 to +0.131). [38]

## How it compares

- 12 h physics forecasts from satellite perimeters are standard (Pyregence, Technosylva, FARSITE). Ours is not a new fire model.
- What is different: it is open, free and offline-capable; every forecast is scored in public against NASA FEDS on fires frozen
  before the run; it says where physics beats simple rules and where it does not; the worst case is graded on capture; and it
  sits on a 26-year harmonized MODIS/VIIRS baseline. We found no public 12 h skill benchmark to compare with, so we built one.

## Limits

- Forecasts are unsuppressed spread potential; firefighting is not modelled. Scored on uncontained fires only.
- Few issue days (Aug 2-5 and 12-14, 2026); PyreCast runs start earlier than ours from their own footprint; FEDS edges are uncertain by about 375 m; fire-days are not independent.
- Fuel moisture is estimated, not measured; US only.
- Research output, not an official forecast: not for evacuation or tactical decisions.

## NASA data used

MOD14A1, MYD14A1 (MODIS), VNP14A1, VJ114A1 (VIIRS), FIRMS archive and near-real-time hotspots, FEDS fire perimeters (VEDA).
Other: LANDFIRE fuels and elevation, USGS 3DEP, NOAA HRRR, gridMET, NIFC WFIGS, GHS-POP 2025 (EC JRC).

## 2-minute demo script

| Time | Screen | Say |
|---|---|---|
| 0:00 | Fire watch, Aug 2 2026, US view | "MODIS and VIIRS see fire differently. We harmonized 26 years of both and built an open fire map on top." |
| 0:15 | Click Sinlahekin (Okanogan Co., WA) | "Each active fire: NASA FEDS perimeter, VIIRS hotspots, wind, containment." |
| 0:30 | Forecast slider 3, 6, 12 h; worst case on | "A 12-hour ELMFIRE forecast: most likely, and a worst case built from 11 weather variants, sized to catch 80% of growth." |
| 0:50 | Dead zones on, Navigate to the fire | "For crews: where radios and phones won't work near the fire, and a route that keeps them in contact." |
| 1:05 | Technical page, verification tables | "We score every forecast on fires frozen before the run. On 97 fire-days, ELMFIRE finds a third more growth than a simple buffer, and on the same fires it located growth better than the operational PyreCast forecast on 25 of 33 fire-days." |
| 1:25 | History page, raw vs harmonized toggle, trends card | "Without harmonization, US fire activity seems to fall off a cliff in 2012. That's the sensor change. Harmonized, the false trend disappears." |
| 1:45 | Fire watch, "this week vs 2003-2025" | "So every fire gets an honest baseline: is this week unusual? Open, verified, free. Ignopticon." |

## Team

Babylonian Belugas.
