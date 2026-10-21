import re
from pathlib import Path

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    return (ROOT / rel).read_text()


def _glsl_function(source: str, signature: str) -> str:
    """Return one complete GLSL function without depending on its neighbours."""
    start = source.index(signature)
    opening_brace = source.index("{", start)
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise ValueError(f"unterminated GLSL function: {signature}")


def _leaf_lane_contract_errors(product: str) -> list[str]:
    """Reject resurrection of the retired tree/falling-leaf runtime owner."""
    errors: list[str] = []
    retired_tokens = (
        "LeafParticleBank",
        "leaf_particle_bank",
        "vsb_emit_retained_leaf_bank",
        "vsb_product_consider_leaf_record",
        "atomicOr(",
        "PRODUCT_LEAF_LIVE_MASK_ENABLED",
        "LEAF_PARTICLE_MASK_WORDS",
        "LEAF_PARTICLE_MASK_ROW_BASE",
    )
    for token in retired_tokens:
        if token in product:
            errors.append(f"retired falling-leaf owner resurrected: {token}")
    return errors


def test_fl4408_inventory_logged_with_counts():
    failure_log = text("docs/FAILURE_LOG.md")
    assert "Operator approved implementation of the AtSaturn PixelCamera runtime-owner inventory" in failure_log
    assert "Scope count: 8 whole-function delete groups, 10 delete-current-role-and-reinsert-under-PixelCameraContext groups, 7 secondary runtime fact targets, 13 family rows" in failure_log


def test_compositor_pixel_camera_context_is_the_runtime_projection_boundary():
    src = text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "class PixelCameraContext" in src
    assert "func _build_pixel_camera_context(" in src
    assert "func _world_to_clip(" not in src
    assert "func _presentation_snap_offset_px(" not in src
    assert "camera_snap_error_pixels" not in src
    assert "func set_camera_snap_error_pixels(" not in src
    assert "func _actor_projection_target_size(" not in src
    assert "func _actor_projection_context_from_scene(" not in src
    assert "func publish_actor_sprite_rows_for_frame(actor_rows: Array)" in src
    assert "func set_actor_sprite_frame_rect_rows" not in src
    assert "_publish_actor_sprite_source_rows_for_scene(scene_data, view, pixel_ctx)" in src
    assert "_dispatch_splat(" not in src
    assert (
        "_vsb_view_receipt = _build_vsb_view_dispatch(\n"
        "\t\t\tpixel_ctx,\n\t\t\tview,\n\t\t\tlocal_light_glyph_count\n\t\t)"
        in src
    )
    assert "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)" in src
    assert "func _resolve_actor_sprite_rows_for_pixel_context(" in src
    render_body = src.split("func _render_callback(", 1)[1].split("\nfunc ", 1)[0]
    assert "_build_local_light_records(" in render_body
    assert "_pixel_camera_frame_index,\n\t\t\tpixel_ctx.frame_time_seconds,\n\t\t\tpixel_ctx,\n\t\t\tbonfire_anchor_valid_for_frame" in render_body
    assert render_body.index("_build_vsb_view_dispatch(") < render_body.index(
        "_dispatch_cell_owner_product("
    )
    assert "prepared_product_dispatch" in render_body
    assert "var pixel_target_size := pixel_target_size_for_frame" in render_body
    assert "_build_pixel_camera_context(scene_data, view, pixel_target_size" in render_body
    assert "func _dispatch_cell_owner_product(fb_size: Vector2i, push: PackedByteArray, depth_bind" not in src
    snap_body = src.split("func _pixel_camera_snap_state_from_projection(", 1)[1].split("\nfunc ", 1)[0]
    assert "if snap_target_size.y <= 0 or not projection.is_orthogonal():" in snap_body
    assert '"snapped_transform": cam_transform' in snap_body
    assert "_atsaturn_grid_snap_state(cam_transform, _pixel_camera_grid, snap_world)" in snap_body
    assert snap_body.count("_pixel_camera_grid = cam_transform") == 1
    assert "_pixel_camera_grid.basis = cam_transform.basis" in snap_body
    light_body = src.split("func _build_local_light_records(", 1)[1].split("\n\t# FL-4203: bonfire test anchor", 1)[0]
    firefly_call = "_add_firefly_records(candidates, glyph_candidates, frame_index, time_seconds)"
    assert light_body.index("_add_lighthouse_beam_records(candidates, time_seconds)") > light_body.index(firefly_call)
    firefly_branch = light_body.split("# --- Firefly producer", 1)[1].split("# --- Lighthouse beam producer", 1)[0]
    assert "baseline_feature_firefly_light" not in firefly_branch
    assert "_add_lighthouse_beam_records" not in firefly_branch
    product_dispatch_body = src.split("func _dispatch_cell_owner_product(", 1)[1].split("\nfunc ", 1)[0]
    assert "u_depth.binding = 2" not in product_dispatch_body
    assert "owner_depth_bind" not in product_dispatch_body
    assert "scene_depth_bind" not in product_dispatch_body
    assert "layout(set = 0, binding = 2) uniform sampler2D depth_tex;" not in text("godot_project/render/product/cell_owner_product.glsl")


