"""End-to-end camera solve for one open control corridor (FL-4812).

    frames + capture metadata + OSM
      -> features and cross-frame matches
      -> relative reconstruction (essential geometry, PnP, bundle adjustment)
      -> similarity alignment onto the capture GPS track
      -> ONE global weighted solve (reprojection + GPS + compass + OSM + focal prior)
      -> reviewed image-to-map controls when supplied, otherwise the legacy
         automatically selected OSM-corroborated facade anchor
      -> one triangulated fixture that OSM does not contain
      -> promotion gates
      -> reprojection of both into EVERY source frame
      -> residual + covariance receipt

Automatic candidate selection remains predicate-driven.  Reviewed control
points are a separate, versioned evidence input: their author, method, stated
uncertainty, refusals, and residuals all remain in the receipt.
"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import gates, receipts
from .capture import Capture, capture_from_item, capture_from_source_record
from .features import FrameFeatures, detect
from .geodesy import EnuFrame
from .control_points import build_constraints, load_control_point_set
from .georegister import (
    AnchorConstraint,
    GeoSolution,
    absolute_block_solve,
    global_solve,
    initial_alignment,
    initial_control_alignment,
)
from .gravity import estimate_from_solution, level_solution
from .osm_anchors import (
    BuildingOutline,
    estimate_block_offset,
    has_class,
    is_pole_like,
    load_anchor_set,
    load_buildings,
    nearest_wall,
)
from .relative import intrinsic_matrix, seed_rng
from .sfm import (
    MAX_RECONSTRUCTION_REPROJECTION_PX,
    Reconstruction,
    build_match_graph,
    bundle_adjust,
    filter_outlier_points,
    incremental_reconstruct,
    prune_for_global_solve,
    reprojection_errors,
)
from .triangulate import Candidate, reproject_into_all_frames, summarize, viewpoint_angle

# ---- facade anchor selection --------------------------------------------
# A COARSE data-association radius, not a measurement sigma.  Before the global solve the
# block still sits wherever the capture GPS put it, several metres from OSM; a
# gate as tight as the OSM sigma would therefore associate nothing and the
# anchor could never be formed.  The residual this association feeds is still
# weighted at OSM_ANCHOR_SIGMA_M (1.5 m) - a wide gate with a tight sigma, which
# is the normal way round.  A gate as tight as the sigma is how a solve silently
# ends up with no anchors at all.
FACADE_COARSE_GATE_M = 4.0
# After the block's systematic shift is removed, the FINE gate is set from the
# scatter that remains, not from a number chosen in advance: the offset estimate
# reports its own robust residual, and a real facade point should sit inside a
# small multiple of it.  Hard-coding 1.5 m here rejected every candidate on real
# data, because the measured post-shift scatter was 1.43 m - the gate was one
# sigma wide.  A floor keeps it from collapsing if the scatter is tiny.
FACADE_FINE_GATE_SIGMA_MULTIPLE = 1.75
FACADE_FINE_GATE_FLOOR_M = 1.5
# Support points on a facade run ALONG the wall.  A plane fitted to them must
# therefore be nearly parallel to the OSM wall segment.
FACADE_MAX_PLANE_ANGLE_DEG = 20.0
# A facade is OPAQUE.  This corridor is lined with a tall chain-link catch net
# that stands right along a building footprint: it is planar, parallel to the
# wall, inside every distance gate, and covered in corner features, so support
# count, plane angle and wall distance all accept it.  What separates it from
# the wall behind it is that you can see through it - the reconstruction has
# points BEHIND it on the same rays.  So an anchor must be the last surface
# along its own ray: nothing reconstructed further away, near the same pixel.
FACADE_OCCLUSION_RADIUS_PX = 25.0
FACADE_OCCLUSION_DEPTH_MARGIN_M = 1.5
# Per-view corroboration is done in PIXELS, not metres.  The metric version -
# intersect each view's ray with the wall plane and measure the miss in metres -
# has to widen its tolerance to the point's own absolute uncertainty, which on
# capture GPS is several metres, and at that width it corroborates anything.
# Asking instead whether the observation lies along the IMAGE of the wall is the
# same question asked where the measurement actually is, and its tolerance does
# not inflate with distance.
FACADE_WALL_IMAGE_TOLERANCE_PX = 25.0
# A facade is a surface.  These two demand that the wall carry real surface
# support before any point on it is used as an anchor: several points, spread
# along the wall rather than stacked on one spot.
FACADE_MIN_SUPPORT_POINTS = 6
FACADE_MIN_ALONG_WALL_M = 2.0

# A fixture only counts as "absent from OSM" if it is not simply a mis-traced
# corner of a structure that IS mapped.  2.5 m is well outside the OSM
# digitising sigma of 1.5 m.  It is NOT the proof of absence on its own: that
# comes from the class census, which finds zero pole-like objects of any
# tagging anywhere in the extract.
FIXTURE_MIN_BUILDING_CLEARANCE_M = 2.5

# ---- shared ---------------------------------------------------------------
# Phone held at chest height while walking.  A prior, used only to put a floor
# under "above the ground" tests, never as a measurement.
CAMERA_HEIGHT_PRIOR_M = 1.55

# ---- unmapped fixture selection -------------------------------------------
# Pole-like cluster: points sharing a horizontal position within this radius
# and spanning at least this much height.
FIXTURE_CLUSTER_RADIUS_M = 0.8
FIXTURE_MIN_VERTICAL_SPAN_M = 1.0

# A vertical structure's image edges run within this many degrees of vertical.
VERTICAL_EDGE_TOLERANCE_DEG = 25.0
STRUCTURE_WINDOW_PX = 21

# Evaluation budget for the global solve.  Reported alongside the optimizer's
# own termination status so a run that ran out of budget is visible rather than
# being read as a converged one.
OPTIMIZER_MAX_EVALUATIONS = 1500


@dataclass
class CorridorInputs:
    items_path: Path
    frames_dir: Path
    osm_path: Path
    anchors_path: Path | None = None
    control_points_path: Path | None = None


def _materialize_working_image(
    source_path: Path, image: np.ndarray, source_digest: str, policy: dict[str, Any],
) -> tuple[Path, np.ndarray, str, Any]:
    """Create the frozen-resolution SfM input deterministically from source pixels."""
    max_edge = int(policy.get("max_edge_px") or max(image.shape[:2]))
    if max(image.shape[:2]) <= max_edge:
        return source_path, image, source_digest, "source_pixels"
    interpolation = str(policy.get("resize_interpolation") or "opencv_inter_area")
    if interpolation != "opencv_inter_area":
        raise ValueError(f"unsupported working-image interpolation: {interpolation}")
    encoding = str(policy.get("encoding") or "jpeg")
    if encoding != "jpeg":
        raise ValueError(f"unsupported working-image encoding: {encoding}")
    quality = int(policy.get("jpeg_quality") or 95)
    scale = max_edge / float(max(image.shape[:2]))
    resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    policy_record = {
        "encoding": encoding,
        "jpeg_quality": quality,
        "max_edge_px": max_edge,
        "resize_interpolation": interpolation,
        "opencv_version": cv2.__version__,
    }
    key = hashlib.sha256(
        (source_digest + "\n" + json.dumps(policy_record, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    cache_dir = Path(__file__).resolve().parents[3] / ".run" / "camera_solve_working"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"{key}.jpg"
    ok, encoded = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"failed to encode deterministic working image for {source_path}")
    payload = encoded.tobytes()
    payload_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if not destination.exists() or receipts.file_digest(destination) != payload_digest:
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
    working = cv2.imread(str(destination), cv2.IMREAD_GRAYSCALE)
    if working is None:
        raise RuntimeError(f"cannot read deterministic working image {destination}")
    return destination, working, payload_digest, policy_record


def load_captures(inputs: CorridorInputs) -> list[Capture]:
    """Pair canonical manifest or legacy STAC records with retained pixels."""
    document = json.loads(inputs.items_path.read_text(encoding="utf-8"))
    source_records = document.get("captures") if isinstance(document, dict) else None
    if source_records is not None:
        working_policy = document.get("working_image_policy") or {}
        component_id = document.get("default_reconstruction_component")
        if component_id is not None:
            components = {
                str(component["component_id"]): component
                for component in document.get("reconstruction_components", [])
            }
            if str(component_id) not in components:
                raise ValueError(
                    f"default reconstruction component {component_id!r} is not declared"
                )
            selected_ids = {
                str(capture_id)
                for capture_id in components[str(component_id)].get("capture_ids", [])
            }
            declared_ids = {str(record["capture_id"]) for record in source_records}
            unknown = sorted(selected_ids - declared_ids)
            if unknown:
                raise ValueError(
                    f"reconstruction component {component_id!r} names unknown captures: {unknown}"
                )
            source_records = [
                record for record in source_records
                if str(record["capture_id"]) in selected_ids
            ]
        captures: list[Capture] = []
        for record in source_records:
            local_path = Path(str(record.get("local_path") or ""))
            candidates = [inputs.frames_dir / local_path.name]
            if local_path.is_absolute():
                candidates.insert(0, local_path)
            else:
                candidates.append(inputs.items_path.parent / local_path)
            path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if path is None:
                continue
            source_image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if source_image is None:
                continue
            source_digest = receipts.file_digest(path)
            working_path, image, working_digest, working_policy_record = _materialize_working_image(
                path, source_image, source_digest, working_policy
            )
            captures.append(capture_from_source_record(
                record, str(working_path), image.shape[1], image.shape[0],
                digest=source_digest,
                source_dimensions_px=(source_image.shape[1], source_image.shape[0]),
                working_image_digest=working_digest,
                working_image_policy=working_policy_record,
            ))
        captures.sort(key=lambda c: (c.rank, c.capture_id))
        return captures

    items = document
    if isinstance(items, dict):
        items = items.get("features", [])
    by_id = {str(i["id"]): i for i in items}
    captures: list[Capture] = []
    frame_paths = sorted(set(inputs.frames_dir.glob("*.jpg")) | set(inputs.frames_dir.glob("*.jpeg")))
    for path in frame_paths:
        capture_id = path.stem.split("_", 1)[-1]
        item = by_id.get(capture_id)
        if item is None:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        captures.append(
            capture_from_item(
                item, str(path), image.shape[1], image.shape[0],
                digest=receipts.file_digest(path),
            )
        )
    captures.sort(key=lambda c: (c.rank, c.capture_id))
    return captures


def enu_frame_for(captures: list[Capture]) -> EnuFrame:
    """Anchor the local frame on the median capture, so it is data-defined."""
    lons = sorted(c.lon for c in captures)
    lats = sorted(c.lat for c in captures)
    alts = sorted(c.alt for c in captures if c.alt is not None)
    mid = len(captures) // 2
    return EnuFrame(
        origin_lon=lons[mid], origin_lat=lats[mid],
        origin_alt=alts[len(alts) // 2] if alts else 0.0,
    )


def _vertical_structure_vote(image: np.ndarray, uv: np.ndarray,
                             window: int = STRUCTURE_WINDOW_PX) -> tuple[bool, float]:
    """Is the local image structure a near-vertical edge?

    Structure tensor over a small window: the dominant gradient direction is
    perpendicular to the edge, so a VERTICAL edge has a HORIZONTAL dominant
    gradient.  This is the per-viewpoint semantic evidence the consistency gate
    consumes; it can and does disagree between views, which is the point.
    """
    h, w = image.shape
    x, y = int(round(uv[0])), int(round(uv[1]))
    r = window // 2
    if x - r < 0 or y - r < 0 or x + r >= w or y + r >= h:
        return False, float("nan")
    patch = image[y - r: y + r + 1, x - r: x + r + 1].astype(np.float64)
    gx = cv2.Sobel(patch, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_64F, 0, 1, ksize=3)
    jxx, jyy, jxy = float((gx * gx).sum()), float((gy * gy).sum()), float((gx * gy).sum())
    if jxx + jyy < 1e-6:
        return False, float("nan")
    # Dominant gradient orientation of the 2x2 structure tensor.
    theta = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
    edge_from_vertical = abs(np.degrees(theta)) % 180.0
    edge_from_vertical = min(edge_from_vertical, 180.0 - edge_from_vertical)
    # theta is the gradient direction; a vertical edge has |theta| near 0 or 180.
    return edge_from_vertical <= VERTICAL_EDGE_TOLERANCE_DEG, float(edge_from_vertical)


def _wall_image_miss_px(solution: GeoSolution, frame: int, uv: np.ndarray,
                        wall_a: np.ndarray, wall_b: np.ndarray,
                        height_z: float) -> float | None:
    """Perpendicular pixel distance from an observation to the OSM wall's image.

    Independent per-view corroboration of the OSM association: each camera has
    to agree on its own, so a facade anchor is not one global distance test
    wearing several hats.  Returns None when the wall cannot be imaged from this
    viewpoint at all, which is reported as inconclusive rather than as a pass.
    """
    ends = []
    for end in (wall_a, wall_b):
        projected, depth = solution.project(frame, np.array([end[0], end[1], height_z]))
        if depth <= 1e-9 or not np.all(np.isfinite(projected)):
            return None
        ends.append(projected)
    a, b = ends
    direction = b - a
    length = float(np.linalg.norm(direction))
    if length < 1e-6:
        return float(np.linalg.norm(uv - a))
    normal = np.array([-direction[1], direction[0]]) / length
    return abs(float((uv - a) @ normal))


def _frame_projection_table(solution: GeoSolution) -> dict[int, tuple[np.ndarray, np.ndarray, list[int]]]:
    """Project every solved point into every frame once, for occlusion tests."""
    table: dict[int, tuple[np.ndarray, np.ndarray, list[int]]] = {}
    tracks = sorted(solution.points)
    for frame in solution.frames:
        uvs, depths, keep = [], [], []
        for track_index in tracks:
            uv, depth = solution.project(frame, solution.points[track_index])
            if depth <= 1e-9 or not np.all(np.isfinite(uv)):
                continue
            uvs.append(uv)
            depths.append(depth)
            keep.append(track_index)
        table[frame] = (
            np.array(uvs).reshape(-1, 2), np.array(depths), keep,
        )
    return table


def _is_terminal_along_ray(solution: GeoSolution, table, track_index: int,
                           observations: dict[int, np.ndarray]) -> tuple[bool, dict]:
    """True when no reconstructed point sits FURTHER along the same ray.

    Run per view and required in every one of them: a see-through structure
    fails wherever the thing behind it was reconstructed, and one view is
    enough to disqualify it.
    """
    per_view: dict[str, int] = {}
    terminal = True
    for frame in sorted(observations):
        uvs, depths, keep = table[frame]
        if uvs.size == 0:
            continue
        own = None
        for i, t in enumerate(keep):
            if t == track_index:
                own = i
                break
        if own is None:
            continue
        near = np.hypot(uvs[:, 0] - uvs[own, 0], uvs[:, 1] - uvs[own, 1]) <= FACADE_OCCLUSION_RADIUS_PX
        behind = int(np.count_nonzero(near & (depths > depths[own] + FACADE_OCCLUSION_DEPTH_MARGIN_M)))
        per_view[str(frame)] = behind
        if behind > 0:
            terminal = False
    return terminal, per_view


def _track_observations(graph, solution: GeoSolution, track_index: int) -> dict[int, np.ndarray]:
    return {
        frame: graph.features[frame].xy[kp]
        for frame, kp in graph.tracks[track_index]
        if frame in solution.centers
    }


def select_facade_anchor(solution: GeoSolution, graph, buildings: list[BuildingOutline],
                         ground_z: float) -> tuple[int, Any, dict] | None:
    """Best reconstructed point that behaves like an OSM building facade.

    Three predicates, each answering a way the naive version got this wrong on
    real data:

    1. The block's systematic shift against OSM is estimated and removed first,
       so association can use a tight gate.  A wide gate accepted a chain-link
       catch net standing 3.9 m in front of a building.
    2. The wall must carry SURFACE support - several points spread along it.  A
       utility pole 0.22 m from a wall passes every distance test a facade does.
    3. A plane fitted to that support must run nearly parallel to the OSM wall.
    """
    above = {t: p for t, p in solution.points.items() if p[2] >= ground_z + 0.5}
    if not above:
        return None
    order = sorted(above)
    offset, offset_report = estimate_block_offset(
        [above[t][:2] for t in order], buildings, FACADE_COARSE_GATE_M
    )
    fine_gate = max(
        FACADE_FINE_GATE_FLOOR_M,
        FACADE_FINE_GATE_SIGMA_MULTIPLE * float(offset_report.get("residual_median_m", 0.0)),
    )

    groups: dict[tuple, list[tuple[int, np.ndarray, Any]]] = {}
    for track_index in order:
        shifted = above[track_index][:2] - offset
        wall = nearest_wall(shifted, buildings)
        if wall is None or wall.distance_m > fine_gate:
            continue
        key = (wall.osm_ref, tuple(wall.seg_a), tuple(wall.seg_b))
        groups.setdefault(key, []).append((track_index, above[track_index], wall))

    table = _frame_projection_table(solution)
    supported: list[tuple[tuple, int, Any, dict]] = []
    for (osm_ref, seg_a, seg_b), members in groups.items():
        if len(members) < FACADE_MIN_SUPPORT_POINTS:
            continue
        a, b = np.array(seg_a), np.array(seg_b)
        direction = b - a
        length = float(np.linalg.norm(direction))
        if length < 1e-6:
            continue
        unit = direction / length
        xy = np.array([point[:2] - offset for _, point, _ in members])
        offsets = (xy - a) @ unit
        along = float(offsets.max() - offsets.min())
        if along < FACADE_MIN_ALONG_WALL_M:
            continue  # vertical post, or a single corner: not a surface
        centred = xy - xy.mean(axis=0)
        principal = np.linalg.svd(centred, full_matrices=False)[2][0]
        angle = float(np.degrees(np.arccos(min(1.0, abs(float(principal @ unit))))))
        if angle > FACADE_MAX_PLANE_ANGLE_DEG:
            continue  # support does not run along the wall; not that facade
        evidence = {
            "osm_ref": osm_ref,
            "support_points": len(members),
            "support_extent_along_wall_m": along,
            "support_plane_angle_to_wall_deg": angle,
            "block_offset_removed_m": [float(offset[0]), float(offset[1])],
            "block_offset_estimate": offset_report,
            "fine_association_gate_m": fine_gate,
        }
        for track_index, point, wall in members:
            obs = _track_observations(graph, solution, track_index)
            if len(obs) < 3:
                continue
            terminal, behind = _is_terminal_along_ray(solution, table, track_index, obs)
            if not terminal:
                continue  # something was reconstructed behind it: see-through
            centers = np.array([solution.centers[f] for f in sorted(obs)])
            from .relative import triangulation_angle_deg
            tri = triangulation_angle_deg(centers, point)
            point_evidence = dict(evidence)
            point_evidence["points_behind_per_view"] = behind
            # Every member of this group is an equally valid point ON the same
            # facade, so the representative is chosen by how well it is MEASURED:
            # viewpoints first, then how tightly it reprojects, then how wide an
            # angle it was seen from.  Ranking by group size first picked
            # whichever wall had the most features and then an arbitrary point
            # on it, which then failed the reprojection gate.
            errors = []
            for f, uv in obs.items():
                proj, depth = solution.project(f, point)
                if depth > 1e-9 and np.all(np.isfinite(proj)):
                    errors.append(float(np.hypot(proj[0] - uv[0], proj[1] - uv[1])))
            median_px = float(np.median(errors)) if errors else float("inf")
            point_evidence["selection_median_reprojection_px"] = median_px
            supported.append(((len(obs), -median_px, tri, len(members), along),
                              track_index, wall, point_evidence))
    if not supported:
        return None
    supported.sort(key=lambda s: s[0], reverse=True)
    return supported[0][1], supported[0][2], supported[0][3]


def select_unmapped_fixture(solution: GeoSolution, graph, buildings: list[BuildingOutline],
                            ground_z: float, images: dict[int, np.ndarray]) -> dict | None:
    """Strongest pole-like cluster of points that OSM contains nothing near.

    The predicate chain is: clear of every mapped building, above the ground,
    vertically extended, seen from at least three viewpoints, and showing a
    near-vertical edge in each of those viewpoints.
    """
    far: list[tuple[int, np.ndarray]] = []
    for track_index, point in solution.points.items():
        if point[2] < ground_z + 0.5:
            continue
        wall = nearest_wall(point[:2], buildings)
        if wall is not None and wall.distance_m < FIXTURE_MIN_BUILDING_CLEARANCE_M:
            continue
        far.append((track_index, point))
    if not far:
        return None

    clusters: list[list[tuple[int, np.ndarray]]] = []
    for track_index, point in sorted(far, key=lambda kv: kv[0]):
        for cluster in clusters:
            anchor = cluster[0][1]
            if np.linalg.norm(point[:2] - anchor[:2]) <= FIXTURE_CLUSTER_RADIUS_M:
                cluster.append((track_index, point))
                break
        else:
            clusters.append([(track_index, point)])

    scored = []
    for cluster in clusters:
        zs = np.array([p[2] for _, p in cluster])
        span = float(zs.max() - zs.min())
        if span < FIXTURE_MIN_VERTICAL_SPAN_M or len(cluster) < 2:
            continue
        for track_index, point in cluster:
            obs = _track_observations(graph, solution, track_index)
            if len(obs) < 3:
                continue
            votes: dict[str, int] = {}
            angles = {}
            for frame, uv in obs.items():
                vertical, deviation = _vertical_structure_vote(images[frame], uv)
                votes["fixture" if vertical else "not_vertical_structure"] = (
                    votes.get("fixture" if vertical else "not_vertical_structure", 0) + 1
                )
                angles[frame] = deviation
            centers = np.array([solution.centers[f] for f in sorted(obs)])
            from .relative import triangulation_angle_deg
            angle = triangulation_angle_deg(centers, point)
            if votes.get("fixture", 0) != len(obs):
                continue
            scored.append({
                "track_index": track_index,
                "point": point,
                "observations": obs,
                "semantic_votes": votes,
                "edge_deviation_deg": angles,
                "cluster_size": len(cluster),
                "cluster_vertical_span_m": span,
                "triangulation_angle_deg": angle,
                "building_clearance_m": (
                    nearest_wall(point[:2], buildings).distance_m if buildings else float("inf")
                ),
            })
    if not scored:
        return None
    scored.sort(key=lambda c: (-len(c["observations"]), -c["triangulation_angle_deg"]))
    return scored[0]


def run(inputs: CorridorInputs, *, seed: int = 4812, match_window: int = 4) -> dict:
    """Run the whole chain and return everything the emitter and receipt need."""
    if inputs.control_points_path is not None and inputs.anchors_path is None:
        raise ValueError("control_points_path requires anchors_path")
    seed_rng(seed)
    captures = load_captures(inputs)
    if len(captures) < 3:
        raise ValueError(f"need at least 3 solvable captures, got {len(captures)}")
    frame = enu_frame_for(captures)
    for capture in captures:
        capture.enu = frame.to_enu(capture.lon, capture.lat, capture.alt or 0.0)
    by_index = {i: c for i, c in enumerate(captures)}

    stages: dict[str, Any] = {}
    source_index = json.loads(inputs.items_path.read_text(encoding="utf-8"))
    stages["capture_selection"] = {
        "source_index": str(inputs.items_path),
        "source_schema": source_index.get("schema") if isinstance(source_index, dict) else "stac",
        "reconstruction_component": (
            source_index.get("default_reconstruction_component")
            if isinstance(source_index, dict) else None
        ),
        "capture_ids": [capture.capture_id for capture in captures],
        "captures": len(captures),
        "working_image_policy": (
            source_index.get("working_image_policy")
            if isinstance(source_index, dict) else None
        ),
    }

    features: list[FrameFeatures] = [
        detect(c.image_path, c.capture_id) for c in captures
    ]
    stages["features"] = {
        "detector": "SIFT",
        "per_frame": {str(c.rank): len(f) for c, f in zip(captures, features)},
    }

    prior = captures[0].intrinsics
    frame_intrinsics = {
        index: intrinsic_matrix(capture.intrinsics.focal_px, *capture.principal_point)
        for index, capture in by_index.items()
    }
    graph, pair_stats = build_match_graph(
        features, frame_intrinsics, window=match_window
    )
    stages["matching"] = {
        "window": match_window,
        "pairs": pair_stats,
        "tracks": len(graph.tracks),
        "track_length_histogram": _histogram([len(t) for t in graph.tracks]),
    }

    recon: Reconstruction = incremental_reconstruct(
        graph,
        prior.focal_px,
        *captures[0].principal_point,
        frame_intrinsics=frame_intrinsics,
    )
    # RMS alone flatters nothing here but hides a lot: the scale-free stage still
    # holds every track that survived the angle floor, including wrong ones, and
    # a handful of them dominate an RMS.  The distribution says which it is.
    relative_errors = reprojection_errors(recon, graph)
    stages["relative_reconstruction"] = {
        "frames_registered": len(recon.registered),
        "frames_total": len(captures),
        "points_triangulated": len(recon.points),
        "reprojection_px": receipts.distribution(relative_errors),
    }
    # Reject tracks that already contradict their calibrated cameras before
    # asking bundle adjustment to refine the block.  The mixed 24/48 mm Numabe
    # station set exposed the old order: 148 gross tracks, some thousands of
    # pixels wrong, consumed the whole iteration budget and were discarded only
    # afterwards.  The same hard 4 px criterion remains in force on both sides.
    dropped_before = filter_outlier_points(recon, graph)
    ba = bundle_adjust(recon, graph)
    after_errors = reprojection_errors(recon, graph)
    dropped = filter_outlier_points(recon, graph)
    stages["relative_bundle_adjustment"] = {
        **ba,
        "reprojection_px_after": receipts.distribution(after_errors),
        "points_dropped_before_ba": dropped_before,
        "points_dropped_after_ba": dropped,
        "outlier_rejection_threshold_px": MAX_RECONSTRUCTION_REPROJECTION_PX,
    }
    stages["global_stability_pruning"] = prune_for_global_solve(recon, graph)

    solution, alignment = initial_alignment(recon, by_index)
    stages["similarity_alignment"] = alignment

    # GPS and compass do not observe the global pitch/roll of a relative SfM
    # block.  Establish one shared gravity direction from repeated vertical
    # structures before reviewed map controls are allowed to set the remaining
    # horizontal datum.  A low reprojection residual is not a gravity proof.
    gravity = estimate_from_solution(
        solution,
        {index: capture.image_path for index, capture in by_index.items()},
        by_index,
        seed=seed,
    )
    solution, gravity_report = level_solution(solution, gravity)
    stages["gravity_alignment"] = gravity_report

    control_constraints = []
    if inputs.anchors_path is not None:
        anchor_set = load_anchor_set(str(inputs.anchors_path), frame)
        stages["osm_anchor_set"] = anchor_set.provenance()
        if inputs.control_points_path is not None:
            control_set = load_control_point_set(inputs.control_points_path)
            control_constraints, control_report = build_constraints(
                control_set,
                anchor_set,
                solution,
                {capture.capture_id: index for index, capture in by_index.items()},
                image_size={
                    index: (capture.width, capture.height)
                    for index, capture in by_index.items()
                },
            )
            if control_set.points and not control_constraints:
                raise ValueError(
                    "the control-point set declared correspondences but none formed a "
                    "constraint; refusing an apparently anchored solve with zero anchors"
                )
            stages["control_point_constraints"] = control_report
            solution, control_alignment = initial_control_alignment(
                solution, control_constraints
            )
            stages["control_point_alignment"] = control_alignment

    buildings = load_buildings(str(inputs.osm_path), frame)
    ground_z = float(np.median([c.enu[2] for c in captures])) - CAMERA_HEIGHT_PRIOR_M
    # "Absent from OSM" is a claim ABOUT THE SOURCE, so it is answered by
    # reading the source rather than by distance to the nearest building.  Every
    # tagging a vertical street pole could carry is counted across the whole
    # extract; a non-empty result would retract the claim outright.
    pole_like = has_class(str(inputs.osm_path), is_pole_like)
    stages["osm"] = {
        "path": str(inputs.osm_path),
        "buildings_loaded": len(buildings),
        "assumed_ground_z_enu_m": ground_z,
        "camera_height_prior_m": CAMERA_HEIGHT_PRIOR_M,
        "pole_like_features_in_extract": len(pole_like),
        "pole_like_examples": pole_like[:5],
        "absence_claim": (
            "no power=pole/tower/portal, man_made=utility_pole/mast/pole/street_lamp, "
            "highway=street_lamp or amenity=street_lamp exists anywhere in this extract"
            if not pole_like else
            "RETRACTED: pole-like features DO exist in this extract"
        ),
    }

    facade = select_facade_anchor(solution, graph, buildings, ground_z)
    anchors: list[AnchorConstraint] = []
    if facade is not None:
        anchors.append(AnchorConstraint(track_index=facade[0], wall=facade[1]))
    stages["facade_anchor_selection"] = facade[2] if facade else {"selected": False}

    images = {i: cv2.imread(c.image_path, cv2.IMREAD_GRAYSCALE) for i, c in by_index.items()}
    fixture_pre = select_unmapped_fixture(solution, graph, buildings, ground_z, images)

    keep = tuple(x for x in (
        facade[0] if facade else None,
        fixture_pre["track_index"] if fixture_pre else None,
    ) if x is not None)

    observations = [
        (f, t, graph.features[f].xy[kp])
        for t in sorted(solution.points)
        for f, kp in graph.tracks[t]
        if f in solution.centers
    ]
    # Reviewed controls can establish the datum for either calibrated or
    # free-focal blocks.  An automatically selected OSM anchor is sufficient
    # for the rigid datum solve only after intrinsics have already been fixed;
    # free-focal corridors still need the joint solve to estimate focal length
    # and its covariance.
    use_rigid_absolute_solve = bool(control_constraints) or (
        bool(anchors) and not solution.focal_free
    )
    if use_rigid_absolute_solve:
        solved = absolute_block_solve(
            solution, by_index, observations, anchors,
            control_points=control_constraints,
            keep_track_covariance=keep, max_iterations=500,
        )
        stages["absolute_registration_model"] = "gravity_leveled_rigid_block_yaw_translation"
    else:
        solved = global_solve(
            solution, by_index, observations, anchors,
            control_points=control_constraints,
            keep_track_covariance=keep, max_iterations=OPTIMIZER_MAX_EVALUATIONS,
        )
        stages["absolute_registration_model"] = "joint_camera_structure"

    per_observation, per_camera = _residual_tables(solved, graph, observations)
    stages["global_solve_residuals"] = {
        "observations": len(per_observation),
        "median_px": float(np.median(per_observation)) if per_observation else None,
    }

    candidates: list[dict] = []
    emitted: dict[str, Any] = {}

    if facade is not None:
        track_index, wall, facade_evidence = facade
        point = solved.points[track_index]
        obs = _track_observations(graph, solved, track_index)
        per_view: dict[str, Any] = {}
        votes: dict[str, int] = {}
        for f, uv in obs.items():
            miss = _wall_image_miss_px(solved, f, uv, wall.seg_a, wall.seg_b, float(point[2]))
            per_view[str(by_index[f].rank)] = miss
            if miss is None:
                key = "inconclusive_wall_not_imageable"
            elif miss <= FACADE_WALL_IMAGE_TOLERANCE_PX:
                key = "facade_region"
            else:
                key = "off_wall_image"
            votes[key] = votes.get(key, 0) + 1
        candidate = Candidate(
            candidate_id="facade_anchor",
            semantic_class="facade_region",
            track_index=track_index,
            position=point,
            observations=obs,
            semantic_votes=votes,
            expected_vote="facade_region",
            covariance=solved.point_covariance.get(track_index),
            notes="OSM-corroborated building facade point used as a georegistration anchor",
        )
        emitted["facade_anchor"] = _finish_candidate(
            candidate, solved, by_index, frame,
            extra={
                "osm_ref": wall.osm_ref,
                "osm_tags": wall.tags,
                "horizontal_distance_to_osm_wall_m": float(
                    nearest_wall(point[:2], buildings).distance_m
                ),
                "per_view_wall_image_miss_px": per_view,
                "per_view_tolerance_px": FACADE_WALL_IMAGE_TOLERANCE_PX,
                "selection_evidence": facade_evidence,
                "role": "absolute georegistration anchor (entered the global solve)",
            },
        )
        candidates.append(emitted["facade_anchor"]["record"])

    if fixture_pre is not None:
        track_index = fixture_pre["track_index"]
        point = solved.points[track_index]
        obs = _track_observations(graph, solved, track_index)
        candidate = Candidate(
            candidate_id="unmapped_fixture",
            semantic_class="fixture",
            track_index=track_index,
            position=point,
            observations=obs,
            semantic_votes=fixture_pre["semantic_votes"],
            expected_vote="fixture",
            covariance=solved.point_covariance.get(track_index),
            notes="vertical street fixture absent from the OSM extract for this area",
        )
        emitted["unmapped_fixture"] = _finish_candidate(
            candidate, solved, by_index, frame,
            extra={
                "osm_pole_like_features_in_extract": len(pole_like),
                "cluster_size": fixture_pre["cluster_size"],
                "cluster_vertical_span_m": fixture_pre["cluster_vertical_span_m"],
                "building_clearance_m": fixture_pre["building_clearance_m"],
                "per_view_edge_deviation_from_vertical_deg": {
                    str(by_index[f].rank): v
                    for f, v in fixture_pre["edge_deviation_deg"].items()
                },
                "role": "triangulated object absent from OSM",
            },
        )
        candidates.append(emitted["unmapped_fixture"]["record"])

    return {
        "frame": frame,
        "captures": by_index,
        "graph": graph,
        "solution": solved,
        "stages": stages,
        "per_observation_px": per_observation,
        "per_camera_px": per_camera,
        "candidates": candidates,
        "emitted": emitted,
        "buildings": buildings,
        "parameters": {
            "seed": seed,
            "match_window": match_window,
            "facade_coarse_gate_m": FACADE_COARSE_GATE_M,
            "facade_fine_gate_sigma_multiple": FACADE_FINE_GATE_SIGMA_MULTIPLE,
            "facade_fine_gate_floor_m": FACADE_FINE_GATE_FLOOR_M,
            "facade_min_support_points": FACADE_MIN_SUPPORT_POINTS,
            "facade_min_along_wall_m": FACADE_MIN_ALONG_WALL_M,
            "facade_max_plane_angle_deg": FACADE_MAX_PLANE_ANGLE_DEG,
            "fixture_min_building_clearance_m": FIXTURE_MIN_BUILDING_CLEARANCE_M,
            "fixture_cluster_radius_m": FIXTURE_CLUSTER_RADIUS_M,
            "fixture_min_vertical_span_m": FIXTURE_MIN_VERTICAL_SPAN_M,
            "vertical_edge_tolerance_deg": VERTICAL_EDGE_TOLERANCE_DEG,
            "facade_wall_image_tolerance_px": FACADE_WALL_IMAGE_TOLERANCE_PX,
            "facade_occlusion_radius_px": FACADE_OCCLUSION_RADIUS_PX,
            "facade_occlusion_depth_margin_m": FACADE_OCCLUSION_DEPTH_MARGIN_M,
            "camera_height_prior_m": CAMERA_HEIGHT_PRIOR_M,
        },
    }


def _finish_candidate(candidate: Candidate, solution: GeoSolution,
                      captures: dict[int, Capture], frame: EnuFrame,
                      extra: dict) -> dict:
    from .georegister import relative_point_covariance
    from .triangulate import covariance_record

    rows = reproject_into_all_frames(solution, candidate, captures)
    matched = [r["residual_px"] for r in rows if r.get("status") == "matched"]
    angle = viewpoint_angle(solution, candidate)
    decision = gates.evaluate(
        viewpoints=candidate.viewpoints,
        triangulation_angle_deg=angle,
        reprojection_px=matched,
        covariance=candidate.covariance,
        semantic_votes=candidate.semantic_votes,
        expected_class=candidate.expected_vote,
    )
    lon, lat, alt = frame.to_geodetic(*candidate.position)
    record = {
        "candidate_id": candidate.candidate_id,
        "semantic_class": candidate.semantic_class,
        "notes": candidate.notes,
        "position_enu_m": [float(v) for v in candidate.position],
        "position_wgs84": {"lon": lon, "lat": lat, "ellipsoidal_height_m": alt},
        "viewpoints": candidate.viewpoints,
        "triangulation_angle_deg": angle,
        "semantic_votes": candidate.semantic_votes,
        "covariance_absolute": covariance_record(candidate.covariance),
        "covariance_relative_to_camera_track": covariance_record(
            relative_point_covariance(solution, candidate.track_index)
        ),
        "reprojection": summarize(rows),
        "reprojection_per_frame": rows,
        "gate": decision.as_record(),
        **extra,
    }
    return {"candidate": candidate, "record": record, "decision": decision, "rows": rows}


def _residual_tables(solution: GeoSolution, graph, observations):
    per_observation: list[float] = []
    per_camera: dict[int, list[float]] = {}
    for f, t, uv in observations:
        proj, depth = solution.project(f, solution.points[t])
        if depth <= 1e-9 or not np.all(np.isfinite(proj)):
            continue
        err = float(np.hypot(proj[0] - uv[0], proj[1] - uv[1]))
        per_observation.append(err)
        per_camera.setdefault(f, []).append(err)
    return per_observation, per_camera


def _histogram(values: list[int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = str(v) if v < 6 else "6+"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))
