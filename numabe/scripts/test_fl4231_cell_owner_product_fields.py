#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"


def test_cell_owner_product_populates_cell_level_fields() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "layout(set = 0, binding = 38) uniform sampler2D actor_sprite_atlas_tex;",
        "uint cell_owner_glyph_candidate(",
        "bool source_is_dynamic_mover(uint winner)",
        "if (source_is_dynamic_mover(winner))",
        # FL-4231 blocker-1/3/4: typed product taxonomy. Dynamic movers are typed
        # from the face airframe_id discriminator; projectile/vapor become typed
        # products instead of being folded into OWNER_DYNAMIC_MESH.
        "const uint OWNER_PROJECTILE   = 8u;",
        "const uint OWNER_VAPOR        = 10u;",
        "const uint OWNER_RETICLE      = 12u;",
        "uint dynamic_mover_typed_kind(uint airframe_id)",
        "if (airframe_id == 2u) return OWNER_PROJECTILE;",
        "if (airframe_id == 3u) return OWNER_RETICLE;",
        "if (airframe_id == 4u) return OWNER_VAPOR;",
        "h.kind = dynamic_mover_typed_kind(uint(fr2.w + 0.5));",
        "paint_kind == OWNER_PROJECTILE || paint_kind == OWNER_VAPOR ||",
        "paint_kind == OWNER_RETICLE) && has_mesh_color) {",
        "const uint PAINT_FLAG_WATERLINE_CLIP = 1u << 0;",
        "const uint PAINT_FLAG_RAIN = 1u << 1;",
        "const uint PAINT_FLAG_SNOW = 1u << 2;",
        "const uint PAINT_FLAG_WEATHER_GLYPH = 1u << 3;",
        "bool actor_sample_survives_waterline_clip(int row, vec2 local_uv, bool base_is_water)",
        "struct ActorStandeeSample",
        "ActorStandeeSample actor_standee_sample_for_ray(int row, ViewRay ray)",
        "vec2 world_size = max(actor_fixed256_pair(row, 13), vec2(0.001));",
        "vec2 anchor = actor_fixed256_pair(row, 14);",
        "float actor_cell_alpha_coverage_and_depth(int row, ivec2 cell_min_px, ivec2 cell_px, ivec2 ansi_xy, vec2 fb_size, bool base_is_water, out float nearest_actor_depth, inout uint reject_bits, inout float nearest_world_depth)",
        "vec4 sample_color = texture(actor_sprite_atlas_tex, actor_atlas_uv_from_local(row, standee.local_uv));",
        "const uint ACTOR_REJECT_ALPHA_VISIBLE = 1u << 2u;",
        "const uint ACTOR_REJECT_WORLD_OCCLUDED = 1u << 4u;",
        "actor_candidate_reject_bits |= ACTOR_REJECT_RECT_OVERLAP;",
        "reject_bits |= ACTOR_REJECT_ALPHA_VISIBLE;",
        "reject_bits |= ACTOR_REJECT_WORLD_OCCLUDED;",
        "ProductHit sample_world = product_world_hit_for_sample(sample_ray, ansi_xy);",
        "if (sample_world.valid && actor_t > sample_world.depth + ACTOR_OCCLUSION_EPSILON) {",
        "bool terrain_like = sample_world.kind == OWNER_TERRAIN || sample_world.kind == OWNER_WATER;",
        "bool terrain_occludes_actor = terrain_like && sample_world.receiver_world.y > actor_foot_world(row).y + 0.55;",
        "if (!terrain_like || terrain_occludes_actor) {",
        "uint cell_owner_weather_kind()",
        "uint cell_owner_weather_flags(uint kind, ivec2 ansi_xy, ivec2 terr_cell, uint paint_kind)",
        "void cell_owner_apply_weather_paint(uint weather_kind, uint weather_flags, ivec2 ansi_xy, vec2 surface_world_xz, ViewRay ray, float surface_depth, uint paint_kind, inout uint glyph, inout vec3 fg, inout vec3 bg)",
        "uint terrain_cell_elevation_class(ivec2 cell)",
        "uint terrain_cell_material_boundary(ivec2 cell, uint mat)",
        "float terrain_foliage_coverage(ivec2 cell, uint owner_kind, uint mat, uint material_family)",
        "uint terrain_foliage_density_class(float coverage)",
        # FL-4231 PATCH-A: foliage_glyph_candidate (CP437 tufts) DELETED (Law 5).
        # assert the new product fact owner signature instead:
        "void compute_product_foliage_fact(",
        "uint water_shoreline_neighbor_mask(ivec2 cell)",
        "uint water_stage_class(ivec2 cell, float water_depth)",
        "uint water_glyph_candidate(ProductWaterSample s)",
        "uint sky_glyph_candidate(ivec2 ansi_xy)",
        "vec3 sky_paint_fg(ivec2 ansi_xy)",
        "vec3 sky_paint_bg(ivec2 ansi_xy)",
        "vec3 water_paint_fg(ProductWaterSample s)",
        "vec3 water_paint_bg(ProductWaterSample s)",
        "uint terrain_material_glyph_candidate(ivec2 cell, uint mat, uint material_family, uint foliage_density_class)",
        "uint cell_owner_material_family(",
        "uint cell_owner_shadow_class(",
        "uint cell_owner_foliage_class(",
        "uint cell_owner_lighting_class(",
        "vec3 cell_owner_paint_fg(",
        "vec3 cell_owner_paint_bg(",
        "void cell_owner_apply_blood_paint(uint blood_visible, inout vec3 fg, inout vec3 bg)",
        "float foliage_coverage = terrain_foliage_coverage(terr_cell, paint_kind, world_mat, world_mat_family);",
        "uint foliage_density_class = terrain_foliage_density_class(foliage_coverage);",
        "uint glyph_candidate = (paint_kind == OWNER_WATER)",
        "? water_glyph_candidate(water_sample)",
        ": cell_owner_glyph_candidate(paint_kind, world_mat, world_mat_family, terr_cell, ansi_xy, foliage_density_class);",
        "best_actor_paint_flags = world_is_water ? PAINT_FLAG_WATERLINE_CLIP : 0u;",
        # FL-4231 PATCH-A: species glyphs 617-634 written to r16/r17 by product fact owner.
        # CP437 dense-grass row is deleted; assert r16/r17 writes exist instead:
        "c.r16 = foliage_r16;",
        "c.r17 = foliage_r17;",
        "c.r2 = uvec4(glyph_candidate, world_mat, world_mat_family,",
        "c.r3 = uvec4(best_actor_frame_id, best_actor_direction_id,",
        "c.r6 = uvec4(cell_owner_shadow_class(final_kind), blood_visible,",
        "uint paint_flags = c.r3.w | cell_owner_weather_flags(weather_kind, ansi_xy, terr_cell, paint_kind);",
        "cell_owner_apply_weather_paint(weather_kind, paint_flags, ansi_xy, product_visible_receiver_world.xz, ray, world_depth, paint_kind, glyph_candidate, paint_fg, paint_bg);",
        "cell_owner_apply_blood_paint_intensity(blood_fact.intensity, paint_fg, paint_bg);",
        "c.r3.w = paint_flags;",
        "c.r7 = vec4(shadow_density, shadow_attenuation, foliage_coverage, float(foliage_density_class));",
        "uint terrain_detail_fact = (paint_kind == OWNER_TERRAIN)",
        "product_pack_terrain_detail_fact(",
        "c.r10 = uvec4(shadow_caster_id, cloud_shadow_density_u16, terrain_detail_fact, floatBitsToUint(world_depth));",
        "c.r11 = vec4(shadow_caster_xz, shadow_luma_before, shadow_luma_after);",
        "c.r12 = uvec4(blood_mark_id, floatBitsToUint(blood_radius), blood_fact.source_event_id, final_kind);",
        "c.r13 = vec4(blood_mark_xz, blood_intensity, actor_candidate_world_depth);",
        "c.r14 = uvec4(terrain_elevation_class_value, terrain_material_boundary_value, actor_presentation_id_value, actor_candidate_reject_bits);",
        # FL-4231 waterline invariant: r15.x still carries the actor waterline clip
        # threshold. FL-4270 (commit 9cfd64950) extended r15.yz with sub-tile world
        # XZ facts WITHOUT displacing the waterline field, so assert the .x prefix
        # rather than the full single-line form.
        "c.r15 = vec4(actor_waterline_clip_threshold_value,",
        "c.r8 = vec4(paint_fg, 1.0);",
        "c.r9 = vec4(paint_bg, 1.0);",
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        "actor_atlas_uv(",
        "actor_atlas_uv_for_pixel(",
        "glyph_id filled by paint refactor",
        "shadow/blood/foliage/lighting facts are attached by the paint refactor",
        "fl4269_apply_blood_mark_to_owner_pixel(",
        "if (mat == 1u) return 44u;",
        "if (mat == 2u || mat == 4u) return 46u;",
        "foliage_density_class = 0u;",
        "if (owner_kind == OWNER_WATER) return 126u;",
        "if (owner_kind == OWNER_WATER) return vec3(0.22, 0.56, 0.92);",
        "if (owner_kind == OWNER_WATER) return vec3(0.04, 0.13, 0.30);",
        "if (owner_kind == OWNER_SKY) return vec3(0.74, 0.80, 0.88);",
        "if (owner_kind == OWNER_SKY) return vec3(0.50, 0.58, 0.70);",
        "int static_face_count = params.cell_dims.w;",
        "? OWNER_DYNAMIC_MESH : OWNER_STATIC_MESH",
    ]
    for needle in forbidden:
        assert needle not in src, needle


