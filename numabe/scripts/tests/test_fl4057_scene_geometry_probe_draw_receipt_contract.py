"""FL-4057 retained-geometry semantic draw descriptor contract."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "godot_project/render/scene/SceneGeometryDB.gd"


def _source() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"(?m)^func\s+{re.escape(name)}\s*\(", source)
    assert match is not None, f"missing SceneGeometryDB function {name}"
    start = match.start()
    next_function = re.search(r"(?m)^func\s+", source[match.end() :])
    end = len(source) if next_function is None else match.end() + next_function.start()
    return source[start:end]


def test_descriptor_reads_retained_families_without_crossing_gpu_ownership() -> None:
    source = _source()
    body = _function_body(source, "build_perspective_probe_draw_receipt")

    assert "func build_perspective_probe_draw_receipt() -> Dictionary" in body
    for family_field in (
        '"terrain"',
        '"water"',
        '"static"',
        '"dynamic"',
        '"draw_sources"',
        '"counts"',
        '"source_revisions"',
        '"missing_fields"',
        '"pass"',
        '"camera_independent"',
    ):
        assert family_field in body

    for publication in (
        "_heightfield_descriptor",
        "_water_descriptor",
        "_instances",
        "get_dynamic_mover_descriptors_pinned",
        "_dynamic_mover_face_rows",
    ):
        assert publication in body

    for forbidden in (
        "binding_rids",
        "binding_byte_sizes",
        "RenderingDevice",
        "build_vsb_scene_bindings",
        "build_vsb_semantic_static_fact_bindings",
        "build_vsb_semantic_dynamic_fact_bindings",
        "buffer_create",
        "buffer_copy",
        "to_byte_array",
        "stable_sample_publication",
        "stable_world_sample_publication",
        "view_revision",
        '"view"',
        '"projection"',
        '"projection_kind"',
        "projection_matrix",
        "GPU",
        "bytes",
        "atlas",
    ):
        assert forbidden not in body
    assert re.search(r"\bRID\b", body) is None


def test_descriptor_exposes_exact_ranges_and_fail_closed_revisions() -> None:
    source = _source()
    body = _function_body(source, "build_perspective_probe_draw_receipt")

    assert "PERSPECTIVE_PROBE_DRAW_RECEIPT_SCHEMA_VERSION := 2" in source
    assert '"actual_geometry_semantic_v2"' in body
    assert '"a3d_heightfield_coarse_capture_v2"' in body

    for field in (
        '"terrain_lattice_dims"',
        '"terrain_cell_count"',
        '"origin_world_xz"',
        '"cell_size_xz"',
        '"height_min"',
        '"height_max"',
        '"bounds_valid"',
        '"coarse_height_bounds"',
        '"coarse_height_bounds_dims"',
        '"coarse_tile_cells"',
        '"water_span_count"',
        '"water_cell_count"',
        '"water_backstop_enabled"',
        '"static_face_count"',
        '"static_instance_count"',
        '"dynamic_face_count"',
        '"dynamic_mover_count"',
        '"scene_revision"',
        '"terrain_revision"',
        '"water_revision"',
        '"static_revision"',
        '"dynamic_revision"',
        '"camera_independent"',
        '"perspective_probe_draw_authority_missing"',
    ):
        assert field in body
    for range_field in ("first_face", "face_count", "mover_index"):
        assert f'"{range_field}"' in body
    # FL-4691: static ranges are retained by the packet owner. Check that owner
    # and its consumer instead of requiring the field to be rebuilt per frame.
    static_packet = _function_body(source, "_perspective_probe_static_draw_packet")
    assert '"instance_index"' in static_packet
    assert '_perspective_probe_static_draw_packet(static_content_digest())' in body
    for revision_field in (
        '"terrain.height_revision"',
        '"terrain.world_bounds"',
        '"terrain.height_bounds"',
        '"terrain.coarse_height_bounds"',
        '"water.water_revision"',
        '"dynamic.publication_result"',
        '"scene_revision"',
    ):
        assert revision_field in body
    assert "PackedVector2Array" in body


def test_dynamic_admission_result_is_compile_checked_and_stale_rows_are_blocked() -> None:
    source = _source()
    body = _function_body(source, "build_perspective_probe_draw_receipt")
    setter = _function_body(source, "set_dynamic_mover_descriptors")

    assert "class DynamicPublicationResult extends RefCounted" in source
    assert "-> DynamicPublicationResult" in source
    assert "func as_dictionary() -> Dictionary" in source
    assert "DYNAMIC_PUBLICATION_STATUS_ACCEPTED_CHANGED" in source
    assert "DYNAMIC_PUBLICATION_STATUS_ACCEPTED_UNCHANGED" in source
    assert "DYNAMIC_PUBLICATION_STATUS_REJECTED_ADMISSION" in source
    assert "set_dynamic_mover_descriptors_result" in source
    assert "_record_dynamic_mover_publication_result" in setter
    assert "_reject_dynamic_mover_publication" in setter
    assert "dynamic_revision = -1" in body
    assert "dynamic_face_count := -1" in body
    assert "dynamic_mover_count := -1" in body
