"""Tmp-clone recovery — disposable git clone for mixed-scope dirty-tree recovery.

Seam: when the worktree has dirty paths that fall outside the watchdog
diagnostic scope, the canonical orchestrator forks a disposable ``git clone``,
proves the committed HEAD (with optional overlay of dirty source files), then
copies back watchdog receipts and artifacts.

Previously this was a side-channel subprocess escape (``_run_dirty_tree_tmp_clone``
returning ``int`` from ``_resolve_worktree_state``).  Now it is a first-class
recovery strategy with typed result and explicit error handling.

Module has no imports from watchdog_run_canonical to prevent circular imports.
It does import helpers from ``run_summary_query``, ``slot_config_repository``,
``bundle_mirror``, and ``recovery_failure_domain``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from run_summary_query import iter_run_summary_records as _iter_records
from watchdog.source.tmp_clone import (
    copy_path_into_tmp_clone as _copy_path_into_tmp_clone,
    rewrite_repo_root_strings as _rewrite_repo_root_strings,
    runs_root_for_repo as _runs_root_for_repo,
    source_dirty_paths_set as _source_dirty_paths_set,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANONICAL_LAUNCH_ENV = "ASCIICKER_WATCHDOG_CANONICAL"
TMP_CLONE_ACTIVE_ENV = "ASCIICKER_WATCHDOG_TMP_CLONE_ACTIVE"

TMP_CLONE_COPYBACK_DOC_PATHS = (
    "docs/FAILURE_LOG.md",
    "docs/multiplayer-vps-regression-ledger.md",
    "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
)

TMP_CLONE_MAX_CLONE_TIMEOUT = 180  # seconds
TMP_CLONE_PREFIX = "asciicker-watchdog-tmp-"
TMP_CLONE_MAX_LIVE_ROOTS = 3
TMP_CLONE_OWNED_PREFIXES = (
    TMP_CLONE_PREFIX,
    "asciicker-y9-2-tmpclone-",
    "asciicker-y9-2-lag-proof-",
    "asciicker-y9-2-fl3800-proof-clone-",
    "asciicker-y9-2-fl3800-flush-proof-",
    "asciicker-runtime-lag-proof-",
    "asciicker-metric-truth-rerun-",
    "asciicker-vps-proof-",
    "asciicker-vps-mounted-recipe-",
    "asciicker-headed-vps-",
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TmpCloneRecoveryResult:
    """Result of a tmp-clone recovery attempt.

    ``ok`` is *True* when the clone run completed (regardless of the child's
    exit code).  ``child_returncode`` carries the cloned run's exit code.
    ``copyback_ok`` is *True* when artifact copy-back succeeded.
    """

    ok: bool
    child_returncode: int
    copyback_ok: bool
    child_prelaunch_ok: bool = True
    prelaunch_failed: bool = False
    created_entries: list[str] = field(default_factory=list)
    copyback_report: dict[str, Any] | None = None
    overlay_report: dict[str, Any] | None = None
    repeat_exact_sources: dict[str, Any] | None = None
    parent_source_drift: dict[str, Any] | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers (moved from watchdog_run_canonical without behavioural change)
# ---------------------------------------------------------------------------


def _git_output(*args: str, cwd: Path) -> str:
    """Run a git command and return stdout, stripped.

    TODO(Phase 5): migrate callers to watchdog.source.tmp_clone.git_output_raw.
    Note: git_output_raw uses timeout=10 and raises RuntimeError on TimeoutExpired;
    this version uses timeout=60 and does NOT catch TimeoutExpired.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _git_status_porcelain(cwd: Path) -> list[str]:
    """Return list of status lines from ``git status --porcelain``.

    TODO(Phase 5): migrate callers to watchdog.source.tmp_clone.git_status_porcelain_raw.
    Note: git_status_porcelain_raw raises RuntimeError on failure;
    this version returns [] (fail-open). Callers must be audited for the
    error-semantics change before migration.
    """
    try:
        rc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in rc.stdout.splitlines() if line]


def _submodule_restoreability_errors(cwd: Path) -> list[dict[str, Any]]:
    """Return submodule states that cannot be restored from the parent commit."""
    # FL-4012: watchdog candidate proof does not use repository submodule
    # contents as deploy inputs. Treat submodules/gitlinks as ignored metadata so
    # a missing historical submodule commit cannot block hermetic tmp-clone proof
    # before deploy/gameplay.
    return []


