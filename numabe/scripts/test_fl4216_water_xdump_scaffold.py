#!/usr/bin/env python3
"""Tests for the FL-4216 water X-dump proof runner scaffold.

Authority: docs/FAILURE_LOG.md entries FL-4216, FL-4129, and FL-4208.
These tests check scaffold mechanics only; they do not define water-lane
requirements, closure, acceptance, glyph policy, nor proof success.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import json
import importlib.util
import re
import subprocess
import sys
import tempfile
import struct
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "proofs" / "proof_fl4216_water_xdump.py"
COMPLETION_STATUS = ROOT / "scripts" / "fl4216_completion_status.py"
WATER_RECEIPTS = ROOT / "assets" / "glyphs" / "generated" / "fl4208_water_role_scoring.jsonl"
MATERIAL_MANIFEST = ROOT / "assets" / "glyphs" / "fixtures" / "extended_glyph_material_additive_v1.json"
GODOT_MATERIAL_MANIFEST = ROOT / "godot_project" / "assets" / "glyphs" / "fixtures" / "extended_glyph_material_additive_v1.json"
SHADER = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
GODOT_WATER_GLYPH_PAGE32 = ROOT / "godot_project" / "assets" / "glyphs" / "atlases" / "material.additive.v1.page32.png"
GODOT_MORPHOLOGY_GLYPH_PAGE32 = ROOT / "godot_project" / "assets" / "glyphs" / "atlases" / "material.morphology.v2.page32.png"

EXPECTED_WATER_SCALARS = {
    512: 0x3075,
    513: 0x308F,
    514: 0x3075,
    515: 0x308F,
    516: 0x3075,
    517: 0x3064,
    518: 0x308F,
    519: 0x3064,
}

EXPECTED_RECEIPT_FIELDS = {
        "glyph_id",
        "material_id",
        "material_family",
        "water_state",
    "water_state.inferred_from=shader_fact_readback",
    "fl4129.depth_fade",
    "fl4129.shore_foam",
    "fl4129.x13_sweep",
    "fl4129.direction_bucket",
    "fl4129.density_bucket",
    "fl4129.foam.role",
    "fl4129.foam.surface_bucket_source",
    "fl4129.foam.owner_side",
    "fl4129.foam.direction_bucket",
    "fl4129.foam.curl_bucket",
    "fl4129.foam.convexity_bucket",
    "fl4129.foam.density_bucket",
    "fl4129.foam.fallback_used",
    "fl4129.foam.extended_atlas_ok",
    "fl4129.foam.selected_glyph_id",
    "fl4129.foam.receipt_id",
    "fl4129.foam.font_hash",
    "fl4129.foam.rendered_bitmap_hash",
    "fl4129.foam.role_verified_at_codepoint",
    "fl4129.foam.fallback_glyph_id",
    "fl4129.reflection.strength",
    "fl4129.reflection.alpha",
    "fl4129.reflection.product_present",
    "fl4129.reflection.fallback_used",
    "fl4129.reflection.fallback_reason",
    "fl4129.wake.strength",
    "fl4129.pixelwater.refraction.displaced_uv_reverted",
    "fl4129.pixelwater.depth_color.depth_fade",
    "fl4129.pixelwater.fog.factor",
    "fl4129.pixelwater.detail.mask",
    "fl4129.pixelwater.crest.active",
    "fl4129.pixelwater.surface_foam.active",
    "fl4129.pixelwater.shore_foam.value",
    "fl4129.reflection.sky_sample_id",
    "fl4129.wake.actor_id",
    "fl4129.x13.wave_anim",
    "fl4129.x13.wave_pos",
    "fl4129.x13.wave_siz",
    "fl4129.x13.ws",
    "fl4129.x13.shade255",
    "fl4129.x13.matched_phase",
    "fl4129.x13.glyph_bucket_before",
    "fl4129.x13.glyph_bucket_after",
    "fl4129.actor_sink",
    "fl4129.actor_sink.actor_id",
    "fl4129.actor_sink.actor_row",
    "fl4129.actor_sink.actor_glyph_id",
    "fl4129.actor_sink.actor_glyph_id_above",
    "fl4129.actor_sink.water_glyph_id",
    "fl4129.actor_sink.water_palette_entry_id",
    "fl4129.actor_sink.sink_alpha",
    "fl4129.actor_sink.reflected_y_shift",
    "fl4129.actor_sink.pass_index",
}


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _completion_status_module() -> Any:
    spec = importlib.util.spec_from_file_location("fl4216_completion_status", COMPLETION_STATUS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proof_runner_module() -> Any:
    spec = importlib.util.spec_from_file_location("proof_fl4216_water_xdump", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _water_receipt_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in WATER_RECEIPTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _actor_sink_takeover_body(shader: str) -> str:
    marker = "void fl4216_apply_actor_sink_row_takeover("
    start = shader.index(marker, shader.index(marker) + 1)
    end = shader.index("\nint fl4129_water_activity_glyph", start)
    return shader[start:end]


def _function_body(shader: str, signature: str, end_marker: str) -> str:
    start = shader.index(signature)
    end = shader.index(end_marker, start)
    return shader[start:end]


def _shader_without_comments(source: str) -> str:
    """Remove GLSL prose so a stale explanation cannot satisfy a source pin."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _brace_function_body(source: str, signature: str) -> str:
    """Return one complete brace-balanced GLSL function."""
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for offset in range(opening, len(source)):
        token = source[offset]
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return source[start:offset + 1]
    raise AssertionError(f"unterminated GLSL function: {signature}")


def _current_water_owner_diagnostic_failures(product: str) -> list[str]:
    """Pin the activated retained-water owner and its observation-only receipt."""
    product = _shader_without_comments(product)
    failures: list[str] = []
    compile_input = _brace_function_body(
        product,
        "VsbProductCompileInput vsb_product_compile_input(",
    )
    prepare_water = _brace_function_body(
        product,
        "bool vsb_product_prepare_water(",
    )
    diagnostic = _brace_function_body(
        product,
        "ProbePayloadDiagnostic q3_probe_payload_diagnostic(",
    )
    product_main = _brace_function_body(
        product,
        "void main() {",
    )

    owner_rows = (
        "input_value.context.surface_kind =\n\t\tvsb_product_owner_kind(record.owner_kind);",
        "} else if (record.owner_kind == VSB_OWNER_WATER) {",
        "owner_valid = vsb_product_prepare_water(",
        "input_value.valid = true;",
        "input_value.facts.valid = true;",
    )
    for row in owner_rows:
        if row not in compile_input:
            failures.append(f"current retained-water handoff lost {row!r}")

    preparation_rows = (
        "record.object_id != product_vsb_frame.water_identity_flags.x",
        "record.material != product_vsb_frame.water_identity_flags.y",
        "bool backstop = local_primitive == cell_count;",
        "input_value.context.payload_from_backstop = true;",
        "input_value.facts.source_key = VSB_PRODUCT_BACKSTOP_SOURCE_KEY;",
        "vsb_product_selected_finite_water_facts =",
        "if (!vsb_product_selected_finite_water_facts.valid) return false;",
    )
    for row in preparation_rows:
        if row not in prepare_water:
            failures.append(f"current retained-water preparation lost {row!r}")

    diagnostic_rows = (
        "layout(set = 0, binding = 62, std430) buffer Q3ProbeSubcellDiagnostics",
        "layout(constant_id = 17) const bool PRODUCT_B62_DIAGNOSTICS_ENABLED = false;",
        "uint markers = facts.payload_diag_markers",
        "| (context.payload_from_backstop ? Q3_DIAG_MARKER_BACKSTOP : 0u);",
        "diagnostic.r0 = uvec4(facts.rekey_diag_reason, facts.rekey_diag_lattice_log2, final_kind, markers);",
    )
    for row in diagnostic_rows:
        search_area = product if row.startswith("layout") else diagnostic
        if row not in search_area:
            failures.append(f"current water diagnostic seam lost {row!r}")

    # A current water hit must reach the payload receipt and final Product
    # store. Binding 62 remains observation-only because every access is fenced
    # by the default-off specialization.
    main_rows = (
        "if (diagnostic_index_valid) {",
        "q3_probe_subcell_diagnostics.records[final_index].r0 =",
        "q3_probe_subcell_diagnostics.records[final_index].r1 =",
        "q3_probe_subcell_diagnostics.records[final_index].r2 =",
        "finalized_probe_subcell_products.products[final_index] =",
    )
    for row in main_rows:
        if row not in product_main:
            failures.append(f"current Product publication lost {row!r}")
    if product.count(
        "finalized_probe_subcell_products.products[final_index] ="
    ) != 1:
        failures.append("current Product no longer has exactly one final store")
    return failures


def test_fl4216_current_water_owner_has_typed_diagnostic_seam() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    failures = _current_water_owner_diagnostic_failures(product)
    assert not failures, failures

    # Both mutations remain valid GLSL. The first disconnects every VSB water
    # hit from its retained preparation owner. The second erases the backstop
    # marker while leaving the visible record untouched.
    owner_branch = "} else if (record.owner_kind == VSB_OWNER_WATER) {"
    assert product.count(owner_branch) == 1
    disconnected_owner = product.replace(
        owner_branch,
        "} else if (false && record.owner_kind == VSB_OWNER_WATER) {",
        1,
    )
    assert any(
        "current retained-water handoff lost" in failure
        for failure in _current_water_owner_diagnostic_failures(disconnected_owner)
    )

    marker_row = (
        "uint markers = facts.payload_diag_markers\n"
        "\t\t| (context.payload_from_backstop ? Q3_DIAG_MARKER_BACKSTOP : 0u);"
    )
    assert product.count(marker_row) == 1
    erased_backstop_marker = product.replace(
        marker_row,
        "uint markers = facts.payload_diag_markers;",
        1,
    )
    assert any(
        "current water diagnostic seam lost" in failure
        for failure in _current_water_owner_diagnostic_failures(erased_backstop_marker)
    )


def test_fl4216_non_splat_plane_admission_is_per_subsample() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    helper_body = _function_body(
        shader,
        "void fl4129_admit_water_subsample_by_plane(inout Sample s, ivec2 sample_pos, ivec2 size) {",
        "\n}\n\nbool raw_sprite_band_at",
    )
    assert "vec2 uv = (vec2(sample_pos) + 0.5) / vec2(size);" in helper_body
    assert "terrain_cell_is_visible_water_owner(plane_cell)" in helper_body
    assert "s.is_water = true;" in helper_body
    assert "s.is_terrain = false;" in helper_body
    main_plane_start = shader.index("// FL-4129 single compositor-owned water coverage predicate, input (a):")
    main_plane_end = shader.index("\n\t// Slice 4a", main_plane_start)
    main_plane_body = shader[main_plane_start:main_plane_end]
    assert "fl4129_admit_water_subsample_by_plane(s0, sp0, size);" in main_plane_body
    assert "fl4129_admit_water_subsample_by_plane(s1, sp1, size);" in main_plane_body
    assert "fl4129_admit_water_subsample_by_plane(s2, sp2, size);" in main_plane_body
    assert "fl4129_admit_water_subsample_by_plane(s3, sp3, size);" in main_plane_body
    assert "cell_center_px" not in main_plane_body
    assert "plane_cell_c" not in main_plane_body
    assert "if (!s0.is_water && s0.is_terrain)" not in main_plane_body


