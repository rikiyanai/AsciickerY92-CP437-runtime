# =============================================================================
# scripts/tests/test_fl4763_structure_not_scored_from_tone.py
# =============================================================================
# FL-4763: structural evidence and tonal evidence are different things, and the
# compiled selector must not answer one with the other.
#
# WHAT WAS WRONG
# --------------
# The renderer builds SceneFactV1::harri_shape6_q0_16 in
# ComputeCellShape6FromSceneRgba (engine/render/render_resolve.cpp) by sampling
# r->pre_glyph_scene.rgba -- the compiler-owned pre-glyph TONAL plane. That
# vector describes how a cell was SHADED.
#
# The pre-FL-4763 compiled pool assigned the Harri shape6 scorer to three
# STRUCTURAL route families: edge.v1, direction.v1, and flow.v1. The product's
# selector was therefore deciding
# which glyph expresses a step, a compass orientation, or a flow axis by
# measuring the cell's tone. That is image-space line re-extraction from a
# colour raster -- the anti-pattern FL-4763 exists to remove -- and it
# manufactures confident structure everywhere the raster happens to vary.
#
# WHAT THE RULE IS NOW
# --------------------
# Compiler and selector rule 3d. Edge, direction, and flow rows carry the
# explicit operand-free decline kind. Mesh bundle retains its object-space U7
# evidence. The selector also keeps a defensive fence for a legacy structural
# fact that names the tonal Harri scorer. Either declining path may hand the
# cell to the lower-authority tonal pair.
#
# The decline is deliberately NOT a refusal and NOT a compiled absence. The
# address exists and answered; what is missing is evidence the renderer never
# promised to have. Declining keeps the cell drawable AND keeps the structural
# gap countable in policy_abstained_family_count/_mask, which is what makes the
# sparsity of real structural claims measurable instead of hidden.
#
# HOW THIS IS TESTED
# ------------------
# Same native harness recipe as scripts/tests/test_fl4512_phase2_native_policy.py
# -- its crafting, build, and row-driving helpers are imported rather than
# copied, so the two modules cannot drift apart. No product build is touched and
# no game is launched.
#
# The decisive image is new here: an edge fact with NO abstain flag and NO
# no-match bound. Under the Phase 2 rules alone such a row selects a glyph
# happily from any non-constant tonal operand, which is precisely the behaviour
# FL-4763 removes. The Phase 2 module's own image cannot show this, because it
# sets that fact's bound to 1 so every real operand declines through the bound
# path regardless.
#
# Run with:
#     python3 -m pytest scripts/tests/test_fl4763_structure_not_scored_from_tone.py -q
# =============================================================================

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List

import pytest

from scripts import compile_fl4512_pools as pool_compiler
from scripts import compile_fl4683_glyph_program as program_compiler
from scripts import predict_fl4678_exact_cell_glyph as predictor

from scripts.tests.test_fl4512_phase2_native_policy import (
    EDGE_KEY,
    FACT_RECORD_SIZE,
    FACT_SCORER_KIND,
    FAMILY_DENSITY,
    FAMILY_EDGE,
    FAMILY_MESH_BUNDLE,
    FAMILY_RAMP,
    OFF_FACT_TABLE_OFFSET,
    OUTCOME_SELECTED,
    ROOT,
    TONAL_LIVE_KEYS,
    TONAL_PAIR_MASK,
    U7_KEY,
    _build_harness,
    _run,
    base_image,
    fact_scorer_kind,
    finalize,
    parse_index,
    populated_fact_index,
    read_u32,
    set_fact_policy,
)

SELECTOR_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
PREDICTOR_SOURCE = ROOT / "scripts/predict_fl4678_exact_cell_glyph.py"

# Scorer kind codes, as in the compiled sidecar and the loader header.
SCORER_HARRI_SHAPE6_WEIGHTED_L2 = 1
SCORER_U7_MASK = 2
SCORER_STRUCTURAL_DECLINE = 4

# Format-v6 header/member geometry. A member record is glyph id (4), weight
# (2), the 12-byte scorer operand union, then reserved (2).
OFF_FACT_MEMBER_TABLE = 168
FACT_MEMBER_OFFSET = 96
FACT_MEMBER_COUNT = 100
FACT_MEMBER_SIZE = 20
FACT_MEMBER_OPERAND = 6

# A non-constant operand. Six alternating extremes cannot trip the
# degenerate-operand rule, so whatever this row does, it does for FL-4763's
# reason and not Phase 2's.
LIVE_TONAL_OPERAND = [65535, 0, 65535, 0, 65535, 0]

