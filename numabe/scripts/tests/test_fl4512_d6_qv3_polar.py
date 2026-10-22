#!/usr/bin/env python3
"""Focused contracts for FL-4512 D6 qv3 polar transform ownership."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/fl4512_d6_aspect.py"
FIXTURE_PATH = ROOT / "assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json"
AUTHORING_PATH = ROOT / "assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json"
LEDGER_PATH = ROOT / "docs/research/glyph-rendering/README.md"
MANUSCRIPT_PATH = (
    ROOT / "docs/agent/glyph-rendering-paper/2026-07-07-fl4512-first-rough-draft.tex"
)

SPEC = importlib.util.spec_from_file_location("fl4512_d6_aspect_qv3", MODULE_PATH)
assert SPEC and SPEC.loader
D6 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D6)


def metric_case(instance_linear: list[float]) -> dict[str, object]:
    return {
        "world_metric_q24_8": [1024, 1024, 256],
        "asset_metric_q24_8": [1024, 1024, 256],
        "instance_linear_row_major": instance_linear,
        "eye_world_q24_8": [256, 0, 256],
        "translation_world_q24_8": [0, 0, 0],
        "anchor_q24_8": [0, 0, 0],
    }


def test_qv3_fixture_and_authoring_delete_qv2_acceptance():
    fixture = json.loads(FIXTURE_PATH.read_text())
    authoring = json.loads(AUTHORING_PATH.read_text())
    assert D6.QUANTIZATION_VERSION == 3
    assert fixture["quantization_version"] == 3
    assert fixture["transform_version"] == "fl4512.projected_view_transform.v3"
    assert all(asset["quantization_version"] == 3 for asset in authoring["assets"])
    summary = D6.validate_fixture(fixture)
    assert summary["metric_cases"] >= 2
    assert summary["polar_cases"] >= 5
    assert summary["polar_rejections"] >= 2


def test_positive_anisotropic_stretch_changes_s_not_angular_direction():
    identity = D6.metric_case_record(metric_case([1, 0, 0, 0, 1, 0, 0, 0, 1]))
    stretched = D6.metric_case_record(metric_case([2, 0, 0, 0, 1, 0, 0, 0, 0.5]))
    assert stretched["polar"]["R_row_major"] == pytest.approx(
        [1, 0, 0, 0, 1, 0, 0, 0, 1], abs=2.0**-40
    )
    assert stretched["polar"]["S_row_major"] == pytest.approx(
        [2, 0, 0, 0, 1, 0, 0, 0, 0.5], abs=2.0**-40
    )
    assert stretched["q15"] == identity["q15"]
    assert stretched["cell_index"] == identity["cell_index"]


def test_rotated_stretch_extracts_the_rotation():
    result = D6.right_polar_decomposition([0, -1, 0, 2, 0, 0, 0, 0, 0.5])
    assert result["R_row_major"] == pytest.approx(
        [0, -1, 0, 1, 0, 0, 0, 0, 1], abs=2.0**-40
    )
    assert result["S_row_major"] == pytest.approx(
        [2, 0, 0, 0, 1, 0, 0, 0, 0.5], abs=2.0**-40
    )
    assert result["reconstruction_residual"] <= 2.0**-40
    assert all(value > 0.0 for value in result["positive_definite_principal_minors"])


@pytest.mark.parametrize(
    "matrix",
    [
        [1.0, 0.25, 0.0, 0.25, 1.5, 0.0, 0.0, 0.0, 0.75],
        [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 2.0**-20],
    ],
)
def test_valid_stretches_prove_reconstruction_and_positive_definiteness(
    matrix: list[float],
):
    result = D6.right_polar_decomposition(matrix)
    assert result["reconstruction_residual"] <= D6.POLAR_TOLERANCE
    assert all(value > 0.0 for value in result["positive_definite_principal_minors"])


@pytest.mark.parametrize(
    ("matrix", "cause"),
    [
        ([-1, 0, 0, 0, 1, 0, 0, 0, 1], "reflected_transform"),
        ([0, 0, 0, 0, 1, 0, 0, 0, 1], "singular_transform"),
        ([1.0e308, 0, 0, 0, 1.0e308, 0, 0, 0, 1.0e308], "determinant_overflow"),
        ([float("inf"), 0, 0, 0, 1, 0, 0, 0, 1], "nonfinite_view"),
    ],
)
def test_invalid_transform_classes_fail_closed(matrix: list[float], cause: str):
    with pytest.raises(D6.D6AspectError) as caught:
        D6.right_polar_decomposition(matrix)
    assert caught.value.cause == cause


def test_paper_and_manuscript_name_the_single_transform_owner():
    ledger = LEDGER_PATH.read_text()
    manuscript = MANUSCRIPT_PATH.read_text()
    assert "fl4512.projected_view_transform.v3" in ledger
    assert "C=R*S" in ledger
    assert "J = derivative(P(V(C*x+t)), x)" in ledger
    assert "positive_definite_principal_minors" in ledger
    assert "reconstruction_residual" in ledger
    assert "determinant_overflow" in ledger
    assert "right polar decomposition $C=RS$" in manuscript
    assert "\\citep{shoemake1992matrixanimation}" in manuscript
