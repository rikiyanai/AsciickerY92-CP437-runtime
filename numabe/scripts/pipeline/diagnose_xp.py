#!/usr/bin/env python3
"""
Diagnostic script to confirm two suspected root causes in XP pipeline:
1. projs mismatch: metadata says projs=2 for angles>0, but sheet width doesn't include reflections
2. Transparency threshold: cells that are 80-95% magenta fail the 100% threshold

Usage:
    python3 diagnose_xp.py <path_to_xp_file>

Output: Evidence for both hypotheses with actionable data.
"""

import sys
import gzip
import struct
from pathlib import Path
import numpy as np

MAGENTA_RGB = (255, 0, 255)


def read_xp_file(path: Path):
    """Read XP file and return layers."""
    with gzip.open(path, 'rb') as f:
        version = struct.unpack('<i', f.read(4))[0]
        num_layers = struct.unpack('<I', f.read(4))[0]

        layers = []
        for _ in range(num_layers):
            width = struct.unpack('<I', f.read(4))[0]
            height = struct.unpack('<I', f.read(4))[0]

            # Column-major storage
            data = [[None] * width for _ in range(height)]
            for x in range(width):
                for y in range(height):
                    glyph = struct.unpack('<I', f.read(4))[0]
                    fg_r, fg_g, fg_b = struct.unpack('BBB', f.read(3))
                    bg_r, bg_g, bg_b = struct.unpack('BBB', f.read(3))
                    data[y][x] = (glyph, (fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b))

            layers.append({'width': width, 'height': height, 'data': data})

        return layers


def get_digit(cell):
    """Decode CP437 digit glyph to int."""
    glyph = cell[0]
    if 48 <= glyph <= 57:  # '0'-'9'
        return glyph - 48
    if 65 <= glyph <= 90:  # 'A'-'Z'
        return glyph + 10 - 65
    if 97 <= glyph <= 122:  # 'a'-'z'
        return glyph + 10 - 97
    return -1


def diagnose_projs_mismatch(layers):
    """
    Hypothesis 1: projs=2 for angles>0 but sheet has only sum(anims) columns.

    Expected: sheet_width = frame_width * sum(anims) * projs
    Actual:   sheet_width = frame_width * sum(anims)

    If projs=2 and expected != actual, the viewer will render half-width frames.
    """
    print("\n" + "="*60)
    print("HYPOTHESIS 1: projs mismatch")
    print("="*60)

    l0 = layers[0]
    visual = layers[2] if len(layers) > 2 else layers[0]

    # Extract metadata
    raw_angles = get_digit(l0['data'][0][0])
    projs = 2 if raw_angles > 0 else 1
    angles = raw_angles if raw_angles > 0 else 1

    # Extract anims
    anims = []
    for a in range(1, l0['width']):
        length = get_digit(l0['data'][0][a])
        if length > 0:
            anims.append(length)
        else:
            break

    total_cols = sum(anims) if anims else 1

    # Compute expected frame width
    # If projs=2, engine expects: visual.width = frame_w * total_cols * projs
    # So: frame_w = visual.width / (total_cols * projs)
    expected_frame_w_with_projs = visual['width'] / (total_cols * projs)
    expected_frame_w_no_projs = visual['width'] / total_cols

    print(f"  Metadata extracted:")
    print(f"    raw_angles = {raw_angles}")
    print(f"    angles     = {angles}")
    print(f"    projs      = {projs}")
    print(f"    anims      = {anims}")
    print(f"    total_cols = {total_cols}")
    print(f"")
    print(f"  Sheet dimensions:")
    print(f"    width      = {visual['width']}")
    print(f"    height     = {visual['height']}")
    print(f"")
    print(f"  Frame width calculations:")
    print(f"    If projs accounted for: frame_w = {visual['width']} / ({total_cols} * {projs}) = {expected_frame_w_with_projs}")
    print(f"    If projs ignored:       frame_w = {visual['width']} / {total_cols} = {expected_frame_w_no_projs}")

    if projs == 2:
        # Check if sheet actually has reflection columns (doubled width)
        # If frame_w with projs accounting is a valid integer, geometry is correct
        if expected_frame_w_with_projs == int(expected_frame_w_with_projs) and expected_frame_w_with_projs > 0:
            print(f"")
            print(f"  ✓ projs=2 and sheet geometry is consistent with reflections.")
            print(f"    Frame width = {expected_frame_w_with_projs} (with projs accounting)")
            return False  # No mismatch
        else:
            print(f"")
            print(f"  ⚠️  GEOMETRY ERROR:")
            print(f"      projs=2 but frame width is {expected_frame_w_with_projs} (not integer).")
            print(f"      Sheet width may not be properly doubled for reflections.")
            if expected_frame_w_no_projs == int(expected_frame_w_no_projs):
                print(f"      Without projs: frame_w = {expected_frame_w_no_projs} (valid).")
                print(f"      Likely cause: projs=2 in metadata but reflections not generated.")
            return True
    else:
        print(f"  ✓ projs=1, no mismatch expected.")
        return False


