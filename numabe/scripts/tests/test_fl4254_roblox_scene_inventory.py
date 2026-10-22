#!/usr/bin/env python3
"""FL-4254 roblox_scene_inventory.py tests.

Uses tiny generated fixtures only. Does not require real Roblox geometry."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / "scripts/pipeline/roblox_scene_inventory.py"


def _run(*args: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(INVENTORY), "--json", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and not proc.stdout:
        return proc.returncode, {"errors": proc.stderr}
    return proc.returncode, json.loads(proc.stdout)


def _write_obj(path: Path, *, named: bool = True) -> None:
    lines = []
    if named:
        lines.append("o Building_ESS")
    lines += [
        "v 0.0 0.0 0.0",
        "v 1.0 0.0 0.0",
        "v 1.0 1.0 0.0",
        "v 0.0 1.0 0.0",
        "v 0.5 0.5 2.0",
        "usemtl brick",
        "f 1 2 3",
        "f 1 3 4",
        "f 1 2 5",
        "f 2 3 5",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _gltf_doc() -> dict:
    return {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "Library_Hall", "mesh": 0, "translation": [10.0, 0.0, 5.0]},
            {"name": "Terrain_Ground", "mesh": 1},
        ],
        "meshes": [
            {
                "name": "library_mesh",
                "primitives": [
                    {
                        "indices": 0,
                        "material": 0,
                        "attributes": {"POSITION": 1},
                    }
                ],
            },
            {
                "name": "terrain_mesh",
                "primitives": [
                    {
                        "indices": 2,
                        "material": 1,
                        "attributes": {"POSITION": 3},
                    }
                ],
            },
        ],
        "materials": [{"name": "brick"}, {"name": "grass"}],
        "accessors": [
            {"count": 6, "type": "SCALAR"},
            {"count": 4, "type": "VEC3", "min": [0.0, 0.0, 0.0], "max": [4.0, 4.0, 8.0]},
            {"count": 12, "type": "SCALAR"},
            {"count": 6, "type": "VEC3", "min": [-100.0, -100.0, 0.0], "max": [100.0, 100.0, 0.5]},
        ],
        "bufferViews": [],
        "buffers": [],
    }


def _write_gltf(path: Path) -> None:
    path.write_text(json.dumps(_gltf_doc()), encoding="utf-8")


def _write_glb(path: Path) -> None:
    json_bytes = json.dumps(_gltf_doc()).encode("utf-8")
    pad = (-len(json_bytes)) % 4
    json_bytes += b" " * pad
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes))
    chunk_header = struct.pack("<II", len(json_bytes), 0x4E4F534A)
    path.write_bytes(header + chunk_header + json_bytes)


def test_obj_named_object_extracts_bbox_and_triangles():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_obj(tmp / "ess.obj")
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        assert result["entry_count"] == 1
        e = result["entries"][0]
        assert e["name"] == "Building_ESS"
        assert e["parser"] == "obj_stdlib"
        assert e["role_guess"] == "building_meshes"
        assert e["triangle_count"] == 4
        assert e["material_names"] == ["brick"]
        assert e["bbox"][0] == [0.0, 0.0, 0.0]
        assert e["bbox"][1] == [1.0, 1.0, 2.0]
        assert e["height"] == 2.0
        assert e["extraction_status"] == "ok"


def test_obj_unnamed_falls_back_to_filename():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_obj(tmp / "thing.obj", named=False)
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        assert result["entry_count"] == 1
        e = result["entries"][0]
        assert e["name"] == "thing"


def test_gltf_text_extracts_nodes_with_meshes():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_gltf(tmp / "campus.gltf")
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        assert result["entry_count"] == 2
        names = {e["name"] for e in result["entries"]}
        assert names == {"Library_Hall", "Terrain_Ground"}
        library = next(e for e in result["entries"] if e["name"] == "Library_Hall")
        assert library["parser"] == "gltf_stdlib"
        assert library["role_guess"] == "building_meshes"
        assert library["triangle_count"] == 2
        assert library["material_names"] == ["brick"]
        assert library["bbox"] == [[0.0, 0.0, 0.0], [4.0, 4.0, 8.0]]
        assert library["height"] == 8.0
        terrain = next(e for e in result["entries"] if e["name"] == "Terrain_Ground")
        assert terrain["role_guess"] == "terrain"
        assert terrain["material_names"] == ["grass"]


def test_glb_binary_parses_json_chunk():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_glb(tmp / "scene.glb")
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        assert result["entry_count"] == 2
        names = {e["name"] for e in result["entries"]}
        assert names == {"Library_Hall", "Terrain_Ground"}


def test_fbx_is_listed_only():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "thing.fbx").write_bytes(b"FBX\x00")
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        assert result["entry_count"] == 1
        e = result["entries"][0]
        assert e["parser"] == "fbx_listed"
        assert e["extraction_status"] == "unsupported_without_blender"
        assert "fbx_requires_blender_or_dedicated_parser" in e["limitations"]


def test_blend_is_listed_only():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "scene.blend").write_bytes(b"BLENDER\x00")
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        assert result["entry_count"] == 1
        e = result["entries"][0]
        assert e["parser"] == "blend_listed"
        assert e["extraction_status"] == "unsupported_without_blender"


def test_empty_raw_dir_emits_empty_payload():
    with tempfile.TemporaryDirectory() as td:
        rc, result = _run("--raw-dir", td)
        assert rc == 0
        assert result["entry_count"] == 0
        assert result["entries"] == []
        assert result["schema_version"] == "1"
        assert result["fl_ref"] == "FL-4254"


def test_missing_raw_dir_fails():
    rc, result = _run("--raw-dir", "/tmp/__definitely_not_a_real_dir_fl4254__")
    assert rc == 2


def test_explicit_file_flag_overrides_raw_dir_scan():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_obj(tmp / "alpha.obj")
        _write_obj(tmp / "beta.obj")
        rc, result = _run("--file", str(tmp / "alpha.obj"))
        assert rc == 0
        assert result["entry_count"] == 1


def test_unknown_extension_is_marked():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "thing.xyz").write_text("garbage")
        rc, result = _run("--file", str(tmp / "thing.xyz"))
        assert rc == 0
        assert result["entry_count"] == 1
        e = result["entries"][0]
        assert e["extraction_status"] == "unsupported_extension"


def test_role_guess_classifies_landmark():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        path = tmp / "umbilic_torus.obj"
        path.write_text("o UmbilicTorus\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
        rc, result = _run("--file", str(path))
        assert rc == 0
        e = result["entries"][0]
        assert e["role_guess"] == "landmarks"


def test_default_output_writes_normalized_json(tmp_path, monkeypatch):
    """When --json not passed, writes to --output path."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_obj(raw / "x.obj")
    out = tmp_path / "norm" / "roblox_objects.json"
    proc = subprocess.run(
        [sys.executable, str(INVENTORY), "--raw-dir", str(raw), "--output", str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["fl_ref"] == "FL-4254"
    assert payload["entry_count"] == 1


def test_required_per_entry_fields_present():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_obj(tmp / "ess.obj")
        rc, result = _run("--raw-dir", str(tmp))
        assert rc == 0
        e = result["entries"][0]
        required = {
            "source_file", "parser", "name", "role_guess", "transform",
            "bbox", "centroid", "height", "triangle_count", "material_names",
            "extraction_status", "limitations",
        }
        assert required.issubset(e.keys()), f"missing: {required - e.keys()}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
