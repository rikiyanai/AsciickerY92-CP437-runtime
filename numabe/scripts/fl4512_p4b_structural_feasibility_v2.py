#!/usr/bin/env python3
# =============================================================================
# fl4512_p4b_structural_feasibility_v2.py
# =============================================================================
# FL-4512 / FL-4547 — P4b structural feasibility, ITERATION 2 (multi-cell parts).
# -----------------------------------------------------------------------------
# WHAT THIS FILE IS (plain language)
# ----------------------------------
# We are trying to make a small ASCII figure (a walking person, a rotating
# horse) look COHERENT as it animates: the same body part should be drawn with
# glyphs that flow smoothly from one frame to the next, mirror correctly, and
# close cleanly when the walk cycle loops back to its first frame.
#
# The first experiment (v1, committed as 60ce8ed4a) modelled each body part as a
# SINGLE grid cell that changes glyph over time. Its receipt showed the fatal
# limit: a single cell cannot both match the drawing in one frame AND stay
# continuous with its neighbours, its mirror partner, and the loop edge. Both
# datasets failed the "loop cost <= median adjacent cost" gate, and the
# single-cell temporal tracks even gave back a few of the connectivity wins.
#
# The v1 receipt's own prescription for what to try next was exact:
#   "Replace one-cell track states with multi-cell connected part states.
#    Jointly score part-internal boundary ports, attachment joints, declared
#    mirror edges, and the cycle edge before expanding the benchmark."
#
# This file does exactly that. A "part" is now a CONNECTED CLUSTER of cells
# (e.g. a whole leg spanning two cells), and the optimiser chooses a joint glyph
# assignment for the entire cluster at once, so it can trade off:
#   * fidelity to the source drawing in each frame (unary term),
#   * the glyph seams INSIDE the part (internal boundary ports),
#   * the glyph seams where the part touches OTHER parts (attachment joints),
#   * frame-to-frame continuity of the whole part (temporal term),
#   * declared mirror relations between specific frames,
#   * and the wrap-around loop edge (last frame back to the first).
#
# IMPORTANT HONESTY BOUNDARY (unchanged from v1):
#   This is an OFFLINE DIAGNOSTIC. It writes NO runtime GlyphIds and performs
#   NO U6 v2 cutover. Per canon RQ-201d / RQ-201h, the runtime "delete-first"
#   structural cutover is gated behind this feasibility passing AND the schema
#   freezes. Nothing here promotes a paper claim or changes a runtime owner.
#
# WHY MULTI-CELL CAN WIN WHERE SINGLE-CELL CANNOT
#   With one cell per part, the loop edge f7->f0 must be satisfied by a single
#   glyph swap, competing directly against per-frame fidelity. With a connected
#   cluster, the part has enough shape capacity to keep its silhouette stable
#   across the loop while individual cells still track the drawing — the loop
#   cost and the fidelity cost stop fighting over the same one degree of freedom.
#
# HOW TO RUN (needs the CLIPasso virtualenv for svgpathtools + the pinned matcher)
#   .scratch/gradscii-art/.venv/bin/python \
#       scripts/fl4512_p4b_structural_feasibility_v2.py
# =============================================================================
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import ImageFont

# -----------------------------------------------------------------------------
# Reuse the committed v1 module verbatim. Importing it (rather than copying) is a
# deliberate integrity choice: the structural atlas, the per-cell descriptors,
# the candidate lattice, and every cost primitive (unary, spatial port join,
# temporal transform distance) stay BYTE-IDENTICAL to the frozen v1 baseline, so
# any measured v1-vs-v2 difference is attributable to the multi-cell model alone.
# -----------------------------------------------------------------------------
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import fl4512_p4b_structural_feasibility as p4b  # noqa: E402

# Convenient short aliases for the v1 primitives / constants we lean on.
REPO = p4b.REPO
CLIPASSO = p4b.CLIPASSO
COLS = p4b.COLS
ROWS = p4b.ROWS
TRANSFORMS = p4b.TRANSFORMS
descriptor_char = lambda d: d.char  # noqa: E731  (tiny readability helper)

SCHEMA = "fl4512.p4b_structural_feasibility.v2"

