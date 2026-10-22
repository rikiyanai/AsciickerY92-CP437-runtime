import struct
from pathlib import Path

import pytest

from scripts.pipeline.compile_world_ir_classic_base import REPO_ROOT, compile_base, fmt


def test_numabe_base_targets_classic_without_legacy_bake_or_launch(tmp_path):
    ir_root = REPO_ROOT / "assets/meshes/osm_runs/numabe_home_to_station_world_ir_20260912"
    bundle = REPO_ROOT / "assets/source_bundles/numabe_home_to_station_20260912"
    receipt = compile_base(
        ir_root / "osm.world_ir.json",
        ir_root / "dem5a.world_ir.json",
        bundle / "numabe_home_to_station_aoi.geojson",
        REPO_ROOT / "assets/a3d/game_map_y8.a3d",
        tmp_path,
    )

    assert receipt["target_runtime"] == "original_cpp_classic"
    assert receipt["legacy_sbu_runner_used"] is False
    assert receipt["asciiid_used"] is False
    assert receipt["runtime_launched"] is False
    assert receipt["counts"]["osm_features"] == 529
    assert receipt["counts"]["dem_features"] == 1
    assert receipt["counts"]["patches"] == 14012
    assert receipt["station"]["label"] == "沼部駅 / Numabe Station"

    with (tmp_path / "classic_base.a3d").open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        for _ in range(header.num_patches):
            fmt.A3DPatch.from_file(fh)
        fh.seek(256 * fmt.A3DMaterial.SIZE, 1)
        assert struct.unpack("<i", fh.read(4))[0] == fmt.WORLD_FORMAT_VERSION
        assert struct.unpack("<i", fh.read(4))[0] == 0
        assert struct.unpack("<i", fh.read(4))[0] == 1
        player = fmt.A3DPlayerStart.from_file(fh)
        assert player.pos[0] == pytest.approx(receipt["spawn"]["x"], abs=1e-4)
        assert player.pos[1] == pytest.approx(receipt["spawn"]["y"], abs=1e-4)
        assert struct.unpack("<i", fh.read(4))[0] == 0
        assert struct.unpack("<i", fh.read(4))[0] == 2
        markers = [fmt.A3DMinimapMarker.from_file(fh) for _ in range(2)]
        assert [marker.name for marker in markers] == ["home_start", "numabe_station"]
        assert markers[1].label == "沼部駅 / Numabe Station"
        assert fh.read() == b""
