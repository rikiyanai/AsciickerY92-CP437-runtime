"""FL-4814 Tokyo residential road-edge surface prior."""

import json
import shutil
from pathlib import Path

import pytest

from scripts.pipeline import world_ir_ingest_osm_extract as OSM
from scripts.pipeline.osm_carto_stamper import stamp_world_ir


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"
MACRO_RUN = REPO_ROOT / "assets/meshes/osm_runs/numabe_macro_calibrated_v4_20260913"


def test_residential_roads_get_visible_edge_strips_without_geometry_authority(tmp_path):
    required = [
        MACRO_RUN / "terrain.a3d",
        MACRO_RUN / "terrain_metadata.json",
        MACRO_RUN / "source_palette.json",
    ]
    if not all(path.exists() for path in required):
        pytest.skip("retained macro-run surface products are unavailable")

    store, _anchors, _receipt = OSM.build(BUNDLE)
    world_ir = tmp_path / "osm.world_ir.json"
    store.save(world_ir)
    target_map = tmp_path / "surface.a3d"
    shutil.copy2(MACRO_RUN / "terrain.a3d", target_map)
    palette = json.loads((MACRO_RUN / "source_palette.json").read_text())

    receipt = stamp_world_ir(
        world_ir,
        MACRO_RUN / "terrain_metadata.json",
        target_map,
        palette,
        tmp_path,
    )

    edge_links = [
        component
        for feature in receipt["features"]
        for component in feature["generated_components"]
        if component["kind"] == "residential_edge_strip"
    ]
    # Thirty-six roads resolve to the residential role; one is an area, so the
    # bilateral line-strip treatment applies to the remaining 35 polylines.
    assert receipt["synthesized_road_edge_strips"] == len(edge_links) == 35
    assert receipt["render_features"] == len(receipt["features"]) + 35
    assert receipt["height_changed"] is False
    assert all(component["visible_each_side_m"] == pytest.approx(0.6)
               for component in edge_links)
    assert all(component["authority"] == "presentation_only" for component in edge_links)
    assert receipt["roles"]["per_role"]["footway"] > 0
