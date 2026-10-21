"""FL-4814 static route-composition gate regression tests."""

import json
from pathlib import Path

from scripts.pipeline import audit_route_view_composition as A


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_route_composition_rejects_edge_on_required_place(monkeypatch, tmp_path):
    scale = 2.25
    monkeypatch.setattr(A, "_project_route", lambda *_args: (
        [(0.0, 0.0), (225.0, 0.0)], {}
    ))
    metadata = _write(tmp_path / "metadata.json", {"content_scale": scale})
    route = _write(tmp_path / "route.json", {"unused": True})
    requirements = _write(tmp_path / "requirements.json", {
        "route_composition_requirements": {
            "minimum_place_facing_dot": 0.15,
            "required_places": [{
                "feature_id": "feature:place:test",
                "rendered_text_required": True,
                "max_route_offset_m": 10.0,
            }],
            "required_infrastructure_counts": {
                "station_platform_proposal": 1,
                "station_entrance_assembly_proposal": 1,
            },
            "station_assembly_semantics": [
                "station_platform_proposal", "station_entrance_assembly_proposal"
            ],
            "station_assembly_max_span_m": 20.0,
            "station_identity_labels_minimum": 1,
            "streetscape_route_coverage": {
                "utility_pole": {
                    "bin_m": 50.0, "max_route_offset_m": 10.0,
                    "minimum_covered_fraction": 1.0,
                }
            },
        }
    })
    places = {
        "instances": [{
            "feature_id": "feature:place:test", "label": "TEST",
            "representation_kind": "commercial_place_front_proposal",
            "rendered_text": "TEST", "front_anchor_world_xy": [45.0, 9.0],
            "front_presentation_normal_xy": [0.0, -1.0],
        }]
    }
    place_path = _write(tmp_path / "places.json", places)
    infrastructure = _write(tmp_path / "infra.json", {"instances": [
        {"semantic_class": "station_platform_proposal",
         "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, 180, 5, 0, 1]},
        {"semantic_class": "station_entrance_assembly_proposal",
         "dimensions": {"rendered_identity_text": "TEST STA"},
         "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, 190, 6, 0, 1]},
    ]})
    poles = []
    for x in (10.0, 70.0, 130.0, 190.0):
        poles.append({
            "feature_id": f"style:utility_pole:{x}",
            "streetscape_kind": "utility_pole",
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, x, 5, 0, 1],
        })
    streetscape = _write(tmp_path / "street.json", {"instances": poles})

    passed = A.audit(
        route_path=route, terrain_metadata_path=metadata,
        place_front_receipt_path=place_path,
        infrastructure_receipt_path=infrastructure,
        streetscape_receipt_path=streetscape,
        requirements_path=requirements, output_path=tmp_path / "pass.json",
    )
    assert passed["gates"]["static_route_composition_ready"] is True
    assert passed["gates"]["station_identity_geometry_present"] is True
    assert passed["gates"]["headed_classic_visibility_proven"] is False

    places["instances"][0]["front_presentation_normal_xy"] = [1.0, 0.0]
    _write(place_path, places)
    failed = A.audit(
        route_path=route, terrain_metadata_path=metadata,
        place_front_receipt_path=place_path,
        infrastructure_receipt_path=infrastructure,
        streetscape_receipt_path=streetscape,
        requirements_path=requirements, output_path=tmp_path / "fail.json",
    )
    assert failed["gates"]["required_place_fronts_are_route_facing"] is False
    assert failed["gates"]["static_route_composition_ready"] is False
