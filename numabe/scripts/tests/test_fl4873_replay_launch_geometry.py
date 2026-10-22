#!/usr/bin/env python3
"""FL-4873: a replay launches Godot with the recording's window REQUEST.

recipe.window_size is the size the recorder observed (the fullscreen client
area, 3600x2252 on a 3600x2338 notched display), not the size the runner
requested. The baseline recording was launched `--fullscreen --resolution
1280x800`; replays launched `--fullscreen --resolution 3600x2252` lost their
on-screen window. These tests build the replay's Godot command offline (no
Godot, no runner launch) and check that it repeats the recording's launch.

Run: python3 -m pytest scripts/tests/test_fl4873_replay_launch_geometry.py -q
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import f9_replay  # noqa: E402
import run_fl4359_headed_movement as runner  # noqa: E402
import test_f9_replay_pipeline as pipeline  # noqa: E402

# Launch-geometry arguments of the baseline bundle
# FL4359_fl4873-baseline-strafe-f10_..._20260926-111905/replay/recipe.json
# (cmdline_user_args), with its observed window_size and window_mode 4.
BASELINE_LAUNCH_ARGS = ["--fl4359-capture-window-mode=fullscreen", "--fl4359-capture-resolution=1280x800"]
BASELINE_WINDOW_SIZE = [3600, 2252]


class _Parsed(Exception):
    def __init__(self, parser):
        super().__init__("parser captured")
        self.parser = parser


def _runner_defaults(_monkeypatch=None) -> argparse.Namespace:
    """The runner's own argument defaults (its parser is built inside _run)."""
    def capture(self, *_a, **_k):
        raise _Parsed(self)
    with pytest.MonkeyPatch.context() as local:
        local.setattr(argparse.ArgumentParser, "parse_args", capture)
        with pytest.raises(_Parsed) as caught:
            runner._run(None)
    return caught.value.parser.parse_args([])


def _recipe_with(bundle: Path, **fields) -> None:
    path = bundle / "replay" / "recipe.json"
    recipe = json.loads(path.read_text())
    recipe.update(fields)
    path.write_text(json.dumps(recipe))


def _replay_command(args: argparse.Namespace, out: Path) -> list[str]:
    return runner._build_godot_command(
        args, godot_project_dir=out / "godot_project", driver_script=out / "driver.gd", out_dir=out,
        receipt_path=out / "receipt.json", capture_path=out / "jitter_capture.jsonl",
        godot_log=out / "godot_client.log", effective_plane_circle_frames=12, effective_turn_frames=8,
        effective_jitter_capture_frame_limit=0)


@pytest.fixture(autouse=True)
def archive_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))


def test_launch_geometry_comes_from_the_recorded_launch_args_not_the_observed_size():
    geometry = f9_replay.recorded_launch_geometry(
        {"cmdline_user_args": ["--port=2589"] + BASELINE_LAUNCH_ARGS, "window_size": BASELINE_WINDOW_SIZE})
    assert geometry == {"window_mode": "fullscreen", "resolution": [1280, 800],
                        "source": "recipe.cmdline_user_args", "recorded_window_size": BASELINE_WINDOW_SIZE}


@pytest.mark.parametrize("recipe, code", [
    ({"window_size": BASELINE_WINDOW_SIZE}, "replay_launch_request_unrecorded"),
    ({}, "replay_launch_request_unrecorded"),
    ({"cmdline_user_args": ["--fl4359-capture-window-mode=fullscreen"]}, "replay_launch_request_unrecorded"),
    ({"cmdline_user_args": ["--fl4431-canonical-presentation=3600x2252"]}, "replay_launch_request_canonical"),
    ({"cmdline_user_args": BASELINE_LAUNCH_ARGS + ["--fl4359-capture-resolution=3600x2252"]},
     "replay_launch_request_conflicting"),
    ({"cmdline_user_args": ["--fl4359-capture-window-mode=canonical_locked",
                            "--fl4359-capture-resolution=1280x800"]}, "replay_launch_request_invalid"),
    ({"cmdline_user_args": ["--fl4359-capture-window-mode=fullscreen",
                            "--fl4359-capture-resolution=0x800"]}, "replay_launch_request_invalid"),
])
def test_launch_geometry_refuses_instead_of_using_the_observed_size(recipe, code):
    with pytest.raises(f9_replay.ReplayRefused) as refused:
        f9_replay.recorded_launch_geometry(recipe)
    assert refused.value.code == code


