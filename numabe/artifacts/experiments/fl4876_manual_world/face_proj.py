"""face_proj.py BLDG FACE E N [BEARING ...] -- face endpoints, the point's along-face position and distance
from the face line, and for each bearing the along-face position where the camera ray meets the face line
(may fall outside the face). List/measure only."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_73/canonical.world_ir.json").read_text())
bid = "feature:building_footprint:way/" + sys.argv[1]
fi = int(sys.argv[2])
E, N = float(sys.argv[3]), float(sys.argv[4])
ring = None
for f in canon["canonical_features"]:
    if f["feature_id"] == bid:
        c = f["geometry"]["coordinates"]
        ring = [tuple(p[:2]) for p in (c[0] if isinstance(c[0][0], list) else c)]
if ring[0] == ring[-1]:
    ring = ring[:-1]
a, b = ring[fi], ring[(fi + 1) % len(ring)]
dx, dy = b[0] - a[0], b[1] - a[1]
L = math.hypot(dx, dy)
u = (dx / L, dy / L)
s = (E - a[0]) * u[0] + (N - a[1]) * u[1]
d = (E - a[0]) * u[1] - (N - a[1]) * u[0]
print("face a=(%.2f,%.2f) b=(%.2f,%.2f) len %.2f dir a->b %.0f deg" % (a + b + (L, math.degrees(math.atan2(dx, dy)) % 360)))
print("point along=%.2f m from a, signed offset %.2f m" % (s, d))
for br in sys.argv[5:]:
    r = math.radians(float(br))
    v = (math.sin(r), math.cos(r))
    den = v[0] * u[1] - v[1] * u[0]
    if abs(den) < 1e-9:
        print(br, "parallel")
        continue
    # E + t v = a + s' u
    t = ((a[0] - E) * u[1] - (a[1] - N) * u[0]) / den
    s2 = (E + t * v[0] - a[0]) * u[0] + (N + t * v[1] - a[1]) * u[1]
    print("bearing %s: meets face line at along=%.2f (t=%.1f m)" % (br, s2, t))