# The tonal fallback lanes the edge cell falls through to.
FALLBACK_RAMP_LANE = 2
FALLBACK_DENSITY_LANE = 1
# The committed terrain:4 edge address the structural rows below drive.
EDGE_LANE = EDGE_KEY[3]


def craft_unpoliced_structural_image() -> bytes:
    """An image whose structural edge fact carries NO Phase 2 policy at all.

    Both Phase 2 decline paths are switched off on that fact: no abstain flag,
    and an unbounded no-match distance. Before FL-4763 this row would select a
    glyph from any non-constant tonal operand. It is the only image that can
    tell the FL-4763 rule apart from the two rules that came before it.
    """
    blob = base_image()
    index = parse_index(blob)

    edge_fact = populated_fact_index(index, EDGE_KEY)
    # Before the guarded republication this fixture exercises the retained
    # legacy shape6 fence. After publication the same address carries the
    # explicit no-operand decline. Both must produce the same selector outcome
    # during the atomic source/artifact transition.
    assert fact_scorer_kind(blob, edge_fact) in (
        SCORER_HARRI_SHAPE6_WEIGHTED_L2,
        SCORER_STRUCTURAL_DECLINE,
    )
    set_fact_policy(blob, edge_fact, 0, 0)

    # The tonal pair is likewise unpoliced, so a cell that reaches it is
    # answered on the merits of the tonal operand alone.
    for key in TONAL_LIVE_KEYS:
        set_fact_policy(blob, populated_fact_index(index, key), 0, 0)

    return finalize(blob)


def craft_explicit_decline_image(*, malformed_operand: bool) -> bytes:
    """Convert the real edge address to kind 4 and control all operand bytes."""
    blob = base_image()
    index = parse_index(blob)
    fact_index = populated_fact_index(index, EDGE_KEY)
    fact_base = read_u32(blob, OFF_FACT_TABLE_OFFSET) + fact_index * FACT_RECORD_SIZE
    blob[fact_base + FACT_SCORER_KIND] = SCORER_STRUCTURAL_DECLINE
    set_fact_policy(blob, fact_index, 0, 0)

    first_member = read_u32(blob, fact_base + FACT_MEMBER_OFFSET)
    member_count = struct.unpack_from("<H", blob, fact_base + FACT_MEMBER_COUNT)[0]
    member_table = read_u32(blob, OFF_FACT_MEMBER_TABLE)
    for member_index in range(first_member, first_member + member_count):
        operand = member_table + member_index * FACT_MEMBER_SIZE + FACT_MEMBER_OPERAND
        blob[operand:operand + 12] = b"\x00" * 12
    if malformed_operand:
        first_operand = member_table + first_member * FACT_MEMBER_SIZE + FACT_MEMBER_OPERAND
        blob[first_operand] = 1
    return finalize(blob)


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    return _build_harness(tmp_path_factory.mktemp("fl4763-harness"))


@pytest.fixture(scope="module")
def image(tmp_path_factory) -> Path:
    written = tmp_path_factory.mktemp("fl4763-image") / "fl4683.glyph_program.v1.bin"
    written.write_bytes(craft_unpoliced_structural_image())
    return written


@pytest.fixture(scope="module")
def explicit_decline_images(tmp_path_factory) -> Dict[str, Path]:
    directory = tmp_path_factory.mktemp("fl4763-explicit-decline")
    paths = {}
    for name, malformed in (("valid", False), ("nonzero_operand", True)):
        path = directory / f"{name}.bin"
        path.write_bytes(craft_explicit_decline_image(malformed_operand=malformed))
        paths[name] = path
    return paths


def _select(harness: Path, image: Path, tmp_path: Path, row: str) -> Dict[str, int]:
    status, rows = _run(harness, image, [row], tmp_path)
    assert status in ("loaded", "loaded_without_local_relation_cache"), status
    assert len(rows) == 1, rows
    return rows[0]


def _terrain_row(mask: int, ramp: int, density: int, edge: int,
                 harri: List[int]) -> str:
    """One terrain material 4 SceneFact row in the harness wire format."""
    values = [2, 1, 1, 4, mask, ramp, density, 0, 0, edge, 1, *harri]
    return " ".join(str(v) for v in values) + " 0 0 0 0 0 0 0"


