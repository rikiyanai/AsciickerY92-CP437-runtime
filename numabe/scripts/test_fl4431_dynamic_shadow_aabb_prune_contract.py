#!/usr/bin/env python3
"""FL-4431 dynamic-shadow TLAS AABB-prune destructive contract.

The shipping Product selector currently opens every dynamic TLAS node in
shadow mode with ``entry = 0.0``.  The intended replacement rejects a node
only when its published world AABB cannot contain a face with positive shadow
density under the existing five-unit radial and vertical influence laws.

This file is deliberately red-first: the executable exhaustive/pruned witness
and all five destructive mutants pass now, while the source-shape test remains
red until the Product shader adopts the pinned predicate.  This is arithmetic
and structural evidence.  It does not execute Metal, measure FPS, or establish
visual acceptance.
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

import pytest


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

ACTOR_SHADOW_ELEVATION_THRESHOLD = 0.6
DYNAMIC_SHADOW_MAX_ALTITUDE = 42.0
DYNAMIC_SHADOW_MAX_RADIUS = 5.0
EFFECT_KIND_SHADOW_CASTER = 1
RANDOM_SEED = 4431
RANDOM_SCENES = 96
RANDOM_RECEIVERS_PER_SCENE = 12


def f32(value: float) -> float:
    """Round one arithmetic result to the shader's IEEE-754 float width."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def float_bits(value: float) -> int:
    """Return the exact uint32 payload used by GLSL floatBitsToUint."""
    return struct.unpack("<I", struct.pack("<f", f32(value)))[0]


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Face:
    """One retained mover face reduced to the shadow law's consumed facts."""

    primitive_id: int
    center: Vec3
    box_min: Vec3
    box_max: Vec3
    effect_kind: int = EFFECT_KIND_SHADOW_CASTER


@dataclass(frozen=True)
class Mover:
    """One stable mover identity plus its published containing AABB."""

    object_id: int
    faces: tuple[Face, ...]
    box_min: Vec3
    box_max: Vec3


@dataclass(frozen=True)
class Node:
    """Small CPU TLAS witness with the same binary AABB ownership."""

    box_min: Vec3
    box_max: Vec3
    left: "Node | None" = None
    right: "Node | None" = None
    mover: Mover | None = None


@dataclass(frozen=True)
class Selection:
    """The exact Product result fields this optimization may not change."""

    identity: tuple[int, int] | None
    density_bits: int


@dataclass(frozen=True)
class PrunePolicy:
    """Mutation knobs; DEFAULT_POLICY is the sole accepted replacement law."""

    radius_expansion: float = DYNAMIC_SHADOW_MAX_RADIUS
    lower_boundary_inclusive: bool = False
    max_altitude: float = DYNAMIC_SHADOW_MAX_ALTITUDE
    radius_boundary_inclusive: bool = False


DEFAULT_POLICY = PrunePolicy()


def extract_glsl_function(source: str, name: str) -> str:
    """Return one complete GLSL function by balancing nested braces."""
    match = re.search(
        r"\b[\w\s]+\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
        source,
        re.S,
    )
    if match is None:
        return ""
    start = source.find("{", match.start())
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    return ""


def min_vec(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z))


def max_vec(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z))


def mover_from_faces(object_id: int, faces: tuple[Face, ...]) -> Mover:
    """Publish the componentwise union required by the retained descriptor."""
    assert faces
    box_min = faces[0].box_min
    box_max = faces[0].box_max
    for face in faces[1:]:
        box_min = min_vec(box_min, face.box_min)
        box_max = max_vec(box_max, face.box_max)
    return Mover(object_id, faces, box_min, box_max)


def build_tlas(movers: tuple[Mover, ...]) -> Node:
    """Build a deterministic binary union tree without reordering leaves."""
    assert movers
    if len(movers) == 1:
        mover = movers[0]
        return Node(mover.box_min, mover.box_max, mover=mover)
    split = len(movers) // 2
    left = build_tlas(movers[:split])
    right = build_tlas(movers[split:])
    return Node(
        min_vec(left.box_min, right.box_min),
        max_vec(left.box_max, right.box_max),
        left,
        right,
    )


def glsl_length2(dx: float, dz: float) -> float:
    """Float32 model of GLSL length(vec2)."""
    xx = f32(f32(dx) * f32(dx))
    zz = f32(f32(dz) * f32(dz))
    return f32(math.sqrt(f32(xx + zz)))


