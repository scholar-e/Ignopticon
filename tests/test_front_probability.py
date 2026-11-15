#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "pyproj", "shapely>=2.1"]
# ///
"""Behavioral checks for opt-in learned front activation."""
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


class FrontTests(unittest.TestCase):
    def test_selection_and_missing_coverage(self):
        band = np.ones((1, 4), bool)
        baseline = np.array([[True, False, True, False]])
        p = np.array([[.01, .9, -9999, -9999]])
        covered = p != -9999
        active, info = C.probability_front(band, baseline, baseline, p, covered, .5)
        np.testing.assert_array_equal(active, [[True, True, True, False]])
        self.assertEqual(info['model_added_cells'], 1)
        self.assertFalse(info['whole_edge_fallback'])
        active, info = C.probability_front(band, band & False, band & False, p, np.ones_like(covered), 1)
        self.assertFalse(active.any())
        self.assertTrue(info['whole_edge_fallback'])
        active, _ = C.probability_front(band, band & False, band & False, p, covered, 1)
        np.testing.assert_array_equal(active, [[False, False, True, True]])


    def test_weighted_selection(self):
        band = np.ones((1, 7), bool)
        hotspot = np.array([[True, False, False, False, False, False, False]])
        baseline = hotspot.copy()
        p = np.array([[.3, 0, .2, 1, .001, -9999, -9999]])
        covered = p != -9999
        active, weight, info = C.probability_front_weighted(band, baseline, hotspot, p, covered)
        # hotspot keeps x1, p=0 inactive, .2/1 weighted, tiny p floored, uncovered follow baseline (inactive here)
        np.testing.assert_array_equal(active, [[True, False, True, True, True, False, False]])
        np.testing.assert_allclose(weight, [[1, 1, .2, 1, C.FRONT_PFLOOR, 1, 1]])
        self.assertEqual((info['hotspot_override_cells'], info['weighted_cells'], info['floored_cells'],
                          info['zero_probability_inactive_cells']), (1, 3, 1, 1))
        self.assertFalse(info['whole_edge_fallback'])
        # No detections anywhere: uncovered cells use the whole-edge baseline at x1.
        active, weight, info = C.probability_front_weighted(band, band & False, band & False, p, covered)
        np.testing.assert_array_equal(active, [[True, False, True, True, True, True, True]])
        self.assertTrue(info['uncovered_baseline_whole_edge'])
        # All covered with p=0 and no hotspot: explicit whole-edge fallback, never a zero multiplier.
        zero = np.zeros((1, 7))
        active, weight, info = C.probability_front_weighted(band, band & False, band & False, zero, band)
        self.assertFalse(active.any())
        self.assertTrue(info['whole_edge_fallback'])
        np.testing.assert_array_equal(weight, np.ones((1, 7)))

    def test_raster_validation_and_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tf = from_origin(0, 900, 100, 100)
            profile = dict(driver='GTiff', height=9, width=9, count=1,
                           dtype='float32', crs='EPSG:3857', transform=tf)
            def raster(name, data, **kw):
                path = root / name
                opts = profile | kw
                with rasterio.open(path, 'w', **opts) as dst:
                    dst.write(data)
                    if name == 'state.tif':
                        dst.update_tags(reference='2021-01-01T00:00:00Z')
                return str(path)
            landscape = raster('land.tif', np.full((8, 9, 9), 101, np.float32), count=8)
            state = raster('state.tif', np.full((1, 9, 9), -9999, np.float32), nodata=-9999)
            for name in ['ws', 'wd', 'm1', 'm10', 'm100']:
                raster(name + '.tif', np.ones((1, 9, 9), np.float32))
            obs = root / 'obs.json'
            obs.write_text(json.dumps(dict(reference='2021-01-01T00:00:00Z', frames=[dict(seconds=0,cells=[[.0076,.0076,1,'VIIRS']])],
                perimeters=[dict(time='2021-01-01T00:00:00Z', geometry=dict(type='Polygon',
                coordinates=[[[.001,.001],[.007,.001],[.007,.007],[.001,.007],[.001,.001]]]))])))
            prob = np.zeros((1, 9, 9), np.float32); prob[0, 2, 2] = .9
            pf = raster('p.tif', prob, nodata=-9999)
            C.read_front_probability(pf, (9,9), tf, rasterio.crs.CRS.from_epsg(3857))
            for kw in [dict(transform=from_origin(1,900,100,100)), dict(crs='EPSG:32610'), dict(count=2), dict(width=8)]:
                data = np.zeros((kw.get('count',1),9,kw.get('width',9)), np.float32)
                bad = raster('bad.tif', data, **kw)
                with self.assertRaises(ValueError):
                    C.read_front_probability(bad,(9,9),tf,rasterio.crs.CRS.from_epsg(3857))
            for value in [-.1, 1.1, np.nan, np.inf]:
                bad = raster('bad.tif', np.full((1,9,9),value,np.float32))
                with self.assertRaises(ValueError):
                    C.read_front_probability(bad,(9,9),tf,rasterio.crs.CRS.from_epsg(3857))
            captures=[]
            def capture(case, transform, crs, landscape, weather, adj, phi, *args, **kwargs):
                captures.append((adj.copy(), phi.copy()))
            nodata_file = raster('nodata.tif', np.full((1,9,9),-9999,np.float32), nodata=-9999)
            for extras in [[], ['FRONT_PFILE='+pf, 'FRONT_PTHRESH=0.5'],
                           ['FRONT_PFILE='+nodata_file, 'FRONT_PTHRESH=0.5']]:
                case = root / str(len(captures)); case.mkdir()
                with patch.object(C,'write_case',capture), patch.object(C,'write_bsq'):
                    C.fire(landscape,str(root),state,'2021-01-01T00:00:00',12,str(case),
                           'INIT=feds','OBSERVED='+str(obs),*extras)
            np.testing.assert_array_equal(captures[0][0],captures[1][0])
            self.assertFalse(np.array_equal(captures[0][1],captures[1][1]))
            np.testing.assert_array_equal(captures[0][1],captures[2][1])
            # Recent observed heat outside the perimeter survives both initializations.
            self.assertLess(captures[0][1][0,8],0)
            self.assertLess(captures[1][1][0,8],0)
            # Weighted mode: second hot cell at (1,8) supports edge cell (1,7); (0,8) stays outside heat.
            obs2 = root / 'obs2.json'
            o = json.loads(obs.read_text()); o['frames'][0]['cells'].append([.0076, .00674, 1, 'VIIRS'])
            obs2.write_text(json.dumps(o))
            base = ['INIT=feds', 'OBSERVED='+str(obs2)]
            # Invalid option combinations are rejected.
            for extras in [['FRONT_PMODE=weighted'], ['FRONT_PMODE=weighted','FRONT_PFILE='+pf,'FRONT_PTHRESH=0.5'],
                           ['FRONT_PMODE=fuzzy','FRONT_PFILE='+pf], ['FRONT_PMODE=binary'],
                           ['FRONT_PMODE=weighted','FRONT_PFILE='+pf,'FRONT=whole'],
                           ['FRONT_PMODE=weighted','FRONT_PFILE='+pf,'FRONT_R=0.5','FRONT_RFILE='+pf],
                           ['FRONT_PMODE=weighted','FRONT_PFILE='+pf,'INIT=hotcells']]:
                with self.assertRaises(ValueError), patch.object(C,'write_case',capture), patch.object(C,'write_bsq'):
                    C.fire(landscape,str(root),state,'2021-01-01T00:00:00',12,str(root/'bad'),*base,*extras)
            # Weighted probabilities: hotspot-supported edge (1,7) p=.3, p=0 on row 4, p=1 at (2,2), tiny at (1,1),
            # nodata on row 7 (uncovered: baseline, i.e. inactive away from detections).
            wp = np.full((1,9,9), .2, np.float32); wp[0,1,7] = .3; wp[0,4,:] = 0; wp[0,2,2] = 1; wp[0,1,1] = .001
            wp[0,7,:] = -9999
            wf = raster('w.tif', wp, nodata=-9999)
            adjfile = raster('adjfile.tif', np.full((1,9,9), 2, np.float32))
            def run(name, *extras):
                case = root / name
                C.fire(landscape,str(root),state,'2021-01-01T00:00:00',12,str(case),*base,*extras)
                with rasterio.open(case/'scratch'/'adj.bsq') as src: adj = src.read(1)
                with rasterio.open(case/'scratch'/'phi.bsq') as src: phi = src.read(1)
                with rasterio.open(case/'scratch'/'ws.bsq') as src: ws = src.read()
                return adj, phi, ws, (case/'elmfire.data').read_text(), json.loads((case/'case.json').read_text())
            control = run('control')
            weighted = run('weighted', 'FRONT_PMODE=weighted', 'FRONT_PFILE='+wf)
            tiny = run('tiny', 'FRONT_PFILE='+wf, 'FRONT_PTHRESH=1e-9')
            composed = run('composed', 'ADJ=0.5', 'ADJ_FILE='+adjfile, 'FRONT_PMODE=weighted', 'FRONT_PFILE='+wf)
            adj, phi = weighted[0], weighted[1]
            self.assertAlmostEqual(float(adj[2,3]), .2, places=6)       # weighted edge
            self.assertAlmostEqual(float(adj[2,2]), 1, places=6)        # p=1
            self.assertAlmostEqual(float(adj[1,1]), C.FRONT_PFLOOR, places=6)  # floored
            self.assertEqual(float(adj[1,7]), 1)                        # hotspot override
            self.assertEqual(float(adj[0,8]), 1)                        # outside heat
            self.assertLess(phi[0,8], 0)
            self.assertLess(phi[1,7], 0)
            self.assertGreater(phi[4,1], 0)                             # p=0 does not start burning
            self.assertGreater(phi[7,3], 0)                             # uncovered, no detections near
            self.assertTrue((adj > 0).all())
            np.testing.assert_array_equal(control[0], np.ones_like(adj))
            # Same initial burning set as binary with an infinitesimal threshold; only adj differs.
            np.testing.assert_array_equal(phi, tiny[1])
            np.testing.assert_array_equal(tiny[0], control[0])
            # Base ADJ terms compose multiplicatively (0.5 * 2 * p).
            np.testing.assert_allclose(composed[0], adj * 1.0, rtol=1e-6)
            # Forecast weather and physical options unchanged.
            np.testing.assert_array_equal(control[2], weighted[2])
            self.assertEqual(control[3], weighted[3])
            front = weighted[4]['init']['front']
            self.assertEqual(front['mode'], 'weighted')
            self.assertEqual(front['effective_adj']['weighted_cells'], int((adj != 1).sum()))
            self.assertTrue(front['floored_cells'] >= 1 and front['hotspot_override_cells'] >= 1)
            # Outward band: perimeter covers rows/cols 1..7; outside ring rows 0/8, cols 0/8; (0,8) burns at t=0.
            bp = np.full((1,9,9), .1, np.float32); bp[0,0,3] = .0049; bp[0,0,4] = .025; bp[0,8,:] = -9999
            bf = raster('band.tif', bp, nodata=-9999)
            opts = ['ADJ_BAND_PFILE='+bf, 'ADJ_BAND_PREF=0.05']
            for extras in [opts, ['ADJ_BAND_PFILE='+bf, 'ADJ_BAND_M=100'], opts+['ADJ_BAND_M=100','ADJ_BAND_FLOOR=3'],
                           opts+['ADJ_BAND_M=-1'], opts+['ADJ_BAND_M=100','INIT=hotcells']]:
                with self.assertRaises(ValueError), patch.object(C,'write_case',capture), patch.object(C,'write_bsq'):
                    C.fire(landscape,str(root),state,'2021-01-01T00:00:00',12,str(root/'bad'),*base,*extras)
            near = run('band100', *opts, 'ADJ_BAND_M=100')
            wide = run('band150', *opts, 'ADJ_BAND_M=150')
            adj = near[0]
            self.assertAlmostEqual(float(adj[0,2]), 2, places=6)        # 0.1/0.05 = 2 (cap)
            self.assertAlmostEqual(float(adj[0,3]), .25, places=6)      # floor
            self.assertAlmostEqual(float(adj[0,4]), .5, places=6)       # p / p_ref
            self.assertAlmostEqual(float(adj[4,0]), 2, places=6)
            self.assertEqual(float(adj[8,4]), 1)                        # uncovered
            self.assertEqual(float(adj[0,8]), 1)                        # burning at t=0
            self.assertEqual(float(adj[0,0]), 1)                        # corner: 141 m > 100 m band
            self.assertTrue((adj[1:8,1:8] == 1).all())                  # inside the start perimeter
            self.assertAlmostEqual(float(wide[0][0,0]), 2, places=6)    # corner inside the 150 m band
            np.testing.assert_array_equal(near[1], control[1])          # start front = control's
            np.testing.assert_array_equal(near[2], control[2])
            self.assertEqual(near[3], control[3])
            ob = near[4]['init']['front']['outward_band']
            self.assertEqual(ob['covered_band_cells'], int((adj != 1).sum()))
            self.assertEqual((ob['floored_cells'], ob['band_m'], ob['p_ref']), (1, 100.0, .05))
            composed = run('band_composed', *opts, 'ADJ_BAND_M=100', 'ADJ=0.5', 'ADJ_FILE='+adjfile)
            np.testing.assert_allclose(composed[0], adj, rtol=1e-6)     # 0.5 * 2 * band


if __name__ == '__main__':
    unittest.main()
