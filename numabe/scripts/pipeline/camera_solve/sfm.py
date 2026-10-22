"""Incremental sparse reconstruction and bundle adjustment (FL-4812).

This module produces a SCALE-FREE reconstruction: camera poses and a sparse
point cloud that are correct up to one unknown similarity transform.  It never
touches GPS, OSM or metres; that separation is deliberate, because it is what
makes the later georegistration an inspectable weighted decision rather than a
step that quietly assumed one source was right.

GAUGE
=====
A pure-visual bundle adjustment has seven unobservable degrees of freedom
(3 rotation, 3 translation, 1 scale).  Left free, the normal equations are
singular and any covariance read off them is meaningless.  Here camera 0 is
held fixed and a soft unit-baseline residual pins the scale, so the relative
solve is well posed.  The global solve in :mod:`georegister` fixes the same
seven differently - with real priors - and that is where covariance is taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from .features import FrameFeatures, MatchGraph, TrackBuilder, match_pair
from .relative import (
    intrinsic_matrix,
    relative_pose,
    triangulate_dlt,
    triangulation_angle_deg,
)

# A forward-walking sequence puts most of the image near the epipole, where the
# parallax is nearly zero and a point can be "triangulated" hundreds of metres
# away with a tiny reprojection error.  Those points are not measurements, and
# left in they dominate the point count and drag the global solve.  The floor
# here is the same 3 deg the promotion gate demands, so structure that could
# never be promoted is never carried either.
MIN_TRIANGULATION_ANGLE_DEG = 3.0
MAX_RECONSTRUCTION_REPROJECTION_PX = 4.0
PNP_REPROJECTION_PX = 4.0
BASELINE_GAUGE_WEIGHT = 1000.0
MIN_GLOBAL_TRACK_VIEWPOINTS = 2
MIN_GLOBAL_CAMERA_TRACKS = 6


@dataclass
class Reconstruction:
    """Poses (world->camera) and structure, in an arbitrary similarity frame."""

    rvecs: dict[int, np.ndarray] = field(default_factory=dict)
    tvecs: dict[int, np.ndarray] = field(default_factory=dict)
    points: dict[int, np.ndarray] = field(default_factory=dict)  # track index -> XYZ
    focal_px: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    frame_intrinsics: dict[int, np.ndarray] = field(default_factory=dict)

    @property
    def registered(self) -> list[int]:
        return sorted(self.rvecs)

    def center(self, frame: int) -> np.ndarray:
        R = cv2.Rodrigues(self.rvecs[frame])[0]
        return -R.T @ self.tvecs[frame]

    def intrinsic(self, frame: int) -> np.ndarray:
        """Return the calibration owned by this exact frame."""
        return self.frame_intrinsics.get(
            frame, intrinsic_matrix(self.focal_px, self.cx, self.cy)
        )

    def observations(self, graph: MatchGraph) -> list[tuple[int, int, np.ndarray]]:
        """Every (frame, track, uv) whose frame is posed and track is triangulated."""
        out = []
        for track_index, members in enumerate(graph.tracks):
            if track_index not in self.points:
                continue
            for frame, kp in members:
                if frame in self.rvecs:
                    out.append((frame, track_index, graph.features[frame].xy[kp]))
        return out


def prune_for_global_solve(
    recon: Reconstruction,
    graph: MatchGraph,
    *,
    min_track_viewpoints: int = MIN_GLOBAL_TRACK_VIEWPOINTS,
    min_camera_tracks: int = MIN_GLOBAL_CAMERA_TRACKS,
) -> dict:
    """Retain the multi-view core that can support absolute registration.

    Incremental PnP can temporarily register a camera from points that are
    later rejected by the common reprojection gate. Carrying that camera into
    the absolute solve leaves six pose parameters supported only by five
    GPS/heading/roll prior rows. That case made the normal matrix numerically
    indefinite on the first real Mapillary 360 sequence. Two-view tracks remain
    eligible because they are valid supporting geometry; three views stay the
    preferred promotion standard for hero features.

    Pruning is iterative because removing an unsupported camera can reduce a
    track below the multi-view threshold. Discarded tracks remain in the match
    receipt; they simply do not become canonical absolute 3D.
    """
    original_frames = set(recon.registered)
    original_points = set(recon.points)
    active_frames = set(original_frames)
    active_points = set(original_points)

    while True:
        next_points = {
            track
            for track in active_points
            if sum(1 for frame, _ in graph.tracks[track] if frame in active_frames)
            >= min_track_viewpoints
        }
        support = {frame: 0 for frame in active_frames}
        for track in next_points:
            for frame, _ in graph.tracks[track]:
                if frame in support:
                    support[frame] += 1
        next_frames = {
            frame for frame, count in support.items() if count >= min_camera_tracks
        }
        if next_frames == active_frames and next_points == active_points:
            break
        active_frames, active_points = next_frames, next_points

    if len(active_frames) < 3:
        raise ValueError(
            "multi-view stability pruning retained fewer than 3 cameras; "
            "the component cannot support absolute registration"
        )
    recon.rvecs = {frame: recon.rvecs[frame] for frame in sorted(active_frames)}
    recon.tvecs = {frame: recon.tvecs[frame] for frame in sorted(active_frames)}
    recon.frame_intrinsics = {
        frame: value for frame, value in recon.frame_intrinsics.items()
        if frame in active_frames
    }
    recon.points = {track: recon.points[track] for track in sorted(active_points)}
    return {
        "minimum_track_viewpoints": min_track_viewpoints,
        "minimum_camera_tracks": min_camera_tracks,
        "frames_before": len(original_frames),
        "frames_after": len(active_frames),
        "frames_dropped": len(original_frames - active_frames),
        "points_before": len(original_points),
        "points_after": len(active_points),
        "points_dropped": len(original_points - active_points),
    }


def _intrinsic_for(
    intrinsics: np.ndarray | Mapping[int, np.ndarray], frame: int
) -> np.ndarray:
    return intrinsics[frame] if isinstance(intrinsics, Mapping) else intrinsics


def build_match_graph(features: list[FrameFeatures],
                      intrinsics: np.ndarray | Mapping[int, np.ndarray],
                      window: int = 3) -> tuple[MatchGraph, dict]:
    """Match every frame pair inside ``window`` and chain the survivors into tracks.

    A window wider than consecutive pairs matters: a point seen in frames i and
    i+2 gets roughly twice the baseline of one seen in i and i+1, and the
    triangulation-angle gate downstream is what the extra baseline buys.
    """
    graph = MatchGraph(features=features)
    builder = TrackBuilder()
    stats: dict[str, dict] = {}
    for i in range(len(features)):
        for j in range(i + 1, min(i + 1 + window, len(features))):
            raw = match_pair(features[i], features[j])
            if len(raw) < 8:
                stats[f"{i}-{j}"] = {"raw": int(len(raw)), "verified": 0}
                continue
            p1 = features[i].xy[raw[:, 0]]
            p2 = features[j].xy[raw[:, 1]]
            pose = relative_pose(
                p1, p2,
                _intrinsic_for(intrinsics, i),
                _intrinsic_for(intrinsics, j),
            )
            if pose is None:
                stats[f"{i}-{j}"] = {"raw": int(len(raw)), "verified": 0}
                continue
            verified = raw[pose.inliers]
            graph.pair_matches[(i, j)] = verified
            stats[f"{i}-{j}"] = {
                "raw": int(len(raw)),
                "verified": int(len(verified)),
                "median_epipolar_px": pose.median_epipolar_px,
            }
            for a, b in verified:
                builder.union((i, int(a)), (j, int(b)))
    graph.tracks = builder.tracks(min_length=2)
    return graph, stats


def _projection(rvec: np.ndarray, tvec: np.ndarray, K: np.ndarray) -> np.ndarray:
    R = cv2.Rodrigues(rvec)[0]
    return K @ np.hstack([R, tvec.reshape(3, 1)])


def _triangulate_track(recon: Reconstruction, graph: MatchGraph,
                       track_index: int, min_angle_deg: float) -> np.ndarray | None:
    members = [(f, kp) for f, kp in graph.tracks[track_index] if f in recon.rvecs]
    if len(members) < 2:
        return None
    projections = [
        _projection(recon.rvecs[f], recon.tvecs[f], recon.intrinsic(f))
        for f, _ in members
    ]
    uv = [graph.features[f].xy[kp] for f, kp in members]
    X = triangulate_dlt(projections, uv)
    if not np.all(np.isfinite(X)):
        return None
    for f, _ in members:  # cheirality: must be in front of every view that saw it
        R = cv2.Rodrigues(recon.rvecs[f])[0]
        if (R @ X + recon.tvecs[f])[2] <= 1e-6:
            return None
    centers = np.array([recon.center(f) for f, _ in members])
    if triangulation_angle_deg(centers, X) < min_angle_deg:
        return None
    return X


def incremental_reconstruct(
    graph: MatchGraph,
    focal_px: float,
    cx: float,
    cy: float,
    *,
    frame_intrinsics: Mapping[int, np.ndarray] | None = None,
    min_angle_deg: float = MIN_TRIANGULATION_ANGLE_DEG,
) -> Reconstruction:
    """Seed from the strongest pair, then register the rest by PnP."""
    recon = Reconstruction(
        focal_px=focal_px,
        cx=cx,
        cy=cy,
        frame_intrinsics={
            int(frame): np.asarray(value, dtype=np.float64).copy()
            for frame, value in (frame_intrinsics or {}).items()
        },
    )

    if not graph.pair_matches:
        raise ValueError("no verified pair matches; cannot reconstruct")
    seed = max(graph.pair_matches, key=lambda k: (len(graph.pair_matches[k]), -k[0]))
    i, j = seed
    p1 = graph.features[i].xy[graph.pair_matches[seed][:, 0]]
    p2 = graph.features[j].xy[graph.pair_matches[seed][:, 1]]
    pose = relative_pose(p1, p2, recon.intrinsic(i), recon.intrinsic(j))
    if pose is None:
        raise ValueError(f"seed pair {seed} lost its pose on re-estimation")
    recon.rvecs[i] = np.zeros(3)
    recon.tvecs[i] = np.zeros(3)
    recon.rvecs[j] = cv2.Rodrigues(pose.rotation)[0].ravel()
    recon.tvecs[j] = pose.translation.copy()

    track_of = {node: t for t, members in enumerate(graph.tracks) for node in members}

    def grow_structure() -> int:
        added = 0
        for track_index in range(len(graph.tracks)):
            if track_index in recon.points:
                continue
            X = _triangulate_track(recon, graph, track_index, min_angle_deg)
            if X is not None:
                recon.points[track_index] = X
                added += 1
        return added

    grow_structure()

    remaining = [f for f in range(len(graph.features)) if f not in recon.rvecs]
    while remaining:
        best_frame, best_obs = None, []
        for frame in remaining:
            obs = []
            for kp in range(len(graph.features[frame])):
                t = track_of.get((frame, kp))
                if t is not None and t in recon.points:
                    obs.append((t, kp))
            if len(obs) > len(best_obs):
                best_frame, best_obs = frame, obs
        if best_frame is None or len(best_obs) < 6:
            break
        object_points = np.array([recon.points[t] for t, _ in best_obs], dtype=np.float64)
        image_points = np.array(
            [graph.features[best_frame].xy[kp] for _, kp in best_obs], dtype=np.float64
        )
        frame_K = recon.intrinsic(best_frame)
        ok, rvec, tvec, _ = cv2.solvePnPRansac(
            object_points, image_points, frame_K, None,
            reprojectionError=PNP_REPROJECTION_PX, confidence=0.9999,
            iterationsCount=2000, flags=cv2.SOLVEPNP_EPNP,
        )
        if not ok:
            remaining.remove(best_frame)
            continue
        rvec, tvec = cv2.solvePnPRefineLM(
            object_points, image_points, frame_K, None, rvec, tvec
        )
        recon.rvecs[best_frame] = np.asarray(rvec, dtype=np.float64).ravel()
        recon.tvecs[best_frame] = np.asarray(tvec, dtype=np.float64).ravel()
        remaining.remove(best_frame)
        grow_structure()

    return recon


def reprojection_errors(recon: Reconstruction, graph: MatchGraph) -> np.ndarray:
    obs = recon.observations(graph)
    if not obs:
        return np.zeros(0)
    errs = []
    for frame, track_index, uv in obs:
        R = cv2.Rodrigues(recon.rvecs[frame])[0]
        cam = R @ recon.points[track_index] + recon.tvecs[frame]
        if cam[2] <= 1e-6:
            errs.append(np.inf)
            continue
        proj = recon.intrinsic(frame) @ cam
        errs.append(float(np.hypot(proj[0] / proj[2] - uv[0], proj[1] / proj[2] - uv[1])))
    return np.asarray(errs)


def filter_outlier_points(recon: Reconstruction, graph: MatchGraph,
                          max_px: float = MAX_RECONSTRUCTION_REPROJECTION_PX) -> int:
    """Drop points whose worst view disagrees; a bad track corrupts the whole solve."""
    dropped = 0
    for track_index in list(recon.points):
        worst = 0.0
        for frame, kp in graph.tracks[track_index]:
            if frame not in recon.rvecs:
                continue
            R = cv2.Rodrigues(recon.rvecs[frame])[0]
            cam = R @ recon.points[track_index] + recon.tvecs[frame]
            if cam[2] <= 1e-6:
                worst = np.inf
                break
            proj = recon.intrinsic(frame) @ cam
            uv = graph.features[frame].xy[kp]
            worst = max(worst, float(np.hypot(proj[0] / proj[2] - uv[0], proj[1] / proj[2] - uv[1])))
        if worst > max_px:
            del recon.points[track_index]
            dropped += 1
    return dropped


def bundle_adjust(recon: Reconstruction, graph: MatchGraph, *, max_iterations: int = 60) -> dict:
    """Relative bundle adjustment: camera 0 fixed, scale pinned by a soft baseline."""
    frames = recon.registered
    anchor = frames[0]
    free_frames = [f for f in frames if f != anchor]
    tracks = sorted(recon.points)
    track_pos = {t: k for k, t in enumerate(tracks)}
    frame_pos = {f: k for k, f in enumerate(free_frames)}

    obs = [(f, t, uv) for f, t, uv in recon.observations(graph)]
    if not obs:
        raise ValueError("bundle adjustment has no observations")
    scale_frame = free_frames[0]

    n_cam, n_pts = len(free_frames), len(tracks)
    x0 = np.concatenate(
        [np.concatenate([np.concatenate([recon.rvecs[f], recon.tvecs[f]]) for f in free_frames])
         if free_frames else np.zeros(0),
         np.concatenate([recon.points[t] for t in tracks])]
    )
    anchor_r, anchor_t = recon.rvecs[anchor], recon.tvecs[anchor]

    obs_frame = np.array([frame_pos.get(f, -1) for f, _, _ in obs])
    obs_track = np.array([track_pos[t] for _, t, _ in obs])
    obs_uv = np.array([uv for _, _, uv in obs])
    obs_focal = np.array([recon.intrinsic(f)[0, 0] for f, _, _ in obs])
    obs_cx = np.array([recon.intrinsic(f)[0, 2] for f, _, _ in obs])
    obs_cy = np.array([recon.intrinsic(f)[1, 2] for f, _, _ in obs])

    def unpack(x):
        cams = x[: 6 * n_cam].reshape(n_cam, 6)
        pts = x[6 * n_cam:].reshape(n_pts, 3)
        return cams, pts

    def residuals(x):
        cams, pts = unpack(x)
        out = np.empty(2 * len(obs) + 1)
        # Stack the anchor pose at index n_cam so the anchor's observations are
        # gathered with everything else instead of branching per observation.
        allR = np.stack(
            [cv2.Rodrigues(cams[i, :3])[0] for i in range(n_cam)]
            + [cv2.Rodrigues(anchor_r)[0]]
        )
        allT = np.vstack([cams[:, 3:], anchor_t.reshape(1, 3)])
        sel = np.where(obs_frame >= 0, obs_frame, n_cam)
        cam_xyz = np.einsum("nij,nj->ni", allR[sel], pts[obs_track]) + allT[sel]
        z = np.where(np.abs(cam_xyz[:, 2]) > 1e-9, cam_xyz[:, 2], 1e-9)
        out[0: 2 * len(obs): 2] = obs_focal * cam_xyz[:, 0] / z + obs_cx - obs_uv[:, 0]
        out[1: 2 * len(obs): 2] = obs_focal * cam_xyz[:, 1] / z + obs_cy - obs_uv[:, 1]
        rv, tv = cams[frame_pos[scale_frame], :3], cams[frame_pos[scale_frame], 3:]
        center = -cv2.Rodrigues(rv)[0].T @ tv
        out[-1] = BASELINE_GAUGE_WEIGHT * (np.linalg.norm(center) - 1.0)
        return out

    sparsity = lil_matrix((2 * len(obs) + 1, x0.size), dtype=int)
    for k in range(len(obs)):
        fi, ti = obs_frame[k], obs_track[k]
        if fi >= 0:
            sparsity[2 * k: 2 * k + 2, 6 * fi: 6 * fi + 6] = 1
        sparsity[2 * k: 2 * k + 2, 6 * n_cam + 3 * ti: 6 * n_cam + 3 * ti + 3] = 1
    fs = frame_pos[scale_frame]
    sparsity[-1, 6 * fs: 6 * fs + 6] = 1

    before = float(np.sqrt(np.mean(residuals(x0)[:-1] ** 2)))
    result = least_squares(
        residuals, x0, jac_sparsity=sparsity.tocsr(), method="trf",
        loss="huber", f_scale=2.0, x_scale="jac", max_nfev=max_iterations, verbose=0,
    )
    cams, pts = unpack(result.x)
    for f in free_frames:
        recon.rvecs[f] = cams[frame_pos[f], :3].copy()
        recon.tvecs[f] = cams[frame_pos[f], 3:].copy()
    for t in tracks:
        recon.points[t] = pts[track_pos[t]].copy()
    after = float(np.sqrt(np.mean(result.fun[:-1] ** 2)))
    return {
        "observations": len(obs),
        "cameras_optimized": n_cam,
        "points_optimized": n_pts,
        "rms_reprojection_px_before": before,
        "rms_reprojection_px_after": after,
        "iterations": int(result.nfev),
        "status": int(result.status),
    }