def glsl_smoothstep(edge0: float, edge1: float, value: float) -> float:
    """Float32 model of the scalar smoothstep used by the density law."""
    t = f32(f32(value - edge0) / f32(edge1 - edge0))
    t = f32(min(max(t, 0.0), 1.0))
    return f32(f32(t * t) * f32(3.0 - f32(2.0 * t)))


def face_density(
    receiver: Vec3,
    face: Face,
    *,
    radius_scale: float = 0.36,
) -> float:
    """Execute the shipping dynamic-mover density arithmetic in float32."""
    if face.effect_kind != EFFECT_KIND_SHADOW_CASTER:
        return f32(0.0)
    if f32(receiver.y - face.center.y) > f32(
        ACTOR_SHADOW_ELEVATION_THRESHOLD
    ):
        return f32(0.0)
    altitude = f32(max(f32(face.center.y - receiver.y), 0.0))
    altitude_intensity = f32(
        min(
            max(
                f32(1.0 - f32(altitude / f32(DYNAMIC_SHADOW_MAX_ALTITUDE))),
                0.0,
            ),
            1.0,
        )
    )
    if altitude_intensity <= 0.0:
        return f32(0.0)
    # Shipping derives radius from the XZ footprint only. Including the face's
    # vertical extent here would let a tall airframe inflate the witness radius
    # even though the shader's `caster_max - caster_min` value is a vec2.
    diagonal = glsl_length2(
        f32(face.box_max.x - face.box_min.x),
        f32(face.box_max.z - face.box_min.z),
    )
    radius = f32(
        f32(f32(diagonal * f32(radius_scale)) + f32(0.75))
        + f32(altitude * f32(0.05))
    )
    radius = f32(min(max(radius, 1.0), DYNAMIC_SHADOW_MAX_RADIUS))
    distance = glsl_length2(
        f32(receiver.x - face.center.x),
        f32(receiver.z - face.center.z),
    )
    density = f32(
        f32(1.0 - glsl_smoothstep(0.0, radius, distance))
        * altitude_intensity
    )
    return f32(density)


def identity_precedes(
    candidate: tuple[int, int],
    selected: tuple[int, int],
) -> bool:
    """Mirror the stable mover-id then local-face tie key."""
    return candidate < selected


def consider(
    selected_identity: tuple[int, int] | None,
    selected_density: float,
    mover: Mover,
    face: Face,
    receiver: Vec3,
    *,
    stable_ties: bool = True,
    radius_scale: float = 0.36,
) -> tuple[tuple[int, int] | None, float]:
    density = face_density(receiver, face, radius_scale=radius_scale)
    identity = (mover.object_id, face.primitive_id)
    denser = density > selected_density
    exact_tie_earlier = (
        stable_ties
        and selected_identity is not None
        and float_bits(density) == float_bits(selected_density)
        and identity_precedes(identity, selected_identity)
    )
    if not denser and not exact_tie_earlier:
        return selected_identity, selected_density
    return identity, density


def exhaustive_select(
    movers: tuple[Mover, ...],
    receiver: Vec3,
    *,
    reverse_order: bool = False,
    stable_ties: bool = True,
    radius_scale: float = 0.36,
) -> Selection:
    """Execute the current shadow-mode law over every mover face."""
    selected_identity: tuple[int, int] | None = None
    selected_density = f32(0.0)
    mover_order = tuple(reversed(movers)) if reverse_order else movers
    for mover in mover_order:
        face_order = (
            tuple(reversed(mover.faces)) if reverse_order else mover.faces
        )
        for face in face_order:
            selected_identity, selected_density = consider(
                selected_identity,
                selected_density,
                mover,
                face,
                receiver,
                stable_ties=stable_ties,
                radius_scale=radius_scale,
            )
    return Selection(selected_identity, float_bits(selected_density))


def point_aabb_xz_distance(receiver: Vec3, node: Node) -> float:
    closest_x = min(max(receiver.x, node.box_min.x), node.box_max.x)
    closest_z = min(max(receiver.z, node.box_min.z), node.box_max.z)
    return glsl_length2(
        f32(receiver.x - closest_x),
        f32(receiver.z - closest_z),
    )


