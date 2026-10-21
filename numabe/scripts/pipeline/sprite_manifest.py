"""
sprite_manifest.py -- Scan and cluster assets/sprites/*.xp into template families

ARCHITECTURE:
    Audit module that discovers all official sprites at runtime, extracts their
    metadata, and clusters them into families by shared geometry. This is the
    foundation for the sprite baseline audit (Phase 1).

    The manifest scanner runs once to produce a JSON snapshot that downstream
    modules consume. The snapshot is used to derive hardcoded presets and
    invariant rules without coupling to the sprites directory at runtime.

PURPOSE:
    AUDIT-01 and AUDIT-02 from Phase 1 -- establish ground truth about what
    "working sprites" actually look like before defining invariants. Without
    this data, invariants and presets would be guesswork (as the current
    presets prove -- ORC_TEMPLATE says 12x12 cells but real player sprites
    are 7x9).

DATA FLOW:
    assets/sprites/*.xp --> sprite_manifest.py --> sprite_manifest.json
    sprite_manifest.json --> sprite_invariants.py (FAMILY_PRESETS dict)
    sprite_invariants.py --> sprite_validator.py (imports invariant rules)
    sprite_invariants.py --> presets.py (imports FAMILY_PRESETS)

[FLOW:MANIFEST]
"""

import argparse
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .xp_core import XPFile


def extract_sprite_facts(xp_path: Path) -> dict[str, Any]:
    """Extract metadata from a single .xp file.

    Args:
        xp_path: Path to .xp file

    Returns:
        Dict with sprite metadata:
        - filename: stem of file
        - path: relative path from repo root
        - width, height: layer 0 dimensions
        - layer_count: number of layers
        - angles, projs, anims: from get_metadata()
        - frame_w, frame_h: computed frame dimensions
        - key_color: RGB tuple from layer 0 cell (0,0) background
        - file_size: size in bytes

        On error, returns dict with 'error' field instead.

    [DATA-CONTRACT:XP] [FLOW:MANIFEST]
    """
    try:
        xp = XPFile()
        xp.load(str(xp_path))

        if not xp.layers:
            return {
                "filename": xp_path.stem,
                "path": str(xp_path),
                "error": "No layers in file"
            }

        # Get basic dimensions from layer 0 (all layers same size)
        layer0 = xp.layers[0]
        width = layer0.width
        height = layer0.height
        layer_count = len(xp.layers)

        # Get metadata (angles, projs, anims)
        metadata = xp.get_metadata()
        if metadata is None:
            return {
                "filename": xp_path.stem,
                "path": str(xp_path),
                "error": "Failed to extract metadata"
            }

        angles = metadata["angles"]
        projs = metadata["projs"]
        anims = metadata["anims"]

        # Compute frame dimensions using canonical formula
        # [ENGINE-ALIGN] Matches sprite.cpp frame computation
        # WHY max(sum(anims), 1): anims may be empty [], treat as [1]
        anim_sum = sum(anims) if anims else 1
        frame_w = width // (projs * anim_sum)
        frame_h = height // angles

        # Extract key color from layer 0 cell (0,0) background
        # [DATA-CONTRACT:XP] Cell format: (glyph, fg_rgb, bg_rgb)
        cell = layer0.data[0][0]
        key_color = cell[2]  # bg_rgb tuple

        # Get file size
        file_size = os.path.getsize(xp_path)

        # Get relative path from repo root (assume assets/sprites/ is in repo root)
        try:
            rel_path = xp_path.relative_to(xp_path.parents[1])
        except ValueError:
            rel_path = xp_path

        return {
            "filename": xp_path.stem,
            "path": str(rel_path),
            "width": width,
            "height": height,
            "layer_count": layer_count,
            "angles": angles,
            "projs": projs,
            "anims": anims,
            "frame_w": frame_w,
            "frame_h": frame_h,
            "key_color": list(key_color),  # Convert tuple to list for JSON
            "file_size": file_size
        }

    except Exception as e:
        return {
            "filename": xp_path.stem,
            "path": str(xp_path),
            "error": str(e)
        }


def scan_sprites_dir(sprites_dir: Path) -> list[dict[str, Any]]:
    """Scan all .xp files in a directory and extract their facts.

    Args:
        sprites_dir: Directory containing .xp files

    Returns:
        List of sprite fact dicts, sorted by filename

    [FLOW:MANIFEST]
    """
    sprites = []

    for xp_path in sorted(sprites_dir.glob("*.xp")):
        facts = extract_sprite_facts(xp_path)
        sprites.append(facts)

    return sprites


