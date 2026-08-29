#!/usr/bin/env python3
"""
Asciicker Asset Maker CLI

Main entry point for asset generation workflows:
- PNG files with magenta transparency
- Blender renders via MCP
- Sprite sheets
"""

import argparse
import sys
import shutil
import logging
from pathlib import Path
from dataclasses import asdict
from typing import Optional


# Set up logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Import staging utilities
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import progress, ok_item, fail_item  # noqa: E402

try:
    from asset_gen.staging import get_staging_dir, build_asset_paths, list_xp_files
except ImportError:
    from asset_gen.staging import get_staging_dir, build_asset_paths, list_xp_files

# Import asset generation modules
try:
    from asset_gen.schemas import AssetDef
    from asset_gen.presets import get_preset
    from asset_gen.pipeline import AssetPipeline
    from asset_gen.auto_adjust import run_auto_adjustments
except ImportError:
    # These will be implemented in future tasks
    print("Warning: asset_gen modules not fully implemented yet", file=sys.stderr)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Asciicker Asset Maker CLI",
        epilog="""
Examples:
  # Create asset from PNG with magenta transparency:
  python asset_maker.py from-png sprites.png --name orc --type character --angles 8

  # Process existing sprite sheet:
  python asset_maker.py from-sheet sprites.png --name item --type item

  # Render from Blender (later):
  python asset_maker.py from-blender --blender-object TestCube --name cube

  # View generated .xp file:
  python asset_maker.py view orc.xp

  # Generate debug sheet:
  python asset_maker.py debug orc.xp

  # Validate asset:
  python asset_maker.py validate orc.xp
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Subcommand: start (wizard mode - later)
    start_parser = subparsers.add_parser(
        "start",
        help="Start wizard for interactive asset creation",
        description="Interactive wizard for creating new assets",
    )
    start_parser.add_argument("--name", help="Asset name (optional)")
    start_parser.add_argument(
        "--type",
        choices=["character", "item"],
        help="Asset type (optional)",
    )
    start_parser.add_argument(
        "--angles",
        type=int,
        choices=[1, 4, 8],
        help="Number of angles (optional)",
    )

    # Subcommand: from-png
    from_png_parser = subparsers.add_parser(
        "from-png",
        help="Create asset from PNG file with magenta transparency",
        description="Process a PNG source file and generate .xp asset",
    )
    from_png_parser.add_argument("input", help="Input PNG file path")
    from_png_parser.add_argument("--name", required=True, help="Asset name")
    from_png_parser.add_argument(
        "--type",
        choices=["character", "item", "custom"],
        default="custom",
        help="Asset type preset",
    )
    from_png_parser.add_argument(
        "--angles", type=int, choices=[1, 4, 8], default=1, help="Number of angles"
    )
    from_png_parser.add_argument(
        "--frames",
        type=int,
        action="append",
        help="Frames per angle (repeat for multiple)",
    )
    from_png_parser.add_argument(
        "--transparency",
        action="store_true",
        help="Treat magenta (255,0,255) as transparent",
    )
    from_png_parser.add_argument(
        "--no-adjust",
        action="store_true",
        help="Skip auto-adjustments (magenta snap, quantize, grid validate, crop/center)",
    )

    # Subcommand: from-sheet
    from_sheet_parser = subparsers.add_parser(
        "from-sheet",
        help="Process existing sprite sheet",
        description="Create asset from pre-baked sprite sheet",
    )
    from_sheet_parser.add_argument("input", help="Input sprite sheet PNG")
    from_sheet_parser.add_argument("--name", required=True, help="Asset name")
    from_sheet_parser.add_argument(
        "--type",
        choices=["character", "item", "custom"],
        default="custom",
        help="Asset type",
    )
    from_sheet_parser.add_argument(
        "--angles", type=int, choices=[1, 4, 8], default=1, help="Number of angles"
    )
    from_sheet_parser.add_argument(
        "--frames",
        type=int,
        action="append",
        help="Frames per angle",
    )
    from_sheet_parser.add_argument(
        "--no-adjust",
        action="store_true",
        help="Skip auto-adjustments (magenta snap, quantize, grid validate, crop/center)",
    )

    # Subcommand: from-blender
    from_blender_parser = subparsers.add_parser(
        "from-blender",
        help="Render asset from Blender scene via MCP",
        description="Generate asset by rendering Blender objects",
    )
    from_blender_parser.add_argument("--name", required=True, help="Asset name")
    from_blender_parser.add_argument(
        "--type",
        choices=["character", "item", "custom"],
        default="custom",
        help="Asset type",
    )
    from_blender_parser.add_argument(
        "--blender-object", required=True, help="Blender object name"
    )
    from_blender_parser.add_argument(
        "--angles", type=int, choices=[1, 4, 8], default=8, help="Number of angles"
    )
    from_blender_parser.add_argument(
        "--no-adjust",
        action="store_true",
        help="Skip auto-adjustments (magenta snap, quantize, grid validate, crop/center)",
    )

    # Subcommand: mcp-session with subcommands
    mcp_parser = subparsers.add_parser(
        "mcp-session",
        help="Manage MCP server connection",
        description="Start or check MCP server status",
    )
    mcp_subparsers = mcp_parser.add_subparsers(
        dest="mcp_command", help="MCP session commands"
    )

    # mcp-session start
    mcp_start_parser = mcp_subparsers.add_parser(
        "start",
        help="Display MCP connection information",
        description="Show MCP server connection details and usage",
    )

    # mcp-session status
    mcp_status_parser = mcp_subparsers.add_parser(
        "status",
        help="Show MCP server status",
        description="Check if MCP server is running",
    )

    # mcp-session copy
    mcp_copy_parser = mcp_subparsers.add_parser(
        "copy",
        help="Copy file to staging directory",
        description="Copy specified file to staging/inputs/",
    )
    mcp_copy_parser.add_argument("input_path", help="Path to file to copy")

    # Subcommand: view
    view_parser = subparsers.add_parser(
        "view",
        help="View generated .xp file",
        description="Display .xp file as ASCII preview",
    )
    view_parser.add_argument("xp_file", help="Path to .xp file")
    view_parser.add_argument(
        "--output", help="Output PNG path (default: {name}_preview.png)"
    )

    # Subcommand: debug
    debug_parser = subparsers.add_parser(
        "debug",
        help="Generate debug visualization",
        description="Create labeled debug PNG sheet with indices",
    )
    debug_parser.add_argument("sheet_path", help="Path to sprite sheet PNG")

    # Subcommand: validate
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate .xp file format",
        description="Check .xp file structure and content",
    )
    validate_parser.add_argument("xp_file", help="Path to .xp file")

    # Subcommand: publish
    publish_parser = subparsers.add_parser(
        "publish",
        help="Publish .xp files to sprites directory",
        description="Publish assets from staging/xp/ to assets/sprites/ directory",
        epilog="""
