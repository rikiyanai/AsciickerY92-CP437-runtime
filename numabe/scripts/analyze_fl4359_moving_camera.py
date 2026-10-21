#!/usr/bin/env python3
"""
FL-4359 MOVING-CAMERA jitter/shimmer analyzer.

Reads JSONL files produced by --fl4359-jitter-capture (the
fl4359_external_movement_driver.gd capture) and splits the visual defect into
its two independent owners, per docs/agent/ADR-0001:

  * CAMERA jitter   -> snap residual delta / sign-flip (gameplay_moving_camera_jitter_bounded)
  * CONTENT shimmer -> visible-signature flips at fixed projected screen cells
    (gameplay_content_shimmer_bounded)

FL-4359 accepts MOVING-camera evidence only: a capture whose camera did not move
is rejected by scenario_moving_camera_continuous, so a steady-pose capture can
never satisfy the jitter/shimmer gates. This supersedes the deleted
scripts/analyze_fl4359_jitter.py (which could pass on a steady pose and is banned
by scripts/git_commit_policy_guard.py); the metric math is the same, the
acceptance contract is motion-gated.

The content classifier names owner_instability as the splat winner-hysteresis
owner and terrain/water churn as the cell-owner product owner -- i.e. exactly the
FL-4359 Tier-B winner-history fix surface (terrain_cell_splat winner bias +
cell_owner_product water deadband).

World-anchor content comparisons require each admitted row to carry the current
b55 Product status, the matching final Product world anchor, and the explicit
`b55_final_product_anchor` comparison-source declaration. Historical b64-only
rows remain readable for diagnostics but are excluded from comparable content
evidence and keep the content gate fail-closed.

Usage:
  python3 scripts/analyze_fl4359_moving_camera.py <capture.jsonl> \\
      --summary-json out.json --fail-on-gate
"""

import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path


ANIMATION_CLASSIFICATIONS = {
    "dynamic_secondary_animation",
    "dynamic_actor_animation",
    "dynamic_water_animation",
    "dynamic_cloud_shadow_animation",
    "foliage_product_payload_instability",
    "flower_product_payload_instability",
}
NON_COMPARABLE_CLASSIFICATIONS = {
    "stable",
    "no_match",
    "different_world_sample",
}
SURFACE_BOUNCE_OWNER_KEYS = (
    "selected_surface_identity_churn_cells",
    "final_product_identity_churn_cells",
    "final_product_cell_churn_cells",
)
FINAL_COLOR_QUANT_STEP = 8
ADMISSION_WORLD = 1
ADMISSION_SCREEN = 2
COMPARISON_ANCHOR_SOURCE_B55 = "b55_final_product_anchor"
FULL_GRID_REQUIRED_DAY_CYCLE_TIME = 0.5
# The b55 selected_surface_status source-epoch bits (30-31) are owned by
# cell_owner_product.glsl and ASCIICompositorEffect.gd.  The producer leaves
# them zero, so this analyzer keeps no mirror of them: decoding a permanently
# zero field would report the "direct" epoch for every row that never carried
# a per-cell provenance measurement at all.
#
# Layer-granular capture-lifecycle evidence published by the host
# (PerspectiveProbePipeline.gd through the ASCIICompositorEffect.gd per-frame
# probe metadata dictionary) and landed in
# frame_trace.perspective_probe_state alongside capture_generation,
# recapture_reason, and captured_face_masks.
#
# Index law, owned by the host and mirrored here for the b61 join:
#   layer = physical_band * PROBE_LAYER_FACE_COUNT + face
# with three physical bands and six cube faces, so exactly 18 layers.  A layer
# content epoch advances only when that exact face layer's pixels were
# rewritten by a successful capture submission; a logical role relabel rotates
# capture_bands without touching any texel.
PROBE_LAYER_BAND_COUNT = 3
PROBE_LAYER_FACE_COUNT = 6
PROBE_LAYER_COUNT = PROBE_LAYER_BAND_COUNT * PROBE_LAYER_FACE_COUNT
# Logical probe roles, the index space of capture_bands and every *_by_probe
# list.  These are roles, not physical bands: capture_bands maps role to band.
PROBE_ROLE_LABELS = {0: "eye", 1: "current", 2: "previous"}
# Explicit absent-evidence bucket.  Absent provenance is never reported as a
# concrete label such as "direct"; it is carried as its own classification.
PROVENANCE_UNMEASURED = "unmeasured"
# Layer-epoch survival across one compared frame pair.
LAYER_EPOCH_SURVIVED = "survived"
LAYER_EPOCH_ADVANCED = "advanced"
# Provenance-transition split for one compared cell across one frame pair.
PROVENANCE_TRANSITION_UNCHANGED = "provenance_unchanged"
PROVENANCE_TRANSITION_BAND_ROTATION = "logical_band_rotation"
PROVENANCE_TRANSITION_LAYER_RECAPTURED = "layer_recaptured"
PROVENANCE_TRANSITION_RETAINED_REPROJECTION = "retained_layer_reprojection"

# Per-cell probe generation owners in the fl4359_jitter_transport.v16 sparse
# schema, most authoritative first.  The legacy pre-v16 names checked ahead of
# this table are absent from v16, which is why the generation lane read
# unmeasured on captures that were in fact carrying generation columns.
#
# Ownership order, and why:
#   1. candidate            - the elected b61 candidate record for this cell;
#                             it is the provenance the frame actually painted.
#   2. prepack              - the packed pre-resolve record; index 0 of
#                             probe_prepack_provenance is the packed probe
#                             generation (the same tuple member
#                             _candidate_source_tuple joins against
#                             nearest_source.packed_probe_generation).
#   3. source_capture       - the elected source-capture record behind b61.
#   4. expected_center      - expected-center capture witness.
#   5. expected_first_match - first exact capture match in the expected set.
#   6. expected_raster      - raster-side source witness.
#   7. expected_neighbor    - neighbour fallback witness, weaker than centre.
#   8. oracle               - direct-ray reference, last: it is a comparison
#                             build, not the provenance the frame painted.
#
# Each entry pairs the generation column with the producer's record-present
# column for that same record.  A mask column counts as present when non-zero.
RETAINED_GENERATION_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("candidate", "probe_candidate_source_generation", "probe_candidate_valid"),
    ("prepack", "probe_prepack_provenance", "probe_prepack_valid"),
    ("source_capture", "probe_source_capture_generation", "probe_source_capture_valid"),
    (
        "expected_center",
        "probe_expected_center_capture_generation",
        "probe_expected_center_capture_valid",
    ),
    (
        "expected_first_match",
        "probe_expected_capture_first_match_generation",
        "probe_expected_capture_exact_match_mask",
    ),
    (
        "expected_raster",
        "probe_expected_raster_source_generation",
        "probe_expected_raster_selection_valid",
    ),
    (
        "expected_neighbor",
        "probe_expected_center_neighbor_generation",
        "probe_expected_center_neighbor_valid",
    ),
    ("oracle", "probe_source_oracle_generation", "probe_source_oracle_query_valid"),
)
# Index 0 of probe_prepack_provenance is the packed probe generation.
RETAINED_GENERATION_PREPACK_COLUMN = "probe_prepack_provenance"
RETAINED_GENERATION_PREPACK_INDEX = 0
# Producer "no record" sentinels.  0xFFFFFFFF is the u32 absent marker and
# 0x3FFFFFFF is the packed-candidate absent marker; neither is a generation.
RETAINED_GENERATION_SENTINELS = frozenset({0xFFFFFFFF, 0x3FFFFFFF})

# This mirrors the immutable b55 Product status ABI.  A non-zero status is not
# enough provenance: stale or malformed historical rows must not enter the
# world-anchor content comparison merely because they carry an anchor-shaped
# value.
FINAL_PRODUCT_STATUS_VALID = 1 << 0
FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT = 1 << 1
FINAL_PRODUCT_STATUS_ABI_MASK = 0xFF000000
FINAL_PRODUCT_STATUS_ABI_V2 = 2 << 24
FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK = 0x0000003C
# FL-4740 presentation cell mask, bits 6..19. Bit 6 says a cell-wide mask was
# elected, bit 7 says that elected mask is foliage, bits 8..19 carry the elected
# glyph. These mirror cell_owner_product.glsl and ascii_compositor.glsl. Without
# them every migrated row fails final_product_status_is_valid here and silently
# drops out of the admitted set, so comparability and churn figures would be
# computed on a filtered subset with no indication that anything was excluded.
# FL-4848: bits 6..23 are reserved; the elected cell presentation lives only
# in the NativePresentationResult record, never in the b55 status word.
FINAL_PRODUCT_STATUS_KNOWN_MASK = (
    FINAL_PRODUCT_STATUS_ABI_MASK
    | FINAL_PRODUCT_STATUS_VALID
    | FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT
    | FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK
)
FINAL_CELL_MAGIC = 0xF14231
SAME_FRAME_ORACLE_SCHEMA = "fl4359.same_frame_direct_ray.v2"
SAME_FRAME_ORACLE_SAMPLING_POLICY = (
    "world_anchor_hash_mod_or_screen_index_mod"
)
FULL_GRID_TEMPORAL_ORACLE_PROFILE = "full_grid_temporal_oracle_v1"
FULL_GRID_TEMPORAL_ORACLE_SAMPLING_POLICY = "exhaustive_all_subcells"

# =====================================================================
# FL-4359 ORACLE REFERENCE LAW — strict identity vs surface continuity
# =====================================================================
# HISTORY THIS EXISTS TO STOP REPEATING. On 2026-08-23 commit 691e654fd removed
# the retained texel splat as the visible perspective geometry owner. The
# disagreement it acted on was fl4359_oracle_write()'s EXACT-PRIMITIVE-IDENTITY
# test (visible_surface_builder.glsl, FL4359_ORACLE_STATE_SOURCE_IDENTITY_AGREES).
# That test is correct for a current-ray owner, where the reference and the
# answer are the same query and primitive_id equality is tautological. It is
# over-strict BY CONSTRUCTION for two published laws that this repository now
# ships or is preparing to ship:
#
#   (a) the FL-4359 world-lattice WHERE law
#       (fl4359_apply_persistent_terrain_where): visibility stays ray-exact and
#       only the semantic paint ADDRESS is re-anchored, so position, depth,
#       normal, owner_kind and object_id are preserved and primitive_id
#       (and, when the elected representative cell carries a different material
#       texel, material) changes on purpose;
#   (b) the retained-owner substitution guarded by
#       fl4359_retained_surface_continuous(): a retained splat reconstructs a
#       surface from a cubemap texel captured from a DIFFERENT origin, so it
#       lands on the correct continuous surface but on a NEIGHBOURING triangle.
#
# Under exact-identity grading a perfectly working renderer therefore scores
# tens of thousands of "geometry errors" per capture, which is what made the
# whole Aug-23 lane inadmissible as proof.
#
# WHAT THIS BLOCK ADDS AND WHAT IT MUST NEVER DO. The strict counters are the
# TOLERANCE-ZERO CONTROL and stay computed and reported unchanged. The
# continuity counters below are a PARALLEL population, never a replacement, and
# they only become blocking when the operator passes
# `--oracle-reference-law continuity_v1`. The default law stays strict so no
# existing receipt changes meaning without an explicit flag.
ORACLE_REFERENCE_LAW_STRICT = "strict"
ORACLE_REFERENCE_LAW_CONTINUITY = "continuity_v1"
ORACLE_REFERENCE_LAWS = (
    ORACLE_REFERENCE_LAW_STRICT,
    ORACLE_REFERENCE_LAW_CONTINUITY,
)
# SurfaceHit owner_kind values, visible_surface_builder.glsl:92-96.
ORACLE_OWNER_ABSENT = 0
ORACLE_OWNER_STATIC_MESH = 1
ORACLE_OWNER_TERRAIN = 2
ORACLE_OWNER_WATER = 3
ORACLE_OWNER_DYNAMIC_MESH = 4
# b64 status source-epoch field, visible_surface_builder.glsl:130-135. The
# runtime stamps a retained substitution with the capture epoch that authored
# it, which is the ONLY published per-row discriminator separating a
# retained-authored answer from a current-ray answer.
ORACLE_STATUS_SOURCE_EPOCH_SHIFT = 30
ORACLE_STATUS_SOURCE_EPOCH_NAMES = {
    0: "direct_current_ray",
    1: "retained_eye",
    2: "retained_current",
    3: "retained_previous",
}
# Continuity tolerances, copied verbatim from
# visible_surface_builder.glsl:4423-4442. These are NOT independent analyzer
# choices: an analyzer that graded with a different tolerance than the shader
# admits with would be measuring a renderer that does not exist.
ORACLE_CONTINUITY_NORMAL_MIN_COS = 0.90630779       # cos(25 degrees)
ORACLE_CONTINUITY_PLANE_TOLERANCE_SPANS = 0.5       # fraction of one ANSI cell
ORACLE_CONTINUITY_PLANE_TOLERANCE_FLOOR = 1.0e-3    # absolute near-field floor
# Reference-collapse guard (audit blueprint 7.4 control 2). If nearly every row
# in an arm is retained-authored AND the continuity law finds no defect at all,
# the oracle is suspected of grading its own output and the analysis marks
# itself invalid instead of reporting a green.
ORACLE_REFERENCE_COLLAPSE_RETAINED_FRACTION = 0.99
# This analyzer owns closure-grade interpretation for the current sparse-capture
# ABI. Historical schemas stay readable below as diagnostics, but they must not
# silently enter the Product-anchor comparison used by a current proof.
CURRENT_SPARSE_CAPTURE_SCHEMA = "fl4359_sparse_surface_capture.v17"
HISTORICAL_SPARSE_CAPTURE_SCHEMAS = ("fl4359_sparse_surface_capture.v16",)
SUPPORTED_SPARSE_CAPTURE_SCHEMAS = (
    CURRENT_SPARSE_CAPTURE_SCHEMA, *HISTORICAL_SPARSE_CAPTURE_SCHEMAS
)
PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES = (
    "corner_heights",
    "continuous_normal_cast",
    "effective_sun_shade",
    "matlib_fg_lambert",
    "matlib_bg_discrete_shade",
    "ambient_fg_cloud",
    "ambient_bg_boundary",
    "ramp_fg_paint_band",
    "ramp_bg_foliage_band",
    "resolved_fg_heightfield_shadow",
    "resolved_bg_role",
    "palette_fg_glyph",
    "palette_bg_validity",
    "final_compiler_fg_glyph",
    "final_compiler_bg_validity",
)
PRODUCT_ADMISSION_REQUIRED_FIELDS = (
    "final_product_authored_source_key",
    "final_product_authored_receiver_cell",
    "final_product_authored_material_id",
    "final_product_authored_role_family",
    "final_product_authored_role_family_valid",
    "final_product_classifier_family",
    "final_product_dirt_mountain_role",
    "final_product_elevation_class",
    "final_product_matlib_column_id",
    "final_product_matlib_profile",
    "final_product_matlib_profile_valid",
    "final_product_matlib_profile_validity_bits",
    "final_product_admission_validity_bits",
    "final_product_admission_transport_valid",
)
# Bits 0,1,2,4,5,6,7,8,9,10 are the exact source/classifier/profile facts
# available in the current Product ABI. Bit 3 is deliberately absent because
# no authoritative authored role/family field exists in the runtime input.
PRODUCT_ADMISSION_EXPECTED_VALIDITY_MASK = sum(
    1 << bit for bit in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10)
)
PRODUCT_ADMISSION_PROFILE_EXPECTED_VALIDITY_MASK = 0x1F
CURRENT_JITTER_TRANSPORT_SCHEMA = "fl4359_jitter_transport.v16"
TERMINAL_OCCURRENCE_FIELDS = (
    "final_paint_terminal_mask_ink_count",
    "final_paint_terminal_mask_sum_x",
    "final_paint_terminal_mask_sum_y",
    "final_paint_terminal_mask_min_x",
    "final_paint_terminal_mask_min_y",
    "final_paint_terminal_mask_max_x",
    "final_paint_terminal_mask_max_y",
    "final_paint_terminal_mask_digest",
    "final_paint_terminal_occurrence_valid",
)

# b55 Product owns this stable enum. It is not the b64/VSB enum: Product word
# 3 is dynamic mesh, word 4 is actor sprite, and word 5 is water. The numeric
# field is the ABI authority. The recorder string remains an audited witness so
# an older mislabeled capture can be replayed without silently trusting it.
PRODUCT_OWNER_NAMES = {
    1: "terrain",
    2: "static_mesh",
    3: "dynamic_mesh",
    4: "actor_sprite",
    5: "water",
    6: "sky",
    7: "item_sprite",
    8: "projectile",
    9: "dash_trail",
    10: "vapor",
    11: "beam",
    12: "reticle",
    13: "attack_swoosh",
    14: "local_light",
}
JITTER_TRANSPORT_F64_BITS_KEY = "__fl4359_f64_bits_le__"


def quantized_rgb(value) -> tuple:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return ()
    return tuple(int(max(0, min(255, int(value[i])))) // FINAL_COLOR_QUANT_STEP for i in range(3))


def quantized_final_ink_coverage(row: dict) -> int:
    total = int(row.get("final_ink_pixels") or 0)
    if total <= 0:
        return -1
    foreground = int(row.get("final_ink_fg") or 0)
    return int(round(float(foreground) * 1000.0 / float(total)))


def quantized_final_ink_mix(row: dict) -> tuple[int, int, int, int]:
    total = int(row.get("final_ink_pixels") or 0)
    if total <= 0:
        return (-1, -1, -1, -1)
    foreground = int(row.get("final_ink_fg") or 0)
    background = int(row.get("final_ink_bg") or 0)
    other = max(0, total - foreground - background)
    return (
        int(round(float(foreground) * 1000.0 / float(total))),
        int(round(float(background) * 1000.0 / float(total))),
        int(round(float(other) * 1000.0 / float(total))),
        total,
    )


def _decode_transport_value(value):
    if isinstance(value, list):
        return [_decode_transport_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {JITTER_TRANSPORT_F64_BITS_KEY}:
        words = value[JITTER_TRANSPORT_F64_BITS_KEY]
        if (
            not isinstance(words, list)
            or len(words) != 2
            or not all(isinstance(word, int) and 0 <= word <= 0xFFFFFFFF for word in words)
        ):
            raise ValueError("invalid compact float64 bit payload")
        return struct.unpack("<d", struct.pack("<II", words[0], words[1]))[0]
    return {key: _decode_transport_value(item) for key, item in value.items()}


def normalize_product_owner_name(row: dict) -> None:
    """Use b55's numeric owner as authority and audit the derived label."""
    owner = row.get("final_product_owner")
    if not isinstance(owner, int) or isinstance(owner, bool):
        return
    canonical = PRODUCT_OWNER_NAMES.get(owner, "unknown")
    transported = row.get("final_product_owner_name")
    row["final_product_owner_name_transport"] = transported
    row["final_product_owner_name_transport_valid"] = transported == canonical
    row["final_product_owner_name"] = canonical


def load_rows(path: str) -> list[dict]:
    rows = []
    frame_traces: dict[int, dict] = {}
    transport_fields: tuple[str, ...] | None = None
    with open(path, "r") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, list):
                if transport_fields is None:
                    raise ValueError(
                        f"compact cell row before schema header at line {line_number}"
                    )
                if len(row) != len(transport_fields):
                    raise ValueError(
                        "compact cell row length differs from schema at "
                        f"line {line_number}: {len(row)} != {len(transport_fields)}"
                    )
                row = dict(
                    zip(
                        transport_fields,
                        (_decode_transport_value(value) for value in row),
                        strict=True,
                    )
                )
            if not isinstance(row, dict):
                continue
            if row.get("row_type") == "fl4359_jitter_schema":
                if transport_fields is not None:
                    raise ValueError(f"duplicate compact schema header at line {line_number}")
                if row.get("transport_schema") != CURRENT_JITTER_TRANSPORT_SCHEMA:
                    raise ValueError(f"unsupported compact transport schema at line {line_number}")
                raw_fields = row.get("fields")
                if (
                    not isinstance(raw_fields, list)
                    or not raw_fields
                    or not all(isinstance(field, str) and field for field in raw_fields)
                    or len(set(raw_fields)) != len(raw_fields)
                ):
                    raise ValueError(f"invalid compact transport fields at line {line_number}")
                transport_fields = tuple(raw_fields)
                continue
            if row.get("row_type") == "fl4359_jitter_frame":
                trace = row.get("frame_trace", {})
                if isinstance(trace, dict):
                    frame_traces[int(row.get("frame_id", 0))] = trace
                continue
            row_type = row.get("row_type")
            # Row-only captures predate the explicit cell tag. Preserve them;
            # only skip a row that names a different schema.
            if row_type not in (None, "", "fl4359_jitter_cell"):
                continue
            normalize_product_owner_name(row)
            if not isinstance(row.get("frame_trace"), dict):
                trace = frame_traces.get(int(row.get("frame_id", 0)))
                if trace is not None:
                    # Shared reference in memory; the serialized capture stores
                    # the frame dictionary exactly once.
                    row["frame_trace"] = trace
            rows.append(row)
    return rows


def group_by_frame(rows: list[dict]) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for r in rows:
        fid = r.get("frame_id", 0)
        groups.setdefault(fid, []).append(r)
    for frame_rows in groups.values():
        # Atomic GPU admission has no ordering guarantee.  The stored sample
        # index is the deterministic join key; never let callback order alter
        # a frame's metrics.
        frame_rows.sort(key=lambda row: (int(row.get("sample_index", 0)), int(row.get("frame_index", 0))))
    return groups


def admitted(row: dict, bit: int) -> bool:
    """Return whether a row belongs to one explicit sparse admission lane.

    Captures made before the admission field existed remain readable as a
    compatibility diagnostic.  New recorder rows always carry admission_bits,
    so absent records cannot enter the world-anchor or fixed-screen gates.
    """
    if "admission_bits" not in row:
        return True
    return bool(int(row.get("admission_bits", 0)) & bit)


def product_key(row: dict) -> tuple:
    return (
        row.get("selected_owner"),
        final_product_identity_key(row),
        row.get("material_role"),
        row.get("actor_id"),
        row.get("terrain_product_detail"),
        row.get("cloud_shadow_phase"),
        foliage_product_key(row),
    )


def product_key_without_cloud_shadow(row: dict) -> tuple:
    return (
        row.get("selected_owner"),
        final_product_identity_key(row),
        row.get("material_role"),
        row.get("actor_id"),
        row.get("terrain_product_detail"),
        foliage_product_key(row),
    )


def foliage_product_key(row: dict) -> tuple:
    if not row.get("foliage_product_present"):
        return (False,)
    return (
        True,
        row.get("foliage_role"),
        row.get("foliage_shape"),
        row.get("foliage_variant"),
        row.get("foliage_density"),
        row.get("foliage_source"),
        row.get("foliage_glyph"),
        row.get("foliage_clump_id"),
        row.get("foliage_coverage"),
        row.get("foliage_effective_y"),
        row.get("foliage_local_u"),
        row.get("foliage_local_v"),
    )


def final_product_identity_key(row: dict) -> tuple:
    """Identity emitted by the live b55 Product record.

    The source key value zero is valid and therefore tested by key presence,
    not truthiness. Matcell is interpreted as a terrain stable cell only for a
    terrain Product; non-terrain records retain the raw value without deriving
    a terrain identity from it.
    """
    if any(
        key in row
        for key in (
            "final_product_owner",
            "final_product_source_key",
            "final_product_matcell_packed",
        )
    ):
        owner = row.get("final_product_owner")
        source_key = row.get("final_product_source_key")
        # The b55 owner, not the selected-surface provenance, decides whether
        # matcell has terrain stable-cell meaning. Product's stable enum is
        # OWNER_TERRAIN=1 and OWNER_STATIC_MESH=2; b64 uses a different enum
        # with those two values swapped. A non-terrain Product must never be
        # decoded as terrain merely because b64 selected terrain.
        matcell = row.get("final_product_matcell_packed") if owner == 1 else None
        return (owner, source_key, matcell)
    return (row.get("selected_product_id"), row.get("terrain_cell"))


def final_cell_signature(row: dict) -> tuple:
    return (
        row.get("glyph_id"),
        quantized_rgb(row.get("fg_rgb")),
        quantized_rgb(row.get("bg_rgb")),
        quantized_final_ink_mix(row),
        row.get("selected_owner"),
        final_product_identity_key(row),
    )


def screen_cell_key(row: dict) -> tuple:
    cell = row.get("screen_cell")
    if isinstance(cell, dict):
        return int(cell.get("x", 0)), int(cell.get("y", 0))
    if isinstance(cell, (list, tuple)) and len(cell) >= 2:
        return int(cell[0]), int(cell[1])
    return ()


def final_cell_signature_fields(row: dict, include_ink: bool = True) -> dict:
    fields = {
        "glyph_id": row.get("glyph_id"),
        "fg_rgb_quant": quantized_rgb(row.get("fg_rgb")),
        "bg_rgb_quant": quantized_rgb(row.get("bg_rgb")),
        "selected_owner": row.get("selected_owner"),
        "final_product_identity": final_product_identity_key(row),
    }
    if include_ink:
        fields["final_ink_mix_q"] = quantized_final_ink_mix(row)
        fields["final_ink_coverage_q"] = quantized_final_ink_coverage(row)
    return fields


def world_product_signature_fields(row: dict) -> dict:
    """Return the visible Product payload owned by one b55 subcell.

    The b43 fg/bg/glyph header is written once per ANSI cell by its top-left
    native pixel. The whole-cell glyph mask has its own elected owner and is
    repeated beside all four b55 rows. Joining that mask to a quarter's b55
    anchor fabricates motion-compensated churn. The cell-wide mask is graded
    separately against the stable anchor encoded by its published winner key.
    """
    if row.get("capture_schema") == CURRENT_SPARSE_CAPTURE_SCHEMA:
        return {
            "final_product_glyph": row.get("final_product_glyph"),
            "final_product_fg_rgb_quant": quantized_rgb(row.get("final_product_fg_rgb")),
            "final_product_bg_rgb_quant": quantized_rgb(row.get("final_product_bg_rgb")),
            "final_product_owner_name": row.get("final_product_owner_name"),
            "final_product_identity": final_product_identity_key(row),
        }
    # Historical unit fixtures and captures remain readable as diagnostics.
    # They cannot pass current-product provenance, so this fallback never makes
    # an old artifact closure-grade.
    return {
        "glyph_id": row.get("glyph_id"),
        "fg_rgb_quant": quantized_rgb(row.get("fg_rgb")),
        "bg_rgb_quant": quantized_rgb(row.get("bg_rgb")),
        "selected_owner": row.get("selected_owner"),
        "final_product_identity": final_product_identity_key(row),
    }


def has_current_product_capture(row: dict) -> bool:
    return row.get("capture_schema") == CURRENT_SPARSE_CAPTURE_SCHEMA


def cell_mask_winner_witness(row: dict) -> dict:
    """Return Product's cell-wide glyph election without screen-slot identity.

    The winning slot is presentation-relative and can change as a stable world
    anchor crosses an ANSI-cell boundary.  The Product key, glyph, and priority
    are the semantic election result.  Keep the slot as a separate diagnostic
    so it cannot silently become part of the stability key.
    """
    identity = row.get("final_product_exact_identity")
    if not isinstance(identity, dict):
        return {"available": False, "cell_consistent": False}
    key = identity.get("cell_mask_winner_key")
    glyph = identity.get("cell_mask_winner_glyph")
    priority = identity.get("cell_mask_winner_priority")
    slot = identity.get("cell_mask_winner_slot")
    source_kind = identity.get("cell_mask_winner_source_kind")
    source_semantic_key = identity.get("cell_mask_winner_source_semantic_key")
    source_anchor_bits = identity.get("cell_mask_winner_source_anchor_bits")
    cell_consistent = identity.get("cell_mask_winner_cell_consistent") is True
    available = bool(
        identity.get("cell_mask_winner_available") is True
        and cell_consistent
        and isinstance(key, dict)
        and isinstance(glyph, int)
        and not isinstance(glyph, bool)
        and isinstance(priority, int)
        and not isinstance(priority, bool)
        and isinstance(slot, int)
        and not isinstance(slot, bool)
        and 0 <= slot <= 3
        and source_kind in {
            "quarter_slot", "center_terrain", "center_sky", "center_surface"
        }
        and source_semantic_key == key
        and isinstance(source_anchor_bits, list)
        and len(source_anchor_bits) == 3
        and source_anchor_bits == key.get("key_d", [])[:3]
    )
    return {
        "available": available,
        "cell_consistent": cell_consistent,
        "key": key if available else None,
        "key_token": (
            json.dumps(key, sort_keys=True, separators=(",", ":"))
            if available else None
        ),
        "glyph": int(glyph) if available else None,
        "priority": int(priority) if available else None,
        "slot": int(slot) if available else None,
        "source_kind": source_kind if available else None,
        "source_semantic_key": source_semantic_key if available else None,
        "source_anchor_bits": source_anchor_bits if available else None,
    }


def b43_elected_identity_witness(row: dict) -> dict:
    """Decode the identity captured at the b43 mask-reduction owner.

    This is intentionally separate from Product's repeated b60 sidecar. The
    sparse row must carry the b43 tail written after the actual native mask
    reduction, and the key is used only as a categorical bridge to a later
    terminal occurrence. It never supplies the expected projected address.
    """
    if row.get("final_paint_elected_identity_available") is not True:
        return {"available": False, "reason": "b43_elected_identity_missing"}
    keys = []
    for name in ("a", "b", "c", "d"):
        value = row.get(f"final_paint_elected_identity_key_{name}")
        if not isinstance(value, list) or len(value) != 4:
            return {"available": False, "reason": "b43_elected_identity_key_invalid"}
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
            return {"available": False, "reason": "b43_elected_identity_key_non_integer"}
        if all(item == 0xFFFFFFFF for item in value):
            return {"available": False, "reason": "b43_elected_identity_key_sentinel"}
        keys.append(tuple(value))
    validity = row.get("final_paint_elected_identity_validity")
    glyph = row.get("final_paint_elected_identity_glyph")
    priority = row.get("final_paint_elected_identity_priority")
    slot = row.get("final_paint_elected_identity_slot")
    if not all(isinstance(value, int) and not isinstance(value, bool)
               for value in (validity, glyph, priority, slot)):
        return {"available": False, "reason": "b43_elected_identity_scalar_invalid"}
    # FL-4848: the validity word is the published NativePresentationResult
    # status (bit 0 valid, bits 8..15 typed refusal, bits 16..23 authority ABI
    # 5) with the capture-only glyph source kind in bits 24..25. The carrier
    # ordinal is 0..3 for a quarter and 4 for the exact centre.
    if (validity & 0x1) == 0 or ((validity >> 16) & 0xFF) != 5 or not 0 <= slot < 5:
        return {"available": False, "reason": "b43_elected_identity_validity_invalid"}
    if glyph != row.get("final_paint_glyph"):
        return {"available": False, "reason": "b43_elected_identity_glyph_mismatch"}
    source_kind_code = (validity >> 24) & 0x3
    if source_kind_code not in {0, 1, 2, 3}:
        return {"available": False, "reason": "b43_elected_identity_source_kind_invalid"}
    name_by_code = {
        0: "quarter_slot",
        1: "center_terrain",
        2: "center_sky",
        3: "center_surface",
    }
    return {
        "available": True,
        "key": {name: list(value) for name, value in zip(("key_a", "key_b", "key_c", "key_d"), keys)},
        "glyph": glyph,
        "priority": priority,
        "slot": slot,
        "source_kind": name_by_code[source_kind_code],
        "source_kind_code": source_kind_code,
    }


def _float32_from_u32(word) -> float | None:
    """Decode one shader floatBitsToUint lane without accepting coercions."""
    if not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= 0xFFFFFFFF:
        return None
    value = struct.unpack("<f", struct.pack("<I", word))[0]
    return value if math.isfinite(value) else None


def _u32_from_float32(value: object) -> int | None:
    """Return the exact IEEE-754 word transported by one shader float lane."""
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        return None
    try:
        return struct.unpack("<I", struct.pack("<f", float(value)))[0]
    except (OverflowError, struct.error):
        return None


def _cell_mask_key_anchor(winner: dict) -> dict | None:
    """Decode an elected mask key's explicitly owned anchor lanes."""
    key = winner.get("key") if winner.get("available") else None
    key_d = key.get("key_d") if isinstance(key, dict) else None
    if not isinstance(key_d, list) or len(key_d) < 3:
        return None
    decoded = [_float32_from_u32(key_d[index]) for index in range(3)]
    if any(value is None for value in decoded):
        return None
    return {"x": decoded[0], "y": decoded[1], "z": decoded[2]}


def cell_mask_owner_anchor_result(
    cell_rows: list[dict], winner: dict, source_owner: str
) -> tuple[dict | None, str | None]:
    """Return the elected mask source's semantic anchor and exact failure.

    A quarter-slot winner owns the b55 Product anchor on its elected subcell.
    Its mask key can retain an earlier VSB anchor; analytic-water backstop rows
    demonstrate that this raw anchor may be shared across many semantic water
    cells.  Center terrain, center surfaces, and late effects are fifth-source
    candidates with no elected b55 quarter, so their key_d lanes remain their
    authoritative world anchor.  Center SKY instead carries a ray direction and
    is excluded from world-anchor comparison before this helper runs.
    """
    if winner.get("source_kind") in {"center_terrain", "center_surface"} \
            or source_owner == "late_effect":
        anchor = _cell_mask_key_anchor(winner)
        return (anchor, None) if anchor is not None else (None, "winner_key_anchor_invalid")
    if winner.get("source_kind") != "quarter_slot":
        return None, "winner_source_kind_invalid"
    elected_slot = winner.get("slot")
    elected_rows = [
        row for row in cell_rows if row.get("subcell_slot") == elected_slot
    ]
    if len(elected_rows) != 1:
        return None, "winner_elected_slot_row_missing_or_duplicate"
    elected_row = elected_rows[0]
    product_owner = elected_row.get("final_product_owner_name")
    if product_owner != source_owner:
        return (
            None,
            "winner_elected_product_owner_mismatch:"
            f"{source_owner}->{product_owner if product_owner is not None else 'missing'}",
        )
    anchor = _world_anchor_value(elected_row.get("final_product_world_anchor"))
    if anchor is None:
        return None, "winner_elected_product_anchor_invalid"
    return {"x": anchor[0], "y": anchor[1], "z": anchor[2]}, None


def cell_mask_owner_anchor(
    cell_rows: list[dict], winner: dict, source_owner: str
) -> dict | None:
    """Return the semantic anchor without discarding the owned error taxonomy."""
    anchor, _error = cell_mask_owner_anchor_result(cell_rows, winner, source_owner)
    return anchor


def cell_mask_winner_source_owner(winner: dict) -> str | None:
    """Decode the Product owner domain from the elected semantic key.

    Priority-zero keys are authored effect identities rather than surface-owner
    tuples. Ordinary keys keep the Product owner enum in key_a.x, including
    terrain's world-cell key. Sky has a direction-domain identity and therefore
    must never be matched through its placeholder world anchor.
    """
    if not winner.get("available"):
        return None
    if winner.get("priority") == 0:
        return "late_effect"
    key = winner.get("key")
    key_a = key.get("key_a") if isinstance(key, dict) else None
    if not isinstance(key_a, list) or not key_a:
        return None
    owner_names = {
        1: "terrain",
        2: "static_mesh",
        3: "dynamic_mesh",
        4: "actor_sprite",
        5: "water",
        6: "sky",
    }
    try:
        owner_word = int(key_a[0])
    except (TypeError, ValueError):
        return None
    return owner_names.get(owner_word)


def _cell_mask_observations(
    rows: list[dict],
) -> tuple[list[dict], Counter, Counter]:
    """Collapse four repeated sparse rows into one cell-wide mask record."""
    grouped: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        cell = screen_cell_key(row)
        if cell:
            grouped.setdefault(cell, []).append(row)
    observations: list[dict] = []
    invalid: Counter = Counter()
    excluded: Counter = Counter()
    for cell, cell_rows in sorted(grouped.items()):
        slots = {int(row.get("subcell_slot", -1)) for row in cell_rows}
        if len(cell_rows) != 4 or slots != {0, 1, 2, 3}:
            invalid["incomplete_cell"] += 1
            continue
        witnesses = [cell_mask_winner_witness(row) for row in cell_rows]
        if not all(witness.get("available") for witness in witnesses):
            invalid["winner_unavailable"] += 1
            continue
        first = witnesses[0]
        if any(
            witness["key_token"] != first["key_token"]
            or witness["glyph"] != first["glyph"]
            or witness["priority"] != first["priority"]
            or witness["source_kind"] != first["source_kind"]
            for witness in witnesses[1:]
        ):
            invalid["winner_not_cell_consistent"] += 1
            continue
        source_owner = cell_mask_winner_source_owner(first)
        if source_owner is None:
            invalid["winner_source_owner_unknown"] += 1
            continue
        if first["source_kind"] == "center_terrain" and source_owner != "terrain":
            invalid["center_source_owner_not_terrain"] += 1
            continue
        if first["source_kind"] == "center_sky" and source_owner != "sky":
            invalid["center_source_owner_not_sky"] += 1
            continue
        if first["source_kind"] == "center_surface" and source_owner not in {
            "static_mesh", "dynamic_mesh"
        }:
            invalid["center_source_owner_not_nonterrain_surface"] += 1
            continue
        paint_glyphs = {row.get("final_paint_slot_glyph") for row in cell_rows}
        if paint_glyphs != {first["glyph"]}:
            invalid["paint_glyph_disagrees_with_winner"] += 1
            continue
        if source_owner == "sky":
            # Ordinary sky key_d is a null placeholder, not a world anchor.
            # Matching it would greedily pair unrelated screen cells and turn
            # view-dependent sky morphology into fabricated temporal churn.
            excluded["sky_direction_domain"] += 1
            continue
        anchor, anchor_error = cell_mask_owner_anchor_result(
            cell_rows, first, source_owner
        )
        if anchor is None:
            invalid[anchor_error or "winner_anchor_invalid"] += 1
            continue
        observations.append(
            {
                "screen_cell": cell,
                "anchor": anchor,
                "key": first["key"],
                "key_token": first["key_token"],
                "glyph": first["glyph"],
                "priority": first["priority"],
                "slot": first["slot"],
                "source_kind": first["source_kind"],
                "source_owner": source_owner,
            }
        )
    return observations, invalid, excluded


def summarize_cell_mask_owner_stability(
    frame_a: list[dict],
    frame_b: list[dict],
    comparable_threshold: float = 0.10,
    details_limit: int = 50,
) -> dict:
    """Compare one elected mask per ANSI cell by the mask owner's own anchor."""
    observations_a, invalid_a, excluded_a = _cell_mask_observations(frame_a)
    observations_b, invalid_b, excluded_b = _cell_mask_observations(frame_b)
    bucket_size = comparable_threshold
    b_index: dict[tuple[int, int, int], list[tuple[int, dict]]] = {}
    for bi, observation in enumerate(observations_b):
        anchor = observation["anchor"]
        bucket = (
            int(math.floor(anchor["x"] / bucket_size)),
            int(math.floor(anchor["y"] / bucket_size)),
            int(math.floor(anchor["z"] / bucket_size)),
        )
        b_index.setdefault(bucket, []).append((bi, observation))
    edges: list[tuple[float, float, int, int]] = []
    for ai, a in enumerate(observations_a):
        anchor = a["anchor"]
        bucket = (
            int(math.floor(anchor["x"] / bucket_size)),
            int(math.floor(anchor["y"] / bucket_size)),
            int(math.floor(anchor["z"] / bucket_size)),
        )
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for bi, b in b_index.get(
                        (bucket[0] + dx, bucket[1] + dy, bucket[2] + dz), []
                    ):
                        anchor_distance = distance3(anchor, b["anchor"])
                        if anchor_distance <= comparable_threshold:
                            ax, ay = a["screen_cell"]
                            bx, by = b["screen_cell"]
                            screen_distance = math.hypot(float(ax - bx), float(ay - by))
                            edges.append((anchor_distance, screen_distance, ai, bi))
    edges.sort()
    matches: dict[int, tuple[int, float]] = {}
    used_b: set[int] = set()
    for anchor_distance, _screen_distance, ai, bi in edges:
        if ai in matches or bi in used_b:
            continue
        matches[ai] = (bi, anchor_distance)
        used_b.add(bi)

    counts: Counter = Counter()
    examples: list[dict] = []
    for ai, (bi, anchor_distance) in matches.items():
        a = observations_a[ai]
        b = observations_b[bi]
        counts["comparable_cells"] += 1
        key_changed = a["key_token"] != b["key_token"]
        glyph_changed = a["glyph"] != b["glyph"]
        priority_changed = a["priority"] != b["priority"]
        source_kind_changed = a["source_kind"] != b["source_kind"]
        source_kind_transition = f"{a['source_kind']}->{b['source_kind']}"
        source_owner_transition = f"{a['source_owner']}->{b['source_owner']}"
        counts[f"source_kind_transition:{source_kind_transition}"] += 1
        counts[f"source_owner_transition:{source_owner_transition}"] += 1
        if key_changed:
            counts["winner_key_changed_cells"] += 1
        if glyph_changed:
            counts["winner_glyph_changed_cells"] += 1
            counts[
                f"glyph_changed_source_kind_transition:{source_kind_transition}"
            ] += 1
            counts[
                f"glyph_changed_source_owner_transition:{source_owner_transition}"
            ] += 1
            counts[
                "glyph_changed_source_kind_priority_transition:"
                f"{a['source_kind']}:{a['priority']}"
                f"->{b['source_kind']}:{b['priority']}"
            ] += 1
            counts[
                "glyph_changed_with_source_kind_changed_cells"
                if source_kind_changed
                else "glyph_changed_with_source_kind_stable_cells"
            ] += 1
            counts[
                "glyph_changed_with_source_key_changed_cells"
                if key_changed
                else "glyph_changed_with_source_key_stable_cells"
            ] += 1
            counts[
                f"glyph_changed_source_kind_key_state:{source_kind_transition}:"
                f"{'changed' if key_changed else 'stable'}"
            ] += 1
        else:
            counts[
                f"glyph_stable_source_kind_transition:{source_kind_transition}"
            ] += 1
            counts[
                f"glyph_stable_source_owner_transition:{source_owner_transition}"
            ] += 1
        if priority_changed:
            counts["winner_priority_changed_cells"] += 1
        if source_kind_changed:
            counts["winner_source_kind_changed_cells"] += 1
        if key_changed or priority_changed or source_kind_changed:
            counts["semantic_owner_changed_cells"] += 1
        # The acceptance surface is the published whole-cell mask. A hidden
        # semantic-key or priority handoff with the same glyph remains an owner
        # diagnostic; it is not a visible mask change and cannot be counted as
        # one merely because exact float anchor bits differ inside key_d.
        if glyph_changed:
            counts["unstable_cells"] += 1
            if len(examples) < details_limit:
                examples.append(
                    {
                        "cell_a": list(a["screen_cell"]),
                        "cell_b": list(b["screen_cell"]),
                        "anchor": a["anchor"],
                        "anchor_distance": anchor_distance,
                        "key_changed": key_changed,
                        "glyph_a": a["glyph"],
                        "glyph_b": b["glyph"],
                        "priority_a": a["priority"],
                        "priority_b": b["priority"],
                        "source_kind_a": a["source_kind"],
                        "source_kind_b": b["source_kind"],
                        "source_owner_a": a["source_owner"],
                        "source_owner_b": b["source_owner"],
                    }
                )
        else:
            counts["stable_cells"] += 1
    comparable = int(counts["comparable_cells"])
    for field in (
        "comparable_cells",
        "stable_cells",
        "unstable_cells",
        "winner_key_changed_cells",
        "winner_glyph_changed_cells",
        "winner_priority_changed_cells",
        "winner_source_kind_changed_cells",
        "glyph_changed_with_source_kind_changed_cells",
        "glyph_changed_with_source_kind_stable_cells",
        "glyph_changed_with_source_key_changed_cells",
        "glyph_changed_with_source_key_stable_cells",
        "semantic_owner_changed_cells",
    ):
        counts.setdefault(field, 0)
    counts["eligible_cells_a"] = len(observations_a)
    counts["eligible_cells_b"] = len(observations_b)
    counts["unmatched_cells_a"] = len(observations_a) - len(matches)
    counts["new_cells_b"] = len(observations_b) - len(used_b)
    counts["excluded_sky_direction_anchor_cells_a"] = excluded_a.get(
        "sky_direction_domain", 0
    )
    counts["excluded_sky_direction_anchor_cells_b"] = excluded_b.get(
        "sky_direction_domain", 0
    )
    raw_cells_a = len(
        {cell for row in frame_a if (cell := screen_cell_key(row)) is not None}
    )
    raw_cells_b = len(
        {cell for row in frame_b if (cell := screen_cell_key(row)) is not None}
    )
    invalid_count_a = sum(invalid_a.values())
    invalid_count_b = sum(invalid_b.values())
    excluded_count_a = sum(excluded_a.values())
    excluded_count_b = sum(excluded_b.values())
    counts["raw_cells_a"] = raw_cells_a
    counts["raw_cells_b"] = raw_cells_b
    counts["invalid_cells_a"] = invalid_count_a
    counts["invalid_cells_b"] = invalid_count_b
    counts["excluded_cells_a"] = excluded_count_a
    counts["excluded_cells_b"] = excluded_count_b
    population_accounting_valid = (
        raw_cells_a == len(observations_a) + invalid_count_a + excluded_count_a
        and raw_cells_b == len(observations_b) + invalid_count_b + excluded_count_b
        and len(observations_a) == len(matches) + counts["unmatched_cells_a"]
        and len(observations_b) == len(used_b) + counts["new_cells_b"]
    )
    return {
        "scope": (
            "One cell-wide b43 glyph mask per ANSI cell, matched across frames "
            "by the elected mask owner's semantic anchor. Quarter-slot winners "
            "use the elected b55 Product row's world anchor. Center-terrain and "
            "late-effect winners use key_d. Ordinary sky is reported separately "
            "because its key_d is not a geometry anchor."
        ),
        "counts": dict(counts),
        "invalid_cells_a": dict(invalid_a),
        "invalid_cells_b": dict(invalid_b),
        "excluded_cells_a": dict(excluded_a),
        "excluded_cells_b": dict(excluded_b),
        "invalid_cell_count": sum(invalid_a.values()) + sum(invalid_b.values()),
        "population_accounting_valid": population_accounting_valid,
        "instability_pct": (
            float(counts["unstable_cells"]) / comparable * 100.0
            if comparable > 0
            else 0.0
        ),
        "glyph_instability_pct": (
            float(counts["winner_glyph_changed_cells"]) / comparable * 100.0
            if comparable > 0
            else 0.0
        ),
        "examples": examples,
    }


def changed_cell_mask_winner_key_fields(a: dict, b: dict) -> list[str]:
    """Name changing semantic-key words without inventing field semantics."""
    fields: list[str] = []
    for lane in ("key_a", "key_b", "key_c", "key_d"):
        a_words = a.get(lane) if isinstance(a, dict) else None
        b_words = b.get(lane) if isinstance(b, dict) else None
        if not isinstance(a_words, (list, tuple)) or not isinstance(
            b_words, (list, tuple)
        ):
            fields.append(f"{lane}.invalid")
            continue
        for index in range(max(len(a_words), len(b_words))):
            a_word = a_words[index] if index < len(a_words) else None
            b_word = b_words[index] if index < len(b_words) else None
            if a_word != b_word:
                fields.append(f"{lane}[{index}]")
    return fields


def cell_mask_winner_observed_product_relation(row: dict) -> dict:
    """Relate an ordinary winner to the Product carried by this subcell.

    This is deliberately unavailable for priority-zero late glyph effects,
    whose semantic key order differs.  For ordinary surfaces, matching base
    identity, Product provenance, and anchor bits proves that the cell winner
    is this observed subcell's Product rather than one of its three siblings.
    """
    winner = cell_mask_winner_witness(row)
    identity = row.get("final_product_exact_identity")
    if not winner["available"] or not isinstance(identity, dict):
        return {"available": False, "matches_observed_product": False}
    if winner["priority"] == 0:
        return {"available": False, "matches_observed_product": False}
    b55 = identity.get("b55")
    b64 = identity.get("b64")
    b60 = identity.get("b60_sidecar")
    if not all(isinstance(value, dict) for value in (b55, b64, b60)):
        return {"available": False, "matches_observed_product": False}
    key = winner["key"]
    expected_a = [
        b55.get("mapped_owner_kind"),
        b64.get("object_id"),
        b64.get("primitive_id"),
        b64.get("material_id"),
    ]
    expected_b = b60.get("output_r1")
    expected_anchor = b55.get("anchor_bits")
    available = bool(
        isinstance(key, dict)
        and isinstance(key.get("key_a"), list)
        and isinstance(key.get("key_b"), list)
        and isinstance(key.get("key_d"), list)
        and isinstance(expected_b, list)
        and len(expected_b) == 4
        and isinstance(expected_anchor, list)
        and len(expected_anchor) == 3
    )
    matches = bool(
        available
        and key["key_a"] == expected_a
        and key["key_b"] == expected_b
        and key["key_d"][:3] == expected_anchor
    )
    return {
        "available": available,
        "matches_observed_product": matches,
        "base_identity_equal": bool(available and key["key_a"] == expected_a),
        "source_provenance_equal": bool(available and key["key_b"] == expected_b),
        "anchor_bits_equal": bool(available and key["key_d"][:3] == expected_anchor),
    }


def cell_mask_candidate_witness(row: dict) -> dict:
    """Recover one quarter's stable Product identity for membership replay.

    The capture does not independently publish every non-winning semantic rank.
    It does publish the exact b55/b64 identity, Product provenance, and stable
    anchor for every quarter. Those fields identify whether the candidate that
    won at one endpoint still exists at the other endpoint without pretending
    that a missing rank can be reconstructed from the final glyph.
    """
    identity = row.get("final_product_exact_identity")
    if not isinstance(identity, dict):
        return {"available": False}
    b55 = identity.get("b55")
    b64 = identity.get("b64")
    b60 = identity.get("b60_sidecar")
    if not all(isinstance(value, dict) for value in (b55, b64, b60)):
        return {"available": False}
    base_identity = [
        b55.get("mapped_owner_kind"),
        b64.get("object_id"),
        b64.get("primitive_id"),
        b64.get("material_id"),
    ]
    provenance = b60.get("output_r1")
    anchor_bits = b55.get("anchor_bits")
    glyph = row.get("final_product_glyph")
    available = bool(
        b55.get("valid") is True
        and b64.get("valid") is True
        and all(isinstance(value, int) and not isinstance(value, bool) for value in base_identity)
        and isinstance(provenance, list)
        and len(provenance) == 4
        and all(isinstance(value, int) and not isinstance(value, bool) for value in provenance)
        and isinstance(anchor_bits, list)
        and len(anchor_bits) == 3
        and all(isinstance(value, int) and not isinstance(value, bool) for value in anchor_bits)
        and isinstance(glyph, int)
        and not isinstance(glyph, bool)
    )
    token_fields = {
        "base_identity": base_identity,
        "provenance": provenance,
        "anchor_bits": anchor_bits,
    }
    return {
        "available": available,
        "token": (
            json.dumps(token_fields, sort_keys=True, separators=(",", ":"))
            if available else None
        ),
        "glyph": int(glyph) if available else None,
        **token_fields,
    }


def cell_mask_winner_source_token(rows: list[dict]) -> dict:
    """Map Product's published cell winner back to exactly one quarter source."""
    if len(rows) != 4:
        return {"available": False, "reason": "incomplete_cell"}
    winners = [cell_mask_winner_witness(row) for row in rows]
    if not all(winner["available"] for winner in winners):
        return {"available": False, "reason": "winner_unavailable"}
    first = winners[0]
    if any(
        winner["key_token"] != first["key_token"]
        or winner["glyph"] != first["glyph"]
        or winner["priority"] != first["priority"]
        for winner in winners[1:]
    ):
        return {"available": False, "reason": "winner_not_cell_consistent"}
    key = first["key"]
    matches: list[dict] = []
    for row in rows:
        candidate = cell_mask_candidate_witness(row)
        if not candidate["available"]:
            return {"available": False, "reason": "candidate_unavailable"}
        ordinary_match = bool(
            key.get("key_a") == candidate["base_identity"]
            and key.get("key_b") == candidate["provenance"]
            and key.get("key_d", [])[:3] == candidate["anchor_bits"]
        )
        late_effect_match = bool(
            key.get("key_b") == candidate["base_identity"]
            and key.get("key_c") == candidate["provenance"]
            and key.get("key_d", [])[:3] == candidate["anchor_bits"]
        )
        if ordinary_match or late_effect_match:
            matches.append(candidate)
    if len(matches) != 1:
        return {
            "available": False,
            "reason": "winner_source_not_unique",
            "match_count": len(matches),
        }
    return {
        "available": True,
        "reason": "",
        "token": matches[0]["token"],
        "glyph": first["glyph"],
        "priority": first["priority"],
        "key": first["key"],
        "key_token": first["key_token"],
    }


def replay_product_base_glyph_consensus(
    candidates: list[dict], winner: dict
) -> dict:
    """Replay a Product-owned base-glyph consensus without temporal history.

    Product priorities below 850 are authored features (effects, shoreline,
    flowers, retained edges, relief, and foliage).  Their selected glyph stays
    authoritative.  Only ordinary terrain/base candidates may use a strict
    glyph majority.  A tie retains Product's semantic winner instead of making
    screen slot or glyph numeric order a hidden owner.
    """
    if not winner.get("available") or not all(
        candidate.get("available") for candidate in candidates
    ):
        return {"available": False, "reason": "missing_candidate_or_winner"}
    winner_glyph = int(winner["glyph"])
    winner_priority = int(winner["priority"])
    if winner_priority < 850:
        return {
            "available": True,
            "glyph": winner_glyph,
            "decision": "authored_feature_winner",
        }
    glyph_counts = Counter(int(candidate["glyph"]) for candidate in candidates)
    largest_count = max(glyph_counts.values())
    modes = sorted(
        glyph for glyph, count in glyph_counts.items() if count == largest_count
    )
    if len(modes) == 1:
        return {
            "available": True,
            "glyph": modes[0],
            "decision": "strict_base_glyph_majority",
        }
    return {
        "available": True,
        "glyph": winner_glyph,
        "decision": "semantic_winner_breaks_no_majority",
    }


def summarize_cell_mask_candidate_membership(
    frame_a: list[dict],
    frame_b: list[dict],
    occurrence_matches: dict[int, tuple[dict, float]],
) -> dict:
    """Classify mask churn using endpoint candidate membership only.

    A stable world-anchor match selects the endpoint ANSI cells. The replay is
    valid only when all four quarter Products and one published winner exist at
    both endpoints. It never invents a missing semantic rank, chooses a glyph by
    frequency, or lets the compositor become a second election owner.
    """
    def group(rows: list[dict]) -> dict[tuple[int, int], list[dict]]:
        grouped: dict[tuple[int, int], list[dict]] = {}
        for row in rows:
            key = screen_cell_key(row)
            if key:
                grouped.setdefault(key, []).append(row)
        return grouped

    groups_a = group(frame_a)
    groups_b = group(frame_b)
    row_to_b_cell: dict[tuple[int, int], set[tuple[int, int]]] = {}
    matched_row_counts: Counter = Counter()
    for ai, (matched_b, _distance) in occurrence_matches.items():
        a_cell = screen_cell_key(frame_a[ai])
        b_cell = screen_cell_key(matched_b)
        if not a_cell or not b_cell:
            continue
        row_to_b_cell.setdefault(a_cell, set()).add(b_cell)
        matched_row_counts[a_cell] += 1

    counts: Counter = Counter()
    reasons: Counter = Counter()
    examples: list[dict] = []
    for a_cell, b_cells in sorted(row_to_b_cell.items()):
        counts["candidate_endpoint_cells"] += 1
        if len(b_cells) != 1:
            reasons["ambiguous_reprojected_endpoint"] += 1
            continue
        b_cell = next(iter(b_cells))
        rows_a = sorted(groups_a.get(a_cell, []), key=lambda row: int(row.get("subcell_slot", -1)))
        rows_b = sorted(groups_b.get(b_cell, []), key=lambda row: int(row.get("subcell_slot", -1)))
        if (
            len(rows_a) != 4
            or len(rows_b) != 4
            or {int(row.get("subcell_slot", -1)) for row in rows_a} != {0, 1, 2, 3}
            or {int(row.get("subcell_slot", -1)) for row in rows_b} != {0, 1, 2, 3}
        ):
            reasons["incomplete_endpoint_cell"] += 1
            continue
        candidates_a = [cell_mask_candidate_witness(row) for row in rows_a]
        candidates_b = [cell_mask_candidate_witness(row) for row in rows_b]
        if not all(candidate["available"] for candidate in candidates_a + candidates_b):
            reasons["candidate_identity_unavailable"] += 1
            continue
        winner_a = cell_mask_winner_source_token(rows_a)
        winner_b = cell_mask_winner_source_token(rows_b)
        if not winner_a["available"] or not winner_b["available"]:
            reasons[
                f"winner_a:{winner_a.get('reason', 'unknown')}|winner_b:{winner_b.get('reason', 'unknown')}"
            ] += 1
            continue
        consensus_a = replay_product_base_glyph_consensus(candidates_a, winner_a)
        consensus_b = replay_product_base_glyph_consensus(candidates_b, winner_b)
        if not consensus_a["available"] or not consensus_b["available"]:
            reasons["base_glyph_consensus_unavailable"] += 1
            continue
        tokens_a = {candidate["token"] for candidate in candidates_a}
        tokens_b = {candidate["token"] for candidate in candidates_b}
        intersection = tokens_a & tokens_b
        if not intersection:
            reasons["empty_candidate_intersection"] += 1
            continue
        counts["comparable_endpoint_cells"] += 1
        membership_changed = tokens_a != tokens_b
        winner_source_changed = winner_a["token"] != winner_b["token"]
        winner_glyph_changed = winner_a["glyph"] != winner_b["glyph"]
        consensus_glyph_changed = consensus_a["glyph"] != consensus_b["glyph"]
        if membership_changed:
            counts["candidate_membership_changed_cells"] += 1
        if winner_source_changed:
            counts["winner_source_changed_cells"] += 1
            if winner_a["token"] not in tokens_b or winner_b["token"] not in tokens_a:
                classification = "winner_change_requires_membership_change"
            else:
                classification = "winner_change_with_both_sources_present"
        elif winner_glyph_changed:
            classification = "stable_winner_source_glyph_payload_change"
        else:
            classification = "stable_winner_source_and_glyph"
        counts[f"{classification}_cells"] += 1
        counts[f"consensus_a:{consensus_a['decision']}_cells"] += 1
        counts[f"consensus_b:{consensus_b['decision']}_cells"] += 1
        if winner_glyph_changed:
            counts["published_winner_glyph_changed_cells"] += 1
        if consensus_glyph_changed:
            counts["replayed_base_consensus_glyph_changed_cells"] += 1
        elif winner_glyph_changed:
            counts["replayed_base_consensus_prevented_change_cells"] += 1
        if len(examples) < 50 and classification != "stable_winner_source_and_glyph":
            examples.append({
                "cell_a": list(a_cell),
                "cell_b": list(b_cell),
                "matched_anchor_rows": int(matched_row_counts[a_cell]),
                "classification": classification,
                "membership_changed": membership_changed,
                "intersection_size": len(intersection),
                "winner_a_priority": winner_a["priority"],
                "winner_b_priority": winner_b["priority"],
                "winner_a_glyph": winner_a["glyph"],
                "winner_b_glyph": winner_b["glyph"],
                "consensus_a_glyph": consensus_a["glyph"],
                "consensus_b_glyph": consensus_b["glyph"],
                "consensus_a_decision": consensus_a["decision"],
                "consensus_b_decision": consensus_b["decision"],
            })
    return {
        "scope": (
            "Stable-anchor endpoint ANSI cells with complete four-quarter Product "
            "identity and an exact published-winner-to-quarter join. The replay "
            "keeps authored feature priorities below 850 and applies a strict "
            "glyph majority only to ordinary base candidates; it contains no "
            "temporal state and is diagnostic rather than acceptance proof."
        ),
        "counts": dict(counts),
        "excluded_reasons": dict(reasons),
        "examples": examples,
    }


def product_surface_bucket(row: dict) -> str:
    owner = row.get("final_product_owner_name")
    if owner in (None, "", "unknown"):
        return "product:unknown"
    return f"product:{owner}"


def comparable_final_cell_signature_fields(a: dict, b: dict) -> tuple[dict, dict]:
    return world_product_signature_fields(a), world_product_signature_fields(b)


def changed_final_cell_signature_fields(a: dict, b: dict) -> list[str]:
    fa, fb = comparable_final_cell_signature_fields(a, b)
    return [name for name in fa if fa.get(name) != fb.get(name)]


# FL-4359 same-key proof (Codex slice): terrain_detail_fact bit layout — must match
# cell_owner_product.glsl product_pack_terrain_detail_fact (struct variant ~:5467).
def unpack_terrain_detail(fact) -> dict:
    if fact is None:
        return {}
    f = int(fact)
    return {
        "slope_class": f & 0x3,
        "ridge": (f >> 2) & 0x1,
        "cliff": (f >> 3) & 0x1,
        "contact_shadow": (f >> 4) & 0x1,
        "ramp_band": (f >> 5) & 0x7,
        "snow": (f >> 8) & 0x1,
        "glyph_role": (f >> 9) & 0xF,
        "depth_class": (f >> 13) & 0x7,
        "material_tone_band": (f >> 16) & 0xF,
        "clone_peak_glyph_lane": (f >> 20) & 0x1,
        "convex_edge": (f >> 21) & 0x1,
        "concave_edge": (f >> 22) & 0x1,
        "heightfield_neighbor_state": (f >> 23) & 0xF,
        "sidewall_score_class": (f >> 27) & 0xF,
    }


# Semantic fields compared for a same-key terrain decision: the unpacked detail-fact
# sub-decisions plus the final glyph and material role. (NOT depth/owner/coverage/UV.)
SAME_KEY_SEMANTIC_FIELDS = [
    "slope_class", "ridge", "cliff", "contact_shadow", "ramp_band", "snow",
    "glyph_role", "depth_class", "material_tone_band", "clone_peak_glyph_lane",
    "convex_edge", "concave_edge", "heightfield_neighbor_state", "sidewall_score_class",
    "glyph_id", "material_role",
]

FL4359_REQUIRED_FIELDS = [
    "frame_index",
    "movement_phase",
    "admission_bits",
    "snap_grid_phase",
    "pixel_camera_snap_world",
    "product_time_seconds",
    "selected_surface_owner",
    "selected_surface_object",
    "selected_surface_primitive",
    "selected_surface_material",
    "selected_surface_scene_revision",
    "selected_surface_view_revision",
    "selected_surface_projection_kind",
    "final_product_owner",
    "final_product_status",
    "final_product_source_key",
    "final_product_matcell_packed",
    "final_product_world_anchor",
    "comparison_anchor_source",
    "capture_schema",
    "sample_index",
    "subcell_slot",
    "final_product_glyph",
    "final_product_material_family",
    "final_product_material_id",
    "final_product_fg_rgb",
    "final_product_bg_rgb",
    "final_product_trace_receiver_bits",
    "final_product_continuous_sample_bits",
    "final_product_continuous_sample_valid",
    "final_product_terrain_colour_witness_bits",
    "final_product_terrain_colour_witness_valid",
    "final_product_pre_finalization_signature_bits",
    "final_product_post_finalization_signature_bits",
    "final_paint_slot_glyph",
    "final_paint_slot_present",
    "final_paint_slot_actor_overlay",
    "final_paint_slot_colour_present",
    "final_paint_slot_fg_rgb",
    "final_paint_slot_bg_rgb",
    "final_paint_glyph",
    "final_paint_mask_presence",
    "final_paint_mask_contributor_count",
    *TERMINAL_OCCURRENCE_FIELDS,
]

# The current sparse producer publishes these facts with every v2 row.  The
# marker is intentionally separate from FL4359_REQUIRED_FIELDS so historical
# synthetic fixtures remain useful while a producer-marked v2 capture cannot
# silently omit lifecycle provenance.
LIFECYCLE_FACT_FIELDS = (
    "perspective_probe_state",
    "dynamic_mover_timing",
    "water_interaction_field_metrics",
)
MATRIX_CONFIGURATION_REQUIRED_FIELDS = (
    "schema",
    "key",
    "actual_image_size",
    "target_size",
    "presentation_size",
    "cell_size",
    "semantic_ansi_size",
    "candidate_extent",
    "candidate_extent_generation",
    "cell_aspect",
    "projection",
    "presentation_mode",
)
PERSPECTIVE_PROBE_REQUIRED_FIELDS = (
    "valid",
    "absence_reason",
    "scene_revision",
    "view_revision",
    "terrain_revision",
    "water_revision",
    "static_revision",
    "dynamic_revision",
    "capture_generation",
    "recapture_reason",
    "recapture_mask",
    "active_mask",
    "transition_remaining",
    "face_masks",
    "recaptured_faces",
    "families_submitted",
    "steady_reuse",
    "candidate_extent",
    "candidate_extent_generation",
    "materialized_records",
)


def _finite_lifecycle_value(value: object) -> bool:
    """Reject null, non-finite, or non-JSON lifecycle payload members."""
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        return True
    if isinstance(value, (list, tuple)):
        return all(_finite_lifecycle_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _finite_lifecycle_value(item)
            for key, item in value.items()
        )
    return False


def _matrix_identity_pair(value: object) -> tuple[int, int] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y")]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value):
        return None
    return int(value[0]), int(value[1])


def _coherent_matrix_identity_key(value: dict) -> str | None:
    pairs = {
        field: _matrix_identity_pair(value.get(field))
        for field in (
            "actual_image_size",
            "target_size",
            "presentation_size",
            "cell_size",
            "semantic_ansi_size",
            "candidate_extent",
        )
    }
    if any(pair is None for pair in pairs.values()):
        return None
    actual = pairs["actual_image_size"]
    target = pairs["target_size"]
    presentation = pairs["presentation_size"]
    cell = pairs["cell_size"]
    ansi = pairs["semantic_ansi_size"]
    candidate = pairs["candidate_extent"]
    if candidate != (ansi[0] * 2, ansi[1] * 2):
        return None
    aspect = value.get("cell_aspect")
    if aspect == "square":
        if cell[0] != cell[1]:
            return None
    elif aspect == "terminal":
        if cell[1] != cell[0] * 2:
            return None
    else:
        return None
    projection = value.get("projection")
    mode = value.get("presentation_mode")
    generation = value.get("candidate_extent_generation")
    if (
        projection not in ("orthographic", "perspective")
        or mode not in ("FF", "FC", "CF", "CC")
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or (projection == "perspective" and generation <= 0)
    ):
        return None
    dimensions = (*actual, *target, *presentation, *cell, *ansi, *candidate)
    return (
        "%dx%d|%dx%d|%dx%d|%dx%d|%dx%d|%dx%d|g%d|%s|%s|%s"
        % (*dimensions, generation, aspect, projection, mode)
    )


def lifecycle_fact_is_valid(name: str, value: object, row: dict | None = None) -> bool:
    """Validate producer lifecycle facts without inventing performance limits."""
    if not isinstance(value, dict) or not value:
        return False
    if name == "perspective_probe_state" and any(
        field not in value for field in PERSPECTIVE_PROBE_REQUIRED_FIELDS
    ):
        return False
    if name == "matrix_identity":
        coherent_key = _coherent_matrix_identity_key(value)
        if (
            any(field not in value for field in MATRIX_CONFIGURATION_REQUIRED_FIELDS)
            or value.get("schema") != "fl4359.matrix_configuration_identity.v1"
            or coherent_key is None
            or value.get("key") != coherent_key
        ):
            return False
    if not _finite_lifecycle_value(value):
        return False
    if value.get("valid") is False or value.get("pass") is False:
        return False
    if value.get("failed") is True:
        return False
    for field in ("absence_reason", "failure_reason", "error_reason"):
        if field in value and value.get(field) not in ("", None):
            return False
    error = value.get("error")
    if error is True or (isinstance(error, str) and error):
        return False
    if name == "perspective_probe_state" and isinstance(row, dict):
        trace = row.get("frame_trace")
        if isinstance(trace, dict):
            for revision in ("scene_revision", "view_revision"):
                if revision in trace and value.get(revision) != trace.get(revision):
                    return False
    if name == "matrix_identity" and isinstance(row, dict):
        trace = row.get("frame_trace")
        if isinstance(trace, dict):
            for identity_field, trace_field in (
                ("target_size", "target_size"),
                ("cell_size", "cell_size"),
                ("semantic_ansi_size", "semantic_ansi_size"),
            ):
                if value.get(identity_field) != trace.get(trace_field):
                    return False
            probe = trace.get("perspective_probe_state")
            if value.get("projection") == "perspective" and isinstance(probe, dict):
                if (
                    _matrix_identity_pair(value.get("candidate_extent"))
                    != _matrix_identity_pair(probe.get("candidate_extent"))
                    or value.get("candidate_extent_generation")
                    != probe.get("candidate_extent_generation")
                ):
                    return False
    return True


def summarize_lifecycle_facts(rows: list[dict]) -> dict:
    def trace_for(row: dict) -> dict:
        trace = row.get("frame_trace")
        return trace if isinstance(trace, dict) else {}

    required_rows = [
        row
        for row in rows
        if row.get("lifecycle_facts_required") is True
        or trace_for(row).get("lifecycle_facts_required") is True
    ]
    invalid = []
    validation_cache: dict[tuple[str, int, object, object], bool] = {}
    for index, row in enumerate(required_rows):
        trace = trace_for(row)
        required_fields = LIFECYCLE_FACT_FIELDS
        if trace.get("matrix_identity_required") is True:
            required_fields = (*required_fields, "matrix_identity")
        for field in required_fields:
            value = row.get(field, trace.get(field))
            # Full-grid captures attach one shared frame trace to every subcell.
            # Validate that producer payload once per frame/revision identity;
            # recursively walking it for every subcell made a five-frame oracle
            # traverse the same nested publication tables 160,460 times.
            cache_key = (
                field,
                id(value),
                trace.get("scene_revision"),
                trace.get("view_revision"),
            )
            valid = validation_cache.get(cache_key)
            if valid is None:
                valid = lifecycle_fact_is_valid(field, value, row)
                validation_cache[cache_key] = valid
            if not valid:
                invalid.append({"row_index": index, "field": field})
    return {
        "required": bool(required_rows),
        "all_valid": bool(required_rows) and not invalid if required_rows else True,
        "required_rows": len(required_rows),
        "invalid": invalid,
    }


def final_product_status_is_valid(status: object) -> bool:
    """Validate the exact ABI-v2 status published by the current b55 Product."""
    try:
        value = int(status)
    except (TypeError, ValueError):
        return False
    return bool(
        value & FINAL_PRODUCT_STATUS_VALID
        and (value & FINAL_PRODUCT_STATUS_ABI_MASK) == FINAL_PRODUCT_STATUS_ABI_V2
        and (value & ~FINAL_PRODUCT_STATUS_KNOWN_MASK) == 0
    )


def _same_frame_oracle_trace(row: dict) -> dict:
    trace = row.get("frame_trace")
    return trace if isinstance(trace, dict) else {}


def _load_runtime_receipt(path: str) -> dict:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _same_frame_oracle_coverage(rows: list[dict], runtime_receipt: dict) -> dict:
    """Describe admitted coverage without treating sparse rows as a grid.

    The capture shader intentionally admits rows by two modulo lanes.  This
    helper therefore reports the exact admitted cardinality and separately
    computes exhaustive coverage.  Temporal validity additionally requires
    contiguous frame/sequence identities, runtime authority continuity, and a
    durable current-build identity.
    """
    oracle_rows = [
        row for row in rows if row.get("oracle_schema") == SAME_FRAME_ORACLE_SCHEMA
    ]
    by_frame: dict[int, list[dict]] = {}
    for row in oracle_rows:
        try:
            frame_id = int(row.get("frame_index", row.get("frame_id", -1)))
        except (TypeError, ValueError):
            frame_id = -1
        by_frame.setdefault(frame_id, []).append(row)
    expected_by_frame: dict[int, int] = {}
    unique_by_frame: dict[int, int] = {}
    duplicate_by_frame: dict[int, int] = {}
    missing_by_frame: dict[int, int] = {}
    out_of_range_by_frame: dict[int, list[int]] = {}
    exact_index_set_by_frame: dict[int, bool] = {}
    metadata_missing_fields: Counter = Counter()
    scene_revisions: set[int] = set()
    view_revisions: set[int] = set()
    projection_kinds: set[int] = set()
    sequence_frames: set[int] = set()
    sequence_by_frame: dict[int, int] = {}
    sequence_inconsistent_frames: set[int] = set()
    frame_indices: set[int] = set()
    camera_identities: set[str] = set()
    dynamic_revisions: set[int] = set()
    sampling_policies: set[str] = set()
    world_strides: set[int] = set()
    screen_strides: set[int] = set()
    ansi_sizes: set[tuple[int, int]] = set()
    for frame_id, frame_rows in by_frame.items():
        indices: list[int] = []
        frame_sequences: set[int] = set()
        for row in frame_rows:
            trace = _same_frame_oracle_trace(row)
            probe_state = trace.get("perspective_probe_state", {})
            if not isinstance(probe_state, dict):
                probe_state = {}
            expected = trace.get(
                "oracle_expected_subcells_per_frame",
                probe_state.get("materialized_records", trace.get("record_count", 0)),
            )
            try:
                expected_int = int(expected)
            except (TypeError, ValueError):
                expected_int = 0
            if expected_int > 0:
                expected_by_frame[frame_id] = expected_int
            try:
                indices.append(int(row.get("sample_index", -1)))
            except (TypeError, ValueError):
                indices.append(-1)
            scene = trace.get("scene_revision", row.get("oracle_scene_revision"))
            view = trace.get("view_revision", row.get("oracle_view_revision"))
            projection = trace.get(
                "camera_projection_mode", row.get("oracle_projection_kind")
            )
            for field, value in (
                ("scene_revision", scene),
                ("view_revision", view),
                ("projection_kind", projection),
                ("sequence_frame", trace.get("sequence_frame")),
                ("camera_world", trace.get("camera_world")),
                ("dynamic_revision", row.get("oracle_dynamic_revision")),
                ("recorder_identity", trace.get("recorder_identity")),
            ):
                if value is None or value == {}:
                    metadata_missing_fields[field] += 1
            if scene is not None:
                try:
                    scene_revisions.add(int(scene))
                except (TypeError, ValueError):
                    pass
            if view is not None:
                try:
                    view_revisions.add(int(view))
                except (TypeError, ValueError):
                    pass
            if projection is not None:
                try:
                    projection_kinds.add(int(projection))
                except (TypeError, ValueError):
                    pass
            sequence = trace.get("sequence_frame")
            if sequence is not None:
                try:
                    sequence_value = int(sequence)
                    sequence_frames.add(sequence_value)
                    frame_sequences.add(sequence_value)
                except (TypeError, ValueError):
                    pass
            if frame_id >= 0:
                frame_indices.add(frame_id)
            camera = trace.get("camera_world")
            if isinstance(camera, dict):
                camera_identities.add(json.dumps(camera, sort_keys=True))
            dynamic_revision = row.get("oracle_dynamic_revision")
            if dynamic_revision is not None:
                try:
                    dynamic_revisions.add(int(dynamic_revision))
                except (TypeError, ValueError):
                    pass
            sampling = trace.get(
                "oracle_sampling_policy", SAME_FRAME_ORACLE_SAMPLING_POLICY
            )
            sampling_policies.add(str(sampling))
            for destination, key in (
                (world_strides, "oracle_world_stride"),
                (screen_strides, "oracle_screen_stride"),
            ):
                value = trace.get(key)
                if value is not None:
                    try:
                        destination.add(int(value))
                    except (TypeError, ValueError):
                        pass
            semantic = trace.get("viewport", {}).get("semantic_ansi_size", [])
            if isinstance(semantic, (list, tuple)) and len(semantic) >= 2:
                try:
                    ansi_sizes.add((int(semantic[0]), int(semantic[1])))
                except (TypeError, ValueError):
                    pass
        unique = len(set(indices))
        unique_by_frame[frame_id] = unique
        duplicate_by_frame[frame_id] = max(0, len(indices) - unique)
        expected_int = expected_by_frame.get(frame_id, 0)
        expected_indices = set(range(expected_int)) if expected_int > 0 else set()
        observed_indices = set(indices)
        exact_index_set_by_frame[frame_id] = bool(expected_indices) and (
            observed_indices == expected_indices
        )
        out_of_range_by_frame[frame_id] = sorted(
            index for index in observed_indices if index not in expected_indices
        )
        missing_by_frame[frame_id] = (
            len(expected_indices - observed_indices) if expected_int > 0 else 0
        )
        if len(frame_sequences) == 1:
            sequence_by_frame[frame_id] = next(iter(frame_sequences))
        else:
            sequence_inconsistent_frames.add(frame_id)
    all_frames_complete = bool(by_frame) and bool(expected_by_frame) and all(
        expected_by_frame.get(frame_id, 0) > 0
        and unique_by_frame.get(frame_id, 0) == expected_by_frame[frame_id]
        and duplicate_by_frame.get(frame_id, 0) == 0
        and missing_by_frame.get(frame_id, 0) == 0
        and exact_index_set_by_frame.get(frame_id, False)
        for frame_id in by_frame
    )
    sorted_frames = sorted(frame_indices)
    sorted_sequences = sorted(sequence_frames)
    contiguous_frames = bool(sorted_frames) and sorted_frames == list(
        range(sorted_frames[0], sorted_frames[-1] + 1)
    )
    contiguous_sequences = bool(sorted_sequences) and sorted_sequences == list(
        range(sorted_sequences[0], sorted_sequences[-1] + 1)
    )
    ordered_sequences = [
        sequence_by_frame[frame_id]
        for frame_id in sorted_frames
        if frame_id in sequence_by_frame
    ]
    monotonic_unique_sequences = (
        bool(sorted_frames)
        and len(ordered_sequences) == len(sorted_frames)
        and not sequence_inconsistent_frames
        and all(
            current > previous
            for previous, current in zip(ordered_sequences, ordered_sequences[1:])
        )
    )
    authority = runtime_receipt.get("authority_tick_continuity", {})
    authority_present = isinstance(authority, dict) and bool(authority)
    authority_window = authority.get("runtime_measurement_window", {}) \
        if authority_present else {}
    authority_ingest_advanced = bool(
        isinstance(authority_window, dict)
        and isinstance(authority_window.get("ingest_seq_delta"), (int, float))
        and int(authority_window["ingest_seq_delta"]) > 0
    )
    authority_queue_valid = bool(
        authority_present
        and authority.get("temporal_claim_valid") is True
        and authority_ingest_advanced
    )
    authority_ticks_by_frame: dict[int, int] = {}
    for frame_id, frame_rows in by_frame.items():
        ticks = {
            int(trace["authority_server_tick"])
            for row in frame_rows
            if isinstance((trace := _same_frame_oracle_trace(row)), dict)
            and isinstance(trace.get("authority_server_tick"), (int, float))
            and int(trace["authority_server_tick"]) >= 0
        }
        if len(ticks) == 1:
            authority_ticks_by_frame[frame_id] = next(iter(ticks))
    ordered_authority_ticks = [
        authority_ticks_by_frame[frame_id]
        for frame_id in sorted_frames
        if frame_id in authority_ticks_by_frame
    ]
    authority_tick_identity_valid = bool(
        sorted_frames
        and len(ordered_authority_ticks) == len(sorted_frames)
        and all(
            current >= previous
            for previous, current in zip(
                ordered_authority_ticks, ordered_authority_ticks[1:]
            )
        )
    )
    authority_window_tick_start = (
        int(authority_window.get("server_tick_start", -1))
        if isinstance(authority_window, dict) else -1
    )
    authority_window_tick_end = (
        int(authority_window.get("server_tick_end", -1))
        if isinstance(authority_window, dict) else -1
    )
    authority_tick_window_bounds_valid = bool(
        authority_tick_identity_valid
        and authority_window_tick_start >= 0
        and authority_window_tick_end > authority_window_tick_start
        and authority_window_tick_start <= ordered_authority_ticks[0]
        and ordered_authority_ticks[-1] <= authority_window_tick_end
    )
    authority_tick_gaps = [
        current - previous
        for previous, current in zip(
            ordered_authority_ticks, ordered_authority_ticks[1:]
        )
    ]
    authority_tick_reuse_transitions = sum(
        1 for gap in authority_tick_gaps if gap == 0
    )
    run_identity = runtime_receipt.get("run_identity", {})
    if not isinstance(run_identity, dict):
        run_identity = {}
    build_head = str(run_identity.get("build_head") or "")
    diff_hash = str(
        run_identity.get("build_worktree_diff_sha256")
        or runtime_receipt.get("worktree_diff_sha256")
        or ""
    )
    source_identity_valid = bool(build_head and diff_hash)
    day_cycle_time = runtime_receipt.get("day_cycle_time")
    day_cycle_time_pin_valid = bool(
        isinstance(day_cycle_time, (int, float))
        and not isinstance(day_cycle_time, bool)
        and math.isfinite(float(day_cycle_time))
        and math.isclose(
            float(day_cycle_time),
            FULL_GRID_REQUIRED_DAY_CYCLE_TIME,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
    )
    celestial_by_frame: dict[int, tuple[float, float, float, float, float]] = {}
    celestial_invalid_rows = 0
    celestial_inconsistent_frames: list[int] = []
    for frame_id, frame_rows in by_frame.items():
        frame_witnesses: set[tuple[float, float, float, float, float]] = set()
        for row in frame_rows:
            trace = _same_frame_oracle_trace(row)
            celestial_time = trace.get("celestial_time")
            night_factor = trace.get("celestial_night_factor")
            sun_direction = trace.get("celestial_sun_direction")
            if (
                not isinstance(celestial_time, (int, float))
                or isinstance(celestial_time, bool)
                or not isinstance(night_factor, (int, float))
                or isinstance(night_factor, bool)
                or not isinstance(sun_direction, (list, tuple))
                or len(sun_direction) != 3
                or not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    for value in sun_direction
                )
            ):
                celestial_invalid_rows += 1
                continue
            witness = (
                float(celestial_time),
                float(night_factor),
                *(float(value) for value in sun_direction),
            )
            direction_length = math.sqrt(sum(value * value for value in witness[2:]))
            if (
                not all(math.isfinite(value) for value in witness)
                or not 0.0 <= witness[0] < 1.0
                or not 0.0 <= witness[1] <= 1.0
                or not math.isclose(direction_length, 1.0, rel_tol=0.0, abs_tol=1.0e-5)
            ):
                celestial_invalid_rows += 1
                continue
            frame_witnesses.add(witness)
        if len(frame_witnesses) == 1:
            celestial_by_frame[frame_id] = next(iter(frame_witnesses))
        else:
            celestial_inconsistent_frames.append(frame_id)
    ordered_celestial_witnesses = [
        celestial_by_frame[frame_id]
        for frame_id in sorted_frames
        if frame_id in celestial_by_frame
    ]
    # DECOMPOSED REASONS (FL-4359 diagnostic transport, not a law change).
    # This conjunction previously collapsed six independent conditions into one
    # boolean, so a red receipt could not distinguish "the celestial witness
    # MOVED during the sequence" — a real temporal defect that voids a colour
    # proof — from "the witness is perfectly frozen but does not equal the
    # requested pin", which is a producer/configuration fact. Each conjunct now
    # names itself. The validity expression below is unchanged.
    celestial_witness_frozen = bool(
        ordered_celestial_witnesses
        and len(set(ordered_celestial_witnesses)) == 1
    )
    celestial_witness_matches_pin = bool(
        ordered_celestial_witnesses
        and day_cycle_time_pin_valid
        and math.isclose(
            ordered_celestial_witnesses[0][0],
            float(day_cycle_time),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
    )
    celestial_witness_invalid_reasons = [
        name
        for name, failed in (
            ("day_cycle_time_pin_not_0.5", not day_cycle_time_pin_valid),
            ("celestial_witness_rows_invalid", celestial_invalid_rows != 0),
            ("celestial_witness_absent", not ordered_celestial_witnesses),
            (
                "celestial_witness_frame_coverage_incomplete",
                len(ordered_celestial_witnesses) != len(sorted_frames),
            ),
            (
                "celestial_witness_inconsistent_within_frame",
                bool(celestial_inconsistent_frames),
            ),
            (
                "celestial_witness_changed_across_frames",
                bool(ordered_celestial_witnesses) and not celestial_witness_frozen,
            ),
            (
                # Frozen but not at the requested hour. Producer fact: the
                # engine only honours --fl4408-day-cycle-time when the
                # day_night_cycle baseline feature is enabled; otherwise
                # ASCIICompositorEffect.gd:771-778 pins the legacy static value
                # and the requested pin is silently ignored.
                "celestial_witness_frozen_but_not_at_requested_pin",
                celestial_witness_frozen and not celestial_witness_matches_pin,
            ),
        )
        if failed
    ]
    celestial_runtime_witness_valid = bool(
        day_cycle_time_pin_valid
        and celestial_invalid_rows == 0
        and bool(ordered_celestial_witnesses)
        and len(ordered_celestial_witnesses) == len(sorted_frames)
        and not celestial_inconsistent_frames
        and celestial_witness_frozen
        and celestial_witness_matches_pin
    )
    temporal_identity_valid = (
        bool(oracle_rows)
        and len(by_frame) >= 2
        and all_frames_complete
        and contiguous_frames
        and monotonic_unique_sequences
        and not metadata_missing_fields
        and authority_queue_valid
        and authority_tick_window_bounds_valid
        and source_identity_valid
        # Product's animation pin does not own the celestial UBO. A temporal
        # colour proof must separately freeze the day/night authority or normal
        # sun motion becomes indistinguishable from shimmer.
        and celestial_runtime_witness_valid
        and sampling_policies == {FULL_GRID_TEMPORAL_ORACLE_SAMPLING_POLICY}
        and world_strides == {1}
        and screen_strides == {1}
    )
    return {
        "same_frame_oracle_frame_count": len(by_frame),
        "same_frame_oracle_frame_indices": sorted_frames,
        "same_frame_oracle_sequence_frames": sorted_sequences,
        "same_frame_oracle_sequence_by_frame": {
            str(frame_id): sequence_by_frame[frame_id]
            for frame_id in sorted(sequence_by_frame)
        },
        "same_frame_oracle_rows_per_frame": {
            str(frame_id): len(frame_rows)
            for frame_id, frame_rows in sorted(by_frame.items())
        },
        "same_frame_oracle_unique_subcells_per_frame": {
            str(frame_id): unique_by_frame[frame_id]
            for frame_id in sorted(unique_by_frame)
        },
        "same_frame_oracle_expected_subcells_per_frame": sorted(
            set(expected_by_frame.values())
        ),
        "same_frame_oracle_duplicate_subcells_per_frame": duplicate_by_frame,
        "same_frame_oracle_missing_subcells_per_frame": missing_by_frame,
        "same_frame_oracle_exact_sample_index_set_per_frame": {
            str(frame_id): exact_index_set_by_frame[frame_id]
            for frame_id in sorted(exact_index_set_by_frame)
        },
        "same_frame_oracle_out_of_range_sample_indices_per_frame": {
            str(frame_id): out_of_range_by_frame[frame_id]
            for frame_id in sorted(out_of_range_by_frame)
        },
        "same_frame_oracle_total_distinct_subcells": len(
            {
                int(row.get("sample_index", -1))
                for row in oracle_rows
                if isinstance(row.get("sample_index"), (int, float))
            }
        ),
        "same_frame_oracle_sampling_policy": sorted(sampling_policies),
        "same_frame_oracle_world_strides": sorted(world_strides),
        "same_frame_oracle_screen_strides": sorted(screen_strides),
        "same_frame_oracle_ansi_sizes": [list(size) for size in sorted(ansi_sizes)],
        "same_frame_oracle_dynamic_revisions": sorted(dynamic_revisions),
        "same_frame_oracle_spatial_snapshot": bool(oracle_rows),
        "same_frame_oracle_exhaustive_frame_coverage": all_frames_complete,
        "same_frame_oracle_frame_indices_contiguous": contiguous_frames,
        "same_frame_oracle_sequence_frames_contiguous": contiguous_sequences,
        "same_frame_oracle_sequence_frames_monotonic_unique": monotonic_unique_sequences,
        "same_frame_oracle_sequence_inconsistent_frames": sorted(
            sequence_inconsistent_frames
        ),
        "same_frame_oracle_metadata_missing_fields": dict(
            sorted(metadata_missing_fields.items())
        ),
        # Zero queue drops and exact rendered-frame tick identity are separate
        # facts. Neither field may be promoted into the other claim.
        "same_frame_oracle_authority_queue_window_valid": authority_queue_valid,
        "same_frame_oracle_authority_ingest_advanced": authority_ingest_advanced,
        "same_frame_oracle_authority_tick_identity_valid": authority_tick_identity_valid,
        # A renderer may legitimately present more than one frame from one
        # immutable server snapshot. Reuse is especially useful for detecting
        # renderer-only shimmer under stable authority. Every frame must name
        # exactly one tick and the mapping must never move backward; queue-drop
        # evidence separately proves that no published snapshot was discarded.
        "same_frame_oracle_authority_tick_mapping_law": (
            "one_exact_tick_per_render_frame_nondecreasing_snapshot_reuse_allowed"
        ),
        "same_frame_oracle_authority_tick_reuse_transitions": (
            authority_tick_reuse_transitions
        ),
        "same_frame_oracle_authority_tick_window_bounds_valid": (
            authority_tick_window_bounds_valid
        ),
        "same_frame_oracle_authority_tick_gaps": authority_tick_gaps,
        "same_frame_oracle_authority_ticks_by_frame": {
            str(frame_id): authority_ticks_by_frame[frame_id]
            for frame_id in sorted(authority_ticks_by_frame)
        },
        "same_frame_oracle_authority_tick_start": (
            authority_window_tick_start if authority_window_tick_start >= 0 else None
        ),
        "same_frame_oracle_authority_tick_end": (
            authority_window_tick_end if authority_window_tick_end >= 0 else None
        ),
        # Compatibility name. It now requires the positive queue witness plus
        # a per-rendered-frame authority-tick association.
        "same_frame_oracle_authority_tick_continuity_valid": (
            authority_queue_valid and authority_tick_window_bounds_valid
        ),
        "same_frame_oracle_source_identity_valid": source_identity_valid,
        "same_frame_oracle_fixed_day_cycle_time_valid": day_cycle_time_pin_valid,
        "same_frame_oracle_day_cycle_time": (
            float(day_cycle_time) if day_cycle_time_pin_valid else None
        ),
        "same_frame_oracle_celestial_runtime_witness_valid": (
            celestial_runtime_witness_valid
        ),
        "same_frame_oracle_celestial_invalid_rows": celestial_invalid_rows,
        "same_frame_oracle_celestial_witness_frozen": celestial_witness_frozen,
        "same_frame_oracle_celestial_witness_matches_pin": (
            celestial_witness_matches_pin
        ),
        "same_frame_oracle_celestial_witness_invalid_reasons": (
            celestial_witness_invalid_reasons
        ),
        "same_frame_oracle_celestial_inconsistent_frames": (
            celestial_inconsistent_frames
        ),
        "same_frame_oracle_celestial_witness_by_frame": {
            str(frame_id): list(celestial_by_frame[frame_id])
            for frame_id in sorted(celestial_by_frame)
        },
        "same_frame_oracle_temporal_sequence_valid": temporal_identity_valid,
        "same_frame_oracle_temporal_invalid_reasons": [
            reason
            for reason, failed in (
                ("no_time_separated_frames", len(by_frame) < 2),
                ("incomplete_subcell_coverage", not all_frames_complete),
                ("frame_indices_not_contiguous", not contiguous_frames),
                (
                    "sequence_frames_not_monotonic_unique",
                    not monotonic_unique_sequences,
                ),
                ("missing_frame_metadata", bool(metadata_missing_fields)),
                ("authority_queue_drop_or_missing", not authority_queue_valid),
                (
                    "authority_tick_identity_missing_backward_or_outside_window",
                    not authority_tick_window_bounds_valid,
                ),
                ("missing_current_build_identity", not source_identity_valid),
                ("day_cycle_time_not_fixed_to_0.5", not day_cycle_time_pin_valid),
                (
                    "celestial_runtime_witness_missing_invalid_or_changed",
                    not celestial_runtime_witness_valid,
                ),
                (
                    "capture_not_exhaustive_policy",
                    sampling_policies != {FULL_GRID_TEMPORAL_ORACLE_SAMPLING_POLICY}
                    or world_strides != {1}
                    or screen_strides != {1},
                ),
            )
            if failed
        ],
    }


def summarize_same_frame_oracle(
    rows: list[dict], details_limit: int = 50, runtime_receipt: dict | None = None
) -> dict:
    """Classify production visibility against the same invocation's direct ray.

    This is not a historical-renderer comparison. Every admitted row must carry
    the current VSB b86 record joined to b61, b64, b55, and b43 from one frame.
    Depth remains a measured distribution because no uncalibrated tolerance is
    promoted into a visual law here.
    """
    classification_counts: Counter = Counter()
    downstream_counts: Counter = Counter()
    source_attribution: Counter = Counter()
    examples: list[dict] = []
    depth_deltas: list[float] = []
    schema_rows = 0
    structurally_valid_rows = 0
    query_invalid_rows = 0
    for row in rows:
        if row.get("oracle_schema") != SAME_FRAME_ORACLE_SCHEMA:
            continue
        schema_rows += 1
        if bool(row.get("oracle_record_valid", False)):
            structurally_valid_rows += 1
        if not bool(row.get("oracle_query_valid", False)):
            query_invalid_rows += 1
        classification = str(
            row.get("oracle_classification", "oracle_record_missing")
        )
        classification_counts[classification] += 1
        product_valid = final_product_status_is_valid(
            row.get("final_product_status")
        )
        product_owner = str(row.get("final_product_owner_name", "unknown"))
        # SceneQuery answers only geometry presence. Ordinary sky is the
        # correct Product result when that ray misses geometry, so treating a
        # valid sky product as a commission would make the oracle condemn its
        # own expected backstop. Unknown owners remain fail-closed instead of
        # being promoted to geometry evidence.
        product_geometry_present = product_valid and product_owner not in {
            "",
            "sky",
            "unknown",
        }
        paint_valid = int(row.get("final_paint_magic", 0)) == FINAL_CELL_MAGIC
        direct_hit = bool(row.get("oracle_direct_hit", False))
        if direct_hit and not product_geometry_present:
            downstream_counts["direct_hit_product_omission"] += 1
        elif not direct_hit and product_geometry_present:
            downstream_counts["direct_absence_product_commission"] += 1
        else:
            downstream_counts["direct_product_presence_agreement"] += 1
        if product_valid and not paint_valid:
            downstream_counts["product_present_final_paint_omission"] += 1
        elif paint_valid:
            downstream_counts["final_paint_observed"] += 1
        if bool(row.get("oracle_selected_depth_abs_delta_valid", False)):
            delta = float(row.get("oracle_selected_depth_abs_delta", 0.0))
            if math.isfinite(delta):
                depth_deltas.append(delta)
        if classification not in {"identity_agreement", "absence_agreement"}:
            source = row.get("probe_candidate_source_probe")
            generation = row.get("probe_candidate_source_generation")
            source_attribution[
                f"{classification}|probe={source}|generation={generation}"
            ] += 1
            if len(examples) < details_limit:
                examples.append({
                    "frame_id": int(row.get("frame_id", 0)),
                    "sample_index": int(row.get("sample_index", -1)),
                    "screen_cell": row.get("screen_cell", {}),
                    "classification": classification,
                    "production_identity": {
                        "owner": row.get("selected_surface_owner"),
                        "object": row.get("selected_surface_object"),
                        "primitive": row.get("selected_surface_primitive"),
                        "material": row.get("selected_surface_material"),
                    },
                    "direct_identity": row.get("oracle_identity", {}),
                    "source_probe": source,
                    "source_texel": row.get("probe_candidate_source_texel"),
                    "source_generation": generation,
                    "selected_depth": row.get("selected_surface_depth"),
                    "direct_depth": row.get("oracle_depth"),
                })
    sorted_depths = sorted(depth_deltas)

    def percentile(fraction: float) -> float:
        if not sorted_depths:
            return 0.0
        index = min(
            len(sorted_depths) - 1,
            max(0, int(math.ceil(fraction * len(sorted_depths))) - 1),
        )
        return float(sorted_depths[index])

    geometry_defect_rows = sum(
        int(classification_counts.get(name, 0))
        for name in ("ghost_or_commission", "omission", "identity_mismatch")
    )
    downstream_defect_rows = sum(
        int(downstream_counts.get(name, 0))
        for name in (
            "direct_hit_product_omission",
            "direct_absence_product_commission",
            "product_present_final_paint_omission",
        )
    )
    coverage = _same_frame_oracle_coverage(rows, runtime_receipt or {})
    spatial_rows_valid = (
        schema_rows == len(rows)
        and schema_rows > 0
        and structurally_valid_rows == schema_rows
    )
    return {
        "same_frame_oracle_schema": SAME_FRAME_ORACLE_SCHEMA,
        "same_frame_oracle_rows": schema_rows,
        # This legacy name now means exhaustive subcell coverage. Sparse row
        # schema validity is exposed separately as spatial_rows_valid so no
        # consumer can promote emitted-row validity to full-grid evidence.
        "same_frame_oracle_coverage_complete": bool(
            coverage.get("same_frame_oracle_exhaustive_frame_coverage", False)
        ),
        "same_frame_oracle_spatial_rows_valid": spatial_rows_valid,
        "same_frame_oracle_structurally_valid_rows": structurally_valid_rows,
        "same_frame_oracle_structurally_valid": (
            schema_rows == len(rows)
            and schema_rows > 0
            and structurally_valid_rows == schema_rows
        ),
        "same_frame_oracle_query_invalid_rows": query_invalid_rows,
        "same_frame_oracle_classification_counts": dict(
            sorted(classification_counts.items())
        ),
        "same_frame_oracle_geometry_defect_rows": geometry_defect_rows,
        "same_frame_oracle_downstream_counts": dict(sorted(downstream_counts.items())),
        "same_frame_oracle_downstream_defect_rows": downstream_defect_rows,
        "same_frame_oracle_source_attribution": dict(sorted(source_attribution.items())),
        "same_frame_oracle_depth_delta_count": len(sorted_depths),
        "same_frame_oracle_depth_delta_median": percentile(0.5),
        "same_frame_oracle_depth_delta_p90": percentile(0.9),
        "same_frame_oracle_depth_delta_max": (
            float(sorted_depths[-1]) if sorted_depths else 0.0
        ),
        "same_frame_oracle_examples": examples,
        **coverage,
    }


def _oracle_identity_tuple(value: object) -> tuple[int, int, int, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        return (
            int(value["owner_kind"]),
            int(value["object_id"]),
            int(value["primitive_id"]),
            int(value.get("material", value.get("material_id"))),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _selected_identity_tuple(row: dict) -> tuple[int, int, int, int] | None:
    try:
        return (
            int(row["selected_surface_owner"]),
            int(row["selected_surface_object"]),
            int(row["selected_surface_primitive"]),
            int(row["selected_surface_material"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _candidate_identity_tuple(row: dict) -> tuple[int, int, int, int] | None:
    if not bool(row.get("probe_candidate_valid", False)):
        return None
    return _oracle_identity_tuple(row.get("probe_candidate_identity"))


def _quantized_anchor(value: object, digits: int = 4) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        anchor = tuple(round(float(value[index]), digits) for index in range(3))
    except (TypeError, ValueError):
        return None
    return anchor if all(math.isfinite(component) for component in anchor) else None


def _exact_oracle_anchor(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        anchor = tuple(float(value[index]) for index in range(3))
    except (TypeError, ValueError):
        return None
    return anchor if all(math.isfinite(component) for component in anchor) else None


def _oracle_reference_key(row: dict) -> tuple | None:
    """Return the exact SceneQuery identity and published stable anchor.

    The current ABI has no categorical reference-anchor ID. Preserve the three
    published finite coordinates exactly instead of rounding them: rounding
    merged nearby anchors in the historical counter. Exact-anchor splits are
    reported as unmatched coverage rather than silently joined.
    """
    identity = _oracle_identity_tuple(row.get("oracle_identity"))
    anchor = _exact_oracle_anchor(row.get("oracle_stable_world_anchor"))
    if (
        not bool(row.get("oracle_direct_hit", False))
        or identity is None
        or anchor is None
    ):
        return None
    return (*identity, *anchor)


def _two_positive_ints(value: object) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return None
    pair = int(value[0]), int(value[1])
    return pair if pair[0] > 0 and pair[1] > 0 else None


def _terminal_projection_contract(rows: list[dict]) -> dict:
    """Return one exact PixelCamera projection contract for a captured frame."""
    if not rows:
        return {"valid": False, "reason": "empty_frame"}
    traces = [row.get("frame_trace") for row in rows]
    if not all(isinstance(trace, dict) for trace in traces):
        return {"valid": False, "reason": "missing_frame_trace"}
    trace = traces[0]
    matrix = trace.get("world_to_clip_matrix")
    if (
        not isinstance(matrix, (list, tuple))
        or len(matrix) != 16
        or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in matrix
        )
    ):
        return {"valid": False, "reason": "invalid_world_to_clip_matrix"}
    target_size = _two_positive_ints(trace.get("target_size"))
    cell_size = _two_positive_ints(trace.get("cell_size"))
    ansi_size = _two_positive_ints(trace.get("semantic_ansi_size"))
    if target_size is None or cell_size is None or ansi_size is None:
        return {"valid": False, "reason": "invalid_pixel_partition"}
    required_ints = (
        "frame_index",
        "sequence_frame",
        "authority_server_tick",
        "camera_hash",
        "scene_revision",
        "view_revision",
        "dynamic_revision",
    )
    metadata: dict[str, int] = {}
    for field in required_ints:
        value = trace.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return {"valid": False, "reason": f"invalid_{field}"}
        metadata[field] = int(value)
    if trace.get("readback_flip_y") is not False:
        return {"valid": False, "reason": "unsupported_row_origin"}
    signature = {
        "world_to_clip_matrix": [float(value) for value in matrix],
        "target_size": list(target_size),
        "cell_size": list(cell_size),
        "semantic_ansi_size": list(ansi_size),
        "readback_flip_y": False,
        **metadata,
    }
    for other in traces[1:]:
        if any(other.get(field) != trace.get(field) for field in signature):
            return {"valid": False, "reason": "conflicting_frame_trace"}
    return {"valid": True, **signature}


def _project_terminal_world_point(
    world: tuple[float, float, float], contract: dict
) -> tuple[float, float] | None:
    """Project one world point with Godot's column-major Projection layout."""
    matrix = contract.get("world_to_clip_matrix")
    target = contract.get("target_size")
    if not isinstance(matrix, list) or len(matrix) != 16:
        return None
    if not isinstance(target, list) or len(target) != 2:
        return None
    x, y, z = world
    clip_x = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]
    clip_y = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]
    clip_w = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15]
    if not all(math.isfinite(value) for value in (clip_x, clip_y, clip_w)):
        return None
    if clip_w <= 1.0e-6:
        return None
    px = (
        (clip_x / clip_w * 0.5 + 0.5) * float(target[0]),
        (clip_y / clip_w * 0.5 + 0.5) * float(target[1]),
    )
    if not all(math.isfinite(value) for value in px):
        return None
    return px


def _terminal_expected_sample_index(
    pixel: tuple[float, float], contract: dict
) -> int | None:
    target = contract.get("target_size")
    cell_size = contract.get("cell_size")
    ansi_size = contract.get("semantic_ansi_size")
    if not all(isinstance(value, list) and len(value) == 2 for value in (target, cell_size, ansi_size)):
        return None
    px, py = pixel
    if px < 0.0 or py < 0.0 or px >= target[0] or py >= target[1]:
        return None
    cell_x = min(int(px) // cell_size[0], ansi_size[0] - 1)
    cell_y = min(int(py) // cell_size[1], ansi_size[1] - 1)
    origin_x = cell_x * cell_size[0]
    origin_y = cell_y * cell_size[1]
    span_x = min(cell_size[0], target[0] - origin_x)
    span_y = min(cell_size[1], target[1] - origin_y)
    slot_x = 1 if int(px) - origin_x >= (span_x + 1) // 2 else 0
    slot_y = 1 if int(py) - origin_y >= (span_y + 1) // 2 else 0
    return _terminal_sample_index_for_cell(
        cell_x,
        cell_y,
        slot_x + slot_y * 2,
        contract,
    )


def _terminal_sample_index_for_cell(
    cell_x: int,
    cell_y: int,
    slot: int,
    contract: dict,
) -> int | None:
    """Encode one ANSI quarter in the producer's row-major subcell ABI.

    b61, b64, Product, and the sparse recorder index a 2xANSI grid by
    subcell row.  The four quarters of an ANSI cell are therefore not four
    contiguous records once the grid is wider than one cell.
    """
    ansi_size = contract.get("semantic_ansi_size")
    if (
        not isinstance(ansi_size, list)
        or len(ansi_size) != 2
        or not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in ansi_size
        )
        or ansi_size[0] <= 0
        or ansi_size[1] <= 0
        or not isinstance(cell_x, int)
        or isinstance(cell_x, bool)
        or not isinstance(cell_y, int)
        or isinstance(cell_y, bool)
        or not isinstance(slot, int)
        or isinstance(slot, bool)
        or not 0 <= cell_x < ansi_size[0]
        or not 0 <= cell_y < ansi_size[1]
        or slot not in {0, 1, 2, 3}
    ):
        return None
    subcell_width = ansi_size[0] * 2
    subcell_x = cell_x * 2 + (slot & 1)
    subcell_y = cell_y * 2 + (slot >> 1)
    return subcell_y * subcell_width + subcell_x


def _terminal_cell_from_sample_index(
    sample_index: int, contract: dict
) -> tuple[int, int] | None:
    """Decode the ANSI-cell address owned by one exhaustive subcell index."""
    ansi_size = contract.get("semantic_ansi_size")
    if (
        not isinstance(sample_index, int)
        or isinstance(sample_index, bool)
        or sample_index < 0
        or not isinstance(ansi_size, list)
        or len(ansi_size) != 2
        or ansi_size[0] <= 0
        or ansi_size[1] <= 0
    ):
        return None
    subcell_width = ansi_size[0] * 2
    subcell_height = ansi_size[1] * 2
    if sample_index >= subcell_width * subcell_height:
        return None
    subcell_x = sample_index % subcell_width
    subcell_y = sample_index // subcell_width
    return subcell_x // 2, subcell_y // 2


def _terminal_occurrence(
    row: dict,
    target_size: list[int],
    cell_size: list[int] | None = None,
    cell: tuple[int, int] | None = None,
) -> dict:
    if row.get("final_paint_terminal_occurrence_valid") is not True:
        reasons = []
        count = row.get("final_paint_terminal_mask_ink_count")
        if isinstance(count, int) and not isinstance(count, bool) and count <= 0:
            reasons.append("no_terminal_ink")
        if row.get("final_paint_slot_actor_overlay") is True:
            reasons.append("actor_overlay")
        mask_state = row.get("final_paint_mask_state")
        if isinstance(mask_state, str) and mask_state:
            reasons.append(f"mask_state:{mask_state}")
        expected_count = row.get("final_paint_expected_atlas_mask_pixel_count")
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and isinstance(expected_count, int)
            and not isinstance(expected_count, bool)
            and count != expected_count
        ):
            reasons.append("atlas_ink_count_mismatch")
        expected_digest = row.get("final_paint_expected_atlas_mask_hash")
        digest = row.get("final_paint_terminal_mask_digest")
        if (
            isinstance(digest, int)
            and not isinstance(digest, bool)
            and isinstance(expected_digest, int)
            and not isinstance(expected_digest, bool)
            and digest != expected_digest
        ):
            reasons.append("atlas_mask_digest_mismatch")
        return {
            "valid": False,
            "reason": "occurrence_not_valid",
            "reasons": reasons or ["host_validity_rejected_unclassified"],
        }
    values = {field: row.get(field) for field in TERMINAL_OCCURRENCE_FIELDS}
    numeric_fields = TERMINAL_OCCURRENCE_FIELDS[:-1]
    if not all(
        isinstance(values[field], int)
        and not isinstance(values[field], bool)
        and values[field] >= 0
        for field in numeric_fields
    ):
        return {"valid": False, "reason": "occurrence_non_integer"}
    count = int(values["final_paint_terminal_mask_ink_count"])
    min_x = int(values["final_paint_terminal_mask_min_x"])
    min_y = int(values["final_paint_terminal_mask_min_y"])
    max_x = int(values["final_paint_terminal_mask_max_x"])
    max_y = int(values["final_paint_terminal_mask_max_y"])
    sum_x = int(values["final_paint_terminal_mask_sum_x"])
    sum_y = int(values["final_paint_terminal_mask_sum_y"])
    if count <= 0 or min_x > max_x or min_y > max_y:
        return {"valid": False, "reason": "occurrence_empty_or_inverted"}
    if max_x >= target_size[0] or max_y >= target_size[1]:
        return {"valid": False, "reason": "occurrence_out_of_bounds"}
    if (
        isinstance(cell_size, list)
        and len(cell_size) == 2
        and isinstance(cell, tuple)
        and len(cell) == 2
    ):
        origin_x = cell[0] * cell_size[0]
        origin_y = cell[1] * cell_size[1]
        span_x = min(cell_size[0], target_size[0] - origin_x)
        span_y = min(cell_size[1], target_size[1] - origin_y)
        if (
            min_x < origin_x
            or max_x >= origin_x + span_x
            or min_y < origin_y
            or max_y >= origin_y + span_y
        ):
            return {"valid": False, "reason": "occurrence_outside_declared_cell"}
    if not (count * min_x <= sum_x <= count * max_x):
        return {"valid": False, "reason": "occurrence_sum_x_invalid"}
    if not (count * min_y <= sum_y <= count * max_y):
        return {"valid": False, "reason": "occurrence_sum_y_invalid"}
    return {
        "valid": True,
        "centroid": (sum_x / float(count) + 0.5, sum_y / float(count) + 0.5),
        "bounds": (min_x, min_y, max_x + 1, max_y + 1),
        "count": count,
        "digest": int(values["final_paint_terminal_mask_digest"]),
    }


def _terminal_occurrence_morphology(occurrence: dict) -> tuple | None:
    """Return position-independent mask facts for displacement attribution."""
    bounds = occurrence.get("bounds")
    if not isinstance(bounds, tuple) or len(bounds) != 4:
        return None
    return (
        occurrence.get("count"),
        bounds[2] - bounds[0],
        bounds[3] - bounds[1],
        occurrence.get("digest"),
    )


def _terminal_occurrence_is_untrackable_blank(occurrence: dict) -> bool:
    """Return whether b43 validly produced no glyph ink to track.

    A Product winner can deliberately select a blank atlas glyph. Such a cell
    cannot seed a terminal trajectory, but it is not corrupt evidence. Any
    additional failure reason keeps the cell red instead of silently excluding
    an incomplete or mismatched non-blank mask.
    """
    if occurrence.get("valid"):
        return False
    reasons = occurrence.get("reasons")
    if not isinstance(reasons, list) or "no_terminal_ink" not in reasons:
        return False
    permitted = {
        "no_terminal_ink",
        "mask_state:final_mask_complete_atlas_mask",
    }
    return set(reasons) <= permitted


def _terminal_cell_is_untrackable_blank(
    cell_rows: list[dict], occurrences: list[dict]
) -> bool:
    """Exclude only a coherent four-slot blank Product glyph.

    A zero-ink occurrence is legitimate only when both b55 and b43 identify
    the same blank glyph in every subcell.  This prevents an incomplete or
    nonblank terminal mask from inheriting the blank-output exclusion merely
    because its reduction also produced zero ink.
    """
    if len(cell_rows) != 4 or len(occurrences) != 4:
        return False
    blank_glyphs: list[int] = []
    for row in cell_rows:
        product_glyph = row.get("final_paint_glyph")
        slot_glyph = row.get("final_paint_slot_glyph")
        if (
            not isinstance(product_glyph, int)
            or isinstance(product_glyph, bool)
            or not isinstance(slot_glyph, int)
            or isinstance(slot_glyph, bool)
            or product_glyph != slot_glyph
            or product_glyph not in {0, 32}
        ):
            return False
        blank_glyphs.append(product_glyph)
    return len(set(blank_glyphs)) == 1 and all(
        _terminal_occurrence_is_untrackable_blank(occurrence)
        for occurrence in occurrences
    )


def _terminal_tracking_token(winner: dict) -> str | None:
    if not winner.get("available"):
        return None
    # The elected categorical key is the only cross-frame surface identity.
    # Source kind is provenance: a later b43 reduction may elect the same
    # surface through center-terrain rather than the frame-A quarter slot.
    # Keep source-kind consistency in the four-slot admission check below.
    fields = {"key": winner.get("key")}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def _terminal_frame_observations(rows: list[dict], contract: dict) -> dict:
    """Collect one native whole-mask occurrence per exact b43 elected token."""
    groups: dict[tuple, list[dict]] = {}
    invalid: Counter = Counter()
    ansi_size = contract.get("semantic_ansi_size")
    ansi_width = int(ansi_size[0])
    ansi_height = int(ansi_size[1])
    for row in rows:
        cell_value = row.get("screen_cell")
        if isinstance(cell_value, dict):
            coordinates = (cell_value.get("x"), cell_value.get("y"))
        elif isinstance(cell_value, (list, tuple)) and len(cell_value) == 2:
            coordinates = (cell_value[0], cell_value[1])
        else:
            coordinates = ()
        if (
            len(coordinates) != 2
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in coordinates
            )
        ):
            invalid["screen_cell_invalid"] += 1
            continue
        cell = int(coordinates[0]), int(coordinates[1])
        slot = row.get("subcell_slot")
        sample_index = row.get("sample_index")
        expected_sample_index = _terminal_sample_index_for_cell(
            cell[0],
            cell[1],
            int(slot) if isinstance(slot, int) else -1,
            contract,
        )
        if not (0 <= cell[0] < ansi_width and 0 <= cell[1] < ansi_height):
            invalid["screen_cell_out_of_bounds"] += 1
            continue
        if (
            not isinstance(slot, int)
            or isinstance(slot, bool)
            or slot not in {0, 1, 2, 3}
            or not isinstance(sample_index, int)
            or isinstance(sample_index, bool)
            or expected_sample_index is None
            or sample_index != expected_sample_index
        ):
            invalid["screen_cell_sample_slot_mismatch"] += 1
            continue
        groups.setdefault(cell, []).append(row)
    observations: dict[str, list[dict]] = {}
    excluded: Counter = Counter()
    for cell, cell_rows in groups.items():
        if len(cell_rows) != 4:
            invalid["incomplete_cell"] += 1
            continue
        slots = {row.get("subcell_slot") for row in cell_rows}
        if slots != {0, 1, 2, 3}:
            invalid["cell_slot_coverage_invalid"] += 1
            continue
        cell_rows.sort(key=lambda row: int(row.get("subcell_slot", -1)))
        winners = [b43_elected_identity_witness(row) for row in cell_rows]
        if not all(winner.get("available") for winner in winners):
            # The b43 elected identity is a required capture ABI. Blank glyphs
            # may still be excluded later as coherent blank output, but a
            # missing/invalid identity is evidence invalidity, not an ordinary
            # non-trackable scene cell.
            invalid["b43_elected_identity_missing_or_invalid"] += 1
            continue
        token = _terminal_tracking_token(winners[0])
        cell_winner_signature = (
            winners[0].get("glyph"),
            winners[0].get("priority"),
            winners[0].get("slot"),
            winners[0].get("source_kind"),
        )
        if token is None or any(
            _terminal_tracking_token(winner) != token
            or (
                winner.get("glyph"),
                winner.get("priority"),
                winner.get("slot"),
                winner.get("source_kind"),
            ) != cell_winner_signature
            for winner in winners[1:]
        ):
            invalid["winner_not_cell_consistent"] += 1
            continue
        if any(row.get("final_paint_slot_actor_overlay") is True for row in cell_rows):
            excluded["actor_overlay"] += 1
            continue
        occurrences = [
            _terminal_occurrence(
                row,
                contract["target_size"],
                contract.get("cell_size"),
                cell,
            )
            for row in cell_rows
        ]
        if not all(occurrence.get("valid") for occurrence in occurrences):
            if _terminal_cell_is_untrackable_blank(cell_rows, occurrences):
                excluded["blank_terminal_glyph"] += 1
                continue
            invalid["terminal_occurrence_invalid"] += 1
            # Preserve the exact failed invariant. A generic invalid count
            # cannot decide whether the next repair belongs to capture,
            # framebuffer bounds, mask completeness, or occurrence admission.
            reasons = {
                str(reason)
                for occurrence in occurrences
                if not occurrence.get("valid")
                for reason in occurrence.get(
                    "reasons", [occurrence.get("reason", "unknown")]
                )
            }
            for reason in reasons:
                invalid[f"terminal_occurrence_invalid:{reason}"] += 1
            continue
        first = occurrences[0]
        if any(occurrence != first for occurrence in occurrences[1:]):
            invalid["terminal_occurrence_not_cell_consistent"] += 1
            continue
        observations.setdefault(token, []).append({
            "cell": cell,
            "winner": winners[0],
            "occurrence": first,
            "rows": cell_rows,
        })
    return {
        "observations": observations,
        "invalid": dict(invalid),
        "excluded": dict(excluded),
    }


def summarize_terminal_expected_reprojection(
    frames: list[list[dict]], details_limit: int = 50
) -> dict:
    """Measure cross-frame terminal displacement for fixed frame-A world hits.

    This deliberately never uses a same-frame b86 projection as the actual
    output coordinate. The expected path is W_A through each later frame's
    exact matrix; the actual path is an independently found b43 whole-mask
    occurrence with the same exact Product winner token.
    """
    counts: Counter = Counter()
    invalid: Counter = Counter()
    excluded: Counter = Counter()
    examples: list[dict] = []
    residual_magnitudes: list[float] = []
    if len(frames) < 2:
        return {
            "valid": False,
            "baseline_calibrated": False,
            "counts": {},
            "invalid": {"insufficient_frames": 1},
            "excluded": {},
            "examples": [],
        }
    contracts = [_terminal_projection_contract(frame) for frame in frames]
    for contract in contracts:
        if not contract.get("valid"):
            invalid[str(contract.get("reason", "invalid_projection_contract"))] += 1
    if invalid:
        return {
            "valid": False,
            "baseline_calibrated": False,
            "counts": {},
            "invalid": dict(invalid),
            "excluded": {},
            "examples": [],
        }
    sequences = [contract["sequence_frame"] for contract in contracts]
    if sequences != list(range(sequences[0], sequences[0] + len(sequences))):
        invalid["non_contiguous_sequence_frames"] += 1
    renderer_frames = [contract["frame_index"] for contract in contracts]
    if renderer_frames != list(
        range(renderer_frames[0], renderer_frames[0] + len(renderer_frames))
    ):
        invalid["non_contiguous_renderer_frames"] += 1
    authority_ticks = [contract["authority_server_tick"] for contract in contracts]
    if any(later < earlier for earlier, later in zip(authority_ticks, authority_ticks[1:])):
        invalid["non_monotonic_authority_ticks"] += 1
    frame_observations = [
        _terminal_frame_observations(frame, contract)
        for frame, contract in zip(frames, contracts)
    ]
    for observation in frame_observations:
        invalid.update(observation["invalid"])
        excluded.update(observation["excluded"])
    if invalid:
        return {
            "valid": False,
            "baseline_calibrated": False,
            "counts": {},
            "invalid": dict(invalid),
            "excluded": dict(excluded),
            "examples": [],
        }

    frame_rows_by_sample = [
        {int(row.get("sample_index", -1)): row for row in frame}
        for frame in frames
    ]
    source_tokens = set(frame_observations[0]["observations"])
    counts["terminal_observation_cells_frame_a"] = sum(
        len(observations)
        for observations in frame_observations[0]["observations"].values()
    )
    counts["terminal_observation_tokens_frame_a"] = len(source_tokens)
    counts["terminal_observation_tokens_by_frame"] = [
        len(observation["observations"]) for observation in frame_observations
    ]
    for frame_offset, observation in enumerate(frame_observations[1:], start=1):
        later_tokens = set(observation["observations"])
        counts[f"later_unmatched_terminal_tokens_frame_{frame_offset}"] = len(
            later_tokens - source_tokens
        )
    for token, source_occurrences in frame_observations[0]["observations"].items():
        if len(source_occurrences) != 1:
            invalid["source_terminal_duplicate_or_ambiguous"] += 1
            continue
        source = source_occurrences[0]
        winner = source["winner"]
        if winner.get("source_kind") != "quarter_slot":
            excluded["center_terrain_without_exact_b86_source"] += 1
            continue
        winner_slot = int(winner.get("slot", -1))
        source_row = next(
            (row for row in source["rows"] if int(row.get("subcell_slot", -1)) == winner_slot),
            None,
        )
        if source_row is None:
            invalid["source_winner_slot_missing"] += 1
            continue
        reference_key = _oracle_reference_key(source_row)
        world = _exact_oracle_anchor(source_row.get("oracle_world_position"))
        if (
            reference_key is None
            or world is None
            or source_row.get("oracle_query_valid") is not True
            or source_row.get("oracle_identity_agrees_with_selected_surface") is not True
        ):
            excluded["source_reference_not_eligible"] += 1
            continue
        # The b43 elected key is only a categorical bridge to the independently
        # scanned later occurrence. It does not claim ownership of W_A and is
        # therefore never compared to the oracle point bits. This avoids the
        # prior false exclusion for analytic water and semantic cell anchors.
        expected_a = _project_terminal_world_point(world, contracts[0])
        if expected_a is None:
            invalid["source_projection_invalid"] += 1
            continue
        expected_sample_a = _terminal_expected_sample_index(expected_a, contracts[0])
        expected_cell_a = (
            _terminal_cell_from_sample_index(expected_sample_a, contracts[0])
            if expected_sample_a is not None
            else None
        )
        if expected_cell_a is None:
            invalid["source_expected_cell_invalid"] += 1
            continue
        actual_a = source["occurrence"]["centroid"]
        actual_cell_a = source["cell"]
        source_morphology = _terminal_occurrence_morphology(source["occurrence"])
        if source_morphology is None:
            invalid["source_terminal_morphology_invalid"] += 1
            continue
        counts["eligible_source_features"] += 1
        counts["comparable_expected_cells"] += 1
        if actual_cell_a == expected_cell_a:
            counts["expected_cell_matches"] += 1
        else:
            counts["expected_cell_mismatches"] += 1
        track_examples = []
        for frame_offset in range(1, len(frames)):
            contract = contracts[frame_offset]
            expected_b = _project_terminal_world_point(world, contract)
            if expected_b is None:
                excluded["expected_offscreen_or_behind_camera"] += 1
                continue
            expected_sample = _terminal_expected_sample_index(expected_b, contract)
            if expected_sample is None:
                excluded["expected_offscreen_or_unaddressable"] += 1
                continue
            reference_b = frame_rows_by_sample[frame_offset].get(expected_sample)
            if reference_b is None:
                invalid["expected_reference_sample_missing"] += 1
                continue
            if reference_b.get("oracle_query_valid") is not True:
                invalid["expected_reference_query_invalid"] += 1
                continue
            if reference_b.get("oracle_direct_hit") is not True:
                excluded["reference_occluded_or_disoccluded"] += 1
                continue
            if _oracle_reference_key(reference_b) != reference_key:
                excluded["reference_identity_changed"] += 1
                continue
            matches = frame_observations[frame_offset]["observations"].get(token, [])
            if not matches:
                invalid["terminal_omission"] += 1
                continue
            if len(matches) != 1:
                invalid["terminal_duplicate_or_ambiguous"] += 1
                continue
            match = matches[0]
            expected_cell = _terminal_cell_from_sample_index(expected_sample, contract)
            if expected_cell is None:
                invalid["expected_cell_invalid"] += 1
                continue
            actual_cell = match["cell"]
            counts["comparable_expected_cells"] += 1
            if actual_cell == expected_cell:
                counts["expected_cell_matches"] += 1
            else:
                counts["expected_cell_mismatches"] += 1
            actual_b = match["occurrence"]["centroid"]
            morphology_b = _terminal_occurrence_morphology(match["occurrence"])
            if morphology_b is None:
                invalid["terminal_morphology_invalid"] += 1
                continue
            if morphology_b != source_morphology:
                counts["mask_morphology_changed_displacements"] += 1
                excluded["mask_morphology_changed"] += 1
                if len(examples) < details_limit:
                    track_examples.append({
                        "frame_offset": frame_offset,
                        "expected": expected_b,
                        "actual": actual_b,
                        "expected_sample_index": expected_sample,
                        "expected_cell": expected_cell,
                        "actual_cell": actual_cell,
                        "mask_morphology_changed": True,
                        "source_morphology": source_morphology,
                        "actual_morphology": morphology_b,
                    })
                continue
            residual = (
                (actual_b[0] - actual_a[0]) - (expected_b[0] - expected_a[0]),
                (actual_b[1] - actual_a[1]) - (expected_b[1] - expected_a[1]),
            )
            magnitude = math.hypot(*residual)
            residual_magnitudes.append(magnitude)
            counts["comparable_terminal_displacements"] += 1
            track_examples.append({
                "frame_offset": frame_offset,
                "expected": expected_b,
                "actual": actual_b,
                "residual": residual,
                "residual_magnitude": magnitude,
                "expected_sample_index": expected_sample,
                "expected_cell": expected_cell,
                "actual_cell": actual_cell,
                "mask_morphology_changed": False,
            })
        if track_examples and len(examples) < details_limit:
            examples.append({
                "token": token,
                "reference_key": reference_key,
                "world_a": world,
                "expected_a": expected_a,
                "actual_a": actual_a,
                "trajectory": track_examples,
            })
    return {
        # Evidence validity is owned by the threshold-free categorical
        # correspondence. Centroid displacement is optional diagnostics and
        # cannot make a structurally complete exact-cell run invalid merely
        # because morphology changed; no centroid threshold is an acceptance
        # requirement.
        "valid": (
            not invalid
            and int(counts.get("eligible_source_features", 0)) > 0
            and int(counts.get("comparable_expected_cells", 0)) > 0
        ),
        "baseline_calibrated": False,
        "acceptance_threshold_authorized": False,
        # Whole-cell presentation is discrete. Exact expected ANSI-cell
        # equality is the sole threshold-free positional invariant. Centroid
        # residual remains descriptive telemetry and has no acceptance law.
        "expected_cell_address_pass": (
            int(counts.get("comparable_expected_cells", 0)) > 0
            and int(counts.get("expected_cell_mismatches", 0)) == 0
        ),
        "counts": dict(counts),
        "invalid": dict(invalid),
        "excluded": dict(excluded),
        "mean_residual_px": (
            sum(residual_magnitudes) / len(residual_magnitudes)
            if residual_magnitudes else 0.0
        ),
        "rms_residual_px": (
            math.sqrt(
                sum(value * value for value in residual_magnitudes)
                / len(residual_magnitudes)
            )
            if residual_magnitudes else 0.0
        ),
        # The acceptance statistic covers every comparable displacement. The
        # bounded examples list is diagnostic output and must never truncate
        # the measured population.
        "max_residual_px": max(residual_magnitudes, default=0.0),
        "examples": examples,
    }


def _oracle_subcell_xy(row: dict, subcell_width: int) -> tuple[int, int] | None:
    try:
        sample_index = int(row["sample_index"])
    except (KeyError, TypeError, ValueError):
        return None
    if sample_index < 0 or subcell_width <= 0:
        return None
    return sample_index % subcell_width, sample_index // subcell_width


def _oracle_mismatch_classification(
    expected: tuple[int, int, int, int], actual: tuple[int, int, int, int]
) -> str:
    if expected[0] != actual[0]:
        return "owner_substitution"
    if expected[1] != actual[1]:
        return "same_owner_different_object"
    if expected[3] != actual[3]:
        # Material is a semantic surface boundary even when the sampled
        # primitive changed too. Checking it before primitive identity keeps a
        # terrain-to-water-family/material substitution out of the finite
        # same-surface resampling bucket.
        return "same_object_different_material"
    if expected[2] != actual[2]:
        return "same_object_same_material_different_primitive_sample"
    return "normalized_identity_agreement"


def _finite_vec3(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        vector = tuple(float(value[index]) for index in range(3))
    except (TypeError, ValueError):
        return None
    return vector if all(math.isfinite(component) for component in vector) else None


def _projected_quad_subcell_coverage(row: dict) -> dict:
    """Mirror the capture shader's six-vertex current-subcell coverage test."""
    if not bool(row.get("probe_reconstructed_clip_valid", False)):
        return {"available": False, "covers": False, "reason": "projected_quad_invalid"}
    trace = _same_frame_oracle_trace(row)
    viewport = trace.get("viewport", {}) if isinstance(trace, dict) else {}
    ansi_size = viewport.get("semantic_ansi_size") \
        if isinstance(viewport, dict) else None
    if not isinstance(ansi_size, (list, tuple)) or len(ansi_size) < 2:
        return {"available": False, "covers": False, "reason": "ansi_extent_missing"}
    try:
        subcell_width = int(ansi_size[0]) * 2
        subcell_height = int(ansi_size[1]) * 2
        sample_index = int(row["sample_index"])
    except (KeyError, TypeError, ValueError):
        return {"available": False, "covers": False, "reason": "sample_index_invalid"}
    expected_samples = subcell_width * subcell_height
    if (
        subcell_width <= 0
        or subcell_height <= 0
        or sample_index < 0
        or sample_index >= expected_samples
    ):
        return {"available": False, "covers": False, "reason": "sample_index_out_of_range"}

    clip_values = row.get("probe_reconstructed_clip_corners")
    if not isinstance(clip_values, list) or len(clip_values) != 4:
        return {"available": False, "covers": False, "reason": "clip_corners_missing"}
    projected: list[tuple[float, float]] = []
    for value in clip_values:
        if not isinstance(value, (list, tuple)) or len(value) < 4:
            return {"available": False, "covers": False, "reason": "clip_corner_invalid"}
        try:
            clip = tuple(float(value[index]) for index in range(4))
        except (TypeError, ValueError):
            return {"available": False, "covers": False, "reason": "clip_corner_invalid"}
        if not all(math.isfinite(component) for component in clip) or clip[3] <= 0.0:
            return {"available": False, "covers": False, "reason": "clip_corner_invalid"}
        projected.append((clip[0] / clip[3], clip[1] / clip[3]))

    subcell_x = sample_index % subcell_width
    subcell_y = sample_index // subcell_width
    point = (
        (2.0 * (float(subcell_x) + 0.5) / float(subcell_width)) - 1.0,
        (2.0 * (float(subcell_y) + 0.5) / float(subcell_height)) - 1.0,
    )

    def edge(
        a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]
    ) -> float:
        return (p[0] - a[0]) * (b[1] - a[1]) \
            - (p[1] - a[1]) * (b[0] - a[0])

    def in_triangle(
        p: tuple[float, float],
        a: tuple[float, float],
        b: tuple[float, float],
        c: tuple[float, float],
    ) -> bool:
        values = (edge(a, b, p), edge(b, c, p), edge(c, a, p))
        epsilon = 1.0e-6
        has_negative = any(value < -epsilon for value in values)
        has_positive = any(value > epsilon for value in values)
        return not (has_negative and has_positive)

    # Match the live shader's two triangles: 00,10,11 and 00,11,01.
    covers = in_triangle(point, projected[0], projected[1], projected[3]) \
        or in_triangle(point, projected[0], projected[3], projected[2])
    return {
        "available": True,
        "covers": covers,
        "reason": "projected_quad_covers_current_subcell"
            if covers else "current_subcell_outside_projected_quad",
        "sample_ndc": [point[0], point[1]],
        "projected_ndc_corners": [list(value) for value in projected],
    }


def _vec3_distance(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _finite_splat_admissibility(row: dict) -> dict:
    """Grade a finite splat without pretending it is an exact current-ray hit.

    A final-pixel texel splat represents one captured surface sample over a
    finite reconstructed quad. Exact primitive equality against the direct
    current-camera ray is therefore diagnostic evidence, not by itself a
    correctness law. The admissible channel remains mechanical and fail-closed:
    the independent source-ray replay must reproduce the captured identity;
    reconstruction must reproduce that source point; projected corners must be
    finite; the actual fragment witness must contain the complete pre-pack
    source tuple; and its hardware depth must equal the winning MRT depth. The
    world-envelope residual is retained as a secondary diagnostic, never as a
    replacement for projected coverage and exact contender provenance.
    """
    expected = _oracle_identity_tuple(row.get("oracle_identity"))
    actual = _selected_identity_tuple(row)
    if (
        str(row.get("oracle_classification", "")) != "identity_mismatch"
        or expected is None
        or actual is None
        or _oracle_mismatch_classification(expected, actual)
        != "same_object_same_material_different_primitive_sample"
    ):
        return {"applicable": False, "admissible": False, "reason": "not_applicable"}

    if (
        not bool(row.get("probe_source_capture_band_map_valid", False))
        or int(row.get("probe_source_capture_join_flags", 0)) != 0x7F
    ):
        return {"applicable": True, "admissible": False, "reason": "source_physical_join_invalid"}
    if bool(row.get("probe_raster_contender_overflow", True)):
        return {"applicable": True, "admissible": False, "reason": "raster_witness_overflow"}
    if int(row.get("probe_raster_selected_identity_contender_count", 0)) <= 0:
        return {"applicable": True, "admissible": False, "reason": "selected_fragment_missing"}

    source_capture = _oracle_identity_tuple(
        row.get("probe_source_capture_identity")
    ) if bool(row.get("probe_source_capture_valid", False)) else None
    source_oracle = _oracle_identity_tuple(
        row.get("probe_source_oracle_identity")
    ) if bool(row.get("probe_source_oracle_direct_hit", False)) else None
    prepack = _oracle_identity_tuple(row.get("probe_prepack_identity")) \
        if bool(row.get("probe_prepack_valid", False)) else None
    candidate = _candidate_identity_tuple(row)
    if not bool(row.get("probe_source_oracle_query_valid", False)):
        return {"applicable": True, "admissible": False, "reason": "source_oracle_query_invalid"}
    if source_capture is None or source_oracle is None:
        return {"applicable": True, "admissible": False, "reason": "source_identity_missing"}
    if source_capture != source_oracle:
        return {"applicable": True, "admissible": False, "reason": "capture_identity_disagrees_with_source_ray"}
    if prepack is None or prepack != source_capture:
        return {"applicable": True, "admissible": False, "reason": "raster_identity_disagrees_with_capture"}
    if candidate is None or candidate != prepack or actual != candidate:
        return {"applicable": True, "admissible": False, "reason": "candidate_or_selection_identity_changed"}

    clip_corners_value = row.get("probe_reconstructed_clip_corners")
    clip_corners = []
    if isinstance(clip_corners_value, list):
        for value in clip_corners_value:
            if not isinstance(value, (list, tuple)) or len(value) < 4:
                continue
            try:
                corner = tuple(float(value[index]) for index in range(4))
            except (TypeError, ValueError):
                continue
            if all(math.isfinite(component) for component in corner):
                clip_corners.append(corner)
    if not bool(row.get("probe_reconstructed_clip_valid", False)) \
            or len(clip_corners) != 4 \
            or any(corner[3] <= 0.0 for corner in clip_corners):
        return {"applicable": True, "admissible": False, "reason": "projected_quad_invalid"}
    projected_coverage = _projected_quad_subcell_coverage(row)
    if not bool(projected_coverage.get("available", False)):
        return {
            "applicable": True,
            "admissible": False,
            "reason": "projected_subcell_coverage_unavailable",
            "projected_coverage": projected_coverage,
        }
    if not bool(projected_coverage.get("covers", False)):
        return {
            "applicable": True,
            "admissible": False,
            "reason": "current_subcell_outside_projected_quad",
            "projected_coverage": projected_coverage,
        }

    if int(row.get("probe_raster_candidate_source_contender_count", 0)) <= 0:
        return {"applicable": True, "admissible": False, "reason": "candidate_source_fragment_missing"}
    nearest_source = row.get("probe_raster_candidate_source_nearest_source")
    prepack_provenance = row.get("probe_prepack_provenance")
    if not isinstance(nearest_source, dict) or not isinstance(prepack_provenance, list) \
            or len(prepack_provenance) < 2:
        return {"applicable": True, "admissible": False, "reason": "candidate_source_tuple_missing"}
    try:
        candidate_source_tuple_agrees = (
            int(nearest_source.get("physical_layer", -1))
                == int(row.get("probe_source_capture_physical_layer", -2))
            and int(nearest_source.get("packed_probe_generation", -1))
                == int(prepack_provenance[0])
            and int(nearest_source.get("source_texel", -1))
                == int(prepack_provenance[1])
        )
    except (TypeError, ValueError):
        candidate_source_tuple_agrees = False
    if not candidate_source_tuple_agrees:
        return {"applicable": True, "admissible": False, "reason": "candidate_source_tuple_disagrees"}

    try:
        winner_depth = float(row.get("probe_prepack_hardware_depth"))
        nearest_depth = float(row.get(
            "probe_raster_candidate_source_nearest_depth"
        ))
        farthest_depth = float(row.get(
            "probe_raster_candidate_source_farthest_depth"
        ))
    except (TypeError, ValueError):
        winner_depth = nearest_depth = farthest_depth = math.nan
    if not all(math.isfinite(value) for value in (
        winner_depth, nearest_depth, farthest_depth
    )):
        return {"applicable": True, "admissible": False, "reason": "candidate_source_depth_missing"}
    depth_tolerance = max(1.0e-7, abs(winner_depth) * 1.0e-6)
    if winner_depth < nearest_depth - depth_tolerance \
            or winner_depth > farthest_depth + depth_tolerance:
        return {
            "applicable": True,
            "admissible": False,
            "reason": "winner_depth_outside_candidate_source_envelope",
            "winner_depth": winner_depth,
            "candidate_source_depth_min": nearest_depth,
            "candidate_source_depth_max": farthest_depth,
            "depth_tolerance": depth_tolerance,
        }

    source_world = _finite_vec3(row.get("probe_source_oracle_world_position"))
    reconstructed_center = _finite_vec3(row.get("probe_reconstructed_center"))
    current_world = _finite_vec3(row.get("oracle_world_position"))
    raster_world = _finite_vec3(row.get("probe_prepack_world_position"))
    corners_value = row.get("probe_reconstructed_world_corners")
    corners = [
        corner for value in corners_value
        if (corner := _finite_vec3(value)) is not None
    ] if isinstance(corners_value, list) else []
    if (
        source_world is None
        or reconstructed_center is None
        or current_world is None
        or raster_world is None
        or len(corners) != 4
    ):
        return {"applicable": True, "admissible": False, "reason": "world_geometry_missing"}

    try:
        source_depth = abs(float(row.get("probe_source_oracle_depth")))
    except (TypeError, ValueError):
        source_depth = math.inf
    source_reconstruction_error = _vec3_distance(
        source_world, reconstructed_center
    )
    source_reconstruction_tolerance = max(1.0e-3, source_depth * 1.0e-3)
    if (
        not math.isfinite(source_depth)
        or source_reconstruction_error > source_reconstruction_tolerance
    ):
        return {
            "applicable": True,
            "admissible": False,
            "reason": "reconstruction_disagrees_with_source_ray",
            "source_reconstruction_error": source_reconstruction_error,
            "source_reconstruction_tolerance": source_reconstruction_tolerance,
        }

    quad_radius = max(
        _vec3_distance(reconstructed_center, corner) for corner in corners
    )
    # The interpolated pre-pack world position is the actual current-camera
    # ray/quad intersection emitted by the splat fragment.  Bounding its
    # displacement from the direct hit by the recorded quad radius accepts
    # finite resampling while rejecting a card that reaches beyond its own
    # declared source footprint.
    current_ray_residual = _vec3_distance(current_world, raster_world)
    envelope_tolerance = max(1.0e-3, quad_radius + source_depth * 1.0e-5)
    if not math.isfinite(quad_radius) or current_ray_residual > envelope_tolerance:
        return {
            "applicable": True,
            "admissible": False,
            "reason": "current_ray_outside_finite_quad_envelope",
            "current_ray_residual": current_ray_residual,
            "finite_quad_envelope": envelope_tolerance,
            "projected_coverage": projected_coverage,
        }

    oracle_normal = _finite_vec3(row.get("oracle_world_normal"))
    raster_normal_depth = row.get("probe_prepack_world_normal_depth")
    raster_normal = _finite_vec3(raster_normal_depth)
    if oracle_normal is None or raster_normal is None:
        return {"applicable": True, "admissible": False, "reason": "surface_normal_missing"}
    oracle_length = math.sqrt(sum(value * value for value in oracle_normal))
    raster_length = math.sqrt(sum(value * value for value in raster_normal))
    if oracle_length <= 1.0e-8 or raster_length <= 1.0e-8:
        return {"applicable": True, "admissible": False, "reason": "surface_normal_degenerate"}
    normal_dot = sum(
        oracle_normal[index] * raster_normal[index] for index in range(3)
    ) / (oracle_length * raster_length)
    if normal_dot < 0.0:
        return {
            "applicable": True,
            "admissible": False,
            "reason": "surface_normal_contradiction",
            "surface_normal_dot": normal_dot,
        }
    return {
        "applicable": True,
        "admissible": True,
        "reason": "finite_splat_source_and_footprint_admissible",
        "source_reconstruction_error": source_reconstruction_error,
        "source_reconstruction_tolerance": source_reconstruction_tolerance,
        "current_ray_residual": current_ray_residual,
        "finite_quad_envelope": envelope_tolerance,
        "projected_coverage": projected_coverage,
        "surface_normal_dot": normal_dot,
        "candidate_source_contender_count": int(row.get(
            "probe_raster_candidate_source_contender_count", 0
        )),
        "winner_depth": winner_depth,
        "candidate_source_depth_min": nearest_depth,
        "candidate_source_depth_max": farthest_depth,
    }


def _oracle_structural_geometry_error(row: dict) -> bool:
    """Separate current-ray geometry failure from finite splat resampling.

    A retained texel is a finite angular sample. One rasterized sample may
    legitimately name an adjacent primitive of the same continuous authored
    object and material while still covering the current ray. That exact
    primitive substitution remains measured, but it is not by itself a slab,
    omission, ghost, or wrong-owner observation. Presence disagreement and any
    owner, object, or material substitution remain structural failures.
    """
    classification = str(row.get("oracle_classification", ""))
    if classification in {"omission", "ghost_or_commission"}:
        return True
    if classification == "oracle_query_invalid":
        # Query validity has its own fail-closed evidence gate. It cannot be
        # assigned to production geometry when the reference did not answer.
        return False
    if classification != "identity_mismatch":
        return False
    expected = _oracle_identity_tuple(row.get("oracle_identity"))
    actual = _selected_identity_tuple(row)
    if expected is None or actual is None:
        return True
    mismatch_kind = _oracle_mismatch_classification(expected, actual)
    if mismatch_kind != "same_object_same_material_different_primitive_sample":
        return True
    return not bool(_finite_splat_admissibility(row).get("admissible", False))


# =====================================================================
# FL-4359 CONTINUITY-AWARE, REFERENCE-DISCRIMINATED RE-CLASSIFICATION
# =====================================================================
# Everything below is ADDITIVE. None of it may be consulted by the strict
# classifiers above; those remain the tolerance-zero control.


def _oracle_answer_source_epoch(row: dict) -> str:
    """Name who authored the PUBLISHED answer for one subcell.

    The b64 status word carries the retained source epoch that
    fl4359_retained_source_epoch() stamped when (and only when) the retained arm
    substituted the hit. Epoch DIRECT means the answer is the current-camera
    SceneQuery result. Anything unreadable is reported as unknown and every
    caller then refuses to admit the row on continuity.
    """
    status = row.get("selected_surface_status")
    if not isinstance(status, int) or isinstance(status, bool):
        return "unknown"
    epoch = (int(status) >> ORACLE_STATUS_SOURCE_EPOCH_SHIFT) & 0x3
    return ORACLE_STATUS_SOURCE_EPOCH_NAMES.get(epoch, "unknown")


def _oracle_reference_owner(row: dict) -> str:
    """Name who produced the oracle REFERENCE for one subcell.

    THE FAILURE THIS GUARDS. If the retained lane's reference were itself
    produced by the retained path, the continuity test would pass everywhere and
    the gate would be meaningless self-grading (audit blueprint 7.4, control 1).
    The runtime places the retained arm strictly BEFORE fl4359_oracle_write()
    and feeds that write the independent traversal from
    fl4359_oracle_query_surface (visible_surface_builder.glsl:4676, 4728-4735,
    4794), so the reference is the current-ray SceneQuery by construction.

    The ABI publishes no reference-owner word, so this cannot be read directly.
    It is derived conservatively: a row is credited as `current_ray_scenequery`
    only when the oracle actually answered its own query for this ray. Any row
    whose oracle query did not run or did not validate is reported as
    `unproven`, and an `unproven` row is never admitted on continuity.
    """
    if not bool(row.get("oracle_record_valid", True)):
        return "unproven"
    if not bool(row.get("oracle_query_valid", False)):
        return "unproven"
    if str(row.get("oracle_schema", "")) != SAME_FRAME_ORACLE_SCHEMA:
        return "unproven"
    return "current_ray_scenequery"


def _oracle_ansi_cell_world_span(row: dict, depth: float) -> float | None:
    """World span perpendicular to the view axis covered by one ANSI cell.

    Ported from fl4359_ansi_cell_world_span(), visible_surface_builder.glsl:4244.
    The shader reads push_data.subcell_extent and push_data.ray_parameters.xy;
    the capture publishes the same two quantities indirectly as the frame's
    semantic ANSI size (one ANSI cell is two 2xANSI subcells per axis) and the
    perspective tan half-extents, which ASCIICompositorEffect.gd:19511-19542
    builds as (tan(fov/2) * aspect, tan(fov/2)) from camera_fov and target_size.

    Returns None — never a guessed span — when the projection is not
    perspective or any input is missing. A missing span keeps the row strict.
    """
    if int(row.get("oracle_projection_kind", -1)) != 1:
        # Only PROJECTION_PERSPECTIVE is reconstructible from the published
        # trace. The orthographic branch of the shader law needs the camera
        # ortho half-extents, which this ABI does not transport.
        return None
    trace = _same_frame_oracle_trace(row)
    if not isinstance(trace, dict):
        return None
    viewport = trace.get("viewport") if isinstance(trace.get("viewport"), dict) else {}
    ansi_size = trace.get("semantic_ansi_size") or viewport.get("semantic_ansi_size")
    target_size = trace.get("target_size") or viewport.get("target_size")
    camera_fov = trace.get("camera_fov")
    if (
        not isinstance(ansi_size, (list, tuple)) or len(ansi_size) < 2
        or not isinstance(target_size, (list, tuple)) or len(target_size) < 2
        or not isinstance(camera_fov, (int, float)) or isinstance(camera_fov, bool)
    ):
        return None
    try:
        extent_x = int(ansi_size[0]) * 2
        extent_y = int(ansi_size[1]) * 2
        target_x = float(target_size[0])
        target_y = float(target_size[1])
        fov = float(camera_fov)
    except (TypeError, ValueError):
        return None
    if extent_x <= 0 or extent_y <= 0 or target_x <= 0.0 or target_y <= 0.0:
        return None
    if not math.isfinite(fov) or fov <= 0.0 or fov >= 180.0:
        return None
    if not math.isfinite(depth) or depth <= 0.0:
        return None
    tan_half_y = math.tan(math.radians(fov) * 0.5)
    tan_half_x = tan_half_y * (target_x / target_y)
    # The factor four is the shader's: two for the ndc [-1,1] normalization used
    # by build_sample_ray and two more because one ANSI cell is two subcells.
    span_x = 4.0 * tan_half_x * depth / float(extent_x)
    span_y = 4.0 * tan_half_y * depth / float(extent_y)
    span = max(abs(span_x), abs(span_y))
    return span if math.isfinite(span) and span > 0.0 else None


def _oracle_surface_continuity(row: dict, tolerance_scale: float = 1.0) -> dict:
    """Grade the published answer against the exact current ray by SURFACE.

    This mirrors fl4359_retained_surface_continuous()
    (visible_surface_builder.glsl:4444) term for term, with the shader's
    `reference` bound to the oracle's independent current-camera traversal and
    the shader's `candidate` bound to the answer this invocation published:

      * water on either side always fails (the FL-4737/FL-4216 coupling proof);
      * owner_kind, object_id and material must be equal;
      * primitive_id is deliberately NOT compared;
      * SIGNED normal agreement at or above cos(25 degrees) — signed, not
        absolute, because a flipped normal is the back face and is exactly the
        oversplat defect the criterion exists to reject;
      * perpendicular point-to-plane distance within a depth-scaled ANSI-cell
        tolerance;
      * along-ray depth separation within the same tolerance (grazing band).

    `tolerance_scale` exists ONLY for the mandated tolerance-zero control. At
    0.0 both bands collapse to exact equality, which must reproduce the strict
    counters; no value other than 1.0 may produce a proof number.
    """
    result = {
        "applicable": False,
        "continuous": False,
        "reason": "not_applicable",
        "normal_dot": None,
        "plane_distance": None,
        "depth_separation": None,
        "tolerance": None,
        "span": None,
    }
    expected = _oracle_identity_tuple(row.get("oracle_identity"))
    actual = _selected_identity_tuple(row)
    if not bool(row.get("oracle_direct_hit", False)) or expected is None:
        result["reason"] = "reference_absent"
        return result
    if not bool(row.get("oracle_production_hit", False)) or actual is None:
        result["reason"] = "answer_absent"
        return result
    result["applicable"] = True
    if _oracle_reference_owner(row) != "current_ray_scenequery":
        # Refuse by default: a row whose reference cannot be proven independent
        # may never be admitted by a criterion the reference itself grades.
        result["reason"] = "reference_owner_unproven"
        return result
    if expected[0] == ORACLE_OWNER_WATER or actual[0] == ORACLE_OWNER_WATER:
        result["reason"] = "water_excluded"
        return result
    if expected[0] != actual[0]:
        result["reason"] = "owner_substitution"
        return result
    if expected[1] != actual[1]:
        result["reason"] = "object_substitution"
        return result
    if expected[3] != actual[3]:
        result["reason"] = "material_substitution"
        return result
    if tolerance_scale <= 0.0 and expected[2] != actual[2]:
        # TOLERANCE-ZERO CONTROL ONLY. At zero tolerance the criterion must
        # degenerate into the Tier-0 exact-identity test the shader ships
        # today, which DOES compare primitive_id. Restoring the comparison here
        # is what makes the control reproduce the strict counters instead of
        # reproducing a slightly-relaxed near-twin of them.
        result["reason"] = "primitive_substitution"
        return result
    reference_position = _finite_vec3(row.get("oracle_world_position"))
    candidate_position = _finite_vec3(row.get("selected_surface_world_position"))
    reference_normal = _finite_vec3(row.get("oracle_world_normal"))
    candidate_normal = _finite_vec3(row.get("selected_surface_world_normal"))
    reference_depth = row.get("oracle_depth")
    candidate_depth = row.get("selected_surface_depth")
    if (
        reference_position is None or candidate_position is None
        or reference_normal is None or candidate_normal is None
        or not isinstance(reference_depth, (int, float))
        or isinstance(reference_depth, bool)
        or not isinstance(candidate_depth, (int, float))
        or isinstance(candidate_depth, bool)
        or not math.isfinite(float(reference_depth))
        or not math.isfinite(float(candidate_depth))
    ):
        # A row that genuinely lacks the geometry stays a strict defect. It is
        # never silently admitted, and it is counted under its own reason so a
        # capture-side transport gap is visible rather than absorbed.
        result["reason"] = "surface_geometry_missing"
        return result
    reference_length_sq = sum(value * value for value in reference_normal)
    candidate_length_sq = sum(value * value for value in candidate_normal)
    if reference_length_sq < 1.0e-12 or candidate_length_sq < 1.0e-12:
        result["reason"] = "surface_normal_degenerate"
        return result
    reference_unit = [
        value / math.sqrt(reference_length_sq) for value in reference_normal
    ]
    candidate_unit = [
        value / math.sqrt(candidate_length_sq) for value in candidate_normal
    ]
    normal_dot = sum(a * b for a, b in zip(reference_unit, candidate_unit))
    result["normal_dot"] = normal_dot
    minimum_cos = (
        1.0 if tolerance_scale <= 0.0 else ORACLE_CONTINUITY_NORMAL_MIN_COS
    )
    if normal_dot < minimum_cos:
        result["reason"] = "surface_normal_discontinuous"
        return result
    span = _oracle_ansi_cell_world_span(row, float(reference_depth))
    result["span"] = span
    if tolerance_scale <= 0.0:
        tolerance = 0.0
    elif span is None:
        result["reason"] = "ansi_cell_span_unavailable"
        return result
    else:
        tolerance = tolerance_scale * max(
            ORACLE_CONTINUITY_PLANE_TOLERANCE_FLOOR,
            ORACLE_CONTINUITY_PLANE_TOLERANCE_SPANS * span,
        )
    result["tolerance"] = tolerance
    plane_distance = abs(
        sum(
            normal * (reference - candidate)
            for normal, reference, candidate in zip(
                candidate_unit, reference_position, candidate_position
            )
        )
    )
    result["plane_distance"] = plane_distance
    if not math.isfinite(plane_distance) or plane_distance > tolerance:
        result["reason"] = "plane_distance_exceeded"
        return result
    depth_separation = abs(float(candidate_depth) - float(reference_depth))
    result["depth_separation"] = depth_separation
    if not math.isfinite(depth_separation) or depth_separation > tolerance:
        result["reason"] = "depth_separation_exceeded"
        return result
    result["continuous"] = True
    result["reason"] = "same_continuous_surface"
    return result


def _oracle_where_reanchor_class(row: dict) -> str:
    """Name the FL-4359 world-lattice WHERE signature on one mismatch row.

    fl4359_apply_persistent_terrain_where() (visible_surface_builder.glsl:4276)
    replaces primitive_id, material and anchor on a TERRAIN hit and leaves
    depth, position, normal, owner_kind and object_id at their exact ray values.
    A row carrying exactly that signature is a BY-DESIGN address change, not a
    geometry defect. Material equality is reported separately because the
    elected representative cell may carry a different material texel; that
    subclass is NOT admitted by the continuity criterion, which compares
    material.
    """
    expected = _oracle_identity_tuple(row.get("oracle_identity"))
    actual = _selected_identity_tuple(row)
    if expected is None or actual is None:
        return "not_where_reanchor"
    if expected[0] != ORACLE_OWNER_TERRAIN or actual[0] != ORACLE_OWNER_TERRAIN:
        return "not_where_reanchor"
    if expected[1] != actual[1] or expected[2] == actual[2]:
        return "not_where_reanchor"
    reference_position = row.get("oracle_world_position")
    candidate_position = row.get("selected_surface_world_position")
    reference_normal = row.get("oracle_world_normal")
    candidate_normal = row.get("selected_surface_world_normal")
    reference_depth = row.get("oracle_depth")
    candidate_depth = row.get("selected_surface_depth")
    # Bit-exact, not epsilon: the WHERE law copies these words through
    # untouched, so anything but equality means some other law also moved the
    # surface and the row is not a pure re-anchor.
    ray_exact = (
        reference_position == candidate_position
        and reference_normal == candidate_normal
        and reference_depth == candidate_depth
    )
    if not ray_exact:
        return "not_where_reanchor"
    if expected[3] != actual[3]:
        return "where_reanchor_material_substituted"
    return "where_reanchor_address_only"


def _oracle_continuity_class(row: dict, tolerance_scale: float = 1.0) -> dict:
    """Return the continuity-law classification for one oracle row.

    The returned `class` values partition the same population the strict
    classifier partitions, so the two can be compared row for row:

      absence_agreement / exact_identity_agreement  — unchanged from strict;
      where_reanchor_address_only                   — BY DESIGN (WHERE law);
      retained_substitution_continuous              — BY DESIGN (retained arm);
      current_ray_continuous_resample               — same surface, different
                                                      primitive, current-ray
                                                      answer (finite sampling);
      real_defect_*                                 — a genuinely different
                                                      surface, owner, object,
                                                      material, or a
                                                      presence/query failure.
    """
    classification = str(row.get("oracle_classification", ""))
    epoch = _oracle_answer_source_epoch(row)
    retained = epoch not in {"direct_current_ray", "unknown"}
    where_class = "not_where_reanchor"
    continuity = {
        "applicable": False,
        "continuous": False,
        "reason": "not_applicable",
    }
    if classification == "absence_agreement":
        name = "absence_agreement"
    elif classification == "identity_agreement":
        name = "exact_identity_agreement"
    elif classification == "omission":
        name = "real_defect_omission"
    elif classification == "ghost_or_commission":
        name = "real_defect_ghost_or_commission"
    elif classification == "oracle_query_invalid":
        name = "real_defect_oracle_query_invalid"
    elif classification != "identity_mismatch":
        name = "real_defect_unclassified"
    else:
        where_class = _oracle_where_reanchor_class(row)
        continuity = _oracle_surface_continuity(row, tolerance_scale)
        if tolerance_scale <= 0.0:
            # Tolerance-zero control: the WHERE short-circuit is a continuity
            # admission too, so it must also be disarmed or the control would
            # not reproduce the strict counters.
            name = "real_defect_" + str(continuity.get("reason", "unclassified"))
        elif where_class == "where_reanchor_address_only":
            name = "where_reanchor_address_only"
        elif continuity.get("continuous", False):
            name = (
                "retained_substitution_continuous"
                if retained
                else "current_ray_continuous_resample"
            )
        else:
            name = "real_defect_" + str(continuity.get("reason", "unclassified"))
    return {
        "class": name,
        "defect": name.startswith("real_defect_"),
        "answer_source_epoch": epoch,
        "retained_authored": retained,
        "where_reanchor": where_class,
        "continuity": continuity,
    }


def _oracle_structural_geometry_error_continuity(
    row: dict, tolerance_scale: float = 1.0
) -> bool:
    """Continuity-law twin of _oracle_structural_geometry_error().

    Kept as a separate function on purpose: the strict predicate must stay
    byte-for-byte the historical one so the two populations can be differenced.
    """
    return bool(_oracle_continuity_class(row, tolerance_scale).get("defect", False))


def _binary_contingency_report(counts: Counter) -> dict:
    """Return counts plus bounded association statistics without causal prose."""
    a = int(counts.get("changed_and_misregistered", 0))
    b = int(counts.get("changed_and_not_misregistered", 0))
    c = int(counts.get("stable_and_misregistered", 0))
    d = int(counts.get("stable_and_not_misregistered", 0))
    changed_total = a + b
    stable_total = c + d
    changed_risk = a / changed_total if changed_total else None
    stable_risk = c / stable_total if stable_total else None
    risk_ratio = (
        changed_risk / stable_risk
        if changed_risk is not None and stable_risk not in (None, 0.0)
        else None
    )
    odds_ratio = (a * d) / (b * c) if b > 0 and c > 0 else None
    phi_denominator = math.sqrt(
        float((a + b) * (c + d) * (a + c) * (b + d))
    )
    phi = ((a * d) - (b * c)) / phi_denominator \
        if phi_denominator > 0.0 else None
    return {
        "counts": {
            "changed_and_misregistered": a,
            "changed_and_not_misregistered": b,
            "stable_and_misregistered": c,
            "stable_and_not_misregistered": d,
        },
        "p_misregistration_given_changed": changed_risk,
        "p_misregistration_given_stable": stable_risk,
        "risk_ratio": risk_ratio,
        "odds_ratio": odds_ratio,
        "phi_coefficient": phi,
        "causal_claim": False,
    }


def _complete_glyph_mask_witness(row: dict) -> dict:
    """Grade b43's pure-atlas digest without treating RGB as glyph identity."""
    expected = row.get("final_paint_expected_atlas_mask_hash")
    actual = row.get("final_paint_actual_mask_hash")
    source_key = row.get("final_paint_mask_source_key")
    expected_pixels = row.get("final_paint_expected_atlas_mask_pixel_count")
    actual_pixels = row.get("final_paint_actual_atlas_mask_pixel_count")
    unknown_bits = row.get("final_paint_atlas_mask_unknown_bits")
    dimensions = row.get("final_paint_atlas_mask_dimensions")
    native_pixel_count = row.get("final_ink_pixels")
    product_identity = row.get("final_product_exact_identity")
    expected_product_key = (
        product_identity.get("cell_mask_winner_key")
        if isinstance(product_identity, dict)
        else None
    )
    expected_glyph = (
        product_identity.get("cell_mask_winner_glyph")
        if isinstance(product_identity, dict)
        else None
    )
    actual_glyph = row.get("final_paint_glyph")
    available = (
        isinstance(expected, int)
        and not isinstance(expected, bool)
        and isinstance(actual, int)
        and not isinstance(actual, bool)
        and isinstance(source_key, dict)
        and isinstance(expected_product_key, dict)
        and isinstance(expected_glyph, int)
        and not isinstance(expected_glyph, bool)
        and isinstance(actual_glyph, int)
        and not isinstance(actual_glyph, bool)
        and isinstance(expected_pixels, int)
        and not isinstance(expected_pixels, bool)
        and isinstance(actual_pixels, int)
        and not isinstance(actual_pixels, bool)
        and isinstance(unknown_bits, int)
        and not isinstance(unknown_bits, bool)
        and isinstance(dimensions, (list, tuple))
        and len(dimensions) == 2
        and all(isinstance(value, int) and value > 0 for value in dimensions)
        and isinstance(native_pixel_count, int)
        and not isinstance(native_pixel_count, bool)
    )
    expected_native_pixel_count = (
        int(dimensions[0]) * int(dimensions[1]) if available else None
    )
    native_coverage_complete = bool(
        available and native_pixel_count == expected_native_pixel_count
    )
    product_key_equal = bool(
        available
        and json.dumps(source_key, sort_keys=True, separators=(",", ":"))
        == json.dumps(expected_product_key, sort_keys=True, separators=(",", ":"))
    )
    glyph_equal = bool(available and actual_glyph == expected_glyph)
    complete = bool(
        available
        and unknown_bits == 0
        and expected == actual
        and expected_pixels == actual_pixels
        and native_coverage_complete
        and product_key_equal
        and glyph_equal
        and bool(row.get("final_paint_complete_atlas_mask", False))
    )
    return {
        "available": available,
        "complete": complete,
        "digest_equal": available and expected == actual,
        "pixel_count_equal": available and expected_pixels == actual_pixels,
        "native_coverage_complete": native_coverage_complete,
        "product_key_equal": product_key_equal,
        "glyph_equal": glyph_equal,
        "mask_source_key": source_key,
        "expected_product_key": expected_product_key,
        "actual_glyph": actual_glyph,
        "expected_glyph": expected_glyph,
        "native_pixel_count": native_pixel_count,
        "expected_native_pixel_count": expected_native_pixel_count,
        "unknown_bits": int(unknown_bits) if isinstance(unknown_bits, int) else None,
    }


def _product_exact_identity_witness(row: dict) -> dict:
    """Grade Product's capture-only b60 witness independently of b55 keys."""
    identity = row.get("final_product_exact_identity")
    available = bool(
        isinstance(identity, dict)
        and identity.get("exact_product_identity_available") is True
    )
    valid = bool(available and identity.get("valid") is True)
    translation_equal = bool(
        valid and identity.get("product_translation_equal") is True
    )
    return {
        "available": available,
        "valid": valid,
        "translation_equal": translation_equal,
        "state": identity.get("state") if isinstance(identity, dict) else None,
        "translation_kind": (
            identity.get("product_translation_kind")
            if isinstance(identity, dict) else None
        ),
        "cell_mask_winner_key_evidence_scope": (
            identity.get("cell_mask_winner_key_evidence_scope")
            if isinstance(identity, dict) else None
        ),
        "relations": identity.get("relations") if isinstance(identity, dict) else None,
    }


def _oracle_expected_probe_path_divergence(row: dict) -> str | None:
    """Locate an exact oracle identity before raster depth can choose a winner."""
    active_mask = int(row.get("probe_expected_visibility_active_mask", 0))
    query_valid_mask = int(row.get(
        "probe_expected_visibility_query_valid_mask", 0
    ))
    visible_identity_mask = int(row.get(
        "probe_expected_visibility_identity_match_mask", 0
    ))
    capture_active_mask = int(row.get(
        "probe_expected_capture_active_mask", 0
    ))
    capture_match_mask = int(row.get(
        "probe_expected_capture_nearby_match_mask", 0
    ))
    if active_mask == 0 or capture_active_mask != active_mask:
        return "expected_capture_search_uninstrumented"
    if (query_valid_mask & active_mask) != active_mask:
        return "expected_capture_query_invalid"
    if visible_identity_mask == 0:
        return "capture_visibility_domain"
    if (capture_match_mask & visible_identity_mask) == 0:
        if str(row.get("capture_schema", "")) in {
            "fl4359_sparse_surface_capture.v7",
            *SUPPORTED_SPARSE_CAPTURE_SCHEMAS,
        }:
            return _oracle_expected_center_stage(
                row, "expected_capture_search_contradiction"
            )
        return "capture_write"
    return None


def _sha256_text_valid(value: object) -> bool:
    text = str(value)
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text
    )


def _capture_submission_context_status(
    row: dict,
    *,
    owner_kind: int,
    source_probe: int,
    face: int,
    physical_layer: int,
    capture_origin: object,
    assigned_origin: object,
    origin_valid: bool,
    face_admitted: bool,
    publication: object,
    submission: object,
) -> str:
    """Require the exact physical face submission before blaming its writer."""
    if str(row.get("capture_schema", "")) != CURRENT_SPARSE_CAPTURE_SCHEMA:
        return "legacy_unavailable"
    family = {1: "static", 2: "terrain", 3: "water", 4: "dynamic"}.get(
        int(owner_kind)
    )
    if family is None:
        return "owner_domain_invalid"
    if (owner_kind == 4 and source_probe != 0) or (
        owner_kind != 4 and source_probe not in {1, 2}
    ):
        return "owner_domain_invalid"
    bands = row.get("probe_capture_bands")
    if (
        not isinstance(bands, list)
        or len(bands) != 3
        or sorted(int(value) for value in bands) != [0, 1, 2]
        or face < 0
        or face >= 6
    ):
        return "physical_layer_context_invalid"
    expected_layer = int(bands[source_probe]) * 6 + face
    if physical_layer != expected_layer:
        return "physical_layer_mismatch"
    if not face_admitted:
        return "face_not_admitted"
    if (
        not origin_valid
        or not isinstance(capture_origin, list)
        or not isinstance(assigned_origin, list)
        or len(capture_origin) != 3
        or len(assigned_origin) != 3
        or any(
            not math.isfinite(float(value))
            for value in [*capture_origin, *assigned_origin]
        )
    ):
        return "origin_context_invalid"
    if capture_origin != assigned_origin:
        return "origin_mismatch"
    if not isinstance(publication, dict) or not isinstance(submission, dict):
        return "publication_context_invalid"
    capture_hash = str(publication.get("capture_sha256", ""))
    current_hash = str(publication.get("current_sha256", ""))
    token_hash = str(submission.get("token_sha256", ""))
    submission_publication = str(submission.get("publication_sha256", ""))
    if not all(
        _sha256_text_valid(value)
        for value in (capture_hash, current_hash, token_hash, submission_publication)
    ):
        return "publication_context_invalid"
    if (
        not bool(publication.get("matches", False))
        or capture_hash != current_hash
        or submission_publication != capture_hash
    ):
        return "publication_mismatch"
    expected_band = int(bands[source_probe])
    submission_probe = int(submission.get("logical_probe", -1))
    # A retained physical band is authored while it is CURRENT, then becomes
    # PREVIOUS when the logical bands swap.  The submission token must preserve
    # that authoring role; rewriting it to the consumer role would erase the
    # lifecycle fact the token exists to prove.  No reverse or EYE transition
    # is valid, and every immutable physical/publication field still has to
    # match below.
    retained_current_to_previous = source_probe == 2 and submission_probe == 1
    authoring_probe_matches = (
        submission_probe == source_probe or retained_current_to_previous
    )
    if (
        str(publication.get("family", "")) != family
        or not authoring_probe_matches
        or int(submission.get("physical_band", -1)) != expected_band
        or int(submission.get("physical_layer", -1)) != physical_layer
        or int(submission.get("face", -1)) != face
        or submission.get("assigned_origin") != assigned_origin
        or not bool(submission.get("face_admitted", False))
        or int(submission.get("draw_count", 0)) <= 0
        or int(submission.get("instance_count", 0)) <= 0
    ):
        return "submission_context_mismatch"
    return (
        "comparable_retained_current_to_previous"
        if retained_current_to_previous
        else "comparable"
    )


def _oracle_expected_center_classification(row: dict) -> str:
    """Classify the exact cubemap-center query against the stored capture texel.

    This witness separates angular center-ray aliasing from a disagreement at
    the capture-write boundary.  It intentionally reports raw depth residuals
    elsewhere instead of inventing a visual-error threshold here.
    """
    if str(row.get("capture_schema", "")) != CURRENT_SPARSE_CAPTURE_SCHEMA:
        return "capture_center_legacy_unavailable"
    if not bool(row.get("probe_expected_center_applicable", False)):
        return "capture_center_not_applicable"
    if not bool(row.get("probe_expected_center_record_valid", False)):
        return "capture_center_record_invalid"
    if not bool(row.get("probe_expected_center_address_valid", False)):
        return "capture_center_address_invalid"
    if not bool(row.get("probe_expected_center_query_valid", False)):
        return "capture_center_query_invalid"

    query_hit = bool(row.get("probe_expected_center_direct_hit", False))
    capture_hit = bool(row.get("probe_expected_center_capture_valid", False))
    expected = _oracle_identity_tuple(row.get("oracle_identity")) \
        if bool(row.get("oracle_direct_hit", False)) else None
    query_identity = _oracle_identity_tuple(
        row.get("probe_expected_center_identity")
    ) if query_hit else None
    capture_identity = _oracle_identity_tuple(
        row.get("probe_expected_center_capture_identity")
    ) if capture_hit else None
    if query_hit and query_identity is None:
        return "capture_center_query_identity_invalid"
    if capture_hit and capture_identity is None:
        return "capture_center_capture_identity_invalid"

    # A retained texel can be older than the current capture generation. That
    # is the purpose of retention, so generation alone is never causal. Writer
    # claims require the exact scene/family publication and probe origin that
    # authored the physical cubemap layer to match the current comparator.
    capture_revisions = row.get("probe_expected_center_capture_revisions")
    current_revisions = row.get("probe_expected_center_current_revisions")
    if not isinstance(capture_revisions, dict) or not isinstance(
        current_revisions, dict
    ):
        return "capture_center_publication_context_invalid"
    if int(capture_revisions.get("scene", -1)) != int(
        current_revisions.get("scene", -2)
    ):
        return "capture_center_scene_revision_mismatch"
    owner_kind = (
        capture_identity[0]
        if capture_identity is not None
        else query_identity[0] if query_identity is not None else 0
    )
    family_field = {
        1: "static",
        2: "terrain",
        3: "water",
        4: "dynamic",
    }.get(owner_kind)
    if family_field is None:
        return "capture_center_owner_domain_invalid"
    source_probe = int(row.get("probe_expected_center_source_probe", -1))
    if (owner_kind == 4 and source_probe != 0) or (
        owner_kind != 4 and source_probe not in {1, 2}
    ):
        return "capture_center_owner_domain_invalid"
    capture_family_revision = capture_revisions.get(
        family_field, capture_revisions.get("family", -1)
    )
    if int(capture_family_revision) != int(
        current_revisions.get(family_field, -2)
    ):
        return "capture_center_family_revision_mismatch"
    capture_origin = row.get("probe_expected_center_capture_origin")
    assigned_origin = row.get("probe_expected_center_assigned_origin")
    if not bool(row.get("probe_expected_center_capture_origin_valid", False)):
        return "capture_center_origin_context_invalid"
    if (
        not isinstance(capture_origin, list)
        or not isinstance(assigned_origin, list)
        or len(capture_origin) != 3
        or len(assigned_origin) != 3
        or any(not math.isfinite(float(value)) for value in capture_origin)
        or any(not math.isfinite(float(value)) for value in assigned_origin)
    ):
        return "capture_center_origin_context_invalid"
    if capture_origin != assigned_origin:
        return "capture_center_origin_mismatch"
    status = _capture_submission_context_status(
        row,
        owner_kind=owner_kind,
        source_probe=source_probe,
        face=int(row.get("probe_expected_center_face", -1)),
        physical_layer=int(row.get(
            "probe_expected_center_capture_physical_layer", -1
        )),
        capture_origin=capture_origin,
        assigned_origin=assigned_origin,
        origin_valid=bool(row.get(
            "probe_expected_center_capture_origin_valid", False
        )),
        face_admitted=bool(row.get(
            "probe_expected_center_face_admitted", False
        )),
        publication=row.get("probe_expected_center_publication_context"),
        submission=row.get("probe_expected_center_submission_context"),
    )
    if status not in {
        "comparable",
        "comparable_retained_current_to_previous",
    }:
        return f"capture_center_{status}"
    if query_hit and not capture_hit:
        return "capture_writer_false_negative"
    if not query_hit and capture_hit:
        return "capture_writer_false_positive"
    if not query_hit:
        return (
            "capture_center_absence_alias_confirmed"
            if expected is not None
            else "capture_center_absence_agreement"
        )
    if capture_identity != query_identity:
        return "capture_writer_identity_mismatch"
    if expected is not None and query_identity != expected:
        return "capture_center_alias_confirmed"
    return "capture_center_identity_agreement"


def _oracle_expected_center_stage(row: dict, contradiction: str) -> str:
    """Map the strong center witness without laundering stale context."""
    center = _oracle_expected_center_classification(row)
    if center in {
        "capture_writer_false_negative",
        "capture_writer_false_positive",
        "capture_writer_identity_mismatch",
    }:
        return "capture_write"
    if center in {
        "capture_center_alias_confirmed",
        "capture_center_absence_alias_confirmed",
    }:
        return "capture_center_sampling_alias"
    if center == "capture_center_identity_agreement":
        return contradiction
    return f"expected_{center}"


def _oracle_source_capture_write_stage(row: dict) -> str:
    """Permit source-writer attribution only for one comparable submission."""
    if str(row.get("capture_schema", "")) != CURRENT_SPARSE_CAPTURE_SCHEMA:
        return "source_capture_legacy_unavailable"
    identity = _oracle_identity_tuple(row.get("probe_source_capture_identity"))
    if identity is None:
        return "source_capture_identity_invalid"
    revisions = row.get("probe_source_capture_revisions")
    current = row.get("probe_source_current_revisions")
    if (
        not isinstance(revisions, list)
        or len(revisions) < 3
        or not isinstance(current, dict)
    ):
        return "source_capture_revision_context_invalid"
    family = {1: "static", 2: "terrain", 3: "water", 4: "dynamic"}.get(
        identity[0]
    )
    if family is None:
        return "source_capture_owner_domain_invalid"
    if int(revisions[0]) != int(current.get("scene", -1)) or int(
        revisions[2]
    ) != int(current.get(family, -2)):
        return "source_capture_revision_mismatch"
    status = _capture_submission_context_status(
        row,
        owner_kind=identity[0],
        source_probe=int(row.get("probe_source_capture_probe", -1)),
        face=int(row.get("probe_source_capture_face", -1)),
        physical_layer=int(row.get("probe_source_capture_physical_layer", -1)),
        capture_origin=row.get("probe_source_capture_origin"),
        assigned_origin=row.get("probe_source_assigned_origin"),
        origin_valid=bool(row.get("probe_source_capture_origin_valid", False)),
        face_admitted=bool(row.get(
            "probe_source_capture_face_admitted", False
        )),
        publication=row.get("probe_source_capture_publication_context"),
        submission=row.get("probe_source_capture_submission_context"),
    )
    return (
        "capture_write"
        if status in {
            "comparable",
            "comparable_retained_current_to_previous",
        }
        else f"source_capture_{status}"
    )


def _finite_distribution(values: list[float]) -> dict:
    """Return bounded descriptive statistics without assigning pass criteria."""
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return {"count": 0, "median": None, "p90": None, "max": None}

    def percentile(fraction: float) -> float:
        index = min(len(finite) - 1, int(math.ceil(fraction * len(finite))) - 1)
        return finite[max(index, 0)]

    return {
        "count": len(finite),
        "median": percentile(0.5),
        "p90": percentile(0.9),
        "max": finite[-1],
    }


def _oracle_first_divergence(row: dict) -> str:
    """Name the earliest observable boundary and refuse invented precision.

    The pre-pack raster-winner attachment and packed b61 record are separate
    capture-only observations.  They localize packing failures exactly and
    retain one honest grouped stage when capture, reconstruction, and hardware
    raster selection have not yet been split by upstream source facts.
    """
    classification = str(row.get("oracle_classification", "oracle_query_invalid"))
    product_witness = _product_exact_identity_witness(row)
    product_translation_error = bool(
        product_witness.get("available", False)
        and not product_witness.get("translation_equal", False)
    )
    mask_witness = _complete_glyph_mask_witness(row)
    glyph_mask_error = bool(
        (
            mask_witness.get("available", False)
            and not mask_witness.get("complete", False)
        )
        or int(row.get("final_paint_mask_contributor_count", 0)) > 1
    )
    if classification in {"identity_agreement", "absence_agreement"}:
        if not product_translation_error and not glyph_mask_error:
            return "none"
    if not bool(row.get("oracle_query_valid", False)):
        failure_name = str(row.get("oracle_query_failure_name", ""))
        if failure_name in {
            "dynamic_publication",
            "dynamic_visit_budget",
            "dynamic_node_row",
            "dynamic_node_box",
            "dynamic_leaf_slot",
            "dynamic_face_nonfinite",
            "dynamic_child",
            "dynamic_stack",
            "static_query",
            "heightfield_query",
            "water_query",
        }:
            return f"oracle_query_{failure_name}"
        return "oracle_query_invalid"
    expected = _oracle_identity_tuple(row.get("oracle_identity"))
    source_capture = _oracle_identity_tuple(
        row.get("probe_source_capture_identity")
    ) if bool(row.get("probe_source_capture_valid", False)) else None
    source_oracle = _oracle_identity_tuple(
        row.get("probe_source_oracle_identity")
    ) if bool(row.get("probe_source_oracle_direct_hit", False)) else None
    prepack = _oracle_identity_tuple(row.get("probe_prepack_identity")) \
        if bool(row.get("probe_prepack_valid", False)) else None
    candidate = _candidate_identity_tuple(row)
    selected = _selected_identity_tuple(row)
    witness_overflow = bool(row.get("probe_raster_contender_overflow", True))
    selected_contenders = int(
        row.get("probe_raster_selected_identity_contender_count", 0)
    )
    structural_contenders = int(
        row.get("probe_raster_structural_identity_contender_count", 0)
    )
    exact_contenders = int(
        row.get("probe_raster_exact_identity_contender_count", 0)
    )
    if witness_overflow:
        return "raster_witness_invalid"
    capture_schema = str(row.get("capture_schema", ""))
    if capture_schema in {
        "fl4359_sparse_surface_capture.v6",
        "fl4359_sparse_surface_capture.v7",
        *SUPPORTED_SPARSE_CAPTURE_SCHEMAS,
    } and bool(
        row.get("oracle_direct_hit", False)
    ) and classification in {"identity_mismatch", "omission"}:
        if not bool(row.get("probe_expected_raster_record_valid", False)):
            return "expected_reference_raster_witness_invalid"
        visible_mask = int(
            row.get("probe_expected_raster_identity_visible_mask", 0)
        )
        exact_capture_mask = int(
            row.get("probe_expected_raster_exact_capture_match_mask", 0)
        )
        source_valid_mask = int(
            row.get("probe_expected_raster_source_valid_mask", 0)
        )
        face_enabled_mask = int(
            row.get("probe_expected_raster_face_enabled_mask", 0)
        )
        clip_valid_mask = int(
            row.get("probe_expected_raster_clip_valid_mask", 0)
        )
        cover_mask = int(row.get("probe_expected_raster_cover_mask", 0))
        suppressed_mask = int(
            row.get("probe_expected_raster_suppressed_mask", 0)
        )
        if visible_mask == 0:
            return "capture_visibility_domain"
        exact_expected_mask = visible_mask & exact_capture_mask
        if exact_expected_mask == 0:
            if capture_schema in {
                "fl4359_sparse_surface_capture.v7",
                *SUPPORTED_SPARSE_CAPTURE_SCHEMAS,
            }:
                return _oracle_expected_center_stage(
                    row, "expected_center_raster_witness_contradiction"
                )
            return "capture_write"
        if (source_valid_mask & exact_expected_mask) == 0:
            if capture_schema in {
                "fl4359_sparse_surface_capture.v7",
                *SUPPORTED_SPARSE_CAPTURE_SCHEMAS,
            }:
                return _oracle_expected_center_stage(
                    row, "expected_center_source_witness_contradiction"
                )
            return "capture_write"
        face_expected_mask = face_enabled_mask & exact_expected_mask
        if face_expected_mask == 0:
            return "capture_face_visibility_domain"
        clip_expected_mask = clip_valid_mask & face_expected_mask
        if clip_expected_mask == 0:
            return "reconstruction_clip_invalid"
        unsuppressed_mask = clip_expected_mask & ~suppressed_mask
        if unsuppressed_mask == 0:
            return "capture_positive_contradiction_suppression"
        if (cover_mask & unsuppressed_mask) == 0:
            return "reconstruction_coverage"
        if exact_contenders <= 0:
            return "raster_expected_fragment_missing"
    if prepack is not None:
        if not bool(row.get("probe_source_capture_band_map_valid", False)):
            return "source_physical_join_invalid"
        if int(row.get("probe_source_capture_join_flags", 0)) != 0x7F:
            return "source_physical_join_invalid"
        # The depth-tested MRT winner must have appeared in the actual
        # fragment contender stream. Missing it means the render-to-compute
        # witness barrier or record join is incomplete, not that packing owns
        # the geometry error.
        if selected_contenders <= 0:
            return "raster_witness_selected_fragment_invalid"
    # Candidate packing owns the first divergence only when the pre-pack
    # identity still agrees with the direct-ray reference.  A missing b61
    # record must not hide an already-wrong hardware winner: that would turn a
    # protective pack rejection into the alleged source of malformed geometry.
    if prepack is not None and candidate is None and prepack == expected:
        return "candidate_pack"
    if bool(row.get("oracle_direct_hit", False)):
        if prepack is None:
            expected_probe_divergence = _oracle_expected_probe_path_divergence(row)
            if expected_probe_divergence is not None:
                return expected_probe_divergence
            if exact_contenders > 0:
                return "raster_witness_prepack_contradiction_invalid"
            return "raster_coverage_omission"
        if source_capture is None:
            # The raster winner can survive after the capture attachment named
            # by its provenance no longer contains a valid texel.  When the
            # address is valid and an independent same-ray source query still
            # reproduces the pre-pack identity, packing is exonerated: the
            # earliest observed break is the association between the hardware
            # winner and its capture-bank source.  Keep this distinct from a
            # missing diagnostic record, which remains fail-closed below.
            if (
                bool(row.get("probe_source_oracle_address_valid", False))
                and bool(row.get("probe_source_oracle_query_valid", False))
                and source_oracle is not None
                and source_oracle == prepack
            ):
                return "raster_winner_source_association"
            return "source_capture_or_provenance_uninstrumented"
        if not bool(row.get("probe_source_oracle_query_valid", False)):
            return "source_capture_query_invalid"
        if source_oracle is None or source_oracle != source_capture:
            return _oracle_source_capture_write_stage(row)
        if source_capture != prepack:
            return "raster_winner_source_association"
        if expected != prepack:
            source_world = row.get("probe_source_oracle_world_position")
            reconstructed_center = row.get("probe_reconstructed_center")
            if (
                isinstance(source_world, list) and len(source_world) >= 3
                and isinstance(reconstructed_center, list)
                and len(reconstructed_center) >= 3
            ):
                center_error = math.sqrt(sum(
                    (float(source_world[index]) - float(reconstructed_center[index])) ** 2
                    for index in range(3)
                ))
                source_depth = float(row.get("probe_source_oracle_depth", 0.0))
                if center_error > max(1.0e-3, abs(source_depth) * 1.0e-3):
                    return "reconstruction"
            if exact_contenders <= 0:
                expected_probe_divergence = _oracle_expected_probe_path_divergence(
                    row
                )
                return expected_probe_divergence or "raster_coverage_omission"
            # Compare the winner only with the exact oracle identity. A broad
            # same-owner/object/material contender includes the wrong primitive
            # that already won, so using it made 53k finite-sample rows appear
            # to violate depth ordering without measuring the expected surface.
            nearest_exact_depth = float(row.get(
                "probe_raster_nearest_exact_identity_depth", 2.0
            ))
            winner_depth = float(row.get("probe_prepack_hardware_depth", 2.0))
            # The raster winner is impossible only when the exact-reference
            # contender is measurably nearer.  Equality and a farther exact
            # contender are both legal depth-test outcomes.
            if nearest_exact_depth + 1.0e-7 < winner_depth:
                return "raster_depth_order_invalid"
            return "raster_depth_winner"
        if candidate is None:
            return "candidate_pack"
        if expected != candidate:
            return "candidate_pack"
        if not bool(row.get("probe_candidate_selected_surface_valid", False)):
            return "vsb_selection"
        if expected != selected:
            return "vsb_selection"
    elif prepack is not None:
        if source_capture is None:
            # The current screen ray is empty, but the independent source-texel
            # ray can still reproduce the hardware winner exactly. That proves
            # the candidate's source identity is real while its reconstructed
            # footprint won outside current-camera visibility. Attribute the
            # first observable error to raster coverage rather than leaving a
            # known ghost row in the generic transport bucket.
            if (
                bool(row.get("probe_source_oracle_address_valid", False))
                and bool(row.get("probe_source_oracle_query_valid", False))
                and source_oracle is not None
                and source_oracle == prepack
            ):
                return "raster_depth_winner"
            return "source_capture_or_provenance_uninstrumented"
        if not bool(row.get("probe_source_oracle_query_valid", False)):
            return "source_capture_query_invalid"
        if source_oracle is None or source_oracle != source_capture:
            return _oracle_source_capture_write_stage(row)
        if source_capture != prepack:
            return "raster_winner_source_association"
        return "raster_depth_winner"
    elif candidate is not None:
        return "candidate_pack"
    elif bool(row.get("oracle_production_hit", False)):
        return "vsb_selection"
    product_owner = str(row.get("final_product_owner_name", "unknown"))
    product_geometry = final_product_status_is_valid(
        row.get("final_product_status")
    ) and product_owner not in {"", "sky", "unknown"}
    if bool(row.get("oracle_direct_hit", False)) != product_geometry:
        return "product"
    # Product and mask may own the earliest defect only after every explicit
    # capture, reconstruction, raster, pack, and b64 selection comparison above
    # has agreed. This ordering prevents a downstream witness from hiding an
    # upstream disagreement merely because the final screen classification was
    # already normalized as an agreement.
    if product_translation_error:
        return "product_translation"
    if glyph_mask_error:
        return "compositor_glyph_mask"
    return "downstream_identity_uninstrumented"


def _raster_winner_relation(row: dict) -> str:
    """Describe the measured depth relation without inventing a depth fault."""
    if not bool(row.get("oracle_direct_hit", False)):
        return "production_splat_outside_current_ray_visibility"
    if int(row.get("probe_raster_exact_identity_contender_count", 0)) <= 0:
        return "exact_reference_contender_absent"
    try:
        reference_depth = float(
            row.get("probe_raster_nearest_exact_identity_depth", float("nan"))
        )
        winner_depth = float(
            row.get("probe_prepack_hardware_depth", float("nan"))
        )
    except (TypeError, ValueError):
        return "depth_witness_invalid"
    if not math.isfinite(reference_depth) or not math.isfinite(winner_depth):
        return "depth_witness_invalid"
    if reference_depth + 1.0e-7 < winner_depth:
        return "exact_reference_nearer_depth_order_invalid"
    if winner_depth + 1.0e-7 < reference_depth:
        return "selected_splat_nearer_legal_depth_result"
    return "depth_tie_legal_depth_result"


def _oracle_components(
    frame_rows: list[dict], subcell_width: int, *, structural_only: bool = False
) -> list[dict]:
    wrong = {
        _oracle_subcell_xy(row, subcell_width): row
        for row in frame_rows
        if (
            _oracle_structural_geometry_error(row)
            if structural_only
            else str(row.get("oracle_classification", ""))
            not in {"identity_agreement", "absence_agreement"}
        )
    }
    wrong.pop(None, None)
    remaining = set(wrong)
    components: list[dict] = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        coordinates = [seed]
        while stack:
            x, y = stack.pop()
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
                    coordinates.append(neighbor)
        rows = [wrong[coordinate] for coordinate in coordinates]
        divergences = [_oracle_first_divergence(row) for row in rows]
        xs = [coordinate[0] for coordinate in coordinates]
        ys = [coordinate[1] for coordinate in coordinates]
        components.append(
            {
                "area_subcells": len(rows),
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "span_x": max(xs) - min(xs) + 1,
                "span_y": max(ys) - min(ys) + 1,
                "classifications": dict(
                    sorted(Counter(str(row.get("oracle_classification")) for row in rows).items())
                ),
                "first_divergence": dict(sorted(Counter(divergences).items())),
                "raster_winner_relations": dict(sorted(Counter(
                    _raster_winner_relation(row)
                    for row, divergence in zip(rows, divergences)
                    if divergence in {
                        "raster_depth_winner",
                        "raster_depth_order_invalid",
                    }
                ).items())),
                "reference_owners": sorted(
                    {
                        identity[0]
                        for row in rows
                        if (identity := _oracle_identity_tuple(row.get("oracle_identity")))
                        is not None
                    }
                ),
                "production_owners": sorted(
                    {
                        identity[0]
                        for row in rows
                        if (identity := _selected_identity_tuple(row)) is not None
                    }
                ),
                "sample_indices": sorted(int(row.get("sample_index", -1)) for row in rows),
                "tracking_keys": sorted(
                    {
                        key
                        for row in rows
                        if (key := _oracle_reference_key(row)) is not None
                    }
                ),
            }
        )
    return sorted(components, key=lambda component: component["area_subcells"], reverse=True)


def _connected_sample_components(
    sample_indices: set[int], subcell_width: int
) -> list[set[int]]:
    """Partition row-major subcell indices without wrapping across scanlines."""
    if subcell_width <= 0:
        return []
    coordinates = {
        (sample % subcell_width, sample // subcell_width): sample
        for sample in sample_indices
        if sample >= 0
    }
    remaining = set(coordinates)
    components: list[set[int]] = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        component = {coordinates[seed]}
        while stack:
            x, y = stack.pop()
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                stack.append(neighbor)
                component.add(coordinates[neighbor])
        components.append(component)
    return components


def _connected_sample_components_8(
    sample_indices: set[int], subcell_width: int
) -> list[set[int]]:
    """Partition one convex splat footprint while preserving diagonal pixels."""
    if subcell_width <= 0:
        return []
    coordinates = {
        (sample % subcell_width, sample // subcell_width): sample
        for sample in sample_indices
        if sample >= 0
    }
    remaining = set(coordinates)
    components: list[set[int]] = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        component = {coordinates[seed]}
        while stack:
            x, y = stack.pop()
            for offset_y in (-1, 0, 1):
                for offset_x in (-1, 0, 1):
                    if offset_x == 0 and offset_y == 0:
                        continue
                    neighbor = (x + offset_x, y + offset_y)
                    if neighbor not in remaining:
                        continue
                    remaining.remove(neighbor)
                    stack.append(neighbor)
                    component.add(coordinates[neighbor])
        components.append(component)
    return components


def _candidate_source_tuple(row: dict) -> tuple | None:
    identity = _oracle_identity_tuple(row.get("probe_prepack_identity")) \
        if bool(row.get("probe_prepack_valid", False)) else None
    provenance = row.get("probe_prepack_provenance")
    if identity is None or not isinstance(provenance, list) or len(provenance) < 2:
        return None
    try:
        return (*identity, int(provenance[0]), int(provenance[1]))
    except (TypeError, ValueError):
        return None


def _candidate_tuple_duplicate_components(
    frame_rows: list[dict], subcell_width: int
) -> int:
    """A single reconstructed source quad cannot own disjoint screen islands."""
    samples_by_tuple: dict[tuple, set[int]] = {}
    for row in frame_rows:
        source_tuple = _candidate_source_tuple(row)
        if source_tuple is None:
            continue
        sample_index = int(row.get("sample_index", -1))
        samples_by_tuple.setdefault(source_tuple, set()).add(sample_index)
    return sum(
        max(0, len(_connected_sample_components_8(samples, subcell_width)) - 1)
        for samples in samples_by_tuple.values()
    )


def _oracle_footprint_movement_counts(
    frame_rows: list[dict], subcell_width: int, *, semantic_identity: bool
) -> tuple[int, int]:
    """Count disconnected and displaced footprints in one identity domain."""
    expected_by_identity: dict[tuple, set[int]] = {}
    actual_by_identity: dict[tuple, set[int]] = {}
    for row in frame_rows:
        sample = int(row.get("sample_index", -1))
        expected = _oracle_identity_tuple(row.get("oracle_identity"))
        actual = _selected_identity_tuple(row)
        if semantic_identity:
            expected = (
                (expected[0], expected[1], expected[3])
                if expected is not None else None
            )
            actual = (
                (actual[0], actual[1], actual[3])
                if actual is not None else None
            )
        if bool(row.get("oracle_direct_hit", False)) and expected is not None:
            expected_by_identity.setdefault(expected, set()).add(sample)
        if bool(row.get("oracle_production_hit", False)) and actual is not None:
            actual_by_identity.setdefault(actual, set()).add(sample)

    duplicate_components = 0
    out_of_place_identities = 0
    for identity in expected_by_identity.keys() & actual_by_identity.keys():
        expected_cells = expected_by_identity[identity]
        actual_cells = actual_by_identity[identity]
        extras = actual_cells - expected_cells
        missing = expected_cells - actual_cells
        actual_components = _connected_sample_components(
            actual_cells, subcell_width
        )
        has_reference_component = any(
            component & expected_cells for component in actual_components
        )
        if has_reference_component:
            duplicate_components += sum(
                1
                for component in actual_components
                if component.isdisjoint(expected_cells)
            )
        if extras and missing:
            out_of_place_identities += 1
    return duplicate_components, out_of_place_identities


def summarize_full_grid_temporal_oracle(
    rows: list[dict], runtime_receipt: dict | None = None, details_limit: int = 100
) -> dict:
    """Grade normalized spatial identity and stable-anchor behavior over time."""
    oracle_rows = [
        row for row in rows if row.get("oracle_schema") == SAME_FRAME_ORACLE_SCHEMA
    ]
    coverage = _same_frame_oracle_coverage(oracle_rows, runtime_receipt or {})
    by_frame: dict[int, list[dict]] = {}
    for row in oracle_rows:
        by_frame.setdefault(int(row.get("frame_index", -1)), []).append(row)

    owner_flow: Counter = Counter()
    mismatch_kinds: Counter = Counter()
    generation_table: dict[str, Counter] = {}
    first_divergence: Counter = Counter()
    structural_first_divergence: Counter = Counter()
    raster_winner_relations: Counter = Counter()
    structural_raster_winner_relations: Counter = Counter()
    normalization_invalid = 0
    raster_witness_invalid = 0
    source_physical_join_invalid = 0
    finite_splat_admissibility_reasons: Counter = Counter()
    finite_splat_admissible_rows = 0
    finite_splat_inadmissible_rows = 0
    finite_splat_projected_coverage: Counter = Counter()
    finite_envelope_projected_coverage: Counter = Counter()
    glyph_split_cells: set[tuple[int, int, int]] = set()
    complete_glyph_mask_rows = 0
    incomplete_glyph_mask_rows = 0
    unavailable_glyph_mask_rows = 0
    product_exact_identity_rows = 0
    product_exact_identity_unavailable_rows = 0
    product_translation_error_rows = 0
    product_translation_kinds: Counter = Counter()
    expected_center_witness: Counter = Counter()
    expected_center_neighbor_offsets: Counter = Counter()
    expected_center_capture_depth_abs_residuals: list[float] = []
    expected_center_capture_depth_relative_residuals: list[float] = []
    expected_center_neighbor_angular_residuals: list[float] = []
    expected_center_neighbor_depth_relative_residuals: list[float] = []
    unclassified = 0
    # FL-4359 continuity law (parallel population; see ORACLE_REFERENCE_LAW_*).
    # Every counter here is computed alongside — never instead of — the strict
    # counters in the same loop, so the two partitions cover identical rows.
    continuity_classes: Counter = Counter()
    continuity_defect_reasons: Counter = Counter()
    continuity_where_classes: Counter = Counter()
    answer_source_epochs: Counter = Counter()
    reference_owners: Counter = Counter()
    continuity_defect_rows = 0
    continuity_retained_rows = 0
    continuity_retained_defect_rows = 0
    # Tolerance-zero control (audit blueprint 7.4, control 2): the SAME rows
    # regraded with the continuity tolerance forced to zero must reproduce the
    # strict exact-identity counts. If they do not, the tolerance is not being
    # applied where it is claimed and the run is void.
    tolerance_zero_defect_rows = 0
    row_continuity_class: dict[int, str] = {}
    for row in oracle_rows:
        classification = str(row.get("oracle_classification", ""))
        continuity_verdict = _oracle_continuity_class(row)
        continuity_classes[continuity_verdict["class"]] += 1
        continuity_where_classes[continuity_verdict["where_reanchor"]] += 1
        answer_source_epochs[continuity_verdict["answer_source_epoch"]] += 1
        reference_owners[_oracle_reference_owner(row)] += 1
        row_continuity_class[id(row)] = continuity_verdict["class"]
        if continuity_verdict["retained_authored"]:
            continuity_retained_rows += 1
        if continuity_verdict["defect"]:
            continuity_defect_rows += 1
            continuity_defect_reasons[continuity_verdict["class"]] += 1
            if continuity_verdict["retained_authored"]:
                continuity_retained_defect_rows += 1
        if _oracle_structural_geometry_error_continuity(row, tolerance_scale=0.0):
            tolerance_zero_defect_rows += 1
        if classification not in {
            "identity_agreement",
            "identity_mismatch",
            "omission",
            "ghost_or_commission",
            "absence_agreement",
            "oracle_query_invalid",
        }:
            unclassified += 1
        expected = _oracle_identity_tuple(row.get("oracle_identity"))
        actual = _selected_identity_tuple(row)
        if bool(row.get("oracle_direct_hit", False)) and expected is None:
            normalization_invalid += 1
        if bool(row.get("oracle_production_hit", False)) and actual is None:
            normalization_invalid += 1
        if bool(row.get("probe_raster_contender_overflow", True)):
            raster_witness_invalid += 1
        elif bool(row.get("probe_prepack_valid", False)) and int(
            row.get("probe_raster_selected_identity_contender_count", 0)
        ) <= 0:
            raster_witness_invalid += 1
        if bool(row.get("probe_prepack_valid", False)) and (
            not bool(row.get("probe_source_capture_band_map_valid", False))
            or int(row.get("probe_source_capture_join_flags", 0)) != 0x7F
        ):
            source_physical_join_invalid += 1
        if expected is not None and actual is not None:
            owner_flow[f"{expected[0]}->{actual[0]}"] += 1
            if classification == "identity_mismatch":
                mismatch_kinds[
                    _oracle_mismatch_classification(expected, actual)
                ] += 1
        source_probe = int(row.get("probe_candidate_source_probe", -1))
        generation_label = {0: "EYE", 1: "CURRENT", 2: "PREVIOUS"}.get(
            source_probe, "ABSENT_OR_UNKNOWN"
        )
        bucket = generation_table.setdefault(generation_label, Counter())
        bucket[
            {
                "identity_agreement": "agreement",
                "identity_mismatch": "substitution",
                "ghost_or_commission": "ghost",
                "omission": "omission",
                "absence_agreement": "absence_agreement",
            }.get(classification, "invalid")
        ] += 1
        divergence = _oracle_first_divergence(row)
        first_divergence[divergence] += 1
        if divergence in {
            "raster_depth_winner",
            "raster_depth_order_invalid",
        }:
            raster_winner_relations[_raster_winner_relation(row)] += 1
        center_state = _oracle_expected_center_classification(row)
        expected_center_witness[center_state] += 1
        if bool(row.get("probe_expected_center_neighbor_valid", False)):
            expected_center_neighbor_offsets[
                f"{int(row.get('probe_expected_center_neighbor_dx', 0))},"
                f"{int(row.get('probe_expected_center_neighbor_dy', 0))}"
            ] += 1
        for field, output in (
            (
                "probe_expected_center_capture_depth_abs_residual",
                expected_center_capture_depth_abs_residuals,
            ),
            (
                "probe_expected_center_capture_depth_relative_residual",
                expected_center_capture_depth_relative_residuals,
            ),
            (
                "probe_expected_center_neighbor_angular_residual",
                expected_center_neighbor_angular_residuals,
            ),
            (
                "probe_expected_center_neighbor_depth_relative_residual",
                expected_center_neighbor_depth_relative_residuals,
            ),
        ):
            try:
                value = float(row.get(field, float("nan")))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                output.append(value)
        finite_splat = _finite_splat_admissibility(row)
        if bool(finite_splat.get("applicable", False)):
            reason = str(finite_splat.get("reason", "unclassified"))
            finite_splat_admissibility_reasons[reason] += 1
            projected = _projected_quad_subcell_coverage(row)
            projected_key = (
                "covered"
                if bool(projected.get("available", False))
                    and bool(projected.get("covers", False))
                else "outside"
                if bool(projected.get("available", False))
                else "unavailable"
            )
            finite_splat_projected_coverage[projected_key] += 1
            if reason == "current_ray_outside_finite_quad_envelope":
                finite_envelope_projected_coverage[projected_key] += 1
            if bool(finite_splat.get("admissible", False)):
                finite_splat_admissible_rows += 1
            else:
                finite_splat_inadmissible_rows += 1
        if _oracle_structural_geometry_error(row):
            structural_first_divergence[divergence] += 1
            if divergence in {
                "raster_depth_winner",
                "raster_depth_order_invalid",
            }:
                structural_raster_winner_relations[
                    _raster_winner_relation(row)
                ] += 1
        if int(row.get("final_paint_mask_contributor_count", 0)) > 1:
            cell = row.get("ansi_cell", {})
            if isinstance(cell, dict):
                glyph_split_cells.add(
                    (int(row.get("frame_index", -1)), int(cell.get("x", -1)), int(cell.get("y", -1)))
                )
        mask_witness = _complete_glyph_mask_witness(row)
        if bool(mask_witness.get("complete", False)):
            complete_glyph_mask_rows += 1
        elif bool(mask_witness.get("available", False)):
            incomplete_glyph_mask_rows += 1
        else:
            unavailable_glyph_mask_rows += 1
        product_witness = _product_exact_identity_witness(row)
        if bool(product_witness.get("available", False)):
            product_exact_identity_rows += 1
            product_translation_kinds[
                str(product_witness.get("translation_kind") or "missing")
            ] += 1
            if not bool(product_witness.get("translation_equal", False)):
                product_translation_error_rows += 1
        else:
            product_exact_identity_unavailable_rows += 1

    frame_keys: dict[int, dict[tuple, list[dict]]] = {}
    ansi_sizes = coverage.get("same_frame_oracle_ansi_sizes", [])
    subcell_width = int(ansi_sizes[0][0]) * 2 if ansi_sizes else 0
    for frame_id, frame_rows in by_frame.items():
        groups: dict[tuple, list[dict]] = {}
        for row in frame_rows:
            key = _oracle_reference_key(row)
            if key is not None:
                groups.setdefault(key, []).append(row)
        frame_keys[frame_id] = groups

    comparable_reference_key_pairs = 0
    changing_reference_key_state_pairs = 0
    reference_identity_set_changes = 0
    reference_defect_state_changes = 0
    # Continuity-law twins of the three counters above.
    changing_reference_key_state_pairs_continuity = 0
    reference_identity_set_changes_continuity = 0
    reference_defect_state_changes_continuity = 0
    source_probe_set_changes = 0
    source_generation_set_flips = 0
    source_texel_set_changes = 0
    exact_probe_texel_generation_tuple_changes = 0
    source_probe_transition_matrix: Counter = Counter()
    generation_misregistration_contingency: Counter = Counter()
    source_probe_misregistration_contingency: Counter = Counter()
    unmatched_reference_keys_from_previous = 0
    unmatched_reference_keys_in_current = 0
    displacement_rows: list[dict] = []
    episodes: list[dict] = []
    all_keys = sorted({key for groups in frame_keys.values() for key in groups})
    sorted_frames = sorted(frame_keys)
    for key in all_keys:
        # (frame_id, strict_defect, exact identity set, source probes,
        #  generations, texels, exact source tuples, centroid,
        #  continuity_defect, continuity identity set)
        states: list[
            tuple[
                int, bool, set[tuple], set[int], set[int], set[int], set[tuple],
                tuple[float, float] | None, bool, set[tuple],
            ]
        ] = []
        for frame_id in sorted_frames:
            group = frame_keys[frame_id].get(key, [])
            if not group:
                continue
            actual_identities = {
                identity for row in group if (identity := _selected_identity_tuple(row)) is not None
            }
            generations = {
                int(row.get("probe_candidate_source_generation", -1)) for row in group
            }
            source_probes = {
                int(row.get("probe_candidate_source_probe", -1)) for row in group
            }
            source_texels = {
                int(row.get("probe_candidate_source_texel", -1)) for row in group
            }
            exact_source_tuples = {
                (
                    int(row.get("probe_candidate_source_probe", -1)),
                    int(row.get("probe_candidate_source_texel", -1)),
                    int(row.get("probe_candidate_source_generation", -1)),
                )
                for row in group
            }
            coordinates = [
                _oracle_subcell_xy(row, subcell_width)
                for row in group
            ]
            coordinates = [coordinate for coordinate in coordinates if coordinate is not None]
            centroid = (
                (sum(x for x, _ in coordinates) / len(coordinates), sum(y for _, y in coordinates) / len(coordinates))
                if coordinates else None
            )
            expected_identity = tuple(int(value) for value in key[:4])
            defect = actual_identities != {expected_identity}
            # Continuity-law twin of the same reference-key state. A key is in
            # a defect state only when at least one of its rows is a REAL
            # defect; a WHERE re-anchor or a continuous retained substitution
            # leaves the key stable. Computed alongside `defect`, never
            # replacing it.
            defect_continuity = any(
                row_continuity_class.get(id(row), "").startswith("real_defect_")
                for row in group
            )
            # Continuity-law identity domain: owner/object/material only, so a
            # by-design primitive re-address does not read as an identity flip.
            continuity_identities = {
                (identity[0], identity[1], identity[3])
                for row in group
                if (identity := _selected_identity_tuple(row)) is not None
            }
            states.append(
                (
                    frame_id, defect, actual_identities, source_probes,
                    generations, source_texels, exact_source_tuples, centroid,
                    defect_continuity, continuity_identities,
                )
            )
        for previous, current in zip(states, states[1:]):
            if current[0] != previous[0] + 1:
                continue
            comparable_reference_key_pairs += 1
            identity_changed = previous[2] != current[2]
            defect_state_changed = previous[1] != current[1]
            if identity_changed:
                reference_identity_set_changes += 1
            if defect_state_changed:
                reference_defect_state_changes += 1
            if identity_changed or defect_state_changed:
                changing_reference_key_state_pairs += 1
            identity_changed_continuity = previous[9] != current[9]
            defect_state_changed_continuity = previous[8] != current[8]
            if identity_changed_continuity:
                reference_identity_set_changes_continuity += 1
            if defect_state_changed_continuity:
                reference_defect_state_changes_continuity += 1
            if identity_changed_continuity or defect_state_changed_continuity:
                changing_reference_key_state_pairs_continuity += 1
            if previous[3] != current[3]:
                source_probe_set_changes += 1
            current_misregistered = any(
                actual[0] == key[0]
                and actual[1] == key[1]
                and actual[2] != key[2]
                for actual in current[2]
            )
            generation_misregistration_contingency[
                (
                    "changed" if previous[4] != current[4] else "stable"
                )
                + "_and_"
                + ("misregistered" if current_misregistered else "not_misregistered")
            ] += 1
            source_probe_misregistration_contingency[
                (
                    "changed" if previous[3] != current[3] else "stable"
                )
                + "_and_"
                + ("misregistered" if current_misregistered else "not_misregistered")
            ] += 1
            previous_probe_label = "+".join(
                {0: "EYE", 1: "CURRENT", 2: "PREVIOUS"}.get(value, "UNKNOWN")
                for value in sorted(previous[3])
            ) or "ABSENT"
            current_probe_label = "+".join(
                {0: "EYE", 1: "CURRENT", 2: "PREVIOUS"}.get(value, "UNKNOWN")
                for value in sorted(current[3])
            ) or "ABSENT"
            source_probe_transition_matrix[
                f"{previous_probe_label}->{current_probe_label}"
            ] += 1
            # This compares numeric generation-ID sets for one stable reference
            # anchor. It does not compare the CURRENT and PREVIOUS probe slots;
            # those owners are reported separately by generation_table.
            if previous[4] != current[4]:
                source_generation_set_flips += 1
            if previous[5] != current[5]:
                source_texel_set_changes += 1
            if previous[6] != current[6]:
                exact_probe_texel_generation_tuple_changes += 1
            if previous[7] is not None and current[7] is not None and len(displacement_rows) < details_limit:
                displacement_rows.append(
                    {
                        "reference_key": list(key),
                        "frame_a": previous[0],
                        "frame_b": current[0],
                        "screen_centroid_delta": [
                            current[7][0] - previous[7][0],
                            current[7][1] - previous[7][1],
                        ],
                        "production_identity_changed": identity_changed,
                        "defect_state_changed": defect_state_changed,
                        "source_probe_changed": previous[3] != current[3],
                        "source_generation_changed": previous[4] != current[4],
                        "source_texel_changed": previous[5] != current[5],
                        "exact_probe_texel_generation_tuple_changed": (
                            previous[6] != current[6]
                        ),
                    }
                )
        open_start: int | None = None
        last_frame: int | None = None
        for frame_id, defect, *_ in states:
            if defect and open_start is None:
                open_start = frame_id
            if open_start is not None and (not defect or (last_frame is not None and frame_id != last_frame + 1)):
                episodes.append(
                    {
                        "reference_key": list(key),
                        "onset_frame": open_start,
                        "recovery_frame": frame_id if not defect else None,
                        "last_defect_frame": last_frame,
                    }
                )
                open_start = frame_id if defect else None
            last_frame = frame_id
        if open_start is not None:
            episodes.append(
                {
                    "reference_key": list(key),
                    "onset_frame": open_start,
                    "recovery_frame": None,
                    "last_defect_frame": last_frame,
                }
            )

    for previous_frame, current_frame in zip(sorted_frames, sorted_frames[1:]):
        if current_frame != previous_frame + 1:
            continue
        previous_keys = set(frame_keys[previous_frame])
        current_keys = set(frame_keys[current_frame])
        unmatched_reference_keys_from_previous += len(previous_keys - current_keys)
        unmatched_reference_keys_in_current += len(current_keys - previous_keys)

    components_by_frame = {
        str(frame_id): _oracle_components(frame_rows, subcell_width)
        for frame_id, frame_rows in sorted(by_frame.items())
    }
    structural_components_by_frame = {
        str(frame_id): _oracle_components(
            frame_rows, subcell_width, structural_only=True
        )
        for frame_id, frame_rows in sorted(by_frame.items())
    }
    candidate_tuple_duplicate_components = sum(
        _candidate_tuple_duplicate_components(frame_rows, subcell_width)
        for frame_rows in by_frame.values()
    )

    def public_component(component: dict) -> dict:
        """Remove tracking-only state and bound evidence samples in receipts."""
        public = {
            key: value
            for key, value in component.items()
            if key not in {"tracking_keys", "sample_indices"}
        }
        samples = list(component.get("sample_indices", []))
        public["sample_count"] = len(samples)
        public["sample_indices"] = samples[:64]
        return public

    component_rows = [
        (frame_id, component)
        for frame_id, components in components_by_frame.items()
        for component in components
    ]
    structural_component_rows = [
        (frame_id, component)
        for frame_id, components in structural_components_by_frame.items()
        for component in components
    ]
    large_components = [
        {"frame_id": int(frame_id), **public_component(component)}
        for frame_id, component in component_rows
        if component["area_subcells"] >= 4
        and (component["span_x"] >= 4 or component["span_y"] >= 4)
    ]
    structural_large_components = [
        {"frame_id": int(frame_id), **public_component(component)}
        for frame_id, component in structural_component_rows
        if component["area_subcells"] >= 4
        and (component["span_x"] >= 4 or component["span_y"] >= 4)
    ]
    # Track wrong components by overlapping normalized reference anchors. This
    # follows a displaced world component through camera motion instead of
    # pretending fixed screen coordinates are stable identities.
    next_track_id = 1
    active_tracks: dict[int, dict] = {}
    finished_tracks: list[dict] = []
    for frame_id in sorted(by_frame):
        components = components_by_frame[str(frame_id)]
        unmatched_tracks = set(active_tracks)
        for component in components:
            keys = {tuple(value) for value in component.get("tracking_keys", [])}
            best_track = None
            best_overlap = 0
            for track_id in unmatched_tracks:
                track_keys = active_tracks[track_id]["last_keys"]
                overlap = len(keys & track_keys)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_track = track_id
            if best_track is None:
                track_id = next_track_id
                next_track_id += 1
                active_tracks[track_id] = {
                    "track_id": track_id,
                    "onset_frame": frame_id,
                    "last_frame": frame_id,
                    "frame_count": 1,
                    "max_area_subcells": int(component["area_subcells"]),
                    "last_keys": keys,
                }
            else:
                track = active_tracks[best_track]
                track["last_frame"] = frame_id
                track["frame_count"] += 1
                track["max_area_subcells"] = max(
                    int(track["max_area_subcells"]), int(component["area_subcells"])
                )
                track["last_keys"] = keys
                unmatched_tracks.remove(best_track)
        for track_id in list(unmatched_tracks):
            track = active_tracks.pop(track_id)
            track["recovery_frame"] = frame_id
            track.pop("last_keys", None)
            finished_tracks.append(track)
    for track in active_tracks.values():
        track = dict(track)
        track["recovery_frame"] = None
        track.pop("last_keys", None)
        finished_tracks.append(track)
    finished_tracks.sort(
        key=lambda track: (int(track["frame_count"]), int(track["max_area_subcells"])),
        reverse=True,
    )

    duplicate_components = 0
    out_of_place_identities = 0
    structural_duplicate_components = 0
    structural_out_of_place_identities = 0
    for frame_rows in by_frame.values():
        exact_duplicate, exact_out_of_place = _oracle_footprint_movement_counts(
            frame_rows, subcell_width, semantic_identity=False
        )
        semantic_duplicate, semantic_out_of_place = \
            _oracle_footprint_movement_counts(
                frame_rows, subcell_width, semantic_identity=True
            )
        duplicate_components += exact_duplicate
        out_of_place_identities += exact_out_of_place
        structural_duplicate_components += semantic_duplicate
        structural_out_of_place_identities += semantic_out_of_place
    unknown_divergence = sum(
        count
        for name, count in first_divergence.items()
        if name.startswith("unknown_")
        or name.endswith("_uninstrumented")
        or name.endswith("_query_invalid")
        or name.endswith("_invalid")
    )
    geometry_counts = Counter(
        str(row.get("oracle_classification", "oracle_record_missing"))
        for row in oracle_rows
    )
    geometry_errors = sum(
        geometry_counts.get(name, 0)
        for name in ("omission", "ghost_or_commission", "identity_mismatch")
    )
    identity_mismatch_rows = int(geometry_counts.get("identity_mismatch", 0))
    omission_rows = int(geometry_counts.get("omission", 0))
    ghost_rows = int(geometry_counts.get("ghost_or_commission", 0))
    finite_sample_substitution_rows = int(mismatch_kinds.get(
        "same_object_same_material_different_primitive_sample", 0
    ))
    structural_identity_mismatch_rows = (
        identity_mismatch_rows
        - finite_splat_admissible_rows
    )
    structural_geometry_error_rows = (
        structural_identity_mismatch_rows + omission_rows + ghost_rows
    )
    mismatch_decomposition_total = sum(int(value) for value in mismatch_kinds.values())
    geometry_decomposition_total = (
        mismatch_decomposition_total + omission_rows + ghost_rows
    )
    candidate_pack_rejection_reasons = Counter(
        int(row.get("probe_candidate_pack_rejection_reason", 0))
        for row in oracle_rows
        if int(row.get("probe_candidate_pack_rejection_reason", 0)) != 0
    )
    product_exact_identity_available = bool(oracle_rows) and (
        product_exact_identity_rows == len(oracle_rows)
    )
    complete_glyph_mask_hash_available = bool(oracle_rows) and (
        unavailable_glyph_mask_rows == 0
    )
    temporal_valid = bool(coverage.get("same_frame_oracle_temporal_sequence_valid", False))

    # -----------------------------------------------------------------
    # FL-4359 continuity-law receipts and their mandatory controls.
    # -----------------------------------------------------------------
    query_invalid_rows = int(geometry_counts.get("oracle_query_invalid", 0))
    # The Tier-0 population the tolerance-zero control must reproduce: every
    # row the strict exact-identity law calls anything other than an agreement.
    strict_defect_rows_control = (
        identity_mismatch_rows + omission_rows + ghost_rows
        + query_invalid_rows + unclassified
    )
    tolerance_zero_control_valid = (
        tolerance_zero_defect_rows == strict_defect_rows_control
    )
    # Blueprint 7.4 control 2's falsifier: if tolerance-zero and tolerance-on
    # score the same on a capture that HAS a strict-defect population, the
    # tolerance is not being applied and the run is void. A capture with no
    # strict defect at all never exercises the tolerance, so the check is
    # reported as unexercised rather than asserted against an empty set.
    tolerance_exercised = strict_defect_rows_control > 0
    tolerance_is_load_bearing = (
        not tolerance_exercised
        or tolerance_zero_defect_rows != continuity_defect_rows
    )
    continuity_by_design_rows = (
        int(continuity_classes.get("where_reanchor_address_only", 0))
        + int(continuity_classes.get("retained_substitution_continuous", 0))
        + int(continuity_classes.get("current_ray_continuous_resample", 0))
    )
    continuity_decomposition_valid = (
        continuity_by_design_rows + continuity_defect_rows
        + int(continuity_classes.get("absence_agreement", 0))
        + int(continuity_classes.get("exact_identity_agreement", 0))
    ) == len(oracle_rows)
    retained_row_fraction = (
        continuity_retained_rows / len(oracle_rows) if oracle_rows else 0.0
    )
    # Blueprint 7.4 control 1: an arm that is almost entirely retained-authored
    # AND reports no defect at all is suspected of grading its own output. The
    # analysis marks itself invalid rather than publishing that result.
    reference_collapse_suspected = (
        bool(oracle_rows)
        and retained_row_fraction > ORACLE_REFERENCE_COLLAPSE_RETAINED_FRACTION
        and continuity_defect_rows == 0
    )
    unproven_reference_rows = int(reference_owners.get("unproven", 0))
    # FL-4359 blueprint 7.4 control 1, second half. The collapse guard above only
    # fires on an all-retained zero-defect arm, and a row graded as an agreement
    # never reaches the continuity relation's `reference_owner_unproven` refusal,
    # so an unproven reference row can otherwise pass silently and the controls
    # would still publish `valid`. Requiring zero unproven reference rows makes
    # the controls fail shut: an unprovable reference voids the verdict instead
    # of grading itself (_oracle_reference_owner()).
    continuity_law_controls_valid = bool(
        oracle_rows
        and tolerance_zero_control_valid
        and tolerance_is_load_bearing
        and continuity_decomposition_valid
        and not reference_collapse_suspected
        and unproven_reference_rows == 0
    )

    component_receipts = {
        frame_id: {
            "component_count": len(components),
            "largest": [
                public_component(component)
                for component in components[: min(details_limit, 16)]
            ],
        }
        for frame_id, components in components_by_frame.items()
    }
    return {
        "full_grid_temporal_oracle_profile": FULL_GRID_TEMPORAL_ORACLE_PROFILE,
        "full_grid_temporal_oracle_valid": temporal_valid,
        "full_grid_temporal_oracle_unclassified_subcells": unclassified,
        "full_grid_temporal_oracle_normalization_invalid_rows": normalization_invalid,
        "full_grid_temporal_oracle_raster_witness_invalid_rows": (
            raster_witness_invalid
        ),
        "full_grid_temporal_oracle_source_physical_join_invalid_rows": (
            source_physical_join_invalid
        ),
        "full_grid_temporal_oracle_owner_flow_matrix": dict(sorted(owner_flow.items())),
        "full_grid_temporal_oracle_mismatch_decomposition": dict(sorted(mismatch_kinds.items())),
        "full_grid_temporal_oracle_generation_table": {
            key: dict(sorted(value.items())) for key, value in sorted(generation_table.items())
        },
        "full_grid_temporal_oracle_geometry_error_rows": geometry_errors,
        # Exact identity disagreement remains durable evidence. The structural
        # channel removes only finite same-object/material primitive sampling,
        # which a texel splat necessarily performs, so slab/ghost ownership is
        # not inflated by the renderer's declared angular discretization.
        "full_grid_temporal_oracle_finite_sample_substitution_rows": (
            finite_sample_substitution_rows
        ),
        "full_grid_temporal_oracle_finite_splat_admissible_rows": (
            finite_splat_admissible_rows
        ),
        "full_grid_temporal_oracle_finite_splat_inadmissible_rows": (
            finite_splat_inadmissible_rows
        ),
        "full_grid_temporal_oracle_finite_splat_admissibility_reasons": dict(
            sorted(finite_splat_admissibility_reasons.items())
        ),
        "full_grid_temporal_oracle_finite_splat_projected_coverage": dict(
            sorted(finite_splat_projected_coverage.items())
        ),
        "full_grid_temporal_oracle_finite_envelope_projected_coverage": dict(
            sorted(finite_envelope_projected_coverage.items())
        ),
        "full_grid_temporal_oracle_finite_splat_decomposition_valid": (
            finite_splat_admissible_rows + finite_splat_inadmissible_rows
            == finite_sample_substitution_rows
        ),
        "full_grid_temporal_oracle_structural_identity_mismatch_rows": (
            structural_identity_mismatch_rows
        ),
        "full_grid_temporal_oracle_structural_geometry_error_rows": (
            structural_geometry_error_rows
        ),
        # ---- FL-4359 continuity law (parallel population; strict stays
        # authoritative unless --oracle-reference-law continuity_v1 selects
        # the continuity variants as the blocking set) ---------------------
        "full_grid_temporal_oracle_continuity_law": (
            ORACLE_REFERENCE_LAW_CONTINUITY
        ),
        "full_grid_temporal_oracle_continuity_tolerance": {
            "normal_min_cos": ORACLE_CONTINUITY_NORMAL_MIN_COS,
            "plane_tolerance_spans": ORACLE_CONTINUITY_PLANE_TOLERANCE_SPANS,
            "plane_tolerance_floor": ORACLE_CONTINUITY_PLANE_TOLERANCE_FLOOR,
            "source": (
                "visible_surface_builder.glsl:4423-4442 "
                "fl4359_retained_surface_continuous"
            ),
        },
        "full_grid_temporal_oracle_continuity_classification": dict(
            sorted(continuity_classes.items())
        ),
        "full_grid_temporal_oracle_continuity_defect_rows": (
            continuity_defect_rows
        ),
        "full_grid_temporal_oracle_continuity_defect_reasons": dict(
            sorted(continuity_defect_reasons.items())
        ),
        "full_grid_temporal_oracle_continuity_by_design_rows": (
            continuity_by_design_rows
        ),
        "full_grid_temporal_oracle_continuity_where_reanchor_rows": int(
            continuity_classes.get("where_reanchor_address_only", 0)
        ),
        "full_grid_temporal_oracle_continuity_where_reanchor_classes": dict(
            sorted(continuity_where_classes.items())
        ),
        "full_grid_temporal_oracle_continuity_retained_substitution_rows": int(
            continuity_classes.get("retained_substitution_continuous", 0)
        ),
        "full_grid_temporal_oracle_continuity_current_ray_resample_rows": int(
            continuity_classes.get("current_ray_continuous_resample", 0)
        ),
        "full_grid_temporal_oracle_continuity_retained_authored_rows": (
            continuity_retained_rows
        ),
        "full_grid_temporal_oracle_continuity_retained_defect_rows": (
            continuity_retained_defect_rows
        ),
        "full_grid_temporal_oracle_answer_source_epoch_counts": dict(
            sorted(answer_source_epochs.items())
        ),
        "full_grid_temporal_oracle_reference_owner_counts": dict(
            sorted(reference_owners.items())
        ),
        "full_grid_temporal_oracle_reference_owner": (
            "current_ray_scenequery"
            if bool(oracle_rows) and unproven_reference_rows == 0
            else "unproven"
        ),
        "full_grid_temporal_oracle_continuity_decomposition_valid": (
            continuity_decomposition_valid
        ),
        "full_grid_temporal_oracle_continuity_controls": {
            "tolerance_zero_defect_rows": tolerance_zero_defect_rows,
            "strict_defect_rows": strict_defect_rows_control,
            "tolerance_zero_reproduces_strict": tolerance_zero_control_valid,
            "tolerance_exercised": tolerance_exercised,
            "tolerance_is_load_bearing": tolerance_is_load_bearing,
            "continuity_defect_rows": continuity_defect_rows,
            "retained_authored_row_fraction": retained_row_fraction,
            "reference_collapse_suspected": reference_collapse_suspected,
            "unproven_reference_rows": unproven_reference_rows,
            "decomposition_valid": continuity_decomposition_valid,
            "valid": continuity_law_controls_valid,
            "scope": (
                "audit blueprint 7.4 controls: tolerance-zero reproduction, "
                "tolerance load-bearing check, reference-collapse guard, "
                "zero unproven reference rows"
            ),
        },
        "full_grid_temporal_oracle_continuity_controls_valid": (
            continuity_law_controls_valid
        ),
        "full_grid_temporal_oracle_continuity_structural_geometry_error_rows": (
            continuity_defect_rows
        ),
        "full_grid_temporal_oracle_structural_first_divergence": dict(
            sorted(structural_first_divergence.items())
        ),
        "full_grid_temporal_oracle_identity_mismatch_rows": identity_mismatch_rows,
        "full_grid_temporal_oracle_omission_rows": omission_rows,
        "full_grid_temporal_oracle_ghost_rows": ghost_rows,
        "full_grid_temporal_oracle_mismatch_decomposition_total": (
            mismatch_decomposition_total
        ),
        "full_grid_temporal_oracle_mismatch_decomposition_valid": (
            mismatch_decomposition_total == identity_mismatch_rows
        ),
        "full_grid_temporal_oracle_geometry_decomposition_total": (
            geometry_decomposition_total
        ),
        "full_grid_temporal_oracle_geometry_decomposition_valid": (
            geometry_decomposition_total == geometry_errors
        ),
        "full_grid_temporal_oracle_candidate_pack_rejection_reasons": {
            str(reason): count
            for reason, count in sorted(candidate_pack_rejection_reasons.items())
        },
        "full_grid_temporal_oracle_product_exact_identity_available": (
            product_exact_identity_available
        ),
        "full_grid_temporal_oracle_complete_glyph_mask_hash_available": (
            complete_glyph_mask_hash_available
        ),
        "full_grid_temporal_oracle_product_exact_identity_rows": (
            product_exact_identity_rows
        ),
        "full_grid_temporal_oracle_product_exact_identity_unavailable_rows": (
            product_exact_identity_unavailable_rows
        ),
        "full_grid_temporal_oracle_product_translation_error_rows": (
            product_translation_error_rows
        ),
        "full_grid_temporal_oracle_product_translation_kinds": dict(
            sorted(product_translation_kinds.items())
        ),
        "full_grid_temporal_oracle_complete_glyph_mask_rows": (
            complete_glyph_mask_rows
        ),
        "full_grid_temporal_oracle_incomplete_glyph_mask_rows": (
            incomplete_glyph_mask_rows
        ),
        "full_grid_temporal_oracle_unavailable_glyph_mask_rows": (
            unavailable_glyph_mask_rows
        ),
        "full_grid_temporal_oracle_first_divergence": dict(sorted(first_divergence.items())),
        "full_grid_temporal_oracle_first_divergence_unknown": unknown_divergence,
        "full_grid_temporal_oracle_raster_winner_relations": dict(
            sorted(raster_winner_relations.items())
        ),
        "full_grid_temporal_oracle_raster_winner_relation_total": sum(
            raster_winner_relations.values()
        ),
        "full_grid_temporal_oracle_structural_raster_winner_relations": dict(
            sorted(structural_raster_winner_relations.items())
        ),
        "full_grid_temporal_oracle_expected_center_witness": dict(
            sorted(expected_center_witness.items())
        ),
        "full_grid_temporal_oracle_expected_center_neighbor_offsets": dict(
            sorted(expected_center_neighbor_offsets.items())
        ),
        "full_grid_temporal_oracle_expected_center_capture_depth_abs_residual": (
            _finite_distribution(expected_center_capture_depth_abs_residuals)
        ),
        "full_grid_temporal_oracle_expected_center_capture_depth_relative_residual": (
            _finite_distribution(expected_center_capture_depth_relative_residuals)
        ),
        "full_grid_temporal_oracle_expected_center_neighbor_angular_residual": (
            _finite_distribution(expected_center_neighbor_angular_residuals)
        ),
        "full_grid_temporal_oracle_expected_center_neighbor_depth_relative_residual": (
            _finite_distribution(expected_center_neighbor_depth_relative_residuals)
        ),
        "full_grid_temporal_oracle_reference_key_kind": (
            "normalized_owner_object_primitive_material_plus_exact_stable_anchor"
        ),
        "full_grid_temporal_oracle_reference_anchor_quantization": "none",
        "full_grid_temporal_oracle_comparable_reference_key_pairs": (
            comparable_reference_key_pairs
        ),
        "full_grid_temporal_oracle_changing_reference_key_state_pairs": (
            changing_reference_key_state_pairs
        ),
        "full_grid_temporal_oracle_reference_identity_set_changes": (
            reference_identity_set_changes
        ),
        "full_grid_temporal_oracle_reference_defect_state_changes": (
            reference_defect_state_changes
        ),
        # Continuity-law twins: identity domain is owner/object/material and a
        # defect state is a REAL defect, so a by-design re-address or a
        # continuous retained substitution no longer reads as instability.
        "full_grid_temporal_oracle_continuity_changing_reference_key_state_pairs": (
            changing_reference_key_state_pairs_continuity
        ),
        "full_grid_temporal_oracle_continuity_reference_identity_set_changes": (
            reference_identity_set_changes_continuity
        ),
        "full_grid_temporal_oracle_continuity_reference_defect_state_changes": (
            reference_defect_state_changes_continuity
        ),
        "full_grid_temporal_oracle_source_probe_set_changes": source_probe_set_changes,
        "full_grid_temporal_oracle_source_probe_transition_matrix": dict(
            sorted(source_probe_transition_matrix.items())
        ),
        "full_grid_temporal_oracle_source_generation_set_flips": (
            source_generation_set_flips
        ),
        "full_grid_temporal_oracle_source_texel_set_changes": source_texel_set_changes,
        "full_grid_temporal_oracle_exact_probe_texel_generation_tuple_changes": (
            exact_probe_texel_generation_tuple_changes
        ),
        "full_grid_temporal_oracle_generation_misregistration_correlation": (
            _binary_contingency_report(generation_misregistration_contingency)
        ),
        "full_grid_temporal_oracle_source_probe_misregistration_correlation": (
            _binary_contingency_report(source_probe_misregistration_contingency)
        ),
        "full_grid_temporal_oracle_unmatched_reference_keys_from_previous": (
            unmatched_reference_keys_from_previous
        ),
        "full_grid_temporal_oracle_unmatched_reference_keys_in_current": (
            unmatched_reference_keys_in_current
        ),
        "full_grid_temporal_oracle_multi_glyph_mask_cells": len(glyph_split_cells),
        "full_grid_temporal_oracle_defect_episodes": episodes[:details_limit],
        "full_grid_temporal_oracle_same_anchor_displacements": displacement_rows,
        "full_grid_temporal_oracle_components_by_frame": component_receipts,
        "full_grid_temporal_oracle_structural_components_by_frame": {
            frame_id: {
                "component_count": len(components),
                "largest": [
                    public_component(component)
                    for component in components[: min(details_limit, 16)]
                ],
            }
            for frame_id, components in structural_components_by_frame.items()
        },
        "full_grid_temporal_oracle_component_lifetimes": finished_tracks[:details_limit],
        "full_grid_temporal_oracle_large_components": large_components[:details_limit],
        "full_grid_temporal_oracle_structural_large_components": (
            structural_large_components[:details_limit]
        ),
        "full_grid_temporal_oracle_duplicate_components": duplicate_components,
        "full_grid_temporal_oracle_out_of_place_identities": out_of_place_identities,
        "full_grid_temporal_oracle_structural_duplicate_components": (
            structural_duplicate_components
        ),
        "full_grid_temporal_oracle_structural_out_of_place_identities": (
            structural_out_of_place_identities
        ),
        "full_grid_temporal_oracle_candidate_tuple_duplicate_components": (
            candidate_tuple_duplicate_components
        ),
    }


def full_grid_oracle_defect_map_rows(rows: list[dict]) -> list[dict]:
    """Return the exhaustive machine-attribution row for every observed defect."""
    result: list[dict] = []
    for row in rows:
        if row.get("oracle_schema") != SAME_FRAME_ORACLE_SCHEMA:
            continue
        classification = str(row.get("oracle_classification", ""))
        mask_state = str(row.get("final_paint_mask_state", ""))
        mask_contributors = int(row.get("final_paint_mask_contributor_count", 0))
        complete_mask = _complete_glyph_mask_witness(row)
        product_identity = _product_exact_identity_witness(row)
        geometry_error = classification in {
            "identity_mismatch", "omission", "ghost_or_commission",
            "oracle_query_invalid",
        }
        glyph_mask_error = (
            mask_contributors > 1
            or (
                bool(complete_mask.get("available", False))
                and not bool(complete_mask.get("complete", False))
            )
            or (
                not bool(complete_mask.get("available", False))
                and mask_state not in {"final_mask_contributors_measured", ""}
            )
        )
        product_translation_error = bool(
            product_identity.get("available", False)
            and not product_identity.get("translation_equal", False)
        )
        if not geometry_error and not glyph_mask_error and not product_translation_error:
            continue
        trace = _same_frame_oracle_trace(row)
        finite_splat = _finite_splat_admissibility(row)
        continuity_verdict = _oracle_continuity_class(row)
        result.append({
            "frame_index": int(row.get("frame_index", -1)),
            "sequence_frame": int(trace.get("sequence_frame", -1)),
            "authority_server_tick": int(trace.get("authority_server_tick", -1)),
            "sample_index": int(row.get("sample_index", -1)),
            "ansi_cell": row.get("ansi_cell", {}),
            "subcell_slot": row.get("subcell_slot"),
            "classification": classification,
            # FL-4359 continuity law, recorded per row beside the strict
            # classification so a reviewer can see exactly how much the
            # tolerance absorbs and on which rows.
            "continuity_classification": continuity_verdict["class"],
            "continuity_defect": continuity_verdict["defect"],
            "continuity_reason": continuity_verdict["continuity"].get("reason"),
            "continuity_normal_dot": continuity_verdict["continuity"].get(
                "normal_dot"
            ),
            "continuity_plane_distance": continuity_verdict["continuity"].get(
                "plane_distance"
            ),
            "continuity_depth_separation": continuity_verdict["continuity"].get(
                "depth_separation"
            ),
            "continuity_tolerance": continuity_verdict["continuity"].get(
                "tolerance"
            ),
            "continuity_ansi_cell_span": continuity_verdict["continuity"].get(
                "span"
            ),
            "where_reanchor": continuity_verdict["where_reanchor"],
            "answer_source_epoch": continuity_verdict["answer_source_epoch"],
            "reference_owner": _oracle_reference_owner(row),
            "first_divergence": _oracle_first_divergence(row),
            "expected_center_witness": _oracle_expected_center_classification(row),
            "structural_geometry_error": _oracle_structural_geometry_error(row),
            "finite_splat_admissibility": finite_splat,
            "reference_identity": row.get("oracle_identity", {}),
            "reference_world_position": row.get("oracle_world_position"),
            "reference_depth": row.get("oracle_depth"),
            "oracle_query_failure_reason": row.get("oracle_query_failure_reason"),
            "oracle_query_failure_name": row.get("oracle_query_failure_name"),
            "prepack_valid": bool(row.get("probe_prepack_valid", False)),
            "prepack_identity": row.get("probe_prepack_identity", {}),
            "prepack_world_position": row.get("probe_prepack_world_position"),
            "prepack_hardware_depth": row.get("probe_prepack_hardware_depth"),
            "prepack_provenance": row.get("probe_prepack_provenance", []),
            "prepack_agrees_with_b61": bool(
                row.get("probe_prepack_agrees_with_b61", False)
            ),
            "source_capture_valid": bool(
                row.get("probe_source_capture_valid", False)
            ),
            "source_capture_identity": row.get(
                "probe_source_capture_identity", {}
            ),
            "source_capture_probe": row.get("probe_source_capture_probe"),
            "source_capture_face": row.get("probe_source_capture_face"),
            "source_capture_texel": row.get("probe_source_capture_texel"),
            "source_capture_generation": row.get(
                "probe_source_capture_generation"
            ),
            "source_capture_physical_layer": row.get(
                "probe_source_capture_physical_layer"
            ),
            "source_capture_physical_band": row.get(
                "probe_source_capture_physical_band"
            ),
            "source_capture_band_map_valid": bool(
                row.get("probe_source_capture_band_map_valid", False)
            ),
            "source_capture_join_flags": int(
                row.get("probe_source_capture_join_flags", 0)
            ),
            "source_capture_depth": (
                row.get("probe_source_capture_normal_depth", [None] * 4)[3]
                if isinstance(row.get("probe_source_capture_normal_depth"), list)
                and len(row.get("probe_source_capture_normal_depth")) >= 4
                else None
            ),
            "source_capture_hardware_depth": row.get(
                "probe_source_capture_hardware_depth"
            ),
            "reconstructed_center": row.get("probe_reconstructed_center"),
            "reconstructed_world_corners": row.get(
                "probe_reconstructed_world_corners", []
            ),
            "reconstructed_clip_corners": row.get(
                "probe_reconstructed_clip_corners", []
            ),
            "reconstructed_clip_valid": bool(
                row.get("probe_reconstructed_clip_valid", False)
            ),
            "raster_witness": {
                "contender_count": row.get("probe_raster_contender_count"),
                "recorded_count": row.get(
                    "probe_raster_contender_recorded_count"
                ),
                "overflow": bool(row.get(
                    "probe_raster_contender_overflow", True
                )),
                "exact_identity_contender_count": row.get(
                    "probe_raster_exact_identity_contender_count"
                ),
                "structural_identity_contender_count": row.get(
                    "probe_raster_structural_identity_contender_count"
                ),
                "selected_identity_contender_count": row.get(
                    "probe_raster_selected_identity_contender_count"
                ),
                "nearest_exact_identity_depth": row.get(
                    "probe_raster_nearest_exact_identity_depth"
                ),
                "nearest_structural_identity_depth": row.get(
                    "probe_raster_nearest_structural_identity_depth"
                ),
                "nearest_exact_identity_source": row.get(
                    "probe_raster_nearest_exact_identity_source", {}
                ),
                "nearest_structural_identity_source": row.get(
                    "probe_raster_nearest_structural_identity_source", {}
                ),
                "candidate_source_contender_count": row.get(
                    "probe_raster_candidate_source_contender_count"
                ),
                "candidate_source_nearest_depth": row.get(
                    "probe_raster_candidate_source_nearest_depth"
                ),
                "candidate_source_farthest_depth": row.get(
                    "probe_raster_candidate_source_farthest_depth"
                ),
                "candidate_source_nearest_source": row.get(
                    "probe_raster_candidate_source_nearest_source", {}
                ),
            },
            "source_oracle_query_valid": bool(
                row.get("probe_source_oracle_query_valid", False)
            ),
            "source_oracle_direct_hit": bool(
                row.get("probe_source_oracle_direct_hit", False)
            ),
            "source_oracle_identity": row.get(
                "probe_source_oracle_identity", {}
            ),
            "source_oracle_world_position": row.get(
                "probe_source_oracle_world_position"
            ),
            "source_oracle_depth": row.get("probe_source_oracle_depth"),
            "expected_center_witness_record": {
                "record_valid": bool(row.get(
                    "probe_expected_center_record_valid", False
                )),
                "applicable": bool(row.get(
                    "probe_expected_center_applicable", False
                )),
                "address_valid": bool(row.get(
                    "probe_expected_center_address_valid", False
                )),
                "query_valid": bool(row.get(
                    "probe_expected_center_query_valid", False
                )),
                "direct_hit": bool(row.get(
                    "probe_expected_center_direct_hit", False
                )),
                "source_probe": row.get("probe_expected_center_source_probe"),
                "face": row.get("probe_expected_center_face"),
                "texel": row.get("probe_expected_center_texel"),
                "query_identity": row.get(
                    "probe_expected_center_identity", {}
                ),
                "capture_identity": row.get(
                    "probe_expected_center_capture_identity", {}
                ),
                "capture_valid": bool(row.get(
                    "probe_expected_center_capture_valid", False
                )),
                "capture_generation": row.get(
                    "probe_expected_center_capture_generation"
                ),
                "neighbor_valid": bool(row.get(
                    "probe_expected_center_neighbor_valid", False
                )),
                "neighbor_dx": row.get("probe_expected_center_neighbor_dx"),
                "neighbor_dy": row.get("probe_expected_center_neighbor_dy"),
                "neighbor_texel": row.get(
                    "probe_expected_center_neighbor_texel"
                ),
                "neighbor_generation": row.get(
                    "probe_expected_center_neighbor_generation"
                ),
                "capture_depth_abs_residual": row.get(
                    "probe_expected_center_capture_depth_abs_residual"
                ),
                "capture_depth_relative_residual": row.get(
                    "probe_expected_center_capture_depth_relative_residual"
                ),
                "neighbor_angular_residual": row.get(
                    "probe_expected_center_neighbor_angular_residual"
                ),
                "neighbor_depth_relative_residual": row.get(
                    "probe_expected_center_neighbor_depth_relative_residual"
                ),
            },
            "expected_visibility": {
                "active_mask": row.get("probe_expected_visibility_active_mask"),
                "query_valid_mask": row.get(
                    "probe_expected_visibility_query_valid_mask"
                ),
                "direct_hit_mask": row.get(
                    "probe_expected_visibility_direct_hit_mask"
                ),
                "identity_match_mask": row.get(
                    "probe_expected_visibility_identity_match_mask"
                ),
            },
            "expected_capture": {
                "active_mask": row.get("probe_expected_capture_active_mask"),
                "exact_valid_mask": row.get(
                    "probe_expected_capture_exact_valid_mask"
                ),
                "exact_match_mask": row.get(
                    "probe_expected_capture_exact_match_mask"
                ),
                "nearby_match_mask": row.get(
                    "probe_expected_capture_nearby_match_mask"
                ),
                "first_match_probe": row.get(
                    "probe_expected_capture_first_match_probe"
                ),
                "first_match_face": row.get(
                    "probe_expected_capture_first_match_face"
                ),
                "first_match_texel": row.get(
                    "probe_expected_capture_first_match_texel"
                ),
                "first_match_generation": row.get(
                    "probe_expected_capture_first_match_generation"
                ),
            },
            "candidate_valid": bool(row.get("probe_candidate_valid", False)),
            "candidate_identity": row.get("probe_candidate_identity", {}),
            "candidate_world_position": row.get("probe_candidate_world_position"),
            "candidate_source_probe": row.get("probe_candidate_source_probe"),
            "candidate_source_texel": row.get("probe_candidate_source_texel"),
            "candidate_source_generation": row.get(
                "probe_candidate_source_generation"
            ),
            "candidate_pack_rejection_reason": int(
                row.get("probe_candidate_pack_rejection_reason", 0)
            ),
            "selected_identity": {
                "owner_kind": row.get("selected_surface_owner"),
                "object_id": row.get("selected_surface_object"),
                "primitive_id": row.get("selected_surface_primitive"),
                "material": row.get("selected_surface_material"),
            },
            "selected_world_position": row.get("selected_surface_world_position"),
            "selected_depth": row.get("selected_surface_depth"),
            "product": {
                "status": row.get("final_product_status"),
                "owner": row.get("final_product_owner"),
                "material_id": row.get("final_product_material_id"),
                "source_key": row.get("final_product_source_key"),
                "entity_id": row.get("final_product_entity_id"),
                "glyph": row.get("final_product_glyph"),
                "exact_identity_witness": product_identity,
            },
            "paint": {
                "glyph": row.get("final_paint_glyph"),
                "mask_state": mask_state,
                "complete_glyph_mask_witness": complete_mask,
                "mask_presence": row.get("final_paint_mask_presence"),
                "mask_contributor_count": mask_contributors,
                "mask_contributor_ids": row.get(
                    "final_paint_mask_contributor_ids", []
                ),
                "mask_ink_pixels": row.get("final_paint_mask_ink_pixels"),
            },
        })
    return result


def _world_anchor_value(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        anchor = (
            float(value["x"]),
            float(value["y"]),
            float(value["z"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(component) for component in anchor):
        return None
    return anchor


def current_product_anchor_provenance_error(row: dict) -> str | None:
    """Return the first failed current-b55 provenance invariant, if any."""
    if not final_product_status_is_valid(row.get("final_product_status")):
        return "invalid_or_missing_final_product_status"
    if row.get("comparison_anchor_source") != COMPARISON_ANCHOR_SOURCE_B55:
        return "comparison_anchor_source_not_b55"
    if row.get("capture_schema") != CURRENT_SPARSE_CAPTURE_SCHEMA:
        return "capture_schema_not_current"
    product_anchor = _world_anchor_value(row.get("final_product_world_anchor"))
    if product_anchor is None:
        return "missing_or_invalid_final_product_world_anchor"
    comparison_anchor = _world_anchor_value(row.get("ansi_cell_world_anchor"))
    if comparison_anchor is None:
        return "missing_or_invalid_comparison_world_anchor"
    if comparison_anchor != product_anchor:
        return "comparison_anchor_mismatches_final_product_world_anchor"
    try:
        subcell_slot = int(row.get("subcell_slot", -1))
        unknown_mask_bits = int(row.get("final_paint_mask_unknown_bits", 0))
    except (TypeError, ValueError):
        return "invalid_native_mask_slot_metadata"
    if not 0 <= subcell_slot <= 3:
        return "invalid_native_mask_slot"
    if not bool(row.get("final_paint_slot_present", False)):
        return "native_mask_slot_missing"
    if bool(row.get("final_paint_slot_actor_overlay", False)):
        return "native_mask_slot_actor_overlay"
    if unknown_mask_bits != 0:
        return "native_mask_unknown_bits"
    if row.get("final_paint_slot_glyph") is None:
        return "native_mask_slot_glyph_missing"
    product_colours_present = (
        isinstance(row.get("final_product_fg_rgb"), (list, tuple))
        and len(row.get("final_product_fg_rgb")) >= 3
        and isinstance(row.get("final_product_bg_rgb"), (list, tuple))
        and len(row.get("final_product_bg_rgb")) >= 3
    )
    if not product_colours_present:
        return "final_product_colours_missing"
    return None


def has_current_product_anchor_provenance(row: dict) -> bool:
    """Require one current-schema b55 Product joined to its native mask slot."""
    return current_product_anchor_provenance_error(row) is None


def summarize_product_anchor_provenance(rows: list[dict]) -> dict:
    """Report whether every world-admitted row proves a current b55 anchor."""
    admitted_rows = [row for row in rows if admitted(row, ADMISSION_WORLD)]
    invalid_rows = [row for row in admitted_rows if not has_current_product_anchor_provenance(row)]
    reasons = Counter(
        current_product_anchor_provenance_error(row) for row in invalid_rows
    )
    return {
        "admitted_world_rows": len(admitted_rows),
        "valid_current_b55_anchor_rows": len(admitted_rows) - len(invalid_rows),
        "invalid_current_b55_anchor_rows": len(invalid_rows),
        "invalid_reason_counts": dict(sorted(reasons.items())),
        "complete": bool(admitted_rows) and not invalid_rows,
    }


# Historical analyzer-only policy from 55c62c8a2. It remains visible in
# receipts for audit compatibility, but it is not acceptance authority: no
# same-pose 6fc Product-family capture calibrated this number.
MOUNTAIN_DIRT_DOMINANCE_LIMIT = 0.65


def _terrain_same_key(row: dict):
    # (source_key, terr_stable_cell_gpu.x, terr_stable_cell_gpu.y) for terrain rows only.
    # The host-side terr_stable_cell is a coarse cross-check; the shader's r18 GPU
    # cell is the product identity used by the source path and the active plan.
    if row.get("selected_owner") != "terrain":
        return None
    sk = row.get("source_key")
    sc = row.get("terr_stable_cell_gpu")
    if sk is None or not isinstance(sc, dict):
        return None
    return (int(sk), int(sc.get("x", 0)), int(sc.get("y", 0)))


def _terrain_semantic_signature(row: dict) -> dict:
    sig = unpack_terrain_detail(row.get("terrain_product_detail"))
    sig["glyph_id"] = row.get("final_product_glyph", row.get("glyph_id"))
    sig["material_family"] = row.get("final_product_material_family")
    sig["material_id"] = row.get("final_product_material_id")
    return sig


def _product_mountain_material_role(row: dict) -> str | None:
    """Decode Product's emitted terrain family without duplicating its mat map."""
    family = row.get("final_product_material_family")
    if not isinstance(family, int) or isinstance(family, bool):
        return None
    if family == 1:
        return "dirt"
    if family == 2:
        return "cliff_rock"
    return None


def _is_mountain_geometry_row(row: dict) -> bool:
    if row.get("selected_owner") != "terrain":
        return False
    detail = unpack_terrain_detail(row.get("terrain_product_detail"))
    return bool(
        detail.get("cliff", 0)
        or detail.get("ridge", 0)
        or detail.get("clone_peak_glyph_lane", 0)
        or int(detail.get("sidewall_score_class", 0)) > 0
    )


def _is_mountain_evidence_row(row: dict) -> bool:
    return _is_mountain_geometry_row(row) and _product_mountain_material_role(row) is not None


def surface_bucket(row: dict) -> str:
    owner = row.get("selected_owner")
    if owner == "water":
        return "water"
    if owner == "terrain":
        role = _product_mountain_material_role(row) or "other"
        if _is_mountain_evidence_row(row):
            return f"mountain:{role}"
        return f"terrain:{role}"
    if owner is None:
        return "unknown"
    return str(owner)


def summarize_same_key_flips(frames: dict) -> dict:
    """Codex same-key proof. For an UNCHANGED (source_key, terr_stable_cell):
      - intra-frame variant count: distinct semantic signatures colliding under one
        key in ONE frame (>1 = a world-key history writer WOULD race; Codex CRIT-1).
      - cross-frame flips: per-field changes for the same key between frames (names
        which semantic decision is unstable; the gate for whether the lane exists).
    Pure post-processing; tolerant of rows lacking the new fields (returns zeros)."""
    frame_ids = sorted(frames.keys())
    terrain_key_rows = 0
    per_frame_groups: dict = {}
    intra_variant_groups = 0
    frame_key_groups = 0
    max_intra_variant = 1
    for fid in frame_ids:
        groups: dict = {}
        for row in frames[fid]:
            key = _terrain_same_key(row)
            if key is None:
                continue
            terrain_key_rows += 1
            sig = _terrain_semantic_signature(row)
            g = groups.setdefault(key, {
                "field_values": {fld: set() for fld in SAME_KEY_SEMANTIC_FIELDS},
                "sig_tuples": set(),
            })
            g["sig_tuples"].add(tuple(sig.get(fld) for fld in SAME_KEY_SEMANTIC_FIELDS))
            for fld in SAME_KEY_SEMANTIC_FIELDS:
                g["field_values"][fld].add(sig.get(fld))
        for g in groups.values():
            frame_key_groups += 1
            vc = len(g["sig_tuples"])
            g["intra_frame_variant_count"] = vc
            if vc > 1:
                intra_variant_groups += 1
            if vc > max_intra_variant:
                max_intra_variant = vc
        per_frame_groups[fid] = groups
    per_field_flip = {fld: 0 for fld in SAME_KEY_SEMANTIC_FIELDS}
    same_key_pairs = 0
    same_key_flip_pairs = 0
    examples: list = []
    for i in range(len(frame_ids) - 1):
        a = per_frame_groups[frame_ids[i]]
        b = per_frame_groups[frame_ids[i + 1]]
        for key, ga in a.items():
            gb = b.get(key)
            if gb is None:
                continue
            same_key_pairs += 1
            flipped = [fld for fld in SAME_KEY_SEMANTIC_FIELDS
                       if ga["field_values"][fld] != gb["field_values"][fld]]
            for fld in flipped:
                per_field_flip[fld] += 1
            if flipped:
                same_key_flip_pairs += 1
                if len(examples) < 30:
                    examples.append({
                        "frame_a": frame_ids[i], "frame_b": frame_ids[i + 1],
                        "source_key": key[0], "stable_x": key[1], "stable_y": key[2],
                        "intra_frame_variant_count_a": ga.get("intra_frame_variant_count", 1),
                        "intra_frame_variant_count_b": gb.get("intra_frame_variant_count", 1),
                        "flipped_fields": flipped,
                    })
    flip_pct = (same_key_flip_pairs / same_key_pairs * 100.0) if same_key_pairs else 0.0
    intra_pct = (intra_variant_groups / frame_key_groups * 100.0) if frame_key_groups else 0.0
    return {
        "same_key_terrain_rows": terrain_key_rows,
        "same_key_frame_groups": frame_key_groups,
        "same_key_intra_frame_variant_groups": intra_variant_groups,
        "same_key_intra_frame_variant_pct": intra_pct,
        "same_key_max_intra_frame_variant_count": max_intra_variant,
        "same_key_cross_frame_pairs": same_key_pairs,
        "same_key_cross_frame_flip_pairs": same_key_flip_pairs,
        "same_key_semantic_flip_pct": flip_pct,
        "same_key_semantic_flip_observed": bool(same_key_flip_pairs > 0 or intra_variant_groups > 0),
        "same_key_per_field_flip_counts": per_field_flip,
        "same_key_flip_examples": examples,
    }


def summarize_fixed_screen_temporal(frames: dict[int, list[dict]]) -> dict:
    """Measure visible and F10 changes at the same projected ANSI cell.

    World-anchor matching remains structural product evidence. This metric owns
    the eye-space temporal question: did the paint or F10 classification at one
    fixed screen cell change between consecutive captured frames?
    """
    frame_ids = sorted(frames.keys())
    totals: Counter = Counter()
    pair_results: list[dict] = []
    for index in range(len(frame_ids) - 1):
        frame_a = frame_ids[index]
        frame_b = frame_ids[index + 1]
        rows_a = {
            screen_cell_key(row): row
            for row in frames[frame_a]
            if admitted(row, ADMISSION_SCREEN) and screen_cell_key(row)
        }
        rows_b = {
            screen_cell_key(row): row
            for row in frames[frame_b]
            if admitted(row, ADMISSION_SCREEN) and screen_cell_key(row)
        }
        pair: Counter = Counter()
        for key in sorted(set(rows_a) & set(rows_b)):
            a = rows_a[key]
            b = rows_b[key]
            # F10 is an optional diagnostic lane. The retained b43 final-paint
            # fields remain authoritative for fixed-screen visible signatures.
            # Missing F10 data must not erase that comparison.
            f10_present = bool(a.get("f10_diag_readback_present")) and bool(b.get("f10_diag_readback_present"))
            pair["comparable_cells"] += 1
            a_mask = int(a.get("f10_backstop_subtap_mask") or 0) if f10_present else 0
            b_mask = int(b.get("f10_backstop_subtap_mask") or 0) if f10_present else 0
            a_markers = tuple(a.get("f10_diag_markers") or ()) if f10_present else ()
            b_markers = tuple(b.get("f10_diag_markers") or ()) if f10_present else ()
            a_reasons = tuple(a.get("f10_rekey_reasons") or ()) if f10_present else ()
            b_reasons = tuple(b.get("f10_rekey_reasons") or ()) if f10_present else ()
            highlighted = a_mask != 0 or b_mask != 0
            marker_flip = f10_present and (a_mask != b_mask or a_markers != b_markers)
            reason_flip = f10_present and a_reasons != b_reasons
            product_flip = final_product_identity_key(a) != final_product_identity_key(b)
            visible_flip = final_cell_signature_fields(a, include_ink=True) != final_cell_signature_fields(b, include_ink=True)
            pair["highlighted_cells"] += int(highlighted)
            pair["backstop_marker_flip_cells"] += int(marker_flip)
            pair["rekey_reason_flip_cells"] += int(reason_flip)
            pair["product_flip_cells"] += int(product_flip)
            pair["visible_signature_flip_cells"] += int(visible_flip)
            pair["highlighted_visible_flip_cells"] += int(highlighted and visible_flip)
            pair["highlighted_classification_flip_cells"] += int(highlighted and (marker_flip or reason_flip))
            pair["stable_highlight_visible_flip_cells"] += int(highlighted and not marker_flip and not reason_flip and visible_flip)
        totals.update(pair)
        comparable = int(pair.get("comparable_cells", 0))
        pair_results.append({
            "frame_a": frame_a,
            "frame_b": frame_b,
            **dict(pair),
            "visible_signature_flip_pct": (
                float(pair.get("visible_signature_flip_cells", 0)) * 100.0 / comparable
                if comparable else 0.0
            ),
        })
    comparable = int(totals.get("comparable_cells", 0))

    def pct(name: str) -> float:
        return float(totals.get(name, 0)) * 100.0 / comparable if comparable else 0.0

    return {
        "fixed_screen_temporal_evidence_present": comparable > 0,
        "fixed_screen_temporal_comparable_cells": comparable,
        "fixed_screen_temporal_pair_count": sum(1 for pair in pair_results if int(pair.get("comparable_cells", 0)) > 0),
        "fixed_screen_backstop_marker_flip_cells": int(totals.get("backstop_marker_flip_cells", 0)),
        "fixed_screen_backstop_marker_flip_pct": pct("backstop_marker_flip_cells"),
        "fixed_screen_rekey_reason_flip_cells": int(totals.get("rekey_reason_flip_cells", 0)),
        "fixed_screen_rekey_reason_flip_pct": pct("rekey_reason_flip_cells"),
        "fixed_screen_product_flip_cells": int(totals.get("product_flip_cells", 0)),
        "fixed_screen_product_flip_pct": pct("product_flip_cells"),
        "fixed_screen_visible_signature_flip_cells": int(totals.get("visible_signature_flip_cells", 0)),
        "fixed_screen_visible_signature_flip_pct": pct("visible_signature_flip_cells"),
        "fixed_screen_highlighted_visible_flip_cells": int(totals.get("highlighted_visible_flip_cells", 0)),
        "fixed_screen_highlighted_visible_flip_pct": pct("highlighted_visible_flip_cells"),
        "fixed_screen_highlighted_classification_flip_cells": int(totals.get("highlighted_classification_flip_cells", 0)),
        "fixed_screen_highlighted_classification_flip_pct": pct("highlighted_classification_flip_cells"),
        "fixed_screen_stable_highlight_visible_flip_cells": int(totals.get("stable_highlight_visible_flip_cells", 0)),
        "fixed_screen_stable_highlight_visible_flip_pct": pct("stable_highlight_visible_flip_cells"),
        "fixed_screen_temporal_pair_results": pair_results,
    }


def retained_probe_source_key(row: dict):
    """Return the recorded cell-owner key that backs the F10 transition tint.

    The mechanical FL-4359 retained-plate gate reads Product/source-key facts
    from jitter_capture.jsonl.  It does not decode diagnostic PNG colors,
    because the palette is only a human-facing projection of these fields.
    """
    for key in ("final_product_source_key", "source_key", "selected_surface_source_key"):
        if key in row and row.get(key) is not None:
            return row.get(key)
    identity = final_product_identity_key(row)
    if isinstance(identity, tuple) and len(identity) >= 2 and identity[1] is not None:
        return identity[1]
    return None


def _probe_evidence_present(row: dict, column: str) -> bool:
    """Return whether the producer marked that exact record as present.

    Accepts both the boolean *_valid flags and the bitmask columns the sparse
    schema uses for multi-face records; a mask counts as present when non-zero.
    An absent column is absent evidence, never an implied True.
    """
    if column not in row:
        return False
    value = row.get(column)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    return bool(value)


def _retained_generation_column_value(row: dict, column: str):
    """Return one v16 generation column as an int, or None when it is absent.

    probe_prepack_provenance is a packed tuple, so its generation member is
    read positionally.  Producer sentinels are rejected rather than reported
    as very large generation numbers.
    """
    raw = row.get(column)
    if column == RETAINED_GENERATION_PREPACK_COLUMN:
        if (
            not isinstance(raw, (list, tuple))
            or len(raw) <= RETAINED_GENERATION_PREPACK_INDEX
        ):
            return None
        raw = raw[RETAINED_GENERATION_PREPACK_INDEX]
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value in RETAINED_GENERATION_SENTINELS or value < 0:
        return None
    return value


def retained_probe_generation_label(row: dict):
    """Return a per-cell CURRENT/PREVIOUS-like provenance label when captured.

    Older R6 receipts captured frame-level transition state but not the exact
    elected probe generation per cell.  Those receipts still support source-key
    plate and flip metrics; generation-specific counts stay explicitly
    unavailable instead of being inferred from colors or frame-level state.

    Ownership: only a per-cell provenance field may answer this question.  The
    b55 selected_surface_status source-epoch bits are deliberately not a
    fallback owner: the producer never sets bits 30-31, so decoding them turns
    every absent measurement into a concrete "direct" label.  Absent evidence
    returns None here and is carried as PROVENANCE_UNMEASURED by callers.

    Two name spaces are read, legacy first: the pre-v16 per-cell keys below,
    then the fl4359_jitter_transport.v16 columns in RETAINED_GENERATION_SOURCES
    ownership order.  A v16 hit is rendered as "<owner>_gen:<n>".  The owner
    prefix is load bearing twice over: it keeps a bare generation counter of 0
    or 1 out of the CURRENT/PREVIOUS role vocabulary that
    _retained_generation_is_current_previous_flip matches on, and it keeps two
    owners with independent generation numbering from being compared as if
    they shared one counter.
    """
    for key in (
        "candidate_provenance",
        "candidate_probe_generation",
        "selected_probe_generation",
        "selected_probe_epoch",
        "selected_source_epoch",
        "source_probe_generation",
        "source_probe_epoch",
        "source_epoch",
        "final_product_source_epoch",
        "final_product_probe_generation",
    ):
        if key not in row or row.get(key) is None:
            continue
        value = row.get(key)
        if isinstance(value, dict):
            for nested_key in ("generation", "epoch", "probe_generation", "probe_epoch", "name"):
                if nested_key in value and value.get(nested_key) is not None:
                    return str(value.get(nested_key)).lower()
            return json.dumps(value, sort_keys=True)
        if isinstance(value, (list, tuple)):
            return json.dumps(value)
        return str(value).lower()
    for owner, generation_column, evidence_column in RETAINED_GENERATION_SOURCES:
        if generation_column not in row:
            continue
        # Reject the row whenever the producer's own record-present flag is
        # unset.  Several v16 columns keep a residual generation after the
        # producer marked that record absent, so a non-sentinel value on its
        # own is not a measurement.
        if not _probe_evidence_present(row, evidence_column):
            continue
        value = _retained_generation_column_value(row, generation_column)
        if value is None:
            continue
        return f"{owner}_gen:{value}"
    return None


def retained_probe_generation_classification(row: dict) -> str:
    """Return the generation label or the explicit unmeasured bucket."""
    label = retained_probe_generation_label(row)
    return PROVENANCE_UNMEASURED if label is None else str(label)


def _probe_int_vector(value: object, length: int) -> list[int] | None:
    """Accept a host vector only at its exact declared length."""
    if not isinstance(value, list) or len(value) != length:
        return None
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        return None


def probe_capture_lifecycle_evidence(row: dict) -> dict | None:
    """Return one frame's layer-granular capture-lifecycle evidence.

    Reads frame_trace.perspective_probe_state, the same transport that already
    carries capture_generation, recapture_reason, and captured_face_masks.
    Returns None for any capture that predates the layer-granular schema or
    publishes a malformed vector, so the caller classifies the sample as
    unmeasured instead of guessing.  Optional members stay None individually
    rather than being defaulted to a concrete value.
    """
    trace = row.get("frame_trace")
    if not isinstance(trace, dict):
        return None
    state = trace.get("perspective_probe_state")
    if not isinstance(state, dict):
        return None
    bands = _probe_int_vector(state.get("capture_bands"), PROBE_LAYER_BAND_COUNT)
    # capture_bands must be a permutation of the physical bands; anything else
    # cannot carry the role-to-band join this evidence exists to provide.
    if bands is None or sorted(bands) != list(range(PROBE_LAYER_BAND_COUNT)):
        return None
    epochs = _probe_int_vector(state.get("layer_content_epochs"), PROBE_LAYER_COUNT)
    if epochs is None:
        return None
    reasons = state.get("layer_content_epoch_reasons")
    if not isinstance(reasons, list) or len(reasons) != PROBE_LAYER_COUNT:
        reasons = None
    activity_raw = state.get("capture_activity_by_probe")
    if isinstance(activity_raw, list) and len(activity_raw) == PROBE_LAYER_BAND_COUNT:
        activity = [str(item) for item in activity_raw]
    else:
        activity = None
    reasons_by_probe_raw = state.get("recapture_reasons_by_probe")
    if (
        isinstance(reasons_by_probe_raw, list)
        and len(reasons_by_probe_raw) == PROBE_LAYER_BAND_COUNT
        and all(isinstance(item, list) for item in reasons_by_probe_raw)
    ):
        # An empty list is the published "no recapture scheduled" fact; it is
        # not missing evidence and must not become an unmeasured bucket.
        reasons_by_probe = [[str(cause) for cause in item] for item in reasons_by_probe_raw]
    else:
        reasons_by_probe = None
    recaptured_raw = state.get("layers_recaptured_this_frame")
    if isinstance(recaptured_raw, list):
        try:
            recaptured = sorted({int(item) for item in recaptured_raw})
        except (TypeError, ValueError):
            recaptured = None
    else:
        recaptured = None
    return {
        "capture_bands": bands,
        "layer_content_epochs": epochs,
        "layer_content_epoch_generations": _probe_int_vector(
            state.get("layer_content_epoch_generations"), PROBE_LAYER_COUNT
        ),
        "layer_content_epochs_before_capture": _probe_int_vector(
            state.get("layer_content_epochs_before_capture"), PROBE_LAYER_COUNT
        ),
        "layer_content_epoch_reasons": reasons,
        "layers_recaptured_this_frame": recaptured,
        "recapture_reasons_by_probe": reasons_by_probe,
        "capture_activity_by_probe": activity,
    }


def retained_probe_provenance_tuple(row: dict):
    """Return the b61 row provenance as (role, face, texel, generation).

    The role is the logical probe (EYE/CURRENT/PREVIOUS), not a physical band.
    An invalid source-capture record yields None so no downstream comparison
    treats zeroed placeholder fields as a measurement.
    """
    if not bool(row.get("probe_source_capture_valid", False)):
        return None
    try:
        role = int(row.get("probe_source_capture_probe", -1))
        face = int(row.get("probe_source_capture_face", -1))
        texel = int(row.get("probe_source_capture_texel", -1))
        generation = int(row.get("probe_source_capture_generation", -1))
    except (TypeError, ValueError):
        return None
    if role < 0 or role >= PROBE_LAYER_BAND_COUNT:
        return None
    if face < 0 or face >= PROBE_LAYER_FACE_COUNT:
        return None
    return (role, face, texel, generation)


def retained_probe_physical_layer(row: dict, evidence: dict | None):
    """Join b61 (role, face) onto the physical layer index for this frame.

    layer = capture_bands[role] * PROBE_LAYER_FACE_COUNT + face.  The band map
    is a per-frame host fact and the face is a per-row b61 fact, so neither
    alone identifies the texture layer a sample actually read.
    """
    if evidence is None:
        return None
    provenance = retained_probe_provenance_tuple(row)
    if provenance is None:
        return None
    role, face = provenance[0], provenance[1]
    return int(evidence["capture_bands"][role]) * PROBE_LAYER_FACE_COUNT + face


def classify_retained_provenance_transition(
    a: dict,
    b: dict,
    evidence_a: dict | None,
    evidence_b: dict | None,
) -> tuple[str, str]:
    """Split one cell's frame-pair provenance change by layer-content evidence.

    Returns (transition, layer_epoch_state).  The decisive fact is whether the
    physical layer the B sample reads had its pixels rewritten across the pair:
    the content epoch at that layer index either survived or advanced.  A role
    relabel with a surviving epoch is a pure logical band rotation and moved no
    texel; an advanced epoch is an actual recapture.  Either side lacking the
    layer-granular schema is unmeasured, never inferred.
    """
    layer_a = retained_probe_physical_layer(a, evidence_a)
    layer_b = retained_probe_physical_layer(b, evidence_b)
    if evidence_a is None or evidence_b is None or layer_a is None or layer_b is None:
        return (PROVENANCE_UNMEASURED, PROVENANCE_UNMEASURED)
    epoch_before = evidence_a["layer_content_epochs"][layer_b]
    epoch_after = evidence_b["layer_content_epochs"][layer_b]
    layer_state = (
        LAYER_EPOCH_SURVIVED if epoch_after == epoch_before else LAYER_EPOCH_ADVANCED
    )
    provenance_a = retained_probe_provenance_tuple(a)
    provenance_b = retained_probe_provenance_tuple(b)
    if provenance_a == provenance_b:
        return (PROVENANCE_TRANSITION_UNCHANGED, layer_state)
    if layer_state == LAYER_EPOCH_ADVANCED:
        return (PROVENANCE_TRANSITION_LAYER_RECAPTURED, layer_state)
    if provenance_a[0] != provenance_b[0]:
        return (PROVENANCE_TRANSITION_BAND_ROTATION, layer_state)
    # Same logical role and retained content: the sample moved to another
    # face/texel/generation of pixels that were never rewritten.
    return (PROVENANCE_TRANSITION_RETAINED_REPROJECTION, layer_state)


def retained_probe_anchor(row: dict):
    for key in ("final_product_world_anchor", "ansi_cell_world_anchor"):
        value = row.get(key)
        if isinstance(value, dict) and all(axis in value for axis in ("x", "y", "z")):
            return value
    return None


def retained_probe_surface_label(row: dict) -> str:
    owner = row.get("final_product_owner_name")
    if owner in (None, "", "unknown"):
        owner = row.get("selected_owner")
    if owner in (None, "", "unknown"):
        return "unknown"
    return str(owner)


def retained_probe_surface_is_metric_surface(row: dict) -> bool:
    """Limit plate metrics to retained world surfaces, not actor/sky sentinels."""
    return retained_probe_surface_label(row) in {
        "terrain",
        "water",
        "static_mesh",
        "mesh",
        "product:terrain",
        "product:water",
        "product:static_mesh",
    }


def _retained_stationary_anchor_pair(a: dict, b: dict, threshold: float = 0.10) -> bool:
    anchor_a = retained_probe_anchor(a)
    anchor_b = retained_probe_anchor(b)
    if anchor_a is None or anchor_b is None:
        return False
    return distance3(anchor_a, anchor_b) <= threshold


def _retained_generation_is_current_previous_flip(a, b) -> bool:
    if a is None or b is None or a == b:
        return False
    normalized = {str(a).lower(), str(b).lower()}
    has_current = any("current" in value or value in ("cur", "0") for value in normalized)
    has_previous = any("previous" in value or value in ("prev", "1") for value in normalized)
    return has_current and has_previous


def _retained_json_key(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _retained_percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(float(fraction) * len(ordered)) - 1)))
    return float(ordered[index])


def summarize_retained_plate_mechanics(frames: dict[int, list[dict]]) -> dict:
    """Compute the mechanical retained-probe plate/wobble diagnostics.

    FL-4359 recurrence guard: a connected Product/source-key component is not
    an exact probe-texel footprint because the namespace also contains keys for
    primitives that legitimately span many cells. Unconditioned fixed-screen
    flips during camera motion also include normal parallax. The stationary-
    anchor subset limits that confound, but exact footprint and retained-thrash
    claims still require (source_probe, source_texel, generation) provenance.
    These formulas guide isolation; they cannot close a visible slab without
    an identical-pose CURRENT-only viewport and matching runtime truth. F10
    colors are intentionally not inputs to either metric.
    """
    frame_ids = sorted(frames.keys())
    component_sizes: list[int] = []
    component_examples: list[dict] = []
    spatial_rows = 0
    spatial_cells = 0
    source_key_groups = 0
    per_frame_component_stats: list[dict] = []
    # Layer-granular capture-lifecycle evidence is a per-frame host fact, so it
    # is read once per frame from any row of that frame rather than per row.
    evidence_by_frame: dict[int, dict | None] = {
        frame_id: (
            probe_capture_lifecycle_evidence(frames[frame_id][0])
            if frames[frame_id]
            else None
        )
        for frame_id in frame_ids
    }
    capture_activity_counts: dict[str, Counter] = {
        label: Counter() for label in PROBE_ROLE_LABELS.values()
    }
    recapture_reason_counts: dict[str, Counter] = {
        label: Counter() for label in PROBE_ROLE_LABELS.values()
    }
    layer_recapture_counts: Counter = Counter()
    layer_evidence_frames = 0
    for frame_id in frame_ids:
        evidence = evidence_by_frame.get(frame_id)
        if evidence is not None:
            layer_evidence_frames += 1
        for role, role_label in PROBE_ROLE_LABELS.items():
            activity = None if evidence is None else evidence["capture_activity_by_probe"]
            capture_activity_counts[role_label][
                PROVENANCE_UNMEASURED if activity is None else activity[role]
            ] += 1
            reasons = None if evidence is None else evidence["recapture_reasons_by_probe"]
            if reasons is None:
                recapture_reason_counts[role_label][PROVENANCE_UNMEASURED] += 1
            elif not reasons[role]:
                # Published empty list: the host scheduled no recapture.
                recapture_reason_counts[role_label]["no_recapture_scheduled"] += 1
            else:
                for cause in reasons[role]:
                    recapture_reason_counts[role_label][cause] += 1
        if evidence is not None and evidence["layers_recaptured_this_frame"] is not None:
            for layer in evidence["layers_recaptured_this_frame"]:
                layer_recapture_counts[str(int(layer))] += 1
        cells_by_source: dict[str, set[tuple[int, int]]] = {}
        generation_by_source: dict[str, set[str]] = {}
        for row in frames[frame_id]:
            # Spatial plates are a property of the retained surface sample set,
            # not only the sparse fixed-screen temporal lane.  Use every row
            # admitted to either world-anchor or screen evidence so the
            # connected-component gate sees contiguous source-key slabs.
            if not (admitted(row, ADMISSION_WORLD) or admitted(row, ADMISSION_SCREEN)):
                continue
            if not retained_probe_surface_is_metric_surface(row):
                continue
            cell = screen_cell_key(row)
            source_key = retained_probe_source_key(row)
            if not cell or source_key is None:
                continue
            spatial_rows += 1
            owner = retained_probe_surface_label(row)
            source = f"{owner}:{_retained_json_key(source_key)}"
            cells_by_source.setdefault(source, set()).add(cell)
            # Absent per-cell provenance is its own reported bucket, so a
            # component example never looks measured when nothing was measured.
            generation_by_source.setdefault(source, set()).add(
                retained_probe_generation_classification(row)
            )
        frame_sizes: list[int] = []
        spatial_cells += sum(len(cells) for cells in cells_by_source.values())
        source_key_groups += len(cells_by_source)
        for source, cells in cells_by_source.items():
            remaining = set(cells)
            while remaining:
                first = remaining.pop()
                stack = [first]
                component = [first]
                while stack:
                    cx, cy = stack.pop()
                    for neighbor in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            stack.append(neighbor)
                            component.append(neighbor)
                size = len(component)
                xs = [cell[0] for cell in component]
                ys = [cell[1] for cell in component]
                component_sizes.append(size)
                frame_sizes.append(size)
                if len(component_examples) < 40 or size > min(example["size"] for example in component_examples):
                    component_examples.append({
                        "frame_id": int(frame_id),
                        "source_key": source,
                        "generation_labels": sorted(generation_by_source.get(source, set())),
                        "size": size,
                        "bbox": {
                            "min_x": min(xs),
                            "min_y": min(ys),
                            "max_x": max(xs),
                            "max_y": max(ys),
                        },
                    })
                    component_examples = sorted(
                        component_examples,
                        key=lambda example: (-int(example["size"]), int(example["frame_id"]), str(example["source_key"])),
                    )[:40]
        per_frame_component_stats.append({
            "frame_id": int(frame_id),
            "component_count": len(frame_sizes),
            "max_component_size": max(frame_sizes) if frame_sizes else 0,
            "median_component_size": _retained_percentile(frame_sizes, 0.50),
            "p90_component_size": _retained_percentile(frame_sizes, 0.90),
        })

    temporal_totals: Counter = Counter()
    temporal_pair_results: list[dict] = []
    for index in range(len(frame_ids) - 1):
        frame_a = frame_ids[index]
        frame_b = frame_ids[index + 1]
        rows_a = {
            screen_cell_key(row): row
            for row in frames[frame_a]
            if admitted(row, ADMISSION_SCREEN)
            and retained_probe_surface_is_metric_surface(row)
            and screen_cell_key(row)
        }
        rows_b = {
            screen_cell_key(row): row
            for row in frames[frame_b]
            if admitted(row, ADMISSION_SCREEN)
            and retained_probe_surface_is_metric_surface(row)
            and screen_cell_key(row)
        }
        pair: Counter = Counter()
        evidence_a = evidence_by_frame.get(frame_a)
        evidence_b = evidence_by_frame.get(frame_b)
        for cell in sorted(set(rows_a) & set(rows_b)):
            a = rows_a[cell]
            b = rows_b[cell]
            source_a = retained_probe_source_key(a)
            source_b = retained_probe_source_key(b)
            generation_a = retained_probe_generation_label(a)
            generation_b = retained_probe_generation_label(b)
            if source_a is None or source_b is None:
                continue
            pair["comparable_screen_cells"] += 1
            source_flip = _retained_json_key(source_a) != _retained_json_key(source_b)
            stationary = _retained_stationary_anchor_pair(a, b)
            generation_comparable = generation_a is not None and generation_b is not None
            generation_flip = generation_comparable and generation_a != generation_b
            current_previous_flip = _retained_generation_is_current_previous_flip(generation_a, generation_b)
            pair["source_key_flip_cells"] += int(source_flip)
            pair["stationary_world_anchor_cells"] += int(stationary)
            pair["stationary_source_key_flip_cells"] += int(stationary and source_flip)
            pair["generation_comparable_cells"] += int(generation_comparable)
            pair["generation_flip_cells"] += int(generation_flip)
            pair["current_previous_generation_flip_cells"] += int(current_previous_flip)
            pair["stationary_generation_flip_cells"] += int(stationary and generation_flip)
            pair["stationary_current_previous_generation_flip_cells"] += int(stationary and current_previous_flip)
            pair["key_or_generation_flip_cells"] += int(source_flip or generation_flip)
            pair["stationary_key_or_generation_flip_cells"] += int(stationary and (source_flip or generation_flip))
            # Absent per-cell provenance is counted, not silently dropped, so
            # the generation and CURRENT/PREVIOUS lanes report their own
            # unmeasured population instead of an implied concrete label.
            pair["generation_unmeasured_cells"] += int(not generation_comparable)
            pair["stationary_generation_unmeasured_cells"] += int(
                stationary and not generation_comparable
            )
            pair["current_previous_classified_cells"] += int(generation_comparable)
            pair["current_previous_unmeasured_cells"] += int(not generation_comparable)
            transition, layer_state = classify_retained_provenance_transition(
                a, b, evidence_a, evidence_b
            )
            pair["layer_epoch_unmeasured_cells"] += int(
                layer_state == PROVENANCE_UNMEASURED
            )
            pair["layer_epoch_measured_cells"] += int(
                layer_state != PROVENANCE_UNMEASURED
            )
            # Survival count: compared samples sitting on a physical layer
            # whose content epoch did not advance across this frame pair.
            pair["layer_epoch_survived_cells"] += int(
                layer_state == LAYER_EPOCH_SURVIVED
            )
            pair["layer_epoch_advanced_cells"] += int(
                layer_state == LAYER_EPOCH_ADVANCED
            )
            pair["stationary_layer_epoch_survived_cells"] += int(
                stationary and layer_state == LAYER_EPOCH_SURVIVED
            )
            provenance_changed = transition not in (
                PROVENANCE_UNMEASURED,
                PROVENANCE_TRANSITION_UNCHANGED,
            )
            pair["provenance_unchanged_cells"] += int(
                transition == PROVENANCE_TRANSITION_UNCHANGED
            )
            pair["provenance_change_cells"] += int(provenance_changed)
            pair["provenance_change_unmeasured_cells"] += int(
                transition == PROVENANCE_UNMEASURED
            )
            pair["provenance_change_band_rotation_cells"] += int(
                transition == PROVENANCE_TRANSITION_BAND_ROTATION
            )
            pair["provenance_change_layer_recaptured_cells"] += int(
                transition == PROVENANCE_TRANSITION_LAYER_RECAPTURED
            )
            pair["provenance_change_retained_reprojection_cells"] += int(
                transition == PROVENANCE_TRANSITION_RETAINED_REPROJECTION
            )
            pair["stationary_provenance_change_band_rotation_cells"] += int(
                stationary and transition == PROVENANCE_TRANSITION_BAND_ROTATION
            )
            pair["stationary_provenance_change_layer_recaptured_cells"] += int(
                stationary and transition == PROVENANCE_TRANSITION_LAYER_RECAPTURED
            )
        temporal_totals.update(pair)
        comparable = int(pair.get("comparable_screen_cells", 0))
        generation_comparable = int(pair.get("generation_comparable_cells", 0))
        temporal_pair_results.append({
            "frame_a": int(frame_a),
            "frame_b": int(frame_b),
            **dict(pair),
            "source_key_flip_pct": (
                float(pair.get("source_key_flip_cells", 0)) * 100.0 / comparable
                if comparable else 0.0
            ),
            "generation_flip_pct": (
                float(pair.get("generation_flip_cells", 0)) * 100.0 / generation_comparable
                if generation_comparable else 0.0
            ),
        })

    temporal_comparable = int(temporal_totals.get("comparable_screen_cells", 0))
    generation_comparable = int(temporal_totals.get("generation_comparable_cells", 0))
    stationary_cells = int(temporal_totals.get("stationary_world_anchor_cells", 0))

    def temporal_pct(name: str, denominator: int = temporal_comparable) -> float:
        return float(temporal_totals.get(name, 0)) * 100.0 / denominator if denominator else 0.0

    spatial_component_count = len(component_sizes)
    return {
        "retained_plate_mechanics_present": spatial_component_count > 0 or temporal_comparable > 0,
        "retained_plate_source_key_rows": spatial_rows,
        "retained_plate_source_key_screen_cells": spatial_cells,
        "retained_plate_source_key_group_count": source_key_groups,
        "retained_plate_component_count": spatial_component_count,
        "retained_plate_component_max_size": max(component_sizes) if component_sizes else 0,
        "retained_plate_component_median_size": _retained_percentile(component_sizes, 0.50),
        "retained_plate_component_p90_size": _retained_percentile(component_sizes, 0.90),
        "retained_plate_component_ge4_count": sum(1 for size in component_sizes if size >= 4),
        "retained_plate_component_ge8_count": sum(1 for size in component_sizes if size >= 8),
        "retained_plate_component_examples": component_examples,
        "retained_plate_component_per_frame": per_frame_component_stats,
        "retained_plate_temporal_comparable_screen_cells": temporal_comparable,
        "retained_plate_temporal_pair_count": sum(
            1 for pair in temporal_pair_results if int(pair.get("comparable_screen_cells", 0)) > 0
        ),
        "retained_plate_source_key_flip_cells": int(temporal_totals.get("source_key_flip_cells", 0)),
        "retained_plate_source_key_flip_pct": temporal_pct("source_key_flip_cells"),
        "retained_plate_stationary_world_anchor_cells": stationary_cells,
        "retained_plate_stationary_source_key_flip_cells": int(temporal_totals.get("stationary_source_key_flip_cells", 0)),
        "retained_plate_stationary_source_key_flip_pct": temporal_pct(
            "stationary_source_key_flip_cells",
            stationary_cells,
        ),
        "retained_plate_generation_comparable_cells": generation_comparable,
        "retained_plate_generation_flip_cells": int(temporal_totals.get("generation_flip_cells", 0)),
        "retained_plate_generation_flip_pct": temporal_pct("generation_flip_cells", generation_comparable),
        "retained_plate_current_previous_generation_flip_cells": int(
            temporal_totals.get("current_previous_generation_flip_cells", 0)
        ),
        "retained_plate_stationary_generation_flip_cells": int(temporal_totals.get("stationary_generation_flip_cells", 0)),
        "retained_plate_stationary_current_previous_generation_flip_cells": int(
            temporal_totals.get("stationary_current_previous_generation_flip_cells", 0)
        ),
        "retained_plate_key_or_generation_flip_cells": int(temporal_totals.get("key_or_generation_flip_cells", 0)),
        "retained_plate_key_or_generation_flip_pct": temporal_pct("key_or_generation_flip_cells"),
        "retained_plate_stationary_key_or_generation_flip_cells": int(
            temporal_totals.get("stationary_key_or_generation_flip_cells", 0)
        ),
        "retained_plate_stationary_key_or_generation_flip_pct": temporal_pct(
            "stationary_key_or_generation_flip_cells",
            stationary_cells,
        ),
        "retained_plate_generation_capture_present": generation_comparable > 0,
        # Explicit absent-evidence populations for the three provenance lanes.
        "retained_plate_generation_unmeasured_cells": int(
            temporal_totals.get("generation_unmeasured_cells", 0)
        ),
        "retained_plate_stationary_generation_unmeasured_cells": int(
            temporal_totals.get("stationary_generation_unmeasured_cells", 0)
        ),
        "retained_plate_current_previous_classified_cells": int(
            temporal_totals.get("current_previous_classified_cells", 0)
        ),
        "retained_plate_current_previous_unmeasured_cells": int(
            temporal_totals.get("current_previous_unmeasured_cells", 0)
        ),
        # Layer-granular capture-lifecycle evidence (host O1 schema).
        "retained_plate_layer_evidence_present": layer_evidence_frames > 0,
        "retained_plate_layer_evidence_frames": layer_evidence_frames,
        "retained_plate_layer_evidence_frame_count": len(frame_ids),
        "retained_plate_layer_epoch_measured_cells": int(
            temporal_totals.get("layer_epoch_measured_cells", 0)
        ),
        "retained_plate_layer_epoch_unmeasured_cells": int(
            temporal_totals.get("layer_epoch_unmeasured_cells", 0)
        ),
        "retained_plate_layer_epoch_survived_cells": int(
            temporal_totals.get("layer_epoch_survived_cells", 0)
        ),
        "retained_plate_layer_epoch_advanced_cells": int(
            temporal_totals.get("layer_epoch_advanced_cells", 0)
        ),
        "retained_plate_stationary_layer_epoch_survived_cells": int(
            temporal_totals.get("stationary_layer_epoch_survived_cells", 0)
        ),
        # The rotation-versus-recapture split this evidence exists to answer.
        "retained_plate_provenance_unchanged_cells": int(
            temporal_totals.get("provenance_unchanged_cells", 0)
        ),
        "retained_plate_provenance_change_cells": int(
            temporal_totals.get("provenance_change_cells", 0)
        ),
        "retained_plate_provenance_change_unmeasured_cells": int(
            temporal_totals.get("provenance_change_unmeasured_cells", 0)
        ),
        "retained_plate_provenance_change_band_rotation_cells": int(
            temporal_totals.get("provenance_change_band_rotation_cells", 0)
        ),
        "retained_plate_provenance_change_layer_recaptured_cells": int(
            temporal_totals.get("provenance_change_layer_recaptured_cells", 0)
        ),
        "retained_plate_provenance_change_retained_reprojection_cells": int(
            temporal_totals.get("provenance_change_retained_reprojection_cells", 0)
        ),
        "retained_plate_stationary_provenance_change_band_rotation_cells": int(
            temporal_totals.get("stationary_provenance_change_band_rotation_cells", 0)
        ),
        "retained_plate_stationary_provenance_change_layer_recaptured_cells": int(
            temporal_totals.get(
                "stationary_provenance_change_layer_recaptured_cells", 0
            )
        ),
        "retained_plate_capture_activity_by_probe_counts": {
            role_label: dict(counts)
            for role_label, counts in capture_activity_counts.items()
        },
        "retained_plate_recapture_reason_by_probe_counts": {
            role_label: dict(counts)
            for role_label, counts in recapture_reason_counts.items()
        },
        "retained_plate_layer_recapture_frame_counts": dict(layer_recapture_counts),
        "retained_plate_temporal_pair_results": temporal_pair_results,
    }


def distance3(a: dict, b: dict) -> float:
    return math.sqrt(
        (a["x"] - b["x"]) ** 2
        + (a["y"] - b["y"]) ** 2
        + (a["z"] - b["z"]) ** 2
    )


def comparable_anchor(a: dict, b: dict, threshold: float = 0.10) -> bool:
    aw = a.get("ansi_cell_world_anchor", {})
    bw = b.get("ansi_cell_world_anchor", {})
    if not aw or not bw:
        return False
    return distance3(aw, bw) <= threshold


def screen_cell_distance(a: dict, b: dict) -> float:
    """Distance between two projected ANSI occurrences of a world product."""
    def xy(row: dict) -> tuple[float, float]:
        cell = row.get("screen_cell")
        if isinstance(cell, dict):
            return float(cell.get("x", 0.0)), float(cell.get("y", 0.0))
        if isinstance(cell, (list, tuple)) and len(cell) >= 2:
            return float(cell[0]), float(cell[1])
        return 0.0, 0.0

    ax, ay = xy(a)
    bx, by = xy(b)
    return math.hypot(ax - bx, ay - by)


def _water_animation_time_advanced(a: dict, b: dict) -> bool:
    """Return whether the Product-owned water clock actually advanced.

    Water ownership alone does not authorize a visible payload change.  The
    renderer's `product_water_animation_time()` reads Product time directly,
    so a fixed-clock or missing-clock comparison must fail closed as shimmer.
    """
    def captured_time(row: dict) -> object:
        value = row.get("product_time_seconds")
        if value is None and isinstance(row.get("frame_trace"), dict):
            value = row["frame_trace"].get("product_time_seconds")
        return value

    time_a = captured_time(a)
    time_b = captured_time(b)
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in (time_a, time_b)
    ):
        return False
    return float(time_a) != float(time_b)


def classify_pair(a: dict, b: dict) -> str:
    if not comparable_anchor(a, b):
        return "different_world_sample"
    if has_current_product_capture(a) and has_current_product_capture(b):
        if final_product_identity_key(a) != final_product_identity_key(b):
            if a.get("final_product_owner") != b.get("final_product_owner"):
                return "owner_instability"
            return "product_payload_instability"
        if a.get("cloud_shadow_phase") != b.get("cloud_shadow_phase"):
            return "dynamic_cloud_shadow_animation"
        if a.get("terrain_product_detail") != b.get("terrain_product_detail"):
            return "terrain_product_detail_churn"
        secondary_a = (
            a.get("secondary_kind"),
            a.get("secondary_source_id"),
            a.get("secondary_glyph"),
            a.get("secondary_coverage"),
            a.get("secondary_alpha"),
            a.get("secondary_blend"),
            a.get("secondary_intensity"),
        )
        secondary_b = (
            b.get("secondary_kind"),
            b.get("secondary_source_id"),
            b.get("secondary_glyph"),
            b.get("secondary_coverage"),
            b.get("secondary_alpha"),
            b.get("secondary_blend"),
            b.get("secondary_intensity"),
        )
        if secondary_a != secondary_b:
            if a.get("secondary_kind") in (9, 11) or b.get("secondary_kind") in (9, 11):
                return "dynamic_secondary_animation"
            return "secondary_payload_instability"
        if world_product_signature_fields(a) != world_product_signature_fields(b):
            owner_a = a.get("final_product_owner_name")
            owner_b = b.get("final_product_owner_name")
            if owner_a == "actor_sprite" and owner_b == "actor_sprite":
                return "dynamic_actor_animation"
            if (
                owner_a == "water"
                and owner_b == "water"
                and _water_animation_time_advanced(a, b)
            ):
                return "dynamic_water_animation"
            return "final_paint_or_glyph_instability"
        return "stable"
    if a.get("selected_owner") == "actor_sprite" and b.get("selected_owner") == "actor_sprite":
        if a.get("actor_rect_min_px") != b.get("actor_rect_min_px"):
            return "actor_projection_snap"
        if a.get("actor_rect_max_px") != b.get("actor_rect_max_px"):
            return "actor_projection_snap"
    if product_key(a) != product_key(b):
        if product_key_without_cloud_shadow(a) == product_key_without_cloud_shadow(b):
            return "dynamic_cloud_shadow_animation"
        if a.get("selected_owner") != b.get("selected_owner"):
            return "owner_instability"
        if a.get("terrain_cell") != b.get("terrain_cell"):
            return "terrain_sample_key_drift"
        if foliage_product_key(a) != foliage_product_key(b):
            if a.get("foliage_role") == 4 or b.get("foliage_role") == 4:
                return "flower_product_payload_instability"
            return "foliage_product_payload_instability"
        if a.get("water_phase") != b.get("water_phase"):
            return "water_phase_camera_drift"
        if a.get("terrain_product_detail") != b.get("terrain_product_detail"):
            return "terrain_product_detail_churn"
        return "product_payload_instability"
    secondary_a = (
        a.get("secondary_kind"),
        a.get("secondary_source_id"),
        a.get("secondary_glyph"),
        a.get("secondary_coverage"),
        a.get("secondary_alpha"),
        a.get("secondary_blend"),
        a.get("secondary_intensity"),
    )
    secondary_b = (
        b.get("secondary_kind"),
        b.get("secondary_source_id"),
        b.get("secondary_glyph"),
        b.get("secondary_coverage"),
        b.get("secondary_alpha"),
        b.get("secondary_blend"),
        b.get("secondary_intensity"),
    )
    if secondary_a != secondary_b:
        if a.get("secondary_kind") in (9, 11) or b.get("secondary_kind") in (9, 11):
            return "dynamic_secondary_animation"
        return "secondary_payload_instability"
    if world_product_signature_fields(a) != world_product_signature_fields(b):
        if a.get("selected_owner") == "actor_sprite" and b.get("selected_owner") == "actor_sprite":
            return "dynamic_actor_animation"
        if (
            a.get("selected_owner") == "water"
            and b.get("selected_owner") == "water"
            and _water_animation_time_advanced(a, b)
        ):
            return "dynamic_water_animation"
        return "final_paint_or_glyph_instability"
    return "stable"


def selected_surface_identity_key(row: dict) -> tuple:
    # Scene/view revision and projection are join/provenance facts. They are
    # validated separately and must not create semantic churn for a stable
    # selected surface across a moving camera.
    return (
        row.get("selected_surface_owner"),
        row.get("selected_surface_object"),
        row.get("selected_surface_primitive"),
        row.get("selected_surface_material"),
    )


def changed_selected_surface_identity_fields(a: dict, b: dict) -> list[str]:
    fields = (
        "selected_surface_owner",
        "selected_surface_object",
        "selected_surface_primitive",
        "selected_surface_material",
    )
    return [name for name in fields if a.get(name) != b.get(name)]


def changed_terrain_detail_fields(a: dict, b: dict) -> list[str]:
    if a.get("terrain_product_detail") == b.get("terrain_product_detail"):
        return []
    da = unpack_terrain_detail(a.get("terrain_product_detail"))
    db = unpack_terrain_detail(b.get("terrain_product_detail"))
    names = sorted(set(da.keys()) | set(db.keys()))
    return [name for name in names if da.get(name) != db.get(name)]


def _continuous_product_sample_key(row: dict) -> tuple | None:
    """Return Product's exact authored terrain-colour sample identity.

    b55.anchor is a semantic terrain-cell center. It is intentionally shared by
    all 64 samples of Product's 8x8 continuous paint lattice and therefore
    cannot grade foreground/background stability. The capture-only sidecar
    transports the exact float-bit sample consumed by the terrain compiler.
    """
    if row.get("capture_schema") != CURRENT_SPARSE_CAPTURE_SCHEMA:
        return None
    if int(row.get("final_product_owner", 0)) != 1:
        return None
    if not bool(row.get("final_product_continuous_sample_valid", False)):
        return None
    bits = row.get("final_product_continuous_sample_bits")
    if not isinstance(bits, (list, tuple)) or len(bits) != 3:
        return None
    try:
        sample_bits = tuple(int(value) & 0xFFFFFFFF for value in bits)
    except (TypeError, ValueError):
        return None
    return (
        final_product_identity_key(row),
        sample_bits,
    )


def _terrain_colour_witness_bits(row: dict) -> dict[str, tuple[int, ...]] | None:
    """Return the complete ordered Product terrain-colour compiler witness."""
    if row.get("capture_schema") != CURRENT_SPARSE_CAPTURE_SCHEMA:
        return None
    if int(row.get("final_product_owner", 0)) != 1:
        return None
    if not bool(row.get("final_product_terrain_colour_witness_valid", False)):
        return None
    raw = row.get("final_product_terrain_colour_witness_bits")
    if not isinstance(raw, dict):
        return None
    witness: dict[str, tuple[int, ...]] = {}
    for stage in PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES:
        value = raw.get(stage)
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            witness[stage] = tuple(int(word) & 0xFFFFFFFF for word in value)
        except (TypeError, ValueError):
            return None
    return witness


def _product_finalization_signature_bits(
    row: dict, field: str
) -> tuple[int, int, int, int] | None:
    """Return one exact quantized Product presentation boundary."""
    value = row.get(field)
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        signature = tuple(int(word) & 0xFFFFFFFF for word in value)
    except (TypeError, ValueError):
        return None
    return signature


def summarize_continuous_product_colour_stability(
    frame_a: list[dict],
    frame_b: list[dict],
    details_limit: int = 50,
) -> dict:
    """Grade FG/BG only where Product consumed the identical terrain sample.

    The exact sample key owns comparability. Product semantic changes remain a
    separate exclusion rather than being mislabeled as continuous-colour
    instability. Duplicate projections are assigned one-to-one by screen
    distance so no frame-B observation can satisfy two frame-A samples.
    """
    terrain_a = [row for row in frame_a if int(row.get("final_product_owner", 0)) == 1]
    terrain_b = [row for row in frame_b if int(row.get("final_product_owner", 0)) == 1]
    invalid_a = sum(1 for row in terrain_a if _continuous_product_sample_key(row) is None)
    invalid_b = sum(1 for row in terrain_b if _continuous_product_sample_key(row) is None)
    invalid_witness_a = sum(
        1 for row in terrain_a if _terrain_colour_witness_bits(row) is None
    )
    invalid_witness_b = sum(
        1 for row in terrain_b if _terrain_colour_witness_bits(row) is None
    )
    finalization_fields = (
        "final_product_pre_finalization_signature_bits",
        "final_product_post_finalization_signature_bits",
    )
    invalid_finalization_a = sum(
        1
        for row in terrain_a
        if any(
            _product_finalization_signature_bits(row, field) is None
            for field in finalization_fields
        )
    )
    invalid_finalization_b = sum(
        1
        for row in terrain_b
        if any(
            _product_finalization_signature_bits(row, field) is None
            for field in finalization_fields
        )
    )
    eligible_exact_samples_a = len(terrain_a) - invalid_a
    eligible_exact_samples_b = len(terrain_b) - invalid_b
    by_key_b: dict[tuple, list[tuple[int, dict]]] = {}
    for index, row in enumerate(frame_b):
        key = _continuous_product_sample_key(row)
        if key is not None:
            by_key_b.setdefault(key, []).append((index, row))
    # FL-4775 observer-power law: exclusions must be decomposed so an
    # identity or sampling instability cannot hide inside one opaque
    # "unmatched" bucket. Secondary indexes classify why a frame-A sample
    # found no exact partner in frame B.
    identity_keys_b = {key[0] for key in by_key_b}
    sample_bits_keys_b = {key[1] for key in by_key_b}
    edges: list[tuple[float, int, int]] = []
    for index_a, row_a in enumerate(frame_a):
        key = _continuous_product_sample_key(row_a)
        if key is None:
            continue
        for index_b, row_b in by_key_b.get(key, []):
            edges.append((screen_cell_distance(row_a, row_b), index_a, index_b))
    edges.sort()
    matches: dict[int, dict] = {}
    used_b: set[int] = set()
    for _screen_distance, index_a, index_b in edges:
        if index_a in matches or index_b in used_b:
            continue
        matches[index_a] = frame_b[index_b]
        used_b.add(index_b)

    counts: Counter = Counter()
    examples: list[dict] = []
    for index_a, row_a in enumerate(frame_a):
        key = _continuous_product_sample_key(row_a)
        if key is None:
            continue
        row_b = matches.get(index_a)
        if row_b is None:
            counts["unmatched_exact_samples"] += 1
            if key in by_key_b:
                counts["unmatched_partner_consumed"] += 1
            elif key[0] in identity_keys_b:
                counts["unmatched_sample_bits_changed_same_identity"] += 1
            elif key[1] in sample_bits_keys_b:
                counts["unmatched_identity_changed_same_sample_bits"] += 1
            else:
                counts["unmatched_identity_and_sample_absent"] += 1
            continue
        counts["matched_exact_samples"] += 1
        if product_key(row_a) != product_key(row_b):
            counts["semantic_state_changed_samples"] += 1
            continue
        counts["comparable_stable_semantic_samples"] += 1
        witness_a = _terrain_colour_witness_bits(row_a)
        witness_b = _terrain_colour_witness_bits(row_b)
        first_changed_stage = None
        if witness_a is not None and witness_b is not None:
            for stage in PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES:
                if witness_a[stage] != witness_b[stage]:
                    first_changed_stage = stage
                    break
        pre_a = _product_finalization_signature_bits(
            row_a, "final_product_pre_finalization_signature_bits"
        )
        pre_b = _product_finalization_signature_bits(
            row_b, "final_product_pre_finalization_signature_bits"
        )
        post_a = _product_finalization_signature_bits(
            row_a, "final_product_post_finalization_signature_bits"
        )
        post_b = _product_finalization_signature_bits(
            row_b, "final_product_post_finalization_signature_bits"
        )
        if first_changed_stage is None and pre_a is not None and pre_b is not None:
            if pre_a != pre_b:
                first_changed_stage = "product_record_construction"
        if first_changed_stage is None and post_a is not None and post_b is not None:
            if post_a != post_b:
                first_changed_stage = "product_finalization"
        fg_changed = quantized_rgb(row_a.get("final_product_fg_rgb")) != quantized_rgb(
            row_b.get("final_product_fg_rgb")
        )
        bg_changed = quantized_rgb(row_a.get("final_product_bg_rgb")) != quantized_rgb(
            row_b.get("final_product_bg_rgb")
        )
        if fg_changed or bg_changed:
            counts["colour_changed_samples"] += 1
            divergence = first_changed_stage or "product_staging_publication"
            counts[f"first_divergence:{divergence}"] += 1
            if len(examples) < details_limit:
                examples.append({
                    "sample_key": key,
                    "cell_a": row_a.get("screen_cell"),
                    "cell_b": row_b.get("screen_cell"),
                    "fg_a": row_a.get("final_product_fg_rgb"),
                    "fg_b": row_b.get("final_product_fg_rgb"),
                    "bg_a": row_a.get("final_product_bg_rgb"),
                    "bg_b": row_b.get("final_product_bg_rgb"),
                    "first_divergence": divergence,
                    "witness_a": witness_a,
                    "witness_b": witness_b,
                    "pre_finalization_a": pre_a,
                    "pre_finalization_b": pre_b,
                    "post_finalization_a": post_a,
                    "post_finalization_b": post_b,
                })
        else:
            counts["colour_stable_samples"] += 1
            if first_changed_stage is not None:
                counts["intermediate_changed_final_stable_samples"] += 1
                counts[
                    f"first_stable_output_intermediate_change:{first_changed_stage}"
                ] += 1
    comparable = int(counts.get("comparable_stable_semantic_samples", 0))
    changed = int(counts.get("colour_changed_samples", 0))
    unmatched_total = int(counts.get("unmatched_exact_samples", 0))
    unmatched_decomposed = (
        int(counts.get("unmatched_partner_consumed", 0))
        + int(counts.get("unmatched_sample_bits_changed_same_identity", 0))
        + int(counts.get("unmatched_identity_changed_same_sample_bits", 0))
        + int(counts.get("unmatched_identity_and_sample_absent", 0))
    )
    counts["eligible_exact_samples_a"] = eligible_exact_samples_a
    counts["eligible_exact_samples_b"] = eligible_exact_samples_b
    counts["new_exact_samples_b"] = eligible_exact_samples_b - len(used_b)
    population_accounting_valid = (
        eligible_exact_samples_a
        == int(counts.get("matched_exact_samples", 0)) + unmatched_total
        and eligible_exact_samples_b
        == int(counts.get("matched_exact_samples", 0))
        + int(counts.get("new_exact_samples_b", 0))
        and unmatched_total == unmatched_decomposed
    )
    return {
        "scope": (
            "Terrain Product FG/BG compared only for byte-identical 8x8 "
            "continuous paint samples and stable Product semantic state."
        ),
        "valid": (
            invalid_a == 0
            and invalid_b == 0
            and invalid_witness_a == 0
            and invalid_witness_b == 0
            and invalid_finalization_a == 0
            and invalid_finalization_b == 0
            and comparable > 0
            and population_accounting_valid
        ),
        "invalid_terrain_rows_a": invalid_a,
        "invalid_terrain_rows_b": invalid_b,
        "invalid_colour_witness_rows_a": invalid_witness_a,
        "invalid_colour_witness_rows_b": invalid_witness_b,
        "invalid_finalization_witness_rows_a": invalid_finalization_a,
        "invalid_finalization_witness_rows_b": invalid_finalization_b,
        "counts": dict(counts),
        "unmatched_decomposition_complete": unmatched_total == unmatched_decomposed,
        "population_accounting_valid": population_accounting_valid,
        "examples": examples,
        "instability_pct": float(changed) / float(comparable) * 100.0
        if comparable > 0 else 0.0,
        "examples": examples,
    }


def compare_frames(frame_a: list[dict], frame_b: list[dict], details_limit: int = 50, comparable_threshold: float = 0.10) -> dict:
    """Compare two frames by matching cells at the same world anchor.

    The handoff says: 'Same world/product anchors keep the same selected product
    id under movement.' So the comparison must match by world anchor proximity,
    NOT by screen cell position. Translation tests move the camera, so the same
    world point appears at different screen cells. Matching by screen cell would
    drop all changed-world-sample rows and make translation look clean.
    """
    # Build a spatial index of frame B by world anchor XZ. The comparable-anchor
    # contract below is 0.10 world units, so the index uses that same scale; a
    # wider bucket silently recreates the old broad nearest-neighbor scan.
    bucket_size = comparable_threshold
    # Product-anchor provenance is an admission condition for this metric.
    # Invalid rows remain visible in diagnostics, but never contribute a
    # comparable pair or denominator cell.
    invalid_product_anchor_rows_a = sum(
        1
        for row in frame_a
        if admitted(row, ADMISSION_WORLD) and not has_current_product_anchor_provenance(row)
    )
    invalid_product_anchor_rows_b = sum(
        1
        for row in frame_b
        if admitted(row, ADMISSION_WORLD) and not has_current_product_anchor_provenance(row)
    )
    b_index: dict[tuple[int, int], list[tuple[int, dict]]] = {}
    for bi, r in enumerate(frame_b):
        if not admitted(r, ADMISSION_WORLD) or not has_current_product_anchor_provenance(r):
            continue
        aw = r.get("ansi_cell_world_anchor", {})
        if not aw or (aw.get("x", 0) == 0 and aw.get("z", 0) == 0):
            continue
        bx = int(math.floor(float(aw.get("x", 0.0)) / bucket_size))
        bz = int(math.floor(float(aw.get("z", 0.0)) / bucket_size))
        b_index.setdefault((bx, bz), []).append((bi, r))

    # Perspective can project one world product into several ANSI cells. Build
    # one global occurrence assignment so two frame-A cells can never reuse one
    # frame-B readback row. World-anchor distance owns comparability; projected
    # screen distance only disambiguates duplicate occurrences of that anchor.
    candidate_edges: list[tuple[float, float, int, int]] = []
    for ai, a in enumerate(frame_a):
        if not admitted(a, ADMISSION_WORLD) or not has_current_product_anchor_provenance(a):
            continue
        aw = a.get("ansi_cell_world_anchor", {})
        if not aw or (aw.get("x", 0) == 0 and aw.get("z", 0) == 0):
            continue
        ax = int(math.floor(float(aw.get("x", 0.0)) / bucket_size))
        az = int(math.floor(float(aw.get("z", 0.0)) / bucket_size))
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for bi, b in b_index.get((ax + dx, az + dz), []):
                    bw = b.get("ansi_cell_world_anchor", {})
                    if not bw:
                        continue
                    anchor_dist = distance3(aw, bw)
                    if anchor_dist <= comparable_threshold:
                        candidate_edges.append((anchor_dist, screen_cell_distance(a, b), ai, bi))
    candidate_edges.sort()
    occurrence_matches: dict[int, tuple[dict, float]] = {}
    used_b: set[int] = set()
    for anchor_dist, _screen_dist, ai, bi in candidate_edges:
        if ai in occurrence_matches or bi in used_b:
            continue
        occurrence_matches[ai] = (frame_b[bi], anchor_dist)
        used_b.add(bi)

    cell_mask_owner_stability = summarize_cell_mask_owner_stability(
        frame_a,
        frame_b,
        comparable_threshold=comparable_threshold,
        details_limit=details_limit,
    )
    continuous_product_colour_stability = (
        summarize_continuous_product_colour_stability(
            frame_a, frame_b, details_limit=details_limit
        )
    )
    # The current capture publishes four b55 quarter candidates but Product may
    # elect a fifth, center-sampled whole-cell nominee. Therefore the historical
    # winner-to-quarter membership replay is not a complete candidate set and
    # cannot carry causal evidence for current captures.
    cell_mask_candidate_membership = {
        "scope": "historical four-quarter replay",
        "valid": False,
        "invalid_reason": "current_capture_missing_cell_owned_center_nominee_token",
        "counts": {},
        "excluded_reasons": {},
        "examples": [],
    }

    classifications: Counter = Counter()
    split_counts: Counter = Counter()
    split_surface_counts: dict[str, Counter] = {
        "selected_surface_identity_churn": Counter(),
        "final_product_identity_churn": Counter(),
        "final_product_cell_churn": Counter(),
        "post_surface_bounce": Counter(),
        "final_cell_signature_churn": Counter(),
        "water_phase_churn": Counter(),
        "cloud_shadow_only": Counter(),
        "weather_effect_paint": Counter(),
        "final_paint_only": Counter(),
        "snap_phase_change": Counter(),
        "final_product_glyph_change": Counter(),
        "cell_mask_glyph_change": Counter(),
        "cell_mask_winner_key_change": Counter(),
        "cell_mask_same_winner_key_glyph_change": Counter(),
        "cell_mask_winner_priority_change": Counter(),
        "cell_mask_winner_slot_change": Counter(),
        "cell_mask_winner_provenance_unavailable": Counter(),
        "discrete_glyph_or_mask_change": Counter(),
        "camera_dependent_colour_only": Counter(),
    }
    final_signature_field_counts: Counter = Counter()
    final_signature_field_surface_counts: Counter = Counter()
    cell_mask_winner_key_field_counts: Counter = Counter()
    cell_mask_winner_key_field_surface_counts: Counter = Counter()
    cell_mask_winner_priority_transition_counts: Counter = Counter()
    cell_mask_glyph_change_priority_transition_counts: Counter = Counter()
    cell_mask_glyph_change_priority_transition_surface_counts: Counter = Counter()
    cell_mask_glyph_change_mechanism_counts: Counter = Counter()
    cell_mask_glyph_change_mechanism_surface_counts: Counter = Counter()
    cell_mask_glyph_change_key_field_transition_counts: Counter = Counter()
    terrain_detail_field_counts: Counter = Counter()
    terrain_detail_field_surface_counts: Counter = Counter()
    selected_surface_identity_field_counts: Counter = Counter()
    selected_surface_identity_field_surface_counts: Counter = Counter()
    original_shimmer_surface_counts: Counter = Counter()
    original_shimmer_class_surface_counts: Counter = Counter()
    detail_rows: list[dict] = []
    total = 0
    anchor_residual_sum = 0.0
    anchor_residual_max = 0.0

    for ai, a in enumerate(frame_a):
        if not admitted(a, ADMISSION_WORLD) or not has_current_product_anchor_provenance(a):
            continue
        aw = a.get("ansi_cell_world_anchor", {})
        if not aw or (aw.get("x", 0) == 0 and aw.get("z", 0) == 0):
            continue  # skip rows with no world anchor (non-terrain)
        total += 1
        match = occurrence_matches.get(ai)
        if match is None:
            classifications["different_world_sample"] += 1
            continue
        best_b, best_dist = match
        anchor_residual_sum += best_dist
        anchor_residual_max = max(anchor_residual_max, best_dist)

        # Use the closest match for classification.
        cls = classify_pair(a, best_b)
        classifications[cls] += 1
        surface = surface_bucket(a)
        product_surface = product_surface_bucket(a)
        if (
            cls not in NON_COMPARABLE_CLASSIFICATIONS
            and cls not in ANIMATION_CLASSIFICATIONS
            and cls != "stable"
        ):
            original_shimmer_surface_counts[surface] += 1
            original_shimmer_class_surface_counts[f"{cls}:{surface}"] += 1
        visible_fields_a, visible_fields_b = comparable_final_cell_signature_fields(a, best_b)
        visible_signature_changed = (
            visible_fields_a != visible_fields_b and cls not in ANIMATION_CLASSIFICATIONS
        )
        product_glyph_changed = (
            a.get("final_product_glyph") != best_b.get("final_product_glyph")
        )
        colour_changed = (
            quantized_rgb(a.get("final_product_fg_rgb"))
            != quantized_rgb(best_b.get("final_product_fg_rgb"))
            or quantized_rgb(a.get("final_product_bg_rgb"))
            != quantized_rgb(best_b.get("final_product_bg_rgb"))
        )
        winner_a = cell_mask_winner_witness(a)
        winner_b = cell_mask_winner_witness(best_b)
        winner_available = bool(winner_a["available"] and winner_b["available"])
        winner_key_changed = bool(
            winner_available and winner_a["key_token"] != winner_b["key_token"]
        )
        winner_glyph_changed = bool(
            winner_available and winner_a["glyph"] != winner_b["glyph"]
        )
        winner_priority_changed = bool(
            winner_available and winner_a["priority"] != winner_b["priority"]
        )
        winner_slot_changed = bool(
            winner_available and winner_a["slot"] != winner_b["slot"]
        )
        winner_relation_a = cell_mask_winner_observed_product_relation(a)
        winner_relation_b = cell_mask_winner_observed_product_relation(best_b)
        winner_relation_available = bool(
            winner_relation_a["available"] and winner_relation_b["available"]
        )
        winner_matches_a = bool(winner_relation_a["matches_observed_product"])
        winner_matches_b = bool(winner_relation_b["matches_observed_product"])
        product_identity_changed = (
            final_product_identity_key(a) != final_product_identity_key(best_b)
        )
        diagnostic_pair = cls not in ANIMATION_CLASSIFICATIONS
        if diagnostic_pair:
            if product_glyph_changed:
                split_counts["final_product_glyph_changed_cells"] += 1
                split_surface_counts["final_product_glyph_change"][product_surface] += 1
            if not winner_available:
                split_counts["cell_mask_winner_provenance_unavailable_cells"] += 1
                split_surface_counts["cell_mask_winner_provenance_unavailable"][product_surface] += 1
            else:
                # Priority is the Product rule-family identifier. Preserve both
                # endpoints so a stable-priority glyph mutation cannot be
                # mistaken for a handoff between rule families.
                priority_transition = (
                    f"{winner_a['priority']}->{winner_b['priority']}"
                )
                cell_mask_winner_priority_transition_counts[
                    priority_transition
                ] += 1
                if winner_glyph_changed:
                    split_counts["cell_mask_glyph_changed_cells"] += 1
                    split_surface_counts["cell_mask_glyph_change"][product_surface] += 1
                    cell_mask_glyph_change_priority_transition_counts[
                        priority_transition
                    ] += 1
                    cell_mask_glyph_change_priority_transition_surface_counts[
                        f"{priority_transition}:{product_surface}"
                    ] += 1
                    # Separate identity turnover from a producer returning two
                    # glyphs for the same semantic key. They authorize fixes in
                    # different owners and must never be merged into one churn
                    # count.
                    glyph_change_mechanism = (
                        f"{priority_transition}:key_"
                        f"{'changed' if winner_key_changed else 'stable'}:slot_"
                        f"{'changed' if winner_slot_changed else 'stable'}"
                    )
                    cell_mask_glyph_change_mechanism_counts[
                        glyph_change_mechanism
                    ] += 1
                    cell_mask_glyph_change_mechanism_surface_counts[
                        f"{glyph_change_mechanism}:{product_surface}"
                    ] += 1
                    if winner_key_changed:
                        for field in changed_cell_mask_winner_key_fields(
                            winner_a["key"], winner_b["key"]
                        ):
                            cell_mask_glyph_change_key_field_transition_counts[
                                f"{priority_transition}:{field}"
                            ] += 1
                if winner_key_changed:
                    split_counts["cell_mask_winner_key_changed_cells"] += 1
                    split_surface_counts["cell_mask_winner_key_change"][product_surface] += 1
                    for field in changed_cell_mask_winner_key_fields(
                        winner_a["key"], winner_b["key"]
                    ):
                        cell_mask_winner_key_field_counts[field] += 1
                        cell_mask_winner_key_field_surface_counts[
                            f"{field}:{product_surface}"
                        ] += 1
                if winner_glyph_changed and not winner_key_changed:
                    split_counts["cell_mask_same_winner_key_glyph_changed_cells"] += 1
                    split_surface_counts["cell_mask_same_winner_key_glyph_change"][product_surface] += 1
                if winner_priority_changed:
                    split_counts["cell_mask_winner_priority_changed_cells"] += 1
                    split_surface_counts["cell_mask_winner_priority_change"][product_surface] += 1
                if winner_slot_changed:
                    split_counts["cell_mask_winner_slot_changed_cells"] += 1
                    split_surface_counts["cell_mask_winner_slot_change"][product_surface] += 1
            if winner_relation_available:
                split_counts["cell_mask_winner_relation_available_cells"] += 1
                if winner_matches_a and winner_matches_b:
                    split_counts["cell_mask_winner_matches_observed_product_both_cells"] += 1
                if not winner_matches_a:
                    split_counts["cell_mask_winner_differs_observed_product_a_cells"] += 1
                if not winner_matches_b:
                    split_counts["cell_mask_winner_differs_observed_product_b_cells"] += 1
                if winner_matches_a != winner_matches_b:
                    split_counts["cell_mask_winner_observed_product_relation_changed_cells"] += 1
                if winner_key_changed and (not winner_matches_a or not winner_matches_b):
                    split_counts["cell_mask_key_changed_with_sibling_winner_cells"] += 1
                if winner_key_changed and winner_matches_a and winner_matches_b:
                    split_counts["cell_mask_key_changed_with_observed_product_winner_cells"] += 1
            else:
                split_counts["cell_mask_winner_relation_unavailable_cells"] += 1
            if product_glyph_changed or winner_key_changed or winner_glyph_changed:
                split_counts["discrete_glyph_or_mask_changed_cells"] += 1
                split_surface_counts["discrete_glyph_or_mask_change"][product_surface] += 1
            if (
                colour_changed
                and not product_identity_changed
                and not product_glyph_changed
                and winner_available
                and not winner_key_changed
                and not winner_glyph_changed
            ):
                split_counts["camera_dependent_colour_only_cells"] += 1
                split_surface_counts["camera_dependent_colour_only"][product_surface] += 1
        surface_bounce = False
        if selected_surface_identity_key(a) != selected_surface_identity_key(best_b):
            split_counts["selected_surface_identity_churn_cells"] += 1
            split_surface_counts["selected_surface_identity_churn"][surface] += 1
            for field in changed_selected_surface_identity_fields(a, best_b):
                selected_surface_identity_field_counts[field] += 1
                selected_surface_identity_field_surface_counts[f"{field}:{surface}"] += 1
        if product_identity_changed:
            split_counts["final_product_identity_churn_cells"] += 1
            split_surface_counts["final_product_identity_churn"][surface] += 1
            surface_bounce = True
        if not has_current_product_capture(a) and a.get("terrain_cell") != best_b.get("terrain_cell"):
            split_counts["final_product_cell_churn_cells"] += 1
            split_surface_counts["final_product_cell_churn"][surface] += 1
            surface_bounce = True
        if cls == "dynamic_cloud_shadow_animation":
            split_counts["cloud_shadow_only_cells"] += 1
            split_surface_counts["cloud_shadow_only"][surface] += 1
        if (
            (a.get("selected_owner") == "water" or best_b.get("selected_owner") == "water")
            and a.get("water_phase") != best_b.get("water_phase")
        ) or cls == "dynamic_water_animation":
            split_counts["water_phase_churn_cells"] += 1
            split_surface_counts["water_phase_churn"][surface] += 1
        if cls in ("dynamic_secondary_animation", "secondary_payload_instability"):
            split_counts["weather_effect_paint_cells"] += 1
            split_surface_counts["weather_effect_paint"][surface] += 1
        if cls == "final_paint_or_glyph_instability":
            split_counts["final_paint_only_cells"] += 1
            split_surface_counts["final_paint_only"][surface] += 1
        if a.get("snap_grid_phase") != best_b.get("snap_grid_phase") and (
            a.get("snap_grid_phase") is not None or best_b.get("snap_grid_phase") is not None
        ):
            split_counts["snap_phase_changed_cells"] += 1
            split_surface_counts["snap_phase_change"][surface] += 1
        terrain_detail_fields = changed_terrain_detail_fields(a, best_b)
        if terrain_detail_fields and cls not in ANIMATION_CLASSIFICATIONS:
            for field in terrain_detail_fields:
                terrain_detail_field_counts[field] += 1
                terrain_detail_field_surface_counts[f"{field}:{surface}"] += 1
        if visible_signature_changed:
            split_counts["final_cell_signature_churn_cells"] += 1
            split_surface_counts["final_cell_signature_churn"][product_surface] += 1
            for field in changed_final_cell_signature_fields(a, best_b):
                final_signature_field_counts[field] += 1
                final_signature_field_surface_counts[f"{field}:{product_surface}"] += 1
            surface_bounce = True
        if surface_bounce:
            split_counts["post_surface_bounce_cells"] += 1
            split_surface_counts["post_surface_bounce"][surface] += 1
        if cls != "stable" and cls != "different_world_sample":
            detail_rows.append({
                "cell_a": a.get("screen_cell"),
                "cell_b": best_b.get("screen_cell"),
                "classification": cls,
                "a_owner": a.get("selected_owner"),
                "b_owner": best_b.get("selected_owner"),
                "a_product_owner": a.get("final_product_owner_name"),
                "b_product_owner": best_b.get("final_product_owner_name"),
                "a_product_id": a.get("selected_product_id"),
                "b_product_id": best_b.get("selected_product_id"),
                "a_glyph": a.get("glyph_id"),
                "b_glyph": best_b.get("glyph_id"),
                "a_product_glyph": a.get("final_product_glyph"),
                "b_product_glyph": best_b.get("final_product_glyph"),
                "a_product_fg_rgb": a.get("final_product_fg_rgb"),
                "b_product_fg_rgb": best_b.get("final_product_fg_rgb"),
                "a_product_bg_rgb": a.get("final_product_bg_rgb"),
                "b_product_bg_rgb": best_b.get("final_product_bg_rgb"),
                "a_cell_mask_winner": winner_a,
                "b_cell_mask_winner": winner_b,
                "a_cell_mask_winner_observed_product_relation": winner_relation_a,
                "b_cell_mask_winner_observed_product_relation": winner_relation_b,
                "a_water_phase": a.get("water_phase"),
                "b_water_phase": best_b.get("water_phase"),
                "a_terrain_product_detail": a.get("terrain_product_detail"),
                "b_terrain_product_detail": best_b.get("terrain_product_detail"),
                "a_secondary": {
                    "kind": a.get("secondary_kind"),
                    "source_id": a.get("secondary_source_id"),
                    "glyph": a.get("secondary_glyph"),
                    "coverage": a.get("secondary_coverage"),
                    "alpha": a.get("secondary_alpha"),
                    "blend": a.get("secondary_blend"),
                    "intensity": a.get("secondary_intensity"),
                },
                "b_secondary": {
                    "kind": best_b.get("secondary_kind"),
                    "source_id": best_b.get("secondary_source_id"),
                    "glyph": best_b.get("secondary_glyph"),
                    "coverage": best_b.get("secondary_coverage"),
                    "alpha": best_b.get("secondary_alpha"),
                    "blend": best_b.get("secondary_blend"),
                    "intensity": best_b.get("secondary_intensity"),
                },
                "a_foliage": {
                    "present": a.get("foliage_product_present"),
                    "role": a.get("foliage_role"),
                    "shape": a.get("foliage_shape"),
                    "variant": a.get("foliage_variant"),
                    "density": a.get("foliage_density"),
                    "source": a.get("foliage_source"),
                    "glyph": a.get("foliage_glyph"),
                    "coverage": a.get("foliage_coverage"),
                    "clump_id": a.get("foliage_clump_id"),
                },
                "b_foliage": {
                    "present": best_b.get("foliage_product_present"),
                    "role": best_b.get("foliage_role"),
                    "shape": best_b.get("foliage_shape"),
                    "variant": best_b.get("foliage_variant"),
                    "density": best_b.get("foliage_density"),
                    "source": best_b.get("foliage_source"),
                    "glyph": best_b.get("foliage_glyph"),
                    "coverage": best_b.get("foliage_coverage"),
                    "clump_id": best_b.get("foliage_clump_id"),
                },
                "a_final_ink_pixels": a.get("final_ink_pixels"),
                "b_final_ink_pixels": best_b.get("final_ink_pixels"),
                "a_final_ink_fg": a.get("final_ink_fg"),
                "b_final_ink_fg": best_b.get("final_ink_fg"),
                "a_final_ink_coverage_q": quantized_final_ink_coverage(a),
                "b_final_ink_coverage_q": quantized_final_ink_coverage(best_b),
                "a_anchor": a.get("ansi_cell_world_anchor"),
                "b_anchor": best_b.get("ansi_cell_world_anchor"),
                "anchor_dist": best_dist,
            })

    # FL-4359 false-green fix: jitter_pct must be the shimmer rate over
    # WORLD-ANCHOR-MATCHED *comparable* cells only. no_match + different_world_sample
    # (scrolled off / saw a different world point) are excluded from the DENOMINATOR
    # so fast camera motion cannot dilute the percentage into a false pass.
    # Animations stay in the denominator (they are comparable) but not the numerator
    # (a legitimate change, not shimmer).
    no_match = classifications.get("no_match", 0)
    scrolled = classifications.get("different_world_sample", 0)
    animation = sum(classifications.get(name, 0) for name in ANIMATION_CLASSIFICATIONS)
    comparable = total - no_match - scrolled
    shimmer_cells = comparable - classifications["stable"] - animation
    original_shimmer_class_counts = {
        name: count
        for name, count in sorted(classifications.items())
        if name not in NON_COMPARABLE_CLASSIFICATIONS
        and name not in ANIMATION_CLASSIFICATIONS
        and count > 0
    }
    authored_animation_excluded_class_counts = {
        name: classifications.get(name, 0)
        for name in sorted(ANIMATION_CLASSIFICATIONS)
        if classifications.get(name, 0) > 0
    }
    noncomparable_excluded = no_match + scrolled
    raw_excluded_counts = {
        "raw_matched_cells": total,
        "comparable_cells": comparable,
        "shimmer_cells": shimmer_cells,
        "excluded_cells": noncomparable_excluded + animation,
        "noncomparable_excluded_cells": noncomparable_excluded,
        "no_match_excluded_cells": no_match,
        "different_world_sample_excluded_cells": scrolled,
        "authored_animation_excluded_cells": animation,
        "population_accounting_valid": (
            total == comparable + noncomparable_excluded
            and comparable
            == classifications.get("stable", 0) + animation + shimmer_cells
            and noncomparable_excluded == no_match + scrolled
        ),
    }
    split_pct = {
        key.replace("_cells", "_pct"): (float(value) / comparable * 100.0) if comparable > 0 else 0.0
        for key, value in split_counts.items()
    }
    split_diag = {
        "comparable_cells": comparable,
        "diagnostic_comparable_cells": comparable - animation,
        "counts": dict(split_counts),
        "pct": split_pct,
        "surface_counts": {
            name: dict(counts)
            for name, counts in split_surface_counts.items()
            if counts
        },
    }
    avg_anchor_residual = (anchor_residual_sum / comparable) if comparable > 0 else 0.0
    diagnostic_comparable = comparable - animation
    cell_mask_count_keys = (
        "final_product_glyph_changed_cells",
        "cell_mask_glyph_changed_cells",
        "cell_mask_winner_key_changed_cells",
        "cell_mask_same_winner_key_glyph_changed_cells",
        "cell_mask_winner_priority_changed_cells",
        "cell_mask_winner_slot_changed_cells",
        "cell_mask_winner_provenance_unavailable_cells",
        "discrete_glyph_or_mask_changed_cells",
        "camera_dependent_colour_only_cells",
        "cell_mask_winner_relation_available_cells",
        "cell_mask_winner_matches_observed_product_both_cells",
        "cell_mask_winner_differs_observed_product_a_cells",
        "cell_mask_winner_differs_observed_product_b_cells",
        "cell_mask_winner_observed_product_relation_changed_cells",
        "cell_mask_key_changed_with_sibling_winner_cells",
        "cell_mask_key_changed_with_observed_product_winner_cells",
        "cell_mask_winner_relation_unavailable_cells",
    )
    cell_mask_counts = {
        key: int(split_counts.get(key, 0)) for key in cell_mask_count_keys
    }
    return {
        "product_anchor_provenance_complete": (
            invalid_product_anchor_rows_a == 0 and invalid_product_anchor_rows_b == 0
        ),
        "product_anchor_provenance_invalid_rows_a": invalid_product_anchor_rows_a,
        "product_anchor_provenance_invalid_rows_b": invalid_product_anchor_rows_b,
        "total_matched_cells": total,
        "comparable_cells": comparable,
        "shimmer_cells": shimmer_cells,
        "original_shimmer_cells": shimmer_cells,
        "original_shimmer_pct": (shimmer_cells / comparable * 100) if comparable > 0 else 0.0,
        "original_shimmer_class_counts": original_shimmer_class_counts,
        "original_shimmer_surface_counts": dict(original_shimmer_surface_counts),
        "original_shimmer_class_surface_counts": dict(original_shimmer_class_surface_counts),
        "authored_animation_excluded_cells": animation,
        "authored_animation_excluded_class_counts": authored_animation_excluded_class_counts,
        "raw_excluded_counts": raw_excluded_counts,
        "classifications": dict(classifications),
        "surface_identity_split_diagnostics": split_diag,
        "cell_mask_winner_decomposition": {
            "diagnostic_comparable_cells": diagnostic_comparable,
            "counts": cell_mask_counts,
            "pct": {
                key.replace("_cells", "_pct"): (
                    float(value) / diagnostic_comparable * 100.0
                    if diagnostic_comparable > 0 else 0.0
                )
                for key, value in cell_mask_counts.items()
            },
            "surface_counts": {
                name: dict(split_surface_counts[name])
                for name in (
                    "final_product_glyph_change",
                    "cell_mask_glyph_change",
                    "cell_mask_winner_key_change",
                    "cell_mask_same_winner_key_glyph_change",
                    "cell_mask_winner_priority_change",
                    "cell_mask_winner_slot_change",
                    "cell_mask_winner_provenance_unavailable",
                    "discrete_glyph_or_mask_change",
                    "camera_dependent_colour_only",
                )
                if split_surface_counts[name]
            },
            "winner_key_field_counts": dict(cell_mask_winner_key_field_counts),
            "winner_key_field_surface_counts": dict(
                cell_mask_winner_key_field_surface_counts
            ),
            "winner_priority_transition_counts": dict(
                cell_mask_winner_priority_transition_counts
            ),
            "glyph_change_priority_transition_counts": dict(
                cell_mask_glyph_change_priority_transition_counts
            ),
            "glyph_change_priority_transition_surface_counts": dict(
                cell_mask_glyph_change_priority_transition_surface_counts
            ),
            "glyph_change_mechanism_counts": dict(
                cell_mask_glyph_change_mechanism_counts
            ),
            "glyph_change_mechanism_surface_counts": dict(
                cell_mask_glyph_change_mechanism_surface_counts
            ),
            "glyph_change_key_field_transition_counts": dict(
                cell_mask_glyph_change_key_field_transition_counts
            ),
            "colour_classification_scope": (
                "Quantized Product fg/bg changed while Product identity, Product "
                "glyph, and Product cell-mask winner key/glyph stayed fixed. This "
                "separates continuous colour response; it does not declare the "
                "change expected, stable, or visually accepted."
            ),
        },
        "cell_mask_candidate_membership": cell_mask_candidate_membership,
        "cell_mask_owner_stability": cell_mask_owner_stability,
        "continuous_product_colour_stability": continuous_product_colour_stability,
        "final_signature_churn_attribution": {
            "field_counts": dict(final_signature_field_counts),
            "field_surface_counts": dict(final_signature_field_surface_counts),
            "terrain_detail_field_counts": dict(terrain_detail_field_counts),
            "terrain_detail_field_surface_counts": dict(terrain_detail_field_surface_counts),
            "selected_surface_identity_field_counts": dict(selected_surface_identity_field_counts),
            "selected_surface_identity_field_surface_counts": dict(selected_surface_identity_field_surface_counts),
            "snap_phase_changed_cells": int(split_counts.get("snap_phase_changed_cells", 0)),
            "snap_phase_surface_counts": dict(split_surface_counts.get("snap_phase_change", Counter())),
        },
        "final_cell_signature_churn_pct": split_pct.get("final_cell_signature_churn_pct", 0.0),
        "motion_compensated_final_cell_instability_pct": split_pct.get("final_cell_signature_churn_pct", 0.0),
        "motion_compensated_final_cell_instability_cells": split_counts.get("final_cell_signature_churn_cells", 0),
        "screen_position_residual_world_avg": avg_anchor_residual,
        "screen_position_residual_world_max": anchor_residual_max,
        "selected_surface_identity_churn_pct": split_pct.get("selected_surface_identity_churn_pct", 0.0),
        "post_surface_bounce_pct": split_pct.get("post_surface_bounce_pct", 0.0),
        "final_product_identity_churn_pct": split_pct.get("final_product_identity_churn_pct", 0.0),
        "cloud_shadow_only_pct": split_pct.get("cloud_shadow_only_pct", 0.0),
        "water_phase_churn_pct": split_pct.get("water_phase_churn_pct", 0.0),
        "weather_effect_paint_pct": split_pct.get("weather_effect_paint_pct", 0.0),
        "stable_pct": (classifications["stable"] / comparable * 100) if comparable > 0 else 0,
        "jitter_pct": (shimmer_cells / comparable * 100) if comparable > 0 else 0.0,
        "jitter_pct_over_total_legacy": (
            (comparable - classifications["stable"] - animation) / total * 100
        ) if total > 0 else 0.0,
        "detail_rows": detail_rows[:details_limit],
    }


def _vec2_from_row(row: dict, key: str) -> tuple[float, float]:
    value = _value_from_row(row, key, {})
    if not isinstance(value, dict):
        return (0.0, 0.0)
    return (float(value.get("x", 0.0)), float(value.get("y", 0.0)))


def _vec3_from_row(row: dict, key: str) -> dict:
    value = _value_from_row(row, key, {})
    if not isinstance(value, dict):
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "x": float(value.get("x", 0.0)),
        "y": float(value.get("y", 0.0)),
        "z": float(value.get("z", 0.0)),
    }


def _value_from_row(row: dict, key: str, default=None):
    if key in row:
        return row.get(key, default)
    frame_trace = row.get("frame_trace", {})
    if isinstance(frame_trace, dict):
        return frame_trace.get(key, default)
    return default


def _has_value_from_row(row: dict, key: str) -> bool:
    if key in row:
        return row.get(key) is not None
    frame_trace = row.get("frame_trace", {})
    return isinstance(frame_trace, dict) and key in frame_trace and frame_trace.get(key) is not None


def _float_from_row(row: dict, key: str, default: float = 0.0) -> float:
    value = _value_from_row(row, key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_from_row(row: dict, key: str, default: int = 0) -> int:
    value = _value_from_row(row, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def summarize_shimmer_metrics(frames: dict[int, list[dict]]) -> dict:
    frame_ids = sorted(frames.keys())
    frame_metrics: list[dict] = []
    previous_snap_px: tuple[float, float] | None = None
    previous_snap_delta: tuple[float, float] | None = None
    max_snap_px_delta = 0.0
    total_snap_px_delta = 0.0
    snap_delta_count = 0
    snap_sign_flip_count = 0
    snap_sign_flip_axis_samples = 0
    projection_modes = Counter()
    fov_values = set()
    near_values = set()
    far_values = set()
    projection_fields_present_frames = 0
    snap_world_values = set()
    snap_world_observed_frames = 0
    snap_world_nonzero_frames = 0
    product_time_values = set()
    projection_contract_invalid_frames = 0
    snap_contract_mismatch_frames = 0
    max_snap_compensation_error_px = 0.0
    style_shift_nonzero_frames = 0
    snap_residual_nonzero_frames = 0
    # FL-4359 moving-camera requirement: track real camera translation between
    # frames so a steady-pose capture is rejected (jitter/shimmer are only
    # meaningful under continuous sub-cell motion).
    camera_motion_eps = 1.0e-4
    previous_camera_world: dict | None = None
    camera_translation_total = 0.0
    camera_translation_max = 0.0
    camera_translation_pairs = 0
    moving_frame_pairs = 0

    for fid in frame_ids:
        rows = frames.get(fid, [])
        sample = rows[0] if rows else {}
        # FL-4359 defect-2 fix: detect motion on the UNSNAPPED presentation pose.
        # The snapped pose can hold still across a snap quantum while the camera is
        # genuinely translating, which would misclassify a moving run as steady.
        cam_world = _vec3_from_row(sample, "camera_world_unsnapped")
        if cam_world["x"] == 0.0 and cam_world["y"] == 0.0 and cam_world["z"] == 0.0:
            cam_world = _vec3_from_row(sample, "camera_world")  # legacy capture fallback
        if previous_camera_world is not None:
            tdx = cam_world["x"] - previous_camera_world["x"]
            tdy = cam_world["y"] - previous_camera_world["y"]
            tdz = cam_world["z"] - previous_camera_world["z"]
            translation = math.sqrt(tdx * tdx + tdy * tdy + tdz * tdz)
            camera_translation_total += translation
            camera_translation_max = max(camera_translation_max, translation)
            camera_translation_pairs += 1
            if translation > camera_motion_eps:
                moving_frame_pairs += 1
        previous_camera_world = cam_world
        snap_px = _vec2_from_row(sample, "camera_snap_error_px")
        snap_cells = _vec2_from_row(sample, "camera_snap_error_cells")
        style_shift_px = _vec2_from_row(sample, "style_post_shift_px")
        snap_px_delta = 0.0
        current_snap_delta: tuple[float, float] | None = None
        if previous_snap_px is not None:
            current_snap_delta = (
                snap_px[0] - previous_snap_px[0],
                snap_px[1] - previous_snap_px[1],
            )
            snap_px_delta = math.sqrt(
                current_snap_delta[0] ** 2
                + current_snap_delta[1] ** 2
            )
            max_snap_px_delta = max(max_snap_px_delta, snap_px_delta)
            total_snap_px_delta += snap_px_delta
            snap_delta_count += 1
            if previous_snap_delta is not None:
                for axis in range(2):
                    prev_axis = previous_snap_delta[axis]
                    cur_axis = current_snap_delta[axis]
                    if abs(prev_axis) > 1.0e-6 and abs(cur_axis) > 1.0e-6:
                        snap_sign_flip_axis_samples += 1
                        if (prev_axis < 0.0) != (cur_axis < 0.0):
                            snap_sign_flip_count += 1
            previous_snap_delta = current_snap_delta
        previous_snap_px = snap_px
        projection_mode_present = _has_value_from_row(sample, "camera_projection_mode")
        projection_mode = _int_from_row(sample, "camera_projection_mode", 0) if projection_mode_present else None
        camera_fov = _float_from_row(sample, "camera_fov", 0.0)
        camera_near = _float_from_row(sample, "camera_near", 0.0)
        camera_far = _float_from_row(sample, "camera_far", 0.0)
        snap_world_present = _has_value_from_row(sample, "pixel_camera_snap_world")
        snap_world = _float_from_row(sample, "pixel_camera_snap_world", 0.0)
        if projection_mode is not None:
            projection_modes.update([projection_mode])
        if camera_fov > 0.0:
            fov_values.add(round(camera_fov, 4))
        if camera_near > 0.0:
            near_values.add(round(camera_near, 6))
        if camera_far > 0.0:
            far_values.add(round(camera_far, 4))
        if snap_world_present:
            snap_world_observed_frames += 1
            snap_world_values.add(round(snap_world, 8))
            if abs(snap_world) > 1.0e-6:
                snap_world_nonzero_frames += 1
        product_time = _value_from_row(sample, "product_time_seconds", None)
        if product_time is not None:
            product_time_values.add(round(float(product_time), 8))
        if _has_value_from_row(sample, "projection_contract_valid") and not bool(_value_from_row(sample, "projection_contract_valid")):
            projection_contract_invalid_frames += 1
        if projection_mode_present and camera_fov > 0.0 and camera_near > 0.0 and camera_far > 0.0:
            projection_fields_present_frames += 1
        snap_compensation_error_px = max(
            abs(snap_px[0] - style_shift_px[0]),
            abs(snap_px[1] - style_shift_px[1]),
        )
        max_snap_compensation_error_px = max(
            max_snap_compensation_error_px,
            snap_compensation_error_px,
        )
        if snap_compensation_error_px > 1.0e-3:
            snap_contract_mismatch_frames += 1
        if max(abs(style_shift_px[0]), abs(style_shift_px[1])) > 1.0e-3:
            style_shift_nonzero_frames += 1
        if max(abs(snap_px[0]), abs(snap_px[1])) > 1.0e-3:
            snap_residual_nonzero_frames += 1
        frame_metrics.append({
            "frame_id": fid,
            "rows": len(rows),
            "camera_world": _vec3_from_row(sample, "camera_world"),
            "camera_world_unsnapped": _vec3_from_row(sample, "camera_world_unsnapped"),
            "camera_snap_error_cells": {"x": snap_cells[0], "y": snap_cells[1]},
            "camera_snap_error_px": {"x": snap_px[0], "y": snap_px[1]},
            "style_post_shift_px": {"x": style_shift_px[0], "y": style_shift_px[1]},
            "camera_projection_mode": projection_mode,
            "camera_fov": camera_fov,
            "camera_near": camera_near,
            "camera_far": camera_far,
            "pixel_camera_snap_world": snap_world,
            "snap_px_delta_from_previous": snap_px_delta,
            "snap_compensation_error_px": snap_compensation_error_px,
        })

    perspective_frames = sum(int(count) for mode, count in projection_modes.items() if int(mode) == 0)
    orthographic_frames = sum(int(count) for mode, count in projection_modes.items() if int(mode) == 1)
    mixed_projection = perspective_frames > 0 and orthographic_frames > 0
    perspective_temporal_measure_present = (
        perspective_frames > 0 and moving_frame_pairs > 0 and camera_translation_pairs > 0
    )
    snap_metric_evidence_present = (
        len(frame_ids) > 1
        and snap_world_observed_frames == len(frame_ids)
        and (
            snap_world_nonzero_frames == len(frame_ids)
            or perspective_temporal_measure_present
        )
    )
    return {
        "frame_metrics": frame_metrics,
        "max_snap_px_delta": max_snap_px_delta,
        "avg_snap_px_delta": (total_snap_px_delta / snap_delta_count) if snap_delta_count else 0.0,
        "snap_sign_flip_count": snap_sign_flip_count,
        "snap_sign_flip_axis_samples": snap_sign_flip_axis_samples,
        "snap_sign_flip_rate": (snap_sign_flip_count / snap_sign_flip_axis_samples) if snap_sign_flip_axis_samples else 0.0,
        "projection_fields_present_frames": projection_fields_present_frames,
        "camera_projection_modes": dict(sorted(projection_modes.items())),
        "camera_fov_values": sorted(fov_values),
        "camera_near_values": sorted(near_values),
        "camera_far_values": sorted(far_values),
        "pixel_camera_snap_world_values": sorted(snap_world_values),
        "pixel_camera_snap_world_observed_frames": snap_world_observed_frames,
        "pixel_camera_snap_world_nonzero_frames": snap_world_nonzero_frames,
        "snap_metric_evidence_present": snap_metric_evidence_present,
        "perspective_temporal_measure_present": perspective_temporal_measure_present,
        "orthographic_frames": orthographic_frames,
        "perspective_frames": perspective_frames,
        "mixed_projection": mixed_projection,
        "product_time_values": sorted(product_time_values),
        "product_time_constant": len(product_time_values) <= 1,
        "projection_contract_invalid_frames": projection_contract_invalid_frames,
        "snap_contract_mismatch_frames": snap_contract_mismatch_frames,
        "max_snap_compensation_error_px": max_snap_compensation_error_px,
        "style_post_shift_nonzero_frames": style_shift_nonzero_frames,
        "camera_snap_residual_nonzero_frames": snap_residual_nonzero_frames,
        "camera_translation_total": camera_translation_total,
        "camera_translation_max_per_frame": camera_translation_max,
        "camera_translation_pairs": camera_translation_pairs,
        "moving_frame_pairs": moving_frame_pairs,
        "camera_motion_continuous_ratio": (
            moving_frame_pairs / camera_translation_pairs
        ) if camera_translation_pairs else 0.0,
    }


def _has_row_value(row: dict, key: str) -> bool:
    if key in row and row.get(key) is not None:
        return True
    frame_trace = row.get("frame_trace")
    return isinstance(frame_trace, dict) and key in frame_trace and frame_trace.get(key) is not None


def summarize_presented_surface_stability(rows: list[dict]) -> dict:
    """FL-4359: observe v16's actual b51 paint without relabeling VSB truth.

    Exact captured world position plus source identity is the comparison key.
    Camera-dependent fog can change colours legitimately, so this diagnostic
    reports magnitudes and never grants acceptance or replaces existing gates.
    """
    frames = {}
    invalid = terminal_mismatch = valid = 0
    seen_cells = set()
    for row in rows:
        presented = row.get("presented_surface")
        if not isinstance(presented, dict) or not presented:
            continue
        frame = row.get("frame_id", row.get("frame_index"))
        screen = row.get("screen_cell", {})
        cell = (frame, screen.get("x"), screen.get("y"))
        if cell in seen_cells:
            continue
        seen_cells.add(cell)
        state = presented.get("state", [])
        head = presented.get("head", [])
        identity = presented.get("identity", [])
        world = presented.get("world", [])
        fg, bg = presented.get("fg", []), presented.get("bg", [])
        if (any(len(value) != 4 for value in (state, head, identity, world, fg, bg))
                or state[:2] != [1, 1]
                or not all(isinstance(v, (int, float)) and math.isfinite(v)
                           for value in (world, fg, bg) for v in value[:3])):
            invalid += 1
            continue
        valid += 1
        painted_fg = row.get("final_paint_slot_fg_rgb")
        painted_bg = row.get("final_paint_slot_bg_rgb")
        joined = row.get("final_paint_glyph") == head[3]
        for observed, expected in ((painted_fg, fg), (painted_bg, bg)):
            joined = joined and isinstance(observed, list) and len(observed) >= 3
            if joined:
                # b43's terminal witness is byte RGB; b51 is unit RGB.
                joined = max(abs(observed[i] / 255.0 - expected[i]) for i in range(3)) <= 1.0 / 255.0 + 1e-5
        if not joined:
            terminal_mismatch += 1
            continue
        key = (tuple(identity), tuple(world[:3]))
        frames.setdefault(frame, {}).setdefault(key, []).append((head[3], tuple(fg[:3]), tuple(bg[:3])))
    comparisons = glyph_changes = colour_changes = ambiguous = 0
    max_fg_delta = max_bg_delta = 0.0
    pairs = []
    ids = sorted(frames)
    for before, after in zip(ids, ids[1:]):
        matched = 0
        for key in frames[before].keys() & frames[after].keys():
            a, b = set(frames[before][key]), set(frames[after][key])
            if len(a) != 1 or len(b) != 1:
                ambiguous += 1
                continue
            a, b = next(iter(a)), next(iter(b))
            matched += 1
            comparisons += 1
            glyph_changes += a[0] != b[0]
            fg_delta = max(abs(x - y) for x, y in zip(a[1], b[1]))
            bg_delta = max(abs(x - y) for x, y in zip(a[2], b[2]))
            max_fg_delta = max(max_fg_delta, fg_delta)
            max_bg_delta = max(max_bg_delta, bg_delta)
            colour_changes += max(fg_delta, bg_delta) > 1.0 / 255.0 + 1e-5
        pairs.append({"before": before, "after": after, "matched_sources": matched})
    return {
        "scope": "actual presented b51 paint; exact source/world join; diagnostic only",
        "valid_presented_cells": valid, "invalid_or_unanchored_cells": invalid,
        "terminal_paint_mismatch_cells": terminal_mismatch,
        "ambiguous_source_pairs": ambiguous, "comparable_source_pairs": comparisons,
        "glyph_changes": glyph_changes, "colour_changes": colour_changes,
        "colour_change_pct": 100.0 * colour_changes / comparisons if comparisons else None,
        "max_fg_delta": max_fg_delta if comparisons else None,
        "max_bg_delta": max_bg_delta if comparisons else None,
        "pairs": pairs, "acceptance_claimed": False,
    }


def summarize_required_field_coverage(rows: list[dict], summary: dict) -> dict:
    frame_count = int(summary.get("frame_count", 0))
    row_count = len(rows)
    pair_count = int(summary.get("pair_count", 0))
    comparable_pair_count = int(summary.get("comparable_pair_count", 0))
    moving_pairs = int(summary.get("moving_frame_pairs", 0))
    frame_metric_count = len(summary.get("frame_metrics", []))
    snap_delta_count = len([
        frame
        for frame in summary.get("frame_metrics", [])
        if float(frame.get("snap_px_delta_from_previous", 0.0)) != 0.0
    ])
    terrain_source_swap_count = 0
    perspective_content_shimmer_count = 0
    for result in summary.get("pair_results", []):
        classifications = result.get("classifications", {})
        terrain_source_swap_count += int(classifications.get("terrain_sample_key_drift", 0))
        perspective_content_shimmer_count += int(result.get("shimmer_cells", 0))

    # Dense full-grid captures carry hundreds of thousands of rows. Count every
    # required raw field in one row traversal. The retired implementation made
    # one complete Python scan per field and stalled after all other analysis
    # had already finished.
    required_names = set(FL4359_REQUIRED_FIELDS)
    raw_field_counts: Counter = Counter()
    water_splat_hidden_as_sky_count = 0
    water_splat_hidden_as_sky_present = False
    for row in rows:
        present_names = {
            key for key, value in row.items() if value is not None
        }
        frame_trace = row.get("frame_trace")
        if isinstance(frame_trace, dict):
            present_names.update(
                key for key, value in frame_trace.items() if value is not None
            )
        raw_field_counts.update(required_names.intersection(present_names))
        if "water_splat_winner_hidden_as_sky" in row:
            water_splat_hidden_as_sky_present = True
        if bool(row.get("water_splat_winner_hidden_as_sky", False)):
            water_splat_hidden_as_sky_count += 1

    final_product_source_key_count = int(
        raw_field_counts["final_product_source_key"]
    )
    mountain_material_rows = int(summary.get("mountain_material_rows", 0))
    product_anchor_provenance = summarize_product_anchor_provenance(rows)
    derived = {
        "perspective_jitter_rows": {
            "present": row_count > 0 and frame_count > 1 and moving_pairs > 0,
            "count": row_count,
        },
        "perspective_content_shimmer_rows": {
            "present": comparable_pair_count > 0,
            "count": perspective_content_shimmer_count,
        },
        "camera_delta": {
            "present": frame_metric_count > 1,
            "count": moving_pairs,
        },
        "snap_error_delta": {
            "present": frame_metric_count > 1,
            "count": snap_delta_count,
        },
        "terrain_source_swap_rate": {
            "present": pair_count > 0,
            "count": terrain_source_swap_count,
        },
        "final_product_source_key": {
            "present": final_product_source_key_count > 0,
            "count": final_product_source_key_count,
        },
        "water_splat_winner_hidden_as_sky": {
            "present": water_splat_hidden_as_sky_present,
            "count": water_splat_hidden_as_sky_count,
        },
        "mountain_material_family_distribution": {
            "present": mountain_material_rows > 0,
            "count": mountain_material_rows,
        },
        "final_cell_signature_churn_pct": {
            "present": pair_count > 0,
            "count": sum(
                int(result.get("motion_compensated_final_cell_instability_cells", 0))
                for result in summary.get("pair_results", [])
            ),
        },
        "motion_compensated_final_cell_instability_pct": {
            "present": pair_count > 0,
            "count": sum(
                int(result.get("motion_compensated_final_cell_instability_cells", 0))
                for result in summary.get("pair_results", [])
            ),
        },
        "screen_position_residual_world_max": {
            "present": pair_count > 0,
            "count": pair_count,
        },
        "fixed_screen_temporal_rows": {
            "present": bool(summary.get("fixed_screen_temporal_evidence_present", False)),
            "count": int(summary.get("fixed_screen_temporal_comparable_cells", 0)),
        },
    }

    fields = {}
    for name in FL4359_REQUIRED_FIELDS:
        raw_rows = int(raw_field_counts[name])
        derived_entry = derived.get(name, {"present": False, "count": 0})
        derived_count = int(derived_entry.get("count", 0))
        present = raw_rows > 0 or bool(derived_entry.get("present", False))
        fields[name] = {
            "present": present,
            "raw_rows": raw_rows,
            "derived_count": derived_count,
        }
    missing = [name for name, data in fields.items() if not data["present"]]
    if not product_anchor_provenance["complete"]:
        missing.append("current_product_anchor_provenance")
    lifecycle_facts = summarize_lifecycle_facts(rows)
    if lifecycle_facts["required"] and not lifecycle_facts["all_valid"]:
        missing.append("lifecycle_facts")
    return {
        "all_present": not missing,
        "missing": missing,
        "present": [name for name, data in fields.items() if data["present"]],
        "fields": fields,
        "product_anchor_provenance": product_anchor_provenance,
        "lifecycle_facts": lifecycle_facts,
    }


def summarize_foliage_metrics(frames: dict[int, list[dict]]) -> dict:
    role_counts: Counter = Counter()
    selected_owner_counts: Counter = Counter()
    frames_with_foliage_fields = 0
    frames_with_flower_rows = 0
    foliage_rows = 0
    flower_rows = 0
    flower_rows_with_glyph = 0
    flower_rows_with_final_ink = 0
    flower_rows_with_glyph_and_final_ink = 0
    for _fid, rows in frames.items():
        frame_has_fields = any("foliage_product_present" in row for row in rows)
        frame_has_flower = False
        if frame_has_fields:
            frames_with_foliage_fields += 1
        for row in rows:
            if not row.get("foliage_product_present"):
                continue
            foliage_rows += 1
            role = row.get("foliage_role")
            role_counts.update([str(role)])
            selected_owner_counts.update([str(row.get("selected_owner"))])
            if role == 4:
                frame_has_flower = True
                flower_rows += 1
                has_glyph = int(row.get("foliage_glyph") or 0) > 0
                has_ink = int(row.get("final_ink_pixels") or 0) > 0
                if has_glyph:
                    flower_rows_with_glyph += 1
                if has_ink:
                    flower_rows_with_final_ink += 1
                if has_glyph and has_ink:
                    flower_rows_with_glyph_and_final_ink += 1
        if frame_has_flower:
            frames_with_flower_rows += 1
    frame_count = len(frames)
    return {
        "foliage_fields_present_frames": frames_with_foliage_fields,
        "foliage_fields_present_all_frames": frame_count > 0 and frames_with_foliage_fields == frame_count,
        "foliage_product_rows": foliage_rows,
        "foliage_role_counts": dict(sorted(role_counts.items())),
        "foliage_selected_owner_counts": dict(sorted(selected_owner_counts.items())),
        "flower_frames": frames_with_flower_rows,
        "flower_product_rows": flower_rows,
        "flower_rows_with_glyph": flower_rows_with_glyph,
        "flower_rows_with_final_ink": flower_rows_with_final_ink,
        "flower_rows_with_glyph_and_final_ink": flower_rows_with_glyph_and_final_ink,
    }


def summarize_mountain_material_metrics(rows: list[dict]) -> dict:
    counts: Counter = Counter()
    material_id_counts: Counter = Counter()
    examples: list[dict] = []
    invalid_rows = 0
    for row in rows:
        if not _is_mountain_geometry_row(row):
            continue
        role = _product_mountain_material_role(row)
        material_id = row.get("final_product_material_id")
        if (
            role is None
            or not isinstance(material_id, int)
            or isinstance(material_id, bool)
            or material_id < 0
        ):
            invalid_rows += 1
            continue
        counts.update([role])
        material_id_counts.update([str(material_id)])
        if len(examples) < 20:
            detail = unpack_terrain_detail(row.get("terrain_product_detail"))
            examples.append({
                "frame_id": row.get("frame_id"),
                "screen_cell": row.get("screen_cell"),
                "terrain_cell": row.get("terrain_cell"),
                "source_key": row.get("source_key"),
                "material_role": role,
                "final_product_material_family": row.get("final_product_material_family"),
                "final_product_material_id": material_id,
                "glyph_id": row.get("glyph_id"),
                "cliff": detail.get("cliff", 0),
                "ridge": detail.get("ridge", 0),
                "sidewall_score_class": detail.get("sidewall_score_class", 0),
                "clone_peak_glyph_lane": detail.get("clone_peak_glyph_lane", 0),
            })
    total = sum(counts.values())
    dirt_count = int(counts.get("dirt", 0))
    cliff_count = int(counts.get("cliff_rock", 0))
    dirt_ratio = (float(dirt_count) / float(total)) if total else 0.0
    return {
        "mountain_material_rows": total,
        "mountain_material_invalid_rows": invalid_rows,
        "mountain_material_source": "final_product_material_family",
        "mountain_material_role_counts": dict(sorted(counts.items())),
        "mountain_material_id_counts": dict(sorted(material_id_counts.items())),
        "mountain_dirt_rows": dirt_count,
        "mountain_cliff_rock_rows": cliff_count,
        "mountain_dirt_ratio": dirt_ratio,
        "mountain_material_examples": examples,
    }


def summarize_terrain_product_homogeneity_metrics(rows: list[dict]) -> dict:
    """Measure final Product terrain diversity without inventing a good band.

    FL-4775 originally reported material, glyph, colour-count, and chroma
    concentration. That observer could not see the user-reported failure where
    many different glyphs shared nearly one grey value. Rec.709 foreground and
    background value spread plus foreground/background ink contrast are kept
    here as separate observations. A same-pose calibrated target still owns any
    acceptance threshold.
    """
    family_counts: Counter = Counter()
    material_counts: Counter = Counter()
    glyph_counts: Counter = Counter()
    quarter_glyph_counts: Counter = Counter()
    fg_counts: Counter = Counter()
    bg_counts: Counter = Counter()
    final_paint_glyph_counts: Counter = Counter()
    product_final_paint_glyph_transitions: Counter = Counter()
    product_final_paint_fg_transitions: Counter = Counter()
    product_final_paint_bg_transitions: Counter = Counter()
    product_final_paint_first_difference: Counter = Counter()
    chroma_values: list[int] = []
    fg_luma_values: list[float] = []
    bg_luma_values: list[float] = []
    ink_contrast_values: list[float] = []
    dirt_mountain_fg_luma_values: list[float] = []
    dirt_mountain_ink_contrast_values: list[float] = []
    material4_dirt_mountain_fg_luma_values: list[float] = []
    material4_dirt_mountain_ink_contrast_values: list[float] = []
    invalid_rows = 0
    product_final_paint_glyph_join_rows = 0
    product_final_paint_colour_join_rows = 0
    product_final_paint_actor_overlay_rows = 0
    product_final_paint_missing_slot_rows = 0
    product_final_paint_missing_colour_rows = 0
    product_final_paint_glyph_equal_rows = 0
    product_final_paint_fg_equal_rows = 0
    product_final_paint_bg_equal_rows = 0
    product_final_paint_signature_equal_rows = 0

    def rec709_luma(value: object) -> float | None:
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        try:
            channels = [
                max(0.0, min(255.0, float(value[index]))) / 255.0
                for index in range(3)
            ]
        except (TypeError, ValueError):
            return None
        return (
            0.2126 * channels[0]
            + 0.7152 * channels[1]
            + 0.0722 * channels[2]
        )

    for row in rows:
        if row.get("final_product_owner_name") != "terrain":
            continue
        family = row.get("final_product_material_family")
        material_id = row.get("final_product_material_id")
        quarter_glyph = row.get("final_product_glyph")
        cell_mask_winner = cell_mask_winner_witness(row)
        glyph = cell_mask_winner.get("glyph")
        fg = quantized_rgb(row.get("final_product_fg_rgb"))
        bg = quantized_rgb(row.get("final_product_bg_rgb"))
        fg_luma = rec709_luma(row.get("final_product_fg_rgb"))
        bg_luma = rec709_luma(row.get("final_product_bg_rgb"))
        final_paint_glyph = row.get("final_paint_slot_glyph")
        if (
            not isinstance(family, int)
            or isinstance(family, bool)
            or not isinstance(material_id, int)
            or isinstance(material_id, bool)
            or material_id < 0
            or not isinstance(quarter_glyph, int)
            or isinstance(quarter_glyph, bool)
            or not cell_mask_winner.get("available", False)
            or not isinstance(glyph, int)
            or isinstance(glyph, bool)
            or len(fg) != 3
            or len(bg) != 3
            or fg_luma is None
            or bg_luma is None
        ):
            invalid_rows += 1
            continue
        family_counts[str(family)] += 1
        material_counts[str(material_id)] += 1
        glyph_counts[str(glyph)] += 1
        quarter_glyph_counts[str(quarter_glyph)] += 1
        fg_counts[",".join(str(channel) for channel in fg)] += 1
        bg_counts[",".join(str(channel) for channel in bg)] += 1
        chroma_values.append(max(fg) - min(fg))
        ink_contrast = abs(fg_luma - bg_luma)
        fg_luma_values.append(fg_luma)
        bg_luma_values.append(bg_luma)
        ink_contrast_values.append(ink_contrast)
        dirt_mountain = row.get("final_product_dirt_mountain_role") is True
        if dirt_mountain:
            dirt_mountain_fg_luma_values.append(fg_luma)
            dirt_mountain_ink_contrast_values.append(ink_contrast)
            if material_id == 4:
                material4_dirt_mountain_fg_luma_values.append(fg_luma)
                material4_dirt_mountain_ink_contrast_values.append(ink_contrast)
        if row.get("final_paint_slot_actor_overlay") is True:
            product_final_paint_actor_overlay_rows += 1
            continue
        if row.get("final_paint_slot_present") is not True:
            product_final_paint_missing_slot_rows += 1
            continue
        if not isinstance(final_paint_glyph, int) or isinstance(
            final_paint_glyph, bool
        ):
            product_final_paint_missing_slot_rows += 1
            continue
        product_final_paint_glyph_join_rows += 1
        product_glyph_key = str(glyph)
        final_paint_glyph_key = str(final_paint_glyph)
        final_paint_glyph_counts[final_paint_glyph_key] += 1
        product_final_paint_glyph_transitions[
            f"{product_glyph_key}->{final_paint_glyph_key}"
        ] += 1
        glyph_equal = glyph == final_paint_glyph
        product_final_paint_glyph_equal_rows += int(glyph_equal)
        final_paint_fg = quantized_rgb(row.get("final_paint_slot_fg_rgb"))
        final_paint_bg = quantized_rgb(row.get("final_paint_slot_bg_rgb"))
        if (
            row.get("final_paint_slot_colour_present") is not True
            or len(final_paint_fg) != 3
            or len(final_paint_bg) != 3
        ):
            product_final_paint_missing_colour_rows += 1
            product_final_paint_first_difference[
                "glyph_id" if not glyph_equal else "colour_witness_missing"
            ] += 1
            continue
        product_final_paint_colour_join_rows += 1
        product_fg_key = ",".join(str(channel) for channel in fg)
        final_paint_fg_key = ",".join(str(channel) for channel in final_paint_fg)
        product_bg_key = ",".join(str(channel) for channel in bg)
        final_paint_bg_key = ",".join(str(channel) for channel in final_paint_bg)
        product_final_paint_fg_transitions[
            f"{product_fg_key}->{final_paint_fg_key}"
        ] += 1
        product_final_paint_bg_transitions[
            f"{product_bg_key}->{final_paint_bg_key}"
        ] += 1
        fg_equal = fg == final_paint_fg
        bg_equal = bg == final_paint_bg
        product_final_paint_fg_equal_rows += int(fg_equal)
        product_final_paint_bg_equal_rows += int(bg_equal)
        signature_equal = glyph_equal and fg_equal and bg_equal
        product_final_paint_signature_equal_rows += int(signature_equal)
        if not glyph_equal:
            product_final_paint_first_difference["glyph_id"] += 1
        elif not fg_equal:
            product_final_paint_first_difference["foreground_rgb"] += 1
        elif not bg_equal:
            product_final_paint_first_difference["background_rgb"] += 1
        else:
            product_final_paint_first_difference["none"] += 1

    total = sum(material_counts.values())
    product_final_paint_eligible_rows = (
        total - product_final_paint_actor_overlay_rows
    )

    def concentration(counter: Counter) -> tuple[str | None, int, float]:
        if not counter or total <= 0:
            return None, 0, 0.0
        key, count = counter.most_common(1)[0]
        return str(key), int(count), float(count) / float(total)

    dominant_material, dominant_material_rows, dominant_material_ratio = concentration(
        material_counts
    )
    dominant_glyph, dominant_glyph_rows, dominant_glyph_ratio = concentration(
        glyph_counts
    )
    top_two_glyph_rows = sum(count for _key, count in glyph_counts.most_common(2))
    sorted_chroma = sorted(chroma_values)
    median_chroma = (
        float(sorted_chroma[len(sorted_chroma) // 2]) if sorted_chroma else 0.0
    )

    def distribution(prefix: str, values: list[float]) -> dict[str, float | int]:
        ordered = sorted(values)

        def percentile(fraction: float) -> float:
            if not ordered:
                return 0.0
            position = int(round((len(ordered) - 1) * fraction))
            return float(ordered[position])

        p10 = percentile(0.10)
        p50 = percentile(0.50)
        p90 = percentile(0.90)
        return {
            f"{prefix}_rows": len(ordered),
            f"{prefix}_p10": p10,
            f"{prefix}_p50": p50,
            f"{prefix}_p90": p90,
            f"{prefix}_p90_p10_spread": p90 - p10,
        }

    value_distribution = {
        **distribution("terrain_product_fg_luma", fg_luma_values),
        **distribution("terrain_product_bg_luma", bg_luma_values),
        **distribution("terrain_product_ink_contrast", ink_contrast_values),
        **distribution(
            "terrain_product_dirt_mountain_fg_luma",
            dirt_mountain_fg_luma_values,
        ),
        **distribution(
            "terrain_product_dirt_mountain_ink_contrast",
            dirt_mountain_ink_contrast_values,
        ),
        **distribution(
            "terrain_product_material4_dirt_mountain_fg_luma",
            material4_dirt_mountain_fg_luma_values,
        ),
        **distribution(
            "terrain_product_material4_dirt_mountain_ink_contrast",
            material4_dirt_mountain_ink_contrast_values,
        ),
    }
    return {
        "terrain_product_homogeneity_source": "final_product_b55",
        "terrain_product_homogeneity_rows": total,
        "terrain_product_homogeneity_invalid_rows": invalid_rows,
        "terrain_product_material_family_counts": dict(sorted(family_counts.items())),
        "terrain_product_material_id_counts": dict(sorted(material_counts.items())),
        # Product's status/sidecar cell-mask glyph is the visible semantic glyph.
        # r0.z is retained separately because it names only this quarter's
        # pre-election provenance and cannot grade one-glyph-per-cell output.
        "terrain_product_glyph_source": "cell_mask_winner_glyph",
        "terrain_product_glyph_counts": dict(glyph_counts.most_common()),
        "terrain_product_quarter_glyph_counts": dict(
            quarter_glyph_counts.most_common()
        ),
        "terrain_product_fg_rgb_quant_counts": dict(fg_counts.most_common()),
        "terrain_product_bg_rgb_quant_counts": dict(bg_counts.most_common()),
        "terrain_final_paint_slot_glyph_counts": dict(
            final_paint_glyph_counts.most_common()
        ),
        "terrain_product_distinct_material_ids": len(material_counts),
        "terrain_product_distinct_glyphs": len(glyph_counts),
        "terrain_product_distinct_fg_rgb_quant": len(fg_counts),
        "terrain_product_distinct_bg_rgb_quant": len(bg_counts),
        "terrain_final_paint_slot_distinct_glyphs": len(final_paint_glyph_counts),
        "terrain_product_final_paint_join_scope": (
            "same-row elected b55 Product versus b43 per-slot glyph and "
            "canonical native-paint FG/BG; actor-overlay, absent-slot, and "
            "legacy cell-header-only colour rows are classified separately"
        ),
        "terrain_product_final_paint_glyph_join_rows": (
            product_final_paint_glyph_join_rows
        ),
        "terrain_product_final_paint_colour_join_rows": (
            product_final_paint_colour_join_rows
        ),
        "terrain_product_final_paint_actor_overlay_rows": (
            product_final_paint_actor_overlay_rows
        ),
        # Actor-covered slots have a different visible owner and therefore are
        # not Product-to-terrain-paint comparisons.  Keep them accounted for,
        # then require every remaining terrain row to carry both the native
        # glyph slot and its per-slot FG/BG witness.
        "terrain_product_final_paint_eligible_rows": (
            product_final_paint_eligible_rows
        ),
        "terrain_product_final_paint_missing_slot_rows": (
            product_final_paint_missing_slot_rows
        ),
        "terrain_product_final_paint_missing_colour_rows": (
            product_final_paint_missing_colour_rows
        ),
        "terrain_product_final_paint_glyph_equal_rows": (
            product_final_paint_glyph_equal_rows
        ),
        "terrain_product_final_paint_fg_equal_rows": (
            product_final_paint_fg_equal_rows
        ),
        "terrain_product_final_paint_bg_equal_rows": (
            product_final_paint_bg_equal_rows
        ),
        "terrain_product_final_paint_signature_equal_rows": (
            product_final_paint_signature_equal_rows
        ),
        "terrain_product_final_paint_first_difference_counts": dict(
            product_final_paint_first_difference.most_common()
        ),
        "terrain_product_final_paint_glyph_transition_counts": dict(
            product_final_paint_glyph_transitions.most_common(64)
        ),
        "terrain_product_final_paint_fg_transition_counts": dict(
            product_final_paint_fg_transitions.most_common(64)
        ),
        "terrain_product_final_paint_bg_transition_counts": dict(
            product_final_paint_bg_transitions.most_common(64)
        ),
        "terrain_product_dominant_material_id": dominant_material,
        "terrain_product_dominant_material_rows": dominant_material_rows,
        "terrain_product_dominant_material_ratio": dominant_material_ratio,
        "terrain_product_dominant_glyph_id": dominant_glyph,
        "terrain_product_dominant_glyph_rows": dominant_glyph_rows,
        "terrain_product_dominant_glyph_ratio": dominant_glyph_ratio,
        "terrain_product_top_two_glyph_rows": top_two_glyph_rows,
        "terrain_product_top_two_glyph_ratio": (
            float(top_two_glyph_rows) / float(total) if total else 0.0
        ),
        "terrain_product_fg_quant_chroma_median": median_chroma,
        "terrain_product_value_observer": (
            "Rec.709 final Product FG/BG value and absolute glyph-ink contrast; "
            "reported for all terrain, dirt-mountain, and material4 dirt-mountain"
        ),
        **value_distribution,
        "terrain_product_homogeneity_baseline_calibrated": False,
        "terrain_product_homogeneity_baseline_reason": (
            "same_pose_6fc_product_family_glyph_colour_fixture_missing"
        ),
    }


def summarize_terrain_product_compiler_distribution(rows: list[dict]) -> dict:
    """Report exact stage diversity without inventing a quality threshold.

    The witness rows have different semantics, so a cardinality drop is a
    localization fact, not proof that the later stage is wrong.  Same-pose
    baseline evidence remains responsible for deciding whether a measured
    compression is acceptable.
    """

    stage_order = [
        *PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES,
        "product_pre_finalization_signature",
        "product_post_finalization_signature",
        "b55_product_signature",
        "b43_native_paint_signature",
    ]

    def signature(glyph: object, fg_value: object, bg_value: object) -> tuple | None:
        if not isinstance(glyph, int) or isinstance(glyph, bool):
            return None
        fg = quantized_rgb(fg_value)
        bg = quantized_rgb(bg_value)
        if len(fg) != 3 or len(bg) != 3:
            return None
        return (int(glyph), *fg, *bg)

    def counter_stats(counter: Counter, valid_rows: int) -> dict:
        dominant_rows = counter.most_common(1)[0][1] if counter else 0
        entropy_bits = 0.0
        if valid_rows > 0:
            for count in counter.values():
                probability = float(count) / float(valid_rows)
                entropy_bits -= probability * math.log2(probability)
        return {
            "valid_rows": valid_rows,
            "distinct_exact_tuples": len(counter),
            "dominant_tuple_rows": int(dominant_rows),
            "dominant_tuple_ratio": (
                float(dominant_rows) / float(valid_rows) if valid_rows else 0.0
            ),
            "shannon_entropy_bits": entropy_bits,
        }

    def new_scope_accumulator() -> dict:
        return {
            "input_rows": 0,
            "stage_counters": {stage: Counter() for stage in stage_order},
            "stage_valid_rows": Counter(),
            # Store each successor once with its exact predecessor set. The old
            # pair Counter duplicated the same high-cardinality tuples here and
            # again during reduction, making dense full-grid diagnostics stall.
            "successor_predecessors": {
                f"{left}->{right}": {}
                for left, right in zip(stage_order, stage_order[1:])
            },
            "successor_rows": {
                f"{left}->{right}": Counter()
                for left, right in zip(stage_order, stage_order[1:])
            },
            "transition_valid_rows": Counter(),
            "invalid_witness_rows": 0,
            "invalid_finalization_rows": 0,
            "invalid_b55_rows": 0,
            "unavailable_b43_rows": 0,
        }

    def row_stage_values(row: dict) -> tuple[dict[str, tuple] | None, dict[str, int]]:
        invalid = {
            "invalid_witness_rows": 0,
            "invalid_finalization_rows": 0,
            "invalid_b55_rows": 0,
            "unavailable_b43_rows": 0,
        }
        witness = _terrain_colour_witness_bits(row)
        if witness is None:
            invalid["invalid_witness_rows"] = 1
            return None, invalid
        values: dict[str, tuple] = {
            stage: witness[stage]
            for stage in PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES
        }
        pre_finalization = _product_finalization_signature_bits(
            row, "final_product_pre_finalization_signature_bits"
        )
        post_finalization = _product_finalization_signature_bits(
            row, "final_product_post_finalization_signature_bits"
        )
        if pre_finalization is None or post_finalization is None:
            invalid["invalid_finalization_rows"] = 1
        else:
            values["product_pre_finalization_signature"] = pre_finalization
            values["product_post_finalization_signature"] = post_finalization
        product_winner = cell_mask_winner_witness(row)
        product_signature = signature(
            product_winner.get("glyph"),
            row.get("final_product_fg_rgb"),
            row.get("final_product_bg_rgb"),
        )
        if product_signature is None:
            invalid["invalid_b55_rows"] = 1
        else:
            values["b55_product_signature"] = product_signature
        paint_signature = None
        if (
            row.get("final_paint_slot_actor_overlay") is not True
            and row.get("final_paint_slot_present") is True
            and row.get("final_paint_slot_colour_present") is True
        ):
            paint_signature = signature(
                row.get("final_paint_slot_glyph"),
                row.get("final_paint_slot_fg_rgb"),
                row.get("final_paint_slot_bg_rgb"),
            )
        if paint_signature is None:
            invalid["unavailable_b43_rows"] = 1
        else:
            values["b43_native_paint_signature"] = paint_signature
        return values, invalid

    def accumulate_scope(
        accumulator: dict,
        values: dict[str, tuple] | None,
        invalid: dict[str, int],
    ) -> None:
        accumulator["input_rows"] += 1
        for key, count in invalid.items():
            accumulator[key] += count
        if values is None:
            return
        for stage, value in values.items():
            accumulator["stage_counters"][stage][value] += 1
            accumulator["stage_valid_rows"][stage] += 1
        for left, right in zip(stage_order, stage_order[1:]):
            if left not in values or right not in values:
                continue
            key = f"{left}->{right}"
            predecessor = values[left]
            successor = values[right]
            accumulator["successor_predecessors"][key].setdefault(
                successor, set()
            ).add(predecessor)
            accumulator["successor_rows"][key][successor] += 1
            accumulator["transition_valid_rows"][key] += 1

    def finalize_scope(accumulator: dict) -> dict:
        stage_stats = {
            stage: counter_stats(
                accumulator["stage_counters"][stage],
                int(accumulator["stage_valid_rows"][stage]),
            )
            for stage in stage_order
        }
        transition_stats = {}
        for left, right in zip(stage_order, stage_order[1:]):
            key = f"{left}->{right}"
            successor_predecessors = accumulator["successor_predecessors"][key]
            successor_rows = accumulator["successor_rows"][key]
            many_to_one_successors = {
                successor
                for successor, predecessors in successor_predecessors.items()
                if len(predecessors) > 1
            }
            predecessor_distinct = stage_stats[left]["distinct_exact_tuples"]
            successor_distinct = stage_stats[right]["distinct_exact_tuples"]
            transition_stats[key] = {
                "valid_rows": int(accumulator["transition_valid_rows"][key]),
                "distinct_transition_pairs": sum(
                    len(predecessors)
                    for predecessors in successor_predecessors.values()
                ),
                "predecessor_distinct_exact_tuples": predecessor_distinct,
                "successor_distinct_exact_tuples": successor_distinct,
                "successor_to_predecessor_cardinality_ratio": (
                    float(successor_distinct) / float(predecessor_distinct)
                    if predecessor_distinct
                    else 0.0
                ),
                "many_to_one_successor_tuples": len(many_to_one_successors),
                "many_to_one_rows": int(
                    sum(successor_rows[item] for item in many_to_one_successors)
                ),
                "max_predecessors_per_successor": max(
                    (len(predecessors) for predecessors in successor_predecessors.values()),
                    default=0,
                ),
            }
        return {
            "input_rows": int(accumulator["input_rows"]),
            "invalid_witness_rows": int(accumulator["invalid_witness_rows"]),
            "invalid_finalization_rows": int(accumulator["invalid_finalization_rows"]),
            "invalid_b55_rows": int(accumulator["invalid_b55_rows"]),
            "unavailable_b43_rows": int(accumulator["unavailable_b43_rows"]),
            "stages": stage_stats,
            "adjacent_transitions": transition_stats,
        }

    all_terrain = new_scope_accumulator()
    material_4 = new_scope_accumulator()
    for row in rows:
        if int(row.get("final_product_owner", 0)) != 1:
            continue
        values, invalid = row_stage_values(row)
        accumulate_scope(all_terrain, values, invalid)
        if row.get("final_product_material_id") == 4:
            accumulate_scope(material_4, values, invalid)
    return {
        "terrain_product_compiler_distribution_source": (
            "capture-only ordered raw-bit witnesses r14-r28, quantized Product "
            "pre/post-finalization signatures r29-r30, terminal b55, and exact "
            "per-slot b43 native-paint inputs"
        ),
        "terrain_product_compiler_distribution_claim_scope": (
            "localization only; cardinality and entropy are not a visual "
            "quality band without same-pose baseline calibration"
        ),
        "terrain_product_compiler_distribution_stage_order": stage_order,
        "terrain_product_compiler_distribution_scopes": {
            "all_terrain": finalize_scope(all_terrain),
            "material_4": finalize_scope(material_4),
        },
    }


def _product_admission_witness_error(row: dict) -> tuple[str, str] | None:
    """Validate the exact authored->Product admission witness.

    This function intentionally rejects absent, malformed, sentinel, and
    contradictory fields. It never substitutes b55 values for missing capture
    lanes because that would erase the first-collapse boundary.
    """
    missing = [
        field for field in PRODUCT_ADMISSION_REQUIRED_FIELDS if field not in row
    ]
    if missing:
        return "missing", ",".join(missing)

    def is_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    source_key = row.get("final_product_authored_source_key")
    receiver = row.get("final_product_authored_receiver_cell")
    material = row.get("final_product_authored_material_id")
    role_family = row.get("final_product_authored_role_family")
    role_valid = row.get("final_product_authored_role_family_valid")
    family = row.get("final_product_classifier_family")
    dirt_role = row.get("final_product_dirt_mountain_role")
    elevation = row.get("final_product_elevation_class")
    column = row.get("final_product_matlib_column_id")
    profile = row.get("final_product_matlib_profile")
    profile_valid = row.get("final_product_matlib_profile_valid")
    profile_validity = row.get("final_product_matlib_profile_validity_bits")
    validity = row.get("final_product_admission_validity_bits")
    transport_valid = row.get("final_product_admission_transport_valid")

    if not is_int(source_key) or source_key < 0:
        return "invalid", "authored_source_key"
    if (
        not isinstance(receiver, (list, tuple))
        or len(receiver) != 2
        or any(not is_int(value) or value < 0 or value > 0xFFFF for value in receiver)
    ):
        return "invalid", "authored_receiver_cell"
    if not is_int(material) or material < 0:
        return "invalid", "authored_material_id"
    # The current runtime has no authored role/family authority. The capture
    # must say so explicitly rather than treating Product's family as authored.
    if not is_int(role_family) or role_family != 0xFFFFFFFF or role_valid is not False:
        return "invalid", "authored_role_family_sentinel"
    if not is_int(family) or family < 0 or family > 2:
        return "invalid", "classifier_family"
    if not isinstance(dirt_role, bool):
        return "invalid", "dirt_mountain_role"
    if not is_int(elevation) or elevation < 0 or elevation > 3:
        return "invalid", "elevation_class"
    if not is_int(column) or column < 0:
        return "invalid", "matlib_column_id"
    if (
        not isinstance(profile, (list, tuple))
        or len(profile) != 3
        or any(not is_int(value) for value in profile)
        or profile[0] <= 0
        or profile[1] < 192
        or profile[2] < 0
        or profile[2] >= 64
    ):
        return "invalid", "matlib_profile"
    if profile_valid is not True:
        return "invalid", "matlib_profile_valid"
    if not is_int(profile_validity) or profile_validity != PRODUCT_ADMISSION_PROFILE_EXPECTED_VALIDITY_MASK:
        return "invalid", "matlib_profile_validity_bits"
    if not is_int(validity) or validity != PRODUCT_ADMISSION_EXPECTED_VALIDITY_MASK:
        return "invalid", "admission_validity_bits"
    if not isinstance(transport_valid, bool) or not transport_valid:
        return "invalid", "admission_transport"
    return None


def summarize_product_admission_contingency(rows: list[dict]) -> dict:
    """Account every row through the authored->classifier->b55->b43 chain."""
    categories = (
        "nonterrain_product_rows",
        "terrain_admission_witness_missing",
        "terrain_admission_witness_invalid",
        "terrain_authored_admission_to_b55_mismatch",
        "terrain_classifier_to_b55_mismatch",
        "terrain_b55_to_b43_actor_overlay",
        "terrain_b55_to_b43_missing",
        "terrain_b55_to_b43_mismatch",
        "terrain_full_chain_valid",
    )
    counts = Counter({category: 0 for category in categories})
    reasons: Counter = Counter()
    flow_counts: Counter = Counter()
    role_elevation_profile_counts: Counter = Counter()
    valid_witness_rows = 0
    for row in rows:
        if row.get("final_product_owner") != 1:
            counts["nonterrain_product_rows"] += 1
            continue
        error = _product_admission_witness_error(row)
        if error is not None:
            category, reason = error
            counts[
                "terrain_admission_witness_missing"
                if category == "missing"
                else "terrain_admission_witness_invalid"
            ] += 1
            reasons[f"{category}:{reason}"] += 1
            continue
        b55_material_id = row.get("final_product_material_id")
        b55_family = row.get("final_product_material_family")
        if (
            not isinstance(b55_material_id, int)
            or isinstance(b55_material_id, bool)
            or b55_material_id < 0
            or not isinstance(b55_family, int)
            or isinstance(b55_family, bool)
            or b55_family < 0
            or b55_family > 3
        ):
            counts["terrain_classifier_to_b55_mismatch"] += 1
            reasons["classifier_to_b55:malformed_b55_material_or_family"] += 1
            continue
        valid_witness_rows += 1
        flow_key = (
            int(row["final_product_authored_material_id"]),
            int(row["final_product_classifier_family"]),
            int(b55_material_id),
            int(b55_family),
            int(bool(row["final_product_dirt_mountain_role"])),
            int(row["final_product_elevation_class"]),
            int(row["final_product_matlib_column_id"]),
            int(row["final_product_matlib_profile"][2]),
        )
        flow_counts[flow_key] += 1
        role_elevation_profile_counts[
            (
                int(bool(row["final_product_dirt_mountain_role"])),
                int(row["final_product_elevation_class"]),
                int(row["final_product_matlib_column_id"]),
                int(row["final_product_matlib_profile"][2]),
            )
        ] += 1

        receiver = row["final_product_authored_receiver_cell"]
        authored_receiver_packed = (int(receiver[0]) << 16) | int(receiver[1])
        if not (
            row["final_product_authored_source_key"]
            == row.get("final_product_source_key")
            and authored_receiver_packed == row.get("final_product_matcell_packed")
            and row["final_product_authored_material_id"]
            == row.get("final_product_material_id")
        ):
            counts["terrain_authored_admission_to_b55_mismatch"] += 1
            continue
        if not (
            row["final_product_classifier_family"]
            == row.get("final_product_material_family")
            and row["final_product_matlib_column_id"]
            == row.get("final_product_material_id")
        ):
            counts["terrain_classifier_to_b55_mismatch"] += 1
            continue

        if row.get("final_paint_slot_actor_overlay") is True:
            counts["terrain_b55_to_b43_actor_overlay"] += 1
            continue
        paint_present = (
            row.get("final_paint_slot_present") is True
            and row.get("final_paint_slot_colour_present") is True
        )
        # b43 consumes the one elected whole-cell glyph.  The quarter-local
        # Product glyph remains provenance and may legitimately differ from it.
        elected_mask = cell_mask_winner_witness(row)
        elected_glyph = elected_mask.get("glyph")
        paint_fields_present = all(
            field in row
            for field in (
                "final_product_fg_rgb",
                "final_product_bg_rgb",
                "final_paint_slot_glyph",
                "final_paint_slot_fg_rgb",
                "final_paint_slot_bg_rgb",
            )
        ) and bool(elected_mask.get("available"))
        if not paint_present or not paint_fields_present:
            counts["terrain_b55_to_b43_missing"] += 1
            continue
        if not (
            elected_glyph == row["final_paint_slot_glyph"]
            and quantized_rgb(row["final_product_fg_rgb"])
            == quantized_rgb(row["final_paint_slot_fg_rgb"])
            and quantized_rgb(row["final_product_bg_rgb"])
            == quantized_rgb(row["final_paint_slot_bg_rgb"])
        ):
            counts["terrain_b55_to_b43_mismatch"] += 1
            continue
        counts["terrain_full_chain_valid"] += 1

    total = sum(counts.values())
    terrain_rows = len(rows) - counts["nonterrain_product_rows"]
    valid_rows = counts["terrain_full_chain_valid"]
    actor_overlay_rows = counts["terrain_b55_to_b43_actor_overlay"]
    structural_valid = bool(
        total == len(rows)
        and terrain_rows > 0
        and counts["terrain_admission_witness_missing"] == 0
        and counts["terrain_admission_witness_invalid"] == 0
        and sum(flow_counts.values()) == valid_witness_rows
    )
    gameplay_chain_mismatch_categories = (
        "terrain_authored_admission_to_b55_mismatch",
        "terrain_classifier_to_b55_mismatch",
        "terrain_b55_to_b43_missing",
        "terrain_b55_to_b43_mismatch",
    )
    gameplay_chain_mismatch_rows = sum(
        counts[category] for category in gameplay_chain_mismatch_categories
    )
    gameplay_chain_valid = bool(
        structural_valid and gameplay_chain_mismatch_rows == 0
    )
    first_collapse_category = next(
        (
            category
            for category in gameplay_chain_mismatch_categories
            if counts[category] > 0
        ),
        None,
    )
    def encode_flow(key: tuple, count: int) -> dict:
        return {
            "authored_material_id": key[0],
            "classifier_family": key[1],
            "b55_material_id": key[2],
            "b55_material_family": key[3],
            "dirt_mountain_role": bool(key[4]),
            "elevation_class": key[5],
            "matlib_column_id": key[6],
            "matlib_profile_row": key[7],
            "rows": count,
            "ratio": float(count) / float(valid_witness_rows)
            if valid_witness_rows else 0.0,
        }

    encoded_flow = [
        encode_flow(key, count)
        for key, count in sorted(flow_counts.items())
    ]
    encoded_companion = [
        {
            "dirt_mountain_role": bool(key[0]),
            "elevation_class": key[1],
            "matlib_column_id": key[2],
            "matlib_profile_row": key[3],
            "rows": count,
            "ratio": float(count) / float(valid_witness_rows)
            if valid_witness_rows else 0.0,
        }
        for key, count in sorted(role_elevation_profile_counts.items())
    ]
    return {
        "product_admission_contingency_source": (
            "capture-only b60 r31-r33 transported through sparse words 504-515"
        ),
        "product_admission_contingency_row_count": len(rows),
        "product_admission_contingency_counts": dict(counts),
        "product_admission_contingency_total": total,
        "product_admission_contingency_total_matches_input": total == len(rows),
        "product_admission_terrain_rows": terrain_rows,
        "product_admission_terrain_valid_rows": valid_rows,
        # Structural evidence validity is deliberately independent from the
        # observed gameplay mapping. A classifier or b55->b43 mismatch is a
        # valid red result with a first-collapse category, not an invalid
        # capture. Keep this compatibility name as the structural gate owner.
        "product_admission_contingency_valid": structural_valid,
        "product_admission_evidence_valid": structural_valid,
        "product_admission_gameplay_chain_valid": gameplay_chain_valid,
        "product_admission_gameplay_chain_mismatch_rows": gameplay_chain_mismatch_rows,
        "product_admission_first_collapse_category": first_collapse_category,
        "product_admission_terrain_actor_overlay_rows": actor_overlay_rows,
        "product_admission_contingency_reasons": dict(reasons),
        "product_admission_flow_valid_witness_rows": valid_witness_rows,
        "product_admission_flow_invalid_witness_rows": (
            terrain_rows - valid_witness_rows
        ),
        "product_admission_flow_histogram": encoded_flow,
        "product_admission_flow_histogram_total": sum(flow_counts.values()),
        "product_admission_flow_histogram_total_matches_valid_witness_rows": (
            sum(flow_counts.values()) == valid_witness_rows
        ),
        "product_admission_flow_companion_distribution": encoded_companion,
        "product_admission_flow_companion_total": sum(
            role_elevation_profile_counts.values()
        ),
        "product_admission_required_fields": list(PRODUCT_ADMISSION_REQUIRED_FIELDS),
        "product_admission_runtime_missing_fields": ["authored_role_family"],
        "product_admission_role_family_contract": (
            "0xffffffff sentinel with validity false; runtime has no authoritative field"
        ),
    }


def summarize_product_owner_name_transport(rows: list[dict]) -> dict:
    """Audit the recorder label against b55's authoritative numeric owner."""
    observed = 0
    invalid = 0
    mismatch_pairs: Counter = Counter()
    for row in rows:
        if "final_product_owner" not in row:
            continue
        observed += 1
        if row.get("final_product_owner_name_transport_valid") is True:
            continue
        invalid += 1
        mismatch_pairs[
            (
                str(row.get("final_product_owner_name_transport", "missing")),
                str(row.get("final_product_owner_name", "unknown")),
            )
        ] += 1
    return {
        "product_owner_name_transport_rows": observed,
        "product_owner_name_transport_invalid_rows": invalid,
        "product_owner_name_transport_mismatch_pairs": {
            f"{transported}->{canonical}": count
            for (transported, canonical), count in sorted(mismatch_pairs.items())
        },
    }


def build_gate_summary(summary: dict, args: argparse.Namespace) -> dict:
    frame_count = int(summary.get("frame_count", 0))
    row_count = int(summary.get("row_count", 0))
    projection_frames = int(summary.get("projection_fields_present_frames", 0))
    snap_world_values = summary.get("pixel_camera_snap_world_values", [])
    snap_world_frames = int(summary.get("pixel_camera_snap_world_observed_frames", 0))
    # FL-4359 accepts moving-camera evidence only. The camera must actually move
    # across enough frame pairs; otherwise jitter/shimmer are not falsifiable and
    # the gameplay gates must not pass on a steady pose.
    moving_frame_pairs = int(summary.get("moving_frame_pairs", 0))
    camera_motion_ratio = float(summary.get("camera_motion_continuous_ratio", 0.0))
    camera_is_moving = (
        moving_frame_pairs >= args.min_moving_frame_pairs
        and camera_motion_ratio >= args.min_camera_motion_ratio
    )
    # FL-4359 false-green guard: the shimmer gate is only meaningful if enough
    # world-anchor-matched comparable cells exist across enough frame pairs. A
    # run with all pairs scrolled/unmatched must not pass vacuously.
    min_comparable_cells = int(summary.get("min_comparable_cells", 0))
    min_nonzero_comparable_cells = int(summary.get("min_nonzero_comparable_cells", 0))
    total_comparable_cells = int(summary.get("total_comparable_cells", 0))
    comparable_pair_count = int(summary.get("comparable_pair_count", 0))
    if comparable_pair_count == 0 and total_comparable_cells > 0:
        comparable_pair_count = 1
    comparable_pairs_meeting_min = int(summary.get("comparable_pairs_meeting_min", 0))
    if comparable_pairs_meeting_min == 0 and min_nonzero_comparable_cells >= args.min_comparable_cells:
        comparable_pairs_meeting_min = comparable_pair_count
    required_comparable_pairs = int(
        summary.get(
            "required_comparable_pairs",
            min(max(comparable_pair_count, 1), max(int(getattr(args, "min_moving_frame_pairs", 1)), 1)),
        )
    )
    comparable_ok = (
        comparable_pairs_meeting_min >= required_comparable_pairs
    )
    water_splat_hidden_as_sky_count = int(summary.get("water_splat_winner_hidden_as_sky_count", 0))
    final_product_source_key_count = int(summary.get("final_product_source_key_count", 0))
    mountain_material_rows = int(summary.get("mountain_material_rows", 0))
    mountain_material_invalid_rows = int(summary.get("mountain_material_invalid_rows", 0))
    mountain_dirt_ratio = float(summary.get("mountain_dirt_ratio", 0.0))
    terrain_homogeneity_rows = int(
        summary.get("terrain_product_homogeneity_rows", 0)
    )
    terrain_homogeneity_invalid_rows = int(
        summary.get("terrain_product_homogeneity_invalid_rows", 0)
    )
    terrain_dominant_material_ratio = float(
        summary.get("terrain_product_dominant_material_ratio", 0.0)
    )
    terrain_homogeneity_baseline_calibrated = bool(
        summary.get("terrain_product_homogeneity_baseline_calibrated", False)
    )
    terrain_presentation_distribution_admissible = (
        terrain_homogeneity_rows > 0
        and terrain_homogeneity_invalid_rows == 0
        and terrain_dominant_material_ratio <= MOUNTAIN_DIRT_DOMINANCE_LIMIT
        and terrain_homogeneity_baseline_calibrated
    )
    presentation_observer_population_accounting_valid = bool(
        summary.get("presentation_observer_population_accounting_valid", False)
    )
    terrain_final_paint_eligible_rows = int(
        summary.get("terrain_product_final_paint_eligible_rows", 0)
    )
    terrain_final_paint_glyph_join_rows = int(
        summary.get("terrain_product_final_paint_glyph_join_rows", 0)
    )
    terrain_final_paint_colour_join_rows = int(
        summary.get("terrain_product_final_paint_colour_join_rows", 0)
    )
    terrain_final_paint_actor_overlay_rows = int(
        summary.get("terrain_product_final_paint_actor_overlay_rows", 0)
    )
    terrain_final_paint_missing_slot_rows = int(
        summary.get("terrain_product_final_paint_missing_slot_rows", 0)
    )
    terrain_final_paint_missing_colour_rows = int(
        summary.get("terrain_product_final_paint_missing_colour_rows", 0)
    )
    product_owner_name_transport_rows = int(
        summary.get("product_owner_name_transport_rows", 0)
    )
    product_owner_name_transport_invalid_rows = int(
        summary.get("product_owner_name_transport_invalid_rows", 0)
    )
    split_diag_present = bool(summary.get("surface_identity_split_diagnostics_present", False))
    max_selected_surface_identity_churn_pct = float(summary.get("max_selected_surface_identity_churn_pct", 0.0))
    max_post_surface_bounce_pct = float(summary.get("max_post_surface_bounce_pct", 0.0))
    max_selected_surface_identity_churn_limit = float(
        getattr(args, "max_selected_surface_identity_churn_pct", getattr(args, "max_probe_record_churn_pct", 0.0))
    )
    max_post_surface_bounce_limit = float(
        getattr(args, "max_post_surface_bounce_pct", getattr(args, "max_post_probe_surface_bounce_pct", 0.0))
    )
    motion_compensated_metric_present = (
        "motion_compensated_final_cell_instability_pct" in summary
    )
    final_visible_signature_instability_pct = float(
        summary.get("motion_compensated_final_cell_instability_pct", 0.0)
    )
    cell_mask_owner_stability = summary.get("cell_mask_owner_stability", {})
    if not isinstance(cell_mask_owner_stability, dict):
        cell_mask_owner_stability = {}
    cell_mask_owner_counts = cell_mask_owner_stability.get("counts", {})
    if not isinstance(cell_mask_owner_counts, dict):
        cell_mask_owner_counts = {}
    cell_mask_owner_comparable = int(cell_mask_owner_counts.get("comparable_cells", 0))
    cell_mask_owner_invalid = int(cell_mask_owner_stability.get("invalid_cell_count", 0))
    cell_mask_owner_pair_count = int(
        cell_mask_owner_stability.get("comparable_pair_count", 0)
    )
    cell_mask_owner_instability_pct = float(
        cell_mask_owner_stability.get("max_instability_pct", 0.0)
    )
    continuous_colour_stability = summary.get(
        "continuous_product_colour_stability", {}
    )
    if not isinstance(continuous_colour_stability, dict):
        continuous_colour_stability = {}
    continuous_colour_counts = continuous_colour_stability.get("counts", {})
    if not isinstance(continuous_colour_counts, dict):
        continuous_colour_counts = {}
    continuous_colour_invalid = int(
        continuous_colour_stability.get("invalid_terrain_row_count", 0)
    )
    continuous_colour_invalid_witness = int(
        continuous_colour_stability.get("invalid_colour_witness_row_count", 0)
    )
    continuous_colour_invalid_finalization = int(
        continuous_colour_stability.get(
            "invalid_finalization_witness_row_count", 0
        )
    )
    continuous_colour_pair_count = int(
        continuous_colour_stability.get("comparable_pair_count", 0)
    )
    continuous_colour_min_comparable = int(
        continuous_colour_stability.get("min_comparable_samples_per_pair", 0)
    )
    continuous_colour_instability_pct = float(
        continuous_colour_stability.get("max_instability_pct", 0.0)
    )
    fixed_screen_temporal_present = bool(summary.get("fixed_screen_temporal_evidence_present", False))
    fixed_screen_temporal_cells = int(summary.get("fixed_screen_temporal_comparable_cells", 0))
    projection_modes = {
        int(mode): int(count)
        for mode, count in (summary.get("camera_projection_modes", {}) or {}).items()
    }
    projection_mode_set = set(projection_modes)
    mixed_projection = bool(summary.get("mixed_projection", False)) or len(projection_mode_set) > 1
    perspective_capture = projection_mode_set == {0}
    orthographic_capture = projection_mode_set == {1}
    snap_world_nonzero_frames = int(summary.get("pixel_camera_snap_world_nonzero_frames", 0))
    snap_world_all_observed = snap_world_frames == frame_count and frame_count > 0
    perspective_snap_contract = snap_world_all_observed and snap_world_nonzero_frames == 0
    orthographic_snap_contract = snap_world_all_observed and snap_world_nonzero_frames == frame_count
    snap_shift_contract_valid = int(summary.get("snap_contract_mismatch_frames", 0)) == 0
    perspective_zero_shift_contract = (
        snap_shift_contract_valid
        and int(summary.get("style_post_shift_nonzero_frames", 0)) == 0
        and int(summary.get("camera_snap_residual_nonzero_frames", 0)) == 0
    )
    orthographic_shift_contract = snap_shift_contract_valid
    motion_compensated_content_evidence = (
        terrain_presentation_distribution_admissible
        and presentation_observer_population_accounting_valid
        and bool(summary.get("corrected_final_cell_oracle_valid", False))
        and motion_compensated_metric_present
        and final_visible_signature_instability_pct <= args.max_content_jitter_pct
    )
    if mixed_projection:
        jitter_metric_owner = "mixed_projection_invalid"
        snap_metric_applicable = False
        projection_jitter_pass = False
    elif orthographic_capture:
        jitter_metric_owner = "snap_grid"
        snap_metric_applicable = True
        projection_jitter_pass = orthographic_snap_contract and orthographic_shift_contract
    elif perspective_capture:
        # Perspective has no grid snap residual to measure. Zero snap_world and
        # zero presentation shift are the complete snap contract. Content
        # shimmer is a separate exact-world/Product-anchor gate below; ordinary
        # scene motion at a fixed screen cell must not become a snap failure.
        jitter_metric_owner = "camera_temporal"
        snap_metric_applicable = False
        projection_jitter_pass = perspective_snap_contract and perspective_zero_shift_contract
    else:
        jitter_metric_owner = "legacy_or_unknown"
        snap_metric_applicable = False
        projection_jitter_pass = bool(summary.get("snap_metric_evidence_present", False))
    sunday_comparison = summary.get("sunday_comparison", {})
    required_field_coverage = summary.get("required_field_coverage", {})
    gates = {
        "evidence_comparable_cells_present": {
            "pass": comparable_ok,
            "min_comparable_cells": min_comparable_cells,
            "min_nonzero_comparable_cells": min_nonzero_comparable_cells,
            "min_comparable_cells_limit": args.min_comparable_cells,
            "comparable_pair_count": comparable_pair_count,
            "comparable_pairs_meeting_min": comparable_pairs_meeting_min,
            "required_comparable_pairs": required_comparable_pairs,
            "total_comparable_cells": total_comparable_cells,
        },
        "scenario_moving_camera_continuous": {
            "pass": camera_is_moving,
            "moving_frame_pairs": moving_frame_pairs,
            "min_moving_frame_pairs": args.min_moving_frame_pairs,
            "camera_motion_continuous_ratio": camera_motion_ratio,
            "min_camera_motion_ratio": args.min_camera_motion_ratio,
            "camera_translation_total": summary.get("camera_translation_total", 0.0),
            "camera_translation_max_per_frame": summary.get("camera_translation_max_per_frame", 0.0),
        },
        "evidence_jitter_capture_present": {
            "pass": projection_jitter_pass,
            "row_count": row_count,
            "frame_count": frame_count,
            "pixel_camera_snap_world_observed_frames": snap_world_frames,
            "pixel_camera_snap_world_values": snap_world_values,
            "pixel_camera_snap_world_nonzero_frames": summary.get("pixel_camera_snap_world_nonzero_frames", 0),
            "perspective_temporal_measure_present": summary.get("perspective_temporal_measure_present", False),
            "jitter_metric_owner": jitter_metric_owner,
            "snap_metric_applicable": snap_metric_applicable,
            "projection_mode_set": sorted(projection_mode_set),
            "mixed_projection": mixed_projection,
        },
        "evidence_product_time_constant": {
            "pass": bool(summary.get("product_time_constant", False)) and len(summary.get("product_time_values", [])) > 0,
            "product_time_values": summary.get("product_time_values", []),
        },
        "evidence_projection_contract_valid": {
            "pass": (
                int(summary.get("projection_contract_invalid_frames", 0)) == 0
                and not mixed_projection
                and snap_shift_contract_valid
                and (perspective_zero_shift_contract if perspective_capture else True)
            ),
            "projection_contract_invalid_frames": int(summary.get("projection_contract_invalid_frames", 0)),
        },
        "evidence_projection_fields_present": {
            "pass": frame_count > 0 and projection_frames == frame_count,
            "projection_fields_present_frames": projection_frames,
            "frame_count": frame_count,
            "camera_projection_modes": summary.get("camera_projection_modes", {}),
            "camera_fov_values": summary.get("camera_fov_values", []),
            "camera_near_values": summary.get("camera_near_values", []),
            "camera_far_values": summary.get("camera_far_values", []),
        },
        "evidence_required_fields_present": {
            "pass": bool(required_field_coverage.get("all_present", False)),
            "required_field_coverage": required_field_coverage,
        },
        "evidence_lifecycle_facts_present": {
            "pass": bool(
                (
                    required_field_coverage.get("lifecycle_facts")
                    if isinstance(required_field_coverage.get("lifecycle_facts"), dict)
                    else {}
                ).get("all_valid", True)
            ),
            "lifecycle_facts": (
                required_field_coverage.get("lifecycle_facts")
                if isinstance(required_field_coverage.get("lifecycle_facts"), dict)
                else {}
            ),
        },
        "evidence_no_water_splat_sky_holes": {
            "pass": water_splat_hidden_as_sky_count == 0,
            "water_splat_winner_hidden_as_sky_count": water_splat_hidden_as_sky_count,
        },
        "evidence_final_product_source_key_present": {
            "pass": final_product_source_key_count > 0,
            "final_product_source_key_count": final_product_source_key_count,
        },
        "evidence_product_owner_name_transport_valid": {
            "pass": product_owner_name_transport_rows > 0
            and product_owner_name_transport_invalid_rows == 0,
            "numeric_owner_rows": product_owner_name_transport_rows,
            "invalid_derived_label_rows": product_owner_name_transport_invalid_rows,
            "mismatch_pairs": summary.get(
                "product_owner_name_transport_mismatch_pairs", {}
            ),
            "authority": "b55_numeric_product_owner_enum",
        },
        "evidence_surface_identity_split_diagnostics_present": {
            "pass": split_diag_present,
            "surface_identity_split_diagnostics_present": split_diag_present,
            "pair_count": int(summary.get("pair_count", 0)),
            "total_comparable_cells": int(summary.get("total_comparable_cells", 0)),
        },
        "evidence_mountain_material_distribution_present": {
            "pass": mountain_material_rows > 0 and mountain_material_invalid_rows == 0,
            "mountain_material_rows": mountain_material_rows,
            "mountain_material_invalid_rows": mountain_material_invalid_rows,
            "mountain_material_source": summary.get("mountain_material_source"),
            "mountain_material_role_counts": summary.get("mountain_material_role_counts", {}),
            "mountain_material_id_counts": summary.get("mountain_material_id_counts", {}),
        },
        "evidence_mountain_dirt_not_dominant": {
            # Historical diagnostic only. The 0.65 number has no same-pose
            # baseline calibration and cannot own final acceptance.
            "pass": False,
            "mountain_dirt_ratio": mountain_dirt_ratio,
            "mountain_dirt_ratio_limit": MOUNTAIN_DIRT_DOMINANCE_LIMIT,
            "baseline_calibrated": False,
            "reason": "historical_threshold_not_same_pose_baseline_calibrated",
            "mountain_material_invalid_rows": mountain_material_invalid_rows,
            "mountain_material_role_counts": summary.get("mountain_material_role_counts", {}),
        },
        "evidence_terrain_product_homogeneity_present": {
            "pass": terrain_homogeneity_rows > 0
            and terrain_homogeneity_invalid_rows == 0,
            "terrain_product_homogeneity_rows": terrain_homogeneity_rows,
            "terrain_product_homogeneity_invalid_rows": terrain_homogeneity_invalid_rows,
            "source": summary.get("terrain_product_homogeneity_source"),
            "material_id_counts": summary.get(
                "terrain_product_material_id_counts", {}
            ),
            "glyph_counts": summary.get("terrain_product_glyph_counts", {}),
        },
        "evidence_terrain_product_final_paint_join_complete": {
            "pass": terrain_homogeneity_rows > 0
            and terrain_homogeneity_invalid_rows == 0
            and terrain_final_paint_eligible_rows > 0
            and terrain_final_paint_actor_overlay_rows >= 0
            and (
                terrain_final_paint_eligible_rows
                + terrain_final_paint_actor_overlay_rows
                == terrain_homogeneity_rows
            )
            and terrain_final_paint_missing_slot_rows == 0
            and terrain_final_paint_missing_colour_rows == 0
            and (
                terrain_final_paint_glyph_join_rows
                == terrain_final_paint_eligible_rows
            )
            and (
                terrain_final_paint_colour_join_rows
                == terrain_final_paint_eligible_rows
            ),
            "terrain_product_rows": terrain_homogeneity_rows,
            "eligible_non_actor_rows": terrain_final_paint_eligible_rows,
            "actor_overlay_rows": terrain_final_paint_actor_overlay_rows,
            "glyph_join_rows": terrain_final_paint_glyph_join_rows,
            "colour_join_rows": terrain_final_paint_colour_join_rows,
            "missing_slot_rows": terrain_final_paint_missing_slot_rows,
            "missing_colour_rows": terrain_final_paint_missing_colour_rows,
            "claim_scope": (
                "transport completeness only; Product-to-paint equality and "
                "terrain material semantics remain separate verdicts"
            ),
        },
        "gameplay_terrain_product_distribution_non_degenerate": {
            "pass": terrain_homogeneity_rows > 0
            and terrain_homogeneity_invalid_rows == 0
            and terrain_dominant_material_ratio <= MOUNTAIN_DIRT_DOMINANCE_LIMIT,
            "terrain_product_dominant_material_id": summary.get(
                "terrain_product_dominant_material_id"
            ),
            "terrain_product_dominant_material_ratio": terrain_dominant_material_ratio,
            "dominant_material_ratio_limit": MOUNTAIN_DIRT_DOMINANCE_LIMIT,
            "threshold_basis": (
                "provisional_temporal_proof_sensitivity_guard_not_visual_baseline_band"
            ),
            "claim_scope": (
                "prevents shimmer closure on a degenerate presentation distribution; "
                "does not prove a renderer material regression"
            ),
        },
        "evidence_terrain_product_homogeneity_baseline_calibrated": {
            "pass": bool(
                summary.get("terrain_product_homogeneity_baseline_calibrated", False)
            ),
            "reason": summary.get("terrain_product_homogeneity_baseline_reason"),
        },
        "evidence_presentation_observer_population_accounted": {
            "pass": presentation_observer_population_accounting_valid,
            "owners": summary.get(
                "presentation_observer_population_accounting", {}
            ),
            "claim_scope": (
                "every matched, unmatched, new, invalid, and excluded cell or "
                "sample is arithmetically assigned before presentation equality "
                "can contribute acceptance evidence"
            ),
        },
        "evidence_foliage_product_fields_present": {
            "pass": bool(summary.get("foliage_fields_present_all_frames", False)),
            "foliage_fields_present_frames": summary.get("foliage_fields_present_frames", 0),
            "frame_count": frame_count,
        },
        "gameplay_moving_camera_jitter_bounded": {
            "pass": (
                camera_is_moving
                and projection_jitter_pass
            ),
            "camera_is_moving": camera_is_moving,
            "max_snap_px_delta": summary.get("max_snap_px_delta", 0.0),
            "max_snap_px_delta_limit": args.max_snap_px_delta,
            "snap_sign_flip_rate": summary.get("snap_sign_flip_rate", 0.0),
            "snap_sign_flip_rate_limit": args.max_snap_sign_flip_rate,
            "max_snap_compensation_error_px": summary.get("max_snap_compensation_error_px", 0.0),
            "snap_compensation_error_limit_px": 1.0e-3,
            "raw_snap_residual_wrap_is_acceptance_gate": False,
            "jitter_metric_owner": jitter_metric_owner,
            "snap_metric_applicable": snap_metric_applicable,
            "projection_mode_set": sorted(projection_mode_set),
        },
        "gameplay_content_shimmer_bounded": {
            "pass": (
                camera_is_moving
                and motion_compensated_content_evidence
            ),
            "camera_is_moving": camera_is_moving,
            "presentation_distribution_admissible": (
                terrain_presentation_distribution_admissible
            ),
            "population_accounting_valid": (
                presentation_observer_population_accounting_valid
            ),
            "diagnostic_only_reason": (
                None
                if terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                else "terrain_presentation_degenerate_uncalibrated_or_population_unaccounted"
            ),
            "corrected_final_cell_oracle_valid": bool(
                summary.get("corrected_final_cell_oracle_valid", False)
            ),
            "product_anchor_provenance_complete": bool(summary.get("product_anchor_provenance_complete", False)),
            "product_anchor_provenance": summary.get("product_anchor_provenance", {}),
            "motion_compensated_metric_present": motion_compensated_metric_present,
            "definition": (
                "maximum of whole-cell mask instability at the elected mask "
                "owner and Product FG/BG instability at the exact continuous "
                "terrain paint sample, admissible only under a nondegenerate "
                "same-pose-calibrated Product distribution; categorical b55 "
                "anchor colour joins are diagnostic only"
            ),
            "motion_compensated_final_cell_instability_pct": final_visible_signature_instability_pct,
            "max_content_jitter_pct_limit": args.max_content_jitter_pct,
            # Same-screen observations remain useful attribution, but moving
            # the camera legitimately changes what a fixed screen cell sees.
            "fixed_screen_metric_is_acceptance_gate": False,
            "fixed_screen_temporal_evidence_present": fixed_screen_temporal_present,
            "fixed_screen_temporal_comparable_cells": fixed_screen_temporal_cells,
            "fixed_screen_visible_signature_flip_pct": summary.get("fixed_screen_visible_signature_flip_pct", 0.0),
            "fixed_screen_backstop_marker_flip_pct": summary.get("fixed_screen_backstop_marker_flip_pct", 0.0),
            "fixed_screen_rekey_reason_flip_pct": summary.get("fixed_screen_rekey_reason_flip_pct", 0.0),
            "fixed_screen_highlighted_classification_flip_pct": summary.get("fixed_screen_highlighted_classification_flip_pct", 0.0),
            "fixed_screen_stable_highlight_visible_flip_pct": summary.get("fixed_screen_stable_highlight_visible_flip_pct", 0.0),
            "world_anchor_metric_is_acceptance_gate": True,
            "legacy_max_pair_jitter_pct": summary.get("max_pair_jitter_pct", 0.0),
            "legacy_max_pair_jitter_is_acceptance_gate": False,
        },
        "gameplay_continuous_product_colour_stability_bounded": {
            "pass": (
                camera_is_moving
                and terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                and continuous_colour_pair_count >= required_comparable_pairs
                and continuous_colour_min_comparable >= args.min_comparable_cells
                and continuous_colour_invalid == 0
                and continuous_colour_invalid_witness == 0
                and continuous_colour_invalid_finalization == 0
                and continuous_colour_instability_pct <= args.max_content_jitter_pct
            ),
            "camera_is_moving": camera_is_moving,
            "presentation_distribution_admissible": (
                terrain_presentation_distribution_admissible
            ),
            "population_accounting_valid": (
                presentation_observer_population_accounting_valid
            ),
            "diagnostic_only_reason": (
                None
                if terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                else "terrain_presentation_degenerate_uncalibrated_or_population_unaccounted"
            ),
            "definition": (
                "Product FG/BG at byte-identical terrain 8x8 paint samples "
                "with stable semantic Product state; the byte-change statistic "
                "is diagnostic-only until the Product distribution is "
                "nondegenerate and same-pose baseline-calibrated"
            ),
            "comparable_pair_count": continuous_colour_pair_count,
            "required_comparable_pairs": required_comparable_pairs,
            "min_comparable_samples_per_pair": continuous_colour_min_comparable,
            "min_comparable_samples_limit": args.min_comparable_cells,
            "invalid_terrain_row_count": continuous_colour_invalid,
            "invalid_colour_witness_row_count": continuous_colour_invalid_witness,
            "invalid_finalization_witness_row_count": (
                continuous_colour_invalid_finalization
            ),
            "max_instability_pct": continuous_colour_instability_pct,
            "max_content_jitter_pct_limit": args.max_content_jitter_pct,
            "counts": continuous_colour_counts,
            "first_divergence_order": continuous_colour_stability.get(
                "first_divergence_order", []
            ),
        },
        "gameplay_cell_mask_owner_stability_bounded": {
            "pass": (
                camera_is_moving
                and terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                and cell_mask_owner_comparable >= args.min_comparable_cells
                and cell_mask_owner_pair_count >= required_comparable_pairs
                and cell_mask_owner_invalid == 0
                and cell_mask_owner_instability_pct <= args.max_content_jitter_pct
            ),
            "camera_is_moving": camera_is_moving,
            "presentation_distribution_admissible": (
                terrain_presentation_distribution_admissible
            ),
            "population_accounting_valid": (
                presentation_observer_population_accounting_valid
            ),
            "diagnostic_only_reason": (
                None
                if terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                else "terrain_presentation_degenerate_uncalibrated_or_population_unaccounted"
            ),
            "definition": (
                "one whole-cell b43 glyph mask matched by its elected semantic "
                "anchor; quarter-slot winners use the elected b55 Product anchor; "
                "stability is falsifier-only until presentation diversity and "
                "complete population accounting are both admissible"
            ),
            "comparable_cells": cell_mask_owner_comparable,
            "comparable_pair_count": cell_mask_owner_pair_count,
            "required_comparable_pairs": required_comparable_pairs,
            "invalid_cell_count": cell_mask_owner_invalid,
            "max_instability_pct": cell_mask_owner_instability_pct,
            "max_content_jitter_pct_limit": args.max_content_jitter_pct,
            "counts": cell_mask_owner_counts,
            "invalid_reasons": cell_mask_owner_stability.get("invalid_reasons", {}),
        },
        "gameplay_surface_identity_bounce_bounded": {
            "pass": (
                camera_is_moving
                and comparable_ok
                and split_diag_present
                and terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                and max_post_surface_bounce_pct <= max_post_surface_bounce_limit
            ),
            "camera_is_moving": camera_is_moving,
            "comparable_cells_present": comparable_ok,
            "surface_identity_split_diagnostics_present": split_diag_present,
            "presentation_component_admissible": (
                terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
            ),
            "diagnostic_only_reason": (
                None
                if terrain_presentation_distribution_admissible
                and presentation_observer_population_accounting_valid
                else "combined_identity_presentation_metric_contains_nonadmissible_presentation_component"
            ),
            "max_selected_surface_identity_churn_pct": max_selected_surface_identity_churn_pct,
            "max_selected_surface_identity_churn_pct_limit": max_selected_surface_identity_churn_limit,
            "selected_surface_identity_churn_is_blocking": False,
            "selected_surface_identity_scope": "b64 diagnostic; not the matched b55 Product identity",
            "max_post_surface_bounce_pct": max_post_surface_bounce_pct,
            "max_post_surface_bounce_pct_limit": max_post_surface_bounce_limit,
            "max_final_product_identity_churn_pct": float(summary.get("max_final_product_identity_churn_pct", 0.0)),
            "max_cloud_shadow_only_pct": float(summary.get("max_cloud_shadow_only_pct", 0.0)),
            "max_water_phase_churn_pct": float(summary.get("max_water_phase_churn_pct", 0.0)),
            "max_weather_effect_paint_pct": float(summary.get("max_weather_effect_paint_pct", 0.0)),
        },
        # FL-4359 same-key companion diagnostic (Codex). Companion diagnostic only —
        # the labeled June-14 PNG grid stays the visual acceptance surface (FL-4377);
        # this is a JSONL-side fail-closed check that the capture carries terrain
        # source_key + terr_stable_cell samples the flip analysis needs (Law 6). The
        # flip statistics (same_key_semantic_flip_observed / per-field counts) are
        # measurements, not a pass/fail — an observed flip is data, not a regression.
        "evidence_same_key_terrain_present": {
            "pass": int(summary.get("same_key_terrain_rows", 0)) >= getattr(args, "min_same_key_terrain_rows", 50),
            "same_key_terrain_rows": int(summary.get("same_key_terrain_rows", 0)),
            "min_required": getattr(args, "min_same_key_terrain_rows", 50),
            "same_key_semantic_flip_observed": bool(summary.get("same_key_semantic_flip_observed", False)),
        },
    }
    oracle_blocking_gates: list[str] = []
    selected_oracle_profile = str(getattr(args, "proof_profile", "general"))
    if selected_oracle_profile in {
        "same_frame_oracle_v1",
        FULL_GRID_TEMPORAL_ORACLE_PROFILE,
    }:
        admission_summary = summary.get("product_admission_contingency", {})
        admission_counts = (
            admission_summary.get("product_admission_contingency_counts", {})
            if isinstance(admission_summary, dict)
            else {}
        )
        def _admission_count_is_zero(name: str) -> bool:
            value = admission_counts.get(name)
            return type(value) is int and value == 0

        def _admission_bool_is_true(name: str) -> bool:
            return (
                isinstance(admission_summary, dict)
                and admission_summary.get(name) is True
            )

        terrain_rows_value = (
            admission_summary.get("product_admission_terrain_rows")
            if isinstance(admission_summary, dict)
            else None
        )
        admission_gate_pass = (
            isinstance(admission_summary, dict)
            and _admission_bool_is_true("product_admission_contingency_valid")
            and _admission_bool_is_true(
                "product_admission_contingency_total_matches_input"
            )
            and _admission_bool_is_true(
                "product_admission_flow_histogram_total_matches_valid_witness_rows"
            )
            and type(terrain_rows_value) is int
            and terrain_rows_value > 0
            and _admission_count_is_zero("terrain_admission_witness_missing")
            and _admission_count_is_zero("terrain_admission_witness_invalid")
        )
        gates["evidence_product_admission_contingency_valid"] = {
            "pass": admission_gate_pass,
            "source": admission_summary.get(
                "product_admission_contingency_source", "missing"
            ) if isinstance(admission_summary, dict) else "missing",
            "terrain_rows": int(
                admission_summary.get("product_admission_terrain_rows", 0)
            ) if isinstance(admission_summary, dict) else 0,
            "counts": admission_counts,
            "total_matches_input": bool(
                admission_summary.get(
                    "product_admission_contingency_total_matches_input", False
                )
            ) if isinstance(admission_summary, dict) else False,
            "flow_total_matches_valid_witness_rows": bool(
                admission_summary.get(
                    "product_admission_flow_histogram_total_matches_valid_witness_rows",
                    False,
                )
            ) if isinstance(admission_summary, dict) else False,
            "required_zero_counts": [
                "terrain_admission_witness_missing",
                "terrain_admission_witness_invalid",
            ],
        }
        gameplay_chain_gate_pass = (
            admission_gate_pass
            and _admission_bool_is_true("product_admission_gameplay_chain_valid")
            and type(
                admission_summary.get(
                    "product_admission_gameplay_chain_mismatch_rows"
                )
            ) is int
            and admission_summary.get(
                "product_admission_gameplay_chain_mismatch_rows"
            ) == 0
        )
        gates["gameplay_product_admission_chain_exact"] = {
            "pass": gameplay_chain_gate_pass,
            "first_collapse_category": admission_summary.get(
                "product_admission_first_collapse_category"
            ) if isinstance(admission_summary, dict) else None,
            "mismatch_rows": admission_summary.get(
                "product_admission_gameplay_chain_mismatch_rows", 0
            ) if isinstance(admission_summary, dict) else 0,
            "scope": (
                "authored admission -> classifier -> b55 -> b43 exactness; "
                "actor overlays are separately accounted"
            ),
        }
        gates["evidence_same_frame_oracle_spatial_rows_valid"] = {
            "pass": bool(summary.get("same_frame_oracle_spatial_rows_valid", False))
            and bool(summary.get("same_frame_oracle_structurally_valid", False)),
            "schema": summary.get("same_frame_oracle_schema", ""),
            "row_count": row_count,
            "oracle_rows": int(summary.get("same_frame_oracle_rows", 0)),
            "structurally_valid_rows": int(
                summary.get("same_frame_oracle_structurally_valid_rows", 0)
            ),
            "scope": "admitted_sparse_rows_only",
            "exhaustive_frame_coverage": bool(
                summary.get("same_frame_oracle_exhaustive_frame_coverage", False)
            ),
        }
        # These are explicit evidence classifications, not hidden aliases for
        # the spatial gate.  A sparse snapshot can remain useful for local
        # geometry attribution while it is categorically insufficient for a
        # full-grid or temporal claim.
        gates["evidence_same_frame_oracle_exhaustive_frame_coverage"] = {
            "pass": bool(
                summary.get("same_frame_oracle_exhaustive_frame_coverage", False)
            ),
            "expected_subcells_per_frame": summary.get(
                "same_frame_oracle_expected_subcells_per_frame", []
            ),
            "rows_per_frame": summary.get(
                "same_frame_oracle_rows_per_frame", {}
            ),
            "unique_subcells_per_frame": summary.get(
                "same_frame_oracle_unique_subcells_per_frame", {}
            ),
            "sampling_policy": summary.get(
                "same_frame_oracle_sampling_policy", []
            ),
            "scope": "full_subcell_grid_required",
        }
        gates["evidence_same_frame_oracle_temporal_continuity"] = {
            "pass": bool(summary.get("same_frame_oracle_temporal_sequence_valid", False)),
            "invalid_reasons": summary.get(
                "same_frame_oracle_temporal_invalid_reasons", []
            ),
            "frame_indices": summary.get("same_frame_oracle_frame_indices", []),
            "sequence_frames": summary.get("same_frame_oracle_sequence_frames", []),
            "authority_tick_continuity_valid": bool(
                summary.get("same_frame_oracle_authority_tick_continuity_valid", False)
            ),
            "source_identity_valid": bool(
                summary.get("same_frame_oracle_source_identity_valid", False)
            ),
            "scope": "time_separated_current_build_sequence",
        }
        gates["evidence_same_frame_oracle_query_complete"] = {
            "pass": int(summary.get("same_frame_oracle_rows", 0)) > 0
            and int(summary.get("same_frame_oracle_query_invalid_rows", 0)) == 0,
            "query_invalid_rows": int(
                summary.get("same_frame_oracle_query_invalid_rows", 0)
            ),
        }
        gates["gameplay_same_frame_geometry_agrees"] = {
            "pass": int(summary.get("same_frame_oracle_rows", 0)) > 0
            and int(summary.get("same_frame_oracle_geometry_defect_rows", 0)) == 0,
            "geometry_defect_rows": int(
                summary.get("same_frame_oracle_geometry_defect_rows", 0)
            ),
            "classification_counts": summary.get(
                "same_frame_oracle_classification_counts", {}
            ),
            "depth_delta_is_diagnostic_only": True,
            "depth_delta_max": float(
                summary.get("same_frame_oracle_depth_delta_max", 0.0)
            ),
        }
        gates["gameplay_same_frame_product_and_paint_presence_agree"] = {
            "pass": int(summary.get("same_frame_oracle_rows", 0)) > 0
            and int(summary.get("same_frame_oracle_downstream_defect_rows", 0)) == 0,
            "downstream_defect_rows": int(
                summary.get("same_frame_oracle_downstream_defect_rows", 0)
            ),
            "downstream_counts": summary.get(
                "same_frame_oracle_downstream_counts", {}
            ),
        }
        oracle_blocking_gates = [
            "evidence_same_frame_oracle_spatial_rows_valid",
            "evidence_same_frame_oracle_query_complete",
            "gameplay_same_frame_geometry_agrees",
            "gameplay_same_frame_product_and_paint_presence_agree",
        ]
        if selected_oracle_profile == FULL_GRID_TEMPORAL_ORACLE_PROFILE:
            terminal_reprojection = summary.get(
                "terminal_expected_reprojection", {}
            )
            if not isinstance(terminal_reprojection, dict):
                terminal_reprojection = {}
            gates["evidence_celestial_time_fixed"] = {
                "pass": bool(
                    summary.get(
                        "same_frame_oracle_celestial_runtime_witness_valid", False
                    )
                ),
                "day_cycle_time": summary.get(
                    "same_frame_oracle_day_cycle_time"
                ),
                "runtime_witness_by_frame": summary.get(
                    "same_frame_oracle_celestial_witness_by_frame", {}
                ),
                "invalid_rows": summary.get(
                    "same_frame_oracle_celestial_invalid_rows", 0
                ),
                "inconsistent_frames": summary.get(
                    "same_frame_oracle_celestial_inconsistent_frames", []
                ),
                # Named conjuncts, so a reader can separate a MOVING witness
                # (real temporal defect) from a frozen witness that does not
                # equal the requested pin (producer/configuration fact).
                "witness_frozen": summary.get(
                    "same_frame_oracle_celestial_witness_frozen", False
                ),
                "witness_matches_pin": summary.get(
                    "same_frame_oracle_celestial_witness_matches_pin", False
                ),
                "invalid_reasons": summary.get(
                    "same_frame_oracle_celestial_witness_invalid_reasons", []
                ),
                "scope": (
                    "separate day/night authority required for temporal "
                    "Product colour and terminal-position proof"
                ),
            }
            gates["evidence_terminal_expected_reprojection_valid"] = {
                "pass": bool(terminal_reprojection.get("valid", False)),
                "counts": terminal_reprojection.get("counts", {}),
                "invalid": terminal_reprojection.get("invalid", {}),
                "excluded": terminal_reprojection.get("excluded", {}),
                "scope": (
                    "cross-frame fixed frame-A world point to independently "
                    "located b43 whole-mask displacement"
                ),
            }
            gates["gameplay_terminal_expected_reprojection_bounded"] = {
                # Retained as a named diagnostic for historical receipts only.
                # The retired centroid-threshold lane is never an acceptance owner.
                "pass": False,
                "baseline_calibrated": bool(
                    terminal_reprojection.get("baseline_calibrated", False)
                ),
                "acceptance_threshold_authorized": bool(
                    terminal_reprojection.get(
                        "acceptance_threshold_authorized", False
                    )
                ),
                "max_residual_px": float(
                    terminal_reprojection.get("max_residual_px", 0.0)
                ),
                "reason": "retired_non_acceptance_centroid_diagnostic_exact_cell_owns_positional_gate",
            }
            gates["gameplay_terminal_expected_cell_address_exact"] = {
                "pass": bool(terminal_reprojection.get("valid", False))
                and bool(
                    terminal_reprojection.get("expected_cell_address_pass", False)
                ),
                "comparable_expected_cells": int(
                    terminal_reprojection.get("counts", {}).get(
                        "comparable_expected_cells", 0
                    )
                ),
                "expected_cell_mismatches": int(
                    terminal_reprojection.get("counts", {}).get(
                        "expected_cell_mismatches", 0
                    )
                ),
                "scope": (
                    "exact analytically projected ANSI-cell address versus "
                    "independently observed b43 Product-token occurrence"
                ),
            }
            gates["evidence_full_grid_temporal_oracle_valid"] = {
                "pass": bool(summary.get("full_grid_temporal_oracle_valid", False)),
                "invalid_reasons": summary.get(
                    "same_frame_oracle_temporal_invalid_reasons", []
                ),
                "expected_subcells_per_frame": summary.get(
                    "same_frame_oracle_expected_subcells_per_frame", []
                ),
                "emitted_subcells_per_frame": summary.get(
                    "same_frame_oracle_rows_per_frame", {}
                ),
                "duplicate_join_coordinates": summary.get(
                    "same_frame_oracle_duplicate_subcells_per_frame", {}
                ),
            }
            gates["evidence_full_grid_identity_normalized"] = {
                "pass": int(
                    summary.get("full_grid_temporal_oracle_normalization_invalid_rows", 0)
                ) == 0,
                "normalization_invalid_rows": int(
                    summary.get("full_grid_temporal_oracle_normalization_invalid_rows", 0)
                ),
                "owner_flow_matrix": summary.get(
                    "full_grid_temporal_oracle_owner_flow_matrix", {}
                ),
                "mismatch_decomposition": summary.get(
                    "full_grid_temporal_oracle_mismatch_decomposition", {}
                ),
            }
            gates["evidence_full_grid_rows_classified"] = {
                "pass": int(
                    summary.get("full_grid_temporal_oracle_unclassified_subcells", 0)
                ) == 0,
                "unclassified_subcells": int(
                    summary.get("full_grid_temporal_oracle_unclassified_subcells", 0)
                ),
            }
            gates["evidence_full_grid_raster_witness_valid"] = {
                "pass": int(summary.get(
                    "full_grid_temporal_oracle_raster_witness_invalid_rows", 0
                )) == 0 and int(summary.get(
                    "full_grid_temporal_oracle_source_physical_join_invalid_rows", 0
                )) == 0,
                "raster_witness_invalid_rows": int(summary.get(
                    "full_grid_temporal_oracle_raster_witness_invalid_rows", 0
                )),
                "source_physical_join_invalid_rows": int(summary.get(
                    "full_grid_temporal_oracle_source_physical_join_invalid_rows", 0
                )),
            }
            gates["evidence_full_grid_downstream_exact_identity"] = {
                "pass": bool(summary.get(
                    "full_grid_temporal_oracle_product_exact_identity_available",
                    False,
                )) and bool(summary.get(
                    "full_grid_temporal_oracle_complete_glyph_mask_hash_available",
                    False,
                )),
                "product_exact_identity_available": bool(summary.get(
                    "full_grid_temporal_oracle_product_exact_identity_available",
                    False,
                )),
                "complete_glyph_mask_hash_available": bool(summary.get(
                    "full_grid_temporal_oracle_complete_glyph_mask_hash_available",
                    False,
                )),
            }
            gates["evidence_full_grid_exact_current_ray_differences_reported"] = {
                "pass": bool(summary.get(
                    "full_grid_temporal_oracle_geometry_decomposition_valid",
                    False,
                )),
                "geometry_error_rows": int(
                    summary.get("full_grid_temporal_oracle_geometry_error_rows", 0)
                ),
                "large_components": summary.get(
                    "full_grid_temporal_oracle_large_components", []
                ),
            }
            gates["gameplay_full_grid_zero_structural_geometry_errors"] = {
                "pass": int(summary.get(
                    "full_grid_temporal_oracle_structural_geometry_error_rows", 0
                )) == 0,
                "structural_geometry_error_rows": int(summary.get(
                    "full_grid_temporal_oracle_structural_geometry_error_rows", 0
                )),
                "finite_sample_substitution_rows": int(summary.get(
                    "full_grid_temporal_oracle_finite_sample_substitution_rows", 0
                )),
                "finite_splat_admissible_rows": int(summary.get(
                    "full_grid_temporal_oracle_finite_splat_admissible_rows", 0
                )),
                "finite_splat_inadmissible_rows": int(summary.get(
                    "full_grid_temporal_oracle_finite_splat_inadmissible_rows", 0
                )),
                "finite_splat_admissibility_reasons": summary.get(
                    "full_grid_temporal_oracle_finite_splat_admissibility_reasons",
                    {},
                ),
                "finite_splat_decomposition_valid": bool(summary.get(
                    "full_grid_temporal_oracle_finite_splat_decomposition_valid",
                    False,
                )),
                "first_divergence": summary.get(
                    "full_grid_temporal_oracle_structural_first_divergence", {}
                ),
                "scope": (
                    "presence plus owner/object/material geometry and every "
                    "finite primitive substitution that fails exact source, "
                    "projected coverage, depth, or contender predicates"
                ),
                "oracle_reference_law": ORACLE_REFERENCE_LAW_STRICT,
            }
            # ---- FL-4359 continuity-law variants -------------------------
            # WHY EACH VARIANT EXISTS. The Aug-23 rollback (commit 691e654fd)
            # acted on an over-strict exact-primitive-identity oracle: a
            # working renderer scored ~54k "geometry errors" per capture
            # purely because a retained splat lands on a neighbouring triangle
            # of the SAME continuous surface, and the FL-4359 WHERE law
            # re-addresses terrain on purpose while keeping visibility
            # ray-exact. The strict gate above stays computed and reported as
            # the tolerance-zero control; these variants measure the same rows
            # under the criterion the runtime itself admits with. Only one of
            # the two sets is blocking, chosen by --oracle-reference-law.
            gates["gameplay_full_grid_zero_structural_geometry_errors_continuity"] = {
                "pass": (
                    int(summary.get(
                        "full_grid_temporal_oracle_continuity_defect_rows", 0
                    )) == 0
                    and bool(summary.get(
                        "full_grid_temporal_oracle_continuity_controls_valid",
                        False,
                    ))
                ),
                "continuity_defect_rows": int(summary.get(
                    "full_grid_temporal_oracle_continuity_defect_rows", 0
                )),
                "continuity_defect_reasons": summary.get(
                    "full_grid_temporal_oracle_continuity_defect_reasons", {}
                ),
                "continuity_by_design_rows": int(summary.get(
                    "full_grid_temporal_oracle_continuity_by_design_rows", 0
                )),
                "where_reanchor_rows": int(summary.get(
                    "full_grid_temporal_oracle_continuity_where_reanchor_rows", 0
                )),
                "where_reanchor_classes": summary.get(
                    "full_grid_temporal_oracle_continuity_where_reanchor_classes",
                    {},
                ),
                "retained_substitution_rows": int(summary.get(
                    "full_grid_temporal_oracle_continuity_retained_substitution_rows",
                    0,
                )),
                "current_ray_resample_rows": int(summary.get(
                    "full_grid_temporal_oracle_continuity_current_ray_resample_rows",
                    0,
                )),
                "strict_structural_geometry_error_rows": int(summary.get(
                    "full_grid_temporal_oracle_structural_geometry_error_rows", 0
                )),
                "controls_valid": bool(summary.get(
                    "full_grid_temporal_oracle_continuity_controls_valid", False
                )),
                "oracle_reference_law": ORACLE_REFERENCE_LAW_CONTINUITY,
                "scope": (
                    "presence plus owner/object/material geometry and every "
                    "substitution whose surface fails the signed-normal, "
                    "point-to-plane and along-ray bands of "
                    "fl4359_retained_surface_continuous; primitive_id is not "
                    "compared and a by-design WHERE re-address is not a defect"
                ),
            }
            gates["evidence_full_grid_continuity_law_controls_valid"] = {
                "pass": bool(summary.get(
                    "full_grid_temporal_oracle_continuity_controls_valid", False
                )),
                **summary.get(
                    "full_grid_temporal_oracle_continuity_controls", {}
                ),
                "reference_owner": summary.get(
                    "full_grid_temporal_oracle_reference_owner", "unproven"
                ),
                "reference_owner_counts": summary.get(
                    "full_grid_temporal_oracle_reference_owner_counts", {}
                ),
                "answer_source_epoch_counts": summary.get(
                    "full_grid_temporal_oracle_answer_source_epoch_counts", {}
                ),
                "tolerance": summary.get(
                    "full_grid_temporal_oracle_continuity_tolerance", {}
                ),
            }
            gates["gameplay_same_frame_geometry_agrees_continuity"] = {
                "pass": int(summary.get(
                    "full_grid_temporal_oracle_continuity_defect_rows", 0
                )) == 0,
                "continuity_defect_rows": int(summary.get(
                    "full_grid_temporal_oracle_continuity_defect_rows", 0
                )),
                "strict_geometry_defect_rows": int(summary.get(
                    "same_frame_oracle_geometry_defect_rows", 0
                )),
                "continuity_classification": summary.get(
                    "full_grid_temporal_oracle_continuity_classification", {}
                ),
                "oracle_reference_law": ORACLE_REFERENCE_LAW_CONTINUITY,
            }
            gates["gameplay_full_grid_zero_changing_reference_state_continuity"] = {
                "pass": (
                    int(summary.get(
                        "full_grid_temporal_oracle_continuity_changing_reference_key_state_pairs",
                        0,
                    )) == 0
                    and int(summary.get(
                        "full_grid_temporal_oracle_source_probe_set_changes", 0
                    )) == 0
                ),
                "comparable_reference_key_pairs": int(summary.get(
                    "full_grid_temporal_oracle_comparable_reference_key_pairs", 0
                )),
                "changing_reference_key_state_pairs_continuity": int(summary.get(
                    "full_grid_temporal_oracle_continuity_changing_reference_key_state_pairs",
                    0,
                )),
                "reference_identity_set_changes_continuity": int(summary.get(
                    "full_grid_temporal_oracle_continuity_reference_identity_set_changes",
                    0,
                )),
                "reference_defect_state_changes_continuity": int(summary.get(
                    "full_grid_temporal_oracle_continuity_reference_defect_state_changes",
                    0,
                )),
                "strict_changing_reference_key_state_pairs": int(summary.get(
                    "full_grid_temporal_oracle_changing_reference_key_state_pairs",
                    0,
                )),
                "source_probe_set_changes": int(summary.get(
                    "full_grid_temporal_oracle_source_probe_set_changes", 0
                )),
                "oracle_reference_law": ORACLE_REFERENCE_LAW_CONTINUITY,
            }
            gates["gameplay_full_grid_zero_changing_reference_state"] = {
                "pass": (
                    int(summary.get(
                        "full_grid_temporal_oracle_changing_reference_key_state_pairs", 0
                    )) == 0
                    and int(summary.get(
                        "full_grid_temporal_oracle_source_probe_set_changes", 0
                    )) == 0
                ),
                "comparable_reference_key_pairs": int(
                    summary.get(
                        "full_grid_temporal_oracle_comparable_reference_key_pairs", 0
                    )
                ),
                "changing_reference_key_state_pairs": int(
                    summary.get(
                        "full_grid_temporal_oracle_changing_reference_key_state_pairs", 0
                    )
                ),
                "source_probe_set_changes": int(
                    summary.get("full_grid_temporal_oracle_source_probe_set_changes", 0)
                ),
                "source_generation_set_flips": int(
                    summary.get("full_grid_temporal_oracle_source_generation_set_flips", 0)
                ),
            }
            gates["gameplay_full_grid_exact_product_translation"] = {
                "pass": bool(summary.get(
                    "full_grid_temporal_oracle_product_exact_identity_available",
                    False,
                )) and int(summary.get(
                    "full_grid_temporal_oracle_product_translation_error_rows", 0
                )) == 0,
                "exact_identity_available": bool(summary.get(
                    "full_grid_temporal_oracle_product_exact_identity_available",
                    False,
                )),
                "translation_error_rows": int(summary.get(
                    "full_grid_temporal_oracle_product_translation_error_rows", 0
                )),
            }
            gates["gameplay_full_grid_zero_glyph_mask_splits"] = {
                "pass": int(
                    summary.get("full_grid_temporal_oracle_multi_glyph_mask_cells", 0)
                ) == 0 and int(
                    summary.get("full_grid_temporal_oracle_incomplete_glyph_mask_rows", 0)
                ) == 0,
                "multi_glyph_mask_cells": int(
                    summary.get("full_grid_temporal_oracle_multi_glyph_mask_cells", 0)
                ),
                "complete_glyph_mask_rows": int(summary.get(
                    "full_grid_temporal_oracle_complete_glyph_mask_rows", 0
                )),
                "incomplete_glyph_mask_rows": int(summary.get(
                    "full_grid_temporal_oracle_incomplete_glyph_mask_rows", 0
                )),
                "unavailable_glyph_mask_rows": int(summary.get(
                    "full_grid_temporal_oracle_unavailable_glyph_mask_rows", 0
                )),
            }
            gates["gameplay_full_grid_zero_duplicate_out_of_place"] = {
                "pass": int(
                    summary.get(
                        "full_grid_temporal_oracle_structural_duplicate_components",
                        0,
                    )
                ) == 0
                and int(
                    summary.get(
                        "full_grid_temporal_oracle_structural_out_of_place_identities",
                        0,
                    )
                ) == 0
                and int(
                    summary.get(
                        "full_grid_temporal_oracle_candidate_tuple_duplicate_components",
                        0,
                    )
                ) == 0,
                "exact_primitive_duplicate_components_diagnostic": int(
                    summary.get("full_grid_temporal_oracle_duplicate_components", 0)
                ),
                "exact_primitive_out_of_place_identities_diagnostic": int(
                    summary.get("full_grid_temporal_oracle_out_of_place_identities", 0)
                ),
                "structural_duplicate_components": int(summary.get(
                    "full_grid_temporal_oracle_structural_duplicate_components", 0
                )),
                "structural_out_of_place_identities": int(summary.get(
                    "full_grid_temporal_oracle_structural_out_of_place_identities", 0
                )),
                "candidate_tuple_duplicate_components": int(summary.get(
                    "full_grid_temporal_oracle_candidate_tuple_duplicate_components", 0
                )),
                "component_lifetimes": summary.get(
                    "full_grid_temporal_oracle_component_lifetimes", []
                ),
            }
            gates["evidence_full_grid_first_divergence_known"] = {
                "pass": int(
                    summary.get("full_grid_temporal_oracle_first_divergence_unknown", 0)
                ) == 0,
                "unknown": int(
                    summary.get("full_grid_temporal_oracle_first_divergence_unknown", 0)
                ),
                "counts": summary.get(
                    "full_grid_temporal_oracle_first_divergence", {}
                ),
            }
            oracle_blocking_gates.extend(
                [
                    "evidence_same_frame_oracle_exhaustive_frame_coverage",
                    "evidence_same_frame_oracle_temporal_continuity",
                    "evidence_full_grid_temporal_oracle_valid",
                    "evidence_full_grid_identity_normalized",
                    "evidence_full_grid_rows_classified",
                    "evidence_full_grid_raster_witness_valid",
                    "evidence_full_grid_downstream_exact_identity",
                    "evidence_full_grid_exact_current_ray_differences_reported",
                    "gameplay_full_grid_zero_structural_geometry_errors",
                    "gameplay_full_grid_zero_changing_reference_state",
                    "gameplay_full_grid_exact_product_translation",
                    "gameplay_full_grid_zero_glyph_mask_splits",
                    "gameplay_cell_mask_owner_stability_bounded",
                    "gameplay_full_grid_zero_duplicate_out_of_place",
                    "evidence_full_grid_first_divergence_known",
                    "evidence_presentation_observer_population_accounted",
                    # Structural geometry agreement cannot promote a moving
                    # renderer whose projected image still jitters, whose
                    # content still shimmers, or whose exact Product colour
                    # changes at a stable authored sample.  These symptom
                    # gates are independent acceptance owners, not diagnostic
                    # annotations on the geometry oracle.
                    "gameplay_moving_camera_jitter_bounded",
                    "gameplay_content_shimmer_bounded",
                    "gameplay_continuous_product_colour_stability_bounded",
                    "evidence_terminal_expected_reprojection_valid",
                    "evidence_celestial_time_fixed",
                    "gameplay_terminal_expected_cell_address_exact",
                    # Whole-cell ASCII position has a threshold-free owner:
                    # the analytically expected ANSI address must equal the
                    # independently observed b43 address.  R_AB remains
                    # diagnostic until canon defines a residual calibration
                    # law; an unauthored threshold must not own acceptance.
                    "evidence_product_owner_name_transport_valid",
                    "evidence_terrain_product_homogeneity_present",
                    "evidence_terrain_product_final_paint_join_complete",
                    "gameplay_terrain_product_distribution_non_degenerate",
                    "evidence_terrain_product_homogeneity_baseline_calibrated",
                    "evidence_product_admission_contingency_valid",
                    "gameplay_product_admission_chain_exact",
                    # FL-4775 observer-power law: presentation-space greens
                    # cannot count while the cohort distribution is
                    # degenerate or exclusions are undecomposed.
                    "evidence_presentation_observer_sensitivity_valid",
                ]
            )
            # ---- Oracle reference law selection ---------------------------
            # ENUMERATED FROM SOURCE. Only these three blocking gates read the
            # oracle's exact-primitive-identity verdict, so only these three
            # have continuity variants:
            #   gameplay_full_grid_zero_structural_geometry_errors
            #       -> _oracle_structural_geometry_error()
            #   gameplay_same_frame_geometry_agrees
            #       -> same_frame_oracle_geometry_defect_rows
            #   gameplay_full_grid_zero_changing_reference_state
            #       -> per-key `defect` and exact selected-identity sets
            #
            # DELIBERATELY WITHOUT A VARIANT, with the reason each one is red:
            #   gameplay_full_grid_zero_duplicate_out_of_place — already grades
            #     footprints in the owner/object/material domain
            #     (_oracle_footprint_movement_counts(semantic_identity=True)),
            #     so primitive strictness is not what makes it red;
            #   evidence_full_grid_downstream_exact_identity — red because the
            #     capture publishes no expected complete-glyph-mask hash
            #     (complete_glyph_mask_hash_available false), a transport gap;
            #   evidence_terminal_expected_reprojection_valid and
            #     gameplay_terminal_expected_cell_address_exact — red on the
            #     terminal atlas mask digest and on an empty comparable
            #     population, neither of which is an oracle identity verdict;
            #   evidence_full_grid_temporal_oracle_valid,
            #     evidence_same_frame_oracle_temporal_continuity and
            #     evidence_celestial_time_fixed — red only on the celestial
            #     witness conjunct;
            #   the terrain/presentation gates — FL-4775 observer power.
            # Relaxing the reference law can therefore never turn those red.
            oracle_reference_law = str(
                getattr(args, "oracle_reference_law", ORACLE_REFERENCE_LAW_STRICT)
                or ORACLE_REFERENCE_LAW_STRICT
            )
            gates["oracle_reference_law"] = oracle_reference_law
            if oracle_reference_law == ORACLE_REFERENCE_LAW_CONTINUITY:
                continuity_substitutions = {
                    "gameplay_full_grid_zero_structural_geometry_errors":
                        "gameplay_full_grid_zero_structural_geometry_errors_continuity",
                    "gameplay_same_frame_geometry_agrees":
                        "gameplay_same_frame_geometry_agrees_continuity",
                    "gameplay_full_grid_zero_changing_reference_state":
                        "gameplay_full_grid_zero_changing_reference_state_continuity",
                }
                oracle_blocking_gates = [
                    continuity_substitutions.get(name, name)
                    for name in oracle_blocking_gates
                ]
                # The controls gate blocks only under the continuity law: it is
                # what stops the relaxed law from grading its own output.
                oracle_blocking_gates.append(
                    "evidence_full_grid_continuity_law_controls_valid"
                )
    if isinstance(sunday_comparison, dict) and sunday_comparison:
        gates["gameplay_current_reduces_sunday_final_cell_instability"] = {
            "pass": bool(sunday_comparison.get("pass", False)),
            **sunday_comparison,
        }
    # FL-4775 observer-power law (third-recurrence lock): on a
    # presentation-degenerate cohort, equality of glyph/FG/BG/mask bytes is
    # non-evidence for temporal correctness. Inequality may falsify
    # determinism; equality cannot validate it (July 10 grey-slab
    # equifinality; FL-4775 proof-sensitivity scope; r4 colour-green
    # recurrence). While the terrain Product distribution gate is red or
    # unevaluated, every presentation-space stability gate is demoted to
    # falsifier-only and this blocking gate stays red, so no acceptance
    # package can cite a presentation-space green.
    presentation_space_stability_gates = (
        "gameplay_continuous_product_colour_stability_bounded",
        "gameplay_content_shimmer_bounded",
        "gameplay_cell_mask_owner_stability_bounded",
    )
    distribution_green = terrain_presentation_distribution_admissible
    demoted_presentation_gates = []
    evaluated_presentation_gates = []
    for presentation_gate_name in presentation_space_stability_gates:
        presentation_gate = gates.get(presentation_gate_name)
        if not isinstance(presentation_gate, dict):
            continue
        evaluated_presentation_gates.append(presentation_gate_name)
        presentation_gate["observer_power"] = (
            "acceptance_candidate"
            if distribution_green
            else "falsifier_only_presentation_degenerate"
        )
        if not distribution_green:
            demoted_presentation_gates.append(presentation_gate_name)
    if evaluated_presentation_gates:
        gates["evidence_presentation_observer_sensitivity_valid"] = {
            "pass": (
                distribution_green
                and presentation_observer_population_accounting_valid
            ),
            "distribution_gate_green": distribution_green,
            "same_pose_baseline_calibrated": (
                terrain_homogeneity_baseline_calibrated
            ),
            "population_accounting_valid": (
                presentation_observer_population_accounting_valid
            ),
            "presentation_space_gates": evaluated_presentation_gates,
            "demoted_gates": demoted_presentation_gates,
            "scope": (
                "On a presentation-degenerate cohort, glyph/FG/BG/mask "
                "equality is non-evidence: inequality may falsify "
                "determinism, equality cannot validate it. Closure requires "
                "a green terrain Product distribution gate plus complete "
                "decomposition of colour-oracle exclusions, or an "
                "identity/address-space oracle."
            ),
        }
    expected_red_gates = set(getattr(args, "expected_red_gate", []) or [])
    proof_profile = str(getattr(args, "proof_profile", "general") or "general")
    general_blocking_gates = [
        name for name, gate in gates.items()
        if isinstance(gate, dict) and name not in {"failed_gates", "expected_red_gates", "blocking_failed_gates"}
    ]
    airplane_blocking_gates = [
        "scenario_moving_camera_continuous",
        "evidence_required_fields_present",
        "evidence_comparable_cells_present",
        "evidence_projection_contract_valid",
        "evidence_projection_fields_present",
        "evidence_jitter_capture_present",
        "evidence_product_time_constant",
        "gameplay_moving_camera_jitter_bounded",
        "evidence_presentation_observer_population_accounted",
        "gameplay_content_shimmer_bounded",
        "gameplay_cell_mask_owner_stability_bounded",
        "gameplay_surface_identity_bounce_bounded",
        # FL-4775 observer-power law: shimmer/mask greens are non-evidence
        # on a degenerate or degeneracy-unmeasured cohort.
        "evidence_presentation_observer_sensitivity_valid",
    ]
    # Any moving/temporal proof is invalid after an authority snapshot drop,
    # even when the visual route and sparse readback completed.
    if proof_profile == "airplane_temporal_v1":
        gates["evidence_temporal_authority_tick_continuity"] = {
            "pass": bool(
                summary.get("same_frame_oracle_authority_tick_continuity_valid", False)
            ),
            "scope": "all_temporal_claims",
            "reason": (
                "runtime receipt must report zero FL-4265 authority tick drops"
            ),
        }
        airplane_blocking_gates.append("evidence_temporal_authority_tick_continuity")
    if proof_profile == "airplane_temporal_v1":
        blocking_profile_gates = airplane_blocking_gates
    elif proof_profile in {"same_frame_oracle_v1", FULL_GRID_TEMPORAL_ORACLE_PROFILE}:
        blocking_profile_gates = oracle_blocking_gates
    else:
        blocking_profile_gates = general_blocking_gates
    failed_gates = [
        name
        for name, gate in gates.items()
        if isinstance(gate, dict) and not bool(gate.get("pass"))
    ]
    blocking_failed_gates = [
        name for name in failed_gates
        if name in blocking_profile_gates and name not in expected_red_gates
    ]
    profile_failed_gates = [name for name in failed_gates if name in blocking_profile_gates]
    diagnostic_failed_gates = [name for name in failed_gates if name not in blocking_profile_gates]
    gates["failed_gates"] = failed_gates
    gates["expected_red_gates"] = sorted(expected_red_gates)
    gates["blocking_failed_gates"] = blocking_failed_gates
    gates["proof_profile"] = proof_profile
    gates["blocking_profile_gates"] = blocking_profile_gates
    gates["diagnostic_failed_gates"] = diagnostic_failed_gates
    gates["profile_failed_gates"] = profile_failed_gates
    gates["jitter_metric_owner"] = jitter_metric_owner
    gates["snap_metric_applicable"] = snap_metric_applicable
    gates["all_pass"] = len(profile_failed_gates) == 0
    gates["all_non_expected_pass"] = len(blocking_failed_gates) == 0
    return gates


def _summary_final_instability(summary: dict) -> float:
    if "motion_compensated_final_cell_instability_pct" in summary:
        return float(summary.get("motion_compensated_final_cell_instability_pct", 0.0))
    if "max_original_shimmer_pct" in summary:
        return float(summary.get("max_original_shimmer_pct", 0.0))
    attr = summary.get("original_shimmer_attribution", {})
    if isinstance(attr, dict):
        return float(attr.get("max_original_shimmer_pct", 0.0))
    return float(summary.get("max_pair_jitter_pct", 0.0))


def build_sunday_comparison(current_summary: dict, sunday_summary: dict, args: argparse.Namespace) -> dict:
    current_pct = _summary_final_instability(current_summary)
    sunday_pct = _summary_final_instability(sunday_summary)
    improvement_pct = sunday_pct - current_pct
    ratio = (current_pct / sunday_pct) if sunday_pct > 0.0 else (0.0 if current_pct == 0.0 else float("inf"))
    materially_lower = (
        sunday_pct > 0.0
        and improvement_pct >= args.min_current_vs_sunday_improvement_pct
        and ratio <= args.max_current_vs_sunday_instability_ratio
    )
    return {
        "pass": materially_lower,
        "current_motion_compensated_final_cell_instability_pct": current_pct,
        "sunday_motion_compensated_final_cell_instability_pct": sunday_pct,
        "improvement_pct": improvement_pct,
        "current_vs_sunday_instability_ratio": ratio,
        "max_current_vs_sunday_instability_ratio": args.max_current_vs_sunday_instability_ratio,
        "min_current_vs_sunday_improvement_pct": args.min_current_vs_sunday_improvement_pct,
    }


def main():
    parser = argparse.ArgumentParser(description="FL-4359 moving-camera jitter/shimmer analyzer")
    parser.add_argument("path", help="Path to JSONL capture file")
    parser.add_argument("--frame-a", type=int, default=None, help="Targeted diagnostic frame ID for A")
    parser.add_argument("--frame-b", type=int, default=None, help="Targeted diagnostic frame ID for B")
    parser.add_argument("--all-frames", action="store_true", help="Compatibility flag; all consecutive frame pairs are the default")
    parser.add_argument("--summary-json", default="", help="Write machine-readable shimmer summary JSON")
    parser.add_argument(
        "--defect-map-jsonl",
        default="",
        help=(
            "Write one exhaustive machine-attribution row for every geometry "
            "or glyph-mask defect. The summary records its SHA-256."
        ),
    )
    parser.add_argument("--max-snap-px-delta", type=float, default=1.0, help="Camera jitter gate limit in native px per frame")
    parser.add_argument("--max-snap-sign-flip-rate", type=float, default=0.05, help="Camera jitter gate limit for residual-axis sign flips")
    parser.add_argument("--max-content-jitter-pct", type=float, default=5.0, help="Content shimmer gate limit as percent of comparable cells")
    parser.add_argument("--max-selected-surface-identity-churn-pct", type=float, default=5.0, help="Surface identity churn gate limit")
    parser.add_argument("--max-post-surface-bounce-pct", type=float, default=12.0, help="Final Product surface-bounce gate limit")
    parser.add_argument(
        "--proof-profile",
        choices=[
            "general",
            "airplane_temporal_v1",
            "same_frame_oracle_v1",
            FULL_GRID_TEMPORAL_ORACLE_PROFILE,
        ],
        default="general",
        help="Blocking gate profile. Airplane captures exclude legacy scene-only diagnostics from proof closure.",
    )
    parser.add_argument(
        "--oracle-reference-law",
        choices=list(ORACLE_REFERENCE_LAWS),
        default=ORACLE_REFERENCE_LAW_STRICT,
        help=(
            "Which oracle reference law owns BLOCKING geometry gates. "
            "'strict' (default) keeps the historical exact-primitive-identity "
            "law. 'continuity_v1' makes the surface-continuity variants "
            "blocking instead, mirroring "
            "fl4359_retained_surface_continuous(). Both populations are always "
            "computed and reported; this flag only selects which one blocks."
        ),
    )
    parser.add_argument(
        "--runtime-receipt",
        default="",
        help=(
            "Current-run receipt used only for temporal authority-tick and "
            "build/source identity validation."
        ),
    )
    parser.add_argument("--sunday-summary-json", default="", help="Optional Sunday-path analyzer summary used for relative final-cell instability gating")
    parser.add_argument("--max-current-vs-sunday-instability-ratio", type=float, default=0.5, help="Current final-cell instability must be at most this fraction of Sunday when --sunday-summary-json is provided")
    parser.add_argument("--min-current-vs-sunday-improvement-pct", type=float, default=5.0, help="Current final-cell instability must improve over Sunday by at least this many percentage points")
    parser.add_argument("--min-moving-frame-pairs", type=int, default=4, help="Min frame pairs with real camera translation (moving-camera requirement)")
    parser.add_argument("--min-camera-motion-ratio", type=float, default=0.5, help="Min fraction of frame pairs that show camera translation")
    parser.add_argument("--min-comparable-cells", type=int, default=200, help="Min world-anchor-matched comparable cells per pair (false-green guard)")
    parser.add_argument("--details-limit", type=int, default=50, help="Max non-stable detail rows to retain per compared pair")
    parser.add_argument("--anchor-threshold", type=float, default=0.10, help="World-anchor distance threshold used when matching cells across frames")
    parser.add_argument("--fail-on-gate", action="store_true", help="Return non-zero when any named gate fails")
    parser.add_argument("--expected-red-gate", action="append", default=[],
                        help="Named gate allowed to fail for diagnostic runs; repeated flag allowed. "
                             "The gate remains FAIL in JSON, but --fail-on-gate ignores it for exit status.")
    parser.add_argument("--min-same-key-terrain-rows", type=int, default=50,
                        help="FL-4359 same-key proof: min terrain rows carrying source_key+terr_stable_cell "
                             "before the same-key flip diagnostic is trusted (fail-closed, Law 6)")
    args = parser.parse_args()

    if not Path(args.path).exists():
        print(f"Error: {args.path} not found", file=sys.stderr)
        sys.exit(1)

    rows = load_rows(args.path)
    if not rows:
        print("Error: no rows found in capture file", file=sys.stderr)
        sys.exit(1)

    frames = group_by_frame(rows)
    frame_ids = sorted(frames.keys())
    terminal_expected_reprojection = summarize_terminal_expected_reprojection(
        [frames[frame_id] for frame_id in frame_ids],
        details_limit=args.details_limit,
    )
    print(f"Loaded {len(rows)} rows across {len(frame_ids)} frames: {frame_ids}")
    print()
    pair_results: list[dict] = []

    pair_mode = args.frame_a is not None or args.frame_b is not None
    if pair_mode and (args.frame_a is None or args.frame_b is None):
        print("Error: --frame-a and --frame-b must be supplied together", file=sys.stderr)
        sys.exit(1)

    if not pair_mode:
        for i in range(len(frame_ids) - 1):
            fa = frames[frame_ids[i]]
            fb = frames[frame_ids[i + 1]]
            result = compare_frames(fa, fb, details_limit=args.details_limit, comparable_threshold=args.anchor_threshold)
            pair_results.append({
                "frame_a": frame_ids[i],
                "frame_b": frame_ids[i + 1],
                **result,
            })
            print(f"=== Frame {frame_ids[i]} -> {frame_ids[i+1]} ===")
            print(f"  Matched cells: {result['total_matched_cells']}")
            print(f"  Stable: {result['stable_pct']:.1f}%")
            print(f"  Jitter: {result['jitter_pct']:.1f}%")
            print(f"  Classifications: {result['classifications']}")
            if result["detail_rows"]:
                print(f"  Top jitter examples (first 5):")
                for d in result["detail_rows"][:5]:
                    print(f"    {d['cell_a']}->{d['cell_b']} {d['classification']}: "
                          f"owner {d['a_owner']}->{d['b_owner']} "
                          f"glyph {d['a_glyph']}->{d['b_glyph']} "
                          f"product {d['a_product_id']}->{d['b_product_id']} "
                          f"dist={d['anchor_dist']:.3f}")
            print()
    else:
        if args.frame_a not in frames:
            print(f"Error: frame {args.frame_a} not found", file=sys.stderr)
            sys.exit(1)
        if args.frame_b not in frames:
            print(f"Error: frame {args.frame_b} not found", file=sys.stderr)
            sys.exit(1)

        result = compare_frames(frames[args.frame_a], frames[args.frame_b], details_limit=args.details_limit, comparable_threshold=args.anchor_threshold)
        pair_results.append({
            "frame_a": args.frame_a,
            "frame_b": args.frame_b,
            **result,
        })
        print(f"=== Frame {args.frame_a} (A) -> Frame {args.frame_b} (B) ===")
        print(f"  Matched cells: {result['total_matched_cells']}")
        print(f"  Stable: {result['stable_pct']:.1f}%")
        print(f"  Jitter: {result['jitter_pct']:.1f}%")
        print(f"  Classifications: {result['classifications']}")
        print()
        if result["detail_rows"]:
            print("Jitter detail (first 20):")
            for d in result["detail_rows"][:20]:
                print(f"  {d['cell_a']}->{d['cell_b']} {d['classification']}: "
                      f"owner {d['a_owner']}->{d['b_owner']} "
                      f"glyph {d['a_glyph']}->{d['b_glyph']} "
                      f"product {d['a_product_id']}->{d['b_product_id']} "
                      f"dist={d['anchor_dist']:.3f}")
                print(f"    anchor A={d['a_anchor']} B={d['b_anchor']}")

    # Summary
    print()
    print("Classification guide:")
    print("  stable                       - No change (good)")
    print("  different_world_sample       - Cell sees a different world point (expected during scroll)")
    print("  terrain_sample_key_drift     - Same world area, terrain cell changed (BUG: shader sample key)")
    print("  owner_instability            - Same world area, owner kind changed (BUG: splat winner hysteresis)")
    print("  product_payload_instability  - Same world area, same owner, product id changed (BUG: payload drift)")
    print("  water_phase_camera_drift     - Same world area, hidden water phase changed (diagnostic unless final paint changes)")
    print("  terrain_product_detail_churn - Same world area, terrain detail fact changed (BUG: terrain product)")
    print("  foliage_product_payload_instability - Same world area, foliage product facts changed (animated lane)")
    print("  flower_product_payload_instability - Same world area, flower product facts changed (animated lane)")
    print("  actor_projection_snap        - Same world area, actor rect jumped (BUG: CPU projection snap)")
    print("  dynamic_water_animation      - Same water product changed while its captured Product clock advanced")
    print("  dynamic_cloud_shadow_animation - Same product, cloud shadow phase/color changed")
    print("  final_paint_or_glyph_instability - Same product, glyph/color/ink changed (BUG: final paint)")
    print()
    print("Coverage:")
    print("  terrain: world anchor from r15.yz (terr_sub_world_xz)")
    print("  water:   world anchor from r15.yz (receiver_world.xz); water_phase from r3.z")
    print("  actor:   world anchor from r15.yz (foot world XZ); actor_rect from actor_cell_tex")
    print("  sky/mesh: no world anchor (skipped — cannot match across frames)")

    shimmer = summarize_shimmer_metrics(frames)
    fixed_screen_temporal = summarize_fixed_screen_temporal(frames)
    retained_plate_mechanics = summarize_retained_plate_mechanics(frames)
    foliage = summarize_foliage_metrics(frames)
    same_key = summarize_same_key_flips(frames)
    mountain_material = summarize_mountain_material_metrics(rows)
    terrain_product_homogeneity = summarize_terrain_product_homogeneity_metrics(rows)
    terrain_product_compiler_distribution = (
        summarize_terrain_product_compiler_distribution(rows)
    )
    product_admission_contingency = summarize_product_admission_contingency(rows)
    product_owner_name_transport = summarize_product_owner_name_transport(rows)
    runtime_receipt = _load_runtime_receipt(args.runtime_receipt)
    same_frame_oracle = summarize_same_frame_oracle(
        rows,
        details_limit=args.details_limit,
        runtime_receipt=runtime_receipt,
    )
    full_grid_temporal_oracle = summarize_full_grid_temporal_oracle(
        rows,
        runtime_receipt=runtime_receipt,
        details_limit=args.details_limit,
    )
    product_anchor_provenance = summarize_product_anchor_provenance(rows)
    water_splat_winner_hidden_as_sky_count = sum(
        1
        for row in rows
        if bool(row.get("water_splat_winner_hidden_as_sky", False))
    )
    final_product_source_key_count = sum(
        1 for row in rows if _has_row_value(row, "final_product_source_key")
    )
    max_pair_jitter_pct = max((float(r.get("jitter_pct", 0.0)) for r in pair_results), default=0.0)
    max_selected_surface_identity_churn_pct = max((float(r.get("selected_surface_identity_churn_pct", 0.0)) for r in pair_results), default=0.0)
    legacy_max_categorical_anchor_post_surface_bounce_pct = max(
        (float(r.get("post_surface_bounce_pct", 0.0)) for r in pair_results),
        default=0.0,
    )
    max_final_product_identity_churn_pct = max((float(r.get("final_product_identity_churn_pct", 0.0)) for r in pair_results), default=0.0)
    max_cloud_shadow_only_pct = max((float(r.get("cloud_shadow_only_pct", 0.0)) for r in pair_results), default=0.0)
    max_water_phase_churn_pct = max((float(r.get("water_phase_churn_pct", 0.0)) for r in pair_results), default=0.0)
    max_weather_effect_paint_pct = max((float(r.get("weather_effect_paint_pct", 0.0)) for r in pair_results), default=0.0)
    legacy_max_categorical_anchor_signature_churn_pct = max(
        (float(r.get("final_cell_signature_churn_pct", 0.0)) for r in pair_results),
        default=0.0,
    )
    legacy_total_categorical_anchor_signature_churn_cells = sum(
        int(r.get("motion_compensated_final_cell_instability_cells", 0))
        for r in pair_results
    )
    max_screen_position_residual_world = max((float(r.get("screen_position_residual_world_max", 0.0)) for r in pair_results), default=0.0)
    total_anchor_residual = sum(
        float(r.get("screen_position_residual_world_avg", 0.0)) * int(r.get("comparable_cells", 0))
        for r in pair_results
    )
    surface_identity_split_totals: Counter = Counter()
    original_shimmer_class_totals: Counter = Counter()
    original_shimmer_surface_totals: Counter = Counter()
    original_shimmer_class_surface_totals: Counter = Counter()
    authored_animation_excluded_totals: Counter = Counter()
    raw_excluded_totals: Counter = Counter()
    surface_identity_surface_totals: dict[str, Counter] = {}
    final_signature_field_totals: Counter = Counter()
    final_signature_field_surface_totals: Counter = Counter()
    terrain_detail_field_totals: Counter = Counter()
    terrain_detail_field_surface_totals: Counter = Counter()
    selected_surface_identity_field_totals: Counter = Counter()
    selected_surface_identity_field_surface_totals: Counter = Counter()
    snap_phase_surface_totals: Counter = Counter()
    snap_phase_changed_cells = 0
    cell_mask_diagnostic_comparable_cells = 0
    cell_mask_winner_key_field_totals: Counter = Counter()
    cell_mask_winner_key_field_surface_totals: Counter = Counter()
    cell_mask_winner_priority_transition_totals: Counter = Counter()
    cell_mask_glyph_change_priority_transition_totals: Counter = Counter()
    cell_mask_glyph_change_priority_transition_surface_totals: Counter = Counter()
    cell_mask_glyph_change_mechanism_totals: Counter = Counter()
    cell_mask_glyph_change_mechanism_surface_totals: Counter = Counter()
    cell_mask_glyph_change_key_field_transition_totals: Counter = Counter()
    cell_mask_owner_stability_totals: Counter = Counter()
    cell_mask_owner_invalid_totals: Counter = Counter()
    cell_mask_owner_max_instability_pct = 0.0
    cell_mask_owner_pair_count = 0
    cell_mask_owner_population_accounting_valid = bool(pair_results)
    continuous_colour_totals: Counter = Counter()
    continuous_colour_invalid_rows = 0
    continuous_colour_invalid_witness_rows = 0
    continuous_colour_invalid_finalization_rows = 0
    continuous_colour_examples: list[dict] = []
    continuous_colour_max_instability_pct = 0.0
    continuous_colour_pair_count = 0
    continuous_colour_population_accounting_valid = bool(pair_results)
    original_shimmer_population_accounting_valid = bool(pair_results)
    for result in pair_results:
        split_diag = result.get("surface_identity_split_diagnostics", {})
        if isinstance(split_diag, dict):
            surface_identity_split_totals.update(split_diag.get("counts", {}))
            for split_name, surface_counts in split_diag.get("surface_counts", {}).items():
                if split_name not in surface_identity_surface_totals:
                    surface_identity_surface_totals[split_name] = Counter()
                surface_identity_surface_totals[split_name].update(surface_counts)
        final_attr = result.get("final_signature_churn_attribution", {})
        if isinstance(final_attr, dict):
            final_signature_field_totals.update(final_attr.get("field_counts", {}))
            final_signature_field_surface_totals.update(final_attr.get("field_surface_counts", {}))
            terrain_detail_field_totals.update(final_attr.get("terrain_detail_field_counts", {}))
            terrain_detail_field_surface_totals.update(final_attr.get("terrain_detail_field_surface_counts", {}))
            selected_surface_identity_field_totals.update(final_attr.get("selected_surface_identity_field_counts", {}))
            selected_surface_identity_field_surface_totals.update(final_attr.get("selected_surface_identity_field_surface_counts", {}))
            snap_phase_surface_totals.update(final_attr.get("snap_phase_surface_counts", {}))
            snap_phase_changed_cells += int(final_attr.get("snap_phase_changed_cells", 0))
        cell_mask_diag = result.get("cell_mask_winner_decomposition", {})
        if isinstance(cell_mask_diag, dict):
            cell_mask_diagnostic_comparable_cells += int(
                cell_mask_diag.get("diagnostic_comparable_cells", 0)
            )
            cell_mask_winner_key_field_totals.update(
                cell_mask_diag.get("winner_key_field_counts", {})
            )
            cell_mask_winner_key_field_surface_totals.update(
                cell_mask_diag.get("winner_key_field_surface_counts", {})
            )
            cell_mask_winner_priority_transition_totals.update(
                cell_mask_diag.get("winner_priority_transition_counts", {})
            )
            cell_mask_glyph_change_priority_transition_totals.update(
                cell_mask_diag.get("glyph_change_priority_transition_counts", {})
            )
            cell_mask_glyph_change_priority_transition_surface_totals.update(
                cell_mask_diag.get(
                    "glyph_change_priority_transition_surface_counts", {}
                )
            )
            cell_mask_glyph_change_mechanism_totals.update(
                cell_mask_diag.get("glyph_change_mechanism_counts", {})
            )
            cell_mask_glyph_change_mechanism_surface_totals.update(
                cell_mask_diag.get("glyph_change_mechanism_surface_counts", {})
            )
            cell_mask_glyph_change_key_field_transition_totals.update(
                cell_mask_diag.get(
                    "glyph_change_key_field_transition_counts", {}
                )
            )
        owner_stability = result.get("cell_mask_owner_stability", {})
        if isinstance(owner_stability, dict):
            cell_mask_owner_population_accounting_valid = (
                cell_mask_owner_population_accounting_valid
                and owner_stability.get("population_accounting_valid") is True
            )
            cell_mask_owner_stability_totals.update(owner_stability.get("counts", {}))
            cell_mask_owner_invalid_totals.update(
                {
                    f"frame_a:{key}": value
                    for key, value in owner_stability.get("invalid_cells_a", {}).items()
                }
            )
            cell_mask_owner_invalid_totals.update(
                {
                    f"frame_b:{key}": value
                    for key, value in owner_stability.get("invalid_cells_b", {}).items()
                }
            )
            cell_mask_owner_max_instability_pct = max(
                cell_mask_owner_max_instability_pct,
                float(owner_stability.get("instability_pct", 0.0)),
            )
            if int(owner_stability.get("counts", {}).get("comparable_cells", 0)) > 0:
                cell_mask_owner_pair_count += 1
        colour_stability = result.get("continuous_product_colour_stability", {})
        if isinstance(colour_stability, dict):
            continuous_colour_population_accounting_valid = (
                continuous_colour_population_accounting_valid
                and colour_stability.get("population_accounting_valid") is True
            )
            continuous_colour_totals.update(colour_stability.get("counts", {}))
            continuous_colour_invalid_rows += int(
                colour_stability.get("invalid_terrain_rows_a", 0)
            ) + int(colour_stability.get("invalid_terrain_rows_b", 0))
            continuous_colour_invalid_witness_rows += int(
                colour_stability.get("invalid_colour_witness_rows_a", 0)
            ) + int(colour_stability.get("invalid_colour_witness_rows_b", 0))
            continuous_colour_invalid_finalization_rows += int(
                colour_stability.get("invalid_finalization_witness_rows_a", 0)
            ) + int(
                colour_stability.get("invalid_finalization_witness_rows_b", 0)
            )
            for example in colour_stability.get("examples", []):
                if len(continuous_colour_examples) >= args.details_limit:
                    break
                continuous_colour_examples.append(example)
            continuous_colour_max_instability_pct = max(
                continuous_colour_max_instability_pct,
                float(colour_stability.get("instability_pct", 0.0)),
            )
            if int(
                colour_stability.get("counts", {}).get(
                    "comparable_stable_semantic_samples", 0
                )
            ) > 0:
                continuous_colour_pair_count += 1
        original_shimmer_class_totals.update(result.get("original_shimmer_class_counts", {}))
        original_shimmer_surface_totals.update(result.get("original_shimmer_surface_counts", {}))
        original_shimmer_class_surface_totals.update(result.get("original_shimmer_class_surface_counts", {}))
        authored_animation_excluded_totals.update(result.get("authored_animation_excluded_class_counts", {}))
        pair_raw_excluded = result.get("raw_excluded_counts", {}) or {}
        raw_excluded_totals.update(
            {
                key: value
                for key, value in pair_raw_excluded.items()
                if key != "population_accounting_valid"
            }
        )
        original_shimmer_population_accounting_valid = (
            original_shimmer_population_accounting_valid
            and pair_raw_excluded.get("population_accounting_valid") is True
        )
    total_original_shimmer_cells = sum(int(r.get("original_shimmer_cells", 0)) for r in pair_results)
    max_original_shimmer_pct = max((float(r.get("original_shimmer_pct", 0.0)) for r in pair_results), default=0.0)
    surface_bounce_owner_counts = {
        key: int(surface_identity_split_totals.get(key, 0))
        for key in SURFACE_BOUNCE_OWNER_KEYS
    }
    dominant_surface_bounce_owner = "none"
    if surface_bounce_owner_counts:
        dominant_surface_bounce_owner = max(
            surface_bounce_owner_counts.items(),
            key=lambda item: item[1],
        )[0]
        if surface_bounce_owner_counts.get(dominant_surface_bounce_owner, 0) <= 0:
            dominant_surface_bounce_owner = "none"
    def _field_counts_for_surface(field_surface_counts: Counter, surface_prefix: str) -> dict:
        totals: Counter = Counter()
        for key, count in field_surface_counts.items():
            if ":" not in key:
                continue
            field, surface_name = key.split(":", 1)
            if surface_name.startswith(surface_prefix):
                totals[field] += int(count)
        return dict(totals)

    def _split_cells_for_surface(split_name: str, surface_prefix: str) -> int:
        counts = surface_identity_surface_totals.get(split_name, Counter())
        return sum(int(count) for surface_name, count in counts.items() if surface_name.startswith(surface_prefix))

    surface_identity_split_surface_totals = {
        split_name: dict(surface_counts)
        for split_name, surface_counts in surface_identity_surface_totals.items()
    }
    cell_mask_count_keys = (
        "final_product_glyph_changed_cells",
        "cell_mask_glyph_changed_cells",
        "cell_mask_winner_key_changed_cells",
        "cell_mask_same_winner_key_glyph_changed_cells",
        "cell_mask_winner_priority_changed_cells",
        "cell_mask_winner_slot_changed_cells",
        "cell_mask_winner_provenance_unavailable_cells",
        "discrete_glyph_or_mask_changed_cells",
        "camera_dependent_colour_only_cells",
        "cell_mask_winner_relation_available_cells",
        "cell_mask_winner_matches_observed_product_both_cells",
        "cell_mask_winner_differs_observed_product_a_cells",
        "cell_mask_winner_differs_observed_product_b_cells",
        "cell_mask_winner_observed_product_relation_changed_cells",
        "cell_mask_key_changed_with_sibling_winner_cells",
        "cell_mask_key_changed_with_observed_product_winner_cells",
        "cell_mask_winner_relation_unavailable_cells",
    )
    cell_mask_counts = {
        key: int(surface_identity_split_totals.get(key, 0))
        for key in cell_mask_count_keys
    }
    mountain_churn_attribution = {
        "final_cell_signature_churn_cells": _split_cells_for_surface("final_cell_signature_churn", "mountain:"),
        "selected_surface_identity_churn_cells": _split_cells_for_surface("selected_surface_identity_churn", "mountain:"),
        "final_product_identity_churn_cells": _split_cells_for_surface("final_product_identity_churn", "mountain:"),
        "final_product_cell_churn_cells": _split_cells_for_surface("final_product_cell_churn", "mountain:"),
        "final_signature_field_counts": _field_counts_for_surface(final_signature_field_surface_totals, "mountain:"),
        "terrain_detail_field_counts": _field_counts_for_surface(terrain_detail_field_surface_totals, "mountain:"),
        "selected_surface_identity_field_counts": _field_counts_for_surface(selected_surface_identity_field_surface_totals, "mountain:"),
        "snap_phase_changed_cells": sum(
            int(count) for surface_name, count in snap_phase_surface_totals.items()
            if surface_name.startswith("mountain:")
        ),
        "surface_counts": {
            split_name: {
                surface_name: int(count)
                for surface_name, count in surface_counts.items()
                if surface_name.startswith("mountain:")
            }
            for split_name, surface_counts in surface_identity_surface_totals.items()
        },
    }
    # FL-4359 false-green guard: track world-anchor-matched comparable cells so a
    # run with too few comparable cells (almost everything scrolled/unmatched)
    # cannot pass the shimmer gate vacuously.
    comparable_per_pair = [int(r.get("comparable_cells", 0)) for r in pair_results]
    nonzero_comparable_per_pair = [c for c in comparable_per_pair if c > 0]
    comparable_pairs_meeting_min = sum(1 for c in comparable_per_pair if c >= args.min_comparable_cells)
    total_comparable_cells = sum(comparable_per_pair)
    avg_screen_position_residual_world = (
        total_anchor_residual / float(total_comparable_cells)
    ) if total_comparable_cells > 0 else 0.0
    min_comparable_cells = min(comparable_per_pair) if comparable_per_pair else 0
    min_nonzero_comparable_cells = min(nonzero_comparable_per_pair) if nonzero_comparable_per_pair else 0
    required_comparable_pairs = min(len(pair_results), args.min_moving_frame_pairs)
    corrected_final_cell_instability_pct = max(
        cell_mask_owner_max_instability_pct,
        continuous_colour_max_instability_pct,
    )
    corrected_final_cell_instability_cells = int(
        cell_mask_owner_stability_totals.get("unstable_cells", 0)
    ) + int(continuous_colour_totals.get("colour_changed_samples", 0))
    corrected_final_cell_oracle_valid = (
        len(pair_results) > 0
        and cell_mask_owner_pair_count == len(pair_results)
        and continuous_colour_pair_count == len(pair_results)
        and cell_mask_owner_population_accounting_valid
        and continuous_colour_population_accounting_valid
        and original_shimmer_population_accounting_valid
        and sum(cell_mask_owner_invalid_totals.values()) == 0
        and continuous_colour_invalid_rows == 0
        and continuous_colour_invalid_witness_rows == 0
        and continuous_colour_invalid_finalization_rows == 0
    )
    continuous_colour_comparable_per_pair = [
        int(
            (result.get("continuous_product_colour_stability", {}) or {}).get(
                "counts", {}
            ).get("comparable_stable_semantic_samples", 0)
        )
        for result in pair_results
    ]
    continuous_colour_min_comparable_samples = min(
        continuous_colour_comparable_per_pair, default=0
    )
    corrected_post_surface_bounce_pct = max(
        max_final_product_identity_churn_pct,
        corrected_final_cell_instability_pct,
    )
    summary = {
        "path": str(args.path),
        "row_count": len(rows),
        "frame_count": len(frame_ids),
        "frame_ids": frame_ids,
        **fixed_screen_temporal,
        **retained_plate_mechanics,
        **product_owner_name_transport,
        "max_pair_jitter_pct": max_pair_jitter_pct,
        "total_comparable_cells": total_comparable_cells,
        "min_comparable_cells": min_comparable_cells,
        "min_nonzero_comparable_cells": min_nonzero_comparable_cells,
        "comparable_pair_count": len(nonzero_comparable_per_pair),
        "comparable_pairs_meeting_min": comparable_pairs_meeting_min,
        "required_comparable_pairs": required_comparable_pairs,
        "raw_excluded_counts": dict(raw_excluded_totals),
        "presentation_observer_population_accounting_valid": (
            cell_mask_owner_population_accounting_valid
            and continuous_colour_population_accounting_valid
            and original_shimmer_population_accounting_valid
        ),
        "presentation_observer_population_accounting": {
            "cell_mask_owner": cell_mask_owner_population_accounting_valid,
            "continuous_product_colour": (
                continuous_colour_population_accounting_valid
            ),
            "motion_compensated_content": (
                original_shimmer_population_accounting_valid
            ),
        },
        "water_splat_winner_hidden_as_sky_count": water_splat_winner_hidden_as_sky_count,
        "final_product_source_key_count": final_product_source_key_count,
        "product_anchor_provenance": product_anchor_provenance,
        "product_anchor_provenance_complete": bool(product_anchor_provenance.get("complete", False)),
        "surface_identity_split_diagnostics_present": len(pair_results) > 0,
        "surface_identity_split_totals": dict(surface_identity_split_totals),
        "surface_identity_split_surface_totals": surface_identity_split_surface_totals,
        "cell_mask_winner_decomposition": {
            "diagnostic_comparable_cells": cell_mask_diagnostic_comparable_cells,
            "counts": cell_mask_counts,
            "pct": {
                key.replace("_cells", "_pct"): (
                    float(value) / cell_mask_diagnostic_comparable_cells * 100.0
                    if cell_mask_diagnostic_comparable_cells > 0 else 0.0
                )
                for key, value in cell_mask_counts.items()
            },
            "surface_counts": {
                name: dict(surface_identity_surface_totals.get(name, {}))
                for name in (
                    "final_product_glyph_change",
                    "cell_mask_glyph_change",
                    "cell_mask_winner_key_change",
                    "cell_mask_same_winner_key_glyph_change",
                    "cell_mask_winner_priority_change",
                    "cell_mask_winner_slot_change",
                    "cell_mask_winner_provenance_unavailable",
                    "discrete_glyph_or_mask_change",
                    "camera_dependent_colour_only",
                )
                if surface_identity_surface_totals.get(name)
            },
            "winner_key_field_counts": dict(cell_mask_winner_key_field_totals),
            "winner_key_field_surface_counts": dict(
                cell_mask_winner_key_field_surface_totals
            ),
            "winner_priority_transition_counts": dict(
                cell_mask_winner_priority_transition_totals
            ),
            "glyph_change_priority_transition_counts": dict(
                cell_mask_glyph_change_priority_transition_totals
            ),
            "glyph_change_priority_transition_surface_counts": dict(
                cell_mask_glyph_change_priority_transition_surface_totals
            ),
            "glyph_change_mechanism_counts": dict(
                cell_mask_glyph_change_mechanism_totals
            ),
            "glyph_change_mechanism_surface_counts": dict(
                cell_mask_glyph_change_mechanism_surface_totals
            ),
            "glyph_change_key_field_transition_counts": dict(
                cell_mask_glyph_change_key_field_transition_totals
            ),
            "colour_classification_scope": (
                "Diagnostic separation of quantized Product fg/bg response from "
                "discrete Product-glyph and cell-mask-election changes."
            ),
        },
        "cell_mask_candidate_membership": {
            "scope": "historical four-quarter replay",
            "valid": False,
            "invalid_reason": "current_capture_missing_cell_owned_center_nominee_token",
            "counts": {},
            "excluded_reasons": {},
            "causal_limit": (
                "The current cell owner may elect a center-sampled nominee that "
                "is not one of the four b55 quarter rows. Prior r32/r33 membership "
                "counts are invalid and cannot identify the churn owner."
            ),
        },
        "cell_mask_owner_stability": {
            "scope": (
                "One b43 whole-cell mask per ANSI cell, matched by the elected "
                "semantic anchor; quarter-slot winners use the elected b55 "
                "Product anchor."
            ),
            "counts": dict(cell_mask_owner_stability_totals),
            "invalid_reasons": dict(cell_mask_owner_invalid_totals),
            "invalid_cell_count": sum(cell_mask_owner_invalid_totals.values()),
            "comparable_pair_count": cell_mask_owner_pair_count,
            "max_instability_pct": cell_mask_owner_max_instability_pct,
            "population_accounting_valid": (
                cell_mask_owner_population_accounting_valid
            ),
        },
        "continuous_product_colour_stability": {
            "scope": (
                "Terrain Product FG/BG compared only for byte-identical 8x8 "
                "continuous paint samples and stable Product semantic state."
            ),
            "counts": dict(continuous_colour_totals),
            "invalid_terrain_row_count": continuous_colour_invalid_rows,
            "invalid_colour_witness_row_count": (
                continuous_colour_invalid_witness_rows
            ),
            "invalid_finalization_witness_row_count": (
                continuous_colour_invalid_finalization_rows
            ),
            "comparable_pair_count": continuous_colour_pair_count,
            "min_comparable_samples_per_pair": (
                continuous_colour_min_comparable_samples
            ),
            "max_instability_pct": continuous_colour_max_instability_pct,
            "population_accounting_valid": (
                continuous_colour_population_accounting_valid
            ),
            "first_divergence_order": list(
                PRODUCT_TERRAIN_COLOUR_WITNESS_STAGES
            ) + [
                "product_record_construction",
                "product_finalization",
                "product_staging_publication",
            ],
            "examples": continuous_colour_examples,
        },
        "corrected_final_cell_oracle_valid": corrected_final_cell_oracle_valid,
        "max_selected_surface_identity_churn_pct": max_selected_surface_identity_churn_pct,
        "legacy_categorical_anchor_max_post_surface_bounce_pct": (
            legacy_max_categorical_anchor_post_surface_bounce_pct
        ),
        "max_post_surface_bounce_pct": corrected_post_surface_bounce_pct,
        "max_final_product_identity_churn_pct": max_final_product_identity_churn_pct,
        "max_cloud_shadow_only_pct": max_cloud_shadow_only_pct,
        "max_water_phase_churn_pct": max_water_phase_churn_pct,
        "max_weather_effect_paint_pct": max_weather_effect_paint_pct,
        "legacy_categorical_anchor_final_cell_signature_churn_cells": (
            legacy_total_categorical_anchor_signature_churn_cells
        ),
        "legacy_categorical_anchor_final_cell_signature_churn_pct": (
            legacy_max_categorical_anchor_signature_churn_pct
        ),
        "final_cell_signature_churn_cells": corrected_final_cell_instability_cells,
        "final_cell_signature_churn_pct": corrected_final_cell_instability_pct,
        "motion_compensated_final_cell_instability_cells": (
            corrected_final_cell_instability_cells
        ),
        "motion_compensated_final_cell_instability_pct": (
            corrected_final_cell_instability_pct
        ),
        "screen_position_residual_world_avg": avg_screen_position_residual_world,
        "screen_position_residual_world_max": max_screen_position_residual_world,
        "original_shimmer_attribution": {
            "measured": len(pair_results) > 0 and total_comparable_cells > 0,
            "max_original_shimmer_pct": max_original_shimmer_pct,
            "total_original_shimmer_cells": total_original_shimmer_cells,
            "raw_excluded_counts": dict(raw_excluded_totals),
            "class_counts": dict(original_shimmer_class_totals),
            "surface_counts": dict(original_shimmer_surface_totals),
            "class_surface_counts": dict(original_shimmer_class_surface_totals),
            "authored_animation_excluded_cells": sum(authored_animation_excluded_totals.values()),
            "authored_animation_excluded_class_counts": dict(authored_animation_excluded_totals),
            "same_key_semantic_flip_observed": bool(same_key.get("same_key_semantic_flip_observed", False)),
            "same_key_semantic_flip_pct": float(same_key.get("same_key_semantic_flip_pct", 0.0)),
            "same_key_cross_frame_flip_pairs": int(same_key.get("same_key_cross_frame_flip_pairs", 0)),
            "same_key_cross_frame_pairs": int(same_key.get("same_key_cross_frame_pairs", 0)),
            "same_key_per_field_flip_counts": same_key.get("same_key_per_field_flip_counts", {}),
        },
        "surface_identity_bounce_attribution": {
            "max_selected_surface_identity_churn_pct": max_selected_surface_identity_churn_pct,
            "max_post_surface_bounce_pct": corrected_post_surface_bounce_pct,
            "max_final_product_identity_churn_pct": max_final_product_identity_churn_pct,
            "max_final_cell_signature_churn_pct": corrected_final_cell_instability_pct,
            "max_water_phase_churn_pct": max_water_phase_churn_pct,
            "owner_counts": surface_bounce_owner_counts,
            "dominant_owner": dominant_surface_bounce_owner,
            "all_split_counts": dict(surface_identity_split_totals),
            "surface_counts": {
                split_name: dict(surface_counts)
                for split_name, surface_counts in surface_identity_surface_totals.items()
            },
        },
        "final_signature_churn_attribution": {
            "field_counts": dict(final_signature_field_totals),
            "field_surface_counts": dict(final_signature_field_surface_totals),
            "terrain_detail_field_counts": dict(terrain_detail_field_totals),
            "terrain_detail_field_surface_counts": dict(terrain_detail_field_surface_totals),
            "selected_surface_identity_field_counts": dict(selected_surface_identity_field_totals),
            "selected_surface_identity_field_surface_counts": dict(selected_surface_identity_field_surface_totals),
            "snap_phase_changed_cells": snap_phase_changed_cells,
            "snap_phase_surface_counts": dict(snap_phase_surface_totals),
        },
        "mountain_churn_attribution": mountain_churn_attribution,
        "pair_count": len(pair_results),
        "pair_results": pair_results,
        "terminal_expected_reprojection": terminal_expected_reprojection,
        **shimmer,
        **foliage,
        **same_key,
        **mountain_material,
        **terrain_product_homogeneity,
        **terrain_product_compiler_distribution,
        "product_admission_contingency": product_admission_contingency,
        **same_frame_oracle,
        **full_grid_temporal_oracle,
    }
    if args.sunday_summary_json:
        with open(args.sunday_summary_json, "r", encoding="utf-8") as f:
            sunday_summary = json.load(f)
        summary["sunday_summary_path"] = args.sunday_summary_json
        summary["sunday_comparison"] = build_sunday_comparison(summary, sunday_summary, args)
    summary["presented_surface_stability"] = summarize_presented_surface_stability(rows)
    print("Presented-surface diagnostic: " + json.dumps(summary["presented_surface_stability"]))
    summary["required_field_coverage"] = summarize_required_field_coverage(rows, summary)
    gates = build_gate_summary(summary, args)
    summary["gates"] = gates
    summary["proof_profile"] = args.proof_profile
    summary["runtime_receipt_path"] = args.runtime_receipt
    if args.defect_map_jsonl:
        defect_map_path = Path(args.defect_map_jsonl)
        defect_map_path.parent.mkdir(parents=True, exist_ok=True)
        defect_rows = full_grid_oracle_defect_map_rows(rows)
        with defect_map_path.open("w", encoding="utf-8") as handle:
            for defect_row in defect_rows:
                handle.write(json.dumps(defect_row, sort_keys=True) + "\n")
        defect_bytes = defect_map_path.read_bytes()
        summary["full_grid_temporal_oracle_defect_map_path"] = str(
            defect_map_path
        )
        summary["full_grid_temporal_oracle_defect_map_sha256"] = (
            hashlib.sha256(defect_bytes).hexdigest()
        )
        summary["full_grid_temporal_oracle_defect_map_bytes"] = len(defect_bytes)
        summary["full_grid_temporal_oracle_defect_map_rows"] = len(defect_rows)
        summary["full_grid_temporal_oracle_defect_map_complete"] = (
            len(defect_rows)
            >= int(summary.get("full_grid_temporal_oracle_geometry_error_rows", 0))
        )
    print()
    print("Shimmer metrics:")
    print(f"  Max pair jitter: {max_pair_jitter_pct:.2f}%")
    print(f"  Max snap residual delta: {summary['max_snap_px_delta']:.3f}px")
    print(f"  Avg snap residual delta: {summary['avg_snap_px_delta']:.3f}px")
    print(f"  Snap residual sign flips: {summary['snap_sign_flip_count']} / {summary['snap_sign_flip_axis_samples']} "
          f"({summary['snap_sign_flip_rate']:.3f})")
    original_attr = summary.get("original_shimmer_attribution", {})
    print("Original FL-4359 shimmer attribution:")
    print(f"  Max original shimmer: {original_attr.get('max_original_shimmer_pct', 0.0):.2f}%")
    print(f"  Total original shimmer cells: {original_attr.get('total_original_shimmer_cells', 0)}")
    print(f"  Raw/excluded counts: {original_attr.get('raw_excluded_counts', {})}")
    print(f"  Original shimmer class counts: {original_attr.get('class_counts', {})}")
    print(f"  Authored animation excluded: {original_attr.get('authored_animation_excluded_class_counts', {})}")
    print("Motion-compensated final-cell instability:")
    print(f"  Max final-cell signature churn: {summary['motion_compensated_final_cell_instability_pct']:.2f}%")
    print(f"  Final-cell churn cells: {summary['motion_compensated_final_cell_instability_cells']}")
    print(f"  Screen-position residual avg/max world: {summary['screen_position_residual_world_avg']:.4f} / {summary['screen_position_residual_world_max']:.4f}")
    print("Retained probe plate mechanics:")
    print(f"  Source-key component max/median/p90 cells: "
          f"{summary['retained_plate_component_max_size']} / "
          f"{summary['retained_plate_component_median_size']:.1f} / "
          f"{summary['retained_plate_component_p90_size']:.1f}")
    print(f"  Fixed-screen source-key flips: {summary['retained_plate_source_key_flip_cells']} / "
          f"{summary['retained_plate_temporal_comparable_screen_cells']} "
          f"({summary['retained_plate_source_key_flip_pct']:.2f}%)")
    print(f"  Stationary source-key flips: {summary['retained_plate_stationary_source_key_flip_cells']} / "
          f"{summary['retained_plate_stationary_world_anchor_cells']} "
          f"({summary['retained_plate_stationary_source_key_flip_pct']:.2f}%)")
    if summary["retained_plate_generation_capture_present"]:
        print(f"  Probe-generation flips: {summary['retained_plate_generation_flip_cells']} / "
              f"{summary['retained_plate_generation_comparable_cells']} "
              f"({summary['retained_plate_generation_flip_pct']:.2f}%)")
        print(f"  CURRENT/PREVIOUS flips on stationary anchors: "
              f"{summary['retained_plate_stationary_current_previous_generation_flip_cells']}")
    else:
        print("  Probe-generation flips: unmeasured; capture lacks per-cell generation provenance")
    print(f"  Generation-unmeasured cells (all/stationary): "
          f"{summary['retained_plate_generation_unmeasured_cells']} / "
          f"{summary['retained_plate_stationary_generation_unmeasured_cells']}")
    print(f"  CURRENT/PREVIOUS classified/unmeasured cells: "
          f"{summary['retained_plate_current_previous_classified_cells']} / "
          f"{summary['retained_plate_current_previous_unmeasured_cells']}")
    if summary["retained_plate_layer_evidence_present"]:
        print(f"  Layer-content evidence frames: "
              f"{summary['retained_plate_layer_evidence_frames']} / "
              f"{summary['retained_plate_layer_evidence_frame_count']}")
        print(f"  Layer-epoch survived/advanced/unmeasured cells: "
              f"{summary['retained_plate_layer_epoch_survived_cells']} / "
              f"{summary['retained_plate_layer_epoch_advanced_cells']} / "
              f"{summary['retained_plate_layer_epoch_unmeasured_cells']}")
        print(f"  Provenance change band-rotation/recaptured/reprojection: "
              f"{summary['retained_plate_provenance_change_band_rotation_cells']} / "
              f"{summary['retained_plate_provenance_change_layer_recaptured_cells']} / "
              f"{summary['retained_plate_provenance_change_retained_reprojection_cells']}")
        print(f"  Provenance change unmeasured cells: "
              f"{summary['retained_plate_provenance_change_unmeasured_cells']}")
    else:
        print("  Layer-content epochs: unmeasured; capture predates the "
              "layer-granular capture-lifecycle schema")
        print(f"  Provenance change unmeasured cells: "
              f"{summary['retained_plate_provenance_change_unmeasured_cells']}")
    print("Same-frame current-build direct-ray oracle:")
    print(
        "  Rows/structurally valid/query invalid: "
        f"{summary['same_frame_oracle_rows']} / "
        f"{summary['same_frame_oracle_structurally_valid_rows']} / "
        f"{summary['same_frame_oracle_query_invalid_rows']}"
    )
    print(
        "  Geometry classifications: "
        f"{summary['same_frame_oracle_classification_counts']}"
    )
    print(
        "  Product/final-paint presence classifications: "
        f"{summary['same_frame_oracle_downstream_counts']}"
    )
    print(
        "  Direct-vs-selected depth delta median/p90/max: "
        f"{summary['same_frame_oracle_depth_delta_median']:.6f} / "
        f"{summary['same_frame_oracle_depth_delta_p90']:.6f} / "
        f"{summary['same_frame_oracle_depth_delta_max']:.6f}"
    )
    if "full_grid_temporal_oracle_continuity_classification" in summary:
        # Both laws are printed side by side on purpose: a reader must be able
        # to see how much the continuity tolerance absorbs without opening the
        # summary JSON.
        print("  Oracle reference law (strict vs continuity_v1):")
        print(
            "    Strict structural geometry errors: "
            f"{summary.get('full_grid_temporal_oracle_structural_geometry_error_rows', 0)}"
        )
        print(
            "    Continuity real defects: "
            f"{summary.get('full_grid_temporal_oracle_continuity_defect_rows', 0)}"
        )
        print(
            "    Continuity classification: "
            f"{summary.get('full_grid_temporal_oracle_continuity_classification', {})}"
        )
        print(
            "    Continuity defect reasons: "
            f"{summary.get('full_grid_temporal_oracle_continuity_defect_reasons', {})}"
        )
        print(
            "    WHERE re-anchor classes: "
            f"{summary.get('full_grid_temporal_oracle_continuity_where_reanchor_classes', {})}"
        )
        print(
            "    Answer source epochs: "
            f"{summary.get('full_grid_temporal_oracle_answer_source_epoch_counts', {})}"
        )
        print(
            "    Reference owner: "
            f"{summary.get('full_grid_temporal_oracle_reference_owner', 'unproven')}"
        )
        print(
            "    Continuity controls: "
            f"{summary.get('full_grid_temporal_oracle_continuity_controls', {})}"
        )
    final_attr = summary.get("final_signature_churn_attribution", {})
    mountain_attr = summary.get("mountain_churn_attribution", {})
    print(f"  Final signature field counts: {final_attr.get('field_counts', {})}")
    print(f"  Terrain detail field counts: {final_attr.get('terrain_detail_field_counts', {})}")
    print(f"  Selected-surface identity field counts: {final_attr.get('selected_surface_identity_field_counts', {})}")
    print(f"  Snap phase changed cells: {final_attr.get('snap_phase_changed_cells', 0)}")
    print(f"  Mountain churn attribution: {mountain_attr}")
    if summary.get("sunday_comparison"):
        comparison = summary["sunday_comparison"]
        print("Sunday/current final-cell instability comparison:")
        print(f"  Sunday: {comparison['sunday_motion_compensated_final_cell_instability_pct']:.2f}%")
        print(f"  Current: {comparison['current_motion_compensated_final_cell_instability_pct']:.2f}%")
        print(f"  Ratio: {comparison['current_vs_sunday_instability_ratio']:.3f}")
        print(f"  Improvement: {comparison['improvement_pct']:.2f}%")
    print("Surface identity split diagnostics (FL-4359):")
    print(f"  Max selected-surface identity churn: {summary['max_selected_surface_identity_churn_pct']:.2f}%")
    print(f"  Max post-surface bounce: {summary['max_post_surface_bounce_pct']:.2f}%")
    print(f"  Max final Product identity churn: {summary['max_final_product_identity_churn_pct']:.2f}%")
    print(f"  Max cloud-shadow-only repaint: {summary['max_cloud_shadow_only_pct']:.2f}%")
    print(f"  Max water-phase churn: {summary['max_water_phase_churn_pct']:.2f}%")
    print(f"  Max weather/effect paint churn: {summary['max_weather_effect_paint_pct']:.2f}%")
    print(f"  Split totals: {summary['surface_identity_split_totals']}")
    print(f"  Surface totals: {summary['surface_identity_split_surface_totals']}")
    print("Foliage product metrics:")
    print(f"  Product rows: {summary['foliage_product_rows']}")
    print(f"  Role counts: {summary['foliage_role_counts']}")
    print(f"  Flower rows: {summary['flower_product_rows']}")
    print(f"  Flower rows with glyph+ink: {summary['flower_rows_with_glyph_and_final_ink']}")
    print("Same-key terrain semantic flips (FL-4359 Codex proof slice):")
    print(f"  Terrain rows w/ source_key+stable_cell: {summary['same_key_terrain_rows']}")
    print(f"  (source_key, stable_cell) frame-groups: {summary['same_key_frame_groups']}")
    print(f"  Intra-frame variant groups (race exposure): {summary['same_key_intra_frame_variant_groups']} "
          f"({summary['same_key_intra_frame_variant_pct']:.2f}%), max variant count={summary['same_key_max_intra_frame_variant_count']}")
    print(f"  Cross-frame same-key pairs: {summary['same_key_cross_frame_pairs']}, "
          f"with a field flip: {summary['same_key_cross_frame_flip_pairs']} ({summary['same_key_semantic_flip_pct']:.2f}%)")
    print(f"  Same-key semantic flip observed: {summary['same_key_semantic_flip_observed']}")
    _nonzero_fields = {k: v for k, v in summary["same_key_per_field_flip_counts"].items() if v}
    print(f"  Per-field flip counts (nonzero): {_nonzero_fields if _nonzero_fields else 'none'}")
    print("  INTERPRETATION: variant>1 or cross-frame flips => a same-key world-history")
    print("    writer would be nondeterministic / the key maps to multiple products.")
    print("    Zero flips => same-key terrain is already stable; hysteresis there is a")
    print("    no-op (Codex HIGH-3) and the semantic-history lane is NOT justified.")
    coverage = summary["required_field_coverage"]
    print("FL-4359 required-field coverage:")
    print(f"  All present: {coverage['all_present']}")
    print(f"  Present: {coverage['present']}")
    print(f"  Missing: {coverage['missing']}")
    print("Named gates:")
    for gate_name, gate in gates.items():
        if not isinstance(gate, dict):
            continue
        expected = gate_name in set(args.expected_red_gate or [])
        suffix = " (EXPECTED-RED)" if expected and not gate.get("pass") else ""
        print(f"  {gate_name}: {'PASS' if gate.get('pass') else 'FAIL'}{suffix}")
    if gates.get("blocking_failed_gates"):
        print(f"Blocking failed gates: {gates['blocking_failed_gates']}")
    if gates.get("expected_red_gates"):
        print(f"Expected-red gates: {gates['expected_red_gates']}")
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Summary JSON: {args.summary_json}")
    if args.fail_on_gate and not gates["all_non_expected_pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
