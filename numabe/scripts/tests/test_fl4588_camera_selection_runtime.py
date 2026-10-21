import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def test_camera_actions_have_no_runtime_admission_path() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    interactions = {row["leaf_id"] for row in registry["interactions"]}
    retired = {
        "scene.camera.keyboard_move",
        "scene.camera.wheel_zoom",
        "scene.camera.pointer_rotate",
        "scene.camera.pointer_pan",
    }
    assert interactions.isdisjoint(retired)

    header = (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text(
        encoding="utf-8"
    )
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    for source in (header, service, native):
        assert "CommitCameraKeyboardMove" not in source
        assert "CommitCameraWheelZoom" not in source
        assert "CommitCameraPointerRotate" not in source
        assert "CommitCameraPointerPan" not in source
        assert "CommitCameraFocusTerrain" not in source

    assert "camera movement input is unavailable fail closed" in native
    assert "camera turn input is unavailable fail closed" in native