Examples:
  # List available assets and interactively confirm:
  python asset_maker.py publish

  # Publish all assets without confirmation:
  python asset_maker.py publish --skip-confirmation

  # Publish specific files:
  python asset_maker.py publish character1.xp character2.xp
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    publish_parser.add_argument(
        "xp_file",
        nargs="*",
        help="Specific .xp files to publish (defaults to all in xp/)",
    )
    publish_parser.add_argument(
        "--skip-confirmation",
        action="store_true",
        help="Skip confirmation prompt and publish all",
    )

    return parser.parse_args()


def cmd_from_png(args):
    """Handle from-png command."""
    print(f"\n{progress(1, 4, 'Processing PNG:')} {args.input}")
    print(f"  Asset name: {args.name}")
    print(f"  Type: {args.type}")
    print(f"  Angles: {args.angles}")
    print(f"  Transparency: {args.transparency}")

    input_path = Path(args.input)

    # Validate input exists
    if not input_path.exists():
        print(fail_item(f"Error: Input file not found: {args.input}"), file=sys.stderr)
        return 1

    # Get staging paths
    paths = build_asset_paths(args.name)
    staging_xp_path = paths["xp_path"]

    print(f"\n{progress(1, 4, 'Preparing input...')}")
    print(f"  Input: {input_path}")
    print(f"  Output: {staging_xp_path}")

    # Prepare AssetDef
    asset_def = AssetDef(
        name=args.name,
        type=args.type,
        angles=args.angles,
        frames=[1] if not args.frames else args.frames,
        source_type="ai" if args.transparency else "file",
        source_path=str(input_path),
        transparency=args.transparency,
    )

    print(f"  Angles: {asset_def.angles}")
    print(f"  Frames: {asset_def.frames}")

    # Validate asset definition
    errors = asset_def.validate()
    if errors:
        print(fail_item("Validation errors:"), file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    # Apply transparency correction if needed
    if args.transparency:
        print(f"\n{progress(2, 4, 'Applying magenta transparency...')}")
        try:
            from asset_gen.color_correction import auto_magenta_correction
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent))
            from asset_gen.color_correction import auto_magenta_correction

        corrected_image = auto_magenta_correction(str(input_path))
        input_path = Path(corrected_image)
        print(f"  {ok_item(f'Corrected image: {corrected_image}')}")

    # Copy to staging inputs
    print(f"\n{progress(3, 5, 'Copying to staging...')}")
    import shutil

    staging_input_path = get_staging_dir("inputs") / input_path.name
    shutil.copy(input_path, staging_input_path)
    print(f"  {ok_item(f'Copied to: {staging_input_path}')}")

    # Auto-adjustments
    print(f"\n{progress(4, 5, 'Auto-adjustments...')}")
    if args.no_adjust:
        print(f"    Skipped (--no-adjust specified)")
    else:
        logger.info(
            f"Running auto-adjustments: magenta snap, quantize, grid validate, crop/center"
        )
        sheet_path = paths["sheet_path"]
        # Get debug path for auto_adjustments output
        staging_debug_path = paths["debug_path"]
        try:
            from asset_gen.auto_adjust import run_auto_adjustments
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent))
            from asset_gen.auto_adjust import run_auto_adjustments

        adjusted_path = run_auto_adjustments(
            img_path=input_path,
            config={
                "output_path": str(sheet_path),
                "angles": asset_def.angles,
                "frames": asset_def.frames,
            },
        )
        print(f"    {ok_item('Auto-adjustments applied')}")
        # Use adjusted sheet for pipeline
        pipeline_input = str(adjusted_path)
        print(f"    Using adjusted sheet: {adjusted_path}")

    # Run pipeline
    print(f"\n{progress(5, 5, 'Running pipeline...')}")
    try:
        from asset_gen.pipeline import AssetPipeline
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.pipeline import AssetPipeline

    pipeline_input = (
        str(pipeline_input) if "pipeline_input" in locals() else str(staging_input_path)
    )
    pipeline = AssetPipeline(asset_def, pipeline_input)
    pipeline.run()

    # Move output to correct location
    pipeline_output = f"scripts/{asset_def.name}.xp"
    if Path(pipeline_output).exists():
        staging_xp_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(pipeline_output, staging_xp_path)
        print(f"\n{ok_item(f'XP file generated: {staging_xp_path}')}")
    else:
        print(
            fail_item(f"Warning: Expected output not found: {pipeline_output}"),
            file=sys.stderr,
        )
        return 1

    print(f"\n{ok_item('All steps finished!')}")
    return 0


