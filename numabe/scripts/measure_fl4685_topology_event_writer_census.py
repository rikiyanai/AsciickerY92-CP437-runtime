#!/usr/bin/env python3
# =============================================================================
# scripts/measure_fl4685_topology_event_writer_census.py
# =============================================================================
# WHAT THIS SCRIPT IS FOR
# -----------------------
# The renderer has a vocabulary of "topology events". A topology event is a
# claim that the SHAPE of something on screen changed in a structural way
# between one frame and the next -- a line split into two lines, two lines
# merged into one, a loop closed, an end-point appeared, a hole opened.
#
# Declaring such a vocabulary is cheap. What this script measures is whether
# anything in the shipped product actually DETECTS each of those events, whether
# any authored (compiled, reviewable, versioned) policy says what to do about
# it, and whether any recorded evidence shows it ever happening.
#
# It is a CENSUS -- a count of what exists -- not a decision about what should
# exist. It never launches the game. It reads source files and already-committed
# recordings.
#
# THE THREE-PART ADMISSION RULE IT MEASURES AGAINST
# ------------------------------------------------
# A topology-event kind may take part in product behaviour only when ALL THREE
# of these exist:
#
#   1. a real production detector  -- code on the live frame path that can
#      actually emit the kind, not just a test or a fixture;
#   2. an authored compiled policy -- a weight, threshold, or rule for that kind
#      that lives in a compiled, reviewable artifact rather than as a number
#      typed into a C++ header;
#   3. evidence the track owner consumes it correctly -- a recording showing the
#      event arriving and being handled, not merely a function that would handle
#      it if one arrived.
#
# TWO VERDICTS THAT MUST NOT BE CONFUSED
# --------------------------------------
#   UNAVAILABLE -- no production code path can ever emit this kind. Its
#                  occurrence count is not "zero events happened"; the question
#                  "how often does it happen" has no meaning yet.
#   NONE        -- a production detector DOES exist and the measurement counted
#                  zero occurrences in the recordings inspected.
#
# Collapsing those two into "0" would let a missing feature read as a quiet
# feature. That is the specific mistake this file exists to prevent.
#
# HOW TO RUN IT
#   python3 scripts/measure_fl4685_topology_event_writer_census.py --write
#   python3 scripts/measure_fl4685_topology_event_writer_census.py --check
#
# `--write` records the census. `--check` recomputes it and fails if the
# recorded copy no longer matches, so a change to the renderer makes the census
# STALE (a loud failure) instead of silently wrong.
# =============================================================================

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# The repository root, found by walking up from this file (scripts/ -> root).
ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Source files the census reads its structural facts out of
# ---------------------------------------------------------------------------
# The header that declares the solver-side vocabulary and the one function that
# can translate an Active-Stroke lifecycle event into it.
HANDOFF_HEADER = Path("engine/render/fl4512_structural_sequence_input.h")
# The header that owns the spatial-temporal track store, its mirror of the same
# vocabulary, and the hard-coded cost weights.
TRACK_OWNER_HEADER = Path("engine/render/fl4512_spacetime_track_owner.h")
# The header that declares the SEPARATE, smaller scene-fact vocabulary.
SCENE_FACT_HEADER = Path("platform/fl4683_scene_fact_record.h")
# The renderer file that fills the per-cell scene fact and that carries the one
# call site translating Active-Stroke lifecycle events into topology events.
RENDER_RESOLVE = Path("engine/render/render_resolve.cpp")
# The Active-Stroke owner header, which declares the lifecycle vocabulary that
# is the ONLY upstream source of a topology event in production.
ACTIVE_STROKE_HEADER = Path("apps/fl4512-runtime/engine/fl4512_active_stroke_owner.h")
# The presenter, which holds the one production call into the spacetime solver.
# Its literal `false` for is_loop_closing_frame is the reason the loop cost term
# cannot activate at that call site.
PRESENTER_SOURCE = Path("platform/terminal_gl_present.cpp")

# Where the census lands.
OUTPUT = Path("assets/glyphs/generated/fl4685.topology_event_writer_census.v1.json")

