#!/usr/bin/env python3
"""Prepare one bounded Fire3D proposal job without granting it world authority.

Fire3D's released single-image protocol consumes ``rgb.jpeg`` plus an organized
``aligned_pcd.ply`` containing exactly one point for every half-resolution RGB
pixel.  This adapter converts an existing geometry-proposal depth frame into
that dataset layout and records the accepted camera frame used to ground it.
The output remains a model proposal input; it cannot create a canonical World
IR feature or accepted placement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


SCHEMA = "y92.fire3d_single_image_input.v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _rodrigues(vector: list[float]) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float64)
    theta = float(np.linalg.norm(v))
    if theta <= 1e-15:
        return np.eye(3)
    x, y, z = v / theta
    skew = np.asarray([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(theta) * skew + (1.0 - math.cos(theta)) * (skew @ skew)


def _resize_float(values: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(
        Image.fromarray(values.astype(np.float32), mode="F").resize(size, Image.Resampling.BILINEAR),
        dtype=np.float32,
    )


def _write_ply(path: Path, points: np.ndarray) -> None:
    flat = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    with path.open("w", encoding="ascii") as handle:
        handle.write("ply\nformat ascii 1.0\n")
        handle.write(f"element vertex {len(flat)}\n")
        handle.write("property float x\nproperty float y\nproperty float z\nend_header\n")
        for x, y, z in flat:
            handle.write(f"{float(x):.8g} {float(y):.8g} {float(z):.8g}\n")


def prepare(*, spec_path: Path, output_root: Path, repo_root: Path,
            target_id: str | None = None) -> dict[str, Any]:
    spec = _load(spec_path)
    if spec.get("schema") == "y92.object_completion_targets.v1":
        targets = spec.get("targets", [])
        matches = [row for row in targets
                   if target_id is None or row.get("target_id") == target_id]
        if len(matches) != 1:
            raise ValueError("target collection requires one unique --target-id")
        spec = matches[0]
    if spec.get("schema") != "y92.object_completion_target.v1":
        raise ValueError("target spec must use y92.object_completion_target.v1")
    if spec.get("backend") != "fire3d_single_image_v1":
        raise ValueError("this adapter only prepares fire3d_single_image_v1")
    budget = spec.get("budget", {})
    if float(budget.get("maximum_job_usd", 0.0)) <= 0.0:
        raise ValueError("target must declare a positive maximum_job_usd")
    if float(budget["maximum_job_usd"]) > float(budget.get("intervention_total_usd", 20.0)):
        raise ValueError("one job cannot exceed the cumulative intervention point")
    if not spec.get("semantic_targets") or not spec.get("cheap_baseline"):
        raise ValueError("target needs semantic_targets and a cheap baseline comparison")

    selection_path = _resolve(repo_root, spec["capture_selection"])
    geometry_path = _resolve(repo_root, spec["geometry_npz"])
    geometry_receipt_path = _resolve(repo_root, spec["geometry_receipt"])
    camera_receipt_path = _resolve(repo_root, spec["camera_registration"])
    source_root = _resolve(repo_root, spec["source_root"])
    selection = _load(selection_path)
    geometry_receipt = _load(geometry_receipt_path)
    camera_receipt = _load(camera_receipt_path)

    capture_id = spec["capture_id"]
    captures = selection.get("captures", [])
    indices = [index for index, row in enumerate(captures) if row.get("capture_id") == capture_id]
    if len(indices) != 1:
        raise ValueError(f"capture {capture_id!r} is not unique in selection")
    frame_index = indices[0]
    capture = captures[frame_index]
    source_image = source_root / capture["local_path"]
    if _sha256(source_image) != capture["sha256"]:
        raise ValueError("source image digest does not match capture selection")
    geometry_output = geometry_receipt.get("outputs", {}).get("geometry", {})
    if geometry_output.get("sha256") != _sha256(geometry_path):
        raise ValueError("geometry proposal digest mismatch")
    if geometry_receipt.get("stage") != "executed_unaccepted_proposal":
        raise ValueError("geometry input must retain its unaccepted proposal state")

    cameras = {row["capture_id"]: row for row in camera_receipt.get("cameras", [])}
    camera = cameras.get(capture_id)
    if camera is None:
        raise ValueError("selected capture has no shared camera-registration row")
    global_solve = camera_receipt.get("global_solve", {})
    camera_registration_accepted = (
        int(global_solve.get("optimizer_status", 0)) > 0
        and int(global_solve.get("control_point_constraints", 0)) > 0
    )
    solve_converged = int(global_solve.get("optimizer_status", 0)) > 0
    gravity_valid = bool(
        camera_receipt.get("stages", {}).get("gravity_alignment", {})
        .get("gate", {}).get("pass", camera_registration_accepted)
    )
    placement_mode = spec.get("placement_mode", "accepted_world")
    if placement_mode == "accepted_world" and not camera_registration_accepted:
        raise ValueError("selected capture lacks accepted camera/world registration")
    if placement_mode == "model_local_proposal" and not (solve_converged and gravity_valid):
        raise ValueError("model-local Fire3D proposal requires converged gravity-leveled cameras")
    if placement_mode not in {"accepted_world", "model_local_proposal"}:
        raise ValueError(f"unsupported placement_mode {placement_mode!r}")

    geometry = np.load(geometry_path, allow_pickle=False)
    depth = np.asarray(geometry["depth"][frame_index], dtype=np.float32)
    intrinsics = np.asarray(geometry["intrinsics"][frame_index], dtype=np.float64)
    if depth.ndim != 2 or intrinsics.shape != (3, 3):
        raise ValueError("geometry proposal has incompatible depth/intrinsic shapes")
    source_width, source_height = Image.open(source_image).size
    if source_width % 2 or source_height % 2:
        raise ValueError("Fire3D organized point lattice requires even RGB dimensions")
    point_width, point_height = source_width // 2, source_height // 2
    depth_half = _resize_float(depth, (point_width, point_height))
    finite = np.isfinite(depth_half) & (depth_half > 0.0)
    if not finite.any():
        raise ValueError("geometry proposal contains no positive finite depth")
    fill = float(np.median(depth_half[finite]))
    depth_half = np.where(finite, depth_half, fill)

    processed_height, processed_width = depth.shape
    sx = point_width / processed_width
    sy = point_height / processed_height
    k_half = intrinsics.copy()
    k_half[0, :] *= sx
    k_half[1, :] *= sy
    u, v = np.meshgrid(np.arange(point_width, dtype=np.float64),
                       np.arange(point_height, dtype=np.float64))
    z = depth_half.astype(np.float64)
    camera_points = np.stack((
        (u - k_half[0, 2]) / k_half[0, 0] * z,
        (v - k_half[1, 2]) / k_half[1, 1] * z,
        z,
    ), axis=-1)
    world_to_camera = _rodrigues(camera["solved_rotation_rodrigues"])
    center = np.asarray(camera["solved_center_enu_m"], dtype=np.float64)
    world_points = camera_points @ world_to_camera + center
    if not np.isfinite(world_points).all():
        raise ValueError("aligned Fire3D point cloud contains non-finite coordinates")

    scene_id = spec["target_id"].replace(":", "_").replace("/", "_")
    dataset_root = output_root / "single_image"
    scene_root = dataset_root / "data" / scene_id
    scene_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_image, scene_root / "rgb.jpeg")
    _write_ply(scene_root / "aligned_pcd.ply", world_points)
    (dataset_root / "single_image_valid.txt").write_text(scene_id + "\n", encoding="utf-8")

    forward = world_to_camera.T @ np.asarray([0.0, 0.0, 1.0])
    up = world_to_camera.T @ np.asarray([0.0, -1.0, 0.0])
    k_native = intrinsics.copy()
    k_native[0, :] *= source_width / processed_width
    k_native[1, :] *= source_height / processed_height
    camera_frame = {
        "eye": center.tolist(),
        "lookat": (center + forward).tolist(),
        "up": up.tolist(),
    }
    camera_payload = {
        "schema": "fire3d_single_image_camera_v1",
        "native": {
            "frame": camera_frame, "forward": forward.tolist(), "K": k_native.tolist(),
            "width": source_width, "height": source_height,
            "crop_top": 0, "crop_left": 0,
            "source_size": [source_height, source_width],
        },
        "reconstruction": {
            "frame": camera_frame, "forward": forward.tolist(), "K": k_native.tolist(),
            "width": source_width, "height": source_height,
            "crop_top": 0, "crop_left": 0,
            "source_size": [source_height, source_width],
        },
    }
    (scene_root / "camera.json").write_text(
        json.dumps(camera_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    command = [
        "fire3d", "infer", "--dataset", "single_image", "--scene-id", scene_id,
        "--data-root", str(output_root.resolve()), "--output-root", "<job-output>",
        "--gpu", "0", "--skip-render",
    ]
    receipt = {
        "schema": SCHEMA,
        "stage": "prepared_not_executed",
        "target_id": spec["target_id"],
        "semantic_targets": spec["semantic_targets"],
        "cheap_baseline": spec["cheap_baseline"],
        "source": {
            "capture_id": capture_id,
            "image": str(source_image), "sha256": _sha256(source_image),
            "license": capture.get("license"), "attribution": capture.get("attribution"),
        },
        "geometry": {
            "backend": geometry_receipt.get("backend"),
            "proposal_stage": geometry_receipt.get("stage"),
            "world_ir_promotion_ready": geometry_receipt.get("world_ir_promotion_ready"),
            "source_npz": str(geometry_path), "source_sha256": _sha256(geometry_path),
            "organized_point_shape": [point_height, point_width, 3],
            "organized_point_count": int(point_height * point_width),
            "finite_point_count": int(np.isfinite(world_points).all(axis=-1).sum()),
            "coordinate_frame": (
                "accepted_camera_registration_enu_m" if camera_registration_accepted
                else "gravity_leveled_candidate_enu_m"
            ),
        },
        "camera_registration": {
            "receipt": str(camera_receipt_path),
            "capture_present": True,
            "solve_converged": solve_converged,
            "gravity_valid": gravity_valid,
            "accepted_world_registration": camera_registration_accepted,
            "placement_mode": placement_mode,
        },
        "fire3d_release_contract": {
            "protocol": "fire3d_single_image_v1",
            "rgb": "data/<scene_id>/rgb.jpeg",
            "organized_geometry": "data/<scene_id>/aligned_pcd.ply",
            "validated_gpu_memory_gb": 96,
            "source": "https://github.com/xiahongchi/Fire3D",
            "audited_at": "2026-09-13",
        },
        "dataset_root": str(dataset_root.resolve()),
        "scene_id": scene_id,
        "command": command,
        "budget": budget,
        "gates": {
            "source_digest_valid": True,
            "geometry_digest_valid": True,
            "organized_point_contract_valid": True,
            "camera_world_registration_available": camera_registration_accepted,
            "model_local_geometry_available": solve_converged and gravity_valid,
            "cheap_baseline_declared": True,
            "input_adapter_ready": True,
            "paid_inference_started": False,
            "world_ir_promotion_ready": False,
        },
        "claim_limit": (
            "prepared Fire3D input is an unexecuted model proposal job; Fire3D output must be "
            "compared with the cheap baseline, simplified to Y9 limits, and separately accepted "
            "before any GeneratedRepresentation or world placement changes"
        ),
    }
    (output_root / "fire3d_input_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--target-id")
    args = parser.parse_args()
    result = prepare(
        spec_path=args.spec.resolve(), output_root=args.output_root.resolve(),
        repo_root=args.repo_root.resolve(), target_id=args.target_id,
    )
    print(json.dumps(result["gates"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
