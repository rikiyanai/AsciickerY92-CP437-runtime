"""
Debug sprite viewer/analyzer for XP files.

Loads a .xp file and prints a comprehensive diagnostic report covering:
  - Layer 0 metadata (angles, animation frame counts) decoded from glyph values
  - Per-layer bounding-box analysis and transparency statistics
  - ASCII art preview of the top-left 24x16 region of each content layer

Intended for interactive debugging during sprite authoring -- run from the
command line with a single .xp path to quickly check whether an exported
sprite encodes the expected structure.

ARCHITECTURE:
    Standalone diagnostic script.  Reads a .xp file via ``xp_core.XPFile``,
    iterates every cell to compute statistics, and prints to stdout.
    No files are written; no images are generated.

KEY EXPORTS:
    - debug_xp_file(filepath): Print full diagnostic report for one .xp file

PIPELINE CONTEXT:
    [PIPELINE:PROCESS]   -- Used after the PROCESS or ASSEMBLE stage to
        inspect .xp output without opening REXPaint.
    [DEPENDENCY:PIL]     -- Imported but not actively used in current code;
        kept for potential future image-preview expansion.
    [DEPENDENCY:XP_CORE] -- Loads XP files via ``xp_core.XPFile``.
    [DATA-CONTRACT:XP]   -- Reads .xp files conforming to the REXPaint binary
        format: gzip-compressed, with Layer 0 encoding metadata as CP437 glyphs.
"""

import sys
import os
from PIL import Image

# WHY: xp_core lives in the parent scripts/ directory, not in asset_gen/.
# This path manipulation is needed because debug_sprite.py can be invoked
# directly from the command line, not only as a package import.
# TODO(PIPELINE-FIX): sys.path.append is fragile -- if this script is
# invoked from a different working directory, __file__ resolves to
# asset_gen/ not scripts/.  Should use explicit relative import or
# install xp_core as a package.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from xp_core import XPFile

def debug_xp_file(filepath):
    """
    Print a comprehensive diagnostic report for a single .xp file.

    Inspects every layer, decoding metadata from Layer 0 and computing
    per-layer transparency statistics and content bounding boxes.
    Prints an ASCII art preview of each layer's top-left 24x16 region.

    Args:
        filepath: Path string to a .xp file to analyze.

    Returns:
        None. All output is printed to stdout.

    Raises:
        No exceptions are raised; all errors are caught and printed.
    """
    print(f"DEBUGGING: {filepath}")
    if not os.path.exists(filepath):
        print("File not found!")
        return

    try:
        xp = XPFile()
        xp.load(filepath)
    except Exception as e:
        print(f"Failed to load XP: {e}")
        return

    # 1. Metadata Check
    print("\n--- LAYER 0: METADATA ---")
    if len(xp.layers) > 0:
        l0 = xp.layers[0]
        print(f"Layer 0 Size: {l0.width}x{l0.height}")
        
        # WHY: Metadata values (angles, frame counts) are stored in Layer 0
        # as CP437 glyph indices.  Digits '0'-'9' (ASCII 48-57) encode 0-9;
        # uppercase letters 'A'-'Z' (ASCII 65-90) encode 10-35.  This gives
        # a single-character encoding for values 0..35.
        def decode_glyph(g):
            """Decode a CP437 glyph index to its encoded metadata value.

            Digits '0'-'9' map to 0-9, uppercase 'A'-'Z' map to 10-35.
            Returns '?' for any glyph outside these ranges.

            Args:
                g: Integer glyph index (CP437 code point).

            Returns:
                Decoded integer value (0-35) or the string '?' if unrecognized.
            """
            if 48 <= g <= 57: return g - 48
            if 65 <= g <= 90: return g + 10 - 65
            return "?"

        if l0.width > 0 and l0.height > 0:
            angles_g = l0.data[0][0][0]
            angles = decode_glyph(angles_g)
            print(f"Angles (0,0): {angles} (Glyph {angles_g})")
            
            anims = []
            for x in range(1, min(20, l0.width)):
                g = l0.data[0][x][0]
                val = decode_glyph(g)
                if val != "?": anims.append(val)
            print(f"Anims (1..N,0): {anims}")
        else:
            print("Layer 0 is empty!")
    else:
        print("No layers found!")

    # 2. Layer Analysis
    for i in range(1, len(xp.layers)):
        print(f"\n--- LAYER {i} ---")
        l = xp.layers[i]
        print(f"Layer Size: {l.width}x{l.height}")

        # Skip empty layers
        if l.width == 0 or l.height == 0:
            print("Layer is empty (0x0)!")
            continue
        
        # WHY: Three separate transparency heuristics are checked because
        # the XP format uses multiple conventions for "empty" cells:
        #   1. glyph==0: null glyph (REXPaint convention)
        #   2. glyph==32: space character (visual blank)
        #   3. bg==(255,0,255): magenta background (Asciicker transparency key)
        # All three must be checked to get an accurate content bounding box.
        min_x, min_y = l.width, l.height
        max_x, max_y = -1, -1
        trans_count = 0
        total_cells = l.width * l.height

        for y in range(l.height):
            for x in range(l.width):
                glyph, fg, bg = l.data[y][x]

                is_trans = False
                if glyph == 0 or glyph == 32:
                    is_trans = True
                elif bg == (255, 0, 255):
                    is_trans = True
                    
                if is_trans:
                    trans_count += 1
                else:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)

        print(f"Transparency: {trans_count}/{total_cells} ({trans_count/total_cells*100:.1f}%)")
        
        if max_x == -1:
            print("Layer appears Empty/Invisible!")
        else:
            print(f"Content Bounding Box: ({min_x},{min_y}) to ({max_x},{max_y})")
            
            # WHY: Preview is capped at 24x16 characters to fit in a standard
            # terminal window.  Non-printable glyphs (outside ASCII 32-126)
            # are replaced with '.' to avoid corrupting terminal output.
            # TODO(PIPELINE-FIX): Hardcoded 24x16 preview size; should be
            # configurable or auto-detect terminal width via shutil.get_terminal_size().
            print(f"Visual Preview (Top-Left 24x16):")
            for py in range(min(16, l.height)):
                line = ""
                for px in range(min(24, l.width)):
                    glyph, fg, bg = l.data[py][px]
                    char = chr(glyph) if 32 <= glyph <= 126 else "."
                    if glyph == 32 or glyph == 0: char = " "
                    line += char
                print(f"|{line}|")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        debug_xp_file(sys.argv[1])
    else:
        print("Usage: python3 scripts/debug_sprite.py <file.xp>")