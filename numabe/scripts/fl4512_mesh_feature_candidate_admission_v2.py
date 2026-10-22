# =============================================================================
# fl4512_mesh_feature_candidate_admission_v2.py
# =============================================================================
# Scene-independent OFFLINE structural-row admission compiler helper.
#
# WHAT THIS IS (in plain words):
#   The FL-4512 engine draws a 3D world as text characters ("glyphs"). Some
#   glyphs are little line/stroke shapes drawn on an 8x8 grid of on/off pixels
#   ("ink"). Out of a big library of candidate stroke glyphs we must pick a
#   small fixed set of exactly 32 that, together, can best approximate every
#   short stroke shape a mesh silhouette/contour could project onto one cell.
#
#   This module does ONLY that offline pick. It is a pure, deterministic
#   function of a caller-supplied glyph "supply". It NEVER looks at the Pieta,
#   a camera, runtime feature masks, expected panel cells, rendered images,
#   runtime state, proof artifacts, profile revisions, or stage names. It emits
#   one 32-glyph "legal row" plus a full, independently-recomputable receipt.
#
# TWO STAGES:
#   1. GRAMMAR (fixed, input-independent): retain the legacy 330 targets and
#      add the bounded U2 runtime-domain line and pairwise-union families.
#   2. SELECTION (depends on supply): start from the legacy-safe row and run
#      deterministic exact-U7 strict single-swap descent over all signatures.
#
# BIT CONVENTION: pixel (x, y) with 0<=x,y<8 is bit index (8*y + x). This
# matches scripts/fl4512_u7_mask.py (mask_bit) and the C++ Fl4512U7Topology.
#
# ARCHITECTURE GUARDS (enforced by construction):
#   * No runtime selector, no glyph painter, no profile editor.
#   * No scene-derived input; no authored 32-glyph list input — the 32 are
#     DERIVED here from the supply, never handed in.
#   * One canonical JSON byte function (`canonical_json_bytes`); no implicit
#     defaults anywhere.
# =============================================================================

from __future__ import annotations

import hashlib
import itertools
import json
import math
import statistics
from array import array
from typing import Iterable

try:
    import numpy as np
except ImportError:  # pragma: no cover - full-supply mode fails closed.
    np = None

# The canonical u7.mask.v1 topology + score live here. We import them so this
# helper's endpoint/junction counts and cost numerator are BYTE-IDENTICAL to the
# rest of the FL-4512 pipeline. `topology_counts` performs Zhang-Suen thinning;
# per the contract we call it ONCE per grammar target and ONCE per supplied
# descriptor (validation), and NEVER inside the inner selection loop.
from fl4512_u7_mask import MASK_SIDE, mask_bit, topology_counts, u64_le_hex

# -----------------------------------------------------------------------------
# Stable identifiers and capacities. No implicit defaults: everything a caller
# or a receipt reader needs is a named constant here.
# -----------------------------------------------------------------------------
GRAMMAR_ID = "fl4512.mesh_feature_candidate_admission.v3"
RECEIPT_SCHEMA = "fl4512.mesh_feature_candidate_admission_receipt.v3"
SCORER_ID = "u7.mask.v1"
ROW_CAPACITY = 32
SIDE = MASK_SIDE  # 8

# The eight compass CONTACT pixels a legal row must collectively touch. These
# are the canonical directional landmarks on the 8x8 cell boundary/edges.
COMPASS_CONTACTS: dict[str, tuple[int, int]] = {
    "N": (3, 0),
    "NE": (7, 0),
    "E": (7, 3),
    "SE": (7, 7),
    "S": (4, 7),
    "SW": (0, 7),
    "W": (0, 4),
    "NW": (0, 0),
}
# Deterministic compass order (used everywhere we serialize a compass set).
COMPASS_ORDER: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Centre seed pixels shared by every three-arm junction target.
CENTRE_SEED: tuple[tuple[int, int], ...] = ((3, 3), (4, 3), (4, 4), (3, 4))
# Which centre pixel each compass arm grows from ("arm root").
ARM_ROOTS: dict[str, tuple[int, int]] = {
    "N": (3, 3),
    "NW": (3, 3),
    "NE": (4, 3),
    "E": (4, 3),
    "SE": (4, 4),
    "S": (4, 4),
    "SW": (3, 4),
    "W": (3, 4),
}

# Minimum squared Euclidean endpoint distance for a perimeter pair to be a chord
# target. 16 == a separation of >=4 px, so trivially short chords are excluded.
CHORD_MIN_SQ_DISTANCE = 16

# The v3 grammar is authored from the U2 raster contract. These are compiler
# inputs, never scene facts: runtime masks are held out until after selection.
U2_DISTANCE_SQ = 0.0200
U2_WORLD_STEP = 0.25
U2_SAMPLE_OFFSET = 0.5
U2_CELL_PIXELS = 8
ENDPOINT_LATTICE_DENOMINATOR = 4
INTERIOR_ENDPOINT_STEP_PIXELS = 1
RUNTIME_LINE_REPRESENTATIVE_CAP = 128
PAIRWISE_UNION_REPRESENTATIVE_CAP = 2016
CAPACITY_64 = 64
GRAMMAR_WORST_BOUND = 660
FULL_SUPPLY_BLOCK_SIZE = 512
FULL_SUPPLY_TARGET_CHUNK_SIZE = 64
FULL_SUPPLY_MEMORY_CAP_BYTES = 512 * 1024 * 1024
SELECTION_ALGORITHM = "full_validated_supply_signature_constrained_strict_single_swap_u7.v1"
COMPASS_SEED_ENDPOINTS = (
    ("north_south_full_span", (16, 0), (16, 32)),
    ("east_west_full_span", (0, 16), (32, 16)),
    ("northwest_southeast_full_span", (0, 0), (32, 32)),
    ("northeast_southwest_full_span", (32, 0), (0, 32)),
)


class AdmissionError(Exception):
    """Raised on any malformed input or failed validation.

    `reason` is one of the STABLE typed reason codes below, so tests and callers
    can branch on the exact failure class without string-matching prose.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# Stable typed reason codes. Never renumber/rename an existing one.
REASON_SUPPLY_EMPTY = "supply_empty"
REASON_SUPPLY_BELOW_CAPACITY = "supply_below_capacity"
REASON_SUPPLY_MISSING_FIELD = "supply_missing_field"
REASON_SUPPLY_ZERO_MASK = "supply_zero_mask"
REASON_SUPPLY_DUPLICATE_GLYPH_ID = "supply_duplicate_glyph_id"
REASON_SUPPLY_TOPOLOGY_INCONSISTENT = "supply_topology_inconsistent"
REASON_SUPPLY_BOOL_FACT = "supply_bool_fact"
REASON_SUPPLY_NON_INTEGER = "supply_non_integer"
REASON_SUPPLY_NEGATIVE = "supply_negative"
REASON_SUPPLY_OVERFLOW = "supply_overflow"
REASON_SCHEMA_MISMATCH = "schema_mismatch"
REASON_GRAMMAR_ID_MISMATCH = "grammar_id_mismatch"
REASON_SCORER_ID_MISMATCH = "scorer_id_mismatch"
REASON_SUPPLY_COUNT_MISMATCH = "supply_count_mismatch"
REASON_DISTINCT_SIGNATURE_COUNT_MISMATCH = "distinct_signature_count_mismatch"
REASON_SELECTED_EMPTY_DESCRIPTOR = "selected_empty_descriptor"
REASON_MISSING_COMPASS_CONTACT = "missing_compass_contact"
REASON_GRAMMAR_HASH_MISMATCH = "grammar_hash_mismatch"
REASON_SUPPLY_HASH_MISMATCH = "supply_hash_mismatch"
REASON_SELECTED_ORDER_MISMATCH = "selected_order_mismatch"
REASON_DESCRIPTOR_HASH_MISMATCH = "descriptor_hash_mismatch"
REASON_NEAREST_WINNER_MISMATCH = "nearest_winner_mismatch"
REASON_NEAREST_SCORE_MISMATCH = "nearest_score_mismatch"
REASON_AGGREGATE_METRIC_MISMATCH = "aggregate_metric_mismatch"
REASON_BOUNDARY_COVERAGE_MISMATCH = "boundary_coverage_mismatch"
REASON_CONTENT_HASH_MISMATCH = "content_hash_mismatch"
REASON_CAPACITY_MISMATCH = "capacity_mismatch"


# =============================================================================
# One canonical JSON byte function (the ONLY serializer in this module).
# =============================================================================
def canonical_json_bytes(obj) -> bytes:
    """Deterministic UTF-8 bytes for any JSON-able object.

    sort_keys makes dict order irrelevant; the compact separators make the byte
    stream stable across Python versions. Every hash and every stored blob in
    this module goes through this one function, so receipts are reproducible.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    """Hex sha256 of raw bytes (used for grammar/supply/content/descriptor)."""
    return hashlib.sha256(data).hexdigest()