def test_actor_rows_take_pixel_camera_yaw_not_raw_camera3d():
    world = text("godot_project/scripts/World.gd")
    stack = text("godot_project/scripts/visual/ActorLayerStack.gd")
    assert "current_pixel_camera_yaw" not in world
    assert "func _current_pixel_camera_yaw(" not in world
    assert "func _sync_actor_visuals_to_compositor()" in world
    actor_sync_body = world.split("func _sync_actor_visuals_to_compositor()", 1)[1].split("\nfunc ", 1)[0]
    assert "Camera3D" not in actor_sync_body
    assert "set_architectural_perspective_active" not in actor_sync_body
    assert "set_world_shadow_casters" not in actor_sync_body
    assert "get_viewport().get_visible_rect().size" not in actor_sync_body
    assert "publish_actor_sprite_rows_for_frame(actor_rows)" in actor_sync_body
    assert "publish_actor_sprite_rows_for_frame([])" in actor_sync_body
    assert "func _collect_actor_sprite_frame_rect_queue()" in world
    assert "func _actor_stack_frame_rect_rows(actor_node: Node, actor_kind: String" in world
    assert "current_actor_sprite_frame_rect_queue()" in world
    assert "func current_actor_sprite_frame_rect_queue()" in stack
    assert "if runtime_row.is_empty():" in stack
    assert "source_cell_facts.is_empty()" in stack
    assert "pixel_camera_yaw" not in stack
    assert "func _yaw_bucket_from_view_yaw(view_yaw: float)" not in stack
    assert "func _yaw_bucket(camera" not in stack
    assert "_select_sidecar_frame(" not in stack
    assert "global_transform" not in stack


