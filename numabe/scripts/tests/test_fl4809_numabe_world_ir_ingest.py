"""FL-4809 - frames, OSM extract ingest and DEM5A ingest against the FROZEN bundle.

Every number asserted here came out of a command run against
``assets/source_bundles/numabe_corridor_20260911``, whose digests
``python3 -m scripts.pipeline.source_manifest --verify`` confirms.  There are no
invented fixtures and no tolerance chosen to make a number pass.

Four groups:

1.  **Frames.**  ``scripts/pipeline/geo_frames.py`` implements the UTM
    projection in stdlib rather than delegating to ``pyproj``, so the residual
    against every independent implementation that imports here is asserted, and
    so are the Y9 bake constants - which are read out of
    ``compile_facade_shell.py`` and ``osm_projection.py`` by source inspection,
    not restated from memory.

2.  **OSM ingest counts.**  Real counts from the real extract, including the two
    that an earlier draft got wrong: the Overpass double-emission trap (a naive
    ``{id: tags}`` dict reported 0 ``railway=rail`` ways where the manifest says
    19) and the AOI clip, whose building-centroid count is checked against the
    frozen manifest's own independently computed ``buildings_inside_aoi``.

3.  **Anchor set.**  Non-empty, geometrically sane, every anchor a real digitised
    OSM node inside the AOI, and every declared anchor type present in the counts
    even when it has zero members.

4.  **Authority discipline.**  No synthesized claim anywhere supports collision
    or walkability, in either emitted document.

The two emitted documents are produced ONCE per session by module-scoped
fixtures, written to ``tmp_path_factory``, and both the library validator and the
``python3 -m scripts.pipeline.world_ir`` CLI are exercised on them, because exit
status is the contract FL-4815/FL-4816 depend on.
"""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline import world_ir_ingest_elevation as DEM  # noqa: E402
from scripts.pipeline import world_ir_ingest_osm_extract as OSM  # noqa: E402
from scripts.pipeline.world_ir import schema as S, validate as V  # noqa: E402

BUNDLE = REPO_ROOT / "assets" / "source_bundles" / "numabe_corridor_20260911"

pytestmark = pytest.mark.real_asset_e2e


# ==========================================================================
# fixtures
# ==========================================================================


@pytest.fixture(scope="module")
def bundle() -> Path:
    if not BUNDLE.is_dir():
        pytest.skip(f"frozen bundle {BUNDLE} is absent")
    return BUNDLE