# -----------------------------------------------------------------------------
# v2 tuning knobs. The v1 lambdas are reused unchanged; v2 only ADDS two weights:
#   LAMBDA_ATTACH   — price on the seam where one authored part meets another
#                     authored part (or a non-part ambient cell) in the SAME
#                     frame. Set equal to the within-frame spatial weight because
#                     an attachment seam is physically the same kind of join as
#                     an internal seam, just across a part boundary.
#   LAMBDA_UNMATCHED— penalty when a part gains or loses a cell between frames so
#                     a cell in one frame has no transform-matched partner in the
#                     next. This discourages the optimiser from "explaining"
#                     motion by silently changing how many cells a part occupies.
# BEAM / CONFIG_M bound the within-frame joint search so the whole run stays
# deterministic and fast (parts are 1–4 cells, so K^n is small but we still cap).
# -----------------------------------------------------------------------------
LAMBDA_SPATIAL = p4b.LAMBDA_SPATIAL              # internal boundary-port weight
LAMBDA_TEMPORAL = p4b.LAMBDA_TEMPORAL            # frame-to-frame relation weight
LAMBDA_TOPOLOGY = p4b.LAMBDA_TOPOLOGY            # component/hole/ink change weight
LAMBDA_DECLARED_RELATION = p4b.LAMBDA_DECLARED_RELATION  # declared mirror weight
LAMBDA_ATTACH = 1.35                             # attachment-joint weight (new)
LAMBDA_UNMATCHED = 0.55                          # per-cell add/drop penalty (new)

BEAM = 12          # partial joint configs kept while growing a part cluster
CONFIG_M = 8       # completed joint configs kept as temporal DP states per frame
OUTER_PASSES = 3   # block-coordinate rounds that couple attached part tracks


# -----------------------------------------------------------------------------
# Small geometry helpers.
# -----------------------------------------------------------------------------
def side_between(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int] | None:
    """If grid cells `a` and `b` are 4-neighbours, return (side_from_a,
    side_from_b) using v1's side numbering (0=N,1=E,2=S,3=W). Else None.

    We reuse v1.neighbors so the side/opposite convention matches the committed
    port-join cost exactly."""
    for other, side, opposite in p4b.neighbors(a):
        if other == b:
            return side, opposite
    return None


def transform_offset(offset: tuple[int, int], transform: str) -> tuple[int, int]:
    """Map a part-relative cell offset (row_delta, col_delta) through a rigid
    transform, so we can pair a cell in frame i with the corresponding cell in
    frame i+1 AFTER the expected mirror/rotation. Grid rows increase downward.

    Reflections are self-inverse; quarter rotations follow the standard integer
    lattice map. Offsets are measured from each frame's own part anchor, so the
    anchor itself (offset (0,0)) always maps to (0,0) and is always paired."""
    dr, dc = offset
    if transform == "mirror_h":     # flip left<->right: negate the column delta
        return (dr, -dc)
    if transform == "mirror_v":     # flip up<->down: negate the row delta
        return (-dr, dc)
    if transform == "rotate_90":    # 90° CCW on (col=x, row=y): (x,y)->(-y,x)
        return (-dc, dr)
    if transform == "rotate_270":   # 90° CW: inverse of the above
        return (dc, -dr)
    return (dr, dc)                 # identity (also the fallback)


# -----------------------------------------------------------------------------
# STEP 1 — derive connected multi-cell parts from the authored single-cell anchors.
# -----------------------------------------------------------------------------
def derive_part_footprints(
    frame: p4b.FrameData,
    anchors: dict[str, tuple[int, int]],
) -> dict[str, list[tuple[int, int]]]:
    """Grow each authored anchor cell into a CONNECTED cluster of occupied cells.

    Track identity stays the semantic authority: the authored anchor
    (from the frozen v1 fixture) decides WHICH part a region belongs to. We only
    derive the part's spatial FOOTPRINT deterministically, so we never invent new
    hand-authored geometry — the footprint is a reproducible function of the
    anchors plus the frame's occupied cells.

    Method (deterministic):
      1. Every occupied cell is assigned to the nearest anchor by Manhattan grid
         distance. Ties break by (anchor track order, then row, then col) so the
         result never depends on dict iteration order.
      2. Each part footprint is the 4-connected component that CONTAINS its
         anchor, restricted to cells assigned to that same anchor. This guarantees
         the footprint is genuinely connected (no islands) and that anchors that
         lose their neighbourhood collapse back to a single cell (v2 == v1 there).
    """
    occupied = list(frame.source_masks.keys())
    ordered_tracks = list(anchors.keys())  # stable track order from the fixture

    # 1. nearest-anchor assignment ------------------------------------------------
    assignment: dict[tuple[int, int], str] = {}
    for pos in occupied:
        best_track = None
        best_key = None
        for track_id in ordered_tracks:
            ar, ac = anchors[track_id]
            dist = abs(pos[0] - ar) + abs(pos[1] - ac)
            # tie-break tuple: closest distance wins; then earliest-declared track;
            # then lexical anchor position for full determinism.
            key = (dist, ordered_tracks.index(track_id), ar, ac)
            if best_key is None or key < best_key:
                best_key = key
                best_track = track_id
        assignment[pos] = best_track

    # 2. connected component from each anchor -------------------------------------
    footprints: dict[str, list[tuple[int, int]]] = {}
    for track_id in ordered_tracks:
        anchor = anchors[track_id]
        if anchor not in frame.source_masks:
            # The fixture guarantees anchors are occupied, but stay defensive.
            footprints[track_id] = [anchor]
            continue
        component: list[tuple[int, int]] = []
        seen = {anchor}
        stack = [anchor]
        while stack:
            cur = stack.pop()
            component.append(cur)
            for other, _side, _opp in p4b.neighbors(cur):
                if (
                    other in frame.source_masks
                    and other not in seen
                    and assignment.get(other) == track_id
                ):
                    seen.add(other)
                    stack.append(other)
        footprints[track_id] = sorted(component)  # sorted = deterministic cell order
    return footprints


