#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "matplotlib", "shapely>=2.1", "pyproj", "rasterio", "scipy"]
# ///
"""Plot and independently recheck three completed August 2 perimeter cases."""
import json,logging,os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/ignopticon-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch,Patch
from matplotlib.path import Path as MPath
from matplotlib.lines import Line2D
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape,Polygon
from shapely.geometry.polygon import orient
import shapely
from pyproj import Transformer
from elmfire_perimeters import known_start
from perimeter_batch import scores
from rasterio.features import rasterize

OUT=Path('data/perimeter_demo');IDS=[66510,11887,18872]
NAMES=['Widemouth 2, Utah','FEDS 11887 · Butler County, Ohio','FEDS 18872 · Berkeley County, South Carolina']

def main():
 OUT.mkdir(exist_ok=True)
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(filename)s:%(lineno)d %(message)s',handlers=[logging.StreamHandler(),logging.FileHandler(OUT/'check.log')],force=True)
 fig,axes=plt.subplots(3,2,figsize=(14,15));report=[]
 for row,(fid,name) in enumerate(zip(IDS,NAMES)):
  root=Path(f'data/perimeter_batch/map20260802-{fid}')
  env=json.loads((root/'envelopes.geojson').read_text());obs=json.loads((root/'associated-observed.json').read_text())
  folder=root/'runs/adj1-wind1/outputs';meta=json.loads((folder/'run.json').read_text())
  with rasterio.open(next(folder.glob('time_of_arrival_*.tif'))) as src:
   toa=src.read(1);valid=(src.read_masks(1)>0)&np.isfinite(toa)&(toa>=0);tr,crs,res=src.transform,src.crs,src.res
  start,_=known_start(meta,toa.shape,tr,crs);start|=valid&(toa==0)
  startgeom=shapely.union_all([shape(g) for g,v in shapes(start.astype(np.uint8),mask=start,transform=tr)])
  centre=startgeom.centroid;cx,cy=centre.x,centre.y
  ll=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
  def project(g): return shapely.transform(g,lambda xy:np.column_stack(ll.transform(xy[:,0],xy[:,1])))
  def draw(ax,g,face='none',edge='none',lw=1,ls='-',alpha=1,z=1):
   for p in getattr(g,'geoms',[g]):
    if p.is_empty or p.geom_type!='Polygon': continue
    p=orient(p,1);vertices=[];codes=[]
    for ring in [p.exterior,*p.interiors]:
     a=np.array(ring.coords);a[:,0]=(a[:,0]-cx)/1000;a[:,1]=(a[:,1]-cy)/1000
     vertices.extend(a);codes.extend([MPath.MOVETO]+[MPath.LINETO]*(len(a)-2)+[MPath.CLOSEPOLY])
    ax.add_patch(PathPatch(MPath(vertices,codes),facecolor=face,edgecolor=edge,lw=lw,ls=ls,alpha=alpha,zorder=z))
  allgeo=[project(shape(f['geometry'])) for f in env['features']]+[startgeom]
  future={p['time']:project(shape(p['geometry'])) for p in obs['perimeters'] if p['time'] in ['2026-08-03T00:00Z','2026-08-03T12:00Z']}
  allgeo+=list(future.values());bounds=shapely.union_all(allgeo).bounds
  half=max(bounds[2]-bounds[0],bounds[3]-bounds[1])/2000*1.07
  xmid=((bounds[0]+bounds[2])/2-cx)/1000;ymid=((bounds[1]+bounds[3])/2-cy)/1000
  stored=json.loads((root/'scores.json').read_text())['adj1-wind1']
  for col,h in enumerate([12,24]):
   ax=axes[row,col]
   for f,color in zip([f for f in env['features'] if f['properties']['hours']==h],['#f4d58d','#65b6c5','#33789a']):
    draw(ax,project(shape(f['geometry'])),face=color,z=1)
   draw(ax,startgeom,edge='#343434',lw=.9,ls='--',z=3)
   pred=start|(valid&(toa<=h*3600))
   baseline=shapely.union_all([shape(g) for g,v in shapes(pred.astype(np.uint8),mask=pred,transform=tr)])
   draw(ax,baseline,edge='#6a3d9a',lw=.8,ls=':',z=4)
   stamp='2026-08-03T00:00Z' if h==12 else '2026-08-03T12:00Z'
   if stamp in future:
    truth=future[stamp];draw(ax,truth,edge='#d02b2b',lw=1.1,z=5)
    mask=rasterize([(truth,1)],out_shape=toa.shape,transform=tr,dtype='uint8').astype(bool)
    checked=scores(pred,mask,start,res);prior=next(s for s in stored if s['hours']==h)
    for key in ('iou','growth_iou','no_growth_iou'): assert checked[key]==prior[key],(fid,h,key)
    assert checked['boundary']==prior['boundary']
    report.append({'fire':fid,'hours':h,**checked})
    ax.text(.02,.02,f"Baseline growth IoU {checked['growth_iou']:.3f}\nMean boundary error {checked['boundary']['mean_m']/1000:.2f} km",transform=ax.transAxes,fontsize=9,bbox=dict(facecolor='white',alpha=.9,edgecolor='none'),zorder=6)
   else:
    ax.text(.02,.02,'No later FEDS perimeter: cannot validate',transform=ax.transAxes,fontsize=9,bbox=dict(facecolor='white',alpha=.9,edgecolor='none'),zorder=6)
   ax.set(xlim=(xmid-half,xmid+half),ylim=(ymid-half,ymid+half),aspect='equal',xlabel='East / west (km)',ylabel='North / south (km)',title=f'{name}\n+{h} h');ax.grid(alpha=.15)
  logging.info('Rechecked %s: %d scoring timestamps',fid,len(stored))
 fig.suptitle('ELMFIRE: three-fire check before scaling up\nIssued August 2, 2026 · 12:00 UTC · each row has its own distance scale',fontsize=16)
 handles=[Patch(color=c,label=l) for c,l in [('#f4d58d','≥1/10 members'),('#65b6c5','≥5/10 members'),('#33789a','≥9/10 members')]]
 handles += [Line2D([],[],color='#343434',ls='--',label='Initial footprint'),Line2D([],[],color='#6a3d9a',ls=':',label='Unadjusted baseline'),Line2D([],[],color='#d02b2b',label='Later FEDS outline')]
 fig.legend(handles=handles,loc='lower center',ncol=3,fontsize=10)
 fig.tight_layout(rect=[0,.065,1,.95]);fig.savefig(OUT/'three-fire-check.png',dpi=130);plt.close(fig)
 (OUT/'verified_scores.json').write_text(json.dumps(report,indent=2)+'\n')
 logging.info('Wrote demonstration and independently verified scores')

if __name__=='__main__':main()
