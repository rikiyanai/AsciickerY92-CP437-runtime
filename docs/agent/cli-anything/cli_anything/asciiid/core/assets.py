"""Asset discovery: list loaded meshes, sprites, and reload sprites."""

import re

from cli_anything.asciiid.core import editor


def list_meshes() -> list[dict]:
    """List all loaded meshes.

    Returns:
        List of dicts with idx, name, faces fields.
    """
    lines = editor.send("LIST_MESHES")
    meshes = []
    for line in lines:
        m = re.match(r"\s*(\d+)\s+(\S+)\s+faces=(\d+)", line)
        if m:
            meshes.append({
                "idx": int(m.group(1)),
                "name": m.group(2),
                "faces": int(m.group(3)),
            })
    return meshes


def list_sprites() -> list[dict]:
    """List all loaded sprites with metadata.

    Returns:
        List of dicts with idx, name, angles, anims, frames, projs fields.
    """
    lines = editor.send("LIST_SPRITES")
    sprites = []
    for line in lines:
        m = re.match(
            r"\s*(\d+)\s+(\S+)\s+angles=(\d+)\s+anims=(\d+)\s+frames=(\d+)\s+projs=(\d+)",
            line,
        )
        if m:
            sprites.append({
                "idx": int(m.group(1)),
                "name": m.group(2),
                "angles": int(m.group(3)),
                "anims": int(m.group(4)),
                "frames": int(m.group(5)),
                "projs": int(m.group(6)),
            })
    return sprites


def reload_sprites() -> dict:
    """Trigger sprite reload from disk (equivalent to F5).

    Returns:
        Dict with status and response.
    """
    lines = editor.send("RELOAD_SPRITES")
    success = any("Success" in l for l in lines)
    return {"status": "reloaded" if success else "error", "response": lines}
