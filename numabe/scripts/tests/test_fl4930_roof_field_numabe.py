"""FL-4930: compile_lidar_roof_field.py on the Numabe affine frame and World IR.

Scope: the frame adapter, the World IR footprint loader, multi-sheet LAS
loading, the vectorized inset depth, and one synthetic end-to-end roof. These
tests do not touch the TMG sheets and say nothing about any real building.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
sys.path.insert(0, str(ROOT))

import compile_lidar_roof_field as R  # noqa: E402
from scripts.pipeline import geo_frames as GF  # noqa: E402

np = pytest.importorskip("numpy")

AFFINE_META = {
    "content_scale": 3.421334928531115,
    "offset_x": 32.0,
    "offset_y": 32.0,
    "origin_easting": 379699.09478405997,
    "origin_northing": 3938417.876643479,
    "projection_kind": "epsg32654_affine",
    "schema": "y92.classic_base_projection.v1",
}
RUN_NOTE = (
    "run projection from terrain_metadata.json: EPSG:32654 zone 54N easting/northing via "
    "geo_frames Kruger, world = (utm - origin) * 3.421334928531115 + offset "
    "(origin 379699.09478405997,3938417.876643479)"
)


def _scene(tmp_path: Path) -> dict:
    p = tmp_path / "terrain_metadata.json"
    p.write_text(json.dumps(AFFINE_META))
    return R.load_scene_params(p)


def _utm_to_world(scene, e, n):
    s = scene["content_scale"]
    return ((e - scene["origin_easting"]) * s + scene["offset_x"],
            (n - scene["origin_northing"]) * s + scene["offset_y"])


def test_affine_metadata_becomes_affine_scene(tmp_path):
    scene = _scene(tmp_path)
    assert scene["frame_kind"] == "epsg32654_affine"
    assert scene["origin_easting"] == AFFINE_META["origin_easting"]


def test_affine_metadata_with_wrong_schema_is_refused(tmp_path):
    p = tmp_path / "terrain_metadata.json"
    p.write_text(json.dumps({**AFFINE_META, "schema": "something.else"}))
    with pytest.raises(SystemExit):
        R.load_scene_params(p)


def test_affine_world_to_lonlat_round_trips_through_geo_frames(tmp_path):
    scene = _scene(tmp_path)
    for lon, lat in [(139.6745, 35.585), (139.6722, 35.5822), (139.6767, 35.5878)]:
        e, n = GF.utm_forward(lon, lat)
        wx, wy = _utm_to_world(scene, e, n)
        lon2, lat2 = R.world_to_lonlat(scene, wx, wy)
        assert math.isclose(lon2, lon, abs_tol=1e-9)
        assert math.isclose(lat2, lat, abs_tol=1e-9)


def test_sbu_scene_path_is_unchanged():
    scene = {"scene_lat": 40.915, "scene_lon": -73.125, "content_scale": 2.25,
             "shift_x": 10.0, "shift_y": -4.0}
    lat, lon = R.world_to_latlon(500.0, 700.0, 40.915, -73.125, 2.25, 10.0, -4.0)
    assert R.world_to_lonlat(scene, 500.0, 700.0) == (lon, lat)


def test_vectorized_inset_depth_matches_scalar():
    # concave ring with a horizontal and a vertical edge
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (5.0, 4.0), (0.0, 10.0)]
    rng = np.random.default_rng(4930)
    xs = rng.uniform(-3, 13, 2000)
    ys = rng.uniform(-3, 13, 2000)
    xs = np.concatenate([xs, [0.0, 10.0, 5.0, 5.0, 2.5]])
    ys = np.concatenate([ys, [0.0, 10.0, 4.0, 0.0, 0.0]])
    got = R.signed_inset_depth_array(xs, ys, poly)
    want = np.array([R.signed_inset_depth(float(x), float(y), poly) for x, y in zip(xs, ys)])
    assert np.array_equal(np.sign(got), np.sign(want))
    assert np.allclose(got, want, atol=1e-12)


def _ir_doc(features, frames):
    return {"canonical_features": features, "coordinate_frames": frames}


def _feat(source, frame_id, coords):
    return {"feature_id": f"feature:building_footprint:{source}", "semantic_class": "building_footprint",
            "geometry": {"kind": "polygon", "frame_id": frame_id, "coordinates": coords}}


def test_world_ir_loader_places_utm_and_run_frame_footprints(tmp_path):
    scene = _scene(tmp_path)
    e0, n0 = 379800.0, 3938600.0
    utm_ring = [[e0, n0], [e0 + 10, n0], [e0 + 10, n0 + 8], [e0, n0 + 8], [e0, n0]]
    run_ring = [[100.0, 100.0], [130.0, 100.0], [130.0, 120.0]]
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps(_ir_doc([_feat("way/1", "epsg32654", utm_ring)], {"epsg32654": {}})))
    plateau = tmp_path / "plateau.json"
    plateau.write_text(json.dumps(_ir_doc(
        [_feat("plateau/bldg_x", "plateau_run_projection", run_ring)],
        {"plateau_run_projection": {"note": RUN_NOTE}})))
    rows = {src: poly for src, poly, _ in R.load_world_ir_footprints([osm, plateau], scene)}
    assert len(rows["way/1"]) == 4  # closing vertex dropped
    assert rows["way/1"][0] == pytest.approx(_utm_to_world(scene, e0, n0))
    assert rows["plateau/bldg_x"] == [tuple(p) for p in run_ring]


@pytest.mark.parametrize("note", [RUN_NOTE.replace("3.421334928531115", "3.0"), "no parameters here"])
def test_world_ir_loader_refuses_a_run_frame_it_cannot_prove(tmp_path, note):
    scene = _scene(tmp_path)
    doc = tmp_path / "plateau.json"
    doc.write_text(json.dumps(_ir_doc(
        [_feat("plateau/bldg_x", "plateau_run_projection", [[0, 0], [1, 0], [1, 1]])],
        {"plateau_run_projection": {"note": note}})))
    with pytest.raises(SystemExit):
        R.load_world_ir_footprints([doc], scene)


def test_world_ir_loader_refuses_unknown_frame_and_duplicates(tmp_path):
    scene = _scene(tmp_path)
    ring = [[379800.0, 3938600.0], [379810.0, 3938600.0], [379810.0, 3938610.0]]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_ir_doc([_feat("way/1", "epsg4326", ring)], {})))
    with pytest.raises(SystemExit):
        R.load_world_ir_footprints([bad], scene)
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_ir_doc([_feat("way/1", "epsg32654", ring)], {})))
    with pytest.raises(SystemExit):
        R.load_world_ir_footprints([a, a], scene)


def _write_las(path: Path, x, y, z, cls):
    laspy = pytest.importorskip("laspy")
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001, 0.001, 0.001]
    header.offsets = [float(np.floor(np.min(x))), float(np.floor(np.min(y))), 0.0]
    las = laspy.LasData(header)
    las.x, las.y, las.z = x, y, z
    las.classification = np.asarray(cls, dtype=np.uint8)
    las.return_number = np.ones(len(x), dtype=np.uint8)
    las.number_of_returns = np.ones(len(x), dtype=np.uint8)
    las.write(str(path))


def test_multiple_sheets_concatenate_without_loss(tmp_path):
    a, b = tmp_path / "a.las", tmp_path / "b.las"
    _write_las(a, np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.array([1.0, 2.0]), [2, 1])
    _write_las(b, np.array([5.0, 6.0, 7.0]), np.array([5.0, 6.0, 7.0]), np.array([3.0, 4.0, 5.0]), [1, 1, 2])
    X, Y, Z, cls, ret, nret, total = R._load_las_arrays([a, b])
    assert total == 5
    assert list(cls) == [2, 1, 1, 1, 2]
    assert R._load_las_arrays([a, b]) is R._load_las_arrays([a, b])  # cached once


def test_synthetic_roof_six_metres_above_ground_on_the_affine_frame(tmp_path):
    """A 12 x 12 m box roof at +6 m over flat ground reads back as 6 m."""
    from pyproj import Transformer

    scene = _scene(tmp_path)
    e0, n0 = 379800.0, 3938600.0
    side = 12.0
    poly_world = [_utm_to_world(scene, e, n) for e, n in
                  [(e0, n0), (e0 + side, n0), (e0 + side, n0 + side), (e0, n0 + side)]]
    to6677 = Transformer.from_crs("EPSG:32654", "EPSG:6677", always_xy=True)
    gx, gy = np.meshgrid(np.arange(-20.0, side + 20.0, 0.5), np.arange(-20.0, side + 20.0, 0.5))
    ue, un = (e0 + gx).ravel(), (n0 + gy).ravel()
    inside = (ue > e0) & (ue < e0 + side) & (un > n0) & (un < n0 + side)
    sx, sy = to6677.transform(ue, un)
    z = np.where(inside, 16.0, 10.0)
    cls = np.where(inside, 1, 2)
    las = tmp_path / "sheet.las"
    _write_las(las, np.asarray(sx), np.asarray(sy), z, cls)
    field = R.compile_roof_field([las], poly_world, scene, source_crs="EPSG:6677")
    assert field["ground"]["elevation"] == pytest.approx(10.0, abs=1e-3)
    assert [r["elevation_m_above_ground"] for r in field["roof_regions"]] == [pytest.approx(6.0, abs=1e-3)]
    assert field["measured_vertices"] > 0
