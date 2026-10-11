"""
sprite_invariants.py -- Canonical sprite invariants derived from manifest data

ARCHITECTURE:
    Defines the canonical invariants that all .xp sprites must satisfy to load
    correctly in the C++ engine (sprite.cpp). Invariants are organized into
    two tiers:

    1. ENGINE_INVARIANTS: Universal requirements for any .xp file to load
    2. FAMILY_PRESETS: Geometry-based family definitions from manifest data

    These invariants are derived from:
    - sprite.cpp LoadSprite() requirements (engine invariants)
    - sprite_manifest.json measured data (family invariants)

    This module is consumed by:
    - sprite_validator.py (validation against invariants)
    - presets.py (import presets for new sprite generation)
    - tests (golden baseline validation)

PURPOSE:
    Replace guesswork with measured ground truth. The existing pipeline had
    hardcoded presets (ORC_TEMPLATE with 12x12 cells) that don't match real
    sprites (player is 7x9). This module codifies what "working sprites"
    actually look like by analyzing all 210+ official sprites.

[DATA-CONTRACT:INVARIANTS] [FLOW:VALIDATION]
"""

import gzip
import hashlib
import struct
from pathlib import Path
from typing import Any

from .xp_core import XPFile


# Manifest hash for freshness verification
# Regenerate sprite_invariants.py if this hash changes
MANIFEST_HASH = "208f1356dfad5474c0c7eec5fe23bc88d34c149e08c669100a7c61227607a247"


# ============================================================================
# TIER 1: ENGINE INVARIANTS
# ============================================================================
# These MUST be true for any .xp file to load in sprite.cpp

ENGINE_INVARIANTS = [
    {
        "id": "ENG-01",
        "name": "valid_gzip",
        "description": "File must be valid gzip format (ID1=31, ID2=139, CM=8)",
        "check": "gzip_valid"
    },
    {
        "id": "ENG-02",
        "name": "min_layers",
        "description": "Layer count >= 3 (colorkey, height, visual)",
        "check": "layer_count >= 3"
    },
    {
        "id": "ENG-03",
        "name": "positive_dimensions",
        "description": "Width >= 1 and Height >= 1",
        "check": "width >= 1 and height >= 1"
    },
    {
        "id": "ENG-04",
        "name": "glyph_range",
        "description": "All glyphs in range 0-255 (CP437)",
        "check": "all_glyphs_valid"
    },
    {
        "id": "ENG-05",
        "name": "width_divisible",
        "description": "Width divisible by projs * max(sum(anims), 1)",
        "check": "width_divisible"
    },
    {
        "id": "ENG-06",
        "name": "height_divisible",
        "description": "Height divisible by angles",
        "check": "height_divisible"
    },
    {
        "id": "ENG-07",
        "name": "gzip_size_match",
        "description": "Decompressed size matches gzip ISIZE trailer",
        "check": "gzip_size_valid"
    },
]


# ============================================================================
# TIER 2: FAMILY PRESETS
# ============================================================================
# Geometry-based family definitions from sprite_manifest.json
# Generated from: .planning/phases/01-sprite-baseline-audit/sprite_manifest.json
# DO NOT EDIT -- Regenerate by running: python3 -m scripts.pipeline.sprite_manifest

