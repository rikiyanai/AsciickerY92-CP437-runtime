import hashlib
import json
from pathlib import Path

import pytest

from scripts.pipeline.route_capture_manifest import SOURCE_SCHEMA, compile_manifest
from scripts.pipeline.street_imagery_probe import HttpLog, MapillaryAdapter, _redact_url


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "assets/source_bundles/numabe_home_to_station_20260912"
EXPANDED_BUNDLE = ROOT / "assets/source_bundles/numabe_home_to_station_sakurazaka_20260913"


def test_mapillary_probe_follows_pages_and_redacts_credentials():
    class FakeHttp:
        def __init__(self):
            self.calls = []

        def json(self, url, **_kwargs):
            self.calls.append(url)
            if len(self.calls) == 1:
                return HttpLog("GET", url, 200), {
                    "data": [{
                        "id": "first",
                        "geometry": {"coordinates": [139.0, 35.0]},
                        "sequence": "sequence-a",
                    }],
                    "paging": {"next": "https://graph.mapillary.com/images?after=cursor&access_token=secret"},
                }
            return HttpLog("GET", _redact_url(url), 200), {
                "data": [{
                    "id": "second",
                    "geometry": {"coordinates": [139.00001, 35.00001]},
                    "sequence": "sequence-a",
                }],
                "paging": {},
            }

        def request(self, *_args, **_kwargs):
            raise AssertionError("tilesets=[] must suppress tile probes")

    http = FakeHttp()
    adapter = MapillaryAdapter(http, {
        "access_token": "secret",
        "limit": 1,
        "max_pages": 3,
        "tilesets": [],
    })
    pictures, logs, refusal = adapter.radius_query(35.0, 139.0, 100.0)

    assert [picture.picture_id for picture in pictures] == ["first", "second"]
    assert len(logs) == 2
    assert refusal is None
    assert "access_token=secret" in http.calls[1]
    assert "secret" not in _redact_url(http.calls[1])
    assert "%3Credacted%3E" in _redact_url(http.calls[1])


def test_mapillary_public_web_client_bootstrap_is_explicit_and_redacted():
    class FakeHttp:
        def __init__(self):
            self.graph_headers = None

        def request(self, url, **kwargs):
            note = kwargs.get("note")
            if url == MapillaryAdapter.WEB_APP:
                payload = b'<script src="main.abc123.js"></script>'
            else:
                payload = b'window.clientToken="MLY|public-client-token-value";'
            return HttpLog("GET", url, 200, note=note), payload

        def json(self, url, **kwargs):
            self.graph_headers = kwargs.get("headers")
            return HttpLog("GET", _redact_url(url), 200), {
                "data": [{
                    "id": "one",
                    "geometry": {"coordinates": [139.0, 35.0]},
                    "sequence": "sequence-a",
                }],
                "paging": {},
            }

    http = FakeHttp()
    adapter = MapillaryAdapter(http, {
        "allow_public_web_client_token": True,
        "tilesets": [],
    })
    pictures, logs, refusal = adapter.bbox_query([138.9, 34.9, 139.1, 35.1])

    assert [picture.picture_id for picture in pictures] == ["one"]
    assert refusal is None
    assert [log.note for log in logs[:2]] == [
        "Mapillary public web-client bootstrap shell",
        "Mapillary public web-client bootstrap bundle",
    ]
    assert http.graph_headers == {
        "Authorization": "OAuth MLY|public-client-token-value"
    }
    assert adapter.access_facts()["token_source"] == "mapillary_public_web_client"
    assert adapter.access_facts()["token_retained"] is False


def _route(path: Path, metres: int = 30, role: str = "walking_route") -> None:
    end_lon = metres / 111_195.0
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"role": role, "length_m": metres},
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [end_lon, 0.0]]},
        }],
    }))


