"""Two-view geometry: essential matrix, relative pose, DLT triangulation.

FL-4812.  This is the "relative reconstruction" half of the chain, before any
world coordinate exists.  Everything here is defined up to an unknown scale;
:mod:`georegister` is the only module allowed to introduce metres.

THE RANSAC SEED
===============
``cv2.findEssentialMat`` and ``cv2.solvePnPRansac`` draw from OpenCV's global
RNG.  Left alone they make the whole reconstruction irreproducible, which
would defeat "same frozen inputs -> same outputs".  :func:`seed_rng` pins it
and the seed is written into the receipt, so a rerun that disagrees is a real
regression rather than sampling noise.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

DEFAULT_SEED = 4812
ESSENTIAL_THRESHOLD_PX = 1.5
ESSENTIAL_CONFIDENCE = 0.99999


def seed_rng(seed: int = DEFAULT_SEED) -> int:
    cv2.setRNGSeed(int(seed))
    np.random.seed(int(seed) % (2**32))
    return int(seed)


def intrinsic_matrix(focal_px: float, cx: float, cy: float) -> np.ndarray:
    return np.array([[focal_px, 0.0, cx], [0.0, focal_px, cy], [0.0, 0.0, 1.0]], dtype=np.float64)


@dataclass
class RelativePose:
    """Pose of frame B in frame A's coordinates, with ||t|| == 1 by construction."""

    rotation: np.ndarray  # (3, 3)
    translation: np.ndarray  # (3,), unit length, scale unknown
    inliers: np.ndarray  # bool mask over the supplied correspondences
    n_inliers: int
    median_epipolar_px: float


def _sampson_px(F: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    """Sampson distance, the first-order geometric epipolar error in pixels."""
    h1 = np.hstack([p1, np.ones((len(p1), 1))])
    h2 = np.hstack([p2, np.ones((len(p2), 1))])
    Fx1 = h1 @ F.T
    Ftx2 = h2 @ F
    num = np.einsum("ij,ij->i", h2, Fx1) ** 2
    den = Fx1[:, 0] ** 2 + Fx1[:, 1] ** 2 + Ftx2[:, 0] ** 2 + Ftx2[:, 1] ** 2
    return np.sqrt(num / np.maximum(den, 1e-12))


def relative_pose(p1: np.ndarray, p2: np.ndarray, K1: np.ndarray,
                  K2: np.ndarray | None = None,
                  threshold_px: float = ESSENTIAL_THRESHOLD_PX) -> RelativePose | None:
    """Essential matrix + cheirality-resolved pose for a calibrated frame pair.

    ``K2`` may differ from ``K1``.  Normalizing each image independently is
    required for mixed-lens sequences; applying frame A's focal length to frame
    B changes the rays before RANSAC and can produce a precise reconstruction
    of the wrong camera geometry.
    """
    if len(p1) < 8:
        return None
    if K2 is None:
        K2 = K1
    n1 = cv2.undistortPoints(
        np.asarray(p1, dtype=np.float64).reshape(-1, 1, 2), K1, None
    ).reshape(-1, 2)
    n2 = cv2.undistortPoints(
        np.asarray(p2, dtype=np.float64).reshape(-1, 1, 2), K2, None
    ).reshape(-1, 2)
    normalized_threshold = threshold_px / max(
        0.5 * (float(K1[0, 0]) + float(K2[0, 0])), 1e-9
    )
    E, mask = cv2.findEssentialMat(
        n1, n2, np.eye(3), method=cv2.USAC_MAGSAC,
        prob=ESSENTIAL_CONFIDENCE, threshold=normalized_threshold,
    )
    if E is None or mask is None or E.shape != (3, 3):
        return None
    mask = mask.ravel().astype(bool)
    if int(mask.sum()) < 8:
        return None
    # recoverPose picks the one of four decompositions with points in front of
    # both cameras; without it the reconstruction can silently mirror.
    n_good, R, t, pose_mask = cv2.recoverPose(
        E, n1[mask], n2[mask], np.eye(3)
    )
    inliers = np.zeros(len(p1), dtype=bool)
    idx = np.flatnonzero(mask)
    inliers[idx[pose_mask.ravel().astype(bool)]] = True
    if int(inliers.sum()) < 8:
        return None
    F = np.linalg.inv(K2).T @ E @ np.linalg.inv(K1)
    err = _sampson_px(F, p1[inliers], p2[inliers])
    return RelativePose(
        rotation=np.asarray(R, dtype=np.float64),
        translation=np.asarray(t, dtype=np.float64).ravel(),
        inliers=inliers,
        n_inliers=int(inliers.sum()),
        median_epipolar_px=float(np.median(err)),
    )


def triangulate_dlt(projections: list[np.ndarray], points_2d: list[np.ndarray]) -> np.ndarray:
    """Linear multi-view triangulation of ONE world point.

    ``projections`` are 3x4 camera matrices ``P = K [R|t]``; ``points_2d`` the
    matching pixel observations.  Solved by SVD of the stacked cross-product
    constraints, which is the standard DLT and needs no initial guess.
    """
    rows = []
    for P, (u, v) in zip(projections, points_2d):
        rows.append(u * P[2] - P[0])
        rows.append(v * P[2] - P[1])
    A = np.asarray(rows, dtype=np.float64)
    _, _, vt = np.linalg.svd(A)
    X = vt[-1]
    if abs(X[3]) < 1e-12:
        return np.array([np.nan, np.nan, np.nan])
    return X[:3] / X[3]


def triangulation_angle_deg(centers: np.ndarray, point: np.ndarray) -> float:
    """Widest angle any two viewpoints subtend at the point.

    This is the number that decides whether a triangulation is a measurement
    or a guess: a point seen from ten near-identical viewpoints has ten
    observations and no depth information at all.
    """
    rays = point[None, :] - centers
    norms = np.linalg.norm(rays, axis=1)
    if np.any(norms < 1e-9):
        return 0.0
    rays = rays / norms[:, None]
    cos = np.clip(rays @ rays.T, -1.0, 1.0)
    np.fill_diagonal(cos, 1.0)
    return float(np.degrees(np.arccos(cos.min())))


def project(points_3d: np.ndarray, rvec: np.ndarray, tvec: np.ndarray,
            focal_px: float, cx: float, cy: float) -> np.ndarray:
    """Pinhole projection, no distortion model.

    The source states no distortion coefficients.  Modelling none is therefore
    honest; it also means residuals carry the lens distortion that is really
    there, which is why the receipt reports the residual distribution instead
    of only its median.
    """
    R = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))[0]
    cam = points_3d @ R.T + np.asarray(tvec, dtype=np.float64)
    z = cam[:, 2]
    uv = np.empty((len(points_3d), 2), dtype=np.float64)
    uv[:, 0] = focal_px * cam[:, 0] / z + cx
    uv[:, 1] = focal_px * cam[:, 1] / z + cy
    return uv


def camera_center(rvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    R = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))[0]
    return -R.T @ np.asarray(tvec, dtype=np.float64)


def optical_axis(rvec: np.ndarray) -> np.ndarray:
    """World-space unit vector the camera looks along."""
    R = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))[0]
    return R.T @ np.array([0.0, 0.0, 1.0])