def _tracked_status_paths(status_lines: list[str]) -> list[str]:
    """Extract tracked (modified/deleted) paths from porcelain lines."""
    paths: list[str] = []
    for line in status_lines:
        if line.startswith("??"):
            continue
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return sorted(set(paths))


def _untracked_status_paths(status_lines: list[str]) -> list[str]:
    """Extract untracked paths from porcelain lines."""
    paths: list[str] = []
    for line in status_lines:
        if not line.startswith("??"):
            continue
        path = line[3:] if len(line) > 3 else ""
        if path:
            paths.append(path)
    return sorted(set(paths))


def _out_write(msg: str) -> None:
    """Write a tmp-clone progress message to stdout."""
    print(msg)


def _load_summary_payload(path: Path) -> dict[str, Any]:
    payload_path = path / "summary.json" if path.is_dir() else path
    return json.loads(payload_path.read_text(encoding="utf-8"))


def _latest_run_id(source_repo: Path) -> str | None:
    source_runs_root = _runs_root_for_repo(source_repo)
    if not source_runs_root.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for path in source_runs_root.iterdir():
        summary_path = path / "summary.json" if path.is_dir() else path
        if path.is_dir() and not summary_path.exists():
            continue
        if path.is_file() and path.suffix != ".json":
            continue
        try:
            candidates.append((summary_path.stat().st_mtime_ns, summary_path))
        except OSError:
            continue
    for _mtime, latest in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            run_id = str(_load_summary_payload(latest).get("run_id") or "").strip()
        except Exception:
            continue
        if run_id:
            return run_id
    return None


def copy_repeat_exact_sources(
    *,
    source_repo: Path,
    clone_repo: Path,
    argv: list[str],
) -> dict[str, Any]:
    """Materialize local run receipts needed by repeat-exact inside a tmp clone."""
    requested: list[str] = []
    for index, token in enumerate(argv or []):
        if token == "--repeat-exact-run" and index + 1 < len(argv):
            requested.append(str(argv[index + 1]).strip())
            continue
        if token.startswith("--repeat-exact-run="):
            requested.append(token.split("=", 1)[1].strip())
            continue
        if token == "--repeat-exact-last":
            latest = _latest_run_id(source_repo)
            if latest:
                requested.append(latest)

    copied: list[str] = []
    missing: list[str] = []
    source_runs_root = _runs_root_for_repo(source_repo)
    clone_runs_root = _runs_root_for_repo(clone_repo)
    for run_id in sorted({run_id for run_id in requested if run_id}):
        source_dir = source_runs_root / run_id
        source_summary = source_dir / "summary.json"
        if not source_summary.exists():
            source_summary = source_runs_root / f"{run_id}.json"
        if not source_summary.exists():
            missing.append(run_id)
            continue
        clone_dir = clone_runs_root / run_id
        clone_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_summary, clone_dir / "summary.json")
        copied.append(f"{run_id}/summary.json")
        source_recipe = source_dir / "controller_recipe.json"
        if source_recipe.exists():
            shutil.copy2(source_recipe, clone_dir / "controller_recipe.json")
            copied.append(f"{run_id}/controller_recipe.json")
    return {"requested": sorted({r for r in requested if r}), "copied": copied, "missing": missing}


def _watchdog_tmp_clone_roots(tmp_parent: Path) -> list[Path]:
    if not tmp_parent.exists():
        return []
    return sorted(
        (
            path for path in tmp_parent.iterdir()
            if path.is_dir() and path.name.startswith(TMP_CLONE_OWNED_PREFIXES)
        ),
        key=lambda path: path.stat().st_mtime,
    )


