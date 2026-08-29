"""
reformatter.py -- Frame-to-sheet reformatter for AI-generated sprite frames.

ARCHITECTURE:
    Takes a directory of individual frame PNGs (named f_{angle}_{frame}.png),
    validates them against expected parameters, normalizes their sizes,
    converts alpha transparency to magenta keying, assembles them into a
    sprite sheet, and optionally applies reflections.

    The output sheet is compatible with the existing 4-stage asset pipeline:
    GENERATE -> SLICE -> PROCESS -> ASSEMBLE

KEY EXPORTS:
    - discover_frames: Find frame PNGs in a directory
    - validate_frame_set: Check completeness of frame set
    - normalize_frame_size: Resize frame to target dimensions
    - convert_alpha_to_magenta: RGBA alpha -> magenta key transparency
    - assemble_sheet: Compose frames into a sprite sheet
    - run_reformatter: Top-level orchestrator

PIPELINE CONTEXT:
    [FLOW:REFORMAT] Runs before the asset pipeline when --reformat is used.
    See docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md for the archived specification.

REUSE:
    - generate_reflections() from reflection_handler.py
    - detect_reflections() from reflection_handler.py
    - validate_reflection_geometry() from reflection_handler.py
"""

import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .reflection_handler import (
    detect_reflections,
    generate_reflections,
    validate_reflection_geometry,
)

logger = logging.getLogger(__name__)

# Frame filename pattern: f_{angle}_{frame}.png
FRAME_PATTERN = re.compile(r"^f_(\d+)_(\d+)\.png$")

# Engine cell size in pixels
CELL_SIZE = 12

# Default magenta transparency key
MAGENTA = (255, 0, 255)


@dataclass
class ReformatResult:
    """Result of a reformatter run.

    Attributes:
        output_path: Path to the assembled sprite sheet.
        sheet_width: Width of the output sheet in pixels.
        sheet_height: Height of the output sheet in pixels.
        projs: Number of projections (1 or 2).
        angles: Number of angle rows.
        frames: Frame counts per animation.
        reflections_applied: Whether reflections were generated.
        warnings: List of non-fatal warnings.
    """

    output_path: Path
    sheet_width: int
    sheet_height: int
    projs: int
    angles: int
    frames: List[int]
    reflections_applied: bool
    warnings: List[str]


def discover_frames(input_dir: Path) -> Dict[Tuple[int, int], Path]:
    """Find frame PNGs matching f_{angle}_{frame}.png in a directory.

    Args:
        input_dir: Directory to scan for frame PNGs.

    Returns:
        Dict mapping (angle, frame) tuples to file paths.

    Raises:
        FileNotFoundError: If input_dir does not exist.
        ValueError: If no matching frames are found.
    """
    input_dir = Path(input_dir)

    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    frame_map: Dict[Tuple[int, int], Path] = {}

    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue

        match = FRAME_PATTERN.match(path.name)
        if match:
            angle = int(match.group(1))
            frame = int(match.group(2))
            frame_map[(angle, frame)] = path

    if not frame_map:
        raise ValueError(f"No frames matching f_{{angle}}_{{frame}}.png found in {input_dir}")

    return frame_map


def validate_frame_set(
    frame_map: Dict[Tuple[int, int], Path],
    angles: int,
    frames: List[int],
) -> List[str]:
    """Validate that all expected (angle, frame) pairs are present.

    Args:
        frame_map: Discovered frames from discover_frames().
        angles: Expected number of angles.
        frames: Expected frame counts per animation.

    Returns:
        List of error strings. Empty list means valid.
    """
    errors = []
    total_frames_per_angle = sum(frames)

    expected = set()
    for a in range(angles):
        for f in range(total_frames_per_angle):
            expected.add((a, f))

    found = set(frame_map.keys())

    missing = expected - found
    if missing:
        missing_sorted = sorted(missing)
        errors.append(
            f"Missing {len(missing)} frames: "
            + ", ".join(f"f_{a}_{f}.png" for a, f in missing_sorted[:10])
            + ("..." if len(missing) > 10 else "")
        )

    extra = found - expected
    if extra:
        extra_sorted = sorted(extra)
        logger.warning(
            "Extra frames found (will be ignored): %s",
            ", ".join(f"f_{a}_{f}.png" for a, f in extra_sorted[:5]),
        )

    return errors


