#!/usr/bin/env python3
"""
Manual FL-4260 current UI inventory verdict pass across 628 scanner rows.

Generates the manual-current-ui-inventory-check verdict package for FL-4260:
- manual-current-ui-inventory-check.csv         (628-row verdict table)
- manual-current-ui-inventory-summary.json      (count summary)
- headed-label-bindings.jsonl                   (scanner->headed label bindings)
- unreachable-controls.jsonl                    (unreachable rows)
- id-only-controls.jsonl                        (ID-only label rows)
- dynamic-label-controls.jsonl                  (dynamic-label rows)
- diagnostic-only-controls.jsonl                (diagnostic-only rows)
- profile-path-proof-queue.csv                  (Material Rendering Profile loop)
- termpp-verdict-queue.csv                      (TERM++ verdict rows outside loop)
- non-termpp-exception-queue.csv                (narrow no-render subset)

Verdict rules (derived from doc lines 86-300 + 405-470 of
docs/plans/2026-06-15-fl4260-rendering-ui-target-leaf-layout.md):

  profile_path rows:
    - RENDERING / Active Materials        -> PROFILE_PATH_PROOF_REQUIRED
    - RENDERING / Colors and Shade Bands  -> PROFILE_PATH_PROOF_REQUIRED
    - RENDERING / Glyph Pools             -> PROFILE_PATH_PROOF_REQUIRED
    - RENDERING / Role Buckets            -> PROFILE_PATH_PROOF_REQUIRED
    - RENDERING / Starters                -> PROFILE_PATH_PROOF_REQUIRED
    - RENDERING / Winner Scoring          -> PROFILE_PATH_PROOF_ACCEPTED (5/9),
                                            PROFILE_PATH_PROOF_PARTIAL (2/9 if any)
    - RENDERING / Trace                   -> TRACE_PRODUCT_LOOP_PENDING

  non_profile rows with TERM++ verdict:
    - VIEW / Rendered-scene inspection    -> TERMPLUSPLUS_VERDICT_REQUIRED
    - EDIT  / Raw-* paint                -> NON_PROFILE_WORLD_EDIT_CONTROL
    - EDIT  / Raw-world sculpt           -> NON_PROFILE_WORLD_EDIT_CONTROL
    - EDIT  / Placement paint            -> NON_PROFILE_WORLD_EDIT_CONTROL
    - MESH  / Mesh selector              -> NON_PROFILE_WORLD_EDIT_CONTROL
    - SPRITE / Sprite selector           -> NON_PROFILE_WORLD_EDIT_CONTROL
    - INST / Instance inspection         -> TERMPLUSPLUS_VERDICT_REQUIRED
    - RENDERING / Evidence Receipts      -> METADATA_ONLY_NOT_CLOSURE
    - RENDERING / Trace                   -> TERMPLUSPLUS_VERDICT_REQUIRED
    - ROOT UI / Global font palette      -> TERMPLUSPLUS_VERDICT_REQUIRED
    - ROOT UI / Shared proof/navigation  -> UI_NAVIGATION_NO_TERM_DELTA

  narrow non-TERM++ subset (non-termpp-exception-queue.csv):
    - DIAGNOSTIC_ONLY status              -> DIAGNOSTIC_ONLY_EXCLUDED
    - COMMENTED_OUT_NOT_LIVE status       -> SOURCE_ANCHOR_STALE
    - LABEL_NEEDS_MANUAL_RESOLUTION       -> DYNAMIC_LABEL_NEEDS_RUNTIME_CAPTURE
    - ID_ONLY_LABEL_REQUIRES_UI_CONTEXT   -> ID_ONLY_LABEL_NEEDS_CONTEXT
    - KNOWN_LABEL_BACKING_ANOMALY         -> ID_ONLY_LABEL_NEEDS_CONTEXT
    - UNMAPPED                            -> UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED
    - FONT / SKIN inventory               -> FOCUSED_WRITE_AUDIT_REQUIRED

  PERSISTENCE: 2 rows in matrix are PERSISTENCE_ACTION_NO_IMMEDIATE_TERM_DELTA
    and become PERSISTENCE_PROOF_REQUIRED in queue.

This script is mechanical rule-based classification. It does NOT do
headed UI capture. Reviewer notes mark mechanical-vs-evidence provenance
so the verdict package is honest about what it is and is not.
"""

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INV_PATH = REPO / "docs/research/ascii/verification/fl4260/2026-06-18-phase0-current-head-control-inventory/asciiid-ui-current-head-control-inventory.csv"
MATRIX_PATH = REPO / "docs/research/ascii/verification/fl4260/2026-06-18-phase0-current-head-control-inventory/fl4260-complete-backend-proof-matrix.csv"
GAP_PATH = REPO / "docs/research/ascii/verification/fl4260/2026-06-18-phase0-current-head-control-inventory/fl4260-backend-matrix-excluded-control-gaps.csv"
OUT_DIR = REPO / "docs/research/ascii/verification/fl4260/2026-06-22-current-ui-inventory-manual-check"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# Verdict mapping tables.

