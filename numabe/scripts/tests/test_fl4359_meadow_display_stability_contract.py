#!/usr/bin/env python3
"""FL-4359 meadow member-law contract.

One member law, product_meadow_member, selects the printed meadow stroke for
both projections. Ortho Product (product_compile_mesh_material) and the
perspective display stage (fl4359_source_effects_apply) call the same
function with the same captured inputs: root anchor, receiver, captured wind
front and bend. Row comes from the world-anchored height fraction with the
0.70/0.75 role thresholds; column comes from the lateral world offset from
the bent plant axis; mirror is the planted heading sign. No observer, extent,
projection, or screen mapping may enter the glyph decision, so camera-only
movement cannot re-select the stroke in either projection. Wind, contact,
trail, and rain still re-select the stroke through the shared world-anchored
front and bend.

The deleted product_meadow_stamp projected root/tip/receiver/heading through
the live observer and quantised them to screen cells (orthographic shimmer);
the deleted product_meadow_display_member duplicated the law behind
FL4359_SOURCE_EFFECTS with a mirror read from dot(lean, perp(lean)) == 0.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
MESH_MATERIAL = ROOT / "godot_project/render/product/material/product_mesh_material.glslinc"
DISPLAY = ROOT / "godot_project/render/product/adapters/perspective/probe_source_display.glslinc"

from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    return _expand_product_includes(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def _function_body(source: str, name: str) -> str:
    start = source.index("{", source.index(name + "("))
    depth, end = 1, start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    body = source[start + 1 : end - 1]
    return re.sub(r"//[^\n]*", "", body)


def _member() -> str:
    return _function_body(_product_source(), "uint product_meadow_member")


def test_one_member_law_no_split_authoring() -> None:
    source = _product_source()
    assert source.count("uint product_meadow_member(") == 1
    for deleted in ("product_meadow_stamp(", "product_meadow_display_member("):
        assert deleted not in re.sub(r"//[^\n]*", "", source), deleted
    # The law is unconditional: every Product variant compiles the same body.
    guard = source.index("uint product_meadow_member(")
    open_ifdef = source.rfind("#ifdef FL4359_SOURCE_EFFECTS", 0, guard)
    close_endif = source.rfind("#endif", 0, guard)
    assert open_ifdef < close_endif


def test_member_takes_no_observer_or_extent() -> None:
    source = _product_source()
    signature = source[
        source.index("uint product_meadow_member(") :
        source.index("{", source.index("uint product_meadow_member("))
    ]
    for token in ("Observer", "observer", "extent", "projection", "mat4", "span"):
        assert token not in signature, token
    assert "vec3 root" in signature and "vec3 receiver" in signature


def test_member_consumes_no_camera_state() -> None:
    member = _member()
    for forbidden in (
        "observer", "inv_view_proj", "world_to_pixel", "probe_face",
        "camera_origin", "camera_forward", "camera_right", "camera_up",
        "ndc", "raster", "extent", "params.", "view_identity",
        "screen_projection", "pixel",
    ):
        assert forbidden not in member, forbidden


def test_member_owns_the_role_thresholds_and_wind_law() -> None:
    member = _member()
    assert "fraction > 0.75" in member and "fraction > 0.70" in member
    assert "length(top_displacement.xz)" in member
    assert 'cell_owner_weather_is_rain(cell_owner_weather_kind()) ? 0.22 : 0.0' in member


def test_member_mirror_is_a_planted_trait() -> None:
    member = _member()
    assert "bool mirrored = clump.heading_shade_lag.x < 0.0;" in member
    assert "dot(lean, column_axis)" not in member


def test_member_decision_carries_no_printed_glyph() -> None:
    member = _member()
    assert "decision = uvec3(species, 4u |" in member
    for forbidden in ("record.r0.z", "payload.x", "printed"):
        assert forbidden not in member, forbidden
    assert "uint(column + 8) | (uint(member_row) << 8u) | (phase << 16u)" in member
    assert "decision = uvec3(0u);" in member
    assert "authored_flower_symbol(species - 4u)" in member


def test_both_projections_call_the_same_law_with_captured_inputs() -> None:
    mesh = MESH_MATERIAL.read_text(encoding="utf-8")
    ortho = mesh[
        mesh.index("paint.glyph = product_meadow_member(") :
        mesh.index("colour_role == 2u", mesh.index("paint.glyph = product_meadow_member("))
    ]
    assert "context.anchor_world, context.trace_receiver" in ortho
    assert "context.vegetation_pose.w, context.vegetation_pose.xyz" in ortho
    for forbidden in ("observer", "cell_dims", "extent", "projection"):
        assert forbidden not in ortho, forbidden
    display = DISPLAY.read_text(encoding="utf-8")
    call = display[
        display.index("glyph = product_meadow_member(") :
        display.index("colour_role == 2u", display.index("glyph = product_meadow_member("))
    ]
    assert "root, world" in call
    assert "vegetation_pose.w, vegetation_pose.xyz" in call
    for forbidden in ("observer", "native_extent", "raster", "projection"):
        assert forbidden not in call, forbidden


def main() -> int:
    test_one_member_law_no_split_authoring()
    test_member_takes_no_observer_or_extent()
    test_member_consumes_no_camera_state()
    test_member_owns_the_role_thresholds_and_wind_law()
    test_member_mirror_is_a_planted_trait()
    test_member_decision_carries_no_printed_glyph()
    test_both_projections_call_the_same_law_with_captured_inputs()
    print("FL4359_MEADOW_MEMBER_LAW_CONTRACT PASS checks=7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
