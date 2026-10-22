#!/usr/bin/env python3
"""FL-4873 replay A harness defects, offline (no Godot process starts).

Replay A (snapshot b98a7ce9c6f0, validation valid, no mismatches) printed
CAPTURE_INELIGIBLE for two harness reasons:
  1. recording_unfinished:incomplete -- the detached F9 finalizer ran from the
     materialized snapshot, and scripts/build_f10_frames_html.py was outside
     the snapshot pathspecs, so only the html (viewer) step failed.
  2. recorder_too_old:receipt_registry_revision_missing -- in replay mode
     receipt.json is the f9 replay receipt; the final variant registry is
     owned by the replay recording's manifest.json.
Covered here: the snapshot pathspecs cover every script the finalizer chain
executes (derived, so a new chain script cannot be missed, and the html
step runs in the snapshot); every named completion problem, html included,
disqualifies the capture (FL-4895: no viewer-only exemption); the registry owner is chosen per run kind and
recorder_too_old fires only when that owner lacks the fields.

Run: python3 -m pytest scripts/tests/test_fl4873_replay_capture_eligibility.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import launch_fl4359_manual_play as launcher  # noqa: E402
import run_fl4359_headed_movement as runner  # noqa: E402
import f9_replay  # noqa: E402
import test_fl4873_run_source_provenance as base  # noqa: E402
from test_fl4873_run_source_provenance import repo  # noqa: E402,F401  (fixture)

GOOD = {f"{base.PASS_RES}|FOO": base._variant(["FOO"], base.PASS_COMPILED_FOO)}


# ---------------------------------------------------------------- defect 1: finalizer chain in the snapshot

def test_recorder_launches_the_finalizer_entry_the_chain_walk_starts_from() -> None:
    controller = (ROOT / "godot_project" / "scripts" / "recording" / "ScreenRecordingController.gd").read_text()
    assert f'"res://../{runner.F9_FINALIZER_ENTRY}"' in controller


def test_every_script_the_f9_finalizer_chain_executes_is_inside_the_runner_snapshot() -> None:
    chain = runner.f9_finalizer_chain_sources()
    # Not vacuous: the known subprocess target and imported helpers are found.
    for expected in (runner.F9_FINALIZER_ENTRY, "scripts/build_f10_frames_html.py",
                     "scripts/recording/f9_session.py", "scripts/run_receipt_preservation.py"):
        assert expected in chain, chain
    specs = runner._runner_source_pathspecs(None)
    uncovered = [p for p in chain if not runner._covered_by(p, specs)]
    assert uncovered == [], f"F9 finalizer chain scripts outside the source snapshot: {uncovered}"


def test_chain_walk_finds_a_new_subprocess_target_and_import(tmp_path: Path) -> None:
    rec = tmp_path / "scripts" / "recording"
    rec.mkdir(parents=True)
    (rec / "f9_finalize_bundle.py").write_text(
        "import json\nimport helper_mod\nTOOL = ROOT / 'scripts' / 'new_viewer_tool.py'\n")
    (rec / "helper_mod.py").write_text("import sys\nOTHER = 'scripts/tools/deep_tool.py'\n")
    (tmp_path / "scripts" / "new_viewer_tool.py").write_text("import helper_two\n")
    (tmp_path / "scripts" / "helper_two.py").write_text("")
    (tmp_path / "scripts" / "tools").mkdir()
    (tmp_path / "scripts" / "tools" / "deep_tool.py").write_text("")
    assert runner.f9_finalizer_chain_sources(tmp_path) == (
        "scripts/helper_two.py", "scripts/new_viewer_tool.py", "scripts/recording/f9_finalize_bundle.py",
        "scripts/recording/helper_mod.py", "scripts/tools/deep_tool.py")
    # A new script outside the launcher + runner pathspecs is added to the snapshot scope.
    specs = list(launcher.RUNTIME_SOURCE_PATHSPECS) + list(runner.RUNNER_EXTRA_SOURCE_PATHSPECS)
    assert not runner._covered_by("scripts/new_viewer_tool.py", specs)


# ---------------------------------------------------------------- defect 1: every named problem disqualifies

def _completion(bundle: Path, status: str, problems) -> None:
    doc = {"status": status, "capture_drops": 0}
    if problems is not None:
        doc["problems"] = problems
    (bundle / "completion.json").write_text(json.dumps(doc))


@pytest.mark.parametrize("status,problems", [
    ("incomplete", ["html_failed"]),
    ("incomplete", ["html_failed", "png_conversion_failed"]),
    ("incomplete", ["html_failed", "mov_failed"]),
    ("incomplete", ["missing_png_files"]),
    ("incomplete", ["replay_missing"]),
    ("incomplete", []),
    ("incomplete", None),
    ("finalizing", ["html_failed"]),
])
def test_any_capture_problem_or_unnamed_problem_still_disqualifies(tmp_path: Path, status, problems) -> None:
    bundle = base._final_manifest_bundle(tmp_path, "FL4359_bad", first_frame={}, final=GOOD)
    _completion(bundle, status, problems)
    reasons, _ = runner._recording_ineligibility(bundle)
    assert f"recording_unfinished:{status}" in reasons


def test_html_failure_does_not_mask_other_capture_checks(tmp_path: Path) -> None:
    bundle = base._final_manifest_bundle(tmp_path, "FL4359_drop", first_frame={}, final=None)
    (bundle / "completion.json").write_text(json.dumps(
        {"status": "incomplete", "problems": ["html_failed"], "capture_drops": 2}))
    reasons, _ = runner._recording_ineligibility(bundle)
    assert "recording_dropped_frames:2" in reasons
    assert "recorder_too_old:compiled_variants_final_missing" in reasons


# ---------------------------------------------------------------- defect 2: registry owner per run kind

def _replay_run(repo: Path, tmp_path: Path, *, final: dict | None, receipt_extra: dict | None = None,
                record: bool = True, completion: str = "complete", problems: list | None = None):
    log = ""
    if record:
        bundle = base._final_manifest_bundle(tmp_path / "rec", "FL4359_replay-baseline_x", first_frame={},
                                             final=final, completion=completion)
        if problems is not None:
            _completion(bundle, completion, problems)
        log = f"FL4359_SCREEN_RECORDING named key=F9 bundle={bundle} mov={bundle}/recording.mov\n"
    receipt = {"schema": f9_replay.RECEIPT_SCHEMA, "validation": "valid", **(receipt_extra or {})}
    provenance, _m, _ = base._run_with_provenance(repo, receipt=receipt, log_text=log)
    return provenance


def test_replay_registry_is_owned_by_the_replay_recording_manifest(repo: Path, tmp_path: Path) -> None:
    provenance = _replay_run(repo, tmp_path, final=GOOD)
    assert provenance.ok, provenance.failures
    assert not any(r.startswith("recorder_too_old") for r in provenance.ineligible), provenance.ineligible
    compiled = provenance.record["executed_sources"]["compiled_shader_variants"]
    assert compiled["status"] == "verified"
    owner = compiled["registry_owner"]
    assert owner["kind"] == "replay_recording_manifest" and owner["ineligible"] == []
    assert owner["documents"][0].endswith("FL4359_replay-baseline_x/manifest.json")


def test_replay_recording_manifest_without_the_registry_is_recorder_too_old(repo: Path, tmp_path: Path) -> None:
    provenance = _replay_run(repo, tmp_path, final=None)
    assert "recorder_too_old:replay_recording_registry_revision_missing" in provenance.ineligible
    assert "recorder_too_old:receipt_registry_revision_missing" not in provenance.ineligible


def test_replay_without_a_recording_names_the_missing_owner(repo: Path, tmp_path: Path) -> None:
    provenance = _replay_run(repo, tmp_path, final=None, record=False)
    assert "replay_recording_missing" in provenance.ineligible
    assert "recorder_too_old:receipt_registry_revision_missing" not in provenance.ineligible


def test_replay_receipt_carrying_the_driver_registry_is_the_owner(repo: Path, tmp_path: Path) -> None:
    # f9_replay.post_run copies the driver's compiled_variants into the replay receipt.
    provenance = _replay_run(repo, tmp_path, final=GOOD, receipt_extra={"compiled_variants": GOOD})
    assert "recorder_too_old:receipt_registry_revision_missing" in provenance.ineligible
    provenance = _replay_run(repo, tmp_path / "b", final=GOOD, receipt_extra={
        "compiled_variants": GOOD, "compiled_variant_registry_revision": 5})
    assert not any(r.startswith("recorder_too_old") for r in provenance.ineligible), provenance.ineligible


def test_movement_receipt_without_revision_is_still_recorder_too_old(repo: Path) -> None:
    provenance, _m, _ = base._run_with_provenance(repo, receipt={"compiled_variants": GOOD})
    assert "recorder_too_old:receipt_registry_revision_missing" in provenance.ineligible
    owner = provenance.record["executed_sources"]["compiled_shader_variants"]["registry_owner"]
    assert owner["kind"] == "receipt"