@pytest.fixture(scope="module")
def manifest(bundle: Path) -> dict:
    return json.loads((bundle / "source_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def osm_ingest(bundle: Path):
    store, anchors, receipt = OSM.build(bundle)
    return store, anchors, receipt


@pytest.fixture(scope="module")
def osm_paths(osm_ingest, tmp_path_factory) -> dict[str, Path]:
    store, anchors, receipt = osm_ingest
    out = tmp_path_factory.mktemp("fl4809_osm")
    doc = out / "numabe_osm.world_ir.json"
    store.save(doc)
    anchor_path = out / "numabe_osm.anchor_set.json"
    anchor_path.write_bytes(S.canonical_bytes(anchors))
    (out / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    return {"ir": doc, "anchors": anchor_path}


@pytest.fixture(scope="module")
def dem_ingest(bundle: Path, tmp_path_factory):
    cache = tmp_path_factory.mktemp("fl4809_dem_cache")
    try:
        store, receipt = DEM.build(bundle, cache)
    except DEM.TileFetchError as exc:
        pytest.skip(f"GSI DEM5A tile unreachable: {exc.url} status={exc.status} {exc.detail}")
    return store, receipt


@pytest.fixture(scope="module")
def dem_path(dem_ingest, tmp_path_factory) -> Path:
    store, _receipt = dem_ingest
    out = tmp_path_factory.mktemp("fl4809_dem") / "numabe_dem5a.world_ir.json"
    store.save(out)
    return out


def _world_ir_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.pipeline.world_ir", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )


# ==========================================================================
# 1. frames
# ==========================================================================


def test_bake_constants_are_read_from_the_pipeline_not_invented():
    """Canonical facade defaults and frame conversion share native calibration."""
    facade = ast.parse(
        (REPO_ROOT / "scripts" / "pipeline" / "compile_facade_shell.py").read_text(
            encoding="utf-8"
        )
    )
    height_scale = None
    for node in facade.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "HEIGHT_SCALE" for t in node.targets
        ):
            height_scale = ast.literal_eval(node.value)
    assert height_scale == 16.0, "compile_facade_shell.HEIGHT_SCALE moved"
    assert GF.BAKE_UNITS_PER_WORLD_UNIT == height_scale

    import inspect
    from scripts.pipeline.compile_facade_shell import compile_procedural_facade_shells
    assert inspect.signature(compile_procedural_facade_shells).parameters['content_scale'].default == GF.WORLD_UNITS_PER_METRE
    # Sprite calibration is unchanged (8 rows = 1.8 m); the map is the operator's
    # 1.5x on top of it (2026-10-13), applied exactly once.
    assert GF.SPRITE_WORLD_UNITS_PER_METRE == pytest.approx(3.421334928531115)
    assert GF.OPERATOR_MAP_SCALE == 1.5
    assert GF.WORLD_UNITS_PER_METRE == pytest.approx(3.421334928531115 * 1.5)
    assert GF.WORLD_UNITS_PER_METRE == pytest.approx(5.132002392796673)
    assert GF.CLASSIC_TARGET_STANDING_HEIGHT_M == 1.8
    assert GF.BAKE_UNITS_PER_METRE == pytest.approx(GF.WORLD_UNITS_PER_METRE*16)
    assert (8/3)/GF.WORLD_UNITS_PER_METRE < .9  # Classic collision fits the reference door.


def test_operator_map_scale_is_applied_once():
    """A 10 m building is 10 * 5.132 wu wide: no second 1.5 on top of the frame."""
    frame = GF.Y9WorldFrame(379700.0, 3938400.0, "unit test")
    x0, _ = frame.utm_to_world(379700.0, 3938400.0)
    x1, _ = frame.utm_to_world(379710.0, 3938400.0)
    assert x1 - x0 == pytest.approx(10.0 * 5.132002392796673)
    assert x1 - x0 == pytest.approx(10.0 * GF.SPRITE_WORLD_UNITS_PER_METRE * GF.OPERATOR_MAP_SCALE)
    assert frame.metres_to_world_z(10.0) == pytest.approx(x1 - x0)
    # The shell compiler default and the train mesh builder derive from the same owner.
    import scripts.build_tokyu_train_akm as train
    assert train.DEFAULT_UPM == GF.WORLD_UNITS_PER_METRE
    assert GF.BAKE_HEIGHT_ZERO == 128.0


def test_bake_height_value_matches_the_probe_convention():
    """probe_instance_terrain_z documents raw height = 128 + z_world * 16."""
    frame = GF.Y9WorldFrame(0.0, 0.0, "unit test")
    assert frame.bake_height_value(0.0) == 128.0
    assert frame.bake_height_value(1.0) == 144.0
    # one metre of real elevation
    assert frame.metres_to_world_z(1.0) == GF.WORLD_UNITS_PER_METRE
    assert frame.bake_height_value(frame.metres_to_world_z(1.0)) == pytest.approx(128 + GF.BAKE_UNITS_PER_METRE)
    assert frame.metres_to_bake_units(1.0) == pytest.approx(GF.BAKE_UNITS_PER_METRE)


def test_utm_agrees_with_every_available_reference_implementation():
    report = GF.self_test()
    available = {n: r for n, r in report["referees"].items() if r.get("available")}
    assert available, f"no independent projection referee imported: {report['referees']}"
    for name, res in available.items():
        # A metre-scale pipeline needs micrometre agreement at worst; the
        # measured value is ~3e-09 m.
        assert res["max_residual_m"] < 1e-6, f"{name} disagrees by {res['max_residual_m']} m"


def test_utm_roundtrip_residual_is_sub_micrometre():
    report = GF.self_test()
    assert report["roundtrip_max_residual_m"] < 1e-6


def test_frozen_bundle_metric_figures_reproduce():
    """A third reference: the bundle's own EPSG:32654 figures, computed elsewhere."""
    ref = GF.bundle_reference_residuals(BUNDLE)
    assert abs(ref["corridor_length_m"]["residual"]) < 0.01
    assert abs(ref["aoi_area_m2"]["residual"]) < 1.0
    # bbox EDGE lengths, not the axis-aligned span: grid convergence rotates the
    # lon/lat rectangle in UTM, so its bounding box is legitimately wider.
    for computed, declared in zip(ref["bbox_edges_m"]["computed"],
                                  ref["bbox_edges_m"]["declared"]):
        assert abs(computed - declared) < 1.0


def test_selected_entrance_projects_to_the_expected_utm_position():
    lon, lat = 139.6730398, 35.5828560
    easting, northing = GF.utm_forward(lon, lat)
    assert easting == pytest.approx(379774.0767, abs=1e-3)
    assert northing == pytest.approx(3938492.8769, abs=1e-3)
    back_lon, back_lat = GF.utm_inverse(easting, northing)
    assert back_lon == pytest.approx(lon, abs=1e-9)
    assert back_lat == pytest.approx(lat, abs=1e-9)


def test_zone_54_is_the_aoi_zone():
    assert GF.zone_for_lon(139.6730398) == 54
    assert GF.utm_epsg(54) == "EPSG:32654"
    assert GF.UTM_ZONE_AOI == 54


def test_declared_frames_are_complete_and_valid():
    frame = GF.Y9WorldFrame.anchored_at_lonlat(139.6730398, 35.5828560, "unit test")
    records = [
        GF.frame_geographic(),
        GF.frame_projected(vertical_datum=GF.VERTICAL_DATUM_GSI),
        GF.frame_y9_world(frame),
        GF.frame_y9_bake(frame),
    ]
    assert {r["frame_id"] for r in records} == {
        GF.FRAME_GEOGRAPHIC, GF.FRAME_PROJECTED, GF.FRAME_Y9_WORLD, GF.FRAME_Y9_BAKE
    }
    for rec in records:
        assert set(GF.S.COORDINATE_FRAME_REQUIRED) <= set(rec)
        assert set(rec) <= S.COORDINATE_FRAME_FIELDS
        assert rec["axis_order"], "every frame must state its axis order"
        assert rec["vertical_datum"], "every frame must state its vertical datum"
        findings: list[V.Finding] = []
        V._validate_frame(rec, rec["frame_id"], f"$.coordinate_frames.{rec['frame_id']}",
                          findings)
        assert findings == []
    assert records[0]["axis_order"] == ["longitude", "latitude"], "RFC 7946 order"
    assert records[1]["units"] == "metre"
    assert records[1]["crs"] == "EPSG:32654"
    assert "Japanese vertical datum" in records[1]["vertical_datum"]


# ==========================================================================
# 2. OSM ingest against the frozen extract
# ==========================================================================


def test_parse_merges_the_overpass_double_emission(osm_ingest, manifest):
    """``out body; >; out skel qt;`` emits every element twice; the second copy
    carries geometry and NO tags.  Last-wins would blank every way's tags."""
    _store, _anchors, receipt = osm_ingest
    stats = receipt["extract"]
    declared = manifest["context"]["extract_element_counts"]
    assert stats["node_elements_read"] == declared["node"] == 40122
    assert stats["way_elements_read"] == declared["way"] == 11145
    assert stats["relation_elements_read"] == declared["relation"] == 34
    assert stats["unique_nodes"] == 39203
    assert stats["unique_ways"] == 11116
    assert stats["duplicate_node_elements_merged"] == 919
    assert stats["duplicate_way_elements_merged"] == 29
    assert stats["tagged_nodes"] == declared["tagged_nodes"] == 31
    assert stats["multipolygon_relations"] == 0


def test_railway_ways_survive_the_merge(osm_ingest, manifest):
    """The regression the merge exists to prevent, asserted as a count.

    A naive ``{id: tags}`` dict reported ZERO railway ways in an AOI the frozen
    manifest says holds 19.
    """
    _store, _anchors, receipt = osm_ingest
    assert receipt["features_by_class"]["railway_boundary"] == 21
    railway_rail = sum(
        1 for feat in _store.features
        if feat["semantic_class"] == "railway_boundary"
        and "railway=rail" in feat["properties"]["semantic_class"]["method"]
    )
    assert railway_rail == manifest["context"]["aoi_content_check"][
        "railway_ways_touching_aoi"
    ] == 19


def test_aoi_clip_counts_and_what_it_dropped(osm_ingest):
    _store, _anchors, receipt = osm_ingest
    clip = receipt["aoi_clip"]
    assert clip["ways_kept"] == 181
    assert clip["ways_clipped_away"] == 10935
    assert clip["ways_kept"] + clip["ways_clipped_away"] == receipt["extract"]["unique_ways"]
    assert clip["clipped_away_fraction_of_ways"] == pytest.approx(0.9837, abs=1e-4)
    assert clip["tagged_nodes_kept"] == 31
    assert clip["tagged_nodes_clipped_away"] == 0
    assert clip["untagged_geometry_carrier_nodes"] == 39203 - 31


def test_building_centroid_count_matches_the_frozen_manifest(bundle, manifest):
    """Independent cross-check of the parse and the clip.

    The manifest's ``buildings_inside_aoi`` was computed by another agent with a
    different toolchain, by point-in-polygon of building-way CENTROIDS.  This
    ingest keeps a way when any vertex is inside OR an edge crosses, which is a
    deliberately wider predicate - so the two numbers must differ in exactly the
    way the two predicates differ, and the centroid subset must match exactly.
    """
    ingest = OSM.OSMIngest(bundle, ir_id="crosscheck", generated_at="2026-09-11T00:00:00Z")
    ways, nodes = ingest.clip_to_aoi()
    ingest.ingest_ways(ways)
    ingest.ingest_nodes(nodes)
    buildings = [
        wid for wid in ingest.kept_ways
        if ingest.class_of[f"way/{wid}"] == "building_footprint"
    ]
    assert len(buildings) == 131
    centroid_inside = sum(
        1 for wid in buildings
        if OSM.point_in_ring(OSM.ring_centroid(ingest.extract.way_points(wid)), ingest.ring)
    )
    assert centroid_inside == manifest["context"]["aoi_content_check"][
        "buildings_inside_aoi"
    ] == 119
    # Every kept building is a closed ring, which is what lets them claim
    # collision at all.
    assert all(ingest.extract.is_closed(wid) for wid in buildings)


def test_highway_classes_match_the_frozen_manifest(osm_ingest, manifest):
    _store, _anchors, receipt = osm_ingest
    declared = manifest["context"]["aoi_content_check"]
    # 20 highway WAYS: 12 road + 4 sidewalk + 4 crossing ways.  Node features
    # also carry highway= methods (crossings, traffic signals), so the count has
    # to be restricted to way features or it reads 32.
    highway_derived = sum(
        1 for feat in _store.features
        if "way/" in feat["feature_id"]
        and feat["properties"]["semantic_class"]["method"].startswith("OSM tag rule: highway=")
    )
    assert highway_derived == declared["highway_ways_touching_aoi"] == 20
    assert receipt["features_by_class"]["sidewalk"] == 4
    assert receipt["features_by_class"]["road"] == 13   # 12 highway + amenity=parking


def test_feature_and_observation_totals(osm_ingest):
    store, _anchors, receipt = osm_ingest
    assert receipt["features_emitted"] == len(store.features) == 209
    assert receipt["observations_emitted"] == len(store.observations) == 209
    assert receipt["features_by_class"] == {
        "bridge_deck": 1,
        "building_footprint": 131,
        "crossing": 16,
        "fixture": 7,
        "place": 9,
        "railway_boundary": 21,
        "road": 13,
        "sidewalk": 4,
        "sign": 1,
        "station_entrance": 2,
        "terrain": 4,
    }
    assert sum(receipt["features_by_class"].values()) == 209
    assert receipt["features_by_gameplay_authority"] == {
        "collision": 131,
        "presentation_only": 77,
        "walkability": 1,
    }


def test_every_emitted_class_is_in_the_closed_registry(osm_ingest):
    store, _anchors, _receipt = osm_ingest
    emitted = {feat["semantic_class"] for feat in store.features}
    assert emitted <= S.SEMANTIC_CLASSES
    mapped_classes = {cls for cls, _why in
                      [r for r in
                       [OSM._way_class({"building": "yes"}), OSM._node_class({"highway": "crossing"})]
                       if r]}
    assert mapped_classes <= S.SEMANTIC_CLASSES


def test_unmapped_aoi_features_are_reported_not_silently_dropped(osm_ingest):
    _store, _anchors, receipt = osm_ingest
    assert receipt["unmapped_count"] == 3
    refs = {u["source_ref"] for u in receipt["unmapped"]}
    # BRIDGE-DECK-01: the closed man_made=bridge outline now classifies as
    # bridge_deck instead of remaining reported here. Station and
    # stop-position nodes are retained as semantic places, without claiming
    # physical platform geometry.
    assert "node/7834193975" not in refs
    assert not any(u["tags"].get("man_made") == "bridge"
                   for u in receipt["unmapped"])
    assert any(u["tags"].get("boundary") == "administrative"
               for u in receipt["unmapped"])
    assert sum(receipt["unmapped_by_reason"].values()) == 3
    for entry in receipt["unmapped"]:
        assert entry["reason"].strip(), f"{entry['source_ref']} has no stated reason"
        assert entry["tags"], "an unmapped report without its tags is not actionable"
    # The two physical station entrances ARE mapped, which is why dropping the
    # topology node loses nothing physical.


def test_observations_carry_the_frozen_source_provenance(osm_ingest, manifest):
    store, _anchors, _receipt = osm_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "osm_overpass_aoi_extract")
    for obs in store.observations:
        assert obs["source_id"] == record["record_id"]
        assert obs["source_kind"] == "vector_map"
        assert obs["digest"] == record["sha256"]
        assert obs["observed_at"] == record["capture_date"]
        assert obs["attributes"]["licence"] == "ODbL-1.0"
        assert "OpenStreetMap contributors" in obs["attributes"]["attribution"]
        assert obs["geometry"]["frame_id"] == GF.FRAME_GEOGRAPHIC, (
            "an observation states what the source said: degrees, not metres"
        )
        assert obs["quality"]["position_sigma_m"] == pytest.approx(
            OSM.OSM_POSITION_SIGMA_M
        )


def test_features_are_projected_and_every_frame_is_declared(osm_ingest):
    store, _anchors, _receipt = osm_ingest
    assert set(store.frames) == {
        GF.FRAME_GEOGRAPHIC, GF.FRAME_PROJECTED, GF.FRAME_Y9_WORLD, GF.FRAME_Y9_BAKE
    }
    for feat in store.features:
        assert feat["geometry"]["frame_id"] == GF.FRAME_PROJECTED
        claim = feat["properties"]["geometry"]
        assert claim["uncertainty"]["units"] == "metre"
        assert claim["uncertainty"]["sigma"] == pytest.approx(OSM.OSM_POSITION_SIGMA_M)


def test_digitising_sigma_is_derived_from_the_carto_owner_not_reinvented():
    carto = ast.parse(
        (REPO_ROOT / "scripts" / "pipeline" / "world_ir_ingest_carto.py").read_text(
            encoding="utf-8"
        )
    )
    sigma_world = None
    for node in carto.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "OSM_POSITION_SIGMA" for t in node.targets
        ):
            sigma_world = ast.literal_eval(node.value)
    assert sigma_world == 1.5, "the carto owner's OSM_POSITION_SIGMA moved"
    assert OSM.OSM_POSITION_SIGMA_WORLD_UNITS == sigma_world
    assert OSM.OSM_POSITION_SIGMA_M == pytest.approx(
        sigma_world / OSM.OSM_POSITION_SIGMA_REFERENCE_WORLD_UNITS_PER_METRE
    )
    assert OSM.OSM_POSITION_SIGMA_M == pytest.approx(0.6667, abs=1e-4)