def test_shadow_wake_blood_beam_old_owners_are_deleted_from_runtime_path():
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    wake_field = text("godot_project/render/effects/water/WakeTrailField.gd")
    world = text("godot_project/scripts/World.gd")
    shader = _expand_includes(
        text("godot_project/render/product/cell_owner_product.glsl")
    )
    player = text("godot_project/scripts/player.gd")

    assert "func set_world_shadow_casters(" not in compositor
    assert "func _ensure_world_shadow_buffer_synced(" not in compositor
    assert "func actor_shadow_caster_world_xz_list(" not in compositor
    assert re.search(r"\b_world_shadow_rd\b", compositor) is None
    assert "set_world_shadow_casters" not in world
    assert "WorldShadowCasterBuffer" not in shader
    assert "world_shadow_caster_fact_at_world" not in shader
    assert "dynamic_mover_shadow_density_at_world" in shader
    assert "dynamic_mover_shadow_fact_at_world" not in shader
    assert not re.search(r"\bstruct\s+ShadowFact\b", shader)

    assert "func _append_airplane_wake_dynamic_faces(" not in world
    assert "AIRPLANE_MAT_WAKE" not in world
    assert "_airplane_wake_samples" not in world
    assert "func _push_airplane_wake_source_sample(" not in world
    assert "func tick_foliage_leaves(" not in compositor
    assert "func _publish_wake_source_sample(" not in world
    assert "func push_wake_source_sample_at_server_time(xz: Vector2, server_time_s: float, source_id: int)" in compositor
    assert "func _dynamic_mover_publish_airplane_trails(ctx: PixelCameraContext" in compositor
    trail_body = compositor.split(
        "func _dynamic_mover_publish_airplane_trails(ctx: PixelCameraContext", 1
    )[1].split("\nfunc ", 1)[0]
    assert "if grounded:" in trail_body
    assert "push_wake_source_sample_at_server_time(Vector2(tail_world.x, tail_world.z), server_time_s, source_id)" in trail_body
    assert "_push_dynamic_airplane_vapor_sample(tail_world, server_time_s, source_id)" in trail_body
    assert "func set_player_world_yaw(" not in compositor
    assert "set_player_world_yaw(" not in world
    assert "_player_world_yaw" not in compositor
    assert "wake_forward" not in compositor
    assert "wake_tail_xz" not in compositor
    assert "_last_wake_tail_xz" not in compositor
    set_pose_body = compositor.split(
        "func set_player_world_xz_at_server_tick(", 1
    )[1].split("\nfunc ", 1)[0]
    compact_set_pose_body = re.sub(r"\s+", "", set_pose_body)
    assert (
        "wake_trail.push_sample("
        "xz,server_tick,server_tick_rate_hz,WakeTrailFieldScript.WAKE_SOURCE_PLAYER)"
    ) in compact_set_pose_body
    assert "WAKE_TRAIL_SAMPLES_PER_SOURCE := 8" in wake_field
    assert "WAKE_TRAIL_MAX_SOURCES := 4" in wake_field
    assert "WAKE_TRAIL_STRIDE := 8" in wake_field
    assert "WAKE_SOURCE_PLAYER := 1" in wake_field
    assert "WAKE_SOURCE_AIRPLANE := 2" not in compositor
    assert "var lane_by_source: Dictionary = {}" in wake_field
    assert "var source_by_lane: Dictionary = {}" in wake_field
    assert "func lane_for_source(source_id: int) -> int:" in wake_field
    assert "source_lane := clampi(source_id - 1" not in compositor
    assert "wake_source_capacity_full" in wake_field
    assert "oldest_source" not in wake_field
    assert "recycled_lane" not in wake_field
    assert "last_pushed_by_source" in wake_field
    assert "write_idx_by_source" in wake_field
    assert "var _wake_trail_write_idx: int" not in compositor
    assert "_wake_trail_last_pushed:" not in compositor
    ensure_wake_body = wake_field.split("func sync_for_bind(server_time_s: float) -> void:", 1)[1].split("\nfunc ", 1)[0]
    assert "_refresh_wake_trail_ages" not in compositor
    assert "set_current_server_time(server_time_s)" in ensure_wake_body
    assert "prune_expired_sources(server_time_s)" in ensure_wake_body
    assert "wake_trail.sync_for_bind(" in compositor
    assert "u_wake_clock.binding = 22" in compositor
    assert "func prune_expired_sources(server_time_s: float) -> void:" in wake_field
    assert "clear_lane(lane, source_id)" in wake_field
    assert "AIRPLANE_WAKE_SOURCE_ID := 2" not in world
    assert "AIRPLANE_SOURCE_ID_BASE := 2000" in world
    assert "func _stable_airplane_source_id_for_pickup(pickup_id: String) -> int:" in world
    assert "func _sync_airplane_source_id_registry() -> void:" in world
    assert "server_time_s, AIRPLANE_WAKE_SOURCE_ID" not in world
    assert "layout(set = 0, binding = 22, std140) uniform WakeClockBuffer" in shader
    assert "bool product_wake_live_row(" in shader
    assert "float age = wake_clock.value.x - point.w;" in shader
    assert "age > PRODUCT_WAKE_VISUAL_AGE_SECONDS" in shader
    wake_body = shader.split("ProductWakeFact product_wake_fact(vec2 world_xz) {", 1)[1].split(
        "\nProductWaterSample ", 1
    )[0]
    assert "predecessor_source_id == point_source_id" in wake_body
    assert "predecessor_sequence + 1u == point_sequence" in wake_body
    assert "fact.source_id = point_source_id" in shader
    assert "wake_trail.points" not in shader
    assert "fact.source_id = 1u" not in shader
    assert "AirplanePresentationHistory" not in world
    assert "_airplane_presentation_history" not in world
    assert "_airplane_world_items = _airplane_world_item_server_snapshots.duplicate(true)" in world
    assert "_latest_airplane_world_item" not in world
    assert "func _local_airplane_presentation() -> Dictionary:" in world
    assert "func _append_airplane_world_mesh_faces" not in world
    assert "func _dynamic_mover_publish_airplane_tail_effects(" not in compositor
    assert "wake_tail_world" not in compositor
    assert "push_wake_source_sample_at_server_time(Vector2(wake" not in compositor
    assert "func _dynamic_mover_publish_airplane_vapor(ctx: PixelCameraContext" not in compositor
    airplane_source_body = world.split("func _append_airplane_source_facts", 1)[1].split("\nfunc ", 1)[0]
    assert "_airplane_world_items" in airplane_source_body
    assert "Basis(Vector3.UP" not in airplane_source_body
    assert "tail_world" not in airplane_source_body
    assert "func _append_dynamic_mover_airplane(ctx: PixelCameraContext" in compositor
    airplane_body = compositor.split(
        "func _append_dynamic_mover_airplane(ctx: PixelCameraContext", 1
    )[1].split("\nfunc ", 1)[0]
    assert "_dynamic_mover_publish_airplane_trails(ctx, fact, basis, source_pos)" in airplane_body
    assert "func local_airplane_presentation_position()" in world
    position_body = world.split("func local_airplane_presentation_position()", 1)[1].split("\nfunc ", 1)[0]
    assert "_local_airplane_presentation()" in position_body
    yaw_body = world.split("func local_airplane_yaw()", 1)[1].split("\nfunc ", 1)[0]
    assert "_local_airplane_presentation()" in yaw_body
    assert "local_airplane_presentation_position" in player
    reticle_body = world.split("func _append_reticle_dynamic_source_facts", 1)[1].split("\nfunc ", 1)[0]
    assert "_local_airplane_presentation()" in reticle_body
    assert "Camera3D" not in reticle_body
    assert "global_transform" not in reticle_body
    assert "func _airplane_vfx_position_for_pickup(pickup_id: String) -> Dictionary:" in world
    assert "_airplane_vfx_position_for_pickup(prev_pickup_id, _snapshot_position(snapshot))" not in world
    assert "_airplane_vfx_position_for_pickup(plane_pickup_id, pos)" not in world
    assert "func _push_airplane_vapor_dynamic_sample(" not in world
    assert "func _push_dynamic_airplane_vapor_sample(world_pos: Vector3, server_time_s: float, source_id: int) -> void:" in compositor
    assert "func _push_airplane_smoke_dynamic_sample(pickup_id: String, source_id: int, world_pos: Vector3, intensity: float)" in world
    assert "func _push_airplane_explosion_sample(pickup_id: String, source_id: int, world_pos: Vector3)" in world
    assert "_push_airplane_smoke_dynamic_sample(pos, damage_ratio)" not in world
    assert "_push_airplane_explosion_sample(_snapshot_position(snapshot))" not in world
    assert "if (airframe_id == 4u) return OWNER_VAPOR;" not in shader
    assert "paint_kind == OWNER_VAPOR" not in shader
    assert not ROOT.joinpath("godot_project/shaders/terrain_cell_splat.glsl").exists()
    assert "product.kind = OWNER_VAPOR" not in shader
    # Vapor is a pair of secondary material lanes selected by one retained
    # mover-tree walk. Pin the composite selector and both independently
    # applied outputs so this contract cannot pass after either smoke family
    # loses its live Product edge.
    assert "VsbRetainedVaporSelections vsb_retained_select_vapor_effect_candidates(" in shader
    assert "void vsb_product_apply_vapor_style(" in shader
    retained_vapor_body = shader.split(
        "VsbRetainedVaporSelections vsb_retained_select_vapor_effect_candidates(", 1
    )[1].split("\nVsbRetained", 1)[0]
    assert "vsb_retained_consider_vapor_mover(" in retained_vapor_body
    assert "vsb_semantic_dynamic_tlas_ids.ids[leaf_slot]" in retained_vapor_body
    assert "return selected;" in retained_vapor_body
    vapor_style_body = shader.split("void vsb_product_apply_vapor_style(", 1)[1].split(
        "\nvoid ", 1
    )[0]
    assert "vapor.facts.actor_effect_kind != 4u" in vapor_style_body
    assert "material == 211u" in vapor_style_body
    assert "effect_ink = vec3(0.74, 0.86, 1.0);" in vapor_style_body
    product_finalize_body = shader.split(
        "ProbeSubcellProductRecord vsb_product_finalize_record(", 1
    )[1].split("\nProbeSubcellProductRecord ", 1)[0]
    assert "vsb_retained_select_vapor_effect_candidates(" in product_finalize_body
    assert "vsb_product_apply_vapor_style(\n\t\tvapor_layers.bonfire_smoke, record);" in product_finalize_body
    assert "vsb_product_apply_vapor_style(\n\t\tvapor_layers.airplane_vapor, record);" in product_finalize_body

    assert "func set_blood_marks(" not in compositor
    assert "func set_blood_events(" in compositor
    assert "set_blood_events" in world
    assert "actor_body_blood_fact" in shader
    assert "_blood_actor_local_hit_fact" in world
    assert "actor_local_u" in world
    assert "actor_local_v" in world
    assert "last_hit_body_zone" in text("colyseus_server/src/sim/world.ts")
    assert "last_hit_local_u" in text("colyseus_server/src/sim/world.ts")
    assert "last_hit_local_v" in text("colyseus_server/src/sim/world.ts")
    assert "bodyZone: 1" not in text("colyseus_server/src/sim/world.ts")
    assert "const BLOOD_MARK_STRIDE := 12" in compositor
    actor_blood_body = shader.split("BloodFact actor_body_blood_fact", 1)[1].split("\n\t// ---- FL-4231 local-light", 1)[0]
    assert "blood_fact_at_world(actor_foot_world(actor_row)" not in actor_blood_body
    assert "actor_foot_world(actor_row)" not in actor_blood_body
    assert "blood_marks.marks[i * 3 + 2]" in actor_blood_body
    assert "BloodFact blood = actor.present" in product_finalize_body
    assert "? actor_body_blood_fact(actor.row, actor.local_uv)" in product_finalize_body
    assert "record.r0.y != OWNER_SKY\n\t\t\t? blood_fact_at_world(effect_receiver, 0u)" in product_finalize_body
    assert "cell_owner_apply_blood_paint_intensity(" in product_finalize_body
    assert "actor_blood_fact.intensity > ground_blood_fact.intensity" not in shader

    assert "beam_depth >= world_depth" not in shader
    assert "beam_world.y <= receiver_world.y" not in shader
    assert "beam_axis_t <= 0.45" not in shader
    assert "axis_t <= 0.45" not in shader
    assert "lighthouse_projected_axis_coverage" not in shader
    assert "BEAM_RAY_VOLUME_STEPS" not in shader
    assert "float ray_t = clamp((d_dot_a * a_dot_r - d_dot_r) / denom, 0.0, max_ray_t);" in shader
    assert "facts.beam.present = true;" in shader
    assert "facts.beam.blend = SEC_BLEND_ADDITIVE;" in shader
    assert "facts.beam.kind" not in shader
    assert "facts.beam.source_id" not in shader
    assert "facts.beam.depth" not in shader
    assert "facts.beam.world" not in shader
    assert "facts.beam.aux" not in shader


