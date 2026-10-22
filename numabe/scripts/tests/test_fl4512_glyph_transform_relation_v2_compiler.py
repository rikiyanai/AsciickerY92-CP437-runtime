"""Focused contracts for the offline GlyphTransformRelation v2 compiler."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4512_glyph_transform_relation_v2.py"
ARTIFACT_PATH = ROOT / "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"


def _load_compiler():
    """Import the compiler from its executable path without changing its API."""
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("fl4512_gtr_v2_compiler", COMPILER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def compiler():
    return _load_compiler()


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_canonical_checked_in_bytes_are_deterministic() -> None:
    """A rebuild must equal the tracked canonical bytes."""
    completed = subprocess.run(
        [sys.executable, str(COMPILER_PATH), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "relations=65536" in completed.stdout


def test_exact_cardinalities_order_and_four_hash_folds(compiler, artifact) -> None:
    """The complete row shape and all compiler-owned SHA folds are present."""
    compiler.validate_document(artifact)
    assert artifact["glyph_ids"] == sorted(set(artifact["glyph_ids"]))
    assert len(artifact["descriptors"]) == 32
    assert len(artifact["aspects"]) == 64
    assert len(artifact["relations"]) == 65536
    assert all(len(value) == 64 for value in (
        artifact["descriptor_sha256"],
        artifact["aspect_sha256"],
        artifact["relation_sha256"],
        artifact["content_sha256"],
    ))


def test_generated_document_satisfies_the_checked_in_schema(artifact) -> None:
    """The public JSON schema accepts the complete canonical compiler output."""
    schema = json.loads(
        (ROOT / "assets/glyphs/schema/fl4512.glyph_transform_relation.v2.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(artifact)


def test_identity_zero_every_aspect_and_view_variety(artifact) -> None:
    """Every identity costs zero and the chart is not one repeated mirror."""
    identities = [
        row for row in artifact["relations"]
        if row["from_glyph_id"] == row["to_glyph_id"]
    ]
    assert len(identities) == 64 * 32
    assert all(row["compiled_transform_cost"] == 0 for row in identities)
    assert all(not any(row["static_components"]) for row in identities)
    assert all(not any(row["affine_components"]) for row in identities)
    transforms = [row["discrete_transform"] for row in artifact["aspects"]]
    assert "reflection_0" in transforms
    assert "reflection_1" in transforms
    assert transforms.count(None) >= 1
    assert artifact["aspects"][63]["compiled_relation_confidence_q0_16"] < 65535


def test_exact_mirror_c4_and_oblique_relations_exist(artifact) -> None:
    """The generated corpus contains discrete partner facts plus oblique rows."""
    descriptors = {row["glyph_id"]: row for row in artifact["descriptors"]}
    mirror_pairs = [
        (row["glyph_id"], partner)
        for row in artifact["descriptors"]
        for partner in row["reflection_partner_glyph_ids"]
        if partner is not None and partner != row["glyph_id"]
    ]
    c4_pairs = [
        (row["glyph_id"], partner)
        for row in artifact["descriptors"]
        for partner in row["c4_partner_glyph_ids"][1:]
        if partner is not None and partner != row["glyph_id"]
    ]
    assert mirror_pairs
    assert c4_pairs
    assert descriptors[mirror_pairs[0][0]]
    oblique = next(row for row in artifact["aspects"] if row["discrete_transform"] is None)
    relation = next(
        row for row in artifact["relations"]
        if row["aspect"] == oblique["address"]
        and row["from_glyph_id"] != row["to_glyph_id"]
    )
    assert len(relation["static_components"]) == 11
    assert len(relation["affine_components"]) == 7


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("v1", "compiled_schema"),
        ("descriptor_hash", "descriptor_hash"),
        ("content_hash", "content_hash"),
        ("missing_aspect", "compiled_aspect_cardinality"),
        ("permutation", "compiled_relation_order"),
        ("duplicate", "compiled_relation_order"),
    ],
)
def test_one_fact_negative_corpus_rejects(compiler, artifact, mutation, reason) -> None:
    """Each named one-fact corruption is rejected at a stable compiler gate."""
    candidate = dict(artifact)
    if mutation == "v1":
        candidate["schema"] = "fl4512.glyph_transform_relation.v1"
    elif mutation == "descriptor_hash":
        candidate["descriptor_sha256"] = "0" * 64
    elif mutation == "content_hash":
        candidate["content_sha256"] = "0" * 64
    elif mutation == "missing_aspect":
        candidate["aspects"] = artifact["aspects"][:-1]
    elif mutation == "permutation":
        candidate["relations"] = list(artifact["relations"])
        candidate["relations"][1], candidate["relations"][2] = (
            candidate["relations"][2],
            candidate["relations"][1],
        )
    else:
        candidate["relations"] = list(artifact["relations"])
        candidate["relations"][2] = candidate["relations"][1]
    with pytest.raises(compiler.CompileError, match=reason):
        compiler.validate_document(candidate)


def test_boolean_integer_rank_loss_and_overflow_authoring_reject(compiler) -> None:
    """Authored chart facts reject Boolean numbers, rank loss, and overflow."""
    authoring = compiler._authoring_template()
    boolean = copy.deepcopy(authoring)
    boolean["aspects"][0]["address"] = True
    with pytest.raises(compiler.CompileError, match="integer_required"):
        compiler._validate_authoring(boolean)
    rank_loss = copy.deepcopy(authoring)
    rank_loss["aspects"][2]["chart_matrix_q16_16"] = [1, 2, 2, 4]
    with pytest.raises(compiler.CompileError, match="aspect_rank_loss"):
        compiler._validate_authoring(rank_loss)
    with pytest.raises(compiler.CompileError, match="affine_overflow"):
        compiler._q824(compiler.Fraction(1 << 20, 1), "fixture")


def test_all_eight_bound_hashes_are_content_hashes(artifact) -> None:
    """The header binds each paper-named source identity with lowercase SHA-256."""
    assert set(artifact["bound_sha256"]) == {
        "admitted_row_sha256",
        "font_bytes_sha256",
        "raster_identity_sha256",
        "atlas_registry_sha256",
        "descriptor_sha256",
        "aspect_asset_sha256",
        "material_profile_sha256",
        "schema_sha256",
    }
    assert all(
        len(value) == 64 and value == value.lower()
        for value in artifact["bound_sha256"].values()
    )
