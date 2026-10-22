"""Triangulated candidates and their reprojection into every source frame.

FL-4812.  A candidate is a world point the solve believes in, together with
everything needed to judge it: which frames saw it, how wide an angle they
subtend, what it costs to reproject it back, and how uncertain it is.

WHY REPROJECT INTO *EVERY* FRAME AND NOT ONLY THE MATCHED ONES
==============================================================
Reprojecting only into the frames that produced the point is circular: those
are the observations the solve already minimised.  Pushing the point back into
frames it was NOT fitted to is the part that can fail, and the failure modes
are distinct and worth naming separately in the receipt:

    ``matched``          an observation exists; the residual is a real check
    ``predicted``        in front of the camera and inside the frame, but never
                         matched there - a prediction the imagery could refute
    ``outside_frame``    projects off the sensor; consistent, not evidence
    ``behind_camera``    the frame cannot see it at all
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .capture import Capture
from .georegister import GeoSolution
from .relative import triangulation_angle_deg

STATUS_MATCHED = "matched"
STATUS_PREDICTED = "predicted"
STATUS_OUTSIDE_FRAME = "outside_frame"
STATUS_BEHIND_CAMERA = "behind_camera"


@dataclass
class Candidate:
    """One triangulated world point under evaluation."""

    candidate_id: str
    semantic_class: str
    track_index: int
    position: np.ndarray  # ENU metres
    observations: dict[int, np.ndarray]  # frame -> observed uv
    semantic_votes: dict[str, int] = field(default_factory=dict)
    # The class the per-view votes must actually AGREE ON.  Kept separate from
    # ``semantic_class`` because unanimous disagreement is still unanimous.
    expected_vote: str = ""
    covariance: np.ndarray | None = None
    notes: str = ""

    @property
    def viewpoints(self) -> int:
        return len(self.observations)


def viewpoint_angle(solution: GeoSolution, candidate: Candidate) -> float:
    centers = np.array([solution.centers[f] for f in sorted(candidate.observations)])
    return triangulation_angle_deg(centers, candidate.position)


def reproject_into_all_frames(
    solution: GeoSolution,
    candidate: Candidate,
    captures: dict[int, Capture],
) -> list[dict]:
    """Push the point back into every solved frame and record what happened."""
    rows: list[dict] = []
    for frame in solution.frames:
        capture = captures[frame]
        uv, depth = solution.project(frame, candidate.position)
        if depth <= 1e-9 or not np.all(np.isfinite(uv)):
            rows.append({
                "frame_rank": capture.rank,
                "capture_id": capture.capture_id,
                "status": STATUS_BEHIND_CAMERA,
                "depth_m": float(depth),
            })
            continue
        inside = 0.0 <= uv[0] <= capture.width and 0.0 <= uv[1] <= capture.height
        row = {
            "frame_rank": capture.rank,
            "capture_id": capture.capture_id,
            "projected_px": [float(uv[0]), float(uv[1])],
            "depth_m": float(depth),
        }
        observed = candidate.observations.get(frame)
        if observed is not None:
            row["status"] = STATUS_MATCHED
            row["observed_px"] = [float(observed[0]), float(observed[1])]
            row["residual_px"] = float(np.hypot(uv[0] - observed[0], uv[1] - observed[1]))
        elif inside:
            row["status"] = STATUS_PREDICTED
        else:
            row["status"] = STATUS_OUTSIDE_FRAME
        rows.append(row)
    return rows


def matched_residuals(rows: list[dict]) -> np.ndarray:
    return np.array([r["residual_px"] for r in rows if r.get("status") == STATUS_MATCHED])


def summarize(rows: list[dict]) -> dict:
    residuals = matched_residuals(rows)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "frames_reprojected": len(rows),
        "status_counts": counts,
        "matched_residual_px": {
            "count": int(residuals.size),
            "median": float(np.median(residuals)) if residuals.size else None,
            "mean": float(residuals.mean()) if residuals.size else None,
            "max": float(residuals.max()) if residuals.size else None,
            "p90": float(np.percentile(residuals, 90)) if residuals.size else None,
        },
    }


def covariance_record(cov: np.ndarray | None) -> dict | None:
    """A covariance plus the scalars a reader actually judges it by."""
    if cov is None:
        return None
    cov = np.asarray(cov, dtype=float)
    if not np.all(np.isfinite(cov)):
        return {"finite": False, "matrix": None}
    sym = (cov + cov.T) / 2.0
    eigenvalues = np.linalg.eigvalsh(sym)
    return {
        "finite": True,
        "positive_definite": bool(np.all(eigenvalues > 0)),
        "matrix": [[float(v) for v in row] for row in sym],
        "sigma_m": [float(np.sqrt(max(sym[i, i], 0.0))) for i in range(sym.shape[0])],
        "sigma_major_m": float(np.sqrt(max(eigenvalues.max(), 0.0))),
        "sigma_minor_m": float(np.sqrt(max(eigenvalues.min(), 0.0))),
        "units": "m",
    }
