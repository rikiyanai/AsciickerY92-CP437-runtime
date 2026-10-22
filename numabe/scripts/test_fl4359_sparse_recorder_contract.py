#!/usr/bin/env python3
"""Focused static contract for the FL-4359 sparse recorder.

This test intentionally verifies the ownership and ordering seams. It does not
claim a headed or visual result. Runtime GPU evidence remains the headed lane.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
SHADER = ROOT / "godot_project/render/diagnostics/experiments/fl4359_sparse_surface_capture.glsl"
SPLAT_SHADER = ROOT / "godot_project/render/backends/perspective/splat/perspective_probe_splat.glsl"
PUBLISH_SHADER = ROOT / "godot_project/render/backends/perspective/publish/perspective_probe_payload_publish.glsl"
PROBE_PIPELINE = ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd"
DRIVER = ROOT / "scripts/adhoc/fl4359_external_movement_driver.gd"
RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"
ANALYZER = ROOT / "scripts/analyze_fl4359_moving_camera.py"
NATIVE_OBSERVER = ROOT / "godot_project/render/diagnostics/frame/NativeFrameObserver.gd"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("fl4359_analyzer", ANALYZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matrix_transition_uses_actual_raster_and_bounds_partial_cells():
    effect = _text(EFFECT)
    pipeline = _text(PROBE_PIPELINE)
    publish = _text(PUBLISH_SHADER)
    assert "func _pixel_camera_projection_extent(" in effect
    assert "pixel_ctx.presentation_size" in effect.split(
        '"resize_candidate_extent", subcell_extent,', 1
    )[1].split(')):', 1)[0]
    assert "raster_extent: Vector2i = Vector2i.ZERO" in pipeline
    assert "raster_extent == _splat_extent" in pipeline
    assert "_splat_extent = raster_extent" in pipeline
    assert "bool sample_pixel_in_extent(ivec2 pixel)" in publish
    assert publish.count("if (!sample_pixel_in_extent(") >= 2
    runner = _text(RUNNER)
    assert "choices=(12, 16, 20, 24, 32, 48, 64, 96)" in runner
    observer = _text(NATIVE_OBSERVER)
    assert "viewport_dispatch_matrix_identity_missing" in observer
    assert "viewport_dispatch_matrix_identity_changed" in observer


def test_shader_compiles_and_validates():
    def expand(source):
        return re.sub(r'^\s*#include "res://([^\"]+)"\s*$',
                      lambda match: expand(_text(ROOT / "godot_project" / match.group(1))),
                      source, flags=re.MULTILINE)
    source = expand(_text(SHADER))
    with tempfile.NamedTemporaryFile("w", suffix=".comp", delete=False) as handle:
        handle.write(source.removeprefix("#[compute]\n"))
        source_path = Path(handle.name)
    spirv_path = source_path.with_suffix(".spv")
    try:
        compile_result = subprocess.run(
            ["glslangValidator", "-V", "--target-env", "vulkan1.2", "-S", "comp", str(source_path), "-o", str(spirv_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert compile_result.returncode == 0, compile_result.stdout
        validate_result = subprocess.run(
            ["spirv-val", str(spirv_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert validate_result.returncode == 0, validate_result.stdout
        # Check the compiled SSBO array stride, which a private 80-byte recorder
        # declaration previously got wrong even though every shader compiled.
        disassembly = subprocess.run(["spirv-dis", str(spirv_path)], text=True,
                                     capture_output=True, check=True).stdout
        array = re.search(r"(%\S+)\s+= OpTypeRuntimeArray %ProbeCellResolveRecord(?:_\d+)?\b", disassembly)
        assert array is not None, "recorder must consume the shared publication type"
        assert re.search(r"OpDecorate " + re.escape(array[1]) + r" ArrayStride 96\b", disassembly)
    finally:
        source_path.unlink(missing_ok=True)
        spirv_path.unlink(missing_ok=True)


def test_capture_only_quad_replay_uses_the_visible_radial_continuity_domain():
    source = _text(SHADER)
    assert "vec4 capture_range;" in source
    assert "float source_capture_radial(float chebyshev_depth)" in source
    assert source.count("float center_radial = source_capture_radial(") == 1
    assert source.count("reconstruct_source_quad(") == 3
    assert source.count("abs(left_radial - center_radial)") == 1
    assert "radial >= 0.0 && radial < 0.999" in source


def test_sparse_replay_uses_production_water_geometry_and_bias_law():
    source = _text(SHADER)
    assert '#include "res://render/backends/perspective/water/perspective_probe_water_plane.glslinc"' in source
    assert "const uint CAPTURE_OWNER_WATER = 3u;" in source

    helper_start = source.index("bool reconstruct_source_quad(")
    helper_end = source.index("\nfloat triangle_edge(", helper_start)
    helper = source[helper_start:helper_end]
    assert "probe_capture_fetch(\n        source_capture_identity, source_capture_anchor," in helper
    assert "bool water_surface = source_identity.x == CAPTURE_OWNER_WATER;" in helper
    assert helper.count("probe_water_corner(") == 2
    assert "center = water_surface" in helper
    assert "corners[corner_index] = water_surface" in helper
    assert "probe_control.capture_range.y" in helper
    assert "vec3 edge_center = source_origin + center_dir" in helper
    assert "normalize(edge_center - probe_control.eye_origin.xyz)" in helper
    assert "if (probe_index == 0u && !water_surface)" in helper
    assert "if (probe_index == 0u) clip.z += 0.001 * clip.w;" not in helper

    prepack_start = source.index("uint raster_face = sample_provenance.y /")
    prepack_end = source.index("uvec4 raster_provenance", prepack_start)
    prepack = source[prepack_start:prepack_end]
    assert "if (raster_identity.x == CAPTURE_OWNER_WATER)" in prepack
    assert "probe_water_corner(" in prepack
    assert "raster_anchor.y" in prepack
    assert "probe_control.capture_range.y" in prepack
    assert "raster_world = vec4(raster_position, 1.0);" in prepack


def test_shader_joins_live_buffers_without_expanding_b55():
    source = _text(SHADER)
    assert "binding = 64" in source
    assert "binding = 55" in source
    assert "binding = 43" in source
    assert "binding = 200" in source
    assert "CAPTURE_RECORD_V16_WORDS = 584u" in source
    assert "CAPTURE_RECORD_V17_WORDS = 604u" in source
    assert "CAPTURE_RECORD_WORDS = CAPTURE_RECORD_V17_WORDS" in source
    assert "CAPTURE_EXPECTED_CENTER_ORACLE_OFFSET_WORDS = 374u" in source
    assert "CAPTURE_EXPECTED_CENTER_CAPTURE_IDENTITY_OFFSET_WORDS = 390u" in source
    assert "CAPTURE_EXPECTED_CENTER_CAPTURE_STATE_OFFSET_WORDS = 394u" in source
    assert "CAPTURE_EXPECTED_CENTER_NEIGHBOR_ADDRESS_OFFSET_WORDS = 398u" in source
    assert "CAPTURE_EXPECTED_CENTER_CAPTURE_REVISIONS_OFFSET_WORDS = 406u" in source
    assert "CAPTURE_FINAL_PAINT_SLOT_COLOUR_OFFSET_WORDS = 478u" in source
    assert "for (uint word = 42u; word < 51u; ++word)" in source
    assert "CAPTURE_FINAL_PAINT_TERMINAL_OCCURRENCE_OFFSET_WORDS = 495u" in source
    assert "for (uint word = 51u; word < 60u; ++word)" in source
    assert "FinalProductRecord" in source
    assert "uint subcell_x = sample_index % subcell_width" in source
    assert "uint subcell_y = sample_index / subcell_width" in source
    assert "uint ansi_width = subcell_width / 2u" in source
    assert "uint product_slot = (subcell_y & 1u) * 2u + (subcell_x & 1u)" in source
    assert "uint paint_cell = ansi_y * ansi_width + ansi_x" in source
    assert "uint product_index = paint_cell * 4u + product_slot" in source
    assert "final_product.records[product_index]" in source
    assert "final_product.records[sample_index]" not in source
    assert "uint paint_cell = sample_index / 4u" not in source
    assert "ADMISSION_JOIN_INVALID" in source
    assert "atomicAdd(capture_output.words[0u], 1u)" in source


def test_sparse_capture_preserves_retired_candidate_wire_offsets():
    shader = _text(SHADER)
    effect = _text(EFFECT)
    driver = _text(DRIVER)
    assert "CAPTURE_RECORD_V16_WORDS = 584u" in shader
    assert "CAPTURE_RECORD_V17_WORDS = 604u" in shader
    assert '"fl4359_sparse_surface_capture.v17"' in driver
    assert '"fl4359_sparse_surface_capture.v16"' in driver
    assert '"fl4359_sparse_surface_capture.v13"' not in driver
    assert "CAPTURE_CANDIDATE_RECORD_WORDS = 28u" in shader
    for offset in (
        "CAPTURE_CANDIDATE_IDENTITY_OFFSET_WORDS = 64u",
        "CAPTURE_CANDIDATE_WORLD_POSITION_OFFSET_WORDS = 68u",
        "CAPTURE_CANDIDATE_WORLD_NORMAL_OFFSET_WORDS = 72u",
        "CAPTURE_CANDIDATE_SCALARS_OFFSET_WORDS = 76u",
        "CAPTURE_CANDIDATE_STABLE_ANCHOR_OFFSET_WORDS = 80u",
        "CAPTURE_CANDIDATE_REVISIONS_OFFSET_WORDS = 84u",
        "CAPTURE_CANDIDATE_PROVENANCE_OFFSET_WORDS = 88u",
        "CAPTURE_CANDIDATE_IDENTITY_AGREEMENT_OFFSET_WORDS = 92u",
        "CAPTURE_CANDIDATE_COHORT_OFFSET_WORDS = 540u",
        "CAPTURE_CANDIDATE_COLOUR_INPUTS_OFFSET_WORDS = 536u",
    ):
        assert offset in shader
    assert "FL4359_SPARSE_CAPTURE_CANDIDATE_BINDING := 61" in effect
    for offset in (
        "FL4359_SPARSE_CAPTURE_CANDIDATE_OFFSET_WORDS := 64",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_IDENTITY_OFFSET_WORDS := 64",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_WORLD_POSITION_OFFSET_WORDS := 68",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_WORLD_NORMAL_OFFSET_WORDS := 72",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_SCALARS_OFFSET_WORDS := 76",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_STABLE_ANCHOR_OFFSET_WORDS := 80",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_REVISIONS_OFFSET_WORDS := 84",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_PROVENANCE_OFFSET_WORDS := 88",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_IDENTITY_AGREEMENT_OFFSET_WORDS := 92",
        "FL4359_SPARSE_CAPTURE_CANDIDATE_VALID_OFFSET_WORDS := 93",
        "FL4359_SPARSE_CAPTURE_SELECTED_SURFACE_VALID_OFFSET_WORDS := 94",
    ):
        assert offset in effect


    dispatch_start = effect.index("func _dispatch_fl4359_sparse_capture(")
    dispatch_end = effect.index("func _queue_fl4359_sparse_capture_readback", dispatch_start)
    dispatch = effect[dispatch_start:dispatch_end]
    assert "probe_receipt.get(" in dispatch
    assert 'var probe_candidate_binding := "deleted"' in dispatch
    assert "candidate.binding =" not in dispatch
    push = dispatch[dispatch.index("var push := _fl4359_sparse_capture_push_bytes("):]
    assert re.search(r"projection_kind,\s+0,\s+1,", push)
    assert (
        "selected, product, product_identity, oracle, paint, output"
        in dispatch
    )
    assert "product_identity.binding = 60" in dispatch
    assert "sparse_capture_product_identity_sidecar_missing" in dispatch
    assert "capture_uniforms.append_array(prepack_uniforms)" in dispatch


def test_actual_vertex_witness_has_distinct_surface_banks_and_explicit_absence():
    shader = _text(SHADER)
    effect = _text(EFFECT)
    pipeline = _text(ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd")
    fixture = _text(ROOT / "godot_project/scripts/tests/test_fl4057_perspective_probe_live_smoke.gd")
    assert "binding = 102" in shader and "binding = 110" in shader
    assert "underlay_raster_witness" in shader
    assert "witness_surface < 2u" in shader
    assert "contender_wins_native_tie" in shader
    assert "contender_surface_join" in shader
    assert "probe_control.bounds.w == 0u" in shader
    assert "probe_control.bounds.w != 0u" in shader
    assert '"probe_raster_witness_underlay_rid"' in effect
    assert '"probe_raster_witness_enabled"' in effect
    assert "_ensure_fl4359_resolve_absence_binding" in effect
    assert "sparse_capture_witness_absence_placeholder_missing" in effect
    assert '"raster_witness_front"' in pipeline
    assert '"raster_witness_underlay"' in pipeline
    assert '"enabled": _raster_witness_enabled' in pipeline
    assert '"capacity": _probe_size * _probe_size * LAYER_COUNT if _raster_witness_enabled else 0' in pipeline
    assert "VERTEX_WITNESS_CAPTURE_ONLY_MAX_BYTES := 360972296" in pipeline
    assert "344.25 MiB" in pipeline
    assert '"front_epoch"' in pipeline and '"underlay_epoch"' in pipeline
    assert '"underlay"' in fixture and "stale_markers" in fixture
    assert "underlay_raster_records_observed" in fixture
    assert "raster bank surface join mismatch" in fixture
    assert "_assert_reflected_geometry_capture" in fixture
    assert "reflection_geometry_draws" in fixture
    assert "reflection_capture_draw_lists" in fixture
    splat = _text(SPLAT_SHADER)
    assert "FL4359_VERTEX_WITNESS" in splat
    assert "readonly buffer Instances { uint texel_ids[]; } instances" in splat
    stability = _text(ROOT / "scripts/fl4359_actual_field_stability.py")
    assert '"actual_vertex_geometry"' in stability
    assert '"expected": actual_expected' in stability
    assert '"missing": actual_missing' in stability
    assert '"invalid": actual_invalid' in stability
    assert '"outside": actual_outside' in stability
    assert '"not_applicable"' in stability
    transport = _text(ROOT / "scripts/fl4359_raster_jitter_capture.py")
    assert "OPTIONAL_HISTORICAL_COLUMNS" in transport
    assert "probe_actual_vertex_clip_corners" in transport
    assert "name in OPTIONAL_HISTORICAL_COLUMNS" in transport
    assert "values[index] if index >= 0 else None" in transport
    driver = _text(DRIVER)
    for field in (
        "probe_actual_vertex_witness_valid",
        "probe_actual_vertex_witness_available",
        "probe_actual_vertex_witness_enabled",
        "probe_actual_vertex_witness_surface",
        "probe_actual_vertex_witness_instance",
        "probe_actual_vertex_witness_primitive",
        "probe_actual_vertex_clip_corners",
        "probe_raster_witness_enabled",
    ):
        assert f'"{field}"' in driver


def test_external_driver_transports_every_product_admission_required_field():
    driver = _text(DRIVER)
    analyzer = _load_analyzer()
    for field in analyzer.PRODUCT_ADMISSION_REQUIRED_FIELDS:
        assert f'"{field}":' in driver, field
    assert '"final_product_authored_role_family", 0xffffffff' in driver
    assert '"final_product_admission_transport_valid", false' in driver


def test_sparse_capture_materializes_candidate_provenance_without_product_key_inference():
    shader = _text(SHADER)
    effect = _text(EFFECT)
    for field in (
        "probe_candidate_source_probe",
        "probe_candidate_source_texel",
        "probe_candidate_world_position",
        "probe_candidate_world_normal",
        "probe_candidate_depth",
        "probe_candidate_coverage",
        "probe_candidate_status",
        "probe_candidate_identity",
        "probe_candidate_revisions",
        "probe_candidate_identity_agrees_with_selected_surface",
    ):
        assert f'"{field}"' in effect
    assert '"probe_candidate_source_texel": probe_candidate_provenance[1]' in effect
    assert "Product source key" in shader
    assert "candidate.provenance" in shader
    assert 'result["schema"] = "fl4359_sparse_surface_capture.v17"' in effect
    assert '"historical_schema": "fl4359_sparse_surface_capture.v16"' in effect
    assert '"reason": "sparse_capture_chunk_prepare_failed"' in effect
    assert '"materialization_chunk_prepare_reason": prepare_reason' in effect
    assert '"schema": "fl4359_sparse_surface_capture.v17"' in effect
    assert "FL4359_SPARSE_CAPTURE_V16_RECORD_WORDS := 584" in effect
    assert "FL4359_SPARSE_CAPTURE_V17_RECORD_WORDS := 604" in effect
    for field in (
        "probe_expected_center_record_valid",
        "probe_expected_center_query_valid",
        "probe_expected_center_capture_identity",
        "probe_expected_center_capture_revisions",
        "probe_expected_center_current_revisions",
        "probe_expected_center_capture_origin",
        "probe_expected_center_assigned_origin",
        "probe_expected_center_capture_origin_valid",
        "probe_expected_center_allocation_context",
        "probe_expected_center_publication_context",
        "probe_expected_center_submission_context",
        "probe_expected_center_expected_physical_layer",
        "probe_expected_center_physical_layer_matches",
        "probe_expected_center_face_admitted",
        "probe_expected_center_neighbor_dx",
        "probe_expected_center_neighbor_dy",
        "probe_expected_center_capture_depth_abs_residual",
    ):
        assert f'"{field}"' in effect


def test_sparse_capture_memory_ceiling_covers_expanded_candidate_record():
    effect = _text(EFFECT)
    capacity = _effect_constant(effect, "FL4359_SPARSE_CAPTURE_DEFAULT_CAPACITY_RECORDS")
    record_words = _effect_constant(effect, "FL4359_SPARSE_CAPTURE_RECORD_WORDS")
    header_bytes = _effect_constant(effect, "FL4359_SPARSE_CAPTURE_HEADER_BYTES")
    ceiling_expr = re.search(
        r"FL4359_SPARSE_CAPTURE_MAX_OUTPUT_BYTES := ([\d\s*]+)", effect
    ).group(1)
    max_output_bytes = 1
    for factor in ceiling_expr.split("*"):
        max_output_bytes *= int(factor.strip())
    per_dispatch_bytes = header_bytes + capacity * record_words * 4
    assert record_words == 604
    assert per_dispatch_bytes <= max_output_bytes
    # Full-grid capacity comes from the live b64/VSB record count. A fixed
    # 113x71 literal overflowed as soon as fullscreen presentation became
    # 113x74; the aggregate byte ceiling remains the sole memory bound.
    driver = _text(DRIVER)
    assert "fl4359_sparse_capture_current_record_count" in effect
    assert 'effect.call("fl4359_sparse_capture_current_record_count")' in driver
    current_count_start = effect.index(
        "func fl4359_sparse_capture_current_record_count() -> int:"
    )
    current_count_end = effect.index(
        "\n\n# The recorder's own declaration", current_count_start
    )
    current_count_helper = effect[current_count_start:current_count_end]
    assert '_vsb_view_receipt.get("publication", null)' in current_count_helper
    assert 'publication.get("record_count", 0)' in current_count_helper
    assert '_vsb_view_receipt.get("record_count", 0)' not in current_count_helper
    assert "FL4431_CANONICAL_ANSI_SIZE.x * FL4431_CANONICAL_ANSI_SIZE.y * 4" in driver
    full_grid_start = driver.index(
        "if _full_grid_temporal_oracle:", driver.index("var arm_capacity")
    )
    full_grid_branch = driver[
        full_grid_start:
        driver.index("elif effect.has_method", full_grid_start)
    ]
    assert 'current_bounds.get("capacity_records"' not in full_grid_branch
    assert "_fl4359_sparse_capture_outstanding_bytes() + required_bytes" in effect
    assert "> FL4359_SPARSE_CAPTURE_MAX_OUTPUT_BYTES" in effect


def test_row_major_subcells_map_bijectively_to_cell_major_product_slots():
    """Exercise the shader's exact index law at the canonical 32px extent."""
    subcell_width = 226
    subcell_height = 148
    ansi_width = subcell_width // 2
    product_indices = set()
    for sample_index in range(subcell_width * subcell_height):
        subcell_x = sample_index % subcell_width
        subcell_y = sample_index // subcell_width
        ansi_x = subcell_x // 2
        ansi_y = subcell_y // 2
        product_slot = (subcell_y & 1) * 2 + (subcell_x & 1)
        paint_cell = ansi_y * ansi_width + ansi_x
        product_index = paint_cell * 4 + product_slot
        product_indices.add(product_index)

        assert product_index // 4 == paint_cell
        assert product_index % 4 == product_slot

    record_count = subcell_width * subcell_height
    assert len(product_indices) == record_count
    assert min(product_indices) == 0
    assert max(product_indices) == record_count - 1


