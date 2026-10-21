#!/usr/bin/env python3
"""
Asciicker Addon Setup Script (Developer Mode)
---------------------------------------------
Links the local Blender addon sources to the Blender addons folder.
This allows you to edit code and see changes in Blender immediately
(after reloading scripts) without re-installing.  On Windows, the
no-privilege file-hardlink fallback exposes in-place edits but requires this
script to be rerun after an editor replaces a file atomically.
"""

import os
import subprocess
import sys
import platform
import shutil
from pathlib import Path

from launcher_lib import tool_versions

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

_WINDOWS_MKLINK_TARGET_ENV = "__ASCIICKER_MKLINK_TARGET"
_WINDOWS_MKLINK_SOURCE_ENV = "__ASCIICKER_MKLINK_SOURCE"


def _run_windows_mklink(
    target_link: Path,
    source_path: Path,
    options: list[str],
    kind: str,
) -> bool:
    """Run one live-link strategy without reparsing user paths as shell code.

    ``mklink`` is a ``cmd.exe`` builtin, so passing a raw path as an argument
    still lets metacharacters such as ``&`` become command syntax.  Put the
    paths in the child environment and expand quoted variable references;
    percent expansion is a single pass and the values are inserted inside
    quotes, while ``/v:off`` keeps ``!`` literal.  The paths therefore remain
    data even when a valid Windows filename contains shell metacharacters,
    ``%``, or ``!``.
    """
    child_env = os.environ.copy()
    child_env[_WINDOWS_MKLINK_TARGET_ENV] = str(target_link)
    child_env[_WINDOWS_MKLINK_SOURCE_ENV] = str(source_path)
    command = " ".join(
        [
            "mklink",
            *options,
            f'"%{_WINDOWS_MKLINK_TARGET_ENV}%"',
            f'"%{_WINDOWS_MKLINK_SOURCE_ENV}%"',
        ]
    )
    # FL-4908: cmd uses shell quoting, not the CRT argv quoting that Python
    # applies to a sequence. Keep the trusted command text intact; paths live
    # only in the child environment, never in this command line.
    args = "cmd /d /v:off /c " + command
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
    except OSError as exc:
        print(f"  {target_link.name} -> {kind} failed: {exc}")
        return False
    if proc.returncode == 0:
        print(f"  {target_link.name} -> {kind} to {source_path}")
        if kind == "HARDLINK":
            print(
                "  WARNING: file hardlink shares the current file identity; "
                "atomic source replacement requires rerunning setup_addon.py."
            )
        return True
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    print(
        f"  {target_link.name} -> {kind} failed: "
        f"{detail[0] if detail else 'unknown error'}"
    )
    return False


def _windows_link_fallback(target_link: Path, source_path: Path) -> bool:
    """Create a Windows link without silently replacing it with a copy.

    Directory junctions preserve the live directory view.  A file hardlink is
    the no-privilege fallback when a symbolic link is unavailable: in-place
    writes are visible, but editors that replace a file atomically require the
    setup command to be rerun.  Junctions and hardlinks have filesystem
    limits, so a failed primary strategy gets one symbolic-link retry.  If
    every live-link strategy fails, return ``False`` and leave the source
    unlinked.
    """
    if source_path.is_dir():
        if _run_windows_mklink(target_link, source_path, ["/J"], "JUNCTION"):
            return True
        return _run_windows_mklink(target_link, source_path, ["/D"], "SYMLINK")

    if _run_windows_mklink(target_link, source_path, ["/H"], "HARDLINK"):
        return True
    # Hardlinks cannot cross volumes. A symbolic link is the only remaining
    # built-in file link that preserves live edits; it may require Developer
    # Mode or elevation, so failure remains visible instead of copying.
    return _run_windows_mklink(target_link, source_path, [], "SYMLINK")


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
    return "unified-modern", UNIFIED_ADDON_SOURCES

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
        series = tool_versions.tool("blender")["accepted_series"]
        print(f"  macOS: ~/Library/Application Support/Blender/{series}/scripts/addons")
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
            except OSError as e:
                if os.name == "nt":
                    if _windows_link_fallback(target_link, source_path):
                        success_count += 1
                        continue
                    print(f"  {target_link.name} -> no live link available: {e}")
                else:
                    print(f"  {target_link.name} -> FAILED to link: {e}")
            except Exception as e:
                print(f"  {target_link.name} -> FAILED to link: {e}")

    if success_count > 0:
        print(f"\nSUCCESS: Linked in {success_count} location(s).")
        print("Restart Blender or run 'bpy.ops.script.reload()' to see changes.")
    else:
        print("\nWARNING: No links were created.")

if __name__ == "__main__":
    main()
