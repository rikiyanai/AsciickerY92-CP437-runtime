#!/usr/bin/env python3
"""FL-4907 PASSES lane: Python reference of the True FF feature passes P1-P4.

The GLSL compute shaders under godot_project/render/ascii/features/ mirror this
module pass for pass (true_ff_feature_traverse*.glsl, _extract.glsl,
_sites.glsl, _visibility.glsl). Atomic append order on the GPU is not
deterministic, so every output here is compared as a set keyed by stable ids.

ABI (std430, set 0; owner: BANK lane, true_ff_feature_abi.glslinc):
  FeatureNode   64 B  sphere(local centre, radius) cone(axis, half-angle rad,
                      < 0 undefined) links(first_child, child_count,
                      feature_first, feature_count) meta(flags, level, -, -)
                      flags bit0: a never-cone-reject class is present in the
                      subtree; bit1: leaf. first_child and feature_first are
                      relative to the instance's node_base / feature_base.
  FeatureRecord 64 B  p0(local xyz, chain arc) p1(local xyz, chain arc)
                      normals(face0, face1, vert0, vert1: octahedral snorm16x2)
                      ident(class, chain_id, stable_id, flags)
  FaceRecord    16 B  (edge0, edge1, edge2, flags): feature indices relative to
                      the instance's feature_base.
  Instance      128 B local_to_world, (node_base, feature_base, face_base,
                      object_id), (max_displacement, max_normal_deviation,
                      node_count, feature_count), source material slots,
                      retained source-part namespace
                      Edges are in winding order (edge k = corner k -> k+1),
                      so the oriented face normal follows from positions.
  Node feature ranges cover the whole subtree; only leaves (flag bit1) are
  admitted. Faces are grouped by the leaf owning edge0 (meta.z, meta.w).
  EdgeFaces      8 B  PASSES adjacency, parallel to FeatureRecord, built once
                      per bank revision from the FaceRecords (GPU pass
                      'adjacency'): two face indices relative to face_base,
                      NONE when absent, in no particular order.

mini_bank() below is a TEST FIXTURE builder (edges of a triangle mesh in one
octree), not the offline bank; the BANK lane owns the real builder.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field, replace

import numpy as np

NONE = 0xFFFFFFFF
SMOOTH, CREASE, MATERIAL, OPEN_BOUNDARY, TWO_SIDED, AUTHORED, RELIEF, SHORELINE = range(1, 9)
NEVER_CONE_REJECT = {CREASE, MATERIAL, OPEN_BOUNDARY, TWO_SIDED, AUTHORED, RELIEF, SHORELINE}
NODE_BYPASS_PRESENT = 1
NODE_LEAF = 2
LEAF_FEATURES = 32
SITE_FLAG_LOOP = 1
SITE_FLAG_OPEN_CHAIN = 2
DENSITY_MAX_LEVEL = 29


# ---------------------------------------------------------------- utilities
def mix32(h: int) -> int:
    """Deterministic fixture ids; never production contour equality."""
    h &= 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x7FEB352D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x846CA68B) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def oct_encode(n) -> int:
    n = np.asarray(n, float)
    n = n / (abs(n[0]) + abs(n[1]) + abs(n[2]))
    x, y = n[0], n[1]
    if n[2] < 0.0:
        x, y = (1.0 - abs(n[1])) * math.copysign(1.0, n[0]), (1.0 - abs(n[0])) * math.copysign(1.0, n[1])
    to16 = lambda v: int(round(max(-1.0, min(1.0, v)) * 32767.0)) & 0xFFFF
    return to16(x) | (to16(y) << 16)


def oct_decode(u: int) -> np.ndarray:
    """unpackSnorm2x16 + octahedral decode (mirrors tff_oct_decode)."""
    def s16(v):
        v &= 0xFFFF
        v = v - 0x10000 if v & 0x8000 else v
        return max(-1.0, v / 32767.0)
    x, y = s16(u), s16(u >> 16)
    z = 1.0 - abs(x) - abs(y)
    if z < 0.0:
        x, y = (1.0 - abs(y)) * math.copysign(1.0, x), (1.0 - abs(x)) * math.copysign(1.0, y)
    v = np.array([x, y, z])
    return v / np.linalg.norm(v)


def angle_between(a, b) -> float:
    c = float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-30))
    return math.acos(max(-1.0, min(1.0, c)))


# ---------------------------------------------------------------- records
@dataclass
class Node:
    centre: np.ndarray
    radius: float
    axis: np.ndarray
    half_angle: float           # < 0: undefined
    first_child: int = 0
    child_count: int = 0
    feature_first: int = 0
    feature_count: int = 0
    flags: int = 0
    level: int = 0


@dataclass
class Feature:
    p0: np.ndarray
    arc0: float
    p1: np.ndarray
    arc1: float
    normals: tuple               # (face0, face1, vert0, vert1) packed octahedral
    cls: int
    chain_id: int
    stable_id: int
    flags: int = 0


@dataclass
class Face:
    edges: tuple
    flags: int = 0               # material (bits 0..15)


@dataclass
class Instance:
    local_to_world: np.ndarray   # 4x4, column vectors: world = M @ [p, 1]
    node_base: int
    feature_base: int
    face_base: int
    object_id: int
    max_displacement: float = 0.0     # < 0: unbounded
    max_normal_deviation: float = 0.0  # < 0: unbounded
    slot_base: int = 0           # host prefix of feature counts (slot map)
    source_namespace: int = 1    # retained source-part namespace; never a frame slot
    terrain: bool = False        # implicit terrain uses the exact feature address


@dataclass
class Bank:
    nodes: list = field(default_factory=list)
    features: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    edge_faces: list = field(default_factory=list)
    max_level: int = 0


@dataclass
class Camera:
    world_to_clip: np.ndarray
    eye: np.ndarray
    forward: np.ndarray
    orthographic: bool
    width: float
    height: float
    advance: float = 11.0        # native full-width advance, px
    row_pitch: float = 17.0
    quantum: float = 1.0 / 1024.0  # finest arc spacing (local units)

    @property
    def inv_view_proj(self):
        return np.linalg.inv(self.world_to_clip)


def perspective(fov_y_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_y_deg) * 0.5)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = far / (near - far)
    m[2, 3] = near * far / (near - far)
    m[3, 2] = -1.0
    return m


def orthographic(half_h, aspect, near, far):
    m = np.eye(4)
    m[0, 0] = 1.0 / (half_h * aspect)
    m[1, 1] = 1.0 / half_h
    m[2, 2] = 1.0 / (near - far)
    m[2, 3] = near / (near - far)
    return m


def look_at(eye, target, up=(0.0, 1.0, 0.0)):
    eye = np.asarray(eye, float)
    fwd = np.asarray(target, float) - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    u = np.cross(right, fwd)
    view = np.eye(4)
    view[0, :3], view[1, :3], view[2, :3] = right, u, -fwd
    view[:3, 3] = -view[:3, :3] @ eye
    return view, fwd


def make_camera(eye, target, ortho=False, width=640.0, height=480.0, fov=50.0,
                half_h=3.0, advance=11.0, quantum=1.0 / 1024.0):
    view, fwd = look_at(eye, target)
    proj = orthographic(half_h, width / height, 0.1, 100.0) if ortho else \
        perspective(fov, width / height, 0.1, 100.0)
    return Camera(proj @ view, np.asarray(eye, float), fwd, ortho, width, height,
                  advance=advance, quantum=quantum)


# ---------------------------------------------------------------- transforms
def xform_point(inst: Instance, p) -> np.ndarray:
    return (inst.local_to_world @ np.append(p, 1.0))[:3]


def linear_part(inst: Instance) -> np.ndarray:
    return inst.local_to_world[:3, :3]


def instance_metric(inst: Instance):
    """Sphere stretch and normal angular distortion bounds for an affine map."""
    gram = linear_part(inst).T @ linear_part(inst)
    diag = np.diag(gram)
    radii = np.abs(gram).sum(axis=1) - diag
    upper, lower = max(diag + radii), min(diag - radii)
    return math.sqrt(upper), math.sqrt(upper / lower) if lower > 0. else -1.


def xform_cone_angle(half_angle, condition_bound):
    if half_angle < 0. or condition_bound < 0.:
        return -1.
    if half_angle >= math.pi * .5:
        return math.pi
    return 2. * math.atan(condition_bound * math.tan(.5 * half_angle))


def xform_covector(inst: Instance, n) -> np.ndarray:
    return np.linalg.inv(linear_part(inst)).T @ n


def xform_normal(inst: Instance, n) -> np.ndarray:
    v = xform_covector(inst, n)
    return v / max(np.linalg.norm(v), 1e-30)


def clip_of(cam: Camera, p) -> np.ndarray:
    return cam.world_to_clip @ np.append(p, 1.0)


def frustum_planes(m: np.ndarray):
    """Gribb-Hartmann planes (a, b, c, d), inside when a.x + d >= 0, for
    clip -w <= x, y <= w and 0 <= z <= w (Vulkan/Godot RD depth range)."""
    r0, r1, r2, r3 = m[0], m[1], m[2], m[3]
    planes = [r3 + r0, r3 - r0, r3 + r1, r3 - r1, r2, r3 - r2]
    out = []
    for p in planes:
        n = np.linalg.norm(p[:3])
        out.append(p / max(n, 1e-30))
    return out


def sphere_in_frustum(cam: Camera, centre, radius) -> bool:
    for p in frustum_planes(cam.world_to_clip):
        if float(np.dot(p[:3], centre) + p[3]) < -radius:
            return False
    return True


# ---------------------------------------------------------------- P1 facing interval
FRONT, BACK, STRADDLE, UNKNOWN = "front", "back", "straddle", "unknown"


def facing_interval(centre_w, radius_w, axis_w, half_angle, max_disp, max_ndev,
                    cam: Camera, cone_valid=True) -> str:
    """DETAILED_SPEC_FIVE_AREAS_20260930 section 4. UNKNOWN keeps everything."""
    if not cone_valid or half_angle < 0.0 or max_disp < 0.0 or max_ndev < 0.0:
        return UNKNOWN
    alpha = half_angle + max_ndev
    if alpha >= math.pi * 0.5:
        return UNKNOWN
    r = radius_w + max_disp
    if cam.orthographic:
        to_eye = -cam.forward
        beta = 0.0
    else:
        to_eye = cam.eye - centre_w
        dist = float(np.linalg.norm(to_eye))
        if dist <= r:
            return UNKNOWN       # eye inside the inflated sphere
        beta = math.asin(r / dist)
    theta = angle_between(axis_w, to_eye)
    right = math.pi * 0.5
    if theta + alpha + beta < right:
        return FRONT
    if (math.pi - theta) + alpha + beta < right:
        return BACK
    return STRADDLE


def node_world(bank: Bank, inst: Instance, node_index: int):
    n = bank.nodes[inst.node_base + node_index]
    scale, condition_bound = instance_metric(inst)
    centre = xform_point(inst, n.centre)
    radius = n.radius * scale
    axis = xform_normal(inst, n.axis) if n.half_angle >= 0.0 else np.array([0.0, 1.0, 0.0])
    return n, centre, radius, axis, xform_cone_angle(n.half_angle, condition_bound)


# ---------------------------------------------------------------- P1 traverse / admit
@dataclass
class TraverseResult:
    leaves: list          # (instance, node_local, smooth_rejected)
    admitted: list        # (instance, feature_local, authorized level_mask)
    rounds: int
    per_level_counts: list
    demand: list          # conservative local-window bound before admission
    offsets: list          # compact admitted-feature offsets by input instance
    feature_counts: list   # admitted feature-record count by input instance
    overflow: bool = False # candidate demand exceeds the physical site page


@dataclass(frozen=True)
class Density:
    length: float
    units: float
    wa: float
    wb: float

    def __post_init__(self):
        if self.units > 0.0:
            object.__setattr__(self, "units", max(self.units, 1e-7))


@dataclass(frozen=True)
class DensitySpan:
    arc_lo: float
    arc_hi: float
    density: Density
    level_mask: int


def segment_density(cam: Camera, inst: Instance, a, b, arc_a: float, arc_b: float):
    """Clip source geometry and plan local dyadic windows on its source arc."""
    w0, w1 = xform_point(inst, a), xform_point(inst, b)
    ca, cb = clip_of(cam, w0), clip_of(cam, w1)
    interval = clip_source_interval(ca, cb)
    if interval is None:
        return None
    lo_t, hi_t = interval
    c0, c1 = ca + (cb - ca) * lo_t, ca + (cb - ca) * hi_t
    if c0[3] <= 1e-7 or c1[3] <= 1e-7:
        return None
    arc_lo = arc_a + (arc_b - arc_a) * lo_t
    arc_hi = arc_a + (arc_b - arc_a) * hi_t
    if arc_hi < arc_lo:
        arc_lo, arc_hi = arc_hi, arc_lo
        c0, c1 = c1, c0
    length = abs(arc_hi - arc_lo)
    if length <= 0.0:
        return None
    p0 = (c0[:2] / c0[3] * 0.5 + 0.5) * (cam.width, cam.height)
    p1 = (c1[:2] / c1[3] * 0.5 + 0.5) * (cam.width, cam.height)
    units = site_units(p1[0] - p0[0], p1[1] - p0[1], cam.advance, cam.row_pitch)
    density = Density(length, units, float(c0[3]), float(c1[3]))
    if not density_valid(density):
        return None
    return DensitySpan(arc_lo, arc_hi, density,
                       density_plan_mask(density, cam.quantum))


def clip_inside(c):
    return (c[3] > 1e-7 and -c[3] <= c[0] <= c[3] and -c[3] <= c[1] <= c[3]
            and 0.0 <= c[2] <= c[3])


def feature_demand(bank: Bank, inst: Instance, cam: Camera, f_local: int):
    """Return the authorized local level mask and its conservative demand.

    The feature bank has no authored required/optional tier metadata.  A
    capacity therefore diagnoses the conservative bound but never changes it.
    Smooth demand uses only the mutually owned outgoing source-face segment.
    Missing admitted neighbors may leave conservative excess demand, but each
    directed source span is charged at most once.
    No crossing is removed by this demand calculation, including offscreen
    crossings which may carry a visible chain's phase.
    """
    feature = bank.features[inst.feature_base + f_local]
    if feature.cls != SMOOTH:
        detail = segment_density(cam, inst, feature.p0, feature.p1,
                                 feature.arc0, feature.arc1)
        return (0, 0) if detail is None else (detail.level_mask,
                                                density_bound(detail.density,
                                                              detail.level_mask,
                                                              cam.quantum))
    t = source_crossing(cam, inst, feature)
    if t is None:
        return 0, 0
    a = feature.p0 + (feature.p1 - feature.p0) * t
    nxt, _ = source_links(bank, inst, cam, f_local)
    if nxt == NONE or source_links(bank, inst, cam, nxt)[1] != f_local:
        return 0, 0
    other = bank.features[inst.feature_base + nxt]
    other_t = source_crossing(cam, inst, other)
    if other_t is None:
        return 0, 0
    b = other.p0 + (other.p1 - other.p0) * other_t
    detail = segment_density(cam, inst, a, b, 0.0, float(np.linalg.norm(b - a)))
    if detail is None:
        return 0, 0
    return detail.level_mask, density_bound(detail.density, detail.level_mask,
                                            cam.quantum)


def traverse(bank: Bank, instances: list, cam: Camera, site_capacity: int | None = None) -> TraverseResult:
    """HVIC traversal, native projected demand, then deterministic admission.

    The current bank does not encode authored required/optional tiers, so the
    local level mask is carried unchanged. ``site_capacity`` is only a
    diagnostic for the physical candidate page; it is never a detail policy.
    """
    frontier = [(i, 0, 0) for i in range(len(instances))]   # seed: roots
    leaves, counts, rounds = [], [], 0
    while frontier:
        counts.append(len(frontier))
        rounds += 1
        nxt = []
        for inst_i, node_i, inherited in frontier:
            inst = instances[inst_i]
            n, c, r, axis, cone_angle = node_world(bank, inst, node_i)
            if not sphere_in_frustum(cam, c, r + max(inst.max_displacement, 0.0)) \
                    and inst.max_displacement >= 0.0:
                continue
            face = facing_interval(c, r, axis, cone_angle, inst.max_displacement,
                                   inst.max_normal_deviation, cam, cone_angle >= 0.)
            rejected = inherited or face in (FRONT, BACK)
            if rejected and not (n.flags & NODE_BYPASS_PRESENT):
                continue          # subtree has only cone-rejectable classes
            if n.flags & NODE_LEAF:
                leaves.append((inst_i, node_i, int(rejected)))
            for k in range(n.child_count):
                nxt.append((inst_i, n.first_child + k, int(rejected)))
        frontier = nxt
    # Bottom-up demand: each feature computes its native projected count once.
    # Prefix consumes these per-instance aggregates without a geometry rescan.
    demand = [0] * len(instances)
    with_kept = []
    leaf_features = {}
    level_masks = {}
    for inst_i, node_i, rej in leaves:
        inst = instances[inst_i]
        n = bank.nodes[inst.node_base + node_i]
        kept = []
        site_demand = 0
        for f in range(n.feature_first, n.feature_first + n.feature_count):
            feature = bank.features[inst.feature_base + f]
            if rej and feature.cls == SMOOTH:
                continue
            if feature.cls == SMOOTH and source_crossing(cam, inst, feature) is None:
                # P2 performs this exact source-crossing rejection before it
                # publishes a crossing. Do not compact valid crossings whose
                # density mask is empty: links and phase still need them.
                continue
            level_mask, feature_count = feature_demand(bank, inst, cam, f)
            level_masks[(inst_i, f)] = level_mask
            site_demand += feature_count
            kept.append(f)
        with_kept.append((inst_i, node_i, rej, site_demand))
        leaf_features[(inst_i, node_i)] = kept
        demand[inst_i] += site_demand

    capacity = (1 << 60) if site_capacity is None else max(0, int(site_capacity))
    overflow = sum(demand) > capacity

    feature_counts = [0] * len(instances)
    for inst_i, node_i, rej, _ in with_kept:
        feature_counts[inst_i] += len(leaf_features[(inst_i, node_i)])
    offsets, run = [], 0
    for count in feature_counts:                           # compact feature prefix
        offsets.append(run)
        run += count
    admitted = [None] * run
    cursor = [0] * len(instances)
    admitted_leaves = []
    for inst_i, node_i, rej, site_demand in with_kept:
        admitted_leaves.append((inst_i, node_i, rej))
        for f in leaf_features[(inst_i, node_i)]:
            level_mask = level_masks[(inst_i, f)]
            admitted[offsets[inst_i] + cursor[inst_i]] = (inst_i, f, level_mask)
            cursor[inst_i] += 1
    return TraverseResult(admitted_leaves, admitted, rounds, counts, demand, offsets,
                          feature_counts, overflow)


# ---------------------------------------------------------------- P2 extract
@dataclass
class Crossing:
    world: np.ndarray
    local: np.ndarray
    instance: int
    feature: int         # local to the instance's feature_base
    stable_id: int
    object_id: int
    normal: np.ndarray = None       # source-owned world normal at the crossing
    source: tuple = (0, 0, 0, 0)    # material, authored colour/paint flags, reserved
    level_mask: int = 0             # P1 authorized local levels carried to segments
    source_namespace: int = 1
    chain_id: int = 0

    @property
    def source_identity(self):
        return (self.source_namespace, self.chain_id, self.feature)


@dataclass
class Segment:
    a: np.ndarray        # world at arc_lo
    b: np.ndarray        # world at arc_hi
    arc_lo: float        # object-space arc
    arc_hi: float
    key: int
    cls: int
    object_id: int
    flags: int
    instance: int = 0
    normal_a: np.ndarray = None     # source-owned world normal at a
    normal_b: np.ndarray = None     # source-owned world normal at b
    source: tuple = (0, 0, 0, 0)    # material, authored colour/paint flags, reserved
    level_mask: int | None = None   # P1 authorized local levels; None only for fixtures
    chain_address: int = 0           # exact retained chain/address lane
    source_address: int = 0          # exact source edge address (diagnostic tie-break)

    @property
    def identity(self):
        return (self.key, self.chain_address, self.cls, self.source_address)


def g_value(cam: Camera, n_world, v_world) -> float:
    return float(np.dot(n_world, cam.forward if cam.orthographic else v_world - cam.eye))


def source_edge_g(cam: Camera, inst: Instance, f: Feature, end: int) -> float:
    return g_value(cam, xform_covector(inst, oct_decode(f.normals[2 + end])),
                   xform_point(inst, f.p0 if end == 0 else f.p1))


def source_crossing(cam: Camera, inst: Instance, f: Feature):
    """P1/P2 common zero crossing. A fixed edge is not a smooth crossing."""
    if f.cls != SMOOTH:
        return None
    g0, g1 = source_edge_g(cam, inst, f, 0), source_edge_g(cam, inst, f, 1)
    if (g0 > 0.0) == (g1 > 0.0):
        return None
    return g0 / (g0 - g1)


def feature_world(bank: Bank, inst: Instance, f_local: int):
    f = bank.features[inst.feature_base + f_local]
    return f, xform_point(inst, f.p0), xform_point(inst, f.p1)


def extract(bank: Bank, instances: list, cam: Camera, admitted: list):
    """Fixed classes: whole-edge segments with chain arc. Smooth: HZ zero
    crossing of g(v) = n_vert(v).(v - eye) (orthographic n.view_dir)."""
    segments, crossings, slot = [], [], {}
    for inst_i, f_local, level_mask in admitted:
        inst = instances[inst_i]
        f, w0, w1 = feature_world(bank, inst, f_local)
        if f.cls != SMOOTH:
            chain_address = f_local if inst.terrain else f.chain_id
            if f.arc0 <= f.arc1:
                a, b, lo, hi = w0, w1, f.arc0, f.arc1
            else:
                a, b, lo, hi = w1, w0, f.arc1, f.arc0
            normal = xform_normal(inst, oct_decode(f.normals[0]))
            segments.append(Segment(a, b, lo, hi, inst.source_namespace,
                                    f.cls, inst.object_id, 0, inst_i, normal, normal,
                                    (f.flags & 0xFFFF, 0, 0, 0), level_mask,
                                    chain_address, f_local))
            continue
        t = source_crossing(cam, inst, f)
        if t is None:
            continue
        local = f.p0 + (f.p1 - f.p0) * t
        world = w0 + (w1 - w0) * t
        normal = xform_normal(inst, (1.0 - t) * oct_decode(f.normals[2])
                              + t * oct_decode(f.normals[3]))
        slot[(inst_i, f_local)] = len(crossings)
        crossings.append(Crossing(world, local, inst_i, f_local, f.stable_id, inst.object_id,
                                  normal, (f.flags & 0xFFFF, 0, 0, 0), level_mask,
                                  inst.source_namespace, f.chain_id))
    return segments, crossings, slot


def source_links(bank: Bank, inst: Instance, cam: Camera, f_local: int):
    """Source-address next/prev; independent of admitted append slots."""
    result = [NONE, NONE]
    f = bank.features[inst.feature_base + f_local]
    t = source_crossing(cam, inst, f)
    if t is None:
        return tuple(result)
    for face_local in bank.edge_faces[inst.feature_base + f_local]:
        if face_local == NONE:
            continue
        face = bank.faces[inst.face_base + face_local]
        edge_slot = face.edges.index(f_local)
        following = bank.features[inst.feature_base + face.edges[(edge_slot + 1) % 3]]
        forward = bool(np.array_equal(f.p1, following.p0) or np.array_equal(f.p1, following.p1))
        # Positive -> nonpositive along the face winding owns its outgoing
        # contour. This remains defined when both crossings coincide at a
        # vertex; geometry cannot decide that zero-length link's orientation.
        lane = 0 if source_edge_g(cam, inst, f, 0 if forward else 1) > 0.0 else 1
        for edge in face.edges:
            if edge == f_local:
                continue
            other = bank.features[inst.feature_base + edge]
            other_t = source_crossing(cam, inst, other)
            if other_t is None:
                continue
            if result[lane] == NONE:
                result[lane] = edge
            break
    return tuple(result)


def link(bank: Bank, instances: list, cam: Camera, crossings: list, slot: dict):
    """Resolve source neighbors; missing crossings never change the pairing."""
    nxt, prv = [NONE] * len(crossings), [NONE] * len(crossings)
    for i, c in enumerate(crossings):
        addresses = source_links(bank, instances[c.instance], cam, c.feature)
        nxt[i], prv[i] = (slot.get((c.instance, address), NONE) for address in addresses)
    # mutual validation: an arrow survives only when both ends agree
    vnext = [j if j != NONE and prv[j] == i else NONE for i, j in enumerate(nxt)]
    vprev = [j if j != NONE and nxt[j] == i else NONE for i, j in enumerate(prv)]
    return vnext, vprev


def jump_rounds(count: int) -> int:
    return 0 if count <= 1 else (count - 1).bit_length()   # ceil(log2(count))


def loops(crossings: list, vnext: list, vprev: list):
    """Pointer jumping (ping-pong, ceil(log2 n) rounds): loop minimum of the
    retained source id, loop/chain detection, cut, arc from the cut, cut id."""
    n = len(crossings)
    R = jump_rounds(n)
    ptr = list(vnext)
    best = [(c.stable_id, c.feature) for c in crossings]
    opened = [p == NONE for p in ptr]
    for _ in range(R):
        nptr, nbest, nopen = list(ptr), list(best), list(opened)
        for i in range(n):
            if ptr[i] == NONE:
                continue
            j = ptr[i]
            nbest[i] = min(best[i], best[j])
            nptr[i] = ptr[j]
            nopen[i] = nptr[i] == NONE
        ptr, best, opened = nptr, nbest, nopen
    cut = [(vprev[i] == NONE) if opened[i] else
           ((crossings[i].stable_id, crossings[i].feature) == best[i]) for i in range(n)]
    seg_len = lambda a, b: float(np.linalg.norm(crossings[b].local - crossings[a].local))
    prev = [NONE if cut[i] else vprev[i] for i in range(n)]
    P = [seg_len(prev[i], i) if prev[i] != NONE else 0.0 for i in range(n)]
    anc = [i if prev[i] == NONE else prev[i] for i in range(n)]
    for _ in range(R):
        nprev, nP, nanc = list(prev), list(P), list(anc)
        for i in range(n):
            p = prev[i]
            if p == NONE:
                continue
            nP[i] = P[i] + P[p]
            nanc[i] = anc[p]
            nprev[i] = prev[p]
        prev, P, anc = nprev, nP, nanc
    return {"rounds": R, "arc": P, "cut_of": anc, "open": opened, "is_cut": cut}


def loop_segments(crossings: list, vnext: list, lp: dict,
                  retained_phase: dict | None = None) -> list:
    """Materialize joined spans, optionally from retained source-chart origins.

    `retained_phase` is keyed by Crossing.source_identity. It mirrors the
    production phase-history owner after that owner has reconstructed an
    anchor on the current source surface; absent entries retain the cold-start
    cut-prefix behavior.
    """
    segs = []
    retained_phase = retained_phase or {}
    for i, c in enumerate(crossings):
        j = vnext[i]
        if j == NONE:
            continue
        lo = retained_phase.get(c.source_identity, lp["arc"][i])
        hi = lo + float(np.linalg.norm(crossings[j].local - c.local))
        cut = crossings[lp["cut_of"][i]]
        flags = SITE_FLAG_LOOP if not lp["open"][i] else SITE_FLAG_OPEN_CHAIN
        segs.append(Segment(c.world, crossings[j].world, lo, hi,
                            cut.source_namespace, SMOOTH, c.object_id,
                            flags, c.instance, c.normal, crossings[j].normal, c.source,
                            c.level_mask, cut.feature, c.feature))
    return segs


# ---------------------------------------------------------------- P4 sites
def arc_level(step: float, quantum: float) -> int:
    """tff_arc_level of true_ff_contour_geometry.glslinc."""
    level, spacing = 0, quantum
    while spacing < step and level < DENSITY_MAX_LEVEL:
        spacing *= 2.0
        level += 1
    return level


def arc_step_at(length, units, wa, wb, t):
    """tff_arc_step_at of true_ff_contour_geometry.glslinc."""
    w = wa + (wb - wa) * t
    return length * (w / wa) * (w / wb) / max(units, 1e-7)


def density_valid(density: Density) -> bool:
    return (density.length > 0.0 and density.units > 0.0 and
            density.wa > 0.0 and density.wb > 0.0)


def density_endpoint_level(density: Density, quantum: float, t: float) -> int:
    return min(arc_level(arc_step_at(density.length, density.units,
                                     density.wa, density.wb, t), quantum),
               DENSITY_MAX_LEVEL)


def density_last_level(level_mask: int) -> int:
    for level in range(DENSITY_MAX_LEVEL, -1, -1):
        if level_mask & (1 << level):
            return level
    return 0


def density_window(density: Density, quantum: float, level_mask: int,
                   level: int) -> tuple[float, float]:
    """Shared mirror of tff_arc_level_window for one admitted level."""
    if (not density_valid(density) or level < 0 or level > DENSITY_MAX_LEVEL or
            not (level_mask & (1 << level))):
        return 1.0, 0.0
    last = density_last_level(level_mask)
    if density.wb == density.wa:
        chosen = min(density_endpoint_level(density, quantum, 0.5), last)
        return (0.0, 1.0) if chosen == level else (1.0, 0.0)
    scale = (density.units / density.length) * density.wa * density.wb
    spacing = quantum * (1 << level)
    low = 0.0 if level == 0 else math.sqrt(0.5 * spacing * scale)
    high = (max(density.wa, density.wb) if level == last
            else math.sqrt(spacing * scale))
    low *= 0.999999
    high *= 1.000001
    a = (low - density.wa) / (density.wb - density.wa)
    b = (high - density.wa) / (density.wb - density.wa)
    peak = np.float32(max(density.wa, density.wb))
    ulp = float(np.nextafter(peak, np.float32(np.inf)) - peak)
    unit_ulp = float(np.nextafter(np.float32(1.), np.float32(np.inf)) - np.float32(1.))
    error = 8.0 * (ulp / abs(density.wb - density.wa) + unit_ulp)
    return (min(1.0, max(0.0, max(0.0, min(a, b)) - error)),
            max(0.0, min(1.0, min(1.0, max(a, b)) + error)))


def density_plan_mask(density: Density, quantum: float) -> int:
    """Authorize every local level window touched by a visible source span."""
    if not density_valid(density):
        return 0
    first = min(density_endpoint_level(density, quantum, 0.0),
                density_endpoint_level(density, quantum, 1.0))
    last = max(density_endpoint_level(density, quantum, 0.0),
               density_endpoint_level(density, quantum, 1.0))
    return ((1 << (last + 1)) - 1) ^ ((1 << first) - 1)


def density_point_level(density: Density, t: float, quantum: float) -> int:
    if not density_valid(density):
        return DENSITY_MAX_LEVEL
    return density_endpoint_level(density, quantum, min(1.0, max(0.0, t)))


def density_bound_count(density: Density, window: tuple[float, float],
                        level: int, quantum: float) -> int:
    lo, hi = window
    if (not density_valid(density) or hi <= lo or
            level < 0 or level > DENSITY_MAX_LEVEL):
        return 0
    spacing = quantum * (1 << level)
    return max(0, math.ceil(density.length * (hi - lo) /
                            max(spacing, 1e-30)) + 1)


def density_bound(density: Density, level_mask: int, quantum: float) -> int:
    total = 0
    for level in range(DENSITY_MAX_LEVEL + 1):
        if level_mask & (1 << level):
            total += density_bound_count(density,
                                         density_window(density, quantum,
                                                        level_mask, level),
                                         level, quantum)
    return min(total, 0xFFFFFFFF)


def site_units(dx, dy, advance, pitch):
    # Same continuous native metric as the shared P1/P4 GLSL owner.
    return max(abs(dx) / advance, abs(dy) / pitch)


@dataclass
class Site:
    x: float
    y: float
    depth: float
    q: int               # finest-level ordinal (arc = q * quantum)
    key: int
    cls: int
    level: int
    flags: int
    object_id: int
    tangent: tuple
    arc: float
    source_world: np.ndarray = None
    source_normal: np.ndarray = None
    source: tuple = (0, 0, 0, 0)
    identity: tuple = None  # (source namespace, chain/address, class, edge address)
    source_extent: float = 0.0


def project(cam: Camera, p):
    c = clip_of(cam, p)
    return c


def clip_source_interval(a, b, *, depth_only=False):
    lo, hi = 0.0, 1.0
    planes = ((a[2], b[2]), (a[3] - a[2], b[3] - b[2]))
    if not depth_only:
        planes = ((a[3] + a[0], b[3] + b[0]), (a[3] - a[0], b[3] - b[0]),
                  (a[3] + a[1], b[3] + b[1]), (a[3] - a[1], b[3] - b[1])) + planes
    for da, db in planes:
        if da < 0 and db < 0:
            return None
        if (da < 0) != (db < 0):
            t = da / (da - db)
            if da < 0:
                lo = max(lo, t)
            else:
                hi = min(hi, t)
        if lo >= hi:
            return None
    return lo, hi


def source_projected_extent(a, b, page_size):
    interval = clip_source_interval(a, b, depth_only=True)
    if interval is None:
        return 0.0
    lo, hi = (a + (b - a) * t for t in interval)
    if lo[3] <= 1e-7 or hi[3] <= 1e-7:
        return 0.0
    return float(np.linalg.norm((hi[:2] / hi[3] - lo[:2] / lo[3]) * .5 * page_size))


def segment_sites(cam: Camera, seg: Segment) -> list:
    if seg.level_mask is None:
        raise ValueError("P4 segment is missing the P1 authorized level mask")
    ca, cb = project(cam, seg.a), project(cam, seg.b)
    source_extent = source_projected_extent(ca, cb, np.array([cam.width, cam.height]))
    interval = clip_source_interval(ca, cb)
    if interval is None:
        return []
    lo, hi = interval
    mix = lambda a, b, t: a + (b - a) * t
    normal_a, normal_b = seg.normal_a, seg.normal_b
    if normal_a is not None and normal_b is not None:
        normal_a, normal_b = mix(seg.normal_a, seg.normal_b, lo), mix(seg.normal_a, seg.normal_b, hi)
    seg = replace(seg, a=mix(seg.a, seg.b, lo), b=mix(seg.a, seg.b, hi),
                  arc_lo=mix(seg.arc_lo, seg.arc_hi, lo), arc_hi=mix(seg.arc_lo, seg.arc_hi, hi),
                  normal_a=normal_a, normal_b=normal_b)
    ca, cb = mix(ca, cb, lo), mix(ca, cb, hi)
    length = seg.arc_hi - seg.arc_lo
    if ca[3] <= 1e-7 or cb[3] <= 1e-7 or length <= 0.0:
        return []
    units = site_units((cb[0] / cb[3] - ca[0] / ca[3]) * 0.5 * cam.width,
                       (cb[1] / cb[3] - ca[1] / ca[3]) * 0.5 * cam.height,
                       cam.advance, cam.row_pitch)
    density = Density(length, units, float(ca[3]), float(cb[3]))
    level_mask = int(seg.level_mask)
    if level_mask == 0:
        return []
    out = []
    dc = cb - ca
    for level in range(DENSITY_MAX_LEVEL + 1):
        if not (level_mask & (1 << level)):
            continue
        lo_t, hi_t = density_window(density, cam.quantum, level_mask, level)
        spacing = cam.quantum * (1 << level)
        first = math.ceil((seg.arc_lo + length * lo_t) / spacing)
        end = math.ceil((seg.arc_lo + length * hi_t) / spacing)
        for j in range(first, max(first, end)):
            arc = j * spacing
            t = (arc - seg.arc_lo) / length
            if t < 0.0 or t >= 1.0:
                continue
            # Discovery windows are expanded around thresholds. Exact point
            # membership makes adjacent local windows disjoint.
            if density_point_level(density, t, cam.quantum) != level:
                continue
            c = ca + dc * t
            if c[3] <= 1e-7:
                continue
            ndc = c[:3] / c[3]
            if abs(ndc[0]) > 1.0 or abs(ndc[1]) > 1.0:
                continue
            x, y = (ndc[0] * 0.5 + 0.5) * cam.width, (ndc[1] * 0.5 + 0.5) * cam.height
            # analytic projected tangent d(ndc)/dt of the 3D segment
            tx = (dc[0] * c[3] - c[0] * dc[3]) / (c[3] * c[3]) * 0.5 * cam.width
            ty = (dc[1] * c[3] - c[1] * dc[3]) / (c[3] * c[3]) * 0.5 * cam.height
            tl = math.hypot(tx, ty)
            tan = (tx / tl, ty / tl) if tl > 1e-12 else (0.0, 0.0)
            world = seg.a + (seg.b - seg.a) * t
            depth = float(np.dot(world - cam.eye, cam.forward))
            source_normal = seg.normal_a
            if seg.normal_a is not None and seg.normal_b is not None:
                source_normal = (1.0 - t) * seg.normal_a + t * seg.normal_b
                normal_length = np.linalg.norm(source_normal)
                if normal_length > 1.0e-30:
                    source_normal = source_normal / normal_length
            out.append(Site(x, y, depth, j * (1 << level), seg.key, seg.cls, level, seg.flags,
                            seg.object_id, tan, arc, world, source_normal, seg.source,
                            seg.identity, source_extent))
    return out


def sites(cam: Camera, segments: list) -> list:
    out = []
    for s in segments:
        out.extend(segment_sites(cam, s))
    return out


# ---------------------------------------------------------------- P3 visibility
@dataclass
class Prepass:
    """Depth (NDC z, D32F) + object id per P0 texel. P0 carries no per-feature
    correspondence to the site (ids = object, material, face, family bits).
    Object equality therefore cannot bypass depth. P0 may be smaller than the page (half size in the
    renderer): a page pixel maps to the texel covering it."""
    depth: np.ndarray     # [h, w] float32
    object_id: np.ndarray  # [h, w] uint
    material_id: np.ndarray | None = None  # optional synthetic P0 channel y (default 0)
    face_id: np.ndarray | None = None      # optional synthetic P0 channel z (default 0)


def view_depth_of_ndc(cam: Camera, x: float, y: float, z: float) -> float:
    ndc = np.array([x / cam.width * 2.0 - 1.0, y / cam.height * 2.0 - 1.0, z, 1.0])
    w = cam.inv_view_proj @ ndc
    world = w[:3] / w[3]
    return float(np.dot(world - cam.eye, cam.forward))


def prepass_texel(cam: Camera, pp: Prepass, x: float, y: float) -> tuple:
    h, w = pp.depth.shape
    return (min(int(math.floor(x * w / cam.width)), w - 1),
            min(int(math.floor(y * h / cam.height)), h - 1))


def texel_centre_px(cam: Camera, pp: Prepass, tx: int, ty: int) -> tuple:
    h, w = pp.depth.shape
    return (tx + 0.5) * cam.width / w, (ty + 0.5) * cam.height / h


def texel_view_depth(cam: Camera, pp: Prepass, px: int, py: int) -> float:
    cx, cy = texel_centre_px(cam, pp, px, py)
    return view_depth_of_ndc(cam, cx, cy, float(pp.depth[py, px]))


def visible(cam: Camera, pp: Prepass, site: Site) -> bool:
    """Depth with a slope-scaled bias of one texel footprint plus the
    depth quantum. P0 object/material/face identity first authorizes each
    neighbouring slope sample. It never bypasses the site's depth test."""
    h, w = pp.depth.shape
    if site.x < 0 or site.y < 0 or site.x >= cam.width or site.y >= cam.height:
        return False
    px, py = prepass_texel(cam, pp, site.x, site.y)
    d = texel_view_depth(cam, pp, px, py)
    slope = 0.0
    for dx, dy in ((1, 0), (0, 1)):
        diffs = []
        for s in (-1, 1):
            qx, qy = px + dx * s, py + dy * s
            if 0 <= qx < w and 0 <= qy < h:
                if pp.object_id[py, px] == 0 or pp.object_id[qy, qx] != pp.object_id[py, px]:
                    continue
                if pp.material_id is not None and pp.material_id[qy, qx] != pp.material_id[py, px]:
                    continue
                if pp.face_id is not None and pp.face_id[qy, qx] != pp.face_id[py, px]:
                    continue
                diffs.append(abs(texel_view_depth(cam, pp, qx, qy) - d))
        if diffs:
            slope = max(slope, min(diffs))
    z = np.float32(pp.depth[py, px])
    z_next = float(np.nextafter(z, np.float32(2.0), dtype=np.float32))
    cx, cy = texel_centre_px(cam, pp, px, py)
    quantum = abs(view_depth_of_ndc(cam, cx, cy, z_next) - d)
    return site.depth <= d + slope + quantum


