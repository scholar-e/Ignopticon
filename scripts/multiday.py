#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "rasterio", "shapely>=2.1", "pyproj", "pygrib"]
# ///
"""Multi-day verification (A) and worst-case ensembles (B) with the Aug 2 physics adj1 forecast, one task per fire-issue.

  multiday.py manifest        freeze, BEFORE any run: for each issue in ISSUES the FEDS fires known at issue (latest geometry
                              in the snapshots up to the issue), matched to WFIGS incidents (containment_aug2.py), kept if
                              uncontained and incident-matched -> data/multiday/manifest.json, tasks.txt, catalog.csv,
                              containment_DAY.json. Also lists the Aug 2 fires with a forecast (ensembles only, task kind aug2).
  multiday.py inputs          the rsync file list of the Aug 2 fires' local inputs (data/multiday/aug2_inputs.txt)
  multiday.py task N [--members a,b]   run line N (0-based) of tasks.txt (SLURM_ARRAY_TASK_ID by default)

A task is: HRRR 12Z run of the issue day (hourly f00-f24 and sub-hourly f00-f12, shared cache, flock'd), build the case if
it is not there (build_fire_case.py: VIIRS from LAADS, LANDFIRE, 3DEP; needs the Earthdata token), associate the initial heat
with the fire (3 km around the latest FEDS outline, as perimeter_batch.py), sub-hourly weather (hrrr_weather.py --subhourly with
the previous day's gridMET fm100 tif data/perimeter_batch/gridmet_fm100_PREVDAY.tif), then the perimeter_batch.py 'worstcase'
member set (adj1 control, wind direction -20/0/+20 x speed x0.75/1/1.25 without the 0/x1 duplicate, dead moisture -2/+2;
11 members) on WORKERS cores, 12 h, INIT=feds, LH 90 (exactly the Aug 2 adj1 settings). Members land in
data/perimeter_batch/FIRE/runs/physics3/NAME/ (where growth_bounds.py members() looks). Status: data/multiday/status/DAY/FIRE.json.
Env: PY (interpreter for the other scripts, python3 in the container), WORKERS, HOURS (12).
"""
import concurrent.futures as cf
import csv, datetime as dt, fcntl, glob, json, logging, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(Path(__file__).resolve().parent.parent)
ISSUES = ['2026-08-02T12:00', '2026-08-03T12:00', '2026-08-04T12:00', '2026-08-05T12:00']
NEW = ISSUES[1:]
MD = Path('data/multiday')
SNAP = Path('earth/public/data/fires/feds/2026/08')
PB = Path('data/perimeter_batch')
HOURS = int(os.environ.get('HOURS', '12'))
TASKS = Path(os.environ.get('TASKS', MD / 'tasks.txt'))      # h2h: data/h2h/tasks_20260813.txt
CATALOG = Path(os.environ.get('CATALOG', MD / 'catalog.csv'))
MEMBERS = os.environ.get('MEMBERS')                         # e.g. adj1 = control only
HRRR_HOURLY, HRRR_SUBH = Path('data/raw/hrrr_hourly'), Path('data/raw/hrrr_subh')
PYRUN = (os.environ.get('PY') or 'uv run --script').split()
log = logging.getLogger('multiday')


def day(issue):
    return issue[:10].replace('-', '')


def fire_dir(issue, feds):
    return f'map{day(issue)}-{feds}'


def snapshot_geoms(issue):
    """latest FEDS geometry per fire id from the snapshots up to the issue (DD-HHMM.json, same month)."""
    from shapely.geometry import shape
    last = issue[8:10] + '-' + issue[11:13] + issue[14:16] + '.json'
    geoms = {}
    for path in sorted(glob.glob(str(SNAP / '[0-9][0-9]-[0-9][0-9][0-9][0-9].json'))):
        if os.path.basename(path) > last:
            continue
        for f in json.load(open(path)).get('features', []):
            geoms[str(f['properties']['id'])] = (shape(f['geometry']), f['properties'])
    return geoms


