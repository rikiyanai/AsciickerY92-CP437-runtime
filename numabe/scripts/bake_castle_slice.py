#!/usr/bin/env python3
"""FL-4826: castle island plateau + cathedral interior blockout.

Generates two face-row JSONs (16 floats/face: p0.xyz, mat, p1.xyz, shade,
p2.xyz, 0, p3.xyz, 0) consumed by authored_landmarks.json placements:

1. fl4826_castle_island_plateau_faces.json — enlarged offshore island. Flat
   top 120x100 at y=32 (fits the 24x16 cathedral hall, towers, ramps and
   bridge landing; the old 69x68 blockout's flat top was 22x18), stepped
   cliff skirt down to y=-10. Replaces castle_massif's mesh pointer; the old
   file stays on disk as history.

2. fl4826_castle_interior_faces.json — open-topped cathedral (FL-4826
   Option A: no roof over the fight hall, so no ceiling ever sits between the
   camera and the interior). Floor local y=0.1 (doorsill above the plateau),
   12u walls, south door gap 2u wide, loft ledges at y=6 on both long walls,
   two corner ramps (26.6 deg), two open-topped 4x4x18 towers with door gaps
   on the hall-facing sides. Placed at base_y 32.0 on the plateau.

Walls/lofts/ramps/towers are emitted as epsilon-separated double quads
(2cm apart): collision is double-sided, and whichever way the product
pipeline culls, one face shows without z-fighting.
"""
from __future__ import annotations

import json
import os

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "godot_project", "assets", "world_mesh",
)

MAT_STONE = 203
MAT_ROCK = 210
MAT_MOSS = 211

faces: list[list[float]] = []
comps: dict[str, int] = {}


def quad(p0, p1, p2, p3, mat, shade, comp):
    faces.append([
        p0[0], p0[1], p0[2], float(mat),
        p1[0], p1[1], p1[2], float(shade),
        p2[0], p2[1], p2[2], 0.0,
        p3[0], p3[1], p3[2], 0.0,
    ])
    comps[comp] = comps.get(comp, 0) + 1


def double(p0, p1, p2, p3, mat, shade, comp, eps=0.01):
    """Two opposite-winding quads separated 2cm along their normal."""
    ax, ay, az = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    bx, by, bz = p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2]
    nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
    ox, oy, oz = nx / ln * eps, ny / ln * eps, nz / ln * eps
    q0 = [(p0[0] + ox, p0[1] + oy, p0[2] + oz), (p1[0] + ox, p1[1] + oy, p1[2] + oz),
          (p2[0] + ox, p2[1] + oy, p2[2] + oz), (p3[0] + ox, p3[1] + oy, p3[2] + oz)]
    q1 = [(p0[0] - ox, p0[1] - oy, p0[2] - oz), (p3[0] - ox, p3[1] - oy, p3[2] - oz),
          (p2[0] - ox, p2[1] - oy, p2[2] - oz), (p1[0] - ox, p1[1] - oy, p1[2] - oz)]
    quad(*q0, mat, shade, comp)
    quad(*q1, mat, shade, comp)


def rect_xz(x0, x1, z0, z1, y, mat, shade, comp):
    quad((x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0), mat, shade, comp)


# --- 1. island plateau: rectangles (hx, hz, y) from top down ----------------
def bake_island():
    global faces, comps
    faces, comps = [], {}
    levels = [(60, 50, 32), (72, 62, 20), (84, 74, 8), (96, 86, -2), (108, 98, -10)]
    # flat top
    hx, hz, y = levels[0]
    rect_xz(-hx, hx, -hz, hz, y, MAT_MOSS, 0.6, "plateau")
    # cliff rings between consecutive levels
    for (ax, az, ay), (bx, bz, by) in zip(levels, levels[1:]):
        mat, shade = (MAT_ROCK, 0.5) if ay > 8 else (MAT_ROCK, 0.4)
        # south (z+), north (z-), east (x+), west (x-) skirts
        quad((-bx, by, bz), (bx, by, bz), (ax, ay, az), (-ax, ay, az), mat, shade, "cliff")
        quad((bx, by, -bz), (-bx, by, -bz), (-ax, ay, -az), (ax, ay, -az), mat, shade, "cliff")
        quad((bx, by, -bz), (bx, by, bz), (ax, ay, az), (ax, ay, -az), mat, shade, "cliff")
        quad((-bx, by, bz), (-bx, by, -bz), (-ax, ay, -az), (-ax, ay, az), mat, shade, "cliff")
    return finish("fl4826_castle_island_plateau_faces.json", "FL-4826",
                  "programmatic stepped plateau for the boss castle (this script)")


