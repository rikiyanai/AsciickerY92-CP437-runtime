"""FL-4876 station/track card fix: facade plane vs rails, S409 hosts, duplicates.

Evidence for each decision lives in the authored owner files; these tests hold
the code guards and the owner data to the review in
artifacts/experiments/fl4876_manual_world/assess/card_review_20261016/station_track.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.pipeline import compile_station_facade_plan as station
from scripts.pipeline.authored_mapillary_decisions import (
    MapillaryDecisionInvalid,
    load_mapillary_decisions,
)
from scripts.pipeline.compile_representation_plan import (
    MAPILLARY_SAME_CLASS_MERGE_M,
    mapillary_class_key,
)
from scripts.pipeline.spatial_coherence_review import (
    SpatialDecisionInvalid,
    load_decisions,
)

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
RECONSTRUCTION = (
    ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"
    / "station_facade_reconstruction.json")
BUILD = ROOT / ".run/fl4876_builds/build_reg_88_temple"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _route_through_rails():
    """Straight stand-in for route tokyu_tamagawa_0 (EPSG:32654 E,N), through
    two measured vertices of build_reg_88 output.trains.json, bearing 139.57."""
    a = (379770.17736977973, 3938493.457278543)
    b = (379771.43399429123, 3938491.9822207745)
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    return [[(a[0] - ux * 80, a[1] - uy * 80), (a[0] + ux * 80, a[1] + uy * 80)]]


# --- facade plane vs rail routes -------------------------------------------

def test_segment_distance_detects_crossing_and_clearance():
    assert station._segment_distance((0, -1), (0, 1), (-1, 0), (1, 0)) == 0.0
    assert station._segment_distance((0, 0), (1, 0), (0, 2), (1, 2)) == pytest.approx(2.0)


def test_guard_refuses_plane_across_the_rails():
    reconstruction = _json(RECONSTRUCTION)
    ends = station._plane_ends_utm(reconstruction, 4.7, 0.0)  # the reg_87 plane
    with pytest.raises(station.FacadeCrossesRail, match="crosses the rails"):
        station.assert_plane_clear_of_rails(ends, _route_through_rails())


def test_authored_decision_places_plane_clear_of_the_rails():
    reconstruction = _json(RECONSTRUCTION)
    decision = next(
        row for row in _json(BUNDLE / "authored_spatial_coherence_decisions.json")["decisions"]
        if row["decision_id"] == "fl4876-manual-station-facade-outward")
    params = decision["parameters"]
    ends = station._plane_ends_utm(
        reconstruction, params["outward_offset_m"], params["lateral_shift_m"])
    clearance = station.assert_plane_clear_of_rails(ends, _route_through_rails())
    assert clearance >= station.RAIL_CLEARANCE_M
    # facing is kept: the plane stays end-on to the rails (photographs nmb00/nmb01)
    (x0, y0), (x1, y1) = ends
    plane_bearing = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180
    assert abs(plane_bearing - (139.57 + 90) % 180) < 3.0


def test_decision_loader_bounds_lateral_shift(tmp_path):
    document = _json(BUNDLE / "authored_spatial_coherence_decisions.json")
    for row in document["decisions"]:
        if row["decision_id"] == "fl4876-manual-station-facade-outward":
            row["parameters"]["lateral_shift_m"] = 9.5
    bad = tmp_path / "decisions.json"
    bad.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpatialDecisionInvalid, match="lateral_shift_m"):
        load_decisions(bad)
    assert load_decisions(BUNDLE / "authored_spatial_coherence_decisions.json")


# --- Mapillary decision owner -----------------------------------------------

def _row(**over):
    row = {"decision_id": "MLY-T-001", "mapillary_object_id": "123", "action": "reject",
           "basis": "looked at it", "reviewed_by": "tester", "reviewed_on": "2026-10-16",
           "evidence": ["a.png"]}
    row.update(over)
    return row


def _doc(tmp_path, *rows):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({
        "schema": "y92.authored_mapillary_object_decisions.v1",
        "decisions": list(rows)}), encoding="utf-8")
    return path


def test_decision_loader_accepts_and_keys_by_feature(tmp_path):
    table = load_mapillary_decisions(_doc(tmp_path, _row()), known_feature_ids={
        "feature:mapillary_object/123"})
    assert list(table) == ["feature:mapillary_object/123"]


@pytest.mark.parametrize("over,message", [
    ({"action": "delete"}, "action"),
    ({"basis": ""}, "basis"),
    ({"reviewed_on": "yesterday"}, "reviewed_on"),
    ({"evidence": []}, "evidence"),
    ({"target_feature_id": "feature:x"}, "exactly one"),
    ({"action": "move_to"}, "move_to"),
])
def test_decision_loader_refuses_bad_rows(tmp_path, over, message):
    with pytest.raises(MapillaryDecisionInvalid, match=message):
        load_mapillary_decisions(_doc(tmp_path, _row(**over)))


def test_decision_loader_refuses_unknown_feature_and_repeat(tmp_path):
    with pytest.raises(MapillaryDecisionInvalid, match="does not carry"):
        load_mapillary_decisions(_doc(tmp_path, _row()), known_feature_ids={"feature:other"})
    with pytest.raises(MapillaryDecisionInvalid, match="second time"):
        load_mapillary_decisions(_doc(tmp_path, _row(), _row(decision_id="MLY-T-002")))


def test_committed_decisions_cover_the_review():
    table = load_mapillary_decisions(BUNDLE / "authored_mapillary_object_decisions.json")
    assert len(table) == 19
    for mid in ("549495619815002", "549495629815001", "549495616481669"):
        assert f"feature:mapillary_object/{mid}" in table
    for node in ("9865199049", "9865199050"):
        assert f"feature:station_entrance:node/{node}" in table
    assert table["feature:mapillary_object/549495629815001"]["action"] == "reject"
    assert all(row["action"] in ("reject", "minimap_only") for row in table.values())


def test_same_class_key_collapses_traffic_light_variants():
    light = lambda value: {"semantic_class": "fixture", "properties": {
        "object_value": {"value": value}}}
    assert mapillary_class_key(light("object--traffic-light--general-upright-front")) == \
        mapillary_class_key(light("object--traffic-light--other"))
    assert mapillary_class_key(light("object--sign--store")) != \
        mapillary_class_key(light("object--sign--advertisement"))
    assert MAPILLARY_SAME_CLASS_MERGE_M == 1.5


# --- authored owner data ----------------------------------------------------

def test_s409_rows_no_longer_share_the_far_platform_host():
    fixtures = {f["fixture_id"]: f for f in _json(BUNDLE / "authored_scene_fixtures.json")["fixtures"]}
    expected = {
        "016": 225563758, "018": 225563758, "019": 225563760, "020": 225563759,
        "021": 225563759, "022": 225563760, "023": 225563760, "025": 225563759,
        "026": 225563760, "027": 225563759, "028": 225563759,
        "029": 225563759, "031": 225563759, "032": 225563759, "033": 225563759,
    }
    for number, host in expected.items():
        assert fixtures[f"SF-4876-S409-{number}"]["carrier_id"] == host, number
    far = [fid for fid, f in fixtures.items()
           if fid.startswith("SF-4876-S409-") and f["carrier_id"] == 225563757
           and fid[-3:] not in ("014", "015", "017")]
    assert far == []


def test_duplicate_crossing_equipment_rows_are_signed_rejections():
    fixtures = {f["fixture_id"]: f for f in _json(BUNDLE / "authored_scene_fixtures.json")["fixtures"]}
    for rejected, kept in (("SF-4876-0157", "SF-4876-0069"), ("SF-4876-0024", "SF-4876-0080")):
        review = fixtures[rejected]["manual_review"]
        assert review["status"] == "rejected"
        assert review["reviewed_by"] and kept in review["reject_reason"]
        assert fixtures[kept]["manual_review"]["status"] == "accepted"


def test_barrier_arm_on_the_platform_wall_is_omitted():
    document = _json(BUNDLE / "authored_observation_positions.json")
    assert all(row["decision_id"] != "POS-4876-029" for row in document["positions"])
    omission = next(row for row in document["omissions"] if row["decision_id"] == "OMIT-4876-005")
    assert omission["instance_id_at_decision"] == "inst-0485"
    # no other row may claim the omitted annotation
    claimed = {aid for row in document["positions"] for aid in row["annotation_ids"]}
    assert not claimed & set(omission["annotation_ids"])


# --- representation plan on the real lane (skipped without the build) --------

@pytest.mark.skipif(not (BUILD / "plan/rules_resolved.json").is_file(),
                    reason="build_reg_88_temple inputs absent")
def test_plan_applies_decisions_suppresses_crossings_and_records_merges(tmp_path):
    from scripts.pipeline import compile_representation_plan as plan_module
    args = [
        "--canonical", str(BUILD / "canonical.world_ir.json"),
        "--entities", str(BUILD / "canonical_entities.json"),
        "--debt", str(BUILD / "observation_debt.json"),
        "--triage-receipt", str(BUILD / "osm_ingest_receipt.json"),
        "--rules", str(BUILD / "plan/rules_resolved.json"),
        "--osm-ir", str(BUILD / "osm.world_ir.json"),
        "--module-catalog", str(ROOT / "artifacts/experiments/fl4813_capture_review_20260915/preserved/numabe_full_727_mapping_20260915/generic_module_catalog.json"),
        "--pack", str(ROOT / "assets/world_templates/japan_urban/pack.json"),
        "--route", str(BUNDLE / "valhalla_pedestrian_route.json"),
        "--terrain-metadata", str(BUILD / "terrain_metadata.json"),
        "--fusion-reviews", str(BUNDLE / "fusion_reviews.json"),
        "--jev-decisions", str(BUILD / "plan/jev_decisions.json"),
        "--route-importance", str(BUILD / "route_importance.json"),
        "--aoi", str(BUILD / "aoi.direct.geojson"),
        "--authored-waivers", str(BUNDLE / "authored_representation_waivers.json"),
        "--authored-tenancy", str(BUNDLE / "authored_place_tenancy.json"),
        "--authored-mapillary-decisions", str(BUNDLE / "authored_mapillary_object_decisions.json"),
        "--authored-fixtures", str(BUNDLE / "authored_scene_fixtures.json"),
        "--out", str(tmp_path / "plan.json"), "--receipt-out", str(tmp_path / "receipt.json"),
        "--owner-dir", str(tmp_path / "by_owner"),
    ]
    assert plan_module.main(args) == 0
    rows = {r["feature_id"]: r for r in _json(tmp_path / "plan.json")["rows"] if r["primary"]}
    receipt = _json(tmp_path / "receipt.json")
    assert rows["feature:mapillary_object/549495619815002"]["disposition"] == "minimap_only"
    assert rows["feature:mapillary_object/549495629815001"]["disposition"] == "intentional_omission"
    assert rows["feature:station_entrance:node/9865199050"]["disposition"] == "minimap_only"
    assert receipt["authored_mapillary_decisions"]["unapplied"] == []
    assert len(receipt["authored_mapillary_decisions"]["applied"]) == 19
    # infrastructure no longer owns a crossing proposal where authored gates stand
    owned = _json(tmp_path / "by_owner/infrastructure.json")["rows"]
    assert not [r for r in owned if r["semantic_class"] == "crossing"]
    assert receipt["crossing_proxy_suppressed_by_authored_gates"]["count"] == 4
    # no two surviving same-class Mapillary fixtures stand within the merge radius
    canonical = {f["feature_id"]: f for f in _json(BUILD / "canonical.world_ir.json")["canonical_features"]}
    kept = [fid for fid, r in rows.items()
            if fid.startswith("feature:mapillary_object/") and r["disposition"] == "mesh_fixture"]
    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            ca, cb = canonical[a], canonical[b]
            if mapillary_class_key(ca) != mapillary_class_key(cb):
                continue
            (xa, ya), (xb, yb) = ca["geometry"]["coordinates"][:2], cb["geometry"]["coordinates"][:2]
            assert math.hypot(xa - xb, ya - yb) > MAPILLARY_SAME_CLASS_MERGE_M, (a, b)
    assert receipt["mapillary_same_class_merges"]["count"] == len(
        receipt["mapillary_same_class_merges"]["merges"])