def cmd_from_sheet(args):
    """Handle from-sheet command."""
    print(f"\n{progress(1, 5, 'Processing sprite sheet:')} {args.input}")
    print(f"  Asset name: {args.name}")
    print(f"  Type: {args.type}")
    print(f"  Angles: {args.angles}")

    input_path = Path(args.input)

    # Validate input exists
    if not input_path.exists():
        print(fail_item(f"Error: Input file not found: {args.input}"), file=sys.stderr)
        return 1

    # Parse frames list
    if args.frames:
        frames = [int(f) for f in args.frames]
    else:
        # Default to single frame
        frames = [1]

    print(f"  Frames: {frames}")

    # Validate sheet dimensions
    print(f"\n{progress(2, 5, 'Validating sheet dimensions...')}")
    try:
        from asset_gen.validator import validate_sheet_specs, print_validation_report
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.validator import validate_sheet_specs, print_validation_report

    validation = validate_sheet_specs(input_path, args.angles, frames)
    if not validation["valid"]:
        print(fail_item("Sprite sheet validation failed:"), file=sys.stderr)
        print_validation_report(validation, "Sprite Sheet")
        return 1
    else:
        exp_w, exp_h, act_w, act_h = validation["dimensions"]
        print(f"  {ok_item(f'Sheet dimensions correct: {act_w}x{act_h}')}")

    print()

    # Get staging paths
    paths = build_asset_paths(args.name)
    staging_sheet_path = paths["sheet_path"]
    staging_xp_path = paths["xp_path"]

    # Copy to staging sheets
    print(f"{progress(3, 6, 'Copying to staging...')}")
    staging_sheet_path.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy(input_path, staging_sheet_path)
    print(f"  {ok_item(f'Copied to: {staging_sheet_path}')}")

    # Auto-adjustments
    print(f"\n{progress(4, 6, 'Auto-adjustments...')}")
    if args.no_adjust:
        print(f"    Skipped (--no-adjust specified)")
    else:
        logger.info(
            f"Running auto_adjustments on {staging_sheet_path}: magenta snap, quantize, grid validate, crop/center"
        )
        staging_debug_path = paths["debug_path"]
        try:
            from asset_gen.auto_adjust import run_auto_adjustments
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent))
            from asset_gen.auto_adjust import run_auto_adjustments

        adjusted_path = run_auto_adjustments(
            img_path=staging_sheet_path,
            config={
                "output_path": str(staging_sheet_path),
                "angles": args.angles,
                "frames": frames,
            },
        )
        print(f"    {ok_item('Auto-adjustments applied')}")
        # Adjusted sheet overwrites original for processing
        print(f"    Using adjusted sheet: {adjusted_path}")

    # Prepare AssetDef
    asset_def = AssetDef(
        name=args.name,
        type=args.type,
        angles=args.angles,
        frames=frames,
        source_type="file",
        source_path=str(staging_sheet_path),
        transparency=False,  # Assume no transparency for sheets (user should pre-snap)
    )

    # Slice frames
    print(f"\n{progress(4, 5, 'Slicing sprite sheet...')}")
    try:
        from asset_gen.slicer import ImageSlicer
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.slicer import ImageSlicer

    from PIL import Image

    img = Image.open(staging_sheet_path)
    slicer = ImageSlicer()
    frames_list = slicer.slice(img, asset_def.angles, asset_def.frames)
    print(f"  {ok_item(f'Sliced into {len(frames_list)} frames')}")

    # Process frames
    print(f"\n{progress(5, 5, 'Processing frames...')}")
    try:
        from processor_core import ImageProcessor
    except ImportError:
        from scripts.processor_core import ImageProcessor

    processor = ImageProcessor()
    processed_frames = []
    import tempfile
    import os

    for i, frame in enumerate(frames_list):
        # Save frame to temp file for processor
        temp_path = tempfile.mktemp(suffix=".png")
        frame.save(temp_path)

        # Process frame
        results = list(processor.process_image(temp_path))

        # Reconstruct 2D grid from flat iterator
        frame_width = frame.width // 12
        frame_height = frame.height // 12
        grid_data = [[None for _ in range(frame_width)] for _ in range(frame_height)]

        for grid_x, grid_y, glyph_idx, color_idx in results:
            grid_data[grid_y][grid_x] = (glyph_idx, color_idx, color_idx)

        processed_frames.append(grid_data)
        os.unlink(temp_path)

    print(f"  {ok_item(f'Processed {len(processed_frames)} frames')}")

    # Assemble to XP file
    print(f"\nAssembling to XP file...")
    try:
        from asset_gen.assembler import XPAssembler
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.assembler import XPAssembler

    assembler = XPAssembler()
    metadata = {"angles": asset_def.angles, "anims": asset_def.frames}
    staging_xp_path.parent.mkdir(parents=True, exist_ok=True)
    assembler.assemble(processed_frames, metadata, str(staging_xp_path))

    print(f"\n{ok_item(f'XP file generated: {staging_xp_path}')}")
    print(f"\n{ok_item('All steps finished!')}")
    return 0


