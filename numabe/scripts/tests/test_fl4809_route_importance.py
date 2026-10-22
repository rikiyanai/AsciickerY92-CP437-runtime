#!/usr/bin/env python3
"""FL-4809 R1: route-derived importance must reach every canonical feature.

These tests pin the two properties that make the stage worth having:

* a BUILDING can be route_visible.  Before this stage the answer was
  structurally no -- buildings are triage group A, the debt stage scopes itself
  to B/C, and the representation plan defaults an absent debt row to
  ``background`` -- so every one of the 733 buildings was background four
  stages before any facade gate was consulted.
* a POLYGON is measured as a polygon.  A frontage on the route makes a
  building route-visible even when its centroid is beyond the threshold, which
  centroid-only sampling would miss.

They also pin agreement with the existing owner of the law: the threshold is
read from ``compile_observation_debt``, never restated here, so a test that
hardcoded 30.0 could not silently outlive a change to it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF
from scripts.pipeline.compile_observation_debt import ROUTE_VISIBLE_DISTANCE_M
from scripts.pipeline.compile_route_importance import compile_route_importance

# A straight two-point route through the Numabe neighbourhood. Decoded to UTM
# in the test itself, so feature geometry can be placed at exact metre offsets
# from the line and the expected distance is arithmetic, not a guess.
ROUTE_LONLAT = [(139.6900, 35.5700), (139.6930, 35.5700)]


def _encode_polyline6(points):
    """Encode [(lon, lat)] to a precision-6 polyline, matching the decoder."""
    out = []
    prev_lat = prev_lon = 0
    for lon, lat in points:
        for value, previous in ((lat, prev_lat), (lon, prev_lon)):
            scaled = int(round(value * 1e6))
            delta = scaled - previous
            delta = ~(delta << 1) if delta < 0 else (delta << 1)
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        prev_lat = int(round(lat * 1e6))
        prev_lon = int(round(lon * 1e6))
    return "".join(out)


def _write_inputs(tmp_path: Path, features, landmarks=()):
    world_ir = tmp_path / "osm.world_ir.json"
    world_ir.write_text(json.dumps({"canonical_features": features}),
                        encoding="utf-8")
    route = tmp_path / "route.json"
    route.write_text(json.dumps({
        "trip": {"legs": [{"shape": _encode_polyline6(ROUTE_LONLAT)}]}}),
        encoding="utf-8")
    landmark_path = tmp_path / "landmarks.json"
    landmark_path.write_text(
        json.dumps({"landmarks": [{"name": n} for n in landmarks]}),
        encoding="utf-8")
    return world_ir, route, landmark_path


def _route_utm():
    return [GF.utm_forward(lon, lat) for lon, lat in ROUTE_LONLAT]


def _offset_from_route(distance_m, along=0.5):
    """A point exactly ``distance_m`` from the route, perpendicular to it.

    Offsetting by +north instead would be wrong: a constant-latitude route is
    not a constant-northing line in UTM. This fixture's own 270 m segment drops
    3.6 m of northing end to end, so a naive "+29.5 north" sits 31.3 m from the
    segment and lands on the far side of a 30 m threshold.
    """
    (ax, ay), (bx, by) = _route_utm()
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    px, py = ax + dx * along, ay + dy * along
    return [px - uy * distance_m, py + ux * distance_m]


def _feature(fid, semantic_class, coordinates, name=None):
    properties = {"name": name} if name else {}
    return {"feature_id": fid, "semantic_class": semantic_class,
            "properties": properties,
            "geometry": {"coordinates": coordinates, "kind": "test"}}


def _rows(tmp_path, features, landmarks=()):
    world_ir, route, landmark_path = _write_inputs(
        tmp_path, features, landmarks)
    receipt = compile_route_importance(
        world_ir, route, landmark_path, tmp_path / "out.json")
    return {row["feature_id"]: row for row in receipt["rows"]}


def test_building_footprint_can_be_route_visible(tmp_path):
    """The case that was structurally impossible before this stage."""
    def square(distance_m, along):
        a = _offset_from_route(distance_m, along)
        b = _offset_from_route(distance_m, along + 0.01)
        c = _offset_from_route(distance_m + 2.0, along + 0.01)
        d = _offset_from_route(distance_m + 2.0, along)
        return [a, b, c, d]

    features = [
        _feature("feature:building_footprint:way/near", "building_footprint",
                 square(ROUTE_VISIBLE_DISTANCE_M - 5.0, 0.4)),
        _feature("feature:building_footprint:way/far", "building_footprint",
                 square(ROUTE_VISIBLE_DISTANCE_M + 5.0, 0.6)),
    ]
    rows = _rows(tmp_path, features)
    assert rows["feature:building_footprint:way/near"]["importance"] \
        == "route_visible"
    assert rows["feature:building_footprint:way/far"]["importance"] \
        == "background"


def test_polygon_is_measured_by_its_nearest_edge_not_its_centroid(tmp_path):
    """A frontage on the route wins even when the centroid is beyond it.

    The building runs away from the route so its centroid sits outside the
    threshold while its near edge sits inside. Centroid-only sampling would
    call this background and drop a building the player walks past.
    """
    near_edge = ROUTE_VISIBLE_DISTANCE_M - 2.0
    depth = ROUTE_VISIBLE_DISTANCE_M * 3.0
    features = [
        _feature("feature:building_footprint:way/deep", "building_footprint",
                 [_offset_from_route(near_edge, 0.45),
                  _offset_from_route(near_edge, 0.55),
                  _offset_from_route(near_edge + depth, 0.55),
                  _offset_from_route(near_edge + depth, 0.45)]),
    ]
    row = _rows(tmp_path, features)["feature:building_footprint:way/deep"]
    centroid_distance = near_edge + depth / 2.0
    assert centroid_distance > ROUTE_VISIBLE_DISTANCE_M, \
        "fixture must place the centroid outside the threshold"
    assert row["importance"] == "route_visible"
    assert row["route_distance_m"] <= ROUTE_VISIBLE_DISTANCE_M


def test_threshold_boundary_follows_the_owner_constant(tmp_path):
    """Just inside is visible, just outside is background, no hardcoded 30."""
    features = [
        _feature("feature:fixture:node/inside", "fixture",
                 _offset_from_route(ROUTE_VISIBLE_DISTANCE_M - 0.5)),
        _feature("feature:fixture:node/outside", "fixture",
                 _offset_from_route(ROUTE_VISIBLE_DISTANCE_M + 0.5)),
    ]
    rows = _rows(tmp_path, features)
    assert rows["feature:fixture:node/inside"]["importance"] == "route_visible"
    assert rows["feature:fixture:node/outside"]["importance"] == "background"


def test_landmark_name_outranks_distance(tmp_path):
    """A named landmark stays a landmark however far from the route it sits."""
    features = [
        _feature("feature:place:node/shrine", "place",
                 _offset_from_route(ROUTE_VISIBLE_DISTANCE_M * 10.0),
                 name="Sakurazaka"),
    ]
    rows = _rows(tmp_path, features, landmarks=("Sakurazaka",))
    row = rows["feature:place:node/shrine"]
    assert row["importance"] == "landmark"
    assert row["route_distance_m"] > ROUTE_VISIBLE_DISTANCE_M


def test_geometryless_feature_is_background_with_a_stated_basis(tmp_path):
    """Absence of geometry is reported, never silently scored as near."""
    features = [{"feature_id": "feature:place:node/bare",
                 "semantic_class": "place", "properties": {},
                 "geometry": {"coordinates": None, "kind": "test"}}]
    row = _rows(tmp_path, features)["feature:place:node/bare"]
    assert row["importance"] == "background"
    assert row["positions_measured"] == 0
    assert "no geometry" in row["basis"]


def test_receipt_records_the_law_it_applied(tmp_path):
    """A consumer must be able to read which threshold produced these rows."""
    features = [_feature("feature:fixture:node/a", "fixture",
                         _offset_from_route(1.0))]
    world_ir, route, landmark_path = _write_inputs(tmp_path, features)
    receipt = compile_route_importance(
        world_ir, route, landmark_path, tmp_path / "out.json")
    assert receipt["law"]["route_visible_distance_m"] \
        == ROUTE_VISIBLE_DISTANCE_M
    assert receipt["counts"]["features"] == 1
    assert receipt["counts"]["by_semantic_class"]["fixture"]["route_visible"] \
        == 1
