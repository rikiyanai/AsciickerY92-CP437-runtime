#!/usr/bin/env python3
"""FL-4876 FREESTAND-01. A resolved attachment that no consumer could consume.

The SPATIAL-A2/A3 repair taught two sites to resolve an object to a
``freestanding`` anchor instead of refusing it. Measured on the Numabe lane
immediately afterwards, that produced:

* **5 attachment records that raise ``KeyError: 'canonical_point'``** the
  moment anything calls ``resolve()`` on them. The A2 record was built from
  scratch and omitted the field every other freestanding record carries.
* **1 record whose 0.901 m reconciliation ``resolve()`` silently discarded**,
  because the freestanding branch re-derived the position from the raw
  feature geometry -- putting the object back inside the mass the sweep had
  just moved it out of.
* **2 rows the placement generator refused** as ``unsupported_anchor_kind``,
  so a resolved attachment reached no compatible placement consumer.

That is the defect class this whole series is about -- a producer declaring a
vocabulary the consumer does not share -- committed by the repair for it.

These tests also pin the two verification gaps the repair left open:

* the sigma budget is measured from the **original observation**, so two
  stages cannot each spend a full sigma;
* a placement's **oriented footprint**, not a point and not a circumscribing
  radius, is what has to clear a mass.

The radius version of that last check was written first and measurement
refuted it: it reported 102 conflicts on this lane, 96 of them shop
signboards whose 1.6 m dimension runs *along* the wall they are mounted on.
The oriented test reports 38.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.pipeline.canonical_attachment as attachment  # noqa: E402
import scripts.pipeline.compile_representation_fixture_placements \
    as generator  # noqa: E402

SIGMA = 5.0
SOURCE = Path(attachment.__file__).read_text(encoding="utf-8")
GENERATOR_SOURCE = Path(generator.__file__).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Scaffolding: a canonical index carrying exactly the masses a case is about.
# --------------------------------------------------------------------------

class _Index:
    def __init__(self, buildings=(), by_id=None, carriers=()):
        self.buildings = list(buildings)
        self.by_id = dict(by_id or {})
        self.carriers = list(carriers)

    def containing_terrain(self, px, py):
        return None

    def nearest_carrier(self, px, py):
        return None


def _square(cx: float, cy: float, half: float) -> list:
    return [[cx - half, cy - half], [cx + half, cy - half],
            [cx + half, cy + half], [cx - half, cy + half],
            [cx - half, cy - half]]


def _point_feature(px: float, py: float, sigma: float = SIGMA) -> dict:
    return {
        "feature_id": "feature:test/point",
        "geometry": {"kind": "point", "coordinates": [px, py]},
        "properties": {"geometry": {
            "uncertainty": {"model": "isotropic_sigma", "sigma": sigma}}},
    }


# --------------------------------------------------------------------------
# resolve() must honour a reconciled position.
# --------------------------------------------------------------------------

def test_resolve_uses_the_position_the_reconciliation_chose():
    """The measured defect: a 0.901 m move thrown away by its own consumer.

    The record says the object belongs at (10.5, 4.0). The feature geometry
    still says (10.0, 4.0), because a canonical observation is not rewritten
    when an attachment moves within its uncertainty. Re-deriving from the
    feature is therefore not a refresh, it is a revert.
    """
    feature = _point_feature(10.0, 4.0)
    index = _Index(by_id={"feature:test/point": feature})
    record = {"record_type": "attachment", "anchor_kind": "freestanding",
              "canonical_point": "feature:test/point",
              "E": 10.5, "N": 4.0,
              "source_E": 10.0, "source_N": 4.0,
              "spatial_reconciliation_m": 0.5}
    pose = attachment.resolve(record, index)
    assert (pose["E"], pose["N"]) == (10.5, 4.0)


def test_resolve_falls_back_to_feature_geometry_without_an_explicit_position():
    """An unreconciled record still resolves the way it always did."""
    feature = _point_feature(10.0, 4.0)
    index = _Index(by_id={"feature:test/point": feature})
    record = {"record_type": "attachment", "anchor_kind": "freestanding",
              "canonical_point": "feature:test/point"}
    pose = attachment.resolve(record, index)
    assert (pose["E"], pose["N"]) == (10.0, 4.0)


def test_resolve_reduces_an_area_to_its_centroid():
    """Area to point is the ring centroid, and stays the ring centroid."""
    ring = _square(6.0, 6.0, 2.0)
    feature = {"feature_id": "feature:test/area",
               "geometry": {"kind": "polygon", "coordinates": ring}}
    index = _Index(by_id={"feature:test/area": feature})
    record = {"record_type": "attachment", "anchor_kind": "freestanding",
              "canonical_point": "feature:test/area"}
    pose = attachment.resolve(record, index)
    assert pose["E"] == pytest.approx(6.0)
    assert pose["N"] == pytest.approx(6.0)


def test_a_carrier_beyond_tolerance_record_is_resolvable():
    """The 5 records that raised KeyError in the one consumer that had to
    resolve them. A record is not attached if nothing can resolve it."""
    feature = _point_feature(50.0, 0.0)
    feature["feature_id"] = "feature:test/isolated"
    feature["semantic_class"] = "fixture"
    mass = {"feature_id": "mass:a", "semantic_class": "building_footprint",
            "geometry": {"kind": "polygon",
                         "coordinates": _square(0.0, 0.0, 1.0)}}
    road = {"feature_id": "carrier:far", "semantic_class": "road",
            "geometry": {"kind": "polyline",
                         "coordinates": [[80.0, -20.0], [80.0, 20.0]]}}
    index = attachment.CanonicalIndex(
        {"canonical_features": [feature, mass, road]})
    record = attachment.attach(feature, index, {"attachment_tolerances": {
        "carrier_association_m": {"value": 12.0},
        "face_resolve_m": {"value": 6.0}}})
    assert record["record_type"] == "attachment"
    assert record["anchor_kind"] == "freestanding"
    assert record["canonical_point"] == "feature:test/isolated"
    pose = attachment.resolve(record, index)
    assert pose["E"] is not None and pose["N"] is not None


# --------------------------------------------------------------------------
# The sigma budget is cumulative, not per-stage.
# --------------------------------------------------------------------------

def test_a_second_stage_cannot_spend_a_second_full_sigma():
    """Two stages each 'within sigma' must not sum to two sigma.

    A feature observed at x=0 has already been moved to x=4.0 by an earlier
    stage, inside its 5.0 m sigma. The nearest clear position from x=4.0 is
    at x=9.0 -- 5.0 m from where the object currently sits, and 9.0 m from
    where it was actually seen. Anchoring the search on the current position
    accepts that. Anchoring it on the observation refuses it.
    """
    index = _Index(buildings=[("mass:a", _square(6.5, 0.0, 2.5))])
    found = attachment._nearest_clear_point_within_sigma(
        4.0, 0.0, 0.0, index, SIGMA, origin=(0.0, 0.0))
    if found is not None:
        assert math.hypot(found[0] - 0.0, found[1] - 0.0) <= SIGMA + 1e-9
        assert found[2] <= SIGMA + 1e-9


def test_the_returned_move_is_measured_from_the_observation():
    """The reported displacement is total, not this stage's increment."""
    index = _Index(buildings=[("mass:a", _square(3.0, 0.0, 1.0))])
    found = attachment._nearest_clear_point_within_sigma(
        2.5, 0.0, 0.0, index, SIGMA, origin=(0.0, 0.0))
    assert found is not None
    assert found[2] == pytest.approx(
        math.hypot(found[0], found[1]), abs=1e-6)


