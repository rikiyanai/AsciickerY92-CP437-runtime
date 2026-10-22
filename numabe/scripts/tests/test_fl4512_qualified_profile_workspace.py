from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
PRODUCER = REPO / "scripts/fl4548/fl4512_build_qualified_profile_workspace.py"
SOURCE_PROFILE = REPO / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
SOURCE_MAP = REPO / "assets/a3d/frog_pieta_4x4.a3d"
PROFILE_RELATIVE = Path("profiles/material_rendering_profiles.v1.json")
POOL_RELATIVE = Path("generated/fl4512.compiled_pool.v1.json")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run_producer(workspace: Path, fixture_map: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(PRODUCER),
            "--workspace", str(workspace),
            "--fixture-map-out", str(fixture_map),
        ],
        cwd=REPO, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def material_offsets(source: bytes, material_key: int) -> list[int]:
    assert source[:4] == b"AS3D"
    header_size = struct.unpack_from("<I", source, 4)[0]
    patch_count = struct.unpack_from("<I", source, 8)[0]
    offsets = []
    for patch_index in range(patch_count):
        patch_offset = header_size + patch_index * 188
        for cell_index in range(64):
            offset = patch_offset + 8 + cell_index * 2
            if struct.unpack_from("<H", source, offset)[0] & 0xFF == material_key:
                offsets.append(offset)
    return offsets


def test_producer_compiles_distinct_same_number_profile_keys(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    fixture_map = tmp_path / "qualified.a3d"
    result = run_producer(workspace, fixture_map)
    assert result.returncode == 0, result.stdout + result.stderr
    profiles = json.loads((workspace / PROFILE_RELATIVE).read_text())
    profile_keys = [profile["material_id"] for profile in profiles["profiles"]]
    assert profile_keys.count("terrain:200") == 1
    assert profile_keys.count("mesh:200") == 1
    pool = json.loads((workspace / POOL_RELATIVE).read_text())
    compiled_keys = [
        f"{material['profile_namespace']}:{material['material_key']}"
        for material in pool["materials"]
    ]
    assert compiled_keys.count("terrain:200") == 1
    assert compiled_keys.count("mesh:200") == 1
    terrain_rows = [
        row for row in pool["rows"]
        if row["profile_namespace"] == "terrain" and row["material_key"] == 200
    ]
    mesh_rows = [
        row for row in pool["rows"]
        if row["profile_namespace"] == "mesh" and row["material_key"] == 200
    ]
    assert terrain_rows
    assert mesh_rows
    terrain_addresses = {
        (row["profile_namespace"], row["material_key"], row["role"],
         row["route_key"], row["lane"])
        for row in terrain_rows
    }
    mesh_addresses = {
        (row["profile_namespace"], row["material_key"], row["role"],
         row["route_key"], row["lane"])
        for row in mesh_rows
    }
    assert terrain_addresses.isdisjoint(mesh_addresses)
    assert len(terrain_addresses) == len(terrain_rows)
    assert len(mesh_addresses) == len(mesh_rows)


def test_map_rewrite_touches_only_terrain_material_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    fixture_map = tmp_path / "qualified.a3d"
    assert run_producer(workspace, fixture_map).returncode == 0
    source = SOURCE_MAP.read_bytes()
    rewritten = fixture_map.read_bytes()
    expected_offsets = material_offsets(source, 1)
    observed_offsets = [
        index
        for index, (before, after) in enumerate(zip(source, rewritten, strict=True))
        if before != after
    ]
    assert expected_offsets
    assert observed_offsets == expected_offsets
    assert all(rewritten[offset] == 200 for offset in observed_offsets)
    manifest = json.loads((workspace / "workspace_manifest.json").read_text())
    assert manifest["fixture_map_rewritten_cell_count"] == len(expected_offsets)
    assert manifest["source_map_sha256"] == sha256_bytes(source)
    assert manifest["fixture_map_sha256"] == sha256_bytes(rewritten)


def test_workspace_is_semantically_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_map = tmp_path / "first.a3d"
    second_map = tmp_path / "second.a3d"
    assert run_producer(first, first_map).returncode == 0
    assert run_producer(second, second_map).returncode == 0
    assert (first / PROFILE_RELATIVE).read_bytes() == (
        second / PROFILE_RELATIVE
    ).read_bytes()
    assert (first / POOL_RELATIVE).read_bytes() == (
        second / POOL_RELATIVE
    ).read_bytes()
    assert first_map.read_bytes() == second_map.read_bytes()
    first_manifest = json.loads((first / "workspace_manifest.json").read_text())
    second_manifest = json.loads((second / "workspace_manifest.json").read_text())
    assert first_manifest["semantic_workspace_sha256"] == second_manifest[
        "semantic_workspace_sha256"
    ]
    assert first_manifest["root_path"] != second_manifest["root_path"]


def test_producer_preserves_production_inputs_and_rejects_reuse(tmp_path: Path) -> None:
    profile_before = SOURCE_PROFILE.read_bytes()
    map_before = SOURCE_MAP.read_bytes()
    workspace = tmp_path / "workspace"
    fixture_map = tmp_path / "qualified.a3d"
    assert run_producer(workspace, fixture_map).returncode == 0
    assert SOURCE_PROFILE.read_bytes() == profile_before
    assert SOURCE_MAP.read_bytes() == map_before
    second = run_producer(workspace, tmp_path / "second.a3d")
    assert second.returncode == 2
    assert "workspace_must_not_exist" in second.stderr
    third = run_producer(tmp_path / "third", fixture_map)
    assert third.returncode == 2
    assert "fixture_map_must_not_exist" in third.stderr