# Exact-integer bounds for supply facts. These mirror the runtime ABI: mask8 is
# a 64-bit ink field, glyph_id fits in 32 bits, and topology counts are byte
# sized. Anything outside these is an overflow — rejected, never silently wrapped.
MASK8_MAX = (1 << 64) - 1
GLYPH_ID_MAX = (1 << 32) - 1
COUNT_MAX = 255


def _require_exact_int(value, field: str, gid) -> int:
    """Return `value` iff it is a *true* Python int (not a bool, not a float,
    not a numpy scalar, not a bool-in-disguise). Booleans are a subclass of int
    in Python, so `isinstance(True, int)` is True — we reject them explicitly so
    a stray True/False fact can never masquerade as 1/0. No implicit coercion:
    we never call int() on the value, which would silently accept floats/strings.
    """
    # bool must be checked BEFORE int because bool is an int subclass.
    if isinstance(value, bool):
        raise AdmissionError(REASON_SUPPLY_BOOL_FACT, f"{field} gid={gid}")
    if type(value) is not int:
        raise AdmissionError(REASON_SUPPLY_NON_INTEGER,
                             f"{field}={value!r} gid={gid}")
    return value


# =============================================================================
# Geometry primitives.
# =============================================================================
def _mask_of(cells: Iterable[tuple[int, int]]) -> int:
    """Set bit (8*y + x) for each in-bounds pixel; return the 64-bit ink mask."""
    mask = 0
    for x, y in cells:
        if 0 <= x < SIDE and 0 <= y < SIDE:
            mask |= 1 << (SIDE * y + x)
    return mask


