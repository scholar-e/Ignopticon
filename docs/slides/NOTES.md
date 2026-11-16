# Speaker notes

1. Ignopticon: an open wildfire map. Every active fire gets a verified 12-hour forecast, and sits on a consistent 26-year MODIS/VIIRS record.

2. MODIS and VIIRS disagree on how much is burning. Stitch them together and fire seems to drop off a cliff in 2012, which is the sensor, not the fire.

3. Daily 0.1 degree grid, MOD14A1, MYD14A1, VNP14A1, VJ114A1. A gradient boosting model gives the probability VIIRS would have seen fire in each MODIS-era cell-day. Harmonized history did not improve forecasts in five tests; its value is consistent baselines and honest trends.

4. Demo: open ignopticon.earth (demo day Aug 2, 2026), click a fire, slide to 12 h, turn on the worst case. Then Dead zones, add a repeater, and Navigate to the fire: compare Fastest with Best coverage.

5. Recall at observed area: the fairest test of where, because how much is removed. 95% confidence intervals above zero on Aug 3, 4 and 5.

6. Per-fire mean difference +0.086 (95% CI +0.042 to +0.131); pooled +0.066 (CI -0.001 to +0.143), so say "better on most fire-days", not "significantly better everywhere". PyreCast runs start up to 15 h earlier from its own footprint; ours starts from the FEDS perimeter at issue.

7. Everything is open: code, scores and the modelling log.