def test_blood_scans_use_the_host_owned_complete_record_count():
    """Zero-mark frames must not scan the sixty-four-record b10 capacity."""
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    shader = text("godot_project/render/product/cell_owner_product.glsl")

    assert "const BLOOD_MARK_STRIDE := 12" in compositor
    assert "var _blood_mark_count: int = 0" in compositor
    blood_setter_body = compositor.split(
        "func set_blood_events(", 1
    )[1].split("\nfunc ", 1)[0]
    assert (
        "var complete_row_count: int = int(rows.size() / BLOOD_MARK_STRIDE)"
        in blood_setter_body
    )
    assert (
        "mini(BLOOD_MARK_MAX_COUNT, complete_row_count)" in blood_setter_body
    )
    assert "_blood_mark_count = copy_count" in blood_setter_body
    assert "if src_offset + j < rows.size()" not in blood_setter_body
    assert "float(_blood_mark_count),\n\t\tfloat(local_light_glyph_count)" in compositor

    blood_count_body = shader.split(
        "uint blood_mark_count_from_receipt()", 1
    )[1].split("\nBloodFact ", 1)[0]
    assert "product_vsb_frame.cloud_night.z" in blood_count_body
    assert "isnan(encoded_count)" in blood_count_body
    assert "isinf(encoded_count)" in blood_count_body
    assert "encoded_count > float(BLOOD_MARK_MAX_COUNT)" in blood_count_body
    assert "encoded_count == float(count) ? count : 0u" in blood_count_body

    ground_blood_body = shader.split(
        "BloodFact blood_fact_at_world", 1
    )[1].split("\n\tBloodFact actor_body_blood_fact", 1)[0]
    actor_blood_body = shader.split(
        "BloodFact actor_body_blood_fact", 1
    )[1].split("\n\t// ---- FL-4231 local-light", 1)[0]
    for blood_body in (ground_blood_body, actor_blood_body):
        assert "uint mark_count = blood_mark_count_from_receipt();" in blood_body
        assert "if (mark_count == 0u)" in blood_body
        assert "for (int i = 0; i < BLOOD_MARK_MAX_COUNT; i++)" in blood_body
        assert "if (uint(i) >= mark_count) break;" in blood_body
        assert blood_body.index(
            "if (uint(i) >= mark_count) break;"
        ) < blood_body.index("blood_marks.marks[i * 3 + 0]")


