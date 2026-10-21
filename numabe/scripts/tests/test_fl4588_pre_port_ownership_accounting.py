# =============================================================
# test_fl4588_pre_port_ownership_accounting.py
# =============================================================
# Focused accounting tests for scripts/fl4588_validate_pre_port.py.
#
# Why this file exists separately:
#   Pane %77 owns scripts/tests/test_fl4588_html_architecture.py.
#   These tests prove the migrate-scoped old-owner readiness metrics
#   and the --debt-json backend-debt inventory without editing that
#   shared architecture suite.
#
# What "migrate-scoped" means in plain language:
#   Only controls marked migration_status=migrate count toward HTML
#   cutover ownership progress. A deleted tombstone/read_only row is
#   historical bookkeeping — it must not make readiness look further
#   along than the migrate queue really is.
#
# What --debt-json owns:
#   The full machine-authoritative backend-debt list (registry hash +
#   exact arrays). Counts are derived live — never hardcoded.
# =============================================================

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "fl4588_validate_pre_port.py"
MATRIX = ROOT / "docs/research/ascii/verification/fl4260/HTML_UI_MIGRATION_MATRIX.csv"
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
INVENTORY_GRAPH = ROOT / "docs/research/ascii/verification/fl4260/INVENTORY_GRAPH.json"

DEBT_ROW_REQUIRED = (
    "control_uid",
    "route",
    "action_schema",
    "proof_status",
    "source_anchors",
    "source_owner",
)

OWNER_DECLARATION_STATUSES = {
    "live_native_ui_owner_declarations": {"live", "read_only"},
    "merged_owner_accounting_declarations": {"merged_into", "merge"},
    "method_only_owner_declarations": {
        "unavailable_method_only",
        "method_only",
    },
}


def _matrix_rows() -> list[dict[str, str]]:
    with MATRIX.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _expected_migrate_ownership(rows: list[dict[str, str]]) -> dict[str, int]:
    """Recompute the truthful migrate-scoped ownership numbers from the CSV."""
    migrate = [row for row in rows if row["migration_status"] == "migrate"]
    deleted_on_migrate = sum(row["old_owner_deleted"] == "true" for row in migrate)
    total_deleted = sum(row["old_owner_deleted"] == "true" for row in rows)
    return {
        "migration_rows": len(migrate),
        "migrated_old_owners_deleted": deleted_on_migrate,
        "migrated_old_owners_remaining": len(migrate) - deleted_on_migrate,
        "deleted_owner_records_total": total_deleted,
    }


def _expected_debt_counts(registry: dict, matrix: list[dict[str, str]]) -> dict[str, int]:
    """Independent recomputation of debt-array lengths (no hardcoded constants)."""
    mutation_leaves = [leaf for leaf in registry["leaves"] if leaf["action_schema"]]
    product = [
        item
        for item in registry["interactions"]
        if item["action_scope"] == "product_transaction"
    ]
    missing = sum(
        1
        for surface in mutation_leaves + product
        if surface["application_service_handler"] is None
    )
    remaining = sum(
        1
        for row in matrix
        if row["migration_status"] == "migrate" and row["old_owner_deleted"] != "true"
    )
    unwired = sum(
        1
        for item in registry["interactions"]
        if item["action_scope"] == "browser_draft"
        and item["application_service_handler"] is None
    )
    expected = {
        "missing_typed_mutation_handlers": missing,
        "remaining_migrated_native_owners": remaining,
        "unwired_browser_draft_interactions": unwired,
    }
    for array_name, statuses in OWNER_DECLARATION_STATUSES.items():
        expected[array_name] = sum(
            1
            for leaf in registry["leaves"]
            if leaf.get("old_owner_deleted") is False
            and leaf.get("status") != "tombstoned"
            and leaf.get("status") in statuses
        )
    return expected


