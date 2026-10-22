#!/usr/bin/env python3
# =============================================================
# bake_akm_to_static_world_faces.py
# =============================================================
# WHAT THIS DOES
# --------------
# The ASCII world you actually see in-game is NOT drawn from Godot scene
# meshes. Instead, World.gd hands the ASCII compositor a flat list of
# "faces" (a big array of numbers) that the GPU turns into the ASCII
# picture. Big static things — the lighthouse tower, the trees, the rocks —
# all live in that face list.
#
# The lighthouse is loaded from a pre-baked JSON file of faces
# (assets/world_mesh/lighthouse_tower_faces.json). This script produces the
# SAME kind of JSON, but from one of your downloaded models (converted to
# the .akm mesh format). The result is a rock (or any prop) that World.gd
# can drop into the ASCII world at a chosen spot — e.g. at the "/note
# 'mossy rocks here'" locations you saved in-game.
#
# THE FACE FORMAT (must match World.gd exactly)
# ---------------------------------------------
# Each face is 16 numbers = four corners, four numbers per corner:
#   corner0:  x, y, z, MATERIAL_ID   <- the shader colours the face by this id
#   corner1:  x, y, z, SHADE         <- a brightness multiplier (0..1)
#   corner2:  x, y, z, 0
#   corner3:  x, y, z, 0
# A triangle is stored as a "flat" quad: corner3 is a copy of corner2.
# World.gd's loader adds the placement anchor (x,y,z) to every corner, so we
# bake the model centred on the origin with its BASE at y=0 — then anchoring
# it at a terrain point makes it sit neatly on the ground.
#
# MATERIAL IDS
# ------------
# The asciicker rock shader knows two ids: 210 = bare rock (grey) and
# 211 = moss (olive). To mimic "moss grows on top", we tag a face as moss
# when its surface points upward (its averaged normal's Y is clearly
# positive) and rock otherwise. This reproduces the look of the hand-authored
# procedural boulders while using the real downloaded geometry.
#
# USAGE
# -----
#   python3 scripts/bake_akm_to_static_world_faces.py \
#       assets/meshes/incoming/mossy_rock.akm \
#       --output godot_project/assets/world_mesh/mossy_rock_faces.json \
#       --target-size 2.4
# =============================================================

import argparse   # command-line flags
import json       # writes the faces JSON
import math       # sin/cos/radians for the orientation fix
import os         # path checks
import sys        # exit codes / stderr


def parse_akm_ply(path):
    """
    Read an ASCII-PLY .akm file and return its vertices and triangles.

    Returns
    -------
    (verts, faces)
      verts : list of dicts {pos:(x,y,z), nrm:(nx,ny,nz), col:(r,g,b)}
      faces : list of (i0, i1, i2) vertex-index triangles

    Why parse by hand: the file is a tiny, well-defined ASCII PLY, so a
    dependency-free reader keeps this script runnable anywhere Python is.

    ABOUT `col` (FL-4789): this baker's own modes colour faces by material id,
    not by baked vertex colour, so nothing in this file reads `col`. It is
    returned because the authored asciicker tree meshes DO carry a meaningful
    per-vertex red/blue/leaf-channel triple, and
    scripts/compile_fl4789_chunk_catalog.py needs that authored signal to pick
    the retained trunk/canopy material for each triangle. Meshes with no colour
    columns report opaque white, which no rule can mistake for foliage.
    """
    with open(path, "r") as fh:
        lines = fh.read().splitlines()

    # --- Read the header: how many vertices/faces, and which columns exist ---
    vcount = 0
    fcount = 0
    props = []          # ordered list of per-vertex property names
    prop_types = []     # matching ordered list of per-vertex property types
    header_end = 0      # line index just past "end_header"
    in_vertex_block = False
    for idx, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element" and parts[1] == "vertex":
            vcount = int(parts[2])
            in_vertex_block = True
        elif parts[0] == "element" and parts[1] == "face":
            fcount = int(parts[2])
            in_vertex_block = False
        elif parts[0] == "property" and in_vertex_block:
            # e.g. "property float x" -> type is parts[1], name is the last token
            props.append(parts[-1])
            prop_types.append(parts[1])
        elif parts[0] == "end_header":
            header_end = idx + 1
            break

    # Column positions for the fields we care about (some may be absent).
    def col(name):
        return props.index(name) if name in props else None
    ix, iy, iz = col("x"), col("y"), col("z")
    inx, iny, inz = col("nx"), col("ny"), col("nz")

    # FL-4789: locate the per-vertex colour channels STRUCTURALLY, as the first
    # three 8-bit properties in the vertex block, instead of by property name.
    # PLY files in this repo were exported by several tools that spell the three
    # channel names differently (long channel words vs single letters) but all of
    # them agree on the ordering and on the 8-bit type, and the position/normal
    # columns are floats. An optional 4th 8-bit alpha column follows and is
    # ignored. If fewer than three such columns exist, the mesh has no authored
    # colour and every vertex reports opaque white below.
    byte_cols = [
        index for index, kind in enumerate(prop_types)
        if kind in ("uchar", "uint8", "char", "int8")
    ]
    colour_cols = byte_cols[:3] if len(byte_cols) >= 3 else None

    # --- Read the vertex rows ---
    verts = []
    cursor = header_end
    for _ in range(vcount):
        toks = lines[cursor].split()
        cursor += 1
        pos = (float(toks[ix]), float(toks[iy]), float(toks[iz]))
        nrm = (float(toks[inx]), float(toks[iny]), float(toks[inz])) if inx is not None else (0.0, 1.0, 0.0)
        # 0..255 authored channel triple, or neutral white when absent so a
        # downstream classifier can never read a missing colour as foliage.
        if colour_cols is None:
            rgb = (255, 255, 255)
        else:
            rgb = tuple(int(toks[index]) for index in colour_cols)
        alpha = int(toks[byte_cols[3]]) if len(byte_cols) >= 4 else 255
        verts.append({"pos": pos, "nrm": nrm, "col": rgb, "alpha": alpha})

    # --- Read the face rows (each: "<count> i0 i1 i2 ...") ---
    faces = []
    for _ in range(fcount):
        toks = lines[cursor].split()
        cursor += 1
        n = abs(int(toks[0]))                 # negative count is native freestyle, not an absent face
        idxs = [int(t) for t in toks[1:1 + n]]
        # Fan-triangulate anything with more than 3 sides, just in case.
        for k in range(1, n - 1):
            faces.append((idxs[0], idxs[k], idxs[k + 1]))
    return verts, faces