def cmd_from_blender(args):
    """Handle from-blender command."""
    print(f"\n{progress(1, 5, 'Rendering from Blender:')} {args.blender_object}")
    print(f"  Asset name: {args.name}")
    print(f"  Type: {args.type}")
    print(f"  Angles: {args.angles}")
    print(f"  Resolution: 12")

    # Get staging paths
    paths = build_asset_paths(args.name)
    staging_render_path = paths["render_path"]
    staging_xp_path = paths["xp_path"]

    print(f"\n{progress(2, 5, 'Checking Blender availability...')}")
    try:
        from blender_render import render_character
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from blender_render import render_character

    print(f"  Rendering object: {args.blender_object}")
    print(f"  Output sprite: {staging_render_path}")

    # Render sprite sheet from Blender
    print(f"\n{progress(3, 5, 'Rendering sprite sheet...')}")
    try:
        render_sheet = render_character(
            object_name=args.blender_object,
            angles=args.angles,
            anims=[1],  # Default single animation for now
        )
        print(f"  {ok_item(f'Rendered to: {render_sheet}')}")
        # Move to staging location
        if render_sheet != str(staging_render_path):
            import shutil

            staging_render_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(render_sheet, staging_render_path)
            print(f"  {ok_item(f'Moved to: {staging_render_path}')}")
    except Exception as e:
        print(fail_item(f"Blender render failed: {e}"), file=sys.stderr)
        print(
            f"\nNote: Blender rendering requires Blender to be installed.",
            file=sys.stderr,
        )
        return 1

    # Prepare AssetDef
    print(f"\n{progress(4, 6, 'Preparing asset definition...')}")
    asset_def = AssetDef(
        name=args.name,
        type=args.type,
        angles=args.angles,
        frames=[1],  # Default single animation
        source_type="blender",
        source_path=str(staging_render_path),
        blender_object=args.blender_object,
        render_resolution=12,
    )

    # Auto-adjustments after render
    print(f"\n{progress(5, 6, 'Auto-adjustments...')}")
    if args.no_adjust:
        print(f"    Skipped (--no-adjust specified)")
    else:
        logger.info(
            f"Running auto_adjustments on rendered {staging_render_path}: magenta snap, quantize, grid validate, crop/center"
        )
        staging_debug_path = paths["debug_path"]
        try:
            from asset_gen.auto_adjust import run_auto_adjustments
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent))
            from asset_gen.auto_adjust import run_auto_adjustments

        adjusted_path = run_auto_adjustments(
            img_path=staging_render_path,
            config={
                "output_path": str(staging_render_path),
                "angles": asset_def.angles,
                "frames": asset_def.frames,
            },
        )
        print(f"    {ok_item('Auto-adjustments applied')}")
        render_sheet = str(adjusted_path)
        print(f"    Using adjusted render: {adjusted_path}")

    # Process rendered sheet
    print(f"\n{progress(6, 6, 'Processing rendered sheet...')}")
    from PIL import Image

    img = Image.open(render_sheet)

    # Slice frames
    try:
        from asset_gen.slicer import ImageSlicer
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.slicer import ImageSlicer

    slicer = ImageSlicer()
    frames_list = slicer.slice(img, asset_def.angles, asset_def.frames)
    print(f"  {ok_item(f'Sliced into {len(frames_list)} frames')}")

    # Process frames
    try:
        from processor_core import ImageProcessor
    except ImportError:
        from scripts.processor_core import ImageProcessor

    processor = ImageProcessor()
    processed_frames = []
    import tempfile
    import os

    for i, frame in enumerate(frames_list):
        # Save frame to temp file for processor
        temp_path = tempfile.mktemp(suffix=".png")
        frame.save(temp_path)

        # Process frame
        results = list(processor.process_image(temp_path))

        # Reconstruct 2D grid from flat iterator
        frame_width = frame.width // 12
        frame_height = frame.height // 12
        grid_data = [[None for _ in range(frame_width)] for _ in range(frame_height)]

        for grid_x, grid_y, glyph_idx, color_idx in results:
            grid_data[grid_y][grid_x] = (glyph_idx, color_idx, color_idx)

        processed_frames.append(grid_data)
        os.unlink(temp_path)

    print(f"  {ok_item(f'Processed {len(processed_frames)} frames')}")

    # Assemble to XP file
    print(f"\nAssembling to XP file...")
    try:
        from asset_gen.assembler import XPAssembler
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.assembler import XPAssembler

    assembler = XPAssembler()
    metadata = {"angles": asset_def.angles, "anims": asset_def.frames}
    staging_xp_path.parent.mkdir(parents=True, exist_ok=True)
    assembler.assemble(processed_frames, metadata, str(staging_xp_path))

    print(f"\n{ok_item(f'XP file generated: {staging_xp_path}')}")
    print(f"\n{ok_item('All steps finished!')}")
    return 0


