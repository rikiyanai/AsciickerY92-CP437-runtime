#!/usr/bin/env python3
"""Contracts for the FL-4512 canonical-scene mesh metric coverage gate."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location(
    "fl4512_mesh_asset_metric_coverage",
    SCRIPTS / "fl4512_mesh_asset_metric_coverage.py",
)
assert SPEC and SPEC.loader
COVERAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COVERAGE)

PIETA_SCENE = ROOT / "assets/a3d/frog_pieta_4x4.a3d"
Y8_SCENE = ROOT / "assets/a3d/game_map_y8_original_game_map.a3d"
REGISTRY = ROOT / "assets/glyphs/profiles/fl4512.mesh_asset_metric.v1.json"
Y8_MESHES = [
    "brick-1.akm",
    "bridge-1.akm",
    "bridge-2.akm",
    "bush-1.akm",
    "fence.akm",
    "fl4131_shape_lab_skull_like.akm",
    "fl4131_shape_lab_sphere.akm",
    "fl4131_shape_lab_triangle_pyramid.akm",
    "house-3.akm",
    "laundry-1.akm",
    "old-tree-1.akm",
    "old-tree-2.akm",
    "rock-1.akm",
    "ship-1.akm",
    "skull.akm",
    "tree-1.akm",
    "tree-2.akm",
    "tree-3.akm",
]


def canonical_sha(value: object) -> str:
    return hashlib.sha256(COVERAGE.canonical_bytes(value)).hexdigest()


def registry_asset_by_basename(registry: dict, basename: str) -> dict:
    return next(
        asset for asset in registry["assets"]
        if Path(asset["geometry_path"]).name == basename
    )


def test_y8_mesh_reference_set_is_exact_and_duplicate_free():
    assert COVERAGE.referenced_meshes(Y8_SCENE) == Y8_MESHES


def test_pieta_map_registry_covers_the_acceptance_mesh():
    manifest = COVERAGE.build_coverage(PIETA_SCENE, REGISTRY, ROOT)
    assert manifest["schema"] == "fl4512.mesh_asset_metric_coverage.v1"
    assert manifest["scene_path"] == "assets/a3d/frog_pieta_4x4.a3d"
    assert manifest["verdict"] == "PASS"
    assert manifest["missing"] == []
    assert manifest["duplicate"] == []
    assert manifest["joined_asset_id"] == [
        "d5077621c2d4e9ed6c1a8c629e760f56ba9d0cf3087b6a9dc7f02259ec8c909e"
    ]


def test_current_y8_registry_covers_all_18_meshes():
    manifest = COVERAGE.build_coverage(Y8_SCENE, REGISTRY, ROOT)
    assert manifest["schema"] == "fl4512.mesh_asset_metric_coverage.v1"
    assert manifest["verdict"] == "PASS"
    assert len(manifest["resolved_nonterrain_geometry_sha256"]) == 18
    assert manifest["missing"] == []
    assert manifest["duplicate"] == []
    assert len(manifest["joined_asset_id"]) == 18
    payload = dict(manifest)
    digest = payload.pop("manifest_sha256")
    assert digest == canonical_sha(payload)


def test_current_live_game_map_registry_covers_all_reachable_meshes():
    live_scene = ROOT / "assets/a3d/game_map_y8.a3d"
    manifest = COVERAGE.build_coverage(live_scene, REGISTRY, ROOT)
    assert manifest["schema"] == "fl4512.mesh_asset_metric_coverage.v1"
    assert manifest["verdict"] == "PASS"
    assert manifest["missing"] == []
    assert manifest["duplicate"] == []
    assert len(manifest["joined_asset_id"]) == 8


def test_registered_pieta_geometry_joins_one_asset_identity():
    scene_raw = b"fixture:pieta_shaded"
    manifest = COVERAGE.coverage_for_mesh_names(
        scene_path="fixture:pieta_shaded",
        scene_raw=scene_raw,
        mesh_names=["pieta_shaded.akm"],
        registry_path=REGISTRY,
        repo_root=ROOT,
    )
    assert manifest["verdict"] == "PASS"
    assert manifest["missing"] == []
    assert manifest["duplicate"] == []
    assert manifest["joined_asset_id"] == [
        "268dfb206f1bddf5063d9fd6dbe09ffc232eca6d17aecdc361385fae82c8d7c6"
    ]


def test_duplicate_metric_owner_is_reported_not_silently_selected(tmp_path: Path):
    registry = json.loads(REGISTRY.read_text())
    registry["assets"].append(
        copy.deepcopy(registry_asset_by_basename(registry, "pieta_shaded.akm"))
    )
    registry["registry_sha256"] = canonical_sha(
        sorted(registry["assets"], key=lambda item: item["asset_id"])
    )
    duplicate_registry = tmp_path / "registry.json"
    duplicate_registry.write_text(json.dumps(registry))
    manifest = COVERAGE.coverage_for_mesh_names(
        scene_path="fixture:pieta_shaded",
        scene_raw=b"fixture:pieta_shaded",
        mesh_names=["pieta_shaded.akm"],
        registry_path=duplicate_registry,
        repo_root=ROOT,
    )
    assert manifest["verdict"] == "FAIL"
    assert manifest["missing"] == []
    assert len(manifest["duplicate"]) == 1
    assert len(manifest["duplicate"][0]["asset_id"]) == 2


def test_registry_hash_mismatch_rejects_before_scene_join(tmp_path: Path):
    registry = json.loads(REGISTRY.read_text())
    registry["registry_sha256"] = "0" * 64
    broken = tmp_path / "registry.json"
    broken.write_text(json.dumps(registry))
    with pytest.raises(COVERAGE.CoverageError, match="registry hash mismatch"):
        COVERAGE.coverage_for_mesh_names(
            scene_path="fixture:pieta_shaded",
            scene_raw=b"fixture:pieta_shaded",
            mesh_names=["pieta_shaded.akm"],
            registry_path=broken,
            repo_root=ROOT,
        )
