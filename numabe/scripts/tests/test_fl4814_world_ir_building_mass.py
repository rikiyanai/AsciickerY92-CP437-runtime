import json
import math
from pathlib import Path

from scripts.pipeline.compile_world_ir_building_specs import compile_specs
from scripts.pipeline.compile_world_ir_classic_base import REPO_ROOT, compile_base


IR_ROOT = REPO_ROOT / "assets/meshes/osm_runs/numabe_home_to_station_world_ir_20260912"
BUNDLE = REPO_ROOT / "assets/source_bundles/numabe_home_to_station_20260912"


def test_numabe_world_ir_building_specs_account_for_every_feature(tmp_path):
    base = compile_base(
        IR_ROOT / "osm.world_ir.json",
        IR_ROOT / "dem5a.world_ir.json",
        BUNDLE / "numabe_home_to_station_aoi.geojson",
        REPO_ROOT / "assets/a3d/game_map_y8.a3d",
        tmp_path,
    )
    receipt = compile_specs(
        IR_ROOT / "osm.world_ir.json",
        tmp_path / "terrain_metadata.json",
        tmp_path / "building_instances.json",
        tmp_path / "building_specs_receipt.json",
    )
    specs = json.loads((tmp_path / "building_instances.json").read_text())

    assert receipt["counts"]["world_ir_buildings"] == 437
    assert receipt["counts"]["specs_emitted"] == 437
    assert receipt["counts"]["height_provenance"] == {
        "derived": 4,
        "synthesized": 433,
    }
    assert len(specs) == len({row["feature_id"] for row in specs}) == 437
    assert all(row["source"].startswith("way/") for row in specs)
    assert all(row["roof_height_m"] > 0.5 for row in specs)
    bounds = base["projection"]["terrain_bounds"]
    assert all(max(point[0] for point in row["footprint"]) <= bounds["max_x"] for row in specs)
    assert all(max(point[1] for point in row["footprint"]) <= bounds["max_y"] for row in specs)
    from scripts.pipeline import geo_frames as GF
    aoi = json.loads((BUNDLE / "numabe_home_to_station_aoi.geojson").read_text())
    line = next(f for f in aoi["features"] if f["properties"].get("role") == "walking_route")["geometry"]["coordinates"]
    a, b = GF.utm_forward(*line[0]), GF.utm_forward(*line[1])
    spawn = base["spawn"]
    assert spawn["source"] == "walking_route_start"
    yaw = math.radians(spawn["yaw"])
    view = (-math.sin(yaw), math.cos(yaw))
    delta = (b[0]-a[0], b[1]-a[1])
    assert (view[0]*delta[0]+view[1]*delta[1])/math.hypot(*delta) > .999999
    projection = base["projection"]
    assert abs(spawn["x"] - ((a[0]-projection["origin_easting"])*projection["content_scale"]+projection["offset_x"])) < 1e-6
    levels = [row for row in specs if row["height_source_key"] == "building:levels"]
    assert sorted(row["roof_height_m"] for row in levels) == [9.0, 15.0, 15.0, 15.0]
