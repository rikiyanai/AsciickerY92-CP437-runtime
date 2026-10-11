#!/usr/bin/env python3
"""Crop+zoom the cylinder thumbnail region (1600x1200 frames) into a side-by-side proof."""
import os
from PIL import Image
BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
       'docs/research/ascii/verification/fl4260/2026-06-24-thumbnail-visibility'))
box = (606, 44, 726, 268)  # left, top, right, bottom in true 1600x1200 coords
pairs = [("02_panel_top_thumbnail/ui_frame.png", "default"),
         ("03_thumbnail_preset_applied/ui_frame.png", "preset")]
scale = 3
crops = []
for rel, _ in pairs:
    im = Image.open(os.path.join(BASE, rel)).convert("RGB").crop(box)
    crops.append(im.resize((im.width*scale, im.height*scale), Image.NEAREST))
gap = 30
W = sum(c.width for c in crops) + gap
H = max(c.height for c in crops)
out = Image.new("RGB", (W, H), (30,30,30))
x = 0
for c in crops:
    out.paste(c, (x, 0)); x += c.width + gap
dst = os.path.join(BASE, "thumbnail_before_after.png")
out.save(dst)
print("WROTE", dst, out.size)
