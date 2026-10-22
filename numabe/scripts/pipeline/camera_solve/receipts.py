"""Numeric residual + covariance receipt for one camera solve (FL-4812).

A receipt exists so a reader who was not there can tell an accepted solve from
a tuned one.  It therefore records what FAILED as readily as what passed, and
it records distributions rather than only their medians: a median of 1 px over
observations whose worst is 40 px is a different solve from one whose worst is
3 px, and only the second is acceptable.

Nothing in here decides anything.  Every gate decision is made in :mod:`gates`
and merely transcribed, so a receipt can never disagree with the run that
produced it.
"""

from __future__ import annotations

import math

import hashlib
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .capture import Capture, horizontal_fov_deg
from .georegister import GeoSolution
from .relative import triangulation_angle_deg
from .triangulate import covariance_record


def distribution(values: Sequence[float] | np.ndarray) -> dict:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0}
    return {
        "count": int(arr.size),
        "min": float(arr.min()),
        "median": float(np.median(arr)),
        "mean": float(arr.mean()),
        "p90": float(np.percentile(arr, 90)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(arr.max()),
        "rms": float(np.sqrt(np.mean(arr ** 2))),
    }


def file_digest(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def tool_versions() -> dict:
    import cv2
    import scipy

    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "note": (
            "no package was added to scripts/pipeline/requirements.txt; "
            "cv2/numpy/scipy were already importable in this interpreter"
        ),
    }


def camera_record(solution: GeoSolution, frame: int, capture: Capture) -> dict:
    center = solution.centers[frame]
    cov = solution.center_covariance.get(frame)
    rot_cov = solution.rotation_covariance.get(frame)
    gps_delta = center - np.asarray(capture.enu)
    return {
        "frame_rank": capture.rank,
        "capture_id": capture.capture_id,
        "solved_center_enu_m": [float(v) for v in center],
        "solved_rotation_rodrigues": [float(v) for v in solution.rvecs[frame]],
        "solved_azimuth_deg": solution.azimuth_deg(frame),
        "prior_azimuth_deg": capture.azimuth_deg,
        "prior_gps_enu_m": [float(v) for v in capture.enu],
        "source_image": {
            "sha256": capture.digest,
            "dimensions_px": list(capture.source_dimensions_px or (capture.width, capture.height)),
        },
        "working_image": {
            "sha256": capture.working_image_digest or capture.digest,
            "dimensions_px": [capture.width, capture.height],
            "policy": capture.working_image_policy,
        },
        "gps_residual_m": {
            "east": float(gps_delta[0]),
            "north": float(gps_delta[1]),
            "up": float(gps_delta[2]),
            "horizontal": float(np.hypot(gps_delta[0], gps_delta[1])),
        },
        "center_covariance": covariance_record(cov),
        "rotation_covariance_rad2": covariance_record(rot_cov),
    }


def sparse_reconstruction_record(
    solution: GeoSolution,
    graph: Any,
    captures: Mapping[int, Capture],
) -> dict:
    """Freeze the accepted SfM points and the pixels that support each point.

    A camera receipt that retains poses but discards the solved point/track
    graph cannot drive facade association.  Downstream code then has to rerun
    SIFT or infer geometry from detector-box centroids, which is exactly how a
    visually plausible station sign acquired a false plane.  This record keeps
    only points that survived the solve's reprojection filter, along with both
    working-image and source-image pixel coordinates.
    """
    points = []
    for track_index in sorted(solution.points):
        position = solution.points[track_index]
        observations = []
        centers = []
        for frame, keypoint_index in graph.tracks[track_index]:
            if frame not in solution.centers or frame not in captures:
                continue
            capture = captures[frame]
            pixel = graph.features[frame].xy[keypoint_index]
            projected, depth = solution.project(frame, position)
            residual = float(np.linalg.norm(projected - pixel))
            source_width, source_height = (
                capture.source_dimensions_px or (capture.width, capture.height)
            )
            source_pixel = [
                float(pixel[0]) * source_width / capture.width,
                float(pixel[1]) * source_height / capture.height,
            ]
            observations.append({
                "capture_id": capture.capture_id,
                "frame_rank": capture.rank,
                "keypoint_index": int(keypoint_index),
                "working_image_pixel_px": [float(pixel[0]), float(pixel[1])],
                "source_image_pixel_px": source_pixel,
                "reprojection_error_px": residual,
                "depth_m": float(depth),
            })
            centers.append(solution.centers[frame])
        if len(observations) < 2:
            continue
        points.append({
            "point_id": f"sfm-point:{track_index}",
            "track_index": int(track_index),
            "position_enu_m": [float(value) for value in position],
            "viewpoints": len(observations),
            "maximum_triangulation_angle_deg": triangulation_angle_deg(
                centers, position
            ),
            "covariance_absolute": covariance_record(
                solution.point_covariance.get(track_index)
            ),
            "observations": observations,
        })
    return {
        "schema": "y92.sparse_reconstruction.v1",
        "coordinate_frame": "control_enu",
        "points": points,
        "counts": {
            "points": len(points),
            "observations": sum(len(point["observations"]) for point in points),
        },
        "evidence_state": "accepted_after_bundle_adjustment_and_reprojection_filter",
    }


def build(
    *,
    corridor: Mapping[str, Any],
    captures: Mapping[int, Capture],
    solution: GeoSolution,
    stages: Mapping[str, Any],
    per_observation_px: Sequence[float],
    per_camera_px: Mapping[int, Sequence[float]],
    candidates: Sequence[Mapping[str, Any]],
    parameters: Mapping[str, Any],
    graph: Any | None = None,
) -> dict:
    """Assemble the full receipt."""
    focal = solution.focal_px
    width = captures[solution.frames[0]].width
    receipt = {
        "receipt_id": "fl4812_camera_solve",
        "evidence_role": "CONTROL / METHOD PROOF ONLY",
        "evidence_role_note": (
            "This receipt proves only the recorded observation-to-world geometric solve. "
            "It is not acceptance evidence for a complete playable corridor."
        ),
        "corridor": dict(corridor),
        "tool_versions": tool_versions(),
        "parameters": dict(parameters),
        "intrinsics": {
            "focal_px_solved": focal,
            # None, not NaN: json.dump writes a bare ``NaN`` token that no
            # strict JSON reader accepts, and the sigma genuinely does not
            # exist when the focal was not free in the solve.
            "focal_sigma_px": (
                float(solution.focal_sigma_px)
                if math.isfinite(solution.focal_sigma_px) else None
            ),
            "focal_state": captures[solution.frames[0]].intrinsics.state,
            "focal_basis": captures[solution.frames[0]].intrinsics.basis,
            "focal_prior_px": captures[solution.frames[0]].intrinsics.focal_px,
            "focal_prior_sigma_px": captures[solution.frames[0]].intrinsics.sigma_px,
            "horizontal_fov_deg_solved": horizontal_fov_deg(focal, width),
            "principal_point_px": [solution.cx, solution.cy],
            "distortion_model": "none (source states no distortion coefficients)",
            "model": (
                "shared_refined_focal"
                if solution.focal_free else "per_frame_fixed_calibration"
            ),
            "per_frame": {
                str(captures[frame].rank): {
                    "capture_id": captures[frame].capture_id,
                    "focal_px": solution.projection_parameters(frame)[0],
                    "principal_point_px": list(
                        solution.projection_parameters(frame)[1:]
                    ),
                    "state": captures[frame].intrinsics.state,
                    "basis": captures[frame].intrinsics.basis,
                }
                for frame in solution.frames
            },
        },
        "stages": dict(stages),
        "global_solve": solution.report,
        "residuals": {
            "all_visual_observations_px": distribution(per_observation_px),
            "per_camera_px": {
                str(captures[f].rank): distribution(v) for f, v in sorted(per_camera_px.items())
            },
        },
        "cameras": [camera_record(solution, f, captures[f]) for f in solution.frames],
        "candidates": [dict(c) for c in candidates],
    }
    if graph is not None:
        receipt["sparse_reconstruction"] = sparse_reconstruction_record(
            solution, graph, captures
        )
    return receipt
