#!/usr/bin/env python3
"""Golden tests for the offline FL-4208 D2 C8 admission producer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/fl4208_orientation_coset.py"
FIXTURE_PATH = ROOT / "assets/glyphs/fixtures/fl4208_orientation_coset_golden_v1.json"
SPEC = importlib.util.spec_from_file_location("fl4208_orientation_coset", MODULE_PATH)
assert SPEC and SPEC.loader
D2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D2)


def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def blank() -> list[list[int]]:
    return [[0] * 48 for _ in range(48)]


def test_fixture_pins_chain_selection_margin_and_coset_identifier() -> None:
    data = fixture()
    assert data["action_version"] == D2.ACTION_VERSION
    assert data["raster_size"] == D2.RASTER_SIZE == 48
    assert data["thresholds"] == {"t_in": D2.T_IN, "t_out": D2.T_OUT}
    for case in data["subgroup_cases"]:
        name, members = D2.largest_contained_stabilizer(case["d_sym_per_k"])
        assert name == case["stabilizer_class"]
        assert D2.mask_for_orientations(members) == case["stabilizer_mask_u8"]
        assert D2.margin_state(case["d_sym_per_k"], members) == case["margin_state"]
    for case in data["coset_cases"]:
        assert list(D2.coset_members(case["phase"], case["stabilizer"])) == case["members"]
        assert D2.coset_id(case["phase"], case["stabilizer"]) == case["coset_id"]


def test_even_elements_are_exact_permutations_from_identity() -> None:
    identity = blank()
    identity[3][7] = 1
    identity[11][19] = 1
    assert D2.rotate_identity_pose(identity, 0) == identity
    assert D2.rotate_identity_pose(identity, 2)[7][44] == 1
    assert D2.rotate_identity_pose(identity, 4)[44][40] == 1
    assert D2.rotate_identity_pose(identity, 6)[40][3] == 1
    assert D2.d_sym_per_k(identity)[0] == 0.0


def test_odd_rotation_is_deterministic_single_resample_not_digitized_composition() -> None:
    identity = blank()
    for x in range(14, 34):
        identity[23][x] = 1
    r1 = D2.rotate_identity_pose(identity, 1)
    assert r1 == D2.rotate_identity_pose(identity, 1)
    # The producer samples R_2 from identity.  A chained digitized R_1 result
    # must not become a hidden alternative definition of the action.
    assert D2.rotate_identity_pose(identity, 2) != D2.rotate_identity_pose(r1, 1)


def test_margin_rejects_dead_zone_and_blank_glyph_fails_closed() -> None:
    data = fixture()
    dead = next(case for case in data["subgroup_cases"] if case["id"] == "dead_band_rejects_near_c4")
    _, members = D2.largest_contained_stabilizer(dead["d_sym_per_k"])
    assert D2.margin_state(dead["d_sym_per_k"], members) == "unstable_stabilizer"

    cache = {
        "schema": D2.TOPOLOGY_CACHE_SCHEMA,
        "entries": [{
            "glyph_id": 9001,
            "unicode_scalar": 0,
            "topology_id": "blank",
            "beta0": 0,
            "beta1_raw": 0,
            "junction_crossing_numbers": [],
            "raster_rows": ["0" * 48] * 48,
        }],
    }
    receipt = D2.build_receipt(cache, atlas_sha256="a" * 64, shape_catalog_sha256="b" * 64)
    record = receipt["records"][0]
    assert record["margin_state"] == "blank_glyph"
    assert record["rejection_cause"] == "unstable_stabilizer"
    assert record["admitted_orientation_mask_u8"] == 0


def test_receipt_schema_recomputes_exactly_and_refuses_noncoset_review_mask() -> None:
    identity = blank()
    for x in range(18, 30):
        identity[23][x] = 1
        identity[24][x] = 1
    cache = {
        "schema": D2.TOPOLOGY_CACHE_SCHEMA,
        "entries": [{
            "glyph_id": 9002,
            "unicode_scalar": 0x2212,
            "topology_id": "bar",
            "beta0": 1,
            "beta1_raw": 0,
            "junction_crossing_numbers": [],
            "raster_rows": D2.raster_rows(identity),
            "reviewed_orientation_mask_u8": 0x11,
        }],
    }
    receipt = D2.build_receipt(cache, atlas_sha256="a" * 64, shape_catalog_sha256="b" * 64)
    assert receipt["schema"] == D2.RECEIPT_SCHEMA
    assert D2.validate_receipt(receipt, cache) == {"records": 1, "stable": 1}
    required = {
        "glyph_id", "unicode_scalar", "topology_id", "beta0", "beta1_raw",
        "junction_crossing_numbers", "d_sym_per_k", "t_in", "t_out",
        "stabilizer_class", "margin_state", "action_version", "coset_id",
        "admitted_orientation_mask_u8", "pool_insertions", "atlas_sha256",
        "shape_catalog_sha256",
    }
    assert required <= receipt["records"][0].keys()

    cache["entries"][0]["reviewed_orientation_mask_u8"] = 0x01
    with pytest.raises(D2.CosetError, match="union of stabilizer cosets"):
        D2.build_receipt(cache, atlas_sha256="a" * 64, shape_catalog_sha256="b" * 64)


def test_compiler_cli_hash_binds_the_cache_atlas_and_shape_catalog(tmp_path: Path) -> None:
    identity = blank()
    for x in range(18, 30):
        identity[23][x] = 1
        identity[24][x] = 1
    cache = {
        "schema": D2.TOPOLOGY_CACHE_SCHEMA,
        "entries": [{
            "glyph_id": 9003,
            "unicode_scalar": 0x2212,
            "topology_id": "bar",
            "beta0": 1,
            "beta1_raw": 0,
            "junction_crossing_numbers": [],
            "raster_rows": D2.raster_rows(identity),
            "reviewed_orientation_mask_u8": 0x11,
        }],
    }
    cache_path = tmp_path / "topology-cache.json"
    atlas_path = tmp_path / "atlas.json"
    catalog_path = tmp_path / "shape-catalog.json"
    output_path = tmp_path / "topology-coset-receipt.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    atlas_path.write_text('{"atlas":"fixture"}\n', encoding="utf-8")
    catalog_path.write_text('{"catalog":"fixture"}\n', encoding="utf-8")
    command = [sys.executable, str(MODULE_PATH), "--topology-cache", str(cache_path),
               "--atlas", str(atlas_path), "--shape-catalog", str(catalog_path),
               "--out", str(output_path)]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    assert "wrote 1 D2 topology/coset records" in completed.stdout
    verified = subprocess.run(command + ["--verify"], cwd=ROOT, text=True,
                              capture_output=True, check=True)
    assert "receipt recomputed: 1 records, 1 admitted" in verified.stdout
    receipt = json.loads(output_path.read_text())
    assert receipt["topology_cache_sha256"] == D2.sha256_json(cache)
    assert receipt["records"][0]["atlas_sha256"] == D2.sha256_file(atlas_path)
    assert receipt["records"][0]["shape_catalog_sha256"] == D2.sha256_file(catalog_path)
