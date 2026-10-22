#!/usr/bin/env python3
"""FL-4888: a manual-play run names a source tree that still exists.

The launcher is the one run-identity owner. These tests drive the launcher's
own functions against a real temporary git repository (binary diff, untracked
bytes, rebuild), drive main() for the fail-closed and env-door paths, and
guard the deleted Godot-side identity producers statically (no Godot launch).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "launch_fl4359_manual_play.py"
GODOT = ROOT / "godot_project"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("fl4888_manual_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "godot_project" / "render").mkdir(parents=True)
    (repo / "scripts" / "recording").mkdir(parents=True)
    (repo / "docs").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "godot_project" / "render" / "law.glsl").write_text("float law() { return 1.0; }\n")
    (repo / "godot_project" / "glyphs.png").write_bytes(bytes(range(256)) * 4)
    (repo / "docs" / "note.md").write_text("docs\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    # Dirty runtime: text edit, binary edit, untracked runtime files.
    (repo / "godot_project" / "render" / "law.glsl").write_text("float law() { return 2.0; }\n")
    (repo / "godot_project" / "glyphs.png").write_bytes(bytes(reversed(range(256))) * 4 + b"\x00\xff")
    (repo / "godot_project" / "render" / "new_pass.glsl").write_text("// untracked pass\n")
    (repo / "scripts" / "recording" / "helper.py").write_bytes(b"\x00binary\xfe untracked\n")
    # Dirt outside the runtime pathspecs must not enter the identity.
    (repo / "docs" / "note.md").write_text("docs edited\n")
    (repo / "docs" / "loose.md").write_text("loose\n")
    return repo


@pytest.fixture()
def launcher_in_fixture(tmp_path: Path, monkeypatch):
    launcher = _load_launcher()
    repo = _fixture_repo(tmp_path)
    monkeypatch.setattr(launcher, "ROOT", repo)
    return launcher, repo, repo / "artifacts" / "run_receipts" / "source_snapshots"


def test_snapshot_preserves_binary_diff_and_untracked_bytes(launcher_in_fixture) -> None:
    launcher, repo, snapshot_root = launcher_in_fixture
    state = launcher._runtime_source_state(snapshot_root)

    assert state["dirty"] is True
    assert state["head"] == _git(repo, "rev-parse", "HEAD").decode().strip()
    assert state["untracked_paths"] == [
        "godot_project/render/new_pass.glsl",
        "scripts/recording/helper.py",
    ]
    snapshot_dir = snapshot_root / state["sha256"]
    assert state["snapshot"] == f"artifacts/run_receipts/source_snapshots/{state['sha256']}/snapshot.json"
    manifest = json.loads((snapshot_dir / "snapshot.json").read_text())
    assert manifest["runtime_source_sha256"] == state["sha256"]
    assert manifest["head"] == state["head"]
    diff = (snapshot_dir / "tracked.diff").read_bytes()
    assert b"GIT binary patch" in diff
    assert b"docs/note.md" not in diff
    assert (snapshot_dir / "untracked" / "scripts/recording/helper.py").read_bytes() == (
        b"\x00binary\xfe untracked\n"
    )
    assert not (snapshot_dir / "untracked" / "docs").exists()


def test_oversized_tracked_diff_uses_verified_transport_shards(
    launcher_in_fixture, monkeypatch
) -> None:
    launcher, _repo, snapshot_root = launcher_in_fixture
    monkeypatch.setattr(launcher, "SOURCE_SNAPSHOT_SHARD_BYTES", 64)
    state = launcher._runtime_source_state(snapshot_root)
    snapshot_dir = snapshot_root / state["sha256"]
    manifest = json.loads((snapshot_dir / "snapshot.json").read_text())
    tracked = manifest["tracked_diff"]

    assert "file" not in tracked
    assert len(tracked["parts"]) > 1
    assert all(row["bytes"] <= 64 for row in tracked["parts"])
    assert not (snapshot_dir / "tracked.diff").exists()
    diff = launcher._tracked_diff_bytes(manifest, snapshot_dir)
    assert b"GIT binary patch" in diff
    assert launcher.verify_snapshot_address(snapshot_dir / "snapshot.json") == state["sha256"]


def test_preserved_snapshot_rebuilds_the_executed_tree(launcher_in_fixture, tmp_path: Path) -> None:
    launcher, repo, snapshot_root = launcher_in_fixture
    state = launcher._runtime_source_state(snapshot_root)
    snapshot_dir = snapshot_root / state["sha256"]
    manifest = json.loads((snapshot_dir / "snapshot.json").read_text())

    rebuilt = tmp_path / "rebuilt"
    _git(tmp_path, "clone", "-q", str(repo), str(rebuilt))
    _git(rebuilt, "checkout", "-q", manifest["head"])
    _git(rebuilt, "apply", "--binary", str(snapshot_dir / "tracked.diff"))
    for row in manifest["untracked"]:
        target = rebuilt / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot_dir / "untracked" / row["path"], target)

    for relative in (
        "godot_project/render/law.glsl",
        "godot_project/glyphs.png",
        "godot_project/render/new_pass.glsl",
        "scripts/recording/helper.py",
    ):
        assert (rebuilt / relative).read_bytes() == (repo / relative).read_bytes(), relative
    # The rebuilt tree hashes to the identity the run carried.
    monkey_launcher = _load_launcher()
    monkey_launcher.ROOT = rebuilt
    assert monkey_launcher._runtime_source_state(tmp_path / "second")["sha256"] == state["sha256"]


def test_same_source_is_idempotent_and_different_bytes_refuse(launcher_in_fixture) -> None:
    launcher, _repo, snapshot_root = launcher_in_fixture
    first = launcher._runtime_source_state(snapshot_root)
    second = launcher._runtime_source_state(snapshot_root)
    assert first == second

    tampered = snapshot_root / first["sha256"] / "tracked.diff"
    tampered.write_bytes(tampered.read_bytes() + b"\n# tampered\n")
    with pytest.raises(launcher.SourceSnapshotError, match="refusing_to_overwrite"):
        launcher._runtime_source_state(snapshot_root)


def test_changed_source_gets_a_new_content_address(launcher_in_fixture) -> None:
    launcher, repo, snapshot_root = launcher_in_fixture
    first = launcher._runtime_source_state(snapshot_root)
    (repo / "godot_project" / "render" / "new_pass.glsl").write_text("// edited pass\n")
    second = launcher._runtime_source_state(snapshot_root)
    assert first["sha256"] != second["sha256"]
    assert (snapshot_root / first["sha256"] / "snapshot.json").is_file()
    assert (snapshot_root / second["sha256"] / "snapshot.json").is_file()


def test_git_failure_is_a_snapshot_error(tmp_path: Path, monkeypatch) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)  # not a git repository
    with pytest.raises(launcher.SourceSnapshotError, match="git_runtime_source_query_failed"):
        launcher._runtime_source_state(tmp_path / "snapshots")


def _main_fixture(tmp_path: Path, monkeypatch, launcher):
    events: list[tuple[str, object]] = []

    class FakeServer:
        pid = 9100

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        events.append(("popen", list(command), dict(kwargs.get("env") or {})))
        return FakeServer()

    sys.path.insert(0, str(ROOT / "scripts" / "recording"))
    import f9_source_identity

    monkeypatch.setattr(
        f9_source_identity,
        "launch_source_identity",
        lambda captured_by: {"git_head": "fixture-head", "captured_by": captured_by},
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "_port_open", lambda _port: False)
    monkeypatch.setattr(launcher, "_terminate", lambda _proc: None)
    monkeypatch.setattr(launcher, "DURABLE_RECEIPT_ROOT", tmp_path / "durable")
    monkeypatch.setattr(launcher.sys, "argv", [
        str(LAUNCHER), "--out", str(tmp_path / "run"), "--windowed",
        "--extra-user-arg=--presentation-mode=FC",
    ])
    return events


def test_main_refuses_to_launch_when_the_source_cannot_be_preserved(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    events = _main_fixture(tmp_path, monkeypatch, launcher)

    def fail(_root):
        raise launcher.SourceSnapshotError("refusing_to_overwrite_preserved_evidence: fixture")

    monkeypatch.setattr(launcher, "_runtime_source_state", fail)
    assert launcher.main() == 7
    assert events == []  # neither Colyseus nor Godot started


def test_main_passes_run_identity_through_the_launch_env_and_preserves_the_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _load_launcher()
    events = _main_fixture(tmp_path, monkeypatch, launcher)
    sha = "ab" * 32
    monkeypatch.setattr(
        launcher,
        "_runtime_source_state",
        lambda root: {
            "available": True,
            "head": "0123456789abcdef",
            "dirty": True,
            "sha256": sha,
            "untracked_paths": [],
            "snapshot": f"artifacts/run_receipts/source_snapshots/{sha}/snapshot.json",
        },
    )
    # Stop right after the server env is fixed: Colyseus never reports LISTENING.
    monkeypatch.setattr(launcher, "_wait_for", lambda _predicate, _timeout: False)

    assert launcher.main() == 3
    env = events[0][2]
    launch = json.loads(env["ASCIICKER_LAUNCH_SOURCE_IDENTITY"])
    identity = launch["run_identity"]
    assert identity["label"] == f"012345678-dirty-{sha[:12]}"
    assert identity["runtime_source_sha256"] == sha
    assert identity["runtime_source_snapshot"].endswith(f"{sha}/snapshot.json")
    receipt = json.loads((tmp_path / "run" / "manual_route_receipt.json").read_text())
    assert receipt["run_identity"] == identity
    assert receipt["source_state_stable"] is True
    preserved = list((tmp_path / "durable" / "fl-4359").glob("*/manual_route_receipt.json"))
    assert len(preserved) == 1
    assert json.loads(preserved[0].read_text())["run_identity"] == identity


def test_godot_identity_readers_run_no_git() -> None:
    compositor = (GODOT / "render" / "api" / "ASCIICompositorEffect.gd").read_text()
    world = (GODOT / "scripts" / "World.gd").read_text()
    assert 'OS.execute("git"' not in compositor
    assert "_repo_git_branch_or_tag" not in compositor
    assert "_repo_git_status_summary" not in compositor
    assert 'OS.execute("git"' not in world
    assert "func _repo_git_head" not in world and "func _repo_is_dirty" not in world
    assert "resolve_git_identity(" not in world
    assert 'launch.get("run_identity"' in world
    assert 'state["run_identity"] = run_identity' in world
    assert 'state["compiled_shader_variants"] = RenderShaderSourceScript.compiled_variant_hashes()' in world


def test_compiled_variant_hash_is_recorded_at_pipeline_creation_only() -> None:
    source = (GODOT / "render" / "core" / "shaders" / "RenderShaderSource.gd").read_text()
    factory = (GODOT / "render" / "core" / "device" / "RenderPipelineFactory.gd").read_text()
    assert "static func record_compiled_variant(" in source
    assert "static func compiled_variant_hashes()" in source
    # The reader returns the published registry; hashing lives only in record.
    reader = source.split("static func compiled_variant_hashes()", 1)[1].split("static func", 1)[0]
    assert "sha256" not in reader and "duplicate" not in reader
    compile_fn = factory.split("static func compile_compute(", 1)[1]
    record = compile_fn.index('record_compiled_variant(path, defines, {"compute": source})')
    assert compile_fn.index("shader_create_from_bytecode") < record
    assert compile_fn.index("shader_create_from_spirv") < record
