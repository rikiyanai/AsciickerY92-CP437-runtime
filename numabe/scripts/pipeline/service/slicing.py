"""Slicing and background specification dataclasses.

These frozen dataclasses flow from job configuration through to the
slicer stage, controlling how sprite sheets are divided into cells.

Grid Resolution Priority:
1. Explicit SlicingSpec (--slice-spec) wins.
2. Explicit slicing flags (--cell-w/--cell-h/--cols/--rows/...) build SlicingSpec.
3. Inference from (image_size, angles, frames, projs) is the fallback.

Failure policy:
- Non-divisible geometry is a hard error with exact mismatch numbers.
- No silent truncation/floor division for cell size or frame count.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .constants import DEFAULT_BG_TOLERANCE


@dataclass
class GridDiagnostics:
    """Structured diagnostics for grid inference decisions.

    Emitted as part of every infer_sheet_spec call to enable tracing
    and debugging of grid resolution decisions.
    """

    method: str = "inferred"
    image_size: Tuple[int, int] = (0, 0)
    total_cols: int = 0
    rows: int = 0
    cell_w_px: int = 0
    cell_h_px: int = 0
    remainder_x: int = 0
    remainder_y: int = 0
    divisible: bool = True
    confidence: str = "high"
    error: Optional[str] = None


@dataclass
class SlicingSpec:
    """Specification for how a sprite sheet is sliced into cells.

    All pixel fields are optional -- when None, the slicer infers
    values from image dimensions and metadata.

    Order modes (user-facing names in parentheses):
        angle_major      -- Row-per-angle: each row is one rotation angle,
                            columns cycle frames then projs.
                            (default, matches existing sprite library)
        animation_major  -- Row-per-animation: each row is one animation frame,
                            columns cycle angles.  Use for sheets where
                            animations run top-to-bottom.
        frame_major      -- Column-major (legacy): columns cycle angles first,
                            then frames.  Prefer angle_major or animation_major
                            for new assets.
    """

    mode: str = "auto"
    cell_w_px: Optional[int] = None
    cell_h_px: Optional[int] = None
    cols: Optional[int] = None
    rows: Optional[int] = None
    margin_x_px: int = 0
    margin_y_px: int = 0
    spacing_x_px: int = 0
    spacing_y_px: int = 0
    origin: str = "top_left"
    order: str = "angle_major"  # "angle_major" | "frame_major" | "animation_major"
    frame_major: bool = False  # DEPRECATED: use order="frame_major" instead
    angle_row_map: Optional[List[int]] = None


@dataclass
class BackgroundSpec:
    """Specification for background handling during assembly.

    Controls whether the assembler uses key-color replacement,
    alpha-based transparency, or no background processing.

    Validation:
        ``__post_init__`` rejects out-of-range values for tolerance,
        alpha_threshold, and key_color components.  All must be in 0-255.
    """

    mode: str = "key_color"
    key_color: Tuple[int, int, int] = (255, 0, 255)
    tolerance: int = DEFAULT_BG_TOLERANCE
    alpha_threshold: int = 128

    def __post_init__(self):
        if not (0 <= self.tolerance <= 255):
            raise ValueError(
                f"BackgroundSpec.tolerance must be 0-255, got {self.tolerance}"
            )
        if not (0 <= self.alpha_threshold <= 255):
            raise ValueError(
                f"BackgroundSpec.alpha_threshold must be 0-255, "
                f"got {self.alpha_threshold}"
            )
        for i, c in enumerate(self.key_color):
            if not (0 <= c <= 255):
                channel = ("R", "G", "B")[i]
                raise ValueError(
                    f"BackgroundSpec.key_color[{channel}] must be 0-255, "
                    f"got {c}"
                )


def infer_sheet_spec(
    img_size: Tuple[int, int],
    angles: int,
    frames: tuple,
    projs: int,
) -> SlicingSpec:
    """Infer a SlicingSpec from image dimensions and metadata.

    Deterministic contract:
    - total_cols = sum(frames) * projs
    - rows = max(angles, 1)
    - cell_w = width / total_cols  (must divide exactly)
    - cell_h = height / rows       (must divide exactly)

    Raises ValueError with actionable diagnostics when geometry is
    non-divisible.  Never silently truncates via floor division.

    Args:
        img_size: (width, height) of the sprite sheet.
        angles: Number of rotation angles.
        frames: Tuple of frame counts per animation.
        projs: Number of projections (1 or 2).

    Returns:
        SlicingSpec with inferred cell dimensions and grid layout.

    Raises:
        ValueError: When image dimensions are not evenly divisible
            by the computed grid layout.
    """
    width, height = img_size
    total_frames = sum(frames)
    total_cols = total_frames * projs
    rows = max(angles, 1)

    if width <= 0 or height <= 0:
        raise ValueError(
            f"Cannot infer grid: image size {width}x{height} is non-positive. "
            f"Provide a valid image."
        )

    if total_cols <= 0:
        raise ValueError(
            f"Cannot infer grid: total_cols={total_cols} "
            f"(sum(frames)={total_frames}, projs={projs}). "
            f"Need at least 1 column. "
            f"Specify --cell-w and --cols explicitly."
        )
    if rows <= 0:
        raise ValueError(
            f"Cannot infer grid: rows={rows} (angles={angles}). "
            f"Need at least 1 row. "
            f"Specify --cell-h and --rows explicitly."
        )

    remainder_x = width % total_cols
    remainder_y = height % rows

    if remainder_x != 0 or remainder_y != 0:
        cell_w_approx = width / total_cols
        cell_h_approx = height / rows
        hints = []
        if remainder_x != 0:
            hints.append(
                f"width {width} / {total_cols} cols = "
                f"{cell_w_approx:.2f}px/cell (remainder {remainder_x}px)"
            )
        if remainder_y != 0:
            hints.append(
                f"height {height} / {rows} rows = "
                f"{cell_h_approx:.2f}px/cell (remainder {remainder_y}px)"
            )
        raise ValueError(
            f"Image dimensions {width}x{height} do not divide evenly "
            f"into {total_cols} cols x {rows} rows "
            f"(frames={list(frames)}, projs={projs}, angles={angles}).\n"
            f"  {'; '.join(hints)}\n"
            f"Fix: supply explicit --cell-w/--cell-h or --cols/--rows "
            f"to override inference."
        )

    cell_w = width // total_cols
    cell_h = height // rows

    if cell_w <= 0 or cell_h <= 0:
        raise ValueError(
            f"Inferred cell size {cell_w}x{cell_h} is non-positive "
            f"(image {width}x{height}, {total_cols} cols, {rows} rows). "
            f"Image is too small for the requested grid layout. "
            f"Specify --cell-w and --cell-h explicitly."
        )

    return SlicingSpec(
        mode="inferred",
        cell_w_px=cell_w,
        cell_h_px=cell_h,
        cols=total_cols,
        rows=rows,
    )


def compute_grid_diagnostics(
    img_size: Tuple[int, int],
    angles: int,
    frames: tuple,
    projs: int,
    spec: Optional[SlicingSpec] = None,
) -> GridDiagnostics:
    """Compute grid diagnostics without raising.

    Always returns a GridDiagnostics object, even for invalid layouts.
    Used by the pipeline trace and analyze command.

    Args:
        img_size: (width, height) of the sprite sheet.
        angles: Number of rotation angles.
        frames: Tuple of frame counts per animation.
        projs: Number of projections (1 or 2).
        spec: Optional explicit SlicingSpec (overrides inference).

    Returns:
        GridDiagnostics with computed geometry and divisibility verdict.
    """
    width, height = img_size

    if spec is not None and spec.mode not in ("auto", "inferred"):
        # Explicit spec -- report its geometry
        cell_w = spec.cell_w_px or 0
        cell_h = spec.cell_h_px or 0
        cols = spec.cols or 0
        rows = spec.rows or 0
        method = "explicit_spec"
    else:
        # Check for explicit flags (partial spec)
        from scripts.pipeline.slicer import _spec_is_active

        if spec is not None and _spec_is_active(spec):
            method = "explicit_flags"
            cols = spec.cols if spec.cols is not None else (sum(frames) * projs)
            rows = spec.rows if spec.rows is not None else max(angles, 1)
            margin_x = spec.margin_x_px
            margin_y = spec.margin_y_px
            spacing_x = spec.spacing_x_px
            spacing_y = spec.spacing_y_px

            if spec.cell_w_px is not None:
                cell_w = spec.cell_w_px
            else:
                content_w = width - 2 * margin_x - max(0, cols - 1) * spacing_x
                cell_w = content_w // cols if cols > 0 else 0

            if spec.cell_h_px is not None:
                cell_h = spec.cell_h_px
            else:
                content_h = height - 2 * margin_y - max(0, rows - 1) * spacing_y
                cell_h = content_h // rows if rows > 0 else 0
        else:
            method = "inferred"
            total_frames = sum(frames)
            cols = total_frames * projs
            rows = max(angles, 1)
            cell_w = width // cols if cols > 0 else 0
            cell_h = height // rows if rows > 0 else 0

    remainder_x = width % cols if cols > 0 else width
    remainder_y = height % rows if rows > 0 else height
    divisible = (remainder_x == 0 and remainder_y == 0)

    error_msg = None
    if not divisible:
        hints = []
        if remainder_x != 0:
            hints.append(
                f"width {width} / {cols} cols "
                f"= {width / cols:.2f}px/cell (remainder {remainder_x}px)"
                if cols > 0 else f"width {width} / 0 cols = N/A (0 cols)"
            )
        if remainder_y != 0:
            hints.append(
                f"height {height} / {rows} rows "
                f"= {height / rows:.2f}px/cell (remainder {remainder_y}px)"
                if rows > 0 else f"height {height} / 0 rows = N/A (0 rows)"
            )
        error_msg = (
            f"Non-divisible: {width}x{height} into {cols}x{rows}. "
            f"{'; '.join(hints)}. "
            f"Fix: supply explicit --cell-w/--cell-h or --cols/--rows."
        )

    confidence = "high" if divisible else "failed"

    return GridDiagnostics(
        method=method,
        image_size=img_size,
        total_cols=cols,
        rows=rows,
        cell_w_px=cell_w,
        cell_h_px=cell_h,
        remainder_x=remainder_x,
        remainder_y=remainder_y,
        divisible=divisible,
        confidence=confidence,
        error=error_msg,
    )