def cmd_view(args):
    """Handle view command."""
    xp_path = Path(args.xp_file)

    print(f"\nViewing .xp file: {args.xp_file}")

    # Validate file exists
    if not xp_path.exists():
        print(fail_item(f"Error: File not found: {args.xp_file}"), file=sys.stderr)
        return 1

    # Import xp_viewer
    try:
        from xp_viewer import render_xp_to_image
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from xp_viewer import render_xp_to_image

    # Determine output path
    if hasattr(args, "output") and args.output:
        output_path = args.output
    else:
        base = xp_path.stem
        output_path = f"{base}_preview.png"

    # Render XP to image
    print(f"  Rendering to: {output_path}")
    result = render_xp_to_image(str(xp_path), output_path)

    if result:
        print(f"\n{ok_item(f'Preview saved to: {result}')}")

        # Optionally open preview
        try:
            import subprocess

            subprocess.run(["open", result], check=False)
        except:
            pass

        return 0
    else:
        print(fail_item("Error: Failed to render XP file"), file=sys.stderr)
        return 1


def cmd_debug(args):
    """Handle debug command - generate labeled debug PNG sheet with grid lines and cell indices."""
    sheet_path = Path(args.sheet_path)

    if not sheet_path.exists():
        print(fail_item(f"Sheet not found: {sheet_path}"))
        return

    print(f"🔍 Generating debug sheet: {sheet_path.name}")

    # Load the sprite sheet
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(sheet_path)
    width, height = img.size
    draw = ImageDraw.Draw(img)

    # Try to load a monospaced font for labels, fallback to default
    try:
        # Try system fonts in order
        font = ImageFont.truetype("Courier New", 12)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSansMono", 12)
        except OSError:
            font = ImageFont.truetype("DejaVuSans", 12)

    # Detect grid dimensions (same pattern as auto_adjust.validate_grid_structure)
    import numpy as np

    arr = np.array(img)

    # Find horizontal and vertical grid lines (black/dark boundaries)
    h_lines = []
    v_lines = []

    # Scan for horizontal lines (dark rows)
    for y in range(height):
        row_dark = np.all(arr[y, :] < 50)  # Threshold for dark lines
        if row_dark:
            h_lines.append(y)

    # Scan for vertical lines (dark columns)
    for x in range(width):
        col_dark = np.all(arr[:, x] < 50)
        if col_dark:
            v_lines.append(x)

    if len(h_lines) > 1 and len(v_lines) > 1:
        # Calculate cell dimensions from detected grid
        cell_h = min([h_lines[i + 1] - h_lines[i] for i in range(len(h_lines) - 1)])
        cell_w = min([v_lines[i + 1] - v_lines[i] for i in range(len(v_lines) - 1)])

        # Calculate number of frames (horizontal) and angles (vertical)
        frames = min(
            [
                len(
                    [
                        v_lines[i]
                        for i in range(len(v_lines) - 1)
                        if v_lines[i + 1] - v_lines[i] <= cell_w + 5
                    ]
                ),
                len(v_lines) - 1,
            ]
        )
        angles = min(
            [
                len(
                    [
                        h_lines[i]
                        for i in range(len(h_lines) - 1)
                        if h_lines[i + 1] - h_lines[i] <= cell_h + 5
                    ]
                ),
                len(h_lines) - 1,
            ]
        )
        angles = min(
            [
                len(
                    [
                        h_lines[i]
                        for i in range(len(h_lines) - 1)
                        if h_lines[i + 1] - h_lines[i] <= cell_h + 5
                    ]
                ),
                len(h_lines) - 1,
            ]
        )

        # Get staging debug directory
        from asset_gen.staging import get_staging_dir

        debug_dir = get_staging_dir("debug")

        # Draw grid lines
        for x in range(0, width, cell_w):
            draw.line([(x, 0), (x, height)], fill="cyan", width=1)
        for y in range(0, height, cell_h):
            draw.line([(0, y), (width, y)], fill="cyan", width=1)

        # Label each cell with angle/frame indices
        label_count = 0
        for angle_idx in range(angles):
            for frame_idx in range(frames):
                cell_x = (
                    (v_lines[0] + frame_idx * cell_w)
                    if len(v_lines) > 0
                    else frame_idx * cell_w
                )
                cell_y = (
                    (h_lines[0] + angle_idx * cell_h)
                    if len(h_lines) > 0
                    else angle_idx * cell_h
                )

                # Draw angle/frame label at cell center
                label_text = f"A{angle_idx:02d}F{frame_idx:02d}"
                bbox = draw.textbbox((0, 0), label_text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]

                text_x = cell_x + (cell_w - text_w) // 2
                text_y = cell_y + (cell_h - text_h) // 2

                # Draw text outline for visibility
                draw.text((text_x - 1, text_y), label_text, font=font, fill="black")
                draw.text((text_x + 1, text_y), label_text, font=font, fill="black")
                draw.text((text_x, text_y - 1), label_text, font=font, fill="black")
                draw.text((text_x, text_y + 1), label_text, font=font, fill="black")
                draw.text((text_x, text_y), label_text, font=font, fill="white")

                label_count += 1

        # Save annotated debug sheet
        debug_path = debug_dir / f"{sheet_path.stem}_debug.png"
        img.save(debug_path)
        print(ok_item(f"Debug sheet saved: {debug_path}"))
        print(f"  Grid detected: {angles} angles × {frames} frames")
        print(f"  Cell size: {cell_w}×{cell_h}px")
        print(f"  Cells labeled: {label_count}")
    else:
        print(fail_item("Could not detect grid structure (no dark grid lines found)"))
        print("  Ensure sprite sheet has dark grid lines (black/gray rows/columns)")


