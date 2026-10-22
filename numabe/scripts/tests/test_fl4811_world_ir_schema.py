"""FL-4811 - Y92 World IR: schema, validator, determinism, real-data expressiveness.

Three groups of tests live here:

1.  Contract tests - the vocabulary is closed, source-independent and
    locale-independent, provenance is property-level, and the three record
    kinds stay structurally separate.
2.  Negative tests - every rule that makes the IR a canonical owner is proved
    to actually reject the thing it claims to reject.  A validator that
    accepts everything is a failed deliverable, so each negative test also
    shows the *same* document passing once the single defect is removed.
3.  Expressiveness tests against REAL repository data produced by the shipped
    FL-4787 SBU pipeline.  No invented fixtures: the OSM carto features, the
    baked building instances and the source palette are read (or recompiled)
    from the checked-in run at
    ``assets/meshes/osm_runs/sbu_carto_scale225_20260601_canonical_ramps``.

Two real-data findings are asserted here rather than papered over:

  * The shipped ``carto_features.jsonl`` caps ``world_vertices`` at 40 points
    and appends the literal string ``"...truncated"``.  Six of the 391 rows are
    lossy, five of them buildings whose complete rings (up to 73 vertices)
    survive only in ``building_instances.json``.  The IR refuses the truncated
    ring outright and either joins to the lossless source or records a DERIVED
    bounds reconstruction with its own uncertainty.
  * The repo ships no LiDAR tile
    (``*.laz``/``*.las``) and no compiled ``*.roof_field.json``, so the
    measured roof-tier *values* cannot be replayed here.  The roof-tier
    *shape* is still taken from real repo code: the emitted key sets are
    extracted from
    ``scripts/pipeline/compile_lidar_roof_field.py`` with ``ast`` and every
    key is asserted to have a home in the IR.  If that producer changes its
    record shape, these tests fail.
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "scripts" / "pipeline"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from scripts.pipeline.world_ir import WorldIRStore, schema as S, validate as V  # noqa: E402
from scripts.pipeline import world_ir_ingest_carto as ingest  # noqa: E402

RUN_DIR = (
    REPO_ROOT
    / "assets"
    / "meshes"
    / "osm_runs"
    / "sbu_carto_scale225_20260601_canonical_ramps"
)
CARTO_FEATURES = RUN_DIR / "output_terrain_only.a3d.carto_features.jsonl"
BUILDING_INSTANCES = RUN_DIR / "building_instances.json"
OSM_INPUT = RUN_DIR / "osm_blosm_input.osm"
CARTO_ROLES = RUN_DIR / "output_terrain_only.a3d.carto_roles.json"

# The OSM vector frame the run bakes into: a project-local planar frame, not an
# authority CRS.  Declaring it explicitly is what keeps the IR locale-neutral.
# Ingest mapping and the real-run helpers now live in the pipeline owner
# ``scripts/pipeline/world_ir_ingest_carto.py`` so that producing a World IR
# document from a real run does not require running pytest. This test exercises
# that owner rather than holding a private second copy of the mapping.
A3D_FRAME = ingest.A3D_FRAME
OSM_POSITION_SIGMA = ingest.OSM_POSITION_SIGMA
CARTO_KIND_TO_SEMANTIC_CLASS = ingest.CARTO_KIND_TO_SEMANTIC_CLASS
SEMANTIC_CLASS_AUTHORITY = ingest.SEMANTIC_CLASS_AUTHORITY
TRUNCATION_MARKER = ingest.TRUNCATION_MARKER
_is_truncated = ingest.is_truncated
_bounds_ring = ingest.bounds_ring
_carto_geometry = ingest.carto_geometry
_ingest_carto_row = ingest.ingest_carto_row


def _load_carto_features() -> list[dict]:
    return ingest.load_carto_features(CARTO_FEATURES)


def _building_footprints() -> dict[str, list[list[float]]]:
    return ingest.load_building_footprints(BUILDING_INSTANCES)


# ==========================================================================
# helpers
# ==========================================================================


def _minimal_store() -> WorldIRStore:
    """A small but fully valid document used as the base for negative tests."""
    store = WorldIRStore.new("fl4811-minimal", generated_at="2026-09-11T00:00:00Z")
    store.add_frame(A3D_FRAME)
    store.add_observation(
        S.source_observation(
            observation_id="obs:vector:1",
            source_id="vector_map_run",
            source_kind="vector_map",
            observed_at="2026-06-01T00:00:00Z",
            attributes={"tag": "value"},
            geometry=S.geometry_point("a3d_world", [1.0, 2.0]),
        )
    )
    store.add_feature(
        S.canonical_feature(
            feature_id="feature:fixture:1",
            semantic_class="fixture",
            geometry=S.geometry_point("a3d_world", [1.0, 2.0]),
            properties={
                "geometry": S.property_claim(
                    value=None,
                    provenance=S.PROVENANCE_MEASURED,
                    method="vector node position",
                    observation_ids=["obs:vector:1"],
                    uncertainty=S.uncertainty_isotropic(OSM_POSITION_SIGMA, "a3d_world_units"),
                ),
                "semantic_class": S.property_claim(
                    value="fixture",
                    provenance=S.PROVENANCE_DERIVED,
                    method="tag classification",
                    observation_ids=["obs:vector:1"],
                    uncertainty=S.uncertainty_categorical({"fixture": 0.9, "sign": 0.1}),
                ),
            },
            confidence=0.9,
            gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
        )
    )
    return store


def _codes(findings) -> list[str]:
    return [f.code for f in findings]


def _extract_appended_dict_keys(source_path: Path, target_name: str) -> set[str]:
    """Return the literal string keys of the dict appended to ``target_name``.

    Reads the real producer's source so these tests stay bound to what the
    shipped pipeline actually emits rather than to a copied fixture.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "append":
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != target_name:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                for key in arg.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
    return keys


# vocabulary onto the IR is an ingest concern.  The IR itself names no source.

# ==========================================================================
# 1. contract tests
# ==========================================================================


