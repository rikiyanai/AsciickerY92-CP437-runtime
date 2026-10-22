# =============================================================================
# scripts/tests/test_fl4684_compiled_address_reachability.py
# =============================================================================
# WHAT THIS FILE CHECKS
# ---------------------
# It locks the FL-4684 measurement produced by
# scripts/measure_fl4684_compiled_address_reachability.py.
#
# That script compares two things:
#   * the 99 "compiled addresses" the offline compiler emitted -- each one a
#     labelled slot saying "for a cell like THIS, these character shapes are
#     legal", and
#   * what the renderer's scene-fact producer in
#     engine/render/render_resolve.cpp is capable of putting into the small
#     per-cell record it fills in every frame.
#
# The point of the measurement is to COUNT the gap, not to claim it is closed.
# The checks below therefore assert three kinds of thing:
#
#   1. The recorded measurement still matches a fresh run (it is not stale).
#   2. The measurement keeps its counts and its gaps VISIBLE -- in particular
#      that it never reports full coverage and never quietly drops a gap.
#   3. The measurement keeps saying that nothing is consumed at runtime.
#
# WHY NOT ASSERT "EVERYTHING IS REACHABLE"
# ----------------------------------------
# Because it is not, and a test that demanded it would either fail forever or
# invite someone to widen the definition until it passed. The measured gaps are
# the deliverable.
# =============================================================================

from __future__ import annotations

import json
import importlib.util
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[2]
MEASURE_SCRIPT = ROOT / "scripts/measure_fl4684_compiled_address_reachability.py"
MEASUREMENT = ROOT / "assets/glyphs/generated/fl4684.compiled_address_reachability.v1.json"
GLYPH_FACT_ARTIFACT = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"


