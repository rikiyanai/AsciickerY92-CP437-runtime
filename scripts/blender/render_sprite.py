#!/usr/bin/env python3
"""
Compatibility wrapper for render_sprite.py.

This script forwards to render_unified.py with sprite-path defaults.
Used by generator.py subprocess path. Do NOT modify the CLI interface.

DEPRECATION: This wrapper exists for backwards compatibility.
New code should call render_unified.py directly.

ARCHITECTURE:
  This wrapper preserves the EXACT CLI interface that generator.py depends on:

    blender -b <scene.blend> -P scripts/blender/render_sprite.py -- \
        --output <dir> --object <name> [options]

  It maps to render_unified.py with sprite-path defaults:
    - output_mode: frames (individual PNGs)
    - camera_mode: manual (rotation_euler, not TRACK_TO)
    - camera_z_ratio: 0.0 (no Z elevation)
    - freestyle: from CLI flag
    - compositor: off

RELATIONSHIP TO PIPELINE:
  This is the script that generator.py calls in the standard subprocess path:

    cli.py / pipeline.py
      -> generator.py  [PIPELINE:GENERATE]
          -> (subprocess) render_sprite.py   ** THIS FILE **
      -> generator.py collects output PNGs and stitches into sprite sheet

  [DEPENDENCY:BLENDER]      -- Requires bpy (Blender Python API).
  [PIPELINE:GENERATE]       -- Produces raw frame PNGs for generator.py.
  [DATA-CONTRACT:XP]        -- Upstream of the XP sprite sheet.
  [DATA-CONTRACT:ASSET-DEF] -- CLI args map 1:1 to AssetDef fields.
"""

import sys
import os

# Add directory to path for local imports
sys.path.insert(0, os.path.dirname(__file__))

from render_unified import main as unified_main
import argparse


def get_args():
    """Parse sprite-path CLI arguments.

    [DATA-CONTRACT:ASSET-DEF] Each argument maps 1:1 to an AssetDef field.

    WHY the -- check: When Blender is invoked as
    blender -b -P script.py -- --output foo, everything before -- is
    consumed by Blender itself. We only parse arguments *after* the separator
    so that Blender flags (like -b) do not confuse argparse.

    Returns:
        argparse.Namespace: Parsed arguments with fields output, object,
        resolution, grid_w, grid_h, angles, freestyle.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Output directory for frames")
    parser.add_argument("--object", required=True, help="Name of the object/collection to render")
    parser.add_argument("--resolution", type=int, default=12, help="Pixels per cell (default 12)")
    parser.add_argument("--grid-w", type=int, default=8, help="Width in cells")
    parser.add_argument("--grid-h", type=int, default=8, help="Height in cells")
    parser.add_argument("--angles", type=int, default=8, help="Number of angles")
    parser.add_argument("--freestyle", action="store_true", help="Enable freestyle outlines")
    parser.add_argument("--keyframe-ranges", type=str, default=None,
                        help="JSON array of per-animation keyframe ranges (forwarded to render_unified)")

    # WHY: Extract only the portion of sys.argv after "--" to avoid parsing
    # Blender's own flags (e.g. -b, -P) which would cause argparse errors.
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
        return parser.parse_args(argv)
    else:
        # Default or error if running without args
        return parser.parse_args([])


def main():
    """Main entry point - forwards to render_unified.py with sprite defaults."""
    args = get_args()

    # Build unified args with sprite-path defaults
    unified_argv = [
        "--output", args.output,
        "--object", args.object,
        "--resolution", str(args.resolution),
        "--grid-w", str(args.grid_w),
        "--grid-h", str(args.grid_h),
        "--angles", str(args.angles),
        "--output-mode", "frames",      # sprite: individual PNGs
        "--camera-mode", "manual",      # legacy: rotation_euler
        "--camera-z-ratio", "0.0",      # no elevation
    ]

    if args.freestyle:
        unified_argv.append("--freestyle")

    # Forward keyframe ranges to unified renderer (BLEND-15-03)
    if args.keyframe_ranges:
        unified_argv.extend(["--keyframe-ranges", args.keyframe_ranges])

    # Replace sys.argv and call unified core
    sys.argv = ["render_unified.py", "--"] + unified_argv
    unified_main()


if __name__ == "__main__":
    main()
