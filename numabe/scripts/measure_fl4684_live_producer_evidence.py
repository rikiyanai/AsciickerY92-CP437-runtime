#!/usr/bin/env python3
# =============================================================================
# scripts/measure_fl4684_live_producer_evidence.py
# =============================================================================
# FL-4684 / FL-4685 LIVE PRODUCER EVIDENCE COMPILER
#
# WHAT THIS SCRIPT IS FOR, IN PLAIN LANGUAGE
# ------------------------------------------
# An earlier measurement (scripts/measure_fl4684_compiled_address_reachability.py)
# read the SOURCE CODE of the renderer and worked out that all 99 compiled
# "addresses" -- the numbered slots the offline glyph compiler produces -- could
# in principle be named by the renderer. That was a reading exercise. It proved
# nothing about what actually happens when a person plays the game.
#
# This script closes that gap. It takes the counters recorded by a REAL play
# session (the observation-only census written by
# platform/fl4684_scene_fact_emission_census.h) and answers, address by address:
#
#     Did the running product actually emit this one? How many times?
#
# It also answers FL-4685's separate question -- whether stroke SPLIT and MERGE
# topology events, previously measured as zero from an essentially static frame,
# appear once the camera is moving.
#
# AND IT ANSWERS THE QUESTION BOTH OF THOSE TRIPPED OVER  (FL-4684)
# -----------------------------------------------------------------
# Both recorded sessions -- desktop and browser -- reported that the renderer
# published ZERO "Active Stroke" ribbons. (Active Stroke is the step that links
# small line-features found on 3D models into long continuous outlines.) Zero on
# both targets makes every downstream split/merge conclusion vacuous, because a
# thing that never existed cannot split.
#
# The trouble was that zero accused nobody. The stroke stage is a chain of
# steps, and the chain can be empty at any link: the build might never have run,
# or run with nothing to chew on, or been refused a compiled profile, or chewed
# plenty and published nothing. The earlier version of this script kept only two
# numbers out of the whole stroke record and discarded the rest, so the question
# could not even be posed.
#
# This script now emits a `reachability_census` block per run, listing every
# link IN PIPELINE ORDER with the first zero called out by name. See
# build_reachability_census() below. Numbers that a run's recording does not
# contain are reported as null, never as zero -- absent and empty are different
# claims and this artifact must never blur them.
#
# WHAT IT DOES NOT DO
# -------------------
# It does not decide anything, does not grade the result, and does not invent a
# verdict. It writes down numbers and the exact conditions under which they were
# taken, including whether the run in question actually achieved camera motion.
# A run whose camera did not move is labelled as such rather than being quietly
# counted as motion evidence.
#
# USAGE
#   python3 scripts/measure_fl4684_live_producer_evidence.py \
#       --run desktop_static=.run/fl4684_native_live_producer/receipt.json \
#       --run web_motion=.run/fl4684_web_live_producer/receipt.json \
#       --out assets/glyphs/generated/fl4684.live_producer_evidence.v1.json
#
# Each --run is NAME=PATH, where PATH is a run receipt produced by one of the
# two driver scripts. Several runs can be folded into one artifact so the
# desktop and browser answers sit side by side.
# =============================================================================

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The compiled-address measurement this evidence is compared against.
REACHABILITY_ARTIFACT = (
    ROOT / "assets" / "glyphs" / "generated"
    / "fl4684.compiled_address_reachability.v1.json"
)

# The material namespace names used by the compiled artifact, and the small
# integer the renderer stores in SceneFactV1::material_namespace for each. These
# come from FL4512_PROFILE_NAMESPACE_* in engine/render/render_frame_input.h and
# must stay identical to the mapping in
# scripts/measure_fl4684_compiled_address_reachability.py.
NAMESPACE_NAME_BY_CODE = {1: "terrain", 2: "mesh"}

# The route-family bit POSITION the census reports, and the compiled artifact's
# name for that family. Bit positions are defined by Fl4683RouteFamilyV1 in
# platform/fl4683_scene_fact_record.h.
ROUTE_KEY_BY_FAMILY_BIT = {
    0: "ramp.v1",
    1: "density.v1",
    2: "edge.v1",
    3: "direction.v1",
    4: "flow.v1",
    5: "filled.field.v1",
    6: "mesh_feature.bundle.v1",
}

# The two families that have exactly one lane. The renderer reports lane 0 for
# them; the compiled artifact records `lane_index: null`. Mapping one onto the
# other here is what lets the two descriptions be compared.
SINGLE_LANE_ROUTE_KEYS = {"filled.field.v1", "mesh_feature.bundle.v1"}

# Human-readable names for the scene-fact role byte, from Fl4683SceneFactRoleV1.
ROLE_NAME_BY_CODE = {
    0: "none",
    1: "tonal_fill",
    2: "structural_feature",
    3: "material_boundary",
    4: "mesh_feature",
    5: "authored_text",
}

# =============================================================================
# FL-4684 ACTIVE STROKE REACHABILITY CENSUS -- the vocabulary
# =============================================================================
# "Active Stroke" is the renderer stage that links small line-features found on
# 3D models into long continuous ribbons, so the text-cell painter can draw a
# flowing outline instead of a scatter of unrelated dashes.
#
# Two recorded play sessions -- one on the desktop build, one in the browser --
# BOTH reported that zero ribbons were ever published. Zero accuses nobody: the
# stage is a chain, and a chain can be empty at any link. The tables below give
# the plain-English name of every typed code the renderer can report, so the
# artifact never asks a reader to memorise a number.

# The stroke owner's per-frame failure byte (Fl4512ActiveStrokeSnakeFailureV2 in
# apps/fl4512-runtime/engine/fl4512_active_stroke_owner.h).
STROKE_FAILURE_NAME_BY_CODE = {
    0: "none",
    1: "missing_authority",
    2: "invalid_range",
    3: "numeric",
    4: "capacity",
    5: "topology_budget",
}

# The retained-store lifecycle reset byte
# (Fl4512ActiveStrokeSnakeResetReasonV2, same header). A "reset" means the store
# threw away the ribbon identities it had been carrying between frames.
STROKE_RESET_NAME_BY_CODE = {
    0: "none",
    1: "lifetime_init",
    2: "world_generation_change",
    3: "registry_identity_change",
    4: "frame_nonmonotonic",
    5: "missing_authority",
    6: "numeric_failure",
    7: "capacity_failure",
}

