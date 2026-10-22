#!/usr/bin/env python3
# =============================================================================
# scripts/predict_fl4678_exact_cell_glyph.py
# =============================================================================
# WHAT THIS SCRIPT IS FOR
# -----------------------
# It states, in advance and without running the game, which exact character
# shape the native selector WOULD choose for a named cell.
#
# WHY THAT MATTERS
# ----------------
# A prediction that can be wrong is the only kind worth making. FL-4678 asks for
# the smallest instrument that can falsify the exact-cell claim: for one named
# cell, does a controlled change to the renderer's description of that cell
# reach the compiled address we expect, and produce the glyph we expect? This
# script is one half of that instrument. It reads the compiled artifact and
# writes down an answer. The other half -- a small C++ program that asks the
# real native selector the same question -- lives in
# scripts/tests/test_fl4678_exact_cell_predictor.py. If the two ever disagree,
# something is wrong, and the test says so.
#
# The point of predicting in a DIFFERENT language, from a DIFFERENT file, is
# that agreement then means something. If this script called the C++ code, or
# read the same binary sidecar the C++ code reads, the two could be wrong
# together and no one would notice. So this reads the original JSON artifact
# (assets/glyphs/generated/fl4676.glyph_fact.v1.json) plus the compiled pool
# (assets/glyphs/generated/fl4512.compiled_pool.v1.json) while the native side
# reads the compiled binary built from them.
#
# WHAT IT DOES NOT DO
# -------------------
# It does not run the game, open a window, or draw anything. It says nothing
# about whether normal play ever produces a cell like the ones it is given. That
# is a live question and is left open, on purpose.
#
# THE SELECTION RULES, WHICH MUST MATCH platform/fl4683_exact_cell_selector.cpp
# ----------------------------------------------------------------------------
# Result schema v3 + FL-4512 Phase 2. Step numbers match the native comments so
# the two can be read side by side:
#   1. The row must carry the expected schema version, be marked valid, and
#      declare a legal route-family mask.
#   1b. Filled promotion: a direct filled claim, or a tonal claim with a live
#      filled primitive, consults the compiled filled fact; the projected extent
#      either admits the filled family or falls through to the tonal pair.
#   2. Each declared family's lane comes from that family's own bin field.
#   3. Every declared family must resolve to a compiled address. If any one does
#      not, the whole selection is refused.
#   3b. If EVERY family of the final arbitration pass is compiled-absent or
#      policy-declined, the cell is program-owned declared absence. A MIXED
#      compiled-absence row (some populated, some absent) refuses (FL-4686).
#   3c. FL-4512 Phase 2: a structural shape6 row DECLINES when its compiled
#      degenerate-operand flag fires on a constant live operand, or when its
#      compiled no-match bound admits no candidate. A structural decline with
#      no winner triggers exactly one pass-1 rearbitration on the tonal pair.
#   3d. FL-4763 structure/tone separation: a structural family (edge, direction,
#      flow, mesh bundle) may only be answered by a scorer whose live operand is
#      structural evidence -- object-space u7 mesh mask or authored S_pf
#      primitive field. The Harri shape6 operand comes from the pre-glyph tonal
#      raster, so a structural family naming it DECLINES exactly as in 3c.
#      Checked before the 4 operand-validity gates, not after.
#   4. The winner is chosen across the union of every resolved family's legal
#      glyphs using the fact's compiler-authored scorer (C.10 F8/W4 Harri,
#      u7.mask, or S_pf). Membership weight 0 is inadmissible; membership never
#      ranks. Tie-break: lower score, then lower glyph_record_id, then lower
#      route-family bit.
#   5. The winning glyph must be in the compiled admitted universe.
#
# USAGE
#   python3 scripts/predict_fl4678_exact_cell_glyph.py \
#       --fixture assets/glyphs/fixtures/fl4678.exact_cell_scene_facts.v1.json
#
# Fixture rows MAY carry the live v5 scorer operands the renderer would supply
# (harri_shape6_q0_16, mesh_target_*, primitive_target_*); a missing operand
# block predicts exactly what the native selector does for an invalid operand.
# =============================================================================

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

# The compiled artifact that owns every address and every legal glyph list.
GLYPH_FACT_ARTIFACT = Path("assets/glyphs/generated/fl4676.glyph_fact.v1.json")
# FL-4686 PA / FL-4512 Phase 2. The pool enumerates every address in the
# profile-scoped space including the deliberately empty ones, and its
# materials[] own the no_match_policy and filled-extent admission the program
# compiler joins into each fact. The predictor reads the same JSON inputs the
# program compiler reads -- never the binary sidecar the native side loads.
COMPILED_POOL_ADDRESS_SPACE = Path("assets/glyphs/generated/fl4512.compiled_pool.v1.json")
# The default set of named cells to predict for.
DEFAULT_FIXTURE = Path("assets/glyphs/fixtures/fl4678.exact_cell_scene_facts.v1.json")

# The scene-fact schema version this predictor understands. Mirrors
# FL4683_SCENE_FACT_SCHEMA_VERSION in platform/fl4683_scene_fact_record.h.
SCENE_FACT_SCHEMA_VERSION = 2

# Route-family bits, mirroring Fl4683RouteFamilyV1. Listed smallest first
# because both the native walk order and the tie-break rules refer to
# "the lower route-family bit".
ROUTE_FAMILY_BITS: List[tuple] = [
    (1 << 0, "ramp.v1", "ramp_bin"),
    (1 << 1, "density.v1", "density_bin"),
    (1 << 2, "edge.v1", "edge_bin"),
    (1 << 3, "direction.v1", "direction_bin"),
    (1 << 4, "flow.v1", "flow_bin"),
    (1 << 5, "filled.field.v1", None),
    (1 << 6, "mesh_feature.bundle.v1", None),
]

# Every legal bit OR-ed together, used to spot a bit nobody has defined.
ALL_ROUTE_FAMILY_BITS = 0
for _bit, _key, _field in ROUTE_FAMILY_BITS:
    ALL_ROUTE_FAMILY_BITS |= _bit

# The only legal multi-family mask: a tonal cell spans ramp and density.
TONAL_PAIR_MASK = (1 << 0) | (1 << 1)
FILLED_FIELD_BIT = 1 << 5

# The structural families whose decline may hand the cell to tone. Mirrors
# kStructuralFamilies in platform/fl4683_exact_cell_selector.cpp: edge,
# direction, flow, and the mesh feature bundle.
STRUCTURAL_FAMILY_MASK = (1 << 2) | (1 << 3) | (1 << 4) | (1 << 6)

# Namespace words used by the compiler, and the numbers the renderer emits.
NAMESPACE_CODE = {"terrain": 1, "mesh": 2}

