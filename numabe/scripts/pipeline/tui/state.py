"""TUI state container shared across all screens.

TUIState is a mutable dataclass holding all wizard state.  Screens
read/write it freely.  When the user hits "Run", to_job_config()
produces a frozen AssetJobConfig for the pipeline.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class TUIState:
    """Mutable wizard state flowing through all TUI screens."""

    # Wizard intent
    intent: str = ""
    asset_type: str = "custom"
    template_name: Optional[str] = None

    # Source
    source_type: str = "file"
    source_path: str = ""
    blender_object: str = ""

    # Geometry
    name: str = "unnamed"
    angles: int = 1
    frames: str = "1"  # Comma-separated, parsed on export
    transparency: bool = False
    normalization: bool = False
    target_cells_high: int = 0
    render_resolution: int = 24  # Blender render resolution (px per cell)

    # Slicing
    cell_w: Optional[int] = None
    cell_h: Optional[int] = None
    cols: Optional[int] = None
    rows: Optional[int] = None
    margin_x: int = 0
    margin_y: int = 0
    spacing_x: int = 0
    spacing_y: int = 0
    origin: str = "top_left"
    order: str = "angle_major"
    angle_row_map: Optional[List[int]] = None

    # Background
    bg_mode: str = "key_color"
    bg_color: Tuple[int, int, int] = (255, 0, 255)
    bg_tolerance: int = 8

    # Reflection handling
    reflection_policy: Optional[str] = None  # "none", "generate", "detect", or None (=generate)
    synthesize_angles: Optional[int] = None  # Target angle count for synthesis (None=off)

    # Blender keyframe ranges (BLEND-15-03)
    keyframe_ranges: Optional[list] = None  # List[AnimationRange] | None

    # Analysis results (populated by analyze screen)
    analysis: Optional[dict] = None

    # Job result
    last_output: Optional[object] = None

    def parse_frames(self) -> tuple:
        """Parse comma-separated frames string into a tuple of ints."""
        try:
            parts = [int(x.strip()) for x in self.frames.split(",") if x.strip()]
            return tuple(parts) if parts else (1,)
        except ValueError:
            return (1,)

    def to_job_config(self):
        """Convert mutable TUI state to a frozen AssetJobConfig."""
        from scripts.pipeline.service.job import AssetJobConfig
        from scripts.pipeline.service.slicing import SlicingSpec, BackgroundSpec
        from scripts.pipeline.service.config_resolver import resolve_projs

        frames_tuple = self.parse_frames()
        projs = resolve_projs(self.angles)

        # Build SlicingSpec only if any slicing field is explicitly set
        slice_spec = None
        if any([
            self.cell_w is not None,
            self.cell_h is not None,
            self.cols is not None,
            self.rows is not None,
            self.margin_x > 0,
            self.margin_y > 0,
            self.spacing_x > 0,
            self.spacing_y > 0,
            self.origin != "top_left",
            self.order != "angle_major",
            self.angle_row_map is not None,
        ]):
            slice_spec = SlicingSpec(
                cell_w_px=self.cell_w,
                cell_h_px=self.cell_h,
                cols=self.cols,
                rows=self.rows,
                margin_x_px=self.margin_x,
                margin_y_px=self.margin_y,
                spacing_x_px=self.spacing_x,
                spacing_y_px=self.spacing_y,
                origin=self.origin,
                order=self.order,
                angle_row_map=self.angle_row_map,
            )

        # Build BackgroundSpec
        bg_spec = BackgroundSpec(
            mode=self.bg_mode,
            key_color=self.bg_color,
            tolerance=self.bg_tolerance,
        )

        return AssetJobConfig(
            name=self.name,
            asset_type=self.asset_type,
            source_type=self.source_type,
            source_path=self.source_path if self.source_path else None,
            blender_object=self.blender_object if self.blender_object else None,
            angles=self.angles,
            frames=frames_tuple,
            projs=projs,
            transparency=self.transparency,
            normalization=self.normalization,
            target_cells_high=self.target_cells_high,
            render_resolution=self.render_resolution,
            template_name=self.template_name,
            slice_spec=slice_spec,
            background=bg_spec,
            slice_mode="explicit" if slice_spec else "auto",
            reflection_policy=self.reflection_policy,
            synthesize_angles=self.synthesize_angles,
            keyframe_ranges=self.keyframe_ranges,
        )