# Material ids the asciicker static-world shader understands. Rocks use the
# rock/moss pair; trees use the leaf/bark pair. Keeping them here means the
# baker and World.gd agree on the exact numbers.
ROCK_MAT = 210
MOSS_MAT = 211
TREE_LEAF_MAT = 1
TREE_BARK_MAT = 2
# Airplane airframe material ids — the SAME numbers scripts/bake_airplane_airframe.py
# uses, so a mesh baked in "airframe" mode is coloured by the compositor exactly
# like the built-in biplane. 200 = fuselage body, 201 = accent (top surfaces,
# reads as a canopy/spine highlight). (202 canopy-glass and 208 propeller are
# authored-plane-only; a downloaded mesh has no separate propeller.)
AIRFRAME_BODY_MAT = 200
AIRFRAME_ACCENT_MAT = 201


def _airframe_remap(px, py, pz, forward_axis):
    """
    Rotate a Y-up model so its NOSE points along the airframe's +X forward axis.

    The built-in biplane (bake_airplane_airframe.py) is authored nose-forward
    along +X, up along +Y, wings along Z. A downloaded plane mesh, though, may
    have its nose along a different source axis. This maps the chosen source
    "forward" axis onto +X while keeping the world up (+Y) unchanged, by a yaw
    (rotation about the vertical Y axis) of 0/90/180/270 degrees:

      forward=+x : identity                (nose already +X)
      forward=-x : 180 deg  -> (-x, y, -z) (nose was tail-ward)
      forward=+z : -90 deg  -> ( z, y, -x) (nose was to the +Z side)
      forward=-z : +90 deg  -> (-z, y,  x)

    Parameters are the already-centred (origin-subtracted) coordinates.
    """
    if forward_axis == "+x":
        return (px, py, pz)
    if forward_axis == "-x":
        return (-px, py, -pz)
    if forward_axis == "+z":
        return (pz, py, -px)
    if forward_axis == "-z":
        return (-pz, py, px)
    raise ValueError("bad forward_axis %r (use +x/-x/+z/-z)" % forward_axis)


def _up_axis_remap(px, py, pz, up_axis):
    """
    Rotate a point so the model's own "up" direction becomes the world's +Y.

    WHY THIS EXISTS: different 3D tools disagree about which axis points at the
    sky. Godot (and this game) treat +Y as up, but Blender and several exporters
    treat +Z as up. If a model authored Z-up is baked without correcting for
    that, it lands in the world lying on its side. That is exactly what happened
    to the Pieta statue: it was baked assuming Y-up, and appeared toppled over.

    up_axis names WHICH AXIS OF THE SOURCE MESH points at the sky:
      "+y"  already correct, change nothing.
      "+z"  a Z-up model (the Blender convention). Tip it upright by rotating
            -90 degrees about the X axis: (x, y, z) -> (x, z, -y). The old Z
            becomes the new Y (up), and the old Y becomes the new -Z (depth).
      "-y"  Y-up but pointing downward after an exporter basis conversion.
            Rotate 180 degrees about X: (x, y, z) -> (x, -y, -z). This is a
            proper rotation, so positions and normals keep the same winding.
      "-z"  Z-up but pointing the other way: (x, y, z) -> (x, -z, y).
      "+x"  X-up: (x, y, z) -> (-y, x, z).
      "-x"  X-up the other way: (x, y, z) -> (y, -x, z).
    """
    if up_axis == "+y":
        return (px, py, pz)
    if up_axis == "-y":
        return (px, -py, -pz)
    if up_axis == "+z":
        return (px, pz, -py)
    if up_axis == "-z":
        return (px, -pz, py)
    if up_axis == "+x":
        return (-py, px, pz)
    if up_axis == "-x":
        return (py, -px, pz)
    raise ValueError("bad up_axis %r (use +y/-y/+z/-z/+x/-x)" % up_axis)