def shadow_node_can_contribute(
    receiver: Vec3,
    node: Node,
    policy: PrunePolicy = DEFAULT_POLICY,
) -> bool:
    """Exact conservative predicate required in the shipping selector."""
    lower_limit = f32(
        receiver.y - ACTOR_SHADOW_ELEVATION_THRESHOLD
    )
    lower_rejected = (
        node.box_max.y <= lower_limit
        if policy.lower_boundary_inclusive
        else node.box_max.y < lower_limit
    )
    if lower_rejected:
        return False
    if node.box_min.y >= f32(receiver.y + policy.max_altitude):
        return False
    distance = point_aabb_xz_distance(receiver, node)
    if policy.radius_boundary_inclusive:
        return distance <= f32(policy.radius_expansion)
    return distance < f32(policy.radius_expansion)


def pruned_select(
    root: Node,
    receiver: Vec3,
    *,
    policy: PrunePolicy = DEFAULT_POLICY,
    reverse_children: bool = False,
    stable_ties: bool = True,
    radius_scale: float = 0.36,
) -> tuple[Selection, int]:
    """Traverse retained bounds, preserving the existing face selector."""
    stack = [root]
    selected_identity: tuple[int, int] | None = None
    selected_density = f32(0.0)
    visited_faces = 0
    while stack:
        node = stack.pop()
        if not shadow_node_can_contribute(receiver, node, policy):
            continue
        if node.mover is not None:
            faces = (
                tuple(reversed(node.mover.faces))
                if reverse_children
                else node.mover.faces
            )
            for face in faces:
                visited_faces += 1
                selected_identity, selected_density = consider(
                    selected_identity,
                    selected_density,
                    node.mover,
                    face,
                    receiver,
                    stable_ties=stable_ties,
                    radius_scale=radius_scale,
                )
            continue
        assert node.left is not None and node.right is not None
        children = (
            (node.right, node.left)
            if reverse_children
            else (node.left, node.right)
        )
        # Match the shader's LIFO walk: append in source order, pop the latter.
        stack.extend(children)
    return Selection(selected_identity, float_bits(selected_density)), visited_faces


def make_face(
    primitive_id: int,
    center: Vec3,
    half_extent: Vec3,
    *,
    effect_kind: int = EFFECT_KIND_SHADOW_CASTER,
) -> Face:
    return Face(
        primitive_id,
        center,
        Vec3(
            center.x - half_extent.x,
            center.y - half_extent.y,
            center.z - half_extent.z,
        ),
        Vec3(
            center.x + half_extent.x,
            center.y + half_extent.y,
            center.z + half_extent.z,
        ),
        effect_kind,
    )


def parity_fixture() -> tuple[tuple[Mover, ...], tuple[Vec3, ...]]:
    """Boundary rows that force every inequality to justify itself."""
    movers = (
        # Receiver (0,0,0) lies outside this raw box but inside the face's
        # positive density radius. Missing the five-unit expansion loses it.
        mover_from_faces(
            40,
            (
                make_face(
                    0,
                    Vec3(1.1, 0.0, 0.0),
                    Vec3(0.1, 1.0, 1.0),
                ),
            ),
        ),
        # Exact lower vertical boundary remains eligible because shipping skips
        # only when receiver_y - caster_y is strictly greater than 0.6.
        mover_from_faces(
            20,
            (
                make_face(
                    0,
                    Vec3(0.0, -ACTOR_SHADOW_ELEVATION_THRESHOLD, 0.0),
                    Vec3(0.4, 0.0, 0.4),
                ),
            ),
        ),
        # Two equal candidates make stable identity observable under traversal
        # reversal. The lower mover id must win independently of encounter order.
        mover_from_faces(
            8,
            (make_face(3, Vec3(0.2, 0.0, 0.2), Vec3(0.5, 0.5, 0.5)),),
        ),
        mover_from_faces(
            9,
            (make_face(3, Vec3(0.2, 0.0, 0.2), Vec3(0.5, 0.5, 0.5)),),
        ),
        # Exact five-unit node distance has zero density at the hard-clamped
        # maximum radius. The strict predicate must reject it without a face read.
        mover_from_faces(
            70,
            (make_face(0, Vec3(5.5, 0.0, 0.0), Vec3(0.5, 10.0, 10.0)),),
        ),
    )
    receivers = (
        Vec3(0.0, 0.0, 0.0),
        Vec3(0.0, 1.5, 0.0),
        Vec3(2.75, -1.0, -0.25),
        Vec3(-4.0, 3.0, 1.0),
    )
    return movers, receivers