def bresenham(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    """Inclusive integer Bresenham line from `a` to `b`, all-octant.

    DETERMINISTIC TIE RULE (the one and only rule this module uses): we use the
    classic generalized integer form with dx=|x1-x0|, dy=-|y1-y0|, err=dx+dy.
    On each iteration e2 = 2*err; if e2 >= dy we step x, if e2 <= dx we step y.
    When BOTH hold (the exact-diagonal tie) BOTH steps are taken in the SAME
    iteration, i.e. a diagonal move — never a pair of axis moves. This makes the
    rasterized pixel set a pure function of the ordered endpoints; combined with
    the mask canonicalization below it is independent of endpoint order.
    """
    x0, y0 = a
    x1, y1 = b
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    out: list[tuple[int, int]] = []
    cx, cy = x0, y0
    while True:
        out.append((cx, cy))
        if cx == x1 and cy == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            cx += sx
        if e2 <= dx:
            err += dx
            cy += sy
    return tuple(out)


def perimeter_cells() -> tuple[tuple[int, int], ...]:
    """The 28 distinct boundary pixels in clockwise order.

    top (0,0)..(7,0)=8, right (7,1)..(7,7)=7, bottom (6,7)..(0,7)=7,
    left (0,6)..(0,1)=6  ->  8+7+7+6 = 28 distinct cells forming one loop.
    """
    cells: list[tuple[int, int]] = []
    cells += [(x, 0) for x in range(0, SIDE)]          # top, left->right
    cells += [(SIDE - 1, y) for y in range(1, SIDE)]   # right, top->bottom
    cells += [(x, SIDE - 1) for x in range(SIDE - 2, -1, -1)]  # bottom, r->l
    cells += [(0, y) for y in range(SIDE - 2, 0, -1)]  # left, bottom->top
    return tuple(cells)


def _boundary_contacts(mask: int) -> tuple[str, ...]:
    """Compass classes whose CONTACT pixel is inked in `mask` (compass order)."""
    return tuple(
        name for name in COMPASS_ORDER
        if mask_bit(mask, *COMPASS_CONTACTS[name])
    )


# =============================================================================
# GRAMMAR construction (input-independent).
# =============================================================================
def _target_record(family: str, endpoints, mask: int) -> dict:
    """Build one canonical target record with its topology + contacts.

    `topology_counts` (Zhang-Suen thinning) is called here ONCE per target; it
    is never called in the inner selection loop. Endpoints are stored as the
    already-canonical form the caller passed (perimeter pair, or compass subset).
    """
    _skeleton, endpoints_count, junctions_count = topology_counts(mask)
    return {
        "family": family,
        "endpoints": endpoints,
        "mask8_hex": u64_le_hex(mask),
        "endpoint_count": int(endpoints_count),
        "junction_count": int(junctions_count),
        "boundary_contacts": list(_boundary_contacts(mask)),
    }


def _build_legacy_grammar() -> dict:
    """Construct the full, deduplicated, sorted grammar and its hash.

    Returns a dict with:
      grammar_id, grammar_hash, target_count, and `targets` (list of records
      sorted by unsigned mask value). `_targets_runtime` carries the same
      records plus the integer mask, used by selection (not serialized).
    """
    # by_mask maps an unsigned 64-bit mask -> its canonical record. Dedup is by
    # mask value across BOTH families; on collision we keep the FIRST inserted
    # record in this deterministic order: chords (sorted) then three-arm
    # (sorted). This makes the grammar a pure function of nothing but the rules.
    by_mask: dict[int, tuple[str, list, int]] = {}

    # ---- Chord family: every perimeter pair with squared distance >= 16. ----
    perim = perimeter_cells()
    for i in range(len(perim)):
        for j in range(i + 1, len(perim)):
            (x0, y0), (x1, y1) = perim[i], perim[j]
            sq = (x1 - x0) ** 2 + (y1 - y0) ** 2
            if sq < CHORD_MIN_SQ_DISTANCE:
                continue
            mask = _mask_of(bresenham(perim[i], perim[j]))
            # Canonical endpoints: the two boundary pixels, sorted.
            eps = sorted([list(perim[i]), list(perim[j])])
            if mask not in by_mask:
                by_mask[mask] = ("chord", eps, mask)

    # ---- Three-arm family: every 3-subset of the 8 compass directions. ------
    centre_mask = _mask_of(CENTRE_SEED)
    for subset in itertools.combinations(COMPASS_ORDER, 3):
        mask = centre_mask
        for direction in subset:
            arm = bresenham(ARM_ROOTS[direction], COMPASS_CONTACTS[direction])
            mask |= _mask_of(arm)
        eps = sorted(subset)  # the three compass names, sorted
        if mask not in by_mask:
            by_mask[mask] = ("three_arm", eps, mask)

    # Sort by unsigned mask value and materialize the canonical records.
    ordered_masks = sorted(by_mask.keys())
    targets: list[dict] = []
    runtime: list[dict] = []
    for mask in ordered_masks:
        family, endpoints, _m = by_mask[mask]
        record = _target_record(family, endpoints, mask)
        targets.append(record)
        rt = dict(record)
        rt["_mask8"] = mask
        runtime.append(rt)

    grammar_hash = _sha256_hex(canonical_json_bytes({
        "grammar_id": GRAMMAR_ID,
        "targets": targets,
    }))
    return {
        "grammar_id": GRAMMAR_ID,
        "grammar_hash": grammar_hash,
        "target_count": len(targets),
        "targets": targets,
        "_targets_runtime": runtime,
    }


# =============================================================================
# SUPPLY validation.
# =============================================================================
def _validate_supply(supply) -> list[dict]:
    """Canonicalize + validate the caller's descriptor supply.

    Each descriptor is a dict with keys glyph_id, mask8, endpoint_count,
    junction_count. We reject missing/empty/duplicate/zero-mask and any
    descriptor whose declared endpoint/junction do NOT match a topology recount
    (`topology_counts`) of its mask. Topology is recounted ONCE here, never in
    the selection loop. Returns descriptors sorted by increasing glyph_id.
    """
    items = list(supply)
    if not items:
        raise AdmissionError(REASON_SUPPLY_EMPTY, "no descriptors supplied")

    seen_ids: set[int] = set()
    validated: list[dict] = []
    for raw in items:
        for key in ("glyph_id", "mask8", "endpoint_count", "junction_count"):
            if key not in raw or raw[key] is None:
                raise AdmissionError(REASON_SUPPLY_MISSING_FIELD, key)
        # Strict exact-integer facts: no bool, no float/string, no coercion.
        gid_probe = raw["glyph_id"]
        glyph_id = _require_exact_int(gid_probe, "glyph_id", gid_probe)
        mask8 = _require_exact_int(raw["mask8"], "mask8", glyph_id)
        endpoint_count = _require_exact_int(raw["endpoint_count"], "endpoint_count", glyph_id)
        junction_count = _require_exact_int(raw["junction_count"], "junction_count", glyph_id)
        # Non-negative.
        if glyph_id < 0 or mask8 < 0 or endpoint_count < 0 or junction_count < 0:
            raise AdmissionError(REASON_SUPPLY_NEGATIVE, f"gid={glyph_id}")
        # Bounds / overflow (never wrap silently).
        if (glyph_id > GLYPH_ID_MAX or mask8 > MASK8_MAX or
                endpoint_count > COUNT_MAX or junction_count > COUNT_MAX):
            raise AdmissionError(REASON_SUPPLY_OVERFLOW, f"gid={glyph_id}")
        if mask8 == 0:
            raise AdmissionError(REASON_SUPPLY_ZERO_MASK, f"gid={glyph_id}")
        if glyph_id in seen_ids:
            raise AdmissionError(REASON_SUPPLY_DUPLICATE_GLYPH_ID, f"gid={glyph_id}")
        seen_ids.add(glyph_id)
        # One-time consistency recount: declared counts MUST match the thinner.
        _skeleton, recount_ep, recount_jn = topology_counts(mask8)
        if endpoint_count != recount_ep or junction_count != recount_jn:
            raise AdmissionError(
                REASON_SUPPLY_TOPOLOGY_INCONSISTENT,
                f"gid={glyph_id} declared=({endpoint_count},{junction_count}) "
                f"recount=({recount_ep},{recount_jn})",
            )
        validated.append({
            "glyph_id": glyph_id,
            "mask8": mask8,
            "endpoint_count": endpoint_count,
            "junction_count": junction_count,
        })

    validated.sort(key=lambda d: d["glyph_id"])
    return validated


def _descriptor_hash(descriptor: dict) -> str:
    """Stable hash of one validated descriptor (mask stored as LE hex)."""
    return _sha256_hex(canonical_json_bytes({
        "glyph_id": descriptor["glyph_id"],
        "mask8_hex": u64_le_hex(descriptor["mask8"]),
        "endpoint_count": descriptor["endpoint_count"],
        "junction_count": descriptor["junction_count"],
    }))


def _supply_hash(validated: list[dict]) -> str:
    """Hash of the whole canonicalized supply (order-invariant by construction:
    `validated` is already sorted by glyph_id)."""
    return _sha256_hex(canonical_json_bytes(
        [{"glyph_id": d["glyph_id"], "mask8_hex": u64_le_hex(d["mask8"]),
          "endpoint_count": d["endpoint_count"],
          "junction_count": d["junction_count"]} for d in validated]))


# =============================================================================
# The exact u7.mask.v1 cost — precomputed-topology form (NO thinning here).
# =============================================================================
def _cost(target_mask: int, target_ep: int, target_jn: int,
          cand_mask: int, cand_ep: int, cand_jn: int) -> int:
    """u7.mask.v1 integer numerator using PRE-COMPUTED topology counts.

    70*hamming + 15*|endpoint_delta| + 15*|junction_delta|. Identical value to
    fl4512_u7_mask.score_num, but this variant takes the endpoint/junction
    counts as arguments (already recounted once) so the inner selection loop
    performs zero thinning — only a popcount and two abs-diffs.
    """
    hamming = (target_mask ^ cand_mask).bit_count()
    return 70 * hamming + 15 * abs(target_ep - cand_ep) + 15 * abs(target_jn - cand_jn)


# =============================================================================
# LEGACY bound seed selection (32 rounds). This is not the v3 row owner.
# =============================================================================
def _select_legacy(grammar_runtime: list[dict], validated: list[dict]):
    """Pick exactly ROW_CAPACITY glyphs.

    Returns (selected, per_target_nearest, distinct_signature_count).

    COMPLEXITY (the honest truth). This is the exact greedy facility selection.
    It STILL performs the full objective scan: at each of ROW_CAPACITY (=32)
    rounds it evaluates every distinct signature against all T (=330) grammar
    targets — i.e. O(row_capacity * distinct_signatures * targets). That scan is
    NOT removed; it is the selector, and on the universal ~109k-entry Unicode
    catalog (~99k distinct signatures) it is a ~133-167 s one-time atlas/compiler
    transaction (measured). Two exact refinements make that single pass tractable
    WITHOUT changing the result (byte-identical to the naive scalar oracle):

      A. SIGNATURE SCAN. A candidate's cost to every target depends ONLY on its
         (mask8, endpoint_count, junction_count) signature, never on glyph_id
         except the final tie-break. Same-signature candidates have identical
         (worst, sum); the lower glyph_id always wins among them. So the scan
         ranges over DISTINCT signatures (<= supply), each carrying its lowest
         *unselected* glyph_id. argmin over all candidates of (worst, sum, gid)
         == argmin over signatures of (worst_sig, sum_sig, lowest_gid_sig).

      B. NO REDUNDANT RE-WORK inside the objective scan. The per-signature cost
         vector to the targets is computed ONCE up front (into a uint16 array
         row); the 32 objective-scan rounds READ it instead of RE-computing the
         u7 score every round. The per-target nearest OWNER is likewise tracked
         incrementally, replacing a separate per-round owner rescan. Neither of
         these removes the objective scan itself — they remove only its former
         per-round score-recompute and owner-rescan.
    """
    if len(validated) < ROW_CAPACITY:
        raise AdmissionError(
            REASON_SUPPLY_BELOW_CAPACITY,
            f"have {len(validated)}, need {ROW_CAPACITY}",
        )

    targets = grammar_runtime
    n_targets = len(targets)
    t_mask = [t["_mask8"] for t in targets]
    t_ep = [t["endpoint_count"] for t in targets]
    t_jn = [t["junction_count"] for t in targets]

    # Group candidates by mask signature. `validated` is sorted by glyph_id, so
    # each mask's gid list is already ascending. (ep/jn are a function of mask —
    # validation guarantees consistency — so the mask alone keys the signature.)
    mask_to_gids: dict[int, list[int]] = {}
    mask_desc: dict[int, tuple[int, int]] = {}
    for d in validated:
        m = d["mask8"]
        lst = mask_to_gids.get(m)
        if lst is None:
            mask_to_gids[m] = [d["glyph_id"]]
            mask_desc[m] = (d["endpoint_count"], d["junction_count"])
        else:
            lst.append(d["glyph_id"])
    masks = sorted(mask_to_gids.keys())
    n_masks = len(masks)

    # Precompute each distinct signature's cost vector to every target ONCE, as
    # a per-signature uint16 array row (max cost 70*64+15*255+15*255 < 65535).
    # The 32 objective-scan rounds READ this; they no longer re-compute the u7
    # score each round. (The objective scan over signatures x targets remains.)
    cost_rows: list = []
    for m in masks:
        cep, cjn = mask_desc[m]
        cost_rows.append(array("H", (
            70 * (t_mask[ti] ^ m).bit_count()
            + 15 * abs(t_ep[ti] - cep) + 15 * abs(t_jn[ti] - cjn)
            for ti in range(n_targets))))

    INF = 1 << 62
    nearest_score = [INF] * n_targets
    nearest_gid: list[int | None] = [None] * n_targets
    ptr = [0] * n_masks               # per-mask cursor into its ascending gid list
    selected: list[dict] = []
    selected_ids: set[int] = set()

    for _round in range(ROW_CAPACITY):
        best_key = None
        best_mi = -1
        best_gid = -1
        for mi in range(n_masks):
            gids = mask_to_gids[masks[mi]]
            p = ptr[mi]
            while p < len(gids) and gids[p] in selected_ids:
                p += 1
            ptr[mi] = p
            if p >= len(gids):
                continue  # this signature has no unselected candidate left
            cand_gid = gids[p]
            # Element-wise nearest = min(current nearest, this candidate's cost),
            # evaluated at C speed via map(min,...); worst=max, sum=sum.
            nn = list(map(min, cost_rows[mi], nearest_score))
            key = (max(nn), sum(nn), cand_gid)
            if best_key is None or key < best_key:
                best_key = key
                best_mi = mi
                best_gid = cand_gid
        m = masks[best_mi]
        cep, cjn = mask_desc[m]
        if m == 0:  # contractually impossible (validation rejects zero masks)
            raise AdmissionError(REASON_SELECTED_EMPTY_DESCRIPTOR, "")
        selected.append({"glyph_id": best_gid, "mask8": m,
                         "endpoint_count": cep, "junction_count": cjn})
        selected_ids.add(best_gid)
        # Incremental nearest + owner update (no rescan): a strict improvement
        # takes ownership; an exact tie keeps the lowest glyph_id.
        row = cost_rows[best_mi]
        for ti in range(n_targets):
            c = row[ti]
            if c < nearest_score[ti]:
                nearest_score[ti] = c
                nearest_gid[ti] = best_gid
            elif c == nearest_score[ti] and (nearest_gid[ti] is None
                                             or best_gid < nearest_gid[ti]):
                nearest_gid[ti] = best_gid

    per_target = [{"nearest_winner_glyph_id": nearest_gid[ti],
                   "nearest_score": nearest_score[ti]} for ti in range(n_targets)]
    return selected, per_target, n_masks


# =============================================================================
# V3 U2 grammar and full-supply constrained selector.
# =============================================================================
def _combined_target_record(mask: int, family: str, endpoints: object,
                            source: str) -> dict:
    """Create one source-independent U2 target.

    A target is authored from the fixed raster/lattice rules below.  It is
    never allowed to originate from a live or held-out mask.
    """
    if source in {"live", "heldout"}:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, source)
    if mask == 0:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, f"zero {family}")
    _skeleton, endpoint_count, junction_count = topology_counts(mask)
    return {
        "family": family,
        "family_membership": [family],
        "weight": 1,
        "source": source,
        "endpoints": endpoints,
        # The frozen U2 grammar identity uses the authored hexadecimal display
        # form. Supply/descriptor hashes continue to use the ABI LE form.
        "mask8_hex": f"{mask:016x}",
        "endpoint_count": int(endpoint_count),
        "junction_count": int(junction_count),
        "boundary_contacts": list(_boundary_contacts(mask)),
        "_mask8": mask,
    }