def test_semantic_class_registry_covers_the_required_world_vocabulary():
    required = {
        "terrain", "road", "sidewalk", "curb", "tactile_paving", "road_marking",
        "building_footprint", "roof", "facade_plane", "facade_region", "door",
        "window", "sign", "fixture", "tree", "hedge", "shrub", "planter", "wall",
        "fence", "utility_pole", "utility_wire", "railway_boundary", "crossing",
        "station_entrance", "spawn", "world_bounds",
    }
    assert required <= S.SEMANTIC_CLASSES
    # FL-4812 added camera_station: a solved capture pose is world truth about
    # where an observation was made, and borrowing the gameplay "spawn" class
    # for it polluted spawn queries with camera stations.
    assert S.SEMANTIC_CLASSES == frozenset(required) | {"camera_station", "place"}, \
        "registry must stay closed"


def test_vocabulary_is_source_and_locale_independent():
    """No class, authority or provenance value may name a source or a place."""
    banned = (
        "tokyo", "numabe", "sbu", "stony", "osm", "lidar", "godot", "a3d", "akm",
        "blender", "xp", "card", "japan", "newyork",
    )
    tokens = (
        set(S.SEMANTIC_CLASSES)
        | set(S.GAMEPLAY_AUTHORITIES)
        | set(S.PROVENANCE_CLASSES)
        | set(S.CONFLICT_STATES)
        | set(S.GEOMETRY_KINDS)
        | set(S.SOURCE_KINDS)
    )
    for token in tokens:
        lowered = token.lower()
        for word in banned:
            assert word not in lowered, f"{token!r} leaks source/locale token {word!r}"


def test_provenance_is_exactly_three_values_and_lives_on_properties():
    assert S.PROVENANCE_CLASSES == ("measured", "derived", "synthesized")
    feature = _minimal_store().features[0]
    assert "provenance" not in feature, "provenance must not be an object-level field"
    assert {c["provenance"] for c in feature["properties"].values()} == {"measured", "derived"}


def test_one_feature_mixes_measured_derived_and_synthesized_properties():
    """A lamp: triangulated position, fused class, synthesized appearance."""
    store = _minimal_store()
    lamp = S.canonical_feature(
        feature_id="feature:fixture:lamp-1",
        semantic_class="fixture",
        geometry=S.geometry_point("a3d_world", [10.0, 20.0, 0.0]),
        properties={
            "geometry": S.property_claim(
                None, S.PROVENANCE_MEASURED, "multi-view triangulation",
                ["obs:vector:1"],
                uncertainty=S.uncertainty_covariance([[0.04, 0.0], [0.0, 0.09]], "m^2"),
            ),
            "semantic_class": S.property_claim(
                "fixture", S.PROVENANCE_DERIVED, "vision + vector-map agreement",
                ["obs:vector:1"],
                uncertainty=S.uncertainty_categorical({"fixture": 0.87, "sign": 0.13}),
            ),
            "appearance_style": S.property_claim(
                "tall_slim", S.PROVENANCE_SYNTHESIZED, "readability rule; no source fact",
            ),
        },
        confidence=0.87,
        gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
    )
    store.add_feature(lamp)
    assert store.validate() == []
    provs = {name: c["provenance"] for name, c in lamp["properties"].items()}
    assert set(provs.values()) == {"measured", "derived", "synthesized"}


def test_street_lamp_stays_a_fixture_under_four_different_lowerings():
    """Representation choice never changes canonical truth."""
    store = _minimal_store()
    store.add_feature(
        S.canonical_feature(
            feature_id="feature:fixture:lamp-2",
            semantic_class="fixture",
            geometry=S.geometry_point("a3d_world", [5.0, 5.0]),
            properties={
                "geometry": S.property_claim(
                    None, S.PROVENANCE_MEASURED, "survey", ["obs:vector:1"],
                    uncertainty=S.uncertainty_isotropic(0.2, "m"),
                ),
            },
            confidence=0.8,
            gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
        )
    )
    for i, target in enumerate(("mesh_asset", "card", "glyph_sprite", "engine_primitive")):
        store.add_representation(
            S.generated_representation(
                representation_id=f"rep:lamp-2:{i}",
                feature_id="feature:fixture:lamp-2",
                target=target,
                product={"kind": target, "ref": f"lamp_{target}"},
                lowering={"rule": "fixture -> vertical marker", "compiler": "test"},
                authority_claim=S.AUTHORITY_PRESENTATION_ONLY,
            )
        )
    assert store.validate() == []
    assert store.feature("feature:fixture:lamp-2")["semantic_class"] == "fixture"
    assert len(list(store.representations_for("feature:fixture:lamp-2"))) == 4


def test_minimal_document_validates():
    assert _minimal_store().validate() == []


def test_validator_imports_no_inference_stack():
    """Validation must never require torch/numpy/sklearn to be importable."""
    banned = {"torch", "transformers", "sklearn", "numpy", "scipy", "pandas", "jsonschema"}
    for module in (S, V):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        assert not (imported & banned), f"{module.__name__} imports {imported & banned}"


# ==========================================================================
# 2. determinism
# ==========================================================================


def test_canonical_round_trip_is_byte_identical(tmp_path):
    """load -> save -> load -> save must produce identical bytes."""
    store = _minimal_store()
    first = tmp_path / "a.world_ir.json"
    store.save(first)
    bytes_1 = first.read_bytes()

    reloaded = WorldIRStore.load(first)
    second = tmp_path / "b.world_ir.json"
    reloaded.save(second)
    bytes_2 = second.read_bytes()

    again = WorldIRStore.load(second)
    third = tmp_path / "c.world_ir.json"
    again.save(third)
    bytes_3 = third.read_bytes()

    assert bytes_1 == bytes_2 == bytes_3
    assert json.loads(bytes_1) == json.loads(bytes_3)


def test_key_order_is_stable_regardless_of_insertion_order():
    doc_a = {"b": 1, "a": {"z": 1, "y": 2}}
    doc_b = {"a": {"y": 2, "z": 1}, "b": 1}
    assert S.canonical_dumps(doc_a) == S.canonical_dumps(doc_b)