def test_fl4216_water_owner_call_sites_use_plane_depth_split() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    packed_fact_body = _function_body(
        shader,
        "if (visible_water_owner && baseline_feature_water_current_package_enabled()) {",
        "\n\t// FL-4208 / step #10: pack the 10 runtime morphology scores",
    )
    assert "float fact_pack_depth_below = terrain_depth_below_plane(cell);" in packed_fact_body
    assert "float fact_pack_terrain_y_for_water = WATER_SURFACE_Y - fact_pack_depth_below;" in packed_fact_body
    assert "fact_pack_terrain_y_for_water" in packed_fact_body
    assert "cell_min_height(cell)" not in packed_fact_body

    fact_body = _function_body(
        shader,
        "if (screen_sub_x == 0 && screen_sub_y == 0 && splat_enabled) {",
        "\n\tvec3 actor_cell_color;",
    )
    assert "float fact_depth_below = terrain_depth_below_plane(fact_cell);" in fact_body
    assert "float fact_terrain_y_for_water = WATER_SURFACE_Y - fact_depth_below;" in fact_body
    assert "cell_avg_height(fact_cell)" not in fact_body
    assert "fact_terrain_y_for_water" in fact_body

    splat_body = _function_body(
        shader,
        "bool water_owner_under_cell = fl4129_visible_water_owner_cell(cell);",
        "\n\t\t\t\tapply_terrain_spawn_cross_marker(cell, r);",
    )
    assert "float terrain_depth_below = terrain_depth_below_plane(cell);" in shader
    assert "float terrain_y_for_water = WATER_SURFACE_Y - terrain_depth_below;" in shader
    assert "terrain_y_for_water" in splat_body
    assert "terrain_y_for_depth" not in splat_body

    mixed_start = shader.index("ivec2 mix_water_cell = world_xz_to_terrain_cell(wp_sp.xz);")
    mixed_body = shader[mixed_start:mixed_start + 700]
    assert "if (fl4129_visible_water_owner_cell(mix_water_cell)) {" in mixed_body
    assert "float mix_terrain_y_for_water = WATER_SURFACE_Y - mix_water_depth;" in mixed_body
    assert "apply_fl4129_water_surface_owner(mix_r, wp_sp.xz, mix_terrain_y_for_water" in mixed_body
    assert "apply_fl4129_water_surface_owner(mix_r, wp_sp.xz, wp_sp.y" not in mixed_body
    assert mixed_body.index("if (fl4129_visible_water_owner_cell(mix_water_cell)) {") < mixed_body.index("apply_fl4129_water_surface_owner(")


def test_fl4216_visible_sprite_submerge_uses_sprite_pixel_y() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    start = shader.index("// FL-4129 actor_submerge — visible Sprite3D path.")
    end = shader.index("apply_baseline_compositor_weather_layers(", start)
    body = shader[start:end]

    assert "vec3 wp_p = wh_p.xyz / wh_p.w;" in body
    assert "float depth_under = WATER_SURFACE_Y - wp_p.y;" in body
    assert "apply_water_surface_overlay(buried, buried_world.xz, wp_p.y" in body
    assert "apply_water_surface_overlay(buried, buried_world.xz, cell_avg_height(weather_cell)" not in body
    assert "void apply_water_surface_overlay(inout AnsiResolve r, vec2 world_xz, float terrain_y, vec2 screen_uv, float time)" in shader
    assert "apply_fl4129_water_surface_owner(r, world_xz, terrain_y, true, screen_uv, time);" in shader
    assert "apply_fl4129_water_surface_owner(r, world_xz, terrain_y, true, vec2(0.0), time);" not in shader
    assert "apply_water_surface_overlay(buried, buried_world.xz, wp_p.y, uv_p, params.feature_params.x);" in body


def test_fl4216_weather_ripples_use_water_owner_gate() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    start = shader.index("// (i) t3ssel8r #5 water ripples")
    end = shader.index("// (j) Ground splashes.", start)
    body = shader[start:end]

    assert "if (terrain_cell_is_visible_water_owner(weather_cell)) {" in body
    assert "cell_min_height(weather_cell) < WATER_SURFACE_Y" not in body


def test_fl4216_water_boundary_uses_plane_owner_not_raw_sample_only() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    body = _function_body(
        shader,
        "bool raw_compositor_water_at(ivec2 p, ivec2 size) {",
        "\n}\n\nbool raw_terrain_band_at",
    )

    assert "Sample s = fetch_sample(p, size);" in body
    assert "if (s.is_water) return true;" in body
    assert "if (!baseline_feature_water_current_package_enabled()) return false;" in body
    assert "if (!s.is_terrain) return false;" in body
    assert "params.inv_view_proj * ndc" in body
    assert "terrain_cell_is_visible_water_owner(world_xz_to_terrain_cell(wp.xz))" in body
    assert "return fetch_sample(p, size).is_water;" not in body


def test_fl4216_pure_water_keeps_owner_error_colors() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    start = shader.index("// FL-4129 Attempt 52: pure-water single-owner overlay.")
    end = shader.index("ivec2 pw_ansi_xy = pos / int(ANSI_CELL_SIZE);", start)
    body = shader[start:end]

    assert "bool pure_visible_water_owner = water_world_valid && fl4129_visible_water_owner_cell(pure_water_cell);" in body
    assert "if (fl4129_pure_owner_active && planar_water_valid && !pure_visible_water_owner) {" in body
    assert "glyph = FL4208_WATER_ERROR_GLYPH;" in body
    assert "bg = vec3(0.0);" in body
    assert "fg = vec3(1.0, 0.0, 0.0);" in body
    assert "water_world_valid && planar_water_valid && pure_visible_water_owner" not in body
    assert "apply_fl4129_water_surface_owner(pw_r, water_world_xz" in body
    assert "bg = pw_r.bg;" in body
    assert "fg = pw_r.fg;" in body
    assert "glyph = pw_r.glyph;" in body
    assert "pw_r.bg = mix(WATER_PALETTE_SHALLOW_BG" not in body
    assert "pw_r.fg = WATER_PALETTE_FG;" not in body


def test_fl4216_all_water_owner_calls_keep_owner_colors() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    fact_start = shader.index("if (fact_water_owner) {")
    fact_end = shader.index("fl4184_write_foliage_facts(fact_ansi_xy", fact_start)
    fact_body = shader[fact_start:fact_end]
    visible_start = shader.index("if (water_owner_under_cell) {")
    visible_end = shader.index("apply_terrain_spawn_cross_marker(cell, r);", visible_start)
    visible_body = shader[visible_start:visible_end]
    final_start = shader.index("apply_baseline_compositor_weather_layers(pos, ansi_xy")
    final_end = shader.index("imageStore(color_image, pos, vec4(color, 1.0));", final_start)
    final_body = shader[final_start:final_end]
    mixed_start = shader.index("// FL-4129 Attempt 52: mixed-shore overlay call site.")
    mixed_end = shader.index("ivec2 sub_sp = pos - ivec2(cell_x * cell_w_px", mixed_start)
    mixed_body = shader[mixed_start:mixed_end]

    for body in (fact_body, visible_body, mixed_body):
        assert "apply_fl4129_water_surface_owner(" in body
        assert "WATER_PALETTE_FG" not in body
        assert "WATER_PALETTE_SHALLOW_BG" not in body
    assert "water_bg_final" not in final_body
    assert "water_fg_final" not in final_body
    assert "color = mix(water_bg_final" not in final_body


def test_fl4216_shore_edge_neighbors_use_strict_water_owner() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    proximity_body = _function_body(
        shader,
        "float shore_proximity_signal(ivec2 cell) {",
        "\n}\n\n// FL-4216 Step 3: submerge depth owner",
    )
    assert "float shore_dist255 = water_distance_to_shore(cell);" in proximity_body
    assert "water_nearest_land_distance_cells(cell)" not in proximity_body
    body = _function_body(
        shader,
        "bool terrain_cell_is_hard_water_edge_neighbor(ivec2 cell) {",
        "\n}\n\nint terrain_shore_immediate_water_mask",
    )
    assert "terrain_cell_is_visible_water_owner(cell)" in body
    assert "cell_max_height(cell) < WATER_SURFACE_Y" not in body
    assert "cell_avg_height(cell) < WATER_SURFACE_Y" not in body
    assert "is_visible_water_mat(splat_mat_at(cell))" not in body


def test_fl4208_water_shore_direction_morphology_is_owned_by_land_side() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    land_edge_body = _function_body(
        shader,
        "float water_land_neighbor_edge(ivec2 cell) {",
        "\n}\n\nint water_x13_land_neighbor_mask",
    )
    assert "terrain_cell_is_visible_land_owner(cell)" in land_edge_body
    assert "cell_max_height(cell) >= WATER_SURFACE_Y" not in land_edge_body
    assert "cell_avg_height(cell) >= WATER_SURFACE_Y" not in land_edge_body
    body = _function_body(
        shader,
        "int water_x13_shore_direction_bucket(int neighbor_mask, float pulse) {",
        "\n}\n\nfloat water_nearest_land_distance_cells",
    )
    assert "if (neighbor_mask == 0) {" in body
    assert "return FL4129_WATER_DIR_HORIZONTAL;" in body
    assert "FL4129_WATER_DIR_VERTICAL" not in body
    assert "if ((neighbor_mask & 1) != 0 && (neighbor_mask & 2) == 0) return FL4129_WATER_DIR_CURL_LEFT;" in body
    assert "if ((neighbor_mask & 2) != 0 && (neighbor_mask & 1) == 0) return FL4129_WATER_DIR_CURL_RIGHT;" in body
    assert "if ((neighbor_mask & 1) != 0 && (neighbor_mask & 2) == 0) return FL4129_WATER_DIR_LEFT;" in body
    assert "if ((neighbor_mask & 2) != 0 && (neighbor_mask & 1) == 0) return FL4129_WATER_DIR_RIGHT;" in body
    assert "if ((neighbor_mask & 4) != 0 && (neighbor_mask & 8) == 0) return FL4129_WATER_DIR_CURL_LEFT;" in body
    assert "if ((neighbor_mask & 8) != 0 && (neighbor_mask & 4) == 0) return FL4129_WATER_DIR_CURL_RIGHT;" in body
    assert "FL4129_WATER_DIR_CURL_LEFT : FL4129_WATER_DIR_RIGHT" not in body
    assert "FL4129_WATER_DIR_CURL_RIGHT : FL4129_WATER_DIR_LEFT" not in body
    assert "pulse >" not in body
    direction_body = _function_body(
        shader,
        "int fl4216_water_direction_bucket_for_cell(Fl4129WaterSample s, float x13_sweep, ivec2 water_cell) {",
        "\n}\n\nint fl4216_water_density_bucket",
    )
    assert "int fl4216_water_direction_bucket(" not in shader
    assert "water_x13_nearest_land_direction_mask" not in shader
    assert "s.wave_height >" not in direction_body
    assert "x13_sweep >" not in direction_body
    assert "((water_cell.x + water_cell.y) & 1)" not in direction_body
    assert "if (shore_mask == 0) {\n\t\t\treturn FL4129_WATER_DIR_HORIZONTAL;\n\t\t}" in direction_body


