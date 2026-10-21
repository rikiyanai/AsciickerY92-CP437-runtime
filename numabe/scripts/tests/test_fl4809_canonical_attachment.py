"""Focused attachment-owner regressions for FL-4809."""

import pytest

from scripts.pipeline.canonical_attachment import (
    CanonicalIndex,
    attach,
    polygons_overlap,
    reconcile_surface_decal,
    resolve,
    resolve_span_endpoints,
)
from scripts.pipeline.compile_spatial_coherence_receipt import (
    _segment_mass_hits,
    _span_vertical_conflicts,
)


RULES = {
    "attachment_tolerances": {
        "carrier_association_m": {"value": 12.0},
        "face_resolve_m": {"value": 6.0},
    },
    "road_class_defaults": {
        "place_front_outward_offset_m": {"value": 0.5},
    },
}


def _world():
    return {
        "canonical_features": [
            {
                "feature_id": "feature:building_footprint:way/1",
                "semantic_class": "building_footprint",
                "geometry": {
                    "kind": "polygon",
                    "coordinates": [
                        [0.0, 0.0], [10.0, 0.0], [10.0, 10.0],
                        [0.0, 10.0], [0.0, 0.0],
                    ],
                },
            },
            {
                "feature_id": "feature:road:way/2",
                "semantic_class": "road",
                "geometry": {
                    "kind": "polyline",
                    "coordinates": [[0.0, -5.0], [10.0, -5.0]],
                },
            },
        ],
    }


def test_contained_sign_snaps_to_nearest_clear_building_face():
    index = CanonicalIndex(_world(), route_xy=[[0.0, -5.0], [10.0, -5.0]])
    sign = {
        "feature_id": "feature:mapillary_object/sign",
        "semantic_class": "sign",
        "geometry": {"kind": "point", "coordinates": [2.0, 1.0]},
    }

    record = attach(sign, index, RULES)
    pose = resolve(record, index)

    assert record["anchor_kind"] == "building_face"
    assert record["canonical_building_id"] == "feature:building_footprint:way/1"
    assert record["face_selection_reason"] == "containing_ring_nearest_face"
    assert record["along_face_fraction"] == pytest.approx(0.2)
    assert pose["E"] == pytest.approx(2.0)
    assert pose["N"] == pytest.approx(-0.5)


def test_contained_place_keeps_route_face_midpoint_policy():
    index = CanonicalIndex(_world(), route_xy=[[0.0, -5.0], [10.0, -5.0]])
    place = {
        "feature_id": "feature:place:node/3",
        "semantic_class": "place",
        "geometry": {"kind": "point", "coordinates": [2.0, 1.0]},
    }

    record = attach(place, index, RULES)
    pose = resolve(record, index)

    assert record["anchor_kind"] == "building_face"
    assert record["along_face_fraction"] == pytest.approx(0.5)
    assert pose["E"] == pytest.approx(5.0)
    assert pose["N"] == pytest.approx(-0.5)


def test_marking_inside_mass_keeps_chainage_and_nearest_clear_lateral():
    index = CanonicalIndex(_world())
    marking = {
        "feature_id": "feature:mapillary_object/marking",
        "semantic_class": "road_marking",
        "geometry": {"kind": "point", "coordinates": [2.0, 1.0]},
        "properties": {
            "geometry": {
                "uncertainty": {
                    "model": "isotropic_sigma",
                    "sigma": 5.0,
                    "units": "metre",
                },
            },
        },
    }

    record = attach(marking, index, RULES)
    pose = resolve(record, index)

    assert record["anchor_kind"] == "carrier_chainage"
    assert record["source_lateral_offset_m"] == pytest.approx(6.0)
    assert record["lateral_offset_m"] == pytest.approx(4.75)
    assert record["spatial_reconciliation"] == \
        "building_mass_conflict_within_geometry_uncertainty"
    assert pose["E"] == pytest.approx(2.0)
    assert pose["N"] == pytest.approx(-0.25)

    footprint_record = reconcile_surface_decal(
        marking, record, index, "crosswalk")
    footprint_pose = resolve(footprint_record, index)

    assert footprint_record["footprint_clearance_m"] == pytest.approx(1.25)
    assert footprint_record["lateral_offset_m"] == pytest.approx(3.5)
    assert footprint_record["spatial_reconciliation"] == \
        "surface_decal_footprint_mass_conflict_within_geometry_uncertainty"
    assert footprint_pose["N"] == pytest.approx(-1.5)


