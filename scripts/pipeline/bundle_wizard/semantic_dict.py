"""
semantic_dict.py — Semantic atlas and dictionary for Asciicker XP sprite regions.

PURPOSE
-------
Given a rectangular selection of cells (x1,y1) → (x2,y2) from the visual layer
of any XP sprite sheet, identify what that region is semantically.

Example: cells from the face-no-helmet region of a north-facing player frame
will return `player_sprite_face_north` plus the supporting semantic fields that
produced that label.

TWO-AXIS DESIGN
---------------
Axis 1 — SPATIAL
    local_y / local_x within a 9-wide × 10-tall frame → body part label.
    Head: rows 0-2. Torso: rows 3-6. Legs: rows 7-9.
    weapon_hand: rows 3-5, cols 7-8. face_center: rows 0-2, cols 3-5.

Axis 2 — GLYPH SIGNATURES
    glyph + color content of each cell → equipment / animation state.
    Key signals: transparency ratio, dominant color category,
    dominant glyph category, presence of skin-tone pixels.

COORDINATE CONVENTION
---------------------
Sheet coordinates: (sheet_row, sheet_col)  — absolute pixel on the full XP sheet
Frame coordinates: (local_y, local_x)      — 0-based offset within one 9×10 frame
  local_y = 0 is the top row of the frame (head)
  local_x = 0 is the leftmost column of the frame

Caller supplies the rect in FRAME-LOCAL space: (y1, x1) → (y2, x2) inclusive.

HOW TO USE
----------
    from scripts.pipeline.bundle_wizard.semantic_dict import identify
    result = identify(cells, local_y=0, local_x=3, sprite_type="player",
                      frame_idx=0, angle=4)
    # cells: list of (glyph, fg_rgb, bg_rgb) tuples from the selected rect

    result = {
        "body_part":  "face_center",
        "direction":  "S",
        "equipment":  "bare_head",
        "anim_state": "idle",
        "sprite_type": "player",
        "confidence": 0.87,
    }

BUILDING THE DICTIONARY (Phase 0)
---------------------------------
    from scripts.pipeline.bundle_wizard.semantic_dict import build_asset_semantic_dict
    semantic = build_asset_semantic_dict("assets/sprites/player-0001.xp", "player")
    # Returns one original-source asset entry with:
    #   semantic["surfaces"]["base_visual"][angle][frame_idx][region_name]
    #       = region stats + exact visible rows/cols for that frame/layer
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAME_W = 9
FRAME_H = 10
ANGLE_COUNT = 8
ANGLE_NAMES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
ANGLE_NAME_WORDS = {
    "N": "north",
    "NE": "northeast",
    "E": "east",
    "SE": "southeast",
    "S": "south",
    "SW": "southwest",
    "W": "west",
    "NW": "northwest",
}
TRANSPARENT_BG = (255, 0, 255)
from scripts.pipeline.xp_core import OVERLAY_KEY_RGB as KEY_RGB
TRANSPARENT_KEYS = {TRANSPARENT_BG, KEY_RGB}
REGION_ATLAS_VERSION = 4
SEMANTIC_DICT_VERSION = 5
SEMANTIC_REGION_CELL_CONTRACT = "exact_region_cells_v1"
SEMANTIC_NAMESPACE_ORIGINAL_GAME_XP = "original_game_xp"
SEMANTIC_NAMESPACE_AD_HOC_XP = "ad_hoc_xp"

# Projection columns: each angle row has 2 projections (left=normal, right=mirror)
PROJ_LEFT  = 0
PROJ_RIGHT = 1

# Action-frame counts per sprite type
ACTION_FRAMES: dict[str, dict[str, int]] = {
    "player": {"idle": 1, "walk": 8},
    "attack": {"attack": 8},
    "plydie": {"death": 5},
    "wolfie": {"idle": 1, "walk": 8},
    "bigbee": {"idle": 1, "fly": 2},
}

LAYER_MODE_BASE_VISUAL = "base_visual"
LAYER_MODE_MERGED_VISUAL = "merged_visual"
LAYER_MODE_RAW_LAYER = "raw_layer"

ORIGINAL_GAME_SOURCE_PREFIXES: dict[str, dict[str, str | None]] = {
    "player": {
        "sprite_type": "player",
        "presentation_kind": "idle_walk_character",
        "mount_state": "unmounted",
        "mount_family": None,
        "palette_variant": "default",
    },
    "player-green": {
        "sprite_type": "player",
        "presentation_kind": "idle_walk_character",
        "mount_state": "unmounted",
        "mount_family": None,
        "palette_variant": "green",
    },
    "attack": {
        "sprite_type": "attack",
        "presentation_kind": "attack_character",
        "mount_state": "unmounted",
        "mount_family": None,
        "palette_variant": "default",
    },
    "attack-green": {
        "sprite_type": "attack",
        "presentation_kind": "attack_character",
        "mount_state": "unmounted",
        "mount_family": None,
        "palette_variant": "green",
    },
    "plydie": {
        "sprite_type": "plydie",
        "presentation_kind": "plydie_character",
        "mount_state": "unmounted",
        "mount_family": None,
        "palette_variant": "default",
    },
    "plydie-green": {
        "sprite_type": "plydie",
        "presentation_kind": "plydie_character",
        "mount_state": "unmounted",
        "mount_family": None,
        "palette_variant": "green",
    },
    "wolfie": {
        "sprite_type": "wolfie",
        "presentation_kind": "mounted_idle_walk_source",
        "mount_state": "mounted",
        "mount_family": "wolf",
        "palette_variant": "default",
    },
    "wolack": {
        "sprite_type": "attack",
        "presentation_kind": "mounted_attack_source",
        "mount_state": "mounted",
        "mount_family": "wolf",
        "palette_variant": "default",
    },
    "bigbee": {
        "sprite_type": "bigbee",
        "presentation_kind": "mounted_idle_flight_source",
        "mount_state": "mounted",
        "mount_family": "bee",
        "palette_variant": "default",
    },
}


def _match_original_game_source_prefix(stem: str) -> tuple[str, str] | None:
    for prefix in sorted(ORIGINAL_GAME_SOURCE_PREFIXES, key=len, reverse=True):
        if stem.startswith(prefix + "-"):
            return prefix, stem[len(prefix) + 1:]
    return None


def _weapon_variant_from_digit(digit: str) -> str:
    if digit == "0":
        return "none"
    if digit == "1":
        return "sword"
    if digit == "2":
        return "crossbow"
    return f"code_{digit}"


def describe_original_game_source_asset(asset_name: str) -> dict[str, object] | None:
    stem = Path(asset_name).stem
    matched = _match_original_game_source_prefix(stem)
    if matched is None:
        return None
    prefix, equipment_code = matched
    if len(equipment_code) != 4 or not equipment_code.isdigit():
        return None

    base_info = ORIGINAL_GAME_SOURCE_PREFIXES[prefix]
    armor_digit, helmet_digit, shield_digit, weapon_digit = equipment_code
    enabled_slots = []
    if armor_digit != "0":
        enabled_slots.append("armor")
    if helmet_digit != "0":
        enabled_slots.append("helmet")
    if shield_digit != "0":
        enabled_slots.append("shield")
    weapon_variant = _weapon_variant_from_digit(weapon_digit)
    if weapon_variant != "none":
        enabled_slots.append(f"weapon:{weapon_variant}")

    return {
        "semantic_namespace": SEMANTIC_NAMESPACE_ORIGINAL_GAME_XP,
        "source_asset_name": asset_name,
        "source_asset_stem": stem,
        "source_family": prefix,
        "sprite_type": base_info["sprite_type"],
        "presentation_kind": base_info["presentation_kind"],
        "mount_state": base_info["mount_state"],
        "mount_family": base_info["mount_family"],
        "palette_variant": base_info["palette_variant"],
        "variant_signature": {
            "equipment_code": equipment_code,
            "armor_enabled": armor_digit != "0",
            "helmet_enabled": helmet_digit != "0",
            "shield_enabled": shield_digit != "0",
            "weapon_code": weapon_digit,
            "weapon_variant": weapon_variant,
            "enabled_slots": enabled_slots,
        },
    }


def is_original_game_source_asset_name(asset_name: str) -> bool:
    return describe_original_game_source_asset(asset_name) is not None

# Semantic atlas regions within one frame, defined in normalized coordinates so
# the same semantic zones can be projected onto 7x9, 9x10, 10x12, 11x13, etc.
# ORDER MATTERS: more-specific sub-regions must appear before their parent
# regions so that cell lookup resolves to the tightest semantic region first.
_REGION_ATLAS = [
    {
        "name": "face_center",
        "body_group": "face",
        "semantic_focus": "face",
        "row_frac": (0.00, 0.52),
        "col_frac": (0.24, 0.76),
        "definition": "Exact facial core only, not the whole head or torso.",
        "strict_reject_if": "Reject crops that include shoulders, full head mass, or upper torso instead of the narrow face core.",
        "front_seed_7x9_cells": [[1, 3], [2, 3]],
    },
    {
        "name": "head_top",
        "body_group": "head",
        "semantic_focus": "head_top",
        "row_frac": (0.00, 0.14),
        "col_frac": (0.28, 0.72),
        "definition": "Topmost head crown cell(s), not the whole face.",
        "strict_reject_if": "Reject crops that include eyes, face, or torso.",
        "front_seed_7x9_cells": [[0, 3]],
    },
    {
        "name": "hair",
        "body_group": "hair",
        "semantic_focus": "hair",
        "row_frac": (0.00, 0.24),
        "col_frac": (0.18, 0.82),
        "definition": "Hair or scalp-cap contour only when it reads as a distinct top/head covering region.",
        "strict_reject_if": "Reject crops that are really the whole face or crown-plus-face mass rather than a distinct hair/scalp band.",
        "front_seed_hint": "For 7x9 front-view rider sprites, hair is the narrow band centered on row 1, cols 2-4, not the full face-plus-hair block.",
        "front_seed_7x9_rect": {"rows": [1, 1], "cols": [2, 4]},
    },
    {
        "name": "eyes_nose",
        "body_group": "face",
        "semantic_focus": "eyes_nose",
        "row_frac": (0.18, 0.39),
        "col_frac": (0.28, 0.72),
        "definition": "Eye-line and nose bridge only when those facial features are actually visible for the facing.",
        "strict_reject_if": "Reject when the sprite faces away or when the crop spans head, hair, or torso beyond the eye-line.",
        "front_seed_7x9_cells": [[1, 3]],
    },
    {
        "name": "mouth",
        "body_group": "face",
        "semantic_focus": "mouth",
        "row_frac": (0.39, 0.52),
        "col_frac": (0.34, 0.66),
        "definition": "Lower facial mouth/chin mark only, not torso or whole lower head mass.",
        "strict_reject_if": "Reject when the crop is really torso or broad lower-head mass rather than a specific mouth/chin marker.",
        "front_seed_7x9_cells": [[2, 3]],
    },
    {
        "name": "left_arm",
        "body_group": "arm",
        "semantic_focus": "left_arm",
        "row_frac": (0.30, 0.68),
        "col_frac": (0.00, 0.28),
        "definition": "Figure-left arm cells only.",
        "strict_reject_if": "Reject if the crop is body core, pelvis, or an angle-swapped opposite limb.",
        "front_seed_7x9_rect": {"rows": [4, 5], "cols": [1, 1]},
    },
    {
        "name": "right_arm",
        "body_group": "arm",
        "semantic_focus": "right_arm",
        "row_frac": (0.30, 0.68),
        "col_frac": (0.72, 1.00),
        "definition": "Figure-right arm cells only.",
        "strict_reject_if": "Reject if the crop is body core, pelvis, or an angle-swapped opposite limb.",
        "front_seed_7x9_rect": {"rows": [4, 5], "cols": [4, 4]},
    },
    {
        "name": "weapon_hand",
        "body_group": "weapon_hand",
        "semantic_focus": "weapon_hand",
        "row_frac": (0.38, 0.68),
        "col_frac": (0.76, 1.00),
    },
    {
        "name": "left_foot",
        "body_group": "foot",
        "semantic_focus": "left_foot",
        "row_frac": (0.90, 1.00),
        "col_frac": (0.08, 0.38),
        "definition": "Figure-left foot endpoint only.",
        "strict_reject_if": "Reject if the crop is really shin, pelvis, or the opposite foot under side-facing ambiguity.",
        "front_seed_7x9_cells": [[7, 3]],
    },
    {
        "name": "right_foot",
        "body_group": "foot",
        "semantic_focus": "right_foot",
        "row_frac": (0.90, 1.00),
        "col_frac": (0.62, 0.92),
        "definition": "Figure-right foot endpoint only.",
        "strict_reject_if": "Reject if the crop is really shin, pelvis, or the opposite foot under side-facing ambiguity.",
        "front_seed_7x9_cells": [[7, 5]],
    },
    {
        "name": "left_leg",
        "body_group": "leg",
        "semantic_focus": "left_leg",
        "row_frac": (0.62, 0.88),
        "col_frac": (0.16, 0.42),
        "definition": "Figure-left leg only, not central crotch/pelvis mass.",
        "strict_reject_if": "Reject if the crop is really pelvis/crotch hinge or the opposite leg under side-facing ambiguity.",
        "front_seed_7x9_cells": [[6, 3]],
    },
    {
        "name": "right_leg",
        "body_group": "leg",
        "semantic_focus": "right_leg",
        "row_frac": (0.62, 0.88),
        "col_frac": (0.58, 0.84),
        "definition": "Figure-right leg only, not central crotch/pelvis mass.",
        "strict_reject_if": "Reject if the crop is really pelvis/crotch hinge or the opposite leg under side-facing ambiguity.",
        "front_seed_7x9_cells": [[6, 5]],
    },
    {
        "name": "pelvis",
        "body_group": "pelvis",
        "semantic_focus": "pelvis",
        "row_frac": (0.52, 0.78),
        "col_frac": (0.24, 0.76),
        "definition": "Rider lower-body hinge or crotch/hip transition, not the whole lower body.",
        "strict_reject_if": "Reject when the crop is mount-owned lower body mass, broad shoulders-down coverage, or a whole leg block instead of the narrow hip hinge.",
        "front_seed_hint": "For 7x9 front-view rider sprites, pelvis reads around the central lower hinge above the separated legs, not the whole shoulders-down block.",
        "worker_prompt": "Mounted sheets: answer owner first, then primitive shape. Look for leg evidence such as blue lower-body pairs before naming pelvis. Only use rider pelvis when a visible rider hip hinge is present. If the crop reads as wolf/bee body mass, belly, haunch, broad lower-body core, or a whole leg block, reject rider pelvis and correct toward mount-owned mass instead.",
    },
    {
        "name": "seat_anchor",
        "body_group": "seat",
        "semantic_focus": "seat_anchor",
        "row_frac": (0.56, 0.78),
        "col_frac": (0.30, 0.70),
        "definition": "Narrow rider-to-mount contact patch used for placement/offset reasoning, not general mount core mass.",
        "strict_reject_if": "Reject when the crop reads as broad mount core/pelvis mass rather than a distinct rider contact patch.",
        "front_seed_hint": "Front-view rider contact tends to sit in the central band around rows 6-7. For wolfie-like mount sheets, the only plausible seat-anchor search area is the narrow centered 06-07 band; once the crop spills into the broader 08+ body mass, reject it as general mount core instead.",
        "worker_prompt": "Mounted sheets: classify owner first, then ask whether a narrow contact-band primitive is actually visible. If you cannot point to a distinct center-band rider contact patch, do not guess seat_anchor. Use a mount-owned correction instead of stretching seat_anchor over broad wolf/bee torso mass.",
    },
    {
        "name": "head",
        "body_group": "head",
        "semantic_focus": "head",
        "row_frac": (0.00, 0.52),
        "col_frac": (0.14, 0.86),
    },
    {
        "name": "torso",
        "body_group": "torso",
        "semantic_focus": "torso",
        "row_frac": (0.30, 0.72),
        "col_frac": (0.18, 0.82),
        "definition": "Rider torso/core mass only.",
        "strict_reject_if": "Reject when the crop is really head/face, mount front surface, or broad whole-body mass.",
        "worker_prompt": "Mounted sheets: classify owner before anatomy and prefer cue-first primitives. If the selected cells read as wolf/bee body mass in front of or behind the rider rather than visible rider chest/abdomen, reject rider torso and correct toward mount-owned front/rear mass.",
    },
    {
        "name": "legs",
        "body_group": "leg",
        "semantic_focus": "legs",
        "row_frac": (0.62, 1.00),
        "col_frac": (0.10, 0.90),
    },
]


def _scale_bounds(lo_frac: float, hi_frac: float, size: int) -> tuple[int, int]:
    if size <= 0:
        return (0, 0)
    lo = max(0, min(size - 1, int(lo_frac * size)))
    hi = max(lo, min(size - 1, int((hi_frac * size) - 1e-9)))
    return (lo, hi)


def _resolve_region_entry(
    entry: dict[str, object],
    *,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> dict[str, object]:
    row_lo, row_hi = _scale_bounds(float(entry["row_frac"][0]), float(entry["row_frac"][1]), frame_h)
    col_lo, col_hi = _scale_bounds(float(entry["col_frac"][0]), float(entry["col_frac"][1]), frame_w)
    return {
        "name": entry["name"],
        "body_group": entry["body_group"],
        "semantic_focus": entry["semantic_focus"],
        "row_lo": row_lo,
        "row_hi": row_hi,
        "col_lo": col_lo,
        "col_hi": col_hi,
    }


def _resolved_region_atlas(
    *,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> list[dict[str, object]]:
    return [_resolve_region_entry(entry, frame_w=frame_w, frame_h=frame_h) for entry in _REGION_ATLAS]


def _region_area(entry: dict[str, object]) -> int:
    return (int(entry["row_hi"]) - int(entry["row_lo"]) + 1) * (int(entry["col_hi"]) - int(entry["col_lo"]) + 1)


def _get_region_entry(name: str, *, frame_w: int = FRAME_W, frame_h: int = FRAME_H) -> dict[str, object]:
    for entry in _resolved_region_atlas(frame_w=frame_w, frame_h=frame_h):
        if entry["name"] == name:
            return entry
    raise KeyError(f"Unknown semantic region '{name}'")


def _export_region_atlas(
    *,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> list[dict[str, object]]:
    exported: list[dict[str, object]] = []
    for base_entry, entry in zip(_REGION_ATLAS, _resolved_region_atlas(frame_w=frame_w, frame_h=frame_h)):
        exported_entry = {
            "name": entry["name"],
            "body_group": entry["body_group"],
            "semantic_focus": entry["semantic_focus"],
            "rows": [entry["row_lo"], entry["row_hi"]],
            "cols": [entry["col_lo"], entry["col_hi"]],
            "area_cells": _region_area(entry),
            "row_frac": [base_entry["row_frac"][0], base_entry["row_frac"][1]],
            "col_frac": [base_entry["col_frac"][0], base_entry["col_frac"][1]],
        }
        for optional_key in (
            "definition",
            "strict_reject_if",
            "front_seed_hint",
            "front_seed_7x9_rect",
            "front_seed_7x9_cells",
            "worker_prompt",
        ):
            if optional_key in base_entry:
                exported_entry[optional_key] = base_entry[optional_key]
        exported.append(exported_entry)
    return exported


def _synthetic_layer_from_rows(
    rows: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> object:
    class _SyntheticLayer:
        def __init__(self, layer_rows: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]) -> None:
            self.data = layer_rows
            self.height = len(layer_rows)
            self.width = len(layer_rows[0]) if layer_rows else 0

    return _SyntheticLayer(rows)


def _cp437_char(glyph: int) -> str:
    if glyph in (0, 32):
        return " "
    try:
        return bytes([glyph & 0xFF]).decode("cp437")
    except Exception:
        return "?"


def _build_surface_key(layer_mode: str, source_layer_index: int) -> str:
    if layer_mode == LAYER_MODE_RAW_LAYER:
        return f"raw_layer_{source_layer_index}"
    return layer_mode


def _describe_source_asset(xp_path: str, sprite_type: str) -> dict[str, object]:
    path = Path(xp_path)
    original = describe_original_game_source_asset(path.name)
    if original is not None:
        return original
    return {
        "semantic_namespace": SEMANTIC_NAMESPACE_AD_HOC_XP,
        "source_asset_name": path.name,
        "source_asset_stem": path.stem,
        "source_family": sprite_type,
        "sprite_type": sprite_type,
        "presentation_kind": sprite_type,
        "mount_state": "unknown",
        "mount_family": None,
        "palette_variant": "unknown",
        "variant_signature": {
            "equipment_code": "unknown",
            "armor_enabled": False,
            "helmet_enabled": False,
            "shield_enabled": False,
            "weapon_code": "unknown",
            "weapon_variant": "unknown",
            "enabled_slots": [],
        },
    }


def _resolve_semantic_surface(
    xp: object,
    *,
    xp_path: str,
    layer_mode: str,
    raw_layer_index: int | None = None,
) -> tuple[object, dict[str, object]]:
    layer_count = len(xp.layers)
    if layer_count < 3:
        raise ValueError(
            f"build_from_xp: '{xp_path}' has {layer_count} layers; "
            "expected at least 3 (layer 0 metadata, layer 2 base visual)"
        )

    if layer_mode == LAYER_MODE_BASE_VISUAL:
        return xp.layers[2], {"layer_mode": layer_mode, "source_layer_index": 2}

    if layer_mode == LAYER_MODE_MERGED_VISUAL:
        from scripts.pipeline import xp_assets_browser_layer_2_only as layer2_browser

        merged_rows = layer2_browser._merge_layers(xp)
        return _synthetic_layer_from_rows(merged_rows), {
            "layer_mode": layer_mode,
            "source_layer_index": 2,
            "merged_overlay_layers": list(range(3, layer_count)),
        }

    if layer_mode == LAYER_MODE_RAW_LAYER:
        if raw_layer_index is None:
            raise ValueError("build_from_xp: raw_layer mode requires raw_layer_index")
        if raw_layer_index < 0 or raw_layer_index >= layer_count:
            raise ValueError(
                f"build_from_xp: raw_layer_index {raw_layer_index} out of range for "
                f"'{xp_path}' ({layer_count} layers)"
            )
        return xp.layers[raw_layer_index], {
            "layer_mode": layer_mode,
            "source_layer_index": raw_layer_index,
        }

    raise ValueError(
        f"build_from_xp: unknown layer_mode '{layer_mode}'; expected "
        f"{LAYER_MODE_BASE_VISUAL}|{LAYER_MODE_MERGED_VISUAL}|{LAYER_MODE_RAW_LAYER}"
    )

# ---------------------------------------------------------------------------
# Per-angle anchor system (FL-2897)
# ---------------------------------------------------------------------------

# Module-level anchor cache: maps (sprite_type, angle) -> inverse index dict
# where inverse index maps (y, x) -> region_name for O(1) body-part lookup.
_ANGLE_ANCHORS: dict[tuple[str, int], dict[tuple[int, int], str]] = {}
_ANGLE_ANCHOR_REGIONS: dict[tuple[str, int], list[dict]] = {}
_ANGLE_ANCHOR_META: dict[str, dict] = {}


def load_angle_anchors(json_path: str, sprite_type: str = "player") -> dict:
    """
    Load per-angle anchor data from a pipeline-v3 semantic map JSON file.

    The JSON file should contain 8 frame entries (one per angle), each with
    per-angle regions including bbox and semantic_cells.

    Populates the module-level _ANGLE_ANCHORS cache so that
    get_body_part_at(y, x, angle=N) uses angle-specific lookups.

    Returns the loaded anchor metadata dict.
    """
    path = Path(json_path)
    if not path.is_file():
        raise FileNotFoundError(f"Anchor file not found: {json_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    anchor_frame_w = data.get("frame_w", FRAME_W)
    anchor_frame_h = data.get("frame_h", FRAME_H)

    _ANGLE_ANCHOR_META[sprite_type] = {
        "path": str(path),
        "frame_w": anchor_frame_w,
        "frame_h": anchor_frame_h,
        "family": data.get("family", sprite_type),
    }

    frames = data.get("frames", {})
    for frame_key, frame_data in frames.items():
        angle = frame_data.get("angle")
        if angle is None:
            # Try parsing frame_key as angle index
            try:
                angle = int(frame_key)
            except (ValueError, TypeError):
                continue
        if not (0 <= angle < ANGLE_COUNT):
            continue

        cache_key = (sprite_type, angle)

        # Build inverse index: (y, x) -> region_name
        inverse: dict[tuple[int, int], str] = {}
        regions_list: list[dict] = []

        for region in frame_data.get("regions", []):
            name = region.get("name", "unknown")
            bbox = region.get("bbox", [])
            slot_affinity = region.get("slot_affinity")
            region_entry = {
                "name": name,
                "bbox": bbox,
                "slot_affinity": slot_affinity,
                "palette_roles": region.get("palette_roles", []),
            }
            regions_list.append(region_entry)

            # Populate inverse index from semantic_cells if available
            for cell in region.get("semantic_cells", []):
                cx, cy = cell.get("x", -1), cell.get("y", -1)
                if cx >= 0 and cy >= 0:
                    inverse[(cy, cx)] = name

            # Also populate from bbox for any cells not in semantic_cells
            if len(bbox) == 4:
                x0, y0, x1, y1 = bbox
                for by in range(y0, y1 + 1):
                    for bx in range(x0, x1 + 1):
                        if (by, bx) not in inverse:
                            inverse[(by, bx)] = name

        _ANGLE_ANCHORS[cache_key] = inverse
        _ANGLE_ANCHOR_REGIONS[cache_key] = regions_list

    return _ANGLE_ANCHOR_META[sprite_type]


def clear_angle_anchors(sprite_type: str | None = None) -> None:
    """Clear loaded anchor data. If sprite_type is None, clear all."""
    keys_to_remove = []
    for key in _ANGLE_ANCHORS:
        if sprite_type is None or key[0] == sprite_type:
            keys_to_remove.append(key)
    for key in keys_to_remove:
        _ANGLE_ANCHORS.pop(key, None)
        _ANGLE_ANCHOR_REGIONS.pop(key, None)
    if sprite_type is None:
        _ANGLE_ANCHOR_META.clear()
    else:
        _ANGLE_ANCHOR_META.pop(sprite_type, None)


def has_angle_anchors(sprite_type: str = "player", angle: int | None = None) -> bool:
    """Check whether per-angle anchor data is loaded."""
    if angle is not None:
        return (sprite_type, angle) in _ANGLE_ANCHORS
    return sprite_type in _ANGLE_ANCHOR_META


def get_anchor_regions(sprite_type: str, angle: int) -> list[dict]:
    """Return the list of region defs for a loaded anchor, or empty list."""
    return _ANGLE_ANCHOR_REGIONS.get((sprite_type, angle), [])


# ---------------------------------------------------------------------------
# Axis 1 — Spatial lookup
# ---------------------------------------------------------------------------

def get_body_part_at(
    local_y: int,
    local_x: int,
    *,
    angle: int | None = None,
    sprite_type: str = "player",
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> str:
    """
    Return the most-specific body-part label for a single cell coordinate.

    When per-angle anchor data is loaded and ``angle`` is supplied, uses
    the O(1) anchor inverse index. Otherwise falls back to the static
    fractional _REGION_ATLAS.
    """
    if angle is not None:
        cache_key = (sprite_type, angle)
        inverse = _ANGLE_ANCHORS.get(cache_key)
        if inverse is not None:
            return inverse.get((local_y, local_x), "unknown")
    # Fallback: static fractional atlas
    for entry in _resolved_region_atlas(frame_w=frame_w, frame_h=frame_h):
        if entry["row_lo"] <= local_y <= entry["row_hi"] and entry["col_lo"] <= local_x <= entry["col_hi"]:
            return str(entry["name"])
    return "unknown"


def get_rect_body_part(
    y1: int,
    x1: int,
    y2: int,
    x2: int,
    *,
    angle: int | None = None,
    sprite_type: str = "player",
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> str:
    """
    Return the dominant body part for a rectangular selection (inclusive).
    Tally votes from each contained cell; return the most frequent label.
    Ties broken by specificity order in _BODY_REGIONS.
    """
    votes: dict[str, int] = {}
    for ry in range(y1, y2 + 1):
        for rx in range(x1, x2 + 1):
            part = get_body_part_at(ry, rx, angle=angle, sprite_type=sprite_type, frame_w=frame_w, frame_h=frame_h)
            votes[part] = votes.get(part, 0) + 1
    if not votes:
        return "unknown"
    region_order = {str(entry["name"]): idx for idx, entry in enumerate(_resolved_region_atlas(frame_w=frame_w, frame_h=frame_h))}
    return max(votes, key=lambda name: (votes[name], -region_order.get(name, len(region_order) + 1)))


def get_frame_origin(
    angle: int,
    frame_idx: int,
    proj: int,
    sprite_type: str,
    *,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> tuple[int, int]:
    """
    Return (sheet_row, sheet_col) of the top-left cell of a specific frame.

    Sheet layout (standard):
        rows   → angle index (0 = N … 7 = NW), each frame_h rows tall
        cols   → (proj * total_frames + frame_idx) * frame_w columns wide
    """
    total_frames = sum(ACTION_FRAMES.get(sprite_type, {"idle": 1}).values())
    sheet_row = angle * frame_h
    sheet_col = (proj * total_frames + frame_idx) * frame_w
    return (sheet_row, sheet_col)


def _iter_anim_frames(anim_info: list[int]) -> list[tuple[int, int, int]]:
    frames: list[tuple[int, int, int]] = []
    flat_frame_idx = 0
    for anim_index, anim_length in enumerate(anim_info):
        for frame_index_in_anim in range(anim_length):
            frames.append((anim_index, frame_index_in_anim, flat_frame_idx))
            flat_frame_idx += 1
    return frames


def _get_frame_origin_from_variant(
    angle: int,
    anim_index: int,
    frame_index_in_anim: int,
    proj: int,
    anim_info: list[int],
    *,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int]:
    sheet_row = angle * frame_h
    sheet_col = (proj * sum(anim_info) + sum(anim_info[:anim_index]) + frame_index_in_anim) * frame_w
    return (sheet_row, sheet_col)


def _get_body_group(region_name: str, *, frame_w: int = FRAME_W, frame_h: int = FRAME_H) -> str:
    if region_name == "unknown":
        return "unknown"
    try:
        return str(_get_region_entry(region_name, frame_w=frame_w, frame_h=frame_h)["body_group"])
    except KeyError:
        # Anchor regions may use simplified names not in the static atlas.
        # Infer body group from the region name itself.
        name = region_name.lower()
        if any(k in name for k in ("face", "head", "hair", "eye", "mouth")):
            return "head"
        if any(k in name for k in ("shirt", "torso", "arm")):
            return "torso"
        if any(k in name for k in ("pants", "leg", "boot", "foot")):
            return "leg"
        return "unknown"


def _get_semantic_focus(region_name: str, *, frame_w: int = FRAME_W, frame_h: int = FRAME_H) -> str:
    if region_name == "unknown":
        return "unknown"
    try:
        return str(_get_region_entry(region_name, frame_w=frame_w, frame_h=frame_h)["semantic_focus"])
    except KeyError:
        return region_name


def _get_frame_role(sprite_type: str, frame_idx: int) -> str:
    if sprite_type == "attack":
        return "attack"
    if sprite_type == "plydie":
        return "death"
    if sprite_type == "bigbee":
        return "idle" if frame_idx == 0 else "flight"
    return "idle" if frame_idx == 0 else "walk"


def _get_frame_role_for_variant(
    sprite_type: str,
    anim_index: int,
    frame_index_in_anim: int,
    flat_frame_idx: int,
) -> str:
    if sprite_type == "attack":
        return "attack"
    if sprite_type == "plydie":
        return "death"
    if sprite_type == "bigbee":
        return "idle" if anim_index == 0 and frame_index_in_anim == 0 else "flight"
    if anim_index == 0 and frame_index_in_anim == 0 and flat_frame_idx == 0:
        return "idle"
    return "walk"


def _direction_word(angle: int) -> str:
    return ANGLE_NAME_WORDS[ANGLE_NAMES[angle % ANGLE_COUNT]]


def _build_semantic_bits(
    region_name: str,
    sprite_type: str,
    angle: int,
    frame_idx: int,
    *,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> list[str]:
    direction = _direction_word(angle)
    bits = [sprite_type, "sprite", _get_semantic_focus(region_name, frame_w=frame_w, frame_h=frame_h), direction, _get_frame_role(sprite_type, frame_idx)]
    return bits


def _build_semantic_id(
    region_name: str,
    sprite_type: str,
    angle: int,
    *,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> str:
    direction = _direction_word(angle)
    return f"{sprite_type}_sprite_{_get_semantic_focus(region_name, frame_w=frame_w, frame_h=frame_h)}_{direction}"


# ---------------------------------------------------------------------------
# Axis 2 — Glyph signature helpers
# ---------------------------------------------------------------------------

_SKIN_TONES = {
    (255, 204, 153), (255, 178, 102), (204, 153, 102),
    (255, 220, 177), (230, 180, 140), (180, 120, 80),
}


def _engine_cell_transparency_flags(
    cell: tuple[int, tuple[int, int, int], tuple[int, int, int]],
    *,
    layer0_key_rgb: tuple[int, int, int] | None = None,
) -> dict[str, object]:
    """Recreate the original loader's per-cell transparency semantics."""
    glyph, fg, bg = cell
    fg_rgb = tuple(fg)
    bg_rgb = tuple(bg)
    key_rgb = tuple(layer0_key_rgb) if layer0_key_rgb is not None else None
    bg_matches_layer0_key = key_rgb is not None and bg_rgb == key_rgb
    fg_matches_layer0_key = key_rgb is not None and fg_rgb == key_rgb
    rexpaint_bg_transparent = bg_rgb == TRANSPARENT_BG
    fallback_bg_transparent = key_rgb is None and bg_rgb in TRANSPARENT_KEYS
    fallback_fg_transparent = key_rgb is None and fg_rgb in TRANSPARENT_KEYS
    engine_bg_transparent = rexpaint_bg_transparent or bg_matches_layer0_key or fallback_bg_transparent
    engine_fg_transparent = rexpaint_bg_transparent or fg_matches_layer0_key or fallback_fg_transparent
    if glyph in (0, 32):
        engine_visible = not engine_bg_transparent
    else:
        engine_visible = not (engine_bg_transparent and engine_fg_transparent)
    return {
        "layer0_key_rgb": key_rgb,
        "bg_matches_layer0_key": bg_matches_layer0_key,
        "fg_matches_layer0_key": fg_matches_layer0_key,
        "engine_bg_transparent": engine_bg_transparent,
        "engine_fg_transparent": engine_fg_transparent,
        "engine_visible": engine_visible,
    }


