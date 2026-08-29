# Ad hoc script: FL-4184 flower visibility audit — count distinctive flower bloom colours (pink/yellow/magenta/red authored at sprite.cpp:3088-3132) in post-fix candidate render; helps prove whether flowers are painted but invisible, or simply not painted
# Created: 2026-06-02
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Audit whether flower bloom colours are actually being painted in the
candidate render. Foliage stamp bloom colours are authored at
ascii_compositor.glsl:3088-3132 as bright distinct flower hues:
  - tulip:      (0.92, 0.32, 0.66)  hot pink
  - sunflower:  (0.96, 0.84, 0.22)  bright yellow
  - wildflower: (0.88, 0.32, 0.88)  magenta
  - rose:       (0.86, 0.18, 0.18)  red

After tonemap/ambient/lambert the colours land somewhere near
(218, 50, 158), (242, 196, 33), (200, 50, 200), (200, 35, 35).
Sample with a wide hue tolerance and count cells matching each flower hue
family vs the green grass background.
"""
from __future__ import annotations
import sys, colorsys
from pathlib import Path
from PIL import Image

FLOWER_HUES = [
    ("tulip-pink",  330.0/360.0),
    ("sunflower-yel", 52.0/360.0),
    ("wildflower-mag", 300.0/360.0),
    ("rose-red",      0.0/360.0),
]
HUE_TOL  = 18.0/360.0
SAT_MIN  = 0.30
VAL_MIN  = 0.30


def classify(rgb):
    r, g, b = rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < VAL_MIN or s < SAT_MIN:
        return None
    # green grass dominant range -> skip
    if 0.18 <= h <= 0.45:
        return "green-bg"
    for name, target_h in FLOWER_HUES:
        d = min(abs(h - target_h), 1.0 - abs(h - target_h))
        if d <= HUE_TOL:
            return name
    return "other-hue"


def main():
    if len(sys.argv) < 2:
        print("usage: <this> <png_or_dir>", file=sys.stderr); raise SystemExit(2)
    target = Path(sys.argv[1])
    pngs = sorted(target.rglob("yaw_*.png")) if target.is_dir() else [target]
    for png in pngs:
        im = Image.open(png).convert("RGB")
        counts: dict[str, int] = {"green-bg": 0, "other-hue": 0, "low-sat-val": 0}
        for n, _ in FLOWER_HUES:
            counts[n] = 0
        total = 0
        for px in im.getdata():
            total += 1
            k = classify(px)
            if k is None:
                counts["low-sat-val"] += 1
            else:
                counts[k] += 1
        flower_total = sum(counts[n] for n, _ in FLOWER_HUES)
        print(f"\n=== {png} ({total} px) ===")
        for k in ["tulip-pink", "sunflower-yel", "wildflower-mag", "rose-red",
                  "other-hue", "green-bg", "low-sat-val"]:
            v = counts[k]
            print(f"  {k:20s} {v:>10d}  {(v/total)*100:6.3f}%")
        print(f"  FLOWER TOTAL         {flower_total:>10d}  {(flower_total/total)*100:6.3f}%")


if __name__ == "__main__":
    main()