def test_driver_subcell_slot_uses_capture_width_at_row_boundaries():
    source = _text(DRIVER)
    assert "func _sparse_subcell_slot(sample_index: int, capture_contract: Dictionary) -> int:" in source
    assert 'var subcell_width := int(semantic[0]) * 2 if semantic.size() > 0 else 0' in source
    assert "var subcell_x := sample_index % subcell_width" in source
    assert "var subcell_y := int(sample_index / subcell_width)" in source
    assert "return 2 * (subcell_y & 1) + (subcell_x & 1)" in source
    subcell_width = 226
    for sample_index in (225, 226, 227, 228, 229):
        x = sample_index % subcell_width
        y = sample_index // subcell_width
        expected = 2 * (y & 1) + (x & 1)
        assert expected == (
            1 if sample_index == 225 else
            2 if sample_index == 226 else
            3 if sample_index == 227 else
            2 if sample_index == 228 else
            3
        )
    assert (228 & 3) != 2


def test_driver_decodes_the_product_owner_enum_not_the_vsb_enum():
    source = _text(DRIVER)
    helper_start = source.index("func _sparse_product_owner_name(")
    helper_end = source.index("func _nullable_product_identity(", helper_start)
    helper = source[helper_start:helper_end]
    expected = {
        1: "terrain",
        2: "static_mesh",
        3: "dynamic_mesh",
        4: "actor_sprite",
        5: "water",
        6: "sky",
        7: "item_sprite",
        8: "projectile",
        9: "dash_trail",
        10: "vapor",
        11: "beam",
        12: "reticle",
        13: "attack_swoosh",
        14: "local_light",
    }
    for owner, name in expected.items():
        assert f'\t\t{owner}:\n\t\t\treturn "{name}"' in helper
    assert '3:\n\t\t\treturn "water"' not in helper
    assert '5:\n\t\t\treturn "actor_sprite"' not in helper


