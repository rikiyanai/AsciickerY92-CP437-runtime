"""Mixed-lens captures must retain their own calibrated image rays (FL-4812)."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.camera_solve.features import FrameFeatures, MatchGraph  # noqa: E402
from scripts.pipeline.camera_solve.relative import intrinsic_matrix, relative_pose  # noqa: E402
from scripts.pipeline.camera_solve.sfm import Reconstruction, reprojection_errors  # noqa: E402


def _project(points: np.ndarray, rotation: np.ndarray, translation: np.ndarray,
             intrinsic: np.ndarray) -> np.ndarray:
    camera = points @ rotation.T + translation
    image = camera @ intrinsic.T
    return image[:, :2] / image[:, 2, None]


def test_relative_pose_uses_each_frames_measured_focal_length():
    rng = np.random.default_rng(4812)
    points = np.column_stack([
        rng.uniform(-2.5, 2.5, 180),
        rng.uniform(-1.5, 1.5, 180),
        rng.uniform(7.0, 18.0, 180),
    ])
    rotation = cv2.Rodrigues(np.radians([1.5, -5.0, 2.0]))[0]
    translation = np.array([1.2, 0.05, 0.15])
    K_wide = intrinsic_matrix(800.0, 640.0, 480.0)
    K_tele = intrinsic_matrix(1600.0, 640.0, 480.0)
    left = _project(points, np.eye(3), np.zeros(3), K_wide)
    right = _project(points, rotation, translation, K_tele)

    pose = relative_pose(left, right, K_wide, K_tele, threshold_px=0.5)
    assert pose is not None
    delta = pose.rotation @ rotation.T
    rotation_error = np.degrees(np.linalg.norm(cv2.Rodrigues(delta)[0]))
    assert rotation_error < 0.05
    assert abs(float(pose.translation @ (translation / np.linalg.norm(translation)))) > 0.999
    assert pose.n_inliers >= 170


def test_reprojection_uses_the_intrinsic_owned_by_each_frame():
    point = np.array([0.8, -0.3, 8.0])
    K_wide = intrinsic_matrix(800.0, 640.0, 480.0)
    K_tele = intrinsic_matrix(1600.0, 640.0, 480.0)
    features = []
    for frame, intrinsic in enumerate((K_wide, K_tele)):
        uv = _project(point[None, :], np.eye(3), np.zeros(3), intrinsic)
        features.append(FrameFeatures(
            capture_id=f"frame-{frame}",
            xy=uv,
            descriptors=np.zeros((1, 128), dtype=np.float32),
            responses=np.ones(1, dtype=np.float32),
        ))
    graph = MatchGraph(features=features, tracks=[[(0, 0), (1, 0)]])
    reconstruction = Reconstruction(
        rvecs={0: np.zeros(3), 1: np.zeros(3)},
        tvecs={0: np.zeros(3), 1: np.zeros(3)},
        points={0: point},
        focal_px=800.0,
        cx=640.0,
        cy=480.0,
        frame_intrinsics={0: K_wide, 1: K_tele},
    )
    assert reprojection_errors(reconstruction, graph) == pytest.approx([0.0, 0.0])

    reconstruction.frame_intrinsics = {}
    assert reprojection_errors(reconstruction, graph)[1] > 70.0
