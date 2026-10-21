import json
import re
import struct
from pathlib import Path

import pytest

from scripts.pipeline import compile_preassembly_candidate as candidate
from scripts.pipeline.compile_preassembly_candidate import build
from scripts.pipeline.compile_population_ground_plan import (
    compile_grounded_dispatch,
    require_grounded,
)
from scripts.pipeline.compile_world_frame import write_metadata
from scripts.pipeline.dispatch_population_plan import SCHEMA as DISPATCH_SCHEMA
from scripts.pipeline.population_representation_contract import (
    linear_module_ids,
)
from scripts.pipeline.population_asset_contract import (
    SCHEMA as POPULATION_ASSET_SCHEMA,
    sha256,
)


def _write(path: Path, document) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _projected_route(monkeypatch):
    monkeypatch.setattr(
        candidate, "_project_route",
        lambda *_args: ([(0.0, 0.0), (100.0, 0.0)], {}))


def _dispatch(owner: str, placements: list[dict], total: int) -> dict:
    carriers = ([{"carrier_id": "carrier:test", "placements": placements}]
                if placements else [])
    return {
        "carriers": carriers,
        "dispatch": {
            "schema": DISPATCH_SCHEMA,
            "owner": owner,
            "placements_total": total,
            "placements_for_owner": len(placements),
            "placements_by_owner": {
                "linear": 0, "fixtures": total,
                "custom": 0, "terminal": 0,
            },
        },
    }


def _placement(placement_id: str, feature_id: str) -> dict:
    return {
        "placement_id": placement_id,
        "module_id": "bench",
        "lowering_owner": "fixtures",
        "representation_decision": {
            "feature_id": feature_id,
            "representation_id": "rep:" + placement_id,
            "disposition": "mesh_fixture",
        },
        "asset_physical_envelope": {
            "max_longitudinal_overhang_m": 0.0,
        },
        "resolved_pose": {"E": 1.0, "N": 2.0, "yaw_deg": 0.0},
        "resolved_frame": {
            "tangent": [1.0, 0.0], "normal": [0.0, 1.0],
        },
        "planned_feature_id": feature_id,
        "planned_parent_feature_id": feature_id,
        "asset_contract": {"module_id": "test"},
    }


def _candidate_inputs(tmp_path: Path) -> dict:
    feature_ids = ("feature:building", "feature:surface",
                   "feature:fixture", "feature:place")
    canonical = {"canonical_features": [
        {"feature_id": feature_id} for feature_id in feature_ids
    ]}
    representation = {"rows": [
        {"feature_id": "feature:building", "primary": True,
         "disposition": "terrain_mass", "evidence_complete": True},
        {"feature_id": "feature:building", "primary": False,
         "representation_id": "rep:placement:building-facade",
         "disposition": "mesh_facade", "evidence_complete": True,
         "attachment_state": "resolved",
         "attachment": {"anchor_kind": "building_face"}},
        {"feature_id": "feature:surface", "primary": True,
         "disposition": "surface_decal", "evidence_complete": True,
         "attachment_state": "resolved"},
        {"feature_id": "feature:fixture", "primary": True,
         "representation_id": "rep:placement:fixture",
         "disposition": "mesh_fixture", "evidence_complete": True,
         "attachment_state": "resolved"},
        {"feature_id": "feature:place", "primary": True,
         "representation_id": "rep:placement:place",
         "disposition": "mesh_facade", "evidence_complete": True,
         "attachment_state": "resolved"},
    ]}
    fixture_placements = [
        _placement("placement:building-facade", "feature:building"),
        _placement("placement:fixture", "feature:fixture"),
        _placement("placement:place", "feature:place"),
    ]
    fixture_placements[0]["representation_decision"][
        "disposition"] = "mesh_facade"
    dispatch_dir = tmp_path / "dispatch"
    for owner in ("linear", "fixtures", "custom", "terminal"):
        document = _dispatch(
            owner, fixture_placements if owner == "fixtures" else [], 3)
        _write(dispatch_dir / f"population_plan.{owner}.json", document)
    asset_root = tmp_path / "meshes"
    asset_root.mkdir()
    asset_rows = []
    for placement in fixture_placements:
        mesh = asset_root / f"{placement['placement_id'].replace(':', '_')}.akm"
        mesh.write_bytes(b"test-akm")
        asset_rows.append({
            "owner": "fixtures",
            "placement_id": placement["placement_id"],
            "mesh_name": mesh.name,
            "mesh_sha256": sha256(mesh),
            **({"appearance_contract": {
                "colour_method": "test fixture association",
            }} if placement["placement_id"] ==
                "placement:building-facade" else {}),
        })
    custom_asset_plan_path = _write(
        tmp_path / "custom_assets.json", {"rows": []})
    source_palette_path = _write(
        tmp_path / "source_palette.json", {
            "building_material_by_feature": {
                "feature:building": {"facade_profile": {
                    "colour_method": "test fixture association"}}
            }})
    population_asset_plan_path = _write(
        tmp_path / "population_assets.json", {
            "schema": POPULATION_ASSET_SCHEMA,
            "asset_root": str(asset_root),
            "inputs": {
                "dispatch_sha256": {
                    owner: sha256(
                        dispatch_dir / f"population_plan.{owner}.json")
                    for owner in ("linear", "fixtures", "custom", "terminal")
                },
                "custom_asset_plan_sha256": sha256(custom_asset_plan_path),
                "source_palette_sha256": sha256(source_palette_path),
            },
            "rows": asset_rows,
        })
    station_mesh = asset_root / "station_facade.akm"
    station_mesh.write_bytes(b"station-akm")
    return {
        "canonical_path": _write(tmp_path / "canonical.json", canonical),
        "representation_plan_path": _write(
            tmp_path / "representation.json", representation),
        "dispatch_dir": dispatch_dir,
        "custom_asset_plan_path": custom_asset_plan_path,
        "population_asset_plan_path": population_asset_plan_path,
        "source_palette_path": source_palette_path,
        "surface_stamp_plan_path": _write(
            tmp_path / "surfaces.json", {"rows": [{
                "feature_id": "feature:surface",
                "terminal_state": "painted",
            }]}),
        "station_facade_plan_path": _write(
            tmp_path / "station.json", {
                "lowering_owner": "station_facade",
                "placements": [{"placement_id": "station:one"}],
                "asset": {
                    "mesh_path": str(station_mesh),
                    "mesh_sha256": sha256(station_mesh),
                },
            }),
        "building_specs_path": _write(
            tmp_path / "buildings.json",
            [{"feature_id": "feature:building"}]),
        "requirements_path": _write(
            tmp_path / "requirements.json", {
                "hero_facades": [{
                    "id": "home_block", "source_coverage": True,
                    "requirement": "street_derived",
                    "feature_id": "feature:place",
                }],
                "required_resolved_archetypes": {"place_fronts": [{
                    "feature_id": "feature:place",
                }]},
                "route_composition_requirements": {"required_places": [{
                    "feature_id": "feature:place",
                }]},
            }),
        "route_path": _write(tmp_path / "route.json", {"route": "test"}),
        "terrain_metadata_path": _write(
            tmp_path / "terrain_metadata.json", {
                "content_scale": 1.0,
                "origin_easting": 0.0,
                "origin_northing": 0.0,
                "offset_x": 0.0,
                "offset_y": 0.0,
            }),
    }


