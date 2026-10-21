#!/usr/bin/env python3
"""Triangulate matched track-mask centroids through accepted camera poses."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.bulk_mask_observations import decode_foreground_runs


SCHEMA = "y92.track_triangulation_hypotheses.v1"
TRACK_SCHEMA = "y92.bulk_object_tracks.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def rodrigues_matrix(vector: list[float]) -> np.ndarray:
    rotation = np.asarray(vector, dtype=float)
    theta = float(np.linalg.norm(rotation))
    if theta == 0.0:
        return np.eye(3)
    axis = rotation / theta
    x, y, z = axis
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(theta) * cross + (1.0 - math.cos(theta)) * (cross @ cross)


def triangulate_rays(
    centers: list[np.ndarray], directions: list[np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    if len(centers) < 2 or len(centers) != len(directions):
        raise ValueError("triangulation needs matching lists of at least two rays")
    projectors = [np.eye(3) - np.outer(direction, direction) for direction in directions]
    normal = sum(projectors)
    right = sum(projector @ center for projector, center in zip(projectors, centers))
    point = np.linalg.solve(normal, right)
    perpendicular = np.array([
        np.linalg.norm(projector @ (point - center))
        for projector, center in zip(projectors, centers)
    ])
    variance = max(float(np.mean(perpendicular ** 2)), 1e-12)
    covariance = np.linalg.inv(normal) * variance
    return point, covariance


def _mask_centroid(frame: dict[str, Any]) -> tuple[float, float]:
    width, height = frame["mask_size_px"]
    mask = decode_foreground_runs(
        frame["foreground_runs"], height=height, width=width
    )
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ValueError("cannot triangulate an empty mask")
    return float(xs.mean()), float(ys.mean())


def camera_intrinsic(
    camera: dict[str, Any], intrinsics: dict[str, Any]
) -> tuple[float, float, float]:
    """Return the calibration belonging to this camera, including mixed lenses."""
    per_frame = intrinsics.get("per_frame") or {}
    record = per_frame.get(str(camera["frame_rank"]))
    if record is None:
        return (
            float(intrinsics["focal_px_solved"]),
            *(float(value) for value in intrinsics["principal_point_px"]),
        )
    return (
        float(record["focal_px"]),
        *(float(value) for value in record["principal_point_px"]),
    )


def build_document(
    *,
    tracks_path: Path,
    camera_receipt_path: Path,
    minimum_views: int = 2,
    minimum_angle_deg: float = 3.0,
    maximum_median_reprojection_px: float = 2.0,
    maximum_reprojection_px: float = 4.0,
) -> dict[str, Any]:
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
    camera_receipt = json.loads(camera_receipt_path.read_text(encoding="utf-8"))
    if tracks.get("schema") != TRACK_SCHEMA:
        raise ValueError(f"tracks are not {TRACK_SCHEMA}")
    if camera_receipt.get("global_solve", {}).get("optimizer_status", 0) <= 0:
        raise ValueError("camera receipt does not contain a converged absolute solve")
    intrinsics = camera_receipt["intrinsics"]
    cameras = {row["capture_id"]: row for row in camera_receipt["cameras"]}

    hypotheses: list[dict[str, Any]] = []
    for track in tracks["tracks"]:
        rays = []
        for frame in track["frames"]:
            capture_id = frame["capture_id"]
            if (
                capture_id not in cameras
                or frame["matched_object_observation_id"] is None
                or frame["mask_area_px"] <= 0
            ):
                continue
            centroid_x, centroid_y = _mask_centroid(frame)
            mask_width, mask_height = frame["mask_size_px"]
            camera = cameras[capture_id]
            focal, principal_x, principal_y = camera_intrinsic(camera, intrinsics)
            solve_width = principal_x * 2.0
            solve_height = principal_y * 2.0
            pixel_x = centroid_x * solve_width / mask_width
            pixel_y = centroid_y * solve_height / mask_height
            center = np.asarray(camera["solved_center_enu_m"], dtype=float)
            world_to_camera = rodrigues_matrix(camera["solved_rotation_rodrigues"])
            camera_ray = np.array([
                (pixel_x - principal_x) / focal,
                (pixel_y - principal_y) / focal,
                1.0,
            ])
            direction = world_to_camera.T @ camera_ray
            direction /= np.linalg.norm(direction)
            rays.append({
                "capture_id": capture_id,
                "object_observation_id": frame["matched_object_observation_id"],
                "center": center,
                "direction": direction,
                "pixel": [pixel_x, pixel_y],
                "world_to_camera": world_to_camera,
                "focal_px": focal,
                "principal_point_px": [principal_x, principal_y],
            })
        if len(rays) < 2:
            continue
        point, covariance = triangulate_rays(
            [row["center"] for row in rays],
            [row["direction"] for row in rays],
        )
        reprojection = []
        depths = []
        for row in rays:
            camera_point = row["world_to_camera"] @ (point - row["center"])
            depths.append(float(camera_point[2]))
            focal = row["focal_px"]
            principal_x, principal_y = row["principal_point_px"]
            projected = [
                focal * camera_point[0] / camera_point[2] + principal_x,
                focal * camera_point[1] / camera_point[2] + principal_y,
            ]
            reprojection.append(float(np.linalg.norm(np.asarray(projected) - row["pixel"])))
        angles = [
            math.degrees(math.acos(float(np.clip(
                np.dot(rays[left]["direction"], rays[right]["direction"]), -1.0, 1.0
            ))))
            for left in range(len(rays)) for right in range(left)
        ]
        median_reprojection = float(np.median(reprojection))
        maximum_reprojection = max(reprojection)
        covariance_finite = bool(np.isfinite(covariance).all())
        geometry_pass = (
            len(rays) >= minimum_views
            and max(angles) >= minimum_angle_deg
            and median_reprojection <= maximum_median_reprojection_px
            and maximum_reprojection <= maximum_reprojection_px
            and all(depth > 0 for depth in depths)
            and covariance_finite
        )
        semantic_resolved = len(track["semantic_candidates"]) == 1
        camera_pose_uncertainty_propagated = False
        hypotheses.append({
            "triangulation_id": f"triangulation:{track['track_id']}",
            "track_id": track["track_id"],
            "semantic_candidates": track["semantic_candidates"],
            "observations": [{
                "capture_id": row["capture_id"],
                "object_observation_id": row["object_observation_id"],
                "centroid_px_in_camera_solve": [round(value, 6) for value in row["pixel"]],
                "reprojection_error_px": round(error, 6),
                "depth_m": round(depth, 6),
            } for row, error, depth in zip(rays, reprojection, depths)],
            "point_enu_m": [round(float(value), 9) for value in point],
            "ray_intersection_covariance_m2": [
                [round(float(value), 12) for value in row] for row in covariance
            ],
            "uncertainty": {
                "model": "ray_intersection_residual_only",
                "camera_pose_uncertainty_propagated": camera_pose_uncertainty_propagated,
                "note": "Camera center/rotation covariance must be propagated before World IR promotion.",
            },
            "quality": {
                "views": len(rays),
                "maximum_triangulation_angle_deg": round(max(angles), 6),
                "median_reprojection_error_px": round(median_reprojection, 6),
                "maximum_reprojection_error_px": round(maximum_reprojection, 6),
                "positive_depth_all_views": all(depth > 0 for depth in depths),
                "covariance_finite": covariance_finite,
            },
            "geometry_gate_passed": geometry_pass,
            "semantic_identity_resolved": semantic_resolved,
            "physical_identity_state": (
                "candidate_geometry_only" if geometry_pass else "rejected_geometry"
            ),
            "world_ir_promotion_ready": (
                geometry_pass and semantic_resolved and camera_pose_uncertainty_propagated
            ),
            "canonical_feature_id": None,
        })
    hypotheses.sort(key=lambda row: row["triangulation_id"])
    return {
        "schema": SCHEMA,
        "inputs": {
            "tracks": {"path": str(tracks_path), "sha256": _sha256(tracks_path)},
            "camera_receipt": {
                "path": str(camera_receipt_path), "sha256": _sha256(camera_receipt_path)
            },
        },
        "coordinate_frame": "control_enu",
        "parameters": {
            "minimum_views": minimum_views,
            "minimum_triangulation_angle_deg": minimum_angle_deg,
            "maximum_median_reprojection_px": maximum_median_reprojection_px,
            "maximum_reprojection_px": maximum_reprojection_px,
            "centroid_source": "tracked_sam2_mask",
            "requires_same_frame_detector_association": True,
        },
        "counts": {
            "track_hypotheses": tracks["counts"]["track_hypotheses"],
            "triangulated_candidates": len(hypotheses),
            "geometry_gate_passed": sum(row["geometry_gate_passed"] for row in hypotheses),
            "semantic_identity_resolved": sum(
                row["semantic_identity_resolved"] for row in hypotheses
            ),
            "world_ir_promotion_ready": sum(
                row["world_ir_promotion_ready"] for row in hypotheses
            ),
            "canonical_features": 0,
        },
        "hypotheses": hypotheses,
        "stage": "triangulation_diagnostic_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--camera-receipt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = build_document(
        tracks_path=args.tracks, camera_receipt_path=args.camera_receipt
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    print(
        f"[triangulate-track-hypotheses] candidates={document['counts']['triangulated_candidates']} "
        f"geometry_pass={document['counts']['geometry_gate_passed']} "
        f"promotion_ready={document['counts']['world_ir_promotion_ready']} canonical=0"
    )
    print(f"[triangulate-track-hypotheses] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
