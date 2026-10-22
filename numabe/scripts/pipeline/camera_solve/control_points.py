"""Reviewed image-to-map control points as versioned evidence (FL-4812).

WHAT THIS IS FOR
================
Absolute registration can fail while local structure-from-motion is perfect.
At Numabe the four-image island reconstructs to a 0.21 px median reprojection
with 29 tracks seen in three or more images, and its four signed azimuth
residuals agree to within 4.51 deg - a single consistent global yaw offset of
about -147 deg.  Relative orientation is therefore right and only the block's
ABSOLUTE yaw is wrong.  Nothing in the imagery can fix that: it is missing
information, not bad information.

The information that is missing is a correspondence between something visible
in a picture and something the map knows the position of.  Automatic
association cannot supply it here, because association assumes the block is
already roughly registered - which is the thing that is broken.  A person can
supply it by looking.

SO THIS IS A MANUAL INPUT.  THAT IS THE WHOLE POINT, AND IT IS NOT A CHEAT
=========================================================================
A reviewed correspondence is evidence with a named reviewer, a stated method, a
stated uncertainty and a version - the same standing as an EXIF GPS fix or an
OSM digitised node, both of which were also produced by a person looking at
something.  What would make it a cheat is any of these, and this module is
built to make all of them impossible:

  * applying it as an override.  A control point becomes one weighted residual
    inside the SAME ``global_solve`` as the reprojection, GPS, compass and OSM
    residuals.  It can lose to them.
  * hiding it.  Every control point appears in the solve report with its
    residual next to every other residual, in degrees and in metres of
    cross-track miss, and its ``residual_sigmas`` says whether the solve
    believed it.
  * laundering it.  The set names its reviewer, its method and its
    ``review_state``.  An UNREVIEWED set loads and reports exactly like a
    reviewed one, but says so, so a receipt can never imply a review that did
    not happen.
  * loosening it silently.  Every refusal below is counted and named in the
    load report.  "no control points formed" and "we did not look" cannot
    produce the same document.

WHAT A CONTROL POINT ASSERTS
============================
One pixel in one named image is the image of one named World IR anchor.  It
asserts a BEARING, not a position: see
:class:`~.georegister.ControlPointConstraint` for why a height the map never
stated is not invented here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .georegister import ControlPointConstraint, control_point_sigma_deg
from .osm_anchors import AnchorSet, WorldIRAnchor

CONTROL_POINT_SET_VERSION = "y92.control_point_set/1.0.0"

# A reviewer clicking a building corner in a street-level photograph is good to
# a few pixels, not to one.  Used only when a point states no sigma of its own;
# a point that states one always wins, because the reviewer knew which features
# were crisp and which were a guess.
DEFAULT_PIXEL_SIGMA_PX = 3.0

# A bearing is the horizontal direction of a ray.  A ray pointing nearly
# straight up or down has almost no horizontal component, so its bearing is
# numerically meaningless and its derivative is enormous.  Such a correspondence
# is refused rather than downweighted: it is not a weak measurement, it is a
# measurement of something else.
MIN_RAY_HORIZONTAL_FRACTION = 0.2

# An anchor essentially under the camera has an ill-conditioned bearing for the
# same reason.
MIN_CONTROL_RANGE_M = 2.0

REVIEW_STATE_REVIEWED = "reviewed"
REVIEW_STATE_UNREVIEWED = "unreviewed"
REVIEW_STATE_SYNTHETIC = "synthetic"
REVIEW_STATES = (REVIEW_STATE_REVIEWED, REVIEW_STATE_UNREVIEWED, REVIEW_STATE_SYNTHETIC)


@dataclass(frozen=True)
class ControlPoint:
    """One reviewed correspondence, exactly as the document states it."""

    control_id: str
    capture_id: str
    pixel: tuple[float, float]
    anchor_id: str
    pixel_sigma_px: float
    reviewer: str = ""
    reviewed_at: str = ""
    review_method: str = ""
    note: str = ""

    def as_record(self) -> dict:
        return {
            "control_id": self.control_id,
            "capture_id": self.capture_id,
            "pixel": [float(self.pixel[0]), float(self.pixel[1])],
            "anchor_id": self.anchor_id,
            "pixel_sigma_px": self.pixel_sigma_px,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "review_method": self.review_method,
            "note": self.note,
        }


@dataclass
class ControlPointSet:
    """A loaded, structurally validated control point document."""

    set_id: str
    version: str
    generated_at: str
    anchor_set_id: str
    review_state: str
    reviewer: str
    review_method: str
    review_tool: str
    source_path: str
    digest: str
    points: list[ControlPoint] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.points)

    @property
    def is_reviewed(self) -> bool:
        return self.review_state == REVIEW_STATE_REVIEWED

    def provenance(self) -> dict:
        return {
            "control_point_set_id": self.set_id,
            "control_point_set_version": self.version,
            "generated_at": self.generated_at,
            "declares_anchor_set_id": self.anchor_set_id,
            "review_state": self.review_state,
            "reviewer": self.reviewer,
            "review_method": self.review_method,
            "review_tool": self.review_tool,
            "source_path": self.source_path,
            "sha256": self.digest,
            "control_points_declared": len(self.points),
            "review_state_note": {
                REVIEW_STATE_REVIEWED:
                    "a named person inspected every correspondence in this set",
                REVIEW_STATE_UNREVIEWED:
                    "NOT REVIEWED: these correspondences carry no human sign-off and "
                    "any result resting on them says so",
                REVIEW_STATE_SYNTHETIC:
                    "SYNTHETIC: generated from known geometry to exercise the "
                    "mechanism; never evidence about a real place",
            }[self.review_state],
        }


class ControlPointError(ValueError):
    """A control point document that cannot be read as what it claims to be."""


def load_control_point_set(path: str | Path) -> ControlPointSet:
    """Read and structurally validate one control point document.

    Structural only: this function knows nothing about cameras or geometry, so
    a malformed document is rejected before any solve state exists to be
    contaminated by it.
    """
    path = Path(path)
    raw = path.read_bytes()
    doc = json.loads(raw.decode("utf-8"))

    version = doc.get("control_point_set_version")
    if version != CONTROL_POINT_SET_VERSION:
        raise ControlPointError(
            f"{path}: control_point_set_version is {version!r}, "
            f"this reader implements {CONTROL_POINT_SET_VERSION!r}"
        )
    review = doc.get("review", {})
    state = review.get("state", REVIEW_STATE_UNREVIEWED)
    if state not in REVIEW_STATES:
        raise ControlPointError(
            f"{path}: review.state is {state!r}; expected one of {REVIEW_STATES}"
        )
    if state == REVIEW_STATE_REVIEWED and not review.get("reviewer"):
        raise ControlPointError(
            f"{path}: review.state is 'reviewed' but no reviewer is named. "
            "An unattributed review is not a review."
        )
    default_sigma = float(doc.get("default_pixel_sigma_px", DEFAULT_PIXEL_SIGMA_PX))

    points: list[ControlPoint] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for entry in doc.get("control_points", []):
        control_id = str(entry.get("control_id", ""))
        if not control_id:
            raise ControlPointError(f"{path}: a control point has no control_id")
        if control_id in seen_ids:
            raise ControlPointError(f"{path}: duplicate control_id {control_id!r}")
        seen_ids.add(control_id)
        pixel = entry.get("pixel")
        if not pixel or len(pixel) != 2:
            raise ControlPointError(f"{path}: {control_id} has no [u, v] pixel")
        capture_id = str(entry.get("capture_id", ""))
        anchor_id = str(entry.get("anchor_id", ""))
        if not capture_id or not anchor_id:
            raise ControlPointError(
                f"{path}: {control_id} must name both a capture_id and an anchor_id"
            )
        pair = (capture_id, anchor_id)
        if pair in seen_pairs:
            raise ControlPointError(
                f"{path}: {control_id} is a second correspondence between "
                f"{capture_id} and {anchor_id}; one image sees one anchor once"
            )
        seen_pairs.add(pair)
        sigma = float(entry.get("pixel_sigma_px", default_sigma))
        if not np.isfinite(sigma) or sigma <= 0.0:
            raise ControlPointError(
                f"{path}: {control_id} has pixel_sigma_px={sigma}; a weight of "
                "infinity is an override, not evidence"
            )
        points.append(ControlPoint(
            control_id=control_id,
            capture_id=capture_id,
            pixel=(float(pixel[0]), float(pixel[1])),
            anchor_id=anchor_id,
            pixel_sigma_px=sigma,
            reviewer=str(entry.get("reviewer", review.get("reviewer", ""))),
            reviewed_at=str(entry.get("reviewed_at", review.get("reviewed_at", ""))),
            review_method=str(entry.get("method", review.get("method", ""))),
            note=str(entry.get("note", "")),
        ))
    points.sort(key=lambda c: c.control_id)
    return ControlPointSet(
        set_id=str(doc.get("control_point_set_id", "")),
        version=version,
        generated_at=str(doc.get("generated_at", "")),
        anchor_set_id=str(doc.get("anchor_set_id", "")),
        review_state=state,
        reviewer=str(review.get("reviewer", "")),
        review_method=str(review.get("method", "")),
        review_tool=str(review.get("tool", "")),
        source_path=str(path),
        digest=hashlib.sha256(raw).hexdigest(),
        points=points,
    )


def write_control_point_set(path: str | Path, *, set_id: str, generated_at: str,
                            anchor_set_id: str, review_state: str, reviewer: str = "",
                            review_method: str = "", review_tool: str = "",
                            default_pixel_sigma_px: float = DEFAULT_PIXEL_SIGMA_PX,
                            points: list[ControlPoint]) -> str:
    """Write a control point document and return its sha256.

    Canonical key order and separators, so the same correspondences produce the
    same bytes and the digest in a receipt identifies the content rather than
    the moment it was serialised.
    """
    doc = {
        "anchor_set_id": anchor_set_id,
        "control_point_set_id": set_id,
        "control_point_set_version": CONTROL_POINT_SET_VERSION,
        "control_points": [p.as_record() for p in sorted(points, key=lambda c: c.control_id)],
        "default_pixel_sigma_px": default_pixel_sigma_px,
        "generated_at": generated_at,
        "review": {
            "method": review_method,
            "reviewer": reviewer,
            "state": review_state,
            "tool": review_tool,
        },
    }
    body = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def build_constraints(control_set: ControlPointSet, anchor_set: AnchorSet,
                      solution, frame_of_capture: dict[str, int],
                      image_size: dict[int, tuple[int, int]] | None = None
                      ) -> tuple[list[ControlPointConstraint], dict]:
    """Turn a loaded set into weighted constraints, counting every refusal.

    ``solution`` supplies the CURRENT camera centres and focal length, used for
    one thing only: fixing each correspondence's angular sigma from its range
    and the stated pixel sigma, once, before the solve starts.  A sigma that
    moved with the state would make the cost stop being a least-squares problem,
    and a sigma chosen after seeing the answer would not be a sigma.
    """
    by_anchor_id = anchor_set.by_id
    refused: dict[str, list[str]] = {}

    def refuse(reason: str, control_id: str) -> None:
        refused.setdefault(reason, []).append(control_id)

    if control_set.anchor_set_id and anchor_set.anchor_set_id and \
            control_set.anchor_set_id != anchor_set.anchor_set_id:
        raise ControlPointError(
            f"control point set {control_set.set_id!r} was reviewed against anchor set "
            f"{control_set.anchor_set_id!r} but was handed "
            f"{anchor_set.anchor_set_id!r}. Anchor ids are only meaningful inside the "
            "set that minted them, so this is refused rather than resolved."
        )

    constraints: list[ControlPointConstraint] = []
    for point in control_set.points:
        frame = frame_of_capture.get(point.capture_id)
        if frame is None:
            refuse("capture is not a registered frame in this solve", point.control_id)
            continue
        anchor: WorldIRAnchor | None = by_anchor_id.get(point.anchor_id)
        if anchor is None:
            refuse("anchor_id is not in the anchor set", point.control_id)
            continue
        if image_size is not None and frame in image_size:
            width, height = image_size[frame]
            u, v = point.pixel
            if not (0.0 <= u < width and 0.0 <= v < height):
                refuse("pixel lies outside the image", point.control_id)
                continue
        center = solution.centers[frame]
        rng = float(np.linalg.norm(anchor.en - np.asarray(center)[:2]))
        if rng < MIN_CONTROL_RANGE_M:
            refuse(f"anchor is closer than {MIN_CONTROL_RANGE_M} m, bearing is "
                   "ill-conditioned", point.control_id)
            continue
        rotation = solution.rotation(frame)
        focal_px, cx, cy = solution.projection_parameters(frame)
        ray_cam = np.array([(point.pixel[0] - cx) / focal_px,
                            (point.pixel[1] - cy) / focal_px, 1.0])
        ray_world = rotation.T @ ray_cam
        ray_world = ray_world / max(float(np.linalg.norm(ray_world)), 1e-12)
        if float(np.hypot(ray_world[0], ray_world[1])) < MIN_RAY_HORIZONTAL_FRACTION:
            refuse("ray is too close to vertical for a bearing to mean anything",
                   point.control_id)
            continue
        sigma_deg = control_point_sigma_deg(
            point.pixel_sigma_px, focal_px, anchor.sigma_m, rng
        )
        constraints.append(ControlPointConstraint(
            control_id=point.control_id,
            frame=frame,
            uv=np.array([point.pixel[0], point.pixel[1]], dtype=np.float64),
            anchor=anchor,
            sigma_deg=sigma_deg,
            pixel_sigma_px=point.pixel_sigma_px,
            range_m_at_setup=rng,
            reviewer=point.reviewer,
            reviewed_at=point.reviewed_at,
            review_method=point.review_method,
            note=point.note,
        ))

    per_frame: dict[str, int] = {}
    per_kind: dict[str, int] = {}
    for c in constraints:
        per_frame[str(c.frame)] = per_frame.get(str(c.frame), 0) + 1
        per_kind[c.anchor.anchor_type] = per_kind.get(c.anchor.anchor_type, 0) + 1

    report = {
        **control_set.provenance(),
        "anchor_set_matched": anchor_set.anchor_set_id,
        "constraints_formed": len(constraints),
        "constraints_by_frame": dict(sorted(per_frame.items())),
        "constraints_by_anchor_kind": dict(sorted(per_kind.items())),
        "refused": {reason: sorted(ids) for reason, ids in sorted(refused.items())},
        "refused_total": sum(len(v) for v in refused.values()),
        "sigma_deg": {
            "min": float(min(c.sigma_deg for c in constraints)) if constraints else None,
            "median": float(np.median([c.sigma_deg for c in constraints]))
            if constraints else None,
            "max": float(max(c.sigma_deg for c in constraints)) if constraints else None,
        },
        "sigma_basis": (
            "quadrature of the reviewed pixel sigma over the focal length and the "
            "anchor's own map sigma over its range; fixed once from the pre-solve "
            "state so the cost stays a least-squares problem"
        ),
        "frames_with_at_least_one_control": len(per_frame),
        "yaw_observability_note": (
            "a single control point on a single camera pins that camera's yaw only; "
            "an unregistered BLOCK is pinned when controls reach enough cameras that "
            "no rigid rotation of the block can satisfy them all"
        ),
    }
    return constraints, report
