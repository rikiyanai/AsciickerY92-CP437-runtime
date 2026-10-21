"""Executable contract for the editor's producer-owned bridge-v18 projection."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
SCHEMA = json.loads(
    (ROOT / "schemas/asciiid.state_snapshot.v1.json").read_text(encoding="utf-8")
)


def test_editor_projects_live_bridge_v18_and_rejects_live_v15():
    projection = EDITOR.split("cJSON* renderer_model", 1)[1]
    projection = projection.split("// temporal_history:", 1)[0]
    assert '"fl4260.bridge_cells.v18"' in projection
    assert '"fl4260.bridge_cells.v15"' not in projection
    assert "state_snapshot_bridge_schema_not_v18" in projection


def test_editor_lifts_the_complete_v18_observer_contract():
    projection = EDITOR.split("cJSON* production_observer", 1)[1]
    projection = projection.split("// temporal_history:", 1)[0]
    for field in (
        "active_stroke_frame",
        "active_stroke_lifecycle",
        "visibility_sheet_failure_count",
        "visibility_sheet_last_failure",
        "sequence_claim_conflicts",
    ):
        assert field in projection
    # FL-4512 U2 cut: the retired silhouette-promotion rows are physically
    # deleted, so their count-mismatch, row, and bounds rejection reasons are
    # deleted with them rather than left reachable-but-dead.
    for retired in (
        "silhouette_promotion_decisions",
        "state_snapshot_bridge_promotion_count_mismatch",
        "state_snapshot_bridge_promotion_row_invalid",
        "state_snapshot_bridge_promotion_bounds_invalid",
    ):
        assert retired not in projection
    for failure in (
        "state_snapshot_bridge_sequence_conflict_invalid",
        "state_snapshot_bridge_observer_bounds_invalid",
        "state_snapshot_bridge_observer_validity_type_invalid",
    ):
        assert failure in projection
    for authority in (
        "active_registry_sha256",
        "state_hash",
        "active_stroke_event_count",
        "visibility_sheet_last_failure",
        "bridge_observer_frame",
        "bridge_lifecycle_world_generation",
    ):
        assert authority in projection


def test_registered_snapshot_source_retires_direct_progression_owner():
    assert "mesh_feature_progression_stage" not in EDITOR, (
        "editor source still emits the retired mesh_feature_progression_stage field"
    )
    assert not (ROOT / "scripts" / "fl4652_seal_progression_prediction.mjs").exists()
    assert not (ROOT / "scripts" / "fl4652_native_progression_product_proof.mjs").exists()


def test_editor_copies_the_u6_final_tuple_without_shell_color_inference():
    projection = EDITOR.split("static const char* const copied_fields[]", 1)[1]
    projection = projection.split("// temporal_history:", 1)[0]
    for field in (
        "final_result_abi_version",
        "result_kind",
        "current_winner_gid",
        "u6_record_valid",
        "temporal_valid",
        "u6_presented_final_gid",
        "u6_final_fg",
        "u6_final_bg",
        "feature_refs_valid",
        "feature_refs_failure",
        "feature_refs_expected_count",
        "feature_refs_actual_count",
        "feature_refs",
        "mesh_feature_observer",
        "mesh_bundle_merged_mask_8x8",
        "u7_mask",
        "structural_sequence",
        "spacetime_track",
        "shape6_global_f32_bits",
    ):
        assert field in projection
    assert '"u6_presented_final_gid"' in projection
    assert "Fl4260GetActiveProfileColor" not in projection


def test_snapshot_groups_live_persistent_track_preimages():
    assert '"persistent_track_required", true' in EDITOR
    assert '"persistent_tracks"' in EDITOR
    assert '"temporal_history.model: no live temporal-trace serializer exists"' not in EDITOR


def test_spacetime_relation_trace_is_the_typed_v2_loader_projection():
    relation = SCHEMA["$defs"]["glyph_transform_relation_trace_v2"]
    assert relation["additionalProperties"] is False
    assert set(relation["required"]) == set(relation["properties"])
    assert (
        SCHEMA["$defs"]["spacetime_track"]["properties"]["relation"]["$ref"]
        == "#/$defs/glyph_transform_relation_trace_v2"
    )
    for field in (
        "load_failure",
        "aspect_address",
        "from_glyph_id",
        "to_glyph_id",
        "effective_affine_confidence_q0_16",
        "arithmetic_failure",
    ):
        assert f'\\"{field}\\":' in (
            ROOT / "platform/terminal_gl_present.cpp"
        ).read_text(encoding="utf-8")


def test_snapshot_grid_dimensions_come_from_same_frame_termpp_header():
    assert '"fl4207.termpp.rendered_buffer.v1"' in EDITOR
    assert 'cJSON_AddNumberToObject(renderer, "width_cells", rendered_width_cells)' in EDITOR
    assert 'cJSON_AddNumberToObject(renderer, "height_cells", rendered_height_cells)' in EDITOR
    renderer_projection = EDITOR.split('cJSON* renderer =', 1)[1].split('cJSON* renderer_model', 1)[0]
    assert "latest_editor_scene_frame" not in renderer_projection