def cmd_validate(args):
    """Handle validate command."""
    print(f"\nValidating .xp file: {args.xp_file}")

    # Import validator
    try:
        from asset_gen.validator import validate_xp, print_validation_report
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.validator import validate_xp, print_validation_report

    xp_path = Path(args.xp_file)

    # Validate
    result = validate_xp(xp_path)
    print_validation_report(result)

    # Return exit code
    return 0 if result["valid"] else 1


def cmd_publish(args):
    """Handle publish command."""
    print(f"\n{progress(1, 2, 'Publishing XP files')}")

    # Determine files to publish
    if args.xp_file:
        # Specific files provided
        files_to_publish = [Path(f) for f in args.xp_file]
    else:
        # List all XP files in staging/xp/
        files_to_publish = list_xp_files()

    if not files_to_publish:
        print("\n  No .xp files found to publish")
        print(
            "  Use 'from-png', 'from-sheet', or 'from-blender' to create assets first"
        )
        return 0

    print(f"\n{progress(2, 2, f'Processing {len(files_to_publish)} asset(s):')}")

    # Check if assets/sprites/ directory exists
    sprites_dir = Path("assets/sprites")
    if not sprites_dir.exists():
        print("  Creating assets/sprites/ directory...")
        sprites_dir.mkdir(parents=True, exist_ok=True)

    published_count = 0
    skip_count = 0

    for xp_file in files_to_publish:
        print(f"\n  Asset: {xp_file.name}")

        # Extract asset info
        asset_name = xp_file.stem
        print(f"    Name: {asset_name}")

        # Get metadata if available
        try:
            from xp_core import XPFile
        except ImportError:
            from scripts.xp_core import XPFile

        try:
            xp = XPFile(str(xp_file))
            meta = xp.get_metadata()
            if meta:
                angles = meta.get("angles", 1)
                anims = meta.get("anims", [1])
                total_frames = sum(anims)
                print(f"    Angles: {angles}, Frames: {anims} (total: {total_frames})")
            else:
                print(f"    (Metadata not available)")
        except:
            print(f"    (Could not read metadata)")

        # Skip confirmation if --skip-confirmation flag is set
        # Note: argparse stores this in args.skip_confirmation (converted to skip_confirmation)
        skip_confirmation = getattr(args, "skip_confirmation", False)

        if not skip_confirmation:
            response = (
                input(f"    Publish to assets/sprites/{asset_name}.xp? [y/N] ").strip().lower()
            )
            if response != "y" and response != "yes":
                print(f"    Skipped: {asset_name}.xp")
                skip_count += 1
                continue

        # Copy to assets/sprites/
        dest_path = sprites_dir / f"{asset_name}.xp"
        import shutil

        shutil.copy(xp_file, dest_path)
        print(f"    {ok_item(f'Published: assets/sprites/{asset_name}.xp')}")
        published_count += 1

    # Print summary
    print(f"\n{ok_item('Publishing finished!')}")
    print(f"  Published: {published_count} asset(s)")
    if skip_count > 0:
        print(f"  Skipped: {skip_count} asset(s)")

    return 0


