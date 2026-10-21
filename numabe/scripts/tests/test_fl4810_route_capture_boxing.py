import json
from pathlib import Path

import pytest

from scripts.pipeline import run_route_capture_boxing as boxing


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    spec = tmp_path / "probe.json"
    route = tmp_path / "route.geojson"
    scope = tmp_path / "scope.json"
    spec.write_text(json.dumps({"providers": {"mapillary": {}}}))
    route.write_text("{}")
    scope.write_text("{}")
    return spec, route, scope


def test_boxing_front_door_persists_refusal_and_names_credential_env(
    tmp_path: Path, monkeypatch
):
    spec, route, scope = _inputs(tmp_path)
    monkeypatch.setattr(
        boxing,
        "run_survey",
        lambda *_args, **_kwargs: {
            "providers": {
                "mapillary": {
                    "state": "UNKNOWN_PENDING_ACCESS",
                    "access": {
                        "refusal": "Invalid OAuth 2.0 Access Token",
                        "access_token_env": "MAPILLARY_ACCESS_TOKEN",
                    },
                }
            }
        },
    )
    workspace = tmp_path / "work"
    with pytest.raises(RuntimeError, match="Set MAPILLARY_ACCESS_TOKEN"):
        boxing.prepare(
            probe_spec_path=spec,
            route_path=route,
            product_scope_path=scope,
            workspace=workspace,
            min_chainage_m=420.0,
            max_chainage_m=None,
            max_lateral_m=25.0,
            workers=1,
        )
    assert json.loads((workspace / "imagery_survey.json").read_text())["providers"]


def test_boxing_front_door_passes_segment_chainage_to_acquisition(
    tmp_path: Path, monkeypatch
):
    spec, route, scope = _inputs(tmp_path)
    monkeypatch.setattr(
        boxing,
        "run_survey",
        lambda *_args, **_kwargs: {
            "providers": {"mapillary": {"state": "PRESENT", "access": {}}}
        },
    )
    seen = {}

    def fake_acquire(**kwargs):
        seen.update(kwargs)
        return {"counts": {"eligible_route_frames": 3, "sequences": 1}}

    monkeypatch.setattr(boxing, "acquire", fake_acquire)
    monkeypatch.setattr(
        boxing,
        "compile_manifest",
        lambda *_args, **_kwargs: {
            "coverage": {"full_route_reconstruction_ready": False}
        },
    )
    result = boxing.prepare(
        probe_spec_path=spec,
        route_path=route,
        product_scope_path=scope,
        workspace=tmp_path / "work",
        min_chainage_m=420.0,
        max_chainage_m=900.0,
        max_lateral_m=25.0,
        workers=2,
    )
    assert seen["min_chainage_m"] == 420.0
    assert seen["max_chainage_m"] == 900.0
    assert seen["max_lateral_m"] == 25.0
    assert result["capture_index"]["counts"]["eligible_route_frames"] == 3
