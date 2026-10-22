"""Material-elevation canvas mutation is deleted; native effects fail closed."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_material_elevation_interactions_are_proof_open() -> None:
    registry = json.loads((ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json").read_text())
    rows = {row["leaf_id"]: row for row in registry["interactions"]}
    for leaf_id, handler in (
        ("scene.terrain.matelev.paint", "CommitMaterialElevationPaint"),
        ("scene.terrain.matelev.eyedropper", "CommitMaterialElevationEyedropper"),
    ):
        assert rows[leaf_id]["application_service_handler"] == handler
        assert rows[leaf_id]["proof_status"] == "PROOF_OPEN"


def test_native_effects_remain_without_transport_or_canvas_committer() -> None:
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text()
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text()
    transport = (ROOT / "web/asciiid-authoring-shell/src/transport.ts").read_text()
    assert "CommitAsciiidMaterialElevationPaint" in native
    assert "CommitAsciiidMaterialElevationEyedropper" in native
    assert "AsciiidApplicationService::CommitMaterialElevationPaint" in service
    assert "AsciiidApplicationService::CommitMaterialElevationEyedropper" in service
    assert "commitMaterialElevationPaint" not in transport
    assert "commitMaterialElevationEyedropper" not in transport
    assert "submitRuntimePhysicalIntent(" not in frame


def test_paint_still_prevalidates_before_mutation() -> None:
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    start = native.index("static AsciiidMaterialElevationPaintCommitV1 CommitAsciiidMaterialElevationPaint")
    end = native.index("static void SampleElevCB", start)
    body = native[start:end]
    reject = body.index("material_elevation_paint_sample_rejected")
    assert reject < body.index("URDO_Open()")
    assert reject < body.index("painting = 3")
