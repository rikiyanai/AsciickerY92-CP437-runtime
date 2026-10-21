#!/usr/bin/env python3
"""Exercise the paper-defined Active Stroke v2 compiler authority.

The source Material Profile owns scalar coefficients and capacities.  The
compiler validates those authored bytes, expands the fixed Gaussian lookup
table with decimal arithmetic, publishes the topology-operation order, and
hashes the complete compiled block.  These tests mutate one fact at a time so
an invalid field cannot be hidden by an unrelated failure.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import jsonschema


ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4512_pools.py"
PROFILE_PATH = (
    ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
)
ARTIFACT_PATH = (
    ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
)
SCHEMA_PATH = (
    ROOT / "assets/glyphs/schema/material_rendering_profiles.v1.schema.json"
)

SPEC = importlib.util.spec_from_file_location(
    "compile_fl4512_pools_active_stroke_v2", COMPILER_PATH
)
assert SPEC and SPEC.loader
COMPILER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPILER)

INITIAL_VALUES = {
    "alpha_q0_16": 66,
    "beta_q0_16": 66,
    "attraction_lambda_q0_16": 16384,
    "temporal_lambda_q0_16": 8192,
    "step_q0_16": 16384,
    "gaussian_sigma_q16_16": 65536,
    "gaussian_distance2_step_q16_16": 4096,
    "gaussian_table_count": 145,
    "relaxation_iterations": 8,
    "resample_min_q16_16": 32768,
    "resample_max_q16_16": 49152,
    "coverage_radius_q16_16": 81920,
    "trim_radius_q16_16": 40960,
    "extend_radius_q16_16": 163840,
    "merge_radius_q16_16": 163840,
    "coverage_tangent_dot_min_q1_15": 27853,
    "merge_tangent_dot_min_q1_15": 27853,
    "split_tangent_dot_max_q1_15": 16384,
    "topology_balance_q0_16": 52429,
    "spring_stiffness_q1_15": 32768,
    "extension_step_q16_16": 49152,
    "extension_max_steps": 4,
    "trim_support_min": 1,
    "max_topology_passes": 8,
    "max_tracks": 4096,
    "max_controls_per_track": 4096,
    "max_controls_per_owner": 65536,
    "max_samples_per_owner": 65536,
    "quantization_version": 2,
}


def canonical_sha(value: object) -> str:
    """Return the compiler's compact, sorted JSON SHA-256 identity."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def authored_block() -> dict:
    """Load a detached copy of the checked-in mesh:200 source block."""

    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile = next(
        row for row in document["profiles"]
        if row["material_id"] == "mesh:200"
    )
    return copy.deepcopy(profile["active_stroke_topology"])


def refresh_source_hash(block: dict) -> None:
    """Rehash one validly shaped source mutation before bounds validation."""

    unhashed = {
        key: value for key, value in block.items()
        if key != "content_sha256"
    }
    block["content_sha256"] = canonical_sha(unhashed)


def compile_block(block: dict) -> dict:
    """Compile one detached source block through the production function."""

    profile = {
        "material_id": "mesh:200",
        "active_stroke_topology": block,
    }
    result = COMPILER.compile_active_stroke_topology(profile)
    assert result is not None
    return result


def test_checked_in_source_and_compiled_block_match_the_exact_initial_values():
    """The first published profile must use the paper's numeric checkpoint."""

    source = authored_block()
    assert source["schema"] == "fl4512.active_stroke_topology.v2"
    assert {
        key: source[key] for key in INITIAL_VALUES
    } == INITIAL_VALUES
    assert source["content_sha256"] == canonical_sha(
        {
            key: value for key, value in source.items()
            if key != "content_sha256"
        }
    )
    compiled = compile_block(source)
    assert {
        key: compiled[key] for key in INITIAL_VALUES
    } == INITIAL_VALUES


def test_checked_in_profile_satisfies_the_material_profile_schema():
    """The authored v2 block must remain valid in its complete source document."""

    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(document)


def test_decimal_gaussian_table_and_complete_compiled_hash_are_deterministic():
    """The table must match the 145-entry round-half-up paper formula."""

    compiled = compile_block(authored_block())
    table = compiled["gaussian_table_q0_16"]
    assert len(table) == 145
    assert table[:5] == [65535, 63519, 61564, 59670, 57834]
    assert table[-5:] == [825, 800, 775, 751, 728]
    assert all(left > right for left, right in zip(table, table[1:]))
    assert compiled["gaussian_table_sha256"] == canonical_sha(table)
    assert compiled["topology_operation_order"] == [
        "delete",
        "split",
        "trim",
        "extend",
        "merge",
        "insert",
    ]
    digest = compiled["content_sha256"]
    assert digest == canonical_sha(
        {
            key: value for key, value in compiled.items()
            if key != "content_sha256"
        }
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("alpha_q0_16", 65536),
        ("beta_q0_16", -1),
        ("attraction_lambda_q0_16", 65536),
        ("temporal_lambda_q0_16", -1),
        ("step_q0_16", 0),
        ("gaussian_sigma_q16_16", 65535),
        ("gaussian_distance2_step_q16_16", 4095),
        ("gaussian_table_count", 144),
        ("relaxation_iterations", 0),
        ("coverage_tangent_dot_min_q1_15", 32769),
        ("merge_tangent_dot_min_q1_15", 32769),
        ("split_tangent_dot_max_q1_15", 32769),
        ("topology_balance_q0_16", 65536),
        ("spring_stiffness_q1_15", 32769),
        ("extension_step_q16_16", 0),
        ("extension_max_steps", 0),
        ("trim_support_min", 0),
        ("max_topology_passes", 0),
        ("max_tracks", 4097),
        ("max_controls_per_track", 4097),
        ("max_controls_per_owner", 65537),
        ("max_samples_per_owner", 65537),
        ("quantization_version", 1),
    ],
)
def test_each_numeric_field_family_rejects_one_invalid_fact(
    field: str,
    bad_value: int,
):
    """Every fixed-point and capacity family has an independent bounds gate."""

    block = authored_block()
    block[field] = bad_value
    refresh_source_hash(block)
    with pytest.raises(
        COMPILER.CompileFailure,
        match="active_stroke_topology_invalid",
    ):
        compile_block(block)


