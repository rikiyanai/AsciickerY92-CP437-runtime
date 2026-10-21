import json
from pathlib import Path

from scripts.pipeline.bulk_object_observations import (
    SCHEMA,
    _semantic_candidates,
)
from scripts.pipeline.bulk_mask_observations import (
    SCHEMA as MASK_SCHEMA,
    decode_foreground_runs,
    encode_foreground_runs,
)
from scripts.pipeline.bulk_track_observations import (
    bbox_iou,
    select_seed_detections,
    track_sequence,
)
from scripts.pipeline.capture_corpus import load_capture_corpus
from scripts.pipeline.template_resolver import (
    discover_pack_chain,
    load_pack_chain,
    resolve_template_candidates,
    unresolved_source_fallback,
)
from scripts.pipeline.triangulate_track_hypotheses import (
    camera_intrinsic,
    rodrigues_matrix,
    triangulate_rays,
)


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "assets/source_bundles/numabe_home_to_station_20260912"


def test_machine_vision_corpus_uses_calibrated_views_and_route_selections():
    route, selected_route = load_capture_corpus(BUNDLE / "route_capture_manifest.json")
    derived, calibrated_views = load_capture_corpus(
        BUNDLE / "mapillary_rectilinear_capture_source_index.json"
    )

    assert len(selected_route) == route["counts"]["selected_keyframes"]
    assert len(calibrated_views) == derived["counts"]["derived_views"] == 107
    assert all(row["projection_model"] == "rectilinear" for row in calibrated_views)
    assert all(
        row["intrinsics_state"] == "derived_exact_equirectangular_projection"
        for row in calibrated_views
    )


def test_semantic_matching_obeys_word_boundaries():
    owners = {
        "tree": "tree",
        "street tree": "tree",
        "street bollard": "bollard",
        "vending machine": "vending_machine",
    }

    assert _semantic_candidates("street bollard", owners) == ["bollard"]
    assert _semantic_candidates("street tree", owners) == ["tree"]
    assert _semantic_candidates("Japanese vending-machine", owners) == [
        "vending_machine"
    ]


def test_frozen_station_detections_remain_image_evidence_only():
    document = json.loads(
        (BUNDLE / "station_object_observations.json").read_text(encoding="utf-8")
    )

    assert document["schema"] == SCHEMA
    assert document["counts"]["captures_processed"] == 9
    assert document["counts"]["observations"] > 0
    assert document["counts"]["world_localized"] == 0
    assert document["counts"]["canonical_features"] == 0
    assert all(
        row["evidence_state"] == "untracked_image_detection"
        and row["world_localization_state"] == "unresolved"
        and row["canonical_feature_id"] is None
        for row in document["observations"]
    )

    found = {
        candidate
        for row in document["observations"]
        for candidate in row["semantic_candidates"]
    }
    assert {
        "station_identity_sign",
        "vending_machine",
        "utility_pole",
        "railway_crossing_signal",
    } <= found


def test_mask_run_encoding_round_trips_sparse_shapes():
    import numpy as np

    mask = np.zeros((7, 11), dtype=bool)
    mask[1:4, 2:5] = True
    mask[5, 0:2] = True
    mask[6, 10] = True
    runs = encode_foreground_runs(mask)

    assert np.array_equal(
        decode_foreground_runs(runs, height=7, width=11), mask
    )


def test_frozen_station_masks_remain_untracked_evidence_if_present():
    path = BUNDLE / "station_mask_observations.json"
    assert path.exists()
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["schema"] == MASK_SCHEMA
    assert document["counts"]["masks"] == document["counts"]["input_detections"]
    assert document["counts"]["nonempty_masks"] == document["counts"]["masks"]
    assert document["counts"]["tracked_objects"] == 0
    assert document["counts"]["world_localized"] == 0
    assert all(
        row["evidence_state"] == "untracked_image_mask"
        and row["canonical_feature_id"] is None
        for row in document["masks"]
    )