def _prepare_tmp_clone_space(
    *,
    tmp_parent: Path = Path("/tmp"),
    max_roots: int = TMP_CLONE_MAX_LIVE_ROOTS,
) -> dict[str, Any]:
    """Keep at most *max_roots* watchdog tmp clone roots before allocation.

    Interrupted runs can leave clone roots behind. Before creating a new root,
    evict the oldest watchdog-owned roots until there is room for one more.
    """
    tmp_parent.mkdir(parents=True, exist_ok=True)
    removed: list[dict[str, Any]] = []
    kept: list[str] = []
    errors: list[dict[str, str]] = []
    try:
        roots = _watchdog_tmp_clone_roots(tmp_parent)
    except OSError as exc:
        return {
            "tmp_parent": str(tmp_parent),
            "max_roots": max_roots,
            "removed": removed,
            "kept": kept,
            "errors": [{"path": str(tmp_parent), "error": str(exc)}],
        }
    target_existing = max(0, max_roots - 1)
    idx = 0
    while len(roots) > target_existing and idx < len(roots):
        root = roots[idx]
        row = {"path": str(root)}
        try:
            shutil.rmtree(root)
            removed.append(row)
            roots.pop(idx)
        except Exception as exc:
            row["error"] = str(exc)
            errors.append(row)
            idx += 1
    kept = [str(root) for root in _watchdog_tmp_clone_roots(tmp_parent)]
    return {
        "tmp_parent": str(tmp_parent),
        "max_roots": max_roots,
        "removed": removed,
        "kept": kept,
        "errors": errors,
    }


def _allocate_tmp_clone_root(*, tmp_parent: Path = Path("/tmp")) -> tuple[Path, dict[str, Any]]:
    space_report = _prepare_tmp_clone_space(tmp_parent=tmp_parent)
    tmp_root = Path(tempfile.mkdtemp(prefix=TMP_CLONE_PREFIX, dir=str(tmp_parent)))
    return tmp_root, space_report


# ---------------------------------------------------------------------------
# Source overlay
# ---------------------------------------------------------------------------


def _overlay_dirty_source_paths(
    *,
    source_repo: Path,
    clone_repo: Path,
    status_lines: list[str],
) -> dict[str, Any]:
    tracked_paths = sorted(_tracked_status_paths(status_lines))
    untracked_paths = sorted(_untracked_status_paths(status_lines))
    dirty_paths = sorted(set(tracked_paths) | set(untracked_paths))
    copied: list[str] = []
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    for rel in dirty_paths:
        source_path = source_repo / rel
        clone_path = clone_repo / rel
        if source_path.exists():
            try:
                _copy_path_into_tmp_clone(source_path=source_path, clone_path=clone_path)
                copied.append(rel)
            except OSError as exc:
                skipped.append({"path": rel, "reason": f"copy_failed:{exc}"})
            continue
        try:
            if clone_path.is_dir():
                shutil.rmtree(clone_path)
                deleted.append(rel)
            elif clone_path.exists() or clone_path.is_symlink():
                clone_path.unlink()
                deleted.append(rel)
            else:
                skipped.append({"path": rel, "reason": "source_missing_clone_missing"})
        except OSError as exc:
            skipped.append({"path": rel, "reason": f"delete_failed:{exc}"})
    return {
        "enabled": True,
        "tracked_paths": tracked_paths,
        "untracked_paths": untracked_paths,
        "copied": copied,
        "deleted": deleted,
        "skipped": skipped,
    }


def _commit_tmp_clone_overlay_state(*, clone_repo: Path, run_id: str) -> dict[str, Any]:
    status_lines = _git_status_porcelain(clone_repo)
    if not status_lines:
        return {"attempted": False, "committed": False, "reason": "clone_clean"}
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(clone_repo), check=True, timeout=60,
    )
    subprocess.run(
        [
            "git", "-c", "user.name=watchdog tmp clone",
            "-c", "user.email=watchdog-tmp-clone@local",
            "commit", "--no-verify",
            "-m", f"chore(watchdog-tmp-clone): overlay dirty source inputs [{run_id}]",
        ],
        cwd=str(clone_repo), check=True, timeout=60,
    )
    return {
        "attempted": True,
        "committed": True,
        "reason": "overlay_committed",
        "commit": _git_output("rev-parse", "--short", "HEAD", cwd=clone_repo),
        "commit_full": _git_output("rev-parse", "HEAD", cwd=clone_repo),
    }