def test_water_glyph_phase_is_water_cell_owned_not_screen_cell_owned() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    sample_start = src.index("ProductWaterSample product_water_sample(")
    sample_end = src.index("\n}\n\nuint product_water_activity_glyph", sample_start)
    sample_body = src[sample_start:sample_end]
    assert "ProductWaterSample product_water_sample(ivec2 cell, vec2 sample_world_xz, float water_depth)" in sample_body
    assert "s.cell = cell;" in sample_body
    assert "s.world_xz = sample_world_xz;" in sample_body
    assert "float time = params.feature_params.x;" not in sample_body
    assert "float ambient_time = product_water_animation_time();" in sample_body
    assert "float stable_time = 0.0;" not in sample_body
    assert "s.wave_height = product_water_wave_shape(wave_uv, ambient_time);" in sample_body
    assert "s.surface_foam = smoothstep(0.55, 0.85, surf_n);" in sample_body
    assert "ProductWakeFact wake_fact = product_wake_fact(s.world_xz);" in sample_body
    assert "s.break_active = product_fl4216_break_active(s, ambient_time, wave_stage);" in sample_body
    assert "s.activity_phase = product_water_activity_phase(s, ambient_time);" in sample_body
    assert "vec3 wake_color = mix(WATER_FOAM, vec3(0.58, 0.88, 1.0), 0.35);" in sample_body
    assert "s.bg = clamp(mix(s.bg, wake_color, s.wake * 0.72), vec3(0.0), vec3(1.0));" in sample_body
    assert "s.wake * 0.06" not in sample_body
    assert "ansi_xy" not in sample_body

    phase_start = src.index("uint product_water_activity_phase(")
    phase_end = src.index("\n}\n\nProductWaterSample product_water_sample", phase_start)
    phase_body = src[phase_start:phase_end]
    assert "uint product_water_activity_phase(ProductWaterSample s, float time)" in phase_body
    assert "return (s.wake_source_id + uint(floor(clamp(s.wake_along, 0.0, 1.0) * 4.0))) & 1u;" in phase_body
    assert "uint body_hash = uint(s.cell.x * 73856093) ^ uint(s.cell.y * 19349663);" in phase_body
    assert "uint stable_body_phase = body_hash % 3u;" in phase_body
    assert "return stable_body_phase;" in phase_body
    assert "time *" not in phase_body
    assert "fract(travel)" not in phase_body
    assert "ansi_xy" not in phase_body
    assert "water_phase_index_3" not in src
    assert "water_shoreline_neighbor_mask(cell)" not in phase_body


def test_clone_stable_noise_hash_drives_material_texture_lanes() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "float stable_cell_noise(ivec2 cell, uint salt)" in src
    stable_start = src.index("float stable_cell_noise(ivec2 cell, uint salt)")
    stable_end = src.index("\n}\n\nint product_foliage_floor_div", stable_start)
    stable_body = src[stable_start:stable_end]
    assert "uint h = uint(cell.x) * 73856093u ^ uint(cell.y) * 19349663u ^ salt * 83492791u;" in stable_body
    assert "h *= 1274126177u;" in stable_body
    assert "return float(h & 0x00FFFFFFu) / 16777216.0;" in stable_body
    grid_start = src.index("float stable_grid_noise(vec2 p, float scale, uint salt)")
    grid_end = src.index("\n}\n\nfloat stable_value_noise", grid_start)
    assert "return stable_cell_noise(ivec2(floor(p * scale)), salt);" in src[grid_start:grid_end]
    assert "stable_cell_noise(cell, 37u)" in src
    assert "stable_cell_noise(jitter_cell, 41u)" in src
    assert "stable_cell_noise(patch_cell, 410211u)" in src
    assert "stable_cell_noise(cell, 410223u)" in src
    assert "cell_owner_hash01(cell, 37u)" not in src
    assert "cell_owner_hash01(patch_cell, 410211u)" not in src


def test_cloud_body_is_deleted_but_product_shadow_stays_world_projected() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "float stable_grid_noise(vec2 p, float scale, uint salt)" in src
    assert "float sky_cloud_density_at(" not in src
    assert "float sky_cloud_visible_density_at(" not in src
    sky_glyph_start = src.index("uint sky_glyph_candidate(")
    sky_glyph_end = src.index("\n}\n\nuint cell_owner_glyph_candidate", sky_glyph_start)
    assert "sky_cloud" not in src[sky_glyph_start:sky_glyph_end]
    sky_fg_start = src.index("vec3 sky_paint_fg(")
    sky_fg_end = src.index("\n}\n\nvec3 sky_paint_bg", sky_fg_start)
    assert "sky_cloud" not in src[sky_fg_start:sky_fg_end]
    sky_bg_start = src.index("vec3 sky_paint_bg(")
    sky_bg_end = src.index("\n}\n\nfloat product_distance_to_segment", sky_bg_start)
    assert "sky_cloud" not in src[sky_bg_start:sky_bg_end]
    assert "return SKY_BG;" in src[sky_bg_start:sky_bg_end]
    shadow_start = src.index("float product_cloud_shadow_for_cell(")
    shadow_end = src.index("\n}\n\n// FL-4273 moon god rays", shadow_start)
    shadow_body = src[shadow_start:shadow_end]
    assert "return product_cloud_density_projected_at(cell, receiver_y);" in shadow_body
    density_start = src.index("float product_cloud_density_at_sample_xz(")
    density_end = src.index("\n}\n\nfloat product_cloud_density_projected_at", density_start)
    density_body = src[density_start:density_end]
    assert "vec2 uv0 = sample_xz * 0.135 + d0 * (time * 0.10);" in density_body
    assert "vec2 uv1 = sample_xz * 0.108 + d1 * (time * 0.10 * 0.89 * 3.14159265 / 3.0);" in density_body
    assert "float n0 = stable_value_noise(bc0 * FL4273_CLOUD_MASS_FREQ, 11u);" in density_body
    assert "float n1 = stable_value_noise(bc1 * FL4273_CLOUD_MASS_FREQ, 23u);" in density_body
    assert "return smoothstep(0.50, 0.82, projected);" in density_body
    assert "vec2 sample_xz = vec2(cell)" not in density_body
    assert "if (architectural_perspective_enabled())" not in density_body
    assert "stable_grid_noise(uv0" not in density_body
    assert "stable_grid_noise(uv1" not in density_body


def test_cloud_shadow_is_product_fact_consumed_by_terrain_paint() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = COMPOSITOR.read_text(encoding="utf-8")

    assert "float product_cloud_shadow_for_cell(ivec2 cell)" in product
    assert "float product_cloud_density_at_sample_xz(vec2 sample_xz)" in product
    assert "void product_apply_ambient_base(inout vec3 fg, inout vec3 bg, vec3 nrm, float lambert_raw, float cloud_shadow, int family)" in product
    assert "r.cloud_shadow = product_cloud_shadow_for_cell(terr_cell);" in product
    assert "product_apply_ambient_base(mfg, mbg, terr_nrm, terr_lambert_raw, r.cloud_shadow, r.family);" in product
    assert "float cloud_floor = (params.misc.w & 134217728) != 0 ? 0.72 : 0.86;" in product
    ambient_start = product.index("void product_apply_ambient_base(")
    ambient_end = product.index("\n}\n\n// ---- FL-4231 parity: material ramp steps", ambient_start)
    ambient_body = product[ambient_start:ambient_end]
    assert "product_apply_chroma_clamp(" not in ambient_body
    shadow_start = product.index("float product_cloud_shadow_for_cell(")
    shadow_end = product.index("\n}\n\nuint terrain_cell_elevation_class", shadow_start)
    shadow_body = product[shadow_start:shadow_end]
    assert "return product_cloud_density_projected_at(cell, receiver_y);" in shadow_body
    assert 'if ((params.misc.w & 134217728) == 0) return 0.0;' not in shadow_body
    assert "product_cloud_density_projected_at(cell + ivec2(" not in shadow_body
    assert "smoothstep(0.03, 0.50, density)" not in shadow_body
    assert "terrain_product_cloud_shadow = terrain_product.cloud_shadow;" in product
    assert "float terrain_cloud_shadow = (paint_kind == OWNER_TERRAIN) ? terrain_product_cloud_shadow : 0.0;" in product
    assert "uint cloud_shadow_density_u16 = uint(round(clamp(terrain_cloud_shadow, 0.0, 1.0) * 65535.0));" in product
    assert "uint terrain_detail_fact = (paint_kind == OWNER_TERRAIN)" in product
    assert "product_pack_terrain_detail_fact(" in product
    assert "c.r10 = uvec4(shadow_caster_id, cloud_shadow_density_u16, terrain_detail_fact, floatBitsToUint(world_depth));" in product

    assert "float cloud_shadow_for_cell(ivec2 cell)" not in compositor
    assert "float cloud_density_projected_at(" not in compositor
    assert "float cloud_density_at(" not in compositor
    assert "float cloud_density_volume_at(" not in compositor


def test_static_world_shadow_is_bound_to_product_owner() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect = EFFECT.read_text(encoding="utf-8")

    assert "layout(set = 0, binding = 34) uniform sampler2D static_world_shadow_tex;" in product
    assert "float product_static_world_shadow_for_cell(ivec2 cell)" in product
    assert "product_apply_static_world_shadow_material_darken(fg, bg, static_shadow);" in product
    assert "var product_static_world_shadow_bind: RID = static_world_shadow_rd if static_world_shadow_rd.is_valid() else _placeholder_rd" in effect
    assert "u_product_static_world_shadow.binding = 34" in effect
    dispatch_start = effect.index("var uset: RID = UniformSetCacheRD.get_cache(cell_owner_product_shader, 0, [")
    dispatch_end = effect.index("var ansi := _cell_owner_ansi_size(fb_size)", dispatch_start)
    product_dispatch = effect[dispatch_start:dispatch_end]
    assert "u_product_static_world_shadow" in product_dispatch


