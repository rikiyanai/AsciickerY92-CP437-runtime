#!/usr/bin/env python3
"""Guard airplane visibility across the activated retained-scene pipeline.

FL-4431 physically deleted the terrain-cell splat shader and the legacy
``product_airplane_hit_for_ray`` scan that these tests originally inspected.
The same FL-4265 requirements now cross three explicit owners:

* Visible Surface Builder intersects retained dynamic faces and publishes a
  mover-local primitive identity for the nearest hit.
* Product reconstructs that identity from retained facts and requires its typed
  airplane selector to agree before it authors the final subcell record.
* The compositor reads that one finalized record and only turns it into pixels.

These are source contracts. They prove that the current owner chain is present
and that the retired owner remains absent; they do not claim a runtime image.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VSB = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
LEGACY_SPLAT = ROOT / "godot_project/shaders/terrain_cell_splat.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"
PLAYER = ROOT / "godot_project/scripts/player.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"


def source(path: Path) -> str:
    """Read one UTF-8 source file used by the structural assertions."""

    return path.read_text(encoding="utf-8")


def strip_glsl_comments(text: str) -> str:
    """Remove GLSL comments so prose cannot satisfy an executable-code pin."""

    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)


def glsl_function_body(text: str, name: str) -> str:
    """Return one complete GLSL function, including its signature and braces.

    A brace walk is used instead of an arbitrary "next function" marker because
    the current shaders contain multiline signatures and their function order
    changes as retired owners are physically deleted.
    """

    code = strip_glsl_comments(text)
    signature = re.search(
        rf"(?m)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*[ \t]+)+"
        rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        code,
    )
    assert signature is not None, f"missing GLSL function: {name}"
    opening_brace = code.find("{", signature.start())
    depth = 0
    for offset in range(opening_brace, len(code)):
        if code[offset] == "{":
            depth += 1
        elif code[offset] == "}":
            depth -= 1
            if depth == 0:
                return code[signature.start() : offset + 1]
    raise AssertionError(f"unterminated GLSL function: {name}")


def normalized(text: str) -> str:
    """Collapse formatting whitespace while retaining executable token order."""

    return " ".join(text.split())


def function_body(text: str, signature: str, next_signature: str) -> str:
    """Slice one GDScript function between two stable neighbouring signatures."""

    start = text.index(signature)
    end = text.index(next_signature, start)
    return text[start:end]


def dynamic_writer_contract_holds(vsb: str) -> bool:
    """Check the retained dynamic writer's mover-local identity contract."""

    leaf = glsl_function_body(vsb, "scene_query_dynamic_leaf")
    compact_leaf = normalized(leaf)
    return all(
        (
            "uint mover_local_face_index = face_index - instance.ranges.x;"
            in compact_leaf,
            (
                "candidate_precedes( hit_depth, OWNER_DYNAMIC_MESH, object_id, "
                "mover_local_face_index, best )"
            )
            in compact_leaf,
            "best.primitive_id = mover_local_face_index;" in compact_leaf,
            "best.owner_kind = OWNER_DYNAMIC_MESH;" in compact_leaf,
        )
    )


def product_airplane_handoff_contract_holds(product: str) -> bool:
    """Check that Product consumes VSB identity without a second selector."""

    prepare = glsl_function_body(product, "vsb_product_prepare_dynamic")
    compact = normalized(prepare)
    required = (
        "vsb_retained_mover_actor_effect_facts(",
        "input_value.retained_face = face;",
        "input_value.context.entity = vsb_product_entity_word(",
    )
    if not all(token in compact for token in required):
        return False
    forbidden = (
        "vsb_retained_select_airplane_hit(",
        "vsb_retained_select_dynamic_candidate(",
        "vsb_product_identity_equal(",
        "product_ray_triangle_t(",
    )
    if any(token in compact for token in forbidden):
        return False
    # Source order is identity-keyed point lookup, retained-face adoption, then
    # entity publication. Product does not rediscover the geometry winner.
    positions = [compact.index(token) for token in required]
    return positions == sorted(positions)