def test_without_an_origin_the_helper_is_unchanged():
    """The A1/A2 callers that have no prior move keep their measured result."""
    index = _Index(buildings=[("mass:a", _square(0.0, 0.0, 1.0))])
    with_default = attachment._nearest_clear_point_within_sigma(
        0.0, 0.0, 0.0, index, SIGMA)
    explicit = attachment._nearest_clear_point_within_sigma(
        0.0, 0.0, 0.0, index, SIGMA, origin=(0.0, 0.0))
    assert with_default == explicit


def test_a_position_already_past_its_budget_is_not_kept_just_for_being_clear():
    """Clear is not the same fact as lawful."""
    index = _Index(buildings=())
    found = attachment._nearest_clear_point_within_sigma(
        9.0, 0.0, 0.0, index, SIGMA, origin=(0.0, 0.0))
    assert found is None


def test_observation_origin_prefers_a_recorded_source_over_the_geometry():
    feature = _point_feature(3.0, 3.0)
    moved = {"source_E": 1.0, "source_N": 1.0}
    assert attachment._observation_origin(feature, moved) == (1.0, 1.0)
    assert attachment._observation_origin(feature, {}) == (3.0, 3.0)


# --------------------------------------------------------------------------
# Segment intersection: the predicate that silently reported nothing.
# --------------------------------------------------------------------------

