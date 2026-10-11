#!/usr/bin/env python3
"""
Compatibility wrapper for render_turntable.py.

This script forwards to render_unified.py with turntable-path defaults.
Used by walk_anim_tool.py and blender_render.py.

DEPRECATION: This wrapper exists for backwards compatibility.
New code should call render_unified.py directly.

ARCHITECTURE:
  This wrapper preserves the CLI interface used by existing callers:

    blender -b <scene.blend> -P scripts/blender/render_turntable.py -- \
        --object <name> [options]

  It maps to render_unified.py with turntable-path defaults:
    - output_mode: sheet (stitched sprite sheet)
    - camera_mode: track_to (TRACK_TO constraint)
    - camera_z_ratio: 0.5 (slight isometric elevation)
    - freestyle: off
    - compositor: off

RELATIONSHIP TO PIPELINE:
  This is the older/standalone turntable renderer:

    cli.py / pipeline.py
      -> generator.py  [PIPELINE:GENERATE]
          -> (subprocess) render_sprite.py   (primary path)
          -> (subprocess) render_turntable.py ** THIS FILE ** (legacy/walk-anim path)

  [DEPENDENCY:BLENDER]  -- Requires bpy (Blender Python API).
  [DEPENDENCY:PIL]      -- Pillow for alpha-to-magenta and stitching (in render_unified).
  [PIPELINE:GENERATE]   -- Produces stitched sprite sheets for walk_anim_tool.py.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from render_unified import main as unified_main
import argparse


def get_args():
    """Parse turntable CLI arguments.

    WHY the -- check: When Blender is invoked as
    blender -b -P script.py -- --object Cube, everything before -- is
    consumed by Blender itself.

    Returns:
        argparse.Namespace: Parsed arguments with fields object, angles,
        output, scale.
    """
    parser = argparse.ArgumentParser(description="Render turntable sprites")
    parser.add_argument("--object", default="Cube", help="Object name to render")
    parser.add_argument("--angles", type=int, default=8, help="Number of angles")
    parser.add_argument("--output", default="output", help="Output directory")
    parser.add_argument("--scale", type=int, default=4, help="Scale factor")

    if "--" in sys.argv:
        return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    return parser.parse_args([])


def main():
    """Main entry point - forwards to render_unified.py with turntable defaults."""
    args = get_args()

    # Compute grid from scale (legacy heuristic)
    # Original: cell_size = 12 * scale_factor
    resolution = 12 * args.scale

    unified_argv = [
        "--output", args.output,
        "--object", args.object,
        "--angles", str(args.angles),
        "--resolution", str(resolution),
        "--output-mode", "sheet",       # turntable: stitched sheet
        "--camera-mode", "track_to",    # modern: TRACK_TO constraint
        "--camera-z-ratio", "0.5",      # isometric elevation
    ]

    sys.argv = ["render_unified.py", "--"] + unified_argv
    unified_main()


# Backwards-compatible exports for direct imports
def convert_alpha_to_magenta(input_path, output_path):
    """
    Deprecated: Use render_unified.convert_alpha_to_magenta instead.

    Convert alpha channel to magenta background.

    [DEPENDENCY:PIL] Pillow for pixel-level array manipulation.
    [PIPELINE:GENERATE] Post-render step bridging Blender's RGBA output to
    the engine's magenta-key transparency convention.

    WHY magenta keying instead of alpha:
      The Asciicker engine's C++ renderer uses a hard-coded magenta color key
      (R=255, G=0, B=255) for transparency rather than an alpha channel.

    Args:
        input_path: Path to RGBA PNG rendered by Blender.
        output_path: Path for output magenta-keyed RGB PNG.

    Returns:
        None. Writes the converted image to output_path.
    """
    from render_unified import convert_alpha_to_magenta as unified_convert
    return unified_convert(input_path, output_path)


def stitch_sprite_sheet(frame_paths, output_path, grid_w, grid_h):
    """
    Deprecated: Use render_unified.stitch_sprite_sheet instead.

    Stitch individual frames into a single sprite sheet.

    [DEPENDENCY:PIL] Pillow for image composition.
    [PIPELINE:GENERATE] Final assembly step within this script.

    Args:
        frame_paths: List of filesystem paths to individual frame PNGs.
        output_path: Filesystem path for the stitched sprite sheet PNG.
        grid_w: Characters wide per frame.
        grid_h: Characters tall per frame.

    Returns:
        str: output_path on success.

    Raises:
        ValueError: If frame_paths is empty.
    """
    from render_unified import stitch_sprite_sheet as unified_stitch
    return unified_stitch(frame_paths, output_path, grid_w, grid_h)


def render_turntable(object_name, angles=8, output_dir="output", scale_factor=4):
    """
    Deprecated: Use render_unified.render or main() instead.

    Render object at multiple angles (turntable).

    [DEPENDENCY:BLENDER] Core render loop using bpy.ops.render.
    [PIPELINE:GENERATE] This is the main entry point for producing raw sprite
    frames from a 3D object.

    Args:
        object_name: Name of the Blender object to render (must exist in scene).
        angles: Number of equidistant turntable angles (typically 8).
        output_dir: Filesystem directory for output PNG frames.
        scale_factor: Multiplier applied to the base 12px cell size.

    Returns:
        List[str]: Filesystem paths to the rendered PNG frames, one per angle.
    """
    # Forward to unified main() with appropriate argv
    import sys
    old_argv = sys.argv
    try:
        resolution = 12 * scale_factor
        sys.argv = [
            "render_unified.py", "--",
            "--output", output_dir,
            "--object", object_name,
            "--angles", str(angles),
            "--resolution", str(resolution),
            "--output-mode", "sheet",
            "--camera-mode", "track_to",
            "--camera-z-ratio", "0.5",
        ]
        unified_main()
        # Return frame paths from output_dir
        import os
        frames = sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.startswith("sprite_") and f.endswith(".png")
        ])
        return frames
    finally:
        sys.argv = old_argv


def setup_scene(clear=True):
    """
    Deprecated: Use render_unified setup functions instead.

    Set up a clean scene for rendering.
    """
    import bpy
    if clear:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

    if "RenderCollection" not in bpy.data.collections:
        collection = bpy.data.collections.new("RenderCollection")
        bpy.context.scene.collection.children.link(collection)

    return bpy.data.collections["RenderCollection"]


def setup_camera(width_px, height_px, distance=10):
    """
    Deprecated: Use render_unified setup functions instead.

    Set up orbiting camera at fixed distance from origin.
    """
    import bpy
    cam_data = bpy.data.cameras.new("RenderCam")
    cam_obj = bpy.data.objects.new("RenderCam", cam_data)
    scene = bpy.context.scene
    scene.collection.objects.link(cam_obj)

    scene.camera = cam_obj

    cam_obj.location = (0, distance, 0)
    cam_obj.rotation_euler = (0, 0, 0)

    scene.render.resolution_x = width_px
    scene.render.resolution_y = height_px
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0

    return cam_obj


def setup_lighting():
    """
    Deprecated: Use render_unified setup functions instead.

    Set up 3-point lighting with ambient.
    """
    import bpy
    scene = bpy.context.scene

    # Clear existing lights
    for obj in scene.objects:
        if obj.type == "LIGHT":
            scene.collection.objects.unlink(obj)

    # Key light (front-right, slightly above)
    key_data = bpy.data.lights.new("KeyLight", type="POINT")
    key_obj = bpy.data.objects.new("KeyLight", key_data)
    scene.collection.objects.link(key_obj)
    key_obj.location = (3, 4, 5)
    key_data.energy = 1000
    key_data.color = (1, 1, 0.95)

    # Fill light (front-left, slightly above)
    fill_data = bpy.data.lights.new("FillLight", type="POINT")
    fill_obj = bpy.data.objects.new("FillLight", fill_data)
    scene.collection.objects.link(fill_obj)
    fill_obj.location = (-3, 4, 5)
    fill_data.energy = 500
    fill_data.color = (0.9, 0.9, 1)

    # Rim light (back, high up)
    rim_data = bpy.data.lights.new("RimLight", type="POINT")
    rim_obj = bpy.data.objects.new("RimLight", rim_data)
    scene.collection.objects.link(rim_obj)
    rim_obj.location = (0, -5, 8)
    rim_data.energy = 400
    rim_data.color = (1, 1, 1)

    scene.world = bpy.data.worlds.new("RenderWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes["Background"]
    bg.inputs["Strength"].default_value = 0.15

    return (key_obj, fill_obj, rim_obj)


def setup_object(object_name):
    """
    Deprecated: Use render_unified setup functions instead.

    Load or find the named object and prepare it for turntable rendering.
    """
    import bpy
    scene = bpy.context.scene
    collection = bpy.data.collections.get("RenderCollection", scene.collection)

    obj = scene.objects.get(object_name)
    if not obj:
        raise ValueError(f"Object '{object_name}' not found in scene")

    if obj.name not in collection.objects:
        collection.objects.link(obj)

    obj.location = (0, 0, 0)

    return obj


def setup_fresnel_material(obj):
    """
    Deprecated: Use render_unified setup functions instead.

    Add fresnel shader to object for better edge definition.
    """
    pass


if __name__ == "__main__":
    main()
