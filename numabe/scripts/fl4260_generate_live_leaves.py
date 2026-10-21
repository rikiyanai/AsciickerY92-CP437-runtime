#!/usr/bin/env python3
# =====================================================================
# fl4260_generate_live_leaves.py
# =====================================================================
# PROBLEM THIS SCRIPT SOLVES (FL-4564 migration, step 1):
#
# The ASCIID Material Look editor has ~320 user-visible controls
# (buttons, sliders, color pickers...). Each one is a row in the
# canonical spreadsheet:
#
#     docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv
#
# The FL-4564 "closed graph" contract (see fl4260/README.md) says every
# proof-critical row must resolve to a LIVE LEAF file — a small markdown
# document at the row's `leaf_path`, directly under the fl4260/ root —
# that records who owns the control in source code, what the user should
# see when they use it, and how a proof of that behavior is run.
#
# Before this script ran, only 2 of 320 rows (the R1 pinned controls,
# rows 429 and 432) had live leaves. 271 rows only had HISTORICAL leaves
# inside the dated browse tree `2026-06-22-current-ui-inventory-tree/`
# (which the contract explicitly forbids using as proof authority), and
# 47 rows had no leaf anywhere.
#
# WHAT THIS SCRIPT DOES:
#
#   for each CSV row without a live leaf:
#       merge  (a) the CSV row      -> identity + expected-result fields
#       with   (b) the dated leaf   -> source anchors + evidence refs
#                                      (only if a dated leaf exists)
#       emit   a live leaf markdown file at fl4260/<leaf_path>
#
# HONESTY RULES (no fabrication):
#   * Every field in a generated leaf comes verbatim from the CSV or the
#     dated leaf. Nothing is guessed or invented.
#   * A field that exists in neither source is written as the literal
#     token "MISSING (authoring required)" so a later human/agent knows
#     the spec gap is real, not an oversight.
#   * Generated leaves are labeled GENERATED and PROOF OPEN. Generating
#     a leaf does NOT mean the control's behavior is proven — it means
#     the row's spec surface now exists in the sanctioned location.
#   * The two hand-authored R1 leaves (rows 429/432) are never touched.
#
# OUTPUTS:
#   1. One live leaf .md per migrated row, at fl4260/<leaf_path>.
#   2. fl4260/INVENTORY_GRAPH.json — a machine-readable index mapping
#      every row to its leaf, the leaf's generation state, and the exact
#      list of fields that are still missing. This is the "closed graph"
#      index the 2026-07-07 process audit asked for (consolidation
#      task 1), in generated-and-regenerable form.
#
# RE-RUNNING IS SAFE: existing live leaves are skipped unless --force is
# given, and the JSON index is deterministic (keyed to the CSV content
# hash, no timestamps) so re-runs produce byte-identical output when
# nothing changed.
#
# Run with:
#   python3 scripts/fl4260_generate_live_leaves.py           # generate
#   python3 scripts/fl4260_generate_live_leaves.py --dry-run # report only
# =====================================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

# --- Fixed repository locations -------------------------------------
# ROOT is the repository root (this file lives in <root>/scripts/).
ROOT = Path(__file__).resolve().parents[1]
# INV_ROOT is the sanctioned live root of the FL-4260 proof inventory.
INV_ROOT = ROOT / "docs/research/ascii/verification/fl4260"
# The canonical control spreadsheet (one row per user-visible control).
CSV_PATH = INV_ROOT / "CURRENT_UI_INVENTORY.csv"
# The historical browse tree. Leaves inside it are EVIDENCE of what the
# UI looked like on 2026-06-22, not live proof authority.
DATED_ROOT = INV_ROOT / "2026-06-22-current-ui-inventory-tree"
# The machine-readable closed-graph index this script (re)generates.
GRAPH_PATH = INV_ROOT / "INVENTORY_GRAPH.json"

# The literal token written into a leaf when neither the CSV nor the
# dated tree records a value. Grep for this token to find spec gaps.
MISSING = "MISSING (authoring required)"

# Dated-leaf keys we carry into live leaves, in display order.
# These are the fields the dated tree has that the CSV does not:
# concrete source-code anchors and headed-capture evidence pointers.
DATED_KEYS = [
    "widget",              # ImGui widget kind, e.g. CollapsingHeader
    "scanner_label",       # label string the UI scanner matched
    "source_anchor",       # file:line of the ImGui call in editor code
    "source_line",         # the literal C++ source line at that anchor
    "render_consumer",     # what runtime surface consumes the change
    "termpp_expected_surface",      # which TERM++ surface should react
    "termpp_expected_property",     # which property should change
    "termpp_expected_delta_class",  # expected delta family (glyph/color)
    "reachability_status", # whether the scanner could reach the control
    "required_next_action",# next proof step recorded at scan time
    "headed_row_crop_file",# per-row screenshot crop (relative path)
    "source_package",      # dated evidence package the crop lives in
]