def test_local_light_glyph_scan_uses_the_successful_per_view_pack_count():
    """Binding 13 capacity must not masquerade as sixty-four live glyphs."""
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    product = text("godot_project/render/product/cell_owner_product.glsl")

    render_body = compositor.split(
        "func _render_callback(", 1
    )[1].split("\nfunc ", 1)[0]
    assert (
        "var local_light_glyph_count: int = _build_local_light_records("
        in render_body
    )
    assert (
        "_build_vsb_view_dispatch(\n"
        "\t\t\tpixel_ctx,\n\t\t\tview,\n\t\t\tlocal_light_glyph_count"
        in render_body
    )
    assert "_local_light_last_metrics" not in render_body

    builder = compositor.split(
        "func _build_local_light_records(", 1
    )[1].split("\n# FL-4265: arm a downwash", 1)[0]
    assert ") -> int:" in "func _build_local_light_records(" + builder
    assert builder.count("return -1") >= 3
    assert "not _local_light_glyph_record_rd.is_valid()" in builder
    assert (
        "_local_light_glyph_record_data.size()\n"
        "\t\t\t!= LOCAL_LIGHT_MAX_GLYPH_RECORDS"
        " * LOCAL_LIGHT_GLYPH_RECORD_BYTES"
        in builder
    )
    assert "return glyph_matched" in builder

    view_builder = compositor.split(
        "func _build_vsb_view_dispatch(", 1
    )[1].split("\nfunc ", 1)[0]
    assert "local_light_glyph_count: int" in view_builder
    assert "local_light_glyph_count < 0" in view_builder
    assert "local_light_glyph_count > LOCAL_LIGHT_MAX_GLYPH_RECORDS" in view_builder
    assert '"absence_reason": "local-light glyph publication rejected"' in view_builder
    assert "_local_light_last_metrics" not in view_builder

    serializer = compositor.split(
        "func _build_vsb_product_frame_receipt_bytes(", 1
    )[1].split("\nfunc ", 1)[0]
    assert "float(_blood_mark_count),\n\t\tfloat(local_light_glyph_count)" in serializer
    assert "_local_light_last_metrics" not in serializer

    count_reader = product.split(
        "uint local_light_glyph_count_from_receipt()", 1
    )[1].split("\nBloodFact ", 1)[0]
    assert "product_vsb_frame.cloud_night.w" in count_reader
    assert "isnan(encoded_count)" in count_reader
    assert "isinf(encoded_count)" in count_reader
    assert "encoded_count > float(LOCAL_LIGHT_MAX_GLYPH_RECORDS)" in count_reader
    assert "encoded_count == float(count) ? count : 0u" in count_reader

    glyph_scan = product.split(
        "EffectCellProduct compute_local_light_glyph_cell_product(", 1
    )[1].split("\nEffectCellProduct compute_lighthouse_beam_cell_product(", 1)[0]
    assert "uint glyph_count = local_light_glyph_count_from_receipt();" in glyph_scan
    assert "if (glyph_count == 0u) return product;" in glyph_scan
    assert "for (int i = 0; i < LOCAL_LIGHT_MAX_GLYPH_RECORDS; ++i)" in glyph_scan
    assert "if (uint(i) >= glyph_count) break;" in glyph_scan
    assert glyph_scan.index(
        "if (uint(i) >= glyph_count) break;"
    ) < glyph_scan.index("local_light_glyph_alpha(i)")


def test_shoreline_breaks_are_product_owned_not_fact_texture_owned():
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    world = text("godot_project/scripts/World.gd")
    loader = text("godot_project/scripts/map/A3DMapLoader.gd")
    extension_loader = text("godot_project/extensions/terrain_3d_a3d/A3DTerrain3DLoader.gd")
    shader = text("godot_project/render/product/cell_owner_product.glsl")

    assert "func _install_fl4216_shoreline_facts(" not in world
    assert "set_fl4216_shoreline_facts" not in compositor
    assert "fl4216_shoreline_fact_rd" not in compositor
    assert "_pending_fl4216_shoreline_fact" not in compositor
    assert "baseline_feature_water_shore_wave" not in compositor
    assert "water_shore_wave" not in compositor
    assert "build_fl4216_shoreline_fact_words" not in loader
    assert "build_fl4216_shoreline_fact_words" not in extension_loader
    assert "void product_fl4216_shore_fact" in shader
    assert "bool product_fl4216_break_active" in shader
    assert "s.break_active = product_fl4216_break_active(s, ambient_time, wave_stage);" in shader
    assert "if (s.break_active && s.break_dir >= 0 && s.break_stage >= 0)" in shader


def test_water_receiver_uses_pixel_camera_ray_not_cell_center():
    shader = text("godot_project/render/product/cell_owner_product.glsl")
    assert "vec2 product_water_world_xz(ivec2 cell)" not in shader
    assert "resolve_raster_splat_owner(" not in shader
    assert "bool vsb_product_prepare_water(" in shader
    water_body = shader.split("bool vsb_product_prepare_water(", 1)[1].split(
        "\nVsbProductCompileInput ", 1
    )[0]
    assert "record.world_position.xz" in water_body
    assert "semantic_cell = ivec2(floor(" in water_body
    assert "uint local_primitive = record.primitive_id - primitive_base;" in water_body
    assert "uvec2(local_primitive % dims.x, local_primitive / dims.x)" in water_body
    assert "input_value.selected_corner_heights_valid" in water_body
    assert "float selected_average_height = 0.25 * (" in water_body
    assert "max(WATER_SURFACE_Y - selected_average_height, 0.0);" in water_body
    assert "input_value.facts.water_horizon_mix = backstop" in water_body
    main_body = shader.split("void main()", 1)[1]
    assert "input_value.facts.distant_water_sky_paint" not in main_body
    assert "compile_probe_subcell_payload(" in main_body


def test_pixel_camera_product_y_contract_is_top_left_not_inverted():
    shader = text("godot_project/render/product/cell_owner_product.glsl")
    vsb = text("godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl")
    compositor = text("godot_project/render/ascii/raster/ascii_compositor.glsl")
    ascii_effect = text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert '\"screen_cell_origin\": \"top_left\"' in ascii_effect
    ray_ndc = "vec2 ndc = vec2(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0);"
    assert ray_ndc in shader
    assert ray_ndc in vsb
    assert "(ndc.y * 0.5 + 0.5) * fb_size.y" in shader
    assert "(1.0 - (ndc.y * 0.5 + 0.5)) * fb_size.y" not in shader
    assert "(ndc.y * 0.5 + 0.5) * target_size.y" in ascii_effect
    assert "(1.0 - (ndc.y * 0.5 + 0.5)) * target_size.y" not in ascii_effect
    assert "1.0 - uv.y * 2.0" not in compositor
    assert "1.0 - (ndc.y * 0.5 + 0.5)" not in compositor


