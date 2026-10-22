#!/usr/bin/env python3
"""FL-4907 BANK: offline True FF world feature bank builder.

One feature tree per asset revision (content digest), in the ABI owned by
godot_project/render/ascii/features/TrueFFFeatureABI.gd. This module reads
every size, offset, class id and flag from that file and the crease threshold
from TrueFFStructureABI.DEFAULT_CREASE_COS, so it restates no ABI number.

Inputs (the static geometry the game registers):
  * baked faces JSONs  godot_project/assets/world_mesh/*_faces.json
    (16 floats per face: 4 corners x,y,z,w; corner0.w = material; a triangle
    repeats corner2 as corner3). Authored face/fold sidecars belong to the
    runtime TrueFFFigureFeatures producer, not this contour compiler;
  * compiled chunk catalogs godot_project/assets/world_partition/
    *fl4789_chunk_catalog.v1.json with their FL4789P1 payload (trees,
    authored fixtures), rows read at rows_offset/rows_length.
Content digest = SHA-256(int32 face_count + float32 rows), the same value as
StaticGeometryStore.asset_content_hash and the chunk compiler's content_hash,
so the runtime finds a bank from SceneGeometryDB.static_asset_content_hash.

Output: godot_project/assets/world_partition/true_ff_feature_bank/
  <content_hash>.v1.bin  magic TFFBANK1, u32 version, u32 header bytes,
                         JSON header (space padded to 16), then 16-byte aligned
                         node, feature and face sections (offsets in header)
  index.v1.json          content_hash -> file, counts, sources
Deterministic: no timestamps, sorted keys, fixed traversal orders.

Terrain: build_terrain_bank() emits the compact implicit-grid terrain tree
(TrueFFTerrainVisibility quadtree, 16-cell leaves). Nodes carry bounds, cones,
grid regions and fixed-class presence; terrain_classes carries one uint16 per
grid vertex. Runtime derives edges, endpoints, normals, ids and shoreline
segments from that section. The map asset route accepts an authoritative
heightfield source from `build --terrain-heights FILE.json` or
`build --terrain-map FILE.a3d`; the source carries origin, topology, corner
heights, materials and water level explicitly.

World planes (FL-4907 WATER_CLOUD_RETAINED_HIERARCHY_20261002): every
terrain source also emits the retained water-surface and cloud-layer banks
(build_world_plane_bank; NODE_FLAG_PLANE in TrueFFFeatureABI.gd), keyed by
SHA-256("<PLANE_ENCODING>:<plane>:<terrain digest>"). Rebuild only those with
`build --terrain-map MAP.a3d --retain-unselected --world-planes-only`.

usage: true_ff_feature_bank.py build --all [--check]
       true_ff_feature_bank.py build --all --rigid --actor-product P.json --retain-unselected --prune
       true_ff_feature_bank.py build --faces F.json [--catalog C.json]
           [--terrain-heights H.json] [--out DIR]
       true_ff_feature_bank.py terrain --heights H.json --out FILE
       true_ff_feature_bank.py refit-bounds --all [--check]

``refit-bounds`` is an offline migration helper owned by this compiler.  It
reads retained feature/face records, recomputes only explicit-mesh node sphere
floats with the same support helper used by ``assemble()``, and leaves implicit
terrain banks byte-identical.  ``--check`` never writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import importlib.util
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "godot_project"
ABI_GD = PROJ / "render/ascii/features/TrueFFFeatureABI.gd"
STRUCTURE_ABI_GD = PROJ / "render/ascii/structure/TrueFFStructureABI.gd"
TERRAIN_VISIBILITY_GD = PROJ / "render/ascii/structure/TrueFFTerrainVisibility.gd"
TERRAIN_WATER_GD = PROJ / "render/scene/geometry/terrain/TerrainWaterGeometryStore.gd"
WORLD_MESH = PROJ / "assets/world_mesh"
WORLD_PARTITION = PROJ / "assets/world_partition"
DEFAULT_OUT = WORLD_PARTITION / "true_ff_feature_bank"
FLOATS_PER_FACE = 16
CATALOG_MAGIC = b"FL4789P1"
CONE_MARGIN_RAD = 1.0e-4
WELD_DIGITS = 9  # bake_akm_to_static_world_faces.py weld convention


def _gd_constants(path: Path) -> dict:
    out = {}
    for m in re.finditer(r"^const\s+(\w+)\s*(?::\s*\w+)?\s*:?=\s*([^\n#]+)", path.read_text(encoding="utf-8"), re.M):
        name, raw = m.group(1), m.group(2).strip()
        if raw.startswith('"'):
            out[name] = raw.strip('"')
            continue
        try:
            out[name] = int(raw, 0)
        except ValueError:
            try:
                out[name] = float(raw)
            except ValueError:
                pass
    return out


def load_abi() -> dict:
    abi = _gd_constants(ABI_GD)
    structure = _gd_constants(STRUCTURE_ABI_GD)
    abi["CREASE_COS"] = structure["DEFAULT_CREASE_COS"]
    abi["BYPASS_CLASSES"] = {abi[f"CLASS_{n}"] for n in (
        "CREASE", "MATERIAL_BOUNDARY", "OPEN_BOUNDARY", "TWO_SIDED", "AUTHORED_STROKE",
        "TERRAIN_RELIEF", "SHORELINE")}
    return abi


ABI = load_abi()
MAX_DEPTH = ABI["HIERARCHY_LEVELS"] - 1
# Plant crown contour, interior and underside (true_ff_role_vegetation.glslinc).
CANOPY_MATERIALS = frozenset({241, 242, 243})


def terrain_water_surface_y() -> float:
    """Read the retained water plane from its one GDScript owner.

    Terrain banks must be compiled with the same water fact that World and
    TerrainWaterGeometryStore publish.  In particular, this is deliberately
    not a duplicated fallback literal in the compiler.
    """
    text = TERRAIN_WATER_GD.read_text(encoding="utf-8")
    match = re.search(
        r"^const\s+WATER_SURFACE_Y\s*:?=\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.M,
    )
    if not match:
        raise ValueError(f"{TERRAIN_WATER_GD}: WATER_SURFACE_Y owner expression missing")
    numerator, denominator = (float(value) for value in match.groups())
    if denominator == 0.0:
        raise ValueError(f"{TERRAIN_WATER_GD}: WATER_SURFACE_Y denominator is zero")
    return numerator / denominator


def terrain_content_hash(heights, dims, origin_xz, cell, water_level, materials=None) -> str:
    """Return the filename digest shared by the runtime terrain connection."""
    W, H = (int(dims[0]), int(dims[1]))
    return hashlib.sha256(
        struct.pack(
            "<2i3f", W, H, float(origin_xz[0]), float(origin_xz[1]), float(cell)
        )
        + struct.pack("<f", float(water_level))
        + struct.pack("<%df" % len(heights), *heights)
        + bytes(materials if materials is not None else [0] * (W * H))
    ).hexdigest()


# --------------------------------------------------------------- primitives
def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _stored_point(p):
    """Canonicalize a point exactly as it is stored in a feature record."""
    return tuple(f32(v) for v in p)


def fnv1a32(data: bytes) -> int:
    h = ABI["FNV_OFFSET"]
    for b in data:
        h = ((h ^ b) * ABI["FNV_PRIME"]) & 0xFFFFFFFF
    return h


def stable_id(content_hash: str, local_index: int) -> int:
    return fnv1a32(bytes.fromhex(content_hash) + struct.pack("<I", local_index))


def content_hash_of(rows, face_count: int) -> str:
    return hashlib.sha256(struct.pack("<i", face_count) + struct.pack("<%df" % len(rows), *rows)).hexdigest()


def _snorm16(v: float) -> int:
    return int(round(max(-1.0, min(1.0, v)) * 32767.0)) & 0xFFFF


def oct_encode(n) -> int:
    """Octahedral snorm16x2; zero vector encodes as 0 (flagged by callers)."""
    s = abs(n[0]) + abs(n[1]) + abs(n[2])
    if s <= 1e-12:
        return 0
    x, y, z = n[0] / s, n[1] / s, n[2] / s
    if z < 0.0:
        x, y = (1.0 - abs(y)) * (1.0 if x >= 0.0 else -1.0), (1.0 - abs(x)) * (1.0 if y >= 0.0 else -1.0)
    return _snorm16(x) | (_snorm16(y) << 16)


def oct_decode(packed: int):
    """Mirror of tff_oct_decode (unpackSnorm2x16 then fold)."""
    def s16(v):
        return v - 0x10000 if v >= 0x8000 else v
    x = max(-1.0, min(1.0, s16(packed & 0xFFFF) / 32767.0))
    y = max(-1.0, min(1.0, s16((packed >> 16) & 0xFFFF) / 32767.0))
    z = 1.0 - abs(x) - abs(y)
    t = max(-z, 0.0)
    x += -t if x >= 0.0 else t
    y += -t if y >= 0.0 else t
    return unit((x, y, z))


# --------------------------------------------------------------- mesh input
def triangles_from_rows(rows, face_count):
    """(corners, material, source_face) per triangle; quads split c0c1c2+c0c2c3."""
    tris = []
    for f in range(face_count):
        b = f * FLOATS_PER_FACE
        c = [tuple(rows[b + k * 4: b + k * 4 + 3]) for k in range(4)]
        mat = int(rows[b + 3])
        tris.append(((c[0], c[1], c[2]), mat, f))
        if c[3] != c[2] and c[3] != c[0]:
            tris.append(((c[0], c[2], c[3]), mat, f))
    return tris


def _key(p):
    return tuple(round(x, WELD_DIGITS) for x in p)


class Feature:
    __slots__ = ("local", "p0", "p1", "fn0", "fn1", "vn0", "vn1", "cls", "mat",
                 "open", "k0", "k1", "chain", "a0", "a1", "sid", "zero_normal")


def _angle_at(p, q, r):
    u, v = unit(sub(q, p)), unit(sub(r, p))
    return math.acos(max(-1.0, min(1.0, dot(u, v)))) if norm(u) and norm(v) else 0.0


def mesh_features(tris, two_sided_materials=frozenset()):
    """Every unique undirected edge (first-seen order over triangles, the
    bake_akm edge-table order) classified by the ABI class rules."""
    face_n = [unit(cross(sub(t[0][1], t[0][0]), sub(t[0][2], t[0][0]))) for t in tris]
    vnorm = {}
    table, order, tri_edges = {}, [], []
    for ti, (corners, _mat, _f) in enumerate(tris):
        for k in range(3):
            p, q, r = corners[k], corners[(k + 1) % 3], corners[(k + 2) % 3]
            w = _angle_at(p, q, r)
            acc = vnorm.setdefault(_key(p), [0.0, 0.0, 0.0])
            for j in range(3):
                acc[j] += face_n[ti][j] * w
        ids = []
        for k in range(3):
            a, b = corners[k], corners[(k + 1) % 3]
            key = tuple(sorted((_key(a), _key(b))))
            slot = table.get(key)
            if slot is None:
                slot = {"v0": a, "v1": b, "tris": [], "index": len(order)}
                table[key] = slot
                order.append(key)
            slot["tris"].append(ti)
            ids.append(slot["index"])
        tri_edges.append(ids)
    vn = {k: unit(tuple(v)) for k, v in vnorm.items()}
    feats = []
    for key in order:
        s = table[key]
        ta = s["tris"][0]
        tb = s["tris"][1] if len(s["tris"]) > 1 else None
        na = face_n[ta]
        nb = face_n[tb] if tb is not None else na
        ma = tris[ta][1]
        mb = tris[tb][1] if tb is not None else ma
        if tb is None:
            cls = ABI["CLASS_OPEN_BOUNDARY"]
        elif ma in two_sided_materials or mb in two_sided_materials \
                or (norm(na) > 0 and norm(nb) > 0 and dot(na, nb) <= -1.0 + 1e-6):
            cls = ABI["CLASS_TWO_SIDED"]
        elif ma in CANOPY_MATERIALS and mb in CANOPY_MATERIALS:
            # A low-poly crown's facet edges and tier seams are tessellation,
            # not branches: as creases they drew a dashed lattice across every
            # crown. The crown keeps its envelope and tier silhouettes only
            # (smooth contours); AAHub sonota-shokubutsu resK-482.
            cls = ABI["CLASS_SMOOTH_CANDIDATE"]
        elif ma != mb:
            cls = ABI["CLASS_MATERIAL_BOUNDARY"]
        elif norm(na) > 0 and norm(nb) > 0 and dot(na, nb) < ABI["CREASE_COS"]:
            cls = ABI["CLASS_CREASE"]
        else:
            cls = ABI["CLASS_SMOOTH_CANDIDATE"]
        f = Feature()
        f.local = s["index"]
        f.p0, f.p1 = s["v0"], s["v1"]
        f.k0, f.k1 = _key(s["v0"]), _key(s["v1"])
        f.fn0, f.fn1 = na, nb
        f.vn0, f.vn1 = vn[f.k0], vn[f.k1]
        f.cls, f.mat, f.open = cls, ma, tb is None
        feats.append(f)
    return feats, tri_edges, face_n


# --------------------------------------------------------------- chains
def build_chains(feats):
    """Maximal runs through valence-2 vertices of one class. Orients each
    feature along its chain (p0 -> p1) and assigns cumulative arc length."""
    at = {}
    for i, f in enumerate(feats):
        at.setdefault((f.cls, f.k0), []).append(i)
        at.setdefault((f.cls, f.k1), []).append(i)
    done = [False] * len(feats)
    chain = 0

    def walk(start_key, e):
        arc = 0.0
        cur_key = start_key
        while True:
            f = feats[e]
            done[e] = True
            if f.k0 != cur_key:  # orient along the walk
                f.p0, f.p1, f.k0, f.k1 = f.p1, f.p0, f.k1, f.k0
                f.vn0, f.vn1 = f.vn1, f.vn0
            f.chain = chain
            f.a0 = arc
            arc += norm(sub(f.p1, f.p0))
            f.a1 = arc
            nxt = at[(f.cls, f.k1)]
            if len(nxt) != 2 or f.k0 == f.k1:
                return
            e2 = nxt[0] if nxt[1] == e else nxt[1]
            if done[e2]:
                return
            cur_key, e = f.k1, e2

    for i, f in enumerate(feats):
        if done[i]:
            continue
        if len(at[(f.cls, f.k0)]) != 2:
            walk(f.k0, i)
            chain += 1
        elif len(at[(f.cls, f.k1)]) != 2:
            walk(f.k1, i)
            chain += 1
    for i, f in enumerate(feats):  # closed loops: lowest index, original v0
        if not done[i]:
            walk(f.k0, i)
            chain += 1
    return chain


# --------------------------------------------------------------- octree
class Node:
    __slots__ = ("children", "feats", "level", "index", "first", "count",
                 "face_first", "face_count", "sphere", "cone", "flags",
                 "region", "height_range", "region_points", "region_normals",
                 "terrain_fixed")

    def __init__(self):
        self.region_points = None
        self.region_normals = None
        self.terrain_fixed = False


def _octree(items, centre, half, level):
    node = Node()
    node.level, node.children, node.feats = level, [], []
    if len(items) <= ABI["LEAF_MAX_FEATURES"]:
        node.feats = items
        return node
    if level >= MAX_DEPTH:
        raise ValueError("feature hierarchy exhausted its ABI depth before reaching bounded leaves")
    buckets = [[] for _ in range(8)]
    for it in items:
        m = it[1]
        o = (m[0] >= centre[0]) | ((m[1] >= centre[1]) << 1) | ((m[2] >= centre[2]) << 2)
        buckets[o].append(it)
    # FL-4907: reserve enough remaining levels for every feature, including
    # coincident midpoints that spatial subdivision cannot separate. Source
    # order within each octant breaks ties; no feature or identity is removed.
    # Such payload children share this spatial bucket. Their actual enclosing
    # spheres/cones are still computed from complete feature support below.
    child_capacity = ABI["LEAF_MAX_FEATURES"] * 8 ** (MAX_DEPTH - level - 1)
    largest = max(len(bucket) for bucket in buckets)
    coincident = largest == len(items) and all(item[1] == items[0][1] for item in items)
    if coincident or largest > child_capacity:
        ordered = [item for bucket in buckets for item in bucket]
        block = (len(ordered) + 7) // 8
        for first in range(0, len(ordered), block):
            node.children.append(_octree(ordered[first:first + block], centre, half, level + 1))
        return node
    h = half * 0.5
    for o, b in enumerate(buckets):
        if b:
            c = (centre[0] + (h if o & 1 else -h), centre[1] + (h if o & 2 else -h),
                 centre[2] + (h if o & 4 else -h))
            node.children.append(_octree(b, c, h, level + 1))
    return node


def _bfs(root):
    out, queue = [root], [root]
    while queue:
        nxt = []
        for n in queue:
            nxt.extend(n.children)
        out.extend(nxt)
        queue = nxt
    for i, n in enumerate(out):
        n.index = i
    return out


def _cone(normals):
    """Conservative cone over decoded (stored) normals; (axis, half) or None."""
    s = [0.0, 0.0, 0.0]
    for n in normals:
        for j in range(3):
            s[j] += n[j]
    axis = unit(tuple(s))
    if norm(axis) == 0.0:
        return None
    axis = unit(tuple(f32(x) for x in axis))
    half = 0.0
    for n in normals:
        half = max(half, math.acos(max(-1.0, min(1.0, dot(axis, n)))))
    half += CONE_MARGIN_RAD
    if half >= math.pi * 0.5:
        return None
    return axis, half


def _sphere_from_support(n, points_of):
    """Compute one conservative node sphere from its spatial support."""
    pts = points_of(n)
    if not pts:
        raise ValueError("cannot bound a node with no spatial support")
    lo = [min(p[j] for p in pts) for j in range(3)]
    hi = [max(p[j] for p in pts) for j in range(3)]
    centre = tuple(f32((lo[j] + hi[j]) * 0.5) for j in range(3))
    if n.children:
        r = max(norm(sub(c.sphere[0], centre)) + c.sphere[1] for c in n.children)
    else:
        r = max(norm(sub(p, centre)) for p in pts)
    r = r * (1.0 + 1e-6) + 1e-6 * (1.0 + max(abs(x) for x in centre))
    return centre, _f32_up(r)


def _finish_spheres(root, points_of):
    """Apply the shared sphere helper in post-order."""
    for child in root.children:
        _finish_spheres(child, points_of)
    root.sphere = _sphere_from_support(root, points_of)


def _finish_node(n, feats_of, normals_of, zero_of, points_of):
    """Sphere, cone and flags from the node's subtree (post-order)."""
    for c in n.children:
        _finish_node(c, feats_of, normals_of, zero_of, points_of)
    n.sphere = _sphere_from_support(n, points_of)
    cone = None if zero_of(n) else _cone(normals_of(n))
    n.cone = cone
    n.flags = (ABI["NODE_FLAG_LEAF"] if not n.children else 0) | \
        (ABI["NODE_FLAG_BYPASS"] if any(f.cls in ABI["BYPASS_CLASSES"] for f in feats_of(n)) else 0)


