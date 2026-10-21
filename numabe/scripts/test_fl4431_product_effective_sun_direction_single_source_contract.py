#!/usr/bin/env python3
"""FL-4431 Product effective-light-direction single-source contract.

Each admitted Product subcell selects and outer-normalizes the semantic
sun/moon direction exactly once before the payload compiler. Terrain, mesh,
cloud, heightfield, and cast-shadow consumers receive that immutable value.
The test is destructive structural evidence; it does not measure GPU time,
inspect rendered pixels, nor establish the 60 FPS runtime target.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def function_span(source: str, name: str) -> tuple[int, int]:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        source,
        re.S,
    )
    assert match is not None, f"missing function definition: {name}"
    brace = source.find("{", match.start())
    depth = 0
    for position in range(brace, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), position + 1
    raise AssertionError(f"unterminated function: {name}")


def function_source(source: str, name: str) -> str:
    start, end = function_span(source, name)
    return source[start:end]


def function_body(source: str, name: str) -> str:
    function = function_source(source, name)
    return function[function.find("{") + 1 : function.rfind("}")]


def declared_function_names(source: str, pattern: str) -> list[str]:
    return re.findall(
        rf"(?m)^\s*(?:precise\s+)?(?:void|float|vec2|vec3|vec4)\s+"
        rf"({pattern})\s*\(",
        source,
    )


def replace_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
) -> str:
    start, end = function_span(source, name)
    function = source[start:end]
    count = function.count(old)
    assert count == 1, (
        f"{name}: mutation target count={count} for {old!r}"
    )
    return source[:start] + function.replace(old, new, 1) + source[end:]


def replace_nth_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
    occurrence: int,
) -> str:
    start, end = function_span(source, name)
    function = source[start:end]
    positions = [
        match.start() for match in re.finditer(re.escape(old), function)
    ]
    assert len(positions) > occurrence, (
        f"{name}: mutation occurrence={occurrence} unavailable for {old!r}; "
        f"count={len(positions)}"
    )
    position = positions[occurrence]
    changed = function[:position] + new + function[position + len(old) :]
    return source[:start] + changed + source[end:]


def assert_effective_direction_single_source(source: str) -> None:
    whole = compact(source)
    main = compact(function_body(source, "main"))
    selector = compact(
        function_body(source, "q3_probe_payload_effective_sun_dir")
    )
    moon = compact(function_body(source, "product_moon_dir"))
    terrain_shade = compact(
        function_body(source, "q3_probe_payload_compile_terrain_shade")
    )
    static_shadow = compact(
        function_body(source, "q3_probe_payload_static_shadow_density")
    )
    cast_shadow = compact(
        function_body(source, "q3_probe_payload_cast_shadow_attenuation")
    )
    heightfield = compact(
        function_body(
            source, "q3_probe_payload_heightfield_shadow_attenuation"
        )
    )
    # FL-4381/FL-4737 R1 (2026-08-13): the up-sun walk to the cloud plane was
    # extracted out of the two wrappers into ONE shared world-space helper, so
    # the helper -- not the wrappers -- is now the reader of the carried
    # direction for both cloud registers.
    cloud_projected_world = compact(
        function_body(source, "product_cloud_shadow_projected_world")
    )
    daylight_godray = compact(
        function_body(source, "product_daylight_godray_veil")
    )
    finalizer = compact(
        function_body(source, "vsb_product_finalize_record")
    )
    cloud_projected = compact(
        function_body(
            source, "q3_probe_payload_live_cloud_shadow_projected_at"
        )
    )
    cloud_filter = compact(
        function_body(
            source, "q3_probe_payload_cutover_cloud_shadow_for_cell"
        )
    )
    ortho_projected = compact(
        function_body(
            source, "q3_probe_payload_ortho_cloud_density_projected_at"
        )
    )
    ortho_filter = compact(
        function_body(
            source, "q3_probe_payload_ortho_cloud_shadow_for_cell"
        )
    )
    material = compact(
        function_body(source, "q3_probe_payload_apply_material")
    )
    static_rock_moss = compact(
        function_body(
            source, "q3_probe_payload_apply_static_rock_moss_material"
        )
    )
    shadow_lit = compact(
        function_body(source, "q3_probe_payload_apply_shadow_and_lit")
    )
    payload_root = compact(
        function_body(source, "compile_probe_subcell_payload")
    )
    water = compact(function_body(source, "q3_probe_payload_apply_water"))

    # The selector definition plus one admitted-subcell caller are the only
    # surviving references. Restoring a nested recomputation turns this red.
    assert whole.count("q3_probe_payload_effective_sun_dir(") == 2
    assert whole.count(
        "normalize(q3_probe_payload_effective_sun_dir("
    ) == 1
    materializer = (
        "precisevec3effective_sun_dir=normalize("
        "q3_probe_payload_effective_sun_dir("
        "input_value.context.flags));"
        "vsb_product_selected_effective_sun_dir=effective_sun_dir;"
    )
    compiler_call = (
        "compile_probe_subcell_payload("
        "input_value.context,input_value.facts,input_value.ray.dir)"
    )
    assert (
        "vec3vsb_product_selected_effective_sun_dir;" in whole
    )
    assert materializer in main
    assert compiler_call in main
    assert main.find(materializer) < main.find(compiler_call)

    # Preserve the established two-stage night expression. The moon accessor
    # normalizes the host vector first, then the admitted-subcell owner keeps
    # the historical outer normalization shared by every consumer.
    assert selector == (
        "returnq3_probe_payload_night_enabled(flags)"
        "?product_moon_dir():product_sun_dir();"
    )
    assert moon == "returnnormalize(moon_ctl.moon_dir.xyz);"

    # Product consumers keep their downstream expressions and consume only the
    # admitted carried direction.
    carried = "vsb_product_selected_effective_sun_dir"
    # One declaration, one admitted-slot write, and the established ambient /
    # material readers, including the current F14 daylight-godray owner. The
    # H-B ridge helper is an additional carried-direction reader, so this is a
    # lower bound rather than a stale exact whole-file count.
    #
    # 12 -> 11 (FL-4381/FL-4737 R1, 2026-08-13): the perspective and orthographic
    # cloud wrappers each used to read the carried direction for their own
    # private copy of the up-sun projection. Those two reads were CONSOLIDATED
    # into the single read inside product_cloud_shadow_projected_world, which
    # both wrappers now call. This is a reduction in the number of readers, which
    # is the direction this single-source contract wants; it is not a lost
    # consumer. The pin below proves the consolidated reader still exists, so
    # deleting the helper's read cannot pass by simply lowering this count.
    assert whole.count(carried) >= 12
    assert cloud_projected_world.count(f"precisevec3sun={carried};") == 1
    assert cloud_projected_world.count(carried) == 1
    assert whole.count("product_daylight_godray_veil(") == 2
    assert f"precisevec3sun={carried};" in daylight_godray
    assert "product_godray_cloud_veil_strength_at_sample_xz(" in daylight_godray
    assert "product_godray_terrain_light_visibility_at_air_sample(" in daylight_godray
    for pin in (
        "constintFL4101_DAY_GODRAY_PLANES=",
        "constfloatFL4101_DAY_GODRAY_PLANE_GAP=",
        "constvec3FL4101_DAY_GODRAY_VEIL_LIGHT=vec3(",
        "constvec3FL4101_DAY_GODRAY_VEIL_COLOR=vec3(",
        "precisevec2plane_normal_xz=vec2(-sun_xz.y,sun_xz.x)/sun_xz_len;",
        "precisevec3plane_normal=vec3(plane_normal_xz.x,0.0,plane_normal_xz.y);",
        "precisefloatdistance_error=mod(",
        "precisefloatplane_t_step=FL4101_DAY_GODRAY_PLANE_GAP/absolute_rate;",
        "precisevec3column_mid_world=ray.near_world+ray.dir*(0.5*t_end);",
        "precisefloatcolumn_veil=",
        "floatfront_to_back_transmittance=1.0;",
        "floataccumulated_strength=0.0;",
        "for(inti=0;i<FL4101_DAY_GODRAY_PLANES;++i)",
        "precisefloatridge_lit=",
        "precisefloatsample_strength=column_veil*ridge_lit;",
        "precisefloatlayer_opacity=",
        "accumulated_strength+=front_to_back_transmittance*layer_opacity;",
        "front_to_back_transmittance*=1.0-clamp(layer_opacity,0.0,0.96);",
    ):
        assert pin in whole
    raw_names = declared_function_names(
        source, r"product_godray_cloud_veil_strength_at_sample_xz"
    )
    assert len(raw_names) == 1
    raw_sampler = compact(function_body(source, raw_names[0]))
    assert "product_cloud_sample_uvs(" in raw_sampler
    assert "product_cloud_raw_mass_from_uvs(" in raw_sampler
    assert raw_sampler.count("stable_value_noise(") == 0
    assert "smoothstep(0.50,0.82" not in raw_sampler
    uv_sampler = compact(function_body(source, "product_cloud_sample_uvs"))
    assert "product_cloud_shadow_time(" in uv_sampler
    assert "cloud_dir" in uv_sampler
    ridge_names = declared_function_names(
        source, r"product_godray_terrain_light_visibility_at_air_sample"
    )
    assert len(ridge_names) == 1
    ridge = compact(function_body(source, ridge_names[0]))
    assert f"{ridge_names[0]}(" in daylight_godray
    assert "sun.xz" in ridge
    assert "cell_max_height(" in ridge
    assert ridge.count("for(") == 1
    assert "any(lessThan(probe_cell,ivec2(0)))" in ridge
    assert "any(greaterThanEqual(probe_cell,dims))" in ridge
    assert "q3_probe_payload_" not in ridge
    assert "tlas" not in ridge.lower()
    assert "receiver_cast_shadow" not in ridge
    assert "constintFL4101_DAY_GODRAY_RIDGE_STEPS=6;" in whole
    assert "constintFL4101_DAY_GODRAY_RIDGE_MAX_STEPS=8;" in whole
    assert "receiver_depth" in daylight_godray
    assert "min(clamp(receiver_depth,0.0,ray.len),180.0)" in daylight_godray
    assert "camera_position" not in daylight_godray
    assert "SCREEN_UV" not in daylight_godray
    assert "receiver_world" not in daylight_godray
    assert "receiver_depth/" not in daylight_godray
    assert "receiver_depth/float" not in daylight_godray
    for retired in ("shaft_0", "shaft_1", "shaft_2", "shaft_3", "Rake", "lake_along", "coverage", "terrain_visibility", "godray_pass", "godray_binding", "product_sun_dir("):
        assert retired not in daylight_godray
    assert "ProductDaylightGodrayVeilgodray_veil=product_daylight_godray_veil(input_value.ray,effect_depth,capture_godray_visibility);" in finalizer
    assert "record.fg.rgb=clamp(mix(record.fg.rgb*gap_retention+godray_veil.light" in finalizer
    assert "record.bg.rgb=clamp(mix(record.bg.rgb*gap_retention+godray_veil.light*0.82" in finalizer
    assert "godray_nonband_" not in finalizer
    assert "godray_addition" not in finalizer
    assert f"max(dot(nrm,{carried}),0.0)" in terrain_shade
    assert f"vec3sun={carried};" in static_shadow
    assert f"max(dot(n,{carried}),0.0)" in cast_shadow
    assert f"precisevec3sun={carried};" in heightfield
    # Both cloud registers still reach the carried direction, now through the one
    # shared helper rather than through two private copies.
    assert f"precisevec3sun={carried};" in cloud_projected_world
    assert "product_cloud_shadow_projected_world(" in cloud_projected
    assert "product_cloud_shadow_projected_world(" in ortho_projected
    assert material.count(
        f"max(dot(nrm,{carried}),0.0)"
    ) == 1
    assert static_rock_moss.count(
        f"max(dot(nrm,{carried}),0.0)"
    ) == 1
    for body in (
        terrain_shade,
        static_shadow,
        cast_shadow,
        heightfield,
        cloud_projected_world,
        cloud_projected,
        ortho_projected,
        material,
        static_rock_moss,
    ):
        assert "q3_probe_payload_effective_sun_dir(" not in body

    # The carried value reaches every nested cloud tap. The thirteen-sample
    # footprint and the projection-specific density sources remain unchanged.
    assert cloud_filter.count(
        "q3_probe_payload_live_cloud_shadow_for_cell("
    ) == 13
    assert ortho_filter.count(
        "q3_probe_payload_ortho_cloud_density_projected_at("
    ) == 5
    assert (
        "max(abs(sun.y),0.08)"
        "*sign(sun.y==0.0?1.0:sun.y)" in cloud_projected_world
    )
    assert "(receiver+sun*t_cloud).xz" in cloud_projected_world
    assert (
        "product_cloud_density_at_sample_xz("
        "(receiver+sun*t_cloud).xz)" in cloud_projected_world
    )
    # Neither wrapper may keep a private copy of the projection arithmetic.
    for wrapper in (cloud_projected, ortho_projected):
        assert "sun*t_cloud" not in wrapper
        assert carried not in wrapper
    assert (
        "q3_probe_payload_clouds_enabled(flags)" in cloud_filter
        and "q3_probe_payload_clouds_enabled(flags)" in ortho_filter
    )

    # Compiler plumbing carries the pre-quant receiver scalar into material.
    # The final shadow/lit phase has no such argument: retained static paint is
    # complete, while dynamic movers and water retain their established late
    # cast lookup.
    assert (
        "q3_probe_payload_apply_material("
        "context,glyph,family,fg,bg,"
        "terrain_face_role,terrain_elevation_row,"
        "foliage_root_ramp_band,receiver_cast_shadow_attenuation)"
        in payload_root
    )
    assert (
        "q3_probe_payload_apply_shadow_and_lit("
        "context,facts,fg,bg)"
        in payload_root
    )
    assert payload_root.find("q3_probe_payload_apply_material(") < (
        payload_root.find("q3_probe_payload_apply_shadow_and_lit(")
    )
    assert (
        "q3_probe_payload_cast_shadow_attenuation("
        "receiver,receiver_normal,context.surface_kind,receiver_face_idx,"
        "context.flags)" in shadow_lit
    )
    assert (
        "if(context.surface_kind==OWNER_DYNAMIC_MESH){"
        "q3_probe_payload_apply_lit_gain(fg,bg,context.lit_rgb);}"
        in shadow_lit
    )
    assert shadow_lit.count("q3_probe_payload_apply_lit_gain(") == 1
    assert "lit_target" not in shadow_lit
    assert "receiver_cast_shadow_attenuation" not in shadow_lit
    assert shadow_lit.count(
        "q3_probe_payload_cast_shadow_attenuation("
    ) == 1

    # Water's authored sample, glyph, and paint law stays independent. Its
    # later common cast-shadow phase merely receives the shared direction.
    assert (
        "ProductWaterSamplewater=product_water_sample("
        "water_facts,"
        "semantic_water_xz);" in water
    )
    assert "glyph=water_glyph_candidate(water);" in water
    assert "product_apply_day_water_pair(water,glyph,fg,bg);" in water
    assert carried not in water


def mutation_witnesses(source: str) -> dict[str, str]:
    lambert = (
        "nrm, vsb_product_selected_effective_sun_dir), 0.0);"
    )
    restored_lambert = (
        "nrm, normalize(q3_probe_payload_effective_sun_dir("
        "context.flags))), 0.0);"
    )
    materializer = (
        "\t\t\t\tprecise vec3 effective_sun_dir = normalize(\n"
        "\t\t\t\t\tq3_probe_payload_effective_sun_dir(\n"
        "\t\t\t\t\t\tinput_value.context.flags));\n"
        "\t\t\t\tvsb_product_selected_effective_sun_dir =\n"
        "\t\t\t\t\teffective_sun_dir;\n"
    )
    compiler = (
        "\t\t\t\tProbePayloadCompileResult compiled =\n"
        "\t\t\t\t\tcompile_probe_subcell_payload(\n"
        "\t\t\t\t\t\tinput_value.context,\n"
        "\t\t\t\t\t\tinput_value.facts,\n"
        "\t\t\t\t\t\tinput_value.ray.dir);\n"
    )
    mutants = {
        "terrain_shade_recompute_restored": replace_in_function(
            source,
            "q3_probe_payload_compile_terrain_shade",
            "nrm, vsb_product_selected_effective_sun_dir), 0.0);",
            "nrm, normalize(q3_probe_payload_effective_sun_dir("
            "flags))), 0.0);",
        ),
        "terrain_base_recompute_restored": replace_nth_in_function(
            source,
            "q3_probe_payload_apply_material",
            lambert,
            restored_lambert,
            0,
        ),
        "mesh_base_recompute_restored": replace_in_function(
            source,
            "q3_probe_payload_apply_static_rock_moss_material",
            lambert,
            restored_lambert,
        ),
        "heightfield_recompute_restored": replace_in_function(
            source,
            "q3_probe_payload_heightfield_shadow_attenuation",
            "precise vec3 sun = "
            "vsb_product_selected_effective_sun_dir;",
            "precise vec3 sun = normalize("
            "q3_probe_payload_effective_sun_dir(flags));",
        ),
        # FL-4381/FL-4737 R1 (2026-08-13): both cloud registers used to hold a
        # private copy of the up-sun projection and therefore a private read of
        # the carried direction. Those two reads were consolidated into
        # product_cloud_shadow_projected_world. The recompute witness follows the
        # read into that helper, and two NEW witnesses prove the consolidation
        # itself: a wrapper that re-derives the direction locally instead of
        # delegating must still turn the contract red.
        "shared_cloud_projection_recompute_restored": replace_in_function(
            source,
            "product_cloud_shadow_projected_world",
            "precise vec3 sun = "
            "vsb_product_selected_effective_sun_dir;",
            "precise vec3 sun = normalize("
            "q3_probe_payload_effective_sun_dir(flags));",
        ),
        "perspective_cloud_bypasses_shared_projection": replace_in_function(
            source,
            "q3_probe_payload_live_cloud_shadow_projected_at",
            "return product_cloud_shadow_projected_world(",
            "return q3_probe_payload_effective_sun_dir(flags).x * "
            "product_cloud_shadow_projected_world(",
        ),
        "ortho_cloud_bypasses_shared_projection": replace_in_function(
            source,
            "q3_probe_payload_ortho_cloud_density_projected_at",
            "return product_cloud_shadow_projected_world(",
            "return q3_probe_payload_effective_sun_dir(flags).x * "
            "product_cloud_shadow_projected_world(",
        ),
        "cast_lambert_recompute_restored": replace_in_function(
            source,
            "q3_probe_payload_cast_shadow_attenuation",
            "n, vsb_product_selected_effective_sun_dir), 0.0);",
            "n, normalize(q3_probe_payload_effective_sun_dir("
            "flags))), 0.0);",
        ),
        "static_shadow_recompute_restored": replace_in_function(
            source,
            "q3_probe_payload_static_shadow_density",
            "vec3 sun = vsb_product_selected_effective_sun_dir;",
            "vec3 sun = normalize("
            "q3_probe_payload_effective_sun_dir(flags));",
        ),
        "outer_normalize_deleted": replace_in_function(
            source,
            "main",
            "precise vec3 effective_sun_dir = normalize(\n"
            "\t\t\t\t\tq3_probe_payload_effective_sun_dir(\n"
            "\t\t\t\t\t\tinput_value.context.flags));",
            "precise vec3 effective_sun_dir =\n"
            "\t\t\t\t\tq3_probe_payload_effective_sun_dir(\n"
            "\t\t\t\t\t\tinput_value.context.flags);",
        ),
        "night_source_replaced_with_day_constant": replace_in_function(
            source,
            "q3_probe_payload_effective_sun_dir",
            "return q3_probe_payload_night_enabled(flags)\n"
            "\t\t? product_moon_dir() : product_sun_dir();",
            "return product_sun_dir();",
        ),
        "moon_inner_normalize_deleted": replace_in_function(
            source,
            "product_moon_dir",
            "return normalize(moon_ctl.moon_dir.xyz);",
            "return moon_ctl.moon_dir.xyz;",
        ),
        "materializer_moved_after_compiler": replace_in_function(
            source,
            "main",
            materializer + compiler,
            compiler + materializer,
        ),
        "static_lit_normalizer_reintroduced": replace_in_function(
            source,
            "q3_probe_payload_apply_shadow_and_lit",
            "if (context.surface_kind == OWNER_DYNAMIC_MESH) {",
            "if (mesh_surface) {",
        ),
    }
    reds: dict[str, str] = {}
    for name, mutant in mutants.items():
        try:
            assert_effective_direction_single_source(mutant)
        except AssertionError as error:
            reds[name] = str(error) or "AssertionError"
            continue
        raise AssertionError(f"mutation stayed green: {name}")
    return reds


def main() -> int:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    assert_effective_direction_single_source(source)
    reds = mutation_witnesses(source)
    print(
        "FL4431_PRODUCT_EFFECTIVE_SUN_DIRECTION_SINGLE_SOURCE_PASS "
        "source_sites=11->1 terrain_perspective_calls=18->1 "
        "terrain_ortho_calls=17->1 static_mesh_calls=17->1 "
        f"mutants={','.join(reds)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