# -----------------------------------------------------------------------------
# STEP 2 — within-frame JOINT glyph configurations for one part cluster.
# -----------------------------------------------------------------------------
def part_configs(
    frame: p4b.FrameData,
    cells: list[tuple[int, int]],
    ambient: dict[tuple[int, int], int],
    descriptors: list,
) -> list[tuple[tuple[int, ...], float]]:
    """Return the top-`CONFIG_M` joint glyph assignments for a connected part.

    A "config" is a tuple of local candidate indices, one per cell in `cells`
    (which is already in a fixed sorted order). Its cost sums:
      * the per-cell unary fidelity cost (source cell vs candidate glyph),
      * the internal boundary-port join cost between adjacent cells INSIDE the
        part (each internal seam counted once),
      * the attachment-joint cost from part cells to their CURRENT ambient
        neighbours (other parts + non-part cells), so a config is scored in the
        context of everything it touches this frame.

    We grow configs one cell at a time with a bounded beam so the search is
    deterministic and cheap even though the raw space is K**len(cells)."""
    # Grow partial configs cell-by-cell; internal seams to already-placed cells
    # are charged as we extend, external/ambient seams are added once at the end.
    beams: list[tuple[tuple[int, ...], float]] = [((), 0.0)]
    placed: list[tuple[int, int]] = []
    for cell in cells:
        cand = frame.candidates[cell].tolist()
        cand_unary = frame.unary[cell].tolist()
        grown: list[tuple[tuple[int, ...], float, tuple[int, ...]]] = []
        for locals_so_far, cost in beams:
            for offset, glyph_local in enumerate(cand):
                add = cand_unary[offset]  # unary fidelity for this cell/glyph
                glyph = descriptors[glyph_local]
                # internal seams to cells already placed in this beam path
                for j, pcell in enumerate(placed):
                    sides = side_between(cell, pcell)
                    if sides is None:
                        continue
                    side, opposite = sides
                    add += LAMBDA_SPATIAL * p4b.spatial_pair_cost(
                        frame, cell, glyph, pcell, descriptors[locals_so_far[j]], side, opposite
                    )
                new_locals = locals_so_far + (glyph_local,)
                tie = tuple(descriptors[x].glyph_id for x in new_locals)
                grown.append((new_locals, cost + add, tie))
        # keep the cheapest `BEAM` partials; tie-break by glyph-id tuple for determinism
        grown.sort(key=lambda item: (item[1], item[2]))
        beams = [(locals_, cost) for locals_, cost, _tie in grown[:BEAM]]
        placed.append(cell)

    part_set = set(cells)
    finals: list[tuple[tuple[int, ...], float, tuple[int, ...]]] = []
    for locals_tuple, cost in beams:
        assign = dict(zip(cells, locals_tuple))
        external = 0.0
        for cell, glyph_local in zip(cells, locals_tuple):
            glyph = descriptors[glyph_local]
            for other, side, opposite in p4b.neighbors(cell):
                if other in part_set:
                    continue  # internal seam already charged during growth
                if other in ambient:
                    external += LAMBDA_ATTACH * p4b.spatial_pair_cost(
                        frame, cell, glyph, other, descriptors[ambient[other]], side, opposite
                    )
        total = cost + external
        tie = tuple(descriptors[x].glyph_id for x in locals_tuple)
        finals.append((locals_tuple, total, tie))
    finals.sort(key=lambda item: (item[1], item[2]))
    # Deduplicate identical glyph tuples that can arise from different beam paths.
    seen: set[tuple[int, ...]] = set()
    unique: list[tuple[tuple[int, ...], float]] = []
    for locals_tuple, total, _tie in finals:
        if locals_tuple in seen:
            continue
        seen.add(locals_tuple)
        unique.append((locals_tuple, total))
        if len(unique) >= CONFIG_M:
            break
    return unique


