"""FL-4815: the scale receipt passes on real cross-checked evidence and fails
when the compiled world scale disagrees with its independent sources."""

import json
from pathlib import Path

from scripts.pipeline.compile_scale_receipt import compile_scale_receipt

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "assets/meshes/osm_runs/numabe_streetscape_phase9_continue_20260914"
IR = ROOT / "assets/meshes/osm_runs/numabe_home_to_station_sakurazaka_world_ir_20260913"
BUNDLE = ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"


def _compile(tmp_path: Path, metadata_src: Path) -> dict:
    return compile_scale_receipt(
        route_walkability_receipt_path=RUN / "route_walkability_receipt.json",
        dem_receipt_path=RUN / "dem_lowering_receipt.json",
        anchor_set_path=IR / "osm.anchor_set.json",
        terrain_metadata_path=metadata_src,
        camera_registration_path=BUNDLE / "station_camera_solve_receipt.json",
        out_path=tmp_path / "scale_receipt.json",
    )


def test_scale_receipt_passes_on_phase9_run(tmp_path):
    receipt = _compile(tmp_path, RUN / "terrain_metadata.json")
    assert receipt["schema"] == "y92.scale_receipt.v1"
    assert receipt["pass"] is True
    assert {check["id"] for check in receipt["checks"]} == {
        "route_length_agreement",
        "avatar_corridor_plausibility",
        "vertical_range_agreement",
        "anchor_metrology",
        "gravity_level_record",
    }
    assert all(check["verdict"] is True for check in receipt["checks"])
    assert (tmp_path / "scale_receipt.json").is_file()


def test_scale_receipt_is_not_circular(tmp_path):
    """A consistently relabelled world-units scale must still fail: the
    anchor chords quoted from the frozen anchor set disagree with it."""
    swapped = tmp_path / "terrain_metadata.json"
    metadata = json.loads((RUN / "terrain_metadata.json").read_text())
    metadata["content_scale"] = 2.25
    swapped.write_text(json.dumps(metadata))
    receipt = _compile(tmp_path, swapped)
    assert receipt["pass"] is False
    anchor = next(
        check
        for check in receipt["checks"]
        if check["id"] == "anchor_metrology"
    )
    assert anchor["verdict"] is False
