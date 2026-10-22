#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _glsl_function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def test_cell_owner_product_dispatch_uses_cached_uniform_set() -> None:
    src = EFFECT.read_text(encoding="utf-8")
    start = src.index("func _dispatch_cell_owner_product(")
    body = src[start:]

    assert "rd.uniform_set_create(" not in body
    assert "var uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [" in body
    assert "rd.compute_list_bind_uniform_set(cl, uset, 0)" in body

    cached_start = body.index("var uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [")
    cached_end = body.index("])", cached_start)
    cached = body[cached_start:cached_end]
    for uniform_name in [
        "u_splat_depth",
        "u_corner",
        "u_actor_cells",
        "u_terrain_mat",
        "u_static_world_mesh",
        "u_dynamic_mover",
        "u_cell_owner",
        "u_blood_mark",
        "u_ll_ctrl",
        "u_ll_record",
        "u_ll_glyph",
        "u_matlib",
        "u_elev",
        "u_wake",
        "u_wake_clock",
        "u_fol_grass",
        "u_fol_accent",
        "u_fol_alb2",
        "u_fol_alb3",
        "u_fol_wind",
        "u_actor_authored_cells",
    ]:
        assert uniform_name in cached


def test_tree_dependent_foliage_product_owner_stays_deleted() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "void compute_product_foliage_fact(" in src
    assert "STATIC_WORLD_TREE_LEAF_MAT" not in src
    assert "leaf_product_attached" not in src


def test_wake_trail_rows_are_stable_source_facts() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "func _refresh_wake_trail_ages" not in effect
    ensure_start = effect.index("func _ensure_wake_trail_buffer_synced() -> void:")
    ensure_end = effect.index("\nfunc _ensure_wake_clock_buffer_synced() -> void:", ensure_start)
    ensure_body = effect[ensure_start:ensure_end]
    assert "_set_wake_current_server_time(server_time_s)" in ensure_body
    assert "_prune_expired_wake_sources(server_time_s)" in ensure_body
    assert "_wake_trail_buffer_dirty = true" not in ensure_body
    assert "func _ensure_wake_clock_buffer_synced() -> void:" in effect
    assert "u_wake_clock.binding = 22" in effect

    assert "layout(set = 0, binding = 22, std140) uniform WakeClockBuffer" in product
    assert "float wake_trail_age_seconds(vec4 point)" in product
    assert "wake_clock.value.x - point.w" in product
    assert "float age = point.z;" not in product
    assert "candidate.z >= point.z" not in product
    assert "candidate.w >= point.w" in product


def test_foliage_anchor_surface_facts_are_cached_per_anchor() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "FoliageBlade foliage_blade_from_anchor(ivec2 anchor_cell, int slot, FoliageSurfaceFacts sf)" in product
    coverage = _glsl_function(product, "FoliageCoverage foliage_blade_coverage_at_cell")

    assert "FoliageSurfaceFacts anchor_facts = foliage_surface_facts(anchor);" in coverage
    assert "if (!anchor_facts.proven_grass) continue;" in coverage
    assert "foliage_blade_from_anchor(anchor, slot, anchor_facts)" in coverage
    assert "foliage_surface_facts(anchor).proven_grass" not in coverage


def test_foliage_grass_admission_uses_cached_target_fact() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "foliage_cards_cell_is_plantable_surface" not in product

    coverage = _glsl_function(product, "FoliageCoverage foliage_blade_coverage_at_cell")
    target_start = coverage.index("FoliageSurfaceFacts target_facts = foliage_surface_facts(cell);")
    reject_start = coverage.index("if (target_family == TERRAIN_FAMILY_CLIFF_ROCK", target_start)
    target_body = coverage[target_start:reject_start]
    grass_start = coverage.index("if (target_family == TERRAIN_FAMILY_GRASS) {")
    dirt_start = coverage.index("} else if (target_family == TERRAIN_FAMILY_DIRT)", grass_start)
    grass_body = coverage[grass_start:dirt_start]

    assert "int target_mat = target_facts.material_id;" in target_body
    assert "int target_family = target_facts.material_family;" in target_body
    assert "splat_mat_at(cell)" not in target_body
    assert "classify_terrain_material_family(target_mat" not in target_body
    assert "if (!target_facts.proven_grass) return c;" in grass_body
    assert "if (target_facts.slope > 2.0) return c;" in grass_body
    assert "any_grass_near = true;" in grass_body
    assert "for (int dz" not in grass_body
    assert "foliage_surface_facts(" not in grass_body


def test_foliage_dirt_edge_admission_uses_one_surface_fact_scan() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    coverage = _glsl_function(product, "FoliageCoverage foliage_blade_coverage_at_cell")
    dirt_start = coverage.index("} else if (target_family == TERRAIN_FAMILY_DIRT)")
    unknown_start = coverage.index("} else {\n\t\treturn c; // UNKNOWN family", dirt_start)
    dirt_body = coverage[dirt_start:unknown_start]

    assert "FoliageSurfaceFacts nf = foliage_surface_facts(n);" in dirt_body
    assert "nf.material_family == TERRAIN_FAMILY_CLIFF_ROCK" in dirt_body
    assert "nf.material_family == TERRAIN_FAMILY_WATER_SKIP" in dirt_body
    assert "is_visible_water_mat(nf.material_id)" in dirt_body
    assert "int n_mat = splat_mat_at(n);" not in dirt_body
    assert "classify_terrain_material_family(n_mat" not in dirt_body
    assert dirt_body.count("for (int dz = -FOLIAGE_EDGE_SOIL_REJECT_RADIUS") == 1
    assert "for (int dz = -FOLIAGE_DENSE_QUERY_RADIUS" not in dirt_body
    assert "if (nf.proven_grass) {" in dirt_body
    assert "float drop = abs(target_facts.surface_y - nf.surface_y);" in dirt_body


def test_foliage_scatter_density_is_reused_for_acceptance_and_export() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "float foliage_scatter_density_at(ivec2 anchor_cell, FoliageSurfaceFacts sf, vec2 player_xz)" in product
    assert "float foliage_player_exclusion_factor_from_player(vec2 world_xz, vec2 player_xz)" in product
    assert "float foliage_player_exclusion_factor(vec2 world_xz)" not in product
    assert "bool foliage_scatter_accept_anchor(ivec2 anchor_cell, int slot, FoliageSurfaceFacts sf, float density)" in product
    density_start = product.index("float foliage_scatter_density_at(")
    density_end = product.index("\n}\n\nbool foliage_scatter_accept_anchor", density_start)
    density_body = product[density_start:density_end]
    assert "foliage_decode_player_xz(" not in density_body
    assert "foliage_player_exclusion_factor_from_player(world_xz, player_xz)" in density_body

    accept_start = product.index("bool foliage_scatter_accept_anchor(")
    accept_end = product.index("\n}\n\nfloat foliage_bayer4", accept_start)
    accept_body = product[accept_start:accept_end]
    assert "foliage_scatter_density_at(" not in accept_body

    blade_start = product.index("FoliageBlade foliage_blade_from_anchor")
    blade_end = product.index("\n}\n\n// FL-4184/FL-4231", blade_start)
    blade_body = product[blade_start:blade_end]
    assert blade_body.count("foliage_decode_player_xz()") == 1
    assert blade_body.count("foliage_scatter_density_at(anchor_cell, sf, player_xz)") == 1
    assert "foliage_scatter_accept_anchor(anchor_cell, slot, sf, scatter_density)" in blade_body
    assert "foliage_player_exclusion_factor_from_player(cell_xz, player_xz)" in blade_body
    assert "b.scatter_density = scatter_density;" in blade_body


