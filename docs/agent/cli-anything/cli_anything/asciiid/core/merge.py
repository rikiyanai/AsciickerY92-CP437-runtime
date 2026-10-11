"""Map merge operations: open, commit, cancel external .a3d maps."""

import re

from cli_anything.asciiid.core import editor


def merge_open(path: str) -> dict:
    """Open an external .a3d file for merging.

    The merge source is loaded but not applied until merge_commit().
    Use SET_CAMERA to position the merge offset before committing.

    Args:
        path: Path to .a3d file to merge.

    Returns:
        Dict with status, path, terrain (bool), meshes (int).
    """
    lines = editor.command(f"MERGE_OPEN {path}")
    result = {"status": "error", "path": path, "terrain": False, "meshes": 0}
    for line in lines:
        if "Error" in line:
            result["error"] = line.strip()
            return result
        m = re.search(r"terrain=(\w+)\s+meshes=(\d+)", line)
        if m:
            result["status"] = "opened"
            result["terrain"] = m.group(1) == "yes"
            result["meshes"] = int(m.group(2))
    return result


def merge_commit() -> dict:
    """Commit the pending merge at current camera position.

    The merge offset is derived from the camera position.
    Use SET_CAMERA before this to control placement.

    Returns:
        Dict with status, patches, instances, offset_x, offset_y.
    """
    lines = editor.command("MERGE_COMMIT")
    result = {"status": "error", "patches": 0, "instances": 0,
              "offset_x": 0, "offset_y": 0}
    for line in lines:
        if "Error" in line:
            result["error"] = line.strip()
            return result
        m = re.search(
            r"patches=(\d+)\s+instances=(\d+)\s+offset=(-?\d+),(-?\d+)", line
        )
        if m:
            result["status"] = "committed"
            result["patches"] = int(m.group(1))
            result["instances"] = int(m.group(2))
            result["offset_x"] = int(m.group(3))
            result["offset_y"] = int(m.group(4))
    return result


def merge_cancel() -> dict:
    """Cancel the pending merge without applying.

    Returns:
        Dict with status.
    """
    lines = editor.command("MERGE_CANCEL")
    result = {"status": "error"}
    for line in lines:
        if "Error" in line:
            result["error"] = line.strip()
            return result
        if "cancelled" in line:
            result["status"] = "cancelled"
    return result
