# =============================================================================
# scripts/tests/test_fl4763_compiler_owned_structural_decline.py
# =============================================================================
# FL-4763: the compiler now STATES that it has no structural operand for the
# EDGE, DIRECTION, and FLOW families, instead of pointing them at the tonal
# shape6 scorer and letting the runtime discover the mismatch.
#
# WHAT WAS WRONG
# --------------
# scripts/compile_fl4512_pools.py chose a row's scorer with a fallthrough:
#
#     U7_SCORER_ID    if role == "mesh_feature"
#     FILLED_SCORER_ID if role == "filled"
#     else TONAL_SCORER_ID          <-- edge, direction, flow landed here
#
# So a populated structural address named a scorer whose live operand is read
# back out of the pre-glyph TONAL colour raster. The row then claimed that the
# shape of a step, a compass orientation, or a flow axis could be decided from
# how the cell was SHADED. FL-4763's runtime fence caught that at selection
# time, but the compiled artifact still asserted a tonal answer to a structural
# question, and any consumer that trusted `scorer_id` inherited the mistake.
#
# WHAT THE RULE IS NOW
# --------------------
# Those three roles compile to an explicit identity,
# `fl4763.structural_decline.v1` (loader kind 4). It keeps the populated address
# and every admitted candidate, and it carries a ZERO member-operand union, no
# policy flag, no primitive field, and no threshold. The runtime declines it and
# the lower-authority tonal pair may take the cell.
#
# The absence of evidence became data the artifact carries, rather than
# something inferred from a scorer that never fitted.
#
# WHAT THIS MODULE PINS
# ---------------------
# Each acceptance item of the FL-4763 compiler-owner contract:
#   compiler mapping, U7/S_pf ownership, loader validation, native selector
#   behaviour, predictor parity, and the RETAINED defensive fence for a legacy
#   artifact whose structural rows still name shape6.
#
# The native harness, image crafting, and row driving are IMPORTED from
# scripts/tests/test_fl4512_phase2_native_policy.py rather than copied, so the
# modules cannot drift. No product build is touched and no game is launched.
#
# A NOTE ON WHICH GATE IS LIVE TODAY
# ----------------------------------
# The committed sidecar under assets/glyphs/generated/ has NOT been recompiled
# (republication is FL-4772's, and it is a separately approved step). Its
# edge/direction/flow facts therefore still name shape6, and the retained
# defensive fence -- not the new explicit decline -- is what handles them right
# now. Both paths are exercised below precisely because the artifact and the
# compiler are intentionally out of step until that republication happens.
#
# Run with:
#     python3 -m pytest \
#       scripts/tests/test_fl4763_compiler_owned_structural_decline.py -q
# =============================================================================

from __future__ import annotations

import importlib.util
import struct
import subprocess
from pathlib import Path

import pytest

from scripts.tests.test_fl4512_phase2_native_policy import (
    EDGE_KEY,
    FACT_POLICY_FLAGS,
    FACT_RECORD_SIZE,
    FACT_SCORER_KIND,
    FACT_THRESHOLD_U32,
    FAMILY_DENSITY,
    FAMILY_RAMP,
    OFF_FACT_TABLE_OFFSET,
    OUTCOME_SELECTED,
    ROOT,
    TONAL_PAIR_MASK,
    U7_KEY,
    _build_harness,
    _row,
    _run,
    base_image,
    fact_scorer_kind,
    finalize,
    parse_index,
    populated_fact_index,
    read_u32,
)

SELECTOR_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
PREDICTOR_SOURCE = ROOT / "scripts/predict_fl4678_exact_cell_glyph.py"

# Scorer kinds, as in Fl4683GlyphProgramScorerKindV1.
SCORER_HARRI_SHAPE6_WEIGHTED_L2 = 1
SCORER_U7_MASK = 2
SCORER_PRIMITIVE_FIELD = 3
SCORER_STRUCTURAL_DECLINE = 4
# Deliberately outside the vocabulary: stands in for an artifact compiled by a
# NEWER compiler than this runtime.
SCORER_UNKNOWN_FUTURE = 9

DECLINE_SCORER_ID = "fl4763.structural_decline.v1"

# Sidecar header offsets for the member table (mirrors kOffsetFactMember* in
# platform/fl4683_glyph_program_loader.cpp).
OFF_MEMBER_TABLE_OFFSET = 168
# Within one 20-byte member record: id u32, weight u16, operand[12], reserved u16.
MEMBER_RECORD_SIZE = 20
MEMBER_OPERAND = 6
MEMBER_OPERAND_SIZE = 12