def _f32_up(x: float) -> float:
    """Smallest float32 >= x (x > 0)."""
    y = f32(x)
    if y < x:
        y = struct.unpack("<f", struct.pack("<I", struct.unpack("<I", struct.pack("<f", y))[0] + 1))[0]
    return y


def _subtree_leaves(n):
    if not n.children:
        return [n]
    out = []
    for c in n.children:
        out.extend(_subtree_leaves(c))
    return out


def feature_support_points(feats, tri_edges=None):
    """Return conservative support points for each feature.

    Every mesh edge owns its incident surface triangles for closed-node
    representatives; smooth edges also carry contour spans through them.
    Bounds must contain that full support, including fixed-class edges.
    Free authored strokes retain endpoint support. The helper is shared by
    fresh assembly and decoded-bank refitting.
    """
    supports = []
    for feature in feats:
        points = []
        seen = set()
        for point in (feature.p0, feature.p1):
            stored = _stored_point(point)
            key = _key(stored)
            if key not in seen:
                seen.add(key)
                points.append(stored)
        supports.append(points)
    if not tri_edges:
        return supports

    by_local = {int(feature.local): index for index, feature in enumerate(feats)}
    for edge_ids in tri_edges:
        if len(edge_ids) != 3:
            raise ValueError("each retained face must reference exactly three feature edges")
        try:
            indices = [by_local[int(edge_id)] for edge_id in edge_ids]
        except KeyError as exc:
            raise ValueError(f"retained face references unknown feature {exc.args[0]}") from exc
        triangle_points = []
        seen = set()
        for index in indices:
            feature = feats[index]
            for point in (feature.p0, feature.p1):
                stored = _stored_point(point)
                key = _key(stored)
                if key not in seen:
                    seen.add(key)
                    triangle_points.append(stored)
        for index in indices:
            supports[index].extend(triangle_points)
            deduped = []
            seen = set()
            for point in supports[index]:
                key = _key(point)
                if key not in seen:
                    seen.add(key)
                    deduped.append(point)
            supports[index] = deduped
    return supports