def test_terrain_ramp_is_world_anchored_and_clone_equivalent() -> None:
	src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

	assert "struct ProductMountainMaterialTone" in src
	assert "ProductMountainMaterialTone product_apply_mountain_material_tone_ramp(" in src
	assert "product_mountain_material_tone_palette(" not in src
	assert "uint shade_band;" in src
	assert "g.shade_band = uint(clamp(s00.shd, 0, 15));" in src
	assert "tone.material_tone_band" not in src
	assert "* 7.0), 0.0, 7.0)" not in src
	assert "mountain_tone = product_apply_mountain_material_tone_ramp(" in src
	assert "if (mountain_family) {" in src
	assert "terrain_product.ramp_band = clone_grammar.shade_band & 7u;" in src
	# The deleted cell-argument ramp consumers are intentionally not pinned
	# here. The live destructive single-author terrain contract owns the typed
	# ProductTerrainRampFacts author plus both typed paint consumers.
	assert "r.material_tone_band = mountain_family ? clone_grammar.shade_band : terrain_product.ramp_band;" in src
	assert "(material_tone_band << 16)" in src
	assert "(clone_peak_glyph_lane << 20)" in src
	assert "(convex_edge << 21)" in src
	assert "(concave_edge << 22)" in src
	assert "(heightfield_neighbor_state << 23)" in src
	assert "(sidewall_score_class << 27)" in src
	assert "if (mat == 0u || mat == VISIBLE_WATER_MAT) return 4u;" in src
	assert "if (mat == 1u) return 1u;" in src
	assert "if (mat == 2u || mat == 4u) return 2u;" in src
	assert "if (mat == 3u || mat == 5u) return 3u;" in src
	ramp_start = src.index("void product_apply_material_ramp_steps(")
	ramp_end = src.index("\n}\n\nvoid store_record", ramp_start)
	ramp_body = src[ramp_start:ramp_end]
	family_start = src.index("int product_terrain_family(int mat)")
	family_end = src.index("\n}\n\n", family_start)
	family_body = src[family_start:family_end]
	assert "if (mat == 0 || mat == int(VISIBLE_WATER_MAT)) return TERRAIN_FAMILY_WATER_SKIP;" in family_body
	assert "if (mat == 1) return TERRAIN_FAMILY_GRASS;" in family_body
	assert "if (mat == 2 || mat == 4) return TERRAIN_FAMILY_DIRT;" in family_body
	assert "if (mat == 3 || mat == 5) return TERRAIN_FAMILY_CLIFF_ROCK;" in family_body
	assert "if (mat == 0 || mat == 1 || mat == 3) return TERRAIN_FAMILY_GRASS;" not in family_body
	assert "if (mat == 4 || mat == 5) return TERRAIN_FAMILY_CLIFF_ROCK;" not in family_body
	assert "ivec2 patch_cell = ivec2(floor(vec2(cell) / 4.0));" in ramp_body
	assert "cell_owner_hash01(ansi_xy" not in ramp_body
	assert "float amp = boundary ? 0.18 : 0.34;" in ramp_body
	assert "amp *= 1.30;" in ramp_body
	assert "amp *= 0.95;" in ramp_body
	assert "slope_dark_scale = 0.14;" in ramp_body
	assert "light_hue = vec3(1.10, 1.04, 0.86);" in ramp_body
	assert "amp *= 1.05;" in ramp_body
	assert "local_peak" not in ramp_body
	assert "crest_fg" not in ramp_body
	assert "crest_bg" not in ramp_body
	assert "slope_dark_scale = 0.10;" in ramp_body
	assert "terrain_nonblack_floor(" not in ramp_body
	assert "terrain_apply_nonblack_floor(" not in ramp_body
	assert "product_apply_chroma_clamp(fg, family" not in ramp_body
	assert "clamp(low_band * amp * 1.10 + slope_dark * 1.12, 0.0, 0.52)" in ramp_body
	assert "clamp(low_band * amp * 0.88 + slope_dark * 1.04, 0.0, 0.42)" in ramp_body
	assert "struct ProductTerrainResolve" in src
	assert "struct ProductTerrainProduct" in src
	assert "ProductTerrainProduct product_resolve_terrain_product(" in src
	assert "const uint PRODUCT_TERRAIN_ROLE_RIDGE" in src
	assert "const uint PRODUCT_TERRAIN_ROLE_CLIFF_FACE" in src
	assert "const uint PRODUCT_TERRAIN_ROLE_SLOPE_FACE" in src
	assert "const int PRODUCT_YY_CLIFF[8]" in src
	assert "uint product_clone_parity_terrain_glyph(uint glyph, int family, uint boundary, ivec2 stable_cell, ivec2 terrain_cell, uint weather_flags)" in src
	# The deleted per-consumer mountain-role helper is intentionally not pinned
	# here. The live destructive single-author contract in
	# test_fl4431_product_terrain_receiver_single_source_contract.py owns the
	# ProductTerrainClassificationFacts author and every typed consumer.
	assert "uint product_terrain_ridge_mask(ivec2 cell, int family, uint boundary)" in src
	assert "uint product_terrain_ridge_continuity(uint ridge_mask)" in src
	assert "uint product_terrain_face_normal_class(vec3 nrm, float step_x, float step_z)" in src
	assert "uint product_terrain_glyph_for_product(ProductTerrainProduct p, uint matlib_glyph, int family, int elv)" in src
	assert "uint product_terrain_segment_phase(ProductTerrainProduct p)" in src
	assert "uint product_terrain_face_glyph(ProductTerrainProduct p, uint family_glyph)" in src
	assert "void product_terrain_apply_product_tone(inout ProductTerrainProduct p, vec3 base_fg, vec3 base_bg, float shadow_attenuation)" in src
	assert "p.ridge_segment_id = product_terrain_segment_id(terrain_cell, p.ridge_mask, p.face_normal_class);" in src
	# The deleted ramp-band recomputation is guarded by the same live contract,
	# which requires the resolver to consume the carried ramp.product_band.
	assert "p.contact_shadow = lower_contact ? 1u : ((shadow_attenuation < 0.92) ? 1u : 0u);" in src
	assert "p.glyph = product_terrain_glyph_for_product(p, matlib_glyph, family, elv);" in src
	assert "product_terrain_apply_product_tone(p, base_fg, base_bg, shadow_attenuation);" in src
	assert "ProductTerrainResolve product_resolve_terrain_cell(" in src
	assert "ProductTerrainResolve terrain_product = product_resolve_terrain_cell(" in src
	assert "ProductTerrainProduct terrain_product = product_resolve_terrain_product(" in src
	assert "r.glyph = terrain_product.glyph;" in src
	assert "r.paint_fg = terrain_product.fg;" in src
	assert "r.paint_bg = terrain_product.bg;" in src
	assert "glyph_candidate = product_readable_terrain_glyph(" not in src
	assert "struct ProductTerrainRole" not in src
	assert "ProductTerrainRole product_select_terrain_role(" not in src
	assert "ProductTerrainRole terrain_role = product_select_terrain_role(" not in src
	assert "cell_owner_hash01(stable_cell, 0x4321C71Fu)" not in src
	assert "cell_owner_hash01(stable_cell, 0x4336C10Du)" not in src
	assert "product_yy_glyph_for_family(" not in src
	# FL-4336 Miss #5 (2026-06-19): the invented cliff-rock contrast floor is DELETED. The
	# clone apply_material_glyph_contrast uses role-aware targets only (boundary 0.22 /
	# interior 0.13 / sparse +0.06); the `max(contrast_target, 0.28)` cliff-rock bump
	# over-separated rock glyph-vs-bg into high-contrast speckle the clone never shows.
	assert "contrast_target = max(contrast_target, 0.28);" not in src
	assert "vec2 terr_sub_world_xz = params.terrain_params.xy + vec2(terr_cell) + vec2(0.5);" in src
	assert "vec2 terr_sub_local_xz = terr_sub_world_xz - params.terrain_params.xy;" in src
	assert "ivec2 terr_stable_cell = terr_cell;" in src
	assert "matlib_terrain_resolve(terr_cell, stable_cell, mat, mglyph, mfg, mbg)" in src
	assert "terrain_subcell_stable_cell" not in src
	assert "vec2 water_sample_world_xz = product_water_world_xz(terr_cell);" in src
	assert "vec2 water_sample_world_xz = world_is_water ? receiver_world.xz" not in src
	# FL-4336 N6a/N6c: divergent scalar value owners are DELETED, and the default
	# clone grammar color is the winner cell's s00 matlib color. Four subsamples
	# remain for boundary votes and half-block LAB colors only; averaging every
	# face cell smooths away the clone's stepped texture.
	assert "float micro = 1.0 + (cell_owner_hash01(stable_cell, 0x4270F0A7u) - 0.5) * 0.08;" not in src
	assert "float slope_shade = mix(0.72, 1.14, terr_sun)" not in src
	grammar_start = src.index("ProductTerrainCloneGrammar product_resolve_clone_terrain_grammar(")
	grammar_end = src.index("// FL-4231 mesh-face matlib", grammar_start)
	grammar_body = src[grammar_start:grammar_end]
	assert "g.fg = s00.fg;" in grammar_body
	assert "g.bg = s00.bg;" in grammar_body
	assert "g.fg = lab_avg4(ss_tl.fg, ss_tr.fg, ss_bl.fg, ss_br.fg);" not in grammar_body
	assert "g.bg = lab_avg4(ss_tl.bg, ss_tr.bg, ss_bl.bg, ss_br.bg);" not in grammar_body
	assert "g.fg = lab_avg4(s00.bg, s10.bg, ss_tl.bg, ss_tr.bg);" in grammar_body
	assert "g.bg = lab_avg4(s01.bg, s11.bg, ss_bl.bg, ss_br.bg);" in grammar_body
	assert "const float SILHOUETTE_THRESH = 1.0;" in grammar_body
	assert "float minus = z_lo - z_hi;" in grammar_body
	assert "float under = z_pr - z_lo;" in grammar_body
	assert "true_front_edge" not in grammar_body
	assert "g.glyph_role = 5u;" not in grammar_body
	assert "float lap = (hl + hr + hd + hu) - 4.0 * hc;" not in grammar_body
	assert "uint pal[8] = uint[8](0x2Eu, 0x2Cu, 0x3Au, 0x3Bu, 0x60u, 0x27u, 0xFAu, 0x2Du);" in grammar_body
	assert "uint pal[8] = uint[8](0x2Fu, 0x5Cu, 0x3Du, 0x2Du, 0x7Eu, 0x5Fu, 0x60u, 0x27u);" in grammar_body
	assert "g.fg = rec709_outline_ink(g.fg, g.bg, ink_strength);" not in grammar_body
	# FL-4336 Attempt 175: Attempt 165's symmetric 4-neighbour normal was an invented
	# owner. The product path must use the clone three-corner source normal everywhere,
	# including edge scoring, so value, crest, and edge facts share one normal owner.
	assert "vec3 nrm = normalize(vec3(hl - hr, 2.0, hd - hu));" not in src
	assert "float y00 = corner_height(cell + ivec2(0, 0));" in src
	assert "float y10 = corner_height(cell + ivec2(1, 0));" in src
	assert "float y01 = corner_height(cell + ivec2(0, 1));" in src
	assert "vec3 nrm = normalize(cross(e_z, e_x));" in src
	assert "vec3 product_edge_source_cell_normal(" not in src

	# FL-4336 clone render-output-owner parity pass (2026-06-19). Each assertion pins one
	# owner restored to the June-14 clone (ascii_compositor.glsl) so a refactor cannot
	# silently reintroduce an invented divergent owner.
	# FL-4336 Attempt 188: clone static_world_shadow is the baked b34 texture. The
	# invented terrain-relief march stays deleted, while the product owner now consumes
	# the baked texture and applies the clone material-darken curve before final paint.
	assert "float product_terrain_self_shadow(ivec2 cell) {" not in src   # march def gone
	assert "= product_terrain_self_shadow(" not in src                     # no march call
	assert "layout(set = 0, binding = 34) uniform sampler2D static_world_shadow_tex;" in src
	assert "float product_static_world_shadow_for_cell(ivec2 cell)" in src
	assert "void product_apply_static_world_shadow_material_darken(" in src
	assert "float cloud_shadow = max(product_cloud_shadow_for_cell(cell), static_shadow);" in src
	assert "product_apply_static_world_shadow_material_darken(fg, bg, static_shadow);" in src
	assert "float terr_shadow_atten = clamp(shadow_attenuation, 0.0, 1.0);" in src
	# FL-4336 Attempt 181: the current-only cel ramp was a second shade owner before
	# matlib quantization. The clone quantizes product_lighting_value directly.
	assert "float product_cel_ramp_scalar(float value, float bayer_t)" not in src
	assert "float wrapped = clamp((lambert_raw + 0.24) / 1.24, 0.0, 1.0);" not in src
	assert "return clamp(0.16 + sky_fill * 0.34 + lambert_raw * 0.48 + sun_lift * 0.16, 0.0, 1.0);" in src
	assert "float side_contrast = side_face * (lambert_raw - 0.45) * 0.16;" not in src
	# Miss #3: clone day ambient (additive sky/sun fill + cloud guard); invented grey-rock
	# warm-sun mix-to-white lift GONE.
	assert "vec3 sky_fill = vec3(0.48, 0.58, 0.72) * (0.04 + hemisphere * 0.06);" in src
	assert "vec3 warm_sun = vec3(1.0, 0.94, 0.80);" not in src
	# Miss #6: surface-stable dither ported and called on final terrain value.
	assert "void product_apply_surface_stable_dither(" in src
	assert "product_apply_surface_stable_dither(terr_cell, r.family, r.boundary != 0u, product_decal_owned_cell, terrain_product.fg, terrain_product.bg);" in src
	# Miss #7 / M1: contact shadow no longer owns the glyph (both 0x5F returns deleted).
	assert "if (p.role == PRODUCT_TERRAIN_ROLE_CONTACT_SHADOW) return 0x5Fu;" not in src
	# Attempt 172: mountain edge glyph plus edge ink is a product-owned post-resolve fact.
	# The old clone compositor did this after resolve_ansi_cell; Godot must do it before
	# paint storage so ascii_compositor remains a consumer only.
	assert "void product_apply_mountain_edge_owner(" in src
	assert "ProductHit nr = product_terrain_neighbor_hit(cell, ivec2( 1,  0));" in src
	assert "p.glyph = vertical ? 0x3Au : (horizontal ? 0x3Du : 0x23u);" in src
	assert "p.glyph = vertical ? 0x2Fu : (horizontal ? 0x3Bu : 0x23u);" in src
	assert "p.fg = mix(outline_edge, warm_edge, ridge_light * 0.34);" in src
	assert "ProductHit product_terrain_neighbor_hit(ivec2 cell, ivec2 step)" in src
	assert "h.receiver_world = vec3(float(nbr.x) + 0.5, h.depth, float(nbr.y) + 0.5);" in src
	assert "if (ad.x > ad.y * 1.6) return ivec2(d.x >= 0.0 ? 1 : -1, 0);" in src
	assert "return ivec2(d.x >= 0.0 ? 1 : -1, d.y >= 0.0 ? 1 : -1);" in src
	assert "product_apply_mountain_edge_owner(terrain_product, terr_cell, mat, r.family, ansi_xy, world_depth, fb_size);" in src
	assert "r.clone_peak_glyph_lane = (mountain_family && clone_grammar.glyph == 0x5Eu) ? 1u : 0u;" in src
	assert "clone_grammar.glyph_role == 5u" not in src
	assert "uint glyph_role = p.glyph_role & 15u;" in src
	assert "bool product_edge_owned_cell = paint_kind == OWNER_TERRAIN && terrain_glyph_role == 6u;" in src
	assert "if (!product_edge_owned_cell &&" in src
	# Attempt 174: the simplified height/material/elevation edge score is deleted. Edge ink now
	# uses clone-shaped convex filtering, terrain-neighbor facts, normal delta, material
	# delta, and signed sidewall thresholding inside the product owner.
	assert "float product_edge_delta_to_neighbor(" not in src
	assert "bool product_convex_edge_enabled()" in src
	assert "return (params.misc.w & 32) != 0;" in src
	assert "bool product_is_convex_edge(ivec2 cell, ProductHit nbr)" in src
	assert "if (!product_convex_edge_enabled()) return true;" in src
	assert "uint product_native_depth_bucket_u(ivec2 ansi_xy, vec2 fb_size)" not in src
	assert "float mismatch = max(native_delta0, native_delta1) / PRODUCT_DEPTH_BUCKET_MAX_U12;" not in src
	assert "float normal_score = normal_delta * 2.15;" in src
	assert "float mat_score = int(nbr.mat) == mat ? 0.0 : 1.40;" in src
	assert "float signed_height_delta = cell_avg_height(cell) - cell_avg_height(nbr.terr_cell);" in src
	assert "signed_height_delta <= PRODUCT_SIDEWALL_HEIGHT_THRESHOLD" in src
	# Attempt 174: final FL-4102 mountain family enforcement is product-owned and runs after edge
	# ink, matching clone order resolve -> edge -> family enforcement before storage.
	assert "void product_apply_fl4102_mountain_family_enforcement(" in src
	assert "if (g == 0x5Fu) { p.glyph = 0x3Bu; return; }" in src
	assert "if (g == 0xC4u) { p.glyph = 0x3Bu; return; }" in src
	assert "if (g == 0xB3u) { p.glyph = (family == TERRAIN_FAMILY_DIRT) ? 0x27u : 0x3Au; return; }" in src
	assert "product_apply_fl4102_mountain_family_enforcement(terrain_product, terr_cell, r.family);" in src
	# Attempt 175: mountain contrast is now owned inside product_resolve_terrain_cell,
	# with clone glyph_distinctness mode split. The later broad final contrast skips
	# mountain terrain so it cannot wash the resolved mountain product.
	assert "bool product_glyph_distinctness_enabled()" in src
	assert "bool decal_stamp = glyph_role == 8u;" in src
	assert "uint product_clone_material_family_glyph(int family, int elv, uint hash_bits)" in src
	assert "uint product_clone_extended_glyph_for_family(int family, int elv, uint hash_bits)" in src
	assert "bool family_claimed_glyph = false;" in src
	assert "if (glyph_distinctness_on && g.glyph == s00.glyph)" in src
	assert "uint h2 = uint(cell.x) * 2654435761u" in src
	assert "uint yy_g = product_clone_material_family_glyph(fam, elv, h2 >> 4u);" in src
	assert "uint ext_g = product_clone_extended_glyph_for_family(fam, elv, h2 >> 4u);" in src
	assert "const float U09_DECAL_THRESHOLD = 0.6;" in src
	assert "g.glyph_role = 8u;" in src
	assert "if (g.glyph == s00.glyph && !family_claimed_glyph)" in src
	assert "bool product_decal_owned_cell = mountain_family && clone_grammar.has_color != 0u && terrain_product.glyph_role == 8u;" in src
	dither_idx = src.index("product_apply_surface_stable_dither(terr_cell, r.family, r.boundary != 0u, product_decal_owned_cell, terrain_product.fg, terrain_product.bg);")
	edge_idx = src.index("product_apply_mountain_edge_owner(terrain_product, terr_cell, mat, r.family, ansi_xy, world_depth, fb_size);")
	enforce_idx = src.index("product_apply_fl4102_mountain_family_enforcement(terrain_product, terr_cell, r.family);")
	store_idx = src.index("r.paint_fg = terrain_product.fg;")
	assert dither_idx < edge_idx < enforce_idx < store_idx