FAMILY_PRESETS = {
    "10x12_a8_p2": {
        "label": "wolfie", "frame_w": 10, "frame_h": 12, "angles": 8, "projs": 2,
        "anims_pattern": [1, 8], "layers": 3, "key_color": (255, 255, 85),
    },
    "10x13_a8_p2": {
        "label": "wol", "frame_w": 10, "frame_h": 13, "angles": 8, "projs": 2,
        "anims_pattern": [8], "layers": 5, "key_color": (255, 255, 85),
    },
    "11x11_a8_p2": {
        "label": "plydie", "frame_w": 11, "frame_h": 11, "angles": 8, "projs": 2,
        "anims_pattern": [5], "layers": 3, "key_color": (255, 255, 85),
    },
    "11x12_a8_p2": {
        "label": "plydie", "frame_w": 11, "frame_h": 12, "angles": 8, "projs": 2,
        "anims_pattern": [5], "layers": 4, "key_color": (255, 255, 85),
    },
    "11x13_a8_p2": {
        "label": "bigbee", "frame_w": 11, "frame_h": 13, "angles": 8, "projs": 2,
        "anims_pattern": [1, 2], "layers": 3, "key_color": (255, 255, 85),
    },
    "18x20_a8_p2": {
        "label": "AAATESTplayer", "frame_w": 18, "frame_h": 20, "angles": 8, "projs": 2,
        "anims_pattern": [1, 8], "layers": 4, "key_color": (0, 0, 0),
    },
    "1x2_a1_p2": {
        "label": "1x2_a1_p2", "frame_w": 1, "frame_h": 2, "angles": 1, "projs": 2,
        "anims_pattern": [1], "layers": 3, "key_color": (0, 170, 0),
    },
    "22x26_a8_p2": {
        "label": "AAATESTbigbee", "frame_w": 22, "frame_h": 26, "angles": 8, "projs": 2,
        "anims_pattern": [1, 2], "layers": 4, "key_color": (0, 0, 0),
    },
    "3x11_a1_p1": {
        "label": "grid", "frame_w": 3, "frame_h": 11, "angles": 1, "projs": 1,
        "anims_pattern": [1], "layers": 3, "key_color": (170, 85, 0),
    },
    "3x3_a1_p1": {
        "label": "grid", "frame_w": 3, "frame_h": 3, "angles": 1, "projs": 1,
        "anims_pattern": [1], "layers": 3, "key_color": (170, 85, 0),
    },
    "3x7_a1_p1": {
        "label": "grid", "frame_w": 3, "frame_h": 7, "angles": 1, "projs": 1,
        "anims_pattern": [1], "layers": 3, "key_color": (170, 85, 0),
    },
    "40x11_a1_p2": {
        "label": "asciicker", "frame_w": 40, "frame_h": 11, "angles": 1, "projs": 2,
        "anims_pattern": [1], "layers": 3, "key_color": (0, 0, 0),
    },
    "51x60_a1_p1": {
        "label": "gamepad", "frame_w": 51, "frame_h": 60, "angles": 1, "projs": 1,
        "anims_pattern": [], "layers": 4, "key_color": (102, 51, 0),
    },
    "5x5_a1_p2": {
        "label": "item", "frame_w": 5, "frame_h": 5, "angles": 1, "projs": 2,
        "anims_pattern": [1], "layers": 3, "key_color": (0, 170, 0),
    },
    "5x6_a14_p2": {
        "label": "desert_plants", "frame_w": 5, "frame_h": 6, "angles": 14, "projs": 2,
        "anims_pattern": [1], "layers": 3, "key_color": (170, 85, 0),
    },
    "65x20_a1_p1": {
        "label": "font", "frame_w": 65, "frame_h": 20, "angles": 1, "projs": 1,
        "anims_pattern": [], "layers": 3, "key_color": (102, 51, 0),
    },
    "7x10_a8_p2": {
        "label": "player", "frame_w": 7, "frame_h": 10, "angles": 8, "projs": 2,
        "anims_pattern": [1, 8], "layers": 4, "key_color": (255, 255, 85),
    },
    "7x11_a1_p1": {
        "label": "grid-big", "frame_w": 7, "frame_h": 11, "angles": 1, "projs": 1,
        "anims_pattern": [1], "layers": 3, "key_color": (170, 85, 0),
    },
    "7x12_a1_p2": {
        "label": "enemygen", "frame_w": 7, "frame_h": 12, "angles": 1, "projs": 2,
        "anims_pattern": [1], "layers": 3, "key_color": (0, 170, 170),
    },
    "7x15_a1_p2": {
        "label": "fire", "frame_w": 7, "frame_h": 15, "angles": 1, "projs": 2,
        "anims_pattern": [7], "layers": 3, "key_color": (85, 85, 85),
    },
    "7x6_a1_p1": {
        "label": "character", "frame_w": 7, "frame_h": 6, "angles": 1, "projs": 1,
        "anims_pattern": [1, 1], "layers": 3, "key_color": (0, 170, 0),
    },
    "7x7_a1_p1": {
        "label": "grid", "frame_w": 7, "frame_h": 7, "angles": 1, "projs": 1,
        "anims_pattern": [1], "layers": 3, "key_color": (170, 85, 0),
    },
    "7x9_a8_p2": {
        "label": "player", "frame_w": 7, "frame_h": 9, "angles": 8, "projs": 2,
        "anims_pattern": [1, 8], "layers": 3, "key_color": (255, 255, 85),
    },
    "9x10_a8_p2": {
        "label": "9x10_a8_p2", "frame_w": 9, "frame_h": 10, "angles": 8, "projs": 2,
        "anims_pattern": [8], "layers": 4, "key_color": (255, 255, 85),
    },
    "1x1_a8_p2": {
        "label": "integration_test", "frame_w": 1, "frame_h": 1, "angles": 8, "projs": 2,
        "anims_pattern": [1, 1], "layers": 3, "key_color": (0, 0, 0),
    },
    "39x35_a1_p1": {
        "label": "inventory", "frame_w": 39, "frame_h": 35, "angles": 1, "projs": 1,
        "anims_pattern": [], "layers": 3, "key_color": (0, 170, 0),
    },
}