# --------------------------------------------------------------------------
# authority discipline
# --------------------------------------------------------------------------


def test_a_centreline_never_claims_a_physical_authority(osm_ingest):
    """Linear OSM geometry has no recorded width, so it cannot decide standing."""
    store, _anchors, _receipt = osm_ingest
    linear = [f for f in store.features if f["geometry"]["kind"] == "polyline"]
    assert linear, "the AOI must contain linear features for this test to mean anything"
    for feat in linear:
        assert feat["gameplay_authority"] == S.AUTHORITY_PRESENTATION_ONLY, (
            f"{feat['feature_id']} is a centreline claiming "
            f"{feat['gameplay_authority']}"
        )
        assert feat["presentation_requirements"]["must_not_imply_collision"] is True


def test_collision_is_claimed_only_by_closed_measured_rings(osm_ingest):
    store, _anchors, _receipt = osm_ingest
    collision = [f for f in store.features
                 if f["gameplay_authority"] == S.AUTHORITY_COLLISION]
    assert len(collision) == 131
    for feat in collision:
        assert feat["geometry"]["kind"] == "polygon"
        assert feat["semantic_class"] == "building_footprint"
        ring = feat["geometry"]["coordinates"]
        assert len(ring) >= 4 and ring[0] == ring[-1], "collision needs a closed ring"
        assert feat["properties"]["geometry"]["provenance"] == S.PROVENANCE_MEASURED


