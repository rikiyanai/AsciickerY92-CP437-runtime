#!/usr/bin/env python3
"""Compile FL-4208 D2 C8 orientation-coset admission receipts.

The carrier for ``c8_raster_action.v1`` is a pinned 48x48 topology-cache
raster.  The measured 8x8 U7 mask is deliberately not used here: odd
45-degree rotations of a digitized 8x8 mask do not form a composable C8
action.  Each R_k is sampled once from the identity raster.

This is an offline producer.  It creates neither U4 rows nor runtime
authority; those consumers remain explicitly PROOF_OPEN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_VERSION = "c8_raster_action.v1"
RECEIPT_SCHEMA = "fl4208.topology_coset_receipt.v1"
TOPOLOGY_CACHE_SCHEMA = "fl4208.topology_cache.v1"
RASTER_SIZE = 48
T_IN = 0.02
T_OUT = 0.04
Q16 = 1 << 16

# k advances counter-clockwise in mathematical image coordinates after the
# y-axis inversion performed in _rotate_odd_from_identity.  The coefficients
# are Q16 fixed-point round-half-down values for cos/sin(k*pi/4).
_COS_Q16 = (Q16, 46340, 0, -46341, -Q16, -46341, 0, 46340)
_SIN_Q16 = (0, 46340, Q16, 46340, 0, -46341, -Q16, -46341)
_GROUPS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("C8", (0, 1, 2, 3, 4, 5, 6, 7)),
    ("C4", (0, 2, 4, 6)),
    ("C2", (0, 4)),
    ("C1", (0,)),
)


class CosetError(ValueError):
    """A structurally invalid cache or receipt input."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _floor_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return numerator // denominator


def _round_half_down_div(numerator: int, denominator: int) -> int:
    """Nearest integer with exact halves directed toward negative infinity."""
    q, r = divmod(numerator, denominator)
    twice = 2 * r
    if twice > denominator:
        return q + 1
    return q


def normalize_raster(raster: Sequence[Sequence[int] | str], *, size: int = RASTER_SIZE) -> list[list[int]]:
    if len(raster) != size:
        raise CosetError(f"raster must have {size} rows")
    normalized: list[list[int]] = []
    for y, source_row in enumerate(raster):
        if isinstance(source_row, str):
            if len(source_row) != size or any(ch not in "01" for ch in source_row):
                raise CosetError(f"raster row {y} must be {size} binary characters")
            row = [int(ch) for ch in source_row]
        else:
            if len(source_row) != size:
                raise CosetError(f"raster row {y} must have {size} entries")
            row = []
            for value in source_row:
                if int(value) not in (0, 1):
                    raise CosetError(f"raster row {y} is not binary")
                row.append(int(value))
        normalized.append(row)
    return normalized


def raster_rows(raster: Sequence[Sequence[int]]) -> list[str]:
    return ["".join(str(int(value)) for value in row) for row in raster]