# ============================================================================
# INVARIANT CHECKERS
# ============================================================================

def check_raw_file_invariants(xp_path: str | Path) -> list[dict[str, Any]]:
    """Check file-level invariants that require raw bytes (ENG-01, ENG-07).

    These checks run BEFORE XPFile.load() since they validate the gzip
    container itself. If gzip is corrupt, XPFile.load() will throw anyway,
    but these checks provide specific diagnostics.

    Args:
        xp_path: Path to .xp file on disk

    Returns:
        List of violation dicts. Empty list = file-level invariants pass.

    [FLOW:VALIDATION]
    """
    violations = []
    xp_path = Path(xp_path)

    try:
        raw = xp_path.read_bytes()
    except OSError as e:
        violations.append({
            "id": "ENG-01",
            "name": "valid_gzip",
            "description": f"Cannot read file: {e}",
            "actual": str(e),
            "expected": "Readable file"
        })
        return violations

    # ENG-01: Valid gzip header (ID1=31, ID2=139, CM=8)
    if len(raw) < 10:
        violations.append({
            "id": "ENG-01",
            "name": "valid_gzip",
            "description": "File too small for gzip header (< 10 bytes)",
            "actual": len(raw),
            "expected": ">= 10 bytes"
        })
        return violations

    if raw[0] != 0x1F or raw[1] != 0x8B:
        violations.append({
            "id": "ENG-01",
            "name": "valid_gzip",
            "description": f"Invalid gzip magic bytes: 0x{raw[0]:02X} 0x{raw[1]:02X}",
            "actual": f"0x{raw[0]:02X}{raw[1]:02X}",
            "expected": "0x1F8B"
        })

    if raw[2] != 8:
        violations.append({
            "id": "ENG-01",
            "name": "valid_gzip",
            "description": f"Invalid gzip compression method: {raw[2]}",
            "actual": raw[2],
            "expected": "8 (deflate)"
        })

    # ENG-07: Decompressed size matches ISIZE trailer
    if len(raw) >= 4:
        isize = struct.unpack("<I", raw[-4:])[0]
        try:
            decompressed = gzip.decompress(raw)
            actual_size = len(decompressed) % (2**32)  # ISIZE is mod 2^32
            if actual_size != isize:
                violations.append({
                    "id": "ENG-07",
                    "name": "gzip_size_match",
                    "description": "Decompressed size does not match ISIZE trailer",
                    "actual": actual_size,
                    "expected": isize
                })
        except Exception as e:
            violations.append({
                "id": "ENG-07",
                "name": "gzip_size_match",
                "description": f"Gzip decompression failed: {e}",
                "actual": str(e),
                "expected": "Successful decompression"
            })

    return violations


def check_engine_invariants(xp: XPFile) -> list[dict[str, Any]]:
    """Check all engine-level invariants against an XPFile.

    Args:
        xp: Loaded XPFile instance

    Returns:
        List of violation dicts. Empty list = all invariants pass.
        Each violation dict contains:
        - id: invariant ID (e.g., "ENG-02")
        - name: invariant name
        - description: what failed
        - actual: actual value found
        - expected: expected value/condition

    [FLOW:VALIDATION]
    """
    violations = []

    # Get basic properties
    if not xp.layers:
        violations.append({
            "id": "ENG-02",
            "name": "min_layers",
            "description": "No layers in file",
            "actual": 0,
            "expected": ">= 3"
        })
        return violations  # Can't check further without layers

    layer0 = xp.layers[0]
    width = layer0.width
    height = layer0.height
    layer_count = len(xp.layers)

    # Get metadata
    metadata = xp.get_metadata()
    if metadata is None:
        violations.append({
            "id": "ENG-META",
            "name": "metadata_extraction",
            "description": "Failed to extract metadata from layer 0",
            "actual": None,
            "expected": "Valid metadata dict"
        })
        return violations

    angles = metadata["angles"]
    projs = metadata["projs"]
    anims = metadata["anims"]
    anim_sum = sum(anims) if anims else 1

    # ENG-02: Layer count >= 3
    if layer_count < 3:
        violations.append({
            "id": "ENG-02",
            "name": "min_layers",
            "description": "Layer count < 3",
            "actual": layer_count,
            "expected": ">= 3"
        })

    # ENG-03: Positive dimensions
    if width < 1:
        violations.append({
            "id": "ENG-03",
            "name": "positive_dimensions",
            "description": "Width < 1",
            "actual": width,
            "expected": ">= 1"
        })

    if height < 1:
        violations.append({
            "id": "ENG-03",
            "name": "positive_dimensions",
            "description": "Height < 1",
            "actual": height,
            "expected": ">= 1"
        })

    # ENG-04: All glyphs in CP437 range (0-255)
    for layer_idx, layer in enumerate(xp.layers):
        for y in range(layer.height):
            for x in range(layer.width):
                cell = layer.data[y][x]
                glyph = cell[0]
                if glyph > 255:
                    violations.append({
                        "id": "ENG-04",
                        "name": "glyph_range",
                        "description": f"Glyph {glyph} out of CP437 range in layer {layer_idx} at ({x},{y})",
                        "actual": glyph,
                        "expected": "0-255"
                    })
                    # Only report first violation to avoid spam
                    break
            else:
                continue
            break

    # ENG-05: Width divisibility
    divisor = projs * anim_sum
    if width % divisor != 0:
        violations.append({
            "id": "ENG-05",
            "name": "width_divisible",
            "description": f"Width not divisible by projs * sum(anims)",
            "actual": f"{width} % ({projs} * {anim_sum}) = {width % divisor}",
            "expected": f"{width} % {divisor} == 0"
        })

    # ENG-06: Height divisibility
    if height % angles != 0:
        violations.append({
            "id": "ENG-06",
            "name": "height_divisible",
            "description": f"Height not divisible by angles",
            "actual": f"{height} % {angles} = {height % angles}",
            "expected": f"{height} % {angles} == 0"
        })

    return violations