def test_track_seed_selection_suppresses_only_semantic_duplicates():
    common = {
        "capture_id": "frame",
        "bbox_xyxy_px": [0.0, 0.0, 10.0, 10.0],
    }
    rows = [
        {**common, "observation_id": "high", "detector_confidence": 0.9,
         "semantic_candidates": ["sign"]},
        {**common, "observation_id": "duplicate", "detector_confidence": 0.8,
         "semantic_candidates": ["sign"]},
        {**common, "observation_id": "different", "detector_confidence": 0.7,
         "semantic_candidates": ["vending_machine"]},
    ]

    selected = select_seed_detections(
        rows, confidence_threshold=0.3, nms_iou=0.6
    )
    assert [row["observation_id"] for row in selected] == ["high", "different"]
    assert bbox_iou([0, 0, 10, 10], [5, 0, 15, 10]) == 1 / 3


def test_japan_station_context_resolves_tokyu_and_excludes_us_templates():
    requirements = json.loads((BUNDLE / "e2e_requirements.json").read_text())
    context = next(
        value for value in requirements["locale_contexts"].values()
        if "railway_station" in value.get("site_contexts", [])
    )
    packs = load_pack_chain(
        ROOT / "assets/world_templates", context["template_pack_chain"]
    )

    station = resolve_template_candidates(
        semantic_candidates=["station_identity_sign"],
        context=context,
        packs=packs,
        ocr_text="沼部駅 Numabe Sta.",
    )
    assert station[0]["template_id"] == "jp.tokyu.station_identity_v1"
    assert station[0]["identity_bucket"] == "operator_standard"
    # The operator template supplies a sign system, not one hard-coded station
    # name. Source OCR remains on the instance and is not pack-selection data.
    assert "operator" in station[0]["score_reasons"]
    assert "ocr" not in station[0]["score_reasons"]

    traffic = resolve_template_candidates(
        semantic_candidates=["statutory_traffic_sign"],
        context=context,
        packs=packs,
    )
    assert [row["template_id"] for row in traffic] == [
        "jp.traffic.regulatory_family_v1"
    ]
    assert not any(row["template_id"].startswith("us.") for row in traffic)


def test_pack_chain_is_derived_from_location_operator_and_site_context():
    root = ROOT / "assets/world_templates"
    japan = discover_pack_chain(root, {
        "country_code": "JP",
        "region": "Tokyo",
        "municipality": "Ota",
        "operator_ids": ["tokyu"],
        "site_contexts": ["railway_station", "urban_residential"],
    })
    united_states = discover_pack_chain(root, {
        "country_code": "US", "operator_ids": [], "site_contexts": []
    })
    unknown = discover_pack_chain(root, {
        "country_code": "FR", "operator_ids": [], "site_contexts": []
    })

    assert [pack["pack_id"] for pack in japan] == [
        "common_global", "japan", "japan_urban", "tokyo",
        "tokyo_station_small", "tokyu",
    ]
    assert [pack["pack_id"] for pack in united_states] == [
        "common_global", "united_states",
    ]
    assert [pack["pack_id"] for pack in unknown] == ["common_global"]


def test_unknown_locale_falls_back_to_source_evidence():
    fallback = unresolved_source_fallback(["statutory_traffic_sign"])
    assert fallback["template_id"] is None
    assert fallback["face_mode"] == "source_derived"
    assert fallback["resolution_state"] == "no_compatible_locale_template"


def test_frozen_tracks_and_template_scores_are_not_canonical_features():
    tracks = json.loads((BUNDLE / "station_object_tracks.json").read_text())
    candidates = json.loads((BUNDLE / "station_template_candidates.json").read_text())

    assert tracks["counts"]["track_hypotheses"] > 0
    assert tracks["counts"]["matched_detections"] > tracks["counts"]["track_hypotheses"]
    assert tracks["counts"]["physical_objects_confirmed"] == 0
    assert candidates["counts"]["tracks_with_candidates"] > 0
    assert candidates["counts"]["selected_templates"] == 0
    assert candidates["counts"]["canonical_features"] == 0
    assert all(row["selected_template_id"] is None for row in candidates["tracks"])


def test_ray_triangulation_recovers_planted_point():
    import numpy as np

    point = np.array([2.0, 4.0, 1.0])
    centers = [np.array([0.0, 0.0, 0.0]), np.array([3.0, 0.0, 0.0])]
    directions = [(point - center) / np.linalg.norm(point - center) for center in centers]
    solved, covariance = triangulate_rays(centers, directions)

    assert np.allclose(solved, point, atol=1e-9)
    assert np.isfinite(covariance).all()
    assert np.allclose(rodrigues_matrix([0.0, 0.0, 0.0]), np.eye(3))


