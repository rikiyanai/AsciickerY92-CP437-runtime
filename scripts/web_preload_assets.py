#!/usr/bin/env python3
"""Emit the asset list that the Emscripten web build should preload."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path


PATCH_SIZE_BYTES = 8 + (8 * 8 * 2) + (5 * 5 * 2) + 2
MATERIALS_SIZE_BYTES = 256 * 512

FIXED_PRELOAD_FILES = [
    Path("assets/a3d/game_map_y8.a3d"),
    Path("assets/a3d/sandbox_20x20.a3d"),
    Path("assets/palettes/palette.gz"),
    Path("assets/images/menu.png"),
]


class PreloadError(RuntimeError):
    pass


def _read_exact(f, size: int) -> bytes:
    data = f.read(size)
    if len(data) != size:
        raise PreloadError("truncated A3D while reading preload mesh list")
    return data


def _read_i32(f) -> int:
    return struct.unpack("<i", _read_exact(f, 4))[0]


def referenced_meshes(a3d_path: Path) -> list[str]:
    with a3d_path.open("rb") as f:
        if _read_exact(f, 4) != b"AS3D":
            raise PreloadError(f"invalid A3D signature: {a3d_path}")
        header_size = struct.unpack("<I", _read_exact(f, 4))[0]
        patch_count = struct.unpack("<I", _read_exact(f, 4))[0]
        _read_exact(f, 4)  # reserved
        f.seek(header_size + patch_count * PATCH_SIZE_BYTES + MATERIALS_SIZE_BYTES)

        first = _read_i32(f)
        if first < 0:
            format_version = -first
            instance_count = _read_i32(f)
        else:
            format_version = 0
            instance_count = first

        meshes: set[str] = set()
        for _ in range(instance_count):
            mesh_id_len = _read_i32(f)
            if mesh_id_len >= 0:
                mesh_id = _read_exact(f, mesh_id_len).decode("utf-8") if mesh_id_len else ""
                if mesh_id.endswith(".ply"):
                    mesh_id = mesh_id[:-4] + ".akm"
                inst_name_len = _read_i32(f)
                _read_exact(f, inst_name_len)
                _read_exact(f, 16 * 8)  # transform
                _read_exact(f, 4)  # flags
                if format_version > 0:
                    _read_exact(f, 4)  # story_id
                if mesh_id:
                    meshes.add(mesh_id)
            elif mesh_id_len == -1:
                inst_name_len = _read_i32(f)
                _read_exact(f, inst_name_len)
                _read_exact(f, 12 + 4 + 8 + 16 + 4)  # pos, yaw, anim/frame, reps, flags
                if format_version > 0:
                    _read_exact(f, 4)  # story_id
            elif mesh_id_len == -2:
                if format_version < 3:
                    raise PreloadError("A3D item record requires format_version >= 3")
                _read_exact(f, 16 + 12 + 4 + 4)  # bundle ids/count, pos, yaw, flags
                _read_exact(f, 4)  # story_id
            else:
                raise PreloadError(f"unknown A3D instance discriminant: {mesh_id_len}")

    return sorted(meshes)


def _bundle_asset_path(asset_path: str) -> Path:
    if asset_path.startswith("assets/"):
        return Path(asset_path)
    return Path("assets/sprites") / asset_path


def referenced_actor_visual_profile_sprite_assets(profile_path: Path) -> list[Path]:
    with profile_path.open("r", encoding="utf-8") as f:
        compiled = json.load(f)

    assets: set[Path] = set()
    for profile in compiled.get("profiles", []):
        for layer in profile.get("layers", []):
            path = layer.get("source_path")
            if isinstance(path, str) and path:
                assets.add(_bundle_asset_path(path))

    return sorted(assets)


def collect_assets(repo_root: Path) -> list[Path]:
    assets: list[Path] = []
    a3d_path = repo_root / "assets/a3d/game_map_y8.a3d"
    for asset in FIXED_PRELOAD_FILES:
        full = repo_root / asset
        if not full.is_file():
            raise PreloadError(f"missing required web preload asset: {asset}")
        assets.append(asset)

    for pattern in ("assets/sprites/*.xp", "assets/samples/*.ogg"):
        matches = sorted(path.relative_to(repo_root) for path in repo_root.glob(pattern) if path.is_file())
        if not matches:
            raise PreloadError(f"no web preload assets matched: {pattern}")
        assets.extend(matches)

    glyph_assets = sorted(
        path.relative_to(repo_root)
        for path in (repo_root / "assets/glyphs").rglob("*")
        if path.is_file()
    )
    if not glyph_assets:
        raise PreloadError("no web preload assets matched: assets/glyphs/**/*")
    assets.extend(glyph_assets)

    for mesh_name in referenced_meshes(a3d_path):
        mesh_path = Path("assets/meshes") / mesh_name
        if not (repo_root / mesh_path).is_file():
            raise PreloadError(f"missing A3D-referenced mesh: {mesh_path}")
        assets.append(mesh_path)

    # Runtime-spawned meshes (not referenced by the A3D, but loaded at runtime
    # by both server and client). FL-4137 placed-block AKM cube is the first
    # of these — engine/network_ingest_items.cpp and server/server_tick.cpp
    # call FindOrLoadMesh on this path on first placement event.
    # FL-4137 #69 (2026-05-31): legacy_yy_block_mesh.akm is the visible AKM
    # mesh for placed blocks. Catalog rows for legacy_yy_block (def 420) and
    # tall_yy_block (def 421) set world_mesh_path to this asset; the engine's
    # authoritative item appearance pass loads it via FindOrLoadMesh on the
    # first placed-block render so it MUST be preloaded into the web build.
    runtime_meshes = [
        "assets/meshes/PicoCube.akm",
        "assets/meshes/legacy_yy_block_mesh.akm",
    ]
    for mesh_rel in runtime_meshes:
        mesh_path = Path(mesh_rel)
        if not (repo_root / mesh_path).is_file():
            raise PreloadError(f"missing runtime-spawned mesh: {mesh_path}")
        assets.append(mesh_path)

    return sorted(dict.fromkeys(assets))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--nul", action="store_true")
    args = parser.parse_args(argv)
    try:
        assets = collect_assets(Path(args.repo_root).resolve())
    except PreloadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    sep = "\0" if args.nul else "\n"
    sys.stdout.write(sep.join(str(path) for path in assets))
    if assets:
        sys.stdout.write(sep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