# --- 2. cathedral interior (local coords; floor y=0.1, door south) -----------
HALL_X = 12.0
HALL_Z0, HALL_Z1 = -2.0, 14.0
WALL_H = 12.0
LOFT_Y = 6.0


def wall_x(x, z0, z1, y0, y1, mat=MAT_STONE, shade=0.55, comp="wall"):
    double((x, y0, z0), (x, y0, z1), (x, y1, z1), (x, y1, z0), mat, shade, comp)


def wall_z(z, x0, x1, y0, y1, mat=MAT_STONE, shade=0.55, comp="wall"):
    double((x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z), mat, shade, comp)


def bake_interior():
    global faces, comps
    faces, comps = [], {}
    # Floor slab just below local y=0: the acceptance sweep drives a body
    # whose feet are AT y=0, so a doorsill above 0 would block entry itself.
    rect_xz(-HALL_X, HALL_X, HALL_Z0, HALL_Z1, -0.05, MAT_STONE, 0.6, "floor")
    # south wall with 2u door gap at x[-1,1]; north/east/west solid
    wall_z(HALL_Z0, -HALL_X, -1.0, 0.0, WALL_H)
    wall_z(HALL_Z0, 1.0, HALL_X, 0.0, WALL_H)
    wall_z(HALL_Z1, -HALL_X, HALL_X, 0.0, WALL_H)
    wall_x(-HALL_X, HALL_Z0, HALL_Z1, 0.0, WALL_H)
    wall_x(HALL_X, HALL_Z0, HALL_Z1, 0.0, WALL_H)
    # loft ledges, 3 deep, on both long walls at y=6
    for z0, z1 in ((HALL_Z0, HALL_Z0 + 3.0), (HALL_Z1 - 3.0, HALL_Z1)):
        a, b, c, d = (-HALL_X, LOFT_Y, z0), (-HALL_X, LOFT_Y, z1), \
            (HALL_X, LOFT_Y, z1), (HALL_X, LOFT_Y, z0)
        double(a, b, c, d, MAT_STONE, 0.5, "loft")
    # corner ramps, 3 wide, rise 0->6 over run 12 (26.6 deg), opposite corners
    # west ramp climbs northward; east ramp climbs southward
    for x0, x1, z_from, z_to in ((-HALL_X, -HALL_X + 3.0, HALL_Z0, HALL_Z0 + 12.0),
                                 (HALL_X - 3.0, HALL_X, HALL_Z0 + 12.0, HALL_Z0)):
        y0 = -0.05 if z_from == HALL_Z0 else LOFT_Y
        y1 = LOFT_Y if z_from == HALL_Z0 else -0.05
        double((x0, y0, z_from), (x0, y1, z_to), (x1, y1, z_to), (x1, y0, z_from),
               MAT_STONE, 0.5, "ramp")
    # two open-topped 4x4x18 towers at diagonal corners, door gaps hall-side
    # tower A west-north, door on east face; tower B east-south, door west face
    for (tx0, tx1, tz0, tz1, gap) in ((-17.0, -13.0, 10.0, 14.0, ("E", 11.0, 13.0)),
                                      (13.0, 17.0, -6.0, -2.0, ("W", -5.0, -3.0))):
        th = 18.0
        if gap[0] == "E":
            wall_x(tx0, tz0, tz1, 0.0, th, comp="tower")
            wall_z(tz0, tx0, tx1, 0.0, th, comp="tower")
            wall_z(tz1, tx0, tx1, 0.0, th, comp="tower")
            wall_x(tx1, tz0, gap[1], 0.0, th, comp="tower")
            wall_x(tx1, gap[2], tz1, 0.0, th, comp="tower")
        else:
            wall_x(tx1, tz0, tz1, 0.0, th, comp="tower")
            wall_z(tz0, tx0, tx1, 0.0, th, comp="tower")
            wall_z(tz1, tx0, tx1, 0.0, th, comp="tower")
            wall_x(tx0, tz0, gap[1], 0.0, th, comp="tower")
            wall_x(tx0, gap[2], tz1, 0.0, th, comp="tower")
    return finish("fl4826_castle_interior_faces.json", "FL-4826",
                  "programmatic open-topped cathedral interior (this script)")


