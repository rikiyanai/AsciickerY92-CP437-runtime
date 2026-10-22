"""Focused P0 tests, lane A: OSM mass-only join + TrueFF frame markers."""

import json

import pytest

from scripts.maintainer.p0 import lane_osm, lane_trueff

BLDG = "feature:building_footprint:way/1001"
BLDG2 = "feature:building_footprint:way/1002"
PLACE = "feature:place:node/55"


def _plan_row(fid, **overrides):
    row = {
        "feature_id": fid,
        "canonical_id": fid.rsplit("/", 1)[-1],
        "semantic_class": "building",
        "reconstruction_class": "C",
        "importance": "route_visible",
        "evidence_state": "derived",
        "disposition": "terrain_mass",
        "representation_kind": "terrain",
        "evidence_complete": True,
        "policy_rule": "representation_policy:observed_defaults:mass_class",
        "representation_id": "terrain_mass:" + fid,
        "owner_stage": "bake",
        "reason": "R-MASS: conflated footprint bakes as terrain mass",
        "source_bridge": "entities:auditfix01:claims",
        "primary": True,
        "jev_route": None,
        "jev_decision": None,
    }
    row.update(overrides)
    return row


def _facade_row(fid):
    return _plan_row(
        fid,
        disposition="mesh_facade",
        representation_kind="observed_physical",
        policy_rule="facade_archetype_defaults_by_use:building_facade",
        representation_id="building_facade:" + fid,
        owner_stage="population_fixtures",
        reason=(
            "R-BUILDING-FACADE: canonical mass retains its primary terrain "
            "representation and receives one rules-selected facade"
        ),
        primary=False,
    )


def _place_row():
    return _plan_row(
        PLACE,
        semantic_class="place",
        disposition="mesh_facade",
        representation_kind="synthetic_identity_proxy",
        evidence_complete=False,
        policy_rule="representation_policy:debt_policy_by_importance:"
        "route_visible_C_rule_backed_facade",
        representation_id="place_front:" + PLACE,
        owner_stage="place_fronts",
        reason=(
            "R-FACADE-SYNTHETIC: canonical place identity and locale profile "
            "authorize a marked reconstruction"
        ),
        primary=True,
    )


def _mass_emission(fid):
    return {
        "record_kind": "emitted_instance",
        "owner": "building_mass",
        "placement_ids": [],
        "feature_id": fid,
        "representation_id": None,
        "inst_name": "building_mass_way_1001",
        "terminal_state": "emitted",
    }


def _facade_emission(fid):
    return {
        "record_kind": "emitted_instance",
        "owner": "fixtures",
        "placement_ids": ["facade-place-1"],
        "feature_id": fid,
        "representation_id": "building_facade:" + fid,
        "inst_name": "lower_fixtures_rule-facade-1001",
        "terminal_state": "emitted",
    }


def _manifest(*emissions):
    return {"schema": "y92.emission_manifest.v1", "emissions": list(emissions)}


def _receipt():
    return {
        "schema": "y92.representation_plan_receipt.v2",
        "inputs": {"canonical": "aa", "route_importance": "bb"},
    }


# --- OSM join: mass-only vs facade-placed vs place-row ignored ---