def visibility(cam: Camera, pp: Prepass, candidate_sites: list) -> list:
    return [s for s in candidate_sites if visible(cam, pp, s)]


# ---------------------------------------------------------------- full chain
def run(bank: Bank, instances: list, cam: Camera, prepass: Prepass | None = None,
        site_capacity: int | None = None) -> dict:
    tr = traverse(bank, instances, cam, site_capacity=site_capacity)
    segs, crossings, slot = extract(bank, instances, cam, tr.admitted)
    vnext, vprev = link(bank, instances, cam, crossings, slot)
    lp = loops(crossings, vnext, vprev)
    segs = segs + loop_segments(crossings, vnext, lp)
    cand = sites(cam, segs)
    final = visibility(cam, prepass, cand) if prepass is not None else cand
    return {"traverse": tr, "crossings": crossings, "next": vnext, "prev": vprev,
            "loops": lp, "segments": segs, "candidates": cand, "sites": final,
            "overflow": tr.overflow}


# ---------------------------------------------------------------- test fixture bank
def uv_sphere(radius=1.0, rings=24, sectors=48):
    verts = [(0.0, radius, 0.0)]
    for r in range(1, rings):
        phi = math.pi * r / rings
        for s in range(sectors):
            th = 2.0 * math.pi * s / sectors
            verts.append((radius * math.sin(phi) * math.cos(th), radius * math.cos(phi),
                          radius * math.sin(phi) * math.sin(th)))
    verts.append((0.0, -radius, 0.0))
    faces = []
    ring = lambda r, s: 1 + (r - 1) * sectors + (s % sectors)
    bottom = len(verts) - 1
    for s in range(sectors):
        faces.append((0, ring(1, s + 1), ring(1, s)))
        faces.append((bottom, ring(rings - 1, s), ring(rings - 1, s + 1)))
    for r in range(1, rings - 1):
        for s in range(sectors):
            a, b, c, d = ring(r, s), ring(r, s + 1), ring(r + 1, s), ring(r + 1, s + 1)
            faces.append((a, b, d))
            faces.append((a, d, c))
    verts = np.array(verts)
    normals = verts / np.linalg.norm(verts, axis=1)[:, None]
    return verts, faces, normals


