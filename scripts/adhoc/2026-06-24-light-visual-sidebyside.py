#!/usr/bin/env python3
from PIL import Image, ImageDraw
import os
B="/tmp/fl_light"
items=[("A_yaw45.png","sun yaw=45 pitch=30"),("B_yawflip.png","yaw=-135 pitch=30"),("C_pitch85.png","yaw=45 pitch=85")]
imgs=[]
for f,lab in items:
    p=os.path.join(B,f)
    if not os.path.exists(p): print("MISSING",p); continue
    im=Image.open(p).convert("RGB")
    imgs.append((im,lab))
if imgs:
    w=max(i.width for i,_ in imgs); h=max(i.height for i,_ in imgs)
    gap=12; lblh=22
    out=Image.new("RGB",(w*len(imgs)+gap*(len(imgs)-1), h+lblh),(20,20,20))
    d=ImageDraw.Draw(out); x=0
    for im,lab in imgs:
        out.paste(im,(x,lblh)); d.text((x+4,4),lab,fill=(255,255,0)); x+=im.width+gap
    dst=os.path.join(B,"light_sidebyside.png"); out.save(dst); print("WROTE",dst,out.size)
