# HRRR live wind frames (2026-08-02)

- Script: `scripts/hrrr_live_wind.py 2026-08-02` (~1 min, 445 MB of U/V byte ranges in data/raw/hrrr_live, gitignored).
- Frame at T (every 15 min, 96 frames) = run floor_hour(T) - 1 h, lead 60/75/90/105 min, from wrfsubhf01 (60) and
  wrfsubhf02 (75-105), instantaneous 15-min output. Runs Aug 1 23Z .. Aug 2 22Z (24). All 96 used sub-hourly files;
  hourly wrfsfcf fallback (nearest whole-hour lead, same run) was never needed. Per-frame run/lead/source in index.json.
- Grid: regular 0.1 deg, lon -125..-66, lat 50..24 (591 x 261), bilinear from the 3 km Lambert grid, grid-relative
  winds rotated to true north (angle = sin(lat_1)(lon - lon_0), lon_0 = 262.5 -> -97.5). 97% of points defined.
- Encoding: earth grib2json headers, values as base64 int8 at 0.25 m/s (+-31.75 m/s; 0 points clipped on Aug 2),
  -128 = undefined (outside HRRR). ~412 KB a frame (~200 KB gzipped); 96 frames 38 MB.
- Git: whole-hour frames only (24, ~10 MB); the 15-min frames are ignored by earth/public/data/weather/hrrr/.gitignore.
  The map falls back to the hour's frame (same run) when a 15-min file is missing. Rebuild with the script to get them.
- Check vs GFS 18Z (0.5 deg) at 18:00Z: Widemouth 38.79,-112.37 HRRR 5.7 m/s from 232 vs GFS 6.5 from 242; Oregon
  coast 2 vs 3 deg, Maine 184 vs 188 (far from lon_0, so the rotation sign is right). Light-wind points differ more.
- Map: products.js wind product uses HRRR for surface wind when /data/weather/hrrr/DATE/index.json has a frame within
  7.5 min; otherwise GFS. Steps: < > 15 min, << >> 3 h while on HRRR. Outside the HRRR grid the field is undefined
  (no GFS composite), so Canada/Mexico are blank on HRRR days.