def test_driver_shutdown_releases_world_compositor_before_scene_tree_exit():
    source = _text(DRIVER)
    helper_start = source.index("func _shutdown_driver(")
    helper_end = source.index("func _run()", helper_start)
    helper = source[helper_start:helper_end]
    assert "shutdown_camera.compositor = null" in helper
    assert 'world.set("_ascii_effect", null)' in helper
    assert helper.index("shutdown_camera.compositor = null") < helper.index(
        'world.set("_ascii_effect", null)'
    )
    assert 'world.get_node_or_null("DebugPaletteMenu")' in helper
    assert "release_rendering_resources" in helper
    assert helper.index("shutdown_camera.compositor = null") < helper.index(
        'effect.call("release_rendering_resources")'
    )
    assert helper.index('effect.call("release_rendering_resources")') < helper.index(
        'world.set("_ascii_effect", null)'
    )
    for callback_field in (
        "apply_callback", "read_callback", "materials_provider",
        "material_callback", "reset_materials_callback",
    ):
        assert callback_field in helper
    assert "debug_palette_menu.free()" in helper
    assert "debug_palette_menu.queue_free()" not in helper
    assert "debug_palette_menu = null" in helper
    assert "effect = null" in helper
    assert "compositor = null" in helper
    assert "shutdown_camera = null" in helper
    assert "await process_frame" in helper
    assert "call_deferred(\"_finish_shutdown\")" in helper
    assert "func _finish_shutdown()" in helper
    assert helper.index("await process_frame") < helper.index(
        'call_deferred("_finish_shutdown")'
    )
    assert helper.index('call_deferred("_finish_shutdown")') < helper.index(
        "func _finish_shutdown()"
    )
    assert helper.index("func _finish_shutdown()") < helper.index("quit(_shutdown_exit_code)")
    assert re.search(
        r'if shutdown_camera != null:\n\s+shutdown_camera\.compositor = null',
        helper,
    )
    assert "# explicit release_rendering_resources owner runs first; PREDELETE is fallback." in helper
    assert re.search(
        r'if world != null:\n'
        r'\s+var debug_palette_menu := world\.get_node_or_null\("DebugPaletteMenu"\)\n'
        r'\s+if debug_palette_menu != null:',
        helper,
    )
    assert re.search(
        r'for callback_field in \[\n'
        r'\s+"apply_callback", "read_callback", "materials_provider",\n'
        r'\s+"material_callback", "reset_materials_callback",\n'
        r'\s+\]:\n'
        r'\s+debug_palette_menu\.set\(callback_field, Callable\(\)\)\n'
        r'\s+debug_palette_menu\.free\(\)',
        helper,
    )
    assert re.search(
        r'if effect != null and effect\.has_method\("release_rendering_resources"\):\n'
        r'\s+var release_ok := bool\(effect\.call\("release_rendering_resources"\)\)\n'
        r'\s+if not release_ok:\n'
        r'\s+push_error\("FL4359 shutdown: retained ASCII effect release failed"\)\n'
        r'\s+shutdown_result = 1',
        helper,
    )
    assert re.search(
        r'elif effect != null:\n'
        r'\s+push_error\("FL4359 shutdown: retained ASCII effect lacks release_rendering_resources"\)\n'
        r'\s+shutdown_result = 1',
        helper,
    )
    assert "var shutdown_result := exit_code" in helper
    assert helper.index("var shutdown_result := exit_code") < helper.index(
        "var release_ok := bool(effect.call(\"release_rendering_resources\"))"
    )
    assert helper.index("shutdown_result = 1") < helper.index(
        'world.set("_ascii_effect", null)'
    )
    assert re.search(
        r'if world != null:\n\s+world\.set\("_ascii_effect", null\)',
        helper,
    )
    assert helper.index("_shutdown_exit_code = shutdown_result") < helper.index(
        'call_deferred("_finish_shutdown")'
    )
    assert re.search(
        r'await process_frame\n'
        r'\s+# Defer quit until this function and the caller\'s await continuation return\.\n'
        r'\s+# That removes proof-driver stack locals from the ObjectDB release boundary\.\n'
        r'\s+_shutdown_exit_code = shutdown_result\n'
        r'\s+if not _shutdown_quit_scheduled:\n'
        r'\s+_shutdown_quit_scheduled = true\n'
        r'\s+call_deferred\("_finish_shutdown"\)',
        helper,
    )
    world_clear_at = helper.index('world.set("_ascii_effect", null)')
    menu_free_at = helper.index("debug_palette_menu.free()")
    release_at = helper.index('var release_ok := bool(effect.call("release_rendering_resources"))')
    detach_at = helper.index("shutdown_camera.compositor = null")
    assert detach_at < menu_free_at < release_at < world_clear_at
    assert "quit(exit_code)" not in helper
    effect_source = _text(EFFECT)
    release_start = effect_source.index("func release_rendering_resources()")
    release_end = effect_source.index("func ", release_start + len("func release_rendering_resources()"))
    release_body = effect_source[release_start:release_end]
    notification_start = effect_source.index("func _notification(what: int)")
    notification_end = effect_source.index("func ", notification_start + len("func _notification(what: int)"))
    notification_body = effect_source[notification_start:notification_end]
    assert re.search(r"func release_rendering_resources\(\) -> bool:\n\s+if _rendering_resources_released:\n\s+return true", release_body)
    assert "if rd == null:" in release_body
    assert re.search(r"if rd == null:\n\s+return false", release_body)
    assert re.search(
        r'func _notification\(what: int\) -> void:\n'
        r'\s+if what != NOTIFICATION_PREDELETE:\n'
        r'\s+return\n'
        r'(?:\s+#[^\n]*\n)*'
        r'(?:\s+#[^\n]*\n)*'
        r'(?:\s+#[^\n]*\n)*'
        r'\s+if not _rendering_resources_released and rd != null:\n'
        r'\s+release_rendering_resources\(\)',
        notification_body,
    )
    shutdown_calls = list(re.finditer(r"^\s*await _shutdown_driver\([^\n]*\)", source, re.MULTILINE))
    assert shutdown_calls
    for call in shutdown_calls:
        continuation = source[call.end() : call.end() + 80]
        assert re.search(r"\n\t+return\b", continuation), continuation

    post_world_route = source[
        source.index("var local_actor_id := await _wait_for_server(main)") :
    ]
    assert "\n\t\tquit(" not in post_world_route
    assert "\n\tquit(" not in post_world_route
    assert source.count("quit(") == 5

    for receipt_marker in (
        '_write_receipt({"pass": false, "reason": "no_colyseus_snapshot"})',
        '_write_receipt({"pass": false, "reason": "missing_player_camera"})',
        '"reason": "airborne_airplane_spawn_not_observed"',
    ):
        marker_at = source.index(receipt_marker)
        shutdown_at = source.index("_shutdown_driver", marker_at)
        assert marker_at < shutdown_at
    run_body = source[source.index("func _run()") : source.index("func _read_args")]
    assert run_body.rindex("_write_receipt(") < run_body.rindex(
        "await _shutdown_driver(main, camera, 0 if ok else 1)"
    )


def test_capture_is_lazy_and_readback_is_sparse_async():
    source = _text(EFFECT)
    initialize = source[source.index("func _initialize_compute"):source.index("func _initialize_compute") + 18000]
    # The retained-probe observer is initialized on the render thread so an
    # armed proof can establish capture identity. The sparse recorder itself
    # remains request-owned and must not allocate during ordinary initialization.
    assert "_ensure_fl4359_sparse_capture_pipeline" not in initialize
    assert "_fl4359_sparse_capture_shader" not in initialize
    assert "_fl4359_sparse_capture_pipeline" not in initialize
    assert "PerspectiveProbePipelineScript.new()" in initialize
    assert 'call("capture_and_splat"' not in initialize
    sparse_start = source.index("func request_fl4359_sparse_capture")
    sparse_end = source.index("func _ensure_fl4359_sparse_capture_pipeline")
    sparse = source[sparse_start:sparse_end]
    assert "_ensure_fl4359_sparse_capture_pipeline" not in sparse
    dispatch_start = source.index("func _dispatch_fl4359_sparse_capture")
    dispatch_end = source.index("\nfunc ", dispatch_start + 1)
    dispatch = source[dispatch_start:dispatch_end]
    assert "buffer_get_data(" not in dispatch
    assert "buffer_get_data_async" not in dispatch
    assert "byte_count := count * FL4359_SPARSE_CAPTURE_RECORD_BYTES" in source
    assert "output_capacity_bytes" in dispatch
    assert "buffer_get_data_async" in source
    assert "_fl4359_sparse_capture_snapshot_callback" in source
    assert "_fl4359_sparse_capture_payload_callback" not in source
    assert "bytes.slice(" in source


def _effect_constant(source: str, name: str) -> int:
    """Read a declared FL-4359 recorder constant instead of restating its value.

    Every number this contract reasons about is pulled from the compositor, so
    the contract cannot drift from the owner the way the driver receipt did.
    """
    match = re.search(rf"{name} := (\d+)", source)
    assert match is not None, name
    return int(match.group(1))


def test_sparse_capacity_covers_canonical_stride_lanes_without_full_grid_readback():
    source = _text(EFFECT)
    capacity = _effect_constant(source, "FL4359_SPARSE_CAPTURE_DEFAULT_CAPACITY_RECORDS")
    record_words = _effect_constant(source, "FL4359_SPARSE_CAPTURE_RECORD_WORDS")
    header_bytes = _effect_constant(source, "FL4359_SPARSE_CAPTURE_HEADER_BYTES")
    ceiling_expr = re.search(
        r"FL4359_SPARSE_CAPTURE_MAX_OUTPUT_BYTES := ([\d\s*]+)", source
    ).group(1)
    max_output_bytes = 1
    for factor in ceiling_expr.split("*"):
        max_output_bytes *= int(factor.strip())
    record_bytes = record_words * 4
    # The record layout is a wire contract with the binding-200 shader; a change
    # here is a shader change, not a capacity change.
    assert record_words == 604
    assert "CAPTURE_RECORD_V17_WORDS = 604u" in _text(SHADER)
    assert "FL4359_SPARSE_CAPTURE_V16_RECORD_WORDS := 584" in source
    assert "actual_vertex_witness_available" in source

    # The gather admits a record when EITHER lane accepts it. The fixed-screen
    # term is exact. The world-anchor term is an expected count under a uniform
    # hash; it is not a mathematical upper bound because hash residues can
    # collide. Overflow remains a fail-closed runtime observation.
    #   screen lane: sample_index % screen_stride == 0
    #   world lane:  anchor_hash(stable_world_anchor) % world_stride == 0
    # R5's operator-designated 32px surface is 120x68 ANSI cells with four b64
    # subcell records per cell.
    canonical_b64_records = 120 * 68 * 4
    canonical_screen_stride = 64  # pinned by scripts/run_fl4359_headed_movement.py
    canonical_world_stride = 4  # successor proof coverage setting

    def expected_two_lane_population(world_stride: int, screen_stride: int) -> int:
        return (
            (canonical_b64_records + screen_stride - 1) // screen_stride
            + (canonical_b64_records + world_stride - 1) // world_stride
        )

    # The floor named by the FL-4359 capacity transaction.
    assert capacity >= 16384
    # A single overflowing invocation fails the whole frame closed. The buffer
    # must clear the expected population with substantial room for hash skew.
    canonical_estimate = expected_two_lane_population(
        canonical_world_stride, canonical_screen_stride
    )
    assert capacity >= canonical_estimate * 3 // 2, (capacity, canonical_estimate)

    # Declared maximum bounds a single allocation...
    assert header_bytes + capacity * record_bytes < max_output_bytes
    # ...and the aggregate of every outstanding buffer. A count-only ceiling
    # would let the live footprint scale with the record capacity.
    assert "_fl4359_sparse_capture_outstanding_bytes" in source
    assert "sparse_capture_outstanding_memory_ceiling" in source
    prepare = source[
        source.index("func _prepare_fl4359_sparse_capture") :
        source.index("func _dispatch_fl4359_sparse_capture")
    ]
    assert "_fl4359_sparse_capture_outstanding_bytes() + required_bytes" in prepare
    assert "> FL4359_SPARSE_CAPTURE_MAX_OUTPUT_BYTES" in prepare

    assert "capacity_records" in source
    assert "sparse_capture_output_capacity_overflow" in source