def test_fl4208_water_xdump_atlas_ok_is_not_range_only() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    body = _function_body(
        effect,
        "func _fl4208_water_extended_atlas_ok(glyph_id: Variant) -> bool:",
        "\nfunc _ensure_water_role_receipts_loaded",
    )
    assert "if not extended_glyph_rd.is_valid():" in body
    assert "return not _fl4208_water_receipt_for_glyph(gid).is_empty()" in body
    assert "return gid >= FL4208_WATER_EXTENDED_GLYPH_MIN and gid <= FL4208_WATER_EXTENDED_GLYPH_MAX" not in body


def test_fl4208_water_slots_are_receipt_backed_and_godot_synced() -> None:
    root_manifest = _json(MATERIAL_MANIFEST)
    godot_manifest = _json(GODOT_MATERIAL_MANIFEST)
    assert root_manifest == godot_manifest

    entries = {
        int(entry["glyph_id"]): int(entry["unicode_scalar"])
        for entry in root_manifest["entries"]
        if 512 <= int(entry["glyph_id"]) <= 519
    }
    assert entries == EXPECTED_WATER_SCALARS

    rows = _water_receipt_rows()
    assert len(rows) == 8
    by_gid = {int(row["extended_glyph"]): row for row in rows}
    assert set(by_gid) == set(EXPECTED_WATER_SCALARS)
    for glyph_id, scalar in EXPECTED_WATER_SCALARS.items():
        row = by_gid[glyph_id]
        assert row["verified"] is True
        assert int(row["receipt_id"]) != 0
        assert int(row["font_hash"]) != 0
        assert int(row["rendered_bitmap_hash"]) != 0
        assert int(row["role_verified_at_codepoint"]) == scalar
        assert int(row["fallback_glyph"]) == 33

    shader = SHADER.read_text(encoding="utf-8")
    assert "FL4129_WATER_GLYPH_BUCKETS" not in shader
    assert "FL4129_WATER_DIR_VERTICAL" not in shader
    assert "apply_splat_owned_shoreline_edge" not in shader
    assert "water_x13_shore_glyph" not in shader
    assert "bool fl4208_water_extended_atlas_runtime_ready()" in shader
    assert "return (params.misc.w & 16) != 0;" in shader
    assert "Fl4208GlyphBucket(0.78" in shader
    assert not re.search(r"Fl4208GlyphBucket\([^)]*FL4129_WATER_DIR_NONE", shader)
    for fallback_name in [
        "CALM",
        "RIPPLE",
        "WAVE",
        "LEFT",
        "RIGHT",
        "SPECKLE",
        "LIGHT",
        "HEAVY",
        "VERTICAL",
    ]:
        assert f"const int FL4129_WATER_FALLBACK_{fallback_name}" in shader
        assert f"FL4129_WATER_FALLBACK_{fallback_name} = FL4208_WATER_ERROR_GLYPH" in shader
    for row in rows:
        expected = (
            f"{int(row['receipt_id'])}, {int(row['font_hash'])}, "
            f"{int(row['rendered_bitmap_hash'])}"
        )
        assert expected in shader
    receipt_body = _function_body(
        shader,
        "bool fl4208_water_bucket_receipt_ready(Fl4208GlyphBucket b) {",
        "\n}\n\nbool fl4208_water_error_glyph",
    )
    assert "fl4208_water_extended_atlas_runtime_ready()" in receipt_body

    effect = EFFECT.read_text(encoding="utf-8")
    assert "static func _fl4216_water_curl_label" in effect
    assert "func _fl4208_water_extended_atlas_runtime_ready() -> bool:" in effect
    assert "if baseline_feature_water_current_package and _fl4208_water_extended_atlas_runtime_ready():" in effect
    assert "terrain_flags |= 16" in effect
    assert '"foam_curl_bucket": _fl4216_water_curl_label(fact_row.get("fl4129_direction_bucket", null))' in effect
    assert '"fl4129.foam.curl_bucket": _fl4216_water_curl_label(fact_row.get("fl4129_direction_bucket", null))' in effect
    assert '"fl4129.foam.curl_bucket": _fl4216_water_direction_label' not in effect
    assert "_WATER_ROLE_SCORING_REPO_RELPATH" in effect
    assert "_fl4208_water_receipt_for_glyph" in effect
    assert '"fl4129.foam.receipt_id"' in effect
    assert '"fl4129.foam.font_hash"' in effect
    assert '"fl4129.foam.rendered_bitmap_hash"' in effect
    assert '"fl4129.foam.role_verified_at_codepoint"' in effect
    assert '"fl4129.foam.surface_bucket_source"' in effect
    assert '"fl4129.reflection.sky_sample_id"' in effect
    assert '"fl4129.wake.actor_id"' in effect
    wake_helper = _function_body(
        effect,
        "static func _fl4216_water_wake_actor_id(_surface_bucket: Variant, wake_strength: Variant) -> Variant:",
        "\n\nstatic func _fl4216_water_owner_side_label",
    )
    assert "_fl4216_water_surface_label(surface_bucket)" not in wake_helper
    assert "var wake_u8 := clampi(int(float(wake_strength) * 255.0 + 0.5), 0, 255)" in wake_helper
    assert "if wake_u8 <= 0:" in wake_helper


def test_fl4208_water_slots_are_not_used_by_non_water_terrain_families() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    body = _function_body(
        shader,
        "int extended_glyph_for_family(int family, int elv, uint hash_bits) {",
        "\n}\n\nvoid apply_material_ramp_steps",
    )
    for glyph_id in range(512, 520):
        assert str(glyph_id) not in body


def test_fl4216_actor_sink_uses_water_wake_and_reflection_owner() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    body = _actor_sink_takeover_body(shader)
    visible_sink_start = shader.index("Fl4129WaterSample sink_ws = fl4129_water_compute_sample(")
    visible_sink_body = shader[visible_sink_start - 300:visible_sink_start + 300]
    assert "float sink_depth_below = terrain_depth_below_plane(cell);" in visible_sink_body
    assert "float sink_terrain_y_for_water = WATER_SURFACE_Y - sink_depth_below;" in visible_sink_body
    assert "sink_terrain_y_for_water" in visible_sink_body
    assert "cell_min_height(cell)" not in visible_sink_body
    assert "vec3 sink_bg = out_bg;" in body
    assert "fl4129_water_activity_ink(sink_bg, s, 0.0, water_glyph)" in body
    assert "fl4129_water_apply_x13_terrain_reflection(sink_fg, s, screen_uv)" in body
    assert "fl4129_water_apply_wake(sink_fg, s)" in body
    assert "water_owner.shallow_color.rgb" not in body
    assert "water_owner.deep_color.rgb" not in body
    assert "s.actor_sink_water_palette_entry_id = fl4208_water_palette_entry_id_for_glyph(water_glyph)" in body
    assert "actor_sink_water_palette_entry_id = fl4208_water_surface_bucket" not in body
    assert "s.reflection_product_present ? 1 : 0" not in body
    assert "s.wake > 0.0 ? 1 : 0" not in body
    sprite_body = _function_body(
        shader,
        "vec3 fl4129_water_apply_x13_sprite_sink(vec3 base, Fl4129WaterSample s) {",
        "\n}\n\nfloat fl4129_water_x13_four_phase_sweep",
    )
    assert "mix(base, water_owner.deep_color.rgb" not in sprite_body
    assert "vec3 sink_color = base;" in sprite_body
    assert "fl4129_water_apply_x13_terrain_reflection(sink_ink, s, s.refracted_screen_uv)" in sprite_body
    assert "fl4129_water_apply_wake(sink_ink, s)" in sprite_body
    assert "water_owner.shallow_color.rgb" not in sprite_body
    assert "water_owner.deep_color.rgb" not in sprite_body
    assert "fl4129_water_foam_glyph(" in sprite_body
    assert "fl4129_water_activity_ink(" in sprite_body
    assert "actor_sink_reflected_y_shift" in shader


def test_fl4216_water_owner_preserves_baseline_a_fg_bg_colors() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    body = _function_body(
        shader,
        "void apply_fl4129_water_surface_owner(inout AnsiResolve r, vec2 world_xz, float terrain_y, bool sprite_owned, vec2 screen_uv, float time) {",
        "\n}\n\nfloat world_cloud_density",
    )
    assert "vec3 baseline_bg = r.bg;" in body
    assert "vec3 baseline_fg = r.fg;" in body
    assert "r.bg = baseline_bg;" in body
    assert "fl4129_water_apply_depth_color(r.bg" not in body
    assert "fl4129_water_apply_fog(r.bg" not in body
    assert "fl4129_water_apply_depth_color(r.fg" not in body
    assert "fl4129_water_apply_fog(r.fg" not in body
    assert "fl4129_water_preserve_blue_identity(" not in body
    assert "fl4129_water_apply_final_blue_identity(r, s)" not in body


def test_fl4216_x13_visible_sweep_uses_proof_phase_fields() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    body = _function_body(
        shader,
        "float fl4129_water_x13_four_phase_sweep(Fl4129WaterSample s, float time) {",
        "\n}\n\n// FL-4129 Attempt 52",
    )
    assert "s.x13_matched_phase" in body
    assert "s.x13_wave_siz[phase]" in body
    assert "s.x13_ws[phase]" in body
    assert "s.x13_glyph_bucket_after > s.x13_glyph_bucket_before" in body
    assert "water_x13_reflection_wave_band(" not in body
    assert "terrain_wave_shade_255(" not in body
    assert "water_distance_to_shore(" not in body


def test_fl4216_actor_sink_deletes_legacy_tint_owners() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    for deleted_helper in [
        "water_x13_reflection_wave_band(",
        "water_submerge_tint(",
        "water_plane_overlay(",
        "water_explicit_pattern_field(",
    ]:
        assert deleted_helper not in shader
    body = _function_body(
        shader,
        "vec3 fl4129_actor_apply_reflected_sunk_paint(",
        "\n}\n\nbool resolve_actor_cell_pixel",
    )
    assert "fl4216_apply_actor_sink_row_takeover(" in body
    assert "vec3 sunk = mix(actor_color, water_bg" in body
    assert "(waterline_alpha > 0.26) ? 0x2A" not in body
    assert "int   water_glyph = takeover_glyph;" in body
    assert "water_submerge_tint" not in body
    assert "float object_y = cell_min_height(actor_terrain_cell);" not in body
    assert "float object_y = terrain_height_at_world_xz(world_uv);" in body
    assert "float actor_depth = texture(depth_tex, screen_uv).r;" in body
    assert "params.inv_view_proj * ndc" in body
    water_state_body = _function_body(
        effect,
        "\t\t\t\tshader_water_state = {",
        "\n\t\t\t\t}\n\t\t\tvar water_state: Variant = shader_water_state",
    )
    assert '"actor_sink_reflected_y_shift": fact_row.get("fl4129_actor_sink_reflected_y_shift", null)' in water_state_body