def _source_index(tmp_path: Path, count: int = 11, *, bad_digest: bool = False) -> Path:
    captures = []
    for index in range(count):
        asset = tmp_path / f"frame-{index:03d}.jpg"
        asset.write_bytes(f"frame {index}".encode())
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        captures.append({
            "capture_id": f"f{index:03d}",
            "provider": "test sequence",
            "source_uri": f"https://example.invalid/page/{index}",
            "asset_uri": f"https://example.invalid/frame/{index}.jpg",
            "timestamp": f"2026-01-01T00:00:{index:02d}Z",
            "sequence_id": "walk-1",
            "sequence_index": index,
            "lon": (index * 3.0) / 111_195.0,
            "lat": 0.0,
            "altitude_m": None,
            "horizontal_accuracy_m": 2.0,
            "vertical_accuracy_m": None,
            "heading_deg": 90.0,
            "pitch_deg": None,
            "roll_deg": None,
            "orientation_state": "heading_measured_pitch_roll_unavailable",
            "license": "CC-BY-4.0",
            "license_uri": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Test Author, CC BY 4.0",
            "width": 100,
            "height": 80,
            "camera_model": "test",
            "focal_length_mm": None,
            "focal_35mm": 24.0,
            "horizontal_fov_deg": None,
            "intrinsics_state": "measured_35mm_equivalent",
            "sha256": "0" * 64 if bad_digest and index == 0 else digest,
            "size_bytes": asset.stat().st_size,
            "retention_permitted": True,
            "local_path": asset.name,
            "provider_detections": [],
            "provider_detection_state": "provider_does_not_supply",
        })
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"schema": SOURCE_SCHEMA, "captures": captures}))
    return source


def test_continuous_three_metre_sequence_is_route_ready(tmp_path):
    route = tmp_path / "route.geojson"
    _route(route)
    document = compile_manifest(route, [_source_index(tmp_path)], tmp_path / "manifest.json")
    assert document["verdict"] == "READY"
    assert document["coverage"]["full_route_reconstruction_ready"] is True
    assert document["coverage"]["home_endpoint_covered"] is True
    assert document["coverage"]["station_endpoint_covered"] is True
    assert document["counts"]["selected_keyframes"] == 11
    assert all(edge["overlap_candidate"] for edge in document["overlap_graph"]["edges"])


def test_expanded_connected_product_route_is_a_supported_capture_spine(tmp_path):
    route = tmp_path / "route.geojson"
    _route(route, role="connected_playable_route")
    document = compile_manifest(route, [_source_index(tmp_path)], tmp_path / "manifest.json")
    assert document["route"]["measured_length_m"] == pytest.approx(30.0, abs=0.1)
    assert document["coverage"]["full_route_reconstruction_ready"] is True


def test_local_asset_digest_is_a_hard_capture_gate(tmp_path):
    route = tmp_path / "route.geojson"
    _route(route)
    document = compile_manifest(
        route, [_source_index(tmp_path, bad_digest=True)], tmp_path / "manifest.json"
    )
    assert document["counts"]["rejected_captures"] == 1
    assert document["rejected"][0]["reasons"] == ["local_asset_digest_mismatch"]


def test_manifest_bytes_are_deterministic(tmp_path):
    route = tmp_path / "route.geojson"
    _route(route)
    source = _source_index(tmp_path)
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    compile_manifest(route, [source], first)
    compile_manifest(route, [source], second)
    assert first.read_bytes() == second.read_bytes()


