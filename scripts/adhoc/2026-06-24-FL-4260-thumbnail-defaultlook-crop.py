#!/usr/bin/env python3
"""Side-by-side: OLD black default-look vs NEW material-color default-look (mat1, mat5)."""
import os
from PIL import Image
V = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
    'docs/research/ascii/verification/fl4260'))
box = (606, 44, 726, 268); scale = 3
items = [
  (os.path.join(V,'2026-06-24-thumbnail-visibility/02_panel_top_thumbnail/ui_frame.png'), "OLD mat1 (black)"),
  (os.path.join(V,'2026-06-24-thumbnail-defaultlook-fix/01_mat1_default_look_top/ui_frame.png'), "NEW mat1"),
  (os.path.join(V,'2026-06-24-thumbnail-defaultlook-fix/02_mat5_default_look_top/ui_frame.png'), "NEW mat5"),
]
crops=[]
for path,_ in items:
    im=Image.open(path).convert("RGB").crop(box)
    crops.append(im.resize((im.width*scale, im.height*scale), Image.NEAREST))
gap=30; W=sum(c.width for c in crops)+gap*(len(crops)-1); H=max(c.height for c in crops)
out=Image.new("RGB",(W,H),(30,30,30)); x=0
for c in crops: out.paste(c,(x,0)); x+=c.width+gap
dst=os.path.join(V,'2026-06-24-thumbnail-defaultlook-fix','before_after_default_look.png')
out.save(dst); print("WROTE", dst, out.size)
# also sample center pixel of top band for each NEW capture to prove non-black
for path,label in items:
    im=Image.open(path).convert("RGB")
    px=im.getpixel((660, 110))  # inside top band region
    print(f"PIXEL {label}: {px}")