# --------------------------------------------------------------- bank
def _encode_feature_normals(f):
    packed = [oct_encode(v) for v in (f.fn0, f.fn1, f.vn0, f.vn1)]
    f.zero_normal = any(norm(v) == 0.0 for v in (f.fn0, f.fn1, f.vn0, f.vn1))
    return packed


def assemble(feats, tri_edges=None, tri_mats=None, root_nodes=None, *,
             triangle_source_faces=None, source_face_count=None):
    """Order source-owned features by octree leaf and emit their records.

    FL-4907: the source compiler owns identity before spatial reordering.
    Assembly must not overwrite actor identities then force its caller to
    recover them by matching geometric endpoints after serialization.
    """
    primitive_refs = None
    triangle_primitives = []
    if triangle_source_faces is not None:
        if (source_face_count is None or source_face_count <= 0
                or len(triangle_source_faces) != len(tri_edges or ())):
            raise ValueError("source primitive topology does not match mesh triangles")
        # P0 draws two triangle slots per original quad, even when the second
        # is degenerate. Preserve that hole; compact triangle indices are not
        # P0 addresses. Only the source compiler knows this permutation.
        primitive_refs = [(ABI["UINT_NONE"], ABI["UINT_NONE"])] * (2 * source_face_count)
        splits = [0] * source_face_count
        for source_face in triangle_source_faces:
            if not 0 <= source_face < source_face_count or splits[source_face] >= 2:
                raise ValueError("source face has an invalid triangle slot")
            triangle_primitives.append(2 * source_face + splits[source_face])
            splits[source_face] += 1
        if any(count == 0 for count in splits):
            raise ValueError("source face is missing its first triangle")
    if root_nodes is None:
        items = [(f, tuple((f.p0[j] + f.p1[j]) * 0.5 for j in range(3))) for f in feats]
        allp = [f.p0 for f in feats] + [f.p1 for f in feats]
        lo = [min(p[j] for p in allp) for j in range(3)]
        hi = [max(p[j] for p in allp) for j in range(3)]
        centre = tuple((lo[j] + hi[j]) * 0.5 for j in range(3))
        half = max(hi[j] - lo[j] for j in range(3)) * 0.5 + 1e-9
        root = _octree(items, centre, half, 0)
    else:
        root = root_nodes
    nodes = _bfs(root)
    leaves = _subtree_leaves(root)
    ordered = []
    for leaf in leaves:
        leaf.first = len(ordered)
        ordered.extend(it[0] for it in leaf.feats)
        leaf.count = len(ordered) - leaf.first
    pos = {id(f): i for i, f in enumerate(ordered)}
    packed_normals = {id(f): _encode_feature_normals(f) for f in ordered}
    decoded = {id(f): [oct_decode(p) for p in packed_normals[id(f)]] for f in ordered}

    def span(n):
        if not n.children:
            return n.first, n.count
        a = span(n.children[0])
        b = span(n.children[-1])
        return a[0], b[0] + b[1] - a[0]

    for n in nodes:
        n.first, n.count = span(n)
    region_normals = root.region_normals

    def feats_of(n):
        return ordered[n.first:n.first + n.count]

    def normals_of(n):
        if region_normals is not None:
            # Boundary vertex normals include adjacent facets outside this
            # terrain tile. They also participate in silhouette extraction,
            # so a facet-only cone could falsely reject its border crossings.
            return region_normals(n) + [v for f in feats_of(n) for v in decoded[id(f)]]
        return [v for f in feats_of(n) for v in decoded[id(f)]]

    def zero_of(n):
        if region_normals is not None:
            return False
        return any(f.zero_normal for f in feats_of(n))

    supports = feature_support_points(feats, tri_edges)
    support_by_id = {id(feature): supports[index] for index, feature in enumerate(feats)}

    def points_of(n):
        if n.region_points:
            return n.region_points
        return [point for feature in feats_of(n) for point in support_by_id[id(feature)]]

    _finish_node(root, feats_of, normals_of, zero_of, points_of)
    # Faces: grouped by the leaf owning edge0, leaf DFS order, then triangle.
    faces = []
    if tri_edges:
        leaf_of = {}
        for leaf in leaves:
            for f in feats_of(leaf):
                leaf_of[f.local] = leaf
        by_leaf = {}
        local_to_pos = {f.local: pos[id(f)] for f in ordered}
        for ti, ids in enumerate(tri_edges):
            by_leaf.setdefault(id(leaf_of[ids[0]]), []).append(ti)
        for leaf in leaves:
            leaf.face_first = len(faces)
            for ti in by_leaf.get(id(leaf), []):
                e = tri_edges[ti]
                if primitive_refs is not None:
                    primitive_refs[triangle_primitives[ti]] = (leaf.index, len(faces))
                first_edge = ordered[local_to_pos[e[0]]]
                second_edge = ordered[local_to_pos[e[1]]]
                # Source vertex keys are the compiler's incidence identity,
                # not a runtime nearest-point search. Keep the non-shared
                # endpoint of edge1 so the GPU can reconstruct the triangle
                # even after its edges have been oriented along chains.
                third_at_p1 = second_edge.k0 in (first_edge.k0, first_edge.k1)
                face_flags = tri_mats[ti] & 0xFFFF
                if third_at_p1:
                    face_flags |= ABI["FACE_FLAG_THIRD_AT_P1"]
                faces.append((local_to_pos[e[0]], local_to_pos[e[1]], local_to_pos[e[2]],
                              face_flags))
            leaf.face_count = len(faces) - leaf.face_first
    node_bytes = bytearray()
    for n in nodes:
        (c, r), cone = n.sphere, n.cone
        axis, half = cone if cone else ((0.0, 0.0, 0.0), ABI["CONE_UNDEFINED"])
        first_child = n.children[0].index if n.children else 0
        ff, fc = (n.face_first, n.face_count) if not n.children and tri_edges else (0, 0)
        node_bytes += struct.pack("<4f4f4I4I", c[0], c[1], c[2], r, axis[0], axis[1], axis[2], half,
                                  first_child, len(n.children), n.first, n.count,
                                  n.flags, n.level, ff, fc)
    feat_bytes = bytearray()
    for f in ordered:
        flags = (f.mat & ABI["FEATURE_FLAG_MATERIAL_MASK"]) | (ABI["FEATURE_FLAG_OPEN"] if f.open else 0)
        feat_bytes += struct.pack("<4f4f4I4I", f.p0[0], f.p0[1], f.p0[2], f.a0,
                                  f.p1[0], f.p1[1], f.p1[2], f.a1,
                                  *packed_normals[id(f)], f.cls, f.chain, f.sid, flags)
    face_bytes = b"".join(struct.pack("<4I", *fr) for fr in faces)
    assert len(node_bytes) == len(nodes) * ABI["NODE_BYTES"]
    assert len(feat_bytes) == len(ordered) * ABI["FEATURE_BYTES"]
    assert len(face_bytes) == len(faces) * ABI["FACE_BYTES"]
    classes = {}
    for f in ordered:
        classes[str(f.cls)] = classes.get(str(f.cls), 0) + 1
    result = {"nodes": bytes(node_bytes), "features": bytes(feat_bytes), "faces": face_bytes,
            "node_count": len(nodes), "feature_count": len(ordered), "face_count": len(faces),
            "class_counts": classes,
            "zero_normal_features": sum(1 for f in ordered if f.zero_normal),
            "max_leaf_features": max(l.count for l in leaves),
            "depth": max(n.level for n in nodes)}
    if primitive_refs is not None:
        result["primitive_refs"] = b"".join(struct.pack("<2I", *ref) for ref in primitive_refs)
        result["source_face_count"] = source_face_count
        result["primitive_encoding"] = ABI["PRIMITIVE_ENCODING"]
    return result


def build_mesh_bank(rows, face_count, *, two_sided_materials=frozenset()):
    content_hash = content_hash_of(rows, face_count)
    tris = triangles_from_rows(rows, face_count)
    feats, tri_edges, _face_n = mesh_features(tris, two_sided_materials)
    mesh_edges = len(feats)
    for feature in feats:
        feature.sid = stable_id(content_hash, feature.local)
    chains = build_chains(feats)
    bank = assemble(feats, tri_edges, [t[1] for t in tris],
                    triangle_source_faces=[t[2] for t in tris], source_face_count=face_count)
    bank.update({"content_hash": content_hash, "chain_count": chains,
                 "mesh_edge_count": mesh_edges, "triangle_count": len(tris),
                 "source_face_count": face_count})
    return bank


# --------------------------------------------------------------- terrain
TERRAIN_NODE_FLAG_IMPLICIT = ABI["NODE_FLAG_TERRAIN"]
TERRAIN_EDGE_H = 0
TERRAIN_EDGE_V = 1
TERRAIN_EDGE_D = 2
TERRAIN_SHORELINE_BIT_TRI0 = 1 << 12
TERRAIN_SHORELINE_BIT_TRI1 = 1 << 13
_TERRAIN_FIXED_CLASSES = frozenset((
    ABI["CLASS_CREASE"], ABI["CLASS_MATERIAL_BOUNDARY"], ABI["CLASS_OPEN_BOUNDARY"],
    ABI["CLASS_TWO_SIDED"], ABI["CLASS_AUTHORED_STROKE"], ABI["CLASS_TERRAIN_RELIEF"],
    ABI["CLASS_SHORELINE"],
))


def _terrain_validate(heights, dims, materials):
    W, H = (int(dims[0]), int(dims[1]))
    if W <= 0 or H <= 0 or W > 0xFFFF or H > 0xFFFF:
        raise ValueError("terrain dimensions must be in 1..65535")
    if 3 * (W + 1) * (H + 1) + 2 * W * H > ABI["UINT_NONE"]:
        raise ValueError("terrain implicit address namespace exceeds uint32")
    if len(heights) != (W + 1) * (H + 1):
        raise ValueError("terrain source dimensions do not match heights")
    mats = list(materials) if materials is not None else [0] * (W * H)
    if len(mats) != W * H or any(int(m) < 0 or int(m) > 255 for m in mats):
        raise ValueError("terrain source dimensions do not match materials")
    canonical_heights = [f32(h) for h in heights]
    if not all(math.isfinite(h) for h in canonical_heights):
        raise ValueError("terrain heights must be finite RF float32 samples")
    return W, H, canonical_heights, [int(m) for m in mats]