def _uncertain_fixture(sigma):
    return {
        "feature_id": "feature:mapillary_object/fixture",
        "semantic_class": "fixture",
        "geometry": {"kind": "point", "coordinates": [2.0, 1.0]},
        "properties": {
            "geometry": {
                "uncertainty": {
                    "model": "isotropic_sigma",
                    "sigma": sigma,
                    "units": "metre",
                },
            },
        },
    }


def test_fixture_inside_mass_reconciles_lateral_within_own_uncertainty():
    index = CanonicalIndex(_world())

    record = attach(_uncertain_fixture(7.0), index, RULES)
    pose = resolve(record, index)

    assert record["anchor_kind"] == "carrier_chainage"
    assert record["source_lateral_offset_m"] == pytest.approx(6.0)
    assert record["lateral_offset_m"] == pytest.approx(4.75)
    assert record["spatial_reconciliation"] == \
        "building_mass_conflict_within_geometry_uncertainty"
    assert record["spatial_reconciliation_m"] == pytest.approx(1.25)
    assert pose["E"] == pytest.approx(2.0)
    assert pose["N"] == pytest.approx(-0.25)


def test_fixture_inside_mass_becomes_debt_beyond_own_uncertainty():
    index = CanonicalIndex(_world())

    record = attach(_uncertain_fixture(1.0), index, RULES)

    assert record["record_type"] == "spatial_reconciliation_debt"
    assert record["anchor_kind"] == "carrier_chainage"
    assert record["reason"] == \
        "building_mass_conflict:beyond_geometry_uncertainty"
    assert record["residual_m"] == pytest.approx(1.0)


def test_curved_carrier_span_resolves_endpoint_specific_offsets():
    index = CanonicalIndex({
        "canonical_features": [{
            "feature_id": "feature:road:way/curve",
            "semantic_class": "road",
            "geometry": {
                "kind": "polyline",
                "coordinates": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]],
            },
        }],
    })
    record = {
        "record_type": "attachment",
        "anchor_kind": "carrier_span",
        "canonical_carrier_id": "feature:road:way/curve",
        "start_chainage_m": 5.0,
        "end_chainage_m": 15.0,
        "lateral_offset_m": 2.0,
        "side": "R",
        "start_lateral_offset_m": 2.0,
        "start_side": "R",
        "end_lateral_offset_m": 2.0,
        "end_side": "R",
    }

    span = resolve_span_endpoints(record, index)
    pose = resolve(record, index)

    assert (span["E0"], span["N0"]) == pytest.approx((5.0, 2.0))
    assert (span["E1"], span["N1"]) == pytest.approx((8.0, 5.0))
    assert (pose["E"], pose["N"]) == pytest.approx((6.5, 3.5))
    assert pose["yaw_deg"] == pytest.approx(45.0)


def test_polygon_overlap_detects_edge_crossing_without_contained_vertex():
    horizontal = [
        [-2.0, -0.5], [2.0, -0.5], [2.0, 0.5], [-2.0, 0.5],
        [-2.0, -0.5],
    ]
    vertical = [
        [-0.5, -2.0], [0.5, -2.0], [0.5, 2.0], [-0.5, 2.0],
        [-0.5, -2.0],
    ]

    assert polygons_overlap(horizontal, vertical)


def test_span_mass_intersection_checks_full_segment_not_only_midpoint():
    rings = {
        "mass-a": [
            [4.0, -1.0], [6.0, -1.0], [6.0, 1.0], [4.0, 1.0],
            [4.0, -1.0],
        ],
        "mass-b": [
            [14.0, -1.0], [16.0, -1.0], [16.0, 1.0], [14.0, 1.0],
            [14.0, -1.0],
        ],
    }

    assert _segment_mass_hits((0.0, 0.0), (20.0, 0.0), rings, set()) == [
        "mass-a", "mass-b"]
    assert _segment_mass_hits(
        (0.0, 0.0), (20.0, 0.0), rings, {"mass-a"}) == ["mass-b"]


def test_span_vertical_conflict_uses_physical_extents_without_tolerance():
    conflicts, clear = _span_vertical_conflicts(
        ["low", "equal", "unknown"], 8.0,
        {"low": 6.0, "equal": 8.0})

    assert conflicts == ["equal", "unknown"]
    assert clear == [("low", 6.0)]
