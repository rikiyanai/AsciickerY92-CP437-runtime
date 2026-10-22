#!/usr/bin/env python3
"""Golden contracts for FL-4512 D6 qv3 aspect addressing."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/fl4512_d6_aspect.py"
FIXTURE_PATH = ROOT / "assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json"
SPEC = importlib.util.spec_from_file_location("fl4512_d6_aspect", MODULE_PATH)
assert SPEC and SPEC.loader
D6 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D6)


def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_checked_fixture_recomputes_every_vector_and_seam_pair():
    assert D6.validate_fixture(fixture()) == {
        "polar_cases": 5,
        "polar_rejections": 2,
        "view_vectors": 12,
        "snapped_vectors": 10,
        "rejections": 1,
        "metric_cases": 2,
        "seam_pairs": 16,
    }


def test_q15_exact_half_ties_choose_negative_infinity():
    unit = 1.0 / 32768.0
    assert D6.q15_component(0.5 * unit) == 0
    assert D6.q15_component(1.5 * unit) == 1
    assert D6.q15_component(-0.5 * unit) == -1
    assert D6.q15_component(-1.5 * unit) == -2
    assert D6.q15_component(1.0) == 32767
    assert D6.q15_component(-1.0) == -32768


def test_boundary_lower_formula_rejects_the_retired_plain_floor_result():
    # u=-3/4 is the exact first internal boundary for N=8.  Plain floor of
    # N*(u+1)/2 yields 1; D6 v1.1 assigns the boundary to lower index 0.
    assert D6.boundary_lower_index(-3, 4) == 0
    assert D6.boundary_lower_index(-1, 4) == 2
    assert D6.boundary_lower_index(1, 4) == 4
    assert D6.boundary_lower_index(3, 4) == 6


def test_seam_graph_is_reflected_edge_cycle_without_corner_diagonals():
    pairs = {tuple(pair) for pair in D6.canonical_seam_pairs()}
    assert {(0, 7), (0, 56), (7, 63), (56, 63)} <= pairs
    assert (0, 63) not in pairs
    assert (7, 56) not in pairs
    assert len(pairs) == 16


def test_zero_and_nonfinite_view_inputs_fail_closed():
    with pytest.raises(D6.D6AspectError, match="zero_vector") as zero:
        D6.normalize_and_snap_q15((0.0, 0.0, 0.0))
    assert zero.value.cause == "zero_vector"

    with pytest.raises(D6.D6AspectError, match="nonfinite_view") as nonfinite:
        D6.normalize_and_snap_q15((float("inf"), 0.0, 1.0))
    assert nonfinite.value.cause == "nonfinite_view"


def test_z_zero_branch_and_south_pole_sign_not_zero_are_pinned():
    assert D6.cell_record_from_q15((1, -1, 0)) == {
        "nu": 1,
        "nv": -1,
        "s": 2,
        "row": 1,
        "col": 5,
        "cell_index": 13,
    }
    assert D6.cell_record_from_q15((0, 0, -32768)) == {
        "nu": 32768,
        "nv": 32768,
        "s": 32768,
        "row": 7,
        "col": 7,
        "cell_index": 63,
    }


def test_fixture_carries_qv3_metric_and_coordinate_versions():
    data = fixture()
    assert data["authority_revision"] == "fl4512.projected_aspect_family.v1/qv3-polar-transform"
    assert data["quantization_version"] == 3
    assert data["transform_version"] == "fl4512.projected_view_transform.v3"
    assert data["strict_binary64_version"] == "fl4512.strict_binary64.v1"
    assert data["coordinate_convention"]["exact_internal_boundary"] == "lower_index"
    assert data["metric_contract"]["world_metric_q24_8"] == [1024, 1024, 256]
    assert data["metric_contract"]["effective_linear"] == "C=W*L*M^-1"


def test_qv3_metric_cases_exercise_world_scale_and_pieta_calibration():
    data = fixture()
    cases = {case["id"]: case for case in data["metric_cases"]}
    world = D6.metric_case_record(cases["world_metric_changes_cell_index"])
    raw = D6.cell_record_from_q15(D6.normalize_and_snap_q15((1.0, 0.0, 1.0)))
    assert world == cases["world_metric_changes_cell_index"]["expected"]
    assert world["cell_index"] == 31
    assert raw["cell_index"] == 29

    pieta = D6.metric_case_record(cases["pieta_metric_inverse_is_live"])
    assert pieta == cases["pieta_metric_inverse_is_live"]["expected"]
    assert pieta["effective_linear_row_major"] == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    assert pieta["cell_index"] == 63


def test_native_renderer_uses_qv3_rotation_only_before_q15_snap():
    source = (ROOT / "engine/render/render_world_pass.cpp").read_text()
    assert "static bool Fl4512AspectQ15ForCurrentMeshV3(" in source
    assert "world_metric[3]" in source
    assert "(double)HEIGHT_CELLS, (double)HEIGHT_CELLS, 1.0" in source
    assert "world_metric[row] *" in source
    assert "256.0 / (double)asset_metric_q24_8[col]" in source
    assert "Fl4512AspectDirectionQ15V3(effective, delta, out_q15, out_polar)" in source
    assert "Fl4512AspectRegionForMeshQ15(mesh_name, 0" in source