def _terrain_metric_facts(origin_xz, cell, water_level):
    """Use exactly the float32 facts hashed and sampled by the live source."""
    origin = tuple(f32(v) for v in origin_xz)
    step, water = f32(cell), f32(water_level)
    if len(origin) != 2 or not all(math.isfinite(v) for v in (*origin, step, water)) or step <= 0:
        raise ValueError("terrain origin/cell/water facts must be finite with positive cell size")
    return origin, step, water


def terrain_edge_address(x, z, orientation, dims):
    """Address an H(+x), V(+z), or D(+x,+z) implicit edge."""
    W, H = (int(dims[0]), int(dims[1]))
    x, z, orientation = int(x), int(z), int(orientation)
    valid = ((orientation == TERRAIN_EDGE_H and 0 <= x < W and 0 <= z <= H) or
             (orientation == TERRAIN_EDGE_V and 0 <= x <= W and 0 <= z < H) or
             (orientation == TERRAIN_EDGE_D and 0 <= x < W and 0 <= z < H))
    if not valid:
        raise ValueError("terrain edge coordinate is outside the grid")
    return 3 * (z * (W + 1) + x) + orientation


def terrain_shoreline_address(x, z, triangle, dims):
    """Address the post-edge shoreline namespace."""
    W, H = (int(dims[0]), int(dims[1]))
    x, z, triangle = int(x), int(z), int(triangle)
    if not (0 <= x < W and 0 <= z < H and triangle in (0, 1)):
        raise ValueError("terrain shoreline coordinate is outside the grid")
    return 3 * (W + 1) * (H + 1) + 2 * (z * W + x) + triangle


def terrain_stable_id(content_hash, address):
    return stable_id(content_hash, int(address))


def _terrain_points(heights, dims, origin_xz, cell):
    W, H = dims
    cols = W + 1

    def point(x, z):
        return (float(origin_xz[0]) + x * float(cell), heights[z * cols + x],
                float(origin_xz[1]) + z * float(cell))

    return point


def _terrain_triangle_normals(heights, dims, origin_xz, cell):
    W, H = dims
    P = _terrain_points(heights, dims, origin_xz, cell)
    out = [None] * (2 * W * H)
    for z in range(H):
        for x in range(W):
            c0, c1, c2, c3 = P(x, z), P(x + 1, z), P(x + 1, z + 1), P(x, z + 1)
            for tri, corners in ((0, (c0, c1, c2)), (1, (c0, c2, c3))):
                n = unit(cross(sub(corners[1], corners[0]), sub(corners[2], corners[0])))
                out[2 * (z * W + x) + tri] = n if n[1] >= 0.0 else tuple(-v for v in n)
    return out


def _terrain_vertex_normals(heights, dims, origin_xz, cell, tri_normals):
    """Angle-weighted normals from every incident triangle, including borders."""
    W, H = dims
    cols = W + 1
    P = _terrain_points(heights, dims, origin_xz, cell)
    acc = [[0.0, 0.0, 0.0] for _ in range(cols * (H + 1))]
    for z in range(H):
        for x in range(W):
            corners = ((x, z), (x + 1, z), (x + 1, z + 1), (x, z + 1))
            for tri, indices in ((0, (0, 1, 2)), (1, (0, 2, 3))):
                pts = [P(*corners[i]) for i in indices]
                n = tri_normals[2 * (z * W + x) + tri]
                for k, corner_index in enumerate(indices):
                    vx, vz = corners[corner_index]
                    w = _angle_at(pts[k], pts[(k + 1) % 3], pts[(k + 2) % 3])
                    for j in range(3):
                        acc[vz * cols + vx][j] += n[j] * w
    return [unit(tuple(v)) for v in acc]


def _terrain_edge_adjacency(x, z, orientation, dims):
    W, H = dims
    if orientation == TERRAIN_EDGE_H:
        if not (0 <= x < W and 0 <= z <= H):
            return None
        if z == 0:
            first, second = (x, z, 0), None
        elif z == H:
            first, second = (x, z - 1, 1), None
        else:
            first, second = (x, z, 0), (x, z - 1, 1)
    elif orientation == TERRAIN_EDGE_V:
        if not (0 <= x <= W and 0 <= z < H):
            return None
        if x == 0:
            first, second = (x, z, 1), None
        elif x == W:
            first, second = (x - 1, z, 0), None
        else:
            first, second = (x, z, 1), (x - 1, z, 0)
    elif orientation == TERRAIN_EDGE_D:
        if not (0 <= x < W and 0 <= z < H):
            return None
        first, second = (x, z, 0), (x, z, 1)
    else:
        raise ValueError("unknown terrain edge orientation")

    def tri_index(tx, tz, tri):
        return 2 * (tz * W + tx) + tri

    return (tri_index(*first), None if second is None else tri_index(*second))


