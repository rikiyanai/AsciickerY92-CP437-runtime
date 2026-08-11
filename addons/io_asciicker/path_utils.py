"""
Path Utilities for the Asciicker Blender Addon
================================================

ARCHITECTURE:
    This module provides repository-root discovery so that other parts of the
    addon (and standalone scripts executed inside Blender) can locate sibling
    packages like ``scripts/`` without hard-coding absolute paths.

    Discovery follows a two-tier strategy:
      1. Walk up from a given *start_path* looking for the sentinel directories
         ``io_asciicker/`` and ``scripts/``.
      2. Fall back to well-known environment variables
         (``ASCIICKER_PATH``, ``ASCIICKER_ROOT``, ``ASCIICKER_REPO``).

KEY EXPORTS:
    - ``find_repo_root(start_path)``       -- Filesystem walk-up search.
    - ``find_repo_root_from_env()``        -- Environment variable lookup.
    - ``ensure_repo_root(start_path)``     -- Combined search + ``sys.path`` injection.
    - ``ensure_scripts_path(start_path)``  -- Convenience wrapper returning
      ``(repo_root, scripts_dir)`` tuple.

PIPELINE CONTEXT:
    [DEPENDENCY:BLENDER] Used at addon init time and by helper scripts that
    need access to ``scripts/asset_gen/`` or ``scripts/blender/`` from within
    the Blender Python environment.

    The repo root is identified by the co-existence of ``io_asciicker/`` (this
    addon) and ``scripts/`` (asset pipeline utilities).
"""

import os
import sys

# WHY these specific env vars: different CI / artist setups may define the
# repo root under any of these names; we check all three for portability.
# TODO(PIPELINE-FIX): Sentinel directories ("io_asciicker" + "scripts") are
# hard-coded here and in find_repo_root(); if the repo layout changes (e.g.
# scripts/ renamed to tools/) these will silently fail to locate the root.
ENV_ROOT_VARS = ["ASCIICKER_PATH", "ASCIICKER_ROOT", "ASCIICKER_REPO"]


def find_repo_root(start_path):
    """Walk up from *start_path* until both ``io_asciicker/`` and ``scripts/``
    directories are found in the same parent.

    Args:
        start_path: Absolute or relative path (file or directory) from which
            the upward search begins.

    Returns:
        The absolute path to the repository root, or an empty string if the
        filesystem root is reached without a match.
    """
    current = os.path.abspath(start_path)
    if os.path.isfile(current):
        current = os.path.dirname(current)

    while True:
        # WHY two sentinel dirs: a single ``io_asciicker/`` could appear inside
        # a virtualenv or site-packages; requiring both greatly reduces false
        # positives.
        if os.path.isdir(os.path.join(current, "io_asciicker")) and os.path.isdir(os.path.join(current, "scripts")):
            return current
        parent = os.path.dirname(current)
        # WHY: on Unix dirname("/") == "/", on Windows dirname("C:\\") == "C:\\";
        # this equality check is the portable way to detect filesystem root.
        if parent == current:
            return ""
        current = parent


def find_repo_root_from_env():
    """Check well-known environment variables for the repository root.

    Returns:
        The validated absolute path, or an empty string if no variable points
        to a directory containing the expected sentinel subdirectories.
    """
    for var in ENV_ROOT_VARS:
        value = os.environ.get(var, "")
        if not value:
            continue
        candidate = os.path.abspath(os.path.expanduser(value))
        if os.path.isdir(os.path.join(candidate, "io_asciicker")) and os.path.isdir(os.path.join(candidate, "scripts")):
            return candidate
    return ""


def ensure_repo_root(start_path):
    """Locate the repo root and add it to ``sys.path`` if not already present.

    This allows ``import scripts.asset_gen...`` to work inside Blender's
    embedded Python, which does not normally have the repo on its path.

    Args:
        start_path: Starting point for the filesystem walk-up search.

    Returns:
        The repo root path, or an empty string on failure.
    """
    repo_root = find_repo_root(start_path)
    if not repo_root:
        repo_root = find_repo_root_from_env()
    # TODO(PIPELINE-FIX): sys.path mutation is permanent for the Blender process
    # lifetime.  If the addon is disabled/re-enabled the entry is duplicated
    # (guarded by the ``not in`` check) but stale entries from a previous repo
    # checkout location will never be cleaned up.
    if repo_root and repo_root not in sys.path:
        sys.path.append(repo_root)
    return repo_root


def ensure_scripts_path(start_path):
    """Convenience wrapper: locate the repo root and derive ``scripts/``.

    Args:
        start_path: Starting point for the filesystem walk-up search.

    Returns:
        A ``(repo_root, scripts_dir)`` tuple.  Both values are empty strings
        if the repo root cannot be determined.
    """
    repo_root = ensure_repo_root(start_path)
    if not repo_root:
        return "", ""
    return repo_root, os.path.join(repo_root, "scripts")


def get_project_root():
    """Get project root from current .blend file location or fallback.

    Resolution order:
        1. Directory of the currently open .blend file, if it contains a
           ``assets/meshes/`` or ``assets/a3d/`` subdirectory (strong project-root signal).
        2. Walk up from the addon's ``__file__`` looking for a repo root
           marker (via ``find_repo_root``).
        3. Hard-coded fallback path (development convenience only).

    Returns:
        str: Absolute path to the project root directory.

    .. note::
        The hard-coded fallback is a developer convenience and should not be
        relied upon in production builds.
    """
    import bpy  # [DEPENDENCY:BLENDER] -- deferred import for non-Blender contexts

    # First try: use the .blend file's directory
    blend_path = bpy.data.filepath
    if blend_path:
        blend_dir = os.path.dirname(blend_path)
        if os.path.exists(os.path.join(blend_dir, "assets", "meshes")) or os.path.exists(os.path.join(blend_dir, "assets", "a3d")):
            return blend_dir

    # Fallback: resolve from addon location (follow symlinks to get real repo path)
    real_file = os.path.realpath(__file__)
    repo_root = find_repo_root(real_file)
    if repo_root:
        return repo_root

    raise RuntimeError(
        "Cannot resolve repo root: set the ASCIICKER_REPO environment variable "
        "to the absolute path of the asciicker-Y9-2 checkout, e.g. "
        "ASCIICKER_REPO=/home/yourname/asciicker-Y9-2"
    )


def normalize_mesh_name(name):
    """Strip ``.akm`` suffix and Blender duplicate suffixes (e.g. ``.001``).

    Args:
        name: Object name string from Blender.

    Returns:
        Cleaned name suitable for use as an AKM filename stem.
    """
    if name.lower().endswith(".akm"):
        name = name[:-4]
    if "." in name:
        base, suffix = name.rsplit(".", 1)
        if suffix.isdigit():
            return base
    return name
