#!/usr/bin/env python3
"""
Audit a semantic map JSON against actual sprite layer data.
Reports: missing assignments, wrong assignments, unclassified cells.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

def load_semantic_map(map_path):
    with open(map_path) as f:
        return json.load(f)

def dump_layer(sprite_dir, sprite, layer, anim, frame, angle):
    cmd = [
        "python3", "scripts/pipeline/xp_raw_layer_inspector.py",
        "--sprite-dir", sprite_dir,
        "--sprite", sprite,
        "--layer", str(layer),
        "--anim", str(anim),
        "--frame", str(frame),
        "--angle", str(angle),
        "--json"
    ]
    REPO_ROOT = os.environ.get("ASCIICKER_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"ERROR running command: {result.stderr}", file=sys.stderr)
        return None
    return json.loads(result.stdout)

def audit_angle(map_data, dump_data, angle):
    """Compare semantic map assignments against dump data for one angle."""
    frame_key = str(angle)
    if frame_key not in map_data.get("frames", {}):
        print(f"  ⚠️  No frame data for angle {angle} in map")
        return
    
    frame_map = map_data["frames"][frame_key]
    regions = frame_map.get("regions", [])
    
    # Build a lookup: (x, y) -> region assignment
    map_assignments = {}
    for region in regions:
        region_name = region.get("name", "unknown")
        for cell in region.get("semantic_cells", []):
            key = (cell["x"], cell["y"])
            map_assignments[key] = {
                "region": region_name,
                "role": cell.get("role", "unknown"),
                "fg": cell.get("fg"),
                "bg": cell.get("bg"),
                "glyph": cell.get("glyph")
            }
    
    # Get actual cells from dump
    cells = dump_data.get("cells", [])
    non_transparent = [c for c in cells if c.get("engine_visible", False)]
    
    print(f"  Angle {angle}: {len(non_transparent)} visible cells, {len(map_assignments)} assigned in map")
    
    issues = []
    
    # Check 1: Cells assigned in map but not visible in sprite
    for (x, y), assignment in map_assignments.items():
        matching = [c for c in non_transparent if c["local_col"] == x and c["local_row"] == y]
        if not matching:
            issues.append(f"  ❌ ASSIGNED but NOT VISIBLE: ({x},{y}) → {assignment['region']} ({assignment['role']})")
        else:
            cell = matching[0]
            # Check color match
            expected_fg = assignment["fg"]
            expected_bg = assignment["bg"]
            actual_fg = cell.get("fg_hex") or "#" + "".join(f"{v:02x}" for v in cell.get("fg_rgb", [0,0,0]))
            actual_bg = cell.get("bg_hex") or "#" + "".join(f"{v:02x}" for v in cell.get("bg_rgb", [0,0,0]))
            if expected_fg and actual_fg and expected_fg != actual_fg:
                issues.append(f"  ⚠️  COLOR MISMATCH at ({x},{y}): map fg={expected_fg}, actual fg={actual_fg}")
            if expected_bg and actual_bg and expected_bg != actual_bg:
                issues.append(f"  ⚠️  COLOR MISMATCH at ({x},{y}): map bg={expected_bg}, actual bg={actual_bg}")
    
    # Check 2: Visible cells NOT assigned in map
    for cell in non_transparent:
        key = (cell["local_col"], cell["local_row"])
        if key not in map_assignments:
            actual_fg = cell.get("fg_hex") or "#" + "".join(f"{v:02x}" for v in cell.get("fg_rgb", [0,0,0]))
            actual_bg = cell.get("bg_hex") or "#" + "".join(f"{v:02x}" for v in cell.get("bg_rgb", [0,0,0]))
            issues.append(f"  ❓ VISIBLE but NOT ASSIGNED: ({cell['local_col']},{cell['local_row']}) fg={actual_fg} bg={actual_bg} glyph={cell['glyph_id']}")
    
    if issues:
        for issue in issues[:20]:  # Limit output
            print(issue)
        if len(issues) > 20:
            print(f"  ... and {len(issues) - 20} more issues")
    else:
        print(f"  ✅ All {len(map_assignments)} assignments match visible cells")
    
    return len(issues)

def main():
    if len(sys.argv) < 2:
        print("Usage: audit_semantic_map.py <map_json>")
        sys.exit(1)
    
    map_path = Path(sys.argv[1])
    map_data = load_semantic_map(map_path)
    
    sprite_dir = "assets/sprites"
    sprite = map_data["reference_xp"].split("/")[-1]
    semantic_layer = map_data.get("semantic_layer", 2)
    
    print(f"\n🔍 Auditing {map_path.name}")
    print(f"   Sprite: {sprite}")
    print(f"   Semantic layer: {semantic_layer}")
    print(f"   Frame size: {map_data['frame_w']}x{map_data['frame_h']}")
    print()
    
    total_issues = 0
    angles = range(8)  # Always check all 8 angles
    
    for angle in angles:
        dump_data = dump_layer(sprite_dir, sprite, semantic_layer, 0, 0, angle)
        if dump_data:
            issues = audit_angle(map_data, dump_data, angle)
            total_issues += (issues or 0)
        print()
    
    print(f"\n📊 Summary: {total_issues} issues found")
    return 0 if total_issues == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