def test_osm_mass_only_building_counted():
    plan = {"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]}
    result = lane_osm.mass_only_join(plan, _manifest(_mass_emission(BLDG)), _receipt())
    assert result["mass_only"] == [BLDG]
    assert result["mass_only_count"] == 1
    assert result["facade_placed"] == []
    assert result["route_importance_wired"] is True
    assert result["ok"] is True


def test_osm_facade_placed_building_excluded():
    plan = {
        "schema": "y92.representation_plan.v2",
        "rows": [_plan_row(BLDG), _facade_row(BLDG)],
    }
    result = lane_osm.mass_only_join(
        plan, _manifest(_mass_emission(BLDG), _facade_emission(BLDG)), _receipt()
    )
    assert result["mass_only"] == []
    assert result["mass_only_count"] == 0
    assert result["facade_placed"] == [BLDG]


def test_osm_place_row_ignored():
    plan = {"schema": "y92.representation_plan.v2", "rows": [_place_row()]}
    result = lane_osm.mass_only_join(plan, _manifest(), _receipt())
    assert result["candidates"] == []
    assert result["mass_only_count"] == 0


def test_osm_unemitted_candidate_not_counted():
    plan = {"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG2)]}
    result = lane_osm.mass_only_join(plan, _manifest(_mass_emission(BLDG)), _receipt())
    assert result["mass_only"] == []
    assert result["unemitted"] == [BLDG2]


def test_osm_inventory_list_form_counts():
    plan = {"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]}
    inventory = [
        {
            "inst_name": "building_mass_way_1001",
            "mesh_name": "mass.akm",
            "feature_id": BLDG,
        }
    ]
    result = lane_osm.mass_only_join(plan, inventory)
    assert result["mass_only"] == [BLDG]
    assert result["route_importance_wired"] is False

def test_osm_planned_but_unemitted_facade_is_mass_only():
    # REGRESSION: primary R-MASS + secondary R-BUILDING-FACADE plan rows,
    # mass emitted, facade NOT emitted. Facade evidence is emission-side
    # only: a planned-but-unemitted facade must not set facade_placed.
    plan = {
        "schema": "y92.representation_plan.v2",
        "rows": [_plan_row(BLDG), _facade_row(BLDG)],
    }
    result = lane_osm.mass_only_join(plan, _manifest(_mass_emission(BLDG)), _receipt())
    feat = result["features"][BLDG]
    assert feat["mass_planned"] is True
    assert feat["facade_planned"] is True
    assert feat["mass_emitted"] is True
    assert feat["facade_emitted"] is False
    assert feat["mass_only_emitted"] is True
    assert feat["planned_facade_missing_emission"] is True
    assert result["mass_only"] == [BLDG]
    assert result["mass_only_emitted"] == [BLDG]
    assert result["mass_only_count"] == 1
    assert result["planned_facade_missing_emission"] == [BLDG]
    assert result["facade_placed"] == []
    assert result["facade_emitted"] == []


def test_osm_non_emitted_facade_record_ignored():
    # A facade record that never reached terminal_state "emitted" is not
    # facade evidence, even with contract-shaped identity fields.
    debt_facade = dict(_facade_emission(BLDG))
    debt_facade["terminal_state"] = "representation_debt"
    plan = {
        "schema": "y92.representation_plan.v2",
        "rows": [_plan_row(BLDG), _facade_row(BLDG)],
    }
    result = lane_osm.mass_only_join(
        plan, _manifest(_mass_emission(BLDG), debt_facade), _receipt()
    )
    assert result["features"][BLDG]["facade_emitted"] is False
    assert result["mass_only"] == [BLDG]
    assert result["facade_placed"] == []
    assert result["planned_facade_missing_emission"] == [BLDG]


def test_osm_emitted_surface_without_mass_ownership_is_not_mass():
    # Generic emitted_surface (owner "surface") must not imply mass, even
    # when it carries a terrain_mass representation_id.
    surface_record = {
        "record_kind": "emitted_surface",
        "owner": "surface",
        "placement_ids": [],
        "feature_id": BLDG,
        "representation_id": "terrain_mass:" + BLDG,
        "attachment_id": None,
        "inst_name": None,
        "mesh_name": None,
        "terminal_state": "emitted",
    }
    plan = {"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]}
    result = lane_osm.mass_only_join(plan, _manifest(surface_record), _receipt())
    assert result["features"][BLDG]["mass_emitted"] is False
    assert result["mass_only"] == []
    assert result["mass_only_count"] == 0
    assert result["unemitted"] == [BLDG]


def test_osm_generic_facade_substring_is_not_facade_evidence():
    # Bare "facade" substring in inst_name is not a contract value; only
    # the documented lower_fixtures_rule-facade- prefix (or a
    # building_facade: representation_id / mesh_facade disposition) counts.
    lookalike = {
        "record_kind": "emitted_instance",
        "owner": "fixtures",
        "placement_ids": ["x"],
        "feature_id": BLDG,
        "representation_id": None,
        "inst_name": "some_facade_thing",
        "terminal_state": "emitted",
    }
    plan = {"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]}
    result = lane_osm.mass_only_join(plan, _manifest(lookalike), _receipt())
    assert result["features"][BLDG]["facade_emitted"] is False
    assert result["facade_placed"] == []
    assert result["unemitted"] == [BLDG]



def test_osm_malformed_inputs_raise():
    with pytest.raises(ValueError):
        lane_osm.mass_only_join({"rows": "not-a-list"}, _manifest())
    with pytest.raises(ValueError):
        lane_osm.mass_only_join({"schema": "y92.representation_plan.v2"}, _manifest())
    with pytest.raises(ValueError):
        lane_osm.mass_only_join(
            {"rows": [_plan_row(BLDG)]}, {"schema": "y92.emission_manifest.v1"}
        )
    with pytest.raises(ValueError):
        # Wrong plan schema marker.
        lane_osm.mass_only_join(
            {"schema": "y92.representation_plan.v1", "rows": [_plan_row(BLDG)]},
            _manifest(),
        )
    with pytest.raises(ValueError):
        # Missing plan schema marker.
        lane_osm.mass_only_join({"rows": [_plan_row(BLDG)]}, _manifest())
    with pytest.raises(ValueError):
        # Wrong plan-receipt schema marker.
        lane_osm.mass_only_join(
            {"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]},
            _manifest(),
            {"schema": "y92.other_receipt.v1", "inputs": {}},
        )


def test_osm_cli_receipt_and_malformed_exit(tmp_path, capsys):
    plan_path = tmp_path / "representation_plan.json"
    plan_path.write_text(
        json.dumps({"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]}),
        encoding="utf-8",
    )
    emission_path = tmp_path / "emission_manifest.json"
    emission_path.write_text(
        json.dumps(_manifest(_mass_emission(BLDG))), encoding="utf-8"
    )
    receipt_path = tmp_path / "receipt.json"
    # Count is data, not a gate: exit 0 even with count > 0.
    assert (
        lane_osm.main(
            [str(plan_path), str(emission_path), "--receipt", str(receipt_path)]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["mass_only_count"] == 1 and out["ok"] is True
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["ok"] is True

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not json", encoding="utf-8")
    assert lane_osm.main([str(bad_path), str(emission_path)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_osm_cli_schema_mismatch_exit_2(tmp_path, capsys):
    emission_path = tmp_path / "emission_manifest.json"
    emission_path.write_text(
        json.dumps(_manifest(_mass_emission(BLDG))), encoding="utf-8"
    )
    # Wrong plan schema marker => exit 2.
    plan_path = tmp_path / "representation_plan.json"
    plan_path.write_text(
        json.dumps({"schema": "y92.representation_plan.v1", "rows": [_plan_row(BLDG)]}),
        encoding="utf-8",
    )
    assert lane_osm.main([str(plan_path), str(emission_path)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    # Wrong plan-receipt schema marker => exit 2.
    plan_path.write_text(
        json.dumps({"schema": "y92.representation_plan.v2", "rows": [_plan_row(BLDG)]}),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "plan_receipt.json"
    receipt_path.write_text(
        json.dumps({"schema": "y92.other_receipt.v1", "inputs": {}}), encoding="utf-8"
    )
    assert (
        lane_osm.main(
            [str(plan_path), str(emission_path), "--plan-receipt", str(receipt_path)]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["ok"] is False


# --- TrueFF markers: good log, missing, duplicate, malformed ---

GOOD_LOG = """[render] fl4691_frame_3_perspective
[render] fl4359_true_ff_depth_begin t=100
[render] fl4359_true_ff_depth_end t=150
[render] fl4359_true_ff_raster_begin t=200
[render] fl4359_true_ff_raster_end t=260
"""


def test_trueff_good_log_passes():
    result = lane_trueff.check_fl4691_markers(GOOD_LOG)
    assert result["ok"] is True
    assert result["count_ok"] is True
    assert result["malformed"] == []
    assert result["projection_ok"] is True
    assert result["frame_index"] == 3
    assert result["ordered_pairs_ok"] is True


def test_trueff_missing_marker_fails():
    result = lane_trueff.check_fl4691_markers("[render] nothing here\n")
    assert result["frame_marker_count"] == 0
    assert result["count_ok"] is False
    assert result["ok"] is False


def test_trueff_duplicate_marker_fails():
    result = lane_trueff.check_fl4691_markers(GOOD_LOG + "fl4691_frame_3_perspective\n")
    assert result["frame_marker_count"] == 2
    assert result["count_ok"] is False
    assert result["ok"] is False


@pytest.mark.parametrize(
    "token",
    [
        "fl4691_frame_3_mercator",
        "fl4691_frame_x_perspective",
        "fl4691_frame_3",
        "fl4691_frame_3_perspective_extra",
    ],
)
def test_trueff_malformed_marker_fails(token):
    result = lane_trueff.check_fl4691_markers("saw %s in log\n" % token)
    assert result["malformed"] == [token]
    assert result["projection_ok"] is False
    assert result["ok"] is False


def test_trueff_unordered_pair_fails():
    log = (
        "fl4691_frame_7_orthographic\n"
        "fl4359_true_ff_depth_end t=150\n"
        "fl4359_true_ff_depth_begin t=100\n"
    )
    result = lane_trueff.check_fl4691_markers(log)
    assert result["count_ok"] is True
    assert result["ordered_pairs_ok"] is False
    assert result["ok"] is False


def test_trueff_cli_file_and_exit_codes(tmp_path, capsys):
    log_path = tmp_path / "frame.log"
    log_path.write_text(GOOD_LOG, encoding="utf-8")
    assert lane_trueff.main([str(log_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["frame_index"] == 3

    log_path.write_text("[render] nothing here\n", encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    assert lane_trueff.main([str(log_path), "--receipt", str(receipt_path)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["ok"] is False