def finish(name, fl, source):
    xs, ys, zs = [], [], []
    flat: list[float] = []
    for f in faces:
        flat.extend(f)
    for i in range(0, len(flat), 16):
        xs += [flat[i], flat[i + 4], flat[i + 8], flat[i + 12]]
        ys += [flat[i + 1], flat[i + 5], flat[i + 9], flat[i + 13]]
        zs += [flat[i + 2], flat[i + 6], flat[i + 10], flat[i + 14]]
    doc = {
        "version": 1,
        "fl": fl,
        "mode": "blockout",
        "source": source,
        "baker": "scripts/bake_castle_slice.py",
        "scale": 1.0,
        "dimensions_xyz": [round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4),
                           round(max(zs) - min(zs), 4)],
        "face_count": len(faces),
        "component_face_counts": dict(comps),
        "mats": [
            {"name": "stone", "id": 203, "color": [0.5, 0.47, 0.44]},
            {"name": "rock", "id": 210, "color": [0.4, 0.385, 0.365]},
            {"name": "moss", "id": 211, "color": [0.33, 0.44, 0.22]},
        ],
        "rows": [round(v, 4) for v in flat],
    }
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(doc, f)
    print(f"{name}: faces={len(faces)} comps={dict(comps)} dims={doc['dimensions_xyz']}")
    return doc


# --- 3. mountain island from the skull-ridge heightfield ---------------------
# (Superseded by section 4's cone island; kept for history, not referenced
# by the manifest.)

# --- 4. cone island: widen + extrude a sampled conical islet ------------------
# Takes a narrow offshore cone (e.g. the (18,408) islet: 25 tall, ~80 wide),
# widens its circumference, extrudes it taller, and cuts a flat castle shelf.
# CONE_C selects the reference islet; rerun with a new center for another.
CONE_C = (18.0, 408.0)
CONE_R = 45.0
CONE_CELL = 6.0
CONE_XZ = 2.0
CONE_LIFT = 1.8
CONE_TOP_Y = 55.0
CONE_SKW = 0.0
# Castle pad: dead-flat 40x30 under the hall, then a 20m falloff ring that
# blends into the sampled mountainside instead of cutting a cliff.
PAD_X, PAD_Z = 20.0, 15.0
PAD_BLEND = 20.0


def bake_cone_island(sample):
    """Sampled cone, widened xCONE_XZ, lifted, flat shelf, skirt to water."""
    global faces, comps
    faces, comps = [], {}
    n = int(CONE_R * 2 / CONE_CELL)
    H = [[None] * (n + 1) for _ in range(n + 1)]
    for ix in range(n + 1):
        for iz in range(n + 1):
            lx = -CONE_R + ix * CONE_CELL
            lz = -CONE_R + iz * CONE_CELL
            h = sample(CONE_C[0] + lx, CONE_C[1] + lz)
            if h is None:
                h = 0.0
            y = h * CONE_LIFT
            # Castle pad: dead-flat 40x30 under the hall, then a falloff ring
            # that blends into the sampled slope like a summit dome.
            if abs(lx) <= PAD_X and abs(lz) <= PAD_Z:
                y = CONE_TOP_Y
            else:
                dx = max(abs(lx) - PAD_X, 0.0)
                dz = max(abs(lz) - PAD_Z, 0.0)
                edge = (dx * dx + dz * dz) ** 0.5
                if edge < PAD_BLEND:
                    t = edge / PAD_BLEND
                    y = CONE_TOP_Y * (1.0 - t) + min(y, CONE_TOP_Y) * t
            H[ix][iz] = (lx * CONE_XZ, y, lz * CONE_XZ)
    for ix in range(n):
        for iz in range(n):
            a, b = H[ix][iz], H[ix][iz + 1]
            c, d = H[ix + 1][iz + 1], H[ix + 1][iz]
            lowest = min(a[1], b[1], c[1], d[1])
            mat = MAT_MOSS if lowest > 30.0 else MAT_ROCK
            quad(a, b, c, d, mat, 0.55, "cone")
    # skirt rim to below the waterline
    for ix in range(n + 1):
        for (ex, ez), (ix2, iz2) in [((ix, 0), (ix, 1)), ((ix, n), (ix, n - 1))]:
            e, inner = H[ex][ez], H[ix2][iz2]
            quad(e, (e[0], SKIRT_Y, e[2]), (inner[0], SKIRT_Y, inner[2]), inner,
                 MAT_ROCK, 0.4, "skirt")
    for iz in range(1, n):
        for (ex, ez), (ix2, iz2) in [((0, iz), (1, iz)), ((n, iz), (n - 1, iz))]:
            e, inner = H[ex][ez], H[ix2][iz2]
            quad(e, (e[0], SKIRT_Y, e[2]), (inner[0], SKIRT_Y, inner[2]), inner,
                 MAT_ROCK, 0.4, "skirt")
    return finish("fl4826_castle_cone_island_faces.json", "FL-4826",
                  "sampled conical islet widened/extruded (this script)")
