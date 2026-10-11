"""Dirty-tree classification — extracted from watchdog_run_canonical.py.

Phase 2: Functions moved without behaviour change from:
  - _source_dirty_paths_set() (line 2604)
  - _scope_labels_for_path() (line 3237)
  - git_status_porcelain() → wraps tmp_clone_recovery._git_status_porcelain
  - tracked_status_paths(), untracked_status_paths() → inline implementations

Phase 3: tmp_clone.py will deduplicate shared helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple


# -- Status-line parsing -------------------------------------------------------

def tracked_status_paths(status_lines: list[str] | None = None) -> list[str]:
    """Return paths from ``git status --porcelain`` whose status is a tracked change.

    The first two chars encode the index + worktree status.  Typical codes:
      M  = modified in index (no worktree change)
       M = modified in worktree (no index change)
      MM = modified in both
      A  = added
      D  = deleted
      R  = renamed
      ?? = untracked (excluded)

    Empty/untracked lines (``??``) and staged deletions (``D `` / `` D``)
    are tracked changes but classified separately — they still count as
    "dirty paths" for the scope label classifier.
    """
    lines = status_lines if status_lines is not None else _git_status_porcelain()
    paths: list[str] = []
    for line in lines:
        if not line or len(line) < 4:
            continue
        # Untracked files have ``??`` status — excluded here, handled by
        # untracked_status_paths().
        if line.startswith("??"):
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def untracked_status_paths(status_lines: list[str] | None = None) -> list[str]:
    """Return paths from ``git status --porcelain`` that are untracked."""
    lines = status_lines if status_lines is not None else _git_status_porcelain()
    return [line[3:] for line in lines if line.startswith("??") and len(line) >= 4]


# -- Git helpers (Phase 3 will deduplicate with tmp_clone_recovery.py) ----------

def _git_status_porcelain(cwd: Path | None = None) -> list[str]:
    """Return list of status lines from ``git status --porcelain``.

    Fail-closed: raises RuntimeError on timeout or subprocess failure
    (FL-3863 C2 / FL-3862 H2).  A corrupted git index or missing git binary
    must not silently report "clean worktree" and allow blind deploy.
    """
    import subprocess

    try:
        rc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "git status --porcelain timed out after 30s"
        )
    except OSError as exc:
        raise RuntimeError(
            f"git status --porcelain failed: {exc}"
        )
    if rc.returncode != 0:
        raise RuntimeError(
            f"git status --porcelain exited {rc.returncode}: "
            f"{(rc.stderr or rc.stdout).strip()}"
        )
    return [line for line in rc.stdout.splitlines() if line]


# -- Scope classification ------------------------------------------------------

ScopeLabels = Tuple[str, ...]


def scope_labels_for_path(path: str) -> ScopeLabels:
    """Classify a dirty path into the candidate diagnostic corpora.

    Extracted from ``_scope_labels_for_path()`` (line 3237) without behaviour change.
    """
    normalized = path.strip()
    if not normalized:
        return ()

    docs_prefixes = (
        "docs/",
        "docs/agent/",
    )
    if normalized.startswith(docs_prefixes):
        return ("docs",)

    gameplay_prefixes = (
        "engine/",
        "server/",
        "web/",
        "assets/actor_visual_profiles/",
        "scripts/pipeline/",
    )
    if normalized.startswith(gameplay_prefixes):
        return ("gameplay",)

    watchdog_prefixes = (
        "scripts/watchdog_",
        "scripts/watchdog/",
        "scripts/watchdog_source.py",
        "scripts/analyze_runs.py",
        "scripts/analyze_failure_log.py",
        "scripts/multiplayer_visual_watchdog.js",
        "scripts/simplified_watchdog_vps_launcher.py",
    )
    if normalized.startswith(watchdog_prefixes):
        return ("watchdog",)

    launcher_prefixes = (
        "scripts/launcher.py",
        "scripts/launcher_lib/",
        "scripts/launcher_ui/",
    )
    if normalized.startswith(launcher_prefixes):
        return ("launcher",)

    return ()


# -- Dirty paths set -----------------------------------------------------------

def source_dirty_paths_set(source_repo: Path) -> set[str]:
    """Return the set of dirty (tracked + untracked) paths for source_repo.

    Extracted from ``_source_dirty_paths_set()`` (line 2604) without behaviour change.
    """
    status_lines = _git_status_porcelain(cwd=source_repo)
    return set(tracked_status_paths(status_lines)) | set(untracked_status_paths(status_lines))
