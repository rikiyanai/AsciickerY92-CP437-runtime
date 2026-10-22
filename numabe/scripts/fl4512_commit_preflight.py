"""Race-resistant preflight checks for the FL-4512 commit helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess]


def _git(root: str | Path, args: list[str], runner: Runner) -> subprocess.CompletedProcess:
    return runner(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def _stdout(result: subprocess.CompletedProcess, description: str) -> str:
    if result.returncode:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{description} failed: {stderr}")
    return (result.stdout or "").strip()


def stable_file_blob(
    root: str | Path,
    path: str,
    runner: Runner = subprocess.run,
) -> str:
    """Hash one worktree file only if its HEAD base stayed stable while read."""
    before = _git(root, ["rev-parse", "--verify", "-q", f"HEAD:{path}"], runner)
    before_blob = (before.stdout or "").strip() if before.returncode == 0 else ""
    blob = _stdout(
        _git(root, ["hash-object", "-w", path], runner),
        f"hash-object {path}",
    )
    after = _git(root, ["rev-parse", "--verify", "-q", f"HEAD:{path}"], runner)
    after_blob = (after.stdout or "").strip() if after.returncode == 0 else ""
    if before_blob != after_blob:
        raise RuntimeError(
            f"HEAD changed while reading --file {path}; rebuild from the current HEAD"
        )
    return blob


def snapshot_interfaces(
    root: str | Path,
    paths: list[str],
    runner: Runner = subprocess.run,
) -> dict[str, str]:
    """Capture landed sibling interface identities before commit preparation."""
    snapshot = {}
    for path in paths:
        result = _git(root, ["rev-parse", "--verify", "-q", f"HEAD:{path}"], runner)
        snapshot[path] = (result.stdout or "").strip() if result.returncode == 0 else ""
    return snapshot


def verify_interfaces(
    root: str | Path,
    snapshot: dict[str, str],
    runner: Runner = subprocess.run,
) -> None:
    """Refuse a commit when a named sibling interface changed mid-task."""
    for path, before in snapshot.items():
        result = _git(root, ["rev-parse", "--verify", "-q", f"HEAD:{path}"], runner)
        after = (result.stdout or "").strip() if result.returncode == 0 else ""
        if before != after:
            raise RuntimeError(
                f"sibling interface changed during commit preparation: {path}; rerun"
            )
