"""FL-4809 actual-mesh audit for resolved Japanese streetscape archetypes."""

import json
import math
import shutil
from pathlib import Path

import pytest

from scripts.pipeline import world_ir_ingest_osm_extract as OSM
from scripts.pipeline.classic_procedural_mesh import _build_archetype
from scripts.pipeline.compile_world_ir_fixtures import (
    compile_world_ir_fixtures,
    compile_world_ir_place_fronts,
)
from scripts.pipeline.template_resolver import discover_pack_chain, resolve_template_candidates


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"
MACRO_RUN = ROOT / "assets/meshes/osm_runs/numabe_macro_calibrated_v4_20260913"
TEMPLATES = ROOT / "assets/world_templates"


def _resolved_parameters(semantic_class: str) -> dict:
    context = json.loads((BUNDLE / "e2e_requirements.json").read_text())[
        "locale_contexts"
    ]["route_default"]
    candidate = resolve_template_candidates(
        semantic_candidates=[semantic_class],
        context=context,
        packs=discover_pack_chain(TEMPLATES, context),
    )[0]
    return candidate["parameters"]


def _positions(vertices, color):
    return {(round(row[0], 3), round(row[2], 3)) for row in vertices if row[6:9] == color}


def _akm_vertices(path: Path):
    lines = path.read_text().splitlines()
    header_end = lines.index("end_header")
    count = int(next(line.split()[-1] for line in lines[:header_end]
                     if line.startswith("element vertex ")))
    return [tuple(map(float, line.split()[:6])) + tuple(map(int, line.split()[6:10]))
            for line in lines[header_end + 1:header_end + 1 + count]]


def test_japan_pack_builds_required_silhouettes_and_colors():
    stop_parameters = _resolved_parameters("stop_sign")
    stop_vertices, _faces = _build_archetype("stop_sign", 1.0, stop_parameters)
    border = tuple(stop_parameters["border_rgb"])
    border_positions = _positions(stop_vertices, border)
    assert (0.0, 1.46) in border_positions
    assert (-0.42, 2.26) in border_positions
    assert (0.42, 2.26) in border_positions

    post_parameters = _resolved_parameters("post_box")
    post_vertices, _faces = _build_archetype("post_box", 1.0, post_parameters)
    red = tuple(post_parameters["body_rgb"])
    red_heights = [row[2] for row in post_vertices if row[6:9] == red]
    assert min(red_heights) == pytest.approx(0.72)
    assert max(red_heights) == pytest.approx(1.55)

    board_parameters = _resolved_parameters("information_board")
    board_vertices, _faces = _build_archetype(
        "information_board", 1.0, board_parameters
    )
    roof = tuple(board_parameters["roof_rgb"])
    roof_positions = _positions(board_vertices, roof)
    assert (0.0, 2.12) in roof_positions
    assert (-0.88, 1.72) in roof_positions
    assert (0.88, 1.72) in roof_positions

    regulatory_parameters = _resolved_parameters("statutory_traffic_sign")
    _vertices, regulatory_faces = _build_archetype(
        "regulatory_sign", 1.0, regulatory_parameters
    )
    assert regulatory_faces
    assert all(2 <= len(face) <= 4 for face in regulatory_faces)


def test_actual_fixture_and_familymart_assets_attest_required_traits(tmp_path):
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
    target_map = tmp_path / "archetypes.a3d"
    shutil.copy2(MACRO_RUN / "classic_base.a3d", target_map)
    fixture_receipt = compile_world_ir_fixtures(
        world_ir_path=world_ir,
        requirements_path=BUNDLE / "e2e_requirements.json",
        template_root=TEMPLATES,
        terrain_metadata_path=MACRO_RUN / "terrain_metadata.json",
        map_path=target_map,
        meshes_out=tmp_path / "meshes",
        instances_out=tmp_path / "fixture_instances.json",
        receipt_out=tmp_path / "fixture_receipt.json",
    )
    assert len(fixture_receipt["required_resolved_archetype_audit"]) == 4
    assert all(row["passed"] for row in fixture_receipt["required_resolved_archetype_audit"])
    for row in fixture_receipt["required_resolved_archetype_audit"]:
        assert row["instances_attested"] > 0

    signals = [row for row in json.loads((tmp_path / "fixture_instances.json").read_text())
               if row["semantic_subtype"] == "traffic_signal"]
    assert signals
    assert all(row["physical_offset_m"] >= 3.0 for row in signals)
    assert all(row["road_centerline_clearance_m"] >= 2.25 for row in signals)
    assert all(row["provenance"]["orientation"] == "derived from nearest road frame"
               for row in signals)
    assert all(not math.isclose(row["transform"][0], 1.0)
                   or not math.isclose(row["transform"][1], 0.0)
               for row in signals)

    place_receipt = compile_world_ir_place_fronts(
        world_ir_path=world_ir,
        terrain_metadata_path=MACRO_RUN / "terrain_metadata.json",
        ground_map_path=MACRO_RUN / "terrain.a3d",
        map_path=target_map,
        meshes_out=tmp_path / "meshes",
        receipt_out=tmp_path / "place_receipt.json",
        template_root=TEMPLATES,
        locale_context=json.loads((BUNDLE / "e2e_requirements.json").read_text())[
            "locale_contexts"
        ]["route_default"],
        route_path=BUNDLE / "valhalla_pedestrian_route.json",
        requirements_path=BUNDLE / "e2e_requirements.json",
    )
    assert place_receipt["required_resolved_archetype_audit"][0]["passed"] is True
    familymart = next(
        row for row in place_receipt["instances"]
        if row["feature_id"] == "feature:place:node/2929215092"
    )
    mesh_text = (tmp_path / "meshes" / familymart["mesh_name"]).read_text()
    for rgb in ((0, 146, 79), (244, 244, 232), (0, 145, 211), (214, 222, 184)):
        assert " ".join(map(str, rgb)) in mesh_text
    vertices = _akm_vertices(tmp_path / "meshes" / familymart["mesh_name"])
    yaw = familymart["front_yaw_rad"]
    sine, cosine = math.sin(yaw), math.cos(yaw)
    label_rgb = (21, 112, 160)
    fascia_rgbs = {(0, 146, 79), (244, 244, 232), (0, 145, 211)}
    label_depth = min(-sine * row[0] + cosine * row[1]
                      for row in vertices if row[6:9] == label_rgb)
    fascia_depth = min(-sine * row[0] + cosine * row[1]
                       for row in vertices if row[6:9] in fascia_rgbs)
    assert label_depth < fascia_depth
    label_vertices = [row for row in vertices if row[6:9] == label_rgb]
    assert (max(row[2] for row in label_vertices)
            - min(row[2] for row in label_vertices)) / 16.0 >= 0.65
    assert familymart["identity_sign"]["fascia_height_m"] >= 1.0