def randomized_scene(rng: random.Random, scene_index: int) -> tuple[Mover, ...]:
    movers: list[Mover] = []
    for mover_index in range(rng.randint(2, 12)):
        faces: list[Face] = []
        base = Vec3(
            rng.uniform(-30.0, 30.0),
            rng.uniform(-8.0, 50.0),
            rng.uniform(-30.0, 30.0),
        )
        for primitive_id in range(rng.randint(1, 5)):
            center = Vec3(
                base.x + rng.uniform(-2.0, 2.0),
                base.y + rng.uniform(-2.0, 2.0),
                base.z + rng.uniform(-2.0, 2.0),
            )
            half_extent = Vec3(
                rng.uniform(0.05, 4.0),
                rng.uniform(0.05, 4.0),
                rng.uniform(0.05, 4.0),
            )
            effect_kind = (
                EFFECT_KIND_SHADOW_CASTER
                if rng.random() < 0.72
                else 4
            )
            faces.append(
                make_face(
                    primitive_id,
                    center,
                    half_extent,
                    effect_kind=effect_kind,
                )
            )
        movers.append(
            mover_from_faces(scene_index * 100 + mover_index + 1, tuple(faces))
        )
    return tuple(movers)


def assert_selection_equal(expected: Selection, actual: Selection) -> None:
    assert actual.identity == expected.identity
    assert actual.density_bits == expected.density_bits


def test_shipping_source_is_red_until_exact_predicate_replaces_entry_zero() -> None:
    """Pin today's exhaustive branch and the complete accepted replacement."""
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    selector = extract_glsl_function(
        source, "vsb_retained_select_dynamic_candidate"
    )
    assert selector
    red_precondition = re.search(
        r"float\s+entry\s*=\s*selection_mode\s*==\s*0"
        r"\s*\?\s*0\.0\s*:\s*product_bvh_intersect_aabb",
        selector,
        re.S,
    )
    helper = extract_glsl_function(
        source, "vsb_retained_dynamic_shadow_node_can_contribute"
    )
    mover_consider = extract_glsl_function(
        source, "vsb_retained_consider_dynamic_mover"
    )
    replacement_shape = (
        helper
        and "box_max.y < receiver_world.y - ACTOR_SHADOW_ELEVATION_THRESHOLD"
        in helper
        and "box_min.y >= receiver_world.y + 42.0" in helper
        and "clamp(receiver_world.xz, box_min.xz, box_max.xz)" in helper
        and "distance(receiver_world.xz, closest_xz) < 5.0" in helper
        and "vsb_retained_dynamic_shadow_node_can_contribute(" in selector
        and "receiver_world, child.node_min, child.node_max" in selector
        # Internal-node pruning alone misses every leaf child plus the
        # single-leaf fast path. The retained mover record already owns its
        # exact published AABB, so shadow mode must apply the same predicate
        # before enumerating that mover's faces.
        and mover_consider
        and "selection_mode == 0" in mover_consider
        and "vsb_retained_dynamic_shadow_node_can_contribute(" in mover_consider
        and "receiver_world, mover.aabb_min.xyz, mover.aabb_max.xyz"
        in mover_consider
        and red_precondition is None
    )
    assert red_precondition is not None or helper, (
        "shipping shadow traversal matches neither the pinned red precondition "
        "nor the exact replacement"
    )
    assert replacement_shape, (
        "RED: shadow mode still opens every TLAS node with entry = 0.0; replace "
        "it with vsb_retained_dynamic_shadow_node_can_contribute(receiver_world, "
        "child.node_min, child.node_max), using XZ distance < 5.0, "
        "box_max.y < receiver_y - 0.6, and box_min.y >= receiver_y + 42.0; "
        "apply the same predicate to each retained mover AABB before its face loop"
    )


def test_exhaustive_and_pruned_selectors_match_boundary_cases() -> None:
    movers, receivers = parity_fixture()
    root = build_tlas(movers)
    for receiver in receivers:
        expected = exhaustive_select(movers, receiver)
        actual, _visited = pruned_select(root, receiver)
        assert_selection_equal(expected, actual)