def _combined_serial_target(target: dict) -> dict:
    serial = {
        "family": target["family"],
        "family_membership": target["family_membership"],
        "weight": target["weight"],
        "source": target["source"],
        "endpoints": target["endpoints"],
        "mask8_hex": target["mask8_hex"],
        "endpoint_count": target["endpoint_count"],
        "junction_count": target["junction_count"],
    }
    if "direction_pair" in target:
        serial["direction_pair"] = list(target["direction_pair"])
        serial["span_pair"] = list(target["span_pair"])
    return serial


def _combined_target_hash(targets: Iterable[dict]) -> str:
    return _sha256_hex(canonical_json_bytes(
        [_combined_serial_target(target) for target in targets]))


def _u2_rasterize_world(endpoint_a_world: tuple[float, float],
                        endpoint_b_world: tuple[float, float]) -> int:
    """Exact scalar mirror of render_resolve.cpp:1770-1817."""
    x0, y0 = endpoint_a_world
    x1, y1 = endpoint_b_world
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom <= 1.0e-8:
        return 0
    mask = 0
    for py in range(U2_CELL_PIXELS):
        for px in range(U2_CELL_PIXELS):
            sx = (px + U2_SAMPLE_OFFSET) * U2_WORLD_STEP
            sy = (py + U2_SAMPLE_OFFSET) * U2_WORLD_STEP
            t = ((sx - x0) * dx + (sy - y0) * dy) / denom
            t = max(0.0, min(1.0, t))
            ex, ey = x0 + t * dx, y0 + t * dy
            ddx, ddy = sx - ex, sy - ey
            if ddx * ddx + ddy * ddy <= U2_DISTANCE_SQ:
                mask |= 1 << (py * U2_CELL_PIXELS + px)
    return mask


def _endpoint_lattice() -> tuple[tuple[int, int], ...]:
    """Quarter-pixel boundary plus integer-pixel interior endpoint lattice."""
    denominator = ENDPOINT_LATTICE_DENOMINATOR
    extent = U2_CELL_PIXELS * denominator
    points: set[tuple[int, int]] = set()
    for q in range(extent + 1):
        points.add((q, 0))
        points.add((extent, q))
        points.add((extent - q, extent))
        points.add((0, extent - q))
    for y in range(1, U2_CELL_PIXELS):
        for x in range(1, U2_CELL_PIXELS):
            points.add((x * denominator // INTERIOR_ENDPOINT_STEP_PIXELS,
                        y * denominator // INTERIOR_ENDPOINT_STEP_PIXELS))
    return tuple(sorted(points))


def _quantized_world_point(point_q: tuple[int, int]) -> tuple[float, float]:
    return (point_q[0] / ENDPOINT_LATTICE_DENOMINATOR * U2_WORLD_STEP,
            point_q[1] / ENDPOINT_LATTICE_DENOMINATOR * U2_WORLD_STEP)


def _line_orientation_bin(point_a: tuple[int, int], point_b: tuple[int, int]) -> int:
    dx, dy = point_b[0] - point_a[0], point_b[1] - point_a[1]
    if dx == 0 and dy == 0:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "zero endpoint span")
    angle = math.atan2(dy, dx) % math.pi
    return int(round((angle / math.pi) * 8.0)) % 8


def _line_span_bin(point_a: tuple[int, int], point_b: tuple[int, int]) -> int:
    span = max(abs(point_b[0] - point_a[0]), abs(point_b[1] - point_a[1]))
    return min(3, (span * 4) // (U2_CELL_PIXELS * ENDPOINT_LATTICE_DENOMINATOR))


def _combined_target_stratum(target: dict) -> tuple[int, ...]:
    if target.get("family") == "pairwise_union":
        pair, spans = target.get("direction_pair"), target.get("span_pair")
        if not (isinstance(pair, tuple) and len(pair) == 2 and
                isinstance(spans, tuple) and len(spans) == 2):
            raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH,
                                 "pairwise stratum missing")
        return (*pair, *spans)
    return (target["orientation_bin"], target["span_bin"])


def _combined_distance(left: dict, right: dict) -> int:
    return _cost(left["_mask8"], left["endpoint_count"], left["junction_count"],
                 right["_mask8"], right["endpoint_count"], right["junction_count"])


def _combined_coverage(domain: list[dict], representatives: list[dict]) -> dict:
    distances = [min(_combined_distance(candidate, representative)
                      for representative in representatives)
                 for candidate in domain]
    return {"worst": max(distances), "median": statistics.median(distances),
            "max": max(distances), "mean": sum(distances) / len(distances),
            "domain_count": len(domain),
            "representative_count": len(representatives)}


def _family_membership_metrics(domain: list[dict], representatives: list[dict]) -> dict:
    predicates = {
        "runtime_line": lambda membership: "runtime_line" in membership,
        "pairwise_union": lambda membership: "pairwise_union" in membership,
        "runtime_line_or_pairwise_union": lambda membership: (
            "runtime_line" in membership or "pairwise_union" in membership),
    }
    result = {}
    for name, predicate in predicates.items():
        subset = [target for target in domain if predicate(target["family_membership"])]
        result[name] = {
            "unique_target_count": len(subset),
            "combined_objective_fraction": len(subset) / len(domain),
            "coverage": _combined_coverage(subset, representatives) if subset else None,
            "combined_authored_duplicate_count": sum(
                target["source"] == "combined_authored" for target in subset),
        }
    return result


def _combined_seed_identities(domain: list[dict]) -> list[dict]:
    by_mask = {target["_mask8"]: target for target in domain}
    seeds = []
    for name, point_a, point_b in COMPASS_SEED_ENDPOINTS:
        mask = _u2_rasterize_world(_quantized_world_point(point_a),
                                   _quantized_world_point(point_b))
        if mask not in by_mask:
            raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, name)
        seeds.append({"identity": name, "mask8_hex": f"{mask:016x}",
                      "a_q": list(point_a), "b_q": list(point_b)})
    return seeds