def cmd_start(args):
    """Handle start wizard command."""
    print("\n" + "=" * 60)
    print("  Asset Maker CLI")
    print("=" * 60)
    print("\nGenerate game assets from PNG, Blender, or AI sources.\n")

    # Display workflow options
    print("Available Workflows:")
    print("  1. PNG      - Process single PNG file with magenta transparency")
    print("  2. Sheet    - Process existing sprite sheet")
    print("  3. Blender  - Render from .blend file via MCP server")
    print("  4. MCP      - Use MCP Blender session for interactive rendering")
    print()

    # Check for provided arguments
    asset_name = None
    asset_type = None
    asset_angles = None

    if hasattr(args, "name") and args.name:
        asset_name = args.name
    else:
        asset_name = input("Asset name? ").strip()

    if hasattr(args, "type") and args.type:
        asset_type = args.type
    else:
        print("\nType? [character/item]")
        type_input = input("> ").strip().lower()
        asset_type = type_input if type_input in ["character", "item"] else "character"

    if hasattr(args, "angles") and args.angles:
        asset_angles = args.angles
    else:
        print("\nAngles? [1/4/8]")
        angles_input = input("> ").strip()
        asset_angles = (
            int(angles_input)
            if angles_input.isdigit() and int(angles_input) in [1, 4, 8]
            else 1
        )

    print("\n" + "-" * 60)
    print("Example command based on your choices:")
    print(f"  python scripts/asset_maker.py from-png your_sprite.png \\")
    print(f"    --name {asset_name} --type {asset_type} --angles {asset_angles}")
    print("-" * 60 + "\n")

    return 0


