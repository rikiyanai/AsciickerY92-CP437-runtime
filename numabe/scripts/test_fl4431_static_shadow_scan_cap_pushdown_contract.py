#!/usr/bin/env python3
"""FL-4431 static-shadow scan-cap pushdown destructive contract.

The retained static selector admits only valid local primitives whose legacy
face is below 2,048 and differs from the receiver face. Those checks happen
before retained face materialization. Material 250 is rejected immediately
after materialization, and the smallest admitted legacy face wins regardless
of traversal order.

The deleted b61 all-ones Q3 mask and ``require_q3_admission`` branch are not
admission semantics. This suite guards their physical absence and carries one
explicit resurrection mutant alongside the surviving parity and reduction
laws. This is structural and arithmetic evidence only; it does not establish
runtime timing, visual parity, operator acceptance, or closure.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
import math
import random
import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

SCAN_CAP = 2048
STATIC_MESH_SHADOW_SOURCE_BASE = 0x54000000
RANDOM_SEED = 4431


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", f32(value)))[0]


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float


def vec_add(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(f32(a.x + b.x), f32(a.y + b.y), f32(a.z + b.z))


def vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(f32(a.x - b.x), f32(a.y - b.y), f32(a.z - b.z))


def vec_mul(a: Vec3, scalar: float) -> Vec3:
    return Vec3(
        f32(a.x * scalar),
        f32(a.y * scalar),
        f32(a.z * scalar),
    )


def vec_dot(a: Vec3, b: Vec3) -> float:
    return f32(f32(f32(a.x * b.x) + f32(a.y * b.y)) + f32(a.z * b.z))


def vec_cross(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(
        f32(f32(a.y * b.z) - f32(a.z * b.y)),
        f32(f32(a.z * b.x) - f32(a.x * b.z)),
        f32(f32(a.x * b.y) - f32(a.y * b.x)),
    )


def vec_length(a: Vec3) -> float:
    return f32(math.sqrt(max(vec_dot(a, a), 0.0)))


def vec_normalize(a: Vec3) -> Vec3:
    length = vec_length(a)
    if length <= f32(1.0e-20):
        return Vec3(0.0, 1.0, 0.0)
    return vec_mul(a, f32(1.0 / length))


def min_vec(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z))


def max_vec(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z))


def average4(points: tuple[Vec3, Vec3, Vec3, Vec3]) -> Vec3:
    a, b, c, d = points
    return Vec3(
        f32(f32(f32(f32(a.x + b.x) + c.x) + d.x) * 0.25),
        f32(f32(f32(f32(a.y + b.y) + c.y) + d.y) * 0.25),
        f32(f32(f32(f32(a.z + b.z) + c.z) + d.z) * 0.25),
    )


@dataclass(frozen=True)
class Transform:
    """Exact quarter-turn Y basis plus uniform scale and translation."""

    origin: Vec3 = Vec3(0.0, 0.0, 0.0)
    quarter_turn: int = 0
    scale: float = 1.0

    def rotate(self, value: Vec3) -> Vec3:
        turn = self.quarter_turn & 3
        if turn == 0:
            return value
        if turn == 1:
            return Vec3(value.z, value.y, f32(-value.x))
        if turn == 2:
            return Vec3(f32(-value.x), value.y, f32(-value.z))
        return Vec3(f32(-value.z), value.y, value.x)

    def inverse_rotate(self, value: Vec3) -> Vec3:
        turn = self.quarter_turn & 3
        if turn == 0:
            return value
        if turn == 1:
            return Vec3(f32(-value.z), value.y, value.x)
        if turn == 2:
            return Vec3(f32(-value.x), value.y, f32(-value.z))
        return Vec3(value.z, value.y, f32(-value.x))

    def point(self, value: Vec3) -> Vec3:
        scaled = vec_mul(value, f32(self.scale))
        return vec_add(self.origin, self.rotate(scaled))

    def local_ray(self, ray: "Ray") -> "Ray":
        inverse_scale = f32(1.0 / f32(self.scale))
        local_origin = vec_mul(
            self.inverse_rotate(vec_sub(ray.origin, self.origin)),
            inverse_scale,
        )
        local_direction = vec_mul(
            self.inverse_rotate(ray.direction),
            inverse_scale,
        )
        return Ray(local_origin, local_direction, ray.length)


@dataclass(frozen=True)
class Face:
    points: tuple[Vec3, Vec3, Vec3, Vec3]
    material: int

    @property
    def box_min(self) -> Vec3:
        result = self.points[0]
        for point in self.points[1:]:
            result = min_vec(result, point)
        return result

    @property
    def box_max(self) -> Vec3:
        result = self.points[0]
        for point in self.points[1:]:
            result = max_vec(result, point)
        return result


@dataclass(frozen=True)
class Asset:
    faces: tuple[Face, ...]

    @property
    def box_min(self) -> Vec3:
        result = self.faces[0].box_min
        for face in self.faces[1:]:
            result = min_vec(result, face.box_min)
        return result

    @property
    def box_max(self) -> Vec3:
        result = self.faces[0].box_max
        for face in self.faces[1:]:
            result = max_vec(result, face.box_max)
        return result


@dataclass(frozen=True)
class Instance:
    object_id: int
    asset: Asset
    first_face: int
    transform: Transform = Transform()

    @property
    def face_count(self) -> int:
        return len(self.asset.faces)

    @property
    def world_box(self) -> tuple[Vec3, Vec3]:
        local_min = self.asset.box_min
        local_max = self.asset.box_max
        corners = tuple(
            self.transform.point(Vec3(x, y, z))
            for x in (local_min.x, local_max.x)
            for y in (local_min.y, local_max.y)
            for z in (local_min.z, local_max.z)
        )
        box_min = corners[0]
        box_max = corners[0]
        for corner in corners[1:]:
            box_min = min_vec(box_min, corner)
            box_max = max_vec(box_max, corner)
        return box_min, box_max


@dataclass(frozen=True)
class Ray:
    origin: Vec3
    direction: Vec3
    length: float = 1000.0


@dataclass(frozen=True)
class BvhNode:
    box_min: Vec3
    box_max: Vec3
    left: "BvhNode | None" = None
    right: "BvhNode | None" = None
    item: int | None = None


def build_bvh(items: tuple[tuple[int, Vec3, Vec3], ...]) -> BvhNode:
    assert items
    if len(items) == 1:
        item, box_min, box_max = items[0]
        return BvhNode(box_min, box_max, item=item)
    split = len(items) // 2
    left = build_bvh(items[:split])
    right = build_bvh(items[split:])
    return BvhNode(
        min_vec(left.box_min, right.box_min),
        max_vec(left.box_max, right.box_max),
        left,
        right,
    )


def asset_bvh(asset: Asset) -> BvhNode:
    return build_bvh(
        tuple(
            (index, face.box_min, face.box_max)
            for index, face in enumerate(asset.faces)
        )
    )


@dataclass(frozen=True)
class Scene:
    instances: tuple[Instance, ...]

    @property
    def tlas(self) -> BvhNode:
        return build_bvh(
            tuple(
                (index, *instance.world_box)
                for index, instance in enumerate(self.instances)
            )
        )


@dataclass
class WorkCounters:
    tlas_node_pops: int = 0
    blas_entries: int = 0
    blas_node_pops: int = 0
    face_materializations: int = 0
    local_face_row_loads: int = 0
    world_point_transforms: int = 0
    triangle_tests: int = 0


@dataclass(frozen=True)
class SelectedCandidate:
    object_id: int
    primitive_id: int
    material: int
    legacy_face: int
    stable_anchor: Vec3
    caster_xz: tuple[float, float]
    depth: float


@dataclass(frozen=True)
class SelectionOutput:
    valid: bool
    identity: tuple[int, int] | None
    material: int
    stable_anchor_bits: tuple[int, int, int, int]
    legacy_face: int | None
    depth_bits: int
    caster_id: int
    caster_xz_bits: tuple[int, int]
    static_attenuation_bits: int
    final_attenuation_bits: int


@dataclass(frozen=True)
class RunResult:
    output: SelectionOutput
    work: WorkCounters


@dataclass(frozen=True)
class EarlyPolicy:
    cap_off_by_one: bool = False
    wrong_whole_range: bool = False
    resurrect_q3_rejected_faces: frozenset[int] = frozenset()
    nearest_selection: bool = False
    reverse_traversal: bool = False


DEFAULT_POLICY = EarlyPolicy()


def ray_aabb_entry(ray: Ray, box_min: Vec3, box_max: Vec3) -> float:
    entry = -math.inf
    exit_depth = math.inf
    for origin, direction, low, high in (
        (ray.origin.x, ray.direction.x, box_min.x, box_max.x),
        (ray.origin.y, ray.direction.y, box_min.y, box_max.y),
        (ray.origin.z, ray.direction.z, box_min.z, box_max.z),
    ):
        if abs(direction) <= 1.0e-20:
            if origin < low or origin > high:
                return math.inf
            continue
        inverse = 1.0 / direction
        near = (low - origin) * inverse
        far = (high - origin) * inverse
        if near > far:
            near, far = far, near
        entry = max(entry, near)
        exit_depth = min(exit_depth, far)
        if exit_depth < entry:
            return math.inf
    if exit_depth < 0.0:
        return math.inf
    return max(entry, 0.0)


def ray_triangle_t(ray: Ray, a: Vec3, b: Vec3, c: Vec3) -> float:
    edge_1 = vec_sub(b, a)
    edge_2 = vec_sub(c, a)
    pvec = vec_cross(ray.direction, edge_2)
    determinant = vec_dot(edge_1, pvec)
    if abs(determinant) <= f32(1.0e-8):
        return f32(-1.0)
    inverse = f32(1.0 / determinant)
    tvec = vec_sub(ray.origin, a)
    u = f32(vec_dot(tvec, pvec) * inverse)
    if u < 0.0 or u > 1.0:
        return f32(-1.0)
    qvec = vec_cross(tvec, edge_1)
    v = f32(vec_dot(ray.direction, qvec) * inverse)
    if v < 0.0 or f32(u + v) > 1.0:
        return f32(-1.0)
    depth = f32(vec_dot(edge_2, qvec) * inverse)
    if depth < 0.0 or depth >= f32(ray.length):
        return f32(-1.0)
    return depth


def smoothstep(edge_0: float, edge_1: float, value: float) -> float:
    t = f32(f32(value - edge_0) / f32(edge_1 - edge_0))
    t = f32(min(max(t, 0.0), 1.0))
    return f32(f32(t * t) * f32(3.0 - f32(2.0 * t)))


def mix(a: float, b: float, weight: float) -> float:
    return f32(f32(a * f32(1.0 - weight)) + f32(b * weight))


SUN = vec_normalize(Vec3(0.78, 0.48, -0.38))


def static_shadow_attenuation(receiver_normal: Vec3, shadowed: bool) -> float:
    normal = vec_normalize(receiver_normal)
    lambert = max(vec_dot(normal, SUN), 0.0)
    hemisphere = smoothstep(-0.18, 0.92, normal.y)
    sky_fill = mix(0.28, 0.74, hemisphere)
    sun_lift = smoothstep(0.18, 0.96, lambert)
    lit_value = f32(
        min(
            max(
                f32(
                    f32(0.16 + f32(sky_fill * 0.34))
                    + f32(f32(lambert * 0.48) + f32(sun_lift * 0.16))
                ),
                0.0,
            ),
            1.0,
        )
    )
    ambient = f32(
        min(max(f32(0.16 + f32(sky_fill * 0.34)), 0.0), 1.0)
    )
    ratio = f32(min(max(f32(ambient / max(lit_value, 1.0e-4)), 0.0), 1.0))
    return mix(1.0, ratio, 1.0 if shadowed else 0.0)


def final_shadow_attenuation(
    receiver_normal: Vec3,
    shadowed: bool,
    blob_density: float,
) -> tuple[float, float]:
    density = f32(min(max(blob_density, 0.0), 1.0))
    blob_attenuation = max(mix(1.0, 0.38, density), 0.08)
    static_attenuation = static_shadow_attenuation(receiver_normal, shadowed)
    return f32(static_attenuation), f32(min(blob_attenuation, static_attenuation))


def correct_legacy_admitted(
    legacy_face: int,
    receiver_face: int,
    scan_cap: int,
) -> bool:
    if legacy_face == receiver_face or legacy_face < 0:
        return False
    return legacy_face < scan_cap


def early_legacy_admitted(
    scene: Scene,
    instance: Instance,
    primitive_id: int,
    receiver_face: int,
    scan_cap: int,
    policy: EarlyPolicy,
) -> tuple[bool, int]:
    if scan_cap <= 0 or primitive_id < 0 or primitive_id >= instance.face_count:
        return False, 0
    legacy_face = instance.first_face + primitive_id
    outside_cap = (
        legacy_face > scan_cap
        if policy.cap_off_by_one
        else legacy_face >= scan_cap
    )
    if outside_cap or legacy_face == receiver_face:
        return False, legacy_face
    if legacy_face in policy.resurrect_q3_rejected_faces:
        return False, legacy_face
    return True, legacy_face


def whole_instance_rejected(
    instance: Instance,
    scan_cap: int,
    policy: EarlyPolicy,
) -> bool:
    if scan_cap <= 0:
        return True
    if policy.wrong_whole_range:
        return instance.first_face + instance.face_count > scan_cap
    if policy.cap_off_by_one:
        return instance.first_face > scan_cap
    return instance.first_face >= scan_cap


def materialize_face(
    instance: Instance,
    primitive_id: int,
    work: WorkCounters,
) -> tuple[tuple[Vec3, Vec3, Vec3, Vec3], Vec3, tuple[float, float], int]:
    work.face_materializations += 1
    work.local_face_row_loads += 4
    work.world_point_transforms += 5
    face = instance.asset.faces[primitive_id]
    world_points = tuple(instance.transform.point(point) for point in face.points)
    assert len(world_points) == 4
    local_anchor = average4(face.points)
    stable_anchor = instance.transform.point(local_anchor)
    caster_center = average4(world_points)
    return (
        world_points,
        stable_anchor,
        (caster_center.x, caster_center.z),
        face.material,
    )


def child_sequence(node: BvhNode, reverse: bool) -> tuple[BvhNode, BvhNode]:
    assert node.left is not None and node.right is not None
    return (node.right, node.left) if reverse else (node.left, node.right)


def select_static(
    scene: Scene,
    ray: Ray,
    receiver_normal: Vec3,
    *,
    receiver_face: int = -1,
    scan_cap: int = SCAN_CAP,
    blob_density: float = 0.37,
    early: bool,
    policy: EarlyPolicy = DEFAULT_POLICY,
) -> RunResult:
    work = WorkCounters()
    selected: SelectedCandidate | None = None

    def consider(instance: Instance, primitive_id: int) -> None:
        nonlocal selected
        if early:
            admitted, legacy_face = early_legacy_admitted(
                scene,
                instance,
                primitive_id,
                receiver_face,
                scan_cap,
                policy,
            )
            if not admitted:
                return
        else:
            legacy_face = instance.first_face + primitive_id

        points, stable_anchor, caster_xz, material = materialize_face(
            instance,
            primitive_id,
            work,
        )

        if not early and not correct_legacy_admitted(
            legacy_face,
            receiver_face,
            scan_cap,
        ):
            return
        if material == 250:
            return

        work.triangle_tests += 2
        t_0 = ray_triangle_t(ray, points[0], points[1], points[2])
        t_1 = ray_triangle_t(ray, points[0], points[2], points[3])
        if t_0 < 0.0 and t_1 < 0.0:
            return
        hit_depth = t_1 if t_0 < 0.0 else (t_0 if t_1 < 0.0 else min(t_0, t_1))
        candidate = SelectedCandidate(
            instance.object_id,
            primitive_id,
            material,
            legacy_face,
            stable_anchor,
            caster_xz,
            f32(hit_depth),
        )
        if selected is None:
            selected = candidate
            return
        if policy.nearest_selection and early:
            if candidate.depth < selected.depth:
                selected = candidate
            return
        if candidate.legacy_face < selected.legacy_face:
            selected = candidate

    def traverse_blas(instance: Instance) -> None:
        if early and whole_instance_rejected(instance, scan_cap, policy):
            return
        work.blas_entries += 1
        root = asset_bvh(instance.asset)
        if root.item is not None:
            consider(instance, root.item)
            return
        local_ray = instance.transform.local_ray(ray)
        stack = [root]
        while stack:
            node = stack.pop()
            work.blas_node_pops += 1
            for child in child_sequence(node, policy.reverse_traversal):
                if child.item is not None:
                    consider(instance, child.item)
                elif ray_aabb_entry(
                    local_ray, child.box_min, child.box_max
                ) < local_ray.length:
                    stack.append(child)

    tlas = scene.tlas
    if tlas.item is not None:
        traverse_blas(scene.instances[tlas.item])
    else:
        stack = [tlas]
        while stack:
            node = stack.pop()
            work.tlas_node_pops += 1
            for child in child_sequence(node, policy.reverse_traversal):
                if child.item is not None:
                    traverse_blas(scene.instances[child.item])
                elif ray_aabb_entry(
                    ray, child.box_min, child.box_max
                ) < ray.length:
                    stack.append(child)

    static_attenuation, final_attenuation = final_shadow_attenuation(
        receiver_normal,
        selected is not None,
        blob_density,
    )
    if selected is None:
        output = SelectionOutput(
            False,
            None,
            0,
            (0, 0, 0, 0),
            None,
            float_bits(1.0e30),
            0,
            (float_bits(0.0), float_bits(0.0)),
            float_bits(static_attenuation),
            float_bits(final_attenuation),
        )
    else:
        output = SelectionOutput(
            True,
            (selected.object_id, selected.primitive_id),
            selected.material,
            (
                float_bits(selected.stable_anchor.x),
                float_bits(selected.stable_anchor.y),
                float_bits(selected.stable_anchor.z),
                float_bits(1.0),
            ),
            selected.legacy_face,
            float_bits(selected.depth),
            STATIC_MESH_SHADOW_SOURCE_BASE + selected.legacy_face,
            (
                float_bits(selected.caster_xz[0]),
                float_bits(selected.caster_xz[1]),
            ),
            float_bits(static_attenuation),
            float_bits(final_attenuation),
        )
    return RunResult(output, work)


def vertical_face(
    z: float,
    *,
    material: int = 1,
    half_size: float = 1.0,
    center_x: float = 0.0,
    center_y: float = 0.0,
) -> Face:
    x_0 = f32(center_x - half_size)
    x_1 = f32(center_x + half_size)
    y_0 = f32(center_y - half_size)
    y_1 = f32(center_y + half_size)
    depth = f32(z)
    return Face(
        (
            Vec3(x_0, y_0, depth),
            Vec3(x_1, y_0, depth),
            Vec3(x_1, y_1, depth),
            Vec3(x_0, y_1, depth),
        ),
        material,
    )


def scene_for_instance(
    first_face: int,
    faces: tuple[Face, ...],
    *,
    transform: Transform = Transform(),
) -> Scene:
    return Scene(
        (Instance(0, Asset(faces), first_face, transform),),
    )


DEFAULT_RAY = Ray(Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 1.0), 100.0)
DEFAULT_NORMAL = Vec3(0.2, 1.0, 0.3)


def assert_parity(
    scene: Scene,
    ray: Ray = DEFAULT_RAY,
    normal: Vec3 = DEFAULT_NORMAL,
    **kwargs: object,
) -> tuple[RunResult, RunResult]:
    late = select_static(scene, ray, normal, early=False, **kwargs)
    early = select_static(scene, ray, normal, early=True, **kwargs)
    assert early.output == late.output
    return late, early


def extract_glsl_function(source: str, name: str) -> str:
    match = re.search(
        r"(?m)^(?:precise\s+)?[A-Za-z_]\w*\s+"
        + re.escape(name)
        + r"\s*\([^;{}]*?\)\s*\{",
        source,
        re.S,
    )
    if match is None:
        return ""
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    return ""


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def test_shipping_source_uses_the_sole_static_admission_law() -> None:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    consider = compact(
        extract_glsl_function(source, "vsb_retained_consider_static_caster")
    )
    blas = compact(
        extract_glsl_function(source, "vsb_retained_static_blas_select")
    )
    helper = compact(
        extract_glsl_function(
            source, "vsb_retained_static_legacy_candidate_admitted"
        )
    )
    assert consider and blas

    helper_shape = (
        helper
        and "scan_cap<=0" in helper
        and "primitive_id>=instance.refs.w" in helper
        and "legacy_face_index=instance.refs.z+primitive_id" in helper
        and "legacy_face_index>=uint(scan_cap)" in helper
        and "int(legacy_face_index)==receiver_face_idx" in helper
        and "legacy_face_index>=selected.facts.legacy_face_index" in helper
    )
    positive_cap_gate = blas.find("scan_cap<=0")
    whole_cap_gate = blas.find("instance.refs.z>=uint(scan_cap)")
    asset_load = blas.find(
        "VsbSemanticStaticAssetasset=vsb_semantic_static_assets.values"
    )
    materializer = consider.find("vsb_retained_static_face_by_indices(")
    material_reject = consider.find("facts.material_id==250u")
    assert helper_shape, "static primitive/cap/receiver admission law drifted"
    assert 0 <= positive_cap_gate < asset_load
    assert 0 <= whole_cap_gate < asset_load, (
        "whole-instance cap must precede asset and BLAS materialization"
    )
    assert 0 <= materializer < material_reject, (
        "material 250 rejection must follow retained face materialization"
    )
    assert blas.count(
        "vsb_retained_static_legacy_candidate_admitted("
    ) >= 2, "both BLAS leaf sites must use the sole admission helper"
    assert "receiver_face_idx" not in consider
    assert "scan_cap" not in consider
    assert "q3_static_face_cull_masks" not in source
    assert "require_q3_admission" not in source


def test_boundary_parity_at_scan_cap_edges() -> None:
    cases = (
        (0, (vertical_face(4.0),)),
        (1, (vertical_face(4.0),)),
        (2047, (vertical_face(4.0),)),
        (2048, (vertical_face(4.0),)),
        (2049, (vertical_face(4.0),)),
        (
            2047,
            (
                vertical_face(4.0),
                vertical_face(5.0),
                vertical_face(6.0),
            ),
        ),
    )
    for first_face, faces in cases:
        assert_parity(scene_for_instance(first_face, faces))


def test_deterministic_randomized_tlas_blas_parity() -> None:
    rng = random.Random(RANDOM_SEED)
    for scene_index in range(48):
        assets: list[Asset] = []
        for asset_index in range(3):
            faces = tuple(
                vertical_face(
                    2.0 + primitive_id * 1.25,
                    material=(
                        250
                        if rng.random() < 0.12
                        else 1 + ((scene_index + asset_index + primitive_id) % 9)
                    ),
                    half_size=rng.uniform(0.35, 1.8),
                    center_x=rng.uniform(-0.8, 0.8),
                    center_y=rng.uniform(-0.8, 0.8),
                )
                for primitive_id in range(rng.randint(1, 6))
            )
            assets.append(Asset(faces))

        instances: list[Instance] = []
        cursor = rng.choice((0, 2038, 2045, 2047))
        for object_id in range(rng.randint(3, 8)):
            asset = assets[rng.randrange(len(assets))]
            instances.append(
                Instance(
                    object_id,
                    asset,
                    cursor,
                    Transform(
                        Vec3(
                            f32(rng.uniform(-8.0, 8.0)),
                            f32(rng.uniform(-3.0, 3.0)),
                            f32(rng.uniform(-3.0, 10.0)),
                        ),
                        rng.randrange(4),
                        f32(rng.uniform(0.55, 2.2)),
                    ),
                )
            )
            cursor += len(asset.faces)

        scene = Scene(tuple(instances))
        for receiver_index in range(8):
            target_instance = instances[rng.randrange(len(instances))]
            target_face = target_instance.asset.faces[
                rng.randrange(target_instance.face_count)
            ]
            target = target_instance.transform.point(average4(target_face.points))
            local_normal = Vec3(0.0, 0.0, 1.0)
            world_normal = vec_normalize(
                target_instance.transform.rotate(local_normal)
            )
            ray = Ray(
                vec_sub(target, vec_mul(world_normal, 12.0)),
                world_normal,
                30.0,
            )
            receiver_face = (
                -1
                if receiver_index % 3
                else rng.randrange(max(cursor, 1))
            )
            assert_parity(
                scene,
                ray,
                Vec3(
                    f32(rng.uniform(-1.0, 1.0)),
                    f32(rng.uniform(0.05, 1.0)),
                    f32(rng.uniform(-1.0, 1.0)),
                ),
                receiver_face=receiver_face,
                blob_density=f32(rng.random()),
            )


def test_exact_selected_and_attenuation_output_bits_match() -> None:
    transform = Transform(Vec3(3.25, -1.5, 7.75), 1, 1.25)
    scene = scene_for_instance(
        77,
        (vertical_face(3.5, material=7, half_size=1.4),),
        transform=transform,
    )
    target = transform.point(Vec3(0.0, 0.0, 3.5))
    direction = vec_normalize(transform.rotate(Vec3(0.0, 0.0, 1.0)))
    ray = Ray(vec_sub(target, vec_mul(direction, 9.0)), direction, 20.0)
    late, early = assert_parity(
        scene,
        ray,
        Vec3(0.31, 0.87, -0.24),
        blob_density=0.413,
    )
    expected = late.output
    actual = early.output
    assert actual.valid and expected.valid
    assert actual.identity == expected.identity == (0, 0)
    assert actual.material == expected.material == 7
    assert actual.stable_anchor_bits == expected.stable_anchor_bits
    assert actual.legacy_face == expected.legacy_face == 77
    assert actual.depth_bits == expected.depth_bits
    assert actual.caster_id == expected.caster_id
    assert actual.caster_xz_bits == expected.caster_xz_bits
    assert actual.static_attenuation_bits == expected.static_attenuation_bits
    assert actual.final_attenuation_bits == expected.final_attenuation_bits


def test_whole_blas_reduction_at_first_face_2048() -> None:
    scene = scene_for_instance(
        2048,
        tuple(vertical_face(3.0 + index) for index in range(8)),
    )
    late, early = assert_parity(scene)
    assert late.work.blas_entries == 1
    assert late.work.face_materializations > 0
    assert late.work.local_face_row_loads > 0
    assert early.work.blas_entries == 0
    assert early.work.blas_node_pops == 0
    assert early.work.face_materializations == 0
    assert early.work.local_face_row_loads == 0
    assert early.work.world_point_transforms == 0
    assert early.work.triangle_tests == 0


def test_boundary_straddling_instance_materializes_only_legacy_2047() -> None:
    scene = scene_for_instance(
        2047,
        (
            vertical_face(3.0),
            vertical_face(4.0),
            vertical_face(5.0),
        ),
    )
    late, early = assert_parity(scene)
    assert late.output.legacy_face == early.output.legacy_face == 2047
    assert late.work.face_materializations == 3
    assert early.work.face_materializations == 1
    assert early.work.local_face_row_loads == 4
    assert early.work.world_point_transforms == 5
    assert early.work.triangle_tests == 2


def test_off_by_one_cap_mutant_is_destroyed_by_work_counters() -> None:
    # The face misses the ray, keeping output equal while exposing wasted work.
    scene = scene_for_instance(
        2048,
        (vertical_face(4.0, center_x=50.0),),
    )
    correct = select_static(
        scene, DEFAULT_RAY, DEFAULT_NORMAL, early=True
    )
    mutant = select_static(
        scene,
        DEFAULT_RAY,
        DEFAULT_NORMAL,
        early=True,
        policy=EarlyPolicy(cap_off_by_one=True),
    )
    assert mutant.output == correct.output
    assert correct.work.blas_entries == 0
    assert correct.work.face_materializations == 0
    assert mutant.work.blas_entries == 1
    assert mutant.work.face_materializations == 1
    assert mutant.work.local_face_row_loads == 4
    assert mutant.work.world_point_transforms == 5
    assert mutant.work.triangle_tests == 2


def test_wrong_whole_range_mutant_loses_admitted_2047_face() -> None:
    scene = scene_for_instance(
        2047,
        (
            vertical_face(3.0),
            vertical_face(4.0),
            vertical_face(5.0),
        ),
    )
    correct = select_static(
        scene, DEFAULT_RAY, DEFAULT_NORMAL, early=True
    )
    mutant = select_static(
        scene,
        DEFAULT_RAY,
        DEFAULT_NORMAL,
        early=True,
        policy=EarlyPolicy(wrong_whole_range=True),
    )
    assert correct.output.valid
    assert correct.output.legacy_face == 2047
    assert mutant.output != correct.output
    assert not mutant.output.valid
    assert mutant.work.blas_entries == 0


def test_deleted_q3_mask_resurrection_mutant_is_destroyed() -> None:
    scene = scene_for_instance(32, (vertical_face(4.0),))
    correct = select_static(
        scene, DEFAULT_RAY, DEFAULT_NORMAL, early=True
    )
    mutant = select_static(
        scene,
        DEFAULT_RAY,
        DEFAULT_NORMAL,
        early=True,
        policy=EarlyPolicy(resurrect_q3_rejected_faces=frozenset({32})),
    )
    assert correct.output.valid
    assert mutant.output != correct.output
    assert not mutant.output.valid


def test_nearest_depth_and_reversed_traversal_mutant_changes_winner() -> None:
    farther_low_legacy = Instance(
        0,
        Asset((vertical_face(10.0, material=3),)),
        10,
    )
    nearer_high_legacy = Instance(
        1,
        Asset((vertical_face(2.0, material=4),)),
        20,
    )
    scene = Scene(
        (farther_low_legacy, nearer_high_legacy),
    )
    correct = select_static(
        scene, DEFAULT_RAY, DEFAULT_NORMAL, early=True
    )
    mutant = select_static(
        scene,
        DEFAULT_RAY,
        DEFAULT_NORMAL,
        early=True,
        policy=EarlyPolicy(
            nearest_selection=True,
            reverse_traversal=True,
        ),
    )
    assert correct.output.identity == (0, 0)
    assert correct.output.legacy_face == 10
    assert mutant.output.identity == (1, 0)
    assert mutant.output.legacy_face == 20
    assert mutant.output != correct.output
