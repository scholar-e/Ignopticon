#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "pyproj", "shapely>=2.1"]
# ///
"""Burn-scar fuel correction (BURNSCAR_FILE / BURNSCAR_YEARS / BURNSCAR_MULT) in elmfire_case.fire."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import elmfire_case as C


class BurnscarTests(unittest.TestCase):
    def test_mask_units(self):
        years = np.array([[1., 4., np.nan, 1.]])
        ids = np.array([[0, 1, 2, 3]])
        start = np.array([[False, False, False, True]])
        m, own = C.burnscar_mask(years, ids, start, 2)
        np.testing.assert_array_equal(m, [[True, False, False, False]])
        self.assertEqual(own, 1)
        # a raster cell shared by an own-start cell is excluded whole
        m, own = C.burnscar_mask(np.array([[1., 1.]]), np.array([[5, 5]]), np.array([[False, True]]), 2)
        self.assertFalse(m.any())
        self.assertEqual(own, 2)

    def test_fire_adj_and_fuel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tf = from_origin(0, 900, 100, 100)
            profile = dict(driver='GTiff', height=9, width=9, count=1, dtype='float32', crs='EPSG:3857', transform=tf)
            def raster(name, data, **kw):
                path = root / name
                with rasterio.open(path, 'w', **(profile | kw)) as dst:
                    dst.write(data)
                    if name == 'state.tif':
                        dst.update_tags(reference='2021-01-01T00:00:00Z')
                return str(path)
            landscape = raster('land.tif', np.full((8, 9, 9), 101, np.float32), count=8)
            state = raster('state.tif', np.full((1, 9, 9), -9999, np.float32), nodata=-9999)
            for name in ['ws', 'wd', 'm1', 'm10', 'm100']:
                raster(name + '.tif', np.ones((1, 9, 9), np.float32))
            obs = root / 'obs.json'
            obs.write_text(json.dumps(dict(reference='2021-01-01T00:00:00Z', frames=[dict(seconds=0, cells=[[.0076, .0076, 1, 'VIIRS']])],
                perimeters=[dict(time='2021-01-01T00:00:00Z', geometry=dict(type='Polygon',
                coordinates=[[[.001, .001], [.007, .001], [.007, .007], [.001, .007], [.001, .001]]]))])))
            base = ['INIT=feds', 'OBSERVED=' + str(obs)]
            yrs = np.full((1, 9, 9), -1, np.float32)       # -1 = no fire recorded
            yrs[0, 0, 0], yrs[0, 0, 1], yrs[0, 0, 2], yrs[0, 3, 3], yrs[0, 8, 8] = 1, 4, 8, 1, 2
            sc = raster('scar.tif', yrs)
            seen = []
            def capture(case, transform, crs, ls, weather, adj, phi, *a, **k):
                seen.append((adj.copy(), phi.copy(), ls['fbfm40'].copy()))
            def run(*extras):
                case = root / ('c%d' % len(seen)); case.mkdir()
                with patch.object(C, 'write_case', capture), patch.object(C, 'write_bsq'):
                    C.fire(landscape, str(root), state, '2021-01-01T00:00:00', 12, str(case), *base, *extras)
                return seen[-1], json.loads((case / 'case.json').read_text())
            opts = ['BURNSCAR_FILE=' + sc, 'BURNSCAR_YEARS=2']
            control, cj0 = run()
            self.assertIsNone(cj0['burnscar'])
            half, cj = run(*opts, 'BURNSCAR_MULT=0.3')
            adj = half[0]
            self.assertAlmostEqual(float(adj[0, 0]), .3, places=6)    # 1 y <= 2
            self.assertAlmostEqual(float(adj[8, 8]), .3, places=6)    # 2 y <= 2, outside the start area
            self.assertEqual(float(adj[0, 1]), 1)                     # 4 y > 2
            self.assertEqual(float(adj[0, 2]), 1)                     # 8 y > 2
            self.assertEqual(float(adj[3, 3]), 1)                     # inside the fire's own start area
            self.assertEqual(cj['burnscar']['recent_cells'], 2)
            self.assertEqual(cj['burnscar']['own_excluded_cells'], 1)
            np.testing.assert_array_equal(half[1], control[0] * 0 + control[1])   # phi unchanged
            np.testing.assert_array_equal(half[2], control[2])                    # fuels unchanged for m > 0
            wide, _ = run('BURNSCAR_FILE=' + sc, 'BURNSCAR_YEARS=5', 'BURNSCAR_MULT=0.3')
            self.assertAlmostEqual(float(wide[0][0, 1]), .3, places=6)
            self.assertEqual(float(wide[0][0, 2]), 1)
            zero, cj = run(*opts, 'BURNSCAR_MULT=0')
            self.assertEqual(int(zero[2][0, 0]), 99)                  # non-burnable
            self.assertEqual(int(zero[2][8, 8]), 99)
            self.assertEqual(int(zero[2][3, 3]), int(control[2][3, 3]))
            np.testing.assert_array_equal(zero[0], control[0])        # m = 0 leaves the multiplier alone
            comp, _ = run(*opts, 'BURNSCAR_MULT=0.3', 'ADJ=0.5')
            self.assertAlmostEqual(float(comp[0][0, 0]), .15, places=6)   # composes with ADJ
            self.assertAlmostEqual(float(comp[0][0, 1]), .5, places=6)
            for extras in [['BURNSCAR_FILE=' + sc], ['BURNSCAR_YEARS=2', 'BURNSCAR_MULT=.3'], opts + ['BURNSCAR_MULT=-1']]:
                with self.assertRaises(ValueError), patch.object(C, 'write_case', capture), patch.object(C, 'write_bsq'):
                    C.fire(landscape, str(root), state, '2021-01-01T00:00:00', 12, str(root / 'bad'), *base, *extras)
            # a coarse (300 m) raster: every raster cell touches the start area, so nothing is down-weighted
            coarse = raster('coarse.tif', np.ones((1, 3, 3), np.float32), height=3, width=3, transform=from_origin(0, 900, 300, 300))
            none, cj = run('BURNSCAR_FILE=' + coarse, 'BURNSCAR_YEARS=2', 'BURNSCAR_MULT=0.3')
            self.assertEqual(cj['burnscar']['recent_cells'], 0)
            self.assertGreater(cj['burnscar']['own_excluded_cells'], 0)


if __name__ == '__main__':
    unittest.main()
