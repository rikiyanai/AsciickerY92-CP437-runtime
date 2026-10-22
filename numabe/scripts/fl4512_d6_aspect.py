#!/usr/bin/env python3
"""Reference evaluator for the FL-4512 D6 qv3 aspect-address ABI.

This module owns no runtime pool lookup.  It executes the paper's strict
binary64 right-polar transform split, normalization, Q1.15 snap, integer
octahedral fold, boundary-lower N-by-N indexing, and reflected-edge seam graph
so compiler, runtime, and U9 implementations have one checked golden corpus to
reproduce independently.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "fl4512.aspect_golden_vectors.v1"
QUANTIZATION_VERSION = 3
STRICT_BINARY64_VERSION = "fl4512.strict_binary64.v1"
TRANSFORM_VERSION = "fl4512.projected_view_transform.v3"
GRID_SIZE = 8
WORLD_METRIC_Q24_8 = (1024, 1024, 256)
POLAR_TOLERANCE = 2.0 ** -40
POLAR_MAX_ITERATIONS = 32


class D6AspectError(ValueError):
    """Fail-closed D6 evaluation error carrying one stable cause."""

    def __init__(self, cause: str, detail: str = "") -> None:
        super().__init__(f"{cause}: {detail}" if detail else cause)
        self.cause = cause


def q15_component(value: float) -> int:
    """Nearest signed Q1.15 with half ties toward negative infinity."""
    if not math.isfinite(value):
        raise D6AspectError("nonfinite_view")
    return max(-32768, min(32767, math.ceil(value * 32768.0 - 0.5)))


def normalize_and_snap_q15(vector: Iterable[float]) -> tuple[int, int, int]:
    """Execute the ordered ``fl4512.strict_binary64.v1`` pre-snap steps."""
    values = tuple(float(component) for component in vector)
    if len(values) != 3:
        raise D6AspectError("missing_authority", "view vector must have three components")
    dx, dy, dz = values
    if not all(math.isfinite(component) for component in values):
        raise D6AspectError("nonfinite_view")
    m = max(abs(dx), abs(dy), abs(dz))
    if m == 0.0:
        raise D6AspectError("zero_vector")
    rx = dx / m
    ry = dy / m
    rz = dz / m
    h2 = ((rx * rx) + (ry * ry)) + (rz * rz)
    h = math.sqrt(h2)
    tx = rx / h
    ty = ry / h
    tz = rz / h
    return q15_component(tx), q15_component(ty), q15_component(tz)


def _q24_8_positive_diagonal(value: Any, label: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(component, int) or isinstance(component, bool) or component <= 0
               for component in value)
    ):
        raise D6AspectError("missing_authority", label)
    return tuple(component / 256.0 for component in value)


def _q24_8_signed_point(value: Any, label: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(component, int) or isinstance(component, bool) for component in value)
    ):
        raise D6AspectError("missing_authority", label)
    return tuple(component / 256.0 for component in value)


def _row_major_matrix(value: Any, label: str) -> tuple[float, ...]:
    if (
        not isinstance(value, list)
        or len(value) != 9
        or any(not isinstance(component, (int, float)) or isinstance(component, bool)
               or not math.isfinite(float(component)) for component in value)
    ):
        raise D6AspectError("missing_authority", label)
    return tuple(float(component) for component in value)


def _matvec(a: tuple[float, ...], v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[0] * v[0] + a[1] * v[1] + a[2] * v[2],
        a[3] * v[0] + a[4] * v[1] + a[5] * v[2],
        a[6] * v[0] + a[7] * v[1] + a[8] * v[2],
    )


def _transpose(a: tuple[float, ...]) -> tuple[float, ...]:
    return (a[0], a[3], a[6], a[1], a[4], a[7], a[2], a[5], a[8])


def _matmul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        ((a[3 * row] * b[col]) + (a[3 * row + 1] * b[3 + col]))
        + (a[3 * row + 2] * b[6 + col])
        for row in range(3)
        for col in range(3)
    )


def _determinant(a: tuple[float, ...]) -> float:
    return (
        a[0] * ((a[4] * a[8]) - (a[5] * a[7]))
        - a[1] * ((a[3] * a[8]) - (a[5] * a[6]))
        + a[2] * ((a[3] * a[7]) - (a[4] * a[6]))
    )


def _inverse_transpose(a: tuple[float, ...]) -> tuple[float, ...]:
    determinant = _determinant(a)
    if determinant == 0.0 or not math.isfinite(determinant):
        raise D6AspectError("polar_nonconvergent", "noninvertible iterate")
    cofactors = (
        (a[4] * a[8]) - (a[5] * a[7]),
        (a[5] * a[6]) - (a[3] * a[8]),
        (a[3] * a[7]) - (a[4] * a[6]),
        (a[2] * a[7]) - (a[1] * a[8]),
        (a[0] * a[8]) - (a[2] * a[6]),
        (a[1] * a[6]) - (a[0] * a[7]),
        (a[1] * a[5]) - (a[2] * a[4]),
        (a[2] * a[3]) - (a[0] * a[5]),
        (a[0] * a[4]) - (a[1] * a[3]),
    )
    return tuple(value / determinant for value in cofactors)


def _orthogonality_residual(a: tuple[float, ...]) -> float:
    product = _matmul(_transpose(a), a)
    return max(
        abs(value - (1.0 if index in (0, 4, 8) else 0.0))
        for index, value in enumerate(product)
    )


def _max_abs_difference(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        raise D6AspectError("missing_authority", "matrix dimensions differ")
    return max(abs(a[index] - b[index]) for index in range(len(a)))


def right_polar_decomposition(c: Iterable[float]) -> dict[str, Any]:
    """Return the deterministic qv3 right-polar split ``C=R*S``."""
    matrix = tuple(float(value) for value in c)
    if len(matrix) != 9:
        raise D6AspectError("missing_authority", "C must contain nine values")
    if not all(math.isfinite(value) for value in matrix):
        raise D6AspectError("nonfinite_view")
    maximum = max(abs(value) for value in matrix)
    if maximum == 0.0:
        raise D6AspectError("singular_transform")
    determinant_c = _determinant(matrix)
    if not math.isfinite(determinant_c):
        raise D6AspectError("determinant_overflow")
    if determinant_c == 0.0:
        raise D6AspectError("singular_transform")
    if determinant_c < 0.0:
        raise D6AspectError("reflected_transform")

    q = tuple(value / maximum for value in matrix)
    iterations = 0
    residual = _orthogonality_residual(q)
    while residual > POLAR_TOLERANCE:
        if iterations >= POLAR_MAX_ITERATIONS:
            raise D6AspectError("polar_nonconvergent")
        inverse_transpose = _inverse_transpose(q)
        q = tuple(0.5 * (q[index] + inverse_transpose[index]) for index in range(9))
        if not all(math.isfinite(value) for value in q):
            raise D6AspectError("polar_nonconvergent")
        iterations += 1
        residual = _orthogonality_residual(q)

    determinant_r = _determinant(q)
    if (not math.isfinite(determinant_r) or determinant_r <= 0.0
            or abs(determinant_r - 1.0) > POLAR_TOLERANCE):
        raise D6AspectError("polar_nonconvergent")
    stretch = _matmul(_transpose(q), matrix)
    if not all(math.isfinite(value) for value in stretch):
        raise D6AspectError("polar_nonconvergent")
    symmetry_residual = max(
        abs(stretch[index] - _transpose(stretch)[index]) for index in range(9)
    )
    symmetry_limit = POLAR_TOLERANCE * max(1.0, max(abs(value) for value in stretch))
    if not math.isfinite(symmetry_residual) or symmetry_residual > symmetry_limit:
        raise D6AspectError("polar_nonconvergent")

    # Sylvester's criterion is evaluated only after the symmetry contract holds.
    # It proves that the returned right-polar stretch is positive definite rather
    # than merely symmetric with a positive full determinant.
    positive_definite_principal_minors = (
        stretch[0],
        (stretch[0] * stretch[4]) - (stretch[1] * stretch[3]),
        _determinant(stretch),
    )
    if (not all(math.isfinite(value) for value in positive_definite_principal_minors)
            or any(value <= 0.0 for value in positive_definite_principal_minors)):
        raise D6AspectError("polar_nonconvergent", "S is not positive definite")

    reconstructed = _matmul(q, stretch)
    if not all(math.isfinite(value) for value in reconstructed):
        raise D6AspectError("polar_nonconvergent")
    reconstruction_residual = _max_abs_difference(reconstructed, matrix)
    reconstruction_limit = POLAR_TOLERANCE * max(1.0, maximum)
    if (not math.isfinite(reconstruction_residual)
            or reconstruction_residual > reconstruction_limit):
        raise D6AspectError("polar_nonconvergent", "R*S does not reconstruct C")
    return {
        "C_row_major": list(matrix),
        "R_row_major": list(q),
        "S_row_major": list(stretch),
        "iterations": iterations,
        "orthogonality_residual": residual,
        "determinant_C": determinant_c,
        "determinant_R": determinant_r,
        "symmetry_residual": symmetry_residual,
        "positive_definite_principal_minors": list(positive_definite_principal_minors),
        "reconstruction_residual": reconstruction_residual,
    }


def _metric_effective_linear(
    world_metric_q24_8: Any,
    instance_linear_row_major: Any,
    asset_metric_q24_8: Any,
) -> tuple[float, ...]:
    """Return qv3 C=W*L*M^-1 in canonical Euclidean asset coordinates."""
    wx, wy, wz = _q24_8_positive_diagonal(world_metric_q24_8, "world_metric_q24_8")
    mx, my, mz = _q24_8_positive_diagonal(asset_metric_q24_8, "asset_metric_q24_8")
    l = _row_major_matrix(instance_linear_row_major, "instance_linear_row_major")
    return (
        wx * l[0] / mx, wx * l[1] / my, wx * l[2] / mz,
        wy * l[3] / mx, wy * l[4] / my, wy * l[5] / mz,
        wz * l[6] / mx, wz * l[7] / my, wz * l[8] / mz,
    )


def metric_case_vector(case: dict[str, Any]) -> tuple[float, float, float]:
    """Construct the qv3 rotation-only angular direction before normalization."""
    world_metric = _q24_8_positive_diagonal(case.get("world_metric_q24_8"), "world_metric_q24_8")
    asset_metric = _q24_8_positive_diagonal(case.get("asset_metric_q24_8"), "asset_metric_q24_8")
    linear = _metric_effective_linear(
        case.get("world_metric_q24_8"),
        case.get("instance_linear_row_major"),
        case.get("asset_metric_q24_8"),
    )
    eye_world = _q24_8_signed_point(case.get("eye_world_q24_8"), "eye_world_q24_8")
    translation_world = _q24_8_signed_point(
        case.get("translation_world_q24_8"), "translation_world_q24_8"
    )
    anchor_raw = _q24_8_signed_point(case.get("anchor_q24_8"), "anchor_q24_8")
    eye_e = tuple(world_metric[i] * eye_world[i] for i in range(3))
    translation_e = tuple(world_metric[i] * translation_world[i] for i in range(3))
    anchor_e = tuple(asset_metric[i] * anchor_raw[i] for i in range(3))
    anchor_world_e = _matvec(linear, anchor_e)
    delta = tuple(eye_e[i] - (anchor_world_e[i] + translation_e[i]) for i in range(3))
    polar = right_polar_decomposition(linear)
    rotation = tuple(polar["R_row_major"])
    return _matvec(_transpose(rotation), delta)


def metric_case_record(case: dict[str, Any], n: int = GRID_SIZE) -> dict[str, Any]:
    vector = metric_case_vector(case)
    q15 = normalize_and_snap_q15(vector)
    effective_linear = _metric_effective_linear(
        case.get("world_metric_q24_8"),
        case.get("instance_linear_row_major"),
        case.get("asset_metric_q24_8"),
    )
    return {
        "effective_linear_row_major": list(effective_linear),
        "polar": right_polar_decomposition(effective_linear),
        "q15": list(q15),
        **cell_record_from_q15(q15, n),
    }


def fold_q15(q15: Iterable[int]) -> tuple[int, int, int]:
    """Return exact folded numerators ``(nu,nv)`` and denominator ``s``."""
    values = tuple(q15)
    if (
        len(values) != 3
        or any(not isinstance(component, int) or isinstance(component, bool) for component in values)
        or any(component < -32768 or component > 32767 for component in values)
    ):
        raise D6AspectError("missing_authority", "invalid Q1.15 raw vector")
    x, y, z = values
    s = abs(x) + abs(y) + abs(z)
    if s == 0:
        raise D6AspectError("zero_vector")
    if z > 0:
        return x, y, s
    snz_x = 1 if x >= 0 else -1
    snz_y = 1 if y >= 0 else -1
    return (s - abs(y)) * snz_x, (s - abs(x)) * snz_y, s


def boundary_lower_index(numerator: int, denominator: int, n: int = GRID_SIZE) -> int:
    """Map one folded rational to a bin, assigning exact boundaries downward."""
    if denominator <= 0 or n <= 0 or not -denominator <= numerator <= denominator:
        raise D6AspectError("missing_authority", "folded rational outside contract")
    b = n * (numerator + denominator)
    d = 2 * denominator
    # Python // is mathematical floor division, including a negative dividend.
    index = (b - 1) // d
    return max(0, min(n - 1, index))


def cell_record_from_q15(q15: Iterable[int], n: int = GRID_SIZE) -> dict[str, int]:
    """Return the complete integer fold and row-major cell record."""
    nu, nv, s = fold_q15(q15)
    col = boundary_lower_index(nu, s, n)
    row = boundary_lower_index(nv, s, n)
    return {
        "nu": nu,
        "nv": nv,
        "s": s,
        "row": row,
        "col": col,
        "cell_index": row * n + col,
    }


def canonical_seam_pairs(n: int = GRID_SIZE) -> list[list[int]]:
    """Enumerate deduplicated reflected square-edge pairs as row-major indices."""
    if n <= 0:
        raise D6AspectError("missing_authority", "grid size must be positive")
    pairs: set[tuple[int, int]] = set()

    def add(row_a: int, col_a: int, row_b: int, col_b: int) -> None:
        a = row_a * n + col_a
        b = row_b * n + col_b
        if a != b:
            pairs.add((min(a, b), max(a, b)))

    for i in range(n):
        add(n - 1, i, n - 1, n - 1 - i)
        add(0, i, 0, n - 1 - i)
        add(i, 0, n - 1 - i, 0)
        add(i, n - 1, n - 1 - i, n - 1)
    return [list(pair) for pair in sorted(pairs)]


def _require_int_list(value: Any, count: int, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != count
        or any(not isinstance(item, int) or isinstance(item, bool) for item in value)
    ):
        raise D6AspectError("missing_authority", label)
    return value


def validate_fixture(fixture: dict[str, Any]) -> dict[str, int]:
    """Recompute every checked result in the canonical golden fixture."""
    if (
        fixture.get("schema") != SCHEMA
        or fixture.get("quantization_version") != QUANTIZATION_VERSION
        or fixture.get("strict_binary64_version") != STRICT_BINARY64_VERSION
        or fixture.get("transform_version") != TRANSFORM_VERSION
        or fixture.get("N") != GRID_SIZE
    ):
        raise D6AspectError("missing_authority", "fixture header")
    if fixture.get("seam_pairs") != canonical_seam_pairs():
        raise D6AspectError("missing_authority", "seam pair mismatch")

    required_polar_cases = {
        "rotated_anisotropic_stretch",
        "symmetric_shear",
        "near_singular_positive_determinant",
        "reflection_rejected",
        "determinant_overflow_rejected",
    }
    observed_polar_cases: set[str] = set()
    polar_count = 0
    polar_rejected_count = 0
    for case in fixture.get("polar_cases", []):
        case_id = case.get("id")
        if not isinstance(case_id, str) or case_id in observed_polar_cases:
            raise D6AspectError("missing_authority", "polar case id")
        observed_polar_cases.add(case_id)
        expected_cause = case.get("expected_cause")
        try:
            actual = right_polar_decomposition(case.get("C_row_major", []))
        except D6AspectError as exc:
            if expected_cause != exc.cause:
                raise D6AspectError(
                    "missing_authority", f"{case_id}: polar cause {exc.cause}"
                ) from exc
            polar_rejected_count += 1
        else:
            if expected_cause is not None or actual != case.get("expected"):
                raise D6AspectError("missing_authority", f"{case_id}: polar mismatch")
        polar_count += 1
    if not required_polar_cases.issubset(observed_polar_cases):
        raise D6AspectError("missing_authority", "polar fixture coverage")

    view_count = 0
    for case in fixture.get("view_vectors", []):
        expected_q15 = _require_int_list(case.get("expected_q15"), 3, "expected_q15")
        actual_q15 = list(normalize_and_snap_q15(case.get("input", [])))
        if actual_q15 != expected_q15:
            raise D6AspectError("missing_authority", f"{case.get('id')}: Q1.15 mismatch")
        actual = cell_record_from_q15(actual_q15)
        if actual != case.get("expected"):
            raise D6AspectError("missing_authority", f"{case.get('id')}: cell mismatch")
        view_count += 1

    snapped_count = 0
    rejected_count = 0
    for case in fixture.get("snapped_vectors", []):
        q15 = _require_int_list(case.get("q15"), 3, "q15")
        expected_cause = case.get("expected_cause")
        try:
            actual = cell_record_from_q15(q15)
        except D6AspectError as exc:
            if exc.cause != expected_cause:
                raise
            rejected_count += 1
        else:
            if expected_cause is not None or actual != case.get("expected"):
                raise D6AspectError("missing_authority", f"{case.get('id')}: snapped mismatch")
        snapped_count += 1

    metric_count = 0
    for case in fixture.get("metric_cases", []):
        actual = metric_case_record(case)
        if actual != case.get("expected"):
            raise D6AspectError("missing_authority", f"{case.get('id')}: metric mismatch")
        metric_count += 1

    if not view_count or not snapped_count or not rejected_count or metric_count < 2:
        raise D6AspectError("missing_authority", "fixture coverage")
    return {
        "polar_cases": polar_count,
        "polar_rejections": polar_rejected_count,
        "view_vectors": view_count,
        "snapped_vectors": snapped_count,
        "rejections": rejected_count,
        "metric_cases": metric_count,
        "seam_pairs": len(canonical_seam_pairs()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json"),
    )
    parser.add_argument("--view", nargs=3, type=float, metavar=("X", "Y", "Z"))
    parser.add_argument("--q15", nargs=3, type=int, metavar=("X", "Y", "Z"))
    parser.add_argument("--metric-case", type=str)
    parser.add_argument("--polar", nargs=9, type=float, metavar=("C00", "C01", "C02", "C10", "C11", "C12", "C20", "C21", "C22"))
    parser.add_argument("--seams", action="store_true")
    args = parser.parse_args()
    selected = (int(args.view is not None) + int(args.q15 is not None) + int(args.polar is not None) +
                int(args.seams) + int(args.metric_case is not None))
    if selected > 1:
        parser.error("select exactly one inspection mode")
    if args.view is not None:
        q15 = normalize_and_snap_q15(args.view)
        print(json.dumps({"q15": list(q15), **cell_record_from_q15(q15)}, sort_keys=True))
        return 0
    if args.q15 is not None:
        print(json.dumps({"q15": args.q15, **cell_record_from_q15(args.q15)}, sort_keys=True))
        return 0
    if args.polar is not None:
        print(json.dumps(right_polar_decomposition(args.polar), sort_keys=True))
        return 0
    if args.metric_case is not None:
        fixture = json.loads(args.fixture.read_text())
        for case in fixture.get("metric_cases", []):
            if case.get("id") == args.metric_case:
                print(json.dumps(metric_case_record(case), sort_keys=True))
                return 0
        parser.error("unknown --metric-case")
    if args.seams:
        print(json.dumps({"N": GRID_SIZE, "seam_pairs": canonical_seam_pairs()}, sort_keys=True))
        return 0
    fixture = json.loads(args.fixture.read_text())
    summary = validate_fixture(fixture)
    print(json.dumps({"status": "PASS", **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
