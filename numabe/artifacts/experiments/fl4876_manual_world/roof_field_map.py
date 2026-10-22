#!/usr/bin/env python3
"""roof_field_map.py ROOF_FIELD_JSON [CELL_M] -- FL-4876 listing aid (measures, decides nothing).

Prints the raw TMG LiDAR roof heights of one building's roof field as a text map (north up, east right), one
character per CELL_M (default 1.5) square: the median raw height in metres above ground, as 0-9 then a-z for 10-35 m,
'.' for lattice cells without a measurement, ' ' outside the lattice. Also prints the E/N origin of the map so a person
can read off where a tall roof (a hall) and a low wing meet when splitting an outline by hand.
"""
import json
import statistics
import sys
from pathlib import Path

doc = json.loads(Path(sys.argv[1]).read_text())
cell = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
sc = float(doc["scene"]["content_scale"])
st = float(doc["grid"]["step"])
meta = doc["scene"]
oe, on = float(meta.get("origin_easting", 0)), float(meta.get("origin_northing", 0))
ox, oy = float(meta.get("offset_x", 0)), float(meta.get("offset_y", 0))
pts = []
for c in doc["cells"]:
    x, y = c["i"] * st, c["j"] * st  # world units
    e, n = oe + (x - ox) / sc, on + (y - oy) / sc
    pts.append((e, n, c.get("raw_m") if c.get("n", 0) >= 1 else None))
e0, e1 = min(p[0] for p in pts), max(p[0] for p in pts)
n0, n1 = min(p[1] for p in pts), max(p[1] for p in pts)
W, H = int((e1 - e0) / cell) + 1, int((n1 - n0) / cell) + 1
bins = {}
for e, n, z in pts:
    k = (int((e - e0) / cell), int((n1 - n) / cell))
    bins.setdefault(k, []).append(z)
print("%s  origin (top-left) E %.1f N %.1f, %.1f m per character, %d x %d" % (
    doc["building"]["source"], e0, n1, cell, W, H))
for r in range(H):
    line = ""
    for c in range(W):
        v = bins.get((c, r))
        if v is None:
            line += " "
            continue
        zs = [z for z in v if z is not None]
        if not zs:
            line += "."
            continue
        m = int(round(statistics.median(zs)))
        line += str(m) if m < 10 else chr(ord("a") + min(25, m - 10))
    print("  " + line)
