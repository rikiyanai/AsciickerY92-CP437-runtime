#!/usr/bin/env python3
"""FL-4737 grounded exact-pose runner preflight contract."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"
DRIVER = ROOT / "scripts/adhoc/fl4359_external_movement_driver.gd"
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"


def load_runner():
    spec = importlib.util.spec_from_file_location("fl4737_headed_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def args(**overrides) -> argparse.Namespace:
    values = {
        "single_frame": True,
        "skip_jitter_analysis": True,
        "camera_mode": "perspective",
        "exact_player_position": "127.1,0,-3.9",
        "exact_camera_position": "124.4,19.6,29.0",
        "exact_camera_yaw": "-0.08",
        "exact_camera_pitch": "-0.46",
        "exact_camera_projection": "perspective",
        "exact_camera_fov": "58",
        "exact_camera_size": "",
        "player_spawn_x": "",
        "player_spawn_z": "",
        "day_cycle_time": 0.5,
        "godot_window_mode": "fullscreen",
        "extra_user_arg": [
            "--single-frame-cell-dump",
            "--fl4431-exact-pose-diagnostic",
        ],
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def main() -> int:
    runner = load_runner()
    selected = args()
    assert runner._has_exact_pose_arguments(selected)
    preflight = runner._exact_pose_capture_preflight(
        selected, godot_timeout_seconds=90.0
    )
    assert preflight["ok"], preflight
    assert preflight["route_identity"] == "fl4737_grounded_exact_pose_v1"
    assert runner._exact_player_spawn_xz("127.1,0,-3.9") == ("127.1", "-3.9")
    assert runner._exact_player_spawn_xz("127.1,nan,-3.9") is None
    assert runner._exact_player_spawn_xz("127.1,-3.9") is None
    assert runner._player_spawn_environment(
        selected, exact_pose_capture=True
    ) == {"PLAYER_SPAWN_X": "127.1", "PLAYER_SPAWN_Z": "-3.9"}
    assert runner._player_spawn_environment(
        args(player_spawn_x="9", player_spawn_z="8"), exact_pose_capture=True
    ) == {"PLAYER_SPAWN_X": "9", "PLAYER_SPAWN_Z": "8"}
    assert runner._player_spawn_environment(
        selected, exact_pose_capture=False
    ) == {}

    for field, value, expected in (
        ("exact_player_position", "", "exact_player_position"),
        ("exact_player_position", "127.1,nan,-3.9", "exact_player_position"),
        ("exact_camera_projection", "ortho", "projection_matches_camera_mode"),
        ("day_cycle_time", None, "fixed_day_cycle_time"),
        ("godot_window_mode", "windowed", "fullscreen_requested"),
        (
            "extra_user_arg",
            ["--single-frame-cell-dump"],
            "exact_pose_diagnostic_requested",
        ),
    ):
        rejected = runner._exact_pose_capture_preflight(
            args(**{field: value}), godot_timeout_seconds=90.0
        )
        assert not rejected["ok"]
        assert expected in rejected["failed_checks"]

    ortho = args(
        camera_mode="ortho",
        exact_camera_projection="ortho",
        exact_camera_fov="",
        exact_camera_size="50.67",
    )
    assert runner._exact_pose_capture_preflight(
        ortho, godot_timeout_seconds=90.0
    )["ok"]

    compositor = COMPOSITOR.read_text(encoding="utf-8")
    driver = DRIVER.read_text(encoding="utf-8")
    assert "var scene_camera_origin: Vector3 = Vector3.ZERO" in compositor
    assert "ctx.scene_camera_origin = camera_transform.origin" in compositor
    assert '"scene_camera_origin": [' in compositor
    assert '"scene_camera_origin", contract.get("camera_origin", [])' in driver
    print(
        "PASS: FL-4737 grounded exact-pose route fails closed independently "
        "of airplane proof"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
