"""
Sheet Stitcher — Assemble Blender frames into sprite sheets with metadata.

[PIPELINE:ASSEMBLE] Takes rendered frame PNGs and combines them into a
single sprite sheet PNG plus a sheet.json metadata file describing the layout.

USAGE:
    from scripts.pipeline.sheet_stitcher import stitch_with_metadata

    sheet_path, metadata = stitch_with_metadata(
        frame_paths=["angle_0_frame_0001.png", ...],
        output_dir=Path("output/"),
        name="player",
        angles=8,
        frames_per_angle=1,
    )

OUTPUT:
    - {name}.png — Stitched sprite sheet with magenta background
    - {name}.json — Metadata describing layout:
        {
            "name": "player",
            "sheet_path": "player.png",
            "frame_width": 96,
            "frame_height": 96,
            "angles": 8,
            "frames_per_angle": 1,
            "total_frames": 8,
            "grid_cols": 1,
            "grid_rows": 8,
            "cell_aligned": true,
            "cell_size": 12
        }

GRID LAYOUT:
    Frames are arranged left-to-right, top-to-bottom:
    - Columns = animation frames (one column per frame index)
    - Rows = angles (one row per viewing angle)

    For 8 angles and 4 frames per angle:
    | a0f0 | a0f1 | a0f2 | a0f3 |
    | a1f0 | a1f1 | a1f2 | a1f3 |
    | a2f0 | a2f1 | a2f2 | a2f3 |
    | a3f0 | a3f1 | a3f2 | a3f3 |
    | a4f0 | a4f1 | a4f2 | a4f3 |
    | a5f0 | a5f1 | a5f2 | a5f3 |
    | a6f0 | a6f1 | a6f2 | a6f3 |
    | a7f0 | a7f1 | a7f2 | a7f3 |

CELL ALIGNMENT:
    For XP engine compatibility, frame dimensions should be divisible by
    cell_size (default 12px). The module validates this and reports alignment
    status in metadata.

ENGINE CONTRACT:
    The XP engine expects sprite sheets where each cell maps to one CP437
    glyph. If frame_width or frame_height is not divisible by cell_size,
    the alignment warning is raised and cell_aligned=false in metadata.
"""

from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
import json
import re


# Default cell size for XP engine (12 pixels per cell)
DEFAULT_CELL_SIZE = 12

# Transparency key color for XP engine
MAGENTA = (255, 0, 255)


def parse_frame_filename(filename: str) -> Tuple[int, int]:
    """Extract angle and frame indices from frame filename.

    Expected format: angle_<N>_frame_<MMMM>.png

    Args:
        filename: Frame filename (basename, not full path)

    Returns:
        Tuple of (angle_index, frame_index)

    Raises:
        ValueError: If filename doesn't match expected pattern
    """
    pattern = r"angle_(\d+)_frame_(\d+)\.png"
    match = re.match(pattern, filename)

    if not match:
        raise ValueError(
            f"Filename '{filename}' doesn't match expected pattern 'angle_<N>_frame_<MMMM>.png'"
        )

    return int(match.group(1)), int(match.group(2))


def validate_cell_alignment(width: int, height: int, cell_size: int = DEFAULT_CELL_SIZE) -> bool:
    """Check if dimensions are divisible by cell size.

    Args:
        width: Frame width in pixels
        height: Frame height in pixels
        cell_size: Cell size in pixels (default 12)

    Returns:
        True if both dimensions are divisible by cell_size
    """
    return (width % cell_size == 0) and (height % cell_size == 0)