def _is_visible_cell(
    cell: tuple[int, tuple[int, int, int], tuple[int, int, int]],
    *,
    layer0_key_rgb: tuple[int, int, int] | None = None,
) -> bool:
    return bool(_engine_cell_transparency_flags(cell, layer0_key_rgb=layer0_key_rgb)["engine_visible"])

def _color_category(rgb: tuple[int, int, int]) -> str:
    """Classify an RGB triple into a broad semantic colour bucket."""
    r, g, b = rgb
    if rgb in TRANSPARENT_KEYS:
        return "transparent"
    if rgb in _SKIN_TONES or (200 <= r <= 255 and 140 <= g <= 210 and 80 <= b <= 170 and r > g > b):
        return "skin"
    if r > 180 and g > 150 and b < 80:
        return "gold"
    if abs(r - g) < 30 and abs(g - b) < 30 and r > 100:
        return "metal"
    if r < 80 and g < 80 and b < 80:
        return "dark"
    if b > 140 and b > r + 40 and b > g + 30:
        return "cloth_blue"
    return "colored"


def _glyph_category(g: int) -> str:
    """Classify a CP437 glyph value into a broad shape bucket."""
    if g == 0 or g == 32:
        return "empty"
    if g in (219, 220, 221, 222, 223):   # full/half blocks
        return "block"
    if g in (176, 177, 178):             # dither patterns
        return "dither"
    if g in range(179, 219):             # box-drawing lines
        return "line"
    if g in (9, 10, 254, 233, 248, 249): # bullets, dots, round chars
        return "round"
    if 65 <= g <= 90:
        return "upper"
    if 97 <= g <= 122:
        return "lower"
    if 48 <= g <= 57:
        return "digit"
    return "symbol"