def test_exhaustive_and_pruned_selectors_match_seeded_random_scenes() -> None:
    rng = random.Random(RANDOM_SEED)
    for scene_index in range(RANDOM_SCENES):
        movers = randomized_scene(rng, scene_index)
        root = build_tlas(movers)
        for _receiver_index in range(RANDOM_RECEIVERS_PER_SCENE):
            receiver = Vec3(
                rng.uniform(-35.0, 35.0),
                rng.uniform(-10.0, 55.0),
                rng.uniform(-35.0, 35.0),
            )
            expected = exhaustive_select(movers, receiver)
            actual, _visited = pruned_select(root, receiver)
            assert_selection_equal(expected, actual)


def test_mutant_missing_radius_expansion_is_destroyed() -> None:
    mover = mover_from_faces(
        1,
        (make_face(0, Vec3(1.1, 0.0, 0.0), Vec3(0.1, 1.0, 1.0)),),
    )
    receiver = Vec3(0.0, 0.0, 0.0)
    expected = exhaustive_select((mover,), receiver)
    mutant, _visited = pruned_select(
        build_tlas((mover,)),
        receiver,
        policy=PrunePolicy(radius_expansion=0.0),
    )
    assert expected.identity is not None
    assert mutant != expected


def test_mutant_wrong_vertical_interval_is_destroyed() -> None:
    mover = mover_from_faces(
        1,
        (make_face(0, Vec3(0.0, 20.0, 0.0), Vec3(0.4, 0.4, 0.4)),),
    )
    receiver = Vec3(0.0, 0.0, 0.0)
    expected = exhaustive_select((mover,), receiver)
    mutant, _visited = pruned_select(
        build_tlas((mover,)),
        receiver,
        policy=PrunePolicy(max_altitude=4.2),
    )
    assert expected.identity is not None
    assert mutant != expected


def test_mutant_strict_lower_boundary_rejection_is_destroyed() -> None:
    mover = mover_from_faces(
        1,
        (
            make_face(
                0,
                Vec3(
                    0.0,
                    f32(-ACTOR_SHADOW_ELEVATION_THRESHOLD),
                    0.0,
                ),
                Vec3(0.4, 0.0, 0.4),
            ),
        ),
    )
    receiver = Vec3(0.0, 0.0, 0.0)
    expected = exhaustive_select((mover,), receiver)
    mutant, _visited = pruned_select(
        build_tlas((mover,)),
        receiver,
        policy=PrunePolicy(lower_boundary_inclusive=True),
    )
    assert expected.identity is not None
    assert mutant != expected


def test_mutant_changed_traversal_order_with_first_hit_ties_is_destroyed() -> None:
    equal_face = make_face(
        0, Vec3(0.2, 0.0, 0.2), Vec3(0.5, 0.5, 0.5)
    )
    movers = (
        mover_from_faces(8, (equal_face,)),
        mover_from_faces(9, (equal_face,)),
    )
    receiver = Vec3(0.0, 0.0, 0.0)
    expected = exhaustive_select(movers, receiver)
    mutant, _visited = pruned_select(
        build_tlas(movers),
        receiver,
        stable_ties=False,
    )
    assert expected.identity == (8, 0)
    assert mutant.identity == (9, 0)
    assert mutant != expected


def test_mutant_altered_density_math_is_destroyed_by_float_bits() -> None:
    mover = mover_from_faces(
        1,
        (make_face(0, Vec3(0.2, 0.2, 0.2), Vec3(0.9, 1.1, 1.3)),),
    )
    receiver = Vec3(0.0, 0.0, 0.0)
    expected = exhaustive_select((mover,), receiver)
    mutant, _visited = pruned_select(
        build_tlas((mover,)),
        receiver,
        radius_scale=0.35,
    )
    assert mutant.identity == expected.identity
    assert mutant.density_bits != expected.density_bits


def test_strict_radius_boundary_rejects_zero_density_without_face_visit() -> None:
    mover = mover_from_faces(
        1,
        (make_face(0, Vec3(5.5, 0.0, 0.0), Vec3(0.5, 10.0, 10.0)),),
    )
    receiver = Vec3(0.0, 0.0, 0.0)
    expected = exhaustive_select((mover,), receiver)
    actual, visited = pruned_select(build_tlas((mover,)), receiver)
    inclusive_mutant, inclusive_visited = pruned_select(
        build_tlas((mover,)),
        receiver,
        policy=PrunePolicy(radius_boundary_inclusive=True),
    )
    assert_selection_equal(expected, actual)
    assert_selection_equal(expected, inclusive_mutant)
    assert visited == 0
    assert inclusive_visited == 1