def _combined_farthest_first(domain: list[dict], max_guard: int,
                             seed_identities: list[dict] | None = None) -> tuple[list[dict], dict]:
    ordered = sorted(domain, key=lambda item: item["_mask8"])
    if not ordered or max_guard < 1:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "empty representative domain")
    seeds = seed_identities or _combined_seed_identities(ordered)
    by_mask = {target["_mask8"]: target for target in ordered}
    selected, selected_masks = [], set()
    for seed in seeds:
        mask = int(seed["mask8_hex"], 16)
        if mask not in by_mask:
            raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "seed outside domain")
        if mask not in selected_masks:
            selected.append(by_mask[mask]); selected_masks.add(mask)
    if len(selected) > max_guard:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "representative guard")
    nearest = {target["_mask8"]: 1 << 62 for target in ordered}

    def add_coverage(target: dict) -> None:
        for candidate in ordered:
            distance = _combined_distance(candidate, target)
            if distance < nearest[candidate["_mask8"]]:
                nearest[candidate["_mask8"]] = distance

    for target in selected:
        add_coverage(target)
    def farthest(candidates: list[dict]) -> dict:
        return max(candidates, key=lambda item: (
            nearest[item["_mask8"]], -item["_mask8"]))
    strata: dict[tuple[int, ...], list[dict]] = {}
    covered = {_combined_target_stratum(target) for target in selected}
    for target in ordered:
        if target["_mask8"] not in selected_masks:
            strata.setdefault(_combined_target_stratum(target), []).append(target)
    for stratum in sorted(strata):
        if stratum in covered:
            continue
        if len(selected) >= max_guard:
            raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "strata guard")
        target = farthest(strata[stratum])
        selected.append(target); selected_masks.add(target["_mask8"])
        covered.add(stratum); add_coverage(target)
    mandatory_count = len(selected)
    mandatory_worst = max(nearest.values())
    previous_worst = None
    while max(nearest.values()) > GRAMMAR_WORST_BOUND:
        if len(selected) >= min(max_guard, len(ordered)):
            raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "threshold guard")
        candidates = [target for target in ordered
                      if target["_mask8"] not in selected_masks]
        previous_worst = max(nearest.values())
        target = farthest(candidates)
        selected.append(target); selected_masks.add(target["_mask8"])
        add_coverage(target)
    coverage = _combined_coverage(ordered, selected)
    return selected, {
        "algorithm": "threshold_seeded_stratified_farthest_first_u7_symmetric.v1",
        "seed_identities": seeds, "seed_count": len(seeds),
        "strata_count": len({_combined_target_stratum(t) for t in ordered}),
        "max_guard": max_guard, "full_domain_count": len(ordered),
        "derived_count": len(selected),
        "selection_order_hash": _combined_target_hash(selected),
        "seed_masks_present": sum(int(seed["mask8_hex"], 16) in selected_masks
                                   for seed in seeds),
        "threshold": GRAMMAR_WORST_BOUND,
        "mandatory_prefix_count": mandatory_count,
        "mandatory_prefix_worst": mandatory_worst,
        "previous_prefix_worst": previous_worst,
        "threshold_crossing_coverage": coverage,
    }


def _combined_runtime_line_domain() -> list[dict]:
    points = _endpoint_lattice()
    by_mask: dict[int, dict] = {}
    for index, point_a in enumerate(points):
        for point_b in points[index + 1:]:
            mask = _u2_rasterize_world(_quantized_world_point(point_a),
                                       _quantized_world_point(point_b))
            if mask.bit_count() < 4 or mask in by_mask:
                continue
            target = _combined_target_record(
                mask, "runtime_line", {
                    "endpoint_lattice_denominator": ENDPOINT_LATTICE_DENOMINATOR,
                    "a_q": list(point_a), "b_q": list(point_b),
                }, "u2_endpoint_lattice")
            target["orientation_bin"] = _line_orientation_bin(point_a, point_b)
            target["span_bin"] = _line_span_bin(point_a, point_b)
            by_mask[mask] = target
    return sorted(by_mask.values(), key=lambda item: item["_mask8"])


def _combined_pairwise_targets(line_targets: list[dict]) -> list[dict]:
    if len(line_targets) > RUNTIME_LINE_REPRESENTATIVE_CAP:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "pairwise base cap")
    base = sorted(line_targets, key=lambda item: item["_mask8"])
    by_mask: dict[int, dict] = {}
    for index, first in enumerate(base):
        for second in base[index + 1:]:
            mask = first["_mask8"] | second["_mask8"]
            if mask == 0 or mask in by_mask:
                continue
            target = _combined_target_record(
                mask, "pairwise_union", {
                    "base_limit": len(base),
                    "left_mask8": first["mask8_hex"],
                    "right_mask8": second["mask8_hex"],
                }, "authored_pairwise_union")
            target["direction_pair"] = tuple(sorted((
                first["orientation_bin"], second["orientation_bin"])))
            target["span_pair"] = tuple(sorted((
                first["span_bin"], second["span_bin"])))
            by_mask[mask] = target
    return sorted(by_mask.values(), key=lambda item: item["_mask8"])


def _merge_combined_families(families: Iterable[list[dict]]) -> list[dict]:
    by_mask: dict[int, dict] = {}
    for family_targets in families:
        for target in family_targets:
            mask = target["_mask8"]
            existing = by_mask.get(mask)
            if existing is None:
                by_mask[mask] = dict(target)
                continue
            membership = list(existing["family_membership"])
            for family in target["family_membership"]:
                if family not in membership:
                    membership.append(family)
            existing["family_membership"] = membership
            if existing["source"] != target["source"]:
                existing["source"] = "combined_authored"
    result = sorted(by_mask.values(), key=lambda item: item["_mask8"])
    for target in result:
        target["weight"] = 1
    return result


def _legacy_targets() -> list[dict]:
    legacy = _build_legacy_grammar()
    if legacy["target_count"] != 330:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "legacy cardinality")
    result = []
    for target in legacy["_targets_runtime"]:
        record = _combined_target_record(target["_mask8"], target["family"],
                                         target["endpoints"], "legacy_330")
        record["family_membership"] = ["legacy_330", target["family"]]
        result.append(record)
    return result


