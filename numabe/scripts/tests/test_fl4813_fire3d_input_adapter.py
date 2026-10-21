"""Bounded Fire3D input adapter contracts."""

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.pipeline.prepare_fire3d_single_image_input import prepare


def _write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fire3d_adapter_writes_exact_organized_point_contract(tmp_path):
    source = tmp_path / "source"
    image_path = source / "frame.jpg"
    source.mkdir()
    Image.new("RGB", (32, 32), (80, 90, 100)).save(image_path)
    selection = _write(tmp_path / "selection.json", {"captures": [{
        "capture_id": "frame", "local_path": "frame.jpg", "sha256": _sha(image_path),
        "license": "test", "attribution": "test",
    }]})
    geometry = tmp_path / "geometry.npz"
    np.savez(geometry, depth=np.full((1, 16, 16), 5.0, np.float32),
             intrinsics=np.asarray([[[8.0, 0.0, 8.0], [0.0, 8.0, 8.0], [0.0, 0.0, 1.0]]]))
    geometry_receipt = _write(tmp_path / "geometry.json", {
        "stage": "executed_unaccepted_proposal", "backend": "test_depth",
        "world_ir_promotion_ready": False,
        "outputs": {"geometry": {"sha256": _sha(geometry)}},
    })
    camera = _write(tmp_path / "camera.json", {
        "global_solve": {"optimizer_status": 1, "control_point_constraints": 3},
        "cameras": [{
            "capture_id": "frame", "solved_rotation_rodrigues": [0.0, 0.0, 0.0],
            "solved_center_enu_m": [1.0, 2.0, 3.0],
        }],
    })
    spec = _write(tmp_path / "spec.json", {
        "schema": "y92.object_completion_target.v1", "target_id": "fixture:test",
        "backend": "fire3d_single_image_v1", "capture_id": "frame",
        "source_root": str(source), "capture_selection": str(selection),
        "geometry_npz": str(geometry), "geometry_receipt": str(geometry_receipt),
        "camera_registration": str(camera), "semantic_targets": ["fixture"],
        "cheap_baseline": {"mesh": "fixture.akm"},
        "budget": {"maximum_job_usd": 10.0, "intervention_total_usd": 20.0},
    })
    output = tmp_path / "output"
    receipt = prepare(spec_path=spec, output_root=output, repo_root=tmp_path)
    scene = output / "single_image/data/fixture_test"
    assert (scene / "rgb.jpeg").is_file()
    assert (scene / "aligned_pcd.ply").read_text().splitlines()[2] == "element vertex 256"
    assert json.loads((scene / "camera.json").read_text())["schema"] == (
        "fire3d_single_image_camera_v1"
    )
    assert receipt["geometry"]["organized_point_shape"] == [16, 16, 3]
    assert receipt["gates"]["input_adapter_ready"] is True
    assert receipt["gates"]["paid_inference_started"] is False
    assert receipt["gates"]["world_ir_promotion_ready"] is False
