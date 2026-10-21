#!/usr/bin/env python3
"""Verify that editors cannot author the FL-4512 admission candidate row.

The mesh-feature admission compiler owns the complete candidate row.  Native
ASCIIID may still observe the compiled runtime bucket for diagnostics, but its
profile edit state, serializer, snapshot draft, and typed lane parser must have
no address that can mutate the row.  The HTML shell and its authoritative
registry must omit the same address.
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
DRAFT = (
    ROOT / "web/asciiid-authoring-shell/src/materialPoolDraft.ts"
).read_text(encoding="utf-8")
REGISTRY = json.loads(
    (
        ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    ).read_text(encoding="utf-8")
)
RESOLVER = (
    ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
).read_text(encoding="utf-8")


def _serializer_source() -> str:
    """Return only the normal profile serializer implementation.

    Narrowing the assertion to this function lets runtime diagnostics keep
    their legitimate ``mesh_bundle`` names without weakening the ownership
    deletion check.
    """

    return EDITOR.split(
        "static bool Fl4260ApplyProfileStateToRoot", 1
    )[1].split("static bool Fl4260BuildProfileBatchBytes", 1)[0]


def test_native_candidate_edit_state_and_lane_address_are_deleted():
    """The generic lane editor has no mesh-bundle storage, enum, parser, or UI."""

    forbidden = (
        "mesh_bundle_candidate_count",
        "mesh_bundle_candidates",
        "FL4512_ROLE_LANE_MESH_BUNDLE",
        'lane_id == "mesh_bundle.0"',
        'emit_lane("mesh_bundle.0"',
        "Mesh bundle (mesh_feature.bundle.v1)",
    )
    for token in forbidden:
        assert token not in EDITOR


def test_profile_serializer_never_copies_rebuilds_nor_deletes_compiler_row():
    """Saving ordinary profile fields leaves the compiler-owned object intact."""

    serializer = _serializer_source()
    assert "mesh_bundle" not in serializer
    assert 'cJSON_DeleteItemFromObject(rb, "mesh_feature")' not in serializer
    assert "candidate_admission_schema" not in serializer
    assert "candidate_admission_receipt_id" not in serializer
    assert "role_eligible_glyph_ids" not in serializer


def test_html_draft_and_registry_have_no_mesh_bundle_candidate_lane():
    """Neither browser inventory can construct the deleted candidate address."""

    assert '"mesh_bundle.0"' not in DRAFT
    for interaction in REGISTRY["interactions"]:
        payload = interaction.get("payload_schema") or {}
        lane_schema = (payload.get("properties") or {}).get("lane_id") or {}
        assert "mesh_bundle.0" not in (lane_schema.get("enum") or [])


def test_runtime_role_coordinate_and_native_observation_remain_available():
    """Deletion stops authoring while preserving compiled-row consumption."""

    assert RESOLVER.count("kFl4512ProfileMeshBundleBucket = 31") == 1
    assert "Fl4260V1Bucket mesh_bundle" in RESOLVER
    assert "mesh_bundle_merged_mask_8x8" in EDITOR
    assert "u7.mask.v1" in EDITOR
    assert "FL4512_POOL_ROUTE_MESH_BUNDLE" in EDITOR
