#!/usr/bin/env python3
# =============================================================================
# fl4547_soft_stroke_walk.py
# =============================================================================
# WHAT THIS FILE IS (increment 3 of the Bénard "Active Strokes" experiment)
# -----------------------------------------------------------------------------
# We are trying to turn a hand-drawn walking figure into a small grid of Unicode
# characters that, played back as 18 frames, reads as a HUMAN WALKING — in all
# eight compass directions.
#
# Increment 2 (scripts/fl4547_arc_stroke_walk.py) ported Bénard et al. 2012
# "Active Strokes" as literally as a character grid allows.  It PASSED every
# structural test we could write (the whole figure came out as one connected
# shape, all eight directions differed, the head never flickered) and yet the
# picture was unreadable cuneiform-and-CJK soup: 407 different characters in one
# short walk.  That was a genuine, useful failure.  The falsification analysis is
# in docs/agent/2026-07-16-fl4547-temporal-glyph-sequence-optimizer/
# 2026-07-18-active-strokes-parity-falsified.md .
#
# It named two errors:
#   1. COUPLING ERROR — increment 2 demanded that neighbouring cells share the
#      EXACT lit pixels along their shared 8-pixel edge.  That is an infinite,
#      un-negotiable requirement.  It forces each cell to grab whatever glyph in
#      the ~110,000-glyph census happens to carry the right edge pixels, no
#      matter how ugly, because "readable" is only a soft preference and a soft
#      preference always loses to infinity.
#   2. SUPPLY ERROR — the candidate glyphs were the ENTIRE non-emoji census.
#      With such a huge supply, some exotic ideograph always satisfies any edge
#      pattern, so the hard requirement never has to compromise and the picture
#      never has to stay legible.
#
# THIS FILE corrects exactly those two errors and keeps everything increment 2
# proved sound:
#   KEEP  - persistent "(limb chain, position-along-the-limb)" tracks: a limb's
#           identity lives in the skeleton, and the character grid is only a
#           surface we paint the limb onto (never where identity is decided).
#   KEEP  - advect-relax transport: each track's position is smoothed around the
#           closed 18-frame walk cycle so motion is coherent frame to frame
#           (this is reused verbatim from increment 2's normalize_tracks()).
#   KEEP  - a stable, single head glyph; eight genuinely different directions.
#   CHANGE (coupling) - SOFT directional-continuity ports.  Instead of demanding
#           exact shared pixels, we gently reward a cell's glyph for LEANING
#           toward the neighbouring limb cell, scored on angle, with a finite
#           weight so legibility can win when the two disagree.
#   CHANGE (supply) - CURATED candidate supply.  Each limb may only use the
#           hand-authored stroke vocabulary for its role, plus a few extra thin,
#           line-like ("stroke-y") glyphs.  Fat, blobby ideographs are refused up
#           front.  We cap how many distinct glyphs any one limb may use.
#
# The prediction we are testing: soft ports + curated supply should collapse the
# 407-glyph soup toward the ~40-glyph legibility of increment 1, while keeping
# the better motion spine (persistent tracks + transport) underneath.
#
# Run:   python3 scripts/fl4547_soft_stroke_walk.py
# Output: a GIF + JSON manifest under the fl4547 skeleton-versions folder, a copy
#         on the Desktop, and a new row in that folder's registry.json.
# =============================================================================
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np

# The repository root is two levels up from this script (…/asciicker-Y9-2).
REPO = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO / "scripts"))

# Reuse three sibling modules rather than re-deriving their work:
#   arc  - low-level cell/geometry helpers (which cell a point lands in, etc.)
#   inc2 - the increment-2 compiler; we borrow its DATA-LOADING and TRANSPORT
#          spine (persistent tracks + advect-relax) but NOT its hard-port solve.
#   hm   - the hand-authored codebook + stroke/head target shapes + strokeness.
#   rig  - decodes the accepted per-part skeleton and renders glyphs to pixels.
import fl4547_arc_skeletal as arc
import fl4547_arc_stroke_walk as inc2
import fl4547_hemisphere_mirror_walk as hm
import fl4547_skeleton_walk as rig

# Where every generated walk (gif + manifest + registry) is recorded.
TRACK_DIR = REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
REGISTRY = TRACK_DIR / "registry.json"
DESKTOP = Path.home() / "Desktop"

# The projection surface is a 5-wide, 9-tall grid of 8x8-pixel character cells,
# 18 frames per direction.  These mirror increment 2 so the two are comparable.
GRID_W = inc2.GRID_W        # 5 columns of character cells
GRID_H = inc2.GRID_H        # 9 rows of character cells
FRAMES = inc2.FRAMES        # 18 frames in one closed walk cycle
GENERATED_DIRECTIONS = tuple(rig.DISPLAY_DIRS)   # the eight compass directions

# The 22 persistent limb-samples-per-direction and how each maps to a codebook
# role — reused unchanged from increment 2 so the skeletons line up exactly.
TRACK_SPECS = inc2.TRACK_SPECS

# -----------------------------------------------------------------------------
# SCORING WEIGHTS
# -----------------------------------------------------------------------------
# The solver adds up several costs; lower total = better.  These weights say how
# much each concern matters relative to the others.  They are the whole ball game
# for legibility, so each one is explained.

# Fidelity: how far the glyph's own orientation is from the limb's real angle.
# Small weight because "roughly the right lean" is enough — the eye forgives a
# few degrees but not a wrong CHARACTER.
LAMBDA_FIDELITY = 0.020

# Strokeness penalty: how blobby the glyph is (0 = clean thin line, higher = fat
# ideograph).  This is the single biggest legibility lever, so it is the largest
# weight.  It is what keeps "/" winning over a dense CJK block that happens to
# cover the same pixels.
LAMBDA_STROKE = 0.90

# Codebook bonus: a discount SUBTRACTED from a glyph's cost when it is one of the
# operator's hand-authored strokes for this limb.  Large enough that an authored
# stroke beats a merely-thin census glyph of similar angle, so the authored
# vocabulary dominates the picture and the glyph count stays small.
CODEBOOK_BONUS = 0.55