def test_track_triangulation_uses_each_camera_lens_calibration():
    intrinsics = {
        "focal_px_solved": 999.0,
        "principal_point_px": [512.0, 384.0],
        "per_frame": {
            "0": {"focal_px": 2730.666, "principal_point_px": [1024.0, 768.0]},
            "3": {"focal_px": 1365.333, "principal_point_px": [1024.0, 768.0]},
        },
    }

    assert camera_intrinsic({"frame_rank": 0}, intrinsics) == (
        2730.666, 1024.0, 768.0
    )
    assert camera_intrinsic({"frame_rank": 3}, intrinsics) == (
        1365.333, 1024.0, 768.0
    )


def test_real_track_triangulation_refuses_bad_or_ambiguous_geometry():
    document = json.loads((BUNDLE / "station_track_triangulation.json").read_text())

    assert document["counts"]["triangulated_candidates"] > 0
    assert document["counts"]["geometry_gate_passed"] > 0
    assert document["counts"]["world_ir_promotion_ready"] == 0
    assert document["counts"]["canonical_features"] == 0
    passed = [row for row in document["hypotheses"] if row["geometry_gate_passed"]]
    assert passed
    assert all(not row["world_ir_promotion_ready"] for row in passed)


def test_station_entrance_facade_is_osm_anchored_and_back_projected():
    from scripts.pipeline.world_ir import WorldIRStore

    receipt = json.loads((BUNDLE / "station_facade_reconstruction.json").read_text())
    world_ir = WorldIRStore.load(BUNDLE / "station_facade.world_ir.json")

    assert receipt["gate"]["pass"] is True
    assert receipt["anchor"]["osm_ref"] == "node/9865199050"
    assert receipt["anchor"]["station_name_tags"]["name:ja"] == "沼部"
    assert len(receipt["portal"]["observations"]) == 2
    assert len(receipt["station_identity_sign"]["observations"]) == 3
    assert receipt["back_projection"]["median_bbox_iou"]["portal"] >= 0.3
    assert receipt["back_projection"]["median_bbox_iou"]["station_identity_sign"] >= 0.3
    assert receipt["locale_context"]["template_pack_chain"] == [
        "common_global", "japan", "japan_urban", "tokyo",
        "tokyo_station_small", "tokyu",
    ]
    assert world_ir.validate() == []
    by_class = {feature["semantic_class"]: feature for feature in world_ir.features}
    assert {"facade_plane", "station_entrance", "sign"} <= set(by_class)
    assert by_class["sign"]["properties"]["text"]["value"] == "沼部"
    assert by_class["sign"]["gameplay_authority"] == "presentation_only"


def test_tracking_seeds_late_arrivals_without_reseeding_matched_objects():
    import numpy as np

    left = np.zeros((12, 12), dtype=bool)
    left[1:4, 1:4] = True
    right = np.zeros((12, 12), dtype=bool)
    right[7:10, 7:10] = True
    detections, masks = {}, {}
    for frame, objects in [("empty", []), ("first", ["a"]),
                           ("later", ["a", "b"]), ("last", ["a", "b"])]:
        detections[frame] = []
        for name in objects:
            key = frame + name
            mask = left if name == "a" else right
            box = [1, 1, 4, 4] if name == "a" else [7, 7, 10, 10]
            detections[frame].append({
                "observation_id": key, "semantic_candidates": ["utility_pole"],
                "detector_confidence": .9, "bbox_xyxy_px": box,
            })
            masks[key] = {"mask_size_px": [12, 12],
                          "foreground_runs": encode_foreground_runs(mask)}
    active, prompts = [], []

    def prompt(frame, ids, new_masks):
        prompts.append((frame, ids))
        active.extend(new_masks)

    tracks = track_sequence(
        list(detections), detections, masks,
        propagate=lambda frame: active, prompt=prompt,
        seed_confidence=.3, nms_iou=.6, association_iou=.15,
    )
    assert prompts == [(1, [1]), (2, [2])]
    assert len(tracks) == 2
    assert [r["capture_id"] for r in tracks[0]["frames"]] == ["first", "later", "last"]
    assert [r["matched_object_observation_id"] for r in tracks[1]["frames"]] == ["laterb", "lastb"]
    assert all(t["canonical_feature_id"] is None for t in tracks)


