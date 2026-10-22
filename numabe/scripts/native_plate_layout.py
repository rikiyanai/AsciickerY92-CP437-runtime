"""Hard native-text locks for fictional plate numbers in authored car pages.

This is a small build-time extension of the pinned chapel fitter, not a change
to that project's sources. Complete native rows remain the output authority.
The immutable executed C++ is retained with each authored target.
"""
import ctypes
import math
import subprocess
import numpy as np
from PIL import Image,ImageDraw


def enable(core,fitter):
    source=(core.CHAPEL/'joint_fit.cpp').read_text()
    needle='extern "C" void fit_page('
    inject='''static std::vector<int> text_forced,text_prefix;
static int text_width=0,text_height=0;
extern "C" void set_text_locks(const int* forced,const int* reserved,int W,int H){
 text_width=W;text_height=H;
 text_forced.assign(forced,forced+(size_t)W*H);
 text_prefix.assign((size_t)(W+1)*H,0);
 for(int y=0;y<H;y++)for(int x=0;x<W;x++)
  text_prefix[(size_t)y*(W+1)+x+1]=text_prefix[(size_t)y*(W+1)+x]+reserved[(size_t)y*W+x];
}
'''
    candidate='const int to=x+advances[g];if(to>end)continue;'
    constraint=candidate+'''
    if(text_width==W && text_height==H && !text_forced.empty()){
     const size_t r=(size_t)y0*(W+1);
     if(text_prefix[r+to]!=text_prefix[r+x] && text_forced[(size_t)y0*W+x]!=g)continue;
    }'''
    assert source.count(needle)==source.count(candidate)==1
    source=source.replace(needle,inject+'\n'+needle).replace(candidate,constraint)
    cpp=core.BUILD/'joint_fit_with_native_text.cpp';library=core.BUILD/'libjointfit_native_text.so'
    if not cpp.exists() or cpp.read_text()!=source:cpp.write_text(source)
    if not library.exists() or library.stat().st_mtime<cpp.stat().st_mtime:
        subprocess.run(['c++','-O3','-std=c++17','-shared','-fPIC',str(cpp),'-o',str(library)],check=True)
    lib=ctypes.CDLL(str(library));fn=lib.fit_page;fn.argtypes=fitter.fn.argtypes
    setter=lib.set_text_locks;setter.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
    fitter.fn=fn;fitter.native_text_library=lib;fitter.text_setter=setter
    return cpp


def add_registration(core,plan,drawing,fitter):
    """Optically set intact, unwarped text only within its visible plate face.

    At acute/hidden angles the number cannot fit and is not displayed as an
    oversized billboard. The physical plate remains drawn. At readable angles
    all text tokens are hard row constraints, never a post-raster overlay.
    """
    W,H=core.W,core.H;forced=np.full((H,W),-1,np.int32);reserved=np.zeros((H,W),np.int32)
    bychar={g['char']:i for i,g in enumerate(fitter.bank)};cam=drawing['camera'];labels=[];new_samples=[]
    for plate in plan.plates:
        if np.dot(cam.eye-np.array(plate['center']),plate['normal'])<=0:continue
        chars=plate['visible_text'];gis=[bychar[ch] for ch in chars]
        advance=sum(int(fitter.adv[g]) for g in gis)
        points=np.asarray(plate['corners']);projected=cam.project(points)
        mask=Image.new('1',(W,H));ImageDraw.Draw(mask).polygon([tuple(q[:2]) for q in projected],fill=1)
        allowed=np.asarray(mask,bool)
        center=cam.project(plate['center']);x0=int(round(center[0]-advance/2))
        best=None
        for y0 in sorted({int(math.floor((center[1]-8)/17))*17+j*17 for j in (-1,0,1)},key=lambda y:abs(y+8-center[1])):
            if x0<0 or x0+advance>W or y0<0 or y0+17>H:continue
            origin=x0;candidate=[];ink=[]
            for gi in gis:
                pts=fitter.bank[gi]['points']+np.array([origin,y0]);ink.extend(pts.tolist());candidate.append((origin,y0,gi));origin+=int(fitter.adv[gi])
            ink=np.asarray(ink,int)
            if not np.all(allowed[ink[:,1],ink[:,0]]):continue
            # Every semantic ink sample has a ray/plane attachment and current
            # depth test. Hidden registrations never bleed through the body.
            rays=cam.forward+(ink[:,0,None]-W/2)/cam.focal*cam.right+(H/2-ink[:,1,None])/cam.focal*cam.up
            distance=(plate['center'][2]-cam.eye[2])/rays[:,2]
            world=cam.eye+rays*distance[:,None];q=cam.project(world)
            visible=1/q[:,2]>=drawing['depth'][ink[:,1],ink[:,0]]-.045/q[:,2]**2
            if not np.all(visible):continue
            best=(candidate,ink,world,q);break
        if best is None:continue
        candidate,ink,world,q=best;fi=len(drawing['features'])
        drawing['features'].append(dict(id=plate['id']+'.registration',refs=plan.spec['refs'],role='Fictional Japanese kana and serial, hard native-text tokens'))
        for x,y,gi in candidate:
            forced[y,x]=gi;reserved[y,x:x+int(fitter.adv[gi])]=1
        # Other selected construction cannot compete inside the number's row
        # span; the plate itself provides white paper behind the semantic ink.
        y=candidate[0][1]
        drawing['target'][y:y+17,x0:x0+advance]=0
        drawing['target'][ink[:,1],ink[:,0]]=1
        drawing['void'][ink[:,1],ink[:,0]]=0
        for j,(pixel,wp,pp) in enumerate(zip(ink,world,q)):
            new_samples.append([*pixel,pp[2],0,0,*wp,fi,j])
        labels.append(dict(id=plate['id'],fictional_registration=plate['registration'],visible_text=chars,
                           x=x0,y=y,width=advance,tokens=[dict(x=x,y=y,gi=g,text=fitter.bank[g]['text']) for x,y,g in candidate]))
    if new_samples:drawing['samples']=np.concatenate([drawing['samples'],np.asarray(new_samples,float)])
    fitter.text_setter(forced.ctypes.data,reserved.ctypes.data,W,H)
    return labels