# Within one 128-byte fact record.
FACT_MEMBER_OFFSET = 96
FACT_MEMBER_COUNT = 100
FACT_PRIMITIVE_REGION_CLASS = 120
FACT_PRIMITIVE_PRIORITY = 122

# A non-constant tonal operand: six alternating extremes cannot trip the
# FL-4512 degenerate-operand rule, so any decline seen below is FL-4763's.
LIVE_TONAL_OPERAND = [65535, 0, 65535, 0, 65535, 0]
FALLBACK_RAMP_LANE = 2
FALLBACK_DENSITY_LANE = 1
EDGE_LANE = EDGE_KEY[3]


def _load(name: str, relative: str):
    """Import one compiler module by path; they are scripts, not a package."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POOLS = _load("fl4512_pools", "scripts/compile_fl4512_pools.py")
PROGRAM = _load("fl4683_program", "scripts/compile_fl4683_glyph_program.py")
FACTS = _load("fl4676_facts", "scripts/compile_fl4676_glyph_facts.py")


# ---------------------------------------------------------------------------
# Acceptance 1-3: the compiler's scorer mapping
# ---------------------------------------------------------------------------
def test_tonal_shape6_is_assigned_only_to_ramp_and_density():
    """Acceptance 1. The tonal scorer may answer only tonal questions."""
    assert POOLS.row_scorer_id("tonal", "addr") == POOLS.TONAL_SCORER_ID
    for role in ("edge", "direction", "flow", "mesh_feature", "filled"):
        assert POOLS.row_scorer_id(role, "addr") != POOLS.TONAL_SCORER_ID, (
            f"role {role!r} still resolves to the tonal shape6 scorer"
        )


def test_edge_direction_and_flow_take_the_explicit_structural_decline():
    """Acceptance 2. The three structural families name the decline identity."""
    for role in ("edge", "direction", "flow"):
        assert POOLS.row_scorer_id(role, "addr") == POOLS.DECLINE_SCORER_ID
    assert POOLS.DECLINE_SCORER_ID == DECLINE_SCORER_ID


def test_u7_stays_on_mesh_and_s_pf_stays_on_filled():
    """Acceptance 3. The change must not disturb the two REAL structural owners.

    This is the control for the falsifier "any mesh structural fact loses U7".
    """
    assert POOLS.row_scorer_id("mesh_feature", "addr") == POOLS.U7_SCORER_ID
    assert POOLS.row_scorer_id("filled", "addr") == POOLS.FILLED_SCORER_ID


def test_an_unnamed_role_is_refused_rather_than_defaulted_to_tone():
    """The deleted fallthrough must not come back in another shape.

    The defect was not only that edge/direction/flow got the tonal scorer -- it
    was that they got it by DEFAULT, silently, through an `else`. A role this
    compiler does not recognise must now fail the artifact by name. Without this
    test a future role could inherit tone exactly as these three did.
    """
    with pytest.raises(POOLS.CompileFailure) as caught:
        POOLS.row_scorer_id("some_future_role", "terrain:4/some_future_role/x/0")
    assert caught.value.reason == "row_scorer_unassigned"


def test_the_retired_tonal_fallthrough_is_gone_from_the_source():
    """DELETE FIRST: no compatibility writer may remain beside the replacement."""
    source = (ROOT / "scripts/compile_fl4512_pools.py").read_text(encoding="utf-8")
    assert "else TONAL_SCORER_ID" not in source, (
        "the retired default-to-tone mapping is still present in the pool compiler"
    )


# ---------------------------------------------------------------------------
# A declining row keeps its address and its members, and carries no operand
# ---------------------------------------------------------------------------
def _decline_row(role: str = "edge") -> dict:
    """One compiled row for a structural family, built by the real make_row."""
    gids = [11, 12, 13]
    u7_descriptors = {
        gid: {"glyph_id": gid, "global6_norm": [0.5] * 6} for gid in gids
    }
    return POOLS.make_row(
        "terrain", 4, role, f"{role}.v1", 5, f"{role}[5]", gids, set(gids),
        u7_descriptors,
    )


def test_a_declining_row_keeps_its_populated_address_and_every_candidate():
    """The falsifier "turns populated addresses into declared absence" must not fire."""
    row = _decline_row()
    assert row["candidate_glyph_ids"] == [11, 12, 13]
    assert row["candidate_count"] == 3
    assert row["bucket_state"] == "live"
    assert row["scorer_id"] == DECLINE_SCORER_ID


def test_a_declining_rows_descriptors_carry_no_scorer_operand():
    """The zero member-operand union starts here, in the pool.

    Emitting `global6_norm` on a declining row would re-seal the tonal vector
    into a structural row's sealed identity -- the exact provenance this change
    removes -- even though the runtime would never read it.
    """
    row = _decline_row()
    assert row["candidate_descriptors"] == [
        {"glyph_id": 11}, {"glyph_id": 12}, {"glyph_id": 13}
    ]


def test_a_tonal_row_still_carries_its_shape6_operand():
    """Control: the decline must not strip operands from rows that DO score."""
    gids = [11, 12]
    u7_descriptors = {
        gid: {"glyph_id": gid, "global6_norm": [0.25] * 6} for gid in gids
    }
    row = POOLS.make_row(
        "terrain", 4, "tonal", "ramp.v1", 2, "ramp[2]", gids, set(gids),
        u7_descriptors,
    )
    assert row["scorer_id"] == POOLS.TONAL_SCORER_ID
    for descriptor in row["candidate_descriptors"]:
        assert "global6_norm" in descriptor


def test_the_pool_declares_the_decline_in_its_scorer_vocabulary():
    assert DECLINE_SCORER_ID in [
        POOLS.TONAL_SCORER_ID, POOLS.U7_SCORER_ID, POOLS.FILLED_SCORER_ID,
        POOLS.DECLINE_SCORER_ID,
    ]


# ---------------------------------------------------------------------------
# Acceptance 4-5: downstream compilers preserve the identity, invent nothing
# ---------------------------------------------------------------------------
def test_the_program_compiler_maps_the_decline_id_to_kind_four():
    assert PROGRAM.SCORER_KIND_BY_ID[DECLINE_SCORER_ID] == SCORER_STRUCTURAL_DECLINE
    assert PROGRAM.SCORER_KIND_STRUCTURAL_DECLINE == SCORER_STRUCTURAL_DECLINE


def test_a_decline_member_operand_is_twelve_zero_bytes():
    """Acceptance 4-5. No invented live operand, and the stride is unchanged.

    Twelve bytes is the fixed member stride for EVERY scorer kind, so encoding
    the decline as zeros keeps the member stride and the program byte-size
    contract exactly as they were.
    """
    encoded = PROGRAM.encode_member_scorer_operand(
        SCORER_STRUCTURAL_DECLINE,
        {"morphology": {"shape6_norm_q0_16": [65535] * 6}},
        {},
        0,
        11,
    )
    assert encoded == b"\x00" * MEMBER_OPERAND_SIZE
    assert len(encoded) == MEMBER_OPERAND_SIZE


def test_the_decline_gets_no_policy_flag_and_no_threshold():
    """Acceptance 4/6. A row that never scores may carry no bound to score by."""
    assert PROGRAM.fact_shape6_no_match_policy(
        SCORER_STRUCTURAL_DECLINE, {"route_key": "edge.v1"}, {}, 0
    ) == (0, 0)
    assert PROGRAM.fact_primitive_policy(
        SCORER_STRUCTURAL_DECLINE, {"route_key": "edge.v1"}, {}, 0
    ) == (0, 0, 0)


def test_the_glyphfact_compiler_does_not_invent_a_temporal_policy():
    """Acceptance 4. A temporal margin over a score that is never computed would
    be a policy about nothing, so the decline is exempt as S_pf already is."""
    assert DECLINE_SCORER_ID in FACTS.SCORERS_WITHOUT_TEMPORAL_POLICY
    assert "S_pf.v1" in FACTS.SCORERS_WITHOUT_TEMPORAL_POLICY


def test_the_pin_receipt_schema_names_the_new_kind():
    import json
    schema = json.loads(
        (ROOT / "assets/glyphs/schema/fl4683.glyph_program_pin.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    values = schema["$defs"]["fl4683_glyph_program_pin"]["properties"] \
        if "$defs" in schema else None
    # The receipt block is nested; find it by key rather than by path so this
    # test survives a schema reorganisation.
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "scorer_kind_values" in node:
                found.append(node["scorer_kind_values"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert found, "the pin schema no longer declares scorer_kind_values"
    for block in found:
        assert block["properties"][DECLINE_SCORER_ID] == {"const": 4}
        assert DECLINE_SCORER_ID in block["required"]


# ---------------------------------------------------------------------------
# Native image crafting for the loader and selector
# ---------------------------------------------------------------------------
def _fact_base(blob: bytes, fact_index: int) -> int:
    return read_u32(blob, OFF_FACT_TABLE_OFFSET) + fact_index * FACT_RECORD_SIZE


def _set_scorer_kind(blob: bytearray, fact_index: int, kind: int) -> None:
    blob[_fact_base(blob, fact_index) + FACT_SCORER_KIND] = kind


def _member_span(blob: bytes, fact_index: int) -> tuple[int, int]:
    base = _fact_base(blob, fact_index)
    offset = struct.unpack_from("<I", blob, base + FACT_MEMBER_OFFSET)[0]
    count = struct.unpack_from("<H", blob, base + FACT_MEMBER_COUNT)[0]
    return offset, count


def _member_operand_base(blob: bytes, member_index: int) -> int:
    table = read_u32(blob, OFF_MEMBER_TABLE_OFFSET)
    return table + member_index * MEMBER_RECORD_SIZE + MEMBER_OPERAND


def _zero_member_operands(blob: bytearray, fact_index: int) -> None:
    offset, count = _member_span(blob, fact_index)
    for member in range(offset, offset + count):
        base = _member_operand_base(blob, member)
        blob[base:base + MEMBER_OPERAND_SIZE] = b"\x00" * MEMBER_OPERAND_SIZE


def craft_decline_image(
    *,
    operand_byte: int = 0,
    threshold: int = 0,
    policy_flags: int = 0,
    region_class: int = 0,
    priority: int = 0,
) -> bytes:
    """The committed terrain:4 edge fact, converted to an explicit decline.

    The knobs exist so each loader rejection below differs from the ACCEPTED
    image in exactly one field. Without that control a rejection could be
    rejecting the crafting rather than the rule under test.
    """
    blob = base_image()
    index = parse_index(blob)
    edge_fact = populated_fact_index(index, EDGE_KEY)
    assert fact_scorer_kind(blob, edge_fact) == SCORER_HARRI_SHAPE6_WEIGHTED_L2, (
        "the committed terrain:4 edge fact was expected to still name shape6; "
        "once FL-4772 republishes the pool it will name the decline directly "
        "and this crafting step becomes unnecessary rather than wrong"
    )
    _set_scorer_kind(blob, edge_fact, SCORER_STRUCTURAL_DECLINE)
    _zero_member_operands(blob, edge_fact)
    base = _fact_base(blob, edge_fact)
    blob[base + FACT_POLICY_FLAGS] = policy_flags
    struct.pack_into("<I", blob, base + FACT_THRESHOLD_U32, threshold)
    struct.pack_into("<H", blob, base + FACT_PRIMITIVE_REGION_CLASS, region_class)
    struct.pack_into("<H", blob, base + FACT_PRIMITIVE_PRIORITY, priority)
    if operand_byte:
        offset, _count = _member_span(blob, edge_fact)
        blob[_member_operand_base(blob, offset)] = operand_byte
    return finalize(blob)


def craft_unknown_scorer_image() -> bytes:
    """The same edge fact pointed at a kind this runtime does not implement."""
    blob = base_image()
    index = parse_index(blob)
    edge_fact = populated_fact_index(index, EDGE_KEY)
    _set_scorer_kind(blob, edge_fact, SCORER_UNKNOWN_FUTURE)
    return finalize(blob)


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    return _build_harness(tmp_path_factory.mktemp("fl4763-decline-harness"))


@pytest.fixture(scope="module")
def images(tmp_path_factory) -> dict:
    directory = tmp_path_factory.mktemp("fl4763-decline-images")
    crafted = {
        "decline": craft_decline_image(),
        "decline_nonzero_operand": craft_decline_image(operand_byte=0x7F),
        "decline_nonzero_threshold": craft_decline_image(threshold=1),
        "decline_policy_flag": craft_decline_image(policy_flags=0x1),
        "decline_primitive_fields": craft_decline_image(region_class=1, priority=1),
        "unknown_scorer": craft_unknown_scorer_image(),
        "legacy_shape6_edge": finalize(base_image()),
    }
    paths = {}
    for name, blob in crafted.items():
        path = directory / f"{name}.bin"
        path.write_bytes(blob)
        paths[name] = path
    return paths


LOADED = ("loaded", "loaded_without_local_relation_cache")


# ---------------------------------------------------------------------------
# Acceptance 6: loader validation of the explicit decline
# ---------------------------------------------------------------------------
def test_loader_accepts_a_well_formed_explicit_decline(harness, images, tmp_path):
    """The control. Every rejection below is the same image plus one bad field."""
    status, _ = _run(harness, images["decline"], [], tmp_path)
    assert status in LOADED, status


@pytest.mark.parametrize(
    "image_name",
    [
        "decline_nonzero_operand",
        "decline_nonzero_threshold",
        "decline_policy_flag",
        "decline_primitive_fields",
    ],
)
def test_loader_rejects_a_decline_carrying_any_operand_or_policy(
    harness, images, tmp_path, image_name
):
    """Acceptance 6 and the falsifier "any explicit-decline fact can carry a
    nonzero operand or threshold".

    A declining row that still carries a bound, a flag, or operand bytes is a
    compiled contradiction. Admitting it would leave a value in the image that a
    later runtime edit could start honouring.
    """
    status, _ = _run(harness, images[image_name], [], tmp_path)
    assert status == "malformed_sidecar", status


# ---------------------------------------------------------------------------
# Acceptance 7: native selector behaviour
# ---------------------------------------------------------------------------
def _select(harness, images, tmp_path, image_name: str, row: str) -> dict:
    status, rows = _run(harness, images[image_name], [row], tmp_path)
    assert status in LOADED, status
    assert len(rows) == 1
    return rows[0]


def test_the_selector_declines_an_explicit_decline_row_to_the_tonal_pair(
    harness, images, tmp_path
):
    """Acceptance 7. The decline is not a refusal and not a declared absence.

    The cell still gets a glyph -- from the lower-authority tonal pair on the
    next pass -- and the structural gap stays COUNTABLE in the abstain counters.
    That countability is the whole point: it is what makes the sparsity of real
    structural claims measurable instead of silently painted over.
    """
    row = _row(
        TONAL_PAIR_MASK | EDGE_KEY[2],
        FALLBACK_RAMP_LANE,
        FALLBACK_DENSITY_LANE,
        EDGE_LANE,
        LIVE_TONAL_OPERAND,
    )
    result = _select(harness, images, tmp_path, "decline", row)
    assert result["outcome"] == OUTCOME_SELECTED
    assert result["glyph"] > 0
    assert result["abstained"] >= 1, "the decline must be counted, not hidden"
    assert result["family_bit"] in (FAMILY_RAMP, FAMILY_DENSITY), (
        "a declining structural row must never be the winning family"
    )


def test_a_declining_row_never_selects_a_glyph_itself(harness, images, tmp_path):
    """The falsifier "any explicit-decline fact selects a glyph directly"."""
    row = _row(
        EDGE_KEY[2], 0, 0, EDGE_LANE, LIVE_TONAL_OPERAND,
    )
    status, rows = _run(harness, images["decline"], [row], tmp_path)
    assert status in LOADED, status
    assert len(rows) == 1
    assert rows[0]["outcome"] != OUTCOME_SELECTED, (
        "an edge-only cell whose single family declines must not select a glyph"
    )


def test_the_legacy_shape6_edge_row_still_declines(harness, images, tmp_path):
    """Acceptance 7. The RETAINED defensive fence.

    This is the committed artifact, untouched: its edge fact still names shape6.
    Until FL-4772 republishes the pool, this fence -- not the explicit decline --
    is what keeps structure from being scored against tone in the product. It
    must survive the arrival of the new kind.
    """
    row = _row(
        TONAL_PAIR_MASK | EDGE_KEY[2],
        FALLBACK_RAMP_LANE,
        FALLBACK_DENSITY_LANE,
        EDGE_LANE,
        LIVE_TONAL_OPERAND,
    )
    result = _select(harness, images, tmp_path, "legacy_shape6_edge", row)
    assert result["abstained"] >= 1
    assert result["family_bit"] in (FAMILY_RAMP, FAMILY_DENSITY)


def test_a_mesh_u7_row_still_selects_from_its_own_evidence(harness, images, tmp_path):
    """Control for "any mesh structural fact loses U7" -- U7 must still SCORE."""
    blob = images["decline"].read_bytes()
    index = parse_index(bytearray(blob))
    mesh_fact = populated_fact_index(index, U7_KEY)
    assert fact_scorer_kind(blob, mesh_fact) == SCORER_U7_MASK, (
        "the mesh bundle row must still name the u7 mask scorer"
    )


def test_an_unknown_scorer_is_refused_by_the_loader_not_declined(
    harness, images, tmp_path
):
    """The falsifier "any unknown scorer silently declines", at the load gate.

    The loader is the first of two independent owners of this rule: an image
    naming a kind this build cannot execute never becomes selectable at all.
    """
    status, _ = _run(harness, images["unknown_scorer"], [], tmp_path)
    assert status == "malformed_sidecar", status


def test_the_selector_asks_known_and_structural_as_two_separate_questions():
    """The second owner of the unknown-scorer rule, in the selector itself.

    The loader above already stops an unknown kind from loading, so the
    selector's own gate is defence in depth and cannot be reached through a
    loaded image. It is pinned on the source because that is where the defect
    lived: the structural predicate answers false BOTH for the tonal scorer and
    for a kind this build has never seen, and the old gate consumed that shared
    false to DECLINE an unknown kind -- reporting an unexecutable artifact as an
    ordinary "no structure here".
    """
    source = SELECTOR_SOURCE.read_text(encoding="utf-8")
    assert "Fl4683ScorerKindIsKnownV1" in source, (
        "the selector no longer distinguishes an unknown kind from a tonal one"
    )
    unknown_gate = source.index("if (!Fl4683ScorerKindIsKnownV1(fact.scorer_kind))")
    decline_gate = source.index(
        "if (fact.scorer_kind == FL4683_GLYPH_PROGRAM_SCORER_STRUCTURAL_DECLINE)\n\t\t{"
    )
    fence = source.index("!Fl4683ScorerOperandIsStructuralV1(fact.scorer_kind))")
    assert unknown_gate < decline_gate < fence, (
        "the unknown-scorer refusal must precede BOTH decline gates; ordered "
        "the other way an unrecognised kind is laundered into a decline"
    )


def test_the_structural_gate_still_precedes_the_operand_validity_check():
    """FL-4763's original ordering rule must survive the new gates above it."""
    source = SELECTOR_SOURCE.read_text(encoding="utf-8")
    fence = source.index("!Fl4683ScorerOperandIsStructuralV1(fact.scorer_kind))")
    validity = source.index("!scene_fact->harri_shape6_valid)\n\t\t\treturn Refuse(")
    assert fence < validity


