#!/usr/bin/env python3
"""Exact compiler/reference arithmetic for ``fl4512.primitive_field.v1``."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

SCORER_ID = "S_pf.v1"
ROUTE_KEY = "filled.field.v1"
MAX_Q16 = 65535
MAX_DISTANCE_SQUARED = 98
MAX_SCORE_NUM = 6_553_500
PRIMITIVE_REF_SCHEMA = "fl4512.primitive_ref.v1"
TARGET_SCHEMA = "fl4512.primitive_field.target.v1"
MASK8_LIMIT = 1 << 64


class PrimitiveFieldError(ValueError):
    pass


def _require_u16(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_Q16:
        raise PrimitiveFieldError(f"{field}_invalid")
    return value


def _require_u64(value: object, field: str, *, nonzero: bool = False) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value < MASK8_LIMIT
        or (nonzero and value == 0)
    ):
        raise PrimitiveFieldError(f"{field}_invalid")
    return value


def canonical_bytes(value: object) -> bytes:
    """Return the one JSON serialization used for target identity hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_primitive_ref(ref: Mapping[str, object]) -> dict[str, int | bool | str]:
    """Validate and canonically serialize one owner-clipped ``PrimitiveRef``.

    This is deliberately an offline compiler substrate.  A ref represents one
    already depth-resolved render primitive projected into a single 8-by-8
    cell.  The producer supplies only stable identity, owner identity, clipped
    coverage, region metadata, importance, visibility, and a quantized depth;
    it never selects a glyph.
    """
    if not isinstance(ref, Mapping):
        raise PrimitiveFieldError("primitive_ref_invalid")
    allowed = {
        "primitive_ref_id",
        "owner_surface_id",
        "occupancy_mask8",
        "region_class",
        "priority_u16",
        "depth_q16",
        "visible",
    }
    if set(ref) != allowed:
        raise PrimitiveFieldError("primitive_ref_schema_invalid")
    visible = ref["visible"]
    if not isinstance(visible, bool):
        raise PrimitiveFieldError("primitive_ref_visibility_invalid")
    occupancy = _require_u64(ref["occupancy_mask8"], "primitive_ref_mask")
    if occupancy == 0:
        raise PrimitiveFieldError("primitive_ref_mask8_empty")
    return {
        "depth_q16": _require_u16(ref["depth_q16"], "primitive_ref_depth"),
        "occupancy_mask8": occupancy,
        "owner_surface_id": _require_u64(ref["owner_surface_id"], "primitive_ref_owner", nonzero=True),
        "primitive_ref_id": _require_u64(ref["primitive_ref_id"], "primitive_ref_id", nonzero=True),
        "priority_u16": _require_u16(ref["priority_u16"], "primitive_ref_priority"),
        "region_class": _require_u16(ref["region_class"], "primitive_ref_region"),
        "visible": visible,
    }


def canonical_primitive_refs(refs: Sequence[Mapping[str, object]]) -> list[dict[str, int | bool | str]]:
    """Validate the frame-local ref list and impose stable identity order."""
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
        raise PrimitiveFieldError("primitive_refs_schema_invalid")
    canonical = [canonical_primitive_ref(ref) for ref in refs]
    canonical.sort(key=lambda item: int(item["primitive_ref_id"]))
    ids = [int(item["primitive_ref_id"]) for item in canonical]
    if len(ids) != len(set(ids)):
        raise PrimitiveFieldError("primitive_ref_duplicate_id")
    return canonical


def _mask_hex(mask: int) -> str:
    return int(mask).to_bytes(8, "little").hex()