def _mesh_row(mesh_mask8_hex: str, harri: List[int]) -> str:
    """One mesh material 200 SceneFact row claiming the mesh bundle family.

    The Harri fields are filled in as well, deliberately: the point of the
    control below is that this family ignores them because its compiled scorer
    reads an object-space operand, not that they happen to be absent.
    """
    values = [2, 1, 2, 200, FAMILY_MESH_BUNDLE, 0, 0, 0, 0, 0, 1, *harri]
    return (" ".join(str(v) for v in values) +
            f" 1 {mesh_mask8_hex} 2 1 0 0 0")


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------
def test_a_structural_row_no_longer_wins_from_a_live_tonal_operand(
    harness, image, tmp_path
):
    """THE FL-4763 CASE.

    An edge cell, a real (non-constant) tonal operand, and an edge fact under no
    Phase 2 policy whatsoever. Before FL-4763 the edge family scored that
    operand and won: the selector returned SELECTED with winning_route_family_bit
    == edge, and a step's glyph had been chosen from the cell's shading.

    Now the edge family declines, the tonal pair answers, and the decline is
    reported. The cell is still drawn -- structure separated from tone is not the
    same thing as a hole in the image.
    """
    result = _select(
        harness, image, tmp_path,
        _terrain_row(FAMILY_EDGE, FALLBACK_RAMP_LANE, FALLBACK_DENSITY_LANE,
                     EDGE_LANE, LIVE_TONAL_OPERAND),
    )
    assert result["accepted"] == 1
    assert result["outcome"] == OUTCOME_SELECTED
    # The structural claim is recorded as declined, by name.
    assert result["abstained"] == 1
    assert result["abstain_mask"] == FAMILY_EDGE
    # And the answer came from the tonal pair on pass 1, not from the edge row.
    assert result["families"] == 2
    assert result["family_bit"] in (FAMILY_RAMP, FAMILY_DENSITY)
    assert result["family_bit"] != FAMILY_EDGE
    assert result["glyph"] > 0
    assert result["absent"] == 0


def test_the_mesh_bundle_family_still_wins_from_its_object_space_operand(
    harness, image, tmp_path
):
    """THE CONTROL THAT KEEPS THE RULE HONEST.

    Mesh bundle is a structural family too, and it is NOT disabled. Its compiled
    fact names the u7 mask scorer, whose operand is a projected mesh feature in
    object space -- real structural evidence -- so it selects normally, with a
    tonal vector present and ignored.

    Without this case, a rule that simply switched every structural family off
    would pass the test above just as well. This is what pins FL-4763 to the
    OPERAND's provenance rather than to a list of family names.
    """
    result = _select(
        harness, image, tmp_path, _mesh_row("00000000000000ff", LIVE_TONAL_OPERAND)
    )
    assert result["accepted"] == 1
    assert result["outcome"] == OUTCOME_SELECTED
    assert result["family_bit"] == FAMILY_MESH_BUNDLE
    assert result["abstained"] == 0
    assert result["glyph"] > 0


def test_loader_accepts_only_an_operand_free_explicit_decline(
    harness, explicit_decline_images, tmp_path
):
    valid = _select(
        harness,
        explicit_decline_images["valid"],
        tmp_path,
        _terrain_row(
            FAMILY_EDGE,
            FALLBACK_RAMP_LANE,
            FALLBACK_DENSITY_LANE,
            EDGE_LANE,
            LIVE_TONAL_OPERAND,
        ),
    )
    assert valid["outcome"] == OUTCOME_SELECTED
    assert valid["abstained"] == 1
    assert valid["abstain_mask"] == FAMILY_EDGE
    assert valid["family_bit"] in (FAMILY_RAMP, FAMILY_DENSITY)

    status, rows = _run(
        harness, explicit_decline_images["nonzero_operand"], [], tmp_path
    )
    assert status == "malformed_sidecar"
    assert rows == []


def test_the_tonal_pair_still_wins_from_the_tonal_operand(harness, image, tmp_path):
    """The other half of the separation: tone still answers tonal cells.

    FL-4763 scopes the pre-glyph raster to ramp and density; it does not retire
    it. A tonal cell with the same operand that made the edge row stand aside
    selects with no policy involvement at all.
    """
    result = _select(
        harness, image, tmp_path,
        _terrain_row(TONAL_PAIR_MASK, FALLBACK_RAMP_LANE, FALLBACK_DENSITY_LANE,
                     0, LIVE_TONAL_OPERAND),
    )
    assert result["accepted"] == 1
    assert result["outcome"] == OUTCOME_SELECTED
    assert result["abstained"] == 0
    assert result["families"] == 2
    assert result["family_bit"] in (FAMILY_RAMP, FAMILY_DENSITY)
    assert result["glyph"] > 0


