"""Instance manipulation: move, scale, delete instances + undo/redo."""

from cli_anything.asciiid.core import editor


def move(idx: int, dx: float, dy: float, dz: float) -> dict:
    """Move an instance by a delta offset.

    Args:
        idx: 0-based instance index from LIST_INSTANCES.
        dx, dy, dz: Delta offset to apply.

    Returns:
        Dict with status and response.
    """
    lines = editor.command(f"MOVE_INSTANCE {idx} {dx} {dy} {dz}")
    success = any("Success" in l for l in lines)
    return {"status": "moved" if success else "error", "idx": idx, "response": lines}


def scale(idx: int, sx: float, sy: float, sz: float) -> dict:
    """Scale an instance.

    Args:
        idx: 0-based instance index from LIST_INSTANCES.
        sx, sy, sz: Scale factors for each axis.

    Returns:
        Dict with status and response.
    """
    lines = editor.command(f"SCALE_INSTANCE {idx} {sx} {sy} {sz}")
    success = any("Success" in l for l in lines)
    return {"status": "scaled" if success else "error", "idx": idx, "response": lines}


def delete(idx: int) -> dict:
    """Delete an instance.

    Args:
        idx: 0-based instance index from LIST_INSTANCES.

    Returns:
        Dict with status and response.
    """
    lines = editor.command(f"DELETE_INSTANCE {idx}")
    success = any("Success" in l for l in lines)
    return {"status": "deleted" if success else "error", "idx": idx, "response": lines}


def undo() -> dict:
    """Undo the last editor operation.

    Returns:
        Dict with status and response.
    """
    lines = editor.command("UNDO")
    success = any("Success" in l or "undone" in l for l in lines)
    return {"status": "undone" if success else "nothing", "response": lines}


def redo() -> dict:
    """Redo the last undone operation.

    Returns:
        Dict with status and response.
    """
    lines = editor.command("REDO")
    success = any("Success" in l or "redone" in l for l in lines)
    return {"status": "redone" if success else "nothing", "response": lines}


def select_instance(idx: int, add: bool = False) -> dict:
    """Select instance by index. add=True adds to selection instead of replacing."""
    lines = editor.send(f"SELECT_INSTANCE {idx} {1 if add else 0}", timeout=5.0)
    success = any("Success" in l for l in lines)
    return {"status": "selected" if success else "error", "idx": idx, "response": lines}


def clear_selection() -> dict:
    """Clear all instance selection."""
    lines = editor.send("CLEAR_SELECTION", timeout=5.0)
    return {"status": "cleared", "response": lines}


def get_selected() -> list[dict]:
    """List all selected instances with positions."""
    lines = editor.send("GET_SELECTED", timeout=10.0)
    selected = []
    for line in lines:
        if line.strip().startswith("[MCP]"):
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 3:
            try:
                coords = parts[2].split(",")
                selected.append({"idx": int(parts[0]), "name": parts[1],
                                "x": float(coords[0]), "y": float(coords[1]), "z": float(coords[2])})
            except (ValueError, IndexError):
                selected.append({"raw": line})
    return selected