def _rotate_even_exact(identity: Sequence[Sequence[int]], k: int) -> list[list[int]]:
    """Exact 90-degree lattice permutations for even C8 elements."""
    size = len(identity)
    quarter_turns = (k // 2) % 4
    if quarter_turns == 0:
        return [list(row) for row in identity]
    if quarter_turns == 1:
        return [[identity[size - 1 - x][y] for x in range(size)] for y in range(size)]
    if quarter_turns == 2:
        return [[identity[size - 1 - y][size - 1 - x] for x in range(size)] for y in range(size)]
    return [[identity[x][size - 1 - y] for x in range(size)] for y in range(size)]


def _sample_bilinear_q16(identity: Sequence[Sequence[int]], x_q16: int, y_q16: int) -> int:
    """Return fixed-point ink coverage on [0,Q16], outside samples are zero."""
    size = len(identity)
    x0 = _floor_div(x_q16, Q16)
    y0 = _floor_div(y_q16, Q16)
    fx = x_q16 - x0 * Q16
    fy = y_q16 - y0 * Q16
    coverage = 0
    for yy, wy in ((y0, Q16 - fy), (y0 + 1, fy)):
        if not 0 <= yy < size:
            continue
        for xx, wx in ((x0, Q16 - fx), (x0 + 1, fx)):
            if 0 <= xx < size and identity[yy][xx]:
                coverage += wx * wy
    # Exact halves become zero at the binary threshold: threshold=0.5 with
    # round-half-down, not a platform float comparison.
    return _round_half_down_div(coverage, Q16 * Q16)


def _rotate_odd_from_identity(identity: Sequence[Sequence[int]], k: int) -> list[list[int]]:
    """One fixed-point bilinear resample from identity, never a chained step."""
    size = len(identity)
    out = [[0] * size for _ in range(size)]
    center_q16 = ((size - 1) * Q16) // 2
    cos_q16 = _COS_Q16[k]
    sin_q16 = _SIN_Q16[k]
    for y in range(size):
        dy_q16 = y * Q16 - center_q16
        for x in range(size):
            dx_q16 = x * Q16 - center_q16
            # Image rows grow downward.  Invert y for the mathematical C8
            # action, perform the inverse lookup, then invert back.
            sx_q16 = center_q16 + _round_half_down_div(cos_q16 * dx_q16 + sin_q16 * dy_q16, Q16)
            sy_q16 = center_q16 + _round_half_down_div(-sin_q16 * dx_q16 + cos_q16 * dy_q16, Q16)
            out[y][x] = _sample_bilinear_q16(identity, sx_q16, sy_q16)
    return out


def rotate_identity_pose(identity: Sequence[Sequence[int] | str], k: int) -> list[list[int]]:
    """Pinned C8 action R_k(identity) on a 48x48 topology-cache raster."""
    grid = normalize_raster(identity)
    k %= 8
    return _rotate_even_exact(grid, k) if k % 2 == 0 else _rotate_odd_from_identity(grid, k)


def jaccard_distance(a: Sequence[Sequence[int]], b: Sequence[Sequence[int]]) -> float:
    intersection = 0
    union = 0
    for arow, brow in zip(a, b):
        for av, bv in zip(arow, brow):
            intersection += 1 if av and bv else 0
            union += 1 if av or bv else 0
    # Blank rasters are rejected before distance classification.  Defining this
    # value keeps the function total for receipt diagnostics.
    return 0.0 if union == 0 else 1.0 - (intersection / union)


def d_sym_per_k(identity: Sequence[Sequence[int] | str]) -> list[float]:
    grid = normalize_raster(identity)
    return [jaccard_distance(grid, rotate_identity_pose(grid, k)) for k in range(8)]


def largest_contained_stabilizer(distances: Sequence[float], *, t_in: float = T_IN) -> tuple[str, tuple[int, ...]]:
    if len(distances) != 8:
        raise CosetError("d_sym_per_k must contain exactly eight values")
    for name, members in _GROUPS:
        if all(distances[k] <= t_in for k in members):
            return name, members
    raise CosetError("identity element must satisfy d_sym(0)<=t_in")


def margin_state(distances: Sequence[float], members: Iterable[int], *, t_in: float = T_IN,
                 t_out: float = T_OUT) -> str:
    members_set = set(members)
    if any(distances[k] > t_in for k in members_set):
        return "member_threshold_failed"
    if any(t_in < distances[k] < t_out for k in range(8) if k not in members_set):
        return "unstable_stabilizer"
    if any(distances[k] < t_out for k in range(8) if k not in members_set):
        return "nonmember_threshold_failed"
    return "stable"


def mask_for_orientations(orientations: Iterable[int]) -> int:
    mask = 0
    for k in orientations:
        if not 0 <= int(k) < 8:
            raise CosetError("orientation must be in C8")
        mask |= 1 << int(k)
    return mask


def coset_members(phase: int, stabilizer: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted((int(phase) + int(member)) % 8 for member in stabilizer))


def coset_id(phase: int, stabilizer: Iterable[int]) -> int:
    return min(coset_members(phase, stabilizer))


def _is_union_of_cosets(mask: int, stabilizer: Iterable[int]) -> bool:
    members = tuple(stabilizer)
    remaining = {k for k in range(8) if mask & (1 << k)}
    while remaining:
        phase = min(remaining)
        coset = set(coset_members(phase, members))
        if not coset <= remaining:
            return False
        remaining -= coset
    return True


def _entry_record(entry: dict[str, Any], *, atlas_sha256: str, shape_catalog_sha256: str,
                  topology_cache_sha256: str) -> dict[str, Any]:
    identity = normalize_raster(entry["raster_rows"])
    ink_pixels = sum(sum(row) for row in identity)
    distances = d_sym_per_k(identity)
    stabilizer_class, stabilizer = largest_contained_stabilizer(distances)
    state = margin_state(distances, stabilizer)
    reviewed_mask = int(entry.get("reviewed_orientation_mask_u8", mask_for_orientations(stabilizer)))
    if not 0 <= reviewed_mask <= 0xFF:
        raise CosetError("reviewed_orientation_mask_u8 must be uint8")
    if not _is_union_of_cosets(reviewed_mask, stabilizer):
        raise CosetError("reviewed_orientation_mask_u8 is not a union of stabilizer cosets")
    phase = int(entry.get("authored_phase", 0)) % 8
    topology_match = bool(entry.get("topology_match", True))
    confidence_ok = bool(entry.get("confidence_ok", True))
    if ink_pixels == 0:
        state = "blank_glyph"
        rejection_cause: str | None = "unstable_stabilizer"
    elif state != "stable":
        rejection_cause = "unstable_stabilizer"
    elif not topology_match:
        rejection_cause = "topology_mismatch"
    elif not confidence_ok:
        rejection_cause = "confidence_below_threshold"
    elif reviewed_mask == 0:
        rejection_cause = "not_in_reviewed_coset"
    else:
        rejection_cause = None
    admitted = 0 if rejection_cause else reviewed_mask
    return {
        "glyph_id": int(entry["glyph_id"]),
        "unicode_scalar": int(entry.get("unicode_scalar", 0)),
        "topology_id": str(entry.get("topology_id", "unknown")),
        "beta0": int(entry.get("beta0", 0)),
        "beta1_raw": int(entry.get("beta1_raw", 0)),
        "junction_crossing_numbers": list(entry.get("junction_crossing_numbers", [])),
        "d_sym_per_k": distances,
        "t_in": T_IN,
        "t_out": T_OUT,
        "stabilizer_class": stabilizer_class,
        "stabilizer_mask_u8": mask_for_orientations(stabilizer),
        "margin_state": state,
        "action_version": ACTION_VERSION,
        "authored_phase": phase,
        "coset_id": coset_id(phase, stabilizer),
        "reviewed_orientation_mask_u8": reviewed_mask,
        "admitted_orientation_mask_u8": admitted,
        "rejection_cause": rejection_cause,
        "pool_insertions": [],
        "atlas_sha256": atlas_sha256,
        "shape_catalog_sha256": shape_catalog_sha256,
        "topology_cache_sha256": topology_cache_sha256,
    }


def build_receipt(topology_cache: dict[str, Any], *, atlas_sha256: str,
                  shape_catalog_sha256: str) -> dict[str, Any]:
    if topology_cache.get("schema") != TOPOLOGY_CACHE_SCHEMA:
        raise CosetError(f"topology cache schema must be {TOPOLOGY_CACHE_SCHEMA}")
    topology_cache_sha256 = sha256_json(topology_cache)
    entries = topology_cache.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CosetError("topology cache must contain nonempty entries")
    records = [_entry_record(entry, atlas_sha256=atlas_sha256,
                             shape_catalog_sha256=shape_catalog_sha256,
                             topology_cache_sha256=topology_cache_sha256)
               for entry in sorted(entries, key=lambda item: int(item["glyph_id"]))]
    return {
        "schema": RECEIPT_SCHEMA,
        "action_version": ACTION_VERSION,
        "topology_cache_schema": TOPOLOGY_CACHE_SCHEMA,
        "topology_cache_sha256": topology_cache_sha256,
        "atlas_sha256": atlas_sha256,
        "shape_catalog_sha256": shape_catalog_sha256,
        "t_in": T_IN,
        "t_out": T_OUT,
        "records": records,
        "runtime_consumption": "PROOF_OPEN",
        "pool_insertions": "PROOF_OPEN",
    }


def validate_receipt(receipt: dict[str, Any], topology_cache: dict[str, Any]) -> dict[str, int]:
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise CosetError("receipt schema mismatch")
    if receipt.get("action_version") != ACTION_VERSION:
        raise CosetError("receipt action_version mismatch")
    if receipt.get("t_in") != T_IN or receipt.get("t_out") != T_OUT:
        raise CosetError("receipt threshold mismatch")
    expected = build_receipt(topology_cache, atlas_sha256=str(receipt["atlas_sha256"]),
                             shape_catalog_sha256=str(receipt["shape_catalog_sha256"]))
    if receipt != expected:
        raise CosetError("receipt does not recompute from the pinned topology cache")
    stable = sum(record["rejection_cause"] is None for record in receipt["records"])
    return {"records": len(receipt["records"]), "stable": stable}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology-cache", required=True, type=Path)
    parser.add_argument("--atlas", required=True, type=Path,
                        help="Pinned atlas-of-atlases whose bytes are receipt-bound")
    parser.add_argument("--shape-catalog", required=True, type=Path,
                        help="Pinned shape catalog whose bytes are receipt-bound")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    cache = _load(args.topology_cache)
    if args.verify:
        result = validate_receipt(_load(args.out), cache)
        print(f"[OK] D2 receipt recomputed: {result['records']} records, {result['stable']} admitted")
        return 0
    receipt = build_receipt(cache, atlas_sha256=sha256_file(args.atlas),
                            shape_catalog_sha256=sha256_file(args.shape_catalog))
    _dump(args.out, receipt)
    print(f"[OK] wrote {len(receipt['records'])} D2 topology/coset records -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
