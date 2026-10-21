#!/usr/bin/env python3
"""author_station_building.py [--write] [--preview OUT.png --bearing B] [--map MAP.a3d] [--edits-dir DIR]  (FL-4876 MAP-EDIT-01, 2026-10-17)

Station-specific details of the Numabe station building way/1507398030 that author_building_mesh.py cannot express: the inside of
the open entrance bay on the track-side long face (face 3, s 1.4-6.2 m, 2.2 m deep, 2.4 m high; the bay itself is cut by the main
spec landmarks/specs/1507398030.json): floor, dark soffit and lintel beam, four concrete ticket-gate posts with blue gate flaps,
the yellow-screen ticket machine, red fare poster and white timetable on the tan back wall, and the round dark hanging sign
(photo mapillary_197621542179728: dark disc hanging in the opening; authored_building_appearance.json: concrete ticket-gate row,
yellow-screen ticket machine, red fare posters inside the concourse).

Same frame as the main mesh (origin = footprint vertex mean, z = 0 at the footprint ground, metres * 5.132, face 3 frame: s along
the face from the NW corner, o outward, z up); the main spec's face 3 is the wall these sit behind. Everything is presentation
(alpha 255, no collision) so walking through the bay is not blocked by decoration. --write puts
meshes/station_building_details_1507398030_20261017.akm (never overwrites different bytes) and writes the a3d_edit.py add record
station_building_details_way_1507398030_20261017 to --edits-dir. No other file is touched.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import author_building_mesh as A  # noqa: E402
from author_temple_meshes import akm_text  # noqa: E402

WAY = "1507398030"
FACE = 3
MESH = "station_building_details_1507398030_20261017.akm"
INST = "station_building_details_way_1507398030_20261017"
BY = "sonnet landmarks 20261017"
BAY = (1.4, 6.2)          # main spec face 3 opening s range
BAY_DEPTH = 2.2
BAY_TOP = 2.4

FLOOR = (138, 140, 142)
SOFFIT = (60, 62, 66)
BEAM = (74, 78, 82)
POST = (190, 190, 186)
FLAP = (58, 108, 168)
MACHINE = (176, 178, 180)
SCREEN = (232, 204, 64)
POSTER_RED = (196, 66, 58)
POSTER_WHITE = (234, 234, 228)
DISC = (36, 38, 42)
ROD = (80, 82, 86)


def disc(face, sc, oc, zc, r, rgb, n=12):
    """Vertical disc in the face's s-z plane at outward offset oc, drawn both sides."""
    ring = [(sc + r * math.cos(2 * math.pi * k / n), oc, zc + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    for k in range(n):
        tri = [(sc, oc, zc), ring[k], ring[(k + 1) % n]]
        face.quad(tri, rgb, 255, (0, 1, 0))
        face.quad([tri[0], tri[2], tri[1]], rgb, 255, (0, -1, 0))


def build():
    global face
    row = A.load_footprint({"way": WAY})
    meta = A.projection()
    cs = float(meta["content_scale"])
    pe = [((x - meta["offset_x"]) / cs, (y - meta["offset_y"]) / cs) for x, y in row["bake_footprint"]]
    cx = sum(p[0] for p in pe) / len(pe)
    cy = sum(p[1] for p in pe) / len(pe)
    poly = [(p[0] - cx, p[1] - cy) for p in pe]
    asg = A.signed_area(poly)
    bld = A.B()
    faces = [A.Face(bld, i, poly[i], poly[(i + 1) % len(poly)], asg) for i in range(len(poly))]
    face = faces[FACE]
    s0, s1 = BAY
    d = BAY_DEPTH - 0.06
    # floor and soffit and lintel beam
    face.quad([(s0, 0, 0.03), (s1, 0, 0.03), (s1, -d, 0.03), (s0, -d, 0.03)], FLOOR, 255, (0, 0, 1))
    face.quad([(s0, 0, BAY_TOP - 0.02), (s0, -d, BAY_TOP - 0.02), (s1, -d, BAY_TOP - 0.02), (s1, 0, BAY_TOP - 0.02)], SOFFIT, 255, (0, 0, -1))
    face.box(s0, s1, -0.35, -0.05, BAY_TOP - 0.3, BAY_TOP - 0.02, BEAM, 255, skip=("+z",))
    # ticket-gate row: four concrete posts across the bay with blue flaps between them
    gate_o0, gate_o1 = -1.55, -0.95
    posts = [2.3, 3.2, 4.1, 5.0]
    for sp in posts:
        face.box(sp - 0.13, sp + 0.13, gate_o0, gate_o1, 0.03, 1.0, POST, 255)
    for a, b in zip(posts[:-1], posts[1:]):
        face.box(a + 0.13, b - 0.13, -1.3, -1.2, 0.78, 0.86, FLAP, 255)
    # ticket machine against the back wall at the east end of the bay, yellow screen on its front
    face.box(5.45, 5.95, -d + 0.02, -d + 0.52, 0.03, 1.55, MACHINE, 255)
    face.quad([(5.52, -d + 0.54, 1.12), (5.88, -d + 0.54, 1.12), (5.88, -d + 0.54, 1.42), (5.52, -d + 0.54, 1.42)], SCREEN, 255, (0, 1, 0))
    # red fare poster and white timetable on the back wall
    face.quad([(1.75, -d + 0.01, 1.05), (2.65, -d + 0.01, 1.05), (2.65, -d + 0.01, 2.0), (1.75, -d + 0.01, 2.0)], POSTER_RED, 255, (0, 1, 0))
    face.quad([(3.1, -d + 0.01, 1.2), (4.5, -d + 0.01, 1.2), (4.5, -d + 0.01, 1.95), (3.1, -d + 0.01, 1.95)], POSTER_WHITE, 255, (0, 1, 0))
    # round dark hanging sign (disc about 0.9 m across) from the soffit near the NW end of the bay
    face.box(2.77, 2.80, -0.62, -0.58, 2.15, BAY_TOP - 0.02, ROD, 255)
    disc(face, 2.78, -0.6, 1.72, 0.46, DISC)
    m = bld.scaled()
    return m, {"origin_E": meta["origin_easting"] + cx, "origin_N": meta["origin_northing"] + cy, "row": row,
               "vertices": len(m.v), "faces_n": len(m.f)}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--preview")
    ap.add_argument("--bearing", type=float, default=232.0)
    ap.add_argument("--map", default=str(A.BUILD / "output.a3d"))
    ap.add_argument("--edits-dir", default=str(HERE / "assess/live_fix_20261017/landmarks/edits"))
    a = ap.parse_args(argv)
    mesh, info = build()
    print(f"station details: {info['vertices']} vertices, {info['faces_n']} faces")
    if a.preview:
        A.preview(mesh, a.preview, a.bearing, 12.0)
        print("preview ->", a.preview)
    if not a.write:
        return 0
    path = A.BUILD / "meshes" / MESH
    text = akm_text(mesh, "FL-4876 station building details way/1507398030 (author_station_building.py): bay interior, gates, "
                          "ticket machine, hanging sign; world units, origin = footprint vertex mean, presentation only")
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise SystemExit(f"{path} exists with other content; never overwrite a mesh")
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    med, lo, hi = A.ground_from_map(a.map, [tuple(p) for p in info["row"]["bake_footprint"]])
    rec = [{"inst": INST, "op": "add", "mesh": MESH, "E": round(info["origin_E"], 4), "N": round(info["origin_N"], 4),
            "z_world": round(med, 2), "bearing_deg": 0.0,
            "basis": "bay interior of the hand-built station building way/1507398030: photo mapillary_197621542179728 (open bay, round dark "
                     "hanging sign) and authored_building_appearance.json (concrete ticket-gate row, yellow-screen ticket machine, red fare posters)",
            "by": BY}]
    out = Path(a.edits_dir) / "1507398030_details_add.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    print("mesh ->", path, "\nedits ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
