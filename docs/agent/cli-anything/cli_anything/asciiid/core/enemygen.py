"""Enemy generator placement: create, list, delete spawn points."""

import re

from cli_anything.asciiid.core import editor


def place_enemygen(x: float, y: float, z: float,
                   alive_max: int = 1, revive_min: int = 0, revive_max: int = 10,
                   armor: int = 5, helmet: int = 5, shield: int = 5,
                   sword: int = 10, crossbow: int = 0) -> dict:
    """Place an enemy generator spawn point.

    Args:
        x, y, z: World position.
        alive_max: Max simultaneous NPCs (1-7).
        revive_min: Min revive exponent (0-10, actual seconds = 2^value).
        revive_max: Max revive exponent (0-10).
        armor: Armor probability (0-10).
        helmet: Helmet probability (0-10).
        shield: Shield probability (0-10).
        sword: Sword weight (0-10).
        crossbow: Crossbow weight (0-10).

    Returns:
        Dict with status and position.
    """
    cmd = (f"PLACE_ENEMYGEN {x} {y} {z} {alive_max} {revive_min} {revive_max} "
           f"{armor} {helmet} {shield} {sword} {crossbow}")
    lines = editor.command(cmd)
    for line in lines:
        if "Error" in line:
            return {"status": "error", "error": line.strip()}
        if "Success" in line:
            return {"status": "placed", "x": x, "y": y, "z": z,
                    "alive_max": alive_max}
    return {"status": "error", "response": lines}


def list_enemygens() -> list[dict]:
    """List all enemy generator spawn points.

    Returns:
        List of dicts with idx, x, y, z, alive_max, revive_min, revive_max,
        armor, helmet, shield, sword, crossbow.
    """
    lines = editor.command("LIST_ENEMYGENS")
    gens = []
    for line in lines:
        m = re.match(
            r"\s*(\d+)\s+pos=([-\d.]+),([-\d.]+),([-\d.]+)\s+"
            r"alive=(\d+)\s+revive=(\d+)-(\d+)\s+"
            r"armor=(\d+)\s+helmet=(\d+)\s+shield=(\d+)\s+"
            r"sword=(\d+)\s+crossbow=(\d+)",
            line,
        )
        if m:
            gens.append({
                "idx": int(m.group(1)),
                "x": float(m.group(2)),
                "y": float(m.group(3)),
                "z": float(m.group(4)),
                "alive_max": int(m.group(5)),
                "revive_min": int(m.group(6)),
                "revive_max": int(m.group(7)),
                "armor": int(m.group(8)),
                "helmet": int(m.group(9)),
                "shield": int(m.group(10)),
                "sword": int(m.group(11)),
                "crossbow": int(m.group(12)),
            })
    return gens


def delete_enemygen(idx: int) -> dict:
    """Delete an enemy generator by index.

    Args:
        idx: Index from list_enemygens().

    Returns:
        Dict with status.
    """
    lines = editor.command(f"DELETE_ENEMYGEN {idx}")
    for line in lines:
        if "Error" in line:
            return {"status": "error", "error": line.strip()}
        if "Success" in line:
            return {"status": "deleted", "idx": idx}
    return {"status": "error", "response": lines}


def delete_all_enemygens() -> dict:
    """Delete all enemy generators.

    Returns:
        Dict with status.
    """
    lines = editor.command("DELETE_ALL_ENEMYGENS")
    for line in lines:
        if "Success" in line:
            return {"status": "deleted_all"}
    return {"status": "error", "response": lines}
