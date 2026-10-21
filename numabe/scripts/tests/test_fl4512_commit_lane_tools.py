"""Focused regressions for the temporary-index and delete-step lane helpers."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DELETE_STEP = _load("fl4895_delete_step", "scripts/fl4895_delete_step.py")
INDEX_RESYNC = _load("fl4512_index_resync", "scripts/fl4512_index_resync.py")

from scripts.fl4512_commit_preflight import (  # noqa: E402
    snapshot_interfaces,
    stable_file_blob,
    verify_interfaces,
)
from scripts.fl4512_commit_transport import push_origin  # noqa: E402


def _step(*paths: str):
    return SimpleNamespace(EDITS={path: [] for path in paths}, DELETE=[])


def test_delete_step_refuses_overlapping_worktree_sequence() -> None:
    first = _step("shared.gd")
    second = _step("shared.gd")
    with pytest.raises(SystemExit, match="overlap"):
        DELETE_STEP.select_work_steps([first, second], head_only=False, sequence=False)
    assert DELETE_STEP.select_work_steps([first, second], head_only=False, sequence=True) == [second]


def test_delete_step_head_only_keeps_worktree_untouched() -> None:
    first = _step("shared.gd")
    second = _step("other.gd")
    assert DELETE_STEP.select_work_steps([first, second], head_only=True, sequence=False) == []


def test_delete_step_rejects_reapplying_a_substitution() -> None:
    with pytest.raises(AssertionError, match="already applied"):
        DELETE_STEP.apply(
            "anchor\nnew value\n",
            [("sub", "anchor\n", "anchor\nnew value\n")],
            "shared.gd",
            reject_already_applied=True,
        )


def test_delete_step_head_blob_rebuild_allows_the_same_substitution() -> None:
    rebuilt = DELETE_STEP.apply(
        "anchor\n",
        [("sub", "anchor\n", "anchor\nnew value\n")],
        "HEAD:shared.gd",
    )
    assert rebuilt == "anchor\nnew value\n"


def test_index_resync_preserves_a_sibling_stage(monkeypatch) -> None:
    monkeypatch.setattr(INDEX_RESYNC, "_head_blob", lambda _path: "headblob")
    entries = iter(["100644 oldblob shared.gd", "100644 siblingblob shared.gd"])
    monkeypatch.setattr(INDEX_RESYNC, "index_entry", lambda _path: next(entries))
    called = []
    monkeypatch.setattr(
        INDEX_RESYNC.subprocess,
        "run",
        lambda *a, **k: called.append(a)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert INDEX_RESYNC.reset_path_if_unchanged(
        "shared.gd", "100644 oldblob shared.gd"
    ) == "foreign"
    assert called == [(["git", "reset", "-q", "--", "shared.gd"],)]


def test_index_resync_requires_the_new_head_entry(monkeypatch) -> None:
    monkeypatch.setattr(INDEX_RESYNC, "_head_blob", lambda _path: "headblob")
    entries = iter(["100644 oldblob shared.gd", "100644 headblob shared.gd"])
    monkeypatch.setattr(INDEX_RESYNC, "index_entry", lambda _path: next(entries))
    monkeypatch.setattr(
        INDEX_RESYNC.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert INDEX_RESYNC.reset_path_if_unchanged(
        "shared.gd", "100644 oldblob shared.gd"
    ) == "reset"


def test_index_resync_set_entry_verifies_the_written_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        INDEX_RESYNC.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        INDEX_RESYNC,
        "index_entry",
        lambda _path: "100644 headblob shared.gd",
    )
    INDEX_RESYNC.set_entry("100644", "headblob", "shared.gd")


def test_commit_owned_invokes_both_guards_and_post_commit_resync() -> None:
    source = Path("scripts/fl4512_commit_owned.py").read_text(encoding="utf-8")
    assert 'for command in ("pre-commit", "commit-msg")' in source
    assert "_run_commit_guards(env, msg)" in source
    assert '"temporary-index commit has no changes; refusing empty commit"' in source
    assert "resync_committed_paths(before_entries)" in source
    assert 'ap.add_argument("--push"' in source
    assert 'ap.add_argument("--interface"' in source
    assert "verify_interfaces(R, interface_snapshot)" in source
    assert 'subprocess.run(["git", "push"' not in source
    assert "push_origin(R, a.push)" in source


def test_push_is_explicit_and_uses_only_the_isolated_temp_repo(tmp_path) -> None:
    calls = []

    def fake_runner(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    repo = tmp_path / "repo"
    repo.mkdir()
    assert push_origin(repo, False, fake_runner) is None
    assert calls == []
    assert push_origin(repo, True, fake_runner) == 0
    assert calls[0][0][0] == ["git", "push", "-q", "origin", "main"]
    assert Path(calls[0][1]["cwd"]).resolve() == repo.resolve()
    assert repo.resolve() != Path("/Users/r/Projects/asciicker-Y9-2").resolve()


def test_file_base_preflight_refuses_a_head_move() -> None:
    outputs = iter(["base", "blob", "new-base"])

    def fake_runner(*args, **kwargs):
        command = args[0]
        if command[1] == "hash-object":
            return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    with pytest.raises(RuntimeError, match="HEAD changed while reading"):
        stable_file_blob("/tmp/isolated-fl4512-repo", "shared.gd", fake_runner)


def test_interface_preflight_detects_a_landed_sibling_change() -> None:
    outputs = iter(["before", "after"])

    def fake_runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    snapshot = snapshot_interfaces("/tmp/isolated-fl4512-repo", ["abi.gd"], fake_runner)
    with pytest.raises(RuntimeError, match="sibling interface changed"):
        verify_interfaces("/tmp/isolated-fl4512-repo", snapshot, fake_runner)


def test_helper_noop_probe_uses_an_isolated_repo_and_cannot_push(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    source = repo / "sample.txt"
    source.write_text("sample\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "sample.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD:sample.txt"], text=True
    ).strip()
    env = os.environ.copy()
    env["FL4512_REPO_ROOT"] = str(repo)
    helper = Path("scripts/fl4512_commit_owned.py").resolve()
    message = "fix(FL-4512): isolated probe\n\nGodot-Feature-Impact: none\n"
    result = subprocess.run(
        [sys.executable, str(helper), "--blob", f"sample.txt={base}@{base}"],
        cwd=repo,
        env=env,
        input=message,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "refusing empty commit" in result.stderr
    assert subprocess.check_output(["git", "-C", str(repo), "remote"], text=True) == ""
