#!/usr/bin/env python3
"""FL-4876 FACEDIST-01. How far a placement is from a wall.

obs-inst-0577, an observed storefront awning, was refused with
``nearest_face_beyond_resolve_tolerance`` at 6.414 m against a 6.0 m
``face_resolve_m``. Its true perpendicular distance to the very face the
refusal named is 5.782 m -- inside the tolerance.

The planner was measuring to the MIDPOINT of the wall edge rather than to the
nearest point on it. On a 9.76 m facade that is 0.632 m of pure metric error,
and it grows with edge length. A declared face point already used the correct
segment distance, so one question had two answers selected by a flag that
carries no geometric meaning.

These tests pin the arithmetic against the real geometry that exposed it, and
pin that the planner no longer carries the midpoint form.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_population_plan import seg_dist2  # noqa: E402

PLANNER_SOURCE = ROOT / "scripts" / "pipeline" / "compile_population_plan.py"

# Canonical geometry, build_e2e_09, feature:building_footprint:way/1507403667
# face #f6, and the observed awning obs-inst-0577.
AWNING_E, AWNING_N = 379889.00775975286, 3938458.67416331
FACE_A = (379894.251, 3938461.893)
FACE_B = (379886.613, 3938467.963)
FACE_RESOLVE_M = 6.0


def _midpoint_distance(px, py, a, b):
    """The metric that was in the planner. Kept here only to pin the error."""
    return math.hypot(px - (a[0] + b[0]) / 2.0, py - (a[1] + b[1]) / 2.0)


def test_the_wall_is_long_enough_for_the_two_metrics_to_diverge():
    assert math.hypot(FACE_B[0] - FACE_A[0], FACE_B[1] - FACE_A[1]) > 9.0


def test_the_true_distance_to_the_named_face_is_inside_the_tolerance():
    d = math.sqrt(seg_dist2(
        AWNING_E, AWNING_N, FACE_A[0], FACE_A[1], FACE_B[0], FACE_B[1]))
    assert round(d, 3) == 5.782, d
    assert d < FACE_RESOLVE_M


def test_the_midpoint_metric_refused_a_placement_that_was_in_tolerance():
    """This is the defect, stated as arithmetic."""
    d_mid = _midpoint_distance(AWNING_E, AWNING_N, FACE_A, FACE_B)
    d_seg = math.sqrt(seg_dist2(
        AWNING_E, AWNING_N, FACE_A[0], FACE_A[1], FACE_B[0], FACE_B[1]))
    assert round(d_mid, 3) == 6.414, d_mid
    assert d_mid > FACE_RESOLVE_M > d_seg
    assert round(d_mid - d_seg, 3) == 0.632


def test_segment_distance_never_exceeds_midpoint_distance():
    """The midpoint lies on the segment, so it can only over-report."""
    for px, py in ((0.0, 0.0), (5.0, 12.0), (-3.0, 0.5), (100.0, -40.0),
                   (AWNING_E, AWNING_N)):
        d_seg = math.sqrt(seg_dist2(
            px, py, FACE_A[0], FACE_A[1], FACE_B[0], FACE_B[1]))
        assert d_seg <= _midpoint_distance(px, py, FACE_A, FACE_B) + 1e-9


def test_the_planner_carries_no_edge_midpoint_face_metric():
    """Falsifier: restore any midpoint form and this fails.

    Scoped to the facade attachment region so an unrelated midpoint elsewhere
    in the file cannot mask a regression here.
    """
    lines = PLANNER_SOURCE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if "_rec is None and isinstance(_run, dict) and _is_facade_mod"
                 in ln)
    end = next(i for i, ln in enumerate(lines[start:], start)
               if "no_building_face_candidate_was_tested" in ln)
    region = "\n".join(lines[start:end])
    assert "_ea[0] + _eb[0]) / 2.0" not in region, (
        "an edge-midpoint face distance is back in the facade region")
    assert region.count("seg_dist2(") >= 3, region.count("seg_dist2(")


def test_the_face_resolve_tolerance_was_not_widened():
    """The repair is a correct measurement, never a wider gate."""
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    assert "face_resolve_m" in source
    for bad in ("face_resolve_m\", 6.5", "face_resolve_m\", 7.0",
                "_face_tol_m = 6.5", "_face_tol_m = 7.0"):
        assert bad not in source, bad
