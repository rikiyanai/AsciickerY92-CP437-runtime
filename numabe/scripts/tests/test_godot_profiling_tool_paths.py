"""Profiling tool-path ownership: the camera-turn perf driver lives under tools/profiling."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot_project"
NEW_SCRIPT = GODOT / "tools" / "profiling" / "run_camera_turn_perf.gd"
NEW_UID = GODOT / "tools" / "profiling" / "run_camera_turn_perf.gd.uid"
OLD_SCRIPT = GODOT / "scripts" / "run_camera_turn_perf.gd"
OLD_UID = GODOT / "scripts" / "run_camera_turn_perf.gd.uid"

EXPECTED_UID = "uid://csacjx8enui07"

CALLERS = [
    ROOT / "scripts" / "run_godot_camera_turn_perf.py",
    ROOT / "scripts" / "test_fl4431_perf_summary_contract.py",
    ROOT / "scripts" / "test_fl4691_stationary_perf_camera.py",
    ROOT / "scripts" / "test_fl4408_pixel_camera_context_contract.py",
    ROOT / "godot_project" / "scripts" / "tests" / "test_fl4691_performance_presentation.gd",
    ROOT / "config" / "godot_feature_regressions.json",
]

_PRELOAD_RE = re.compile(r'preload\("res://([^"]+)"\)')
_LOAD_RE = re.compile(r'load\("res://([^"]+)"\)')


def test_new_canonical_path_exists_with_uid_sibling() -> None:
    assert NEW_SCRIPT.is_file(), f"missing {NEW_SCRIPT}"
    assert NEW_UID.is_file(), f"missing {NEW_UID}"
    text = NEW_UID.read_text(encoding="utf-8").strip()
    assert text == EXPECTED_UID, f"UID drift in {NEW_UID}: {text!r}"


def test_old_runtime_address_absent() -> None:
    assert not OLD_SCRIPT.exists(), f"old driver address remains: {OLD_SCRIPT}"
    assert not OLD_UID.exists(), f"old driver UID remains: {OLD_UID}"


def test_moved_body_carries_no_old_self_address() -> None:
    source = NEW_SCRIPT.read_text(encoding="utf-8")
    assert "res://scripts/run_camera_turn_perf" not in source, "driver still references old address"


def test_moved_driver_preloads_and_loads_resolve() -> None:
    missing: list[str] = []
    source = NEW_SCRIPT.read_text(encoding="utf-8")
    for match in list(_PRELOAD_RE.finditer(source)) + list(_LOAD_RE.finditer(source)):
        target = GODOT / match.group(1)
        if not target.is_file():
            missing.append(f"run_camera_turn_perf.gd -> res://{match.group(1)}")
    assert missing == [], f"unresolved driver references: {missing}"


def test_known_callers_and_registry_resolve_to_new_address() -> None:
    for caller in CALLERS:
        assert caller.is_file(), f"missing caller {caller}"
        text = caller.read_text(encoding="utf-8")
        assert "tools/profiling/run_camera_turn_perf" in text, f"{caller} missing new address"
        assert "scripts/run_camera_turn_perf" not in text, f"{caller} still references old address"
