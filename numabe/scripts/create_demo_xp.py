#!/usr/bin/env python3
"""
Simple demo: Create a minimal XP file manually.

This shows the XP format structure without dependencies.
"""

from xp_core import XPLayer, XPFile
import sys
import os


def create_simple_xp():
    """Create a minimal XP file with metadata."""

    print("=" * 60)
    print("DEMO: Create Minimal XP File")
    print("=" * 60)

    # Create XP file
    xp = XPFile()
    xp.version = -1  # REXPaint current version

    # Create layer (10×10 grid)
    width = 10
    height = 10

    # Initialize layer data
    layer_data = [
        [(32, (0, 0, 0), (0, 0, 0)) for _ in range(width)] for _ in range(height)
    ]

    # Write metadata in Layer 0 first row
    # Cell (0,0): Angles = 8
    angle_glyph = ord("8")  # ASCII '8'
    layer_data[0][0] = (angle_glyph, (255, 255, 255), (0, 0, 0))

    # Cells (1..N, 0): Animation frame counts
    # Anim 0: 4 frames -> '4'
    # Anim 1: 4 frames -> '4'
    layer_data[0][1] = (ord("4"), (255, 255, 255), (0, 0, 0))
    layer_data[0][2] = (ord("4"), (255, 255, 255), (0, 0, 0))

    # Add some "character" data
    # Draw a simple '@' character
    layer_data[5][5] = (ord("@"), (255, 255, 255), (0, 0, 0))  # Center

    # Add some variety
    chars = ["#", "-", "|", "/", "\\", "X", "+", "*"]
    for i in range(8):
        layer_data[3][1 + i] = (ord(chars[i]), (200, 200, 200), (0, 0, 0))

    # Create layer
    layer = XPLayer(width, height, layer_data)
    xp.layers.append(layer)

    # Save
    output = "demo_simple.xp"
    xp.save(output)

    print(f"✓ Created: {output}")
    print(f"  Dimensions: {width}×{height}")
    print(f"  Metadata: angles=8, anims=[4, 4]")
    print()

    # Verify by loading
    xp_loaded = XPFile(output)
    metadata = xp_loaded.get_metadata()

    print("Verification:")
    print(f"  Version: {xp_loaded.version}")
    print(f"  Layers: {len(xp_loaded.layers)}")
    print(f"  Angles: {metadata['angles']}")
    print(f"  Animations: {metadata['anims']}")

    # Show some cells
    print()
    print("Sample cells:")
    for y in range(min(5, height)):
        row = []
        for x in range(min(10, width)):
            glyph, fg, bg = xp_loaded.layers[0].data[y][x]
            char = chr(glyph) if 32 <= glyph <= 126 else "?"
            row.append(char)
        print(f"  Row {y}: {' '.join(row)}")


if __name__ == "__main__":
    # Make sure we're in project root
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    print(f"Working directory: {os.getcwd()}")
    print(f"XP file path: {root}/xp_core.py")

    try:
        create_simple_xp()
        print()
        print("=" * 60)
        print("✓ Demo complete!")
        print("=" * 60)
        print()
        print(f"You can now: python xp_tool.py")
        print(f"then load: demo_simple.xp")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
