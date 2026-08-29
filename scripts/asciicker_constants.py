#!/usr/bin/env python3
"""Shared gameplay-space constants for verifier scripts.

This module used to import a deleted `constants.py`. Keep the historical
exports alive by sourcing the live A3D format contract directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_A3D_FORMAT = _REPO_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"

_spec = importlib.util.spec_from_file_location("a3d_format", _A3D_FORMAT)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load A3D format module from {_A3D_FORMAT}")
_a3d_format = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_a3d_format)

ASCIICKER_SCALE = _a3d_format.HEIGHT_SCALE
WATER_LEVEL = _a3d_format.WATER_LEVEL
BASE_HEIGHT = WATER_LEVEL


def blender_to_game_z(z: float) -> float:
    return z * ASCIICKER_SCALE + 120.0


GAME_Z_BASE = BASE_HEIGHT
GAME_Z_SCALE = ASCIICKER_SCALE
GAME_WATER_LEVEL = WATER_LEVEL
