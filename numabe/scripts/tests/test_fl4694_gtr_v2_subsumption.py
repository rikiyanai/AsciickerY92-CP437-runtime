#!/usr/bin/env python3
# =============================================================================
# test_fl4694_gtr_v2_subsumption.py
# =============================================================================
# WHAT THESE TESTS PROTECT
# ------------------------
# `scripts/measure_fl4694_gtr_v2_subsumption.py` answers one question: does the
# newer FL-4694 nearest-neighbour relation family already contain everything the
# older GTR v2 transform-relation table contains? If it does, GTR v2 is a
# duplicate owner and must be deleted. If it does not, GTR v2 must survive.
#
# That is a high-stakes answer, and the danger is not that the arithmetic is
# wrong - it is that the COMPARISON RULE gets quietly reshaped until it produces
# whichever answer someone wanted. These tests exist to make that reshaping
# visible:
#
#   * the rule must be predeclared and stored inside the result file;
#   * the verdict must be derived mechanically from counted mismatches, with no
#     thresholds and no tunable weights;
#   * exactly one verdict may be declared, never two and never none;
#   * an inability to measure must fail loudly rather than pass quietly;
#   * nothing here may be wired into a product build.
#
# Several tests drive the verdict function with hand-built synthetic numbers
# rather than the real 65,536-row table. That is deliberate: a test that only
# reads the committed file would keep passing even if the rule were deleted.
#
# Run with:  python3 -m pytest scripts/tests/test_fl4694_gtr_v2_subsumption.py
# =============================================================================

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# `parents[2]` walks up from scripts/tests/ to the repository root.
ROOT = Path(__file__).resolve().parents[2]

MEASURE_SCRIPT = ROOT / "scripts/measure_fl4694_gtr_v2_subsumption.py"
RESULT_PATH = ROOT / "assets/glyphs/generated/fl4694.gtr_v2_subsumption.v2.json"
SCHEMA_PATH = ROOT / "assets/glyphs/schema/fl4694.gtr_v2_subsumption.v2.schema.json"


