# =============================================================================
# scripts/tests/test_fl4762_flat_routes_tonal.py
# =============================================================================
# FL-4762: a FLAT vertical relation must not claim the compiled EDGE route.
#
# WHAT WENT WRONG
# ---------------
# `vertical_relation` reports the step between two stacked surfaces. FLAT (0)
# means "there is no step". The live route owner nevertheless accepted the
# whole range FLAT..OVERHANG into the edge family, so flat ground was sent to
# edge lane 0 and painted with a structural hatch. At the fixed-pose canonical
# Pieta the FL-4512 phase1f bridge dump measured this on 488 of 641 eligible
# material-1 cells.
#
# WHY THIS TEST EXISTS AT ALL
# ---------------------------
# The same two-sided comparison had been COPIED into six places: the live
# owner, its debug replay, the SceneFact role classifier, the SceneFact route
# mask, the terminal bridge dump, and the editor trace diagram. Copies drift.
# When FL-4762 was implemented, not one existing test failed -- nothing pinned
# the rule -- which is precisely how a route owner and its five reporters can
# disagree without anyone noticing.
#
# So this file pins two different things:
#
#   1. THE RULE, executably. The shared predicate is compiled and run, and its
#      full truth table over every enum value is asserted. This is the
#      falsifier: if FLAT is ever readmitted to the edge family, this fails.
#   2. THE SINGLE OWNER. Every route site must reach the rule THROUGH that one
#      predicate, and no route site may open-code the bounds again.
#
# WHAT THIS TEST DOES NOT CLAIM
# -----------------------------
# Nothing here observes the product. It does not build the game, launch it,
# render a frame, or claim any pixel changed. It pins a source-level routing
# rule only. The visible consequence at the canonical Pieta pose is a separate,
# later, runtime-owned proof.
#
# Run with:
#     python3 -m pytest scripts/tests/test_fl4762_flat_routes_tonal.py -q
# =============================================================================

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The header that owns the enum AND the predicate. It is deliberately
# self-contained enough to include on its own, which is what lets the rule be
# tested by execution instead of by reading the source.
ROUTE_RULE_HEADER = ROOT / "engine/render/render_frame_input.h"

# The one predicate every route site must call.
PREDICATE = "Fl4260VerticalRelationClaimsEdgeRouteV1"

# FL-4762 second pass. The same centralisation for the two compass families.
# The first pass fixed only the vertical-relation bound, which left the ROLE
# classifier testing the DIRECTION and FLOW bits alone while the ROUTE mask
# tested each bit AND its range -- the identical divergence, still open.
DIRECTION_PREDICATE = "Fl4260DirectionClaimsDirectionRouteV1"
FLOW_PREDICATE = "Fl4260FlowClaimsFlowRouteV1"

# Every site that decides, replays, or REPORTS which route family a cell takes,
# with the exact number of calls to each predicate.
# A reporter is included on purpose: a diagnostic that disagrees with the owner
# is not a harmless inaccuracy, it is a tool that lies about the product.
# render_resolve.cpp carries extra compass calls because the FACT PRODUCER also
# gates on them, so "bit set" and "index admissible" cannot drift apart.
ROUTE_SITES = {
    # file: (vertical, direction, flow)
    "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp": (2, 2, 2),  # owner + replay
    "engine/render/render_resolve.cpp": (2, 4, 3),          # role + mask + producer
    "platform/terminal_gl_present.cpp": (1, 1, 1),          # canonical tuple dump
    "apps/asciicker-presenter/asciiid.cpp": (2, 2, 2),                        # diagram + pool preview
}

# Open-coded compass range conjunctions still permitted, with the reason.
# asciiid.cpp keeps exactly one: Fl4260DirectionLaneProvenForUi answers a
# DIFFERENT question (may this bin be saved without the blocked stamp) and
# mirrors Fl4260EdgeLaneProvenForUi, which deliberately holds its own range.
# Folding it into the routing predicate would conflate authoring with routing.
COMPASS_OPEN_CODE_ALLOWANCE = {
    "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp": 0,
    "engine/render/render_resolve.cpp": 0,
    "platform/terminal_gl_present.cpp": 0,
    "apps/asciicker-presenter/asciiid.cpp": 1,
}

OPEN_CODED_COMPASS_RANGE = re.compile(
    r"FL4260_(?:DIRECTION|FLOW)_N\b[^\n]*&&"
)

# The open-coded shape that FL-4762 removed. Written as a regex over the two
# adjacent comparisons so it cannot match the editor's legitimate AUTHORING
# loops, which iterate `for (vr = FLAT; vr <= OVERHANG; vr++)` and validate
# `if (vr < FLAT || vr > OVERHANG)`. Those keep the FLAT lane authorable; they
# just no longer route to it.
OPEN_CODED_ROUTE_RANGE = re.compile(
    r">=\s*FL4260_VERTICAL_RELATION_FLAT\s*&&\s*\n?\s*"
    r"[A-Za-z_][A-Za-z0-9_]*\s*<=\s*FL4260_VERTICAL_RELATION_OVERHANG"
)