def single_product_paint_contract_holds(product: str, compositor: str) -> bool:
    """Check the one-store producer and one-input compositor ownership chain."""

    product_code = strip_glsl_comments(product)
    compositor_code = strip_glsl_comments(compositor)
    product_main = normalized(glsl_function_body(product, "main"))
    compositor_main = normalized(glsl_function_body(compositor, "main"))
    producer_index = "uint final_index = cell_index * 4u + slot;"
    producer_store = (
        "finalized_probe_subcell_products.products[final_index] = final_record;"
    )
    consumer_load = (
        "FinalSubcellProductRecord product = "
        "finalized_subcell_products.products[cell_index * 4u + slot];"
    )
    pixel_store = "imageStore(color_image, pos, vec4(color, 1.0));"
    # Membership is checked before positional comparison so a destructive
    # mutation returns a clean False instead of making the test helper crash.
    if producer_index not in product_main or producer_store not in product_main:
        return False
    if consumer_load not in compositor_main or pixel_store not in compositor_main:
        return False
    return all(
        (
            len(
                re.findall(
                    r"\bfinalized_probe_subcell_products\s*\.\s*products\s*\[",
                    product_code,
                )
            )
            == 1,
            product_main.count(producer_index) == 1,
            product_main.index(producer_index) < product_main.index(producer_store),
            compositor_code.count(
                "layout(set = 0, binding = 44, std430) readonly buffer"
            )
            == 1,
            len(
                re.findall(
                    r"\bfinalized_subcell_products\s*\.\s*products\s*\[",
                    compositor_code,
                )
            )
            == 1,
            compositor_main.count(consumer_load) == 1,
            compositor_main.index(consumer_load) < compositor_main.index(pixel_store),
        )
    )


def test_airplane_is_deleted_from_legacy_ansi_center_splat_owner() -> None:
    # Deletion is the ownership boundary: no dormant legacy shader is allowed
    # to coexist with the retained-scene replacement.
    assert not LEGACY_SPLAT.exists()

    vsb = source(VSB)
    main = normalized(glsl_function_body(vsb, "main"))
    dynamic_tlas = normalized(
        glsl_function_body(vsb, "scene_query_dynamic_tlas_prevalidated")
    )

    # One frozen subcell ray reaches the retained dynamic TLAS before the other
    # scene families. A hit is then serialized by the same VSB invocation.
    ray_build = "build_subcell_ray(subcell, ray)"
    dynamic_query = "scene_query_dynamic_tlas(ray, 0u, nearest)"
    static_query = "scene_query_static_tlas(ray, nearest)"
    final_write = "visible_surface_products.records[record_index] = hit_record(nearest);"
    assert main.index(ray_build) < main.index(dynamic_query)
    assert main.index(dynamic_query) < main.index(static_query)
    assert main.index(static_query) < main.index(final_write)
    assert "dynamic_tlas_leaf_ids.ids[0], required_kind_id, best" in dynamic_tlas
    assert "instance_index, required_kind_id, best" in dynamic_tlas
    assert dynamic_writer_contract_holds(vsb)


def test_sample_ray_and_final_cell_use_one_authored_face_intersection_owner() -> None:
    vsb = source(VSB)
    product = source(PRODUCT)
    leaf = normalized(glsl_function_body(vsb, "scene_query_dynamic_leaf"))
    compile_input = normalized(glsl_function_body(product, "vsb_product_compile_input"))
    prepare_dynamic = normalized(
        glsl_function_body(product, "vsb_product_prepare_dynamic")
    )

    # VSB owns the primary face intersection. Both triangles use the published
    # row0-row2 diagonal, then the winning mover-local identity crosses b64.
    assert (
        "intersect_triangle( ray, row_0.xyz, row_1.xyz, row_2.xyz, "
        "best.depth, depth_a, normal_a )"
    ) in leaf
    assert (
        "intersect_triangle( ray, row_0.xyz, row_2.xyz, row_3.xyz, "
        "best.depth, depth_b, normal_b )"
    ) in leaf
    assert "best.primitive_id = mover_local_face_index;" in leaf

    # Product routes the b64 dynamic identity through one identity-keyed
    # retained handoff. It must not re-intersect the ray or elect another face.
    assert "owner_valid = vsb_product_prepare_dynamic(record, input_value);" in compile_input
    assert product_airplane_handoff_contract_holds(product)
    assert "vsb_retained_mover_actor_effect_facts(" in prepare_dynamic
    assert "product_ray_triangle_t(" not in prepare_dynamic

    executable_product = strip_glsl_comments(product)
    assert "product_airplane_hit_for_ray(" not in executable_product
    assert "product_dynamic_mesh_hit_for_face(" not in executable_product
    assert "product_world_hit_for_cell(" not in executable_product
    assert "vsb_retained_select_airplane_hit(" not in executable_product
    assert "vsb_product_identity_equal(" not in executable_product


def test_product_dynamic_handoff_uses_retained_identity_point_lookup() -> None:
    product = source(PRODUCT)
    lookup = normalized(
        glsl_function_body(product, "vsb_retained_dynamic_face_local_facts")
    )
    prepare = normalized(
        glsl_function_body(product, "vsb_product_prepare_dynamic")
    )

    # VSB's mover-local object/primitive identity directly addresses one
    # retained face. Material and stable anchor are validation facts, not a new
    # winner election.
    assert "vsb_retained_dynamic_range(identity.object_id, mover_range)" in lookup
    assert "identity.primitive_id >= mover_range.z" in lookup
    assert "uint face_index = mover_range.y + identity.primitive_id;" in lookup
    assert "facts.material_id != identity.material" in lookup
    assert "vsb_retained_anchor_matches(" in lookup
    assert "vsb_retained_mover_actor_effect_facts(" in prepare
    assert "vsb_retained_select_dynamic_shadow_caster(" not in prepare
    assert "vsb_retained_select_vapor_effect_candidates(" not in prepare


def test_completed_airplane_product_survives_perspective_final_paint() -> None:
    product = source(PRODUCT)
    compositor = source(COMPOSITOR)
    compositor_main = normalized(glsl_function_body(compositor, "main"))

    # Product has exactly one final store for both projection families. The
    # compositor reads the matching four-record-per-cell index at binding 44.
    assert single_product_paint_contract_holds(product, compositor)
    assert "layout(set = 0, binding = 55, std430) writeonly buffer" in product
    assert "layout(set = 0, binding = 44, std430) readonly buffer" in compositor
    assert "const uint CO_OWNER_DYNAMIC_MESH = 3u;" in compositor

    product_load = (
        "FinalSubcellProductRecord product = "
        "finalized_subcell_products.products[cell_index * 4u + slot];"
    )
    projection_paint = "bool perspective_paint = compositor_uses_perspective_paint();"
    pixel_store = "imageStore(color_image, pos, vec4(color, 1.0));"
    assert compositor_main.index(product_load) < compositor_main.index(projection_paint)
    assert compositor_main.index(projection_paint) < compositor_main.index(pixel_store)

    # Projection now changes only the ink grammar after the finalized record is
    # loaded. No perspective-only geometry owner and no probe fallback survives.
    assert "perspective_route" not in strip_glsl_comments(compositor)
    assert "probe_product" not in strip_glsl_comments(compositor)
    assert "resolve_cell_owner_product(" not in strip_glsl_comments(compositor)


def test_current_owner_contract_rejects_identity_handoff_and_store_mutants() -> None:
    """Prove that representative regressions make the structural pins go red."""

    vsb = source(VSB)
    product = source(PRODUCT)
    compositor = source(COMPOSITOR)
    assert dynamic_writer_contract_holds(vsb)
    assert product_airplane_handoff_contract_holds(product)
    assert single_product_paint_contract_holds(product, compositor)

    # Mutation 1 revives publication-global face identity. Reordering a different
    # mover would then renumber the airplane and destabilize its visible key.
    global_identity_mutant = vsb.replace(
        "best.primitive_id = mover_local_face_index;",
        "best.primitive_id = face_index;",
        1,
    )
    assert not dynamic_writer_contract_holds(global_identity_mutant)

    # Mutation 2 injects the retired Product-side selector after the valid VSB
    # identity handoff. That creates a second geometry owner and must fail.
    stale_selector_mutant = product.replace(
        "uint legacy_face = face.legacy_face_index;",
        (
            "VsbRetainedDynamicSelection airplane =\n"
            "\t\tvsb_retained_select_airplane_hit(input_value.ray);\n"
            "\tif (!airplane.valid) return false;\n"
            "\tuint legacy_face = face.legacy_face_index;"
        ),
        1,
    )
    assert not product_airplane_handoff_contract_holds(stale_selector_mutant)

    # Mutation 3 adds a second final Product store. Both writes could compile,
    # yet compositor pixels would no longer have one authoritative producer.
    duplicated_store_mutant = product.replace(
        "finalized_probe_subcell_products.products[final_index] = final_record;",
        (
            "finalized_probe_subcell_products.products[final_index] = final_record;\n"
            "finalized_probe_subcell_products.products[final_index] = final_record;"
        ),
        1,
    )
    assert not single_product_paint_contract_holds(
        duplicated_store_mutant, compositor
    )

    # Mutation 4 shifts the compositor to a neighbouring quarter-cell while
    # preserving valid-looking data and a fully compiling shader.
    shifted_consumer_mutant = compositor.replace(
        "finalized_subcell_products.products[cell_index * 4u + slot]",
        "finalized_subcell_products.products[cell_index * 4u + ((slot + 1u) & 3u)]",
        1,
    )
    assert not single_product_paint_contract_holds(product, shifted_consumer_mutant)


def test_mount_transition_applies_airplane_perspective_zoom_before_chase_return() -> None:
    player = source(PLAYER)
    body = function_body(
        player,
        "func _apply_pilot_camera_projection(piloting: bool) -> void:",
        "## Processes mouse-look rotation",
    )

    transition = "var pilot_transition := piloting != _pilot_camera_active"
    assert transition in body
    assert body.index(transition) < body.index("if _chase_camera_active():")
    assert "if _perspective_active():" in body
    assert "_apply_projection_default_zoom(true)" in body


def test_server_mount_truth_alone_hides_mounted_pilot_bodies() -> None:
    world = source(WORLD)
    local_body = function_body(
        world,
        "func _should_hide_local_pilot_body() -> bool:",
        "func _should_hide_actor_pilot_body(actor_id: int) -> bool:",
    )
    actor_body = function_body(
        world,
        "func _should_hide_actor_pilot_body(actor_id: int) -> bool:",
        "func _should_hide_npc_pilot_body(npc_id: int) -> bool:",
    )
    npc_body = function_body(
        world,
        "func _should_hide_npc_pilot_body(npc_id: int) -> bool:",
        "func last_actor_frame_rect_row_count() -> int:",
    )

    assert "func _has_published_airplane_body_replacement()" not in world
    assert "_last_airplane_dynamic_face_count" not in local_body
    assert "_last_airplane_dynamic_face_count" not in actor_body
    assert "_last_airplane_dynamic_face_count" not in npc_body
    assert "return _local_pilot_hidden" in local_body
    assert "return _airplane_pilot_actor_ids.has(actor_id)" in actor_body
    assert "return _airplane_pilot_npc_ids.has(npc_id)" in npc_body
