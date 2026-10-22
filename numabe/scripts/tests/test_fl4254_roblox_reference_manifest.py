#!/usr/bin/env python3
"""FL-4254 roblox_reference_manifest.py validator tests.

Uses small temp fixtures only. Does not require real Roblox geometry."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "scripts/pipeline/roblox_reference_manifest.py"


def _run(manifest_path: Path, *extra: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--manifest", str(manifest_path), "--json", *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def _base_manifest() -> dict:
    return {
        "schema_version": "2",
        "fl_ref": "FL-4254",
        "proof_state": "no_geometry_acquired",
        "source": {
            "platform": "roblox",
            "game_id": "102219369954436",
            "place_name": "test",
            "creator": "test",
            "url": "https://example.com",
        },
        "acquisition": {
            "method": None,
            "authorized": None,
            "research_only": None,
            "date_acquired": None,
            "acquired_by": None,
        },
        "files": [],
        "coordinate_assumptions": {
            "origin_lat": 40.9146,
            "origin_lon": -73.1236,
        },
        "normalization": {
            "roblox_units_per_meter": None,
            "a3d_units_per_meter": None,
            "transform_matrix_to_a3d": None,
            "anchors_used": [],
        },
        "known_anchors": [{"name": "ESS"}],
        "known_limitations": [],
    }


def _write(tmp: Path, manifest: dict) -> Path:
    path = tmp / "source_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_base_no_geometry_manifest_passes():
    with tempfile.TemporaryDirectory() as td:
        manifest_path = _write(Path(td), _base_manifest())
        rc, result = _run(manifest_path)
        assert rc == 0, result
        assert result["ok"] is True
        assert result["errors"] == []
        assert result["proof_state"] == "no_geometry_acquired"
        assert result["file_count"] == 0


def test_missing_top_level_field_fails():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        del m["normalization"]
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "top_level_field_missing" in codes


def test_schema_version_mismatch_fails():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["schema_version"] = "1"
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "schema_version_mismatch" in codes


def test_invalid_proof_state_fails():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["proof_state"] = "totally_done"
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "proof_state_invalid" in codes


def test_geometry_without_acquisition_method_fails():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["proof_state"] = "raw_only"
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "acquisition_method_invalid" in codes
        assert "acquisition_auth_state_unset" in codes


def test_unauthorized_must_be_research_only():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["proof_state"] = "raw_only"
        m["acquisition"]["method"] = "externally_provided_gltf"
        m["acquisition"]["authorized"] = False
        m["acquisition"]["research_only"] = False
        m["acquisition"]["date_acquired"] = "2026-06-08"
        m["acquisition"]["acquired_by"] = "test"
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "acquisition_unauthorized_not_research_only" in codes


def test_authorized_geometry_passes():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["proof_state"] = "raw_only"
        m["acquisition"]["method"] = "roblox_studio_gltf_authorized"
        m["acquisition"]["authorized"] = True
        m["acquisition"]["research_only"] = False
        m["acquisition"]["date_acquired"] = "2026-06-08"
        m["acquisition"]["acquired_by"] = "test"
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 0, result


def test_file_entry_bad_kind_fails():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["files"] = [
            {
                "path": "raw/x.gltf",
                "kind": "weird_kind",
                "stage": "raw",
                "role": "scene",
                "sha256": "0" * 64,
                "bytes": 0,
            }
        ]
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "file_kind_invalid" in codes


def test_strict_files_missing_on_disk_fails():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["files"] = [
            {
                "path": "raw/nope.gltf",
                "kind": "gltf",
                "stage": "raw",
                "role": "scene",
                "sha256": "0" * 64,
                "bytes": 0,
            }
        ]
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path, "--strict-files")
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "file_missing_on_disk" in codes


def test_strict_files_sha_and_bytes_match():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        raw_dir = tmp / "raw"
        raw_dir.mkdir()
        payload = b"hello roblox"
        (raw_dir / "scene.gltf").write_bytes(payload)
        m = _base_manifest()
        m["files"] = [
            {
                "path": "raw/scene.gltf",
                "kind": "gltf",
                "stage": "raw",
                "role": "scene",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        ]
        manifest_path = _write(tmp, m)
        rc, result = _run(manifest_path, "--strict-files")
        assert rc == 0, result


def test_strict_files_sha_mismatch_fails():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "raw").mkdir()
        (tmp / "raw" / "scene.gltf").write_bytes(b"actual")
        m = _base_manifest()
        m["files"] = [
            {
                "path": "raw/scene.gltf",
                "kind": "gltf",
                "stage": "raw",
                "role": "scene",
                "sha256": "0" * 64,
                "bytes": 6,
            }
        ]
        manifest_path = _write(tmp, m)
        rc, result = _run(manifest_path, "--strict-files")
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "file_sha256_mismatch" in codes


def test_normalized_state_requires_scale_fields():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["proof_state"] = "normalized"
        m["acquisition"]["method"] = "roblox_studio_gltf_authorized"
        m["acquisition"]["authorized"] = True
        m["acquisition"]["research_only"] = False
        m["acquisition"]["date_acquired"] = "2026-06-08"
        m["acquisition"]["acquired_by"] = "test"
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 1
        codes = {e["code"] for e in result["errors"]}
        assert "normalization_scale_missing" in codes
        assert "normalization_anchors_used_missing" in codes


def test_normalized_state_with_full_scale_passes():
    with tempfile.TemporaryDirectory() as td:
        m = _base_manifest()
        m["proof_state"] = "normalized"
        m["acquisition"]["method"] = "roblox_studio_gltf_authorized"
        m["acquisition"]["authorized"] = True
        m["acquisition"]["research_only"] = False
        m["acquisition"]["date_acquired"] = "2026-06-08"
        m["acquisition"]["acquired_by"] = "test"
        m["normalization"]["roblox_units_per_meter"] = 0.28
        m["normalization"]["a3d_units_per_meter"] = 9.0
        m["normalization"]["transform_matrix_to_a3d"] = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        m["normalization"]["anchors_used"] = ["ESS"]
        manifest_path = _write(Path(td), m)
        rc, result = _run(manifest_path)
        assert rc == 0, result


def test_real_repo_manifest_is_valid():
    """The checked-in source_manifest.json must validate."""
    real_path = REPO_ROOT / "assets/reference/roblox_sbu/source_manifest.json"
    rc, result = _run(real_path)
    assert rc == 0, result
    assert result["proof_state"] == "no_geometry_acquired"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