# FL-4588 source re-audit (2026-07-15). The June 22 scan anchors for the
# workspace-history block drifted after later editor changes, and its disabled
# render states were incorrectly described as separate actions. These exact
# facts override the dated browse copy while the CSV remains the current
# expectation and migration-status authority.
SOURCE_FACT_OVERRIDES = {
    "fl4260_row_430__starters_highlight_selected": {
        "widget kind": "Checkbox",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24485",
        "source line": "ImGui::Checkbox(\"Highlight selected material on the map\", &g_fl4260_trace_highlight_enabled)",
        "backend owner": "Editor-only selected-material overlay state; TERM++ does not consume it",
        "backend mutation owner": "Browser draft view state after target cutover; no C++ product mutation",
        "render consumer": "Current ImGui editor overlays at editor/asciiid.cpp:9369 and editor/asciiid.cpp:9595",
        "expected user result": "Selected-material cells receive a browser overlay above native TERM++ RGBA.",
        "expected TERM++ result": "Native TERM++ framebuffer bytes remain unchanged.",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ scope": "Empty native TERM++ delta.",
        "expected TERM++ delta class": "empty_delta_browser_draft",
        "expected artifact state change": "Only browser view state changes; product artifacts, revisions, trace, and native frame hash remain unchanged.",
        "expected proof method": "Physical input into the target browser toggle plus native-frame hash equality and visual overlay review.",
        "expectation status": "source_audited_browser_draft_proof_open",
        "TERM++ verdict": "NO_NATIVE_TERMPLUSPLUS_DELTA",
        "pipeline status": "SOURCE_AUDITED_BROWSER_DRAFT_PROOF_OPEN",
        "pipeline gap reason": "target_browser_overlay_and_physical_input_receipt_absent",
        "visibility failure": "none",
    },
    "fl4260_row_431__starters_mode_status_header": {
        "widget kind": "CollapsingHeader",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24495",
        "source line": "if (ImGui::CollapsingHeader(\"1. Material status\", ImGuiTreeNodeFlags_DefaultOpen))",
        "backend owner": "Read-only active ProfileKeyV1 and live-profile status presentation",
        "render consumer": "None",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_read_model",
    },
    "fl4260_row_444__starters_starters_header": {
        "widget kind": "CollapsingHeader",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24523",
        "source line": "if (ImGui::CollapsingHeader(\"2. Start from a look\", ImGuiTreeNodeFlags_DefaultOpen))",
        "backend owner": "ImGui expansion state only; target browser presentation group",
        "render consumer": "None",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_read_model",
    },
    "fl4260_row_453__starters_add_all": {
        "widget kind": "Checkbox",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24547",
        "source line": "if (ImGui::Checkbox(\"Add all character shapes to this material (saves immediately)##fl4260_top_select_all\", &include_all))",
        "backend owner": "Current flat pool_gids toggle; target tombstone because it has no routable-row semantics",
        "render consumer": "No direct compiler row consumer; compile_fl4512_pools reads role_buckets",
        "expected TERM++ surface": "none after target tombstone",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_458__starters_glyph_style_presets": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24575",
        "source line": "if (ImGui::Button(\"Scroll to starting looks##fl4260_top_presets\"))",
        "backend owner": "ImGui scroll flags only; target stable route replaces this button",
        "render consumer": "None",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_459__starters_color_presets": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24584",
        "source line": "if (ImGui::Button(\"Scroll to color controls##fl4260_top_palette\"))",
        "backend owner": "ImGui scroll flags only; target stable route replaces this button",
        "render consumer": "None",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_461__starters_vegetation_cp437": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24795",
        "source line": "if (ImGui::Button(\"Vegetation shapes##rq151startercp\"))",
        "backend owner": "Fl4260ApplyEditCp437GrassPool writes raw CP437 values rejected by the compiled-row candidate contract; target merges into admitted routed presets",
        "render consumer": "No valid compiled row; compile_fl4512_pools rejects candidate GlyphIds at most 255",
        "expected TERM++ surface": "none after target merge",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_494__trace_highlight_selected_material_only": {
        "widget kind": "Checkbox",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:26195",
        "source line": "ImGui::Checkbox(\"Highlight selected material only\", &g_fl4260_trace_highlight_enabled)",
        "backend owner": "Second visible checkbox for the same editor-only overlay state",
        "backend mutation owner": "none independently; merged into scene.viewport.highlight_selected_material",
        "render consumer": "None independently",
        "expected user result": "This duplicate has no independent target control.",
        "expected TERM++ result": "No independent TERM++ delta.",
        "expected TERM++ scope": "No independent cell scope.",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
        "expected artifact state change": "No independent artifact mutation.",
        "expected proof method": "Registry and migration-matrix merge validation.",
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "target_browser_cutover_pending",
        "visibility failure": "none",
    },
    "fl4260_row_640__preset_human_purpose_labels": {
        "widget kind": "presentation metadata",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24523",
        "source line": "Human-readable labels belong to the seven routed preset buttons",
        "backend owner": "none independently; merged into material.profile.shape_presets.section",
        "backend mutation owner": "none independently",
        "render consumer": "None independently",
        "expected user result": "Preset actions retain human-readable labels.",
        "expected TERM++ result": "No independent TERM++ delta.",
        "expected TERM++ scope": "No independent cell scope.",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
        "expected artifact state change": "No independent artifact mutation.",
        "expected proof method": "Registry and migration-matrix merge validation.",
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "typed_preset_handlers_and_cutover_pending",
        "visibility failure": "none",
    },
    "fl4260_row_641__cp437_primary_label_removed": {
        "widget kind": "presentation metadata",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24795",
        "source line": "Vegetation shapes label for the raw CP437 starter",
        "backend owner": "none independently; merged into the compiler-invalid raw CP437 starter row",
        "backend mutation owner": "none independently",
        "render consumer": "None independently",
        "expected user result": "Use admitted routed shape presets.",
        "expected TERM++ result": "No independent TERM++ delta.",
        "expected TERM++ scope": "No independent cell scope.",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
        "expected artifact state change": "No independent artifact mutation.",
        "expected proof method": "Registry and migration-matrix merge validation.",
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "admitted_routed_preset_cutover_pending",
        "visibility failure": "none",
    },
    "fl4260_row_646__status_plain_text": {
        "widget kind": "presentation metadata",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24495",
        "source line": "Plain-language selected-material status text",
        "backend owner": "none independently; merged into material.profile.status",
        "backend mutation owner": "none independently",
        "render consumer": "None independently",
        "expected user result": "The canonical material status read model uses plain language.",
        "expected TERM++ result": "No independent TERM++ delta.",
        "expected TERM++ scope": "No independent cell scope.",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
        "expected artifact state change": "No independent artifact mutation.",
        "expected proof method": "Registry and migration-matrix merge validation.",
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "browser_read_model_cutover_pending",
        "visibility failure": "none",
    },
    "fl4260_row_647__quick_shape_setup_label": {
        "widget kind": "Checkbox plus presentation label",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24547",
        "source line": "Quick shape setup plus Add all mutate the flat pool_gids aggregate",
        "backend owner": "Current flat pool_gids bulk union; target tombstone because compiled rows read role_buckets",
        "backend mutation owner": "none after target tombstone",
        "render consumer": "No direct compiled-row consumer",
        "expected user result": "This invalid bulk shortcut is absent from the target shell.",
        "expected TERM++ result": "No target TERM++ delta exists.",
        "expected TERM++ scope": "No target cell scope exists.",
        "expected TERM++ surface": "none after target tombstone",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
        "expected artifact state change": "No target artifact mutation exists.",
        "expected proof method": "Source review plus registry and migration-matrix tombstone validation.",
        "expectation status": "source_audited_target_tombstone",
        "TERM++ verdict": "NO_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_TOMBSTONE",
        "pipeline gap reason": "delete_first_target_cutover_pending",
        "visibility failure": "none",
    },
    "fl4260_row_462__starters_vegetation_ramp": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24824",
        "source line": "if (ImGui::Button(\"Vegetation colors##rq150startergrass\"))",
        "backend owner": "Fl4260ApplyEditColorsGrassDefault plus queued profile serialize, compile, validate, and publish transaction",
        "render consumer": "Selected-profile compiled color rows and final TERM++ foreground plus background indices",
        "expected TERM++ surface": "selected-profile final native TERM++ cells",
        "expected TERM++ property": "foreground and background indices with GlyphId protected",
        "expected TERM++ delta class": "dependency_derived_color_delta",
    },
    "fl4260_row_465__quick_select_all_eligible": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24905",
        "source line": "if (ImGui::Button(\"Add all preset shapes##fl4260qs_all\"))",
        "backend owner": "Current bulk union rewrites flat pool_gids; target tombstone because no addressed legal lane owns the action",
        "render consumer": "None; compile_fl4512_pools constructs rows only from role_buckets",
        "expected TERM++ surface": "none after target tombstone",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_466__quick_clear_pool": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24931",
        "source line": "if (ImGui::Button(\"Clear shapes##fl4260qs_clear\"))",
        "backend owner": "Current duplicate flat pool_gids clear; target merge into material.pool.candidate.clear for one addressed lane",
        "render consumer": "No current compiler-row consumer; target addressed role_buckets row",
        "expected TERM++ surface": "selected-profile cells addressing the target lane after cutover",
        "expected TERM++ property": "registered empty-row absence with every other lane protected",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_467__quick_restore_defaults": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24949",
        "source line": "if (ImGui::Button(\"Restore first look##fl4260qs_restore\"))",
        "backend owner": "Current broken flat-pool preset helper; target merge into the routed Grass tops shape-preset transaction",
        "render consumer": "Target compiled ramp plus density rows for the selected profile",
        "expected TERM++ surface": "selected-profile tonal cells after target cutover",
        "expected TERM++ property": "current and presented GlyphId with colors protected",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_482__pool_clear_extended_pool": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25184",
        "source line": "if (ImGui::Button(\"Clear custom shapes##rq151ext\"))",
        "backend owner": "Current duplicate flat pool_gids clear; target merge into material.pool.candidate.clear for one addressed lane",
        "render consumer": "No current compiler-row consumer; target addressed role_buckets row",
        "expected TERM++ surface": "selected-profile cells addressing the target lane after cutover",
        "expected TERM++ property": "registered empty-row absence with every other lane protected",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_483__pool_select_all_catalog": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25271",
        "source line": "if (ImGui::Button(\"Add all catalog shapes##rq151cat_all\"))",
        "backend owner": "Current catalog-wide flat pool_gids union; target tombstone because no single addressed row owns the set",
        "render consumer": "None; the operation can exceed the compiled legal-row capacity of 32",
        "expected TERM++ surface": "none after target tombstone",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_484__pool_invert_catalog_selection": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25283",
        "source line": "if (ImGui::Button(\"Swap catalog selection##rq151cat_invert\"))",
        "backend owner": "Current catalog inversion rewrites flat pool_gids; target tombstone because no addressed legal-row transaction exists",
        "render consumer": "None; compile_fl4512_pools ignores the flat glyph_pools list",
        "expected TERM++ surface": "none after target tombstone",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_485__pool_restore_preset_defaults": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25296",
        "source line": "if (ImGui::Button(\"Restore first look##rq151cat_restore\"))",
        "backend owner": "Current duplicate preset helper plus duplicate direct-edit queue; target merge into the routed Grass tops shape-preset transaction",
        "render consumer": "Target compiled ramp plus density rows for the selected profile",
        "expected TERM++ surface": "selected-profile tonal cells after target cutover",
        "expected TERM++ property": "current and presented GlyphId with colors protected",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_648__clear_and_autofill_tooltips": {
        "widget kind": "Composite inventory row",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24997",
        "source line": "Reload saved colors, Clear custom shapes, and Auto-place from shape set are independent widgets",
        "backend owner": "Inventory-only grouping of three independent actions; no mutation owner exists for the aggregate row",
        "render consumer": "None as one action; target leaves retain their own compiler and renderer consumers",
        "expected TERM++ surface": "none as an independent target leaf",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_652__glyph_catalog_morphology_grouping": {
        "widget kind": "Child catalog plus glyph chips",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25390",
        "source line": "ImGui::BeginChild(\"##glyphpool_catalog\", ImVec2(0, 4.5f * step), true, ImGuiWindowFlags_HorizontalScrollbar)",
        "backend owner": "Current browse filters plus mouse, drag, keyboard, and context-menu flat pool_gids writers; target splits read-only catalog state from typed addressed-lane actions",
        "render consumer": "Browse filters have none; current flat-pool writes have no compiled-row consumer",
        "expected TERM++ surface": "selected-profile cells addressing the target lane only after typed-action cutover",
        "expected TERM++ property": "current and presented GlyphId with unrelated lanes protected",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_658__unified_glyphid_shape_pool": {
        "widget kind": "Composite flat-pool readout and editor",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25168",
        "source line": "which glyphs MAY be used by the selected profile edit state",
        "backend owner": "Flat pool_gids catalog membership aggregate; it is not a compiler-consumed legal row",
        "render consumer": "None directly; target addressed role_buckets rows feed the selector",
        "expected TERM++ surface": "none as an independent target leaf",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_target_663__glyph_set_controls": {
        "widget kind": "Target inventory aggregate",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25168",
        "source line": "Target aggregate derived from the current flat pool and lane action widgets",
        "backend owner": "Replaced by six typed candidate controls plus two typed catalog actions, each addressed to one legal lane",
        "render consumer": "No independent aggregate consumer",
        "expected TERM++ surface": "none as an independent target leaf",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_670__role_ramp_lane_chip_remove": {
        "widget kind": "InvisibleButton plus keyboard and context-menu activation",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25654",
        "source line": "Fl4260RemoveRoleLaneGlyph(g_asciiid_active_profile_key, family, lane, remove_gid, \"role_bucket_remove\")",
        "backend owner": "Current Fl4260RemoveRoleLaneGlyph lane mutation; target merge into material.pool.candidate.remove",
        "render consumer": "Compiled row addressed by selected profile, selected role, and exact lane",
        "expected TERM++ surface": "selected-profile cells whose route addresses the edited lane",
        "expected TERM++ property": "current and presented GlyphId; colors plus every other lane protected",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_519__view_save_as": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:26826",
        "source line": "if (ImGui::Button(\"SAVE AS\"))",
        "backend owner": "Legacy document-dialog mode and browser-target Save As draft opener",
        "render consumer": "No renderer consumer until a distinct save commit succeeds",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_dialog_open",
    },
    "fl4260_row_520__view_load": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:26854",
        "source line": "if (ImGui::Button(\"LOAD\"))",
        "backend owner": "Legacy document-dialog mode and browser-target Open draft opener",
        "render consumer": "No renderer consumer until a distinct open commit succeeds",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_dialog_open",
    },
    "fl4260_row_521__view_merge": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:26871",
        "source line": "if (ImGui::Button(\"MERGE\"))",
        "backend owner": "Legacy document-dialog mode and browser-target Merge draft opener",
        "render consumer": "No renderer consumer until a distinct merge commit succeeds",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_dialog_open",
    },
    "fl4260_row_522__view_new": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:26889",
        "source line": "if (ImGui::Button(\"NEW\"))",
        "backend owner": "ConfirmNew calls destructive New; target owns validated default-document construction plus atomic replacement",
        "render consumer": "Complete native TERM++ viewport after replacement publication",
        "expected TERM++ surface": "final native TERM++ framebuffer and rendered-cell records",
        "expected TERM++ property": "complete visible tuple from the new default document",
        "expected TERM++ delta class": "dependency_derived_document_replace",
    },
    "fl4260_row_527__cancel": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27046",
        "source line": "if (ImGui::Button(\"Cancel\"))",
        "backend owner": "Legacy dialog-state cancellation; target browser draft cancellation",
        "render consumer": "None",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_browser_draft",
    },
    "fl4260_row_547__id_only_control_visible_label_from_adjacent_text": {
        "widget kind": "InputText",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27364",
        "source line": "if (ImGui::InputText(\"###path\",save_path,4096,ImGuiInputTextFlags_EnterReturnsTrue))",
        "backend owner": "Legacy path buffer plus polymorphic Enter commit; target browser path draft only",
        "render consumer": "None until a distinct document commit succeeds",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_browser_draft",
    },
    "fl4260_row_548__id_only_control_visible_label_from_adjacent_text": {
        "widget kind": "ListBoxHeader",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27393",
        "source line": "bool dir_list_open = save && ImGui::ListBoxHeader(\"###dir\", ImVec2(-1, -ImGui::GetItemsLineHeightWithSpacing()) );",
        "backend owner": "Legacy C++ directory listing read model",
        "render consumer": "None",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_read_model",
    },
    "fl4260_row_549__dynamic_label_di": {
        "widget kind": "Selectable",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27405",
        "source line": "if (ImGui::Selectable((*di)->name,false, ImGuiSelectableFlags_AllowDoubleClick))",
        "backend owner": "Legacy path selection and immediate MergeOpen staging; target browser selection draft only",
        "render consumer": "None until a distinct document commit succeeds",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta_browser_draft",
    },
    "fl4260_row_550__dynamic_label_commit_label": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27452",
        "source line": "if (save && (ImGui::Button(commit_label) || save_do))",
        "backend owner": "Current polymorphic Save, Open, and Merge commit owner; target tombstone",
        "render consumer": "None after delete-first target cutover",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_551__dynamic_label_save_1_save_overwrite_confirm_clear_confirm_cancel": {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27478",
        "source line": "if (save && ImGui::Button(save == 1 && save_overwrite_confirm ? \"CLEAR CONFIRM\" : \"CANCEL\"))",
        "backend owner": "Current polymorphic overwrite-confirm clear and dialog cancel owner; target tombstone",
        "render consumer": "None after delete-first target cutover",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_554__edit_urdo_header": {
        "widget kind": "CollapsingHeader",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27532",
        "source line": "bool undo_redo_open = ImGui::CollapsingHeader(\"Undo / Redo\", ImGuiTreeNodeFlags_DefaultOpen);",
        "backend owner": "URDO availability and memory readout",
        "render consumer": "No renderer consumer; presentation state only",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "empty_delta",
    },
    "fl4260_row_555__edit_undo_all_disabled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27536",
        "source line": "if (!URDO_CanUndo())",
        "backend owner": "Disabled rendering state for the atomic Undo button",
        "render consumer": "Canonical workspace.history.undo availability",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_556__edit_undo_one_disabled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27544",
        "source line": "Fl4260RecordControlRect(\"edit.undo.one_disabled\");",
        "backend owner": "Disabled rendering state for retired partial Undo",
        "render consumer": "None after target tombstone",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_557__unlabeled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27550",
        "source line": "if (ImGui::Button(\"<<\") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)",
        "backend owner": "URDO grouped map history; target AsciiidApplicationService transaction history",
        "render consumer": "Affected map cells in current source; all transaction dependencies in target source",
        "expected TERM++ surface": "final native TERM++ framebuffer and rendered-cell records",
        "expected TERM++ property": "complete visible tuple after restored transaction publication",
        "expected TERM++ delta class": "dependency_derived_transaction_delta",
    },
    "fl4260_row_558__unlabeled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27555",
        "source line": "{ URDO_Undo(1); inst_list_dirty = true; }",
        "backend owner": "Current URDO partial group traversal; no target owner",
        "render consumer": "None after target tombstone",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_559__edit_redo_one_disabled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27559",
        "source line": "if (!URDO_CanRedo())",
        "backend owner": "Disabled rendering state for retired partial Redo",
        "render consumer": "None after target tombstone",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_560__edit_redo_all_disabled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27567",
        "source line": "Fl4260RecordControlRect(\"edit.redo.all_disabled\");",
        "backend owner": "Disabled rendering state for the atomic Redo button",
        "render consumer": "Canonical workspace.history.redo availability",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_561__unlabeled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27573",
        "source line": "if (ImGui::Button(\">\") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)",
        "backend owner": "Current URDO partial group traversal; no target owner",
        "render consumer": "None after target tombstone",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
    "fl4260_row_562__unlabeled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27577",
        "source line": "if (ImGui::Button(\">>\") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)",
        "backend owner": "URDO grouped map history; target AsciiidApplicationService transaction history",
        "render consumer": "Affected map cells in current source; all transaction dependencies in target source",
        "expected TERM++ surface": "final native TERM++ framebuffer and rendered-cell records",
        "expected TERM++ property": "complete visible tuple after reapplied transaction publication",
        "expected TERM++ delta class": "dependency_derived_transaction_delta",
    },
    "fl4260_row_563__edit_urdo_purge_disabled": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27582",
        "source line": "if (!URDO_CanRedo() && !URDO_CanUndo())",
        "backend owner": "Disabled rendering state for retired manual history purge",
        "render consumer": "None after target tombstone",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "merged_accounting_row",
    },
    "fl4260_row_564__edit_urdo_purge": {
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:27592",
        "source line": "if (ImGui::Button(\"PURGE\"))",
        "backend owner": "Current URDO_Purge recovery-history deletion; no target owner",
        "render consumer": "None after target tombstone",
        "expected TERM++ surface": "none",
        "expected TERM++ property": "none",
        "expected TERM++ delta class": "tombstone_decision",
    },
}