def torus(R=1.0, r=0.35, n_major=48, n_minor=24):
    verts, normals = [], []
    for i in range(n_major):
        u = 2.0 * math.pi * i / n_major
        for j in range(n_minor):
            v = 2.0 * math.pi * j / n_minor
            c = np.array([math.cos(u), 0.0, math.sin(u)])
            n = c * math.cos(v) + np.array([0.0, math.sin(v), 0.0])
            verts.append(c * R + n * r)
            normals.append(n)
    idx = lambda i, j: (i % n_major) * n_minor + (j % n_minor)
    faces = []
    for i in range(n_major):
        for j in range(n_minor):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i, j + 1), idx(i + 1, j + 1)
            faces.append((a, c, d))
            faces.append((a, d, b))
    return np.array(verts), faces, np.array(normals)


def _face_normal(verts, f):
    n = np.cross(verts[f[1]] - verts[f[0]], verts[f[2]] - verts[f[0]])
    return n / np.linalg.norm(n)


def mini_bank(verts, faces, vnormals, classify=None, leaf=LEAF_FEATURES, asset_salt=0x2545):
    """TEST FIXTURE: one asset, every mesh edge a FeatureRecord, an octree
    with normal cones bounding every face and vertex normal of its edges."""
    edges, edge_index, edge_faces = [], {}, []
    face_recs = []
    for fi, f in enumerate(faces):
        es = []
        for k in range(3):
            a, b = f[k], f[(k + 1) % 3]
            key = (min(a, b), max(a, b))
            if key not in edge_index:
                edge_index[key] = len(edges)
                edges.append(key)
                edge_faces.append([NONE, NONE])
            e = edge_index[key]
            slot_ = 0 if edge_faces[e][0] == NONE else 1
            edge_faces[e][slot_] = fi
            es.append(e)
        face_recs.append(Face(tuple(es), 0))
    fnorm = [_face_normal(verts, f) for f in faces]
    feats = []
    for e, (a, b) in enumerate(edges):
        f0, f1 = edge_faces[e]
        n0 = fnorm[f0]
        n1 = fnorm[f1] if f1 != NONE else n0
        cls = classify(e, a, b, n0, n1) if classify else SMOOTH
        if f1 == NONE and cls == SMOOTH:
            cls = OPEN_BOUNDARY
        feats.append(Feature(verts[a].copy(), 0.0, verts[b].copy(),
                             float(np.linalg.norm(verts[b] - verts[a])),
                             (oct_encode(n0), oct_encode(n1), oct_encode(vnormals[a]),
                              oct_encode(vnormals[b])), cls, e, mix32(asset_salt ^ mix32(e))))
    bank = Bank()
    order = []

    def build(ids, level, lo, hi):
        node_i = len(bank.nodes)
        bank.nodes.append(None)
        pts = np.array([p for e in ids for p in (feats[e].p0, feats[e].p1)])
        centre = (pts.min(axis=0) + pts.max(axis=0)) * 0.5
        radius = float(np.max(np.linalg.norm(pts - centre, axis=1)))
        ns = [oct_decode(u) for e in ids for u in feats[e].normals]
        axis = np.sum(ns, axis=0)
        half = -1.0
        if np.linalg.norm(axis) > 1e-9:
            axis = axis / np.linalg.norm(axis)
            half = max(angle_between(axis, n) for n in ns)
            if half >= math.pi * 0.5:
                half = -1.0
        else:
            axis = np.array([0.0, 1.0, 0.0])
        flags = NODE_BYPASS_PRESENT if any(feats[e].cls != SMOOTH for e in ids) else 0
        node = Node(centre, radius, axis, half, flags=flags, level=level)
        bank.nodes[node_i] = node
        if len(ids) <= leaf or level >= 12:
            node.flags |= NODE_LEAF
            node.feature_first = len(order)
            node.feature_count = len(ids)
            order.extend(ids)
            bank.max_level = max(bank.max_level, level)
            return node_i
        mids = [(feats[e].p0 + feats[e].p1) * 0.5 for e in ids]
        split = (lo + hi) * 0.5
        buckets = {}
        for e, m in zip(ids, mids):
            k = int(m[0] > split[0]) | int(m[1] > split[1]) << 1 | int(m[2] > split[2]) << 2
            buckets.setdefault(k, []).append(e)
        if len(buckets) == 1:
            node.flags |= NODE_LEAF
            node.feature_first = len(order)
            node.feature_count = len(ids)
            order.extend(ids)
            bank.max_level = max(bank.max_level, level)
            return node_i
        children = []
        for k in sorted(buckets):
            clo = np.array([split[a] if (k >> a) & 1 else lo[a] for a in range(3)])
            chi = np.array([hi[a] if (k >> a) & 1 else split[a] for a in range(3)])
            children.append((buckets[k], clo, chi))
        return node_i, children

    # breadth-first so each node's children are contiguous
    root_pts = verts
    queue = [(list(range(len(edges))), 0, root_pts.min(axis=0), root_pts.max(axis=0), None)]
    while queue:
        nq = []
        for ids, level, lo, hi, parent in queue:
            res = build(ids, level, lo, hi)
            node_i = res if isinstance(res, int) else res[0]
            if parent is not None:
                p = bank.nodes[parent]
                if p.child_count == 0:
                    p.first_child = node_i
                p.child_count += 1
            if not isinstance(res, int):
                for cids, clo, chi in res[1]:
                    nq.append((cids, level + 1, clo, chi, node_i))
        queue = nq
    remap = {old: new for new, old in enumerate(order)}
    bank.features = [feats[e] for e in order]
    bank.edge_faces = [tuple(edge_faces[e]) for e in order]
    bank.faces = [Face(tuple(remap[e] for e in f.edges), f.flags) for f in face_recs]
    for i in reversed(range(len(bank.nodes))):
        n = bank.nodes[i]
        for k in range(n.child_count):
            if bank.nodes[n.first_child + k].flags & NODE_BYPASS_PRESENT:
                n.flags |= NODE_BYPASS_PRESENT
    return bank


def single_instance(bank: Bank, object_id=7, transform=None, max_disp=0.0, max_ndev=0.0):
    return Instance(np.eye(4) if transform is None else transform, 0, 0, 0, object_id,
                    max_disp, max_ndev, 0, 1)


def flat_prepass(cam: Camera, depth_ndc=1.0, object_id=0, divisor=1) -> Prepass:
    h, w = -(-int(cam.height) // divisor), -(-int(cam.width) // divisor)
    return Prepass(np.full((h, w), depth_ndc, np.float32), np.full((h, w), object_id, np.uint32))