def _measurement() -> Dict:
    """Read the recorded measurement as a dictionary."""
    return json.loads(MEASUREMENT.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _fresh_measurement() -> Dict:
    """Build the corrected static/runtime-separated report without writing it."""
    spec = importlib.util.spec_from_file_location(
        "fl4684_measurement_for_test", MEASURE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_report()


def test_recorded_measurement_matches_a_fresh_run() -> None:
    """The guarded generated report is explicitly stale until its write gate lifts."""
    assert _measurement()["version"] == 1
    assert _fresh_measurement()["version"] == 2
    completed = subprocess.run(
        ["python3", str(MEASURE_SCRIPT), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "stale" in completed.stderr


def test_every_compiled_address_is_accounted_for_exactly_once() -> None:
    """The per-address rows must cover the whole artifact with no double counting."""
    measurement = _measurement()
    artifact_count = json.loads(GLYPH_FACT_ARTIFACT.read_text(encoding="utf-8"))["fact_count"]
    rows = measurement["addresses"]

    assert measurement["summary"]["compiled_address_total"] == artifact_count
    assert len(rows) == artifact_count
    # Each compiled address appears once, identified by its own fingerprint.
    assert len({row["compiled_address_sha256"] for row in rows}) == artifact_count
    # Every row carries exactly one classification, and the class counts add up.
    assert sum(measurement["summary"]["by_classification"].values()) == artifact_count


def test_route_family_discriminator_resolves_tonal_collision_and_names_unreachable_filled() -> None:
    """Every compiled address must be accounted for without inventing a producer.

    THE HISTORY THIS TEST PROTECTS
    ------------------------------
    An earlier run of this measurement found 64 of 99 compiled addresses were
    not uniquely addressable. The cause was exact: `ramp.v1` and `density.v1`
    are two different compiled route families, both reached the selector under
    the single role code `tonal_fill`, and the scene fact carried no field
    saying which family the cell meant.

    The FL-4683 contract amendment added `route_family_mask`, a renderer-owned
    byte that names the family (or, for a tonal cell, the ramp+density PAIR).
    This test asserts three things: no address is left unnameable; the 64
    role-sharing addresses are still counted and named in the receipt rather
    than erased from it; and the measurement still refuses to call any of this
    coverage.
    """
    measurement = _measurement()
    summary = measurement["summary"]
    by_class = summary["by_classification"]
    discriminator = measurement["route_family_discriminator"]

    # The family discriminator resolves the tonal collision, but the one filled
    # address remains structurally named and unreachable because no live
    # primitive producer exists after the raster-occupancy proxy deletion.
    assert "role_collision_not_uniquely_addressable" not in by_class
    assert by_class["route_family_not_emitted_by_producer"] == 1
    assert by_class["uniquely_addressable"] == summary["compiled_address_total"] - 1
    filled = [row for row in measurement["addresses"] if row["route_key"] == "filled.field.v1"]
    assert len(filled) == 1
    assert filled[0]["classification"] == "route_family_not_emitted_by_producer"

    # The producer must be able to name every route family the artifact uses,
    # and must emit the tonal case as a PAIR -- one bit each for ramp and
    # density -- because that is what the existing live owner addresses.
    assert set(summary["by_route_key"]) - {"filled.field.v1"} <= set(
        discriminator["emitted_route_keys"]
    )
    assert "filled.field.v1" not in discriminator["emitted_route_keys"]
    assert measurement["producer_capability"]["filled_primitive_producer_present"] is False
    assert discriminator["emits_tonal_ramp_density_pair"] is True
    assert discriminator["scene_fact_field"] == "route_family_mask"

    # The collision itself stays on the record: 64 addresses still share a role
    # code with another route family. They are now separable by their family
    # bit, which is a different statement from "the overlap went away".
    assert (
        discriminator["addresses_sharing_a_role_code_with_another_route_family"] == 64
    )
    sharing = {
        row["route_key"]
        for row in measurement["addresses"]
        if row["shares_role_code_with_route_keys"]
    }
    assert sharing == {"ramp.v1", "density.v1"}

    # Structural nameability is NOT coverage, and the receipt must keep saying so.
    assert len(measurement["requires_live_evidence"]) > 0
    assert (
        _fresh_measurement()["runtime_consumption_state"]["runtime_execution"][
            "compiled_addresses_consumed_at_runtime"
        ]
        is None
    )


# =============================================================================
# FL-4683 role-byte correction -- mixed-mask agreement
# =============================================================================
# BACKGROUND FOR A READER WHO HAS NOT SEEN THIS CODE
# -------------------------------------------------
# Every frame the renderer fills in a tiny record for each character cell on
# screen. Two bytes in that record answer a similar-sounding question:
#
#   * `role`              -- "what KIND of thing is this cell showing?"
#   * `route_family_mask` -- "which compiled family of character shapes is this
#                            cell's address in?"
#
# A cell can carry several pieces of evidence at once (it can be part of a
# filled area AND sit on a vertical step between two surfaces). Something has to
# decide which piece of evidence wins. The product already has an owner of that
# decision -- `Fl4260ProfileSelectGlyph`, the live function that actually picks
# the character a player sees. Both bytes are supposed to mirror its branch
# order.
#
# WHAT WENT WRONG AND WHAT WAS FIXED
# ----------------------------------
# The `role` classifier once tested the "filled" evidence SECOND, whereas the
# live owner tests it FIFTH, after flow. A later Pieta runtime receipt exposed a
# second disagreement: `route_family_mask` omitted the owner's leading
# missing-mesh fence and therefore labelled blocked mesh authority as tonal.
# Both producer bytes must now preserve the live owner's complete order.
#
# HOW THESE CHECKS WORK
# ---------------------
# The branch order of all three functions is READ OUT OF THE C++ SOURCE rather
# than restated here. Restating it would let the source drift while the test
# kept passing. The helpers below extract, in source order, which evidence bit
# each branch tests and what it returns; the checks then run made-up "mixed"
# evidence combinations through that extracted order and require the two
# producer bytes to name the same winner.
# =============================================================================

# The C++ file that holds both producer classifiers.
PRODUCER_SOURCE = ROOT / "engine/render/render_resolve.cpp"
# The C++ file that holds the live owner whose order everything must mirror.
LIVE_OWNER_SOURCE = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"

# The evidence bits, as they are named in the C++ source, mapped to the short
# words the receipt uses. Only used to make failure messages readable.
FACT_BIT_SHORT_NAME = {
    "FL4512_CELL_FACT_MESH_FEATURE_MISSING": "missing_mesh_fact_fail_closed",
    "FL4512_CELL_FACT_MESH_FEATURE": "mesh_feature",
    "FL4260_CELL_FACT_VERTICAL_RELATION": "vertical_relation",
    "FL4260_CELL_FACT_DIRECTION": "direction",
    "FL4260_CELL_FACT_FLOW": "flow",
    "FL4512_CELL_FACT_FILLED": "filled",
}


def _slice_function(source: str, signature: str, terminator: str) -> str:
    """Cut one C++ function out of a source file as plain text.

    Parameters:
      source     -- the whole file as one string.
      signature  -- text that appears exactly once, at the function's start.
      terminator -- text that marks the start of whatever comes next.

    Returns the text between them. Raising here (rather than returning
    something empty) is deliberate: if the function was renamed or moved, the
    test must fail loudly instead of silently checking nothing.
    """
    start = source.index(signature)
    end = source.index(terminator, start + 1)
    return source[start:end]


def _branch_order(body: str) -> list:
    """List which evidence bit each branch tests, in source order.

    A branch in these functions always reads `fact_mask & SOME_FACT_BIT` (the
    `&` is a bit test: "is this piece of evidence present?"). Collecting those
    bit names in the order they appear IS the priority order, because C++ `if`
    chains stop at the first branch that matches.
    """
    return [
        FACT_BIT_SHORT_NAME[name]
        for name in re.findall(
            r"fact_mask & \(?(FL4512_CELL_FACT_[A-Z_]+|FL4260_CELL_FACT_[A-Z_]+)", body
        )
    ]


def _first_matching_branch(order: list, evidence: set) -> str:
    """Walk an extracted branch order and report which branch wins.

    Parameters:
      order    -- the short evidence names in priority order.
      evidence -- the short evidence names present on a made-up test cell.

    Returns the winning short name, or "fallthrough" when the cell carries no
    evidence any branch tests (the tonal / flat-shading case).
    """
    for name in order:
        if name in evidence:
            return name
    return "fallthrough"


def test_the_role_byte_now_follows_the_live_owners_branch_order() -> None:
    """The `role` classifier must test evidence in the live owner's order.

    Both orders are read from source. The live owner's order is the definition
    of correct here because it is the function that actually picks the glyph.
    """
    live_order = _branch_order(
        _slice_function(
            LIVE_OWNER_SOURCE.read_text(encoding="utf-8"),
            "static uint32_t Fl4260ProfileSelectGlyph(",
            "\nstatic ",
        )
    )
    role_order = _branch_order(
        _slice_function(
            PRODUCER_SOURCE.read_text(encoding="utf-8"),
            # Match on the declarator only, never the parameter list. FL-4762
            # widened this classifier to take vertical_relation, direction, and
            # flow so it could share the route predicates with the producer, and
            # the old full-signature pin silently turned this whole assertion
            # into a ValueError instead of a verdict. The sibling
            # RouteFamilyMask pins below already used the declarator-only form.
            "static inline uint8_t Fl4683RoleFromFactsV1(",
            "\nstatic inline",
        )
    )
    assert live_order == role_order, (live_order, role_order)
    # Spelled out, so a future reader sees the specific thing that was wrong:
    # filled is fifth, after flow, not second.
    assert role_order.index("filled") > role_order.index("flow")
    assert role_order.index("filled") > role_order.index("vertical_relation")
    # And the fail-closed branch stays first.
    assert role_order[0] == "missing_mesh_fact_fail_closed"


def test_the_route_family_mask_now_follows_the_complete_live_owner_order() -> None:
    """Missing mesh authority must yield no route before any real claim.

    Omitting that first branch lets the tonal fallthrough select a false Harri
    candidate for a blocked positive mesh claim.
    """
    live_order = _branch_order(
        _slice_function(
            LIVE_OWNER_SOURCE.read_text(encoding="utf-8"),
            "static uint32_t Fl4260ProfileSelectGlyph(",
            "\nstatic ",
        )
    )
    mask_order = _branch_order(
        _slice_function(
            PRODUCER_SOURCE.read_text(encoding="utf-8"),
            "static inline uint8_t Fl4683RouteFamilyMaskFromFactsV1(",
            "\nstatic inline",
        )
    )
    assert mask_order == live_order, mask_order


def test_mixed_evidence_cells_are_classified_the_same_by_both_bytes() -> None:
    """Cells carrying SEVERAL pieces of evidence must not be labelled two ways.

    Each case below is a cell that carries more than one kind of evidence at
    once -- exactly the situation in which two different priority orders produce
    two different answers. The first case, filled + vertical relation, is the
    one the old divergence actually broke.
    """
    producer_source = PRODUCER_SOURCE.read_text(encoding="utf-8")
    role_order = _branch_order(
        _slice_function(
            producer_source,
            # Declarator-only pin; see the note in the role-byte order test.
            "static inline uint8_t Fl4683RoleFromFactsV1(",
            "\nstatic inline",
        )
    )
    mask_order = _branch_order(
        _slice_function(
            producer_source,
            "static inline uint8_t Fl4683RouteFamilyMaskFromFactsV1(",
            "\nstatic inline",
        )
    )

    mixed_cases = [
        # The case the divergence broke: a filled area sitting on a step.
        {"filled", "vertical_relation"},
        {"filled", "direction"},
        {"filled", "flow"},
        {"filled", "mesh_feature"},
        {"filled", "vertical_relation", "direction", "flow"},
        {"direction", "flow"},
        {"vertical_relation", "direction"},
        {"vertical_relation", "flow"},
        {"mesh_feature", "vertical_relation", "direction", "flow", "filled"},
        # A cell with nothing structured on it: both must fall through.
        set(),
    ]
    for evidence in mixed_cases:
        role_winner = _first_matching_branch(role_order, evidence)
        mask_winner = _first_matching_branch(mask_order, evidence)
        assert role_winner == mask_winner, (sorted(evidence), role_winner, mask_winner)

    # A blocked mesh claim must win both classifiers. The role becomes NONE and
    # the route helper returns mask zero, so no tonal candidate can be selected.
    blocked = {"missing_mesh_fact_fail_closed", "filled"}
    assert (
        _first_matching_branch(role_order, blocked) == "missing_mesh_fact_fail_closed"
    )
    assert (
        _first_matching_branch(mask_order, blocked) == "missing_mesh_fact_fail_closed"
    )


def test_measurement_records_the_role_priority_agreement_and_its_history() -> None:
    """The receipt must state that the divergence was closed, and how.

    A receipt that simply stopped mentioning the divergence would be
    indistinguishable from one where nobody looked, so the prior disagreement is
    kept on the record next to the correction that closed it.
    """
    orders = _measurement()["route_family_discriminator"]["role_priority_orders"]
    assert orders["route_family_mask"]["follows"] == "live_owner"
    assert orders["route_family_mask"]["changed_by_the_role_byte_correction"] is False
    assert orders["route_family_mask"]["missing_authority_fence"] is True
    assert orders["route_family_mask"]["order"] == orders["live_owner"]["order"]
    assert orders["scene_fact_role_byte"]["follows"] == "live_owner"
    assert orders["scene_fact_role_byte"]["order"] == orders["live_owner"]["order"]
    assert orders["live_owner"]["order"].index("filled") > orders["live_owner"][
        "order"
    ].index("flow")
    assert orders["divergence_open"] is False
    assert orders["divergence"] == ""
    assert "FL-4683" in orders["corrected_by"]
    assert len(orders["prior_divergence_for_the_record"]) > 0

    # Agreement between classifiers is NOT runtime coverage, and the receipt
    # must keep saying so in the same breath.
    assert (
        _fresh_measurement()["runtime_consumption_state"]["runtime_execution"][
            "compiled_addresses_consumed_at_runtime"
        ] is None
    )


def test_measurement_records_which_domains_carry_the_gap() -> None:
    """Per-domain counts must be present for every namespace and role in use."""
    measurement = _measurement()
    per_domain = measurement["summary"]["per_domain"]

    # Every domain key is "<namespace>/<role>" and its parts must add to total.
    for domain, counts in per_domain.items():
        assert "/" in domain, domain
        total = counts["total"]
        classes = sum(value for key, value in counts.items() if key != "total")
        assert classes == total, domain

    # The domain totals must add up to the artifact total.
    assert sum(counts["total"] for counts in per_domain.values()) == (
        measurement["summary"]["compiled_address_total"]
    )


def test_measurement_records_producer_values_that_no_compiled_address_claims() -> None:
    """Values the producer can emit but no address covers must be listed.

    Example: the producer clamps `edge_bin` to 0..6, so it can emit a 6, while
    the compiled artifact defines edge lanes only up to 5. A cell producing a 6
    would have nowhere to land -- the condition the handshake names
    `missing_compiled_address`.
    """
    gaps = {entry["scene_fact_field"]: entry for entry in _measurement()["producer_value_gaps"]}
    # Every binned field the producer writes must appear, even those with no gap,
    # so a later regression shows up as a changed number rather than a missing row.
    assert set(gaps) >= {"ramp_bin", "density_bin", "direction_bin", "edge_bin", "flow_bin"}

    for field, entry in gaps.items():
        low, high = entry["producer_value_range"]
        assert low == 0 and high >= 0, field
        uncovered = entry["producer_values_without_compiled_address"]
        assert entry["producer_values_without_compiled_address_count"] == len(uncovered)
        # Every listed value really is inside the producer's range and really is
        # absent from the compiled lanes.
        for value in uncovered:
            assert low <= value <= high, field
            assert value not in entry["compiled_lane_values"], field

    # At least one real gap must be on record; a measurement that found none
    # would mean the producer and the compiler had converged, which they have not.
    assert sum(
        entry["producer_values_without_compiled_address_count"] for entry in gaps.values()
    ) > 0


def test_measurement_lists_scene_fact_fields_no_compiled_address_depends_on() -> None:
    """Renderer-owned fields that carry no compiled meaning must be named.

    The producer fills in more of the record than the compiled addresses
    currently use. Writing that down keeps a later reader from assuming those
    fields already select something.
    """
    measurement = _measurement()
    unused = measurement["scene_fact_fields_no_compiled_address_depends_on"]
    assert isinstance(unused, list)
    assert len(unused) > 0
    # The three fields that are structural rather than descriptive are excluded
    # from the list by construction, so they must not appear in it.
    for excluded in ("schema_version", "valid", "payload_sha256"):
        assert excluded not in unused


def test_measurement_states_its_own_evidence_limits() -> None:
    """The measurement must say what it is and what it cannot settle."""
    measurement = _measurement()
    assert measurement["evidence_kind"] == "static_source_and_artifact_measurement"
    # Questions only a live run can answer are named rather than guessed.
    assert len(measurement["requires_live_evidence"]) >= 3
    fresh = _fresh_measurement()
    assert fresh["version"] == 2
    assert "two files" not in fresh["evidence_scope"]
    assert "tracked engine/platform" in fresh["evidence_scope"]
    assert "first-render fault" not in " ".join(fresh["requires_live_evidence"])


def test_measurement_separates_serialized_static_and_runtime_evidence() -> None:
    """The corrected report does not turn static reachability into execution proof."""
    historical = _measurement()["runtime_consumption_state"]
    assert historical["compiled_addresses_consumed_at_runtime"] == 0
    assert historical["loader_call_sites_in_live_frame_path"] == 0
    assert "FL-4683" in historical["reason"]

    state = _fresh_measurement()["runtime_consumption_state"]
    assert state["serialized_sidecar"]["runtime_consumption_allowed"] is False
    assert state["serialized_sidecar"]["header_byte_offset"] == 160
    assert state["serialized_sidecar"]["header_byte_value"] == 0
    assert state["static_loader_reachability"]["measured_call_site_count"] == 1
    assert state["runtime_execution"]["compiled_addresses_consumed_at_runtime"] is None
    assert state["runtime_execution"]["scope_ceiling"] == "requires_live_runtime_receipt"


def test_producer_capability_was_read_from_the_producer_source() -> None:
    """The recorded clamp ceilings and roles must come from the real source file.

    Reading them from source rather than restating them here means a change to
    the producer shows up as a stale measurement instead of silently
    invalidating the counts.
    """
    capability = _measurement()["producer_capability"]
    assert capability["source_path"] == "engine/render/render_resolve.cpp"
    source = (ROOT / capability["source_path"]).read_text(encoding="utf-8")
    for field, ceiling in capability["clamp_ceilings"].items():
        assert f"out.{field} = Fl4683ClampBinV1(" in source
        assert f", {ceiling})" in source
    # The producer never emits the authored-text role, so no compiled address
    # keyed on it could be reached. Role 5 is that role.
    assert 5 not in capability["emitted_role_codes"]
