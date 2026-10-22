"""
models.py -- Typed dataclass models for the template-driven asset generation system.

ARCHITECTURE:
  This module defines the core data structures that represent a parsed template.
  Template JSON files (e.g. character_idle_walk.json) are loaded by loader.py,
  validated against schemas defined in schemas.py, and then hydrated into these
  dataclasses. The resulting Template object is passed through the pipeline
  (cli.py -> pipeline.py) to control every stage of asset generation: Blender
  rendering parameters, sprite sheet layout, image processing flags, and .xp
  output dimensions.

  Data flow:
    JSON file --> loader.py --> Template (this module) --> pipeline.py / cli.py

KEY EXPORTS:
  - Template: Top-level container holding all template sections plus layout helpers.
  - ProcessingSection: Image processing flags (magenta snap, quantize, downscale).
  - SourceSection: Where the source image comes from (file, AI, Blender).
  - LayoutSection: Sprite sheet grid layout (rows, cols, frame ordering).
  - DebugSection: Optional debug sheet and intermediate output controls.
  - OutputSection: Explicit .xp and .png output path overrides.
  - Type aliases: AssetType, SourceType, DownscaleType.

PIPELINE CONTEXT:
  [FLOW:TEMPLATE] -- These dataclasses are the canonical in-memory representation
  of a template after loading. Every downstream consumer reads from these models.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Literal

# [FLOW:TEMPLATE] Type aliases constraining the legal values for template fields.
# These mirror the "enum" constraints in schemas.py JSON Schema definitions.
# Downstream code (pipeline.py, cli.py) performs isinstance/equality checks
# against these literal types rather than raw strings.
AssetType = Literal["character", "item", "ui", "custom"]
SourceType = Literal["file", "ai", "blender"]
DownscaleType = Literal["nearest", "box", "area", "block-majority"]


@dataclass
class ProcessingSection:
    """Image processing flags applied during pipeline Stage 1 (Generation) and Stage 3 (Processing).

    These flags control how raw source images are transformed before .xp assembly.
    When a template specifies processing options, they override the pipeline defaults.
    CLI flags can further override template values (see cli.py resolve_field_config).

    [FLOW:TEMPLATE] -- Read by pipeline.apply_template_processing() to build
    the processing_config dict that drives generation/downscaling.
    """

    magenta_snap: bool = False
    palette_quantize: bool = False
    # [DEPENDENCY:BLENDER] -- "box" is the default because AI-generated images
    # (the most common source) have smooth gradients that benefit from box
    # averaging rather than nearest-neighbor aliasing. Blender renders use
    # "nearest" by default (set in cli.py DEFAULT_BY_SOURCE), but templates
    # can override this per-asset.
    downscale: DownscaleType = "box"
    crop_center: bool = False
    # WHY: normalization auto-scales each angle strip to a target cell height
    # and adds 2px top/bottom padding. This is critical for characters whose
    # raw renders vary in height across angles (e.g. side view vs front view).
    normalization: bool = False


@dataclass
class SourceSection:
    """Describes where the input image originates.

    [DEPENDENCY:BLENDER] -- When type="blender", blender_object names the object
    to render and render_resolution sets the per-cell pixel size for the Blender
    camera. The Blender render script (scripts/blender/render_sprite.py) reads
    these values to configure the scene.

    [FLOW:CLI] -- The source section is also populated from CLI --source-type,
    --blender-object, and --input flags when running in raw mode.
    """

    type: SourceType = "file"
    path: Optional[str] = None
    blender_object: Optional[str] = None
    # WHY: 24 = 2x cell size, matching the canonical default across all entry
    # points.  See service/constants.py DEFAULT_RENDER_RESOLUTION.
    render_resolution: int = 24  # Canonical default: 24 (see service/constants.py)
    transparency: bool = False


@dataclass
class LayoutSection:
    """Sprite sheet grid layout configuration.

    Defines how frames are arranged in the final sprite sheet. The standard
    layout is rows=angles (one row per camera angle) and cols=sum(frames)
    (total animation frames across all animations as columns).

    [DATA-CONTRACT:XP] -- The layout determines the .xp layer structure:
    each row becomes a set of layers in the output .xp file.
    """

    # WHY: String values ("angles", "sum(frames)") rather than ints allow
    # templates to express intent declaratively. The pipeline resolves these
    # to concrete numbers via Template.layout_rows() / layout_cols().
    rows: str = "angles"
    cols: str = "sum(frames)"
    # Frame size in pixels (width, height). Each frame in the sprite sheet
    # occupies this many pixels. Default (12, 12) = 12x12 pixels per frame.
    # [DATA-CONTRACT:XP] -- Must match the game engine's expected sprite size.
    frame_size: Tuple[int, int] = (12, 12)
    # TODO(PIPELINE-FIX): frame_order is defined in JSON templates (e.g.
    # character_idle_walk.json) but never consumed by the pipeline. The
    # assembler currently assumes sequential frame ordering. If frame_order
    # is intended to reorder columns, the slicer/assembler need to respect it.
    frame_order: Optional[List[str]] = None


@dataclass
class DebugSection:
    """Controls optional debug output: labeled sprite sheets and intermediate PNGs.

    Debug sheets are opt-in. Generation is triggered when label_sheet is a
    non-empty string (the output path) or when labels is True (uses a default
    path). See pipeline.should_generate_debug_sheet().
    """

    labels: bool = False
    label_format: str = "A{angle}-F{frame}"
    # WHY: Empty string (not None) as default makes the opt-in check simpler --
    # any truthy value means the user explicitly requested a debug sheet.
    label_sheet: str = ""
    save_intermediate: bool = False


@dataclass
class OutputSection:
    """Explicit output path overrides for .xp and .png files.

    [DATA-CONTRACT:XP] -- xp_path, when set, overrides the default staging
    path (staging/xp/{name}.xp). If omitted, the pipeline computes the path
    from the template name.
    """

    # TODO(PIPELINE-FIX): These path overrides are defined in the model but
    # never read by pipeline.py or cli.py. The pipeline always writes to
    # STAGING_DIR / "xp" / f"{name}.xp". These fields should either be
    # wired into the pipeline or removed to avoid confusion.
    xp_path: Optional[str] = None
    png_path: Optional[str] = None


@dataclass
class Template:
    """Top-level template object representing a fully parsed and validated template.

    Constructed by TemplateLoader.from_file() or TemplateLoader.from_dict() after
    JSON schema validation. Provides helper methods for computing sprite sheet
    dimensions that are used by the grid validator and pipeline.

    [FLOW:TEMPLATE] -- This is the single source of truth for template data once
    loaded. Passed to pipeline.run(template=...) and used throughout all stages.
    """

    # Required fields -- must be present in every template JSON.
    # Validated by REQUIRED_FIELDS_SCHEMA in schemas.py.
    version: int
    name: str
    type: AssetType
    # WHY: angles is constrained to {1, 4, 8} by the schema. Characters use 8
    # (N, NE, E, SE, S, SW, W, NW), items typically use 1, and 4 covers
    # cardinal directions only.
    angles: int
    # WHY: frames is a list (not a single int) because different animations
    # within the same template can have different frame counts. For example,
    # character_idle_walk.json has frames=[1, 8] (1 idle + 8 walk frames).
    frames: List[int]

    # Optional per-animation keyframe ranges for Blender renders.
    # When provided, the generator dispatches per-range renders instead of
    # sequential frame rendering from the Blender timeline.
    # [DATA-CONTRACT:ASSET-DEF] Mapped to AssetDef.keyframe_ranges in pipeline.
    keyframe_ranges: Optional[list] = None

    # Optional sections -- default to empty/default instances if not in JSON.
    processing: ProcessingSection = field(default_factory=ProcessingSection)
    source: SourceSection = field(default_factory=SourceSection)
    layout: LayoutSection = field(default_factory=LayoutSection)
    debug: DebugSection = field(default_factory=DebugSection)
    output: OutputSection = field(default_factory=OutputSection)

    def total_frames(self) -> int:
        """Total number of frames across all animations (sum of frames list).

        Used by layout_cols() and expected_dimensions() to compute the sprite
        sheet width.
        """
        return sum(self.frames)

    def layout_cols(self) -> int:
        """Number of columns in the sprite sheet layout.

        [DATA-CONTRACT:XP] -- Each column is one animation frame. The total
        column count equals sum(frames), i.e. every animation's frames laid
        out sequentially.
        """
        return self.total_frames()

    def layout_rows(self) -> int:
        """Number of rows in the sprite sheet layout.

        [DATA-CONTRACT:XP] -- Each row is one camera angle. For an 8-angle
        character, this returns 8.
        """
        return self.angles

    def expected_dimensions(self) -> Tuple[int, int]:
        """Expected sprite sheet dimensions (width, height) in pixels.

        [DATA-CONTRACT:XP] -- Pixel dimensions are calculated as:
          width  = columns * frame_width_pixels
          height = rows * frame_height_pixels

        The frame_size from the layout section specifies frame dimensions
        in pixels (not cells). This matches how users naturally measure
        their sprite sheets.

        Returns:
            Tuple of (width_px, height_px).
        """
        cols = self.layout_cols()
        rows = self.layout_rows()
        # Frame size is already in pixels (not cells)
        frame_w_px, frame_h_px = self.layout.frame_size
        width = cols * frame_w_px
        height = rows * frame_h_px
        return (width, height)
