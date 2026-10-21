#!/usr/bin/env python3
"""Deterministic FL-4512 semantic-chain projected-extent admission helpers."""

from __future__ import annotations

import math
from typing import Any


Q_ONE = 1 << 16
Q_MAX = (1 << 32) - 1
EXTENT_QUANTIZATION_VERSION = 1


class ProjectedFeatureExtentError(ValueError):
    def __init__(self, cause: str, detail: str = "") -> None:
        super().__init__(f"{cause}: {detail}" if detail else cause)
        self.cause = cause


def quantize_projected_extent_q16_16(cells: float) -> int:
    value = float(cells)
    if not math.isfinite(value) or value <= 0.0:
        raise ProjectedFeatureExtentError("invalid_projected_feature_extent")
    scaled = value * float(Q_ONE)
    if not math.isfinite(scaled):
        raise ProjectedFeatureExtentError("invalid_projected_feature_extent")
    return min(Q_MAX, int(math.floor(scaled + 0.5)))


def validate_admission_threshold(value: Any, label: str) -> tuple[int, int]:
    """Return the Q16.16 terminal-cell extent band for one feature class."""
    if not isinstance(value, dict):
        raise ProjectedFeatureExtentError("missing_feature_extent_admission", label)
    enter_q = value.get("enter_q")
    exit_q = value.get("exit_q")
    if (
        not isinstance(enter_q, int)
        or isinstance(enter_q, bool)
        or not isinstance(exit_q, int)
        or isinstance(exit_q, bool)
        or exit_q < 1
        or enter_q < exit_q
        or enter_q > Q_MAX
    ):
        raise ProjectedFeatureExtentError("invalid_feature_extent_admission", label)
    return enter_q, exit_q


def admission_state(
    projected_extent_q: int,
    threshold: Any,
    incumbent_same_surface_role: bool,
) -> str:
    """Evaluate current admission plus the bounded U6 extent-retention band."""
    if (
        not isinstance(projected_extent_q, int)
        or isinstance(projected_extent_q, bool)
        or projected_extent_q < 1
        or projected_extent_q > Q_MAX
    ):
        raise ProjectedFeatureExtentError(
            "missing_projected_feature_extent", "projected_extent_q"
        )
    enter_q, exit_q = validate_admission_threshold(threshold, "threshold")
    if projected_extent_q >= enter_q:
        return "CLAIMED_CURRENT"
    if incumbent_same_surface_role and projected_extent_q >= exit_q:
        return "U6_RETAIN_WITHIN_BAND"
    return "NOT_CLAIMED_EXTENT"


def validate_fixture(data: dict[str, Any]) -> dict[str, int]:
    if data.get("schema") != "fl4512.projected_feature_extent_golden_vectors.v2":
        raise ProjectedFeatureExtentError("missing_feature_extent_authority", "schema")
    if data.get("extent_schema") != "fl4512.projected_feature_extent_admission.v2":
        raise ProjectedFeatureExtentError("missing_feature_extent_authority", "extent_schema")
    if data.get("extent_quantization_version") != EXTENT_QUANTIZATION_VERSION:
        raise ProjectedFeatureExtentError("missing_feature_extent_authority", "version")
    thresholds = data.get("mesh_thresholds")
    if not isinstance(thresholds, dict):
        raise ProjectedFeatureExtentError("missing_feature_extent_admission", "thresholds")
    for feature_class in ("silhouette", "crease", "material_boundary", "suggestive_contour"):
        validate_admission_threshold(thresholds.get(feature_class), feature_class)
    validate_admission_threshold(data.get("filled_threshold"), "filled")
    handoffs = 0
    for case in data.get("handoff_cases", []):
        actual = admission_state(
            case.get("projected_extent_q"),
            (data.get("filled_threshold") if case.get("feature_class") == "filled"
             else thresholds.get(case.get("feature_class"))),
            bool(case.get("incumbent_same_surface_role")),
        )
        if actual != case.get("expected_state"):
            raise ProjectedFeatureExtentError("golden_mismatch", str(case.get("id")))
        handoffs += 1
    return {"handoffs": handoffs}
