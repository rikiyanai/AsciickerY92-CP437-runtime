#!/usr/bin/env python3
"""MCP session: list instances, delete all test-marker.akm, report remaining positions."""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from asciiid_direct import AsciiidDirect

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAP_PATH = "assets/a3d/copy_game_map_y8.a3d"


def parse_instances(lines):
    """Parse LIST_INSTANCES output into list of (idx, name, x, y, z)."""
    instances = []
    for line in lines:
        m = re.search(r'ID:\s*\S+\s+Name:\s*(\S+)\s+Pos:\s*([\-\d.]+),([\-\d.]+),([\-\d.]+)', line)
        if m:
            name, x, y, z = m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))
            instances.append((len(instances), name, x, y, z))
    return instances


def main():
    print(f"Starting asciiid --mcp ...")
    ed = AsciiidDirect()

    print("Waiting for ready...")
    if not ed.wait_ready(timeout=30):
        print("ERROR: asciiid did not respond in 30s")
        ed.proc.kill()
        sys.exit(1)
    print("Ready.")

    print(f"\nLoading {MAP_PATH} ...")
    lines = ed.send(f"LOAD_MAP {MAP_PATH}")
    for l in lines:
        print(" ", l)

    if ed.proc.poll() is not None:
        print(f"ERROR: asciiid exited after LOAD_MAP")
        sys.exit(1)

    # List all instances
    print("\nListing instances...")
    lines = ed.send("LIST_INSTANCES")
    instances = parse_instances(lines)
    for i, name, x, y, z in instances:
        marker = " <-- TEST MARKER" if "test" in name.lower() and "marker" in name.lower() else ""
        print(f"  [{i:2d}] {name:40s}  {x:8.2f} {y:8.2f} {z:8.2f}{marker}")

    # Find test-marker instances (any name containing test and marker, case-insensitive)
    to_delete = [(i, name, x, y, z) for i, name, x, y, z in instances
                 if "test" in name.lower() or "marker" in name.lower()]
    print(f"\nFound {len(to_delete)} test/marker instance(s) to delete.")

    # Delete in reverse index order so indices stay valid
    for i, name, x, y, z in reversed(to_delete):
        print(f"  Deleting [{i}] {name} at {x:.2f},{y:.2f},{z:.2f} ...")
        result = ed.send(f"DELETE_INSTANCE {i}")
        for l in result:
            print("   ", l)

    if to_delete:
        print(f"\nSaving map to {MAP_PATH} ...")
        result = ed.send(f"SAVE_MAP {MAP_PATH}")
        for l in result:
            print(" ", l)

    # Final instance list
    print("\nFinal instance list:")
    lines = ed.send("LIST_INSTANCES")
    final = parse_instances(lines)
    for i, name, x, y, z in final:
        print(f"  [{i:2d}] {name:40s}  {x:8.2f} {y:8.2f} {z:8.2f}")

    ed.quit()
    print("\nDone.")


if __name__ == "__main__":
    main()
