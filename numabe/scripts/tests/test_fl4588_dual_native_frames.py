"""FL-4734 single final-frame ownership contracts.

The former editor-scene renderer/readback/identity ring and browser canvas
mutation surface are deleted. TERM++ final is the only native frame exposed to
the authoring shell; its canvas is inspection-only.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_application_service_exposes_only_termpp_final_surface() -> None:
    header = read("apps/asciicker-presenter/asciiid_application_service.h")
    service = read("apps/asciicker-presenter/asciiid_application_service.cpp")
    assert "AsciiidFrameSurfaceV1::TermppFinal" in service
    assert "termpp_final_frame_provider_" in header
    assert "EditorScene" not in header
    assert "editor_scene_frame_provider_" not in service


def test_native_editor_scene_readback_and_identity_ring_are_deleted() -> None:
    app = read("apps/asciicker-presenter/asciiid.cpp")
    for retired in (
        "CaptureAsciiidEditorSceneFrame",
        "FulfillAsciiidEditorSceneFrameRequest",
        "FindAsciiidEditorSceneFrameIdentity",
        "kAsciiidEditorFrameIdentityHistory",
        "g_asciiid_latest_editor_scene_frame",
    ):
        assert retired not in app
    assert "CaptureAsciiidTermppFinalFrame" in app


def test_http_and_web_publish_one_final_surface() -> None:
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    view = read("web/asciiid-authoring-shell/src/FrameView.tsx")
    transport = read("web/asciiid-authoring-shell/src/transport.ts")
    assert 'GET /api/v1/frames/termpp-final.rgba HTTP/1.1' in http
    assert "frames/editor-scene.rgba" not in http
    assert 'NativeFrameSurface = "termpp_final"' in transport
    assert '"termpp_final"' in view
    assert '"editor_scene"' not in view


def test_final_canvas_is_inspection_only_and_never_submits_mutation() -> None:
    view = read("web/asciiid-authoring-shell/src/FrameView.tsx")
    assert "fetchCellInspection(" in view
    assert "submitRuntimePhysicalIntent(" not in view
    assert "capturePhysicalCanvasEvent" not in view
    assert "resolveCanvasEdge" not in view
    assert not (ROOT / "web/asciiid-authoring-shell/src/canvasEventResolver.ts").exists()


def test_runtime_witness_is_bound_to_the_final_frame() -> None:
    service = read("apps/asciicker-presenter/asciiid_application_service.cpp")
    assert "CurrentRuntimeSnapshot" in service
    assert "finalized_frame_id" in service
    assert "finalized_frame_sha256" in service
    assert "view_state_sha256" in service
    assert "CaptureFrame(AsciiidFrameSurfaceV1::TermppFinal)" in service