def test_crossing_segments_are_zero_apart():
    """``_seg_seg_dist`` returned 1.414 for the unit diagonals."""
    assert attachment._seg_seg_dist([0, 0], [2, 2], [0, 2], [2, 0]) == 0.0


def test_segments_intersect_agrees_with_the_polygon_predicate():
    """One owner for the question, not a copy per caller."""
    assert attachment._segments_intersect((0, 0), (2, 2), (0, 2), (2, 0))
    assert not attachment._segments_intersect((0, 0), (1, 0), (0, 1), (1, 1))
    assert attachment._segments_intersect((0, 0), (1, 0), (1, 0), (1, 1))
    assert attachment.polygons_overlap(_square(0.0, 0.0, 1.0),
                                       _square(1.5, 0.0, 1.0))
    assert not attachment.polygons_overlap(_square(0.0, 0.0, 1.0),
                                           _square(5.0, 0.0, 1.0))


def test_polygons_overlap_has_no_private_copy_of_the_predicate():
    assert "def _intersects(" not in SOURCE


# --------------------------------------------------------------------------
# Oriented footprint, not a point and not a radius.
# --------------------------------------------------------------------------

WALL = _square(0.0, 0.0, 5.0)          # mass spanning x,y in [-5, 5]


def test_a_panel_mounted_along_a_wall_is_not_a_wall_conflict():
    """The 96 false positives the radius bound produced.

    A 1.6 x 0.12 m signboard standing 0.7 m off the wall face at x=5. Rotated
    to lie along the wall it protrudes 0.06 m toward it and clears. Its
    circumscribing radius is 0.802 m, which crosses the wall at any yaw, so
    a radius test cannot tell these two cases apart.
    """
    index = _Index(buildings=[("mass:wall", WALL)])
    along = attachment.footprint_mass_conflicts(
        5.7, 0.0, 90.0, 1.6, 0.12, index)
    assert along == []
    assert 0.5 * math.hypot(1.6, 0.12) > 0.7   # a radius test would fire


def test_the_same_panel_turned_into_the_wall_is_a_conflict():
    index = _Index(buildings=[("mass:wall", WALL)])
    into = attachment.footprint_mass_conflicts(
        5.7, 0.0, 0.0, 1.6, 0.12, index)
    assert into == ["mass:wall"]


def test_a_footprint_whose_centre_is_inside_a_mass_conflicts():
    index = _Index(buildings=[("mass:wall", WALL)])
    assert attachment.footprint_mass_conflicts(
        0.0, 0.0, 0.0, 0.6, 0.6, index) == ["mass:wall"]


def test_a_clear_footprint_reports_nothing():
    index = _Index(buildings=[("mass:wall", WALL)])
    assert attachment.footprint_mass_conflicts(
        9.0, 0.0, 0.0, 0.6, 0.6, index) == []


def test_crossed_bars_conflict_although_no_corner_is_inside_anything():
    """The edge-crossing branch on its own.

    A 4.0 x 0.2 m footprint lying east-west across a 0.2 x 4.0 m mass. Every
    footprint corner is outside the mass and every mass corner is outside the
    footprint, so containment finds nothing either way round; only the edges
    meet. Without this case a predicate that had lost edge crossing entirely
    still passed every other test here.
    """
    bar = [[-0.1, -2.0], [0.1, -2.0], [0.1, 2.0], [-0.1, 2.0], [-0.1, -2.0]]
    index = _Index(buildings=[("mass:bar", bar)])
    assert attachment.footprint_mass_conflicts(
        0.0, 0.0, 0.0, 4.0, 0.2, index) == ["mass:bar"]


def test_a_mass_smaller_than_the_footprint_still_conflicts():
    """The third way two rings meet, and the one a corner-and-edge test
    misses: the mass lies entirely inside the asset footprint, so no corner
    is inside it and no edge crosses it."""
    index = _Index(buildings=[("mass:tiny", _square(0.0, 0.0, 0.05))])
    assert attachment.footprint_mass_conflicts(
        0.0, 0.0, 0.0, 2.0, 2.0, index) == ["mass:tiny"]


