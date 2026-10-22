"""track_offset.py WAY_ID E N [OFFSET_M] -- list/measure only: the nearest point of a line feature (build_reg_75
canonical) to (E, N), the point's perpendicular distance and side, and the point OFFSET_M from the line on the same
side (where a trackside / roadside object of known offset would stand). Prints; writes nothing."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
way, E, N = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
off = float(sys.argv[4]) if len(sys.argv) > 4 else None
pts = None
for f in json.loads((ROOT / ".run/fl4876_builds/build_reg_75/canonical.world_ir.json").read_text())["canonical_features"]:
    if f["feature_id"].endswith("/" + way):
        pts = [tuple(p[:2]) for p in f["geometry"]["coordinates"]]
best = None
for a, b in zip(pts, pts[1:]):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((E - a[0]) * dx + (N - a[1]) * dy) / (dx * dx + dy * dy)))
    q = (a[0] + t * dx, a[1] + t * dy)
    d = math.hypot(E - q[0], N - q[1])
    if best is None or d < best[0]:
        best = (d, q, math.degrees(math.atan2(dx, dy)) % 360)
d, q, brg = best
print("nearest point (%.2f, %.2f), distance %.2f m, line bearing %.1f" % (q[0], q[1], d, brg))
if off is not None:
    ux, uy = (E - q[0]) / d, (N - q[1]) / d
    print("at %.2f m on the same side: (%.2f, %.2f)" % (off, q[0] + ux * off, q[1] + uy * off))