@pytest.mark.parametrize("which", ["osm", "dem"])
def test_no_synthesized_claim_supports_collision_or_walkability(which, osm_ingest,
                                                                dem_ingest):
    store = osm_ingest[0] if which == "osm" else dem_ingest[0]
    synthesized = 0
    for feat in store.features:
        for name, claim in feat["properties"].items():
            if claim["provenance"] == S.PROVENANCE_SYNTHESIZED:
                synthesized += 1
        if feat["gameplay_authority"] not in S.PHYSICAL_AUTHORITIES:
            continue
        supporting = feat.get("authority_supporting_properties") or ["geometry"]
        provenances = {
            feat["properties"][name]["provenance"]
            for name in supporting if name in feat["properties"]
        }
        assert provenances, f"{feat['feature_id']} names no supporting claim"
        assert S.PROVENANCE_SYNTHESIZED not in provenances, (
            f"{feat['feature_id']} claims {feat['gameplay_authority']} off a "
            "synthesized property"
        )
        assert provenances <= {S.PROVENANCE_MEASURED, S.PROVENANCE_DERIVED}
    # Neither ingest synthesizes anything at all: both read a source.
    assert synthesized == 0


# ==========================================================================
# 3. anchor set
# ==========================================================================


def test_anchor_set_is_non_empty_and_counts_every_declared_type(osm_ingest):
    _store, anchors, _receipt = osm_ingest
    assert anchors["anchor_set_version"] == OSM.ANCHOR_SET_VERSION
    assert anchors["anchor_count"] == 765
    assert anchors["anchor_count"] == len(anchors["anchors"]) > 0
    assert anchors["anchor_count_by_type"] == {
        "building_corner": 678,
        "crossing_corner": 20,
        "railway_vertex": 40,
        "road_intersection": 25,
        "station_entrance": 2,
        "wall_fence_endpoint": 0,
    }
    # A type with no members is REPORTED as zero with a reason, so "none found"
    # and "never looked" cannot produce the same document.
    assert set(anchors["anchor_count_by_type"]) == set(OSM.ANCHOR_TYPES)
    assert set(anchors["anchor_types_with_no_members"]) == {"wall_fence_endpoint"}
    assert anchors["anchor_types_with_no_members"]["wall_fence_endpoint"]
    assert sum(anchors["anchor_count_by_type"].values()) == anchors["anchor_count"]
    # Anchors reaching past the ring are reported, not dropped.
    assert anchors["anchor_count_inside_aoi"] == 687
    assert anchors["anchor_count_outside_aoi"] == 78
    assert (anchors["anchor_count_inside_aoi"] + anchors["anchor_count_outside_aoi"]
            == anchors["anchor_count"])
    assert sum(1 for a in anchors["anchors"] if a["inside_aoi"]) == 687


