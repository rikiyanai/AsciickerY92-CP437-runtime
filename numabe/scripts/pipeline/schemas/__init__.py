"""
Package shim for asset_gen schemas.

This package exposes:
  - Core AssetDef schema (from scripts/pipeline/schemas.py)
  - Render contract dataclasses (from render_contract.py)

WHY: A package named `schemas` is now used to hold render_contract, but the
pipeline still imports `scripts.pipeline.schemas` expecting AssetDef from the
legacy `schemas.py` module. We bridge that here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .render_contract import RenderRequest, RenderResponse

# Load legacy schemas.py by file path to avoid name collision with this package.
_base_path = Path(__file__).resolve().parent.parent / "schemas.py"
_spec = importlib.util.spec_from_file_location("asset_gen_schemas_base", _base_path)
_base = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_base)

# Re-export legacy schema symbols
AnimationRange = _base.AnimationRange
AssetDef = _base.AssetDef
AssetType = _base.AssetType
SourceType = _base.SourceType
MESH_EXTENSIONS = _base.MESH_EXTENSIONS

__all__ = [
    "AnimationRange",
    "AssetDef",
    "AssetType",
    "SourceType",
    "MESH_EXTENSIONS",
    "RenderRequest",
    "RenderResponse",
]
