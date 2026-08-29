"""RestoreabilityContract — front-door deploy contract with HMAC integrity.

Phase 2: Extracted from build_front_door_restoreability_contract() (line 6975).
Adds HMAC verification to prevent tampering with the contract JSON between
wrapper write and child read (e.g., reset_candidate_runtime.py).

The HMAC key comes from ``AK_DEPLOY_HMAC_KEY``.  In deployment contexts
this key is required; ``sign()`` raises if absent.  On developer workstations
``verify()`` returns ``UNVERIFIABLE`` rather than falsely passing.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

CONTRACT_HMAC_KEY_ENV = "AK_DEPLOY_HMAC_KEY"


class VerifyResult(enum.Enum):
    """Three-state HMAC verification outcome (FL-3862 H1)."""
    VERIFIED = "verified"          # HMAC present and matches
    UNVERIFIABLE = "unverifiable"  # HMAC present but key unavailable
    TAMPERED = "tampered"          # HMAC present and mismatch
    NONE = "none"                  # No HMAC present


def _safe_tuple(value: Any) -> tuple[str, ...]:
    """Convert a list/tuple to tuple; guard against strings iterating char-by-char."""
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


class ContractIntegrityError(ValueError):
    """HMAC verification failed — contract may have been tampered with."""


@dataclass(frozen=True)
class RestoreabilityContract:
    """Immutable front-door contract that proves deploy source identity.

    Written to ``run_dir/front_door_restoreability_contract.json``.
    Read by child scripts (reset_candidate_runtime.py, verify_candidate_web.py)
    to confirm the deployed source matches what the wrapper committed to.

    Fields match the current ``build_front_door_restoreability_contract()`` dict:
    """

    # Identity
    format_version: int = 1
    owner: str = "scripts/watchdog_run_canonical.py"

    # Run identity
    run_id: str = ""
    effective_mode: str = ""
    effective_target: str = ""

    # Source coordinates
    source_ref: str = ""  # short SHA
    source_ref_full: str = ""  # full SHA

    # Dirty state
    dirty_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()

    # Restoreability verdict
    restoreable_by_commit: bool = False
    restoreability_reason: str = ""

    # Recovery decisions
    accepted_recovery_mode: str = "none"
    accepted_untracked_policy: str = "not_applicable"
    accepted_ignored_policy: str = "not_applicable"

    # Run intent / diagnostic scope
    run_intent_diff: dict[str, Any] | None = None
    diagnostic_scope: dict[str, Any] | None = None

    # Build inputs
    local_build_inputs: dict[str, str] = field(default_factory=dict)

    # Integrity
    emitted_at: str = ""  # ISO timestamp
    _hmac: str = ""  # SHA-256 HMAC of the above fields

    # --- Construction ---

    @classmethod
    def from_build_front_door(
        cls,
        *,
        run_id: str,
        effective_mode: str,
        effective_target: str,
        head_short: str,
        head_full: str,
        status_lines: list[str],
        commit_all_reset_recovery: dict[str, Any],
        pre_run_doc_commit: dict[str, Any],
        run_intent_diff: dict[str, Any] | None,
        diagnostic_scope: dict[str, Any] | None,
        build_inputs: dict[str, Any],
        allow_dirty_snapshot: bool = False,
    ) -> "RestoreabilityContract":
        """Construct from the same inputs as build_front_door_restoreability_contract().

        This mirrors the current dict construction exactly so we can swap the
        implementation without changing contract contents.
        """
        from watchdog.source.dirty_tree import tracked_status_paths, untracked_status_paths

        recovery_mode = "none"
        if commit_all_reset_recovery.get("committed"):
            recovery_mode = "commit_all_and_reset"
        elif pre_run_doc_commit.get("committed"):
            recovery_mode = "pre_run_doc_auto_commit"
        elif allow_dirty_snapshot and status_lines:
            recovery_mode = "dirty_snapshot"

        accepted_untracked_policy = "not_applicable"
        if commit_all_reset_recovery.get("enabled"):
            accepted_untracked_policy = (
                "included"
                if commit_all_reset_recovery.get("include_untracked")
                else "excluded"
            )

        accepted_ignored_policy = "not_applicable"
        if commit_all_reset_recovery.get("enabled"):
            accepted_ignored_policy = (
                "force_added"
                if commit_all_reset_recovery.get("force_ignored")
                else "excluded"
            )

        tracked_paths = sorted(tracked_status_paths(status_lines))
        untracked_paths_list = sorted(untracked_status_paths(status_lines))

        contract = cls(
            format_version=1,
            run_id=run_id,
            effective_mode=effective_mode,
            effective_target=effective_target,
            source_ref=head_short,
            source_ref_full=head_full,
            dirty_paths=tuple(tracked_paths),
            untracked_paths=tuple(untracked_paths_list),
            restoreable_by_commit=not status_lines,
            restoreability_reason="clean" if not status_lines else "git_worktree_dirty",
            accepted_recovery_mode=recovery_mode,
            accepted_untracked_policy=accepted_untracked_policy,
            accepted_ignored_policy=accepted_ignored_policy,
            run_intent_diff=run_intent_diff,
            diagnostic_scope=diagnostic_scope,
            local_build_inputs={
                "sha256": build_inputs.get("sha256", ""),
                "file_count": str(build_inputs.get("file_count", "")),
            },
            emitted_at=datetime.now(timezone.utc).isoformat(),
        )
        return contract

    # --- Dict conversion (backward compat) ---

    def to_dict(self) -> dict[str, Any]:
        """Convert to the same dict format as build_front_door_restoreability_contract().

        This ensures child scripts that read the dict directly still work.
        """
        return {
            "format_version": self.format_version,
            "owner": self.owner,
            "run_id": self.run_id,
            "effective_mode": self.effective_mode,
            "effective_target": self.effective_target,
            "source_ref": self.source_ref,
            "source_ref_full": self.source_ref_full,
            "dirty_paths": list(self.dirty_paths),
            "untracked_paths": list(self.untracked_paths),
            "restoreability": {
                "restoreable_by_commit": self.restoreable_by_commit,
                "reason": self.restoreability_reason,
            },
            "accepted_recovery_mode": self.accepted_recovery_mode,
            "accepted_untracked_policy": self.accepted_untracked_policy,
            "accepted_ignored_policy": self.accepted_ignored_policy,
            "run_intent_diff": self.run_intent_diff,
            "diagnostic_scope": self.diagnostic_scope,
            "local_build_inputs": self.local_build_inputs,
            "emitted_at": self.emitted_at,
            "_hmac": self._hmac,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RestoreabilityContract":
        """Convert from the dict format produced by build_front_door_restoreability_contract()."""
        restoreability = data.get("restoreability", {}) or {}
        local_inputs = data.get("local_build_inputs", {}) or {}
        try:
            fmt_ver = int(data.get("format_version", 1))
        except (ValueError, TypeError):
            fmt_ver = 1
        return cls(
            format_version=fmt_ver,
            owner=str(data.get("owner", "")),
            run_id=str(data.get("run_id", "")),
            effective_mode=str(data.get("effective_mode", "")),
            effective_target=str(data.get("effective_target", "")),
            source_ref=str(data.get("source_ref", "")),
            source_ref_full=str(data.get("source_ref_full", "")),
            dirty_paths=_safe_tuple(data.get("dirty_paths", [])),
            untracked_paths=_safe_tuple(data.get("untracked_paths", [])),
            restoreable_by_commit=bool(restoreability.get("restoreable_by_commit", False)),
            restoreability_reason=str(restoreability.get("reason", "")),
            accepted_recovery_mode=str(data.get("accepted_recovery_mode", "none")),
            accepted_untracked_policy=str(data.get("accepted_untracked_policy", "not_applicable")),
            accepted_ignored_policy=str(data.get("accepted_ignored_policy", "not_applicable")),
            run_intent_diff=data.get("run_intent_diff"),
            diagnostic_scope=data.get("diagnostic_scope"),
            local_build_inputs={str(k): str(v) for k, v in local_inputs.items()},
            emitted_at=str(data.get("emitted_at", "")),
            _hmac=str(data.get("_hmac", "")),
        )

    # --- HMAC integrity ---

    def _payload_for_hmac(self) -> str:
        """Serialize all non-HMAC fields for integrity verification."""
        payload = self.to_dict()
        payload.pop("_hmac", None)
        return json.dumps(payload, sort_keys=True)

    def sign(self, key: str | None = None) -> "RestoreabilityContract":
        """Return a new contract with HMAC signed.

        If key is None, reads AK_DEPLOY_HMAC_KEY from env.
        Raises RuntimeError if no key is available (fail-closed — FL-3862 H3).
        In deploy contexts a missing key means integrity cannot be guaranteed.
        """
        hmac_key = key or os.environ.get(CONTRACT_HMAC_KEY_ENV)
        if not hmac_key:
            raise RuntimeError(
                f"{CONTRACT_HMAC_KEY_ENV} not set — cannot sign "
                f"RestoreabilityContract.  Set this env var in deploy contexts."
            )

        payload = self._payload_for_hmac()
        computed = hmac.new(
            hmac_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return replace(self, _hmac=computed)

    def verify(self, key: str | None = None) -> VerifyResult:
        """Verify HMAC integrity. Returns three-state result (FL-3862 H1).

        - ``VERIFIED``: HMAC present and matches
        - ``UNVERIFIABLE``: HMAC present but key unavailable (cannot check)
        - ``TAMPERED``: HMAC present and mismatch (ContractIntegrityError raised)
        - ``NONE``: No HMAC present

        Raises ContractIntegrityError when result is TAMPERED.
        """
        if not self._hmac:
            _logger.debug("contract has no HMAC — integrity not verified")
            return VerifyResult.NONE

        hmac_key = key or os.environ.get(CONTRACT_HMAC_KEY_ENV)
        if not hmac_key:
            _logger.warning(
                "%s not set — cannot verify contract HMAC integrity; "
                "treating as UNVERIFIABLE (not VERIFIED)",
                CONTRACT_HMAC_KEY_ENV,
            )
            return VerifyResult.UNVERIFIABLE  # Not True — cannot check

        payload = self._payload_for_hmac()
        expected = hmac.new(
            hmac_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, self._hmac):
            raise ContractIntegrityError(
                f"RestoreabilityContract HMAC mismatch — contract may have been tampered with"
            )
        return VerifyResult.VERIFIED

    # --- I/O ---

    def write(self, path: Path) -> Path:
        """Write the contract to path as JSON (with HMAC if signed)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(json.dumps(self.to_dict(), indent=2) + "\n")
        os.replace(tmp_path, path)
        return path

    @classmethod
    def load(
        cls,
        path: Path,
        verify_hmac: bool = True,
        hmac_key: str | None = None,
    ) -> "RestoreabilityContract":
        """Load contract from JSON, optionally verifying HMAC.

        Raises ContractIntegrityError on HMAC mismatch.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractIntegrityError(
                f"RestoreabilityContract unreadable: {path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ContractIntegrityError(
                f"RestoreabilityContract must be a JSON object: {path}"
            )

        contract = cls.from_dict(data)
        if verify_hmac:
            result = contract.verify(key=hmac_key)
            if result == VerifyResult.UNVERIFIABLE:
                _logger.warning(
                    "RestoreabilityContract loaded with unverifiable HMAC: %s",
                    path,
                )
            elif result == VerifyResult.NONE:
                _logger.info(
                    "RestoreabilityContract loaded without HMAC: %s",
                    path,
                )
        return contract
