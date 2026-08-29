#!/usr/bin/env python3
"""Inspect an .xp sprite file: layer dimensions, metadata, palette analysis.

Loads a REXPaint .xp file and prints layer structure, metadata from Layer 0,
and per-layer cell statistics (glyph/Counter, palette usage).

Origin: proposal BF-1e507530c8c0 from codex session history
  (source: ~/.codex/history.jsonl, code_size ~700)
Generalized: replaced hardcoded path with CLI arg, added metadata extraction,
  palette analysis, and layer filtering. Fixed import path from
  scripts.asset_gen.xp_core → scripts.pipeline.xp_core (actual module location).

Usage:
  python3 scripts/adhoc/xp_inspector.py <file.xp>
  python3 scripts/adhoc/xp_inspector.py <file.xp> --layer 2      # inspect only layer 2
  python3 scripts/adhoc/xp_inspector.py <file.xp> --palette       # show palette analysis
  python3 scripts/adhoc/xp_inspector.py <file.xp> --summary       # quick summary only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Add repo root to path so the pipeline module can be imported.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.pipeline.xp_core import XPFile, XPLayer  # noqa: E402


def _cell_color_key(cell: tuple) -> str:
    """Return a compact color hex string for a cell tuple (glyph, fg, bg)."""
    glyph, fg, bg = cell
    fg_hex = f"#{fg[0]:02x}{fg[1]:02x}{fg[2]:02x}"
    bg_hex = f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}"
    return f"{glyph:3d} fg={fg_hex} bg={bg_hex}"


def inspect_layer(xp: XPFile, layer_idx: int) -> None:
    """Print detailed analysis for a single layer."""
    layer = xp.layers[layer_idx]
    w, h = layer.width, layer.height
    total = w * h

    glyphs: Counter[str] = Counter()
    fg_colors: Counter[tuple] = Counter()
    bg_colors: Counter[tuple] = Counter()
    transparent = 0

    MAGENTA = (255, 0, 255)  # transparency sentinel

    for y in range(h):
        for x in range(w):
            cell = layer.data[y][x]
            glyph = cell[0]
            fg = cell[1] if len(cell) > 1 else (0, 0, 0)
            bg = cell[2] if len(cell) > 2 else (0, 0, 0)

            if isinstance(glyph, int):
                glyphs[chr(glyph) if 32 <= glyph <= 126 else f"\\x{glyph:02x}"] += 1
            else:
                glyphs[str(glyph)] += 1

            fg_colors[fg] += 1
            bg_colors[bg] += 1

            if bg == MAGENTA:
                transparent += 1

    print(f"\n--- Layer {layer_idx}: {w}x{h} ({total} cells) ---")
    print(f"  Transparent cells (magenta bg): {transparent} ({100*transparent/max(total,1):.1f}%)")

    print(f"\n  Top 10 glyphs:")
    for glyph_char, count in glyphs.most_common(10):
        print(f"    '{glyph_char}' = {count} ({100*count/total:.1f}%)")

    print(f"\n  Unique FG colors: {len(fg_colors)}")
    for color, count in fg_colors.most_common(5):
        print(f"    #{color[0]:02x}{color[1]:02x}{color[2]:02x} = {count}")
    if len(fg_colors) > 5:
        print(f"    ... and {len(fg_colors) - 5} more")

    print(f"\n  Unique BG colors: {len(bg_colors)}")
    for color, count in bg_colors.most_common(5):
        print(f"    #{color[0]:02x}{color[1]:02x}{color[2]:02x} = {count}")
    if len(bg_colors) > 5:
        print(f"    ... and {len(bg_colors) - 5} more")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an .xp sprite file: layer dimensions, metadata, palette analysis."
    )
    parser.add_argument("file", type=Path, help="Path to .xp file")
    parser.add_argument("--layer", "-l", type=int, default=None,
                        help="Inspect only this layer index (default: all layers)")
    parser.add_argument("--palette", "-p", action="store_true",
                        help="Show full palette analysis per layer")
    parser.add_argument("--summary", "-s", action="store_true",
                        help="Quick summary only (skip per-layer cell analysis)")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 1

    try:
        xp = XPFile(str(args.file))
    except Exception as e:
        print(f"ERROR: failed to load {args.file}: {e}", file=sys.stderr)
        return 1

    print(f"File: {args.file}")
    print(f"Version: {xp.version}")
    print(f"Layers: {len(xp.layers)}")

    # Per-layer dimensions
    print(f"\nLayer dimensions:")
    for i, layer in enumerate(xp.layers):
        role = {
            0: "metadata/colorkey",
            1: "height",
            2: "visual",
        }.get(i, f"layer_{i}")
        print(f"  [{i}] {layer.width}x{layer.height}  ({role})")

    # Metadata from Layer 0
    if len(xp.layers) >= 1:
        try:
            meta = xp.get_metadata()
            print(f"\nMetadata (Layer 0): {meta}")
        except Exception:
            print("\nMetadata (Layer 0): <could not extract>")
            meta = None

    if args.summary:
        return 0

    # Per-layer cell analysis
    if args.layer is not None:
        if args.layer < 0 or args.layer >= len(xp.layers):
            print(f"ERROR: layer index {args.layer} out of range (0..{len(xp.layers)-1})", file=sys.stderr)
            return 1
        inspect_layer(xp, args.layer)
    elif args.palette:
        for i in range(len(xp.layers)):
            inspect_layer(xp, i)
    else:
        # Default: inspect layers 0, 1, 2 at minimum
        default_layers = list(range(min(3, len(xp.layers))))
        for i in default_layers:
            inspect_layer(xp, i)
        if len(xp.layers) > 3:
            print(f"\n  ... {len(xp.layers) - 3} more layers (use --palette to show all)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