def test_fl4216_reflection_fails_closed_without_x13_product() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    body = _function_body(
        shader,
        "vec3 fl4129_water_apply_x13_terrain_reflection(vec3 base, Fl4129WaterSample s, vec2 screen_uv) {",
        "\n}\n\n// X13 sprite reflection + sink",
    )
    assert "imageLoad(fl4129_reflected_terrain_image" in body
    assert "imageLoad(water_owner_snapshot_image" not in body
    assert "return base;" in body
    assert "product_depth > 0.0" in body
    assert "product_weight = 0.22" not in body


def test_fl4216_reflected_terrain_product_marks_above_plane_source() -> None:
    producer = (ROOT / "godot_project/shaders/fl4129_reflected_terrain_product.glsl").read_text(encoding="utf-8")
    assert "reflected_height = max(world.y - params.water_params.x, 0.0);" in producer
    assert "params.water_params.x - world.y" not in producer
    assert "positive values are world units above WATER_SURFACE_Y" in producer


def test_fl4216_reflected_terrain_product_clears_before_dispatch() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    clear_body = _function_body(
        effect,
        "func _clear_fl4129_reflected_terrain_product() -> void:",
        "\nfunc _dispatch_fl4129_reflected_terrain_product",
    )
    assert "rd.texture_clear(_fl4129_reflected_terrain_rd, Color(0, 0, 0, 0), 0, 1, 0, 1)" in clear_body
    assert "rd.texture_clear(_fl4129_reflected_terrain_depth_rd, Color(0, 0, 0, 0), 0, 1, 0, 1)" in clear_body
    dispatch_body = _function_body(
        effect,
        "func _dispatch_fl4129_reflected_terrain_product(fb_size: Vector2i, color_image: RID, depth_image: RID, inv_view_proj: Projection) -> void:",
        "\nfunc _dispatch_style_post",
    )
    clear_idx = dispatch_body.index("_clear_fl4129_reflected_terrain_product()")
    pipeline_idx = dispatch_body.index("if not fl4129_reflected_terrain_pipeline.is_valid():")
    input_idx = dispatch_body.index("if not color_image.is_valid()")
    resource_idx = dispatch_body.index("if not _fl4129_reflected_terrain_rd.is_valid()")
    assert clear_idx < pipeline_idx
    assert clear_idx < input_idx
    assert clear_idx < resource_idx


def test_fl4216_reflected_terrain_product_textures_support_clear() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    allocation_body = _function_body(
        effect,
        "if _fl4129_reflected_terrain_size != image_size or not _fl4129_reflected_terrain_rd.is_valid() or not _fl4129_reflected_terrain_depth_rd.is_valid():",
        "\nfunc _write_water_owner_params_defaults",
    )
    color_fmt_start = allocation_body.index("var refl_fmt := RDTextureFormat.new()")
    depth_fmt_start = allocation_body.index("var refl_depth_fmt := RDTextureFormat.new()")
    color_fmt_body = allocation_body[color_fmt_start:depth_fmt_start]
    depth_fmt_body = allocation_body[depth_fmt_start:]
    assert "RenderingDevice.TEXTURE_USAGE_CAN_COPY_TO_BIT" in color_fmt_body
    assert "RenderingDevice.TEXTURE_USAGE_CAN_COPY_TO_BIT" in depth_fmt_body


def test_fl4216_pixelwater_stage_facts_are_packed_and_dumped() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    pack_body = _function_body(
        shader,
        "if (visible_water_owner && baseline_feature_water_current_package_enabled()) {",
        "\n\t}\n\t// FL-4208 / step #10: pack the 10 runtime morphology scores",
    )
    assert "fact_ws.displaced_uv_reverted ? 1u : 0u) << 20u" in pack_body
    assert "fact_ws.fog_factor * 15.0" in pack_body
    assert "<< 21u" in pack_body
    assert "fact_ws.detail_mask * 15.0" in pack_body
    assert "<< 25u" in pack_body
    assert "fact_ws.crest > 0.0" in pack_body
    assert "<< 29u" in pack_body
    assert "fact_ws.surface_foam > 0.0" in pack_body
    assert "<< 30u" in pack_body

    effect = EFFECT.read_text(encoding="utf-8")
    for expected in [
        '"fl4129.pixelwater.refraction.displaced_uv_reverted"',
        '"fl4129.pixelwater.depth_color.depth_fade"',
        '"fl4129.pixelwater.fog.factor"',
        '"fl4129.pixelwater.detail.mask"',
        '"fl4129.pixelwater.crest.active"',
        '"fl4129.pixelwater.surface_foam.active"',
        '"fl4129.pixelwater.shore_foam.value"',
    ]:
        assert expected in effect


def test_fl4216_xdump_writer_has_no_cpu_inferred_water_state() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    assert '_final_dump_water_state' not in effect
    assert '_final_dump_inferred_role' not in effect
    assert 'cpu_final_image_avg_rgb' not in effect
    assert 'var water_state: Variant = shader_water_state' in effect
    assert 'water_state = _final_dump_water_state' not in effect


def test_fl4216_water_surface_bucket_uses_packed_wake_provenance() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    helper = _function_body(
        shader,
        "int fl4216_water_wake_actor_sample_id(float wake) {",
        "\n}\n\n// Canonical sample carrying PixelWater",
    )
    assert "int wake_u8 = clamp(int(wake * 255.0 + 0.5), 0, 255);" in helper
    assert "return wake_u8 > 0 ? 1 : 0;" in helper
    assert "fact_ws.wake > 0.0 ? 1 : 0" not in shader
    assert "s.wake > 0.0 ? 1 : 0" not in shader
    assert "fl4216_water_wake_actor_sample_id(fact_ws.wake)" in shader
    bucket_body = _function_body(
        shader,
        "int fl4208_water_surface_bucket(",
        "\n}\n\nint fl4216_water_wake_actor_sample_id",
    )
    assert "if (wake_actor_id != 0)            return FL4208_SURFACE_WAKE;" in bucket_body
    assert bucket_body.index("FL4208_SURFACE_WAKE") < bucket_body.index("FL4208_SURFACE_REFLECTION")


def test_fl4208_water_role_lookup_is_density_ordered_not_exact_fallthrough() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    start = shader.index("int fl4129_water_foam_glyph(")
    end = shader.index("\n// FL-4216 Step 8: surface bucket resolver", start)
    body = shader[start:end]
    assert "if (owner_side == FL4216_OWNER_SIDE_LAND)" in body
    assert "return FL4208_WATER_ERROR_GLYPH;" in body
    assert "chosen_score" in body
    assert "abs(float(b.density_bucket - density_bucket))" in body
    assert "abs(float(b.convexity_bucket - convexity_bucket))" in body
    assert "if (b.density_bucket != density_bucket) continue" not in body
    assert "if (b.convexity_bucket != convexity_bucket) continue" not in body
    assert "if (foam < b.min_activity) continue" not in body
    assert "Direction is the hard owner" in body
    assert "valid directional shore cell cannot become an error marker" in body


def test_fl4216_dormant_legacy_water_side_owners_are_deleted() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    for deleted_owner in [
        "baseline_feature_water_shore_wave_enabled",
        "SHORELINE_FOAM_CONSUMER_ENABLED",
        "SHORELINE_FOAM_COLOR",
        "!fl4129_pure_owner_active",
    ]:
        assert deleted_owner not in shader
    pure_branch = _function_body(
        shader,
        "} else if (water_count >= 1) {",
        "\n\t\t} else {\n\t\t// Sky / clear fallback.",
    )
    assert "apply_fl4129_water_surface_owner(pw_r, water_world_xz" in pure_branch
    assert "glyph = 0x27" not in pure_branch
    assert "glyph = 0x2A" not in pure_branch
    ink_body = _function_body(
        shader,
        "vec3 fl4129_water_activity_ink(vec3 bg, Fl4129WaterSample s, float x13_sweep, int glyph) {",
        "\n}\n\n// PixelWater depth-color",
    )
    assert "glyph == 0x2A" not in ink_body
    assert "glyph == 0x2F" not in ink_body
    assert "glyph == 0x2C" not in ink_body
    assert "glyph == 0x2E" not in ink_body
    assert "glyph == 0x7E" not in ink_body
    assert "glyph >= 512 && glyph <= 519" in ink_body
    assert "glyph == 518 || glyph == 519" in ink_body


def test_fl4208_water_runtime_uses_page32_extended_glyph_atlas() -> None:
    world = WORLD.read_text(encoding="utf-8")
    assert 'EXTENDED_GLYPH_ATLAS_PATH := "res://assets/glyphs/atlases/material.additive.v1.page32.png"' in world
    assert 'MORPHOLOGY_GLYPH_ATLAS_PATH := "res://assets/glyphs/atlases/material.morphology.v2.page32.png"' in world
    assert "material.additive.v1.page16.png" not in world
    assert GODOT_WATER_GLYPH_PAGE32.exists()
    assert GODOT_MORPHOLOGY_GLYPH_PAGE32.exists()
    data = GODOT_WATER_GLYPH_PAGE32.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (512, 320)


def test_fl4216_source_identity_tracks_runtime_water_atlas_files() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    assert '"godot_project/scripts/World.gd"' in runner
    assert '"godot_project/shaders/fl4129_reflected_terrain_product.glsl"' in runner
    assert '"godot_project/assets/glyphs/atlases/material.additive.v1.page32.png"' in runner
    assert '"godot_project/assets/glyphs/atlases/material.morphology.v2.page32.png"' in runner
    assert '"assets/glyphs/generated/fl4208_water_role_scoring.jsonl"' in runner
    assert '"godot_project/assets/glyphs/generated/fl4208_water_role_scoring.jsonl"' not in runner


def test_fl4216_source_identity_excludes_docs_and_scaffold_tests() -> None:
    runner = _proof_runner_module()
    assert runner._is_water_relevant_dirty("godot_project/render/ascii/raster/ascii_compositor.glsl") is True
    assert runner._is_water_runtime_relevant_dirty("godot_project/shaders/fl4129_reflected_terrain_product.glsl") is True
    assert runner._is_water_relevant_dirty("godot_project/render/api/ASCIICompositorEffect.gd") is True
    assert runner._is_water_runtime_relevant_dirty("scripts/proofs/proof_fl4216_water_xdump.py") is False
    assert runner._is_water_proof_tool_relevant_dirty("scripts/proofs/proof_fl4216_water_xdump.py") is True
    assert runner._is_water_proof_tool_relevant_dirty("scripts/validate_godot_water_mechanics.py") is False
    assert runner._is_water_proof_tool_relevant_dirty("scripts/validate_fl4208_glyph_contract.py") is False
    assert runner._is_water_runtime_relevant_dirty("assets/glyphs/generated/fl4208_water_role_scoring.jsonl") is True
    assert runner._is_water_runtime_relevant_dirty("godot_project/assets/glyphs/generated/fl4208_water_role_scoring.jsonl") is False
    assert runner._is_water_relevant_dirty("docs/FAILURE_LOG.md") is False
    assert runner._is_water_relevant_dirty("scripts/test_fl4216_water_xdump_scaffold.py") is False