for _preset_uid in (
    "fl4260_row_460.0__starters_preset_0_grass",
    "fl4260_row_460.1__starters_preset_1_water",
    "fl4260_row_460.2__starters_preset_2_grass",
    "fl4260_row_460.3__starters_preset_3_stone",
    "fl4260_row_460.4__starters_preset_4_dirt",
    "fl4260_row_460.5__starters_preset_5_stone",
    "fl4260_row_460.6__starters_preset_6_gravel",
):
    SOURCE_FACT_OVERRIDES[_preset_uid] = {
        "widget kind": "Button",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:24737",
        "source line": "if (ImGui::Button(btn) && starter_available)",
        "backend owner": "Fl4260ApplyProfileEditFromPreset clears edit state and writes only flat pool_gids; target typed action must write the previewed ramp plus density rows",
        "render consumer": "Current flat pool has no compiled-row consumer; target ramp plus density rows feed the GPU selector",
        "expected TERM++ surface": "selected-profile tonal cells after target cutover",
        "expected TERM++ property": "current and presented GlyphId with colors protected",
        "expected TERM++ delta class": "dependency_derived_routed_shape_preset_delta",
    }


_SCORING_SOURCE_ROWS = {
    "fl4260_row_474__scoring_curve": ("TL", "curve", "0.0 through 4.0"),
    "fl4260_row_475__scoring_diagonal": ("TR", "diagonal", "0.0 through 4.0"),
    "fl4260_row_476__scoring_horizontal": ("ML", "horizontal", "0.0 through 4.0"),
    "fl4260_row_477__scoring_vertical": ("MR", "vertical", "0.0 through 4.0"),
    "fl4260_row_478__scoring_sparse": ("BL", "sparse", "0.0 through 4.0"),
    "fl4260_row_479__scoring_dense": ("BR", "dense", "0.0 through 4.0"),
    "fl4260_row_489__scoring_detail_contrast": ("detail", "detail_contrast", "0 through 100"),
    "fl4260_row_490__scoring_tone_contrast": ("tone", "tone_contrast", "0 through 100"),
    "fl4260_row_491__scoring_density_bias": ("density bias", "density_bias", "-100 through 100"),
}

for _uid, (_target_name, _durable_key, _range_text) in _SCORING_SOURCE_ROWS.items():
    _positional = _target_name in {"TL", "TR", "ML", "MR", "BL", "BR"}
    SOURCE_FACT_OVERRIDES[_uid] = {
        "widget kind": "SliderFloat" if _positional else "SliderInt",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25822" if _positional else "apps/asciicker-presenter/asciiid.cpp:25802",
        "source line": (
            "ImGui::SliderFloat writes one positional scoring_role_weights slot"
            if _positional else f"ImGui::SliderInt writes {_durable_key}"
        ),
        "backend owner": (
            f"Current ImGui callback writes {_durable_key}; target action names the value {_target_name} "
            f"and constrains it to {_range_text} before the C++ compiler publishes six effective weights"
        ),
        "backend mutation owner": (
            "Current Fl4260ApplyProfileDirectEdit queues profile serialization, pool compilation, validation, "
            "and runtime publication; no target application-service handler exists"
        ),
        "render consumer": (
            "Tonal, edge, direction, and flow weighted global6 scorers consume the compiled vector; "
            "structural mask and filled-field scorers do not"
        ),
        "expected user result": f"The selected qualified material stores the authored {_target_name} scoring value.",
        "expected TERM++ result": (
            "Only selected-profile cells using a weighted global6 scorer may change their complete visible tuple."
        ),
        "expected TERM++ scope": (
            "Exact dependency-derived selected-profile tonal, edge, direction, and flow cells; structural, "
            "filled, other-profile, and recomputed-equal cells are protected."
        ),
        "expected artifact state change": (
            "One authored scoring value plus the six compiled and published effective weights change in one transaction."
        ),
        "expected proof method": (
            "Two independent counterfactual evaluators must agree before physical input into the visible slider; "
            "the same-frame TERM++ tuple, trace, console, hashes, and receipt must match the prediction."
        ),
        "expectation status": "source_audited_target_contract_proof_open",
        "TERM++ verdict": "DEPENDENCY_DERIVED_DELTA_EXPECTED",
        "pipeline status": "SOURCE_AUDITED_TARGET_CONTRACT_PROOF_OPEN",
        "pipeline gap reason": "typed_target_handler_evaluator_pair_and_headed_receipt_absent",
        "visibility failure": "none",
    }

SOURCE_FACT_OVERRIDES["fl4260_row_491__scoring_density_bias"].update({
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:CommitAsciiidMaterialScoreParameter",
    "source line": "Physical HTML slider commits density_bias through the typed application service",
    "backend owner": (
        "AsciiidApplicationService validates the selected qualified profile and CommitAsciiidMaterialScoreParameter "
        "writes density_bias through the sole U4 profile transaction"
    ),
    "backend mutation owner": (
        "CommitAsciiidMaterialScoreParameter calls Fl4260SaveProfileEdit, which serializes, compiles, validates, "
        "publishes one revision, and restores the prior edit state if the transaction fails"
    ),
    "expectation status": "typed_handler_implemented_physical_receipt_open",
    "pipeline status": "HTML_TYPED_HANDLER_IMPLEMENTED_PROOF_OPEN",
    "pipeline gap reason": "prediction_first_physical_input_receipt_absent",
})

SOURCE_FACT_OVERRIDES["fl4260_row_488__section_winner_scoring_header"] = {
    "widget kind": "CollapsingHeader",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:25767",
    "source line": 'ImGui::CollapsingHeader("6. Tune shape matching")',
    "backend owner": "Read-only grouping control; no mutation owner",
    "backend mutation owner": "none",
    "render consumer": "none",
    "expected user result": "Expand and collapse the Shape matching controls.",
    "expected TERM++ result": "No TERM++ delta.",
    "expected TERM++ scope": "No cell scope.",
    "expected artifact state change": "No artifact mutation.",
    "expected proof method": "Registry and accessibility validation for one read-only section heading.",
    "expectation status": "source_audited_read_only",
    "TERM++ verdict": "NO_DELTA",
    "pipeline status": "SOURCE_AUDITED_READ_ONLY",
    "pipeline gap reason": "none",
    "visibility failure": "none",
}

