#!/usr/bin/env python3
# =============================================================
# fl4588_validate_pre_port.py
# =============================================================
# Fail-closed FL-4588 readiness audit for the ASCIIID browser port.
#
# This tool does not treat a generated registry as implementation. It
# reports the remaining ownership and specification blockers, then
# returns failure only when `--require-ready` asks whether browser
# mutation may begin.
#
# Ownership accounting rule (readiness):
#   Count old-owner deletions only on rows whose migration_status is
#   "migrate". Tombstone / read_only deleted-owner rows are historical
#   records — they must not inflate readiness progress against the
#   migrate queue.
#
# Backend debt list (--debt-json):
#   Machine-authoritative full list of remaining backend debt derived
#   live from the registry + migration matrix. Counts are never
#   hardcoded — callers must read the emitted arrays.
# =============================================================

"""Fail-closed FL-4588 readiness audit for the ASCIIID browser port.

This tool does not treat a generated registry as implementation. It reports the
remaining ownership and specification blockers, then returns failure only when
`--require-ready` asks whether browser mutation may begin.

`--debt-json` emits the complete readiness-debt inventory derived from the
live registry, migration matrix, active-tool graph, and inventory graph. It
keeps the original backend cutover arrays and classifies every non-tombstone
undeleted owner declaration as live native UI, merged accounting, or
method-only debt. Zero backend-handler debt is not editor readiness.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
MATRIX_PATH = ROOT / "docs/research/ascii/verification/fl4260/HTML_UI_MIGRATION_MATRIX.csv"
INVENTORY_GRAPH_PATH = (
    ROOT / "docs/research/ascii/verification/fl4260/INVENTORY_GRAPH.json"
)
COUNTERFACTUAL_GATE_PATH = (
    ROOT / "docs/agent/asciiid-html-architecture/asciiid-counterfactual-golden-gate.md"
)
CPP_SOURCE_ROOTS = (
    ROOT / "editor",
    ROOT / "apps/asciicker-presenter",
    ROOT / "engine",
    ROOT / "platform",
)

# Stable debt-row fields every array entry must carry so agents can act
# without re-deriving identity from prose.
DEBT_ROW_REQUIRED_FIELDS = (
    "control_uid",
    "route",
    "action_schema",
    "proof_status",
    "source_anchors",
    "source_owner",
)


def _contains_spec_open(values: list[str]) -> bool:
    return any(value.startswith("SPEC_OPEN") for value in values)


def _application_service_implemented() -> bool:
    declaration = "class AsciiidApplicationService"
    for source_root in CPP_SOURCE_ROOTS:
        for suffix in ("*.h", "*.hpp", "*.cpp", "*.cc"):
            for path in source_root.rglob(suffix):
                if declaration in path.read_text(encoding="utf-8", errors="ignore"):
                    return True
    return False


def _load_registry_and_matrix() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Read the two authoritative debt inputs: UI registry + migration matrix."""
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    with MATRIX_PATH.open(newline="", encoding="utf-8") as handle:
        matrix = list(csv.DictReader(handle))
    return registry, matrix


def _as_anchor_list(value: Any) -> list[str]:
    """Normalize source_anchors to a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def _debt_row(
    *,
    control_uid: str,
    route: str | None,
    action_schema: str | None,
    proof_status: str | None,
    source_anchors: Any,
    source_owner: Any,
    leaf_id: str | None = None,
    surface_kind: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build one backend-debt row with stable identity fields.

    source_anchors / source_owner are included when available (empty list /
    null when absent) so callers never have to guess which key exists.
    """
    anchors = _as_anchor_list(source_anchors)
    owner = source_owner if isinstance(source_owner, str) and source_owner.strip() else None
    row: dict[str, Any] = {
        "control_uid": control_uid,
        "route": route,
        "action_schema": action_schema,
        "proof_status": proof_status,
        "source_anchors": anchors,
        # Singular convenience: first pin when the list is non-empty.
        "source_anchor": anchors[0] if anchors else None,
        "source_owner": owner,
    }
    if leaf_id is not None:
        row["leaf_id"] = leaf_id
    if surface_kind is not None:
        row["surface_kind"] = surface_kind
    if extra:
        row.update(extra)
    return row