def _terrain_edge_class(x, z, orientation, dims, materials, tri_normals):
    adjacent = _terrain_edge_adjacency(x, z, orientation, dims)
    if adjacent is None:
        return 0
    a, b = adjacent
    if b is None:
        return ABI["CLASS_OPEN_BOUNDARY"]
    W, H = dims
    ma, mb = materials[a // 2], materials[b // 2]
    na, nb = tri_normals[a], tri_normals[b]
    if ma != mb:
        return ABI["CLASS_MATERIAL_BOUNDARY"]
    d = dot(na, nb)
    if d <= -1.0 + 1e-6:
        return ABI["CLASS_TWO_SIDED"]
    if d < ABI["CREASE_COS"]:
        return ABI["CLASS_TERRAIN_RELIEF"]
    return ABI["CLASS_SMOOTH_CANDIDATE"]


def _terrain_shoreline_present(x, z, tri, dims, heights, origin_xz, cell, water_level):
    W, H = dims
    P = _terrain_points(heights, dims, origin_xz, cell)
    corners = ((x, z), (x + 1, z), (x + 1, z + 1), (x, z + 1))
    indices = (0, 1, 2) if tri == 0 else (0, 2, 3)
    pts = [P(*corners[i]) for i in indices]
    return sum((pts[k][1] >= water_level) != (pts[(k + 1) % 3][1] >= water_level)
               for k in range(3)) == 2


def terrain_class_grid(heights, dims, origin_xz, cell, water_level, materials=None):
    """Bake uint16 edge classes and shoreline bits at grid vertices."""
    W, H, heights, materials = _terrain_validate(heights, dims, materials)
    origin_xz, cell, water_level = _terrain_metric_facts(origin_xz, cell, water_level)
    dims = (W, H)
    tri_normals = _terrain_triangle_normals(heights, dims, origin_xz, cell)
    grid = [0] * ((W + 1) * (H + 1))
    counts = {}
    for z in range(H + 1):
        for x in range(W + 1):
            value = 0
            for orientation, shift in ((TERRAIN_EDGE_H, 0), (TERRAIN_EDGE_V, 4), (TERRAIN_EDGE_D, 8)):
                cls = _terrain_edge_class(x, z, orientation, dims, materials, tri_normals)
                value |= int(cls) << shift
                if cls:
                    counts[str(cls)] = counts.get(str(cls), 0) + 1
            if z < H and x < W:
                for tri, bit in ((0, TERRAIN_SHORELINE_BIT_TRI0), (1, TERRAIN_SHORELINE_BIT_TRI1)):
                    if _terrain_shoreline_present(x, z, tri, dims, heights, origin_xz, cell, water_level):
                        value |= bit
                        key = str(ABI["CLASS_SHORELINE"])
                        counts[key] = counts.get(key, 0) + 1
            grid[z * (W + 1) + x] = value
    return grid, counts, tri_normals


def terrain_reference_edges(heights, dims, origin_xz, cell, water_level, materials=None):
    """Reconstruct implicit edges on demand for offline topology tests."""
    W, H, heights, materials = _terrain_validate(heights, dims, materials)
    origin_xz, cell, water_level = _terrain_metric_facts(origin_xz, cell, water_level)
    grid, _counts, tri_normals = terrain_class_grid(
        heights, (W, H), origin_xz, cell, water_level, materials)
    vertex_normals = _terrain_vertex_normals(heights, (W, H), origin_xz, cell, tri_normals)
    P = _terrain_points(heights, (W, H), origin_xz, cell)
    digest = terrain_content_hash(heights, (W, H), origin_xz, cell, water_level, materials)
    records = []
    for z in range(H + 1):
        for x in range(W + 1):
            value = grid[z * (W + 1) + x]
            for orientation, shift, delta in ((TERRAIN_EDGE_H, 0, (1, 0)),
                                               (TERRAIN_EDGE_V, 4, (0, 1)),
                                               (TERRAIN_EDGE_D, 8, (1, 1))):
                cls = (value >> shift) & 0xF
                if not cls:
                    continue
                qx, qz = x + delta[0], z + delta[1]
                adjacent = _terrain_edge_adjacency(x, z, orientation, (W, H))
                n0 = tri_normals[adjacent[0]]
                n1 = tri_normals[adjacent[1]] if adjacent[1] is not None else n0
                records.append({"address": terrain_edge_address(x, z, orientation, (W, H)),
                                "x": x, "z": z, "orientation": orientation, "class": cls,
                                "p0": P(x, z), "p1": P(qx, qz), "face0": n0, "face1": n1,
                                "vertex0": vertex_normals[z * (W + 1) + x],
                                "vertex1": vertex_normals[qz * (W + 1) + qx],
                                "stable_id": terrain_stable_id(digest, terrain_edge_address(x, z, orientation, (W, H)))})
    for z in range(H):
        for x in range(W):
            value = grid[z * (W + 1) + x]
            for tri, bit in ((0, TERRAIN_SHORELINE_BIT_TRI0), (1, TERRAIN_SHORELINE_BIT_TRI1)):
                if value & bit:
                    address = terrain_shoreline_address(x, z, tri, (W, H))
                    records.append({"address": address, "x": x, "z": z, "triangle": tri,
                                    "class": ABI["CLASS_SHORELINE"], "stable_id": terrain_stable_id(digest, address)})
    return records


def _terrain_node_tree(heights, dims, origin_xz, cell, tri_normals, vertex_normals, classes, leaf):
    W, H = dims
    chunks = max((max(W, H) + leaf - 1) // leaf, 1)
    side = 1
    while side < chunks:
        side *= 2

    def region(lx, lz, span, level):
        x0, z0 = lx * span * leaf, lz * span * leaf
        x1, z1 = min(x0 + span * leaf, W), min(z0 + span * leaf, H)
        if x0 >= W or z0 >= H:
            return None
        n = Node()
        n.level, n.children, n.feats = level, [], []
        n.region = (x0, z0, x1, z1)
        if span > 1:
            half = span // 2
            for child in range(4):
                c = region(lx * 2 + (child & 1), lz * 2 + (child >> 1), half, level + 1)
                if c is not None:
                    n.children.append(c)
        cols = W + 1
        # An owned H/V edge is incident to a neighbouring cell's triangle.
        # Its visible surface anchor can lie outside the owned-cell rectangle.
        # Retain one conservative incident-cell halo in the geometric support,
        # without changing the cell/edge ownership encoded in n.region.
        sx0, sz0 = max(x0 - 1, 0), max(z0 - 1, 0)
        sx1, sz1 = min(x1 + 1, W), min(z1 + 1, H)
        n.height_range = (min(heights[z * cols + x] for z in range(sz0, sz1 + 1) for x in range(sx0, sx1 + 1)),
                          max(heights[z * cols + x] for z in range(sz0, sz1 + 1) for x in range(sx0, sx1 + 1)))
        ox, oz = origin_xz
        n.region_points = [(ox + sx0 * cell, n.height_range[0], oz + sz0 * cell),
                           (ox + sx1 * cell, n.height_range[1], oz + sz1 * cell)]
        n.region_normals = [vertex_normals[z * cols + x] for z in range(sz0, sz1 + 1) for x in range(sx0, sx1 + 1)]
        n.region_normals += [tri_normals[2 * (z * W + x) + tri] for z in range(sz0, sz1) for x in range(sx0, sx1) for tri in (0, 1)]
        fixed = any(((classes[z * cols + x] >> shift) & 0xF) in _TERRAIN_FIXED_CLASSES
                    for z in range(z0, z1 + 1) for x in range(x0, x1 + 1) for shift in (0, 4, 8))
        fixed = fixed or any(classes[z * cols + x] & (TERRAIN_SHORELINE_BIT_TRI0 | TERRAIN_SHORELINE_BIT_TRI1)
                             for z in range(z0, z1) for x in range(x0, x1))
        n.terrain_fixed = fixed
        return n

    return region(0, 0, side, 0)


def _terrain_float_bits(value):
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


def build_terrain_bank(heights, dims, origin_xz, cell, water_level, materials=None):
    """Build a compact implicit terrain bank; explicit features/faces are absent."""
    W, H, heights, materials = _terrain_validate(heights, dims, materials)
    origin_xz, cell, water_level = _terrain_metric_facts(origin_xz, cell, water_level)
    dims = (W, H)
    classes, class_counts, tri_normals = terrain_class_grid(heights, dims, origin_xz, cell, water_level, materials)
    vertex_normals = _terrain_vertex_normals(heights, dims, origin_xz, cell, tri_normals)
    leaf = int(_gd_constants(TERRAIN_VISIBILITY_GD)["LEAF"])
    root = _terrain_node_tree(heights, dims, origin_xz, cell, tri_normals, vertex_normals, classes, leaf)
    nodes = _bfs(root)
    initial_radius = {}
    for node in nodes:
        low, high = node.region_points
        cx, cy, cz = (f32((low[axis] + high[axis]) * 0.5) for axis in range(3))
        # Measure from the stored float32 centre, not the unrounded midpoint.
        half = [max(abs(low[axis] - (cx, cy, cz)[axis]),
                    abs(high[axis] - (cx, cy, cz)[axis])) for axis in range(3)]
        radius = math.sqrt(sum(value * value for value in half))
        initial_radius[id(node)] = radius
        node.sphere = ((cx, cy, cz), radius)
        stored_normals = []
        zero_normal = False
        for normal in node.region_normals:
            if norm(normal) <= 1e-12:
                zero_normal = True
            else:
                stored_normals.append(oct_decode(oct_encode(normal)))
        node.cone = None if zero_normal else _cone(stored_normals)
        node.flags = TERRAIN_NODE_FLAG_IMPLICIT | (ABI["NODE_FLAG_LEAF"] if not node.children else 0)
        if node.terrain_fixed:
            node.flags |= ABI["NODE_FLAG_BYPASS"]
        node.first = node.children[0].index if node.children else 0
        node.count = len(node.children)
    # Inflate inner nodes over finalized child spheres as well as their own
    # vertex box. Differing sphere centres otherwise break containment.
    for node in reversed(nodes):
        centre = node.sphere[0]
        radius = initial_radius[id(node)]
        if node.children:
            radius = max(radius, *(norm(sub(child.sphere[0], centre)) + child.sphere[1]
                                   for child in node.children))
        node.sphere = (centre, _f32_up(radius * (1.0 + 1e-6) +
                                      1e-6 * (1.0 + max(abs(v) for v in centre))))
    node_bytes = bytearray()
    for node in nodes:
        (centre, radius), cone = node.sphere, node.cone
        axis, half = cone if cone else ((0.0, 0.0, 0.0), ABI["CONE_UNDEFINED"])
        x0, z0, x1, z1 = node.region
        lo, hi = node.height_range
        node_bytes += struct.pack("<4f4f4I4I", centre[0], centre[1], centre[2], radius,
                                  axis[0], axis[1], axis[2], half, node.first, node.count,
                                  int(x0) | (int(z0) << 16), int(x1 - x0) | (int(z1 - z0) << 16),
                                  node.flags, node.level, _terrain_float_bits(lo), _terrain_float_bits(hi))
    digest = terrain_content_hash(heights, dims, origin_xz, cell, water_level, materials)
    return ({"content_hash": digest, "kind": "terrain", "encoding": ABI["TERRAIN_ENCODING"],
             "surface_support": ABI["TERRAIN_SUPPORT_ENCODING"],
             "terrain_dims": [W, H], "terrain_origin": [float(origin_xz[0]), float(origin_xz[1])],
             "terrain_cell": float(cell), "water_level": float(water_level), "leaf_cells": leaf,
             "terrain_classes": struct.pack("<%dH" % len(classes), *classes),
             "terrain_class_count": len(classes), "nodes": bytes(node_bytes), "features": b"", "faces": b"",
             "node_count": len(nodes), "feature_count": 0, "face_count": 0, "chain_count": 0,
             "class_counts": class_counts, "zero_normal_features": 0, "max_leaf_features": 0,
             "depth": max(node.level for node in nodes)}, root)


# --------------------------------------------------------------- world planes
# FL-4907 WATER_CLOUD_RETAINED_HIERARCHY_20261002: the sea/inland water
# surface and the cloud layer are retained hierarchical sources, built here
# once per terrain digest and uploaded once. Layout: TrueFFFeatureABI.gd
# (NODE_FLAG_PLANE). No per-frame CPU tile enumeration exists at runtime.
WATER_FEATURES_GD = PROJ / "render/ascii/features/TrueFFWaterFeatures.gd"
SKY_FEATURES_GD = PROJ / "render/ascii/features/TrueFFSkyFeatures.gd"
WORLD_PLANES = ("water", "cloud")


def world_plane_content_hash(plane: str, terrain_hash: str) -> str:
    """Mirror of TrueFFFeatureBank.world_plane_content_hash."""
    return hashlib.sha256(f"{ABI['PLANE_ENCODING']}:{plane}:{terrain_hash}".encode()).hexdigest()


def world_plane_spec(plane: str, water_level: float) -> dict:
    """Cell side, sheet height and stroke identity from their one GD owner."""
    if plane == "water":
        owner = _gd_constants(WATER_FEATURES_GD)
        return {"cell": float(ABI["WATER_CREST_CELL"]), "y": float(water_level),
                "role": int(owner["SURFACE_ROLE"]), "material": int(owner["WATER_MATERIAL"])}
    if plane == "cloud":
        owner = _gd_constants(SKY_FEATURES_GD)
        return {"cell": float(owner["CLOUD_CELL"]), "y": float(owner["CLOUD_Y"]),
                "role": int(owner["CLOUD_ROLE"]), "material": int(owner["CLOUD_MATERIAL"])}
    raise ValueError(f"unknown world plane {plane!r}")


def _plane_wet_prefix(heights, dims, materials, water_level):
    """Inclusive 2D prefix count of possibly-wet authored cells.

    Conservative superset of the live GPU test (material 140, or the sample's
    interpolated terrain height <= sea): a cell is possibly wet when it is
    material 140 or its lowest corner is at or below the water plane.
    """
    W, H = dims
    cols = W + 1
    prefix = [[0] * (W + 1) for _ in range(H + 1)]
    for z in range(H):
        row, above, run = prefix[z + 1], prefix[z], 0
        for x in range(W):
            low = min(heights[z * cols + x], heights[z * cols + x + 1],
                      heights[(z + 1) * cols + x], heights[(z + 1) * cols + x + 1])
            wet = (materials is not None and materials[z * W + x] == 140) or low <= water_level
            run += 1 if wet else 0
            row[x + 1] = above[x + 1] + run
    return prefix


def build_world_plane_bank(plane, terrain_hash, heights, dims, origin_xz, cell, water_level, materials=None):
    """Bounded hierarchical world-plane bank (no per-cell records)."""
    spec = world_plane_spec(plane, water_level)
    W, H = int(dims[0]), int(dims[1])
    leaf_cells = int(ABI["PLANE_LEAF_CELLS"])
    if 2 * leaf_cells * leaf_cells != ABI["PLANE_LEAF_FEATURES"]:
        raise ValueError("PLANE_LEAF_FEATURES must equal two addresses per leaf cell")
    leaf_min = spec["cell"] * leaf_cells
    extent = float(ABI["PLANE_ROOT_EXTENT"])
    ratio = extent / leaf_min
    if ratio < 1 or ratio != 2 ** int(round(math.log2(ratio))):
        raise ValueError(f"{plane}: root extent must be a power-of-two multiple of the finest leaf")
    ratio_k = float(ABI["PLANE_SUBDIVISION"])
    mx0, mz0 = float(origin_xz[0]), float(origin_xz[1])
    mx1, mz1 = mx0 + W * float(cell), mz0 + H * float(cell)
    rx = math.floor(((mx0 + mx1) * 0.5 - extent * 0.5) / leaf_min) * leaf_min
    rz = math.floor(((mz0 + mz1) * 0.5 - extent * 0.5) / leaf_min) * leaf_min
    prefix = _plane_wet_prefix(heights, (W, H), materials, water_level) if plane == "water" else None

    def present(ox, oz, size):
        if prefix is None:
            return True
        # Outside the authored rectangle the backstop owns water.
        if ox < mx0 or oz < mz0 or ox + size > mx1 or oz + size > mz1:
            return True
        x0, z0 = int(math.floor((ox - mx0) / cell)), int(math.floor((oz - mz0) / cell))
        x1, z1 = int(math.ceil((ox + size - mx0) / cell)), int(math.ceil((oz + size - mz0) / cell))
        return prefix[z1][x1] - prefix[z0][x1] - prefix[z1][x0] + prefix[z0][x0] > 0

    def distance(ox, oz, size):
        dx = max(mx0 - (ox + size), ox - mx1, 0.0)
        dz = max(mz0 - (oz + size), oz - mz1, 0.0)
        return max(dx, dz)

    def region(ox, oz, size, level):
        if not present(ox, oz, size):
            return None
        n = Node()
        n.level, n.children, n.feats = level, [], []
        n.region = (ox, oz, size)
        leaf = size <= leaf_min or distance(ox, oz, size) >= ratio_k * size or level >= MAX_DEPTH
        if not leaf:
            half = size * 0.5
            for child in range(4):
                c = region(ox + half * (child & 1), oz + half * (child >> 1), half, level + 1)
                if c is not None:
                    n.children.append(c)
            if not n.children:
                return None
        return n

    root = region(rx, rz, extent, 0)
    if root is None:
        raise ValueError(f"{plane}: world plane domain has no source cells")
    nodes = _bfs(root)
    # Depth-first leaf order: every subtree owns one contiguous address range.
    leaves = []

    def order(n):
        if not n.children:
            n.first = len(leaves) * ABI["PLANE_LEAF_FEATURES"]
            leaves.append(n)
            n.count = ABI["PLANE_LEAF_FEATURES"]
            return
        for child in n.children:
            order(child)
        n.first = n.children[0].first
        n.count = sum(child.count for child in n.children)

    order(root)
    digest = world_plane_content_hash(plane, terrain_hash)
    node_bytes = bytearray()
    for n in nodes:
        ox, oz, size = n.region
        centre = (f32(ox + size * 0.5), 0.0, f32(oz + size * 0.5))
        if centre[0] != ox + size * 0.5 or centre[2] != oz + size * 0.5 or f32(ox) != ox or f32(oz) != oz:
            raise ValueError(f"{plane}: node origin/centre is not exact in float32")
        # The crest and cloud level sets lie IN the sheet (zero animated
        # displacement), so the cell rectangle bounds every live segment.
        radius = _f32_up(size * math.sqrt(0.5) * (1.0 + 1e-6))
        flags = ABI["NODE_FLAG_PLANE"] | (ABI["NODE_FLAG_LEAF"] if not n.children else 0)
        node_bytes += struct.pack("<4f4f4I4I", centre[0], 0.0, centre[2], radius,
                                  0.0, 1.0, 0.0, ABI["CONE_UNDEFINED"],
                                  n.children[0].index if n.children else 0, len(n.children),
                                  n.first, n.count, flags, n.level,
                                  _terrain_float_bits(ox), _terrain_float_bits(oz))
    seed = fnv1a32(bytes.fromhex(digest))
    up = oct_encode((0.0, 1.0, 0.0))
    stroke_flags = (spec["material"] & ABI["FEATURE_FLAG_MATERIAL_MASK"]) | ABI["FEATURE_FLAG_OPEN"] \
        | ((spec["role"] & 63) << 17)
    record_bytes = bytearray()
    for n in leaves:
        ox, oz, size = n.region
        record_bytes += struct.pack("<4f4f4I4I", ox, 0.0, oz, size / leaf_cells,
                                    ox + size, 0.0, oz + size, 0.0, up, up, up, up,
                                    ABI["CLASS_AUTHORED_STROKE"], n.index, seed, stroke_flags)
    return {"content_hash": digest, "kind": "world_plane", "encoding": ABI["PLANE_ENCODING"],
            "plane": plane, "plane_y": spec["y"], "plane_cell": spec["cell"],
            "plane_domain": [rx, rz, extent], "plane_terrain_hash": terrain_hash,
            "plane_max_displacement": 0.0, "leaf_cells": leaf_cells,
            "nodes": bytes(node_bytes), "features": bytes(record_bytes), "faces": b"",
            "node_count": len(nodes), "feature_count": len(leaves), "face_count": 0, "chain_count": 0,
            "class_counts": {str(ABI["CLASS_AUTHORED_STROKE"]): len(leaves) * ABI["PLANE_LEAF_FEATURES"]},
            "zero_normal_features": 0, "max_leaf_features": ABI["PLANE_LEAF_FEATURES"],
            "depth": max(n.level for n in nodes)}



# --------------------------------------------------------------- file
def encode_file(bank: dict, sources: list) -> bytes:
    header = {
        "schema": ABI["SCHEMA"],
        "content_hash": bank["content_hash"],
        "sources": sorted(sources),
        "node_count": bank["node_count"],
        "feature_count": bank["feature_count"],
        "face_count": bank["face_count"],
        "chain_count": bank["chain_count"],
        "class_counts": bank["class_counts"],
        "zero_normal_features": bank["zero_normal_features"],
        "max_leaf_features": bank["max_leaf_features"],
        "depth": bank["depth"],
        "crease_cos": ABI["CREASE_COS"],
        "leaf_max_features": ABI["LEAF_MAX_FEATURES"],
        "sections": {},
    }
    for k in ("kind", "encoding", "mesh_edge_count", "triangle_count", "source_face_count", "terrain_dims",
              "terrain_origin", "terrain_cell", "water_level", "leaf_cells", "terrain_class_count",
              "actor_feature_identity", "canonical_address_signature", "address_layout_version",
              "actor_kind", "topology_signature", "pose_payload_hash", "primitive_encoding", "surface_support",
              "plane", "plane_y", "plane_cell", "plane_domain", "plane_terrain_hash", "plane_max_displacement"):
        if k in bank:
            header[k] = bank[k]

    def pad16(n):
        return (n + 15) & ~15

    section_items = [("nodes", ABI["NODE_BYTES"]), ("features", ABI["FEATURE_BYTES"]),
                     ("faces", ABI["FACE_BYTES"])]
    if "terrain_classes" in bank:
        section_items.append(("terrain_classes", 2))
    if "primitive_refs" in bank:
        section_items.append(("primitive_refs", ABI["PRIMITIVE_REF_BYTES"]))
    # Section offsets depend on header length; iterate to a fixed point.
    hlen = 0
    for _ in range(4):
        off = pad16(16 + hlen)
        for name, stride in section_items:
            header["sections"][name] = {"offset": off, "bytes": len(bank[name]), "stride": stride}
            off = pad16(off + len(bank[name]))
        text = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        new_len = pad16(len(text) + 16) - 16
        if new_len == hlen:
            break
        hlen = new_len
    text = text.ljust(hlen, b" ")
    out = bytearray(ABI["FILE_MAGIC"].encode() + struct.pack("<II", ABI["FILE_VERSION"], hlen) + text)
    for name, _stride in section_items:
        out += b"\0" * (header["sections"][name]["offset"] - len(out))
        out += bank[name]
    return bytes(out)


def decode_file(data: bytes) -> dict:
    assert data[:8] == ABI["FILE_MAGIC"].encode(), "bad magic"
    version, hlen = struct.unpack_from("<II", data, 8)
    assert version == ABI["FILE_VERSION"]
    header = json.loads(data[16:16 + hlen])
    out = {"header": header}
    for name, sec in header["sections"].items():
        out[name] = data[sec["offset"]:sec["offset"] + sec["bytes"]]
    return out


def _decoded_feature_records(decoded):
    """Decode feature endpoints/classes for the refit support path."""
    records = []
    raw = decoded.get("features", b"")
    if len(raw) % ABI["FEATURE_BYTES"]:
        raise ValueError("feature section is not aligned to the ABI stride")
    for index, values in enumerate(struct.iter_unpack("<4f4f4I4I", raw)):
        feature = Feature()
        feature.local = index
        feature.p0 = tuple(values[0:3])
        feature.p1 = tuple(values[4:7])
        feature.cls = int(values[12])
        records.append(feature)
    return records


def _decoded_node_tree(decoded):
    """Reconstruct the retained BFS node tree without changing its records."""
    raw = decoded.get("nodes", b"")
    if len(raw) % ABI["NODE_BYTES"]:
        raise ValueError("node section is not aligned to the ABI stride")
    rows = list(struct.iter_unpack("<4f4f4I4I", raw))
    if not rows:
        raise ValueError("explicit bank has no nodes")
    nodes = []
    for index, row in enumerate(rows):
        node = Node()
        node.index = index
        node.level = int(row[13])
        node.first = int(row[10])
        node.count = int(row[11])
        node.children = []
        nodes.append(node)
    for index, row in enumerate(rows):
        first, count = int(row[8]), int(row[9])
        if count:
            if first <= index or first + count > len(nodes):
                raise ValueError(f"node {index} child range is outside the node section")
            nodes[index].children = nodes[first:first + count]
    parents = [0] * len(nodes)
    for node in nodes:
        for child in node.children:
            parents[child.index] += 1
        if node.children:
            cursor = node.first
            for child in node.children:
                if child.first != cursor:
                    raise ValueError("child feature ranges do not partition their parent")
                cursor += child.count
            if cursor != node.first + node.count:
                raise ValueError("child feature ranges do not partition their parent")
    if parents != [0] + [1] * (len(nodes) - 1):
        raise ValueError("node tree contains shared or unreachable children")
    return nodes[0], nodes, rows


def refit_explicit_bounds(data: bytes) -> tuple[bytes, dict]:
    """Refit only explicit-mesh node spheres from retained bank records.

    The returned bytes are identical for implicit terrain banks.  For an
    explicit bank, feature records and face topology are decoded, the shared
    support and sphere helpers are applied, and only the first 16 bytes of
    each node record are rewritten.
    """
    decoded = decode_file(data)
    header = decoded["header"]
    if header.get("kind") == "terrain" or header.get("encoding") == "implicit_grid_v1":
        return data, {"kind": "terrain", "changed": False, "node_count": 0}

    header_end = 16 + struct.unpack_from("<I", data, 12)[0]
    intervals = []
    for name, count_name, stride in (("nodes", "node_count", ABI["NODE_BYTES"]),
                                     ("features", "feature_count", ABI["FEATURE_BYTES"]),
                                     ("faces", "face_count", ABI["FACE_BYTES"])):
        section = header["sections"][name]
        offset, size = int(section["offset"]), int(section["bytes"])
        count = int(header[count_name])
        if (offset < header_end or offset % 16 or size < 0 or count < 0
                or int(section["stride"]) != stride or size != count * stride
                or offset + size > len(data)):
            raise ValueError(f"invalid {name} section extent or stride")
        if size:
            intervals.append((offset, offset + size))
    intervals.sort()
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise ValueError("bank sections overlap")

    features = _decoded_feature_records(decoded)
    faces_raw = decoded.get("faces", b"")
    if len(faces_raw) % ABI["FACE_BYTES"]:
        raise ValueError("face section is not aligned to the ABI stride")
    tri_edges = [tuple(int(value) for value in row[:3])
                 for row in struct.iter_unpack("<4I", faces_raw)]
    declared_features = int(header.get("feature_count", len(features)))
    declared_faces = int(header.get("face_count", len(tri_edges)))
    if declared_features != len(features) or declared_faces != len(tri_edges):
        raise ValueError("explicit bank record counts do not match its header")
    supports = feature_support_points(features, tri_edges)
    support_by_index = {index: points for index, points in enumerate(supports)}
    root, nodes, _rows = _decoded_node_tree(decoded)
    if int(header.get("node_count", -1)) != len(nodes):
        raise ValueError("node count does not match its header")
    if root.first != 0 or root.count != len(features):
        raise ValueError("root feature range does not cover the feature section")
    for node in nodes:
        if node.count <= 0 or node.first + node.count > len(features):
            raise ValueError("node feature range is outside the feature section")

    def points_of(node):
        return [point for index in range(node.first, node.first + node.count)
                for point in support_by_index[index]]

    _finish_spheres(root, points_of)
    out = bytearray(data)
    node_offset = int(header["sections"]["nodes"]["offset"])
    for index, node in enumerate(nodes):
        centre, radius = node.sphere
        struct.pack_into("<4f", out, node_offset + index * ABI["NODE_BYTES"],
                         centre[0], centre[1], centre[2], radius)
    result = bytes(out)
    return result, {"kind": "explicit", "changed": result != data,
                    "node_count": len(nodes), "feature_count": len(features),
                    "face_count": len(tri_edges)}


# --------------------------------------------------------------- sources
def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def faces_sources(paths):
    for path in paths:
        raw = path.read_bytes()
        doc = json.loads(raw)
        rows, fc = doc.get("rows"), int(doc.get("face_count", 0) or 0)
        if not isinstance(rows, list) or fc <= 0 or len(rows) != fc * FLOATS_PER_FACE:
            print(f"skip {_rel(path)}: no 16-float face rows", file=sys.stderr)
            continue
        rows32 = [f32(x) for x in rows]
        # Welding and geometry use the JSON doubles (bake convention); the
        # content digest uses the float32 rows the runtime registers.
        yield {"rows": rows, "rows32": rows32, "face_count": fc,
               "source": _rel(path)}


def catalog_sources(paths):
    for path in paths:
        cat = json.loads(path.read_text(encoding="utf-8"))
        payload = ROOT / cat["header"]["asset_payload"]["path"]
        data = payload.read_bytes()
        assert data[:8] == CATALOG_MAGIC, payload
        for rec in cat["asset_records"]:
            fc = int(rec["face_count"])
            raw = data[rec["rows_offset"]:rec["rows_offset"] + rec["rows_length"]]
            rows = list(struct.unpack("<%df" % (len(raw) // 4), raw))
            assert len(rows) == fc * FLOATS_PER_FACE
            yield {"rows": rows, "rows32": rows, "face_count": fc,
                   "source": f"{_rel(path)}#{rec['content_hash']}", "expect_hash": rec["content_hash"]}


LANDMARK_MANIFEST = WORLD_MESH / "authored_landmarks.json"
WORLD_GD = PROJ / "scripts/World.gd"


def _box_face_rows(center, size, mat_id: int, shade: float) -> list:
    """One identity-basis box in World._append_static_world_box order and
    float32 Vector3 arithmetic: corners are ((c +/- hx) +/- hy) +/- hz with a
    float32 rounding after every operation, so the rows (and the digest) are
    bit-identical to the SceneGeometryDB asset World registers."""
    c = [f32(v) for v in center]
    half = [f32(f32(v) * 0.5) for v in size]

    def corner(sx, sy, sz):
        out = []
        for axis in range(3):
            value = c[axis]
            for sign, h_axis in ((sx, 0), (sy, 1), (sz, 2)):
                h = half[h_axis] if axis == h_axis else 0.0
                value = f32(value + h) if sign > 0 else f32(value - h)
            out.append(value)
        return out

    p = {(sx, sy, sz): corner(sx, sy, sz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)}
    quads = (((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1)),
             ((1, -1, 1), (-1, -1, 1), (-1, 1, 1), (1, 1, 1)),
             ((-1, -1, 1), (-1, -1, -1), (-1, 1, -1), (-1, 1, 1)),
             ((1, -1, -1), (1, -1, 1), (1, 1, 1), (1, 1, -1)),
             ((-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1)),
             ((-1, -1, 1), (1, -1, 1), (1, -1, -1), (-1, -1, -1)))
    rows = []
    for quad in quads:
        for k, w in zip(quad, (f32(mat_id), f32(shade), 0.0, 0.0)):
            rows.extend(p[k] + [w])
    return rows


def landmark_box_sources(manifest: Path = LANDMARK_MANIFEST):
    """FL-4907: box-list placements in authored_landmarks.json. World publishes
    each as SceneGeometryDB asset `<id>` with these local rows (instance origin
    = terrain/base_y anchor), so their banks must be compiled from the manifest
    exactly as mesh placements are compiled from their *_faces.json."""
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    mat_id = int(_gd_constants(WORLD_GD)["STATIC_WORLD_CYAN_PEDESTAL_MAT"])
    for pid, placement in sorted(doc.get("placements", {}).items()):
        if "boxes" not in placement:
            continue
        rows = []
        for box in placement["boxes"]:
            rows.extend(_box_face_rows(box["center"], box["size"], mat_id, float(box["shade"])))
        fc = len(rows) // FLOATS_PER_FACE
        yield {"rows": rows, "rows32": rows, "face_count": fc, "source": f"{_rel(manifest)}#{pid}"}


def build_source(src):
    content_hash = content_hash_of(src["rows32"], src["face_count"])
    if src.get("expect_hash") and src["expect_hash"] != content_hash:
        raise SystemExit(f"{src['source']}: content hash mismatch")
    # build_mesh_bank digests the rows as float32, the runtime's digest.
    bank = build_mesh_bank(src["rows"], src["face_count"])
    assert bank["content_hash"] == content_hash
    return bank


def default_inputs():
    faces = sorted(WORLD_MESH.glob("*_faces.json"))
    catalogs = sorted(WORLD_PARTITION.glob("*fl4789_chunk_catalog.v1.json"))
    return faces, catalogs


def _emit_world_planes(files, index, source, terrain_hash, doc, dims, origin, cell, water_level):
    for plane in WORLD_PLANES:
        plane_bank = build_world_plane_bank(plane, terrain_hash, doc["heights"], dims, origin, cell,
                                            water_level, doc.get("materials"))
        ph = plane_bank["content_hash"]
        if ph in files:
            raise SystemExit(f"{source}: world plane digest collides with bank {ph}")
        files[ph] = plane_bank
        index[ph] = {"file": f"{ph}.v1.bin", "kind": "world_plane", "sources": [source],
                     "encoding": plane_bank["encoding"], "plane": plane,
                     "plane_terrain_hash": terrain_hash, "leaf_cells": plane_bank["leaf_cells"],
                     "node_count": plane_bank["node_count"],
                     "feature_count": plane_bank["feature_count"], "face_count": 0}


# FL-4907 BANK_INVENTORY_PRUNE_20261002: a retained bank belongs to one source
# family. A build that enumerates a family owns that family's inventory.
ACTOR_PRODUCT_SOURCE = re.compile(r"assets/actor_mesh/[^/#]+_rig_poses\.json$")
RIGID_SOURCE_MARKS = ("#rigid-part=", "#shard=", "#boat_v1")


def _bank_family(entry) -> str:
    if entry.get("kind") in ("terrain", "world_plane"):
        return "terrain"
    sources = entry.get("sources", [])
    if any(ACTOR_PRODUCT_SOURCE.search(source) for source in sources):
        return "actor"
    if any(mark in source for source in sources for mark in RIGID_SOURCE_MARKS):
        return "rigid"
    return "static"


def prune_inventory(index, produced, run_sources, families):
    """Digests of retained banks that no current source emits.

    A bank is stale when every source file it names is gone, or when this run
    enumerated its family and every source it names (for actor banks: the
    family's whole product list) without emitting its digest. A family this
    run did not enumerate (e.g. terrain without --terrain-map) is kept as is.
    """
    covered = {source.split("#", 1)[0] for source in run_sources}
    stale = []
    for digest, entry in sorted(index.items()):
        if digest in produced:
            continue
        paths = [source.split("#", 1)[0] for source in entry.get("sources", [])]
        missing = not any((ROOT / path).exists() for path in paths)
        family = _bank_family(entry)
        enumerated = family in families and (family == "actor" or all(path in covered for path in paths))
        if missing or enumerated:
            stale.append(digest)
    return stale


def cmd_build(args) -> int:
    faces = [Path(p) for p in args.faces]
    catalogs = [Path(p) for p in args.catalog]
    if args.all:
        faces, catalogs = default_inputs()
    out = Path(args.out)
    files, index = {}, {}
    if getattr(args, "retain_unselected", False):
        inventory_path = out / "index.v1.json"
        if inventory_path.exists():
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            if inventory.get("schema") != ABI["SCHEMA"] or not isinstance(inventory.get("banks"), dict):
                raise SystemExit(f"{_rel(inventory_path)}: invalid retained bank inventory")
            index.update(inventory["banks"])
    sources = list(faces_sources(faces)) + list(catalog_sources(catalogs))
    if args.all:
        sources.extend(landmark_box_sources())
    if getattr(args, "rigid", False):
        sources.extend(_extension("true_ff_rigid_features").rigid_sources(PROJ))
    for src in sources:
        h = content_hash_of(src["rows32"], src["face_count"])
        if h in files:
            index[h]["sources"].append(src["source"])
            continue
        bank = build_source(src)
        files[h] = bank
        index[h] = {"file": f"{h}.v1.bin", "sources": [src["source"]],
                    "node_count": bank["node_count"], "feature_count": bank["feature_count"],
                    "face_count": bank["face_count"]}
    terrain_sources = [(Path(p), json.loads(Path(p).read_text(encoding="utf-8")))
                       for p in getattr(args, "terrain_heights", [])]
    terrain_maps = list(getattr(args, "terrain_map", []))
    if args.all and terrain_maps and not getattr(args, "world_planes_only", False):
        # FL-4876 PUBLISH-01: --all means every installed source. Each chunk
        # catalog names its own source map, and that map's terrain is a
        # source of the same publication: without its bank the runtime omits
        # the terrain every frame (feature_bank_missing) and re-prepares on
        # source_residency_changed, so no frame of the map ever presents.
        # Before this only the one --terrain-map (Y8) was built, so the SBU and
        # Numabe maps had no terrain bank at all.
        known = {Path(p).resolve() for p in terrain_maps}
        for catalog_path in catalogs:
            header = json.loads(Path(catalog_path).read_text(encoding="utf-8")).get("header", {})
            rel = header.get("source_map_path")
            if not rel:
                continue
            map_path = (ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
            if map_path.is_file() and map_path.resolve() not in known:
                known.add(map_path.resolve())
                terrain_maps.append(str(map_path))
    for path in terrain_maps:
        terrain_sources.append((Path(path), _extension("true_ff_terrain_sources").map_source(
            path, SimpleNamespace(**globals()))))
    for terrain_path, doc in terrain_sources:
        required = ("heights", "dims", "origin", "cell", "water_level")
        missing = [name for name in required if name not in doc]
        if missing:
            raise SystemExit(f"{_rel(terrain_path)}: missing terrain fields {', '.join(missing)}")
        # The source record must provide the water plane explicitly.  Do not
        # substitute the world's current default here: another map importer
        # may own a different plane, and the digest must describe that fact.
        water_level = float(doc["water_level"])
        dims = tuple(int(value) for value in doc["dims"])
        origin = tuple(float(value) for value in doc["origin"])
        cell = float(doc["cell"])
        source = _rel(terrain_path)
        if getattr(args, "world_planes_only", False):
            # Rebuild only this map's retained water/cloud planes; the terrain
            # bank file and its index entry stay as published (--retain-unselected).
            h = terrain_content_hash(doc["heights"], dims, origin, cell, water_level, doc.get("materials"))
            if index.get(h, {}).get("kind") != "terrain":
                raise SystemExit(f"{source}: terrain bank {h} is not in the retained inventory")
            _emit_world_planes(files, index, source, h, doc, dims, origin, cell, water_level)
            continue
        bank, _ = build_terrain_bank(doc["heights"], dims, origin, cell, water_level, doc.get("materials"))
        h = bank["content_hash"]
        if h in files:
            if index[h].get("kind") != "terrain":
                raise SystemExit(f"{source}: content digest collides with static bank {h}")
            index[h]["sources"].append(source)
            continue
        files[h] = bank
        index[h] = {"file": f"{h}.v1.bin", "kind": "terrain", "sources": [source],
                    "encoding": bank["encoding"], "leaf_cells": bank["leaf_cells"],
                    "surface_support": bank["surface_support"],
                    "node_count": bank["node_count"], "feature_count": bank["feature_count"],
                    "face_count": bank["face_count"], "terrain_dims": bank["terrain_dims"],
                    "terrain_origin": bank["terrain_origin"], "terrain_cell": bank["terrain_cell"],
                    "water_level": bank["water_level"], "terrain_class_count": bank["terrain_class_count"]}
        # The retained water surface and cloud layer of this map: bounded
        # implicit hierarchies keyed by the terrain digest (WORLD_PLANES).
        _emit_world_planes(files, index, source, h, doc, dims, origin, cell, water_level)
    actor_manifest = None
    if getattr(args, "actor_product", []):
        actors = _extension("true_ff_actor_features").compile_actor_feature_sources(
            args.actor_product, SimpleNamespace(**globals()))
        actor_manifest = actors["manifest"]
        if actor_manifest["rejections"]:
            raise SystemExit(f"actor feature compilation rejected: {actor_manifest['rejections']}")
        for digest, actor_bank in actors["banks"].items():
            files[digest] = actor_bank
            index[digest] = {"file": f"{digest}.v1.bin", "sources": list(args.actor_product),
                             "node_count": actor_bank["node_count"], "feature_count": actor_bank["feature_count"],
                             "face_count": actor_bank["face_count"]}
    pruned_files = []
    if getattr(args, "prune", False):
        if not getattr(args, "retain_unselected", False):
            raise SystemExit("--prune edits the retained inventory; pass --retain-unselected")
        families = set()
        if faces or catalogs:
            families.add("static")
        if getattr(args, "rigid", False):
            families.add("rigid")
        if getattr(args, "actor_product", []):
            families.add("actor")
        if terrain_sources:
            families.add("terrain")
        run_sources = [src["source"] for src in sources] + [_rel(path) for path, _ in terrain_sources] \
            + list(getattr(args, "actor_product", []))
        for digest in prune_inventory(index, set(files), run_sources, families):
            pruned_files.append(out / index.pop(digest)["file"])
        freed = sum(path.stat().st_size for path in pruned_files if path.exists())
        print(f"pruned={len(pruned_files)} bytes_freed={freed} families={','.join(sorted(families))}")
    blobs = {f"{h}.v1.bin": encode_file(b, index[h]["sources"]) for h, b in files.items()}
    blobs["index.v1.json"] = (json.dumps({"schema": ABI["SCHEMA"], "banks": index},
                                         sort_keys=True, indent=1) + "\n").encode()
    if actor_manifest is not None:
        blobs["../true_ff_actor_feature_sources.v1.json"] = (
            json.dumps(actor_manifest, sort_keys=True, indent=2) + "\n").encode()
    bad = 0
    if not args.check:
        out.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(blobs.items()):
        target = out / name
        if args.check:
            if not target.exists() or target.read_bytes() != data:
                print(f"DIFF {_rel(target)}")
                bad += 1
        else:
            target.write_bytes(data)
    for path in pruned_files:
        if not path.exists():
            continue
        if args.check:
            print(f"PRUNE {_rel(path)}")
            bad += 1
        else:
            path.unlink()
    print(f"banks={len(files)} sources={len(sources) + len(terrain_sources)} out={_rel(out)}" +
          (f" diffs={bad}" if args.check else ""))
    return 1 if bad else 0


def cmd_terrain(args) -> int:
    doc = json.loads(Path(args.heights).read_text(encoding="utf-8"))
    # No fallback water level: this CLI is a source-fact boundary, so the
    # explicit value is part of the terrain digest and shoreline geometry.
    water_level = float(doc["water_level"])
    bank, _ = build_terrain_bank(doc["heights"], tuple(doc["dims"]), tuple(doc["origin"]),
                                 float(doc["cell"]), water_level, doc.get("materials"))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encode_file(bank, [str(args.heights)]))
    print(f"terrain nodes={bank['node_count']} features={bank['feature_count']}")
    return 0


def cmd_refit_bounds(args) -> int:
    """Check or write sphere-only refits for the authoritative bank inventory."""
    out = Path(args.out)
    raw_paths = list(getattr(args, "file", []))
    if args.all:
        index_path = out / "index.v1.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        raw_paths.extend(out / entry["file"] for entry in index["banks"].values())
    if not raw_paths:
        raise SystemExit("refit-bounds requires --all or at least one --file")

    paths = []
    seen = set()
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_absolute() and not path.exists():
            path = out / path
        path = path.resolve()
        if path not in seen:
            seen.add(path)
            paths.append(path)

    explicit = terrain = changed = diffs = 0
    for path in sorted(paths):
        original = path.read_bytes()
        refit, facts = refit_explicit_bounds(original)
        if facts["kind"] == "terrain":
            terrain += 1
            continue
        explicit += 1
        if refit != original:
            changed += 1
            if args.check:
                print(f"DIFF {_rel(path)}")
                diffs += 1
            else:
                path.write_bytes(refit)
    mode = "check" if args.check else "write"
    print(f"refit-bounds mode={mode} banks={len(paths)} explicit={explicit} "
          f"terrain_skipped={terrain} changed={changed} diffs={diffs} out={_rel(out)}")
    return 1 if diffs else 0


def _extension(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--all", action="store_true")
    b.add_argument("--faces", action="append", default=[])
    b.add_argument("--catalog", action="append", default=[])
    b.add_argument("--rigid", action="store_true", help="include retained aircraft, car and weapon parts")
    b.add_argument("--actor-product", action="append", default=[], help="include a baked actor pose product")
    b.add_argument("--retain-unselected", action="store_true",
                   help="retain existing inventory entries outside this source publication")
    b.add_argument("--terrain-map", action="append", default=[], help="read authoritative centered A3D terrain")
    b.add_argument("--terrain-heights", action="append", default=[],
                   help="authoritative terrain JSON(s) to compile into the bank directory")
    b.add_argument("--world-planes-only", action="store_true",
                   help="with --terrain-map/--terrain-heights: rebuild only the retained water/cloud "
                        "plane banks of an already-published terrain (use with --retain-unselected)")
    b.add_argument("--prune", action="store_true",
                   help="with --retain-unselected: delete retained banks (index entry and .v1.bin) that no "
                        "current source emits, inside each family this run enumerates (FL-4907)")
    b.add_argument("--out", default=str(DEFAULT_OUT))
    b.add_argument("--check", action="store_true", help="compare with files on disk, write nothing")
    t = sp.add_parser("terrain")
    t.add_argument("--heights", required=True)
    t.add_argument("--out", required=True)
    r = sp.add_parser("refit-bounds")
    r.add_argument("--all", action="store_true", help="use the bank directory index as inventory")
    r.add_argument("--file", "--bank", dest="file", action="append", default=[],
                   help="explicit bank file to check or rewrite")
    r.add_argument("--out", default=str(DEFAULT_OUT), help="bank directory containing the inventory")
    r.add_argument("--check", action="store_true", help="compare with refitted bytes, write nothing")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "terrain":
        return cmd_terrain(args)
    return cmd_refit_bounds(args)


if __name__ == "__main__":
    sys.exit(main())