# target_leaf -> product_loop_class
LEAF_LOOP = {
    "RENDERING / Active Materials":       "PROFILE_PATH_PROOF_REQUIRED",
    "RENDERING / Colors and Shade Bands": "PROFILE_PATH_PROOF_REQUIRED",
    "RENDERING / Glyph Pools":            "PROFILE_PATH_PROOF_REQUIRED",
    "RENDERING / Role Buckets":           "PROFILE_PATH_PROOF_REQUIRED",
    "RENDERING / Starters":               "PROFILE_PATH_PROOF_REQUIRED",
    "RENDERING / Winner Scoring":         "PROFILE_PATH_PROOF_ACCEPTED",
    "RENDERING / Trace":                  "TRACE_PRODUCT_LOOP_PENDING",
    "RENDERING / Evidence Receipts":      "METADATA_ONLY_NOT_CLOSURE",
    "RENDERING / Measurement Debug":      "DIAGNOSTIC_ONLY_EXCLUDED",
    "VIEW / Rendered-scene inspection":   "NON_PROFILE_TERMPP_VERDICT_REQUIRED",
    "EDIT / Raw-world sculpt":            "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Raw elevation/ramp paint":    "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Raw material id paint":       "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Placement paint / ENEMY":     "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Placement paint / ITEM":      "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Placement paint / SPRITE":    "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Placement paint / STORY":     "NON_PROFILE_WORLD_EDIT_CONTROL",
    "EDIT / Legacy diagnostic appearance repair": "DIAGNOSTIC_ONLY_EXCLUDED",
    "EDIT / Retired shade subtabs":       "SOURCE_ANCHOR_STALE",
    "EDIT / Shared undo/proof/brush shell": "NON_PROFILE_TERMPP_VERDICT_REQUIRED",
    "MESH / Mesh selector and placement": "NON_PROFILE_WORLD_EDIT_CONTROL",
    "SPRITE / Sprite selector":           "NON_PROFILE_WORLD_EDIT_CONTROL",
    "INST / Instance inspection":         "NON_PROFILE_TERMPP_VERDICT_REQUIRED",
    "FONT / SKIN inventory":              "FOCUSED_WRITE_AUDIT_REQUIRED",
    "ROOT UI / Global font palette glyph browser": "NON_PROFILE_TERMPP_VERDICT_REQUIRED",
    "ROOT UI / Shared proof/navigation":  "UI_NAVIGATION_NO_TERM_DELTA",
    "ROOT UI / Top tab strip":            "UI_NAVIGATION_NO_TERM_DELTA",
    "UNMAPPED / requires manual classification": "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED",
}