def test_fl4216_shader_dirty_relevance_is_water_hunk_scoped() -> None:
    runner = _proof_runner_module()
    original = runner._diff_changed_lines
    try:
        runner._diff_changed_lines = lambda path: [
            "const int   FOLIAGE_SLOTS_PER_GRASS_CELL = 4;",
            "float authored_value = clamp(0.70 + asset_luma * 0.32, 0.62, 1.08);",
        ]
        assert runner._is_water_runtime_relevant_dirty("godot_project/render/ascii/raster/ascii_compositor.glsl") is False
        runner._diff_changed_lines = lambda path: [
            "return fl4216_water_wake_actor_sample_id(fact_ws.wake);",
        ]
        assert runner._is_water_runtime_relevant_dirty("godot_project/render/ascii/raster/ascii_compositor.glsl") is True
    finally:
        runner._diff_changed_lines = original


def test_fl4216_dump_source_hash_mismatch_reports_water_file_drift() -> None:
    runner = _proof_runner_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / ".run" / "baseline_compositor_ab" / "current_source" / "deadbeef"
        dump = root / ".run" / "final_render_cell_dump" / "sample"
        dump.mkdir(parents=True)
        rel_path = "scripts/test_fl4216_water_xdump_scaffold.py"
        frozen_file = root / rel_path
        frozen_file.parent.mkdir(parents=True)
        frozen_file.write_text("frozen proof scaffold\n", encoding="utf-8")
        mismatches = runner._source_file_hash_mismatches(dump, (rel_path,))
        assert any(item["path"] == rel_path for item in mismatches)


def test_fl4216_dump_source_hash_mismatch_reports_missing_runtime_file() -> None:
    runner = _proof_runner_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / ".run" / "baseline_compositor_ab" / "current_source" / "deadbeef"
        dump = root / ".run" / "final_render_cell_dump" / "sample"
        dump.mkdir(parents=True)
        rel_path = "godot_project/shaders/fl4129_reflected_terrain_product.glsl"
        mismatches = runner._source_file_hash_mismatches(dump, (rel_path,))
        assert any(
            item["path"] == rel_path
            and item["dump_sha256"] == "missing"
            and item["live_sha256"] != "missing"
            for item in mismatches
        )