# Samples the real ridge around RIDGE_C into a grid, widens diameter x1.5,
# lifts heights x1.25, and flattens a 60x50 castle shelf at TOP_Y. Skirt ring
# drops the rim to SKIRT_Y so the mass meets open water like terrain.
RIDGE_C = (-112.0, 330.0)
SAMPLE_R = 55.0
CELL = 8.0
XZ_SCALE = 1.5
LIFT = 1.25
TOP_Y = 50.0
SHELF_X, SHELF_Z = 30.0, 25.0
SKIRT_Y = -10.0


def bake_ridge_island(sample):
    """sample(x, z) -> height or None. Emits grid top + outer skirt."""
    global faces, comps
    faces, comps = [], {}
    n = int(SAMPLE_R * 2 / CELL)
    H = [[None] * (n + 1) for _ in range(n + 1)]
    for ix in range(n + 1):
        for iz in range(n + 1):
            lx = -SAMPLE_R + ix * CELL
            lz = -SAMPLE_R + iz * CELL
            h = sample(RIDGE_C[0] + lx, RIDGE_C[1] + lz)
            if h is None:
                continue
            y = 20.0 + (h - 20.0) * LIFT
            # Cut-and-fill castle shelf: the whole 60x50 footprint sits at
            # TOP_Y regardless of the sampled peak, so the hall footprint is
            # guaranteed level. Edges fall off as cliffs.
            if abs(lx) <= SHELF_X and abs(lz) <= SHELF_Z:
                y = TOP_Y
            H[ix][iz] = (lx * XZ_SCALE, y, lz * XZ_SCALE)
    # Fill water gaps from neighbours so the mass is watertight; the rim
    # skirt then meets a closed edge all round. Ten passes plus a global
    # mean fallback: any remaining hole is a body-sized fall-through.
    for _ in range(10):
        for ix in range(n + 1):
            for iz in range(n + 1):
                if H[ix][iz] is not None:
                    continue
                near = [H[ax][az] for ax in (ix - 1, ix + 1) for az in (iz - 1, iz + 1)
                        if 0 <= ax <= n and 0 <= az <= n and H[ax][az] is not None]
                if near:
                    lx = -SAMPLE_R + ix * CELL
                    lz = -SAMPLE_R + iz * CELL
                    H[ix][iz] = (lx * XZ_SCALE, sum(v[1] for v in near) / len(near), lz * XZ_SCALE)
    known = [v[1] for row in H for v in row if v is not None]
    mean_y = sum(known) / len(known)
    for ix in range(n + 1):
        for iz in range(n + 1):
            if H[ix][iz] is None:
                lx = -SAMPLE_R + ix * CELL
                lz = -SAMPLE_R + iz * CELL
                H[ix][iz] = (lx * XZ_SCALE, mean_y, lz * XZ_SCALE)
    for ix in range(n):
        for iz in range(n):
            a, b = H[ix][iz], H[ix][iz + 1]
            c, d = H[ix + 1][iz + 1], H[ix + 1][iz]
            if a is None or b is None or c is None or d is None:
                continue
            mat = MAT_MOSS if a[1] > 40.0 else MAT_ROCK
            quad(a, b, c, d, mat, 0.55, "slope")
    # skirt: rim vertices straight down to SKIRT_Y
    rim = []
    for ix in range(n + 1):
        rim.append(((ix, 0), (ix, 1)))
        rim.append(((ix, n), (ix, n - 1)))
    for iz in range(1, n):
        rim.append(((0, iz), (1, iz)))
        rim.append(((n, iz), (n - 1, iz)))
    for (ex, ez), (ix, iz) in rim:
        e, inner = H[ex][ez], H[ix][iz]
        if e is None or inner is None:
            continue
        skirt_e = (e[0], SKIRT_Y, e[2])
        quad(e, skirt_e, (inner[0], SKIRT_Y, inner[2]), inner,
             MAT_ROCK, 0.4, "skirt")
    return finish("fl4826_castle_ridge_island_faces.json", "FL-4826",
                  "skull-ridge heightfield widened x1.5 lifted x1.25 (this script)")
    xs, ys, zs = [], [], []
    flat: list[float] = []
    for f in faces:
        flat.extend(f)
    for i in range(0, len(flat), 16):
        xs += [flat[i], flat[i + 4], flat[i + 8], flat[i + 12]]
        ys += [flat[i + 1], flat[i + 5], flat[i + 9], flat[i + 13]]
        zs += [flat[i + 2], flat[i + 6], flat[i + 10], flat[i + 14]]
    doc = {
        "version": 1,
        "fl": fl,
        "mode": "blockout",
        "source": source,
        "baker": "scripts/bake_castle_slice.py",
        "scale": 1.0,
        "dimensions_xyz": [round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4),
                           round(max(zs) - min(zs), 4)],
        "face_count": len(faces),
        "component_face_counts": dict(comps),
        "mats": [
            {"name": "stone", "id": 203, "color": [0.5, 0.47, 0.44]},
            {"name": "rock", "id": 210, "color": [0.4, 0.385, 0.365]},
            {"name": "moss", "id": 211, "color": [0.33, 0.44, 0.22]},
        ],
        "rows": [round(v, 4) for v in flat],
    }
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(doc, f)
    print(f"{name}: faces={len(faces)} comps={dict(comps)} dims={doc['dimensions_xyz']}")
    return doc