# ---------------------------------------------------------------------------
# Axis 2b — Role-fingerprint glyph signatures (FL-4078, 2026-05-20)
# ---------------------------------------------------------------------------
# Empirical role-discriminating glyph table built from 26 ledger-verified role
# layers and 13 user-confirmed glyph identifications. See:
#   memory/reference_xp_glyph_family_signatures.md
#   docs/FAILURE_LOG.md FL-4078 INFO updates 2026-05-20
#
# Upstream engine does NOT identify per-layer roles — files are loaded by AHSW
# tuple (game.cpp:3181-3260 GetSprite) and all L3+ layers merge into L2 blindly
# (sprite.cpp:541-622 LoadSprite). Per-layer role identification is a Y9-2
# pipeline concern. The signatures below let the pipeline resolve combo-file
# layer ordering without re-running visual review for every file.

SILHOUETTE_HALFBLOCKS = {0xdb, 0xdc, 0xdd, 0xde, 0xdf}
# 0xdb = █ full block, 0xdc = ▄ lower half, 0xdd = ▌ left half,
# 0xde = ▐ right half, 0xdf = ▀ upper half.
# These appear in every role layer as anti-aliased silhouette skin.
# NOT role-discriminating; filter out before role classification.

GLYPH_SIGNATURES = {
    # --- Strong role fingerprints (appear in <=4 roles, high signal) ---
    0x40: ("shield",     "shield_boss"),         # @  -- round-shield dome boss
    0x07: ("weapon",     "crossbow_bolt"),       # BEL -- crossbow bolt nock
    0xc4: ("weapon",     "crossbow_stock"),      # -- (hbar) horizontal crossbow stock
    0xb3: ("weapon",     "crossbow_trigger"),    # |  (vbar) vertical crossbow trigger
    0x19: ("weapon",     "crossbow_windlass"),   # EOM
    0x3c: ("mount_body", "bee_wing_left"),       # <  -- bigbee L2 only
    0x3e: ("mount_body", "bee_wing_right"),      # >  -- bigbee L2 only
    # --- Anatomy (position-gated; caller filters by frame row) ---
    0x22: ("face", "eyes"),                      # "  -- paired dot-glyph cells
    0x76: ("face", "mouth_front"),               # v  -- front-view mouth
    0x27: ("face", "mouth_side"),                # '  -- side-view mouth (profile)
    0x60: ("face", "mouth_corner"),              # `  -- mouth corner / cheek
    0xbf: ("face", "ear_corner_dr"),             # corner glyphs - face/ear outline
    0xc0: ("face", "ear_corner_ul"),
    0xd9: ("face", "ear_corner_ur"),
    0xda: ("face", "ear_corner_dl"),
    0x2e: ("face", "tooth_dot"),                 # .  -- player baseline only
    # --- Family texture shading ---
    0xb0: ("texture", "wolf_fur"),               # -- light dither (wolfie/wolack L2 body only)
    0xb1: ("texture", "armor_chainmail"),        # -- medium dither (armor rows + wolfie L5 shield)
    0x1e: ("texture", "wolf_ear_up"),            # CP437 up-triangle
    0x1f: ("texture", "wolf_ear_down"),          # CP437 down-triangle
    0x5c: ("weapon_or_diagonal", "slash_left"),  # \
    0x2f: ("weapon_or_diagonal", "slash_right"), # /
}

# Cyan foreground (0,255,255) on the last layer of an XP triggers swoosh /
# swing-effect rendering per sprite.cpp:343-348. NOT equipment.
SWOOSH_FG_RGB = (0, 255, 255)


def glyph_role_signature(g: int) -> tuple[str, str] | None:
    """Return (role_class, anatomy_label) if the glyph is role-discriminating.

    Returns None for empty/space cells and for silhouette half-blocks which are
    used universally for anti-aliased curved edges and carry no role signal.
    """
    if g == 0 or g == 32:
        return None
    if g in SILHOUETTE_HALFBLOCKS:
        return None
    return GLYPH_SIGNATURES.get(g)


def role_signature_summary(
    glyph_counts: dict[int, int],
    *,
    cyan_fg_count: int = 0,
) -> dict[str, int]:
    """Sum glyph occurrences by role_class for a layer.

    Pass `cyan_fg_count` for the number of cells in the layer whose fg RGB is
    (0,255,255); if non-zero on a layer's final layer index, the layer is
    a swoosh, not equipment.

    Returns counts keyed by role_class. Use the counts as the input to
    role-class heuristics:
      - shield_boss >= 10        -> shield (composite if eyes also present)
      - armor_chainmail >= 30    -> armor
      - helmet point (^) >=10 + low cell count -> helmet
      - crossbow_bolt + crossbow_stock         -> crossbow
      - cyan_fg_count > 0 (final layer)        -> swoosh
    """
    out: dict[str, int] = {}
    if cyan_fg_count > 0:
        out["swoosh_fg_cells"] = cyan_fg_count
    for glyph, n in glyph_counts.items():
        sig = glyph_role_signature(glyph)
        if sig is None:
            continue
        role_class, anatomy = sig
        out[anatomy] = out.get(anatomy, 0) + n
        out[role_class] = out.get(role_class, 0) + n
    return out


# ---------------------------------------------------------------------------
# Axis 2 — Cell analysis
# ---------------------------------------------------------------------------

def _analyze_cells(
    cells: list[tuple],
    *,
    layer0_key_rgbs: list[tuple[int, int, int] | None] | None = None,
) -> dict:
    """
    Aggregate glyph+colour statistics over a list of cells.
    Each cell: (glyph: int, fg: (r,g,b), bg: (r,g,b))

    Returns a stats dict with keys:
        total, transparent_count, transparent_ratio,
        color_counts (dict), dominant_color,
        glyph_counts (dict), dominant_glyph,
        has_skin, has_gold, has_metal
    """
    total = len(cells)
    if total == 0:
        return {
            "total": 0,
            "transparent_ratio": 1.0,
            "dominant_color": "transparent",
            "dominant_glyph": "empty",
            "has_skin": False,
            "has_gold": False,
            "has_metal": False,
            "bg_transparent_count": 0,
            "fg_transparent_count": 0,
            "layer0_key_match_count": 0,
            "visible_cell_count": 0,
        }

    if layer0_key_rgbs is not None and len(layer0_key_rgbs) != total:
        raise ValueError(
            f"_analyze_cells: expected {total} layer0_key_rgbs entries, got {len(layer0_key_rgbs)}"
        )

    transparent_count = 0
    bg_transparent_count = 0
    fg_transparent_count = 0
    layer0_key_match_count = 0
    visible_cell_count = 0
    color_counts: dict[str, int] = {}
    glyph_counts:  dict[str, int] = {}

    for idx, (glyph, fg, bg) in enumerate(cells):
        key_rgb = None if layer0_key_rgbs is None else layer0_key_rgbs[idx]
        flags = _engine_cell_transparency_flags(
            (glyph, fg, bg),
            layer0_key_rgb=key_rgb,
        )
        g_cat = _glyph_category(glyph)
        if flags["engine_bg_transparent"]:
            transparent_count += 1
            bg_transparent_count += 1
        else:
            bg_cat = _color_category(bg)
            color_counts[bg_cat] = color_counts.get(bg_cat, 0) + 2  # bg weight = 2
        if flags["engine_fg_transparent"]:
            fg_transparent_count += 1
        elif glyph not in (0, 32):
            fg_cat = _color_category(fg)
            color_counts[fg_cat] = color_counts.get(fg_cat, 0) + 1
        if flags["bg_matches_layer0_key"] or flags["fg_matches_layer0_key"]:
            layer0_key_match_count += 1
        if flags["engine_visible"]:
            visible_cell_count += 1
            glyph_counts[g_cat] = glyph_counts.get(g_cat, 0) + 1
        elif g_cat == "empty":
            glyph_counts[g_cat] = glyph_counts.get(g_cat, 0) + 1

    # Remove transparent from dominant-colour contest
    color_counts.pop("transparent", None)
    dominant_color = max(color_counts, key=lambda k: color_counts[k]) if color_counts else "transparent"
    dominant_glyph = max(glyph_counts, key=lambda k: glyph_counts[k]) if glyph_counts else "empty"

    return {
        "total": total,
        "transparent_count": transparent_count,
        "transparent_ratio": transparent_count / total,
        "color_counts": color_counts,
        "dominant_color": dominant_color,
        "glyph_counts": glyph_counts,
        "dominant_glyph": dominant_glyph,
        "has_skin":  color_counts.get("skin",  0) > 0,
        "has_gold":  color_counts.get("gold",  0) > 0,
        "has_metal": color_counts.get("metal", 0) > 0,
        "bg_transparent_count": bg_transparent_count,
        "fg_transparent_count": fg_transparent_count,
        "layer0_key_match_count": layer0_key_match_count,
        "visible_cell_count": visible_cell_count,
    }


