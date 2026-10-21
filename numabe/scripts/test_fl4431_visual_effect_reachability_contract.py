#!/usr/bin/env python3
"""Guard three FL-4431 effect paths against silent source-side suppression.

The retained visibility cutover keeps secondary effects in Product instead of
making each effect a primary VSB owner.  A function existing in the shader is
therefore insufficient evidence: the host must publish its live facts and the
finalizer must admit the effect.  These checks bind those call edges and run
destructive source mutations so a future default-off owner cannot pass merely
because its renderer still exists.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
ACTOR_STACK = ROOT / "godot_project/scripts/visual/ActorLayerStack.gd"
OWNER_TERRAIN = 1
OWNER_ACTOR_SPRITE = 4
OWNER_SKY = 6


def function_body(source: str, marker: str) -> str:
    """Return one source function so an unrelated file-wide token cannot pass."""
    start = source.index(marker)
    next_function = source.find("\nfunc ", start + len(marker))
    if next_function < 0:
        return source[start:]
    return source[start:next_function]


def shader_function_body(source: str, marker: str, next_marker: str) -> str:
    """Return one GLSL function using its next stable declaration as the edge."""
    start = source.index(marker)
    end = source.index(next_marker, start + len(marker))
    return source[start:end]


def balanced_shader_function_body(source: str, marker: str) -> str:
    """Return one GLSL function without relying on a comment boundary."""
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[start : position + 1]
    raise ValueError(f"unterminated GLSL function: {marker}")


def declared_function_names(source: str, pattern: str) -> list[str]:
    return re.findall(
        rf"(?m)^\s*(?:precise\s+)?(?:void|float|vec2|vec3|vec4)\s+"
        rf"({pattern})\s*\(",
        source,
    )


def swap_adjacent_blocks(
    source: str,
    first_marker: str,
    second_marker: str,
    end_marker: str,
) -> str:
    """Swap two adjacent source blocks without weakening their contents."""
    first = source.index(first_marker)
    second = source.index(second_marker, first)
    end = source.index(end_marker, second)
    assert first < second < end
    return (
        source[:first]
        + source[second:end]
        + source[first:second]
        + source[end:]
    )


def replace_in_source_block(
    source: str,
    start_marker: str,
    end_marker: str,
    old: str,
    new: str,
) -> str:
    """Replace one token only inside the named source block."""
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    block = source[start:end]
    assert block.count(old) == 1
    return source[:start] + block.replace(old, new, 1) + source[end:]


def move_source_block_before(
    source: str,
    scope_marker: str,
    block_start_marker: str,
    block_end_marker: str,
    destination_marker: str,
) -> str:
    """Move one complete block within a function for an ordering mutation."""
    scope_start = source.index(scope_marker)
    block_start = source.index(block_start_marker, scope_start)
    block_end = source.index(block_end_marker, block_start)
    block = source[block_start:block_end]
    without_block = source[:block_start] + source[block_end:]
    destination = without_block.index(destination_marker, scope_start)
    return (
        without_block[:destination]
        + block
        + without_block[destination:]
    )


def firefly_glyph_admitted(
    final_kind: int,
    world_depth: float,
    glyph_kind: int,
    glyph_id: int,
    alpha: float,
    glyph_world_z: float,
    radius_cells: float = 0.28,
) -> bool:
    """Execute the Product admission law on one centre-line glyph row.

    The fixture ray starts at zero, points along positive Z, and ends at depth
    100. A centre-line row isolates receiver admission from unrelated screen
    coverage while preserving Product's depth clamp, 0.25 receiver slack,
    radius floor, alpha threshold, kind validity, and zero-glyph rejection.
    """
    if final_kind == OWNER_ACTOR_SPRITE:
        return False
    best_alpha = 0.0
    best_glyph = 0
    if alpha <= 0.001:
        return False
    if glyph_kind == 0:
        return False
    glyph_depth = min(max(glyph_world_z, 0.0), 100.0)
    if glyph_depth < 0.0 or glyph_depth > world_depth + 0.25:
        return False
    ray_glyph_world_z = glyph_depth
    radius_world = max(radius_cells * 0.72, 0.42)
    world_distance = abs(ray_glyph_world_z - glyph_world_z)
    if world_distance > radius_world:
        return False
    inner_radius = max(radius_world - 0.44, 0.0)
    if world_distance <= inner_radius:
        edge_fade = 0.0
    elif world_distance >= radius_world:
        edge_fade = 1.0
    else:
        t = (world_distance - inner_radius) / (radius_world - inner_radius)
        edge_fade = t * t * (3.0 - 2.0 * t)
    visible_alpha = alpha * (1.0 - edge_fade)
    if visible_alpha > best_alpha:
        best_alpha = visible_alpha
        best_glyph = glyph_id
    return best_alpha > 0.0 and best_glyph != 0


def godray_active_owner_errors(product: str) -> list[str]:
    """Require the current world-plane owner and identical additive edge."""
    errors: list[str] = []
    helper = shader_function_body(
        product,
        "ProductDaylightGodrayVeil product_daylight_godray_veil(",
        "\n// FL-4425: one unfiltered receiver sample",
    )
    finalizer = shader_function_body(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\n// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4",
    )
    if product.count(
        "ProductDaylightGodrayVeil product_daylight_godray_veil(\n"
        "\tViewRay ray,\n"
        "\tfloat receiver_depth,\n"
        "\tbool capture_visibility\n"
        ")"
    ) != 1:
        errors.append("daylight godray Product helper is missing or duplicated")
    if helper.count("ProductDaylightGodrayVeil product_daylight_godray_veil(") != 1:
        errors.append("daylight godray helper body is not isolated")
    if finalizer.count(
        "product_daylight_godray_veil(\n"
        "\t\t\tinput_value.ray, effect_depth, capture_godray_visibility)"
    ) != 1:
        errors.append("daylight godray helper/call edge is not singular")
    if "vsb_product_selected_effective_sun_dir" not in helper:
        errors.append("daylight godray does not consume the carried sun")
    raw_names = declared_function_names(
        product,
        r"product_godray_cloud_veil_strength_at_sample_xz",
    )
    if len(raw_names) != 1:
            errors.append("godray-only cloud veil sampler is missing or duplicated")
    else:
        raw_sampler = balanced_shader_function_body(product, f"{raw_names[0]}(")
        if "product_cloud_sample_uvs(" not in raw_sampler:
            errors.append("godray raw sampler bypasses the shared cloud UV owner")
        if raw_sampler.count("stable_value_noise(") != 0:
            errors.append("godray veil sampler bypasses the shared broad cloud mass owner")
        if "smoothstep(0.50, 0.82" in raw_sampler:
            errors.append("godray veil sampler retained the surface binary smoothstep")
        if f"{raw_names[0]}(" not in helper:
            errors.append("daylight godray does not call its cloud veil sampler")
    uv_names = declared_function_names(product, r"product_cloud_sample_uvs")
    if uv_names != ["product_cloud_sample_uvs"]:
        errors.append("shared cloud UV sampler is missing or duplicated")
    else:
        uv_sampler = balanced_shader_function_body(product, "product_cloud_sample_uvs(")
        for pin in ("product_cloud_shadow_time(", "cloud_dir", "time * 0.10"):
            if pin not in uv_sampler:
                errors.append(f"shared cloud UV owner lost {pin}")
    for pin in (
        "const int FL4101_DAY_GODRAY_PLANES = ",
        "const float FL4101_DAY_GODRAY_PLANE_GAP = ",
        "const vec3 FL4101_DAY_GODRAY_VEIL_LIGHT = vec3(",
        "const vec3 FL4101_DAY_GODRAY_VEIL_COLOR = vec3(",
    ):
        if pin not in product:
            errors.append(f"world-plane godray constant drifted: {pin}")
    for pin in (
        "if (daylight <= 0.001 || sun_above_horizon <= 0.001\n\t\t\t|| sun_xz_len <= 0.001\n\t\t\t|| !baseline_feature_clouds_enabled()) return empty_veil;",
        "precise float t_end = min(clamp(receiver_depth, 0.0, ray.len), 180.0);",
        "precise vec2 plane_normal_xz = vec2(-sun_xz.y, sun_xz.x) / sun_xz_len;",
        "precise vec3 plane_normal = vec3(",
        "precise float distance_error = mod(",
        "precise float plane_t_step =\n\t\tFL4101_DAY_GODRAY_PLANE_GAP / absolute_rate;",
        "precise vec3 column_mid_world = ray.near_world + ray.dir * (0.5 * t_end);",
        "precise float column_veil =",
        "float front_to_back_transmittance = 1.0;",
        "float accumulated_strength = 0.0;",
        "for (int i = 0; i < FL4101_DAY_GODRAY_PLANES; ++i)",
        "precise vec3 sample_world = ray.near_world + ray.dir * t;",
        "precise float ridge_lit =",
        "precise float sample_strength = column_veil * ridge_lit;",
        "precise float layer_opacity =",
        "accumulated_strength += front_to_back_transmittance * layer_opacity;",
        "front_to_back_transmittance *= 1.0 - clamp(layer_opacity, 0.0, 0.96);",
    ):
        if pin not in helper:
            errors.append(f"world-plane godray body drifted: {pin}")
    ridge_names = declared_function_names(
        product,
        r"product_godray_terrain_light_visibility_at_air_sample",
    )
    if len(ridge_names) != 1:
        errors.append("ridge visibility helper is missing or duplicated")
    else:
        ridge = balanced_shader_function_body(product, f"{ridge_names[0]}(")
        if f"{ridge_names[0]}(" not in helper:
            errors.append("daylight godray does not call ridge visibility")
        for pin in (
            "sun.xz",
            "cell_max_height(",
            "any(lessThan(probe_cell, ivec2(0)))",
            "any(greaterThanEqual(probe_cell, dims))",
        ):
            if pin not in ridge:
                errors.append(f"ridge visibility lost {pin}")
        if ridge.count("for (") != 1:
            errors.append("ridge visibility is not one fixed bounded loop")
        ridge_code = "\n".join(line.split("//", 1)[0] for line in ridge.splitlines())
        if any(token in ridge_code for token in (
            "q3_probe_payload_", "TLAS", "receiver_cast_shadow",
            "heightfield_shadow_attenuation",
        )):
            errors.append("ridge visibility acquired a forbidden receiver/TLAS owner")
    if "const int FL4101_DAY_GODRAY_RIDGE_STEPS = 6;" not in product:
        errors.append("ridge visibility step count is not six")
    if "const int FL4101_DAY_GODRAY_RIDGE_MAX_STEPS = 8;" not in product:
        errors.append("ridge visibility compile bound is not eight")
    helper_without_comments = "\n".join(
        line.split("//", 1)[0] for line in helper.splitlines()
    )
    if "camera" in helper_without_comments.lower():
        errors.append("godray helper unexpectedly consumes camera input")
    if "SCREEN_UV" in helper_without_comments:
        errors.append("godray helper unexpectedly reconstructs SCREEN_UV")
    for retired in ("receiver_world", "SCREEN_UV", "shaft_0", "shaft_1", "shaft_2", "shaft_3", "lake_along", "Rake", "coverage", "terrain_visibility"):
        if retired in helper_without_comments:
            errors.append(f"godray helper retains retired authored structure {retired}")
    if "if (!baseline_feature_night_enabled())" not in finalizer:
        errors.append("daylight godray finalizer gate drifted")
    for pin in (
        "if (!baseline_feature_night_enabled()) {",
        "ProductDaylightGodrayVeil godray_veil = product_daylight_godray_veil(",
        "record.fg.rgb * gap_retention + godray_veil.light",
        "record.bg.rgb * gap_retention + godray_veil.light * 0.82",
    ):
        if pin not in finalizer:
            errors.append(f"godray additive finalizer drifted: {pin}")
    if "godray_nonband_" in finalizer:
        errors.append("godray finalizer retains non-band darkening")
    godray_finalizer = finalizer[finalizer.find("ProductDaylightGodrayVeil godray_veil") :]
    if any(token in godray_finalizer for token in ("OWNER_WATER", "OWNER_TERRAIN", "OWNER_STATIC_MESH")):
        errors.append("godray finalizer retains material-specific attenuation")
    return errors


def godray_retirement_errors(effect: str, world: str, product: str) -> list[str]:
    """Bind the dependency-parked F14 edge without parsing unrelated effects."""
    errors: list[str] = []
    for retired_owner in (
        "night_moon_godray_inscatter(",
        "NIGHT_GODRAY_",
        "product_spike_2am_look",
    ):
        if retired_owner in product:
            errors.append(f"dependency-parked godray owner survived {retired_owner}")
    if "fl4359_spike_2am_look" in effect:
        errors.append("host still owns the retired moon-godray mode flag")
    if '"spike_2am_look"' in world:
        errors.append("World still publishes the retired moon-godray mode flag")
    if "terrain_flags |= 2097152" in effect:
        errors.append("host still packs the retired moon-godray mode bit")
    return errors


def contract_errors(
    effect: str,
    world: str,
    product: str,
    actor_stack: str,
) -> list[str]:
    """Describe each broken effect edge without converting exceptions into PASS."""
    errors = godray_active_owner_errors(product)
    errors.extend(godray_retirement_errors(effect, world, product))

    actor_runtime_row = function_body(actor_stack, "func _build_runtime_row(")
    required_actor_rig_facts = (
        "var rig_descriptor: Dictionary = "
        "_presentation_state_machine.current_rig_descriptor()",
        '"slash_ribbon_active": bool('
        'rig_descriptor.get("slash_ribbon_active", false))',
        '"slash_sweep_start_u": float('
        'rig_descriptor.get("slash_sweep_start_u", 0.18))',
        '"slash_sweep_end_u": float('
        'rig_descriptor.get("slash_sweep_end_u", 0.82))',
        '"slash_center_v": float(rig_descriptor.get("slash_center_v", 0.50))',
        '"slash_width_v": float(rig_descriptor.get("slash_width_v", 0.18))',
    )
    for fact in required_actor_rig_facts:
        if fact not in actor_runtime_row:
            errors.append(f"ActorLayerStack does not forward rig fact {fact}")

    actor_row_packer = function_body(
        effect, "func _pack_actor_sprite_frame_rect_queue("
    )
    if (
        actor_row_packer.count(
            "words.append(_pack_actor_slash_rig_params(row))"
        )
        != 1
    ):
        errors.append("actor row does not append exactly one slash-rig col12 word")

    packer = function_body(effect, "func _pack_actor_slash_rig_params(")
    required_packer_facts = (
        '"slash_ribbon_active"',
        '"slash_sweep_start_u"',
        '"slash_sweep_end_u"',
        '"slash_center_v"',
        '"slash_width_v"',
        "<< 8",
        "<< 16",
        "<< 24",
    )
    if "ACTOR_ATTACK_SWOOSH_ENABLED" in product:
        errors.append("attack swoosh still has a second default-off owner")
    if "if not bool(row.get(\"slash_ribbon_active\", false)):" not in packer:
        errors.append("inactive slash rows are not encoded as the zero ABI word")
    for fact in required_packer_facts:
        if fact not in packer:
            errors.append(f"slash rig packer does not consume {fact}")
    packed_return = (
        "return start_byte | (end_byte << 8) | "
        "(center_byte << 16) | (width_byte << 24)"
    )
    if packed_return not in packer:
        errors.append("active slash rig rows still collapse to zero")
    # Byte zero is reserved by Product as "use fallback". Every active
    # component therefore needs the lower bound one, even when the authored
    # normalized component is exactly zero.
    if packer.count("\n\t\t1,\n\t\t0xFF\n\t)") != 4:
        errors.append("active slash components do not all avoid byte-zero")

    if "uint actor_attack_rig_packed(int row) {" in product:
        slash_decoder = shader_function_body(
            product,
            "uint actor_attack_rig_packed(int row) {",
            "\nconst uint ACTOR_DASH_TRAIL_SOURCE_TAG",
        )
        required_slash_decoder_laws = (
            "return actor_cell_word(12, row);",
            "uint value = (packed >> shift) & 0xFFu;",
            "if (value == 0u) return fallback_value;",
            "return actor_attack_rig_packed(row) != 0u;",
            "actor_attack_rig_byte(packed, 0u, 0.18)",
            "actor_attack_rig_byte(packed, 8u, 0.82)",
            "actor_attack_rig_byte(packed, 16u, 0.50)",
            "actor_attack_rig_byte(packed, 24u, 0.18)",
        )
        for law in required_slash_decoder_laws:
            if law not in slash_decoder:
                errors.append(f"Product slash-rig decoder lost {law}")
    else:
        attached_scheduler = balanced_shader_function_body(
            product,
            "bool actor_attached_effect_schedules_cell(",
        )
        for law in (
            "uint phase = actor_attack_phase_id_for_schedule(row);",
            "bool attack_active = actor_cell_word(12, row) != 0u",
            "&& (phase == 2u || phase == 3u);",
        ):
            if law not in attached_scheduler:
                errors.append(f"Product attack attached-effect scheduler lost {law}")

    firefly_builder = function_body(effect, "func _build_local_light_records(")
    firefly_call = (
        "_add_firefly_records(candidates, glyph_candidates, "
        "frame_index, time_seconds)"
    )
    if "baseline_feature_firefly_light" in effect:
        errors.append("firefly publication still has a mutable feature owner")
    if firefly_call not in firefly_builder:
        errors.append("local-light builder does not publish fireflies")
    if f"if baseline_feature_firefly_light:\n\t\t{firefly_call}" in firefly_builder:
        errors.append("firefly publisher is still default-off")
    if '_manual_env_feature_overrides["firefly_light"] = false' in world:
        errors.append("World still forces fireflies off")
    if 'set_baseline_feature_enabled", "firefly_light", false' in world:
        errors.append("World still sends the retired firefly disable command")
    process_body = function_body(world, "func _process(delta: float) -> void:")
    if process_body.count("_sync_manual_environment_light_anchor()") != 1:
        errors.append(
            "the one live World process path does not refresh the firefly anchor"
        )
    firefly_publisher = function_body(effect, "func _add_firefly_records(")
    required_firefly_admission = (
        (
            "if not celestial.night_enabled():\n"
            '\t\t_bump_analyzer_counter("firefly.rejected_day")\n'
            "\t\treturn",
            "night",
        ),
        (
            "if weather_kind == WEATHER_KIND_RAIN "
            "or weather_kind == WEATHER_KIND_LIGHT_RAIN:\n"
            '\t\t_bump_analyzer_counter("firefly.rejected_rain")\n'
            "\t\treturn",
            "rain",
        ),
        (
            "if weather_kind in [WEATHER_KIND_LIGHT_SNOW, "
            "WEATHER_KIND_HEAVY_SNOW, WEATHER_KIND_BLIZZARD]:\n"
            '\t\t_bump_analyzer_counter("firefly.rejected_snow")\n'
            "\t\treturn",
            "snow",
        ),
        ("if not _firefly_anchor_set:\n\t\treturn", "player anchor"),
        (
            "if _firefly_grass_anchors.is_empty():\n"
            '\t\t_bump_analyzer_counter("firefly.rejected_no_grass_anchors")\n'
            "\t\treturn",
            "grass anchor",
        ),
    )
    for law, name in required_firefly_admission:
        if law not in firefly_publisher:
            errors.append(f"firefly publisher lost its {name} admission gate")

    glyph_product = shader_function_body(
        product,
        "EffectCellProduct compute_local_light_glyph_cell_product(",
        "\n// FL-4431 VSB ACTIVATION CUT (step 1c): legacy local-light volume",
    )
    glyph_count_reader = shader_function_body(
        product,
        "uint local_light_glyph_count_from_receipt()",
        "\nBloodFact blood_fact_at_world(",
    )
    required_glyph_count_laws = (
        "product_vsb_frame.cloud_night.w",
        "isnan(encoded_count)",
        "isinf(encoded_count)",
        "encoded_count > float(LOCAL_LIGHT_MAX_GLYPH_RECORDS)",
        "encoded_count == float(count) ? count : 0u",
    )
    for law in required_glyph_count_laws:
        if law not in glyph_count_reader:
            errors.append(f"firefly glyph count authority lost {law}")
    required_glyph_receiver_laws = (
        "if (!local_light_records_enabled()) return product;",
        "uint glyph_count = local_light_glyph_count_from_receipt();",
        "if (glyph_count == 0u) return product;",
        "if (final_kind == OWNER_ACTOR_SPRITE) return product;",
        "if (uint(i) >= glyph_count) break;",
        "if (alpha <= 0.001) continue;",
        "if (local_light_glyph_kind(i) == 0) continue;",
        "if (glyph_depth < 0.0 || glyph_depth > world_depth + 0.25) continue;",
        "if (best_alpha <= 0.0 || best_glyph == 0u) return product;",
    )
    for law in required_glyph_receiver_laws:
        if law not in glyph_product:
            errors.append(f"firefly glyph receiver law lost {law}")
    if (
        glyph_product.find("if (uint(i) >= glyph_count) break;")
        > glyph_product.find("local_light_glyph_alpha(i)")
    ):
        errors.append("firefly glyph prefix guard moved after the first b13 load")
    if "OWNER_SKY" in glyph_product:
        errors.append("firefly glyphs are still suppressed on the sky receiver")

    finalizer = shader_function_body(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\n// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4",
    )
    # Bonfire smoke and airplane vapor share one retained dynamic-tree walk.
    # Their nearest-hit state stays separate, and each paint layer remains at
    # its historical finalizer position.
    vapor_mover = shader_function_body(
        product,
        "void vsb_retained_consider_vapor_mover(",
        "\n// Walk the retained dynamic TLAS once",
    )
    vapor_candidate = shader_function_body(
        product,
        "void vsb_retained_consider_vapor_candidate(",
        "\n// Inspect one retained mover once",
    )
    vapor_selector = shader_function_body(
        product,
        "VsbRetainedVaporSelections "
        "vsb_retained_select_vapor_effect_candidates(",
        "\nVsbRetainedDynamicSelection "
        "vsb_retained_select_dynamic_candidate(",
    )
    if "vsb_retained_select_vapor_effect_candidate(" in product:
        errors.append("retired boolean-partitioned vapor selector survived")
    if "bonfire_smoke_only" in product:
        errors.append("retired boolean vapor partition survived")
    required_vapor_mover_laws = (
        "facts.actor_effect_kind != 4u",
        "facts.source_id == 0u",
        "facts.material_id == 234u",
        "facts.material_id == 207u",
        "facts.material_id == 211u",
        "selected.bonfire_smoke",
        "selected.airplane_vapor",
    )
    for law in required_vapor_mover_laws:
        if law not in vapor_mover:
            errors.append(f"composite vapor material partition lost {law}")
    if vapor_mover.count("vsb_retained_consider_vapor_candidate(") != 2:
        errors.append("unknown kind-4 material gained a vapor paint lane")
    required_vapor_candidate_laws = (
        "facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz",
        "facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz",
        "depth > world_depth + 0.25",
        "floatBitsToUint(depth) == floatBitsToUint(selected.depth)",
        "mover_object_id, local_face,",
        "selected.identity.object_id, selected.identity.primitive_id)",
    )
    for law in required_vapor_candidate_laws:
        if law not in vapor_candidate:
            errors.append(f"composite vapor winner law lost {law}")
    if "facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz" in vapor_candidate:
        errors.append("retired bowtie vapor split reappeared")
    for law in (
        "leaf_count != vsb_semantic_geometry_counts.dynamic_counts.x",
        "vsb_semantic_geometry_counts.auxiliary_counts.x",
        "stack_size > 0 && pops < node_count",
        "vsb_retained_consider_vapor_mover(",
    ):
        if law not in vapor_selector:
            errors.append(f"composite vapor traversal lost {law}")
    composite_call = (
        "vsb_retained_select_vapor_effect_candidates(\n"
        "\t\t\tinput_value.ray, effect_depth);"
    )
    if finalizer.count("vsb_retained_select_vapor_effect_candidates(") != 1:
        errors.append("Product finalizer does not have exactly one vapor traversal")
    if composite_call not in finalizer:
        errors.append("vapor traversal no longer uses actor-adjusted effect depth")
    else:
        current_order = (
            "float effect_depth = input_value.facts.world_depth;",
            composite_call,
            "vsb_product_collect_local_light_slot_facts(",
            "vapor_layers.bonfire_smoke",
            "uint weather_kind",
            "BloodFact blood = record.r0.y != OWNER_SKY",
            "compute_local_light_glyph_cell_product(",
            "vapor_layers.airplane_vapor",
            "product_daylight_godray_veil(",
        )
        if any(marker not in finalizer for marker in current_order):
            errors.append("independent vapor paint layer marker missing")
        elif [finalizer.index(marker) for marker in current_order] != sorted(
            finalizer.index(marker) for marker in current_order
        ):
            errors.append("independent vapor paint layers moved from their live order")
    if "record.r0.y = OWNER_ACTOR_SPRITE;" in finalizer and not (
        finalizer.index("record.r0.y = OWNER_ACTOR_SPRITE;")
        < finalizer.index("compute_local_light_glyph_cell_product(")
    ):
        errors.append("firefly glyph selection moved before actor paint takeover")

    return errors


def test_godray_dependency_parking_contract() -> None:
    """The retired Product/host edges stay absent while F14 is parked."""
    errors = godray_active_owner_errors(
        _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    )
    errors.extend(godray_retirement_errors(
        EFFECT.read_text(encoding="utf-8"),
        WORLD.read_text(encoding="utf-8"),
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
    ))
    assert not errors, "\n".join(errors)


def test_godray_dependency_parking_mutations() -> None:
    """Restoring either ce72 edge makes the focused reachability proof red."""
    effect = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    marker = "\n// FL-4425: one unfiltered receiver sample"
    assert product.count(marker) == 1
    active_call = (
        "\t\tProductDaylightGodrayVeil godray_veil = "
        "product_daylight_godray_veil(\n"
        "\t\t\tinput_value.ray, effect_depth, capture_godray_visibility);"
    )
    mutants = (
        product.replace(
            active_call,
            "\t\tProductDaylightGodrayVeil godray_veil = product_empty_daylight_godray_veil();",
            1,
        ),
        product.replace(
            marker,
            "\nvec3 product_spike_2am_look = vec3(0.0);\n"
            + marker,
            1,
        ),
    )
    for mutant in mutants:
        errors = godray_active_owner_errors(mutant)
        errors.extend(godray_retirement_errors(effect, world, mutant))
        assert errors


def test_contract() -> None:
    """The live tree must expose all three secondary-effect call edges."""
    errors = contract_errors(
        EFFECT.read_text(encoding="utf-8"),
        WORLD.read_text(encoding="utf-8"),
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
        ACTOR_STACK.read_text(encoding="utf-8"),
    )
    assert not errors, "\n".join(errors)


def test_firefly_glyph_receiver_admission() -> None:
    """Run the receiver cases that the source contract binds to Product."""
    assert firefly_glyph_admitted(
        OWNER_SKY, 100.0, 2, 0xFA, 0.5, 6.0
    ), "a valid firefly in front of sky must remain visible"
    assert not firefly_glyph_admitted(
        OWNER_TERRAIN, 5.0, 2, 0xFA, 0.5, 5.5
    ), "a firefly beyond the receiver's 0.25 depth slack must stay occluded"
    assert not firefly_glyph_admitted(
        OWNER_ACTOR_SPRITE, 5.0, 2, 0xFA, 0.5, 4.0
    ), "actor-authored glyph paint must not be replaced by a firefly"
    assert not firefly_glyph_admitted(
        OWNER_SKY, 100.0, 2, 0, 0.5, 6.0
    ), "glyph id zero is explicit absence"
    assert not firefly_glyph_admitted(
        OWNER_SKY, 100.0, 0, 0xFA, 0.5, 6.0
    ), "kind zero is an unused glyph-buffer row"
    assert not firefly_glyph_admitted(
        OWNER_SKY, 100.0, 2, 0xFA, 0.001, 6.0
    ), "threshold alpha is not visible glyph evidence"


def test_destructive_mutations() -> None:
    """Each historical suppression shape must make the contract fail."""
    effect = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    actor_stack = ACTOR_STACK.read_text(encoding="utf-8")
    glyph_function_start = (
        "EffectCellProduct compute_local_light_glyph_cell_product("
    )
    glyph_function_end = (
        "\n// FL-4431 VSB ACTIVATION CUT (step 1c): legacy local-light volume"
    )
    glyph_before_actor = move_source_block_before(
        product,
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        "\tEffectCellProduct glyph_light =",
        "\t// Ordinary contrails and wounded-airplane damage trails historically layer",
        "\tVsbProductLocalLightSlotFacts local_light_facts =",
    )

    mutations = {
        "attack_active_rows_zeroed": (
            effect.replace(
                "return start_byte | (end_byte << 8) | "
                "(center_byte << 16) | (width_byte << 24)",
                "return 0",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
            "attack_default_off_owner_restored": (
                effect,
                world,
                product.replace(
                    "uint actor_attack_phase_id_for_schedule(int row) {",
                    "const bool ACTOR_ATTACK_SWOOSH_ENABLED = false;\n\n"
                    "uint actor_attack_phase_id_for_schedule(int row) {",
                    1,
                ),
                actor_stack,
            ),
        "attack_stack_fact_dropped": (
            effect,
            world,
            product,
            actor_stack.replace(
                '"slash_width_v": float('
                'rig_descriptor.get("slash_width_v", 0.18)),',
                '"slash_width_v": 0.18,',
                1,
            ),
        ),
        "attack_col12_append_removed": (
            effect.replace(
                "words.append(_pack_actor_slash_rig_params(row))",
                "words.append(0)",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
            "attack_product_col12_read_shifted": (
                effect,
                world,
                product.replace(
                    "bool attack_active = actor_cell_word(12, row) != 0u",
                    "bool attack_active = actor_cell_word(13, row) != 0u",
                    1,
                ),
                actor_stack,
            ),
        "attack_active_zero_component_reencoded_as_fallback": (
            effect.replace(
                "\n\t\t1,\n\t\t0xFF\n\t)",
                "\n\t\t0,\n\t\t0xFF\n\t)",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "firefly_host_gate_restored": (
            effect.replace(
                "\t_add_firefly_records(candidates, glyph_candidates, "
                "frame_index, time_seconds)",
                "\tif baseline_feature_firefly_light:\n"
                "\t\t_add_firefly_records(candidates, glyph_candidates, "
                "frame_index, time_seconds)",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "firefly_world_disable_restored": (
            effect,
            world.replace(
                "\t_print_manual_environment_state(reason)",
                '\t_manual_env_feature_overrides["firefly_light"] = false\n'
                '\t_ascii_effect.call("set_baseline_feature_enabled", '
                '"firefly_light", false)\n'
                "\t_print_manual_environment_state(reason)",
                1,
            ),
            product,
            actor_stack,
        ),
        "firefly_anchor_refresh_removed": (
            effect,
            world.replace(
                "\t_sync_manual_environment_light_anchor()\n",
                "\t# MUTANT: live firefly anchor refresh removed.\n",
                1,
            ),
            product,
            actor_stack,
        ),
        "firefly_night_gate_removed": (
            effect.replace(
                "\tif not celestial.night_enabled():\n"
                '\t\t_bump_analyzer_counter("firefly.rejected_day")\n'
                "\t\treturn\n",
                "",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "firefly_rain_snow_gates_removed": (
            effect.replace(
                "\tif weather_kind == WEATHER_KIND_RAIN "
                "or weather_kind == WEATHER_KIND_LIGHT_RAIN:\n"
                '\t\t_bump_analyzer_counter("firefly.rejected_rain")\n'
                "\t\treturn\n"
                "\tif weather_kind in [WEATHER_KIND_LIGHT_SNOW, "
                "WEATHER_KIND_HEAVY_SNOW, WEATHER_KIND_BLIZZARD]:\n"
                '\t\t_bump_analyzer_counter("firefly.rejected_snow")\n'
                "\t\treturn\n",
                "",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "firefly_player_anchor_gate_removed": (
            effect.replace(
                "\tif not _firefly_anchor_set:\n\t\treturn\n",
                "",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "firefly_grass_anchor_gate_removed": (
            effect.replace(
                "\tif _firefly_grass_anchors.is_empty():\n"
                '\t\t_bump_analyzer_counter("firefly.rejected_no_grass_anchors")\n'
                "\t\treturn\n",
                "",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "firefly_sky_receiver_blocked": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "if (final_kind == OWNER_ACTOR_SPRITE) return product;",
                "if (final_kind == OWNER_ACTOR_SPRITE "
                "|| final_kind == OWNER_SKY) return product;",
            ),
            actor_stack,
        ),
        "firefly_finite_depth_gate_removed": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "if (glyph_depth < 0.0 || glyph_depth > "
                "world_depth + 0.25) continue;",
                "if (glyph_depth < 0.0) continue;",
            ),
            actor_stack,
        ),
        "firefly_finite_depth_slack_widened": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "if (glyph_depth < 0.0 || glyph_depth > "
                "world_depth + 0.25) continue;",
                "if (glyph_depth < 0.0 || glyph_depth > "
                "world_depth + 0.50) continue;",
            ),
            actor_stack,
        ),
        "firefly_actor_receiver_admitted": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "\tif (final_kind == OWNER_ACTOR_SPRITE) return product;\n",
                "",
            ),
            actor_stack,
        ),
        "firefly_zero_count_fast_exit_removed": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "\tif (glyph_count == 0u) return product;\n",
                "",
            ),
            actor_stack,
        ),
        "firefly_prefix_guard_moved_after_load": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "\t\tif (uint(i) >= glyph_count) break;\n"
                "\t\tfloat alpha = local_light_glyph_alpha(i);\n",
                "\t\tfloat alpha = local_light_glyph_alpha(i);\n"
                "\t\tif (uint(i) >= glyph_count) break;\n",
            ),
            actor_stack,
        ),
        "firefly_invalid_alpha_admitted": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "\t\tif (alpha <= 0.001) continue;\n",
                "",
            ),
            actor_stack,
        ),
        "firefly_invalid_kind_admitted": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "\t\tif (local_light_glyph_kind(i) == 0) continue;\n",
                "",
            ),
            actor_stack,
        ),
        "firefly_zero_glyph_admitted": (
            effect,
            world,
            replace_in_source_block(
                product,
                glyph_function_start,
                glyph_function_end,
                "if (best_alpha <= 0.0 || best_glyph == 0u) return product;",
                "if (best_alpha <= 0.0) return product;",
            ),
            actor_stack,
        ),
        "firefly_glyph_before_actor_takeover": (
            effect,
            world,
            glyph_before_actor,
            actor_stack,
        ),
            "godray_day_edge_removed": (
                effect,
                world,
                product.replace(
                    "\t\tProductDaylightGodrayVeil godray_veil = "
                    "product_daylight_godray_veil(\n"
                    "\t\t\tinput_value.ray, effect_depth, capture_godray_visibility);",
                    "\t\tProductDaylightGodrayVeil godray_veil = product_empty_daylight_godray_veil();",
                    1,
                ),
            actor_stack,
        ),
        "godray_night_edge_restored": (
            effect,
            world,
            product.replace(
                "\treturn record;\n}\n\n"
                "// ===========================================================================\n"
                "// FL-4431 Q3 Slice 4",
                "\tvec3 night_ray = night_moon_godray_inscatter(effect_receiver);\n"
                "\treturn record;\n}\n\n"
                "// ===========================================================================\n"
                "// FL-4431 Q3 Slice 4",
                1,
            ),
            actor_stack,
        ),
        "godray_host_mode_owner_restored": (
            effect.replace(
                "var fl4359_pool_lod_clamp: bool = false",
                "var fl4359_spike_2am_look: bool = false\n"
                "var fl4359_pool_lod_clamp: bool = false",
                1,
            ),
            world,
            product,
            actor_stack,
        ),
        "godray_world_mode_publisher_restored": (
            effect,
            world.replace(
                '\t\t"ascii_birds",\n',
                '\t\t"ascii_birds",\n\t\t"spike_2am_look",\n',
                1,
            ),
            product,
            actor_stack,
        ),
        "vapor_shared_winner_state": (
            effect,
            world,
            product.replace(
                "facts, retained_anchor, selected.airplane_vapor);",
                "facts, retained_anchor, selected.bonfire_smoke);",
                1,
            ),
            actor_stack,
        ),
        "vapor_material_partition_collapsed": (
            effect,
            world,
            product.replace(
                "if (facts.material_id == 234u) {",
                "if (facts.material_id == 207u) {",
                1,
            ),
            actor_stack,
        ),
        "vapor_unknown_material_admitted": (
            effect,
            world,
            product.replace(
                "\t\t\tcontinue;\n\t\t}\n\t}\n}\n\n"
                "// Walk the retained dynamic TLAS once",
                "\t\t\tvsb_retained_consider_vapor_candidate(\n"
                "\t\t\t\tray, world_depth, mover.identity_range.x, local_face,\n"
                "\t\t\t\tfacts, retained_anchor, selected.airplane_vapor);\n"
                "\t\t}\n\t}\n}\n\n"
                "// Walk the retained dynamic TLAS once",
                1,
            ),
            actor_stack,
        ),
        "vapor_second_traversal_restored": (
            effect,
            world,
            product.replace(
                "vsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);",
                "vsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);\n"
                "\tVsbRetainedVaporSelections duplicate_vapor_layers =\n"
                "\t\tvsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);",
                1,
            ),
            actor_stack,
        ),
        "vapor_bowtie_split_restored": (
            effect,
            world,
            replace_in_source_block(
                replace_in_source_block(
                    product,
                    "void vsb_retained_consider_vapor_candidate(",
                    "// Inspect one retained mover once",
                    "ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz);",
                    "ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz);",
                ),
                "void vsb_retained_consider_vapor_candidate(",
                "// Inspect one retained mover once",
                "ray, facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz);",
                "ray, facts.row_0.xyz, facts.row_3.xyz, facts.row_2.xyz);",
            ),
            actor_stack,
        ),
        "vapor_bonfire_smoke_after_weather": (
            effect,
            world,
                swap_adjacent_blocks(
                    product,
                    "\t// Bonfire smoke layers after local illumination and before weather.",
                    "\tuint weather_kind = cell_owner_weather_kind();",
                    "\tBloodFact blood = record.r0.y != OWNER_SKY",
                ),
                actor_stack,
            ),
        "vapor_airplane_before_glyph_light": (
            effect,
            world,
                swap_adjacent_blocks(
                    product,
                    "\tEffectCellProduct glyph_light =",
                    "\t// Ordinary contrails and wounded-airplane damage trails historically layer",
                    "\tif (!baseline_feature_night_enabled()) {",
                ),
                actor_stack,
            ),
        "vapor_tie_reversed": (
            effect,
            world,
            product.replace(
                "mover_object_id, local_face,\n"
                "\t\t\tselected.identity.object_id, "
                "selected.identity.primitive_id);",
                "selected.identity.object_id, "
                "selected.identity.primitive_id,\n"
                "\t\t\tmover_object_id, local_face);",
                1,
            ),
            actor_stack,
        ),
        "vapor_depth_slack_drifted": (
            effect,
            world,
            product.replace(
                "if (depth < 0.0 || depth > world_depth + 0.25) return;",
                "if (depth < 0.0 || depth > world_depth + 0.50) return;",
                1,
            ),
            actor_stack,
        ),
    }
    for (
        name,
        (mutant_effect, mutant_world, mutant_product, mutant_actor_stack),
    ) in mutations.items():
        assert (
            mutant_effect != effect
            or mutant_world != world
            or mutant_product != product
            or mutant_actor_stack != actor_stack
        ), f"mutation did not alter source: {name}"
        assert contract_errors(
            mutant_effect,
            mutant_world,
            mutant_product,
            mutant_actor_stack,
        ), f"mutation escaped contract: {name}"


def main() -> int:
    """Run the same assertions when invoked through the repository front door."""
    test_contract()
    test_firefly_glyph_receiver_admission()
    test_destructive_mutations()
    print(
        "FL4431_VISUAL_EFFECT_REACHABILITY_CONTRACT_PASS "
        f"mutants={len(_mutation_names())}"
    )
    return 0


def _mutation_names() -> tuple[str, ...]:
    """Keep the front-door total explicit without duplicating mutation bodies."""
    return (
        "attack_active_rows_zeroed",
        "attack_default_off_owner_restored",
        "attack_stack_fact_dropped",
        "attack_col12_append_removed",
        "attack_product_col12_read_shifted",
        "attack_active_zero_component_reencoded_as_fallback",
        "firefly_host_gate_restored",
        "firefly_world_disable_restored",
        "firefly_anchor_refresh_removed",
        "firefly_night_gate_removed",
        "firefly_rain_snow_gates_removed",
        "firefly_player_anchor_gate_removed",
        "firefly_grass_anchor_gate_removed",
        "firefly_sky_receiver_blocked",
        "firefly_finite_depth_gate_removed",
        "firefly_finite_depth_slack_widened",
        "firefly_actor_receiver_admitted",
        "firefly_invalid_alpha_admitted",
        "firefly_invalid_kind_admitted",
        "firefly_zero_glyph_admitted",
        "firefly_glyph_before_actor_takeover",
        "godray_day_edge_removed",
        "godray_night_edge_restored",
        "godray_host_mode_owner_restored",
        "godray_world_mode_publisher_restored",
        "vapor_shared_winner_state",
        "vapor_material_partition_collapsed",
        "vapor_unknown_material_admitted",
        "vapor_second_traversal_restored",
        "vapor_bowtie_split_restored",
        "vapor_bonfire_smoke_after_weather",
        "vapor_airplane_before_glyph_light",
        "vapor_tie_reversed",
        "vapor_depth_slack_drifted",
    )


if __name__ == "__main__":
    raise SystemExit(main())
