#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Self-calibration from the fire's own recent progression (MODELLING_LOG.md entry 17).

For a fire and issue time T, using only data available at T:
 1. Hindcast: ELMFIRE (physics3 inputs, ADJ=1, INIT=feds) from the FEDS perimeter at T-24 h, else T-12 h (recorded
    as start_used), to T. Weather: HRRR sub-hourly (wrfsubhf) stitched from the hourly runs inside the window, each
    run's f00 analysis and its 15/30/45 min steps (the past, so analyses and the shortest leads); 1-h moisture EMC
    per step, 10-h lagged EMC (tau 10 h) re-run over the stitched series from the previous-day gridMET fm100 at the
    start, 100-h = previous-day gridMET fm100 (as physics3, hrrr_weather.py --subhourly).
 2. Correction map: modelled versus observed advance over (start, T]. Observed burned = FEDS perimeters in the
    window plus every VIIRS cell (375 m square) detected in the window, outside the start perimeter. The state
    raster's arrival guesses are not used: they interpolate with later snapshots. Advance = p90 of the distance
    from the start perimeter of a sector's newly burned cells; 16 sectors by bearing from the start centroid.
    r = (observed + EPS) / (modelled + EPS) with EPS = 187.5 m (half a VIIRS pixel: keeps zero/zero at 1, zero
    model with real observed growth large, observed none with model growth small), clamped to [0.1, 5], smoothed
    circularly (1/4, 1/2, 1/4 in log r). Each model cell gets log r interpolated linearly in bearing between sector
    centres, blended to the fire-wide median log r with weight exp(-d / DECAY_M) of its distance d from the start
    perimeter (DECAY_M = 3000 m). Written as a float32 raster on the model (landscape) grid: adj_selfcal.tif.
 3. Forecast from T for HOURS (12) with ADJ=1 ADJ_FILE=adj_selfcal.tif, physics3 inputs otherwise.

Usage: selfcal_adj.py FIRE ISSUE OBSERVED.json OUTDIR [--physics3=RUNDIR/outputs] [--wx=WXDIR]
   e.g. selfcal_adj.py map20260802-66510 2026-08-02T12:00 data/perimeter_batch/map20260802-66510/associated-observed.json
            data/perimeter_batch/selfcal/66510 --physics3=data/perimeter_batch/map20260802-66510/runs/physics3/adj1/outputs