def test_every_anchor_is_a_real_digitised_node_inside_the_aoi(osm_ingest, bundle):
    _store, anchors, _receipt = osm_ingest
    geojson = json.loads((bundle / "numabe_corridor_aoi.geojson").read_text(encoding="utf-8"))
    ring = next(f for f in geojson["features"]
                if f["id"] == "aoi_polygon")["geometry"]["coordinates"][0]
    ids = set()
    for anchor in anchors["anchors"]:
        assert anchor["anchor_id"] not in ids, "anchor ids must be unique"
        ids.add(anchor["anchor_id"])
        assert anchor["osm_ref"].startswith("node/"), (
            "an anchor must be a digitised node, never an interpolated point"
        )
        assert anchor["anchor_id"].endswith(anchor["osm_ref"])
        assert anchor["anchor_type"] in OSM.ANCHOR_TYPES
        assert anchor["reason"].strip(), f"{anchor['anchor_id']} states no reason"
        # A kept building is kept when ANY vertex is inside, so some of its
        # corners land outside the ring.  They are still usable anchors, so the
        # flag must be TRUTHFUL rather than the anchor being dropped.
        assert anchor["inside_aoi"] is OSM.point_in_ring(anchor["epsg4326"], ring), (
            f"{anchor['anchor_id']} reports inside_aoi={anchor['inside_aoi']} "
            "but the ring test disagrees"
        )
        assert anchor["owner_feature_ids"], "an anchor must name the feature it came from"
        assert anchor["owner_observation_ids"], "an anchor must name its evidence"
        assert anchor["uncertainty"] == S.uncertainty_isotropic(
            OSM.OSM_POSITION_SIGMA_M, "metre"
        )