def test_local_light_records_are_signature_cached_and_beam_time_is_product_owned() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "var _local_light_record_signature: String = \"\"" in effect
    assert "func _local_light_records_signature(records: Array) -> String:" in effect
    assert "var record_cache_hit := record_signature == _local_light_record_signature" in effect
    assert "if not record_cache_hit:" in effect
    assert "\"record_cache_hit\": record_cache_hit" in effect
    assert "cone.flags = 1 if _lighthouse_beam_azimuth_pinned else 0" in effect
    assert "cone.anchor_x = _lighthouse_beam_azimuth_pin if _lighthouse_beam_azimuth_pinned else LIGHTHOUSE_BEAM_DEFAULT_AZIMUTH" in effect

    assert "float local_light_frame_time_seconds() { return local_light_ctrl.control1.y; }" in product
    assert "const float LIGHTHOUSE_BEAM_ROT_RAD_PER_S = 0.5235987756;" in product
    assert "const int LOCAL_LIGHT_FLAG_PINNED_AZIMUTH = 1;" in product
    cone_start = product.index("LighthouseCone lighthouse_cone_from_record(int idx)")
    cone_end = product.index("\n}\n\nfloat lighthouse_cone_ray_contribution", cone_start)
    cone_body = product[cone_start:cone_end]
    assert "az = mod(LIGHTHOUSE_BEAM_DEFAULT_AZIMUTH + local_light_frame_time_seconds() * LIGHTHOUSE_BEAM_ROT_RAD_PER_S" in cone_body


def test_terrain_and_water_source_samples_are_owner_gated() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "ProductWaterSample empty_product_water_sample()" in product
    main_start = product.index("void main()")
    main = product[main_start:]
    assert "bool paint_is_terrain = paint_kind == OWNER_TERRAIN;" in main
    assert "bool paint_is_water = paint_kind == OWNER_WATER;" in main
    assert "ProductWaterSample water_sample = empty_product_water_sample();" in main

    terrain_gate = main.index("if (paint_is_terrain) {")
    water_gate = main.index("if (paint_is_water) {")
    terrain_body = main[terrain_gate:water_gate]
    water_body = main[water_gate:main.index("vec3 product_effect_receiver_world", water_gate)]
    assert "terrain_surface_point_for_ray(ray, terr_cell)" in terrain_body
    assert "product_water_world_xz(ray, terr_cell)" not in terrain_body
    assert "product_water_world_xz(ray, terr_cell)" in water_body
    assert "product_water_sample(water_sample_cell, water_sample_world_xz, water_sample_depth)" in water_body


def test_static_tree_shadow_scan_stays_deleted() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "static_tree_shadow_fact_at_world" not in product
    assert "STATIC_TREE_SHADOW_SOURCE_BASE" not in product
    assert "STATIC_WORLD_TREE_LEAF_MAT" not in product


def test_final_cell_runtime_audit_reports_selected_payload_coverage() -> None:
    effect = EFFECT.read_text(encoding="utf-8")

    audit_start = effect.index("func _runtime_final_cell_paint_audit(max_cells: int = 0) -> Dictionary:")
    audit_end = effect.index("\nfunc _notification", audit_start)
    audit = effect[audit_start:audit_end]

    for needle in [
        '"selected_payload_rows": 0',
        '"missing_payload_rows": 0',
        '"selected_payload_coverage": 0.0',
        '"ink_cell_coverage": 0.0',
        '"blank_glyph_coverage": 0.0',
        '"proves_selected_payload_row_coverage": false',
        'result["selected_payload_rows"] = int(result["selected_payload_rows"]) + 1',
        'result["missing_payload_rows"] = maxi(int(result["sampled_cells"]) - int(result["selected_payload_rows"]), 0)',
        'result["selected_payload_coverage"] = float(result["selected_payload_rows"]) / denom',
        'result["proves_selected_payload_row_coverage"] = int(result["sampled_cells"]) > 0 and int(result["missing_payload_rows"]) == 0',
    ]:
        assert needle in audit
