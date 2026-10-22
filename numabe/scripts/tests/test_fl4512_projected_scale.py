#!/usr/bin/env python3
"""Golden contracts for FL-4512 projected-scale qv1."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/fl4512_projected_scale.py"
EXTENT_MODULE_PATH = ROOT / "scripts/fl4512_projected_feature_extent.py"
FIXTURE_PATH = ROOT / "assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json"
SPEC = importlib.util.spec_from_file_location("fl4512_projected_scale", MODULE_PATH)
assert SPEC and SPEC.loader
SCALE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCALE)
EXTENT_SPEC = importlib.util.spec_from_file_location(
    "fl4512_projected_feature_extent", EXTENT_MODULE_PATH
)
assert EXTENT_SPEC and EXTENT_SPEC.loader
EXTENT = importlib.util.module_from_spec(EXTENT_SPEC)
EXTENT_SPEC.loader.exec_module(EXTENT)


def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_golden_ladder_and_fail_closed_cases_recompute():
    assert SCALE.validate_fixture(fixture()) == {"valid": 6, "rejected": 2}


def test_rotation_does_not_change_largest_singular_value():
    quarter_turn = [0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    jacobian = SCALE.perspective_jacobian_asset_to_cell(
        (0.0, 0.0, 2.0), (1.0, 1.0), quarter_turn
    )
    assert SCALE.quantize_projected_scale_q16_16(
        SCALE.largest_singular_value_2x3(jacobian)
    ) == 32768


def test_off_axis_perspective_includes_projection_jacobian_not_raw_distance():
    center = SCALE.perspective_jacobian_asset_to_cell(
        (0.0, 0.0, 2.0), (1.0, 1.0), (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    )
    off_axis = SCALE.perspective_jacobian_asset_to_cell(
        (1.0, 0.0, 2.0), (1.0, 1.0), (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    )
    assert SCALE.largest_singular_value_2x3(off_axis) > SCALE.largest_singular_value_2x3(center)


def test_underflow_overflow_and_degenerate_projection_are_named():
    with pytest.raises(SCALE.ProjectedScaleError, match="scale_underflow"):
        SCALE.quantize_projected_scale_q16_16(1.0 / float(1 << 18))
    with pytest.raises(SCALE.ProjectedScaleError, match="scale_overflow"):
        SCALE.quantize_projected_scale_q16_16(65536.0)
    with pytest.raises(SCALE.ProjectedScaleError, match="degenerate_projection"):
        SCALE.largest_singular_value_2x3((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


def test_q16_16_exact_half_rounds_up():
    assert SCALE.quantize_projected_scale_q16_16(1.0 + 0.5 / 65536.0) == 65537


def test_q16_16_exact_overflow_boundary_is_classified_not_narrowed():
    # Exact-boundary fixture (FL-4512). rho below is exactly 2^16 - 2^-17, so
    # rho * 65536 is exactly the double 2^32 - 0.5 (power-of-two scaling is
    # exact). Rounding that value produces 2^32, which does not fit Q16.16's
    # 32-bit range: the quantizer must classify overflow, never wrap or
    # narrow. The C++ runtime twin (engine/render/fl4512_projected_scale.h)
    # saturates the same boundary via its >= pre-round guard.
    boundary_rho = (2.0**32 - 0.5) / 65536.0
    assert boundary_rho * 65536.0 == 2.0**32 - 0.5  # scaling really is exact
    with pytest.raises(SCALE.ProjectedScaleError, match="scale_overflow"):
        SCALE.quantize_projected_scale_q16_16(boundary_rho)
    # One representable step below the boundary still rounds inside the
    # domain and must return the maximum legal Q16.16 value.
    below = math.nextafter(2.0**32 - 0.5, 0.0) / 65536.0
    assert SCALE.quantize_projected_scale_q16_16(below) == 2**32 - 1


def test_u6_band_delays_then_yields_without_changing_material_identity():
    threshold = {"enter_q": 65536, "exit_q": 49152}
    assert EXTENT.admission_state(57344, threshold, False) == "NOT_CLAIMED_EXTENT"
    assert EXTENT.admission_state(57344, threshold, True) == "U6_RETAIN_WITHIN_BAND"
    assert EXTENT.admission_state(49151, threshold, True) == "NOT_CLAIMED_EXTENT"


def test_invalid_threshold_order_fails_closed():
    with pytest.raises(EXTENT.ProjectedFeatureExtentError, match="invalid_feature_extent_admission"):
        EXTENT.validate_admission_threshold({"enter_q": 100, "exit_q": 101}, "crease")