# -----------------------------------------------------------------------------
# STEP 3 — temporal cost between two whole-part configs (transform aware).
# -----------------------------------------------------------------------------
def config_temporal_cost(
    anchor_prev: tuple[int, int],
    cells_prev: list[tuple[int, int]],
    config_prev: tuple[int, ...],
    anchor_cur: tuple[int, int],
    cells_cur: list[tuple[int, int]],
    config_cur: tuple[int, ...],
    transform: str,
    descriptors: list,
) -> float:
    """Cost of moving a part from one frame's config to the next frame's config
    under the expected rigid `transform`.

    Cells are matched by their offset from each frame's part anchor, AFTER the
    transform is applied to the previous frame's offsets. Matched cells pay v1's
    exact `temporal_pair_cost` (glyph relation distance + topology change).
    Cells with no partner (the part changed shape/size) pay LAMBDA_UNMATCHED.
    The total is averaged over the number of compared slots so its scale matches
    v1's single-cell distances — keeping the loop-vs-adjacent gate comparable."""
    prev_map = {
        (cell[0] - anchor_prev[0], cell[1] - anchor_prev[1]): local
        for cell, local in zip(cells_prev, config_prev)
    }
    cur_map = {
        (cell[0] - anchor_cur[0], cell[1] - anchor_cur[1]): local
        for cell, local in zip(cells_cur, config_cur)
    }
    total = 0.0
    matched = 0
    unmatched = 0
    used_cur: set[tuple[int, int]] = set()
    for offset, local in prev_map.items():
        mapped = transform_offset(offset, transform)
        if mapped in cur_map:
            total += p4b.temporal_pair_cost(
                descriptors[local], descriptors[cur_map[mapped]], transform
            )
            matched += 1
            used_cur.add(mapped)
        else:
            unmatched += 1
    for offset in cur_map:
        if offset not in used_cur:
            unmatched += 1
    total += LAMBDA_UNMATCHED * unmatched
    denom = matched + unmatched
    return total / denom if denom else 0.0


# -----------------------------------------------------------------------------
# STEP 4 — exact cyclic dynamic program over per-frame configs (includes loop).
# -----------------------------------------------------------------------------
def cyclic_config_dp(
    frames: list[p4b.FrameData],
    anchors: list[tuple[int, int]],
    footprints: list[list[tuple[int, int]]],
    states: list[list[tuple[tuple[int, ...], float]]],
    expected: list[str],
    descriptors: list,
) -> tuple[list[int], float]:
    """Choose one config index per frame minimising
        sum_i unary(i) + sum_i temporal(i -> i+1)     (indices mod N, so the
    last->first LOOP edge is part of the objective, not an afterthought).

    Exact over the bounded config states: we pin the first frame's config, run a
    forward pass, then close the loop, and keep the best over all first choices.
    Deterministic tie-breaks by the concatenated glyph-id path."""
    n = len(frames)
    best_total = math.inf
    best_path: list[int] | None = None

    def pair(i: int, s_prev: int, s_cur: int) -> float:
        j = (i + 1) % n
        return config_temporal_cost(
            anchors[i], footprints[i], states[i][s_prev][0],
            anchors[j], footprints[j], states[j][s_cur][0],
            expected[i], descriptors,
        )

    def path_ids(path: list[int]) -> tuple[int, ...]:
        ids: list[int] = []
        for frame_index, state_index in enumerate(path):
            ids.extend(descriptors[x].glyph_id for x in states[frame_index][state_index][0])
        return tuple(ids)

    for first in range(len(states[0])):
        # costs[s] = best cost of reaching config s at the current frame, given
        # the pinned first-frame config; paths[s] = the config-index path taken.
        costs = {first: states[0][first][1]}
        paths = {first: [first]}
        for frame_index in range(1, n):
            next_costs: dict[int, float] = {}
            next_paths: dict[int, list[int]] = {}
            for cur in range(len(states[frame_index])):
                cur_unary = states[frame_index][cur][1]
                best_choice = None
                for prev, prev_cost in costs.items():
                    step = prev_cost + cur_unary + pair(frame_index - 1, prev, cur)
                    candidate = (step, path_ids(paths[prev] + [cur]), prev)
                    if best_choice is None or candidate < best_choice:
                        best_choice = candidate
                next_costs[cur] = best_choice[0]
                next_paths[cur] = paths[best_choice[2]] + [cur]
            costs, paths = next_costs, next_paths
        # close the cycle: add the last->first loop edge for every terminal state
        for last, cost in costs.items():
            total = cost + pair(n - 1, last, first)
            path = paths[last]
            candidate_ids = path_ids(path)
            if best_path is None or (total, candidate_ids) < (best_total, path_ids(best_path)):
                best_total = total
                best_path = path
    assert best_path is not None
    return best_path, best_total


