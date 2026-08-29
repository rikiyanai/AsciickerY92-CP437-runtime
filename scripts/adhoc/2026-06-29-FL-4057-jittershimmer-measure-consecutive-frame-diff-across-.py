# Ad hoc script: FL-4057 jitter/shimmer measure: consecutive-frame diff across a sub-cell camera sweep, probe vs legacy
# Created: 2026-06-29
# Canonical gap: <describe what tool should own this>

# saved for reuse; executed separately
import sys, os, json
from PIL import Image
import numpy as np

def series(prefix, n):
    frames=[]
    for i in range(n):
        p=f"{prefix}_{i}.png"
        if not os.path.isfile(p): return None
        frames.append(np.asarray(Image.open(p).convert("RGB"),dtype=np.int16))
    out=[]
    for i in range(1,len(frames)):
        d=np.abs(frames[i]-frames[i-1])
        per=d.max(axis=2)
        out.append({"pair":f"{i-1}->{i}","mad":round(float(d.mean()),3),
                    "pct_changed_gt24":round(float((per>24).mean()*100),2)})
    return out

n=6
pr=series("/tmp/jit_probe",n); lg=series("/tmp/jit_legacy",n)
print("PROBE  ON :", json.dumps(pr))
print("LEGACY OFF:", json.dumps(lg))
# no-resnap window = pairs 0->1..3->4 (offsets <0.5); pair 4->5 crosses 0.5 (probe re-snaps)
def windowmad(s, lo, hi): return round(sum(x["mad"] for x in s[lo:hi])/max(hi-lo,1),3)
if pr and lg:
    print(f"NO-RESNAP mean MAD (pairs 0..3): probe={windowmad(pr,0,4)}  legacy={windowmad(lg,0,4)}")
    print(f"RESNAP pair 4->5 MAD: probe={pr[4]['mad']}  legacy={lg[4]['mad']}")

# Accumulated flicker map: per pixel, max consecutive-frame change across the
# whole sweep. Bright = that screen cell flickered under sub-cell camera motion.
def flicker_map(prefix, n):
    acc=None
    prev=None
    for i in range(n):
        im=np.asarray(Image.open(f"{prefix}_{i}.png").convert("RGB"),dtype=np.int16)
        if prev is not None:
            d=np.abs(im-prev).max(axis=2)
            acc=d if acc is None else np.maximum(acc,d)
        prev=im
    return acc
pf=flicker_map("/tmp/jit_probe",6); lf=flicker_map("/tmp/jit_legacy",6)
if pf is not None and lf is not None:
    from PIL import ImageDraw
    h,w=pf.shape
    canvas=Image.new("RGB",(w,h*2),(16,16,16))
    def tint(a):
        v=np.clip(a,0,64)/64*255
        return Image.fromarray(v.astype('uint8')).convert("RGB")
    top=tint(lf); bot=tint(pf)
    d=ImageDraw.Draw(top); d.rectangle([0,0,360,20],fill=(0,0,0)); d.text((4,4),"LEGACY shimmer (bright=flicker)",fill=(255,80,80))
    d=ImageDraw.Draw(bot); d.rectangle([0,0,360,20],fill=(0,0,0)); d.text((4,4),"PROBE shimmer (bright=flicker)",fill=(120,255,120))
    canvas.paste(top,(0,0)); canvas.paste(bot,(0,h))
    canvas.save("/tmp/jit_flicker_compare.png")
    print(f"LEGACY flicker: mean={float(lf.mean()):.2f} pct>24={float((lf>24).mean()*100):.1f}%")
    print(f"PROBE  flicker: mean={float(pf.mean()):.2f} pct>24={float((pf>24).mean()*100):.1f}%")
    print("WROTE /tmp/jit_flicker_compare.png")