def cmd_mcp_session(args):
    """Handle mcp-session command."""
    # Import MCP session module
    try:
        from asset_gen.mcp_session import (
            get_mcp_status,
            copy_to_staging,
        )
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from asset_gen.mcp_session import (
            get_mcp_status,
            copy_to_staging,
        )

    # Route to subcommands
    if not hasattr(args, "mcp_command") or not args.mcp_command:
        print(
            "Error: No subcommand specified. Use 'mcp-session start', 'mcp-session status', or 'mcp-session copy'",
            file=sys.stderr,
        )
        return 1

    if args.mcp_command == "start":
        print("\n" + "=" * 50)
        print("  MCP Session Manager")
        print("=" * 50)
        print("\nMCP Server Connection Info:")
        print("  Port: 9876")
        print("  Protocol: MCP (Model Context Protocol)")
        print("\nStatus Check:")
        print("  python scripts/asset_maker.py mcp-session status")
        print("\nOther Commands:")
        print("  mcp-session start   - Show this info")
        print("  mcp-session status  - Check server status")
        print("  mcp-session copy x  - Copy file to staging/")
        print("=" * 50)

    elif args.mcp_command == "status":
        print("\nMCP Server Status:")
        status = get_mcp_status()
        print(f"  Server: {'Running' if status['available'] else 'Stopped'}")
        print(f"  Port: {status['port']}")
        if status["blender_running"] is not None:
            print(
                f"  Blender: {'Running' if status['blender_running'] else 'Not found'}"
            )

    elif args.mcp_command == "copy":
        print(f"\nCopying file to staging/inputs/: {args.input_path}")
        try:
            output_path = copy_to_staging(args.input_path)
            print(f"  {ok_item(f'Copied to: {output_path}')}")
        except FileNotFoundError as e:
            print(f"  {fail_item(f'Error: {e}')}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"  {fail_item(f'Error: {e}')}", file=sys.stderr)
            return 1

    return 0


def main():
    """Main entry point."""
    args = parse_args()

    if not args.command:
        print("Error: No command specified", file=sys.stderr)
        print("\nUse --help to see available commands\n")
        sys.exit(1)

    # Dispatch to command handlers
    cmd_map = {
        "start": cmd_start,
        "from-png": cmd_from_png,
        "from-sheet": cmd_from_sheet,
        "from-blender": cmd_from_blender,
        "mcp-session": cmd_mcp_session,
        "view": cmd_view,
        "debug": cmd_debug,
        "validate": cmd_validate,
        "publish": cmd_publish,
    }

    if args.command in cmd_map:
        try:
            cmd_map[args.command](args)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(1)
    else:
        print(f"Error: Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