def test_the_host_mass_is_not_a_collision():
    """A facade fixture is mounted on its building. The building holding it
    up is not something it collides with."""
    index = _Index(buildings=[("mass:wall", WALL)])
    into = attachment.footprint_mass_conflicts(
        5.0, 0.0, 0.0, 1.6, 0.12, index)
    assert into == ["mass:wall"]
    hosted = attachment.footprint_mass_conflicts(
        5.0, 0.0, 0.0, 1.6, 0.12, index, exclude_mass_ids=("mass:wall",))
    assert hosted == []


# --------------------------------------------------------------------------
# The generator consumes freestanding, and refuses when the mesh would not fit.
# --------------------------------------------------------------------------

def _canonical(features: list) -> dict:
    return {"canonical_features": features}


def _template(subtype: str, bbox: list) -> dict:
    return {"physical": {"archetype_subtype": subtype, "native_bbox_m": bbox,
                         "anchor_origin": "ground_center",
                         "orientation_basis": "yaw_only"}}


def _row(feature_id: str, attachment_record: dict) -> dict:
    return {"feature_id": feature_id, "primary": True,
            "owner_stage": "fixtures", "disposition": "mesh_fixture",
            "attachment_state": "resolved",
            "attachment": attachment_record,
            "asset_match": "catalog:tpl.manhole",
            "representation_id": "rep:" + feature_id,
            "evidence_state": "observed"}


def _build(features, rows):
    return generator.build(
        representation_plan={"rows": rows},
        canonical=_canonical(features),
        templates={"tpl.manhole": _template("manhole_cover",
                                            [0.6, 0.6, 0.035])})


def test_the_generator_places_a_freestanding_row():
    """C-3: this kind used to be refused as unsupported."""
    feature = {"feature_id": "feature:test/free",
               "geometry": {"kind": "point", "coordinates": [40.0, 40.0]}}
    rows = [_row("feature:test/free",
                 {"record_type": "attachment", "anchor_kind": "freestanding",
                  "canonical_point": "feature:test/free"})]
    placements, receipt = _build([feature], rows)
    assert receipt["counts"]["unsupported_anchor_kind"] == 0
    assert len(placements) == 1
    assert placements[0]["canonical_feature_id"] == "feature:test/free"
    assert placements[0]["E"] == pytest.approx(40.0)


def test_the_generator_places_a_reconciled_freestanding_row_where_it_was_moved():
    """The whole contract: the move survives into the placement."""
    feature = {"feature_id": "feature:test/free",
               "geometry": {"kind": "point", "coordinates": [40.0, 40.0]}}
    rows = [_row("feature:test/free",
                 {"record_type": "attachment", "anchor_kind": "freestanding",
                  "canonical_point": "feature:test/free",
                  "E": 43.0, "N": 40.0,
                  "source_E": 40.0, "source_N": 40.0})]
    placements, _receipt = _build([feature], rows)
    assert len(placements) == 1
    assert placements[0]["E"] == pytest.approx(43.0)


def test_the_generator_refuses_a_freestanding_row_whose_mesh_sits_in_a_mass():
    """The attachment stage could only test a point. Here the asset is
    known, so the real footprint is testable -- and refused by identity."""
    mass = {"feature_id": "mass:a", "semantic_class": "building_footprint",
            "geometry": {"kind": "polygon", "coordinates": _square(0.0, 0.0,
                                                                  5.0)}}
    feature = {"feature_id": "feature:test/free",
               "geometry": {"kind": "point", "coordinates": [5.1, 0.0]}}
    rows = [_row("feature:test/free",
                 {"record_type": "attachment", "anchor_kind": "freestanding",
                  "canonical_point": "feature:test/free"})]
    placements, receipt = _build([mass, feature], rows)
    assert placements == []
    assert receipt["skipped"]["envelope_conflicts_mass"] == [
        "feature:test/free"]


def test_an_unresolvable_freestanding_row_is_named_not_crashed():
    feature = {"feature_id": "feature:test/free",
               "geometry": {"kind": "point", "coordinates": [40.0, 40.0]}}
    rows = [_row("feature:test/free",
                 {"record_type": "attachment", "anchor_kind": "freestanding"})]
    placements, receipt = _build([feature], rows)
    assert placements == []
    assert receipt["skipped"]["freestanding_unresolvable"] == [
        "feature:test/free"]


def test_the_generator_does_not_reimplement_the_anchor_to_pose_reduction():
    """A second copy of the centroid and yaw rules is how one of them stops
    agreeing with the other."""
    assert "resolve_anchor(attachment, index)" in GENERATOR_SOURCE
    assert "footprint_mass_conflicts(" in GENERATOR_SOURCE
