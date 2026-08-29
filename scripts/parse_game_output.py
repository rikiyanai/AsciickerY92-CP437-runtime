#!/usr/bin/env python3
"""
Game Output Parser
Automatically parse game debug output and diagnose terrain issues
"""
import re
import sys

def parse_game_output(output_text):
    """Parse game debug output and diagnose terrain issues"""

    issues = []
    info = {}

    # Extract key values
    patches_match = re.search(r'Patches loaded: (\d+)', output_text)
    if patches_match:
        info['patches'] = int(patches_match.group(1))
        if info['patches'] == 0:
            issues.append("CRITICAL: Zero patches loaded!")

    water_match = re.search(r'Water level: (\d+)', output_text)
    if water_match:
        info['water_level'] = int(water_match.group(1))

    spawn_patch_match = re.search(r'Spawn patch found', output_text)
    if not spawn_patch_match:
        no_patch_match = re.search(r'NO PATCH AT SPAWN', output_text)
        if no_patch_match:
            issues.append("CRITICAL: No terrain patch at spawn point (0, 15)")

    visual_match = re.search(r'Visual center: 0x([0-9A-Fa-f]+) \(material (\d+)\)', output_text)
    if visual_match:
        info['spawn_visual'] = int(visual_match.group(1), 16)
        info['spawn_material'] = int(visual_match.group(2))
        if info['spawn_material'] == 0:
            issues.append("WARNING: Spawn material is 0 (water)")

    height_match = re.search(r'Height center: (\d+)', output_text)
    if height_match:
        info['spawn_height'] = int(height_match.group(1))
        if 'water_level' in info:
            if info['spawn_height'] < info['water_level']:
                issues.append(f"CRITICAL: Spawn height ({info['spawn_height']}) below water ({info['water_level']})")

    # Check for freeze indicators
    if 'AddTerrainPatch' in output_text:
        add_count = output_text.count('AddTerrainPatch')
        info['add_patch_calls'] = add_count
        if add_count > 10000:
            issues.append(f"CRITICAL: AddTerrainPatch called {add_count} times - possible infinite loop!")

    # Check for loading progress
    loading_match = re.search(r'Loading patch (\d+)/(\d+)', output_text)
    if loading_match:
        info['last_patch_loaded'] = int(loading_match.group(1))
        info['total_patches'] = int(loading_match.group(2))

    # Print diagnosis
    print("=== GAME STATE DIAGNOSIS ===")
    print("\nLoaded values:")
    for key, val in info.items():
        print(f"  {key}: {val}")

    print("\nIssues detected:")
    if not issues:
        print("  None detected from output")
    else:
        for issue in issues:
            print(f"  • {issue}")

    # Check if output suggests freeze
    if not info:
        print("\n  ⚠ No debug info found - game may not have debug output enabled")
        print("    Add printf statements to terrain.cpp LoadTerrain() to trace loading")

    return info, issues

if __name__ == '__main__':
    # Read from file or stdin
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            output = f.read()
    else:
        print("Usage: python3 scripts/parse_game_output.py <game_output.log>")
        print("   or: ./.run/game 2>&1 | python3 scripts/parse_game_output.py")
        print("\nReading from stdin...")
        output = sys.stdin.read()

    parse_game_output(output)
