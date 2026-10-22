"""FL-4876 GRAVEYARD-01 / AUTHORED-SCENE-MESH-01: the temple graveyard terraces.

authored_scene_fixtures.json rows with emission = authored_scene_mesh are carrier-less scenery.  The identity resolver
skips them (the population planner would turn them into carrier_beyond_tolerance debt); the building bake emits them on
the terrain at their E/N and yaw with the population geometry builder's grave_terrace_row_block.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.bake_osm_building_footprints import _emit_authored_scene_meshes, fmt, write_a3d  # noqa: E402
from scripts.pipeline.population_asset_geometry import (  # noqa: E402
    CUSTOM_METRIC_FAMILY, CUSTOM_VARIANTS, GRAVE_BLOCK_X_M, GRAVE_BLOCK_Y_M, GRAVE_COLS, GRAVE_ROWS,
    build_custom_geometry)
from scripts.pipeline.resolve_identity_entities import load_adjudication  # noqa: E402

BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
KEY = ("stone_marker_bollard", "grave_terrace_row_block")


def test_grave_terrace_block_is_a_registered_custom_variant_with_the_stated_extent():
    assert KEY in CUSTOM_VARIANTS and KEY[0] in CUSTOM_METRIC_FAMILY
    v, f = build_custom_geometry(*KEY, scale=1.0)
    xs, ys, zs = [p[0] for p in v], [p[1] for p in v], [p[2] for p in v]
    assert max(xs) - min(xs) == pytest.approx(GRAVE_BLOCK_X_M + 0.08)       # coping overhangs 0.04 m each end
    assert max(ys) - min(ys) == pytest.approx(GRAVE_BLOCK_Y_M + 0.06, abs=0.1)
    assert min(zs) == pytest.approx(-0.6) and max(zs) == pytest.approx(0.9 + 1.08)
    assert all(p[9] == 255 for p in v), "scenery is presentation only (no collision)"
    # 4 rows x 6 plots, one gravestone shaft box (colour 118,116,112: 6 faces x 4 vertices) per plot
    assert sum(1 for p in v if tuple(p[6:9]) == (118, 116, 112)) == 24 * GRAVE_ROWS * GRAVE_COLS


def _ground(tmp_path: Path) -> Path:
    patches = []
    for py in range(4):
        for px in range(4):
            p = fmt.A3DPatch(x=px, y=py)
            for row in p.height:
                for i in range(len(row)):
                    row[i] = 200
            patches.append(p)
    path = tmp_path / "ground.a3d"
    write_a3d(path, fmt.A3DHeader(len(patches)), patches, b"")
    return path


def _fixture(fid, E, N, emission="authored_scene_mesh"):
    row = {"fixture_id": fid, "module_id": KEY[0], "variant": KEY[1], "E": E, "N": N, "carrier_id": None,
           "yaw_deg": 51.0, "position_uncertainty_m": 3.0,
           "manual_review": {"status": "accepted", "reviewed_by": "t", "reviewed_on": "2026-10-16",
                             "module_basis": "m", "position_basis": "p", "carrier_basis": "c", "orientation_basis": "o"}}
    if emission:
        row["emission"] = emission
    return row


def test_scene_fixture_is_emitted_on_the_terrain_with_its_yaw(tmp_path):
    ground = _ground(tmp_path)
    meta = tmp_path / "terrain_metadata.json"
    meta.write_text(json.dumps({"content_scale": 2.0, "offset_x": 4.0, "offset_y": 6.0, "origin_easting": 1000.0,
                                "origin_northing": 2000.0}))
    fx = tmp_path / "fixtures.json"
    fx.write_text(json.dumps({"fixtures": [_fixture("SF-T-1", 1010.0, 2005.0), _fixture("SF-T-2", 1011.0, 2006.0, None)]}))
    instances, receipts = _emit_authored_scene_meshes(fx, meta, ground, tmp_path / "meshes")
    assert len(instances) == 1 and receipts[0]["fixture_id"] == "SF-T-1"      # the ordinary fixture is not ours
    t = instances[0]["transform"]
    assert t[12:14] == pytest.approx([(1010 - 1000) * 2 + 4, (2005 - 2000) * 2 + 6])
    assert (t[0], t[1], t[4], t[5]) == pytest.approx((math.cos(math.radians(51)), math.sin(math.radians(51)),
                                                      -math.sin(math.radians(51)), math.cos(math.radians(51))))
    assert t[10] == float(fmt.HEIGHT_SCALE) and t[14] == 200          # grounded on the terrain raw height there
    mesh = tmp_path / "meshes" / instances[0]["mesh_name"]
    text = mesh.read_text()
    assert "alpha=255" in text and int(text.split("element face ")[1].split()[0]) > 1000


def test_identity_resolver_skips_scene_mesh_fixtures(tmp_path):
    (tmp_path / "authored_observation_positions.json").write_text(json.dumps({}))
    (tmp_path / "authored_scene_fixtures.json").write_text(json.dumps({"fixtures": [
        _fixture("SF-T-1", 1.0, 2.0), _fixture("SF-T-2", 3.0, 4.0, None)]}))
    adj = load_adjudication(tmp_path / "authored_observation_positions.json")
    ids = [f["fixture_id"] for f in adj["scene_fixtures"]]
    assert ids == ["SF-T-2"] and not adj["scene_fixture_debt"] and not adj["scene_fixture_rejections"]


def test_shipped_graveyard_fixtures_are_rows_on_the_row_bearing():
    doc = json.loads((BUNDLE / "authored_scene_fixtures.json").read_text())
    g = [f for f in doc["fixtures"] if str(f["fixture_id"]).startswith("SF-4876-G")]
    assert len(g) >= 10
    for f in g:
        assert f["emission"] == "authored_scene_mesh" and (f["module_id"], f["variant"]) == KEY
        assert f["manual_review"]["status"] == "accepted" and f["yaw_deg"] == 51.0   # rows along compass bearing 39
        # inside the map region NW/N of the hall (E379783 N3938600) and within the cemetery polygon's bounding box
        assert 379715 < f["E"] < 379795 and 3938600 < f["N"] < 3938660
