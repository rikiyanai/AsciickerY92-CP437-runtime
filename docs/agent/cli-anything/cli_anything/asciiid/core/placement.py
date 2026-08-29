"""Instance placement: meshes and sprites."""

from cli_anything.asciiid.core import editor
from cli_anything.asciiid.core.session import update_session


def place_mesh(name: str, x: float, y: float, z: float,
               scale: float = 1.0) -> dict:
    """Place a mesh instance in the world.

    Args:
        name: Mesh filename (e.g., "Tree.akm").
        x, y, z: World coordinates.
        scale: Uniform scale factor.

    Returns:
        Dict with placement info.
    """
    lines = editor.send(
        f"PLACE_MESH {name} {x} {y} {z} {scale}", timeout=10.0
    )
    update_session(modified=True)
    return {
        "status": "placed",
        "type": "mesh",
        "name": name,
        "position": {"x": x, "y": y, "z": z},
        "scale": scale,
        "response": lines,
    }


def place_sprite(name: str, x: float, y: float, z: float,
                 yaw: float = 0.0, anim: int = 0, frame: int = 0) -> dict:
    """Place a sprite instance in the world.

    Args:
        name: Sprite path (e.g., "player-0100.xp").
        x, y, z: World coordinates.
        yaw: Rotation in degrees.
        anim: Animation index.
        frame: Frame index.

    Returns:
        Dict with placement info.
    """
    lines = editor.send(
        f"PLACE_SPRITE {name} {x} {y} {z} {yaw} {anim} {frame}",
        timeout=10.0,
    )
    update_session(modified=True)
    return {
        "status": "placed",
        "type": "sprite",
        "name": name,
        "position": {"x": x, "y": y, "z": z},
        "yaw": yaw,
        "anim": anim,
        "frame": frame,
        "response": lines,
    }


def place_sprite_active(x: float, y: float, z: float,
                        yaw: float = 0.0, anim: int = 0,
                        frame: int = 0) -> dict:
    """Place the active sprite at absolute coordinates.

    Returns:
        Dict with placement info.
    """
    lines = editor.send(
        f"PLACE_SPRITE_ACTIVE {x} {y} {z} {yaw} {anim} {frame}",
        timeout=10.0,
    )
    update_session(modified=True)
    return {
        "status": "placed",
        "type": "sprite_active",
        "position": {"x": x, "y": y, "z": z},
        "yaw": yaw,
        "response": lines,
    }


def place_sprite_active_rel(dx: float, dy: float, dz: float,
                            yaw: float = 0.0, anim: int = 0,
                            frame: int = 0) -> dict:
    """Place the active sprite relative to current camera position.

    Returns:
        Dict with placement info.
    """
    lines = editor.send(
        f"PLACE_SPRITE_ACTIVE_REL {dx} {dy} {dz} {yaw} {anim} {frame}",
        timeout=10.0,
    )
    update_session(modified=True)
    return {
        "status": "placed",
        "type": "sprite_active_rel",
        "offset": {"dx": dx, "dy": dy, "dz": dz},
        "yaw": yaw,
        "response": lines,
    }


def set_active_sprite(name: str) -> dict:
    """Set the active sprite for subsequent placements.

    Args:
        name: Sprite name or path.

    Returns:
        Dict with status.
    """
    lines = editor.send(f"SET_ACTIVE_SPRITE {name}", timeout=10.0)
    return {"status": "set", "active_sprite": name, "response": lines}


def load_sprite(path: str) -> dict:
    """Load a sprite from a file path.

    Args:
        path: Path to .xp sprite file (relative to assets/sprites/ or absolute).

    Returns:
        Dict with status.
    """
    lines = editor.send(f"LOAD_SPRITE {path}", timeout=10.0)
    return {"status": "loaded", "path": path, "response": lines}
