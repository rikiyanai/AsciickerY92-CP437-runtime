"""
Unified Blender Render Script — Canonical render core for the asset pipeline.

This is the **canonical render implementation** that consolidates the duplicated
logic from render_sprite.py and render_turntable.py.  Those scripts are now
compatibility wrappers that invoke this unified core.

ARCHITECTURE:
  This script runs **inside Blender's embedded Python interpreter** (bpy API).
  It is invoked via subprocess:

      blender -b <scene.blend> -P scripts/blender/render_unified.py -- <args>

  The ``--`` separator ensures that everything after it is passed to this
  script's ``argparse`` parser, not to Blender's own CLI.

USAGE:
  Two primary output modes:

  1. **frames mode** (default) — Renders individual PNG files:
     blender -b scene.blend -P render_unified.py -- \\
         --output output_dir --object MyObject --output-mode frames

     Produces: angle_0_frame_0001.png, angle_1_frame_0001.png, etc.

  2. **sheet mode** — Renders frames then stitches into sprite sheet:
     blender -b scene.blend -P render_unified.py -- \\
         --output output_dir --object MyObject --output-mode sheet

     Produces: individual frames + sprite_sheet.png

CLI FLAGS:
  --output <dir>        : Output directory (required)
  --object <name>       : Blender object name (required)
  --angles <int>        : Number of turntable angles (default 8)
  --resolution <int>    : Pixels per cell (default 12)
  --grid-w <int>        : Width in cells (default 8)
  --grid-h <int>        : Height in cells (default 8)
  --output-mode <mode>  : "frames" or "sheet" (default "frames")
  --camera-mode <mode>  : "track_to" or "manual" (default "track_to")
  --camera-z-ratio <float> : Z elevation as ratio of distance (default 0.0)
  --freestyle           : Enable Freestyle line art (default OFF)
  --compositor          : Enable compositor node tree (default OFF)
  --line-thickness <float> : Freestyle line thickness (default 1.0)

CAMERA MODES:
  - **track_to** (recommended): Uses TRACK_TO constraint for robust aiming.
    Camera automatically aims at target object regardless of orbit position.

  - **manual**: Uses rotation_euler for backwards compatibility with
    render_sprite.py.  Camera rotation computed manually per angle.

CAMERA ORBIT MATH:
  For angle index *i* of *N* total angles:

      theta = i * (360 / N)
      cam.x =  distance * sin(theta)
      cam.y = -distance * cos(theta)   # negative so angle 0 = front view
      cam.z =  distance * camera_z_ratio

  Where distance = 10 Blender units (orthographic projection makes exact
  distance irrelevant for framing; ortho_scale controls size).

FILENAME CONTRACT:
  Frame mode produces files named: ``angle_<N>_frame_<MMMM>.png``
  where N = angle index (0-based) and MMMM = frame number (zero-padded).
  This naming is required by generator.py downstream parser.

RELATIONSHIP TO PIPELINE:
  This is the canonical render core invoked by:
    - render_sprite.py (wrapper for generator.py subprocess path)
    - render_turntable.py (wrapper for walk_anim_tool.py / manual use)

  [DEPENDENCY:BLENDER]  -- Requires ``bpy`` (Blender Python API).
  [DEPENDENCY:PIL]      -- Pillow required for sheet mode stitching.
  [PIPELINE:GENERATE]   -- Produces raw frame PNGs for asset pipeline.
  [DATA-CONTRACT:XP]    -- Upstream of XP sprite sheet generation.
"""

import bpy
import json
import math
import os
import sys
import argparse
from math import radians, sin, cos


