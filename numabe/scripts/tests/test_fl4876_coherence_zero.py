"""FL-4876 COHERENCE-ZERO-01: physical rules added to reach a buildable map.

* A pole-to-pole wire is not chained through a building footprint unless that
  building's measured height is below the wire (population_representation_contract).
* A freestanding pole has no chain position and is not wired.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shapely.geometry import Polygon  # noqa: E402

from scripts.pipeline.population_representation_contract import (  # noqa: E402
    _span_building_conflicts, span_building_index)

SQUARE = Polygon([(10, -5), (20, -5), (20, 5), (10, 5)])


def test_wire_through_unknown_height_building_is_blocked():
    assert _span_building_conflicts(0, 0, 30, 0, 8.0, [("b", SQUARE, None)]) == ["b"]


def test_wire_over_measured_lower_roof_is_clear():
    assert _span_building_conflicts(0, 0, 30, 0, 8.0, [("b", SQUARE, 6.5)]) == []
    assert _span_building_conflicts(0, 0, 30, 0, 8.0, [("b", SQUARE, 9.0)]) == ["b"]


def test_wire_beside_a_building_is_not_blocked():
    assert _span_building_conflicts(0, 10, 30, 10, 8.0, [("b", SQUARE, None)]) == []


def test_wire_wholly_inside_a_footprint_is_blocked():
    assert _span_building_conflicts(12, 0, 18, 0, 8.0, [("b", SQUARE, None)]) == ["b"]


def test_building_index_reads_measured_height_claims():
    feats = [{"feature_id": "f1", "semantic_class": "building_footprint",
              "geometry": {"frame_id": "epsg32654", "coordinates": [[0, 0], [4, 0], [4, 4], [0, 4]]},
              "properties": {"height_m": {"value": 7.5}}},
             {"feature_id": "f2", "semantic_class": "road", "geometry": {}}]
    idx = span_building_index(feats)
    assert [(fid, h) for fid, _r, h in idx] == [("f1", 7.5)]
