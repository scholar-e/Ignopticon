#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "scipy", "shapely>=2.1", "pyproj", "matplotlib"]
# ///
"""Head-to-head, our 12 h forecast (adj1 control) vs PyreCast (operational ELMFIRE, p10..p90) on the frozen fires of
data/h2h/manifest_DAY.json, same truth (FEDS), same valid time T+12 h. Nothing here is tuned; run once.

Per fire, on OUR run grid (37.5 m): start = FEDS(T) | our toa==0 (where_skill.prepare); observed growth = FEDS(T+12h) & ~start &
burnable (FBFM40>100), exactly where_skill's definition. PyreCast ToA (hours since run_dt, UTM 30 m, 0 = nodata/burned at its
start) is resampled (nearest) to our grid; predicted by T+12 h = valid & toa_h <= (T+12h - run_dt) in hours. Cells their run
already burned before T but outside FEDS(T) count as predicted growth. Ours predicted = toa in (0, 12 h].
Threshold metrics (each model at its own calibration): growth recall, precision, IoU, area ratio (pred / observed).
Ranking at equal area (the fair one): candidates = burnable & ~start & (dist to start <= 2 x max observed advance | reached by
either model by T+12 h); score = arrival time (earlier first, unreached last by distance); metric = recall of the top-N cells,
N = observed growth cells in the candidates; same for 'buffer' (distance to start). Pooled = sum hits / sum N; macro = mean of fires.
Paired bootstrap over fires (10000) of ours minus PyreCast p50.
Usage: h2h_score.py 2026-08-13T12:00     writes data/h2h/results_DAY.json and .md
"""
import json, logging, os, sys, datetime as dt
from pathlib import Path
import numpy as np
os.environ['WHERE_ISSUE'] = sys.argv[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import where_skill as W
import rasterio
from rasterio.warp import reproject, Resampling
from scipy.ndimage import distance_transform_edt
ROOT = W.ROOT
H = ROOT / 'data/h2h'
PCTS = ['10', '30', '50', '70', '90']
log = logging.getLogger('h2h_score')


def pyre_arrival(path, shape, transform, crs, hours_cap):
    """PyreCast ToA hours -> our grid, nan where no arrival (nodata 0)."""
    out = np.full(shape, np.nan, 'float32')
    with rasterio.open(path) as src:
        reproject(rasterio.band(src, 1), out, src_nodata=0, dst_nodata=np.nan, dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.nearest)
    out[~(out > 0)] = np.nan
    return out


def rank_hits(arr_s, dist, cand, obs):
    """top-N hits among candidate cells; arr_s = arrival seconds (nan/inf = unreached); earlier first, then by distance."""
    d = dist[cand]; a = np.where(np.isfinite(arr_s[cand]), arr_s[cand], np.inf)
    order = np.lexsort((d, a))
    n = int(obs[cand].sum())
    return int(obs[cand][order[:n]].sum()), n


def score_fire(f, T, hours=12):
    fire = f['fire']; rd = W.run_dir(fire)
    if rd is None:
        return {'fire': fire, 'status': 'no our run'}
    P = W.prepare(fire, rd[0])
    if P is None:
        return {'fire': fire, 'status': 'no truth at T+12h'}
    toa, valid, tr, crs, res, _ = W.load_toa(rd[0])
    with rasterio.open(W.landscape_path(fire)) as s:
        burnable = s.read(4) > 100
    start, truth = P['start'], P['truth']
    base = burnable & ~start
    obs = truth & base
    n_obs = int(obs.sum())
    if n_obs == 0:
        return {'fire': fire, 'status': 'no burnable growth'}
    rt = dt.datetime.strptime(f['run_ts'], '%Y%m%d_%H%M%S')
    off = (T - rt).total_seconds() / 3600           # hours the PyreCast run started before the issue
    cap = off + hours
    ours = P['reach']                               # toa in (0, 12 h]
    ours_s = np.where(ours, toa, np.nan)
    pf = H / 'pyrecast' / f['slug'] / f['run_ts']
    pyre = {p: pyre_arrival(pf / f'{p}.tif', toa.shape, tr, crs, cap) for p in PCTS}
    pred = {'ours': ours & base}
    for p in PCTS:
        pred['pyre' + p] = (pyre[p] <= cap) & base
    dist, _ = distance_transform_edt(~start, sampling=res, return_indices=True)
    maxadv = float(dist[obs].max())
    reached_any = ours | (pyre['50'] <= cap)
    cand = base & ((dist <= 2 * maxadv) | reached_any)
    row = {'fire': fire, 'status': 'ok', 'slug': f['slug'], 'run_ts': f['run_ts'], 'lag_h': f['lag_h'], 'cell_m': float(res[0]),
           'n_obs': n_obs, 'n_cand': int(cand.sum()), 'n_obs_cand': int(obs[cand].sum()), 'max_adv_m': maxadv, 'threshold': {}, 'rank': {}}
    for k, m in pred.items():
        i = int((m & obs).sum()); n = int(m.sum())
        row['threshold'][k] = {'pred': n, 'hits': i, 'recall': i / n_obs, 'precision': (i / n) if n else None,
                               'iou': i / (n + n_obs - i), 'area_ratio': n / n_obs}
    keys = {'ours': ours_s, 'buffer': np.full(toa.shape, np.nan)}
    for p in PCTS:
        keys['pyre' + p] = np.where(pyre[p] <= cap, (pyre[p] - off) * 3600.0, np.nan)   # seconds after the issue
    for k, a in keys.items():
        hit, n = rank_hits(a, dist, cand, obs)
        row['rank'][k] = {'hits': hit, 'n': n, 'recall': hit / n if n else None}
    # sanity: how much of the observed growth lies inside PyreCast's raster at all
    row['pyre_domain_obs_share'] = float(np.isfinite(pyre['90'])[obs].mean())
    return row


def boot(a, b, B=10000, seed=1):
    """paired bootstrap over fires: pooled hits/N difference a-b and macro mean difference. a,b: arrays (hits, n)."""
    rng = np.random.default_rng(seed); n = len(a)
    idx = rng.integers(0, n, (B, n))
    pa, pb = a[:, 0] / a[:, 1], b[:, 0] / b[:, 1]
    pool = a[idx, 0].sum(1) / a[idx, 1].sum(1) - b[idx, 0].sum(1) / b[idx, 1].sum(1)
    mac = (pa - pb)[idx].mean(1)
    q = lambda x: [float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))]
    return {'pooled_diff': float(a[:, 0].sum() / a[:, 1].sum() - b[:, 0].sum() / b[:, 1].sum()), 'pooled_ci': q(pool),
            'macro_diff': float((pa - pb).mean()), 'macro_ci': q(mac), 'p_ours_better_pooled': float((pool > 0).mean())}


