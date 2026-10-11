"""
adapters.py -- Convert various input formats to AssetJobConfig.

Each function bridges one caller type (CLI, wizard, batch, MCP) to the
unified AssetJobConfig schema.  All adapters delegate projs derivation
and algorithm resolution to config_resolver.resolve_config().

Adapter functions:
    create_job_from_cli_args()    -- from argparse.Namespace + template
    create_job_from_wizard()      -- from WizardResult
    create_job_from_batch()       -- from ManifestEntry + defaults
    create_job_from_mcp()         -- from MCP tool params dict

New unified import path:
    ImportRequest               -- Single request schema for PNG imports
    build_job_from_import_request() -- Convert ImportRequest → AssetJobConfig
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config_resolver import resolve_config
from .job import AssetJobConfig
from .slicing import SlicingSpec, BackgroundSpec
from .manifest import ManifestEntry, _parse_slicing_dict, _parse_background_dict


@dataclass
class ImportRequest:
    """Unified request schema for PNG→XP imports.

    Consumed by both CLI and xp_tool entry points to ensure identical
    behavior. All fields are explicit — no hidden defaults.

    Fields:
        name: Output sprite name
        source_path: Path to source PNG
        source_type: "file", "ai", "blender"
        angles: Number of rotation angles
        frames: Frame counts per animation (list)
        render_resolution: Blender render or downscale resolution
        explicit_projs: Explicit projection override (None = derive)
        slice_spec: Slicing parameters (None = infer)
        background: Background handling spec (None = auto-detect)
        import_mode: "as_is" | "sheet_explicit" | "compatibility"
        reflection_policy: "none" | "generate" | "detect" (legacy "explicit" maps to "none")
        downscale_policy: "off" | "explicit"
        asset_type: Asset category string
        synthesize_angles: Target angle count for synthesis (None = off)
    """

    name: str
    source_path: str
    frames: list  # Required — callers must specify e.g. [1] or [1,8]
    source_type: str = "file"
    angles: int = 1
    render_resolution: int = 24
    explicit_projs: Optional[int] = None
    source_projs: int = 1  # Source image projections: 1=single (generate reflections), 2=pre-baked
    slice_spec: Optional[SlicingSpec] = None
    background: Optional[BackgroundSpec] = None
    import_mode: str = "as_is"
    reflection_policy: str = "generate"  # fail-closed default: generate reflections, not detect
    downscale_policy: str = "off"
    asset_type: str = "custom"
    synthesize_angles: Optional[int] = None

    def __post_init__(self):
        if not self.frames or not isinstance(self.frames, (list, tuple)):
            raise ValueError(
                "ImportRequest.frames is required and must be a non-empty list "
                "(e.g. [1] for static, [1,8] for idle+walk). "
                "Specify animation frame counts explicitly."
            )


def build_job_from_import_request(request: ImportRequest) -> AssetJobConfig:
    """Convert ImportRequest → AssetJobConfig.

    Single adapter path for all import operations. No entry point should
    construct AssetDef or AssetJobConfig directly after this wave.

    Args:
        request: Validated import request

    Returns:
        AssetJobConfig ready for AssetService.run()
    """
    frames_tuple = tuple(request.frames) if isinstance(request.frames, list) else (1,)

    job = resolve_config(
        name=request.name,
        angles=request.angles,
        frames=frames_tuple,
        source_type=request.source_type,
        source_path=request.source_path,
        asset_type=request.asset_type,
        render_resolution=request.render_resolution,
        explicit_projs=request.explicit_projs,
        source_projs=getattr(request, "source_projs", 1),
        slice_spec=request.slice_spec,
        background=request.background,
    )

    # Wire reflection_policy from ImportRequest
    reflection_policy = getattr(request, "reflection_policy", None)
    if reflection_policy is not None:
        job.reflection_policy = reflection_policy

    # Wire synthesize_angles from ImportRequest
    synthesize_angles = getattr(request, "synthesize_angles", None)
    if synthesize_angles is not None:
        job.synthesize_angles = synthesize_angles

    return job


def create_job_from_cli_args(
    args,
    template=None,
) -> AssetJobConfig:
    """Build AssetJobConfig from parsed CLI arguments.

    Accepts the --slice-* and --bg-* flags introduced in Phase 1C.

    Args:
        args: argparse.Namespace with all CLI flags.
        template: Optional loaded Template object.

    Returns:
        AssetJobConfig.
    """
    # Parse frames string to tuple
    frames = (1,)
    raw_frames = getattr(args, "frames", None)
    if raw_frames:
        frames = tuple(int(x.strip()) for x in raw_frames.split(","))
    elif template and hasattr(template, "frames") and template.frames:
        frames = tuple(
            template.frames
            if isinstance(template.frames, list)
            else [template.frames]
        )

    # Resolve angles
    angles = getattr(args, "angles", None)
    if angles is None and template and hasattr(template, "angles"):
        angles = template.angles
    if angles is None:
        angles = 1

    # Build slice_spec from CLI flags if provided
    slice_spec = _build_slice_spec_from_args(args)

    # Build background spec from CLI flags if provided
    background = _build_background_spec_from_args(args)

    # Resolve name
    name = getattr(args, "name", None)
    if name is None and template:
        name = getattr(template, "name", "unnamed")
    if name is None:
        name = "unnamed"

    # Resolve explicit_projs: --projs flag takes precedence, then _reformat_projs
    explicit_projs = getattr(args, "projs", None)
    if explicit_projs is None:
        explicit_projs = getattr(args, "_reformat_projs", None)

    job = resolve_config(
        name=name,
        angles=angles,
        frames=frames,
        source_type=getattr(args, "source_type", "file") or "file",
        source_path=getattr(args, "input", None),
        blender_object=getattr(args, "blender_object", None),
        asset_type=getattr(args, "type", "custom") or "custom",
        transparency=getattr(args, "transparency", False),
        normalization=getattr(args, "normalization", False),
        target_cells_high=getattr(args, "target_cells_high", 0),
        downscale_algorithm=getattr(args, "downscale", None),
        template_name=template.name if template else None,
        explicit_projs=explicit_projs,
        slice_spec=slice_spec,
        background=background,
    )

    # Wire source_projs from CLI (default 1 for file imports)
    source_projs = getattr(args, "source_projs", None)
    if source_projs is not None:
        job.source_projs = int(source_projs)

    # Wire reflection_policy from CLI (exists in Reformatter group)
    reflection_policy = getattr(args, "reflection_policy", None)
    if reflection_policy is not None:
        job.reflection_policy = reflection_policy

    # Reformatter compatibility: frames may already include projection columns.
    job.frames_include_projs = bool(getattr(args, "_frames_include_projs", False))

    # Wire synthesize_angles from CLI (Reflection Handling group)
    synthesize_angles = getattr(args, "synthesize_angles", None)
    if synthesize_angles is not None:
        job.synthesize_angles = synthesize_angles

    # Wire pre-slice hook flags from CLI (Phase 12)
    job.pre_slice_check = getattr(args, "pre_slice_check", False)
    job.pre_slice_check_strict = getattr(args, "pre_slice_check_strict", False)
    job.pixel_perfect_mode = getattr(args, "pixel_perfect_mode", "off") or "off"

    # Wire keyframe ranges from CLI (BLEND-15-03)
    # keyframe_ranges is already parsed into AnimationRange list on the asset
    keyframe_ranges = getattr(args, "_parsed_keyframe_ranges", None)
    if keyframe_ranges is not None:
        job.keyframe_ranges = keyframe_ranges

    # Shared 4-track PipelineConfig snapshot (Phase 19 schema wiring)
    pipeline_config = getattr(args, "_pipeline_config_dict", None)
    if pipeline_config is not None:
        job.pipeline_config = pipeline_config

    return job


def create_job_from_wizard(wizard_result) -> AssetJobConfig:
    """Build AssetJobConfig from a WizardResult.

    Args:
        wizard_result: WizardResult from run_wizard_mode() or WizardEngine.

    Returns:
        AssetJobConfig.
    """
    template = getattr(wizard_result, "template", None)

    frames = (1,)
    if template and hasattr(template, "frames") and template.frames:
        frames = tuple(
            template.frames
            if isinstance(template.frames, list)
            else [template.frames]
        )

    angles = 1
    if template and hasattr(template, "angles"):
        angles = template.angles

    source_type = getattr(wizard_result, "source_type", "file") or "file"
    input_path = getattr(wizard_result, "input_path", None)
    blend_file_path = getattr(wizard_result, "blend_file_path", None)

    source_path = str(input_path) if input_path else None
    if source_type == "blender" and blend_file_path:
        source_path = str(blend_file_path)

    name = None
    if template:
        name = getattr(template, "name", None)
    if name is None:
        name = "wizard_asset"

    job = resolve_config(
        name=name,
        angles=angles,
        frames=frames,
        source_type=source_type,
        source_path=source_path,
        blender_object=getattr(wizard_result, "blender_object", None),
        asset_type=getattr(wizard_result, "asset_type", "custom") or "custom",
        transparency=(source_type == "ai"),
        template_name=template.name if template else None,
        source_projs=1,  # wizard imports default to single-projection
        reflection_policy="generate",  # fail-closed: generate, not detect
    )
    return job


def create_job_from_batch(
    entry: ManifestEntry,
    defaults: dict,
) -> AssetJobConfig:
    """Build AssetJobConfig from a ManifestEntry.

    Delegates to manifest.entry_to_job_config() for implementation.

    Args:
        entry: Single manifest entry.
        defaults: Shared defaults from the manifest.

    Returns:
        AssetJobConfig.
    """
    from .manifest import entry_to_job_config

    return entry_to_job_config(entry, defaults)


def create_job_from_mcp(params: dict) -> AssetJobConfig:
    """Build AssetJobConfig from MCP tool call parameters.

    Accepts slice_spec and background as nested dicts directly.
    This lets MCP callers pass explicit grid params without script editing.

    Args:
        params: Dict from MCP tool call with keys matching AssetJobConfig fields.

    Returns:
        AssetJobConfig.
    """
    slice_spec = _parse_slicing_dict(params.get("slice_spec"))
    background = _parse_background_dict(params.get("background"))

    frames = params.get("frames")
    if frames is None:
        raise ValueError(
            "MCP call missing 'frames' parameter. "
            "Specify animation frame counts explicitly (e.g. [1] for static)."
        )
    if isinstance(frames, list):
        frames = tuple(frames)

    job = resolve_config(
        name=params.get("name", "mcp_asset"),
        angles=params.get("angles", 1),
        frames=frames,
        source_type=params.get("source_type", "file"),
        source_path=params.get("source_path"),
        blender_object=params.get("blender_object"),
        asset_type=params.get("asset_type", "custom"),
        transparency=params.get("transparency", False),
        normalization=params.get("normalization", False),
        target_cells_high=params.get("target_cells_high", 0),
        downscale_algorithm=params.get("downscale_algorithm"),
        template_name=params.get("template_name"),
        explicit_projs=params.get("projs"),
        source_projs=params.get("source_projs", 1),
        slice_spec=slice_spec,
        background=background,
        reflection_policy=params.get("reflection_policy", "generate"),
    )
    return job


# ============================================================================
# Private helpers for CLI arg parsing
# ============================================================================


def _build_slice_spec_from_args(args) -> Optional[SlicingSpec]:
    """Build SlicingSpec from --slice-* CLI flags.

    Precedence:
    1. --slice-spec FILE overrides all individual flags.
    2. Individual --cell-w/--cell-h/--cols/--rows build explicit SlicingSpec.
    3. No flags = inference mode (returns None).

    When --slice-spec is provided alongside individual flags, the file
    wins and a warning is printed.
    """
    import sys

    slice_spec_file = getattr(args, "slice_spec", None)
    cell_w = getattr(args, "cell_w", None)
    cell_h = getattr(args, "cell_h", None)
    cols = getattr(args, "cols", None)
    rows = getattr(args, "rows", None)

    individual_flags = [cell_w, cell_h, cols, rows]
    has_individual = any(v is not None for v in individual_flags)

    # Detect --order override (non-default value should produce a spec
    # even without geometry flags)
    order = getattr(args, "order", "angle_major") or "angle_major"
    has_order_override = order != "angle_major"

    # Parse --angle-row-map CSV string to List[int]
    angle_row_map_raw = getattr(args, "angle_row_map", None)
    angle_row_map = None
    if angle_row_map_raw is not None:
        raw = angle_row_map_raw.strip()
        if not raw:
            raise ValueError("--angle-row-map cannot be empty")
        tokens = raw.split(",")
        parsed = []
        for i, tok in enumerate(tokens):
            tok = tok.strip()
            if not tok:
                raise ValueError(
                    f"--angle-row-map has empty token at position {i} "
                    f"(trailing comma?). Got: '{angle_row_map_raw}'"
                )
            try:
                parsed.append(int(tok))
            except ValueError:
                raise ValueError(
                    f"--angle-row-map token '{tok}' at position {i} "
                    f"is not an integer. Got: '{angle_row_map_raw}'"
                )
        angle_row_map = parsed
    has_row_map = angle_row_map is not None

    if slice_spec_file:
        import json
        from pathlib import Path

        if has_individual:
            print(
                "Warning: --slice-spec overrides individual --cell-w/--cell-h/"
                "--cols/--rows flags. Individual flags will be ignored.",
                file=sys.stderr,
            )
        with open(Path(slice_spec_file), "r") as f:
            data = json.load(f)
        return _parse_slicing_dict(data)

    if has_individual or has_order_override or has_row_map:
        margin_x = getattr(args, "margin_x", 0) or 0
        margin_y = getattr(args, "margin_y", 0) or 0
        spacing_x = getattr(args, "spacing_x", 0) or 0
        spacing_y = getattr(args, "spacing_y", 0) or 0

        # Reject negative values -- these indicate CLI misuse
        for label, val in [
            ("--cell-w", cell_w), ("--cell-h", cell_h),
            ("--cols", cols), ("--rows", rows),
            ("--margin-x", margin_x), ("--margin-y", margin_y),
            ("--spacing-x", spacing_x), ("--spacing-y", spacing_y),
        ]:
            if val is not None and val < 0:
                raise ValueError(
                    f"{label} must be non-negative, got {val}."
                )

        # Validate: margins/spacing without cell-w or cols is ambiguous
        has_layout = (margin_x > 0 or margin_y > 0 or spacing_x > 0 or spacing_y > 0)
        has_geometry = (cell_w is not None or cols is not None)
        if has_layout and not has_geometry:
            print(
                "Warning: --margin-x/--margin-y/--spacing-x/--spacing-y "
                "specified without --cell-w or --cols. Margins/spacing "
                "will be used but cell size will be inferred from "
                "content area.",
                file=sys.stderr,
            )

        if (has_order_override or has_row_map) and not has_individual:
            parts = []
            if has_order_override:
                parts.append(f"--order {order}")
            if has_row_map:
                parts.append("--angle-row-map")
            print(
                f"Info: {' '.join(parts)} set; geometry will be inferred.",
                file=sys.stderr,
            )

        return SlicingSpec(
            cell_w_px=cell_w,
            cell_h_px=cell_h,
            cols=cols,
            rows=rows,
            margin_x_px=margin_x,
            margin_y_px=margin_y,
            spacing_x_px=spacing_x,
            spacing_y_px=spacing_y,
            origin=getattr(args, "origin", "top_left") or "top_left",
            order=order,
            angle_row_map=angle_row_map,
        )

    return None


def _build_background_spec_from_args(args) -> Optional[BackgroundSpec]:
    """Build BackgroundSpec from --bg-* CLI flags."""
    bg_mode = getattr(args, "bg_mode", None)
    if bg_mode is None:
        return None

    bg_color_hex = getattr(args, "bg_color", None)
    key_color = (255, 0, 255)
    if bg_color_hex:
        # Parse "#FF00FF" or "FF00FF"
        hex_str = bg_color_hex.lstrip("#")
        key_color = (
            int(hex_str[0:2], 16),
            int(hex_str[2:4], 16),
            int(hex_str[4:6], 16),
        )

    return BackgroundSpec(
        mode=bg_mode,
        key_color=key_color,
        tolerance=getattr(args, "bg_tolerance", 8) or 8,
        alpha_threshold=getattr(args, "alpha_threshold", 128) or 128,
    )