def test_complete_plan_is_ready_before_a3d_assembly(tmp_path):
    document = build(**_candidate_inputs(tmp_path))
    assert document["ready_for_a3d_assembly"] is True
    assert document["blockers"] == []
    assert document["counts"]["population_placements"] == 3
    assert document["counts"]["building_facades"] == 1


def test_representation_debt_and_missing_population_refuse_assembly(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    representation = json.loads(
        inputs["representation_plan_path"].read_text(encoding="utf-8"))
    representation["rows"][3]["disposition"] = "observation_owed"
    representation["rows"][3]["evidence_complete"] = False
    _write(inputs["representation_plan_path"], representation)
    fixtures = json.loads(
        (inputs["dispatch_dir"] / "population_plan.fixtures.json").read_text(
            encoding="utf-8"))
    fixtures["carriers"][0]["placements"] = [
        placement for placement in fixtures["carriers"][0]["placements"]
        if placement["placement_id"] != "placement:fixture"]
    fixtures["dispatch"]["placements_for_owner"] = 2
    _write(inputs["dispatch_dir"] / "population_plan.fixtures.json", fixtures)

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    assert {row["kind"] for row in document["blockers"]} >= {
        "representation_debt", "evidence_debt",
    }


def test_world_frame_compiles_without_a3d(tmp_path):
    aoi = _write(tmp_path / "aoi.geojson", {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"role": "home_start"},
             "geometry": {"type": "Point",
                          "coordinates": [139.68, 35.58]}},
            {"type": "Feature",
             "properties": {"role": "selected_station_entrance"},
             "geometry": {"type": "Point",
                          "coordinates": [139.681, 35.581]}},
            {"type": "Feature", "properties": {"role": "aoi"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [139.679, 35.579], [139.682, 35.579],
                 [139.682, 35.582], [139.679, 35.582],
                 [139.679, 35.579],
             ]]}},
        ],
    })
    metadata = write_metadata(aoi, tmp_path / "terrain_metadata.json")
    assert metadata["projection_kind"] == "epsg32654_affine"
    assert metadata["terrain_bounds"]["max_x"] > 0
    assert not list(tmp_path.glob("*.a3d"))