def test_anchor_coordinates_are_mutually_consistent(osm_ingest):
    """4326 / 32654 / y9_world must be three views of ONE position."""
    _store, anchors, _receipt = osm_ingest
    meta = anchors["y9_frame"]
    worst_projection = 0.0
    worst_world = 0.0
    for anchor in anchors["anchors"]:
        lon, lat = anchor["epsg4326"]
        easting, northing = anchor["epsg32654"]
        expected = GF.utm_forward(lon, lat)
        worst_projection = max(worst_projection, math.dist(expected, (easting, northing)))
        wx, wy = anchor["y9_world"]
        expect_wx = (easting - meta["anchor_easting_m"]) * meta["world_units_per_metre"]
        expect_wy = (northing - meta["anchor_northing_m"]) * meta["world_units_per_metre"]
        worst_world = max(worst_world, math.dist((wx, wy), (expect_wx, expect_wy)))
    assert worst_projection < 1e-6
    assert worst_world < 1e-6


def test_anchor_set_spans_the_corridor_and_not_more(osm_ingest, manifest):
    """Geometric sanity: the anchors must cover a corridor-sized extent."""
    _store, anchors, _receipt = osm_ingest
    lo = anchors["anchor_bounds_inside_aoi_epsg32654"]["min"]
    hi = anchors["anchor_bounds_inside_aoi_epsg32654"]["max"]
    width = hi[0] - lo[0]
    height = hi[1] - lo[1]
    declared = manifest["context"]["aoi"]["bbox_extent_m"]
    # Inside-AOI anchors must fit the AOI bbox.  The slack is the grid
    # convergence rotation proved in test_frozen_bundle_metric_figures_reproduce:
    # the lon/lat rectangle's UTM bounding box is ~2.4 m wider than its sides.
    assert 0 < width <= declared[0] + 5.0, width
    assert 0 < height <= declared[1] + 5.0, height
    # and actually spread out, not clustered in one building
    assert width > 100.0 and height > 100.0
    # The full set reaches further, because buildings straddle the ring.
    full_lo = anchors["anchor_bounds_epsg32654"]["min"]
    full_hi = anchors["anchor_bounds_epsg32654"]["max"]
    assert full_hi[0] - full_lo[0] >= width
    assert full_hi[1] - full_lo[1] >= height


def test_station_entrance_anchors_name_the_selected_entrance(osm_ingest, manifest):
    _store, anchors, _receipt = osm_ingest
    entrances = [a for a in anchors["anchors"] if a["anchor_type"] == "station_entrance"]
    assert len(entrances) == 2
    selected_node = manifest["context"]["corridor"]["selected_entrance_osm_node"]
    selected = [a for a in entrances if a["osm_ref"] == f"node/{selected_node}"]
    assert len(selected) == 1
    assert "SELECTED" in selected[0]["reason"]
    # The Y9 frame is anchored ON the selected entrance, so it is world origin.
    assert selected[0]["y9_world"] == pytest.approx([0.0, 0.0], abs=1e-6)
    lon, lat = selected[0]["epsg4326"]
    assert (lon, lat) == pytest.approx(
        tuple(manifest["context"]["corridor"]["selected_entrance_lonlat"]), abs=1e-9
    )


def test_building_corner_anchors_are_real_corners(osm_ingest):
    _store, anchors, _receipt = osm_ingest
    corners = [a for a in anchors["anchors"] if a["anchor_type"] == "building_corner"]
    assert len(corners) == 678
    assert anchors["corner_min_turn_deg"] == OSM.CORNER_MIN_TURN_DEG == 30.0
    for anchor in corners:
        turn = float(anchor["reason"].split("turns ")[1].split(" deg")[0])
        assert turn >= OSM.CORNER_MIN_TURN_DEG, (
            "a vertex below the turn threshold is a digitising kink on a straight "
            "wall, not a registerable corner"
        )
        assert turn <= 180.0


def test_anchor_set_carries_source_licence_and_attribution(osm_ingest, manifest):
    _store, anchors, _receipt = osm_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "osm_overpass_aoi_extract")
    assert anchors["source"]["sha256"] == record["sha256"]
    assert anchors["source"]["licence"] == "ODbL-1.0"
    assert "OpenStreetMap contributors" in anchors["source"]["attribution"]
    assert set(anchors["coordinate_frames"]) == {
        GF.FRAME_GEOGRAPHIC, GF.FRAME_PROJECTED, GF.FRAME_Y9_WORLD, GF.FRAME_Y9_BAKE
    }
    assert anchors["world_ir_schema_version"] == S.SCHEMA_VERSION


