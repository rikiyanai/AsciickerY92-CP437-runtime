"""Probe Blender, addon, and blosm launcher state."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

UNIFIED_ADDONS = ("io_asciicker", "blender_mcp_addon.py")
LEGACY_286_ADDONS = (
    "io_mesh_akm",
    "akm_curve_volumizer.py",
    "any_obj_vtex_color.py",
    "vertex_coloring_building.py",
)


@dataclass
class BlenderStatus:
    blender_path: str | None
    version: str | None
    addon_dir: Path | None
    addon_profile: str
    required_addons: tuple[str, ...]
    addons: dict[str, bool]
    legacy_addons: dict[str, bool]
    blosm_available: bool
    detail: str = ""


def _find_blender() -> str | None:
    env = Path.home()
    system = platform.system()

    candidates: list[Path] = []
    if system == "Darwin":
        candidates.extend([
            Path("/Applications/Blender.app/Contents/MacOS/Blender"),
            env / "Applications" / "Blender.app" / "Contents" / "MacOS" / "Blender",
        ])
    elif system == "Windows":
        candidates.extend([
            Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
            Path(r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe"),
            Path(r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"),
        ])
    else:
        candidates.extend([
            Path("/usr/bin/blender"),
            Path("/usr/local/bin/blender"),
            Path("/snap/bin/blender"),
        ])

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    found = shutil.which("blender")
    return found


def _addon_dir(version: str) -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Blender" / version / "scripts" / "addons"
    if system == "Windows":
        return home / "AppData" / "Roaming" / "Blender Foundation" / "Blender" / version / "scripts" / "addons"
    return home / ".config" / "blender" / version / "scripts" / "addons"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts)


def _addon_sources_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "addons"


def _profile_for_version(version: str) -> tuple[str, tuple[str, ...]]:
    vt = _version_tuple(version)
    if vt[:2] == (2, 86):
        return "legacy-2.86", LEGACY_286_ADDONS
    if vt and vt[0] >= 4:
        return "unified-4.x", UNIFIED_ADDONS
    return "unknown", UNIFIED_ADDONS


def _probe_addon_map(addon_dir: Path, addons: tuple[str, ...]) -> dict[str, bool]:
    return {name: (addon_dir / name).exists() for name in addons}


def _probe_legacy_repo_sources() -> dict[str, bool]:
    repo_addons = _addon_sources_dir()
    legacy_sources = {
        "io_mesh_akm": repo_addons / "io_mesh_akm",
        "akm_curve_volumizer.py": repo_addons / "blender_addons_4_5" / "akm_curve_volumizer.py",
        "any_obj_vtex_color.py": repo_addons / "blender_addons_4_5" / "any_obj_vtex_color.py",
        "vertex_coloring_building.py": repo_addons / "blender_addons_4_5" / "vertex_coloring_building.py",
    }
    return {name: path.exists() for name, path in legacy_sources.items()}


def probe() -> BlenderStatus:
    blender_path = _find_blender()
    if not blender_path:
        return BlenderStatus(
            None,
            None,
            None,
            "unknown",
            UNIFIED_ADDONS,
            {name: False for name in UNIFIED_ADDONS},
            _probe_legacy_repo_sources(),
            False,
            "Blender not found",
        )

    try:
        result = subprocess.run(
            [blender_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return BlenderStatus(
            blender_path,
            None,
            None,
            "unknown",
            UNIFIED_ADDONS,
            {name: False for name in UNIFIED_ADDONS},
            _probe_legacy_repo_sources(),
            False,
            str(exc),
        )

    version_match = re.search(r"(\d+\.\d+)", result.stdout)
    if result.returncode != 0 or not version_match:
        return BlenderStatus(
            blender_path,
            None,
            None,
            "unknown",
            UNIFIED_ADDONS,
            {name: False for name in UNIFIED_ADDONS},
            _probe_legacy_repo_sources(),
            False,
            "Unable to read Blender version",
        )

    version = version_match.group(1)
    addon_dir = _addon_dir(version)
    addon_profile, required_addons = _profile_for_version(version)
    addons = _probe_addon_map(addon_dir, required_addons)
    legacy_addons = _probe_addon_map(addon_dir, LEGACY_286_ADDONS)
    blosm_available = any(path.name.lower().startswith("blosm") for path in addon_dir.glob("*")) if addon_dir.exists() else False

    return BlenderStatus(
        blender_path=blender_path,
        version=version,
        addon_dir=addon_dir,
        addon_profile=addon_profile,
        required_addons=required_addons,
        addons=addons,
        legacy_addons=legacy_addons,
        blosm_available=blosm_available,
        detail="" if addon_dir.exists() else "addon directory not created yet",
    )