def check_family_match(xp: XPFile, family_id: str = None) -> dict[str, Any]:
    """Check if XPFile matches a known family or auto-detect family.

    Args:
        xp: Loaded XPFile instance
        family_id: Optional family ID to check against (e.g., "7x9_a8_p2")
                   If None, attempts to auto-detect from geometry

    Returns:
        Dict with:
        - matched: bool (True if matches a known family)
        - family_id: matched family ID or None
        - family_data: preset dict or None
        - mismatches: list of mismatch dicts (empty if matched)

    [FLOW:VALIDATION]
    """
    # Get sprite geometry
    if not xp.layers:
        return {
            "matched": False,
            "family_id": None,
            "family_data": None,
            "mismatches": [{"field": "layers", "reason": "No layers in file"}]
        }

    metadata = xp.get_metadata()
    if metadata is None:
        return {
            "matched": False,
            "family_id": None,
            "family_data": None,
            "mismatches": [{"field": "metadata", "reason": "Failed to extract metadata"}]
        }

    layer0 = xp.layers[0]
    width = layer0.width
    height = layer0.height
    angles = metadata["angles"]
    projs = metadata["projs"]
    anims = metadata["anims"]
    anim_sum = sum(anims) if anims else 1

    # Compute frame dimensions
    frame_w = width // (projs * anim_sum)
    frame_h = height // angles

    # Compute geometry ID
    computed_id = f"{frame_w}x{frame_h}_a{angles}_p{projs}"

    # Auto-detect or validate explicit family_id
    if family_id is None:
        family_id = computed_id

    # Check if family exists
    if family_id not in FAMILY_PRESETS:
        return {
            "matched": False,
            "family_id": family_id,
            "family_data": None,
            "mismatches": [{"field": "family_id", "reason": f"Unknown family: {family_id}"}]
        }

    preset = FAMILY_PRESETS[family_id]
    mismatches = []

    # Check geometry fields
    if frame_w != preset["frame_w"]:
        mismatches.append({
            "field": "frame_w",
            "actual": frame_w,
            "expected": preset["frame_w"]
        })

    if frame_h != preset["frame_h"]:
        mismatches.append({
            "field": "frame_h",
            "actual": frame_h,
            "expected": preset["frame_h"]
        })

    if angles != preset["angles"]:
        mismatches.append({
            "field": "angles",
            "actual": angles,
            "expected": preset["angles"]
        })

    if projs != preset["projs"]:
        mismatches.append({
            "field": "projs",
            "actual": projs,
            "expected": preset["projs"]
        })

    # NOTE: Layer count and key_color are NOT checked for family matching
    # Layer count varies by equipment (player-0000 has 3, player-1112 has 6)
    # Key color varies by sprite even within families
    # Family matching is ONLY geometry-based: frame_w, frame_h, angles, projs

    return {
        "matched": len(mismatches) == 0,
        "family_id": family_id,
        "family_data": preset,
        "mismatches": mismatches
    }