def test_ocr_rejects_successful_plain_text_response(monkeypatch):
    import subprocess
    import pytest
    from PIL import Image
    from scripts.pipeline.bulk_sign_ocr import _ocr_tsv

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(a, 0, b"TEST 123\n", b"Can't open tsv"))
    with pytest.raises(RuntimeError, match="malformed TSV"):
        _ocr_tsv(Image.new("RGB", (100, 40)), tesseract_binary=Path("tesseract"),
                 tessdata_dir=Path("unused"), language="jpn", page_segmentation_mode=6)


def test_local_japanese_ocr_works_without_tessdata_config_files(tmp_path):
    import pytest
    from PIL import Image, ImageDraw, ImageFont
    from scripts.pipeline.bulk_sign_ocr import _ocr_tsv

    binary = Path("/opt/homebrew/bin/tesseract")
    trained = ROOT / ".run/fl4813-tessdata/jpn.traineddata"
    font = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    if not all(p.is_file() for p in [binary, trained, font]):
        pytest.skip("local pinned OCR installation absent")
    (tmp_path / "jpn.traineddata").symlink_to(trained)
    image = Image.new("RGB", (480, 110), "white")
    ImageDraw.Draw(image).text((15, 15), "TEST 123", fill="black",
                              font=ImageFont.truetype(str(font), 60))
    result = _ocr_tsv(image, tesseract_binary=binary, tessdata_dir=tmp_path,
                      language="jpn", page_segmentation_mode=6)
    assert "TEST" in result["text"] and "123" in result["text"]
    assert result["words"]


def test_tracking_windows_cover_long_sequence_with_overlap():
    from scripts.pipeline.bulk_track_observations import sequence_windows
    frames = list(range(64))
    windows = list(sequence_windows(frames, window_frames=8, overlap_frames=2))
    assert set(frame for _, window in windows for frame in window) == set(frames)
    assert all(len(window) <= 8 for _, window in windows)
    assert all(left[-2:] == right[:2] for (_, left), (_, right) in zip(windows, windows[1:]))


def test_window_merge_requires_unambiguous_shared_detection():
    from scripts.pipeline.bulk_track_observations import merge_track_windows

    def track(name, observations):
        return {"track_id": name, "frames": [
            {"capture_id": frame, "matched_object_observation_id": detection,
             "track_id": name} for frame, detection in observations
        ]}

    first = track("first", [("a", "d1"), ("b", "d2")])
    second = track("second", [("b", "d2"), ("c", "d3")])
    merged, conflicts = merge_track_windows([[first], [second]], ["a", "b", "c"])
    assert len(merged) == 1 and not conflicts
    assert [row["capture_id"] for row in merged[0]["frames"]] == ["a", "b", "c"]
    assert len(merged[0]["window_track_ids"]) == 2
    assert second["track_id"] == "second"  # Input evidence remains unchanged.

    competing = track("competing", [("b", "d2")])
    merged, conflicts = merge_track_windows([[first], [second, competing]], ["a", "b", "c"])
    assert len(merged) == 3 and len(conflicts) == 2
    assert all(row["state"] == "unresolved_overlap_identity" for row in conflicts)


def test_window_merge_does_not_join_on_class_or_proximity_alone():
    from scripts.pipeline.bulk_track_observations import merge_track_windows
    tracks = [[{"track_id": name, "frames": [{"capture_id": "a",
               "matched_object_observation_id": detection}]}]
              for name, detection in [("one", "d1"), ("two", "d2")]]
    merged, conflicts = merge_track_windows(tracks, ["a"])
    assert len(merged) == 2 and not conflicts


