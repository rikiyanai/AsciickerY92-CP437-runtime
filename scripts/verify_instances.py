#!/usr/bin/env python3
import argparse
import os
import sys

scripts_dir = os.path.dirname(os.path.abspath(__file__))
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
repo_root = os.path.abspath(os.path.join(scripts_dir, ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from cli_style import status as cli_status  # noqa: E402

from asciicker_constants import blender_to_game_z
from validate_a3d import read_a3d

DEFAULT_EXPECTATIONS = {
    "PassMesh": (10.0, 0.0, blender_to_game_z(1.0)),
    "SolidMesh": (0.0, 10.0, blender_to_game_z(1.0)),
}

def find_instance(instances, name):
    for inst in instances:
        if inst["inst_name"] == name or inst["mesh_name"].startswith(name):
            return inst
    return None

def main():
    parser = argparse.ArgumentParser(description="Verify instance transforms in an A3D file.")
    parser.add_argument("path", help="Path to A3D file.")
    parser.add_argument("--tolerance", type=float, default=0.1, help="Allowed positional tolerance.")
    parser.add_argument("--mode", choices=["blender_build_scene"], default="blender_build_scene",
                        help="Expectation preset.")
    args = parser.parse_args()

    info = read_a3d(args.path, include_instances=True)
    instances = info.get("instances") or []

    if args.mode == "blender_build_scene":
        expectations = DEFAULT_EXPECTATIONS
    else:
        expectations = {}

    missing = []
    failed = []

    for name, (exp_x, exp_y, exp_z) in expectations.items():
        inst = find_instance(instances, name)
        if not inst:
            missing.append(name)
            continue

        tx, ty, tz = inst["transform"][12], inst["transform"][13], inst["transform"][14]
        if (abs(tx - exp_x) > args.tolerance or
                abs(ty - exp_y) > args.tolerance or
                abs(tz - exp_z) > args.tolerance):
            failed.append((name, (tx, ty, tz), (exp_x, exp_y, exp_z)))

    if missing:
        print(cli_status("FAIL", f"Missing instances: {', '.join(missing)}"))
        sys.exit(1)

    if failed:
        for name, got, expected in failed:
            print(cli_status("FAIL", f"{name} pos={got} expected={expected}"))
        sys.exit(1)

    print(cli_status("PASS", "Instance transforms match expectations"))
    sys.exit(0)

if __name__ == "__main__":
    main()
