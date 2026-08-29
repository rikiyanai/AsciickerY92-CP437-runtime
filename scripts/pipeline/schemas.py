"""
schemas.py -- Asset definition data contract for the sprite generation pipeline.

ARCHITECTURE:
  This module defines the canonical ``AssetDef`` dataclass that every pipeline
  stage receives as its configuration object.  It is the single authoritative
  schema for describing *what* a sprite asset should look like (dimensions,
  angles, animation frames, source type) before any pixel processing begins.

  AssetDef instances are created by:
    - cli.py (interactive or argument-driven)
    - presets.py (pre-built templates like ORC_TEMPLATE)
    - pipeline.py (deserialized from JSON template files)

  AssetDef instances are consumed by:
    - generator.py   [PIPELINE:GENERATE]  -- loads/generates source images
    - processor.py   [PIPELINE:PROCESS]   -- converts images to glyph grids
    - pipeline.py    [PIPELINE:ASSEMBLE]  -- orchestrates the full pipeline

KEY EXPORTS:
  - AssetType: Literal type alias -- "character", "item", or "custom"
  - SourceType: Literal type alias -- "file", "ai", or "blender"
  - AssetDef: Core dataclass defining an asset's structural requirements

PIPELINE CONTEXT:
  [DATA-CONTRACT:ASSET-DEF] -- Consumed by every pipeline stage as the
    primary configuration object.  Fields map 1:1 to CLI arguments in cli.py.
  [FLOW:CLI] -- AssetDef instances are built from CLI args in cli.py.
  [FLOW:TEMPLATE] -- Also hydrated from JSON templates via pipeline.py.
  [DEPENDENCY:NONE] -- Pure Python stdlib only (dataclasses, typing).
"""

from dataclasses import dataclass, field
from typing import Any, List, Tuple, Literal, Optional

# [DATA-CONTRACT:ASSET-DEF] Valid asset categories recognized by the pipeline.
# WHY: "custom" exists as an escape hatch for non-standard assets that don't
# fit the character (8-angle) or item (1-angle) mold.  It bypasses the
# angle-count enforcement in validate().
AssetType = Literal["character", "item", "custom"]

# [DATA-CONTRACT:ASSET-DEF] Supported 3D mesh file extensions for auto-import.
# WHY: These formats can be imported by Blender's built-in operators and
# auto-converted to .blend files for the rendering pipeline.
MESH_EXTENSIONS = frozenset({'.obj', '.stl', '.fbx', '.gltf', '.glb', '.ply'})

# [DATA-CONTRACT:ASSET-DEF] Valid source backends for image acquisition.
# WHY: "ai" sources assume magenta-keyed backgrounds from AI image generators;
# "blender" sources use MCP or subprocess rendering; "file" is a raw image path;
# "mesh" sources auto-convert 3D model files to .blend before rendering.
SourceType = Literal["file", "ai", "blender", "mesh", "engine"]


@dataclass
class AnimationRange:
    """Blender keyframe range for one animation sequence.

    Used to map template frame counts to Blender timeline segments.
    Example: frames=[1,8] + keyframe_ranges=[
        AnimationRange(count=1, keyframe_start=0, keyframe_end=0, name="idle"),
        AnimationRange(count=8, keyframe_start=1, keyframe_end=24, name="walk")
    ]

    [DATA-CONTRACT:ASSET-DEF] Consumed by generator.py to dispatch per-range
    Blender renders instead of sequential frame rendering.
    """
    count: int              # Number of output frames for this animation
    keyframe_start: int     # Blender start keyframe (inclusive)
    keyframe_end: int       # Blender end keyframe (inclusive)
    name: str = ""          # Optional label (e.g., "idle", "walk", "attack")