def test_mask_palette_excludes_background_and_retains_appearance_limits():
    import numpy as np
    from scripts.pipeline.bulk_mask_observations import masked_source_palette
    image = np.full((10, 10, 3), [255, 0, 0], dtype=np.uint8)
    mask = np.zeros((10, 10), dtype=bool)
    mask[:, :5] = True
    image[:, :5] = [180, 170, 150]
    image[0, :5] = 0
    image[9, :5] = 255
    result = masked_source_palette(image, mask)
    assert result["median_rgb"] == [180, 170, 150]
    assert result["masked_pixels"] == 50 and result["sampled_pixels"] == 40
    assert result["illumination_corrected"] is False
    assert result["occlusion_reviewed"] is False
    assert masked_source_palette(image, np.zeros_like(mask))["median_rgb"] is None


def test_bulk_planar_selection_requires_distinct_registered_views():
    from scripts.pipeline.reconstruct_planar_tracks import eligible_planar_tracks
    def track(name, semantic, captures):
        return {'track_id': name, 'semantic_candidates': [semantic], 'frames': [
            {'capture_id': capture, 'mask_area_px': 10} for capture in captures]}
    tracks = [track('a', 'facade_window', ['c1', 'c2']),
              track('b', 'storefront_sign', ['c1', 'c1']),
              track('c', 'vending_machine', ['c1', 'c2']),
              track('d', 'facade_wall', ['c3', 'c4'])]
    eligible, unresolved = eligible_planar_tracks(tracks, {'c1', 'c2'})
    assert eligible == [('a', ['facade_window'])]
    assert {row['track_id'] for row in unresolved} == {'b', 'd'}
    assert [row['available_camera_views'] for row in unresolved] == [1, 0]


def test_plane_consensus_rejects_background_and_deduplicates_pair_points():
    import numpy as np
    from scripts.pipeline.reconstruct_planar_tracks import robust_plane, triangulation_angles
    rng = np.random.default_rng(42)
    wall = np.column_stack((rng.uniform(-3, 3, 40), np.full(40, 5.), rng.uniform(0, 4, 40)))
    clutter = rng.uniform(-20, 20, (15, 3))
    points = np.concatenate([wall, wall, clutter])
    fit, support = robust_plane(points, threshold_m=.08)
    assert support['unique_points'] == 55
    assert support['inliers'] >= 40 and support['rejected_points'] >= 10
    assert support['inlier_fraction'] > .6
    assert abs(fit[1][1]) > .99
    assert support == robust_plane(points, threshold_m=.08)[1]
    angles = triangulation_angles(np.array([[0., 0., 10.], [0., 0., 1000.]]), [-1, 0, 0], [1, 0, 0])
    assert angles[0] > 3 and angles[1] < 3


def test_scattered_geometry_cannot_claim_a_supported_plane():
    import numpy as np
    from scripts.pipeline.reconstruct_planar_tracks import robust_plane
    points = np.random.default_rng(14).uniform(-10, 10, (100, 3))
    _, support = robust_plane(points, threshold_m=.08)
    assert support['inlier_fraction'] < .6


def test_surface_uses_shared_sfm_points_only_with_multiview_mask_support():
    from scripts.pipeline.reconstruct_planar_tracks import shared_sfm_surface_points
    cameras = {'a': {'solved_center_enu_m': [-1, 0, 0]},
               'b': {'solved_center_enu_m': [1, 0, 0]}}
    track = {'frames': [{'capture_id': cid, 'mask_size_px': [3, 3],
                         'foreground_runs': [[4, 1]], 'matched_object_observation_id': None}
                        for cid in cameras]}
    def point(name, xyz, pixels):
        return {'point_id': name, 'position_enu_m': xyz, 'observations': [
            {'capture_id': cid, 'source_image_pixel_px': pixel,
             'depth_m': 10, 'reprojection_error_px': .5} for cid, pixel in zip(cameras, pixels)]}
    sparse = {'points': [point('good', [0, 0, 10], [[1, 1], [1, 1]]),
                         point('one_view', [0, 0, 10], [[1, 1], [0, 0]]),
                         point('weak_angle', [0, 0, 1000], [[1, 1], [1, 1]])]}
    accepted = shared_sfm_surface_points(track, sparse, {}, cameras)
    assert [row['point_id'] for row in accepted] == ['good']
    assert accepted[0]['supporting_capture_ids'] == ['a', 'b']
