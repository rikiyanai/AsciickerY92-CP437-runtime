#!/usr/bin/env python3
"""
Demo script: Generate a test asset and convert to XP format.

This shows the complete pipeline:
1. Load PNG sprite sheet
2. Slice into frames
3. Process (quantize + glyph match)
4. Assemble to XP file
5. Load and verify
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from asset_gen.slicer import ImageSlicer
from asset_gen.processor import SpriteProcessor
from asset_gen.assembler import XPAssembler
from xp_core import XPFile

def main():
    print("=" * 60)
    print("DEMO: Generate Test Asset and Convert to XP Format")
    print("=" * 60)
    print()

    # Example 1: Use AI sprite sheet
    fixture_path = 'tests/fixtures/ai_sprite_sheet.png'
    
    if not os.path.exists(fixture_path):
        print(f"Creating simple test fixture...")
        os.makedirs('tests/fixtures', exist_ok=True)
        from PIL import Image
        # Create simple 12×12 magenta image
        img = Image.new('RGB', (12, 12), (255, 0, 255))
        img.save(fixture_path)
        print(f"✓ Created {fixture_path}")
    
    print()
    print("Step 1: Load PNG Sprite Sheet")
    print("-" * 60)
    img = Image.open(fixture_path)
    print(f"✓ Loaded: {img.width}×{img.height}")
    
    print()
    print("Step 2: Setup Asset Definition")
    print("-" * 60)
    # Simple 1×1 character, 4 frames, 1 anim
    asset_type = {
        'name': 'test_character',
        'size': (1, 1),  # 1 char wide, 1 char tall
        'type': 'character',
        'angles': 1,
        'animations': [{'name': 'idle', 'frames': 4}]
    }
    print(f"Asset name: {asset_type['name']}")
    print(f"Size: {asset_type['size']} chars = {asset_type['size'][0]*12}×{asset_type['size'][1]*12} pixels")
    print(f"Angles: {asset_type['angles']}")
    print(f"Animations: {sum(a['frames'] for a in asset_type['animations'])} frames")
    
    print()
    print("Step 3: Slice into Frames")
    print("-" * 60)
    slicer = ImageSlicer()
    # Reshape for single column x multiple rows for our demo
    frames = slicer.slice_image(img, asset_type)
    print(f"✓ Sliced into {len(frames)} frames")
    
    print()
    print("Step 4: Process (Quantize + Glyph Match)")
    print("-" * 60)
    processor = SpriteProcessor()
    processed_frames = []
    for i, frame in enumerate(frames):
        processed = processor.process_image(frame, type('Asset', (), asset_type))
        processed_frames.append(processed)
        print(f"✓ Processed frame {i+1}/{len(frames)}")
    
    print()
    print("Step 5: Assemble to XP File")
    print("-" * 60)
    assembler = XPAssembler()
    metadata = {
        'angles': asset_type['angles'],
        'anims': [a['frames'] for a in asset_type['animations']]
    }
    
    output_path = 'demo_output.xp'
    assembler.assemble(processed_frames, metadata, output_path)
    print(f"✓ Saved to: {output_path}")
    
    print()
    print("Step 6: Verify XP File")
    print("-" * 60)
    xp = XPFile(output_path)
    print(f"✓ Loaded XP file")
    print(f"  Version: {xp.version}")
    print(f"  Layers: {len(xp.layers)}")
    
    # Extract metadata
    xp_metadata = xp.get_metadata()
    print(f"  Angles: {xp_metadata['angles']}")
    print(f"  Animations: {xp_metadata['anims']}")
    
    if xp.layers:
        layer = xp.layers[0]
        print(f"  Layer dimensions: {layer.width}×{layer.height}")
        sample_cell = layer.data[0][0] if layer.height > 0 and layer.width > 0 else None
        if sample_cell:
            glyph, fg, bg = sample_cell
            print(f"  Sample cell (0,0): glyph={glyph} (chr={chr(glyph) if 32 <= glyph <= 126 else '?'}), " +
                  f"fg={fg}, bg={bg}")
    
    print()
    print("Step 8: Display First 10 cells (if applicable)")
    print("-" * 60)
    if xp.layers and xp.layers[0]:
        layer = xp.layers[0]
        count = 0
        for y in range(layer.height):
            for x in range(layer.width):
                if count >= 10:
                    break
                glyph, fg, bg = layer.data[y][x]
                char = chr(glyph) if 32 <= glyph <= 126 else '?'
                print(f"  Cell ({x},{y}): '{char}' (glyph={glyph:3}), fg=({fg[0]:3},{fg[1]:3},{fg[2]:3})")
                count += 1
    
    print()
    print("=" * 60)
    print("DEMO COMPLETE!")
    print("=" * 60)
    print()
    print(f"Generated file: {os.path.abspath(output_path)}")
    print()
    print("You can now:")
    print("  • Open this .xp file in xp_tool.py for visual verification")
    print("  • Use it in the Asciicker game engine")
    print("  • Load it programmatically with xp_core.py")
    print()

if __name__ == '__main__':
    main()
