#!/usr/bin/env python3
"""author_temple_graveyard.py [--build BUILD_DIR] [--write] -- transcription of the main session's hand authoring
(2026-10-16) of the 有慶山東光院 graveyard as terraced grave-plot fixtures (FL-4876 GRAVEYARD-01).

Operator 2026-10-16: the temple is "bullshit"; the references show a large terraced graveyard of rows of grey stone
markers NW and N of the main hall on stepped stone terraces with retaining walls (operator_ref_a top-left, operator_ref_b
top-left).

WHERE IT IS.  OSM way/225102214 (amenity=grave_yard, source Bing 2007-04, 16 nodes) lies inside the map (world x 149..518,
y 998..1282; terrain bounds 0..2224 x 0..3312), 3 to 65 m NW and N of the main hall way/225101288 (hall centroid
E379783.0 N3938600.3; polygon centroid E379764.0 N3938625.5; extent 33 m along the rows, 72 m across).  The other cemetery
polygon in the bundle, way/263039360 (landuse=cemetery, operator 密蔵院), is a different temple's graveyard ~230 m SE.

WHAT IS PLACED.  One fixture per terrace block, module stone_marker_bollard (the catalogue's nearest stone marker; it has
no cemetery module), custom variant grave_terrace_row_block (population_asset_geometry.py: a 9.6 x 7.2 m stone-faced
terrace with a 0.9 m retaining wall on its low side and 4 rows of 6 kerbed grave plots with three-tier gravestones).

WHY THE FIXTURES CARRY emission = authored_scene_mesh.  The population planner attaches every point fixture to a road,
rail or waterway carrier within 12 m (rules attachment_tolerances.carrier_association_m) and re-derives its yaw from that
carrier's nearest edge.  A graveyard interior is 16-21 m or more from any carrier: build_reg_87 + these fixtures, run
through resolve_identity_entities + compile_population_plan, left 11 of 15 blocks as carrier_beyond_tolerance debt and
turned the other four 90 degrees onto a road's bearing.  These fixtures therefore stay in authored_scene_fixtures.json
(the owner) but are emitted by the building bake (bake_osm_building_footprints.py, AUTHORED-SCENE-MESH-01) at their E/N
and yaw on the terrain; the identity resolver skips them (resolve_identity_entities.load_adjudication).

yaw_deg is the math angle: local +x = (cos yaw, sin yaw) in (E, N); local +y = (-sin yaw, cos yaw).

ROW DIRECTION AND SPACING (R: operator_ref_b crops 100..400 x 150..450, upscaled 1.3x).  The pale paths between plots
run from lower-left to upper-right, bearing ~40 deg (the same direction as the hall's long edges, 37.5-39.4 deg), so the
rows run along compass bearing 39 (math angle 51; local +y then points NW, the down-slope side).  Plots read ~1.6 m wide
with rows ~1.7 m apart; a path every ~4.8 m.
TERRACES.  The DEM under the graveyard (dem_terrain.a3d sampled every 5 m) climbs from ~1.2 m at the NW edge to 3.5 m
beside the hall in flat treads (values repeat at 2.25 and 3.52 m) with ~0.3 m risers.  Each block is one tread: a 7.2 m
deep fill with a 0.9 m stone-faced retaining wall on its NW (low) side; the next block SE of it stands on the next higher
ground (the bake grounds each block on the terrain at its own point).
"""
import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.pipeline import geo_frames as GF  # noqa: E402

BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
FX = BUNDLE / "authored_scene_fixtures.json"
OSM = BUNDLE / "numabe_connected_neighborhood_aoi.osm"
WAY = "225102214"
THETA = 39.0                       # R: path / row bearing (compass), also the hall's long-edge bearing (M)
YAW_MATH = 90.0 - THETA            # local +x along the rows: (cos 51, sin 51) = (sin 39, cos 39)
BLOCK_U, BLOCK_V = 9.6, 7.2        # D: 6 plots x 1.6 m along the rows; 4 rows x 1.7 m + 0.4 m wall
CLEAR_M = 1.4                      # D: keep a block this far off every building
EDGE_M = 0.5                       # D: block corners stay this far inside the polygon
BY = "main session (temple graveyard hand authoring from operator references, 2026-10-16)"
MODULE, VARIANT = "stone_marker_bollard", "grave_terrace_row_block"


def load_osm():
    nodes, ways = {}, {}
    for _ev, el in ET.iterparse(OSM):
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("lon")), float(el.get("lat")))
        elif el.tag == "way":
            ways[el.get("id")] = [n.get("ref") for n in el.findall("nd")]
    return nodes, ways