for _uid, _target in {
    "fl4260_row_492__scoring_curve": "material.score.tl",
    "fl4260_target_667__shape_match_controls": "the nine material.score controls plus material.score.reset",
}.items():
    SOURCE_FACT_OVERRIDES[_uid] = {
        "widget kind": "inventory accounting row",
        "source anchor": "apps/asciicker-presenter/asciiid.cpp:25767",
        "source line": "No independent target callback",
        "backend owner": f"Merged into {_target}",
        "backend mutation owner": "none",
        "render consumer": "none independently",
        "expected user result": f"This row has no independent target control; use {_target}.",
        "expected TERM++ result": "No independent TERM++ delta.",
        "expected TERM++ scope": "No independent cell scope.",
        "expected artifact state change": "No independent artifact mutation.",
        "expected proof method": "Registry and migration-matrix merge validation.",
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "typed_target_handler_and_delete_first_cutover_pending",
        "visibility failure": "none",
    }

SOURCE_FACT_OVERRIDES["fl4260_row_463__persist_save_material_look"] = {
    "widget kind": "retired inventory Button",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:11933",
    "source line": "static bool Fl4260CommitProfileBatchWithHistory(const ProfileKeyV1* keys, int count,",
    "backend owner": "No independent Save control; typed Material Profile committers call Fl4260CommitProfileBatchWithHistory, which prepares URDO_Fl4260ProfilePrepare before installing candidate_states and persists through Fl4260CommitProfileBatch",
    "backend mutation owner": "none for the retired control",
    "render consumer": "none independently",
    "expected user result": "Each typed Material Profile edit commits as one versioned transaction without a separate Save Material Look control.",
    "expected TERM++ result": "No independent TERM++ delta exists for the retired control.",
    "expected TERM++ scope": "No independent cell scope.",
    "expected artifact state change": "No independent artifact mutation exists.",
    "expected proof method": "Resolve Fl4260CommitProfileBatchWithHistory, verify prepare-before-candidate ordering and the sole Fl4260CommitProfileBatch durable call, then validate the retired inventory observation.",
    "expectation status": "source_audited_target_tombstone",
    "TERM++ verdict": "NO_TARGET_DELTA",
    "pipeline status": "SOURCE_AUDITED_TARGET_TOMBSTONE",
    "pipeline gap reason": "headed_html_transaction_receipt_absent",
    "visibility failure": "none",
}

SOURCE_FACT_OVERRIDES["fl4260_row_464__persist_revert_from_disk"] = {
    "widget kind": "retired inventory Button",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:11848",
    "source line": "bool Fl4260ProfileUndoSwapPublishBatch(int count, const int* profile_index,",
    "backend owner": "No independent disk-revert control; canonical recovery is workspace.history.undo plus workspace.history.redo",
    "backend mutation owner": "CommitAsciiidHistoryUndo and CommitAsciiidHistoryRedo traverse canonical URDO history; Fl4260ProfileUndoSwapPublishBatch owns Material Profile Undo and Redo publication",
    "render consumer": "none independently",
    "expected user result": "Use the visible HTML workspace Undo and Redo controls for committed Material Profile history.",
    "expected TERM++ result": "No independent TERM++ delta exists for the retired inventory observation.",
    "expected TERM++ scope": "No independent cell scope.",
    "expected artifact state change": "No independent artifact mutation exists.",
    "expected proof method": "Resolve CommitAsciiidHistoryUndo, CommitAsciiidHistoryRedo, and Fl4260ProfileUndoSwapPublishBatch, then validate the retired inventory observation.",
    "expectation status": "source_audited_target_tombstone",
    "TERM++ verdict": "NO_TARGET_DELTA",
    "pipeline status": "SOURCE_AUDITED_TARGET_TOMBSTONE",
    "pipeline gap reason": "headed_html_history_receipt_absent",
    "visibility failure": "none",
    "expected TERM++ surface": "none independently",
    "expected TERM++ property": "none independently",
    "expected TERM++ delta class": "tombstone_decision",
}

SOURCE_FACT_OVERRIDES["fl4260_row_655__material_look_one_step_undo"] = {
    "widget kind": "merged inventory observation",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:31648",
    "source line": "static AsciiidHistoryCommitV1 CommitAsciiidHistoryUndo(",
    "backend owner": "Merged observation of canonical workspace.history.undo through CommitAsciiidHistoryUndo",
    "backend mutation owner": "Fl4260CommitProfileBatchWithHistory prepares URDO_Fl4260ProfilePrepare before installing candidates; Fl4260ProfileUndoSwapPublishBatch owns canonical Undo and Redo publication through Fl4260CommitProfileBatch",
    "render consumer": "All final TERM++ cells dependent on the restored qualified profiles",
    "expected user result": "The visible HTML workspace Undo control reverses one complete committed Material Profile transaction.",
    "expected TERM++ result": "The restored Material Profile batch is compiled, published, and reflected in its exact dependency-derived final-cell delta.",
    "expected TERM++ scope": "All visible cells dependent on the restored qualified profiles; equal complete visible tuples remain protected.",
    "expected artifact state change": "CommitAsciiidHistoryUndo advances one canonical Material Profile batch only after Fl4260ProfileUndoSwapPublishBatch republishes it through Fl4260CommitProfileBatch.",
    "expected proof method": "Physical input into workspace.history.undo plus grouped transaction, rendered-cell, trace, manifest, and receipt verification.",
    "expectation status": "source_audited_target_merge_proof_open",
    "TERM++ verdict": "PROOF_OPEN",
    "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
    "pipeline gap reason": "headed_html_workspace_history_receipt_absent",
    "visibility failure": "none",
    "expected TERM++ surface": "final native TERM++ framebuffer and rendered-cell records",
    "expected TERM++ property": "complete visible tuple after canonical profile-batch history publication",
    "expected TERM++ delta class": "dependency_derived_profile_batch_transaction",
}

for _uid, _target, _anchor in (
    (
        "fl4260_row_644__color_swatch_plain_labels",
        "the eight canonical material.colors.rowN foreground and background leaves",
        "apps/asciicker-presenter/asciiid.cpp:25028",
    ),
    (
        "fl4260_target_662__palette_color_controls",
        "the separately registered row-color, strength, anchor-color, and palette actions",
        "apps/asciicker-presenter/asciiid.cpp:24083",
    ),
):
    SOURCE_FACT_OVERRIDES[_uid] = {
        "widget kind": "inventory accounting row",
        "source anchor": _anchor,
        "source line": "No independent target callback",
        "backend owner": f"Merged into {_target}",
        "backend mutation owner": "none",
        "render consumer": "none independently",
        "expected user result": f"This row has no independent target control; use {_target}.",
        "expected TERM++ result": "No independent TERM++ delta.",
        "expected TERM++ scope": "No independent cell scope.",
        "expected artifact state change": "No independent artifact mutation.",
        "expected proof method": "Registry and migration-matrix merge validation.",
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "typed_target_handlers_and_delete_first_cutover_pending",
        "visibility failure": "none",
    }

SOURCE_FACT_OVERRIDES["fl4260_row_631__palette_theme_button"] = {
    "widget kind": "inventory accounting row",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:24094",
    "source line": "Palette-name click selects draft state; square click applies one anchor; name drop spreads eight colors",
    "backend owner": "Merged into four separately registered palette interactions",
    "backend mutation owner": "none for the conflated inventory row",
    "render consumer": "none independently",
    "expected user result": "Use palette selection, palette color application, palette spread, and next-unused assignment as separate interactions.",
    "expected TERM++ result": "Selection has no product delta; each product action has its own bounded color delta.",
    "expected TERM++ scope": "Defined by the separately registered semantic action.",
    "expected artifact state change": "No independent artifact mutation exists for this accounting row.",
    "expected proof method": "Source review plus registry and migration-matrix merge validation.",
    "expectation status": "source_audited_target_merge",
    "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
    "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
    "pipeline gap reason": "dead_theme_helper_dead_color_drag_and_bulk_atomicity_gap_logged",
    "visibility failure": "Fl4260ApplyThemeToProfile has no caller; FL4260_PALETTE_COLOR has no drop consumer.",
}

SOURCE_FACT_OVERRIDES["fl4260_row_638__material_anchor_color_swatch"] = {
    "widget kind": "ImGui::ColorButton plus ColorPicker3",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:24352",
    "source line": "Fl4260ApplyFlatAnchorColorToProfileMaterial derives four color rows from one anchor RGB",
    "backend owner": "One qualified Fl4260ProfileEditState color family",
    "backend mutation owner": "Fl4260ApplyFlatAnchorColorToProfileMaterial then one-profile autosave",
    "render consumer": "Compiled profile colors plus final TERM++ foreground and background",
    "expected user result": "Set one qualified profile anchor RGB and derive all four color rows.",
    "expected TERM++ result": "Only owned cells with changed foreground plus background may change; GlyphIds remain protected.",
    "expected TERM++ scope": "Cells whose qualified profile owner equals the edited terrain plus mesh profile.",
    "expected artifact state change": "One profile record, compiled row, transaction, and publication revision change atomically.",
    "expected proof method": "Independent evaluator pair plus headed physical-input receipt and same-frame cell trace.",
    "expectation status": "source_audited_target_contract_frozen_proof_open",
    "TERM++ verdict": "TERMPLUSPLUS_COLOR_DELTA_EXPECTED",
    "pipeline status": "SOURCE_AUDITED_TARGET_CONTRACT_FROZEN",
    "pipeline gap reason": "typed_handler_and_exact_cell_evaluator_pair_missing",
    "visibility failure": "none",
}

SOURCE_FACT_OVERRIDES["fl4260_target_671__assign_palette_to_next_unused_slots"] = {
    "widget kind": "ImGui palette-name context menu item",
    "source anchor": "apps/asciicker-presenter/asciiid.cpp:24113",
    "source line": "Fl4260AssignPaletteToNextUnusedSlots loops over unprofiled keys but overwrites one pending autosave key",
    "backend owner": "Palette asset plus frozen terrain-profile liveness",
    "backend mutation owner": "Target atomic multi-profile palette assignment transaction",
    "render consumer": "Compiled profile colors plus final TERM++ foreground and background",
    "expected user result": "Assign the palette to the first eight unprofiled terrain keys in one atomic transaction.",
    "expected TERM++ result": "Only owned cells under the predicted target keys with changed colors may change; GlyphIds remain protected.",
    "expected TERM++ scope": "Cells owned by the frozen first-eight-unused profile-key set.",
    "expected artifact state change": "Every predicted profile plus compiled row changes under one transaction and one publication revision.",
    "expected proof method": "Independent key-selection and exact-cell evaluators plus headed context-menu receipt.",
    "expectation status": "source_bug_logged_target_atomic_contract_frozen_proof_open",
    "TERM++ verdict": "TERMPLUSPLUS_COLOR_DELTA_EXPECTED_AFTER_ATOMIC_TARGET",
    "pipeline status": "SOURCE_BUG_NONATOMIC_TARGET_CONTRACT_FROZEN",
    "pipeline gap reason": "current_loop_overwrites_single_pending_autosave_key",
    "visibility failure": "The deleted hidden CDP command and its historical receipt are inadmissible under Law 12b.",
}