def normalize_frame_size(
    frame: Image.Image,
    target_w: int,
    target_h: int,
) -> Image.Image:
    """Resize a frame to target dimensions, grid-aligned to CELL_SIZE.

    Uses LANCZOS for downscale, NEAREST for upscale.

    Args:
        frame: Source PIL Image (any mode).
        target_w: Target width in pixels (must be divisible by CELL_SIZE).
        target_h: Target height in pixels (must be divisible by CELL_SIZE).

    Returns:
        New Image resized to (target_w, target_h).
    """
    # Snap target to grid
    target_w = max(CELL_SIZE, _snap_to_grid(target_w))
    target_h = max(CELL_SIZE, _snap_to_grid(target_h))

    if frame.size == (target_w, target_h):
        return frame.copy()

    # Choose resampling: LANCZOS for downscale, NEAREST for upscale
    src_w, src_h = frame.size
    is_downscale = (target_w * target_h) < (src_w * src_h)
    resampling = Image.Resampling.LANCZOS if is_downscale else Image.Resampling.NEAREST

    return frame.resize((target_w, target_h), resampling)


def convert_alpha_to_magenta(
    frame: Image.Image,
    threshold: int = 128,
) -> Image.Image:
    """Convert RGBA alpha transparency to magenta-key transparency.

    Pixels with alpha < threshold become magenta (255, 0, 255).
    Pixels with alpha >= threshold become opaque RGB.

    If the input is already RGB (no alpha), returns a copy unchanged.

    Args:
        frame: Source PIL Image (RGBA or RGB).
        threshold: Alpha value below which pixels are treated as transparent.

    Returns:
        New RGB Image with magenta transparency.
    """
    if frame.mode == "RGB":
        return frame.copy()

    if frame.mode != "RGBA":
        frame = frame.convert("RGBA")

    arr = np.array(frame)
    rgb = arr[:, :, :3].copy()
    alpha = arr[:, :, 3]

    # Set transparent pixels to magenta
    transparent_mask = alpha < threshold
    rgb[transparent_mask] = [255, 0, 255]

    return Image.fromarray(rgb, "RGB")


def assemble_sheet(
    frame_map: Dict[Tuple[int, int], Path],
    angles: int,
    frames: List[int],
    target_w: Optional[int] = None,
    target_h: Optional[int] = None,
    alpha_to_magenta: bool = True,
    alpha_threshold: int = 128,
) -> Image.Image:
    """Assemble individual frames into a sprite sheet.

    Layout: rows = angles, columns = sum(frames).
    Canvas is filled with magenta (255, 0, 255).

    Args:
        frame_map: Discovered frames mapping (angle, frame) -> Path.
        angles: Number of angle rows.
        frames: Frame counts per animation.
        target_w: Target frame width in pixels (None = auto from first frame).
        target_h: Target frame height in pixels (None = auto from first frame).
        alpha_to_magenta: Convert alpha transparency to magenta.
        alpha_threshold: Alpha threshold for conversion.

    Returns:
        Assembled RGB sprite sheet Image.
    """
    total_cols = sum(frames)

    # Determine frame dimensions from first frame if not specified
    if target_w is None or target_h is None:
        first_key = min(frame_map.keys())
        first_frame = Image.open(frame_map[first_key])
        if target_w is None:
            target_w = _snap_to_grid(first_frame.width)
        if target_h is None:
            target_h = _snap_to_grid(first_frame.height)

    sheet_w = target_w * total_cols
    sheet_h = target_h * angles

    # Create magenta canvas
    sheet = Image.new("RGB", (sheet_w, sheet_h), MAGENTA)

    for a in range(angles):
        col = 0
        for f in range(total_cols):
            key = (a, f)
            if key not in frame_map:
                col += 1
                continue

            frame = Image.open(frame_map[key])

            # Convert alpha to magenta if needed
            if alpha_to_magenta:
                frame = convert_alpha_to_magenta(frame, alpha_threshold)
            elif frame.mode != "RGB":
                frame = frame.convert("RGB")

            # Normalize size
            frame = normalize_frame_size(frame, target_w, target_h)

            # Paste into sheet
            x = col * target_w
            y = a * target_h
            sheet.paste(frame, (x, y))
            col += 1

    return sheet