FIRE inputs from data/training/fires/FIRE (landscape.tif, fire-state.tif, wx-subh/YYYYMMDDTHH for the forecast).
Writes OUTDIR/{wx-hours,wx-hind,hindcast,forecast}/, adj_selfcal.tif, selfcal.json (start used, per-sector r,
hindcast skill, forecast scores vs FEDS within HOURS for selfcal, physics3 adj1 if given, no growth) and, with
--figure=PNG, a map of the perimeters and the correction field.
Environment: PY as elmfire_forecast.sh; FM100_TIF (default data/perimeter_batch/gridmet_fm100_20260801.tif);
HOURS (12); SECTORS (16); EPS_M (187.5); DECAY_M (3000).
"""
import datetime as dt, json, logging, os, subprocess, sys
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt
import shapely
from shapely.geometry import shape, mapping
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from perimeter_batch import scores  # noqa: E402

log = logging.getLogger(__name__)
FM100 = os.environ.get('FM100_TIF', 'data/perimeter_batch/gridmet_fm100_20260801.tif')
HOURS = int(os.environ.get('HOURS', '12'))
SECTORS = int(os.environ.get('SECTORS', '16'))
EPS_M = float(os.environ.get('EPS_M', '187.5'))
DECAY_M = float(os.environ.get('DECAY_M', '3000'))
PYRUN = (os.environ.get('PY') or 'uv run --script').split()
utc = lambda s: dt.datetime.fromisoformat(s.replace('Z', '')[:16])
iso = lambda t: t.strftime('%Y-%m-%dT%H:%M')


def choose_start(obs, issue):
    times = {utc(p['time']): p for p in obs['perimeters']}
    for back in (24, 12):
        t = issue - dt.timedelta(hours=back)
        if t in times: return t, times[t], back
    raise ValueError('No FEDS perimeter at T-24 h or T-12 h')


def hindcast_weather(state_tif, start, issue, out):
    """Stitch hourly HRRR sub-hourly runs start..issue-1h: f00, +15, +30, +45 min of each, + the last run's +60."""
    hours = int((issue - start).total_seconds() // 3600)
    wxdir = out / 'wx-hind'
    if (wxdir / 'wx.json').exists(): return wxdir
    stacks = {k: [] for k in ('ws', 'wd', 'm1', 'm100')}
    for i in range(hours):
        run = start + dt.timedelta(hours=i)
        d = out / 'wx-hours' / run.strftime('%Y-%m-%dT%H')
        if not (d / 'wx.json').exists():
            subprocess.run([*PYRUN, 'scripts/hrrr_weather.py', str(state_tif), run.strftime('%Y-%m-%dT%H'), '1', str(d),
                            'data/raw/hrrr_subh', '--subhourly', f'--fm100={FM100}'], check=True)
        for k in stacks:
            with rasterio.open(d / f'{k}.tif') as src:
                profile, bands = src.profile, src.read()
            stacks[k].append(bands if i == hours - 1 else bands[:4])
    data = {k: np.concatenate(v) for k, v in stacks.items()}
    decay = np.exp(-900 / (10 * 3600))
    m10 = [np.clip(data['m100'][0], 2, 35)]
    for m1 in data['m1'][1:]: m10.append(m1 + (m10[-1] - m1) * decay)
    data['m10'] = np.array(m10)
    wxdir.mkdir(parents=True, exist_ok=True)
    for k, v in data.items():
        with rasterio.open(wxdir / f'{k}.tif', 'w', **{**profile, 'count': len(v), 'dtype': 'float32'}) as dst:
            dst.write(v.astype('float32'))
    (wxdir / 'wx.json').write_text(json.dumps({
        'source': 'HRRR wrfsubhf stitched: hourly runs %s..%s, f00 analysis + 15/30/45 min leads (+60 min of the last)'
                  % (iso(start), iso(issue - dt.timedelta(hours=1))),
        'run': iso(start) + ':00Z', 'hours': hours, 'dt_s': 900, 'steps': int(len(data['ws'])),
        'moisture': {'m1': 'EMC per step', 'm10': 'lagged EMC tau 10 h over the stitched series, initial gridMET fm100',
                     'm100': 'previous-day gridMET fm100, constant: ' + FM100},
        'mean_ws_mph': [round(float(b.mean()), 2) for b in data['ws']],
        'mean_m1_pct': [round(float(b.mean()), 2) for b in data['m1']]}, indent=1))
    return wxdir


def elmfire(root, wxdir, issue, hours, case, observed, *extra):
    if not (case / 'outputs' / 'run.json').exists():
        subprocess.run(['bash', 'scripts/elmfire_forecast.sh', str(root / 'landscape.tif'), str(wxdir),
                        str(root / 'fire-state.tif'), iso(issue), str(hours), str(case), 'OBSERVED=' + str(observed),
                        'ADJ=1.0', 'INIT=feds', *extra, 'LH_MOISTURE_CONTENT=90'], check=True)
    return case / 'outputs'


def load_toa(outputs):
    with rasterio.open(sorted(Path(outputs).glob('time_of_arrival_*.tif'))[-1]) as src:
        toa = src.read(1)
        valid = (src.read_masks(1) > 0) & (toa >= 0) & np.isfinite(toa)
        return toa, valid, src.transform, src.crs, src.res, src.profile


def raster_of(geom_ll, shape_, transform, crs):
    p = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    g = shapely.transform(shape(geom_ll), lambda xy: np.column_stack(p.transform(xy[:, 0], xy[:, 1])))
    return rasterize([(mapping(g), 1)], out_shape=shape_, transform=transform, dtype='uint8').astype(bool)


def observed_window(obs, start, issue, shape_, transform, crs, cell_m=375.0):
    """FEDS perimeters in (start, issue] plus VIIRS cells (375 m squares) detected in (start, issue]."""
    burned = np.zeros(shape_, bool)
    n_per, n_cells = 0, 0
    for p in obs['perimeters']:
        if start < utc(p['time']) <= issue:
            burned |= raster_of(p['geometry'], shape_, transform, crs); n_per += 1
    ref = utc(obs['reference'])
    proj = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    h = int(round(cell_m / 2 / transform.a))
    for f in obs['frames']:
        t = ref + dt.timedelta(seconds=f['seconds'])
        if not start < t <= issue: continue
        for lon, lat, *_ in f['cells']:
            x, y = proj.transform(lon, lat)
            c, r = int((x - transform.c) / transform.a), int((y - transform.f) / transform.e)
            burned[max(r - h, 0):r + h, max(c - h, 0):c + h] = True; n_cells += 1
    return burned, {'feds_perimeters': n_per, 'viirs_cells': n_cells}


def bearings(shape_, transform, start):
    rr, cc = np.nonzero(start)
    r0, c0 = rr.mean(), cc.mean()
    r, c = np.mgrid[0:shape_[0], 0:shape_[1]]
    return np.degrees(np.arctan2((c - c0) * transform.a, (r0 - r) * abs(transform.e))) % 360  # from north, clockwise


def correction_map(hind_out, obs, start, issue, start_geom, out_tif):
    toa, valid, transform, crs, res, profile = load_toa(hind_out)
    H = (issue - start).total_seconds()
    start_r = raster_of(start_geom, toa.shape, transform, crs)
    model = valid & (toa <= H) & ~start_r
    observed, obs_info = observed_window(obs, start, issue, toa.shape, transform, crs)
    observed &= ~start_r
    dist = distance_transform_edt(~start_r, sampling=res)
    b = bearings(toa.shape, transform, start_r)
    width = 360 / SECTORS
    sector = (np.floor((b + width / 2) / width) % SECTORS).astype(int)
    p90 = lambda m: float(np.quantile(dist[m], .9)) if m.any() else 0.0
    rows = []
    for s in range(SECTORS):
        mo, ob = p90(model & (sector == s)), p90(observed & (sector == s))
        rows.append({'sector': s, 'bearing_deg': s * width, 'model_advance_m': round(mo, 1),
                     'observed_advance_m': round(ob, 1), 'model_cells': int((model & (sector == s)).sum()),
                     'observed_cells': int((observed & (sector == s)).sum()),
                     'r_raw': round(float(np.clip((ob + EPS_M) / (mo + EPS_M), .1, 5)), 3)})
    lr = np.log([x['r_raw'] for x in rows])
    lr_s = .25 * np.roll(lr, 1) + .5 * lr + .25 * np.roll(lr, -1)
    for x, v in zip(rows, lr_s): x['r'] = round(float(np.exp(v)), 3)
    med = float(np.median(lr_s))
    # Linear interpolation in bearing between sector centres (circular), blended to the median away from the fire.
    pos = b / width
    i0 = np.floor(pos).astype(int) % SECTORS
    f = pos - np.floor(pos)
    field = lr_s[i0] * (1 - f) + lr_s[(i0 + 1) % SECTORS] * f
    w = np.exp(-dist / DECAY_M)
    adj = np.exp(w * field + (1 - w) * med).astype('float32')
    prof = {k: v for k, v in profile.items() if k not in ('nodata',)}
    with rasterio.open(out_tif, 'w', **{**prof, 'count': 1, 'dtype': 'float32', 'driver': 'GTiff'}) as dst:
        dst.write(adj, 1)
    return {'sectors': rows, 'median_r': round(float(np.exp(med)), 3), 'eps_m': EPS_M, 'decay_m': DECAY_M,
            'observed': obs_info, 'adj_min': float(adj.min()), 'adj_max': float(adj.max())}


def perimeter_scores(outputs, start_geom, truths, res_hours):
    toa, valid, transform, crs, res, _ = load_toa(outputs)
    start = raster_of(start_geom, toa.shape, transform, crs) | (valid & (toa == 0))
    out = []
    for p in truths:
        truth = raster_of(p['geometry'], toa.shape, transform, crs)
        pred = start | (valid & (toa <= p['hours'] * 3600))
        out.append({'time': p['time'], 'hours': p['hours'], **scores(pred, truth, start, res)})
    return out


def figure(png, start_geom, issue_geom, truth_geom, runs, adj_tif, rows, start_label):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    with rasterio.open(adj_tif) as src:
        adj, transform, crs = src.read(1), src.transform, src.crs
    m = lambda g: raster_of(g, adj.shape, transform, crs)
    masks = {start_label: m(start_geom), 'FEDS T (2026-08-02 12Z)': m(issue_geom),
             'FEDS T+12 h (observed)': m(truth_geom)}
    for name, (toa, valid) in runs.items(): masks[name] = masks['FEDS T (2026-08-02 12Z)'] | (valid & (toa <= HOURS * 3600))
    allm = np.any(list(masks.values()), axis=0)
    rr, cc = np.nonzero(allm)
    pad = 20
    sl = np.s_[max(rr.min() - pad, 0):rr.max() + pad, max(cc.min() - pad, 0):cc.max() + pad]
    km = abs(transform.a) / 1000
    ext = [0, (sl[1].stop - sl[1].start) * km, 0, (sl[0].stop - sl[0].start) * km]
    fig, ax = plt.subplots(1, 2, figsize=(14, 6.5))
    colors = ['#888888', '#000000', '#d62728', '#1f77b4', '#ff7f0e']
    styles = ['--', '-', '-', '-', '-']
    handles = []
    for (name, mk), col, ls in zip(masks.items(), colors, styles):
        cs = ax[0].contour(mk[sl][::-1].astype(float), levels=[.5], colors=[col], linestyles=ls, linewidths=1.3,
                           extent=ext)
        handles.append(plt.Line2D([], [], color=col, ls=ls, label=name))
    ax[0].legend(handles=handles, loc='lower left', fontsize=8); ax[0].set_aspect('equal')
    ax[0].set_title('Widemouth 66510: perimeters (forecasts at T+12 h)'); ax[0].set_xlabel('km'); ax[0].set_ylabel('km')
    im = ax[1].imshow(adj[sl], cmap='RdBu_r', norm=matplotlib.colors.LogNorm(.1, 10), extent=ext)
    for k, col in ((start_label, 'k'), ('FEDS T (2026-08-02 12Z)', 'g')):
        ax[1].contour(masks[k][sl][::-1].astype(float), levels=[.5], colors=col, linewidths=.8, extent=ext)
    ax[1].set_aspect('equal')
    fig.colorbar(im, ax=ax[1], label='self-calibration ADJ factor')
    ax[1].set_title('Correction map from the hindcast; sector r from N clockwise:\n' +
                    ' '.join(f"{x['r']:.2g}" for x in rows), fontsize=9)
    fig.tight_layout(); fig.savefig(png, dpi=130); plt.close(fig)


def run(fire, issue_s, observed, outdir, physics3=None, wx=None, png=None):
    root = Path('data/training/fires') / fire
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    issue = utc(issue_s)
    obs = json.loads(Path(observed).read_text())
    start, start_p, back = choose_start(obs, issue)
    issue_p = next(p for p in obs['perimeters'] if utc(p['time']) == issue)
    wxh = hindcast_weather(root / 'fire-state.tif', start, issue, out)
    hind = elmfire(root, wxh, start, back, out / 'hindcast', observed)
    result = {'fire': fire, 'issue': iso(issue), 'start_used': {'time': start_p['time'], 'hours_before_issue': back},
              'hindcast_weather': json.loads((wxh / 'wx.json').read_text())['source']}
    result['correction'] = correction_map(hind, obs, start, issue, start_p['geometry'], out / 'adj_selfcal.tif')
    result['hindcast_skill'] = perimeter_scores(hind, start_p['geometry'], [{**issue_p, 'hours': back}], back)[0]
    toa, valid, *_ = load_toa(hind)
    cell = abs(load_toa(hind)[2].a) ** 2 / 4046.8564224
    result['hindcast_skill']['modelled_growth_acres'] = result['hindcast_skill']['predicted_growth_cells'] * cell
    result['hindcast_skill']['observed_growth_acres'] = result['hindcast_skill']['observed_growth_cells'] * cell
    wxf = Path(wx) if wx else root / 'wx-subh' / issue.strftime('%Y%m%dT%H')
    fc = elmfire(root, wxf, issue, HOURS, out / 'forecast', observed, 'ADJ_FILE=' + str((out / 'adj_selfcal.tif').resolve()))
    truths = [{**p, 'hours': (utc(p['time']) - issue).total_seconds() / 3600} for p in obs['perimeters']
              if 0 < (utc(p['time']) - issue).total_seconds() <= HOURS * 3600]
    result['forecast_scores'] = {'selfcal': perimeter_scores(fc, issue_p['geometry'], truths, HOURS)}
    if physics3: result['forecast_scores']['physics3_adj1'] = perimeter_scores(physics3, issue_p['geometry'], truths, HOURS)
    toa_f, valid_f, transform, crs, res, _ = load_toa(fc)
    s0 = raster_of(issue_p['geometry'], toa_f.shape, transform, crs)
    result['forecast_scores']['no_growth'] = [{'time': p['time'], 'hours': p['hours'],
                                               **scores(s0, raster_of(p['geometry'], s0.shape, transform, crs), s0, res)}
                                              for p in truths]
    (out / 'selfcal.json').write_text(json.dumps(result, indent=1) + '\n')
    if png and truths:
        runs = {'self-calibrated forecast': (toa_f, valid_f)}
        if physics3: runs['physics3 ADJ=1 forecast'] = load_toa(physics3)[:2]
        figure(png, start_p['geometry'], issue_p['geometry'], truths[-1]['geometry'], runs, out / 'adj_selfcal.tif',
               result['correction']['sectors'], f"FEDS start {start_p['time']} (T-{back} h)")
    return result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s')
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = dict((a[2:].split('=', 1) + [True])[:2] for a in sys.argv[1:] if a.startswith('--'))
    if len(args) != 4: sys.exit(__doc__)
    r = run(*args, physics3=flags.get('physics3'), wx=flags.get('wx'), png=flags.get('figure'))
    print(json.dumps({k: r[k] for k in ('start_used', 'hindcast_skill')}, indent=1))