def _mutation_surfaces(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Typed mutation surfaces = leaves with an action_schema plus product
    transaction interactions. Browser-draft interactions are a separate
    debt queue (unwired drafts), not counted as missing product handlers.
    """
    leaves = registry["leaves"]
    interactions = registry["interactions"]
    mutation_leaves = [leaf for leaf in leaves if leaf["action_schema"]]
    product_interactions = [
        interaction
        for interaction in interactions
        if interaction["action_scope"] == "product_transaction"
    ]
    return mutation_leaves + product_interactions


def debt_report() -> dict[str, Any]:
    """
    Emit the full backend-debt inventory as machine-authoritative JSON.

    Arrays are derived live from the registry and migration matrix. Lengths
    in `counts` always equal the corresponding array lengths — never a
    separately hardcoded constant.
    """
    registry, matrix = _load_registry_and_matrix()
    leaves_by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}

    # 1) Missing typed mutation handlers (product mutation surfaces only).
    missing_typed_mutation_handlers: list[dict[str, Any]] = []
    for surface in _mutation_surfaces(registry):
        if surface.get("application_service_handler") is not None:
            continue
        # Leaves carry legacy_source_handler; interactions may not.
        legacy_handler = surface.get("legacy_source_handler")
        anchors = list(_as_anchor_list(surface.get("source_anchors")))
        if isinstance(legacy_handler, str) and legacy_handler.strip():
            if legacy_handler.strip() not in anchors:
                anchors.append(legacy_handler.strip())
        kind = (
            "product_interaction"
            if surface.get("action_scope") == "product_transaction"
            else "mutation_leaf"
        )
        missing_typed_mutation_handlers.append(
            _debt_row(
                control_uid=surface["control_uid"],
                route=surface.get("route"),
                action_schema=surface.get("action_schema"),
                proof_status=surface.get("proof_status"),
                source_anchors=anchors,
                source_owner=surface.get("source_owner"),
                leaf_id=surface.get("leaf_id"),
                surface_kind=kind,
            )
        )

    # 2) Remaining migrated native owners: migrate rows whose old ImGui /
    #    native owner has not been deleted yet. Enrich from registry when
    #    the control_uid is present there.
    remaining_migrated_native_owners: list[dict[str, Any]] = []
    for row in matrix:
        if row.get("migration_status") != "migrate":
            continue
        if row.get("old_owner_deleted") == "true":
            continue
        leaf = leaves_by_uid.get(row["control_uid"])
        anchors = _as_anchor_list(leaf.get("source_anchors") if leaf else None)
        legacy = row.get("old_source_handler") or (
            leaf.get("legacy_source_handler") if leaf else None
        )
        if isinstance(legacy, str) and legacy.strip() and legacy.strip() not in anchors:
            anchors.append(legacy.strip())
        remaining_migrated_native_owners.append(
            _debt_row(
                control_uid=row["control_uid"],
                route=(
                    (leaf.get("route") if leaf else None)
                    or row.get("target_route")
                ),
                action_schema=(
                    (leaf.get("action_schema") if leaf else None)
                    or row.get("new_action_schema")
                    or None
                ),
                proof_status=(
                    (leaf.get("proof_status") if leaf else None)
                    or row.get("proof_status")
                ),
                source_anchors=anchors,
                source_owner=(leaf.get("source_owner") if leaf else None),
                leaf_id=(
                    (leaf.get("leaf_id") if leaf else None)
                    or row.get("target_leaf_id")
                ),
                surface_kind="migrated_native_owner",
                extra={
                    "migration_status": "migrate",
                    "old_owner_deleted": False,
                    "old_source_handler": row.get("old_source_handler") or None,
                },
            )
        )

    # 3) Unwired browser-draft interactions: draft-scope rows that still
    #    have no application-service (or draft-wire) handler binding.
    unwired_browser_draft_interactions: list[dict[str, Any]] = []
    for interaction in registry["interactions"]:
        if interaction.get("action_scope") != "browser_draft":
            continue
        if interaction.get("application_service_handler") is not None:
            # Already wired to a typed service entry — not debt.
            continue
        unwired_browser_draft_interactions.append(
            _debt_row(
                control_uid=interaction["control_uid"],
                route=interaction.get("route"),
                action_schema=interaction.get("action_schema"),
                proof_status=interaction.get("proof_status"),
                source_anchors=interaction.get("source_anchors"),
                source_owner=interaction.get("source_owner"),
                leaf_id=interaction.get("leaf_id"),
                surface_kind="browser_draft",
            )
        )

    # 4) Classify every non-tombstone leaf whose old owner is still declared
    #    undeleted. Each accepted status maps to exactly one array. An unknown
    #    status fails closed instead of silently dropping a completion blocker.
    live_native_ui_owner_declarations: list[dict[str, Any]] = []
    merged_owner_accounting_declarations: list[dict[str, Any]] = []
    method_only_owner_declarations: list[dict[str, Any]] = []
    owner_declaration_arrays = {
        "live_native_ui_owner_declarations": live_native_ui_owner_declarations,
        "merged_owner_accounting_declarations": (
            merged_owner_accounting_declarations
        ),
        "method_only_owner_declarations": method_only_owner_declarations,
    }
    declaration_array_by_status = {
        "live": "live_native_ui_owner_declarations",
        "read_only": "live_native_ui_owner_declarations",
        "merged_into": "merged_owner_accounting_declarations",
        "merge": "merged_owner_accounting_declarations",
        "unavailable_method_only": "method_only_owner_declarations",
        "method_only": "method_only_owner_declarations",
    }
    surface_kind_by_array = {
        "live_native_ui_owner_declarations": "live_native_ui_owner_declaration",
        "merged_owner_accounting_declarations": (
            "merged_owner_accounting_declaration"
        ),
        "method_only_owner_declarations": "method_only_owner_declaration",
    }
    for leaf in registry["leaves"]:
        if leaf.get("old_owner_deleted") is not False:
            continue
        if leaf.get("status") == "tombstoned":
            continue
        status = str(leaf.get("status") or "")
        array_name = declaration_array_by_status.get(status)
        if array_name is None:
            raise ValueError(
                "unclassified undeleted owner declaration: "
                f"{leaf.get('control_uid')} status={status!r}"
            )
        owner_declaration_arrays[array_name].append(
            _debt_row(
                control_uid=leaf["control_uid"],
                route=leaf.get("route"),
                action_schema=leaf.get("action_schema"),
                proof_status=leaf.get("proof_status"),
                source_anchors=leaf.get("source_anchors"),
                source_owner=leaf.get("source_owner"),
                leaf_id=leaf.get("leaf_id"),
                surface_kind=surface_kind_by_array[array_name],
                extra={
                    "status": status,
                    "migration_status": leaf.get("migration_status"),
                    "old_owner_deleted": False,
                },
            )
        )

    # 5) Missing primary-canvas and modifier edges from the sole active-tool
    #    graph. A tool can have a wired base gesture while still lacking the
    #    selection/delete chord that makes the editor usable.
    interactions_by_leaf = {
        item.get("leaf_id"): item for item in registry["interactions"]
    }
    missing_active_tool_edges: list[dict[str, Any]] = []
    active_tool_graph = registry.get("active_tool_graph") or {}
    for tool in active_tool_graph.get("tools") or []:
        tool_id = str(tool.get("tool_id") or "")
        canvas_leaf_id = tool.get("canvas_gesture_leaf_id")
        if tool.get("canvas_gesture_status") != "WIRED":
            interaction = interactions_by_leaf.get(canvas_leaf_id) or {}
            missing_active_tool_edges.append(
                _debt_row(
                    control_uid=(
                        interaction.get("control_uid")
                        or f"active_tool.{tool_id}.canvas"
                    ),
                    route=interaction.get("route"),
                    action_schema=interaction.get("action_schema"),
                    proof_status=tool.get("proof_status"),
                    source_anchors=interaction.get("source_anchors"),
                    source_owner=interaction.get("source_owner"),
                    leaf_id=canvas_leaf_id,
                    surface_kind="active_tool_canvas_edge",
                    extra={
                        "tool_id": tool_id,
                        "edge_status": tool.get("canvas_gesture_status"),
                    },
                )
            )
        for modifier in tool.get("tool_modifiers") or []:
            if modifier.get("status") == "WIRED":
                continue
            interaction_id = modifier.get("claimed_interaction_id")
            interaction = interactions_by_leaf.get(interaction_id) or {}
            role = str(modifier.get("role") or "unknown")
            missing_active_tool_edges.append(
                _debt_row(
                    control_uid=(
                        interaction.get("control_uid")
                        or f"active_tool.{tool_id}.modifier.{role}"
                    ),
                    route=interaction.get("route"),
                    action_schema=interaction.get("action_schema"),
                    proof_status=tool.get("proof_status"),
                    source_anchors=interaction.get("source_anchors"),
                    source_owner=interaction.get("source_owner"),
                    leaf_id=interaction_id,
                    surface_kind="active_tool_modifier_edge",
                    extra={
                        "tool_id": tool_id,
                        "chord": modifier.get("chord"),
                        "role": role,
                        "edge_status": modifier.get("status"),
                    },
                )
            )

    # 6) User-reachable interactions without a closed grouped receipt. This
    #    prevents structural wiring from being reported as executed behavior.
    accepted_proof_statuses = {"PASS", "PROVEN", "RECEIPT_PASS"}
    unproven_interactions: list[dict[str, Any]] = []
    for interaction in registry["interactions"]:
        if interaction.get("proof_status") in accepted_proof_statuses:
            continue
        unproven_interactions.append(
            _debt_row(
                control_uid=interaction["control_uid"],
                route=interaction.get("route"),
                action_schema=interaction.get("action_schema"),
                proof_status=interaction.get("proof_status"),
                source_anchors=interaction.get("source_anchors"),
                source_owner=interaction.get("source_owner"),
                leaf_id=interaction.get("leaf_id"),
                surface_kind="unproven_interaction",
                extra={
                    "proof_open_reason": interaction.get("proof_open_reason"),
                },
            )
        )

    # 7) Inventory graph rows that still lack authored proof-critical fields.
    #    P0 receipt rows and P5 tombstones have their own completion semantics.
    inventory_graph = json.loads(
        INVENTORY_GRAPH_PATH.read_text(encoding="utf-8")
    )
    incomplete_inventory_graph_rows: list[dict[str, Any]] = []
    for entry in inventory_graph.get("rows") or []:
        priority = str(entry.get("authoring_priority") or "")
        if priority.startswith("P0_") or priority.startswith("P5_"):
            continue
        missing_fields = list(entry.get("missing_fields") or [])
        anchor_missing = entry.get("source_anchor_status") == "MISSING"
        if not missing_fields and not anchor_missing:
            continue
        incomplete_inventory_graph_rows.append(
            _debt_row(
                control_uid=str(entry.get("control_uid") or ""),
                route=None,
                action_schema=None,
                proof_status=entry.get("proof_status"),
                source_anchors=[],
                source_owner=None,
                leaf_id=str(entry.get("leaf_path") or ""),
                surface_kind="incomplete_inventory_graph_row",
                extra={
                    "authoring_priority": priority,
                    "missing_fields": missing_fields,
                    "source_anchor_status": entry.get("source_anchor_status"),
                },
            )
        )

    # Deterministic order so diffs and agent queues stay stable.
    missing_typed_mutation_handlers.sort(key=lambda row: row["control_uid"])
    remaining_migrated_native_owners.sort(key=lambda row: row["control_uid"])
    unwired_browser_draft_interactions.sort(key=lambda row: row["control_uid"])
    live_native_ui_owner_declarations.sort(key=lambda row: row["control_uid"])
    merged_owner_accounting_declarations.sort(key=lambda row: row["control_uid"])
    method_only_owner_declarations.sort(key=lambda row: row["control_uid"])
    missing_active_tool_edges.sort(key=lambda row: row["control_uid"])
    unproven_interactions.sort(key=lambda row: row["control_uid"])
    incomplete_inventory_graph_rows.sort(key=lambda row: row["control_uid"])

    completion_arrays = (
        missing_typed_mutation_handlers,
        remaining_migrated_native_owners,
        unwired_browser_draft_interactions,
        live_native_ui_owner_declarations,
        merged_owner_accounting_declarations,
        method_only_owner_declarations,
        missing_active_tool_edges,
        unproven_interactions,
        incomplete_inventory_graph_rows,
    )

    return {
        "schema": "asciiid.pre_port_backend_debt.v1",
        "fl": "FL-4588",
        "registry_sha256": registry["registry_sha256"],
        "registry_path": "assets/asciiid/ui/asciiid.ui_registry.v1.json",
        "matrix_path": (
            "docs/research/ascii/verification/fl4260/HTML_UI_MIGRATION_MATRIX.csv"
        ),
        "counts": {
            "missing_typed_mutation_handlers": len(missing_typed_mutation_handlers),
            "remaining_migrated_native_owners": len(remaining_migrated_native_owners),
            "unwired_browser_draft_interactions": len(
                unwired_browser_draft_interactions
            ),
            "live_native_ui_owner_declarations": len(
                live_native_ui_owner_declarations
            ),
            "merged_owner_accounting_declarations": len(
                merged_owner_accounting_declarations
            ),
            "method_only_owner_declarations": len(
                method_only_owner_declarations
            ),
            "missing_active_tool_edges": len(missing_active_tool_edges),
            "unproven_interactions": len(unproven_interactions),
            "incomplete_inventory_graph_rows": len(
                incomplete_inventory_graph_rows
            ),
        },
        "completion_blocked": any(completion_arrays),
        "missing_typed_mutation_handlers": missing_typed_mutation_handlers,
        "remaining_migrated_native_owners": remaining_migrated_native_owners,
        "unwired_browser_draft_interactions": unwired_browser_draft_interactions,
        "live_native_ui_owner_declarations": live_native_ui_owner_declarations,
        "merged_owner_accounting_declarations": (
            merged_owner_accounting_declarations
        ),
        "method_only_owner_declarations": method_only_owner_declarations,
        "missing_active_tool_edges": missing_active_tool_edges,
        "unproven_interactions": unproven_interactions,
        "incomplete_inventory_graph_rows": incomplete_inventory_graph_rows,
    }


def audit() -> dict:
    registry, matrix = _load_registry_and_matrix()

    leaves = registry["leaves"]
    interactions = registry["interactions"]
    mutation_leaves = [leaf for leaf in leaves if leaf["action_schema"]]
    product_interactions = [
        interaction for interaction in interactions
        if interaction["action_scope"] == "product_transaction"
    ]
    browser_draft_interactions = [
        interaction for interaction in interactions
        if interaction["action_scope"] == "browser_draft"
    ]
    mutation_surfaces = mutation_leaves + product_interactions
    spec_open_read = sum(_contains_spec_open(leaf["read_model_fields"]) for leaf in leaves)
    spec_open_write = sum(_contains_spec_open(leaf["write_set_template"]) for leaf in leaves)
    missing_handlers = sum(
        surface["application_service_handler"] is None for surface in mutation_surfaces
    )
    missing_paper_rules = sum(leaf["paper_rule_id"] is None for leaf in mutation_leaves)
    missing_predictors = sum(leaf["predictor_model_id"] is None for leaf in mutation_leaves)

    # --- Old-owner deletion accounting (migrate-scoped readiness) ---
    # migration_status=migrate rows are the only queue that readiness may
    # claim as "HTML cutover ownership progress." Rows marked tombstone /
    # read_only can still record old_owner_deleted=true as historical
    # bookkeeping; counting those into the readiness numerator was the
    # ownership accounting defect this block corrects.
    migrate_matrix_rows = [
        row for row in matrix if row["migration_status"] == "migrate"
    ]
    migrate_rows = len(migrate_matrix_rows)
    migrated_old_owners_deleted = sum(
        row["old_owner_deleted"] == "true" for row in migrate_matrix_rows
    )
    migrated_old_owners_remaining = migrate_rows - migrated_old_owners_deleted
    # Every matrix row with old_owner_deleted=true, including non-migrate.
    deleted_owner_records_total = sum(
        row["old_owner_deleted"] == "true" for row in matrix
    )

    gate_text = COUNTERFACTUAL_GATE_PATH.read_text(encoding="utf-8")
    counterfactual_gate_open = "Status: `GATE_OPEN`" in gate_text
    app_service_implemented = _application_service_implemented()

    metrics = {
        "registry_cutover_status": registry["cutover_status"],
        "registry_leaf_count": registry["leaf_count"],
        "registry_interaction_count": registry["interaction_count"],
        "legacy_inventory_is_generator_input": not (
            registry.get("authority", {}).get("source_kind") == "direct_registry_contract"
            and registry.get("authority", {}).get("path")
            == "assets/asciiid/ui/asciiid.ui_registry.v1.json"
            and registry.get("authority", {}).get("legacy_inventory_role")
            == "historical_accounting_only"
        ),
        "spec_open_read_models": spec_open_read,
        "spec_open_write_sets": spec_open_write,
        "mutation_surfaces": len(mutation_surfaces),
        "browser_draft_interactions": len(browser_draft_interactions),
        "missing_application_service_handlers": missing_handlers,
        "mutation_leaves_missing_paper_rules": missing_paper_rules,
        "mutation_leaves_missing_predictors": missing_predictors,
        "counterfactual_gate_open": counterfactual_gate_open,
        "application_service_cpp_present": app_service_implemented,
        "migration_rows": migrate_rows,
        # Explicit readiness ownership metrics (migrate-scoped).
        "migrated_old_owners_deleted": migrated_old_owners_deleted,
        "migrated_old_owners_remaining": migrated_old_owners_remaining,
        "deleted_owner_records_total": deleted_owner_records_total,
        # DEPRECATED compatibility field for existing callers
        # (scripts/tests/test_fl4588_html_architecture.py). Semantics are
        # intentionally identical to deleted_owner_records_total: the count
        # of ALL matrix rows with old_owner_deleted=true, including
        # non-migrate tombstone/read_only history. Do NOT use this field for
        # readiness ownership progress — use migrated_old_owners_* instead.
        "old_owners_deleted": deleted_owner_records_total,
    }

    blockers: list[dict[str, str]] = []

    def block(code: str, detail: str) -> None:
        blockers.append({"code": code, "detail": detail})

    # FL-4588 RQ-201f: each condition below independently blocks browser
    # mutation. A label, route, schema shell, plus generated row is insufficient.
    if registry["cutover_status"] != "authoritative":
        block("REGISTRY_PRE_CUTOVER", "The UI registry is not the authoritative generation source.")
    if metrics["legacy_inventory_is_generator_input"]:
        block(
            "LEGACY_INVENTORY_STILL_UPSTREAM",
            "CURRENT_UI_INVENTORY.csv still generates the registry; the required authority direction has not been reversed.",
        )
    if spec_open_read:
        block("READ_MODELS_SPEC_OPEN", f"{spec_open_read} leaf read models remain SPEC_OPEN.")
    if spec_open_write:
        block("WRITE_SETS_SPEC_OPEN", f"{spec_open_write} mutation write sets remain SPEC_OPEN.")
    if missing_handlers:
        block(
            "APPLICATION_HANDLERS_ABSENT",
            f"{missing_handlers} mutation surfaces have no typed C++ application-service handler.",
        )
    if missing_paper_rules:
        block(
            "PAPER_RULE_BINDINGS_INCOMPLETE",
            f"{missing_paper_rules} mutation leaves have no normative paper rule binding.",
        )
    if missing_predictors:
        block(
            "PREDICTOR_BINDINGS_INCOMPLETE",
            f"{missing_predictors} mutation leaves have no counterfactual predictor binding.",
        )
    if counterfactual_gate_open:
        block("COUNTERFACTUAL_GATE_OPEN", "The independent C++ and Python evaluator gate remains open.")
    if not app_service_implemented:
        block("APPLICATION_SERVICE_ABSENT", "No C++ AsciiidApplicationService class exists.")
    if migrated_old_owners_remaining > 0:
        # Detail uses migrate-scoped counts only so a tombstone deletion
        # cannot masquerade as migrate-queue progress.
        block(
            "OLD_IMGUI_OWNERS_REMAIN",
            (
                f"Only {migrated_old_owners_deleted} of {migrate_rows} migrate rows "
                f"record old_owner_deleted=true "
                f"({migrated_old_owners_remaining} migrated old owners remain; "
                f"deleted_owner_records_total={deleted_owner_records_total} "
                f"includes non-migrate rows and is not readiness progress)."
            ),
        )

    return {
        "schema": "asciiid.pre_port_readiness.v1",
        "fl": "FL-4588",
        "ready_for_html_mutation": not blockers,
        "status": "READY" if not blockers else "BLOCKED",
        "metrics": metrics,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--debt-json",
        action="store_true",
        help=(
            "Emit the full backend-debt inventory (registry hash + exact "
            "arrays for missing typed mutation handlers, remaining migrated "
            "native owners, and unwired browser-draft interactions)."
        ),
    )
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    if args.debt_json:
        print(json.dumps(debt_report(), indent=2, sort_keys=True))
        return 0
    report = audit()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{report['status']}: FL-4588 browser mutation readiness; "
            f"blockers={len(report['blockers'])}"
        )
        for blocker in report["blockers"]:
            print(f"- {blocker['code']}: {blocker['detail']}")
    return 1 if args.require_ready and not report["ready_for_html_mutation"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