# Scorer kinds, mirroring the loader vocabulary and SCORER_KIND_BY_ID in
# scripts/compile_fl4683_glyph_program.py (including the legacy Harri label the
# pool still uses, which the compiler maps to the exact v2 scorer).
SCORER_KIND_SHAPE6_WEIGHTED_L2 = 1
SCORER_KIND_U7_MASK = 2
SCORER_KIND_S_PF = 3
# FL-4763 explicit compiler-owned decline; mirrors
# FL4683_GLYPH_PROGRAM_SCORER_STRUCTURAL_DECLINE in the loader header. The row is
# populated but carries no live operand, so it is never scored.
SCORER_KIND_STRUCTURAL_DECLINE = 4
SCORER_KIND_BY_ID = {
    "fl4512.shape6_f8_w4.v2": SCORER_KIND_SHAPE6_WEIGHTED_L2,
    "fl4260.shape6_weighted_l2.v1": SCORER_KIND_SHAPE6_WEIGHTED_L2,
    "u7.mask.v1": SCORER_KIND_U7_MASK,
    "S_pf.v1": SCORER_KIND_S_PF,
    "fl4763.structural_decline.v1": SCORER_KIND_STRUCTURAL_DECLINE,
}
# The kinds this predictor can execute. Kept separate from the structural test
# so an UNKNOWN kind refuses instead of being folded into a decline -- see the
# native Fl4683ScorerKindIsKnownV1 for the full reasoning.
SCORER_KINDS_KNOWN = frozenset({
    SCORER_KIND_SHAPE6_WEIGHTED_L2,
    SCORER_KIND_U7_MASK,
    SCORER_KIND_S_PF,
    SCORER_KIND_STRUCTURAL_DECLINE,
})

# FL-4512 Phase 2 fact policy. Mirrors Fl4683FactPolicyFlagV1 and the emission
# rules in scripts/compile_fl4683_glyph_program.py fact_shape6_no_match_policy:
# only STRUCTURAL shape6 route keys may carry the degenerate-operand abstain
# flag; tonal shape6 rows never do (tonal_constant_operand_legal).
FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN = 0x1
STRUCTURAL_SHAPE6_ROUTE_KEYS = frozenset({"edge.v1", "direction.v1", "flow.v1"})
TONAL_SHAPE6_ROUTE_KEYS = frozenset({"ramp.v1", "density.v1"})

# Rejection names, mirroring Fl4683RejectionReasonV1. The NUMBER is what the
# native side prints, so both are recorded and the number is what gets compared.
REJECTION_CODE = {
    "none": 0,
    "runtime_rights_blocked": 1,
    "missing_artifact": 2,
    "artifact_sha_mismatch": 3,
    "glyph_universe_mismatch": 4,
    "scene_schema_mismatch": 5,
    "unknown_glyph_fact": 6,
    "unknown_glyph_record": 7,
    "missing_compiled_address": 8,
    "empty_legal_row": 9,
    "unadmitted_glyph": 10,
    "missing_scene_fact": 11,
    "malformed_scene_fact": 12,
    "frame_order_violation": 13,
    "adapter_abi_mismatch": 14,
    "missing_scene_descriptor": 15,
    "unsupported_compiled_scorer": 16,
}

# Pulls "ramp[2]" apart into its family name and its lane number.
LANE_PATTERN = re.compile(r"^(?P<name>[a-z_]+)\[(?P<index>\d+)\]$")

# FL-4686 PA. The three outcomes, mirroring Fl4683ExactCellOutcomeV1 in
# platform/fl4683_exact_cell_selector.h. The NUMBER is what the native side
# reports, so both the name and the number are recorded here.
OUTCOME_CODE = {
    "rejected": 0,
    "selected": 1,
    "declared_absence": 2,
}

# The bucket_state values the compiled pool uses, mirroring the sidecar
# compiler. Read from the pool rather than assumed.
POOL_STATE_POPULATED = "live"
POOL_STATE_DECLARED_ABSENT = "empty_absence"


class PredictError(RuntimeError):
    """Raised when an input is missing or does not have the expected shape."""


