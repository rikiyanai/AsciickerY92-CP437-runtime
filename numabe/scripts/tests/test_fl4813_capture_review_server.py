"""FL-4813 capture-review server contracts."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from scripts.pipeline.capture_review_server import (
    AGENT_SCHEMA,
    APP_HTML,
    GROUP_REVIEW_HTML,
    REVIEW_SCHEMA,
    CaptureReviewStore,
    build_server,
)
from scripts.pipeline.compile_capture_review_routing import canonical_bytes, empty_decisions
from scripts.pipeline.group_capture_review_crops import build_document as build_groups


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    image = tmp_path / "captures" / "frame.jpg"
    image.parent.mkdir()
    source = Image.new("RGB", (100, 50), (32, 64, 96))
    source.save(image, format="JPEG", quality=90)
    source_digest = hashlib.sha256(image.read_bytes()).hexdigest()
    index = tmp_path / "capture_index.json"
    index.write_text(
        json.dumps(
            {
                "schema": "y92.capture_source_index.v1",
                "captures": [
                    {
                        "capture_id": "mapillary_123",
                        "local_path": "captures/frame.jpg",
                        "sha256": source_digest,
                        "license": "CC-BY-SA-4.0",
                        "attribution": "operator / Mapillary",
                        "lat": 35.5,
                        "lon": 139.6,
                        "heading_deg": 90.0,
                        "projection_model": "perspective",
                        "width": 100,
                        "height": 50,
                        "sequence_id": "route-a",
                        "sequence_index": 3,
                        "acquisition_route_projection": {
                            "chainage_m": 200.0,
                            "lateral_m": 5.0,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "route_capture_manifest.json").write_text(
        json.dumps(
            {
                "schema": "y92.route_capture_manifest.v1",
                "route": {
                    "length_m": 428.0,
                    "coordinates_lonlat": [
                        [139.61, 35.51],
                        [139.60, 35.50],
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return index, tmp_path / "annotations.json"


def _annotation_payload() -> dict:
    return {
        "schema": REVIEW_SCHEMA,
        "annotations": [
            {
                "annotation_id": "ann-1",
                "capture_id": "mapillary_123",
                "box_norm": [0.1, 0.2, 0.3, 0.4],
                "label": "facade",
                "parent_feature_id": "way/42",
                "evidence_role": "correspondence_candidate",
                "note": "Visible ground contact and two vertical edges.",
            }
        ],
    }


def _product_scope_fixture(tmp_path: Path) -> Path:
    scope = tmp_path / "scope_spec.json"
    scope.write_text(
        json.dumps(
            {
                "schema": "y92.connected_product_scope.v1",
                "scope_id": "expanded-numabe",
                "route_order": ["station", "home", "sakurazaka", "lawson_laundry"],
                "coverage_state": {
                    "existing_428m_capture_manifest": "retained_as_partial_evidence_only",
                    "sakurazaka_imagery": "not_yet_accepted",
                    "lawson_coin_laundry_extension_imagery": "not_yet_surveyed",
                    "required_next_receipt": "expanded route imagery survey and route_capture_manifest",
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "connected_scope.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "role": "connected_playable_route",
                            "length_m": 1041.762,
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [139.60, 35.50],
                                [139.61, 35.51],
                                [139.62, 35.52],
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return scope


def test_store_enriches_annotation_with_frozen_pose_and_source(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    store = CaptureReviewStore(index, annotations)
    receipt = store.save_receipt(_annotation_payload())
    row = receipt["annotations"][0]
    assert row["review_state"] == "operator_authored"
    assert row["capture_snapshot"]["sha256"] == hashlib.sha256(
        (tmp_path / "captures/frame.jpg").read_bytes()
    ).hexdigest()
    assert row["capture_snapshot"]["lat"] == 35.5
    assert row["capture_snapshot"]["heading_deg"] == 90.0
    assert row["capture_snapshot"]["acquisition_route_projection"]["chainage_m"] == 200.0
    assert receipt["promotion"] == {
        "camera_registration_accepted": False,
        "world_ir_promotion_ready": False,
    }
    assert row["crop"]["pixel_box_xywh"] == [10, 10, 30, 20]
    assert row["crop"]["width"] == 30
    assert row["crop"]["height"] == 20
    assert Path(row["crop"]["path"]).is_file()
    assert hashlib.sha256(Path(row["crop"]["path"]).read_bytes()).hexdigest() == row[
        "crop"
    ]["sha256"]
    assert json.loads(annotations.read_text())["annotations"][0]["capture_id"] == "mapillary_123"


@pytest.mark.parametrize(
    "box",
    ([0.9, 0.2, 0.2, 0.2], [-0.1, 0.2, 0.2, 0.2], [0.1, 0.2, 0.0, 0.2]),
)
def test_store_rejects_boxes_outside_source_image(tmp_path: Path, box: list[float]):
    index, annotations = _fixture(tmp_path)
    store = CaptureReviewStore(index, annotations)
    payload = _annotation_payload()
    payload["annotations"][0]["box_norm"] = box
    with pytest.raises(ValueError, match="outside the image"):
        store.save_receipt(payload)


def test_store_refuses_capture_path_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside.jpg"
    outside.write_bytes(b"outside")
    index = tmp_path / "capture_index.json"
    index.write_text(
        json.dumps(
            {
                "schema": "y92.capture_source_index.v1",
                "captures": [
                    {"capture_id": "escape", "local_path": "../outside.jpg"}
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="escapes the bundle root"):
        CaptureReviewStore(index, tmp_path / "annotations.json")


def test_store_refuses_source_digest_mismatch_before_crop(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    payload = json.loads(index.read_text())
    payload["captures"][0]["sha256"] = "0" * 64
    index.write_text(json.dumps(payload), encoding="utf-8")
    store = CaptureReviewStore(index, annotations)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        store.save_receipt(_annotation_payload())


def test_store_rejects_unsafe_annotation_id(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    payload = _annotation_payload()
    payload["annotations"][0]["annotation_id"] = "../escape"
    with pytest.raises(ValueError, match="annotation_id is unsafe"):
        CaptureReviewStore(index, annotations).save_receipt(payload)


def test_existing_receipt_is_enriched_without_losing_operator_fields(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    original = _annotation_payload()
    original["created_at"] = "2026-09-15T00:00:00+00:00"
    original["snapshot"] = {
        "schema": "y92.capture_review_snapshot.v1",
        "frozen_utc": "2026-09-15T00:00:00+00:00",
        "source_receipt_sha256": "0" * 64,
        "path_policy": "crop paths are relative to this snapshot directory",
        "annotation_count": 1,
        "verified_crop_count": 1,
    }
    annotations.write_text(json.dumps(original), encoding="utf-8")
    receipt = CaptureReviewStore(index, annotations).load_receipt()
    row = receipt["annotations"][0]
    assert row["label"] == "facade"
    assert row["note"] == "Visible ground contact and two vertical edges."
    assert row["crop"]["url"] == "/api/crop/ann-1"
    assert receipt["snapshot"] == original["snapshot"]
    assert json.loads(annotations.read_text())["annotations"][0]["crop"]["width"] == 30
    first_bytes = annotations.read_bytes()
    first_crop_sha = row["crop"]["sha256"]
    second = CaptureReviewStore(index, annotations).load_receipt()
    assert annotations.read_bytes() == first_bytes
    assert second["annotations"][0]["crop"]["sha256"] == first_crop_sha


def test_loopback_http_api_serves_local_image_and_saves_receipt(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    store = CaptureReviewStore(index, annotations)
    server = build_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/config") as response:
            config = json.load(response)
        assert config["captures"][0]["image_url"] == "/api/image/mapillary_123"
        assert "asset_uri" not in config["captures"][0]
        assert config["route"]["length_m"] == 428.0
        assert config["route"]["coordinates_lonlat"][0] == [139.61, 35.51]
        fixture_items = config["suggestions"]["groups"][0]["items"]
        utility_pole = next(row for row in fixture_items if row["label"] == "utility_pole")
        assert utility_pole["template_id"] == "jp.urban.utility_pole_concrete_v1"
        with urllib.request.urlopen(f"{base}/api/image/mapillary_123") as response:
            assert response.read() == (tmp_path / "captures/frame.jpg").read_bytes()
        request = urllib.request.Request(
            f"{base}/api/annotations",
            data=json.dumps(_annotation_payload()).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            receipt = json.load(response)
        assert receipt["annotations"][0]["capture_snapshot"]["lon"] == 139.6
        with urllib.request.urlopen(f"{base}/api/agent") as response:
            agent = json.load(response)
        assert agent["schema"] == AGENT_SCHEMA
        assert agent["annotations"][0]["camera"]["heading_deg"] == 90.0
        assert agent["annotations"][0]["route_projection"]["chainage_m"] == 200.0
        assert agent["annotations"][0]["crop"]["pixel_box_xywh"] == [10, 10, 30, 20]
        assert agent["annotations"][0]["source_image_url"] == "/api/image/mapillary_123"
        with urllib.request.urlopen(f"{base}/api/crop/ann-1") as response:
            crop_bytes = response.read()
            assert response.headers.get_content_type() == "image/png"
        with Image.open(io.BytesIO(crop_bytes)) as crop:
            assert crop.size == (30, 20)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_api_rejects_unknown_capture(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    server = build_server(CaptureReviewStore(index, annotations), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/image/unknown"
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(url)
        assert caught.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ui_contract_autosaves_drag_and_exposes_suggestions_and_agent_link():
    assert "await createAnnotationFromDraft()" in APP_HTML
    assert "field edits auto-save after 350 ms" in APP_HTML
    assert "then clears the type so it cannot leak into the next box" in APP_HTML
    assert 'id="suggestions"' in APP_HTML
    assert 'href="/api/agent"' in APP_HTML
    assert 'href="/review"' in APP_HTML


def test_group_review_ui_renders_pose_map_and_partial_coverage_warning():
    assert 'id="coverage"' in GROUP_REVIEW_HTML
    assert 'id="map"' in GROUP_REVIEW_HTML
    assert "PARTIAL IMAGERY ONLY" in GROUP_REVIEW_HTML
    assert "heading_deg" in GROUP_REVIEW_HTML
    assert "chainage_m" in GROUP_REVIEW_HTML
    assert "lateral_m" in GROUP_REVIEW_HTML
    assert "source frame" in GROUP_REVIEW_HTML


def test_store_reads_current_route_manifest_measured_length(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    manifest = tmp_path / "route_capture_manifest.json"
    document = json.loads(manifest.read_text())
    document["route"]["measured_length_m"] = document["route"].pop("length_m")
    manifest.write_text(json.dumps(document), encoding="utf-8")
    assert CaptureReviewStore(index, annotations).public_config()["route"]["length_m"] == 428.0


def test_group_review_api_autosaves_and_rebuilds_routing(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    CaptureReviewStore(index, annotations).save_receipt(_annotation_payload())
    groups_path = tmp_path / "groups.json"
    groups_path.write_bytes(canonical_bytes(build_groups(annotations)))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_bytes(canonical_bytes(empty_decisions(groups_path)))
    routing_path = tmp_path / "routing.json"
    store = CaptureReviewStore(
        index,
        annotations,
        groups_path=groups_path,
        decisions_path=decisions_path,
        routing_path=routing_path,
        product_scope_path=_product_scope_fixture(tmp_path),
    )
    group_id = store.group_review_view()["groups"][0]["group_id"]
    decisions = store.load_semantic_decisions()
    decisions["group_reviews"] = [{
        "group_id": group_id,
        "action": "correct",
        "semantic_label": "storefront_sign",
        "custom_extraction_required": True,
        "custom_meta": {
            "asset_key": "specific_storefront",
            "authoring_kind": "storefront_identity",
            "reason": "specific lettering",
        },
        "correction_note": "operator correction",
    }]
    saved = store.save_semantic_decisions(decisions)
    assert saved["group_reviews"][0]["action"] == "correct"
    routing = store.routing_view()
    assert routing["counts"]["custom_authoring"] == 1
    assert routing["custom_authoring_queue"][0]["custom_meta"]["asset_key"] == (
        "specific_storefront"
    )


def test_group_review_api_separates_capture_route_from_expanded_product_scope(
    tmp_path: Path,
):
    index, annotations = _fixture(tmp_path)
    CaptureReviewStore(index, annotations).save_receipt(_annotation_payload())
    groups_path = tmp_path / "groups.json"
    groups_path.write_bytes(canonical_bytes(build_groups(annotations)))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_bytes(canonical_bytes(empty_decisions(groups_path)))
    store = CaptureReviewStore(
        index,
        annotations,
        groups_path=groups_path,
        decisions_path=decisions_path,
        routing_path=tmp_path / "routing.json",
        product_scope_path=_product_scope_fixture(tmp_path),
    )
    queue = store.group_review_view()
    assert queue["capture_route"]["length_m"] == 428.0
    assert queue["product_scope"]["route"]["length_m"] == 1041.762
    assert queue["product_scope"]["coverage_state"]["sakurazaka_imagery"] == (
        "not_yet_accepted"
    )
    member = queue["groups"][0]["members"][0]
    assert member["camera"]["lat"] == 35.5
    assert member["camera"]["lon"] == 139.6
    assert member["camera"]["heading_deg"] == 90.0
    assert member["route_projection"]["chainage_m"] == 200.0
    assert member["product_route_projection"]["chainage_m"] == pytest.approx(0.0)
    assert member["product_route_projection"]["lateral_m"] == pytest.approx(0.0)
    assert member["source_image_url"] == "/api/image/mapillary_123"


def test_rejected_similarity_group_expands_to_individual_review_cards(tmp_path: Path):
    index, annotations = _fixture(tmp_path)
    payload = _annotation_payload()
    second = dict(payload["annotations"][0])
    second["annotation_id"] = "ann-2"
    payload["annotations"].append(second)
    CaptureReviewStore(index, annotations).save_receipt(payload)
    groups_path = tmp_path / "groups.json"
    groups_path.write_bytes(canonical_bytes(build_groups(annotations, threshold=0.01)))
    decisions_path = tmp_path / "decisions.json"
    decisions = empty_decisions(groups_path)
    group_id = json.loads(groups_path.read_text())["groups"][0]["group_id"]
    decisions["group_reviews"] = [{
        "group_id": group_id,
        "action": "reject_similarity",
        "semantic_label": None,
        "custom_extraction_required": None,
        "custom_meta": None,
        "correction_note": "not the same fixture class",
    }]
    decisions_path.write_bytes(canonical_bytes(decisions))
    store = CaptureReviewStore(
        index,
        annotations,
        groups_path=groups_path,
        decisions_path=decisions_path,
        routing_path=tmp_path / "routing.json",
    )
    cards = store.group_review_view()["groups"]
    assert len(cards) == 2
    assert {card["review_scope"] for card in cards} == {"annotation"}
    assert {card["annotation_id"] for card in cards} == {"ann-1", "ann-2"}


def test_browser_drag_and_field_edit_autosave(tmp_path: Path, page):
    index, annotations = _fixture(tmp_path)
    server = build_server(CaptureReviewStore(index, annotations), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page.goto(base)
        page.wait_for_function("document.querySelector('#image').naturalWidth === 100")
        page.get_by_role("button", name="utility pole", exact=True).click()
        assert page.locator("#label").input_value() == "utility_pole"
        overlay = page.locator("#overlay").bounding_box()
        image_rect = page.evaluate("imageContentRect()")
        assert overlay is not None
        start_x = overlay["x"] + image_rect["x"] + image_rect["w"] * 0.2
        start_y = overlay["y"] + image_rect["y"] + image_rect["h"] * 0.2
        end_x = overlay["x"] + image_rect["x"] + image_rect["w"] * 0.5
        end_y = overlay["y"] + image_rect["y"] + image_rect["h"] * 0.6
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(end_x, end_y)
        page.mouse.up()
        assert page.locator("#label").input_value() == ""
        page.wait_for_function(
            "async () => (await (await fetch('/api/annotations')).json()).annotations.length === 1"
        )
        receipt = json.loads(annotations.read_text())
        assert receipt["annotations"][0]["label"] == "utility_pole"
        assert receipt["annotations"][0]["crop"]["width"] == 30
        page.wait_for_function("() => document.querySelector('#label').value === ''")
        page.mouse.move(
            overlay["x"] + image_rect["x"] + image_rect["w"] * 0.6,
            overlay["y"] + image_rect["y"] + image_rect["h"] * 0.2,
        )
        page.mouse.down()
        page.mouse.move(
            overlay["x"] + image_rect["x"] + image_rect["w"] * 0.8,
            overlay["y"] + image_rect["y"] + image_rect["h"] * 0.4,
        )
        page.mouse.up()
        page.wait_for_function(
            "async () => (await (await fetch('/api/annotations')).json()).annotations.length === 2"
        )
        page.wait_for_function("() => document.querySelector('#label').value === ''")
        receipt = json.loads(annotations.read_text())
        assert [row["label"] for row in receipt["annotations"]] == [
            "utility_pole",
            "unclassified_fixture",
        ]
        page.locator("[data-select]").first.click()
        page.locator("#note").fill("agent-visible fixture note")
        assert page.locator("#status").text_content() == "field change pending…"
        page.wait_for_function(
            "() => document.querySelector('#status').textContent.startsWith('saved locally')"
        )
        assert json.loads(annotations.read_text())["annotations"][0]["note"] == (
            "agent-visible fixture note"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_browser_group_yes_autosaves_routing(tmp_path: Path, page):
    index, annotations = _fixture(tmp_path)
    CaptureReviewStore(index, annotations).save_receipt(_annotation_payload())
    groups_path = tmp_path / "groups.json"
    groups_path.write_bytes(canonical_bytes(build_groups(annotations)))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_bytes(canonical_bytes(empty_decisions(groups_path)))
    routing_path = tmp_path / "routing.json"
    store = CaptureReviewStore(
        index,
        annotations,
        groups_path=groups_path,
        decisions_path=decisions_path,
        routing_path=routing_path,
        product_scope_path=_product_scope_fixture(tmp_path),
    )
    server = build_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page.goto(base + "/review")
        page.wait_for_function("document.querySelector('#counter').textContent === '1/1'")
        assert "PARTIAL IMAGERY ONLY" in page.locator("#coverage").text_content()
        assert "428.000 m capture route" in page.locator("#coverage").text_content()
        assert "1041.762 m product route" in page.locator("#coverage").text_content()
        location = page.locator("#location").text_content()
        assert "lat: 35.5000000" in location
        assert "lon: 139.6000000" in location
        assert "heading_deg: 90.0" in location
        assert "capture_route_chainage_m: 200.00" in location
        assert "product_route_chainage_m: 0.00" in location
        assert page.locator("#map").evaluate("canvas => canvas.width > 0")
        page.locator("#label").fill("traffic_sign")
        page.get_by_role("button", name="YES / accept class").click()
        page.wait_for_function(
            "async () => (await (await fetch('/api/routing')).json()).counts.generic_template_instances === 1"
        )
        receipt = json.loads(routing_path.read_text())
        assert receipt["generic_template_instances"][0]["semantic_label"] == "traffic_sign"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
