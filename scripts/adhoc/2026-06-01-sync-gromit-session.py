#!/usr/bin/env python3
"""Sync 2026-05-28-gromit.xp into the live Gromit session JSON."""
import json, sys
from pathlib import Path

V3  = Path("/Users/r/Downloads/asciicker-pipeline-v3")
Y9  = Path("/Users/r/Downloads/asciicker-Y9-2")
SES = V3 / "data/sessions/5050301b-a087-4c69-8976-15256c0641db.json"
XP  = Y9 / "assets/sprites/2026-05-28-gromit.xp"
sys.path.insert(0, str(Y9 / "scripts/pipeline"))
from xp_core import XPFile

xp = XPFile(); xp.load(str(XP))
s = json.loads(SES.read_text())
gc = s["grid_cols"]; gr = s["grid_rows"]
assert xp.layers[0].width == gc and xp.layers[0].height == gr, f"dim mismatch xp={xp.layers[0].width}x{xp.layers[0].height} sess={gc}x{gr}"

def layer_to_cells(layer):
    out = []
    for ay in range(gr):
        for ax in range(gc):
            g, fg, bg = layer.data[ay][ax]
            out.append({"idx": ay*gc+ax, "glyph": g, "fg": list(fg), "bg": list(bg)})
    return out

s["layers"] = [layer_to_cells(L) for L in xp.layers]
s["cells"]  = layer_to_cells(xp.layers[2])
SES.write_text(json.dumps(s))
print(f"synced session {SES.name} from {XP.name}")