# ---------------------------------------------------------------------------
# Axis 2 — Equipment and animation inference
# ---------------------------------------------------------------------------

def _infer_equipment(body_part: str, stats: dict) -> str:
    """Infer equipment slot state from semantic region + cell statistics."""
    tr = stats["transparent_ratio"]
    dc = stats["dominant_color"]
    dg = stats["dominant_glyph"]

    if tr > 0.85:
        return "empty_slot"

    if body_part in ("mouth", "eyes_nose", "face_center", "head"):
        if stats["has_gold"] and not stats["has_skin"]:
            return "helmet_gold"
        if stats["has_metal"] and not stats["has_skin"]:
            return "helmet_metal"
        if dc == "dark" and not stats["has_skin"]:
            return "helmet_dark"
        if stats["has_skin"]:
            return "bare_head"
        return "helmet_unknown"

    if body_part in ("hair", "head_top"):
        if tr > 0.85:
            return "empty_slot"
        if stats["has_gold"]:
            return "helmet_gold"
        if stats["has_metal"]:
            return "helmet_metal"
        if dc == "dark":
            return "hair_dark"
        return "hair_other"

    if body_part in ("torso", "pelvis", "seat_anchor"):
        if stats["has_gold"]:
            return "armor_gold"
        if stats["has_metal"]:
            return "armor_metal"
        if dc == "dark":
            return "armor_dark"
        if dc == "cloth_blue":
            return "shirt_blue"
        if stats["has_skin"]:
            return "bare_torso" if body_part == "torso" else "bare_pelvis"
        return "armor_unknown"

    if body_part in ("left_arm", "right_arm", "weapon_hand"):
        if tr > 0.5:
            return "no_weapon"
        if dg == "line":
            return "weapon_sword"
        if dg == "block":
            return "weapon_shield"
        return "weapon_other"

    if body_part in ("legs", "left_leg", "right_leg", "left_foot", "right_foot"):
        if dc == "dark":
            return "pants_dark"
        if stats["has_skin"]:
            return "bare_legs"
        return "pants_other"

    return "unknown_equipment"


def _infer_anim_state(body_part: str, stats: dict, sprite_type: str, frame_idx: int) -> str:
    """Infer animation state from frame index and cell statistics."""
    if sprite_type == "plydie":
        return "dying"

    if sprite_type == "attack":
        return "attacking"

    # player / wolfie / bigbee: idle = frame 0, rest = motion
    if frame_idx == 0:
        return "idle"

    if sprite_type == "bigbee":
        return "flying"

    # For the walk cycle, legs with offset transparent ratio indicate mid-stride
    if body_part in ("legs", "left_leg", "right_leg", "left_foot", "right_foot"):
        tr = stats["transparent_ratio"]
        if tr < 0.3:
            return "walking_mid_stride"
        return "walking"

    return "walking"


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def identify(
    cells: list[tuple],
    local_y: int,
    local_x: int,
    sprite_type: str = "player",
    frame_idx: int = 0,
    angle: int = 4,
    rect_w: int = 1,
    rect_h: int = 1,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
    region_name_hint: str | None = None,
    layer0_key_rgbs: list[tuple[int, int, int] | None] | None = None,
) -> dict:
    """
    Identify the semantic meaning of a rectangular region of XP cells.

    Parameters
    ----------
    cells       : list of (glyph, fg_rgb, bg_rgb) — all cells in selection,
                  in row-major order (top-left first)
    local_y     : top row of the rectangle in frame-local coordinates  (row first — matches get_rect_body_part)
    local_x     : left column of the rectangle in frame-local coordinates
    sprite_type : "player" | "attack" | "plydie" | "wolfie" | "bigbee" | ...
    frame_idx   : 0-based frame number within the animation strip
    angle       : 0=N, 1=NE, 2=E, 3=SE, 4=S, 5=SW, 6=W, 7=NW
    rect_w      : width of the selection (cols)
    rect_h      : height of the selection (rows)

    Returns
    -------
    dict with keys:
        body_part   — dominant semantic region
        body_group  — broad semantic group
        region_name — alias of body_part for explicit atlas lookup
        direction   — cardinal label (N/NE/E/SE/S/SW/W/NW)
        frame_role  — broad animation role used in semantic bits
        semantic_bits — normalized components used to form semantic_id
        semantic_id — canonical semantic token for the selected region
        equipment   — inferred equipment slot
        anim_state  — inferred animation state
        sprite_type — echo of input
        frame_idx   — echo of input
        angle       — echo of input
        confidence  — rough heuristic confidence 0.0–1.0
        stats       — raw cell statistics (from _analyze_cells)
    """
    x2 = local_x + rect_w - 1
    y2 = local_y + rect_h - 1
    body_part = region_name_hint or get_rect_body_part(
        local_y, local_x, y2, x2,
        angle=angle, sprite_type=sprite_type,
        frame_w=frame_w, frame_h=frame_h,
    )
    body_group = _get_body_group(body_part, frame_w=frame_w, frame_h=frame_h)
    direction = ANGLE_NAMES[angle % ANGLE_COUNT]
    frame_role = _get_frame_role(sprite_type, frame_idx)
    semantic_bits = _build_semantic_bits(body_part, sprite_type, angle, frame_idx, frame_w=frame_w, frame_h=frame_h)
    semantic_id = _build_semantic_id(body_part, sprite_type, angle, frame_w=frame_w, frame_h=frame_h)
    stats = _analyze_cells(cells, layer0_key_rgbs=layer0_key_rgbs)
    equipment  = _infer_equipment(body_part, stats)
    anim_state = _infer_anim_state(body_part, stats, sprite_type, frame_idx)

    # Confidence: penalise mostly-transparent or unknown body-part
    confidence = 1.0
    if stats["transparent_ratio"] > 0.7:
        confidence *= 0.4
    if body_part == "unknown":
        confidence *= 0.5
    if equipment.endswith("_unknown"):
        confidence *= 0.7

    return {
        "body_part":   body_part,
        "body_group":  body_group,
        "region_name": body_part,
        "direction":   direction,
        "frame_role":  frame_role,
        "semantic_bits": semantic_bits,
        "semantic_id": semantic_id,
        "equipment":   equipment,
        "anim_state":  anim_state,
        "sprite_type": sprite_type,
        "frame_idx":   frame_idx,
        "angle":       angle,
        "frame_w":     frame_w,
        "frame_h":     frame_h,
        "confidence":  round(confidence, 3),
        "stats":       stats,
    }


# ---------------------------------------------------------------------------
# Phase 0 builder — scan a full XP sheet
# ---------------------------------------------------------------------------

def _build_variant_region_id(
    region_name: str,
    sprite_type: str,
    angle: int,
    frame_idx: int,
    *,
    source_meta: dict[str, object],
    surface_key: str,
    proj: int = PROJ_LEFT,
    anim_index: int = 0,
    frame_w: int = FRAME_W,
    frame_h: int = FRAME_H,
) -> str:
    direction = _direction_word(angle)
    focus = _get_semantic_focus(region_name, frame_w=frame_w, frame_h=frame_h)
    return (
        f"{source_meta['semantic_namespace']}:"
        f"{source_meta['source_asset_stem']}:"
        f"{surface_key}:"
        f"{sprite_type}:"
        f"{direction}:"
        f"proj_{proj}:"
        f"anim_{anim_index}:"
        f"frame_{frame_idx}:"
        f"{focus}"
    )


def _build_from_loaded_xp(
    xp: object,
    xp_path: str,
    sprite_type: str,
    *,
    layer_mode: str = LAYER_MODE_BASE_VISUAL,
    raw_layer_index: int | None = None,
    include_stats: bool = True,
) -> dict:
    source_meta = _describe_source_asset(xp_path, sprite_type)
    meta = xp.get_metadata()
    layer, layer_meta = _resolve_semantic_surface(
        xp,
        xp_path=xp_path,
        layer_mode=layer_mode,
        raw_layer_index=raw_layer_index,
    )
    layer0 = xp.layers[0]

    angle_count = meta.get("angles", ANGLE_COUNT)
    anim_info = meta.get("anims", [1])

    # Authoritative frame count comes from the sheet's own metadata.
    # Cross-check against ACTION_FRAMES so a stale table entry is caught early
    # rather than silently producing wrong sheet_col offsets for every frame.
    sheet_total_frames = sum(anim_info) if anim_info else 1
    if sprite_type in ACTION_FRAMES:
        table_total = sum(ACTION_FRAMES[sprite_type].values())
        if table_total != sheet_total_frames:
            raise ValueError(
                f"build_from_xp: '{xp_path}' metadata reports {sheet_total_frames} "
                f"frames (anims={anim_info}), but ACTION_FRAMES['{sprite_type}'] "
                f"expects {table_total}. Update ACTION_FRAMES or pass the correct sprite_type."
            )
    total_frames = sheet_total_frames
    projs = int(meta.get("projs", PROJ_RIGHT + 1))
    layer_w = getattr(layer, "width", len(layer.data[0]) if getattr(layer, "data", None) else 0)
    layer_h = getattr(layer, "height", len(layer.data) if getattr(layer, "data", None) else 0)
    frame_w = layer_w // max(1, projs * total_frames)
    frame_h = layer_h // max(1, angle_count)
    surface_key = _build_surface_key(str(layer_meta["layer_mode"]), int(layer_meta["source_layer_index"]))

    result: dict = {
        "__meta__": {
            "semantic_namespace": source_meta["semantic_namespace"],
            "source_asset_name": source_meta["source_asset_name"],
            "source_asset_stem": source_meta["source_asset_stem"],
            "source_family": source_meta["source_family"],
            "presentation_kind": source_meta["presentation_kind"],
            "mount_state": source_meta["mount_state"],
            "mount_family": source_meta["mount_family"],
            "palette_variant": source_meta["palette_variant"],
            "variant_signature": source_meta["variant_signature"],
            "atlas_version": REGION_ATLAS_VERSION,
            "frame_w_cells": frame_w,
            "frame_h_cells": frame_h,
            "projs": projs,
            "anims": anim_info,
            "layer_mode": layer_meta["layer_mode"],
            "surface_key": surface_key,
            "source_layer_index": layer_meta["source_layer_index"],
            "merged_overlay_layers": layer_meta.get("merged_overlay_layers", []),
            "semantic_contract": SEMANTIC_REGION_CELL_CONTRACT,
            "legacy_proj_left_alias": True,
            "region_atlas": _export_region_atlas(frame_w=frame_w, frame_h=frame_h),
        }
    }
    result["variants"] = {}
    variant_frames = _iter_anim_frames([int(value) for value in anim_info])

    for angle in range(angle_count):
        result[angle] = {}
        result["variants"][angle] = {}
        for proj in range(projs):
            result["variants"][angle][proj] = {}
            for anim_index, frame_index_in_anim, flat_frame_idx in variant_frames:
                result["variants"][angle][proj].setdefault(anim_index, {})
                result["variants"][angle][proj][anim_index][frame_index_in_anim] = {}
                if proj == PROJ_LEFT:
                    result[angle][flat_frame_idx] = {}
                sheet_row, sheet_col = _get_frame_origin_from_variant(
                    angle,
                    anim_index,
                    frame_index_in_anim,
                    proj,
                    [int(value) for value in anim_info],
                    frame_w=frame_w,
                    frame_h=frame_h,
                )

                for entry in _resolved_region_atlas(frame_w=frame_w, frame_h=frame_h):
                    part_name = str(entry["name"])
                    r0 = int(entry["row_lo"])
                    r1 = int(entry["row_hi"])
                    c0 = int(entry["col_lo"])
                    c1 = int(entry["col_hi"])
                    cells = []
                    layer0_key_rgbs: list[tuple[int, int, int]] = []
                    visible_rows: set[int] = set()
                    visible_cols: set[int] = set()
                    visible_cells: list[dict[str, object]] = []
                    for ry in range(r0, r1 + 1):
                        for rx in range(c0, c1 + 1):
                            abs_row = sheet_row + ry
                            abs_col = sheet_col + rx
                            try:
                                cell = layer.data[abs_row][abs_col]
                            except IndexError:
                                continue
                            cells.append(cell)
                            layer0_key_rgb = tuple(layer0.data[abs_row][abs_col][2])
                            layer0_key_rgbs.append(layer0_key_rgb)
                            flags = _engine_cell_transparency_flags(cell, layer0_key_rgb=layer0_key_rgb)
                            if not flags["engine_visible"]:
                                continue
                            glyph, fg_rgb, bg_rgb = cell
                            visible_rows.add(ry)
                            visible_cols.add(rx)
                            visible_cells.append(
                                {
                                    "row": ry,
                                    "col": rx,
                                    "sheet_row": abs_row,
                                    "sheet_col": abs_col,
                                    "glyph_id": int(glyph),
                                    "glyph_char": _cp437_char(int(glyph)),
                                    "fg_rgb": list(tuple(fg_rgb)),
                                    "bg_rgb": list(tuple(bg_rgb)),
                                }
                            )

                    stats = _analyze_cells(cells, layer0_key_rgbs=layer0_key_rgbs)
                    equipment = _infer_equipment(part_name, stats)
                    anim_state = _infer_anim_state(part_name, stats, sprite_type, flat_frame_idx)
                    frame_role = _get_frame_role_for_variant(
                        sprite_type,
                        anim_index,
                        frame_index_in_anim,
                        flat_frame_idx,
                    )
                    region_payload = {
                        "body_group": _get_body_group(part_name, frame_w=frame_w, frame_h=frame_h),
                        "frame_role": frame_role,
                        "semantic_bits": _build_semantic_bits(part_name, sprite_type, angle, flat_frame_idx, frame_w=frame_w, frame_h=frame_h),
                        "semantic_id": _build_semantic_id(part_name, sprite_type, angle, frame_w=frame_w, frame_h=frame_h),
                        "variant_region_id": _build_variant_region_id(
                            part_name,
                            sprite_type,
                            angle,
                            frame_index_in_anim,
                            source_meta=source_meta,
                            surface_key=surface_key,
                            proj=proj,
                            anim_index=anim_index,
                            frame_w=frame_w,
                            frame_h=frame_h,
                        ),
                        "projection_index": proj,
                        "animation_index": anim_index,
                        "frame_index_in_animation": frame_index_in_anim,
                        "flat_frame_index": flat_frame_idx,
                        "cells": visible_cells,
                        "rows": sorted(visible_rows),
                        "cols": sorted(visible_cols),
                        "row_bounds": [min(visible_rows), max(visible_rows)] if visible_rows else None,
                        "col_bounds": [min(visible_cols), max(visible_cols)] if visible_cols else None,
                        "atlas_rows": [r0, r1],
                        "atlas_cols": [c0, c1],
                        "visible_cell_count": len(visible_cells),
                        "region_cell_count": len(cells),
                        "present": bool(visible_cells),
                        "equipment": equipment,
                        "anim_state": anim_state,
                    }
                    if include_stats:
                        region_payload["stats"] = stats
                    result["variants"][angle][proj][anim_index][frame_index_in_anim][part_name] = region_payload
                    if proj == PROJ_LEFT:
                        result[angle][flat_frame_idx][part_name] = region_payload

    return result