def build_grammar() -> dict:
    """Build the frozen combined 1111-target grammar used by production."""
    legacy = _legacy_targets()
    line_domain = _combined_runtime_line_domain()
    runtime_line, line_selection = _combined_farthest_first(
        line_domain, RUNTIME_LINE_REPRESENTATIVE_CAP)
    pairwise_domain = _combined_pairwise_targets(runtime_line)
    line_seeds = _combined_seed_identities(line_domain)
    pairwise_seeds = []
    pairwise_by_mask = {target["_mask8"]: target for target in pairwise_domain}
    for index, left in enumerate(line_seeds):
        for right in line_seeds[index + 1:]:
            mask = int(left["mask8_hex"], 16) | int(right["mask8_hex"], 16)
            if mask not in pairwise_by_mask:
                raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "pairwise seed")
            pairwise_seeds.append({
                "identity": f"{left['identity']}+{right['identity']}",
                "mask8_hex": f"{mask:016x}", "a_q": left["a_q"], "b_q": right["b_q"],
            })
    pairwise, pairwise_selection = _combined_farthest_first(
        pairwise_domain, PAIRWISE_UNION_REPRESENTATIVE_CAP, pairwise_seeds)
    targets = _merge_combined_families((legacy, runtime_line, pairwise))
    runtime_pairwise = _merge_combined_families((runtime_line, pairwise))
    if len(targets) != 1111 or sum("legacy_330" in t["family_membership"]
                                   for t in targets) != 330:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "combined cardinality")
    return {
        "schema": "fl4512.u2_runtime_domain_grammar.v1",
        "grammar_id": GRAMMAR_ID,
        "grammar_hash": _combined_target_hash(targets),
        "target_count": len(targets),
        "legacy_330_count": len(legacy),
        "runtime_line_count": len(runtime_line),
        "pairwise_union_count": len(pairwise),
        "combined_unique_mask_count": len(targets),
        "runtime_line_max_guard": RUNTIME_LINE_REPRESENTATIVE_CAP,
        "pairwise_union_base_limit": len(runtime_line),
        "pairwise_union_representative_cap": PAIRWISE_UNION_REPRESENTATIVE_CAP,
        "runtime_line_domain_count": len(line_domain),
        "runtime_line_domain_hash": _combined_target_hash(line_domain),
        "pairwise_union_domain_count": len(pairwise_domain),
        "pairwise_union_domain_hash": _combined_target_hash(pairwise_domain),
        "runtime_line_selection": line_selection,
        "pairwise_union_selection": pairwise_selection,
        "family_membership_metrics": _family_membership_metrics(
            targets, runtime_pairwise),
        "representative_algorithm": "stratified_seeded_farthest_first_u7_symmetric.v1",
        "endpoint_lattice": {
            "denominator": ENDPOINT_LATTICE_DENOMINATOR,
            "pixel_extent": U2_CELL_PIXELS,
            "interior_step_pixels": INTERIOR_ENDPOINT_STEP_PIXELS,
            "point_count": len(_endpoint_lattice()),
        },
        "u2_raster": {"sample_offset": U2_SAMPLE_OFFSET,
                      "world_step": U2_WORLD_STEP,
                      "distance_squared_threshold": U2_DISTANCE_SQ,
                      "accepted_minimum_bits": 4},
        "targets": [_combined_serial_target(target) for target in targets],
        "_targets_runtime": targets,
    }


# Names retained as direct exports for read-only analyzers. The implementation
# remains in this production module; no analyzer owns a second grammar.
_serial_target = _combined_serial_target
_target_hash = _combined_target_hash
_u2_rasterize_world = _u2_rasterize_world
_endpoint_lattice = _endpoint_lattice
_runtime_line_domain = _combined_runtime_line_domain
_pairwise_union_targets = _combined_pairwise_targets
_merge_target_families = _merge_combined_families
_threshold_farthest_first_prefix = _combined_farthest_first
_u7_symmetric_distance = _combined_distance
_coverage_metrics = _combined_coverage
_authored_seed_identities = _combined_seed_identities
_target_stratum = _combined_target_stratum


def _descriptor_signature(descriptor: dict) -> tuple[int, int, int]:
    return (descriptor["mask8"], descriptor["endpoint_count"],
            descriptor["junction_count"])


def _full_supply_signature_universe(validated: list[dict]) -> tuple[list[dict], dict]:
    """Keep the lowest GlyphId owner for each exact U7 descriptor signature."""
    owners: dict[tuple[int, int, int], dict] = {}
    for descriptor in validated:
        signature = _descriptor_signature(descriptor)
        if signature not in owners or descriptor["glyph_id"] < owners[signature]["glyph_id"]:
            owners[signature] = descriptor
    universe = sorted(owners.values(), key=lambda item: item["glyph_id"])
    if not universe:
        raise AdmissionError(REASON_SUPPLY_EMPTY, "signature universe empty")
    serialized = [[item["glyph_id"], *list(_descriptor_signature(item))]
                  for item in universe]
    return universe, {
        "full_supply_count": len(validated),
        "distinct_signature_count": len(owners),
        "candidate_count": len(universe),
        "lowest_glyph_id_tie_owner": True,
        "candidate_signature_hash": _sha256_hex(canonical_json_bytes(serialized)),
        "candidate_glyph_ids_sha256": _sha256_hex(canonical_json_bytes(
            [item["glyph_id"] for item in universe])),
    }


def _validate_frozen_full_supply_contract(
    artifact: dict, source_identity: dict, grammar: dict, validated: list[dict],
) -> None:
    """Validate the immutable source/grammar joins before full-supply selection."""
    if grammar.get("grammar_hash") != _combined_target_hash(grammar["_targets_runtime"]):
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "grammar hash")
    if grammar.get("target_count") != 1111:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "target count")
    source = artifact.get("source")
    if not isinstance(source, dict):
        raise AdmissionError(REASON_SUPPLY_HASH_MISMATCH, "artifact source")
    for name, key in (("catalog", "catalog_source_sha256"),
                      ("registry", "registry_source_sha256")):
        if source.get(key) != source_identity[name]["sha256"]:
            raise AdmissionError(REASON_SUPPLY_HASH_MISMATCH, name)
    if len(validated) < ROW_CAPACITY:
        raise AdmissionError(REASON_SUPPLY_BELOW_CAPACITY, str(len(validated)))


def _selection_metrics(scores: list[int]) -> dict:
    if not scores:
        return {"worst": 0, "median": 0, "max": 0, "mean": 0.0,
                "count": 0, "sum": 0}
    return {"worst": max(scores), "median": statistics.median(scores),
            "max": max(scores), "mean": sum(scores) / len(scores),
            "count": len(scores), "sum": sum(scores)}


def _selection_objective(scores: list[int], glyph_ids: Iterable[int]) -> tuple:
    return (max(scores) if scores else 0, sum(scores), tuple(sorted(glyph_ids)))


def _full_supply_memory_ledger(target_count: int, block_size: int,
                               legacy_target_count: int = 330) -> dict:
    if not isinstance(target_count, int) or target_count < 1:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "target shape")
    if not isinstance(block_size, int) or block_size < 1:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "block shape")
    max_targets = max(target_count, legacy_target_count)
    values = {
        "uint64_xor_scratch_bytes": block_size * max_targets * 8,
        "uint8_popcount_lookup_scratch_bytes": block_size * max_targets * 8,
        "uint16_family_cost_matrices_bytes": block_size * (target_count + legacy_target_count) * 2,
        "uint16_comparison_tensors_bytes": block_size * ROW_CAPACITY
        * min(max_targets, FULL_SUPPLY_TARGET_CHUNK_SIZE) * 4,
        "uint64_sum_accumulator_bytes": block_size * ROW_CAPACITY * 8,
        "uint16_reduction_accumulators_bytes": block_size * ROW_CAPACITY * 4,
        "uint32_popcount_terms_bytes": block_size * max_targets * 4,
        "uint32_endpoint_terms_bytes": block_size * max_targets * 4,
        "uint32_junction_terms_bytes": block_size * max_targets * 4,
        "uint32_addition_temporaries_bytes": block_size * max_targets * 8,
    }
    values["modeled_live_tensor_bytes"] = sum(values.values())
    values["memory_cap_bytes"] = FULL_SUPPLY_MEMORY_CAP_BYTES
    values["memory_bound_pass"] = values["modeled_live_tensor_bytes"] <= FULL_SUPPLY_MEMORY_CAP_BYTES
    values["actual_rss_measured"] = False
    values["actual_rss_status"] = "pending_canonical_execution"
    return values


def _full_supply_block_memory_bytes(target_count: int, block_size: int,
                                    legacy_target_count: int = 330) -> int:
    return _full_supply_memory_ledger(target_count, block_size,
                                      legacy_target_count)["modeled_live_tensor_bytes"]


def _full_supply_blocks(items: list[dict], block_size: int):
    if _full_supply_block_memory_bytes(1111, block_size) > FULL_SUPPLY_MEMORY_CAP_BYTES:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "memory cap")
    for start in range(0, len(items), block_size):
        yield items[start:start + block_size]


