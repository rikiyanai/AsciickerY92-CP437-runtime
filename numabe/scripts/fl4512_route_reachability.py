#!/usr/bin/env python3
# =============================================================================
# fl4512_route_reachability.py — the authoritative FL-4512 lane reachability table
# =============================================================================
# WHAT THIS FILE IS FOR
# ---------------------
# The ASCII renderer decides which text character ("glyph") to draw in each
# character cell on screen. That decision is looked up in a pre-computed table
# called the "compiled pool". The pool has one row per "lane", where a lane is
# one specific combination of:
#
#     ProfileKey  — which material's authored look is in charge of this cell,
#                   written as "terrain:1", "terrain:5", "mesh:200", ...
#     role        — which KIND of question the lane answers
#                   (tonal / edge / direction / flow / filled / mesh_feature)
#     fact        — which specific answer inside that role
#                   (edge fact "ridge", direction fact "north_east", ...)
#
# The renderer only ever reads a lane when the frame produced the matching
# FACT for that cell. A "fact" is a measurement taken from the rendered scene —
# for example "this cell sits on a ridge" or "this cell is water and the shore
# lies to the north-east". Facts are produced by a small number of functions in
# the C++ engine. We call those functions the FACT PRODUCERS.
#
# THE QUESTION THIS FILE ANSWERS
# ------------------------------
# For a given (ProfileKey, role, fact), can the running engine EVER produce that
# fact for a cell owned by that ProfileKey?
#
#   - If yes, the lane is ADDRESSABLE. Someone must author it, explicitly switch
#     it off, or declare a genuinely missing producer. Leaving it silently empty
#     is a contract violation and the compiler must refuse to build.
#
#   - If no, the lane is NOT APPLICABLE. The route schema proves the ProfileKey
#     can never reach it, so there is nothing for a human to author and no
#     defect to report.
#
# WHY THIS MATTERS — THE MISTAKE THIS FILE EXISTS TO PREVENT
# ---------------------------------------------------------
# An earlier attempt decided lanes were "not applicable" simply because nobody
# had written them down in the authored profile file. Review rejected that as
# "inference from omission": silence in an authoring file is not evidence about
# what the engine can do. It equally rejected using the ABSENCE OF EDITOR UI as
# proof — whether a lane has a control in the ASCIIID editor is a separate
# concern (FL-4260) and says nothing about runtime routing.
#
# So every entry below is justified by a citation to the actual C++ producer,
# and, where available, by measured runtime evidence from a captured frame.
# Nothing here is inferred from what an authoring file happens to contain.
#
# WHAT THIS FILE IS *NOT* ALLOWED TO DO
# ------------------------------------
# This table states the route CONTRACT — what the engine is specified to do. It
# is not a transcript of everything the engine has ever been observed doing.
#
# That distinction has teeth, and it was learned the expensive way. An earlier
# revision found six runtime cells where two different owners disagreed about
# which material a cell belonged to, and recorded that disagreement here as a
# capability: "look, this lane really is reachable." It reproduced faithfully at
# runtime and it was completely wrong to write down. A defect cemented into a
# reachability table becomes a permanent ABI: lanes it wrongly marks reachable
# get authored, the authored content makes the defect load-bearing, and the bug
# can no longer be deleted without breaking content.
#
# The rule that follows:
#
#     Observed behaviour that contradicts the route contract is a BUG REPORT,
#     never a reachability verdict. Fix the engine, then describe the engine.
#
# Concretely, this file may only mark a lane addressable when the producer is
# SPECIFIED to deliver that fact to that ProfileKey. If runtime disagrees, the
# disagreement is recorded as a defect in the failure ledger and repaired at the
# source — it does not soften the entry below.
#
# THE ONE STRUCTURAL FACT THAT DRIVES ALMOST EVERYTHING
# ----------------------------------------------------
# Every screen cell is resolved on exactly one of two code paths, chosen by a
# tag called the "dispatch surface":
#
#   GLYPH_DISPATCH_TERRAIN_MATERIAL — ground/landscape cells. Handled at
#       engine/render/render_resolve.cpp:2587-2614.
#
#   GLYPH_DISPATCH_AUTOMAP_MESH_RGB — cells covered by a 3D model such as the
#       Pieta statue. Handled at engine/render/render_resolve.cpp:2255-2292.
#
# Those two paths produce DIFFERENT facts, and several facts are hard-wired to
# "unknown" on the path that does not own them. That is what makes reachability
# provable rather than guessed.
#
# Profiles in the "terrain" namespace own terrain-path cells; profiles in the
# "mesh" namespace own mesh-path cells.
#
# Usage as a library:
#     from fl4512_route_reachability import lane_disposition, Reach
#     reach = lane_disposition("terrain", 5, "flow", "north")
# =============================================================================