def infer_layout(frame_paths: List[Path]) -> Dict[str, int]:
    """Infer grid layout from frame filenames.

    Analyzes frame filenames to determine number of angles and frames per angle.

    Args:
        frame_paths: List of paths to frame PNGs

    Returns:
        Dict with keys:
            - angles: Number of unique angle indices
            - frames_per_angle: Number of frames per angle
            - total_frames: Total frame count

    Raises:
        ValueError: If frame_paths is empty or filenames don't parse
    """
    if not frame_paths:
        raise ValueError("No frames provided")

    angles_seen = set()
    frames_by_angle = {}

    for path in frame_paths:
        angle_idx, frame_idx = parse_frame_filename(path.name)
        angles_seen.add(angle_idx)

        if angle_idx not in frames_by_angle:
            frames_by_angle[angle_idx] = set()
        frames_by_angle[angle_idx].add(frame_idx)

    # Validate all angles have same frame count
    frame_counts = [len(frames) for frames in frames_by_angle.values()]
    if len(set(frame_counts)) > 1:
        raise ValueError(
            f"Inconsistent frame counts across angles: {dict((a, len(f)) for a, f in frames_by_angle.items())}"
        )

    # Validate frame indices are contiguous and identical across angles
    if frames_by_angle:
        reference_frames = sorted(next(iter(frames_by_angle.values())))
        for angle_idx, frames in frames_by_angle.items():
            if sorted(frames) != reference_frames:
                raise ValueError(
                    f"Inconsistent frame indices for angle {angle_idx}: {sorted(frames)} != {reference_frames}"
                )
        # Contiguity check (supports 0-based or 1-based sequences)
        if reference_frames:
            expected_len = reference_frames[-1] - reference_frames[0] + 1
            if expected_len != len(reference_frames):
                raise ValueError(
                    f"Non-contiguous frame indices detected: {reference_frames}"
                )

    return {
        "angles": len(angles_seen),
        "frames_per_angle": frame_counts[0] if frame_counts else 0,
        "total_frames": len(frame_paths),
    }


def stitch_frames(
    frame_paths: List[Path],
    angles: int,
    frames_per_angle: int,
) -> "Image":
    """Stitch frames into a single sprite sheet.

    Layout: angles as columns, animation frames as rows.

    Args:
        frame_paths: Paths to frame PNGs
        angles: Number of viewing angles (columns)
        frames_per_angle: Frames per angle (rows)

    Returns:
        PIL Image containing the stitched sprite sheet
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("PIL (Pillow) is required. Install with: pip install Pillow")

    # Load first frame to get dimensions
    first_frame = Image.open(frame_paths[0])
    frame_w, frame_h = first_frame.size

    # Build index maps to normalize 0-based/1-based naming
    frames_by_angle = {}
    for path in frame_paths:
        angle_idx, frame_idx = parse_frame_filename(path.name)
        frames_by_angle.setdefault(angle_idx, set()).add(frame_idx)

    if len(frames_by_angle) != angles:
        raise ValueError(
            f"Expected {angles} angles but found {len(frames_by_angle)}: {sorted(frames_by_angle.keys())}"
        )

    reference_frames = sorted(next(iter(frames_by_angle.values())))
    for angle_idx, frames in frames_by_angle.items():
        if sorted(frames) != reference_frames:
            raise ValueError(
                f"Inconsistent frame indices for angle {angle_idx}: {sorted(frames)} != {reference_frames}"
            )

    if len(reference_frames) != frames_per_angle:
        raise ValueError(
            f"Expected {frames_per_angle} frames per angle but found {len(reference_frames)}"
        )

    angle_to_row = {angle: idx for idx, angle in enumerate(sorted(frames_by_angle.keys()))}
    frame_to_col = {frame: idx for idx, frame in enumerate(reference_frames)}

    # Create sheet with magenta background
    sheet_w = frames_per_angle * frame_w
    sheet_h = angles * frame_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), MAGENTA)

    # Sort frames by angle and frame index for correct placement
    sorted_frames = sorted(
        frame_paths,
        key=lambda p: parse_frame_filename(p.name)
    )

    # Place frames in grid (columns = frames, rows = angles)
    for path in sorted_frames:
        angle_idx, frame_idx = parse_frame_filename(path.name)

        # Normalize indices to contiguous 0-based positions
        row = angle_to_row[angle_idx]
        col = frame_to_col[frame_idx]

        # Calculate position (frame is column, angle is row)
        x = col * frame_w
        y = row * frame_h

        frame_img = Image.open(path)

        # Handle RGBA by compositing onto magenta
        if frame_img.mode == "RGBA":
            # Create magenta background for this cell
            cell_bg = Image.new("RGB", (frame_w, frame_h), MAGENTA)
            # Composite frame onto magenta using alpha
            frame_rgb = Image.new("RGBA", frame_img.size, (*MAGENTA, 255))
            frame_rgb.paste(frame_img, mask=frame_img.split()[3])
            frame_img = frame_rgb.convert("RGB")

        sheet.paste(frame_img, (x, y))

    return sheet


def create_metadata(
    name: str,
    sheet_path: Path,
    frame_width: int,
    frame_height: int,
    angles: int,
    frames_per_angle: int,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> Dict[str, Any]:
    """Create metadata dict for sprite sheet.

    Args:
        name: Asset name
        sheet_path: Path to sprite sheet PNG
        frame_width: Width of each frame in pixels
        frame_height: Height of each frame in pixels
        angles: Number of viewing angles
        frames_per_angle: Animation frames per angle
        cell_size: Cell size for alignment check

    Returns:
        Metadata dict for JSON serialization
    """
    cell_aligned = validate_cell_alignment(frame_width, frame_height, cell_size)

    return {
        "name": name,
        "sheet_path": sheet_path.name,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "angles": angles,
        "frames_per_angle": frames_per_angle,
        "total_frames": angles * frames_per_angle,
        "grid_cols": frames_per_angle,
        "grid_rows": angles,
        "cell_aligned": cell_aligned,
        "cell_size": cell_size,
    }


def stitch_with_metadata(
    frame_paths: List[Path],
    output_dir: Path,
    name: str,
    angles: Optional[int] = None,
    frames_per_angle: Optional[int] = None,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> Tuple[Path, Dict[str, Any]]:
    """Stitch frames into sprite sheet and emit metadata.

    Main entry point for sheet stitching pipeline.

    Args:
        frame_paths: Paths to rendered frame PNGs
        output_dir: Directory for output files
        name: Asset name (used for output filenames)
        angles: Number of angles (optional, inferred from filenames if not provided)
        frames_per_angle: Frames per angle (optional, inferred if not provided)
        cell_size: Cell size for alignment validation (default 12)

    Returns:
        Tuple of (sheet_path, metadata_dict)

    Raises:
        ValueError: If frames are missing or inconsistent
        ImportError: If PIL is not available
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("PIL (Pillow) is required. Install with: pip install Pillow")

    # Convert to Path objects
    frame_paths = [Path(p) for p in frame_paths]

    # Infer layout if not provided
    if angles is None or frames_per_angle is None:
        layout = infer_layout(frame_paths)
        angles = angles or layout["angles"]
        frames_per_angle = frames_per_angle or layout["frames_per_angle"]

    # Get frame dimensions from first frame
    first_frame = Image.open(frame_paths[0])
    frame_w, frame_h = first_frame.size
    first_frame.close()

    # Validate alignment
    if not validate_cell_alignment(frame_w, frame_h, cell_size):
        print(
            f"WARNING: Frame dimensions ({frame_w}x{frame_h}) not aligned to "
            f"{cell_size}px cells. XP conversion may have artifacts."
        )

    # Stitch frames
    sheet = stitch_frames(frame_paths, angles, frames_per_angle)

    # Save outputs
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sheet_path = output_dir / f"{name}.png"
    sheet.save(sheet_path)

    metadata = create_metadata(
        name=name,
        sheet_path=sheet_path,
        frame_width=frame_w,
        frame_height=frame_h,
        angles=angles,
        frames_per_angle=frames_per_angle,
        cell_size=cell_size,
    )

    metadata_path = output_dir / f"{name}.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"Sheet: {sheet_path}")
    print(f"Metadata: {metadata_path}")
    print(f"  Grid: {frames_per_angle}x{angles} ({angles * frames_per_angle} frames)")
    print(f"  Frame size: {frame_w}x{frame_h}px")
    print(f"  Cell aligned: {metadata['cell_aligned']}")

    return sheet_path, metadata