def cmd_manifest():
    import containment_aug2
    MD.mkdir(parents=True, exist_ok=True)
    man = {'frozen_by': 'scripts/multiday.py manifest, before any multi-day or ensemble run',
           'frozen': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'rule': 'FEDS fires known at issue (latest geometry in snapshots <= issue), matched to WFIGS incidents '
                   '(containment_aug2.py), kept if uncontained at issue and incident-matched',
           'members': 'scripts/perimeter_batch.py worstcase', 'hours': HOURS, 'issues': {}}
    cat_rows, tasks = [], []
    for issue in NEW:
        geoms = snapshot_geoms(issue)
        ids = MD / f'fires_{day(issue)}.txt'
        ids.write_text('\n'.join(sorted(geoms, key=int)) + '\n')
        out = MD / f'containment_{day(issue)}.json'
        containment_aug2.main(str(ids), str(SNAP), issue, str(out))
        cont = json.load(open(out))['fires']
        counts, keep = {}, []
        for i, c in sorted(cont.items(), key=lambda x: int(x[0])):
            counts[c['status']] = counts.get(c['status'], 0) + 1
            if c['status'] != 'uncontained':
                continue
            g, props = geoms[i]
            w, s, e, n = g.bounds
            d = dt.date.fromisoformat(issue[:10])
            cat_rows.append({'id': fire_dir(issue, i), 'west': round(w, 4), 'south': round(s, 4), 'east': round(e, 4),
                             'north': round(n, 4), 'start': (d - dt.timedelta(days=1)).isoformat(),
                             'end': (d + dt.timedelta(days=1)).isoformat(), 'feds_id': i})
            keep.append({'feds_id': i, 'fire': fire_dir(issue, i), 'incident': c['incident'], 'area_km2': props.get('area_km2'),
                         'place': props.get('place'), 'incident_acres': c.get('size_acres')})
            tasks.append(f"new\t{issue}\t{fire_dir(issue, i)}\t{i}")
        man['issues'][issue] = {'fires_known_at_issue': len(geoms), 'containment_counts': counts, 'fires': keep}
    # Aug 2: every fire with a forecast (ensembles only; the control/skill for Aug 2 exist already)
    idx = json.load(open('earth/public/data/forecasts/index.json'))['forecasts']
    a2 = sorted({str(x['fire_id']) for x in idx if x['issued'].startswith('2026-08-02T12')}, key=int)
    old = {r['feds_id']: r for r in csv.DictReader(open(PB / 'catalog.csv'))}
    cont2 = json.load(open('data/containment/aug2_containment.json'))['fires']
    man['issues'][ISSUES[0]] = {'purpose': 'worst-case ensembles only', 'fires': [
        {'feds_id': i, 'fire': 'map20260802-' + i, 'containment': cont2.get(i, {}).get('status'),
         'in_catalog': i in old} for i in a2]}
    for i in a2:
        if i in old:
            cat_rows.append({k: old[i][k] for k in ('id', 'west', 'south', 'east', 'north', 'start', 'end', 'feds_id')})
            tasks.append(f"aug2\t{ISSUES[0]}\tmap20260802-{i}\t{i}")
    with open(MD / 'catalog.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, ['id', 'west', 'south', 'east', 'north', 'start', 'end', 'feds_id'])
        w.writeheader(); w.writerows(cat_rows)
    (MD / 'tasks.txt').write_text('\n'.join(tasks) + '\n')
    man['n_tasks'] = len(tasks)
    man['tasks_file'] = 'data/multiday/tasks.txt'
    (MD / 'manifest.json').write_text(json.dumps(man, indent=1) + '\n')
    log.info('%d tasks: %s', len(tasks), {k: len(v['fires']) for k, v in man['issues'].items()})


def cmd_inputs():
    """Files of the Aug 2 fires that can be sent instead of rebuilt on Hyak (smaller and cheaper)."""
    names = ['case.json', 'fire-state.tif', 'fire-observed.json', 'landscape.tif']
    lines = []
    for line in (MD / 'tasks.txt').read_text().split('\n'):
        if not line.startswith('aug2'):
            continue
        fire = line.split('\t')[2]
        root = Path('data/training/fires') / fire
        if not all((root / n).exists() for n in names):
            continue
        lines += [str(root / n) for n in names]
        sub = root / 'wx-subh' / '20260802T12'
        lines += [str(p) for p in sorted(sub.glob('*'))]
        lines += [str(p) for p in sorted((root / 'obs').glob('*'))]
        lines += [str(p) for p in sorted((root / 'wx').glob('*/ws.tif'))]  # only so that the case counts as built
    (MD / 'aug2_inputs.txt').write_text('\n'.join(lines) + '\n')
    print(len(lines), 'files')


def run(cmd, logfile):
    with open(logfile, 'a') as f:
        subprocess.run([str(c) for c in cmd], stdout=f, stderr=subprocess.STDOUT, check=True)


def prefetch_hrrr(issue):
    import hrrr_weather
    run_t = dt.datetime.fromisoformat(issue)
    HRRR_HOURLY.mkdir(parents=True, exist_ok=True); HRRR_SUBH.mkdir(parents=True, exist_ok=True)
    with open(f'data/raw/.hrrr_{day(issue)}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)   # one task per issue downloads; the others find the files
        with cf.ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda h: hrrr_weather.download(run_t, h, str(HRRR_HOURLY)), range(25)))
            list(pool.map(lambda h: hrrr_weather.download_subhourly(run_t, h, str(HRRR_SUBH)), range(HOURS + 1)))