def test_dump_and_capture_launches_keep_default_visible_products_enabled() -> None:
    src = WORLD.read_text(encoding="utf-8")
    apply_start = src.index("func _apply_default_visible_render_features()")
    apply_end = src.index("\nfunc _manual_controls_action", apply_start)
    body = src[apply_start:apply_end]

    assert 'for feature_name in ["foliage_cards", "water_current_package", "material_ramp_steps", "cloud_shadow_deep"]:' in body
    assert 'text.begins_with("--baseline-feature=") or text.begins_with("--force-baseline-feature=")' in body
    assert 'text.begins_with("--auto-final-render-dump")' not in body
    assert 'text.begins_with("--probe-")' not in body
    assert 'text == "--capture-viewport-exit"' not in body


def test_manual_all_features_locks_foliage_but_keeps_ramp_toggleable() -> None:
    src = WORLD.read_text(encoding="utf-8")
    lock_start = src.index("func _manual_product_feature_locked(")
    lock_end = src.index("\nfunc _apply_default_visible_render_features", lock_start)
    lock_body = src[lock_start:lock_end]
    toggle_start = src.index("func _toggle_manual_baseline_feature(")
    toggle_end = src.index("\nfunc _force_manual_baseline_feature", toggle_start)
    toggle_body = src[toggle_start:toggle_end]
    force_start = src.index("func _force_manual_baseline_feature(")
    force_end = src.index("\nfunc _manual_product_feature_locked", force_start)
    force_body = src[force_start:force_end]

    assert 'OS.get_cmdline_user_args().has("--manual-environment-test-all")' in lock_body
    assert 'feature_name == "foliage_cards"' in lock_body
    assert 'feature_name == "material_ramp_steps"' not in lock_body
    assert "_manual_product_feature_locked(feature_name)" in toggle_body
    assert '_print_manual_environment_state("%s locked_on" % feature_name)' in toggle_body
    assert "_manual_product_feature_locked(feature_name)" in force_body
    assert "enabled = true" in force_body


def test_force_perspective_launch_keeps_manual_toggle_owner() -> None:
    src = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    assert '_has_player_user_flag("--fl4270-force-perspective")' in src
    assert "var _force_perspective: bool = false" in src
    assert "set_force_perspective(true)" in src
    assert "set_camera_view_mode(CAMERA_VIEW_TRUE_PERSPECTIVE if on else CAMERA_VIEW_ORTHO)" in src
    assert "CAMERA_VIEW_ORTHO if _true_perspective_active()" in src
    assert "else CAMERA_VIEW_TRUE_PERSPECTIVE" in src
    assert "_force_perspective_locked" not in src


def test_only_orthographic_and_normal_perspective_modes_remain() -> None:
    src = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    assert "const CAMERA_VIEW_ORTHO: int = 0" in src
    assert "const CAMERA_VIEW_TRUE_PERSPECTIVE: int = 2" in src
    assert "return _true_perspective_active()" in src
    assert "return mode == CAMERA_VIEW_TRUE_PERSPECTIVE" in src
    assert "CAMERA_VIEW_ARCHITECTURAL" not in src
    assert "CAMERA_VIEW_SPIKE_2AM" not in src
    assert "_architectural_perspective_active" not in src
    assert "spike_2am" not in src
    assert "camera.transform = _camera_base_transform" in src
    assert "camera.projection = Camera3D.PROJECTION_ORTHOGONAL" in src
    assert "camera.projection = Camera3D.PROJECTION_PERSPECTIVE" in src