def test_fl4216_shader_source_drift_classifies_water_owned_lines() -> None:
    runner = _proof_runner_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / ".run" / "baseline_compositor_ab" / "current_source" / "deadbeef"
        dump = root / ".run" / "final_render_cell_dump" / "sample"
        dump.mkdir(parents=True)
        frozen_shader = root / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
        frozen_shader.parent.mkdir(parents=True)
        live_shader = (ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl").read_text(encoding="utf-8")
        frozen_text = live_shader.replace("fl4216_water_wake_actor_sample_id", "old_wake_actor_sample_id", 1)
        frozen_text = frozen_text.replace("FOLIAGE_SLOTS_PER_GRASS_CELL", "FOLIAGE_SLOTS_PER_GRASS_CELL_OLD", 1)
        frozen_shader.write_text(frozen_text, encoding="utf-8")
        drift = runner._water_shader_source_drift(dump)
        assert drift["available"] is True
        assert drift["water_changed_lines"] >= 1
        assert "fl4216" in drift["water_tokens"] or "wake" in drift["water_tokens"]
        assert drift["non_water_changed_lines"] >= 1
        assert "FOLIAGE" in drift["non_water_tokens"]


def test_fl4216_completion_status_keeps_diagnostic_proof_out_of_closure() -> None:
    status = _completion_status_module()
    stale_validator = {
        "exit_code": 0,
        "failures": [],
        "runtime_source_hash_mismatches": [
            {"path": "godot_project/render/ascii/raster/ascii_compositor.glsl"},
        ],
        "water_shader_source_drift": {
            "water_changed_lines": 2,
            "water_tokens": ["fl4216", "wake"],
        },
        "proof_tool_hash_mismatches": [
            {"path": "scripts/proofs/proof_fl4216_water_xdump.py"},
        ],
        "source_current_for_live_tree": False,
        "current_source_identity": {
            "dirty_files_relevant": [
                "godot_project/render/ascii/raster/ascii_compositor.glsl",
                "scripts/proofs/proof_fl4216_water_xdump.py",
            ],
            "dirty_files_runtime_relevant": [
                "godot_project/render/ascii/raster/ascii_compositor.glsl",
            ],
            "dirty_files_proof_tool_relevant": [
                "scripts/proofs/proof_fl4216_water_xdump.py",
            ],
        },
    }
    pass_validator = {
        "exit_code": 0,
        "failures": [],
        "source_current_for_live_tree": True,
    }
    shader_drift_validator = {
        "exit_code": 0,
        "failures": [],
        "source_current_for_live_tree": False,
        "water_shader_source_drift": {
            "water_changed_lines": 1,
            "water_tokens": ["fl4216"],
        },
    }
    fail_validator = {
        "exit_code": 1,
        "failures": ["missing water receipt rows"],
        "source_current_for_live_tree": True,
        "pose_id": "shore_flat_line",
    }
    stale_fail_validator = {
        "exit_code": 1,
        "dump_dir": ".run/final_render_cell_dump/stale",
        "verdict": "fail",
        "failures": ["shoreline right-direction bucket underrepresented"],
        "runtime_source_hash_mismatches": [
            {"path": "godot_project/render/ascii/raster/ascii_compositor.glsl"},
        ],
        "source_current_for_live_tree": False,
        "pose_id": "shore_flat_line",
    }
    missing_dump_validator = {
        "exit_code": 1,
        "verdict": "missing_dump",
        "failures": ["no required X-dump artifact found for shore_grass_context"],
        "source_current_for_live_tree": False,
        "pose_id": "shore_grass_context",
    }
    assert status._validator_display_status(stale_validator) == "STALE"
    assert status._validator_display_status(pass_validator) == "PASS"
    assert status._validator_display_status(shader_drift_validator) == "STALE"
    assert status._validator_display_status(fail_validator) == "FAIL"
    assert status._validator_display_status(stale_fail_validator) == "STALE"
    assert status._validator_display_status(missing_dump_validator) == "FAIL"
    blockers = status._closure_blockers([fail_validator, stale_fail_validator, missing_dump_validator], stale_validator)
    assert "required closure evidence view shore_flat_line is FAIL" in blockers
    assert "shore_flat_line: missing water receipt rows" in blockers
    assert "required closure evidence view shore_flat_line is STALE" in blockers
    assert "shore_flat_line: shoreline right-direction bucket underrepresented" not in blockers
    assert "required closure evidence view shore_grass_context is FAIL" in blockers
    assert "shore_grass_context: no required X-dump artifact found for shore_grass_context" in blockers
    assert "latest diagnostic X-dump is source-stale and cannot close FL-4216" in blockers
    assert "latest diagnostic runtime source hash mismatch: godot_project/render/ascii/raster/ascii_compositor.glsl" in blockers
    assert "latest diagnostic shader water-owned drift: 2 water-token changed lines (fl4216, wake)" in blockers
    assert "latest diagnostic proof-tool hash mismatch: scripts/proofs/proof_fl4216_water_xdump.py" in blockers
    assert any(
        blocker.startswith("current runtime water source has relevant dirty files: godot_project/render/ascii/raster/ascii_compositor.glsl")
        for blocker in blockers
    )
    assert any(
        blocker.startswith("current water proof tools have relevant dirty files: scripts/proofs/proof_fl4216_water_xdump.py")
        for blocker in blockers
    )


def test_fl4216_completion_status_requires_source_backed_reflection_pose() -> None:
    status = _completion_status_module()
    assert "open_reflection_source_backed" in status.REQUIRED_CLOSURE_POSES
    assert "open_reflection_band" not in status.REQUIRED_CLOSURE_POSES


def test_fl4216_completion_status_rejects_shared_required_pose_dump() -> None:
    status = _completion_status_module()
    pose_validators = [
        {
            "pose_id": "actor_sink_deep",
            "dump_dir": ".run/final_render_cell_dump/shared",
            "exit_code": 0,
            "failures": [],
            "source_current_for_live_tree": True,
        },
        {
            "pose_id": "wave_band_sweep",
            "dump_dir": ".run/final_render_cell_dump/shared",
            "exit_code": 0,
            "failures": [],
            "source_current_for_live_tree": True,
        },
    ]
    status._annotate_required_pose_dump_uniqueness(pose_validators)
    blockers = status._closure_blockers(
        pose_validators,
        {"exit_code": 0, "failures": [], "source_current_for_live_tree": True},
    )
    assert status._required_pose_dumps_unique(pose_validators) is False
    assert status._validator_display_status(pose_validators[0]) == "DUPLICATE"
    assert status._validator_display_status(pose_validators[1]) == "DUPLICATE"
    assert pose_validators[0]["shared_dump_pose_ids"] == ["actor_sink_deep", "wave_band_sweep"]
    assert (
        "required closure evidence views share one X-dump artifact: actor_sink_deep, wave_band_sweep -> "
        ".run/final_render_cell_dump/shared"
    ) in blockers


def test_fl4216_completion_status_step14_uses_unique_pose_artifacts() -> None:
    status = _completion_status_module()
    report = {"step_status": {}}
    actor_validator = {
        "pose_id": "actor_sink_deep",
        "dump_dir": ".run/final_render_cell_dump/shared",
        "exit_code": 0,
        "failures": [],
        "verdict": "proof_pass",
        "source_current_for_live_tree": True,
        "counts": {"water_rows": 1, "shader_readback_rows": 1},
    }
    wave_validator = {
        "pose_id": "wave_band_sweep",
        "dump_dir": ".run/final_render_cell_dump/shared",
        "exit_code": 0,
        "failures": [],
        "verdict": "proof_pass",
        "source_current_for_live_tree": True,
    }
    pose_validators = [actor_validator, wave_validator]
    status._annotate_required_pose_dump_uniqueness(pose_validators)
    status._apply_xdump_step_status(report, actor_validator)
    required_pose_pass = all(status._validator_closure_pass(v) for v in pose_validators)
    required_pose_pass = required_pose_pass and status._required_pose_dumps_unique(pose_validators)
    report["step_status"]["Step 14 headed manual proof"] = {
        "status": "PROOF_PASS" if required_pose_pass else "PENDING",
        "evidence": {str(v.get("pose_id")): v.get("dump_dir") for v in pose_validators},
    }
    assert report["step_status"]["Step 11 water readback"]["status"] == "PROOF_PASS"
    assert report["step_status"]["Step 14 headed manual proof"]["status"] == "PENDING"


def test_fl4216_completion_status_missing_pose_artifact_names_title_prefix() -> None:
    status = _completion_status_module()
    validator = status._run_water_xdump_validator(None, "shore_flat_line")
    assert validator["verdict"] == "missing_dump"
    assert validator["pose_id"] == "shore_flat_line"
    assert any(
        "no required X-dump artifact found for shore_flat_line" in failure
        and "fl4216_shore_flat_line_required_clean" in failure
        for failure in validator["failures"]
    )


def test_fl4216_completion_status_reads_step11_launch_manifests() -> None:
    status = _completion_status_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        actor_dir = root / "actor"
        actor_dir.mkdir()
        (actor_dir / "step11_launch_manifest.json").write_text(json.dumps({
            "schema": "fl4216_step11_launch_manifest.v1",
            "pose_id": "actor_sink_deep",
            "input_owner_kind": "scripted",
            "will_launch": False,
            "expected_dump_title_prefix": "fl4216_actor_sink_required_clean",
            "command": [
                "python3",
                "scripts/run_fl4359_headed_movement.py",
                "--request-final-dump",
            ],
        }), encoding="utf-8")
        partial = status._step11_launch_manifest_status([root], ["actor_sink_deep", "shore_flat_line"])
        assert partial["status"] == "PARTIAL"
        assert "actor_sink_deep" in partial["evidence"]["manifests"]
        assert partial["evidence"]["missing_required_pose_manifests"] == ["shore_flat_line"]

        shore_dir = root / "shore"
        shore_dir.mkdir()
        (shore_dir / "step11_launch_manifest.json").write_text(json.dumps({
            "schema": "fl4216_step11_launch_manifest.v1",
            "pose_id": "shore_flat_line",
            "input_owner_kind": "scripted",
            "will_launch": False,
            "expected_dump_title_prefix": "fl4216_shore_flat_line_required_clean",
            "command": [
                "python3",
                "scripts/run_fl4359_headed_movement.py",
                "--request-final-dump",
            ],
        }), encoding="utf-8")
        complete = status._step11_launch_manifest_status([root], ["actor_sink_deep", "shore_flat_line"])
        assert complete["status"] == "FILE_PRESENT"
        assert complete["evidence"]["missing_required_pose_manifests"] == []


def test_list_poses() -> None:
    proc = _run(["--list-poses"])
    assert proc.returncode == 0, proc.stderr
    poses = json.loads(proc.stdout)
    for pose_id in [
        "shore_flat_line",
        "actor_sink_deep",
        "shore_grass_context",
        "open_reflection_band",
        "open_reflection_source_backed",
        "wave_band_sweep",
    ]:
        assert pose_id in poses
    for pose_id, pose in poses.items():
        assert isinstance(pose["player"], list), pose_id
        assert len(pose["player"]) == 3, pose_id
        assert isinstance(pose["viewport"], list), pose_id
        assert len(pose["viewport"]) == 2, pose_id
        assert pose["purpose"], pose_id
    assert poses["actor_sink_deep"]["wake_dir"] == [0.0, 0.0, 1.0]
    assert poses["actor_sink_deep"]["camera_pitch_deg"] == -30.0
    assert poses["open_reflection_band"]["diagnostic_only"] is True
    assert poses["open_reflection_band"]["closure_eligible"] is False
    assert poses["open_reflection_source_backed"]["closure_eligible"] is True
    assert poses["open_reflection_source_backed"]["reflection_pose_source"] == "source_map_probe"
    assert poses["open_reflection_source_backed"]["source_cell"] == {"x": -423, "y": 129}


def test_validate_pose_registry() -> None:
    proc = _run(["--validate-pose-registry"])
    assert proc.returncode == 0, proc.stderr
    assert "FL4216_WATER_POSE_REGISTRY_OK" in proc.stdout


def test_source_map_probe_emits_source_backed_reflection_pose() -> None:
    proc = _run(["--source-map-probe"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = json.loads(proc.stdout)
    assert receipt["schema"] == "fl4216_source_map_reflection_pose_probe.v1"
    assert receipt["reflection_pose_source"] == "source_map_probe"
    assert receipt["selected_pose_id"] == "open_reflection_source_backed"
    assert receipt["selected"]["player"] == [-366.5, 6.0, -46.5]
    assert receipt["selected"]["source_cell"] == {"x": -423, "y": 129}
    assert receipt["selected"]["material_id"] == 0
    assert receipt["candidate_count"] > 0


def test_dry_run_receipt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        proc = _run([
            "--pose-id",
            "shore_flat_line",
            "--input-owner-kind",
            "scripted",
            "--output",
            str(out),
            "--dry-run",
        ])
        assert proc.returncode == 0, proc.stderr
        receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
        assert receipt["verdict"] == "dry_run"
        assert receipt["pose_id"] == "shore_flat_line"
        assert receipt["input_owner_kind"] == "scripted"
        assert receipt["closure_eligible"] is False
        assert isinstance(receipt["git_head"], str)
        assert isinstance(receipt["dirty"], bool)
        assert isinstance(receipt["dirty_files"], list)
        assert isinstance(receipt["dirty_files_allowed"], list)
        assert isinstance(receipt["dirty_files_relevant"], list)
        assert isinstance(receipt["dirty_files_runtime_relevant"], list)
        assert isinstance(receipt["dirty_files_proof_tool_relevant"], list)
        assert receipt["source_identity_scope"] in {
            "clean",
            "dirty_allowed",
            "dirty_runtime_relevant",
            "dirty_proof_tool_relevant",
        }
        assert EXPECTED_RECEIPT_FIELDS.issubset(set(receipt["required_fields"]))
        check = _run(["--validate-receipt", str(out / "receipt.json")])
        assert check.returncode == 1
        assert "verdict must be proof_pass for proof validation" in check.stderr


def test_step11_driver_writes_non_launch_command_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        proc = _run([
            "--pose-id",
            "actor_sink_deep",
            "--input-owner-kind",
            "scripted",
            "--output",
            str(out),
        ])
        assert proc.returncode == 0, proc.stderr
        receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
        manifest = json.loads((out / "step11_launch_manifest.json").read_text(encoding="utf-8"))
        assert receipt["verdict"] == "launch_manifest_ready"
        assert receipt["will_launch"] is False
        assert manifest["will_launch"] is False
        command = manifest["command"]
        # FL-4216 repair 2026-08-19: the fl4057 X-dump transport is deleted in
        # the engine (Main.gd fails closed on --fl4057-ab-x-dump) and
        # --final-render-dump-title has no consumer. The pose registry is
        # unchanged; only the transport moved to the surviving exact-pose seam
        # plus World._request_final_render_cell_dump.
        assert "--fl4057-ab-x-dump" not in command
        assert not any(c.startswith("--final-render-dump-title") for c in command)
        assert "run_fl4359_headed_movement.py" in " ".join(command)
        assert "--exact-player-position" in command
        assert "155.5,2.0,-0.5" in command
        # 180 degrees expressed in radians for the exact-pose seam.
        assert any(c.startswith("--exact-camera-yaw=3.14159") for c in command)
        assert "--request-final-dump" in command
        assert "--resolution" in command and "1280x800" in command
        check = _run(["--validate-receipt", str(out / "receipt.json")])
        assert check.returncode == 1
        assert "verdict must be proof_pass for proof validation" in check.stderr


def test_manual_scripted_schema_parity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scripted = root / "scripted"
        manual = root / "manual"
        scripted_proc = _run([
            "--pose-id",
            "wave_band_sweep",
            "--input-owner-kind",
            "scripted",
            "--output",
            str(scripted),
            "--dry-run",
        ])
        assert scripted_proc.returncode == 0, scripted_proc.stderr
        manual_proc = _run([
            "--pose-id",
            "wave_band_sweep",
            "--input-owner-kind",
            "manual",
            "--output",
            str(manual),
            "--dry-run",
        ])
        assert manual_proc.returncode == 0, manual_proc.stderr
        parity = _run([
            "--compare-schema",
            str(scripted / "receipt.json"),
            str(manual / "receipt.json"),
        ])
        assert parity.returncode == 1
        assert "verdict must be proof_pass for proof validation" in parity.stderr


def test_write_receipt_pair() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        proc = _run([
            "--pose-id",
            "shore_grass_context",
            "--write-receipt-pair",
            str(out),
        ])
        assert proc.returncode == 1
        parity_path = out / "schema_parity_receipt.json"
        assert str(parity_path) in proc.stderr
        scripted = json.loads((out / "scripted" / "receipt.json").read_text(encoding="utf-8"))
        manual = json.loads((out / "manual" / "receipt.json").read_text(encoding="utf-8"))
        parity = json.loads(parity_path.read_text(encoding="utf-8"))
        assert scripted["input_owner_kind"] == "scripted"
        assert manual["input_owner_kind"] == "manual"
        assert scripted["pose_id"] == manual["pose_id"] == "shore_grass_context"
        assert parity["verdict"] == "fail"
        assert parity["exempt_fields"] == ["input_owner_kind"]
        assert len(parity["failures"]) >= 2


def test_write_receipt_pair_requires_pose() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run([
            "--write-receipt-pair",
            tmp,
        ])
        assert proc.returncode == 2
        assert "--write-receipt-pair requires --pose-id" in proc.stderr


def test_schema_parity_rejects_missing_field() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scripted = root / "scripted"
        manual = root / "manual"
        assert _run([
            "--pose-id",
            "actor_sink_deep",
            "--input-owner-kind",
            "scripted",
            "--output",
            str(scripted),
            "--dry-run",
        ]).returncode == 0
        assert _run([
            "--pose-id",
            "actor_sink_deep",
            "--input-owner-kind",
            "manual",
            "--output",
            str(manual),
            "--dry-run",
        ]).returncode == 0
        manual_receipt = manual / "receipt.json"
        payload = json.loads(manual_receipt.read_text(encoding="utf-8"))
        del payload["required_dump_family"]
        manual_receipt.write_text(json.dumps(payload), encoding="utf-8")
        parity = _run([
            "--compare-schema",
            str(scripted / "receipt.json"),
            str(manual_receipt),
        ])
        assert parity.returncode == 1
        assert "missing top-level field required_dump_family" in parity.stderr


def test_validate_receipt_rejects_missing_planned_water_field() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        assert _run([
            "--pose-id",
            "shore_flat_line",
            "--input-owner-kind",
            "scripted",
            "--output",
            str(out),
            "--dry-run",
        ]).returncode == 0
        receipt = out / "receipt.json"
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["verdict"] = "proof_pass"
        payload["required_fields"].remove("fl4129.foam.selected_glyph_id")
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        proc = _run(["--validate-receipt", str(receipt)])
        assert proc.returncode == 1
        assert "required_fields missing fl4129.foam.selected_glyph_id" in proc.stderr


def test_validate_receipt_rejects_closure_with_relevant_dirty_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        assert _run([
            "--pose-id",
            "open_reflection_source_backed",
            "--input-owner-kind",
            "scripted",
            "--output",
            str(out),
            "--dry-run",
        ]).returncode == 0
        receipt = out / "receipt.json"
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["verdict"] = "proof_pass"
        payload["closure_eligible"] = True
        payload["dirty_files_runtime_relevant"] = ["godot_project/render/ascii/raster/ascii_compositor.glsl"]
        payload["source_identity_scope"] = "dirty_runtime_relevant"
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        proc = _run(["--validate-receipt", str(receipt)])
        assert proc.returncode == 1
        assert "closure_eligible true with runtime relevant dirty files" in proc.stderr


def _water_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "screen_cell": {"x": 1, "y": 2},
        "pixel_rect": {"x": 16, "y": 32, "w": 16, "h": 16},
        "glyph_id": 516,
        "glyph_char": "W",
        "material_id": 0,
        "material_family": 3,
        "water_state": {
            "inferred_from": "shader_fact_readback",
            "material_family": 3,
            "foam_selected_glyph_id": 516,
            "foam_direction_bucket": "left",
            "foam_curl_bucket": "none",
            "foam_density_bucket": "medium",
        },
        "fl4129.depth_fade": 0.5,
        "fl4129.shore_foam": 0.25,
        "fl4129.x13_sweep": 0.3,
        "fl4129.direction_bucket": "left",
        "fl4129.density_bucket": "medium",
        "fl4129.foam.role": "wake",
        "fl4129.foam.surface_bucket_source": "actor_velocity",
        "fl4129.foam.owner_side": "water",
        "fl4129.foam.direction_bucket": "left",
        "fl4129.foam.curl_bucket": "none",
        "fl4129.foam.convexity_bucket": "continuous_curl",
        "fl4129.foam.density_bucket": "medium",
        "fl4129.foam.extended_atlas_ok": True,
        "fl4129.foam.fallback_used": False,
        "fl4129.foam.selected_glyph_id": 516,
        "fl4129.foam.fallback_glyph_id": None,
        "fl4129.foam.receipt_id": 4208005,
        "fl4129.foam.font_hash": 124449485,
        "fl4129.foam.rendered_bitmap_hash": 2002801432,
        "fl4129.foam.role_verified_at_codepoint": 0x3075,
        "fl4129.reflection.strength": 0.4,
        "fl4129.reflection.alpha": 1.0,
        "fl4129.reflection.product_present": True,
        "fl4129.reflection.fallback_used": False,
        "fl4129.reflection.fallback_reason": 0,
        "fl4129.reflection.sky_sample_id": None,
        "fl4129.wake.strength": 0.2,
        "fl4129.wake.actor_id": 1,
        "fl4129.pixelwater.refraction.displaced_uv_reverted": False,
        "fl4129.pixelwater.depth_color.depth_fade": 0.4,
        "fl4129.pixelwater.fog.factor": 0.27,
        "fl4129.pixelwater.detail.mask": 0.33,
        "fl4129.pixelwater.crest.active": True,
        "fl4129.pixelwater.surface_foam.active": True,
        "fl4129.pixelwater.shore_foam.value": 0.25,
        "fl4129.x13.wave_anim": [1.0, 2.0, 3.0, 4.0],
        "fl4129.x13.wave_pos": [1.0, 2.0, 3.0, 4.0],
        "fl4129.x13.wave_siz": [1.0, 2.0, 3.0, 4.0],
        "fl4129.x13.ws": [1.0, 2.0, 3.0, 4.0],
        "fl4129.x13.shade255": 120,
        "fl4129.x13.matched_phase": 1,
        "fl4129.x13.glyph_bucket_before": "ripple",
        "fl4129.x13.glyph_bucket_after": "wave",
    }
    row.update(overrides)
    return row


def _metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "fact_readback_state": "gpu_shader_readback_readback_ok",
        "feature_state": {
            "water_current_package": True,
            "water_player_wake": True,
            "actor_submerge": True,
        },
        "player_world": {"x": 155.5, "y": 2.0, "z": -0.5},
        "viewport_size": {"w": 1280, "h": 800},
        "camera_pitch": -0.523599,
        "dirty": False,
        "fl4129": {
            "profile": {
                "color_mode": 0,
                "foam_mode": 0,
                "wave_shape": 0,
            },
            "perf": {
                "budget": {
                    "water_package_gpu_us_median": 1200.0,
                    "fact_readback_us_median": 500.0,
                    "fl4208_frame_cost_us_median": 800.0,
                    "headed_visible_fps_min": 24.0,
                },
                "water_package_gpu_us_median": 2.0,
                "fact_readback_us_median": 100.0,
                "fl4208_frame_cost_us_median": 2.0,
                "headed_visible_fps_min": 60.0,
            },
        },
    }
    payload.update(overrides)
    return payload


def _write_dump(root: Path, metadata: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (root / "cells.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_validate_dump_accepts_complete_fixture() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        rows = [
            _water_row(),
            {
                "screen_cell": {"x": 2, "y": 3},
                "pixel_rect": {"x": 32, "y": 48, "w": 16, "h": 16},
                "fl4129.actor_sink.actor_id": 7,
                "fl4129.actor_sink.actor_row": 24,
                "fl4129.actor_sink.actor_glyph_id": 220,
                "fl4129.actor_sink.actor_glyph_id_above": 220,
                "fl4129.actor_sink.water_glyph_id": 516,
                "fl4129.actor_sink.water_palette_entry_id": 5,
                "fl4129.actor_sink.sink_alpha": 0.9,
                "fl4129.actor_sink.reflected_y_shift": 4.0,
                "fl4129.actor_sink.pass_index": 0,
            },
        ]
        _write_dump(dump, _metadata(), rows)
        proc = _run(["--validate-dump", str(dump), "--pose-id", "actor_sink_deep"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "proof_pass"
        assert receipt["counts"]["water_rows"] == 1
        assert receipt["counts"]["actor_sink_takeover_rows"] == 1


def test_validate_dump_rejects_actor_sink_palette_entry_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        rows = [
            _water_row(),
            {
                "screen_cell": {"x": 2, "y": 3},
                "pixel_rect": {"x": 32, "y": 48, "w": 16, "h": 16},
                "fl4129.actor_sink.actor_id": 7,
                "fl4129.actor_sink.actor_row": 24,
                "fl4129.actor_sink.actor_glyph_id": 220,
                "fl4129.actor_sink.actor_glyph_id_above": 220,
                "fl4129.actor_sink.water_glyph_id": 516,
                "fl4129.actor_sink.water_palette_entry_id": 0,
                "fl4129.actor_sink.sink_alpha": 0.9,
                "fl4129.actor_sink.reflected_y_shift": 4.0,
                "fl4129.actor_sink.pass_index": 0,
            },
        ]
        _write_dump(dump, _metadata(), rows)
        proc = _run(["--validate-dump", str(dump), "--pose-id", "actor_sink_deep"])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("actor sink water palette entry mismatch" in failure for failure in receipt["failures"])


def test_validate_dump_hard_fails_water_runtime_budget() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        metadata = _metadata()
        metadata["fl4129"]["perf"]["water_package_gpu_us_median"] = 1201.0
        _write_dump(dump, metadata, [_water_row()])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("water_package_gpu_us_median=1201.0 budget=1200.0" in failure for failure in receipt["failures"])


def test_validate_dump_warns_on_proof_apparatus_perf_without_failing_water_runtime() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        metadata = _metadata()
        metadata["fl4129"]["perf"]["fact_readback_us_median"] = 501.0
        metadata["fl4129"]["perf"]["headed_visible_fps_min"] = 23.0
        _write_dump(dump, metadata, [_water_row()])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 0, proc.stderr
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "proof_pass"
        assert not receipt["failures"]
        assert any("fact_readback_us_median=501.0 budget=500.0" in warning for warning in receipt["warnings"])
        assert any("headed_visible_fps_min=23.0 budget=24.0 owner=FL-4218" in warning for warning in receipt["warnings"])


def test_validate_dump_does_not_apply_shore_distribution_to_open_reflection_pose() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        metadata = _metadata(
            player_world={"x": -366.5, "y": 6.0, "z": -46.5},
            camera_yaw=3.141592653589793,
        )
        rows = [_water_row(**{"fl4129.shore_foam": 0.25, "glyph_id": 516}) for _ in range(64)]
        _write_dump(dump, metadata, rows)
        proc = _run(["--validate-dump", str(dump), "--pose-id", "open_reflection_source_backed"])
        assert proc.returncode == 0, proc.stderr
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "proof_pass"
        assert not any("shoreline curl glyphs" in failure for failure in receipt["failures"])


def test_validate_dump_applies_shore_distribution_to_shore_pose() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        metadata = _metadata(player_world={"x": 155.5, "y": 4.0, "z": -0.5}, camera_yaw=0.0)
        rows = [_water_row(**{"fl4129.shore_foam": 0.25, "glyph_id": 516}) for _ in range(64)]
        _write_dump(dump, metadata, rows)
        proc = _run(["--validate-dump", str(dump), "--pose-id", "shore_flat_line"])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert any("shoreline curl glyphs 518/519 underrepresented" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_missing_x13_phase_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row()
        del row["fl4129.x13.matched_phase"]
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("missing X13 phase proof fields" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_water_error_glyph_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            glyph_id=33,
            glyph_char="!",
            water_state={
                "inferred_from": "shader_fact_readback",
                "foam_selected_glyph_id": 33,
            },
            **{
                "fl4129.foam.extended_atlas_ok": False,
                "fl4129.foam.fallback_used": True,
                "fl4129.foam.selected_glyph_id": 33,
                "fl4129.foam.fallback_glyph_id": 33,
            },
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("receiptless error glyph" in failure for failure in receipt["failures"])
        assert any("water rows used fallback glyph path" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_reserved_water_glyph_on_non_water_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        terrain_row = {
            "screen_cell": {"x": 8, "y": 9},
            "pixel_rect": {"x": 128, "y": 144, "w": 16, "h": 16},
            "glyph_id": 516,
            "glyph_char": "W",
            "material_id": 4,
            "water_state": {
                "inferred_from": "terrain_family",
                "foam_selected_glyph_id": 516,
            },
        }
        _write_dump(dump, _metadata(), [_water_row(), terrain_row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert receipt["counts"]["reserved_water_glyph_non_water_rows"] == 1
        assert any("reserved FL-4208 water glyphs on non-water rows" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_water_fields_on_non_water_material_family() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            material_id=4,
            material_family=1,
            water_state={
                "inferred_from": "shader_fact_readback",
                "material_family": 1,
                "foam_selected_glyph_id": 516,
                "foam_direction_bucket": "left",
                "foam_curl_bucket": "none",
                "foam_density_bucket": "medium",
            },
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("not shader water material family" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_sharp_water_glyph_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            glyph_id=0x2F,
            glyph_char="/",
            water_state={
                "inferred_from": "shader_fact_readback",
                "foam_selected_glyph_id": 0x2F,
            },
            **{
                "fl4129.foam.extended_atlas_ok": False,
                "fl4129.foam.fallback_used": True,
                "fl4129.foam.selected_glyph_id": 0x2F,
                "fl4129.foam.fallback_glyph_id": 0x2F,
            },
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("sharp/straight banned glyphs" in failure for failure in receipt["failures"])
        assert any("not receipt-backed rounded FL-4208 slots" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_extended_water_without_receipt_tuple() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(**{"fl4129.foam.receipt_id": None})
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("receipt_id missing" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_reflection_bucket_masking_active_wake() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            **{
                "fl4129.foam.role": "reflection",
                "fl4129.foam.surface_bucket_source": "reflection_sky_sample",
                "fl4129.reflection.sky_sample_id": 1,
                "fl4129.wake.strength": 0.25,
                "fl4129.wake.actor_id": 1,
            }
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert receipt["counts"]["wake_active_rows"] == 1
        assert receipt["counts"]["wake_actor_rows"] == 1
        assert any("wake has active rows but no wake bucket rows" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_reflection_fallback_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            **{
                "fl4129.reflection.product_present": False,
                "fl4129.reflection.fallback_used": True,
                "fl4129.reflection.fallback_reason": 1,
            }
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("reflection fallback rows present" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_active_wake_without_actor_provenance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            **{
                "fl4129.wake.strength": 0.25,
                "fl4129.wake.actor_id": None,
            }
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("wake has active rows but no actor provenance rows" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_wake_bucket_without_actor_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            **{
                "fl4129.foam.role": "wake",
                "fl4129.foam.surface_bucket_source": "wave_intensity",
                "fl4129.reflection.sky_sample_id": None,
                "fl4129.wake.actor_id": None,
            }
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("wake bucket source provenance invalid" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_non_water_owner_side() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(**{"fl4129.foam.owner_side": "land"})
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("not material-owned water side" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_vertical_water_direction() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(
            **{
                "fl4129.direction_bucket": "vertical",
                "fl4129.foam.direction_bucket": "vertical",
            }
        )
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("banned empty/vertical direction" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_invalid_curl_bucket_split() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(**{
            "fl4129.direction_bucket": "left",
            "fl4129.foam.direction_bucket": "left",
            "fl4129.foam.curl_bucket": "left",
        })
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("invalid curl bucket split" in failure for failure in receipt["failures"])


def test_validate_dump_rejects_water_state_axis_drift() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        row = _water_row(water_state={
            "inferred_from": "shader_fact_readback",
            "foam_selected_glyph_id": 516,
            "foam_direction_bucket": "right",
            "foam_curl_bucket": "none",
            "foam_density_bucket": "medium",
        })
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("water_state morphology axes match row fields" in failure for failure in receipt["failures"])

        row = _water_row(**{
            "fl4129.direction_bucket": "curl_left",
            "fl4129.foam.direction_bucket": "curl_left",
            "fl4129.foam.curl_bucket": "none",
        })
        _write_dump(dump, _metadata(), [row])
        proc = _run(["--validate-dump", str(dump)])
        assert proc.returncode == 1
        receipt = json.loads(proc.stdout)
        assert receipt["verdict"] == "fail"
        assert any("invalid curl bucket split" in failure for failure in receipt["failures"])


def main() -> int:
    test_list_poses()
    test_validate_pose_registry()
    test_source_map_probe_emits_source_backed_reflection_pose()
    test_dry_run_receipt()
    test_step11_driver_writes_non_launch_command_manifest()
    test_manual_scripted_schema_parity()
    test_write_receipt_pair()
    test_write_receipt_pair_requires_pose()
    test_schema_parity_rejects_missing_field()
    test_validate_receipt_rejects_missing_planned_water_field()
    test_validate_receipt_rejects_closure_with_relevant_dirty_files()
    test_fl4216_current_water_owner_has_typed_diagnostic_seam()
    test_fl4216_non_splat_plane_admission_is_per_subsample()
    test_fl4216_water_owner_call_sites_use_plane_depth_split()
    test_fl4216_visible_sprite_submerge_uses_sprite_pixel_y()
    test_fl4216_weather_ripples_use_water_owner_gate()
    test_fl4216_water_boundary_uses_plane_owner_not_raw_sample_only()
    test_fl4216_pure_water_keeps_owner_error_colors()
    test_fl4216_all_water_owner_calls_keep_owner_colors()
    test_fl4216_shore_edge_neighbors_use_strict_water_owner()
    test_fl4216_dormant_legacy_water_side_owners_are_deleted()
    test_fl4208_water_runtime_uses_page32_extended_glyph_atlas()
    test_fl4216_source_identity_tracks_runtime_water_atlas_files()
    test_fl4216_source_identity_excludes_docs_and_scaffold_tests()
    test_fl4216_shader_dirty_relevance_is_water_hunk_scoped()
    test_fl4216_dump_source_hash_mismatch_reports_water_file_drift()
    test_fl4216_dump_source_hash_mismatch_reports_missing_runtime_file()
    test_fl4216_shader_source_drift_classifies_water_owned_lines()
    test_fl4208_water_slots_are_not_used_by_non_water_terrain_families()
    test_fl4208_water_shore_direction_morphology_is_owned_by_land_side()
    test_fl4208_water_xdump_atlas_ok_is_not_range_only()
    test_fl4216_actor_sink_uses_water_wake_and_reflection_owner()
    test_fl4216_water_owner_preserves_baseline_a_fg_bg_colors()
    test_fl4216_x13_visible_sweep_uses_proof_phase_fields()
    test_fl4216_actor_sink_deletes_legacy_tint_owners()
    test_fl4216_reflection_fails_closed_without_x13_product()
    test_fl4216_reflected_terrain_product_marks_above_plane_source()
    test_fl4216_reflected_terrain_product_clears_before_dispatch()
    test_fl4216_reflected_terrain_product_textures_support_clear()
    test_fl4216_pixelwater_stage_facts_are_packed_and_dumped()
    test_fl4216_xdump_writer_has_no_cpu_inferred_water_state()
    test_fl4216_water_surface_bucket_uses_packed_wake_provenance()
    test_fl4216_completion_status_keeps_diagnostic_proof_out_of_closure()
    test_fl4216_completion_status_requires_source_backed_reflection_pose()
    test_fl4216_completion_status_rejects_shared_required_pose_dump()
    test_fl4216_completion_status_step14_uses_unique_pose_artifacts()
    test_fl4216_completion_status_missing_pose_artifact_names_title_prefix()
    test_fl4216_completion_status_reads_step11_launch_manifests()
    test_validate_dump_accepts_complete_fixture()
    test_validate_dump_rejects_actor_sink_palette_entry_mismatch()
    test_validate_dump_hard_fails_water_runtime_budget()
    test_validate_dump_warns_on_proof_apparatus_perf_without_failing_water_runtime()
    test_validate_dump_does_not_apply_shore_distribution_to_open_reflection_pose()
    test_validate_dump_applies_shore_distribution_to_shore_pose()
    test_validate_dump_rejects_missing_x13_phase_fields()
    test_validate_dump_rejects_water_error_glyph_rows()
    test_validate_dump_rejects_reserved_water_glyph_on_non_water_row()
    test_validate_dump_rejects_water_fields_on_non_water_material_family()
    test_validate_dump_rejects_sharp_water_glyph_rows()
    test_validate_dump_rejects_extended_water_without_receipt_tuple()
    test_validate_dump_rejects_reflection_bucket_masking_active_wake()
    test_validate_dump_rejects_reflection_fallback_rows()
    test_validate_dump_rejects_active_wake_without_actor_provenance()
    test_validate_dump_rejects_wake_bucket_without_actor_source()
    test_validate_dump_rejects_non_water_owner_side()
    test_validate_dump_rejects_vertical_water_direction()
    test_validate_dump_rejects_invalid_curl_bucket_split()
    test_validate_dump_rejects_water_state_axis_drift()
    print("FL4216_WATER_XDUMP_SCAFFOLD_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _fake_dump(root, name: str, *, player, yaw_deg: float, viewport=(1280, 800)) -> None:
    """One fabricated final_render_cell_dump with the metadata the validator reads."""
    import math as _math
    d = root / name
    d.mkdir(parents=True)
    (d / "cells.jsonl").write_text("", encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({
        "player_world": {"x": player[0], "y": player[1], "z": player[2]},
        "viewport_size": {"w": viewport[0], "h": viewport[1]},
        "camera_yaw": _math.radians(yaw_deg),
    }), encoding="utf-8")


def test_two_identical_poses_require_two_distinct_dumps() -> None:
    """shore_flat_line and shore_grass_context share ONE frozen pose.

    Player (155.5, 4.0, -0.5), yaw 0, viewport 1280x800, no declared pitch.
    Pose matching alone therefore cannot tell them apart, and admitting either
    on pose alone would let a SINGLE capture satisfy BOTH required views. That
    is the false green this contract exists to stop.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fl4216_completion_status", ROOT / "scripts" / "fl4216_completion_status.py"
    )
    status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(status)

    poses = {
        "shore_flat_line": {"player": [155.5, 4.0, -0.5], "yaw": 0.0, "viewport": [1280, 800]},
        "shore_grass_context": {"player": [155.5, 4.0, -0.5], "yaw": 0.0, "viewport": [1280, 800]},
    }
    with tempfile.TemporaryDirectory() as tmp:
        dumps = Path(tmp) / "dumps"
        dumps.mkdir()
        _fake_dump(dumps, "stamp_A", player=[155.5, 4.0, -0.5], yaw_deg=0.0)
        _fake_dump(dumps, "stamp_B", player=[155.5, 4.0, -0.5], yaw_deg=0.0)

        # Without an association BOTH ambiguous views must fail closed, rather
        # than both selecting the same newest dump.
        for pose_id in ("shore_flat_line", "shore_grass_context"):
            assert status._latest_dump_for_pose(
                pose_id, poses, associations={}, dump_roots=[dumps]
            ) is None

        # With a recorded association each view resolves to ITS OWN capture.
        assoc = {"shore_flat_line": "stamp_A", "shore_grass_context": "stamp_B"}
        flat = status._latest_dump_for_pose(
            "shore_flat_line", poses, associations=assoc, dump_roots=[dumps]
        )
        grass = status._latest_dump_for_pose(
            "shore_grass_context", poses, associations=assoc, dump_roots=[dumps]
        )
        assert flat is not None and grass is not None
        assert flat.name == "stamp_A"
        assert grass.name == "stamp_B"
        assert flat != grass, "two identical poses must not resolve to one dump"

        # A wrong association must NOT be rescued by the pose match.
        assert status._latest_dump_for_pose(
            "shore_flat_line", poses, associations={"shore_flat_line": "stamp_MISSING"},
            dump_roots=[dumps],
        ) is None


def test_every_generated_command_passes_runner_preflight() -> None:
    """Each required view's emitted command must survive the runner's own preflight.

    This is a PREFLIGHT-ONLY contract: it never launches Godot. It exists
    because the previously emitted command was not closure-grade -- it omitted
    camera position, a projection parameter, a fixed day-cycle time, the
    single-frame/temporal mode, the diagnostic marker, and the stability wait,
    and it inherited the default movement-proof classification, which the
    launcher rejects when combined with --skip-jitter-analysis.

    KNOWN RED, deliberately: the frozen pose registry records player, yaw and
    viewport (plus pitch for actor_sink_deep only). It does NOT record camera
    position, pitch for the other four views, fov/size, or day-cycle time, and
    those values are not recorded in the ledger or in any archived FL-4216
    receipt either. This contract names that gap instead of letting a command
    that cannot run be treated as ready.
    """
    required = [
        "actor_sink_deep",
        "open_reflection_source_backed",
        "shore_flat_line",
        "shore_grass_context",
        "wave_band_sweep",
    ]
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        for pose_id in required:
            out = Path(tmp) / pose_id
            gen = subprocess.run(
                [sys.executable, str(RUNNER), "--pose-id", pose_id,
                 "--input-owner-kind", "scripted", "--output", str(out)],
                capture_output=True, text=True,
            )
            manifest = out / "step11_launch_manifest.json"
            if gen.returncode != 0 or not manifest.exists():
                failures.append(f"{pose_id}: manifest not generated")
                continue
            command = json.loads(manifest.read_text(encoding="utf-8"))["command"]
            pre = subprocess.run(
                command + ["--preflight-only"], capture_output=True, text=True, cwd=str(ROOT)
            )
            if pre.returncode != 0:
                failures.append(f"{pose_id}: preflight rejected the generated command")
    assert not failures, "; ".join(failures)
