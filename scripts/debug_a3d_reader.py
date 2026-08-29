#!/usr/bin/env python3
"""
A3D File Binary Analyzer
Comprehensive inspection of exported A3D file structure
"""
import struct
import sys
from pathlib import Path

def analyze_a3d(filepath):
    """Comprehensive A3D file analysis"""
    try:
        with open(filepath, 'rb') as f:
            # Header
            sig = f.read(4)
            header_size, num_patches, reserved = struct.unpack('<III', f.read(12))

            print(f"=== A3D FILE ANALYSIS: {filepath} ===")
            print(f"Signature: {sig}")
            print(f"Patches: {num_patches}")
            print()

            # Analyze all patches
            patch_coords = []
            height_stats = {'min': 65535, 'max': 0, 'zero_count': 0}
            visual_stats = {}

            for i in range(num_patches):
                px, py = struct.unpack('<ii', f.read(8))
                patch_coords.append((px, py))

                # Visual data (8x8)
                visual_data = []
                for _ in range(64):
                    v = struct.unpack('<H', f.read(2))[0]
                    visual_data.append(v)
                    mat_id = v & 0xFF
                    visual_stats[mat_id] = visual_stats.get(mat_id, 0) + 1

                # Height data (5x5)
                heights = []
                for _ in range(25):
                    h = struct.unpack('<H', f.read(2))[0]
                    heights.append(h)
                    if h == 0:
                        height_stats['zero_count'] += 1
                    height_stats['min'] = min(height_stats['min'], h)
                    height_stats['max'] = max(height_stats['max'], h)

                diag = struct.unpack('<H', f.read(2))[0]

                # Report first few patches in detail
                if i < 3:
                    print(f"Patch {i}: ({px}, {py})")
                    print(f"  Heights: {heights}")
                    print(f"  Visual (first 8): {visual_data[:8]}")
                    print()

            print(f"Coordinate range:")
            print(f"  X: [{min(p[0] for p in patch_coords)}, {max(p[0] for p in patch_coords)}]")
            print(f"  Y: [{min(p[1] for p in patch_coords)}, {max(p[1] for p in patch_coords)}]")
            print()

            print(f"Height statistics:")
            print(f"  Min: {height_stats['min']}")
            print(f"  Max: {height_stats['max']}")
            print(f"  Zero heights: {height_stats['zero_count']}/{num_patches * 25}")
            print()

            print(f"Material distribution:")
            for mat_id in sorted(visual_stats.keys()):
                print(f"  Material {mat_id}: {visual_stats[mat_id]} cells")
            print()

            # Check spawn coverage
            spawn_x, spawn_y = 0, 15
            spawn_patch_x = spawn_x // 8
            spawn_patch_y = spawn_y // 8
            if (spawn_patch_x, spawn_patch_y) in patch_coords:
                print(f"✓ Spawn point ({spawn_x}, {spawn_y}) covered by patch ({spawn_patch_x}, {spawn_patch_y})")
            else:
                print(f"✗ Spawn point ({spawn_x}, {spawn_y}) NOT covered! Expected patch ({spawn_patch_x}, {spawn_patch_y})")
                nearby = [(x, y) for x, y in patch_coords if abs(x - spawn_patch_x) <= 1 and abs(y - spawn_patch_y) <= 1]
                print(f"  Nearby patches: {nearby}")

            print()

            # --- SKIP MATERIALS BLOCK ---
            # Fixed size: 256 * 512 bytes = 131072
            MATERIAL_BLOCK_SIZE = 131072
            current_pos = f.tell()
            f.seek(current_pos + MATERIAL_BLOCK_SIZE)

            # --- READ INSTANCES ---
            if f.tell() < 10000000: # Sanity check for file size
                try:
                    inst_ver = struct.unpack('<i', f.read(4))[0]
                    print(f"Instance Version: {inst_ver} (Signed)")

                    if inst_ver == -1:
                        num_inst = struct.unpack('<I', f.read(4))[0]
                        print(f"Instance Count: {num_inst}")

                        for k in range(min(num_inst, 5)): # Show first 5
                            # Mesh Name
                            mn_len = struct.unpack('<I', f.read(4))[0]
                            mesh_name = f.read(mn_len).decode('utf-8')

                            # Inst Name
                            in_len = struct.unpack('<I', f.read(4))[0]
                            inst_name = f.read(in_len).decode('utf-8')

                            # Transform (16 doubles = 128 bytes)
                            f.read(128)

                            # Flags, StoryID
                            flags, story_id = struct.unpack('<II', f.read(8))

                            print(f"  [{k}] Name: '{inst_name}' | Source: '{mesh_name}' | ID: {story_id}")
                except struct.error:
                    print("End of file reached before/during Instance block.")

    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        repo_root = Path(__file__).resolve().parents[1]
        filepath = str(repo_root / 'assets/a3d/game_map_y8.a3d')

    analyze_a3d(filepath)