def run_reformatter(
    input_dir: Path,
    output: Path,
    angles: int = 8,
    frames: Optional[List[int]] = None,
    target_cells_high: int = 8,
    target_cells_wide: Optional[int] = None,
    reflection_dim: float = 0.5,
    reflection_policy: str = "generate",
    alpha_to_magenta: bool = True,
    alpha_threshold: int = 128,
) -> ReformatResult:
    """Top-level reformatter orchestrator.

    Flow: discover -> validate -> assemble (with normalize + alpha convert) -> reflect -> save.

    Args:
        input_dir: Directory containing frame PNGs.
        output: Output sprite sheet path.
        angles: Number of viewing angles.
        frames: Frame counts per animation. Defaults to [4].
        target_cells_high: Target frame height in cells.
        target_cells_wide: Target frame width in cells (None = auto).
        reflection_dim: Brightness multiplier for reflections.
        reflection_policy: 'none', 'generate', or 'detect'.
        alpha_to_magenta: Convert RGBA alpha to magenta key.
        alpha_threshold: Alpha threshold for conversion.

    Returns:
        ReformatResult with output metadata.

    Raises:
        FileNotFoundError: If input_dir doesn't exist.
        ValueError: If no frames found or validation fails.
    """
    if frames is None:
        frames = [4]

    input_dir = Path(input_dir)
    output = Path(output)
    warnings: List[str] = []

    # 1. Discover frames
    logger.info("Discovering frames in %s", input_dir)
    frame_map = discover_frames(input_dir)
    logger.info("Found %d frames", len(frame_map))

    # 2. Validate frame set
    errors = validate_frame_set(frame_map, angles, frames)
    if errors:
        raise ValueError("Frame validation failed:\n" + "\n".join(errors))

    # 3. Compute target dimensions
    target_h = target_cells_high * CELL_SIZE

    if target_cells_wide is not None:
        target_w = target_cells_wide * CELL_SIZE
    else:
        # Auto-derive from first frame, snapped to grid
        first_key = min(frame_map.keys())
        first_frame = Image.open(frame_map[first_key])
        target_w = _snap_to_grid(first_frame.width)
        if target_w < CELL_SIZE:
            target_w = CELL_SIZE

    # 4. Assemble sheet
    logger.info(
        "Assembling sheet: %d angles, %s frames, %dx%d px/frame",
        angles, frames, target_w, target_h,
    )
    sheet = assemble_sheet(
        frame_map=frame_map,
        angles=angles,
        frames=frames,
        target_w=target_w,
        target_h=target_h,
        alpha_to_magenta=alpha_to_magenta,
        alpha_threshold=alpha_threshold,
    )

    # 5. Apply reflection policy
    reflections_applied = False
    projs = 1

    if angles > 1 and reflection_policy != "none":
        if reflection_policy == "detect":
            has_refl, confidence = detect_reflections(sheet, angles, frames)
            if has_refl and confidence >= 0.7:
                logger.info("Existing reflections detected (confidence=%.2f)", confidence)
                projs = 2
            else:
                logger.info("No reflections detected, generating...")
                sheet = generate_reflections(sheet, angles, frames, reflection_dim)
                reflections_applied = True
                projs = 2
        elif reflection_policy == "generate":
            sheet = generate_reflections(sheet, angles, frames, reflection_dim)
            reflections_applied = True
            projs = 2

    # 6. Validate final geometry
    is_valid, info = validate_reflection_geometry(
        sheet.width, sheet.height, angles, frames, CELL_SIZE
    )
    if not is_valid:
        # Try to provide useful diagnostics
        issues = info.get("issues", [])
        warnings.append(
            f"Geometry validation warning: {'; '.join(issues)}"
        )

    # 7. Save output
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(output), "PNG")
    logger.info("Saved reformatted sheet to %s (%dx%d)", output, sheet.width, sheet.height)

    return ReformatResult(
        output_path=output,
        sheet_width=sheet.width,
        sheet_height=sheet.height,
        projs=projs,
        angles=angles,
        frames=list(frames),
        reflections_applied=reflections_applied,
        warnings=warnings,
    )