# CLI entry point
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="Stitch Blender frames into sprite sheet with metadata"
    )
    parser.add_argument(
        "frames_dir",
        help="Directory containing frame PNGs (angle_N_frame_MMMM.png)"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output directory for sheet and metadata"
    )
    parser.add_argument(
        "--name", "-n",
        default="sprite",
        help="Asset name (default: sprite)"
    )
    parser.add_argument(
        "--angles",
        type=int,
        help="Number of angles (optional, inferred from filenames)"
    )
    parser.add_argument(
        "--frames",
        type=int,
        help="Frames per angle (optional, inferred from filenames)"
    )
    parser.add_argument(
        "--cell-size",
        type=int,
        default=DEFAULT_CELL_SIZE,
        help=f"Cell size for alignment check (default: {DEFAULT_CELL_SIZE})"
    )

    args = parser.parse_args()

    # Collect frame paths
    frames_dir = Path(args.frames_dir)
    frame_paths = sorted(frames_dir.glob("angle_*_frame_*.png"))

    if not frame_paths:
        print(f"ERROR: No frame files found in {frames_dir}")
        sys.exit(1)

    print(f"Found {len(frame_paths)} frames")

    sheet_path, metadata = stitch_with_metadata(
        frame_paths=frame_paths,
        output_dir=Path(args.output),
        name=args.name,
        angles=args.angles,
        frames_per_angle=args.frames,
        cell_size=args.cell_size,
    )

    print(f"\nDone! Output: {sheet_path}")