def test_current_numabe_corpus_records_partial_mapillary_route_coverage():
    document = json.loads((BUNDLE / "route_capture_manifest.json").read_text())
    assert document["counts"] == {
        "accepted_captures": 212,
        "input_captures": 212,
        "locally_verified_captures": 212,
        "overlap_candidate_components": 4,
        "rejected_captures": 0,
        "selected_keyframes": 175,
        "sequences": 17,
    }
    assert document["coverage"]["station_endpoint_covered"] is True
    assert document["coverage"]["home_endpoint_covered"] is False
    assert document["coverage"]["coverage_fraction"] == pytest.approx(0.340904)
    assert document["coverage"]["aggregate_coverage_fraction"] == pytest.approx(0.406135)
    assert document["coverage"]["longest_gap_m"] == pytest.approx(282.306)
    assert document["coverage"]["full_route_reconstruction_ready"] is False
    assert all("provider_detections" in row for row in document["captures"])
    mapillary = json.loads((BUNDLE / "mapillary_capture_source_index.json").read_text())
    assert mapillary["counts"] == {
        "eligible_route_frames": 202,
        "sequences": 15,
        "survey_pictures": 2324,
    }
    assert all(
        capture["provider"] == "Mapillary"
        and capture["license"] == "CC-BY-SA-4.0"
        and capture["retention_permitted"] is True
        for capture in mapillary["captures"]
    )


def test_hero_source_review_admits_mid_evidence_without_inventing_home_coverage():
    review = json.loads((EXPANDED_BUNDLE / "hero_source_coverage_review.json").read_text())
    targets = {item["id"]: item for item in review["hero_targets"]}

    for source in review["inputs"].values():
        if not isinstance(source, dict) or "path" not in source:
            continue
        source_path = EXPANDED_BUNDLE / source["path"]
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source["sha256"]

    assert targets["home_block"]["source_coverage"] is False
    assert targets["home_block"]["acquisition_state"] == "operator_capture_required"
    assert targets["home_block"]["geometry_state"] == "no_accepted_camera_or_facade_geometry"

    mid = targets["mid_route_landmark"]
    assert mid["source_coverage"] is True
    assert mid["reconstruction_route"] == "single_image_plus_OSM_footprint_ground_and_facade_priors"
    assert mid["geometry_state"] == "source_observation_admitted_no_accepted_camera_or_facade_geometry"
    assert mid["parent_correspondence_state"].startswith("unresolved_pending_")

    capture_index = json.loads((EXPANDED_BUNDLE / "mapillary_capture_source_index.json").read_text())
    captures = {item["capture_id"]: item for item in capture_index["captures"]}
    for admitted in mid["admitted_source_observations"]:
        retained = captures[admitted["capture_id"]]
        local_path = EXPANDED_BUNDLE / retained["local_path"]
        assert local_path.is_file()
        assert hashlib.sha256(local_path.read_bytes()).hexdigest() == admitted["sha256"]
        assert retained["license"] == "CC-BY-SA-4.0"
        assert retained["retention_permitted"] is True

    requirements = json.loads((EXPANDED_BUNDLE / "e2e_requirements.json").read_text())
    required_targets = {item["id"]: item for item in requirements["hero_facades"]}
    assert required_targets["home_block"]["source_coverage"] is False
    assert required_targets["mid_route_landmark"]["source_coverage"] is True
    assert requirements["compiler_inputs"]["hero_source_coverage_review"] == "hero_source_coverage_review.json"


def test_exact_hero_surveys_preserve_the_public_source_gap_and_redact_credentials():
    home_text = (EXPANDED_BUNDLE / "hero_home_imagery_probe.survey.json").read_text()
    mid_text = (EXPANDED_BUNDLE / "hero_mid_imagery_probe.survey.json").read_text()
    assert "MLY|" not in home_text + mid_text
    assert "Authorization" not in home_text + mid_text

    home = json.loads(home_text)
    mid = json.loads(mid_text)
    assert home["providers"]["mapillary"]["viewpoints"]["pictures_in_range"] == 0
    assert all(
        home["providers"][provider]["state"] == "ABSENT_IN_SURVEYED_RADIUS"
        for provider in ("kartaview", "panoramax", "wikimedia_commons")
    )
    assert mid["providers"]["mapillary"]["viewpoints"]["pictures_in_range"] == 17
    assert mid["providers"]["mapillary"]["viewpoints"]["distinct_viewpoints"] == 11
