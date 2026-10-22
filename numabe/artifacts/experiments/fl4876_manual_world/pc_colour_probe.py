"""pc_colour_probe.py [BUILDING_TAIL ...] -- measuring aid only (decides nothing): reads the TMG airborne
point cloud tiles (LAS 1.2, point format 3 = RGB, JGD2011 plane IX / EPSG:6677) and reports whether RGB
is populated, then for each given building footprint (id tail) the colour statistics of points inside a
0.6 m band just OUTSIDE..INSIDE its outline and between 1.0 m and (roof - 1.0 m) above local ground --
i.e. wall-face returns, not roof returns -- with their count, so a person can judge whether airborne
points carry a usable wall colour."""
import json, sys
from pathlib import Path
import numpy as np
import laspy
from pyproj import Transformer
from shapely.geometry import Polygon, Point
from shapely.prepared import prep
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LAS = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/tmg_pointcloud/las"
T = Transformer.from_crs("EPSG:32654", "EPSG:6677", always_xy=True)
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_46/canonical.world_ir.json").read_text())
fps = {f["feature_id"]: f["geometry"]["coordinates"] for f in canon["canonical_features"]
       if f.get("semantic_class") == "building_footprint"}
tiles = []
for p in sorted(LAS.glob("*.las")):
    with laspy.open(p) as r:
        h = r.header
        tiles.append((p, h.mins, h.maxs))
first = laspy.read(tiles[0][0])
rgb = np.vstack([first.red, first.green, first.blue]).T
print("tile", tiles[0][0].name, "points", len(rgb), "rgb max", rgb.max(axis=0), "nonzero frac", float((rgb.sum(1) > 0).mean()))
for tail in sys.argv[1:]:
    fid = next(k for k in fps if k.endswith(tail))
    ring = [T.transform(q[0], q[1]) for q in fps[fid]]
    poly = Polygon(ring)
    band = prep(poly.buffer(0.6).difference(poly.buffer(-0.6)))
    minx, miny, maxx, maxy = poly.bounds
    pts = []
    for p, mn, mx in tiles:
        if mx[0] < minx - 1 or mn[0] > maxx + 1 or mx[1] < miny - 1 or mn[1] > maxy + 1:
            continue
        las = laspy.read(p)
        x, y, z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        m = (x > minx - 1) & (x < maxx + 1) & (y > miny - 1) & (y < maxy + 1)
        for xi, yi, zi, r_, g_, b_ in zip(x[m], y[m], z[m], np.asarray(las.red)[m], np.asarray(las.green)[m], np.asarray(las.blue)[m]):
            pts.append((xi, yi, zi, r_, g_, b_))
    if not pts:
        print(tail, "no points"); continue
    a = np.array(pts, dtype=float)
    ground = np.percentile(a[:, 2], 3); roof = np.percentile(a[:, 2], 95)
    keep = [i for i, q in enumerate(a) if ground + 1.0 < q[2] < roof - 1.0 and band.contains(Point(q[0], q[1]))]
    w = a[keep]
    scale = 256.0 if a[:, 3:].max() > 255 else 1.0
    print("%s pts %d ground %.1f roof %.1f wall-band pts %d" % (tail, len(a), ground, roof, len(w)),
          "wall rgb median" , (np.median(w[:, 3:], axis=0) / scale).round().tolist() if len(w) else None,
          "roof rgb median", (np.median(a[a[:, 2] > roof - 0.5][:, 3:], axis=0) / scale).round().tolist())