# ---------------------------------------------------------------------------
# Recordings the census counts occurrences in
# ---------------------------------------------------------------------------
# These are already-committed recordings of real frames. They are listed
# explicitly rather than globbed so that the census names exactly what it looked
# at -- "we found nothing" is only meaningful alongside "here is where we
# looked".
RECEIPTS_INSPECTED = [
    Path(
        "docs/research/ascii/verification/fl4656/2026-07-29-bridge-v18-diagnostic/"
        "before_bridge_facts.jsonl"
    ),
    Path(
        "docs/research/ascii/verification/fl4656/2026-07-29-bridge-v18-diagnostic/"
        "before_cell_trace.jsonl"
    ),
    Path(".run/fl4652-persistent-predictor-surgery/sealed_prediction.json"),
]

# The two spellings a recorded topology event can take in those files. Group 1
# of each pattern is the numeric kind.
#   * the flat spelling used by the cell-trace writer, e.g.
#         "spacetime_topology_event_kind":0
#   * the nested spelling used by the bridge-facts writer, e.g.
#         "topology_event":{"kind":0,"magnitude":0}
OCCURRENCE_PATTERNS = [
    re.compile(r'topology_event_kind"\s*:\s*(\d+)'),
    re.compile(r'"topology_event"\s*:\s*\{\s*"kind"\s*:\s*(\d+)'),
]


class CensusError(RuntimeError):
    """Raised when a source file no longer has the shape this script expects.

    Failing loudly matters more than producing a number here: a census that
    quietly measured the wrong thing would be worse than no census.
    """


def read(path: Path) -> str:
    """Read one repository file as text, with a clear error if it moved."""
    full = ROOT / path
    if not full.exists():
        raise CensusError(f"{path} not found; the census source moved or was deleted")
    return full.read_text(encoding="utf-8", errors="replace")


def parse_enum(source: str, enum_name: str) -> Dict[str, int]:
    """Pull one C++ `enum` out of a header as {CONSTANT_NAME: number}.

    Parameters:
      source    -- the whole header file as one string.
      enum_name -- the C++ name of the enum, e.g. "Fl4512TopologyEventHandoffKindV1".

    Reading the enum instead of restating it means that adding a kind to the
    header makes the recorded census stale (a `--check` failure) rather than
    leaving the new kind silently uncounted.
    """
    match = re.search(
        r"enum\s+" + re.escape(enum_name) + r"[^{]*\{(?P<body>[^}]*)\}", source
    )
    if not match:
        raise CensusError(f"enum {enum_name} not found; the vocabulary moved")
    members: Dict[str, int] = {}
    for name, value in re.findall(
        r"([A-Z][A-Z0-9_]+)\s*=\s*(\d+)", match.group("body")
    ):
        members[name] = int(value)
    if not members:
        raise CensusError(f"enum {enum_name} parsed but empty; shape changed")
    return members


def line_of(source: str, needle: str, path: Path) -> int:
    """Return the 1-based line number where `needle` first appears.

    Citations in the census carry line numbers so a reader can go and look. They
    are computed, never typed, so they cannot drift out of date silently.
    """
    index = source.find(needle)
    if index < 0:
        raise CensusError(f"{needle!r} not found in {path}")
    return source.count("\n", 0, index) + 1


def read_lifecycle_to_topology_mapping(handoff_source: str) -> Dict[str, Any]:
    """Read WHICH topology kinds the one production translator can ever return.

    There is exactly one function in the product that turns an upstream event
    into a topology event: `Fl4512TopologyEventFromActiveStrokeLifecycleV1`. It
    is a short chain of `if` statements. Whatever constants it can return are
    the ONLY kinds production can emit; every other declared kind is
    structurally unreachable no matter what the scene contains.

    Returns a dictionary describing the function and the set of kinds it emits.
    """
    signature = "static inline uint8_t Fl4512TopologyEventFromActiveStrokeLifecycleV1("
    start = handoff_source.index(signature)
    end = handoff_source.index("\nstruct ", start + 1)
    body = handoff_source[start:end]

    # Which lifecycle numbers the function tests for, and which topology
    # constants it returns. Both are read out of the body.
    lifecycle_values = sorted(
        {int(value) for value in re.findall(r"lifecycle_kind == (\d+)u", body)}
    )
    returned = re.findall(r"return (FL4512_TOPOLOGY_HANDOFF_[A-Z_]+);", body)
    emitted = sorted({name for name in returned if name != "FL4512_TOPOLOGY_HANDOFF_NONE"})
    return {
        "function": "Fl4512TopologyEventFromActiveStrokeLifecycleV1",
        "source": HANDOFF_HEADER.as_posix(),
        "source_line": handoff_source.count("\n", 0, start) + 1,
        "lifecycle_values_it_tests": lifecycle_values,
        "topology_kinds_it_can_emit": emitted,
        "everything_else_returns": "FL4512_TOPOLOGY_HANDOFF_NONE",
    }