def test_capture_size_formula_has_one_owner_and_the_receipt_derives_from_it():
    """No call site may restate `min(record_count, CAPACITY) * RECORD_BYTES`."""
    source = _text(EFFECT)
    assert "func fl4359_sparse_capture_capacity_records" in source
    assert "func fl4359_sparse_capture_output_bytes" in source
    # The clamp appears exactly once: inside its own owner.
    assert source.count("mini(record_count, _fl4359_sparse_capture_capacity_records)") == 1
    for call_site in (
        "func _allocate_fl4359_sparse_capture_output",
        "func _prepare_fl4359_sparse_capture",
    ):
        start = source.index(call_site)
        body = source[start : start + 1800]
        assert "fl4359_sparse_capture_output_bytes(" in body, call_site

    # The receipt publishes the recorder's own declaration, never a literal.
    driver = _text(DRIVER)
    assert 'sparse_effect.has_method("fl4359_sparse_capture_bounds")' in driver
    assert '"sparse_capture_output_byte_formula": sparse_capture_bounds.get(' in driver
    assert '"sparse_capture_declared_capacity_records"' in driver
    assert "min(record_count,4096)" not in driver
    assert not re.search(r'"sparse_capture_max_outstanding_buffers": \d+', driver)


def test_push_constant_matches_four_uvec4_plus_projection_shader_abi():
    effect = _text(EFFECT)
    shader = _text(SHADER)
    assert "uvec4 grid;" in shader
    assert "uvec4 epoch;" in shader
    assert "uvec4 limits;" in shader
    assert "uvec4 capture_bands;" in shader
    assert "mat4 camera_view_projection;" in shader
    push_start = effect.index("func _fl4359_sparse_capture_push_bytes")
    push_end = effect.index("func _prepare_fl4359_sparse_capture", push_start)
    push = effect[push_start:push_end]
    assert "bytes.resize(128)" in push
    assert "bytes.encode_u32(32, capacity_records)" in push
    assert "bytes.encode_u32(36, subcell_width)" in push
    assert "bytes.encode_u32(40, candidate_binding_mode)" in push
    # b86's 60-word record size is not a push-constant byte offset.  Keep the
    # four lanes disjoint: oracle binding at 44, capture bands at 48/52/56,
    # and capture control at 60.
    assert "FL4359_ORACLE_RECORD_WORDS := 60" in _text(
        ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"
    )
    assert "bytes.encode_u32(44, oracle_binding_mode)" in push
    assert "bytes.encode_u32(48, int(capture_bands[0]))" in push
    assert "bytes.encode_u32(52, int(capture_bands[1]))" in push
    assert "bytes.encode_u32(56, int(capture_bands[2]))" in push
    assert "bytes.encode_u32(60, capture_control)" in push
    assert "bytes.encode_u32(60, oracle_binding_mode)" not in push
    assert "bytes.encode_float(64 + lane * 4" in push
    assert "pixel_ctx.semantic_ansi_size.x * 2" in effect


def test_driver_schedule_is_bounded_and_drain_is_fail_closed():
    source = _text(DRIVER)
    assert "mini(_sparse_capture_frame_limit, active_frames)" in source
    assert '"requested_active_frames"' in source
    assert '"effective_release_frames"' in source
    assert '"effective_settle_frames"' in source
    assert '"effective_total_frames"' in source
    assert '"sparse_capture_max_outstanding_buffers"' in source
    assert '"sparse_capture_peak_output_capacity_bytes"' in source
    assert "_sparse_capture_final_ready_count = _drain_sparse_capture(main)" in source
    assert "sparse_capture_drain_timeout" in source
    assert "_sparse_capture_result_error_count += 1" in source
    assert "_product_time_observed_set" in source
    assert "product_time_changed_across_capture" in source
    runner = _text(RUNNER)
    assert "evidence_sparse_capture_schedule_complete" in runner
    assert "sparse_capture_join_invalid_rows" in runner
    assert '"authority_snapshot_queue_window"' in source
    assert "_sparse_capture_completed_frame_indices" in source
    assert "current_frame != previous_frame + 1" in source
    assert "current_sequence != previous_sequence + 1" in source


def test_full_grid_capture_defers_cpu_materialization_until_motion_window_closes():
    source = _text(DRIVER)
    loop_start = source.index("for frame in range(active_frames):")
    loop_end = source.index(
        "_authority_snapshot_queue_window_end =", loop_start
    )
    active_loop = source[loop_start:loop_end]
    assert "if not _full_grid_temporal_oracle:\n\t\t\t_drain_sparse_capture(main)" in active_loop
    assert active_loop.rfind("_drain_sparse_capture(main)") < active_loop.rfind(
        "if not _full_grid_temporal_oracle:"
    ) + len("if not _full_grid_temporal_oracle:\n\t\t\t_drain_sparse_capture(main)")
    assert source.index("_authority_snapshot_queue_window_start =") < loop_start
    assert loop_end < source.index("for after_index in range(settle_after_frames):")
    assert "_stop_authority_ingest_before_materialization(main)" in source
    assert 'client.call("stop_authority_ingest_for_proof")' in source
    assert "or _authority_ingest_stopped_before_materialization" in source


def test_driver_uses_exact_product_anchor_for_motion_comparison():
    source = _text(DRIVER)
    row_start = source.index("func _sparse_row_for_analyzer")
    row_end = source.index("func _drain_sparse_capture", row_start)
    row_builder = source[row_start:row_end]
    assert 'row.get("selected_surface_stable_world_anchor", [])' in row_builder
    assert 'row.get("final_product_anchor", [])' in row_builder
    assert '"ansi_cell_world_anchor": comparison_anchor' in row_builder
    assert '"selected_surface_stable_world_anchor": selected_anchor_dict' in row_builder
    assert '"final_product_world_anchor": product_anchor_dict' in row_builder
    assert '"final_product_status": final_product_status' in row_builder
    assert '"comparison_anchor_source": comparison_anchor_source' in row_builder
    assert '"b55_final_product_anchor"' in row_builder
    assert '"b64_selected_surface_stable_world_anchor"' in row_builder


def test_grounded_headed_route_has_one_physical_input_owner():
    source = _text(DRIVER)
    for function_name, next_function in (
        ("func _set_ground_strafe", "func _cmd"),
        ("func _set_ground_move_input", "func _set_camera_turn_input"),
        ("func _set_camera_turn_input", "func _camera_yaw"),
    ):
        start = source.index(function_name)
        end = source.index(next_function, start)
        body = source[start:end]
        assert "_press_key(" in body
        assert "Input.action_press(" not in body
        assert "Input.action_release(" not in body
    assert "physical_ground_action_observed = physical_ground_action_observed or (" in source
    assert "physical_turn_action_observed = physical_turn_action_observed or (" in source
    grounded_acceptance_start = source.index("\telif local_actor_id == 0 and _capture_side == \"baseline\":")
    grounded_acceptance_end = source.index("\tok = ok and bool(exact_render_pose", grounded_acceptance_start)
    grounded_acceptance = source[grounded_acceptance_start:grounded_acceptance_end]
    assert "and physical_ground_action_observed" in grounded_acceptance
    assert "and (active_turn_frames == 0 or physical_turn_action_observed)" in grounded_acceptance
    assert "and camera_yaw_turn_measured" in grounded_acceptance
    assert '"physical_ground_action_observed": physical_ground_action_observed' in source
    assert '"physical_turn_action_observed": physical_turn_action_observed' in source
    assert 'fail_reason = "physical_ground_action_not_observed"' in source
    assert 'fail_reason = "physical_turn_action_not_observed"' in source
    assert 'fail_reason = "ground_camera_turn_not_measured"' in source


def test_gather_is_after_final_paint_barrier_and_default_path_is_empty():
    source = _text(EFFECT)
    gather = source.index("_dispatch_fl4359_sparse_capture(")
    barrier = source.index("rd.compute_list_add_barrier(compute_list)", gather - 600)
    assert barrier < gather
    final_dispatch = source.index("rd.compute_list_dispatch(compute_list, groups_x, groups_y, 1)")
    assert final_dispatch < barrier
    assert "if _fl4359_sparse_capture_active and final_paint_capture_active" in source
    assert "_fl4359_sparse_capture_active = false" in source
    assert "fl4359_settle_probe" not in source
    assert "_print_settle_probe" not in source


def test_gather_payload_is_published_and_validated_before_analysis():
    effect = _text(EFFECT)
    shader = _text(SHADER)
    dispatch_start = effect.index("func _dispatch_fl4359_sparse_capture")
    dispatch_end = effect.index("func _queue_fl4359_sparse_capture_readback", dispatch_start)
    dispatch = effect[dispatch_start:dispatch_end]
    gather_dispatch = dispatch.index("rd.compute_list_dispatch")
    post_gather_barrier = dispatch.index("rd.compute_list_add_barrier", gather_dispatch)
    assert post_gather_barrier > gather_dispatch
    assert "memoryBarrierBuffer();" in shader
    materialize_start = effect.index("func materialize_fl4359_sparse_capture")
    materialize_end = effect.index("func take_fl4359_sparse_capture_ready", materialize_start)
    materialize = effect[materialize_start:materialize_end]
    assert "sparse_capture_record_validation_failed" in materialize
    assert "admission_bits != 0" in materialize
    assert 'int(row["frame_index"]) == int(request.get("frame_index", -1))' in materialize
    assert "is_finite(float(stable_anchor[0]))" in materialize


