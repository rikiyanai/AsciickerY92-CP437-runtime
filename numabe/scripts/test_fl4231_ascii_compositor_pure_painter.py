#!/usr/bin/env python3
"""Guard the ASCII compositor's one-way Product-to-pixel ownership boundary.

The FL-4431 retained-scene activation removed the probe/raster arbitration that
used to live in the final compositor. These tests verify the replacement chain:
Visible Surface Builder chooses geometry, Product authors one finalized subcell
record, and the compositor only rasterizes that record. The module supports
both pytest collection and direct execution so a plain ``python3`` invocation
cannot silently run zero checks and report success.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

from PIL import Image, ImageChops
import pytest


ROOT = Path(__file__).resolve().parents[1]
SHADER = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
MAIN = ROOT / "godot_project" / "scripts" / "Main.gd"
GODOT_FONT = ROOT / "godot_project" / "assets" / "font.png"
CP437_20 = ROOT / "assets" / "fonts" / "cp437_20x20.png"
ADDITIVE_PAGE32 = (
    ROOT / "godot_project" / "assets" / "glyphs" / "atlases"
    / "material.additive.v1.page32.png"
)
MORPHOLOGY_PAGE32 = (
    ROOT / "godot_project" / "assets" / "glyphs" / "atlases"
    / "material.morphology.v2.page32.png"
)


def _main_body(src: str) -> str:
    marker = "\nvoid main() {"
    start = src.index(marker) + 1
    depth = 0
    body_start = None
    for i in range(start, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
            if body_start is None:
                body_start = i + 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[body_start:i]
    raise AssertionError("could not extract ascii_compositor.glsl main()")


def _extract_function_bodies(src: str) -> dict[str, str]:
    functions: dict[str, str] = {}
    pattern = re.compile(
        r"(?m)^(?:[A-Za-z_][A-Za-z0-9_]*\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
    )
    for match in pattern.finditer(src):
        name = match.group(1)
        body_start = match.end()
        depth = 1
        i = body_start
        while i < len(src):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    functions[name] = src[body_start:i]
                    break
            i += 1
    return functions


def _function_body(src: str, signature: str, end_marker: str) -> str:
    start = src.index(signature)
    end = src.index(end_marker, start)
    return src[start:end]


def _strip_glsl_comments(src: str) -> str:
    """Remove comments so executable-owner checks ignore documentation.

    Projection comments intentionally name the retired matrix expression while
    explaining why it is forbidden. This helper keeps the assertion focused on
    executable shader text.
    """

    return re.sub(r"/\*.*?\*/|//[^\n]*", "", src, flags=re.DOTALL)


def _main_reachable_functions(src: str) -> set[str]:
    functions = _extract_function_bodies(src)
    reachable = {"main"}
    queue = ["main"]
    while queue:
        current = queue.pop()
        body = functions[current]
        for candidate in functions:
            if candidate in reachable:
                continue
            if re.search(r"\b" + re.escape(candidate) + r"\s*\(", body):
                reachable.add(candidate)
                queue.append(candidate)
    return reachable


def _assert_final_product_glyph_painter_contract(source: str) -> None:
    """Require the accepted Product glyph and continuous paint law at the read seam."""

    body = _main_body(source)
    assert "bool cell_mask_present = final_product_status_has_cell_mask(product_status);" in body
    assert "int(final_product_status_cell_mask_glyph(product_status))" in body
    assert ": 0xDB;" in body
    assert "color = mix(resolved.bg, resolved.fg, intensity);" in body
    assert "color = chose_fg ? resolved.fg : resolved.bg;" not in body
    assert "compositor_uses_perspective_paint" not in body
    assert "perspective_product_ink_threshold" not in source
    assert "switch (product.r0.y)" not in body
    assert "resolve_cell_owner_product(" not in body


def _replace_exact(source: str, old: str, new: str, count: int = 1) -> str:
    assert source.count(old) == count, old
    return source.replace(old, new)


def test_main_dispatches_from_cell_owner_product_only() -> None:
    body = _main_body(SHADER.read_text(encoding="utf-8"))

    # The finalized buffer is laid out as four subcells per ANSI cell. Pin the
    # producer-consumer index law here because a one-slot drift still produces
    # a plausible image while painting the wrong quarter of every character.
    assert "uint slot = final_subcell_slot(sub, cell_span_px);" in body
    # FL-4359: a perspective view never reads binding 44; the load is
    # orthographic-only and the perspective record is explicit absence.
    assert "if (!fl4359_perspective_view) {\n\t\tproduct = finalized_subcell_products.products[cell_index * 4u + slot];" in body
    assert "sample_compositor_glyph_intensity_native_footprint(" in body
    assert "glyph, sub, cell_span_px" in body
    # Projection no longer selects a second modal/raw glyph or paint grammar.
    assert "bool perspective_paint = compositor_uses_perspective_paint();" not in body
    assert "fractional_glyph_sampling_enabled" not in body
    assert "float intensity = clamp(" in body
    assert "float intensity = step(" not in body
    assert "color = chose_fg ? resolved.fg : resolved.bg;" not in body
    assert "if (foliage_center_pin) {" in body
    assert "terrain_center_pin" not in body
    assert "color = resolved.fg;" in body
    assert "color = mix(resolved.bg, resolved.fg, intensity);" in body
    assert "record_final_cell_paint(pos, ansi_xy, ansi_size, cell_min_px," in body
    assert "#ifdef FL4431_FINAL_CELL_CAPTURE" in body
    assert "imageStore(color_image, pos, vec4(color, 1.0));" in body
    assert "load_cell_owner(" not in body
    assert "resolve_cell_owner_product(" not in body
    assert "switch (product.r0.y)" not in body
    assert "case CO_OWNER_" not in body

    forbidden = [
        "fetch_sample(",
        "texture(depth_tex",
        "fl4129_cell_has_water_surface_owner(",
        "terrain_cell_is_visible_water_owner(",
        "raw_compositor_water_at(",
        "surface_kind ==",
        "visible_surface_owner_image",
        "resolve_ansi_cell_cached(",
        "resolve_static_world_face(",
        "resolve_dynamic_mover_face(",
        "fl4231_write_static_world_facts(",
        "fl4231_write_dynamic_mover_facts(",
        "apply_baseline_compositor_weather_layers(",
        "apply_airplane_vapor_trail(",
        "fl4269_apply_blood_mark_to_owner_pixel(",
        "apply_lighthouse_beam_shaft(",
        "apply_screen_space_shafts(",
        "apply_unified_view_ray_god_rays(",
        "apply_fl4129_water_surface_owner(",
        "water_cell_sample_xz(",
        "water_cell_screen_uv(",
        "SKY_BG",
        "terrain_cell_from_winner(",
    ]
    for token in forbidden:
        assert token not in body, token


def test_fl4057_final_paint_has_only_cell_product_and_presentation_evaluators() -> None:
    src = SHADER.read_text(encoding="utf-8")
    code = _strip_glsl_comments(src)
    reachable = _main_reachable_functions(src)

    # This exact set includes the bounded native-pixel actor overlay helpers.
    # Any new geometry selector, material compiler, weather stage, or effect
    # arbitrator added here would create a second visible owner after Product
    # finalized the record.
    assert reachable == {
        "main",
        "fl4359_paint_actor_overlay",
        "final_subcell_slot",
        "final_product_status_is_valid",
        "final_product_status_has_foliage",
        "final_product_status_foliage_role",
        "final_product_status_has_cell_mask",
        "final_product_status_cell_mask_glyph",
        "sample_compositor_glyph_intensity_native_footprint",
        "sample_compositor_page_footprint",
        "compositor_atlas_source_pos",
        "actor_water_surface_world_y",
        "actor_cell_word",
        "compositor_actor_fixed256_pair",
        "compositor_actor_root_world",
        "compositor_actor_vertical_offset_world",
        "compositor_actor_water_contact",
        "actor_attack_phase_id",
        "actor_attack_phase_frame_id",
        "actor_dash_ticks_remaining",
        "actor_rig_byte",
        "actor_attack_rig",
        "actor_dash_coverage",
        "actor_attack_coverage",
        "actor_hit_survives_water_surface",
        "fl4346_actor_survives_world_depth",
        "fl4346_prepare_world_depth_context",
        "compositor_actor_card_debug_ink",
        "actor_fragment_over",
        "compositor_actor_ray_from_pixel",
        "compositor_actor_hit",
        "compositor_actor_fragment_for_cell",
    }
    assert "load_cell_owner" not in code
    assert "resolve_cell_owner_product" not in code
    assert "FEATURE_FRACTIONAL_GLYPH_SAMPLING_FLAG" not in src
    assert "sample_compositor_glyph_intensity_fractional" not in src
    assert "float sample_compositor_glyph_intensity_native_footprint(" in src
    assert "compositor_atlas_source_pos(sub, cell_span_px, source_span)" in src
    assert "sample_compositor_glyph_intensity_native_footprint(\n\t\t\tglyph, sub, cell_span_px)" in src
    assert "sample_compositor_glyph_intensity_aa" not in src
    assert "float intensity = clamp(" in src
    assert "float intensity = step(" not in src
    assert src.count("layout(set = 0, binding = 44, std430) readonly buffer") == 1
    assert "FinalizedSubcellProducts" in src
    assert "finalized_subcell_products.products[cell_index * 4u + slot]" in src
    assert src.count("layout(set = 0, binding = 68, std140) uniform") == 1
    assert "uint projection_kind = product_vsb_frame.view_identity.z;" in src
    assert "params.inv_view_proj" not in code
    assert "color = mix(resolved.bg, resolved.fg, intensity);" in src
    assert "color = chose_fg ? resolved.fg : resolved.bg;" not in src
    assert "paint_cell_glyph" not in src
    assert "rotation_crawl_threshold_jitter" not in src
    assert "stable_cell_noise" not in src
    assert "baseline_feature_rotation_crawl_enabled" not in src
    assert "bool foliage_product_present =" in src
    assert "final_product_status_has_foliage(product_status)" in src
    assert "bool cell_mask_foliage_present = cell_mask_present" in src
    assert "bool foliage_center_pin = cell_mask_foliage_present" in src
    assert "float(cell_span_px.x) * 0.12" in src
    assert "float(cell_span_px.y) * 0.12" in src
    assert "Q3_DIAG_MARKER_FOLIAGE" not in src
    assert "q3_probe_subcell_diagnostics" not in src
    # These numeric bands still occur in Product-side foliage helpers. The
    # painter itself must not reintroduce the retired center-pin magic values.
    main = _main_body(src)
    assert "0.22" not in main
    assert "0.30" not in main
    assert "bool terrain_product_present" not in src
    assert "bool mountain_product_present" not in src
    assert "bool terrain_center_pin" not in src
    assert "const uint TERRAIN_FAMILY_CLIFF_ROCK" not in src
    assert "const uint TERRAIN_FAMILY_WATER_SKIP" not in src
    assert "if (foliage_center_pin) {" in src
    assert "color = mix(resolved.bg, resolved.fg, intensity);" in src
    # Product material-family metadata may be captured, but the compositor may
    # not interpret it as terrain morphology or force atlas-mask pixels.
    main = _main_body(src)
    assert "material_family == TERRAIN_FAMILY_CLIFF_ROCK" not in main
    assert "material_family == TERRAIN_FAMILY_WATER_SKIP" not in main
    assert "mountain_present" not in src


ADMISSION = SHADER.parent / "actor_depth_admission.glslinc"


def _assert_finalized_product_depth_helper(source: str) -> None:
    """Pin the actor-depth admission consumer without prescribing visible paint.

    FL-4848: the world-depth decision is Product's declared per-quarter
    admission (actor_depth_admission.glslinc). The painter compares its own hit
    distance against admitted facts; it reads no Product status word, sibling
    record, glyph, colour, or competing actor geometry owner.
    """
    include = ADMISSION.read_text(encoding="utf-8")
    functions = _extract_function_bodies(include)
    decide = functions["actor_depth_admission_decide"]
    admits = functions["actor_depth_admits"]
    assert "if (!product_valid || is_sky || is_water || !finite) {" in decide
    assert "return actor_depth_admission_pack(false, false, FL4346_WORLD_DEPTH_FAIL_OPEN," in decide
    assert "disagreement = max(disagreement, abs(sibling_t[n] - world_ray_t));" in decide
    assert "float slack = max(disagreement * FL4346_WORLD_DEPTH_SLACK_GAIN," in decide
    assert "!= ACTOR_DEPTH_ADMISSION_ABI) return true;" in admits
    assert "if ((flags & ACTOR_DEPTH_ADMISSION_COMPARE) == 0u) return true;" in admits
    assert "if (hit_ray_t <= world_ray_t + slack) return true;" in admits
    for forbidden in (
        ".fg", ".bg", ".r0.", ".r1.", "finalized_subcell_products",
        "final_product_status", "sample_compositor_glyph_intensity", "imageLoad",
    ):
        assert forbidden not in admits, forbidden
    # The painter consumes the admission through the one comparison law.
    assert "fl4346_prepare_world_depth_context" not in source
    assert "Fl4346WorldDepthContext" not in source
    assert "finalized_subcell_products.products[base + (slot ^ neighbour)]" not in source
    assert source.count("actor_depth_admissions.quarters[cell_index * 4u + subcell_slot]") == 1
    assert source.count("actor_depth_admits(") == 2


def test_finalized_product_depth_helper_rejects_abi_mutation() -> None:
    source = SHADER.read_text(encoding="utf-8")
    _assert_finalized_product_depth_helper(source)
    include = ADMISSION.read_text(encoding="utf-8")
    assert "!= ACTOR_DEPTH_ADMISSION_ABI) return true;" in include

    depth_mutant = source.replace(
        "actor_depth_admissions.quarters[cell_index * 4u + subcell_slot]",
        "uvec4(0u)",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_finalized_product_depth_helper(depth_mutant)


def test_compositor_consumes_final_product_glyph() -> None:
    _assert_final_product_glyph_painter_contract(SHADER.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "int(final_product_status_cell_mask_glyph(product_status)),",
            "int(product.r0.z),",
        ),
        (
            "color = mix(resolved.bg, resolved.fg, intensity);",
            "color = intensity > 0.5 ? resolved.fg : resolved.bg;",
        ),
    ],
)
def test_product_painter_contract_rejects_modal_or_constant_mutants(old: str, new: str) -> None:
    source = SHADER.read_text(encoding="utf-8")
    mutant = _replace_exact(source, old, new)
    with pytest.raises(AssertionError):
        _assert_final_product_glyph_painter_contract(mutant)


def test_fl4216_extended_atlas_cell_size_is_texture_derived() -> None:
    src = SHADER.read_text(encoding="utf-8")
    body = _function_body(
        src,
        "float sample_compositor_glyph_intensity(int glyph, ivec2 glyph_sub, int glyph_w_px, int glyph_h_px) {",
        "\n}\n\n#ifdef FL4431_FINAL_CELL_CAPTURE",
    )

    assert "const int COMPOSITOR_GLYPH_ATLAS_COLS = 16;" in src
    assert "EXT_GLYPH_W_PX" not in body
    assert "EXT_GLYPH_H_PX" not in body
    assert "MORPH_GLYPH_W_PX" not in body
    assert "MORPH_GLYPH_H_PX" not in body
    assert "int ext_cell_px = max(ext_size.x / COMPOSITOR_GLYPH_ATLAS_COLS, 1);" in body
    assert "int morph_cell_px = max(morph_size.x / COMPOSITOR_GLYPH_ATLAS_COLS, 1);" in body
    assert "ivec2 ext_base = ivec2(col * ext_cell_px, row * ext_cell_px);" in body
    assert "ivec2 morph_base = ivec2(col * morph_cell_px, row * morph_cell_px);" in body


def test_fl4482_cp437_atlas_cell_size_is_texture_derived() -> None:
    # FL-4848: the orphan sample_cp437_glyph_intensity/sample_compositor_glyph_intensity
    # pair is deleted; the live CP437 tail of the native footprint sampler
    # carries the same texture-derived cell-size law.
    src = SHADER.read_text(encoding="utf-8")
    assert "sample_cp437_glyph_intensity" not in src
    assert src.count("float sample_compositor_glyph_intensity") == 1
    body = _function_body(
        src,
        "float sample_compositor_glyph_intensity_native_footprint(",
        "\n}\n",
    )
    assert "ivec2 font_size = textureSize(font_tex, 0);" in body
    assert "max(font_size.x / COMPOSITOR_GLYPH_ATLAS_COLS, 1)" in body
    assert "max(font_size.y / COMPOSITOR_GLYPH_ATLAS_COLS, 1)" in body
    assert "glyph_col * source_span.x" in body
    assert "glyph_row * source_span.y" in body
    assert "glyph_col * glyph_w_px + glyph_sub.x" not in body


def test_fl4482_final_mask_samples_page_native_footprint_for_all_runtime_pages() -> None:
    src = SHADER.read_text(encoding="utf-8")
    native = _function_body(
        src,
        "float sample_compositor_glyph_intensity_native_footprint(",
        "\n}\n\n#ifdef FL4431_FINAL_CELL_CAPTURE",
    )
    footprint = _function_body(
        src,
        "float sample_compositor_page_footprint(",
        "\n}\n\nfloat sample_compositor_glyph_intensity_native_footprint",
    )
    main = _main_body(src)

    assert "sample_compositor_glyph_intensity_native_footprint(\n\t\t\tglyph, sub, cell_span_px)" in main
    assert "glyph_pos" not in main
    assert "sample_compositor_glyph_intensity_aa" not in src
    assert "vec2(0.5)) * vec2(source_span_px) / vec2(target) - vec2(0.5)" in src
    assert "textureSize(font_tex, 0)" in native
    assert "textureSize(extended_glyph_tex, 0)" in native
    assert "textureSize(morphology_glyph_tex, 0)" in native
    assert "compositor_atlas_source_pos(sub, cell_span_px, source_span)" in native
    assert "source_span_px.x > cell_span_px.x" in footprint
    assert "coverage_in_alpha ? sample_value.a : sample_value.r" in footprint
    assert "coverage_in_alpha ? s00.a : s00.r" in footprint
    assert native.count("source_span, cell_span_px, true)") == 2
    assert native.count("source_span, cell_span_px, false)") == 1
    assert footprint.count("texelFetch(") == 5


def test_fl4482_generated_pages_publish_antialias_coverage_in_alpha() -> None:
    """Generated page RGB is binary; alpha owns the authored edge coverage."""

    for page_path in (ADDITIVE_PAGE32, MORPHOLOGY_PAGE32):
        atlas = Image.open(page_path).convert("RGBA")
        pixels = atlas.get_flattened_data()
        red_levels = {pixel[0] for pixel in pixels}
        alpha_levels = {pixel[3] for pixel in pixels}
        assert red_levels == {0, 255}
        assert len(alpha_levels) > 2


def test_fl4482_godot_font_atlas_contains_visible_high_resolution_coverage() -> None:
    """Prevent another 640px atlas that is only a nearest-neighbor 20px copy."""

    atlas = Image.open(GODOT_FONT).convert("L")
    assert atlas.size == (640, 640)
    colors = atlas.getcolors(maxcolors=1024)
    assert colors is not None
    assert len(colors) > 2
    nearest_20 = Image.open(CP437_20).convert("L").resize(
        atlas.size,
        Image.Resampling.NEAREST,
    )
    diff = ImageChops.difference(atlas, nearest_20)
    assert diff.getbbox() is not None


def test_fl4408_texel_splat_full_is_deleted_from_runtime_render_path() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")

    # FL-4057/FL-4359 revived Ebert lane: keep the deleted second renderer dead.
    # Future probe work must feed cell_owner_product instead of restoring this path.
    assert "texel_splat_full" not in effect
    assert "TEXEL_SPLAT_FULL" not in effect
    assert "_texel_splat_full" not in effect
    assert not list((ROOT / "godot_project").rglob("texel_splat_full*.glsl"))

    # Main still records the historical command-line label in diagnostic JSON.
    # It does not pass that value to World, the compositor, and any renderer.
    # Pin the complete use list so the label cannot quietly regain runtime
    # authority while remaining available to interpret older capture scripts.
    probe_body = _function_body(
        main,
        "func _run_fl4057_ab_yaw_capture_probe() -> void:",
        "\nfunc _install_pause_menu",
    )
    spike_lines = [
        line.strip()
        for line in probe_body.splitlines()
        if "spike_enabled" in line
    ]
    assert spike_lines == [
        'var spike_enabled := OS.get_cmdline_user_args().has("--texel-splat-full")',
        '"texel_splat_full": spike_enabled,',
        '"texel_splat_full": spike_enabled,',
        '"texel_splat_full": spike_enabled,',
    ]

    render_body = effect.split("func _render_callback(", 1)[1].split("\nfunc _", 1)[0]
    forbidden = [
        "if texel_splat_full_enabled:",
        "_dispatch_texel_splat_full_probe_capture(",
        "_dispatch_texel_splat_full_probe_owner_splat(",
        "_dispatch_texel_splat_full_probe_owner_expand(",
        "_dispatch_texel_splat_full_terrain_ray(",
        "_dispatch_texel_splat_full_fail_closed(",
        "_emit_texel_splat_full_owner_churn_metric(",
    ]
    for token in forbidden:
        assert token not in render_body, token


def test_fl4359_vsb_product_compositor_is_the_sole_visible_owner() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    shader_dir = ROOT / "godot_project"
    product_src = _expand_includes((shader_dir / "render/product/cell_owner_product.glsl").read_text(encoding="utf-8"))
    compositor_src = SHADER.read_text(encoding="utf-8")

    # These are the three surviving stages. The host compiles each stage, the
    # callback dispatches VSB before Product, and compositor binding 44 consumes
    # the same final RID that Product writes at binding 55.
    for name in [
        "render/backends/ortho/visibility/visible_surface_builder.glsl",
        "render/product/cell_owner_product.glsl",
        "render/ascii/raster/ascii_compositor.glsl",
    ]:
        assert (shader_dir / name).exists(), name
    assert '_compile_compute("res://render/ascii/raster/ascii_compositor.glsl")' in effect
    # Product now supplies specialization constants for default-off phase
    # counters plus its decode-first A/B gate. Accept whitespace and additional
    # arguments while still requiring this exact shader path at the compile edge.
    assert re.search(
        r'_compile_compute\(\s*"res://render/product/cell_owner_product\.glsl"',
        effect,
    )
    assert '"res://render/backends/ortho/visibility/visible_surface_builder.glsl"' in effect
    assert "layout(set = 0, binding = 67, std430) readonly buffer" in product_src
    assert "VisibleSurfaceProduct products[];" in product_src
    assert "layout(set = 0, binding = 44, std430) readonly buffer" in compositor_src
    assert "FinalSubcellProductRecord products[];" in compositor_src

    # The old visible probe chain is physically absent. The standalone clear
    # utility is outside this ownership assertion; no live callback compiles,
    # dispatches, and reads it as a visible-payload source.
    for retired_name in [
        "ascii_probe_capture.glsl",
        "ascii_probe_capture_downsample.glsl",
        "ascii_probe_light.glsl",
        "ascii_probe_cull.glsl",
        "ascii_probe_edge_mask.glsl",
        "ascii_probe_splat_args.glsl",
        "ascii_probe_splat.glsl",
        "ascii_probe_resolve.glsl",
    ]:
        assert not list(shader_dir.rglob(retired_name)), retired_name
        assert f'res://shaders/{retired_name}' not in effect

    render_body = effect.split("func _render_callback(", 1)[1].split("\nfunc _", 1)[0]
    assert "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)" in render_body
    assert "_dispatch_cell_owner_product(" in render_body
    assert "u_final_subcell_product.binding = 44" in render_body
    assert "u_final_subcell_product.add_id(final_subcell_bind)" in render_body
    assert (
        "u_compositor_vsb_product_frame.uniform_type = "
        "RenderingDevice.UNIFORM_TYPE_UNIFORM_BUFFER"
    ) in render_body
    assert "u_compositor_vsb_product_frame.binding = 68" in render_body
    assert (
        "u_compositor_vsb_product_frame.add_id(_vsb_product_frame_receipt_rd)"
        in render_body
    )
    for token in [
        "_dispatch_ascii_probe_",
        "_dispatch_probe_",
        "probe_clear_pipeline",
        "probe_light_pipeline",
        "probe_cull_pipeline",
        "probe_edge_mask_pipeline",
        "probe_resolve_pipeline",
    ]:
        assert token not in render_body, token


def test_fl4708_tree_and_leaf_emitter_runtime_stays_deleted() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    prepare_body = _function_body(
        effect,
        "func _prepare_cell_owner_product_dispatch(",
        "\n\n# Dispatch the already-prepared Product",
    )

    assert "static_world_mesh_face_count" not in effect
    for retired in (
        "leaf_scan_faces",
        "leaf_lanes",
        "leaf_emitter_armed",
        "leaf_particle_bank_rd",
        "FL4431_ENABLE_LEAF_EMITTER",
        "_probe_u_storage(59",
    ):
        assert retired not in prepare_body


def test_compositor_does_not_keep_dead_weather_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "void apply_baseline_compositor_weather_layers(",
        "single weather owner",
        "rain_direct_streak_alpha(pos",
        "weather_snow_state()",
        "foliage_query(weather_cell)",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_cloud_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "float cloud_density_projected_at(",
        "float cloud_density_at(",
        "float cloud_shadow_for_cell(",
        "float cloud_density_volume_at(",
        "cloud_density_projected_at(cell",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_local_light_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "layout(set = 0, binding = 6, std140) uniform LocalLightControl",
        "layout(set = 0, binding = 7, std430) buffer LocalLightRecord",
        "layout(set = 0, binding = 9, std430) buffer LocalLightGlyphRecord",
        "bool local_light_records_enabled()",
        "vec3 local_light_world_pos(int i)",
        "LightAccum local_light_accumulation(ivec2 cell, vec3 world_pos, vec3 normal, float depth_key)",
        "void apply_local_light_volume(inout vec3 fg_vec, inout vec3 bg_vec, LightAccum accum)",
        "void apply_local_light_glyph_overlay(inout AnsiResolve r, ivec2 ansi_xy, ivec2 fb_size)",
        "local_light_glyph_records.rows",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_world_resolve_fact_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "AnsiResolve resolve_static_world_face(",
        "AnsiResolve resolve_dynamic_mover_face(",
        "void init_ansi_cache(",
        "AnsiResolve resolve_ansi_cell_cached(",
        "bool projected_terrain_resolve(",
        "void fl4231_write_static_world_facts(",
        "void fl4231_write_dynamic_mover_facts(",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_blood_cell_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "vec3 fl4269_apply_blood_mark_to_owner_pixel(",
        "vec2 blood_mark_for_cell(",
        "blood_marks.marks",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_screen_space_light_owners() -> None:
    src = SHADER.read_text(encoding="utf-8")
    old_beam_splat_image = "beam_splat_depth" + "_image"

    forbidden = [
        "float unified_view_ray_god_ray_strength(",
        "float screen_space_shaft_strength(",
        "void apply_screen_space_shafts(",
        "void apply_unified_view_ray_god_rays(",
        "void apply_lighthouse_beam_shaft(",
        "int find_lighthouse_cone_record(",
        "LighthouseCone lighthouse_cone_from_record(",
        "SCREEN_SPACE_SHAFTS_ENABLED",
        old_beam_splat_image,
        "vapor_splat_depth_image",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_write_legacy_fact_surfaces() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "foliage_fact_glyph_image",
        "foliage_fact_terrain_image",
        "foliage_fact_foliage_image",
        "foliage_fact_foliage2_image",
        "void fl4184_write_foliage_facts(",
        "imageStore(foliage_fact_",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_terrain_resolve_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "AnsiResolve resolve_ansi_cell(",
        "void apply_baseline_plus_foliage(",
        "apply_baseline_plus_foliage(",
        "Resolve one ANSI cell's owning world cell",
    ]
    for token in forbidden:
        assert token not in src, token


def test_compositor_does_not_keep_dead_contact_shadow_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")

    forbidden = [
        "float baseline_plus_contact_shadow(",
        "float terrain_shadow_visibility(",
        "float terrain_shadow_visibility_single(",
        "actor_shadow_for_cell(",
        "_actor_shadow_",
        "baseline_feature_contact_shadow",
    ]
    for token in forbidden:
        assert token not in src, token

    for token in [
        # The capture decoder retains the historical role label
        # "contact_shadow" for old receipts. Only executable/runtime owners
        # are stale; the generic label is not an ownership assertion.
        "baseline_feature_contact_shadow",
        "probe_contact_shadow_",
    ]:
        assert token not in effect, token
        assert token not in world, token


def test_compositor_does_not_keep_dead_material_ramp_owner() -> None:
    src = SHADER.read_text(encoding="utf-8")

    forbidden = [
        "void apply_material_slope_value_contrast(",
        "void apply_material_ramp_steps(",
        "void apply_surface_stable_dither(",
        "bool apply_material_glyph_contrast(",
        "int yy_glyph_for_family(",
        "int extended_glyph_for_family(",
        "void fl4102_enforce_family_terrain_glyph(",
        "FL4102_BLOCKED_TERRAIN_GLYPHS",
        "bool baseline_feature_material_ramp_steps_enabled(",
        "bool baseline_feature_surface_stable_dither_enabled(",
        "bool baseline_feature_glyph_distinctness_enabled(",
        "MATERIAL_GLYPH_FAMILY_ENABLED",
        "MATERIAL_GLYPH_FAMILY_DEBUG_MARKERS",
    ]
    for token in forbidden:
        assert token not in src, token


if __name__ == "__main__":
    # Direct execution must collect this module through pytest and propagate
    # pytest's nonzero status. Without this runner, Python merely defined the
    # functions, executed zero assertions, and falsely appeared successful.
    raise SystemExit(pytest.main([__file__, "-q"]))