from __future__ import annotations

from typing import NamedTuple


# -----------------------------------------------------------------------------
# Fact vocabularies. A lane's position in these tuples IS its fact identity in
# the compiled pool, because compiled rows do not store the fact name. These
# must stay byte-identical to the tuples in scripts/compile_fl4512_pools.py.
# -----------------------------------------------------------------------------
EDGE_FACTS = ("flat", "rise", "fall", "ridge", "valley", "wall", "overhang")
DIRECTION_FACTS = (
    "north", "north_east", "east", "south_east",
    "south", "south_west", "west", "north_west",
)


class Reach(NamedTuple):
    """The verdict for one lane, together with the evidence behind it.

    Fields
    ------
    addressable : bool
        True  -> the running engine can produce this fact for this ProfileKey,
                 so the lane needs a real authoring disposition.
        False -> the route schema proves it cannot. The compiler will emit
                 `not_applicable` and no human action is required.

    producer_present : bool
        Only meaningful when `addressable` is True. False means the lane IS on
        a reachable route, but the specific fact VALUE has no producer in the
        engine at all — nothing ever computes it. That is the one case where a
        "blocked, missing producer" declaration is legitimate.

    reason : str
        Plain-English justification, carried into the compiled artifact so the
        decision is auditable without re-reading this file.

    citation : str
        file:line of the C++ evidence. Every verdict must have one.
    """

    addressable: bool
    producer_present: bool
    reason: str
    citation: str


# =============================================================================
# EVIDENCE BLOCK 1 — the terrain path (GLYPH_DISPATCH_TERRAIN_MATERIAL)
# =============================================================================
# render_resolve.cpp:2587  vertical_relation = ComputeFl4260VerticalRelationFromSamples(...)
# render_resolve.cpp:2590  direction         = ComputeFl4260DirectionFromSamples(...)
# render_resolve.cpp:2600  flow              = ComputeFl4260FlowFromSamples(...)
# render_resolve.cpp:2613  mesh_feature      = FL4512_MESH_FEATURE_UNKNOWN  (hard-wired)
#
# So a terrain cell can carry edge, direction and flow facts, but never a mesh
# feature fact.
#
# EVIDENCE BLOCK 2 — the mesh path (GLYPH_DISPATCH_AUTOMAP_MESH_RGB)
# =============================================================================
# render_resolve.cpp:2266  auto_vertical_relation = FL4260_VERTICAL_RELATION_UNKNOWN
# render_resolve.cpp:2282  auto_direction         = FL4260_DIRECTION_UNKNOWN
#                          (the in-source comment reads: "projected mesh direction
#                           was previously written into the terrain descent-direction
#                           lane. That alias is dead.")
# render_resolve.cpp:2283  auto_flow              = FL4260_FLOW_UNKNOWN
# render_resolve.cpp:2288  the only fact bit a mesh cell can raise is
#                          FL4512_CELL_FACT_MESH_FEATURE
#
# All three of those are unconditional constant assignments, not measurements.
# A mesh cell therefore can NEVER carry an edge, direction or flow fact.
# =============================================================================


# -----------------------------------------------------------------------------
# EDGE ROLE — the "vertical relation" fact (is this cell a ridge, a valley, ...)
# -----------------------------------------------------------------------------
# Producer: ComputeFl4260VerticalRelationFromSamples,
#           engine/render/render_resolve.cpp:301-381.
#
# It compares the average terrain height of this cell against its eight
# neighbours. It takes NO material argument and applies NO material test, so it
# runs for every terrain cell regardless of which material owns it. Every
# terrain ProfileKey can therefore receive an edge fact.
#
# BUT: read the function's return statements. It can return FLAT, RISE, FALL,
# RIDGE, VALLEY and WALL — and never OVERHANG. A repository-wide search for
# FL4260_VERTICAL_RELATION_OVERHANG finds only the enum definition
# (render_frame_input.h:312), the editor's name/parse helpers, and range-bound
# checks of the form `vr <= FL4260_VERTICAL_RELATION_OVERHANG`. Not one site
# ASSIGNS it as a measured value.
#
# So `overhang` is on a reachable route with no producer: the one shape of lane
# that a "missing producer" declaration honestly describes.
EDGE_FACTS_WITH_PRODUCER = frozenset({"flat", "rise", "fall", "ridge", "valley", "wall"})

