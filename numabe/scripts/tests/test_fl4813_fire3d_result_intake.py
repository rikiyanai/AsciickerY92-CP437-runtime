import json
import pickle
from pathlib import Path

import numpy as np

from scripts.pipeline.ingest_fire3d_object_proposals import ingest


def _write_inputs(tmp_path: Path):
    target = tmp_path / "targets.json"
    target.write_text(json.dumps({
        "schema": "y92.object_completion_targets.v1",
        "targets": [{
            "schema": "y92.object_completion_target.v1",
            "target_id": "fixture:street_cluster", "backend": "fire3d_single_image_v1",
            "semantic_targets": ["vending_machine", "utility_pole"],
            "budget": {"maximum_job_usd": 10, "intervention_total_usd": 20},
        }],
    }))
    receipt = tmp_path / "input.json"
    receipt.write_text(json.dumps({
        "schema": "y92.fire3d_single_image_input.v1", "target_id": "fixture:street_cluster",
        "scene_id": "fixture_street_cluster", "source": {"capture_id": "capture:1"},
        "geometry": {"organized_point_shape": [16, 16, 3]},
        "gates": {"input_adapter_ready": True},
    }))
    return target, receipt


def test_preflight_proves_result_contract_without_claiming_inference(tmp_path):
    target, receipt = _write_inputs(tmp_path)
    out = tmp_path / "preflight.json"
    result = ingest(target_path=target, target_id="fixture:street_cluster",
                    input_receipt_path=receipt, run_root=tmp_path / "future",
                    detector_paths=[], output_path=out, preflight=True)
    assert result["gates"]["result_contract_ready"] is True
    assert result["gates"]["paid_inference_started"] is False
    assert result["gates"]["world_ir_promotion_ready"] is False


def test_executed_result_associates_instance_but_withholds_world_ir(tmp_path):
    target, receipt = _write_inputs(tmp_path)
    run = tmp_path / "run"; scene = "fixture_street_cluster"
    perception = run / "perception/single_image" / scene
    reconstruction = run / "reconstruction" / scene
    perception.mkdir(parents=True); reconstruction.mkdir(parents=True)
    (run / "resolved_protocol.json").write_text(json.dumps({
        "protocol": {"name": "fire3d_single_image_v1"}
    }))
    (perception / "raw_oriented_bboxes.json").write_text(json.dumps({
        "objects": [{"index": 1, "translate": [1, 2, 3],
                     "rotation": [1, 0, 0, 0], "scale": [1, 1, 2], "score": .9}]
    }))
    labels = np.zeros((2, 2), dtype=np.int64); labels[:, :1] = 1
    with (perception / "point_instance_masks.pkl").open("wb") as stream:
        pickle.dump({"pred": labels.reshape(-1)}, stream)
    mesh = reconstruction / "vending.glb"; mesh.write_bytes(b"glTF-fixture")
    (reconstruction / "inference_summary.json").write_text(json.dumps({
        "status": "complete", "objects": [{"object_id": 1, "textured_glb": str(mesh)}]
    }))
    detections = tmp_path / "detections.json"
    detections.write_text(json.dumps({"observations": [{
        "capture_id": "capture:1", "observation_id": "det:1",
        "bbox_xyxy_normalized": [0, 0, .5, 1],
        "semantic_candidates": ["vending_machine"],
    }]}))
    result = ingest(target_path=target, target_id="fixture:street_cluster",
                    input_receipt_path=receipt, run_root=run,
                    detector_paths=[detections], output_path=tmp_path / "result.json",
                    actual_cost_usd=2.5)
    assert result["counts"]["candidate_objects"] == 1
    assert result["target_semantic_hits"] == ["vending_machine"]
    assert result["candidates"][0]["matched_observation_id"] == "det:1"
    assert result["gates"]["result_assets_resolve"] is True
    assert result["gates"]["world_ir_promotion_ready"] is False