def _load_measure_module():
    """Import the measurement script as a module so its functions can be probed.

    The script is a command-line tool rather than an installed package, so it is
    loaded by file path. Importing it does not run the measurement: everything
    heavy sits behind `main()`, guarded by the usual `if __name__ ==
    "__main__"` block.
    """
    spec = importlib.util.spec_from_file_location(
        "measure_fl4694_gtr_v2_subsumption", MEASURE_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_result() -> dict:
    """Read the committed answer file."""
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# 1. The pieces exist and agree about their own identity
# -----------------------------------------------------------------------------


def test_script_schema_and_result_all_exist() -> None:
    """All three deliverables must be present; a missing one breaks the audit chain."""
    assert MEASURE_SCRIPT.exists(), f"measurement script missing: {MEASURE_SCRIPT}"
    assert SCHEMA_PATH.exists(), f"schema missing: {SCHEMA_PATH}"
    assert RESULT_PATH.exists(), f"result artifact missing: {RESULT_PATH}"


def test_result_declares_its_schema_and_generator() -> None:
    """The result must name its own shape and the only script allowed to write it."""
    result = _load_result()
    assert result["schema"] == "fl4694.gtr_v2_subsumption.v2"
    assert result["version"] == 2
    assert result["owning_fl"] == "FL-4694"
    assert result["generated_by"] == "scripts/measure_fl4694_gtr_v2_subsumption.py"


def test_result_self_fingerprint_matches_its_own_contents() -> None:
    """The stored fingerprint must actually describe the stored bytes.

    A file cannot fingerprint itself including the fingerprint, so the
    convention (shared with the FL-4694 pointer manifest) is to hash the body
    with that one field removed. Recomputing it here catches a hand-edited
    artifact that was never regenerated.
    """
    module = _load_measure_module()
    result = _load_result()
    stored = result.pop("artifact_sha256")
    assert module.self_fingerprint(result) == stored, (
        "artifact_sha256 does not describe the file contents; the artifact was "
        "edited by hand instead of regenerated with --write"
    )


# -----------------------------------------------------------------------------
# 2. The comparison rule is predeclared, and it is the one that was used
# -----------------------------------------------------------------------------


def test_committed_contract_is_byte_identical_to_the_generator_constant() -> None:
    """The rule in the file must be the rule in the code, not a paraphrase.

    If someone edits the contract inside the artifact to describe a comparison
    that was never run, this test fails.
    """
    module = _load_measure_module()
    result = _load_result()
    assert result["predeclared_contract"] == module.PREDECLARED_CONTRACT


def test_contract_declares_it_was_written_before_the_measurement() -> None:
    """The predeclaration flag and contract identity must both be present."""
    contract = _load_result()["predeclared_contract"]
    assert contract["written_before_measurement"] is True
    assert contract["contract_id"] == "fl4694.gtr_v2_subsumption_contract.v2"
    assert contract["exactly_one_disposition"] is True
    # v2 was amended AFTER a measurement had already been run under v1, which
    # weakens the predeclaration guarantee. The contract must therefore say so
    # out loud, name what it replaced, and explain why - otherwise an amended
    # rule could pass itself off as an original one.
    assert contract["amended_after_first_run"] is True
    assert contract["supersedes_contract_id"] == "fl4694.gtr_v2_subsumption_contract.v1"
    assert contract["amendment_reason"].strip()


def test_contract_covers_exactly_the_six_required_axes() -> None:
    """All six comparison axes must be declared, with no extras and none missing.

    The six are fixed by the FL-4694 question itself: the two component vectors,
    confidence, the compiled cost, direction, and tie-breaking. Dropping one
    would let a real difference go unmeasured.
    """
    contract = _load_result()["predeclared_contract"]
    axis_ids = [axis["axis_id"] for axis in contract["axes"]]
    assert axis_ids == [
        "A1_static_components",
        "A2_affine_components",
        "A3_confidence",
        "A4_compiled_transform_cost",
        "A5_directionality",
        "A6_tie_behaviour",
    ]
    # Every axis must state a pass condition BEFORE any number exists.
    for axis in contract["axes"]:
        assert axis["pass_condition"].strip(), f"{axis['axis_id']} has no pass condition"


def test_contract_names_the_candidate_projection() -> None:
    """A subsumption claim is meaningless without naming what would do the subsuming."""
    projection = _load_result()["predeclared_contract"]["candidate_projection"]
    assert projection["projection_id"], "the candidate projection must be named"
    assert projection["why_this_projection_and_not_another"].strip()


# -----------------------------------------------------------------------------
# 3. Exactly one verdict, and it is mechanically derived
# -----------------------------------------------------------------------------


def test_result_declares_exactly_one_known_disposition() -> None:
    """One verdict, drawn from the closed set. Never two, never none, never novel."""
    result = _load_result()
    disposition = result["result"]["disposition"]
    assert disposition in {
        "EXACT_SUBSUMPTION",
        "DISTINCT_TRUTH",
        "NOT_SUBSUMED_BY_THIS_PROJECTION",
        "UNMEASURED",
    }


def _synthetic_axes(
    rows_compared: int = 65536,
    rows_covered: int = 65536,
    disagreements: dict | None = None,
) -> dict:
    """Build a fake axis block for driving the verdict function directly.

    Feeding the verdict function invented numbers keeps these tests meaningful
    even when the real data changes, and proves each branch is reachable rather
    than dead code.

    rows_compared  - size of the whole GTR v2 domain being asked about.
    rows_covered   - how many of those rows the projection can address at all.
    disagreements  - per-axis count of rows where the projection spoke and was
                     wrong. Anything not named here defaults to zero.

    Rows that are not covered are filled into every axis's not-addressable
    count, mirroring what the real measurement does.
    """
    disagreements = disagreements or {}
    not_addressable = rows_compared - rows_covered
    axes: dict = {
        "gtr_v2_rows_compared": rows_compared,
        "gtr_v2_rows_covered_by_projection": rows_covered,
    }
    for axis_id in (
        "A1_static_components",
        "A2_affine_components",
        "A3_confidence",
        "A4_compiled_transform_cost",
        "A5_directionality",
        "A6_tie_behaviour",
    ):
        axes[axis_id] = {
            "rows_disagreeing": disagreements.get(axis_id, 0),
            "rows_not_addressable": not_addressable,
        }
    return axes


def test_verdict_function_says_subsumption_only_on_a_perfect_score() -> None:
    """Synthetic all-zero mismatches with full coverage must give EXACT_SUBSUMPTION.

    This drives the verdict function directly with invented numbers, so it keeps
    working even if the real data changes, and it proves the positive branch is
    reachable rather than dead code.
    """
    module = _load_measure_module()
    verdict = module.derive_disposition(_synthetic_axes())
    assert verdict["disposition"] == "EXACT_SUBSUMPTION"
    assert verdict["total_axis_row_disagreements"] == 0
    assert verdict["total_axis_row_noncoverage"] == 0
    # The consequence must state the delete-list obligation, because that is the
    # entire point of asking the question.
    assert "E8" in verdict["consequence"]
    assert "MANDATORY" in verdict["consequence"]


def test_a_single_mismatch_anywhere_defeats_subsumption() -> None:
    """One mismatched row on any axis must flip the verdict to DISTINCT_TRUTH.

    Run once per axis so that no axis can be silently ignored by the rule.
    """
    module = _load_measure_module()
    for axis in (
        "A1_static_components",
        "A2_affine_components",
        "A3_confidence",
        "A4_compiled_transform_cost",
        "A5_directionality",
        "A6_tie_behaviour",
    ):
        # Full coverage plus exactly one genuine disagreement on this axis.
        axes = _synthetic_axes(disagreements={axis: 1})
        verdict = module.derive_disposition(axes)
        assert verdict["disposition"] == "DISTINCT_TRUTH", (
            f"a disagreement on {axis} did not defeat subsumption; the rule is "
            f"ignoring that axis"
        )
        assert verdict["total_axis_row_disagreements"] == 1


def test_incomplete_coverage_alone_defeats_subsumption() -> None:
    """Zero mismatches is not enough if most rows were never addressable at all.

    Without this, a projection that covers six rows perfectly and cannot see the
    other 65,530 would score a clean sweep.
    """
    module = _load_measure_module()
    axes = _synthetic_axes(rows_compared=65536, rows_covered=384)
    assert (
        module.derive_disposition(axes)["disposition"]
        == "NOT_SUBSUMED_BY_THIS_PROJECTION"
    )


def test_non_coverage_alone_is_never_reported_as_distinct_truth() -> None:
    """Silence must never be reported as contradiction. This is the v2 fix.

    Under the superseded v1 rule, rows the projection could not address were
    added to the mismatch total, so a projection that simply had no data at an
    address was scored exactly as if it had supplied a conflicting value. With
    coverage at 384 of 65536 rows that made DISTINCT_TRUTH the only reachable
    verdict, and DISTINCT_TRUTH asserts something much stronger than the data
    supports: that GTR v2 holds truth the family does not.

    Here the projection disagrees about NOTHING and is merely absent almost
    everywhere. The honest verdict is that this candidate did not subsume GTR
    v2, leaving the general question open - not that the two hold different
    truth.
    """
    module = _load_measure_module()
    axes = _synthetic_axes(rows_compared=65536, rows_covered=1)
    verdict = module.derive_disposition(axes)
    assert verdict["disposition"] == "NOT_SUBSUMED_BY_THIS_PROJECTION"
    assert verdict["total_axis_row_disagreements"] == 0
    # All three halves of the consequence must be stated, because dropping any
    # one of them lets the result be read as settling something it did not.
    consequence = verdict["consequence"]
    # (a) the delete-list entry cannot be promoted on this evidence
    assert "E8" in consequence
    # (b) distinct truth cannot be claimed either
    assert "does NOT" in consequence or "not establish" in consequence.lower()
    # (c) the finding is bounded to the projection actually tested, and must not
    #     be phrased as a claim about every possible projection
    assert "bounds only the projection it tested" in consequence
    assert "no projection of the family" not in consequence


def test_committed_result_reports_far_more_absence_than_disagreement() -> None:
    """Guards the real finding against being quietly restated as disagreement.

    The measured outcome is that the candidate projection is silent on 325,760
    rows and disagrees on only 1,536. If a future change ever inverts that
    balance without anyone noticing, the conclusion drawn from this artifact
    would change meaning entirely, so the shape of the evidence is asserted.
    """
    result = _load_result()["result"]
    if result["disposition"] == "UNMEASURED":
        pytest.skip("inputs unavailable in this checkout")
    assert result["total_axis_row_noncoverage"] > result["total_axis_row_disagreements"]
    assert result["coverage_fraction_of_domain"] < 0.01


def test_the_superseded_v1_verdict_is_preserved_for_audit() -> None:
    """An amended contract must show its own history, not bury it.

    Because the rule was changed after a run, the artifact carries what the old
    rule said about the identical numbers. Without this a reader cannot check
    whether the amendment was a correctness fix or a way to reach a nicer
    answer.
    """
    body = _load_result()
    if body["result"]["disposition"] == "UNMEASURED":
        pytest.skip("inputs unavailable in this checkout")
    superseded = body["superseded_by_amendment"]
    assert superseded["contract_id"] == "fl4694.gtr_v2_subsumption_contract.v1"
    assert superseded["disposition_under_v1"] == "DISTINCT_TRUTH"
    assert superseded["why_superseded"].strip()


def test_verdict_function_never_invents_unmeasured() -> None:
    """UNMEASURED must mean 'inputs were absent', never 'the data looked odd'.

    Feeding the verdict function extreme numbers must still yield one of the
    data-derived verdicts.
    """
    module = _load_measure_module()
    # An empty domain with enormous disagreement counts on every axis: nonsense
    # input, but the rule must still return a data-derived verdict rather than
    # reaching for the "we could not measure" escape hatch.
    extreme = _synthetic_axes(
        rows_compared=0,
        rows_covered=0,
        disagreements={
            "A1_static_components": 999999,
            "A2_affine_components": 999999,
            "A3_confidence": 999999,
            "A4_compiled_transform_cost": 999999,
            "A5_directionality": 999999,
            "A6_tie_behaviour": 999999,
        },
    )
    assert module.derive_disposition(extreme)["disposition"] != "UNMEASURED"


# -----------------------------------------------------------------------------
# 4. A DISTINCT_TRUTH verdict must be a buildable specification, not a shrug
# -----------------------------------------------------------------------------


def test_distinct_truth_names_the_projection_and_quantifies_every_axis() -> None:
    """If GTR v2 survives, the file must say exactly what fell short and by how much.

    A negative result is only useful if it tells the next person what to build.
    Skipped when the committed verdict is not DISTINCT_TRUTH, so the test stays
    correct if a future relation family really does subsume GTR v2.
    """
    result = _load_result()
    verdict = result["result"]
    # Both negative verdicts owe the reader a buildable specification: the one
    # that proves different truth, and the one that reports a failed candidate.
    if verdict["disposition"] not in (
        "DISTINCT_TRUTH",
        "NOT_SUBSUMED_BY_THIS_PROJECTION",
    ):
        pytest.skip(f"committed verdict is {verdict['disposition']}")

    assert verdict["named_projection_that_fell_short"], (
        "a negative verdict must name the projection that was tried"
    )
    counts = verdict["disagreement_counts_per_axis"]
    for axis in (
        "A1_static_components",
        "A2_affine_components",
        "A3_confidence",
        "A4_compiled_transform_cost",
        "A5_directionality",
        "A6_tie_behaviour",
    ):
        assert axis in counts, f"{axis} has no quantified mismatch count"
        assert isinstance(counts[axis], int)
    # The headline totals must be the plain sums of the per-axis columns, so a
    # reader can add them up by hand and get the same answer.
    assert verdict["total_axis_row_disagreements"] == sum(counts.values())
    assert verdict["total_axis_row_noncoverage"] == sum(
        verdict["non_coverage_counts_per_axis"].values()
    )
    # The consequence must address the delete-list entry either way.
    assert "E8" in verdict["consequence"]


def test_measurement_examined_the_whole_relation_table() -> None:
    """All 32 x 64 x 32 = 65,536 rows must be compared, not a sample.

    Skipped when the verdict is UNMEASURED, since nothing was compared then.
    """
    result = _load_result()
    if result["result"]["disposition"] == "UNMEASURED":
        pytest.skip("inputs were unavailable; nothing was measured")
    measurement = result["measurement"]
    assert measurement["gtr_v2_rows_compared"] == 32 * 64 * 32 == 65536


def test_inputs_are_fingerprinted_so_numbers_can_be_traced_to_bytes() -> None:
    """Every input must be pinned, and the never-committed cache must be verified."""
    result = _load_result()
    if result["result"]["disposition"] == "UNMEASURED":
        pytest.skip("inputs were unavailable; nothing was measured")
    inputs = result["inputs"]
    assert inputs["available"] is True
    assert len(inputs["gtr_v2"]["content_sha256"]) == 64
    family = inputs["fl4694_family"]
    assert len(family["cache_sha256_pinned"]) == 64
    assert family["cache_sha256_verified"] is True, (
        "the FL-4694 cache is never committed, so a run that did not verify its "
        "fingerprint may have compared against stale local data"
    )


# -----------------------------------------------------------------------------
# 5. Missing inputs must fail loudly, not degrade quietly
# -----------------------------------------------------------------------------


def test_missing_cache_raises_the_dedicated_unavailable_error(monkeypatch) -> None:
    """Pointing the loader at a nonexistent cache must raise InputsUnavailable.

    A dedicated exception type matters: it is the only thing permitted to
    produce UNMEASURED, so an ordinary bug can never be mistaken for absent
    inputs. The error message must also tell the operator how to regenerate.
    """
    module = _load_measure_module()
    monkeypatch.setattr(
        module, "FL4694_MANIFEST_PATH", ROOT / "assets/glyphs/generated/does-not-exist.json"
    )
    with pytest.raises(module.InputsUnavailable):
        module.resolve_fl4694_cache()


def test_cache_fingerprint_mismatch_is_refused(monkeypatch, tmp_path) -> None:
    """A cache whose contents disagree with the pin must be refused, not used.

    A stale or truncated local cache would silently change every number in the
    result. Comparing against it and publishing a verdict would be worse than
    publishing nothing.
    """
    module = _load_measure_module()

    # Build a throwaway manifest that points at a real file with a wrong pin.
    fake_cache = tmp_path / "fake_cache.jsonl"
    fake_cache.write_text("{}\n", encoding="utf-8")
    fake_manifest = tmp_path / "pointer.json"
    fake_manifest.write_text(
        json.dumps(
            {
                "local_artifacts": [
                    {
                        "name": "glyph_relation_knn",
                        # Path is recorded relative to the repository root, so
                        # express the temporary file that way.
                        "path": str(fake_cache),
                        "sha256": "0" * 64,  # deliberately wrong
                    }
                ],
                "regeneration": {"command": "python3 scripts/compile_fl4694_glyph_relations.py --write"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "FL4694_MANIFEST_PATH", fake_manifest)
    # An absolute path joined onto REPO yields the absolute path itself, so the
    # loader will find the throwaway file and then reject its fingerprint.
    with pytest.raises(module.InputsUnavailable) as caught:
        module.resolve_fl4694_cache()
    assert "fingerprint" in str(caught.value).lower()


# -----------------------------------------------------------------------------
# 6. Determinism, and no runtime linkage
# -----------------------------------------------------------------------------


def test_check_mode_passes_against_the_committed_artifact() -> None:
    """Recomputing must reproduce the committed file exactly.

    This is the determinism guarantee that makes the artifact trustworthy: the
    same inputs on any machine produce the same bytes. Skipped, not failed, when
    the never-committed caches are absent from this checkout - that situation is
    reported by the dedicated UNMEASURED path rather than by pretending to pass.
    """
    module = _load_measure_module()
    try:
        module.resolve_fl4694_cache()
    except module.InputsUnavailable as problem:
        pytest.skip(f"FL-4694 caches not present in this checkout: {problem}")
    assert module.main(["--check"]) == 0


def test_check_mode_fails_when_nothing_could_be_measured(monkeypatch) -> None:
    """An UNMEASURED recomputation must make --check exit non-zero.

    This is the rule that keeps an honest placeholder from becoming a
    comfortable one: if the caches are gone, the check fails rather than
    reporting a stale verdict as current.
    """
    module = _load_measure_module()
    monkeypatch.setattr(
        module, "FL4694_MANIFEST_PATH", ROOT / "assets/glyphs/generated/does-not-exist.json"
    )
    assert module.main(["--check"]) != 0


def test_artifact_and_script_are_not_wired_into_any_product_build() -> None:
    """Nothing in the shipped game may reference this offline measurement.

    The relation lane's standing rule is that no compiled relation data reaches
    a rendered frame yet. Scanning the build inputs for the artifact name is a
    cheap, direct check of that.
    """
    result = _load_result()
    assert result["runtime_consumption_allowed"] is False

    needles = ("fl4694.gtr_v2_subsumption", "measure_fl4694_gtr_v2_subsumption")
    build_surfaces = [
        ROOT / "Makefile",
        ROOT / "build-web.sh",
        ROOT / "CMakeLists.txt",
    ]
    for surface in build_surfaces:
        if not surface.exists():
            continue
        text = surface.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            assert needle not in text, (
                f"{surface.name} references {needle}; this offline measurement "
                f"must never be part of a product build"
            )


def test_schema_declares_the_closed_disposition_set() -> None:
    """The schema must pin the three allowed verdicts and forbid runtime consumption."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    disposition = schema["properties"]["result"]["properties"]["disposition"]
    assert set(disposition["enum"]) == {
        "EXACT_SUBSUMPTION",
        "DISTINCT_TRUTH",
        "NOT_SUBSUMED_BY_THIS_PROJECTION",
        "UNMEASURED",
    }
    assert schema["properties"]["runtime_consumption_allowed"]["const"] is False
