#!/usr/bin/env python3
"""
Asciicker Addon Setup Script (Developer Mode)
---------------------------------------------
Links the local Blender addon sources to the Blender addons folder.
This allows you to edit code and see changes in Blender immediately
(after reloading scripts) without re-installing.
"""

import os
import sys
import platform
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
UNIFIED_ADDON_SOURCES = {
    "io_asciicker": REPO_ROOT / "addons" / "io_asciicker",
    "blender_mcp_addon.py": REPO_ROOT / "addons" / "blender_mcp_addon.py",
}
LEGACY_286_ADDON_SOURCES = {
    "io_mesh_akm": REPO_ROOT / "addons" / "io_mesh_akm",
    "akm_curve_volumizer.py": REPO_ROOT / "addons" / "blender_addons_4_5" / "akm_curve_volumizer.py",
    "any_obj_vtex_color.py": REPO_ROOT / "addons" / "blender_addons_4_5" / "any_obj_vtex_color.py",
    "vertex_coloring_building.py": REPO_ROOT / "addons" / "blender_addons_4_5" / "vertex_coloring_building.py",
}

def get_blender_addon_paths():
    """Return list of potential Blender addon paths based on OS"""
    system = platform.system()
    home = Path.home()
    paths = []

    if system == "Darwin":  # macOS
        # Check specific versions or wildcards? 
        # For now, let's look for standard locations
        base = home / "Library/Application Support/Blender"
        if base.exists():
            for version_dir in base.iterdir():
                if version_dir.is_dir() and version_dir.name[0].isdigit():
                    paths.append(version_dir / "scripts/addons")
    
    elif system == "Linux":
        base = home / ".config/blender"
        if base.exists():
            for version_dir in base.iterdir():
                if version_dir.is_dir() and version_dir.name[0].isdigit():
                    paths.append(version_dir / "scripts/addons")
    
    elif system == "Windows":
        base = Path(os.environ["APPDATA"]) / "Blender Foundation/Blender"
        if base.exists():
            for version_dir in base.iterdir():
                if version_dir.is_dir() and version_dir.name[0].isdigit():
                    paths.append(version_dir / "scripts/addons")

    return sorted(paths, reverse=True) # Newest versions first


def addon_sources_for_version(version: str):
    if version.startswith("2.86"):
        return "legacy-2.86", LEGACY_286_ADDON_SOURCES
    return "unified-4.x", UNIFIED_ADDON_SOURCES

def main():
    print("--- Setting up Blender addons in Developer Mode ---")

    missing_sources = [
        str(path)
        for source_map in (UNIFIED_ADDON_SOURCES, LEGACY_286_ADDON_SOURCES)
        for path in source_map.values()
        if not path.exists()
    ]
    if missing_sources:
        print("Error: Source path(s) not found:")
        for path in sorted(set(missing_sources)):
            print(f"  {path}")
        sys.exit(1)

    targets = get_blender_addon_paths()
    
    if not targets:
        print("Error: Could not find any Blender installation directories.")
        print("expected path examples:")
        print("  macOS: ~/Library/Application Support/Blender/4.5/scripts/addons")
        sys.exit(1)

    print(f"Found {len(targets)} Blender versions.")
    
    success_count = 0
    
    for addons_dir in targets:
        # Create 'scripts/addons' if they don't exist (unlikely for valid installs but good practice)
        if not addons_dir.exists():
            try:
                addons_dir.mkdir(parents=True)
            except Exception as e:
                print(f"  [Skip] Could not create {addons_dir}: {e}")
                continue

        version = addons_dir.parent.parent.name
        profile, addon_sources = addon_sources_for_version(version)
        print(f"\nTarget: {version} ({addons_dir})")
        print(f"  profile: {profile}")

        for addon_name, source_path in addon_sources.items():
            target_link = addons_dir / addon_name

            if target_link.is_symlink():
                resolved = target_link.resolve()
                if resolved == source_path:
                    print(f"  {target_link.name} -> ALREADY LINKED correctly.")
                    success_count += 1
                    continue
                print(f"  {target_link.name}: remapped symlink from {resolved}")
                target_link.unlink()
            elif target_link.exists():
                print(f"  {target_link.name}: removing existing directory/file copy...")
                if target_link.is_dir():
                    shutil.rmtree(target_link)
                else:
                    target_link.unlink()

            try:
                target_link.symlink_to(source_path)
                print(f"  {target_link.name} -> LINKED to {source_path}")
                success_count += 1
            except Exception as e:
                print(f"  {target_link.name} -> FAILED to link: {e}")

    if success_count > 0:
        print(f"\nSUCCESS: Linked in {success_count} location(s).")
        print("Restart Blender or run 'bpy.ops.script.reload()' to see changes.")
    else:
        print("\nWARNING: No links were created.")

if __name__ == "__main__":
    main()
