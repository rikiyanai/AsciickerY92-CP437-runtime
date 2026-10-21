#!/usr/bin/env python3
"""FL-4873 tasks 5-8 offline: replay plan and validation.

Builds a recorded bundle (manifest.json, replay/ prerequisites, session.json)
and a replay output (replay_result.json, replay_witness.jsonl, a replay
bundle with a real F9SYMv02 sidecar), then drives the same post_run the runner calls.
No Godot and no headed run: the headed proof run is the integration gate.

Run: python3 -m pytest scripts/tests/test_f9_replay_pipeline.py -q
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))

import f9_replay  # noqa: E402
import f9_replay_validate  # noqa: E402
import f9_session  # noqa: E402
import f9_symbolic_codec as codec  # noqa: E402

COLS, ROWS, CW, CH, W, H = 10, 10, 10, 10, 100, 100
SHA = "a" * 64
CAM = [0.0, 0.0, 10.0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
LENS = [1, 60.0, 10.0, 0.05, 100.0]
SEL = [[3, 4], [4, 4]]


def _row(seq, **over):
    row = {"seq": seq, "engine_frame": 500 + seq, "renderer_epoch": 900 + seq, "camera": CAM, "camera_lens": LENS,
           "player": CAM, "product_time": 10.0, "day_cycle_time": 0.25, "anim_time": 1.0, "local_actor_id": 1,
           "actors": {"1": [0, 0, 0], "2": [5.0, 0.0, 5.0]}}
    row.update(over)
    return row


def _frame_row(seq, engine):
    return {"seq": seq, "engine_frame": engine, "mode": "FF", "camera": "orthogonal", "cell": f"{CW}x{CH}:terminal",
            "matrix": f"[{COLS},{ROWS}]", "image": f"{W}x{H}", "png": f"frames/frame_{seq:05d}.png", "unix_ms": 1000 + seq}


def _identity(name):
    return {"name": name, "mode": "FF", "cols": COLS, "rows": ROWS, "cell_w": CW, "cell_h": CH, "raster_w": W,
            "raster_h": H, "projection": "orthogonal", "source": {"executed_source_sha256": SHA}}


def _recorded(tmp: Path) -> Path:
    bundle = tmp / "FL4359_rec"
    (bundle / "replay").mkdir(parents=True)
    frames = [_frame_row(s, 500 + s) for s in range(6)]
    lines = ["recording_identity: FL4359_rec", "frames:"] + [
        "frame: " + " ".join(f"{k}={v}" for k, v in f.items()) for f in frames]
    (bundle / "manifest.txt").write_text("\n".join(lines) + "\n")
    (bundle / "manifest.json").write_text(json.dumps({"identity": _identity("FL4359_rec"), "frames": frames,
                                                      "world": {"main_scene": "res://scenes/Main.tscn", "world_scene": "w", "world_map": "m"},
                                                      "segments": []}))
    (bundle / "replay" / "recipe.json").write_text(json.dumps({"prerequisites": "recorded", "preroll": {"frames": 30},
                                                               "window_size": [W, H], "spawn": CAM,
                                                               # the runner's launch request (FL-4873)
                                                               "cmdline_user_args": ["--fl4359-capture-window-mode=fullscreen",
                                                                                     f"--fl4359-capture-resolution={W}x{H}"]}))
    (bundle / "replay" / "frames.jsonl").write_text("".join(json.dumps(_row(s)) + "\n" for s in range(6)))
    capture = f9_session.capture_identity(bundle)
    cells = sorted(SEL, key=lambda c: (c[1], c[0]))
    (bundle / "session.json").write_text(json.dumps({
        "schema": f9_session.SCHEMA, "capture": {"id": capture["id"], "manifest_sha256": capture["manifest_sha256"]},
        "grid": {"cols": COLS, "rows": ROWS}, "selection": {"cells": cells, "count": len(cells), "bbox": [3, 4, 4, 4],
                                                            "digest": f9_session.selection_digest(cells)},
        "frames": {"referenced": [2, 3], "ranges": [[2, 3]], "current": 2}, "notes": {"3": "shimmer"}}))
    return bundle


def _replay_out(tmp: Path, *, camera_shift=0.0, swap_at=None, actor_moves=False) -> Path:
    out = tmp / "replay_run"
    replay = out / "replay"
    replay.mkdir(parents=True)
    rb = out / "replay_recording" / "FL4359_replay"
    rb.mkdir(parents=True)
    window = list(range(0, 6))
    frames = [_frame_row(i, 700 + i) for i in range(len(window))]
    (rb / "manifest.json").write_text(json.dumps({"identity": _identity("FL4359_replay"), "frames": frames,
                                                  "world": {"main_scene": "res://scenes/Main.tscn", "world_scene": "w", "world_map": "m"}}))
    grids = []
    for i, seq in enumerate(window):
        grid = [[32, 0, 0, 0, 0, 0] for _ in range(COLS * ROWS)]
        for x in range(10):
            glyph = 99 if (swap_at == seq and x == 4) else (65 if x == 4 else 60 + x)
            grid[4 * COLS + x] = [glyph, 0xFFFFFF, 0, 0, 3, 1000 + x]
        grids.append({"kind": "key", "seq": i, "mode": 0, "f10": 0, "cells": grid})
    codec.dumps(rb / codec.SIDECAR_FILENAME, {"version": 2, "cols": COLS, "rows": ROWS, "keyframe_every": 60, "frames": grids})
    witnesses = []
    for i, seq in enumerate(window):
        cam = list(CAM)
        cam[0] += camera_shift
        actors = {"1": [0, 0, 0], "2": [5.0 + (seq * 0.5 if actor_moves else 0.0), 0.0, 5.0]}
        witnesses.append(_row(seq, recorded_seq=seq, engine_frame=700 + i, camera=cam, actors=actors))
    (replay / "replay_witness.jsonl").write_text("".join(json.dumps(w) + "\n" for w in witnesses))
    (replay / "replay_result.json").write_text(json.dumps({"status": "replayed", "window": [0, 5], "preroll_frames": 30,
                                                           "replay_bundle": str(rb), "drive_scope": "pose"}))
    (out / "godot_client.log").write_text("noise\n")
    return out


def _args(bundle, **over):
    parser = argparse.ArgumentParser()
    f9_replay.add_arguments(parser)
    args = parser.parse_args(["--replay-bundle", str(bundle)])
    args.glyph_matrix_cell_px, args.extra_user_arg = 32, []
    for key, value in over.items():
        setattr(args, key, value)
    return args


@pytest.fixture(autouse=True)
def archive_in_tmp(tmp_path, monkeypatch):
    """plan() archives the consumed selection before use; keep it in tmp_path."""
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")


@pytest.fixture
def same_source(monkeypatch):
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (SHA, "captured"))


def test_plan_takes_conditions_from_the_bundle(tmp_path, same_source):
    bundle = _recorded(tmp_path)
    args = _args(bundle)
    plan = f9_replay.plan(args, tmp_path / "out")
    assert args.camera_mode == "ortho" and args.resolution == (W, H) and args.record_flight_video
    assert plan["session"]["count"] == 2 and plan["window"] == [0, 5]


def test_plan_refuses_selection_size_and_source_mismatch(tmp_path, same_source, monkeypatch):
    bundle = _recorded(tmp_path)
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(_args(bundle, expect_cells=3), tmp_path / "out")
    assert err.value.code == "selection_selection_count_mismatch"
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: ("b" * 64, "captured"))
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(_args(bundle), tmp_path / "out")
    assert err.value.code == "source_mismatch"


def test_valid_replay_validates(tmp_path, same_source):
    bundle = _recorded(tmp_path)
    out = _replay_out(tmp_path, swap_at=3)
    plan = f9_replay.plan(_args(bundle), out)
    code = f9_replay.post_run(plan, 0, out / "godot_client.log")
    validation = json.loads((out / "replay_validation.json").read_text())
    assert validation["status"] == "valid", validation["mismatches"]
    assert code == 0
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["validation"]["status"] == "valid"


def test_deliberate_camera_mismatch_is_detected(tmp_path, same_source):
    bundle = _recorded(tmp_path)
    out = _replay_out(tmp_path, camera_shift=0.5)
    plan = f9_replay.plan(_args(bundle), out)
    assert f9_replay.post_run(plan, 0, out / "godot_client.log") == 1
    validation = json.loads((out / "replay_validation.json").read_text())
    assert validation["status"] == "mismatch"
    assert validation["mismatches"]["camera_trajectory_diverged"]["worst"] == pytest.approx(0.5)


def test_pose_replay_rejected_when_gameplay_state_moves(tmp_path, same_source):
    bundle = _recorded(tmp_path)
    rows = [_row(s, actors={"1": [0, 0, 0], "2": [5.0 + s, 0.0, 5.0]}) for s in range(6)]
    (bundle / "replay" / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = _replay_out(tmp_path)
    result = f9_replay_validate.validate(bundle, out / "replay", "baseline", "pose", SHA)
    assert "camera_only_substitution_rejected" in result["mismatches"]
