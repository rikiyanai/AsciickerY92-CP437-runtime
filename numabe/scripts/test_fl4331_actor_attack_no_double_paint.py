#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTOR_STACK = ROOT / "godot_project" / "scripts" / "visual" / "ActorLayerStack.gd"
PRESENTATION_STATE_MACHINE = ROOT / "godot_project" / "scripts" / "visual" / "ActorPresentationStateMachine.gd"
COMBAT_PRESENTATION_RIG = ROOT / "godot_project" / "scripts" / "visual" / "ActorCombatPresentationRig.gd"
COLYSEUS_ADAPTER = ROOT / "godot_project" / "scripts" / "net" / "ColyseusStateAdapter.gd"
COLYSEUS_SCHEMA = ROOT / "colyseus_server" / "src" / "rooms" / "schema" / "ActorSchema.ts"
COLYSEUS_ROOM = ROOT / "colyseus_server" / "src" / "rooms" / "AuthoritativeRoom.ts"
SIM_WORLD = ROOT / "colyseus_server" / "src" / "sim" / "world.ts"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def test_attack_visual_does_not_emit_size_scaled_duplicate_actor_row() -> None:
    src = ACTOR_STACK.read_text(encoding="utf-8")

    forbidden = [
        "_build_attack_swoosh_row",
        '"actor_attack_swoosh"',
        '"attack_swoosh"',
        "* pulse",
    ]
    for needle in forbidden:
        assert needle not in src, needle

    assert "var runtime_row := _build_runtime_row(sidecar, selected, layer, sidecar_path)" in src
    assert "variants[str(yaw_bucket)] = runtime_row" in src
    assert "rows.append(_build_pixel_camera_source_row(sidecar, layer, sidecar_path, variants))" in src


def test_attack_frame_uses_server_frame_linearly() -> None:
    stack = ACTOR_STACK.read_text(encoding="utf-8")
    presentation = PRESENTATION_STATE_MACHINE.read_text(encoding="utf-8")
    adapter = COLYSEUS_ADAPTER.read_text(encoding="utf-8")
    schema = COLYSEUS_SCHEMA.read_text(encoding="utf-8")
    room = COLYSEUS_ROOM.read_text(encoding="utf-8")
    sim_world = SIM_WORLD.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "return clampi(phase_frame % frame_count, 0, max_frame)",
        '"attack_phase_frame": int(_read_field(actor, "attack_phase_frame", frame))',
        "declare attack_phase_frame: number;",
        'attack_phase_frame: "number"',
        "export function attackPhaseFrame(actor: ActorModel): number",
        "schema.attack_phase_frame = attackPhaseFrame(actor);",
        '"visual_frame_index": int(resolved_frame.get("frame", _presentation_state_machine.current_frame_index(sidecar, layer)))',
        '"server_attack_phase_frame": _last_actor_state.attack_phase_frame',
        "words.append(visual_frame_index |",
        "(attack_phase_frame << 16)",
        "uint actor_frame_id(int row)",
        "uint actor_attack_phase_frame_id(int row)",
        "return texelFetch(actor_cell_tex, ivec2(10, row), 0).r & 0xFFFFu;",
    ]
    for needle in required:
        assert needle in stack + presentation + adapter + schema + room + sim_world + effect + product, needle

    forbidden = [
        "phase * phase_span + phase_frame",
        "var phase_span :=",
    ]
    for needle in forbidden:
        assert needle not in stack + presentation + product, needle


def test_fl4392_actor_presentation_uses_move_state_machine_with_consecutive_attack() -> None:
	assert PRESENTATION_STATE_MACHINE.exists()
	assert COMBAT_PRESENTATION_RIG.exists()
	stack = ACTOR_STACK.read_text(encoding="utf-8")
	presentation = PRESENTATION_STATE_MACHINE.read_text(encoding="utf-8")
	rig = COMBAT_PRESENTATION_RIG.read_text(encoding="utf-8")

	required = [
		"class_name ActorPresentationStateMachine",
		"const ActorCombatPresentationRigScript := preload(\"res://scripts/visual/ActorCombatPresentationRig.gd\")",
		"var combat_presentation_rig = ActorCombatPresentationRigScript.new()",
		"class PresentationMove:",
        "func entry(ctx: Dictionary) -> void:",
        "func update(ctx: Dictionary) -> void:",
        "func exit(ctx: Dictionary) -> void:",
        "func switch(ctx: Dictionary) -> StringName:",
        "class ConsecutiveAttack extends PresentationMove:",
        "func next_attack_move_id(ctx: Dictionary) -> String:",
        "var consecutive_attack := ConsecutiveAttack.new()",
        "func select_frame(sidecar: Dictionary, layer: Dictionary, yaw_bucket: int) -> Dictionary:",
        "func current_frame_index(sidecar: Dictionary, layer: Dictionary) -> int:",
        "const ActorPresentationStateMachineScript := preload(\"res://scripts/visual/ActorPresentationStateMachine.gd\")",
        "var _presentation_state_machine = ActorPresentationStateMachineScript.new()",
		"_presentation_state_machine.update_from_actor_state(actor_state)",
		"var selected: Dictionary = _presentation_state_machine.select_frame(sidecar, layer, yaw_bucket)",
	]
	for needle in required:
		assert needle in stack + presentation + rig, needle

	forbidden = [
		"const MOVE_PRESENTATION_MAP :=",
		"func _attack_visual_frame_index(",
		"func _effective_presentation_kind(",
		"func _mapped_frame_index(",
	]
	for needle in forbidden:
		assert needle not in stack, needle