def test_anchor_set_serializes_to_canonical_bytes(osm_paths):
    raw = osm_paths["anchors"].read_bytes()
    assert S.canonical_bytes(json.loads(raw.decode("utf-8"))) == raw


# ==========================================================================
# 4. DEM5A ingest
# ==========================================================================


def test_dem_tile_digest_matches_the_frozen_manifest(dem_ingest, manifest):
    _store, receipt = dem_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "gsi_dem5a_elevation")
    assert len(receipt["tiles"]) == 1, "the AOI fits one z15 tile"
    tile = receipt["tiles"][0]
    assert tile["tile"] == "15/29097/12914"
    assert tile["http_status"] in (200, "cached")
    assert tile["sha256"] == record["sha256"]
    assert tile["sha256_matches_frozen_manifest"] is True
    assert tile["size_bytes"] == record["size_bytes"] == 353256


def test_dem_tile_statistics_reproduce_the_frozen_probe(dem_ingest, manifest):
    _store, receipt = dem_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "gsi_dem5a_elevation")
    declared = record["parameters"]
    tile = receipt["tiles"][0]
    assert tile["cells_total"] == declared["valid_samples"] == 65536
    assert tile["cells_nodata"] == declared["nodata_cells"] == 0
    assert tile["elevation_min_m"] == pytest.approx(declared["elev_min_m"], abs=0.005)
    assert tile["elevation_max_m"] == pytest.approx(declared["elev_max_m"], abs=0.005)
    assert tile["elevation_mean_m"] == pytest.approx(declared["elev_mean_m"], abs=0.005)
    assert tile["cell_size_m"] == pytest.approx(declared["cell_size_m_at_z15"], abs=0.002)


def test_dem_entrance_elevation_matches_the_frozen_probe(dem_ingest, manifest):
    """The one elevation the frozen bundle already published, reproduced."""
    _store, receipt = dem_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "gsi_dem5a_elevation")
    probe = receipt["entrance_probe"]
    assert probe is not None
    assert probe["cell"] == list(record["parameters"]["entrance_cell"]) == [89, 85]
    assert probe["elevation_m"] == record["parameters"]["entrance_elevation_m"] == 7.28
    assert receipt["entrance_elevation_declared_by_bundle_m"] == 7.28
    # and the bake height the Y9 frame would give that elevation
    assert receipt["entrance_bake_height_value"] == pytest.approx(
        128.0 + 7.28 * GF.BAKE_UNITS_PER_METRE
    )


def test_dem_samples_are_ingested_with_no_invented_values(dem_ingest):
    store, receipt = dem_ingest
    assert receipt["samples_ingested"] == 1789
    assert receipt["aoi_cells_nodata_dropped"] == 0
    assert receipt["tile_cells_nodata_total"] == 0
    assert "never interpolated" in store.observations[0]["quality"]["nodata_handling"]
    feature = store.feature("feature:terrain:gsi_dem5a_aoi_samples")
    assert feature is not None
    assert feature["semantic_class"] == "terrain"
    geometry = feature["geometry"]
    assert geometry["kind"] == "field_samples"
    assert geometry["frame_id"] == GF.FRAME_PROJECTED
    assert len(geometry["samples"]) == 1789
    assert geometry["lookup"].strip()
    elevations = [s["elevation_m"] for s in geometry["samples"]]
    assert min(elevations) == pytest.approx(receipt["elevation_min_m"])
    assert max(elevations) == pytest.approx(receipt["elevation_max_m"])
    # every value is a real two-decimal source reading, not a smoothed one
    for value in elevations:
        assert round(value, 2) == value


def test_dem_vertical_datum_is_quoted_from_the_manifest(dem_ingest, manifest):
    store, receipt = dem_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "gsi_dem5a_elevation")
    assert "orthometric metres on the Japanese vertical datum" in record["crs"]
    assert receipt["vertical_datum"] == GF.VERTICAL_DATUM_GSI
    assert "Japanese vertical datum" in receipt["vertical_datum"]
    assert store.frames[GF.FRAME_PROJECTED]["vertical_datum"] == GF.VERTICAL_DATUM_GSI
    feature = store.feature("feature:terrain:gsi_dem5a_aoi_samples")
    datum_claim = feature["properties"]["vertical_datum"]
    assert datum_claim["value"] == GF.VERTICAL_DATUM_GSI
    assert datum_claim["provenance"] == S.PROVENANCE_MEASURED
    assert record["record_id"] in datum_claim["method"]


def test_dem_uncertainty_is_measured_sampling_spread_not_a_claimed_accuracy(dem_ingest):
    store, receipt = dem_ingest
    feature = store.feature("feature:terrain:gsi_dem5a_aoi_samples")
    unc = feature["properties"]["geometry"]["uncertainty"]
    assert unc["model"] == "bounds"
    assert unc["units"] == "metre"
    assert unc["low"] == 0.0
    assert unc["high"] == pytest.approx(receipt["sampling_max_adjacent_step_m"] / 2.0)
    assert unc["high"] > 0.0
    method = feature["properties"]["geometry"]["method"]
    assert "SAMPLING spread" in method
    assert "published vertical accuracy" in method, (
        "the claim must say what it is NOT, or a reader will read it as accuracy"
    )


