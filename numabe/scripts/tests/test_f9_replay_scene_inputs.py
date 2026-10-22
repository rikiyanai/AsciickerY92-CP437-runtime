#!/usr/bin/env python3
"""FL-4873 scene-input comparison, F10 view and durable preservation, offline.

Candidate vs baseline replay witnesses (scene_inputs parts written by
F9ReplayDriver.SceneInputs), the baseline self-consistency check between two
baseline replays, the F10 view as a compared setting, and the replay results
preserved into the runner's content-addressed receipt bundle. Fixture files
only: no Godot and no headed run.

Run: python3 -m pytest scripts/tests/test_f9_replay_scene_inputs.py -q
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import f9_replay  # noqa: E402
import f9_replay_validate  # noqa: E402
import test_f9_replay_pipeline as pipeline  # noqa: E402
from run_receipt_preservation import DeferredRunReceiptPreserver  # noqa: E402

PARTS = ("meshes", "multimeshes", "other_visuals", "lights", "skeletons", "animation", "actors",
         "authority_actors", "environment", "environment_state", "terrain", "clocks", "seeds")
OTHER_SHA = "b" * 64


@pytest.fixture(autouse=True)
def archive_in_tmp(tmp_path, monkeypatch):
    """plan() archives the consumed selection before use; keep it in tmp_path."""
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")


def _scene_inputs(seq: int, diverge: dict | None = None) -> dict:
    parts = {name: f"{name}-{seq}".ljust(64, "0") for name in PARTS}
    nodes = {"meshes:World/Box": f"box{seq}".ljust(16, "0"), "lights:World/Sun": "sun".ljust(16, "0")}
    for name, value in (diverge or {}).items():
        if name.startswith("node:"):
            nodes[name[5:]] = value
        else:
            parts[name] = value
    return {"sha256": json.dumps(sorted(parts.items())).encode().hex()[:64], "count": 20, "parts": parts,
            "nodes": nodes}


def _replay(tmp: Path, name: str, bundle: Path, *, diverge_at: dict | None = None, f10: int = 0,
            witness_f10: int | None = None, scene_inputs: bool = True) -> Path:
    """A replay output directory (the runner --out) for the recorded bundle.
    diverge_at: {seq: {part or "node:<key>": digest}}."""
    base = tmp / name
    base.mkdir()
    out = pipeline._replay_out(base)
    replay = out / "replay"
    result = json.loads((replay / "replay_result.json").read_text())
    result.update(bundle=str(bundle), drive="pose")
    (replay / "replay_result.json").write_text(json.dumps(result))
    rows = []
    for line in (replay / "replay_witness.jsonl").read_text().splitlines():
        row = json.loads(line)
        seq = int(row["recorded_seq"])
        row["f10_view"] = f10 if witness_f10 is None else witness_f10
        if scene_inputs:
            row["scene_inputs"] = _scene_inputs(seq, (diverge_at or {}).get(seq))
        rows.append(row)
    (replay / "replay_witness.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    manifest_path = Path(result["replay_bundle"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for frame in manifest["frames"]:
        frame["f10_view"] = f10
        frame["f10_view_name"] = ("NORMAL", "FGONLY", "DISOCCLUSION")[f10]
    manifest_path.write_text(json.dumps(manifest))
    return out


def _recorded(tmp: Path, f10: int = 0) -> Path:
    bundle = pipeline._recorded(tmp)
    manifest = json.loads((bundle / "manifest.json").read_text())
    for frame in manifest["frames"]:
        frame["f10_view"] = f10
        frame["f10_view_name"] = ("NORMAL", "FGONLY", "DISOCCLUSION")[f10]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    return bundle.resolve()


def _valid_baseline(out: Path) -> Path:
    (out / "replay_validation.json").write_text(json.dumps({"mode": "baseline", "status": "valid"}))
    return out


def _candidate(bundle: Path, out: Path, baseline) -> dict:
    return f9_replay_validate.validate(bundle, out / "replay", "candidate", "pose", OTHER_SHA,
                                       str(baseline) if baseline else "")


def test_candidate_identical_scene_inputs_validates(tmp_path):
    bundle = _recorded(tmp_path)
    baseline = _valid_baseline(_replay(tmp_path, "baseline", bundle))
    candidate = _replay(tmp_path, "candidate", bundle)
    result = _candidate(bundle, candidate, baseline)
    assert result["status"] == "valid", result["mismatches"]
    assert result["baseline_comparison"]["status"] == "identical_inputs"
    assert result["baseline_comparison"]["frames_compared"] == 6


def test_candidate_scene_divergence_is_named_with_first_frame_and_parts(tmp_path):
    bundle = _recorded(tmp_path)
    baseline = _valid_baseline(_replay(tmp_path, "baseline", bundle))
    candidate = _replay(tmp_path, "candidate", bundle, diverge_at={
        2: {"meshes": "f" * 64, "node:meshes:World/Box": "moved0000000000"},
        4: {"lights": "e" * 64}})
    result = _candidate(bundle, candidate, baseline)
    assert result["status"] == "mismatch"
    entry = result["mismatches"]["scene_inputs_diverged"]
    assert entry["first_seq"] == 2 and entry["frames"] == 2
    assert entry["first_parts"] == ["meshes"]
    assert entry["parts"] == ["lights", "meshes"]
    assert entry["first_nodes"] == ["meshes:World/Box"]


def test_candidate_camera_clock_epoch_divergence_named(tmp_path):
    bundle = _recorded(tmp_path)
    baseline = _valid_baseline(_replay(tmp_path, "baseline", bundle))
    candidate = _replay(tmp_path, "candidate", bundle)
    witness = candidate / "replay" / "replay_witness.jsonl"
    rows = [json.loads(l) for l in witness.read_text().splitlines()]
    rows[1]["anim_time"] += 0.01
    rows[3]["renderer_epoch"] += 1
    rows[3]["camera"] = [rows[3]["camera"][0] + 1e-3] + rows[3]["camera"][1:]
    witness.write_text("".join(json.dumps(r) + "\n" for r in rows))
    mismatches = _candidate(bundle, candidate, baseline)["mismatches"]
    assert mismatches["replay_pair_clock_diverged_anim_time"]["first_seq"] == 1
    assert mismatches["replay_pair_epoch_diverged"]["first_seq"] == 3
    assert mismatches["replay_pair_camera_diverged"]["first_seq"] == 3


def test_candidate_without_baseline_directory_is_refused(tmp_path, monkeypatch):
    bundle = _recorded(tmp_path)
    candidate = _replay(tmp_path, "candidate", bundle)
    assert "candidate_without_baseline" in _candidate(bundle, candidate, "")["mismatches"]
    missing = _candidate(bundle, candidate, tmp_path / "no_such_replay")["mismatches"]
    assert "baseline_replay_missing" in missing
    # A string that is not a replay output directory is refused before Godot.
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (OTHER_SHA, "captured"))
    for baseline_arg, code in (("", "candidate_without_baseline"),
                               (str(tmp_path / "no_such_replay"), "baseline_replay_missing"),
                               ("baseline-2026-09-24", "baseline_replay_missing")):
        args = pipeline._args(bundle, replay_mode="candidate", baseline_replay=baseline_arg)
        with pytest.raises(f9_replay.ReplayRefused) as err:
            f9_replay.plan(args, tmp_path / "out")
        assert err.value.code == code


def test_candidate_baseline_must_be_a_valid_baseline_of_the_same_bundle(tmp_path, monkeypatch):
    bundle = _recorded(tmp_path)
    unvalidated = _replay(tmp_path, "unvalidated", bundle)
    candidate = _replay(tmp_path, "candidate", bundle)
    assert "baseline_replay_not_valid_baseline" in _candidate(bundle, candidate, unvalidated)["mismatches"]
    other = _valid_baseline(_replay(tmp_path, "other", tmp_path / "another_bundle"))
    assert "replay_pair_bundle_differs" in _candidate(bundle, candidate, other)["mismatches"]
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (OTHER_SHA, "captured"))
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(pipeline._args(bundle, replay_mode="candidate", baseline_replay=str(other)), tmp_path / "out")
    assert err.value.code == "baseline_replay_other_bundle"
    good = _valid_baseline(_replay(tmp_path, "good", bundle))
    plan = f9_replay.plan(pipeline._args(bundle, replay_mode="candidate", baseline_replay=str(good / "replay")),
                          tmp_path / "out")
    assert plan["baseline_replay"] == str(good.resolve())


def test_witnesses_without_scene_inputs_cannot_validate_a_candidate(tmp_path):
    bundle = _recorded(tmp_path)
    baseline = _valid_baseline(_replay(tmp_path, "baseline", bundle, scene_inputs=False))
    candidate = _replay(tmp_path, "candidate", bundle)
    entry = _candidate(bundle, candidate, baseline)["mismatches"]["scene_inputs_unwitnessed"]
    assert entry["frames"] == 6 and "reference" in entry["detail"]


def test_baseline_self_consistency_two_baseline_replays(tmp_path):
    """Two baseline replays of one bundle must give identical scene_inputs per
    frame; a divergence between them is named the same way."""
    bundle = _recorded(tmp_path)
    first = _valid_baseline(_replay(tmp_path, "baseline_1", bundle))
    second = _replay(tmp_path, "baseline_2", bundle)
    report = f9_replay_validate.compare_replays(first / "replay", second / "replay")
    assert report["status"] == "identical_inputs" and report["frames_compared"] == 6
    result = f9_replay_validate.validate(bundle, second / "replay", "baseline", "pose", pipeline.SHA, str(first))
    assert result["status"] == "valid", result["mismatches"]
    drifted = _replay(tmp_path, "baseline_3", bundle, diverge_at={5: {"animation": "a" * 64}})
    report = f9_replay_validate.compare_replays(first / "replay", drifted / "replay")
    assert report["status"] == "inputs_diverged"
    assert report["mismatches"]["scene_inputs_diverged"]["first_seq"] == 5
    assert report["mismatches"]["scene_inputs_diverged"]["parts"] == ["animation"]
    result = f9_replay_validate.validate(bundle, drifted / "replay", "baseline", "pose", pipeline.SHA, str(first))
    assert "scene_inputs_diverged" in result["mismatches"]
    # The command-line form of the same check.
    assert f9_replay_validate.main(["compare", str(first), str(second)]) == 0
    assert f9_replay_validate.main(["compare", str(first), str(drifted)]) == 1


def test_f10_view_is_a_compared_setting(tmp_path):
    bundle = _recorded(tmp_path, f10=1)
    same = _replay(tmp_path, "same", bundle, f10=1)
    result = f9_replay_validate.validate(bundle, same / "replay", "baseline", "pose", pipeline.SHA)
    assert result["status"] == "valid", result["mismatches"]
    assert result["f10_views"] == {"recorded": {"FGONLY": 6}, "replayed": {"FGONLY": 6}}
    other = _replay(tmp_path, "other", bundle, f10=0)
    result = f9_replay_validate.validate(bundle, other / "replay", "baseline", "pose", pipeline.SHA)
    entry = result["mismatches"]["settings_mismatch"]
    assert entry["detail"].startswith("f10_view:") and entry["first_seq"] == 0
    assert result["f10_views"] == {"recorded": {"FGONLY": 6}, "replayed": {"NORMAL": 6}}


def test_f10_override_is_reported_not_silently_valid(tmp_path):
    bundle = _recorded(tmp_path, f10=0)
    out = _replay(tmp_path, "override", bundle, f10=0)
    result_path = out / "replay" / "replay_result.json"
    result = json.loads(result_path.read_text())
    result["f10_override"] = 1
    result_path.write_text(json.dumps(result))
    validation = f9_replay_validate.validate(bundle, out / "replay", "baseline", "pose", pipeline.SHA)
    assert validation["status"] == "mismatch" and "view_overridden" in validation["mismatches"]


def test_f10_view_flag_reaches_godot_user_args(tmp_path, monkeypatch):
    parser = argparse.ArgumentParser()
    f9_replay.add_arguments(parser)
    f9_replay.add_recording_arguments(parser)
    assert f9_replay.f10_view_user_args(parser.parse_args(["--f10-view", "FGONLY"])) == ["--fl4359-debug-view=1"]
    assert f9_replay.f10_view_user_args(parser.parse_args(["--f10-view", "DISOCCLUSION"])) == ["--fl4359-debug-view=2"]
    assert f9_replay.f10_view_user_args(parser.parse_args([])) == []
    # The runner appends them to extra_user_arg, which its Godot command
    # builder copies verbatim; the driver applies --fl4359-debug-view before
    # its F9 recording toggle. Source-order check (no runner execution).
    runner = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text()
    assert "f9_replay.add_recording_arguments(parser)" in runner
    assert "args.extra_user_arg = list(args.extra_user_arg or []) + f9_replay.f10_view_user_args(args)" in runner
    assert "for extra_arg in args.extra_user_arg:\n        if extra_arg:\n            cmd.append(extra_arg)" in runner
    driver = (ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd").read_text()
    assert driver.index('_cmd("--fl4359-debug-view=", "0")') < driver.index('call("toggle_screen_recording", false, true)')
    # A replay takes the recorded view; --f10-view there is refused.
    bundle = _recorded(tmp_path)
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))
    args = pipeline._args(bundle, f10_view="FGONLY")
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(args, tmp_path / "out")
    assert err.value.code == "f10_view_in_replay"
    args = pipeline._args(bundle, replay_f10_view="FGONLY")
    plan = f9_replay.plan(args, tmp_path / "out")
    assert "--fl4873-replay-f10-view=1" in args.extra_user_arg and plan["f10_override"] == "FGONLY"
    args = pipeline._args(bundle)
    f9_replay.plan(args, tmp_path / "out")
    assert "--fl4873-replay-f10-view=-1" in args.extra_user_arg


def _post_run(tmp_path, monkeypatch, preserver):
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))
    bundle = _recorded(tmp_path)
    out = _replay(tmp_path, "run", bundle)
    plan = f9_replay.plan(pipeline._args(bundle), out)
    return out, f9_replay.post_run(plan, 0, out / "godot_client.log", preserver=preserver)


def test_replay_results_are_preserved_durably(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    preserver = DeferredRunReceiptPreserver(repo_root=repo, fl_id="FL-4359", runner="test")
    out, code = _post_run(tmp_path, monkeypatch, preserver)
    assert code == 0
    manifest = preserver.manifest_path
    assert manifest is not None and manifest.is_relative_to(repo / "artifacts" / "run_receipts")
    names = {row["name"] for row in json.loads(manifest.read_text())["files"]}
    assert {"receipt.json", "replay_validation.json"} <= names
    for name in ("replay_validation.json",):
        assert (manifest.parent / name).read_bytes() == (out / name).read_bytes()
    assert f"preserved durable={manifest.parent}" in capsys.readouterr().out


def test_preservation_refuses_differing_bytes_and_fails_the_run(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    first = DeferredRunReceiptPreserver(repo_root=repo, fl_id="FL-4359", runner="test")
    out, code = _post_run(tmp_path, monkeypatch, first)
    assert code == 0
    # Same receipt bytes (same content address), different companion bytes.
    (out / "replay_validation.json").write_text('{"tampered": true}\n')
    second = DeferredRunReceiptPreserver(repo_root=repo, fl_id="FL-4359", runner="test")
    with pytest.raises(RuntimeError, match="refusing to replace preserved evidence"):
        f9_replay.preserve_companions(out, second)

    class Broken(DeferredRunReceiptPreserver):
        def preserve(self):
            raise RuntimeError("disk full")

    capsys.readouterr()
    (tmp_path / "second").mkdir()
    _, code = _post_run(tmp_path / "second", monkeypatch, Broken(repo_root=repo, fl_id="FL-4359", runner="test"))
    assert code == 1
    assert "RECEIPT_PRESERVATION_FAIL: disk full" in capsys.readouterr().out
