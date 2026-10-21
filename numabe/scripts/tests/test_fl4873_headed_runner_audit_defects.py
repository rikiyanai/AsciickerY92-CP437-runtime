#!/usr/bin/env python3
"""FL-4873 audited headed-runner defects, offline (no Godot process starts).

Defect 1: a snapshot replay launches Godot with --path <materialized
snapshot>/godot_project. PID discovery matches that path plus the port, so
every project-path consumer must receive the selected directory, never the
checkout's GODOT_PROJECT.

Defect 4: a run that never wrote its final Godot movement receipt (timeout,
exit code) is final_receipt_missing, not recorder_too_old. recorder_too_old
stays reserved for a receipt that exists without the registry fields.

Run: python3 -m pytest scripts/tests/test_fl4873_headed_runner_audit_defects.py -q
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import launch_fl4359_manual_play as launcher  # noqa: E402
import run_fl4359_headed_movement as runner  # noqa: E402
from test_fl4873_run_source_provenance import _run_with_provenance, repo  # noqa: E402,F401

PORT = 2600
PID = 424242


# ---------------------------------------------------------------- defect 1

def _snapshot_project(tmp_path: Path) -> Path:
    project = tmp_path / "materialized" / "godot_project"
    project.mkdir(parents=True)
    return project.resolve()


def _fake_system(monkeypatch, snapshot_project: Path, seen: list[Path]):
    """ps lists one Godot engine launched from the snapshot, after `open`."""
    launched = {"open": False}
    engine = (f"{PID} /Applications/Godot.app/Contents/MacOS/Godot --path {snapshot_project} "
              f"--script driver.gd -- --colyseus-client --port={PORT}\n")

    def fake_run(command, **_kwargs):
        if command[:1] == ["/usr/bin/open"]:
            launched["open"] = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:1] == ["ps"]:
            return subprocess.CompletedProcess(command, 0, engine if launched["open"] else "", "")
        raise AssertionError(f"unexpected command {command}")

    real_matching = launcher._matching_godot_engine_pids

    def spy_matching(project_path: Path, port: int) -> set[int]:
        seen.append(Path(project_path))
        return real_matching(project_path, port)

    def fake_kill(pid, sig):
        raise ProcessLookupError(pid)  # the engine has exited: wait() returns

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "_matching_godot_engine_pids", spy_matching)
    monkeypatch.setattr(launcher.os, "kill", fake_kill)
    monkeypatch.setattr(launcher.time, "sleep", lambda _s: None)
    monkeypatch.setattr(runner, "_activate_macos_window",
                        lambda pid, timeout=10.0: {"attempted": True, "activated": True, "reason": "fixture"})


def test_mocked_listing_finds_the_snapshot_engine_and_not_the_checkout(tmp_path, monkeypatch) -> None:
    snapshot_project = _snapshot_project(tmp_path)
    seen: list[Path] = []
    _fake_system(monkeypatch, snapshot_project, seen)
    # The listing already holds the launched engine: checkout lookup returns
    # nothing, the snapshot lookup returns the engine.
    launcher.subprocess.run(["/usr/bin/open"])
    assert launcher._matching_godot_engine_pids(runner.GODOT_PROJECT, PORT) == set()
    assert launcher._matching_godot_engine_pids(snapshot_project, PORT) == {PID}


def test_recorded_launch_passes_the_snapshot_path_to_pid_discovery(tmp_path, monkeypatch) -> None:
    snapshot_project = _snapshot_project(tmp_path)
    seen: list[Path] = []
    _fake_system(monkeypatch, snapshot_project, seen)
    cmd = [runner.GODOT_BIN, "--path", str(snapshot_project), "--", f"--port={PORT}"]
    godot_rc, activation = runner._launch_recorded_macos_godot(
        cmd, {}, tmp_path / "godot_client.log", snapshot_project, PORT, 5.0)
    assert seen and all(p == snapshot_project for p in seen), seen
    assert runner.GODOT_PROJECT.resolve() not in seen
    assert activation["engine_pid"] == PID
    assert activation["launch_reason"] == "exact_engine_pid_resolved"
    assert activation["project_path"] == str(snapshot_project)
    assert activation["activated"] is True
    assert godot_rc == launcher.DETACHED_GODOT_EXIT_STATUS_UNKNOWN


def test_checkout_path_would_not_discover_the_snapshot_engine(tmp_path, monkeypatch) -> None:
    """The pre-fix call: GODOT_PROJECT for a snapshot-launched engine."""
    snapshot_project = _snapshot_project(tmp_path)
    _fake_system(monkeypatch, snapshot_project, [])
    monkeypatch.setattr(launcher.time, "monotonic", iter([0.0, 0.0, 100.0]).__next__)
    child, receipt = launcher._launch_macos_godot_bundle(
        ["--path", str(snapshot_project)], {}, tmp_path / "log", runner.GODOT_PROJECT, PORT, False, timeout=1.0)
    assert child is None and receipt["launch_reason"] == "engine_pid_not_found"


def test_runner_launch_and_provenance_use_the_selected_project_dir() -> None:
    source = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text()
    run_body = source.split("def _run(preserver", 1)[1].split("\ndef main()", 1)[0]
    assert "_launch_recorded_macos_godot(\n                cmd, env, godot_log, godot_project_dir," in run_body
    assert "godot_project_dir = _selected_godot_project_dir(args)" in run_body
    assert "godot_log=godot_log, godot_project_dir=godot_project_dir)" in run_body
    # The constant is only the --godot-project-dir default: no consumer reads it.
    assert run_body.count("GODOT_PROJECT") == 1
    assert 'parser.add_argument(\n        "--godot-project-dir", default=str(GODOT_PROJECT),' in run_body


def test_runner_refuses_an_unregistered_project_tree(tmp_path) -> None:
    """FL-4896: provenance snapshots this checkout (or a materialized replay
    tree), so a hand-made copy such as .run/<run>/src/godot_project can only end
    in compiled_variant_not_reproducible_from_snapshot; refuse it before launch."""
    assert runner._unregistered_project_tree(runner.GODOT_PROJECT, None) == ""
    copy = tmp_path / "src" / "godot_project"
    copy.mkdir(parents=True)
    reason = runner._unregistered_project_tree(copy, None)
    assert reason.startswith("unregistered_project_tree:"), reason
    assert runner._unregistered_project_tree(copy, {"materialized_source": None}) == reason
    registered = {"materialized_source": {"root": str(tmp_path / "src")}}
    assert runner._unregistered_project_tree(copy, registered) == ""
    source = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text()
    run_body = source.split("def _run(preserver", 1)[1].split("\ndef main()", 1)[0]
    select = run_body.index("godot_project_dir = _selected_godot_project_dir(args)")
    refuse = run_body.index("unregistered_tree = _unregistered_project_tree(godot_project_dir, replay_plan)")
    launch = run_body.index("_launch_recorded_macos_godot(")
    assert select < refuse < launch


def test_selected_dir_follows_the_replay_rewrite(tmp_path) -> None:
    snapshot_project = _snapshot_project(tmp_path)
    args = argparse.Namespace(godot_project_dir=str(snapshot_project))
    assert runner._selected_godot_project_dir(args) == snapshot_project
    assert runner._selected_godot_project_dir(argparse.Namespace(godot_project_dir="godot_project")) \
        == runner.GODOT_PROJECT.resolve()


def test_provenance_scans_the_selected_project_dir(tmp_path) -> None:
    snapshot_project = _snapshot_project(tmp_path)
    provenance = runner.RunSourceProvenance(snapshot_root=tmp_path / "snapshots", finalize_wait_seconds=0)
    provenance.godot_project_dir = snapshot_project
    assert provenance._godot_dir() == snapshot_project


def test_canonical_profile_reads_atlases_from_the_selected_project(tmp_path, monkeypatch) -> None:
    snapshot_project = _snapshot_project(tmp_path)
    read: list[Path] = []
    monkeypatch.setattr(runner, "_decoded_rgba8_identity", lambda p: (read.append(Path(p)), ("d", (1, 1)))[1])
    runner._canonical_profile_spec(terminal=True, godot_project_dir=snapshot_project)
    runner._ground_turn_profile_spec(argparse.Namespace(
        godot_project_dir=str(snapshot_project), glyph_matrix_cell_px=32, cell_aspect="terminal"))
    assert read and all(snapshot_project in p.parents for p in read), read


# ---------------------------------------------------------------- defect 4

def test_timed_out_run_without_receipt_is_final_receipt_missing_not_recorder_too_old(repo: Path) -> None:
    provenance2, manifest2, started = _run_with_timeout(repo, lambda p: p.note_godot_exit(124))
    assert started
    reasons = provenance2.ineligible
    assert "final_receipt_missing:timeout" in reasons
    assert not any(r.startswith("recorder_too_old:final_variant_registry_missing") for r in reasons)
    assert not any(r.startswith("recorder_too_old:receipt_registry_revision_missing") for r in reasons)
    assert provenance2.verdict in ("failed", "ineligible")
    assert runner._apply_source_provenance_verdict(provenance2, 0) == 1
    # Failure evidence preserved: the provenance record is the durable receipt.
    assert manifest2 is not None
    record = json.loads((manifest2.parent / runner.RUN_SOURCE_PROVENANCE_NAME).read_text())
    assert record["artifacts"]["run_source_provenance"]["exists"] is True
    assert record["artifacts"]["godot_movement_receipt"] == {
        "path": "receipt.json", "exists": False, "parsed": False, "status": "missing"}
    assert record["godot_run"] == {"exit_code": 124, "reason": "timeout"}
    assert record["final_receipt_missing"]["reason"] == "timeout"
    assert record["executed_sources"]["compiled_shader_variants"]["status"] \
        == "registry_unavailable_final_receipt_missing"


def test_nonzero_exit_without_receipt_names_the_exit_code(repo: Path) -> None:
    provenance, manifest, _ = _run_with_timeout(repo, lambda p: p.note_godot_exit(3))
    assert "final_receipt_missing:exit_code_3" in provenance.ineligible
    assert not any(r.startswith("recorder_too_old") for r in provenance.ineligible)


def test_receipt_present_without_registry_is_still_recorder_too_old(repo: Path) -> None:
    provenance, manifest, _ = _run_with_timeout(repo, lambda p: p.note_godot_exit(0), receipt={"pass": True})
    reasons = provenance.ineligible
    assert "recorder_too_old:final_variant_registry_missing" in reasons
    assert "recorder_too_old:receipt_registry_revision_missing" in reasons
    assert not any(r.startswith("final_receipt_missing") for r in reasons)
    assert provenance.verdict == "ineligible"
    record = json.loads((manifest.parent / "receipt.json").read_text())["run_source_provenance"]
    assert record["artifacts"]["godot_movement_receipt"]["status"] == "present"
    assert "final_receipt_missing" not in record


def _run_with_timeout(repo: Path, note, receipt: dict | None = None):
    """_run_with_provenance with the Godot outcome recorded before finalize."""
    holder: dict = {}
    real_start = runner.RunSourceProvenance.start

    def start(self, **kwargs):
        holder["p"] = self
        return real_start(self, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(runner.RunSourceProvenance, "start", start)
    try:
        return _run_with_provenance(repo, receipt=receipt, between=lambda: note(holder["p"]))
    finally:
        mp.undo()


# Run 1d: Godot was killed on timeout mid-recording, so the recorder never
# wrote its "finalizing" completion.json and never launched the finalizer; the
# runner then waited the full 600 s for a file nothing would write.
def test_absent_completion_returns_without_waiting(tmp_path: Path) -> None:
    import time
    start = time.monotonic()
    assert runner._wait_for_f9_finalize(tmp_path, timeout=30.0) == "absent"
    assert time.monotonic() - start < 1.0


def test_finalizing_completion_is_still_waited_on_until_final(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "completion.json").write_text(json.dumps({"status": "finalizing"}))
    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        (tmp_path / "completion.json").write_text(json.dumps({"status": "complete"}))

    monkeypatch.setattr(runner.time, "sleep", fake_sleep)
    assert runner._wait_for_f9_finalize(tmp_path, timeout=30.0) == "complete"
    assert calls["n"] == 1