def test_dead_probe_and_leaf_projection_apis_are_not_runtime_surfaces():
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    main = text("godot_project/scripts/Main.gd")
    world = text("godot_project/scripts/World.gd")
    vsb = text("godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl")
    assert "func collect_foliage_leaf_cells(" not in compositor
    assert "raw Camera3D proof mutators are deleted from runtime acceptance lanes" in main
    assert "func _fail_closed_fl4408_probe_acceptance(probe_name: String) -> void:" in main
    assert "func _fl4408_probe_acceptance_disabled(" not in main
    assert "FL4408_RUNTIME_OWNER_REPAIR_PROBES_DISABLED" not in main
    assert '_fail_closed_fl4408_probe_acceptance("--probe-sprite-orbit")' in main
    assert '_fail_closed_fl4408_probe_acceptance("--probe-live-owner-drift")' in main
    assert '_fail_closed_fl4408_probe_acceptance("--probe-splat-churn-camera-turn")' in main
    assert '_fail_closed_fl4408_probe_acceptance("--wake-demo")' in main
    assert '_fail_closed_fl4408_probe_acceptance("--fl4057-ab-yaw-capture")' not in main
    assert "if not _fail_closed_fl4408_probe_acceptance" not in main
    assert "if not _fl4408_probe_acceptance_disabled" not in main
    assert "func _run_lens_probe(" not in main
    assert "func _run_god_ray_probe(" not in main
    assert "func _god_ray_probe_hill_focus(" not in main
    assert "GodRayProbeCamera" not in main
    assert "_run_lens_probe()" not in main
    assert "_run_god_ray_probe()" not in main
    assert "func _run_probe_fl4270_perspective(" not in main
    assert "func _drive_fl4270_probe_camera(" not in main
    assert "func _run_fl4231_exact_pose_dump(" not in main
    assert "func _apply_fl4231_exact_projection(" not in main
    assert 'call_deferred("_run_fl4231_exact_pose_dump")' not in main
    assert "set_fl4231_actor_rows_probe_state" not in world
    assert "_fl4231_disable_actor_rows_for_probe" not in world
    assert "fl4231_actor_rows_probe" not in world
    assert "[] if _fl4231_disable_actor_rows_for_probe else" not in world
    assert "_append_debug_cube_faces" not in world
    assert "DEBUG_CUBE_MAT" not in world
    assert "--fl4270-debug-cube" not in world
    assert "NormalRoughnessProbeEffectScript" not in world
    assert "_normal_roughness_probe_effect" not in world
    assert "var normal_roughness_probe" not in world
    assert not ROOT.joinpath("godot_project/scripts/render/NormalRoughnessProbeEffect.gd").exists()
    assert not ROOT.joinpath("godot_project/shaders/ascii_normal_roughness_probe.glsl").exists()
    assert not ROOT.joinpath("godot_project/shaders/fl4129_reflected_terrain_product.glsl").exists()
    assert "--fl4231-actor-row-toggle-dump is deleted" not in main
    assert "normal-roughness probe capture is deleted" in main
    assert "func _run_normal_roughness_capture_probe(" not in main
    assert not ROOT.joinpath("godot_project/shaders/terrain_cell_splat.glsl").exists()
    assert "bool build_subcell_ray(uvec2 subcell, out SceneRay ray)" in vsb
    assert "scene_query_heightfield(ray, nearest)" in vsb