if __name__ == "__main__":
    import sys
    bake_island()
    bake_interior()
    # Ridge island needs live heightfield samples (dumped by the sim):
    # node --import tsx -e "<loader>" > /tmp/ridge_samples.json
    sample_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ridge_samples.json"
    if os.path.exists(sample_path):
        with open(sample_path) as f:
            dump = json.load(f)
        # Ridge dump and cone dump share the grid schema; the active center
        # and radius select which one this file holds.
        cx, cz, radius = dump["cx"], dump["cz"], dump["r"]
        cols = int(radius * 2 / dump["cell"]) + 1

        def sample(x, z, _dump=dump, _cols=cols):
            ix = int(round((x - (cx - radius)) / dump["cell"]))
            iz = int(round((z - (cz - radius)) / dump["cell"]))
            if 0 <= ix < _cols and 0 <= iz < _cols:
                return _dump["samples"][iz * _cols + ix]
            return None

        bake_ridge_island(sample)
    else:
        print(f"skip ridge island (no {sample_path})")
    cone_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cone_samples.json"
    if os.path.exists(cone_path):
        with open(cone_path) as f:
            cdump = json.load(f)
        cx2, cz2, radius2 = cdump["cx"], cdump["cz"], cdump["r"]
        cols2 = int(radius2 * 2 / cdump["cell"]) + 1

        def csample(x, z, _dump=cdump, _cols=cols2):
            ix = int(round((x - (cx2 - radius2)) / _dump["cell"]))
            iz = int(round((z - (cz2 - radius2)) / _dump["cell"]))
            if 0 <= ix < _cols and 0 <= iz < _cols:
                return _dump["samples"][iz * _cols + ix]
            return None

        bake_cone_island(csample)
    else:
        print(f"skip cone island (no {cone_path})")