def get_args():
    """Parse command-line arguments from after the ``--`` separator.

    WHY the ``--`` check: When Blender is invoked as
    ``blender -b -P script.py -- --output foo``, everything before ``--`` is
    consumed by Blender itself.  We only parse arguments *after* the separator
    so that Blender flags (like ``-b``) do not confuse argparse.

    Returns:
        argparse.Namespace: Parsed arguments with all CLI flags.
    """
    parser = argparse.ArgumentParser(
        description="Unified Blender render script for asset pipeline"
    )

    # Required arguments
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for frames or sprite sheet"
    )
    parser.add_argument(
        "--object",
        required=True,
        help="Name of the Blender object/collection to render"
    )

    # Render settings
    parser.add_argument(
        "--angles",
        type=int,
        default=8,
        help="Number of turntable angles (default 8)"
    )
    parser.add_argument(
        "--resolution",
        type=int,
        default=12,
        help="Pixels per cell (default 12)"
    )
    parser.add_argument(
        "--grid-w",
        type=int,
        default=8,
        help="Width in cells (default 8)"
    )
    parser.add_argument(
        "--grid-h",
        type=int,
        default=8,
        help="Height in cells (default 8)"
    )

    # Output mode
    parser.add_argument(
        "--output-mode",
        choices=["frames", "sheet"],
        default="frames",
        help="Output mode: frames (individual PNGs) or sheet (stitched sprite sheet)"
    )

    # Camera settings
    parser.add_argument(
        "--camera-mode",
        choices=["track_to", "manual"],
        default="track_to",
        help="Camera aiming mode: track_to (constraint) or manual (rotation_euler)"
    )
    parser.add_argument(
        "--camera-z-ratio",
        type=float,
        default=0.0,
        help="Z elevation as ratio of distance (0.0 = no elevation, 0.5 = slight isometric)"
    )

    # Visual effects
    parser.add_argument(
        "--freestyle",
        action="store_true",
        help="Enable Freestyle line art rendering"
    )
    parser.add_argument(
        "--compositor",
        action="store_true",
        help="Enable compositor node tree"
    )
    parser.add_argument(
        "--line-thickness",
        type=float,
        default=1.0,
        help="Freestyle line thickness when enabled (default 1.0)"
    )

    # Keyframe range support (BLEND-15-03)
    parser.add_argument(
        "--keyframe-ranges",
        type=str,
        default=None,
        help=(
            "JSON array of per-animation keyframe ranges. "
            'Each entry: {"start": int, "end": int, "count": int, "name": str}. '
            "When provided, renders each range separately instead of sequential frames. "
            "Backward compatible: omit for sequential render from scene frame_start to frame_end."
        ),
    )

    # Extract arguments after "--" separator
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
        return parser.parse_args(argv)
    else:
        # Default or error if running without args
        return parser.parse_args([])


def setup_scene(args):
    """Configure Blender's render settings for sprite output.

    [DEPENDENCY:BLENDER] Modifies ``bpy.context.scene.render`` properties.
    [PIPELINE:GENERATE] Render dimensions directly determine the pixel size of
    each output frame.

    Resolution math:
      ``px_w = grid_w * resolution``  (e.g. 8 cells * 12 px/cell = 96 px)
      ``px_h = grid_h * resolution``

    WHY film_transparent: Renders the background as alpha=0 instead of a solid
    color.  Post-processing later composites onto magenta for the engine's
    transparency key.

    Args:
        args: Parsed CLI arguments from get_args().

    Returns:
        bpy.types.Scene: The active scene with updated render settings.
    """
    scene = bpy.context.scene

    # Resolution calculation
    px_w = args.grid_w * args.resolution
    px_h = args.grid_h * args.resolution

    scene.render.resolution_x = px_w
    scene.render.resolution_y = px_h
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True

    # Image format
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.compression = 85

    # Freestyle line art (optional)
    scene.render.use_freestyle = args.freestyle
    if args.freestyle:
        scene.render.line_thickness = args.line_thickness

    # Compositor (optional)
    scene.use_nodes = args.compositor

    return scene


def setup_camera(target_obj, args):
    """Set up or retrieve the specialized render camera.

    [DEPENDENCY:BLENDER] Creates/reuses a camera object via bpy.data API.
    [PIPELINE:GENERATE] Camera type and ortho_scale directly affect how much
    of the object is visible in each frame.

    Configures the camera as **Orthographic** to match the pixel-art aesthetic
    (no perspective distortion).

    Camera placement:
      Initial position at (0, -10, 0). The render loop will update location
      per-angle based on camera_mode setting.

    Ortho Scale heuristic:
      ``ortho_scale = grid_h * 0.35``

      WHY 0.35: A standard humanoid character in Blender is ~2m tall.  With
      ``grid_h = 8`` cells, ``ortho_scale = 8 * 0.35 = 2.8`` Blender units,
      which frames a 2m character with ~0.4 units of headroom on each side.

    Args:
        target_obj: The Blender object to frame (used for TRACK_TO constraint).
        args: Parsed CLI arguments; uses ``args.grid_h`` for scale heuristic.

    Returns:
        bpy.types.Object: The camera object, configured as orthographic.
    """
    # Check for existing camera or create new
    cam_name = "RenderCamera"
    if cam_name in bpy.data.objects:
        cam_obj = bpy.data.objects[cam_name]
    else:
        cam_data = bpy.data.cameras.new(cam_name)
        cam_obj = bpy.data.objects.new(cam_name, cam_data)
        bpy.context.scene.collection.objects.link(cam_obj)

    bpy.context.scene.camera = cam_obj

    # Configure as orthographic
    cam_obj.data.type = 'ORTHO'
    cam_obj.data.ortho_scale = args.grid_h * 0.35

    # Initial position (render loop will update per angle)
    cam_obj.location = (0, -10, 0)

    # Camera aiming mode
    if args.camera_mode == "track_to":
        # Use TRACK_TO constraint for robust aiming
        # Clear existing constraints first
        cam_obj.constraints.clear()

        cns = cam_obj.constraints.new('TRACK_TO')
        cns.target = target_obj
        cns.track_axis = 'TRACK_NEGATIVE_Z'
        cns.up_axis = 'UP_Y'
    elif args.camera_mode == "manual":
        # Manual rotation mode (backwards compatibility)
        # Clear constraints
        cam_obj.constraints.clear()

        # Base rotation: 90 degrees around X to look horizontally
        cam_obj.rotation_euler = (radians(90), 0, 0)

    return cam_obj