def test_negative_zero_is_normalized_and_non_finite_is_refused():
    assert S.canonical_dumps({"v": -0.0}) == S.canonical_dumps({"v": 0.0})
    with pytest.raises(S.CanonicalizationError):
        S.canonical_dumps({"v": float("nan")})
    with pytest.raises(S.CanonicalizationError):
        S.canonical_dumps({"v": float("inf")})


# ==========================================================================
# 3. negative tests - each must actually fire
# ==========================================================================


def test_negative_unknown_semantic_class():
    store = _minimal_store()
    store.features[0]["semantic_class"] = "street_lamp_tokyo_variant"
    assert V.E_UNKNOWN_SEMANTIC_CLASS in _codes(store.validate())
    store.features[0]["semantic_class"] = "fixture"
    assert store.validate() == []


def test_negative_duplicate_feature_id():
    store = _minimal_store()
    stored = store.add_feature(copy.deepcopy(store.features[0]))
    assert V.E_DUPLICATE_ID in _codes(store.validate())
    stored["feature_id"] = "feature:fixture:2"
    assert store.validate() == []


def test_negative_geometric_feature_with_no_uncertainty():
    store = _minimal_store()
    claim = store.features[0]["properties"]["geometry"]
    removed = claim.pop("uncertainty")
    assert V.E_MISSING_UNCERTAINTY in _codes(store.validate())
    claim["uncertainty"] = removed
    assert store.validate() == []


def test_negative_measured_property_with_no_supporting_observation():
    store = _minimal_store()
    claim = store.features[0]["properties"]["geometry"]
    store.features[0]["supporting_observation_ids"] = []
    claim["observation_ids"] = []
    assert V.E_MEASURED_WITHOUT_OBSERVATION in _codes(store.validate())
    claim["observation_ids"] = ["obs:vector:1"]
    store.features[0]["supporting_observation_ids"] = ["obs:vector:1"]
    assert store.validate() == []


def test_negative_collision_authority_supported_only_by_synthesized_provenance():
    store = _minimal_store()
    feature = store.features[0]
    feature["semantic_class"] = "wall"
    feature["gameplay_authority"] = S.AUTHORITY_COLLISION
    feature["properties"]["geometry"]["provenance"] = S.PROVENANCE_SYNTHESIZED
    feature["properties"]["geometry"]["observation_ids"] = []
    feature["supporting_observation_ids"] = ["obs:vector:1"]
    codes = _codes(store.validate())
    assert V.E_SYNTHESIZED_AUTHORITY in codes
    feature["properties"]["geometry"]["provenance"] = S.PROVENANCE_MEASURED
    feature["properties"]["geometry"]["observation_ids"] = ["obs:vector:1"]
    assert store.validate() == []


def test_negative_walkability_authority_supported_only_by_synthesized_provenance():
    store = _minimal_store()
    feature = store.features[0]
    feature["semantic_class"] = "sidewalk"
    feature["gameplay_authority"] = S.AUTHORITY_WALKABILITY
    feature["properties"]["geometry"]["provenance"] = S.PROVENANCE_SYNTHESIZED
    feature["properties"]["geometry"]["observation_ids"] = []
    assert V.E_SYNTHESIZED_AUTHORITY in _codes(store.validate())


def test_negative_generated_representation_submitted_as_canonical_feature():
    store = _minimal_store()
    rep = S.generated_representation(
        representation_id="rep:1",
        feature_id="feature:fixture:1",
        target="mesh_asset",
        product={"kind": "mesh", "ref": "lamp.akm"},
        lowering={"rule": "fixture -> mesh", "compiler": "test"},
        authority_claim=S.AUTHORITY_PRESENTATION_ONLY,
    )
    # The store refuses it at insertion time ...
    with pytest.raises(V.WorldIRValidationError):
        store.add_feature(rep)
    # ... and the validator refuses it if it is smuggled in around the store.
    store.to_dict()["canonical_features"].append(rep)
    assert V.E_REPRESENTATION_AS_CANONICAL in _codes(store.validate())


def test_negative_representation_choice_stored_as_canonical_truth():
    """A mesh/material choice hidden inside a feature's properties is rejected."""
    store = _minimal_store()
    store.features[0]["properties"]["appearance"] = S.property_claim(
        {"mesh_name": "street_lamp.akm", "material_id": 34},
        S.PROVENANCE_SYNTHESIZED,
        "picked a mesh",
    )
    codes = _codes(store.validate())
    assert codes.count(V.E_REPRESENTATION_AS_CANONICAL) == 2  # mesh_name + material_id
    del store.features[0]["properties"]["appearance"]
    assert store.validate() == []


def test_negative_conflict_resolved_with_no_resolution_record():
    store = _minimal_store()
    feature = store.features[0]
    feature["conflict"] = {
        "state": S.CONFLICT_RESOLVED_BY_REVIEW,
        "contradictions": [
            S.contradiction("height_m", ["obs:vector:1", "obs:vector:1"], [4.5, 12.0])
        ],
        "resolution": None,
    }
    assert V.E_RESOLVED_WITHOUT_RESOLUTION in _codes(store.validate())
    feature["conflict"]["resolution"] = {
        "reviewer": "operator",
        "decided_at": "2026-09-11T00:00:00Z",
        "rationale": "LiDAR tier wins over the levels tag",
        "chosen": {"observation_id": "obs:vector:1", "value": 12.0},
    }
    assert store.validate() == []


def test_negative_resolution_that_deletes_the_contradictory_evidence():
    """Conflicts are preserved: resolving must not erase what disagreed."""
    store = _minimal_store()
    store.features[0]["conflict"] = {
        "state": S.CONFLICT_RESOLVED_BY_REVIEW,
        "contradictions": [],
        "resolution": {
            "reviewer": "operator",
            "decided_at": "2026-09-11T00:00:00Z",
            "rationale": "picked one",
            "chosen": {"value": 12.0},
        },
    }
    assert V.E_CONFLICT_EVIDENCE_LOST in _codes(store.validate())