@pytest.mark.parametrize(
    ("field", "other_field"),
    [
        ("resample_min_q16_16", "resample_max_q16_16"),
        ("trim_radius_q16_16", "coverage_radius_q16_16"),
        ("coverage_radius_q16_16", "extend_radius_q16_16"),
        ("trim_radius_q16_16", "merge_radius_q16_16"),
    ],
)
def test_each_cross_field_ordering_rejects_equality(
    field: str,
    other_field: str,
):
    """Minimum, radius, and capacity relationships fail closed at equality."""

    block = authored_block()
    block[field] = block[other_field]
    refresh_source_hash(block)
    with pytest.raises(
        COMPILER.CompileFailure,
        match="active_stroke_topology_invalid",
    ):
        compile_block(block)


def test_owner_capacity_cannot_be_smaller_than_one_track_capacity():
    """A track cannot declare more controls than its detached owner can hold."""

    block = authored_block()
    block["max_controls_per_owner"] = (
        block["max_controls_per_track"] - 1
    )
    refresh_source_hash(block)
    with pytest.raises(
        COMPILER.CompileFailure,
        match="active_stroke_topology_invalid",
    ):
        compile_block(block)


def test_non_integer_extra_missing_stale_hash_and_v1_resurrection_reject():
    """Shape, integer domain, source hash, and schema version are all exact."""

    mutations: list[dict] = []

    boolean_value = authored_block()
    boolean_value["alpha_q0_16"] = True
    refresh_source_hash(boolean_value)
    mutations.append(boolean_value)

    extra_key = authored_block()
    extra_key["gaussian_table_q0_16"] = []
    refresh_source_hash(extra_key)
    mutations.append(extra_key)

    missing_key = authored_block()
    del missing_key["beta_q0_16"]
    refresh_source_hash(missing_key)
    mutations.append(missing_key)

    stale_hash = authored_block()
    stale_hash["alpha_q0_16"] += 1
    mutations.append(stale_hash)

    v1 = {
        "schema": "fl4512.active_stroke_topology.v1",
        "endpoint_projected_tolerance_q16_16": 32768,
        "content_sha256": "0" * 64,
    }
    mutations.append(v1)

    for mutation in mutations:
        with pytest.raises(
            COMPILER.CompileFailure,
            match="active_stroke_topology_invalid",
        ):
            compile_block(mutation)


def test_generated_mutation_fixtures_break_the_owned_compiled_invariants():
    """Item, order, duplicate, length, and hash mutations stay detectable."""

    compiled = compile_block(authored_block())
    expected_order = compiled["topology_operation_order"]

    table_mutations: list[list[int]] = []
    item = compiled["gaussian_table_q0_16"].copy()
    item[10] += 1
    table_mutations.append(item)
    permutation = compiled["gaussian_table_q0_16"].copy()
    permutation[10], permutation[11] = permutation[11], permutation[10]
    table_mutations.append(permutation)
    duplicate = compiled["gaussian_table_q0_16"].copy()
    duplicate[10] = duplicate[11]
    table_mutations.append(duplicate)
    table_mutations.append(compiled["gaussian_table_q0_16"][:-1])
    for table in table_mutations:
        assert canonical_sha(table) != compiled["gaussian_table_sha256"]

    assert "0" * 64 != compiled["gaussian_table_sha256"]
    assert [
        "split", "delete", "trim", "extend", "merge", "insert"
    ] != expected_order
    assert [
        "delete", "split", "trim", "extend", "merge", "merge"
    ] != expected_order
    assert [
        "delete", "split", "trim", "extend", "merge", "unknown"
    ] != expected_order

    content_mutation = copy.deepcopy(compiled)
    content_mutation["alpha_q0_16"] += 1
    digest = content_mutation.pop("content_sha256")
    assert canonical_sha(content_mutation) != digest


def test_registry_projection_has_no_retired_silhouette_probe_hash():
    """Registry identity is exactly profile key plus v2 Active Stroke hash."""

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    rows = [
        {
            "profile_namespace": material["profile_namespace"],
            "material_key": material["material_key"],
            "active_stroke_topology_sha256":
                material["active_stroke_topology"]["content_sha256"],
        }
        for material in artifact["materials"]
        if material["active_stroke_topology"] is not None
    ]
    rows.sort(key=lambda row: (row["profile_namespace"], row["material_key"]))
    assert artifact["header"][
        "active_stroke_topology_registry_sha256"
    ] == canonical_sha(rows)
    assert "silhouette_visibility_admission_sha256" not in json.dumps(
        artifact,
        sort_keys=True,
    )