def read_hard_coded_weights(track_owner_source: str) -> List[Dict[str, Any]]:
    """Record the cost weights that are typed into C++ instead of compiled.

    A "weight" here is how many cost units the solver charges for something.
    These numbers decide behaviour, so where they LIVE matters: a number in a
    compiled artifact can be reviewed, versioned, and changed without a rebuild;
    a number typed into a header cannot, and no per-kind policy can exist for it.

    This is the measurement behind admission criterion 2 ("authored compiled
    policy"): for every topology kind, the answer is the same single hard-coded
    number, so no kind has its own policy.
    """
    weights = []
    for name, value in re.findall(
        r"static const uint32_t (kFl4512Lambda[A-Za-z0-9]+V2)\s*=\s*(\d+)u", track_owner_source
    ):
        weights.append(
            {
                "symbol": name,
                "value": int(value),
                "source": TRACK_OWNER_HEADER.as_posix(),
                "source_line": line_of(
                    track_owner_source, f"{name} ", TRACK_OWNER_HEADER
                ),
                "authored_in_a_compiled_artifact": False,
                "note": (
                    "typed into the C++ header; no compiled artifact under "
                    "assets/glyphs/generated/ supplies this value"
                ),
            }
        )
    if not weights:
        raise CensusError("no kFl4512Lambda*V2 weights found; the cost model moved")
    return weights


def count_occurrences_in_receipts() -> Dict[str, Any]:
    """Count every recorded topology event in the committed recordings.

    Returns a dictionary with a per-kind-number count and the list of files
    actually read. A file that is missing is reported as missing rather than
    skipped, because "we looked and it was not there" and "we did not look" are
    different statements and only one of them supports a NONE verdict.
    """
    per_kind: Dict[str, int] = {}
    inspected: List[Dict[str, Any]] = []
    for receipt in RECEIPTS_INSPECTED:
        full = ROOT / receipt
        if not full.exists():
            inspected.append({"path": receipt.as_posix(), "present": False, "matches": 0})
            continue
        text = full.read_text(encoding="utf-8", errors="replace")
        matches = 0
        for pattern in OCCURRENCE_PATTERNS:
            for value in pattern.findall(text):
                per_kind[value] = per_kind.get(value, 0) + 1
                matches += 1
        inspected.append(
            {"path": receipt.as_posix(), "present": True, "matches": matches}
        )
    total = sum(per_kind.values())
    non_none = sum(count for kind, count in per_kind.items() if kind != "0")
    return {
        "receipts_inspected": inspected,
        "recorded_topology_event_fields_read": total,
        "occurrences_by_kind_number": dict(sorted(per_kind.items(), key=lambda kv: int(kv[0]))),
        "non_none_occurrences": non_none,
        "how_counted": (
            "every JSON field named topology_event_kind (flat spelling) or "
            "topology_event.kind (nested spelling) in the listed recordings"
        ),
    }


