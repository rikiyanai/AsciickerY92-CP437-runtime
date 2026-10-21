"""Mesh canvas modifier deletion contracts."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_mesh_frame_bound_modifier_interactions_are_hard_deleted() -> None:
    registry = json.loads((ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json").read_text())
    interactions = {row["leaf_id"] for row in registry["interactions"]}
    assert "scene.objects.mesh.marquee_select" not in interactions
    assert "scene.objects.mesh.delete" not in interactions
    mesh = next(row for row in registry["active_tool_graph"]["tools"] if row["tool_id"] == "mesh")
    assert mesh["tool_modifiers"] == []
    assert mesh["canvas_gesture_status"] == "PROOF_OPEN"


def test_native_frozen_editor_frame_committers_are_absent() -> None:
    app = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    header = (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text()
    for retired in (
        "CommitAsciiidMeshMarqueeSelection",
        "CommitAsciiidMeshDeletion",
        "mesh_marquee_exact_frame_expired",
        "mesh_deletion_exact_frame_expired",
        "mesh_marquee_selection_committer_",
        "mesh_deletion_committer_",
    ):
        assert retired not in app
        assert retired not in header


def test_browser_has_no_mesh_canvas_gesture_owner() -> None:
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text()
    transport = (ROOT / "web/asciiid-authoring-shell/src/transport.ts").read_text()
    assert "meshMarqueeDrag" not in frame
    assert "commitMeshMarqueeSelection" not in transport
    assert "commitMeshDeletion" not in transport
    assert "resolveCanvasEdge" not in frame