def diagnose_transparency_threshold(layers):
    """
    Hypothesis 2: LANCZOS resampling degrades magenta, causing transparency check to fail.

    The processor uses: if transparent_count == 144: # All 144 pixels
    But anti-aliased edges mean cells are 80-95% magenta, not 100%.
    """
    print("\n" + "="*60)
    print("HYPOTHESIS 2: Transparency threshold too strict")
    print("="*60)

    visual = layers[2] if len(layers) > 2 else layers[0]

    # Count cells by their bg color and fg==bg situations
    magenta_bg_count = 0
    black_bg_with_magenta_fg = 0  # The problematic artifact
    total_non_space = 0

    fg_eq_bg_magenta = 0  # fg==bg==MAGENTA cells (should be transparent)
    fg_eq_bg_other = 0    # fg==bg!=MAGENTA cells (solid color, ok)

    for y in range(visual['height']):
        for x in range(visual['width']):
            glyph, fg, bg = visual['data'][y][x]

            if glyph != 32:  # Not a space
                total_non_space += 1

            if bg == MAGENTA_RGB:
                magenta_bg_count += 1

            # Check for the problematic fallback: fg==MAGENTA, bg==BLACK
            if fg == MAGENTA_RGB and bg == (0, 0, 0):
                black_bg_with_magenta_fg += 1

            # Check fg==bg situations
            if fg == bg:
                if fg == MAGENTA_RGB:
                    fg_eq_bg_magenta += 1
                else:
                    fg_eq_bg_other += 1

    total_cells = visual['width'] * visual['height']

    print(f"  Cell analysis:")
    print(f"    Total cells:              {total_cells}")
    print(f"    Magenta background:       {magenta_bg_count} ({100*magenta_bg_count/total_cells:.1f}%)")
    print(f"    Non-space glyphs:         {total_non_space}")
    print(f"")
    print(f"  Artifact detection:")
    print(f"    fg==bg==MAGENTA:          {fg_eq_bg_magenta} (should be transparent but may show as artifact)")
    print(f"    fg==bg (other colors):    {fg_eq_bg_other} (solid color blocks, normal)")
    print(f"    fg=MAGENTA, bg=BLACK:     {black_bg_with_magenta_fg} ← ARTIFACT from fallback")

    if black_bg_with_magenta_fg > 0:
        print(f"")
        print(f"  ⚠️  ARTIFACT DETECTED:")
        print(f"      {black_bg_with_magenta_fg} cells have fg=MAGENTA, bg=BLACK.")
        print(f"      This is the fg==bg==MAGENTA fallback creating visible artifacts.")
        print(f"      These cells should be transparent (glyph=32, bg=MAGENTA).")
        return True
    else:
        print(f"  ✓ No fg=MAGENTA, bg=BLACK artifacts found.")
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 diagnose_xp.py <path_to_xp_file>")
        sys.exit(1)

    xp_path = Path(sys.argv[1])
    if not xp_path.exists():
        print(f"Error: {xp_path} not found")
        sys.exit(1)

    print(f"Diagnosing: {xp_path}")

    try:
        layers = read_xp_file(xp_path)
        print(f"Loaded {len(layers)} layers")
    except Exception as e:
        print(f"Error reading XP file: {e}")
        sys.exit(1)

    projs_issue = diagnose_projs_mismatch(layers)
    threshold_issue = diagnose_transparency_threshold(layers)

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    if projs_issue:
        print("  ❌ Hypothesis 1 CONFIRMED: projs mismatch")
        print("     Fix: Either set projs=1 in metadata OR generate reflection columns")
    else:
        print("  ✓ Hypothesis 1 not confirmed")

    if threshold_issue:
        print("  ❌ Hypothesis 2 CONFIRMED: fg==bg==MAGENTA fallback to black")
        print("     Fix: When fg==bg==MAGENTA, emit transparent cell instead of black bg")
    else:
        print("  ✓ Hypothesis 2 not confirmed")

    if not projs_issue and not threshold_issue:
        print("  Neither hypothesis confirmed - investigate other causes")


if __name__ == "__main__":
    main()
