"""FL-4812: widened OSM anchor kinds and reviewed control points.

WHAT THESE TESTS ARE ABOUT
==========================
The camera solve could only register against OSM ways carrying a ``building``
tag.  At Numabe the station entrance is not traced as a building, the nearest
facade is 7.46 m away, and the consequence is arithmetic: zero buildings in
range means zero anchors, which means the block's absolute yaw is unpinned and
comes out about 147 deg wrong with the four per-camera residuals agreeing to
4.51 deg - a consistent rotation of a self-consistent block.

``world_ir_ingest_osm_extract.py --anchors-out`` already emits 765 Numabe
anchors across five populated kinds.  The tests below cover the two things that
were missing: consuming that anchor set with each kind modelled as the geometry
the map actually asserts, and accepting a REVIEWED image-to-map correspondence
as weighted, inspectable evidence rather than as a silent correction.

The round-trip at the bottom is the one that matters.  It plants a known yaw
error in a synthetic block, shows the solve CANNOT remove it without controls,
and shows that reviewed controls recover the known poses.  Everything before it
exists so that a failure in the round-trip can be localised.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
from dataclasses import replace

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.camera_solve.capture import (  # noqa: E402
    INTRINSICS_PRIOR,
    Capture,
    IntrinsicsPrior,
)
from scripts.pipeline.camera_solve.control_points import (  # noqa: E402
    CONTROL_POINT_SET_VERSION,
    REVIEW_STATE_REVIEWED,
    REVIEW_STATE_SYNTHETIC,
    REVIEW_STATE_UNREVIEWED,
    ControlPoint,
    ControlPointError,
    build_constraints,
    load_control_point_set,
    write_control_point_set,
)
from scripts.pipeline.camera_solve.geodesy import EnuFrame, angle_delta_deg  # noqa: E402
from scripts.pipeline.camera_solve.georegister import (
    _resolve_track_reversal,  # noqa: E402
    AnchorConstraint,
    ControlPointConstraint,
    GeoSolution,
    WorldIRAnchorConstraint,
    absolute_block_solve,
    constraint_residual_report,
    control_point_sigma_deg,
    global_solve,
    initial_control_alignment,
)
from scripts.pipeline.camera_solve.osm_anchors import (  # noqa: E402
    ANCHOR_CURVE_KINDS,
    ANCHOR_POINT_KINDS,
    AnchorSet,
    WorldIRAnchor,
    anchors_within,
    associate_anchors,
    load_anchor_set,
    nearest_anchors,
)

FRAME = EnuFrame(origin_lon=139.673, origin_lat=35.5828, origin_alt=0.0)


# ==========================================================================
# anchor set: every declared kind is readable, and keeps its own geometry
# ==========================================================================


def _anchor_doc(anchors: list[dict], sigma: float = 0.6666666666666666) -> dict:
    return {
        "anchor_set_version": "y92.osm_anchor_set/1.0.0",
        "anchor_set_id": "test_corridor.osm_anchors",
        "world_ir_id": "test_corridor",
        "position_uncertainty": {"model": "isotropic_sigma", "sigma": sigma,
                                 "units": "metre"},
        "anchors": anchors,
    }


def _anchor_record(anchor_id: str, kind: str, east: float, north: float,
                   owner: str = "feature:x:way/1", inside: bool = True) -> dict:
    # Built through the frame's own exact inverse rather than a flat-earth
    # approximation, so a failure here is the loader's and not the fixture's.
    lon, lat, _ = FRAME.to_geodetic(east, north, 0.0)
    return {
        "anchor_id": anchor_id,
        "anchor_type": kind,
        "osm_ref": anchor_id.rsplit(":", 1)[-1],
        "epsg4326": [lon, lat],
        "inside_aoi": inside,
        "owner_feature_ids": [owner],
        "reason": "synthetic",
        "uncertainty": {"model": "isotropic_sigma", "sigma": 0.6666666666666666,
                        "units": "metre"},
    }


def _write_anchor_doc(tmp_path: Path, anchors: list[dict]) -> Path:
    path = tmp_path / "anchors.json"
    path.write_text(json.dumps(_anchor_doc(anchors)), encoding="utf-8")
    return path


def test_all_five_populated_kinds_load_not_just_buildings(tmp_path):
    """The bug was that four of the five kinds had no way into the solve."""
    records = [
        _anchor_record("anchor:building_corner:node/1", "building_corner", 10.0, 0.0),
        _anchor_record("anchor:station_entrance:node/2", "station_entrance", 0.0, 12.0),
        _anchor_record("anchor:road_intersection:node/3", "road_intersection", -20.0, 5.0),
        _anchor_record("anchor:crossing_corner:node/4", "crossing_corner", 5.0, -18.0),
        _anchor_record("anchor:railway_vertex:node/5", "railway_vertex", 30.0, 0.0,
                       owner="feature:railway_boundary:way/9"),
        _anchor_record("anchor:railway_vertex:node/6", "railway_vertex", 30.0, 10.0,
                       owner="feature:railway_boundary:way/9"),
        _anchor_record("anchor:railway_vertex:node/7", "railway_vertex", 30.0, 20.0,
                       owner="feature:railway_boundary:way/9"),
    ]
    loaded = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME)
    assert len(loaded) == 7
    assert loaded.counts_by_kind == {
        "building_corner": 1, "crossing_corner": 1, "railway_vertex": 3,
        "road_intersection": 1, "station_entrance": 1,
    }
    assert set(ANCHOR_POINT_KINDS) >= {
        "building_corner", "crossing_corner", "road_intersection",
        "station_entrance", "wall_fence_endpoint",
    }
    assert ANCHOR_CURVE_KINDS == ("railway_vertex",)


def test_anchor_positions_round_trip_into_enu_metres(tmp_path):
    records = [_anchor_record("anchor:building_corner:node/1", "building_corner",
                              25.0, -40.0)]
    loaded = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME)
    assert loaded.anchors[0].en == pytest.approx([25.0, -40.0], abs=0.05)


def test_a_position_anchor_constrains_two_directions(tmp_path):
    records = [_anchor_record("anchor:station_entrance:node/2", "station_entrance",
                              0.0, 0.0)]
    anchor = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME).anchors[0]
    assert anchor.geometry_kind == "point"
    assert anchor.residual_rows == 2
    assert anchor.offset(np.array([3.0, -4.0])) == pytest.approx([3.0, -4.0], abs=0.05)
    assert anchor.miss_m(np.array([3.0, -4.0])) == pytest.approx(5.0, abs=0.05)


def test_a_rail_vertex_constrains_only_across_the_track(tmp_path):
    """The map states the CURVE, not which vertex a sleeper is.

    A point 25 m along the rail from the nearest digitised vertex is not 25 m
    wrong; it is on the rail.  A point 2 m to the side of it is 2 m wrong.  A
    2-D residual would invent an along-track position the source never claimed.
    """
    records = [
        _anchor_record(f"anchor:railway_vertex:node/{i}", "railway_vertex",
                       0.0, 10.0 * i, owner="feature:railway_boundary:way/9")
        for i in range(5)
    ]
    anchors = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME).anchors
    rail = [a for a in anchors if a.anchor_id.endswith("node/2")][0]
    assert rail.geometry_kind == "point_on_curve"
    assert rail.residual_rows == 1
    # tangent runs north; along-track displacement is free
    assert abs(float(rail.tangent_en[1])) == pytest.approx(1.0, abs=0.02)
    assert rail.miss_m(np.array([0.0, 20.0 + 25.0])) == pytest.approx(0.0, abs=0.05)
    assert rail.miss_m(np.array([2.0, 20.0])) == pytest.approx(2.0, abs=0.05)


def test_a_rail_vertex_with_no_neighbour_is_dropped_and_counted(tmp_path):
    """An undirected vertex is not quietly demoted to a position anchor."""
    records = [_anchor_record("anchor:railway_vertex:node/5", "railway_vertex",
                              0.0, 0.0, owner="feature:railway_boundary:way/9")]
    loaded = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME)
    assert len(loaded) == 0
    assert sum(loaded.dropped.values()) == 1
    assert "tangent" in " ".join(loaded.dropped)


def test_the_anchor_sets_own_sigma_is_used_not_the_wall_assumption(tmp_path):
    records = [_anchor_record("anchor:building_corner:node/1", "building_corner", 0.0, 0.0)]
    loaded = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME)
    assert loaded.anchors[0].sigma_m == pytest.approx(0.6667, abs=1e-3)
    provenance = loaded.provenance()
    # Both numbers are reported so the disagreement between the two owners is
    # visible rather than averaged away.
    assert provenance["declared_position_sigma_m"] == pytest.approx(0.6667, abs=1e-3)
    assert provenance["wall_path_assumed_sigma_m"] == 1.5


def test_kinds_can_be_restricted_and_the_restriction_is_reported(tmp_path):
    records = [
        _anchor_record("anchor:building_corner:node/1", "building_corner", 0.0, 0.0),
        _anchor_record("anchor:crossing_corner:node/4", "crossing_corner", 5.0, 0.0),
    ]
    loaded = load_anchor_set(str(_write_anchor_doc(tmp_path, records)), FRAME,
                             kinds=("building_corner",))
    assert loaded.counts_by_kind == {"building_corner": 1}


# ==========================================================================
# association: a correspondence that could have been its neighbour is not one
# ==========================================================================


def _set_of(anchors: list[WorldIRAnchor]) -> AnchorSet:
    counts: dict[str, int] = {}
    for a in anchors:
        counts[a.anchor_type] = counts.get(a.anchor_type, 0) + 1
    return AnchorSet(anchor_set_id="test_corridor.osm_anchors",
                     anchor_set_version="y92.osm_anchor_set/1.0.0",
                     world_ir_id="test_corridor", source_path="<memory>",
                     declared_sigma_m=0.6666666666666666, anchors=anchors,
                     counts_by_kind=counts, dropped={})


def _point_anchor(name: str, east: float, north: float,
                  kind: str = "building_corner") -> WorldIRAnchor:
    return WorldIRAnchor(anchor_id=name, anchor_type=kind, osm_ref=name,
                         en=np.array([east, north]), sigma_m=0.6666666666666666,
                         inside_aoi=True, reason="synthetic")


def test_nearest_anchor_names_its_runner_up():
    anchors = [_point_anchor("a", 0.0, 0.0), _point_anchor("b", 3.0, 0.0)]
    best, best_d, second, second_d = nearest_anchors(np.array([0.5, 0.0]), anchors)
    assert best.anchor_id == "a" and best_d == pytest.approx(0.5)
    assert second.anchor_id == "b" and second_d == pytest.approx(2.5)


def test_an_ambiguous_correspondence_is_refused_and_counted():
    """678 corners in a 200 m box: the second-nearest is usually just as close."""
    anchors = [_point_anchor("a", 0.0, 0.0), _point_anchor("b", 1.0, 0.0)]
    points = {0: np.array([0.5, 0.0, 2.0])}  # equidistant: a coin toss
    matches, report = associate_anchors(points, anchors, gate_m=2.0)
    assert matches == []
    assert report["points_inside_gate"] == 1
    assert report["points_rejected_ambiguous"] == 1


def test_an_unambiguous_correspondence_is_kept_with_its_evidence():
    anchors = [_point_anchor("a", 0.0, 0.0), _point_anchor("b", 40.0, 0.0)]
    points = {7: np.array([0.3, 0.0, 2.0])}
    matches, report = associate_anchors(points, anchors, gate_m=2.0)
    assert [m.anchor.anchor_id for m in matches] == ["a"]
    assert matches[0].track_index == 7
    assert matches[0].ambiguity_ratio > 100.0
    assert report["matches_by_kind"] == {"building_corner": 1}


def test_association_removes_the_blocks_shift_rather_than_widening_the_gate():
    anchors = [_point_anchor("a", 0.0, 0.0), _point_anchor("b", 40.0, 0.0)]
    points = {0: np.array([6.2, -3.1, 2.0])}
    assert associate_anchors(points, anchors, gate_m=1.0)[0] == []
    matches, _ = associate_anchors(points, anchors, gate_m=1.0,
                                   offset_en=np.array([6.0, -3.0]))
    assert [m.anchor.anchor_id for m in matches] == ["a"]


def test_a_census_separates_nothing_in_range_from_nothing_formed():
    anchors = [_point_anchor("a", 0.0, 0.0),
               _point_anchor("c", 4.0, 0.0, kind="crossing_corner")]
    assert anchors_within(np.array([0.0, 0.0]), anchors, 1.0) == {"building_corner": 1}
    assert anchors_within(np.array([0.0, 0.0]), anchors, 10.0) == {
        "building_corner": 1, "crossing_corner": 1}
    assert anchors_within(np.array([500.0, 0.0]), anchors, 10.0) == {}


# ==========================================================================
# control point documents
# ==========================================================================


def _write_controls(tmp_path: Path, points: list[ControlPoint], *,
                    state: str = REVIEW_STATE_REVIEWED,
                    reviewer: str = "operator", anchor_set_id: str = "test_corridor.osm_anchors",
                    ) -> Path:
    path = tmp_path / "controls.json"
    write_control_point_set(
        path, set_id="test.controls", generated_at="2026-09-12T00:00:00Z",
        anchor_set_id=anchor_set_id, review_state=state, reviewer=reviewer,
        review_method="visual identification in the source frame",
        review_tool="pytest", points=points,
    )
    return path


def _cp(control_id: str, capture_id: str, anchor_id: str, u: float, v: float,
        sigma: float = 3.0) -> ControlPoint:
    return ControlPoint(control_id=control_id, capture_id=capture_id,
                        pixel=(u, v), anchor_id=anchor_id, pixel_sigma_px=sigma,
                        reviewer="operator", reviewed_at="2026-09-12T00:00:00Z",
                        review_method="visual identification")


def test_a_control_point_set_round_trips_byte_stably(tmp_path):
    points = [_cp("cp-2", "img-b", "a", 10.0, 20.0), _cp("cp-1", "img-a", "a", 1.0, 2.0)]
    first = _write_controls(tmp_path, points)
    digest_a = (tmp_path / "controls.json").read_bytes()
    _write_controls(tmp_path, list(reversed(points)))
    assert (tmp_path / "controls.json").read_bytes() == digest_a
    loaded = load_control_point_set(first)
    assert [p.control_id for p in loaded.points] == ["cp-1", "cp-2"]
    assert loaded.version == CONTROL_POINT_SET_VERSION
    assert loaded.digest == __import__("hashlib").sha256(digest_a).hexdigest()


def test_provenance_carries_reviewer_method_and_digest(tmp_path):
    path = _write_controls(tmp_path, [_cp("cp-1", "img-a", "a", 1.0, 2.0)])
    provenance = load_control_point_set(path).provenance()
    assert provenance["reviewer"] == "operator"
    assert provenance["review_state"] == REVIEW_STATE_REVIEWED
    assert provenance["review_method"]
    assert len(provenance["sha256"]) == 64
    assert provenance["control_points_declared"] == 1


def test_an_unreviewed_set_loads_but_says_so(tmp_path):
    path = _write_controls(tmp_path, [_cp("cp-1", "img-a", "a", 1.0, 2.0)],
                           state=REVIEW_STATE_UNREVIEWED, reviewer="")
    loaded = load_control_point_set(path)
    assert not loaded.is_reviewed
    assert "NOT REVIEWED" in loaded.provenance()["review_state_note"]


def test_a_review_with_no_reviewer_is_refused(tmp_path):
    path = _write_controls(tmp_path, [_cp("cp-1", "img-a", "a", 1.0, 2.0)],
                           state=REVIEW_STATE_REVIEWED, reviewer="")
    with pytest.raises(ControlPointError, match="not a review"):
        load_control_point_set(path)


def test_a_zero_sigma_is_refused_because_it_is_an_override(tmp_path):
    path = _write_controls(tmp_path, [_cp("cp-1", "img-a", "a", 1.0, 2.0, sigma=0.0)])
    with pytest.raises(ControlPointError, match="override"):
        load_control_point_set(path)


def test_two_correspondences_for_one_image_and_anchor_are_refused(tmp_path):
    path = _write_controls(tmp_path, [_cp("cp-1", "img-a", "a", 1.0, 2.0),
                                      _cp("cp-2", "img-a", "a", 90.0, 80.0)])
    with pytest.raises(ControlPointError, match="one image sees one anchor once"):
        load_control_point_set(path)


def test_an_unknown_version_is_refused(tmp_path):
    path = tmp_path / "controls.json"
    path.write_text(json.dumps({"control_point_set_version": "y92.control_point_set/9.9.9"}))
    with pytest.raises(ControlPointError, match="this reader implements"):
        load_control_point_set(path)


def test_controls_reviewed_against_a_different_anchor_set_are_refused(tmp_path,
                                                                     synthetic_scene):
    path = _write_controls(tmp_path, [_cp("cp-1", "cap-0", "a", 1.0, 2.0)],
                           anchor_set_id="some_other_corridor.osm_anchors")
    scene = synthetic_scene
    with pytest.raises(ControlPointError, match="only meaningful inside the set"):
        build_constraints(load_control_point_set(path), _set_of(scene["anchors"]),
                          scene["truth"], scene["frame_of_capture"])


def test_sigma_combines_the_pixel_and_the_map_error_in_quadrature():
    pixel_only = control_point_sigma_deg(3.0, 1500.0, 0.0, 50.0)
    assert pixel_only == pytest.approx(math.degrees(3.0 / 1500.0), rel=1e-9)
    both = control_point_sigma_deg(3.0, 1500.0, 0.667, 50.0)
    assert both > pixel_only
    # A distant anchor is a better bearing reference than a near one.
    assert control_point_sigma_deg(3.0, 1500.0, 0.667, 200.0) < both


# ==========================================================================
# synthetic round trip: known poses -> correspondences -> recovered poses
# ==========================================================================
# The plant is a rigid YAW ROTATION of the whole block, which is the exact
# failure mode observed at Numabe: relative orientation self-consistent, the
# block's absolute heading wrong by one common angle.  No image measurement can
# see it, so the test first CONFIRMS the solve cannot remove it, and only then
# shows that reviewed controls do.

TRUE_YAW_ERROR_DEG = 22.0


def _capture(index: int, enu, *, focal_px: float, width: int, height: int,
             horizontal_accuracy_m: float) -> Capture:
    return Capture(
        capture_id=f"cap-{index}", rank=index, image_path="", width=width, height=height,
        lon=0.0, lat=0.0, alt=0.0,
        # The compass is deliberately absent.  If it were present it would pin
        # the yaw on its own and the test would prove nothing about controls.
        azimuth_deg=None,
        horizontal_accuracy_m=horizontal_accuracy_m,
        intrinsics=IntrinsicsPrior(focal_px=focal_px, sigma_px=40.0,
                                   state=INTRINSICS_PRIOR, basis="synthetic"),
        licence="", producer="", captured_at="", attribution="", source_url="",
        enu=tuple(float(v) for v in enu),
    )


def _look_at(center: np.ndarray, target: np.ndarray) -> np.ndarray:
    """World-to-camera rotation for a level camera at ``center`` facing ``target``."""
    forward = target - center
    forward = forward / np.linalg.norm(forward)
    world_up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, world_up)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    return np.vstack([right, down, forward])


def _yaw(theta_deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


@pytest.fixture(scope="module")
def synthetic_scene():
    """A small, fully known block: 6 cameras, 60 points, 5 anchors, no noise."""
    rng = np.random.default_rng(4812)
    focal_px, width, height = 1500.0, 2048, 1536
    cx, cy = width / 2.0, height / 2.0

    centers = {i: np.array([-12.0 + 4.0 * i, 0.0, 1.55]) for i in range(6)}
    # Structure on a facade 14 m north of the track, plus some on the far side,
    # so the reconstruction is not degenerate.
    points = {}
    for t in range(60):
        east = rng.uniform(-20.0, 20.0)
        north = 14.0 if t % 3 else -11.0
        points[t] = np.array([east, north + rng.uniform(-0.6, 0.6),
                              rng.uniform(0.5, 9.0)])

    rvecs = {}
    for i, c in centers.items():
        R = _look_at(c, np.array([c[0] + rng.uniform(-2, 2), 14.0, 2.5]))
        rvecs[i] = cv2.Rodrigues(R)[0].ravel()

    truth = GeoSolution(frames=list(centers), rvecs=rvecs, centers=centers,
                        points=points, focal_px=focal_px, cx=cx, cy=cy,
                        focal_free=True)

    observations = []
    for i in centers:
        for t, X in points.items():
            uv, depth = truth.project(i, X)
            if depth > 0.5 and 0.0 <= uv[0] < width and 0.0 <= uv[1] < height:
                observations.append((i, t, uv))

    # Five mapped features the reviewer can name, spread along the facade.
    anchors = [
        _point_anchor("anchor:building_corner:node/1", -18.0, 14.0),
        _point_anchor("anchor:building_corner:node/2", -6.0, 14.0),
        _point_anchor("anchor:station_entrance:node/3", 2.0, 14.0, "station_entrance"),
        _point_anchor("anchor:building_corner:node/4", 12.0, 14.0),
        _point_anchor("anchor:crossing_corner:node/5", 0.0, -11.0, "crossing_corner"),
    ]

    # Synthesize the reviewed pixels from the TRUE poses, clicking each anchor
    # at an arbitrary height.  The height is arbitrary on purpose: a bearing
    # residual must not depend on it, and a reviewer clicking a building corner
    # really does click somewhere unmeasured along a vertical edge.
    controls: list[ControlPoint] = []
    n = 0
    for i in centers:
        for anchor in anchors:
            click_z = float(rng.uniform(0.5, 8.0))
            world = np.array([anchor.en[0], anchor.en[1], click_z])
            uv, depth = truth.project(i, world)
            if depth <= 0.5 or not (0.0 <= uv[0] < width and 0.0 <= uv[1] < height):
                continue
            n += 1
            controls.append(ControlPoint(
                control_id=f"cp-{n:03d}", capture_id=f"cap-{i}",
                pixel=(float(uv[0]), float(uv[1])), anchor_id=anchor.anchor_id,
                pixel_sigma_px=3.0, reviewer="operator",
                reviewed_at="2026-09-12T00:00:00Z",
                review_method="visual identification in the source frame",
                note=f"clicked at {click_z:.2f} m above ground on the vertical edge",
            ))

    # GPS loose enough that a 22 deg block rotation is not resolved by position
    # alone - which is the situation at Numabe, where the GPS residual median is
    # 5.58 m and the cameras are demonstrably not snapped to it.
    captures = {i: _capture(i, centers[i], focal_px=focal_px, width=width,
                            height=height, horizontal_accuracy_m=8.0)
                for i in centers}

    # THE PLANT: rotate cameras and structure together about the block centroid.
    pivot = np.mean([centers[i] for i in centers], axis=0)
    M = _yaw(TRUE_YAW_ERROR_DEG)
    spun = GeoSolution(
        frames=list(centers),
        rvecs={i: cv2.Rodrigues(cv2.Rodrigues(rvecs[i])[0] @ M.T)[0].ravel()
               for i in centers},
        centers={i: M @ (centers[i] - pivot) + pivot for i in centers},
        points={t: M @ (X - pivot) + pivot for t, X in points.items()},
        focal_px=focal_px, cx=cx, cy=cy, focal_free=True,
    )
    return {
        "truth": truth, "spun": spun, "captures": captures,
        "observations": observations, "anchors": anchors, "controls": controls,
        "frame_of_capture": {f"cap-{i}": i for i in centers},
        "image_size": {i: (width, height) for i in centers},
        "width": width, "height": height, "focal_px": focal_px,
    }


def _yaw_error_deg(solved: GeoSolution, truth: GeoSolution) -> list[float]:
    return [angle_delta_deg(solved.azimuth_deg(i), truth.azimuth_deg(i))
            for i in truth.frames]


def test_the_planted_block_rotation_is_invisible_to_the_imagery(synthetic_scene):
    """Every reprojection is still perfect after the block is spun.

    This is the premise of the whole exercise: the images cannot see the error,
    so no amount of bundle adjustment can remove it.
    """
    scene = synthetic_scene
    errors = []
    for i, t, uv in scene["observations"]:
        projected, depth = scene["spun"].project(i, scene["spun"].points[t])
        assert depth > 0.0
        errors.append(float(np.hypot(*(projected - uv))))
    assert max(errors) < 1e-6
    assert max(abs(e) for e in _yaw_error_deg(scene["spun"], scene["truth"])) == \
        pytest.approx(TRUE_YAW_ERROR_DEG, abs=0.01)


def test_the_initialiser_does_nothing_without_controls(synthetic_scene):
    """No controls, no motion.  It cannot invent a datum it was not given."""
    started, report = initial_control_alignment(synthetic_scene["spun"], [])
    assert report["applied"] is False
    assert started is synthetic_scene["spun"]


def test_the_initialiser_moves_the_block_rigidly(synthetic_scene):
    """Every relative geometry survives, so no reprojection can change."""
    scene = synthetic_scene
    constraints = [
        ControlPointConstraint(control_id=c.control_id,
                               frame=scene["frame_of_capture"][c.capture_id],
                               uv=np.array(c.pixel), sigma_deg=1.0,
                               anchor={a.anchor_id: a for a in scene["anchors"]}[c.anchor_id])
        for c in scene["controls"]
    ]
    started, _ = initial_control_alignment(scene["spun"], constraints)
    for i, t, uv in scene["observations"]:
        projected, depth = started.project(i, started.points[t])
        assert depth > 0.0
        assert float(np.hypot(*(projected - uv))) < 1e-6


def test_without_controls_or_compass_a_track_reversal_is_unrecoverable(synthetic_scene):
    """The negative control, planting the ONE rotation this geometry cannot see.

    An earlier version planted 22 degrees and asserted the solve could not undo
    it. That was never the real degeneracy: rotating a 20 m straight track by 22
    degrees moves its end cameras several metres off their GPS fixes, so GPS can
    and does pull it back. The earlier test only passed because georegister.py
    was writing GPS prior rows at stride 4 inside a stride-5 block, scrambling
    them -- it was asserting a bug.

    The genuinely unobservable rotation is 180 degrees: a straight track maps
    onto itself, every camera lands back on its own GPS fix, and only the
    compass can tell "walking east looking north" from "walking west looking
    south". With the compass stripped, nothing in the solve can recover it, and
    that is what makes the control-point round trip below meaningful.
    """
    scene = synthetic_scene
    pivot = np.mean([scene["truth"].centers[i] for i in scene["truth"].frames], axis=0)
    M = _yaw(180.0)
    reversed_block = GeoSolution(
        frames=list(scene["truth"].frames),
        rvecs={i: cv2.Rodrigues(cv2.Rodrigues(scene["truth"].rvecs[i])[0] @ M.T)[0].ravel()
               for i in scene["truth"].frames},
        centers={i: M @ (scene["truth"].centers[i] - pivot) + pivot
                 for i in scene["truth"].frames},
        points={t: M @ (X - pivot) + pivot for t, X in scene["truth"].points.items()},
        focal_px=scene["focal_px"], cx=scene["truth"].cx, cy=scene["truth"].cy,
        focal_free=True,
    )
    blind = {f: replace(cap, azimuth_deg=None) for f, cap in scene["captures"].items()}
    solved = global_solve(reversed_block, blind, scene["observations"], [], max_iterations=800)
    residual = _yaw_error_deg(solved, scene["truth"])
    assert np.median(np.abs(residual)) > 90.0, (
        "a 180 degree track reversal was recovered without a compass or a control "
        "point; then this fixture no longer isolates the unobservable direction"
    )
    assert solved.report["control_point_constraints"] == 0


def test_the_compass_branch_check_resolves_a_track_reversal(synthetic_scene):
    """The complement: with the compass present, the reversal IS resolved.

    This is the regression guard for two defects at once. If the GPS rows ever
    overwrite the azimuth rows again (the stride-4/stride-5 collision), or if
    the track-reversal branch check in initial_alignment is removed, the compass
    stops reaching the solve on the one rotation GPS cannot see, and this fails.
    """
    scene = synthetic_scene
    pivot = np.mean([scene["truth"].centers[i] for i in scene["truth"].frames], axis=0)
    M = _yaw(180.0)
    reversed_rvecs = {i: cv2.Rodrigues(cv2.Rodrigues(scene["truth"].rvecs[i])[0] @ M.T)[0].ravel()
                      for i in scene["truth"].frames}
    reversed_centers = {i: M @ (scene["truth"].centers[i] - pivot) + pivot
                        for i in scene["truth"].frames}
    reversed_points = {t: M @ (X - pivot) + pivot for t, X in scene["truth"].points.items()}

    # The base fixture's captures carry NO compass -- which is itself why the
    # earlier negative control was really testing GPS, not the compass. Give
    # them the true bearings here, because the compass is the only evidence that
    # can separate the two reversal branches.
    with_compass = {
        f: replace(cap, azimuth_deg=scene["truth"].azimuth_deg(f))
        for f, cap in scene["captures"].items()
    }
    report = _resolve_track_reversal(reversed_rvecs, reversed_centers, reversed_points,
                                     list(scene["truth"].frames), with_compass)
    assert report["applied"] is True, report
    assert report["flipped_branch_azimuth_median_deg"] < 1.0
    assert report["kept_branch_azimuth_median_deg"] > 170.0


def test_with_the_compass_alone_the_solve_recovers_a_small_spin(synthetic_scene):
    """The complement: a compass prior inside Huber's quadratic regime works.

    This is the regression guard for the stride-4/stride-5 row collision. If the
    GPS rows ever overwrite the azimuth rows again, this test fails, because the
    compass will stop reaching the solver.
    """
    scene = synthetic_scene
    solved = global_solve(scene["spun"], scene["captures"], scene["observations"],
                          [], max_iterations=800)
    residual = _yaw_error_deg(solved, scene["truth"])
    assert np.median(np.abs(residual)) < 1.0, (
        "the compass prior did not recover a 1.5-sigma spin; the most likely "
        "cause is that the azimuth residual rows are being overwritten again"
    )


def test_reviewed_controls_recover_the_known_poses(tmp_path, synthetic_scene):
    """THE ROUND TRIP: known poses -> correspondences -> recovered poses."""
    scene = synthetic_scene
    path = _write_controls(tmp_path, scene["controls"], state=REVIEW_STATE_SYNTHETIC,
                           reviewer="")
    control_set = load_control_point_set(path)
    assert control_set.review_state == REVIEW_STATE_SYNTHETIC
    anchor_set = _set_of(scene["anchors"])

    constraints, report = build_constraints(
        control_set, anchor_set, scene["spun"], scene["frame_of_capture"],
        image_size=scene["image_size"],
    )
    assert report["refused_total"] == 0
    assert report["constraints_formed"] == len(scene["controls"])
    assert report["frames_with_at_least_one_control"] == 6

    started, alignment = initial_control_alignment(scene["spun"], constraints)
    # The initialiser is ONE rigid motion of the whole block; it must recover
    # the planted rotation and must not have touched anything relative.
    assert alignment["applied"]
    assert alignment["block_yaw_rotation_deg"] == pytest.approx(-TRUE_YAW_ERROR_DEG,
                                                               abs=0.5)
    assert alignment["bearing_residual_deg_before"]["median_abs"] > 15.0
    assert alignment["bearing_residual_deg_after"]["median_abs"] < 1.0

    solved = global_solve(started, scene["captures"], scene["observations"],
                          [], control_points=constraints, max_iterations=800)

    yaw = _yaw_error_deg(solved, scene["truth"])
    assert max(abs(v) for v in yaw) < 1.0, f"yaw not recovered: {yaw}"
    for i in scene["truth"].frames:
        assert np.linalg.norm(solved.centers[i] - scene["truth"].centers[i]) < 1.0
    assert solved.report["control_point_constraints"] == len(constraints)
    assert solved.report["control_point_rows"] == len(constraints)


def test_reviewed_controls_can_register_the_converged_block_rigidly(
        tmp_path, synthetic_scene):
    """The production control path sets the datum without reopening SfM."""
    scene = synthetic_scene
    path = _write_controls(tmp_path, scene["controls"], state=REVIEW_STATE_SYNTHETIC,
                           reviewer="")
    constraints, _ = build_constraints(
        load_control_point_set(path), _set_of(scene["anchors"]), scene["spun"],
        scene["frame_of_capture"], image_size=scene["image_size"],
    )
    started, _ = initial_control_alignment(scene["spun"], constraints)
    before = [
        started.project(frame, started.points[track])[0]
        for frame, track, _ in scene["observations"]
    ]
    solved = absolute_block_solve(
        started, scene["captures"], scene["observations"], [],
        control_points=constraints, max_iterations=100,
    )

    assert solved.report["absolute_model"] == "rigid_block_yaw_translation"
    assert solved.report["optimizer_status"] in {1, 2, 3, 4}
    assert solved.report["parameters"] == 4
    assert solved.report["reprojection_invariance"]["max_projected_pixel_delta"] < 1e-7
    for expected, (frame, track, _) in zip(before, scene["observations"]):
        actual, _ = solved.project(frame, solved.points[track])
        assert np.linalg.norm(actual - expected) < 1e-7
    for frame in solved.frames:
        assert np.all(np.isfinite(solved.center_covariance[frame]))
        assert np.all(np.isfinite(solved.rotation_covariance[frame]))
    assert solved.report["constraint_residuals"]["control_point_residual_sigmas"]["max"] < 2.0


def test_the_receipt_shows_every_control_point_residual(tmp_path, synthetic_scene):
    """Evidence that is not shown beside the other residuals is an override."""
    scene = synthetic_scene
    path = _write_controls(tmp_path, scene["controls"], state=REVIEW_STATE_SYNTHETIC,
                           reviewer="")
    constraints, _ = build_constraints(
        load_control_point_set(path), _set_of(scene["anchors"]), scene["spun"],
        scene["frame_of_capture"], image_size=scene["image_size"],
    )
    started, _ = initial_control_alignment(scene["spun"], constraints)
    solved = global_solve(started, scene["captures"], scene["observations"],
                          [], control_points=constraints, max_iterations=800)
    residuals = solved.report["constraint_residuals"]
    rows = residuals["control_points"]
    assert len(rows) == len(constraints)
    for row in rows:
        assert row["residual_deg"] is not None
        assert row["residual_sigmas"] is not None
        assert row["cross_track_miss_m"] is not None
        assert row["anchor_id"] and row["control_id"]
        assert row["sigma_deg"] > 0.0
    assert residuals["control_point_residual_deg"]["count"] == len(constraints)
    assert residuals["control_point_residual_deg"]["median"] < 1.0


def test_a_bearing_does_not_depend_on_the_height_the_reviewer_clicked(synthetic_scene):
    """A correspondence at 1 m and one at 8 m on the same vertical edge agree."""
    scene = synthetic_scene
    truth = scene["truth"]
    anchor = scene["anchors"][1]
    residuals = []
    for height in (0.6, 3.0, 8.0):
        uv, depth = truth.project(0, np.array([anchor.en[0], anchor.en[1], height]))
        assert depth > 0.0
        control = ControlPointConstraint(
            control_id=f"h{height}", frame=0, uv=uv, anchor=anchor, sigma_deg=1.0,
        )
        residuals.append(control.residual_deg(truth.rotation(0), truth.centers[0],
                                              truth.focal_px, truth.cx, truth.cy))
    assert max(abs(r) for r in residuals) < 1e-6


def test_a_wrong_correspondence_shows_up_as_a_large_residual_not_a_moved_answer(
        tmp_path, synthetic_scene):
    """One mis-clicked control must be visible in the receipt, not absorbed.

    A control point is weighted evidence.  A single bad one among many should
    lose to the rest AND be reported as having lost, which is the difference
    between a mechanism that can be audited and a correction applied behind the
    solve's back.
    """
    scene = synthetic_scene
    poisoned = list(scene["controls"])
    good = poisoned[0]
    poisoned[0] = ControlPoint(
        control_id=good.control_id, capture_id=good.capture_id,
        pixel=(good.pixel[0] + 600.0, good.pixel[1]), anchor_id=good.anchor_id,
        pixel_sigma_px=good.pixel_sigma_px, reviewer="operator",
        reviewed_at="2026-09-12T00:00:00Z", review_method="deliberately mis-clicked",
    )
    path = _write_controls(tmp_path, poisoned, state=REVIEW_STATE_SYNTHETIC, reviewer="")
    constraints, _ = build_constraints(
        load_control_point_set(path), _set_of(scene["anchors"]), scene["spun"],
        scene["frame_of_capture"], image_size=scene["image_size"],
    )
    started, _ = initial_control_alignment(scene["spun"], constraints)
    solved = global_solve(started, scene["captures"], scene["observations"],
                          [], control_points=constraints, max_iterations=800)
    rows = {r["control_id"]: r for r in solved.report["constraint_residuals"]["control_points"]}
    bad = rows[good.control_id]
    assert abs(bad["residual_sigmas"]) > 10.0
    others = [abs(r["residual_sigmas"]) for cid, r in rows.items() if cid != good.control_id]
    assert np.median(others) < 2.0
    # and the answer is still right: the bad control lost.
    assert max(abs(v) for v in _yaw_error_deg(solved, scene["truth"])) < 2.0


# ==========================================================================
# the widened anchors in the solve
# ==========================================================================


def test_a_mixed_anchor_list_assembles_the_right_number_of_rows(synthetic_scene):
    """A wall anchor, a position anchor and a curve anchor in one solve."""
    scene = synthetic_scene
    truth = scene["truth"]
    track_ids = sorted(truth.points)[:3]
    position = WorldIRAnchorConstraint(
        track_index=track_ids[0],
        anchor=_point_anchor("anchor:building_corner:node/9",
                             *truth.points[track_ids[0]][:2]),
    )
    curve_anchor = WorldIRAnchor(
        anchor_id="anchor:railway_vertex:node/9", anchor_type="railway_vertex",
        osm_ref="node/9", en=np.array(truth.points[track_ids[1]][:2]),
        sigma_m=0.6666666666666666, inside_aoi=True, reason="synthetic",
        tangent_en=np.array([1.0, 0.0]),
    )
    curve = WorldIRAnchorConstraint(track_index=track_ids[1], anchor=curve_anchor)
    assert position.residual_rows == 2
    assert curve.residual_rows == 1

    solved = global_solve(truth, scene["captures"], scene["observations"],
                          [position, curve], max_iterations=60)
    assert solved.report["anchor_constraints"] == 2
    assert solved.report["anchor_constraint_rows"] == 3
    assert solved.report["anchor_constraints_by_geometry"] == {
        "point": 1, "point_on_curve": 1}
    assert solved.report["anchor_constraints_by_kind"] == {
        "building_corner": 1, "railway_vertex": 1}
    residuals = solved.report["constraint_residuals"]["anchors"]
    assert {r["geometry"] for r in residuals} == {"point", "point_on_curve"}
    for row in residuals:
        assert row["residual_m"] is not None and row["residual_sigmas"] is not None


def test_a_wall_only_solve_reports_exactly_what_it_always_did(synthetic_scene):
    """The CONTROL corridor's path must be unchanged by the widening."""
    from scripts.pipeline.camera_solve.osm_anchors import BuildingOutline, nearest_wall
    scene = synthetic_scene
    truth = scene["truth"]
    track = sorted(truth.points)[0]
    x, y = truth.points[track][:2]
    ring = np.array([[x - 5, y], [x + 5, y], [x + 5, y + 8], [x - 5, y + 8], [x - 5, y]])
    wall = nearest_wall(np.array([x, y]),
                        [BuildingOutline("way/1", {"building": "yes"}, ring)])
    solved = global_solve(truth, scene["captures"], scene["observations"],
                          [AnchorConstraint(track_index=track, wall=wall)],
                          max_iterations=60)
    assert solved.report["anchor_constraints"] == 1
    assert solved.report["anchor_constraint_rows"] == 1
    assert solved.report["anchor_constraints_by_geometry"] == {"plane": 1}
    assert solved.report["control_point_constraints"] == 0


def test_a_control_point_naming_an_unregistered_frame_is_refused(synthetic_scene):
    scene = synthetic_scene
    stray = ControlPointConstraint(
        control_id="cp-stray", frame=99, uv=np.array([10.0, 10.0]),
        anchor=scene["anchors"][0], sigma_deg=1.0,
    )
    with pytest.raises(ValueError, match="did not register"):
        global_solve(scene["truth"], scene["captures"], scene["observations"], [],
                     control_points=[stray], max_iterations=5)


def test_constraint_residual_report_is_callable_on_its_own(synthetic_scene):
    """The receipt owner can ask for the residuals without re-running the solve."""
    scene = synthetic_scene
    report = constraint_residual_report(scene["truth"], [], [])
    assert report["anchors"] == [] and report["control_points"] == []
    assert report["anchor_residual_m"] is None