def inside(poly, x, y):
    c = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            c = not c
    return c


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def dist_to_ring(poly, p):
    return min(seg_dist(p, a, b) for a, b in zip(poly, poly[1:] + poly[:1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=str(ROOT / ".run/fl4876_builds/build_reg_87"),
                    help="a build dir: its canonical footprints (building_instances.json) are the buildings to avoid")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    build = Path(args.build)
    nodes, ways = load_osm()
    gy = [GF.utm_forward(*nodes[r]) for r in ways[WAY]]
    gy = gy[:-1] if gy[0] == gy[-1] else gy
    cx = sum(p[0] for p in gy) / len(gy)
    cy = sum(p[1] for p in gy) / len(gy)
    ub = (math.sin(math.radians(THETA)), math.cos(math.radians(THETA)))
    vb = (math.sin(math.radians(THETA + 90)), math.cos(math.radians(THETA + 90)))

    def en(u, v):
        return (cx + u * ub[0] + v * vb[0], cy + u * ub[1] + v * vb[1])

    meta = json.loads((build / "terrain_metadata.json").read_text())
    sc = meta["content_scale"]
    buildings = [[((x - meta["offset_x"]) / sc + meta["origin_easting"],
                   (y - meta["offset_y"]) / sc + meta["origin_northing"]) for x, y in s["footprint"]]
                 for s in json.loads((build / "building_instances.json").read_text())]
    gu = [((p[0] - cx) * ub[0] + (p[1] - cy) * ub[1], (p[0] - cx) * vb[0] + (p[1] - cy) * vb[1]) for p in gy]
    u_lo, u_hi = min(p[0] for p in gu), max(p[0] for p in gu)
    v_lo, v_hi = min(p[1] for p in gu), max(p[1] for p in gu)

    def block_ok(u, v):
        corners = [en(u + du, v + dv) for du in (-BLOCK_U / 2, BLOCK_U / 2) for dv in (-BLOCK_V / 2, BLOCK_V / 2)]
        probes = [en(u + du, v + dv) for du in (-BLOCK_U / 2, 0, BLOCK_U / 2) for dv in (-BLOCK_V / 2, 0, BLOCK_V / 2)]
        if not all(inside(gy, *p) for p in probes) or any(dist_to_ring(gy, p) < EDGE_M for p in corners):
            return False
        for b in buildings:
            if any(inside(b, *p) for p in probes) or any(inside([en(u + du, v + dv) for du, dv in
                                                                 ((-BLOCK_U / 2, -BLOCK_V / 2), (BLOCK_U / 2, -BLOCK_V / 2),
                                                                  (BLOCK_U / 2, BLOCK_V / 2), (-BLOCK_U / 2, BLOCK_V / 2))],
                                                                *q) for q in b):
                return False
            if any(dist_to_ring(b, p) < CLEAR_M for p in probes):
                return False
        return True

    best = []
    for k in range(12):
        for m in range(12):
            chosen = []
            u = u_lo + k * BLOCK_U / 12
            while u < u_hi:
                v = v_lo + m * BLOCK_V / 12
                while v < v_hi:
                    if block_ok(u + BLOCK_U / 2, v + BLOCK_V / 2):
                        chosen.append((u + BLOCK_U / 2, v + BLOCK_V / 2))
                    v += BLOCK_V
                u += BLOCK_U
            if len(chosen) > len(best):
                best = chosen
    print("polygon centroid", round(cx, 2), round(cy, 2), "u", round(u_lo, 1), round(u_hi, 1), "v", round(v_lo, 1),
          round(v_hi, 1), "blocks", len(best))

    recs = []
    for i, (u, v) in enumerate(sorted(best, key=lambda t: (t[1], t[0]))):
        E, N = en(u, v)
        recs.append({
            "fixture_id": "SF-4876-G%03d" % (i + 1), "module_id": MODULE, "variant": VARIANT,
            "emission": "authored_scene_mesh",
            "E": round(E, 2), "N": round(N, 2), "carrier_id": None, "yaw_deg": round(YAW_MATH, 1),
            "position_uncertainty_m": 3.0,
            "sightings": [{"capture_id": "operator_ref_b_20261016", "what": "terraced graveyard of rows of grey grave "
                           "markers NW and N of the main hall (operator satellite view)",
                           "beside": "OSM way/225102214 amenity=grave_yard"}],
            "manual_review": {
                "status": "accepted", "reviewed_by": BY, "reviewed_on": "2026-10-16",
                "module_basis": "no cemetery module exists; the nearest stone marker takes the custom variant "
                                "grave_terrace_row_block (a 9.6 x 7.2 m stone-faced terrace with a 0.9 m retaining wall "
                                "and 4 rows x 6 kerbed three-tier gravestones), as parked_bicycles takes cluster variants",
                "position_basis": "block %d of a 9.6 x 7.2 m lattice laid on the NE-SW row bearing %.0f over OSM "
                                  "way/225102214 (amenity=grave_yard): every corner lies %.1f m or more inside the polygon "
                                  "and every block %.1f m off every canonical building footprint"
                                  % (i + 1, THETA, EDGE_M, CLEAR_M),
                "carrier_basis": "none: the graveyard interior is beyond the 12 m carrier association tolerance of every "
                                 "road, rail and waterway, so the block is emitted by the building bake on the terrain "
                                 "at its own point (emission authored_scene_mesh)",
                "orientation_basis": "rows run along bearing %.0f (math angle %.0f); the retaining wall stands on the "
                                     "local +y (NW, down-slope) side" % (THETA, YAW_MATH)}})
    print("fixtures", len(recs))
    for r in recs:
        print("  ", r["fixture_id"], r["E"], r["N"])
    if args.write:
        fx = json.loads(FX.read_text())
        fx["fixtures"] = [f for f in fx["fixtures"] if not str(f["fixture_id"]).startswith("SF-4876-G")] + recs
        FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
        print("written")


if __name__ == "__main__":
    main()
