#!/usr/bin/env python3
"""FL-4131 shape-lab fixture isolation + admitted-glyph contract."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "assets/a3d/fl4131_shape_lab_20x20.a3d"
SIDECAR = REPO_ROOT / "assets/a3d/fl4131_shape_lab_20x20.a3d.glyph_profile.json"
SANDBOX = REPO_ROOT / "assets/a3d/sandbox_20x20.a3d"
SHAPE_CATALOG = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"

EXPECTED_MESH_NAMES = {
    "fl4131_shape_lab_sphere.akm",
    "fl4131_shape_lab_triangle_pyramid.akm",
    "fl4131_shape_lab_skull_like.akm",
}

REQUIRED_ZONES = {
    "shoreline",
    "grass_flowers",
    "mountain_snow_strata",
    "sphere",
    "triangle_pyramid",
    "skull_like",
}

INSPECT_SECTION_TOKENS = ("Terrain", "Instances")


def _load_a3d_format():
    spec = importlib.util.spec_from_file_location(
        "a3d_format", REPO_ROOT / "addons/io_asciicker/scene/a3d_format.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load a3d_format module")
    module = importlib.util.module_from_spec(spec)
    sys.modules["a3d_format"] = module
    spec.loader.exec_module(module)
    return module


def _read_instances(path: Path) -> list:
    fmt = _load_a3d_format()
    with path.open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        for _ in range(header.num_patches):
            fmt.A3DPatch.from_file(fh)
        for _ in range(256):
            fmt.A3DMaterial.read(fh)
        fmt_ver = int.from_bytes(fh.read(4), "little", signed=True)
        instance_count = int.from_bytes(fh.read(4), "little", signed=True)
        return [fmt.A3DInstance.from_file(fh, fmt_ver) for _ in range(instance_count)]


def main() -> int:
    errors: list[str] = []

    if not FIXTURE.is_file():
        errors.append(f"missing fixture: {FIXTURE.relative_to(REPO_ROOT)}")
    if not SIDECAR.is_file():
        errors.append(f"missing sidecar: {SIDECAR.relative_to(REPO_ROOT)}")
    if FIXTURE.exists() and SANDBOX.exists() and FIXTURE.read_bytes() == SANDBOX.read_bytes():
        errors.append("shape-lab fixture must not be byte-identical to sandbox_20x20.a3d")

    if SIDECAR.exists():
        data = json.loads(SIDECAR.read_text(encoding="utf-8"))
        catalog = json.loads(SHAPE_CATALOG.read_text(encoding="utf-8"))
        admitted = {int(entry["glyph_id"]) for entry in catalog.get("entries", [])}
        zones = {str(z.get("zone_id")) for z in data.get("zones", [])}
        missing = sorted(REQUIRED_ZONES - zones)
        if missing:
            errors.append(f"sidecar missing zones: {missing}")
        glyph_ids = [
            int(g)
            for zone in data.get("zones", [])
            for g in zone.get("glyph_ids", [])
        ]
        if not glyph_ids or min(glyph_ids) < 512:
            errors.append("fixture sidecar must use admitted extended GlyphIds, not CP437-only approximations")
        missing_admitted = sorted(set(glyph_ids) - admitted)
        if missing_admitted:
            errors.append(f"fixture sidecar glyph_ids are not admitted: {missing_admitted}")

        mesh_assets = {
            str(zone.get("mesh_asset"))
            for zone in data.get("zones", [])
            if zone.get("mesh") in {"sphere", "pyramid", "skull"}
        }
        missing_mesh_assets = sorted(EXPECTED_MESH_NAMES - mesh_assets)
        if missing_mesh_assets:
            errors.append(f"sidecar missing mesh_asset links: {missing_mesh_assets}")
        missing_mesh_files = sorted(
            name for name in EXPECTED_MESH_NAMES if not (REPO_ROOT / "assets/meshes" / name).is_file()
        )
        if missing_mesh_files:
            errors.append(f"sidecar mesh_asset files are missing: {missing_mesh_files}")

    if FIXTURE.exists():
        out = subprocess.check_output(
            ["python3", "scripts/inspect_a3d.py", str(FIXTURE.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT,
            text=True,
        )
        for token in INSPECT_SECTION_TOKENS:
            if token not in out:
                errors.append(f"inspect_a3d output missing {token!r}")
        instances = _read_instances(FIXTURE)
        mesh_names = {inst.mesh_name for inst in instances if inst.variant == "mesh"}
        sprite_count = sum(1 for inst in instances if inst.variant == "sprite")
        missing_meshes = sorted(EXPECTED_MESH_NAMES - mesh_names)
        if missing_meshes:
            errors.append(f"fixture missing real mesh instances: {missing_meshes}")
        if sprite_count:
            errors.append(f"shape-lab mesh zones must not be sprite placeholders; found {sprite_count} sprite instances")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print("PASS: FL-4131 shape-lab fixture is isolated and extended-glyph backed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
