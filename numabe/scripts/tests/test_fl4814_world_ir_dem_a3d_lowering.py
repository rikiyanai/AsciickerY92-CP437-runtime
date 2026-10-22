import json
import math
from pathlib import Path

from scripts.pipeline import geo_frames as GF
from scripts.pipeline.apply_world_ir_dem_to_a3d import fmt, lower
from scripts.pipeline.osm_projection import world_to_latlon
from scripts.pipeline.world_ir import WorldIRStore, schema as S


def _base_run(tmp_path: Path) -> Path:
    run = tmp_path / "base_run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({
        "bbox": {"min_lat": 35.58, "max_lat": 35.59, "min_lon": 139.67, "max_lon": 139.68}
    }))
    (run / "terrain_metadata.json").write_text(json.dumps({
        "scene_lat": 35.585,
        "scene_lon": 139.675,
        "content_scale": GF.WORLD_UNITS_PER_METRE,
        "terrain_shift": {"x": 0.0, "y": 0.0},
        "engine_offset_x": 0.0,
        "engine_offset_y": 0.0,
        "terrain_bounds": {"min_x": 0, "min_y": 0, "max_x": 16, "max_y": 8},
    }))
    return run


def _base_map(path: Path) -> bytes:
    patches = [fmt.A3DPatch(0, 0), fmt.A3DPatch(1, 0)]
    tail = b"post-patch-payload-must-survive"
    with path.open("wb") as fh:
        fmt.A3DHeader(len(patches)).write(fh)
        for patch in patches:
            patch.write(fh)
        fh.write(tail)
    return tail


def _world_ir(path: Path, run: Path) -> None:
    metadata = json.loads((run / "terrain_metadata.json").read_text())
    samples = []
    for x in (0.0, 8.0, 16.0):
        for y in (0.0, 8.0):
            lat, lon = world_to_latlon(
                x, y, metadata["scene_lat"], metadata["scene_lon"],
                metadata["content_scale"], 0.0, 0.0,
            )
            easting, northing = GF.utm_forward(lon, lat)
            zoom = 15
            n = 2 ** zoom
            global_x = int((lon + 180.0) / 360.0 * n * 256.0)
            lat_rad = math.radians(lat)
            global_y = int(
                (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
                / 2.0 * n * 256.0
            )
            samples.append({
                "easting": easting,
                "northing": northing,
                "elevation_m": 10.0 + x / 4.0 + y / 8.0,
                "tile": f"{zoom}/{global_x // 256}/{global_y // 256}",
                "cell": [global_x % 256, global_y % 256],
            })
    store = WorldIRStore.new("dem-test", generated_at="2026-09-12T00:00:00Z")
    frame = GF.Y9WorldFrame.anchored_at_lonlat(139.675, 35.585, "test anchor")
    GF.declare_frames(store, frame, vertical_datum=GF.VERTICAL_DATUM_GSI)
    obs = S.source_observation(
        observation_id="obs:dem:test",
        source_id="dem:test",
        source_kind="point_cloud",
        observed_at="2026-09-12T00:00:00Z",
        attributes={"semantic_class": "terrain"},
        geometry=S.geometry_field_samples(GF.FRAME_PROJECTED, samples, "test lattice"),
    )
    store.add_observation(obs)
    store.add_feature(S.canonical_feature(
        feature_id="feature:terrain:test",
        semantic_class="terrain",
        geometry=S.geometry_field_samples(GF.FRAME_PROJECTED, samples, "test lattice"),
        confidence=1.0,
        gameplay_authority=S.AUTHORITY_WALKABILITY,
        supporting_observation_ids=["obs:dem:test"],
        authority_supporting_properties=["geometry", "semantic_class"],
        properties={
            "geometry": S.property_claim(
                None, S.PROVENANCE_MEASURED, "test",
                observation_ids=["obs:dem:test"],
                uncertainty=S.uncertainty_bounds(0.0, 0.1, "metre"),
            ),
            "semantic_class": S.property_claim("terrain", S.PROVENANCE_MEASURED, "test", observation_ids=["obs:dem:test"]),
        },
    ))
    store.save(path)


def _read(path: Path):
    with path.open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        patches = [fmt.A3DPatch.from_file(fh) for _ in range(header.num_patches)]
        return patches, fh.read()


def test_lowering_is_nonflat_seam_safe_tail_safe_and_deterministic(tmp_path):
    run = _base_run(tmp_path)
    source = tmp_path / "base.a3d"
    tail = _base_map(source)
    world_ir = tmp_path / "dem.world_ir.json"
    _world_ir(world_ir, run)
    out1 = tmp_path / "one.a3d"
    out2 = tmp_path / "two.a3d"
    receipt1 = lower(source, world_ir, run, out1, tmp_path / "one.json")
    receipt2 = lower(source, world_ir, run, out2, tmp_path / "two.json")
    patches, output_tail = _read(out1)

    assert out1.read_bytes() == out2.read_bytes()
    assert output_tail == tail
    assert patches[0].height[0][4] == patches[1].height[0][0]
    assert patches[0].height[4][4] == patches[1].height[4][0]
    assert receipt1["counts"]["seam_mismatches"] == 0
    assert receipt1["elevation"]["compiled_raw_max"] > receipt1["elevation"]["compiled_raw_min"]
    assert receipt1["output_map_sha256"] == receipt2["output_map_sha256"]


def test_lowering_rejects_effectively_flat_source(tmp_path):
    run = _base_run(tmp_path)
    source = tmp_path / "base.a3d"
    _base_map(source)
    world_ir = tmp_path / "dem.world_ir.json"
    _world_ir(world_ir, run)
    doc = json.loads(world_ir.read_text())
    for collection in ("canonical_features", "source_observations"):
        for sample in doc[collection][0]["geometry"]["samples"]:
            sample["elevation_m"] = 10.0
    world_ir.write_text(S.canonical_dumps(doc))

    try:
        lower(source, world_ir, run, tmp_path / "out.a3d", tmp_path / "receipt.json")
    except ValueError as exc:
        assert "effectively flat" in str(exc)
    else:
        raise AssertionError("flat DEM was accepted")
