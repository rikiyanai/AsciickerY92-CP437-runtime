"""Shared FL overlay normalization utilities (FL-1825 / RQ-054).

This module holds the canonical implementation of FL overlay helper functions
that are used by ``scripts/maintainer/lib/failure_log.py`` and the native
failure-log front door. Consolidating here prevents consumers from diverging
silently.

Consolidated helpers:
  _normalize_overlay_list_value  — normalize any overlay field that must be a list[str]
  _derive_default_fl_area        — shared default-area derivation with optional entry callback
  _effective_overlay_record      — shared overlay-record normalization with caller-provided
                                   inferred defaults
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from .fl_schema import FL_OVERLAY_LIST_FIELDS, FL_OVERLAY_AREA_VALUES


def normalize_overlay_list_value(value: object) -> list[str]:
    """Return *value* as a clean list of non-empty strings.

    Accepts a single string (wraps in list), a list/tuple of values, or None.
    Returns an empty list for any other type.

    This is the canonical shared implementation; both failure_log.py and
    failure-log callers import this rather than maintaining separate copies.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def derive_default_fl_area(
    *,
    category: str = "",
    entry: object = None,
    infer_from_entry: Callable[[object], str] | None = None,
) -> str:
    """Derive a default overlay area from either category text or an entry callback."""
    if infer_from_entry is not None:
        inferred = infer_from_entry(entry)
        return inferred.strip() if isinstance(inferred, str) else ""
    for part in re.split(r"[|/,]+", category or ""):
        normalized = re.sub(r"[^a-z0-9]+", "_", part.strip().lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if normalized in FL_OVERLAY_AREA_VALUES:
            return normalized
    return ""


def effective_overlay_record(
    fl_id: str,
    *,
    overlay: Mapping[str, object] | None = None,
    default_category: str = "",
    default_priority: str = "",
    default_area: str = "",
    default_kinds: Sequence[str] | None = None,
    default_proof_state: str = "OPEN",
    default_epoch_status: str = "",
    default_complaint_counter_state: str = "",
    placeholder_overlay: bool = False,
    normalize_fix_attempts: Callable[[object], list[dict[str, str]]] | None = None,
    default_fix_attempts: Sequence[Mapping[str, str]] | None = None,
    default_last_tested_run_id: str = "",
    default_last_tested_date: str = "",
    default_last_gate_result: str = "",
) -> dict:
    """Build a normalized overlay record from shared defaults plus optional overlay."""
    kinds_default = [str(item).strip() for item in (default_kinds or []) if str(item).strip()]
    record = {
        "fl": fl_id,
        "Category": default_category,
        "Priority": default_priority,
        "Area": default_area,
        "Subsystems": [],
        "Kinds": kinds_default or ["unclassified"],
        "ProofState": default_proof_state or "OPEN",
        "EpochStatus": default_epoch_status,
        "ComplaintRefs": [],
        "RQRefs": [],
        "GitHubRefs": [],
        "ComplaintCounterState": default_complaint_counter_state,
        "CodeRefs": [],
        "TouchedFiles": [],
        "AnchorFunctions": [],
    }
    include_history_fields = any(
        (
            normalize_fix_attempts is not None,
            default_fix_attempts,
            default_last_tested_run_id,
            default_last_tested_date,
            default_last_gate_result,
            isinstance(overlay, Mapping)
            and any(key in overlay for key in ("FixAttempts", "LastTestedRunId", "LastTestedDate", "LastGateResult")),
        )
    )
    if include_history_fields:
        record.update(
            {
                "LastTestedRunId": "",
                "LastTestedDate": "",
                "LastGateResult": "",
                "FixAttempts": [],
            }
        )
    if isinstance(overlay, Mapping):
        record.update(dict(overlay))
    for key in FL_OVERLAY_LIST_FIELDS:
        record[key] = normalize_overlay_list_value(record.get(key))
    for key in ("Category", "Priority", "Area", "ProofState", "EpochStatus", "ComplaintCounterState"):
        value = record.get(key)
        record[key] = str(value).strip() if isinstance(value, str) else ""
    if record["Priority"]:
        record["Priority"] = record["Priority"].upper()
    for key in ("LastTestedRunId", "LastTestedDate", "LastGateResult"):
        if key not in record:
            continue
        value = record.get(key)
        record[key] = str(value).strip() if isinstance(value, str) else ""
    if normalize_fix_attempts is not None:
        record["FixAttempts"] = normalize_fix_attempts(record.get("FixAttempts"))

    if not record["Category"]:
        record["Category"] = default_category
    if not record["Priority"]:
        record["Priority"] = default_priority
    if (not overlay or placeholder_overlay) and default_area:
        record["Area"] = default_area
    if not record["Area"]:
        record["Area"] = default_area
    if (not overlay or placeholder_overlay) and kinds_default:
        record["Kinds"] = list(kinds_default)
    if not record["Kinds"]:
        record["Kinds"] = list(kinds_default) or ["unclassified"]
    overlay_proof = str((overlay or {}).get("ProofState") or "").strip() if isinstance(overlay, Mapping) else ""
    if (not overlay or placeholder_overlay) and default_proof_state and not overlay_proof:
        record["ProofState"] = default_proof_state
    if not record["ProofState"]:
        record["ProofState"] = default_proof_state or "OPEN"
    if not record["EpochStatus"]:
        record["EpochStatus"] = default_epoch_status
    if not record["ComplaintCounterState"]:
        record["ComplaintCounterState"] = default_complaint_counter_state

    if "LastTestedRunId" in record and not record["LastTestedRunId"]:
        record["LastTestedRunId"] = default_last_tested_run_id
    if "LastTestedDate" in record and not record["LastTestedDate"]:
        record["LastTestedDate"] = default_last_tested_date
    if "LastGateResult" in record and not record["LastGateResult"]:
        record["LastGateResult"] = default_last_gate_result
    if "FixAttempts" in record and not record["FixAttempts"]:
        record["FixAttempts"] = [
            {
                "date": str(item.get("date") or "").strip(),
                "branch": str(item.get("branch") or "").strip(),
                "suffix": str(item.get("suffix") or "").strip(),
            }
            for item in (default_fix_attempts or [])
            if isinstance(item, Mapping)
        ]
    if not record.get("fl"):
        record["fl"] = fl_id
    return record


# Private alias for callers that import the underscore-prefixed name directly.
_normalize_overlay_list_value = normalize_overlay_list_value
_derive_default_fl_area = derive_default_fl_area
_effective_overlay_record = effective_overlay_record
