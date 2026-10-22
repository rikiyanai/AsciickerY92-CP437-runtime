"""FL-4876 REG-EDGE-01: observation registration rays, routing, rail edges, stable ids.

Unit checks pin the helpers. The real-data check runs register_observations.py
on the bundled Numabe inputs and pins the placements that were checked by eye
against their photos (artifacts/experiments/fl4876_fixture_visual/):
rail fence obs-inst-0119 stands NE of Tamagawa way/24475081, rail fence
obs-inst-0114 stands SW of it, and crosswalk guardrail obs-inst-0083 is no
longer registered on a rail line.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.pipeline import register_observations as R  # noqa: E402


@pytest.mark.parametrize("mapping, rail", [
    ({"module_id": "pedestrian_guardrail_continuous", "placement_context": "carriageway_edge"}, False),
    ({"module_id": "zebra_crossing", "placement_context": "road_crossing"}, False),
    ({"module_id": "utility_pole_wire_cluster", "placement_context": "roadside_utility_corridor"}, False),
    ({"module_id": "vending_white_red_blue", "placement_context": "station_or_storefront_forecourt"}, False),
    ({"module_id": "rail_corridor_fence", "placement_context": "rail_or_property_corridor_edge"}, True),
    ({"module_id": "platform_railing", "placement_context": "station_platform_edge"}, True),
    ({"module_id": "level_crossing_equipment", "placement_context": "level_crossing_approach"}, True),
    ({"module_id": "station_name_face_ja_en"}, True),
])
def test_rail_routing_matches_whole_tokens(mapping, rail):
    assert R.is_rail_row(mapping) is rail
    assert (R.carrier_order(mapping)[0] == "railway") is rail


def test_edge_modules():
    assert R.is_edge_module({"module_id": "rail_corridor_fence"})
    assert R.is_edge_module({"module_id": "pedestrian_guardrail_continuous"})
    assert not R.is_edge_module({"module_id": "utility_pole_wire_cluster"})


def test_instance_ids_stay_stable_through_split_and_merge():
    ledger = {"annotation_to_instance": {"a": "inst-0000", "b": "inst-0000", "c": "inst-0001",
                                         "d": "inst-0002", "e": "inst-0003"}}
    # inst-0000 splits (a | b), inst-0002 and inst-0003 merge (d+e), inst-0001 unchanged
    ids = R.assign_instance_ids([["a"], ["b"], ["c"], ["d", "e"]], ledger)
    assert ids[2] == "inst-0001"
    assert {ids[0], ids[1]} == {"inst-0000", "inst-0004"}
    assert ids[3] in {"inst-0002", "inst-0003"}
    assert len(set(ids)) == 4
    assert R.assign_instance_ids([["x"], ["y"]], None) == ["inst-0000", "inst-0001"]


def test_authored_non_osm_carrier_is_explicit_and_not_inferred():
    doc = {"authored_carriers": [{
        "carrier_id": "canonical:station",
        "carrier_class": "building",
        "polyline_epsg32654": [[1, 2], [3, 4], [1, 2]],
        "source_feature_id": "feature:building_footprint:plateau/station",
        "basis": "reviewed measured footprint",
    }]}
    carrier = R.authored_carriers(doc)[0]
    assert carrier["id"] == "canonical:station"
    assert carrier["class"] == "building"
    assert carrier["segments"] == [((1.0, 2.0), (3.0, 4.0)),
                                    ((3.0, 4.0), (1.0, 2.0))]
    assert carrier["authored_basis"] == "reviewed measured footprint"


def _edges():
    # straight way running north (tx, ty) = (0, 1); left edge 3 m (west), right edge 7 m (east)
    st = [[float(s), 0.0, float(s), 0.0, 1.0, 3.0, 7.0] for s in range(0, 101, 2)]
    return R.RailCorridorEdges({"parameters": {"step": 2.0}, "ways": {"1": {"stations": st}}})


def test_inside_corridor_side_comes_from_image_side_not_absolute_bearing():
    edges = _edges()
    # cab camera on the track heading north (5 deg heading error); box right of image centre
    ray = math.radians(2.0)  # absolute ray points slightly LEFT of the true track direction
    moved = R.rail_edge_position(edges, 0.5, 10.0, math.sin(ray), math.cos(ray), 20.0, 1,
                                 150.0, rel_bearing_deg=+7.0, heading_deg=355.0)
    assert moved is not None and moved[4] == "inside_corridor_perpendicular"
    assert moved[1] == pytest.approx(7.0, abs=0.01)  # east = right of a north-bound train
    # same box seen from a south-bound train: right of the image is west
    moved = R.rail_edge_position(edges, 0.5, 60.0, 0.0, -1.0, 20.0, 1,
                                 150.0, rel_bearing_deg=+7.0, heading_deg=180.0)
    assert moved[1] == pytest.approx(-3.0, abs=0.01)


def test_outside_camera_takes_first_edge_and_rejects_shallow_crossings():
    edges = _edges()
    moved = R.rail_edge_position(edges, 20.0, 50.0, -1.0, 0.0, 20.0, 1, 150.0, 0.0, 270.0)
    assert moved is not None and moved[4] == "outside_corridor_first_edge"
    assert moved[1] == pytest.approx(7.0, abs=0.01)
    shallow = math.radians(270.0 + 80.0)  # 10 deg off the way direction
    assert R.rail_edge_position(edges, 20.0, 50.0, math.sin(shallow), math.cos(shallow),
                                 120.0, 1, 150.0, 0.0, 350.0) is None


@pytest.fixture(scope="module")
def registered(tmp_path_factory):
    out = tmp_path_factory.mktemp("reg")
    bundle = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
    ledger = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/"
              "numabe_full_727_mapping_20260915/observation_module_mapping.jsonl")
    argv = sys.argv
    sys.argv = ["register_observations.py", "--ledger", str(ledger),
                "--osm", str(bundle / "numabe_connected_neighborhood_aoi.osm"), "--out-dir", str(out)]
    try:
        assert R.main() == 0
    finally:
        sys.argv = argv
    return {i["instance_id"]: i for i in json.loads((out / "registered_instances.json").read_text())}


def _signed_left(e, n, way_id, edges):
    st = edges.station(way_id, e, n)
    return (e - st[1]) * -st[4] + (n - st[2]) * st[3]


def test_photo_only_rows_do_not_emit_geometry(registered):
    registered_anns = {a for i in registered.values() for a in i["annotation_ids"]}
    # These rows used to be ray-cast onto the rail corridor or a nearby road.
    # They have no explicit authored placement, so the strict manual lane must
    # retain them as evidence/debt rather than create geometry.
    assert "ann-1789503222948-2d1a52" not in registered_anns
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    explicitly_placed = {a for row in doc["positions"] for a in row["annotation_ids"]}
    assert registered_anns <= explicitly_placed


def test_authored_hand_placements_apply_by_annotation(registered):
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    by_ann = {a: i for i in registered.values() for a in i["annotation_ids"]}
    placed = {a: d for d in doc["positions"] for a in d["annotation_ids"]}
    for inst in registered.values():
        members = [placed[a] for a in inst["annotation_ids"] if a in placed]
        if not members:
            continue
        # One hand-placed annotation registers at its authored E/N; repeated
        # sightings of one object cluster into one instance at the member mean.
        mean_e = sum(d["E"] for d in members) / len(members)
        mean_n = sum(d["N"] for d in members) / len(members)
        assert math.hypot(inst["E"] - mean_e, inst["N"] - mean_n) < 0.01
        assert inst["carrier_id"] in {d["carrier_id"] for d in members}


def test_authored_reclassification_sets_the_module(registered):
    # JEV-VISUAL-01 POS-4876-034: the 0136 photo shows two small trees, not a hedge.
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    by_ann = {a: i for i in registered.values() for a in i["annotation_ids"]}
    rows = [d for d in doc["positions"] if d.get("module_id_override")]
    assert rows
    for d in rows:
        for aid in d["annotation_ids"]:
            assert by_ann[aid]["module_id"] == d["module_id_override"]


def test_authored_omissions_are_not_registered(registered):
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    registered_anns = {a for i in registered.values() for a in i["annotation_ids"]}
    omitted = {a for d in doc.get("omissions", []) for a in d["annotation_ids"]}
    assert omitted and not (omitted & registered_anns)


def test_unboxed_manual_objects_enter_as_exact_authored_instances(registered):
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    rows = doc.get("authored_objects") or []
    assert rows
    for row in rows:
        inst = registered[row["instance_id"]]
        assert inst["module_id"] == row["module_id"]
        assert inst["carrier_id"] == row["carrier_id"]
        assert inst["E"] == pytest.approx(row["E"])
        assert inst["N"] == pytest.approx(row["N"])
        assert inst["yaw_deg"] == pytest.approx(row["yaw_deg"])
        assert inst["capture_ids"] == sorted(set(row["capture_ids"]))
        assert inst["position_uncertainty_basis"] == "manual_authored_object"
        assert inst["annotation_ids"] == []


def test_equirectangular_box_height_angle_spans_180_degrees():
    from scripts.pipeline.compile_custom_asset_plan import _angular_extent
    w, h = _angular_extent({"projection_model": "equirectangular", "width": 1024, "height": 512},
                           [0.4, 0.4, 0.1, 0.1])
    assert w == pytest.approx(36.0) and h == pytest.approx(18.0)


def test_every_annotation_geometry_has_an_authored_decision(registered):
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    decision_by_ann = {
        ann: row["decision_id"] for row in doc["positions"] for ann in row["annotation_ids"]
    }
    for inst in registered.values():
        for ann in inst["annotation_ids"]:
            assert ann in decision_by_ann


def test_unauthored_identity_never_enters_the_generic_fixture_registrar(registered):
    ledger = (ROOT / "artifacts/experiments/fl4813_capture_review_20260915/"
              "preserved/numabe_full_727_mapping_20260915/"
              "observation_module_mapping.jsonl")
    identity = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (row.get("mapping") or {}).get("population_role") == "custom_identity":
            identity.add(row["annotation_id"])
    doc = json.loads((ROOT / R.DEFAULT_AUTHORED_POSITIONS).read_text())
    explicitly_placed = {a for row in doc["positions"] for a in row["annotation_ids"]}
    registered_anns = {a for i in registered.values() for a in i["annotation_ids"]}
    assert identity
    # MANUAL-WORLD-03 admits an annotation once a hand placement names its
    # E/N and carrier -- identity rows included. Unauthored identity rows
    # stay delegated and never become geometry on their own.
    assert not ((identity - explicitly_placed) & registered_anns)
    by_ann = {a: i for i in registered.values() for a in i["annotation_ids"]}
    adjudicated = {a for row in doc.get("identities", [])
                   for a in row.get("annotation_ids", [])}
    for aid in identity & explicitly_placed:
        # Identity evidence is the reviewed subject or a visual identity
        # adjudication (mapped_place/business) resolved by the resolver.
        assert by_ann[aid]["custom_identity_subject"] or aid in adjudicated