# ---------------------------------------------------------------------------
# The ordered chain itself
# ---------------------------------------------------------------------------
# Each entry is one LINK, in the order the renderer actually runs them. A reader
# scans down this list and stops at the first link whose number is zero: that
# link, and not "Active Stroke" in general, is what was empty.
#
# EVERY NUMBER IN THIS CHAIN IS A MONOTONIC COUNTER OWNED AT ITS SOURCE SITE.
# The renderer increments each one at the exact line where the thing it counts
# happens, and it only ever goes up for the life of one launched game. The
# in-engine census copies the whole record and OVERWRITES its previous copy; it
# never adds anything up. That rule exists because an earlier design summed
# per-frame readings inside a loop that runs once per CHARACTER CELL, using "the
# frame identifier changed" as its clock -- a clock that ticked 856 times in a
# recorded session whose last gameplay frame was numbered 119. Everything built
# on it counted the same frame many times over. (FL-4684)
#
# The columns are:
#   stage               -- short machine name for the link
#   measure             -- key to read from the census reachability block
#   zero_is_unavailable -- True when a zero at this link means the measurement
#                          downstream is ABSENT rather than empty, so this link
#                          must never be reported as "the first zero"
#   zero_means          -- plain English for what a zero at this link proves
REACHABILITY_STAGES = [
    {
        "stage": "finalizer_entered",
        "measure": "finalizer_entered_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "The stroke build was never entered. Whatever is wrong lies "
            "upstream of Active Stroke entirely -- the renderer never asked "
            "for strokes, so the stroke owner cannot be at fault."
        ),
    },
    {
        "stage": "pre_admission_authority_ready",
        "measure": "pre_admission_ready_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "The stroke build was entered but never once had both of its "
            "prerequisites. Either the retained ribbon store was absent, or "
            "the reprojection pass had already failed. Read "
            "`pre_admission_breakdown` to see which -- in the live source both "
            "hide behind one short-circuit test, and before this measurement "
            "existed BOTH of them looked from the outside exactly like 'no "
            "strokeable geometry was found', which is a different and much "
            "more alarming conclusion aimed at the wrong owner."
        ),
    },
    {
        "stage": "admission_scan_entered",
        "measure": "admission_scan_entered_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "The prerequisites were satisfied, yet the search for usable model "
            "features was never entered. No code path allows that, so a zero "
            "here would itself be the finding rather than a diagnosis."
        ),
    },
    {
        "stage": "eligible_input_features",
        "measure": "eligible_input_feature_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "The search ran, and the shared upstream feature-production chain "
            "handed it nothing that qualified as stroke input. This locates a "
            "BOUNDARY, not a culprit: the emptiness begins at or before "
            "feature production -- projection, visibility, eligibility "
            "marking, feature-kind assignment -- which is upstream of the "
            "stroke owner, and the stroke owner was never reached. It does NOT "
            "establish that the scene contains no strokeable content. A scene "
            "can be full of geometry while the production chain marks none of "
            "it eligible. Separating those two requires measuring the producer "
            "itself, which this chain does not do."
        ),
    },
    {
        "stage": "compiled_profile_admitted",
        "measure": "admission_success_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "Qualifying features existed, but the compiled-profile handshake "
            "never agreed on one topology block, so the stroke owner was never "
            "handed anything to work on. The blocker is the compiled profile."
        ),
    },
    {
        "stage": "stroke_owner_invoked",
        "measure": "owner_invoked_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "The frame was admitted and the ribbon builder was still never "
            "called. No code path allows that either, so a zero here is a "
            "finding about the renderer rather than about the stroke stage."
        ),
    },
    {
        "stage": "valid_observation_published",
        "measure": "observation_valid_total",
        # A zero here does NOT locate a defect. It means the renderer never
        # certified a whole record, so every number after it was never
        # accumulated at all. Reporting it as "the first zero" would blame a
        # pipeline link for what is actually missing authority.
        "zero_is_unavailable": True,
        "zero_means": (
            "The renderer never once certified a whole Active Stroke record "
            "for a frame. Every link after this one is therefore UNAVAILABLE, "
            "not empty: those counters were never accumulated, because the "
            "renderer refuses to accumulate from an uncertified frame. No "
            "first-zero verdict may be issued from this run. Read "
            "`failure_classes_by_name` for the renderer's own typed reason."
        ),
    },
    {
        "stage": "input_samples",
        "measure": "input_sample_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "The stroke owner ran on certified frames but collected no raw "
            "input points, so sampling -- not joining -- is where the "
            "pipeline emptied."
        ),
    },
    {
        "stage": "owner_groups",
        "measure": "owner_group_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "Raw points existed but were never partitioned into a single "
            "owner group, so grouping is where the pipeline emptied."
        ),
    },
    {
        "stage": "accepted_owner_groups",
        "measure": "accepted_owner_group_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "Groups were formed and every single one of them was REJECTED. "
            "When that happens, publishing zero ribbons is the correct "
            "downstream consequence and the publication step is innocent. "
            "Read `rejection` below for how many groups were thrown away and "
            "on how many frames nothing survived."
        ),
    },
    {
        "stage": "published_snakes",
        "measure": "published_snake_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "Groups survived rejection and still no ribbon was published from "
            "any of them. THIS is the link the two zero-result runs reported, "
            "and reaching it with every earlier link non-zero is what makes "
            "the stroke owner's own publication step the remaining suspect."
        ),
    },
    {
        "stage": "published_segments",
        "measure": "published_segment_total",
        "zero_is_unavailable": False,
        "zero_means": (
            "Ribbons were published but carried no segments, which would make "
            "them empty shells rather than drawable outlines."
        ),
    },
]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 fingerprint of a file, or an empty string if absent."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_output(*args: str) -> str:
    """Run a git command in the repository and return its trimmed output."""
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return ""


def compiled_address_identity(row: dict) -> tuple:
    """Reduce one COMPILED address to the tuple the renderer can name it by.

    The compiled artifact describes each address with many fields, but only five
    of them are things the renderer states about a cell. Those five are the
    identity used for matching:

        (namespace name, material key, role code, route key, lane index)

    `lane_index` is None for the two single-lane families.
    """
    return (
        row["profile_namespace"],
        int(row["material_key"]),
        int(row["scene_fact_role_code"]),
        row["route_key"],
        row["lane_index"],
    )


