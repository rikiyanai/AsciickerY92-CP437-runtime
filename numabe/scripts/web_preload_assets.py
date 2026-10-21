#!/usr/bin/env python3
"""Emit the asset list that the Emscripten web build should preload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from a3d_mesh_refs import A3DMeshReferenceError, referenced_meshes


PRELOAD_SCENE_FILES = [
    Path("assets/a3d/game_map_y8.a3d"),
    Path("assets/a3d/sandbox_20x20.a3d"),
    # FL-4693: native and Web execute the same compiler-address outcome corpus.
    # This existing map reaches live and compiler-declared-empty profile rows;
    # omitting it from the Web package made the requested-map receipt name a
    # file that the WebAssembly virtual filesystem could not actually open.
    Path("assets/a3d/fl4260_fixture_all_materials.a3d"),
    # FL-4687 / FL-4693: operator-selected cross-target game-runtime corpus.
    # The 1x1 fixture was rejected after visual inspection; the 4x4 scene is
    # the validated fitted-camera Pieta map.
    Path("assets/a3d/frog_pieta_4x4.a3d"),
]

GLYPH_PROGRAM_SIDECAR = Path(
    "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
)

FIXED_PRELOAD_FILES = [
    *PRELOAD_SCENE_FILES,
    Path("assets/palettes/palette.gz"),
    Path("assets/images/menu.png"),
]


class PreloadError(RuntimeError):
    pass


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


def collect_assets(repo_root: Path, negative_fixture: str | None = None) -> list[Path]:
    assets: list[Path] = []
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

    for scene_path in PRELOAD_SCENE_FILES:
        try:
            scene_meshes = referenced_meshes(repo_root / scene_path)
        except A3DMeshReferenceError as exc:
            raise PreloadError(str(exc)) from exc
        for mesh_name in scene_meshes:
            mesh_path = Path("assets/meshes") / mesh_name
            if not (repo_root / mesh_path).is_file():
                raise PreloadError(
                    f"missing A3D-referenced mesh for {scene_path}: {mesh_path}"
                )
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

    assets = sorted(dict.fromkeys(assets))
    if negative_fixture == "missing-glyph-program":
        if GLYPH_PROGRAM_SIDECAR not in assets:
            raise PreloadError(
                f"negative fixture target was not in the preload set: {GLYPH_PROGRAM_SIDECAR}"
            )
        assets.remove(GLYPH_PROGRAM_SIDECAR)
    return assets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--nul", action="store_true")
    parser.add_argument(
        "--negative-fixture",
        choices=("missing-glyph-program",),
        default=None,
    )
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        assets = collect_assets(
            Path(args.repo_root).resolve(), negative_fixture=args.negative_fixture
        )
    except PreloadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(
                {
                    "schema": "fl4693.web_preload.v1",
                    "negative_fixture": args.negative_fixture,
                    "omitted_asset": (
                        str(GLYPH_PROGRAM_SIDECAR)
                        if args.negative_fixture == "missing-glyph-program"
                        else None
                    ),
                    "asset_count": len(assets),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    sep = "\0" if args.nul else "\n"
    sys.stdout.write(sep.join(str(path) for path in assets))
    if assets:
        sys.stdout.write(sep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
