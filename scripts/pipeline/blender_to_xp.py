"""
Blender-to-XP Pipeline Integration — Convert Blender sheets to XP sprites.

[PIPELINE:ORCHESTRATOR] This module connects the Blender render pipeline output
(sprite sheet + metadata) to the PNG→XP conversion pipeline, producing .xp files
ready for the game engine.

USAGE:
    from scripts.pipeline.blender_to_xp import convert_blender_to_xp

    xp_path = convert_blender_to_xp(
        sheet_path=Path("output/player.png"),
        metadata_path=Path("output/player.json"),  # Optional
        output_dir=Path("assets/sprites/"),
    )

PIPELINE FLOW:
    Blender → render_unified.py → frames (angle_N_frame_MMMM.png)
           → sheet_stitcher.py → sheet.png + sheet.json
           → blender_to_xp.py → output.xp  ← THIS MODULE

    This skips the full AssetPipeline Stage 1 (generation) since we already
    have the rendered sheet from Blender.

METADATA CONTRACT:
    The sheet.json file (from sheet_stitcher) provides:
    - angles: Number of viewing angles
    - frames_per_angle: Animation frames per angle
    - frame_width, frame_height: Per-frame dimensions in pixels
    - cell_aligned: Whether dimensions are 12px aligned

    If metadata_path is not provided, the module attempts to infer layout
    from the sheet dimensions (assuming standard 12px cells).

ENGINE CONTRACT:
    Multi-angle sprites (angles > 0) require reflections for the engine's
    projs=2 expectation. This module automatically generates reflections
    if not already present in the sheet.
"""

from typing import Optional, Dict, Any, Tuple
from pathlib import Path
import json
import sys
import os

# WHY: ensure the parent package (scripts/) is on sys.path so that
# relative imports within the asset_gen package resolve correctly when
# this module is executed or imported from varying working directories.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image