SOURCE_FACT_OVERRIDES["fl4588_html_mesh_asset_combo"] = {
    "widget kind": "HTML select",
    "source anchor": "web/asciiid-authoring-shell/src/ActiveToolOptions.tsx:118",
    "source line": "onChange={(event) => void onPick(event.target.value)}",
    "backend owner": "AsciiidApplicationService::CommitSceneObjectAction validates select_mesh_asset against the live mesh library",
    "backend mutation owner": "editor_session.active_mesh via the typed select_mesh_asset action",
    "render consumer": "RuntimeIntentOwner may consume the selection through a separately registered placement control; TERM++ remains read-only",
    "visibility failure": "none",
    "expected TERM++ surface": "none until the following mesh placement transaction",
    "expected TERM++ property": "none for selection alone",
    "expected TERM++ delta class": "empty_final_termpp_editor_session_selection",
}

SOURCE_FACT_OVERRIDES["target_workspace_document_open_commit"] = {
    "widget kind": "HTML file input reached through the visible Open button",
    "source anchor": "web/asciiid-authoring-shell/src/WorkspaceActions.tsx:138",
    "source line": "handleOpenPick passes the user-selected File to runDocumentOpenSequence and uploadDocument",
    "backend owner": "AsciiidApplicationService::OpenDocumentBytes validates the authenticated upload before CommitAsciiidBrowserDocument adopts it",
    "backend mutation owner": "CommitAsciiidBrowserDocument atomically replaces the live terrain, world, materials, enemy generators, markers, active mesh, document identity, and history state",
    "render consumer": "The runtime-published TERM++ presenter consumes the newly adopted native document",
    "visibility failure": "none; invalid bytes fail visibly while the prior document remains live",
    "expected TERM++ surface": "the complete TERM++ final surface for the adopted document",
    "expected TERM++ property": "the final GlyphId, foreground, background, and flags resolved from the newly adopted world",
    "expected TERM++ delta class": "full_document_replacement_final_cell_delta",
}

SOURCE_FACT_OVERRIDES["asciiid_interaction_workspace_file_save"] = {
    "widget kind": "HTML button",
    "source anchor": "web/asciiid-authoring-shell/src/WorkspaceActions.tsx:171",
    "source line": "handleSave runs the visible Save sequence through downloadDocument, then refreshes the authenticated live snapshot",
    "backend owner": "AsciiidApplicationService::DownloadDocument delegates to DownloadAsciiidBrowserDocument for canonical live A3D bytes",
    "backend mutation owner": "none; Save downloads the canonical active document and updates browser presentation state without mutating native document truth",
    "render consumer": "The browser download plus WorkspaceActions active-document status consume the canonical bytes, logical name, and refreshed state revision",
    "visibility failure": "A missing session, missing native downloader, serialization failure, empty payload, plus invalid content type fail visibly without fabricating a download",
    "expected TERM++ surface": "the existing complete TERM++ final surface remains unchanged",
    "expected TERM++ property": "final GlyphId, foreground, background, flags, and frame identity remain unchanged by the read-only save transaction",
    "expected TERM++ delta class": "empty_final_termpp_document_download",
}

SOURCE_AUDITED_UIDS = {
    "fl4260_row_430__starters_highlight_selected",
    "fl4260_row_519__view_save_as",
    "fl4260_row_520__view_load",
    "fl4260_row_521__view_merge",
    "fl4260_row_522__view_new",
    "fl4260_row_527__cancel",
    "fl4260_row_547__id_only_control_visible_label_from_adjacent_text",
    "fl4260_row_548__id_only_control_visible_label_from_adjacent_text",
    "fl4260_row_549__dynamic_label_di",
    "fl4260_row_550__dynamic_label_commit_label",
    "fl4260_row_551__dynamic_label_save_1_save_overwrite_confirm_clear_confirm_cancel",
    "fl4260_row_431__starters_mode_status_header",
    "fl4260_row_444__starters_starters_header",
    "fl4260_row_453__starters_add_all",
    "fl4260_row_458__starters_glyph_style_presets",
    "fl4260_row_459__starters_color_presets",
    "fl4260_row_460.0__starters_preset_0_grass",
    "fl4260_row_460.1__starters_preset_1_water",
    "fl4260_row_460.2__starters_preset_2_grass",
    "fl4260_row_460.3__starters_preset_3_stone",
    "fl4260_row_460.4__starters_preset_4_dirt",
    "fl4260_row_460.5__starters_preset_5_stone",
    "fl4260_row_460.6__starters_preset_6_gravel",
    "fl4260_row_461__starters_vegetation_cp437",
    "fl4260_row_462__starters_vegetation_ramp",
    "fl4260_row_463__persist_save_material_look",
    "fl4260_row_464__persist_revert_from_disk",
    "fl4260_row_494__trace_highlight_selected_material_only",
    "fl4260_row_640__preset_human_purpose_labels",
    "fl4260_row_641__cp437_primary_label_removed",
    "fl4260_row_644__color_swatch_plain_labels",
    "fl4260_row_631__palette_theme_button",
    "fl4260_row_638__material_anchor_color_swatch",
    "fl4260_row_646__status_plain_text",
    "fl4260_row_647__quick_shape_setup_label",
    "fl4260_row_465__quick_select_all_eligible",
    "fl4260_row_466__quick_clear_pool",
    "fl4260_row_467__quick_restore_defaults",
    "fl4260_row_482__pool_clear_extended_pool",
    "fl4260_row_483__pool_select_all_catalog",
    "fl4260_row_484__pool_invert_catalog_selection",
    "fl4260_row_485__pool_restore_preset_defaults",
    "fl4260_row_648__clear_and_autofill_tooltips",
    "fl4260_row_652__glyph_catalog_morphology_grouping",
    "fl4260_row_655__material_look_one_step_undo",
    "fl4260_row_658__unified_glyphid_shape_pool",
    "fl4260_target_663__glyph_set_controls",
    "fl4260_row_670__role_ramp_lane_chip_remove",
    "fl4260_row_474__scoring_curve",
    "fl4260_row_475__scoring_diagonal",
    "fl4260_row_476__scoring_horizontal",
    "fl4260_row_477__scoring_vertical",
    "fl4260_row_478__scoring_sparse",
    "fl4260_row_479__scoring_dense",
    "fl4260_row_488__section_winner_scoring_header",
    "fl4260_row_489__scoring_detail_contrast",
    "fl4260_row_490__scoring_tone_contrast",
    "fl4260_row_491__scoring_density_bias",
    "fl4260_row_492__scoring_curve",
    "fl4260_target_667__shape_match_controls",
    "fl4260_target_662__palette_color_controls",
    "fl4260_target_671__assign_palette_to_next_unused_slots",
    "fl4588_html_mesh_asset_combo",
    "target_workspace_document_open_commit",
    "asciiid_interaction_workspace_file_save",
}


def _candidate_audit_fields(
    physical_target: str,
    target_action: str | None,
    disposition: str,
    current_owner: str,
) -> dict[str, str]:
    """Return target-architecture facts for one audited candidate-set row.

    The inventory CSV still contains historical backend proof commands. Those
    commands were deleted from ASCIIID and cannot remain reader-facing facts on
    a source-audited leaf. Current visible ownership stays documented here,
    while target mutation belongs only to the separately registered typed
    action.
    """
    physical_label = (
        f"physical-input target {physical_target}; read-only identity, not a backend command"
    )
    if disposition == "tombstone":
        return {
            "CDP control label": physical_label,
            "backend mutation owner": (
                f"Current visible ImGui callback: {current_owner}. The target shell deletes this "
                "control and has no replacement mutation owner."
            ),
            "expected user result": "This ambiguous legacy control is absent from the target shell.",
            "expected TERM++ result": "No target TERM++ delta exists.",
            "expected TERM++ scope": "No target cell scope exists.",
            "expected artifact state change": "No target artifact mutation exists.",
            "expected proof method": (
                "Source review plus registry and migration-matrix tombstone validation. "
                "No mutation proof applies to a deleted target control."
            ),
            "expectation status": "source_audited_target_tombstone",
            "TERM++ verdict": "NO_TARGET_DELTA",
            "pipeline status": "SOURCE_AUDITED_TARGET_TOMBSTONE",
            "pipeline gap reason": "delete_first_target_cutover_pending",
            "visibility failure": "none",
        }

    target = target_action if target_action else "the registered replacement interaction"
    return {
        "CDP control label": physical_label,
        "backend mutation owner": (
            f"Current visible ImGui callback: {current_owner}. No backend command is admissible. "
            f"After cutover, {target} owns the typed target transaction."
        ),
        "expected user result": (
            f"This inventory row has no independent target action; it is accounted for by {target}."
        ),
        "expected TERM++ result": (
            f"No independent target delta exists for this accounting row. {target} owns its own "
            "dependency-derived TERM++ contract."
        ),
        "expected TERM++ scope": "No independent target cell scope exists for this accounting row.",
        "expected artifact state change": (
            f"No independent artifact mutation exists. {target} owns the atomic target write set."
        ),
        "expected proof method": (
            "Source review plus registry and migration-matrix merge validation. The registered target "
            "action requires headed physical input and a graph-closed receipt."
        ),
        "expectation status": "source_audited_target_merge",
        "TERM++ verdict": "NO_INDEPENDENT_TARGET_DELTA",
        "pipeline status": "SOURCE_AUDITED_TARGET_MERGE",
        "pipeline gap reason": "typed_target_handler_and_delete_first_cutover_pending",
        "visibility failure": "none",
    }