def test_negative_schema_version_mismatch():
    store = _minimal_store()
    store.to_dict()["schema_version"] = "y92.world_ir/0.9.0"
    findings = store.validate()
    assert V.E_SCHEMA_VERSION in _codes(findings)
    store.to_dict()["schema_version"] = S.SCHEMA_VERSION
    assert store.validate() == []


def test_negative_dangling_observation_reference():
    store = _minimal_store()
    store.features[0]["properties"]["geometry"]["observation_ids"] = ["obs:does-not-exist"]
    store.features[0]["supporting_observation_ids"] = ["obs:does-not-exist"]
    assert _codes(store.validate()).count(V.E_UNKNOWN_OBSERVATION_REF) == 2


def test_negative_representation_cannot_escalate_authority():
    store = _minimal_store()
    store.add_representation(
        S.generated_representation(
            representation_id="rep:escalate",
            feature_id="feature:fixture:1",
            target="mesh_asset",
            product={"kind": "mesh", "ref": "lamp.akm"},
            lowering={"rule": "fixture -> mesh", "compiler": "test"},
            authority_claim=S.AUTHORITY_COLLISION,
        )
    )
    assert V.E_AUTHORITY_ESCALATION in _codes(store.validate())
    store.representations[0]["authority_claim"] = S.AUTHORITY_PRESENTATION_ONLY
    assert store.validate() == []


def test_negative_unknown_field_and_unknown_authority():
    store = _minimal_store()
    store.features[0]["colour"] = "white"
    store.features[0]["gameplay_authority"] = "blocks_everything"
    codes = _codes(store.validate())
    assert V.E_UNKNOWN_FIELD in codes
    assert V.E_UNKNOWN_AUTHORITY in codes


def test_negative_presentation_requirements_cannot_carry_a_representation():
    store = _minimal_store()
    store.features[0]["presentation_requirements"]["mesh_name"] = "lamp.akm"
    codes = _codes(store.validate())
    assert V.E_REPRESENTATION_AS_CANONICAL in codes
    assert V.E_BAD_PRESENTATION in codes


def test_negative_unknown_geometry_frame_and_bad_confidence():
    store = _minimal_store()
    store.features[0]["geometry"]["frame_id"] = "undeclared_frame"
    store.features[0]["confidence"] = 1.4
    codes = _codes(store.validate())
    assert V.E_UNKNOWN_FRAME in codes
    assert V.E_BAD_CONFIDENCE in codes


def test_negative_unresolved_conflict_needs_two_disagreeing_observations():
    store = _minimal_store()
    store.features[0]["conflict"] = {
        "state": S.CONFLICT_UNRESOLVED,
        "contradictions": [{"property": "height_m", "observation_ids": ["obs:vector:1"], "values": [4.5]}],
        "resolution": None,
    }
    assert V.E_BAD_TYPE in _codes(store.validate())


# ==========================================================================
# 4. optional structural cross-check with jsonschema (skipped when absent)
# ==========================================================================


def test_published_json_schema_agrees_with_the_handrolled_validator():
    jsonschema = pytest.importorskip("jsonschema")
    store = _minimal_store()
    jsonschema.validate(json.loads(store.dumps()), S.json_schema())
    bad = json.loads(store.dumps())
    bad["canonical_features"][0]["semantic_class"] = "not_a_class"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S.json_schema())


# ==========================================================================
# 5. expressiveness against REAL FL-4787 repository data
# ==========================================================================

pytestmark_real = pytest.mark.real_asset_e2e

REAL_BUILDING_REF = "way/54722289"  # Earth & Space Sciences; has OSM roof:colour


def _real_palette() -> dict:
    """Recompile the real source palette from the checked-in run inputs."""
    import compile_source_palette as csp

    roles = sorted(set(json.loads(CARTO_ROLES.read_text(encoding="utf-8")).values()))
    return csp.compile_palette(str(BUILDING_INSTANCES), str(OSM_INPUT), roles)


def _real_building_instance(ref: str) -> dict:
    for spec in json.loads(BUILDING_INSTANCES.read_text(encoding="utf-8")):
        if spec.get("source") == ref:
            return spec
    raise AssertionError(f"{ref} missing from {BUILDING_INSTANCES}")


def _real_carto_row(ref: str, kind: str) -> dict:
    want = ref.split("/")
    for row in _load_carto_features():
        if row["osm_type"] == want[0] and row["osm_id"] == want[1] and row["feature_kind"] == kind:
            return row
    raise AssertionError(f"{kind} {ref} missing from {CARTO_FEATURES}")


@pytest.mark.real_asset_e2e
def test_real_run_inputs_exist():
    for path in (CARTO_FEATURES, BUILDING_INSTANCES, OSM_INPUT, CARTO_ROLES):
        assert path.is_file(), f"real FL-4787 input missing: {path}"


@pytest.mark.real_asset_e2e
def test_every_real_carto_feature_kind_maps_onto_the_closed_class_registry():
    rows = _load_carto_features()
    assert len(rows) == 391, "the checked-in run holds 391 carto features"
    kinds = {row["feature_kind"] for row in rows}
    unmapped = kinds - set(CARTO_KIND_TO_SEMANTIC_CLASS)
    assert not unmapped, f"real source kinds with no IR class: {sorted(unmapped)}"
    for kind, semantic in CARTO_KIND_TO_SEMANTIC_CLASS.items():
        assert semantic in S.SEMANTIC_CLASSES, f"{kind} -> {semantic} is not a declared class"


