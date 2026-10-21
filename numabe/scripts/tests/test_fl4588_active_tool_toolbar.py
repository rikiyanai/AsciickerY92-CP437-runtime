"""Active-tool controls retain runtime ownership after canvas deletion."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_toolbar_submits_registered_tool_selection_as_runtime_intent() -> None:
    toolbar = source("web/asciiid-authoring-shell/src/Toolbar.tsx")
    transport = source("web/asciiid-authoring-shell/src/transport.ts")
    assert "const graph = registry.active_tool_graph;" in toolbar
    assert "submitRuntimePhysicalIntent(" in toolbar
    assert "commitSelectTerrainTool" not in transport
    assert "submitRuntimePhysicalIntent" in transport


def test_canvas_authoring_edges_are_proof_open_not_browser_wired() -> None:
    graph = registry()["active_tool_graph"]
    assert graph["canvas_surface"]["surface_id"] == "termpp_final_canvas"
    assert graph["canvas_surface"]["gesture"] == "inspect_only"
    for tool in graph["tools"]:
        assert tool["canvas_gesture_status"] == "PROOF_OPEN"
        assert tool["proof_status"] == "PROOF_OPEN"
        assert all(edge["status"] == "PROOF_OPEN" for edge in tool["tool_modifiers"])


def test_retired_canvas_router_and_recorder_are_absent() -> None:
    frame = source("web/asciiid-authoring-shell/src/FrameView.tsx")
    recorder = source("web/asciiid-authoring-shell/src/uiEventRecorder.ts")
    assert "resolveCanvasEdge" not in frame
    assert "capturePhysicalCanvasEvent" not in recorder
    assert not (ROOT / "web/asciiid-authoring-shell/src/canvasEventResolver.ts").exists()


def test_runtime_intent_owner_retains_tool_selection_dispatch() -> None:
    service = source("apps/asciicker-presenter/asciiid_application_service.cpp")
    assert "AsciiidApplicationService::SubmitPhysicalIntent" in service
    assert 'terrain_handler == "CommitSelectTerrainTool"' in service
    assert "effect = CommitSelectTerrainTool(tool, transaction_id);" in service
