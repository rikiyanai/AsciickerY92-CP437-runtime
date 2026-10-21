#!/usr/bin/env python3
"""FL-4876 SPATIAL-F1. One OSM node is one object.

``build_street_entities`` matched each Mapillary dedup group to the globally
nearest OSM point of the same class, with no check that the node had already
lent its position to another group.

On the Numabe lane one traffic-signal node, ``feature:fixture:node/442165549``,
was claimed by 16 distinct dedup groups: it lent its exact position to 4
canonical entities and generated spatial reconciliation debt against 12 more.
Measured across the lane, **9 canonical street objects sat at just 3
coordinates** -- stacked exactly on top of one another, each of which would
serialize as a separate mesh instance at an identical transform.

Two different wrongs follow from one defect, and both are pinned here:

* several objects claiming one position is duplicated geometry in the world;
* a residual measured against a node another object already consumed is not
  this object's displacement, so it is not evidence and must not become debt.

The tests drive ``build_street_entities`` through its two data seams so each
case states its own geometry, and they check the budget is untouched.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.pipeline.compile_canonical_entities as entities  # noqa: E402

BUDGETS = {"fixture": 4.0}


def _sighting(mid: str, x: float, y: float, cls: str = "fixture",
              value: str = "traffic_light", group: str | None = None) -> dict:
    return {
        "feature_id": f"feature:mapillary_object/{mid}",
        "mid": mid,
        "semantic_class": cls,
        "object_value": value,
        "utm": (x, y),
        "frames_seen": [f"frame-{mid}"],
        "dedup_group": group or f"group-{mid}",
        "obs_ids": [f"obs-{mid}"],
    }


@pytest.fixture
def lane(monkeypatch):
    """Supply the sightings and OSM points directly."""
    state: dict = {"sightings": [], "points": {}}
    monkeypatch.setattr(entities, "_mapillary_sightings",
                        lambda _path: list(state["sightings"]))
    monkeypatch.setattr(
        entities, "_osm_points",
        lambda _path, classes: {
            fid: point for fid, point in state["points"].items()
            if point["class"] in classes})
    return state


def _build(lane_state):
    return entities.build_street_entities(
        Path("osm.json"), Path("mapillary.json"), BUDGETS)


def _disposition(entity: dict) -> str:
    debt = ((entity.get("claims") or {}).get(
        "spatial_reconciliation_debt") or {}).get("value")
    if isinstance(debt, dict) and debt.get("osm_candidate"):
        return "fused_debt"
    if any(s.get("source") == "osm" for s in entity.get("source_bridge") or []):
        return "merged_into_osm"
    return "fused_separate"


def _positions(built) -> Counter:
    result = Counter()
    for entity in built[0]:
        position = (entity.get("claims") or {}).get("position") or {}
        result[tuple(position.get("value") or ())] += 1
    return result


# --------------------------------------------------------------------------
# The defect: one node, many objects.
# --------------------------------------------------------------------------

def test_one_node_lends_its_position_to_at_most_one_object(lane):
    """Four sightings around one mapped node, as at a real intersection.

    All four are within the 4.0 m fixture budget of the same node. The old
    code merged all four, giving four canonical entities at one identical
    coordinate.
    """
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [
        _sighting("a", 0.5, 0.0), _sighting("b", 1.5, 0.0),
        _sighting("c", 2.5, 0.0), _sighting("d", 3.5, 0.0),
    ]
    built = _build(lane)
    assert sum(count for count in _positions(built).values() if count > 1) == 0, (
        "two canonical objects share one coordinate")
    dispositions = Counter(_disposition(e) for e in built[0])
    assert dispositions["merged_into_osm"] == 1


def test_the_closest_sighting_wins_the_node(lane):
    """Nearest-first, so the result does not depend on dedup group order."""
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    # Deliberately ordered so the nearest is last by feature id.
    lane["sightings"] = [_sighting("zzz", 0.5, 0.0), _sighting("aaa", 3.5, 0.0)]
    built = _build(lane)
    merged = [e for e in built[0] if _disposition(e) == "merged_into_osm"]
    assert len(merged) == 1
    assert any("zzz" in str(s.get("source_object_id"))
               for s in merged[0]["source_bridge"])


def test_a_second_object_takes_its_nearest_unconsumed_node(lane):
    """Losing the nearest node is not losing every node."""
    lane["points"] = {
        "feature:fixture:node/1": {"class": "fixture", "utm": (0.0, 0.0)},
        "feature:fixture:node/2": {"class": "fixture", "utm": (3.0, 0.0)},
    }
    lane["sightings"] = [_sighting("a", 0.5, 0.0), _sighting("b", 1.5, 0.0)]
    built = _build(lane)
    assert Counter(_disposition(e) for e in built[0])["merged_into_osm"] == 2
    assert all(count == 1 for count in _positions(built).values())


# --------------------------------------------------------------------------
# The evidence claim: a consumed node is not a residual.
# --------------------------------------------------------------------------

def test_a_consumed_node_does_not_become_another_object_s_debt(lane):
    """The 12-debts-against-one-node case, in miniature.

    The far sighting is 6.0 m from the node: past the 4.0 m budget but
    inside the 8.0 m search radius, so it used to be recorded as
    fused_debt naming a node that already belonged to the near sighting.
    """
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting("near", 0.5, 0.0), _sighting("far", 6.0, 0.0)]
    built = _build(lane)
    dispositions = Counter(_disposition(e) for e in built[0])
    assert dispositions["merged_into_osm"] == 1
    assert dispositions["fused_debt"] == 0
    assert dispositions["fused_separate"] == 1


def test_an_unconsumed_node_inside_the_search_radius_is_still_debt(lane):
    """Exclusivity must not silence a real contradiction.

    Nothing has consumed this node, so a sighting past the budget but inside
    the search radius is still a genuine unexplained residual.
    """
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting("far", 6.0, 0.0)]
    built = _build(lane)
    assert Counter(_disposition(e) for e in built[0])["fused_debt"] == 1


# --------------------------------------------------------------------------
# The budget, unchanged.
# --------------------------------------------------------------------------

def test_a_pair_outside_the_class_budget_still_does_not_merge(lane):
    """Exclusivity is a matching rule, not permission to reach further."""
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting("a", 4.25, 0.0)]
    built = _build(lane)
    assert Counter(_disposition(e) for e in built[0])["merged_into_osm"] == 0


def test_the_budget_boundary_is_where_it_was(lane):
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting("a", 4.0, 0.0)]
    built = _build(lane)
    assert Counter(_disposition(e) for e in built[0])["merged_into_osm"] == 1


def test_only_a_merge_consumes_a_node(lane):
    """A node is reserved by the object that actually adopts its position.

    If the reservation were taken on a wider radius than the merge budget, a
    node no object merged with would still be locked away -- and the second
    object's genuine residual against it would be silenced into
    fused_separate. Both of these are past the 4.0 m budget and inside the
    8.0 m search radius, so both are real unexplained residuals and both
    must say so.
    """
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting("p", 5.0, 0.0), _sighting("q", 7.0, 0.0)]
    built = _build(lane)
    dispositions = Counter(_disposition(e) for e in built[0])
    assert dispositions["merged_into_osm"] == 0
    assert dispositions["fused_debt"] == 2
    assert dispositions["fused_separate"] == 0


def test_a_node_of_another_class_is_never_matched(lane):
    lane["points"] = {"feature:sign/1": {"class": "sign", "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting("a", 0.5, 0.0, cls="fixture")]
    built = _build(lane)
    assert Counter(_disposition(e) for e in built[0])["merged_into_osm"] == 0


def test_no_object_is_lost_when_nodes_run_out(lane):
    """Every sighting still produces exactly one canonical entity."""
    lane["points"] = {"feature:fixture:node/1": {"class": "fixture",
                                                 "utm": (0.0, 0.0)}}
    lane["sightings"] = [_sighting(str(i), 0.5 * i, 0.0) for i in range(1, 7)]
    built = _build(lane)
    assert len(built[0]) == 6