@pytest.mark.real_asset_e2e
def test_whole_real_carto_run_ingests_and_validates(tmp_path):
    """All 391 real OSM vegetation/fixture/road/building features -> World IR."""
    rows = _load_carto_features()
    store = WorldIRStore.new("fl4811-real-carto", generated_at="2026-09-11T00:00:00Z")
    store.add_frame(A3D_FRAME)
    footprints = _building_footprints()
    for row in rows:
        _ingest_carto_row(store, row, footprints)

    findings = store.validate()
    assert findings == [], f"real-data ingest produced findings: {findings[:5]}"
    assert len(store.observations) == len(rows) == 391
    assert len(store.features) == 391

    histogram: dict[str, int] = {}
    for feature in store.features:
        histogram[feature["semantic_class"]] = histogram.get(feature["semantic_class"], 0) + 1
    # Counts follow straight from the real run; they are asserted so a changed
    # source run cannot silently pass this test.
    assert histogram == {
        "sidewalk": 142,
        "terrain": 103,
        "road": 51,
        "tree": 53,
        "building_footprint": 27,
        "fixture": 14,
        "hedge": 1,
    }

    # Determinism over a real 391-feature document, not a toy one.
    first = tmp_path / "real_a.json"
    store.save(first)
    reloaded = WorldIRStore.load(first)
    second = tmp_path / "real_b.json"
    reloaded.save(second)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.real_asset_e2e
def test_real_tree_becomes_a_tree_feature_with_presentation_only_authority():
    trees = [r for r in _load_carto_features() if r["feature_kind"] == "tree"]
    assert len(trees) == 53
    store = WorldIRStore.new("fl4811-real-tree")
    store.add_frame(A3D_FRAME)
    assert not any(_is_truncated(r) for r in trees)
    _, feature = _ingest_carto_row(store, trees[0])
    assert store.validate() == []
    assert feature["semantic_class"] == "tree"
    assert feature["gameplay_authority"] == S.AUTHORITY_PRESENTATION_ONLY
    assert feature["geometry"]["kind"] == "point"
    assert feature["properties"]["geometry"]["provenance"] == S.PROVENANCE_MEASURED
    assert trees[0]["source_tags"] == {"natural": "tree"}


@pytest.mark.real_asset_e2e
def test_real_building_holds_osm_footprint_measured_roof_tier_and_palette_family():
    """One real building expressed end to end, with mixed property provenance.

    Sources fused here, all from the checked-in run:
      * OSM footprint + tags      -> measured geometry, derived class
      * baked A3D instance height -> derived height, and a preserved conflict
      * source palette family     -> derived colour family
      * OSM roof:colour=white     -> measured roof colour
      * LiDAR roof tier           -> measured elevation (SHAPE from the real
        producer; see the module docstring on the missing tile)
    """
    import compile_lidar_roof_field as roof_mod

    row = _real_carto_row(REAL_BUILDING_REF, "building")
    spec = _real_building_instance(REAL_BUILDING_REF)
    # The carto row truncates this footprint; the complete ring lives in the
    # baked instance record, so the IR ingests the lossless source.
    assert _is_truncated(row)
    ring = spec["footprint"]
    palette = _real_palette()
    entry = palette["building_material"][REAL_BUILDING_REF]
    roof_material = palette["materials"][str(entry["roof"])]
    wall_material = palette["materials"][str(entry["wall"])]

    # Real repo facts, asserted so the test fails if the run data changes.
    assert row["source_tags"]["roof:colour"] == "white"
    assert row["source_tags"]["building"] == "university"
    assert entry["family"] == "brick_red"
    assert entry["provenance_class"] == S.PROVENANCE_DERIVED
    assert roof_material["provenance_class"] == S.PROVENANCE_MEASURED
    assert wall_material["provenance_class"] == S.PROVENANCE_DERIVED
    assert spec["bake_height"] == 826

    # The roof-tier record shape comes from the real producer's source.
    region_keys = _extract_appended_dict_keys(
        PIPELINE_DIR / "compile_lidar_roof_field.py", "regions"
    )
    assert {"elevation_m_above_ground", "vertex_fraction", "residual_rms_m"} <= region_keys
    roof_region = {
        "region": 0,
        "elevation_m_above_ground": 11.482,
        "vertex_count": 4821,
        "vertex_fraction": 0.7029,
        "residual_rms_m": 0.148,
        "estimator": "median of lattice-vertex medians within tier bandwidth",
    }
    assert set(roof_region) == region_keys, "producer changed its roof-region shape"

    store = WorldIRStore.new("fl4811-real-building", generated_at="2026-09-11T00:00:00Z")
    store.add_frame(A3D_FRAME)
    store.add_frame(
        S.coordinate_frame(
            frame_id="lidar_source",
            crs="EPSG:6347",
            units="m",
            vertical_datum="NAVD88 (Geoid18)",
            note="defaults declared by scripts/pipeline/compile_lidar_roof_field.py",
        )
    )

    obs_vector = store.add_observation(
        S.source_observation(
            observation_id=f"obs:vector:{REAL_BUILDING_REF}",
            source_id="osm_vector_extract",
            source_kind="vector_map",
            observed_at="2026-06-01T03:39:00Z",
            attributes={"source_ref": REAL_BUILDING_REF, "tags": row["source_tags"]},
            geometry=S.geometry_polygon("a3d_world", ring),
        )
    )
    obs_bake = store.add_observation(
        S.source_observation(
            observation_id=f"obs:bake:{REAL_BUILDING_REF}",
            source_id="a3d_building_bake",
            source_kind="procedural",
            observed_at="2026-06-01T03:40:00Z",
            attributes={
                "source_ref": REAL_BUILDING_REF,
                "bake_height_world": spec["bake_height"],
                "footprint_vertex_count": len(spec["footprint"]),
            },
        )
    )
    obs_lidar = store.add_observation(
        S.source_observation(
            observation_id=f"obs:point_cloud:{REAL_BUILDING_REF}",
            source_id="lidar_tile",
            source_kind="point_cloud",
            observed_at="2026-06-01T00:00:00Z",
            attributes={"roof_region": roof_region, "field_schema": "asciicker.roof_field/v1"},
            quality={
                "estimator": roof_region["estimator"],
                "residual_rms_m": roof_region["residual_rms_m"],
            },
        )
    )
    assert roof_mod.PROVENANCE_MEASURED == S.PROVENANCE_MEASURED
    assert roof_mod.PROVENANCE_DERIVED == S.PROVENANCE_DERIVED

    building = store.add_feature(
        S.canonical_feature(
            feature_id=f"feature:building_footprint:{REAL_BUILDING_REF}",
            semantic_class="building_footprint",
            geometry=S.geometry_polygon("a3d_world", ring),
            properties={
                "geometry": S.property_claim(
                    None, S.PROVENANCE_MEASURED,
                    "OSM way node coordinates projected into the run frame",
                    [obs_vector["observation_id"]],
                    uncertainty=S.uncertainty_isotropic(OSM_POSITION_SIGMA, "a3d_world_units"),
                ),
                "semantic_class": S.property_claim(
                    "building_footprint", S.PROVENANCE_DERIVED,
                    "OSM building=university tag mapped onto the class registry",
                    [obs_vector["observation_id"]],
                    uncertainty=S.uncertainty_categorical({"building_footprint": 1.0}),
                ),
                "name": S.property_claim(
                    row["source_tags"]["name"], S.PROVENANCE_MEASURED,
                    "OSM name tag", [obs_vector["observation_id"]],
                ),
                "colour_family": S.property_claim(
                    entry["family"], entry["provenance_class"],
                    "compile_source_palette family selection: " + "; ".join(entry["reasons"]),
                    [obs_vector["observation_id"]],
                ),
                "roof_colour": S.property_claim(
                    row["source_tags"]["roof:colour"], roof_material["provenance_class"],
                    roof_material["source"], [obs_vector["observation_id"]],
                ),
                "height_world_units": S.property_claim(
                    spec["bake_height"], S.PROVENANCE_DERIVED,
                    "height baked by the A3D building pass",
                    [obs_bake["observation_id"]],
                    uncertainty=S.uncertainty_bounds(0.0, float(spec["bake_height"]), "a3d_world_units"),
                ),
            },
            confidence=0.95,
            gameplay_authority=S.AUTHORITY_COLLISION,
            authority_supporting_properties=["geometry"],
            conflict=S.conflict_unresolved(
                [
                    S.contradiction(
                        "height_world_units",
                        [obs_bake["observation_id"], obs_lidar["observation_id"]],
                        [spec["bake_height"], roof_region["elevation_m_above_ground"]],
                    )
                ]
            ),
            presentation=S.presentation_requirements(
                visible=True, silhouette_required=True, legibility_class="landmark"
            ),
        )
    )

    roof = store.add_feature(
        S.canonical_feature(
            feature_id=f"feature:roof:{REAL_BUILDING_REF}#region0",
            semantic_class="roof",
            geometry=S.geometry_polygon("a3d_world", ring),
            parent_feature_id=building["feature_id"],
            properties={
                "geometry": S.property_claim(
                    None, S.PROVENANCE_DERIVED,
                    "roof region outline inherited from the OSM footprint",
                    [obs_vector["observation_id"]],
                    uncertainty=S.uncertainty_isotropic(OSM_POSITION_SIGMA, "a3d_world_units"),
                ),
                "elevation_m_above_ground": S.property_claim(
                    roof_region["elevation_m_above_ground"], S.PROVENANCE_MEASURED,
                    roof_region["estimator"], [obs_lidar["observation_id"]],
                    uncertainty=S.uncertainty_isotropic(roof_region["residual_rms_m"], "m"),
                ),
                "vertex_fraction": S.property_claim(
                    roof_region["vertex_fraction"], S.PROVENANCE_DERIVED,
                    "share of lattice vertices snapped to this tier",
                    [obs_lidar["observation_id"]],
                ),
                "vertex_count": S.property_claim(
                    roof_region["vertex_count"], S.PROVENANCE_MEASURED,
                    "lattice vertices carrying a measured return",
                    [obs_lidar["observation_id"]],
                ),
                "tier_index": S.property_claim(
                    roof_region["region"], S.PROVENANCE_DERIVED,
                    "tier ordinal from 1-D mode seeking", [obs_lidar["observation_id"]],
                ),
            },
            confidence=0.9,
            gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
        )
    )

    findings = store.validate()
    assert findings == [], f"real building did not validate: {findings}"

    # Property-level provenance really is mixed on one object.
    provs = {c["provenance"] for c in building["properties"].values()}
    assert provs == {"measured", "derived"}
    assert roof["properties"]["elevation_m_above_ground"]["provenance"] == "measured"
    assert roof["properties"]["vertex_fraction"]["provenance"] == "derived"
    # Every roof-region key the real producer emits has a home in the IR.
    housed = set(roof["properties"]) | {"estimator"}
    for key in region_keys:
        assert key in housed or key in {"region", "residual_rms_m"}, key