def _u7_cost_matrix_block(targets: list[dict], descriptors: list[dict]):
    """Exact uint16 U7 matrix; no float or semantic approximation."""
    if np is None:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "numpy unavailable")
    target_masks = np.asarray([target["_mask8"] for target in targets], dtype=np.uint64)
    target_eps = np.asarray([target["endpoint_count"] for target in targets], dtype=np.int16)
    target_jns = np.asarray([target["junction_count"] for target in targets], dtype=np.int16)
    candidate_masks = np.asarray([item["mask8"] for item in descriptors], dtype=np.uint64)
    candidate_eps = np.asarray([item["endpoint_count"] for item in descriptors], dtype=np.int16)
    candidate_jns = np.asarray([item["junction_count"] for item in descriptors], dtype=np.int16)
    xor = np.bitwise_xor(candidate_masks[:, None], target_masks[None, :])
    byte_view = xor.view(np.uint8).reshape(len(descriptors), len(targets), 8)
    lookup = np.asarray([value.bit_count() for value in range(256)], dtype=np.uint8)
    popcount = np.take(lookup, byte_view).sum(axis=2, dtype=np.uint16)
    endpoint_delta = np.abs(candidate_eps[:, None] - target_eps[None, :])
    junction_delta = np.abs(candidate_jns[:, None] - target_jns[None, :])
    return (70 * popcount.astype(np.uint32)
            + 15 * endpoint_delta.astype(np.uint32)
            + 15 * junction_delta.astype(np.uint32)).astype(np.uint16)


def _full_supply_constrained_exchange(
    current_ids: list[int], universe: list[dict], grammar_targets: list[dict],
    *, legacy_targets: list[dict] | None = None,
    block_size: int = FULL_SUPPLY_BLOCK_SIZE,
) -> dict:
    """Deterministic strict single-swap descent over every signature owner."""
    if np is None:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "numpy unavailable")
    if len(current_ids) != ROW_CAPACITY or len(set(current_ids)) != ROW_CAPACITY:
        raise AdmissionError(REASON_SUPPLY_BELOW_CAPACITY, "current row")
    if not grammar_targets or not universe:
        raise AdmissionError(REASON_SUPPLY_EMPTY, "selection domain")
    legacy = legacy_targets or _legacy_targets()
    memory_ledger = _full_supply_memory_ledger(len(grammar_targets), block_size, len(legacy))
    if not memory_ledger["memory_bound_pass"]:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "memory cap")
    universe = sorted(universe, key=lambda item: item["glyph_id"])
    by_gid = {item["glyph_id"]: item for item in universe}
    if not set(current_ids) <= set(by_gid):
        raise AdmissionError(REASON_SUPPLY_BELOW_CAPACITY, "current row outside universe")
    current = tuple(sorted(current_ids))
    initial_items = [by_gid[gid] for gid in current]
    initial_combined_scores = _u7_cost_matrix_block(
        grammar_targets, initial_items).min(axis=0).astype(int).tolist()
    swaps = []
    iterations = 0
    matrix_cell_evaluations = ROW_CAPACITY * len(grammar_targets)
    objective_operations = 0
    initial_legacy = _u7_cost_matrix_block(
        legacy, [by_gid[gid] for gid in current]).min(axis=0).astype(int).tolist()
    matrix_cell_evaluations += ROW_CAPACITY * len(legacy)
    if max(initial_legacy) > GRAMMAR_WORST_BOUND:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH,
                             f"legacy bound {max(initial_legacy)}")
    while True:
        current_items = [by_gid[gid] for gid in current]
        combined_current = _u7_cost_matrix_block(grammar_targets, current_items)
        legacy_current = _u7_cost_matrix_block(legacy, current_items)
        matrix_cell_evaluations += ROW_CAPACITY * (len(grammar_targets) + len(legacy))
        combined_retained = [combined_current[[j for j in range(ROW_CAPACITY) if j != i]].min(axis=0)
                             for i in range(ROW_CAPACITY)]
        legacy_retained = [legacy_current[[j for j in range(ROW_CAPACITY) if j != i]].min(axis=0)
                           for i in range(ROW_CAPACITY)]
        current_scores = combined_current.min(axis=0).astype(int).tolist()
        current_objective = _selection_objective(current_scores, current)
        current_set = set(current)
        candidates = [item for item in universe if item["glyph_id"] not in current_set]
        matrix_cell_evaluations += len(candidates) * (len(grammar_targets) + len(legacy))
        retained_ids = [tuple(gid for gid in current if gid != removed) for removed in current]
        combined_retained_matrix = np.asarray(combined_retained)
        legacy_retained_matrix = np.asarray(legacy_retained)
        best = None
        for block in _full_supply_blocks(candidates, block_size):
            block_combined = _u7_cost_matrix_block(grammar_targets, block)
            block_legacy = _u7_cost_matrix_block(legacy, block)
            count = len(block)
            candidate_max = np.zeros((count, ROW_CAPACITY), dtype=np.uint16)
            candidate_sum = np.zeros((count, ROW_CAPACITY), dtype=np.uint64)
            candidate_legacy_max = np.zeros((count, ROW_CAPACITY), dtype=np.uint16)
            for start in range(0, max(len(grammar_targets), len(legacy)), FULL_SUPPLY_TARGET_CHUNK_SIZE):
                if start < len(grammar_targets):
                    stop = min(start + FULL_SUPPLY_TARGET_CHUNK_SIZE, len(grammar_targets))
                    chunk = np.minimum(block_combined[:, None, start:stop],
                                       combined_retained_matrix[None, :, start:stop])
                    candidate_max = np.maximum(candidate_max, chunk.max(axis=2))
                    candidate_sum += chunk.sum(axis=2, dtype=np.uint64)
                if start < len(legacy):
                    stop = min(start + FULL_SUPPLY_TARGET_CHUNK_SIZE, len(legacy))
                    chunk = np.minimum(block_legacy[:, None, start:stop],
                                       legacy_retained_matrix[None, :, start:stop])
                    candidate_legacy_max = np.maximum(candidate_legacy_max, chunk.max(axis=2))
            for offset, item in enumerate(block):
                added = item["glyph_id"]
                for removed_index, removed in enumerate(current):
                    objective_operations += len(grammar_targets) + len(legacy)
                    legacy_worst = int(candidate_legacy_max[offset, removed_index])
                    if legacy_worst > GRAMMAR_WORST_BOUND:
                        continue
                    candidate_ids = tuple(sorted(retained_ids[removed_index] + (added,)))
                    candidate_objective = (int(candidate_max[offset, removed_index]),
                                           int(candidate_sum[offset, removed_index]),
                                           candidate_ids)
                    if candidate_objective >= current_objective:
                        continue
                    if best is None or candidate_objective < best[0]:
                        best = (candidate_objective, candidate_ids, removed, added,
                                legacy_worst)
        if best is None:
            break
        candidate_objective, current, removed, added, legacy_worst = best
        swaps.append({"removed_glyph_id": removed, "added_glyph_id": added,
                      "objective_before": current_objective,
                      "objective_after": candidate_objective,
                      "legacy_worst_after": legacy_worst})
        iterations += 1
    final_items = [by_gid[gid] for gid in current]
    final_combined = _u7_cost_matrix_block(grammar_targets, final_items).min(axis=0).astype(int).tolist()
    final_legacy = _u7_cost_matrix_block(legacy, final_items).min(axis=0).astype(int).tolist()
    matrix_cell_evaluations += ROW_CAPACITY * (len(grammar_targets) + len(legacy))
    return {
        "algorithm": SELECTION_ALGORITHM,
        "block_size": block_size,
        "memory_cap_bytes": FULL_SUPPLY_MEMORY_CAP_BYTES,
        "modeled_live_tensor_bytes": memory_ledger["modeled_live_tensor_bytes"],
        "actual_rss_measured": False,
        "actual_rss_status": "pending_canonical_execution",
        "memory_ledger": memory_ledger,
        "iterations": iterations, "swap_count": len(swaps), "swaps": swaps,
        "starting_objective": _selection_objective(initial_combined_scores,
                                                    current_ids),
        "final_objective": _selection_objective(final_combined, current),
        "legacy_metrics": _selection_metrics(final_legacy),
        "legacy_worst_bound": GRAMMAR_WORST_BOUND,
        "legacy_worst_pass": max(final_legacy) <= GRAMMAR_WORST_BOUND,
        "final_selected_glyph_ids": list(current),
        "final_selected_descriptors": final_items,
        "exact_u7_objective_operations": objective_operations,
        "exact_u7_matrix_cell_evaluations": matrix_cell_evaluations,
        "exact_u7_total_operations": objective_operations + matrix_cell_evaluations,
    }


