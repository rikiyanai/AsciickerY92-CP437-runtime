from dataclasses import dataclass, field, asdict
from typing import Tuple, Optional

@dataclass
class RenderRequest:
    """
    Schema for a render request sent to the Blender MCP server.
    """
    asset_name: str
    resolution: Tuple[int, int]
    frames: int
    object_name: Optional[str] = None
    animation_name: str = "idle"
    background_color: str = "#FF00FF"
    transparent_bg: bool = True
    convert_to_magenta: bool = False
    output_dir: Optional[str] = None
    return_bytes: bool = False
    angles: int = 1
    order: str = "angle-major"  # angle-major or frame-major
    seed: int = 0

    def validate(self) -> list[str]:
        """Validate the render request fields."""
        errors = []
        if not self.asset_name:
            errors.append("asset_name is required")
        if self.resolution[0] <= 0 or self.resolution[1] <= 0:
            errors.append("resolution must be positive")
        if self.frames <= 0:
            errors.append("frames must be positive")
        if self.angles not in [1, 4, 8]:
            errors.append(f"angles must be 1, 4, or 8 (got {self.angles})")
        if self.order not in ["angle-major", "frame-major"]:
            errors.append(f"invalid order: {self.order}")
        return errors

    def to_dict(self) -> dict:
        """Convert to a dictionary suitable for JSON serialization."""
        d = asdict(self)
        # Ensure resolution is a list for JSON
        d["resolution"] = list(self.resolution)
        return d

@dataclass
class RenderResponse:
    """
    Schema for a render response received from the Blender MCP server.
    """
    filepath: str
    angles: int
    frames: int
    width: int = 0
    height: int = 0
    frame_order: str = "angle-major"
    success: bool = True
    base64_data: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'RenderResponse':
        """Create a RenderResponse from a dictionary."""
        return cls(
            filepath=data.get("filepath", ""),
            angles=data.get("angles", 0),
            frames=data.get("frames", 0),
            width=data.get("width", 0),
            height=data.get("height", 0),
            frame_order=data.get("frame_order", "angle-major"),
            success=data.get("success", True),
            base64_data=data.get("base64_data")
        )

    def to_dict(self) -> dict:
        """Convert to a dictionary."""
        return asdict(self)