# -----------------------------------------------------------------------------
# DIRECTION ROLE — steepest-descent compass bearing
# -----------------------------------------------------------------------------
# Producer: ComputeFl4260DirectionFromSamples,
#           engine/render/render_resolve.cpp:383-415.
#
# It finds which of the eight neighbours lies furthest below this cell. Like the
# edge producer it takes no material argument and applies no material test, and
# any of the eight bearings can win. All eight direction facts are reachable for
# every terrain ProfileKey.

# -----------------------------------------------------------------------------
# FLOW ROLE — water-to-shore bearing
# -----------------------------------------------------------------------------
# Producer: ComputeFl4260FlowFromSamples,
#           engine/render/render_resolve.cpp:445-476.
#
# This one DOES have a material test. It calls Fl4260TerminalCellMaterialVote —
# a majority vote over the cell's four samples — and returns "unknown"
# immediately unless the winner is material 0, the water material (:455-457).
#
# So the flow fact belongs to the water material, and the only ProfileKey that
# may hold a flow lane is terrain:0.
#
# ── A CORRECTION, AND WHY IT MATTERS ─────────────────────────────────────────
# An earlier version of this file said the opposite: that flow was reachable for
# EVERY terrain profile. The argument was that the engine keys the flow fact and
# the ProfileKey off different things —
#
#     the FLOW FACT comes from the four-sample MAJORITY VOTE   (:456)
#     the PROFILE KEY comes from the TOP-LEFT SAMPLE, mat[0]   (:1925-1930, :2608)
#
# — so a cell whose four samples are [grass, water, water, water] votes water,
# earns a flow fact, and is nonetheless routed to terrain:1. That divergence is
# real and it is measured: in
# docs/research/ascii/verification/fl4260/2026-06-26-rq153d-flow-runtime-proof/
# flow_after.bridge.jsonl, 112 cells carry a flow fact and their owning
# material_id splits 106 on material 0 and SIX ON MATERIAL 1.
#
# But those six cells are evidence of a DEFECT, not of a capability. Two owners
# disagreeing about which material a cell belongs to is a bug in the route
# contract. Writing that disagreement into the reachability table would have
# frozen the bug into the ABI: eight flow lanes per terrain profile would have
# become permanently "addressable", and someone would eventually have authored
# glyphs for them, making the mismatch load-bearing and undeletable.
#
# The fix belongs in the engine, not in this table. One canonical terminal-cell
# material identity — the same four-sample vote the fact producer already uses —
# now feeds ProfileKey, bridge identity, role routing and tracing alike, so the
# fact producer and the addressed profile can no longer disagree.
#
# This table therefore states the INTENDED contract: flow is addressable only
# under terrain:0. A capability claim must never be derived from a known
# ownership bug, however faithfully that bug reproduces at runtime.
FLOW_PRODUCER_MATERIAL_VOTE = 0
FLOW_OWNER_PROFILE_KEY = ("terrain", FLOW_PRODUCER_MATERIAL_VOTE)

# -----------------------------------------------------------------------------
# FILLED ROLE — no live primitive producer
# -----------------------------------------------------------------------------
# The former same-material occupancy producer was deleted because raster
# occupancy is not a structural primitive. DispatchShape6ResolverHook now leaves
# the zero-initialized filled operand invalid until a renderer producer with an
# actual primitive/region claim exists. Therefore neither namespace can reach a
# filled address in the current frame path.
#
# NOTE ON WHAT DID *NOT* DECIDE THIS. An earlier pass reached the same verdict
# from a comment in editor/asciiid.cpp saying `filled_field.0` has no live
# profile-edit owner. That reasoning was rejected, and rightly: missing editor
# support is an authoring-surface gap (FL-4260), not proof about runtime
# routing. The verdict above rests only on the runtime guard at :489.

# -----------------------------------------------------------------------------
# MESH_FEATURE ROLE — projected structural features of a 3D model
# -----------------------------------------------------------------------------
# Producer: Fl4512BuildMeshFeatureBundle, called at render_resolve.cpp:2269,
# reachable only when `auto_has_material` is true, which itself requires
# dispatch_surface == GLYPH_DISPATCH_AUTOMAP_MESH_RGB (:2258-2260).
#
# The terrain path hard-wires the mesh feature index to
# FL4512_MESH_FEATURE_UNKNOWN (:2613), so a terrain ProfileKey can never carry
# a mesh feature fact. This is the same conclusion the execution order reaches
# by a different route ("mesh structural routing belongs to the mesh namespace")
# and it is confirmed here by the runtime guards.

# -----------------------------------------------------------------------------
# TONAL ROLE — the ramp/density lanes
# -----------------------------------------------------------------------------
# Route source "global6", produced for every profile-owned cell on both paths by
# the Stage-6 finalized-global6 producer in platform/terminal_gl_present.cpp
# (see the route_source table at :5154-5166). Reachable everywhere. In practice
# every tonal lane is already authored and live, so this branch never decides a
# disputed row — it is stated for completeness.


