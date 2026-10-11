#!/usr/bin/env python3
"""Wallace: surgically restore the 8 cells where the session JSON diverges
from the authoritative XP (mistaken paste during cp/paste debug, 2026-06-01).
Updates both session.layers[2] and session.cells, in place."""
import json
import shutil
import sys
from pathlib import Path

V3 = Path("/Users/r/Downloads/asciicker-pipeline-v3")
Y9 = Path("/Users/r/Downloads/asciicker-Y9-2")
sys.path.insert(0, str(Y9 / "scripts" / "pipeline"))
from xp_core import XPFile

SESS = V3 / "data/sessions/7a7dd262-6c1c-47f4-b2f5-3a267c01c373.json"
XP = Y9 / "assets/sprites/2026-05-28-wallace.xp"
BACKUP = V3 / "asciicker-dumpster/cp-fix-2026-06-01" / (SESS.name + ".bak-before-unpaste.json")

BACKUP.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(SESS, BACKUP)
print(f"backup -> {BACKUP}")

with open(SESS) as f:
    s = json.load(f)
cols = int(s["grid_cols"])

xp = XPFile()
xp.load(str(XP))
xp_l2 = xp.layers[2]
sess_l2 = s["layers"][2]
sess_cells = s.get("cells", [])

restored = 0
details = []
for ay in range(min(int(s["grid_rows"]), xp_l2.height)):
    for ax in range(min(cols, xp_l2.width)):
        idx = ay * cols + ax
        if idx >= len(sess_l2):
            continue
        x_g, x_fg, x_bg = xp_l2.data[ay][ax]
        s_cell = sess_l2[idx] or {}
        s_g = int(s_cell.get("glyph", 0))
        s_fg = list(s_cell.get("fg") or [0, 0, 0])
        s_bg = list(s_cell.get("bg") or [0, 0, 0])
        if s_g == x_g and s_fg == list(x_fg) and s_bg == list(x_bg):
            continue
        new_cell = {"idx": idx, "glyph": int(x_g), "fg": list(x_fg), "bg": list(x_bg)}
        sess_l2[idx] = new_cell
        if isinstance(sess_cells, list) and idx < len(sess_cells):
            sess_cells[idx] = dict(new_cell)
        details.append((ax, ay, (s_g, tuple(s_fg), tuple(s_bg)), (int(x_g), tuple(x_fg), tuple(x_bg))))
        restored += 1

s["layers"][2] = sess_l2
if isinstance(sess_cells, list):
    s["cells"] = sess_cells

with open(SESS, "w") as f:
    json.dump(s, f)

print(f"restored {restored} cells in {SESS.name}")
for ax, ay, before, after in details:
    print(f"  ({ax},{ay})  was {before}  ->  {after}")
