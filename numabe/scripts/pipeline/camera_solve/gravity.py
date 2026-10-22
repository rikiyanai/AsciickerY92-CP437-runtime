"""Route-wide gravity estimation from repeated vertical image structure.

The relative camera solve determines structure only up to a global rotation.
GPS and compass constrain position and yaw; they do not prove which direction
is vertical.  This owner estimates one shared gravity axis from long vertical
line families across the image corpus, then rotates the entire rigid visual
block into the ENU gravity frame before absolute registration.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from .georegister import GeoSolution


@dataclass(frozen=True)
class VerticalLineObservation:
    frame: int
    p0: tuple[float, float]
    p1: tuple[float, float]
    length_px: float


@dataclass
class GravityEstimate:
    up_vector: np.ndarray
    observations: list[VerticalLineObservation]
    inlier_indices: list[int]
    angular_residual_deg: np.ndarray
    report: dict


def detect_vertical_lines(
    image_path: str | Path,
    frame: int,
    *,
    minimum_length_fraction: float = 0.035,
    maximum_image_vertical_deviation_deg: float = 38.0,
) -> list[VerticalLineObservation]:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot read gravity source image: {image_path}")
    detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    detected = detector.detect(image)[0]
    if detected is None:
        return []
    minimum = min(image.shape[:2]) * minimum_length_fraction
    observations: list[VerticalLineObservation] = []
    for raw in detected[:, 0, :]:
        x0, y0, x1, y1 = (float(value) for value in raw)
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < minimum:
            continue
        deviation = math.degrees(math.atan2(abs(dx), abs(dy)))
        if deviation > maximum_image_vertical_deviation_deg:
            continue
        observations.append(VerticalLineObservation(frame, (x0, y0), (x1, y1), length))
    observations.sort(key=lambda row: (-row.length_px, row.p0, row.p1))
    return observations


def _constraint_rows(
    observations: Iterable[VerticalLineObservation],
    rotations: dict[int, np.ndarray],
    intrinsic: np.ndarray | dict[int, np.ndarray],
) -> tuple[list[VerticalLineObservation], np.ndarray, np.ndarray]:
    kept: list[VerticalLineObservation] = []
    rows: list[np.ndarray] = []
    weights: list[float] = []
    for observation in observations:
        rotation = rotations.get(observation.frame)
        if rotation is None:
            continue
        frame_intrinsic = intrinsic[observation.frame] if isinstance(intrinsic, dict) else intrinsic
        p0 = np.array([*observation.p0, 1.0])
        p1 = np.array([*observation.p1, 1.0])
        image_line = np.cross(p0, p1)
        norm = np.linalg.norm(image_line[:2])
        if norm < 1e-12:
            continue
        image_line /= norm
        row = image_line @ frame_intrinsic @ rotation
        row_norm = np.linalg.norm(row)
        if row_norm < 1e-12:
            continue
        kept.append(observation)
        rows.append(row / row_norm)
        weights.append(math.sqrt(max(observation.length_px, 1.0)))
    return kept, np.asarray(rows), np.asarray(weights)


def solve_gravity(
    observations: Iterable[VerticalLineObservation],
    rotations: dict[int, np.ndarray],
    intrinsic: np.ndarray | dict[int, np.ndarray],
    *,
    seed: int = 4812,
    ransac_iterations: int = 5000,
    inlier_threshold_deg: float = 2.5,
    minimum_inliers: int = 24,
    minimum_frames: int = 3,
) -> GravityEstimate:
    kept, rows, weights = _constraint_rows(observations, rotations, intrinsic)
    if len(kept) < minimum_inliers:
        raise ValueError(f"gravity needs at least {minimum_inliers} vertical line observations, got {len(kept)}")
    rng = np.random.default_rng(seed)
    threshold = math.sin(math.radians(inlier_threshold_deg))
    best: tuple[float, int, np.ndarray, np.ndarray] | None = None
    for _ in range(ransac_iterations):
        left, right = rng.choice(len(rows), size=2, replace=False)
        candidate = np.cross(rows[left], rows[right])
        norm = np.linalg.norm(candidate)
        if norm < 1e-9:
            continue
        candidate /= norm
        if candidate[2] < 0:
            candidate *= -1.0
        raw = np.abs(rows @ candidate)
        inliers = raw <= threshold
        frames = {kept[index].frame for index in np.flatnonzero(inliers)}
        if len(frames) < minimum_frames:
            continue
        score = float(np.sum(weights[inliers] * (1.0 - raw[inliers] / threshold)))
        rank = (score, int(np.sum(inliers)), candidate, inliers)
        if best is None or rank[:2] > best[:2]:
            best = rank
    if best is None or best[1] < minimum_inliers:
        raise RuntimeError("no route-wide gravity consensus passed the line/frame gates")

    inliers = best[3]
    # Refit the shared null direction with length weighting, then reclassify
    # once.  This is deterministic for a frozen observation bundle.
    weighted = rows[inliers] * weights[inliers, None]
    _, _, vh = np.linalg.svd(weighted, full_matrices=False)
    up = vh[-1]
    up /= np.linalg.norm(up)
    if up[2] < 0:
        up *= -1.0
    raw = np.abs(rows @ up)
    inliers = raw <= threshold
    residual = np.degrees(np.arcsin(np.clip(raw, 0.0, 1.0)))
    indices = [int(index) for index in np.flatnonzero(inliers)]
    frames = sorted({kept[index].frame for index in indices})
    inlier_residual = residual[inliers]
    report = {
        "model": "shared_vertical_vanishing_direction",
        "up_vector_in_input_block": [float(value) for value in up],
        "observations": len(kept),
        "inliers": len(indices),
        "inlier_frames": frames,
        "inlier_frame_count": len(frames),
        "angular_residual_deg": {
            "median": float(np.median(inlier_residual)),
            "p95": float(np.percentile(inlier_residual, 95)),
            "max": float(np.max(inlier_residual)),
        },
        "gate": {
            "minimum_inliers": minimum_inliers,
            "minimum_frames": minimum_frames,
            "threshold_deg": inlier_threshold_deg,
            "pass": len(indices) >= minimum_inliers and len(frames) >= minimum_frames,
        },
    }
    return GravityEstimate(up, kept, indices, residual, report)


def rotation_from_to(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = np.asarray(source, dtype=np.float64) / np.linalg.norm(source)
    target = np.asarray(target, dtype=np.float64) / np.linalg.norm(target)
    cross = np.cross(source, target)
    dot = float(np.clip(source @ target, -1.0, 1.0))
    norm = np.linalg.norm(cross)
    if norm < 1e-12:
        if dot > 0:
            return np.eye(3)
        axis = np.array([1.0, 0.0, 0.0])
        if abs(source @ axis) > 0.9:
            axis = np.array([0.0, 1.0, 0.0])
        axis -= source * (source @ axis)
        axis /= np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    axis = cross / norm
    skew = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    angle = math.acos(dot)
    return np.eye(3) + math.sin(angle) * skew + (1.0 - math.cos(angle)) * (skew @ skew)


def level_solution(solution: GeoSolution, estimate: GravityEstimate) -> tuple[GeoSolution, dict]:
    if estimate.report["gate"]["pass"] is not True:
        raise ValueError("gravity estimate did not pass its evidence gate")
    transform = rotation_from_to(estimate.up_vector, np.array([0.0, 0.0, 1.0]))
    pivot = np.mean([solution.centers[frame] for frame in solution.frames], axis=0)
    leveled = GeoSolution(
        frames=list(solution.frames),
        rvecs={
            frame: cv2.Rodrigues(solution.rotation(frame) @ transform.T)[0].ravel()
            for frame in solution.frames
        },
        centers={
            frame: transform @ (solution.centers[frame] - pivot) + pivot
            for frame in solution.frames
        },
        points={track: transform @ (point - pivot) + pivot for track, point in solution.points.items()},
        focal_px=solution.focal_px,
        cx=solution.cx,
        cy=solution.cy,
        focal_free=solution.focal_free,
        frame_intrinsics={
            frame: value.copy() for frame, value in solution.frame_intrinsics.items()
        },
    )
    projection_delta = []
    in_image_delta = []
    camera_coordinate_delta = []
    for frame in solution.frames:
        for track, point in solution.points.items():
            before_xyz = solution.rotation(frame) @ (point - solution.centers[frame])
            after_xyz = leveled.rotation(frame) @ (leveled.points[track] - leveled.centers[frame])
            relative_error = float(np.linalg.norm(before_xyz - after_xyz)) / max(
                float(np.linalg.norm(before_xyz)), 1e-12,
            )
            if not np.isfinite(relative_error) or relative_error > 1e-10:
                raise RuntimeError("gravity levelling changed camera-space geometry")
            camera_coordinate_delta.append(relative_error)
            before, before_depth = solution.project(frame, point)
            after, after_depth = leveled.project(frame, leveled.points[track])
            if before_depth > 0 and after_depth > 0:
                delta = float(np.linalg.norm(before - after))
                projection_delta.append(delta)
                _, cx, cy = solution.projection_parameters(frame)
                if 0 <= before[0] < 2 * cx and 0 <= before[1] < 2 * cy:
                    in_image_delta.append(delta)
    optical_pitches = {}
    for frame in leveled.frames:
        forward = leveled.rotation(frame).T @ np.array([0.0, 0.0, 1.0])
        optical_pitches[str(frame)] = math.degrees(math.atan2(forward[2], math.hypot(forward[0], forward[1])))
    report = {
        **estimate.report,
        "gravity_to_world_z_rotation_deg": math.degrees(
            math.acos(float(np.clip(estimate.up_vector @ np.array([0.0, 0.0, 1.0]), -1.0, 1.0)))
        ),
        "max_rigid_reprojection_delta_px": max(projection_delta, default=0.0),
        "max_in_image_reprojection_delta_px": max(in_image_delta, default=0.0),
        "max_relative_camera_coordinate_delta": max(camera_coordinate_delta, default=0.0),
        "invariance_pairs": len(camera_coordinate_delta),
        "in_image_projection_pairs": len(in_image_delta),
        "leveled_camera_optical_pitch_deg": optical_pitches,
    }
    # FL-4812: near-zero-depth off-screen points have arbitrarily large
    # projected coordinates. Absolute pixel error there is ill-conditioned.
    # Audit every pair in camera space above; keep the original pixel bound
    # over the finite image domain and retain the off-screen maximum as data.
    if report["max_in_image_reprojection_delta_px"] > 1e-5:
        raise RuntimeError(
            "gravity levelling changed rigid visual reprojection: "
            f"max_delta_px={report['max_in_image_reprojection_delta_px']:.12g}"
        )
    return leveled, report


def estimate_from_solution(
    solution: GeoSolution,
    image_paths: dict[int, str | Path],
    captures: dict[int, object],
    *,
    seed: int = 4812,
) -> GravityEstimate:
    observations = [
        observation
        for frame, path in sorted(image_paths.items())
        for observation in detect_vertical_lines(path, frame)
    ]
    intrinsic = {}
    for frame in solution.frames:
        capture = captures[frame]
        intrinsic[frame] = np.array([
            [capture.intrinsics.focal_px, 0.0, capture.principal_point[0]],
            [0.0, capture.intrinsics.focal_px, capture.principal_point[1]],
            [0.0, 0.0, 1.0],
        ])
    rotations = {frame: solution.rotation(frame) for frame in solution.frames}
    return solve_gravity(observations, rotations, intrinsic, seed=seed)
