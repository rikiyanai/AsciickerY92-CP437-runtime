"""
presets.py -- Real-data import presets for sprite generation pipeline

ARCHITECTURE:
  Import presets derived from measured sprite manifest data (FAMILY_PRESETS).
  Replaces the hypothetical ORC_TEMPLATE (12x12) and ITEM_TEMPLATE (4x4) with
  real geometry from the 210+ sprites in assets/sprites/.

  Called by:
    - cli.py  -- ``get_preset("player")`` as starting point for asset creation
    - xp_tool.py  -- ``get_preset_for_family()`` for editor import presets

KEY EXPORTS:
  - SPRITE_PRESETS: Dict of named presets with real geometry
  - get_preset(name): Name-to-AssetDef lookup (returns fresh copy)
  - get_all_presets(): Full preset registry
  - get_preset_for_family(family_name): Match by family ID or label
  - ORC_TEMPLATE: Backward compat alias (now uses real player geometry)
  - ITEM_TEMPLATE: Backward compat alias (now uses real item geometry)

PIPELINE CONTEXT:
  [DATA-CONTRACT:ASSET-DEF] -- Presets must satisfy AssetDef.validate()
  [DEPENDENCY:SCHEMAS] -- Imports AssetDef from schemas.py
  [DEPENDENCY:SPRITE_INVARIANTS] -- Imports FAMILY_PRESETS for geometry
"""

from copy import deepcopy
from typing import Dict, Any

from .schemas import AssetDef
from .sprite_invariants import FAMILY_PRESETS


# ============================================================================
# SPRITE PRESETS - Real geometry from manifest data
# ============================================================================

# Build presets from FAMILY_PRESETS with human-friendly names
# Each preset includes: name, type, angles, frames, size, projs, family_id, description

def _build_preset(family_id: str, preset_name: str, asset_type: str, description: str) -> Dict[str, Any]:
    """Build a preset dict from FAMILY_PRESETS geometry."""
    family = FAMILY_PRESETS[family_id]

    # Determine frames based on anims_pattern
    anims = family.get("anims_pattern", [1])
    if not anims:  # Empty anims for static sprites like fonts
        anims = [1]

    return {
        "name": preset_name,
        "type": asset_type,
        "angles": family["angles"],
        "frames": anims,
        "size": (family["frame_w"], family["frame_h"]),
        "projs": family["projs"],
        "family_id": family_id,
        "description": description,
        "asset_def": AssetDef(
            name=preset_name,
            type=asset_type,
            angles=family["angles"],
            frames=anims.copy(),
            size=(family["frame_w"], family["frame_h"]),
            projs=family["projs"],
        )
    }


# Real-data presets organized by use case
SPRITE_PRESETS = {
    # Character presets (8-angle humanoids and creatures)
    "player_idle_walk": _build_preset(
        "7x9_a8_p2", "player_idle_walk", "character",
        "Standard player sprite - 7x9 cells, idle+walk animations"
    ),
    "player_attack": _build_preset(
        "7x10_a8_p2", "player_attack", "character",
        "Player with attack - 7x10 cells, idle+walk animations"
    ),
    "bigbee": _build_preset(
        "11x13_a8_p2", "bigbee", "character",
        "Large bee creature - 11x13 cells, idle+fly animations"
    ),
    "wolfie": _build_preset(
        "10x12_a8_p2", "wolfie", "character",
        "Wolf creature - 10x12 cells, idle+walk animations"
    ),
    "wol": _build_preset(
        "10x13_a8_p2", "wol", "character",
        "Wolf variant - 10x13 cells, walk animation"
    ),

    # Item presets (1-angle world objects)
    "item_world": _build_preset(
        "5x5_a1_p2", "item_world", "item",
        "Standard world item - 5x5 cells, single angle"
    ),
    "item_tiny": _build_preset(
        "1x2_a1_p2", "item_tiny", "item",
        "Tiny item - 1x2 cells, single angle"
    ),
    "fire_anim": _build_preset(
        "7x15_a1_p2", "fire_anim", "custom",
        "Animated fire - 7x15 cells, 7 frames"
    ),

    # UI/Grid presets (1-angle, projs=1)
    "grid_small": _build_preset(
        "3x3_a1_p1", "grid_small", "custom",
        "Small grid icon - 3x3 cells"
    ),
    "grid_medium": _build_preset(
        "7x7_a1_p1", "grid_medium", "custom",
        "Medium grid icon - 7x7 cells"
    ),
    "grid_tall": _build_preset(
        "3x11_a1_p1", "grid_tall", "custom",
        "Tall grid icon - 3x11 cells"
    ),
    "grid_big": _build_preset(
        "7x11_a1_p1", "grid_big", "custom",
        "Large grid icon - 7x11 cells"
    ),

    # Special presets
    # NOTE: desert_plants (14 angles) excluded - doesn't pass AssetDef.validate()
    # The engine only supports 1, 4, or 8 angles
    "enemygen": _build_preset(
        "7x12_a1_p2", "enemygen", "custom",
        "Enemy generator - 7x12 cells"
    ),
    "asciicker_logo": _build_preset(
        "40x11_a1_p2", "asciicker_logo", "custom",
        "Asciicker logo sprite - 40x11 cells"
    ),
}


