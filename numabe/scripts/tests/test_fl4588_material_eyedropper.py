"""Material eyedropper effect survives behind a fail-closed runtime boundary."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_eyedropper_interaction_is_proof_open_without_canvas_owner() -> None:
    registry = json.loads((ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json").read_text())
    row = next(x for x in registry["interactions"] if x["leaf_id"] == "scene.terrain.eyedropper.material")
    assert row["application_service_handler"] == "CommitTerrainMaterialEyedropper"
    assert row["proof_status"] == "PROOF_OPEN"
    material = next(x for x in registry["active_tool_graph"]["tools"] if x["tool_id"] == "material_paint")
    edge = next(x for x in material["tool_modifiers"] if x["role"] == "eyedropper")
    assert edge["status"] == "PROOF_OPEN"


def test_native_effect_remains_but_browser_committer_is_deleted() -> None:
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text()
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text()
    transport = (ROOT / "web/asciiid-authoring-shell/src/transport.ts").read_text()
    assert "CommitAsciiidTerrainMaterialEyedropper" in native
    assert "AsciiidApplicationService::CommitTerrainMaterialEyedropper" in service
    assert "commitTerrainMaterialEyedropper" not in transport
    assert "eyedropperArmed" not in frame


def test_canvas_resolver_and_canvas_event_capture_are_deleted() -> None:
    recorder = (ROOT / "web/asciiid-authoring-shell/src/uiEventRecorder.ts").read_text()
    assert not (ROOT / "web/asciiid-authoring-shell/src/canvasEventResolver.ts").exists()
    assert "capturePhysicalCanvasEvent" not in recorder
