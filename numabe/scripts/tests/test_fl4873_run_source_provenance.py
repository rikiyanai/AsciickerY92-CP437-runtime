#!/usr/bin/env python3
"""FL-4873/FL-4888: run source provenance, executed-shader checks and durable
F9 evidence, offline.

Every test builds a temporary git repository (or a synthetic F9 bundle); no
Godot process starts. Covered:
  1. scripts/run_fl4359_headed_movement.py preserves the runtime source
     snapshot at run start and run end through the launcher's owner
     (scripts/launch_fl4359_manual_play.py); drift and an unpreserved snapshot
     fail the run while the receipt is still preserved.
  2. Executed True FF files and compiled shader variants are reproduced from
     the preserved snapshot bytes; a mismatch fails the run. Compile sites
     that bypass RenderShaderSource.record_compiled_variant are listed.
  3. F9 bundles (manifests, replay/, session.json, frames.html) and the Godot
     log trace are preserved content-addressed; frame PNGs and the MOV are
     hash-referenced.
  4. A saved selection is preserved with its metadata; f9_before_after refuses
     a selection bound to another manifest.
  5. Replay validity names the recording's preserved snapshot.

Run: python3 -m pytest scripts/tests/test_fl4873_run_source_provenance.py -q
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))

import launch_fl4359_manual_play as launcher  # noqa: E402
import run_fl4359_headed_movement as runner  # noqa: E402
import run_receipt_preservation as preservation  # noqa: E402
import f9_before_after as before_after  # noqa: E402
import f9_replay  # noqa: E402
import f9_replay_validate  # noqa: E402
import f9_session  # noqa: E402
import f9_source_identity  # noqa: E402

PASS_RES = "res://render/core/shaders/pass.glsl"
PASS_TEXT = '#[compute]\n#version 450\n#include "res://render/core/shaders/inc.glsl"\nvoid main() {}\n'
INC_COMMITTED = "float k() { return 1.0; }\n"
INC_DIRTY = "float k() { return 2.0; }\n"
# Independent derivation of the text RenderPipelineFactory.compile_compute
# hands the compiler for PASS_RES with defines ["FOO"] on the dirty tree:
# include expanded, "#[compute]" removed, edges stripped, define injected.
PASS_COMPILED_FOO = "#version 450\n#define FOO\nfloat k() { return 2.0; }\n\nvoid main() {}"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout.decode()


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def _variant(defines: list[str], compiled: str) -> dict:
    stage = _sha(compiled)
    return {"path": PASS_RES, "defines": defines, "stages": {"compute": stage},
            "sha256": _sha(f"compute:{stage}\n"), "bytes": len(compiled), "compile_count": 1,
            "superseded_sha256": []}


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shaders = repo / "godot_project" / "render" / "core" / "shaders"
    shaders.mkdir(parents=True)
    (repo / "godot_project" / "render" / "core" / "device").mkdir(parents=True)
    (repo / "godot_project" / "render" / "ascii").mkdir(parents=True)
    (repo / "colyseus_server").mkdir()
    (repo / "scripts").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (shaders / "pass.glsl").write_text(PASS_TEXT)
    (shaders / "inc.glsl").write_text(INC_COMMITTED)
    (repo / "godot_project" / "render" / "core" / "device" / "Factory.gd").write_text(
        "static func compile_compute(rd, path):\n"
        "\tvar spirv = rd.shader_compile_spirv_from_source(src)\n"
        "\tRenderShaderSource.record_compiled_variant(path, defines, {})\n")
    (repo / "godot_project" / "render" / "ascii" / "Stage.gd").write_text(
        "func _compile():\n"
        "\tvar spirv := _rd.shader_compile_spirv_from_source(input)\n"
        "\treturn spirv\n")
    (repo / "colyseus_server" / "index.ts").write_text("export {};\n")
    (repo / "scripts" / "run_fl4359_headed_movement.py").write_text("# runner\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    # The executed dirty tree: a tracked include edit and an untracked pass.
    (shaders / "inc.glsl").write_text(INC_DIRTY)
    (shaders / "new_pass.glsl").write_text("// untracked pass\n")
    return repo


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    repo = _fixture_repo(tmp_path)
    monkeypatch.setattr(launcher, "ROOT", repo)
    monkeypatch.setattr(runner, "ROOT", repo)
    monkeypatch.setattr(runner, "GODOT_PROJECT", repo / "godot_project")
    monkeypatch.setattr(f9_source_identity, "ROOT", repo)
    return repo


def _run_with_provenance(repo: Path, *, receipt: dict | None, between=None, log_text: str = ""):
    out = repo / ".run" / "headed-run"
    out.mkdir(parents=True, exist_ok=True)
    receipt_path = out / "receipt.json"
    godot_log = out / "godot_client.log"
    godot_log.write_text(log_text)
    provenance = runner.RunSourceProvenance(
        snapshot_root=repo / "artifacts" / "run_receipts" / "source_snapshots", finalize_wait_seconds=0)
    preserver = preservation.DeferredRunReceiptPreserver(
        repo_root=repo, fl_id="FL-4359", runner="scripts/run_fl4359_headed_movement.py")
    preserver.configure(receipt_path)
    preserver.add_finalizer(provenance.finalize)
    started = provenance.start(receipt_path=receipt_path, godot_log=godot_log,
                               pathspecs=tuple(launcher.RUNTIME_SOURCE_PATHSPECS)
                               + runner.RUNNER_EXTRA_SOURCE_PATHSPECS)
    if between is not None:
        between()
    if receipt is not None:
        receipt_path.write_text(json.dumps(receipt))
    manifest = preserver.preserve()
    return provenance, manifest, started


# ---------------------------------------------------------------- task 1

def test_start_and_end_snapshots_are_preserved_and_recorded_in_the_receipt(repo: Path) -> None:
    provenance, manifest, started = _run_with_provenance(repo, receipt={"pass": True})
    assert started and provenance.ok, provenance.failures
    bundle = manifest.parent
    assert bundle.parent == repo / "artifacts" / "run_receipts" / "fl-4359"
    preserved = json.loads((bundle / "receipt.json").read_text())
    record = preserved["run_source_provenance"]
    assert record["start"]["sha256"] == record["end"]["sha256"]
    assert record["source_state_stable"] is True
    snapshot = repo / record["start"]["snapshot"]
    assert snapshot.is_file() and snapshot.parent.name == record["start"]["sha256"]
    snapshot_doc = json.loads(snapshot.read_text())
    assert "scripts/run_fl4359_headed_movement.py" in snapshot_doc["pathspecs"]
    assert (snapshot.parent / "untracked" / "godot_project/render/core/shaders/new_pass.glsl").read_text() \
        == "// untracked pass\n"
    assert record["run_identity"]["runtime_source_snapshot"] == record["start"]["snapshot"]
    assert record["run_identity"]["owner"] == "scripts/run_fl4359_headed_movement.py"
    assert (bundle / runner.RUN_SOURCE_PROVENANCE_NAME).is_file()
    # Integrity holds, but this fixture has no final variant registry and an
    # unrecorded compile site: ineligible, never success (review P4/P5).
    assert provenance.verdict == "ineligible"
    assert "recorder_too_old:final_variant_registry_missing" in provenance.ineligible
    assert runner._apply_source_provenance_verdict(provenance, 0) == 1


def test_source_drift_between_start_and_end_fails_but_the_receipt_is_preserved(repo: Path) -> None:
    shader = repo / "godot_project" / "render" / "core" / "shaders" / "pass.glsl"
    provenance, manifest, _ = _run_with_provenance(
        repo, receipt={"pass": True}, between=lambda: shader.write_text(PASS_TEXT + "// edited mid-run\n"))
    assert not provenance.ok
    assert "source_drift_between_start_and_end" in provenance.failures
    record = json.loads((manifest.parent / "receipt.json").read_text())["run_source_provenance"]
    assert record["source_state_stable"] is False
    # Both trees exist durably: the executed start tree and the drifted end tree.
    assert (repo / record["start"]["snapshot"]).is_file() and (repo / record["end"]["snapshot"]).is_file()
    assert runner._apply_source_provenance_verdict(provenance, 0) == 1


def test_unpreservable_start_snapshot_fails_and_the_provenance_record_is_preserved(
        repo: Path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise launcher.SourceSnapshotError("refusing_to_overwrite_preserved_evidence: fixture")

    monkeypatch.setattr(launcher, "_runtime_source_state", refuse)
    provenance, manifest, started = _run_with_provenance(repo, receipt=None)
    assert started is False and not provenance.ok
    assert provenance.failures == ["start_snapshot_not_preserved"]
    assert manifest is not None and manifest.parent.name.startswith("headed-run__")
    record = json.loads((manifest.parent / runner.RUN_SOURCE_PROVENANCE_NAME).read_text())
    assert record["start"] == {"available": False,
                               "reason": "refusing_to_overwrite_preserved_evidence: fixture"}
    assert runner._apply_source_provenance_verdict(provenance, 0) == 1


def test_runner_wires_provenance_before_launch_and_into_the_launch_identity() -> None:
    source = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text()
    run_body = source.split("def _run(preserver", 1)[1].split("\ndef main()", 1)[0]
    start_at = run_body.index("provenance.start(")
    assert start_at < run_body.index("server = subprocess.Popen(")
    assert "return 7" in run_body[start_at:start_at + 600]
    assert "provenance.run_identity if provenance is not None else None" in run_body
    main_body = source.split("\ndef main()", 1)[1]
    assert "preserver.add_finalizer(provenance.finalize)" in main_body
    assert "_apply_source_provenance_verdict(provenance, result)" in main_body
    doc = json.loads(runner._launch_source_identity({"git_head": "h"}, {"label": "abc", "runtime_source_sha256": "s"}))
    assert doc["run_identity"] == {"label": "abc", "runtime_source_sha256": "s"}


def test_launcher_default_digest_is_unchanged_and_wider_scopes_get_their_own_address(repo: Path) -> None:
    capture = launcher._capture_runtime_source()
    legacy = hashlib.sha256()
    legacy.update(b"tracked-diff\0")
    legacy.update(capture["tracked_diff"])
    for path in sorted(capture["untracked"]):
        legacy.update(b"untracked-path\0" + path.encode() + b"\0" + capture["untracked"][path])
    default_state = launcher._runtime_source_state(repo / "snapshots")
    # The v1 content digest survives as a named field for older receipts; the
    # address (P3) now also covers head and pathspecs.
    assert default_state["content_sha256_v1"] == legacy.hexdigest()
    assert default_state["sha256"] != legacy.hexdigest()
    wide = launcher._runtime_source_state(
        repo / "snapshots", tuple(launcher.RUNTIME_SOURCE_PATHSPECS) + ("scripts/run_fl4359_headed_movement.py",))
    # Same dirty bytes (the runner file is clean), different scope: two addresses, no overwrite refusal.
    assert wide["sha256"] != default_state["sha256"]


def test_snapshot_reader_rebuilds_bytes_without_touching_the_checkout(repo: Path) -> None:
    state = launcher._runtime_source_state(repo / "snapshots")
    # The executed tree moves on after the run.
    (repo / "godot_project" / "render" / "core" / "shaders" / "inc.glsl").write_text("float k() { return 3.0; }\n")
    status_before = _git(repo, "status", "--porcelain")
    rebuilt = launcher.snapshot_file_bytes(repo / state["snapshot"], [
        "godot_project/render/core/shaders/inc.glsl",
        "godot_project/render/core/shaders/pass.glsl",
        "godot_project/render/core/shaders/new_pass.glsl",
        "godot_project/render/core/shaders/absent.glsl",
    ])
    assert rebuilt["godot_project/render/core/shaders/inc.glsl"] == INC_DIRTY.encode()
    assert rebuilt["godot_project/render/core/shaders/pass.glsl"] == PASS_TEXT.encode()
    assert rebuilt["godot_project/render/core/shaders/new_pass.glsl"] == b"// untracked pass\n"
    assert rebuilt["godot_project/render/core/shaders/absent.glsl"] is None
    assert _git(repo, "status", "--porcelain") == status_before
    assert _git(repo, "diff", "--cached", "--name-only") == ""


# ---------------------------------------------------------------- task 2

def test_compiled_variant_is_reproduced_from_the_snapshot(repo: Path) -> None:
    variants = {f"{PASS_RES}|FOO": _variant(["FOO"], PASS_COMPILED_FOO)}
    provenance, manifest, _ = _run_with_provenance(repo, receipt={"compiled_variants": variants, "compiled_variant_registry_revision": 1})
    assert provenance.ok, provenance.failures
    executed = json.loads((manifest.parent / "receipt.json").read_text())["run_source_provenance"]["executed_sources"]
    compiled = executed["compiled_shader_variants"]
    assert compiled["status"] == "verified" and compiled["sources"] == ["receipt.json"]
    row = compiled["variants"][0]
    assert row["reproduced"] is True
    assert {f["path"]: f["snapshot_sha256"] for f in row["files"]} == {
        "godot_project/render/core/shaders/pass.glsl": _sha(PASS_TEXT),
        "godot_project/render/core/shaders/inc.glsl": _sha(INC_DIRTY),
    }
    assert executed["true_ff_scope"]["status"] == "verified"
    assert executed["true_ff_scope"]["files_checked"] >= 3


def test_compiled_variant_not_reproducible_from_the_snapshot_fails_the_run(repo: Path) -> None:
    # The committed include text, not the executed dirty bytes.
    stale = PASS_COMPILED_FOO.replace("2.0", "1.0")
    variants = {f"{PASS_RES}|FOO": _variant(["FOO"], stale)}
    provenance, _manifest, _ = _run_with_provenance(repo, receipt={"compiled_variants": variants, "compiled_variant_registry_revision": 1})
    assert "compiled_variant_not_reproducible_from_snapshot" in provenance.failures
    compiled = provenance.record["executed_sources"]["compiled_shader_variants"]
    assert compiled["mismatched"] == [f"{PASS_RES}|FOO"]


def test_executed_file_hash_not_in_the_snapshot_fails_the_run(repo: Path, monkeypatch) -> None:
    real = f9_source_identity.executed_source_files

    def claimed():
        files, status = real()
        files["godot_project/render/core/shaders/pass.glsl"] = "0" * 64
        return files, status

    monkeypatch.setattr(f9_source_identity, "executed_source_files", claimed)
    provenance, _manifest, _ = _run_with_provenance(repo, receipt={"pass": True})
    assert "executed_file_not_reproducible_from_snapshot" in provenance.failures
    assert provenance.record["executed_sources"]["true_ff_scope"]["mismatched"] == [
        "godot_project/render/core/shaders/pass.glsl"]


def test_unrecorded_compile_sites_are_listed(repo: Path) -> None:
    sites = runner._unrecorded_shader_compile_sites(repo / "godot_project")
    assert sites == ["godot_project/render/ascii/Stage.gd:2"]
    provenance, _manifest, _ = _run_with_provenance(repo, receipt={"pass": True})
    executed = provenance.record["executed_sources"]
    assert executed["unrecorded_compile_sites"] == sites
    assert executed["compiled_shader_variants"]["status"] == "registry_missing"
    assert "unrecorded_compile_sites" in provenance.ineligible


def test_real_project_compile_sites_outside_the_recorder_are_named() -> None:
    sites = runner._unrecorded_shader_compile_sites(ROOT / "godot_project")
    # The Godot worker is closing these sites concurrently, so the live list
    # is not pinned; each listed site must be a real compile call whose
    # function records nothing, and the recording factory is never listed.
    files = {site.rsplit(":", 1)[0] for site in sites}
    assert "godot_project/render/core/device/RenderPipelineFactory.gd" not in files
    for site in sites:
        path, line = site.rsplit(":", 1)
        assert "shader_compile_spirv_from_source(" in (ROOT / path).read_text().splitlines()[int(line) - 1]


# ---------------------------------------------------------------- task 3

def _bundle(root: Path, name: str = "FL4359_rec") -> Path:
    bundle = root / name
    (bundle / "frames").mkdir(parents=True)
    (bundle / "replay").mkdir()
    (bundle / "frames" / "frame_00000.png").write_bytes(b"\x89PNG frame 0")
    (bundle / "frames" / "frame_00001.png").write_bytes(b"\x89PNG frame 1")
    (bundle / "recording.mov").write_bytes(b"mov bytes")
    frames = [{"seq": s, "engine_frame": 10 + s, "matrix": "[4,3]", "png": f"frames/frame_{s:05d}.png"} for s in (0, 1)]
    (bundle / "manifest.json").write_text(json.dumps({
        "identity": {"name": name, "source": {"executed_source_sha256": "a" * 64,
                                              "run_identity": {"label": "lbl", "runtime_source_snapshot": "x"}}},
        "frames": frames}))
    (bundle / "manifest.txt").write_text(f"recording_identity: {name}\nframes:\n"
                                         + "".join(f"frame: seq={s} matrix=[4,3]\n" for s in (0, 1)))
    (bundle / "completion.json").write_text(json.dumps({"status": "complete"}))
    (bundle / "frames.html").write_text("<html></html>")
    for part in ("recipe.json", "checkpoint.json", "frames.jsonl", "inputs.jsonl"):
        (bundle / "replay" / part).write_text("{}\n")
    return bundle


def _session(bundle: Path, cells=((1, 1), (2, 1)), **capture_extra) -> dict:
    ordered = sorted(cells, key=lambda c: (c[1], c[0]))
    capture = f9_session.capture_identity(bundle)
    doc = {"schema": f9_session.SCHEMA,
           "capture": dict({"id": capture["id"], "manifest_sha256": capture["manifest_sha256"]}, **capture_extra),
           "grid": {"cols": 4, "rows": 3},
           "selection": {"cells": [list(c) for c in ordered], "count": len(ordered), "bbox": [1, 1, 2, 1],
                         "digest": f9_session.selection_digest(ordered)},
           "frames": {"referenced": [1], "ranges": [[1, 1]], "current": 1}, "notes": {"1": "shimmer"}}
    (bundle / "session.json").write_text(json.dumps(doc))
    return doc


def test_f9_bundle_preservation_copies_structure_and_hash_references_media(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "Desktop")
    _session(bundle)
    manifest = f9_session.preserve_bundle(bundle, repo_root=tmp_path, fl_id="FL-4873", runner="test")
    dest = manifest.parent
    assert dest.parent == tmp_path / "artifacts" / "run_receipts" / "fl-4873"
    assert dest.name.startswith("f9-bundle-FL4359_rec__")
    for name in ("manifest.json", "manifest.txt", "completion.json", "session.json",
                 "replay/recipe.json", "replay/checkpoint.json", "replay/frames.jsonl", "replay/inputs.jsonl"):
        assert (dest / name).read_bytes() == (bundle / name).read_bytes(), name
    # The recording's own viewer is kept, but under original/: its images are
    # the full frames, which the durable copy does not hold.
    assert (dest / "original" / "frames.html").read_bytes() == (bundle / "frames.html").read_bytes()
    assert not (dest / "frames.html").exists()
    assert not (dest / "frames").exists() and not (dest / "recording.mov").exists()
    doc = json.loads(manifest.read_text())
    referenced = {row["path"]: row for row in doc["referenced_not_copied"]}
    assert set(referenced) == {"frames/frame_00000.png", "frames/frame_00001.png", "recording.mov"}
    assert referenced["recording.mov"]["sha256"] == _sha(b"mov bytes")
    assert referenced["recording.mov"]["storage"] == str(bundle / "recording.mov")
    selection = doc["metadata"]["selection"]
    assert selection["status"] == "valid" and selection["digest"] == f9_session.selection_digest([(1, 1), (2, 1)])
    assert selection["cells"] == [[1, 1], [2, 1]] and selection["referenced"] == [1]
    assert selection["notes"] == {"1": "shimmer"}
    assert selection["bundle_manifest_json_sha256"] == _sha((bundle / "manifest.json").read_bytes())
    assert selection["session_sha256"] == _sha((bundle / "session.json").read_bytes())
    # Idempotent for the same bytes; a later saved selection is a new durable state.
    assert f9_session.preserve_bundle(bundle, repo_root=tmp_path, fl_id="FL-4873", runner="test") == manifest
    _session(bundle, cells=((0, 0),))
    assert f9_session.preserve_bundle(bundle, repo_root=tmp_path, fl_id="FL-4873", runner="test").parent != dest


def test_f9_bundle_preservation_refuses_to_overwrite_different_bytes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "Desktop")
    manifest = f9_session.preserve_bundle(bundle, repo_root=tmp_path, fl_id="FL-4873", runner="test")
    (manifest.parent / "manifest.txt").write_text("tampered\n")
    with pytest.raises(RuntimeError, match="refusing to replace preserved evidence"):
        f9_session.preserve_bundle(bundle, repo_root=tmp_path, fl_id="FL-4873", runner="test")


def test_f9_session_cli_preserves_and_reports_the_durable_path(tmp_path: Path, capsys) -> None:
    bundle = _bundle(tmp_path / "Desktop")
    rc = f9_session.main([str(bundle), "--preserve", "--fl-id", "FL-4873", "--repo-root", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and report["ok"] and report["referenced"] == 3 and report["selection"] == "absent"
    assert Path(report["durable"]).parent == tmp_path / "artifacts" / "run_receipts" / "fl-4873"


def test_runner_preserves_log_named_f9_bundles_replay_inputs_and_the_trace_log(repo: Path, tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    first = _bundle(desktop, "FL4359_pending")
    renamed = desktop / "FL4359_persp"
    first.rename(renamed)
    replay_input = _bundle(tmp_path / "Operator", "FL4359_input")
    _session(replay_input)
    log = ("FL4359_SCREEN_RECORDING named key=F9 bundle=%s mov=%s/recording.mov\n"
           "FL4359_SCREEN_RECORDING renamed key=F9 from=%s to=%s reason=first_drawn_projection_perspective\n"
           "[true_ff_trace] vsb frame=901 rect=[0,0,4,3] cells=[]\n") % (first, first, first, renamed)
    out = repo / ".run" / "headed-run"
    provenance = runner.RunSourceProvenance(
        snapshot_root=repo / "artifacts" / "run_receipts" / "source_snapshots", finalize_wait_seconds=0)
    provenance.input_bundles.append(replay_input)
    out.mkdir(parents=True)
    (out / "godot_client.log").write_text(log)
    preserver = preservation.DeferredRunReceiptPreserver(repo_root=repo, fl_id="FL-4359", runner="r")
    preserver.configure(out / "receipt.json")
    preserver.add_finalizer(provenance.finalize)
    assert provenance.start(receipt_path=out / "receipt.json", godot_log=out / "godot_client.log",
                            pathspecs=tuple(launcher.RUNTIME_SOURCE_PATHSPECS))
    (out / "receipt.json").write_text("{}")
    manifest = preserver.preserve()
    assert provenance.ok, provenance.failures
    rows = {row["role"]: row for row in provenance.record["f9_bundles"]}
    assert rows["recorded"]["bundle"] == str(renamed) and rows["recorded"]["completion_status"] == "complete"
    assert rows["replay_input"]["selection_status"] == "valid"
    for row in rows.values():
        assert (repo / row["durable_manifest"]).is_file()
    # The Godot diagnostic trace leaves .run with the receipt.
    assert "[true_ff_trace]" in (manifest.parent / "godot_client.log").read_text()


# ---------------------------------------------------------------- task 4

def test_session_bound_to_another_manifest_json_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _session(bundle, bundle_manifest_sha256=_sha((bundle / "manifest.json").read_bytes()))
    assert f9_session.load_session(bundle)["capture"]["bundle_manifest_sha256"] == \
        _sha((bundle / "manifest.json").read_bytes())
    doc = json.loads((bundle / "manifest.json").read_text())
    doc["frames"][0]["engine_frame"] = 99
    (bundle / "manifest.json").write_text(json.dumps(doc))
    with pytest.raises(f9_session.SessionMismatch) as exc:
        f9_session.load_session(bundle)
    assert exc.value.code == "bundle_manifest_hash"


def _before_after_args(before: Path, out: Path, repo_root: Path) -> list[str]:
    return [str(before), "--after-self", "--out", str(out), "--no-video", "--repo-root", str(repo_root)]


def test_before_after_refuses_selection_bound_to_another_bundle_manifest(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_before_after as fixtures  # noqa: E402

    before = fixtures.make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    fixtures.write_session(before, "B")
    session = json.loads((before / "session.json").read_text())
    session["capture"]["bundle_manifest_sha256"] = "0" * 64
    (before / "session.json").write_text(json.dumps(session))
    args = before_after.build_parser().parse_args(_before_after_args(before, tmp_path / "out", tmp_path))
    with pytest.raises(before_after.Refused) as exc:
        before_after.run(args, [])
    assert exc.value.code == "session_bundle_manifest_hash"
    fixtures.write_session(before, "B")
    (before / "manifest.txt").write_text((before / "manifest.txt").read_text() + "# edited\n")
    with pytest.raises(before_after.Refused) as exc:
        before_after.run(args, [])
    assert exc.value.code == "session_manifest_hash"


def test_before_after_preserves_the_selection_beside_its_outputs(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_before_after as fixtures  # noqa: E402

    before = fixtures.make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    fixtures.write_session(before, "B")
    after = fixtures.make_replay(tmp_path / "A", before, list(range(2, 9)), [1] * 7)
    argv = [str(before), "--after", str(after), "--out", str(tmp_path / "out"), "--no-video",
            "--repo-root", str(tmp_path)]
    manifest = before_after.run(before_after.build_parser().parse_args(argv), argv)
    durable = Path(manifest["_durable"])
    assert (durable / before_after.SESSION_COPY_NAME).read_bytes() == (before / "session.json").read_bytes()
    preserved = json.loads((durable / before_after.MANIFEST_NAME).read_text())
    assert preserved["inputs"]["before"]["session"]["preserved_as"] == before_after.SESSION_COPY_NAME
    assert preserved["inputs"]["before"]["identity"]["bundle_manifest_sha256"] == \
        _sha((before / "manifest.json").read_bytes())


# ---------------------------------------------------------------- task 5

def _recording_with_snapshot(repo: Path, tmp_path: Path, *, tamper: bool = False) -> Path:
    state = launcher._runtime_source_state(repo / "artifacts" / "run_receipts" / "source_snapshots")
    files, _status = f9_source_identity.executed_source_files()
    if tamper:
        files["godot_project/render/core/shaders/inc.glsl"] = _sha(INC_COMMITTED)
    bundle = tmp_path / "Desktop" / "FL4359_snap"
    (bundle / "replay").mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps({"identity": {"name": "FL4359_snap", "source": {
        "executed_source_sha256": f9_source_identity.inventory_sha256(files), "executed_source_files": files,
        "run_identity": {"git_head": state["head"], "runtime_source_sha256": state["sha256"],
                         "runtime_source_snapshot": state["snapshot"]}}}}))
    return bundle


def test_recording_snapshot_status_names_absent_verified_and_mismatched_snapshots(repo: Path, tmp_path: Path) -> None:
    assert f9_replay_validate.recording_snapshot_status(_bundle(tmp_path / "old"), repo)["status"] == "missing"
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "manifest.json").write_text(json.dumps({"identity": {"source": {"executed_source_sha256": "a" * 64}}}))
    assert f9_replay_validate.recording_snapshot_status(plain, repo)["status"] == "absent"
    verified = f9_replay_validate.recording_snapshot_status(_recording_with_snapshot(repo, tmp_path), repo)
    assert verified["status"] == "verified" and verified["files_checked"] >= 3
    tampered = f9_replay_validate.recording_snapshot_status(
        _recording_with_snapshot(repo, tmp_path / "t", tamper=True), repo)
    assert tampered["status"] == "mismatch"
    assert tampered["mismatched"] == ["godot_project/render/core/shaders/inc.glsl"]


def test_baseline_replay_refusal_points_at_the_preserved_snapshot(repo: Path, tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_replay_pipeline as pipeline  # noqa: E402

    bundle = pipeline._recorded(tmp_path)
    state = launcher._runtime_source_state(repo / "artifacts" / "run_receipts" / "source_snapshots")
    files, _ = f9_source_identity.executed_source_files()
    doc = json.loads((bundle / "manifest.json").read_text())
    doc["identity"]["source"] = {"executed_source_sha256": pipeline.SHA, "executed_source_files": files,
                                 "run_identity": {"git_head": state["head"], "runtime_source_snapshot": state["snapshot"],
                                                  "runtime_source_sha256": state["sha256"]}}
    (bundle / "manifest.json").write_text(json.dumps(doc))
    monkeypatch.setattr(f9_replay_validate, "ROOT", repo)
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: ("b" * 64, "captured"))
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(pipeline._args(bundle), tmp_path / "out")
    assert err.value.code == "source_mismatch"
    assert f"preserved and verified at {state['snapshot']}" in str(err.value)
    # A recording whose preserved bytes do not reproduce its own identity is refused outright.
    doc["identity"]["source"]["executed_source_files"]["godot_project/render/core/shaders/inc.glsl"] = "0" * 64
    (bundle / "manifest.json").write_text(json.dumps(doc))
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(pipeline._args(bundle), tmp_path / "out")
    assert err.value.code == "recording_snapshot_mismatch"


def test_validation_reports_the_recording_snapshot(repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(f9_replay_validate, "ROOT", repo)
    bundle = _recording_with_snapshot(repo, tmp_path, tamper=True)
    result = f9_replay_validate.validate(bundle, tmp_path / "no-replay", "baseline", "pose", "x" * 64)
    assert result["recording_snapshot"]["status"] == "mismatch"
    assert "recording_snapshot_mismatch" in result["mismatches"]


# ---------------------------------------------------------------- round 2 A: preserved selection viewer

import re  # noqa: E402
import shutil  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import build_f10_frames_html as viewer_builder  # noqa: E402

GCOLS, GROWS, GCW, GCH = 8, 6, 4, 4
GW, GH = GCOLS * GCW, GROWS * GCH - 2  # last row clipped by the image edge, as in real recordings


def _cell_colour(cx: int, cy: int, seq: int) -> tuple:
    return (cx * 30 % 256, cy * 40 % 256, seq * 20 % 256)


def _image_bundle(root: Path, name: str = "FL4359_img", seqs=range(10)) -> Path:
    from PIL import Image

    bundle = root / name
    (bundle / "frames").mkdir(parents=True)
    lines, frames = [], []
    for seq in seqs:
        image = Image.new("RGB", (GW, GH))
        for cy in range(GROWS):
            for cx in range(GCOLS):
                for py in range(cy * GCH, min(GH, (cy + 1) * GCH)):
                    for px in range(cx * GCW, (cx + 1) * GCW):
                        image.putpixel((px, py), _cell_colour(cx, cy, seq))
        png = f"frames/frame_{seq:05d}.png"
        image.save(bundle / png)
        lines.append(f"frame: seq={seq} engine={100 + seq} unix_ms={1000 + 33 * seq} f10=1:FGONLY "
                     f"matrix=[{GCOLS},{GROWS}] image={GW}x{GH} cell={GCW}x{GCH}:terminal png={png}")
        frames.append({"seq": seq, "png": png})
    (bundle / "manifest.txt").write_text(f"recording_identity: {name}\nframes:\n" + "\n".join(lines) + "\n")
    (bundle / "manifest.json").write_text(json.dumps({"identity": {"name": name}, "frames": frames}))
    (bundle / "recording.mov").write_bytes(b"mov")
    (bundle / "frames.html").write_text("<html>original viewer</html>")
    return bundle


def _image_session(bundle: Path, cells=((3, 2), (4, 2)), referenced=(4, 5), name="FL4359_img",
                   path: Path | None = None) -> Path:
    ordered = sorted(cells, key=lambda c: (c[1], c[0]))
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    doc = {"schema": f9_session.SCHEMA,
           "capture": {"id": name, "manifest_sha256": _sha((bundle / "manifest.txt").read_bytes())},
           "grid": {"cols": GCOLS, "rows": GROWS},
           "selection": {"cells": [list(c) for c in ordered], "count": len(ordered),
                         "bbox": [min(xs), min(ys), max(xs), max(ys)], "digest": f9_session.selection_digest(ordered)},
           "frames": {"referenced": list(referenced), "ranges": [], "current": referenced[0]},
           "notes": {str(referenced[0]): "shimmer"}, "saved_at": "2026-09-26T10:00:00Z"}
    target = path or bundle / "session.json"
    target.write_text(json.dumps(doc))
    return target


def _viewer_frames(html: str) -> list:
    return json.loads(re.search(r"const FRAMES = (.*?);\n", html).group(1))


def test_preserved_viewer_renders_from_the_durable_folder_alone(tmp_path: Path) -> None:
    bundle = _image_bundle(tmp_path / "Desktop")
    _image_session(bundle)
    manifest = f9_session.preserve_bundle(bundle, repo_root=tmp_path / "repo", fl_id="FL-4873", runner="t")
    isolated = tmp_path / "isolated"
    shutil.copytree(manifest.parent, isolated)
    shutil.rmtree(tmp_path / "Desktop")  # the recording volume is gone
    doc = json.loads((isolated / "preservation_manifest.json").read_text())
    rows = {row["name"]: row for row in doc["files"]}
    html = (isolated / "viewer.html").read_text()
    frames = _viewer_frames(html)
    # Referenced frames 4-5 plus the frame on each side of the run.
    assert [int(f["seq"]) for f in frames] == [3, 4, 5, 6]
    for frame in frames:
        assert not frame["png"].startswith("frames/")
        data = (isolated / frame["png"]).read_bytes()
        assert _sha(data) == rows[frame["png"]]["sha256"]
    viewer = doc["metadata"]["viewer"]
    assert viewer["preserved"] == "selection_region_only"
    assert viewer["full_frames"] == {"location": str(bundle / "frames") + "/", "storage_status": "PENDING",
                                     "viewer_preserved": False}
    notice = json.loads(re.search(r"const PRESERVED = (.*?);\n", html).group(1))["notice"]
    assert "stored by sha256 only at " + str(bundle / "frames") + "/" in notice
    assert "PENDING" in notice and "full-frame viewer is NOT preserved" in notice and "[3, 4, 5, 6]" in notice
    assert all(row["storage_status"] == "PENDING" for row in doc["referenced_not_copied"])
    assert {row["path"] for row in doc["referenced_not_copied"]} == \
        {f"frames/frame_{s:05d}.png" for s in range(10)} | {"recording.mov"}
    assert doc["metadata"]["original_viewer"] == {"file": "original/frames.html", "renders_from_durable_copy": False,
                                                  "reason": "its images are the full frames, stored by hash only"}


def test_crop_origin_offset_places_the_selected_cell_on_its_own_pixels(tmp_path: Path) -> None:
    from PIL import Image

    bundle = _image_bundle(tmp_path / "Desktop")
    _image_session(bundle, cells=((3, 2), (4, 2)), referenced=(4,))
    manifest = f9_session.preserve_bundle(bundle, repo_root=tmp_path / "repo", fl_id="FL-4873", runner="t")
    crop_row = next(r for r in json.loads(manifest.read_text())["metadata"]["viewer"]["crops"] if r["seq"] == 4)
    # Cells (3,2)-(4,2) plus one cell of margin: cells (2,1)-(5,3) = pixels x 8..24, y 4..16.
    assert crop_row["crop_cells"] == [2, 1, 5, 3] and crop_row["crop_px"] == [8, 4, 16, 12]
    crop = Image.open(manifest.parent / crop_row["file"]).convert("RGB")
    frame = next(f for f in _viewer_frames((manifest.parent / "viewer.html").read_text()) if int(f["seq"]) == 4)
    # The viewer draws cell rects in full-frame pixels minus frame.crop (x, y).
    x0, y0, x1, y1 = viewer_builder.cell_rect(3, 2, frame)
    left, top = x0 - frame["crop"]["x"], y0 - frame["crop"]["y"]
    assert (left, top) == (4, 4)
    assert crop.getpixel((left, top)) == _cell_colour(3, 2, 4)
    assert crop.getpixel((left + (x1 - x0) - 1, top + (y1 - y0) - 1)) == _cell_colour(3, 2, 4)
    assert crop.getpixel((left - 1, top)) == _cell_colour(2, 2, 4)
    # The clipped last row keeps the image-edge rule of the full frame.
    edge = viewer_builder.selection_crop(frame, [(7, 5)])
    assert edge == {"x": 24, "y": 16, "w": 8, "h": GH - 16, "cells": [6, 4, 7, 5]}
    html = (manifest.parent / "viewer.html").read_text()
    assert "(x0 - ox) * sx" in html and "cr.x + (e.clientX - r.left) / r.width * cr.w" in html


def test_bundle_without_a_selection_records_the_viewer_as_not_preserved(tmp_path: Path) -> None:
    bundle = _image_bundle(tmp_path / "Desktop")
    manifest = f9_session.preserve_bundle(bundle, repo_root=tmp_path / "repo", fl_id="FL-4873", runner="t")
    doc = json.loads(manifest.read_text())
    viewer = doc["metadata"]["viewer"]
    assert viewer["preserved"] == "none" and viewer["entry"] is None and viewer["crops"] == []
    assert viewer["full_frames"]["viewer_preserved"] is False
    assert "full-frame viewer is NOT preserved" in viewer["reason"]
    assert not (manifest.parent / "viewer.html").exists() and not (manifest.parent / "crops").exists()


def test_normal_viewer_build_is_unchanged_by_the_preserved_mode(tmp_path: Path) -> None:
    bundle = _image_bundle(tmp_path / "Desktop")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_f10_frames_html.py"), str(bundle)],
                   check=True, capture_output=True)
    html = (bundle / "frames.html").read_text()
    assert "const PRESERVED = null;" in html
    assert [f["png"] for f in _viewer_frames(html)] == [f"frames/frame_{s:05d}.png" for s in range(10)]


# ---------------------------------------------------------------- round 2 B: archive before use

def test_before_after_archives_the_selection_before_use_and_refuses_a_tampered_archive(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_before_after as fixtures  # noqa: E402

    before = fixtures.make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    fixtures.write_session(before, "B")
    argv = [str(before), "--after-self", "--out", str(tmp_path / "out"), "--no-video", "--repo-root", str(tmp_path)]
    manifest = before_after.run(before_after.build_parser().parse_args(argv), argv)
    archive = Path(manifest["inputs"]["before"]["session"]["archived_before_use"])
    assert (archive.parent / "session.json").read_bytes() == (before / "session.json").read_bytes()
    assert (archive.parent / "viewer.html").is_file()
    # The viewer autosaves a new selection: the next use archives a new state,
    # the earlier one stays.
    fixtures.write_session(before, "B", cells=[(1, 1)], referenced=(2,))
    second = Path(before_after.run(before_after.build_parser().parse_args(argv), argv)
                  ["inputs"]["before"]["session"]["archived_before_use"])
    assert second.parent != archive.parent and (archive.parent / "session.json").is_file()
    # An archived copy with different bytes is never overwritten: the run is refused.
    (second.parent / "session.json").write_text("{}")
    with pytest.raises(before_after.Refused) as exc:
        before_after.run(before_after.build_parser().parse_args(argv), argv)
    assert exc.value.code == "selection_not_archived"


def test_replay_plan_archives_the_consumed_selection_before_use(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_replay_pipeline as pipeline  # noqa: E402

    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))
    bundle = pipeline._recorded(tmp_path)
    elsewhere = tmp_path / "operator_selection.json"
    shutil.copyfile(bundle / "session.json", elsewhere)
    plan = f9_replay.plan(pipeline._args(bundle, selection=str(elsewhere)), tmp_path / "out")
    archive = Path(plan["selection_archive"])
    assert archive.is_file() and archive.parent.parent == tmp_path / "archive_repo" / "artifacts" / "run_receipts" / "fl-4873"
    assert (archive.parent / "selection" / "operator_selection.json").read_bytes() == elsewhere.read_bytes()
    assert json.loads(archive.read_text())["metadata"]["selection"]["session_path"] == str(elsewhere.resolve())
    # A failing archive refuses the replay before anything runs.
    monkeypatch.setattr(f9_replay.f9_session, "preserve_bundle",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store unavailable")))
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(pipeline._args(bundle), tmp_path / "out")
    assert err.value.code == "selection_not_archived"


def test_runner_passes_the_consumed_selection_to_end_of_run_preservation() -> None:
    source = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text()
    run_body = source.split("def _run(preserver", 1)[1]
    assert "_register_replay_input(provenance, replay_plan)" in run_body
    assert "selection_input=selection_input)" in source.split("def _preserve_bundles", 1)[1].split("def finalize", 1)[0]


def test_preserve_sweep_archives_new_selections_once(tmp_path: Path, capsys) -> None:
    desktop = tmp_path / "Desktop"
    selected = _image_bundle(desktop, "FL4359_img")
    _image_session(selected)
    _image_bundle(desktop, "FL4359_unselected")
    (desktop / "loose").mkdir()
    (desktop / "loose" / "session.json").write_text("{}")  # not a bundle: ignored
    repo = tmp_path / "repo"
    rc = f9_session.main(["--preserve-sweep", str(desktop), "--repo-root", str(repo)])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and [row["bundle"] for row in report["archived"]] == [str(selected)]
    assert report["already_archived"] == [] and report["failed"] == []
    assert (Path(report["archived"][0]["durable"]) / "viewer.html").is_file()
    assert f9_session.main(["--preserve-sweep", str(desktop), "--repo-root", str(repo)]) == 0
    assert json.loads(capsys.readouterr().out)["already_archived"] == [str(selected)]
    _image_session(selected, cells=((0, 0),), referenced=(8,))
    assert f9_session.main(["--preserve-sweep", str(desktop), "--repo-root", str(repo)]) == 0
    again = json.loads(capsys.readouterr().out)
    assert [row["bundle"] for row in again["archived"]] == [str(selected)]
    assert again["archived"][0]["durable"] != report["archived"][0]["durable"]


# ================================================================ round 3 (review P1-P7)

def _pipeline():
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_replay_pipeline as pipeline  # noqa: E402

    return pipeline


def _rewrite_selection(path: Path, bundle: Path, cells) -> bytes:
    """What the viewer autosave does: a new valid session.json in place."""
    doc = json.loads(path.read_text())
    ordered = sorted(cells, key=lambda c: (c[1], c[0]))
    doc["selection"] = {"cells": [list(c) for c in ordered], "count": len(ordered),
                        "bbox": [min(c[0] for c in ordered), min(c[1] for c in ordered),
                                 max(c[0] for c in ordered), max(c[1] for c in ordered)],
                        "digest": f9_session.selection_digest(ordered)}
    data = json.dumps(doc).encode()
    path.write_bytes(data)
    return data


def _autosave_after_first_validation(monkeypatch, path: Path, bundle: Path, cells=((0, 0),)) -> None:
    """Rewrite the selection right after the consumer's first validation: the
    window between the old read (load_session) and the old re-read (archive)."""
    real = f9_session.validate
    state = {"done": False}

    def validate_then_autosave(doc, bundle_arg, expect_cells=None):
        result = real(doc, bundle_arg, expect_cells)
        if not state["done"]:
            state["done"] = True
            _rewrite_selection(path, bundle, cells)
        return result

    monkeypatch.setattr(f9_session, "validate", validate_then_autosave)


# ---------------------------------------------------------------- P1 immutable selection handoff

def test_p1_replay_plan_archives_exactly_the_selection_it_uses_despite_autosave(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline()
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))
    bundle = pipeline._recorded(tmp_path)
    used = (bundle / "session.json").read_bytes()
    _autosave_after_first_validation(monkeypatch, bundle / "session.json", bundle)
    plan = f9_replay.plan(pipeline._args(bundle), tmp_path / "out")
    assert json.loads((bundle / "session.json").read_text())["selection"]["cells"] == [[0, 0]]  # autosave happened
    assert plan["session"]["cells"] == [(3, 4), (4, 4)]
    # The race itself (fails on 2f65d4823, which archived the autosaved [(0,0)]):
    archive_dir = Path(plan["selection_archive"]).parent
    assert json.loads((archive_dir / "session.json").read_text())["selection"]["cells"] == [[3, 4], [4, 4]]
    archived = f9_session.archived_selection_path(Path(plan["selection_archive"]))
    assert archived.read_bytes() == used
    assert plan["selection_sha256"] == plan["selection_archived_sha256"] == _sha(used)
    record = json.loads(Path(plan["selection_archive"]).read_text())["metadata"]
    assert record["selection"]["cells"] == [[3, 4], [4, 4]] and record["viewer"]["selection_sha256"] == _sha(used)


def test_p1_before_after_archives_exactly_the_selection_it_uses_despite_autosave(tmp_path, monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "tests"))
    import test_f9_before_after as fixtures  # noqa: E402

    before = fixtures.make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    fixtures.write_session(before, "B")
    used = (before / "session.json").read_bytes()
    _autosave_after_first_validation(monkeypatch, before / "session.json", before)
    argv = [str(before), "--after-self", "--out", str(tmp_path / "out"), "--no-video", "--repo-root", str(tmp_path)]
    manifest = before_after.run(before_after.build_parser().parse_args(argv), argv)
    session_row = manifest["inputs"]["before"]["session"]
    # The race itself (fails on 2f65d4823, which archived the autosaved cell):
    assert (Path(session_row["archived_before_use"]).parent / "session.json").read_bytes() == used
    assert session_row["sha256"] == session_row["archived_sha256"] == _sha(used)
    assert manifest["selection"]["digest"] == f9_session.selection_digest(fixtures.SEL)
    archived = f9_session.archived_selection_path(Path(session_row["archived_before_use"]))
    assert archived.read_bytes() == used
    assert (tmp_path / "out" / before_after.SESSION_COPY_NAME).read_bytes() == used


def test_p1_headed_runner_selection_handoff_uses_the_archived_bytes(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline()
    archive_root = tmp_path / "archive_repo"
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", archive_root)
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))
    bundle = pipeline._recorded(tmp_path)
    selection = tmp_path / "operator_selection.json"
    selection.write_bytes((bundle / "session.json").read_bytes())
    used = selection.read_bytes()
    plan = f9_replay.plan(pipeline._args(bundle, selection=str(selection)), tmp_path / "out")
    _rewrite_selection(selection, bundle, [(0, 0)])  # viewer autosave after the plan, before run end
    monkeypatch.setattr(runner, "ROOT", archive_root)
    monkeypatch.setattr(launcher, "ROOT", archive_root)
    provenance = runner.RunSourceProvenance(snapshot_root=archive_root / "snapshots", finalize_wait_seconds=0)
    runner._register_replay_input(provenance, plan)
    row = provenance._preserve_bundles([(bundle, "replay_input")])[0]
    assert "error" not in row, row
    assert row["selection_sha256_used"] == row["selection_sha256_archived"] == _sha(used)
    assert not provenance.failures


# ---------------------------------------------------------------- P2 sweep discovery

def test_p2_sweep_finds_nested_recordings_and_reports_skipped_dirs(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    nested = _image_bundle(tmp_path / "Desktop" / "ay9jit", "FL4359_img")
    _image_session(nested)
    _image_bundle(tmp_path / "Desktop" / "ay9jit", "FL4359_bare")  # no selection
    too_deep = tmp_path / "Desktop" / "a" / "b" / "c" / "d"
    _image_session(_image_bundle(too_deep, "FL4359_img"))
    locked = tmp_path / "Desktop" / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        rc = f9_session.main(["--preserve-sweep", "--repo-root", str(tmp_path / "repo")])  # default ~/Desktop
        report = json.loads(capsys.readouterr().out)
    finally:
        locked.chmod(0o755)
    assert [row["bundle"] for row in report["archived"]] == [str(nested)]
    skipped = {row.get("bundle") or row.get("path"): row.get("reason") for row in report["skipped"]}
    assert skipped[str(tmp_path / "Desktop" / "ay9jit" / "FL4359_bare")] == "no_session_json"
    assert any(path.endswith("locked") and reason.startswith("unreadable") for path, reason in skipped.items())
    assert str(too_deep / "FL4359_img") not in json.dumps(report)
    assert rc == 0


# ---------------------------------------------------------------- P3 snapshot address

def test_p3_one_dirty_diff_on_two_heads_gets_two_addresses(repo: Path) -> None:
    root = repo / "snapshots"
    first = launcher._runtime_source_state(root)
    (repo / "docs.txt").write_text("outside the runtime pathspecs\n")
    _git(repo, "add", "docs.txt")
    _git(repo, "commit", "-q", "-m", "move head, keep the runtime diff")
    second = launcher._runtime_source_state(root)
    assert first["head"] != second["head"]
    assert first["content_sha256_v1"] == second["content_sha256_v1"]  # the old address collided here
    assert first["sha256"] != second["sha256"]
    assert json.loads((repo / second["snapshot"]).read_text())["head"] == second["head"]
    # One tree, one address: an identical re-run reuses the directory.
    assert launcher._runtime_source_state(root) == second
    # Different pathspec sets over the same bytes: different addresses.
    wide = launcher._runtime_source_state(root, tuple(launcher.RUNTIME_SOURCE_PATHSPECS) + ("docs.txt",))
    assert wide["sha256"] != second["sha256"]
    # Existing directory with different bytes: hard failure.
    (repo / second["snapshot"]).parent.joinpath("tracked.diff").write_bytes(b"tampered")
    with pytest.raises(launcher.SourceSnapshotError, match="refusing_to_overwrite"):
        launcher._runtime_source_state(root)


# ---------------------------------------------------------------- P4 final registry

def _final_manifest_bundle(root: Path, name: str, *, first_frame: dict, final: dict | None,
                           completion: str = "complete", revision=5, frame_revision=True) -> Path:
    bundle = root / name
    (bundle / "frames").mkdir(parents=True)
    (bundle / "frames" / "frame_00000.png").write_bytes(b"png")
    row = {"seq": 0, "png": "frames/frame_00000.png"}
    if frame_revision:
        row["variant_revision"] = 1
    doc = {"identity": {"name": name}, "frames": [row], "counts": {"dropped": 0},
           "settings": {"first_frame_view_state": {"compiled_shader_variants": first_frame}}}
    if final is not None:
        doc["compiled_variants_final"] = final
        doc["compiled_variant_registry_revision_final"] = revision
    (bundle / "manifest.json").write_text(json.dumps(doc))
    (bundle / "manifest.txt").write_text(f"recording_identity: {name}\nframes:\nframe: seq=0 png=frames/frame_00000.png\n")
    (bundle / "completion.json").write_text(json.dumps({"status": completion, "capture_drops": 0}))
    return bundle


def test_p4_late_compiled_variant_from_the_final_registry_is_verified(repo: Path, tmp_path: Path) -> None:
    good = {f"{PASS_RES}|FOO": _variant(["FOO"], PASS_COMPILED_FOO)}
    late_bad = dict(good, **{f"{PASS_RES}|BAR": _variant(["BAR"], "#version 450\n#define BAR\nnot the source")})
    bundle = _final_manifest_bundle(tmp_path / "Desktop", "FL4359_late", first_frame=good, final=late_bad)
    log = f"FL4359_SCREEN_RECORDING named key=F9 bundle={bundle} mov={bundle}/recording.mov\n"
    provenance, _manifest, _ = _run_with_provenance(
        repo, receipt={"compiled_variants": good, "compiled_variant_registry_revision": 5}, log_text=log)
    compiled = provenance.record["executed_sources"]["compiled_shader_variants"]
    assert compiled["registry"] == "final" and str(bundle / "manifest.json") in compiled["sources"]
    assert compiled["mismatched"] == [f"{PASS_RES}|BAR"]
    assert "compiled_variant_not_reproducible_from_snapshot" in provenance.failures
    assert provenance.verdict == "failed"


def test_p4_superseded_digests_must_rederive_from_a_preserved_snapshot(repo: Path) -> None:
    # Current text: RenderPipelineFactory (no defines). Superseded text: the
    # ProbeWorklistOrder construction of the same snapshot file
    # (expanded.replace("#[compute]", ""), newline kept, no strip), which a
    # preserved snapshot re-derives: recorded as a mid-run change, not a failure.
    variant = _variant([], "#version 450\nfloat k() { return 2.0; }\n\nvoid main() {}")
    replaced = "\n#version 450\nfloat k() { return 2.0; }\n\nvoid main() {}\n"
    variant["superseded_sha256"] = [_sha(f"compute:{_sha(replaced)}\n")]
    provenance, _m, _ = _run_with_provenance(
        repo, receipt={"compiled_variants": {f"{PASS_RES}|": variant}, "compiled_variant_registry_revision": 2})
    compiled = provenance.record["executed_sources"]["compiled_shader_variants"]
    assert compiled["variants"][0]["reproduced"] is True, compiled
    assert compiled["changed_mid_run"] == [f"{PASS_RES}|"] and compiled["superseded_unreproducible"] == []
    assert provenance.ok
    variant["superseded_sha256"] = ["0" * 64]  # text no preserved snapshot holds
    provenance, _m, _ = _run_with_provenance(
        repo, receipt={"compiled_variants": {f"{PASS_RES}|": variant}, "compiled_variant_registry_revision": 2})
    assert "superseded_variant_not_reproducible:executed_source_changed_mid_run" in provenance.failures


def test_p4_eligible_only_with_final_registry_and_no_unrecorded_sites(repo: Path) -> None:
    receipt = {"compiled_variants": {f"{PASS_RES}|FOO": _variant(["FOO"], PASS_COMPILED_FOO)},
               "compiled_variant_registry_revision": 3}
    provenance, _m, _ = _run_with_provenance(repo, receipt=dict(receipt))
    assert provenance.verdict == "ineligible" and provenance.ineligible == ["unrecorded_compile_sites"]
    (repo / "godot_project" / "render" / "ascii" / "Stage.gd").unlink()
    _git(repo, "rm", "-q", "--cached", "godot_project/render/ascii/Stage.gd")
    _git(repo, "commit", "-q", "-m", "all sites record")
    provenance, _m, _ = _run_with_provenance(repo, receipt=dict(receipt))
    assert provenance.verdict == "eligible", (provenance.failures, provenance.ineligible)
    assert runner._apply_source_provenance_verdict(provenance, 0) == 0
    # The runtime's own report of an unrecorded site also makes the run ineligible.
    provenance, _m, _ = _run_with_provenance(repo, receipt=dict(receipt, unrecorded_compile_sites=[{"path": "res://x/X.gd", "line": 9, "reason": "vendored"}]))
    assert "godot_project/x/X.gd:9" in provenance.record["executed_sources"]["unrecorded_compile_sites"]
    assert "unrecorded_compile_sites" in provenance.ineligible
    # A graphics variant no modelled rule reproduces is unverified: ineligible, not pass.
    graphics = dict(_variant([], "x"), stages={"vertex": "a" * 64, "fragment": "b" * 64}, sha256="c" * 64)
    provenance, _m, _ = _run_with_provenance(
        repo, receipt=dict(receipt, compiled_variants=dict(receipt["compiled_variants"], **{f"{PASS_RES}|G": graphics})))
    assert "compiled_variants_unverified" in provenance.ineligible


# ---------------------------------------------------------------- P5 capture eligibility

@pytest.mark.parametrize("breakage,reason", [
    ("completion_missing", "recording_completion_missing"),
    ("unfinished", "recording_unfinished:finalizing"),
    ("dropped", "recording_dropped_frames:3"),
    ("missing_png", "recording_missing_png:1"),
    ("registry_missing", "recorder_too_old:compiled_variants_final_missing"),
    ("frame_revision_missing", "recorder_too_old:frame_variant_revision_missing"),
])
def test_p5_each_capture_defect_is_named(tmp_path: Path, breakage: str, reason: str) -> None:
    good = {f"{PASS_RES}|FOO": _variant(["FOO"], PASS_COMPILED_FOO)}
    bundle = _final_manifest_bundle(tmp_path, "FL4359_x", first_frame={}, final=None if breakage == "registry_missing"
                                    else good, completion="finalizing" if breakage == "unfinished" else "complete",
                                    frame_revision=breakage != "frame_revision_missing")
    if breakage == "completion_missing":
        (bundle / "completion.json").unlink()
    if breakage == "dropped":
        (bundle / "completion.json").write_text(json.dumps({"status": "complete", "capture_drops": 3}))
    if breakage == "missing_png":
        (bundle / "frames" / "frame_00000.png").unlink()
    reasons, _facts = runner._recording_ineligibility(bundle)
    assert reason in reasons
    clean = _final_manifest_bundle(tmp_path / "clean", "FL4359_ok", first_frame={}, final=good)
    assert runner._recording_ineligibility(clean) == ([], {"completion_status": "complete", "dropped": 0,
                                                           "missing_png": 0, "variant_revision_final": 5})


def test_p5_ineligible_capture_fails_the_run_and_keeps_every_piece_of_evidence(repo: Path, tmp_path: Path) -> None:
    good = {f"{PASS_RES}|FOO": _variant(["FOO"], PASS_COMPILED_FOO)}
    bundle = _final_manifest_bundle(tmp_path / "Desktop", "FL4359_unfinished", first_frame={}, final=good,
                                    completion="finalizing")
    log = f"FL4359_SCREEN_RECORDING named key=F9 bundle={bundle} mov={bundle}/recording.mov\n"
    provenance, manifest, _ = _run_with_provenance(
        repo, receipt={"compiled_variants": good, "compiled_variant_registry_revision": 5}, log_text=log)
    assert provenance.ok and provenance.verdict == "ineligible"
    assert "FL4359_unfinished:recording_unfinished:finalizing" in provenance.ineligible
    assert runner._apply_source_provenance_verdict(provenance, 0) == 1
    preserved = json.loads((manifest.parent / "receipt.json").read_text())["run_source_provenance"]
    assert preserved["verdict"] == "ineligible"
    row = preserved["f9_bundles"][0]
    assert row["ineligible_reasons"] == ["recording_unfinished:finalizing"]
    assert (repo / row["durable_manifest"]).is_file()
    assert (manifest.parent / "godot_client.log").is_file()


# ---------------------------------------------------------------- P6 finalizer preservation and packages

def test_p6_finalizer_preserves_even_an_incomplete_bundle_with_its_reason(tmp_path: Path) -> None:
    bundle = _image_bundle(tmp_path / "Desktop")
    (bundle / "frames" / "frame_00003.png").unlink()
    (bundle / "completion.json").write_text(json.dumps({"status": "finalizing", "finalize_reason": "stop"}))
    env = dict(__import__("os").environ, ASCIICKER_F9_PRESERVE_REPO_ROOT=str(tmp_path / "repo"))
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "recording" / "f9_finalize_bundle.py"), str(bundle)],
                          capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 1
    completion = json.loads((bundle / "completion.json").read_text())
    assert completion["status"] == "incomplete" and completion["preservation"]["status"] == "preserved"
    durable = Path(completion["preservation"]["durable_manifest"])
    assert durable.is_relative_to(tmp_path / "repo" / "artifacts" / "run_receipts" / "fl-4873")
    preserved_completion = json.loads((durable.parent / "completion.json").read_text())
    assert "missing_png_files" in preserved_completion["problems"]


def test_p6_package_restores_a_working_viewer_with_every_hash_verified(tmp_path: Path, capsys) -> None:
    bundle = _image_bundle(tmp_path / "Desktop")
    _image_session(bundle)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_f10_frames_html.py"), str(bundle)],
                   check=True, capture_output=True)
    repo = tmp_path / "repo"
    rc = f9_session.main(["--package", str(bundle), "--dest", str(tmp_path / "operator_archive"),
                          "--repo-root", str(repo)])
    record = json.loads(capsys.readouterr().out)
    assert rc == 0 and record["storage_status"] == "ARCHIVED" and record["verification"]["status"] == "verified"
    package = Path(record["location"])
    assert _sha(package.read_bytes()) == record["sha256"] and package.stat().st_size == record["bytes"]
    durable = json.loads(Path(record["durable_manifest"]).read_text())
    assert durable["metadata"]["package"]["storage_status"] == "ARCHIVED"
    assert durable["metadata"]["package"]["location"] == str(package)
    # Same bundle state: the same package; a different file at that name is refused.
    assert f9_session.package_bundle(bundle, tmp_path / "operator_archive", repo_root=repo,
                                     fl_id="FL-4873")["sha256"] == record["sha256"]
    shutil.rmtree(tmp_path / "Desktop")  # the original recording is gone
    rc = f9_session.main(["--restore", str(package), "--dest", str(tmp_path / "restored"),
                          "--expect-sha256", record["sha256"]])
    restored = Path(json.loads(capsys.readouterr().out)["root"])
    assert rc == 0
    sums = {row["path"]: row["sha256"] for row in
            json.loads((restored / f9_session.PACKAGE_SUMS).read_text())["files"]}
    frames = _viewer_frames((restored / "frames.html").read_text())
    assert len(frames) == 10
    for frame in frames:
        assert _sha((restored / frame["png"]).read_bytes()) == sums[frame["png"]]
    assert _sha((restored / "recording.mov").read_bytes()) == sums["recording.mov"]
    with pytest.raises(RuntimeError, match="not empty"):
        f9_session.restore_package(package, tmp_path / "restored")
    tampered = tmp_path / "tampered.tar"
    data = bytearray(package.read_bytes())
    at = data.find(b"\x89PNG")
    data[at + 40] ^= 0xFF
    tampered.write_bytes(bytes(data))
    with pytest.raises(RuntimeError, match="does not verify"):
        f9_session.restore_package(tampered, tmp_path / "restored2")


def test_p6_unverified_package_stays_pending(tmp_path: Path, monkeypatch) -> None:
    bundle = _image_bundle(tmp_path / "Desktop")
    monkeypatch.setattr(f9_session, "verify_package",
                        lambda path: {"status": "mismatch", "problems": ["forced"], "files": 0})
    record = f9_session.package_bundle(bundle, tmp_path / "operator_archive", repo_root=tmp_path / "repo",
                                       fl_id="FL-4873")
    assert record["storage_status"] == "PENDING"


# ---------------------------------------------------------------- P7 snapshot replay

def test_p7_materialize_without_checkout_and_verify(repo: Path, tmp_path: Path) -> None:
    # Generated files are judged by the snapshot's own .gitignore (FL-4873).
    (repo / ".gitignore").write_text(".godot/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore engine cache")
    state =launcher._runtime_source_state(repo / "artifacts" / "run_receipts" / "source_snapshots")
    snapshot = repo / state["snapshot"]
    dest = tmp_path / "materialized"
    status_before = _git(repo, "status", "--porcelain")
    result = launcher.materialize_snapshot(snapshot, dest)
    assert result["status"] == "verified" and result["address"] == state["sha256"]
    assert _git(repo, "status", "--porcelain") == status_before
    assert "worktree" not in _git(repo, "worktree", "list")[len(str(repo)):]
    assert (dest / "godot_project/render/core/shaders/inc.glsl").read_text() == INC_DIRTY
    assert (dest / "godot_project/render/core/shaders/new_pass.glsl").read_text() == "// untracked pass\n"
    assert f9_source_identity.executed_source_files(dest) == f9_source_identity.executed_source_files()
    # Engine caches are ignored; any other change is a mismatch.
    (dest / "godot_project" / ".godot").mkdir()
    (dest / "godot_project" / ".godot" / "cache.bin").write_bytes(b"x")
    assert launcher.verify_materialized_tree(snapshot, dest)["status"] == "verified"
    (dest / "godot_project/render/core/shaders/inc.glsl").write_text("edited\n")
    bad = launcher.verify_materialized_tree(snapshot, dest)
    assert bad["status"] == "mismatch" and bad["differs"] == ["godot_project/render/core/shaders/inc.glsl"]
    with pytest.raises(launcher.SourceSnapshotError, match="not_empty"):
        launcher.materialize_snapshot(snapshot, dest)
    with pytest.raises(launcher.SourceSnapshotError, match="inside_repository"):
        launcher.materialize_snapshot(snapshot, repo / "inside")


def test_p7_replay_from_snapshot_runs_the_materialized_tree(repo: Path, tmp_path: Path, monkeypatch) -> None:
    pipeline = _pipeline()
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", repo)
    monkeypatch.setattr(f9_replay_validate, "ROOT", repo)
    state = launcher._runtime_source_state(repo / "artifacts" / "run_receipts" / "source_snapshots")
    files, _ = f9_source_identity.executed_source_files()
    bundle = pipeline._recorded(tmp_path)
    doc = json.loads((bundle / "manifest.json").read_text())
    doc["identity"]["source"] = {"executed_source_sha256": f9_source_identity.inventory_sha256(files),
                                 "executed_source_files": files,
                                 "run_identity": {"git_head": state["head"], "runtime_source_sha256": state["sha256"],
                                                  "runtime_source_snapshot": state["snapshot"]}}
    (bundle / "manifest.json").write_text(json.dumps(doc))
    # The checkout moves on: a plain baseline replay is refused...
    (repo / "godot_project/render/core/shaders/inc.glsl").write_text("float k() { return 9.0; }\n")
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(pipeline._args(bundle), tmp_path / "out")
    assert err.value.code == "source_mismatch"
    # ...and runs from the preserved snapshot instead, on the materialized tree.
    mat = tmp_path / "materialized"
    args = pipeline._args(bundle, replay_from_snapshot=state["sha256"], materialize_dir=str(mat))
    plan = f9_replay.plan(args, tmp_path / "out")
    assert plan["materialized_source"]["status"] == "verified" and plan["materialized_source"]["reused"] is False
    assert args.godot_project_dir == str(mat.resolve() / "godot_project")
    assert args.colyseus_dir == str(mat.resolve() / "colyseus_server")
    assert plan["current_sha"] == doc["identity"]["source"]["executed_source_sha256"]
    # A second plan re-verifies and reuses the tree; a tampered tree is refused.
    assert f9_replay.plan(pipeline._args(bundle, replay_from_snapshot=state["sha256"], materialize_dir=str(mat)),
                          tmp_path / "out")["materialized_source"]["reused"] is True
    (mat / "godot_project/render/core/shaders/pass.glsl").write_text("tampered\n")
    with pytest.raises(f9_replay.ReplayRefused) as err:
        f9_replay.plan(pipeline._args(bundle, replay_from_snapshot=state["sha256"], materialize_dir=str(mat)),
                       tmp_path / "out")
    assert err.value.code == "materialized_tree_mismatch"
    # The headed runner treats the verified materialized tree as the executed source.
    identity = runner._materialized_source_identity(dict(plan["materialized_source"], root=str(tmp_path / "gone")))
    assert identity["valid"] is False


def test_p4_replay_receipt_keeps_the_driver_final_registry(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline()
    monkeypatch.setattr(f9_replay, "ARCHIVE_REPO_ROOT", tmp_path / "archive_repo")
    monkeypatch.setattr(f9_replay.f9_source_identity, "executed_source_sha256", lambda: (pipeline.SHA, "captured"))
    bundle = pipeline._recorded(tmp_path)
    out = pipeline._replay_out(tmp_path)
    plan = f9_replay.plan(pipeline._args(bundle), out)
    registry = {f"{PASS_RES}|FOO": _variant(["FOO"], PASS_COMPILED_FOO)}
    (out / "receipt.json").write_text(json.dumps({"schema": "fl4359.movement_receipt", "compiled_variants": registry,
                                                  "compiled_variant_registry_revision": 7}))
    f9_replay.post_run(plan, 0, out / "godot_client.log")
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["schema"] == f9_replay.RECEIPT_SCHEMA
    assert receipt["compiled_variants"] == registry and receipt["compiled_variant_registry_revision"] == 7
    assert receipt["runtime_receipt"]["schema"] == "fl4359.movement_receipt"
