# Ad hoc script: PPM color histogram: classify a captured frame into black/green/yellow-marker/red-marker/water/other buckets to confirm CP437 baseline has no PROFILE marker colors (FL-4260 RQ-146 checkpoint)
# Created: 2026-06-15
# Canonical gap: <describe what tool should own this>

import sys
def load_ppm(path):
    with open(path,'rb') as f:
        assert f.readline().strip()==b'P6'
        line=f.readline()
        while line.startswith(b'#'): line=f.readline()
        w,h=map(int,line.split())
        mx=int(f.readline()); data=f.read(w*h*3)
    return w,h,data
def classify(r,g,b):
    if r<24 and g<24 and b<24: return 'black'
    # PROFILE markers: MISSING_POLICY bg196 ~ (255,0,0) red; MISSING_GLYPH fg226 yellow on bg16 black
    if r>180 and g<70 and b<70: return 'red_marker'        # bg196 MISSING_POLICY
    if r>200 and g>200 and b<90: return 'yellow_marker'    # fg226 MISSING_GLYPH glyph
    if g>r and g>b and g>60: return 'green_terrain'
    if b>r and b>g and b>60: return 'water_blue'
    return 'other'
def main():
    path=sys.argv[1] if len(sys.argv)>1 else None
    w,h,data=load_ppm(path)
    from collections import Counter
    c=Counter(); n=w*h
    # sample every 4th pixel for speed
    step=4
    for i in range(0,n,step):
        o=i*3; c[classify(data[o],data[o+1],data[o+2])]+=1
    tot=sum(c.values())
    print(f"frame {path} {w}x{h} sampled={tot}")
    for k in ['black','green_terrain','water_blue','yellow_marker','red_marker','other']:
        print(f"  {k:14s} {100.0*c[k]/tot:6.2f}%")
    markers=c['yellow_marker']+c['red_marker']
    print(f"  PROFILE_MARKER_TOTAL {100.0*markers/tot:.3f}%  (CP437 baseline expectation: ~0)")
if __name__=='__main__': main()
