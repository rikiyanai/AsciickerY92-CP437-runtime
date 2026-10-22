"""Configuration resolution utilities.

Centralizes the logic for deriving projs from angles, resolving
slicing parameters, and building complete AssetJobConfig instances.

Grid Resolution Priority:
1. Explicit SlicingSpec (--slice-spec file) wins.
2. Explicit slicing flags (--cell-w/--cell-h/--cols/--rows) build a SlicingSpec.
3. Inference from (image_size, angles, frames, projs) is used only when
   no explicit geometry is provided.

Failure policy:
- Non-divisible geometry is a hard error with exact mismatch numbers.
- No silent truncation/floor division for cell size or frame count.
- Negative or zero effective content areas are rejected immediately.
"""

from typing import Optional, Tuple

from .job import AssetJobConfig
from .slicing import SlicingSpec, BackgroundSpec, infer_sheet_spec


def resolve_projs(angles: int, explicit_projs: Optional[int] = None) -> int:
    """Derive projection count from angles.

    Engine contract (sprite.cpp): angles > 0 implies projs = 2
    (original + horizontal reflection).  angles = 0 means a flat
    sprite with projs = 1.

    Args:
        angles: Number of rotation angles.
        explicit_projs: Override value; returned as-is if not None.

    Returns:
        Projection count (1 or 2).
    """
    if explicit_projs is not None:
        return explicit_projs
    return 2 if angles > 0 else 1


def resolve_slicing(
    job: AssetJobConfig,
    img_size: Tuple[int, int],
) -> SlicingSpec:
    """Resolve the effective SlicingSpec for a job.

    If the job has an explicit slice_spec, returns it with any missing
    fields filled from image dimensions.  Otherwise infers from the
    image geometry.

    Args:
        job: Job configuration.
        img_size: (width, height) of the source image.

    Returns:
        Fully resolved SlicingSpec.
    """
    if job.slice_spec is not None:
        spec = job.slice_spec
        width, height = img_size

        cell_w = spec.cell_w_px
        cell_h = spec.cell_h_px
        cols = spec.cols
        rows = spec.rows

        # Compute effective content area after margins/spacing
        margin_x = spec.margin_x_px
        margin_y = spec.margin_y_px
        spacing_x = spec.spacing_x_px
        spacing_y = spec.spacing_y_px

        if cols is None and cell_w is not None and cell_w > 0:
            content_w = width - 2 * margin_x
            available_w = content_w + spacing_x
            cols = available_w // (cell_w + spacing_x)
        if rows is None and cell_h is not None and cell_h > 0:
            content_h = height - 2 * margin_y
            available_h = content_h + spacing_y
            rows = available_h // (cell_h + spacing_y)
        if cell_w is None and cols is not None and cols > 0:
            content_w = width - 2 * margin_x - max(0, cols - 1) * spacing_x
            if content_w <= 0:
                raise ValueError(
                    f"Negative content width: image width {width} minus "
                    f"margins 2*{margin_x} and spacing {cols - 1}*{spacing_x} "
                    f"= {content_w}px. Reduce margins/spacing or use a wider image."
                )
            remainder = content_w % cols
            if remainder != 0:
                raise ValueError(
                    f"Content width {content_w}px "
                    f"(image {width} - 2*{margin_x} margin - "
                    f"{max(0, cols - 1)}*{spacing_x} spacing) "
                    f"is not divisible by {cols} cols "
                    f"(remainder {remainder}px). "
                    f"Specify --cell-w explicitly."
                )
            cell_w = content_w // cols
        if cell_h is None and rows is not None and rows > 0:
            content_h = height - 2 * margin_y - max(0, rows - 1) * spacing_y
            if content_h <= 0:
                raise ValueError(
                    f"Negative content height: image height {height} minus "
                    f"margins 2*{margin_y} and spacing {rows - 1}*{spacing_y} "
                    f"= {content_h}px. Reduce margins/spacing or use a taller image."
                )
            remainder = content_h % rows
            if remainder != 0:
                raise ValueError(
                    f"Content height {content_h}px "
                    f"(image {height} - 2*{margin_y} margin - "
                    f"{max(0, rows - 1)}*{spacing_y} spacing) "
                    f"is not divisible by {rows} rows "
                    f"(remainder {remainder}px). "
                    f"Specify --cell-h explicitly."
                )
            cell_h = content_h // rows

        # Final validation: reject non-positive cell sizes
        if cell_w is not None and cell_w <= 0:
            raise ValueError(
                f"Resolved cell_w={cell_w} is non-positive. "
                f"Image width {width}, margins 2*{margin_x}, "
                f"spacing {max(0, (cols or 1) - 1)}*{spacing_x}. "
                f"Reduce margins/spacing or use a wider image."
            )
        if cell_h is not None and cell_h <= 0:
            raise ValueError(
                f"Resolved cell_h={cell_h} is non-positive. "
                f"Image height {height}, margins 2*{margin_y}, "
                f"spacing {max(0, (rows or 1) - 1)}*{spacing_y}. "
                f"Reduce margins/spacing or use a taller image."
            )

        return SlicingSpec(
            mode="explicit",
            cell_w_px=cell_w,
            cell_h_px=cell_h,
            cols=cols,
            rows=rows,
            margin_x_px=margin_x,
            margin_y_px=margin_y,
            spacing_x_px=spacing_x,
            spacing_y_px=spacing_y,
            origin=spec.origin,
            order=spec.order,
        )

    return infer_sheet_spec(img_size, job.angles, job.frames, job.projs)


