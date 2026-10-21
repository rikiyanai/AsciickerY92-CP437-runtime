#!/usr/bin/env python3
"""FL History Transcript-Mining Ledger.

Single-source-of-truth state file for the paired Pi-skill suite described in
``docs/plans/2026-05-16-001-feat-fl-history-mining-skills-plan.md``. Used by
the orchestrator skill (frequent writes) and the finalize script (validation
reads). Miners do NOT import this module — they update their own rows inline
via ``python -c`` recipes per KD1 platform-cleanliness rule.

Ledger format: JSONL, one row per line.
- ``type: fl_row`` rows track per-FL-id state through the lifecycle.
- ``type: extension_request`` rows track cluster-extension claims pending
  orchestrator adjudication.

Atomic write recipe: ``fcntl.flock`` on a sidecar ``.lock`` file across the
read-modify-write cycle, then temp-file + ``fsync`` + ``os.replace`` for the
data file. Readers tolerate truncated-tail lines from a writer that died
mid-write (per origin R21).

Run-style:

    from fl_mining_ledger import Ledger
    led = Ledger("docs/audits/2026-05-16-fl-history-mining/ledger.jsonl")
    led.write_row({"type": "fl_row", "fl_id": "FL-644", ...})
    led.transition("FL-644", "pending", "claimed", cluster_id="CL-001", ...)
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Lock acquisition timeout, in seconds. A stuck miner holding the lock should not
# halt the entire pilot indefinitely (P1 #8 from plan-39fa929c code review).
# Override via FL_MINING_LOCK_TIMEOUT_SEC env var if pilot reveals 60s is wrong.
_LOCK_TIMEOUT_SEC = float(os.environ.get("FL_MINING_LOCK_TIMEOUT_SEC", "60"))
_LOCK_RETRY_SLEEP_SEC = 0.1


_VALID_TRANSITIONS: set[tuple[str, str]] = {
    ("pending", "claimed"),
    ("claimed", "mining"),
    ("mining", "awaiting_verify"),
    ("mining", "claimed-pool"),
    ("awaiting_verify", "verified"),
    ("awaiting_verify", "claimed-pool"),
    ("awaiting_verify", "needs_human_review"),
    ("claimed-pool", "claimed"),
    ("claimed-pool", "needs_human_review"),
}


_REQUIRED_FIELDS: dict[str, set[str]] = {
    "pending": {"fl_id", "source_file", "status", "last_update"},
    "claimed": {
        "fl_id", "source_file", "status", "last_update",
        "cluster_id", "claimed_by_pane", "claimed_at",
    },
    "mining": {
        "fl_id", "source_file", "status", "last_update",
        "cluster_id", "claimed_by_pane", "claimed_at", "miner_session_jsonl",
    },
    "awaiting_verify": {
        "fl_id", "source_file", "status", "last_update",
        "cluster_id", "claimed_by_pane", "claimed_at",
        "miner_session_jsonl", "annotation_path",
    },
    "verified": {
        "fl_id", "source_file", "status", "last_update",
        "cluster_id", "claimed_by_pane", "claimed_at",
        "miner_session_jsonl", "annotation_path", "verify_result",
    },
    "needs_human_review": {
        "fl_id", "source_file", "status", "last_update",
        "cluster_id", "verify_result",
    },
    "claimed-pool": {"fl_id", "source_file", "status", "last_update", "cluster_id"},
}
# Note: ``extension_denied`` is NOT an fl_row status. It appears only as the
# ``resolution`` field value on extension_request rows. The fl_row whose extension
# was denied stays in its current status (typically ``pending``). Dead-status
# removal landed in plan-39fa929c code review (anchor 100, multi-reviewer).


_EXTENSION_REQUEST_FIELDS = {
    "request_id", "requested_at", "requesting_pane",
    "current_cluster_id", "target_fl_id",
}

# Optional fl_row fields that may appear on any status without failing validation.
# ``cass_snapshot_count`` is the count of cass hits at orchestrator dispatch time;
# used by Step 4f honest-negative re-verification to avoid self-poisoning as the
# pilot accumulates new sessions (P1 #10 from plan-39fa929c code review).
_OPTIONAL_FL_ROW_FIELDS = {"cass_snapshot_count", "reject_count", "extension_of"}


class LedgerTransitionError(ValueError):
    """Raised when a status transition is rejected by the state-machine."""

    def __init__(self, fl_id: str, from_status: str, to_status: str, reason: str = ""):
        self.fl_id = fl_id
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason
        msg = f"FL {fl_id}: invalid transition {from_status!r} -> {to_status!r}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class LedgerSchemaError(ValueError):
    """Raised when a row fails required-fields validation."""


class LedgerLockTimeoutError(TimeoutError):
    """Raised when the ledger lock cannot be acquired within ``_LOCK_TIMEOUT_SEC``.
    Includes the holder identity recorded in the sidecar ``.lock.meta`` file."""

    def __init__(self, lock_path: Path, holder_info: str):
        self.lock_path = lock_path
        self.holder_info = holder_info
        super().__init__(
            f"failed to acquire {lock_path} within {_LOCK_TIMEOUT_SEC}s; "
            f"current holder: {holder_info}"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Ledger:
    """JSONL ledger with atomic writes + state-machine transition validation."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.lock_path = Path(str(self.path) + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── locking ──────────────────────────────────────────────────────

    @property
    def _meta_path(self) -> Path:
        return Path(str(self.lock_path) + ".meta")

    def _read_holder_info(self) -> str:
        try:
            return self._meta_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return "unknown (no .lock.meta file)"

    def _write_holder_info(self) -> None:
        info = f"pid={os.getpid()} host={socket.gethostname()} acquired_at={_now_iso()}"
        try:
            self._meta_path.write_text(info, encoding="utf-8")
        except OSError:
            pass  # best-effort observability; don't fail the lock acquisition

    def _lock(self):
        """Acquire ledger lock with timeout. Closes the fd on failure so the
        descriptor doesn't leak in long-running orchestrators (P1 reliability)."""
        lf = open(self.lock_path, "a+")
        deadline = time.monotonic() + _LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError) as exc:
                if isinstance(exc, OSError) and exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    lf.close()
                    raise
                if time.monotonic() >= deadline:
                    holder = self._read_holder_info()
                    lf.close()
                    raise LedgerLockTimeoutError(self.lock_path, holder) from None
                time.sleep(_LOCK_RETRY_SLEEP_SEC)
        try:
            self._write_holder_info()
        except Exception:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            lf.close()
            raise
        return lf

    def _unlock(self, lf) -> None:
        try:
            try:
                self._meta_path.unlink()
            except FileNotFoundError:
                pass
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        finally:
            lf.close()

    # ── raw I/O (must be called under lock) ──────────────────────────

    def _read_locked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, 1):
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    print(
                        f"[fl_mining_ledger] warn: skipping malformed line "
                        f"{line_no} in {self.path}",
                        file=sys.stderr,
                    )
                    continue
        return rows

    def _write_locked(self, rows: list[dict[str, Any]]) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".ledger-",
            suffix=".jsonl.tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    # ── public reads ─────────────────────────────────────────────────

    def read_all(self) -> list[dict[str, Any]]:
        lf = self._lock()
        try:
            return self._read_locked()
        finally:
            self._unlock(lf)

    def read_row(self, fl_id: str) -> dict[str, Any] | None:
        for row in self.read_all():
            if row.get("type") == "fl_row" and row.get("fl_id") == fl_id:
                return row
        return None

    # ── public writes ────────────────────────────────────────────────

    def write_row(self, row: dict[str, Any]) -> None:
        """Idempotent upsert by row key (fl_id for fl_row, request_id for extension_request)."""
        self._validate_row(row)
        row = dict(row)
        row["last_update"] = _now_iso()
        lf = self._lock()
        try:
            rows = self._read_locked()
            key = self._row_key(row)
            for i, existing in enumerate(rows):
                if self._row_key(existing) == key:
                    rows[i] = row
                    break
            else:
                rows.append(row)
            self._write_locked(rows)
        finally:
            self._unlock(lf)

    def transition(
        self,
        fl_id: str,
        from_status: str,
        to_status: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """Validate transition + update row atomically. Returns updated row."""
        if (from_status, to_status) not in _VALID_TRANSITIONS:
            raise LedgerTransitionError(
                fl_id, from_status, to_status,
                "not in allowed transitions table",
            )

        lf = self._lock()
        try:
            rows = self._read_locked()
            target_idx: int | None = None
            for i, r in enumerate(rows):
                if r.get("type") == "fl_row" and r.get("fl_id") == fl_id:
                    target_idx = i
                    break
            if target_idx is None:
                raise LedgerTransitionError(
                    fl_id, from_status, to_status, "row not found",
                )
            target = rows[target_idx]
            if target.get("status") != from_status:
                raise LedgerTransitionError(
                    fl_id, from_status, to_status,
                    f"actual status is {target.get('status')!r}",
                )

            updated = dict(target)
            updated["status"] = to_status
            updated["last_update"] = _now_iso()
            for k, v in fields.items():
                updated[k] = v

            # reject path bookkeeping
            if to_status == "claimed-pool" and from_status == "awaiting_verify":
                updated["reject_count"] = updated.get("reject_count", 0) + 1
                if updated["reject_count"] >= 2:
                    raise LedgerTransitionError(
                        fl_id, from_status, to_status,
                        f"reject_count would reach {updated['reject_count']}; "
                        f"use needs_human_review instead",
                    )
                for k in (
                    "claimed_by_pane", "claimed_at",
                    "miner_session_jsonl", "annotation_path",
                ):
                    updated.pop(k, None)
            elif to_status == "claimed-pool" and from_status == "mining":
                # stuck-miner recovery — DO NOT increment reject_count
                for k in (
                    "claimed_by_pane", "claimed_at",
                    "miner_session_jsonl", "annotation_path",
                ):
                    updated.pop(k, None)
            elif to_status == "needs_human_review" and from_status == "awaiting_verify":
                updated["reject_count"] = updated.get("reject_count", 0) + 1

            self._validate_row(updated)
            rows[target_idx] = updated
            self._write_locked(rows)
            return updated
        finally:
            self._unlock(lf)

    # ── extensions ───────────────────────────────────────────────────

    def request_extension(
        self,
        requesting_pane: str,
        current_cluster_id: str,
        target_fl_id: str,
        rationale: str = "",
    ) -> str:
        """Append a pending extension_request row. Returns the request_id.
        The request_id includes a uuid4 hex suffix so two requests from the same
        pane within the same microsecond do not collide (P1 #7 from review)."""
        now = _now_iso()
        request_id = f"EXT-{now}-{requesting_pane}-{uuid.uuid4().hex[:8]}"
        row = {
            "type": "extension_request",
            "request_id": request_id,
            "requested_at": now,
            "requesting_pane": requesting_pane,
            "current_cluster_id": current_cluster_id,
            "target_fl_id": target_fl_id,
            "rationale": rationale,
            "resolution": None,
        }
        self.write_row(row)
        return request_id

    def resolve_extensions(self) -> list[dict[str, Any]]:
        """Adjudicate pending extension_requests. First-by-timestamp wins per target.

        Returns a list of resolution records. The ledger is updated: target
        fl_row rows get the winner's cluster_id, loser requests get
        ``resolution: extension_denied_taken``, winner requests get
        ``resolution: approved``.
        """
        lf = self._lock()
        try:
            rows = self._read_locked()
            pending_by_target: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                if r.get("type") != "extension_request":
                    continue
                if r.get("resolution") is not None:
                    continue
                pending_by_target.setdefault(r.get("target_fl_id"), []).append(r)

            resolutions: list[dict[str, Any]] = []
            now = _now_iso()
            for target_id, reqs in pending_by_target.items():
                target_row = next(
                    (r for r in rows if r.get("type") == "fl_row" and r.get("fl_id") == target_id),
                    None,
                )
                reqs.sort(key=lambda r: r.get("requested_at", ""))

                if target_row is None or target_row.get("status") != "pending":
                    for req in reqs:
                        req["resolution"] = "extension_denied_taken"
                        resolutions.append({
                            "request_id": req["request_id"],
                            "verdict": "denied",
                            "reason": "target not pending or missing",
                        })
                    continue

                winner = reqs[0]
                target_row["cluster_id"] = winner["current_cluster_id"]
                target_row["extension_of"] = winner["request_id"]
                target_row["last_update"] = now
                winner["resolution"] = "approved"
                resolutions.append({
                    "request_id": winner["request_id"],
                    "verdict": "approved",
                    "target_fl_id": target_id,
                })
                for loser in reqs[1:]:
                    loser["resolution"] = "extension_denied_taken"
                    resolutions.append({
                        "request_id": loser["request_id"],
                        "verdict": "denied",
                        "reason": "lost to earlier request",
                    })

            if resolutions:
                self._write_locked(rows)
            return resolutions
        finally:
            self._unlock(lf)

    # ── summary ──────────────────────────────────────────────────────

    def summarize(self) -> dict[str, int]:
        """Return ``{status: count}`` for fl_row rows."""
        counts: dict[str, int] = {}
        for row in self.read_all():
            if row.get("type") != "fl_row":
                continue
            status = row.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    # ── internals ────────────────────────────────────────────────────

    @staticmethod
    def _row_key(row: dict[str, Any]) -> tuple[str, str]:
        rtype = row.get("type")
        if rtype == "fl_row":
            return ("fl_row", str(row.get("fl_id")))
        if rtype == "extension_request":
            return ("extension_request", str(row.get("request_id")))
        return ("unknown", str(id(row)))

    @staticmethod
    def _validate_row(row: dict[str, Any]) -> None:
        if not isinstance(row, dict):
            raise LedgerSchemaError(
                f"row must be dict, got {type(row).__name__}",
            )
        rtype = row.get("type")
        if rtype == "fl_row":
            status = row.get("status")
            if status not in _REQUIRED_FIELDS:
                raise LedgerSchemaError(f"unknown status {status!r}")
            missing = _REQUIRED_FIELDS[status] - set(row.keys())
            if missing:
                raise LedgerSchemaError(
                    f"FL {row.get('fl_id')!r} status {status!r}: "
                    f"missing required fields {sorted(missing)}",
                )
        elif rtype == "extension_request":
            missing = _EXTENSION_REQUEST_FIELDS - set(row.keys())
            if missing:
                raise LedgerSchemaError(
                    f"extension_request: missing required fields {sorted(missing)}",
                )
        else:
            raise LedgerSchemaError(f"unknown row type {rtype!r}")


if __name__ == "__main__":
    # operator quick-look: python3 scripts/fl_mining_ledger.py <ledger.jsonl>
    if len(sys.argv) < 2:
        print("usage: fl_mining_ledger.py <ledger.jsonl>", file=sys.stderr)
        sys.exit(2)
    led = Ledger(sys.argv[1])
    print(json.dumps(led.summarize(), indent=2, sort_keys=True))