@pytest.mark.real_asset_e2e
def test_real_facade_shell_is_presentation_only_and_cannot_claim_collision():
    """The alpha-255 no-collision contract, expressed as IR authority."""
    import compile_facade_shell as facade_mod

    assert facade_mod.ALPHA_NO_COLLISION == 255
    assert facade_mod.HEIGHT_SCALE == 16.0
    instance_keys = _extract_appended_dict_keys(
        PIPELINE_DIR / "compile_facade_shell.py", "instances"
    )
    assert instance_keys == {
        "feature_id", "mesh_name", "inst_name", "transform", "flags",
        "story_id", "provenance", "source", "facade_profile",
    }
    # Every one of those keys is a representation fact; none may sit on truth.
    leaking = instance_keys & S.REPRESENTATION_ONLY_KEYS
    assert {"mesh_name", "transform"} <= leaking

    row = _real_carto_row(REAL_BUILDING_REF, "building")
    spec = _real_building_instance(REAL_BUILDING_REF)
    ring = spec["footprint"]
    palette = _real_palette()
    entry = palette["building_material"][REAL_BUILDING_REF]
    wall_material = palette["materials"][str(entry["wall"])]

    store = WorldIRStore.new("fl4811-real-facade")
    store.add_frame(A3D_FRAME)
    obs = store.add_observation(
        S.source_observation(
            observation_id=f"obs:vector:{REAL_BUILDING_REF}",
            source_id="osm_vector_extract",
            source_kind="vector_map",
            observed_at="2026-06-01T03:39:00Z",
            attributes={"source_ref": REAL_BUILDING_REF, "tags": row["source_tags"]},
        )
    )
    shell = store.add_feature(
        S.canonical_feature(
            feature_id=f"feature:facade_plane:{REAL_BUILDING_REF}",
            semantic_class="facade_plane",
            geometry=S.geometry_prism("a3d_world", ring, 0.0, float(spec["bake_height"])),
            properties={
                "geometry": S.property_claim(
                    None, S.PROVENANCE_DERIVED,
                    "OSM footprint extruded to the dominant measured LiDAR roof tier",
                    [obs["observation_id"]],
                    uncertainty=S.uncertainty_isotropic(OSM_POSITION_SIGMA, "a3d_world_units"),
                ),
                "colour_family": S.property_claim(
                    entry["family"], wall_material["provenance_class"],
                    wall_material["source"], [obs["observation_id"]],
                ),
            },
            confidence=0.8,
            # The shell exists to be seen and must never become physical truth.
            gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
            presentation=S.presentation_requirements(
                visible=True,
                silhouette_required=True,
                must_not_imply_collision=True,
                legibility_class="landmark",
                notes="vertical surface must read as a wall; terrain keeps walkability",
            ),
        )
    )
    store.add_representation(
        S.generated_representation(
            representation_id=f"rep:facade:{REAL_BUILDING_REF}",
            feature_id=shell["feature_id"],
            target="mesh_asset",
            product={
                "kind": "akm_shell",
                "mesh_name": f"facade_way_54722289.akm",
                "vertex_alpha": facade_mod.ALPHA_NO_COLLISION,
                "transform_scale_z": facade_mod.HEIGHT_SCALE,
            },
            lowering={
                "rule": "OSM footprint extruded to the dominant measured LiDAR roof tier",
                "compiler": "scripts/pipeline/compile_facade_shell.py",
                "collision": "none; alpha=255 drops every triangle at physics.cpp:663",
            },
            authority_claim=S.AUTHORITY_PRESENTATION_ONLY,
        )
    )
    assert store.validate() == []

    # Promoting the shell's lowered product to collision truth is rejected.
    store.representations[0]["authority_claim"] = S.AUTHORITY_COLLISION
    assert V.E_AUTHORITY_ESCALATION in _codes(store.validate())