# ============================================================================
# API FUNCTIONS
# ============================================================================

def get_preset(name: str) -> AssetDef | None:
    """
    Look up a pre-built AssetDef preset by name.

    Args:
        name: Preset name (e.g., "player_idle_walk", "item_world")
              Also supports legacy aliases: "character" → "player_idle_walk"
                                           "item" → "item_world"

    Returns:
        A fresh AssetDef copy for the given preset, or None if not found.

    Note:
        Always returns a fresh copy - callers can mutate without affecting
        the shared preset.
    """
    # Handle legacy aliases
    if name == "character":
        name = "player_idle_walk"
    elif name == "item":
        name = "item_world"

    preset = SPRITE_PRESETS.get(name)
    if preset is None:
        return None

    # Return fresh copy to prevent shared mutation
    asset_def = preset["asset_def"]
    return AssetDef(
        name=asset_def.name,
        type=asset_def.type,
        angles=asset_def.angles,
        frames=asset_def.frames.copy(),
        size=asset_def.size,
        projs=asset_def.projs,
        prompt=asset_def.prompt,
        source_type=asset_def.source_type,
        source_path=asset_def.source_path,
        blender_object=asset_def.blender_object,
        render_resolution=asset_def.render_resolution,
        transparency=asset_def.transparency,
        normalization=asset_def.normalization,
        target_cells_high=asset_def.target_cells_high,
    )


def get_all_presets() -> Dict[str, Dict[str, Any]]:
    """
    Get full preset registry.

    Returns:
        Dict mapping preset names to preset metadata dicts.
        Each preset dict contains: name, type, angles, frames, size, projs,
        family_id, description, asset_def.
    """
    return deepcopy(SPRITE_PRESETS)


def get_preset_for_family(family_name: str) -> AssetDef | None:
    """
    Get preset matching a family ID or label.

    Args:
        family_name: Either a family ID (e.g., "7x9_a8_p2") or
                     label (e.g., "player")

    Returns:
        A fresh AssetDef copy for the matching family, or None if not found.
    """
    # Try exact family_id match first
    for preset_name, preset_data in SPRITE_PRESETS.items():
        if preset_data["family_id"] == family_name:
            return get_preset(preset_name)

    # Try label match from FAMILY_PRESETS
    for family_id, family_data in FAMILY_PRESETS.items():
        if family_data["label"] == family_name:
            # Find preset with this family_id
            for preset_name, preset_data in SPRITE_PRESETS.items():
                if preset_data["family_id"] == family_id:
                    return get_preset(preset_name)

    return None


# ============================================================================
# BACKWARD COMPATIBILITY
# ============================================================================

# Legacy templates using real player/item geometry
# OLD: ORC_TEMPLATE was 12x12 (fabricated)
# NEW: Uses real player geometry 7x9 from manifest
ORC_TEMPLATE = get_preset("character")

# OLD: ITEM_TEMPLATE was 4x4 (fabricated)
# NEW: Uses real item geometry 5x5 from manifest
ITEM_TEMPLATE = get_preset("item")
