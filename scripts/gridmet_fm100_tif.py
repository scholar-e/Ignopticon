#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "h5py", "rasterio"]
# ///
"""One day of gridMET 100-h dead fuel moisture (%) from data/raw/gridmet/fm100_2026.nc as a GeoTIFF for hrrr_weather.py --fm100.
Usage: gridmet_fm100_tif.py YYYY-MM-DD OUT.tif   e.g. 2026-08-02 data/perimeter_batch/gridmet_fm100_20260802.tif"""
import datetime as dt, sys
import h5py, numpy as np, rasterio
from rasterio.transform import from_origin

day, out = dt.date.fromisoformat(sys.argv[1]), sys.argv[2]
with h5py.File('data/raw/gridmet/fm100_2026.nc') as f:
    d = f['dead_fuel_moisture_100hr']
    i = int(round(f['day'][0])) and (day - dt.date(1900, 1, 1)).days - int(f['day'][0])
    raw = d[i]
    scale = float(np.ravel(d.attrs['scale_factor'])[0]); off = float(np.ravel(d.attrs['add_offset'])[0])
    fill = np.ravel(d.attrs['_FillValue'])[0]
    lat, lon = f['lat'][:], f['lon'][:]
a = np.where(raw == fill, -9999.0, raw.astype('float32') * scale + off).astype('float32')
dx, dy = abs(lon[1] - lon[0]), abs(lat[1] - lat[0])
t = from_origin(lon[0] - dx / 2, lat[0] + dy / 2, dx, dy)
with rasterio.open(out, 'w', driver='GTiff', height=a.shape[0], width=a.shape[1], count=1, dtype='float32', crs='EPSG:4326',
                   transform=t, nodata=-9999.0, compress='deflate') as o:
    o.write(a, 1)
    o.update_tags(source=f'gridMET fm100 (dead_fuel_moisture_100hr) {day}, data/raw/gridmet/fm100_2026.nc', units='percent')
print(out, 'index', i)