def build_target(refs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Build the only filled-role target a future runtime producer may consume.

    At each occupied bit the nearer ``depth_q16`` wins; equal depth resolves
    to the lower stable ``primitive_ref_id``.  The resulting ownership map is
    then classified.  Empty visible coverage is ``not_claimed``.  Mixed
    winning region classes are ``region_conflict`` and intentionally produce
    no descriptor, so the caller falls through to tonal rather than guessing.
    """
    canonical = canonical_primitive_refs(refs)
    winners: list[dict[str, int | bool | str] | None] = [None] * 64
    for ref in canonical:
        if not bool(ref["visible"]):
            continue
        mask = int(ref["occupancy_mask8"])
        for bit_index in range(64):
            if not mask & (1 << bit_index):
                continue
            incumbent = winners[bit_index]
            candidate_key = (int(ref["depth_q16"]), int(ref["primitive_ref_id"]))
            if incumbent is None or candidate_key < (
                int(incumbent["depth_q16"]), int(incumbent["primitive_ref_id"])
            ):
                winners[bit_index] = ref

    occupancy = sum(1 << index for index, ref in enumerate(winners) if ref is not None)
    selected = sorted(
        {int(ref["primitive_ref_id"]) for ref in winners if ref is not None}
    )
    selected_refs = [ref for ref in canonical if int(ref["primitive_ref_id"]) in selected]
    common = {
        "primitive_ref_ids": selected,
        "primitive_ref_bundle_sha256": hashlib.sha256(canonical_bytes(selected_refs)).hexdigest(),
        "schema": TARGET_SCHEMA,
    }
    if occupancy == 0:
        return {**common, "reason": "empty_target", "state": "not_claimed"}

    regions = {int(ref["region_class"]) for ref in winners if ref is not None}
    if len(regions) != 1:
        return {
            **common,
            "occupancy_mask8_u64_le_hex": _mask_hex(occupancy),
            "reason": "region_conflict",
            "state": "region_conflict",
        }

    priority_sum = sum(int(ref["priority_u16"]) for ref in winners if ref is not None)
    target_priority = round_half_up(priority_sum, occupancy.bit_count())
    region_class = next(iter(regions))
    return {
        **common,
        **descriptor(occupancy, region_class),
        "occupancy_mask8_u64_le_hex": _mask_hex(occupancy),
        "state": "live",
        "target_priority_u16": target_priority,
    }


def round_half_up(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator <= 0:
        raise PrimitiveFieldError("invalid_round_domain")
    return (numerator + denominator // 2) // denominator


def boundary4(mask: int) -> int:
    """Return ``M & ~erode4(M)`` with outside-grid samples fixed to zero."""
    mask &= (1 << 64) - 1
    boundary = 0
    for y in range(8):
        for x in range(8):
            bit = 1 << (8 * y + x)
            if not mask & bit:
                continue
            interior = x > 0 and x < 7 and y > 0 and y < 7
            if interior:
                interior = all(mask & (1 << (8 * ny + nx)) for nx, ny in (
                    (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)
                ))
            if not interior:
                boundary |= bit
    return boundary


def _points(mask: int) -> list[tuple[int, int]]:
    return [(x, y) for y in range(8) for x in range(8) if mask & (1 << (8 * y + x))]


def symmetric_squared_euclidean_sum(a: int, b: int) -> int:
    pa, pb = _points(a), _points(b)
    if not pa or not pb:
        raise PrimitiveFieldError("empty_boundary")

    def directed(source: list[tuple[int, int]], target: list[tuple[int, int]]) -> int:
        return sum(min((x - u) ** 2 + (y - v) ** 2 for u, v in target) for x, y in source)

    return directed(pa, pb) + directed(pb, pa)


def detail_u16(mask: int) -> int:
    count = int(mask).bit_count()
    if count == 0:
        raise PrimitiveFieldError("mask8_empty")
    return round_half_up(MAX_Q16 * boundary4(mask).bit_count(), count)


def descriptor(mask: int, region_class: int) -> dict[str, int | str]:
    if not isinstance(region_class, int) or isinstance(region_class, bool) or not 0 <= region_class <= 65535:
        raise PrimitiveFieldError("region_class_invalid")
    if not mask:
        raise PrimitiveFieldError("mask8_empty")
    return {
        "mask8_u64_le_hex": int(mask).to_bytes(8, "little").hex(),
        "boundary8_u64_le_hex": boundary4(mask).to_bytes(8, "little").hex(),
        "filled_region_class": region_class,
        "detail_u16": detail_u16(mask),
    }


def score(target_mask: int, candidate_mask: int, target_region: int,
          candidate_region: int, target_priority_u16: int,
          candidate_detail_u16: int) -> dict[str, int | str]:
    if not target_mask or not candidate_mask:
        raise PrimitiveFieldError("mask8_empty")
    if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MAX_Q16
           for v in (target_region, candidate_region, target_priority_u16, candidate_detail_u16)):
        raise PrimitiveFieldError("field_out_of_range")
    target_boundary = boundary4(target_mask)
    candidate_boundary = boundary4(candidate_mask)
    d_occ = round_half_up(MAX_Q16 * (target_mask ^ candidate_mask).bit_count(), 64)
    chamfer_sum = symmetric_squared_euclidean_sum(target_boundary, candidate_boundary)
    d_sil = round_half_up(
        MAX_Q16 * chamfer_sum,
        MAX_DISTANCE_SQUARED * (target_boundary.bit_count() + candidate_boundary.bit_count()),
    )
    d_reg = 0 if target_region == candidate_region else MAX_Q16
    d_pri = abs(target_priority_u16 - candidate_detail_u16)
    score_num = 50 * d_occ + 25 * d_sil + 15 * d_reg + 10 * d_pri
    if not 0 <= score_num <= MAX_SCORE_NUM:
        raise PrimitiveFieldError("score_out_of_range")
    return {
        "scorer_id": SCORER_ID,
        "d_occ": d_occ,
        "d_sil": d_sil,
        "d_reg": d_reg,
        "d_pri": d_pri,
        "score_num": score_num,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True,
                        help="PrimitiveRef[] fixture to validate and compile")
    args = parser.parse_args()
    raw = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != "fl4512.primitive_field.fixture.v1"
        or raw.get("primitive_ref_schema") != PRIMITIVE_REF_SCHEMA
    ):
        raise PrimitiveFieldError("primitive_field_fixture_schema_invalid")
    refs = raw.get("primitive_refs")
    result = build_target(refs)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
