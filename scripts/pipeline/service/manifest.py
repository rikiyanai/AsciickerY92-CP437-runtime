"""Batch manifest types and parsing helpers.

ManifestEntry represents a single asset in a batch manifest YAML/JSON.
Helper functions convert raw dicts to SlicingSpec and BackgroundSpec.
"""

from dataclasses import dataclass, field
from typing import Optional

from .config_resolver import resolve_config
from .slicing import SlicingSpec, BackgroundSpec


@dataclass
class ManifestEntry:
    """Single asset entry in a batch manifest."""

    name: str = "unnamed"
    input: str = ""
    angles: Optional[int] = None
    frames: str = "1"
    asset_type: str = "custom"
    source_type: str = "file"
    transparency: Optional[bool] = None
    normalization: Optional[bool] = None
    target_cells_high: Optional[int] = None
    source_projs: Optional[int] = None
    reflection_policy: Optional[str] = None
    slice_spec: Optional[dict] = None
    background: Optional[dict] = None


def _parse_slicing_dict(data: Optional[dict]) -> Optional[SlicingSpec]:
    """Parse a raw dict into a SlicingSpec, or return None."""
    if data is None:
        return None
    return SlicingSpec(
        mode=data.get("mode", "grid"),
        cell_w_px=data.get("cell_w_px"),
        cell_h_px=data.get("cell_h_px"),
        cols=data.get("cols"),
        rows=data.get("rows"),
        margin_x_px=data.get("margin_x_px", 0),
        margin_y_px=data.get("margin_y_px", 0),
        spacing_x_px=data.get("spacing_x_px", 0),
        spacing_y_px=data.get("spacing_y_px", 0),
        origin=data.get("origin", "top_left"),
        order=data.get("order", "angle_major"),
    )


def _parse_background_dict(data: Optional[dict]) -> Optional[BackgroundSpec]:
    """Parse a raw dict into a BackgroundSpec, or return None."""
    if data is None:
        return None

    key_color = data.get("key_color", (255, 0, 255))
    if isinstance(key_color, list):
        key_color = tuple(key_color)

    return BackgroundSpec(
        mode=data.get("mode", "key_color"),
        key_color=key_color,
        tolerance=data.get("tolerance", 8),
    )


def entry_to_job_config(entry: ManifestEntry, defaults: dict):
    """Convert a ManifestEntry + defaults dict into AssetJobConfig.

    Uses `is not None` checks instead of falsy `or` to avoid swallowing
    valid False/0 values from the entry.
    """
    frames_str = entry.frames if entry.frames else defaults.get("frames", "1")
    frames = tuple(int(x.strip()) for x in frames_str.split(",") if x.strip())
    if not frames:
        frames = (1,)

    angles = entry.angles if entry.angles is not None else defaults.get("angles", 1)

    slice_spec = _parse_slicing_dict(
        entry.slice_spec if entry.slice_spec is not None else defaults.get("slice_spec")
    )
    background = _parse_background_dict(
        entry.background if entry.background is not None else defaults.get("background")
    )

    # Falsy-safe: use `is not None` for bool/int fields to avoid swallowing False/0
    transparency = (
        entry.transparency if entry.transparency is not None
        else defaults.get("transparency", False)
    )
    normalization = (
        entry.normalization if entry.normalization is not None
        else defaults.get("normalization", False)
    )
    target_cells_high = (
        entry.target_cells_high if entry.target_cells_high is not None
        else defaults.get("target_cells_high", 0)
    )
    source_projs = (
        entry.source_projs if entry.source_projs is not None
        else defaults.get("source_projs", 1)
    )
    reflection_policy = (
        entry.reflection_policy if entry.reflection_policy is not None
        else defaults.get("reflection_policy", "generate")
    )

    return resolve_config(
        name=entry.name,
        angles=angles,
        frames=frames,
        source_type=entry.source_type or defaults.get("source_type", "file"),
        source_path=entry.input or None,
        asset_type=entry.asset_type or defaults.get("asset_type", "custom"),
        transparency=transparency,
        normalization=normalization,
        target_cells_high=target_cells_high,
        source_projs=source_projs,
        slice_spec=slice_spec,
        background=background,
        reflection_policy=reflection_policy,
    )