def lane_disposition(namespace: str, material: int, role: str, fact: str | None) -> Reach:
    """Decide whether one lane can ever be reached by the running engine.

    Parameters
    ----------
    namespace : str
        "terrain" or "mesh". This is the half of the ProfileKey that selects
        which of the two resolve paths owns the cell.
    material : int
        The numeric half of the ProfileKey (1, 5, 200, ...). Present for
        completeness and for future per-material rules; no current rule needs
        it, because every reachability question is decided by the dispatch
        path, not by the material number. Deliberately NOT used to special-case
        magic material numbers.
    role : str
        "tonal", "edge", "direction", "flow", "filled" or "mesh_feature".
    fact : str | None
        The fact name for fact-indexed roles (edge/direction/flow). None for
        the single-address roles (tonal/filled/mesh_feature).

    Returns
    -------
    Reach
        addressable + producer_present + human-readable reason + C++ citation.
    """
    is_mesh = namespace == "mesh"

    # ---- tonal ------------------------------------------------------------
    if role == "tonal":
        return Reach(
            addressable=True,
            producer_present=True,
            reason="global6 route produced for every profile-owned cell on both resolve paths",
            citation="platform/terminal_gl_present.cpp:5154",
        )

    # ---- edge -------------------------------------------------------------
    if role == "edge":
        if is_mesh:
            # Mesh cells set vertical relation to UNKNOWN unconditionally.
            return Reach(
                False, False,
                "mesh resolve path hard-wires vertical_relation to UNKNOWN; "
                "a mesh cell can never carry an edge fact",
                "engine/render/render_resolve.cpp:2266",
            )
        if fact not in EDGE_FACTS_WITH_PRODUCER:
            # On the route, but nothing in the engine ever computes this value.
            return Reach(
                True, False,
                f"edge fact {fact!r} is on a reachable route but no engine site "
                f"assigns FL4260_VERTICAL_RELATION_{str(fact).upper()}; the sole "
                f"producer never returns it",
                "engine/render/render_resolve.cpp:301",
            )
        return Reach(
            True, True,
            "vertical-relation producer applies no material test and runs for "
            "every terrain cell",
            "engine/render/render_resolve.cpp:301",
        )

    # ---- direction --------------------------------------------------------
    if role == "direction":
        if is_mesh:
            return Reach(
                False, False,
                "mesh resolve path hard-wires direction to UNKNOWN; the projected "
                "mesh direction alias into this lane was deleted",
                "engine/render/render_resolve.cpp:2282",
            )
        return Reach(
            True, True,
            "steepest-descent producer applies no material test and any of the "
            "eight bearings can win",
            "engine/render/render_resolve.cpp:383",
        )

    # ---- flow -------------------------------------------------------------
    if role == "flow":
        if is_mesh:
            return Reach(
                False, False,
                "mesh resolve path hard-wires flow to UNKNOWN",
                "engine/render/render_resolve.cpp:2283",
            )
        # Terrain: the flow producer refuses any cell whose canonical material is
        # not water, so only the water ProfileKey can hold a flow lane. Every
        # other terrain profile is unreachable for flow by the route schema.
        if material != FLOW_PRODUCER_MATERIAL_VOTE:
            return Reach(
                False, False,
                f"flow producer returns UNKNOWN unless the terminal cell's "
                f"canonical material is {FLOW_PRODUCER_MATERIAL_VOTE} (water); "
                f"terrain:{material} can never own a flow fact",
                "engine/render/render_resolve.cpp:456",
            )
        return Reach(
            True, True,
            "water is the flow fact's material owner; this is the one ProfileKey "
            "the producer can claim",
            "engine/render/render_resolve.cpp:456",
        )

    # ---- filled -----------------------------------------------------------
    if role == "filled":
        return Reach(
            False, False,
            "no live renderer producer publishes an actual primitive/region "
            "claim; zero-initialized filled operands fail closed",
            "engine/render/render_resolve.cpp:1061",
        )

    # ---- mesh_feature -----------------------------------------------------
    if role == "mesh_feature":
        if is_mesh:
            return Reach(
                True, True,
                "mesh feature bundle is built for mesh-dispatch cells",
                "engine/render/render_resolve.cpp:2269",
            )
        return Reach(
            False, False,
            "terrain resolve path hard-wires the mesh feature index to "
            "FL4512_MESH_FEATURE_UNKNOWN; mesh structural routing belongs to "
            "the mesh namespace",
            "engine/render/render_resolve.cpp:2613",
        )

    raise ValueError(f"unknown role: {role!r}")
