#!/usr/bin/env python3
"""FL-4691 retained rigid mover source contract.

This test is deliberately headless.  It checks the affine math that replaces
the old airplane and spider per-frame row builders, then checks that the local
mesh/part ABI is wired through every retained geometry consumer.  It does not
launch Godot, a game/server, a renderer, or a capture runner.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

import pytest


ROOT = Path(__file__).resolve().parents[2]
RIGID = ROOT / "godot_project/render/scene/geometry/dynamic/rigid/RigidMoverGeometry.gd"
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"
SPIDER = ROOT / "godot_project/scripts/creatures/SpiderPresentationBuilder.gd"
DB = ROOT / "godot_project/render/scene/SceneGeometryDB.gd"
ATLAS = ROOT / "godot_project/render/scene/surfaces/SurfaceNeighborhoodAtlas.gd"
VSB_HOST = ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"
VSB_SHADER = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
CAPTURE_SHADER = ROOT / "godot_project/render/backends/perspective/capture/perspective_probe_capture.glsl"
PRODUCT_SHADER = ROOT / "godot_project/render/product/cell_owner_product.glsl"
RIGID_SHADER = ROOT / "godot_project/render/scene/geometry/dynamic/rigid/rigid_mover_geometry.glslinc"
PIPELINE = ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd"


Vec3 = tuple[float, float, float]
Mat3 = tuple[tuple[float, float, float], ...]


def _v_add(a: Vec3, b: Vec3) -> Vec3:
    return tuple(x + y for x, y in zip(a, b))  # type: ignore[return-value]


def _v_sub(a: Vec3, b: Vec3) -> Vec3:
    return tuple(x - y for x, y in zip(a, b))  # type: ignore[return-value]


def _v_scale(a: Vec3, scale: float) -> Vec3:
    return tuple(value * scale for value in a)  # type: ignore[return-value]


def _mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(
        tuple(sum(a[row][k] * b[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _mat_vec(a: Mat3, v: Vec3) -> Vec3:
    return tuple(sum(a[row][k] * v[k] for k in range(3)) for row in range(3))  # type: ignore[return-value]


def _axis_angle(axis: Vec3, angle: float) -> Mat3:
    # Rodrigues, with the same column-vector convention as Godot Basis.
    x, y, z = axis
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def _assert_vec_close(actual: Vec3, expected: Vec3, *, eps: float = 1.0e-6) -> None:
    assert actual == pytest.approx(expected, abs=eps, rel=eps)


def _function_source(source: str, name: str) -> str:
    # Signatures may carry default Callable() arguments with nested parens,
    # so match the header with balanced parens instead of [^)]*.
    header = re.search(rf"\bfunc\s+{re.escape(name)}\s*\(", source, flags=re.S)
    assert header, f"missing function {name}"
    depth, index = 0, header.end() - 1
    while True:
        char = source[index]
        depth += (char == "(") - (char == ")")
        if depth == 0:
            break
        index += 1
    tail = re.match(r"\s*(?:->\s*[^:]+)?\s*:", source[index + 1 :], flags=re.S)
    assert tail, f"missing function {name}"
    start = header.start()
    next_function = re.search(r"\n(?:static\s+)?func\s+", source[index + 1 + tail.end() :])
    end = index + 1 + tail.end() + next_function.start() if next_function else len(source)
    return source[start:end]


def _glsl_function_source(source: str, name: str) -> str:
    match = re.search(
        rf"\b(?:bool|void|uint|float|vec[234]|uvec[234]|int)\s+{re.escape(name)}\s*\(",
        source,
        flags=re.S,
    )
    assert match, f"missing GLSL function {name}"
    opening = source.find("{", match.end())
    assert opening >= 0, f"missing body for GLSL function {name}"
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : position + 1]
    raise AssertionError(f"unterminated GLSL function {name}")


def test_airplane_root_and_prop_affine_decode_matches_retired_row_math() -> None:
    # The retired path scaled the model point, then rotated propeller faces in
    # Y/Z about a scaled pivot, then applied the presented airframe basis.  The
    # retained path publishes root + prop transforms; this is the exact affine
    # decomposition the GPU must decode.
    yaw, pitch, roll, offset = 1.17, -0.23, 0.41, -math.pi * 0.5
    prop_angle = 0.87
    scale = 1.7
    pivot: Vec3 = (1.85, 0.46, -0.09)
    origin: Vec3 = (-4.0, 2.0, 9.0)
    basis = _mat_mul(
        _axis_angle((0.0, 1.0, 0.0), yaw + offset),
        _mat_mul(
            _axis_angle((0.0, 0.0, 1.0), pitch),
            _axis_angle((1.0, 0.0, 0.0), -roll),
        ),
    )
    prop_rotation = _axis_angle((1.0, 0.0, 0.0), prop_angle)
    for model_point in ((0.2, 0.4, 0.6), (-2.1, 0.08, 0.3), (1.9, 0.5, -0.2)):
        scaled = _v_scale(model_point, scale)
        retired_local = _v_add(
            _mat_vec(prop_rotation, _v_sub(scaled, _v_scale(pivot, scale))),
            _v_scale(pivot, scale),
        )
        retired = _v_add(origin, _mat_vec(basis, retired_local))

        # root * Transform3D(prop_rotation, pivot - prop_rotation * pivot)
        # decoded in local coordinates, with the root carrying uniform scale.
        prop_translation = _v_sub(pivot, _mat_vec(prop_rotation, pivot))
        retained_local = _v_add(
            _mat_vec(prop_rotation, model_point), prop_translation
        )
        retained = _v_add(origin, _mat_vec(basis, _v_scale(retained_local, scale)))
        _assert_vec_close(retained, retired)


def test_spider_segment_part_affine_decode_matches_retired_corner_rows() -> None:
    # The old builder emitted four quads per segment.  The retained template
    # emits unit quads and stores start/side/up/direction in one part pose.
    start: Vec3 = (-0.31, 0.44, 0.12)
    end: Vec3 = (0.58, -0.16, 1.71)
    half_width, half_height = 0.075, 0.050
    direction = _v_sub(end, start)
    length_sq = sum(value * value for value in direction)
    if length_sq <= 1.0e-6:
        direction = (0.0, 0.0, 0.001)
    length = math.sqrt(sum(value * value for value in direction))
    unit_direction = _v_scale(direction, 1.0 / length)
    side_raw = (
        direction[1] * 1.0 - direction[2] * 0.0,
        direction[2] * 0.0 - direction[0] * 1.0,
        direction[0] * 0.0 - direction[1] * 0.0,
    )
    side_length = math.sqrt(sum(value * value for value in side_raw))
    side = (1.0, 0.0, 0.0) if side_length <= 1.0e-6 else _v_scale(side_raw, 1.0 / side_length)
    up_raw = (
        side[1] * unit_direction[2] - side[2] * unit_direction[1],
        side[2] * unit_direction[0] - side[0] * unit_direction[2],
        side[0] * unit_direction[1] - side[1] * unit_direction[0],
    )
    up_length = math.sqrt(sum(value * value for value in up_raw))
    up = _v_scale(up_raw, 1.0 / up_length) if up_length > 1.0e-12 and abs(up_raw[1] / up_length) >= 0.15 else (0.0, 1.0, 0.0)

    # Four corners in the old first segment face order: start+side+up,
    # start-side+up, end-side+up, end+side+up.
    old_corners = (
        _v_add(start, _v_add(_v_scale(side, -half_width), _v_scale(up, half_height))),
        _v_add(start, _v_add(_v_scale(side, half_width), _v_scale(up, half_height))),
        _v_add(end, _v_add(_v_scale(side, half_width), _v_scale(up, half_height))),
        _v_add(end, _v_add(_v_scale(side, -half_width), _v_scale(up, half_height))),
    )
    unit_corners = ((-1.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0))
    for point, expected in zip(unit_corners, old_corners):
        decoded = _v_add(
            start,
            _v_add(
                _v_add(_v_scale(side, point[0] * half_width), _v_scale(up, point[1] * half_height)),
                _v_scale(direction, point[2]),
            ),
        )
        _assert_vec_close(decoded, expected)


def test_virtual_face_address_is_positive_and_wired_end_to_end() -> None:
    rigid = RIGID.read_text(encoding="utf-8")
    host = VSB_HOST.read_text(encoding="utf-8")
    db = DB.read_text(encoding="utf-8")
    atlas = ATLAS.read_text(encoding="utf-8")
    assert "const FACE_TAG := 0x40000000" in rigid
    assert "const INSTANCE_SHIFT := 20" in rigid
    assert "FACE_TAG | (index << INSTANCE_SHIFT) | int(mesh.first_face)" in rigid
    assert "(first_face & RigidMoverGeometryScript.FACE_TAG) != 0" in db
    assert "source.pose_source = pose_source" in db
    assert "pose_source.poses[int(rows[base + 11])]" in atlas
    assert "((first_face & 0x40000000) != 0" in host
    assert "bytes.encode_u32(base + DYNAMIC_INSTANCE_FLAGS_OFFSET, 2 if (first_face & 0x40000000) != 0 else 0)" in host
    for shader in (VSB_SHADER, CAPTURE_SHADER, PRODUCT_SHADER):
        source = shader.read_text(encoding="utf-8")
        assert '#include "res://render/scene/geometry/dynamic/rigid/rigid_mover_geometry.glslinc"' in source
        assert "rigid_face_rows" in _expand_includes(source)


def test_rigid_mesh_is_the_only_airplane_spider_geometry_producer_per_frame() -> None:
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    spider = SPIDER.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    descriptor = _function_source(compositor, "_rigid_mover_descriptor")
    product = _function_source(spider, "build_spider_product")
    assert "_append_dynamic_mover_airplane" not in compositor
    assert "_append_dynamic_mover_spider" not in compositor
    assert "for face in range" not in descriptor
    assert "for corner in range" not in descriptor
    # FL-4907 N16: the key comes from the baked rig asset (mesh_template()).
    assert '"mesh_key": String(mesh_template().get("mesh_key", ""))' in product
    assert '"part_poses": part_poses' in product
    assert "_append_body_rows" not in spider
    assert "_append_leg_segment_rows" not in spider
    airplane = _function_source(world, "_append_one_airplane_source_fact")
    assert '"mesh_key": mesh_key' in airplane
    assert '"airframe_rows": airframe_rows' not in airplane


def test_rigid_shader_clamps_authored_shade_before_capture_and_product() -> None:
    # Boat source faces historically carry authored shade values 10..12 and the
    # deleted row appender clamped them in _append_dynamic_mover_face.  The
    # rigid decode must retain that old contract before b100 capture and Product
    # consume row-1.w.  This currently exposes the integration defect if the
    # shader only multiplies b.w by the instance shade.
    rigid = RIGID_SHADER.read_text(encoding="utf-8")
    assert "b=vec4(points[1],clamp(b.w*shade,0.0,1.0));" in re.sub(r"\s+", "", rigid)


def test_rigid_gpu_bindings_are_present_in_vsb_capture_and_product_owners() -> None:
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    pipeline = PIPELINE.read_text(encoding="utf-8")
    assert "for binding in [115,116,117]" in compositor
    assert "capture_binding_rids.merge(_rigid_banks)" in compositor
    assert "product_uniforms.append_array(_rigid_mover_uniforms())" in compositor
    assert "for binding in [115,116,117]" in pipeline


def test_rigid_shadow_walk_uses_mover_local_index_for_tagged_faces() -> None:
    product = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))
    function = _glsl_function_source(product, "vsb_retained_consider_dynamic_shadow_mover")
    # A tagged face address is 0x40000000 + slot << 20 + local face.  Applying
    # the legacy global-bank 512-face cutoff to that tagged value rejects every
    # rigid caster before rigid_face_rows() can decode it.
    assert "if (legacy_face >= 512u) continue;" not in function
    assert "rigid_face_address(first_face)" in function
    assert "local_face >= 512u" in function


def test_boat_identity_is_unique_per_server_world_item() -> None:
    boat = _function_source(WORLD.read_text(encoding="utf-8"), "_append_boat_source_facts")
    # The server owns one boat model per pickup item. A fixed 6200 identity
    # makes a second boat collide in SceneGeometryDB's (kind, source_id) map and
    # fails closed for the complete dynamic publication.
    assert '"source_id": 6200' not in boat
