"""ray_hits.py E N BEARING [GEO_E GEO_N] -- geometry aid only (decides nothing): the building footprints a
sight line from camera E,N at BEARING (deg clockwise from north) crosses within 60 m, in order, with the
entry face index, entry distance and along-face metres; and, if given, which footprint contains (or is
nearest to) the address geocode GEO_E,GEO_N, with each face's length and outward bearing."""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
E, N, B = (float(v) for v in sys.argv[1:4])
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_46/canonical.world_ir.json").read_text())
fps = {f["feature_id"]: f["geometry"]["coordinates"] for f in canon["canonical_features"]
       if f.get("semantic_class") == "building_footprint"}
dx, dy = math.sin(math.radians(B)), math.cos(math.radians(B))


def hit(a, b):
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = dx * ey - dy * ex
    if abs(den) < 1e-12:
        return None
    t = ((a[0] - E) * ey - (a[1] - N) * ex) / den
    u = ((a[0] - E) * dy - (a[1] - N) * dx) / den
    return (t, u * math.hypot(ex, ey)) if t > 0 and 0 <= u <= 1 else None


rows = []
for k, ring in fps.items():
    for i, (a, b) in enumerate(zip(ring, ring[1:])):
        h = hit(a, b)
        if h and h[0] < 60:
            rows.append((h[0], k, i, h[1], math.dist(a, b)))
rows.sort()
seen = set()
for t, k, i, s, L in rows:
    if k in seen:
        continue
    seen.add(k)
    print("ray hit %5.1f m  %-48s face %d along %.1f / %.1f m" % (t, k, i, s, L))
if len(sys.argv) > 5:
    g = (float(sys.argv[4]), float(sys.argv[5]))

    def inside(p, r):
        c = False
        for (x1, y1), (x2, y2) in zip(r, r[1:]):
            if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
                c = not c
        return c
    cont = [k for k, r in fps.items() if inside(g, r)]

    def dist(p, r):
        best = 1e9
        for a, b in zip(r, r[1:]):
            ex, ey = b[0] - a[0], b[1] - a[1]; L2 = ex * ex + ey * ey or 1e-12
            t = max(0, min(1, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / L2))
            best = min(best, math.hypot(p[0] - a[0] - t * ex, p[1] - a[1] - t * ey))
        return best
    near = sorted((dist(g, r), k) for k, r in fps.items())[:3]
    print("geocode inside:", cont or "-", "| nearest:", [(k, round(d, 1)) for d, k in near])
    for k in (cont or [near[0][1]]):
        r = fps[k]
        area = sum(x[0] * y[1] - y[0] * x[1] for x, y in zip(r, r[1:]))
        for i, (a, b) in enumerate(zip(r, r[1:])):
            ex, ey = b[0] - a[0], b[1] - a[1]; L = math.hypot(ex, ey)
            nx, ny = (ey / L, -ex / L) if area > 0 else (-ey / L, ex / L)
            print("   %s face %d len %.1f outward %.0f" % (k[-12:], i, L, math.degrees(math.atan2(nx, ny)) % 360))