def live_address_identity(entry: dict) -> tuple | None:
    """Reduce one OBSERVED census tuple to the same five-part identity.

    Returns None when the observed tuple names a namespace or family the
    compiled artifact has no vocabulary for, which is itself worth counting
    rather than silently dropping.
    """
    namespace_name = NAMESPACE_NAME_BY_CODE.get(int(entry["material_namespace"]))
    route_key = ROUTE_KEY_BY_FAMILY_BIT.get(int(entry["route_family_bit"]))
    if namespace_name is None or route_key is None:
        return None
    lane = int(entry["lane"])
    # The renderer says lane 0 for a single-lane family; the compiler says null.
    lane_index = None if route_key in SINGLE_LANE_ROUTE_KEYS else (
        None if lane < 0 else lane
    )
    return (
        namespace_name,
        int(entry["material_key"]),
        int(entry["role"]),
        route_key,
        lane_index,
    )


def load_census_for_run(receipt_path: Path) -> tuple[dict, dict]:
    """Load one run receipt and the census file it points at.

    Returns (receipt, census). Raises if either is missing, because a receipt
    without its census cannot support any claim about what the producer emitted.
    """
    receipt = json.loads(receipt_path.read_text())
    census_rel = receipt.get("census_path")
    if not census_rel:
        raise SystemExit(f"{receipt_path}: receipt names no census_path")
    census_path = Path(census_rel)
    if not census_path.is_absolute():
        census_path = ROOT / census_path
    if not census_path.exists():
        raise SystemExit(f"{receipt_path}: census file missing at {census_path}")
    return receipt, json.loads(census_path.read_text())


def summarise_motion(receipt: dict) -> dict:
    """State, from measurements only, whether this run's camera actually moved.

    THE RULE, AND THE MISTAKE IT EXISTS TO PREVENT
    ----------------------------------------------
    The obvious test -- "did two consecutive photographs of the viewport differ
    a lot?" -- is not good enough, and trusting it produced a wrong answer once
    already.

    The game's MAIN MENU draws a full 3D scene behind its title and menu items.
    So while a player sits on the menu the renderer runs, the producer emits,
    and the photographs look like a real game. In the first desktop run four of
    five photographs were menu frames. The only large difference between
    consecutive photographs, 82.8%, was the instant the player pressed New Game.
    Read naively that looked like a camera turn. It was a screen change.

    So a difference may only be counted when BOTH photographs either side of it
    were classified as gameplay by someone who looked at them. That
    classification is recorded on the receipt by
    scripts/annotate_fl4684_capture_scene_phase.py, together with who did the
    looking and what they saw. Frames nobody has classified are never allowed to
    support a motion claim, because "unlabelled" must not quietly behave like
    "gameplay".

    A run with fewer than two consecutive gameplay frames yields no motion
    evidence at all, however much its pictures differ.
    """
    captures = receipt.get("viewport_captures", []) or []
    classification = receipt.get("capture_scene_phase_classification")

    # Walk consecutive pairs. Each capture after the first carries how much it
    # differed from the one before it, so pair i is (capture i-1, capture i).
    usable_diffs = []
    rejected_pairs = []
    for i in range(1, len(captures)):
        previous = captures[i - 1]
        current = captures[i]
        pct = current.get("pixel_bytes_differing_from_previous_pct")
        if pct is None:
            continue
        phase_previous = previous.get("scene_phase", "unclassified")
        phase_current = current.get("scene_phase", "unclassified")
        if phase_previous == "in_play" and phase_current == "in_play":
            usable_diffs.append(pct)
        else:
            rejected_pairs.append(
                {
                    "pair": [i - 1, i],
                    "difference_pct": pct,
                    "scene_phases": [phase_previous, phase_current],
                    "why_rejected": (
                        "a frame in this pair is not classified as gameplay, so "
                        "the difference cannot be attributed to the camera moving"
                    ),
                }
            )

    engine_input = receipt.get("engine_reported_input", {}) or {}
    web_input = receipt.get("browser_input_drive", {}) or {}
    phases = [c.get("scene_phase", "unclassified") for c in captures]
    return {
        "declared_mode": receipt.get("run", {}).get("mode", "unknown"),
        "viewport_capture_count": len(captures),
        "capture_scene_phases": phases,
        "in_play_capture_count": sum(1 for p in phases if p == "in_play"),
        "scene_phase_classified_by": (
            classification.get("classified_by") if classification else None
        ),
        "scene_phase_evidence": (
            classification.get("evidence") if classification else None
        ),
        # Only gameplay-to-gameplay differences survive into the verdict.
        "consecutive_frame_difference_pct": usable_diffs,
        "max_consecutive_frame_difference_pct": max(usable_diffs) if usable_diffs else 0.0,
        "rejected_pairs": rejected_pairs,
        "engine_reported_input_events_total": engine_input.get("input_events_total"),
        "browser_reported_key_hold_ms": web_input.get("total_key_hold_ms"),
        # The threshold is generous on purpose: a camera turn moves most of the
        # screen, while ambient water and foliage animation moves well under one
        # percent of the bytes.
        "camera_motion_evidenced": bool(usable_diffs) and max(usable_diffs) >= 5.0,
        "camera_motion_threshold_pct": 5.0,
        "how_motion_is_decided": (
            "Two viewport pictures from the same continuous run are compared "
            "byte by byte, but ONLY when both were classified as gameplay by "
            "someone who looked at them. The main menu renders a 3D scene, so an "
            "unfiltered comparison mistakes leaving the menu for a camera turn. "
            "A camera turn or walk changes most of the screen; ambient animation "
            "changes a fraction of one percent. A run with fewer than two "
            "consecutive gameplay frames provides no motion evidence at all."
        ),
    }


def _named_histogram(counts, name_by_code: dict) -> dict | None:
    """Turn a bare list of counts into a {name: count} mapping. (FL-4684)

    `counts` is a list where position N holds how many frames reported typed
    code N. Bare integers are unreadable six months later, so every position is
    relabelled with the name the C++ enum gives it. A position with no declared
    name keeps a visible `code_N` label rather than being dropped, because an
    undeclared code appearing at runtime is exactly the kind of drift that must
    stay loud.

    Returns None when the census predates this measurement and carries no such
    list at all -- absent is never the same thing as all-zero.
    """
    if counts is None:
        return None
    return {
        name_by_code.get(index, f"code_{index}"): int(value)
        for index, value in enumerate(counts)
    }


def _value_or_none(block: dict | None, key: str):
    """Read one measurement, keeping ABSENT and ZERO strictly apart. (FL-4684)

    Parameters:
      block -- the census reachability object, or None when the run's recording
               carries no such object at all.
      key   -- the measurement name to read.

    Returns the integer value, or None when the value is missing.

    THE RULE THIS FUNCTION EXISTS TO ENFORCE
    ----------------------------------------
    "This recording does not contain the number" and "the number was measured
    and came back zero" are completely different claims. Only the second one is
    evidence about the product. Anything that quietly turns the first into the
    second manufactures a measurement that was never taken, and the ordered scan
    below would then blame a pipeline link for it.

    So a missing block, a missing key, and an explicit JSON `null` all return
    None here, and None is never coerced to 0 anywhere downstream. This also
    means recordings made by OLDER builds stay honestly unreadable rather than
    being retrospectively reclassified as empty measurements.
    """
    if not block:
        return None
    raw = block.get(key)
    if raw is None:
        return None
    return int(raw)