# -----------------------------------------------------------------------------
# STEP 5 — declared mirror chords (non-adjacent frame pairs), config granularity.
# -----------------------------------------------------------------------------
def refine_declared_chords(
    frames: list[p4b.FrameData],
    anchors: list[tuple[int, int]],
    footprints: list[list[tuple[int, int]]],
    states: list[list[tuple[tuple[int, ...], float]]],
    chosen: list[int],
    expected: list[str],
    relation_edges: list[dict],
    descriptors: list,
) -> list[int]:
    """The cyclic DP above is exact for the CHAIN + loop. Declared mirror edges
    (e.g. horse frame 1 mirrors frame 7) are non-adjacent chords that the chain
    DP cannot see. We fold them in with a bounded, deterministic coordinate
    descent: repeatedly re-pick each frame's config to also minimise the mirror
    chord distance to its partner frame. This is explicitly a DIAGNOSTIC pass and
    is never reported as a global optimum (same honesty stance as v1)."""
    if not relation_edges:
        return chosen
    n = len(frames)
    by_frame: dict[int, list[dict]] = {i: [] for i in range(n)}
    for edge in relation_edges:
        by_frame[int(edge["a"])].append(edge)
        by_frame[int(edge["b"])].append(edge)

    result = list(chosen)
    for _sweep in range(8):
        changed = 0
        for i in range(n):
            prev = (i - 1) % n
            nxt = (i + 1) % n
            best = None
            for s in range(len(states[i])):
                cost = states[i][s][1]
                # chain neighbours (both directions) hold their current config
                cost += config_temporal_cost(
                    anchors[prev], footprints[prev], states[prev][result[prev]][0],
                    anchors[i], footprints[i], states[i][s][0],
                    expected[prev], descriptors,
                )
                cost += config_temporal_cost(
                    anchors[i], footprints[i], states[i][s][0],
                    anchors[nxt], footprints[nxt], states[nxt][result[nxt]][0],
                    expected[i], descriptors,
                )
                # declared mirror chords touching this frame
                for edge in by_frame[i]:
                    a, b = int(edge["a"]), int(edge["b"])
                    other = b if i == a else a
                    relation = edge["relation"]
                    if i == b:  # traversing the chord backwards; rotations reverse
                        relation = {"rotate_90": "rotate_270", "rotate_270": "rotate_90"}.get(
                            relation, relation
                        )
                    cost += LAMBDA_DECLARED_RELATION * config_temporal_cost(
                        anchors[i], footprints[i], states[i][s][0],
                        anchors[other], footprints[other], states[other][result[other]][0],
                        relation, descriptors,
                    )
                tie = tuple(descriptors[x].glyph_id for x in states[i][s][0])
                # Pick the cheapest config; break ties by glyph-id tuple so the
                # refinement is fully deterministic and order-independent.
                if best is None or (cost, tie) < (best[0], best[1]):
                    best = (cost, tie, s)
            if best[2] != result[i]:
                changed += 1
            result[i] = best[2]
        if changed == 0:
            break
    return result


