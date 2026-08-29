#!/usr/bin/env python3
"""
Preflight validation for Blender render invocations.

Provides fail-fast validation of render prerequisites with clear error messages.
Used by generator.py before spawning Blender subprocess to catch issues early.

Functions:
    check_blender_binary() -> str
        Validates Blender executable exists and is accessible.
        Returns path if found, raises FileNotFoundError with install instructions.

    check_blend_file(path: str) -> Path
        Validates .blend file exists and is readable.
        Returns resolved Path, raises FileNotFoundError if missing.

    check_output_dir(path: str, create: bool = True) -> Path
        Validates output directory exists and is writable.
        Creates directory if create=True, raises PermissionError if not writable.

    check_object_exists(blender_bin: str, blend_path: str, object_name: str) -> bool
        Probes Blender to verify object exists in scene.
        Returns True if found, raises ValueError with available objects list.

    run_preflight(blend_path: str, output_dir: str, object_name: str) -> dict
        Runs all preflight checks in sequence.
        Returns dict with validated paths on success, lets exceptions propagate.

Example usage:
    >>> from scripts.blender.blender_preflight import run_preflight
    >>> result = run_preflight(
    ...     blend_path="scene.blend",
    ...     output_dir="output/",
    ...     object_name="Cube"
    ... )
    >>> print(result["blender_path"])
    /Applications/Blender.app/Contents/MacOS/Blender

Error messages:
    - Missing Blender: "Blender not found. Set BLENDER_BIN environment variable or install Blender to /Applications/Blender.app"
    - Invalid blend path: "Blend file not found: /path/to/missing.blend"
    - Non-existent object: "Object 'BadName' not found in scene.blend. Available: ['Cube', 'Camera', 'Light']"
"""

import os
import subprocess
from pathlib import Path
from scripts.blender_utils import get_blender_bin


def check_blender_binary() -> str:
    """Check if Blender executable exists.

    Uses get_blender_bin() from scripts.blender_utils to reuse existing
    binary discovery logic (BLENDER_BIN env var, common install paths).

    Returns:
        str: Path to Blender executable

    Raises:
        FileNotFoundError: If Blender not found, with install instructions

    Example:
        >>> path = check_blender_binary()
        >>> print(path)
        /Applications/Blender.app/Contents/MacOS/Blender
    """
    blender_path = get_blender_bin()

    if blender_path is None:
        raise FileNotFoundError(
            "Blender not found. Set BLENDER_BIN environment variable or "
            "install Blender to /Applications/Blender.app"
        )

    return blender_path


def check_blend_file(path: str) -> Path:
    """Validate .blend file exists and is readable.

    Args:
        path: Path to .blend file (string or Path-like)

    Returns:
        Path: Resolved absolute path to blend file

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file doesn't have .blend extension

    Example:
        >>> blend_path = check_blend_file("scene.blend")
        >>> print(blend_path)
        /Users/r/project/scene.blend
    """
    blend_path = Path(path).resolve()

    if not blend_path.exists():
        raise FileNotFoundError(f"Blend file not found: {blend_path}")

    if not blend_path.is_file():
        raise FileNotFoundError(f"Blend path is not a file: {blend_path}")

    if blend_path.suffix != ".blend":
        raise ValueError(f"File is not a .blend file: {blend_path}")

    return blend_path


def check_output_dir(path: str, create: bool = True) -> Path:
    """Validate output directory exists and is writable.

    Args:
        path: Path to output directory
        create: If True, create directory if it doesn't exist (default: True)

    Returns:
        Path: Resolved absolute path to output directory

    Raises:
        PermissionError: If directory is not writable
        OSError: If directory creation fails

    Example:
        >>> output = check_output_dir("output/frames/", create=True)
        >>> print(output)
        /Users/r/project/output/frames
    """
    output_path = Path(path).resolve()

    # Create directory if requested and doesn't exist
    if create and not output_path.exists():
        output_path.mkdir(parents=True, exist_ok=True)

    # Verify directory exists
    if not output_path.exists():
        raise FileNotFoundError(f"Output directory does not exist: {output_path}")

    if not output_path.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {output_path}")

    # Check if writable
    if not os.access(output_path, os.W_OK):
        raise PermissionError(f"Output directory is not writable: {output_path}")

    return output_path


