"""Absolute georegistration: one global weighted solve (FL-4812).

THE WEIGHTING IS THE POINT
==========================
Four sources disagree about where things are, and none of them is allowed to
win by default:

  visual reprojection   sigma ~= 1 px    dense, precise, but scale-free and
                                         drift-prone over a long corridor
  capture GPS           sigma = the source's own ``quality:horizontal_accuracy``
                                         (5 m here), vertical 3x worse
  compass azimuth       sigma = 15 deg   quantised to whole degrees by the
                                         source, disturbed by steel and wires
  OSM building anchor   sigma = 1.5 m    traced from aerial imagery, not surveyed
  World IR OSM anchor   sigma = the anchor set's own stated tolerance (0.667 m
                                         at Numabe); 2-D for a corner, doorway
                                         or junction node, 1-D across the curve
                                         for a rail vertex
  reviewed control pt   sigma = quadrature of the reviewed pixel sigma and the
                                         named anchor's map sigma, as an angle
  focal length prior    sigma = the intrinsics prior's own sigma

Every one of them enters the same normal equations as ``residual / sigma``, so
the relative influence of each source is a number that can be read off the
receipt, not a hidden precedence rule.  Specifically: OSM never sets XYZ.  It
pulls, against GPS pulling the other way and against reprojection resisting
both, and the residual receipt says by how much each ended up moving.  A
REVIEWED control point is in the same position: it is weighted evidence that
can lose, never a correction applied outside the solve, and
``constraint_residual_report`` prints its residual beside all the others.

GAUGE AND COVARIANCE
====================
The seven similarity degrees of freedom that a visual reconstruction cannot
observe are fixed here by the GPS and azimuth priors rather than by pinning a
camera, so the system is full rank and ``(J^T J)^-1`` is a real covariance.
Point parameters are marginalized by Schur complement, exactly, so the camera
and anchor covariances reported are marginals of the full solve and not a
conditional block pretending to be one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from .capture import AZIMUTH_SIGMA_DEG, ROLL_SIGMA_DEG, Capture
from .geodesy import angle_delta_deg, bearing_deg
from .osm_anchors import (
    OSM_ANCHOR_SIGMA_M,
    WallMatch,
    WorldIRAnchor,
    signed_wall_offset,
)
from .sfm import Reconstruction

REPROJECTION_SIGMA_PX = 1.0

# Robust scale, in sigmas, for the control-point datum search.  The same value
# the global solve uses for its own Huber loss, so a correspondence is not
# "an outlier" to one stage and "a measurement" to the next.
CONTROL_ALIGNMENT_HUBER_SIGMAS = 3.0

# Street-image residuals come from subpixel feature localization on compressed
# derivatives. Requiring 1e-8 relative parameter/cost changes asks the optimizer
# to resolve far below the measurement precision and caused valid route blocks
# to run until the evaluation cap. These tolerances remain substantially below
# the 1 px observation sigma and are emitted in every receipt.
GLOBAL_SOLVE_FTOL = 1e-6
GLOBAL_SOLVE_XTOL = 1e-6
GLOBAL_SOLVE_GTOL = 1e-6


def umeyama_similarity(source: np.ndarray, target: np.ndarray,
                       weights: np.ndarray | None = None) -> tuple[float, np.ndarray, np.ndarray]:
    """Weighted least-squares similarity (scale, rotation, translation) source->target."""
    if weights is None:
        weights = np.ones(len(source))
    w = weights / weights.sum()
    mu_s = (w[:, None] * source).sum(axis=0)
    mu_t = (w[:, None] * target).sum(axis=0)
    ds, dt = source - mu_s, target - mu_t
    cov = (w[:, None] * dt).T @ ds
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1.0
    R = U @ S @ Vt
    var_s = float((w * (ds ** 2).sum(axis=1)).sum())
    scale = float((D * np.diag(S)).sum() / var_s) if var_s > 1e-12 else 1.0
    t = mu_t - scale * R @ mu_s
    return scale, R, t


@dataclass
class GeoSolution:
    """The georegistered solve: poses in ENU metres, plus its own covariance."""

    frames: list[int]
    rvecs: dict[int, np.ndarray]
    centers: dict[int, np.ndarray]
    points: dict[int, np.ndarray]
    focal_px: float
    cx: float
    cy: float
    focal_free: bool
    frame_intrinsics: dict[int, np.ndarray] = field(default_factory=dict)
    center_covariance: dict[int, np.ndarray] = field(default_factory=dict)
    rotation_covariance: dict[int, np.ndarray] = field(default_factory=dict)
    point_covariance: dict[int, np.ndarray] = field(default_factory=dict)
    focal_sigma_px: float = float("nan")
    variance_factor: float = float("nan")
    report: dict = field(default_factory=dict)
    # Full marginal covariance of [cameras..., focal?, kept points...] together
    # with the row each block starts at.  Kept because the CROSS terms between a
    # point and the cameras are what separate "this point is uncertain" from
    # "the whole block's datum is uncertain", and only one of those two is a
    # defect in the reconstruction.
    marginal_block: np.ndarray | None = None
    marginal_offsets: dict = field(default_factory=dict)

    def rotation(self, frame: int) -> np.ndarray:
        return cv2.Rodrigues(self.rvecs[frame])[0]

    def intrinsic(self, frame: int) -> np.ndarray:
        """Calibration for ``frame``, with legacy scalar fallback."""
        if self.focal_free:
            # The legacy open-capture control corpus intentionally solves one
            # shared focal.  Its refined scalar must remain live here.
            return np.array([
                [self.focal_px, 0.0, self.cx],
                [0.0, self.focal_px, self.cy],
                [0.0, 0.0, 1.0],
            ])
        return self.frame_intrinsics.get(
            frame,
            np.array([
                [self.focal_px, 0.0, self.cx],
                [0.0, self.focal_px, self.cy],
                [0.0, 0.0, 1.0],
            ]),
        )

    def projection_parameters(self, frame: int) -> tuple[float, float, float]:
        K = self.intrinsic(frame)
        return float(K[0, 0]), float(K[0, 2]), float(K[1, 2])

    def project(self, frame: int, point: np.ndarray) -> tuple[np.ndarray, float]:
        """Pixel position and depth of a world point in one frame."""
        cam = self.rotation(frame) @ (point - self.centers[frame])
        z = float(cam[2])
        if z <= 1e-9:
            return np.array([np.nan, np.nan]), z
        K = self.intrinsic(frame)
        projected = K @ cam
        return projected[:2] / projected[2], z

    def azimuth_deg(self, frame: int) -> float:
        axis = self.rotation(frame).T @ np.array([0.0, 0.0, 1.0])
        return bearing_deg(axis[0], axis[1])


def _level_roll(rvecs: dict[int, np.ndarray], centers: dict[int, np.ndarray],
                points: dict[int, np.ndarray], frames: list[int]) -> tuple[float, np.ndarray]:
    """Rotate the whole block about its own track axis until the cameras are level.

    Umeyama alignment onto a NEARLY COLLINEAR camera track leaves exactly this
    one rotation free, so the similarity alignment can - and on real data does -
    hand the global solve a block rolled by tens of degrees.  Searching the one
    free angle for the least total roll puts the block the right way up before
    the roll prior ever has to pull it there, which is the difference between a
    prior that refines a solution and a prior that has to rescue one.
    """
    track = np.array([centers[f] for f in frames])
    direction = track[-1] - track[0]
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return 0.0, np.eye(3)
    axis = direction / norm
    pivot = track.mean(axis=0)

    def rotation_about(theta: float) -> np.ndarray:
        K = np.array([[0.0, -axis[2], axis[1]],
                      [axis[2], 0.0, -axis[0]],
                      [-axis[1], axis[0], 0.0]])
        return np.eye(3) + np.sin(theta) * K + (1.0 - np.cos(theta)) * (K @ K)

    def evaluate(theta: float) -> tuple[float, float]:
        """Return (sum of squared roll, mean world-z of the cameras' image-up)."""
        M = rotation_about(theta)
        roll_cost = 0.0
        upness = 0.0
        for f in frames:
            R = cv2.Rodrigues(rvecs[f])[0] @ M.T
            roll_cost += float(R.T[2, 0]) ** 2
            # Image "up" is -y in camera axes; its world z must be positive.
            upness += float(-R.T[2, 1])
        return roll_cost, upness / len(frames)

    # Levelling the roll has TWO solutions 180 deg apart: one upright, one with
    # every camera upside down.  Both have the image-right axis horizontal, and
    # neither the compass nor the GPS can tell them apart, because rotating the
    # block about a track axis that the cameras look along changes no bearing
    # and moves no camera centre.  The first run of this function picked the
    # flipped branch and put the whole reconstruction below the road.  So the
    # search is restricted to angles whose cameras are the right way up.
    grid = np.linspace(0.0, 2.0 * np.pi, 721, endpoint=False)
    scored = [(theta,) + evaluate(theta) for theta in grid]
    upright = [s for s in scored if s[2] > 0.0]
    if not upright:
        upright = scored
    best = min(upright, key=lambda s: s[1])[0]
    fine = [(theta,) + evaluate(theta) for theta in np.linspace(best - 0.02, best + 0.02, 81)]
    refined = min([s for s in fine if s[2] > 0.0] or fine, key=lambda s: s[1])[0]
    M = rotation_about(refined)
    for f in frames:
        rvecs[f] = cv2.Rodrigues(cv2.Rodrigues(rvecs[f])[0] @ M.T)[0].ravel()
        centers[f] = M @ (centers[f] - pivot) + pivot
    for t in list(points):
        points[t] = M @ (points[t] - pivot) + pivot
    return float(np.degrees(refined)), M


def _resolve_track_reversal(rvecs: dict[int, np.ndarray], centers: dict[int, np.ndarray],
                            points: dict[int, np.ndarray], frames: list[int],
                            captures: dict[int, "Capture"]) -> dict:
    """Pick between the two yaw branches a straight camera track cannot separate.

    WHY THIS EXISTS
    ===============
    ``umeyama_similarity`` fits the reconstruction onto the GPS track. When the
    walk is nearly straight, rotating the whole block 180 degrees about the
    vertical axis through the track centroid maps that track almost exactly onto
    itself: every camera centre lands on the track again, so the GPS residual
    barely moves. Orientation, however, flips completely -- every camera bearing
    changes by 180 degrees. GPS therefore cannot tell "walking north taking
    photos east" from "walking south taking photos west", and Umeyama picks
    whichever branch the visual reconstruction happened to be built in.

    This is the same degeneracy ``_level_roll`` already handles for roll, where
    the two solutions 180 degrees apart are upright and upside-down. Yaw has the
    identical two-branch problem and was not being resolved.

    WHY IT CANNOT BE LEFT TO THE GLOBAL SOLVE
    =========================================
    The global solve runs ``least_squares(..., loss="huber", f_scale=3.0)`` and
    the compass prior carries AZIMUTH_SIGMA_DEG = 15. On the wrong branch the
    compass disagrees by roughly 147 degrees, which is 9.8 sigma, and Huber
    treats anything past 3 sigma as an outlier to be down-weighted. Measured on
    the Numabe island-A block, 99.6 of the total optimizer cost of 126.8 was
    exactly this down-weighted compass term: the prior was shouting the correct
    answer and the robust loss was ignoring it, while rotating through 147
    degrees would first have to climb through far worse reprojection error. The
    solve therefore sat on the wrong branch with an excellent reprojection error
    and a 147 degree heading error -- a low residual certifying a wrong answer,
    the same failure class as the roll degeneracy that once passed at 0.44 px.

    Choosing the branch HERE, before the robust loss ever sees it, leaves a
    residual of roughly 33 degrees, which is 2.2 sigma and inside Huber's
    quadratic regime, so the compass prior can then do the refining it was
    always meant to do.

    The compass is the only evidence that separates these branches, so when no
    capture carries an azimuth this function does nothing and says so.
    """
    az_frames = [f for f in frames if captures[f].azimuth_deg is not None]
    if not az_frames:
        return {"applied": False, "reason": "no capture carries a compass azimuth"}

    track = np.array([centers[f] for f in frames])
    pivot = track.mean(axis=0)
    flip = np.array([[-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]])

    def azimuth_cost(rv: dict[int, np.ndarray]) -> float:
        errs = []
        for f in az_frames:
            axis = cv2.Rodrigues(rv[f])[0].T @ np.array([0.0, 0.0, 1.0])
            errs.append(abs(angle_delta_deg(bearing_deg(axis[0], axis[1]),
                                            captures[f].azimuth_deg)))
        return float(np.median(errs))

    def gps_cost(ct: dict[int, np.ndarray]) -> float:
        return float(np.median([np.linalg.norm(ct[f] - captures[f].enu) for f in frames]))

    kept_az, kept_gps = azimuth_cost(rvecs), gps_cost(centers)

    flipped_rvecs = {f: cv2.Rodrigues(cv2.Rodrigues(rvecs[f])[0] @ flip.T)[0].ravel()
                     for f in frames}
    flipped_centers = {f: flip @ (centers[f] - pivot) + pivot for f in frames}
    flip_az, flip_gps = azimuth_cost(flipped_rvecs), gps_cost(flipped_centers)

    report = {
        "applied": False,
        "kept_branch_azimuth_median_deg": kept_az,
        "flipped_branch_azimuth_median_deg": flip_az,
        "kept_branch_gps_median_m": kept_gps,
        "flipped_branch_gps_median_m": flip_gps,
    }
    # Only flip when the compass clearly prefers it AND the GPS track does not
    # object: a genuinely curved track is not reversal-degenerate, and there the
    # flip would wreck the position fit, so the GPS guard is what keeps this
    # from firing on data that does not have the ambiguity.
    if flip_az < kept_az - 1.0 and flip_gps <= max(kept_gps * 1.5, kept_gps + 1.0):
        for f in frames:
            rvecs[f] = flipped_rvecs[f]
            centers[f] = flipped_centers[f]
        for t in list(points):
            points[t] = flip @ (points[t] - pivot) + pivot
        report["applied"] = True
    return report


def initial_alignment(recon: Reconstruction, captures: dict[int, Capture]) -> tuple[GeoSolution, dict]:
    """Similarity-align the scale-free reconstruction onto the capture GPS track.

    This is only the INITIALISATION for the global solve.  It is a separate,
    named step because it is the moment metres enter the pipeline, and because
    its residual is itself diagnostic: a large similarity residual means the
    visual track shape and the GPS track shape disagree, which no amount of
    later refinement repairs honestly.
    """
    frames = recon.registered
    src = np.array([recon.center(f) for f in frames])
    dst = np.array([captures[f].enu for f in frames])
    weights = np.array([1.0 / max(captures[f].horizontal_accuracy_m, 0.1) ** 2 for f in frames])
    scale, R_s, t_s = umeyama_similarity(src, dst, weights)

    centers = {f: scale * R_s @ recon.center(f) + t_s for f in frames}
    rvecs = {}
    for f in frames:
        R_rel = cv2.Rodrigues(recon.rvecs[f])[0]
        rvecs[f] = cv2.Rodrigues(R_rel @ R_s.T)[0].ravel()
    points = {t: scale * R_s @ X + t_s for t, X in recon.points.items()}
    roll_correction_deg, _ = _level_roll(rvecs, centers, points, frames)
    track_reversal = _resolve_track_reversal(rvecs, centers, points, frames, captures)

    solution = GeoSolution(
        frames=frames, rvecs=rvecs, centers=centers, points=points,
        focal_px=recon.focal_px, cx=recon.cx, cy=recon.cy,
        focal_free=captures[frames[0]].intrinsics.is_free_parameter,
        frame_intrinsics={
            frame: recon.intrinsic(frame).copy() for frame in frames
        },
    )
    residuals = np.array([np.linalg.norm(centers[f] - captures[f].enu) for f in frames])
    az_res = [
        angle_delta_deg(solution.azimuth_deg(f), captures[f].azimuth_deg)
        for f in frames if captures[f].azimuth_deg is not None
    ]
    rolls = [
        float(np.degrees(np.arcsin(np.clip(cv2.Rodrigues(rvecs[f])[0].T[2, 0], -1.0, 1.0))))
        for f in frames
    ]
    report = {
        "similarity_scale_recon_units_per_metre": scale,
        "track_axis_roll_correction_deg": roll_correction_deg,
        "track_reversal_branch": track_reversal,
        "residual_camera_roll_deg": {
            "median_abs": float(np.median(np.abs(rolls))),
            "max_abs": float(np.max(np.abs(rolls))),
        },
        "gps_residual_m": {
            "median": float(np.median(residuals)),
            "max": float(residuals.max()),
            "rms": float(np.sqrt(np.mean(residuals ** 2))),
        },
        "azimuth_residual_deg": {
            "median_abs": float(np.median(np.abs(az_res))) if az_res else None,
            "max_abs": float(np.max(np.abs(az_res))) if az_res else None,
        },
    }
    return solution, report


# ==========================================================================
# constraint vocabulary
# ==========================================================================
# Three anchor SHAPES enter the same normal equations.  They are separate types
# rather than one type with a mode flag because they are not the same claim:
#
#   AnchorConstraint            1 row   a wall PLANE.  Only the across-wall
#                                       component is observable; a point may
#                                       slide freely along the facade.
#   WorldIRAnchorConstraint     1 or 2  a World IR anchor.  2 rows when the map
#                                       asserts a POSITION (a corner, a doorway,
#                                       a junction node), 1 row when it asserts a
#                                       CURVE (a rail vertex) and the point is
#                                       free to slide along it.
#   ControlPointConstraint      1 row   a REVIEWED image-pixel-to-anchor
#                                       correspondence, as a bearing.
#
# Every one of them exposes ``residual_rows`` and a residual already divided by
# its own stated sigma, so ``global_solve`` assembles them without knowing what
# any of them means.


@dataclass
class AnchorConstraint:
    """One OSM wall the solve is pulled toward, through one reconstructed point."""

    track_index: int
    wall: WallMatch
    sigma_m: float = OSM_ANCHOR_SIGMA_M

    residual_rows: int = field(default=1, init=False, repr=False)

    def residual_en(self, point_en: np.ndarray) -> np.ndarray:
        return np.array([signed_wall_offset(point_en, self.wall) / self.sigma_m])

    def miss_m(self, point_en: np.ndarray) -> float:
        return abs(float(signed_wall_offset(point_en, self.wall)))

    def as_record(self) -> dict:
        return {
            "kind": "osm_building_wall",
            "geometry": "plane",
            "track_index": int(self.track_index),
            "osm_ref": self.wall.osm_ref,
            "sigma_m": self.sigma_m,
            "residual_rows": 1,
        }


@dataclass
class WorldIRAnchorConstraint:
    """One World IR OSM anchor the solve is pulled toward, through one point.

    The anchor states its own geometry and its own sigma; this type does not
    second-guess either.  A point anchor therefore contributes two rows and a
    point-on-curve anchor one, and the receipt can say which without inferring
    it from a count.
    """

    track_index: int
    anchor: WorldIRAnchor
    sigma_m: float = float("nan")
    # Association evidence, carried so a formed constraint can be audited back
    # to how it was formed rather than only to what it did.
    association_distance_m: float = float("nan")
    ambiguity_ratio: float = float("nan")

    def __post_init__(self) -> None:
        if not np.isfinite(self.sigma_m):
            self.sigma_m = self.anchor.sigma_m

    @property
    def residual_rows(self) -> int:
        return self.anchor.residual_rows

    def residual_en(self, point_en: np.ndarray) -> np.ndarray:
        return self.anchor.offset(point_en) / self.sigma_m

    def miss_m(self, point_en: np.ndarray) -> float:
        return self.anchor.miss_m(point_en)

    def as_record(self) -> dict:
        return {
            "kind": self.anchor.anchor_type,
            "geometry": self.anchor.geometry_kind,
            "track_index": int(self.track_index),
            "anchor_id": self.anchor.anchor_id,
            "osm_ref": self.anchor.osm_ref,
            "sigma_m": self.sigma_m,
            "residual_rows": self.residual_rows,
            "association_distance_m": self.association_distance_m,
            "ambiguity_ratio": self.ambiguity_ratio,
        }


def control_point_sigma_deg(pixel_sigma_px: float, focal_px: float,
                            anchor_sigma_m: float, range_m: float) -> float:
    """Angular sigma of one reviewed correspondence, from its two real errors.

    A reviewed pixel is uncertain by however well the reviewer could see the
    feature (``pixel_sigma_px``, which the annotation states per point, not a
    constant).  The anchor it names is uncertain by the map's own digitising
    tolerance.  In a BEARING both appear as angles, and they are independent,
    so they combine in quadrature.  The map term shrinks with range, which is
    why a distant anchor is a better bearing reference than a near one - a fact
    this formula makes visible instead of burying in one hand-set number.
    """
    pixel_rad = float(pixel_sigma_px) / max(float(focal_px), 1e-9)
    map_rad = float(anchor_sigma_m) / max(float(range_m), 1e-6)
    return float(np.degrees(np.hypot(pixel_rad, map_rad)))


@dataclass
class ControlPointConstraint:
    """One REVIEWED image-pixel-to-anchor correspondence, as a weighted bearing.

    WHY A BEARING AND NOT A REPROJECTION
    ====================================
    An OSM anchor node is a GROUND-PLANE position.  The map states no height for
    it, and a reviewer clicking a building corner clicks somewhere along a
    vertical edge, at a height nobody measured.  A reprojection residual would
    therefore need a height, and the only way to get one is to invent it - which
    would put a fabricated number inside the solve wearing a measured number's
    weight.

    The horizontal bearing from the camera to the feature is height-free and is
    observable from exactly what both sides actually state: the reviewed pixel
    and the anchor's east/north.  It is also the quantity that pins ABSOLUTE
    YAW, which is what an unregistered block is missing.

    THIS IS EVIDENCE, NOT AN OVERRIDE
    =================================
    It enters the same normal equations as the GPS, compass, OSM and
    reprojection residuals, divided by its own stated sigma, and it can lose to
    them.  Its residual appears in the receipt beside theirs.  A reviewed
    correspondence that disagrees with the imagery shows up as a large residual;
    it does not silently move the answer.
    """

    control_id: str
    frame: int
    uv: np.ndarray
    anchor: WorldIRAnchor
    sigma_deg: float
    pixel_sigma_px: float = float("nan")
    range_m_at_setup: float = float("nan")
    reviewer: str = ""
    reviewed_at: str = ""
    review_method: str = ""
    note: str = ""

    residual_rows: int = field(default=1, init=False, repr=False)

    def observed_bearing_deg(self, rotation: np.ndarray, focal_px: float,
                             cx: float, cy: float) -> float:
        """Compass bearing of the reviewed pixel's ray, in world axes."""
        ray_cam = np.array([(float(self.uv[0]) - cx) / focal_px,
                            (float(self.uv[1]) - cy) / focal_px, 1.0])
        ray_world = rotation.T @ ray_cam
        return bearing_deg(float(ray_world[0]), float(ray_world[1]))

    def map_bearing_deg(self, center: np.ndarray) -> float:
        """Compass bearing from the camera centre to the anchor the map states."""
        delta = self.anchor.en - np.asarray(center, dtype=np.float64)[:2]
        return bearing_deg(float(delta[0]), float(delta[1]))

    def residual_deg(self, rotation: np.ndarray, center: np.ndarray,
                     focal_px: float, cx: float, cy: float) -> float:
        return angle_delta_deg(
            self.observed_bearing_deg(rotation, focal_px, cx, cy),
            self.map_bearing_deg(center),
        )

    def as_record(self) -> dict:
        return {
            "kind": "reviewed_control_point",
            "geometry": "horizontal_bearing",
            "control_id": self.control_id,
            "frame": int(self.frame),
            "pixel": [float(self.uv[0]), float(self.uv[1])],
            "anchor_id": self.anchor.anchor_id,
            "anchor_type": self.anchor.anchor_type,
            "osm_ref": self.anchor.osm_ref,
            "sigma_deg": self.sigma_deg,
            "pixel_sigma_px": self.pixel_sigma_px,
            "anchor_sigma_m": self.anchor.sigma_m,
            "range_m_at_setup": self.range_m_at_setup,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "review_method": self.review_method,
            "note": self.note,
            "residual_rows": 1,
        }


def initial_control_alignment(solution: GeoSolution,
                              control_points: list[ControlPointConstraint],
                              *, grid_steps: int = 721) -> tuple[GeoSolution, dict]:
    """Put the block the right way ROUND before the global solve, from controls.

    WHY AN INITIALISER AND NOT JUST MORE ITERATIONS
    ===============================================
    A block whose absolute heading is wrong by tens of degrees is wrong along a
    direction the visual residuals cannot see at all: every reprojection stays
    perfect while the whole reconstruction spins.  In the normal equations that
    is a near-null direction, so a trust-region step through it is tiny and a
    solve started 22 deg out is still 21.8 deg out after six thousand function
    evaluations.  Measured, not assumed: that is what this solve does without
    this step.

    So the same thing is done here that ``initial_alignment`` does for scale and
    ``_level_roll`` does for roll - the one badly-conditioned degree of freedom
    is found by direct search before the refinement starts.  This is ONE rigid
    horizontal motion of the ENTIRE block, cameras and structure together.  It
    is not a per-camera snap: no camera moves relative to any other, no point
    moves relative to any camera, and every relative geometry the reconstruction
    earned survives it exactly.  The global solve then owns the answer, with the
    control points still in it as weighted residuals that can lose.

    The search is a grid over the rotation because the bearing residual wraps at
    +/-180 deg and has local minima, then one bounded refinement inside the cell
    the grid picked, so the refinement cannot walk out of the basin the robust
    grid chose.
    """
    empty = {"applied": False, "reason": "no control points were supplied"}
    if not control_points:
        return solution, empty

    pivot = np.mean([solution.centers[f] for f in solution.frames], axis=0)
    rvecs0 = {f: solution.rvecs[f].copy() for f in solution.frames}
    centers0 = {f: solution.centers[f].copy() for f in solution.frames}
    points0 = {t: X.copy() for t, X in solution.points.items()}

    # ROTATION ONLY, and deliberately so.  Position is already set to within the
    # capture GPS sigma and is refined inside the global solve against it; the
    # rotation is the one thing nothing else in the pipeline can observe.  Giving
    # this search a free translation as well was measured to make it WORSE: a
    # few metres of shift is a cheap way to satisfy one mis-clicked bearing, and
    # a single bad correspondence in thirty then pulled the datum from -22.0 deg
    # to -7.4 deg.  A search with fewer handles is a search an outlier has less
    # to grab.
    def transform(theta_deg: float) -> GeoSolution:
        c, s_ = np.cos(np.radians(theta_deg)), np.sin(np.radians(theta_deg))
        M = np.array([[c, -s_, 0.0], [s_, c, 0.0], [0.0, 0.0, 1.0]])
        return GeoSolution(
            frames=list(solution.frames),
            rvecs={f: cv2.Rodrigues(cv2.Rodrigues(rvecs0[f])[0] @ M.T)[0].ravel()
                   for f in solution.frames},
            centers={f: M @ (centers0[f] - pivot) + pivot for f in solution.frames},
            points={t: M @ (X - pivot) + pivot for t, X in points0.items()},
            focal_px=solution.focal_px, cx=solution.cx, cy=solution.cy,
            focal_free=solution.focal_free,
            frame_intrinsics={
                frame: value.copy()
                for frame, value in solution.frame_intrinsics.items()
            },
        )

    def residuals(state: GeoSolution) -> np.ndarray:
        return np.array([
            control.residual_deg(state.rotation(control.frame),
                                 state.centers[control.frame],
                                 *state.projection_parameters(control.frame)) / control.sigma_deg
            for control in control_points
        ])

    # A reviewed set is allowed to contain a mis-click, and a datum search that
    # one mis-click can capture is not fit to set a datum.  Measured: with 30
    # correspondences, ONE pixel moved 600 px pulled a plain least-squares
    # version of this search from -22.0 deg to -7.4 deg, because a free
    # translation bought the outlier cheaply.  So both the grid and the
    # refinement use the same Huber scale the global solve uses, and the outlier
    # then loses to the twenty-nine that agree - and is still reported.
    def cost(theta_deg: float) -> float:
        r = residuals(transform(theta_deg)) / CONTROL_ALIGNMENT_HUBER_SIGMAS
        return float(np.sum(np.where(np.abs(r) <= 1.0, r ** 2, 2.0 * np.abs(r) - 1.0)))

    grid = np.linspace(-180.0, 180.0, grid_steps, endpoint=False)
    best_theta = float(min(grid, key=cost))
    step = 360.0 / grid_steps
    result = least_squares(
        lambda p: residuals(transform(float(p[0]))), np.array([best_theta]),
        method="trf", loss="huber", f_scale=CONTROL_ALIGNMENT_HUBER_SIGMAS,
        bounds=([best_theta - step], [best_theta + step]), max_nfev=200,
    )
    theta = float(result.x[0])
    before = np.array([
        control.residual_deg(solution.rotation(control.frame),
                             solution.centers[control.frame],
                             *solution.projection_parameters(control.frame))
        for control in control_points
    ])
    moved = transform(theta)
    after = np.array([
        control.residual_deg(moved.rotation(control.frame),
                             moved.centers[control.frame],
                             *moved.projection_parameters(control.frame))
        for control in control_points
    ])

    def stats(values: np.ndarray) -> dict:
        return {
            "median_abs": float(np.median(np.abs(values))),
            "max_abs": float(np.max(np.abs(values))),
            "rms": float(np.sqrt(np.mean(values ** 2))),
            "mean_signed": float(np.mean(values)),
        }

    report = {
        "applied": True,
        "control_points_used": len(control_points),
        "block_yaw_rotation_deg": theta,
        "block_translation_m": [0.0, 0.0],
        "block_translation_note": (
            "none: only the rotation is searched here; position is owned by the "
            "capture GPS prior inside the global solve"
        ),
        "pivot_enu_m": [float(v) for v in pivot],
        "grid_seed_deg": best_theta,
        "grid_steps": grid_steps,
        "bearing_residual_deg_before": stats(before),
        "bearing_residual_deg_after": stats(after),
        "rigidity_note": (
            "ONE rotation and translation applied to every camera and every point "
            "together; relative pose and structure are unchanged, so this cannot "
            "improve any reprojection residual and cannot hide a reconstruction "
            "defect"
        ),
    }
    return moved, report


def absolute_block_solve(
    solution: GeoSolution,
    captures: dict[int, Capture],
    observations: list[tuple[int, int, np.ndarray]],
    anchors: list,
    *,
    control_points: list[ControlPointConstraint] = (),
    keep_track_covariance: tuple[int, ...] = (),
    max_iterations: int = 500,
) -> GeoSolution:
    """Register a converged visual block with one rigid yaw + XYZ translation.

    The local bundle adjustment has already solved camera-to-camera and
    camera-to-point geometry. Reviewed image-to-map controls or a verified
    automatic OSM anchor establish the remaining absolute datum; neither
    justifies reopening every camera and point as independent parameters. This
    solve therefore moves the complete block with four shared parameters. A
    rigid motion cannot improve visual reprojection, and the invariant check
    below makes that a tested property of the result rather than a claim in
    this docstring.
    """
    frames = list(solution.frames)
    control_points = list(control_points)
    if not control_points and not anchors:
        raise ValueError("absolute_block_solve requires a reviewed control or OSM anchor")
    frame_set = set(frames)
    for control in control_points:
        if control.frame not in frame_set:
            raise ValueError(
                f"control point {control.control_id} names frame {control.frame}, "
                "which this solve did not register"
            )
    for constraint in anchors:
        if constraint.track_index not in solution.points:
            raise ValueError(
                f"anchor names track {constraint.track_index}, which this solve did not retain"
            )

    pivot = np.mean([solution.centers[f] for f in frames], axis=0)
    az_frames = [f for f in frames if captures[f].azimuth_deg is not None]
    anchor_rows = sum(c.residual_rows for c in anchors)

    def transformed(params: np.ndarray) -> tuple[GeoSolution, np.ndarray]:
        theta = np.radians(float(params[0]))
        c, s = np.cos(theta), np.sin(theta)
        motion = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        translation = np.asarray(params[1:4], dtype=np.float64)
        moved = GeoSolution(
            frames=frames,
            rvecs={
                f: cv2.Rodrigues(solution.rotation(f) @ motion.T)[0].ravel()
                for f in frames
            },
            centers={
                f: motion @ (solution.centers[f] - pivot) + pivot + translation
                for f in frames
            },
            points={
                track: motion @ (point - pivot) + pivot + translation
                for track, point in solution.points.items()
            },
            focal_px=solution.focal_px,
            cx=solution.cx,
            cy=solution.cy,
            focal_free=solution.focal_free,
            focal_sigma_px=solution.focal_sigma_px,
            frame_intrinsics={
                frame: value.copy()
                for frame, value in solution.frame_intrinsics.items()
            },
        )
        return moved, motion

    def residuals(params: np.ndarray) -> np.ndarray:
        moved, _ = transformed(params)
        rows: list[float] = []
        for f in frames:
            capture = captures[f]
            delta = moved.centers[f] - np.asarray(capture.enu)
            rows.extend((
                float(delta[0] / capture.horizontal_accuracy_m),
                float(delta[1] / capture.horizontal_accuracy_m),
                float(delta[2] / capture.vertical_accuracy_m),
            ))
        for f in az_frames:
            rows.append(
                angle_delta_deg(moved.azimuth_deg(f), captures[f].azimuth_deg)
                / AZIMUTH_SIGMA_DEG
            )
        for constraint in anchors:
            rows.extend(constraint.residual_en(
                moved.points[constraint.track_index][:2]
            ).tolist())
        for control in control_points:
            rows.append(control.residual_deg(
                moved.rotation(control.frame), moved.centers[control.frame],
                *moved.projection_parameters(control.frame),
            ) / control.sigma_deg)
        return np.asarray(rows, dtype=np.float64)

    result = least_squares(
        residuals,
        np.zeros(4, dtype=np.float64),
        method="trf",
        loss="huber",
        f_scale=CONTROL_ALIGNMENT_HUBER_SIGMAS,
        x_scale="jac",
        max_nfev=max_iterations,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
    )
    if result.status <= 0:
        raise RuntimeError(
            "absolute block registration did not converge: "
            f"status={result.status}, nfev={result.nfev}, message={result.message}"
        )

    solved, _ = transformed(result.x)
    weighted = residuals(result.x)
    n_rows = len(weighted)
    dof = max(n_rows - 4, 1)
    variance_factor = float(weighted @ weighted / dof)
    jacobian = np.asarray(result.jac, dtype=np.float64)
    information = jacobian.T @ jacobian
    try:
        block_covariance = variance_factor * np.linalg.inv(information)
    except np.linalg.LinAlgError as exc:
        raise RuntimeError("absolute block registration covariance is singular") from exc
    block_covariance = (block_covariance + block_covariance.T) / 2.0
    if not np.all(np.isfinite(block_covariance)):
        raise RuntimeError("absolute block registration covariance is not finite")

    # Propagate the four datum parameters to each solved centre and selected
    # point.  These matrices deliberately describe ABSOLUTE DATUM uncertainty;
    # they do not pretend to contain relative SfM uncertainty, whose owner is
    # the converged local bundle adjustment.
    radians_per_degree = np.pi / 180.0

    def position_covariance(position: np.ndarray) -> np.ndarray:
        translation = np.asarray(result.x[1:4])
        rotated = position - pivot - translation
        x, y = float(rotated[0]), float(rotated[1])
        gradient = np.array([
            [-y * radians_per_degree, 1.0, 0.0, 0.0],
            [x * radians_per_degree, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ])
        covariance = gradient @ block_covariance @ gradient.T
        return (covariance + covariance.T) / 2.0

    yaw_variance_rad2 = float(block_covariance[0, 0] * radians_per_degree ** 2)
    for f in frames:
        solved.center_covariance[f] = position_covariance(solved.centers[f])
        solved.rotation_covariance[f] = np.diag([
            np.radians(ROLL_SIGMA_DEG) ** 2,
            np.radians(AZIMUTH_SIGMA_DEG) ** 2,
            yaw_variance_rad2,
        ])
    for track in keep_track_covariance:
        if track in solved.points:
            solved.point_covariance[track] = position_covariance(solved.points[track])
    solved.variance_factor = variance_factor

    before_error: list[float] = []
    after_error: list[float] = []
    projection_delta: list[float] = []
    for f, track, uv in observations:
        before_uv, _ = solution.project(f, solution.points[track])
        after_uv, _ = solved.project(f, solved.points[track])
        before_error.append(float(np.linalg.norm(before_uv - uv)))
        after_error.append(float(np.linalg.norm(after_uv - uv)))
        projection_delta.append(float(np.linalg.norm(after_uv - before_uv)))
    max_projection_delta = max(projection_delta, default=0.0)
    if max_projection_delta > 1e-7:
        raise RuntimeError(
            "rigid absolute registration changed visual reprojection: "
            f"max pixel delta={max_projection_delta}"
        )

    gps_residuals = [
        float(np.linalg.norm(solved.centers[f] - np.asarray(captures[f].enu)))
        for f in frames
    ]
    heading_residuals = [
        abs(angle_delta_deg(solved.azimuth_deg(f), captures[f].azimuth_deg))
        for f in az_frames
    ]

    def stats(values: list[float]) -> dict | None:
        if not values:
            return None
        array = np.asarray(values, dtype=np.float64)
        return {
            "count": len(values),
            "median": float(np.median(array)),
            "max": float(np.max(array)),
            "rms": float(np.sqrt(np.mean(array ** 2))),
        }

    solved.report = {
        "absolute_model": "rigid_block_yaw_translation",
        "relative_geometry_owner": "converged local bundle adjustment",
        "covariance_scope": (
            "four-parameter absolute datum covariance; local relative SfM "
            "uncertainty is not restated as datum uncertainty"
        ),
        "block_parameters": {
            "yaw_deg": float(result.x[0]),
            "translation_enu_m": [float(v) for v in result.x[1:4]],
            "pivot_enu_m": [float(v) for v in pivot],
            "covariance": block_covariance.tolist(),
        },
        "cameras": len(frames),
        "points": len(solved.points),
        "visual_observations": len(observations),
        "anchor_constraints": len(anchors),
        "anchor_constraint_rows": anchor_rows,
        "anchor_constraints_by_kind": _count_by(
            [c.as_record()["kind"] for c in anchors]
        ),
        "anchor_constraints_by_geometry": _count_by(
            [c.as_record()["geometry"] for c in anchors]
        ),
        "control_point_constraints": len(control_points),
        "control_point_rows": sum(c.residual_rows for c in control_points),
        "azimuth_constraints": len(az_frames),
        "focal_is_free_parameter": False,
        "focal_refined_by_local_bundle_adjustment": solution.focal_free,
        "residual_rows": n_rows,
        "parameters": 4,
        "degrees_of_freedom": dof,
        "variance_factor": variance_factor,
        "optimizer_status": int(result.status),
        "optimizer_status_meaning": {
            1: "converged: gradient tolerance",
            2: "converged: cost tolerance",
            3: "converged: parameter tolerance",
            4: "converged: cost and parameter tolerance",
        }.get(int(result.status), "unknown"),
        "optimizer_nfev": int(result.nfev),
        "optimizer_cost": float(result.cost),
        "optimizer_optimality": float(result.optimality),
        "optimizer_message": str(result.message),
        "gps_residual_m": stats(gps_residuals),
        "heading_residual_deg": stats(heading_residuals),
        "reprojection_invariance": {
            "before_median_px": float(np.median(before_error)) if before_error else None,
            "after_median_px": float(np.median(after_error)) if after_error else None,
            "max_projected_pixel_delta": max_projection_delta,
        },
        "sigmas": {
            "capture_gps_horizontal_m": {
                f: captures[f].horizontal_accuracy_m for f in frames
            },
            "capture_gps_vertical_m": {
                f: captures[f].vertical_accuracy_m for f in frames
            },
            "azimuth_deg": AZIMUTH_SIGMA_DEG,
            "world_ir_anchor_m": sorted({
                c.sigma_m for c in anchors if isinstance(c, WorldIRAnchorConstraint)
            }),
            "control_point_deg": sorted({c.sigma_deg for c in control_points}),
        },
    }
    solved.report["constraint_residuals"] = constraint_residual_report(
        solved, anchors, control_points
    )
    return solved


def global_solve(
    solution: GeoSolution,
    captures: dict[int, Capture],
    observations: list[tuple[int, int, np.ndarray]],
    anchors: list,
    *,
    control_points: list[ControlPointConstraint] = (),
    keep_track_covariance: tuple[int, ...] = (),
    max_iterations: int = 120,
) -> GeoSolution:
    """Solve every pose, point and the focal length together under all priors.

    ``anchors`` may mix :class:`AnchorConstraint` (a wall plane) and
    :class:`WorldIRAnchorConstraint` (a World IR anchor, point or point-on-curve)
    freely; each declares how many rows it needs.  ``control_points`` are
    reviewed image-pixel-to-anchor correspondences and are assembled here, in
    the SAME solve, rather than applied as a separate alignment step - snapping
    cameras to controls outside the solve is precisely the independent snap the
    weighting exists to prevent.
    """
    frames = solution.frames
    frame_pos = {f: i for i, f in enumerate(frames)}
    tracks = sorted(solution.points)
    track_pos = {t: i for i, t in enumerate(tracks)}
    n_cam, n_pts = len(frames), len(tracks)
    focal_free = solution.focal_free
    n_focal = 1 if focal_free else 0
    cam_block = 6 * n_cam + n_focal

    focal_prior = captures[frames[0]].intrinsics
    cx, cy = solution.cx, solution.cy
    obs_frame = np.array([frame_pos[f] for f, _, _ in observations])
    obs_track = np.array([track_pos[t] for _, t, _ in observations])
    obs_uv = np.array([uv for _, _, uv in observations], dtype=np.float64)
    obs_focal_fixed = np.array([
        solution.intrinsic(f)[0, 0] for f, _, _ in observations
    ])
    obs_cx = np.array([
        solution.intrinsic(f)[0, 2] for f, _, _ in observations
    ])
    obs_cy = np.array([
        solution.intrinsic(f)[1, 2] for f, _, _ in observations
    ])
    n_obs = len(observations)

    az_frames = [f for f in frames if captures[f].azimuth_deg is not None]

    def pack(sol: GeoSolution) -> np.ndarray:
        parts = [np.concatenate([np.concatenate([sol.rvecs[f], sol.centers[f]]) for f in frames])]
        if focal_free:
            parts.append(np.array([sol.focal_px]))
        parts.append(np.concatenate([sol.points[t] for t in tracks]))
        return np.concatenate(parts)

    def unpack(x: np.ndarray):
        cams = x[: 6 * n_cam].reshape(n_cam, 6)
        focal = float(x[6 * n_cam]) if focal_free else solution.focal_px
        pts = x[cam_block:].reshape(n_pts, 3)
        return cams, focal, pts

    control_points = list(control_points)
    # Each constraint states its own row count, so a mixed list needs no case
    # analysis here and adding a sixth anchor shape needs no edit to this file.
    anchor_row_at: list[int] = []
    running = 0
    for constraint in anchors:
        anchor_row_at.append(running)
        running += constraint.residual_rows
    n_anchor_rows = running
    n_control_rows = sum(c.residual_rows for c in control_points)
    for control in control_points:
        if control.frame not in frame_pos:
            raise ValueError(
                f"control point {control.control_id} names frame {control.frame}, "
                "which this solve did not register"
            )

    n_rows = 2 * n_obs + 5 * n_cam + n_anchor_rows + n_control_rows + n_focal

    def residuals(x: np.ndarray) -> np.ndarray:
        cams, focal, pts = unpack(x)
        out = np.zeros(n_rows)
        rotations = np.stack([cv2.Rodrigues(cams[i, :3])[0] for i in range(n_cam)])
        # -- visual reprojection (vectorized; a per-observation Python loop
        # here made every finite-difference Jacobian column unaffordable) ----
        delta = pts[obs_track] - cams[obs_frame, 3:]
        cam_xyz = np.einsum("nij,nj->ni", rotations[obs_frame], delta)
        z = np.where(np.abs(cam_xyz[:, 2]) > 1e-9, cam_xyz[:, 2], 1e-9)
        obs_focal = focal if focal_free else obs_focal_fixed
        out[0: 2 * n_obs: 2] = (
            obs_focal * cam_xyz[:, 0] / z + obs_cx - obs_uv[:, 0]
        ) / REPROJECTION_SIGMA_PX
        out[1: 2 * n_obs: 2] = (
            obs_focal * cam_xyz[:, 1] / z + obs_cy - obs_uv[:, 1]
        ) / REPROJECTION_SIGMA_PX
        base = 2 * n_obs
        # -- capture GPS + compass ------------------------------------------
        for i, f in enumerate(frames):
            cap = captures[f]
            sh, sv = cap.horizontal_accuracy_m, cap.vertical_accuracy_m
            # STRIDE 5, NOT 4. This block reserves 5 rows per camera -- three
            # GPS, one azimuth, one roll -- and the azimuth/roll rows below are
            # written at 5*i+3 and 5*i+4. Writing GPS at 4*i made the two
            # strides collide: camera i+1's GPS rows landed on camera i's
            # azimuth and roll rows and silently overwrote them, so most
            # compass priors never reached the solver at all while the receipt
            # still reported them as applied.
            out[base + 5 * i + 0] = (cams[i, 3] - cap.enu[0]) / sh
            out[base + 5 * i + 1] = (cams[i, 4] - cap.enu[1]) / sh
            out[base + 5 * i + 2] = (cams[i, 5] - cap.enu[2]) / sv
            if cap.azimuth_deg is None:
                out[base + 5 * i + 3] = 0.0
            else:
                axis = rotations[i].T @ np.array([0.0, 0.0, 1.0])
                solved = bearing_deg(axis[0], axis[1])
                out[base + 5 * i + 3] = angle_delta_deg(solved, cap.azimuth_deg) / AZIMUTH_SIGMA_DEG
            # Roll: the camera's image-right axis should lie in the horizontal
            # plane.  Written against image-right rather than image-up so that
            # PITCH stays free; a walking phone really does pitch, but it does
            # not tip sideways.
            right_up = float(rotations[i].T[2, 0])
            out[base + 5 * i + 4] = np.degrees(np.arcsin(np.clip(right_up, -1.0, 1.0))) / ROLL_SIGMA_DEG
        base += 5 * n_cam
        # -- OSM anchors -----------------------------------------------------
        for row, constraint in zip(anchor_row_at, anchors):
            p = pts[track_pos[constraint.track_index]][:2]
            block = constraint.residual_en(p)
            out[base + row: base + row + len(block)] = block
        base += n_anchor_rows
        # -- reviewed control points -----------------------------------------
        for k, control in enumerate(control_points):
            i = frame_pos[control.frame]
            K = solution.intrinsic(control.frame)
            control_focal = focal if focal_free else float(K[0, 0])
            out[base + k] = control.residual_deg(
                rotations[i], cams[i, 3:], control_focal,
                float(K[0, 2]), float(K[1, 2]),
            ) / control.sigma_deg
        base += n_control_rows
        # -- intrinsic prior --------------------------------------------------
        if focal_free:
            out[base] = (focal - focal_prior.focal_px) / focal_prior.sigma_px
        return out

    def sparsity() -> lil_matrix:
        S = lil_matrix((n_rows, cam_block + 3 * n_pts), dtype=int)
        for k in range(n_obs):
            i, t = obs_frame[k], obs_track[k]
            S[2 * k: 2 * k + 2, 6 * i: 6 * i + 6] = 1
            S[2 * k: 2 * k + 2, cam_block + 3 * t: cam_block + 3 * t + 3] = 1
            if focal_free:
                S[2 * k: 2 * k + 2, 6 * n_cam] = 1
        base = 2 * n_obs
        for i in range(n_cam):
            S[base + 5 * i: base + 5 * i + 5, 6 * i: 6 * i + 6] = 1
        base += 5 * n_cam
        for row, constraint in zip(anchor_row_at, anchors):
            t = track_pos[constraint.track_index]
            S[base + row: base + row + constraint.residual_rows,
              cam_block + 3 * t: cam_block + 3 * t + 3] = 1
        base += n_anchor_rows
        # A control point is a statement about ONE CAMERA against a fixed map
        # position, so it touches that camera's six parameters and the focal
        # length - and no structure point at all.  That is what makes it able to
        # pin absolute yaw: it does not move with the block it is judging.
        for k, control in enumerate(control_points):
            i = frame_pos[control.frame]
            S[base + k, 6 * i: 6 * i + 6] = 1
            if focal_free:
                S[base + k, 6 * n_cam] = 1
        base += n_control_rows
        if focal_free:
            S[base, 6 * n_cam] = 1
        return S

    x0 = pack(solution)
    jac_pattern = sparsity().tocsr()
    result = least_squares(
        residuals, x0, jac_sparsity=jac_pattern, method="trf",
        loss="huber", f_scale=3.0, x_scale="jac",
        ftol=GLOBAL_SOLVE_FTOL, xtol=GLOBAL_SOLVE_XTOL, gtol=GLOBAL_SOLVE_GTOL,
        max_nfev=max_iterations, verbose=0,
    )

    cams, focal, pts = unpack(result.x)
    solved = GeoSolution(
        frames=frames,
        rvecs={f: cams[i, :3].copy() for i, f in enumerate(frames)},
        centers={f: cams[i, 3:].copy() for i, f in enumerate(frames)},
        points={t: pts[track_pos[t]].copy() for t in tracks},
        focal_px=focal, cx=cx, cy=cy, focal_free=focal_free,
        frame_intrinsics={
            frame: value.copy()
            for frame, value in solution.frame_intrinsics.items()
        },
    )

    # -- covariance ---------------------------------------------------------
    # ``result.jac`` is SciPy's *modified* Jacobian: with a robust loss it is
    # already scaled so that J^T J is the Gauss-Newton Hessian of the robust
    # cost, which is exactly the information matrix wanted here.  Using it is
    # therefore correct, not a shortcut - but it is worth naming, because the
    # raw Jacobian of the residual function would NOT be.
    #
    # The variance factor is computed from the plain sigma-weighted residuals
    # rather than the robust cost, so downweighted outliers still count against
    # it.  That makes it conservative: a value near 1 cannot have been bought by
    # the loss function hiding the misfits.
    J = result.jac.tocsc() if hasattr(result.jac, "tocsc") else result.jac
    r = residuals(result.x)
    dof = max(n_rows - x0.size, 1)
    variance_factor = float(r @ r / dof)
    solved.variance_factor = variance_factor
    keep = [track_pos[t] for t in keep_track_covariance if t in track_pos]
    cov = _marginal_covariance(J, cam_block, n_pts, keep)
    if cov is not None:
        # Symmetrize once, here.  A covariance is symmetric by definition, but
        # ``np.linalg.inv`` of the Schur-reduced information matrix returns
        # entries that differ in the last bits, and the World IR validator
        # (rightly) refuses a matrix whose off-diagonals disagree.  Fixing it at
        # the emitter would have hidden an asymmetry that meant something; fixing
        # it at the source keeps the emitted number equal to the solved one.
        block = variance_factor * ((cov + cov.T) / 2.0)
        for i, f in enumerate(frames):
            solved.rotation_covariance[f] = block[6 * i: 6 * i + 3, 6 * i: 6 * i + 3]
            solved.center_covariance[f] = block[6 * i + 3: 6 * i + 6, 6 * i + 3: 6 * i + 6]
        if focal_free:
            solved.focal_sigma_px = float(np.sqrt(max(block[6 * n_cam, 6 * n_cam], 0.0)))
        kept = [t for t in keep_track_covariance if t in track_pos]
        for n, t in enumerate(kept):
            s = cam_block + 3 * n
            solved.point_covariance[t] = block[s: s + 3, s: s + 3]
        solved.marginal_block = block
        solved.marginal_offsets = {
            "camera_center": {f: 6 * i + 3 for i, f in enumerate(frames)},
            "camera_rotation": {f: 6 * i for i, f in enumerate(frames)},
            "point": {t: cam_block + 3 * n for n, t in enumerate(kept)},
            "focal": 6 * n_cam if focal_free else None,
        }

    solved.report = {
        "cameras": n_cam,
        "points": n_pts,
        "visual_observations": n_obs,
        "anchor_constraints": len(anchors),
        "anchor_constraint_rows": n_anchor_rows,
        "anchor_constraints_by_kind": _count_by(
            [c.as_record()["kind"] for c in anchors]
        ),
        "anchor_constraints_by_geometry": _count_by(
            [c.as_record()["geometry"] for c in anchors]
        ),
        "control_point_constraints": len(control_points),
        "control_point_rows": n_control_rows,
        "azimuth_constraints": len(az_frames),
        "focal_is_free_parameter": focal_free,
        "residual_rows": n_rows,
        "parameters": int(x0.size),
        "degrees_of_freedom": dof,
        "variance_factor": variance_factor,
        "optimizer_status": int(result.status),
        "optimizer_status_meaning": {
            -1: "improper input",
            0: "EVALUATION BUDGET EXHAUSTED - not converged by ftol/xtol/gtol",
            1: "converged: gradient tolerance",
            2: "converged: cost tolerance",
            3: "converged: parameter tolerance",
            4: "converged: cost and parameter tolerance",
        }.get(int(result.status), "unknown"),
        "optimizer_nfev": int(result.nfev),
        "optimizer_cost": float(result.cost),
        "optimizer_tolerances": {
            "ftol": GLOBAL_SOLVE_FTOL,
            "xtol": GLOBAL_SOLVE_XTOL,
            "gtol": GLOBAL_SOLVE_GTOL,
        },
        "sigmas": {
            "reprojection_px": REPROJECTION_SIGMA_PX,
            "capture_gps_horizontal_m": {f: captures[f].horizontal_accuracy_m for f in frames},
            "capture_gps_vertical_m": {f: captures[f].vertical_accuracy_m for f in frames},
            "azimuth_deg": AZIMUTH_SIGMA_DEG,
            "roll_deg": ROLL_SIGMA_DEG,
            "osm_anchor_m": OSM_ANCHOR_SIGMA_M,
            "world_ir_anchor_m": sorted({c.sigma_m for c in anchors
                                         if isinstance(c, WorldIRAnchorConstraint)}),
            "control_point_deg": sorted({c.sigma_deg for c in control_points}),
            "focal_prior_px": focal_prior.sigma_px,
        },
    }
    solved.report["constraint_residuals"] = constraint_residual_report(
        solved, anchors, control_points
    )
    return solved


def _count_by(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def constraint_residual_report(solution: GeoSolution, anchors: list,
                               control_points=()) -> dict:
    """What every anchor and control point ended up asking for, after the solve.

    A control point that is not shown next to the residuals it competed with is
    indistinguishable from a correction applied behind the solve's back.  So
    each one is reported in its own natural unit - metres for an anchor, degrees
    for a bearing - together with the miss it still carries and the sigma it was
    weighted at, and the summary states both so a reader can see whether a
    constraint won, lost, or was ignored.
    """
    anchor_rows = []
    for constraint in anchors:
        point = solution.points.get(constraint.track_index)
        record = constraint.as_record()
        record["residual_m"] = (
            None if point is None else float(constraint.miss_m(point[:2]))
        )
        record["residual_sigmas"] = (
            None if point is None else float(constraint.miss_m(point[:2]) / constraint.sigma_m)
        )
        anchor_rows.append(record)

    control_rows = []
    for control in control_points:
        record = control.as_record()
        if control.frame in solution.centers:
            rotation = solution.rotation(control.frame)
            center = solution.centers[control.frame]
            residual = control.residual_deg(
                rotation, center, *solution.projection_parameters(control.frame)
            )
            record["residual_deg"] = float(residual)
            record["residual_sigmas"] = float(residual / control.sigma_deg)
            record["range_m"] = float(
                np.linalg.norm(control.anchor.en - np.asarray(center)[:2])
            )
            # The same miss expressed where a reader can feel it: how far across
            # the line of sight the reviewed ray lands from the mapped anchor.
            record["cross_track_miss_m"] = float(
                abs(np.radians(residual)) * record["range_m"]
            )
        else:
            record["residual_deg"] = None
            record["residual_sigmas"] = None
        control_rows.append(record)

    def summarize(values):
        finite = [v for v in values if v is not None and np.isfinite(v)]
        if not finite:
            return None
        return {
            "count": len(finite),
            "median": float(np.median(finite)),
            "max": float(np.max(finite)),
            "rms": float(np.sqrt(np.mean(np.square(finite)))),
        }

    return {
        "anchors": anchor_rows,
        "anchor_residual_m": summarize([r["residual_m"] for r in anchor_rows]),
        "anchor_residual_sigmas": summarize([r["residual_sigmas"] for r in anchor_rows]),
        "control_points": control_rows,
        "control_point_residual_deg": summarize(
            [abs(r["residual_deg"]) for r in control_rows if r["residual_deg"] is not None]
        ),
        "control_point_residual_sigmas": summarize(
            [abs(r["residual_sigmas"]) for r in control_rows
             if r["residual_sigmas"] is not None]
        ),
        "control_point_cross_track_miss_m": summarize(
            [r.get("cross_track_miss_m") for r in control_rows]
        ),
    }


def _marginal_covariance(J, cam_block: int, n_pts: int, keep: list[int]) -> np.ndarray | None:
    """Exact marginal covariance of the camera block plus the kept points.

    Structure points are eliminated by Schur complement.  ``keep`` names the
    few points whose own covariance the receipt must carry (the facade anchor,
    the triangulated fixture); every other point is marginalized away, which is
    what makes the result a true marginal rather than a conditional block.
    """
    keep_set = set(keep)
    J = J.tocsc() if hasattr(J, "tocsc") else J
    order_head = list(range(cam_block)) + [
        cam_block + 3 * k + a for k in keep for a in range(3)
    ]
    tail = [k for k in range(n_pts) if k not in keep_set]
    Jp = J[:, order_head]
    A = np.asarray((Jp.T @ Jp).todense()) if hasattr(Jp.T @ Jp, "todense") else Jp.T @ Jp
    for k in tail:
        cols = [cam_block + 3 * k + a for a in range(3)]
        Jk = J[:, cols]
        C = np.asarray((Jk.T @ Jk).todense()) if hasattr(Jk.T @ Jk, "todense") else Jk.T @ Jk
        B = np.asarray((Jp.T @ Jk).todense()) if hasattr(Jp.T @ Jk, "todense") else Jp.T @ Jk
        if abs(np.linalg.det(C)) < 1e-14:
            continue
        A = A - B @ np.linalg.solve(C, B.T)
    try:
        return np.linalg.inv(A)
    except np.linalg.LinAlgError:
        return None


def relative_point_covariance(solution: GeoSolution, track_index: int) -> np.ndarray | None:
    """Covariance of a point MEASURED AGAINST the camera track it was seen from.

    The absolute covariance of any reconstructed point contains the datum
    uncertainty of the whole block - here dominated by phone GPS altitude,
    whose sigma is metres.  That datum term is common to the cameras and the
    point alike, so it says nothing about whether the triangulation itself is
    sharp.  Differencing the point against the mean camera centre removes it
    exactly, using the cross-covariances the marginal block already carries:

        d = X - (1/n) sum_i C_i ,   cov(d) = L Sigma L^T

    Both numbers belong in the receipt.  The absolute one is what a consumer
    placing this object in a world must budget for; the relative one is what
    says the geometry was solved well.
    """
    block = solution.marginal_block
    offsets = solution.marginal_offsets
    if block is None or track_index not in offsets.get("point", {}):
        return None
    n = len(solution.frames)
    L = np.zeros((3, block.shape[0]))
    start = offsets["point"][track_index]
    L[:, start: start + 3] = np.eye(3)
    for f in solution.frames:
        c = offsets["camera_center"][f]
        L[:, c: c + 3] -= np.eye(3) / n
    result = L @ block @ L.T
    return (result + result.T) / 2.0