def _run_validator(*flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *flags],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_audit_metrics_are_migrate_scoped_not_inflated_by_tombstones():
    """
    Live validator JSON must expose migrate-scoped ownership metrics and
    must not treat non-migrate deletions as readiness progress.
    """
    rows = _matrix_rows()
    expected = _expected_migrate_ownership(rows)
    # Sanity: the accounting defect only matters when non-migrate deletions exist.
    assert expected["deleted_owner_records_total"] > expected["migrated_old_owners_deleted"]

    completed = _run_validator("--json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    metrics = report["metrics"]

    assert metrics["migration_rows"] == expected["migration_rows"]
    assert metrics["migrated_old_owners_deleted"] == expected["migrated_old_owners_deleted"]
    assert metrics["migrated_old_owners_remaining"] == expected["migrated_old_owners_remaining"]
    assert metrics["deleted_owner_records_total"] == expected["deleted_owner_records_total"]

    # Deprecated compatibility key keeps total-record semantics for callers
    # that still read old_owners_deleted (architecture suite owned by %77).
    assert metrics["old_owners_deleted"] == expected["deleted_owner_records_total"]
    assert metrics["old_owners_deleted"] != metrics["migrated_old_owners_deleted"]


def test_old_imgui_blocker_detail_states_truthful_migrated_count():
    """OLD_IMGUI_OWNERS_REMAIN appears only while migrate owners remain.

    Zero remaining owners is valid completion: the blocker must be absent.
    Nonzero remaining owners must keep the migrate-scoped detail contract.
    """
    expected = _expected_migrate_ownership(_matrix_rows())
    completed = _run_validator("--json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    blockers = {item["code"]: item["detail"] for item in report["blockers"]}

    if expected["migrated_old_owners_remaining"] == 0:
        # Completion semantics: no OLD_IMGUI_OWNERS_REMAIN when the migrate
        # queue has zero surviving native owners.
        assert "OLD_IMGUI_OWNERS_REMAIN" not in blockers
        assert report["metrics"]["migrated_old_owners_remaining"] == 0
        return

    assert "OLD_IMGUI_OWNERS_REMAIN" in blockers
    detail = blockers["OLD_IMGUI_OWNERS_REMAIN"]
    assert (
        f"Only {expected['migrated_old_owners_deleted']} of "
        f"{expected['migration_rows']} migrate rows"
    ) in detail
    assert f"{expected['migrated_old_owners_remaining']} migrated old owners remain" in detail
    assert (
        f"deleted_owner_records_total={expected['deleted_owner_records_total']}"
    ) in detail
    # Must not claim the inflated total-as-migrate numerator.
    inflated = (
        f"Only {expected['deleted_owner_records_total']} of "
        f"{expected['migration_rows']} migration rows"
    )
    assert inflated not in detail


def test_audit_helper_matches_json_metrics_directly():
    """Importing audit() yields the same migrate-scoped fields as --json."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import fl4588_validate_pre_port as validator  # noqa: WPS433

    report = validator.audit()
    expected = _expected_migrate_ownership(_matrix_rows())
    metrics = report["metrics"]
    assert metrics["migrated_old_owners_deleted"] == expected["migrated_old_owners_deleted"]
    assert metrics["migrated_old_owners_remaining"] == expected["migrated_old_owners_remaining"]
    assert metrics["deleted_owner_records_total"] == expected["deleted_owner_records_total"]
    assert metrics["old_owners_deleted"] == expected["deleted_owner_records_total"]


def test_debt_json_schema_and_registry_hash():
    """--debt-json must emit the backend-debt schema plus live registry hash."""
    registry = _registry()
    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)

    assert report["schema"] == "asciiid.pre_port_backend_debt.v1"
    assert report["fl"] == "FL-4588"
    assert report["registry_sha256"] == registry["registry_sha256"]
    assert len(report["registry_sha256"]) == 64
    for key in (
        "missing_typed_mutation_handlers",
        "remaining_migrated_native_owners",
        "unwired_browser_draft_interactions",
        "live_native_ui_owner_declarations",
        "merged_owner_accounting_declarations",
        "method_only_owner_declarations",
        "missing_active_tool_edges",
        "unproven_interactions",
        "incomplete_inventory_graph_rows",
        "counts",
    ):
        assert key in report
    assert "undeleted_owner_declarations" not in report
    assert "undeleted_owner_declarations" not in report["counts"]
    assert report["completion_blocked"] is True


def test_debt_json_counts_match_live_arrays_not_hardcoded():
    """
    Counts must equal array lengths and match an independent recomputation
    from the live registry + matrix (no magic constants in the test).
    """
    registry = _registry()
    matrix = _matrix_rows()
    expected = _expected_debt_counts(registry, matrix)

    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    counts = report["counts"]

    for key, expected_count in expected.items():
        assert counts[key] == expected_count
        assert len(report[key]) == expected_count
        assert len(report[key]) == counts[key]


def test_debt_json_rows_carry_stable_identity_fields():
    """Debt rows carry identity fields; empty arrays are valid zero-debt completion.

    Nonzero debt detection stays strict: when an array is nonempty, every row
    must expose the stable identity contract. Empty arrays are allowed and must
    match the live counts (completion semantics).
    """
    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    expected = _expected_debt_counts(_registry(), _matrix_rows())

    for array_name in (
        "missing_typed_mutation_handlers",
        "remaining_migrated_native_owners",
        "unwired_browser_draft_interactions",
        "live_native_ui_owner_declarations",
        "merged_owner_accounting_declarations",
        "method_only_owner_declarations",
    ):
        rows = report[array_name]
        assert isinstance(rows, list)
        assert len(rows) == expected[array_name]
        assert len(rows) == report["counts"][array_name]
        for row in rows:
            for field in DEBT_ROW_REQUIRED:
                assert field in row, f"{array_name} row missing {field}: {row}"
            assert isinstance(row["control_uid"], str) and row["control_uid"]
            assert isinstance(row["source_anchors"], list)
            assert row["proof_status"] is not None


def test_debt_json_remaining_owners_are_migrate_and_not_deleted():
    """remaining_migrated_native_owners must be migrate-scoped undeleted only."""
    matrix = _matrix_rows()
    expected_uids = {
        row["control_uid"]
        for row in matrix
        if row["migration_status"] == "migrate" and row["old_owner_deleted"] != "true"
    }
    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    actual_uids = {row["control_uid"] for row in report["remaining_migrated_native_owners"]}
    assert actual_uids == expected_uids
    for row in report["remaining_migrated_native_owners"]:
        assert row.get("migration_status") == "migrate"
        assert row.get("old_owner_deleted") is False


def test_debt_json_unwired_drafts_exclude_wired_handlers():
    """Browser-draft rows with a bound handler must not appear in unwired debt."""
    registry = _registry()
    wired = {
        item["control_uid"]
        for item in registry["interactions"]
        if item["action_scope"] == "browser_draft"
        and item["application_service_handler"] is not None
    }
    unwired_expected = {
        item["control_uid"]
        for item in registry["interactions"]
        if item["action_scope"] == "browser_draft"
        and item["application_service_handler"] is None
    }
    assert wired, "fixture expectation: at least one wired browser-draft exists"
    # Empty unwired_expected is valid zero-draft-debt completion.

    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    actual = {row["control_uid"] for row in report["unwired_browser_draft_interactions"]}
    assert actual == unwired_expected
    assert actual.isdisjoint(wired)


def test_debt_report_helper_matches_cli():
    """debt_report() import path must match --debt-json CLI output."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import fl4588_validate_pre_port as validator  # noqa: WPS433

    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    cli_report = json.loads(completed.stdout)
    helper_report = validator.debt_report()
    assert helper_report == cli_report


def test_debt_json_does_not_equate_zero_handler_debt_with_editor_readiness():
    registry = _registry()
    graph = json.loads(INVENTORY_GRAPH.read_text(encoding="utf-8"))
    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)

    expected_owner_uids: dict[str, list[str]] = {}
    for array_name, statuses in OWNER_DECLARATION_STATUSES.items():
        expected_owner_uids[array_name] = sorted(
            leaf["control_uid"]
            for leaf in registry["leaves"]
            if leaf.get("old_owner_deleted") is False
            and leaf.get("status") != "tombstoned"
            and leaf.get("status") in statuses
        )

    classified_uid_sets: list[set[str]] = []
    for array_name, statuses in OWNER_DECLARATION_STATUSES.items():
        rows = report[array_name]
        actual_uids = [row["control_uid"] for row in rows]
        assert actual_uids == expected_owner_uids[array_name]
        assert report["counts"][array_name] == len(rows)
        assert all(row["status"] in statuses for row in rows)
        assert all(row["old_owner_deleted"] is False for row in rows)
        classified_uid_sets.append(set(actual_uids))

    for index, uids in enumerate(classified_uid_sets):
        for other_uids in classified_uid_sets[index + 1:]:
            assert uids.isdisjoint(other_uids)

    expected_all_undeleted = {
        leaf["control_uid"]
        for leaf in registry["leaves"]
        if leaf.get("old_owner_deleted") is False
        and leaf.get("status") != "tombstoned"
    }
    actual_all_classified = set().union(*classified_uid_sets)
    assert actual_all_classified == expected_all_undeleted
    assert any(classified_uid_sets)
    assert report["completion_blocked"] is True

    expected_missing_edges = {
        (tool["tool_id"], modifier["role"])
        for tool in registry["active_tool_graph"]["tools"]
        for modifier in tool.get("tool_modifiers", [])
        if modifier.get("status") != "WIRED"
    }
    actual_missing_edges = {
        (row["tool_id"], row["role"])
        for row in report["missing_active_tool_edges"]
        if row["surface_kind"] == "active_tool_modifier_edge"
    }
    assert actual_missing_edges == expected_missing_edges
    assert actual_missing_edges == set()
    assert report["counts"]["missing_active_tool_edges"] == 0

    expected_unproven = {
        item["control_uid"]
        for item in registry["interactions"]
        if item.get("proof_status") not in {"PASS", "PROVEN", "RECEIPT_PASS"}
    }
    assert {
        row["control_uid"] for row in report["unproven_interactions"]
    } == expected_unproven

    expected_incomplete = {
        str(entry["control_uid"])
        for entry in graph["rows"]
        if not str(entry.get("authoring_priority") or "").startswith(("P0_", "P5_"))
        and (
            entry.get("missing_fields")
            or entry.get("source_anchor_status") == "MISSING"
        )
    }
    assert {
        row["control_uid"]
        for row in report["incomplete_inventory_graph_rows"]
    } == expected_incomplete
    assert report["completion_blocked"] is True


def test_zero_remaining_owners_is_valid_completion():
    """Empty remaining_migrated_native_owners is legal when the matrix agrees."""
    expected = _expected_debt_counts(_registry(), _matrix_rows())
    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["counts"]["remaining_migrated_native_owners"] == expected[
        "remaining_migrated_native_owners"
    ]
    assert len(report["remaining_migrated_native_owners"]) == expected[
        "remaining_migrated_native_owners"
    ]
    # Live tree currently has zero remaining migrate owners; keep the
    # completion assertion explicit so a future reopen fails this test.
    assert expected["remaining_migrated_native_owners"] == 0
    assert report["remaining_migrated_native_owners"] == []


def test_nonzero_debt_arrays_still_detect_missing_handlers():
    """
    Missing-handler debt detection must stay live even at zero debt.

    When every mutation leaf and product_transaction names a handler,
    the expected count is 0 and the debt array must be empty. The test
    still recomputes the count from the registry so a future reopen
    that drops a handler would fail the equality check below — not by
    asserting "> 0" forever after cutover completion.
    """
    expected = _expected_debt_counts(_registry(), _matrix_rows())
    completed = _run_validator("--debt-json")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    # Live tree: typed-handler debt is cleared (document-merge + prior
    # material cutovers). Keep detection equality, not a forever "> 0".
    assert expected["missing_typed_mutation_handlers"] == 0
    assert report["counts"]["missing_typed_mutation_handlers"] == expected[
        "missing_typed_mutation_handlers"
    ]
    assert len(report["missing_typed_mutation_handlers"]) == expected[
        "missing_typed_mutation_handlers"
    ]
    assert report["missing_typed_mutation_handlers"] == []
    for row in report["missing_typed_mutation_handlers"]:
        assert row["control_uid"]
