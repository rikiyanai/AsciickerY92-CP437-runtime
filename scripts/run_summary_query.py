"""Run summary query — shared seam for watchdog run history and artifact lookup.

Seam: single authoritative read path for watchdog run summaries, artifacts,
and run metadata.  Previously duplicated across ``scripts/launcher.py``
(``_run_summary_path``, ``_iter_run_summary_records``, ``_latest_run_id``,
``_read_run_summary``, ``_path_from_summary``, ``_watchdog_artifact_status``,
``_choose_run_id``, ``_run_record_label``) and inline in the canonical script.

Module has no imports from watchdog_run_canonical or testing/launcher to
prevent circular imports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RUNS_ROOT = Path(__file__).resolve().parent.parent / "artifacts" / "maintainer" / "watchdog_runs"
_SAFE_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,200}$")


# ---------------------------------------------------------------------------
# Run summary path resolution
# ---------------------------------------------------------------------------


def run_summary_path(run_id: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    """Return the filesystem path for a run's summary JSON.

    Normal runs are stored at ``{runs_root}/{run_id}/summary.json``.
    Failed runs (interrupted before summary was written) are stored as
    ``{runs_root}/failed-{timestamp}.json``.
    """
    if not _SAFE_RUN_ID_RE.match(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    if run_id.startswith("failed-"):
        return runs_root / f"{run_id}.json"
    return runs_root / run_id / "summary.json"


# ---------------------------------------------------------------------------
# Summary file loading
# ---------------------------------------------------------------------------


def load_summary_file(summary_path: Path) -> dict[str, Any] | None:
    """Load and parse a summary JSON file.

    Returns *None* when the file doesn't exist, isn't valid JSON, or
    the top-level value is not a dict.
    """
    if not summary_path or not summary_path.exists():
        return None
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# Iterate run records
# ---------------------------------------------------------------------------


def iter_run_summary_records(
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> list[tuple[str, Path, dict[str, Any]]]:
    """Iterate all run summary records, most recent first.

    Each record is a tuple of ``(run_id, summary_path, summary_dict)``.
    Both directory-based runs (``{run_id}/summary.json``) and flat failed
    files (``failed-{ts}.json``) are included.
    """
    if not runs_root.exists():
        return []

    try:
        candidates = list(runs_root.iterdir())
    except OSError:
        return []

    records: list[tuple[str, Path, dict[str, Any]]] = []
    for path in candidates:
        run_id = path.stem if path.is_file() else path.name
        if path.is_dir():
            summary_path = path / "summary.json"
        elif path.is_file() and path.name.startswith("failed-") and path.suffix == ".json":
            summary_path = path
        else:
            continue
        if not summary_path.exists():
            continue
        summary = load_summary_file(summary_path)
        if summary is None:
            continue
        records.append((run_id, summary_path, summary))

    def _mtime(record: tuple[str, Path, dict[str, Any]]) -> float:
        try:
            return record[1].stat().st_mtime
        except OSError:
            return 0.0

    return sorted(records, key=_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Latest run ID
# ---------------------------------------------------------------------------


def latest_run_id(runs_root: Path = DEFAULT_RUNS_ROOT) -> str | None:
    """Return the run ID of the most recent watchdog run, or *None*."""
    records = iter_run_summary_records(runs_root)
    return records[0][0] if records else None


# ---------------------------------------------------------------------------
# Read summary by run ID
# ---------------------------------------------------------------------------


def read_run_summary(
    run_id: str,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> dict[str, Any] | None:
    """Load a run summary by run ID.

    Returns *None* when the run doesn't exist or the summary is invalid.
    """
    summary_path = run_summary_path(run_id, runs_root=runs_root)
    if not summary_path.exists():
        return None
    return load_summary_file(summary_path)


# ---------------------------------------------------------------------------
# Path from summary value
# ---------------------------------------------------------------------------


def path_from_summary(value: object) -> Path | None:
    """Extract and return a filesystem ``Path`` from a summary dict value.

    Accepts *str* values that are non-empty after stripping.  Returns
    *None* for *None*, empty strings, or non-*str* types.  The path is
    expanded via ``Path.expanduser()``.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


# ---------------------------------------------------------------------------
# Run record label (for display in choose menus)
# ---------------------------------------------------------------------------