# -----------------------------------------------------------------------------
# STEP 6 — solve every part track, coupling attached parts by block coordinate.
# -----------------------------------------------------------------------------
def solve_dataset(
    dataset: str,
    frames: list[p4b.FrameData],
    manifest: dict,
    descriptors: list,
) -> tuple[list[dict[tuple[int, int], int]], list[dict], dict]:
    """Return (labels_per_frame, per_track_receipts, gate_metrics).

    Non-part cells keep their v1 spatial-connectivity glyph (the tonal/ambient
    baseline). Authored parts are re-solved jointly. Because a part is scored
    against its ambient neighbours (attachment joints), we iterate all part
    tracks a few times (block-coordinate descent) so attached parts settle
    against each other deterministically."""
    tracks = manifest["datasets"][dataset]["tracks"]
    relation_edges = manifest["datasets"][dataset].get("relation_edges", [])

    # ambient baseline = v1 independent -> spatial refinement (unchanged path)
    independent = [p4b.independent_labels(frame) for frame in frames]
    spatial = [
        p4b.spatial_refine(frame, descriptors, labels)
        for frame, labels in zip(frames, independent)
    ]
    result = [dict(labels) for labels in spatial]  # mutated in place per part

    # Per-frame authored anchors and derived connected footprints for each track.
    anchors_by_track: dict[str, list[tuple[int, int]]] = {
        track["id"]: [tuple(p) for p in track["points"]] for track in tracks
    }
    footprints_by_track: dict[str, list[list[tuple[int, int]]]] = {}
    for track in tracks:
        per_frame_footprints: list[list[tuple[int, int]]] = []
        for frame_index, frame in enumerate(frames):
            anchors_this_frame = {
                t["id"]: anchors_by_track[t["id"]][frame_index] for t in tracks
            }
            footprints = derive_part_footprints(frame, anchors_this_frame)
            per_frame_footprints.append(footprints[track["id"]])
        footprints_by_track[track["id"]] = per_frame_footprints

    receipts: list[dict] = []
    for _outer in range(OUTER_PASSES):
        receipts = []
        for track in tracks:
            track_id = track["id"]
            anchors = anchors_by_track[track_id]
            footprints = footprints_by_track[track_id]
            relations = track.get("relations", ["source_best"] * len(frames))

            # expected chain transforms (incl. loop edge) from anchor source masks,
            # exactly as v1 derives them, so identity/mirror choices are comparable.
            expected = [
                p4b.source_transform(
                    frames[i].source_masks[anchors[i]],
                    frames[(i + 1) % len(frames)].source_masks[anchors[(i + 1) % len(frames)]],
                    relations[i],
                )
                for i in range(len(frames))
            ]

            # per-frame joint configs, scored against the CURRENT ambient (other
            # parts already placed this pass + non-part cells)
            states: list[list[tuple[tuple[int, ...], float]]] = []
            for i, frame in enumerate(frames):
                part_cells = set(footprints[i])
                ambient = {p: l for p, l in result[i].items() if p not in part_cells}
                states.append(part_configs(frame, footprints[i], ambient, descriptors))

            chosen, energy = cyclic_config_dp(
                frames, anchors, footprints, states, expected, descriptors
            )
            track_relation_edges = [e for e in relation_edges if e["track_id"] == track_id]
            if track_relation_edges:
                chosen = refine_declared_chords(
                    frames, anchors, footprints, states, chosen, expected,
                    track_relation_edges, descriptors,
                )

            # write the solved configs back into the label field
            for i in range(len(frames)):
                config = states[i][chosen[i]][0]
                for cell, local in zip(footprints[i], config):
                    result[i][cell] = local

            receipts.append(
                _track_receipt(
                    track, frames, anchors, footprints, states, chosen, expected,
                    track_relation_edges, descriptors, energy,
                )
            )

    gate = _gate_metrics(receipts)
    return result, receipts, gate


def _track_receipt(track, frames, anchors, footprints, states, chosen, expected,
                   relation_edges, descriptors, energy) -> dict:
    """Build the per-track evidence record: solved glyphs per cell, the per-edge
    adjacent transition costs, the loop-edge cost, and declared-mirror results."""
    n = len(frames)
    adjacent_costs: list[float] = []
    for i in range(n - 1):
        adjacent_costs.append(
            config_temporal_cost(
                anchors[i], footprints[i], states[i][chosen[i]][0],
                anchors[i + 1], footprints[i + 1], states[i + 1][chosen[i + 1]][0],
                expected[i], descriptors,
            )
        )
    loop_cost = config_temporal_cost(
        anchors[n - 1], footprints[n - 1], states[n - 1][chosen[n - 1]][0],
        anchors[0], footprints[0], states[0][chosen[0]][0],
        expected[n - 1], descriptors,
    )
    frame_records = []
    for i in range(n):
        config = states[i][chosen[i]][0]
        frame_records.append({
            "frame": frames[i].key,
            "anchor": list(anchors[i]),
            "cells": [list(c) for c in footprints[i]],
            "glyph_ids": [descriptors[x].glyph_id for x in config],
            "codepoints": [f"U+{descriptors[x].codepoint:04X}" for x in config],
            "chars": [descriptors[x].char for x in config],
        })
    declared = []
    for edge in relation_edges:
        a, b = int(edge["a"]), int(edge["b"])
        distance = config_temporal_cost(
            anchors[a], footprints[a], states[a][chosen[a]][0],
            anchors[b], footprints[b], states[b][chosen[b]][0],
            edge["relation"], descriptors,
        )
        declared.append({
            **edge,
            "distance": distance,
            "passes_diagnostic_threshold_0_46": distance <= 0.46,
        })
    return {
        "track_id": track["id"],
        "semantic_part": track["semantic_part"],
        "cell_counts_per_frame": [len(f) for f in footprints],
        "expected_transforms": expected,
        "cyclic_config_dp_energy": energy,
        "adjacent_transform_costs": adjacent_costs,
        "median_adjacent_transform_cost": float(np.median(adjacent_costs)) if adjacent_costs else 0.0,
        "loop_transform_cost": loop_cost,
        "loop_cost_le_median_adjacent": bool(
            adjacent_costs and loop_cost <= float(np.median(adjacent_costs))
        ),
        "declared_relation_edges": declared,
        "frames": frame_records,
    }


