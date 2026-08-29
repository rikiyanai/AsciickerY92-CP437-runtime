#!/usr/bin/env python3
"""
Centralized Blender binary detection for the Asciicker project.

All scripts that need to invoke Blender headless should use this module
instead of rolling their own PATH / hardcoded-path detection.

Resolution order:
  1. BLENDER_BIN environment variable (highest priority)
  2. Common macOS application bundle paths
  3. Common Linux system paths
  4. ``blender`` on PATH via shutil.which()
"""

import os
import shutil
from pathlib import Path

# Candidate paths checked in order after BLENDER_BIN.
# macOS bundles first (most common dev environment), then Linux system paths.
_CANDIDATE_PATHS = [
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/Applications/Blender 4.5.app/Contents/MacOS/Blender",
    "/Applications/Blender4.5.app/Contents/MacOS/Blender",
    "/Applications/Blender4.4.app/Contents/MacOS/Blender",
    "/usr/bin/blender",
    "/usr/local/bin/blender",
]


def get_blender_bin():
    """Return a Blender executable path, or None if not found.

    Checks BLENDER_BIN env var first, then well-known system paths,
    then falls back to PATH lookup via shutil.which().
    """
    env_bin = os.environ.get("BLENDER_BIN")
    candidates = [env_bin] + _CANDIDATE_PATHS + ["blender"]

    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if os.path.exists(candidate):
            return candidate

    return None


def get_blender_pythonpath_entries(repo_root):
    """Return import roots Blender should receive for local project runs."""
    root = Path(repo_root).resolve()
    candidates = [root / "addons", root]

    for venv_name in (".venv", "mcp_venv"):
        lib_dir = root / venv_name / "lib"
        if not lib_dir.is_dir():
            continue
        candidates.extend(sorted(lib_dir.glob("python*/site-packages")))

    entries = []
    seen = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen or not os.path.exists(candidate_str):
            continue
        seen.add(candidate_str)
        entries.append(candidate_str)
    return entries