# The first consolidation test only recognized symbolic enum comparisons. The
# retired GPU winner and three reports instead spelled the same rule with raw
# numeric bounds, which let a second owner survive while this suite stayed
# green. Numeric route decisions are forbidden on the reviewed route surfaces.
OPEN_CODED_NUMERIC_ROUTE_RANGE = re.compile(
    r"(?:p?vr|vertical_relation)\s*>=\s*0\s*&&\s*"
    r"(?:p?vr|vertical_relation)\s*<\s*7"
)

RETIRED_GPU_WINNER_TOKENS = (
    "harri_winner_fs",
    "harri_u3_pack_fs",
    "harri_winner_prg",
    "harri_winner_gid_tex",
    "HarriAttachWinnerMRT",
    "HarriReplayGpuScan",
    "Fl4512MaterializeFinalGlyphResults",
    "gpu_sidecar_ready",
    "gpu winner pass",
)

# The enum, and what each value must answer. RISE..OVERHANG describe a real
# step and stay structural; everything else -- including the out-of-range
# values a producer can emit when it has nothing to say -- must not claim edge.
TRUTH_TABLE = (
    (-1, False, "UNKNOWN: the producer reported nothing"),
    (0, False, "FLAT: no step between surfaces, so no structure to draw"),
    (1, True, "RISE"),
    (2, True, "FALL"),
    (3, True, "RIDGE"),
    (4, True, "VALLEY"),
    (5, True, "WALL"),
    (6, True, "OVERHANG"),
    (7, False, "past the end of the compiled edge lanes"),
    (8, False, "further past the end"),
    (-99, False, "far out of range"),
    (99, False, "far out of range"),
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def predicate_answers(tmp_path_factory) -> dict[int, bool]:
    """Compile the real header and ask the real predicate for every value.

    This is an execution check, not a source check: it links against the actual
    shipped definition, so a future edit that changes the bound cannot satisfy
    this test by leaving the source text looking right.
    """
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("no C++ compiler available to execute the route predicate")

    work = tmp_path_factory.mktemp("fl4762")
    source = work / "probe.cpp"
    values = ", ".join(str(value) for value, _, _ in TRUTH_TABLE)
    source.write_text(
        textwrap.dedent(
            f"""
            #include "render/render_frame_input.h"
            #include <cstdio>

            int main()
            {{
                const int values[] = {{ {values} }};
                for (int i = 0; i < (int)(sizeof(values) / sizeof(values[0])); i++)
                    printf("%d %d %d %d\\n", values[i],
                           {PREDICATE}(values[i]) ? 1 : 0,
                           {DIRECTION_PREDICATE}(values[i]) ? 1 : 0,
                           {FLOW_PREDICATE}(values[i]) ? 1 : 0);
                return 0;
            }}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    binary = work / "probe"
    build = subprocess.run(
        [
            compiler,
            "-std=c++20",
            f"-I{ROOT}",
            f"-I{ROOT / 'engine'}",
            f"-I{ROOT / 'engine/render'}",
            str(source),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr

    answers: dict[int, tuple[bool, bool, bool]] = {}
    for line in run.stdout.split("\n"):
        if not line.strip():
            continue
        value, vertical, direction, flow = line.split()
        answers[int(value)] = (vertical == "1", direction == "1", flow == "1")
    return answers


@pytest.mark.parametrize("value,claims_edge,why", TRUTH_TABLE)
def test_predicate_truth_table(predicate_answers, value, claims_edge, why):
    """FLAT never claims the edge family; only a real step does."""
    assert predicate_answers[value][0] is claims_edge, why


# The compass families admit their full authored lane set, 0..7. FL-4762's
# second pass changed WHERE the range is tested, not what it admits, so this
# table is the unchanged authored range stated explicitly.
COMPASS_TRUTH_TABLE = (
    (-99, False, "far out of range"),
    (-1, False, "UNKNOWN: the producer reported nothing"),
    (0, True, "N"),
    (7, True, "NW"),
    (8, False, "past the last authored compass lane"),
    (99, False, "far out of range"),
)


@pytest.mark.parametrize("value,claims,why", COMPASS_TRUTH_TABLE)
def test_direction_predicate_truth_table(predicate_answers, value, claims, why):
    """An out-of-range compass index claims no route, in role AND in mask."""
    assert predicate_answers[value][1] is claims, why


@pytest.mark.parametrize("value,claims,why", COMPASS_TRUTH_TABLE)
def test_flow_predicate_truth_table(predicate_answers, value, claims, why):
    assert predicate_answers[value][2] is claims, why


def test_the_rule_has_exactly_one_owner():
    """The predicate is defined once, in the header both sides already include."""
    header = ROUTE_RULE_HEADER.read_text(encoding="utf-8")
    assert f"int {PREDICATE}(" in header
    # The lower bound is RISE, not FLAT. Stated explicitly so that a silent
    # revert of just this bound is a named failure rather than a mystery.
    definition = header.split(f"int {PREDICATE}(", 1)[1]
    assert "FL4260_VERTICAL_RELATION_RISE" in definition.split("}", 1)[0]


@pytest.mark.parametrize("relative,expected", sorted(ROUTE_SITES.items()))
def test_every_route_site_calls_the_shared_predicates(relative, expected):
    """Owner, replay, role, route mask, dump, and diagram share one rule each."""
    body = _read(relative)
    got = (
        body.count(f"{PREDICATE}("),
        body.count(f"{DIRECTION_PREDICATE}("),
        body.count(f"{FLOW_PREDICATE}("),
    )
    assert got == expected, (
        f"{relative} calls (vertical, direction, flow) {got} times, expected "
        f"{expected}; a route site was added or removed without updating this "
        "contract"
    )


@pytest.mark.parametrize("relative,allowed", sorted(COMPASS_OPEN_CODE_ALLOWANCE.items()))
def test_compass_ranges_are_not_open_coded_in_route_files(relative, allowed):
    """Direction and flow ranges must come from their predicates too."""
    found = len(OPEN_CODED_COMPASS_RANGE.findall(_read(relative)))
    assert found == allowed, (
        f"{relative} open-codes a compass range {found} times, expected "
        f"{allowed}; call {DIRECTION_PREDICATE}/{FLOW_PREDICATE} instead"
    )


@pytest.mark.parametrize("relative", sorted(ROUTE_SITES))
def test_no_route_site_open_codes_the_bounds_again(relative):
    """Re-inlining the range is how the six copies drifted apart before."""
    match = OPEN_CODED_ROUTE_RANGE.search(_read(relative))
    assert match is None, (
        f"{relative} open-codes the edge range again at offset {match.start() if match else -1}; "
        f"call {PREDICATE} instead"
    )

    numeric = OPEN_CODED_NUMERIC_ROUTE_RANGE.search(_read(relative))
    assert numeric is None, (
        f"{relative} open-codes the numeric edge range at offset "
        f"{numeric.start() if numeric else -1}; call {PREDICATE} instead"
    )


def test_shipping_presenter_has_no_retired_gpu_glyph_selector():
    """RuntimeFinalTupleOwner is the only native/Web glyph-selection owner."""
    presenter = _read("platform/terminal_gl_present.cpp")
    for token in RETIRED_GPU_WINNER_TOKENS:
        assert token not in presenter, f"retired GPU winner surface survived: {token}"
    assert "Fl4693PresentCommittedTupleV1(" in presenter
    assert "Fl4693ReadProfileTupleV1(" in presenter


def test_role_and_route_read_the_same_operands():
    """The role classifier takes every operand the route mask takes.

    This is the defect FL-4762 closed in two passes. The SceneFact ROLE
    classifier used to test bare fact BITS while the ROUTE mask tested each bit
    AND its index range, so the two could describe the same cell differently.
    The first pass fixed only vertical_relation; direction and flow stayed
    divergent until the second. If a future edit drops an operand from this
    signature, that divergence comes straight back, so the signature and its
    single call site are both pinned here.
    """
    body = _read("engine/render/render_resolve.cpp")
    assert (
        "Fl4683RoleFromFactsV1(\n"
        "    uint8_t fact_mask, int vertical_relation, int direction, int flow)"
    ) in body
    assert (
        "Fl4683RoleFromFactsV1(\n"
        "        in.fact_mask, in.vertical_relation_idx, in.direction_idx, in.flow_idx)"
    ) in body


def test_no_tool_pins_the_retired_single_operand_signature():
    """No analyzer may anchor itself to the pre-FL-4762 signature text.

    Widening the classifier broke two string pins that anchored on the OLD
    single-argument spelling: one in scripts/tests/test_fl4684_compiled_address
    _reachability.py and one in the FL-4684 measurement instrument itself. Both
    raised ValueError rather than returning a verdict, so the role-byte
    agreement measurement reported nothing at all while still looking like an
    ordinary test failure. An instrument that cannot read the source is not a
    failing measurement, it is an ABSENT one, and that distinction is the whole
    reason this guard exists. Pin the declarator, never the parameter list.
    """
    retired = "Fl4683RoleFromFactsV1(uint8_t fact_mask)"
    offenders = []
    for path in sorted(ROOT.glob("scripts/**/*.py")):
        if path.name == Path(__file__).name:
            continue
        if retired in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders
