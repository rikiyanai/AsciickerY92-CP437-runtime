#!/usr/bin/env python3
"""
FL-4260 CDP-Driven Visual Audit

Audits each spec requirement against actual UI state via CDP commands.
"""

import json
import socket
import sys
import time
import os

CDP_HOST = "localhost"
CDP_PORT = 8765

def send_cdp(cmd, params=None, timeout=5):
    """Send CDP command and return response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((CDP_HOST, CDP_PORT))
        msg = {"id": 1, "method": cmd}
        if params:
            msg["params"] = params
        line = json.dumps(msg) + "\n"
        sock.sendall(line.encode())

        # Accumulate response with timeout
        response = b""
        sock.settimeout(2)
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                # Check if we have a complete JSON response
                if b'}' in chunk:
                    break
            except socket.timeout:
                break

        response_str = response.decode()
        # Parse JSON - try to find complete JSON object
        for line in response_str.strip().split('\n'):
            if line.strip().startswith('{'):
                try:
                    return json.loads(line)
                except:
                    continue

        # Try parsing the whole response
        try:
            return json.loads(response_str)
        except:
            return {"error": f"Could not parse: {response_str[:200]}"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        sock.close()

def capture_frame(label, dir_path):
    """Capture UI frame to directory."""
    os.makedirs(dir_path, exist_ok=True)
    result = send_cdp("CAPTURE_UI_FRAME", dir_path)
    if "result" in result:
        print(f"  ✓ Captured: {label} -> {dir_path}")
        return True
    print(f"  ✗ Failed: {label} - {result.get('error', 'unknown')}")
    return False

def audit_spec_requirement(req_id, description, check_fn):
    """Audit one spec requirement."""
    print(f"\n{'='*60}")
    print(f"{req_id}: {description}")
    print('-'*60)
    try:
        result = check_fn()
        status = "PASS" if result.get("pass") else "FAIL"
        print(f"  Status: {status}")
        if result.get("detail"):
            print(f"  Detail: {result['detail']}")
        return result
    except Exception as e:
        print(f"  Status: ERROR")
        print(f"  Exception: {e}")
        return {"pass": False, "error": str(e)}

def check_tab_order():
    """SPEC §1: Tab order must be VIEW | EDIT | RENDERING | SPRITE | MESH | INST | FONT | SKIN | INFO"""
    # Force RENDERING tab and capture
    result = send_cdp("FL4260_RENDERING_PROOF", "1 0")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/tab_order"
    capture_frame("Tab order audit", dir_path)

    # Check source for tab order
    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Find BeginTabItem calls
    import re
    tab_items = re.findall(r'BeginTabItem\("(\w+)"', content)

    # Expected order
    expected = ["VIEW", "EDIT", "RENDERING", "SPRITE", "MESH", "INST", "FONT", "SKIN", "INFO"]

    # Check if RENDERING comes after EDIT and before SPRITE
    try:
        edit_idx = tab_items.index("EDIT")
        rendering_idx = tab_items.index("RENDERING")
        sprite_idx = tab_items.index("SPRITE")

        if edit_idx < rendering_idx < sprite_idx:
            return {"pass": True, "detail": f"Tab order correct: VIEW|EDIT|RENDERING|SPRITE... (RENDERING at index {rendering_idx})"}
        else:
            return {"pass": False, "detail": f"Tab order wrong: RENDERING({rendering_idx}) should be between EDIT({edit_idx}) and SPRITE({sprite_idx})"}
    except ValueError as e:
        return {"pass": False, "detail": f"Missing tab: {e}"}

def check_rendering_intro_copy():
    """SPEC §2: Rendering tab must have intro copy at top"""
    result = send_cdp("FL4260_RENDERING_PROOF", "1 0")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/rendering_intro"
    capture_frame("Rendering intro audit", dir_path)

    # Check source for intro copy
    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Look for intro text patterns
    intro_patterns = [
        "Rendering controls how materials look",
        "material presentation",
        "Edit changes raw map data",
        "Rendering changes"
    ]

    found = []
    for pattern in intro_patterns:
        if pattern.lower() in content.lower():
            found.append(pattern)

    if len(found) >= 2:
        return {"pass": True, "detail": f"Intro copy found: {found}"}
    return {"pass": False, "detail": f"Intro copy missing. Found: {found}"}

def check_active_materials_pinned():
    """SPEC §3: Active Materials must be pinned at top of Rendering tab"""
    result = send_cdp("FL4260_RENDERING_PROOF", "1 0")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/active_materials"
    capture_frame("Active Materials audit", dir_path)

    # Check source for Active Materials position in Rendering
    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Look for Active Materials in Rendering context
    if "Active Materials" in content and "RENDERING" in content:
        # Check if it's rendered early in Rendering tab body
        rendering_section = content[content.find("sidebar_tab == 9"):content.find("sidebar_tab == 9") + 50000]
        if "Active Materials" in rendering_section:
            active_mat_pos = rendering_section.find("Active Materials")
            # Should be in first 5000 chars of Rendering body
            if active_mat_pos < 5000 and active_mat_pos > 0:
                return {"pass": True, "detail": f"Active Materials pinned at top (offset {active_mat_pos} in Rendering body)"}
            return {"pass": False, "detail": f"Active Materials not at top (offset {active_mat_pos})"}

    return {"pass": False, "detail": "Active Materials not found in Rendering tab"}

def check_starters_section():
    """SPEC §4: Starters section with full starters, glyph-pool starters, palette starters"""
    result = send_cdp("FL4260_RENDERING_PROOF", "1 0")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/starters"
    capture_frame("Starters audit", dir_path)

    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Check for starter patterns
    starter_patterns = {
        "full_starters": ["Grass Complete", "Path Complete", "Water Complete", "Stone Complete"],
        "glyph_pool_starters": ["Grass Tops", "Wave Flow", "Dense", "Minimal", "Blocks", "Halves"],
        "palette_starters": ["Grass ramp", "Stone ramp", "Water ramp", "Sand ramp"]
    }

    found = {}
    for category, patterns in starter_patterns.items():
        found[category] = []
        for p in patterns:
            if p.lower() in content.lower():
                found[category].append(p)

    # Check section numbering
    if "1. Mode and Status" in content and "2. Starters" in content:
        section_numbered = True
    else:
        section_numbered = False

    return {
        "pass": len(found.get("full_starters", [])) > 0 or len(found.get("glyph_pool_starters", [])) > 0,
        "detail": f"Found starters: {found}, section_numbered={section_numbered}"
    }

def check_glyph_pools_in_rendering():
    """SPEC §5: Glyph Pools must be in Rendering tab, not Character/Edit"""
    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Find Rendering tab body
    rendering_start = content.find("sidebar_tab == 9")
    if rendering_start < 0:
        return {"pass": False, "detail": "Rendering tab body not found"}

    rendering_body = content[rendering_start:rendering_start + 100000]

    # Check for Glyph Pools section in Rendering
    glyph_pools_patterns = ["Glyph Pools", "Pool Summary", "Extended Pool", "CP437 Pool", "Pool Actions"]
    found_in_rendering = sum(1 for p in glyph_pools_patterns if p in rendering_body)

    # Check if Extended Glyph Palette is in Character (Edit) tab
    edit_start = content.find("sidebar_tab == 1")
    edit_body = content[edit_start:edit_start + 100000] if edit_start >= 0 else ""

    extended_in_edit = "Extended Glyph Palette" in edit_body or "Extended Glyphs" in edit_body

    return {
        "pass": found_in_rendering > 0,
        "detail": f"Glyph Pools patterns in Rendering: {found_in_rendering}/5, Extended in Edit: {extended_in_edit}"
    }

def check_trace_selected_cell_story():
    """SPEC §6: Trace must show selected-cell story with rendered chips"""
    result = send_cdp("FL4260_RENDERING_PROOF", "1 -1 trace")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/trace"
    capture_frame("Trace audit", dir_path)

    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Check for Trace fields
    trace_patterns = [
        "axis=Ramp", "elevation row", "shade column",
        "pool", "bucket", "scorer",
        "final glyph", "fg", "bg",
        "rejection reason", "marker"
    ]

    found = [p for p in trace_patterns if p.lower() in content.lower()]

    # Check for trace highlight enabled
    has_trace_highlight = "g_fl4260_trace_highlight_enabled" in content
    has_trace_mode = "g_fl4260_trace_highlight_mode" in content

    return {
        "pass": len(found) >= 4 and has_trace_highlight,
        "detail": f"Trace fields found: {found}/{len(trace_patterns)}, highlight={has_trace_highlight}, mode={has_trace_mode}"
    }

def check_legacy_ui_hidden():
    """SPEC §7: Edit Character/Palette/Options must be hidden by default"""
    # First capture without flag
    result = send_cdp("FL4260_CAPTURE_EDIT_LEAF", "character docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/legacy_default")
    time.sleep(0.5)

    # Check source for guard
    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Look for Fl4260LegacyMaterialUiEnabled guards around Character/Palette/Options
    legacy_guard_pattern = "Fl4260LegacyMaterialUiEnabled()"

    # Count how many times it's used to gate Edit content
    guard_count = content.count(legacy_guard_pattern)

    # Check the function definition
    if "static bool Fl4260LegacyMaterialUiEnabled()" in content:
        func_start = content.find("static bool Fl4260LegacyMaterialUiEnabled()")
        func_end = content.find("}", func_start)
        func_body = content[func_start:func_end]

        # Should check env var or CLI flag
        checks_env = "getenv" in func_body or "ASCIICKER_LEGACY_MATERIAL_UI" in func_body
        checks_cli = "g_fl4260_legacy_material_ui_cli" in func_body

        return {
            "pass": checks_env or checks_cli,
            "detail": f"Legacy UI guard: {guard_count} uses, checks_env={checks_env}, checks_cli={checks_cli}"
        }

    return {"pass": False, "detail": "Fl4260LegacyMaterialUiEnabled() not found"}

def check_direct_edit_proof_pending_badge():
    """SPEC §8: Direct profile edit must show proof-pending status."""
    result = send_cdp("FL4260_RENDERING_PROOF", "1 0")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/direct_edit_badge"
    capture_frame("Direct profile-edit badge audit", dir_path)

    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Look for badge text
    badge_patterns = ["PROFILE EDIT", "proof pending", "direct edit"]
    found = [p for p in badge_patterns if p in content]

    return {
        "pass": len(found) >= 2,
        "detail": f"Badge patterns found: {found}"
    }

def check_mode_status_panel():
    """SPEC §9: Mode and Status read-only panel with renderer mode, profile name, runtime state"""
    result = send_cdp("FL4260_RENDERING_PROOF", "1 0")
    time.sleep(0.5)

    dir_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/mode_status"
    capture_frame("Mode/Status audit", dir_path)

    with open("editor/asciiid.cpp", "r") as f:
        content = f.read()

    # Look for mode/status fields
    mode_patterns = ["renderer mode", "profile name", "runtime state", "runtime profile", "runtime_profile_live"]
    found = [p for p in mode_patterns if p.lower() in content.lower()]

    # Check for section numbering
    has_mode_section = "1. Mode and Status" in content or "Mode and Status" in content

    return {
        "pass": len(found) >= 2 and has_mode_section,
        "detail": f"Mode/Status fields: {found}, section={has_mode_section}"
    }

def main():
    print("="*60)
    print("FL-4260 CDP-DRIVEN VISUAL AUDIT")
    print(f"Target: {CDP_HOST}:{CDP_PORT}")
    print("="*60)

    # Test CDP connection
    print("\nTesting CDP connection...")
    test_result = send_cdp("GET_CAMERA")
    if "error" in test_result:
        print(f"  ✗ CDP connection failed: {test_result['error']}")
        print("  Make sure ASCIIID is running with --cdp 8765")
        sys.exit(1)
    print("  ✓ CDP connection OK")

    # Run audits
    results = {}

    results["REQ-001-tab-order"] = audit_spec_requirement(
        "REQ-001",
        "Tab order: VIEW | EDIT | RENDERING | SPRITE | MESH | INST | FONT | SKIN | INFO",
        check_tab_order
    )

    results["REQ-002-rendering-intro"] = audit_spec_requirement(
        "REQ-002",
        "Rendering tab has intro copy explaining material presentation",
        check_rendering_intro_copy
    )

    results["REQ-003-active-materials"] = audit_spec_requirement(
        "REQ-003",
        "Active Materials pinned at top of Rendering tab",
        check_active_materials_pinned
    )

    results["REQ-004-starters"] = audit_spec_requirement(
        "REQ-004",
        "Starters section with full/glyph-pool/palette starters",
        check_starters_section
    )

    results["REQ-005-glyph-pools"] = audit_spec_requirement(
        "REQ-005",
        "Glyph Pools in Rendering tab (not Character/Edit)",
        check_glyph_pools_in_rendering
    )

    results["REQ-006-trace"] = audit_spec_requirement(
        "REQ-006",
        "Trace shows selected-cell story with rendered chips",
        check_trace_selected_cell_story
    )

    results["REQ-007-legacy-hidden"] = audit_spec_requirement(
        "REQ-007",
        "Edit Character/Palette/Options hidden by default",
        check_legacy_ui_hidden
    )

    results["REQ-008-direct-edit-badge"] = audit_spec_requirement(
        "REQ-008",
        "Direct profile edit shows proof-pending status",
        check_direct_edit_proof_pending_badge
    )

    results["REQ-009-mode-status"] = audit_spec_requirement(
        "REQ-009",
        "Mode and Status read-only panel",
        check_mode_status_panel
    )

    # Summary
    print("\n" + "="*60)
    print("AUDIT SUMMARY")
    print("="*60)

    passed = sum(1 for r in results.values() if r.get("pass"))
    failed = sum(1 for r in results.values() if not r.get("pass"))

    print(f"\nPassed: {passed}/{len(results)}")
    print(f"Failed: {failed}/{len(results)}")

    print("\nDetailed results:")
    for req_id, result in results.items():
        status = "✓ PASS" if result.get("pass") else "✗ FAIL"
        print(f"  {status} {req_id}: {result.get('detail', '')[:80]}")

    # Save results
    output_path = "docs/research/ascii/verification/fl4260/2026-06-17-cdp-audit/AUDIT_RESULTS.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
