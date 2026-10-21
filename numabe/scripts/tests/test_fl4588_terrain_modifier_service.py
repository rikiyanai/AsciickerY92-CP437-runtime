"""Terrain modifier effects remain native but have no visible canvas owner."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_modifier_interactions_are_explicitly_proof_open() -> None:
    registry = json.loads((ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json").read_text())
    rows = {row["leaf_id"]: row for row in registry["interactions"]}
    for leaf_id, handler in (
        ("scene.terrain.paint.blur_sharpen", "CommitTerrainSculptBlurSharpen"),
        ("scene.terrain.diagonal.flip", "CommitTerrainDiagonalFlip"),
        ("scene.terrain.probe.height", "CommitTerrainProbeHeight"),
        ("scene.terrain.patch.topology", "CommitTerrainPatchTopology"),
    ):
        assert rows[leaf_id]["application_service_handler"] == handler
        assert rows[leaf_id]["proof_status"] == "PROOF_OPEN"


def test_native_modifier_effects_remain_without_direct_or_canvas_dispatch() -> None:
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text()
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text()
    for symbol in (
        "AsciiidApplicationService::CommitTerrainSculptBlurSharpen",
        "AsciiidApplicationService::CommitTerrainDiagonalFlip",
        "AsciiidApplicationService::CommitTerrainProbeHeight",
        "AsciiidApplicationService::CommitTerrainPatchTopology",
    ):
        assert symbol in service
    assert "CommitDirectAction" not in service
    assert "submitRuntimePhysicalIntent(" not in frame


def test_retired_browser_canvas_resolver_is_absent() -> None:
    assert not (ROOT / "web/asciiid-authoring-shell/src/canvasEventResolver.ts").exists()
