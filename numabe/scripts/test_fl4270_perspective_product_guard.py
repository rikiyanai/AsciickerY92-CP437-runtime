#!/usr/bin/env python3
"""Prove perspective presentation consumes VSB instead of rescanning geometry.

FL-4270 originally guarded the probe-raster presentation boundary.  FL-4431
deleted that route.  The equivalent retained boundary is stricter: VSB alone
selects a surface, Product validates the frozen projection identity, and the
compositor paints the finalized record without a geometry query.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VSB = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"
HOST = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
BUILDER = ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _function_body(source: str, name: str) -> str:
    match = re.search(
        r"\b\w+(?:\s+\w+)*\s+" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
        source,
        flags=re.S,
    )
    assert match is not None, f"missing GLSL function {name}"
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"unterminated GLSL function {name}")


def _branch_block(source: str, marker: str) -> str:
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated branch {marker}")


def main() -> int:
    vsb = VSB.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    host = HOST.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")

    clean_vsb = _without_comments(vsb)
    main_body = _function_body(clean_vsb, "main")
    current_query = _function_body(clean_vsb, "query_current_camera_surface")

    assert "bool build_subcell_ray(uvec2 subcell, out SceneRay ray)" in vsb
    # FL-4359 cutover: VSB builds orthographic rays only. The perspective
    # ray branch is deleted; a perspective projection fails closed.
    sample_ray = _function_body(clean_vsb, "build_sample_ray")
    assert "PROJECTION_PERSPECTIVE" not in sample_ray
    assert "push_data.projection_kind == PROJECTION_ORTHOGRAPHIC" in sample_ray
    assert "fl4359_pending_source_epoch" not in clean_vsb
    assert "query_current_camera_surface(" in main_body
    # FL-4359 cutover: the retained candidate bank (b61/b62/b63) and its
    # decoder are deleted. main() reads no retained candidate; in a perspective
    # view VSB is not dispatched at all (the texel graph owns the frame).
    assert main_body.count("probe_candidate_to_hit(") == 0
    assert "probe_candidate_records" not in clean_vsb
    # Neither site may hand `nearest` to the decoder as its out-parameter. A
    # retained candidate must land in its own variable first so that the
    # continuity criterion can still refuse it; writing straight into `nearest`
    # would make every decoded candidate final geometry unconditionally.
    assert "probe_candidate_to_hit(record_index, ray, nearest" not in re.sub(
        r"\s+", " ", main_body
    )
    assert "query_perspective_water_source(" not in clean_vsb
    for primary_query in (
        "scene_query_heightfield(",
        "scene_query_static_tlas(",
        "scene_query_dynamic_tlas(",
        "scene_query_water(",
    ):
        assert primary_query in current_query
    assert "const RECORD_STRIDE_BYTES := 96" in builder
    assert "PROBE_CONTROL_" not in builder
    assert "vsb_visible_surface_products.products[visible_index]" in product
    assert "record.projection_kind != product_vsb_frame.view_identity.z" in product
    # Product retains secondary-effect selectors whose names include
    # `vsb_retained_scene_query_`. The primary surface queries below are the
    # deleted probe-raster owners and must remain exclusive to VSB.
    for primary_query in (
        "scene_query_heightfield(",
        "scene_query_static_tlas(",
        "scene_query_dynamic_tlas(",
    ):
        assert primary_query not in product

    # ------------------------------------------------------------------
    # FL-4359 cutover: no retained arm, no hybrid admission, one owner.
    # ------------------------------------------------------------------
    # The old VSB retained-owner substitution, the stable-identity hybrid
    # (Product constant 23), and the b61/b62/b63 candidate bank are deleted.
    # A perspective view never dispatches VSB; the texel graph (capture ->
    # shade -> splat -> publish) is the sole perspective world author.
    compact_vsb = re.sub(r"\s+", "", clean_vsb)
    clean_product = _without_comments(product)
    compact_product = re.sub(r"\s+", "", clean_product)
    assert "VSB_FL4359_RETAINED_OWNER_ENABLED" not in compact_vsb
    assert "fl4359_retained_surface_continuous" not in compact_vsb
    assert "PRODUCT_FL4359_STABLE_IDENTITY_ADMISSION_ENABLED" not in compact_product
    assert "fl4359_stable_identity_epoch_admitted" not in compact_product
    assert "_fl4359_retained_owner_opt_in" not in host
    assert "FL4359_RETAINED_EPOCH_ADMISSION_ENABLED" not in host
    # Product admits DIRECT unconditionally and admits a retained texel epoch
    # (EYE/CURRENT/PREVIOUS) only on a perspective record built by the shade.
    assert (
        "record.projection_kind==VSB_PROJECTION_PERSPECTIVE" in compact_product
    )
    assert (
        "if(source_epoch!=VSB_STATUS_SOURCE_EPOCH_DIRECT"
        "&&!fl4359_retained_epoch_admitted)" in compact_product
    )
    # The host dispatches VSB and the screen-cell Product only outside a
    # perspective view, and refuses the binding 51 contract when the texel
    # graph did not publish this frame.
    assert "if fl4359_perspective_view:" in host
    assert "_fl4359_refuse_resolve_contract(" in host

    clean_compositor = _without_comments(compositor)
    assert "finalized_subcell_products.products[" in clean_compositor
    assert "scene_query_" not in clean_compositor
    assert clean_compositor.count("imageStore(") == 1

    vsb_bind = "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)"
    product_call = "_dispatch_cell_owner_product("
    compositor_bind = (
        "rd.compute_list_bind_compute_pipeline(\n"
        "\t\t\tcompute_list,\n"
        "\t\t\tcompositor_pipeline_for_frame\n"
        "\t\t)"
    )
    assert host.index(vsb_bind) < host.index(product_call) < host.index(compositor_bind)
    assert host.count("rd.compute_list_add_barrier(product_compute_list)") == 5

    print("FL4270_RETAINED_PERSPECTIVE_PRESENTATION_GUARD_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
