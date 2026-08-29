"""
guidance_manifest.py -- Generate guidance manifests for AI sprite frame generation.

ARCHITECTURE:
    A guidance manifest is a JSON file that describes the expected parameters
    for a set of AI-generated sprite frames. It serves as the contract between
    the prompt engineer and the reformatter: the manifest says what to generate,
    the reformatter validates that the output matches.

KEY EXPORTS:
    - GuidanceManifest: Frozen dataclass describing expected frame parameters
    - build_manifest: Factory function to create a manifest
    - write_manifest: Serialize manifest to JSON file

PIPELINE CONTEXT:
    [FLOW:MANIFEST] Runs before AI generation, consumed by reformatter validation.
    See docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md for the archived specification.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json
from pathlib import Path


@dataclass(frozen=True)
class GuidanceManifest:
    """Frozen description of expected AI-generated sprite frames.

    All fields are immutable after creation to prevent accidental mutation
    during validation passes.

    Attributes:
        name: Asset identifier (used for output filenames).
        angles: Number of viewing angles (1, 4, or 8).
        frames: Frame counts per animation (e.g. [1, 8] = 1 idle + 8 walk).
        target_cells_high: Target frame height in cells (1 cell = 12 px).
        frame_width_px: Expected frame width in pixels.
        frame_height_px: Expected frame height in pixels.
        total_frames: Total number of frame PNGs expected.
        naming_pattern: Filename pattern for frames.
        reflection_policy: One of 'none', 'generate', 'detect'.
        projs: Number of projections (1 or 2).
        expected_files: Complete list of expected filenames.
    """

    name: str
    angles: int
    frames: List[int]
    target_cells_high: int
    frame_width_px: int
    frame_height_px: int
    total_frames: int
    naming_pattern: str
    reflection_policy: str
    projs: int
    expected_files: List[str]


def build_manifest(
    name: str,
    angles: int = 8,
    frames: Optional[List[int]] = None,
    target_cells_high: int = 8,
    frame_width_cells: int = 8,
    reflection_policy: str = "generate",
) -> GuidanceManifest:
    """Build a guidance manifest from high-level parameters.

    Args:
        name: Asset identifier.
        angles: Number of viewing angles.
        frames: Frame counts per animation. Defaults to [4].
        target_cells_high: Target frame height in cells.
        frame_width_cells: Target frame width in cells.
        reflection_policy: 'none', 'generate', or 'detect'.

    Returns:
        Frozen GuidanceManifest instance.

    Raises:
        ValueError: If angles or frames are invalid.
    """
    if frames is None:
        frames = [4]

    if angles not in (1, 4, 8):
        raise ValueError(f"angles must be 1, 4, or 8, got {angles}")

    if any(f < 1 for f in frames):
        raise ValueError(f"All frame counts must be >= 1, got {frames}")

    if reflection_policy not in ("none", "generate", "detect"):
        raise ValueError(
            f"reflection_policy must be 'none', 'generate', or 'detect', "
            f"got '{reflection_policy}'"
        )

    cell_size = 12
    frame_width_px = frame_width_cells * cell_size
    frame_height_px = target_cells_high * cell_size
    total_anim_frames = sum(frames)
    total_frames = angles * total_anim_frames

    # Determine projs based on reflection policy and angles
    if angles <= 1 or reflection_policy == "none":
        projs = 1
    else:
        projs = 2

    # Build expected file list
    expected_files = []
    for angle in range(angles):
        for frame_idx in range(total_anim_frames):
            expected_files.append(f"f_{angle}_{frame_idx}.png")

    return GuidanceManifest(
        name=name,
        angles=angles,
        frames=list(frames),
        target_cells_high=target_cells_high,
        frame_width_px=frame_width_px,
        frame_height_px=frame_height_px,
        total_frames=total_frames,
        naming_pattern="f_{angle}_{frame}.png",
        reflection_policy=reflection_policy,
        projs=projs,
        expected_files=expected_files,
    )


def write_manifest(manifest: GuidanceManifest, output_path: Path) -> None:
    """Serialize a guidance manifest to a JSON file.

    Args:
        manifest: The manifest to write.
        output_path: Destination file path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(manifest)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def load_manifest(path: Path) -> GuidanceManifest:
    """Load a guidance manifest from a JSON file.

    Args:
        path: Path to the manifest JSON file.

    Returns:
        GuidanceManifest instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path) as f:
        data = json.load(f)

    return GuidanceManifest(**data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a guidance manifest for AI sprite frame generation"
    )
    parser.add_argument("--name", required=True, help="Asset name")
    parser.add_argument(
        "--angles", type=int, default=8, choices=[1, 4, 8],
        help="Number of viewing angles"
    )
    parser.add_argument(
        "--frames", type=str, default="4",
        help="Frame counts per animation (comma-separated, e.g. '1,8')"
    )
    parser.add_argument(
        "--target-cells-high", type=int, default=8,
        help="Target frame height in cells (1 cell = 12px)"
    )
    parser.add_argument(
        "--frame-width-cells", type=int, default=8,
        help="Target frame width in cells"
    )
    parser.add_argument(
        "--reflection-policy", choices=["none", "generate", "detect"],
        default="generate", help="Reflection policy"
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Output JSON path (default: <name>_manifest.json)"
    )

    args = parser.parse_args()

    frames_list = [int(x.strip()) for x in args.frames.split(",")]

    manifest = build_manifest(
        name=args.name,
        angles=args.angles,
        frames=frames_list,
        target_cells_high=args.target_cells_high,
        frame_width_cells=args.frame_width_cells,
        reflection_policy=args.reflection_policy,
    )

    output_path = Path(args.output) if args.output else Path(f"{args.name}_manifest.json")
    write_manifest(manifest, output_path)

    print(f"Manifest written to {output_path}")
    print(f"  Total frames: {manifest.total_frames}")
    print(f"  Frame size: {manifest.frame_width_px}x{manifest.frame_height_px}")
    print(f"  Expected files: {len(manifest.expected_files)}")
    print(f"  Projs: {manifest.projs}")
