"""Failure log management — append-only structured failure tracking.

The canonical failure log lives at:
  docs/FAILURE_LOG.md

Each entry has a unique ID (FL-NNN), status, and structured fields.
Updates enforce append-only semantics and status vocabulary.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .fl_config import CANONICAL_FAILURE_LOG
from .fl_overlay import (
    _derive_default_fl_area as shared_derive_default_fl_area,
    _effective_overlay_record as shared_effective_overlay_record,
    _normalize_overlay_list_value,
)  # canonical shared impls (FL-1825 / RQ-054)
from .fl_schema import FL_OVERLAY_AREA_VALUES, FL_OVERLAY_LIST_FIELDS
from .report_schema import FORBIDDEN_STATUS_WORDS

VALID_STATUS = frozenset({"OPEN", "PARTIAL", "MONITORING", "RESOLVED", "ACCOUNTED"})
TERMINAL_STATUS = frozenset({"RESOLVED", "ACCOUNTED"})

# Regex to parse FL entries from markdown
_FL_HEADER_RE = re.compile(
    r"^###\s+(FL-\d{3,})\s*[:\-—]\s*(.+)$", re.MULTILINE
)
_FL_STATUS_RE = re.compile(
    r"^\s*(?:[-*]\s+)?\*{0,2}Status:\*{0,2}\s*(.+?)\s*$",
    re.MULTILINE,
)


def _normalize_status_value(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    return value.split()[0]


@dataclass
class FailureEntry:
    """A single failure log entry.

    ``status`` is the original status from the **Status:** line.
    ``effective_status`` reflects the latest append-only status update
    (if any), falling back to ``status`` when no updates exist.
    Consumers should use ``effective_status`` for filtering.
    """
    failure_id: str         # "FL-001"
    title: str
    status: str             # OPEN | PARTIAL | MONITORING | RESOLVED (original)
    date_opened: str        # ISO date
    category: str           # e.g. "pipeline", "quality_gate", "doc_drift"
    description: str
    root_cause: str = ""
    evidence: list[str] = field(default_factory=list)    # commit hashes, file paths
    resolution: str = ""
    date_resolved: str = ""
    related_ids: list[str] = field(default_factory=list)  # e.g. ["FL-002"]
    effective_status: str = ""   # latest status after updates (empty = same as status)
    last_update_date: str = ""   # date of most recent status update
    # Overlay metadata (from ## FL Metadata Overlay section; later rows patch earlier rows)
    area: str = ""
    subsystems: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    proof_state: str = ""
    epoch_status: str = ""
    complaint_refs: list[str] = field(default_factory=list)
    rq_refs: list[str] = field(default_factory=list)
    github_refs: list[str] = field(default_factory=list)
    complaint_counter_state: str = ""
    code_refs: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    anchor_functions: list[str] = field(default_factory=list)
    enforce_resolution_invariant: bool = field(default=True, repr=False, compare=False)

    def __post_init__(self):
        # Validate original status
        if self.status not in VALID_STATUS:
            raise ValueError(
                f"Invalid status {self.status!r}, must be one of {VALID_STATUS}"
            )
        if not self.failure_id.startswith("FL-"):
            raise ValueError(
                f"failure_id must start with 'FL-', got {self.failure_id!r}"
            )
        # Default effective_status to original status
        if not self.effective_status:
            object.__setattr__(self, "effective_status", self.status)
        # Enforce RESOLVED invariant at creation time
        if self.enforce_resolution_invariant and self.effective_status in TERMINAL_STATUS:
            if not self.resolution and not self.evidence:
                raise ValueError(
                    f"{self.effective_status} status requires resolution text or evidence"
                )


def _parse_entry_block(block: str, fid: str, title: str, *, strict: bool = False) -> FailureEntry:
    """Parse a single entry block into a FailureEntry.

    Args:
        strict: If True, raise ValueError on malformed input (missing Status field,
                invalid Status value). If False (default), silently fall back to "OPEN".
    """
    status_match = _FL_STATUS_RE.search(block)
    if status_match:
        status = _normalize_status_value(status_match.group(1))
        if strict and status not in VALID_STATUS:
            raise ValueError(
                f"{fid}: invalid Status value {status!r}; "
                f"expected one of {sorted(VALID_STATUS)}"
            )
        if not strict and status not in VALID_STATUS:
            warnings.warn(
                f"{fid} has unrecognized status {status!r}; defaulting to OPEN",
                RuntimeWarning,
                stacklevel=2,
            )
    else:
        if strict:
            raise ValueError(f"{fid}: missing **Status:** field")
        warnings.warn(
            f"{fid}: missing **Status:** field; defaulting to OPEN",
            RuntimeWarning,
            stacklevel=2,
        )
        status = "OPEN"

    def _extract_field(name: str) -> str:
        # Markdown bold format: **Field:** value (colon inside bold)
        pattern = re.compile(
            rf"^\s*(?:[-*]\s+)?\*{{0,2}}{name}:\*{{0,2}}\s*(.+)$",
            re.MULTILINE | re.IGNORECASE,
        )
        m = pattern.search(block)
        if not m:
            return ""
        return m.group(1).strip()

    date_opened = _extract_field("Date Opened") or _extract_field("Opened")
    category = _extract_field("Category")
    description = _extract_field("Description")
    root_cause = _extract_field("Root Cause")
    resolution = _extract_field("Resolution")
    date_resolved = (
        _extract_field("Date Resolved")
        or _extract_field("Resolved")
        or _extract_field("Date Closed")
        or _extract_field("Closed")
    )

    # Extract evidence lines (bulleted list after "Evidence:")
    evidence: list[str] = []
    ev_match = re.search(
        r"^\*{0,2}Evidence:\*{0,2}\s*\n((?:\s*[-*]\s+.+\n?)+)",
        block, re.MULTILINE
    )
    if ev_match:
        for line in ev_match.group(1).strip().split("\n"):
            line = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if line:
                evidence.append(line)

    # Extract related IDs
    related: list[str] = []
    rel_match = re.search(
        r"^\*{0,2}Related:\*{0,2}\s*(.+)$", block, re.MULTILINE
    )
    if rel_match:
        related = [r.strip() for r in rel_match.group(1).split(",") if r.strip()]

    # Parse append-only status update subsections.
    # Format: > **[2026-02-19] Status update: OPEN -> PARTIAL**
    effective_status = status if status in VALID_STATUS else "OPEN"
    last_update_date = ""
    _STATUS_UPDATE_RE = re.compile(
        r">\s*\*{0,2}\[(\d{4}-\d{2}-\d{2})\]\s*Status update:\s*\w+\s*->\s*(\w+)\*{0,2}",
    )
    for m in _STATUS_UPDATE_RE.finditer(block):
        update_date = m.group(1)
        new_status = m.group(2)
        if new_status in VALID_STATUS:
            effective_status = new_status
            last_update_date = update_date
    # If resolved via update, pull resolution from the update subsection
    if effective_status in TERMINAL_STATUS and not resolution:
        # Look for resolution line in blockquote after the last status update
        res_in_update = re.findall(
            r">\s*(?:Resolution:\s*)?(.+?)(?:\n|$)", block
        )
        if res_in_update:
            # Use the line right after the status update header
            for line in res_in_update:
                line = line.strip()
                if line and "Status update:" not in line and "Evidence:" not in line:
                    resolution = line
                    break

    return FailureEntry(
        failure_id=fid,
        title=title,
        status=status if status in VALID_STATUS else "OPEN",
        date_opened=date_opened,
        category=category,
        description=description,
        root_cause=root_cause,
        evidence=evidence,
        resolution=resolution,
        date_resolved=date_resolved,
        related_ids=related,
        effective_status=effective_status,
        last_update_date=last_update_date,
        enforce_resolution_invariant=False,
    )


def iter_failure_entry_blocks_from_text(content: str) -> list[tuple[str, str, str]]:
    """Return `(fl_id, title, block)` tuples in file order."""
    headers = list(_FL_HEADER_RE.finditer(content or ""))
    if not headers:
        return []
    blocks: list[tuple[str, str, str]] = []
    for index, match in enumerate(headers):
        fid = match.group(1)
        title = match.group(2).strip()
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(content)
        blocks.append((fid, title, content[start:end]))
    return blocks


def iter_failure_entry_blocks(path: Optional[Path] = None) -> list[tuple[str, str, str]]:
    """Read the failure log and return `(fl_id, title, block)` tuples."""
    path = path or CANONICAL_FAILURE_LOG
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    return iter_failure_entry_blocks_from_text(content)


def find_failure_entry_blocks(path: Optional[Path], fl_id: str) -> list[str]:
    """Return all raw markdown blocks for one FL id in file order."""
    target = str(fl_id or "").strip().upper()
    if not target:
        return []
    return [
        f"### {fid}: {title}\n{block}".rstrip()
        for fid, title, block in iter_failure_entry_blocks(path)
        if fid.upper() == target
    ]


_FL_OVERLAY_BLOCK_RE = re.compile(
    r"^## FL Metadata Overlay[ \t]*\n.*?^```jsonl[ \t]*\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)


def _iter_overlay_records(path: Path):
    """Yield (fl_id, record) tuples from the FL Metadata Overlay block.

    Handles missing file, missing overlay block, malformed JSONL lines,
    and missing/bad fl_id fields gracefully (all skipped).
    """
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    m = _FL_OVERLAY_BLOCK_RE.search(content)
    if not m:
        return
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        fl_id = record.get("fl")
        if fl_id and isinstance(fl_id, str):
            yield fl_id, record


def read_fl_overlay(path: Optional[Path] = None) -> dict:
    """Parse the FL metadata overlay section from the failure log.

    Returns dict mapping fl_id -> merged overlay record (dict).
    Later rows patch earlier rows key-by-key (records processed in file order).

    NOTE FL-4023 (2026-05-14): a union-for-list-fields variant was briefly
    shipped to fix manual-edit shadowing, but an audit found it resurrected
    3,735 intentionally-pruned items across 1,105 fl_ids — late JSONL rows
    act as authoritative snapshots, not deltas. Reverted to last-write-wins.
    The right fix is a 'fl overlay consolidate FL-NNNN' subcommand that
    collapses an fl_id's rows into a single canonical row after manual edits.
    """
    path = path or CANONICAL_FAILURE_LOG
    overlay: dict = {}
    for fl_id, record in _iter_overlay_records(path):
        merged = dict(overlay.get(fl_id) or {})
        merged.update(record)
        overlay[fl_id] = merged
    return overlay


# _normalize_overlay_list_value is imported from fl_overlay (FL-1825 / RQ-054).
# Do not redefine here — the shared module is the canonical source.


def _derive_default_fl_area(category: str) -> str:
    return shared_derive_default_fl_area(category=category)


def _effective_overlay_record(
    fid: str,
    *,
    category: str = "",
    overlay: Optional[dict] = None,
) -> dict:
    return shared_effective_overlay_record(
        fid,
        overlay=overlay,
        default_area=_derive_default_fl_area(category),
        default_kinds=["unclassified"],
        default_proof_state="OPEN",
    )


def read_failure_log(path: Optional[Path] = None, *, strict: bool = False) -> list[FailureEntry]:
    """Read and parse the failure log markdown into entries.

    Args:
        strict: If True, propagate to _parse_entry_block — raises ValueError on
                malformed entries (missing/invalid Status, etc.) instead of silently
                recovering. Useful for CI validation and test fixtures.
    """
    path = path or CANONICAL_FAILURE_LOG
    if not path.exists():
        return []

    overlay = read_fl_overlay(path)
    blocks = iter_failure_entry_blocks(path)
    if not blocks:
        return []

    entries: list[FailureEntry] = []
    _warn_unrecognized: list[str] = []
    _warn_missing: list[str] = []
    with warnings.catch_warnings(record=True) as _captured:
        warnings.simplefilter("always")
        for fid, title, block in blocks:
            try:
                entry = _parse_entry_block(block, fid, title, strict=strict)
                ov = _effective_overlay_record(fid, category=entry.category, overlay=overlay.get(fid))
                entry.area = ov.get("Area", "") or ""
                entry.subsystems = list(ov.get("Subsystems") or [])
                entry.kinds = list(ov.get("Kinds") or [])
                entry.proof_state = ov.get("ProofState", "") or ""
                entry.epoch_status = ov.get("EpochStatus", "") or ""
                entry.complaint_refs = list(ov.get("ComplaintRefs") or [])
                entry.rq_refs = list(ov.get("RQRefs") or [])
                entry.github_refs = list(ov.get("GitHubRefs") or [])
                entry.complaint_counter_state = ov.get("ComplaintCounterState", "") or ""
                entry.code_refs = list(ov.get("CodeRefs") or [])
                entry.touched_files = list(ov.get("TouchedFiles") or [])
                entry.anchor_functions = list(ov.get("AnchorFunctions") or [])
                entries.append(entry)
            except ValueError:
                if strict:
                    raise
                # Lenient mode: skip malformed entries rather than crash
                continue

    # Batch parse warnings: emit one aggregate line instead of one per legacy entry
    for w in _captured:
        msg = str(w.message)
        if "unrecognized status" in msg:
            _warn_unrecognized.append(msg)
        else:
            _warn_missing.append(msg)
    if _warn_unrecognized:
        warnings.warn(
            f"FL parser: {len(_warn_unrecognized)} entr{'y' if len(_warn_unrecognized) == 1 else 'ies'} "
            "with unrecognized status values (pre-existing debt, defaulting to OPEN)",
            RuntimeWarning,
            stacklevel=2,
        )
    if _warn_missing:
        warnings.warn(
            f"FL parser: {len(_warn_missing)} entr{'y' if len(_warn_missing) == 1 else 'ies'} "
            "missing Status field (pre-existing debt, defaulting to OPEN)",
            RuntimeWarning,
            stacklevel=2,
        )

    return entries


def _parse_fl_base_number(fl_id: str) -> int:
    """Extract the base numeric part from an FL id.

    Handles both plain ids ("FL-123") and sibling ids ("FL-123.4").
    Sibling ids claim their parent's namespace for collision avoidance.
    Raises (IndexError, ValueError) on unparseable ids.
    """
    return int(fl_id.split("-")[1].split(".")[0])


def _max_fl_id_from_overlay(fl_path: Path):
    """Scan overlay JSONL rows for the highest FL number and known ids.

    Returns (max_num, known_ids) tuple:
        max_num: int, highest base FL number found (0 if none)
        known_ids: set of all FL id strings found in the overlay
    Uses the shared _iter_overlay_records iterator.
    """
    max_num = 0
    known_ids: set[str] = set()
    for fl_id, _record in _iter_overlay_records(fl_path):
        known_ids.add(fl_id)
        try:
            num = _parse_fl_base_number(fl_id)
            max_num = max(max_num, num)
        except (IndexError, ValueError):
            continue
    return max_num, known_ids


@contextmanager
def fl_write_lock(
    fl_path: Optional[Path] = None,
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[None]:
    """Serialize FAILURE_LOG.md mutations across processes (FL-4020).

    Wraps the next_failure_id -> append_entry -> _write_overlay_rows sequence
    in an exclusive fcntl.flock on a sentinel file beside FAILURE_LOG.md.
    Holds the lock for the whole critical section so concurrent 'fl add' calls
    serialize instead of racing for the same FL-NNNN id.

    Falls back to a no-op lock on platforms where fcntl is unavailable.
    """
    target = fl_path if fl_path is not None else CANONICAL_FAILURE_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = None
        if timeout_seconds and timeout_seconds > 0:
            import time as _t
            deadline = _t.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if deadline is None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    break
                import time as _t
                if _t.monotonic() >= deadline:
                    raise TimeoutError(
                        f"fl_write_lock timed out after {timeout_seconds}s on {lock_path}"
                    )
                _t.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def next_failure_id(entries: list[FailureEntry], fl_path: Optional[Path] = None) -> str:
    """Generate the next FL-NNN id.

    Scans both markdown entries and overlay JSONL rows to find the
    absolute highest FL base number, then returns the next increment.

    Sibling ids (e.g. FL-3899.1) claim their parent's namespace so
    the next allocation doesn't collide with the sibling family.

    Raises ValueError if the generated id would collide with any
    known id from markdown entries or overlay rows.

    Args:
        entries: Parsed FailureEntry list from read_failure_log.
        fl_path: Path to the failure log file for overlay scanning.
                 Defaults to CANONICAL_FAILURE_LOG.
    """
    max_num = 0
    known_ids: set[str] = set()
    for e in entries:
        known_ids.add(e.failure_id)
        try:
            num = _parse_fl_base_number(e.failure_id)
            max_num = max(max_num, num)
        except (IndexError, ValueError):
            continue

    # Always scan overlay when the file exists — overlay rows can hold
    # IDs not yet in the markdown, and the file may exist with overlay
    # rows but zero markdown entries (e.g. overlay-only patches).
    overlay_path = fl_path if fl_path is not None else CANONICAL_FAILURE_LOG
    if overlay_path.exists():
        overlay_max, overlay_ids = _max_fl_id_from_overlay(overlay_path)
        max_num = max(max_num, overlay_max)
        known_ids.update(overlay_ids)

    if max_num == 0:
        candidate = "FL-001"
    else:
        candidate = f"FL-{max_num + 1:03d}"

    # Safety: verify no collision with any known id (markdown or overlay).
    if candidate in known_ids:
        raise ValueError(
            f"Generated FL id {candidate} already exists. "
            f"Scanned max base was {max_num} but {candidate} is present "
            f"in markdown entries or overlay rows. "
            f"This is a bug in the FL id allocation scan."
        )

    return candidate


def find_open_entries(entries: list[FailureEntry]) -> list[FailureEntry]:
    """Return entries whose effective status is OPEN or PARTIAL."""
    return [e for e in entries if e.effective_status in ("OPEN", "PARTIAL")]


def _parse_iso_date(date_str: str) -> Optional[datetime]:
    """Parse a YYYY-MM-DD string into a timezone-aware datetime, or None."""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(
            date_str.strip(), "%Y-%m-%d"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _last_activity_date(entry: FailureEntry) -> Optional[datetime]:
    """Return max(date_opened, last_update_date) — the most recent activity."""
    opened = _parse_iso_date(entry.date_opened)
    updated = _parse_iso_date(entry.last_update_date)
    if opened and updated:
        return max(opened, updated)
    return updated or opened  # whichever is non-None, or None


def find_stale_open_entries(
    entries: list[FailureEntry],
    stale_days: int = 7,
) -> list[FailureEntry]:
    """Return OPEN/PARTIAL entries with no activity for stale_days.

    Uses max(date_opened, last_update_date) as the activity date.
    A recent status update resets the staleness clock.
    """
    open_entries = find_open_entries(entries)
    if not open_entries:
        return []

    now = datetime.now(timezone.utc)
    stale: list[FailureEntry] = []
    for entry in open_entries:
        activity = _last_activity_date(entry)
        if activity is None:
            # No parseable date = assume stale (can't prove recency)
            stale.append(entry)
            continue
        days_since_activity = (now - activity).days
        if days_since_activity >= stale_days:
            stale.append(entry)

    return stale


def find_long_open_entries(
    entries: list[FailureEntry],
    long_open_days: int = 30,
) -> list[FailureEntry]:
    """Return OPEN/PARTIAL entries opened more than long_open_days ago.

    Unlike find_stale_open_entries(), this ignores recent updates —
    it flags entries that have been unresolved for a long time even
    if they're being actively managed.
    """
    open_entries = find_open_entries(entries)
    if not open_entries:
        return []

    now = datetime.now(timezone.utc)
    long_open: list[FailureEntry] = []
    for entry in open_entries:
        opened = _parse_iso_date(entry.date_opened)
        if opened is None:
            # No date = assume long-open
            long_open.append(entry)
            continue
        if (now - opened).days >= long_open_days:
            long_open.append(entry)

    return long_open


def entry_to_markdown(entry: FailureEntry) -> str:
    """Render a single entry as markdown block."""
    lines = [
        f"### {entry.failure_id}: {entry.title}",
        f"",
        f"**Status:** {entry.status}",
        f"**Date Opened:** {entry.date_opened}",
        f"**Category:** {entry.category}",
        f"**Description:** {entry.description}",
    ]
    if entry.root_cause:
        lines.append(f"**Root Cause:** {entry.root_cause}")
    if entry.evidence:
        lines.append(f"**Evidence:**")
        for ev in entry.evidence:
            lines.append(f"- {ev}")
    if entry.resolution:
        lines.append(f"**Resolution:** {entry.resolution}")
    if entry.date_resolved:
        lines.append(f"**Date Resolved:** {entry.date_resolved}")
    if entry.related_ids:
        lines.append(f"**Related:** {', '.join(entry.related_ids)}")
    lines.append("")
    return "\n".join(lines)


def append_entry(
    entry: FailureEntry,
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> str:
    """Append a new entry to the failure log. Returns the markdown text.

    In dry_run mode, returns the text without writing to disk.
    """
    path = path or CANONICAL_FAILURE_LOG
    md = entry_to_markdown(entry)

    if dry_run:
        return md

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        header = "# Failure Log\n\nCanonical append-only failure tracking.\n\n"
        path.write_text(header + md, encoding="utf-8")
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + md)

    return md


def update_status(
    failure_id: str,
    new_status: str,
    resolution: str = "",
    evidence_refs: tuple[str, ...] = (),
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> bool:
    """Record a status change by appending a subsection (never edits prior lines).

    Enforces:
    - new_status must be in VALID_STATUS
    - RESOLVED requires non-empty resolution AND at least one evidence ref
    - Append-only: original status line is preserved, update is appended
    """
    if new_status not in VALID_STATUS:
        raise ValueError(f"Invalid status {new_status!r}")

    if new_status in TERMINAL_STATUS and not resolution:
        raise ValueError(f"{new_status} status requires a resolution description")

    if new_status in TERMINAL_STATUS and not evidence_refs:
        raise ValueError(f"{new_status} status requires at least one evidence ref")

    path = path or CANONICAL_FAILURE_LOG
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")

    # Find this entry's block to locate where to append the update
    entry_header = re.compile(
        rf"^###\s+{re.escape(failure_id)}\s*[:\-—]",
        re.MULTILINE,
    )
    match = entry_header.search(content)
    if not match:
        return False

    # Determine the current effective status for the from->to label.
    # Check for append-only status updates first (most recent wins),
    # then fall back to the original **Status:** line.
    block_start = match.start()
    next_hdr = re.search(r"^### ", content[match.end():], re.MULTILINE)
    block_end = match.end() + next_hdr.start() if next_hdr else len(content)
    block_text = content[block_start:block_end]

    _STATUS_UPDATE_RE_LOCAL = re.compile(
        r">\s*\*{0,2}\[\d{4}-\d{2}-\d{2}\]\s*Status update:\s*\w+\s*->\s*(\w+)\*{0,2}",
    )
    update_matches = list(_STATUS_UPDATE_RE_LOCAL.finditer(block_text))
    if update_matches:
        old_status = update_matches[-1].group(1)  # last update's target
    else:
        status_in_block = _FL_STATUS_RE.search(block_text)
        old_status = _normalize_status_value(status_in_block.group(1)) if status_in_block else "?"

    if dry_run:
        return True

    # Reuse block_end computed above for the insert point
    insert_point = block_end

    # Build append-only status update subsection
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    update_lines = [
        f"\n> **[{date_str}] Status update: {old_status} -> {new_status}**",
    ]
    if resolution:
        update_lines.append(f"> {resolution}")
    for ref in evidence_refs:
        update_lines.append(f"> Evidence: {ref}")
    update_lines.append("")

    update_text = "\n".join(update_lines)

    new_content = (
        content[:insert_point].rstrip("\n")
        + "\n"
        + update_text
        + "\n"
        + content[insert_point:]
    )

    path.write_text(new_content, encoding="utf-8")
    return True