def test_runtime_camera_controls_use_world_keymap_owner() -> None:
    player = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    world = (ROOT / "godot_project/scripts/World.gd").read_text(encoding="utf-8")
    controls = (ROOT / "godot_project/scripts/ControlsRegistry.gd").read_text(encoding="utf-8")

    assert '"id": "perspective",  "key": KEY_P' in controls
    assert '"id": "zoom_out",     "key": KEY_MINUS' in controls
    assert '"id": "render_near",  "key": KEY_BRACKETLEFT' in controls
    assert '_call_player_camera_control("runtime_toggle_perspective")' in world
    assert '_call_player_camera_control("runtime_zoom_out")' in world
    assert "func runtime_toggle_perspective() -> void:" in player
    assert "func runtime_zoom_out() -> void:" in player

    unhandled = player.split("func _unhandled_input(event):", 1)[1].split("func _physics_process(delta):", 1)[0]
    assert "KEY_P" not in unhandled
    assert "KEY_MINUS" not in unhandled
    assert "KEY_BRACKETLEFT" not in unhandled
    assert "KEY_BRACKETRIGHT" not in unhandled


def test_foliage_product_forces_readable_flower_candidate_cells() -> None:
	src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

	required = [
		"const int FOLIAGE_TILE_SIZE      = 8;",
		"struct FoliageCoverage",
		"FoliageCoverage foliage_blade_coverage_at_cell(ivec2 cell)",
		"int foliage_glyph_for_coverage(FoliageCoverage c)",
		"FoliageCardMask foliage_card_mask_sample(FoliageBlade b, vec2 uv)",
		"textureLod(product_foliage_grassleaf_tex, tex_uv, 0.0).a",
		"textureLod(product_foliage_accentleaf_tex, tex_uv, 0.0).a",
		"uint  clump_id;",
		"void compute_product_foliage_fact(ivec2 terr_cell, uint owner_kind, uint mat_id,",
		"cov = foliage_blade_coverage_at_cell(terr_cell);",
		"cov = tree_canopy_foliage_coverage_at_cell(terr_cell, receiver_world, world_depth);",
		"r16_out.x = 1u | (role_bits << 1) | (density_bits << 4) | (shape_bits << 6) | (variant_bits << 9) | (source_bits << 11);",
		"r16_out.y = uint(glyph);",
		"r16_out.z = cov.clump_id;",
		"r16_out.w = floatBitsToUint(cov.wind_sample);",
		"r17_out = vec4(cov.coverage, cov.effective_y, cov.local_u, cov.local_v);",
		"foliage_fg = mix(foliage_fg, vec3(1.0), 0.10);",
		"(paint_kind == OWNER_TERRAIN) ? terr_sub_local_xz : (vec2(terr_cell) + vec2(0.5))",
	]
	for needle in required:
		assert needle in src, needle

	compute_body = src[src.index("void compute_product_foliage_fact("):src.index("\n}\n\n// FL-4231 (restore", src.index("void compute_product_foliage_fact("))]
	mask_body = src[src.index("FoliageCardMask foliage_card_mask_sample("):src.index("\n}\n\nFoliageBlade foliage_blade_from_anchor", src.index("FoliageCardMask foliage_card_mask_sample("))]
	assert "mask.alpha = alpha;" in mask_body
	assert "max(mask.alpha" not in mask_body
	for forbidden in [
		"struct ProductFoliageCell",
		"ProductFoliageCell product_foliage_cell_from_clump",
		"ivec2 product_foliage_clump_anchor_for_cell",
		"uint product_foliage_pack_fact",
		"foliage_hidden_card_alpha",
		"foliage_flower_center_alpha",
		"ProductFoliageCell product_foliage_cell_from_coverage",
		"struct FoliageStampCell",
		"struct FoliageAnchor",
		"struct ProductFoliageStamp",
        "foliage_stamp_product_at_cell",
        "foliage_stamp_bloom_lookup",
        "foliage_anchor_at_tile",
        "foliage_paint_from_anchor",
        "if (grass.role == FOLIAGE_ROLE_FLOWER) return;",
	]:
		assert forbidden not in src, forbidden


def test_weather_rain_is_visible_product_payload() -> None:
    # FL-4333/FL-4338: rain is WORLD-SPACE, resolved per ANSI cell. NO screen overlay
    # (the ansi_xy diagonal "blade" is gone). The falling drop is sampled along the
    # cell's own view ray and occluded by geometry; the ground ripple is the world-
    # anchored consequence; both resolve from world facts, never screen ansi_xy.
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    # No screen-space rain hatch anywhere in the product.
    assert "cell_owner_rain_blade" not in src
    assert "float(ansi_xy.x) + float(ansi_xy.y) * LEAN" not in src

    # World-space falling-drop owner: marches the cell view ray, occluded at surface.
    assert "bool cell_owner_rain_air_drop(ViewRay ray, float surface_depth, float time, float intensity, out uint drop_glyph, out float strength)" in src
    drop_body = src[src.index("bool cell_owner_rain_air_drop("):src.index("\n}\n\n", src.index("bool cell_owner_rain_air_drop("))]
    assert "vec3 P = ray.near_world + ray.dir * tt;" in drop_body          # sampled in world along the ray
    assert "if (tt > reach) break;" in drop_body                          # occluded by surface_depth
    # Drop streak slant is world-down projected to screen (camera consequence).
    assert "uint cell_owner_rain_drop_glyph(vec3 world_p)" in src

    # World-space, ANSI-cell-granular ground ripple keyed on the cell's world hit.
    assert "float cell_owner_rain_ripple(vec2 world_xz, float t, float intensity, out uint ripple_glyph)" in src
    ripple_body = src[src.index("float cell_owner_rain_ripple("):src.index("\n}\n\n", src.index("float cell_owner_rain_ripple("))]
    assert "ivec2 site = ivec2(floor(world_xz / SITE));" in ripple_body  # world-unit sites

    paint_start = src.index("void cell_owner_apply_weather_paint(")
    paint_end = src.index("uint cell_owner_shadow_class", paint_start)
    paint_body = src[paint_start:paint_end]
    # Falling drop (world-space) wins the glyph; ripple is the ground consequence.
    assert "cell_owner_rain_air_drop(ray, surface_depth, t, intensity, drop_glyph, drop_str)" in paint_body
    assert "cell_owner_rain_ripple(surface_world_xz, t, intensity, ripple_glyph)" in paint_body
    assert "glyph = drop_glyph;" in paint_body
    assert "glyph = ripple_glyph;" in paint_body
    # Retired per-cell random glyph lottery (terrain-locked) is gone.
    assert "uint rain_glyphs[4] = uint[4](0xB3u, 0x7Cu, 0x2Fu, 0x5Cu);" not in paint_body
    # FL-4336 Attempt 187: snow must not wash every mountain cell after product paint.
    # Clone snow is altitude-gated accumulation plus sparse flakes below the line.
    assert "fg = mix(fg, vec3(0.92, 0.96, 1.0), mix(0.30, 0.52, intensity));" not in paint_body
    assert "bg = mix(bg, vec3(0.72, 0.80, 0.92), mix(0.18, 0.34, intensity));" not in paint_body
    assert "float snow_line_world = 60.0 - intensity * 54.0;" in paint_body
    assert "float cover_strength = cover_band * intensity * 0.52;" in paint_body
    assert "cover_strength *= smoothstep(0.0, 1.0, cover_band);" in paint_body
    assert "uint snow_glyphs[4] = uint[4](0x2Au, 0x2Bu, 0x2Eu, 0x2Cu);" in paint_body


def test_actor_sprite_keeps_ground_shadow_fact_under_actor_cells() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    shadow_start = src.index("ShadowFact shadow_fact =")
    shadow_end = src.index("float shadow_density = shadow_fact.density;", shadow_start)
    shadow_body = src[shadow_start:shadow_end]
    assert "final_kind == OWNER_SKY" in shadow_body
    assert "actor_shadow_fact_at_world(product_visible_receiver_world)" in shadow_body
    assert "actor_projected_footprint_shadow_fact" not in src
    assert "projected_actor_shadow" not in src
    assert "actor_ground_shadow_for_selected_actor" not in src


def test_water_product_uses_clone_sample_payload_not_reduced_phase_hash() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        "struct ProductWaterSample",
        "struct ProductWakeFact",
        "ProductWaterSample product_water_sample(ivec2 cell, vec2 sample_world_xz, float water_depth)",
        "float product_water_gradient_noise(vec2 p)",
        "float product_water_wave_shape(vec2 uv, float phase)",
        "ProductWakeFact product_wake_fact(vec2 world_xz)",
        "float product_distance_to_segment(vec2 p, vec2 a, vec2 b, out float along)",
        "uint product_water_activity_phase(ProductWaterSample s, float time)",
        "uint product_water_activity_glyph(ProductWaterSample s)",
        "vec3 product_water_activity_ink(vec3 bg, ProductWaterSample s, uint glyph)",
        "bool product_fl4216_break_active(ProductWaterSample s, float time, out int wave_stage)",
        "uint product_pack_water_state(ProductWaterSample s)",
        "if (s.wake_source_id != 0u && s.wake > 0.18) state |= 1u << 12u;",
        'state |= (s.wake_source_id & 0xFFu) << 16u;',
        "uint water_glyph_candidate(ProductWaterSample s)",
        "vec3 water_paint_fg(ProductWaterSample s)",
        "vec3 water_paint_bg(ProductWaterSample s)",
        "ProductWaterSample water_sample = product_water_sample(terr_cell, water_sample_world_xz, water_depth);",
        "? water_glyph_candidate(water_sample)",
        "if (owner_kind == OWNER_WATER) return water_paint_fg(water_sample);",
        "if (owner_kind == OWNER_WATER) return water_paint_bg(water_sample);",
        "c.r3 = uvec4(0u, 0u, world_is_water ? product_pack_water_state(water_sample) : 0u, 0u);",
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        "uint water_phase_index_3(",
        "uint body_r = water_phase_index_3(",
        "float product_fl4216_wave_break(",
        "ProductWakeFact product_wake_fact(vec2 world_xz, float time)",
        "float product_wake_strength(vec2 world_xz, float time)",
        "ring_center = radius + age * ripple_rate",
        "ripple_gate = 0.65 + 0.35 * sin",
        "if (brk > 0.45)",
    ]
    for needle in forbidden:
        assert needle not in src, needle