def test_fl4389_slash_uses_rig_descriptor_not_shader_only_sweep() -> None:
	stack = ACTOR_STACK.read_text(encoding="utf-8")
	presentation = PRESENTATION_STATE_MACHINE.read_text(encoding="utf-8")
	rig = COMBAT_PRESENTATION_RIG.read_text(encoding="utf-8")
	effect = EFFECT.read_text(encoding="utf-8")
	product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

	required = [
		"class_name ActorCombatPresentationRig",
		"REFERENCE_TPS_DEMO_REPO := \"https://github.com/godotengine/tps-demo\"",
		"REFERENCE_TPS_DEMO_ASSET := \"https://godotengine.org/asset-library/asset/678\"",
		"ANIMATION_TREE_NODE := \"AnimationTree\"",
		"ANIMATION_TREE_STATE_PARAM := \"parameters/state/transition_request\"",
		"ROOT_MOTION_TRACK := \"Robot_Skeleton/Skeleton3D:root\"",
		"SLASH_SOCKET_PATH := \"PlayerModel/SwordRig/Skeleton3D/SwordTrailSocket\"",
		"func current_rig_descriptor() -> Dictionary:",
		"var rig_descriptor: Dictionary = _presentation_state_machine.current_rig_descriptor()",
		'"animation_tree_transition_request": String(rig_descriptor.get("animation_tree_transition_request", ""))',
		'"effect_socket_path": String(rig_descriptor.get("effect_socket_path", ""))',
		'"slash_sweep_start_u": float(rig_descriptor.get("slash_sweep_start_u", 0.18))',
		"words.append(_pack_actor_slash_rig_params(row))",
		"func _pack_actor_slash_rig_params(row: Dictionary) -> int:",
			"_upload_r32ui_words(_pending_actor_cell_words, ACTOR_CELL_WORDS_PER_ROW, _pending_actor_cell_count)",
			"const ACTOR_CELL_WORDS_PER_ROW := 18",
		"row[\"actor_sprite_world_size\"] = sprite_world_size",
		"row[\"actor_sprite_anchor_world\"] = anchor_world",
		"func _pack_actor_world_size_pair(x: float, y: float) -> int:",
		"uint actor_attack_rig_packed(int row)",
		"vec4 actor_attack_rig_slash_params(int row)",
		"bool actor_attack_rig_slash_active(int row)",
		"float sweep_head = mix(-1.40, 1.40, mix(rig.x, rig.y, frame_phase));",
		"vec3 foot = actor_foot_world(row);",
		"vec2 delta = receiver_world.xz - foot.xz;",
		"float radial_gate = 1.0 - smoothstep(",
	]
	for needle in required:
		assert needle in stack + presentation + rig + effect + product, needle

	for needle in [
		"float sweep_head = mix(0.30, 0.88, frame_phase);",
		"float y_center = mix(0.50, 0.54, phase);",
	]:
		assert needle not in product, needle


def test_actor_shadow_is_actor_footprint_sized() -> None:
    world = WORLD.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "vec3 actor_foot_world(int row) {",
        "struct ActorRowFootFact",
        "struct ActorStandeeSample",
        "ActorStandeeSample actor_standee_sample_for_ray(",
        "ActorRowFootFact row_foot",
        "actor_fixed256_pair(row_foot.row, 13)",
        "actor_fixed256_pair(row_foot.row, 14)",
        "vec2 local_uv = vec2(local_x / world_size.x, local_y / world_size.y);",
        "uint pfx = actor_cell_word(4, row);",
        "uint pfz = actor_cell_word(7, row);",
        "uint pfy = actor_cell_word(16, row);",
        "float actor_row_shadow_density_at_world(vec3 receiver_world)",
        "vec2 world_size = max(actor_fixed256_pair(row, 13), vec2(0.001));",
        "length(receiver_world.xz - foot.xz)",
        "actor_row_shadow_density_at_world(receiver_world)",
        "precise float q3_probe_payload_cast_shadow_attenuation(",
        "return world_pos - Vector3.UP * foot_sink_world",
    ]
    for needle in required:
        assert needle in product + world, needle

    forbidden = [
        "caster_rows.append(clampf(width_world * 1.05, 2.2, 3.8))",
        "const float ACTOR_SHADOW_RADIUS = 1.6;",
        "vec2 uv = actor_local_uv_for_pixel(row, pos);\n\t\tfloat x = abs(uv.x - 0.5) / 0.32;",
        "ShadowFact actor_projected_footprint_shadow_fact",
        "projected_actor_shadow",
        "_append_actor_shadow_casters_from_projected_rows",
        "_append_actor_shadow_casters_from_actor_rows",
        "actor_shadow_radius_world",
        "actor_atlas_uv_for_pixel(",
    ]
    for needle in forbidden:
        assert needle not in product + world, needle
    assert "actor_ground_shadow_for_selected_actor" not in product


