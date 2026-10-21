"""Canonical manifest schema for normalized sprite packages.

A canonical manifest records the detected geometry of a single asset
after normalization.  It is the source-of-truth for downstream slicing:
when a manifest exists, the pipeline uses explicit frame dimensions and
frame_map instead of heuristic grid inference.

Canonical package layout::

    staging/normalized/<asset_name>/
        manifest.json
        source.png              # BG-normalized source
        spritesheet_canonical.png
        frames/
            dir_0/anim_0/frame_000.png
            dir_0/anim_0/frame_001.png
            ...
        artifacts/
            normalize_preview.png
            normalize_diagnostics.json

[FLOW:MANIFEST] [DATA-CONTRACT:CANONICAL]
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_REQUIRED_FIELDS = {
    "name", "schema_version", "grid", "angles", "anim_frames",
    "source_projs", "frame_map", "normalization",
}


@dataclass
class GridInfo:
    """Grid dimensions from normalization."""
    rows: int
    cols: int
    frame_width: int
    frame_height: int

    def validate(self) -> List[str]:
        errors = []
        if self.rows < 1:
            errors.append(f"rows must be >= 1, got {self.rows}")
        if self.cols < 1:
            errors.append(f"cols must be >= 1, got {self.cols}")
        if self.frame_width < 1:
            errors.append(f"frame_width must be >= 1, got {self.frame_width}")
        if self.frame_height < 1:
            errors.append(f"frame_height must be >= 1, got {self.frame_height}")
        return errors


@dataclass
class FrameRef:
    """Single frame mapping: grid position -> semantic ID."""
    row: int
    col: int
    direction: str   # e.g. "dir_0"
    animation: str   # e.g. "anim_0"
    frame_idx: int   # 0-based index within animation
    file: str        # relative path under frames/


@dataclass
class NormalizationInfo:
    """Metadata about how normalization was performed."""
    method: str              # e.g. "extractor_cluster"
    confidence: float        # 0.0-1.0
    bg_model: str            # e.g. "alpha", "color_corner", "magenta"
    bg_color: Optional[List[int]] = None  # [R,G,B] if color-based
    sprite_count: int = 0
    cluster_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalManifest:
    """Canonical manifest for a normalized sprite package.

    This is the production contract that the pipeline uses for
    deterministic slicing when normalize_input_mode is enabled.
    """
    name: str
    schema_version: int
    grid: GridInfo
    angles: int
    anim_frames: List[int]
    source_projs: int
    frame_map: List[FrameRef]
    normalization: NormalizationInfo
    source_path: str = ""
    canonical_sheet_path: str = ""

    def validate(self) -> List[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []

        if not self.name:
            errors.append("name is required")
        if self.schema_version != SCHEMA_VERSION:
            errors.append(
                f"schema_version must be {SCHEMA_VERSION}, got {self.schema_version}"
            )

        errors.extend(self.grid.validate())

        if self.angles < 1:
            errors.append(f"angles must be >= 1, got {self.angles}")
        if not self.anim_frames:
            errors.append("anim_frames must not be empty")
        if self.source_projs not in (1, 2):
            errors.append(f"source_projs must be 1 or 2, got {self.source_projs}")

        if not self.frame_map:
            errors.append("frame_map must not be empty")

        # anim_frames can be:
        #   - Per-angle list (len == angles): anim_frames[i] = frames for angle i
        #   - Uniform spec (len < angles): applied to all angles
        if len(self.anim_frames) == self.angles:
            # Per-angle frame counts.
            expected_total = sum(self.anim_frames) * self.source_projs
        else:
            # Uniform: same animation structure for every angle.
            expected_total = self.angles * sum(self.anim_frames) * self.source_projs

        if len(self.frame_map) != expected_total:
            errors.append(
                f"frame_map has {len(self.frame_map)} entries but expected "
                f"{expected_total} (angles={self.angles}, anim_frames="
                f"{self.anim_frames}, projs={self.source_projs})"
            )

        if not (0.0 <= self.normalization.confidence <= 1.0):
            errors.append(
                f"normalization.confidence must be 0.0-1.0, "
                f"got {self.normalization.confidence}"
            )

        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        # Convert FrameRef list to simple dicts
        d["frame_map"] = [asdict(fr) for fr in self.frame_map]
        return d

    def save(self, path: Path) -> None:
        """Write manifest to JSON file."""
        errors = self.validate()
        if errors:
            raise ValueError(
                "Cannot save invalid manifest:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Saved canonical manifest: %s", path)

    @classmethod
    def load(cls, path: Path) -> "CanonicalManifest":
        """Load and validate a canonical manifest from JSON."""
        if not path.exists():
            raise FileNotFoundError(f"Canonical manifest not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        missing = _REQUIRED_FIELDS - set(raw.keys())
        if missing:
            raise ValueError(f"Manifest missing required fields: {missing}")

        grid_raw = raw["grid"]
        grid = GridInfo(
            rows=int(grid_raw["rows"]),
            cols=int(grid_raw["cols"]),
            frame_width=int(grid_raw["frame_width"]),
            frame_height=int(grid_raw["frame_height"]),
        )

        norm_raw = raw["normalization"]
        normalization = NormalizationInfo(
            method=str(norm_raw["method"]),
            confidence=float(norm_raw["confidence"]),
            bg_model=str(norm_raw["bg_model"]),
            bg_color=norm_raw.get("bg_color"),
            sprite_count=int(norm_raw.get("sprite_count", 0)),
            cluster_params=dict(norm_raw.get("cluster_params", {})),
        )

        frame_map = []
        for fr in raw["frame_map"]:
            frame_map.append(FrameRef(
                row=int(fr["row"]),
                col=int(fr["col"]),
                direction=str(fr["direction"]),
                animation=str(fr["animation"]),
                frame_idx=int(fr["frame_idx"]),
                file=str(fr["file"]),
            ))

        manifest = cls(
            name=str(raw["name"]),
            schema_version=int(raw["schema_version"]),
            grid=grid,
            angles=int(raw["angles"]),
            anim_frames=[int(x) for x in raw["anim_frames"]],
            source_projs=int(raw["source_projs"]),
            frame_map=frame_map,
            normalization=normalization,
            source_path=str(raw.get("source_path", "")),
            canonical_sheet_path=str(raw.get("canonical_sheet_path", "")),
        )

        errors = manifest.validate()
        if errors:
            raise ValueError(
                f"Manifest validation failed:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        return manifest