@pytest.mark.real_asset_e2e
def test_real_palette_entry_splits_between_world_truth_and_representation():
    """Palette material ids are representation; family and provenance are truth."""
    palette = _real_palette()
    entry = palette["building_material"][REAL_BUILDING_REF]
    assert set(entry) == {"name", "family", "roof", "wall", "provenance_class", "reasons"}
    provenance_counts: dict[str, int] = {}
    for material in palette["materials"].values():
        cls = material["provenance_class"]
        provenance_counts[cls] = provenance_counts.get(cls, 0) + 1
    # Real recompiled counts from the checked-in run.
    assert provenance_counts == {"derived": 61, "synthesized": 10, "measured": 2}
    assert set(provenance_counts) <= set(S.PROVENANCE_CLASSES)
    assert len(palette["building_material"]) == 23

    store = _minimal_store()
    feature = store.features[0]
    # Material ids on world truth are rejected as representation choices.
    feature["properties"]["palette"] = S.property_claim(
        {"material_id": entry["wall"]}, S.PROVENANCE_DERIVED, "palette", ["obs:vector:1"]
    )
    assert V.E_REPRESENTATION_AS_CANONICAL in _codes(store.validate())
    # The family and its provenance are world truth and are accepted.
    del feature["properties"]["palette"]
    feature["properties"]["colour_family"] = S.property_claim(
        entry["family"], entry["provenance_class"], "; ".join(entry["reasons"]), ["obs:vector:1"]
    )
    assert store.validate() == []


@pytest.mark.real_asset_e2e
def test_real_height_conflict_is_preserved_through_review():
    """Two real sources disagree about one building's height; both survive."""
    spec = _real_building_instance(REAL_BUILDING_REF)
    ring = spec["footprint"]
    store = WorldIRStore.new("fl4811-real-conflict")
    store.add_frame(A3D_FRAME)
    for source_id, kind, oid in (
        ("a3d_building_bake", "procedural", "obs:bake"),
        ("lidar_tile", "point_cloud", "obs:point_cloud"),
    ):
        store.add_observation(
            S.source_observation(
                observation_id=oid,
                source_id=source_id,
                source_kind=kind,
                observed_at="2026-06-01T03:40:00Z",
                attributes={"source_ref": REAL_BUILDING_REF},
            )
        )
    disagreement = S.contradiction(
        "height_world_units",
        ["obs:bake", "obs:point_cloud"],
        [spec["bake_height"], 11.482],
    )
    feature = store.add_feature(
        S.canonical_feature(
            feature_id=f"feature:building_footprint:{REAL_BUILDING_REF}",
            semantic_class="building_footprint",
            geometry=S.geometry_polygon("a3d_world", ring),
            properties={
                "geometry": S.property_claim(
                    None, S.PROVENANCE_MEASURED, "OSM way nodes", ["obs:bake"],
                    uncertainty=S.uncertainty_isotropic(OSM_POSITION_SIGMA, "a3d_world_units"),
                ),
                "height_world_units": S.property_claim(
                    spec["bake_height"], S.PROVENANCE_DERIVED, "A3D bake",
                    ["obs:bake"],
                    alternatives=[
                        {
                            "value": 11.482,
                            "provenance": S.PROVENANCE_MEASURED,
                            "observation_ids": ["obs:point_cloud"],
                            "note": "LiDAR dominant roof tier, metres above local ground",
                        }
                    ],
                    uncertainty=S.uncertainty_bounds(0.0, float(spec["bake_height"]), "a3d_world_units"),
                ),
            },
            confidence=0.9,
            gameplay_authority=S.AUTHORITY_COLLISION,
            conflict=S.conflict_unresolved([disagreement]),
        )
    )
    assert store.validate() == []
    assert feature["conflict"]["state"] == S.CONFLICT_UNRESOLVED

    feature["conflict"] = S.conflict_resolved(
        [disagreement],
        reviewer="operator",
        decided_at="2026-09-11T00:00:00Z",
        rationale="the measured LiDAR tier outranks the baked legacy height",
        chosen={"observation_id": "obs:point_cloud", "value": 11.482, "units": "m"},
    )
    assert store.validate() == []
    # Resolution ADDED a record; the losing observation is still on file.
    assert feature["conflict"]["contradictions"] == [disagreement]
    assert "obs:bake" in feature["conflict"]["contradictions"][0]["observation_ids"]
    assert store.observation("obs:bake") is not None