# =============================================================================
# PUBLIC: admit_row — the one entry point.
# =============================================================================
def admit_row(supply, *, grammar: dict | None = None) -> dict:
    """Compile one 32-glyph legal row + full receipt from a descriptor supply.

    `supply`: iterable of {glyph_id, mask8, endpoint_count, junction_count}.
    `grammar`: optionally a prebuilt grammar (build_grammar()); built if None.
    Raises AdmissionError (with a typed reason) on any malformed input or on the
    missing-compass-contact rule. Returns the receipt dict.
    """
    if grammar is None:
        grammar = build_grammar()
    validated = _validate_supply(supply)
    legacy_grammar = _build_legacy_grammar()
    seed, _seed_per_target, _seed_distinct = _select_legacy(
        legacy_grammar["_targets_runtime"], validated)
    seed_ids = [item["glyph_id"] for item in seed]
    universe, universe_identity = _full_supply_signature_universe(validated)
    exchange = _full_supply_constrained_exchange(
        seed_ids, universe, grammar["_targets_runtime"],
        legacy_targets=_legacy_targets(),
    )
    selected = exchange["final_selected_descriptors"]

    # The published receipt is a projection of the final set, never the
    # optimizer's internal swap order. Winner ties are owned by lowest GlyphId.
    per_target = []
    for target in grammar["_targets_runtime"]:
        scored = sorted((_cost(target["_mask8"], target["endpoint_count"],
                               target["junction_count"], item["mask8"],
                               item["endpoint_count"], item["junction_count"]),
                         item["glyph_id"]) for item in selected)
        score, gid = scored[0]
        per_target.append({"nearest_winner_glyph_id": gid,
                           "nearest_score": score})
    distinct_signature_count = universe_identity["distinct_signature_count"]

    # CANONICAL PUBLICATION ORDER. `selected_glyph_ids` names a SET, not a
    # sequence: the exchange order is an implementation artifact, not the
    # row's identity. Publish the chosen set in ascending GlyphId order
    # so the artifact has ONE canonical byte representation and no permutation
    # (even a consistently re-hashed one) can masquerade as canonical. per_target
    # is indexed by grammar target and is independent of this order.
    selected = sorted(selected, key=lambda d: d["glyph_id"])

    # Rule: the selected row must COLLECTIVELY touch every compass contact.
    union_mask = 0
    for d in selected:
        union_mask |= d["mask8"]
    covered = _boundary_contacts(union_mask)
    missing = [c for c in COMPASS_ORDER if c not in covered]
    if missing:
        raise AdmissionError(REASON_MISSING_COMPASS_CONTACT, ",".join(missing))

    # Aggregate metrics over the combined grammar targets.
    scores = [pt["nearest_score"] for pt in per_target]
    worst_score = max(scores) if scores else 0
    score_sum = sum(scores)
    denom = len(scores) if scores else 1
    mean_num, mean_den = _reduce_fraction(score_sum, denom)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "grammar_id": grammar["grammar_id"],
        "grammar_hash": grammar["grammar_hash"],
        "supply_hash": _supply_hash(validated),
        "scorer_id": SCORER_ID,
        "row_capacity": ROW_CAPACITY,
        # Provenance of the exact objective scan: how many descriptors were
        # scanned and how many distinct signatures the scan actually ranged over.
        "supply_count": len(validated),
        "distinct_signature_count": distinct_signature_count,
        "selected_glyph_ids": [d["glyph_id"] for d in selected],
        "selected_descriptor_hashes": [_descriptor_hash(d) for d in selected],
        "per_target": per_target,
        "worst_score": worst_score,
        "score_sum": score_sum,
        "mean_numerator": mean_num,
        "mean_denominator": mean_den,
        "boundary_contact_coverage": list(covered),
        "selection_algorithm": exchange["algorithm"],
        "legacy_worst_score": exchange["legacy_metrics"]["worst"],
        "legacy_worst_bound": GRAMMAR_WORST_BOUND,
        "selection_iterations": exchange["iterations"],
        "selection_swap_count": exchange["swap_count"],
        "selection_exact_u7_operations": exchange["exact_u7_total_operations"],
        "selection_modeled_live_tensor_bytes": exchange["modeled_live_tensor_bytes"],
        "selection_memory_cap_bytes": exchange["memory_cap_bytes"],
        "selection_actual_rss_measured": exchange["actual_rss_measured"],
        "selection_actual_rss_status": exchange["actual_rss_status"],
        "content_hash": "",
    }
    receipt["content_hash"] = _content_hash(receipt)
    return receipt


def _reduce_fraction(num: int, den: int) -> tuple[int, int]:
    """Reduce num/den to lowest terms (den > 0). Exact rational mean, no float."""
    if den == 0:
        return (0, 1)
    from math import gcd
    g = gcd(num, den) or 1
    return (num // g, den // g)


def _content_hash(receipt: dict) -> str:
    """Content hash over the whole receipt EXCEPT its own content_hash field."""
    body = {k: v for k, v in receipt.items() if k != "content_hash"}
    return _sha256_hex(canonical_json_bytes(body))


# =============================================================================
# INDEPENDENT recompute/validate: rebuild every derived field from the supply
# and reject a mismatch with a stable typed reason code. This is the trust
# anchor — a receipt is only believed if this function reproduces it exactly.
# =============================================================================
def recompute_and_validate(receipt: dict, supply, *, grammar: dict | None = None) -> None:
    """Raise AdmissionError(reason) unless `receipt` is byte-for-byte the exact
    output of admit_row(supply). Every derived field is rebuilt from scratch."""
    if grammar is None:
        grammar = build_grammar()

    # 0. Identity fields: schema, grammar id, scorer id must be exact.
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise AdmissionError(REASON_SCHEMA_MISMATCH, str(receipt.get("schema")))
    if receipt.get("grammar_id") != grammar["grammar_id"]:
        raise AdmissionError(REASON_GRAMMAR_ID_MISMATCH, str(receipt.get("grammar_id")))
    if receipt.get("scorer_id") != SCORER_ID:
        raise AdmissionError(REASON_SCORER_ID_MISMATCH, str(receipt.get("scorer_id")))

    # 1. Grammar + supply provenance.
    if receipt.get("grammar_hash") != grammar["grammar_hash"]:
        raise AdmissionError(REASON_GRAMMAR_HASH_MISMATCH, "")
    validated = _validate_supply(supply)
    if receipt.get("supply_hash") != _supply_hash(validated):
        raise AdmissionError(REASON_SUPPLY_HASH_MISMATCH, "")
    if receipt.get("row_capacity") != ROW_CAPACITY:
        raise AdmissionError(REASON_CAPACITY_MISMATCH, "")

    # 2. Re-run the sole production selector and compare its complete receipt.
    expected = admit_row(validated, grammar=grammar)
    for key in ("supply_count", "distinct_signature_count", "selected_glyph_ids",
                "selected_descriptor_hashes", "per_target", "worst_score",
                "score_sum", "mean_numerator", "mean_denominator",
                "boundary_contact_coverage", "selection_algorithm",
                "legacy_worst_score", "legacy_worst_bound",
                "selection_iterations", "selection_swap_count",
                "selection_exact_u7_operations",
                "selection_modeled_live_tensor_bytes", "selection_memory_cap_bytes",
                "selection_actual_rss_measured", "selection_actual_rss_status"):
        if receipt.get(key) != expected.get(key):
            reason = REASON_SELECTED_ORDER_MISMATCH if key == "selected_glyph_ids" else (
                REASON_DESCRIPTOR_HASH_MISMATCH if key == "selected_descriptor_hashes" else
                REASON_NEAREST_WINNER_MISMATCH if key == "per_target" else
                REASON_AGGREGATE_METRIC_MISMATCH if key in {
                    "worst_score", "score_sum", "mean_numerator", "mean_denominator"
                } else REASON_DISTINCT_SIGNATURE_COUNT_MISMATCH if key == "distinct_signature_count"
                else REASON_SUPPLY_COUNT_MISMATCH if key == "supply_count"
                else REASON_CONTENT_HASH_MISMATCH)
            raise AdmissionError(reason, key)
    if receipt.get("content_hash") != _content_hash(receipt):
        raise AdmissionError(REASON_CONTENT_HASH_MISMATCH, "")
