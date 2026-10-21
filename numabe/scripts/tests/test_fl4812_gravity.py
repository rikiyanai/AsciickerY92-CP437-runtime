import math

import cv2
import numpy as np
import pytest

from scripts.pipeline.camera_solve.georegister import GeoSolution
from scripts.pipeline.camera_solve.gravity import (
    VerticalLineObservation,
    level_solution,
    solve_gravity,
)


def _rz(degrees: float) -> np.ndarray:
    angle = math.radians(degrees)
    return np.array([
        [math.cos(angle), -math.sin(angle), 0.0],
        [math.sin(angle), math.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])


def _line_through(point: np.ndarray, vanishing: np.ndarray, length: float = 140.0):
    direction = vanishing - point
    direction /= np.linalg.norm(direction)
    return tuple(point), tuple(point + direction * length)


def test_shared_vertical_lines_recover_and_level_gravity_without_changing_projection():
    focal, cx, cy = 1200.0, 640.0, 480.0
    intrinsic = np.array([[focal, 0.0, cx], [0.0, focal, cy], [0.0, 0.0, 1.0]])
    planted_up = np.array([0.22, -0.17, 0.9606])
    planted_up /= np.linalg.norm(planted_up)
    rotations = {frame: _rz(frame * 13.0) for frame in range(4)}
    observations = []
    for frame, rotation in rotations.items():
        homogeneous = intrinsic @ rotation @ planted_up
        vanishing = homogeneous[:2] / homogeneous[2]
        for index in range(12):
            base = np.array([120.0 + index * 41.0, 700.0 - (index % 3) * 35.0])
            p0, p1 = _line_through(base, vanishing)
            observations.append(VerticalLineObservation(frame, p0, p1, 140.0 + index))
    # Add horizontal clutter. It must lose to the shared multi-frame family.
    for index in range(12):
        observations.append(VerticalLineObservation(index % 4, (10.0, 20.0 + index), (200.0, 20.0 + index), 190.0))

    estimate = solve_gravity(observations, rotations, intrinsic)
    assert abs(float(estimate.up_vector @ planted_up)) > 0.999
    assert estimate.report["gate"]["pass"] is True
    assert estimate.report["inlier_frame_count"] == 4

    solution = GeoSolution(
        frames=list(rotations),
        rvecs={frame: cv2.Rodrigues(rotation)[0].ravel() for frame, rotation in rotations.items()},
        centers={frame: np.array([float(frame), 0.2 * frame, 0.0]) for frame in rotations},
        points={0: np.array([2.0, 1.0, 8.0]), 1: np.array([-1.0, 2.0, 10.0])},
        focal_px=focal,
        cx=cx,
        cy=cy,
        focal_free=False,
    )
    leveled, report = level_solution(solution, estimate)
    assert report["max_rigid_reprojection_delta_px"] < 1e-8
    # The exact estimated input up axis becomes ENU +Z.
    from scripts.pipeline.camera_solve.gravity import rotation_from_to
    transform = rotation_from_to(estimate.up_vector, np.array([0.0, 0.0, 1.0]))
    assert transform @ estimate.up_vector == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)
    assert set(leveled.centers) == set(solution.centers)


def test_gravity_refuses_a_single_frame_even_with_many_lines():
    intrinsic = np.eye(3)
    rotations = {0: np.eye(3)}
    observations = [
        VerticalLineObservation(0, (float(i), 0.0), (float(i), 100.0), 100.0)
        for i in range(30)
    ]
    with pytest.raises(RuntimeError, match="gravity consensus"):
        solve_gravity(observations, rotations, intrinsic)


def test_gravity_checks_camera_geometry_for_near_plane_offscreen_points(monkeypatch):
    from scripts.pipeline.camera_solve.gravity import GravityEstimate
    import scripts.pipeline.camera_solve.gravity as owner
    up = np.array([0.2, 0.1, 0.97]); up /= np.linalg.norm(up)
    estimate = GravityEstimate(up, [], [], np.array([]), {"gate": {"pass": True}})
    solution = GeoSolution(
        frames=[0], rvecs={0: np.zeros(3)}, centers={0: np.array([3., 4., 5.])},
        points={0: np.array([4., 5., 5.000000001]), 1: np.array([3., 4., 15.])},
        focal_px=256., cx=256., cy=256., focal_free=False,
    )
    _, report = level_solution(solution, estimate)
    assert report["max_rigid_reprojection_delta_px"] > 1e-5
    assert report["max_in_image_reprojection_delta_px"] < 1e-5
    assert report["max_relative_camera_coordinate_delta"] < 1e-10
    assert report["invariance_pairs"] == 2
    assert report["in_image_projection_pairs"] == 1
    original = owner.rotation_from_to
    monkeypatch.setattr(owner, "rotation_from_to", lambda *args: original(*args) * 1.001)
    with pytest.raises(RuntimeError, match="camera-space geometry"):
        level_solution(solution, estimate)