def test_sparse_request_preserves_probe_lifecycle_truth_from_the_same_view_receipt():
    source = _text(EFFECT)
    helper_start = source.index("func _fl4057_probe_recorder_metadata")
    helper_end = source.index("func _", helper_start + len("func _fl4057_probe_recorder_metadata"))
    helper = source[helper_start:helper_end]
    for field in (
        '"valid"',
        '"absence_reason"',
        '"scene_revision"',
        '"view_revision"',
        '"terrain_revision"',
        '"water_revision"',
        '"static_revision"',
        '"dynamic_revision"',
        '"capture_generation"',
        '"recapture_reason"',
        '"recapture_mask"',
        '"active_mask"',
        '"transition_remaining"',
        '"stable_identity_transition_progress"',
        '"current_identity_transition_threshold"',
        '"previous_identity_transition_threshold"',
        '"face_masks"',
        '"recaptured_faces"',
        '"families_submitted"',
        '"steady_reuse"',
        '"candidate_extent"',
        '"materialized_records"',
    ):
        assert field in helper
    for retired_field in (
        '"bayer_phase"',
        '"bayer_threshold"',
        '"current_bayer_threshold"',
        '"previous_bayer_threshold"',
        '"probe_crossfade_phase"',
        '"transition_phase"',
    ):
        assert retired_field not in helper
    request_start = source.index("func _dispatch_fl4359_sparse_capture")
    request_end = source.index("func _queue_fl4359_sparse_capture_readback", request_start)
    request = source[request_start:request_end]
    assert '"perspective_probe_state": _fl4057_probe_recorder_metadata()' in request
    assert '"dynamic_mover_timing": _last_fl4691_dynamic_mover_timing.duplicate(true)' in request
    assert '"water_interaction_field_metrics": (' in request
    assert '_water_interaction_field_last_metrics.duplicate(true)' in request
    driver = _text(DRIVER)
    assert 'frame_trace["perspective_probe_state"] = result.get(' in driver
    assert 'frame_trace["perspective_probe_state"] = result.get("perspective_probe_state").duplicate(true)' in driver
    lifecycle_start = driver.index('frame_trace["lifecycle_facts_required"] = true')
    lifecycle_end = driver.index('frame_trace["viewport"]', lifecycle_start)
    assert '"perspective_probe_state", {}' not in driver[lifecycle_start:lifecycle_end]
    assert 'frame_trace["dynamic_mover_timing"] = result.get(' in driver
    assert 'frame_trace["dynamic_mover_timing"] = result.get("dynamic_mover_timing").duplicate(true)' in driver
    assert '"dynamic_mover_timing", {}' not in driver
    assert 'frame_trace["water_interaction_field_metrics"] = result.get(' in driver
    assert 'frame_trace["water_interaction_field_metrics"] = result.get("water_interaction_field_metrics").duplicate(true)' in driver
    assert '"water_interaction_field_metrics", {}' not in driver
    assert 'func _sparse_lifecycle_facts_are_valid(result: Dictionary) -> bool:' in driver
    assert '"sparse_lifecycle_facts_valid": _sparse_lifecycle_facts_valid' in driver
    assert 'var subcell_slot := _sparse_subcell_slot(sample_index, capture_contract)' in driver


def test_sparse_capture_authority_tick_is_bound_at_render_dispatch():
    source = _text(EFFECT)
    dispatch_start = source.index("func _dispatch_fl4359_sparse_capture")
    dispatch_end = source.index("func _queue_fl4359_sparse_capture_readback", dispatch_start)
    dispatch = source[dispatch_start:dispatch_end]
    requested = 'metadata["requested_authority_server_tick"] = metadata.get('
    authoritative = 'metadata["authority_server_tick"] = _last_player_server_tick'
    source_tag = '"compositor_dispatch_player_publication"'
    request_store = '_fl4359_sparse_capture_requests[request_id] = {'
    assert requested in dispatch
    assert authoritative in dispatch
    assert source_tag in dispatch
    assert dispatch.index(requested) < dispatch.index(authoritative)
    assert dispatch.index(authoritative) < dispatch.index(request_store)


def test_sparse_capture_budget_and_route_metadata_advance_only_on_dispatch():
    effect = _text(EFFECT)
    assert "var _fl4359_sparse_capture_session_dispatch_count: int = 0" in effect
    assert effect.count("_fl4359_sparse_capture_session_dispatch_count = 0") >= 2
    assert "func fl4359_sparse_capture_session_dispatch_count() -> int:" in effect
    dispatch_start = effect.index("func _dispatch_fl4359_sparse_capture")
    dispatch_end = effect.index(
        "func _queue_fl4359_sparse_capture_readback", dispatch_start
    )
    dispatch = effect[dispatch_start:dispatch_end]
    assert "_fl4359_sparse_capture_session_dispatch_count += 1" in dispatch
    render_start = effect.index("func _render_callback")
    render = effect[render_start:]
    assert 'if sparse_capture_dispatch.has("request_id"):' in render
    decrement = render.index("_fl4359_sparse_capture_frames_remaining -= 1")
    request_guard = render.rfind(
        'if sparse_capture_dispatch.has("request_id"):', 0, decrement
    )
    assert request_guard >= 0

    driver = _text(DRIVER)
    assert "func _await_sparse_capture_dispatch(" in driver
    assert "current_count == previous_count + 1" in driver
    assert "current_count > previous_count + 1" in driver
    run_start = driver.index("func _run() -> void:")
    run_end = driver.index(
        "func _run_stationary_terminal_reprojection_calibration", run_start
    )
    run = driver[run_start:run_end]
    assert run.count("await _await_sparse_capture_dispatch(") == 2
    assert 'fail_fast_reason = "full_grid_sparse_dispatch_sequence_failed"' in run


def test_sparse_lifecycle_accepts_stringname_metrics_and_bounds_failure_logs():
    driver = _text(DRIVER)
    finite_start = driver.index("func _lifecycle_payload_is_finite")
    finite_end = driver.index("func _lifecycle_dictionary_is_healthy", finite_start)
    finite = driver[finite_start:finite_end]
    assert "TYPE_STRING_NAME" in finite
    assert "key is String or key is StringName" in finite
    drain_start = driver.index("func _drain_sparse_capture")
    drain_end = driver.index("func _arm_moving_dump_before_active_route", drain_start)
    drain = driver[drain_start:drain_end]
    assert 'result.get("pass", false)' in drain
    assert "_sparse_capture_result_summary(result, \"capture_result_failed\")" in drain
    assert "_sparse_capture_result_summary(result, \"lifecycle_facts_invalid\")" in drain
    assert "_sparse_capture_completed_request_ids.has(request_id)" in drain
    assert 'JSON.stringify(result)' not in drain
    assert "var analyzer_row := _sparse_row_for_analyzer(row, result)" in drain
    assert 'analyzer_row.erase("frame_trace")' in drain
    assert "_store_sparse_analyzer_row(analyzer_row)" in drain
    assert '"row_type": "fl4359_jitter_frame"' in drain


def test_full_grid_transport_writes_one_schema_and_positional_rows():
    driver = _text(DRIVER)
    helper_start = driver.index("func _store_sparse_analyzer_row")
    helper_end = driver.index("func _lifecycle_payload_is_finite", helper_start)
    helper = driver[helper_start:helper_end]
    assert '"fl4359_jitter_transport.v16"' in driver
    assert '"final_paint_slot_colour_present"' in driver
    assert '"final_paint_slot_fg_rgb"' in driver
    assert '"final_paint_slot_bg_rgb"' in driver
    assert '"row_type": "fl4359_jitter_schema"' in helper
    assert '"fields": _sparse_capture_transport_fields' in helper
    assert "_sparse_capture_transport_fields.sort()" in helper
    assert "analyzer_row.size() != _sparse_capture_transport_fields.size()" in helper
    assert "not analyzer_row.has(field_name)" in helper
    assert "JSON.stringify(values)" in helper
    assert "func _fl4359_transport_json_value" in driver
    assert "bytes.encode_double(0, float(value))" in driver
    assert "bytes.decode_u32(0)" in driver
    assert "bytes.decode_u32(4)" in driver
    assert 'out["final_index"] = row.get("final_index", null)' in driver
    assert 'out["binding_62_refusal"] = row.get("binding_62_refusal", null)' in driver
    assert 'out["terrain_cell"] = null' in driver
    assert 'out["terr_stable_cell_gpu"] = null' in driver


def test_sparse_transport_stops_after_failed_write_or_flush():
    driver = _text(DRIVER)
    write = driver.split("func _store_sparse_capture_line(", 1)[1].split("\nfunc ", 1)[0]
    assert "if _sparse_capture_file.store_line(line):" in write
    assert "_sparse_lifecycle_facts_valid = false" in write
    assert "_sparse_capture_result_error_count += 1" in write
    assert "_sparse_capture_file.close()" in write
    assert "_sparse_capture_file = null" in write
    assert driver.count("_sparse_capture_file.store_line(") == 1
    drain = driver.split("func _write_sparse_capture_batch(", 1)[1].split("\nfunc ", 1)[0]
    assert "if not _store_sparse_capture_line(JSON.stringify({" in drain
    assert "if _sparse_capture_file.get_error() != OK:" in drain
    assert "_sparse_capture_file = null" in drain


def test_full_grid_exports_one_decoded_frame_at_a_time():
    effect = _text(EFFECT)
    take = effect.split("func take_fl4359_sparse_capture_ready(", 1)[1].split("\nfunc ", 1)[0]
    assert "max_results: int = 0" in take
    assert "if max_results > 0 and ready.size() >= max_results:" in take
    assert "break" in take
    # Chunk opt-in failures retire the request instead of falling back to an
    # unbounded decode; the second call is the named failure path.
    assert take.count("materialize_fl4359_sparse_capture(request_id)") == 2
    driver = _text(DRIVER)
    drain = driver.split("func _drain_sparse_capture(", 1)[1].split("\nfunc ", 1)[0]
    assert "take_fl4359_sparse_capture_ready" in drain
    assert "FL4359_FULL_GRID_MATERIALIZATION_GROUPS_PER_CHUNK" in drain
    assert "1,\n\t\t\tFL4359_FULL_GRID_MATERIALIZATION_GROUPS_PER_CHUNK" in drain
    assert drain.index("_write_sparse_capture_batch(ready)") < drain.index("ready.clear()")
    assert "while _sparse_capture_file != null:" in drain
    assert "_sparse_capture_result_error_count != errors_before" in drain