@pytest.mark.parametrize("mode, resolution", [("fullscreen", (1280, 800)), ("windowed", (1600, 1000))])
def test_runner_records_its_engine_window_flags_as_the_capture_user_args(tmp_path, mode, resolution):
    """The documented rule recorded_launch_geometry relies on: the runner's
    engine flags and its recorded user args come from the same values."""
    args = _runner_defaults()
    args.camera_mode = "perspective"  # replays run perspective/ortho, never the canonical airplane arm
    args.godot_window_mode, args.resolution = mode, resolution
    cmd = _replay_command(args, tmp_path)
    user_args = cmd[cmd.index("--") + 1:]
    assert cmd[1:4] == [f"--{mode}", "--resolution", "%dx%d" % resolution]
    assert f9_replay.recorded_launch_geometry({"cmdline_user_args": user_args})["resolution"] == list(resolution)
    assert f9_replay.recorded_launch_geometry({"cmdline_user_args": user_args})["window_mode"] == mode


def test_runner_canonical_airplane_launch_records_no_engine_flags_and_is_refused(tmp_path):
    args = _runner_defaults()
    assert runner._uses_canonical_airplane_presentation(args)  # the runner default arm
    cmd = _replay_command(args, tmp_path)
    assert cmd[1] == "--path"
    with pytest.raises(f9_replay.ReplayRefused) as refused:
        f9_replay.recorded_launch_geometry({"cmdline_user_args": cmd[cmd.index("--") + 1:]})
    assert refused.value.code == "replay_launch_request_canonical"


def test_replay_of_the_baseline_launches_godot_exactly_like_the_recording(tmp_path, monkeypatch):
    bundle = pipeline._recorded(tmp_path)
    _recipe_with(bundle, cmdline_user_args=BASELINE_LAUNCH_ARGS, window_size=BASELINE_WINDOW_SIZE)
    args = _runner_defaults(monkeypatch)
    args.replay_bundle, args.diagnostics, args.analysis = str(bundle), "", "none"
    args.resolution = (3600, 2252)  # whatever the operator or a stale default says, the recording wins
    plan = f9_replay.plan(args, tmp_path / "out")
    assert plan["launch_geometry"]["source"] == "recipe.cmdline_user_args"
    cmd = _replay_command(args, tmp_path / "out")
    assert cmd[1:4] == ["--fullscreen", "--resolution", "1280x800"]
    for recorded in BASELINE_LAUNCH_ARGS:
        assert recorded in cmd
    assert not any("3600x2252" in part for part in cmd)


def test_replay_window_request_matches_a_movement_run_at_the_recorded_resolution(tmp_path, monkeypatch):
    bundle = pipeline._recorded(tmp_path)
    _recipe_with(bundle, cmdline_user_args=BASELINE_LAUNCH_ARGS, window_size=BASELINE_WINDOW_SIZE)
    replay = _runner_defaults(monkeypatch)
    replay.replay_bundle, replay.diagnostics, replay.analysis = str(bundle), "", "none"
    f9_replay.plan(replay, tmp_path / "out")
    movement = _runner_defaults(monkeypatch)
    movement.camera_mode = replay.camera_mode

    def window_args(cmd: list[str]) -> list[str]:
        head = cmd[1:cmd.index("--path")]
        return head + [p for p in cmd if p.startswith((f9_replay.LAUNCH_MODE_ARG, f9_replay.LAUNCH_RESOLUTION_ARG))]

    assert window_args(_replay_command(replay, tmp_path / "out")) == window_args(
        _replay_command(movement, tmp_path / "out"))


def test_bundle_without_a_recorded_launch_request_is_refused_before_any_archive(tmp_path, monkeypatch):
    bundle = pipeline._recorded(tmp_path)
    recipe_path = bundle / "replay" / "recipe.json"
    recipe = json.loads(recipe_path.read_text())
    recipe.pop("cmdline_user_args")
    recipe_path.write_text(json.dumps(recipe))
    args = _runner_defaults(monkeypatch)
    args.replay_bundle, args.diagnostics, args.analysis = str(bundle), "", "none"
    with pytest.raises(f9_replay.ReplayRefused) as refused:
        f9_replay.plan(args, tmp_path / "out")
    assert refused.value.code == "replay_launch_request_unrecorded"
    assert not (tmp_path / "archive_repo").exists()
