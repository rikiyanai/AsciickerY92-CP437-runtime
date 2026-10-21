#!/usr/bin/env python3
"""Ingest one Fire3D run as object proposals, never as World IR truth.

The official single-image release writes raw oriented boxes, a point-instance
label grid, and per-object reconstructed meshes.  This adapter associates those
anonymous instances with the existing image-space detector observations and
records exactly which output assets exist.  A successful import is still only a
proposal: a separate comparison and accepted world placement are required
before an object may become a GeneratedRepresentation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "y92.fire3d_object_proposals.v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_target(path: Path, target_id: str | None) -> dict[str, Any]:
    payload = _load(path)
    if payload.get("schema") == "y92.object_completion_targets.v1":
        matches = [
            row for row in payload.get("targets", [])
            if target_id is None or row.get("target_id") == target_id
        ]
        if len(matches) != 1:
            raise ValueError("target collection requires one unique --target-id")
        payload = matches[0]
    if payload.get("schema") != "y92.object_completion_target.v1":
        raise ValueError("target must use y92.object_completion_target.v1")
    if payload.get("backend") != "fire3d_single_image_v1":
        raise ValueError("target backend is not fire3d_single_image_v1")
    return payload


def _result_paths(run_root: Path, scene_id: str) -> dict[str, Path]:
    perception = run_root / "perception" / "single_image" / scene_id
    reconstruction = run_root / "reconstruction" / scene_id
    return {
        "protocol": run_root / "resolved_protocol.json",
        "raw_obbs": perception / "raw_oriented_bboxes.json",
        "labels": perception / "point_instance_masks.pkl",
        "summary": reconstruction / "inference_summary.json",
    }


def _bbox_iou(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = aa + bb - intersection
    return intersection / union if union > 0.0 else 0.0


def _instance_bboxes(label_path: Path, width: int, height: int) -> dict[int, list[float]]:
    # The file is produced by the pinned Fire3D process that this adapter just
    # ran.  It is never accepted from an arbitrary upload or network response.
    with label_path.open("rb") as stream:
        payload = pickle.load(stream)  # noqa: S301 - trusted local model output
    labels = np.asarray(payload["pred"], dtype=np.int64).reshape(-1)
    grid_w, grid_h = width // 16, height // 16
    if labels.size != grid_w * grid_h:
        raise ValueError(
            f"Fire3D point-instance grid has {labels.size} labels; "
            f"expected {grid_w}x{grid_h}"
        )
    grid = labels.reshape(grid_h, grid_w)
    result: dict[int, list[float]] = {}
    for ident in sorted(int(v) for v in np.unique(grid) if int(v) > 0):
        ys, xs = np.nonzero(grid == ident)
        result[ident] = [
            float(xs.min() / grid_w), float(ys.min() / grid_h),
            float((xs.max() + 1) / grid_w), float((ys.max() + 1) / grid_h),
        ]
    return result


def _detector_rows(path: Path, capture_id: str) -> list[dict[str, Any]]:
    payload = _load(path)
    return [
        row for row in payload.get("observations", [])
        if row.get("capture_id") == capture_id
    ]


def _normal_bbox(row: dict[str, Any], width: int, height: int) -> list[float]:
    if len(row.get("bbox_xyxy_normalized", [])) == 4:
        return [float(v) for v in row["bbox_xyxy_normalized"]]
    x0, y0, x1, y1 = (float(v) for v in row["bbox_xyxy_px"])
    return [x0 / width, y0 / height, x1 / width, y1 / height]


def _resolve_result_asset(run_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    direct = Path(value).expanduser()
    if direct.is_file():
        return direct.resolve()
    matches = list(run_root.rglob(direct.name))
    return matches[0].resolve() if len(matches) == 1 else None


def _preflight(*, target: dict[str, Any], input_receipt: dict[str, Any],
               run_root: Path) -> dict[str, Any]:
    scene_id = input_receipt["scene_id"]
    expected = _result_paths(run_root, scene_id)
    return {
        "schema": SCHEMA,
        "stage": "result_intake_ready_not_executed",
        "target_id": target["target_id"],
        "scene_id": scene_id,
        "semantic_targets": target["semantic_targets"],
        "expected_outputs": {key: str(value) for key, value in expected.items()},
        "gates": {
            "input_adapter_ready": input_receipt.get("gates", {}).get("input_adapter_ready") is True,
            "result_contract_ready": True,
            "paid_inference_started": False,
            "fire3d_run_complete": False,
            "world_ir_promotion_ready": False,
        },
        "claim_limit": "result importer is ready; no Fire3D inference or object output exists",
    }


def ingest(*, target_path: Path, input_receipt_path: Path, run_root: Path,
           detector_paths: list[Path], output_path: Path, target_id: str | None = None,
           actual_cost_usd: float = 0.0, preflight: bool = False) -> dict[str, Any]:
    target = _read_target(target_path, target_id)
    input_receipt = _load(input_receipt_path)
    if input_receipt.get("schema") != "y92.fire3d_single_image_input.v1":
        raise ValueError("input receipt is not a Fire3D single-image input")
    if input_receipt.get("target_id") != target.get("target_id"):
        raise ValueError("input receipt and target identify different jobs")
    if input_receipt.get("gates", {}).get("input_adapter_ready") is not True:
        raise ValueError("Fire3D input adapter did not pass")
    budget = target.get("budget", {})
    if actual_cost_usd < 0.0:
        raise ValueError("actual cost cannot be negative")
    if actual_cost_usd > float(budget.get("maximum_job_usd", 0.0)):
        raise ValueError("Fire3D job exceeded its declared maximum_job_usd")
    if actual_cost_usd > float(budget.get("intervention_total_usd", 20.0)):
        raise ValueError("Fire3D job crossed the cumulative intervention point")
    if preflight:
        result = _preflight(target=target, input_receipt=input_receipt, run_root=run_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result

    paths = _result_paths(run_root, input_receipt["scene_id"])
    missing = [key for key, value in paths.items() if not value.is_file()]
    if missing:
        raise FileNotFoundError("Fire3D run is incomplete: " + ", ".join(missing))
    protocol = _load(paths["protocol"])
    if protocol.get("protocol", {}).get("name") not in {None, "fire3d_single_image_v1"}:
        raise ValueError("Fire3D run used a different protocol")
    raw_obbs = _load(paths["raw_obbs"])
    summary = _load(paths["summary"])
    if summary.get("status") != "complete":
        raise ValueError("Fire3D reconstruction summary is not complete")

    width, height = input_receipt["geometry"]["organized_point_shape"][1] * 2, \
        input_receipt["geometry"]["organized_point_shape"][0] * 2
    boxes = _instance_bboxes(paths["labels"], width, height)
    capture_id = input_receipt["source"]["capture_id"]
    detections: list[dict[str, Any]] = []
    for detector_path in detector_paths:
        detections.extend(_detector_rows(detector_path, capture_id))
    reconstruction = {
        int(row.get("object_id")): row for row in summary.get("objects", [])
        if row.get("object_id") is not None
    }
    candidates = []
    for obb in raw_obbs.get("objects", []):
        ident = int(obb["index"])
        bbox = boxes.get(ident)
        if bbox is None:
            continue
        ranked = sorted(
            ((_bbox_iou(bbox, _normal_bbox(row, width, height)), row) for row in detections),
            key=lambda pair: pair[0], reverse=True,
        )
        overlap, matched = ranked[0] if ranked else (0.0, None)
        semantics = list(matched.get("semantic_candidates", [])) if matched and overlap >= 0.10 else []
        record = reconstruction.get(ident, {})
        asset = _resolve_result_asset(
            run_root, record.get("textured_glb") or record.get("mesh_path")
        )
        translate = [float(v) for v in obb.get("translate", [])]
        scale = [float(v) for v in np.asarray(obb.get("scale", [])).reshape(-1)]
        candidates.append({
            "proposal_id": f"fire3d:{input_receipt['scene_id']}:{ident}",
            "fire3d_object_id": ident,
            "source_capture_id": capture_id,
            "image_bbox_normalized": [round(v, 6) for v in bbox],
            "matched_observation_id": matched.get("observation_id") if matched and overlap >= 0.10 else None,
            "detector_iou": round(float(overlap), 6),
            "semantic_candidates": semantics,
            "obb_model_local": {
                "translate": translate,
                "rotation_wxyz": [float(v) for v in obb.get("rotation", [])],
                "scale": scale,
                "score": float(obb.get("score", 0.0)),
            },
            "mesh_asset": str(asset) if asset else None,
            "mesh_sha256": _sha256(asset) if asset else None,
            "world_placement_status": "unaccepted_model_local",
            "generated_representation_status": "withheld_pending_comparison_and_placement",
        })

    observed = sorted({item for row in candidates for item in row["semantic_candidates"]})
    target_hits = sorted(set(target["semantic_targets"]) & set(observed))
    assets_resolve = bool(candidates) and all(row["mesh_asset"] for row in candidates)
    result = {
        "schema": SCHEMA,
        "stage": "executed_unaccepted_proposals",
        "target_id": target["target_id"],
        "scene_id": input_receipt["scene_id"],
        "backend": "fire3d_single_image_v1",
        "source": input_receipt["source"],
        "input_receipt": str(input_receipt_path.resolve()),
        "run_root": str(run_root.resolve()),
        "upstream_outputs": {
            key: {"path": str(value.resolve()), "sha256": _sha256(value)}
            for key, value in paths.items()
        },
        "cost": {"actual_usd": actual_cost_usd, "budget": budget},
        "counts": {
            "raw_oriented_boxes": len(raw_obbs.get("objects", [])),
            "instance_masks": len(boxes),
            "candidate_objects": len(candidates),
            "semantic_matches": sum(bool(row["semantic_candidates"]) for row in candidates),
            "resolved_mesh_assets": sum(bool(row["mesh_asset"]) for row in candidates),
        },
        "observed_semantics": observed,
        "target_semantic_hits": target_hits,
        "candidates": candidates,
        "gates": {
            "fire3d_run_complete": True,
            "result_assets_resolve": assets_resolve,
            "target_semantics_observed": bool(target_hits),
            "job_budget_respected": True,
            "cheap_baseline_comparison_complete": False,
            "accepted_world_placement": False,
            "world_ir_promotion_ready": False,
        },
        "claim_limit": (
            "Fire3D objects are model-local proposals. Keep only objects that beat the declared "
            "cheap archetype after PS1 simplification and receive accepted world placement."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--target-id")
    parser.add_argument("--input-receipt", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--detectors", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--actual-cost-usd", type=float, default=0.0)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    result = ingest(
        target_path=args.target.resolve(), target_id=args.target_id,
        input_receipt_path=args.input_receipt.resolve(), run_root=args.run_root.resolve(),
        detector_paths=[path.resolve() for path in args.detectors],
        output_path=args.out.resolve(), actual_cost_usd=args.actual_cost_usd,
        preflight=args.preflight,
    )
    print(json.dumps(result["gates"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
