from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import compile_fl4676_glyph_facts as glyph_fact_compiler

SCHEMA_PATH = ROOT / "assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json"
ARTIFACT_PATH = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
ATLAS_PATH = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
SCALAR_PATH = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.scalar_registry.json"
RUNTIME_BINDINGS_PATH = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.runtime_bindings.json"
ADDITIVE_CATALOG_PATH = ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
MORPHOLOGY_CATALOG_PATH = ROOT / "assets/glyphs/generated/material.morphology.v2.shape_catalog.json"
UNICODE_CATALOG_PATH = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"
DESCRIPTOR_CENSUS_PATH = ROOT / "assets/glyphs/generated/fl4512.descriptor_census.v1.jsonl"
DESCRIPTOR_MANIFEST_PATH = ROOT / "assets/glyphs/generated/fl4512.descriptor_census.v1.manifest.json"
POOL_PATH = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
PROFILE_PATH = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
MESH_ADMISSION_PATH = ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"
TEMPORAL_PATH = ROOT / "assets/glyphs/profiles/temporal_hysteresis.v1.json"
NORMALIZED_SUM = 65535


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha_value(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_without(value: dict, field: str) -> str:
    payload = dict(value)
    payload.pop(field)
    return _sha_value(payload)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _schema() -> dict:
    return _load(SCHEMA_PATH)


@lru_cache(maxsize=1)
def _artifact() -> dict:
    return _load(ARTIFACT_PATH)


@lru_cache(maxsize=1)
def _records_by_gid() -> dict[int, dict]:
    return {int(row["glyph_record_id"]): row for row in _artifact()["glyph_records"]}


@lru_cache(maxsize=1)
def _records_by_id() -> dict[int, dict]:
    return {int(row["glyph_record_id"]): row for row in _artifact()["glyph_records"]}


@lru_cache(maxsize=1)
def _descriptor_rows() -> list[dict]:
    with DESCRIPTOR_CENSUS_PATH.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _q0_16(value: float) -> int:
    return int(
        (Decimal(str(value)) * NORMALIZED_SUM).to_integral_value(rounding=ROUND_HALF_UP)
    )


def _fragment_schema(definition: str) -> dict:
    root = _schema()
    return {
        "$schema": root["$schema"],
        "$ref": f"#/$defs/{definition}",
        "$defs": root["$defs"],
    }


def test_01_compiler_check_passes_reproducibly() -> None:
    result = subprocess.run(
        ["python3", "scripts/compile_fl4676_glyph_facts.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "glyph_records=111040 facts=99" in result.stdout
    assert "canonical_content_sha256=" in result.stdout
    assert "serialized_file_sha256=" in result.stdout


def test_02_artifact_validates_and_distinguishes_content_from_file_identity() -> None:
    document = _artifact()
    jsonschema.Draft202012Validator(_schema()).validate(document)
    assert document["canonical_content_sha256"] == _sha_without(
        document, "canonical_content_sha256"
    )
    file_sha256 = hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest()
    assert file_sha256 != document["canonical_content_sha256"]
    assert document["artifact_identity"]["scene_fact_schema_state"] == "blocked_by_fl4683"
    assert "scene_fact_schema_sha256" not in document["artifact_identity"]


def test_03_source_pins_match_every_consumed_file_and_atlas_page() -> None:
    document = _artifact()
    pins = {pin["name"]: pin for pin in document["source_pins"]}
    required = {
        "native_atlas_registry",
        "scalar_registry",
        "runtime_bindings",
        "material_additive_catalog",
        "material_morphology_catalog",
        "unicode_shape_catalog",
        "descriptor_census",
        "descriptor_census_manifest",
        "compiled_pool_v1",
        "material_profiles",
        "mesh_admission",
        "temporal_hysteresis",
        "gtr_v2",
    }
    assert required.issubset(pins)
    assert len([name for name in pins if name.startswith("atlas_page_")]) == 93
    for pin in pins.values():
        assert hashlib.sha256((ROOT / pin["path"]).read_bytes()).hexdigest() == pin["sha256"]


def test_04_glyph_records_exactly_cover_independent_atlas_admission() -> None:
    document = _artifact()
    atlas = _load(ATLAS_PATH)
    atlas_by_gid = {int(row["glyph_id"]): row for row in atlas["bindings"]}
    records = _records_by_gid()
    assert atlas["binding_count"] == 111040
    assert len(atlas_by_gid) == 111040
    assert document["glyph_record_count"] == len(document["glyph_records"]) == 111040
    assert set(records) == set(atlas_by_gid)
    assert len({row["glyph_record_id"] for row in document["glyph_records"]}) == 111040
    for glyph_id, atlas_row in atlas_by_gid.items():
        record = records[glyph_id]
        admission = record["admission"]
        for field in (
            "pack_index",
            "page_index",
            "layer_index",
            "source_column",
            "source_row",
        ):
            assert admission[field] == atlas_row[field]
        assert record["glyph_record_id"] == glyph_id


def test_05_text_identity_matches_scalar_runtime_and_material_sources() -> None:
    records = _records_by_gid()
    atlas = _load(ATLAS_PATH)
    pack_by_gid = {int(row["glyph_id"]): int(row["pack_index"]) for row in atlas["bindings"]}
    scalar_by_gid = {
        int(row["glyph_id"]): int(row["unicode_scalar"])
        for row in _load(SCALAR_PATH)["allocations"]
    }
    runtime_by_gid = {
        int(row["glyph_id"]): row for row in _load(RUNTIME_BINDINGS_PATH)["bindings"]
    }
    additive_by_gid = {
        int(row["glyph_id"]): int(row["unicode_scalar"])
        for row in _load(ADDITIVE_CATALOG_PATH)["entries"]
    }
    morphology_by_gid = {
        int(row["glyph_id"]): int(row["unicode_scalar"])
        for row in _load(MORPHOLOGY_CATALOG_PATH)["entries"]
    }
    unicode_by_gid = {
        int(row["glyph_id"]): int(row["unicode_scalar"])
        for row in _load(UNICODE_CATALOG_PATH)["entries"]
    }
    assert len(scalar_by_gid) == len(unicode_by_gid) == 109416
    for glyph_id, record in records.items():
        identity = record["text_identity"]
        pack_index = pack_by_gid[glyph_id]
        if pack_index == 0:
            assert identity == {"kind": "cp437_code_unit", "cp437_code_unit": glyph_id}
        else:
            expected = (
                additive_by_gid[glyph_id]
                if pack_index == 1
                else morphology_by_gid[glyph_id]
                if pack_index == 2
                else unicode_by_gid[glyph_id]
            )
            assert identity == {"kind": "unicode_scalar", "unicode_scalar": expected}
            assert expected > 0
        if glyph_id in runtime_by_gid:
            assert record["admission"]["runtime_binding_declared"] is True
            assert identity["unicode_scalar"] == int(runtime_by_gid[glyph_id]["unicode_scalar"])
        else:
            assert record["admission"]["runtime_binding_declared"] is False
    for glyph_id, scalar in scalar_by_gid.items():
        assert records[glyph_id]["text_identity"]["unicode_scalar"] == scalar


def test_06_descriptor_census_agrees_with_all_110604_joined_records() -> None:
    records = _records_by_gid()
    descriptor_rows = _descriptor_rows()
    manifest = _load(DESCRIPTOR_MANIFEST_PATH)
    assert hashlib.sha256(DESCRIPTOR_CENSUS_PATH.read_bytes()).hexdigest() == manifest["census_sha256"]
    assert len(descriptor_rows) == manifest["record_count"] == 110604
    for source in descriptor_rows:
        record = records[int(source["glyph_id"])]
        assert record["morphology"]["descriptor_source"] == 0
        assert record["morphology"]["shape6_q0_16"] == [
            _q0_16(float(value)) for value in source["shape6"]
        ]
        assert record["morphology"]["shape6_norm_q0_16"] == [
            _q0_16(float(value)) for value in source["shape6_norm"]
        ]
        mask = int(str(source["mask8_u64"]), 16)
        assert record["connectivity"]["mask8_u64_le_hex"] == mask.to_bytes(8, "little").hex()
        assert record["structure"]["endpoint_count"] == int(source["endpoint_count"])
        assert record["structure"]["junction_count"] == int(source["junction_cluster_count"])
    origins: dict[str, int] = {}
    for record in records.values():
        key = record["morphology"]["descriptor_source"]
        origins[key] = origins.get(key, 0) + 1
    assert origins == {
        0: 110604,
        1: 256,
        2: 180,
    }


def test_07_facts_reference_records_only_and_normalize_exactly() -> None:
    document = _artifact()
    records_by_id = _records_by_id()
    records_by_gid = _records_by_gid()
    gid_by_record_id = {record_id: record_id for record_id in records_by_id}
    pool_rows = {
        str(row["row_sha256"]): row
        for row in _load(POOL_PATH)["rows"]
        if int(row.get("candidate_count", 0)) > 0 and row.get("candidate_glyph_ids")
    }
    assert document["fact_count"] == len(document["facts"]) == len(pool_rows) == 99
    for fact in document["facts"]:
        assert "glyph_subset" not in fact
        assert "morphology" not in fact
        assert fact["subset_weight_sum_q0_16"] == NORMALIZED_SUM
        members = fact["glyph_members"]
        assert sum(row["membership_weight_q0_16"] for row in members) == NORMALIZED_SUM
        assert all(set(row) == {"glyph_record_id", "membership_weight_q0_16"} for row in members)
        assert all(row["glyph_record_id"] in records_by_id for row in members)
        source = pool_rows[fact["scene_membership_key"]["row_sha256"]]
        assert [gid_by_record_id[row["glyph_record_id"]] for row in members] == source["candidate_glyph_ids"]
        assert all(records_by_gid[gid_by_record_id[row["glyph_record_id"]]]["record_sha256"] for row in members)


def test_08_profiles_mesh_admission_and_temporal_tables_are_consumed_data() -> None:
    document = _artifact()
    records = _records_by_gid()
    profiles_source = {
        str(row["material_id"]): row for row in _load(PROFILE_PATH)["profiles"]
    }
    profiles = {row["profile_id"]: row for row in document["profiles"]}
    assert set(profiles) == set(profiles_source)
    for profile_id, source in profiles_source.items():
        assert profiles[profile_id]["source_profile_sha256"] == _sha_value(source)

    mesh_source = _load(MESH_ADMISSION_PATH)
    mesh_ids = [int(value) for value in mesh_source["receipt"]["selected_glyph_ids"]]
    mesh_fact = next(
        fact for fact in document["facts"] if fact["scene_membership_key"]["role"] == "mesh_feature"
    )
    assert [
        int(_records_by_id()[row["glyph_record_id"]]["glyph_record_id"])
        for row in mesh_fact["glyph_members"]
    ] == mesh_ids
    assert all(records[glyph_id]["mesh_admission_member"] for glyph_id in mesh_ids)
    assert sum(record["mesh_admission_member"] for record in records.values()) == 32

    temporal_source = _load(TEMPORAL_PATH)
    source_by_scorer = {str(row["scorer_id"]): row for row in temporal_source["rows"]}
    policies = {
        str(row["source_row"]["scorer_id"]): row for row in document["temporal_policies"]
    }
    assert set(policies) == set(source_by_scorer)
    assert "fl4512.shape6_f8_w4.v2" in policies
    assert "fl4260.shape6_weighted_l2.v1" not in policies
    assert "fl4260.shape6_weighted_l2.v1" not in {
        fact["scene_membership_key"]["scorer_id"] for fact in document["facts"]
    }
    for scorer_id, source in source_by_scorer.items():
        assert policies[scorer_id]["source_row"] == source
        assert policies[scorer_id]["policy_sha256"] == _sha_value(source)
    for fact in document["facts"]:
        scorer_id = fact["scene_membership_key"]["scorer_id"]
        if scorer_id in glyph_fact_compiler.SCORERS_WITHOUT_TEMPORAL_POLICY:
            assert fact["temporal_policy_state"] == "not_applicable_to_structural_field"
            assert fact["temporal_policy_id"] is None
        else:
            assert fact["temporal_policy_state"] == "compiled_margin"
            assert fact["temporal_policy_id"] == policies[scorer_id]["temporal_policy_id"]


def test_09_stroke_port_graphs_are_published_only_for_structural_members() -> None:
    document = _artifact()
    structural_record_ids = {
        int(member["glyph_record_id"])
        for fact in document["facts"]
        if fact["scene_membership_key"]["scorer_id"] == "u7.mask.v1"
        for member in fact["glyph_members"]
    }
    assert len(structural_record_ids) == 32
    records_with_graph = {
        int(record["glyph_record_id"])
        for record in document["glyph_records"]
        if record["connectivity"].get("stroke_port_graph") is not None
    }
    assert records_with_graph == structural_record_ids
    for record_id in structural_record_ids:
        graph = _records_by_id()[record_id]["connectivity"]["stroke_port_graph"]
        assert graph["graph_schema"] == "u7.stroke_port_graph.v1"
        assert graph["graph_valid"] is True


def test_10_runtime_stays_blocked_and_has_no_intrinsic_authority() -> None:
    rights = _artifact()["runtime_rights"]
    assert rights["runtime_consumption_allowed"] is False
    assert rights["blocked_by"] == "FL-4683"
    forbidden = set(rights["forbidden_authorities"])
    assert {
        "derive_glyph_metadata_at_runtime",
        "derive_relation_edges_at_runtime",
        "invent_membership_at_runtime",
        "substitute_missing_fact",
        "mutate_compiled_artifact",
        "treat_scene_fact_as_glyph_metadata",
    }.issubset(forbidden)


def test_11_schema_rejects_attempt4_embedded_intrinsic_metadata() -> None:
    member = copy.deepcopy(_artifact()["facts"][0]["glyph_members"][0])
    member["morphology"] = {"shape6_q0_16": [0, 0, 0, 0, 0, 0]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_fragment_schema("fact_member")).validate(member)

    fact = copy.deepcopy(_artifact()["facts"][0])
    fact["glyph_subset"] = [{"glyph_id": 0}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_fragment_schema("glyph_fact")).validate(fact)


def test_12_join_and_weight_helpers_fail_closed() -> None:
    from compile_fl4676_glyph_facts import (
        CompileError,
        normalized_member_weights,
        require_exact_glyph_ids,
    )

    with pytest.raises(CompileError, match="admitted_universe_mismatch"):
        require_exact_glyph_ids("test", {1, 2}, {1})
    with pytest.raises(CompileError, match="empty_subset"):
        normalized_member_weights(0)
    for count in range(1, 33):
        weights = normalized_member_weights(count)
        assert sum(weights) == NORMALIZED_SUM
        assert weights == sorted(weights, reverse=True)
        assert all(weight > 0 for weight in weights)
