"""FL-4812: observation-to-world geometric solve.

TWO LAYERS, ON PURPOSE
======================
The fast layer exercises every decision rule on synthetic or hand-built input:
the intrinsics policy, the promotion gates, the geodesy, the OSM offset
estimator, the covariance bookkeeping.  These run in under a second and they
are the tests that would catch a rule quietly changing.

The corridor layer runs the WHOLE chain on the real frozen control frames and
asserts the numbers the receipt claims.  It is skipped when those frames are
not on this machine - they are CC-BY-SA-4.0 imagery kept out of the repo - and
skipping is stated loudly rather than silently passing.  Set
``FL4812_CORRIDOR`` to the directory holding ``raw/selected.json``, ``frames/``
and ``osm/control.osm`` to run it.

WHAT THE CORRIDOR LAYER IS EVIDENCE OF
======================================
CONTROL / METHOD PROOF ONLY.  It proves the machinery runs end to end on real
open imagery.  It is NOT acceptance evidence for the Numabe target corridor,
and no assertion here should ever be cited as such.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.camera_solve import gates  # noqa: E402
from scripts.pipeline.camera_solve.capture import (  # noqa: E402
    INTRINSICS_MEASURED,
    INTRINSICS_PRIOR,
    capture_from_item,
    horizontal_fov_deg,
    intrinsics_from_item,
)
from scripts.pipeline.camera_solve.geodesy import (  # noqa: E402
    EnuFrame,
    angle_delta_deg,
    bearing_deg,
    exif_dms,
    exif_rational,
)
from scripts.pipeline.camera_solve.georegister import umeyama_similarity  # noqa: E402
from scripts.pipeline.camera_solve.osm_anchors import (  # noqa: E402
    BuildingOutline,
    estimate_block_offset,
    is_pole_like,
    nearest_wall,
    point_segment_distance,
)
from scripts.pipeline.camera_solve.relative import (  # noqa: E402
    intrinsic_matrix,
    triangulate_dlt,
    triangulation_angle_deg,
)
from scripts.pipeline.camera_solve.sfm import (  # noqa: E402
    Reconstruction,
    prune_for_global_solve,
)
from scripts.pipeline.camera_solve.triangulate import covariance_record  # noqa: E402
from scripts.pipeline.world_ir import schema as S  # noqa: E402
from scripts.pipeline.world_ir.validate import validate_document  # noqa: E402

CORRIDOR = Path(
    os.environ.get(
        "FL4812_CORRIDOR",
        "/private/tmp/claude-501/-Users-r-Downloads-asciicker-Y9-2/"
        "7c815c93-bea8-4f4e-a616-c7ed9c9d19a8/scratchpad/fl4812",
    )
)


def corridor_available() -> bool:
    return (
        (CORRIDOR / "raw" / "selected.json").is_file()
        and (CORRIDOR / "osm" / "control.osm").is_file()
        and len(list((CORRIDOR / "frames").glob("*.jpg"))) >= 3
        if (CORRIDOR / "frames").is_dir()
        else False
    )


def test_global_stability_pruning_removes_orphan_cameras_and_their_tracks():
    recon = Reconstruction(
        rvecs={frame: np.zeros(3) for frame in range(5)},
        tvecs={frame: np.array([float(frame), 0.0, 0.0]) for frame in range(5)},
        points={track: np.array([float(track), 0.0, 10.0]) for track in range(7)},
        focal_px=100.0,
        cx=50.0,
        cy=50.0,
    )
    graph = SimpleNamespace(tracks=[
        [(0, track), (1, track), (2, track)] for track in range(6)
    ] + [[(3, 6), (4, 6)]])

    report = prune_for_global_solve(recon, graph)

    assert recon.registered == [0, 1, 2]
    assert sorted(recon.points) == list(range(6))
    assert report == {
        "minimum_track_viewpoints": 2,
        "minimum_camera_tracks": 6,
        "frames_before": 5,
        "frames_after": 3,
        "frames_dropped": 2,
        "points_before": 7,
        "points_after": 6,
        "points_dropped": 1,
    }


requires_corridor = pytest.mark.skipif(
    not corridor_available(),
    reason=(
        "FL-4812 control corridor frames are not on this machine. They are "
        "CC-BY-SA-4.0 Panoramax imagery kept OUT of the repo; set FL4812_CORRIDOR "
        "to a directory holding raw/selected.json, frames/ and osm/control.osm."
    ),
)


# ==========================================================================
# geodesy
# ==========================================================================


def test_enu_roundtrip_is_sub_millimetre():
    frame = EnuFrame(139.7218, 35.5762, 6.0)
    for lon, lat, alt in [(139.7218, 35.5762, 6.0), (139.7231, 35.5771, 12.5),
                          (139.7202, 35.5750, -3.0)]:
        east, north, up = frame.to_enu(lon, lat, alt)
        back = frame.to_geodetic(east, north, up)
        assert abs(back[0] - lon) * 90000 < 1e-3
        assert abs(back[1] - lat) * 111320 < 1e-3
        assert abs(back[2] - alt) < 1e-3


def test_enu_axes_point_east_north_up():
    frame = EnuFrame(139.7218, 35.5762, 0.0)
    east, north, _ = frame.to_enu(139.7228, 35.5762, 0.0)
    assert east > 50 and abs(north) < 1.0
    east, north, _ = frame.to_enu(139.7218, 35.5772, 0.0)
    assert north > 100 and abs(east) < 1.0
    assert frame.to_enu(139.7218, 35.5762, 10.0)[2] == pytest.approx(10.0, abs=1e-6)


def test_bearing_and_angle_wrapping():
    assert bearing_deg(0.0, 1.0) == pytest.approx(0.0)
    assert bearing_deg(1.0, 0.0) == pytest.approx(90.0)
    assert bearing_deg(-1.0, 0.0) == pytest.approx(270.0)
    assert angle_delta_deg(350.0, 10.0) == pytest.approx(-20.0)
    assert angle_delta_deg(10.0, 350.0) == pytest.approx(20.0)


def test_exif_rationals():
    assert exif_rational("6345/1000") == pytest.approx(6.345)
    assert exif_dms("35/1 34/1 18204487/566659") == pytest.approx(35.575590, abs=1e-5)


# ==========================================================================
# intrinsics: the missing-focal-length policy
# ==========================================================================


def test_absent_focal_length_becomes_a_prior_and_says_so():
    """The control source states no focal length anywhere. It must not be faked."""
    props = {
        "pers:interior_orientation": {
            "camera_model": "iPhone",
            "camera_manufacturer": "Apple",
            "sensor_array_dimensions": [4032, 3024],
        },
        "exif": {"Exif.Image.Make": "Apple"},
    }
    prior = intrinsics_from_item(props, 2048, 1536)
    assert prior.state == INTRINSICS_PRIOR
    assert prior.is_free_parameter is True
    assert "NO focal length" in prior.basis
    assert prior.sigma_px > 100.0, "a prior with a tight sigma is a measurement in disguise"
    assert 50.0 < horizontal_fov_deg(prior.focal_px, 2048) < 90.0


def test_stated_35mm_equivalent_is_measured_and_pinned():
    props = {"exif": {"Exif.Photo.FocalLengthIn35mmFilm": "26/1"}}
    prior = intrinsics_from_item(props, 2048, 1536)
    assert prior.state == INTRINSICS_MEASURED
    assert prior.is_free_parameter is False
    assert prior.focal_px == pytest.approx(2048 * 26.0 / 36.0)


def test_focal_in_mm_without_a_sensor_width_stays_a_prior():
    """Millimetres with no sensor size cannot become pixels; guessing would be a lie."""
    props = {"pers:interior_orientation": {"focal_length": 6.9,
                                           "sensor_array_dimensions": [2160, 3840]}}
    prior = intrinsics_from_item(props, 2048, 1536)
    assert prior.state == INTRINSICS_PRIOR
    assert "sensor width" in prior.basis


def test_capture_carries_licence_attribution_and_its_own_accuracy():
    item = {
        "id": "abc",
        "geometry": {"type": "Point", "coordinates": [139.7218, 35.5762]},
        "links": [{"rel": "self", "href": "https://example.invalid/items/abc"}],
        "providers": [{"name": "motocultrice", "roles": ["producer"]},
                      {"name": "Zaneo", "roles": ["producer"]}],
        "properties": {
            "datetime": "2022-06-24T21:12:25Z",
            "license": "CC-BY-SA-4.0",
            "geovisio:producer": "motocultrice",
            "geovisio:rank_in_collection": 13,
            "view:azimuth": 40,
            "quality:horizontal_accuracy": 5.0,
            "pers:interior_orientation": {"camera_model": "iPhone",
                                          "sensor_array_dimensions": [4032, 3024]},
            "exif": {"Exif.GPSInfo.GPSAltitude": "6140/1000"},
        },
    }
    capture = capture_from_item(item, "/dev/null", 2048, 1536)
    assert capture.licence == "CC-BY-SA-4.0"
    assert "Zaneo" in capture.attribution and "CC-BY-SA-4.0" in capture.attribution
    assert capture.horizontal_accuracy_m == 5.0
    assert capture.vertical_accuracy_m > capture.horizontal_accuracy_m
    assert capture.alt == pytest.approx(6.14)
    assert capture.intrinsics.state == INTRINSICS_PRIOR


def test_sparse_receipt_freezes_working_and_source_pixels():
    from scripts.pipeline.camera_solve.capture import Capture, IntrinsicsPrior
    from scripts.pipeline.camera_solve.features import FrameFeatures, MatchGraph
    from scripts.pipeline.camera_solve.georegister import GeoSolution
    from scripts.pipeline.camera_solve.receipts import sparse_reconstruction_record

    intrinsics = IntrinsicsPrior(100.0, 1.0, INTRINSICS_MEASURED, "test")
    captures = {
        0: Capture(
            capture_id="a", rank=0, image_path="/a", width=100, height=50,
            lon=0, lat=0, alt=0, azimuth_deg=0, horizontal_accuracy_m=1,
            intrinsics=intrinsics, licence="licence", producer="producer",
            captured_at="time", attribution="attribution", source_url="url",
            digest="sha256:a", source_dimensions_px=(200, 100),
        ),
        1: Capture(
            capture_id="b", rank=1, image_path="/b", width=100, height=50,
            lon=0, lat=0, alt=0, azimuth_deg=0, horizontal_accuracy_m=1,
            intrinsics=intrinsics, licence="licence", producer="producer",
            captured_at="time", attribution="attribution", source_url="url",
            digest="sha256:b", source_dimensions_px=(400, 200),
        ),
    }
    features = [
        FrameFeatures("a", np.array([[50.0, 25.0]]), np.zeros((1, 128)), np.ones(1)),
        FrameFeatures("b", np.array([[25.0, 10.0]]), np.zeros((1, 128)), np.ones(1)),
    ]
    graph = MatchGraph(features=features, tracks=[[(0, 0), (1, 0)]])
    solution = GeoSolution(
        frames=[0, 1],
        rvecs={0: np.zeros(3), 1: np.zeros(3)},
        centers={0: np.zeros(3), 1: np.array([1.0, 0.0, 0.0])},
        points={0: np.array([0.0, 0.0, 10.0])},
        focal_px=100.0, cx=50.0, cy=25.0, focal_free=False,
        frame_intrinsics={
            0: np.array([[100.0, 0.0, 50.0], [0.0, 100.0, 25.0], [0.0, 0.0, 1.0]]),
            1: np.array([[100.0, 0.0, 50.0], [0.0, 100.0, 25.0], [0.0, 0.0, 1.0]]),
        },
    )
    record = sparse_reconstruction_record(solution, graph, captures)
    assert record["counts"] == {"points": 1, "observations": 2}
    observations = record["points"][0]["observations"]
    assert observations[0]["source_image_pixel_px"] == [100.0, 50.0]
    assert observations[1]["source_image_pixel_px"] == [100.0, 40.0]


def test_wikimedia_capture_is_not_labeled_as_panoramax():
    item = {
        "id": "commons-1",
        "geometry": {"type": "Point", "coordinates": [139.6727, 35.5830]},
        "links": [{"rel": "self", "href": "https://commons.wikimedia.org/wiki/File:Numabe.jpeg"}],
        "providers": [{"name": "Nesnad", "roles": ["producer"]}],
        "properties": {
            "datetime": "2024-05-30T16:09:38+09:00",
            "license": "CC BY 4.0",
            "geovisio:producer": "Nesnad / Wikimedia Commons",
            "geovisio:rank_in_collection": 0,
            "pers:interior_orientation": {"sensor_array_dimensions": [4032, 3024]},
            "exif": {},
        },
    }
    capture = capture_from_item(item, "/dev/null", 2048, 1536)
    assert capture.source_provider == "Wikimedia Commons"
    assert capture.source_id == "wikimedia_commons_street_imagery"
    assert "Wikimedia Commons" in capture.attribution
    assert "Panoramax" not in capture.attribution


def test_canonical_capture_index_selects_component_and_derives_working_pixels(tmp_path):
    import cv2

    from scripts.pipeline.camera_solve.solve import CorridorInputs, load_captures

    frames = tmp_path / "captures"
    frames.mkdir()
    image = np.zeros((12, 24, 3), dtype=np.uint8)
    assert cv2.imwrite(str(frames / "cap-a.jpeg"), image)
    assert cv2.imwrite(str(frames / "cap-b.jpeg"), image)
    common = {
        "provider": "test",
        "license": "CC0",
        "lat": 35.0,
        "lon": 139.0,
        "altitude_m": 10.0,
        "horizontal_accuracy_m": 2.0,
        "vertical_accuracy_m": 7.0,
        "focal_35mm": 24.0,
        "heading_deg": 90.0,
    }
    source_index = tmp_path / "captures.json"
    source_index.write_text(json.dumps({
        "schema": "y92.capture_source_index.v1",
        "default_reconstruction_component": "selected",
        "reconstruction_components": [{
            "component_id": "selected", "capture_ids": ["cap-a"]
        }],
        "working_image_policy": {
            "max_edge_px": 12,
            "resize_interpolation": "opencv_inter_area",
            "encoding": "jpeg",
            "jpeg_quality": 95,
        },
        "captures": [
            {**common, "capture_id": "cap-a", "sequence_index": 0,
             "local_path": "captures/cap-a.jpeg"},
            {**common, "capture_id": "cap-b", "sequence_index": 1,
             "local_path": "captures/cap-b.jpeg"},
        ],
    }))

    captures = load_captures(CorridorInputs(source_index, frames, tmp_path / "unused.osm"))

    assert [capture.capture_id for capture in captures] == ["cap-a"]
    assert (captures[0].width, captures[0].height) == (12, 6)
    assert captures[0].source_dimensions_px == (24, 12)
    assert captures[0].vertical_accuracy_m == 7.0
    assert captures[0].working_image_digest.startswith("sha256:")
    assert captures[0].working_image_policy["max_edge_px"] == 12


# ==========================================================================
# two-view geometry
# ==========================================================================


def test_triangulation_recovers_a_known_point():
    K = intrinsic_matrix(1480.0, 1024.0, 768.0)
    truth = np.array([2.0, -1.0, 14.0])
    projections, uv = [], []
    for baseline in (0.0, 4.0, 8.0):
        P = K @ np.hstack([np.eye(3), np.array([[-baseline], [0.0], [0.0]])])
        h = P @ np.append(truth, 1.0)
        projections.append(P)
        uv.append(h[:2] / h[2])
    assert np.allclose(triangulate_dlt(projections, uv), truth, atol=1e-6)


def test_triangulation_angle_is_the_widest_pair_not_the_first():
    centers = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [10.0, 0.0, 0.0]])
    point = np.array([0.0, 0.0, 10.0])
    angle = triangulation_angle_deg(centers, point)
    assert angle == pytest.approx(math.degrees(math.atan2(10.0, 10.0)), abs=0.5)


def test_triangulation_angle_is_zero_for_one_viewpoint_repeated():
    centers = np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
    assert triangulation_angle_deg(centers, np.array([1.0, 2.0, 20.0])) == pytest.approx(0.0)


def test_umeyama_recovers_a_known_similarity():
    rng = np.random.default_rng(4812)
    source = rng.normal(size=(12, 3)) * 5.0
    angle = 0.7
    R = np.array([[math.cos(angle), -math.sin(angle), 0.0],
                  [math.sin(angle), math.cos(angle), 0.0],
                  [0.0, 0.0, 1.0]])
    target = 2.5 * source @ R.T + np.array([10.0, -4.0, 1.5])
    scale, R_hat, t_hat = umeyama_similarity(source, target)
    assert scale == pytest.approx(2.5, rel=1e-6)
    assert np.allclose(R_hat, R, atol=1e-6)
    assert np.allclose(t_hat, [10.0, -4.0, 1.5], atol=1e-6)


# ==========================================================================
# OSM anchors
# ==========================================================================


def _square(ref: str, x0: float, y0: float, size: float) -> BuildingOutline:
    ring = np.array([[x0, y0], [x0 + size, y0], [x0 + size, y0 + size],
                     [x0, y0 + size], [x0, y0]])
    return BuildingOutline(osm_ref=ref, tags={"building": "yes"}, ring=ring)


def test_point_segment_distance_clamps_to_the_segment():
    a, b = np.array([0.0, 0.0]), np.array([10.0, 0.0])
    assert point_segment_distance(np.array([5.0, 3.0]), a, b)[0] == pytest.approx(3.0)
    assert point_segment_distance(np.array([-4.0, 0.0]), a, b)[0] == pytest.approx(4.0)


def test_nearest_wall_picks_the_closest_building():
    buildings = [_square("way/1", 0.0, 0.0, 10.0), _square("way/2", 40.0, 0.0, 10.0)]
    match = nearest_wall(np.array([-2.0, 5.0]), buildings)
    assert match.osm_ref == "way/1"
    assert match.distance_m == pytest.approx(2.0)


def test_block_offset_estimator_finds_a_planted_shift():
    """A common translation is recoverable; a single outlier must not steer it."""
    buildings = [_square("way/1", 0.0, 0.0, 20.0), _square("way/2", 0.0, 40.0, 20.0)]
    shift = np.array([1.8, -0.9])
    points = []
    for t in np.linspace(1.0, 19.0, 14):
        points.append(np.array([t, 0.0]) + shift)
        points.append(np.array([0.0, 40.0 + t]) + shift)
    points.append(np.array([10.0, 3.2]))  # one gross mismatch
    offset, report = estimate_block_offset(points, buildings, gate_m=4.0)
    assert report["converged"] is True
    assert np.allclose(offset, shift, atol=0.25)


def test_pole_tagging_predicate_covers_the_real_spellings():
    assert is_pole_like({"power": "pole"})
    assert is_pole_like({"man_made": "utility_pole"})
    assert is_pole_like({"highway": "street_lamp"})
    assert not is_pole_like({"building": "yes"})
    assert not is_pole_like({"amenity": "vending_machine"})


# ==========================================================================
# promotion gates - the refusals matter more than the passes
# ==========================================================================


def _strong(**overrides):
    kwargs = dict(
        viewpoints=5,
        triangulation_angle_deg=12.0,
        reprojection_px=[0.4, 0.6, 0.9, 1.1, 1.4],
        covariance=np.diag([0.2, 0.3, 0.5]),
        semantic_votes={"fixture": 5},
        expected_class="fixture",
    )
    kwargs.update(overrides)
    return gates.evaluate(**kwargs)


def test_strong_evidence_is_promoted():
    decision = _strong()
    assert decision.promoted
    assert decision.failed_criteria == []
    assert all(c["pass"] for c in decision.as_record()["criteria"])


def test_single_viewpoint_is_refused():
    decision = _strong(viewpoints=1, reprojection_px=[0.2])
    assert not decision.promoted
    assert "useful_viewpoints" in decision.failed_criteria


def test_narrow_triangulation_angle_is_refused():
    decision = _strong(triangulation_angle_deg=2.4)
    assert not decision.promoted
    assert "triangulation_angle_deg" in decision.failed_criteria


def test_two_degree_angle_is_refused_even_with_perfect_pixels():
    """A point straight ahead reprojects perfectly and is still not measured."""
    decision = _strong(triangulation_angle_deg=0.4, reprojection_px=[0.01] * 5)
    assert not decision.promoted


def test_angle_between_floor_and_preference_passes_but_is_flagged():
    decision = _strong(triangulation_angle_deg=3.5)
    assert decision.promoted
    angle = [c for c in decision.as_record()["criteria"]
             if c["criterion"] == "triangulation_angle_deg"][0]
    assert angle["pass"] is True and angle["preferred_met"] is False


def test_high_median_reprojection_is_refused():
    decision = _strong(reprojection_px=[2.1, 2.4, 2.8, 3.0, 3.2])
    assert not decision.promoted
    assert "median_reprojection_px" in decision.failed_criteria


def test_one_observation_above_four_pixels_needs_review():
    decision = _strong(reprojection_px=[0.3, 0.4, 0.5, 0.6, 9.5])
    assert not decision.promoted
    assert "worst_accepted_observation_px" in decision.failed_criteria


def test_missing_or_singular_covariance_is_refused():
    assert "finite_positive_definite_uncertainty" in _strong(covariance=None).failed_criteria
    singular = np.diag([0.2, 0.3, 0.0])
    assert "finite_positive_definite_uncertainty" in _strong(covariance=singular).failed_criteria
    nonfinite = np.diag([0.2, 0.3, np.inf])
    assert "finite_positive_definite_uncertainty" in _strong(covariance=nonfinite).failed_criteria


def test_disagreeing_viewpoints_are_refused():
    decision = _strong(semantic_votes={"fixture": 3, "tree": 2})
    assert not decision.promoted
    assert "consistent_semantics" in decision.failed_criteria


def test_unanimous_agreement_on_the_WRONG_class_is_still_refused():
    """Five views agreeing a point is NOT what is claimed is unanimous, not consistent."""
    decision = _strong(semantic_votes={"no_wall_intersection": 5}, expected_class="facade_region")
    assert not decision.promoted
    assert "consistent_semantics" in decision.failed_criteria


def test_weak_evidence_is_unresolved_not_promoted():
    decision = _strong(viewpoints=2, triangulation_angle_deg=1.0, covariance=None,
                       reprojection_px=[5.0, 6.0], semantic_votes={})
    assert decision.decision == gates.DECISION_UNRESOLVED
    assert len(decision.failed_criteria) >= 4


def test_covariance_record_reports_sigmas_and_definiteness():
    record = covariance_record(np.diag([0.04, 0.09, 0.25]))
    assert record["finite"] and record["positive_definite"]
    assert record["sigma_m"] == pytest.approx([0.2, 0.3, 0.5])
    assert record["sigma_major_m"] == pytest.approx(0.5)
    assert covariance_record(None) is None


# ==========================================================================
# emission rules that must hold whatever the data says
# ==========================================================================


def test_reconstructed_geometry_never_claims_physical_authority():
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    assert ingest.RECONSTRUCTED_AUTHORITY not in S.PHYSICAL_AUTHORITIES
    assert ingest.REFUSED_AUTHORITY == S.AUTHORITY_NONE


def test_a_refused_candidate_is_emitted_as_unresolved_with_no_authority():
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    frame = EnuFrame(139.7218, 35.5762, 6.0)
    record = {
        "candidate_id": "weak_thing",
        "semantic_class": "fixture",
        "position_enu_m": [1.0, 2.0, 3.0],
        "viewpoints": 2,
        "triangulation_angle_deg": 1.2,
        "covariance_absolute": {"matrix": [[9.0, 0.0, 0.0], [0.0, 9.0, 0.0], [0.0, 0.0, 9.0]]},
        "reprojection": {
            "frames_reprojected": 6,
            "status_counts": {"matched": 2, "outside_frame": 4},
            "matched_residual_px": {"count": 2, "median": 5.1, "max": 6.2},
        },
        "gate": {"decision": "unresolved", "failed": ["triangulation_angle_deg"],
                 "all_required_passed": False, "criteria": []},
    }
    feature = ingest.candidate_feature(record, frame, ["obs:imagery:panoramax/x"])
    assert feature["gameplay_authority"] == S.AUTHORITY_NONE
    assert feature["conflict"]["state"] == S.CONFLICT_UNRESOLVED
    assert feature["conflict"]["contradictions"], "the refusal must stay in the document"
    assert feature["presentation_requirements"]["must_not_imply_collision"] is True


def test_a_promoted_candidate_stays_presentation_only():
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    frame = EnuFrame(139.7218, 35.5762, 6.0)
    record = {
        "candidate_id": "good_thing",
        "semantic_class": "fixture",
        "position_enu_m": [1.0, 2.0, 3.0],
        "viewpoints": 6,
        "triangulation_angle_deg": 11.0,
        "covariance_absolute": {"matrix": [[0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 0.1]]},
        "reprojection": {
            "frames_reprojected": 8,
            "status_counts": {"matched": 6, "predicted": 2},
            "matched_residual_px": {"count": 6, "median": 0.8, "max": 1.4},
        },
        "gate": {"decision": "promote", "failed": [], "all_required_passed": True, "criteria": []},
    }
    feature = ingest.candidate_feature(record, frame, ["obs:imagery:panoramax/x"])
    assert feature["gameplay_authority"] == S.AUTHORITY_PRESENTATION_ONLY
    assert feature["gameplay_authority"] not in S.PHYSICAL_AUTHORITIES
    assert feature["conflict"]["state"] == S.CONFLICT_NONE


def test_every_emitted_record_names_its_control_evidence_role():
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    assert "NOT acceptance evidence" in ingest.EVIDENCE_ROLE_NOTE
    assert "Geometric registration evidence" in ingest.EVIDENCE_ROLE_NOTE


def test_camera_ingest_cli_forwards_anchor_and_reviewed_control_paths(
        tmp_path, monkeypatch):
    """The public build command must not silently fall back to GPS-only."""
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    seen = {}

    class FakeStore:
        def validate(self):
            return []

        def save(self, path):
            Path(path).write_text("{}", encoding="utf-8")

    def fake_build(inputs, **_kwargs):
        seen["inputs"] = inputs
        return FakeStore(), {
            "world_ir": {
                "source_observations": 0,
                "canonical_features": 0,
                "gameplay_authorities": [],
            },
            "corridor": {"frames_solved": 0},
            "intrinsics": {
                "focal_sigma_px": None,
                "focal_px_solved": 1.0,
                "focal_state": "test",
            },
            "residuals": {"all_visual_observations_px": {
                "median": 0.0, "p90": 0.0, "max": 0.0, "count": 0,
            }},
            "candidates": [],
        }

    monkeypatch.setattr(ingest, "build_document", fake_build)
    paths = {name: tmp_path / name for name in ("items", "frames", "osm", "anchors", "controls")}
    assert ingest.main([
        "--items", str(paths["items"]),
        "--frames", str(paths["frames"]),
        "--osm", str(paths["osm"]),
        "--anchors", str(paths["anchors"]),
        "--control-points", str(paths["controls"]),
        "--out", str(tmp_path / "world.json"),
    ]) == 0
    assert seen["inputs"].anchors_path == paths["anchors"]
    assert seen["inputs"].control_points_path == paths["controls"]


def test_control_points_cannot_run_without_the_anchor_set_that_minted_their_ids(
        tmp_path):
    from scripts.pipeline.camera_solve.solve import CorridorInputs, run

    with pytest.raises(ValueError, match="requires anchors_path"):
        run(CorridorInputs(
            tmp_path / "items", tmp_path / "frames", tmp_path / "osm",
            control_points_path=tmp_path / "controls",
        ))


# ==========================================================================
# the real corridor: the end-to-end proof
# ==========================================================================


def _corridor_inputs():
    from scripts.pipeline.camera_solve.solve import CorridorInputs

    return CorridorInputs(
        CORRIDOR / "raw" / "selected.json",
        CORRIDOR / "frames",
        CORRIDOR / "osm" / "control.osm",
    )


# Both fixtures are module-scoped and each costs one full solve of 22 frames.
# Letting individual tests call ``build_document`` themselves ran the whole
# pipeline six times and turned this file into a fifteen-minute test.
@pytest.fixture(scope="module")
def corridor_result():
    from scripts.pipeline.camera_solve.solve import run

    return run(_corridor_inputs())


@pytest.fixture(scope="module")
def corridor_document():
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    return ingest.build_document(_corridor_inputs())


@requires_corridor
def test_corridor_every_frame_is_solved(corridor_result):
    stage = corridor_result["stages"]["relative_reconstruction"]
    assert stage["frames_registered"] == stage["frames_total"]
    assert stage["frames_registered"] >= 12


@requires_corridor
def test_corridor_source_states_no_focal_length_so_it_is_solved(corridor_result):
    captures = corridor_result["captures"]
    assert all(c.intrinsics.state == INTRINSICS_PRIOR for c in captures.values())
    solution = corridor_result["solution"]
    assert solution.focal_free is True
    assert math.isfinite(solution.focal_sigma_px)
    # The posterior must be tighter than the prior, or the imagery taught nothing.
    assert solution.focal_sigma_px < captures[solution.frames[0]].intrinsics.sigma_px / 5.0


@requires_corridor
def test_corridor_cameras_are_level_and_gravity_is_resolved(corridor_result):
    """The roll gauge is unobservable from a straight GPS track; it must still be fixed."""
    solution = corridor_result["solution"]
    rolls = [
        abs(math.degrees(math.asin(np.clip(solution.rotation(f).T[2, 0], -1.0, 1.0))))
        for f in solution.frames
    ]
    assert max(rolls) < 15.0, f"cameras are rolled: max {max(rolls):.1f} deg"
    ups = [float(-solution.rotation(f).T[2, 1]) for f in solution.frames]
    assert all(u > 0 for u in ups), "the reconstruction is upside down"


@requires_corridor
def test_corridor_reprojection_residuals_are_small(corridor_result):
    errors = np.asarray(corridor_result["per_observation_px"])
    assert errors.size > 500
    assert float(np.median(errors)) < 1.0
    assert float(np.percentile(errors, 90)) < 3.0


@requires_corridor
def test_corridor_weighting_is_explicit_and_self_consistent(corridor_result):
    report = corridor_result["solution"].report
    sigmas = report["sigmas"]
    for key in ("reprojection_px", "capture_gps_horizontal_m", "capture_gps_vertical_m",
                "azimuth_deg", "roll_deg", "osm_anchor_m", "focal_prior_px"):
        assert key in sigmas, f"{key} is not an inspectable weight"
    # A variance factor far from 1 means the stated sigmas do not describe the
    # data; near 1 means the whole weighted set is mutually consistent.
    assert 0.25 < report["variance_factor"] < 4.0


@requires_corridor
def test_corridor_osm_contains_no_pole_like_feature(corridor_result):
    """The unmapped-fixture claim is about the map; it is checked against the map."""
    osm = corridor_result["stages"]["osm"]
    assert osm["pole_like_features_in_extract"] == 0
    assert "RETRACTED" not in osm["absence_claim"]


@requires_corridor
def test_corridor_produces_a_facade_anchor_and_an_unmapped_fixture(corridor_result):
    ids = {c["candidate_id"] for c in corridor_result["candidates"]}
    assert {"facade_anchor", "unmapped_fixture"} <= ids


@requires_corridor
def test_corridor_candidates_carry_finite_covariance_and_full_reprojection(corridor_result):
    solution = corridor_result["solution"]
    for candidate in corridor_result["candidates"]:
        covariance = candidate["covariance_absolute"]
        assert covariance is not None and covariance["finite"] and covariance["positive_definite"]
        assert candidate["covariance_relative_to_camera_track"] is not None
        # Reprojected into EVERY solved frame, not only the ones it was fitted to.
        assert candidate["reprojection"]["frames_reprojected"] == len(solution.frames)
        assert candidate["reprojection"]["matched_residual_px"]["count"] >= 2


@requires_corridor
def test_corridor_gate_decisions_match_the_numbers_they_cite(corridor_result):
    """The receipt may never disagree with the run that produced it."""
    for candidate in corridor_result["candidates"]:
        gate = candidate["gate"]
        criteria = {c["criterion"]: c for c in gate["criteria"]}
        assert criteria["useful_viewpoints"]["observed"] == candidate["viewpoints"]
        assert criteria["triangulation_angle_deg"]["observed"] == pytest.approx(
            candidate["triangulation_angle_deg"]
        )
        assert criteria["median_reprojection_px"]["observed"] == pytest.approx(
            candidate["reprojection"]["matched_residual_px"]["median"]
        )
        assert gate["all_required_passed"] == all(c["pass"] for c in gate["criteria"])
        assert (gate["decision"] == "promote") == gate["all_required_passed"]


@requires_corridor
def test_corridor_document_validates_through_the_cli(corridor_document, tmp_path):
    store, _ = corridor_document
    assert validate_document(store.to_dict()) == []

    written = tmp_path / "world_ir.json"
    store.save(written)
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.pipeline.world_ir", "validate", str(written)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@requires_corridor
def test_corridor_rebuild_is_byte_identical(corridor_document):
    """Same frozen inputs, same bytes - the whole determinism claim in one assert."""
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    store, receipt = corridor_document
    store2, receipt2 = ingest.build_document(_corridor_inputs())
    assert store2.dump_bytes() == store.dump_bytes(), "same frozen inputs, different bytes"
    a = {k: v for k, v in receipt.items() if k != "tool_versions"}
    b = {k: v for k, v in receipt2.items() if k != "tool_versions"}
    assert json.dumps(a, sort_keys=True, default=float) == json.dumps(
        b, sort_keys=True, default=float
    )


@requires_corridor
def test_corridor_document_never_grants_physical_authority(corridor_document):
    from scripts.pipeline import world_ir_ingest_cameras as ingest

    store, _ = corridor_document
    for feature in store.features:
        assert feature["gameplay_authority"] not in S.PHYSICAL_AUTHORITIES, feature["feature_id"]
    for observation in store.observations:
        assert observation["attributes"]["evidence_role"] == ingest.EVIDENCE_ROLE
    for feature in store.features:
        assert "evidence_role" in feature["properties"]


@requires_corridor
def test_corridor_receipt_counts_match_the_document(corridor_document):
    store, receipt = corridor_document
    assert receipt["world_ir"]["source_observations"] == len(store.observations)
    assert receipt["world_ir"]["canonical_features"] == len(store.features)
    assert receipt["validation_findings"] == 0 if "validation_findings" in receipt else True
    assert receipt["evidence_role"] == "CONTROL / METHOD PROOF ONLY"
    assert receipt["corridor"]["licence"] == ["CC-BY-SA-4.0"]


@requires_corridor
def test_corridor_records_licence_and_attribution_for_every_frame(corridor_document):
    store, _ = corridor_document
    imagery = [o for o in store.observations if o["source_kind"] == "imagery"]
    assert imagery
    for observation in imagery:
        assert observation["attributes"]["licence"] == "CC-BY-SA-4.0"
        assert observation["attributes"]["attribution"]
        assert observation["digest"].startswith("sha256:")