# Soft directional-continuity port (the corrected coupling).  Rewards a limb
# cell's glyph for pointing toward the neighbouring limb cell it connects to, so
# strokes visually flow into one another across cell borders.  FINITE and small:
# it nudges, it never dictates, so it can lose to fidelity/legibility.
LAMBDA_FLOW = 0.012

# Temporal smoothing (between consecutive frames), applied in the Viterbi
# transition cost.  These discourage the glyph from twitching frame to frame.
LAMBDA_SWITCH = 0.08              # flat cost for changing the glyph at all
LAMBDA_EXPLAINED_MOTION = 0.018   # cost for glyph-angle change the limb did NOT make
LAMBDA_MASK_CHANGE = 0.14         # cost proportional to how many pixels flipped

# CURATED SUPPLY caps (the corrected supply).
# A census glyph is only admissible as a limb candidate if it is at least this
# thin — this refuses fat ideographs before they can enter any pool.
MAX_STROKENESS_ADMIT = 0.42
# Hard ceiling on how many distinct glyphs one limb-track's pool may hold.  Small
# on purpose: a walking limb needs a handful of leans, not a dictionary.
MAX_POOL_PER_TRACK = 14
# How many census candidates to fetch per frame before filtering/bucketing.
LOCAL_K = 20
# How many orientation buckets to spread the curated census fill across, so the
# limb always has a clean stroke available at every angle it sweeps through.
ANGLE_BINS = 9

# STROKE-FRIENDLY CODEPOINT RANGES for census fill (the second corrected-supply
# gate).  The authored codebook is always allowed, but any EXTRA glyph pulled from
# the census to top up angular coverage must live in one of these ranges — the
# ranges that hold line-like marks (ASCII, Latin-1, dashes/primes, box-drawing,
# a few slash operators).  This is what keeps thin-but-alien glyphs (cuneiform,
# musical symbols, Thaana, etc.) out of the picture even when they pass the
# blobbiness test, matching Stone Story's tight ASCII-family alphabet.
_STROKE_FRIENDLY_RANGES = (
    (0x0021, 0x007E),   # printable ASCII: / \ | - _ ( ) o O v V T L etc.
    (0x00A1, 0x00FF),   # Latin-1 supplement: ¡ ¯ etc.
    (0x02C0, 0x02DF),   # spacing modifier letters (small hooks/strokes)
    (0x2010, 0x2015),   # hyphens and dashes
    (0x2018, 0x2033),   # quotes and prime marks
    (0x203E, 0x203E),   # overline ‾
    (0x2044, 0x2044),   # fraction slash
    (0x2215, 0x2216),   # division slash / set-minus
    (0x2500, 0x257F),   # box drawing (│ ─ ╱ ╲ etc.)
    (0x2571, 0x2573),   # box-drawing diagonals (subset of the above, explicit)
    (0x27CB, 0x27CD),   # mathematical rising/falling diagonals
)


def _is_stroke_friendly(scalar: int) -> bool:
    """True if a codepoint sits in a line-like block admissible for census fill."""
    return any(low <= scalar <= high for low, high in _STROKE_FRIENDLY_RANGES)

# A cell's glyph angle can flip by a large step the skeleton never commanded —
# that is a "topology transition" (the stroke visually snapping).  We price it in
# the transition cost, reusing increment 2's threshold so the number is directly
# comparable across increments.
MAX_UNEXPLAINED_ANGLE_STEP = inc2.MAX_UNEXPLAINED_ANGLE_STEP
TOPOLOGY_TRANSITION_COST = inc2.TOPOLOGY_TRANSITION_COST

# The ownership contract records, in the manifest, exactly which design promises
# THIS increment makes — so a reviewer can check claims against behaviour.
OWNERSHIP_CONTRACT = {
    "persistent_chain_slot_owner": True,          # limb identity = skeleton track
    "advect_relax_transport_owner": True,         # motion smoothed around cycle
    "soft_directional_port_owner": True,          # ports are finite, angle-based
    "hard_pixel_port_removed": True,              # the increment-2 error is gone
    "curated_codebook_supply_owner": True,        # census is filtered + capped
    "strokeness_admission_gate_owner": True,      # blobby glyphs refused up front
    "sequence_level_temporal_solver": True,       # cyclic Viterbi per track
    "cell_is_raster_destination_only": True,      # grid never decides identity
    "all_eight_source_directions_compiled_directly": True,
}


# -----------------------------------------------------------------------------
# Small helpers reused verbatim from increment 2 (kept as thin aliases so the
# reader sees exactly what this file leans on, and so a change there is visible).
# -----------------------------------------------------------------------------
track_role = inc2.track_role                     # "arm_a@1" -> "arm_a"
track_slot = inc2.track_slot                     # "arm_a@1" -> 1
track_links = inc2.track_links                   # skeleton connectivity graph
_signed_angle_delta = inc2._signed_angle_delta   # signed change folded to +-90
_folded_angle_distance = inc2._folded_angle_distance  # unsigned distance 0..90
_target_mask = inc2._target_mask                 # role -> ideal 8x8 stroke/head
_assembled_component_count = inc2._assembled_component_count  # connected pieces


def _codebook_role(track_id: str) -> str:
    """Map a persistent track id to its hand-authored codebook role.

    Most limbs map straight through (arm_a -> "arm").  Legs are special: a leg
    track is a 5-sample strip running hip->foot, and the codebook has separate
    vocabularies for the thigh ("leg_upper") and the shin ("leg_lower").  We pick
    by where the sample sits along the leg: the first half is thigh, the rest
    shin.  This is the same split increment 2 used, kept identical on purpose.
    """
    role = track_role(track_id)
    spec = TRACK_SPECS[role]
    if role.startswith("leg_"):
        fraction = track_slot(track_id) / max(1, spec["count"] - 1)
        return "leg_upper" if fraction < 0.5 else "leg_lower"
    return spec["codebook"]


def _glyph_angle(index: int, catalog) -> float:
    """The orientation (0..180 degrees) of the glyph at catalog position `index`.

    We ask hm.glyph_angle, which runs a principal-axis fit on the glyph's lit
    pixels (the direction the ink mostly points).  Passing an empty cache means
    "don't bother remembering"; callers that loop pass their own cache instead.
    """
    return hm.glyph_angle(int(catalog["masks"][index]), {})


