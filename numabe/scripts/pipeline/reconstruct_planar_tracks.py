#!/usr/bin/env python3
"""Reconstruct tracked planar facade/sign surfaces from multi-view features."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.bulk_mask_observations import decode_foreground_runs
from scripts.pipeline.triangulate_track_hypotheses import (
    camera_intrinsic,
    rodrigues_matrix,
)


SCHEMA = "y92.planar_track_reconstruction.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _projection(camera: dict[str, Any], intrinsics: dict[str, Any]) -> np.ndarray:
    focal, cx, cy = camera_intrinsic(camera, intrinsics)
    matrix = np.array([[focal, 0.0, cx], [0.0, focal, cy], [0.0, 0.0, 1.0]])
    rotation = rodrigues_matrix(camera["solved_rotation_rodrigues"])
    center = np.asarray(camera["solved_center_enu_m"], dtype=float)
    return matrix @ np.column_stack((rotation, -rotation @ center))


def _scale_points(
    points: np.ndarray, source_size: tuple[int, int], camera: dict[str, Any],
    intrinsics: dict[str, Any],
) -> np.ndarray:
    _, cx, cy = camera_intrinsic(camera, intrinsics)
    width, height = source_size
    return points * np.array([(2.0 * cx) / width, (2.0 * cy) / height])


def _reprojection_errors(
    points: np.ndarray, projection: np.ndarray, observations: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    homogeneous = np.column_stack((points, np.ones(len(points))))
    projected = (projection @ homogeneous.T).T
    depth = projected[:, 2]
    pixels = projected[:, :2] / depth[:, None]
    return np.linalg.norm(pixels - observations, axis=1), depth


def _fit_plane(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    center = points.mean(axis=0)
    _, _, vh = np.linalg.svd(points - center, full_matrices=False)
    normal = vh[-1]
    normal /= np.linalg.norm(normal)
    if normal[2] < 0:
        normal *= -1.0
    residuals = np.abs((points - center) @ normal)
    return center, normal, residuals


def triangulation_angles(points, left_center, right_center):
    left = points - np.asarray(left_center)
    right = points - np.asarray(right_center)
    denominator = np.linalg.norm(left, axis=1) * np.linalg.norm(right, axis=1)
    cosine = np.divide(np.sum(left * right, axis=1), denominator,
                       out=np.ones(len(points)), where=denominator > 1e-12)
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))


def robust_plane(points, *, threshold_m, seed=4813):
    """Fit a supported plane while retaining the rejected evidence count."""
    # A feature reconstructed from several pairs is not several unique supports.
    unique = np.unique(np.round(np.asarray(points, dtype=float), 3), axis=0)
    if len(unique) < 3:
        return None, {"unique_points": len(unique), "inliers": 0,
                      "rejected_points": len(unique), "inlier_fraction": 0.0}
    rng = np.random.default_rng(seed)
    best = np.zeros(len(unique), dtype=bool)
    for _ in range(512):
        sample = unique[rng.choice(len(unique), 3, replace=False)]
        normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal /= norm
        inliers = np.abs((unique - sample[0]) @ normal) <= threshold_m
        if inliers.sum() > best.sum():
            best = inliers
    if best.sum() < 3:
        return None, {"unique_points": len(unique), "inliers": 0,
                      "rejected_points": len(unique), "inlier_fraction": 0.0}
    # Refit only consensus points, then remove any outside the same metric bound.
    for _ in range(3):
        center, normal, _ = _fit_plane(unique[best])
        refined = np.abs((unique - center) @ normal) <= threshold_m
        if refined.sum() < 3 or np.array_equal(refined, best):
            break
        best = refined
    center, normal, residuals = _fit_plane(unique[best])
    return (center, normal, residuals), {
        "unique_points": len(unique), "inliers": int(best.sum()),
        "rejected_points": int((~best).sum()), "inlier_fraction": float(best.mean()),
    }


def _triangulate_pairs(
    left_projection: np.ndarray, right_projection: np.ndarray,
    left_pixels: np.ndarray, right_pixels: np.ndarray,
) -> np.ndarray:
    points = []
    for (left_x, left_y), (right_x, right_y) in zip(left_pixels, right_pixels):
        system = np.stack((
            left_x * left_projection[2] - left_projection[0],
            left_y * left_projection[2] - left_projection[1],
            right_x * right_projection[2] - right_projection[0],
            right_y * right_projection[2] - right_projection[1],
        ))
        _, _, vh = np.linalg.svd(system)
        homogeneous = vh[-1]
        if abs(homogeneous[3]) <= 1e-12:
            points.append([math.nan, math.nan, math.nan])
        else:
            points.append(homogeneous[:3] / homogeneous[3])
    return np.asarray(points, dtype=float)


def shared_sfm_surface_points(track, sparse, direct_masks, cameras, *, minimum_angle_deg=3., maximum_reprojection_px=4.):
    """FL-4813: associate masks with the existing bundle-adjusted point graph."""
    frame_masks = {}
    for frame in track["frames"]:
        cid = frame["capture_id"]
        if cid not in cameras:
            continue
        row = direct_masks.get(frame.get("matched_object_observation_id"), frame)
        width, height = row["mask_size_px"]
        frame_masks[cid] = decode_foreground_runs(row["foreground_runs"], height=height, width=width)
    accepted = []
    for point in sparse["points"]:
        support = {}
        for obs in point["observations"]:
            cid = obs["capture_id"]
            mask = frame_masks.get(cid)
            if mask is None or obs["depth_m"] <= 0 or obs["reprojection_error_px"] > maximum_reprojection_px:
                continue
            x, y = (int(round(v)) for v in obs["source_image_pixel_px"])
            if 0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and mask[y, x]:
                support[cid] = obs
        if len(support) < 2 or np.median([r["reprojection_error_px"] for r in support.values()]) > 2.0:
            continue
        ids = sorted(support)
        angle = max(float(triangulation_angles(
            np.asarray([point["position_enu_m"]]), cameras[a]["solved_center_enu_m"], cameras[b]["solved_center_enu_m"],
        )[0]) for i, a in enumerate(ids) for b in ids[i+1:])
        if angle < minimum_angle_deg:
            continue
        accepted.append({"point_id": point["point_id"], "position_enu_m": point["position_enu_m"],
                         "supporting_capture_ids": ids, "triangulation_angle_deg": angle,
                         "reprojection_median_px": float(np.median([r["reprojection_error_px"] for r in support.values()]))})
    return accepted


def fit_surface_geometry(reconstructed_points, maximum_plane_residual_m, maximum_normal_z, minimum_plane_support):
    points = np.asarray(reconstructed_points, dtype=float).reshape(-1, 3)
    fit, support = robust_plane(points, threshold_m=maximum_plane_residual_m)
    if fit is None:
        plane = None
        gate_failures = ["minimum_surface_points"]
    else:
        center, normal, residuals = fit
        gate_failures = []
        if support["inliers"] < 12:
            gate_failures.append("minimum_surface_points")
        if support["inlier_fraction"] < minimum_plane_support:
            gate_failures.append("plane_consensus_support")
        if float(np.max(residuals)) > maximum_plane_residual_m:
            gate_failures.append("plane_residual")
        if abs(float(normal[2])) > maximum_normal_z:
            gate_failures.append("vertical_plane")
        plane = {
            "center_enu_m": center.tolist(), "normal_enu": normal.tolist(),
            "normal_z_abs": abs(float(normal[2])), "surface_points": support["inliers"],
            "raw_triangulated_points": len(points), "consensus": support,
            "residual_m": {"median": float(np.median(residuals)),
                           "p95": float(np.percentile(residuals, 95)),
                           "max": float(np.max(residuals))},
        }

    return plane, gate_failures


def build_document(
    *, tracks_path: Path, masks_path: Path, camera_receipt_path: Path, capture_index_path: Path,
    semantic_class: str, track_id: str | None = None, ratio_threshold: float = 0.75,
    homography_threshold_px: float = 5.0, maximum_reprojection_px: float = 4.0,
    maximum_plane_residual_m: float = 0.08, maximum_normal_z: float = 0.2,
    minimum_triangulation_angle_deg: float = 3.0, minimum_plane_support: float = 0.6,
    sparse_reconstruction_path: Path | None = None,
) -> dict[str, Any]:
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
    masks = json.loads(masks_path.read_text(encoding="utf-8"))
    receipt = json.loads(camera_receipt_path.read_text(encoding="utf-8"))
    source_index = json.loads(capture_index_path.read_text(encoding="utf-8"))
    cameras = {row["capture_id"]: row for row in receipt["cameras"]}
    intrinsics = receipt["intrinsics"]
    sources = {row["capture_id"]: row for row in source_index["captures"]}
    direct_masks = {
        row["object_observation_id"]: row for row in masks["masks"]
    }
    track = next(
        (
            row for row in tracks["tracks"]
            if semantic_class in row["semantic_candidates"]
            and (track_id is None or row["track_id"] == track_id)
        ),
        None,
    )
    if track is None:
        raise ValueError(f"no tracked surface has semantic class {semantic_class!r}")

    declared_sparse = receipt.get("sparse_reconstruction", {})
    if sparse_reconstruction_path is None and declared_sparse.get("path"):
        sparse_reconstruction_path = Path(declared_sparse["path"])
        if not sparse_reconstruction_path.is_absolute():
            sparse_reconstruction_path = ROOT / sparse_reconstruction_path
    if sparse_reconstruction_path is not None:
        expected = declared_sparse.get("sha256", "").removeprefix("sha256:")
        if not expected or _sha256(sparse_reconstruction_path) != expected:
            raise ValueError("sparse reconstruction differs from camera receipt")
        sparse = json.loads(sparse_reconstruction_path.read_text())
        if sparse.get("schema") != "y92.sparse_reconstruction.v1":
            raise ValueError("unsupported sparse reconstruction schema")
        points = shared_sfm_surface_points(
            track, sparse, direct_masks, cameras,
            minimum_angle_deg=minimum_triangulation_angle_deg,
            maximum_reprojection_px=maximum_reprojection_px,
        )
        plane, failures = fit_surface_geometry(
            [row["position_enu_m"] for row in points], maximum_plane_residual_m,
            maximum_normal_z, minimum_plane_support,
        )
        used_captures = sorted({cid for point in points for cid in point["supporting_capture_ids"]})
        for cid in used_captures:
            if _sha256(capture_index_path.parent / sources[cid]["local_path"]) != sources[cid]["sha256"].removeprefix("sha256:"):
                raise ValueError(f"source pixels changed: {cid}")
        return {
            "schema": SCHEMA, "track_id": track["track_id"], "semantic_class": semantic_class,
            "geometry_owner": "accepted_camera_solve_sparse_tracks",
            "inputs": {key: {"path": str(path), "sha256": _sha256(path)} for key, path in (
                ("tracks", tracks_path), ("masks", masks_path), ("camera_receipt", camera_receipt_path),
                ("capture_index", capture_index_path), ("sparse_reconstruction", sparse_reconstruction_path),
            )},
            "views": [{"capture_id": cid, "source_sha256": sources[cid]["sha256"]} for cid in used_captures],
            "source_point_support": points, "plane": plane,
            "gate": {"pass": not failures, "failed": failures, "minimum_surface_points": 12,
                     "minimum_triangulation_angle_deg": minimum_triangulation_angle_deg,
                     "maximum_reprojection_px": maximum_reprojection_px,
                     "minimum_plane_support_fraction": minimum_plane_support,
                     "maximum_plane_residual_m": maximum_plane_residual_m, "maximum_normal_z": maximum_normal_z},
            "tool_versions": {"opencv": cv2.__version__, "numpy": np.__version__},
            "world_ir_promotion_ready": False, "canonical_feature_id": None,
            "stage": "planar_surface_geometry_candidate",
        }

    sift = cv2.SIFT_create(nfeatures=3000)
    views: dict[str, dict[str, Any]] = {}
    for frame in track["frames"]:
        capture_id = frame["capture_id"]
        if capture_id not in cameras or frame["mask_area_px"] <= 0:
            continue
        source = sources[capture_id]
        image_path = capture_index_path.parent / source["local_path"]
        if _sha256(image_path) != source["sha256"].removeprefix("sha256:"):
            raise ValueError(f"source pixels changed: {capture_id}")
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        matched_id = frame["matched_object_observation_id"]
        mask_record = direct_masks.get(matched_id, frame)
        mask_width, mask_height = mask_record["mask_size_px"]
        mask = decode_foreground_runs(
            mask_record["foreground_runs"], height=mask_height, width=mask_width
        ).astype(np.uint8) * 255
        if mask.shape != image.shape:
            mask = cv2.resize(mask, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
        # SAM's object mask may deliberately exclude glyph holes, borders and
        # background pixels that carry the repeatable planar texture.  Geometry
        # matching therefore uses the mask's tight panel rectangle while the
        # segmentation mask continues to own the object silhouette.
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            continue
        # Include adjacent facade texture because a small, low-resolution sign
        # often has too few repeatable keypoints on its face alone. Homography,
        # reprojection and vertical-plane gates still decide whether that
        # context belongs to one physical surface.
        padding = max(32, int(0.75 * max(xs.max() - xs.min(), ys.max() - ys.min())))
        x0, x1 = max(0, int(xs.min()) - padding), min(image.shape[1], int(xs.max()) + padding + 1)
        y0, y1 = max(0, int(ys.min()) - padding), min(image.shape[0], int(ys.max()) + padding + 1)
        feature_mask = np.zeros_like(mask)
        feature_mask[y0:y1, x0:x1] = 255
        keypoints, descriptors = sift.detectAndCompute(image, feature_mask)
        if descriptors is None or len(keypoints) < 4:
            continue
        views[capture_id] = {
            "camera": cameras[capture_id],
            "source": source,
            "image_size": (image.shape[1], image.shape[0]),
            "keypoints": keypoints,
            "descriptors": descriptors,
            "mask_source": "direct_detector_sam2" if matched_id in direct_masks else "tracked_sam2",
            "feature_region_bbox_xyxy_px": [x0, y0, x1, y1],
        }

    pair_rows = []
    reconstructed_points = []
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    capture_ids = sorted(views)
    for left_index in range(len(capture_ids)):
        for right_index in range(left_index + 1, len(capture_ids)):
            left_id, right_id = capture_ids[left_index], capture_ids[right_index]
            left, right = views[left_id], views[right_id]
            matches = matcher.knnMatch(left["descriptors"], right["descriptors"], k=2)
            ratio_matches = [first for first, second in matches if first.distance < ratio_threshold * second.distance]
            pair = {
                "left": left_id,
                "right": right_id,
                "left_features": len(left["keypoints"]),
                "right_features": len(right["keypoints"]),
                "ratio_matches": len(ratio_matches),
                "homography_inliers": 0,
                "triangulated_points": 0,
            }
            if len(ratio_matches) < 4:
                pair_rows.append(pair)
                continue
            left_source = np.asarray([left["keypoints"][m.queryIdx].pt for m in ratio_matches])
            right_source = np.asarray([right["keypoints"][m.trainIdx].pt for m in ratio_matches])
            _, inlier_mask = cv2.findHomography(
                left_source, right_source, cv2.RANSAC, homography_threshold_px
            )
            if inlier_mask is None:
                pair_rows.append(pair)
                continue
            inliers = inlier_mask.ravel().astype(bool)
            pair["homography_inliers"] = int(inliers.sum())
            if int(inliers.sum()) < 4:
                pair_rows.append(pair)
                continue
            left_pixels = _scale_points(
                left_source[inliers], left["image_size"], left["camera"], intrinsics
            )
            right_pixels = _scale_points(
                right_source[inliers], right["image_size"], right["camera"], intrinsics
            )
            left_projection = _projection(left["camera"], intrinsics)
            right_projection = _projection(right["camera"], intrinsics)
            points = _triangulate_pairs(
                left_projection, right_projection, left_pixels, right_pixels
            )
            finite = np.isfinite(points).all(axis=1)
            points = points[finite]
            left_pixels = left_pixels[finite]
            right_pixels = right_pixels[finite]
            left_error, left_depth = _reprojection_errors(points, left_projection, left_pixels)
            right_error, right_depth = _reprojection_errors(points, right_projection, right_pixels)
            pair["raw_triangulation"] = {
                "points": len(points),
                "positive_depth_both": int(((left_depth > 0) & (right_depth > 0)).sum()),
                "reprojection_px": {
                    "median": float(np.median(np.concatenate((left_error, right_error)))),
                    "max": float(np.max(np.concatenate((left_error, right_error)))),
                },
            }
            angles = triangulation_angles(
                points, left["camera"]["solved_center_enu_m"],
                right["camera"]["solved_center_enu_m"],
            )
            pair["weak_baseline_points"] = int((angles < minimum_triangulation_angle_deg).sum())
            keep = (
                (angles >= minimum_triangulation_angle_deg)
                & (left_depth > 0) & (right_depth > 0)
                & (left_error <= maximum_reprojection_px)
                & (right_error <= maximum_reprojection_px)
            )
            accepted = points[keep]
            pair["triangulated_points"] = len(accepted)
            if len(accepted):
                pair["reprojection_px"] = {
                    "median": float(np.median(np.concatenate((left_error[keep], right_error[keep])))),
                    "max": float(np.max(np.concatenate((left_error[keep], right_error[keep])))),
                }
                reconstructed_points.extend(accepted)
            pair_rows.append(pair)

    plane, gate_failures = fit_surface_geometry(
        reconstructed_points, maximum_plane_residual_m, maximum_normal_z, minimum_plane_support,
    )

    return {
        "schema": SCHEMA,
        "track_id": track["track_id"],
        "semantic_class": semantic_class,
        "inputs": {
            "tracks": {"path": str(tracks_path), "sha256": _sha256(tracks_path)},
            "masks": {"path": str(masks_path), "sha256": _sha256(masks_path)},
            "camera_receipt": {"path": str(camera_receipt_path), "sha256": _sha256(camera_receipt_path)},
            "capture_index": {"path": str(capture_index_path), "sha256": _sha256(capture_index_path)},
        },
        "views": [{
            "capture_id": capture_id,
            "source_sha256": views[capture_id]["source"]["sha256"],
            "masked_sift_features": len(views[capture_id]["keypoints"]),
            "mask_source": views[capture_id]["mask_source"],
            "feature_region_bbox_xyxy_px": views[capture_id]["feature_region_bbox_xyxy_px"],
        } for capture_id in capture_ids],
        "pairs": pair_rows,
        "plane": plane,
        "gate": {
            "pass": not gate_failures,
            "failed": gate_failures,
            "minimum_surface_points": 12,
            "minimum_triangulation_angle_deg": minimum_triangulation_angle_deg,
            "minimum_plane_support_fraction": minimum_plane_support,
            "maximum_plane_residual_m": maximum_plane_residual_m,
            "maximum_normal_z": maximum_normal_z,
        },
        "tool_versions": {"opencv": cv2.__version__, "numpy": np.__version__},
        "plane_fit": {"method": "deterministic_ransac_svd", "seed": 4813, "trials": 512, "deduplication_m": 0.001},
        "world_ir_promotion_ready": False,
        "canonical_feature_id": None,
        "stage": "planar_surface_geometry_candidate",
    }


PLANAR_SEMANTICS = {
    "facade_wall", "facade_window", "facade_door", "facade_shutter",
    "storefront_fascia", "storefront_sign", "station_identity_sign", "notice_panel",
    "statutory_traffic_sign", "bicycle_parking_prohibited_notice",
}


def eligible_planar_tracks(tracks, camera_ids):
    """FL-4813: select all supported surfaces, leaving sparse coverage explicit."""
    eligible, unresolved = [], []
    for track in sorted(tracks, key=lambda row: row["track_id"]):
        semantics = sorted(PLANAR_SEMANTICS.intersection(track["semantic_candidates"]))
        if not semantics:
            continue
        views = {row["capture_id"] for row in track["frames"]
                 if row["capture_id"] in camera_ids and row["mask_area_px"] > 0}
        if len(views) < 2:
            unresolved.append({"track_id": track["track_id"], "available_camera_views": len(views),
                               "reason": "insufficient_registered_views"})
        else:
            eligible.append((track["track_id"], semantics))
    return eligible, unresolved


def build_parent_batch(*, tracks_path, masks_path, camera_receipt_path,
                       capture_index_path, parent_facades_path):
    """FL-4813: route child regions through the shared bounded facade owner."""
    from scripts.pipeline.reconstruct_anchored_facade import project_child_to_parent
    from scripts.pipeline.capture_corpus import load_capture_corpus
    tracks = json.loads(tracks_path.read_text())
    cameras = json.loads(camera_receipt_path.read_text())
    _, capture_rows = load_capture_corpus(capture_index_path)
    for key, path in (("capture_manifest", capture_index_path), ("masks", masks_path)):
        if tracks.get("inputs", {}).get(key, {}).get("sha256") != _sha256(path):
            raise ValueError(f"tracked region input changed: {key}")
    parents = json.loads(parent_facades_path.read_text())
    if parents.get("schema") != "y92.parent_facade_proposals.v1":
        raise ValueError("unsupported parent facade proposal schema")
    if parents.get("camera_receipt_sha256") != _sha256(camera_receipt_path):
        raise ValueError("parent facade camera frame differs from camera receipt")
    ids = [p["parent_id"] for p in parents["parents"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate parent facade identity")
    sources = {row["capture_id"]: row for row in capture_rows}
    camera_ids = {row["capture_id"] for row in cameras["cameras"]}
    eligible, unresolved = eligible_planar_tracks(tracks["tracks"], camera_ids)
    tracks_by_id = {row["track_id"]: row for row in tracks["tracks"]}
    if len(tracks_by_id) != len(tracks["tracks"]):
        raise ValueError("duplicate physical track hypothesis ID")
    inputs = {key: {"path": str(path), "sha256": _sha256(path)} for key, path in (
        ("tracks", tracks_path), ("masks", masks_path),
        ("camera_receipt", camera_receipt_path), ("capture_index", capture_index_path),
        ("parent_facades", parent_facades_path))}
    verified, surfaces = set(), []
    for track_id, semantics in eligible:
        track = tracks_by_id[track_id]
        observations = []
        for frame in track["frames"]:
            cid = frame["capture_id"]
            if cid not in camera_ids or frame["mask_area_px"] <= 0:
                continue
            source = sources[cid]
            if cid not in verified:
                if _sha256(capture_index_path.parent / source["local_path"]) != source["sha256"].removeprefix("sha256:"):
                    raise ValueError(f"source pixels changed: {cid}")
                verified.add(cid)
            observations.append({
                "capture_id": cid, "observation_id": f"{track_id}:{cid}",
                "bbox_xyxy_px": frame["mask_bbox_xyxy_px"],
                "image_size_px": frame["mask_size_px"],
                "detector_confidence": 0.0,  # A propagated mask has no detector score.
                "detector_text_label": "/".join(semantics),
            })
        candidates = [project_child_to_parent(parent, observations, cameras)
                      for parent in parents["parents"]]
        passed = [row for row in candidates if row["gate"]["pass"]]
        failures = [] if len(passed) == 1 else [
            "ambiguous_parent" if passed else "no_parent_with_multiview_agreement"]
        surfaces.append({
            "schema": SCHEMA, "track_id": track_id,
            "semantic_class": semantics[0], "semantic_candidates": semantics,
            "geometry_owner": "bounded_parent_facade_projection", "inputs": inputs,
            "parent_candidates": candidates,
            "selected_parent_id": passed[0]["parent_id"] if len(passed) == 1 else None,
            "gate": {"pass": not failures, "failed": failures},
            "world_ir_promotion_ready": False, "canonical_feature_id": None,
            "stage": "parent_attached_region_candidate",
        })
    return {"schema": "y92.planar_track_reconstruction_batch.v1", "inputs": inputs,
            "surfaces": surfaces, "unresolved": unresolved,
            "counts": {"input_tracks": len(tracks["tracks"]),
                       "attempted_surfaces": len(surfaces),
                       "insufficient_camera_coverage": len(unresolved),
                       "geometry_gate_passed": sum(row["gate"]["pass"] for row in surfaces),
                       "canonical_features": 0},
            "world_ir_promotion_ready": False, "stage": "parent_attached_region_candidates"}


def build_batch(*, tracks_path, masks_path, camera_receipt_path, capture_index_path,
                parent_facades_path=None):
    if parent_facades_path is not None:
        return build_parent_batch(tracks_path=tracks_path, masks_path=masks_path,
                                  camera_receipt_path=camera_receipt_path,
                                  capture_index_path=capture_index_path,
                                  parent_facades_path=parent_facades_path)
    tracks = json.loads(tracks_path.read_text())
    cameras = json.loads(camera_receipt_path.read_text())
    eligible, unresolved = eligible_planar_tracks(
        tracks["tracks"], {row["capture_id"] for row in cameras["cameras"]},
    )
    surfaces = []
    for index, (track_id, semantics) in enumerate(eligible):
        result = build_document(
            tracks_path=tracks_path, masks_path=masks_path,
            camera_receipt_path=camera_receipt_path, capture_index_path=capture_index_path,
            semantic_class=semantics[0], track_id=track_id,
        )
        result["semantic_candidates"] = semantics
        surfaces.append(result)
        print(f"[reconstruct-planar-tracks] surface={index+1}/{len(eligible)} "
              f"gate={result['gate']['pass']}", flush=True)
    return {
        "schema": "y92.planar_track_reconstruction_batch.v1",
        "inputs": {key: {"path": str(path), "sha256": _sha256(path)} for key, path in (
            ("tracks", tracks_path), ("masks", masks_path),
            ("camera_receipt", camera_receipt_path), ("capture_index", capture_index_path),
        )},
        "counts": {"input_tracks": len(tracks["tracks"]), "attempted_surfaces": len(surfaces),
                   "insufficient_camera_coverage": len(unresolved),
                   "geometry_gate_passed": sum(row["gate"]["pass"] for row in surfaces),
                   "canonical_features": 0},
        "surfaces": surfaces, "unresolved": unresolved,
        "world_ir_promotion_ready": False,
        "stage": "planar_surface_geometry_candidates",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--masks", type=Path, required=True)
    parser.add_argument("--camera-receipt", type=Path, required=True)
    parser.add_argument("--capture-index", type=Path, required=True)
    parser.add_argument("--semantic-class")
    parser.add_argument("--all-tracks", action="store_true")
    parser.add_argument("--parent-facades", type=Path,
                        help="Bounded parent proposals for batch child projection")
    parser.add_argument("--track-id")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.all_tracks:
        if args.semantic_class or args.track_id:
            parser.error("--all-tracks cannot be combined with single-track selectors")
        document = build_batch(
            tracks_path=args.tracks, masks_path=args.masks,
            camera_receipt_path=args.camera_receipt, capture_index_path=args.capture_index,
            parent_facades_path=args.parent_facades,
        )
    else:
        if args.parent_facades:
            parser.error("--parent-facades requires --all-tracks")
        if not args.semantic_class:
            parser.error("provide --semantic-class or --all-tracks")
        document = build_document(
            tracks_path=args.tracks, masks_path=args.masks,
            camera_receipt_path=args.camera_receipt, capture_index_path=args.capture_index,
            semantic_class=args.semantic_class, track_id=args.track_id,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    if args.all_tracks:
        print(f"[reconstruct-planar-tracks] {document['counts']}")
        return 0
    print(
        f"[reconstruct-planar-tracks] class={args.semantic_class} "
        f"views={len(document['views'])} "
        f"points={(document['plane'] or {}).get('surface_points', 0)} "
        f"gate={'PASS' if document['gate']['pass'] else 'FAIL'} "
        f"failed={document['gate']['failed'] or 'none'} canonical=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