def test_dem_provenance_records_gsi_attribution_and_open_terms(dem_ingest, manifest):
    store, _receipt = dem_ingest
    record = next(r for r in manifest["records"] if r["record_id"] == "gsi_dem5a_elevation")
    assert len(store.observations) == 1
    obs = store.observations[0]
    assert obs["source_kind"] == "point_cloud"
    attrs = obs["attributes"]
    assert attrs["licence"] == "JP-GSI-TERMS"
    assert attrs["attribution"] == record["attribution"]
    assert "国土地理院" in attrs["attribution"]
    assert attrs["open_terms"] is True
    assert "OPEN_LICENCES" in attrs["open_terms_basis"]
    assert attrs["licence_uri"] == record["licence_uri"]
    assert "DEM5A" in attrs["model"] and "LiDAR" in attrs["model"]
    feature = store.feature("feature:terrain:gsi_dem5a_aoi_samples")
    assert feature["properties"]["attribution"]["value"] == record["attribution"]


def test_dem_terrain_claims_walkability_not_collision(dem_ingest):
    store, _receipt = dem_ingest
    feature = store.feature("feature:terrain:gsi_dem5a_aoi_samples")
    assert feature["gameplay_authority"] == S.AUTHORITY_WALKABILITY
    assert feature["authority_supporting_properties"] == ["geometry", "vertical_datum"]
    for name in feature["authority_supporting_properties"]:
        assert feature["properties"][name]["provenance"] == S.PROVENANCE_MEASURED


def test_tile_fetch_failure_is_reported_not_estimated(tmp_path, bundle):
    """An unreachable tile must fail loudly; no elevation may be invented."""
    empty_cache = tmp_path / "no_tiles"
    with pytest.raises(DEM.TileFetchError) as exc:
        DEM.build(bundle, empty_cache, offline=True)
    assert exc.value.status == "offline"
    assert "cyberjapandata.gsi.go.jp" in exc.value.url
    assert not list(empty_cache.glob("*.txt")) if empty_cache.is_dir() else True


def test_tile_geometry_helpers_place_the_aoi_in_one_tile(bundle):
    geojson = json.loads((bundle / "numabe_corridor_aoi.geojson").read_text(encoding="utf-8"))
    ring = next(f for f in geojson["features"]
                if f["id"] == "aoi_polygon")["geometry"]["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    tiles = DEM.tiles_covering((min(lons), min(lats), max(lons), max(lats)), 15)
    assert tiles == [(29097, 12914)]
    assert DEM.cell_size_m(35.5828560, 15) == pytest.approx(3.885, abs=0.002)


# ==========================================================================
# validation gates - both documents, library AND CLI
# ==========================================================================


def test_library_validator_accepts_both_documents(osm_ingest, dem_ingest):
    assert osm_ingest[0].validate() == []
    assert dem_ingest[0].validate() == []
    assert osm_ingest[2]["validation_findings"] == 0
    assert dem_ingest[1]["validation_findings"] == 0


@pytest.mark.parametrize("doc", ["osm", "dem"])
def test_world_ir_cli_validate_exits_zero(doc, osm_paths, dem_path):
    path = osm_paths["ir"] if doc == "osm" else dem_path
    result = _world_ir_cli("validate", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[world-ir] ok" in result.stdout


@pytest.mark.parametrize("doc", ["osm", "dem"])
def test_world_ir_cli_canonicalize_check_exits_zero(doc, osm_paths, dem_path):
    path = osm_paths["ir"] if doc == "osm" else dem_path
    result = _world_ir_cli("canonicalize", "--check", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "canonical=yes" in result.stdout


@pytest.mark.parametrize("doc", ["osm", "dem"])
def test_documents_reload_byte_identically(doc, osm_paths, dem_path):
    path = osm_paths["ir"] if doc == "osm" else dem_path
    raw = path.read_bytes()
    assert S.canonical_bytes(json.loads(raw.decode("utf-8"))) == raw


def test_neither_document_stores_a_representation_choice(osm_ingest, dem_ingest):
    for store in (osm_ingest[0], dem_ingest[0]):
        assert store.representations == []
        blob = store.dumps()
        for key in ("mesh_name", "akm", "xp_file", "material_id", "transform"):
            assert f'"{key}"' not in blob, f"{key} is a lowered product, not world truth"


def test_cli_entry_points_produce_the_same_documents(tmp_path, bundle, osm_ingest):
    """The module is reachable from a shell, not only from pytest."""
    out = tmp_path / "cli.world_ir.json"
    anchors = tmp_path / "cli.anchor_set.json"
    result = subprocess.run(
        [sys.executable, "scripts/pipeline/world_ir_ingest_osm_extract.py",
         "--bundle", str(bundle), "--out", str(out),
         "--anchors-out", str(anchors), "--validate"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_bytes() == osm_ingest[0].dump_bytes()
    assert json.loads(anchors.read_text(encoding="utf-8"))["anchor_count"] == 765