def test_attack_swoosh_is_secondary_product_not_duplicate_actor_row() -> None:
    stack = ACTOR_STACK.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "const uint ACTOR_PRESENTATION_ATTACK = 2u;",
        "struct VsbActorPaintSelection {",
        "float attack_depth;",
        "EffectCellProduct attack_product;",
        "selection.attack_depth = 1.0e20;",
        "selection.attack_product = empty_effect_cell_product();",
        "EffectCellProduct attack_product =\n"
        "\t\t\tempty_effect_cell_product();",
        "attack_product = actor_attack_swoosh_cell_product(",
        "bool attack_candidate =\n"
        "\t\t\tattack_product.present;",
        "selections[slot].attack_depth =\n"
        "\t\t\t\t\t\tattack_depth;",
        "selections[slot].attack_product =\n"
        "\t\t\t\t\t\tattack_product;",
        "EffectCellProduct actor_attack_swoosh_cell_product",
        "actor_presentation_id(row) != ACTOR_PRESENTATION_ATTACK",
        "product.blend = SEC_BLEND_GLYPH;",
        "if (actor.attack_product.present) {",
        "vsb_product_apply_effect(actor.attack_product, record);",
    ]
    for needle in required:
        assert needle in product, needle

    assert "float actor_attack_swoosh_coverage" not in product
    assert product.count("actor_attack_swoosh_cell_product(") == 2
    assert "slot_attack_product" not in product
    assert "attack_product.depth" not in product
    finalizer_start = product.index("ProbeSubcellProductRecord vsb_product_finalize_record(")
    finalizer_end = product.index("\nvoid vsb_emit_retained_leaf_bank(", finalizer_start)
    finalizer = product[finalizer_start:finalizer_end]
    assert "actor_attack_swoosh_cell_product(" not in finalizer

    for needle in ['"attack_swoosh"', "_build_attack_swoosh_row"]:
        assert needle not in stack, needle


def test_fl4389_horizontal_swoosh_is_gpu_trail_style_ribbon_fact() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "FL-4389",
        "actor_attack_swoosh_ribbon_coverage",
        "ACTOR_ATTACK_RIBBON_SOURCE_TAG",
        "receiver.world_position = visible.world_position;",
        "attack_receiver_world += receivers[slot].world_position;",
        "attack_receiver_world /= attack_receiver_count;",
        "vec2 forward = vec2(sin(facing_angle), cos(facing_angle));",
        "float angle = atan(dot(delta, right), dot(delta, forward));",
        "float ground_gate = 1.0 - smoothstep(0.80, 1.45, abs(receiver_world.y - foot.y));",
        "uint slash_glyph = actor_attack_swoosh_glyph(coverage, trail_age);",
        "product.blend = SEC_BLEND_GLYPH;",
        "product.coverage = coverage * fade;",
        "product.alpha = mix(0.42, 0.92, coverage) * fade;",
        "ACTOR_ATTACK_SLASH_GLYPH_CORE = 47u",
        "ACTOR_DASH_TRAIL_GLYPH_CORE = 196u",
        "ACTOR_DASH_TRAIL_SOURCE_TAG = 4351u",
        "actor_dash_trail_ribbon_coverage",
        "EffectCellProduct actor_dash_trail_cell_product",
        "product.blend = SEC_BLEND_ALPHA;",
    ]
    for needle in required:
        assert needle in product, needle

    forbidden = [
        "float y_center = mix(0.50, 0.54, phase);",
        "uint slash_glyph = 196u;",
        "ACTOR_ATTACK_RIBBON_GLYPH_CORE = 196u",
        "product.world = actor_foot_world(row);",
        "product.aux = float(ACTOR_ATTACK_RIBBON_SOURCE_TAG);",
        "product.source_id = ACTOR_DASH_TRAIL_SOURCE_TAG;",
        "product.kind = OWNER_ATTACK_SWOOSH;",
        "struct AttackRibbonSample",
        "actor_attack_ribbon_sample",
        "ACTOR_ATTACK_RIBBON_SAMPLE_COUNT",
        "float horizontal_band",
        "float y_error = abs(p.y - curve);",
    ]
    for needle in forbidden:
        assert needle not in product, needle