@dataclass
class AssetDef:
    """
    Defines the structural requirements for a single game asset.

    This is the pipeline's central configuration object.  Every field here
    corresponds to a CLI flag (see cli.py) or a JSON template key (see
    templates/loader.py).  The ``validate()`` method enforces engine-level
    constraints before any expensive image processing begins.

    Attributes:
        name: Unique identifier used for output filenames and staging paths.
        type: Asset category -- determines validation rules (e.g. characters
            require 8 angles).
        angles: Number of viewing angles (1, 4, or 8).  Maps to sprite sheet
            columns in the final XP output.
        frames: Per-animation frame counts.  Length = number of animations;
            each entry = number of frames in that animation.
        size: (width, height) in character cells.  Pixel size = size * 12.
        prompt: Text prompt for AI-based image generation (source_type="ai").
        source_type: How to acquire the source image.
        source_path: Explicit file path override (source_type="file").
        blender_object: Blender object name for MCP rendering (source_type="blender").
        render_resolution: Pixels-per-cell for Blender renders.
        transparency: Whether the asset uses magenta-key transparency.
        normalization: Whether to normalize scale and pad across angles.
        target_cells_high: Target height in cells (0 = auto-derive from size).
    """

    name: str
    type: AssetType
    angles: int = 1
    frames: List[int] = field(default_factory=lambda: [1])
    size: Tuple[int, int] = (0, 0)  # Width, Height in characters; (0,0) sentinel = unset
    prompt: str = ""
    source_type: SourceType = (
        "file"  # Source: file, ai (magenta bg), or blender MCP/subprocess
    )
    source_path: str | None = None
    blender_object: str | None = None
    mesh_source_path: str | None = None  # Original mesh file path (before .blend conversion)
    render_resolution: int = 24  # Canonical default: 24 (see service/constants.py)
    transparency: bool = False  # Has magenta transparency?
    normalization: bool = False  # Normalize scale & pad per angle?
    target_cells_high: int = 0  # Target height in cells per angle (0=auto)
    projs: int | None = None  # Projections: None=default(1), 2=reflections baked in sheet
    source_projs: int | None = None  # Source image projections: 1=single (generate reflections), 2=pre-baked, None=legacy
    slice_spec: Any = None  # SlicingSpec | None — explicit slicing grid (None = auto-infer)
    background: Any = None  # BackgroundSpec | None — background handling (None = magenta key)
    reflection_policy: str | None = None  # "none", "generate", "detect", or None (=generate)
    synthesize_angles: int | None = None  # Target angle count for synthesis (None=off)
    pre_slice_check: bool = False  # Enable pre-slice extractor check (default: off)
    pre_slice_check_strict: bool = False  # Upgrade mismatch to hard fail
    pixel_perfect_mode: str = "off"  # "off" or "auto_adjust" — pixel-perfect normalization
    keyframe_ranges: Optional[list] = None  # List[AnimationRange] | None — per-animation Blender keyframe ranges

    # [DATA-CONTRACT:ASSET-DEF] Validation boundary -- catches bad configs
    # before expensive image processing begins.
    def validate(self) -> List[str]:
        """
        Check engine-level constraints on this asset definition.

        Returns:
            A list of human-readable error strings.  Empty list = valid.

        Note:
            This only checks structural validity (angle counts, frame counts).
            It does NOT verify that source_path exists or that the prompt is
            non-empty -- those checks happen at generation time in generator.py.
        """
        errors = []

        # NOTE: Removed hard [1,4,8] gate per Phase 2. Engine can load any
        # angle count that divides height evenly (ENG-06 invariant).
        # Policy layers (character presets) may still warn/restrict.
        # Character presets still require 8 angles for gameplay.
        if self.type == "character" and self.angles != 8:
            errors.append(f"Characters must have 8 angles, got {self.angles}.")

        # WHY: An empty frames list produces sum(frames)=0, crashing the
        # slicer and assembler with division-by-zero on column width.
        if not self.frames:
            errors.append("Animation frames list must not be empty.")

        # WHY: Zero-frame animations produce empty sprite sheet columns that
        # crash the assembler's XP serializer (division-by-zero on column width).
        if any(f < 1 for f in self.frames):
            errors.append("Animation frames must be >= 1.")

        # TODO(PIPELINE-FIX): No validation for size > 0, or for source_path
        #   existence when source_type="file".  These fail later in generator.py
        #   with unclear error messages.

        return errors