def cluster_into_families(sprites: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group sprites by geometry signature into families.

    Args:
        sprites: List of sprite fact dicts

    Returns:
        Dict keyed by family ID (geometry-based), each value contains:
        - id: family ID (same as key)
        - label: human-readable name derived from common prefix
        - geometry: dict with frame_w, frame_h, angles, projs
        - count: number of sprites in family
        - representatives: list of 2-3 representative filenames
        - members: list of all member filenames

    Family ID format: "{frame_w}x{frame_h}_a{angles}_p{projs}"
    This is deterministic and stable across sprite renames.

    [FLOW:MANIFEST]
    """
    # Group by geometry signature
    clusters: dict[str, list[dict]] = {}

    for sprite in sprites:
        # Skip error entries
        if "error" in sprite:
            continue

        # Compute family ID from geometry
        frame_w = sprite["frame_w"]
        frame_h = sprite["frame_h"]
        angles = sprite["angles"]
        projs = sprite["projs"]

        family_id = f"{frame_w}x{frame_h}_a{angles}_p{projs}"

        if family_id not in clusters:
            clusters[family_id] = []

        clusters[family_id].append(sprite)

    # Build family dicts
    families = {}

    for family_id, members in clusters.items():
        # Extract geometry from first member
        first = members[0]
        geometry = {
            "frame_w": first["frame_w"],
            "frame_h": first["frame_h"],
            "angles": first["angles"],
            "projs": first["projs"]
        }

        # Derive human label from common filename prefix
        filenames = [m["filename"] for m in members]
        label = _derive_family_label(filenames, family_id)

        # Pick 2-3 representatives (prefer simple names)
        representatives = _select_representatives(filenames)

        families[family_id] = {
            "id": family_id,
            "label": label,
            "geometry": geometry,
            "count": len(members),
            "representatives": representatives,
            "members": filenames
        }

    return families


def _derive_family_label(filenames: list[str], fallback_id: str) -> str:
    """Derive a human-readable label from common filename prefix.

    Args:
        filenames: List of filenames in family
        fallback_id: Geometry ID to use if no common prefix

    Returns:
        Human-readable label (e.g., "player", "item-world", "grid")
    """
    if not filenames:
        return fallback_id

    # Find common prefix
    prefix = filenames[0]
    for name in filenames[1:]:
        # Find longest common prefix
        i = 0
        while i < len(prefix) and i < len(name) and prefix[i] == name[i]:
            i += 1
        prefix = prefix[:i]

    # Clean up prefix (remove trailing dashes, digits, etc.)
    prefix = prefix.rstrip("-0123456789")

    if prefix and len(prefix) >= 3:
        return prefix

    return fallback_id


def _select_representatives(filenames: list[str]) -> list[str]:
    """Select 2-3 representative filenames from a family.

    Prefers files with simplest names (shortest, no equipment codes).

    Args:
        filenames: All filenames in family

    Returns:
        List of 2-3 representative filenames
    """
    # Sort by length (prefer shorter names)
    sorted_names = sorted(filenames, key=lambda x: (len(x), x))

    # Pick 2-3 representatives
    count = min(3, len(sorted_names))
    return sorted_names[:count]


def build_manifest(sprites_dir: Path) -> dict[str, Any]:
    """Build complete sprite manifest with scan + cluster.

    Args:
        sprites_dir: Directory containing .xp files

    Returns:
        Dict with:
        - generated: ISO timestamp
        - total_sprites: count of sprites discovered
        - manifest_hash: SHA-256 of sorted sprite facts (for freshness check)
        - families: dict of family clusters
        - sprites: list of all sprite facts

    [FLOW:MANIFEST]
    """
    # Scan all sprites
    sprites = scan_sprites_dir(sprites_dir)

    # Cluster into families
    families = cluster_into_families(sprites)

    # Compute manifest hash (SHA-256 of sorted sprite facts)
    # This allows downstream modules to verify their hardcoded values match
    hash_input = []
    for sprite in sorted(sprites, key=lambda x: x.get("filename", "")):
        if "error" not in sprite:
            # Hash only stable geometry data
            hash_input.append(f"{sprite['filename']}:{sprite['frame_w']}x{sprite['frame_h']}_a{sprite['angles']}_p{sprite['projs']}")

    manifest_hash = hashlib.sha256("\n".join(hash_input).encode()).hexdigest()

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_sprites": len(sprites),
        "manifest_hash": manifest_hash,
        "families": families,
        "sprites": sprites
    }


def main():
    """CLI entry point for manifest generation."""
    parser = argparse.ArgumentParser(
        description="Scan sprites directory and generate family manifest"
    )
    parser.add_argument(
        "--sprites-dir",
        type=Path,
        default=Path("assets/sprites"),
        help="Directory containing .xp files (default: assets/sprites/)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Build manifest
    manifest = build_manifest(args.sprites_dir)

    # Output JSON
    json_str = json.dumps(manifest, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json_str)
        print(f"Manifest written to {args.output}")
        print(f"Total sprites: {manifest['total_sprites']}")
        print(f"Families: {len(manifest['families'])}")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