def build_from_xp(
    xp_path: str,
    sprite_type: str = "player",
    *,
    layer_mode: str = LAYER_MODE_BASE_VISUAL,
    raw_layer_index: int | None = None,
    include_stats: bool = True,
) -> dict:
    """
    Scan an entire XP sprite sheet and build a nested semantic dictionary.

    Returns
    -------
    dict keyed by (angle, frame_idx, region_name):
        { "stats": ..., "equipment": ..., "anim_state": ..., "semantic_id": ... }

    Also serialisable to JSON for offline lookup.
    """
    from ..xp_core import XPFile

    xp = XPFile(xp_path)
    return _build_from_loaded_xp(
        xp,
        xp_path,
        sprite_type,
        layer_mode=layer_mode,
        raw_layer_index=raw_layer_index,
        include_stats=include_stats,
    )


# ---------------------------------------------------------------------------
# Accepted corpus — legacy mounted semantic promotion owner deleted on 2026-05-01.
# ---------------------------------------------------------------------------

# BEGIN ACCEPTED_CORPUS_ROWS
ACCEPTED_CORPUS_ROWS: dict[str, dict] = {   'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=head_top|rows=0-1|cols=2-6': {   'agent_guess': {   'angle': 0,
                                                                                                              'anim_state': 'attacking',
                                                                                                              'body_group': 'face',
                                                                                                              'body_part': 'face_center',
                                                                                                              'confidence': 0.4,
                                                                                                              'direction': 'N',
                                                                                                              'equipment': 'empty_slot',
                                                                                                              'frame_h': 10,
                                                                                                              'frame_idx': 0,
                                                                                                              'frame_role': 'attack',
                                                                                                              'frame_w': 9,
                                                                                                              'region_name': 'face_center',
                                                                                                              'semantic_bits': [   'attack',
                                                                                                                                   'sprite',
                                                                                                                                   'face',
                                                                                                                                   'north',
                                                                                                                                   'attack'],
                                                                                                              'semantic_id': 'attack_sprite_face_north',
                                                                                                              'sprite_type': 'attack',
                                                                                                              'stats': {   'bg_transparent_count': 9,
                                                                                                                           'color_counts': {   'colored': 2,
                                                                                                                                               'dark': 1,
                                                                                                                                               'metal': 2},
                                                                                                                           'dominant_color': 'metal',
                                                                                                                           'dominant_glyph': 'empty',
                                                                                                                           'fg_transparent_count': 7,
                                                                                                                           'glyph_counts': {   'block': 3,
                                                                                                                                               'empty': 7},
                                                                                                                           'has_gold': False,
                                                                                                                           'has_metal': True,
                                                                                                                           'has_skin': False,
                                                                                                                           'layer0_key_match_count': 2,
                                                                                                                           'total': 10,
                                                                                                                           'transparent_count': 9,
                                                                                                                           'transparent_ratio': 0.9,
                                                                                                                           'visible_cell_count': 3}},
                                                                                           'guessed_at': None,
                                                                                           'review_key': 'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=head_top|rows=0-1|cols=2-6',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'attack-0001.xp'},
    'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=left_leg|rows=6-8|cols=1-3': {   'agent_guess': {   'angle': 0,
                                                                                                              'anim_state': 'attacking',
                                                                                                              'body_group': 'leg',
                                                                                                              'body_part': 'left_leg',
                                                                                                              'confidence': 0.4,
                                                                                                              'direction': 'N',
                                                                                                              'equipment': 'empty_slot',
                                                                                                              'frame_h': 10,
                                                                                                              'frame_idx': 0,
                                                                                                              'frame_role': 'attack',
                                                                                                              'frame_w': 9,
                                                                                                              'region_name': 'left_leg',
                                                                                                              'semantic_bits': [   'attack',
                                                                                                                                   'sprite',
                                                                                                                                   'left_leg',
                                                                                                                                   'north',
                                                                                                                                   'attack'],
                                                                                                              'semantic_id': 'attack_sprite_left_leg_north',
                                                                                                              'sprite_type': 'attack',
                                                                                                              'stats': {   'bg_transparent_count': 8,
                                                                                                                           'color_counts': {   'cloth_blue': 3,
                                                                                                                                               'colored': 1,
                                                                                                                                               'dark': 1},
                                                                                                                           'dominant_color': 'cloth_blue',
                                                                                                                           'dominant_glyph': 'empty',
                                                                                                                           'fg_transparent_count': 6,
                                                                                                                           'glyph_counts': {   'block': 3,
                                                                                                                                               'empty': 6},
                                                                                                                           'has_gold': False,
                                                                                                                           'has_metal': False,
                                                                                                                           'has_skin': False,
                                                                                                                           'layer0_key_match_count': 2,
                                                                                                                           'total': 9,
                                                                                                                           'transparent_count': 8,
                                                                                                                           'transparent_ratio': 0.8888888888888888,
                                                                                                                           'visible_cell_count': 3}},
                                                                                           'guessed_at': None,
                                                                                           'review_key': 'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=left_leg|rows=6-8|cols=1-3',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'attack-0001.xp'},
    'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=legs|rows=6-9|cols=0-8': {   'agent_guess': {   'angle': 0,
                                                                                                          'anim_state': 'attacking',
                                                                                                          'body_group': 'leg',
                                                                                                          'body_part': 'left_leg',
                                                                                                          'confidence': 0.4,
                                                                                                          'direction': 'N',
                                                                                                          'equipment': 'empty_slot',
                                                                                                          'frame_h': 10,
                                                                                                          'frame_idx': 0,
                                                                                                          'frame_role': 'attack',
                                                                                                          'frame_w': 9,
                                                                                                          'region_name': 'left_leg',
                                                                                                          'semantic_bits': [   'attack',
                                                                                                                               'sprite',
                                                                                                                               'left_leg',
                                                                                                                               'north',
                                                                                                                               'attack'],
                                                                                                          'semantic_id': 'attack_sprite_left_leg_north',
                                                                                                          'sprite_type': 'attack',
                                                                                                          'stats': {   'bg_transparent_count': 32,
                                                                                                                       'color_counts': {   'cloth_blue': 10,
                                                                                                                                           'colored': 2,
                                                                                                                                           'dark': 3},
                                                                                                                       'dominant_color': 'cloth_blue',
                                                                                                                       'dominant_glyph': 'empty',
                                                                                                                       'fg_transparent_count': 28,
                                                                                                                       'glyph_counts': {   'block': 7,
                                                                                                                                           'empty': 29},
                                                                                                                       'has_gold': False,
                                                                                                                       'has_metal': False,
                                                                                                                       'has_skin': False,
                                                                                                                       'layer0_key_match_count': 4,
                                                                                                                       'total': 36,
                                                                                                                       'transparent_count': 32,
                                                                                                                       'transparent_ratio': 0.8888888888888888,
                                                                                                                       'visible_cell_count': 8}},
                                                                                       'guessed_at': None,
                                                                                       'review_key': 'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=legs|rows=6-9|cols=0-8',
                                                                                       'review_status': 'yes',
                                                                                       'source_asset': 'attack-0001.xp'},
    'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=right_leg|rows=6-8|cols=5-7': {   'agent_guess': {   'angle': 0,
                                                                                                               'anim_state': 'attacking',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 0.4,
                                                                                                               'direction': 'N',
                                                                                                               'equipment': 'pants_other',
                                                                                                               'frame_h': 10,
                                                                                                               'frame_idx': 0,
                                                                                                               'frame_role': 'attack',
                                                                                                               'frame_w': 9,
                                                                                                               'region_name': 'right_leg',
                                                                                                               'semantic_bits': [   'attack',
                                                                                                                                    'sprite',
                                                                                                                                    'right_leg',
                                                                                                                                    'north',
                                                                                                                                    'attack'],
                                                                                                               'semantic_id': 'attack_sprite_right_leg_north',
                                                                                                               'sprite_type': 'attack',
                                                                                                               'stats': {   'bg_transparent_count': 7,
                                                                                                                            'color_counts': {   'cloth_blue': 4,
                                                                                                                                                'colored': 1,
                                                                                                                                                'dark': 2},
                                                                                                                            'dominant_color': 'cloth_blue',
                                                                                                                            'dominant_glyph': 'empty',
                                                                                                                            'fg_transparent_count': 6,
                                                                                                                            'glyph_counts': {   'block': 3,
                                                                                                                                                'empty': 6},
                                                                                                                            'has_gold': False,
                                                                                                                            'has_metal': False,
                                                                                                                            'has_skin': False,
                                                                                                                            'layer0_key_match_count': 1,
                                                                                                                            'total': 9,
                                                                                                                            'transparent_count': 7,
                                                                                                                            'transparent_ratio': 0.7777777777777778,
                                                                                                                            'visible_cell_count': 3}},
                                                                                            'guessed_at': None,
                                                                                            'review_key': 'attack-0001.xp|layer=2|anim=0|frame=0|angle=0|region=right_leg|rows=6-8|cols=5-7',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'attack-0001.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=0|region=left_leg|rows=5-7|cols=1-2': {   'agent_guess': {   'angle_observation': 'front-facing '
                                                                                                                                   'idle; '
                                                                                                                                   'symmetric '
                                                                                                                                   'face '
                                                                                                                                   'with '
                                                                                                                                   'quoted '
                                                                                                                                   'eyes '
                                                                                                                                   'and '
                                                                                                                                   'centered '
                                                                                                                                   'mouth '
                                                                                                                                   'mark',
                                                                                                              'body_group': 'leg',
                                                                                                              'body_part': 'left_leg',
                                                                                                              'confidence': 'high',
                                                                                                              'guessed_region_name': 'left_leg',
                                                                                                              'inspector_basis': 'manual '
                                                                                                                                 'visual '
                                                                                                                                 'inspection '
                                                                                                                                 'of '
                                                                                                                                 'raw '
                                                                                                                                 'layer-2 '
                                                                                                                                 'glyphs/cells '
                                                                                                                                 'plus '
                                                                                                                                 'exact '
                                                                                                                                 'payload '
                                                                                                                                 'context',
                                                                                                              'interpretation': 'screen-left '
                                                                                                                                'leg '
                                                                                                                                'segment '
                                                                                                                                'for '
                                                                                                                                'this '
                                                                                                                                'facing',
                                                                                                              'raw_preview_rows': [   '.▐',
                                                                                                                                      '.▐',
                                                                                                                                      '.▀'],
                                                                                                              'semantic_id': 'player_sprite_left_leg_north',
                                                                                                              'uncertainty': None,
                                                                                                              'visible_cell_count': 3,
                                                                                                              'visible_positions': [   [   5,
                                                                                                                                           2],
                                                                                                                                       [   6,
                                                                                                                                           2],
                                                                                                                                       [   7,
                                                                                                                                           2]]},
                                                                                           'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                           'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=0|region=left_leg|rows=5-7|cols=1-2',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=0|region=right_leg|rows=5-7|cols=4-5': {   'agent_guess': {   'angle_observation': 'front-facing '
                                                                                                                                    'idle; '
                                                                                                                                    'symmetric '
                                                                                                                                    'face '
                                                                                                                                    'with '
                                                                                                                                    'quoted '
                                                                                                                                    'eyes '
                                                                                                                                    'and '
                                                                                                                                    'centered '
                                                                                                                                    'mouth '
                                                                                                                                    'mark',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 'high',
                                                                                                               'guessed_region_name': 'right_leg',
                                                                                                               'inspector_basis': 'manual '
                                                                                                                                  'visual '
                                                                                                                                  'inspection '
                                                                                                                                  'of '
                                                                                                                                  'raw '
                                                                                                                                  'layer-2 '
                                                                                                                                  'glyphs/cells '
                                                                                                                                  'plus '
                                                                                                                                  'exact '
                                                                                                                                  'payload '
                                                                                                                                  'context',
                                                                                                               'interpretation': 'screen-right '
                                                                                                                                 'leg '
                                                                                                                                 'segment '
                                                                                                                                 'for '
                                                                                                                                 'this '
                                                                                                                                 'facing',
                                                                                                               'raw_preview_rows': [   '▌.',
                                                                                                                                       '▐.',
                                                                                                                                       '▀.'],
                                                                                                               'semantic_id': 'player_sprite_right_leg_north',
                                                                                                               'uncertainty': None,
                                                                                                               'visible_cell_count': 3,
                                                                                                               'visible_positions': [   [   5,
                                                                                                                                            4],
                                                                                                                                        [   6,
                                                                                                                                            4],
                                                                                                                                        [   7,
                                                                                                                                            4]]},
                                                                                            'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                            'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=0|region=right_leg|rows=5-7|cols=4-5',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=1|region=right_leg|rows=5-7|cols=4-5': {   'agent_guess': {   'angle_observation': 'front-left '
                                                                                                                                    'three-quarter '
                                                                                                                                    'idle; '
                                                                                                                                    'right '
                                                                                                                                    'side '
                                                                                                                                    'starts '
                                                                                                                                    'to '
                                                                                                                                    'compress',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 'high',
                                                                                                               'guessed_region_name': 'right_leg',
                                                                                                               'inspector_basis': 'manual '
                                                                                                                                  'visual '
                                                                                                                                  'inspection '
                                                                                                                                  'of '
                                                                                                                                  'raw '
                                                                                                                                  'layer-2 '
                                                                                                                                  'glyphs/cells '
                                                                                                                                  'plus '
                                                                                                                                  'exact '
                                                                                                                                  'payload '
                                                                                                                                  'context',
                                                                                                               'interpretation': 'screen-right '
                                                                                                                                 'leg '
                                                                                                                                 'segment '
                                                                                                                                 'for '
                                                                                                                                 'this '
                                                                                                                                 'facing',
                                                                                                               'raw_preview_rows': [   '▌.',
                                                                                                                                       '▐.',
                                                                                                                                       '▀.'],
                                                                                                               'semantic_id': 'player_sprite_right_leg_northwest',
                                                                                                               'uncertainty': None,
                                                                                                               'visible_cell_count': 3,
                                                                                                               'visible_positions': [   [   5,
                                                                                                                                            4],
                                                                                                                                        [   6,
                                                                                                                                            4],
                                                                                                                                        [   7,
                                                                                                                                            4]]},
                                                                                            'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                            'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=1|region=right_leg|rows=5-7|cols=4-5',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=3|region=left_leg|rows=5-7|cols=1-2': {   'agent_guess': {   'angle_observation': 'rear-left '
                                                                                                                                   'three-quarter '
                                                                                                                                   'idle; '
                                                                                                                                   'face '
                                                                                                                                   'region '
                                                                                                                                   'mostly '
                                                                                                                                   'reads '
                                                                                                                                   'as '
                                                                                                                                   'back-of-head '
                                                                                                                                   'plus '
                                                                                                                                   'rear '
                                                                                                                                   'contour',
                                                                                                              'body_group': 'leg',
                                                                                                              'body_part': 'left_leg',
                                                                                                              'confidence': 'high',
                                                                                                              'guessed_region_name': 'left_leg',
                                                                                                              'inspector_basis': 'manual '
                                                                                                                                 'visual '
                                                                                                                                 'inspection '
                                                                                                                                 'of '
                                                                                                                                 'raw '
                                                                                                                                 'layer-2 '
                                                                                                                                 'glyphs/cells '
                                                                                                                                 'plus '
                                                                                                                                 'exact '
                                                                                                                                 'payload '
                                                                                                                                 'context',
                                                                                                              'interpretation': 'screen-left '
                                                                                                                                'leg '
                                                                                                                                'segment '
                                                                                                                                'for '
                                                                                                                                'this '
                                                                                                                                'facing',
                                                                                                              'raw_preview_rows': [   '.▐',
                                                                                                                                      '.▐',
                                                                                                                                      '.▀'],
                                                                                                              'semantic_id': 'player_sprite_left_leg_southwest',
                                                                                                              'uncertainty': None,
                                                                                                              'visible_cell_count': 3,
                                                                                                              'visible_positions': [   [   5,
                                                                                                                                           2],
                                                                                                                                       [   6,
                                                                                                                                           2],
                                                                                                                                       [   7,
                                                                                                                                           2]]},
                                                                                           'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                           'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=3|region=left_leg|rows=5-7|cols=1-2',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=3|region=right_leg|rows=5-7|cols=4-5': {   'agent_guess': {   'angle_observation': 'rear-left '
                                                                                                                                    'three-quarter '
                                                                                                                                    'idle; '
                                                                                                                                    'face '
                                                                                                                                    'region '
                                                                                                                                    'mostly '
                                                                                                                                    'reads '
                                                                                                                                    'as '
                                                                                                                                    'back-of-head '
                                                                                                                                    'plus '
                                                                                                                                    'rear '
                                                                                                                                    'contour',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 'high',
                                                                                                               'guessed_region_name': 'right_leg',
                                                                                                               'inspector_basis': 'manual '
                                                                                                                                  'visual '
                                                                                                                                  'inspection '
                                                                                                                                  'of '
                                                                                                                                  'raw '
                                                                                                                                  'layer-2 '
                                                                                                                                  'glyphs/cells '
                                                                                                                                  'plus '
                                                                                                                                  'exact '
                                                                                                                                  'payload '
                                                                                                                                  'context',
                                                                                                               'interpretation': 'screen-right '
                                                                                                                                 'leg '
                                                                                                                                 'segment '
                                                                                                                                 'for '
                                                                                                                                 'this '
                                                                                                                                 'facing',
                                                                                                               'raw_preview_rows': [   '▌.',
                                                                                                                                       '▐.',
                                                                                                                                       '▀.'],
                                                                                                               'semantic_id': 'player_sprite_right_leg_southwest',
                                                                                                               'uncertainty': None,
                                                                                                               'visible_cell_count': 3,
                                                                                                               'visible_positions': [   [   5,
                                                                                                                                            4],
                                                                                                                                        [   6,
                                                                                                                                            4],
                                                                                                                                        [   7,
                                                                                                                                            4]]},
                                                                                            'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                            'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=3|region=right_leg|rows=5-7|cols=4-5',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=4|region=left_leg|rows=5-7|cols=1-2': {   'agent_guess': {   'angle_observation': 'back-facing '
                                                                                                                                   'idle; '
                                                                                                                                   'face '
                                                                                                                                   'rows '
                                                                                                                                   'actually '
                                                                                                                                   'read '
                                                                                                                                   'as '
                                                                                                                                   'rear '
                                                                                                                                   'head '
                                                                                                                                   '/ '
                                                                                                                                   'helmet '
                                                                                                                                   'back '
                                                                                                                                   'rather '
                                                                                                                                   'than '
                                                                                                                                   'facial '
                                                                                                                                   'features',
                                                                                                              'body_group': 'leg',
                                                                                                              'body_part': 'left_leg',
                                                                                                              'confidence': 'high',
                                                                                                              'guessed_region_name': 'left_leg',
                                                                                                              'inspector_basis': 'manual '
                                                                                                                                 'visual '
                                                                                                                                 'inspection '
                                                                                                                                 'of '
                                                                                                                                 'raw '
                                                                                                                                 'layer-2 '
                                                                                                                                 'glyphs/cells '
                                                                                                                                 'plus '
                                                                                                                                 'exact '
                                                                                                                                 'payload '
                                                                                                                                 'context',
                                                                                                              'interpretation': 'screen-left '
                                                                                                                                'leg '
                                                                                                                                'segment '
                                                                                                                                'for '
                                                                                                                                'this '
                                                                                                                                'facing',
                                                                                                              'raw_preview_rows': [   '.▐',
                                                                                                                                      '.▐',
                                                                                                                                      '.▀'],
                                                                                                              'semantic_id': 'player_sprite_left_leg_south',
                                                                                                              'uncertainty': None,
                                                                                                              'visible_cell_count': 3,
                                                                                                              'visible_positions': [   [   5,
                                                                                                                                           2],
                                                                                                                                       [   6,
                                                                                                                                           2],
                                                                                                                                       [   7,
                                                                                                                                           2]]},
                                                                                           'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                           'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=4|region=left_leg|rows=5-7|cols=1-2',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=4|region=right_leg|rows=5-7|cols=4-5': {   'agent_guess': {   'angle_observation': 'back-facing '
                                                                                                                                    'idle; '
                                                                                                                                    'face '
                                                                                                                                    'rows '
                                                                                                                                    'actually '
                                                                                                                                    'read '
                                                                                                                                    'as '
                                                                                                                                    'rear '
                                                                                                                                    'head '
                                                                                                                                    '/ '
                                                                                                                                    'helmet '
                                                                                                                                    'back '
                                                                                                                                    'rather '
                                                                                                                                    'than '
                                                                                                                                    'facial '
                                                                                                                                    'features',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 'high',
                                                                                                               'guessed_region_name': 'right_leg',
                                                                                                               'inspector_basis': 'manual '
                                                                                                                                  'visual '
                                                                                                                                  'inspection '
                                                                                                                                  'of '
                                                                                                                                  'raw '
                                                                                                                                  'layer-2 '
                                                                                                                                  'glyphs/cells '
                                                                                                                                  'plus '
                                                                                                                                  'exact '
                                                                                                                                  'payload '
                                                                                                                                  'context',
                                                                                                               'interpretation': 'screen-right '
                                                                                                                                 'leg '
                                                                                                                                 'segment '
                                                                                                                                 'for '
                                                                                                                                 'this '
                                                                                                                                 'facing',
                                                                                                               'raw_preview_rows': [   '▌.',
                                                                                                                                       '▐.',
                                                                                                                                       '▀.'],
                                                                                                               'semantic_id': 'player_sprite_right_leg_south',
                                                                                                               'uncertainty': None,
                                                                                                               'visible_cell_count': 3,
                                                                                                               'visible_positions': [   [   5,
                                                                                                                                            4],
                                                                                                                                        [   6,
                                                                                                                                            4],
                                                                                                                                        [   7,
                                                                                                                                            4]]},
                                                                                            'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                            'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=4|region=right_leg|rows=5-7|cols=4-5',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=5|region=left_leg|rows=5-7|cols=1-2': {   'agent_guess': {   'angle_observation': 'rear-right '
                                                                                                                                   'three-quarter '
                                                                                                                                   'idle; '
                                                                                                                                   'mirrored '
                                                                                                                                   'back/side '
                                                                                                                                   'contour',
                                                                                                              'body_group': 'leg',
                                                                                                              'body_part': 'left_leg',
                                                                                                              'confidence': 'high',
                                                                                                              'guessed_region_name': 'left_leg',
                                                                                                              'inspector_basis': 'manual '
                                                                                                                                 'visual '
                                                                                                                                 'inspection '
                                                                                                                                 'of '
                                                                                                                                 'raw '
                                                                                                                                 'layer-2 '
                                                                                                                                 'glyphs/cells '
                                                                                                                                 'plus '
                                                                                                                                 'exact '
                                                                                                                                 'payload '
                                                                                                                                 'context',
                                                                                                              'interpretation': 'screen-left '
                                                                                                                                'leg '
                                                                                                                                'segment '
                                                                                                                                'for '
                                                                                                                                'this '
                                                                                                                                'facing',
                                                                                                              'raw_preview_rows': [   '.▐',
                                                                                                                                      '.▐',
                                                                                                                                      '.▀'],
                                                                                                              'semantic_id': 'player_sprite_left_leg_southeast',
                                                                                                              'uncertainty': None,
                                                                                                              'visible_cell_count': 3,
                                                                                                              'visible_positions': [   [   5,
                                                                                                                                           2],
                                                                                                                                       [   6,
                                                                                                                                           2],
                                                                                                                                       [   7,
                                                                                                                                           2]]},
                                                                                           'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                           'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=5|region=left_leg|rows=5-7|cols=1-2',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=5|region=right_leg|rows=5-7|cols=4-5': {   'agent_guess': {   'angle_observation': 'rear-right '
                                                                                                                                    'three-quarter '
                                                                                                                                    'idle; '
                                                                                                                                    'mirrored '
                                                                                                                                    'back/side '
                                                                                                                                    'contour',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 'high',
                                                                                                               'guessed_region_name': 'right_leg',
                                                                                                               'inspector_basis': 'manual '
                                                                                                                                  'visual '
                                                                                                                                  'inspection '
                                                                                                                                  'of '
                                                                                                                                  'raw '
                                                                                                                                  'layer-2 '
                                                                                                                                  'glyphs/cells '
                                                                                                                                  'plus '
                                                                                                                                  'exact '
                                                                                                                                  'payload '
                                                                                                                                  'context',
                                                                                                               'interpretation': 'screen-right '
                                                                                                                                 'leg '
                                                                                                                                 'segment '
                                                                                                                                 'for '
                                                                                                                                 'this '
                                                                                                                                 'facing',
                                                                                                               'raw_preview_rows': [   '▌.',
                                                                                                                                       '▐.',
                                                                                                                                       '▀.'],
                                                                                                               'semantic_id': 'player_sprite_right_leg_southeast',
                                                                                                               'uncertainty': None,
                                                                                                               'visible_cell_count': 3,
                                                                                                               'visible_positions': [   [   5,
                                                                                                                                            4],
                                                                                                                                        [   6,
                                                                                                                                            4],
                                                                                                                                        [   7,
                                                                                                                                            4]]},
                                                                                            'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                            'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=5|region=right_leg|rows=5-7|cols=4-5',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=7|region=left_leg|rows=5-7|cols=1-2': {   'agent_guess': {   'angle_observation': 'front-right '
                                                                                                                                   'three-quarter '
                                                                                                                                   'idle; '
                                                                                                                                   'left '
                                                                                                                                   'side '
                                                                                                                                   'starts '
                                                                                                                                   'to '
                                                                                                                                   'compress',
                                                                                                              'body_group': 'leg',
                                                                                                              'body_part': 'left_leg',
                                                                                                              'confidence': 'high',
                                                                                                              'guessed_region_name': 'left_leg',
                                                                                                              'inspector_basis': 'manual '
                                                                                                                                 'visual '
                                                                                                                                 'inspection '
                                                                                                                                 'of '
                                                                                                                                 'raw '
                                                                                                                                 'layer-2 '
                                                                                                                                 'glyphs/cells '
                                                                                                                                 'plus '
                                                                                                                                 'exact '
                                                                                                                                 'payload '
                                                                                                                                 'context',
                                                                                                              'interpretation': 'screen-left '
                                                                                                                                'leg '
                                                                                                                                'segment '
                                                                                                                                'for '
                                                                                                                                'this '
                                                                                                                                'facing',
                                                                                                              'raw_preview_rows': [   '.▐',
                                                                                                                                      '.▐',
                                                                                                                                      '.▀'],
                                                                                                              'semantic_id': 'player_sprite_left_leg_northeast',
                                                                                                              'uncertainty': None,
                                                                                                              'visible_cell_count': 3,
                                                                                                              'visible_positions': [   [   5,
                                                                                                                                           2],
                                                                                                                                       [   6,
                                                                                                                                           2],
                                                                                                                                       [   7,
                                                                                                                                           2]]},
                                                                                           'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                           'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=7|region=left_leg|rows=5-7|cols=1-2',
                                                                                           'review_status': 'yes',
                                                                                           'source_asset': 'player-0000.xp'},
    'player-0000.xp|layer=2|anim=0|frame=0|angle=7|region=right_leg|rows=5-7|cols=4-5': {   'agent_guess': {   'angle_observation': 'front-right '
                                                                                                                                    'three-quarter '
                                                                                                                                    'idle; '
                                                                                                                                    'left '
                                                                                                                                    'side '
                                                                                                                                    'starts '
                                                                                                                                    'to '
                                                                                                                                    'compress',
                                                                                                               'body_group': 'leg',
                                                                                                               'body_part': 'right_leg',
                                                                                                               'confidence': 'high',
                                                                                                               'guessed_region_name': 'right_leg',
                                                                                                               'inspector_basis': 'manual '
                                                                                                                                  'visual '
                                                                                                                                  'inspection '
                                                                                                                                  'of '
                                                                                                                                  'raw '
                                                                                                                                  'layer-2 '
                                                                                                                                  'glyphs/cells '
                                                                                                                                  'plus '
                                                                                                                                  'exact '
                                                                                                                                  'payload '
                                                                                                                                  'context',
                                                                                                               'interpretation': 'screen-right '
                                                                                                                                 'leg '
                                                                                                                                 'segment '
                                                                                                                                 'for '
                                                                                                                                 'this '
                                                                                                                                 'facing',
                                                                                                               'raw_preview_rows': [   '▌.',
                                                                                                                                       '▐.',
                                                                                                                                       '▀.'],
                                                                                                               'semantic_id': 'player_sprite_right_leg_northeast',
                                                                                                               'uncertainty': None,
                                                                                                               'visible_cell_count': 3,
                                                                                                               'visible_positions': [   [   5,
                                                                                                                                            4],
                                                                                                                                        [   6,
                                                                                                                                            4],
                                                                                                                                        [   7,
                                                                                                                                            4]]},
                                                                                            'guessed_at': '2026-04-28T22:04:31-0400',
                                                                                            'review_key': 'player-0000.xp|layer=2|anim=0|frame=0|angle=7|region=right_leg|rows=5-7|cols=4-5',
                                                                                            'review_status': 'yes',
                                                                                            'source_asset': 'player-0000.xp'}}
# END ACCEPTED_CORPUS_ROWS


def build_asset_semantic_dict(
    xp_path: str,
    sprite_type: str = "player",
    *,
    include_stats: bool = False,
    include_merged_visual: bool = True,
    include_raw_layers: bool = True,
) -> dict:
    """Build one source-asset entry with explicit per-surface semantic data."""
    from ..xp_core import XPFile

    xp = XPFile(xp_path)
    source_meta = _describe_source_asset(xp_path, sprite_type)
    surfaces: dict[str, dict] = {}

    surface_requests: list[tuple[str, int | None]] = [(LAYER_MODE_BASE_VISUAL, None)]
    if include_merged_visual and len(xp.layers) > 3:
        surface_requests.append((LAYER_MODE_MERGED_VISUAL, None))
    if include_raw_layers:
        for layer_index in range(2, len(xp.layers)):
            surface_requests.append((LAYER_MODE_RAW_LAYER, layer_index))

    for layer_mode, raw_layer_index in surface_requests:
        surface_payload = _build_from_loaded_xp(
            xp,
            xp_path,
            sprite_type,
            layer_mode=layer_mode,
            raw_layer_index=raw_layer_index,
            include_stats=include_stats,
        )
        surfaces[surface_payload["__meta__"]["surface_key"]] = surface_payload

    asset_meta = dict(source_meta)
    asset_meta["layer_count"] = len(xp.layers)
    asset_meta["surface_keys"] = list(surfaces.keys())
    asset_meta["raw_layer_indices"] = list(range(2, len(xp.layers)))
    return {
        "__meta__": asset_meta,
        "surfaces": surfaces,
    }


# ---------------------------------------------------------------------------
# Per-angle propagation and overlay mask derivation (FL-2897)
# ---------------------------------------------------------------------------

def rgb_cell_key(glyph: int, fg_rgb: tuple, bg_rgb: tuple) -> tuple:
    """
    RGB-based cell signature for propagation tracking.

    This uses RGB tuples as semantic_dict.py sees them. The old
    generate_presentation_overlays visual-key helper is tombstoned with the
    standalone actor-slot overlay generator; do not route new actor item
    authoring through that deleted owner.
    """
    return (int(glyph), tuple(fg_rgb), tuple(bg_rgb))


def propagate_from_anchors(
    xp_path: str,
    anchor_data: dict,
    sprite_type: str = "player",
    *,
    visual_layer_index: int = 2,
) -> dict:
    """
    Propagate body-part labels from anchor frames to non-anchor frames
    at the same angle using RGB-based glyph+color signature matching.

    Parameters
    ----------
    xp_path      : path to the XP sprite sheet to propagate labels onto
    anchor_data  : loaded anchor JSON (the dict from json.load, not the path)
    sprite_type  : family name
    visual_layer_index : which XP layer to read cells from (default 2)

    Returns
    -------
    dict matching pipeline-v3 semantic map JSON format with propagated
    regions, confidence scores, and frame metadata.
    """
    from ..xp_core import XPFile

    xp = XPFile(xp_path)
    anchor_fw = anchor_data.get("frame_w", FRAME_W)
    anchor_fh = anchor_data.get("frame_h", FRAME_H)

    if visual_layer_index >= len(xp.layers):
        raise ValueError(
            f"propagate_from_anchors: layer {visual_layer_index} out of range "
            f"for '{xp_path}' ({len(xp.layers)} layers)"
        )

    layer = xp.layers[visual_layer_index]
    sheet_rows = len(layer.data)
    sheet_cols = len(layer.data[0]) if sheet_rows > 0 else 0

    # Verify frame dimensions match
    if sheet_cols == 0 or sheet_rows == 0:
        return {"frames": {}, "__meta__": {"error": "empty sheet"}}

    source_meta = _describe_source_asset(xp_path, sprite_type)
    # Derive anim_info from ACTION_FRAMES — _describe_source_asset() does not
    # return it, and the fallback [1] would only process 1 frame per angle.
    action_frames = ACTION_FRAMES.get(sprite_type, {"idle": 1})
    anim_info = list(action_frames.values())
    total_frames = sum(anim_info)

    # Build per-angle anchor signatures: {angle -> {region_name -> set of rgb_cell_key}}
    anchor_sigs: dict[int, dict[str, set[tuple]]] = {}
    anchor_positions: dict[int, dict[str, list[tuple[int, int]]]] = {}

    for frame_key, frame_data in anchor_data.get("frames", {}).items():
        angle = frame_data.get("angle")
        if angle is None:
            try:
                angle = int(frame_key)
            except (ValueError, TypeError):
                continue
        if not (0 <= angle < ANGLE_COUNT):
            continue

        angle_sigs: dict[str, set[tuple]] = {}
        angle_pos: dict[str, list[tuple[int, int]]] = {}

        for region in frame_data.get("regions", []):
            rname = region.get("name", "unknown")
            sigs: set[tuple] = set()
            positions: list[tuple[int, int]] = []

            for cell in region.get("semantic_cells", []):
                cx, cy = cell.get("x", -1), cell.get("y", -1)
                glyph = cell.get("glyph", 0)
                fg = cell.get("fg", "#000000")
                bg = cell.get("bg", "#000000")
                fg_rgb = _hex_to_rgb(fg)
                bg_rgb = _hex_to_rgb(bg)
                sigs.add(rgb_cell_key(glyph, fg_rgb, bg_rgb))
                positions.append((cy, cx))

            # Also use bbox if semantic_cells is sparse
            if not sigs and len(region.get("bbox", [])) == 4:
                x0, y0, x1, y1 = region["bbox"]
                for by in range(y0, y1 + 1):
                    for bx in range(x0, x1 + 1):
                        positions.append((by, bx))

            angle_sigs[rname] = sigs
            angle_pos[rname] = positions

        anchor_sigs[angle] = angle_sigs
        anchor_positions[angle] = angle_pos

    # Pre-build slot_affinity index: {(angle, region_name) -> slot_affinity}
    slot_aff_index: dict[tuple[int, str], str | None] = {}
    for frame_key, frame_data in anchor_data.get("frames", {}).items():
        fa = frame_data.get("angle")
        if fa is None:
            try:
                fa = int(frame_key)
            except (ValueError, TypeError):
                continue
        for region in frame_data.get("regions", []):
            rname = region.get("name", "unknown")
            sa = region.get("slot_affinity")
            if sa is not None:
                slot_aff_index[(fa, rname)] = sa

    # Track which angles were actually propagated (vs ground truth anchors)
    propagated_angle_set: set[int] = set()

    # Propagate to each non-anchor frame
    result_frames: dict[str, dict] = {}
    flat_idx = 0

    for proj in range(source_meta.get("projs", 2)):
        for angle in range(ANGLE_COUNT):
            if angle not in anchor_sigs:
                flat_idx += total_frames
                continue

            propagated_angle_set.add(angle)
            asigs = anchor_sigs[angle]
            apos = anchor_positions[angle]

            for anim_index, anim_length in enumerate(anim_info):
                for frame_in_anim in range(anim_length):
                    # Get frame origin on the sheet
                    origin_row, origin_col = _get_frame_origin_from_variant(
                        angle, anim_index, frame_in_anim, proj,
                        anim_info, frame_w=anchor_fw, frame_h=anchor_fh,
                    )

                    # Read cells from the XP layer
                    frame_cells: dict[tuple[int, int], tuple] = {}
                    for ly in range(anchor_fh):
                        for lx in range(anchor_fw):
                            sy, sx = origin_row + ly, origin_col + lx
                            if sy < sheet_rows and sx < sheet_cols:
                                cell = layer.data[sy][sx]
                                glyph, fg, bg = cell[0], cell[1], cell[2]
                                frame_cells[(ly, lx)] = (glyph, tuple(fg), tuple(bg))

                    # Match each cell against anchor signatures
                    cell_labels: dict[tuple[int, int], str] = {}
                    cell_confidences: dict[str, list[float]] = {}

                    for (ly, lx), (glyph, fg, bg) in frame_cells.items():
                        # Skip transparent cells
                        if glyph == 32 and fg == (255, 0, 255) and bg == (255, 0, 255):
                            cell_labels[(ly, lx)] = "transparent"
                            continue
                        if bg == (255, 0, 255) and glyph == 0:
                            cell_labels[(ly, lx)] = "transparent"
                            continue

                        key = rgb_cell_key(glyph, fg, bg)
                        candidates: list[tuple[str, float]] = []

                        for rname, sigs in asigs.items():
                            if key in sigs:
                                # Signature match — compute spatial proximity
                                positions = apos.get(rname, [])
                                if positions:
                                    min_dist = min(
                                        abs(ly - py) + abs(lx - px)
                                        for py, px in positions
                                    )
                                    # Closer to anchor position = higher confidence
                                    proximity_score = max(0.0, 1.0 - min_dist / 4.0)
                                else:
                                    proximity_score = 0.5
                                candidates.append((rname, proximity_score))

                        if candidates:
                            # Pick best match by proximity
                            best = max(candidates, key=lambda c: c[1])
                            cell_labels[(ly, lx)] = best[0]
                            cell_confidences.setdefault(best[0], []).append(best[1])
                        else:
                            cell_labels[(ly, lx)] = "unknown"

                    # Build propagated regions from cell labels
                    region_cells: dict[str, list[dict]] = {}
                    for (ly, lx), label in cell_labels.items():
                        if label in ("transparent", "unknown"):
                            continue
                        region_cells.setdefault(label, []).append({
                            "x": lx, "y": ly,
                            "glyph": frame_cells[(ly, lx)][0],
                            "fg": _rgb_to_hex(frame_cells[(ly, lx)][1]),
                            "bg": _rgb_to_hex(frame_cells[(ly, lx)][2]),
                        })

                    regions_out = []
                    for rname, cells_list in region_cells.items():
                        xs = [c["x"] for c in cells_list]
                        ys = [c["y"] for c in cells_list]
                        confs = cell_confidences.get(rname, [0.5])
                        avg_conf = sum(confs) / len(confs) if confs else 0.0

                        # Get slot_affinity from this angle's anchor
                        slot_aff = slot_aff_index.get((angle, rname))

                        region_out: dict = {
                            "name": rname,
                            "bbox": [min(xs), min(ys), max(xs), max(ys)],
                            "confidence": "high" if avg_conf > 0.7 else ("medium" if avg_conf > 0.4 else "low"),
                            "palette_roles": [],
                            "semantic_cells": cells_list,
                            "propagation_confidence": round(avg_conf, 3),
                        }
                        if slot_aff:
                            region_out["slot_affinity"] = slot_aff
                        regions_out.append(region_out)

                    fkey = str(flat_idx)
                    result_frames[fkey] = {
                        "projection": proj,
                        "angle": angle,
                        "anim_index": anim_index,
                        "regions": regions_out,
                    }
                    flat_idx += 1

    return {
        "schema_version": "0.1.0",
        "family": sprite_type,
        "reference_xp": xp_path,
        "semantic_layer": visual_layer_index,
        "frame_w": anchor_fw,
        "frame_h": anchor_fh,
        "grid_layout": anchor_data.get("grid_layout", {}),
        "palette_roles": anchor_data.get("palette_roles", {}),
        "frames": result_frames,
        "angle_anchors": {
            "ground_truth_angles": sorted(anchor_sigs.keys()),
            "propagated_angles": sorted(propagated_angle_set),
        },
    }


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert '#rrggbb' to (r, g, b) tuple."""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return (0, 0, 0)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(rgb: tuple) -> str:
    """Convert (r, g, b) tuple to '#rrggbb' string."""
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


# ---------------------------------------------------------------------------
# Overlay mask derivation (FL-2897 / U6)
# ---------------------------------------------------------------------------

def derive_overlay_masks(
    body_xp_path: str,
    overlay_xp_path: str,
    sprite_type: str = "player",
    *,
    visual_layer_index: int = 2,
) -> dict:
    """
    Derive which body regions an overlay covers at each angle by combining
    cell-level diff with the angle-aware body semantic map.

    Requires per-angle anchor data to be loaded via load_angle_anchors()
    before calling.

    Returns
    -------
    dict matching pipeline-v3 overlay_masks schema:
        { slot_name: { angle_str: { body_parts_covered, covered_cells, slot_affinity } } }
    """
    from ..xp_core import XPFile

    body_xp = XPFile(body_xp_path)
    overlay_xp = XPFile(overlay_xp_path)

    if visual_layer_index >= len(body_xp.layers) or visual_layer_index >= len(overlay_xp.layers):
        return {}

    body_layer = body_xp.layers[visual_layer_index]
    overlay_layer = overlay_xp.layers[visual_layer_index]

    source_meta = _describe_source_asset(body_xp_path, sprite_type)
    # Derive anim_info from ACTION_FRAMES (same fix as propagate_from_anchors)
    action_frames = ACTION_FRAMES.get(sprite_type, {"idle": 1})
    anim_info = list(action_frames.values())
    anchor_meta = _ANGLE_ANCHOR_META.get(sprite_type, {})
    fw = anchor_meta.get("frame_w", FRAME_W)
    fh = anchor_meta.get("frame_h", FRAME_H)

    sheet_rows = len(body_layer.data)
    sheet_cols = len(body_layer.data[0]) if sheet_rows > 0 else 0

    # Aggregate diff cells per angle across all frames (not just idle)
    angle_diffs: dict[int, list[tuple[int, int, str]]] = {}

    for angle in range(ANGLE_COUNT):
        if not has_angle_anchors(sprite_type, angle):
            continue

        diffs: list[tuple[int, int, str]] = []
        seen_cells: set[tuple[int, int]] = set()
        SWOOSH_RGB = (0, 255, 255)

        # Iterate all frames at this angle (not just idle)
        for anim_index, anim_length in enumerate(anim_info):
            for frame_in_anim in range(anim_length):
                origin_row, origin_col = _get_frame_origin_from_variant(
                    angle, anim_index, frame_in_anim, 0,
                    anim_info, frame_w=fw, frame_h=fh,
                )

                for ly in range(fh):
                    for lx in range(fw):
                        sy, sx = origin_row + ly, origin_col + lx
                        if sy >= sheet_rows or sx >= sheet_cols:
                            continue
                        if sy >= len(overlay_layer.data) or sx >= len(overlay_layer.data[0]):
                            continue

                        body_cell = body_layer.data[sy][sx]
                        overlay_cell = overlay_layer.data[sy][sx]

                        b_key = (body_cell[0], tuple(body_cell[1]), tuple(body_cell[2]))
                        o_key = (overlay_cell[0], tuple(overlay_cell[1]), tuple(overlay_cell[2]))

                        if b_key != o_key:
                            # Deduplicate across frames — same (lx, ly) cell
                            # may diff in multiple frames
                            if (lx, ly) not in seen_cells:
                                seen_cells.add((lx, ly))

                                # Swoosh detection via CYAN RGB
                                fg_rgb = tuple(overlay_cell[1])
                                bg_rgb = tuple(overlay_cell[2])
                                is_swoosh = (fg_rgb == SWOOSH_RGB or bg_rgb == SWOOSH_RGB)

                                if is_swoosh:
                                    body_part = "weapon_swing"
                                else:
                                    body_part = get_body_part_at(
                                        ly, lx, angle=angle, sprite_type=sprite_type,
                                        frame_w=fw, frame_h=fh,
                                    )
                                    if body_part == "unknown":
                                        body_part = "overlay_extension"

                                diffs.append((lx, ly, body_part))

        if diffs:
            angle_diffs[angle] = diffs

    # Infer slot from dominant body parts
    all_body_parts: list[str] = []
    for diffs in angle_diffs.values():
        for _, _, bp in diffs:
            if bp not in ("overlay_extension", "weapon_swing"):
                all_body_parts.append(bp)

    # Count body part groups for slot inference
    head_parts = {"face_center", "head_top", "hair", "eyes_nose", "mouth", "face"}
    torso_parts = {"torso", "left_arm", "right_arm", "pelvis", "shirt"}
    weapon_parts = {"weapon_hand", "weapon_swing"}

    head_count = sum(1 for bp in all_body_parts if bp in head_parts)
    torso_count = sum(1 for bp in all_body_parts if bp in torso_parts)
    weapon_count = sum(1 for bp in all_body_parts if bp in weapon_parts)
    total_non_ext = len(all_body_parts)

    # Thresholds per spec §2.3.11.4e: >80% head = helmet/head slot
    if total_non_ext > 0:
        if weapon_count / total_non_ext > 0.3:
            inferred_slot = "weapon"
        elif head_count / total_non_ext > 0.8:
            inferred_slot = "head"
        elif torso_count / total_non_ext > 0.5:
            inferred_slot = "armor"
        else:
            inferred_slot = "body"
    else:
        inferred_slot = "body"

    # Build output
    masks: dict[str, dict] = {}
    for angle, diffs in angle_diffs.items():
        angle_key = str(angle)
        body_parts_covered = sorted(set(bp for _, _, bp in diffs if bp != "overlay_extension"))
        covered_cells = [[x, y] for x, y, _ in diffs]

        masks.setdefault(inferred_slot, {})[angle_key] = {
            "body_parts_covered": body_parts_covered,
            "covered_cells": covered_cells,
            "slot_affinity": inferred_slot,
        }

    return masks


# ---------------------------------------------------------------------------
# Anchor template generator and validation (FL-2897 / U7)
# ---------------------------------------------------------------------------

_REGION_TO_SLOT: dict[str, str] = {
    "face_center": "head", "head_top": "head", "hair": "head",
    "eyes_nose": "head", "mouth": "head", "face": "head",
    "torso": "armor", "left_arm": "armor", "right_arm": "armor",
    "pelvis": "armor", "shirt": "armor",
    "left_leg": "body", "right_leg": "body", "left_foot": "body",
    "right_foot": "body", "legs": "body", "boots": "body",
    "weapon_hand": "weapon", "shield_hand": "shield",
    "seat_anchor": "mount",
}


def _classify_cell_color(
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
) -> str:
    """Classify a cell's dominant color into a body-part candidate group."""
    # Known palette roles from the player sprite semantic maps
    SKIN = (255, 85, 85)
    SKIN_DARK = (170, 0, 0)
    SHIRT = (170, 0, 170)
    PANTS_DARK = (0, 0, 170)
    PANTS_BRIGHT = (85, 85, 255)
    BOOTS = (170, 85, 0)
    HAIR_BLACK = (0, 0, 0)
    SUBCELL_YELLOW = (255, 255, 85)

    # Classify by bg first (more reliable for filled cells), then fg
    if bg == SKIN or fg == SKIN or bg == SKIN_DARK or fg == SKIN_DARK:
        return "skin"
    if bg == SHIRT or fg == SHIRT:
        return "shirt"
    if bg == PANTS_DARK or fg == PANTS_DARK or bg == PANTS_BRIGHT or fg == PANTS_BRIGHT:
        return "pants"
    if fg == BOOTS:
        return "boots"
    if bg == SUBCELL_YELLOW and fg == HAIR_BLACK:
        return "hair"
    if bg == SUBCELL_YELLOW:
        return "edge"
    if fg == HAIR_BLACK and bg == HAIR_BLACK:
        return "outline"
    return "other"


# Color group -> body part name + slot affinity
_COLOR_GROUP_TO_REGION: dict[str, tuple[str, str]] = {
    "skin": ("face", "head"),
    "hair": ("hair", "head"),
    "shirt": ("shirt", "armor"),
    "pants": ("pants", "body"),
    "boots": ("boots", "body"),
    "edge": ("edge", "body"),
    "outline": ("outline", "body"),
    "other": ("other", "body"),
}


def export_angle_anchor_template(
    sprite_type: str = "player",
    frame_w: int | None = None,
    frame_h: int | None = None,
    xp_path: str | None = None,
    output_path: str | None = None,
) -> dict:
    """
    Generate an 8-angle anchor JSON template from real XP sprite data.

    When xp_path is provided, reads the actual idle frame at each angle
    and groups visible cells by color signature into candidate body-part
    regions with pre-populated semantic_cells and bboxes.

    When xp_path is None, falls back to static _REGION_ATLAS scaffolding.

    The user reviews each angle's regions, corrects region names where
    the color-based guess is wrong, and marks confidence as "high".

    Returns the template dict. If output_path is given, also writes to disk.
    """
    fw = frame_w or (7 if sprite_type == "player" else FRAME_W)
    fh = frame_h or FRAME_H

    # Try to load XP data
    xp_cells = None
    xp_w = 0
    xp_h = 0
    if xp_path:
        try:
            import gzip
            import struct
            raw = Path(xp_path).read_bytes()
            if raw.startswith(b"\x1f\x8b"):
                data = gzip.decompress(raw)
            else:
                data = raw
            offset = 0
            version = struct.unpack_from("<i", data, 0)[0]
            offset = 4
            layer_count = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            for li in range(min(layer_count, 3)):
                lw = struct.unpack_from("<i", data, offset)[0]
                offset += 4
                lh = struct.unpack_from("<i", data, offset)[0]
                offset += 4
                if li == 2:
                    # Visual layer — read cells
                    xp_w, xp_h = lw, lh
                    xp_cells = [None] * (lw * lh)
                    for x in range(lw):
                        for y in range(lh):
                            glyph = struct.unpack_from("<I", data, offset)[0]
                            offset += 4
                            fg_rgb = tuple(data[offset:offset + 3])
                            bg_rgb = tuple(data[offset + 3:offset + 6])
                            offset += 6
                            xp_cells[y * lw + x] = (glyph, fg_rgb, bg_rgb)
                else:
                    # Skip this layer's cells
                    offset += lw * lh * 10  # 4 (glyph) + 6 (fg+bg)
        except Exception:
            xp_cells = None

    # Discovered palette roles (from real data)
    discovered_roles: dict[str, dict] = {}

    frames: dict[str, dict] = {}
    for angle in range(ANGLE_COUNT):
        direction = ANGLE_NAMES[angle]

        if xp_cells and xp_w > 0 and xp_h > 0:
            # Read real cells from the idle frame at this angle
            origin_row = angle * fh
            origin_col = 0  # idle = frame 0, proj 0

            # Group cells by color classification
            color_groups: dict[str, list[dict]] = {}
            for ly in range(fh):
                for lx in range(fw):
                    sx, sy = origin_col + lx, origin_row + ly
                    if sy >= xp_h or sx >= xp_w:
                        continue
                    glyph, fg, bg = xp_cells[sy * xp_w + sx]
                    # Skip transparent cells
                    if glyph == 32 or bg == (255, 0, 255):
                        continue
                    if glyph == 0 and bg == (255, 0, 255):
                        continue

                    group = _classify_cell_color(fg, bg)
                    fg_hex = _rgb_to_hex(fg)
                    bg_hex = _rgb_to_hex(bg)
                    color_groups.setdefault(group, []).append({
                        "x": lx, "y": ly,
                        "glyph": glyph,
                        "fg": fg_hex,
                        "bg": bg_hex,
                        "role": f"{group}_cell",
                    })

                    # Track palette roles
                    if fg_hex not in ("#000000", "#ff00ff"):
                        discovered_roles.setdefault(f"fg_{fg_hex}", {
                            "colors": [fg_hex],
                            "confidence": "medium",
                            "usage": "fg",
                            "description": f"Foreground color {fg_hex}",
                        })
                    if bg_hex not in ("#000000", "#ff00ff"):
                        discovered_roles.setdefault(f"bg_{bg_hex}", {
                            "colors": [bg_hex],
                            "confidence": "medium",
                            "usage": "bg",
                            "description": f"Background color {bg_hex}",
                        })

            # Build regions from color groups
            regions_out = []
            for group_name, cells_list in color_groups.items():
                region_name, slot = _COLOR_GROUP_TO_REGION.get(
                    group_name, ("other", "body"),
                )
                xs = [c["x"] for c in cells_list]
                ys = [c["y"] for c in cells_list]
                regions_out.append({
                    "name": region_name,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)],
                    "confidence": "medium",
                    "palette_roles": [],
                    "semantic_cells": cells_list,
                    "slot_affinity": slot,
                    "notes": f"Auto-grouped from {len(cells_list)} cells with "
                             f"'{group_name}' color at angle {angle} ({direction}). "
                             f"Review: verify region name, split/merge as needed.",
                })

            # Sort regions top-to-bottom by min y
            regions_out.sort(key=lambda r: r["bbox"][1])

        else:
            # Fallback: static atlas scaffolding (no XP data)
            regions_out = []
            for entry in _REGION_ATLAS:
                rname = entry["name"]
                row_lo, row_hi = _scale_bounds(
                    entry["row_frac"][0], entry["row_frac"][1], fh,
                )
                col_lo, col_hi = _scale_bounds(
                    entry["col_frac"][0], entry["col_frac"][1], fw,
                )
                slot = _REGION_TO_SLOT.get(rname, "body")
                regions_out.append({
                    "name": rname,
                    "bbox": [col_lo, row_lo, col_hi, row_hi],
                    "confidence": "low",
                    "palette_roles": [],
                    "semantic_cells": [],
                    "slot_affinity": slot,
                    "notes": f"Scaffolded from static atlas for angle {angle} "
                             f"({direction}). Adjust bbox and fill semantic_cells.",
                })

        frames[str(angle)] = {
            "projection": 0,
            "angle": angle,
            "anim_index": 0,
            "anim_name": "idle",
            "regions": regions_out,
        }

    # Deduplicate discovered palette roles by color
    palette_roles: dict[str, dict] = {}
    seen_colors: set[str] = set()
    KNOWN_ROLES = {
        "#ff5555": ("skin", "Flesh tone — face, arms", "bg"),
        "#aa0000": ("skin_detail", "Dark red — mouth/expression detail", "fg"),
        "#aa00aa": ("shirt_primary", "Purple torso garment", "both"),
        "#0000aa": ("pants_dark", "Dark blue lower garment", "fg"),
        "#5555ff": ("pants_bright", "Bright blue pants highlight", "both"),
        "#aa5500": ("boots_primary", "Brown boot/footwear", "fg"),
        "#ffff55": ("subcell_fill", "Yellow sub-cell rendering fill (NOT hair)", "bg"),
        "#000000": ("outline", "Black — hair, eyes, outlines", "both"),
    }
    for key, role_data in discovered_roles.items():
        color = role_data["colors"][0]
        if color in seen_colors:
            continue
        seen_colors.add(color)
        if color in KNOWN_ROLES:
            name, desc, usage = KNOWN_ROLES[color]
            palette_roles[name] = {
                "colors": [color],
                "confidence": "high",
                "usage": usage,
                "description": desc,
            }
        else:
            palette_roles[key] = role_data

    ref_xp = xp_path or f"sprites/{sprite_type}-0100.xp"

    template: dict = {
        "schema_version": "0.1.0",
        "family": sprite_type,
        "reference_xp": ref_xp,
        "semantic_layer": 2,
        "frame_w": fw,
        "frame_h": fh,
        "grid_layout": {
            "angles": ANGLE_COUNT,
            "projections": 2,
            "anim_counts": list(ACTION_FRAMES.get(sprite_type, {"idle": 1}).values()),
            "frames_per_row": sum(ACTION_FRAMES.get(sprite_type, {"idle": 1}).values()),
            "rows": ANGLE_COUNT,
        },
        "palette_roles": palette_roles,
        "frames": frames,
        "angle_anchors": {
            "ground_truth_angles": [],
            "propagated_angles": [],
        },
        "ambiguities": [
            "Auto-populated from real XP data. Regions are grouped by color — "
            "review each angle and correct region names where color grouping "
            "is ambiguous (e.g., 'edge' cells may belong to adjacent regions)."
        ] if xp_cells else [
            "Static atlas scaffolding — no XP data was provided. "
            "Fill semantic_cells from the actual sprite at each angle."
        ],
    }

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2)

    return template


def validate_anchor_file(json_path: str) -> tuple[bool, list[str]]:
    """
    Validate a completed angle anchor file.

    Returns (ok, errors) where ok is True if the anchor file is valid.
    """
    path = Path(json_path)
    if not path.is_file():
        return False, [f"File not found: {json_path}"]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    errors: list[str] = []
    warnings: list[str] = []

    fw = data.get("frame_w", FRAME_W)
    fh = data.get("frame_h", FRAME_H)

    # Check all 8 angles present
    frames = data.get("frames", {})
    found_angles: set[int] = set()
    for frame_key, frame_data in frames.items():
        angle = frame_data.get("angle")
        if angle is None:
            try:
                angle = int(frame_key)
            except (ValueError, TypeError):
                continue
        if 0 <= angle < ANGLE_COUNT:
            found_angles.add(angle)

    missing_angles = set(range(ANGLE_COUNT)) - found_angles
    if missing_angles:
        errors.append(f"Missing angles: {sorted(missing_angles)}")

    # Check core body parts at each angle
    core_parts = {"face", "face_center", "head_top", "torso", "shirt"}
    for angle in sorted(found_angles):
        angle_regions = set()
        for frame_key, frame_data in frames.items():
            fa = frame_data.get("angle")
            if fa is None:
                try:
                    fa = int(frame_key)
                except (ValueError, TypeError):
                    continue
            if fa != angle:
                continue
            for region in frame_data.get("regions", []):
                angle_regions.add(region.get("name", ""))

        # Check for at least some core parts (flexible — not all required at every angle)
        has_head = bool(angle_regions & {"face", "face_center", "head_top", "hair"})
        has_torso = bool(angle_regions & {"torso", "shirt"})
        if not has_head:
            warnings.append(f"Angle {angle}: no head region defined")
        if not has_torso:
            warnings.append(f"Angle {angle}: no torso region defined")

        # Check for overlapping bboxes
        bboxes: list[tuple[str, list[int]]] = []
        for frame_key, frame_data in frames.items():
            fa = frame_data.get("angle")
            if fa is None:
                try:
                    fa = int(frame_key)
                except (ValueError, TypeError):
                    continue
            if fa != angle:
                continue
            for region in frame_data.get("regions", []):
                bbox = region.get("bbox", [])
                if len(bbox) == 4:
                    bboxes.append((region.get("name", "?"), bbox))

        for i, (n1, b1) in enumerate(bboxes):
            for j, (n2, b2) in enumerate(bboxes):
                if j <= i:
                    continue
                # Check overlap: b = [x0, y0, x1, y1]
                if b1[0] <= b2[2] and b2[0] <= b1[2] and b1[1] <= b2[3] and b2[1] <= b1[3]:
                    warnings.append(
                        f"Angle {angle}: overlapping bboxes between '{n1}' {b1} and '{n2}' {b2}"
                    )

        # Check slot_affinity values
        valid_slots = {"body", "head", "shield", "weapon", "armor", "mount"}
        for frame_key, frame_data in frames.items():
            fa = frame_data.get("angle")
            if fa is None:
                try:
                    fa = int(frame_key)
                except (ValueError, TypeError):
                    continue
            if fa != angle:
                continue
            for region in frame_data.get("regions", []):
                sa = region.get("slot_affinity")
                if sa is not None and sa not in valid_slots:
                    errors.append(
                        f"Angle {angle}, region '{region.get('name', '?')}': "
                        f"invalid slot_affinity '{sa}'"
                    )

    all_messages = errors + [f"WARNING: {w}" for w in warnings]
    return len(errors) == 0, all_messages