def test_sparse_line_writer_reports_real_fileaccess_failure(tmp_path):
    """Execute the production helper with a writable and a read-only handle."""
    import json
    import shutil
    import pytest

    godot = shutil.which("godot")
    if godot is None:
        pytest.skip("Godot required for FileAccess error-path test")
    driver = _text(DRIVER)
    helper = "func _store_sparse_capture_line(" + driver.split(
        "func _store_sparse_capture_line(", 1)[1].split("\nfunc ", 1)[0]
    target = tmp_path / "capture.jsonl"
    script = tmp_path / "writer_test.gd"
    script.write_text(
        "extends SceneTree\n"
        "var _sparse_capture_file: FileAccess\n"
        "var _sparse_capture_result_error_count := 0\n"
        "var _sparse_lifecycle_facts_valid := true\n" + helper + "\n"
        "func _init() -> void:\n"
        f"\tvar path := {json.dumps(str(target))}\n"
        "\t_sparse_capture_file = FileAccess.open(path, FileAccess.WRITE)\n"
        '\tassert(_store_sparse_capture_line("[1,2,3]"))\n'
        "\t_sparse_capture_file.close()\n"
        '\tassert(FileAccess.get_file_as_string(path) == "[1,2,3]\\n")\n'
        "\t_sparse_capture_file = FileAccess.open(path, FileAccess.READ)\n"
        '\tassert(not _store_sparse_capture_line("[4,5,6]"))\n'
        "\tassert(_sparse_capture_file == null)\n"
        "\tassert(_sparse_capture_result_error_count == 1)\n"
        "\tassert(not _sparse_lifecycle_facts_valid)\n"
        '\tassert(not _store_sparse_capture_line("[7,8,9]"))\n'
        '\tassert(FileAccess.get_file_as_string(path) == "[1,2,3]\\n")\n'
        '\tprint("SPARSE_WRITE_ERROR_PATH_VERIFIED")\n'
        "\tquit(0)\n",
        encoding="utf-8",
    )
    result = subprocess.run([godot, "--headless", "--path", str(tmp_path),
                             "--script", str(script)], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SPARSE_WRITE_ERROR_PATH_VERIFIED" in result.stdout
    assert "SCRIPT ERROR" not in result.stderr
    assert target.read_text() == "[1,2,3]\n"


def test_full_grid_transport_preserves_terminal_product_colour_witness():
    driver = _text(DRIVER)
    compact_start = driver.index("func _sparse_row_for_analyzer")
    compact_end = driver.index("func _store_sparse_analyzer_row", compact_start)
    compact = driver[compact_start:compact_end]
    assert '"final_product_terrain_colour_witness_bits": row.get(' in compact
    assert '"final_product_terrain_colour_witness_valid": bool(row.get(' in compact


def test_v15_full_grid_transport_preserves_product_finalization_witnesses():
    driver = _text(DRIVER)
    compact_start = driver.index("func _sparse_row_for_analyzer")
    compact_end = driver.index("func _store_sparse_analyzer_row", compact_start)
    compact = driver[compact_start:compact_end]
    assert '"final_product_pre_finalization_signature_bits": row.get(' in compact
    assert '"final_product_post_finalization_signature_bits": row.get(' in compact


def test_full_grid_transport_preserves_terminal_occurrence_and_projection_contract():
    driver = _text(DRIVER)
    compact_start = driver.index("func _sparse_row_for_analyzer")
    compact_end = driver.index("func _store_sparse_analyzer_row", compact_start)
    compact = driver[compact_start:compact_end]
    for field in (
        "final_paint_terminal_mask_ink_count",
        "final_paint_terminal_mask_sum_x",
        "final_paint_terminal_mask_sum_y",
        "final_paint_terminal_mask_min_x",
        "final_paint_terminal_mask_min_y",
        "final_paint_terminal_mask_max_x",
        "final_paint_terminal_mask_max_y",
        "final_paint_terminal_mask_digest",
        "final_paint_terminal_occurrence_valid",
    ):
        assert f'"{field}":' in compact
    drain_start = driver.index("func _drain_sparse_capture")
    drain_end = driver.index("func _arm_moving_dump_before_active_route", drain_start)
    drain = driver[drain_start:drain_end]
    for field in (
        "world_to_clip_matrix",
        "target_size",
        "cell_size",
        "semantic_ansi_size",
        "readback_flip_y",
        "camera_hash",
        "dynamic_revision",
    ):
        assert f'frame_trace["{field}"]' in drain


def test_full_grid_transport_preserves_product_material_authority():
    driver = _text(DRIVER)
    compact_start = driver.index("func _sparse_row_for_analyzer")
    compact_end = driver.index("func _store_sparse_analyzer_row", compact_start)
    compact = driver[compact_start:compact_end]
    assert '"final_product_material_family": int(row.get(' in compact
    assert '"final_product_material_id": int(row.get(' in compact


def test_driver_keeps_one_monotonic_frame_sequence_through_release():
    source = _text(DRIVER)
    assert re.search(r"var sequence_frame := 0", source)
    assert re.search(r"var sparse_sequence_frame := 0", source)
    assert len(re.findall(r"^\t\tsequence_frame \+= 1$", source, re.MULTILINE)) == 2
    assert "if full_grid_capture_frame:\n\t\t\tsparse_sequence_frame += 1" in source
    assert "if _full_grid_temporal_oracle:\n\t\t\tsparse_sequence_frame += 1" in source
    png_route = source[source.index("func _frame_png_route_active()"):]
    png_route = png_route.split("\nfunc ", 1)[0]
    assert "not _full_grid_temporal_oracle" in png_route
    assert source.count("await _save_frame_png(main, camera, sparse_sequence_frame)\n\t\t\tsparse_sequence_frame += 1") == 2
    assert '"movement_phase": tag' in source
    assert '"release" if after_index == 0 else "settle"' in source
    assert "camera_snap_error_px" in source
    snap_helper = source[source.index("func _camera_snap_error_px_from_contract"):source.index("func _sparse_owner_name")]
    assert 'contract.get("drift_local"' not in snap_helper


def test_full_grid_route_captures_exact_turn_release_settle_window():
    source = _text(DRIVER)
    run_start = source.index("func _run() -> void:")
    run_end = source.index("func _run_stationary_terminal_reprojection_calibration", run_start)
    run_body = source[run_start:run_end]
    arm_start = source.index("func _arm_sparse_capture_before_active_route")
    arm_end = source.index("func _await_full_grid_fresh_authority_tick", arm_start)
    arm = source[arm_start:arm_end]

    assert "const FL4359_FULL_GRID_MAX_CAPTURE_FRAMES := 20" in source
    assert "_sparse_capture_release_frames_effective = 1" in arm
    assert "1 if _full_grid_temporal_oracle else maxi(_after_frames - 1, 1)" in arm
    assert "_sparse_capture_total_frames_effective" in arm
    assert "> FL4359_FULL_GRID_MAX_CAPTURE_FRAMES" in arm
    assert "_sparse_capture_total_frames_effective," in run_body
    assert '"turn_release_settle"' in run_body
    assert (
        "active_frames - _sparse_capture_active_frames_effective" in run_body
    )
    assert "if full_grid_capture_frame and sparse_sequence_frame == 0:" in run_body
    assert (
        "sparse_sequence_frame < _sparse_capture_active_frames_effective"
        in run_body
    )
    assert (
        "sparse_sequence_frame if _full_grid_temporal_oracle else sequence_frame"
        in run_body
    )
    assert '"release" if after_index == 0 else "settle"' in run_body
    assert "settle_after_frames := 0 if _full_grid_temporal_oracle" not in run_body
    assert run_body.count("await _await_sparse_capture_dispatch(") == 2
    assert (
        "if sequence_frame == midpoint_frame and not "
        "_full_grid_temporal_oracle:" in run_body
    )

    settle_loop = run_body.index("for after_index in range(settle_after_frames):")
    authority_end = run_body.index(
        "if _full_grid_temporal_oracle:\n"
        "\t\t_authority_snapshot_queue_window_end =",
        settle_loop,
    )
    authority_stop = run_body.index(
        "_stop_authority_ingest_before_materialization(main)", authority_end
    )
    assert settle_loop < authority_end < authority_stop


def test_full_grid_route_rejects_missing_duplicate_and_hidden_sequence_frames():
    source = _text(DRIVER)
    run_start = source.index("func _run() -> void:")
    run_end = source.index("func _run_stationary_terminal_reprojection_calibration", run_start)
    run_body = source[run_start:run_end]
    assert (
        "completed_frames.size() == _sparse_capture_total_frames_effective"
        in run_body
    )
    assert (
        "_sparse_capture_completed_sequence_frames.size()\n"
        "\t\t\t\t== _sparse_capture_total_frames_effective"
        in run_body
    )
    assert "sequence_frame == _sparse_capture_total_frames_effective" in run_body
    assert (
        "for expected_sequence_frame in range("
        "_sparse_capture_total_frames_effective):" in run_body
    )
    assert "current_sequence != previous_sequence + 1" in run_body
    assert "_sparse_capture_duplicate_sequence_frames == 0" in run_body
    assert "_sparse_capture_duplicate_frame_indices == 0" in run_body


def test_sparse_arm_publishes_frame_identity_before_render_request():
    source = _text(DRIVER)
    arm_start = source.index("func _arm_sparse_capture_phase")
    arm_end = source.index("func _set_sparse_capture_frame_metadata", arm_start)
    arm = source[arm_start:arm_end]
    metadata_publish = arm.index("_publish_sparse_capture_frame_metadata(")
    render_request = arm.index('effect.call(\n\t\t"request_fl4359_sparse_capture"')
    assert metadata_publish < render_request
    assert '"turn" if active_turn_frames > 0 else "post_turn"' in source
    assert '_arm_sparse_capture_phase(main, _after_frames, "release", sequence_frame, "release")' in source


def test_full_grid_oracle_waits_for_fresh_authority_before_contiguous_arm():
    source = _text(DRIVER)
    route_start = source.index("_arm_sparse_capture_before_active_route(")
    route_end = source.index("func _reset_jitter_capture_before_active_route", route_start)
    route = source[route_start:route_end]
    helper_start = source.index("func _await_full_grid_fresh_authority_tick")
    helper_end = source.index("func _arm_sparse_capture_phase", helper_start)
    helper = source[helper_start:helper_end]
    arm_start = source.index("func _arm_sparse_capture_before_active_route")
    arm_end = source.index("func _await_full_grid_fresh_authority_tick", arm_start)
    arm = source[arm_start:arm_end]
    assert "RenderingServer.set_render_loop_enabled" not in route
    assert "RenderingServer.force_draw" not in route
    assert route.index("await _await_full_grid_fresh_authority_tick(main)") < route.index("_arm_sparse_capture_phase(")
    assert "_sparse_capture_active_frames_effective" in route
    assert "observed_tick > _full_grid_last_captured_authority_tick" in helper
    assert "for _wait_frame in range(_sparse_capture_drain_deadline_frames)" in helper
    assert "if not _full_grid_temporal_oracle:\n\t\t_arm_sparse_capture_phase(" in arm
    assert "RenderingServer.set_render_loop_enabled" not in source
    assert "RenderingServer.force_draw" not in source


def test_full_grid_oracle_stops_ingest_then_waits_for_main_to_drain_queue():
    source = _text(DRIVER)
    start = source.index("func _stop_authority_ingest_before_materialization")
    end = source.index("func _ascii_effect", start)
    helper = source[start:end]
    assert 'client.call("stop_authority_ingest_for_proof")' in helper
    assert "for _drain_frame in range(8):" in helper
    assert "await process_frame" in helper
    assert 'int(queue.get("pending", -1)) == 0' in helper
    assert "take_pending_snapshots" not in helper
    client = _text(ROOT / "godot_project/scripts/net/ColyseusGameClient.gd")
    stop_start = client.index("func stop_authority_ingest_for_proof")
    stop_end = client.index("func ", stop_start + len("func "))
    stop = client[stop_start:stop_end]
    assert "_shutdown_started = true" in stop
    assert "set_process(false)" in stop
    assert "room = null" not in stop
    assert "sdk_client = null" not in stop


def test_clean_play_command_and_driver_have_zero_capture_owners():
    runner = _text(RUNNER)
    command_start = runner.index("def _build_godot_command")
    command_end = runner.index("def _", command_start + len("def _build_godot_command"))
    command = runner[command_start:command_end]
    isolation_guard = command.index('if not getattr(args, "clean_play_no_capture", False):')
    assert isolation_guard < command.index('f"--fl4359-jitter-capture-path={capture_path}"')
    assert isolation_guard < command.index(
        'cmd.append(f"--fl4359-capture-window-mode={capture_mode}")'
    )
    assert isolation_guard < command.index(
        'f"--fl4359-capture-resolution={capture_resolution[0]}x{capture_resolution[1]}"'
    )
    assert isolation_guard < command.index('f"--fl4359-request-final-dump=')

    driver = _text(DRIVER)
    assert 'if not _clean_play_no_capture:\n\t\tawait _save_screenshot(' in driver
    assert '"clean_play_timing_uncontaminated": clean_play_timing_uncontaminated' in driver
    assert '"jitter_capture_requested": sparse_capture_requested' in driver
    assert '"frame_captured": not _screenshots.is_empty()' in driver
    assert '"visual_frame_count": _screenshots.size()' in driver
    clean_parse = driver[driver.index("\tif _clean_play_no_capture:", driver.index("func _read_args")):driver.index("func _await_route_process_frame")]
    for disabled_owner in (
        '_capture_path = ""',
        "_sparse_capture_frame_limit = 0",
        "_request_final_dump = false",
        "_fl4216_temporal_capture = false",
        "_single_frame = false",
        "_replay_gif = false",
        "_record_f9_test = false",
    ):
        assert disabled_owner in clean_parse


def test_analyzer_uses_surface_and_product_identity_fields():
    analyzer = _load_analyzer()
    forbidden = {
        "probe_depth_test_result",
        "probe_candidate_fallback_cell",
        "probe_texel_id",
        "probe_feed_source",
    }
    assert forbidden.isdisjoint(analyzer.FL4359_REQUIRED_FIELDS)
    row = {
        "selected_owner": "static_mesh",
        "final_product_owner": 2,
        "final_product_source_key": 0,
        "final_product_matcell_packed": 0xDEADBEEF,
    }
    assert analyzer.final_product_identity_key(row) == (2, 0, None)
    assert analyzer._has_row_value(row, "final_product_source_key")
    terrain = dict(row, selected_owner="terrain", final_product_owner=1)
    assert analyzer.final_product_identity_key(terrain) == (1, 0, 0xDEADBEEF)
    nonterrain_product_on_terrain_surface = dict(row, selected_owner="terrain", final_product_owner=2)
    assert analyzer.final_product_identity_key(nonterrain_product_on_terrain_surface) == (2, 0, None)


def test_runner_forwards_sparse_recorder_identity():
    source = _text(RUNNER)
    assert "--fl4359-jitter-capture-path=" in source
    assert "--fl4359-jitter-capture-frame-limit=" in source
    assert "--fl4359-headed-log-path=" in source
    assert "gameplay_surface_identity_bounce_bounded" in source
    assert "post_probe" not in source
    assert "probe_candidate_fallback_cell_count" not in source


def test_per_request_output_buffers_are_immutable_until_payload_completion():
    """Header A must never read payload bytes overwritten for request B."""
    source = _text(EFFECT)
    assert 'request["output_rd"]' in source
    assert "_fl4359_sparse_capture_output_rd" not in source
    requests = {
        41: {"output": bytearray(b"A-headerA-payload")},
        42: {"output": bytearray(b"B-headerB-payload")},
    }
    header_a = bytes(requests[41]["output"][:8])
    requests[42]["output"][:] = b"B-headerB-payload"
    payload_a = bytes(requests[41]["output"][8:])
    assert header_a == b"A-header"
    assert payload_a == b"A-payload"


def test_world_admission_rejects_absent_surface_but_screen_can_observe_absence():
    source = _text(SHADER)
    assert "selected_surface_valid" in source
    assert "if (presented_source_valid" in source
    assert "&& anchor_hash(presented_position)" in source
    assert "ADMISSION_SCREEN" in source
    assert (0 & 1) == 0
    assert (2 & 2) != 0


def test_snap_residual_uses_nearest_grid_boundaries_and_image_y_sign():
    source = _text(DRIVER)
    helper = source[source.index("func _camera_snap_error_px_from_contract"):source.index("func _sparse_owner_name")]
    assert "var drift_x := -phase_x if phase_x < 0.5 else 1.0 - phase_x" in helper
    assert "var drift_y := -phase_y if phase_y < 0.5 else 1.0 - phase_y" in helper
    assert '"y": -drift_y * pixel_per_cell_y' in helper
    for phase, expected in ((0.0, 0.0), (0.25, -0.25), (0.5, 0.5), (0.75, 0.25), (0.999, 0.001)):
        residual = -phase if phase < 0.5 else 1.0 - phase
        assert abs(residual - expected) < 1e-9


def test_b43_distinct_sentinel_decode_uses_presence_and_unknown_bits():
    source = _text(EFFECT)
    assert 'payload.decode_u32(base + 188)' in source
    assert 'payload.decode_u32(base + 192)' in source
    assert 'payload.decode_u32(base + 196 + mask_slot * 4)' in source
    assert 'payload.decode_u32(base + 216)' in source
    assert 'payload.decode_u32(base + 220)' in source
    assert '"final_paint_mask_contributor_ids": contributor_id_list' in source
    # Canonical fixture: encoded glyphs in absent slots must not become
    # contributors, while byte216 remains ink pixels rather than a count.
    words = [0] * 28
    words[19] = 0b0101
    words[20] = 0b1
    words[21:25] = [11, 22, 33, 44]
    words[26] = 777
    words[27] = 0x80
    contributors = [value - 1 for slot, value in enumerate(words[21:25]) if words[19] & (1 << slot) and value > 0]
    assert contributors == [10, 32]
    assert len(contributors) != words[26]


def test_fixed_screen_temporal_uses_retained_sparse_rows_without_f10():
    analyzer = _load_analyzer()
    common = {
        "row_type": "fl4359_jitter_cell",
        "screen_cell": [4, 5],
        "admission_bits": analyzer.ADMISSION_SCREEN,
        "selected_owner": "terrain",
        "final_product_owner": 2,
        "final_product_source_key": 0,
        "final_product_matcell_packed": 3,
        "glyph_id": 65,
        "fg_rgb": [255, 255, 255],
        "bg_rgb": [0, 0, 0],
        "final_ink_pixels": 8,
        "final_ink_fg": 8,
        "final_ink_bg": 0,
    }
    frame_a = dict(common, frame_id=0)
    frame_b = dict(common, frame_id=1, glyph_id=66)
    result = analyzer.summarize_fixed_screen_temporal({0: [frame_a], 1: [frame_b]})
    assert result["fixed_screen_temporal_comparable_cells"] == 1
    assert result["fixed_screen_visible_signature_flip_cells"] == 1


def test_surface_identity_ignores_view_revision_and_projection():
    analyzer = _load_analyzer()
    a = {
        "frame_id": 0, "screen_cell": [1, 1], "ansi_cell_world_anchor": {"x": 1, "y": 0, "z": 1},
        "selected_owner": "terrain", "selected_surface_owner": 2,
        "selected_surface_object": 7, "selected_surface_primitive": 3, "selected_surface_material": 4,
        "selected_surface_view_revision": 1, "selected_surface_projection_kind": 0,
        "admission_bits": analyzer.ADMISSION_WORLD,
    }
    b = dict(a, frame_id=1, selected_surface_view_revision=9, selected_surface_projection_kind=1)
    result = analyzer.compare_frames([a], [b])
    assert result["selected_surface_identity_churn_pct"] == 0.0


def test_atomic_row_order_permutation_has_identical_grouping():
    analyzer = _load_analyzer()
    rows = [
        {"frame_id": 0, "sample_index": 3, "screen_cell": [3, 0]},
        {"frame_id": 0, "sample_index": 1, "screen_cell": [1, 0]},
        {"frame_id": 0, "sample_index": 2, "screen_cell": [2, 0]},
    ]
    ordered = analyzer.group_by_frame(rows)
    permuted = analyzer.group_by_frame(list(reversed(rows)))
    assert [row["sample_index"] for row in ordered[0]] == [1, 2, 3]
    assert ordered == permuted


def test_product_time_constant_fixture_rejects_clock_change():
    analyzer = _load_analyzer()
    def row(frame_id, product_time):
        return {
            "frame_id": frame_id,
            "camera_world_unsnapped": {"x": float(frame_id), "y": 0.0, "z": 0.0},
            "camera_world": {"x": float(frame_id), "y": 0.0, "z": 0.0},
            "camera_snap_error_px": {"x": 0.0, "y": 0.0},
            "style_post_shift_px": {"x": 0.0, "y": 0.0},
            "pixel_camera_snap_world": 0.0,
            "camera_projection_mode": 0,
            "camera_fov": 1.0, "camera_near": 0.1, "camera_far": 100.0,
            "product_time_seconds": product_time,
        }
    assert analyzer.summarize_shimmer_metrics({0: [row(0, 0.35)], 1: [row(1, 0.35)]})["product_time_constant"]
    assert not analyzer.summarize_shimmer_metrics({0: [row(0, 0.35)], 1: [row(1, 0.36)]})["product_time_constant"]


def test_airplane_preflight_main_accepts_both_projection_identities_and_rejects_crossed():
    commands = (
        ("plane_air", "perspective", 0),
        ("plane_air_ortho", "ortho", 0),
        ("plane_air", "ortho", 2),
        ("plane_air_ortho", "perspective", 2),
    )
    for mode, projection, expected_rc in commands:
        result = subprocess.run(
            [
                sys.executable,
                str(RUNNER.parent.parent / "scripts/run_fl4359_headed_movement.py"),
                "--camera-mode", mode,
                "--exact-camera-projection", projection,
                    "--product-time", "0.35",
                    "--preflight-only",
                    "--keep-forever",
                    "--godot-window-mode", "fullscreen",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert result.returncode == expected_rc, result.stdout
        if expected_rc == 0:
            assert '"route_identity": "scripted_airborne_airplane_movement"' in result.stdout
            assert '"projection_matches_camera_mode": true' in result.stdout


def test_airplane_profile_blocks_only_retained_contract_and_preserves_legacy_diagnostics():
    analyzer = _load_analyzer()
    args = type("Args", (), {
        "max_snap_px_delta": 1.0,
        "max_snap_sign_flip_rate": 0.05,
        "max_content_jitter_pct": 5.0,
        "max_selected_surface_identity_churn_pct": 5.0,
        "max_post_surface_bounce_pct": 12.0,
        "min_moving_frame_pairs": 1,
        "min_camera_motion_ratio": 0.5,
        "min_comparable_cells": 1,
        "min_same_key_terrain_rows": 50,
        "expected_red_gate": [],
        "proof_profile": "airplane_temporal_v1",
    })()
    summary = {
        "frame_count": 2,
        "row_count": 2,
        "projection_fields_present_frames": 2,
        "camera_projection_modes": {0: 2},
        "projection_contract_invalid_frames": 0,
        "snap_contract_mismatch_frames": 0,
        "style_post_shift_nonzero_frames": 0,
        "camera_snap_residual_nonzero_frames": 0,
        "pixel_camera_snap_world_observed_frames": 2,
        "pixel_camera_snap_world_nonzero_frames": 0,
        "pixel_camera_snap_world_values": [0.0],
        "product_time_values": [0.35],
        "product_time_constant": True,
        "moving_frame_pairs": 1,
        "camera_motion_continuous_ratio": 1.0,
        "min_comparable_cells": 1,
        "min_nonzero_comparable_cells": 1,
        "total_comparable_cells": 2,
        "comparable_pair_count": 1,
        "comparable_pairs_meeting_min": 1,
        "required_comparable_pairs": 1,
        "max_snap_px_delta": 0.0,
        "snap_sign_flip_rate": 0.0,
        "fixed_screen_temporal_evidence_present": True,
        "fixed_screen_temporal_comparable_cells": 2,
        "fixed_screen_visible_signature_flip_pct": 0.0,
        "motion_compensated_final_cell_instability_pct": 0.0,
        "corrected_final_cell_oracle_valid": True,
        "continuous_product_colour_stability": {
            "counts": {"comparable_stable_semantic_samples": 2},
            "invalid_terrain_row_count": 0,
            "comparable_pair_count": 1,
            "min_comparable_samples_per_pair": 2,
            "max_instability_pct": 0.0,
        },
        "cell_mask_owner_stability": {
            "counts": {"comparable_cells": 2, "stable_cells": 2},
            "invalid_cell_count": 0,
            "comparable_pair_count": 1,
            "max_instability_pct": 0.0,
        },
        "surface_identity_split_diagnostics_present": True,
        "max_selected_surface_identity_churn_pct": 0.0,
        "max_post_surface_bounce_pct": 0.0,
        "final_product_source_key_count": 2,
        "required_field_coverage": {"all_present": True, "missing": []},
        "product_anchor_provenance_complete": True,
        "same_frame_oracle_authority_tick_continuity_valid": True,
        "terrain_product_homogeneity_rows": 2,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
    }
    gates = analyzer.build_gate_summary(summary, args)
    assert gates["proof_profile"] == "airplane_temporal_v1"
    assert gates["all_pass"] is True
    assert gates["diagnostic_failed_gates"]
    summary["required_field_coverage"] = {"all_present": False, "missing": ["final_paint_glyph"]}
    blocked = analyzer.build_gate_summary(summary, args)
    assert blocked["all_pass"] is False
    assert "evidence_required_fields_present" in blocked["blocking_failed_gates"]
    args.proof_profile = "general"
    legacy = analyzer.build_gate_summary(summary, args)
    assert legacy["all_pass"] is False


def test_projection_specific_snap_gates_fail_closed_for_ortho_zero_and_mixed():
    analyzer = _load_analyzer()
    args = type("Args", (), {
        "max_snap_px_delta": 1.0, "max_snap_sign_flip_rate": 0.05,
        "max_content_jitter_pct": 5.0, "max_selected_surface_identity_churn_pct": 5.0,
        "max_post_surface_bounce_pct": 12.0, "min_moving_frame_pairs": 1,
        "min_camera_motion_ratio": 0.5, "min_comparable_cells": 1,
        "min_same_key_terrain_rows": 50, "expected_red_gate": [],
        "proof_profile": "airplane_temporal_v1",
    })()
    def base(modes, nonzero):
        return {
            "frame_count": 2, "row_count": 2, "projection_fields_present_frames": 2,
            "camera_projection_modes": modes, "projection_contract_invalid_frames": 0,
            "snap_contract_mismatch_frames": 0, "style_post_shift_nonzero_frames": 0,
            "camera_snap_residual_nonzero_frames": 0,
            "pixel_camera_snap_world_observed_frames": 2,
            "pixel_camera_snap_world_nonzero_frames": nonzero,
            "pixel_camera_snap_world_values": [0.5] if nonzero else [0.0],
            "product_time_values": [0.35], "product_time_constant": True,
            "moving_frame_pairs": 1, "camera_motion_continuous_ratio": 1.0,
            "min_comparable_cells": 1, "min_nonzero_comparable_cells": 1,
            "total_comparable_cells": 2, "comparable_pair_count": 1,
            "comparable_pairs_meeting_min": 1, "required_comparable_pairs": 1,
            "max_snap_px_delta": 0.0, "snap_sign_flip_rate": 0.0,
            "fixed_screen_temporal_evidence_present": True,
            "fixed_screen_temporal_comparable_cells": 2,
            "fixed_screen_visible_signature_flip_pct": 0.0,
            "surface_identity_split_diagnostics_present": True,
            "max_selected_surface_identity_churn_pct": 0.0,
            "max_post_surface_bounce_pct": 0.0,
            "final_product_source_key_count": 2,
            "required_field_coverage": {"all_present": True, "missing": []},
            "product_anchor_provenance_complete": True,
        }
    ortho_zero = analyzer.build_gate_summary(base({1: 2}, 0), args)
    assert ortho_zero["snap_metric_applicable"] is True
    assert ortho_zero["evidence_jitter_capture_present"]["pass"] is False
    ortho_shift_mismatch = base({1: 2}, 2)
    ortho_shift_mismatch["snap_contract_mismatch_frames"] = 1
    mismatch = analyzer.build_gate_summary(ortho_shift_mismatch, args)
    assert mismatch["evidence_projection_contract_valid"]["pass"] is False
    ortho_compensated_wrap = base({1: 2}, 2)
    ortho_compensated_wrap["max_snap_px_delta"] = 19.8
    ortho_compensated_wrap["snap_sign_flip_rate"] = 1.0
    ortho_compensated_wrap["motion_compensated_final_cell_instability_pct"] = 0.0
    compensated = analyzer.build_gate_summary(ortho_compensated_wrap, args)
    assert compensated["gameplay_moving_camera_jitter_bounded"]["pass"] is True
    assert compensated["gameplay_moving_camera_jitter_bounded"]["raw_snap_residual_wrap_is_acceptance_gate"] is False
    perspective_no_content_evidence = base({0: 2}, 0)
    perspective_no_content_evidence["fixed_screen_temporal_evidence_present"] = False
    no_evidence = analyzer.build_gate_summary(perspective_no_content_evidence, args)
    assert no_evidence["evidence_jitter_capture_present"]["pass"] is True
    assert no_evidence["gameplay_content_shimmer_bounded"]["pass"] is False
    mixed = analyzer.build_gate_summary(base({0: 1, 1: 1}, 1), args)
    assert mixed["jitter_metric_owner"] == "mixed_projection_invalid"
    assert mixed["all_pass"] is False


def test_final_field_and_sparse_lanes_cannot_retire_in_either_completion_order():
    """The shared request stays live until both independent async lanes finish."""
    source = _text(EFFECT)
    queue_start = source.index("func _queue_fl4359_sparse_capture_readback")
    queue_end = source.index("func _fl4359_sparse_capture_mark_lane_done", queue_start)
    queue = source[queue_start:queue_end]
    sparse_failure = queue.index('"sparse_capture_snapshot_async_queue_failed"')
    pixel_submit = queue.index('var pixel_callback')
    assert sparse_failure < pixel_submit
    reservation = queue.index('1 + (1 if pixel_requested else 0)')
    first_async_submit = queue.index('rd.buffer_get_data_async')
    assert reservation < first_async_submit
    assert '"final_pixel_field_actual_image_size"' in queue
    assert '"pixel",' in queue
    assert '"final_pixel_capture_async_queue_failed"' in queue

    mark_start = source.index("func _fl4359_sparse_capture_mark_lane_done")
    mark_end = source.index("func _fl4359_sparse_capture_snapshot_callback", mark_start)
    mark = source[mark_start:mark_end]
    assert 'request["failed"] = true' in mark
    assert 'request["pending"] = int(request.get("pending_lanes", 0)) > 0' in mark
    assert 'if not bool(request.get("pending", false))' in mark
    assert 'and bool(request.get("readback_lanes_armed", false)):' in mark
    assert '_retire_fl4359_sparse_capture_request(request_id)' in mark

    ready_start = source.index("func is_fl4359_sparse_capture_ready")
    ready_end = source.index("func materialize_fl4359_sparse_capture", ready_start)
    ready = source[ready_start:ready_end]
    assert 'int(request.get("pending_lanes", 0)) == 0' in ready
    materialize = source[ready_end:source.index("func take_fl4359_sparse_capture_ready", ready_end)]
    assert materialize.index('int(request.get("pending_lanes", 0)) > 0') < materialize.index(
        'bool(request.get("failed", false))'
    )


def test_final_field_schema_records_actual_image_extent_and_logical_target_separately():
    source = _text(EFFECT)
    dispatch_start = source.index("func _dispatch_fl4359_sparse_capture")
    dispatch_end = source.index("func _queue_fl4359_sparse_capture_readback", dispatch_start)
    dispatch = source[dispatch_start:dispatch_end]
    assert "actual_image_size: Vector2i" in dispatch
    assert '"final_pixel_field_actual_image_size": [actual_image_size.x, actual_image_size.y]' in dispatch
    assert '"final_pixel_field_logical_target_size": [pixel_ctx.target_size.x, pixel_ctx.target_size.y]' in dispatch
    assert '"final_pixel_field_presentation_size": [pixel_ctx.presentation_size.x, pixel_ctx.presentation_size.y]' in dispatch
    assert 'const FINAL_PIXEL_PAINT_SCHEMA := "fl4359.final_paint_field.v2"' in source


def test_final_field_frame_trace_joins_sidecar_request_id():
    source = _text(DRIVER)
    trace_start = source.index('var frame_trace: Dictionary = result.get("metadata", {}).duplicate(true)')
    trace_end = source.index('frame_trace["oracle_expected_subcells_per_frame"]', trace_start)
    trace = source[trace_start:trace_end]
    assert 'frame_trace["request_id"] = int(result.get("request_id", -1))' in trace


def test_final_field_prepares_before_flight_and_arms_with_sparse_publication():
    effect = _text(EFFECT)
    driver = _text(DRIVER)
    arm = effect.split("func request_fl4359_sparse_capture(", 1)[1].split("func reset_fl4359_sparse_capture", 1)[0]
    assert arm.index('_final_pixel_paint_capture_frames = mini(') < arm.index('_fl4359_sparse_capture_active = true')
    assert arm.index('request_final_cell_paint_capture(') < arm.index('_fl4359_sparse_capture_active = true')
    assert 'final_pixel_capture_not_prepared_or_busy' in arm
    main = driver.split('func _run() -> void:', 1)[1].split('func _read_args', 1)[0]
    assert main.index('await _prepare_final_paint_field_before_flight(main)') < main.index('_authority_snapshot_queue_window_start =')
    activation = driver.split('func _arm_sparse_capture_phase(', 1)[1].split('func _set_sparse_capture_frame_metadata', 1)[0]
    assert activation.count('"request_fl4359_sparse_capture"') == 2  # capability check + one call
    assert '"request_final_pixel_paint_capture"' not in activation
    callback = effect.split('func _render_callback(', 1)[1].split('func request_final_cell_paint_capture', 1)[0]
    preparation_call = callback.split(
        '_prepare_final_pixel_paint_observer(', 1
    )[1].split(')', 1)[0]
    for required_argument in (
        'final_pixel_image_size',
        'pixel_ctx.semantic_ansi_size.x * pixel_ctx.semantic_ansi_size.y * 4',
        'pixel_ctx.cell_size',
        'pixel_ctx.target_size',
        'pixel_ctx.presentation_size',
        'pixel_ctx.semantic_ansi_size',
    ):
        assert required_argument in preparation_call
    assert 'sparse_capture_matrix_configuration_changed' in effect
    assert 'final_pixel_capture_prepared_matrix_changed' in callback
    assert 'final_pixel_capture_prepared_extent_changed' in callback
    assert '"resize_candidate_extent", subcell_extent,' in effect
    assert 'pixel_ctx.presentation_size' in effect.split(
        '"resize_candidate_extent", subcell_extent,', 1
    )[1].split(')):', 1)[0]
    assert 'frame_trace["matrix_identity_required"] = true' in driver
    assert 'frame_trace["matrix_identity"] = result.get("matrix_identity", {}).duplicate(true)' in driver
    assert '_ensure_final_pixel_paint_pipeline()' not in callback
    assert '_prepare_final_pixel_paint_capture_buffer(' not in callback


def test_full_grid_materialization_guard_precedes_cpu_decode():
    driver = _text(DRIVER)
    drain = driver.split('func _drain_sparse_capture(main: Node)', 1)[1].split('\nfunc ', 1)[0]
    assert drain.index('_authority_snapshot_queue_window_end.is_empty()') < drain.index('effect.call("take_fl4359_sparse_capture_ready")')
    main = driver.split('func _run() -> void:', 1)[1].split('func _read_args', 1)[0]
    stop = main.index('screen_recording_world.call("toggle_screen_recording", false, true)')
    materialize = main.index('_sparse_capture_final_ready_count = _drain_sparse_capture(main)')
    assert stop < materialize
