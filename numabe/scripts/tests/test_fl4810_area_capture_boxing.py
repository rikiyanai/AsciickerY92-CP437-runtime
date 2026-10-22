"""AOI street-network acquisition contracts for FL-4810."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scripts.pipeline import street_network_capture_index as AREA


def _geojson(path: Path) -> None:
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"role": "aoi"},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-0.001, -0.001], [0.003, -0.001], [0.003, 0.002],
                    [-0.001, 0.002], [-0.001, -0.001],
                ]]},
            },
            {
                "type": "Feature",
                "properties": {"role": "connected_playable_route", "length_m": 222.39},
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.002, 0.0]]},
            },
        ],
    }), encoding="utf-8")


def _osm(path: Path) -> None:
    path.write_text("""<osm version="0.6">
<node id="1" lat="0" lon="0"/><node id="2" lat="0" lon="0.002"/>
<node id="3" lat="0.001" lon="0"/><node id="4" lat="0.001" lon="0.002"/>
<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
<way id="20"><nd ref="3"/><nd ref="4"/><tag k="highway" v="footway"/></way>
</osm>""", encoding="utf-8")


def test_area_index_uses_all_streets_and_keeps_route_as_secondary_projection(
    tmp_path: Path, monkeypatch,
):
    scope = tmp_path / "scope.geojson"
    osm = tmp_path / "area.osm"
    _geojson(scope)
    _osm(osm)
    pictures = [
        {"picture_id": "a", "sequence_id": "s1", "lon": 0.0002, "lat": 0.0},
        {"picture_id": "b", "sequence_id": "s1", "lon": 0.0018, "lat": 0.0},
        {"picture_id": "c", "sequence_id": "s2", "lon": 0.001, "lat": 0.001},
    ]
    for row in pictures:
        row.update({
            "asset_uri": f"https://example.invalid/{row['picture_id']}.jpg",
            "capture_date": "2026-01-01T00:00:00Z",
            "heading_deg": 90.0,
            "author": "fixture",
        })
    survey = tmp_path / "survey.json"
    survey.write_text(json.dumps({
        "schema_version": AREA.SURVEY_SCHEMA,
        "providers": {"mapillary": {"state": "PRESENT", "pictures": pictures}},
    }), encoding="utf-8")

    def fake_download(row, output_dir, _user_agent):
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"mapillary_{row['picture_id']}.jpg"
        Image.new("RGB", (32, 24), "gray").save(path)
        return row["picture_id"], path

    monkeypatch.setattr(AREA, "_download", fake_download)
    document = AREA.acquire_area(
        survey_path=survey, aoi_geojson_path=scope, osm_path=osm,
        route_path=scope, output_dir=tmp_path / "captures",
        output_index=tmp_path / "capture_source_index.json",
        min_frame_spacing_m=1.0,
    )

    assert document["counts"]["eligible_area_frames"] == 3
    assert document["counts"]["street_ways"] == 2
    assert {row["street_network_projection"]["way_id"] for row in document["captures"]} == {"10", "20"}
    footway = next(row for row in document["captures"] if row["capture_id"] == "mapillary_c")
    assert footway["street_network_projection"]["highway"] == "footway"
    assert footway["acquisition_route_projection"]["lateral_m"] > 100
    assert document["policy"]["route_role"] == "synchronization spine only; not an eligibility filter"
    assert document["area_coverage"]["camera_proximity_coverage_fraction"] > 0
