"""FL-4873: snapshot materialization handles gitlinks, provisions npm
dependencies only for an identical lockfile, imports the Godot project, and
judges generated files by the snapshot's own .gitignore rules.

Synthetic repositories only; the Godot binary is a fake shell script.
"""
from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "recording"))

import launch_fl4359_manual_play as launcher  # noqa: E402

GITLINK = "godot_project/ext/upstream/godot-cpp"
GITLINK_COMMIT = "6388e26dd8a42071f65f764a3ef3d9523dda3d6e"
PACKAGE_JSON = '{"name": "server", "dependencies": {"dep": "1.0.0"}}\n'
LOCK_JSON = '{"lockfileVersion": 3, "packages": {"node_modules/dep": {"version": "1.0.0"}}}\n'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout.decode()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "godot_project" / "shaders").mkdir(parents=True)
    (repo / "colyseus_server" / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    # Root rules live OUTSIDE the snapshot pathspecs; a nested rule lives inside.
    (repo / ".gitignore").write_text(
        ".godot/\nnode_modules/\n__pycache__/\ngodot_project/**/*.glsl.import\n")
    (repo / "godot_project" / ".gitignore").write_text("*.tmp\n!keep.tmp\n")
    (repo / "godot_project" / "project.godot").write_text("[application]\n")
    (repo / "godot_project" / "shaders" / "a.glsl").write_text("void main() {}\n")
    (repo / "colyseus_server" / "package.json").write_text(PACKAGE_JSON)
    (repo / "colyseus_server" / "package-lock.json").write_text(LOCK_JSON)
    (repo / "colyseus_server" / "src" / "index.ts").write_text("export {};\n")
    _git(repo, "add", "-A")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{GITLINK_COMMIT},{GITLINK}")
    _git(repo, "commit", "-q", "-m", "fixture")
    (repo / GITLINK).mkdir(parents=True)  # an unpopulated submodule, as in the real checkout
    assert _git(repo, "status", "--porcelain") == ""
    # The checkout's installed dependencies (ignored, never snapshotted).
    (repo / "colyseus_server" / "node_modules" / "dep").mkdir(parents=True)
    (repo / "colyseus_server" / "node_modules" / "dep" / "index.js").write_text("export default 1;\n")
    monkeypatch.setattr(launcher, "ROOT", repo)
    monkeypatch.setattr(launcher, "GODOT_BIN", str(_fake_godot(tmp_path, rc=0)))
    return repo


def _fake_godot(tmp_path: Path, rc: int) -> Path:
    """Writes what a real `--import` writes: git-ignored sidecars and cache."""
    script = tmp_path / f"fake_godot_{rc}.sh"
    script.write_text(
        "#!/bin/sh\n"
        'while [ "$1" != "--path" ]; do shift; done\n'
        'project="$2"\n'
        'mkdir -p "$project/.godot/imported"\n'
        'echo cache > "$project/.godot/imported/a.bin"\n'
        'echo import > "$project/shaders/a.glsl.import"\n'
        'echo imported\n'
        f"exit {rc}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _snapshot(repo: Path) -> Path:
    state = launcher._runtime_source_state(repo / "artifacts" / "run_receipts" / "source_snapshots")
    return repo / state["snapshot"]


def test_gitlink_is_expected_as_a_directory_and_recorded(repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    listing, _ = launcher._expected_snapshot_listing(launcher._load_snapshot_manifest(snapshot), snapshot.parent)
    assert listing[GITLINK] == (launcher.GITLINK_MODE, GITLINK_COMMIT)
    dest = tmp_path / "mat"
    result = launcher.materialize_snapshot(snapshot, dest)
    assert result["status"] == "verified"
    assert result["gitlinks"] == [{"path": GITLINK, "commit": GITLINK_COMMIT, "present": True}]
    assert (dest / GITLINK).is_dir()
    (dest / GITLINK).rmdir()
    bad = launcher.verify_materialized_tree(snapshot, dest)
    assert bad["status"] == "mismatch" and bad["missing"] == [GITLINK]
    (dest / GITLINK).write_text("not a directory\n")
    assert launcher.verify_materialized_tree(snapshot, dest)["missing"] == [GITLINK]


def test_dependencies_are_symlinked_for_identical_lockfile_and_hashes_recorded(repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    dest = tmp_path / "mat"
    result = launcher.materialize_snapshot(snapshot, dest)
    link = dest / "colyseus_server" / "node_modules"
    assert link.is_symlink() and link.resolve() == (repo / "colyseus_server" / "node_modules").resolve()
    assert (link / "dep" / "index.js").is_file()
    [dependency] = result["dependencies"]
    assert dependency["package_dir"] == "colyseus_server" and dependency["equal"] is True
    assert dependency["files"] == {
        "colyseus_server/package.json": {"snapshot_sha256": _sha(PACKAGE_JSON), "checkout_sha256": _sha(PACKAGE_JSON)},
        "colyseus_server/package-lock.json": {"snapshot_sha256": _sha(LOCK_JSON), "checkout_sha256": _sha(LOCK_JSON)},
    }
    # The provisioned link is reported, never walked, never an extra file.
    assert result["extra"] == [] and result["provisioned_dependencies"][0]["path"] == "colyseus_server/node_modules"
    assert not any("node_modules" in p for p in result["generated_ignored"])
    # A lockfile change in the checkout invalidates the provisioned modules.
    (repo / "colyseus_server" / "package-lock.json").write_text(LOCK_JSON.replace("1.0.0", "2.0.0"))
    bad = launcher.verify_materialized_tree(snapshot, dest)
    assert bad["status"] == "mismatch"
    assert bad["dependency_problems"] == [{"path": "colyseus_server/node_modules",
                                           "reason": "dependency_identity_mismatch"}]
    # An unprovisioned tree cannot start the server: mismatch, named.
    (repo / "colyseus_server" / "package-lock.json").write_text(LOCK_JSON)
    link.unlink()
    bad = launcher.verify_materialized_tree(snapshot, dest)
    assert bad["dependency_problems"] == [{"path": "colyseus_server/node_modules",
                                           "reason": "dependencies_not_provisioned"}]


def test_different_checkout_lockfile_refuses_with_a_named_code_and_preserves_the_receipt(
        repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    (repo / "colyseus_server" / "package-lock.json").write_text(LOCK_JSON.replace("1.0.0", "2.0.0"))
    with pytest.raises(launcher.SourceSnapshotError, match="^dependency_identity_mismatch"):
        launcher.materialize_snapshot(snapshot, tmp_path / "mat")
    assert not (tmp_path / "mat" / "colyseus_server" / "node_modules").exists()
    receipts = list((snapshot.parent / "materializations").glob("*.json"))
    assert len(receipts) == 1 and "dependency_identity_mismatch" in json.loads(receipts[0].read_text())["error"]


def test_missing_checkout_dependencies_refuse(repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    (repo / "colyseus_server" / "node_modules" / "dep" / "index.js").unlink()
    (repo / "colyseus_server" / "node_modules" / "dep").rmdir()
    (repo / "colyseus_server" / "node_modules").rmdir()
    with pytest.raises(launcher.SourceSnapshotError, match="^dependency_source_missing"):
        launcher.materialize_snapshot(snapshot, tmp_path / "mat")


def test_import_runs_and_generated_ignored_files_are_reported_not_mismatched(repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    dest = tmp_path / "mat"
    result = launcher.materialize_snapshot(snapshot, dest)
    assert result["godot_import"]["rc"] == 0 and result["godot_import"]["status"] == "imported"
    assert result["godot_import"]["command"][1:] == ["--headless", "--path", str(dest / "godot_project"), "--import"]
    assert isinstance(result["godot_import"]["duration_seconds"], float)
    assert result["status"] == "verified"
    assert result["generated_ignored"] == ["godot_project/.godot/imported/a.bin",
                                           "godot_project/shaders/a.glsl.import"]
    assert result["generated_ignored_count"] == 2
    # The receipt is durable, content-addressed, and names itself.
    receipt = repo / result["materialize_receipt"]
    assert receipt.parent == snapshot.parent / "materializations"
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == receipt.stem
    # Nested snapshot rules apply, including negation.
    (dest / "godot_project" / "scratch.tmp").write_text("x")
    (dest / "godot_project" / "keep.tmp").write_text("x")
    (dest / "godot_project" / "shaders" / "__pycache__").mkdir()
    (dest / "godot_project" / "shaders" / "__pycache__" / "m.pyc").write_text("x")
    check = launcher.verify_materialized_tree(snapshot, dest)
    assert check["extra"] == ["godot_project/keep.tmp"] and check["status"] == "mismatch"
    assert "godot_project/scratch.tmp" in check["generated_ignored"]
    assert "godot_project/shaders/__pycache__/m.pyc" in check["generated_ignored"]
    (dest / "godot_project" / "keep.tmp").unlink()
    assert launcher.verify_materialized_tree(snapshot, dest)["status"] == "verified"
    # A non-ignored extra or a changed snapshot file still fails.
    (dest / "godot_project" / "shaders" / "new.glsl").write_text("x")
    assert launcher.verify_materialized_tree(snapshot, dest)["extra"] == ["godot_project/shaders/new.glsl"]
    (dest / "godot_project" / "shaders" / "new.glsl").unlink()
    (dest / "godot_project" / "shaders" / "a.glsl").write_text("changed\n")
    assert launcher.verify_materialized_tree(snapshot, dest)["differs"] == ["godot_project/shaders/a.glsl"]


def test_checkout_and_user_ignore_rules_do_not_decide(repo: Path, tmp_path: Path, monkeypatch) -> None:
    """Only the snapshot's rules count: a rule added to the checkout after the
    snapshot, or a user-level excludes file, cannot hide an extra file."""
    snapshot = _snapshot(repo)
    dest = tmp_path / "mat"
    launcher.materialize_snapshot(snapshot, dest)
    (repo / ".gitignore").write_text((repo / ".gitignore").read_text() + "*.hidden\n")
    user_excludes = tmp_path / "user_ignore"
    user_excludes.write_text("*.hidden\n")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.excludesFile={user_excludes}'")
    (dest / "godot_project" / "x.hidden").write_text("x")
    assert launcher.verify_materialized_tree(snapshot, dest)["extra"] == ["godot_project/x.hidden"]


def test_failed_import_refuses_and_preserves_the_receipt(repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launcher, "GODOT_BIN", str(_fake_godot(tmp_path, rc=3)))
    snapshot = _snapshot(repo)
    with pytest.raises(launcher.SourceSnapshotError, match="^materialize_import_failed"):
        launcher.materialize_snapshot(snapshot, tmp_path / "mat")
    [receipt] = (snapshot.parent / "materializations").glob("*.json")
    assert '"rc": 3' in json.loads(receipt.read_text())["error"]


def test_generated_uid_sidecar_for_a_snapshot_file_is_reported_not_mismatched(repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    dest = tmp_path / "mat"
    launcher.materialize_snapshot(snapshot, dest)
    (dest / "godot_project" / "shaders" / "a.glsl.uid").write_text("uid://generated123\n")
    check = launcher.verify_materialized_tree(snapshot, dest)
    assert check["status"] == "verified" and check["extra"] == []
    assert check["generated_uid_sidecars"] == [{"path": "godot_project/shaders/a.glsl.uid",
                                                "uid": "uid://generated123"}]


def test_snapshot_listed_uid_that_differs_still_fails(tmp_path: Path, repo: Path) -> None:
    (repo / "godot_project" / "shaders" / "a.glsl.uid").write_text("uid://tracked\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tracked uid")
    snapshot = _snapshot(repo)
    dest = tmp_path / "mat"
    assert launcher.materialize_snapshot(snapshot, dest)["generated_uid_sidecars"] == []
    (dest / "godot_project" / "shaders" / "a.glsl.uid").write_text("uid://regenerated\n")
    check = launcher.verify_materialized_tree(snapshot, dest)
    assert check["status"] == "mismatch" and check["differs"] == ["godot_project/shaders/a.glsl.uid"]
    assert check["generated_uid_sidecars"] == []


def test_uid_without_a_snapshot_sibling_still_fails(repo: Path, tmp_path: Path) -> None:
    snapshot = _snapshot(repo)
    dest = tmp_path / "mat"
    launcher.materialize_snapshot(snapshot, dest)
    (dest / "godot_project" / "shaders" / "orphan.gd.uid").write_text("uid://orphan\n")
    check = launcher.verify_materialized_tree(snapshot, dest)
    assert check["status"] == "mismatch" and check["extra"] == ["godot_project/shaders/orphan.gd.uid"]
    assert check["generated_uid_sidecars"] == []