def check_object_exists(blender_bin: str, blend_path: str, object_name: str) -> bool:
    """Probe Blender to verify object exists in scene.

    Uses Blender subprocess with --python-expr to query objects without
    loading GUI. This is the definitive check - if Blender can't find it,
    render will fail.

    Args:
        blender_bin: Path to Blender executable
        blend_path: Path to .blend file
        object_name: Name of object to find

    Returns:
        bool: True if object exists

    Raises:
        ValueError: If object not found, with list of available objects
        RuntimeError: If Blender subprocess fails

    Example:
        >>> exists = check_object_exists(
        ...     "/Applications/Blender.app/Contents/MacOS/Blender",
        ...     "scene.blend",
        ...     "Cube"
        ... )
        >>> print(exists)
        True
    """
    # Probe Blender to check if object exists
    cmd = [
        blender_bin,
        "-b",
        "--factory-startup",
        blend_path,
        "--python-expr",
        f"import bpy; print('EXISTS:', '{object_name}' in bpy.data.objects)"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Blender subprocess timed out while checking object '{object_name}'"
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Blender subprocess failed (exit {result.returncode}): "
            f"{result.stderr[:200]}"
        )

    # Parse "EXISTS: True" or "EXISTS: False" from output
    exists = False
    for line in result.stdout.split('\n'):
        if 'EXISTS:' in line:
            exists_str = line.split('EXISTS:')[1].strip()
            exists = exists_str == 'True'
            break

    if not exists:
        # Object not found - probe again to get available objects list
        cmd_list = [
            blender_bin,
            "-b",
            "--factory-startup",
            blend_path,
            "--python-expr",
            "import bpy; print('OBJECTS:', ','.join([o.name for o in bpy.data.objects]))"
        ]

        try:
            result_list = subprocess.run(cmd_list, capture_output=True, text=True, timeout=30)

            # Parse available objects
            available = []
            for line in result_list.stdout.split('\n'):
                if 'OBJECTS:' in line:
                    objects_part = line.split('OBJECTS:')[1].strip()
                    if objects_part:
                        available = [o.strip() for o in objects_part.split(',') if o.strip()]
                    break

            available_str = str(available) if available else "[]"
            blend_name = Path(blend_path).name

            raise ValueError(
                f"Object '{object_name}' not found in {blend_name}. "
                f"Available: {available_str}"
            )

        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Blender subprocess timed out while listing objects"
            )

    return True


def run_preflight(blend_path: str, output_dir: str, object_name: str) -> dict:
    """Run all preflight checks for Blender render invocation.

    Validates all prerequisites before spawning Blender subprocess:
    1. Blender binary exists
    2. Blend file exists and is readable
    3. Output directory exists and is writable
    4. Object exists in scene (via Blender probe)

    Writes debug info to staging/debug/ on failure.

    Args:
        blend_path: Path to .blend file
        output_dir: Path to output directory
        object_name: Name of object to render

    Returns:
        dict: Validated paths
            - "blender_path": Path to Blender executable
            - "blend_file": Resolved Path to blend file
            - "output_dir": Resolved Path to output directory

    Raises:
        FileNotFoundError: If Blender binary or blend file not found
        ValueError: If object doesn't exist in scene
        PermissionError: If output directory not writable

    Example:
        >>> result = run_preflight(
        ...     blend_path="scene.blend",
        ...     output_dir="output/",
        ...     object_name="Cube"
        ... )
        >>> print(result["blender_path"])
        /Applications/Blender.app/Contents/MacOS/Blender
    """
    debug_dir = Path("scripts/pipeline/staging/debug")

    try:
        # 1. Check Blender binary
        blender_path = check_blender_binary()

        # 2. Check blend file
        blend_file = check_blend_file(blend_path)

        # 3. Check output directory
        output_path = check_output_dir(output_dir, create=True)

        # 4. Check object exists (via Blender probe)
        check_object_exists(blender_path, str(blend_file), object_name)

        return {
            "blender_path": blender_path,
            "blend_file": blend_file,
            "output_dir": output_path
        }

    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as e:
        # Write debug info on failure
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_log = debug_dir / "preflight_error.log"

        debug_info = [
            "=== Preflight Failure ===",
            f"Error: {e}",
            f"Blend path: {blend_path}",
            f"Output dir: {output_dir}",
            f"Object name: {object_name}",
            ""
        ]

        debug_log.write_text("\n".join(debug_info))

        # Re-raise with original error
        raise
