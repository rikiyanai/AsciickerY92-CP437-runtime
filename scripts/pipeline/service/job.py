"""Job configuration and output dataclasses.

AssetJobConfig is the frozen input contract for AssetService.run().
AssetJobOutput is the result produced after a successful pipeline run.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class AssetJobConfig:
    """Frozen job configuration for the asset pipeline.

    Constructed by adapters (CLI, wizard, batch, MCP) or TUIState.to_job_config().
    """

    name: str = "unnamed"
    asset_type: str = "custom"
    source_type: str = "file"
    source_path: Optional[str] = None
    blender_object: Optional[str] = None
    angles: int = 1
    frames: tuple = (1,)
    projs: int = 1
    transparency: bool = False
    normalization: bool = False
    target_cells_high: int = 0
    render_resolution: int = 24
    downscale_algorithm: Optional[str] = None
    template_name: Optional[str] = None
    slice_spec: Optional[object] = None
    background: Optional[object] = None
    slice_mode: str = "auto"
    explicit_projs: Optional[int] = None
    # Source image projections: 1=single (generate reflections), 2=pre-baked, None=legacy
    source_projs: Optional[int] = None
    # True when frames already include projection multiplication
    # (e.g. reformatter baked projs=2 and doubled frame counts).
    frames_include_projs: bool = False
    reflection_policy: Optional[str] = None
    synthesize_angles: Optional[int] = None
    pre_slice_check: bool = False
    pre_slice_check_strict: bool = False
    pixel_perfect_mode: str = "off"
    keyframe_ranges: Optional[list] = None  # List[AnimationRange] | None
    pipeline_config: Optional[dict] = None


@dataclass
class AssetJobOutput:
    """Result of a completed asset pipeline run."""

    xp_path: Path = field(default_factory=lambda: Path("."))
    checksum_sha256: str = ""
    metadata: dict = field(default_factory=dict)
    resolved_slice_spec: Optional[object] = None
    created_at: str = ""
    job_id: str = ""
    diagnostics: dict = field(default_factory=dict)