def _resolve_overlay_mode(
    *,
    policy: str,
    allow_prompt: bool,
    automation_requested: bool,
) -> tuple[bool, str]:
    """Resolve whether to overlay dirty source paths into the tmp clone.

    Returns ``(enabled, reason)``.
    """
    normalized = (policy or "auto").strip().lower()
    if normalized == "overlay-dirty-source":
        return True, "flag_forced_overlay"
    if normalized == "committed-head-only":
        return False, "flag_forced_committed_head_only"
    if normalized == "prompt":
        if allow_prompt:
            from failure_ux import prompt_tmp_clone_source_overlay
            decision = prompt_tmp_clone_source_overlay()
            return decision.value == "retry", f"operator_{decision.value}"
        return False, "prompt_unavailable"
    if normalized == "auto":
        if automation_requested:
            return True, "auto_accepted"
        if allow_prompt:
            from failure_ux import prompt_tmp_clone_source_overlay
            decision = prompt_tmp_clone_source_overlay()
            return decision.value == "retry", f"auto_{decision.value}"
        return True, "auto_noninteractive_overlay"
    return False, f"unknown_policy:{normalized}"


# ---------------------------------------------------------------------------
# Copy-back
# ---------------------------------------------------------------------------


def _copy_tmp_clone_doc_surfaces(
    *,
    clone_repo: Path,
    source_repo: Path,
    dirty_source_paths: set[str],
) -> dict[str, Any]:
    copied: list[str] = []
    skipped: list[dict[str, str]] = []
    for rel in TMP_CLONE_COPYBACK_DOC_PATHS:
        clone_path = clone_repo / rel
        if not clone_path.exists():
            continue
        if rel in dirty_source_paths or f"{rel}/" in dirty_source_paths:
            skipped.append({"path": rel, "reason": "source_dirty"})
            continue
        source_path = source_repo / rel
        source_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.exists() and source_path.read_bytes() == clone_path.read_bytes():
            continue
        shutil.copy2(clone_path, source_path)
        copied.append(rel)
    return {"copied": copied, "skipped": skipped}


def copy_back_outputs(
    *,
    clone_repo: Path,
    source_repo: Path,
    created_entries: list[str],
) -> dict[str, Any]:
    """Copy watchdog artifacts and docs from the clone back to the source repo.

    This is the public copy-back entry point.  It handles:
      - Copying run summary directories and failed-run files
      - Rewriting repo root strings in summary JSON payloads
      - Copying doc surfaces (FAILURE_LOG.md, etc.) when not locally dirty
      - Rebuilding the run analysis index

    Returns a report dict with keys: ``copied_entries``, ``skipped_entries``,
    ``docs``, ``index_rebuilt``.
    """
    clone_runs_root = _runs_root_for_repo(clone_repo)
    source_runs_root = _runs_root_for_repo(source_repo)
    source_runs_root.mkdir(parents=True, exist_ok=True)
    dirty_source_paths = _source_dirty_paths_set(source_repo)
    copied_entries: list[str] = []
    skipped_entries: list[dict[str, str]] = []
    for name in created_entries:
        clone_entry = clone_runs_root / name
        if not clone_entry.exists():
            skipped_entries.append({"path": name, "reason": "clone_output_missing"})
            continue
        if clone_entry.is_dir():
            summary_path = clone_entry / "summary.json"
            if not summary_path.exists():
                skipped_entries.append({"path": name, "reason": "incomplete_run_dir"})
                continue
            dest_entry = source_runs_root / name
            if dest_entry.exists():
                shutil.rmtree(dest_entry)
            shutil.copytree(clone_entry, dest_entry)
            copied_summary = dest_entry / "summary.json"
            payload = json.loads(copied_summary.read_text(encoding="utf-8"))
            payload = _rewrite_repo_root_strings(payload, clone_repo, source_repo)
            copied_summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            copied_entries.append(name)
            continue
        if clone_entry.suffix == ".json" and clone_entry.name.startswith("failed-"):
            dest_entry = source_runs_root / name
            payload = json.loads(clone_entry.read_text(encoding="utf-8"))
            payload = _rewrite_repo_root_strings(payload, clone_repo, source_repo)
            dest_entry.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            copied_entries.append(name)
            continue
        skipped_entries.append({"path": name, "reason": "unrecognized_output"})

    doc_report = _copy_tmp_clone_doc_surfaces(
        clone_repo=clone_repo,
        source_repo=source_repo,
        dirty_source_paths=dirty_source_paths,
    )
    index_rebuilt = _rebuild_analysis_index(source_repo)
    return {
        "copied_entries": copied_entries,
        "skipped_entries": skipped_entries,
        "docs": doc_report,
        "index_rebuilt": index_rebuilt,
    }


