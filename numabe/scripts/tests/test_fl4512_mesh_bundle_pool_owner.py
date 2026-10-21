#!/usr/bin/env python3
"""Protect the compiler-owned FL-4512 mesh-bundle row.

The offline admission compiler is the only component allowed to choose the
candidate glyphs for ``mesh_feature.bundle.v1``.  These tests keep the runtime
role-level coordinate observable while proving that neither editor surface can
address, copy, rebuild, nor publish that candidate row.
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text()
EDITOR = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
FRAME = (ROOT / "engine/render/render_frame_input.h").read_text()
RESOLVE = (ROOT / "engine/render/render_resolve.cpp").read_text()
EXACT_SELECTOR = (ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp").read_text()
U7_SCORE = (ROOT / "apps/fl4512-runtime/engine/fl4512_u7_score.h").read_text()
FINAL_TUPLE_OWNER = (
    ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
).read_text()
MATERIAL_POOL_DRAFT = (
    ROOT / "web/asciiid-authoring-shell/src/materialPoolDraft.ts"
).read_text()
UI_REGISTRY = json.loads(
    (ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json").read_text()
)
# Post admission cutover: the mesh row is the strict admission artifact's
# selected_row (exact stored order, canonical ascending GlyphId), NOT a
# profile-authored array. Source it from the artifact so this stays authoritative.
MESH_STRUCTURAL_GIDS = [
    row["glyph_id"]
    for row in json.loads(
        (ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json").read_text()
    )["selected_row"]
]


def test_runtime_has_exactly_one_mesh_bundle_pool_coordinate():
    assert "kFl4512ProfileMeshBundleBucket = 31" in RESOLVER
    assert "kFl4260ProfileBucketCount = 33" in RESOLVER
    assert "Fl4260V1Bucket mesh_bundle" in RESOLVER
    assert "mesh_feature[3]" not in RESOLVER
    assert "31 + mesh_feature" not in PRESENTER
    assert "31+mesh_feature" not in PRESENTER
    assert "profile_bucket_count = 33" in FRAME


def test_runtime_loader_rejects_kind_addressed_rows_and_editor_has_no_bundle_lane():
    assert "Fl4512LoadCompiledProfileMetadataV1" in RESOLVER
    assert "Fl4260LoadV1ProfileTable" not in RESOLVER
    assert "Fl4512WorkspaceCompiledPoolPath" in RESOLVER
    assert "kFl4512CompiledPoolPath" not in RESOLVER
    assert "kFl4260V1ProfilesPath" not in RESOLVER
    assert "Fl4512SetActiveProfileMeshBundle" not in RESOLVER
    assert "Fl4260SetActiveProfilePool" not in RESOLVER
    assert "Fl4260SetActiveProfileBuckets" not in RESOLVER
    assert "Fl4512SetActiveProfileMeshFeatureBucket" not in RESOLVER
    assert "FL4512_ROLE_LANE_MESH_FEATURE" not in EDITOR
    assert "FL4512_SET_MESH_FEATURE_BUCKET" not in EDITOR
    assert "FL4512_ROLE_LANE_MESH_BUNDLE" not in EDITOR
    assert 'strcmp(name, "mesh_bundle")' not in EDITOR


def test_persisted_row_has_normative_address_and_union_candidates():
    artifact = json.loads(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_text()
    )
    rows = [
        row for row in artifact["rows"]
        if row["profile_namespace"] == "mesh"
        and row["material_key"] == 200
        and row["role"] == "mesh_feature"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "mesh_feature"
    assert row["route_key"] == "mesh_feature.bundle.v1"
    assert row["lane"] == 0
    assert row["lane_name"] == "mesh_bundle"
    assert row["candidate_glyph_ids"] == MESH_STRUCTURAL_GIDS
    assert row["role_eligible_glyph_ids"] == MESH_STRUCTURAL_GIDS
    assert [item["glyph_id"] for item in row["candidate_descriptors"]] == MESH_STRUCTURAL_GIDS
    assert all(len(item["mask8_u64_le_hex"]) == 16 for item in row["candidate_descriptors"])


def test_offline_artifact_is_the_only_mesh_bundle_candidate_owner():
    # Ordinary candidate editing still exists for terrain lanes.  The mesh
    # bundle is absent from that generic writer, so no compatibility alias can
    # route a browser action into the compiler-owned row.
    assert "Fl4260ReplaceRoleLaneRange" in EDITOR
    assert "Fl4512CompileAndCommitProfileArtifact" in EDITOR
    assert "Fl4512ReloadCompiledPoolArtifact" not in EDITOR
    assert "rebuild the pinned GlyphProgram before restart" in EDITOR
    assert "FL4260_CLEAR_LIVE_PROFILE" not in EDITOR
    assert "FL4512_ROLE_LANE_MESH_BUNDLE, 0, 6000" not in EDITOR
    lane_names = EDITOR.split(
        "static const char* Fl4260RoleLaneFamilyName", 1
    )[1].split("static bool Fl4260RoleLaneFamilyFromName", 1)[0]
    assert '"mesh_bundle"' not in lane_names
    assert "mesh_bundle.0" not in MATERIAL_POOL_DRAFT


def test_asciiid_serializer_leaves_compiler_owned_mesh_bundle_byte_shape_untouched():
    serializer = EDITOR.split(
        "static bool Fl4260ApplyProfileStateToRoot", 1
    )[1].split("static bool Fl4260BuildProfileBatchBytes", 1)[0]
    assert "mesh_bundle_candidate_count" not in EDITOR
    assert "mesh_bundle_candidates" not in EDITOR
    assert "mesh_bundle" not in serializer
    assert 'cJSON_DeleteItemFromObject(rb, "mesh_feature")' not in serializer


def test_registry_candidate_actions_cannot_address_mesh_bundle_lane():
    # Every interaction schema is checked because the registry duplicates the
    # lane enum across add/remove/reorder/clear/restore/catalog actions.
    for interaction in UI_REGISTRY["interactions"]:
        payload = interaction.get("payload_schema") or {}
        lane_schema = (payload.get("properties") or {}).get("lane_id") or {}
        assert "mesh_bundle.0" not in (lane_schema.get("enum") or [])


def test_u7_bundle_build_replaces_single_best_feature_owner():
    assert "Fl4512BuildProjectedFeatureBundle" in RESOLVE
    assert "Fl4512ResolveMeshFeatureCell" not in RESOLVE
    assert "best_bits" not in RESOLVE
    assert "ambiguous" not in RESOLVE
    assert "merged_mask |= entries[i].mask" in RESOLVE
    assert "out->retention_ref_count >= 32" in RESOLVE
    assert "std::sort(entries, entries + ref_count" in RESOLVE
    assert "mesh_bundle_feature_kind_mask" in FRAME
    assert "mesh_feature_primitive_id" not in FRAME


def test_compiled_structural_presentation_admits_only_authored_feature_kinds():
    assert "Fl4512ProjectedFeatureExtentAdmissionForFeatureV2(" in RESOLVE
    assert "profile_key, f.feature_kind, &extent_admission" in RESOLVE
    assert "projected_feature_bundle.feature_kind_mask" in RESOLVE
    assert "material boundaries remain observed producer facts" not in RESOLVE


def test_projected_scale_retention_target_is_separate_from_raw_claim_owner():
    assert "Entry retention_entries[32]" in RESOLVE
    assert "extent_q < extent_admission.admission.exit_q" in RESOLVE
    assert "extent_q < extent_admission.admission.enter_q" in RESOLVE
    assert "out->retention_valid = 1" in RESOLVE
    assert "projected_feature_bundle.retention_valid" in RESOLVE
    assert "mesh_retention_valid" in FRAME
    assert "mesh_retention_merged_mask_8x8" in FRAME
    assert "mesh_retention_bundle_id" in FRAME
    raw_fact_owner = RESOLVE.split("const uint8_t auto_fact_mask =", 1)[1].split(";", 1)[0]
    assert "mesh_retention" not in raw_fact_owner


def test_mixed_kind_bundle_does_not_require_scalar_kind_for_routing():
    selector = RESOLVER.split("static uint32_t Fl4260ProfileSelectGlyph", 1)[1]
    selector = selector.split("GlyphId Fl4260GetRawSelection", 1)[0]
    assert "if (fact_mask & FL4512_CELL_FACT_MESH_FEATURE)" in selector
    assert "mesh_feature_kind >=" not in selector
    assert "b.mesh_bundle_valid[idx]" in PRESENTER


def test_exact_selector_mesh_role_uses_u7_integer_mask_score_not_global6():
    assert "harri_winner_fs" not in PRESENTER
    assert "FL4683_GLYPH_PROGRAM_SCORER_U7_MASK" in EXACT_SELECTOR
    assert "Fl4512ScoreMaskV1" in EXACT_SELECTOR
    assert "scene_fact.mesh_target_mask8" in EXACT_SELECTOR
    assert "FL4512_DESCRIPTOR_MASK8_TOPOLOGY" in FINAL_TUPLE_OWNER
    assert "Fl4512PickBestMaskV1" in RESOLVER
    assert "70u * h + 15u * dE + 15u * dJ" in U7_SCORE
    assert "return Fl4260MultiBucketPickBestL2(lanes, 1, src6" not in RESOLVER
    assert "s_cell_ramp_upload[idx] = (int32_t)(uint32_t)mask" not in PRESENTER
    # ABI-B15: the former 18-float positional descriptor is deleted.  Each
    # scorer owns named source fields and a fixed transport record.
    assert "profile_candidate_descriptor_width" not in PRESENTER
    assert "profile_candidate_shape6" not in PRESENTER
    assert "profile_candidate_descriptor_width" not in FRAME
    assert "profile_candidate_shape6" not in FRAME
    assert "profile_candidate_global6" in FRAME
    assert "profile_candidate_u7_mask8_lo" in FRAME
    assert "profile_candidate_u7_endpoint_count" in FRAME
    assert "profile_candidate_pf_mask8_lo" in FRAME
    assert "harri_profile_candidate_global6_tex" not in PRESENTER
    assert "harri_profile_candidate_role_score_tex" not in PRESENTER
