"""Shared tmp-clone helpers — deduplicated from watchdog_run_canonical.py and tmp_clone_recovery.py.

Phase 3: Extract helpers that exist in BOTH files.  After extraction:
  - wrapper delegates _runs_root_for_repo and _copy_path_into_tmp_clone
    (via lazy imports).  _rewrite_repo_root_strings and _source_dirty_paths_set
    remain local in the wrapper — migrate in Phase 5.
  - tmp_clone_recovery.py imports from here for _runs_root_for_repo,
    _rewrite_repo_root_strings, _copy_path_into_tmp_clone, _source_dirty_paths_set

The wrapper's ``git_output()`` and ``git_status_porcelain()`` keep their
CanonicalRunError wrapping and are NOT extracted — they are the authoritative
entry points for the wrapper's own use.

git_output_raw / git_status_porcelain_raw are Phase 5 scaffolding.
No production caller uses them yet — both tmp_clone_recovery.py and the wrapper
keep their own local _git_output / _git_status_porcelain with different timeout
defaults and error semantics (return [] vs raise RuntimeError).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Git subprocess helpers (minimal — callers add their own error wrapping)
# ---------------------------------------------------------------------------


def git_output_raw(*args: str, cwd: Path, timeout: int = 10) -> str:
    """Run a git command, return stripped stdout.  Raises RuntimeError on failure.

    Callers (wrapper, tmp_clone_recovery) are expected to catch RuntimeError
    and re-raise as CanonicalRunError or TypedRecoveryResult as appropriate.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git {' '.join(args)} timed out after {timeout}s"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def git_status_porcelain_raw(cwd: Path, timeout: int = 10) -> list[str]:
    """Return list of status lines from ``git status --porcelain``.

    Callers add their own error wrapping.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git status --porcelain timed out after {timeout}s"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"git status --porcelain failed: {(result.stderr or result.stdout).strip()}"
        )
    return [line for line in result.stdout.splitlines() if line]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def runs_root_for_repo(repo_root: Path) -> Path:
    """Return the watchdog runs root for a repo.

    Duplicated in both files — canonical version lives here.
    """
    return repo_root / "artifacts" / "maintainer" / "watchdog_runs"


def rewrite_repo_root_strings(value: Any, old_root: Path, new_root: Path) -> Any:
    """Recursively rewrite repo root strings from *old_root* to *new_root*.

    Two implementations existed (wrapper vs tmp_clone_recovery).  This is
    the union: macOS ``/private/tmp`` normalisation from the wrapper +
    recursive dict/list/str rewriting from tmp_clone_recovery.
    """
    old_real = str(old_root.resolve())
    new_real = str(new_root.resolve())
    exact_old_variants = tuple(dict.fromkeys((old_real, str(old_root))))
    old_prefix_variants = tuple(
        dict.fromkeys(
            (
                old_real + os.sep,
                old_real + "/",
                str(old_root) + os.sep,
                str(old_root) + "/",
            )
        )
    )

    def _rewrite(v: Any) -> Any:
        if isinstance(v, dict):
            return {key: _rewrite(subvalue) for key, subvalue in v.items()}
        if isinstance(v, list):
            return [_rewrite(item) for item in v]
        if isinstance(v, str):
            for ov in exact_old_variants:
                if v == ov:
                    return new_real
            for ov in old_prefix_variants:
                if v.startswith(ov):
                    return new_real + os.sep + v[len(ov):]
        return v

    return _rewrite(value)


def copy_path_into_tmp_clone(*, source_path: Path, clone_path: Path) -> None:
    """Copy a source path into a tmp-clone, creating parent directories.

    Extracted from ``_copy_path_into_tmp_clone`` (both files had identical logic).
    """
    if source_path.is_dir():
        shutil.copytree(source_path, clone_path, dirs_exist_ok=True)
        return
    clone_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, clone_path)


# -- source_dirty_paths_set is now in dirty_tree.py (Phase 2).  Keep an alias
#    here for backward compat with tmp_clone_recovery.py imports during
#    the transition.

# Deliberate late import to prevent circular dependency:
# dirty_tree.py does NOT import from tmp_clone.py, but Phase 5 deduplication
# of _git_status_porcelain into tmp_clone will create a reverse edge.
# Keeping this import at module bottom ensures it survives that refactor.
from watchdog.source.dirty_tree import source_dirty_paths_set  # noqa: E402