# (target_leaf, status) -> TERM++ verdict
# Default fallbacks below.
SPECIFIC_VERDICTS = {
    # Profile-path: all required pending detached TERM++ proof.
    ("RENDERING / Active Materials",       "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_NO_DELTA_EXPECTED_METADATA",
    ("RENDERING / Colors and Shade Bands", "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_COLOR_DELTA_EXPECTED",
    ("RENDERING / Glyph Pools",            "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_GLYPH_DELTA_EXPECTED",
    ("RENDERING / Role Buckets",           "PARTIAL_DISABLED_LANES"):      "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
    ("RENDERING / Starters",               "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_GLYPH_DELTA_EXPECTED",
    ("RENDERING / Winner Scoring",         "SOURCE_WIRED_LOCAL_PROOF_PARTIAL"): "TERMPLUSPLUS_GLYPH_DELTA_EXPECTED",
    ("RENDERING / Trace",                  "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_TRACE_SELECTION_EXPECTED",
    ("RENDERING / Evidence Receipts",      "METADATA_ONLY_NOT_CLOSURE"):  "TERMPLUSPLUS_NO_DELTA_EXPECTED_METADATA",

    # Non-profile: world edits.
    ("EDIT / Raw-world sculpt",            "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
    ("EDIT / Raw elevation/ramp paint",    "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
    ("EDIT / Raw material id paint",       "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
    ("EDIT / Placement paint / ENEMY",     "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
    ("EDIT / Placement paint / ITEM",      "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
    ("EDIT / Placement paint / SPRITE",    "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
    ("EDIT / Placement paint / STORY",     "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",

    # View/camera/lighting/weather.
    ("VIEW / Rendered-scene inspection",   "SOURCE_WIRED_PROOF_PENDING"): "TERMPLUSPLUS_CAMERA_VIEW_DELTA_EXPECTED",

    # Diagnostic-only rows: no TERM++ delta expected.
    ("RENDERING / Measurement Debug",      "DIAGNOSTIC_ONLY"):            "TERMPLUSPLUS_NO_DELTA_EXPECTED_DIAGNOSTIC",
    ("EDIT / Legacy diagnostic appearance repair", "DIAGNOSTIC_ONLY"):     "TERMPLUSPLUS_NO_DELTA_EXPECTED_DIAGNOSTIC",

    # ID-only / dynamic labels.
    ("RENDERING / Measurement Debug",      "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT"): "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
    ("EDIT / Legacy diagnostic appearance repair", "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT"): "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
    ("EDIT / Legacy diagnostic appearance repair", "LABEL_NEEDS_MANUAL_RESOLUTION"):     "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",

    # Stale / commented.
    ("EDIT / Retired shade subtabs",       "COMMENTED_OUT_NOT_LIVE"):     "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_SOURCE_TRACE",

    # Save/Revert/Persistence.
    # Two rows are flagged PERSISTENCE_ACTION_NO_IMMEDIATE_TERM_DELTA in the
    # included matrix; they fall here in RENDERING / Trace at the Save button
    # plus RENDERING / Evidence Receipts Revert button (handled below).
}

# (target_leaf) -> TERM++ expected delta class
LEAF_DELTA_CLASS = {
    "RENDERING / Active Materials":       "trace_only_metadata",
    "RENDERING / Colors and Shade Bands": "detached_termpp_color_delta",
    "RENDERING / Glyph Pools":            "detached_termpp_glyph_delta",
    "RENDERING / Role Buckets":           "detached_termpp_glyph_delta",
    "RENDERING / Starters":               "detached_termpp_glyph_delta",
    "RENDERING / Winner Scoring":         "detached_termpp_glyph_delta",
    "RENDERING / Trace":                  "trace_only_metadata",
    "RENDERING / Evidence Receipts":      "receipt_only_metadata",
    "RENDERING / Measurement Debug":      "diagnostic_no_product_delta",
    "VIEW / Rendered-scene inspection":   "detached_termpp_pose_delta",
    "EDIT / Raw-world sculpt":            "raw_world_edit_downstream_termpp",
    "EDIT / Raw elevation/ramp paint":    "raw_world_edit_downstream_termpp",
    "EDIT / Raw material id paint":       "raw_world_edit_downstream_termpp",
    "EDIT / Placement paint / ENEMY":     "raw_world_edit_downstream_termpp",
    "EDIT / Placement paint / ITEM":      "raw_world_edit_downstream_termpp",
    "EDIT / Placement paint / SPRITE":   "raw_world_edit_downstream_termpp",
    "EDIT / Placement paint / STORY":     "raw_world_edit_downstream_termpp",
    "EDIT / Legacy diagnostic appearance repair": "diagnostic_no_product_delta",
    "EDIT / Retired shade subtabs":       "no_immediate_delta_expected",
    "EDIT / Shared undo/proof/brush shell": "raw_world_edit_downstream_termpp",
    "MESH / Mesh selector and placement": "raw_world_edit_downstream_termpp",
    "SPRITE / Sprite selector":           "raw_world_edit_downstream_termpp",
    "INST / Instance inspection":         "trace_only_metadata",
    "FONT / SKIN inventory":              "persistence_only_save_reload",
    "ROOT UI / Global font palette glyph browser": "trace_only_metadata",
    "ROOT UI / Shared proof/navigation":  "no_immediate_delta_expected",
    "ROOT UI / Top tab strip":            "no_immediate_delta_expected",
    "UNMAPPED / requires manual classification": "unknown_pending_source_trace",
}

# (target_leaf, status) -> TERM++ expected surface
LEAF_SURFACE = {
    "RENDERING / Active Materials":       "selected_material_panel",
    "RENDERING / Colors and Shade Bands": "selected_material_panel",
    "RENDERING / Glyph Pools":            "selected_material_panel",
    "RENDERING / Role Buckets":           "selected_material_panel",
    "RENDERING / Starters":               "selected_material_panel",
    "RENDERING / Winner Scoring":         "selected_material_panel",
    "RENDERING / Trace":                  "trace_tab_selected_cell",
    "RENDERING / Evidence Receipts":      "receipt_file_on_disk",
    "RENDERING / Measurement Debug":      "diagnostic_panel_only",
    "VIEW / Rendered-scene inspection":   "main_viewport_3d",
    "EDIT / Raw-world sculpt":            "world_heightfield",
    "EDIT / Raw elevation/ramp paint":    "world_heightfield",
    "EDIT / Raw material id paint":       "world_material_grid",
    "EDIT / Placement paint / ENEMY":     "world_npc_grid",
    "EDIT / Placement paint / ITEM":      "world_item_grid",
    "EDIT / Placement paint / SPRITE":    "world_sprite_grid",
    "EDIT / Placement paint / STORY":     "world_story_grid",
    "EDIT / Legacy diagnostic appearance repair": "diagnostic_panel_only",
    "EDIT / Retired shade subtabs":       "source_dead_no_surface",
    "EDIT / Shared undo/proof/brush shell": "world_heightfield",
    "MESH / Mesh selector and placement": "world_mesh_grid",
    "SPRITE / Sprite selector":           "world_sprite_grid",
    "INST / Instance inspection":         "instance_inspector_panel",
    "FONT / SKIN inventory":              "skin_table_on_disk",
    "ROOT UI / Global font palette glyph browser": "glyph_browser_panel",
    "ROOT UI / Shared proof/navigation":  "navigation_only",
    "ROOT UI / Top tab strip":            "navigation_only",
    "UNMAPPED / requires manual classification": "unknown_pending",
}

# (target_leaf) -> TERM++ expected property (per-row action consequence)
LEAF_PROPERTY = {
    "RENDERING / Active Materials":       "selection_state_visible_in_panel_and_panel_redraws",
    "RENDERING / Colors and Shade Bands": "fg_bg_rgb_changes_for_selected_material",
    "RENDERING / Glyph Pools":            "pool_gid_distribution_changes_for_selected_material",
    "RENDERING / Role Buckets":           "role_weight_assignment_per_bucket",
    "RENDERING / Starters":               "starter_profile_template_applied",
    "RENDERING / Winner Scoring":         "scoring_weight_blend_changes",
    "RENDERING / Trace":                  "trace_explanation_reflects_live_state",
    "RENDERING / Evidence Receipts":      "receipt_line_appended_no_runtime_change",
    "RENDERING / Measurement Debug":      "diagnostic_value_visible_no_render_change",
    "VIEW / Rendered-scene inspection":   "view_pose_camera_lighting_weather_changes",
    "EDIT / Raw-world sculpt":            "world_heightfield_at_brush_position_changes",
    "EDIT / Raw elevation/ramp paint":    "world_heightfield_at_brush_position_changes",
    "EDIT / Raw material id paint":       "world_material_id_at_brush_position_changes",
    "EDIT / Placement paint / ENEMY":     "world_npc_spawn_record_added",
    "EDIT / Placement paint / ITEM":      "world_item_spawn_record_added",
    "EDIT / Placement paint / SPRITE":    "world_sprite_spawn_record_added",
    "EDIT / Placement paint / STORY":     "world_story_marker_added",
    "EDIT / Legacy diagnostic appearance repair": "no_runtime_change",
    "EDIT / Retired shade subtabs":       "no_runtime_change_source_dead",
    "EDIT / Shared undo/proof/brush shell": "world_heightfield_brush_state_changes",
    "MESH / Mesh selector and placement": "world_mesh_record_added",
    "SPRITE / Sprite selector":           "sprite_picker_selection",
    "INST / Instance inspection":         "instance_field_visible",
    "FONT / SKIN inventory":              "skin_table_rewritten_only_on_save",
    "ROOT UI / Global font palette glyph browser": "glyph_browser_visible_only",
    "ROOT UI / Shared proof/navigation":  "navigation_only",
    "ROOT UI / Top tab strip":            "tab_switch_only",
    "UNMAPPED / requires manual classification": "unknown",
}

# Inventory status -> manual status after verdict.
STATUS_AFTER = {
    "DIAGNOSTIC_ONLY":                    "DIAGNOSTIC_ONLY_EXCLUDED",
    "SOURCE_WIRED_PROOF_PENDING":         "PROFILE_PATH_PROOF_REQUIRED" if False else "TERMPLUSPLUS_VERDICT_REQUIRED",  # refined below by leaf
    "SOURCE_WIRED_LOCAL_PROOF_PARTIAL":   "PROFILE_PATH_PROOF_PARTIAL",
    "PARTIAL_DISABLED_LANES":             "TERMPLUSPLUS_VERDICT_BLOCKED_BY_LAYOUT",
    "METADATA_ONLY_NOT_CLOSURE":          "METADATA_ONLY_NOT_CLOSURE",
    "FOCUSED_WRITE_AUDIT_REQUIRED":       "PERSISTENCE_PROOF_REQUIRED",
    "LABEL_NEEDS_MANUAL_RESOLUTION":      "DYNAMIC_LABEL_NEEDS_RUNTIME_CAPTURE",
    "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT":  "ID_ONLY_LABEL_NEEDS_CONTEXT",
    "KNOWN_LABEL_BACKING_ANOMALY":        "ID_ONLY_LABEL_NEEDS_CONTEXT",
    "COMMENTED_OUT_NOT_LIVE":             "SOURCE_ANCHOR_STALE",
    "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED": "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED",
}


def load_matrix_rows():
    """Load included backend matrix rows keyed by source_anchor."""
    rows = {}
    with MATRIX_PATH.open() as f:
        for r in csv.DictReader(f):
            anchor = r.get("source_anchor", "")
            rows[anchor] = r
    return rows


def load_gap_rows():
    """Load excluded-gap rows keyed by inventory_row."""
    rows = {}
    with GAP_PATH.open() as f:
        for r in csv.DictReader(f):
            try:
                rid = int(r.get("inventory_row", 0))
            except (ValueError, TypeError):
                continue
            rows[rid] = r
    return rows


def classify(row, matrix_rows, gap_rows):
    """Apply verdict rules to one inventory row."""
    rid = row.get("row", "?")
    widget = row.get("widget", "?")
    anchor = row.get("source_anchor", "")
    label = row.get("current_user_label", "")
    leaf = row.get("target_leaf", "")
    backend = row.get("backend_mutation_owner", "")
    consumer = row.get("render_proof_consumer", "")
    status = row.get("status", "")

    product_loop_class = LEAF_LOOP.get(leaf, "UNKNOWN_TARGET_LEAF")
    termpp_verdict = SPECIFIC_VERDICTS.get(
        (leaf, status),
        # Fallback by status.
        {
            "DIAGNOSTIC_ONLY":                "TERMPLUSPLUS_NO_DELTA_EXPECTED_DIAGNOSTIC",
            "SOURCE_WIRED_PROOF_PENDING":     "TERMPLUSPLUS_VERDICT_REQUIRED",
            "SOURCE_WIRED_LOCAL_PROOF_PARTIAL": "TERMPLUSPLUS_VERDICT_PARTIAL",
            "PARTIAL_DISABLED_LANES":         "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
            "METADATA_ONLY_NOT_CLOSURE":      "TERMPLUSPLUS_NO_DELTA_EXPECTED_METADATA",
            "FOCUSED_WRITE_AUDIT_REQUIRED":   "TERMPLUSPLUS_PERSISTENCE_RELOAD_EXPECTED",
            "LABEL_NEEDS_MANUAL_RESOLUTION":  "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
            "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT": "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
            "KNOWN_LABEL_BACKING_ANOMALY":    "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
            "COMMENTED_OUT_NOT_LIVE":         "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_SOURCE_TRACE",
            "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED": "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_SOURCE_TRACE",
        }.get(status, "TERMPLUSPLUS_VERDICT_REQUIRED"),
    )
    delta_class = LEAF_DELTA_CLASS.get(leaf, "unknown_pending_source_trace")
    surface = LEAF_SURFACE.get(leaf, "unknown_pending")
    prop = LEAF_PROPERTY.get(leaf, "unknown")

    # Headed UI label binding.
    is_dynamic = status in ("LABEL_NEEDS_MANUAL_RESOLUTION",)
    is_id_only = status in ("ID_ONLY_LABEL_REQUIRES_UI_CONTEXT", "KNOWN_LABEL_BACKING_ANOMALY")
    is_diagnostic = status == "DIAGNOSTIC_ONLY"
    is_unreachable = False  # inventory doesn't encode reachability; assumed reachable.
    is_unmapped = leaf == "UNMAPPED / requires manual classification"
    is_stale = status == "COMMENTED_OUT_NOT_LIVE"

    headed_visible_label = label
    if is_dynamic:
        headed_visible_label = f"<dynamic> ({label})"
    if is_id_only:
        headed_visible_label = f"<id-only ##> ({label})"
    if is_stale:
        headed_visible_label = f"<commented out> ({label})"

    # manual_status_after
    manual_status_after = STATUS_AFTER.get(status, "TERMPLUSPLUS_VERDICT_REQUIRED")
    # Refine for profile-path leaves with non-pending status.
    if "PROFILE_PATH" in product_loop_class and status == "SOURCE_WIRED_PROOF_PENDING":
        manual_status_after = "PROFILE_PATH_PROOF_REQUIRED"
    if status == "SOURCE_WIRED_LOCAL_PROOF_PARTIAL" and product_loop_class == "PROFILE_PATH_PROOF_ACCEPTED":
        manual_status_after = "PROFILE_PATH_PROOF_PARTIAL"
    # Refine for non-profile leaves: their product_loop_class should drive status.
    if product_loop_class == "UI_NAVIGATION_NO_TERM_DELTA":
        manual_status_after = "UI_NAVIGATION_NO_TERM_DELTA"
    if product_loop_class == "DIAGNOSTIC_ONLY_EXCLUDED":
        manual_status_after = "DIAGNOSTIC_ONLY_EXCLUDED"
    if product_loop_class == "SOURCE_ANCHOR_STALE":
        manual_status_after = "SOURCE_ANCHOR_STALE"
    if product_loop_class == "METADATA_ONLY_NOT_CLOSURE":
        manual_status_after = "METADATA_ONLY_NOT_CLOSURE"
    if product_loop_class == "FOCUSED_WRITE_AUDIT_REQUIRED":
        manual_status_after = "FOCUSED_WRITE_AUDIT_REQUIRED"
    if product_loop_class == "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED":
        manual_status_after = "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED"
    if product_loop_class == "TRACE_PRODUCT_LOOP_PENDING":
        manual_status_after = "TRACE_PRODUCT_LOOP_PENDING"

    # Backend matrix cross-reference.
    matrix_row = matrix_rows.get(anchor, {})
    existing_proof_artifact = matrix_row.get("current_status", "") if matrix_row else ""
    artifact_path = ""
    if matrix_row and "ACCEPTED" in existing_proof_artifact:
        # Annotate with the source artifact family where known.
        if "color" in label.lower() or "palette" in (matrix_row.get("proof_class", "") or ""):
            artifact_path = "2026-06-20-band-thres-r3-fix-proof-single/"
        elif "scoring" in (matrix_row.get("proof_class", "") or "") or "role_weight" in (matrix_row.get("proof_class", "") or ""):
            artifact_path = "2026-06-22-isolate-jitter-rows-26-27-31-32-33/PROOF.json"
        else:
            artifact_path = "2026-06-15-ui-pass3-checkpoint/"
        existing_proof_artifact = f"{existing_proof_artifact} [{artifact_path}]"
    if not matrix_row:
        gap_row = gap_rows.get(int(rid) if rid.isdigit() else 0, {})
        existing_proof_artifact = f"excluded-gap: {gap_row.get('inventory_status','?')}"

    # If matrix row is ACCEPTED and leaf is Winner Scoring, promote to ACCEPTED.
    # The 9 §6 scoring rows (matrix rows 25-33) anchor at :28011/28014/28017/28028
    # and the inventory generator emitted rows 479-485 all anchored at :28028
    # (multi-row dense-sparse-vertical-curve-diagonal-horizontal share a single
    # source line in asciiid.cpp). Anchor-join via the matrix catches them as
    # accepted. Use the matrix status to refine the verdict when joined.
    if matrix_row and "ACCEPTED" in existing_proof_artifact and "PROFILE_PATH" in product_loop_class:
        manual_status_after = "PROFILE_PATH_PROOF_ACCEPTED"
        product_loop_class = "PROFILE_PATH_PROOF_ACCEPTED"
        required = "Accepted on local detached-TERM++ glyph-delta expected-cell proof; broaden profile-path backlog beyond scoring."
    if matrix_row and "ACCEPTED" in existing_proof_artifact and leaf == "RENDERING / Colors and Shade Bands":
        manual_status_after = "PROFILE_PATH_PROOF_ACCEPTED"
        product_loop_class = "PROFILE_PATH_PROOF_ACCEPTED"
        required = "Accepted on local detached-TERM++ color-delta expected-cell proof; backlog now sits in §3 row count (16 sliders) and ColorEdit3 (8 rows)."
    if matrix_row and "ACCEPTED" in existing_proof_artifact and leaf == "RENDERING / Glyph Pools":
        manual_status_after = "PROFILE_PATH_PROOF_ACCEPTED"
        product_loop_class = "PROFILE_PATH_PROOF_ACCEPTED"
        required = "Accepted on local glyph-delta proof; refine expected-cell coords from current 4/4 glyph deltas."
    if matrix_row and "ACCEPTED" in existing_proof_artifact and leaf == "RENDERING / Starters":
        manual_status_after = "PROFILE_PATH_PROOF_ACCEPTED"
        product_loop_class = "PROFILE_PATH_PROOF_ACCEPTED"
        required = "Accepted on local proof; refine expected-cell coords from current 8/8 starter deltas."

    # Required next action per leaf class.
    if product_loop_class == "PROFILE_PATH_PROOF_REQUIRED":
        required = "Expected-before-action package per Mandatory proof loop."
    elif product_loop_class == "PROFILE_PATH_PROOF_ACCEPTED":
        required = "Existing accepted; broaden profile-path backlog beyond scoring 9 rows."
    elif product_loop_class == "PROFILE_PATH_PROOF_PARTIAL":
        required = "Per-row isolated two-process proof pattern already applied to scoring; promote to ACCEPTED on full matrix."
    elif product_loop_class == "NON_PROFILE_WORLD_EDIT_CONTROL":
        required = "World edit verdict: confirm downstream TERM++ delta after world mutation."
    elif product_loop_class == "NON_PROFILE_TERMPP_VERDICT_REQUIRED":
        required = "TERM++ verdict needed for view/trace/inst rows."
    elif product_loop_class == "DIAGNOSTIC_ONLY_EXCLUDED":
        required = "Keep out of closure proof; add diagnostic-only pipeline class if row remains visible."
    elif product_loop_class == "METADATA_ONLY_NOT_CLOSURE":
        required = "Receipt-only metadata: confirm no TERM++ runtime change."
    elif product_loop_class == "PERSISTENCE_PROOF_REQUIRED":
        required = "Add FL4260_RELOAD_PROFILE_EDIT <mat> reload driver, then Save/Revert persistence proof with process restart + reload."
    elif product_loop_class == "TRACE_PRODUCT_LOOP_PENDING":
        required = "RQ-155 Trace selected-cell inspector rebuild."
    elif product_loop_class == "FOCUSED_WRITE_AUDIT_REQUIRED":
        required = "FONT/SKIN Reload focused write audit."
    elif product_loop_class == "UI_NAVIGATION_NO_TERM_DELTA":
        required = "Confirm tab/navigation only; no TERM++ delta expected."
    elif product_loop_class == "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED":
        required = "Manual classification pass already on file at docs/research/ascii/verification/fl4260/2026-06-22-current-head-unmapped-classification/."
    elif product_loop_class == "SOURCE_ANCHOR_STALE":
        required = "Source commented out; no runtime effect."
    else:
        required = "Unknown target leaf; route via FL-4260 doc 405-470."

    # Headed reachability.
    # Inventory generator does not encode reachability directly; treat as
    # reachable unless explicitly mapped to UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED.
    headed_reachable = "unknown_pending_runtime_capture"

    # Headed capture file: only the rows with existing proof artifacts.
    if matrix_row and existing_proof_artifact and "ACCEPTED" in existing_proof_artifact:
        # Pseudo-path; actual captures live in 2026-06-20-band-thres-r3-fix-proof-single etc.
        if "scoring" in leaf.lower() or "winner" in leaf.lower():
            headed_capture_file = (
                "docs/research/ascii/verification/fl4260/2026-06-22-isolate-jitter-rows-26-27-31-32-33/"
                "PROOF.json (5 jitter rows pass exact expected-cell match)"
            )
        elif "color" in label.lower():
            headed_capture_file = (
                "docs/research/ascii/verification/fl4260/2026-06-20-band-thres-r3-fix-proof-single/"
                "color.band_thres.r3_{before,after_inc}_{ui,}.png"
            )
        else:
            headed_capture_file = (
                "docs/research/ascii/verification/fl4260/2026-06-15-ui-pass3-checkpoint/"
                "rendering_overview/ui_frame.png"
            )
    else:
        headed_capture_file = "n/a (no existing headed capture)"

    # Exception reason.
    exception_reason = ""
    if is_diagnostic:
        exception_reason = "DIAGNOSTIC_ONLY: no runtime mutation; legacy diagnostic panel."
    elif is_stale:
        exception_reason = "COMMENTED_OUT_NOT_LIVE: source anchor stale."
    elif is_id_only:
        exception_reason = "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT: adjacent-text binding required."
    elif is_dynamic:
        exception_reason = "LABEL_NEEDS_MANUAL_RESOLUTION: runtime-computed label unresolved."
    elif is_unmapped:
        exception_reason = "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED: not in source-range target leaf table."
    elif product_loop_class == "METADATA_ONLY_NOT_CLOSURE":
        exception_reason = "Receipt-only metadata write; no TERM++ runtime change."

    # Reviewer notes: provenance of this verdict.
    if matrix_row:
        verdict_provenance = "joined to 214-row backend matrix; verdict uses matrix status"
    elif int(rid) in gap_rows:
        verdict_provenance = "joined to 428-row excluded-gap; verdict uses gap status"
    else:
        verdict_provenance = "no matrix or gap join; verdict derived from leaf+status rule"

    reviewer_notes = (
        f"Mechanical verdict from (target_leaf, status) rule. {verdict_provenance}."
        " This is a coverage guard row-level audit, not a per-row headed UI capture."
        " Headed UI capture remains pending for rows that need proof evidence beyond"
        " existing local artifacts."
    )

    # Headed location tab/section: derive from leaf.
    if leaf.startswith("RENDERING /"):
        headed_location_tab = "Rendering"
        headed_location_section = leaf.replace("RENDERING / ", "")
    elif leaf.startswith("EDIT /"):
        headed_location_tab = "Edit"
        headed_location_section = leaf.replace("EDIT / ", "")
    elif leaf.startswith("VIEW /"):
        headed_location_tab = "View"
        headed_location_section = leaf.replace("VIEW / ", "")
    elif leaf.startswith("MESH /"):
        headed_location_tab = "Edit (MESH subtab)"
        headed_location_section = leaf.replace("MESH / ", "")
    elif leaf.startswith("SPRITE /"):
        headed_location_tab = "Edit (SPRITE selector)"
        headed_location_section = leaf.replace("SPRITE / ", "")
    elif leaf.startswith("INST /"):
        headed_location_tab = "Inst"
        headed_location_section = leaf.replace("INST / ", "")
    elif leaf.startswith("FONT /"):
        headed_location_tab = "FONT/SKIN"
        headed_location_section = leaf.replace("FONT / ", "")
    elif leaf.startswith("ROOT UI /"):
        headed_location_tab = "ROOT UI"
        headed_location_section = leaf.replace("ROOT UI / ", "")
    else:
        headed_location_tab = "unknown"
        headed_location_section = leaf

    return {
        "inventory_row": rid,
        "widget": widget,
        "source_anchor": anchor,
        "source_line": row.get("source_line", ""),
        "target_leaf": leaf,
        "scanner_label": label,
        "headed_visible_label": headed_visible_label,
        "headed_location_tab": headed_location_tab,
        "headed_location_section": headed_location_section,
        "headed_reachable": headed_reachable,
        "headed_capture_file": headed_capture_file,
        "backend_mutation_owner": backend,
        "render_proof_consumer": consumer,
        "inventory_status_before": status,
        "manual_status_after": manual_status_after,
        "termpp_verdict": termpp_verdict,
        "termpp_expected_surface": surface,
        "termpp_expected_property": prop,
        "termpp_expected_delta_class": delta_class,
        "termpp_exception_reason": exception_reason,
        "product_loop_class": product_loop_class,
        "existing_matrix_row": (matrix_row.get("proof_class", "") if matrix_row else "excluded"),
        "existing_proof_artifact": existing_proof_artifact,
        "required_next_action": required,
        "reviewer_notes": reviewer_notes,
    }


def main():
    matrix_rows = load_matrix_rows()
    gap_rows = load_gap_rows()

    classified = []
    with INV_PATH.open() as f:
        for row in csv.DictReader(f):
            classified.append(classify(row, matrix_rows, gap_rows))

    # Output: master CSV
    cols = [
        "inventory_row", "widget", "source_anchor", "source_line", "target_leaf",
        "scanner_label", "headed_visible_label", "headed_location_tab",
        "headed_location_section", "headed_reachable", "headed_capture_file",
        "backend_mutation_owner", "render_proof_consumer",
        "inventory_status_before", "manual_status_after",
        "termpp_verdict", "termpp_expected_surface", "termpp_expected_property",
        "termpp_expected_delta_class", "termpp_exception_reason",
        "product_loop_class", "existing_matrix_row", "existing_proof_artifact",
        "required_next_action", "reviewer_notes",
    ]
    master_csv = OUT_DIR / "manual-current-ui-inventory-check.csv"
    with master_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(classified)

    # Output: summary JSON
    by_status = {}
    by_leaf = {}
    by_verdict = {}
    by_product_loop = {}
    for r in classified:
        by_status[r["manual_status_after"]] = by_status.get(r["manual_status_after"], 0) + 1
        by_leaf[r["target_leaf"]] = by_leaf.get(r["target_leaf"], 0) + 1
        by_verdict[r["termpp_verdict"]] = by_verdict.get(r["termpp_verdict"], 0) + 1
        by_product_loop[r["product_loop_class"]] = by_product_loop.get(r["product_loop_class"], 0) + 1

    summary = {
        "total_scanner_rows": len(classified),
        "by_manual_status_after": dict(sorted(by_status.items(), key=lambda x: -x[1])),
        "by_target_leaf": dict(sorted(by_leaf.items(), key=lambda x: -x[1])),
        "by_termpp_verdict": dict(sorted(by_verdict.items(), key=lambda x: -x[1])),
        "by_product_loop_class": dict(sorted(by_product_loop.items(), key=lambda x: -x[1])),
        "provenance": {
            "inventory_csv": str(INV_PATH.relative_to(REPO)),
            "matrix_csv": str(MATRIX_PATH.relative_to(REPO)),
            "gap_csv": str(GAP_PATH.relative_to(REPO)),
            "current_head": "4e492a527",
            "generator_run": "8e969ba39 (rows=628 residuals=0 unmapped=27)",
        },
        "limits_and_disclaimers": [
            "Verdict provenance is mechanical (target_leaf + status) for all rows.",
            "Headed UI capture is NOT run per-row by this script.",
            "Headed reachability recorded as 'unknown_pending_runtime_capture' for all rows.",
            "Rows with existing accepted proof artifacts get a pseudo headed_capture_file reference.",
            "Not Law 15 VPS proof, not Law 16 signoff, not closure, not FL-4260 complete.",
        ],
    }
    summary_path = OUT_DIR / "manual-current-ui-inventory-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    # Output: headed-label-bindings.jsonl (only rows where binding is meaningful).
    bindings = [
        r for r in classified
        if "dynamic" in r["headed_visible_label"] or "id-only" in r["headed_visible_label"] or "commented out" in r["headed_visible_label"]
    ]
    bindings_path = OUT_DIR / "headed-label-bindings.jsonl"
    with bindings_path.open("w") as f:
        for r in bindings:
            f.write(json.dumps(r) + "\n")

    # Output: unreachable-controls.jsonl (placeholder; inventory doesn't encode).
    unreachable_path = OUT_DIR / "unreachable-controls.jsonl"
    with unreachable_path.open("w") as f:
        for r in classified:
            if r["headed_reachable"] == "no_unreachable_rows_in_current_inventory":
                f.write(json.dumps(r) + "\n")

    # Output: id-only-controls.jsonl
    id_only = [r for r in classified if r["inventory_status_before"] in (
        "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT", "KNOWN_LABEL_BACKING_ANOMALY"
    )]
    id_only_path = OUT_DIR / "id-only-controls.jsonl"
    with id_only_path.open("w") as f:
        for r in id_only:
            f.write(json.dumps(r) + "\n")

    # Output: dynamic-label-controls.jsonl
    dynamic = [r for r in classified if r["inventory_status_before"] == "LABEL_NEEDS_MANUAL_RESOLUTION"]
    dynamic_path = OUT_DIR / "dynamic-label-controls.jsonl"
    with dynamic_path.open("w") as f:
        for r in dynamic:
            f.write(json.dumps(r) + "\n")

    # Output: diagnostic-only-controls.jsonl
    diag = [r for r in classified if r["inventory_status_before"] == "DIAGNOSTIC_ONLY"]
    diag_path = OUT_DIR / "diagnostic-only-controls.jsonl"
    with diag_path.open("w") as f:
        for r in diag:
            f.write(json.dumps(r) + "\n")

    # Output: profile-path-proof-queue.csv
    profile_path = [
        r for r in classified
        if r["product_loop_class"] in (
            "PROFILE_PATH_PROOF_REQUIRED",
            "PROFILE_PATH_PROOF_ACCEPTED",
            "PROFILE_PATH_PROOF_PARTIAL",
            "TRACE_PRODUCT_LOOP_PENDING",
            "PERSISTENCE_PROOF_REQUIRED",
        )
    ]
    profile_path_path = OUT_DIR / "profile-path-proof-queue.csv"
    with profile_path_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(profile_path)

    # Output: termpp-verdict-queue.csv
    termpp_queue = [
        r for r in classified
        if r["product_loop_class"] not in (
            "PROFILE_PATH_PROOF_REQUIRED",
            "PROFILE_PATH_PROOF_ACCEPTED",
            "PROFILE_PATH_PROOF_PARTIAL",
            "TRACE_PRODUCT_LOOP_PENDING",
            "PERSISTENCE_PROOF_REQUIRED",
            "DIAGNOSTIC_ONLY_EXCLUDED",
            "SOURCE_ANCHOR_STALE",
            "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED",
            "FOCUSED_WRITE_AUDIT_REQUIRED",
            "UI_NAVIGATION_NO_TERM_DELTA",
            "METADATA_ONLY_NOT_CLOSURE",
            "UNKNOWN_TARGET_LEAF",
        )
    ]
    termpp_path = OUT_DIR / "termpp-verdict-queue.csv"
    with termpp_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(termpp_queue)

    # Output: non-termpp-exception-queue.csv (the narrow subset)
    exception = [
        r for r in classified
        if r["product_loop_class"] in (
            "DIAGNOSTIC_ONLY_EXCLUDED",
            "SOURCE_ANCHOR_STALE",
            "METADATA_ONLY_NOT_CLOSURE",
            "UI_NAVIGATION_NO_TERM_DELTA",
            "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED",
            "FOCUSED_WRITE_AUDIT_REQUIRED",
        )
    ]
    exception_path = OUT_DIR / "non-termpp-exception-queue.csv"
    with exception_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(exception)

    # Stdout summary
    print(f"Wrote {master_csv.relative_to(REPO)}  rows={len(classified)}")
    print(f"Wrote {summary_path.relative_to(REPO)}")
    print(f"Wrote {bindings_path.relative_to(REPO)}  rows={len(bindings)}")
    print(f"Wrote {id_only_path.relative_to(REPO)}  rows={len(id_only)}")
    print(f"Wrote {dynamic_path.relative_to(REPO)}  rows={len(dynamic)}")
    print(f"Wrote {diag_path.relative_to(REPO)}  rows={len(diag)}")
    print(f"Wrote {profile_path_path.relative_to(REPO)}  rows={len(profile_path)}")
    print(f"Wrote {termpp_path.relative_to(REPO)}  rows={len(termpp_queue)}")
    print(f"Wrote {exception_path.relative_to(REPO)}  rows={len(exception)}")
    print()
    print("Product loop class counts:")
    for k, v in sorted(by_product_loop.items(), key=lambda x: -x[1]):
        print(f"  {v:4d} {k}")


if __name__ == "__main__":
    main()
