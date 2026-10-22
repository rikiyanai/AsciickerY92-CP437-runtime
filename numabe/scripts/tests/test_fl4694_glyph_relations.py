# =============================================================================
# test_fl4694_glyph_relations.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# scripts/compile_fl4694_glyph_relations.py compiles two families of information
# that were measured as MISSING from the accepted FL-4676 unified glyph record
# artifact:
#
#   1. "weighted relations" -- for every glyph, which other glyphs look most like
#      it, bounded to the k nearest so the result fits on a disk.
#   2. "ramp membership" -- which tonal (light-to-dark) and directional (turning)
#      ladders each glyph belongs to, and at which rung.
#
# The compiled caches themselves are deliberately NOT in version control; the
# repository commits only a small "pointer manifest" holding their SHA-256
# fingerprints, sizes, rules and the command that recreates them.  These tests
# therefore prove two different things:
#
#   * OFFLINE COMPONENT EVIDENCE -- the compiler behaves as promised: same inputs
#     give byte-identical outputs, a missing attribute stops it dead, the
#     neighbour count is bounded, weights are exact Q0.16 fractions summing to
#     65535, and the neighbour lists really are the nearest ones (checked against
#     a slow brute-force reference on a small universe).
#   * PIN EVIDENCE -- the committed manifest matches the schema, matches its own
#     self-fingerprint, matches every pinned input on disk, and matches the local
#     caches when those caches are present on this machine.
#
# NOTHING HERE IS A RUNTIME CLAIM.  The running game is still forbidden from
# consuming any of this data (FL-4683 owns that block), and one test asserts that
# the prohibition in the FL-4676 schema is still intact and unamended.
#
# Run with:
#     python3 -m pytest scripts/tests/test_fl4694_glyph_relations.py -q
# =============================================================================

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

# jsonschema is an optional dependency in this repository; without it the schema
# shape tests cannot run, so the whole module is skipped rather than reporting a
# misleading failure.
jsonschema = pytest.importorskip("jsonschema")

# scripts/tests/<this file> -> scripts/tests -> scripts -> repository root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

COMPILER = ROOT / "scripts/compile_fl4694_glyph_relations.py"
POINTER_MANIFEST = ROOT / "assets/glyphs/generated/fl4694.relation_pointer.v1.json"
POINTER_SCHEMA = ROOT / "assets/glyphs/schema/fl4694.relation_pointer.v1.schema.json"
FL4676_ARTIFACT = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
FL4676_SCHEMA = ROOT / "assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json"
# Scratch space for the miniature compiles the tests drive.  It lives inside the
# gitignored local cache directory so (a) nothing a test writes can ever be
# committed by accident and (b) the paths recorded in a test manifest are still
# repository-relative, exactly like the real thing.
TEST_SCRATCH_ROOT = ROOT / "assets/glyphs/generated/local/pytest-fl4694"

# A Q0.16 weight set that describes one whole thing must add up to exactly this.
NORMALIZED_SUM = 65535
# How many glyphs the fast miniature compiles use.  Small enough that a compile
# finishes in about a second, large enough to exercise bucketing and ties.
MINI_UNIVERSE = 600
# How many glyphs the brute-force cross-check uses.  Squared work in pure Python,
# so this stays small.
BRUTE_UNIVERSE = 150
MINI_K = 8


# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------


