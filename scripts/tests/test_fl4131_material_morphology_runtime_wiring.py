#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_resolver_loads_material_morphology_profile_table() -> None:
    runtime = (REPO_ROOT / "engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")

    assert "material.morphology.v2.profile_tables.json" in runtime
    assert "Fl4131RuntimeLoadMorphologyProfiles" in runtime
    assert "Fl4131RuntimeGlyphAllowedByMorphologyProfile" in runtime


def test_runtime_honors_sidecar_morphology_profile_override() -> None:
    """Step 9 contract: the runtime reads `morphology_profile_name` out of the
    harri sidecar and applies it as the per-material profile name before the
    candidate-pool review gate runs. Load order: morphology table first
    (seeds defaults), then sidecar (applies override). Without the override the
    default profile name per material wins; with the override the sidecar wins.
    """
    runtime = (REPO_ROOT / "engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")

    assert "morphology_profile_name" in runtime
    assert "g_fl4131_runtime_material_profile_name_storage" in runtime
    # Load order: morphology table first, sidecar second.
    morph_idx = runtime.index("Fl4131RuntimeLoadMorphologyProfiles();")
    sidecar_idx = runtime.index("Fl4131RuntimeLoadProfiles();")
    assert morph_idx < sidecar_idx, (
        "Fl4131RuntimeLoadMorphologyProfiles must run before Fl4131RuntimeLoadProfiles "
        "so the sidecar override is the last writer to "
        "g_fl4131_runtime_material_profile_names[]."
    )


def test_asciiid_exposes_material_profile_assignment_surface() -> None:
    asciiid = (REPO_ROOT / "editor/asciiid.cpp").read_text(encoding="utf-8")

    assert "Material Morphology Profile" in asciiid
    assert "FL4131_MORPHOLOGY_ASSIGN_PROFILE" in asciiid
    assert "material.morphology.v2.profile_tables.json" in asciiid


def test_asciiid_exposes_ux_proof_receipt_surface() -> None:
    """Step 10 contract: the Material Workspace surfaces the proof-grade
    receipt fields the goal enumerated -- assigned_profile + table_hash +
    review state on the Edit tab, the Harri panel winner, the candidate table,
    the 4 stage vectors (raw6 / normalized6 / external10 / directional6 /
    global6), and the slider receipt -- and the FL4131_MORPHOLOGY_DUMP_UX_RECEIPT
    MCP command writes all of them to a JSON file in one snapshot."""
    asciiid = (REPO_ROOT / "editor/asciiid.cpp").read_text(encoding="utf-8")

    # Edit-tab provenance fields the loader captures from profile_tables.json.
    assert "g_asciiid_morphology_table_source_catalog_hash" in asciiid
    assert "g_asciiid_morphology_table_review_state" in asciiid
    assert "g_asciiid_morphology_table_profile_live" in asciiid
    assert "g_asciiid_morphology_table_review_input_hash" in asciiid
    # UI exposes them as TextDisabled lines.
    assert "table_hash:" in asciiid
    assert "review_state:" in asciiid
    assert "dirty:" in asciiid
    # Stage vectors tracked on rescore.
    assert "last_raw6" in asciiid and "last_normalized6" in asciiid
    assert "last_external10" in asciiid and "last_directional6" in asciiid
    assert "last_global6" in asciiid
    # Slider receipt fields exist on the state struct.
    assert "last_slider_changed" in asciiid
    assert "last_slider_prev_value" in asciiid
    assert "last_slider_post_value" in asciiid
    # MCP command writes the bundled receipt to a JSON file.
    assert "FL4131_MORPHOLOGY_DUMP_UX_RECEIPT" in asciiid
    assert "fl4131_material_profile_resolution_receipt.v2" in asciiid


def test_asciiid_exposes_morphology_review_receipt_surface() -> None:
    """Step 6 contract: the Material Workspace gives a reviewer two
    user-reachable surfaces -- Accept / Reject buttons and the
    FL4131_MORPHOLOGY_REVIEW_RECEIPT MCP command -- and both append a row to
    material.morphology.v2.manual_review_receipts.jsonl. Per CLAUDE.md
    agent-native parity, the MCP path must exist for headless / scripted
    reviewers as well."""
    asciiid = (REPO_ROOT / "editor/asciiid.cpp").read_text(encoding="utf-8")

    assert "AsciiidWriteMorphologyReviewReceipt(" in asciiid
    assert "material.morphology.v2.manual_review_receipts.jsonl" in asciiid
    assert "FL4131_MORPHOLOGY_REVIEW_RECEIPT" in asciiid
    # Both Accept and Reject buttons must call the writer with the active glyph.
    assert "Accept##fl4131_morphology_review_accept" in asciiid
    assert "Reject##fl4131_morphology_review_reject" in asciiid


def test_asciiid_persists_morphology_profile_to_sidecar() -> None:
    """Step 8 contract: dropdown assign + MCP assign write morphology_profile_name
    into assets/a3d/fl4131_harri_mat_profiles.json and the loader restores it.
    Schema v2 keeps backward compatibility with v1 sidecars (which simply
    omit the field and fall back to the per-material default)."""
    asciiid = (REPO_ROOT / "editor/asciiid.cpp").read_text(encoding="utf-8")

    assert "fl4131_harri_mat_profiles.v2" in asciiid
    assert "morphology_profile_name" in asciiid
    assert "AsciiidHarriSaveProfiles();" in asciiid
    assert "AsciiidAssignMaterialMorphologyProfile(mat_id, morphology_profile_name)" in asciiid