# ---------------------------------------------------------------------------
# The two properties a refactor could silently break
# ---------------------------------------------------------------------------
def test_the_structural_gate_runs_before_the_operand_validity_check():
    """Order matters, and nothing else would notice if it changed.

    A structural family must decline whether or not a shape6 vector was
    published, because that vector was never its operand. If the gate were moved
    after the validity check, a renderer that correctly stopped publishing tone
    for structural rows would get MISSING_SCENE_DESCRIPTOR -- a refusal, a
    blanked cell -- instead of the intended decline. Both orderings compile and
    both pass every behavioural test above, because those rows all publish a
    valid vector. So the ordering is pinned here, on the source itself.
    """
    source = SELECTOR_SOURCE.read_text(encoding="utf-8")
    gate = source.index("Fl4683ScorerOperandIsStructuralV1(fact.scorer_kind)")
    validity = source.index("!scene_fact->harri_shape6_valid)\n\t\t\treturn Refuse(")
    assert gate < validity, (
        "the FL-4763 structural gate must precede the shape6 operand-validity "
        "refusal in Fl4683ExactCellSelectV1"
    )


def test_the_offline_predictor_carries_the_same_rule():
    """scripts/predict_fl4678_exact_cell_glyph.py replays the selector exactly.

    A rule that lives in only one of the two makes every FL-4678 exact-cell
    prediction disagree with the product it is supposed to predict, and the
    disagreement would read as a renderer defect rather than as a stale mirror.
    """
    source = PREDICTOR_SOURCE.read_text(encoding="utf-8")
    assert "FL-4763" in source, "the predictor has no FL-4763 rule at all"
    gate = source.index("if family_bit & STRUCTURAL_FAMILY_MASK and kind not in (")
    validity = source.index(
        'if kind == SCORER_KIND_SHAPE6_WEIGHTED_L2 and not live["harri_valid"]:'
    )
    assert gate < validity, (
        "the predictor's FL-4763 gate must sit before its operand-validity "
        "refusal, matching the native order"
    )


def test_compiler_assigns_every_role_to_an_explicit_evidence_owner():
    expected = {
        "tonal": pool_compiler.TONAL_SCORER_ID,
        "edge": pool_compiler.DECLINE_SCORER_ID,
        "direction": pool_compiler.DECLINE_SCORER_ID,
        "flow": pool_compiler.DECLINE_SCORER_ID,
        "mesh_feature": pool_compiler.U7_SCORER_ID,
        "filled": pool_compiler.FILLED_SCORER_ID,
    }
    assert {
        role: pool_compiler.row_scorer_id(role, f"test/{role}")
        for role in expected
    } == expected
    with pytest.raises(pool_compiler.CompileFailure, match="row_scorer_unassigned"):
        pool_compiler.row_scorer_id("future_role", "test/future")


def test_declining_rows_and_program_members_carry_no_scorer_operand():
    row = pool_compiler.make_row(
        "terrain", 1, "edge", "edge.v1", 0, "edge[0]", [300], {300}, {}
    )
    assert row["scorer_id"] == pool_compiler.DECLINE_SCORER_ID
    assert row["candidate_descriptors"] == [{"glyph_id": 300}]
    assert program_compiler.encode_member_scorer_operand(
        program_compiler.SCORER_KIND_STRUCTURAL_DECLINE, {}, {}, 0, 300
    ) == b"\x00" * 12

    fact = {
        "fact_id": "fact://decline",
        "source_profile_id": "profile://test",
        "scene_membership_key": {
            "profile_namespace": "terrain",
            "material_key": 1,
            "route_key": "edge.v1",
            "row_sha256": "row",
            "scorer_id": pool_compiler.DECLINE_SCORER_ID,
        },
        "glyph_members": [
            {"glyph_record_id": 300, "membership_weight_q0_16": 65535}
        ],
    }
    rebuilt = predictor.build_fact_program(
        fact,
        "edge.v1",
        {"profile://test": (1, 1, 1, 1, 1, 1)},
        {"row": {300: {"glyph_id": 300}}},
        {("terrain", 1): {}},
        {},
    )
    assert rebuilt["scorer_kind"] == SCORER_STRUCTURAL_DECLINE
    assert rebuilt["threshold_u32"] == 0
    assert rebuilt["members"] == [{"glyph_record_id": 300, "weight": 65535}]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
    OFF_FACT_TABLE_OFFSET,
