from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_camera_drag_owner_is_deleted_from_browser_surface() -> None:
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text(
        encoding="utf-8"
    )
    transport = (ROOT / "web/asciiid-authoring-shell/src/transport.ts").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text(
        encoding="utf-8"
    )

    assert "CameraDrag" not in frame
    assert "cameraDrag" not in frame
    assert "commitCameraPointerRotate" not in frame
    assert "commitCameraPointerPan" not in frame
    assert "commitCameraPointerRotate" not in transport
    assert "commitCameraPointerPan" not in transport
    assert "scene.camera.pointer_rotate" not in app
    assert "scene.camera.pointer_pan" not in app
    assert "VIEW LOCKED · CAMERA INPUT UNAVAILABLE" in frame