def test_shoreline_break_is_water_product_payload() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    glyph_start = src.index("uint water_glyph_candidate(ProductWaterSample s)")
    glyph_end = src.index("\n}\n\nuint terrain_material_glyph_candidate", glyph_start)
    glyph_body = src[glyph_start:glyph_end]
    assert "if (s.break_active && s.break_dir >= 0 && s.break_stage >= 0)" in glyph_body
    assert "return uint(FL4216_GLYPH_ID[s.break_dir * 4 + s.break_stage]);" in glyph_body
    assert "return product_water_activity_glyph(s, 0.0);" in glyph_body

    fg_start = src.index("vec3 water_paint_fg(ProductWaterSample s)")
    fg_end = src.index("\n}\n\nvec3 water_paint_bg", fg_start)
    fg_body = src[fg_start:fg_end]
    assert "if (s.break_active)" in fg_body
    assert "vec3 fg = product_water_activity_ink(s.bg, s, glyph);" in fg_body
    assert "return fg;" in fg_body

    bg_start = src.index("vec3 water_paint_bg(ProductWaterSample s)")
    bg_end = src.index("\n}\n\nvec3 cell_owner_paint_fg", bg_start)
    bg_body = src[bg_start:bg_end]
    assert "if (s.break_active)" in bg_body
    assert "break_wash" in bg_body


def test_blood_dump_joins_radius_owner_and_final_ink() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect = EFFECT.read_text(encoding="utf-8")

    assert "float radius;" in product
    assert "fact.radius = radius;" in product
    assert "c.r12 = uvec4(blood_mark_id, floatBitsToUint(blood_radius), blood_fact.source_event_id, final_kind);" in product
    assert "source_event_id = uint(max(slot1.z, 0.0) + 0.5);" in product
    assert "fact.age = max(slot1.w, 0.0);" in product
    assert "source_event_id = uint(max(slot1.x, 0.0) + 0.5);" not in product
    assert "if (slot0.w <= 0.0 || source_event_id == 0u) continue;" in product
    assert "float blood_radius = blood_fact.radius;" in product

    assert "co_blood_radius = cell_product_data.decode_float(co + 196)" in effect
    assert "co_blood_source_event_id = int(cell_product_data.decode_u32(co + 200))" in effect
    assert "co_blood_selected_base_owner = _cell_owner_kind_name(int(cell_product_data.decode_u32(co + 204)))" in effect
    assert '"cell_owner_blood_radius": co_blood_radius' in effect
    assert '"cell_owner_blood_source_event_id": co_blood_source_event_id' in effect
    assert '"cell_owner_blood_selected_base_owner": co_blood_selected_base_owner' in effect
    assert 'var cell_owner_blood_radius: Variant = fact_row.get("cell_owner_blood_radius", null) if fact_ok else null' in effect
    assert '"blood_radius_field": "cell_owner_blood_radius"' in effect
    assert '"selected_owner_field": "cell_owner_kind"' in effect
    assert '"final_glyph_ink_field": "final_glyph_ink"' in effect
    assert '"cell_owner_blood_radius": cell_owner_blood_radius' in effect