def load_metadata(metadata_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Load sheet metadata from JSON file.

    Args:
        metadata_path: Path to sheet.json (optional)

    Returns:
        Metadata dict or None if path not provided/doesn't exist
    """
    if metadata_path is None:
        return None

    if not metadata_path.exists():
        print(f"Warning: Metadata file not found: {metadata_path}")
        return None

    with open(metadata_path) as f:
        return json.load(f)


def infer_layout_from_sheet(sheet: Image.Image, cell_size: int = 12) -> Dict[str, Any]:
    """Infer layout from sheet dimensions assuming standard cells.

    Args:
        sheet: PIL Image of the sprite sheet
        cell_size: Cell size in pixels (default 12)

    Returns:
        Inferred metadata dict
    """
    width, height = sheet.size

    if width % cell_size != 0 or height % cell_size != 0:
        print(f"Warning: Sheet dimensions ({width}x{height}) not aligned to {cell_size}px")

    # Try common angle counts; assume rows = angles, columns = frames
    candidate_angles = [8, 4, 1]

    for angles in candidate_angles:
        if height % angles != 0:
            continue

        frame_height = height // angles

        # Assume square frames for fallback inference
        if frame_height == 0 or width % frame_height != 0:
            continue

        total_cols = width // frame_height
        if total_cols <= 0:
            continue

        # If multi-angle and even columns, assume reflections present (projs=2)
        projs = 2 if angles > 0 and total_cols % 2 == 0 else 1
        frames_per_angle = total_cols // projs
        frame_width = width // (frames_per_angle * projs) if frames_per_angle > 0 else 0

        if frame_width <= 0:
            continue

        return {
            "angles": angles,
            "frames_per_angle": frames_per_angle,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "cell_aligned": (frame_width % cell_size == 0) and (frame_height % cell_size == 0),
            "cell_size": cell_size,
            "projs": projs,
            "inferred": True,  # Mark as inferred vs explicit
        }

    # Fallback: treat as single-frame single-angle
    return {
        "angles": 1,
        "frames_per_angle": 1,
        "frame_width": width,
        "frame_height": height,
        "cell_aligned": (width % cell_size == 0) and (height % cell_size == 0),
        "cell_size": cell_size,
        "projs": 1,
        "inferred": True,
    }


def convert_blender_to_xp(
    sheet_path: Path,
    output_dir: Path,
    metadata_path: Optional[Path] = None,
    name: Optional[str] = None,
) -> Path:
    """Convert Blender-rendered sprite sheet to XP format.

    Main entry point for Blender → XP conversion.

    Args:
        sheet_path: Path to sprite sheet PNG
        output_dir: Directory for XP output
        metadata_path: Path to sheet.json (optional, will infer if missing)
        name: Asset name (defaults to sheet filename stem)

    Returns:
        Path to generated .xp file

    Raises:
        FileNotFoundError: If sheet doesn't exist
        ValueError: If sheet dimensions are invalid
    """
    from .schemas import AssetDef
    from .slicer import ImageSlicer
    from .processor import SpriteProcessor
    from .assembler import XPAssembler
    from .reflection_handler import generate_reflections

    # Resolve paths
    sheet_path = Path(sheet_path)
    output_dir = Path(output_dir)

    if not sheet_path.exists():
        raise FileNotFoundError(f"Sheet not found: {sheet_path}")

    # Resolve name
    if name is None:
        name = sheet_path.stem

    print(f"=== Blender → XP Conversion: {name} ===")

    # Load sheet image
    sheet = Image.open(sheet_path)
    if sheet.mode != "RGB":
        sheet = sheet.convert("RGB")

    print(f"   Sheet: {sheet_path.name} ({sheet.width}x{sheet.height})")

    # Load or infer metadata
    metadata = load_metadata(metadata_path)
    if metadata is None:
        print("   No metadata found, inferring from dimensions...")
        metadata = infer_layout_from_sheet(sheet)

    angles = metadata["angles"]
    frames_per_angle = metadata.get("frames_per_angle", 1)

    print(f"   Layout: {angles} angles × {frames_per_angle} frames")

    # Compute frame dimensions from metadata or sheet geometry
    frame_width = metadata.get("frame_width")
    frame_height = metadata.get("frame_height")
    if frame_width is None or frame_height is None:
        # Infer from sheet dimensions using any projs hint from metadata/inference
        projs_hint = metadata.get("projs", 1)
        if sheet.width % (frames_per_angle * projs_hint) != 0:
            raise ValueError(
                f"Sheet width {sheet.width} not divisible by frames_per_angle*projs "
                f"({frames_per_angle}*{projs_hint})"
            )
        if sheet.height % angles != 0:
            raise ValueError(
                f"Sheet height {sheet.height} not divisible by angles ({angles})"
            )
        frame_width = sheet.width // (frames_per_angle * projs_hint)
        frame_height = sheet.height // angles

    # Determine whether reflections are already present based on geometry
    projs_existing = metadata.get("projs")
    if projs_existing is None:
        if sheet.width % (frame_width * frames_per_angle) != 0:
            raise ValueError(
                f"Sheet width {sheet.width} not divisible by frame_width*frames_per_angle "
                f"({frame_width}*{frames_per_angle})"
            )
        projs_existing = sheet.width // (frame_width * frames_per_angle)

    if projs_existing not in (1, 2):
        raise ValueError(f"Unexpected projs factor from sheet geometry: {projs_existing}")

    # ================================================================
    # Check for reflections and generate if needed
    # ================================================================
    anims = [frames_per_angle]
    if angles > 0:
        print("   Checking for reflections (multi-angle sprite)...")
        if projs_existing == 2:
            print("   ✓ Reflections detected in sheet (geometry already doubled)")
            projs = 2
        else:
            print("   Generating reflections (required for projs=2)...")
            sheet = generate_reflections(sheet, angles, anims)
            projs = 2
    else:
        projs = 1

    print(f"   Frame size: {frame_width}x{frame_height}px")

    # Validate geometry against metadata
    expected_width = frame_width * frames_per_angle * projs
    expected_height = frame_height * angles
    if sheet.width != expected_width or sheet.height != expected_height:
        raise ValueError(
            f"Sheet geometry mismatch: got {sheet.width}x{sheet.height}, "
            f"expected {expected_width}x{expected_height} "
            f"(frame {frame_width}x{frame_height}, frames {frames_per_angle}, "
            f"angles {angles}, projs {projs})"
        )

    # Create AssetDef for the processor (size is in character cells)
    if frame_width % 12 != 0 or frame_height % 12 != 0:
        raise ValueError(
            f"Frame dimensions {frame_width}x{frame_height} not aligned to 12px cells"
        )
    cell_width = frame_width // 12
    cell_height = frame_height // 12

    asset_def = AssetDef(
        name=name,
        type="custom",
        angles=angles,
        frames=[frames_per_angle],
        source_type="file",
        source_path=str(sheet_path),
        size=(cell_width, cell_height),
    )

    # ================================================================
    # Stage 2: Slicing
    # ================================================================
    print("Stage 2/4: Slicing...")

    slicer = ImageSlicer()

    # Slice all columns (projection + reflection if present).
    total_cols = sum(anims) * projs
    frames_for_slicing = [total_cols]
    tiles = slicer.slice(sheet, angles, frames_for_slicing)
    print(f"   Extracted {len(tiles)} tiles")

    # ================================================================
    # Stage 3: Processing (cell conversion)
    # ================================================================
    print("Stage 3/4: Processing...")

    processor = SpriteProcessor()
    processed_grids = []

    for i, tile in enumerate(tiles):
        grid = processor.process_image(tile, asset_def)
        processed_grids.append(grid)

    print(f"   Processed {len(processed_grids)} grids")

    # ================================================================
    # Stage 4: Assembly
    # ================================================================
    print("Stage 4/4: Assembly...")

    output_dir.mkdir(parents=True, exist_ok=True)
    xp_path = output_dir / f"{name}.xp"

    assembler = XPAssembler()

    # Assembler metadata
    asm_metadata = {
        "angles": angles,
        "anims": anims,
        "projs": projs,
    }

    assembler.assemble(
        frames=processed_grids,
        metadata=asm_metadata,
        filename=str(xp_path),
    )

    print(f"   ✓ Output: {xp_path}")

    # Verify output
    from .xp_core import XPFile
    xp = XPFile()
    xp.load(str(xp_path))
    meta = xp.get_metadata()

    print(f"   Verification: {len(xp.layers)} layers, "
          f"angles={meta.get('angles')}, projs={meta.get('projs')}")

    return xp_path


def convert_from_frames(
    frames_dir: Path,
    output_dir: Path,
    name: str,
    angles: Optional[int] = None,
    frames_per_angle: Optional[int] = None,
) -> Path:
    """Convert Blender frames directly to XP (stitch + convert in one step).

    Convenience function that combines sheet_stitcher and convert_blender_to_xp.

    Args:
        frames_dir: Directory containing frame PNGs (angle_N_frame_MMMM.png)
        output_dir: Directory for XP output
        name: Asset name
        angles: Number of angles (optional, inferred from filenames)
        frames_per_angle: Frames per angle (optional, inferred)

    Returns:
        Path to generated .xp file
    """
    from .sheet_stitcher import stitch_with_metadata

    frames_dir = Path(frames_dir)
    output_dir = Path(output_dir)

    # Collect frame paths
    frame_paths = sorted(frames_dir.glob("angle_*_frame_*.png"))

    if not frame_paths:
        raise FileNotFoundError(f"No frame files found in {frames_dir}")

    print(f"Found {len(frame_paths)} frames in {frames_dir}")

    # Create staging directory for intermediate files
    staging_dir = output_dir / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Stitch frames into sheet
    sheet_path, metadata = stitch_with_metadata(
        frame_paths=frame_paths,
        output_dir=staging_dir,
        name=name,
        angles=angles,
        frames_per_angle=frames_per_angle,
    )

    metadata_path = staging_dir / f"{name}.json"

    # Convert sheet to XP
    return convert_blender_to_xp(
        sheet_path=sheet_path,
        output_dir=output_dir,
        metadata_path=metadata_path,
        name=name,
    )


# CLI entry point
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert Blender sprite sheet to XP format"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Sheet conversion command
    sheet_parser = subparsers.add_parser("sheet", help="Convert from sprite sheet")
    sheet_parser.add_argument(
        "sheet_path",
        help="Path to sprite sheet PNG"
    )
    sheet_parser.add_argument(
        "--metadata", "-m",
        help="Path to sheet.json metadata (optional)"
    )
    sheet_parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output directory for XP file"
    )
    sheet_parser.add_argument(
        "--name", "-n",
        help="Asset name (defaults to sheet filename)"
    )

    # Frames conversion command
    frames_parser = subparsers.add_parser("frames", help="Convert from frame directory")
    frames_parser.add_argument(
        "frames_dir",
        help="Directory containing frame PNGs"
    )
    frames_parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output directory for XP file"
    )
    frames_parser.add_argument(
        "--name", "-n",
        required=True,
        help="Asset name"
    )
    frames_parser.add_argument(
        "--angles",
        type=int,
        help="Number of angles (optional, inferred from filenames)"
    )
    frames_parser.add_argument(
        "--frames",
        type=int,
        help="Frames per angle (optional, inferred)"
    )

    args = parser.parse_args()

    if args.command == "sheet":
        xp_path = convert_blender_to_xp(
            sheet_path=Path(args.sheet_path),
            output_dir=Path(args.output),
            metadata_path=Path(args.metadata) if args.metadata else None,
            name=args.name,
        )
        print(f"\nDone! Output: {xp_path}")

    elif args.command == "frames":
        xp_path = convert_from_frames(
            frames_dir=Path(args.frames_dir),
            output_dir=Path(args.output),
            name=args.name,
            angles=args.angles,
            frames_per_angle=args.frames,
        )
        print(f"\nDone! Output: {xp_path}")

    else:
        parser.print_help()
        sys.exit(1)
