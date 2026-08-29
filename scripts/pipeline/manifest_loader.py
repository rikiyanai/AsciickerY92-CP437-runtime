"""Gate 2 manifest loader and validation.

Loads the authoritative gate2_manifest.json and provides typed access
to per-sheet geometry declarations. Gate runners consume this instead
of ad-hoc CLI guesses.

[FLOW:MANIFEST] Production-path loader. Test fixtures remain in tests/fixtures/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


# Resolve project root (scripts/pipeline/ -> scripts/ -> project root).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

MANIFEST_PATH = _PROJECT_ROOT / "tests" / "fixtures" / "real_assets" / "gate2_manifest.json"

# Required fields for every manifest entry
_REQUIRED_FIELDS = {"filename", "width", "height", "angles", "frames", "source_projs", "reflection_policy"}


@dataclass
class Gate2Entry:
    """Single sprite sheet entry from the Gate 2 manifest."""

    key: str
    filename: str
    width: int
    height: int
    cell_px: Optional[int]
    cols: Optional[int]
    rows: Optional[int]
    angles: int
    frames: List[int]
    order: str
    source_projs: int
    reflection_policy: str
    background: str
    provenance: str
    notes: str

    @property
    def has_grid(self) -> bool:
        """True if cell_px, cols, and rows are all specified."""
        return self.cell_px is not None and self.cols is not None and self.rows is not None

    @property
    def total_frames(self) -> int:
        """Sum of all animation frame counts."""
        return sum(self.frames)

    @property
    def expected_cols(self) -> Optional[int]:
        """Expected number of columns based on frames and projs."""
        if not self.has_grid:
            return None
        return self.cols

    def validate_dimensions(self) -> List[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        if self.cell_px is not None:
            if self.cols is not None and self.width != self.cell_px * self.cols:
                errors.append(
                    f"width={self.width} != cell_px={self.cell_px} * cols={self.cols} "
                    f"(expected {self.cell_px * self.cols})"
                )
            if self.rows is not None and self.height != self.cell_px * self.rows:
                errors.append(
                    f"height={self.height} != cell_px={self.cell_px} * rows={self.rows} "
                    f"(expected {self.cell_px * self.rows})"
                )
        if self.source_projs not in (1, 2):
            errors.append(f"source_projs must be 1 or 2, got {self.source_projs}")
        if self.reflection_policy not in ("generate", "none", "detect"):
            errors.append(f"reflection_policy must be generate/none/detect, got {self.reflection_policy}")
        if not self.frames:
            errors.append("frames list is empty")
        if self.angles < 1:
            errors.append(f"angles must be >= 1, got {self.angles}")
        return errors


def load_manifest(path: Optional[Path] = None) -> Dict[str, Gate2Entry]:
    """Load and validate the Gate 2 manifest.

    Returns a dict keyed by entry name.
    Raises ValueError on missing required fields or dimension mismatches.
    """
    manifest_path = path or MANIFEST_PATH
    if not manifest_path.exists():
        raise FileNotFoundError(f"Gate 2 manifest not found: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    entries_raw = raw.get("entries", {})
    if not entries_raw:
        raise ValueError("Gate 2 manifest has no entries")

    result: Dict[str, Gate2Entry] = {}
    all_errors: List[str] = []

    for key, data in entries_raw.items():
        # Check required fields
        missing = _REQUIRED_FIELDS - set(data.keys())
        if missing:
            all_errors.append(f"{key}: missing required fields: {missing}")
            continue

        entry = Gate2Entry(
            key=key,
            filename=data["filename"],
            width=data["width"],
            height=data["height"],
            cell_px=data.get("cell_px"),
            cols=data.get("cols"),
            rows=data.get("rows"),
            angles=data["angles"],
            frames=data["frames"],
            order=data.get("order", "angle_major"),
            source_projs=data["source_projs"],
            reflection_policy=data["reflection_policy"],
            background=data.get("background", "unknown"),
            provenance=data.get("provenance", "unknown"),
            notes=data.get("notes", ""),
        )

        dim_errors = entry.validate_dimensions()
        if dim_errors:
            all_errors.extend(f"{key}: {e}" for e in dim_errors)
            continue

        result[key] = entry

    if all_errors:
        raise ValueError(
            f"Gate 2 manifest validation failed:\n" + "\n".join(f"  - {e}" for e in all_errors)
        )

    return result


def get_entry(name: str, manifest: Optional[Dict[str, Gate2Entry]] = None) -> Gate2Entry:
    """Get a single entry by name, loading manifest if not provided."""
    if manifest is None:
        manifest = load_manifest()
    if name not in manifest:
        raise KeyError(f"No Gate 2 manifest entry for '{name}'. Available: {sorted(manifest.keys())}")
    return manifest[name]


def get_fixture_path(entry: Gate2Entry) -> Path:
    """Resolve the fixture file path for a manifest entry."""
    fixture_dir = MANIFEST_PATH.parent / "smalltestpngs"
    path = fixture_dir / entry.filename
    if not path.exists():
        # Also check wave2 directory
        wave2_path = MANIFEST_PATH.parent / "wave2" / entry.filename
        if wave2_path.exists():
            return wave2_path
    return path