_CANDIDATE_AUDIT_ROWS = {
    "fl4260_row_465__quick_select_all_eligible": (
        "quick.select_all_eligible", None, "tombstone", "bulk flat-pool preset union"
    ),
    "fl4260_row_466__quick_clear_pool": (
        "quick.clear_pool", "material.pool.candidate.clear", "merge", "flat pool_gids clear"
    ),
    "fl4260_row_467__quick_restore_defaults": (
        "quick.restore_defaults", "material.profile.shape_preset.grass_tops", "merge",
        "flat-pool first-look helper",
    ),
    "fl4260_row_482__pool_clear_extended_pool": (
        "pool.clear_extended_pool", "material.pool.candidate.clear", "merge",
        "duplicate flat pool_gids clear",
    ),
    "fl4260_row_483__pool_select_all_catalog": (
        "pool.select_all_catalog", None, "tombstone", "catalog-wide flat-pool union"
    ),
    "fl4260_row_484__pool_invert_catalog_selection": (
        "pool.invert_catalog_selection", None, "tombstone", "catalog inversion of flat pool_gids"
    ),
    "fl4260_row_485__pool_restore_preset_defaults": (
        "pool.restore_preset_defaults", "material.profile.shape_preset.grass_tops", "merge",
        "duplicate flat-pool first-look helper",
    ),
    "fl4260_row_648__clear_and_autofill_tooltips": (
        "visible action tooltips", "their separately registered target actions", "merge",
        "inventory grouping only, with no aggregate callback",
    ),
    "fl4260_row_652__glyph_catalog_morphology_grouping": (
        "glyphpool_catalog", "glyph catalog browse state plus typed addressed-lane actions", "merge",
        "browse filters plus physical chip, drag, keyboard, and context-menu flat-pool writers",
    ),
    "fl4260_row_658__unified_glyphid_shape_pool": (
        "unified_glyphid_shape_pool", "material.pool.candidate controls", "merge",
        "flat pool_gids aggregate",
    ),
    "fl4260_target_663__glyph_set_controls": (
        "candidate controls aggregate", "six candidate controls plus two catalog actions", "merge",
        "target inventory grouping only",
    ),
    "fl4260_row_670__role_ramp_lane_chip_remove": (
        "role.ramp_lane_chip_remove", "material.pool.candidate.remove", "merge",
        "Fl4260RemoveRoleLaneGlyph shifts one visible lane and queues autosave",
    ),
}

for _uid, _args in _CANDIDATE_AUDIT_ROWS.items():
    SOURCE_FACT_OVERRIDES[_uid].update(_candidate_audit_fields(*_args))


def parse_dated_leaf(path: Path) -> dict[str, str]:
    """Parse a historical dated-tree leaf into a key->value dict.

    Dated leaves are simple markdown bullet lists, one fact per line:

        - source_anchor: editor/asciiid.cpp:27575

    We split each bullet on the FIRST ': ' only, because values may
    themselves contain colons (e.g. 'file.cpp:123'). Returns an empty
    dict for keys that are present but blank, which callers treat the
    same as absent.
    """
    facts: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        # Only bullet lines carry facts; headings/prose are skipped.
        if not line.startswith("- "):
            continue
        # Split "key: value" once; bullets without a colon are ignored.
        body = line[2:]
        if ": " not in body and not body.endswith(":"):
            continue
        key, _, value = body.partition(":")
        facts[key.strip()] = value.strip()
    return facts


def field(value: str | None) -> str:
    """Return a CSV/dated value for display, or the MISSING token.

    Centralizing this keeps the honesty rule in one place: an empty
    string and an absent key are both rendered as the explicit MISSING
    marker, never silently dropped.
    """
    value = (value or "").strip()
    return value if value else MISSING


def build_leaf(row: dict[str, str], dated: dict[str, str] | None,
               dated_rel: str | None, existing: dict[str, str] | None = None) -> str:
    """Render one live leaf markdown document.

    Parameters:
      row       -- the CSV row (authority for identity + expectations).
      dated     -- parsed dated-leaf facts, or None if the row has no
                   historical leaf (the 47 'dangling' rows).
      dated_rel -- dated leaf path relative to the dated tree root,
                   recorded so a reader can audit the provenance.

    Returns the full markdown text. The layout mirrors the hand-authored
    R1 leaves (0429/0432): Identity -> Expected Result -> Evidence ->
    Proof Graph, plus an explicit Provenance section that generated
    leaves need and hand-authored ones don't.
    """
    uid = row["control_uid"]
    inv = row["inventory_row"]
    label = row["visible_label"]
    dated = dated or {}
    existing = existing or {}
    receipt_rel = PINNED_RECEIPTS.get(uid)
    receipt_file = (INV_ROOT / receipt_rel) if receipt_rel else None
    receipt_verdict = None
    if receipt_file is not None and receipt_file.is_file():
        receipt_verdict = json.loads(
            receipt_file.read_text(encoding="utf-8")).get("verdict")
    receipt_backed = receipt_verdict is not None
    source_override = SOURCE_FACT_OVERRIDES.get(uid, {})

    # Preserve hand-authored values from an existing live leaf; CSV/dated fall back.
    def get_existing(key: str, csv_val: str | None = None, dated_key: str | None = None) -> str:
        if source_override.get(key):
            return source_override[key]
        if key in existing and existing[key] and MISSING not in existing[key] and "TOMBSTONED" not in existing[key]:
            return existing[key]
        if dated_key and dated.get(dated_key):
            return dated[dated_key]
        return csv_val or ""

    # Leaf generation state, stated up front so no reader can mistake a
    # generated spec surface for a hand-verified one.
    if uid in SOURCE_AUDITED_UIDS:
        state = "SOURCE-AUDITED GENERATED LEAF (FL-4564 migration)"
    elif dated_rel is not None:
        state = "GENERATED FROM CSV + DATED TREE (FL-4564 migration)"
    else:
        state = "GENERATED FROM CSV ONLY - NO DATED SOURCE EXISTS (FL-4564 migration)"

    # Collect every identity/expectation fact. Each tuple is
    # (display name, value, where the value came from).
    identity = [
        ("control_uid", f"`{uid}`", "CSV"),
        ("inventory row", f"`{inv}`", "CSV"),
        ("visible label", f"`{label}`", "CSV"),
        ("CDP control label", field(get_existing("CDP control label", row.get("cdp_control_label"))), "CSV"),
        ("tab", field(row.get("tab")), "CSV"),
        ("container", field(row.get("container")), "CSV"),
        ("section", field(row.get("section")), "CSV"),
        ("widget kind", field(get_existing("widget kind", dated_key="widget")), "dated leaf"),
        ("source anchor", field(get_existing("source anchor", dated_key="source_anchor")), "dated leaf"),
        ("source line", field(get_existing("source line", dated_key="source_line")), "dated leaf"),
        ("backend owner",
         field(get_existing("backend owner", row.get("backend_owner") or dated.get("backend_owner"))),
         "CSV, dated leaf fallback"),
        ("backend mutation owner",
         field(get_existing("backend mutation owner", row.get("backend_mutation_owner"))),
         "source override, CSV fallback"),
        ("render consumer", field(get_existing("render consumer", dated_key="render_consumer")), "dated leaf"),
    ]

    expectations = [
        ("expected user result", field(get_existing("expected user result", row.get("expected_user_result"))), "source override, CSV fallback"),
        ("expected TERM++ result", field(get_existing("expected TERM++ result", row.get("expected_termpp_result"))), "source override, CSV fallback"),
        ("expected TERM++ scope", field(get_existing("expected TERM++ scope", row.get("expected_termpp_scope"))), "source override, CSV fallback"),
        ("expected artifact state change", field(get_existing("expected artifact state change", row.get("expected_artifact_state_change"))), "source override, CSV fallback"),
        ("expected proof method", field(get_existing("expected proof method", row.get("expected_proof_method"))), "source override, CSV fallback"),
        ("expectation status", field(get_existing("expectation status", row.get("expectation_status"))), "source override, CSV fallback"),
        ("TERM++ verdict", field(get_existing("TERM++ verdict", row.get("termpp_verdict"))), "source override, CSV fallback"),
        ("pipeline status", field(get_existing("pipeline status", row.get("pipeline_status"))), "source override, CSV fallback"),
        ("pipeline gap reason", field(get_existing("pipeline gap reason", row.get("pipeline_gap_reason"))), "source override, CSV fallback"),
        # CSV owns current machine status. An older generated leaf may preserve
        # source anchors, yet it must never override a later contradiction,
        # retraction, or diagnostic result recorded in the inventory row.
        ("visibility failure", field(get_existing("visibility failure", row.get("visibility_failure"))), "source override, CSV fallback"),
        ("expected TERM++ surface", field(get_existing("expected TERM++ surface", dated_key="termpp_expected_surface")), "dated leaf"),
        ("expected TERM++ property", field(get_existing("expected TERM++ property", dated_key="termpp_expected_property")), "dated leaf"),
        ("expected TERM++ delta class", field(get_existing("expected TERM++ delta class", dated_key="termpp_expected_delta_class")), "dated leaf"),
    ]

    # Headed-capture evidence pointer: the dated scan stored one cropped
    # screenshot per row inside a dated evidence package. We join the
    # package dir and the crop's package-relative path so the reference
    # is resolvable from the fl4260/ root, then CHECK it on disk so a
    # broken pointer is recorded as broken instead of trusted.
    crop_note = MISSING
    pkg = dated.get("source_package", "").strip()
    crop = dated.get("headed_row_crop_file", "").strip()
    if pkg and crop:
        crop_rel = f"{pkg}/{crop}"
        exists = (INV_ROOT / crop_rel).is_file()
        crop_note = f"`{crop_rel}`" + ("" if exists else " (BROKEN - file not found at generation time)")

    lines: list[str] = []
    lines.append(f"# {label}")
    lines.append("")
    status_suffix = f"RECEIPT {receipt_verdict}" if receipt_backed else "PROOF OPEN"
    lines.append(f"Status: LIVE LEAF / {state} / {status_suffix}")
    lines.append("")
    lines.append(f"This is the live closed-graph leaf for `CURRENT_UI_INVENTORY.csv` row {inv}.")
    if uid in SOURCE_AUDITED_UIDS:
        lines.append("Current source facts are pinned by the generator override; the generated leaf remains a specification surface, not proof.")
    elif dated_rel is not None:
        lines.append("The historical browse copy under `2026-06-22-current-ui-inventory-tree/` is not the proof authority.")
    else:
        lines.append("No historical leaf exists for this row anywhere; this leaf was built from CSV columns alone and needs source-anchor authoring.")
    lines.append("")
    lines.append("## Identity")
    lines.append("")
    for name, value, _src in identity:
        lines.append(f"- {name}: {value}")
    lines.append("")
    lines.append("## Expected Result")
    lines.append("")
    for name, value, _src in expectations:
        lines.append(f"- {name}: {value}")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    lines.append(f"- headed row crop (2026-06-22 scan): {crop_note}")
    if receipt_backed:
        lines.append(f"- proof receipt: `{receipt_rel}`")
        lines.append(f"- receipt verdict: `{receipt_verdict}` (read from the receipt at generation time)")
        lines.append("- capture instruments: S2 atomic frame+cell capture, S4 rendered-buffer dump, S5 bridge-fact dump, S10 console transcript, and S11 MCP/CDP action transcript; definitions live in paper ledger §07.1.1.")
        lines.append(f"- claim ledger: {CLAIM_LEDGER_REFS[uid]}")
    else:
        lines.append("- proof run bundle: none yet - this control is PROOF OPEN and is not pinned in the receipt map.")
        lines.append("- A future proof requires physical input into a registered RuntimeIntentOwner control, the live TERM++ frame, and matching runtime/player/world provenance. This generated leaf does not authorize a run.")
        if uid in CLAIM_LEDGER_REFS:
            lines.append("- required capture instruments: S2 atomic frame+cell capture, S4 rendered-buffer dump, S5 bridge-fact dump, S10 console transcript, and S11 MCP/CDP action transcript; definitions live in paper ledger §07.1.1.")
            lines.append(f"- claim ledger: {CLAIM_LEDGER_REFS[uid]}")
    lines.append("")
    lines.append("## Known Setup Failures")
    lines.append("")
    lines.append("- No row-specific setup failures are recorded for this control yet.")
    lines.append("- The generic FL-4548 failure classes apply: an empty before-side expected set is `SETUP_FAILURE`, a non-empty expected set with zero observed deltas is `FAIL`, and any delta outside the predeclared set is `FAIL` (see `fl4548/proof_evidence_requirements.md`).")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append("- generated by: `scripts/fl4260_generate_live_leaves.py` (regenerable; do not hand-edit facts without also fixing the CSV or noting the divergence here)")
    lines.append(f"- CSV source row: `{inv}` in `CURRENT_UI_INVENTORY.csv`")
    if dated_rel is not None:
        lines.append(f"- dated-tree source: `2026-06-22-current-ui-inventory-tree/{dated_rel}`")
    else:
        lines.append("- dated-tree source: none (row had no historical leaf)")
    missing = [name for name, value, _ in identity + expectations if value == MISSING]
    if missing:
        lines.append(f"- fields still MISSING (authoring required): {', '.join(missing)}")
    else:
        lines.append("- fields still MISSING: none")
    lines.append("")
    lines.append("## Proof Graph")
    lines.append("")
    lines.append("```text")
    lines.append(f"CURRENT_UI_INVENTORY.csv row {inv}")
    lines.append("  -> this live leaf")
    lines.append("  -> registered RuntimeIntentOwner control + live TERM++ runtime witness")
    if receipt_backed:
        lines.append(f"  -> {receipt_rel}")
        lines.append(f"  -> {CLAIM_LEDGER_REFS[uid]}")
    else:
        lines.append("  -> headed before/action/after run bundle (none yet)")
        if uid in CLAIM_LEDGER_REFS:
            lines.append(f"  -> {CLAIM_LEDGER_REFS[uid]}")
        else:
            lines.append("  -> FL-4260 / FL-4564; harness lane FL-4548 / RQ-201c once pinned")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# -------------------------------------------------------------------
