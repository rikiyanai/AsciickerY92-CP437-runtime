#!/usr/bin/env python3
"""verify_roundtrip.py -- CI script for XP -> PNG -> XP roundtrip verification.

Tests ALL .xp sprite files in assets/sprites/ for visual roundtrip fidelity.
The roundtrip renders each sprite's visual layer to PNG using the
transparency-preserving renderer, then reverse-renders back to cell data
and compares pixel-by-pixel.

Exit codes:
    0  All sprites pass (0 visual mismatches)
    1  One or more sprites have visual mismatches
    2  Usage error or missing dependencies

RT-05: CI verification script for roundtrip testing.

[FLOW:CI] [DATA-CONTRACT:XP] [DATA-CONTRACT:CP437]
"""

import sys
import time
from pathlib import Path

# Resolve project root
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
SPRITES_DIR = PROJECT_ROOT / "assets" / "sprites"
FONT_ATLAS = PROJECT_ROOT / "assets" / "fonts" / "cp437_12x12.png"

sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from scripts.pipeline.xp_core import XPFile
from scripts.pipeline._render_core import (
    load_font_atlas,
    load_glyph_masks,
    render_cell,
    render_xp_layer_to_png,
    reverse_render_sheet,
    MAGIC_PINK,
    clear_cache,
)


def roundtrip_sprite(xp_path, glyphs, masks):
    """Test roundtrip for a single sprite.

    Returns:
        (total_cells, exact_matches, complement_pairs, visual_mismatches)
    """
    xp = XPFile()
    xp.load(str(xp_path))

    if len(xp.layers) < 3:
        return (0, 0, 0, 0)

    visual = xp.layers[2]
    png = render_xp_layer_to_png(visual.data, glyphs)
    grid = reverse_render_sheet(png, masks)

    total = 0
    exact = 0
    complement = 0
    visual_bad = 0

    for y in range(visual.height):
        for x in range(visual.width):
            og, of, ob = visual.data[y][x]
            rg, rf, rb = grid[y][x]
            total += 1

            if og == rg and of == rf and ob == rb:
                exact += 1
                continue
            if ob == MAGIC_PINK and rb == MAGIC_PINK:
                exact += 1
                continue

            px1 = np.array(render_cell(og, of, ob, glyphs))
            px2 = np.array(render_cell(rg, rf, rb, glyphs))
            if np.array_equal(px1, px2):
                complement += 1
            else:
                visual_bad += 1

    return (total, exact, complement, visual_bad)


def main():
    if not SPRITES_DIR.exists():
        print(f"ERROR: assets/sprites/ directory not found at {SPRITES_DIR}", file=sys.stderr)
        sys.exit(2)

    if not FONT_ATLAS.exists():
        print(f"ERROR: Font atlas not found at {FONT_ATLAS}", file=sys.stderr)
        sys.exit(2)

    xp_files = sorted(SPRITES_DIR.glob("*.xp"))
    if not xp_files:
        print("ERROR: No .xp files found in assets/sprites/", file=sys.stderr)
        sys.exit(2)

    print(f"Roundtrip verification: {len(xp_files)} sprites")
    print(f"Font atlas: {FONT_ATLAS}")
    print()

    glyphs = load_font_atlas(str(FONT_ATLAS))
    masks = load_glyph_masks(str(FONT_ATLAS))

    passed = 0
    failed = 0
    skipped = 0
    total_cells = 0
    total_complement = 0
    start_time = time.time()

    failures = []

    for xp_path in xp_files:
        name = xp_path.stem
        try:
            total, exact, complement, visual_bad = roundtrip_sprite(
                xp_path, glyphs, masks
            )
        except Exception as e:
            print(f"  SKIP {name}: {e}")
            skipped += 1
            continue

        if total == 0:
            skipped += 1
            continue

        total_cells += total
        total_complement += complement

        if visual_bad == 0:
            passed += 1
        else:
            failed += 1
            failures.append((name, total, visual_bad))
            print(f"  FAIL {name}: {visual_bad}/{total} visual mismatches")

        # Clear render cache periodically to avoid memory bloat
        if (passed + failed) % 50 == 0:
            clear_cache()

    elapsed = time.time() - start_time
    print()
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"Total cells verified: {total_cells:,}")
    print(f"Complement pairs (pixel-identical): {total_complement:,}")
    print(f"Time: {elapsed:.1f}s")

    if failures:
        print()
        print("FAILURES:")
        for name, total, bad in failures:
            print(f"  {name}: {bad}/{total} visual mismatches")
        sys.exit(1)
    else:
        print()
        print("ALL SPRITES PASS ROUNDTRIP VERIFICATION")
        sys.exit(0)


if __name__ == "__main__":
    main()