def _gate_metrics(receipts: list[dict]) -> dict:
    """Aggregate the per-track loop gate + declared-mirror accuracy for the
    dataset. The dataset passes the loop gate only when EVERY authored track's
    worst loop cost is no worse than its median adjacent cost — the exact
    criterion the v1 receipt named as the failing one."""
    all_pass = bool(receipts) and all(t["loop_cost_le_median_adjacent"] for t in receipts)
    declared_total = sum(len(t["declared_relation_edges"]) for t in receipts)
    declared_pass = sum(
        1 for t in receipts for e in t["declared_relation_edges"]
        if e["passes_diagnostic_threshold_0_46"]
    )
    return {
        "tracks": len(receipts),
        "loop_gate_all_tracks_pass": all_pass,
        "per_track_loop_pass": {t["track_id"]: t["loop_cost_le_median_adjacent"] for t in receipts},
        "declared_relation_edges": declared_total,
        "declared_relation_edges_passing": declared_pass,
        "declared_relation_accuracy": (declared_pass / declared_total if declared_total else None),
        "max_loop_over_median_ratio": max(
            (
                t["loop_transform_cost"] / t["median_adjacent_transform_cost"]
                if t["median_adjacent_transform_cost"] > 0 else float("inf")
                for t in receipts
            ),
            default=0.0,
        ),
    }


