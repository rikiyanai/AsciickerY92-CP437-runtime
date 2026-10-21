#!/usr/bin/env python3
"""Shared contract for population assets authored before A3D assembly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SCHEMA = "y92.population_asset_plan.v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_asset_plan(path: Path, asset_root: Path) -> tuple[dict, dict, dict]:
    """Load and verify one immutable population asset manifest.

    Returns the document plus placement- and logical-instance indexes.  File
    existence, non-empty payload, duplicate identity, and byte hash are checked
    here so all lowerers enforce the same contract.
    """
    path = Path(path)
    asset_root = Path(asset_root)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA:
        raise ValueError(
            f"population asset plan requires {SCHEMA}, got "
            f"{document.get('schema')!r}")
    by_placement: dict[str, dict] = {}
    by_logical: dict[str, dict] = {}
    for row in document.get("rows", []):
        placement_id = str(row.get("placement_id") or "")
        if not placement_id:
            raise ValueError("population asset row has no placement_id")
        if row.get("logical_instance_id"):
            logical_id = str(row["logical_instance_id"])
            if logical_id in by_logical:
                raise ValueError(
                    f"population asset logical id appears twice: {logical_id}")
            by_logical[logical_id] = row
        else:
            if placement_id in by_placement:
                raise ValueError(
                    f"population asset placement appears twice: {placement_id}")
            by_placement[placement_id] = row
        mesh_name = str(row.get("mesh_name") or "")
        if not mesh_name or Path(mesh_name).name != mesh_name:
            raise ValueError(
                f"population asset row has unsafe mesh name: {mesh_name!r}")
        mesh_path = asset_root / mesh_name
        if not mesh_path.is_file() or mesh_path.stat().st_size <= 0:
            raise ValueError(f"population asset is absent or empty: {mesh_path}")
        if sha256(mesh_path) != row.get("mesh_sha256"):
            raise ValueError(f"population asset hash mismatch: {mesh_path}")
    return document, by_placement, by_logical