def _repo_relative(path: Path, repo_root: Path) -> str:
    """Return *path* relative to *repo_root*, or the absolute path as fallback."""
    try:
        return str(path.resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


def run_record_label(
    record: tuple[str, Path, dict[str, Any]],
    repo_root: Path | None = None,
) -> str:
    """Return a one-line label for a run record.

    Format: ``{run_id} mode={mode} target={target} label={label} head={head12} source={rel_path}``
    """
    run_id, summary_path, summary = record
    target = str(summary.get("target") or summary.get("slot") or summary.get("target_slot") or "-")
    mode = str(summary.get("mode") or "-")
    label = str(summary.get("run_label") or summary.get("label") or "-")
    head = str(summary.get("git_head") or summary.get("source_ref") or "")
    head_text = f" head={head[:12]}" if head else ""
    rel = _repo_relative(summary_path, repo_root or Path.cwd())
    return f"{run_id} mode={mode} target={target} label={label}{head_text} source={rel}"


# ---------------------------------------------------------------------------
# Choose run ID interactively
# ---------------------------------------------------------------------------


def choose_run_id(
    runs_root: Path = DEFAULT_RUNS_ROOT,
    default: str | None = None,
    *,
    prompt_fn: Any = None,
    write_fn: Any = None,
) -> str | None:
    """Interactive run selection.

    Displays the 30 most recent run records (numbered) and lets the user
    pick by number or paste a run ID directly.

    Args:
        runs_root: Root directory of run summaries.
        default: Default run ID to select (e.g., the latest).
        prompt_fn: A callable ``(prompt_text, default_value) -> str`` for
            user input.  Defaults to ``input()``.
        write_fn: A callable ``(text) -> None`` for display output.
            Defaults to ``print()``.

    Returns:
        The selected run ID, or *None* if the user cancels.
    """
    records = iter_run_summary_records(runs_root)
    _print = write_fn or print
    _input = prompt_fn or input

    if not records:
        _print("  No watchdog run summaries found.")
        return default

    _print("  Pick a run by number or paste a run id:")
    for index, record in enumerate(records[:30], 1):
        marker = " [latest]" if index == 1 else ""
        _print(f"    {index:2}. {run_record_label(record)}{marker}")
    if len(records) > 30:
        _print(f"    ... {len(records) - 30} more runs omitted; paste the run id to select one.")

    default_choice = "1"
    if default:
        for index, record in enumerate(records[:30], 1):
            if record[0] == default:
                default_choice = str(index)
                break

    try:
        raw = _input(
            f"  Run number/id (1-N from list above, or paste a run ID like 20260428-123456) — q to cancel [{default_choice}]: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not raw or raw.lower() == "q":
        return None

    if raw.isdigit():
        selected = int(raw)
        if 1 <= selected <= len(records):
            return records[selected - 1][0]
        _print(f"  Run number {selected} is out of range — enter a number from 1 to {len(records)}.")
        return None

    return raw


# ---------------------------------------------------------------------------
# Watchdog artifact status classification
# ---------------------------------------------------------------------------


def watchdog_artifact_status(
    summary: dict[str, Any] | None,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> dict[str, Any]:
    """Classify machine-local watchdog artifacts without implying git tracking.

    Returns a dict with keys:
      - summary: ``"present"`` | ``"loaded"`` | ``"missing"``
      - raw: ``"present"`` | ``"missing"`` | ``"not-recorded"``
      - archive: ``"present"`` | ``"missing"`` | ``"not-recorded"``
      - slot_manifest: ``"present"`` | ``"missing"``
      - storage: always ``"machine-local/gitignored"``
      - source_ref, git_head, worktree_clean, restoreable_by_commit
    """
    if not summary:
        return {
            "summary": "missing",
            "raw": "unknown",
            "archive": "unknown",
            "slot_manifest": "unknown",
            "storage": "machine-local/gitignored",
        }

    run_id = str(summary.get("run_id") or "")
    indexed_summary = runs_root / run_id / "summary.json" if run_id else None
    flat_failed = runs_root / f"{run_id}.json" if run_id.startswith("failed-") else None
    summary_present = bool(
        (indexed_summary and indexed_summary.is_file())
        or (flat_failed and flat_failed.is_file())
    )

    raw_path = path_from_summary(summary.get("artifact_path"))
    archive_path = path_from_summary(summary.get("archive_path"))
    raw_present = bool(raw_path and raw_path.is_dir())
    archive_present = bool(archive_path and archive_path.is_dir())
    slot_manifest_present = bool(
        (raw_path and (raw_path / "slot_manifest.json").is_file())
        or (archive_path and (archive_path / "slot_manifest.json").is_file())
    )
    rollback = summary.get("rollback") if isinstance(summary.get("rollback"), dict) else {}

    return {
        "summary": "present" if summary_present else "loaded",
        "raw": "present" if raw_present else ("missing" if raw_path else "not-recorded"),
        "archive": "present" if archive_present else ("missing" if archive_path else "not-recorded"),
        "slot_manifest": "present" if slot_manifest_present else "missing",
        "storage": "machine-local/gitignored",
        "source_ref": summary.get("source_ref") or summary.get("git_head") or "-",
        "git_head": summary.get("git_head") or "-",
        "worktree_clean": summary.get("worktree_clean"),
        "restoreable_by_commit": rollback.get("restoreable_by_commit"),
    }
