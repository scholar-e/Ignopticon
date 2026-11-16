#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "lightgbm", "scikit-learn"]
# ///
"""Causal, disjoint-year summer diagnostic. Outputs isolated in data/compare/controlled.
Run: uv run scripts/compare_controlled.py
Harmonizer: 2012-14. Forecaster: Jul-Aug 2015/17/19. Validation: Jul-Aug 2020.
No test-year reads, no refitting of published models. Day-t weather only.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import numpy as np
import lightgbm as lgb
from sklearn.metrics import average_precision_score, roc_auc_score, log_loss
import harmonize as H
import compare_dataset as D

OUT=Path('data/compare/controlled')
CAUSAL=[k for k in H.BASE if k not in ('next','next3')]
THREADS=8
log=logging.getLogger(__name__)


def metrics(y,p,w):
    return dict(ap=float(average_precision_score(y,p,sample_weight=w)),
                auc=float(roc_auc_score(y,p,sample_weight=w)),
                logloss=float(log_loss(y,p,sample_weight=w)),
                brier=float(np.average((p-y)**2,weights=w)),
                expected=float(np.dot(p,w)),observed=float(np.dot(y,w)))


def fit_harmonizer(land):
    parts=[]
    for year in (2012,2013,2014):
        m=H.modis(HGRID,year);v=H.load(HGRID,'VNP14A1',year)
        f=H.features(m,year)
        mask=(v['clear']>0)&(m['clear']>0)&land
        near=(f['fire3']+f['prev3']+H.box3(v['fire']))>0
        random=np.random.default_rng(year).random(mask.shape)<.01
        keep=mask&(near|random)
        x=np.column_stack([f[k][keep] for k in CAUSAL]).astype(np.float32)
        y=(v['fire'][keep]>0).astype(np.uint8)
        w=np.where(near[keep],1,100).astype(np.float32)
        parts.append((x,y,w));log.info('harmonizer %d: %d rows',year,len(y))
    X,y,w=[np.concatenate([p[i] for p in parts]) for i in range(3)]
    model=lgb.train(dict(objective='binary',learning_rate=.03,num_leaves=31,
                        min_data_in_leaf=200,lambda_l2=50,num_threads=THREADS,verbosity=-1,seed=0),
                    lgb.Dataset(X,label=y,weight=w,feature_name=CAUSAL),num_boost_round=300)
    model.save_model(str(OUT/'causal_harmonizer.txt'))
    return model


def channels(f,clear):
    f3,f5=D.box(f,3),D.box(f,5)
    since=np.full(f.shape[1:],30.,np.float32);ss=[]
    for a in f3:
        since=np.where(a>=.5,0,np.minimum(since+1,30));ss.append(since)
    # all returned dates have 30 earlier days, so roll wrap is never selected
    return np.stack([np.roll(a,k,axis=0) for a in (f,f3,f5) for k in range(3)]+[np.stack(ss),clear],axis=1).astype(np.float16)


def make_rows(year,harm,static,land):
    m=H.modis(HGRID,year);v=H.load(HGRID,'VNP14A1',year)
    f=H.features(m,year)
    start=int((np.datetime64(f'{year}-07-01')-np.datetime64(f'{year}-01-01')).astype(int))
    stop=int((np.datetime64(f'{year}-09-01')-np.datetime64(f'{year}-01-01')).astype(int))
    begin=start-30
    raw=(m['fire'][begin:stop]>0).astype(np.float32)
    viirs=(v['fire'][begin:stop]>0).astype(np.float32)
    clear=(m['clear'][begin:stop]>0).astype(np.float32)
    hp=np.empty_like(raw)
    for i in range(begin,stop,8):
        end=min(i+8,stop)
        x=np.column_stack([f[k][i:end].ravel() for k in CAUSAL]).astype(np.float32)
        hp[i-begin:end-begin]=harm.predict(x,num_threads=THREADS).reshape(end-i,D.NY,D.NX)
    del f
    stacks={'raw':channels(raw,clear),'harm':channels(hp,clear),
            'viirs':channels(viirs,(v['clear'][begin:stop]>0).astype(np.float32))}
    label=(v['fire'][start+1:stop+1]>0)
    # Identical population and rows in all arms: clear input-day MODIS,
    # clear input/target VIIRS, land. Restricts conclusions to common clear looks.
    mask=(m['clear'][start:stop]>0)&(v['clear'][start:stop]>0)&(v['clear'][start+1:stop+1]>0)&land
    near=(stacks['raw'][30:,6:9].max(1)>0)|(stacks['viirs'][30:,6:9].max(1)>0)|label
    keep=mask&(near|(np.random.default_rng(year).random(mask.shape)<.01))
    d,r,c=np.nonzero(keep); y=label[keep].astype(np.uint8); w=np.where(near[keep],1,100).astype(np.float32)
    weather=np.load(f'data/harmonize/weather/weather_{year}.npz')
    wx=np.column_stack([weather[k][d+start,r,c] for k in D.WVARS])
    a=2*np.pi*(d+start+1)/D.days_in(year)
    common=np.column_stack([wx,np.sin(a),np.cos(a),static[:,r,c].T]).astype(np.float16).astype(np.float32)
    rows={k:np.column_stack([x[d+30,:,r,c],common]).astype(np.float32) for k,x in stacks.items()}
    digest=hashlib.sha256(np.column_stack([d+start,r,c]).tobytes()).hexdigest()
    log.info('forecast %d: %d shared rows, %d positives, row hash %s',year,len(y),y.sum(),digest)
    return rows,y,w,dict(year=year,rows=len(y),positives=int(y.sum()),row_sha256=digest)


HGRID='data/harmonize/grid'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(OUT/'run.log')],force=True)
    assert 'next' not in CAUSAL and 'next3' not in CAUSAL
    static,snames,land=D.load_static()
    # Keep the harmonizer feature function base-only; no weather/elevation loading.
    H.FEATURES=CAUSAL
    harm=fit_harmonizer(land)
    parts=[make_rows(year,harm,static,land) for year in (2015,2017,2019)]
    val,yv,wv,vm=make_rows(2020,harm,static,land)
    y=np.concatenate([p[1] for p in parts]);w=np.concatenate([p[2] for p in parts])
    names=D.FIRE_CH+[k+'_t' for k in D.WVARS]+['doy_sin','doy_cos']+snames
    result={'harmonizer_years':[2012,2013,2014],'harmonizer_features':CAUSAL,
            'training_rows':[p[3] for p in parts],'validation_rows':vm,'season':'July-August',
            'weather':'day t only','results':{},'curves':{}}
    for arm in ('raw','harm','viirs'):
        X=np.concatenate([p[0][arm] for p in parts]);history={}
        dt=lgb.Dataset(X,label=y,weight=w,feature_name=names,categorical_feature=['fuel_fbfm40_mode'])
        dv=lgb.Dataset(val[arm],label=yv,weight=wv,reference=dt)
        model=lgb.train(dict(objective='binary',learning_rate=.1,num_leaves=63,min_data_in_leaf=200,
                            feature_fraction=.8,bagging_fraction=.8,bagging_freq=1,lambda_l2=1,
                            num_threads=THREADS,verbosity=-1,metric=['binary_logloss','average_precision'],seed=0),
                        dt,num_boost_round=600,valid_sets=[dv],valid_names=['validation'],
                        callbacks=[lgb.record_evaluation(history),lgb.early_stopping(50,first_metric_only=True,verbose=False)])
        model.save_model(str(OUT/f'forecast_{arm}.txt'))
        result['curves'][arm]=history
        result['results'][arm]=metrics(yv,model.predict(val[arm],num_threads=THREADS),wv)|{'trees':model.best_iteration}
        if arm=='harm':
            result['results']['harm_given_viirs']=metrics(yv,model.predict(val['viirs'],num_threads=THREADS),wv)
        log.info('%s: %s',arm,result['results'][arm])
        (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    lines=['# Controlled summer pilot: validation 2020','',
           'Same VIIRS targets and sampled rows; causal harmonization trained on separate 2012–2014 years. Day-t weather only. July–August, common clear looks. Exploratory validation, not a test result.','',
           '| Input arm | AP | AUC | Log loss | Trees |','|---|---:|---:|---:|---:|']
    for arm,r in result['results'].items():
        lines.append(f"| {arm} | {r['ap']:.4f} | {r['auc']:.4f} | {r['logloss']:.6f} | {r.get('trees','—')} |")
    (OUT/'results.md').write_text('\n'.join(lines)+'\n')
    (OUT/'complete').touch();log.info('COMPLETE')

if __name__=='__main__': main()