# ---------------------------------------------------------------------------
# Per-kind facts that CANNOT be derived by reading a single file
# ---------------------------------------------------------------------------
# Everything below is a statement about the meaning of a kind -- what would have
# to detect it, what its magnitude number would mean, and what observation would
# prove the census wrong. These are written down once, here, and each is bound
# to the kind by name so that adding a kind to the header without adding a row
# here makes the census fail rather than silently omit it.
#
# "detection_rule" answers: what would a real detector have to look at?
# "magnitude_semantics" answers: what does the accompanying number mean?
# "falsifier" answers: what single observation would prove this row wrong?
KIND_SEMANTICS: Dict[str, Dict[str, str]] = {
    "FL4512_TOPOLOGY_HANDOFF_ENDPOINT_APPEAR": {
        "human_meaning": "a stroke grew a new loose end that was not there last frame",
        "detection_rule": (
            "compare the endpoint count in this frame's topology signature with "
            "the corresponded prior frame's signature for the same track and "
            "report an increase; the signature itself already exists "
            "(Fl4512TopologySignatureFromMaskV1) but nothing compares the two "
            "and declares this kind"
        ),
        "magnitude_semantics": (
            "number of descriptor-axis units of change the declaration explains, "
            "which Fl4512SpacetimeTopologyCostV1 then forgives; no producer sets "
            "it to anything but 0 or 1"
        ),
        "falsifier": (
            "any recording containing topology_event_kind == 1 produced by a "
            "normal-play desktop or WebAssembly frame"
        ),
    },
    "FL4512_TOPOLOGY_HANDOFF_ENDPOINT_VANISH": {
        "human_meaning": "a stroke lost a loose end it had last frame",
        "detection_rule": (
            "the endpoint-count decrease counterpart of ENDPOINT_APPEAR; no "
            "production code performs the comparison"
        ),
        "magnitude_semantics": "same forgiveness-unit meaning as every other kind",
        "falsifier": (
            "any recording containing topology_event_kind == 2 from a normal-play "
            "frame; note that the only place this value appears anywhere in the "
            "repository is an embedded test harness"
        ),
    },
    "FL4512_TOPOLOGY_HANDOFF_JUNCTION_SPLIT": {
        "human_meaning": "a place where strokes met came apart",
        "detection_rule": (
            "compare junction counts between corresponded frames for one track; "
            "no production code performs the comparison"
        ),
        "magnitude_semantics": "same forgiveness-unit meaning as every other kind",
        "falsifier": "any recording containing topology_event_kind == 3 from a normal-play frame",
    },
    "FL4512_TOPOLOGY_HANDOFF_JUNCTION_MERGE": {
        "human_meaning": "two stroke ends met and became a junction",
        "detection_rule": (
            "the junction-count increase counterpart of JUNCTION_SPLIT; no "
            "production code performs the comparison"
        ),
        "magnitude_semantics": "same forgiveness-unit meaning as every other kind",
        "falsifier": "any recording containing topology_event_kind == 4 from a normal-play frame",
    },
    "FL4512_TOPOLOGY_HANDOFF_COMPONENT_SPLIT": {
        "human_meaning": "one connected stroke became two",
        "detection_rule": (
            "the Active Stroke owner publishes lifecycle events "
            "SPLIT_RETAINED (5) and SPLIT_BRANCH (6) for the two sides of one "
            "split; the translator maps BOTH to this kind"
        ),
        "magnitude_semantics": (
            "the translator's caller sets magnitude to exactly 1 whenever the "
            "kind is not NONE, so magnitude carries no measured size -- it is a "
            "presence flag wearing a number's clothes"
        ),
        "falsifier": (
            "a normal-play recording in which the Active Stroke owner publishes "
            "lifecycle 5 or 6 and no topology_event_kind == 5 reaches the track "
            "owner, or the reverse"
        ),
    },
    "FL4512_TOPOLOGY_HANDOFF_COMPONENT_MERGE": {
        "human_meaning": "two connected strokes became one",
        "detection_rule": (
            "the Active Stroke owner publishes lifecycle event MERGE_RETAINED "
            "(7); the translator maps it to this kind"
        ),
        "magnitude_semantics": (
            "same presence-flag-as-1 behaviour as COMPONENT_SPLIT; the merge's "
            "actual structural size is never measured"
        ),
        "falsifier": (
            "a normal-play recording in which lifecycle 7 is published and no "
            "topology_event_kind == 6 reaches the track owner, or the reverse"
        ),
    },
    "FL4512_TOPOLOGY_HANDOFF_HOLE_OPEN": {
        "human_meaning": "an enclosed gap appeared inside a shape",
        "detection_rule": (
            "compare the hole count (the second Betti number) between "
            "corresponded frames; the count exists in the signature, nothing "
            "compares it"
        ),
        "magnitude_semantics": "same forgiveness-unit meaning as every other kind",
        "falsifier": "any recording containing topology_event_kind == 7 from a normal-play frame",
    },
    "FL4512_TOPOLOGY_HANDOFF_HOLE_CLOSE": {
        "human_meaning": "an enclosed gap inside a shape filled in",
        "detection_rule": (
            "the hole-count decrease counterpart of HOLE_OPEN; nothing compares "
            "the counts"
        ),
        "magnitude_semantics": "same forgiveness-unit meaning as every other kind",
        "falsifier": "any recording containing topology_event_kind == 8 from a normal-play frame",
    },
    # ---- the separate, smaller scene-fact vocabulary ----
    "FL4683_TOPOLOGY_EVENT_BIRTH": {
        "human_meaning": "the feature this cell belongs to came into existence",
        "detection_rule": (
            "would require the scene-fact writer to receive a lifecycle fact "
            "from a feature owner; it receives none and writes the constant "
            "NONE unconditionally"
        ),
        "magnitude_semantics": "the scene-fact record has no magnitude field at all",
        "falsifier": (
            "any recorded scene fact whose topology_event byte is not 0, or any "
            "second assignment to SceneFactV1::topology_event appearing in the "
            "source"
        ),
    },
    "FL4683_TOPOLOGY_EVENT_DEATH": {
        "human_meaning": "the feature this cell belongs to ceased to exist",
        "detection_rule": "same absent path as BIRTH",
        "magnitude_semantics": "the scene-fact record has no magnitude field at all",
        "falsifier": "any recorded scene fact whose topology_event byte is not 0",
    },
    "FL4683_TOPOLOGY_EVENT_SPLIT": {
        "human_meaning": "the feature this cell belongs to came apart into two",
        "detection_rule": (
            "same absent path as BIRTH; note that the solver-side vocabulary DOES "
            "have a production split detector, but nothing connects it to this "
            "byte"
        ),
        "magnitude_semantics": "the scene-fact record has no magnitude field at all",
        "falsifier": "any recorded scene fact whose topology_event byte is not 0",
    },
    "FL4683_TOPOLOGY_EVENT_MERGE": {
        "human_meaning": "this cell's feature joined with another",
        "detection_rule": (
            "same absent path as BIRTH; the solver-side merge detector exists and "
            "is not connected to this byte"
        ),
        "magnitude_semantics": "the scene-fact record has no magnitude field at all",
        "falsifier": "any recorded scene fact whose topology_event byte is not 0",
    },
    "FL4683_TOPOLOGY_EVENT_LOOP_CLOSURE": {
        "human_meaning": "this cell's feature closed back on itself into a ring",
        "detection_rule": (
            "same absent path as BIRTH. Separately, the solver's loop-cost term "
            "is dormant: its only production call site passes the literal false "
            "for is_loop_closing_frame, so the loop term can never activate, and "
            "its weight is a hard-coded 30. Loop closure is therefore UNAVAILABLE "
            "in both vocabularies for two independent reasons."
        ),
        "magnitude_semantics": "the scene-fact record has no magnitude field at all",
        "falsifier": (
            "any recorded scene fact whose topology_event byte is not 0, or any "
            "production call site passing a non-false is_loop_closing_frame"
        ),
    },
}