def test_dump_decodes_cell_owner_product_extended_fields() -> None:
    src = EFFECT.read_text(encoding="utf-8")

    required = [
        "const CELL_OWNER_RECORD_BYTES := 368",  # FL-4359: +r22 probe evidence diagnostic row
        "co_shadow_class = int(cell_product_data.decode_u32(co + 96))",
        "co_blood_visible = int(cell_product_data.decode_u32(co + 100))",
        "co_foliage_class = int(cell_product_data.decode_u32(co + 104))",
        "co_lighting_class = int(cell_product_data.decode_u32(co + 108))",
        "co_shadow_density = cell_product_data.decode_float(co + 112)",
        "co_shadow_attenuation = cell_product_data.decode_float(co + 116)",
        "co_foliage_coverage = cell_product_data.decode_float(co + 120)",
        "co_foliage_density_class = int(cell_product_data.decode_float(co + 124))",
        "co_water_state = int(cell_product_data.decode_u32(co + 56))",
        "co_paint_flags = int(cell_product_data.decode_u32(co + 60))",
        "co_waterline_clip = (co_paint_flags & 1) != 0",
        "co_paint_fg = [",
        "cell_product_data.decode_float(co + 128),",
        "co_paint_bg = [",
        "cell_product_data.decode_float(co + 144),",
        "co_shadow_caster_id = int(cell_product_data.decode_u32(co + 160))",
        "co_cloud_shadow_density = float(cell_product_data.decode_u32(co + 164)) / 65535.0",
        "co_terrain_detail_packed = int(cell_product_data.decode_u32(co + 168))",
        "co_terrain_slope_class = int(co_terrain_detail_packed) & 3",
        "co_terrain_ridge_fact = (int(co_terrain_detail_packed) >> 2) & 1",
        "co_terrain_cliff_fact = (int(co_terrain_detail_packed) >> 3) & 1",
        "co_terrain_contact_shadow_fact = (int(co_terrain_detail_packed) >> 4) & 1",
        "co_terrain_ramp_band = (int(co_terrain_detail_packed) >> 5) & 7",
        "co_terrain_snow_fact = (int(co_terrain_detail_packed) >> 8) & 1",
        "co_terrain_glyph_role = (int(co_terrain_detail_packed) >> 9) & 15",
        "co_terrain_depth_class = (int(co_terrain_detail_packed) >> 13) & 7",
        "co_terrain_material_tone = (int(co_terrain_detail_packed) >> 16) & 15",
        "co_terrain_clone_peak_glyph_lane = (int(co_terrain_detail_packed) >> 20) & 1",
        "co_terrain_convex_edge = (int(co_terrain_detail_packed) >> 21) & 1",
        "co_terrain_concave_edge = (int(co_terrain_detail_packed) >> 22) & 1",
        "co_terrain_heightfield_neighbor_state = (int(co_terrain_detail_packed) >> 23) & 15",
        "co_terrain_sidewall_score_class = (int(co_terrain_detail_packed) >> 27) & 15",
        "co_terrain_fg_luma = clampf(float(co_paint_fg[0]) * 0.2126",
        "co_terrain_bg_luma = clampf(float(co_paint_bg[0]) * 0.2126",
        "co_terrain_fg_bg_contrast = abs(float(co_terrain_fg_luma) - float(co_terrain_bg_luma))",
        "co_terrain_depth_key_fact = cell_product_data.decode_float(co + 172)",
        "co_shadow_caster_xz = [",
        "cell_product_data.decode_float(co + 176),",
        "co_shadow_luma_before = cell_product_data.decode_float(co + 184)",
        "co_shadow_luma_after = cell_product_data.decode_float(co + 188)",
        "co_blood_mark_id = int(cell_product_data.decode_u32(co + 192))",
        "co_blood_mark_xz = [",
        "cell_product_data.decode_float(co + 208),",
        "co_blood_intensity = cell_product_data.decode_float(co + 216)",
        "co_actor_candidate_world_depth = cell_product_data.decode_float(co + 220)",
        "co_terrain_elevation_class = int(cell_product_data.decode_u32(co + 224))",
        "co_terrain_material_boundary = int(cell_product_data.decode_u32(co + 228))",
        "co_actor_presentation_id = int(cell_product_data.decode_u32(co + 232))",
        "co_actor_candidate_reject_bits = int(cell_product_data.decode_u32(co + 236))",
        "co_actor_waterline_clip_threshold = cell_product_data.decode_float(co + 240)",
        "co_actor_candidate_depth = cell_product_data.decode_float(co + 252)",
        "var probe_evidence_winner_raw := int(data.decode_u32(co + 352))",
        "var probe_depth_test_result := int(data.decode_u32(co + 356)) != 0",
        "var probe_candidate_fallback_cell := int(data.decode_u32(co + 360)) != 0",
        "var probe_texel_id := probe_evidence_winner_raw if probe_depth_test_result",
        "var probe_candidate_fallback_winner: Variant = probe_evidence_winner_raw if probe_candidate_fallback_cell",
        '"probe_texel_id": probe_texel_id',
        '"probe_candidate_fallback_winner": probe_candidate_fallback_winner',
        '"probe_depth_test_result": probe_depth_test_result',
        '"probe_candidate_fallback_cell": probe_candidate_fallback_cell',
        '"cell_owner_shadow_class": co_shadow_class',
        '"cell_owner_blood_visible": co_blood_visible',
        '"cell_owner_foliage_class": co_foliage_class',
        '"cell_owner_lighting_class": co_lighting_class',
        '"cell_owner_shadow_density": co_shadow_density',
        '"cell_owner_shadow_attenuation": co_shadow_attenuation',
        '"cell_owner_shadow_caster_id": co_shadow_caster_id',
        '"cell_owner_cloud_shadow_density": co_cloud_shadow_density',
        '"cell_owner_terrain_detail_packed": co_terrain_detail_packed',
        '"cell_owner_terrain_slope_class": co_terrain_slope_class',
        '"cell_owner_terrain_ridge_fact": co_terrain_ridge_fact',
        '"cell_owner_terrain_cliff_fact": co_terrain_cliff_fact',
        '"cell_owner_terrain_contact_shadow_fact": co_terrain_contact_shadow_fact',
        '"cell_owner_terrain_ramp_band": co_terrain_ramp_band',
        '"cell_owner_terrain_snow_fact": co_terrain_snow_fact',
        '"cell_owner_terrain_glyph_role": co_terrain_glyph_role',
        '"cell_owner_terrain_depth_class": co_terrain_depth_class',
        '"cell_owner_terrain_material_tone": co_terrain_material_tone',
        '"cell_owner_terrain_clone_peak_glyph_lane": co_terrain_clone_peak_glyph_lane',
        '"cell_owner_terrain_convex_edge": co_terrain_convex_edge',
        '"cell_owner_terrain_concave_edge": co_terrain_concave_edge',
        '"cell_owner_terrain_heightfield_neighbor_state": co_terrain_heightfield_neighbor_state',
        '"cell_owner_terrain_sidewall_score_class": co_terrain_sidewall_score_class',
        '"cell_owner_terrain_fg_luma": co_terrain_fg_luma',
        '"cell_owner_terrain_bg_luma": co_terrain_bg_luma',
        '"cell_owner_terrain_fg_bg_contrast": co_terrain_fg_bg_contrast',
        '"cell_owner_terrain_depth_key_fact": co_terrain_depth_key_fact',
        '"cell_owner_shadow_caster_xz": co_shadow_caster_xz',
        '"cell_owner_shadow_luma_before": co_shadow_luma_before',
        '"cell_owner_shadow_luma_after": co_shadow_luma_after',
        '"cell_owner_blood_mark_id": co_blood_mark_id',
        '"cell_owner_blood_mark_xz": co_blood_mark_xz',
        '"cell_owner_blood_intensity": co_blood_intensity',
        '"cell_owner_actor_candidate_world_depth": co_actor_candidate_world_depth',
        '"cell_owner_terrain_elevation_class": co_terrain_elevation_class',
        '"cell_owner_terrain_material_boundary": co_terrain_material_boundary',
        '"cell_owner_actor_presentation_id": co_actor_presentation_id',
        '"cell_owner_actor_candidate_reject_bits": co_actor_candidate_reject_bits',
        '"cell_owner_actor_waterline_clip_threshold": co_actor_waterline_clip_threshold',
        '"cell_owner_actor_candidate_depth": co_actor_candidate_depth',
        '"cell_owner_foliage_coverage": co_foliage_coverage',
        '"cell_owner_foliage_density_class": co_foliage_density_class',
        '"cell_owner_foliage_pa_species": co_foliage_species',
        '"cell_owner_water_state": co_water_state',
        '"cell_owner_paint_flags": co_paint_flags',
        '"cell_owner_waterline_clip": co_waterline_clip',
        '"cell_owner_paint_fg_rgb": co_paint_fg',
        '"cell_owner_paint_bg_rgb": co_paint_bg',
        'var cell_owner_shadow_caster_id: Variant = fact_row.get("cell_owner_shadow_caster_id", null) if fact_ok else null',
        'var cell_owner_shadow_caster_xz: Variant = fact_row.get("cell_owner_shadow_caster_xz", null) if fact_ok else null',
        'var cell_owner_shadow_luma_before: Variant = fact_row.get("cell_owner_shadow_luma_before", null) if fact_ok else null',
        'var cell_owner_shadow_luma_after: Variant = fact_row.get("cell_owner_shadow_luma_after", null) if fact_ok else null',
        'var cell_owner_blood_mark_id: Variant = fact_row.get("cell_owner_blood_mark_id", null) if fact_ok else null',
        'var cell_owner_blood_mark_xz: Variant = fact_row.get("cell_owner_blood_mark_xz", null) if fact_ok else null',
        'var cell_owner_blood_intensity: Variant = fact_row.get("cell_owner_blood_intensity", null) if fact_ok else null',
        'var cell_owner_actor_candidate_world_depth: Variant = fact_row.get("cell_owner_actor_candidate_world_depth", null) if fact_ok else null',
        'var cell_owner_terrain_elevation_class: Variant = fact_row.get("cell_owner_terrain_elevation_class", null) if fact_ok else null',
        'var cell_owner_terrain_material_boundary: Variant = fact_row.get("cell_owner_terrain_material_boundary", null) if fact_ok else null',
        'var cell_owner_terrain_material_tone: Variant = fact_row.get("cell_owner_terrain_material_tone", null) if fact_ok else null',
        'var cell_owner_terrain_clone_peak_glyph_lane: Variant = fact_row.get("cell_owner_terrain_clone_peak_glyph_lane", null) if fact_ok else null',
        'var cell_owner_terrain_fg_bg_contrast: Variant = fact_row.get("cell_owner_terrain_fg_bg_contrast", null) if fact_ok else null',
        'var cell_owner_actor_candidate_reject_bits: Variant = fact_row.get("cell_owner_actor_candidate_reject_bits", null) if fact_ok else null',
        'var cell_owner_actor_candidate_depth: Variant = fact_row.get("cell_owner_actor_candidate_depth", null) if fact_ok else null',
        '"cell_owner_shadow_caster_id": cell_owner_shadow_caster_id',
        '"cell_owner_shadow_caster_xz": cell_owner_shadow_caster_xz',
        '"cell_owner_shadow_luma_before": cell_owner_shadow_luma_before',
        '"cell_owner_shadow_luma_after": cell_owner_shadow_luma_after',
        '"cell_owner_blood_mark_id": cell_owner_blood_mark_id',
        '"cell_owner_blood_mark_xz": cell_owner_blood_mark_xz',
        '"cell_owner_blood_intensity": cell_owner_blood_intensity',
        '"cell_owner_actor_candidate_world_depth": cell_owner_actor_candidate_world_depth',
        '"cell_owner_terrain_elevation_class": cell_owner_terrain_elevation_class',
        '"cell_owner_terrain_material_boundary": cell_owner_terrain_material_boundary',
        '"cell_owner_terrain_material_tone": cell_owner_terrain_material_tone',
        '"cell_owner_terrain_clone_peak_glyph_lane": cell_owner_terrain_clone_peak_glyph_lane',
        '"cell_owner_terrain_fg_bg_contrast": cell_owner_terrain_fg_bg_contrast',
        '"cell_owner_actor_candidate_reject_bits": cell_owner_actor_candidate_reject_bits',
        '"actor_candidate_reject_bits": cell_owner_actor_candidate_reject_bits',
        '"cell_owner_actor_candidate_depth": cell_owner_actor_candidate_depth',
        '"actor_candidate_depth": cell_owner_actor_candidate_depth',
        '"actor_candidate_world_depth": cell_owner_actor_candidate_world_depth',
        '"blood_mark_field": "cell_owner_blood_mark_id"',
        '"blood_intensity_field": "cell_owner_blood_intensity"',
        '"shadow_caster_field": "cell_owner_shadow_caster_id"',
        '"shadow_luma_fields": ["cell_owner_shadow_luma_before", "cell_owner_shadow_luma_after"]',
        '"visible_luminance_delta_source": "cell_owner_product_ssbo"',
        "cell_owner_shadow_luma_before != null and cell_owner_shadow_luma_after != null",
        '"terrain_detail_fields": ["cell_owner_terrain_elevation_class", "cell_owner_terrain_material_boundary", "cell_owner_terrain_slope_class", "cell_owner_terrain_ridge_fact", "cell_owner_terrain_cliff_fact", "cell_owner_terrain_contact_shadow_fact", "cell_owner_terrain_ramp_band", "cell_owner_terrain_snow_fact", "cell_owner_terrain_glyph_role", "cell_owner_terrain_depth_class", "cell_owner_terrain_material_tone", "cell_owner_terrain_clone_peak_glyph_lane", "cell_owner_terrain_fg_luma", "cell_owner_terrain_bg_luma", "cell_owner_terrain_fg_bg_contrast", "cell_owner_terrain_convex_edge", "cell_owner_terrain_concave_edge", "cell_owner_terrain_heightfield_neighbor_state", "cell_owner_terrain_sidewall_score_class"]',
        "var selected_cell_product_payload := {",
        '"source": "cell_owner_product_ssbo_after_selection"',
        '"selected_cell_product_payload": selected_cell_product_payload',
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        '"debug_luma_estimate_source": "gpu_fact_foliage2_actor_shadow_readback_plus_final_luma_estimate"',
        "var shadow_actor_density: Variant = fact_row.get(\"actor_shadow_density\", null) if fact_ok else null",
        "shadow_contribution_before_luma = clampf(luma / float(shadow_actor_attenuation), 0.0, 1.0)",
    ]
    for needle in forbidden:
        assert needle not in src, needle

    effect_src = EFFECT.read_text(encoding="utf-8")
    effect_required = [
        "func _actor_presentation_kind_id(kind: String) -> int:",
        '"attack": return 2',
        # FL-4231 blocker-1/4: dump enum mirror names typed effect products so a
        # projectile/vapor product is observable in the dump, not masked as "none".
        '8: return "projectile"',
        '10: return "vapor"',
        '12: return "reticle"',
        "var presentation_id := _actor_presentation_kind_id(String(row.get(\"presentation_kind\", \"\")))",
        "((clampi(presentation_id, 0, 0xFF) & 0xFF) << 24)",
    ]
    for needle in effect_required:
        assert needle in effect_src, needle


def test_final_painter_accepts_cell_product_extended_glyph_ids() -> None:
    src = COMPOSITOR.read_text(encoding="utf-8")

    required = [
        "r.glyph = clamp(int(owner.r2.x), 0, 671);",
        "if (glyph >= 512 && glyph <= 671)",
        "return texelFetch(extended_glyph_tex, ext_px, 0).r;",
        # FL-4231 blocker-1/2: compositor typed taxonomy stays in lockstep with the
        # producer; typed effect products get selected-payload paint cases.
        "const uint CO_OWNER_PROJECTILE   = 8u;",
        "const uint CO_OWNER_VAPOR        = 10u;",
        "const uint CO_OWNER_RETICLE      = 12u;",
        "case CO_OWNER_PROJECTILE: {",
        "case CO_OWNER_VAPOR: {",
        "case CO_OWNER_RETICLE: {",
        "|| owner.r0.x == CO_OWNER_RETICLE;",
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        "r.glyph = clamp(int(owner.r2.x), 0, 255);",
        "AnsiResolve sky_resolve(",
        "return sky_resolve(",
    ]
    for needle in forbidden:
        assert needle not in src, needle


