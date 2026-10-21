#!/usr/bin/env python3
"""FL-4876 CLEARANCE-PERF-01. Pruning must not change an answer.

The lattice search in ``_nearest_clear_point_within_sigma`` asks "is this
point clear of every mass" for up to ~1300 candidate positions. Each ask was
walking all 733 canonical rings and every edge of each, twice: once for the
nearest distance and once for containment. Production runs 11, 12 and 13
stalled inside it.

Bounding-box pruning fixes the cost. These tests exist because a faster
measurement that returns a different answer is not an optimisation, it is a
regression with a stopwatch attached.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.pipeline.canonical_attachment as CA  # noqa: E402


def _index(rings):
    doc = {"canonical_features": [
        {"feature_id": f"feature:building_footprint:test/{i}",
         "semantic_class": "building_footprint",
         "geometry": {"kind": "polygon", "coordinates": r}}
        for i, r in enumerate(rings)]}
    return CA.CanonicalIndex(doc)


def _brute_clearance(px, py, index):
    nearest = min((CA._dist_point_to_ring(px, py, r)
                   for _, r in index.buildings), default=float("inf"))
    return -nearest if CA._inside_any_building(px, py, index) else nearest


def _grid_rings(n=12, size=8.0, gap=14.0):
    out = []
    for i in range(n):
        for j in range(n):
            x, y = i * gap, j * gap
            out.append([[x, y], [x + size, y],
                        [x + size, y + size], [x, y + size], [x, y]])
    return out


RINGS = _grid_rings()
INDEX = _index(RINGS)


def test_the_index_builds_one_box_per_ring():
    assert len(INDEX.building_bboxes) == len(INDEX.buildings) == len(RINGS)


def test_the_box_bound_is_never_greater_than_the_true_distance():
    """Soundness. If the bound could exceed the true distance, pruning would
    discard the actual nearest ring."""
    random.seed(4876)
    for _ in range(400):
        px = random.uniform(-20, 190)
        py = random.uniform(-20, 190)
        for i, (_, ring) in enumerate(INDEX.buildings):
            bound = CA._dist_point_to_bbox(px, py, INDEX.building_bboxes[i])
            true = CA._dist_point_to_ring(px, py, ring)
            assert bound <= true + 1e-9, (px, py, bound, true)


def test_a_point_inside_a_box_has_zero_bound():
    bb = INDEX.building_bboxes[0]
    cx = (bb[0] + bb[2]) / 2.0
    cy = (bb[1] + bb[3]) / 2.0
    assert CA._dist_point_to_bbox(cx, cy, bb) == 0.0


def test_pruned_clearance_equals_exhaustive_clearance():
    random.seed(11)
    pts = [(random.uniform(-20, 190), random.uniform(-20, 190))
           for _ in range(500)]
    for ring in RINGS[:40]:
        pts.append((ring[0][0], ring[0][1]))                       # on a corner
        pts.append(((ring[0][0] + ring[2][0]) / 2.0,
                    (ring[0][1] + ring[2][1]) / 2.0))              # inside
    for px, py in pts:
        fast = CA.mass_clearance_m(px, py, INDEX)
        slow = _brute_clearance(px, py, INDEX)
        assert fast == pytest.approx(slow, abs=1e-9), (px, py, fast, slow)


def test_inside_a_mass_still_reports_a_negative_clearance():
    ring = RINGS[0]
    cx = (ring[0][0] + ring[2][0]) / 2.0
    cy = (ring[0][1] + ring[2][1]) / 2.0
    assert CA.mass_clearance_m(cx, cy, INDEX) < 0.0


@pytest.mark.parametrize("threshold", [-1.0, 0.0, 0.25, 1.0, 3.0, 7.0])
def test_the_threshold_predicate_matches_the_measurement(threshold):
    """_clear_of_buildings short-circuits. It must still agree exactly with
    ``mass_clearance_m(...) > threshold``, including for a negative threshold
    and for points inside a mass."""
    random.seed(22)
    pts = [(random.uniform(-20, 190), random.uniform(-20, 190))
           for _ in range(300)]
    for ring in RINGS[:30]:
        pts.append(((ring[0][0] + ring[2][0]) / 2.0,
                    (ring[0][1] + ring[2][1]) / 2.0))
    for px, py in pts:
        fast = CA._clear_of_buildings(px, py, threshold, INDEX)
        slow = _brute_clearance(px, py, INDEX) > threshold
        assert fast == slow, (threshold, px, py, fast, slow)


def test_an_index_without_boxes_still_works():
    """The fallback path must not be silently wrong for a hand-built index."""
    idx = _index(RINGS[:5])
    idx.building_bboxes = []
    assert CA.mass_clearance_m(0.0, 0.0, idx) == pytest.approx(
        _brute_clearance(0.0, 0.0, idx), abs=1e-9)
    assert CA._clear_of_buildings(0.0, 0.0, 1.0, idx) == (
        _brute_clearance(0.0, 0.0, idx) > 1.0)


def test_an_empty_world_is_infinitely_clear():
    idx = _index([])
    assert CA.mass_clearance_m(0.0, 0.0, idx) == float("inf")
    assert CA._clear_of_buildings(0.0, 0.0, 99.0, idx) is True


def test_the_lattice_search_still_returns_a_lawful_move():
    ring = RINGS[0]
    cx = (ring[0][0] + ring[2][0]) / 2.0
    cy = (ring[0][1] + ring[2][1]) / 2.0
    got = CA._nearest_clear_point_within_sigma(cx, cy, 0.5, INDEX, 6.0)
    assert got is not None
    x, y, move = got
    assert move <= 6.0 + 1e-9
    assert CA._clear_of_buildings(x, y, 0.5, INDEX)
    assert math.hypot(x - cx, y - cy) == pytest.approx(move, abs=1e-6)