@pytest.mark.real_asset_e2e
def test_real_carto_jsonl_truncates_large_polygons_and_the_validator_rejects_them():
    """FL-4811 real-data finding, asserted so it cannot be forgotten.

    ``output_terrain_only.a3d.carto_features.jsonl`` caps ``world_vertices`` at
    40 points and appends the literal string ``"...truncated"``.  Six of the
    391 rows are affected, five of them buildings whose complete rings (up to
    73 vertices) survive only in ``building_instances.json``.  Feeding the
    truncated ring straight into the IR is a validation error, not a silent
    rounding: lossy source geometry cannot become world truth.
    """
    rows = _load_carto_features()
    truncated = [r for r in rows if _is_truncated(r)]
    assert len(truncated) == 6
    assert {f"{r['osm_type']}/{r['osm_id']}" for r in truncated} == {
        "way/54722289",
        "way/55446707",
        "way/60911995",
        "way/60912003",
        "way/289263333",
        "way/967696497",
    }
    for row in truncated:
        assert row["world_vertices"][-1] == TRUNCATION_MARKER
        assert len(row["world_vertices"]) == 41

    footprints = _building_footprints()
    recoverable = [r for r in truncated if f"{r['osm_type']}/{r['osm_id']}" in footprints]
    assert len(recoverable) == 5
    assert len(footprints["way/55446707"]) == 73, "the complete ring is longer than 40"

    # The truncated ring itself is refused by the validator.
    store = _minimal_store()
    store.features[0]["geometry"] = {
        "kind": "polygon",
        "frame_id": "a3d_world",
        "coordinates": truncated[0]["world_vertices"],
    }
    assert V.E_BAD_GEOMETRY in _codes(store.validate())


@pytest.mark.real_asset_e2e
def test_real_row_with_no_lossless_source_falls_back_to_derived_bounds_geometry():
    """The one truncated row with no complete ring anywhere in the run."""
    row = _real_carto_row("way/967696497", "pedestrian_area")
    assert _is_truncated(row)
    assert "way/967696497" not in _building_footprints()

    store = WorldIRStore.new("fl4811-real-bounds-fallback")
    store.add_frame(A3D_FRAME)
    _, feature = _ingest_carto_row(store, row, _building_footprints())
    assert store.validate() == []
    claim = feature["properties"]["geometry"]
    # Reconstructed geometry is DERIVED, never measured, and states how far the
    # reconstruction may be off.
    assert claim["provenance"] == S.PROVENANCE_DERIVED
    assert claim["uncertainty"]["model"] == "bounds"
    assert "truncated" in claim["method"]
    assert feature["semantic_class"] == "sidewalk"
    assert feature["gameplay_authority"] == S.AUTHORITY_WALKABILITY


# ==========================================================================
# 5. the command-line front door
# ==========================================================================
# REGRESSION GUARD: the CLI first shipped reading the STORE's short attribute
# names (features/observations/frames) instead of the document's canonical keys
# (canonical_features/source_observations/coordinate_frames), so it printed
# "features=0" for a valid 391-feature document while still exiting 0. A
# validator that under-reports what it checked is worse than one that fails, so
# the counts are asserted here and not just the exit status.


def _cli(*args: str) -> "subprocess.CompletedProcess[str]":
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "scripts.pipeline.world_ir", *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


@pytest.mark.skipif(not CARTO_FEATURES.is_file(), reason="real carto run not present")
def test_cli_validates_a_real_ingested_run_and_reports_true_counts(tmp_path):
    store, receipt = ingest.build_document(CARTO_FEATURES, BUILDING_INSTANCES, ir_id="cli-check")
    doc_path = tmp_path / "world_ir.json"
    store.save(doc_path)
    assert receipt["features_emitted"] > 300

    done = _cli("validate", str(doc_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[world-ir] ok" in done.stdout
    # the real counts must appear, not zeros
    assert f"features={receipt['features_emitted']}" in done.stdout
    assert f"observations={receipt['observations_emitted']}" in done.stdout
    assert "frames=1" in done.stdout

    checked = _cli("canonicalize", "--check", str(doc_path))
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.skipif(not CARTO_FEATURES.is_file(), reason="real carto run not present")
def test_cli_exits_nonzero_and_names_the_code_on_a_defective_document(tmp_path):
    store, _receipt = ingest.build_document(CARTO_FEATURES, BUILDING_INSTANCES, ir_id="cli-bad")
    doc = store.to_dict()
    doc["canonical_features"][0]["semantic_class"] = "skyscraper_of_doom"
    bad = tmp_path / "world_ir_bad.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")

    done = _cli("validate", str(bad))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "E_UNKNOWN_SEMANTIC_CLASS" in done.stdout
    # it still reports what it inspected rather than bailing on the first row
    assert "features=" in done.stdout and "features=0" not in done.stdout


def test_cli_exits_two_on_unreadable_input(tmp_path):
    missing = _cli("validate", str(tmp_path / "nope.json"))
    assert missing.returncode != 0
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert _cli("validate", str(broken)).returncode != 0


@pytest.mark.skipif(not CARTO_FEATURES.is_file(), reason="real carto run not present")
def test_ingest_owner_surfaces_the_source_truncation_defect():
    """The stamper truncates footprints over 40 vertices; ingest must SAY so."""
    _store, receipt = ingest.build_document(CARTO_FEATURES, BUILDING_INSTANCES, ir_id="trunc")
    assert receipt["rows_truncated_by_source"] > 0
    assert (receipt["truncated_rows_repaired_from_complete_footprint"]
            + receipt["truncated_rows_lowered_to_derived_bounds_ring"]
            == receipt["rows_truncated_by_source"])
    assert receipt["unmapped_source_kinds"] == {}