def test_sparse_population_refuses_route_density_before_assembly(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    requirements = json.loads(
        inputs["requirements_path"].read_text(encoding="utf-8"))
    requirements["route_composition_requirements"][
        "streetscape_route_coverage"] = {
            "utility_pole": {
                "bin_m": 25.0,
                "max_route_offset_m": 10.0,
                "minimum_covered_fraction": 0.75,
            },
        }
    _write(inputs["requirements_path"], requirements)
    fixtures = json.loads(
        (inputs["dispatch_dir"] / "population_plan.fixtures.json").read_text(
            encoding="utf-8"))
    fixtures["carriers"][0]["placements"][0][
        "module_id"] = "utility_pole_wire_cluster"
    _write(inputs["dispatch_dir"] / "population_plan.fixtures.json", fixtures)

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    assert any(row["kind"] == "route_density" for row in document["blockers"])
    coverage = document["streetscape_route_coverage"][0]
    assert coverage["covered_fraction"] == 0.25


def test_source_coverage_and_catalog_name_do_not_substitute_for_emission(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    requirements = json.loads(
        inputs["requirements_path"].read_text(encoding="utf-8"))
    requirements["hero_facades"][0].pop("source_coverage")
    requirements["hero_facades"][0]["source_coverage"] = True
    requirements["hero_facades"][0].pop("feature_id")
    requirements["required_resolved_archetypes"]["fixtures"] = [{
        "semantic_class": "stop_sign",
        "template_id": "jp.urban.stop_sign_v1",
    }]
    requirements["route_composition_requirements"][
        "required_infrastructure_counts"] = {
            "station_platform_proposal": 1,
        }
    _write(inputs["requirements_path"], requirements)

    document = build(**inputs)
    kinds = {row["kind"] for row in document["blockers"]}
    assert "required_facade_identity" in kinds
    assert "required_fixture_archetype" in kinds
    assert "required_infrastructure" in kinds


def test_direct_runner_gates_candidate_before_classic_base():
    source = Path("scripts/sbu_e2e_run.py").read_text(encoding="utf-8")
    gate = source.index("write_preassembly_candidate(")
    assembly = source.index("base = compile_base(", gate)
    assert gate < assembly


def test_planner_and_metric_do_not_import_a_lowerer_as_contract_owner():
    planner = Path("scripts/pipeline/compile_population_plan.py").read_text(
        encoding="utf-8")
    metric = Path(
        "scripts/pipeline/compile_object_metric_receipt.py").read_text(
            encoding="utf-8")
    assert "from scripts.pipeline.lower_plan_linear import" not in planner
    assert "from scripts.pipeline.lower_plan_linear import" not in metric
    assert linear_module_ids([
        {"module_id": "metal_fence", "owning_domain": "property_frontage"},
        {"module_id": "street_tree", "owning_domain": "vegetation"},
        {"module_id": "bench", "owning_domain": "street_fixture"},
    ], include_points=False) == {"metal_fence"}


def test_asset_authoring_is_absent_from_population_lowerers():
    asset_plan = Path(
        "scripts/pipeline/compile_population_asset_plan.py").read_text(
            encoding="utf-8")
    assert "population_asset_geometry import" in asset_plan
    for lowerer_path in (
        "scripts/pipeline/lower_plan_fixtures.py",
        "scripts/pipeline/lower_plan_linear.py",
    ):
        lowerer = Path(lowerer_path).read_text(encoding="utf-8")
        assert "write_akm" not in lowerer
        assert "def build_run_panel" not in lowerer
        assert "def build_unit_mesh" not in lowerer
        assert "def build_point_mesh" not in lowerer


def test_dispatch_resolves_asset_envelope_before_attachment_pose():
    source = Path("scripts/pipeline/dispatch_population_plan.py").read_text(
        encoding="utf-8")
    envelope = source.index(
        'envelope = placement.get("asset_physical_envelope")')
    pose = source.index('placement["resolved_pose"] =')
    assert envelope < pose


def test_ground_plan_is_the_only_population_terrain_sampler(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    terrain = tmp_path / "terrain.a3d"
    payload = bytearray(16 + 188)
    struct.pack_into("<4sII", payload, 0, b"AS3D", 1, 1)
    struct.pack_into("<ii", payload, 16, 0, 0)
    struct.pack_into("<25H", payload, 16 + 8 + 128, *([200] * 25))
    terrain.write_bytes(payload)
    outputs = compile_grounded_dispatch(
        dispatch_dir=inputs["dispatch_dir"],
        terrain_metadata_path=inputs["terrain_metadata_path"],
        terrain_map_path=terrain,
        out_dir=tmp_path / "grounded")
    grounded = json.loads(outputs["fixtures"].read_text(encoding="utf-8"))
    require_grounded(grounded, "fixtures")
    rows = grounded["carriers"][0]["placements"]
    assert {row["ground_bake_z"] for row in rows} == {200.0}
    for filename in (
            "lower_plan_fixtures.py", "lower_plan_custom.py",
            "lower_plan_linear.py"):
        source = Path("scripts/pipeline", filename).read_text(encoding="utf-8")
        assert "terrain_z" not in source
        assert "load_height_grid" not in source


# ---------------------------------------------------------------------------
# FL-4809 R2: the preassembly approval cross-check.
#
# These fixtures are synthetic on purpose, and they have to be.  The only
# waiver in the lane bundle is WVR-4809-001 with status "proposed" -- a camera
# omission nobody has decided -- so load_authored_waivers returns an empty
# table, compile_representation_plan emits no authored_waiver: row, and a real
# run exercises none of the paths below.  A check that can only be reached by
# a document the lane does not have is a check that has to be reached by a
# document a test writes.
# ---------------------------------------------------------------------------

WAIVER_SCHEMA = "y92.authored_representation_waivers.v2"
APPROVAL_RECORD = "commit:" + "a" * 40
OTHER_APPROVAL_RECORD = "commit:" + "d" * 40
APPROVED_CONDITIONS = "b" * 64
OTHER_CONDITIONS = "c" * 64
WAIVED_FEATURE = "feature:waived"
PRINCIPAL = "Tester Person <tester@example.com>"


def _approved_waiver(**overrides) -> dict:
    row = {
        "waiver_id": "WVR-TEST-001",
        "feature_id": WAIVED_FEATURE,
        "disposition_waived": "observation_owed",
        "reason": "the world supplies no observation and no honest proxy",
        "status": "approved",
        "proposed_by": PRINCIPAL,
        "proposed_on": "2026-01-01",
        "conditions_sha256": APPROVED_CONDITIONS,
        "approved_by": PRINCIPAL,
        "approved_on": "2026-01-02",
        "approval_record": APPROVAL_RECORD,
    }
    row.update(overrides)
    return row


def _proposed_waiver(**overrides) -> dict:
    row = {
        "waiver_id": "WVR-TEST-001",
        "feature_id": WAIVED_FEATURE,
        "disposition_waived": "observation_owed",
        "reason": "an omission somebody is arguing for, undecided",
        "status": "proposed",
        "proposed_by": PRINCIPAL,
        "proposed_on": "2026-01-01",
        "conditions_sha256": APPROVED_CONDITIONS,
    }
    row.update(overrides)
    return row


def _waiver_document(tmp_path: Path, rows: list[dict]) -> Path:
    return _write(tmp_path / "authored_waivers.json", {
        "schema": WAIVER_SCHEMA, "waivers": rows})


def _claim_waiver(inputs: dict, *, approval_record: str,
                  conditions_sha256: str | None = APPROVED_CONDITIONS,
                  feature_id: str = WAIVED_FEATURE) -> None:
    """Add the row compile_representation_plan.emit_waived would write."""
    canonical = json.loads(inputs["canonical_path"].read_text(
        encoding="utf-8"))
    canonical["canonical_features"].append({"feature_id": feature_id})
    _write(inputs["canonical_path"], canonical)
    representation = json.loads(
        inputs["representation_plan_path"].read_text(encoding="utf-8"))
    row = {
        "feature_id": feature_id,
        "primary": True,
        "representation_id": "authored_waiver:" + feature_id,
        "disposition": "intentional_omission",
        "representation_kind": "omitted",
        "evidence_complete": True,
        "policy_rule": "authored_waiver:" + approval_record,
        "owner_stage": "none",
    }
    if conditions_sha256 is not None:
        row["waiver_conditions_sha256"] = conditions_sha256
    representation["rows"].append(row)
    _write(inputs["representation_plan_path"], representation)


def _waiver_blockers(document: dict) -> list[dict]:
    return [row for row in document["blockers"]
            if row["kind"] == candidate.WAIVER_PROVENANCE_KIND]


def test_approved_waiver_with_matching_feature_and_digest_is_not_blocked(
        tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    inputs["authored_waivers_path"] = _waiver_document(
        tmp_path, [_approved_waiver()])

    document = build(**inputs)
    assert _waiver_blockers(document) == []
    assert document["blockers"] == []
    assert document["ready_for_a3d_assembly"] is True
    assert document["counts"]["authored_waiver_claims"] == 1
    assert "authored_waivers_sha256" in document["inputs"]


def test_unresolvable_approval_record_refuses_assembly(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    # An approved waiver exists, but for another feature and another record,
    # so the record this plan cites resolves to nothing at all.
    inputs["authored_waivers_path"] = _waiver_document(tmp_path, [
        _approved_waiver(waiver_id="WVR-TEST-009",
                         feature_id="feature:elsewhere",
                         approval_record=OTHER_APPROVAL_RECORD)])

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1
    assert "WAIVER_APPROVAL_RECORD_UNKNOWN" in blockers[0]["reason"]
    assert blockers[0]["subject"] == WAIVED_FEATURE
    assert APPROVAL_RECORD in blockers[0]["reason"]


def test_proposed_waiver_does_not_discharge_a_plan_claim(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    inputs["authored_waivers_path"] = _waiver_document(
        tmp_path, [_proposed_waiver()])

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1
    assert "WAIVER_APPROVAL_NOT_APPROVED" in blockers[0]["reason"]
    assert blockers[0]["subject"] == WAIVED_FEATURE
    assert APPROVAL_RECORD in blockers[0]["reason"]
    assert "'proposed'" in blockers[0]["reason"]
    assert "WVR-TEST-001" in blockers[0]["reason"]


def test_superseded_waiver_does_not_discharge_a_plan_claim(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    # A retired approval keeps its approver and its record; it is inert.
    inputs["authored_waivers_path"] = _waiver_document(tmp_path, [
        _approved_waiver(status="superseded",
                         superseded_by="WVR-TEST-002",
                         superseded_on="2026-02-01",
                         supersession_reason="replaced by a later decision"),
        _proposed_waiver(waiver_id="WVR-TEST-002",
                         reason="the replacement, not yet decided")])

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1
    assert "WAIVER_APPROVAL_NOT_APPROVED" in blockers[0]["reason"]
    assert blockers[0]["subject"] == WAIVED_FEATURE
    assert APPROVAL_RECORD in blockers[0]["reason"]
    assert "'superseded'" in blockers[0]["reason"]


def test_approval_for_a_different_feature_refuses_assembly(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    inputs["authored_waivers_path"] = _waiver_document(tmp_path, [
        _approved_waiver(feature_id="feature:elsewhere")])

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1
    assert "WAIVER_FEATURE_MISMATCH" in blockers[0]["reason"]
    assert blockers[0]["subject"] == WAIVED_FEATURE
    assert APPROVAL_RECORD in blockers[0]["reason"]
    assert "feature:elsewhere" in blockers[0]["reason"]


def test_conditions_digest_drift_refuses_assembly(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD,
                  conditions_sha256=OTHER_CONDITIONS)
    inputs["authored_waivers_path"] = _waiver_document(
        tmp_path, [_approved_waiver()])

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1
    assert "WAIVER_CONDITIONS_DIGEST_MISMATCH" in blockers[0]["reason"]
    assert blockers[0]["subject"] == WAIVED_FEATURE
    assert APPROVAL_RECORD in blockers[0]["reason"]
    assert OTHER_CONDITIONS in blockers[0]["reason"]
    assert APPROVED_CONDITIONS in blockers[0]["reason"]


def test_waiver_claim_without_a_supplied_document_is_a_blocker(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    # authored_waivers_path deliberately not supplied.

    document = build(**inputs)
    assert document["ready_for_a3d_assembly"] is False
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1
    assert "WAIVER_DOCUMENT_ABSENT" in blockers[0]["reason"]
    assert WAIVED_FEATURE in blockers[0]["reason"]
    assert APPROVAL_RECORD in blockers[0]["reason"]
    assert "authored_waivers_sha256" not in document["inputs"]


def test_a_run_with_no_waiver_claims_needs_no_waiver_document(tmp_path):
    """The check must not become a new way for an honest lane run to fail."""
    document = build(**_candidate_inputs(tmp_path))
    assert document["counts"]["authored_waiver_claims"] == 0
    assert _waiver_blockers(document) == []
    assert document["ready_for_a3d_assembly"] is True


def test_representation_id_is_not_read_as_an_approval_record(tmp_path):
    """emit_waived writes authored_waiver:<feature_id> there, not a record."""
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD)
    inputs["authored_waivers_path"] = _waiver_document(
        tmp_path, [_approved_waiver()])

    document = build(**inputs)
    assert document["counts"]["authored_waiver_claims"] == 1
    assert _waiver_blockers(document) == []


def test_a_claim_without_a_conditions_digest_is_unbound(tmp_path):
    """Deleting a field must not delete the condition binding.

    emit_waived writes waiver_conditions_sha256 beside every claim it makes,
    so a row carrying the claim without it did not come from that path.
    Accepting it would make the one check that ties an approval to the world
    it was approved against removable by dropping a key.
    """
    inputs = _candidate_inputs(tmp_path)
    _claim_waiver(inputs, approval_record=APPROVAL_RECORD,
                  conditions_sha256=None)
    inputs["authored_waivers_path"] = _waiver_document(
        tmp_path, [_approved_waiver()])

    document = build(**inputs)
    blockers = _waiver_blockers(document)
    assert len(blockers) == 1, blockers
    assert "WAIVER_CONDITIONS_UNBOUND" in blockers[0]["reason"]
    assert WAIVED_FEATURE in blockers[0]["reason"]
    assert document["ready_for_a3d_assembly"] is False


def test_the_candidate_receipt_is_preserved_outside_the_run_tree(tmp_path):
    """.run/ is gitignored, so a gate result written only there vanishes.

    This is the receipt naming the blockers that stopped assembly, which a
    later reader cannot reconstruct from anything else.
    """
    inputs = _candidate_inputs(tmp_path)
    out_path = tmp_path / "preassembly_candidate.json"
    document = candidate.write(out_path=out_path, **inputs)

    preserved = Path(document["preserved_path"])
    try:
        assert preserved.is_file()
        assert preserved.read_bytes() == out_path.read_bytes()
        assert candidate.PRESERVED_DIR in preserved.parents
        assert ".run" not in preserved.parts
        # Content-addressed, so re-preserving identical bytes is a no-op.
        assert candidate.preserve_receipt(out_path) == preserved
    finally:
        # Synthetic evidence must not be left in the tracked tree.
        preserved.unlink(missing_ok=True)


def test_preserving_different_bytes_at_one_address_is_refused(tmp_path):
    inputs = _candidate_inputs(tmp_path)
    out_path = tmp_path / "preassembly_candidate.json"
    document = candidate.write(out_path=out_path, **inputs)
    preserved = Path(document["preserved_path"])
    try:
        preserved.write_bytes(preserved.read_bytes() + b" tampered")
        with pytest.raises(RuntimeError, match="disagrees with this run"):
            candidate.preserve_receipt(out_path)
    finally:
        preserved.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# FL-4876. Terminal placements author nothing, so asset coverage skips them.
# That makes terminal the one owner where a placement can vanish without any
# gate noticing, and it is exactly where the facade repair now routes an
# unattachable facade. Every test below starts from an otherwise-green
# candidate, so a failure can only come from the terminal check itself.
# --------------------------------------------------------------------------

def _with_terminal_placement(inputs: dict, placement_id: str,
                             terminal_state, *,
                             module_id: str = "narrow_mixed_use") -> dict:
    """Add one terminal placement to an otherwise-passing candidate."""
    dispatch_dir = inputs["dispatch_dir"]
    placement = {
        "placement_id": placement_id,
        "module_id": module_id,
        "lowering_owner": "terminal",
    }
    if terminal_state is not None:
        placement["terminal_state"] = terminal_state
    for owner in ("linear", "fixtures", "custom", "terminal"):
        path = dispatch_dir / f"population_plan.{owner}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["dispatch"]["placements_total"] = 4
        document["dispatch"]["placements_by_owner"] = {
            "linear": 0, "fixtures": 3, "custom": 0, "terminal": 1}
        if owner == "terminal":
            document["carriers"] = [
                {"carrier_id": "carrier:road", "placements": [placement]}]
            document["dispatch"]["placements_for_owner"] = 1
        _write(path, document)
    # Rewriting the slices changed their bytes, so the asset plan's
    # dispatch_sha256 would otherwise fire four provenance blockers and the
    # terminal check would no longer be the only thing under test.
    asset_plan_path = inputs["population_asset_plan_path"]
    asset_plan = json.loads(asset_plan_path.read_text(encoding="utf-8"))
    asset_plan["inputs"]["dispatch_sha256"] = {
        owner: sha256(dispatch_dir / f"population_plan.{owner}.json")
        for owner in ("linear", "fixtures", "custom", "terminal")}
    _write(asset_plan_path, asset_plan)
    return inputs


def _terminal_blockers(document: dict) -> list[dict]:
    return [row for row in document["blockers"]
            if row["kind"] == candidate.TERMINAL_DEBT_KIND]


def test_the_baseline_candidate_passes_before_any_terminal_placement(tmp_path):
    """Pins that these tests start green, so a failure names one cause."""
    document = build(**_candidate_inputs(tmp_path))
    assert document["blockers"] == []
    assert document["ready_for_a3d_assembly"] is True


@pytest.mark.parametrize("placement_id,cause,distance", [
    ("obs-inst-0217", "nearest_face_beyond_resolve_tolerance", 6.832),
    ("obs-inst-0245", "nearest_face_beyond_resolve_tolerance", 30.907),
    ("obs-inst-0577", "no_building_face_candidate_was_tested", None),
])
def test_an_unfilled_facade_slot_refuses_assembly(
        tmp_path, placement_id, cause, distance):
    """The three placements the facade repair routes to terminal.

    Routing them out of "falsely attached" must not route them into
    "silently omitted". Each one must stop assembly by name.
    """
    inputs = _with_terminal_placement(
        _candidate_inputs(tmp_path), placement_id,
        {"state": "observation_owed", "category": "unfilled_facade_slot",
         "reason": "a facade module resolved to no canonical building face",
         "cause": cause, "nearest_face_distance_m": distance})

    document = build(**inputs)
    blockers = _terminal_blockers(document)
    assert len(blockers) == 1, document["blockers"]
    assert blockers[0]["subject"] == placement_id
    assert "observation_owed" in blockers[0]["reason"]
    assert "unfilled_facade_slot" in blockers[0]["reason"]
    assert document["ready_for_a3d_assembly"] is False
    # Nothing else in the candidate broke; the terminal check is the cause.
    assert document["blockers"] == blockers


def test_a_decided_omission_still_passes(tmp_path):
    """The distinguishing test: 122 of this lane's terminal placements.

    A decided omission authors nothing and is allowed to. If this blocked,
    the gate would simply refuse every run with an intentional omission.
    """
    inputs = _with_terminal_placement(
        _candidate_inputs(tmp_path), "obs-inst-0900",
        {"state": "intentional_omission",
         "category": "consumed_by_station_facade_reconstruction",
         "reason": "the accepted station facade plan owns this assembly"})

    document = build(**inputs)
    assert _terminal_blockers(document) == []
    assert document["blockers"] == []
    assert document["ready_for_a3d_assembly"] is True
    assert document["counts"]["terminal_placements"] == 1


def test_representation_owed_debt_also_refuses_assembly(tmp_path):
    inputs = _with_terminal_placement(
        _candidate_inputs(tmp_path), "obs-inst-0901",
        {"state": "representation_owed", "category": "something_owed",
         "reason": "no representation decided"})

    blockers = _terminal_blockers(build(**inputs))
    assert len(blockers) == 1
    assert "representation_owed" in blockers[0]["reason"]


def test_an_unknown_terminal_state_is_refused_not_assumed_permitted(tmp_path):
    """Fail closed. A state this gate never heard of is the likeliest new
    way to lose a placement quietly."""
    inputs = _with_terminal_placement(
        _candidate_inputs(tmp_path), "obs-inst-0902",
        {"state": "deferred_to_later_pass", "category": "who_knows",
         "reason": "invented state"})

    blockers = _terminal_blockers(build(**inputs))
    assert len(blockers) == 1
    assert "unknown terminal state" in blockers[0]["reason"]
    assert "deferred_to_later_pass" in blockers[0]["reason"]


def test_a_terminal_placement_with_no_state_is_refused(tmp_path):
    inputs = _with_terminal_placement(
        _candidate_inputs(tmp_path), "obs-inst-0903", None)

    blockers = _terminal_blockers(build(**inputs))
    assert len(blockers) == 1
    assert "no terminal_state" in blockers[0]["reason"]


# --------------------------------------------------------------------------
# FL-4876. The planner mints one primary row per triage-unmapped source_ref
# on purpose; the gate independently demanded every primary feature exist in
# canonical. Two contracts, no shared statement. The gate now imports the
# planner's shape, and the exemption must stay narrow.
# --------------------------------------------------------------------------

def _unmapped_row(feature_id: str = "unmapped:node/297026604", **overrides):
    row = {"feature_id": feature_id, "primary": True,
           "representation_id": "intentional_omission:" + feature_id,
           "evidence_complete": True, "attachment_state": "resolved",
           "unmapped_kind": candidate.UNMAPPED_NON_PHYSICAL,
           "unmapped_tags": {"junction": "yes"},
           **candidate.UNMAPPED_ROW_CONTRACT}
    row.update(overrides)
    return row


def _with_primary_row(inputs: dict, row: dict) -> dict:
    path = inputs["representation_plan_path"]
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["rows"].append(row)
    _write(path, plan)
    return inputs


def test_a_triage_unmapped_row_is_not_an_identity_error(tmp_path):
    inputs = _with_primary_row(_candidate_inputs(tmp_path), _unmapped_row())
    document = build(**inputs)
    assert document["blockers"] == []
    assert document["ready_for_a3d_assembly"] is True
    assert document["counts"]["triage_unmapped_rows"] == 1


@pytest.mark.parametrize("field", sorted(candidate.UNMAPPED_ROW_CONTRACT))
def test_the_exemption_requires_every_declared_field(tmp_path, field):
    """Narrowing, not loosening. Break one marker and it blocks again."""
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        _unmapped_row(**{field: "something-else"}))
    document = build(**inputs)
    identity = [b for b in document["blockers"]
                if b["kind"] == "representation_identity"]
    assert len(identity) == 1, document["blockers"]
    assert identity[0]["subject"] == "unmapped:node/297026604"
    assert document["ready_for_a3d_assembly"] is False


def test_a_genuinely_dangling_feature_still_blocks(tmp_path):
    """The check the exemption must not swallow."""
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        {"feature_id": "feature:ghost:node/1", "primary": True,
         "representation_id": "mesh_fixture:feature:ghost:node/1",
         "disposition": "mesh_fixture", "evidence_complete": True,
         "attachment_state": "resolved"})
    document = build(**inputs)
    identity = [b for b in document["blockers"]
                if b["kind"] == "representation_identity"]
    assert len(identity) == 1
    assert identity[0]["subject"] == "feature:ghost:node/1"


def test_an_omission_that_only_claims_the_prefix_still_blocks(tmp_path):
    """A forged id without the planner's shape gets no exemption."""
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        {"feature_id": "unmapped:node/999", "primary": True,
         "representation_id": "intentional_omission:unmapped:node/999",
         "disposition": "intentional_omission", "evidence_complete": True,
         "attachment_state": "resolved"})
    document = build(**inputs)
    identity = [b for b in document["blockers"]
                if b["kind"] == "representation_identity"]
    assert len(identity) == 1
    assert identity[0]["subject"] == "unmapped:node/999"


def test_the_unmapped_contract_matches_what_the_planner_actually_emits():
    """Non-circular check: fixtures built from the contract prove nothing.

    Every other test here constructs its row from UNMAPPED_ROW_CONTRACT, so
    all of them passed while the contract named a key -- "bridge" -- that no
    row has ever carried. The planner stores that argument as
    "source_bridge", so the exemption matched nothing on real data. Read the
    planner's own emit site instead.
    """
    source = (Path(__file__).resolve().parents[2] / "scripts" / "pipeline"
              / "compile_representation_plan.py").read_text(encoding="utf-8")
    start = source.index("for u in sorted(unmapped,")
    emit_site = source[start:start + 700]
    for field in candidate.UNMAPPED_ROW_CONTRACT:
        assert f'UNMAPPED_ROW_CONTRACT["{field}"]' in emit_site or (
            field in ("semantic_class", "evidence_state",
                      "representation_kind")), (
            f"{field} is declared in the contract but the unmapped emit site "
            "does not take it from there")
    # Every contract key must be a key emit() actually writes onto a row.
    row_keys = source[source.index("rows.append({"):
                      source.index("rows.append({") + 1400]
    for field in candidate.UNMAPPED_ROW_CONTRACT:
        assert f'"{field}"' in row_keys, (
            f"contract names {field!r}, which emit() never writes onto a row")


# --------------------------------------------------------------------------
# FL-4876. A mesh row owned by a stage outside population still refuses, but
# with its real reason. The old message sent readers into population looking
# for a row population was never given.
# --------------------------------------------------------------------------

def test_a_place_front_row_refuses_with_its_real_owner(tmp_path):
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        {"feature_id": "feature:place:node/2929215092", "primary": True,
         "representation_id": "place_front:feature:place:node/2929215092",
         "disposition": "mesh_facade", "owner_stage": "place_fronts",
         "evidence_complete": True, "attachment_state": "resolved"})
    document = build(**inputs)
    owned = [b for b in document["blockers"]
             if b["kind"] == "unwired_representation_owner"]
    assert len(owned) == 1, document["blockers"]
    assert owned[0]["subject"] == "feature:place:node/2929215092"
    assert "place_fronts" in owned[0]["reason"]
    assert "compile_world_ir_place_fronts" in owned[0]["reason"]
    # It must still refuse. Naming the cause is not discharging it.
    assert document["ready_for_a3d_assembly"] is False
    assert not [b for b in document["blockers"]
                if b["kind"] == "population_owner"]


def test_an_unknown_owner_stage_refuses(tmp_path):
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        {"feature_id": "feature:fixture:node/7", "primary": True,
         "representation_id": "mesh_fixture:feature:fixture:node/7",
         "disposition": "mesh_fixture", "owner_stage": "some_new_stage",
         "evidence_complete": True, "attachment_state": "resolved"})
    document = build(**inputs)
    owned = [b for b in document["blockers"]
             if b["kind"] == "unwired_representation_owner"]
    assert len(owned) == 1
    assert "some_new_stage" in owned[0]["reason"]
    assert document["ready_for_a3d_assembly"] is False


def test_a_population_owned_row_without_a_placement_still_blocks(tmp_path):
    """The check the reroute must not swallow."""
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        {"feature_id": "feature:fixture:node/8", "primary": True,
         "representation_id": "mesh_fixture:feature:fixture:node/8",
         "disposition": "mesh_fixture", "owner_stage": "fixtures",
         "evidence_complete": True, "attachment_state": "resolved"})
    document = build(**inputs)
    pop = [b for b in document["blockers"]
           if b["kind"] == "population_owner"]
    assert len(pop) == 1
    assert pop[0]["subject"] == "feature:fixture:node/8"


def test_every_owner_stage_the_planner_emits_is_classified():
    """Non-circular: read the planner's own emit sites.

    An owner_stage that is neither a population owner, nor a declared
    emission owner, nor 'none', would blocked every row carrying it with a
    reason nobody can act on.
    """
    from scripts.pipeline.compile_representation_plan import (
        NON_POPULATION_OWNER_STAGES, POPULATION_OWNER_STAGES)
    known = set(POPULATION_OWNER_STAGES) | set(NON_POPULATION_OWNER_STAGES)
    # Stages that emit no mesh and are checked by other branches.
    known |= {"none", "bake", "stamp", "minimap"}
    source = (Path(__file__).resolve().parents[2] / "scripts" / "pipeline"
              / "compile_representation_plan.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'"(bake|stamp|minimap|none|fixtures|'
                             r'population_fixtures|population_linear|'
                             r'place_fronts)"', source))
    assert emitted <= known, sorted(emitted - known)
    assert "place_fronts" in NON_POPULATION_OWNER_STAGES


def test_an_unmapped_physical_feature_still_refuses(tmp_path):
    """A bridge triage could not map is not nothing.

    This lane's five unmapped rows include man_made=bridge and
    landuse=brownfield. Exempting every omission silenced the only complaint
    the pipeline made about a bridge missing from the world.
    """
    inputs = _with_primary_row(
        _candidate_inputs(tmp_path),
        _unmapped_row("unmapped:way/1304205921",
                      unmapped_kind=candidate.UNMAPPED_PHYSICAL,
                      unmapped_tags={"man_made": "bridge", "layer": "1"}))
    document = build(**inputs)
    physical = [b for b in document["blockers"]
                if b["kind"] == "unregistered_physical_feature"]
    assert len(physical) == 1, document["blockers"]
    assert physical[0]["subject"] == "unmapped:way/1304205921"
    assert "man_made" in physical[0]["reason"]
    assert document["ready_for_a3d_assembly"] is False


def test_an_unmapped_row_with_no_classification_refuses(tmp_path):
    """Fail closed: a plan too old to carry the field is not a pass."""
    row = _unmapped_row("unmapped:way/264846635")
    row.pop("unmapped_kind")
    inputs = _with_primary_row(_candidate_inputs(tmp_path), row)
    document = build(**inputs)
    assert [b for b in document["blockers"]
            if b["kind"] == "unregistered_physical_feature"]
    assert document["ready_for_a3d_assembly"] is False


@pytest.mark.parametrize("tags,expected_physical", [
    ({"junction": "yes"}, False),
    ({"junction": "yes", "name": "x", "name:ja": "x"}, False),
    ({"boundary": "administrative", "admin_level": "9"}, False),
    ({"man_made": "bridge", "layer": "1"}, True),
    ({"landuse": "brownfield", "source": "Bing"}, True),
    ({"building": "yes"}, True),
    ({}, False),
])
def test_unmapped_classification_matches_this_lane(tags, expected_physical):
    """The five real records, plus a tag the list has never seen."""
    from scripts.pipeline.compile_representation_plan import (
        UNMAPPED_PHYSICAL, classify_unmapped)
    assert (classify_unmapped(tags) == UNMAPPED_PHYSICAL) is expected_physical