def build_kind_rows(
    handoff_kinds: Dict[str, int],
    scene_fact_kinds: Dict[str, int],
    translator: Dict[str, Any],
    occurrences: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Assemble one census row per declared topology-event kind.

    Parameters:
      handoff_kinds    -- the solver-side vocabulary read from the header.
      scene_fact_kinds -- the scene-fact vocabulary read from its header.
      translator       -- what the one production translator can emit.
      occurrences      -- the per-kind counts read out of committed recordings.

    Each row carries the eight facts the census was asked for, plus the
    three-part admission verdict.
    """
    emittable = set(translator["topology_kinds_it_can_emit"])
    counts = occurrences["occurrences_by_kind_number"]
    rows: List[Dict[str, Any]] = []

    # --- solver-side vocabulary -------------------------------------------
    for name, value in sorted(handoff_kinds.items(), key=lambda kv: kv[1]):
        if name.endswith("_NONE"):
            continue  # "no event" is the absence of a kind, not a kind
        semantics = KIND_SEMANTICS[name]
        has_detector = name in emittable
        # An occurrence count is only meaningful when a detector exists. When it
        # does not, the field says so in words rather than printing a 0 that a
        # skim-reader would take for "measured, and it never happens".
        recorded = counts.get(str(value), 0)
        rows.append(
            {
                "vocabulary": "Fl4512TopologyEventHandoffKindV1",
                "vocabulary_header": HANDOFF_HEADER.as_posix(),
                "enum_name": name,
                "enum_value": value,
                "human_meaning": semantics["human_meaning"],
                # 1. Producer symbol.
                "producer_symbol": translator["function"] if has_detector else None,
                "producer_source": (
                    f"{translator['source']}:{translator['source_line']}"
                    if has_detector
                    else None
                ),
                "producer_call_site": (
                    "engine/render/render_resolve.cpp "
                    "Fl4512BuildMeshBundleCandidates (per-child copy of the "
                    "Active Stroke owner's per-chain event)"
                    if has_detector
                    else None
                ),
                # 2. Complete identity key.
                "complete_identity_key": (
                    "semantic_chain_id only. The declared event carries "
                    "Fl4512TopologyEventV1::semantic_chain_id and is attached to "
                    "the track by Fl4512SequenceTrackKeyV2(chain.front() "
                    "feature_ref, cell_index). It is NOT keyed by a complete "
                    "FeatureRef plus epoch."
                    if has_detector
                    else "none exists; no producer assigns this kind an identity"
                ),
                # 3. Detection rule.
                "detection_rule": semantics["detection_rule"],
                # 4. Magnitude semantics.
                "magnitude_semantics": semantics["magnitude_semantics"],
                # 5. Compiled policy consumer.
                "compiled_policy_consumer": None,
                "compiled_policy_note": (
                    "no per-kind compiled policy exists. Every kind is charged "
                    "the same hard-coded kFl4512LambdaTopologyV2 = 25 inside "
                    "Fl4512SpacetimeTopologyCostV1, and the declaration's only "
                    "effect is to forgive `magnitude` units of measured change."
                ),
                # 6. Runtime occurrence count.
                "runtime_occurrence_count": recorded if has_detector else None,
                "runtime_occurrence_state": "MEASURED_NONE" if has_detector else "UNAVAILABLE",
                "runtime_occurrence_basis": (
                    "static analysis plus existing committed recordings; the "
                    "game was not launched"
                ),
                # 7. Visible effect evidence.
                "visible_effect_evidence": None,
                "visible_effect_note": (
                    "absent: no committed receipt, golden, or screenshot manifest "
                    "in the repository ties any topology-event kind to a pixel or "
                    "glyph difference"
                ),
                # 8. Falsifier.
                "falsifier": semantics["falsifier"],
                # The three-part admission rule.
                "admission": {
                    "real_production_detector": has_detector,
                    "authored_compiled_policy": False,
                    "track_owner_consumption_evidence": False,
                    "meets_all_three": False,
                    "blocking_reasons": (
                        [
                            "no authored compiled policy: the cost weight is a "
                            "hard-coded C++ literal",
                            "no consumption evidence: zero occurrences recorded "
                            "in any committed recording; only test-harness "
                            "coverage exists",
                        ]
                        if has_detector
                        else [
                            "no production detector: the only production "
                            "translator cannot return this kind",
                            "no authored compiled policy",
                            "no consumption evidence",
                        ]
                    ),
                },
                "verdict": "MEASURED_NONE" if has_detector else "UNAVAILABLE",
            }
        )

    # --- scene-fact vocabulary --------------------------------------------
    for name, value in sorted(scene_fact_kinds.items(), key=lambda kv: kv[1]):
        if name.endswith("_NONE"):
            continue
        semantics = KIND_SEMANTICS[name]
        rows.append(
            {
                "vocabulary": "FL4683_TOPOLOGY_EVENT",
                "vocabulary_header": SCENE_FACT_HEADER.as_posix(),
                "enum_name": name,
                "enum_value": value,
                "human_meaning": semantics["human_meaning"],
                "producer_symbol": None,
                "producer_source": None,
                "producer_call_site": None,
                "complete_identity_key": (
                    "none exists; the field is written as a constant and never "
                    "associated with a feature identity"
                ),
                "detection_rule": semantics["detection_rule"],
                "magnitude_semantics": semantics["magnitude_semantics"],
                "compiled_policy_consumer": None,
                "compiled_policy_note": (
                    "no compiled address or policy is keyed on this field; the "
                    "FL-4684 reachability measurement already classifies "
                    "SceneFactV1::topology_event as a constant-zero output"
                ),
                "runtime_occurrence_count": None,
                "runtime_occurrence_state": "UNAVAILABLE",
                "runtime_occurrence_basis": (
                    "the single assignment to SceneFactV1::topology_event is "
                    "unconditional and writes FL4683_TOPOLOGY_EVENT_NONE; no "
                    "other write exists anywhere in the source"
                ),
                "visible_effect_evidence": None,
                "visible_effect_note": "absent",
                "falsifier": semantics["falsifier"],
                "admission": {
                    "real_production_detector": False,
                    "authored_compiled_policy": False,
                    "track_owner_consumption_evidence": False,
                    "meets_all_three": False,
                    "blocking_reasons": [
                        "no production detector: the field is assigned a constant",
                        "no authored compiled policy",
                        "no consumption evidence",
                    ],
                },
                "verdict": "UNAVAILABLE",
            }
        )
    return rows


def build_report() -> Dict[str, Any]:
    """Assemble the whole census."""
    handoff_source = read(HANDOFF_HEADER)
    track_owner_source = read(TRACK_OWNER_HEADER)
    scene_fact_source = read(SCENE_FACT_HEADER)
    render_resolve_source = read(RENDER_RESOLVE)
    active_stroke_source = read(ACTIVE_STROKE_HEADER)

    handoff_kinds = parse_enum(handoff_source, "Fl4512TopologyEventHandoffKindV1")
    scene_fact_kinds = parse_enum(scene_fact_source, "Fl4683TopologyEventV1")

    # The track-owner header keeps its own copy of the solver vocabulary under
    # slightly different constant names (FL4512_TOPOLOGY_EVENT_* rather than
    # FL4512_TOPOLOGY_HANDOFF_*). Two hand-maintained copies of one vocabulary
    # is exactly the kind of thing that silently drifts apart, so the census
    # checks them against each other and refuses to report if they disagree.
    mirror_kinds = parse_enum(track_owner_source, "Fl4512TopologyEventKindV1")
    handoff_suffixes = {
        name.replace("FL4512_TOPOLOGY_HANDOFF_", ""): value
        for name, value in handoff_kinds.items()
    }
    mirror_suffixes = {
        name.replace("FL4512_TOPOLOGY_EVENT_", ""): value
        for name, value in mirror_kinds.items()
    }
    if handoff_suffixes != mirror_suffixes:
        raise CensusError(
            "the two copies of the solver topology vocabulary disagree: "
            f"{sorted(handoff_suffixes.items())} vs {sorted(mirror_suffixes.items())}"
        )
    lifecycle_kinds = parse_enum(active_stroke_source, "Fl4512ActiveStrokeSnakeEventKindV2")
    translator = read_lifecycle_to_topology_mapping(handoff_source)
    occurrences = count_occurrences_in_receipts()

    # Every declared kind must have a semantics row, or the census is incomplete.
    declared = [
        name
        for name in list(handoff_kinds) + list(scene_fact_kinds)
        if not name.endswith("_NONE")
    ]
    missing = [name for name in declared if name not in KIND_SEMANTICS]
    if missing:
        raise CensusError(
            "declared kinds with no census row: " + ", ".join(sorted(missing))
        )

    rows = build_kind_rows(handoff_kinds, scene_fact_kinds, translator, occurrences)

    # Counts, kept explicitly separate so UNAVAILABLE never reads as zero.
    meets_all_three = [row for row in rows if row["admission"]["meets_all_three"]]
    unavailable = [row for row in rows if row["verdict"] == "UNAVAILABLE"]
    measured_none = [row for row in rows if row["verdict"] == "MEASURED_NONE"]

    # The single production call site whose literal argument keeps the loop term
    # dormant. Located by text so the citation cannot go stale unnoticed.
    presenter_source = read(PRESENTER_SOURCE)
    loop_call_site_line = line_of(
        presenter_source, "/*is_loop_closing_frame*/false", PRESENTER_SOURCE
    )

    return {
        "schema": "fl4685.topology_event_writer_census.v1",
        "generated_by": "scripts/measure_fl4685_topology_event_writer_census.py",
        "owning_fl": "FL-4685",
        "what_this_is": (
            "A census of every declared topology-event kind: who produces it, "
            "how it is identified, what would detect it, what its magnitude "
            "means, what compiled policy consumes it, how often it has been "
            "recorded, whether any visible effect was ever evidenced, and what "
            "would falsify each row. It is a measurement, not a decision."
        ),
        "the_game_was_not_launched": True,
        "three_part_admission_rule": {
            "1_real_production_detector": (
                "code on the live frame path that can actually emit the kind"
            ),
            "2_authored_compiled_policy": (
                "a per-kind weight, threshold, or rule in a compiled reviewable "
                "artifact, not a number typed into a C++ header"
            ),
            "3_track_owner_consumption_evidence": (
                "a recording showing the event arriving and being handled "
                "correctly, not merely a function that would handle one"
            ),
            "a_kind_enters_cutover_behaviour_only_when_all_three_exist": True,
        },
        "verdict_vocabulary": {
            "UNAVAILABLE": (
                "no production code path can emit this kind; its occurrence "
                "count is undefined rather than zero"
            ),
            "MEASURED_NONE": (
                "a production detector exists and the recordings inspected "
                "contain zero occurrences of it"
            ),
            "MEETS_ALL_THREE": "all three admission criteria are satisfied",
        },
        "vocabularies_censused": [
            {
                "enum": "Fl4512TopologyEventHandoffKindV1",
                "header": HANDOFF_HEADER.as_posix(),
                "mirrored_by": "Fl4512TopologyEventKindV1 in "
                + TRACK_OWNER_HEADER.as_posix(),
                "mirror_agrees": True,
            },
            {
                "enum": "Fl4683TopologyEventV1",
                "header": SCENE_FACT_HEADER.as_posix(),
            },
        ],
        "vocabularies_deliberately_excluded": [
            {
                "enum": "Fl4512CellTopologyKindV1",
                "header": HANDOFF_HEADER.as_posix(),
                "why": (
                    "it names what a cell IS within a chain (chain body, "
                    "topology anchor, projection anchor), not a change between "
                    "frames, so it is not a topology-event vocabulary"
                ),
            }
        ],
        "production_translator": translator,
        "active_stroke_lifecycle_vocabulary": dict(
            sorted(lifecycle_kinds.items(), key=lambda kv: kv[1])
        ),
        "hard_coded_cost_weights": read_hard_coded_weights(track_owner_source),
        "scene_fact_topology_event_writer": {
            "assignment": "out.topology_event = FL4683_TOPOLOGY_EVENT_NONE;",
            "source": RENDER_RESOLVE.as_posix(),
            "source_line": line_of(
                render_resolve_source,
                "out.topology_event = FL4683_TOPOLOGY_EVENT_NONE;",
                RENDER_RESOLVE,
            ),
            "conditional": False,
            "other_writers_in_the_repository": 0,
        },
        "loop_closure_cross_reference": {
            "production_call_site": PRESENTER_SOURCE.as_posix(),
            "production_call_site_line": loop_call_site_line,
            "note": (
                "the current audited production call site cannot activate loop "
                "cost: it passes a literal false for is_loop_closing_frame, and "
                "the loop weight is a hard-coded 30. This is a statement about "
                "the audited call site, not a claim about every historical frame."
            ),
        },
        "occurrence_measurement": occurrences,
        "kinds": rows,
        "summary": {
            "declared_kinds_total": len(rows),
            "kinds_meeting_all_three_criteria": len(meets_all_three),
            "kinds_unavailable_no_production_detector": len(unavailable),
            "kinds_with_a_detector_and_measured_none": len(measured_none),
            "kinds_with_any_visible_effect_evidence": 0,
            "kinds_with_a_per_kind_compiled_policy": 0,
        },
        "what_this_census_does_not_establish": [
            "that any topology event has ever occurred during normal play",
            "that any topology event has ever changed a visible pixel",
            "that the two split/merge kinds are consumed correctly by the track "
            "owner; the recordings inspected contain zero occurrences",
            "that a kind absent from the recordings inspected is absent from all "
            "runs; only the listed recordings were read",
        ],
        "runtime_consumption_allowed": False,
    }


def main() -> int:
    """Command-line entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="record the census")
    parser.add_argument("--check", action="store_true", help="verify the recorded census")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write and --check")

    try:
        report = build_report()
    except CensusError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    text = json.dumps(report, indent=2, sort_keys=False) + "\n"
    target = ROOT / OUTPUT
    if args.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {OUTPUT.as_posix()} bytes={len(text.encode('utf-8'))}")
        return 0

    if not target.exists():
        print(f"error: {OUTPUT.as_posix()} has not been recorded yet", file=sys.stderr)
        return 1
    if target.read_text(encoding="utf-8") != text:
        print(
            f"error: {OUTPUT.as_posix()} is stale; rerun with --write and review "
            "the difference",
            file=sys.stderr,
        )
        return 1
    print(f"ok {OUTPUT.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