def setup_lighting():
    """Set up 3-point lighting with ambient.

    [DEPENDENCY:BLENDER] Creates POINT lights and world background node via bpy.

    WHY 3-point lighting:
      The classic key/fill/rim arrangement produces clear silhouettes that
      survive aggressive downsampling to the pixel-art cell grid.

    Returns:
        Tuple of (key_obj, fill_obj, rim_obj) light objects.
    """
    scene = bpy.context.scene

    # Clear existing lights
    for obj in list(scene.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)

    # Key light (front-right, slightly above)
    key_data = bpy.data.lights.new("KeyLight", type="POINT")
    key_obj = bpy.data.objects.new("KeyLight", key_data)
    scene.collection.objects.link(key_obj)
    key_obj.location = (3, 4, 5)
    key_data.energy = 1000
    key_data.color = (1, 1, 0.95)  # Slightly warm

    # Fill light (front-left, slightly above)
    fill_data = bpy.data.lights.new("FillLight", type="POINT")
    fill_obj = bpy.data.objects.new("FillLight", fill_data)
    scene.collection.objects.link(fill_obj)
    fill_obj.location = (-3, 4, 5)
    fill_data.energy = 500
    fill_data.color = (0.9, 0.9, 1)  # Slightly cool

    # Rim light (back, high up)
    rim_data = bpy.data.lights.new("RimLight", type="POINT")
    rim_obj = bpy.data.objects.new("RimLight", rim_data)
    scene.collection.objects.link(rim_obj)
    rim_obj.location = (0, -5, 8)
    rim_data.energy = 400
    rim_data.color = (1, 1, 1)  # Pure white

    # World background for ambient
    if not scene.world:
        scene.world = bpy.data.worlds.new("RenderWorld")
    scene.world.use_nodes = True
    if "Background" in scene.world.node_tree.nodes:
        bg = scene.world.node_tree.nodes["Background"]
        bg.inputs["Strength"].default_value = 0.15  # Low ambient

    return (key_obj, fill_obj, rim_obj)


def _parse_keyframe_ranges(json_str):
    """Parse --keyframe-ranges JSON string into a list of dicts.

    Each dict has keys: start (int), end (int), count (int), name (str).
    Returns None if json_str is None or empty.

    Raises:
        ValueError: If JSON is malformed or entries lack required keys.
    """
    if not json_str:
        return None
    try:
        ranges = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid --keyframe-ranges JSON: {e}") from e

    if not isinstance(ranges, list) or len(ranges) == 0:
        raise ValueError("--keyframe-ranges must be a non-empty JSON array")

    for i, r in enumerate(ranges):
        for key in ("start", "end", "count"):
            if key not in r:
                raise ValueError(
                    f"Keyframe range [{i}] missing required key '{key}'"
                )
    return ranges


def _render_angle_frames(cam, scene, args, angle_idx, start_frame, end_frame, frame_offset=0):
    """Render frames for one camera angle over a keyframe range.

    Positions camera at the orbit angle, then renders each frame between
    start_frame and end_frame (inclusive), distributing evenly across the
    range when count < (end - start + 1).

    Args:
        cam: Camera object.
        scene: Active Blender scene.
        args: Parsed CLI arguments.
        angle_idx: Camera angle index (0-based).
        start_frame: First keyframe (inclusive).
        end_frame: Last keyframe (inclusive).
        frame_offset: Column offset for output filename numbering.

    Returns:
        List[str]: Rendered frame paths.
    """
    distance = 10  # Fixed orbit radius in Blender units
    angle_deg = angle_idx * (360.0 / args.angles)
    angle_rad = radians(angle_deg)

    # Position camera
    cam.location.x = distance * sin(angle_rad)
    cam.location.y = -distance * cos(angle_rad)
    cam.location.z = distance * args.camera_z_ratio

    if args.camera_mode == "manual":
        cam.rotation_euler = (radians(90), 0, angle_rad)

    bpy.context.view_layer.update()

    frame_paths = []
    for frame in range(start_frame, end_frame + 1):
        scene.frame_set(frame)
        output_idx = frame_offset + (frame - start_frame)
        filename = f"angle_{angle_idx}_frame_{output_idx:04d}.png"
        filepath = os.path.join(args.output, filename)
        scene.render.filepath = filepath
        bpy.ops.render.render(write_still=True)
        frame_paths.append(filepath)
        print(f"Rendered {filepath}")

    return frame_paths


def render_turntable_frames(obj, cam, args):
    """Orbit the camera and render every (angle, frame) pair.

    [DEPENDENCY:BLENDER] Uses bpy.ops.render and scene frame control.
    [PIPELINE:GENERATE] Core render loop -- produces the individual frame PNGs.

    Supports two modes:
      1. **Keyframe ranges** (--keyframe-ranges): Renders each animation range
         separately, producing output frames numbered sequentially across ranges.
      2. **Sequential** (default): Renders scene.frame_start to scene.frame_end.

    Camera orbit math:
      For angle index *i* out of *N*::

          theta  = i * (360 / N)
          cam.x  =  10 * sin(theta)    # orbit radius = 10 BU
          cam.y  = -10 * cos(theta)    # -cos so angle 0 = front
          cam.z  =  10 * camera_z_ratio

    Output naming:
      ``angle_<N>_frame_<MMMM>.png``
      This naming convention is required by generator.py downstream parser.

    Args:
        obj: The Blender object being rendered.
        cam: The camera object.
        args: Parsed CLI arguments with ``angles``, ``output``, etc.

    Returns:
        List[str]: Filesystem paths to rendered PNG frames.
    """
    scene = bpy.context.scene

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Parse keyframe ranges if provided
    keyframe_ranges = _parse_keyframe_ranges(
        getattr(args, "keyframe_ranges", None)
    )

    frame_paths = []

    if keyframe_ranges:
        # Per-range render mode: render each animation range separately
        print(f"Keyframe range mode: {len(keyframe_ranges)} ranges")
        for angle_idx in range(args.angles):
            frame_offset = 0
            for kr in keyframe_ranges:
                name = kr.get("name", "")
                ks = kr["start"]
                ke = kr["end"]
                count = kr["count"]
                print(
                    f"  Angle {angle_idx}: range '{name}' "
                    f"keyframes {ks}-{ke}, {count} output frames"
                )
                paths = _render_angle_frames(
                    cam, scene, args, angle_idx,
                    start_frame=ks,
                    end_frame=ke,
                    frame_offset=frame_offset,
                )
                frame_paths.extend(paths)
                frame_offset += (ke - ks + 1)
    else:
        # Sequential fallback: render from scene frame_start to frame_end
        start_frame = scene.frame_start
        end_frame = scene.frame_end

        for angle_idx in range(args.angles):
            paths = _render_angle_frames(
                cam, scene, args, angle_idx,
                start_frame=start_frame,
                end_frame=end_frame,
                frame_offset=0,
            )
            frame_paths.extend(paths)

    return frame_paths


def convert_alpha_to_magenta(input_path, output_path):
    """Convert alpha channel to magenta background.

    [DEPENDENCY:PIL] Pillow for pixel-level array manipulation.
    [PIPELINE:GENERATE] Post-render step bridging Blender's RGBA output to
    the engine's magenta-key transparency convention.

    WHY magenta keying instead of alpha:
      The Asciicker engine uses a hard-coded magenta color key (R=255, G=0, B=255)
      for transparency rather than an alpha channel.  All transparent pixels must
      be converted from RGBA alpha=0 to solid magenta.

    Args:
        input_path: Path to RGBA PNG rendered by Blender.
        output_path: Path for output magenta-keyed RGB PNG.

    Returns:
        str: output_path on success.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        raise ImportError(
            "PIL (Pillow) and NumPy are required for alpha-to-magenta conversion. "
            "Install with: pip install Pillow numpy (or in Blender's Python)"
        )

    img = Image.open(input_path)

    # Convert RGBA to RGB with magenta for transparency
    if img.mode == "RGBA":
        img_array = np.array(img)

        # Mask fully transparent pixels
        mask = img_array[:, :, 3] == 0
        img_array[mask, 0] = 255  # R
        img_array[mask, 1] = 0    # G
        img_array[mask, 2] = 255  # B
        img_array[:, :, 3] = 255  # Set alpha to fully opaque

        # Drop alpha channel
        img_rgb = Image.fromarray(img_array[:, :, :3].astype(np.uint8))
        img_rgb.save(output_path)
    else:
        # Already RGB, just copy
        img.save(output_path)

    return output_path


def stitch_sprite_sheet(frame_paths, output_path):
    """Stitch individual frames into a single sprite sheet.

    [DEPENDENCY:PIL] Pillow for image composition.
    [PIPELINE:GENERATE] Final assembly step -- produces the single-image sprite
    sheet that downstream pipeline stages expect.

    Layout: max 8 columns (angles), rows wrap for additional frames.
    Background: magenta (255, 0, 255) for transparency key.

    Args:
        frame_paths: List of filesystem paths to individual frame PNGs.
        output_path: Filesystem path for the stitched sprite sheet PNG.

    Returns:
        str: output_path on success.

    Raises:
        ValueError: If frame_paths is empty.
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError(
            "PIL (Pillow) is required for sprite sheet stitching. "
            "Install with: pip install Pillow (or in Blender's Python)"
        )

    if not frame_paths:
        raise ValueError("No frames to stitch")

    # Load all frames
    frames = [Image.open(p) for p in frame_paths]

    # Get dimensions
    frame_w, frame_h = frames[0].size

    # Calculate grid layout (max 8 columns)
    n_frames = len(frames)
    cols = min(8, n_frames)
    rows = (n_frames + cols - 1) // cols

    # Create sheet with magenta background
    sheet = Image.new("RGB", (cols * frame_w, rows * frame_h), (255, 0, 255))

    # Paste frames left-to-right, top-to-bottom
    for i, frame in enumerate(frames):
        x = (i % cols) * frame_w
        y = (i // cols) * frame_h
        sheet.paste(frame, (x, y))

    sheet.save(output_path)
    return output_path


def render(args):
    """Main render entry point.

    Orchestrates the full render pipeline based on args.output_mode:
    - frames: Render individual PNGs only
    - sheet: Render frames + convert to magenta + stitch into sprite sheet

    Args:
        args: Parsed CLI arguments from get_args().

    Returns:
        dict: Summary of rendered output with keys:
            - mode: "frames" or "sheet"
            - frame_paths: List of rendered frame paths
            - sheet_path: Path to stitched sheet (sheet mode only)
    """
    # Get target object
    obj = bpy.data.objects.get(args.object)
    if not obj:
        raise ValueError(f"Object '{args.object}' not found in scene")

    # Setup scene, camera, lighting
    setup_scene(args)
    cam = setup_camera(obj, args)
    setup_lighting()

    # Render turntable frames
    frame_paths = render_turntable_frames(obj, cam, args)

    result = {
        "mode": args.output_mode,
        "frame_paths": frame_paths,
    }

    # Sheet mode: convert alpha and stitch
    if args.output_mode == "sheet":
        # Convert all frames to magenta-keyed
        magenta_paths = []
        for path in frame_paths:
            magenta_path = path.replace(".png", "_magenta.png")
            convert_alpha_to_magenta(path, magenta_path)
            magenta_paths.append(magenta_path)

        # Stitch into sprite sheet
        sheet_path = os.path.join(args.output, "sprite_sheet.png")
        stitch_sprite_sheet(magenta_paths, sheet_path)

        result["sheet_path"] = sheet_path
        print(f"Sprite sheet: {sheet_path}")

    return result


def main():
    """Main execution when run as Blender script.

    [DEPENDENCY:BLENDER] Intended to be called via:
        ``blender -b <scene.blend> -P render_unified.py -- <args>``

    [PIPELINE:GENERATE] Full render pipeline entry point.
    """
    if "--" not in sys.argv:
        print("Error: No arguments provided after '--' separator")
        print("Usage: blender -b scene.blend -P render_unified.py -- --output <dir> --object <name>")
        sys.exit(1)

    args = get_args()
    result = render(args)

    print(f"\nRender complete:")
    print(f"  Mode: {result['mode']}")
    print(f"  Frames: {len(result['frame_paths'])}")
    if "sheet_path" in result:
        print(f"  Sheet: {result['sheet_path']}")


if __name__ == "__main__":
    main()