def cmd_task(n, only=None):
    from elmfire_perimeters import utc
    import numpy as np, rasterio, shapely
    from shapely.geometry import shape
    from pyproj import Transformer
    from perimeter_batch import _worstcase
    only = only or (MEMBERS.split(',') if MEMBERS else None)
    kind, issue, fire, feds = TASKS.read_text().split('\n')[n].split('\t')
    dest = PB / fire; dest.mkdir(parents=True, exist_ok=True)
    (MD / 'status' / day(issue)).mkdir(parents=True, exist_ok=True)
    stf = MD / 'status' / day(issue) / f'{fire}.json'
    st = {'fire': fire, 'feds_id': feds, 'issue': issue, 'kind': kind, 'status': 'start',
          'started': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}
    save = lambda: stf.write_text(json.dumps(st, indent=1) + '\n')
    save(); t0 = time.time()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(dest / 'multiday.log')], force=True)
    os.environ['FEDS_SNAPSHOT_DIR'] = str(SNAP)
    try:
        prefetch_hrrr(issue); st['t_hrrr_s'] = round(time.time() - t0); save()
        root = Path('data/training/fires') / fire

        def built():
            try:
                c = json.loads((root / 'case.json').read_text())
                return c.get('ready') and any(i['issue'] == issue and Path(i['wx'], 'ws.tif').exists() for i in c['issues'])
            except (OSError, ValueError, KeyError, TypeError):
                return False
        if not built():
            raw = Path('data/training/raw/hrrr') / fire; raw.mkdir(parents=True, exist_ok=True)
            for g in HRRR_HOURLY.glob(f'hrrr_{day(issue)}_t{issue[11:13]}z_f*.grb2'):
                if not (raw / g.name).exists():
                    os.link(g, raw / g.name)
            run([*PYRUN, 'scripts/build_fire_case.py', fire, '--catalog', CATALOG, '--issue', issue,
                 '--feds-fire-id', feds], dest / 'build.log')
            st['inputs'] = 'built'
        else:
            st['inputs'] = 'reused'
        st['t_build_s'] = round(time.time() - t0); save()
        case = json.loads((root / 'case.json').read_text())
        if case.get('truncated'): raise ValueError('bounding box truncated')
        if not built(): raise ValueError('case not ready after build')
        item = next(i for i in case['issues'] if i['issue'] == issue)
        obs = json.loads(Path(item['observed']).read_text())
        known = [p for p in obs['perimeters'] if utc(p['time']) <= utc(issue)]
        if not known: raise ValueError('no FEDS start')
        outline = max(known, key=lambda p: utc(p['time']))
        with rasterio.open(root / 'fire-state.tif') as src: crs = src.crs
        pr = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
        g = shapely.transform(shape(outline['geometry']), lambda xy: np.column_stack(pr.transform(xy[:, 0], xy[:, 1]))).buffer(3000)
        for f in obs['frames']:
            if utc(f['time']) <= utc(issue):
                f['cells'] = [c for c in f['cells'] if g.covers(shapely.Point(*pr.transform(c[0], c[1])))]
        hot = sum(len(f['cells']) for f in obs['frames'] if utc(issue) - dt.timedelta(hours=6) <= utc(f['time']) <= utc(issue))
        st.update(hot_cells_before=hot, latest_perimeter=outline['time'])
        if hot == 0: raise ValueError('no associated VIIRS hot cells in 6 h before issue')
        assoc = dest / 'associated-observed.json'; assoc.write_text(json.dumps(obs, separators=(',', ':')))
        prev = (dt.date.fromisoformat(issue[:10]) - dt.timedelta(days=1)).strftime('%Y%m%d')
        fm100 = PB / f'gridmet_fm100_{prev}.tif'
        sub = root / 'wx-subh' / (day(issue) + 'T' + issue[11:13])
        if not (sub / 'wx.json').exists():
            run([*PYRUN, 'scripts/hrrr_weather.py', root / 'fire-state.tif', issue[:13], HOURS, sub, HRRR_SUBH,
                 '--subhourly', f'--fm100={fm100}'], dest / 'wx-subh.log')
        st['t_wx_s'] = round(time.time() - t0); st['status'] = 'running'; save()
        members = _worstcase()
        if only: members = {k: v for k, v in members.items() if k in only}

        def member(name):
            folder = dest / 'runs' / 'physics3' / name
            if (folder / 'outputs' / 'run.json').exists():
                return name, 'done already'
            extra = [f'{k}={v}' for k, v in members[name].items()]
            try:
                run(['bash', 'scripts/elmfire_forecast.sh', root / 'landscape.tif', sub, root / 'fire-state.tif', issue, HOURS,
                     folder, 'OBSERVED=' + str(assoc), 'INIT=feds', *extra, 'LH_MOISTURE_CONTENT=90'], dest / f'worstcase-{name}.log')
                return name, 'ok'
            except subprocess.CalledProcessError:
                return name, 'failed'
        with cf.ThreadPoolExecutor(int(os.environ.get('WORKERS', os.environ.get('SLURM_CPUS_PER_TASK', 4)))) as pool:
            res = dict(pool.map(member, members))
        cells = {}
        for name in members:
            tifs = sorted((dest / 'runs' / 'physics3' / name / 'outputs').glob('time_of_arrival_*.tif'))
            if tifs:
                with rasterio.open(tifs[-1]) as s:
                    t = s.read(1); ok = (s.read_masks(1) > 0) & np.isfinite(t)
                cells[name] = int((ok & (t > 0)).sum())
        st.update(members=res, grown_cells=cells, status='complete' if all(v != 'failed' for v in res.values()) else 'partial')
    except Exception as e:
        st.update(status='failed', error=repr(e)); log.exception(fire)
    st['finished'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    st['elapsed_s'] = round(time.time() - t0); save()
    log.info('%s %s %ss', fire, st['status'], st['elapsed_s'])
    sys.exit(0 if st['status'] in ('complete', 'partial') else 1)


def cmd_recall(issue):
    """Worst-case recall: share of the observed +12 h burnable growth (where_skill.prepare, uncontained incident-matched fires)
    inside the cells ELMFIRE reached within 12 h, control adj1 alone vs the union of the worst-case members. No area cut."""
    os.environ['WHERE_ISSUE'] = issue
    import where_skill as W
    from selfcal_adj import load_toa
    import numpy as np
    man = json.load(open(MD / 'manifest.json'))['issues'][issue]['fires']
    names = list(_members())
    rows = []
    for f in man:
        if f.get('containment', 'uncontained') != 'uncontained':
            continue
        fire = f['fire']; rd = W.run_dir(fire)
        if rd is None: continue
        try: P = W.prepare(fire, rd[0])
        except Exception as e: log.warning('%s: %s', fire, e); continue
        if P is None or not P['obs'].any(): continue
        reach_u = P['reach'].copy(); n = 0
        for m in names:
            d = PB / fire / 'runs' / 'physics3' / m / 'outputs'
            if not (d / 'run.json').exists(): continue
            toa, valid, *_ = load_toa(d)
            if toa.shape != reach_u.shape: continue
            reach_u |= valid & (toa > 0) & (toa <= 12 * 3600); n += 1
        o = P['obs']; no = int(o.sum())
        rows.append({'fire': fire, 'obs_cells': no, 'members': n, 'recall_control': float((P['reach'] & o).sum() / no),
                     'recall_union': float((reach_u & o).sum() / no), 'area_ratio_control': float(P['reach'].sum() / max(P['obs_growth_cells'], 1)),
                     'area_ratio_union': float(reach_u.sum() / max(P['obs_growth_cells'], 1)), 'cell_m': P['res']})
    out = MD / f'recall_{day(issue)}.json'
    out.write_text(json.dumps(rows, indent=1) + '\n')
    ok = [r for r in rows if r['members'] > 1]
    log.info('%s: %d fires with observed growth, %d with an ensemble; mean recall control %.3f union %.3f', issue, len(rows), len(ok),
             np.mean([r['recall_control'] for r in ok]) if ok else float('nan'), np.mean([r['recall_union'] for r in ok]) if ok else float('nan'))


def _members():
    from perimeter_batch import _worstcase
    return _worstcase()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout)])
    a = sys.argv[1:]
    if a[0] == 'manifest': cmd_manifest()
    elif a[0] == 'inputs': cmd_inputs()
    elif a[0] == 'recall': cmd_recall(a[1])
    else:
        only = a[a.index('--members') + 1].split(',') if '--members' in a else None
        cmd_task(int(a[1]) if len(a) > 1 and a[1] != '--members' else int(os.environ['SLURM_ARRAY_TASK_ID']), only)
