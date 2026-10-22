"""FL-4813 semantic-place retention for the expanded Numabe source bundle."""

from pathlib import Path

from scripts.pipeline import world_ir_ingest_osm_extract as OSM
from scripts.pipeline.compile_facade_shell import _load_a3d_edit
from scripts.pipeline.compile_world_ir_classic_base import compile_base


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"
IR_ROOT = REPO_ROOT / "assets/meshes/osm_runs/numabe_home_to_station_sakurazaka_world_ir_20260913"


def test_named_station_shops_survive_as_places_without_inventing_signs():
    store, _anchors, receipt = OSM.build(BUNDLE)
    by_id = {feature["feature_id"]: feature for feature in store.features}

    expected = {
        "feature:place:node/2684634174": ("My Basket", "shop=supermarket"),
        "feature:place:node/2929215092": ("FamilyMart", "shop=convenience"),
    }
    for feature_id, (label, category) in expected.items():
        feature = by_id[feature_id]
        assert feature["semantic_class"] == "place"
        assert feature["labels"]["name:en"] == label
        assert feature["properties"]["place_category"]["value"] == category
        assert feature["gameplay_authority"] == "presentation_only"
        assert feature["geometry"]["kind"] == "point"

    assert receipt["features_by_class"]["place"] >= 2
    assert not any(feature_id.replace("feature:place:", "") == row["source_ref"]
                   for feature_id in expected for row in receipt["unmapped"])


def test_place_identity_does_not_materialize_a_sign_or_fixture():
    store, _anchors, _receipt = OSM.build(BUNDLE)
    shop_refs = {"node/2684634174", "node/2929215092"}
    for feature in store.features:
        if any(feature["feature_id"].endswith(ref) for ref in shop_refs):
            assert feature["semantic_class"] == "place"
    assert not any(
        feature["semantic_class"] in {"sign", "fixture"}
        and any(feature["feature_id"].endswith(ref) for ref in shop_refs)
        for feature in store.features
    )


def test_station_and_stop_positions_survive_only_as_platform_priors():
    store, _anchors, _receipt = OSM.build(BUNDLE)
    by_id = {feature["feature_id"]: feature for feature in store.features}
    station = by_id["feature:place:node/7834193975"]
    assert station["properties"]["place_category"]["value"] == "public_transport=station"
    assert station["gameplay_authority"] == "presentation_only"
    stops = [
        by_id["feature:place:node/266085809"],
        by_id["feature:place:node/2145028048"],
    ]
    assert all(feature["gameplay_authority"] == "presentation_only" for feature in stops)
    assert all("platform prior" in feature["properties"]["semantic_class"]["method"]
               for feature in stops)
    assert not any(feature["semantic_class"] == "railway_boundary"
                   and feature["geometry"]["kind"] == "point" for feature in store.features)


def test_named_shops_are_read_back_from_the_actual_a3d_marker_payload(tmp_path):
    store, _anchors, _receipt = OSM.build(BUNDLE)
    osm_ir = tmp_path / "osm.world_ir.json"
    store.save(osm_ir)
    receipt = compile_base(
        osm_ir,
        IR_ROOT / "dem5a.world_ir.json",
        BUNDLE / "numabe_home_to_station_sakurazaka_aoi.geojson",
        REPO_ROOT / "assets/a3d/game_map_y8.a3d",
        tmp_path / "base",
    )
    editor = _load_a3d_edit()
    _pre, _version, _instances, _start, _gens, markers = editor.read_a3d_sections(
        Path(receipt["output"]["map"])
    )
    actual = {(marker.name, marker.label, marker.glyph) for marker in markers}
    assert ("feature:place:node/2684634174", "My Basket", "P") in actual
    assert ("feature:place:node/2929215092", "FamilyMart", "P") in actual