def resolve_config(
    name: str = "unnamed",
    angles: int = 1,
    frames: tuple = (1,),
    source_type: str = "file",
    source_path: Optional[str] = None,
    blender_object: Optional[str] = None,
    asset_type: str = "custom",
    transparency: bool = False,
    normalization: bool = False,
    target_cells_high: int = 0,
    downscale_algorithm: Optional[str] = None,
    template_name: Optional[str] = None,
    explicit_projs: Optional[int] = None,
    source_projs: Optional[int] = None,
    slice_spec: Optional[SlicingSpec] = None,
    background: Optional[BackgroundSpec] = None,
    render_resolution: int = 24,
    reflection_policy: Optional[str] = None,
) -> AssetJobConfig:
    """Build a fully resolved AssetJobConfig.

    Derives projs from angles (unless explicit_projs is given) and
    assembles all parameters into a frozen config object.

    Args:
        name: Asset name.
        angles: Number of rotation angles.
        frames: Tuple of frame counts per animation.
        source_type: "file", "blender", or "ai".
        source_path: Path to the source image or blend file.
        blender_object: Blender object name (for source_type="blender").
        asset_type: Asset category string.
        transparency: Whether to use alpha transparency.
        normalization: Whether to normalize colors.
        target_cells_high: Target height in cells (0 = auto).
        downscale_algorithm: Downscale algorithm name.
        template_name: Template name to load.
        explicit_projs: Override for projs derivation.
        slice_spec: Explicit slicing specification.
        background: Background handling specification.
        render_resolution: Blender render resolution.

    Returns:
        AssetJobConfig.
    """
    projs = resolve_projs(angles, explicit_projs)
    slice_mode = "explicit" if slice_spec is not None else "auto"

    return AssetJobConfig(
        name=name,
        asset_type=asset_type,
        source_type=source_type,
        source_path=source_path,
        blender_object=blender_object,
        angles=angles,
        frames=frames,
        projs=projs,
        transparency=transparency,
        normalization=normalization,
        target_cells_high=target_cells_high,
        render_resolution=render_resolution,
        downscale_algorithm=downscale_algorithm,
        template_name=template_name,
        slice_spec=slice_spec,
        background=background,
        slice_mode=slice_mode,
        explicit_projs=explicit_projs,
        source_projs=source_projs,
        reflection_policy=reflection_policy,
    )