def test_secondary_effect_product_block_schema_and_fact_only_consume() -> None:
    # FL-4231 step-B: base-plus-secondary effect product facts (r18-r21). The BASE
    # product (r0-r17) is unchanged; a typed SECONDARY effect overlay candidate is
    # carried as product facts and composited by final paint from facts ONLY.
    product_src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor_src = COMPOSITOR.read_text(encoding="utf-8")
    effect_src = EFFECT.read_text(encoding="utf-8")

    # Producer: blend-role constants + r18-r21 schema + fail-closed init.
    product_required = [
        "const uint SEC_BLEND_NONE     = 0u;",
        "const uint SEC_BLEND_ADDITIVE = 1u;",
        "const uint SEC_BLEND_ALPHA    = 2u;",
        "const uint SEC_BLEND_GLYPH    = 3u;",
        "uvec4 r18; // x=sec_kind(OWNER_* effect class), y=sec_source_id, z=sec_reject_bits, w=sec_glyph",
        "vec4  r19; // x=sec_depth, y=sec_coverage[0,1], z=sec_alpha[0,1], w=float(sec_blend_role)",
        "vec4  r20; // xyz=sec_fg.rgb (effect color), w=sec_intensity (additive scale)",
        "vec4  r21; // xyz=sec_world.xyz (effect anchor, proof), w=reserved",
        # empty_record() must zero the new block AND close the latent r16/r17 gap so
        # no path emits an unwritten/garbage secondary overlay (Law 6 fail-closed).
        "c.r16 = uvec4(0u);  // FL-4231: complete the initializer (r16/r17 were latently undefined)",
        "c.r18 = uvec4(0u);",
        "c.r19 = vec4(0.0);",
        "c.r20 = vec4(0.0);",
        "c.r21 = vec4(0.0);",
    ]
    for needle in product_required:
        assert needle in product_src, needle

    # Compositor: mirror constants + r18-r21 schema + fact-only composite hook.
    compositor_required = [
        "const uint CO_SEC_BLEND_NONE     = 0u;",
        "const uint CO_SEC_BLEND_ADDITIVE = 1u;",
        "const uint CO_SEC_BLEND_ALPHA    = 2u;",
        "const uint CO_SEC_BLEND_GLYPH    = 3u;",
        "uvec4 r18; // x=sec_kind, y=sec_source_id, z=sec_reject_bits, w=sec_glyph",
        "vec4  r21; // xyz=sec_world.xyz, w=reserved",
        "void composite_secondary_effect(ivec2 pos, ivec2 ansi_xy, CellOwnerRecord owner,",
        "uint sec_kind  = owner.r18.x;",
        "uint sec_blend = uint(owner.r19.w + 0.5);",
        "if (sec_kind == CO_OWNER_NONE || sec_blend == CO_SEC_BLEND_NONE) return;",
        "color += mask * sec_alpha * sec_intens * sec_fg;",
        "color = mix(color, sec_fg, mask * sec_alpha);",
        # the composite must be CALLED in main() after the base switch, before store.
        "composite_secondary_effect(pos, ansi_xy, owner, glyph_w_px, glyph_h_px, color);",
        "imageStore(color_image, pos, vec4(color, 1.0));",
    ]
    for needle in compositor_required:
        assert needle in compositor_src, needle

    # The composite must read ONLY product facts — never discover/rank/sample hidden
    # effect buffers. Assert it does not reach for any non-product effect source.
    composite_start = compositor_src.index("void composite_secondary_effect(")
    composite_body = compositor_src[composite_start:compositor_src.index("void main()", composite_start)]
    for offender in ["imageLoad(", "texelFetch(", "local_light", "firefly", "beam_", "wake_trail", "dynamic_mover"]:
        assert offender not in composite_body, offender

    # GDScript dump: both decode paths expose the secondary facts as proof fields.
    effect_required = [
        "co_sec_kind = _cell_owner_kind_name(int(cell_product_data.decode_u32(co + 288)))",
        "co_sec_blend_role = int(cell_product_data.decode_float(co + 316))",
        '"cell_owner_sec_kind": co_sec_kind,',
        '"cell_owner_sec_coverage": co_sec_coverage,',
        '"cell_owner_sec_blend_role": co_sec_blend_role,',
        '"cell_owner_sec_world": co_sec_world,',
        # second inline dump dict path
        '"cell_owner_sec_kind": _cell_owner_kind_name(int(cell_product_data.decode_u32(co + 288))),',
        '"cell_owner_sec_blend_role": int(cell_product_data.decode_float(co + 316)),',
    ]
    for needle in effect_required:
        assert needle in effect_src, needle


def test_local_light_migrated_to_secondary_product_fact_old_owner_deleted() -> None:
    # FL-4231 C1+C2: LocalLight (lighthouse beam cone kind==4 + point/bonfire volume
    # glow + FL-4204 firefly/bonfire glyph) must be a TYPED SECONDARY PRODUCT FACT
    # (r18-r21) owned by the producer, NOT baked into base paint. The final compositor
    # applies it ONLY through composite_secondary_effect(). The old paint-bake owners
    # apply_local_light_volume / apply_local_light_glyph_overlay must be DELETED (Law 5).
    product_src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    required = [
        # new single owner of the secondary local-light fact
        "void compute_local_light_secondary_fact(",
        "ViewRay ray, vec3 receiver_world, float world_depth, ivec2 ansi_xy, vec2 fb_size,",
        "out uvec4 r18, out vec4 r19, out vec4 r20, out vec4 r21) {",
        # C2 firefly glyph claim -> GLYPH blend, OWNER_PARTICLE, carries source/depth/cov/alpha/world
        "r18 = uvec4(OWNER_PARTICLE, uint(best_src + 1), 0u, best_glyph);",
        "r19 = vec4(world_depth, best_cov, clamp(best_alpha, 0.0, 1.0), float(SEC_BLEND_GLYPH));",
        # C1 beam cone + volume glow -> ADDITIVE blend, OWNER_BEAM for cone else OWNER_PARTICLE
        "uint dominant_kind = (dom_kind == 4) ? OWNER_BEAM : OWNER_PARTICLE;",
        "float sec_alpha = clamp(dom_kind == 4 ? max(dom_coverage * 0.34, dom_contrib * 0.38) : 1.0, 0.0, 0.85);",
        "float sec_coverage = clamp(dom_kind == 4 ? max(dom_coverage, dom_contrib / max(dom_intensity, 1e-3)) : 1.0, 0.0, 1.0);",
        "r19 = vec4(world_depth, sec_coverage, sec_alpha, float(SEC_BLEND_ADDITIVE));",
        "r21 = vec4(dom_world, 0.0);",
        # producer still reuses the accumulation math (not deleted — it feeds the fact)
        # depth_key uses receiver_world.y (world Y) to match occ_key scale (anchor.y+0.5).
        "bool sky_base = final_kind == OWNER_SKY;",
        "if (sky_base && !local_light_has_beam_record()) return;",
        "LightAccum accum = local_light_accumulation(receiver_world, vec3(0.0, 1.0, 0.0), receiver_world.y, false);",
        "if (sky_base && kind != 4) continue;",
        "if (!sky_base && kind == 4) continue;",
        # the cell paint site writes the secondary fact into the record, not into base paint
        "compute_local_light_secondary_fact(ray, product_visible_receiver_world, world_depth, ansi_xy, fb_size,",
        "final_kind, c.r18, c.r19, c.r20, c.r21);",
        # fail-closed: no active C1 volume glow -> no secondary fact. FL-4360
        # deletes the C0 beam splat owner; kind=4 local-light owns the beam.
        "bool records_enabled = local_light_records_enabled();",
        "if (records_enabled && final_kind != OWNER_ACTOR_SPRITE && final_kind != OWNER_SKY) {",
        "LighthouseCone cone = lighthouse_cone_from_record(i);",
        "float lighthouse_cone_ray_contribution(ViewRay ray, LighthouseCone cone, float intensity, out vec3 beam_world, out float beam_coverage)",
        "float ray_contrib = lighthouse_cone_ray_contribution(ray, cone, local_light_intensity(i), beam_world, beam_cov);",
        "contrib = ray_contrib;",
        "if (dom_kind == 4 && dom_src >= 0 && dom_contrib > 0.0)",
        "if (!records_enabled) return;",
        "if (rec709_luminance(add_fg) < 0.001) return; // fail-closed: no glow -> no fact",
    ]
    for needle in required:
        assert needle in product_src, needle

    forbidden = [
        # the old base-paint-bake owners must be gone (definitions AND call sites)
        "void apply_local_light_volume(",
        "void apply_local_light_glyph_overlay(",
        "apply_local_light_volume(paint_fg, paint_bg, la);",
        "apply_local_light_glyph_overlay(glyph_candidate, paint_fg, paint_bg, ansi_xy, fb_size);",
        "uint beam_key",
        "if (final_kind == OWNER_SKY) return;",
        "r18 = uvec4(OWNER_BEAM, 0u, 0u, 0u);",
        "float lighthouse_cone_point_contribution",
        "contrib = lighthouse_cone_point_contribution",
    ]
    for needle in forbidden:
        assert needle not in product_src, needle


def test_dynamic_mesh_uses_dedicated_buffer_not_static_split() -> None:
    product_src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect_src = EFFECT.read_text(encoding="utf-8")
    world_src = WORLD.read_text(encoding="utf-8")
    compositor_src = COMPOSITOR.read_text(encoding="utf-8")

    required = [
        "bool source_is_dynamic_mover(uint winner)",
        # FL-4231: the dedicated dynamic-mover buffer path survives the typed-product
        # refactor; movers are typed from the face airframe_id, and untyped movers
        # still resolve to OWNER_DYNAMIC_MESH through the helper (dedicated buffer,
        # NOT a static-buffer split).
        "h.kind = dynamic_mover_typed_kind(uint(fr2.w + 0.5));",
        "return OWNER_DYNAMIC_MESH;",
        "var dynamic_source_facts: Array = []",
        'bird_faces = int(_bird_flock.call("append_dynamic_source_facts", dynamic_source_facts))',
        "var vapor_faces := _append_airplane_vapor_dynamic_source_facts(dynamic_source_facts)",
        "var dynamic_face_count := bird_faces + airplane_faces + projectile_faces + reticle_faces + vapor_faces",
        "func set_dynamic_mover_source_facts(source_facts: Array, expected_face_count: int = 0) -> void:",
        "func _build_dynamic_mover_faces_for_pixel_context(ctx: PixelCameraContext) -> Dictionary:",
        "func _publish_dynamic_mover_source_facts_for_pixel_context(ctx: PixelCameraContext) -> void:",
        "_dispatch_cell_owner_product(presentation_size, push, pixel_ctx)",
        "dynamic mesh ownership has its own SSBO",
        "const AIRPLANE_MAT_VAPOR",
        "const VAPOR_DYNAMIC_AIRFRAME_ID",
    ]
    for needle in required:
        assert needle in product_src or needle in effect_src or needle in world_src, needle

    forbidden = [
        "set_static_world_mesh_dynamic_split",
        "_static_world_mesh_static_face_count",
        "static_buffer_dynamic_faces",
        "push_airplane_vapor_sample",
        "VAPOR_TRAIL_CAPACITY",
        "_vapor_trail_buffer_rd",
        "u_vapor_trail",
        "binding = 41",
        "_append_lighthouse_cone_faces",
        "cone_faces",
        "airplane_vapor_trail",
        "apply_airplane_vapor_trail(",
        "prepass_push",
        "int static_face_count = params.cell_dims.w;",
        "? OWNER_DYNAMIC_MESH : OWNER_STATIC_MESH",
        "_bird_flock.call(\"append_world_mesh_faces\", combined)",
        "_append_lighthouse_cone_faces(combined)",
        "var dynamic_rows := PackedFloat32Array()",
        "set_dynamic_mover_mesh_faces",
        "_append_airplane_world_mesh_faces",
        "_append_one_airplane_faces",
    ]
    joined = "\n".join([product_src, effect_src, world_src, compositor_src])
    for needle in forbidden:
        assert needle not in joined, needle