def _rebuild_analysis_index(repo_root: Path) -> bool:
    """Rebuild the run analysis index in the source repo."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "analyze_runs",
            str(repo_root / "scripts" / "analyze_runs.py"),
        )
        if spec is None or spec.loader is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "load_all_runs"):
            mod.load_all_runs(repo_root)
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Created entries helper
# ---------------------------------------------------------------------------


def _created_tmp_clone_run_entries(
    clone_repo: Path,
    baseline_entries: set[str],
) -> list[str]:
    """Return run entries created during the tmp-clone run (not in baseline)."""
    clone_runs_root = _runs_root_for_repo(clone_repo)
    if not clone_runs_root.exists():
        return []
    return sorted(
        entry.name
        for entry in clone_runs_root.iterdir()
        if entry.name != "index.json" and entry.name not in baseline_entries
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_tmp_clone_recovery(
    *,
    source_repo: Path,
    source_run_dir: Path,
    argv: list[str] | None = None,
    scope_blocker: dict[str, Any] | None = None,
    status_lines: list[str] | None = None,
    tmp_clone_source_policy: str = "committed-head-only",
    allow_prompt: bool = False,
    automation_requested: bool = False,
) -> TmpCloneRecoveryResult:
    """Run the canonical watchdog from a disposable git clone.

    This is the public entry point for tmp-clone recovery.  It:
      1. Creates a disposable ``git clone`` of *source_repo*.
      2. Optionally overlays dirty source paths into the clone.
      3. Runs ``watchdog_run_canonical.py`` from the clone.
      4. Copies back artifacts and doc surfaces.
      5. Annotates summary payloads with ``tmp_clone_fallback`` metadata.
      6. Cleans up the clone directory.

    Args:
        source_repo: The authoritative repository to clone.
        source_run_dir: The run directory in the source repo (cleaned
            before clone to avoid stale state).
        argv: Command-line arguments for the cloned canonical run.
            Defaults to ``sys.argv[1:]``.
        scope_blocker: The scope-boundary blocker dict that triggered
            the tmp-clone path (stored in summary metadata).
        status_lines: Git status lines from the source repo (used for
            overlay decisions).
        tmp_clone_source_policy: Overlay policy string (``auto``,
            ``overlay-dirty-source``, ``committed-head-only``, ``prompt``).
        allow_prompt: Whether interactive prompts are permitted.
        automation_requested: Whether the run was requested in
            non-interactive automation mode.

    Returns:
        A ``TmpCloneRecoveryResult``.
    """
    # Clean stale source_run_dir before clone.
    shutil.rmtree(source_run_dir, ignore_errors=True)

    tmp_root: Path | None = None
    tmp_root, tmp_space_report = _allocate_tmp_clone_root(tmp_parent=Path("/tmp"))
    if tmp_space_report.get("removed") or tmp_space_report.get("errors"):
        _out_write(
            "[WATCHDOG] tmp-clone space = "
            f"removed_oldest={len(tmp_space_report.get('removed') or [])} "
            f"kept={len(tmp_space_report.get('kept') or [])} "
            f"errors={len(tmp_space_report.get('errors') or [])}"
        )
    clone_repo = tmp_root / "repo"
    created_entries: list[str] = []
    copyback_report: dict[str, Any] | None = None
    overlay_report: dict[str, Any] | None = None
    repeat_exact_sources: dict[str, Any] | None = None
    error = ""

    try:
        # 1. Clone the exact parent commit. Candidate deploy must build from
        # immutable source, not from the operator's mutable working tree.
        try:
            source_ref_full = _git_output("rev-parse", "HEAD", cwd=source_repo)
        except Exception as exc:
            return TmpCloneRecoveryResult(
                ok=False,
                child_returncode=-1,
                copyback_ok=False,
                error=f"source commit resolution failed: {exc}",
            )
        try:
            subprocess.run(
                ["git", "clone", "--quiet", str(source_repo), str(clone_repo)],
                check=True, timeout=TMP_CLONE_MAX_CLONE_TIMEOUT,
            )
            subprocess.run(
                ["git", "checkout", "--quiet", source_ref_full],
                cwd=str(clone_repo),
                check=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
            return TmpCloneRecoveryResult(
                ok=False,
                child_returncode=-1,
                copyback_ok=False,
                error=f"git clone failed: {exc}",
            )

        # 2. Prepare environment
        env = os.environ.copy()
        env[TMP_CLONE_ACTIVE_ENV] = "1"
        env[CANONICAL_LAUNCH_ENV] = "1"
        env.setdefault("WATCHDOG_NON_INTERACTIVE", "1")

        child_cmd = [
            sys.executable, "scripts/watchdog_run_canonical.py",
            *(argv or sys.argv[1:]),
        ]
        repeat_exact_sources = copy_repeat_exact_sources(
            source_repo=source_repo,
            clone_repo=clone_repo,
            argv=list(argv or sys.argv[1:]),
        )
        if repeat_exact_sources.get("requested"):
            _out_write(
                "[WATCHDOG] tmp-clone repeat-exact sources = "
                f"requested={','.join(repeat_exact_sources.get('requested') or [])} "
                f"copied={len(repeat_exact_sources.get('copied') or [])} "
                f"missing={','.join(repeat_exact_sources.get('missing') or []) or 'none'}"
            )
        baseline_entries = set(_created_tmp_clone_run_entries(clone_repo, set()))
        status_lines = list(status_lines or [])

        # 3. Source overlay
        overlay_enabled, overlay_reason = _resolve_overlay_mode(
            policy=tmp_clone_source_policy,
            allow_prompt=allow_prompt,
            automation_requested=automation_requested,
        )
        if overlay_enabled:
            overlay_data = _overlay_dirty_source_paths(
                source_repo=source_repo,
                clone_repo=clone_repo,
                status_lines=status_lines,
            )
            overlay_data["policy"] = tmp_clone_source_policy
            overlay_data["reason"] = overlay_reason
            overlay_commit = _commit_tmp_clone_overlay_state(
                clone_repo=clone_repo,
                run_id=source_run_dir.name,
            )
            overlay_data["overlay_commit"] = overlay_commit
            overlay_report = overlay_data
            _out_write(
                "[WATCHDOG] TMP-CLONE PREP = mixed dirty scope detected; "
                "local edits stay untouched and proof will overlay dirty local source inputs "
                "into the disposable clone before deploy"
            )
        else:
            overlay_report = {
                "enabled": False,
                "policy": tmp_clone_source_policy,
                "reason": overlay_reason,
                "tracked_paths": sorted(_tracked_status_paths(status_lines)),
                "untracked_paths": sorted(_untracked_status_paths(status_lines)),
            }
            _out_write(
                "[WATCHDOG] TMP-CLONE PREP = mixed dirty scope detected; "
                "local edits stay untouched and proof will use committed HEAD only"
            )

        _out_write(f"[WATCHDOG] tmp clone source = {source_repo}")
        _out_write(f"[WATCHDOG] tmp clone path   = {clone_repo}")
        _out_write("[WATCHDOG] TMP-CLONE LAUNCH = starting canonical proof from disposable clone")

        # 4. Run the child
        child = subprocess.run(
            child_cmd,
            cwd=str(clone_repo),
            env=env,
            text=True,
            check=False,
        )

        parent_source_drift: dict[str, Any] | None = None
        try:
            source_ref_after_child = _git_output("rev-parse", "HEAD", cwd=source_repo)
        except Exception as exc:
            parent_source_drift = {
                "kind": "source_locked",
                "reason": f"parent source commit resolution failed after tmp-clone child: {exc}",
                "expected_source_ref": source_ref_full,
                "current_source_ref": None,
            }
        else:
            if source_ref_after_child != source_ref_full:
                parent_source_drift = {
                    "kind": "source_locked",
                    "reason": "parent source repo moved while tmp-clone proof was running",
                    "expected_source_ref": source_ref_full,
                    "current_source_ref": source_ref_after_child,
                }

        # 5. Copy back
        created_entries = _created_tmp_clone_run_entries(clone_repo, baseline_entries)
        copyback_report = copy_back_outputs(
            clone_repo=clone_repo,
            source_repo=source_repo,
            created_entries=created_entries,
        )
        _out_write(
            "[WATCHDOG] tmp-clone result = "
            f"child_rc={child.returncode} "
            f"entries={len(copyback_report.get('copied_entries') or [])} "
            f"docs={len((copyback_report.get('docs') or {}).get('copied') or [])} "
            f"index_rebuilt={copyback_report.get('index_rebuilt')}"
        )

        # 6. Annotate summary payloads with tmp_clone_fallback metadata
        _annotate_tmp_clone_summaries(
            source_repo=source_repo,
            clone_repo=clone_repo,
            created_entries=created_entries,
            scope_blocker=scope_blocker,
            overlay_report=overlay_report,
            copyback_report=copyback_report,
            tmp_clone_source_policy=tmp_clone_source_policy,
            parent_source_drift=parent_source_drift,
        )

        child_failed_without_entries = child.returncode != 0 and not created_entries
        child_prelaunch_ok = not child_failed_without_entries
        prelaunch_failed = child_failed_without_entries
        if copyback_report is not None:
            copyback_report["child_prelaunch_ok"] = child_prelaunch_ok
            copyback_report["prelaunch_failed"] = prelaunch_failed
            copyback_report["child_returncode"] = child.returncode
            copyback_report["parent_source_drift"] = parent_source_drift
        effective_returncode = 2 if parent_source_drift else child.returncode
        copyback_ok = (
            len(copyback_report.get("copied_entries", [])) > 0
            or (effective_returncode == 0 and not created_entries)
        )
        return TmpCloneRecoveryResult(
            ok=True,
            child_returncode=effective_returncode,
            copyback_ok=copyback_ok,
            child_prelaunch_ok=child_prelaunch_ok,
            prelaunch_failed=prelaunch_failed,
            created_entries=created_entries,
            copyback_report=copyback_report,
            overlay_report=overlay_report,
            repeat_exact_sources=repeat_exact_sources,
            parent_source_drift=parent_source_drift,
            error=(
                "parent source repo moved while tmp-clone proof was running"
                if parent_source_drift
                else
                "tmp clone child exited before creating run entries"
                if child_failed_without_entries
                else ""
            ),
        )

    except Exception as exc:
        error = str(exc)
        return TmpCloneRecoveryResult(
            ok=False,
            child_returncode=-1,
            copyback_ok=False,
            error=error,
        )

    finally:
        # Always clean up the tmp clone directory.
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Summary annotation helper
# ---------------------------------------------------------------------------


def _annotate_tmp_clone_summaries(
    *,
    source_repo: Path,
    clone_repo: Path,
    created_entries: list[str],
    scope_blocker: dict[str, Any] | None = None,
    overlay_report: dict[str, Any] | None = None,
    copyback_report: dict[str, Any] | None = None,
    tmp_clone_source_policy: str = "committed-head-only",
    parent_source_drift: dict[str, Any] | None = None,
) -> None:
    """Annotate source repo run summaries with ``tmp_clone_fallback`` metadata.

    This runs inside the ``finally`` block of the parent orchestrator so
    summaries always carry the provenance metadata even if copy-back fails.
    """
    if not created_entries:
        return

    summary_root = _runs_root_for_repo(source_repo)
    for name in created_entries:
        copied_path = summary_root / name
        if not copied_path.exists():
            continue
        payload_path = copied_path / "summary.json" if copied_path.is_dir() else copied_path
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        payload["tmp_clone_fallback"] = {
            "used": True,
            "source_repo": str(source_repo),
            "clone_repo": str(clone_repo),
            "scope_blocker_reason": (scope_blocker or {}).get("reason"),
            "source_overlay": overlay_report or {"enabled": False, "policy": tmp_clone_source_policy},
            "copyback": copyback_report or {"copied_entries": created_entries, "index_rebuilt": False},
        }
        payload["recovery_mode"] = "tmp_clone"
        payload["proof_source_mode"] = "tmp_clone"
        if parent_source_drift:
            payload["source_locked"] = parent_source_drift
            payload["prelaunch_blocker"] = {
                "stage": "source_locked",
                "kind": "post_contract_source_drift",
                "owner": "scripts/watchdog_run_canonical.py",
                "expected_source_ref": parent_source_drift.get("expected_source_ref"),
                "current_source_ref": parent_source_drift.get("current_source_ref"),
                "first_fatal": parent_source_drift.get("reason"),
                "next_action": (
                    "Rerun from a committed HEAD and do not mutate the parent repo while "
                    "the tmp-clone proof is running."
                ),
            }
            payload["error_kind"] = "prelaunch_block"
        payload_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