def _canonical(value: object) -> bytes:
    """The project's one canonical JSON byte form: sorted keys, no stray spaces."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha_value(value: object) -> str:
    """Fingerprint a Python value through its canonical JSON form."""
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_file(path: Path) -> str:
    """Fingerprint a file, reading it in blocks so 100+ MB caches stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSON-Lines file into a list, one parsed object per line."""
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _run_compile(output_dir: Path, universe_limit: int, k: int) -> dict:
    """Drive one miniature compile as a subprocess and return its pointer manifest.

    Using a subprocess rather than an in-process import is deliberate: it is the
    same entry point an operator or another machine would use, so what the tests
    exercise is the real command recorded in the manifest's regeneration field.

    Parameters
    ----------
    output_dir:
        Where this compile's three cache files and manifest are written.
    universe_limit:
        How many glyph records to compile (a shortened, fast universe).
    k:
        Neighbour breadth for this compile.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    result = subprocess.run(
        [
            "python3",
            str(COMPILER),
            "--write",
            "--universe-limit",
            str(universe_limit),
            "--k",
            str(k),
            "--output-dir",
            str(output_dir),
            "--manifest-path",
            str(manifest_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scratch_root() -> Path:
    """Create, hand over, and then remove the miniature-compile scratch directory."""
    if TEST_SCRATCH_ROOT.exists():
        shutil.rmtree(TEST_SCRATCH_ROOT)
    TEST_SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    yield TEST_SCRATCH_ROOT
    shutil.rmtree(TEST_SCRATCH_ROOT, ignore_errors=True)


@pytest.fixture(scope="module")
def mini_compile(scratch_root: Path) -> tuple[dict, Path]:
    """Run the standard miniature compile once and share it across tests."""
    output_dir = scratch_root / "mini"
    manifest = _run_compile(output_dir, MINI_UNIVERSE, MINI_K)
    return manifest, output_dir


@lru_cache(maxsize=1)
def _pointer_manifest() -> dict:
    """Load the committed pointer manifest."""
    return json.loads(POINTER_MANIFEST.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _pointer_schema() -> dict:
    """Load the committed pointer-manifest schema."""
    return json.loads(POINTER_SCHEMA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _fl4676_records() -> list[dict]:
    """Load the GlyphRecords from the accepted FL-4676 artifact, ascending by id."""
    document = json.loads(FL4676_ARTIFACT.read_text(encoding="utf-8"))
    return sorted(document["glyph_records"], key=lambda row: int(row["glyph_record_id"]))


# -----------------------------------------------------------------------------
# 1. The committed pointer manifest
# -----------------------------------------------------------------------------


def test_01_pointer_manifest_matches_its_schema_and_self_fingerprint() -> None:
    """The committed manifest must satisfy the committed schema and its own hash.

    The self-fingerprint is the manifest hashed with the fingerprint field taken
    out.  Recomputing it the same way is how a reader detects a hand edit.
    """
    manifest = _pointer_manifest()
    jsonschema.Draft202012Validator(_pointer_schema()).validate(manifest)

    unhashed = dict(manifest)
    declared = unhashed.pop("manifest_sha256")
    assert _sha_value(unhashed) == declared

    # Form A-LOCAL specifics the operator chose.
    assert manifest["form"] == "A_LOCAL"
    assert manifest["relation_contract"]["k"] == 32
    assert manifest["relation_contract"]["k_revisable"] is True
    assert manifest["relation_contract"]["pruning"] == "none"
    assert manifest["relation_contract"]["approximation"] is False
    assert manifest["relation_contract"]["tie_break"] == "ascending_glyph_record_id"
    assert manifest["local_artifact_policy"]["committed"] is False
    assert manifest["regeneration"]["deterministic"] is True
    assert (
        manifest["regeneration"]["command"]
        == "python3 scripts/compile_fl4694_glyph_relations.py --write"
    )
    # A full compile, not a shortened test compile.
    assert manifest["glyph_universe"]["universe_limit"] == 0
    assert manifest["glyph_universe"]["compiled_record_count"] == 111041


def test_02_pointer_manifest_pins_every_input_it_actually_used() -> None:
    """Every pinned input must exist and still carry the recorded fingerprint.

    A pointer manifest is only worth anything if it names exactly what went in.
    If any of these drift, the local cache must be regenerated before its
    fingerprints mean anything.
    """
    manifest = _pointer_manifest()
    pins = {pin["name"]: pin for pin in manifest["source_pins"]}
    assert {
        "fl4676_glyph_fact_artifact",
        "fl4676_glyph_fact_schema",
        "fl4676_compiler_script",
        "fl4694_compiler_script",
        "fl4694_relation_pointer_schema",
    }.issubset(pins)
    for pin in pins.values():
        path = ROOT / pin["path"]
        assert path.exists(), pin["path"]
        # Paths must be repository-relative so the manifest is machine independent.
        assert not pin["path"].startswith("/")
        assert _sha_file(path) == pin["sha256"], pin["path"]

    # The manifest also echoes the source artifact's own declared identity, which
    # is what lets a reader confirm the caches came from the accepted artifact.
    universe = manifest["glyph_universe"]
    source = json.loads(
        (ROOT / pins["fl4676_glyph_fact_artifact"]["path"]).read_text(encoding="utf-8")
    )
    assert universe["source_artifact_schema"] == source["schema"]
    assert universe["declared_glyph_record_count"] == source["glyph_record_count"]
    assert universe["source_canonical_content_sha256"] == source["canonical_content_sha256"]
    assert universe["source_artifact_id"] == source["artifact_identity"]["artifact_id"]
    assert (
        universe["source_compiler_script_sha256"]
        == source["artifact_identity"]["compiler_script_sha256"]
    )


def test_03_pointer_manifest_pins_match_the_local_caches_on_this_machine() -> None:
    """If the never-committed caches are present here, they must match the pins.

    On a fresh clone the caches do not exist yet, which is the intended state:
    the test then skips and tells the reader the one command that creates them.
    """
    manifest = _pointer_manifest()
    missing = [
        pin["path"] for pin in manifest["local_artifacts"] if not (ROOT / pin["path"]).exists()
    ]
    if missing:
        pytest.skip(
            "local FL-4694 caches absent on this machine; regenerate with: "
            f"{manifest['regeneration']['command']} (missing: {', '.join(missing)})"
        )
    for pin in manifest["local_artifacts"]:
        path = ROOT / pin["path"]
        assert path.stat().st_size == pin["byte_size"], pin["path"]
        assert _sha_file(path) == pin["sha256"], pin["path"]
        # Every local artifact must sit inside the gitignored directory.
        assert pin["path"].startswith("assets/glyphs/generated/local/")


# -----------------------------------------------------------------------------
# 2. Determinism
# -----------------------------------------------------------------------------


def test_04_two_independent_compiles_produce_identical_fingerprints(
    scratch_root: Path,
) -> None:
    """Running the compiler twice must give byte-identical caches.

    This is the property the whole Form A-LOCAL arrangement rests on: because the
    caches are never committed, a second machine has to be able to rebuild them
    and get exactly the same bytes, otherwise the committed fingerprints could
    never be checked.  The two runs write to different directories so nothing is
    shared between them except the pinned inputs.
    """
    first = _run_compile(scratch_root / "determinism_a", MINI_UNIVERSE, MINI_K)
    second = _run_compile(scratch_root / "determinism_b", MINI_UNIVERSE, MINI_K)

    first_pins = {pin["name"]: (pin["sha256"], pin["byte_size"]) for pin in first["local_artifacts"]}
    second_pins = {
        pin["name"]: (pin["sha256"], pin["byte_size"]) for pin in second["local_artifacts"]
    }
    assert first_pins == second_pins
    assert len(first_pins) == 3

    # The manifests differ only in the recorded output paths (different scratch
    # directories); everything else, including the diagnostics, must be identical.
    def _strip_paths(manifest: dict) -> dict:
        stripped = json.loads(json.dumps(manifest))
        for pin in stripped["local_artifacts"]:
            pin.pop("path")
        stripped.pop("manifest_sha256")
        return stripped

    assert _strip_paths(first) == _strip_paths(second)
    # No wall-clock value may have leaked into the stored diagnostics; if one had,
    # the two runs above would already have differed.
    assert set(first["compile_diagnostics"]) == set(second["compile_diagnostics"])


# -----------------------------------------------------------------------------
# 3. Fail-closed behaviour
# -----------------------------------------------------------------------------


def _write_stub_artifact(path: Path, records: list[dict]) -> None:
    """Write a minimal file shaped like the FL-4676 artifact around given records.

    Only the fields the FL-4694 compiler actually reads are included.  This lets a
    test hand the compiler a deliberately damaged universe without touching the
    real 98 MB artifact.
    """
    source = json.loads(FL4676_ARTIFACT.read_text(encoding="utf-8"))
    document = {
        "schema": "fl4676.glyph_fact.v1",
        "glyph_record_count": len(records),
        "glyph_records": records,
        "runtime_rights": source["runtime_rights"],
        "artifact_identity": source["artifact_identity"],
        "canonical_content_sha256": source["canonical_content_sha256"],
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def _compile_expecting_failure(output_dir: Path, artifact_path: Path) -> str:
    """Run a compile that is supposed to fail; return its standard-error text."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "python3",
            str(COMPILER),
            "--write",
            "--k",
            "4",
            "--glyph-fact-artifact",
            str(artifact_path),
            "--output-dir",
            str(output_dir),
            "--manifest-path",
            str(output_dir / "manifest.json"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2, (
        "compile was expected to fail closed but returned "
        f"{result.returncode}: {result.stdout}{result.stderr}"
    )
    return result.stderr


def test_05_a_record_missing_a_required_attribute_aborts_the_whole_compile(
    scratch_root: Path,
) -> None:
    """Deleting one required number from one record must stop the compile.

    "Fail closed" is the point: a glyph whose ink coverage is missing must not be
    quietly skipped, given a default, or dropped from the neighbour search.  The
    compile stops and names the offending record and field.
    """
    records = json.loads(json.dumps(_fl4676_records()[:80]))
    victim = records[40]
    victim_id = victim["glyph_record_id"]
    del victim["morphology"]["density_q0_16"]

    artifact_path = scratch_root / "missing_density.json"
    _write_stub_artifact(artifact_path, records)
    stderr = _compile_expecting_failure(scratch_root / "missing_density", artifact_path)
    assert "required_attribute_missing" in stderr
    assert "density_q0_16" in stderr
    assert str(victim_id) in stderr


def test_06_missing_connectivity_and_structure_fields_also_abort(scratch_root: Path) -> None:
    """The same fail-closed rule covers every attribute family the rule consumes."""
    # (a) A missing pixel mask.
    records = json.loads(json.dumps(_fl4676_records()[:80]))
    del records[10]["connectivity"]["skeleton_u64_le_hex"]
    artifact_path = scratch_root / "missing_skeleton.json"
    _write_stub_artifact(artifact_path, records)
    stderr = _compile_expecting_failure(scratch_root / "missing_skeleton", artifact_path)
    assert "required_attribute_missing" in stderr
    assert "skeleton_u64_le_hex" in stderr

    # (b) A missing topology count.
    records = json.loads(json.dumps(_fl4676_records()[:80]))
    del records[11]["structure"]["junction_count"]
    artifact_path = scratch_root / "missing_junction.json"
    _write_stub_artifact(artifact_path, records)
    stderr = _compile_expecting_failure(scratch_root / "missing_junction", artifact_path)
    assert "required_attribute_missing" in stderr
    assert "junction_count" in stderr

    # (c) A missing stroke-lean vector, which the directional ramps depend on.
    records = json.loads(json.dumps(_fl4676_records()[:80]))
    del records[12]["stroke_identity"]["orientation_q1_15"]
    artifact_path = scratch_root / "missing_orientation.json"
    _write_stub_artifact(artifact_path, records)
    stderr = _compile_expecting_failure(scratch_root / "missing_orientation", artifact_path)
    assert "required_attribute_missing" in stderr
    assert "orientation_q1_15" in stderr


def test_07_an_amended_runtime_prohibition_aborts_the_compile(scratch_root: Path) -> None:
    """Flipping the runtime ban on must stop this compiler cold.

    Form A-LOCAL exists precisely so the FL-4676 ban on runtime consumption can
    stay in force.  If somebody amended the artifact to allow runtime derivation
    (the rejected "Form B"), this compiler refuses to run rather than silently
    producing data for a use that was turned down.
    """
    records = json.loads(json.dumps(_fl4676_records()[:80]))
    artifact_path = scratch_root / "amended_rights.json"
    _write_stub_artifact(artifact_path, records)
    document = json.loads(artifact_path.read_text(encoding="utf-8"))
    document["runtime_rights"]["runtime_consumption_allowed"] = True
    artifact_path.write_text(json.dumps(document), encoding="utf-8")

    stderr = _compile_expecting_failure(scratch_root / "amended_rights", artifact_path)
    assert "runtime_rights_amended" in stderr


# -----------------------------------------------------------------------------
# 4. The bounded neighbour lists
# -----------------------------------------------------------------------------


def test_08_every_glyph_gets_exactly_k_distinct_other_glyphs(
    mini_compile: tuple[dict, Path],
) -> None:
    """k is a hard bound: never more, never fewer, never the glyph itself.

    The whole reason Form A-LOCAL is affordable is that each glyph carries a fixed
    small number of edges instead of one edge per glyph in the universe.  If the
    bound leaked the artifact size argument would collapse.
    """
    manifest, output_dir = mini_compile
    rows = _read_jsonl(output_dir / "fl4694.glyph_relation_knn.v1.jsonl")
    header, records = rows[0], rows[1:]

    assert header["schema"] == "fl4694.glyph_relation_knn.v1"
    assert header["k"] == MINI_K
    assert header["self_edges_excluded"] is True
    assert header["runtime_consumption_allowed"] is False
    assert len(records) == MINI_UNIVERSE == header["record_count"]

    known_ids = {row["glyph_record_id"] for row in records}
    for row in records:
        neighbors = row["neighbors"]
        assert len(neighbors) == MINI_K
        targets = [edge[0] for edge in neighbors]
        # No repeats, no self-reference, nothing outside the compiled universe.
        assert len(set(targets)) == MINI_K
        assert row["glyph_record_id"] not in targets
        assert set(targets).issubset(known_ids)

    assert manifest["compile_diagnostics"]["knn_edge_count"] == MINI_UNIVERSE * MINI_K


def test_09_neighbour_weights_are_exact_q0_16_fractions_summing_to_65535(
    mini_compile: tuple[dict, Path],
) -> None:
    """Each glyph's k weights must be whole numbers adding to exactly 65535.

    Q0.16 means an integer from 0 to 65535 standing for a fraction from 0.0 to
    1.0.  "Adding to exactly 65535" is what makes a weight set a genuine share-out
    with no rounding crumbs left over.  The weights must also fall as the distance
    rises, so the nearest relative always carries the largest share.
    """
    from compile_fl4676_glyph_facts import normalized_member_weights

    _manifest, output_dir = mini_compile
    rows = _read_jsonl(output_dir / "fl4694.glyph_relation_knn.v1.jsonl")
    header, records = rows[0], rows[1:]
    assert header["weight_algorithm"] == "descending_rank_largest_remainder.v1"
    assert header["weight_sum_per_record_q0_16"] == NORMALIZED_SUM

    # The compiler must use the FL-4676-owned algorithm, not a second copy of it.
    expected_weights = normalized_member_weights(MINI_K)
    assert sum(expected_weights) == NORMALIZED_SUM

    for row in records:
        weights = [edge[2] for edge in row["neighbors"]]
        distances = [edge[1] for edge in row["neighbors"]]
        assert weights == expected_weights
        assert sum(weights) == NORMALIZED_SUM
        assert all(isinstance(weight, int) and 0 < weight <= NORMALIZED_SUM for weight in weights)
        # Weights descend with rank, and rank follows distance.
        assert weights == sorted(weights, reverse=True)
        assert distances == sorted(distances)
        assert all(isinstance(distance, int) and distance >= 0 for distance in distances)


def test_10_neighbour_lists_match_a_brute_force_reference(scratch_root: Path) -> None:
    """Re-derive the answer the slow obvious way and demand the same result.

    The compiler uses a matrix-multiplication shortcut to compare 111,040 glyphs
    against each other in minutes rather than days.  This test rebuilds a small
    universe's neighbour lists with plain Python whole-number arithmetic -- no
    shortcuts, no floating point -- and requires an exact match, including the
    "lowest glyph_record_id wins" tie-break.
    """
    from compile_fl4694_glyph_relations import (
        FEATURE_DIMENSIONS,
        build_feature_matrix,
        exact_distance,
        family_weight_vector,
    )

    manifest = _run_compile(scratch_root / "brute", BRUTE_UNIVERSE, MINI_K)
    rows = _read_jsonl(scratch_root / "brute" / "fl4694.glyph_relation_knn.v1.jsonl")
    compiled = {row["glyph_record_id"]: row["neighbors"] for row in rows[1:]}

    records = _fl4676_records()[:BRUTE_UNIVERSE]
    matrix, record_ids = build_feature_matrix(records)
    weights = family_weight_vector()
    assert matrix.shape == (BRUTE_UNIVERSE, FEATURE_DIMENSIONS)
    assert manifest["relation_contract"]["feature_dimensions"] == FEATURE_DIMENSIONS

    for source in range(BRUTE_UNIVERSE):
        # Score this glyph against every other glyph the slow, exact way.
        scored = [
            (exact_distance(matrix[source], matrix[other], weights), record_ids[other])
            for other in range(BRUTE_UNIVERSE)
            if other != source
        ]
        # Sorting by (distance, glyph_record_id) is literally the promised rule:
        # nearest first, ties broken by the lower identifier.
        scored.sort()
        expected = [(record_id, distance) for distance, record_id in scored[:MINI_K]]
        actual = [(edge[0], edge[1]) for edge in compiled[record_ids[source]]]
        assert actual == expected, f"neighbour mismatch for glyph_record_id {record_ids[source]}"


def test_11_the_recorded_distance_rule_is_complete_and_integer_only() -> None:
    """The manifest alone must be enough to re-derive the distance rule.

    Someone with only the committed manifest should be able to rebuild the caches.
    That means the manifest has to name every attribute the rule reads, how many
    numbers each supplies, its scale and its importance weight -- and the total
    has to match the compiler's own count.
    """
    from compile_fl4694_glyph_relations import FEATURE_DIMENSIONS, family_weight_vector

    contract = _pointer_manifest()["relation_contract"]
    assert contract["feature_dimensions"] == FEATURE_DIMENSIONS
    assert sum(block["dimensions"] for block in contract["feature_layout"]) == FEATURE_DIMENSIONS
    attributes = {block["attribute"] for block in contract["feature_layout"]}
    assert attributes == {
        "morphology.shape6_norm_q0_16",
        "morphology.density_q0_16",
        "structure.beta0|beta1|endpoint_count|junction_count",
        "stroke_identity.orientation_q1_15",
        "connectivity.mask8_u64_le_hex",
        "connectivity.skeleton_u64_le_hex",
    }
    # Every weight and scale is a whole number; that is what keeps the arithmetic
    # exact and therefore identical on every machine.
    for block in contract["feature_layout"]:
        assert isinstance(block["weight"], int) and block["weight"] >= 1
        assert isinstance(block["scale"], int) and block["scale"] >= 1
    # The manifest's stated weights must be the weights the compiler really uses.
    weights = family_weight_vector()
    cursor = 0
    for block in contract["feature_layout"]:
        span = block["dimensions"]
        assert {int(value) for value in weights[cursor : cursor + span]} == {block["weight"]}
        cursor += span
    assert cursor == FEATURE_DIMENSIONS


# -----------------------------------------------------------------------------
# 5. Ramp membership
# -----------------------------------------------------------------------------


def test_12_every_glyph_sits_in_exactly_one_tonal_ramp(
    mini_compile: tuple[dict, Path],
) -> None:
    """Tonal coverage is total: no glyph is left without a place on a tone ladder.

    This is the direct answer to the measured gap "per-record membership in tonal
    ramps is absent".  Directional coverage is deliberately smaller, because a
    glyph with no lean at all has no position on a turning circle; those are
    counted and reported rather than quietly dropped.
    """
    manifest, output_dir = mini_compile
    diagnostics = manifest["compile_diagnostics"]
    index_rows = _read_jsonl(output_dir / "fl4694.ramp_record_index.v1.jsonl")
    header, entries = index_rows[0], index_rows[1:]

    assert header["schema"] == "fl4694.ramp_record_index.v1"
    assert len(entries) == MINI_UNIVERSE == header["record_count"]
    assert diagnostics["tonal_member_count"] == MINI_UNIVERSE

    tonal_seen = 0
    directional_seen = 0
    for entry in entries:
        families = [row["ramp_family"] for row in entry["ramp_memberships"]]
        # Exactly one tonal ladder each; at most one directional ladder each.
        assert families.count("tonal") == 1
        assert families.count("directional") <= 1
        tonal_seen += 1
        directional_seen += families.count("directional")
        for row in entry["ramp_memberships"]:
            assert row["ramp_position"] >= 0
            assert row["ramp_id"].startswith("glyphramp://fl4694/v1/")

    assert tonal_seen == MINI_UNIVERSE
    assert directional_seen == diagnostics["directional_member_count"]
    assert (
        directional_seen + diagnostics["directional_degenerate_orientation_excluded"]
        == MINI_UNIVERSE
    )


def test_13_ramps_are_bounded_normalized_and_monotone(
    mini_compile: tuple[dict, Path],
) -> None:
    """Each ladder must be a real progression: bounded, weighted, and strictly rising.

    * bounded  -- never more than 32 rungs, matching the FL-4676 fact member cap.
    * weighted -- member weights are Q0.16 whole numbers adding to exactly 65535,
                  in the same member shape the accepted fact schema uses.
    * rising   -- the axis value strictly increases from rung to rung, which is
                  what makes it a ramp rather than an unordered bag.
    """
    from compile_fl4676_glyph_facts import normalized_member_weights

    manifest, output_dir = mini_compile
    rows = _read_jsonl(output_dir / "fl4694.glyph_ramp.v1.jsonl")
    header, ramps = rows[0], rows[1:]

    assert header["schema"] == "fl4694.glyph_ramp.v1"
    assert header["member_shape"] == ["glyph_record_id", "membership_weight_q0_16"]
    assert header["member_cap"] == 32
    assert len(ramps) == header["ramp_count"]
    assert len(ramps) == (
        manifest["compile_diagnostics"]["tonal_ramp_count"]
        + manifest["compile_diagnostics"]["directional_ramp_count"]
    )

    seen_ramp_ids: set[str] = set()
    for ramp in ramps:
        members = ramp["glyph_members"]
        assert 1 <= len(members) <= 32
        assert ramp["member_count"] == len(members)
        assert ramp["ramp_family"] in {"tonal", "directional"}

        # Member shape is exactly the accepted fact-member shape -- no extra
        # intrinsic metadata smuggled into the member rows.
        for member in members:
            assert set(member) == {"glyph_record_id", "membership_weight_q0_16"}

        weights = [member["membership_weight_q0_16"] for member in members]
        assert weights == normalized_member_weights(len(members))
        assert sum(weights) == NORMALIZED_SUM
        assert ramp["subset_weight_sum_q0_16"] == NORMALIZED_SUM

        # Strictly increasing axis values: a genuine ladder, not a heap.
        levels = ramp["axis_levels"]
        assert len(levels) == len(members)
        assert levels == sorted(levels)
        assert len(set(levels)) == len(levels)

        # Identifier is unique and is the fingerprint of the ramp's own content.
        assert ramp["ramp_id"] not in seen_ramp_ids
        seen_ramp_ids.add(ramp["ramp_id"])
        body = {key: value for key, value in ramp.items() if key not in {"ramp_id", "ramp_sha256"}}
        assert ramp["ramp_sha256"] == _sha_value(body)
        assert ramp["ramp_id"].endswith(ramp["ramp_sha256"][:16])


def test_14_ramp_membership_index_agrees_with_the_ramp_definitions(
    mini_compile: tuple[dict, Path],
) -> None:
    """The per-glyph index and the ramp list must tell the same story.

    Two files describe the same facts from opposite directions -- one ramp at a
    time, and one glyph at a time.  A disagreement between them would make the
    ramp data unusable, so the cross-check is explicit.
    """
    _manifest, output_dir = mini_compile
    ramps = _read_jsonl(output_dir / "fl4694.glyph_ramp.v1.jsonl")[1:]
    entries = _read_jsonl(output_dir / "fl4694.ramp_record_index.v1.jsonl")[1:]

    # Rebuild the index from the ramp definitions and require an exact match.
    rebuilt: dict[int, set[tuple[str, str, int]]] = {}
    for ramp in ramps:
        for position, member in enumerate(ramp["glyph_members"]):
            rebuilt.setdefault(member["glyph_record_id"], set()).add(
                (ramp["ramp_id"], ramp["ramp_family"], position)
            )
    from_index: dict[int, set[tuple[str, str, int]]] = {}
    for entry in entries:
        from_index[entry["glyph_record_id"]] = {
            (row["ramp_id"], row["ramp_family"], row["ramp_position"])
            for row in entry["ramp_memberships"]
        }
    assert rebuilt == from_index


def test_15_direction_sectors_follow_the_true_turning_order() -> None:
    """The whole-number angle substitute must order directions like a real angle.

    Sorting glyphs by lean angle with the usual arctangent function would give
    slightly different answers on different maths libraries, and the compile would
    stop being reproducible.  The compiler instead uses the "diamond angle", built
    from whole numbers only.  This test walks a full turn of the circle and
    confirms the substitute increases in exactly the same order as the true angle,
    and that a glyph with no lean at all is rejected rather than given a fake
    direction.
    """
    import math

    from compile_fl4694_glyph_relations import (
        DIRECTION_SECTOR_COUNT,
        RelationCompileError,
        direction_sector,
        pseudo_angle,
    )

    previous = None
    for degrees in range(0, 360, 3):
        radians = math.radians(degrees)
        # A radius of 30000 keeps the components inside the q1.15 range the
        # artifact uses for orientation vectors.
        x = round(30000 * math.cos(radians))
        y = round(30000 * math.sin(radians))
        value = pseudo_angle(x, y)
        assert 0 <= value < 4
        if previous is not None:
            assert value > previous, f"diamond angle went backwards at {degrees} degrees"
        previous = value
        sector = direction_sector(x, y)
        assert 0 <= sector < DIRECTION_SECTOR_COUNT

    # Known anchor points around the circle.
    assert pseudo_angle(1, 0) == 0
    assert pseudo_angle(0, 1) == 1
    assert pseudo_angle(-1, 0) == 2
    assert pseudo_angle(0, -1) == 3

    # A glyph with no lean has no place on the circle and must be refused.
    with pytest.raises(RelationCompileError, match="orientation_degenerate"):
        pseudo_angle(0, 0)


# -----------------------------------------------------------------------------
# 6. The runtime prohibition stays exactly where it was
# -----------------------------------------------------------------------------


def test_16_the_fl4676_runtime_prohibition_is_untouched_and_restated() -> None:
    """Compiling FL-4694 must not have loosened the FL-4676 runtime ban.

    The rejected alternative ("Form B") would have amended the schema so the
    running game could derive relations for itself.  It was turned down.  This
    test reads the FL-4676 schema directly and requires that the ban is still
    written there as a fixed constant, and that the FL-4694 manifest restates the
    same ban rather than replacing it.
    """
    schema = json.loads(FL4676_SCHEMA.read_text(encoding="utf-8"))
    rights = schema["properties"]["runtime_rights"]["properties"]
    assert rights["runtime_consumption_allowed"] == {"const": False}
    assert rights["blocked_by"] == {"const": "FL-4683"}
    forbidden = set(rights["forbidden_authorities"]["items"]["enum"])
    assert "derive_relation_edges_at_runtime" in forbidden
    assert "derive_glyph_metadata_at_runtime" in forbidden

    manifest_rights = _pointer_manifest()["runtime_rights"]
    assert manifest_rights["runtime_consumption_allowed"] is False
    assert manifest_rights["blocked_by"] == "FL-4683"
    assert manifest_rights["source_schema_amended"] is False
    assert manifest_rights["derivation_owner"] == "offline_compiler"
    assert forbidden.issuperset(manifest_rights["forbidden_authorities"])

    # The local caches must repeat the ban in their own headers, so a reader who
    # opens only a cache file still sees it.
    for pin in _pointer_manifest()["local_artifacts"]:
        path = ROOT / pin["path"]
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            header = json.loads(handle.readline())
        assert header["runtime_consumption_allowed"] is False