# Pinned R1 receipt evidence (graph schema v2, re-audit item 6).
# These are the ONLY inventory rows whose proof status is currently
# backed by a live receipt. The mapping is deliberately explicit and
# small: when a new bundle becomes "CURRENT PASS EVIDENCE" per
# docs/research/ascii/verification/fl4260/fl4548/README.md, this table
# must be updated in the same change — the verdict itself is always
# read live from the receipt file, never hard-coded here.
# Paths are relative to the fl4260/ inventory root.
# -------------------------------------------------------------------
PINNED_RECEIPTS = {
    "fl4260_row_429__material_selectable":
        "fl4548/2026-07-07-r1-followup3/run429/receipt.json",
    "fl4260_row_432__color_fg_r0":
        "fl4548/2026-07-07-r1-followup3/run432/receipt.json",
}

# Where each receipt-backed row's claim lives in the paper ledger.
CLAIM_LEDGER_REFS = {
    "fl4260_row_429__material_selectable":
        "docs/research/glyph-rendering/README.md §05.2 R1 exit gate / §07.1.1 capture instruments",
    "fl4260_row_432__color_fg_r0":
        "docs/research/glyph-rendering/README.md §05.2 R1 exit gate / §07.1.1 capture instruments",
    "fl4260_target_672__mesh_material_id_edge_flow_controls":
        "docs/research/glyph-rendering/README.md §03.1.4 U2/U7/U8 / §05.2 G3 / §07.1.1 capture instruments",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FL-4564 live inventory leaves from the CSV + dated tree.")
    # --dry-run reports what WOULD be generated without writing leaves.
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    parser.add_argument(
        "--graph-only",
        action="store_true",
        help="regenerate INVENTORY_GRAPH.json without rewriting live leaves",
    )
    parser.add_argument(
        "--control-uid",
        action="append",
        default=[],
        metavar="UID",
        help=(
            "regenerate one named generated leaf; repeat for multiple leaves while "
            "indexing every existing inventory row"
        ),
    )
    # --force regenerates leaves this script previously wrote. It still
    # never overwrites a leaf that lacks our generation marker (i.e. the
    # hand-authored R1 leaves), so a forced re-run cannot destroy
    # hand-written proof content.
    parser.add_argument("--force", action="store_true", help="regenerate previously generated leaves")
    args = parser.parse_args()

    if args.graph_only and args.control_uid:
        parser.error("--graph-only cannot be combined with --control-uid")
    if args.force and args.control_uid:
        parser.error("--force cannot be combined with --control-uid")

    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    rows_by_uid: dict[str, dict[str, str]] = {}
    for row in rows:
        uid = (row.get("control_uid") or "").strip()
        if not uid:
            raise SystemExit("FAIL-CLOSED: inventory row has no control_uid")
        if uid in rows_by_uid:
            raise SystemExit(f"FAIL-CLOSED: duplicate control_uid in inventory: {uid}")
        rows_by_uid[uid] = row

    requested_uids = [uid.strip() for uid in args.control_uid]
    if any(not uid for uid in requested_uids):
        raise SystemExit("FAIL-CLOSED: --control-uid requires a non-empty UID")
    selected_uids = set(requested_uids)
    unknown_uids = sorted(selected_uids - rows_by_uid.keys())
    if unknown_uids:
        raise SystemExit(
            "FAIL-CLOSED: unknown --control-uid target(s): "
            + ", ".join(unknown_uids)
        )

    for uid in sorted(selected_uids):
        row = rows_by_uid[uid]
        live_path = INV_ROOT / (row.get("leaf_path") or "")
        if not live_path.is_file():
            continue
        existing_text = live_path.read_text(encoding="utf-8")
        if "scripts/fl4260_generate_live_leaves.py" not in existing_text:
            raise SystemExit(
                f"FAIL-CLOSED: --control-uid targets hand-authored leaf: {uid}"
            )

    if args.graph_only or selected_uids:
        missing_live = [
            row.get("control_uid", "<missing control_uid>")
            for row in rows
            if row.get("control_uid") not in selected_uids
            if not (INV_ROOT / (row.get("leaf_path") or "")).is_file()
        ]
        if missing_live:
            raise SystemExit(
                "FAIL-CLOSED: selected generation cannot index rows without live leaves: "
                f"{missing_live[:3]}"
            )

    # Index entries for INVENTORY_GRAPH.json, one per CSV row.
    graph_rows = []
    leaf_text_by_path: dict[str, str] = {}
    written = skipped_hand = skipped_existing = csv_only = from_dated = 0

    for row in rows:
        leaf_rel = (row.get("leaf_path") or "").strip()
        uid = row.get("control_uid", "")
        live_path = INV_ROOT / leaf_rel
        dated_path = DATED_ROOT / leaf_rel

        entry: dict[str, object] = {
            "control_uid": uid,
            "inventory_row": row.get("inventory_row"),
            "leaf_path": leaf_rel,
        }

        # Default: no live leaf text yet. The new-row path (a CSV row
        # whose leaf_path does not yet resolve to a live file) must not
        # reference an undefined `text` variable.
        text = ""

        if live_path.is_file():
            # A live leaf already exists. Distinguish hand-authored
            # leaves (no generation marker) from our own earlier output.
            text = live_path.read_text(encoding="utf-8")
            leaf_text_by_path[leaf_rel] = text
            generated_marker = "scripts/fl4260_generate_live_leaves.py" in text
            if not generated_marker:
                # Hand-authored (the R1 leaves). Never touched.
                entry["leaf_state"] = "HAND_AUTHORED"
                entry["missing_fields"] = []
                graph_rows.append(entry)
                skipped_hand += 1
                continue
            existing_values: dict[str, str] | None = None
        should_regenerate = args.force or uid in selected_uids
        if should_regenerate and live_path.is_file():
            # Preserve hand-authored values when regenerating. The live leaf
            # (even a tombstoned one) is the authority for values it already
            # carries; CSV/dated only fill gaps.
            existing_values = parse_live_leaf(text)
        if not live_path.is_file():
            # New row: no live leaf exists. Generate it from CSV + dated
            # facts regardless of --force (the leaf does not exist yet).
            dated = parse_dated_leaf(dated_path) if dated_path.is_file() else None
            dated_rel = leaf_rel if dated_path.is_file() else None
            existing_values = None
        elif not should_regenerate:
            # Our own previous output. Canonical graph state comes from recorded
            # provenance, never from whether this invocation rewrote the leaf.
            entry["leaf_state"] = (
                "GENERATED_FROM_CSV_AND_DATED"
                if dated_path.is_file()
                else "GENERATED_FROM_CSV_ONLY"
            )
            entry["missing_fields"] = _missing_from_text(text, row.get("expectation_status", ""))
            graph_rows.append(entry)
            skipped_existing += 1
            continue

        # Pull dated facts when the historical leaf exists.
        dated = parse_dated_leaf(dated_path) if dated_path.is_file() else None
        dated_rel = leaf_rel if dated_path.is_file() else None
        text = build_leaf(row, dated, dated_rel, existing_values)
        leaf_text_by_path[leaf_rel] = text

        if dated_rel is not None:
            entry["leaf_state"] = "GENERATED_FROM_CSV_AND_DATED"
            from_dated += 1
        else:
            entry["leaf_state"] = "GENERATED_FROM_CSV_ONLY"
            csv_only += 1
        entry["missing_fields"] = _missing_from_text(text, row.get("expectation_status", ""))
        graph_rows.append(entry)

        if not args.dry_run and not args.graph_only:
            # Leaf paths contain subdirectories (tab/container/section)
            # that may not exist yet under the live root.
            live_path.parent.mkdir(parents=True, exist_ok=True)
            live_path.write_text(text, encoding="utf-8")
        written += 1

    # ---- v2 proof-linkage augmentation (re-audit item 6) -------------
    # v1 rows only said "a leaf exists and these fields are missing".
    # v2 adds the proof semantics a consumer actually needs: is the row
    # receipt-backed, where is the receipt, what did it last say, where
    # does its claim live in the paper ledger, does the row have real
    # source anchors, and how urgent is its remaining authoring work.
    # expectation_status per uid, straight from the CSV: used below to
    # keep deleted/superseded controls out of the authoring queue (they
    # need a tombstone DECISION, not field authoring).
    status_by_uid = {r.get("control_uid"): (r.get("expectation_status") or "")
                     for r in rows}
    for entry in graph_rows:
        uid = str(entry["control_uid"])
        # Receipt linkage: verdict is read LIVE from the receipt file so
        # the graph can never assert a verdict the receipt does not hold.
        receipt_rel = PINNED_RECEIPTS.get(uid)
        receipt_file = (INV_ROOT / receipt_rel) if receipt_rel else None
        if receipt_file is not None and receipt_file.is_file():
            verdict = json.loads(
                receipt_file.read_text(encoding="utf-8")).get("verdict")
            entry["receipt_path"] = receipt_rel
            entry["last_verdict"] = verdict
            entry["proof_status"] = (
                f"RECEIPT_{verdict}" if verdict else "RECEIPT_UNREADABLE")
            entry["claim_ledger_ref"] = CLAIM_LEDGER_REFS[uid]
        else:
            entry["receipt_path"] = None
            entry["last_verdict"] = None
            entry["proof_status"] = "PROOF_OPEN"
            entry["claim_ledger_ref"] = None
        # Whether a dated (historical browse) source exists for this row.
        # Recomputed from the filesystem so dated-source provenance remains
        # independently checkable against the canonical leaf_state.
        dated_exists = (DATED_ROOT / str(entry["leaf_path"])).is_file()
        entry["dated_source_exists"] = dated_exists
        # Source-anchor status: hand-authored leaves carry a literal
        # "## Source Anchors" section with file:line anchors; generated
        # leaves either inherited an anchor from the dated tree or list
        # source_anchor among their MISSING fields.
        leaf_path = str(entry["leaf_path"])
        leaf_text = leaf_text_by_path.get(leaf_path)
        if leaf_text is None:
            raise RuntimeError(
                f"generator lost the authoritative leaf text for {leaf_path}"
            )
        is_tombstone = _is_tombstone_leaf(leaf_text, status_by_uid.get(uid, ""))
        if entry["leaf_state"] == "HAND_AUTHORED":
            entry["source_anchor_status"] = (
                "AUTHORED_FILE_LINE" if "## Source Anchors" in leaf_text
                else "MISSING")
        elif is_tombstone:
            entry["source_anchor_status"] = "TOMBSTONED"
        elif not dated_exists:
            # No dated source exists, but the leaf text may still carry
            # an authored file:line anchor from source tracing. Check
            # the leaf text for a .cpp/.h:NNNN pattern before falling
            # back to MISSING.
            if re.search(
                r'[A-Za-z0-9_./-]+\.(?:c|cc|cpp|h|hpp|js|jsx|py|ts|tsx):\d+',
                leaf_text,
            ):
                entry["source_anchor_status"] = "AUTHORED_FILE_LINE"
            else:
                entry["source_anchor_status"] = "MISSING"
        elif "source anchor" in entry["missing_fields"]:
            entry["source_anchor_status"] = "MISSING"
        else:
            entry["source_anchor_status"] = "FROM_DATED_TREE"
        # Mechanical authoring priority (re-audit item 7 queue). R2/RQ
        # sampling is not pinned yet, so the queue orders by evidence
        # poverty: rows with NO dated source at all need the most work.
        # Controls the CSV itself marks deleted/superseded are pulled
        # OUT of the authoring queue into a tombstone-decision class —
        # authoring spec fields for a control that no longer exists
        # would be busywork at best and fabrication at worst.
        status = status_by_uid.get(uid, "")
        is_tombstone_candidate = (status.startswith("control_deleted")
                                  or status.startswith("source_deleted")
                                  or "superseded" in status)
        if entry["proof_status"].startswith("RECEIPT_"):
            entry["authoring_priority"] = "P0_RECEIPT_BACKED"
        elif is_tombstone_candidate:
            entry["authoring_priority"] = "P5_TOMBSTONE_DECISION"
            # Derive tombstone_reason from the CSV expectation_status.
            gap = status_by_uid.get(uid, "")
            if gap.startswith("control_deleted"):
                entry["tombstone_reason"] = "CONTROL_DELETED"
            elif gap.startswith("source_deleted"):
                entry["tombstone_reason"] = "SOURCE_DELETED"
            elif "superseded" in gap:
                entry["tombstone_reason"] = "SOURCE_SUPERSEDED"
            else:
                entry["tombstone_reason"] = "UNKNOWN"
        elif not dated_exists:
            entry["authoring_priority"] = "P1_NO_DATED_SOURCE"
        elif entry["source_anchor_status"] == "MISSING":
            entry["authoring_priority"] = "P2_ANCHOR_MISSING"
        elif entry["missing_fields"]:
            entry["authoring_priority"] = "P3_MINOR_FIELDS"
        else:
            entry["authoring_priority"] = "P4_SPEC_FIELDS_PRESENT"

    # ---- Emit the machine-readable closed-graph index ---------------
    # Keyed to the CSV content hash (NOT a timestamp) so regeneration is
    # deterministic: same CSV + same leaves -> byte-identical JSON.
    csv_sha = hashlib.sha256(CSV_PATH.read_bytes()).hexdigest()
    priority_histogram: dict[str, int] = {}
    for e in graph_rows:
        key = str(e["authoring_priority"])
        priority_histogram[key] = priority_histogram.get(key, 0) + 1
    graph = {
        "schema": "fl4260.inventory_graph.v2",
        "source_csv": "CURRENT_UI_INVENTORY.csv",
        "source_csv_sha256": csv_sha,
        "primary_key": "control_uid",
        "inventory_row_is_display_only": True,
        "generator": "scripts/fl4260_generate_live_leaves.py",
        "note": (
            "Machine-readable closed-graph index (FL-4564). One entry per CSV row; "
            "control_uid is the primary key. inventory_row is display-only and is NOT "
            "unique (duplicate inventory_row values exist). "
            "leaf_state HAND_AUTHORED = written by a human/proof session, never regenerated. "
            "GENERATED_* = produced by the generator from recorded facts only; "
            "missing_fields lists proof-critical spec gaps that still need real authoring. "
            "v2 adds proof linkage per row: proof_status (RECEIPT_<verdict> for the "
            "pinned R1 rows, read live from the receipt; PROOF_OPEN otherwise), "
            "receipt_path, last_verdict, claim_ledger_ref, dated_source_exists, "
            "source_anchor_status, and a mechanical authoring_priority queue "
            "(P0 receipt-backed .. P4 spec fields present). A generated leaf is a "
            "spec surface, not a proof: proof status lives in run bundles and "
            "receipts under fl4548/."
        ),
        "summary": {
            "rows": len(rows),
            "hand_authored": sum(
                1 for e in graph_rows if e["leaf_state"] == "HAND_AUTHORED"
            ),
            "generated_from_csv_and_dated": sum(
                1 for e in graph_rows
                if e["leaf_state"] == "GENERATED_FROM_CSV_AND_DATED"
            ),
            "generated_from_csv_only": sum(
                1 for e in graph_rows
                if e["leaf_state"] == "GENERATED_FROM_CSV_ONLY"
            ),
            "rows_with_missing_fields": sum(1 for e in graph_rows if e["missing_fields"]),
            "receipt_backed_rows": sum(
                1 for e in graph_rows
                if str(e["proof_status"]).startswith("RECEIPT_")),
            "authoring_priority_histogram": priority_histogram,
        },
        "rows": graph_rows,
    }
    if not args.dry_run:
        GRAPH_PATH.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    # Human-facing run summary.
    mode = "DRY-RUN" if args.dry_run else ("WROTE GRAPH ONLY" if args.graph_only else "WROTE")
    print(f"[fl4260-leaf-gen] {mode}: {written} leaves "
          f"({from_dated} from CSV+dated, {csv_only} CSV-only), "
          f"{skipped_hand} hand-authored skipped, {skipped_existing} generated-existing skipped")
    print(f"[fl4260-leaf-gen] rows with MISSING fields: {graph['summary']['rows_with_missing_fields']}/{len(rows)}")
    if not args.dry_run:
        print(f"[fl4260-leaf-gen] index: {GRAPH_PATH.relative_to(ROOT)}")


# Proof-critical fields that must be present in a live leaf. Evidence pointers
# (e.g. headed row crops) are tracked but not counted as proof-critical spec gaps.
_PROOF_CRITICAL_IDENTITY = [
    "widget kind",
    "source anchor",
    "source line",
    "backend owner",
    "render consumer",
    "visibility failure",
]
_PROOF_CRITICAL_EXPECTED = [
    "expected TERM++ surface",
    "expected TERM++ property",
    "expected TERM++ delta class",
]


def _is_tombstone_leaf(text: str, status: str = "") -> bool:
    """A leaf is tombstoned when it or the CSV explicitly marks it non-proof-critical."""
    if "TOMBSTONED" in text.splitlines()[2] if len(text.splitlines()) > 2 else False:
        return True
    low = status.lower()
    return (low.startswith("control_deleted")
            or low.startswith("source_deleted")
            or "superseded" in low)


def parse_live_leaf(text: str) -> dict[str, str]:
    """Parse an existing live leaf's Identity and Expected Result sections.

    This lets a forced re-generation preserve hand-authored values (e.g. source
    anchors written during FL-4564 Item 7) instead of overwriting them with the
    stale CSV/dated-tree fallback.
    """
    values: dict[str, str] = {}
    section = None
    for line in text.splitlines():
        if line.startswith("## Identity"):
            section = "identity"
            continue
        if line.startswith("## Expected Result"):
            section = "expected"
            continue
        if line.startswith("## "):
            section = None
            continue
        if section and line.startswith("- "):
            body = line[2:]
            if ": " not in body and not body.endswith(":"):
                continue
            key, _, value = body.partition(":")
            values[key.strip()] = value.strip()
    return values


def _missing_from_text(text: str, status: str = "") -> list[str]:
    """Compute missing proof-critical fields from the leaf body itself.

    Tombstoned rows intentionally carry TOMBSTONED markers; they are not
    proof-critical and have no missing spec fields. For all other rows we
    parse Identity and Expected Result bullets and flag any value that is
    empty, MISSING, or TOMBSTONED as a real gap.
    """
    if _is_tombstone_leaf(text, status):
        return []
    values = parse_live_leaf(text)
    missing: list[str] = []
    for key in _PROOF_CRITICAL_IDENTITY + _PROOF_CRITICAL_EXPECTED:
        val = values.get(key, "").strip()
        if not val or MISSING in val or "TOMBSTONED" in val:
            missing.append(key)
    # CDP control label is required to drive a headed proof, but the CSV owns it.
    cdp = values.get("CDP control label", "").strip()
    if not cdp or MISSING in cdp:
        missing.append("CDP control label")
    return missing


if __name__ == "__main__":
    main()
