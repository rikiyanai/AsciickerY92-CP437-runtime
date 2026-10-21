"""FL-4814 cheap station/platform/crossing grammar in the direct Classic path."""

import json
import shutil
from pathlib import Path

import pytest

from scripts.pipeline import world_ir_ingest_osm_extract as OSM
from scripts.pipeline.compile_facade_shell import _load_a3d_edit
from scripts.pipeline.compile_world_ir_fixtures import (
    _cluster_crossing_features,
    compile_world_ir_infrastructure,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"
MACRO_RUN = REPO_ROOT / "assets/meshes/osm_runs/numabe_macro_calibrated_v4_20260913"


def test_per_track_crossing_nodes_collapse_to_one_physical_candidate():
    rows = [
        ({"feature_id": f"crossing/{index}"}, {"name": "Numabe"}, point)
        for index, point in enumerate(((0.0, 0.0), (1.0, 0.2), (2.0, 0.1), (3.0, 0.3)))
    ]
    groups = _cluster_crossing_features(rows, radius_world=4.0)
    assert len(groups) == 1
    assert len(groups[0]) == 4


def test_numabe_station_grammar_embeds_platform_and_one_crossing(tmp_path):
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
    target_map = tmp_path / "station_grammar.a3d"
    shutil.copy2(MACRO_RUN / "classic_base.a3d", target_map)

    receipt = compile_world_ir_infrastructure(
        world_ir_path=world_ir,
        terrain_metadata_path=MACRO_RUN / "terrain_metadata.json",
        ground_map_path=MACRO_RUN / "terrain.a3d",
        map_path=target_map,
        meshes_out=tmp_path / "meshes",
        receipt_out=tmp_path / "infrastructure_receipt.json",
        locale_context={
            "country_code": "JP", "region": "Tokyo", "municipality": "Ota",
            "operator_ids": ["tokyu"], "site_contexts": ["railway_station"],
        },
        template_root=REPO_ROOT / "assets" / "world_templates",
    )

    assert receipt["platform_proposals"] == 1
    assert receipt["station_entrance_proposals"] == 2
    assert receipt["station_identity_labels_emitted"] == 2
    assert receipt["crossing_equipment_proposals"] == 1
    assert receipt["rail_fence_proposals"] == 1
    assert receipt["rail_fence_posts"] >= 20
    assert receipt["bridge_track_proposals"] == 3
    assert receipt["bridge_piers"] > 0
    assert receipt["tunnel_tracks_withheld"] == 0
    assert receipt["watercourse_edge_proposals"] == 3
    assert receipt["watercourse_tunnels_withheld"] == 3
    assert receipt["crossing_source_points_grouped"] == 4
    proposals = {row["semantic_class"]: row for row in receipt["instances"]
                 if row.get("proposal_status")}
    assert proposals["station_platform_proposal"]["dimensions"]["platform_count"] == 2
    assert proposals["station_platform_proposal"]["dimensions"]["platform_height_m"] == .94
    assert proposals["station_platform_proposal"]["feature_id"] == (
        "feature:place:node/7834193975"
    )
    assert proposals["railway_crossing_equipment_proposal"]["feature_id"] == (
        "feature:crossing:node/2145028052"
    )
    assert proposals["rail_corridor_fence_proposal"]["feature_id"] == (
        "feature:place:node/7834193975"
    )
    entrance_rows = [row for row in receipt["instances"]
                     if row["semantic_class"] == "station_entrance_assembly_proposal"]
    assert {row["dimensions"]["entrance_ref"] for row in entrance_rows} == {"1", "2"}
    assert {row["dimensions"]["rendered_identity_text"] for row in entrance_rows} == {
        "NUMABE STA"
    }
    assert all(row["dimensions"]["identity_template_id"] ==
               "jp.tokyu.station_identity_v1" for row in entrance_rows)
    assert all(row["provenance"]["collision"] == "none; presentation only"
               for row in proposals.values())

    editor = _load_a3d_edit()
    actual = {row.inst_name for row in editor.read_a3d_sections(target_map)[2]}
    for proposal in proposals.values():
        assert proposal["inst_name"] in actual
        assert (tmp_path / "meshes" / proposal["mesh_name"]).stat().st_size > 0
    on_disk = json.loads((tmp_path / "infrastructure_receipt.json").read_text())
    assert on_disk["stage"] == "executed_artifact_attested"