def test_the_scorer_predicate_excludes_the_decline():
    """A decline names no operand at all, so it is not structural EVIDENCE.

    If it were folded into the structural predicate, the fence would let it
    through to the scorer instead of declining it.
    """
    probe = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
    source = probe.read_text(encoding="utf-8")
    body = source[source.index("bool Fl4683ScorerOperandIsStructuralV1"):]
    body = body[: body.index("}")]
    assert "STRUCTURAL_DECLINE" not in body


# ---------------------------------------------------------------------------
# Acceptance 7-8: the offline predictor mirrors the native rules
# ---------------------------------------------------------------------------
def test_the_predictor_knows_the_decline_kind():
    source = PREDICTOR_SOURCE.read_text(encoding="utf-8")
    assert "SCORER_KIND_STRUCTURAL_DECLINE = 4" in source
    assert DECLINE_SCORER_ID in source


def test_the_predictor_orders_its_gates_like_the_selector():
    """A rule living in only one of the two makes every FL-4678 exact-cell
    prediction disagree with the product it is supposed to predict, and the
    disagreement reads as a renderer defect rather than a stale mirror."""
    source = PREDICTOR_SOURCE.read_text(encoding="utf-8")
    unknown_gate = source.index("if kind not in SCORER_KINDS_KNOWN:")
    decline_gate = source.index("if kind == SCORER_KIND_STRUCTURAL_DECLINE:")
    fence = source.index("if family_bit & STRUCTURAL_FAMILY_MASK and kind not in (")
    validity = source.index(
        'if kind == SCORER_KIND_SHAPE6_WEIGHTED_L2 and not live["harri_valid"]:'
    )
    assert unknown_gate < decline_gate < fence < validity


def test_the_predictor_keeps_one_owner_for_the_unknown_refusal():
    """DELETE FIRST, mirrored: no second copy of the moved refusal may remain."""
    source = PREDICTOR_SOURCE.read_text(encoding="utf-8")
    assert source.count('return refuse("unsupported_compiled_scorer")') == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