def test_actor_and_leaf_products_are_complete_before_final_paint():
    product = text("godot_project/render/product/cell_owner_product.glsl")
    compositor = text("godot_project/render/ascii/raster/ascii_compositor.glsl")

    assert "ActorCellProduct" in product
    assert "vsb_product_select_actor_paint_quad(" in product
    assert "ActorSourceCellFact" in product
    assert "actor_product_source_cell_fact(" in product
    assert "actor_product_glyph_from_authored_alpha(" not in product
    assert "actor_authored_cell_tex" in product
    assert "vec2 source_uv = local_uv;" in product
    assert "vec2 source_uv = vec2(local_uv.x, 1.0 - local_uv.y);" not in product
    assert "actor_product_glyph_from_sample_shape(" not in product
    assert "local_uv2_weighted" not in product
    assert "luma_variance" not in product
    assert "actor_sprite_atlas_tex" not in product
    assert "product_actor_atlas_bind" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "u_product_actor_atlas" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "texture(actor_sprite_atlas_tex" not in product
    assert "actor_atlas_sample_visible(" not in product
    actor_select_body = product.split("void vsb_product_select_actor_paint_quad(", 1)[1].split(
        "\nvoid ", 1
    )[0]
    assert "sample_color" not in actor_select_body
    assert "representative_actor_uv" not in product
    assert "uv_accum += actor_uv" not in product
    assert "local_uv_weighted" not in product
    assert "alpha_weight" not in product
    assert "actor_product_source_cell_fact(\n\t\t\t\t\t\trow, standee.local_uv)" in product
    assert "local_uv_weighted / alpha_weight" not in product
    assert "actor_product_glyph_from_coverage(" not in product
    assert "var rect_margin := 0.0" in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "ACTOR_REJECT_EMPTY_SOURCE" in product
    finalize_body = product.split("ProbeSubcellProductRecord vsb_product_finalize_record(", 1)[1].split(
        "\nProbeSubcellProductRecord ", 1
    )[0]
    assert "uint source_key = actor_source_key(actor.row);" in finalize_body
    assert "record.r0.y = OWNER_ACTOR_SPRITE;" in finalize_body
    assert "source_key, actor_direction_id(actor.row)" in finalize_body
    assert "uint(actor.row + 1)" not in finalize_body
    ascii_effect = text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "var actor_count_for_product := _render_actor_cell_count" in ascii_effect
    assert "maxi(_render_actor_cell_count, _pending_actor_cell_count)" not in ascii_effect
    assert "params.terrain_params.xy + vec2(cell)" not in actor_select_body
    assert "view_ray_for_pixel_center(" in actor_select_body

    assert "texture(actor_sprite_atlas_tex" not in compositor
    assert "actor_sprite_atlas_uv_for_product_local" not in compositor
    assert "case CO_OWNER_ACTOR_SPRITE:" not in compositor
    assert "FinalSubcellProductRecord product = finalized_subcell_products.products[cell_index * 4u + slot];" in compositor
    assert "resolved.glyph = glyph;" in compositor
    assert "resolved.fg = clamp(product.fg.rgb" in compositor
    assert "resolved.bg = clamp(product.bg.rgb" in compositor
    assert "actor_sample" not in compositor
    assert "actor_shadow" not in compositor
    assert "actor_blood" not in compositor

    assert _leaf_lane_contract_errors(product) == []
    assert "compute_static_tree_leaf_secondary_fact(" not in product
    assert "float trace_radius = mix(1.85, 3.10" not in product
    assert "final_kind = OWNER_PARTICLE;" not in product
    assert "int(cell_product_data.decode_u32(co + 0)) == 9" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "leaf_particle_id: Variant = cell_owner_sec_source_id" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "cell_owner_leaf_product_source_id" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "EffectCellProduct" in product
    assert "vsb_product_collect_local_light_slot_facts(" in product
    assert "facts.beam = empty_effect_cell_product();" in product
    assert "EffectCellProduct beam = local_light_facts.beam;" in product
    assert "compute_vapor_cell_product(" not in product
    assert "vsb_retained_select_vapor_effect_candidates(" in product
    assert "vsb_product_apply_vapor_style(" in product
    assert "actor_attack_swoosh_cell_product(" in product
    assert "actor_dash_trail_cell_product(" in product
    compact_product = re.sub(r"\s+", "", product)
    assert (
        "actor_dash_trail_cell_product("
        "introw,ivec2product_cell_center_px)"
    ) in compact_product
    assert "actor.dash_row,ivec2(cell_center_px)" in compact_product
    assert "actor.dash_depth" not in product
    assert "actor.dash_row, actor.local_uv" not in product
    assert "apply_effect_cell_product_to_paint(" in product
    assert "apply_selected_secondary_product_to_cell(" not in product
    assert "secondary_effect_priority(" not in product
    assert "select_secondary_effect_candidate(" not in product
    assert "compute_local_light_secondary_fact(" not in product
    assert "effect_product_metadata_score(" not in product
    assert "record_effect_product_metadata(" not in product
    assert "if (c.r18.x == OWNER_NONE)" not in product
    assert "vapor claims the single secondary slot" not in product

    assert "void composite_secondary_effect(" not in compositor
    assert "composite_secondary_effect(" not in compositor
    assert "u_actor_sprite_atlas" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "layout(set = 0, binding = 8) uniform sampler2D depth_tex;" not in compositor
    assert "depth_tex" not in compositor
    assert "imageLoad(color_image" not in compositor
    assert "raw_sprite_band_at" not in compositor
    assert "raw_terrain_band_at" not in compositor
    assert "fetch_elev(" not in compositor
    assert "fetch_height(" not in compositor
    assert "u_depth.binding = 8" not in text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "u_depth.add_id(depth_bind)" not in text("godot_project/render/api/ASCIICompositorEffect.gd")


def test_leaf_lane_rejects_retired_runtime_resurrection():
    product = text("godot_project/render/product/cell_owner_product.glsl")
    mutant = product + "\nstruct LeafParticleBank { uint resurrected; };\n"
    assert mutant != product
    errors = _leaf_lane_contract_errors(mutant)
    assert any("retired falling-leaf owner resurrected" in error for error in errors)


