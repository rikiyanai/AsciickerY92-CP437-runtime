"""Authoring health and transport ownership after FL-4734 deletion."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_health_reports_one_final_frame_and_product_readiness() -> None:
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    assert "GET /api/v1/health HTTP/1.1" in http
    assert "product_ready" in http
    assert "termpp_final_ready" in http
    assert "editor_scene_ready" not in http
    assert '\\"frame_surfaces\\":[\\"termpp_final\\"]' in http


def test_runtime_intent_is_the_only_transport_mutation_endpoint() -> None:
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    assert 'POST /api/v1/runtime-intent HTTP/1.1' in http
    assert 'AuthorizedSession(origin, "runtime-intent.write")' in http
    assert "service_->SubmitPhysicalIntent(" in http
    for retired in (
        "POST /api/v1/action.commit HTTP/1.1",
        "POST /api/v1/action.preview HTTP/1.1",
        "CommitDirectAction",
        "CommitPreviewedAction",
    ):
        assert retired not in http


def test_runtime_snapshot_and_witness_bind_final_frame_identity() -> None:
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    service = read("apps/asciicker-presenter/asciiid_application_service.cpp")
    assert 'GET /api/v1/runtime-snapshot HTTP/1.1' in http
    assert "CurrentRuntimeSnapshot" in service
    for field in (
        "finalized_frame_id",
        "finalized_frame_width",
        "finalized_frame_height",
        "finalized_frame_sha256",
        "view_state_sha256",
        "witness_sha256",
    ):
        assert field in service


def test_frame_transport_exposes_termpp_final_only() -> None:
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    transport = read("web/asciiid-authoring-shell/src/transport.ts")
    assert 'GET /api/v1/frames/termpp-final.rgba HTTP/1.1' in http
    assert "frames/editor-scene.rgba" not in http
    assert 'NativeFrameSurface = "termpp_final"' in transport


def test_event_recorder_is_observation_only() -> None:
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    recorder = read("web/asciiid-authoring-shell/src/uiEventRecorder.ts")
    assert 'POST /api/v1/event.record HTTP/1.1' in http
    assert 'GET /api/v1/events HTTP/1.1' in http
    assert "Product input must remain usable when observation fails" in recorder
    assert "/api/v1/action.commit" not in recorder


def test_offline_preview_has_no_commit_state_or_transport_owner() -> None:
    header = read("apps/asciicker-presenter/asciiid_application_service.h")
    service = read("apps/asciicker-presenter/asciiid_application_service.cpp")
    http = read("apps/asciicker-presenter/asciiid_authoring_http.cpp")
    assert "AsciiidApplicationService::PreviewAction" in service
    assert "CommitPreviewedAction" not in service
    assert "ArmedPreviewTokenForTesting" not in header
    assert "armed_preview_token_" not in header
    assert "action.preview" not in http