def main(issue):
    d = issue[:10].replace('-', '')
    man = json.load(open(H / f'manifest_{d}.json'))
    T = dt.datetime.fromisoformat(issue)
    rows = [score_fire(f, T) for f in man['fires']]
    ok = [r for r in rows if r['status'] == 'ok']
    res = {'issue': issue, 'manifest_counts': man['counts'], 'n_fires_manifest': len(rows), 'n_scored': len(ok),
           'excluded': {r['fire']: r['status'] for r in rows if r['status'] != 'ok'}, 'fires': rows}
    arr = lambda k: np.array([[r['rank'][k]['hits'], r['rank'][k]['n']] for r in ok], float)
    pool = {}
    for k in ['ours', 'buffer'] + ['pyre' + p for p in PCTS]:
        a = arr(k)
        pool[k] = {'rank_recall_pooled': float(a[:, 0].sum() / a[:, 1].sum()), 'rank_recall_macro': float((a[:, 0] / a[:, 1]).mean())}
    for k in ['ours'] + ['pyre' + p for p in PCTS]:
        t = [r['threshold'][k] for r in ok]; H_, P_, O = sum(x['hits'] for x in t), sum(x['pred'] for x in t), sum(r['n_obs'] for r in ok)
        pool[k].update(recall_pooled=H_ / O, precision_pooled=H_ / P_ if P_ else None, iou_pooled=H_ / (P_ + O - H_),
                       area_ratio_pooled=P_ / O, recall_macro=float(np.mean([x['recall'] for x in t])),
                       iou_macro=float(np.mean([x['iou'] for x in t])), area_ratio_median=float(np.median([x['area_ratio'] for x in t])))
    res['pooled'] = pool
    if len(ok) > 2:
        res['bootstrap_rank_recall'] = {f'ours_minus_{b}': boot(arr('ours'), arr(b)) for b in ['pyre50', 'buffer']}
        res['bootstrap_rank_recall']['pyre50_minus_buffer'] = boot(arr('pyre50'), arr('buffer'))
    (H / f'results_{d}.json').write_text(json.dumps(res, indent=1) + '\n')
    L = [f'# Head-to-head {issue} (ours adj1 control vs PyreCast), +12 h, FEDS truth', '',
         f'Manifest {len(rows)} fires, scored {len(ok)}; excluded: {res["excluded"]}', '',
         '## Ranking at equal area (recall of top-N, N = observed growth cells)', '', '| method | pooled | macro |', '|---|---|---|']
    L += [f'| {k} | {v["rank_recall_pooled"]:.3f} | {v["rank_recall_macro"]:.3f} |' for k, v in pool.items()]
    L += ['', '## Threshold metrics at each model\'s own calibration (pooled over fires)', '',
          '| model | recall | precision | IoU | area ratio (pooled) | area ratio (median) | macro recall | macro IoU |', '|---|---|---|---|---|---|---|---|']
    for k in ['ours'] + ['pyre' + p for p in PCTS]:
        v = pool[k]; L.append(f'| {k} | {v["recall_pooled"]:.3f} | {v["precision_pooled"]:.3f} | {v["iou_pooled"]:.3f} | {v["area_ratio_pooled"]:.2f} | '
                              f'{v["area_ratio_median"]:.2f} | {v["recall_macro"]:.3f} | {v["iou_macro"]:.3f} |')
    if 'bootstrap_rank_recall' in res:
        L += ['', '## Paired bootstrap over fires, rank recall difference (95% CI)', '']
        for k, v in res['bootstrap_rank_recall'].items():
            L.append(f'- {k}: pooled {v["pooled_diff"]:+.3f} [{v["pooled_ci"][0]:+.3f}, {v["pooled_ci"][1]:+.3f}], macro {v["macro_diff"]:+.3f} '
                     f'[{v["macro_ci"][0]:+.3f}, {v["macro_ci"][1]:+.3f}], P(ours better, pooled) {v["p_ours_better_pooled"]:.3f}')
    L += ['', '## Per fire (rank recall at observed area; threshold recall / area ratio)', '',
          '| fire | slug | lag h | obs cells | ours rank | pyre50 rank | buffer rank | ours rec/ratio | pyre50 rec/ratio |', '|---|---|---|---|---|---|---|---|---|']
    for r in ok:
        g = lambda k: r['rank'][k]['recall']; t = r['threshold']
        L.append(f'| {r["fire"]} | {r["slug"]} | {r["lag_h"]} | {r["n_obs"]} | {g("ours"):.2f} | {g("pyre50"):.2f} | {g("buffer"):.2f} | '
                 f'{t["ours"]["recall"]:.2f}/{t["ours"]["area_ratio"]:.2f} | {t["pyre50"]["recall"]:.2f}/{t["pyre50"]["area_ratio"]:.2f} |')
    (H / f'results_{d}.md').write_text('\n'.join(L) + '\n')
    log.info('\n'.join(L[:20]))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(filename)s:%(lineno)d %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
    main(sys.argv[1])
