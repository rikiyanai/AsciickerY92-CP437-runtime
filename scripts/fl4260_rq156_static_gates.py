#!/usr/bin/env python3
"""FL-4260 RQ-156 static code-inspection gate runner (10 gates).

This is a POST-AUTHORING static analysis gate — it inspects source files for the
code evidence that each gate's invariant holds. It does NOT run the game, does NOT
launch the editor, and does NOT mutate any file.

LAW 16: PASS here means "code evidence found in the current static snapshot".
It is NOT closure, does NOT mean FL-4260 is resolved, and does NOT substitute
for the canonical RQ-156 headed two-tab VPS proof run + Law 16 human signoff.

Gate classes (Law 13):
  evidence_* : code / artifact existence checks
  gameplay_* : runtime-path wiring (IMPLEMENTED_UNPROVEN until headed run proves it)

Gates:
  1. evidence_fl4260_renderer_mode_declared
  2. evidence_fl4260_unfiltered_lut_dead
  3. evidence_fl4260_morphology_runtime_profile_live_guard
  4. gameplay_fl4260_profile_bucket_lane_used            [IMPLEMENTED_UNPROVEN]
  5. evidence_fl4260_old_preset_owners_dead
  6. evidence_fl4260_profile_trace_complete
  7. evidence_fl4260_diagnostic_mode_excluded_from_closure
  8. evidence_fl4260_all_visual_leaves_captured
  9. evidence_fl4260_legacy_visual_leaves_hidden
 10. evidence_fl4260_rendering_preview_delta                [STATIC_PASS]

Front door:
  python3 scripts/fl4260_rq156_static_gates.py            # human table
  python3 scripts/fl4260_rq156_static_gates.py --json     # machine-readable
  python3 scripts/fl4260_rq156_static_gates.py --gate G   # single gate

Exit codes:
  0 - all gates PASS or IMPLEMENTED_UNPROVEN
  1 - one or more gates FAIL
  2 - error (file not found / read error)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

ASCIIID = REPO_ROOT / "editor" / "asciiid.cpp"
HARRI_CPP = REPO_ROOT / "engine" / "fl4131_runtime_harri_resolver.cpp"
HARRI_H = REPO_ROOT / "engine" / "fl4131_runtime_harri_resolver.h"
RENDER_RESOLVE = REPO_ROOT / "engine" / "render" / "render_resolve.cpp"
REVIEW_RECEIPTS = REPO_ROOT / "scripts" / "fl4260_review_receipts.py"

PASS = "pass"
IMPLEMENTED_UNPROVEN = "implemented_unproven"
FAIL = "fail"
OPEN = "open"
ERROR = "error"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e}") from e


def _count(text: str, pattern: str) -> int:
    return text.count(pattern)


def _search(text: str, pattern: str) -> bool:
    return pattern in text


def _re_search(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text))


# ── Gate implementations ──────────────────────────────────────────────────────

def check_evidence_fl4260_renderer_mode_declared() -> tuple[str, str]:
    """Renderer-wide Material Look mode is deleted; per-material live facts remain."""
    try:
        harri_h = _read(HARRI_H)
        asciiid = _read(ASCIIID)
        render_internal = _read(REPO_ROOT / "engine" / "render" / "render_internal.h")
    except RuntimeError as e:
        return ERROR, str(e)

    old_mode_deleted = (
        not _search(harri_h, "FL4131_RENDER_MODE_PROFILE")
        and not _search(harri_h, "FL4131_RENDER_MODE_CP437")
        and not _search(harri_h, "Fl4131RendererMode")
        and not _search(asciiid, "FL4260_SET_RENDER_MODE")
        and not _search(asciiid, "FL4260_GET_RENDER_MODE")
    )
    has_live_facts = (
        _search(harri_h, "runtime_profile_live")
        and _search(render_internal, "per-frame Material Look session facts")
        and _search(render_internal, "There is no")
        and _search(render_internal, "renderer-wide glyph-policy mode")
    )

    if old_mode_deleted and has_live_facts:
        return PASS, (
            "renderer-wide Material Look mode enum and CDP commands are absent; "
            "runtime_profile_live remains as per-material session fact"
        )
    missing = []
    if not old_mode_deleted:
        missing.append("stale renderer-wide Material Look mode surface still present")
    if not has_live_facts:
        missing.append("per-material Material Look session fact declaration missing")
    return FAIL, "; ".join(missing)


def check_evidence_fl4260_unfiltered_lut_dead() -> tuple[str, str]:
    """Old force-live bypass deleted; no live unguarded setter remains."""
    try:
        resolve = _read(RENDER_RESOLVE)
        harri_cpp = _read(HARRI_CPP)
    except RuntimeError as e:
        return ERROR, str(e)

    # render_resolve.cpp must document the removed bypass owner
    has_removed_owner_comment = (
        _search(resolve, "FL-4260 RQ-154 b")
        and _search(resolve, "old proof-toggle live-state owner deleted")
    )
    # No live assignment to the old force flag outside deleted/commented code.
    # (the flag itself may still exist as a variable but must not be set)
    force_set_re = re.compile(
        r"g_fl4260_proof_force_runtime_profile_live\s*=\s*(?:true|1|false|0)"
    )
    active_sets = [
        m.group(0)
        for m in force_set_re.finditer(resolve + harri_cpp)
    ]
    # Filter out commented lines (very rough heuristic)
    active_sets = [s for s in active_sets if not s.startswith("//")]

    if has_removed_owner_comment and not active_sets:
        return PASS, "render_resolve.cpp documents deleted proof-toggle live-state owner; no live force-setter found"
    problems = []
    if not has_removed_owner_comment:
        problems.append("FL-4260 RQ-154 b deleted-owner comment missing from render_resolve.cpp")
    if active_sets:
        problems.append(f"live force-setter found: {active_sets[:2]}")
    return FAIL, "; ".join(problems)


def check_evidence_fl4260_morphology_runtime_profile_live_guard() -> tuple[str, str]:
    """Direct Material Look table is loaded and uses per-material guards."""
    try:
        harri_cpp = _read(HARRI_CPP)
        harri_h = _read(HARRI_H)
    except RuntimeError as e:
        return ERROR, str(e)

    fn_defined = _re_search(harri_cpp, r"void\s+Fl4260LoadV1ProfileTable\s*\(")
    fn_called_from_runtime_load = _search(
        harri_cpp,
        "Fl4131RuntimeLoadProfiles();\n\t// FL-4260 RQ-154 b: load live v1.json"
    ) and _search(harri_cpp, "Fl4260LoadV1ProfileTable();")
    live_field = _search(harri_h, "runtime_profile_live")
    live_guard = (
        _search(harri_cpp, "return g_fl4260_v1_profiles[terrain_mat_id].loaded")
        and _search(harri_cpp, "vp->live")
        and _search(harri_cpp, "Fl4260MaterialProfileLive")
    )

    if fn_defined and fn_called_from_runtime_load and live_field and live_guard:
        return PASS, (
            "Fl4260LoadV1ProfileTable defined and called from runtime load; "
            "runtime_profile_live summary field present; per-material loaded/live guards present"
        )
    problems = []
    if not fn_defined:
        problems.append("Fl4260LoadV1ProfileTable not defined in harri.cpp")
    if not fn_called_from_runtime_load:
        problems.append("Fl4260LoadV1ProfileTable is not called from runtime load path")
    if not live_field:
        problems.append("runtime_profile_live missing from harri.h")
    if not live_guard:
        problems.append("loaded/live guard missing from harri.cpp")
    return FAIL, "; ".join(problems)


def check_gameplay_fl4260_profile_bucket_lane_used() -> tuple[str, str]:
    """Profile bucket (g_fl4260_v1_profiles[].loaded/live) consulted at render time.

    This is IMPLEMENTED_UNPROVEN: the bucket lane is wired in harri.cpp but no
    headed two-tab VPS run has yet demonstrated CPU/GPU parity through the
    live Material Look path (RQ-154/RQ-156 canonical proof still pending).
    """
    try:
        harri_cpp = _read(HARRI_CPP)
    except RuntimeError as e:
        return ERROR, str(e)

    has_profiles_array = _search(harri_cpp, "g_fl4260_v1_profiles")
    has_live_read = _re_search(harri_cpp, r"g_fl4260_v1_profiles\[.+?\]\.loaded") or \
        _re_search(harri_cpp, r"g_fl4260_v1_profiles\[.+?\]\.live")
    has_bucket_propagation = _search(harri_cpp, "Fl4260SetActiveProfileBuckets")

    if has_profiles_array and has_live_read:
        detail = (
            "g_fl4260_v1_profiles[] declared and loaded/live read in harri.cpp"
        )
        if has_bucket_propagation:
            detail += "; Fl4260SetActiveProfileBuckets lane-sensitive propagation wired"
        detail += "; no headed VPS proof run yet (RQ-154/RQ-156 pending)"
        return IMPLEMENTED_UNPROVEN, detail
    problems = []
    if not has_profiles_array:
        problems.append("g_fl4260_v1_profiles[] missing from harri.cpp")
    if not has_live_read:
        problems.append("no loaded/live read on g_fl4260_v1_profiles in harri.cpp")
    return FAIL if problems else IMPLEMENTED_UNPROVEN, "; ".join(problems)


def check_evidence_fl4260_old_preset_owners_dead() -> tuple[str, str]:
    """Legacy glyph_plane writers gated behind Fl4260LegacyMaterialUiEnabled()."""
    try:
        asciiid = _read(ASCIIID)
    except RuntimeError as e:
        return ERROR, str(e)

    gate_count = _count(asciiid, "Fl4260LegacyMaterialUiEnabled")
    # Glyph Presets section gated: `if (Fl4260LegacyMaterialUiEnabled())`
    preset_gated = _re_search(
        asciiid,
        r"if\s*\(\s*Fl4260LegacyMaterialUiEnabled\s*\(\s*\)\s*\)[^}]{0,400}Glyph Presets"
    )
    # Live paint section gated: `if (!Fl4260LegacyMaterialUiEnabled())`
    paint_gated = _re_search(
        asciiid,
        r"if\s*\(\s*!\s*Fl4260LegacyMaterialUiEnabled\s*\(\s*\)\s*\)"
    )

    if gate_count >= 15 and paint_gated:
        return PASS, (
            f"Fl4260LegacyMaterialUiEnabled() called {gate_count}× in asciiid.cpp; "
            f"live-paint gate present; preset section gated={preset_gated}"
        )
    problems = []
    if gate_count < 15:
        problems.append(f"Fl4260LegacyMaterialUiEnabled call count={gate_count} (expected ≥15)")
    if not paint_gated:
        problems.append("live-paint !Fl4260LegacyMaterialUiEnabled gate missing")
    return FAIL, "; ".join(problems)


def check_evidence_fl4260_profile_trace_complete() -> tuple[str, str]:
    """Trace highlight, route chain, winner chips, and receipt backend all present."""
    try:
        asciiid = _read(ASCIIID)
    except RuntimeError as e:
        return ERROR, str(e)

    has_hl_enabled = _search(asciiid, "g_fl4260_trace_highlight_enabled")
    has_hl_mode = _search(asciiid, "g_fl4260_trace_highlight_mode")
    has_selected_only = _search(asciiid, "Highlight selected material only") and \
        _search(asciiid, "Broad ramp, density, missing-policy, and all-terrain tint modes are deleted")
    has_direct_edit_badge = _search(asciiid, "MATERIAL LOOK EDIT / PROOF PENDING") and \
        _search(asciiid, "Direct edit active")
    has_route_candidate_set = _search(asciiid, "Route candidate set:") and \
        _search(asciiid, "candidate GlyphId")
    has_winner_chips = _search(asciiid, "Selected cell glyph:") and \
        _search(asciiid, "CPU winner") and \
        _search(asciiid, "GPU final")
    has_decision_flow = _search(asciiid, "Decision flow:") and \
        _search(asciiid, "material_id: terrain:%d") and \
        _search(asciiid, "route: %s lane -> %s") and \
        _search(asciiid, "candidate set: %d GlyphIds") and \
        _search(asciiid, "winner_gid: CPU %u%s")
    has_edge_chain = _search(asciiid, "Edge source: material_id -> vertical_relation=%d (%s) -> Edge lane -> candidate set -> winner_gid")
    has_direction_chain = _search(asciiid, "Direction source: material_id -> cell_direction_idx=%d (%s) -> Direction lane -> candidate set -> winner_gid")
    has_flow_chain = _search(asciiid, "Flow source: material_id -> cell_flow_idx=%d (%s) -> Flow lane -> candidate set -> winner_gid")
    has_review_receipts = REVIEW_RECEIPTS.exists()

    if (has_hl_enabled and has_hl_mode and has_selected_only and has_direct_edit_badge
            and has_route_candidate_set and has_winner_chips and has_decision_flow
            and has_edge_chain and has_direction_chain and has_flow_chain and has_review_receipts):
        return PASS, (
            "g_fl4260_trace_highlight_enabled/mode statics present; "
            "selected-material-only highlight present; broad modes deleted; "
            "direct Material Look edit proof-pending badge present; route candidate set, "
            "CPU/GPU winner chips, wrapped decision flow, and Edge/Direction/Flow source chains present; "
            "evidence receipt backend exists"
        )
    problems = []
    if not has_hl_enabled:
        problems.append("g_fl4260_trace_highlight_enabled missing")
    if not has_hl_mode:
        problems.append("g_fl4260_trace_highlight_mode missing")
    if not has_selected_only:
        problems.append("selected-material-only highlight text missing")
    if not has_direct_edit_badge:
        problems.append("direct Material Look edit proof-pending badge missing from asciiid.cpp")
    if not has_route_candidate_set:
        problems.append("route candidate set display missing")
    if not has_winner_chips:
        problems.append("selected-cell CPU/GPU winner chips missing")
    if not has_decision_flow:
        problems.append("wrapped decision flow fields missing")
    if not has_edge_chain:
        problems.append("Edge source chain missing")
    if not has_direction_chain:
        problems.append("Direction source chain missing")
    if not has_flow_chain:
        problems.append("Flow source chain missing")
    if not has_review_receipts:
        problems.append(f"fl4260_review_receipts.py not found at {REVIEW_RECEIPTS}")
    return FAIL, "; ".join(problems)


def check_evidence_fl4260_diagnostic_mode_excluded_from_closure() -> tuple[str, str]:
    """Diagnostic badge shown when legacy mode active; closure field in receipt backend."""
    try:
        asciiid = _read(ASCIIID)
        review_src = _read(REVIEW_RECEIPTS) if REVIEW_RECEIPTS.exists() else ""
    except RuntimeError as e:
        return ERROR, str(e)

    # Diagnostic surfaces must visibly label themselves as excluded from closure.
    has_closure_excluded_text = (
        _search(asciiid, "closure-excluded")
        and _search(asciiid, "diagnostic ONLY -- not closure proof")
        and _search(asciiid, "diagnostic-only; never mutates live profile policy")
    )
    # Fl4260LegacyDiagnosticBadge called when legacy mode is active
    has_badge_fn = _search(asciiid, "Fl4260LegacyDiagnosticBadge")
    # Evidence receipt set exposes closure field
    has_closure_field = _search(review_src, '"closure"') or _search(review_src, "'closure'")
    has_review_receipts = REVIEW_RECEIPTS.exists()

    if has_closure_excluded_text and has_badge_fn and has_review_receipts and has_closure_field:
        return PASS, (
            "diagnostic closure-excluded text present in asciiid.cpp; "
            "Fl4260LegacyDiagnosticBadge() wired; "
            "closure field in fl4260_review_receipts.py"
        )
    problems = []
    if not has_closure_excluded_text:
        problems.append("diagnostic closure-excluded text missing from asciiid.cpp")
    if not has_badge_fn:
        problems.append("Fl4260LegacyDiagnosticBadge missing from asciiid.cpp")
    if not has_review_receipts:
        problems.append("fl4260_review_receipts.py missing")
    if not has_closure_field:
        problems.append("closure field missing from fl4260_review_receipts.py")
    return FAIL, "; ".join(problems)


def check_evidence_fl4260_all_visual_leaves_captured() -> tuple[str, str]:
    """Every appearance-relevant EDIT leaf has a CDP capture route."""
    try:
        asciiid = _read(ASCIIID)
    except RuntimeError as e:
        return ERROR, str(e)

    has_driver = _search(asciiid, "FL4260_CAPTURE_EDIT_LEAF")
    has_inventory = _search(asciiid, "FL4260_DUMP_VISUAL_LEAF_INVENTORY")
    leaves = {
        "character": "g_fl4260_force_scroll_edit_character",
        "materials": "g_fl4260_force_scroll_edit_materials",
        "palette": "g_fl4260_force_scroll_edit_palette",
        "options": "g_fl4260_force_scroll_edit_options",
    }
    missing = [leaf for leaf, token in leaves.items() if not _search(asciiid, token)]

    if has_driver and has_inventory and not missing:
        return PASS, (
            "FL4260_CAPTURE_EDIT_LEAF plus FL4260_DUMP_VISUAL_LEAF_INVENTORY present; "
            "Character/Materials/Palette/Options scroll targets wired"
        )
    problems = []
    if not has_driver:
        problems.append("FL4260_CAPTURE_EDIT_LEAF missing")
    if not has_inventory:
        problems.append("FL4260_DUMP_VISUAL_LEAF_INVENTORY missing")
    if missing:
        problems.append(f"missing scroll targets: {','.join(missing)}")
    return FAIL, "; ".join(problems)


def check_evidence_fl4260_legacy_visual_leaves_hidden() -> tuple[str, str]:
    """Default EDIT visual leaves must not compete with RENDERING ownership."""
    try:
        asciiid = _read(ASCIIID)
    except RuntimeError as e:
        return ERROR, str(e)

    # The source can still contain the legacy writers, but they must be reachable
    # only through the explicit terminal diagnostic flag. The inventory is the CDP
    # readback surface, and the call boundary proves normal EDIT does not draw the
    # legacy leaf owners.
    inventory_hidden = all(
        _re_search(
            asciiid,
            rf'\\"name\\":\\"{leaf}\\"[\s\S]{{0,220}}'
            rf'\\"default_visible\\":false[\s\S]{{0,220}}'
            rf'FL4260_CAPTURE_EDIT_LEAF {leaf} <dir>[\s\S]{{0,220}}'
            rf'\\"legacy_gated\\":true'
        )
        for leaf in ("character", "palette", "options")
    )
    call_boundary = _re_search(
        asciiid,
        r"const\s+bool\s+fl4260_legacy_visual_ui\s*=\s*Fl4260LegacyMaterialUiEnabled\s*\(\s*\)\s*;"
        r"[\s\S]{0,500}if\s*\(\s*fl4260_legacy_visual_ui\s*\)\s*\{[\s\S]{0,260}render_character_owner\s*\(\s*\)"
        r"[\s\S]{0,500}render_materials_owner\s*\(\s*\)"
        r"[\s\S]{0,500}if\s*\(\s*fl4260_legacy_visual_ui\s*\)\s*\{[\s\S]{0,260}render_palette_owner\s*\(\s*\)"
        r"[\s\S]{0,220}render_options_owner\s*\(\s*\)"
    )
    has_diagnostic_badges = (
        _search(asciiid, "EDIT Character legacy glyph browser")
        and _search(asciiid, "EDIT Palette legacy palette/material appearance controls")
        and _search(asciiid, "EDIT Options legacy appearance preview toggles")
    )

    if inventory_hidden and call_boundary and has_diagnostic_badges:
        return PASS, "EDIT Character/Palette/Options hidden behind legacy diagnostic flag"
    problems = []
    if not inventory_hidden:
        problems.append("inventory still marks Character/Palette/Options default-visible")
    if not call_boundary:
        problems.append("default EDIT call boundary still exposes Character/Palette/Options")
    if not has_diagnostic_badges:
        problems.append("legacy diagnostic badges missing for hidden visual leaves")
    return FAIL, "; ".join(problems)


def check_evidence_fl4260_rendering_preview_delta() -> tuple[str, str]:
    """Rendering direct-edit → render_resolve → detached TERM++ delta pipeline exists.

    This is a STATIC_PASS: the code path from RENDERING sidebar edits through
    Fl4260ApplyProfileDirectEdit into render_resolve (via
    Fl4260GetActiveProfileColor) and out to the detached TERM++ rendered buffer
    dump (FL4207_DUMP_TERMPP_RENDERED_BUFFER) is present in source. The palette
    starter precondition activator (FL4260_APPLY_PALETTE_STARTER) and bridge cell
    dump (FL4260_DUMP_BRIDGE_CELLS) must also exist for before/after proof.

    This does NOT prove the delta is visible in a particular fixture/camera.
    That requires a canonical run-backed evidence package.
    """
    try:
        asciiid = _read(ASCIIID)
        resolve = _read(RENDER_RESOLVE)
    except RuntimeError as e:
        return ERROR, str(e)

    has_direct_edit = _search(asciiid, "Fl4260ApplyProfileDirectEdit")
    has_palette_starter = _search(asciiid, "FL4260_APPLY_PALETTE_STARTER")
    has_termpp_dump = _search(asciiid, "FL4207_DUMP_TERMPP_RENDERED_BUFFER")
    has_bridge_dump = _search(asciiid, "FL4260_DUMP_BRIDGE_CELLS")
    has_render_consumer = _search(resolve, "Fl4260GetActiveProfileColor")

    if has_direct_edit and has_palette_starter and has_termpp_dump and has_bridge_dump and has_render_consumer:
        return PASS, (
            "Fl4260ApplyProfileDirectEdit in asciiid.cpp; "
            "Fl4260GetActiveProfileColor called in render_resolve.cpp; "
            "FL4207_DUMP_TERMPP_RENDERED_BUFFER + FL4260_DUMP_BRIDGE_CELLS + "
            "FL4260_APPLY_PALETTE_STARTER CDP commands present"
        )
    problems = []
    if not has_direct_edit:
        problems.append("Fl4260ApplyProfileDirectEdit missing from asciiid.cpp")
    if not has_palette_starter:
        problems.append("FL4260_APPLY_PALETTE_STARTER MCP missing from asciiid.cpp")
    if not has_termpp_dump:
        problems.append("FL4207_DUMP_TERMPP_RENDERED_BUFFER MCP missing from asciiid.cpp")
    if not has_bridge_dump:
        problems.append("FL4260_DUMP_BRIDGE_CELLS MCP missing from asciiid.cpp")
    if not has_render_consumer:
        problems.append("Fl4260GetActiveProfileColor not called in render_resolve.cpp")
    return FAIL, "; ".join(problems)


# ── Registry ──────────────────────────────────────────────────────────────────

GATES: dict[str, Any] = {
    "evidence_fl4260_renderer_mode_declared":
        check_evidence_fl4260_renderer_mode_declared,
    "evidence_fl4260_unfiltered_lut_dead":
        check_evidence_fl4260_unfiltered_lut_dead,
    "evidence_fl4260_morphology_runtime_profile_live_guard":
        check_evidence_fl4260_morphology_runtime_profile_live_guard,
    "gameplay_fl4260_profile_bucket_lane_used":
        check_gameplay_fl4260_profile_bucket_lane_used,
    "evidence_fl4260_old_preset_owners_dead":
        check_evidence_fl4260_old_preset_owners_dead,
    "evidence_fl4260_profile_trace_complete":
        check_evidence_fl4260_profile_trace_complete,
    "evidence_fl4260_diagnostic_mode_excluded_from_closure":
        check_evidence_fl4260_diagnostic_mode_excluded_from_closure,
    "evidence_fl4260_all_visual_leaves_captured":
        check_evidence_fl4260_all_visual_leaves_captured,
    "evidence_fl4260_legacy_visual_leaves_hidden":
        check_evidence_fl4260_legacy_visual_leaves_hidden,
    "evidence_fl4260_rendering_preview_delta":
        check_evidence_fl4260_rendering_preview_delta,
}


def run_gates(gate_filter: str | None = None) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for name, fn in GATES.items():
        if gate_filter and gate_filter != name:
            continue
        status, detail = fn()
        results[name] = {"status": status, "detail": detail}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FL-4260 RQ-156 static code-inspection gates (9 gates)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser.add_argument("--gate", metavar="GATE", help="run a single gate by name")
    args = parser.parse_args()

    results = run_gates(args.gate)

    if args.json:
        any_fail = any(r["status"] == FAIL for r in results.values())
        any_error = any(r["status"] == ERROR for r in results.values())
        print(json.dumps({
            "fl": "FL-4260",
            "scope": "static_code_inspection",
            "law16_disclaimer": "PASS here = code evidence only. Not closure. Headed VPS proof + human signoff required.",
            "overall": "fail" if any_fail or any_error else "pass_or_unproven",
            "gates": results,
        }, indent=2))
    else:
        print(f"FL-4260 RQ-156 static code-inspection gates ({len(results)})")
        print(f"  scope: static source inspection — not a runtime proof")
        print(f"  LAW 16: PASS ≠ closure. Headed two-tab VPS run + human signoff required.")
        print()
        col = 55
        any_fail = False
        for name, r in results.items():
            status = r["status"]
            detail = r["detail"]
            icon = {
                PASS: "PASS",
                IMPLEMENTED_UNPROVEN: "IMPL",
                FAIL: "FAIL",
                OPEN: "OPEN",
                ERROR: "ERR ",
            }.get(status, "????")
            print(f"  [{icon}] {name:<{col}}  {detail}")
            if status in (FAIL, ERROR):
                any_fail = True
        print()
        if any_fail:
            print("  OVERALL: FAIL — one or more gates not met")
        else:
            print("  OVERALL: PASS (static evidence) — headed VPS proof pending")

    any_fail = any(r["status"] in (FAIL, ERROR) for r in results.values())
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
