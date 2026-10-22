#!/usr/bin/env python3
"""FL-4873 F9 finalizer gates: capture integrity and when the MOV opens.

Audited defects (operator-visible):
  1. f9_finalize_bundle.py --open ran /usr/bin/open whenever the MOV encoded.
     A failed harness replay (63 frames, 2793 capture drops, 0 validated)
     therefore put an unusable movie in front of the operator.
  2. completion.json said status "complete" while counts.dropped > 0
     (regionB baseline replay: dropped=1654, then 2793).
Covered here, offline over synthetic bundles (no Godot process starts):
  - any capture drop, a missing drop count, or a frame-count mismatch against
    the recorder's own manifest.json makes the bundle "incomplete" with a
    named problem, and the runner's _recording_ineligibility still rejects it;
  - a harness run (replay output dir, headed-runner launch identity, explicit
    opt-out, pytest) never opens the MOV;
  - an incomplete bundle never opens the MOV, even when the MOV encoded;
  - a clean operator bundle opens, and completion.json "open" says why.
The opener is a stub script; no real viewer window opens.

Run: python3 -m pytest scripts/tests/test_fl4873_finalize_gates.py -q
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import f9_finalize_bundle as finalizer  # noqa: E402
import run_fl4359_headed_movement as runner  # noqa: E402
import test_f9_bundle_finalize as base  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(finalizer.resolve_ffmpeg("") == "", reason="ffmpeg unavailable")
HARNESS_ENV = (finalizer.NO_OPEN_ENV, finalizer.REPLAY_OUTPUT_DIR_ENV, finalizer.LAUNCH_SOURCE_ENV,
               "PYTEST_CURRENT_TEST")


@pytest.fixture
def opener(tmp_path, monkeypatch):
    """Stub OPEN_COMMAND that records its argument instead of opening it."""
    marker = tmp_path / "opened.txt"
    stub = tmp_path / "fake_open.sh"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$1" >> "{marker}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(finalizer, "OPEN_COMMAND", str(stub))
    monkeypatch.setenv(finalizer.PRESERVE_REPO_ROOT_ENV, str(tmp_path / "preserve_repo"))
    return marker


def _operator_env(monkeypatch) -> None:
    for name in HARNESS_ENV:
        monkeypatch.delenv(name, raising=False)


def _completion(bundle: Path) -> dict:
    return json.loads((bundle / "completion.json").read_text())


# ---------------------------------------------------------------- defect 2: drops are never "complete"

def test_capture_drops_make_the_bundle_incomplete_with_a_named_problem(tmp_path, monkeypatch):
    bundle = base._bundle(tmp_path, dropped=2793)
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    doc = finalizer.finalize(bundle, "")
    assert doc["status"] == "incomplete"
    assert "capture_dropped:2793" in doc["problems"]
    assert doc["capture_drops"] == 2793 and doc["every_engine_frame_captured"] is False


def test_missing_drop_count_is_not_read_as_zero(tmp_path, monkeypatch):
    bundle = base._bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    del manifest["counts"]["dropped"]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    doc = finalizer.finalize(bundle, "")
    assert "capture_drop_count_missing" in doc["problems"] and doc["status"] == "incomplete"


def test_frame_count_mismatch_against_the_recorder_manifest_is_named(tmp_path, monkeypatch):
    bundle = base._bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["counts"]["frames_listed"] = 63
    manifest["frames"] = [{"seq": 0}]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    doc = finalizer.finalize(bundle, "")
    assert "frame_count_mismatch:frames_listed=63,manifest_txt=3" in doc["problems"]
    assert "frame_count_mismatch:manifest_json_frames=1,manifest_txt=3" in doc["problems"]
    assert doc["status"] == "incomplete"


def test_capture_integrity_is_clean_for_a_matching_zero_drop_manifest():
    manifest = {"counts": {"frames_listed": 3, "dropped": 0}, "frames": [{}, {}, {}]}
    assert finalizer.capture_integrity_problems(manifest, manifest["counts"], 3) == []


@needs_ffmpeg
def test_runner_still_rejects_a_finalized_bundle_with_drops(tmp_path, monkeypatch, opener):
    _operator_env(monkeypatch)
    bundle = base._bundle(tmp_path, dropped=1654)
    finalizer.main([str(bundle), "--open"])
    doc = _completion(bundle)
    assert doc["status"] == "incomplete" and "capture_dropped:1654" in doc["problems"]
    reasons, _ = runner._recording_ineligibility(bundle)
    assert "recording_unfinished:incomplete" in reasons, reasons
    assert "recording_dropped_frames:1654" in reasons, reasons


# ---------------------------------------------------------------- defect 1: when the MOV opens

@needs_ffmpeg
def test_incomplete_bundle_never_opens_even_when_the_mov_encoded(tmp_path, monkeypatch, opener):
    _operator_env(monkeypatch)
    bundle = base._bundle(tmp_path, dropped=2793)
    rc = finalizer.main([str(bundle), "--open"])
    doc = _completion(bundle)
    assert rc == 1
    assert doc["outputs"]["mov"]["status"] == "ok", "the regression case: MOV encoded, capture not clean"
    assert doc["open"] == {"requested": True, "opened": False, "reason": "finalization_not_clean:incomplete"}
    assert not opener.exists()


@pytest.mark.parametrize("name,value,signal", [
    ("ASCIICKER_SCREEN_RECORDING_DIR", "/tmp/replay_out/replay_recording", "ASCIICKER_SCREEN_RECORDING_DIR"),
    ("ASCIICKER_LAUNCH_SOURCE_IDENTITY",
     json.dumps({"captured_by": "scripts/run_fl4359_headed_movement.py"}),
     "ASCIICKER_LAUNCH_SOURCE_IDENTITY.captured_by=scripts/run_fl4359_headed_movement.py"),
    ("ASCIICKER_F9_FINALIZE_NO_OPEN", "1", "ASCIICKER_F9_FINALIZE_NO_OPEN"),
    ("PYTEST_CURRENT_TEST", "x.py::t (call)", "PYTEST_CURRENT_TEST"),
])
@needs_ffmpeg
def test_harness_run_never_opens_a_clean_bundle(tmp_path, monkeypatch, opener, name, value, signal):
    _operator_env(monkeypatch)
    monkeypatch.setenv(name, value)
    bundle = base._bundle(tmp_path)
    rc = finalizer.main([str(bundle), "--open"])
    doc = _completion(bundle)
    assert rc == 0 and doc["status"] == "complete", doc["problems"]
    assert doc["open"]["opened"] is False
    assert doc["open"]["reason"] == f"harness_run:{signal}"
    assert not opener.exists()


def test_harness_signals_match_the_real_env_doors():
    """The signals are the env the harnesses actually set."""
    replay = (ROOT / "scripts" / "recording" / "f9_replay.py").read_text()
    assert f'"{finalizer.REPLAY_OUTPUT_DIR_ENV}": str(out_dir / "replay_recording")' in replay
    headed = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text()
    assert f'env["{finalizer.LAUNCH_SOURCE_ENV}"] = _launch_source_identity(' in headed
    assert f'"captured_by": "{finalizer.HARNESS_LAUNCHERS[0]}"' in headed
    manual = json.dumps({"captured_by": "scripts/launch_fl4359_manual_play.py"})
    assert finalizer.harness_signals({finalizer.LAUNCH_SOURCE_ENV: manual}) == [], \
        "the operator's manual-play launcher is not a harness"
    assert finalizer.harness_signals({finalizer.LAUNCH_SOURCE_ENV: "not json"}) == []
    assert finalizer.harness_signals({finalizer.NO_OPEN_ENV: "0"}) == []


@needs_ffmpeg
def test_clean_operator_bundle_opens_and_records_why(tmp_path, monkeypatch, opener):
    _operator_env(monkeypatch)
    monkeypatch.setenv(finalizer.LAUNCH_SOURCE_ENV,
                       json.dumps({"captured_by": "scripts/launch_fl4359_manual_play.py"}))
    bundle = base._bundle(tmp_path)
    rc = finalizer.main([str(bundle), "--open"])
    doc = _completion(bundle)
    assert rc == 0 and doc["status"] == "complete" and doc["problems"] == []
    assert doc["preservation"]["status"] == "preserved"
    assert doc["open"] == {"requested": True, "opened": True, "reason": "complete_and_clean"}
    assert opener.read_text().splitlines() == [str(bundle / "recording.mov")]


@needs_ffmpeg
def test_without_open_flag_nothing_opens(tmp_path, monkeypatch, opener):
    _operator_env(monkeypatch)
    bundle = base._bundle(tmp_path)
    finalizer.main([str(bundle)])
    assert _completion(bundle)["open"] == {"requested": False, "opened": False, "reason": "not_requested"}
    assert not opener.exists()


def test_failed_preservation_never_opens():
    doc = {"status": "complete", "problems": [], "capture_drops": 0, "outputs": {"mov": {"status": "ok"}},
           "preservation": {"status": "failed"}}
    assert finalizer.open_refusal(doc, True, {}) == "preservation_failed"


# FL-4896: a --godot-project-dir snapshot has no scripts/ tree beside its
# project, so the recorder's res://../scripts finalizer path was missing
# (finalizer_missing, recording_missing_png). The runner hands the executed
# tree's finalizer through ASCIICKER_F9_FINALIZER and the recorder prefers it.
def test_runner_names_the_executed_trees_finalizer(tmp_path: Path) -> None:
    live = runner._f9_finalizer_path(None)
    assert live == ROOT / "scripts" / "recording" / "f9_finalize_bundle.py"
    assert live.is_file()
    assert runner._f9_finalizer_path({"materialized_source": None}) == live
    snap = runner._f9_finalizer_path({"materialized_source": {"root": str(tmp_path)}})
    assert snap == tmp_path / "scripts" / "recording" / "f9_finalize_bundle.py"
    run_body = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text(encoding="utf-8")
    assert 'env["ASCIICKER_F9_FINALIZER"] = str(_f9_finalizer_path(replay_plan))' in run_body


def test_recorder_reads_the_finalizer_override() -> None:
    gd = (ROOT / "godot_project" / "scripts" / "recording" / "ScreenRecordingController.gd").read_text(
        encoding="utf-8")
    assert 'const _BUNDLE_FINALIZER_ENV := "ASCIICKER_F9_FINALIZER"' in gd
    body = gd.split("static func bundle_finalizer_path() -> String:", 1)[1].split("\nstatic func", 1)[0]
    assert "OS.get_environment(_BUNDLE_FINALIZER_ENV)" in body
    assert "is_absolute_path()" in body
