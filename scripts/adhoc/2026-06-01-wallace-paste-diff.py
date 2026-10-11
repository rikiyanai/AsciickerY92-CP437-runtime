#!/usr/bin/env python3
"""Diff Wallace session JSON visual layer (layer 2) vs the XP file's layer 2.
Reports any cells where session diverges from XP (likely the mistakenly pasted region)."""
import json
import sys
from pathlib import Path

V3 = Path("/Users/r/Downloads/asciicker-pipeline-v3")
Y9 = Path("/Users/r/Downloads/asciicker-Y9-2")
sys.path.insert(0, str(Y9 / "scripts" / "pipeline"))
from xp_core import XPFile

SESS = V3 / "data/sessions/7a7dd262-6c1c-47f4-b2f5-3a267c01c373.json"
XP = Y9 / "assets/sprites/2026-05-28-wallace.xp"

with open(SESS) as f:
    s = json.load(f)

cols = int(s["grid_cols"])
rows = int(s["grid_rows"])
print(f"session: {cols}x{rows} cells, {len(s['layers'])} layers")

xp = XPFile()
xp.load(str(XP))
print(f"xp: {xp.layers[2].width}x{xp.layers[2].height}, {len(xp.layers)} layers")

# Compare layer 2 (visual)
sess_l2 = s["layers"][2]
xp_l2 = xp.layers[2]

if len(sess_l2) != cols * rows:
    print(f"WARN: session layer 2 has {len(sess_l2)} cells, expected {cols*rows}")

diffs = []
for ay in range(min(rows, xp_l2.height)):
    for ax in range(min(cols, xp_l2.width)):
        idx = ay * cols + ax
        s_cell = sess_l2[idx] if idx < len(sess_l2) else None
        x_g, x_fg, x_bg = xp_l2.data[ay][ax]
        if s_cell is None:
            continue
        s_g = int(s_cell.get("glyph", 0))
        s_fg = tuple(int(v) for v in (s_cell.get("fg") or [0,0,0]))
        s_bg = tuple(int(v) for v in (s_cell.get("bg") or [0,0,0]))
        if s_g != x_g or s_fg != tuple(x_fg) or s_bg != tuple(x_bg):
            diffs.append((ax, ay, (s_g, s_fg, s_bg), (x_g, tuple(x_fg), tuple(x_bg))))

print(f"\n{len(diffs)} divergent cells")
if not diffs:
    print("OK: session matches XP — no paste/edit detected on visual layer")
    sys.exit(0)

xs = sorted({d[0] for d in diffs})
ys = sorted({d[1] for d in diffs})
print(f"x range: {min(xs)}..{max(xs)} ({len(xs)} unique cols)")
print(f"y range: {min(ys)}..{max(ys)} ({len(ys)} unique rows)")

# Show first 30 diffs
print("\nfirst 30 diffs (x,y) [session glyph,fg,bg] -> [xp glyph,fg,bg]:")
for ax, ay, s_cell, x_cell in diffs[:30]:
    print(f"  ({ax},{ay})  sess={s_cell}  xp={x_cell}")

# Also compare cells (top-level rendered list) which the editor uses
sess_cells = s.get("cells", [])
if isinstance(sess_cells, list) and len(sess_cells) == len(sess_l2):
    cells_diffs = 0
    for idx in range(len(sess_l2)):
        l2c = sess_l2[idx]
        cc = sess_cells[idx]
        if not l2c or not cc:
            continue
        if int(l2c.get("glyph",0)) != int(cc.get("glyph",0)) or l2c.get("fg") != cc.get("fg") or l2c.get("bg") != cc.get("bg"):
            cells_diffs += 1
    print(f"\nsess.layers[2] vs sess.cells: {cells_diffs} divergences")