def _snap_to_grid(value: int) -> int:
    """Snap a pixel value to the nearest CELL_SIZE (12px) boundary.

    Rounds to nearest; rounds up on exact half.
    """
    remainder = value % CELL_SIZE
    if remainder == 0:
        return value
    if remainder > CELL_SIZE // 2:
        return value + (CELL_SIZE - remainder)
    return value - remainder


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Reformat individual frame PNGs into a sprite sheet"
    )
    parser.add_argument(
        "--input-dir", required=True, help="Directory containing f_A_F.png frames"
    )
    parser.add_argument(
        "--output", "-o", required=True, help="Output sprite sheet path"
    )
    parser.add_argument(
        "--angles", type=int, default=8, choices=[1, 4, 8],
        help="Number of viewing angles"
    )
    parser.add_argument(
        "--frames", type=str, default="4",
        help="Frame counts per animation (comma-separated)"
    )
    parser.add_argument(
        "--target-cells-high", type=int, default=8,
        help="Target frame height in cells (1 cell = 12px)"
    )
    parser.add_argument(
        "--target-cells-wide", type=int, default=None,
        help="Target frame width in cells (auto if not set)"
    )
    parser.add_argument(
        "--reflection-dim", type=float, default=0.5,
        help="Reflection brightness factor (0.0-1.0)"
    )
    parser.add_argument(
        "--reflection-policy", choices=["none", "generate", "detect"],
        default="generate", help="Reflection handling policy"
    )
    parser.add_argument(
        "--alpha-to-magenta", action="store_true", default=True,
        help="Convert RGBA alpha to magenta key (default: on)"
    )
    parser.add_argument(
        "--no-alpha-to-magenta", action="store_true",
        help="Disable alpha-to-magenta conversion"
    )
    parser.add_argument(
        "--alpha-threshold", type=int, default=128,
        help="Alpha threshold for transparency (0-255)"
    )
    parser.add_argument(
        "--manifest", type=str, default=None,
        help="Path to guidance manifest JSON (validates against it)"
    )
    parser.add_argument(
        "--write-meta", type=str, default=None,
        help="Write output metadata to JSON file"
    )

    args = parser.parse_args()

    frames_list = [int(x.strip()) for x in args.frames.split(",")]
    do_alpha = not args.no_alpha_to_magenta

    result = run_reformatter(
        input_dir=Path(args.input_dir),
        output=Path(args.output),
        angles=args.angles,
        frames=frames_list,
        target_cells_high=args.target_cells_high,
        target_cells_wide=args.target_cells_wide,
        reflection_dim=args.reflection_dim,
        reflection_policy=args.reflection_policy,
        alpha_to_magenta=do_alpha,
        alpha_threshold=args.alpha_threshold,
    )

    print(f"Output: {result.output_path}")
    print(f"Sheet size: {result.sheet_width}x{result.sheet_height}")
    print(f"Projs: {result.projs}")
    print(f"Reflections applied: {result.reflections_applied}")

    if result.warnings:
        for w in result.warnings:
            print(f"Warning: {w}")

    # Write metadata if requested
    if args.write_meta:
        import json
        meta = {
            "output_path": str(result.output_path),
            "sheet_width": result.sheet_width,
            "sheet_height": result.sheet_height,
            "projs": result.projs,
            "angles": result.angles,
            "frames": result.frames,
            "reflections_applied": result.reflections_applied,
        }
        with open(args.write_meta, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"Metadata written to {args.write_meta}")