def stone_edge_roles(verts, faces):
    """FL-4359: two bits per triangle edge: smooth, convex, concave, boundary.

    Weld position aliases for adjacency only. Preserve every authored vertex,
    triangle and winding in the output; a UV seam is not a carved contour.
    """
    def sub(a, b):
        return tuple(x - y for x, y in zip(a, b))

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    normals, edges = [], {}
    for index, face in enumerate(faces):
        p = [verts[i]["pos"] for i in face]
        u, v = sub(p[1], p[0]), sub(p[2], p[0])
        n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        length = math.sqrt(dot(n, n))
        normals.append(tuple(x / length for x in n) if length > 1e-12 else (0, 0, 0))
        for edge in range(3):
            key = tuple(sorted(tuple(round(x, 9) for x in p[k]) for k in (edge, (edge+1) % 3)))
            edges.setdefault(key, []).append((index, edge, p[(edge+2) % 3]))
    roles = [0] * len(faces)
    for uses in edges.values():
        if len(uses) == 1:
            index, edge, _ = uses[0]
            roles[index] |= 3 << (edge * 2)
        elif len(uses) == 2:
            for index, edge, opposite in uses:
                other = uses[1] if uses[0][0] == index else uses[0]
                if dot(normals[index], normals[other[0]]) >= math.cos(math.radians(32.0)):
                    continue
                role = 2 if dot(normals[index], sub(other[2], opposite)) > 1e-9 else 1
                roles[index] |= role << (edge * 2)
        # Non-manifold adjacency has no unique crease normal; leave it unmarked.
    return roles

FACE_B_OPEN = 0xFFFFFFFF
EDGE_TABLE_SCHEMA = "fl4359.edge_rows.v2"
EDGE_ROW_STRIDE_FLOATS = 16
EDGE_ROW_STRIDE_UINTS = 4
EDGE_RUN_STRIDE_UINTS = 2


def fl4359_edge_table(corners, face_mats, face_roles):
    """FL-4359 true-FF acquisition: view-independent edge table, one row per
    unique undirected edge over the BAKED (transformed) corner positions --
    the same domain the GPU reads and the CPU proof welds.

    corners: per-face (p0, p1, p2) transformed position triples.
    face_mats: per-face material id. face_roles: per-face packed 2-bit roles
    in the stone_edge_roles layout (edge k owns bits 2k..2k+1).

    Weld convention is stone_edge_roles' own: sorted round-9 endpoints, so a
    role computed pre-transform names the same edge post-transform (the bake
    transform is translate plus uniform scale, topology-preserving).
    face_b is FACE_B_OPEN for single-use (open, role 3) edges. Edges used by
    three or more faces keep the first two; their role stays 0 because
    stone_edge_roles leaves non-manifold adjacency unmarked.

    Returns (floats, uints, stats). floats holds 16 numbers per edge:
    v0.xyz,0, v1.xyz,0, n_a.xyz,0, n_b.xyz,0 (n_b zero for open edges).
    uints holds 4 per edge: face_a, face_b, role, mat_a. The record is five
    vec4 slots (80 bytes) so it can go straight into std430 later.
    """
    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def cross(u, v):
        return (u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0])

    def unit(n):
        length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
        if length <= 1e-12:
            return (0.0, 0.0, 0.0)
        return (n[0] / length, n[1] / length, n[2] / length)

    face_normals = []
    for (p0, p1, p2) in corners:
        face_normals.append(unit(cross(sub(p1, p0), sub(p2, p0))))
    table = {}
    order = []
    for face_index, (p0, p1, p2) in enumerate(corners):
        for local in range(3):
            a, b = (p0, p1, p2)[local], (p0, p1, p2)[(local + 1) % 3]
            key = tuple(sorted(tuple(round(x, 9) for x in p) for p in (a, b)))
            slot = table.get(key)
            if slot is None:
                slot = {"v0": a, "v1": b, "faces": []}
                table[key] = slot
                order.append(key)
            slot["faces"].append((face_index, local))
    floats: list = []
    uints: list = []
    stats = {"edges": len(order), "open": 0, "non_manifold": 0, "degenerate": 0}
    for key in order:
        slot = table[key]
        uses = slot["faces"]
        face_a, local_a = uses[0]
        if len(uses) == 1:
            face_b = FACE_B_OPEN
            stats["open"] += 1
        else:
            face_b = uses[1][0]
            if len(uses) > 2:
                stats["non_manifold"] += 1
        if slot["v0"] == slot["v1"]:
            stats["degenerate"] += 1
        role = (face_roles[face_a] >> (local_a * 2)) & 3
        n_a = face_normals[face_a]
        n_b = face_normals[face_b] if face_b != FACE_B_OPEN else (0.0, 0.0, 0.0)
        floats.extend([slot["v0"][0], slot["v0"][1], slot["v0"][2], 0.0,
                       slot["v1"][0], slot["v1"][1], slot["v1"][2], 0.0,
                       n_a[0], n_a[1], n_a[2], 0.0,
                       n_b[0], n_b[1], n_b[2], 0.0])
        uints.extend([face_a, face_b, role, face_mats[face_a]])
    return floats, uints, stats