def test_capture_readback_uses_pixel_camera_context_contract():
    main = text("godot_project/scripts/Main.gd")
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    world = text("godot_project/scripts/World.gd")
    harness = text("godot_project/tools/proofs/run_headed_gameplay_harness.gd")
    menu = text("godot_project/scripts/MainMenu.gd")
    live_movement = text("godot_project/tools/validation/validate_live_movement.gd")
    dogfight = text("godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd")

    assert "var _last_pixel_camera_capture_contract: Dictionary = {}" in compositor
    assert "func _record_pixel_camera_capture_contract(ctx: PixelCameraContext) -> void:" in compositor
    assert "func get_last_pixel_camera_capture_contract() -> Dictionary:" in compositor
    assert "func normalize_pixel_camera_capture_image(image: Image) -> Image:" in compositor
    assert "func capture_pixel_camera_viewport_image(viewport: Viewport) -> Image:" in compositor
    assert "_record_pixel_camera_capture_contract(pixel_ctx)" in compositor
    assert '"screen_cell_origin": "top_left"' in compositor
    assert '"product_row_origin": "top_left"' in compositor
    assert '"viewport_readback_row_origin": "top_left"' in compositor
    assert '"normalized_image_row_origin": "top_left"' in compositor
    assert '"readback_flip_y": false' in compositor
    assert 'get("readback_flip_y", false)' not in compositor
    assert "normalized.flip_y()" not in compositor
    assert "product_rows_are_flipped_from_image" not in compositor
    assert 'get("product_row_origin", "top_left")' in compositor
    assert 'product_row_origin == "bottom_left"' not in compositor
    assert "var product_y := y" in compositor

    assert "func _pixel_camera_capture_image() -> Image:" in main
    assert "capture_pixel_camera_viewport_image" in main
    assert "func _write_pixel_camera_capture_metadata(path: String, image_size: Vector2i) -> void:" in main
    capture_body = main.split("func _pixel_camera_capture_image() -> Image:", 1)[1].split(
        "\nfunc ", 1
    )[0]
    assert "capture_pixel_camera_viewport_image" in capture_body
    assert "get_texture().get_image()" not in capture_body

    assert "capture_pixel_camera_viewport_image" in world
    assert "pixel_camera_capture_contract" in world
    assert "build_final_render_cell_rows(final_image: Image)" in compositor
    assert "build_final_render_cell_rows(final_image, camera" not in world
    assert "build_final_render_dump_metadata(\n\t\tgit_head: String,\n\t\tdirty: bool,\n\t\tviewport_size: Vector2i" in compositor
    assert "capture_pixel_camera_viewport_image" in harness
    assert "get_texture().get_image()" not in harness
    assert "get_texture().get_image()" not in menu
    assert "PixelCameraContext capture owner unavailable" in menu
    assert "capture_pixel_camera_viewport_image" in live_movement
    assert 'const DEFAULT_PROJECTION_MODES := ["orthographic", "perspective"]' in live_movement
    assert "--movement-projection-modes=" in live_movement
    assert '"pixel_camera_contract": _pixel_camera_contract_snapshot()' in live_movement
    assert '"render_cpu_ms": RenderingServer.viewport_get_measured_render_time_cpu(vp)' in live_movement
    assert '"render_gpu_ms": RenderingServer.viewport_get_measured_render_time_gpu(vp)' in live_movement
    assert "get_texture().get_image()" not in live_movement
    assert "capture_pixel_camera_viewport_image" in dogfight
    assert "get_texture().get_image()" not in dogfight
    assert "pixel_camera_capture_owner_unavailable" in dogfight


def test_camera_turn_perf_driver_records_projection_modes_and_pacing():
    perf = text("godot_project/tools/profiling/run_camera_turn_perf.gd")
    assert 'const DEFAULT_PROJECTION_MODES: Array[String] = ["orthographic", "perspective"]' in perf
    assert "--camera-turn-perf-projection-modes=" in perf
    assert "func _run_projection_mode(mode: String) -> Dictionary:" in perf
    assert 'player.call("set_force_perspective", mode == "perspective")' in perf
    assert '"projection_modes": projection_modes' in perf
    assert '"mode_results": mode_results' in perf
    assert '"pixel_camera_contract": _pixel_camera_contract_snapshot()' in perf
    assert '"stage_timing": _stage_timing_snapshot()' in perf
    assert "func _stage_timing_summary(stage_timing_samples: Array[Dictionary]) -> Dictionary:" in perf
    assert '"render_cpu_ms": RenderingServer.viewport_get_measured_render_time_cpu(vp)' in perf
    assert '"render_gpu_ms": RenderingServer.viewport_get_measured_render_time_gpu(vp)' in perf


def test_bird_flock_consumes_pixel_camera_contract_not_camera3d():
    world = text("godot_project/scripts/World.gd")
    bird = text("godot_project/scripts/visual/BirdFlock3D.gd")
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "func set_camera(" not in bird
    assert "Camera3D" not in bird
    assert "get_viewport()" not in bird
    assert "unproject_position" not in bird
    assert ".projection" not in bird
    assert ".fov" not in bird
    assert "func set_pixel_camera_contract(contract: Dictionary)" not in bird
    assert "_pixel_camera_contract" not in bird
    assert "_contract_vector3(" not in bird
    assert "_contract_vector2(" not in bird
    assert "func _sync_bird_flock_pixel_camera_contract()" not in world
    assert "set_pixel_camera_contract" not in world
    assert "append_dynamic_source_facts" in bird
    assert "bird_faces = int(_bird_flock.call(\"append_dynamic_source_facts\", dynamic_source_facts))" in world
    assert '_ascii_effect.call("set_dynamic_mover_source_facts", dynamic_source_facts, dynamic_face_count)' in world
    # FL-4907 N16: a bird is one retained rigid mover; the CPU face path is deleted.
    assert "_append_dynamic_bird_face" not in compositor
    assert '"kind": "bird_face", "mesh_key"' in bird
    build_body = compositor.split(
        "func _build_dynamic_mover_faces_for_pixel_context(", 1
    )[1].split("\nfunc ", 1)[0]
    assert 'if kind in ["airplane", "spider", "ambient_mesh", "bird_face"]:' in build_body
    assert "_rigid_mover_descriptor(ctx, fact)" in build_body
    publish_body = compositor.split(
        "func _publish_dynamic_mover_source_facts_for_pixel_context(ctx: PixelCameraContext)", 1
    )[1].split("\nfunc ", 1)[0]
    assert "_build_dynamic_mover_faces_for_pixel_context(ctx, true)" in publish_body
    assert '"set_dynamic_mover_descriptors"' in publish_body
    assert "set_camera" not in world.split("func _sync_bird_flock_gate()", 1)[1].split("func _ensure_visual_source_root()", 1)[0]


def test_texel_splat_full_is_deleted_from_fl4408_runtime_owner():
    compositor = text("godot_project/render/api/ASCIICompositorEffect.gd")
    assert "texel_splat_full" not in compositor
    assert "TEXEL_SPLAT_FULL" not in compositor
    assert "_texel_splat_full" not in compositor
    assert "_pixel_camera_frame_index" in compositor
