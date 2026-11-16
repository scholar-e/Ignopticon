# Population layer (GHS-POP R2023A, 2025 epoch, 30 arcsec ~1 km, EC JRC, CC BY 4.0)
- Source zip (484 MB, not committed, data/population/raw/): https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_4326_30ss/V1-0/GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip
- Layer: `scripts/population_layer.py layer` -> earth/public/data/population/density.png (2 km blocks, 1.2 MB) + meta.json.
- Exposure: `scripts/population_layer.py exposure` -> exposure_20260802.json. Re-run it whenever earth/public/data/forecasts_bounds changes.
  Current = start polygon of the upper file; most likely = lower file's 12 h extent (fallback forecasts/); worst = upper file's 12 h extent. Cells weighted by covered fraction.
- Caveat: Butler Co., OH (11887) upper footprint equals its start (extent 2645 ac vs bound area 93 km2), so worst = current there.