# FF-ACQ-04C: maximum direction change along a run, degrees. PROVISIONAL
# value; mechanism parent is the FF-DPR-49 arc-window unit (consecutive
# frames absorb turnover inside the window, so one mesh span must read as
# one identity across the sub-window turns a silhouette migrates over).
# Order anchor only: the -05 census puts overwhelming edge sharing below
# ~0.3 deg and the 0.5-collapse past ~3-5 deg; 30 deg chains smooth mesh
# contours while stopping at corners. Item 3 (run-key Jaccard on the
# manifest steps) confirms or rejects this value post-hoc: run-key sharing
# near 1.0 means the threshold holds real contours.
RUN_MAX_TURN_DEG = 30.0


def fl4359_edge_runs(floats):
    """FF-ACQ-04C: chain table edges into runs. Edges sharing a welded
    vertex (same round-9 convention as fl4359_edge_table) join one run
    while the unsigned direction change stays below RUN_MAX_TURN_DEG.
    Deterministic: start edges in index order, min-(turn, index) greedy.
    Degenerate (zero-length) edges never chain and read as runs of one.
    Returns (run_ids, run_orders, stats) with run ids dense 0..R-1 and
    consecutive order indices per run. floats is the 16-per-edge table.
    """
    if len(floats) % EDGE_ROW_STRIDE_FLOATS:
        raise ValueError("edge rows have a partial float record")
    count = len(floats) // EDGE_ROW_STRIDE_FLOATS
    dirs = []
    keys = []
    for i in range(count):
        v0 = (floats[i * EDGE_ROW_STRIDE_FLOATS],
              floats[i * EDGE_ROW_STRIDE_FLOATS + 1],
              floats[i * EDGE_ROW_STRIDE_FLOATS + 2])
        v1 = (floats[i * EDGE_ROW_STRIDE_FLOATS + 4],
              floats[i * EDGE_ROW_STRIDE_FLOATS + 5],
              floats[i * EDGE_ROW_STRIDE_FLOATS + 6])
        d = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
        length = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
        if length <= 1e-12:
            dirs.append(None)
        else:
            dirs.append((d[0] / length, d[1] / length, d[2] / length))
        keys.append((tuple(round(x, 9) for x in v0),
                     tuple(round(x, 9) for x in v1)))
    at_vertex = {}
    for i, (ka, kb) in enumerate(keys):
        at_vertex.setdefault(ka, []).append(i)
        at_vertex.setdefault(kb, []).append(i)
    run_ids = [-1] * count
    run_orders = [0] * count
    runs = 0
    max_turn = math.cos(math.radians(RUN_MAX_TURN_DEG))

    def away(edge, pt):
        """Unit direction of edge leaving pt (None for degenerate)."""
        d = dirs[edge]
        if d is None:
            return None
        ka, kb = keys[edge]
        if pt == ka:
            return d
        return (-d[0], -d[1], -d[2])

    def extend(head_pt, travel):
        """Greedy min-turn extension from head_pt along travel. Returns the
        appended edge list in walk order. Signed turns: hairpins break."""
        out = []
        while True:
            best = None
            best_cos = -2.0
            for cand in at_vertex.get(head_pt, []):
                if run_ids[cand] != -1:
                    continue
                leave = away(cand, head_pt)
                if leave is None:
                    continue
                c = (travel[0] * leave[0] + travel[1] * leave[1]
                     + travel[2] * leave[2])
                if best is None or (c, -cand) > (best_cos, -best):
                    best = cand
                    best_cos = c
            if best is None or best_cos < max_turn:
                return out
            out.append(best)
            run_ids[best] = runs
            ka, kb = keys[best]
            travel = away(best, head_pt)
            head_pt = kb if ka == head_pt else ka
        return out

    for start in range(count):
        if run_ids[start] != -1:
            continue
        if dirs[start] is None:
            run_ids[start] = runs
            run_orders[start] = 0
            runs += 1
            continue
        run_ids[start] = runs
        ka0, kb0 = keys[start]
        suffix = extend(kb0, dirs[start])
        neg = (-dirs[start][0], -dirs[start][1], -dirs[start][2])
        prefix = extend(ka0, neg)
        ordered = list(reversed(prefix)) + [start] + suffix
        for order, edge in enumerate(ordered):
            run_orders[edge] = order
        runs += 1
    lengths = [0] * runs
    for rid in run_ids:
        lengths[rid] += 1
    stats = {"runs": runs,
             "mean_length": (sum(lengths) / runs) if runs else 0.0,
             "max_length": max(lengths) if lengths else 0}
    return run_ids, run_orders, stats


