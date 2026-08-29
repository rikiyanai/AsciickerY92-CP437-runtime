#!/usr/bin/env python3
import argparse
import importlib.util
import os
import sys
import types
from pathlib import Path

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
addons_root = os.path.join(repo_root, "addons")

for extra in (repo_root, addons_root):
    if extra not in sys.path:
        sys.path.insert(0, extra)

def load_module(name, rel_path):
    module_path = os.path.join(repo_root, rel_path)
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

a3d_format = load_module("a3d_format", os.path.join("addons", "io_asciicker", "scene", "a3d_format.py"))
path_utils = load_module("io_asciicker.path_utils", os.path.join("addons", "io_asciicker", "path_utils.py"))
io_asciicker_pkg = types.ModuleType("io_asciicker")
io_asciicker_pkg.path_utils = path_utils
sys.modules.setdefault("io_asciicker", io_asciicker_pkg)
sys.modules["io_asciicker.path_utils"] = path_utils
default_materials = load_module("default_materials", os.path.join("addons", "io_asciicker", "scene", "default_materials.py"))

A3DHeader = a3d_format.A3DHeader
A3DPatch = a3d_format.A3DPatch
BASE_TERRAIN_HEIGHT = a3d_format.BASE_TERRAIN_HEIGHT
get_default_materials_binary = default_materials.get_default_materials_binary

def build_patches(grid, start_x, start_y, height_value, material_id):
    patches = []
    for py in range(start_y, start_y + grid):
        for px in range(start_x, start_x + grid):
            patch = A3DPatch(px, py)
            for hy in range(len(patch.height)):
                for hx in range(len(patch.height[hy])):
                    patch.height[hy][hx] = height_value
            for vy in range(len(patch.visual)):
                for vx in range(len(patch.visual[vy])):
                    patch.visual[vy][vx] = material_id
            patches.append(patch)
    return patches

def write_a3d(output_path, patches):
    materials_binary = get_default_materials_binary()
    with open(output_path, "wb") as handle:
        header = A3DHeader(len(patches))
        header.write(handle)
        for patch in patches:
            patch.write(handle)
        handle.write(materials_binary)
        handle.write(int(-1).to_bytes(4, byteorder="little", signed=True))  # format_version
        handle.write(int(0).to_bytes(4, byteorder="little", signed=True))   # instance_count
        handle.write(int(0).to_bytes(4, byteorder="little", signed=True))   # enemy_count


def _print_start_card(args, output_path):
    print("=== Minimal A3D Map Generator ===")
    print(f"  target:      {output_path}")
    print(f"  grid:        {args.grid}x{args.grid} patch(es)")
    print(f"  start:       ({args.start_x}, {args.start_y})")
    print(f"  height:      {args.height_value}")
    print(f"  material:    {args.material_id}")
    print(f"  overwrite:   {'yes (--force)' if args.force else 'no'}")
    print("  mutates:     writes the target A3D file")
    print()


def _print_final_summary(*, result, output_path, patch_count=0, next_action):
    print()
    print("=== Final Summary ===")
    print(f"  result:      {result}")
    print(f"  output:      {output_path}")
    if patch_count:
        print(f"  patches:     {patch_count}")
    print(f"  next action: {next_action}")


def main():
    parser = argparse.ArgumentParser(description="Generate a minimal deterministic A3D map.")
    parser.add_argument("--grid", type=int, default=1, help="Square grid size in patches.")
    parser.add_argument("--start-x", type=int, default=0, help="Patch start X coordinate.")
    parser.add_argument("--start-y", type=int, default=0, help="Patch start Y coordinate.")
    parser.add_argument("--height-value", type=int, default=BASE_TERRAIN_HEIGHT,
                        help="Constant height value for all patch vertices.")
    parser.add_argument("--material-id", type=int, default=1, help="Material ID for all cells.")
    parser.add_argument("--out", default="", help="Output file path.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    args = parser.parse_args()

    output_path = args.out
    if not output_path:
        output_path = os.path.join("assets", "a3d", f"minimal_{args.grid}x{args.grid}.a3d")
    output_path = Path(output_path).expanduser()

    _print_start_card(args, output_path)

    if args.grid < 1:
        print("ERROR: --grid must be >= 1", file=sys.stderr)
        _print_final_summary(
            result="FAILED",
            output_path=output_path,
            next_action="Rerun with --grid 1 or larger.",
        )
        return 1

    if output_path.exists() and not args.force:
        print(f"ERROR: refusing to overwrite existing output: {output_path}", file=sys.stderr)
        _print_final_summary(
            result="FAILED",
            output_path=output_path,
            next_action="Choose a new --out path or rerun with --force to overwrite deliberately.",
        )
        return 1

    patches = build_patches(args.grid, args.start_x, args.start_y, args.height_value, args.material_id)
    output_dir = output_path.parent
    if str(output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
    try:
        write_a3d(output_path, patches)
    except OSError as exc:
        print(f"ERROR: could not write {output_path}: {exc}", file=sys.stderr)
        _print_final_summary(
            result="FAILED",
            output_path=output_path,
            next_action="Check the output path and filesystem permissions, then rerun.",
        )
        return 1
    _print_final_summary(
        result="OK",
        output_path=output_path,
        patch_count=len(patches),
        next_action="Open the map in ASCIIID or select it from the launcher map browser.",
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
