#!/usr/bin/env python3
"""Reference evaluator for FL-4512 projected-scale qv1.

The evaluator owns no pool lookup and no runtime presentation.  It maps the
canonical Euclidean asset metric into TERM++ cell space, computes the largest
singular value of that 2x3 projection Jacobian, then quantizes the result as
unsigned Q16.16.  Compiler, runtime, and U9 must reproduce the checked golden
fixture independently before a scale-sensitive claim can be promoted.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "fl4512.projected_scale_golden_vectors.v1"
SCALE_SCHEMA = "fl4512.projected_scale.v1"
SCALE_QUANTIZATION_VERSION = 1
Q_FRACTION_BITS = 16
Q_ONE = 1 << Q_FRACTION_BITS
Q_MAX = (1 << 32) - 1


class ProjectedScaleError(ValueError):
    """Fail-closed projected-scale error carrying one stable cause."""

    def __init__(self, cause: str, detail: str = "") -> None:
        super().__init__(f"{cause}: {detail}" if detail else cause)
        self.cause = cause


def _finite_tuple(value: Iterable[float], count: int, label: str) -> tuple[float, ...]:
    result = tuple(float(component) for component in value)
    if len(result) != count:
        raise ProjectedScaleError("missing_scale_authority", label)
    if not all(math.isfinite(component) for component in result):
        raise ProjectedScaleError("nonfinite_scale", label)
    return result


def _mul_2x3_3x3(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    """Ordered row-major 2x3 by 3x3 multiplication without contraction."""
    out: list[float] = []
    for row in range(2):
        for col in range(3):
            p0 = left[row * 3 + 0] * right[0 * 3 + col]
            p1 = left[row * 3 + 1] * right[1 * 3 + col]
            p2 = left[row * 3 + 2] * right[2 * 3 + col]
            out.append((p0 + p1) + p2)
    return tuple(out)


def perspective_jacobian_asset_to_cell(
    anchor_camera: Iterable[float],
    focal_cells: Iterable[float],
    asset_to_camera_row_major: Iterable[float],
) -> tuple[float, ...]:
    """Differentiate the pinned direct-perspective projection at one anchor."""
    x, y, z = _finite_tuple(anchor_camera, 3, "anchor_camera")
    fx, fy = _finite_tuple(focal_cells, 2, "focal_cells")
    rotation = _finite_tuple(asset_to_camera_row_major, 9, "asset_to_camera_row_major")
    if z <= 0.0:
        raise ProjectedScaleError("nonpositive_scale_depth")
    if fx <= 0.0 or fy <= 0.0:
        raise ProjectedScaleError("degenerate_projection")
    inv_z = 1.0 / z
    inv_z2 = inv_z * inv_z
    projection = (
        fx * inv_z, 0.0, -(fx * x) * inv_z2,
        0.0, fy * inv_z, -(fy * y) * inv_z2,
    )
    return _mul_2x3_3x3(projection, rotation)


def orthographic_jacobian_asset_to_cell(
    scale_cells: Iterable[float],
    asset_to_camera_row_major: Iterable[float],
) -> tuple[float, ...]:
    """Construct the pinned direct-orthographic projection Jacobian."""
    sx, sy = _finite_tuple(scale_cells, 2, "scale_cells")
    rotation = _finite_tuple(asset_to_camera_row_major, 9, "asset_to_camera_row_major")
    if sx <= 0.0 or sy <= 0.0:
        raise ProjectedScaleError("degenerate_projection")
    return _mul_2x3_3x3((sx, 0.0, 0.0, 0.0, sy, 0.0), rotation)


def largest_singular_value_2x3(jacobian: Iterable[float]) -> float:
    """Return sqrt(lambda_max(J J^T)) with one fixed binary64 order."""
    j = _finite_tuple(jacobian, 6, "jacobian_asset_to_cell")
    a = ((j[0] * j[0]) + (j[1] * j[1])) + (j[2] * j[2])
    b = ((j[0] * j[3]) + (j[1] * j[4])) + (j[2] * j[5])
    c = ((j[3] * j[3]) + (j[4] * j[4])) + (j[5] * j[5])
    delta = a - c
    disc = (delta * delta) + ((4.0 * b) * b)
    if not math.isfinite(disc) or disc < 0.0:
        raise ProjectedScaleError("nonfinite_scale")
    lambda_max = ((a + c) + math.sqrt(disc)) * 0.5
    if not math.isfinite(lambda_max):
        raise ProjectedScaleError("nonfinite_scale")
    if lambda_max <= 0.0:
        raise ProjectedScaleError("degenerate_projection")
    rho = math.sqrt(lambda_max)
    if not math.isfinite(rho):
        raise ProjectedScaleError("nonfinite_scale")
    return rho


def quantize_projected_scale_q16_16(rho: float) -> int:
    """Round nonnegative rho to nearest Q16.16; exact halves round upward."""
    value = float(rho)
    if not math.isfinite(value):
        raise ProjectedScaleError("nonfinite_scale")
    if value <= 0.0:
        raise ProjectedScaleError("degenerate_projection")
    scaled = value * float(Q_ONE)
    if not math.isfinite(scaled):
        raise ProjectedScaleError("nonfinite_scale")
    q = math.floor(scaled + 0.5)
    if q < 1:
        raise ProjectedScaleError("scale_underflow")
    if q > Q_MAX:
        raise ProjectedScaleError("scale_overflow")
    return int(q)


def record_from_case(case: dict[str, Any]) -> dict[str, Any]:
    kind = case.get("projection_kind")
    if kind == "DIRECT_PERSPECTIVE":
        jacobian = perspective_jacobian_asset_to_cell(
            case.get("anchor_camera", []),
            case.get("focal_cells", []),
            case.get("asset_to_camera_row_major", []),
        )
    elif kind == "DIRECT_ORTHOGRAPHIC":
        jacobian = orthographic_jacobian_asset_to_cell(
            case.get("scale_cells", []),
            case.get("asset_to_camera_row_major", []),
        )
    else:
        raise ProjectedScaleError("unsupported_scale_projection")
    rho = largest_singular_value_2x3(jacobian)
    return {
        "jacobian_asset_to_cell": list(jacobian),
        "projected_scale_q": quantize_projected_scale_q16_16(rho),
    }


def validate_fixture(data: dict[str, Any]) -> dict[str, int]:
    if data.get("schema") != SCHEMA:
        raise ProjectedScaleError("missing_scale_authority", "schema")
    if data.get("scale_schema") != SCALE_SCHEMA:
        raise ProjectedScaleError("missing_scale_authority", "scale_schema")
    if data.get("scale_quantization_version") != SCALE_QUANTIZATION_VERSION:
        raise ProjectedScaleError("missing_scale_authority", "scale_quantization_version")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProjectedScaleError("missing_scale_authority", "cases")
    valid = 0
    rejected = 0
    previous_q: int | None = None
    for case in cases:
        expected = case.get("expected", {})
        cause = expected.get("cause")
        try:
            actual = record_from_case(case)
        except ProjectedScaleError as exc:
            if exc.cause != cause:
                raise
            rejected += 1
            continue
        if cause is not None or actual != expected:
            raise ProjectedScaleError("golden_mismatch", str(case.get("id")))
        if case.get("ladder") == "near_to_far":
            q = actual["projected_scale_q"]
            if previous_q is not None and q >= previous_q:
                raise ProjectedScaleError("golden_mismatch", "ladder_not_strictly_decreasing")
            previous_q = q
        valid += 1
    return {"valid": valid, "rejected": rejected}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json"
    print(json.dumps(validate_fixture(json.loads(fixture.read_text())), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
