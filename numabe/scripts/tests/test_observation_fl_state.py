"""Synthetic, temp-only tests for the observation FL-state adapter."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.maintainer.lib.observation_fl_state import (
    SCHEMA_VERSION,
    validate_json_file,
    validate_receipt,
    validate_records,
)


def _base_receipt() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "fl_id": "FL-147",
        "status": "OPEN",
        "checks": [
            "spent-lane",
            "parked-lane",
            "requirements",
            "retrieval",
            "attempt-order",
            "status",
            "warning-comments",
            "deletion",
            "directives",
        ],
        "attempts": [
            {
                "id": "A1",
                "kind": "attempt",
                "recorded_at": "2026-09-29T12:00:00Z",
                "outcome": "FAILED",
                "counter": 1,
                "receipt_ref": "obs://A1",
            },
            {
                "id": "A2",
                "kind": "successor",
                "recorded_at": "2026-09-29T12:01:00Z",
                "outcome": "PENDING",
                "counter": 2,
                "fix_attempt_ref": "obs://A2",
            },
        ],
        "spent_lanes": [{"id": "A1", "mechanism": "edit-hook"}],
        "parked_lanes": [{"id": "P1", "mechanism": "retired-launch-hook"}],
        "proposal": {
            "mechanism": "edit-hook",
            "differs_from_spent": True,
            "difference": "receipt-only adapter with no live hook installation",
        },
        "requirements": [
            {"id": "REQ-32", "required": True},
            {"id": "REQ-33", "required": True},
        ],
        "requirements_receipt": {
            "covered_ids": ["REQ-32", "REQ-33"],
            "source": "archived observation bodies",
            "receipt_ref": "obs://0105",
            "captured_at": "2026-09-30T12:00:00Z",
        },
        "retrieval": {
            "source": "bounded archived-body scan",
            "window_start": "2026-09-27T00:00:00Z",
            "window_end": "2026-09-30T12:00:00Z",
            "files_scanned": 7,
            "records_scanned": 7,
            "first_timestamp": "2026-09-27T00:00:00Z",
            "last_timestamp": "2026-09-30T12:00:00Z",
            "baseline_id": "baseline-2026-09-30",
            "measured_at": "2026-09-30T12:00:00Z",
            "coverage_complete": True,
            "baseline_current": True,
        },
        "warning_comments": {
            "inventory": ["W1"],
            "removed": [],
            "moved": [],
            "retained": [{"id": "W1", "reason": "still actionable", "evidence": "obs://W1"}],
            "added": [],
        },
        "deletion": {
            "targets": [
                {
                    "id": "T1",
                    "path": "scripts/old_gate.py",
                    "symbol": "old_gate",
                    "present": False,
                    "callers_present": False,
                    "stub_present": False,
                    "archive_present": False,
                    "quarantine_present": False,
                }
            ],
            "revivals": [],
        },
        "directives": [
            {
                "id": "D1",
                "router": True,
                "guard": True,
                "preflight": True,
                "evidence": ["obs://D1/router", "obs://D1/guard", "obs://D1/preflight"],
            }
        ],
    }


def test_complete_explicit_receipt_passes_all_gates() -> None:
    report = validate_receipt(_base_receipt())
    assert report.ok
    assert report.status == "pass"
    assert report.findings == ()


def test_missing_gate_selection_is_not_treated_as_pass() -> None:
    receipt = _base_receipt()
    receipt.pop("checks")
    report = validate_receipt(receipt)
    assert not report.ok
    assert "missing-check-selection" in {item.code for item in report.errors}


def test_spent_mechanism_requires_concrete_difference() -> None:
    receipt = _base_receipt()
    receipt["proposal"]["differs_from_spent"] = False
    receipt["checks"] = ["spent-lane"]
    report = validate_receipt(receipt)
    assert not report.ok
    assert "spent-lane-repeat" in {item.code for item in report.errors}


def test_parked_lane_requires_explicit_revival_receipt() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["parked-lane"]
    receipt["proposal"] = {"mechanism": "retired-launch-hook"}
    report = validate_receipt(receipt)
    assert not report.ok
    assert "parked-lane-revival-denied" in {item.code for item in report.errors}


def test_requirements_report_uncovered_requirement() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["requirements"]
    receipt["requirements_receipt"]["covered_ids"] = ["REQ-32"]
    report = validate_receipt(receipt)
    assert not report.ok
    assert "uncovered-requirements" in {item.code for item in report.errors}


def test_retrieval_rejects_stale_or_unbounded_baseline() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["retrieval"]
    receipt["retrieval"]["coverage_complete"] = False
    receipt["retrieval"]["baseline_current"] = False
    report = validate_receipt(receipt)
    codes = {item.code for item in report.errors}
    assert {"retrieval-not-bounded", "stale-baseline"} <= codes


def test_successor_before_failure_and_non_monotonic_counter_fail() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["attempt-order"]
    receipt["attempts"][1]["counter"] = 1
    receipt["attempts"].reverse()
    report = validate_receipt(receipt)
    codes = {item.code for item in report.errors}
    assert "non-monotonic-attempt-time" in codes
    assert "non-monotonic-attempt-counter" in codes
    assert "successor-without-failure" in codes


def test_operator_rejection_requires_open_status() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["status"]
    receipt["status"] = "RESOLVED"
    receipt["attempts"][1]["outcome"] = "REJECTED"
    report = validate_receipt(receipt)
    assert not report.ok
    assert "failed-status-contradiction" in {item.code for item in report.errors}


def test_warning_comment_inventory_requires_accounting() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["warning-comments"]
    receipt["warning_comments"]["inventory"] = ["W1", "W2"]
    report = validate_receipt(receipt)
    assert not report.ok
    assert "unaccounted-warning-removal" in {item.code for item in report.errors}


def test_warning_move_requires_locations_and_receipt_reference() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["warning-comments"]
    receipt["warning_comments"]["retained"] = []
    receipt["warning_comments"]["moved"] = [{"id": "W1", "reason": "relocated", "evidence": "obs://W1"}]
    report = validate_receipt(receipt)
    codes = {item.code for item in report.errors}
    assert {"incomplete-warning-move", "unlinked-warning-accounting"} <= codes


def test_deletion_rejects_present_target_and_unauthorized_revival() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["deletion"]
    receipt["deletion"]["targets"][0]["present"] = True
    receipt["deletion"]["revivals"] = [{"target_id": "T1"}]
    report = validate_receipt(receipt)
    codes = {item.code for item in report.errors}
    assert {"deletion-not-proven", "unauthorized-revival"} <= codes


def test_directive_requires_router_guard_and_preflight_coverage() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["directives"]
    receipt["directives"][0]["guard"] = False
    receipt["directives"][0].pop("evidence")
    report = validate_receipt(receipt)
    codes = {item.code for item in report.errors}
    assert {"directive-not-covered", "directive-without-evidence"} <= codes


def test_json_file_validation_is_read_only_and_temp_scoped(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(_base_receipt()), encoding="utf-8")
    before = path.read_bytes()
    report = validate_json_file(path)
    assert report.ok
    assert path.read_bytes() == before


def test_json_file_reports_invalid_json_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    before = path.read_bytes()
    report = validate_json_file(path)
    assert not report.ok
    assert report.errors[0].code == "receipt-json-error"
    assert path.read_bytes() == before


def test_validate_records_keeps_record_index_in_findings() -> None:
    first = _base_receipt()
    first["checks"] = ["status"]
    first["status"] = "RESOLVED"
    first["attempts"][1]["outcome"] = "REJECTED"
    second = _base_receipt()
    second["checks"] = ["retrieval"]
    second["retrieval"]["baseline_current"] = False
    report = validate_records([first, second])
    assert not report.ok
    assert any("record 0:" in item.message and item.code == "failed-status-contradiction" for item in report.errors)
    assert any(item.evidence[0] == "record[1]" for item in report.errors)


def test_explicit_gate_override_is_supported() -> None:
    receipt = _base_receipt()
    receipt.pop("checks")
    report = validate_receipt(receipt, gates=["status"])
    assert report.ok


def test_unknown_gate_fails_closed() -> None:
    receipt = _base_receipt()
    receipt["checks"] = ["future-live-hook"]
    report = validate_receipt(receipt)
    assert not report.ok
    assert "unknown-gate" in {item.code for item in report.errors}