def bake(akm_path, output_path, target_size, shade, mode, forward_axis="+x",
         airframe_mat=None, airframe_body_mat=AIRFRAME_BODY_MAT,
         airframe_accent_mat=AIRFRAME_ACCENT_MAT, moss_threshold=None,
         up_axis="+y", yaw_degrees=0.0, stone_topology=False, wing_root_z=None):
    """
    Convert an .akm mesh into a faces JSON the game's face path can consume.

    mode == "rock":
        Size by horizontal FOOTPRINT (boulders read by width). Up-facing
        triangles become moss (211), the rest rock (210).
    mode == "tree":
        Size by HEIGHT (trees read by how tall they are). Triangles in the
        lower part of the model become bark/trunk (2); everything above a
        height fraction becomes leaf/canopy (1).
    mode == "statue":
        Size by HEIGHT (a statue reads by how tall it stands). EVERY face gets
        the same bare-rock material (210) -- UNLESS moss_threshold is given.

    moss_threshold (optional float, statue mode only):
        Turns a bare statue into an OVERGROWN, weathered one. Any triangle
        whose averaged surface normal points upward more than this amount
        becomes moss (211) instead of rock (210); everything else stays rock.

        The number is the Y component of the triangle's averaged unit normal:
          1.00 = perfectly flat, facing straight up at the sky
          0.00 = a vertical wall
         -1.00 = facing straight down at the ground
        So a SMALLER threshold catches MORE surfaces. Rock mode hard-codes
        0.35, which only takes near-horizontal shelves. For a statue we want
        the reference look where moss visibly spills down over shoulders and
        rounded upper edges, so a smaller value (~0.15) is the useful setting:
        it grabs every surface tilted even slightly skyward while still
        leaving vertical faces and undercuts as clean stone.
    mode == "airframe":
        A FLYABLE airplane body (e.g. the downloaded Nausicaa plane), baked into
        the SAME model-space faces format the biplane uses so World.gd can pick
        it per-plane. The mesh is centred on ALL axes (a plane pivots about its
        centre, it does not sit on the ground), rotated so its nose points +X
        (see forward_axis / _airframe_remap), and scaled so its forward LENGTH
        hits target_size. Upper surfaces get airframe_accent_mat (201 by
        default), the rest airframe_body_mat (200 by default). Callers baking
        another vehicle family must pass that family's dedicated columns; a
        race car must never inherit the airplane palette. No propeller faces
        (prop_face_indices = []).

    wing_root_z (optional float, airframe mode only; FL-4902 lane VP):
        Derives the wing part lane at bake time. A face whose baked centroid
        lies farther than this from the centreline along the lateral model
        axis (+Z after the nose->+X remap) is a wing face; the list is written
        as wing_face_indices, and World.gd registers those faces as rigid
        part 2 so True FF can name their contours VEHICLE_WING. Choose a
        value inside the empty band between the fuselage's widest face and
        the wing root (Nausicaa: fuselage face centroids reach |z| 0.49, wing
        face centroids start at 1.35; the bake uses 0.75). Omitted: wing_face_indices = [] (no wing part).
    For rock/tree/statue the model is centred on X/Z with its base dropped to
    y=0 so anchoring at a terrain point sets it on the ground; for airframe it
    is centred on every axis instead.
    """
    verts, faces = parse_akm_ply(akm_path)
    if not verts or not faces:
        raise ValueError("AKM had no geometry")

    # --- Orientation correction ---------------------------------------------
    # Stand the model upright (and optionally spin it) BEFORE anything else
    # measures it. Everything downstream -- the bounding box, the centring, the
    # base-drop to y=0, the height-vs-footprint scaling, and the moss test -- all
    # assume "up is +Y", so the fix has to happen first or those steps inherit
    # the wrong orientation.
    #
    # NORMALS ARE ROTATED TOO, and that is not optional: the moss rule decides
    # "does this surface face the sky?" by reading the Y component of a face's
    # normal. If positions were rotated but normals were not, the statue would
    # stand up correctly yet grow moss on whichever side used to be its top.
    if up_axis != "+y" or yaw_degrees != 0.0:
        yaw = math.radians(yaw_degrees)
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)

        def _orient(vec):
            # First tip the model upright, then spin it about the (now correct)
            # vertical axis. Rotating about Y mixes X and Z and leaves height
            # alone, which is why the yaw is applied second.
            ox, oy, oz = _up_axis_remap(vec[0], vec[1], vec[2], up_axis)
            return (ox * cos_y + oz * sin_y, oy, -ox * sin_y + oz * cos_y)

        for v in verts:
            v["pos"] = _orient(v["pos"])
            v["nrm"] = _orient(v["nrm"])

    is_airframe = (mode == "airframe")

    # --- Bounds --------------------------------------------------------------
    xs = [v["pos"][0] for v in verts]
    ys = [v["pos"][1] for v in verts]
    zs = [v["pos"][2] for v in verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    cx = (min_x + max_x) * 0.5     # horizontal centre X
    cz = (min_z + max_z) * 0.5     # horizontal centre Z
    cy = (min_y + max_y) * 0.5     # vertical centre (airframe pivots here)
    height = max(max_y - min_y, 1e-6)
    footprint = max(max_x - min_x, max_z - min_z, 1e-6)

    if is_airframe:
        # Scale so the FORWARD span (the axis the nose runs along) hits
        # target_size, giving a plane of a predictable length.
        fwd_span = (max_x - min_x) if forward_axis in ("+x", "-x") else (max_z - min_z)
        scale = target_size / max(fwd_span, 1e-6)
    else:
        # Trees and statues scale by HEIGHT; rocks by footprint.
        scale = (target_size / height) if mode in ("tree", "statue") else (target_size / footprint)

    def transform(p):
        if is_airframe:
            # Centre on all axes, rotate nose->+X, then uniform-scale.
            rx, ry, rz = _airframe_remap(p[0] - cx, p[1] - cy, p[2] - cz, forward_axis)
            return (rx * scale, ry * scale, rz * scale)
        # rock/tree/statue: recentre horizontally, drop base to y=0, scale.
        return (
            (p[0] - cx) * scale,
            (p[1] - min_y) * scale,
            (p[2] - cz) * scale,
        )

    # For trees, anything whose (base-relative) height fraction is below this
    # line is treated as trunk/bark; above it is canopy/leaf. 0.32 puts the
    # split about a third of the way up, which reads as a normal trunk.
    bark_line = 0.32

    def choose_mat(v0, v1, v2):
        if is_airframe:
            # If a single uniform airframe material was requested, use it for
            # EVERY face. This is how a downloaded plane is given a DISTINCT
            # colour from the built-in red biplane: e.g. --airframe-mat 220 tags
            # the whole plane with the material the shader already tints blue, so
            # the second ridable plane is visibly a different aircraft, not a
            # second red one.
            if airframe_mat is not None:
                return airframe_mat
            # Otherwise: upper surfaces (top ~third by height) read as an accent/
            # spine highlight; everything else is the fuselage body colour.
            hy = ((v0["pos"][1] + v1["pos"][1] + v2["pos"][1]) / 3.0 - min_y) / height
            return airframe_accent_mat if hy > 0.62 else airframe_body_mat
        if mode == "statue":
            # A carved stone figure is one uniform material everywhere, unless
            # the caller asked for an overgrown statue. When moss is requested
            # we reuse the SAME normal-based rule the boulders use, just with a
            # caller-chosen (usually more generous) threshold, so "moss grows
            # on whatever faces the sky" stays one single concept in this file
            # rather than two rules that could drift apart.
            if moss_threshold is None:
                return ROCK_MAT
            avg_ny = (v0["nrm"][1] + v1["nrm"][1] + v2["nrm"][1]) / 3.0
            return MOSS_MAT if avg_ny > moss_threshold else ROCK_MAT
        if mode == "tree":
            # Average the three vertices' height fraction (0 at base, 1 at top).
            hy = ((v0["pos"][1] + v1["pos"][1] + v2["pos"][1]) / 3.0 - min_y) / height
            return TREE_BARK_MAT if hy < bark_line else TREE_LEAF_MAT
        # rock: moss on up-facing triangles, rock elsewhere.
        avg_ny = (v0["nrm"][1] + v1["nrm"][1] + v2["nrm"][1]) / 3.0
        return MOSS_MAT if avg_ny > 0.35 else ROCK_MAT

    # --- Emit faces -----------------------------------------------------------
    if stone_topology and mode not in ("rock", "statue"):
        raise ValueError("stone topology is only defined for retained rock/statue faces")
    edge_roles = stone_edge_roles(verts, faces) if stone_topology else [0] * len(faces)
    table_corners = []
    table_mats = []
    rows = []
    for face_index, (i0, i1, i2) in enumerate(faces):
        v0, v1, v2 = verts[i0], verts[i1], verts[i2]
        p0 = transform(v0["pos"])
        p1 = transform(v1["pos"])
        p2 = transform(v2["pos"])
        mat_id = choose_mat(v0, v1, v2)
        table_corners.append((p0, p1, p2))
        table_mats.append(mat_id)
        # A triangle stored as a degenerate quad (corner3 == corner2).
        rows.extend([p0[0], p0[1], p0[2], float(mat_id)])
        rows.extend([p1[0], p1[1], p1[2], float(shade)])
        rows.extend([p2[0], p2[1], p2[2], float(edge_roles[face_index])])
        rows.extend([p2[0], p2[1], p2[2], 4359.0 if stone_topology else 0.0])

    if mode == "tree":
        mats = {"leaf": TREE_LEAF_MAT, "bark": TREE_BARK_MAT}
    elif mode == "statue":
        # Report the moss id too when the statue was baked overgrown, so the
        # JSON header honestly lists every material actually present in `rows`.
        mats = ({"rock": ROCK_MAT} if moss_threshold is None
                else {"rock": ROCK_MAT, "moss": MOSS_MAT})
    elif is_airframe:
        mats = {"body": airframe_body_mat, "accent": airframe_accent_mat}
    else:
        mats = {"rock": ROCK_MAT, "moss": MOSS_MAT}

    # Reported dimensions: for the airframe the X axis is now the (scaled)
    # forward length after the nose->+X remap; for the others it stays source-X.
    if is_airframe:
        fwd_span = (max_x - min_x) if forward_axis in ("+x", "-x") else (max_z - min_z)
        lat_span = (max_z - min_z) if forward_axis in ("+x", "-x") else (max_x - min_x)
        dims = [fwd_span * scale, (max_y - min_y) * scale, lat_span * scale]
    else:
        dims = [(max_x - min_x) * scale, (max_y - min_y) * scale, (max_z - min_z) * scale]

    # FL-4359 true-FF acquisition: the view-independent edge table lives
    # beside the face rows in the same artifact. Roles come from an
    # independent stone_edge_roles pass so the table carries authored
    # crease/boundary facts for every mode without touching `rows`.
    table_roles = stone_edge_roles(verts, faces)
    table_floats, table_uints, table_stats = fl4359_edge_table(
        table_corners, table_mats, table_roles)
    run_ids, run_orders, run_stats = fl4359_edge_runs(table_floats)
    run_uints = []
    for i in range(table_stats["edges"]):
        run_uints.extend([run_ids[i], run_orders[i]])
    if len(table_floats) != table_stats["edges"] * EDGE_ROW_STRIDE_FLOATS:
        raise ValueError("edge float bank does not match edge-table count")
    if len(table_uints) != table_stats["edges"] * EDGE_ROW_STRIDE_UINTS:
        raise ValueError("edge uint bank does not match edge-table count")
    if len(run_uints) != table_stats["edges"] * EDGE_RUN_STRIDE_UINTS:
        raise ValueError("edge run bank does not match edge-table count")
    edge_table_doc = {
        "schema": EDGE_TABLE_SCHEMA,
        "count": table_stats["edges"],
        "stride_floats": EDGE_ROW_STRIDE_FLOATS,
        "stride_uints": EDGE_ROW_STRIDE_UINTS,
        "run_stride_uints": EDGE_RUN_STRIDE_UINTS,
        "run_columns": ["run_id", "order_index"],
        "bytes_std430": table_stats["edges"] * 80,
        "weld": "round9-sorted-endpoints-on-baked-positions",
        "face_b_open": FACE_B_OPEN,
        "open_edges": table_stats["open"],
        "non_manifold_edges": table_stats["non_manifold"],
        "degenerate_edges": table_stats["degenerate"],
        "runs": run_stats["runs"],
        "run_mean_length": round(run_stats["mean_length"], 3),
        "run_max_length": run_stats["max_length"],
        "run_max_turn_deg": RUN_MAX_TURN_DEG,
    }

    doc = {
        "version": 1,
        "source": os.path.basename(akm_path),
        "baker": "bake_akm_to_static_world_faces.py",
        "mode": mode,
        "scale": scale,
        "dimensions_xyz": dims,
        "face_count": len(faces),
        "mats": mats,
        "rows": rows,
        "edge_table": edge_table_doc,
        "edge_rows": {"floats": table_floats, "uints": table_uints,
                      "run_uints": run_uints},
    }
    if stone_topology:
        doc["stone_topology"] = {"version": 1, "marker": 4359,
                                 "edge_roles": "2bits-per-edge: smooth/convex/concave/boundary"}
    if wing_root_z is not None and not is_airframe:
        raise ValueError("--wing-root-z applies to airframe mode only")
    if is_airframe:
        # World.gd reads prop_face_indices from the airframe JSON (defaults to
        # empty if absent). A downloaded plane has no authored propeller, so we
        # state that explicitly. forward_axis is recorded for provenance.
        doc["prop_face_indices"] = []
        doc["wing_face_indices"] = []
        if wing_root_z is not None:
            for face in range(len(rows) // 16):
                # Rows are triangles (corner 3 repeats corner 2): centroid of 0..2.
                lateral = sum(rows[face * 16 + corner * 4 + 2] for corner in range(3)) / 3.0
                if abs(lateral) > wing_root_z:
                    doc["wing_face_indices"].append(face)
            doc["wing_rule"] = {"lateral_axis": "+z", "abs_centroid_gt": wing_root_z}
        doc["model_space"] = True
        doc["forward_axis"] = forward_axis
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump(doc, fh)
    print("wrote %s (faces=%d, footprint_scaled=%.2f, height_scaled=%.2f)" % (
        output_path, len(faces), max(doc["dimensions_xyz"][0], doc["dimensions_xyz"][2]),
        doc["dimensions_xyz"][1]))


def main():
    ap = argparse.ArgumentParser(description="Bake an .akm mesh into a static-world faces JSON.")
    ap.add_argument("input", help="Path to the .akm (ASCII PLY) mesh")
    ap.add_argument("--output", "-o", required=True, help="Output faces .json path")
    ap.add_argument("--target-size", type=float, default=2.4,
                    help="Target metres: footprint (rock mode) or height (tree mode)")
    ap.add_argument("--mode", choices=["rock", "tree", "statue", "airframe"], default="rock",
                    help="rock: moss-on-top by normal; tree: leaf/bark by "
                         "height; statue: uniform bare rock, height-sized; "
                         "airframe: flyable plane body, centred + nose->+X, "
                         "target-size = forward length")
    ap.add_argument("--forward-axis", choices=["+x", "-x", "+z", "-z"], default="+x",
                    help="(airframe mode) which SOURCE axis the nose points "
                         "along; it is rotated onto the airframe +X forward axis")
    ap.add_argument("--airframe-mat", type=int, default=None,
                    help="(airframe mode) tag EVERY face with this single material "
                         "id instead of the 200/201 body/accent split — used to "
                         "give a downloaded plane a distinct colour (e.g. 220 = the "
                         "material the shader tints blue)")
    ap.add_argument("--airframe-body-mat", type=int, default=AIRFRAME_BODY_MAT,
                    help="(airframe mode) body material for the split-material bake")
    ap.add_argument("--airframe-accent-mat", type=int, default=AIRFRAME_ACCENT_MAT,
                    help="(airframe mode) upper-surface material for the split-material bake")
    ap.add_argument("--wing-root-z", type=float, default=None,
                    help="(airframe mode) faces whose centroid |z| exceeds this lateral "
                         "distance are the wing part (wing_face_indices); omit for none")
    ap.add_argument("--shade", type=float, default=0.82, help="Per-face brightness (0..1)")
    ap.add_argument("--stone-topology", action="store_true",
                    help="Compile geometric crease roles into reserved static-face words, without changing geometry")
    ap.add_argument("--moss-threshold", type=float, default=None,
                    help="(statue mode) bake an OVERGROWN statue: any triangle "
                         "whose averaged normal Y exceeds this value becomes "
                         "moss (211) instead of rock (210). Smaller catches "
                         "more surfaces (1.0=straight up, 0.0=vertical wall). "
                         "Rock mode uses 0.35 internally; ~0.15 gives the "
                         "moss-spilling-over-the-shoulders look. Omit for a "
                         "clean bare statue.")
    ap.add_argument("--up-axis", choices=["+y", "-y", "+z", "-z", "+x", "-x"], default="+y",
                    help="Which axis of the SOURCE mesh points at the sky. This "
                         "game is +Y-up, but Blender and several exporters are "
                         "+Z-up, and a Z-up model baked as-is ends up lying on "
                         "its side. Use +z for those. Normals are rotated too, "
                         "so the moss-on-top test stays correct.")
    ap.add_argument("--yaw-degrees", type=float, default=0.0,
                    help="Spin the model about the vertical axis after standing "
                         "it upright, to choose which way it faces. Applied "
                         "second so it does not disturb the up-axis fix.")
    args = ap.parse_args()
    if not os.path.exists(args.input):
        print("Error: input not found: %s" % args.input, file=sys.stderr)
        sys.exit(1)
    bake(args.input, args.output, args.target_size, args.shade, args.mode,
         forward_axis=args.forward_axis, airframe_mat=args.airframe_mat,
         airframe_body_mat=args.airframe_body_mat,
         airframe_accent_mat=args.airframe_accent_mat,
         moss_threshold=args.moss_threshold, up_axis=args.up_axis,
         yaw_degrees=args.yaw_degrees, stone_topology=args.stone_topology,
         wing_root_z=args.wing_root_z)


if __name__ == "__main__":
    main()
