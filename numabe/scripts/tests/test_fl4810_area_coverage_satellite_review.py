"""Required-addition coverage review contracts for FL-4810."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.pipeline.compile_area_coverage_satellite_review import (
    SPATIAL_NOTES_SCHEMA,
    SpatialNotesStore,
    compile_review,
)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_satellite_review_exposes_zero_added_route_frames_without_promoting_tiles(
    tmp_path: Path,
):
    scope = tmp_path / "scope_spec.json"
    _write(scope, {
        "schema": "y92.connected_product_scope.v1", "scope_id": "test",
        "mapped_landmarks": [{"name": "Lawson", "source_ref": "node/1", "lonlat": [1.002, 2.0]}],
        "route_landmarks": [{"name": "Sakurazaka", "required_route_pass_points_lonlat": [[1.001, 2.0]]}],
        "operator_reported_search_zones": [{"zone_id": "laundry", "center_lonlat": [1.002, 2.0], "radius_m": 75, "note": "unresolved"}],
    })
    _write(tmp_path / "connected_scope.geojson", {
        "type": "FeatureCollection", "features": [
            {"properties": {"role": "aoi"}, "geometry": {"type": "Polygon", "coordinates": [[[1, 2], [1.003, 2], [1.003, 2.003], [1, 2]]] }},
            {"properties": {"role": "connected_playable_route", "length_m": 1000}, "geometry": {"type": "LineString", "coordinates": [[1, 2], [1.001, 2], [1.002, 2]]}},
        ],
    })
    _write(tmp_path / "scope_receipt.json", {"route_composition": {"preserved_base_route_length_m": 100.0}})
    capture = tmp_path / "capture.json"
    _write(capture, {"schema": "y92.capture_source_index.v1", "captures": [{"capture_id": "c1", "lat": 2.0, "lon": 1.0, "acquisition_route_projection": {"chainage_m": 50.0}}]})
    route = tmp_path / "route.json"
    _write(route, {"schema": "y92.route_capture_manifest.v1", "coverage": {"aggregate_covered_intervals_m": [[0, 60]]}})
    html = tmp_path / "review.html"
    receipt = tmp_path / "review.json"
    result = compile_review(scope_path=scope, capture_index_path=capture, route_manifest_path=route, output_html=html, output_receipt=receipt)
    assert result["counts"] == {
        "retained_cameras": 1,
        "cameras_on_required_additions": 0,
        "required_additions_have_street_imagery": False,
    }
    assert result["output"]["remote_tile_persistence"] is False
    assert result["output"]["evidence_role"] == "visual_verification_only"
    text = html.read_text(encoding="utf-8")
    assert "REQUIRED ADDITIONS: ZERO STREET-LEVEL CAMERAS" in text
    assert "World_Imagery" in text
    assert "Satellite pixels are not retained" in text
    assert "Sakurazaka" in text and "Lawson" in text and "laundry" in text
    assert "DRAW BOX" in text
    assert "/api/spatial-notes" in text
    assert "unclassified_spatial_note" in text
    assert "tile.openstreetmap.org" not in text


def test_personal_spatial_notes_save_only_geometry_and_text(tmp_path: Path):
    path = tmp_path / "personal_spatial_notes.json"
    store = SpatialNotesStore(path, scope_id="scope-1")
    receipt = store.empty_receipt()
    receipt["notes"].append({
        "note_id": "note-123-abcdef",
        "bounds_swen": [35.58, 139.67, 35.581, 139.671],
        "label": "rail_guard_fence",
        "note": "continuous fence along tracks",
        "custom_authoring_candidate": False,
    })
    saved = store.save(receipt)
    assert saved["schema"] == SPATIAL_NOTES_SCHEMA
    assert saved["source_policy"]["remote_imagery_pixels_retained"] is False
    assert saved["source_policy"]["reconstruction_evidence"] is False
    assert saved["notes"][0]["center_latlon"] == [35.5805, 139.6705]
    row = saved["notes"][0]
    assert not ({"image", "image_url", "tile_url", "pixels", "crop"} & row.keys())
    assert store.load() == saved
