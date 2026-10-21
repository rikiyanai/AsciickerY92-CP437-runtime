from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "godot_project/scripts/World.gd"
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function_body(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def assert_bonfire_single_owner_contract(world: str, compositor: str) -> None:
    """Reject every old switch that could erase the world-owned bonfire."""
    publisher = function_body(
        world,
        "func _publish_world_bonfire() -> void:",
        "\n\nfunc _process_blood_marks(",
    )
    callback = function_body(
        compositor,
        "func _render_callback(",
        "\nfunc request_final_cell_paint_capture(",
    )
    builder = function_body(
        compositor,
        "func _build_local_light_records(",
        "\n# FL-4265: arm a downwash ruffle pulse.",
    )
    setter = function_body(
        compositor,
        "func set_baseline_feature_enabled(",
        "\nfunc set_weather_kind(",
    )
    getter = function_body(
        compositor,
        "func get_environment_feature_state(",
        "\nfunc _final_cell_unpack_pair(",
    )

    # These variables were mutable second owners. Their absence matters more
    # than their default value: no environment profile can flip what does not
    # exist.
    assert "baseline_feature_bonfire_light" not in compositor
    assert "baseline_feature_local_light_volume" not in compositor
    assert "baseline_feature_firefly_light" not in compositor
    assert '"bonfire_light":' not in setter
    assert '"local_light_volume":' not in setter
    assert '"firefly_light":' not in setter
    assert '"bonfire_light":' not in getter
    assert '"local_light_volume":' not in getter

    # World may publish only the fixed anchor fact. Re-enabling a feature here
    # would restore mixed ownership between World and the compositor.
    assert "set_baseline_feature_enabled" not in publisher
    assert "set_local_light_test_anchor" in publisher

    # The render callback freezes and consumes the World anchor once, then gives
    # the same fact to every view. Removing the consume would reuse a stale
    # anchor after a missed World publication.
    anchor_freeze = callback.index("var bonfire_anchor_valid_for_frame: bool = (")
    assert "_light_test_dirty = false" in callback
    anchor_consume = callback.index("_light_test_dirty = false", anchor_freeze)
    view_loop = callback.index("for view in range(view_count):", anchor_consume)
    record_build = callback.index(
        "bonfire_anchor_valid_for_frame\n\t\t)",
        view_loop,
    )
    assert anchor_freeze < anchor_consume < view_loop < record_build
    assert "_light_test_kind == LOCAL_LIGHT_KIND_BONFIRE" in callback

    # Bonfire collection is unconditional once the current World fact exists.
    # Fireflies are also permanent scene content; their producer owns the
    # night/weather/anchor admission. Lighthouse keeps its night gate.
    bonfire_section = builder.split("# --- Bonfire producer", 1)[1].split(
        "# --- Firefly producer",
        1,
    )[0]
    assert "if " not in bonfire_section
    assert (
        "_add_bonfire_records(candidates, time_seconds, "
        "bonfire_anchor_valid_for_frame)"
    ) in bonfire_section
    assert (
        "_add_firefly_records(candidates, glyph_candidates, "
        "frame_index, time_seconds)"
    ) in builder
    assert (
        "if baseline_feature_lighthouse_light and "
        "celestial.night_enabled():"
    ) in builder

    # The transport UBO is infrastructure, so disabled optional families are
    # represented by zero records instead of a mutable volume-disable bit.
    assert "_write_float_le(_local_light_control_data, 0, 1.0)" in builder
    assert '"enabled": true' in builder


def test_dynamic_billboard_is_the_only_visible_bonfire_owner() -> None:
    compositor = source(COMPOSITOR)
    producer = function_body(
        compositor,
        "func _add_bonfire_records(",
        "\n# FL-4204: firefly local light records",
    )

    assert '"bonfire_flame":' in compositor
    assert "_append_dynamic_bonfire_flame(" in compositor
    assert "LocalLightGlyphRecordData.new()" not in producer
    assert "glyphs.append(" not in producer
    assert "_bonfire_flame_glyph(" not in compositor
    assert "rec.flags = 2" in producer


def test_bonfire_motion_cadence_is_slow_and_deterministic() -> None:
    """Fire motion must stay slow and reproducible from the clock alone.

    Every term here is a function of published time, never of a random number
    generator: two viewers of the same frame must see the identical fire, and a
    replayed frame must reproduce byte-for-byte.

    FL-4203 (2026-07-27): the flame body's own lick moved from World.gd into the
    compositor's per-character tessellation, because that is where the flame's
    shape is now decided (see _append_dynamic_bonfire_flame_body). World.gd still
    owns the spark cadence, so that half of the contract stays pinned there.
    """
    world = source(WORLD)
    compositor = source(COMPOSITOR)
    product = source(PRODUCT)

    body = function_body(
        compositor,
        "func _append_dynamic_bonfire_flame_body(",
        "\nfunc _append_dynamic_billboard(",
    )
    # The flame leans on two slow, unrelated sine terms so it never reads as a
    # metronome, and the lean grows with height so the base stays planted.
    assert "sin(anim_time * 2.1 + rise * 5.2)" in body
    assert "sin(anim_time * 3.7 + rise * 2.3)" in body
    assert "(rise * 1.9)" in body
    # The ragged tip is a hash of the character's own grid position plus a
    # quantised clock -- deterministic, never RNG.
    assert "floor(anim_time * 6.0)" in body
    assert "randf" not in body and "randi" not in body
    # Spark cadence remains World-owned and clock-derived.
    assert "fmod(t * 0.32 + seed, 1.5)" in world
    assert "sin(t * 1.2 + seed * 9.0)" in world
    assert "cos(t * 1.0 + seed * 7.0)" in world
    assert "params.feature_params.x * 1.8 + phase * 12.0" in product


def test_bonfire_light_uses_receiver_geometry_and_authored_shadow_casters() -> None:
    product = source(PRODUCT)
    visibility = function_body(
        product,
        "float vsb_product_local_light_visibility(",
        "\nVsbProductLocalLightSlotFacts "
        "vsb_product_collect_local_light_slot_facts(",
    )
    addition = function_body(
        product,
        "VsbProductLocalLightSlotFacts "
        "vsb_product_collect_local_light_slot_facts(",
        "\nstruct VsbProductCompileInput",
    )

    assert "vsb_retained_select_point_light_caster(" in visibility
    assert "shadow_ray, receiver_face_idx" in visibility
    assert "local_light_actor_blocked" in visibility
    assert "return 0.0;" in visibility
    assert "distance_to_light - 0.08" in visibility
    assert "radius * 4.0" in addition
    assert "radius_t * radius_t * slot_1.w" in addition
    assert "normalize(receiver_normal), delta / distance_to_light" in addition
    assert "LOCAL_LIGHT_FLAG_CAST_SHADOW" in addition


def test_point_light_reuses_complete_static_bvh_and_lights_actor_faces() -> None:
    compositor = source(COMPOSITOR)
    product = source(PRODUCT)

    for binding in range(70, 83):
        assert f"layout(set = 0, binding = {binding}" in product
        assert f"_probe_u_storage({binding}," in compositor
    visibility = function_body(
        product,
        "float vsb_product_local_light_visibility(",
        "\nVsbProductLocalLightSlotFacts "
        "vsb_product_collect_local_light_slot_facts(",
    )
    finalize = function_body(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\n// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4",
    )
    assert "vsb_retained_select_point_light_caster(" in visibility
    assert "vsb_retained_select_airplane_hit" not in visibility
    assert "effect_normal = actor_local_light_normal(" in finalize
    assert "actor.row, actor.local_uv);" in finalize
    assert "input_value.ray.dir * actor.depth" in finalize
    assert "actor.present ? actor.row : -1" in finalize
    assert "input_value.retained_face.legacy_face_index" in finalize


def test_smoke_and_sparks_survive_perspective_projection() -> None:
    world = source(WORLD)
    compositor = source(COMPOSITOR)
    product = source(PRODUCT)

    billboard = function_body(
        compositor,
        "func _append_dynamic_billboard(",
        "\nfunc _append_dynamic_effect_sample(",
    )
    assert "ctx.basis.x * radius_x" in billboard
    assert "ctx.basis.y * radius_y" in billboard
    assert '"mat_id": BONFIRE_SMOKE_MAT' in world
    assert '"radius": 0.62' in world
    selector = function_body(
        product,
        "VsbRetainedVaporSelections vsb_retained_select_vapor_effect_candidates(",
        "\nVsbRetainedDynamicSelection vsb_retained_select_dynamic_candidate(",
    )
    style = function_body(
        product,
        "void vsb_product_apply_vapor_style(",
        "\nstruct VsbActorPaintSelection",
    )
    finalize = function_body(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\n// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4",
    )
    # One retained-tree walk selects bonfire smoke independently from the
    # airplane-vapor lane. This structural assertion rejects a regression to
    # the deleted two-traversal selector without weakening the material law.
    assert "vsb_retained_consider_vapor_mover(" in selector
    assert "selected.bonfire_smoke" in product
    assert "facts.material_id == 234u" in product
    assert "facts.material_id == 207u" in product
    assert "facts.material_id == 211u" in product
    assert "material == 234u" in style
    assert (
        "vsb_product_apply_vapor_style(\n"
        "\t\tvapor_layers.bonfire_smoke, input_value.ray, record);"
    ) in finalize
    # Operator change 2026-07-27: smoke is shaped PER CELL rather than painted
    # as one flat rectangle per puff. The style function therefore needs the
    # view ray so it can locate this cell inside the puff's billboard, and it
    # must actually use that position to attenuate towards the puff rim. These
    # assertions reject a regression to the uniform per-puff square.
    assert "ViewRay ray" in style
    assert "ray.near_world + ray.dir * vapor.depth" in style
    assert "rim_distance" in style and "smoothstep(0.30, 1.05, rim_distance)" in style
    # Per-cell jitter is what breaks the repeated-character grid, and the
    # thin-cell early-out is what makes the puff's outline ragged.
    assert "cell_jitter" in style
    assert "if (density <= 0.06) return;" in style
    # The per-cell shaping stays scoped to bonfire smoke: contrail and damage
    # vapor must keep their previous uniform appearance.
    assert style.index("material == 234u") < style.index("rim_distance")
    assert "if (airframe_id == 8u)" in product
    assert "mat_id == 230u" in product
    assert "mat_id == 231u" in product
    # Operator change 2026-07-27: the inner flame carries orange/red in BOTH the
    # glyph ink and the cell fill. Previously one `band` drove both and the fill
    # was that band scaled by only 0.14..0.24 -- effectively black, so the fire
    # read as a bright glyph on a black square. Assert the two-band split plus a
    # fill floor high enough to actually glow, which rejects a silent return to
    # the near-black background.
    assert "ink_band" in product and "fill_band" in product
    # Operator change 2026-07-27 (second pass): the fire must be distinct in HUE
    # and VALUE from the pale cream light it throws on the ground. That means
    # deeply saturated ink/fill pairs -- yellow-on-orange, orange-on-red,
    # red-on-dark-red -- which no additively lit surface can reproduce.
    assert "vec3(0.92, 0.36, 0.02)" in product   # hot core fill: strong orange
    assert "vec3(0.60, 0.09, 0.01)" in product   # mid flame fill: red
    assert "vec3(0.26, 0.02, 0.01)" in product   # cool edge fill: dark red
    assert "vec3(1.00, 0.90, 0.32)" in product   # hot core ink: yellow
    assert "vec3(1.00, 0.48, 0.08)" in product   # mid flame ink: orange
    assert "vec3(0.92, 0.17, 0.04)" in product   # cool edge ink: red
    # Every fill band must be saturated: the red channel has to dominate the
    # blue one by a wide margin, or the "fire" has drifted back towards grey.
    for red, green, blue in (
        (0.92, 0.36, 0.02),
        (0.60, 0.09, 0.01),
        (0.26, 0.02, 0.01),
    ):
        assert red > blue * 4.0, (red, green, blue)
        assert green < red * 0.6, (red, green, blue)
    # The fill floor stays high enough to remain firmly orange/red; dropping it
    # further slides the fire towards brown and blunts the contrast.
    assert "fill_band * (0.72 + 0.28 * fract(" in product
    assert "band * (0.14 + 0.10 * fract(" not in product


def test_fire_is_emissive_and_escapes_night_dimming_and_its_own_light() -> None:
    """The fire must not be treated as a surface that its own light falls on.

    FL-4203 (operator report 2026-07-27): the flame rendered the same washed-out
    cream as the grass it was lighting. Root cause was ordering, not colour --
    `product_apply_bonfire_dynamic_style` wrote a warm orange, then the scene's
    night response multiplied it down and the bonfire's own additive point light
    (at zero distance, so at full strength) pushed every channel up until it
    clipped. Net result: fire and firelight became the same colour.

    A fire is a light SOURCE. Both stages must skip effect kinds 8 (flame body)
    and 9 (spark). Smoke is kind 4 and is deliberately NOT exempt, because smoke
    really is lit by the fire.
    """
    product = source(PRODUCT)
    finalize = function_body(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\n// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4",
    )
    # The classification is made once, from the retained face's effect kind.
    assert "bool bonfire_emissive = input_value.retained_face.valid" in finalize
    assert "input_value.retained_face.actor_effect_kind == 8u" in finalize
    assert "input_value.retained_face.actor_effect_kind == 9u" in finalize
    # Kind 4 (smoke) must NOT be swept into the emissive set.
    emissive_decl = finalize.split("bool bonfire_emissive =", 1)[1].split(";", 1)[0]
    assert "actor_effect_kind == 4u" not in emissive_decl

    # Both washing stages must consult it.
    assert "if (night > 0.0001 && !bonfire_emissive)" in finalize
    assert "if (record.r0.y != OWNER_SKY && !bonfire_emissive)" in finalize
    # ...and the emissive test must be established BEFORE either of them runs,
    # otherwise it cannot protect the paint.
    declaration = finalize.index("bool bonfire_emissive =")
    assert declaration < finalize.index("if (night > 0.0001 && !bonfire_emissive)")
    assert declaration < finalize.index(
        "if (record.r0.y != OWNER_SKY && !bonfire_emissive)"
    )


def test_world_receiver_light_crosses_probe_cutover_without_glyph_owner() -> None:
    product = source(PRODUCT)
    finalize = function_body(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\n// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4",
    )
    collection = finalize.index(
        "vsb_product_collect_local_light_slot_facts("
    )
    beam = finalize.index(
        "EffectCellProduct beam = local_light_facts.beam;"
    )
    point_light = finalize.index(
        "vec3 addition = local_light_facts.point_addition;"
    )
    glyph_light = finalize.index("compute_local_light_glyph_cell_product(")
    assert collection < beam < point_light < glyph_light
    assert "ground_probe_cutover_paint" not in finalize
    assert (
        "record.fg.rgb + addition" in finalize
        and "record.bg.rgb + addition * 0.28" in finalize
    )


def test_world_bonfire_is_always_published_after_environment_state() -> None:
    world = source(WORLD)
    compositor = source(COMPOSITOR)
    process = function_body(
        world,
        "func _process(delta: float) -> void:",
        "\n# FL-4203: publish the normal world's warm bonfire every frame.",
    )
    # FL-4431 physically deleted the duplicate PERF trace execution branch.
    # The one shipping process path must update the environment first, then
    # publish the always-present world bonfire in that same frame.
    assert process.index("_update_manual_environment_controller(delta)") < process.index(
        "_publish_world_bonfire()"
    )
    assert "_perf_trace_enabled" not in process
    assert "--fl4203-night-bonfire" not in world
    assert "_night_bonfire_enabled" not in world
    assert_bonfire_single_owner_contract(world, compositor)


def test_bonfire_single_owner_mutations_are_rejected() -> None:
    world = source(WORLD)
    compositor = source(COMPOSITOR)
    compositor_mutants = {
        "mutable_bonfire_feature_restored": compositor.replace(
            "var baseline_feature_lighthouse_light: bool = true",
            "var baseline_feature_bonfire_light: bool = true\n"
            "var baseline_feature_lighthouse_light: bool = true",
            1,
        ),
        "mutable_volume_setter_restored": compositor.replace(
            '\t\t"lighthouse_light":',
            '\t\t"local_light_volume":\n'
            "\t\t\tpass\n"
            '\t\t"lighthouse_light":',
            1,
        ),
        "bonfire_record_refenced": compositor.replace(
            "\t_add_bonfire_records(candidates, time_seconds, "
            "bonfire_anchor_valid_for_frame)",
            "\tif baseline_feature_lighthouse_light:\n"
            "\t\t_add_bonfire_records(candidates, time_seconds, "
            "bonfire_anchor_valid_for_frame)",
            1,
        ),
        "stale_anchor_reuse_restored": compositor.replace(
            "\t_light_test_dirty = false",
            "\t# stale anchor left pending",
            1,
        ),
        "transport_disable_bit_restored": compositor.replace(
            "_write_float_le(_local_light_control_data, 0, 1.0)",
            "_write_float_le(_local_light_control_data, 0, "
            "1.0 if baseline_feature_lighthouse_light else 0.0)",
            1,
        ),
    }
    for mutation_name, mutant in compositor_mutants.items():
        assert mutant != compositor, f"mutation did not alter source: {mutation_name}"
        with pytest.raises(AssertionError):
            assert_bonfire_single_owner_contract(world, mutant)

    world_mutant = world.replace(
        "\t_ascii_effect.call(\"set_local_light_test_anchor\", anchor, "
        "NIGHT_BONFIRE_RADIUS, NIGHT_BONFIRE_COLOR, LOCAL_LIGHT_KIND_BONFIRE)",
        "\t_ascii_effect.call(\"set_baseline_feature_enabled\", "
        "\"bonfire_light\", true)\n"
        "\t_ascii_effect.call(\"set_local_light_test_anchor\", anchor, "
        "NIGHT_BONFIRE_RADIUS, NIGHT_BONFIRE_COLOR, LOCAL_LIGHT_KIND_BONFIRE)",
        1,
    )
    assert world_mutant != world
    with pytest.raises(AssertionError):
        assert_bonfire_single_owner_contract(world_mutant, compositor)


def test_bonfire_body_offsets_are_resolved_in_billboard_basis() -> None:
    """Fire geometry must be laid out on the camera-facing basis, not world XY.

    A fixed world-XY plane turns edge-on as the camera swings round, which made
    the flame vanish at certain angles. Everything the fire appends -- the
    tessellated flame column, the sparks, the smoke puffs -- must be built from
    `ctx.basis.x` / `ctx.basis.y`, the accepted pixel-camera axes.
    """
    compositor = source(COMPOSITOR)
    body = function_body(
        compositor,
        "func _append_dynamic_bonfire_flame_body(",
        "\nfunc _append_dynamic_billboard(",
    )
    billboard = function_body(
        compositor,
        "func _append_dynamic_billboard(",
        "\nfunc _append_dynamic_effect_sample(",
    )
    # The flame column steps sideways and upwards along the camera basis.
    assert "ctx.basis.x * (column_offset * cell.x)" in body
    assert "ctx.basis.y * ((float(row) + 0.5) * cell.y)" in body
    # Its per-character quad corners come from the same two axes.
    assert "ctx.basis.x * (cell.x * 0.52)" in body
    assert "ctx.basis.y * (cell.y * 0.52)" in body
    # No world-axis fallback may reappear.
    assert "Vector3.RIGHT" not in body and "Vector3.UP" not in body
    # Sparks and smoke keep the shared billboard builder, also basis-derived.
    assert "ctx.basis.x * radius_x" in billboard
    assert "ctx.basis.y * radius_y" in billboard


def test_fire_particles_are_sized_to_exactly_one_character_cell() -> None:
    """Sparks and smoke puffs must each occupy ONE character, not a block.

    FL-4203 (operator report 2026-07-27, with screenshot): the smoke and sparks
    rendered as solid dark rectangles filled with a repeating grid of identical
    `*` marks. Root cause is a size mismatch, not a colour bug -- a spark quad
    was 0.62 world units in radius, which at ordinary play zoom covers four or
    five character cells, and one quad can only ever produce ONE glyph and ONE
    foreground/background pair for every cell it covers. So it painted a
    rectangle by construction.

    The cure is to measure how large a character actually is, in world units, at
    the particle's own position, and size the quad to that. This test pins the
    measurement and its use, because reverting either one silently restores the
    rectangles.
    """
    world = source(WORLD)
    compositor = source(COMPOSITOR)

    # FL-4359: one authored effect character is a fixed WORLD-space quad. The
    # camera-measured screen cell size (`_ansi_cell_world_size_at`) is deleted:
    # a current-eye fact may not size world geometry the retained probes
    # capture.
    assert "func _ansi_cell_world_size_at(" not in compositor
    assert "_ansi_cell_world_size_at(ctx" not in compositor
    assert "const FL4359_EFFECT_CHARACTER_WORLD_SIZE := Vector2(0.18, 0.30)" in compositor

    # Both particle families opt in, and both consume the measurement.
    spark_builder = function_body(
        compositor,
        "func _append_dynamic_bonfire_flame(",
        "\n# Builds the frame's complete dynamic-mover face bank",
    )
    puff_builder = function_body(
        compositor,
        "func _append_dynamic_effect_sample(",
        "\nfunc _append_dynamic_projectile(",
    )
    for builder in (spark_builder, puff_builder):
        assert 'bool(fact.get("cell_particle", false))' in builder
        assert "FL4359_EFFECT_CHARACTER_WORLD_SIZE" in builder
    # World.gd must actually request it for the fire's sparks and smoke.
    fire = function_body(
        world,
        "func _append_bonfire_flame_dynamic_source_facts(",
        "\nfunc _append_static_world_face(",
    )
    assert fire.count('"cell_particle": true') == 2
    assert '"mat_id": BONFIRE_SPARK_MAT' in fire
    assert '"mat_id": BONFIRE_SMOKE_MAT' in fire
    # Airplane contrails and damage vapor must NOT opt in -- they are smears by
    # design, and resizing them would change an unrelated accepted effect.
    contrail = function_body(
        compositor,
        'var fact := {\n\t\t\t"kind": "vapor_sample",',
        '"radius": 0.95,',
    )
    assert "cell_particle" not in contrail


def test_flame_body_is_tessellated_into_per_character_quads() -> None:
    """The flame column is filled in one character at a time, at world scale.

    Two properties have to hold together, and they pull in opposite directions:

    1. the fire is a SOLID OBJECT, so its physical size in the world is fixed --
       walking towards it must make it look bigger, exactly like a tree;
    2. every quad must cover exactly one character, or it paints a flat block.

    The only way to satisfy both is to keep the authored world size and vary the
    NUMBER of characters used to draw it, which is what the tessellator does.
    """
    world = source(WORLD)
    compositor = source(COMPOSITOR)

    # World.gd publishes the fire's PHYSICAL size, in world units, and nothing
    # about characters -- it does not know the camera.
    assert "const BONFIRE_FLAME_HALF_WIDTH := 1.9" in world
    assert "const BONFIRE_FLAME_HEIGHT := 3.2" in world
    fire = function_body(
        world,
        "func _append_bonfire_flame_dynamic_source_facts(",
        "\nfunc _append_static_world_face(",
    )
    assert '"flame_body": true' in fire
    assert '"half_width": BONFIRE_FLAME_HALF_WIDTH' in fire
    assert '"height": BONFIRE_FLAME_HEIGHT' in fire
    # The whole column is ONE dynamic mover, so the retained-scene database gets
    # one face range rather than a mover per character.
    assert fire.count('"flame_body": true') == 1

    body = function_body(
        compositor,
        "func _append_dynamic_bonfire_flame_body(",
        "\nfunc _append_dynamic_billboard(",
    )
    # Grid resolution is derived from the measured character size, and bounded at
    # both ends: a distant fire cannot collapse to nothing and a close one cannot
    # flood the face bank.
    assert "var cell := FL4359_EFFECT_CHARACTER_WORLD_SIZE" in body
    assert "clampi(int(round(2.0 * half_width / cell.x)), 3, 21)" in body
    assert "clampi(int(round(height / cell.y)), 3, 24)" in body
    assert "var face_budget := 220" in body
    assert "if appended >= face_budget:" in body
    # An odd column count guarantees a single true centre column for the hot
    # core; an even count would split the core in two.
    assert "if columns % 2 == 0:" in body
    # Heat falls off sideways AND with height, and picks one of the three
    # authored bands -- that per-character gradient is what the three big flat
    # quads could never produce.
    assert "var heat := clampf(1.0 - lateral * 0.62 - rise * 0.72, 0.0, 1.0)" in body
    assert "mat_id = mat_hot" in body
    assert "mat_id = mat_mid" in body
    # Each character carries its OWN id so the shader can flicker it
    # independently of its neighbours.
    assert "cell_source_id_base + appended" in body
    assert "const BONFIRE_FLAME_CELL_SOURCE_ID_BASE := 30001" in world
    # The compositor routes the two fire shapes off the flag, under the ONE
    # registered dynamic-ABI kind name (see the ABI test below).
    assert 'if bool(fact.get("flame_body", false)):' in compositor
    assert "_append_dynamic_bonfire_flame_body(ctx, face_rows, fact)" in compositor


def test_every_bonfire_mover_kind_is_registered_in_the_dynamic_abi() -> None:
    """A mover kind VSB does not know makes it reject the WHOLE frame's movers.

    FL-4203 (2026-07-27, caught in a headed run): the flame column was first
    published under a brand-new kind name, "bonfire_flame_body".
    VisibleSurfaceBuilder keeps a VERSIONED table of mover kind names and packs
    each one's number into the high byte of the product's object identity, so an
    unregistered name is an ABI violation. VSB did the correct thing and failed
    closed -- but "fail closed" for a malformed publication means discarding
    EVERY mover in that frame, so the planes, birds, sparks and smoke all
    vanished, not just the new geometry. Nothing in the structural suite caught
    it; only the running game did.

    This test closes that gap: every kind the bonfire publishes must already
    exist in the ABI table. Adding a genuinely new kind is still allowed, but it
    now has to be registered once in the retained scene owner.
    """
    world = source(WORLD)
    builder = (
        ROOT / "godot_project/render/scene/SceneGeometryDB.gd"
    ).read_text(encoding="utf-8")

    table = builder.split("const VSB_DYNAMIC_KIND_IDS := {", 1)[1].split("}", 1)[0]
    registered = set(re.findall(r'&"([a-z_]+)"\s*:\s*\d+', table))
    assert registered, "could not parse the dynamic ABI kind table"

    fire = function_body(
        world,
        "func _append_bonfire_flame_dynamic_source_facts(",
        "\nfunc _append_static_world_face(",
    )
    published = set(re.findall(r'"kind"\s*:\s*"([a-z_]+)"', fire))
    assert published, "could not find any published bonfire mover kinds"
    unregistered = published - registered
    assert not unregistered, (
        "bonfire publishes mover kinds absent from VisibleSurfaceBuilder's "
        "DYNAMIC_KIND_IDS, which makes VSB discard every mover in the frame: "
        f"{sorted(unregistered)}"
    )