# -----------------------------------------------------------------------------
# Driver.
# -----------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO / "scripts" / "fixtures" / "fl4512_p4b_tracks_v1.json",
        help="authored-track fixture (v1 anchors are reused; footprints are derived)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=CLIPASSO / "artifacts" / "fl4512-p4b-structural-feasibility-v2",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=REPO / "docs" / "research" / "ascii" / "verification" / "fl4512"
        / "2026-07-17-p4b-structural-feasibility-v2" / "receipt-summary.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    # Shared, frozen v1 setup: structural atlas + per-cell candidate lattices.
    full_chars, full_feats, descriptors, atlas_receipt = p4b.build_structural_atlas()
    datasets = {
        dataset: [p4b.load_frame(key, svg, full_feats, descriptors) for key, svg in specs]
        for dataset, specs in p4b.frame_specs().items()
    }
    source_sheet = p4b.write_source_sheet(datasets, args.out)

    manifest = json.loads(args.manifest.read_text())
    if manifest.get("schema") != "fl4512.p4b_authored_tracks.v1":
        raise ValueError("unexpected authored-track manifest schema")
    if manifest.get("source_geometry") != "clipasso-p400-frozen-20260716":
        raise ValueError("manifest is not pinned to the frozen P400 geometry")

    receipt: dict = {
        "schema": SCHEMA,
        "status": "DIAGNOSTIC_ONLY_NOT_P4B_PASS",
        "supersedes_diagnostic": "fl4512.p4b_structural_feasibility.v1",
        "ownership": {
            "runtime_writes": False,
            "u6_v2_cutover": False,
            "final_owner_claim": False,
            "authored_track_manifest": str(args.manifest.relative_to(REPO)),
            "part_footprints": "derived deterministically from authored anchors (nearest-anchor + 4-connectivity)",
        },
        "parameters": {
            "grid": [COLS, ROWS],
            "top_k": p4b.TOP_K,
            "beam": BEAM,
            "config_m": CONFIG_M,
            "outer_passes": OUTER_PASSES,
            "lambda_spatial": LAMBDA_SPATIAL,
            "lambda_temporal": LAMBDA_TEMPORAL,
            "lambda_topology": LAMBDA_TOPOLOGY,
            "lambda_attach": LAMBDA_ATTACH,
            "lambda_unmatched": LAMBDA_UNMATCHED,
            "lambda_declared_relation": LAMBDA_DECLARED_RELATION,
        },
        "atlas": atlas_receipt,
        "manifest_sha256": p4b.sha256(args.manifest),
        "source_cell_sheet": {
            "path": str(source_sheet.relative_to(REPO)),
            "sha256": p4b.sha256(source_sheet),
        },
        "datasets": {},
    }

    for dataset, frames in datasets.items():
        independent = [p4b.independent_labels(frame) for frame in frames]
        spatial = [
            p4b.spatial_refine(frame, descriptors, labels)
            for frame, labels in zip(frames, independent)
        ]
        parts, track_receipts, gate = solve_dataset(dataset, frames, manifest, descriptors)

        variants = {
            "current_full_atlas": [p4b.grid_from_full(frame, full_chars) for frame in frames],
            "restricted_independent": [p4b.grid_from_labels(l, descriptors) for l in independent],
            "spatial_connectivity": [p4b.grid_from_labels(l, descriptors) for l in spatial],
            "structural_parts_v2": [p4b.grid_from_labels(l, descriptors) for l in parts],
        }
        artifacts = p4b.write_variant_artifacts(dataset, frames, variants, args.out)
        receipt["datasets"][dataset] = {
            "frames": [frame.key for frame in frames],
            "source_svgs": [
                {"path": str(frame.svg.relative_to(REPO)), "sha256": p4b.sha256(frame.svg)}
                for frame in frames
            ],
            "occupied_cells": [len(frame.source_masks) for frame in frames],
            "metrics": {
                "restricted_independent": p4b.connection_breaks(frames, independent, descriptors),
                "spatial_connectivity": p4b.connection_breaks(frames, spatial, descriptors),
                "structural_parts_v2": p4b.connection_breaks(frames, parts, descriptors),
                "loop_and_relation_gates": gate,
            },
            "tracks": track_receipts,
            "artifacts": artifacts,
        }

    receipt["deterministic_product_sha256"] = p4b.json_sha256(receipt)
    receipt_path = args.out / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")

    # Compact, human-first summary committed under the verification tree.
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "fl4512.p4b_structural_feasibility.summary.v2",
        "status": receipt["status"],
        "date": "2026-07-17",
        "supersedes": "docs/research/ascii/verification/fl4512/2026-07-16-p4b-structural-feasibility-v1/receipt-summary.json",
        "implementation": "scripts/fl4512_p4b_structural_feasibility_v2.py",
        "authored_tracks": str(args.manifest.relative_to(REPO)),
        "reproduction_command": ".scratch/gradscii-art/.venv/bin/python scripts/fl4512_p4b_structural_feasibility_v2.py",
        "runtime_writes": False,
        "u6_v2_cutover": False,
        "deterministic_product_sha256": receipt["deterministic_product_sha256"],
        "full_receipt": str(receipt_path.relative_to(REPO)),
        "beam": BEAM,
        "config_m": CONFIG_M,
    }
    for dataset in ("walk_e", "horse"):
        d = receipt["datasets"][dataset]
        summary[dataset] = {
            "restricted_independent_broken_connections": d["metrics"]["restricted_independent"]["broken_connections"],
            "spatial_broken_connections": d["metrics"]["spatial_connectivity"]["broken_connections"],
            "structural_parts_v2_broken_connections": d["metrics"]["structural_parts_v2"]["broken_connections"],
            "desired_connections": d["metrics"]["structural_parts_v2"]["desired_connections"],
            "loop_gate_all_tracks_pass": d["metrics"]["loop_and_relation_gates"]["loop_gate_all_tracks_pass"],
            "per_track_loop_pass": d["metrics"]["loop_and_relation_gates"]["per_track_loop_pass"],
            "declared_relation_accuracy": d["metrics"]["loop_and_relation_gates"]["declared_relation_accuracy"],
            "cell_counts_per_track": {t["track_id"]: t["cell_counts_per_frame"] for t in d["tracks"]},
        }
    args.summary_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    print(json.dumps({
        "receipt": str(receipt_path),
        "summary": str(args.summary_out),
        "deterministic_product_sha256": receipt["deterministic_product_sha256"],
        "walk_e": summary["walk_e"],
        "horse": summary["horse"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
