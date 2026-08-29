"""Shared schema constants for the failure log overlay fields.

Single source of truth for overlay field names and area values used by
analyze_failure_log.py and maintainer/lib/failure_log.py.
Import these instead of defining duplicate tuples/frozensets in each tool.
"""
from __future__ import annotations

# Overlay fields whose JSON values are normalised to list[str].
# Order matches the canonical overlay record key order.
FL_OVERLAY_LIST_FIELDS = (
    "Subsystems",
    "Kinds",
    "ComplaintRefs",
    "RQRefs",
    "GitHubRefs",
    "CodeRefs",
    "TouchedFiles",
    "RequiredFields",
    "AnchorFunctions",
)

# Valid values for the "Kinds" overlay field.
FL_OVERLAY_KIND_VALUES = frozenset(
    {
        "adr",
        "data_loss",
        "context",
        "state_corruption",
        "protocol_violation",
        "proof_gap",
        "process_failure",
        "ux_bug",
        "tooling_gap",
        "doc_drift",
        "regression",
        "performance",
        "config_error",
        "missing_feature",
        "security",
        "open_bug",
        "historical_gate_row",
        "runtime_bug",
        "verification_gap",
        "observability_gap",
        "test_gap",
        "structural_gap",
        "structural_hazard",
        "bug_fix",
        "partial_fix",
        "implemented",
        "resolved",
        "planned",
        "deferred",
        "cleanup",
        "investigation",
        "refactor",
        "user_report",
        "unclassified",
    }
)

# Overlay fields whose JSON values are normalised as structured objects (not list[str]).
_FL_OVERLAY_STRUCTURED_FIELDS = ("FixAttempts",)

# Valid values for the "Area" overlay field.
FL_OVERLAY_AREA_VALUES = frozenset(
    {
        "asset_pipeline",
        "config_status",
        "docs_process",
        "engine",
        "failure_log",
        "gameplay",
        "installer",
        "iso_viewer",
        "launcher",
        "map_assets",
        "multiplayer",
        "pipeline",
        "process",
        "tooling",
    }
)