def load_json(relative: Path) -> Any:
    """Read one JSON file from the repository, failing with a clear name."""
    absolute = ROOT / relative
    if not absolute.is_file():
        raise PredictError(f"required input is missing: {relative}")
    return json.loads(absolute.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# The three compiled scorers, mirrored integer for integer
# -----------------------------------------------------------------------------
def quant_shape6_q0_16_to_f8(value: int) -> int:
    """C.10 F8 quantization over the canonical q0.16 descriptor.

    Mirrors QuantShapeQ0_16ToF8 in platform/fl4683_exact_cell_selector.cpp:
    adding floor(65535/2) implements half-up rounding for an odd denominator,
    floor((q/65535)*256 + 0.5), yielding exactly 0..256.
    """
    return (value * 256 + 32767) // 65535


def score_shape6(
    role_weights_q4: Tuple[int, ...], live_q0_16: List[int], cand_q0_16: Tuple[int, ...]
) -> int:
    """Frozen W4 integer Harri scorer over BOTH F8-quantized six-vectors.

    score = sum(role_weights_q4[axis] * (live_f8 - cand_f8)^2). Both operands
    are quantized the same way, exactly as ScoreCandidate does natively.
    """
    score = 0
    for axis in range(6):
        difference = quant_shape6_q0_16_to_f8(live_q0_16[axis]) - quant_shape6_q0_16_to_f8(
            cand_q0_16[axis]
        )
        score += role_weights_q4[axis] * (difference * difference)
    return score


def score_u7_mask(
    target_mask8: int,
    candidate_mask8: int,
    target_endpoints: int,
    candidate_endpoints: int,
    target_junctions: int,
    candidate_junctions: int,
) -> int:
    """Mirror of Fl4512ScoreMaskV1 in apps/fl4512-runtime/engine/fl4512_u7_score.h.

    70 * hamming(mask8) + 15 * |dEndpoints| + 15 * |dJunctions|. No tonal
    descriptor and no membership weight participates.
    """
    hamming = bin(target_mask8 ^ candidate_mask8).count("1")
    return (
        70 * hamming
        + 15 * abs(target_endpoints - candidate_endpoints)
        + 15 * abs(target_junctions - candidate_junctions)
    )


def _pf_round_half_up(numerator: int, denominator: int) -> int:
    """Mirror of Fl4512PfRoundHalfUp: (n + d/2) / d in integers, 0 when d==0."""
    return (numerator + denominator // 2) // denominator if denominator else 0


def _pf_boundary4(mask: int) -> int:
    """Mirror of Fl4512PfBoundary4: 4-neighborhood boundary of an 8x8 mask."""
    out = 0
    for y in range(8):
        for x in range(8):
            bit = 1 << (8 * y + x)
            if not mask & bit:
                continue
            inside = 0 < x < 7 and 0 < y < 7
            if inside:
                inside = bool(mask & (bit >> 1)) and bool(mask & (bit << 1)) and bool(
                    mask & (bit >> 8)
                ) and bool(mask & (bit << 8))
            if not inside:
                out |= bit
    return out


def _pf_directed_distance(a: int, b: int) -> int:
    """Mirror of Fl4512PfDirectedDistance: sum over a-pixels of the nearest
    b-pixel squared distance, capped at 98 (the 8x8 diagonal) when b is empty
    or far."""
    b_points = [
        (bx, by) for by in range(8) for bx in range(8) if b >> (8 * by + bx) & 1
    ]
    total = 0
    for ay in range(8):
        for ax in range(8):
            if not a >> (8 * ay + ax) & 1:
                continue
            best = 98
            for bx, by in b_points:
                d = (ax - bx) * (ax - bx) + (ay - by) * (ay - by)
                if d < best:
                    best = d
            total += best
    return total


def score_primitive_field(
    target_mask8: int,
    candidate_mask8: int,
    target_region: int,
    candidate_region: int,
    target_priority: int,
    candidate_detail: int,
) -> Optional[int]:
    """Mirror of Fl4512ScorePrimitiveFieldV1 in engine/render/fl4512_primitive_field.h.

    Returns the fixed-point score numerator, or None where the native function
    returns false (empty mask, no boundary pixels, or score beyond the 6553500
    ceiling) -- which the native selector converts to a MISSING_SCENE_DESCRIPTOR
    refusal.
    """
    if not target_mask8 or not candidate_mask8:
        return None
    target_boundary = _pf_boundary4(target_mask8)
    candidate_boundary = _pf_boundary4(candidate_mask8)
    boundary_count = bin(target_boundary).count("1") + bin(candidate_boundary).count("1")
    if not boundary_count:
        return None
    d_occ = _pf_round_half_up(65535 * bin(target_mask8 ^ candidate_mask8).count("1"), 64)
    chamfer = _pf_directed_distance(target_boundary, candidate_boundary) + _pf_directed_distance(
        candidate_boundary, target_boundary
    )
    d_sil = _pf_round_half_up(65535 * chamfer, 98 * boundary_count)
    d_reg = 0 if target_region == candidate_region else 65535
    d_pri = abs(target_priority - candidate_detail)
    score = 50 * d_occ + 25 * d_sil + 15 * d_reg + 10 * d_pri
    return score if score <= 6553500 else None


# -----------------------------------------------------------------------------
# Compiled program reconstruction (the same joins the program compiler makes)
# -----------------------------------------------------------------------------
def quantize_role_weight_q4(value: Any, what: str) -> int:
    """Apply the frozen Harri profile-weight transform.

    Mirrors quantize_role_weight_q4 in scripts/compile_fl4683_glyph_program.py:
    q_W(w) = round-half-up(clamp(w, 0, 1.5) * 16), one byte in 0..24. Decimal
    arithmetic avoids binary-float ties-to-even becoming a second policy.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PredictError(f"{what} is not a numeric role weight")
    weight = Decimal(str(value))
    if not weight.is_finite():
        raise PredictError(f"{what} is not a finite role weight")
    clamped = min(max(weight, Decimal("0")), Decimal("1.5"))
    quantized = int((clamped * Decimal("16")).to_integral_value(rounding=ROUND_HALF_UP))
    if not 0 <= quantized <= 24:
        raise PredictError(f"{what} quantized outside q4 range: {quantized}")
    return quantized


def decode_u64_le_hex(value: Any, what: str) -> int:
    """Decode the artifact's canonical eight little-endian carrier bytes."""
    if not isinstance(value, str) or len(value) != 16:
        raise PredictError(f"{what} is not a 16-character u64 little-endian hex value")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise PredictError(f"{what} is not valid hexadecimal: {exc}") from exc
    return int.from_bytes(raw, "little")


def build_role_weight_index(artifact: Dict[str, Any]) -> Dict[str, Tuple[int, ...]]:
    """profile_id -> six frozen q4 Harri role weights, exactly as compiled."""
    weights: Dict[str, Tuple[int, ...]] = {}
    for profile_index, profile in enumerate(artifact["profiles"]):
        profile_id = str(profile["profile_id"])
        raw = profile.get("role_weights")
        if not isinstance(raw, list) or len(raw) != 6:
            raise PredictError(
                f"profiles[{profile_index}] role_weights must hold exactly 6 values"
            )
        weights[profile_id] = tuple(
            quantize_role_weight_q4(value, f"profiles[{profile_index}].role_weights")
            for value in raw
        )
    return weights


def build_pool_descriptor_index(
    pool: Dict[str, Any],
) -> Dict[str, Dict[int, Dict[str, Any]]]:
    """row_sha256 -> glyph_id -> compiled candidate descriptor (pool-owned)."""
    index: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for row in pool["rows"]:
        index[str(row["row_sha256"])] = {
            int(descriptor["glyph_id"]): descriptor
            for descriptor in row.get("candidate_descriptors", [])
        }
    return index


def build_pool_material_index(pool: Dict[str, Any]) -> Dict[tuple, Dict[str, Any]]:
    """(profile_namespace, material_key) -> compiled material policy row."""
    return {
        (str(material["profile_namespace"]), int(material["material_key"])): material
        for material in pool["materials"]
    }


def fact_shape6_policy(
    route_key: str, material: Dict[str, Any], fact_name: str
) -> Tuple[int, int]:
    """(policy_flags, no_match_bound_q) for one shape6 fact.

    Mirrors fact_shape6_no_match_policy in scripts/compile_fl4683_glyph_program.py
    rather than re-deciding the rules: the abstain flag is emitted only for
    STRUCTURAL route keys whose material authored structural_degenerate_declines,
    and the bound comes from max_error_q by role. Tonal rows never carry the
    flag (tonal_constant_operand_legal).
    """
    policy = material.get("no_match_policy")
    if not isinstance(policy, dict):
        raise PredictError(f"{fact_name}: material carries no no_match_policy")
    max_error_q = policy["max_error_q"]
    if route_key in STRUCTURAL_SHAPE6_ROUTE_KEYS:
        flags = (
            FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN
            if policy["structural_degenerate_declines"]
            else 0
        )
        return flags, int(max_error_q["structural"])
    if route_key in TONAL_SHAPE6_ROUTE_KEYS:
        return 0, int(max_error_q["tonal"])
    raise PredictError(f"{fact_name}: shape6 route key {route_key!r} has no bound role")


def build_fact_program(
    fact: Dict[str, Any],
    route_key: str,
    role_weights_by_profile: Dict[str, Tuple[int, ...]],
    pool_descriptors_by_row: Dict[str, Dict[int, Dict[str, Any]]],
    pool_materials: Dict[tuple, Dict[str, Any]],
    glyph_records_by_id: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """Reconstruct the compiled per-fact program record from the JSON inputs.

    This performs the SAME joins encode_fact_table makes in
    scripts/compile_fl4683_glyph_program.py, so each field mirrors exactly what
    the native fact record carries:
      scorer_kind      from scene_membership_key.scorer_id
      role_weights_q4  from artifact profiles[] via source_profile_id
      policy_flags     from pool materials[].no_match_policy (shape6 only)
      threshold_u32    shape6 -> compiled no-match bound; S_pf -> extent-enter
                       admission; u7 and structural decline -> 0
      members[]        glyph id, admission weight, and the compiler-owned
                       scorer operand (shape6_norm_q0_16 from glyph_records[],
                       u7 mask/topology from glyph_records[], S_pf mask/region/
                       detail from the pool candidate descriptors; structural
                       decline deliberately contributes no operand fields)
    """
    membership = fact["scene_membership_key"]
    fact_name = str(fact.get("fact_id", "fact"))
    scorer_id = membership.get("scorer_id")
    scorer_kind = SCORER_KIND_BY_ID.get(str(scorer_id))
    if scorer_kind is None:
        raise PredictError(f"{fact_name}: unknown scorer_id {scorer_id!r}")

    material = pool_materials.get(
        (str(membership["profile_namespace"]), int(membership["material_key"]))
    )
    if material is None:
        raise PredictError(f"{fact_name}: no compiled material policy")

    role_weights = role_weights_by_profile.get(str(fact["source_profile_id"]))
    if role_weights is None:
        raise PredictError(f"{fact_name}: source_profile_id names no profile")

    policy_flags = 0
    threshold_u32 = 0
    primitive_region = 0
    primitive_priority = 0
    if scorer_kind == SCORER_KIND_SHAPE6_WEIGHTED_L2:
        policy_flags, threshold_u32 = fact_shape6_policy(
            str(membership["route_key"]), material, fact_name
        )
    elif scorer_kind == SCORER_KIND_S_PF:
        # Mirrors fact_primitive_policy: the record's u32 keeps its extent-enter
        # meaning for the primitive scorer, and the target region/priority come
        # from the owning material.
        primitive_region = int(material["filled_region_class"])
        primitive_priority = int(material["filled_priority_u16"])
        threshold_u32 = int(
            material["projected_feature_extent_admission"]["filled_threshold"]["enter_q"]
        )

    descriptors = pool_descriptors_by_row.get(str(membership["row_sha256"]), {})
    members: List[Dict[str, Any]] = []
    for member in fact["glyph_members"]:
        record_id = int(member["glyph_record_id"])
        weight = int(member["membership_weight_q0_16"])
        entry: Dict[str, Any] = {"glyph_record_id": record_id, "weight": weight}
        if scorer_kind == SCORER_KIND_SHAPE6_WEIGHTED_L2:
            record = glyph_records_by_id.get(record_id)
            shape6 = (
                record.get("morphology", {}).get("shape6_norm_q0_16")
                if isinstance(record, dict)
                else None
            )
            if not isinstance(shape6, list) or len(shape6) != 6:
                raise PredictError(
                    f"{fact_name}: member {record_id} has no shape6_norm_q0_16[6]"
                )
            entry["shape6_norm_q0_16"] = tuple(int(v) for v in shape6)
        elif scorer_kind == SCORER_KIND_U7_MASK:
            record = glyph_records_by_id.get(record_id)
            if not isinstance(record, dict):
                raise PredictError(f"{fact_name}: member {record_id} has no glyph record")
            entry["mask8"] = decode_u64_le_hex(
                record["connectivity"]["mask8_u64_le_hex"],
                f"{fact_name} member {record_id} mask8",
            )
            entry["endpoint_count"] = int(record["structure"]["endpoint_count"])
            entry["junction_count"] = int(record["structure"]["junction_count"])
        elif scorer_kind == SCORER_KIND_S_PF:
            descriptor = descriptors.get(record_id)
            if descriptor is None:
                raise PredictError(
                    f"{fact_name}: member {record_id} has no pool candidate descriptor"
                )
            entry["mask8"] = decode_u64_le_hex(
                descriptor["mask8_u64_le_hex"],
                f"{fact_name} member {record_id} pool mask8",
            )
            entry["filled_region_class"] = int(descriptor["filled_region_class"])
            entry["detail_u16"] = int(descriptor["detail_u16"])
        elif scorer_kind == SCORER_KIND_STRUCTURAL_DECLINE:
            descriptor = descriptors.get(record_id)
            if (
                not isinstance(descriptor, dict)
                or set(descriptor) != {"glyph_id"}
                or int(descriptor["glyph_id"]) != record_id
            ):
                raise PredictError(
                    f"{fact_name}: declining member {record_id} must carry "
                    "glyph_id membership and no scorer operand"
                )
            # Membership and weight remain compiled facts. No shape6, mask,
            # topology, primitive, or threshold field is invented here.
        members.append(entry)

    return {
        "scorer_kind": scorer_kind,
        "role_weights_q4": role_weights,
        "policy_flags": policy_flags,
        "threshold_u32": threshold_u32,
        "primitive_target_region_class": primitive_region,
        "primitive_target_priority_u16": primitive_priority,
        "members": members,
    }


def build_address_index(
    artifact: Dict[str, Any], pool: Dict[str, Any]
) -> Dict[tuple, Dict[str, Any]]:
    """Build the same lookup table the binary sidecar carries, from the JSON.

    Parameters:
      artifact -- the parsed compiled artifact.
      pool     -- the parsed compiled pool, which enumerates every address in
                  the profile-scoped space including the deliberately empty
                  ones (FL-4686 PA), and owns the material policies (FL-4512
                  Phase 2).

    Returns a dictionary whose key is
    (namespace code, material key, route-family bit, lane index) and whose value
    is `{"disposition": ..., "fact": ..., "program": ...}`. `fact` and
    `program` are None for a declared-absent address.

    WHY THIS HAS TO BE BUILT AT ALL: the artifact identifies each address by a
    fingerprint of the whole compiled row. Nothing that only knows what a cell
    LOOKS like can compute that fingerprint, so a lookup keyed on the things a
    renderer does know has to exist. The offline compiler packs exactly this
    table into the sidecar; rebuilding it here independently is what lets the
    two sides be compared rather than merely agree by construction.
    """
    route_key_to_bit = {key: bit for bit, key, _field in ROUTE_FAMILY_BITS}
    role_weights_by_profile = build_role_weight_index(artifact)
    pool_descriptors_by_row = build_pool_descriptor_index(pool)
    pool_materials = build_pool_material_index(pool)
    glyph_records_by_id = {
        int(record["glyph_record_id"]): record for record in artifact["glyph_records"]
    }
    index: Dict[tuple, Dict[str, Any]] = {}

    for position, fact in enumerate(artifact["facts"]):
        membership = fact["scene_membership_key"]

        namespace_code = NAMESPACE_CODE.get(str(membership["profile_namespace"]))
        if namespace_code is None:
            raise PredictError(
                f"facts[{position}] uses namespace "
                f"{membership['profile_namespace']!r}, which no renderer "
                "namespace code matches"
            )

        family_bit = route_key_to_bit.get(str(membership["route_key"]))
        if family_bit is None:
            raise PredictError(
                f"facts[{position}] uses route key {membership['route_key']!r}, "
                "which the route-family discriminator does not define"
            )

        # A lane name without a bracketed number is a single-lane family.
        match = LANE_PATTERN.match(str(membership["lane_name"]))
        lane_index = int(match.group("index")) if match else 0

        key = (
            namespace_code,
            int(membership["material_key"]),
            family_bit,
            lane_index,
        )
        if key in index:
            raise PredictError(
                f"two compiled addresses share the scene key {key}; a lookup "
                "could not choose between them"
            )
        index[key] = {
            "disposition": "populated",
            "fact": fact,
            "program": build_fact_program(
                fact,
                str(membership["route_key"]),
                role_weights_by_profile,
                pool_descriptors_by_row,
                pool_materials,
                glyph_records_by_id,
            ),
        }

    # ---- FL-4686 PA: add the addresses that hold no glyph -------------------
    # The pool enumerates the whole profile-scoped space. Every row it marks
    # empty becomes a declared-absent entry, so the predictor answers such a
    # cell the same way the native selector now does instead of refusing it.
    for row_index, row in enumerate(pool["rows"]):
        namespace_code = NAMESPACE_CODE.get(str(row["profile_namespace"]))
        if namespace_code is None:
            raise PredictError(
                f"compiled pool row {row_index} uses namespace "
                f"{row['profile_namespace']!r}, which no renderer namespace "
                "code matches"
            )
        family_bit = route_key_to_bit.get(str(row["route_key"]))
        if family_bit is None:
            raise PredictError(
                f"compiled pool row {row_index} uses route key "
                f"{row['route_key']!r}, which the route-family discriminator "
                "does not define"
            )
        match = LANE_PATTERN.match(str(row["lane_name"]))
        lane_index = int(match.group("index")) if match else 0
        key = (namespace_code, int(row["material_key"]), family_bit, lane_index)

        state = str(row["bucket_state"])
        if state == POOL_STATE_POPULATED:
            # Cross-check: the pool says this address holds glyphs, so the
            # artifact must have supplied them. A disagreement means the two
            # inputs were built from different sources.
            if key not in index:
                raise PredictError(
                    f"compiled pool row {row_index} is marked "
                    f"{POOL_STATE_POPULATED!r} but address {key} supplies no "
                    "glyphs in the glyph-fact artifact"
                )
            continue
        if state != POOL_STATE_DECLARED_ABSENT:
            raise PredictError(
                f"compiled pool row {row_index} has bucket_state {state!r}, "
                "which this predictor will not guess the disposition of"
            )
        if key in index:
            raise PredictError(
                f"address {key} is both a compiled fact and a declared absence"
            )
        index[key] = {"disposition": "declared_absent", "fact": None, "program": None}

    return index


def route_family_mask_is_legal(mask: int) -> bool:
    """Report whether a route-family mask is one the contract allows.

    Legal means: at least one bit set, no undefined bit set, and -- when more
    than one bit is set -- exactly the tonal ramp+density pair. Mirrors
    Fl4683RouteFamilyMaskIsLegalV1 in the C++ record header.
    """
    if mask == 0:
        return False
    if mask & ~ALL_ROUTE_FAMILY_BITS:
        return False
    # `mask & (mask - 1)` clears the lowest set bit; zero means only one was set.
    if mask & (mask - 1) == 0:
        return True
    return mask == TONAL_PAIR_MASK


def read_live_operands(row: Dict[str, Any]) -> Dict[str, Any]:
    """Read the OPTIONAL live v5 scorer operands from one fixture row.

    These mirror the SceneFactV1 operand fields the renderer owns. A fixture
    row that carries none predicts exactly what the native selector does for a
    zeroed/invalid operand block, which is what the harness feeds it too.
    """
    harri = row.get("harri_shape6_q0_16", [0, 0, 0, 0, 0, 0])
    if not isinstance(harri, list) or len(harri) != 6:
        raise PredictError(f"row {row.get('name')!r} harri_shape6_q0_16 must hold 6 values")
    return {
        "harri_valid": int(
            row.get("harri_shape6_valid", 1 if "harri_shape6_q0_16" in row else 0)
        ),
        "harri": [int(value) for value in harri],
        "mesh_valid": int(
            row.get("mesh_target_valid", 1 if "mesh_target_mask8_le_hex" in row else 0)
        ),
        "mesh_mask8": decode_u64_le_hex(
            row.get("mesh_target_mask8_le_hex", "0000000000000000"),
            f"row {row.get('name')!r} mesh_target_mask8_le_hex",
        )
        if "mesh_target_mask8_le_hex" in row
        else 0,
        "mesh_endpoint_count": int(row.get("mesh_target_endpoint_count", 0)),
        "mesh_junction_count": int(row.get("mesh_target_junction_count", 0)),
        "primitive_valid": int(
            row.get(
                "primitive_target_valid",
                1 if "primitive_target_mask8_le_hex" in row else 0,
            )
        ),
        "primitive_mask8": decode_u64_le_hex(
            row.get("primitive_target_mask8_le_hex", "0000000000000000"),
            f"row {row.get('name')!r} primitive_target_mask8_le_hex",
        )
        if "primitive_target_mask8_le_hex" in row
        else 0,
        "primitive_extent_q": int(row.get("primitive_projected_extent_q", 0)),
    }


def lane_for_family(row: Dict[str, Any], lane_field: Optional[str]) -> int:
    """Mirror of Fl4683SceneFactLaneForRouteFamilyV1: the family's own bin
    field owns the lane; single-lane families always use lane 0."""
    return 0 if lane_field is None else int(row[lane_field])


def refuse(reason: str) -> Dict[str, Any]:
    """Build a refusal prediction with a named, numbered reason.

    FL-4686 PA: a refusal always carries outcome "rejected". Declared absence is
    a positive answer built by `declare_absent` below and can never be produced
    by a failure path.
    """
    return {
        "accepted": False,
        "outcome": "rejected",
        "outcome_code": OUTCOME_CODE["rejected"],
        "rejection_reason": reason,
        "rejection_code": REJECTION_CODE[reason],
    }


def declare_absent(
    resolved: List[Dict[str, Any]],
    declared_absent_count: int,
    policy_declined_total: int,
    policy_declined_mask: int,
) -> Dict[str, Any]:
    """Build a program-owned declared-absence prediction. FL-4686 PA.

    Parameters:
      resolved              -- the final-pass declared families.
      declared_absent_count -- how many of them resolved to compiled-empty
                               addresses (the rest declined under FL-4512
                               Phase 2 compiled policy).
      policy_declined_total -- Phase 2 decline count across BOTH passes.
      policy_declined_mask  -- exact declining route-family bits, both passes.

    This is a SUCCESS, not a refusal: `rejection_reason` is "none" and no glyph
    is named because none is meant to be drawn. `accepted` stays False because
    that field means "a glyph was selected"; callers branch on `outcome`. The
    native selector reports considered_candidate_count = 0 here even when
    candidates were scored in a declined pass, so the predictor does too.
    """
    return {
        "accepted": False,
        "outcome": "declared_absence",
        "outcome_code": OUTCOME_CODE["declared_absence"],
        "rejection_reason": "none",
        "rejection_code": REJECTION_CODE["none"],
        "resolved_family_count": len(resolved),
        "declared_absent_family_count": declared_absent_count,
        "policy_abstained_family_count": policy_declined_total,
        "policy_abstained_family_mask": policy_declined_mask,
        "considered_candidate_count": 0,
        "resolved_addresses": [
            {
                "route_key": entry["route_key"],
                "route_family_bit": entry["route_family_bit"],
                "lane_index": entry["lane_index"],
                "disposition": entry["disposition"],
            }
            for entry in resolved
        ],
    }


def score_member(
    program: Dict[str, Any], member: Dict[str, Any], live: Dict[str, Any]
) -> Optional[int]:
    """Mirror of ScoreCandidate: one candidate through the fact's own scorer.

    Returns the exact fixed-point score, or None where the native helper
    returns false (which the selector refuses as MISSING_SCENE_DESCRIPTOR).
    """
    kind = program["scorer_kind"]
    if kind == SCORER_KIND_SHAPE6_WEIGHTED_L2:
        if not live["harri_valid"]:
            return None
        return score_shape6(
            program["role_weights_q4"], live["harri"], member["shape6_norm_q0_16"]
        )
    if kind == SCORER_KIND_U7_MASK:
        if not live["mesh_valid"]:
            return None
        return score_u7_mask(
            live["mesh_mask8"],
            member["mask8"],
            live["mesh_endpoint_count"],
            member["endpoint_count"],
            live["mesh_junction_count"],
            member["junction_count"],
        )
    if kind == SCORER_KIND_S_PF:
        if (
            not live["primitive_valid"]
            or live["primitive_mask8"] == 0
            or live["primitive_extent_q"] < program["threshold_u32"]
        ):
            return None
        return score_primitive_field(
            live["primitive_mask8"],
            member["mask8"],
            program["primitive_target_region_class"],
            member["filled_region_class"],
            program["primitive_target_priority_u16"],
            member["detail_u16"],
        )
    return None


def predict_row(
    row: Dict[str, Any],
    address_index: Dict[tuple, Dict[str, Any]],
    admitted_glyph_record_ids: set,
) -> Dict[str, Any]:
    """Predict the exact glyph the native selector would choose for one row.

    Parameters:
      row                       -- one scene-fact row from the fixture.
      address_index             -- the table from `build_address_index`.
      admitted_glyph_record_ids -- every glyph identifier the compiler admitted.

    Returns a dictionary describing the chosen glyph, the declared absence, or
    the refusal. The steps below are numbered to match the comments in
    platform/fl4683_exact_cell_selector.cpp, so the two can be read side by side.
    """
    # ---- Step 1: is this a row this contract understands? -------------------
    if int(row["schema_version"]) != SCENE_FACT_SCHEMA_VERSION:
        return refuse("scene_schema_mismatch")
    if not int(row["valid"]):
        return refuse("missing_scene_fact")

    mask = int(row["route_family_mask"])
    if not route_family_mask_is_legal(mask):
        return refuse("malformed_scene_fact")

    live = read_live_operands(row)
    namespace_code = int(row["material_namespace"])
    material_key = int(row["material_key"])

    # ---- Step 1b: filled promotion (compiled extent admission) --------------
    # Mirrors the potential-filled-claim block: a direct filled claim, or a
    # tonal claim with a live filled primitive, consults the compiled filled
    # fact. A missing or declared-absent filled address falls through to the
    # tonal pair; a populated one admits filled only when the projected extent
    # meets its compiled enter threshold.
    selection_mask = mask
    direct_filled_claim = selection_mask == FILLED_FIELD_BIT
    potential_filled_claim = direct_filled_claim or (
        selection_mask == TONAL_PAIR_MASK and live["primitive_valid"]
    )
    if potential_filled_claim:
        if not live["primitive_valid"] or live["primitive_mask8"] == 0:
            return refuse("missing_scene_descriptor")
        filled_entry = address_index.get(
            (namespace_code, material_key, FILLED_FIELD_BIT, 0)
        )
        if filled_entry is None or filled_entry["disposition"] == "declared_absent":
            # Natively: DECLARED_ABSENCE or REJECTED/MISSING_COMPILED_ADDRESS
            # from the filled lookup both fall through to the tonal pair.
            selection_mask = TONAL_PAIR_MASK
        else:
            filled_program = filled_entry["program"]
            if filled_program["scorer_kind"] != SCORER_KIND_S_PF:
                return refuse("unsupported_compiled_scorer")
            if live["primitive_extent_q"] >= filled_program["threshold_u32"]:
                selection_mask = FILLED_FIELD_BIT
            else:
                selection_mask = TONAL_PAIR_MASK

    # ---- Steps 2-4: two-pass arbitration (FL-4512 Phase 2) ------------------
    # Pass 0 runs the (possibly promoted) declared families. When a STRUCTURAL
    # family declines its claim under compiled policy and no family produced a
    # winner, pass 1 reruns arbitration once on the lower-authority tonal pair.
    # Only `considered`, `policy_declined_total`, and the decline mask survive
    # between passes -- exactly as in the native selector.
    considered = 0
    policy_declined_total = 0
    policy_declined_mask = 0
    effective_mask = selection_mask
    best: Optional[Dict[str, Any]] = None
    resolved: List[Dict[str, Any]] = []
    declared_absent_count = 0
    pass_declined_count = 0

    for pass_index in range(2):
        best = None
        resolved = []
        declared_absent_count = 0
        pass_declined_count = 0
        structural_declined = False

        for family_bit, route_key, lane_field in ROUTE_FAMILY_BITS:
            if not effective_mask & family_bit:
                continue
            lane_index = lane_for_family(row, lane_field)
            if lane_index == 255:
                return refuse("malformed_scene_fact")
            entry = address_index.get(
                (namespace_code, material_key, family_bit, lane_index)
            )
            if entry is None:
                # The address is OUTSIDE the profile-scoped space entirely. That
                # is a genuine miss and stays a failure: an address the program
                # never enumerated is not an address the program owns.
                return refuse("missing_compiled_address")
            if entry["disposition"] == "declared_absent":
                # FL-4686 PA. The program owns this address and states it is
                # empty. No scoring loop: there are no candidates to score.
                resolved.append(
                    {
                        "route_family_bit": family_bit,
                        "route_key": route_key,
                        "lane_index": lane_index,
                        "disposition": "declared_absent",
                        "members": [],
                    }
                )
                declared_absent_count += 1
                continue
            fact = entry["fact"]
            program = entry["program"]
            if not fact["glyph_members"]:
                # A fact that exists but carries no members is a compiled
                # contradiction, NOT a declared absence. Declared absence is
                # stated by the address disposition, never inferred.
                return refuse("empty_legal_row")
            resolved.append(
                {
                    "route_family_bit": family_bit,
                    "route_key": route_key,
                    "lane_index": lane_index,
                    "disposition": "populated",
                    "fact_id": fact["fact_id"],
                    "compiled_address_sha256": fact["scene_membership_key"]["row_sha256"],
                    "members": fact["glyph_members"],
                }
            )

            # ---- Step 4 gates, in the native order --------------------------
            kind = program["scorer_kind"]
            # FL-4763 structure/tone separation. Mirrors the native gate in
            # Fl4683ExactCellSelectV1, and mirrors its POSITION too: it runs
            # before the operand-validity check below, so a structural family
            # declines whether or not a shape6 vector was published.
            #
            # A structural family (edge, direction, flow, mesh bundle) may only
            # be answered by a scorer whose live operand is structural evidence
            # -- the object-space u7 mesh mask or the authored S_pf primitive
            # field. The shape6 operand is derived from the pre-glyph TONAL
            # colour raster, so answering a structural claim from it would
            # decide the shape of a feature from how the cell was shaded. Such
            # a family declines; the tonal pair may then take the cell.
            # FL-4763 gate 0: an unknown scorer REFUSES, it never declines.
            # Mirrors Fl4683ScorerKindIsKnownV1 and its position. A decline is a
            # statement about the scene; an unexecutable scorer is a statement
            # about the artifact, and only the latter may blank the cell.
            if kind not in SCORER_KINDS_KNOWN:
                return refuse("unsupported_compiled_scorer")
            # FL-4763 gate 1: the compiler's EXPLICIT decline. The address is
            # populated but the compiler holds no live structural operand for
            # it, so nothing is scored and the decline is counted. Family
            # independent, exactly as in the native selector.
            if kind == SCORER_KIND_STRUCTURAL_DECLINE:
                pass_declined_count += 1
                policy_declined_total += 1
                policy_declined_mask |= family_bit
                if family_bit & STRUCTURAL_FAMILY_MASK:
                    structural_declined = True
                # Deliberately no scoring loop: the compiler declined this row.
                continue
            # FL-4763 gate 2: defensive structure-vs-tone fence. RETAINED for a
            # LEGACY or malformed artifact whose structural rows still name the
            # tonal shape6 scorer; gate 1 owns every row the current compiler
            # emits.
            if family_bit & STRUCTURAL_FAMILY_MASK and kind not in (
                SCORER_KIND_U7_MASK,
                SCORER_KIND_S_PF,
            ):
                pass_declined_count += 1
                policy_declined_total += 1
                policy_declined_mask |= family_bit
                structural_declined = True
                # Deliberately no scoring loop: no evidence to score with.
                continue
            if kind == SCORER_KIND_SHAPE6_WEIGHTED_L2 and not live["harri_valid"]:
                return refuse("missing_scene_descriptor")
            # FL-4512 Phase 2: compiled degenerate-operand decline. Fires only
            # after the operand-validity gate, on a live shape6 operand whose
            # six raw q0.16 dims are ALL equal (authored epsilon exactly zero).
            if (
                kind == SCORER_KIND_SHAPE6_WEIGHTED_L2
                and program["policy_flags"] & FACT_POLICY_DEGENERATE_OPERAND_ABSTAIN
                and all(value == live["harri"][0] for value in live["harri"])
            ):
                pass_declined_count += 1
                policy_declined_total += 1
                policy_declined_mask |= family_bit
                if family_bit & STRUCTURAL_FAMILY_MASK:
                    structural_declined = True
                # Deliberately no scoring loop: policy forbids a claim here.
                continue
            if kind == SCORER_KIND_U7_MASK and not live["mesh_valid"]:
                return refuse("missing_scene_descriptor")
            if kind == SCORER_KIND_S_PF and (
                not live["primitive_valid"]
                or live["primitive_mask8"] == 0
                or live["primitive_extent_q"] < program["threshold_u32"]
            ):
                return refuse("missing_scene_descriptor")
            # The unsupported-scorer refusal that used to sit here has MOVED to
            # gate 0 above, matching the native selector. It is not duplicated
            # here: a second copy behind the decline gates would keep the old,
            # wrong ordering alive in the file.

            # The compiled no-match bound for this row (0 = unbounded). For the
            # primitive scorer the same u32 already served as the extent-enter
            # admission above, so the bound applies to the two distance scorers.
            no_match_bound_q = (
                0 if kind == SCORER_KIND_S_PF else program["threshold_u32"]
            )
            family_admissible = 0
            for member in program["members"]:
                # Membership is compiled admission, not a per-frame ranking
                # term. A zero member cannot enter the scorer even if its
                # distance would otherwise be smallest.
                if member["weight"] == 0:
                    continue
                considered += 1
                score = score_member(program, member, live)
                if score is None:
                    return refuse("missing_scene_descriptor")
                # FL-4512 Phase 2: a candidate beyond the row's compiled
                # no-match bound is inadmissible -- a nearest glyph that is
                # still too far is not an answer, it is a declined claim.
                if no_match_bound_q != 0 and score > no_match_bound_q:
                    continue
                family_admissible += 1

                # The tie-break ladder, in order: (a) lower score; (b) on an
                # equal score, lower glyph identifier; (c) on both equal, the
                # lower route-family bit.
                wins = (
                    best is None
                    or score < best["score"]
                    or (
                        score == best["score"]
                        and (
                            member["glyph_record_id"] < best["glyph_record_id"]
                            or (
                                member["glyph_record_id"] == best["glyph_record_id"]
                                and family_bit < best["route_family_bit"]
                            )
                        )
                    )
                )
                if wins:
                    best = {
                        "glyph_record_id": member["glyph_record_id"],
                        "weight": member["weight"],
                        "score": score,
                        "route_family_bit": family_bit,
                        "scorer_kind": kind,
                    }
            # Every candidate fell beyond the compiled bound: the row made no
            # claim. This is a decline, not a compiled absence.
            if family_admissible == 0:
                pass_declined_count += 1
                policy_declined_total += 1
                policy_declined_mask |= family_bit
                if family_bit & STRUCTURAL_FAMILY_MASK:
                    structural_declined = True

        # A row whose mask was legal always declares at least one family.
        if not resolved:
            return refuse("empty_legal_row")

        if (
            best is None
            and structural_declined
            and pass_index == 0
            and effective_mask != TONAL_PAIR_MASK
            and int(row["ramp_bin"]) != 255
            and int(row["density_bin"]) != 255
        ):
            # Role-correct fallback: the structural claim declined and nothing
            # else answered, so the lower-authority tonal pair receives the
            # cell on the next (and final) pass.
            effective_mask = TONAL_PAIR_MASK
            continue

        # ---- Step 3b: the declared-absence decision (FL-4686 PA + Phase 2) --
        # Absence requires that every family of the FINAL arbitration pass
        # either resolved to a compiled-empty address or declined its claim
        # under compiled policy.
        if best is None and declared_absent_count + pass_declined_count == len(resolved):
            return declare_absent(
                resolved,
                declared_absent_count,
                policy_declined_total,
                policy_declined_mask,
            )
        # A MIXED row: a winner (or a scored family) beside a compiled-absent
        # family. Two answers are defensible and neither side chooses, so both
        # refuse. A policy DECLINE is deliberately not part of this refusal.
        if declared_absent_count > 0:
            return refuse("empty_legal_row")
        if best is None:
            return refuse("empty_legal_row")
        break

    # ---- Step 5: the winner must be an admitted shape ----------------------
    if best["glyph_record_id"] not in admitted_glyph_record_ids:
        return refuse("unadmitted_glyph")

    return {
        "accepted": True,
        "outcome": "selected",
        "outcome_code": OUTCOME_CODE["selected"],
        "rejection_reason": "none",
        "rejection_code": REJECTION_CODE["none"],
        "resolved_family_count": len(resolved),
        "declared_absent_family_count": declared_absent_count,
        "policy_abstained_family_count": policy_declined_total,
        "policy_abstained_family_mask": policy_declined_mask,
        "winning_route_family_bit": best["route_family_bit"],
        "winning_glyph_record_id": best["glyph_record_id"],
        "winning_membership_weight_q0_16": best["weight"],
        "winning_score": best["score"],
        "winning_scorer_kind": best["scorer_kind"],
        # Mirror the native u16 clamp: reporting a saturated count is better
        # than reporting a wrapped-around one.
        "considered_candidate_count": min(considered, 65535),
        "resolved_addresses": [
            {
                "route_key": entry["route_key"],
                "route_family_bit": entry["route_family_bit"],
                "lane_index": entry["lane_index"],
                "fact_id": entry["fact_id"],
                "compiled_address_sha256": entry["compiled_address_sha256"],
                "member_count": len(entry["members"]),
                "disposition": entry["disposition"],
            }
            for entry in resolved
        ],
    }


def predict_all(fixture_path: Path) -> Dict[str, Any]:
    """Predict for every row in a fixture and return the whole report."""
    artifact = load_json(GLYPH_FACT_ARTIFACT)
    # FL-4686 PA / FL-4512 Phase 2: the pool is read as a second input because
    # it enumerates the addresses that hold no glyph AND owns the material
    # no-match policies and primitive candidate descriptors.
    pool = load_json(COMPILED_POOL_ADDRESS_SPACE)
    fixture = load_json(fixture_path)
    address_index = build_address_index(artifact, pool)
    admitted = {
        int(record["glyph_record_id"]) for record in artifact["glyph_records"]
    }

    predictions = []
    for row in fixture["rows"]:
        prediction = predict_row(row, address_index, admitted)
        prediction["name"] = row["name"]
        predictions.append(prediction)

    return {
        "schema": "fl4678.exact_cell_prediction.v1",
        # Version 2: mirrors selector result schema v3 (compiled scorers,
        # per-row no-match bounds, degenerate-operand declines, two-pass tonal
        # rearbitration) instead of the retired membership-weight ranking.
        "version": 2,
        "evidence_kind": "offline_prediction_from_compiled_artifact",
        "evidence_scope": (
            "Reads the compiled artifact and a pinned set of scene-fact rows. "
            "Runs no game, opens no window, draws no pixel. Says nothing about "
            "whether normal play emits rows like these."
        ),
        "sources": {
            "compiled_artifact": GLYPH_FACT_ARTIFACT.as_posix(),
            "address_space": COMPILED_POOL_ADDRESS_SPACE.as_posix(),
            "scene_fact_fixture": fixture_path.as_posix(),
        },
        "compiled_address_count": len(address_index),
        # FL-4686 PA. Split out so a reader of the report alone can see that the
        # index covers addresses that hold no glyph, and how many.
        "populated_address_count": sum(
            1
            for entry in address_index.values()
            if entry["disposition"] == "populated"
        ),
        "declared_absent_address_count": sum(
            1
            for entry in address_index.values()
            if entry["disposition"] == "declared_absent"
        ),
        "predictions": predictions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "State, offline, which glyph the native selector would choose for "
            "each pinned scene-fact row."
        )
    )
    parser.add_argument(
        "--fixture",
        default=str(DEFAULT_FIXTURE),
        help="path to a scene-fact fixture, relative to the repository root",
    )
    args = parser.parse_args()

    try:
        report = predict_all(Path(args.fixture))
    except PredictError as exc:
        print(f"FL-4678 exact-cell prediction failed: {exc}", file=sys.stderr)
        return 2

    # Canonical JSON so two runs are byte-identical and a diff is meaningful.
    print(json.dumps(report, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