def build_reachability_census(census: dict) -> dict:
    """Lay the Active Stroke pipeline out link by link so the first zero shows.

    WHY THIS FUNCTION EXISTS  (FL-4684)
    -----------------------------------
    Two recorded play sessions both reported zero published strokes. Every
    conclusion anyone wanted to draw from that -- about splits, about merges,
    about whether the glyph compiler's stroke addresses are reachable -- was
    vacuous, because "zero strokes" does not say WHICH step produced nothing.

    This function reads every link of the chain and reports them IN PIPELINE
    ORDER, together with the first link whose value is zero. That first zero is
    the located defect; everything after it is a consequence, not a cause.

    THE FOUR HONESTY RULES THIS FUNCTION KEEPS  (FL-4684)
    -----------------------------------------------------
    1. ABSENT IS NOT ZERO. A number missing from the recording is reported as
       null. A recording made before this measurement existed must never be made
       to look like a measurement that came back empty, and it can never be
       retrospectively reclassified into one.

    2. NO SAMPLES AT ALL MEANS THE WHOLE RUN IS UNAVAILABLE. If the in-engine
       census never took a single reading, the reachability block is null and no
       link is reported as empty. Nothing about the stroke pipeline may be
       concluded from that run.

    3. NO CERTIFIED OBSERVATION MEANS EVERY LATER LINK IS UNAVAILABLE. The
       renderer only accumulates the links after its validity certificate on
       frames it certified. When no frame was ever certified, those counters
       were never touched. They are reported as null, and the certificate link
       itself is reported as a BLOCKED verdict rather than as "the first zero" --
       because missing authority is not a pipeline link failing.

    4. A ZERO THAT IS NOT A FAILURE STAYS OUT OF THE SCAN. Rejections, splits,
       merges, and open/closed shape may legitimately never happen in a short
       recording, so they are reported separately where they cannot be mistaken
       for the point of failure. Rejection does appear in the scan, but as its
       POSITIVE form -- "accepted groups" -- because groups surviving rejection
       is what the next link actually needs.

    Parameter:
      census -- the parsed census JSON written by the in-engine tap
                (platform/fl4684_scene_fact_emission_census.h).

    Returns the census block for one run.
    """
    # The single source object. There is exactly one owner of every Active
    # Stroke number: the renderer's monotonic record, copied whole into the
    # census. Splitting these numbers across two JSON objects (as an earlier
    # version did) creates two owners of the same fact that can silently
    # disagree, so there is deliberately only one here.
    #
    # `None` means this run's recording carries no such object at all.
    reach = census.get("active_stroke_reachability")
    block_present = isinstance(reach, dict)
    # This is an integrity certificate, not a pipeline stage. A positive value
    # means impossible owner data was observed. A missing value means the run
    # predates this certificate. Neither case may yield a first-zero verdict.
    owner_group_contract_violations = _value_or_none(
        reach if block_present else None,
        "owner_group_count_contract_violation_total",
    )

    # ---- Walk the chain in order ---------------------------------------
    stages = []
    for order, spec in enumerate(REACHABILITY_STAGES):
        value = _value_or_none(reach if block_present else None, spec["measure"])
        stages.append(
            {
                "order": order,
                "stage": spec["stage"],
                "census_key": f"active_stroke_reachability.{spec['measure']}",
                # Stating this on every row keeps the reader from assuming any
                # of these numbers was reconstructed by adding up observations.
                "kind": "monotonic_source_site_total",
                # None means "this run's recording does not carry the number",
                # which is a different statement from "the number was zero".
                "value": value,
                "zero_means": spec["zero_means"],
            }
        )

    # ---- Locate the first empty link, or refuse to ----------------------
    # The scan stops at the FIRST of three things, whichever comes first:
    #
    #   * a missing value  -> the verdict is BLOCKED. A hole ahead of a zero
    #                         could be hiding the real first zero.
    #   * a zero at a link flagged `zero_is_unavailable` -> also BLOCKED, with
    #                         its own reason. Those links are the ones where a
    #                         zero means "nothing downstream was measured",
    #                         not "this link produced nothing".
    #   * a plain zero     -> that link is the located defect.
    first_empty = None
    verdict_blocked_by = None
    verdict_blocked_reason = None
    if not block_present:
        # Rule 2: the recording contains no Active Stroke reading whatsoever.
        verdict_blocked_by = "census_snapshot_absent"
        verdict_blocked_reason = (
            "This run's recording carries no Active Stroke reachability block "
            "at all, so not one reading of the stroke pipeline exists. The "
            "whole measurement is UNAVAILABLE for this run. It is NOT a run in "
            "which the pipeline was measured and found empty, and it cannot be "
            "reclassified into one after the fact."
        )
    elif owner_group_contract_violations is None:
        verdict_blocked_by = "owner_group_count_contract_check"
        verdict_blocked_reason = (
            "This recording predates the owner-group count integrity check. "
            "It cannot establish that rejected groups never exceeded groups "
            "seen, so its derived accepted-group count is unavailable."
        )
    elif owner_group_contract_violations > 0:
        verdict_blocked_by = "owner_group_count_contract_violation"
        verdict_blocked_reason = (
            "At least one certified observation reported more rejected owner "
            "groups than owner groups seen. That is impossible under the "
            "declared count contract. The frame was not normalized into an "
            "accepted-group zero, and no first-zero verdict may be issued."
        )
    else:
        for order, (stage, spec) in enumerate(zip(stages, REACHABILITY_STAGES)):
            if stage["value"] is None:
                verdict_blocked_by = stage["stage"]
                verdict_blocked_reason = (
                    "This run's recording does not carry the number for this "
                    "link, so any zero further down might be a consequence of "
                    "something inside the gap. Absent is not zero, and no "
                    "first-zero verdict may be issued."
                )
                break
            if stage["value"] == 0:
                if spec["zero_is_unavailable"]:
                    verdict_blocked_by = stage["stage"]
                    verdict_blocked_reason = spec["zero_means"]
                else:
                    first_empty = stage
                break

    # A convenience view for the reader: every link that carries no number.
    unavailable_stages = [s["stage"] for s in stages if s["value"] is None]

    return {
        "what_this_answers": (
            "Zero published strokes was measured on both targets, but the "
            "evidence could not say WHICH step of the stroke pipeline was "
            "already empty. This block reads every step in the order the "
            "renderer runs them, so the first zero locates the defect instead "
            "of leaving 'Active Stroke' as a whole under suspicion."
        ),
        "how_to_read_it": (
            "Read `stages` from top to bottom. The first entry whose `value` "
            "is 0 is where the pipeline emptied; its `zero_means` says what "
            "that proves. Entries after it are consequences, not causes. A "
            "`value` of null means this run's recording does not contain that "
            "number at all -- absent, not zero. Whenever a null appears before "
            "any zero, or whenever the renderer never certified an "
            "observation, NO first-zero verdict is issued and "
            "`verdict_blocked_by_missing_measure` names why."
        ),
        "every_value_is_a_monotonic_source_site_total": (
            "Each number here is incremented once, at the line in the renderer "
            "where the thing it counts happens, and only ever increases for "
            "the life of one launched game. The in-engine census copies the "
            "whole record and overwrites its previous copy; it never adds "
            "readings together. This matters because the census runs inside a "
            "per-character-cell loop that executes several times per displayed "
            "frame, so any summing done there would count the same frame's "
            "work many times over."
        ),
        # False for any run recorded before the in-engine census gained this
        # block. Such a run keeps its old numbers and honestly reports that the
        # new links were never recorded.
        "census_block_present": block_present,
        "stages": stages,
        "stages_with_no_measurement": unavailable_stages,
        "first_empty_stage": None if first_empty is None else first_empty["stage"],
        "first_empty_stage_order": (
            None if first_empty is None else first_empty["order"]
        ),
        "first_empty_stage_meaning": (
            None if first_empty is None else first_empty["zero_means"]
        ),
        # Names the link at which the scan refused to continue, if it refused.
        # While this is set, no first-zero verdict may be quoted from this run.
        "verdict_blocked_by_missing_measure": verdict_blocked_by,
        "verdict_blocked_reason": verdict_blocked_reason,
        # ---- The two prerequisites, told apart --------------------------
        # In the live source a missing ribbon store and an already-failed
        # reprojection pass hide behind ONE short-circuit test, so before this
        # measurement existed both produced the same outward evidence as "no
        # strokeable geometry was found" -- a different conclusion aimed at a
        # different owner. These two counters separate them.
        "pre_admission_breakdown": {
            "note": (
                "Which of the stroke build's two prerequisites was missing. "
                "Both are counted at their own source site, so a positive "
                "value in either names the blocker outright."
            ),
            "missing_store_total": _value_or_none(reach, "missing_store_total"),
            "reprojection_failure_total": _value_or_none(
                reach, "reprojection_failure_total"
            ),
            "pre_admission_ready_total": _value_or_none(
                reach, "pre_admission_ready_total"
            ),
        },
        # ---- Rejection, reported as a cause and not as fallout ----------
        "rejection": {
            "note": (
                "Rejection can be the CAUSE of an empty publication. If every "
                "owner group was thrown away, then publishing zero ribbons is "
                "the correct downstream consequence and the publication step "
                "is innocent. The `accepted_owner_groups` link in the ordered "
                "scan above is what makes that visible; these numbers say how "
                "much was thrown away and how often nothing survived."
            ),
            "rejected_owner_group_total": _value_or_none(
                reach, "rejected_owner_group_total"
            ),
            "rejected_owner_group_frame_max": _value_or_none(
                reach, "rejected_owner_group_frame_max"
            ),
            "frames_with_any_rejected_owner_group": _value_or_none(
                reach, "frames_with_any_rejected_owner_group"
            ),
            "frames_with_all_owner_groups_rejected": _value_or_none(
                reach, "frames_with_all_owner_groups_rejected"
            ),
            "owner_group_count_contract_violation_total": (
                owner_group_contract_violations
            ),
        },
        # ---- Numbers kept OUT of the ordered scan on purpose ------------
        # For each of these a zero is either good news or simply not a failure
        # point, so putting them in the scan would produce a false culprit.
        "not_a_failure_point": {
            "note": (
                "A zero in any of these is not evidence of a broken step. "
                "Ribbon shape and topology events are things that may "
                "legitimately never happen in a short recording."
            ),
            "open_snake_total": _value_or_none(reach, "open_snake_total"),
            "closed_snake_total": _value_or_none(reach, "closed_snake_total"),
            "split_total": _value_or_none(reach, "split_total"),
            "merge_total": _value_or_none(reach, "merge_total"),
        },
        # ---- Was the record even certified? -----------------------------
        "observation_validity": {
            "note": (
                "The renderer sets a validity certificate only when it has "
                "written the whole per-frame stroke record and its authority "
                "checks passed. Frames without it hold zero-filled placeholder "
                "storage. The renderer therefore accumulates NOTHING from an "
                "uncertified frame -- not even a zero -- and when no frame was "
                "ever certified every link after the certificate is reported "
                "as null. The links BEFORE the certificate are copied "
                "regardless, precisely so they survive the case where "
                "everything else is unusable."
            ),
            "observation_valid_total": _value_or_none(
                reach, "observation_valid_total"
            ),
            "observation_invalid_total": _value_or_none(
                reach, "observation_invalid_total"
            ),
            "snapshot_reads": _value_or_none(reach, "snapshot_reads"),
            "snapshot_reads_is_not_a_frame_count": (
                "This counts how many times the census refreshed its copy of "
                "the renderer's totals. It is not a frame count and is not a "
                "denominator for anything."
            ),
        },
        # ---- Why a link was empty, in the renderer's own words ----------
        "failure_classes_by_name": _named_histogram(
            (reach or {}).get("failure_frame_counts"),
            STROKE_FAILURE_NAME_BY_CODE,
        ),
        "reset_reasons_by_name": _named_histogram(
            (reach or {}).get("reset_reason_frame_counts"),
            STROKE_RESET_NAME_BY_CODE,
        ),
        "codes_outside_the_declared_vocabulary": {
            "note": (
                "Non-zero here means the renderer reported a failure or reset "
                "code this measurement has no name for, so the two histograms "
                "above cover only part of what happened and must not be read "
                "as exhaustive."
            ),
            "failure_out_of_range": _value_or_none(reach, "failure_out_of_range"),
            "reset_reason_out_of_range": _value_or_none(
                reach, "reset_reason_out_of_range"
            ),
        },
        "single_frame_peaks": {
            "note": (
                "A total can hide a single busy frame among many empty ones, "
                "and an average would hide it completely. These are the largest "
                "value any one frame reported."
            ),
            "input_sample_frame_max": _value_or_none(
                reach, "input_sample_frame_max"
            ),
            "owner_group_frame_max": _value_or_none(reach, "owner_group_frame_max"),
            "accepted_owner_group_frame_max": _value_or_none(
                reach, "accepted_owner_group_frame_max"
            ),
            "published_snake_frame_max": _value_or_none(
                reach, "published_snake_frame_max"
            ),
            "published_segment_frame_max": _value_or_none(
                reach, "published_segment_frame_max"
            ),
        },
        "frames_with_any": {
            "note": (
                "How many CERTIFIED frames reported a non-zero value. A zero "
                "total together with a zero count here is the strongest "
                "possible statement that the link never once produced "
                "anything. A null means the run never certified a frame, so "
                "the question was never asked."
            ),
            "input_sample": _value_or_none(reach, "frames_with_any_input_sample"),
            "owner_group": _value_or_none(reach, "frames_with_any_owner_group"),
            "accepted_owner_group": _value_or_none(
                reach, "frames_with_any_accepted_owner_group"
            ),
            "rejected_owner_group": _value_or_none(
                reach, "frames_with_any_rejected_owner_group"
            ),
            "published_snake": _value_or_none(
                reach, "frames_with_any_published_snake"
            ),
            "published_segment": _value_or_none(
                reach, "frames_with_any_published_segment"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="NAME=RECEIPT_PATH",
        help="a run receipt to fold in, labelled with a short name",
    )
    parser.add_argument(
        "--out",
        default=str(
            ROOT / "assets" / "glyphs" / "generated"
            / "fl4684.live_producer_evidence.v1.json"
        ),
        help="where to write the evidence artifact",
    )
    args = parser.parse_args()
    if not args.run:
        parser.error("at least one --run NAME=RECEIPT_PATH is required")

    reachability = json.loads(REACHABILITY_ARTIFACT.read_text())
    compiled_rows = reachability["addresses"]
    # Index every compiled address by the five-part identity the renderer can
    # name. Several compiled addresses may share one identity only if the
    # compiler produced duplicates; the count is recorded so that stays visible.
    compiled_by_identity: dict[tuple, list] = collections.defaultdict(list)
    for row in compiled_rows:
        compiled_by_identity[compiled_address_identity(row)].append(row)

    runs_out = {}
    # Union across every run: which compiled addresses were seen live anywhere.
    union_seen: set[tuple] = set()

    for spec in args.run:
        if "=" not in spec:
            parser.error(f"--run must be NAME=PATH, got {spec!r}")
        name, _, path_text = spec.partition("=")
        receipt_path = Path(path_text)
        if not receipt_path.is_absolute():
            receipt_path = ROOT / receipt_path
        receipt, census = load_census_for_run(receipt_path)

        # ---- Match every observed tuple against the compiled address list ----
        seen_counts: dict[tuple, int] = collections.Counter()
        unmatched = []
        unmapped_vocabulary = 0
        for entry in census.get("addresses", []):
            identity = live_address_identity(entry)
            if identity is None:
                unmapped_vocabulary += int(entry["count"])
                continue
            if identity in compiled_by_identity:
                seen_counts[identity] += int(entry["count"])
            else:
                unmatched.append({**entry, "resolved_identity": list(
                    "null" if p is None else p for p in identity)})
        union_seen.update(seen_counts.keys())

        # ---- Break the result down per domain (namespace + compiler role) ----
        per_domain = collections.defaultdict(
            lambda: {"compiled": 0, "seen_live": 0, "emissions": 0}
        )
        for row in compiled_rows:
            domain = f"{row['profile_namespace']}/{row['compiler_role']}"
            identity = compiled_address_identity(row)
            per_domain[domain]["compiled"] += 1
            if identity in seen_counts:
                per_domain[domain]["seen_live"] += 1
                per_domain[domain]["emissions"] += seen_counts[identity]

        # ---- Break it down per route family as well -------------------------
        per_route = collections.defaultdict(
            lambda: {"compiled": 0, "seen_live": 0, "emissions": 0}
        )
        for row in compiled_rows:
            identity = compiled_address_identity(row)
            bucket = per_route[row["route_key"]]
            bucket["compiled"] += 1
            if identity in seen_counts:
                bucket["seen_live"] += 1
                bucket["emissions"] += seen_counts[identity]

        motion = summarise_motion(receipt)
        # The ONE owner of every Active Stroke number in this recording. There
        # is deliberately no second object holding a copy of any of them: two
        # owners of the same fact can silently disagree, and an earlier version
        # of this artifact had exactly that problem. `{}` when the recording
        # carries no such block, which keeps every lookup below returning None
        # (absent) rather than 0 (measured empty).
        reach_block = census.get("active_stroke_reachability") or {}

        runs_out[name] = {
            "receipt": (
                str(receipt_path.relative_to(ROOT))
                if receipt_path.is_relative_to(ROOT) else str(receipt_path)
            ),
            "receipt_sha256": sha256_file(receipt_path),
            "target": receipt.get("run", {}).get("target", "desktop_native"),
            "run": receipt.get("run", {}),
            "revision": receipt.get("revision", {}),
            "motion": motion,
            # Pins for the raw capture files, which stay local because .run/ is
            # not tracked. Form A_LOCAL follows the FL-4694 pointer pattern: the
            # repository carries the fingerprint, never the payload.
            "local_capture_pins": {
                "form": "A_LOCAL",
                "form_rationale": (
                    "Desktop viewport captures are uncompressed PPM images of "
                    "about 11 MB each and live under .run/, which is not "
                    "tracked. The repository commits these pins plus the "
                    "regeneration command instead of the image bytes."
                ),
                "regeneration_command": (
                    "python3 scripts/proofs/"
                    "proof_fl4684_native_live_producer_motion.py "
                    "--out-dir .run/fl4684_native_live_producer"
                    if receipt.get("run", {}).get("target") == "desktop_native"
                    else "node scripts/proofs/"
                    "proof_fl4684_web_live_producer_motion.js"
                ),
                "pins": [
                    {
                        "path": c.get("path"),
                        "bytes": c.get("bytes"),
                        "sha256": c.get("sha256"),
                        "scene_phase": c.get("scene_phase", "unclassified"),
                        "bridge_frame": c.get("bridge_frame"),
                    }
                    for c in receipt.get("viewport_captures", [])
                ],
            },
            "capture_scene_phase_classification": receipt.get(
                "capture_scene_phase_classification"
            ),
            "producer_emission": {
                # NAMED LITERALLY. This counts how many times the scene-fact
                # frame identifier CHANGED. It is NOT a count of displayed
                # frames and must never be used as one, nor as a denominator:
                # the producer runs several passes per displayed frame, and in
                # one recorded desktop session this counter reached 856 while
                # the last captured gameplay frame reported frame number 119.
                # `.get` keeps a recording made by an older build readable and
                # leaves its value null rather than coercing it to zero.
                "frame_id_transitions_observed": census.get(
                    "frame_id_transitions_observed"
                ),
                "scene_facts_emitted": census["facts_emitted"],
                "address_observations": census["address_observations"],
                "distinct_live_address_tuples": census["distinct_addresses"],
                "address_table_overflow": census["address_table_overflow"],
                "facts_with_empty_route_family_mask":
                    census["facts_with_empty_route_family_mask"],
                "facts_with_role_out_of_range":
                    census["facts_with_role_out_of_range"],
                "role_counts_by_code": {
                    ROLE_NAME_BY_CODE.get(i, str(i)): v
                    for i, v in enumerate(census["role_counts"])
                },
                "route_family_counts_by_route_key": {
                    ROUTE_KEY_BY_FAMILY_BIT[i]: v
                    for i, v in enumerate(census["route_family_counts"])
                },
            },
            "compiled_address_coverage": {
                "compiled_address_total": len(compiled_rows),
                "compiled_addresses_seen_live": len(seen_counts),
                "compiled_addresses_never_seen_live":
                    len(compiled_rows) - len(seen_counts),
                "per_domain": {k: dict(v) for k, v in sorted(per_domain.items())},
                "per_route_key": {k: dict(v) for k, v in sorted(per_route.items())},
            },
            "live_tuples_with_no_compiled_address": {
                "distinct": len(unmatched),
                "emissions": sum(int(u["count"]) for u in unmatched),
                "entries": sorted(unmatched, key=lambda u: -int(u["count"]))[:40],
                "note": (
                    "Tuples the running renderer emitted that no compiled "
                    "address names. These are the reverse of a coverage gap: "
                    "the product says something the compiled artifact has no "
                    "slot for."
                ),
            },
            "emissions_outside_compiled_vocabulary": unmapped_vocabulary,
            "topology": {
                "scene_fact_topology_event_counts_by_code":
                    census["scene_fact_topology_event_counts"],
                "scene_fact_topology_events_non_none":
                    sum(census["scene_fact_topology_event_counts"][1:]),
                "active_stroke_split_total": reach_block.get("split_total"),
                "active_stroke_merge_total": reach_block.get("merge_total"),
                "active_stroke_split_frame_max": reach_block.get("split_frame_max"),
                "active_stroke_merge_frame_max": reach_block.get("merge_frame_max"),
                "frames_with_any_split": reach_block.get("frames_with_any_split"),
                "frames_with_any_merge": reach_block.get("frames_with_any_merge"),
                "active_stroke_published_snake_total":
                    reach_block.get("published_snake_total"),
                "active_stroke_published_snake_frame_max":
                    reach_block.get("published_snake_frame_max"),
            },
            # FL-4684: the same run's stroke pipeline, laid out link by link in
            # the order the renderer runs them, so the first zero names the
            # step that was already empty instead of leaving the whole stage
            # under suspicion. See build_reachability_census above.
            "reachability_census": build_reachability_census(census),
        }

    # ---- The cross-run union ------------------------------------------------
    union_per_domain = collections.defaultdict(
        lambda: {"compiled": 0, "seen_live_in_any_run": 0}
    )
    never_seen = []
    for row in compiled_rows:
        identity = compiled_address_identity(row)
        domain = f"{row['profile_namespace']}/{row['compiler_role']}"
        union_per_domain[domain]["compiled"] += 1
        if identity in union_seen:
            union_per_domain[domain]["seen_live_in_any_run"] += 1
        else:
            never_seen.append(
                {
                    "fact_id": row["fact_id"],
                    "profile_namespace": row["profile_namespace"],
                    "material_key": row["material_key"],
                    "scene_fact_role_name": row["scene_fact_role_name"],
                    "route_key": row["route_key"],
                    "lane_name": row["lane_name"],
                }
            )

    # Which runs, if any, actually achieved camera motion? Only those may be
    # cited for a motion claim.
    motion_runs = [
        name for name, r in runs_out.items() if r["motion"]["camera_motion_evidenced"]
    ]

    def sum_complete_motion_measure(key: str):
        values = [runs_out[n]["topology"].get(key) for n in motion_runs]
        if not values or any(value is None for value in values):
            return None
        return sum(values)

    motion_split_total = sum_complete_motion_measure("active_stroke_split_total")
    motion_merge_total = sum_complete_motion_measure("active_stroke_merge_total")
    motion_snake_total = sum_complete_motion_measure(
        "active_stroke_published_snake_total"
    )
    motion_aggregate_blocked = None
    if not motion_runs:
        motion_aggregate_blocked = "no_run_with_evidenced_camera_motion"
    elif any(
        value is None
        for value in (motion_split_total, motion_merge_total, motion_snake_total)
    ):
        motion_aggregate_blocked = "motion_run_missing_active_stroke_measure"

    answerable_runs = [
        name
        for name, run in runs_out.items()
        if run["reachability_census"]["census_block_present"]
        and run["reachability_census"][
            "verdict_blocked_by_missing_measure"
        ] is None
    ]
    represented_targets = {
        runs_out[name]["target"] for name in answerable_runs
    }
    targets_agree = (
        len(runs_out) >= 2
        and len(answerable_runs) == len(runs_out)
        and len(represented_targets) >= 2
        and len(
            {
                runs_out[name]["reachability_census"]["first_empty_stage"]
                for name in answerable_runs
            }
        ) == 1
    )

    out = {
        "schema": "fl4684.live_producer_evidence.v1",
        "version": 1,
        "generated_by": "scripts/measure_fl4684_live_producer_evidence.py",
        "owning_fl": ["FL-4684", "FL-4685"],
        "evidence_kind": "live_runtime_measurement",
        "what_this_is": (
            "Measured evidence of what the renderer's scene-fact producer emits "
            "during real play sessions of the shipped product, on the desktop "
            "and WebAssembly targets, together with the Active Stroke split and "
            "merge counts recorded while those sessions ran. Every number here "
            "comes from a launched product, not from reading source."
        ),
        "the_game_was_launched": True,
        "compared_against": {
            "compiled_address_reachability": str(
                REACHABILITY_ARTIFACT.relative_to(ROOT)
            ),
            "compiled_address_reachability_sha256": sha256_file(REACHABILITY_ARTIFACT),
            "compiled_address_total": len(compiled_rows),
        },
        "instrumentation": {
            "tap": "platform/fl4684_scene_fact_emission_census.h",
            "tap_call_site": (
                "engine/render/render_resolve.cpp, at the end of "
                "Fl4683WriteSceneFactFromResolverInputV1"
            ),
            "read_only": True,
            "note": (
                "The tap reads the completed scene-fact row and the render "
                "bridge's whole monotonic Active Stroke record and overwrites "
                "its previous snapshot. It writes "
                "nothing the renderer reads back, so it cannot change a pixel. "
                "It is switched off unless a run asks for it."
            ),
            "runtime_consumption_allowed": False,
            "runtime_consumption_note": (
                "The FL-4683 glyph-program loader and selector remain gated off. "
                "This measurement observes the existing producer only."
            ),
        },
        "revision": {
            "head": git_output("rev-parse", "HEAD"),
            "branch": git_output("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_paths": [
                line[3:] for line in git_output("status", "--porcelain").splitlines()
            ],
        },
        "measurement_limitations": {
            "frames_observed_counts_frame_id_transitions": (
                "The census increments its frame counter whenever the scene-fact "
                "row's frame_id differs from the previous row's. That id is the "
                "FL-4208 bridge frame. In the desktop run the counter reached 856 "
                "while the last captured gameplay frame reported bridge_frame 119, "
                "so the counter is NOT a count of displayed frames. The most "
                "likely cause is that the producer runs in more than one pass per "
                "displayed frame, so a single displayed frame can produce several "
                "id transitions. Treat frames_observed as 'frame-id transitions "
                "seen', never as 'frames rendered'. The emission totals, the "
                "distinct address tuples, the route-family counts, and the "
                "split/merge counts do not depend on this counter and are "
                "unaffected."
            ),
            "main_menu_renders_the_producer_too": (
                "The desktop main menu draws a full 3D scene behind its title, so "
                "the scene-fact producer emits while nobody is playing. Four of "
                "the five desktop captures are menu frames, and all four report "
                "bridge_frame 0 while the single gameplay capture reports 119. "
                "The desktop emission totals therefore describe mostly "
                "MENU-BACKDROP rendering rather than normal play, and the desktop "
                "run must not be cited on its own as evidence of what normal play "
                "emits. The browser run, whose driver clicks PLAY and waits for "
                "readiness before sampling, is the run that measures gameplay."
            ),
            "desktop_run_has_no_camera_motion_evidence": (
                "The desktop run contains zero consecutive gameplay-to-gameplay "
                "capture pairs, so it supports no camera-motion claim at all. Its "
                "single 82.8 percent frame difference is the main-menu-to-gameplay "
                "transition between captures 3 and 4, not a camera turn."
            ),
        },
        "runs": runs_out,
        # ------------------------------------------------------------------
        # FL-4684: the same first-zero question, answered across every run at
        # once, so a reader does not have to open each run to find out whether
        # the two targets even agree about where the pipeline emptied.
        # ------------------------------------------------------------------
        "fl4684_active_stroke_reachability": {
            "question": (
                "Both recorded sessions reported zero published Active Stroke "
                "ribbons. Which step of the stroke pipeline was already empty, "
                "and do the desktop and browser targets agree about it?"
            ),
            "how_to_read_it": (
                "`first_empty_stage_by_run` gives each run's verdict. A value "
                "of null there means either that no step was empty, or that "
                "the run's recording is missing a number and no verdict could "
                "be issued -- `verdict_blocked_by_run` distinguishes the two."
            ),
            "first_empty_stage_by_run": {
                name: run["reachability_census"]["first_empty_stage"]
                for name, run in runs_out.items()
            },
            "verdict_blocked_by_run": {
                name: run["reachability_census"][
                    "verdict_blocked_by_missing_measure"
                ]
                for name, run in runs_out.items()
            },
            # False for any run recorded before the in-engine census carried the
            # FL-4684 block. Such a run cannot answer this question at all, and
            # saying so plainly is required: an old recording must never be
            # presented as a measurement that came back empty.
            "census_block_present_by_run": {
                name: run["reachability_census"]["census_block_present"]
                for name, run in runs_out.items()
            },
            "runs_that_can_answer": answerable_runs,
            "represented_targets_that_can_answer": sorted(represented_targets),
            "targets_agree_on_first_empty_stage": targets_agree,
        },
        "union_across_runs": {
            "compiled_addresses_seen_live_in_any_run": len(union_seen),
            "compiled_addresses_never_seen_live": len(compiled_rows) - len(union_seen),
            "per_domain": {k: dict(v) for k, v in sorted(union_per_domain.items())},
            "addresses_never_seen_live": never_seen,
        },
        "fl4685_topology_during_motion": {
            "question": (
                "The FL-4685 census recorded MEASURED_NONE for stroke split and "
                "merge, but read that from recordings dominated by one static "
                "frame. Splits and merges are supposed to fire during motion. "
                "Does a session with a moving camera produce any?"
            ),
            "runs_with_evidenced_camera_motion": motion_runs,
            "runs_with_evidenced_camera_motion_count": len(motion_runs),
            "active_stroke_split_total_in_motion_runs": motion_split_total,
            "active_stroke_merge_total_in_motion_runs": motion_merge_total,
            "active_stroke_published_snake_total_in_motion_runs": motion_snake_total,
            "aggregate_blocked_by_missing_measure": motion_aggregate_blocked,
            "scene_fact_topology_events_non_none_all_runs": sum(
                r["topology"]["scene_fact_topology_events_non_none"]
                for r in runs_out.values()
            ),
            "what_a_zero_published_snake_total_means": (
                "The Active Stroke owner publishes STROKES; splits and merges "
                "are things that happen TO strokes. If no strokes were published "
                "at all, then no split or merge could have occurred for a reason "
                "that has nothing to do with whether the camera moved. That "
                "distinction is the difference between 'the detector ran and saw "
                "nothing' and 'the detector had nothing to look at'."
            ),
        },
    }

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out_path}")
    print(json.dumps(out["union_across_runs"]["per_domain"], indent=1))
    print(json.dumps(out["fl4685_topology_during_motion"], indent=1)[:1200])
    # FL-4684: print the located first zero for every run. This is the single
    # most useful line for an operator who just finished a run, so it goes to
    # the terminal rather than only into the artifact.
    print(json.dumps(out["fl4684_active_stroke_reachability"], indent=1)[:1600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
