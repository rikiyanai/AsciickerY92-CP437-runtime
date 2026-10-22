import json
from pathlib import Path

import pytest

from scripts.pipeline.compile_connected_product_scope import compile_scope
from scripts.pipeline.source_manifest import load_manifest, verify


ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"


def test_numabe_successor_scope_is_connected_and_extends_the_route(tmp_path):
    output = tmp_path / "scope.geojson"
    receipt_path = tmp_path / "receipt.json"
    document, receipt = compile_scope(SCOPE / "scope_spec.json", output, receipt_path)

    assert receipt["passed"] is True
    assert all(receipt["checks"].values())
    assert receipt["metrics"]["aoi_connected_components"] == 1
    assert receipt["metrics"]["route_points"] == 52
    assert receipt["metrics"]["route_length_m"] == pytest.approx(1016.882, abs=0.01)
    assert receipt["metrics"]["aoi_area_m2"] > 95_768.0
    assert receipt["annotation_snapshot_verification"] == {
        "digest_matches": True,
        "count_matches": True,
        "annotations": 499,
        "crops_verified": 499,
        "failures": [],
    }
    assert receipt["route_landmark_traversal"] == [{
        "landmark_id": "sakurazaka",
        "source_ref": "way/37661730",
        "route_distance_m": [0.001, 0.017],
        "max_route_distance_m": 1.0,
        "traversed": True,
    }]

    route = next(
        feature for feature in document["features"]
        if feature["properties"]["role"] == "connected_playable_route"
    )
    assert route["properties"]["ordered_roles"] == [
        "numabe_station_selected_entrance",
        "private_home_block",
        "sakurazaka_full_lower_endpoint",
        "sakurazaka_full_upper_endpoint",
        "lawson_coin_laundry_cluster",
    ]
    base = json.loads(
        (ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913/numabe_home_to_station_sakurazaka_aoi.geojson").read_text()
    )
    old_route = next(
        feature for feature in base["features"]
        if feature["properties"].get("role") == "walking_route"
    )["geometry"]["coordinates"]
    assert route["geometry"]["coordinates"][: len(old_route)] == list(reversed(old_route))
    assert receipt["route_composition"]["preserved_base_route_length_m"] == pytest.approx(
        428.0, abs=0.2
    )
def test_unmapped_coin_laundry_is_a_search_zone_not_a_fake_point(tmp_path):
    document, receipt = compile_scope(
        SCOPE / "scope_spec.json",
        tmp_path / "scope.geojson",
        tmp_path / "receipt.json",
    )
    zones = [
        feature for feature in document["features"]
        if feature["properties"]["role"] == "operator_reported_landmark_search_zone"
    ]
    assert len(zones) == 1
    assert zones[0]["geometry"]["type"] == "Polygon"
    assert zones[0]["properties"]["location_state"] == (
        "operator_reported_unresolved_within_cluster"
    )
    assert receipt["checks"]["coin_laundry_not_claimed_as_mapped"] is True


def test_compiler_output_is_deterministic(tmp_path):
    first = tmp_path / "first.geojson"
    first_receipt = tmp_path / "first.receipt.json"
    second = tmp_path / "second.geojson"
    second_receipt = tmp_path / "second.receipt.json"
    compile_scope(SCOPE / "scope_spec.json", first, first_receipt)
    compile_scope(SCOPE / "scope_spec.json", second, second_receipt)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first_receipt.read_text()) == json.loads(second_receipt.read_text())


def test_connected_scope_source_manifest_verifies():
    manifest = load_manifest(SCOPE / "source_manifest.json")
    assert manifest.manifest_id == "numabe_connected_neighborhood_scope_20260915"
    assert all(row["status"] in {"OK", "SKIP"} for row in verify(manifest, SCOPE))
