#!/usr/bin/env python3
"""Seal canonical-scene coverage against the FL-4512 mesh metric registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

from a3d_mesh_refs import A3DMeshReferenceError, referenced_meshes


SCHEMA = "fl4512.mesh_asset_metric_coverage.v1"
REGISTRY_SCHEMA = "fl4512.mesh_asset_metric.v1"
AXIS_CONVENTION = "akm.right_handed.xyz.v1"
REGISTRY_FIELDS = {
    "geometry_path",
    "geometry_sha256",
    "anchor_q24_8",
    "axis_convention",
    "asset_metric_q24_8",
    "asset_id",
}


class CoverageError(RuntimeError):
    """Coverage inputs are malformed and cannot produce a receipt."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_file(repo_root: Path, path_text: Any) -> Path:
    if not isinstance(path_text, str) or not path_text or Path(path_text).is_absolute():
        raise CoverageError(f"invalid repo-relative path: {path_text!r}")
    root = repo_root.resolve()
    path = (root / path_text).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CoverageError(f"path escapes repository: {path_text}") from exc
    if not path.is_file():
        raise CoverageError(f"missing file: {path_text}")
    return path


def _asset_id(
    geometry_raw: bytes,
    anchor_q24_8: list[int],
    axis_convention: str,
    asset_metric_q24_8: list[int],
) -> str:
    return sha256_bytes(
        geometry_raw
        + struct.pack("<iii", *anchor_q24_8)
        + axis_convention.encode("utf-8")
        + struct.pack("<III", *asset_metric_q24_8)
    )


def load_registry(registry_path: Path, repo_root: Path) -> tuple[list[dict[str, Any]], str]:
    registry = json.loads(registry_path.read_bytes())
    if not isinstance(registry, dict) or set(registry) != {
        "schema",
        "assets",
        "registry_sha256",
    }:
        raise CoverageError("mesh metric registry top-level schema invalid")
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise CoverageError("mesh metric registry schema id invalid")
    assets = registry.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CoverageError("mesh metric registry assets[] missing")
    if any(not isinstance(asset, dict) or set(asset) != REGISTRY_FIELDS for asset in assets):
        raise CoverageError("mesh metric registry asset fields invalid")
    ordered = sorted(assets, key=lambda asset: str(asset.get("asset_id", "")))
    registry_hash = sha256_bytes(canonical_bytes(ordered))
    if registry.get("registry_sha256") != registry_hash:
        raise CoverageError("mesh metric registry hash mismatch")

    for asset in ordered:
        geometry_path = _repo_file(repo_root, asset["geometry_path"])
        geometry_raw = geometry_path.read_bytes()
        anchor = asset["anchor_q24_8"]
        metric = asset["asset_metric_q24_8"]
        if (
            sha256_bytes(geometry_raw) != asset["geometry_sha256"]
            or not isinstance(anchor, list)
            or len(anchor) != 3
            or any(not isinstance(value, int) or isinstance(value, bool) for value in anchor)
            or asset["axis_convention"] != AXIS_CONVENTION
            or not isinstance(metric, list)
            or len(metric) != 3
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > 0xFFFFFFFF
                for value in metric
            )
            or _asset_id(geometry_raw, anchor, AXIS_CONVENTION, metric)
            != asset["asset_id"]
        ):
            raise CoverageError(f"mesh metric record invalid: {asset['geometry_path']}")
    return ordered, registry_hash


def coverage_for_mesh_names(
    *,
    scene_path: str,
    scene_raw: bytes,
    mesh_names: list[str],
    registry_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    assets, registry_hash = load_registry(registry_path, repo_root)
    by_geometry: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        by_geometry.setdefault(asset["geometry_sha256"], []).append(asset)

    resolved_hashes: list[str] = []
    joined_ids: list[str] = []
    missing: list[dict[str, str]] = []
    duplicate: list[dict[str, Any]] = []
    for mesh_name in sorted(set(mesh_names)):
        mesh_path_text = str(Path("assets/meshes") / mesh_name)
        mesh_path = _repo_file(repo_root, mesh_path_text)
        geometry_hash = sha256_bytes(mesh_path.read_bytes())
        resolved_hashes.append(geometry_hash)
        matches = by_geometry.get(geometry_hash, [])
        if not matches:
            missing.append(
                {
                    "mesh_name": mesh_name,
                    "geometry_path": mesh_path_text,
                    "geometry_sha256": geometry_hash,
                }
            )
        elif len(matches) > 1:
            duplicate.append(
                {
                    "mesh_name": mesh_name,
                    "geometry_sha256": geometry_hash,
                    "asset_id": sorted(asset["asset_id"] for asset in matches),
                }
            )
        else:
            joined_ids.append(matches[0]["asset_id"])

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "scene_path": scene_path,
        "scene_sha256": sha256_bytes(scene_raw),
        "resolved_nonterrain_geometry_sha256": sorted(resolved_hashes),
        "joined_asset_id": sorted(joined_ids),
        "missing": missing,
        "duplicate": duplicate,
        "registry_sha256": registry_hash,
        "verdict": "PASS" if not missing and not duplicate else "FAIL",
    }
    payload["manifest_sha256"] = sha256_bytes(canonical_bytes(payload))
    return payload


def build_coverage(scene_path: Path, registry_path: Path, repo_root: Path) -> dict[str, Any]:
    scene_raw = scene_path.read_bytes()
    try:
        mesh_names = referenced_meshes(scene_path)
    except A3DMeshReferenceError as exc:
        raise CoverageError(str(exc)) from exc
    try:
        scene_text = str(scene_path.resolve().relative_to(repo_root.resolve()))
    except ValueError as exc:
        raise CoverageError("scene path escapes repository") from exc
    return coverage_for_mesh_names(
        scene_path=scene_text,
        scene_raw=scene_raw,
        mesh_names=mesh_names,
        registry_path=registry_path,
        repo_root=repo_root,
    )


def _write_atomic(path: Path, encoded: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scene", default="assets/a3d/game_map_y8_original_game_map.a3d"
    )
    parser.add_argument(
        "--registry",
        default="assets/glyphs/profiles/fl4512.mesh_asset_metric.v1.json",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out", default="-")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    try:
        manifest = build_coverage(
            _repo_file(repo_root, args.scene),
            _repo_file(repo_root, args.registry),
            repo_root,
        )
    except (CoverageError, json.JSONDecodeError, OSError) as exc:
        print(f"FL4512_MESH_METRIC_COVERAGE_REJECT {exc}", file=sys.stderr)
        return 2
    encoded = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.out == "-":
        sys.stdout.write(encoded)
    else:
        _write_atomic(Path(args.out), encoded)
    print(
        "FL4512_MESH_METRIC_COVERAGE_"
        f"{manifest['verdict']} missing={len(manifest['missing'])} "
        f"duplicate={len(manifest['duplicate'])}",
        file=sys.stderr,
    )
    return 0 if manifest["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
