# Ad hoc script: Localize + crop the changed-pixel region between two SAR capture PNGs (before/after): emits bbox, changed-pixel count, a red diff-overlay, and margin crops of both frames so an agent can confirm WHERE a draft-edit delta landed (embedded TERM++ FBO vs sidebar chrome)
# Created: 2026-06-17
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Localize the changed region between two PNG frames (SAR delta forensics).
Usage: python3 <this> BEFORE.png AFTER.png OUTDIR [--thresh 24]
"""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("before"); ap.add_argument("after"); ap.add_argument("outdir")
ap.add_argument("--thresh", type=int, default=24)
a = ap.parse_args()
out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

ia = Image.open(a.before).convert("RGB")
ib = Image.open(a.after).convert("RGB")
assert ia.size == ib.size, f"size mismatch {ia.size} {ib.size}"
na = np.asarray(ia, dtype=np.int16); nb = np.asarray(ib, dtype=np.int16)
diff = np.abs(na - nb).max(axis=2)           # per-pixel max channel delta
mask = diff > a.thresh                        # h x w bool
changed = int(mask.sum())
ys, xs = np.where(mask)
w, h = ia.size
if changed:
    minx, maxx, miny, maxy = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    over = np.asarray(ib).copy()
    over[mask] = (255, 0, 0)
    Image.fromarray(over).save(out / "diff_overlay_red.png")
    m = 40
    box = (max(0, minx - m), max(0, miny - m), min(w, maxx + m), min(h, maxy + m))
    ia.crop(box).save(out / "before_crop.png")
    ib.crop(box).save(out / "after_crop.png")
    Image.fromarray(over).crop(box).save(out / "overlay_crop.png")
    print(f"changed={changed} bbox=({minx},{miny})-({maxx},{maxy}) frame={w}x{h} crop_box={box}")
else:
    print(f"changed=0 frame={w}x{h}")
