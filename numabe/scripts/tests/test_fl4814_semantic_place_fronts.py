"""FL-4814 named-place to parent-facade PS1 proposal lowering."""

import shutil
from pathlib import Path

import pytest

from scripts.pipeline import world_ir_ingest_osm_extract as OSM
from scripts.pipeline.compile_facade_shell import _load_a3d_edit
from scripts.pipeline.compile_world_ir_fixtures import compile_world_ir_place_fronts


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"
MACRO_RUN = REPO_ROOT / "assets/meshes/osm_runs/numabe_macro_calibrated_v4_20260913"


def test_named_places_attach_to_parent_facades_and_embed_real_assets(tmp_path):
    required = [
        MACRO_RUN / "classic_base.a3d",
        MACRO_RUN / "terrain.a3d",
        MACRO_RUN / "terrain_metadata.json",
    ]
    if not all(path.exists() for path in required):
        pytest.skip("retained macro-run terrain products are unavailable")

    store, _anchors, _receipt = OSM.build(BUNDLE)
    world_ir = tmp_path / "osm.world_ir.json"
    store.save(world_ir)
    target_map = tmp_path / "places.a3d"
    shutil.copy2(MACRO_RUN / "classic_base.a3d", target_map)
    receipt = compile_world_ir_place_fronts(
        world_ir_path=world_ir,
        terrain_metadata_path=MACRO_RUN / "terrain_metadata.json",
        ground_map_path=MACRO_RUN / "terrain.a3d",
        map_path=target_map,
        meshes_out=tmp_path / "meshes",
        receipt_out=tmp_path / "place_front_receipt.json",
        template_root=REPO_ROOT / "assets" / "world_templates",
        locale_context={
            "country_code": "JP", "region": "Tokyo", "municipality": "Ota",
            "site_contexts": ["urban_residential"], "operator_ids": [],
        },
        route_path=BUNDLE / "valhalla_pedestrian_route.json",
        requirements_path=BUNDLE / "e2e_requirements.json",
    )

    assert receipt["places_considered"] == 16
    assert receipt["place_fronts_emitted"] == 11
    assert receipt["commercial_place_fronts_emitted"] == 10
    assert receipt["place_entry_gate_proposals"] == 1
    # Five Latin source labels fit their measured parent frontage.  The longer
    # Shiba bank label is deliberately withheld instead of becoming illegible.
    assert receipt["place_identity_labels_emitted"] == 5
    assert len(receipt["excluded"]) == 2
    assert {row["reason"] for row in receipt["excluded"]} == {
        "named locality/memorial is a minimap landmark, not a storefront"
    }
    by_id = {row["feature_id"]: row for row in receipt["instances"]}
    expected = {
        "feature:place:node/2684634174": ("My Basket", "feature:building_footprint:way/225369230"),
        "feature:place:node/2929215092": ("FamilyMart", "feature:building_footprint:way/225198068"),
    }
    for feature_id, (label, parent) in expected.items():
        row = by_id[feature_id]
        assert row["label"] == label
        assert row["parent_building_feature_id"] == parent
        assert row["association"] == "containing_building"
        assert row["proposal_status"] == "synthesized_candidate_pending_source_sign_review"
        outward = row["front_outward_normal_xy"]
        presented = row["front_presentation_normal_xy"]
        assert sum(a * b for a, b in zip(outward, presented)) > 0.8
    assert by_id["feature:place:node/2929215092"]["rendered_text"] == "FAMILY MART"
    assert by_id["feature:place:node/2929215092"]["identity_sign"] == {
        "fascia_height_m": 1.2,
        "label_height_m": 0.7,
        "label_width_fraction": 0.94,
    }
    assert by_id["feature:place:node/2929215092"]["resolved_visual_traits"] == [
        "green_white_blue_fascia", "warm_lit_glazing", "source_named_frontage"
    ]
    assert receipt["required_resolved_archetype_audit"] == [
        {
            "feature_id": "feature:place:node/2929215092",
            "profile_id": "tokyo.familymart_front",
            "visual_traits": [
                "green_white_blue_fascia", "source_named_frontage", "warm_lit_glazing"
            ],
            "passed": True,
        },
        {
            "feature_id": "feature:place:node/2684634174",
            "profile_id": "tokyo.mybasket_front",
            "visual_traits": [
                "green_white_green_fascia", "source_named_frontage", "warm_lit_glazing"
            ],
            "passed": True,
        },
    ]
    assert by_id["feature:place:node/2684634174"]["resolved_visual_traits"] == [
        "green_white_green_fascia", "warm_lit_glazing", "source_named_frontage"
    ]
    assert by_id["feature:place:node/2684634174"]["rendered_text"] == "MY BASKET"
    assert by_id["feature:place:node/2684634174"]["identity_sign"] == {
        "fascia_height_m": 1.2,
        "label_height_m": 0.76,
        "label_width_fraction": 0.9,
    }
    # Regression from the rejected Classic review: tenancy points inside
    # irregular footprints selected side/rear walls.  Named fronts must use a
    # street-facing parent edge before they are accepted as visible proposals.
    cocokara = by_id["feature:place:node/11404946450"]
    familymart = by_id["feature:place:node/2929215092"]
    assert cocokara["front_edge_selection"] == "nearest_mapped_road_then_poi_tiebreak"
    assert cocokara["front_road_distance_m"] < 3.0
    assert familymart["front_road_distance_m"] < 1.5
    assert cocokara["front_edge_index"] == 7
    assert familymart["front_edge_index"] == 3
    assert by_id["feature:place:node/2929215092"]["provenance"]["text"] == (
        "OSM name:en rendered with synthesized 5x7 block glyphs"
    )
    assert by_id["feature:place:node/1420789979"]["rendered_text"] is None
    assert by_id["feature:place:node/1420789979"]["provenance"]["text"] == (
        "not rendered; no Latin source label"
    )
    temple = by_id["feature:place:node/2339243972"]
    assert temple["representation_kind"] == "place_entry_gate_proposal"
    assert temple["category"] == "amenity=place_of_worship"
    sequence = {row["label"]: row for row in receipt["route_place_sequence"]}
    assert sequence["FamilyMart"]["route_offset_m"] < 60.0
    assert sequence["My Basket"]["route_offset_m"] < 60.0
    # The temple is a context branch, not a fabricated point on the 428 m
    # pedestrian spine; preserve that distinction in the route inventory.
    assert 60.0 < sequence["有慶山東光院"]["route_offset_m"] < 100.0

    actual = {row.inst_name for row in _load_a3d_edit().read_a3d_sections(target_map)[2]}
    for row in receipt["instances"]:
        assert row["inst_name"] in actual
        assert (tmp_path / "meshes" / row["mesh_name"]).stat().st_size > 0