def _cell_center(cell_xy: tuple[int, int]) -> tuple[float, float]:
    """Pixel-space centre of a character cell, used to measure flow directions.

    A cell at grid column `col`, row `row` spans 8x8 pixels, so its centre in the
    whole-figure pixel space is (col*8 + 4, row*8 + 4).
    """
    col, row = cell_xy
    return (col * 8 + 4.0, row * 8 + 4.0)


# -----------------------------------------------------------------------------
# STEP 1 — curated candidate supply (the corrected SUPPLY error)
# -----------------------------------------------------------------------------
def build_track_pool(track_id: str, angles: list[float], catalog,
                     component_cache: dict, codebook_pools: dict) -> tuple[list[int], set[int]]:
    """Assemble the small, legible glyph vocabulary ONE limb-track may draw from.

    This is where increment 2's soup is prevented.  The pool is built from two
    sources, in priority order:
      (a) the operator's hand-authored strokes for this limb's role — always
          included, because these are known-legible by construction;
      (b) a few extra census glyphs, but ONLY ones that are genuinely thin
          (strokeness below MAX_STROKENESS_ADMIT) and single-piece, spread across
          orientation buckets so every angle the limb sweeps has a clean stroke.
    The union is capped at MAX_POOL_PER_TRACK so no single limb can explode the
    glyph count.  There are NO hard pixel requirements here at all.

    Returns (pool_indices, authored_index_set).  The authored set lets the solver
    hand those glyphs the CODEBOOK_BONUS discount later.
    """
    codebook_role = _codebook_role(track_id)
    authored = set(codebook_pools.get(codebook_role, []))
    kind = TRACK_SPECS[track_role(track_id)]["kind"]

    # Heads have no meaningful angle; they are matched by round SHAPE instead, so
    # they get their authored ring glyphs plus a couple of clean round census
    # matches and nothing else.
    if kind == "head":
        pool = set(authored)
        head_target = hm.head_mask()
        for candidate in arc.local_candidates(head_target, 0, catalog, component_cache, LOCAL_K):
            pool.add(candidate["index"])
        pool.add(catalog["space_index"])   # blank must always be reachable
        return sorted(pool), authored

    # LIMBS: bucket admissible census glyphs by their own orientation and keep the
    # cleanest (least blobby) one or two per orientation bucket.  This guarantees
    # angular coverage without letting fat glyphs in.
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for fi in range(FRAMES):
        target = hm.stroke_mask(angles[fi])
        for candidate in arc.local_candidates(target, 0, catalog, component_cache, LOCAL_K):
            index = candidate["index"]
            scalar = int(catalog["scalars"][index])
            # Refuse the Control-Pictures block (U+2400..U+243F): those render as
            # literal text labels ("NUL", "SOH", ...) not as strokes.
            if 0x2400 <= scalar <= 0x243F:
                continue
            # Census fill must be a line-like mark (Stone Story alphabet family);
            # thin-but-alien glyphs (cuneiform, musical, Thaana, ...) are refused.
            if not _is_stroke_friendly(scalar):
                continue
            mask = int(catalog["masks"][index])
            penalty = hm.strokeness(mask)
            # SUPPLY GATE: fat glyphs are refused before they can ever be chosen.
            if penalty > MAX_STROKENESS_ADMIT:
                continue
            glyph_angle = _glyph_angle(index, catalog)
            bucket = min(ANGLE_BINS - 1, int(glyph_angle / (180.0 / ANGLE_BINS)))
            buckets[bucket].append((penalty, index))

    # Start the pool with every authored stroke (known-legible), then fill the
    # remaining slots with the cleanest census stroke per orientation bucket.
    pool = list(authored)
    per_bin = max(1, (MAX_POOL_PER_TRACK - len(pool)) // max(1, len(buckets) or 1))
    for bucket in sorted(buckets):
        for _penalty, index in sorted(buckets[bucket])[:per_bin]:
            if index not in pool:
                pool.append(index)
            if len(pool) >= MAX_POOL_PER_TRACK:
                break
        if len(pool) >= MAX_POOL_PER_TRACK:
            break

    # Blank is always available so an occluded/absent limb can vanish cleanly.
    if catalog["space_index"] not in pool:
        pool.append(catalog["space_index"])
    return pool, authored


# -----------------------------------------------------------------------------
# STEP 2 — per-frame flow angles (the corrected COUPLING error)
# -----------------------------------------------------------------------------
def compute_flow_angles(cells: dict, active: dict) -> dict:
    """For every track and frame, the direction it should visually flow toward.

    A limb reads as connected when each of its cells leans toward the next cell
    in the limb.  For each track we look at every skeleton link touching it
    (previous/next sample along the same limb, and joints like torso->arm), find
    the cells those neighbours occupy this frame, and average the direction from
    THIS cell's centre to those neighbour centres.  That average is the "flow
    angle": the soft target the glyph is nudged toward.  When a track has no
    active neighbour this frame we fall back to None (no nudge).
    """
    # Build, per track, the list of tracks it connects to (both link directions).
    neighbours: dict[str, list[str]] = defaultdict(list)
    for track_a, track_b in track_links():
        neighbours[track_a].append(track_b)
        neighbours[track_b].append(track_a)

    flow = {track_id: [None] * FRAMES for track_id in cells}
    for track_id, cell_series in cells.items():
        for fi in range(FRAMES):
            if not active[track_id][fi]:
                continue
            here = _cell_center(cell_series[fi])
            vectors = []
            for other in neighbours.get(track_id, []):
                if other not in cells or not active[other][fi]:
                    continue
                there = _cell_center(cells[other][fi])
                dx, dy = there[0] - here[0], there[1] - here[1]
                if dx == 0 and dy == 0:
                    continue   # same cell: no direction to flow toward
                vectors.append((dx, dy))
            if not vectors:
                continue
            # Average the neighbour directions, then fold to a 0..180 orientation
            # (a stroke and its 180-degree flip look identical, so orientation is
            # what matters, not sign).
            mx = sum(v[0] for v in vectors) / len(vectors)
            my = sum(v[1] for v in vectors) / len(vectors)
            flow[track_id][fi] = math.degrees(math.atan2(my, mx)) % 180.0
    return flow


# -----------------------------------------------------------------------------
# STEP 3 — cost of using one glyph for one cell in one frame (unary cost)
# -----------------------------------------------------------------------------
def unary_cost(index: int, angle: float, flow_angle: float | None, kind: str,
               target_mask: int, glyph_angle_value: float, catalog,
               authored: set[int]) -> float:
    """How bad is glyph `index` for this cell this frame?  Lower = better.

    Adds up: shape/angle fidelity, blobbiness penalty, the soft flow nudge, minus
    the codebook discount.  Everything is FINITE — there is no infinite gate, so
    legibility can always outvote a merely-convenient shape.  This is the heart
    of the increment-3 correction.
    """
    mask = int(catalog["masks"][index])
    if kind == "head":
        # Heads are scored by how closely their pixels match a small ring shape.
        value = rig._glyph_index_cost(target_mask, index, catalog)
    else:
        # Limbs: reward matching the real limb angle, punish blobbiness, and add
        # the soft flow nudge toward the neighbouring limb cell.
        value = LAMBDA_FIDELITY * _folded_angle_distance(glyph_angle_value, angle)
        value += LAMBDA_STROKE * hm.strokeness(mask)
        if flow_angle is not None:
            value += LAMBDA_FLOW * _folded_angle_distance(glyph_angle_value, flow_angle)
    if index in authored:
        value -= CODEBOOK_BONUS   # discount the operator's known-legible strokes
    return value


# -----------------------------------------------------------------------------
# STEP 4 — solve one limb-track's 18-frame glyph sequence (cyclic Viterbi)
# -----------------------------------------------------------------------------
def solve_track(track_id: str, angles: list[float], flow: list[float | None],
                pool: list[int], authored: set[int], catalog) -> list[int]:
    """Pick the best glyph per frame for one track, balancing fidelity vs.
    frame-to-frame smoothness, around the CLOSED walk cycle.

    "Cyclic" means frame 17 must transition smoothly back into frame 0 — the walk
    loops, so the last frame and the first are neighbours.  We solve it as a
    Viterbi dynamic program: for every possible starting glyph we find the
    cheapest path through all 18 frames and back to the start, then keep the
    globally cheapest closed loop.  Because the pool is tiny (<= ~14 glyphs) this
    brute-force-over-starts approach is fast and exact.
    """
    kind = TRACK_SPECS[track_role(track_id)]["kind"]
    # Precompute each pool glyph's own orientation once (used a lot below).
    angle_cache = {index: _glyph_angle(index, catalog) for index in pool}

    # Per-frame unary cost of every pool glyph.  All finite: no hard gate.
    unary = []
    for fi in range(FRAMES):
        target_mask = _target_mask(kind, angles[fi])
        unary.append({
            index: unary_cost(index, angles[fi], flow[fi], kind, target_mask,
                              angle_cache[index], catalog, authored)
            for index in pool
        })

    # A head has no motion; the cheapest single glyph held for all 18 frames wins.
    if kind == "head":
        totals = {index: sum(unary[fi][index] for fi in range(FRAMES)) for index in pool}
        best = min(totals, key=lambda index: (totals[index], int(catalog["scalars"][index])))
        return [best] * FRAMES

    def transition(fi: int, previous: int, current: int) -> float:
        """Cost of switching from `previous` (frame fi-1) to `current` (frame fi).

        Penalises: changing glyph at all (switch), changing the glyph's ANGLE by
        more than the limb actually rotated (unexplained motion), the sheer
        number of pixels that flipped (mask change), and any large angle snap the
        skeleton never asked for (topology transition).
        """
        mask_a, mask_b = int(catalog["masks"][previous]), int(catalog["masks"][current])
        glyph_delta = _signed_angle_delta(angle_cache[previous], angle_cache[current])
        target_delta = _signed_angle_delta(angles[(fi - 1) % FRAMES], angles[fi])
        unexplained = abs(glyph_delta - target_delta)
        topology_cost = 0.0
        if abs(glyph_delta) > MAX_UNEXPLAINED_ANGLE_STEP and abs(target_delta) < 30.0:
            topology_cost = TOPOLOGY_TRANSITION_COST + 0.10 * (
                abs(glyph_delta) - MAX_UNEXPLAINED_ANGLE_STEP
            )
        return (topology_cost
                + (LAMBDA_SWITCH if previous != current else 0.0)
                + LAMBDA_EXPLAINED_MOTION * unexplained
                + LAMBDA_MASK_CHANGE * ((mask_a ^ mask_b).bit_count() / 64.0))

    # Try every possible starting glyph; keep the cheapest closed loop overall.
    best_cycle = None
    for start in pool:
        costs = {start: unary[0][start]}
        paths = {start: [start]}
        for fi in range(1, FRAMES):
            next_costs, next_paths = {}, {}
            for current in pool:
                choice = min(
                    ((base + transition(fi, previous, current),
                      int(catalog["scalars"][previous]), previous)
                     for previous, base in costs.items()),
                )
                next_costs[current] = choice[0] + unary[fi][current]
                next_paths[current] = paths[choice[2]] + [current]
            costs, paths = next_costs, next_paths
        # Close the loop: frame 17 -> frame 0 must also be paid.
        for last, base in costs.items():
            total = base + transition(0, last, start)
            candidate = (total, paths[last])
            if best_cycle is None or candidate[0] < best_cycle[0]:
                best_cycle = candidate
    return best_cycle[1]


# -----------------------------------------------------------------------------
# STEP 5 — compile one direction: pools -> per-track solves -> paint the grid
# -----------------------------------------------------------------------------
def compile_direction(direction: str, tracks: dict, visibility: dict, catalog,
                      codebook_pools: dict, component_cache: dict,
                      anti_alias: bool = False) -> dict:
    """Derive geometry from raw transported positions, then compile.

    This is the "healthy environment" entry point: it turns the per-frame
    normalized+transported track POSITIONS into the three geometry series the
    solver needs (angle, cell, active) and hands them to compile_from_geometry.
    When skimage is unavailable we instead load those same three series straight
    from increment 2's manifest (compile_from_geometry called directly) — the
    geometry is identical either way because it is exactly increment 2's spine.
    """
    # The real limb angles (per track, per frame) from the accepted skeleton.
    angles = inc2._track_angles(tracks)
    # Which cell each track lands in per frame (with hysteresis so it does not
    # jitter between cells) — reused from the arc layer.
    cells = {
        track_id: arc.solve_cyclic_occupancy(
            [tuple(value) for value in positions], GRID_W, GRID_H
        )
        for track_id, positions in tracks.items()
    }
    # Presence, healed of one-frame holes/births (a limb should not blink).
    active = {track_id: inc2._presence_hysteresis(values)
              for track_id, values in visibility.items()}
    return compile_from_geometry(direction, angles, cells, active, catalog,
                                 codebook_pools, component_cache, anti_alias)


# Ordered limb chains (proximal -> distal), used by the anti-alias post-pass so
# it can walk a limb from body outward and smooth each interior cell into the
# line formed by its two neighbours.  Built once from the persistent-track spec.
LIMB_GROUPS = {
    role: [f"{role}@{slot}" for slot in range(spec["count"])]
    for role, spec in TRACK_SPECS.items()
    if role in ("torso", "arm_a", "arm_b", "leg_a", "leg_b") and spec["count"] >= 3
}

# A limb cell is only "kinked" (worth anti-aliasing) if its glyph points more than
# this many degrees away from the straight line through its two neighbours.  Below
# this the cell already reads as continuous and we leave it alone.
ANTI_ALIAS_KINK_DEG = 25.0


def anti_alias_diagonal_runs(sequences: dict, cells: dict, active: dict,
                             pools: dict, catalog) -> tuple[dict, int]:
    """Stone Story edge anti-aliasing: smooth each limb cell into its neighbours.

    Stone Story RPG fakes anti-aliasing by MIXING glyphs along a slanted edge so a
    diagonal run reads as one smooth slope instead of a disconnected staircase.
    We do the same, per limb, per frame: for every interior limb sample whose two
    neighbours are visible, we look at the straight line (chord) from the previous
    cell's centre to the next cell's centre — the direction the limb is actually
    heading through this cell — and, IF the currently chosen glyph leans more than
    ANTI_ALIAS_KINK_DEG away from that chord, we swap it for the glyph in this
    track's CURATED pool whose own orientation best matches the chord.  Because we
    only ever pick from the same small pool, this can never reintroduce soup; it
    just replaces a kink with the stroke that bridges the two neighbours.

    Returns (possibly-updated sequences, number_of_cells_changed).
    """
    new_sequences = {track_id: list(seq) for track_id, seq in sequences.items()}
    changed = 0
    for _role, chain in LIMB_GROUPS.items():
        for fi in range(FRAMES):
            for position in range(1, len(chain) - 1):     # interior samples only
                here, prev, nxt = chain[position], chain[position - 1], chain[position + 1]
                # All three must be present this frame for a chord to exist.
                if not (active.get(here, [False] * FRAMES)[fi]
                        and active.get(prev, [False] * FRAMES)[fi]
                        and active.get(nxt, [False] * FRAMES)[fi]):
                    continue
                current = new_sequences[here][fi]
                if current == catalog["space_index"]:
                    continue
                # Chord direction (folded to a 0..180 orientation) through this cell.
                a, b = _cell_center(cells[prev][fi]), _cell_center(cells[nxt][fi])
                dx, dy = b[0] - a[0], b[1] - a[1]
                if dx == 0 and dy == 0:
                    continue
                chord = math.degrees(math.atan2(dy, dx)) % 180.0
                current_dev = _folded_angle_distance(_glyph_angle(current, catalog), chord)
                if current_dev <= ANTI_ALIAS_KINK_DEG:
                    continue                                # already reads straight
                # Pick the curated-pool glyph whose orientation best matches the chord.
                best = None
                for index in pools.get(here, []):
                    if index == catalog["space_index"]:
                        continue
                    deviation = _folded_angle_distance(_glyph_angle(index, catalog), chord)
                    candidate = (deviation, int(catalog["scalars"][index]), index)
                    if best is None or candidate < best:
                        best = candidate
                # Only swap if it genuinely straightens the cell.
                if best is not None and best[0] + 1e-6 < current_dev and best[2] != current:
                    new_sequences[here][fi] = best[2]
                    changed += 1
    return new_sequences, changed


def compile_from_geometry(direction: str, angles: dict, cells: dict, active: dict,
                          catalog, codebook_pools: dict, component_cache: dict,
                          anti_alias: bool = False) -> dict:
    """Compile one direction from already-derived geometry (the increment-3 solve).

    `angles[track][frame]`  = the limb's real orientation (degrees, 0..180).
    `cells[track][frame]`   = the (col, row) character cell the track lands in.
    `active[track][frame]`  = whether the limb is visible (hysteresis already
                              applied — do NOT re-heal here).
    Everything below — curated pools, soft-port flow, cyclic solve, and the
    collision compositor — is the new increment-3 behaviour that replaces
    increment 2's hard-pixel-port machinery.
    """
    # Soft flow direction per track/frame (the corrected coupling).
    flow = compute_flow_angles(cells, active)

    # Build the curated pool per track, then solve its cyclic glyph sequence.
    sequences, pools, authored_by_track = {}, {}, {}
    for track_id in sorted(cells):
        pool, authored = build_track_pool(
            track_id, angles[track_id], catalog, component_cache, codebook_pools
        )
        pools[track_id] = pool
        authored_by_track[track_id] = authored
        sequences[track_id] = solve_track(
            track_id, angles[track_id], flow[track_id], pool, authored, catalog
        )

    # Stone Story edge anti-aliasing: after the per-limb solve, straighten any
    # interior limb cell that kinks away from the line through its neighbours.
    anti_alias_changed = 0
    if anti_alias:
        sequences, anti_alias_changed = anti_alias_diagonal_runs(
            sequences, cells, active, pools, catalog
        )

    # Paint each frame: drop every active track's chosen glyph into its cell.  If
    # two tracks (a joint) land in the SAME cell, a deterministic compositor picks
    # ONE glyph whose shape covers both limb angles — drawn from the SAME curated
    # supply, so a collision can never reintroduce soup.
    selected, contributors_manifest, collision_count = [], [], 0
    for fi in range(FRAMES):
        contributors = defaultdict(list)
        for track_id, sequence in sequences.items():
            if not active[track_id][fi]:
                continue
            col, row = cells[track_id][fi]
            contributors[row * GRID_W + col].append(track_id)

        frame = [catalog["space_index"]] * (GRID_W * GRID_H)
        frame_contributors = {}
        for cell in sorted(contributors):
            track_ids = contributors[cell]
            frame_contributors[str(cell)] = list(track_ids)
            if len(track_ids) == 1:
                frame[cell] = sequences[track_ids[0]][fi]
                continue
            # Joint collision: combine the colliding limbs' target shapes and pick
            # the cheapest CURATED candidate that covers the union.
            collision_count += 1
            union_pool, union_authored = set(), set()
            target = 0
            for track_id in track_ids:
                kind = TRACK_SPECS[track_role(track_id)]["kind"]
                target |= _target_mask(kind, angles[track_id][fi])
                union_pool.update(pools[track_id])
                union_authored.update(authored_by_track[track_id])
                union_authored.discard(catalog["space_index"])
            best = None
            for index in union_pool:
                if index == catalog["space_index"]:
                    continue
                mask = int(catalog["masks"][index])
                cost = (LAMBDA_STROKE * hm.strokeness(mask)
                        + 0.020 * (mask ^ target).bit_count() / 64.0)
                if index in union_authored:
                    cost -= CODEBOOK_BONUS
                candidate = (cost, int(catalog["scalars"][index]), index)
                if best is None or candidate < best:
                    best = candidate
            frame[cell] = best[2] if best else sequences[track_ids[0]][fi]
        selected.append(frame)
        contributors_manifest.append(frame_contributors)

    return {
        "direction": direction,
        "selected": selected,
        "track_ids": sorted(cells),   # the persistent track identities present
        "cells": cells,
        "angles": angles,
        "flow": flow,
        "active": active,
        "sequences": sequences,
        "pools": pools,
        "contributors": contributors_manifest,
        "collision_count": collision_count,
        "anti_alias_changed": anti_alias_changed,
    }


# -----------------------------------------------------------------------------
# STEP 6 — quality metrics (numbers a reviewer can check against the picture)
# -----------------------------------------------------------------------------
def metrics(compiled: dict, catalog) -> dict:
    """Summarise the whole eight-direction result into checkable numbers.

    The headline is unique_glyph_count: increment 2 hit 407 (soup); this should
    be a small fraction of that.  We also report how connected each frame is
    (a walking figure is a FEW pieces, not necessarily one blob — that is fine),
    head stability, and how often a glyph snapped angle without the limb moving.
    """
    components, unique = [], set()
    head_changes = topology_transitions = 0
    direction_signatures = set()
    flow_errors = []
    for _direction, result in compiled.items():
        direction_signatures.add(tuple(result["selected"][0]))
        head_sequence = result["sequences"]["head@0"]
        head_changes += sum(head_sequence[i] != head_sequence[(i + 1) % FRAMES]
                            for i in range(FRAMES))
        for track_id, sequence in result["sequences"].items():
            glyph_angles = [_glyph_angle(index, catalog) for index in sequence]
            targets = result["angles"][track_id]
            flows = result["flow"][track_id]
            for fi in range(FRAMES):
                glyph_delta = abs(_signed_angle_delta(
                    glyph_angles[(fi - 1) % FRAMES], glyph_angles[fi]))
                target_delta = abs(_signed_angle_delta(
                    targets[(fi - 1) % FRAMES], targets[fi]))
                topology_transitions += (glyph_delta > MAX_UNEXPLAINED_ANGLE_STEP
                                         and target_delta < 30.0)
                if flows[fi] is not None and sequence[fi] != catalog["space_index"]:
                    flow_errors.append(_folded_angle_distance(glyph_angles[fi], flows[fi]))
        for frame in result["selected"]:
            components.append(_assembled_component_count(frame, catalog))
            unique.update(index for index in frame if index != catalog["space_index"])
    return {
        "ownership_contract": OWNERSHIP_CONTRACT,
        "persistent_track_count_per_direction": sum(spec["count"] for spec in TRACK_SPECS.values()),
        "compiled_source_direction_count": len(compiled),
        "distinct_direction_frame0_count": len(direction_signatures),
        "unique_glyph_count": len(unique),
        "increment2_unique_glyph_count_for_reference": 407,
        "assembled_component_min": min(components),
        "assembled_component_max": max(components),
        "head_glyph_change_count": head_changes,
        "priced_topology_transition_count": int(topology_transitions),
        "mean_soft_flow_angle_error_deg": round(sum(flow_errors) / len(flow_errors), 3)
        if flow_errors else 0.0,
        "collision_compositor_count": sum(r["collision_count"] for r in compiled.values()),
        "anti_alias_changed_count": sum(r.get("anti_alias_changed", 0) for r in compiled.values()),
    }


# -----------------------------------------------------------------------------
# STEP 7 — render the eight-panel GIF + JSON manifest + registry row
# -----------------------------------------------------------------------------
def render_and_write(compiled: dict, catalog, frame_ms: int, metadata: dict):
    """Draw all eight directions into one animated GIF and record the receipts."""
    from PIL import Image, ImageDraw, ImageFont

    font_chain, font_receipt = rig._canonical_glyph_renderer()
    tile_cache = {}

    def tile(index: int, cell_px: int):
        """Render one glyph to a green-on-near-black tile at the requested size."""
        scalar = int(catalog["scalars"][index])
        if scalar not in tile_cache:
            rendered = font_chain.render(scalar)
            if rendered is None:
                raise ValueError(f"selected U+{scalar:04X} has no pinned font owner")
            rgba = Image.frombytes("RGBA", (font_chain.cell_px, font_chain.cell_px), rendered.rgba8)
            base = Image.new("RGB", rgba.size, (8, 8, 8))
            base.paste(Image.new("RGB", rgba.size, (120, 230, 120)), mask=rgba.getchannel("A"))
            tile_cache[scalar] = base
        return tile_cache[scalar].resize((cell_px, cell_px), Image.Resampling.NEAREST)

    cell_px, label_h, pad = 19, 16, 6
    panel_w, panel_h = GRID_W * cell_px, GRID_H * cell_px
    canvas_w = pad + rig.GRID_COLS * (panel_w + pad)
    canvas_h = pad + rig.GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    manifest_frames = {direction: [] for direction in rig.DISPLAY_DIRS}

    for fi in range(FRAMES):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for direction_index, direction in enumerate(rig.DISPLAY_DIRS):
            gc, gr = direction_index % rig.GRID_COLS, direction_index // rig.GRID_COLS
            x0 = pad + gc * (panel_w + pad)
            y0 = pad + gr * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            rows = []
            for row in range(GRID_H):
                chars = []
                for col in range(GRID_W):
                    index = compiled[direction]["selected"][fi][row * GRID_W + col]
                    chars.append(chr(int(catalog["scalars"][index])))
                    if index != catalog["space_index"]:
                        canvas.paste(tile(index, cell_px),
                                     (x0 + col * cell_px, y0 + label_h + row * cell_px))
                rows.append("".join(chars))
            manifest_frames[direction].append({
                "frame_index": fi,
                "rows": rows,
                "contributors": compiled[direction]["contributors"][fi],
            })
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    # Increment 4 (Stone-Story codebook + edge anti-aliasing) is labelled
    # distinctly so its GIF/manifest never collide with the increment-3 baseline.
    variant = "stonestory" if metadata.get("anti_alias") else "soft-stroke"
    name = f"{timestamp}__{variant}-{GRID_W}x{GRID_H}__walk_all_directions"
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    manifest = {
        "schema": "fl4547.soft_stroke_walk.v4" if metadata.get("anti_alias")
        else "fl4547.soft_stroke_walk.v3",
        "increment": 4 if metadata.get("anti_alias") else 3,
        "codebook": metadata.get("codebook"),
        "anti_alias": bool(metadata.get("anti_alias")),
        "status": ("experimental_increment4_operator_review_open"
                   if metadata.get("anti_alias")
                   else "experimental_increment3_operator_review_open"),
        "primary_owner": "FL-4561",
        "lineage": ["FL-4512", "FL-4547", "benard-2012-active-strokes",
                    "fl4547_arc_stroke_walk.py@increment2-falsified"],
        "correction_of": "hard-pixel-port + full-census soup (407 glyphs)",
        "name": name,
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_direction": FRAMES,
        "candidate_supply": "authored codebook + strokeness<=%.2f census, capped %d/track"
                            % (MAX_STROKENESS_ADMIT, MAX_POOL_PER_TRACK),
        "source_checkpoint": str(rig.ACCEPTED_PERPART_CHECKPOINT.relative_to(REPO)),
        "source_checkpoint_sha256": rig.ACCEPTED_PERPART_CHECKPOINT_SHA256,
        "directions": list(GENERATED_DIRECTIONS),
        "font_receipt": font_receipt,
        "descriptor_sha256": catalog["descriptor_sha256"],
        "descriptor_manifest_sha256": catalog["manifest_sha256"],
        "ownership_contract": OWNERSHIP_CONTRACT,
        "weights": {
            "LAMBDA_FIDELITY": LAMBDA_FIDELITY, "LAMBDA_STROKE": LAMBDA_STROKE,
            "CODEBOOK_BONUS": CODEBOOK_BONUS, "LAMBDA_FLOW": LAMBDA_FLOW,
            "LAMBDA_SWITCH": LAMBDA_SWITCH, "LAMBDA_EXPLAINED_MOTION": LAMBDA_EXPLAINED_MOTION,
            "LAMBDA_MASK_CHANGE": LAMBDA_MASK_CHANGE,
            "MAX_STROKENESS_ADMIT": MAX_STROKENESS_ADMIT,
            "MAX_POOL_PER_TRACK": MAX_POOL_PER_TRACK,
        },
        "normalization": metadata["normalization"],
        "source_hashes": metadata["source_hashes"],
        "spine_provenance": metadata.get("spine_provenance"),
        "metrics": metadata["metrics"],
        "frames": manifest_frames,
        "tracks": {
            direction: {
                track_id: {
                    "cells": [list(cell) for cell in compiled[direction]["cells"][track_id]],
                    "angles": [round(v, 6) for v in compiled[direction]["angles"][track_id]],
                    "glyph_scalars": [int(catalog["scalars"][index])
                                      for index in compiled[direction]["sequences"][track_id]],
                    "active": compiled[direction]["active"][track_id],
                }
                for track_id in compiled[direction]["track_ids"]
            }
            for direction in rig.DISPLAY_DIRS
        },
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    entry = {
        "name": name,
        "kind": "stonestory-walk" if metadata.get("anti_alias") else "soft-stroke-walk",
        "schema": manifest["schema"],
        "status": manifest["status"],
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_dir": FRAMES,
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(tracked_manifest.read_bytes()).hexdigest(),
        "metrics": metadata["metrics"],
    }
    registry = json.loads(REGISTRY.read_text())
    registry.append(entry)
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    return tracked_gif, desktop_gif, tracked_manifest


# -----------------------------------------------------------------------------
# STEP 8 — load the accepted skeleton, compile all eight directions, render
# -----------------------------------------------------------------------------
def load_geometry_from_manifest(path: Path) -> tuple[dict, dict]:
    """Read increment 2's manifest and return its per-direction transported spine.

    Increment 2 already ran the whole loading + persistent-track + advect-relax
    transport pipeline and recorded, per direction and per track, the three
    geometry series the solver needs: `angles`, `cells`, and `active`.  Reading
    them back is a faithful reuse of that exact spine — and it sidesteps the
    skimage/scipy skeletonization path, which is unavailable in this environment.

    Returns (geometry_by_direction, provenance) where geometry_by_direction maps
    each direction to {"angles": {...}, "cells": {...}, "active": {...}}.
    """
    manifest = json.loads(path.read_text())
    geometry = {}
    for direction, tracks in manifest["tracks"].items():
        angles = {tid: [float(v) for v in data["angles"]] for tid, data in tracks.items()}
        # Manifest cells are stored as [col, row] lists; the solver wants tuples.
        cells = {tid: [(int(c), int(r)) for c, r in data["cells"]]
                 for tid, data in tracks.items()}
        active = {tid: [bool(v) for v in data["active"]] for tid, data in tracks.items()}
        geometry[direction] = {"angles": angles, "cells": cells, "active": active}
    provenance = {
        "spine_source": "increment2 manifest",
        "spine_manifest": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "spine_schema": manifest.get("schema"),
        "normalization": manifest.get("normalization"),
        "source_hashes": manifest.get("source_hashes"),
    }
    return geometry, provenance


def compile_soft_strokes(manifest_path: Path | None = None,
                         codebook_path: Path | None = None,
                         anti_alias: bool = False) -> tuple[dict, dict, dict]:
    """Compile every direction with the soft/curated solve.

    The loading + persistent-track extraction + advect-relax transport spine is
    reused verbatim from increment 2 (that machinery was never the problem).
    Only the per-direction COMPILE is the new soft/curated one.  Two ways to get
    the spine:
      * manifest_path given  -> read increment 2's already-transported geometry
        (needed here because skimage skeletonization is unavailable);
      * manifest_path None   -> reconstruct it live from the accepted skeleton
        (works only when skimage/scipy import cleanly).

    `codebook_path` selects the authored role vocabulary (default = the v1
    codebook that produced increment 3; increment 4 passes the Stone-Story v2).
    `anti_alias` enables the Stone Story edge-anti-aliasing post-pass.
    """
    catalog = rig.load_descriptor_catalog()
    book = codebook_path if codebook_path is not None else hm.CODEBOOK
    codebook_pools, missing = hm.load_codebook(book, catalog)
    if missing:
        print(f"codebook preference skipped unavailable glyphs: {missing}")

    component_cache, compiled = {}, {}
    if manifest_path is not None:
        geometry, provenance = load_geometry_from_manifest(manifest_path)
        for direction in rig.DISPLAY_DIRS:
            geo = geometry[direction]
            compiled[direction] = compile_from_geometry(
                direction, geo["angles"], geo["cells"], geo["active"],
                catalog, codebook_pools, component_cache, anti_alias,
            )
            print(f"compiled {direction}: soft ports + curated supply "
                  f"(collisions={compiled[direction]['collision_count']}, "
                  f"anti_alias={compiled[direction]['anti_alias_changed']}) "
                  f"[spine from increment2 manifest]")
        metadata = {
            "normalization": provenance.get("normalization"),
            "source_hashes": provenance.get("source_hashes"),
            "spine_provenance": provenance,
            "codebook": str(book.relative_to(REPO)) if Path(book).is_relative_to(REPO) else str(book),
            "anti_alias": anti_alias,
            "metrics": metrics(compiled, catalog),
        }
        return compiled, catalog, metadata

    # Live reconstruction path (skimage required).
    skeletons, source_hashes, _bboxes = rig.load_accepted_perpart_skeletons(upscale=3)
    raw_by_direction, visibility_by_direction, shapes = {}, {}, {}
    for direction in rig.DISPLAY_DIRS:
        raw, visibility = inc2.raw_direction_tracks(skeletons[direction])
        raw_by_direction[direction] = raw
        visibility_by_direction[direction] = visibility
        shapes[direction] = next(iter(skeletons[direction][0].values())).shape
    normalized, normalization = inc2.normalize_tracks(raw_by_direction, shapes)
    for direction in rig.DISPLAY_DIRS:
        compiled[direction] = compile_direction(
            direction, normalized[direction], visibility_by_direction[direction],
            catalog, codebook_pools, component_cache, anti_alias,
        )
        print(f"compiled {direction}: soft ports + curated supply "
              f"(collisions={compiled[direction]['collision_count']}, "
              f"anti_alias={compiled[direction]['anti_alias_changed']}) [live skeleton]")
    metadata = {
        "normalization": normalization,
        "source_hashes": source_hashes,
        "spine_provenance": {"spine_source": "live accepted skeleton reconstruction"},
        "codebook": str(book.relative_to(REPO)) if Path(book).is_relative_to(REPO) else str(book),
        "anti_alias": anti_alias,
        "metrics": metrics(compiled, catalog),
    }
    return compiled, catalog, metadata


def run(args) -> int:
    """Entry point: compile, render, and print the receipts."""
    manifest_path = Path(args.from_manifest) if args.from_manifest else None
    if manifest_path is not None and not manifest_path.is_absolute():
        manifest_path = (REPO / manifest_path).resolve()
    codebook_path = Path(args.codebook) if args.codebook else None
    if codebook_path is not None and not codebook_path.is_absolute():
        codebook_path = (REPO / codebook_path).resolve()
    compiled, catalog, metadata = compile_soft_strokes(
        manifest_path, codebook_path, args.anti_alias)
    tracked, desktop, manifest = render_and_write(compiled, catalog, args.frame_ms, metadata)
    print(json.dumps(metadata["metrics"], indent=2, ensure_ascii=False))
    print(f"desktop: {desktop}")
    print(f"tracked: {tracked.relative_to(REPO)}")
    print(f"manifest: {manifest.relative_to(REPO)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-ms", type=int, default=90,
                        help="milliseconds per frame in the output GIF (default 90)")
    parser.add_argument("--from-manifest", type=str, default=None,
                        help="reuse the transported track spine from an increment-2 "
                             "manifest instead of reconstructing it live (required "
                             "when skimage is unavailable)")
    parser.add_argument("--codebook", type=str, default=None,
                        help="authored role-vocabulary JSON (default: v1 codebook that "
                             "produced increment 3; pass the Stone-Story v2 for increment 4)")
    parser.add_argument("--anti-alias", action="store_true",
                        help="enable the Stone Story edge-anti-aliasing post-pass "
                             "(increment 4): straighten kinked interior limb cells")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
