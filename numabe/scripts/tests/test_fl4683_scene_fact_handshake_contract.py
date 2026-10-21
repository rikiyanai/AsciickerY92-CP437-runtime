from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "assets/glyphs/schema/fl4683.scene_fact_handshake.v1.schema.json"
FIXTURE_PATH = ROOT / "assets/glyphs/fixtures/fl4683.scene_fact_handshake.v1.json"
HEADER_PATH = ROOT / "platform/fl4683_scene_fact_handshake.h"
GLYPH_FACT_SCHEMA_PATH = ROOT / "assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json"
GLYPH_FACT_ARTIFACT_PATH = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
PRESENTER_PATH = ROOT / "platform/terminal_gl_present.cpp"
WEB_CPP_PATH = ROOT / "web/game_web.cpp"
WEB_HTML_PATH = ROOT / "web/game_web.html"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_contract_fixture_validates_against_schema() -> None:
    jsonschema.Draft202012Validator(_load(SCHEMA_PATH)).validate(_load(FIXTURE_PATH))


def test_scene_fact_fields_are_renderer_owned_and_exclude_glyph_metadata() -> None:
    contract = _load(FIXTURE_PATH)
    scene = contract["scene_fact_v1"]
    fields = {row["name"]: row for row in scene["fields"]}
    assert scene["owner"] == "renderer"
    # FL-4683 tonal-discriminator amendment raised this from 24 to 25 by adding
    # `route_family_mask`. The count is asserted exactly, rather than as a
    # minimum, so silently growing the renderer-owned record is a test failure
    # and has to be a deliberate contract decision.
    assert len(fields) == 25
    for required in (
        "frame_id",
        "cell_x",
        "cell_y",
        "surface_id",
        "feature_ref",
        "material_namespace",
        "material_key",
        "role",
        "ramp_bin",
        "density_bin",
        "direction_bin",
        "flow_bin",
        "edge_bin",
        "vertical_relation",
        "topology_event",
        "visibility_state",
        "route_family_mask",
        "payload_sha256",
        "valid",
    ):
        assert fields[required]["owner"] == "renderer"
    assert set(scene["forbidden_fields"]) == {
        "glyph_record_id",
        "glyph_intrinsic_metadata",
        "membership_weight",
        "relation_edge",
        "compiled_cost_weight",
    }


def test_route_family_discriminator_matches_the_native_header() -> None:
    """The JSON contract and the C++ header must name the same route families.

    WHY THIS EXISTS: the route-family discriminator is written by the renderer
    (C++) and read by the offline predictor (Python). If the bit numbers in the
    header and the bit numbers in the contract fixture ever drift apart, a
    prediction would silently be made against the wrong family. This test makes
    that drift a build-time failure instead of a wrong answer.
    """
    contract = _load(FIXTURE_PATH)["route_families"]
    header = HEADER_PATH.read_text(encoding="utf-8")
    record_header = (ROOT / "platform/fl4683_scene_fact_record.h").read_text(
        encoding="utf-8"
    )

    assert contract["discriminator_field"] == "route_family_mask"
    assert contract["discriminator_owner"] == "renderer"
    assert set(contract["legal_masks"]) == {
        "single_family_bit",
        "ramp_density_tonal_pair",
    }

    # The header writes each bit as "1 << N". Map the compiler's route key to
    # the C++ enum name, then read N out of the header and check 2**N matches
    # the bit the fixture records.
    enum_name_by_route_key = {
        "ramp.v1": "FL4683_ROUTE_FAMILY_RAMP_V1",
        "density.v1": "FL4683_ROUTE_FAMILY_DENSITY_V1",
        "edge.v1": "FL4683_ROUTE_FAMILY_EDGE_V1",
        "direction.v1": "FL4683_ROUTE_FAMILY_DIRECTION_V1",
        "flow.v1": "FL4683_ROUTE_FAMILY_FLOW_V1",
        "filled.field.v1": "FL4683_ROUTE_FAMILY_FILLED_FIELD_V1",
        "mesh_feature.bundle.v1": "FL4683_ROUTE_FAMILY_MESH_FEATURE_BUNDLE_V1",
    }
    families = {row["route_key"]: row for row in contract["families"]}
    assert set(families) == set(enum_name_by_route_key)
    for route_key, enum_name in enum_name_by_route_key.items():
        match = re.search(
            rf"{enum_name}\s*=\s*1\s*<<\s*(\d+)", record_header
        )
        assert match is not None, f"{enum_name} missing from the record header"
        assert families[route_key]["bit"] == 1 << int(match.group(1))

    # The lane field named per family must be the one the shared C++ resolver
    # returns for that family, and single-lane families must name none.
    assert families["ramp.v1"]["lane_field"] == "ramp_bin"
    assert families["density.v1"]["lane_field"] == "density_bin"
    assert families["edge.v1"]["lane_field"] == "edge_bin"
    assert families["direction.v1"]["lane_field"] == "direction_bin"
    assert families["flow.v1"]["lane_field"] == "flow_bin"
    assert families["filled.field.v1"]["lane_field"] is None
    assert families["mesh_feature.bundle.v1"]["lane_field"] is None

    # The pre-flight in the handshake header must actually consult the mask.
    assert "Fl4683RouteFamilyMaskIsLegalV1" in header


def test_glyph_program_view_pins_fl4676_without_activating_it() -> None:
    contract = _load(FIXTURE_PATH)
    assert GLYPH_FACT_SCHEMA_PATH.exists()
    artifact = _load(GLYPH_FACT_ARTIFACT_PATH)
    program = contract["glyph_program_v1"]
    assert program["owner"] == "offline_compiler"
    assert contract["identity"]["compiler_artifact_schema"] == artifact["schema"]
    assert contract["identity"]["compiler_artifact_path"] == str(
        GLYPH_FACT_ARTIFACT_PATH.relative_to(ROOT)
    )
    assert program["required_identities"] == [
        "artifact_sha256",
        "artifact_content_sha256",
        "glyph_universe_sha256",
        "scene_fact_schema_sha256",
    ]
    rights = contract["runtime_rights"]
    assert rights["runtime_consumption_allowed_now"] is False
    assert "derive_glyph_metadata_at_runtime" in rights["forbidden_authorities"]
    assert "derive_relation_edges_at_runtime" in rights["forbidden_authorities"]
    assert "invent_membership_at_runtime" in rights["forbidden_authorities"]


def test_selector_contract_outputs_finalglyphresultv2_and_names_fail_closed_states() -> None:
    contract = _load(FIXTURE_PATH)
    assert contract["selector_input_v1"]["owner"] == "single_native_selector"
    assert contract["selector_output_v1"] == {
        "type": "FinalGlyphResultV2",
        "owner_after_selection": "u6_final_tuple_writer",
        "absence_publication": "named_rejection_visible_absence_tuple",
    }
    assert set(contract["rejection_reasons"]) == {
        "runtime_rights_blocked",
        "missing_artifact",
        "artifact_sha_mismatch",
        "glyph_universe_mismatch",
        "scene_schema_mismatch",
        "unknown_glyph_fact",
        "unknown_glyph_record",
        "missing_compiled_address",
        "empty_legal_row",
        "unadmitted_glyph",
        "missing_scene_fact",
        "malformed_scene_fact",
        "frame_order_violation",
        "adapter_abi_mismatch",
    }


def test_adapters_transport_only_and_preserve_frame_order() -> None:
    contract = _load(FIXTURE_PATH)
    adapters = {row["adapter_kind"]: row for row in contract["adapters"]}
    assert set(adapters) == {
        "desktop_term_plus_plus",
        "webassembly_webgl_transport",
    }
    for adapter in adapters.values():
        assert set(adapter["rights"]) == {
            "transport_scene_fact_bytes",
            "transport_final_tuple_bytes",
            "present_final_tuple_without_reselection",
        }
    assert contract["frame_ordering"]["ordering"] == [
        "renderer_emits_scene_fact_v1",
        "adapter_transports_scene_fact_without_reselection",
        "selector_checks_artifact_universe_and_scene_schema_identities",
        "selector_evaluates_compiler_emitted_costs_only",
        "u6_final_tuple_writer_publishes_finalglyphresultv2",
        "presenter_consumes_finalglyphresultv2_without_reselection",
    ]


def test_native_header_compiles_and_preflight_blocks_until_runtime_rights_exist(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    source = tmp_path / "fl4683_contract.cpp"
    binary = tmp_path / "fl4683_contract"
    source.write_text(
        r'''
#include "platform/fl4683_scene_fact_handshake.h"

int main()
{
	Fl4683SelectorPreflightV1 missing =
		Fl4683SelectorInputPreflightV1(nullptr);
	if (missing.accepted != 0 ||
	    missing.rejection_reason != FL4683_REJECTION_MISSING_SCENE_FACT)
		return 1;

	FinalGlyphResultV2 output = {};
	Fl4683SelectorInputV1 input = {};
	input.schema_version = FL4683_SELECTOR_INPUT_SCHEMA_VERSION;
	input.adapter_kind = FL4683_ADAPTER_DESKTOP_TERM_PLUS_PLUS;
	input.scene_fact.schema_version = FL4683_SCENE_FACT_SCHEMA_VERSION;
	input.scene_fact.valid = 1;
	// FL-4683 tonal-discriminator amendment: a valid row must name the compiled
	// route family it belongs to. A tonal cell names the ramp+density pair,
	// which is the only legal multi-family mask.
	input.scene_fact.route_family_mask = FL4683_ROUTE_FAMILY_TONAL_PAIR_V1;
	input.glyph_program.schema_version = FL4683_GLYPH_PROGRAM_SCHEMA_VERSION;
	input.output_tuple = &output;

	Fl4683SelectorPreflightV1 blocked =
		Fl4683SelectorInputPreflightV1(&input);
	if (blocked.accepted != 0 ||
	    blocked.rejection_reason != FL4683_REJECTION_RUNTIME_RIGHTS_BLOCKED)
		return 2;

	input.glyph_program.runtime_consumption_allowed = 1;
	input.glyph_program.runtime_rights =
		FL4683_RUNTIME_RIGHT_SELECT_FACT_FROM_SCENE_FACT |
		FL4683_RUNTIME_RIGHT_SCORE_COMPILER_EMITTED_COSTS;
	Fl4683SelectorPreflightV1 accepted =
		Fl4683SelectorInputPreflightV1(&input);
	if (accepted.accepted != 1 ||
	    accepted.rejection_reason != FL4683_REJECTION_NONE ||
	    accepted.adapter_kind != FL4683_ADAPTER_DESKTOP_TERM_PLUS_PLUS)
		return 3;

	// A row that names no route family is malformed: two compiled families
	// (ramp and density) share the tonal role code, so nothing could resolve it
	// to one address. The pre-flight must refuse rather than guess.
	Fl4683SelectorInputV1 unnamed = input;
	unnamed.scene_fact.route_family_mask = FL4683_ROUTE_FAMILY_NONE;
	Fl4683SelectorPreflightV1 unnamed_result =
		Fl4683SelectorInputPreflightV1(&unnamed);
	if (unnamed_result.accepted != 0 ||
	    unnamed_result.rejection_reason != FL4683_REJECTION_MALFORMED_SCENE_FACT)
		return 4;

	// A row claiming two families that are NOT the tonal pair is a
	// contradiction: the live owner never addresses, say, edge and direction at
	// once. Refused for the same fail-closed reason.
	Fl4683SelectorInputV1 contradictory = input;
	contradictory.scene_fact.route_family_mask =
		(uint8_t)(FL4683_ROUTE_FAMILY_EDGE_V1 | FL4683_ROUTE_FAMILY_DIRECTION_V1);
	Fl4683SelectorPreflightV1 contradictory_result =
		Fl4683SelectorInputPreflightV1(&contradictory);
	if (contradictory_result.accepted != 0 ||
	    contradictory_result.rejection_reason !=
	        FL4683_REJECTION_MALFORMED_SCENE_FACT)
		return 5;

	// Each single-family row resolves to exactly the lane its own bin field
	// names. This is the mapping the renderer, the offline predictor, and any
	// future selector must all agree on.
	SceneFactV1 lanes = {};
	lanes.ramp_bin = 2;
	lanes.density_bin = 3;
	lanes.edge_bin = 5;
	lanes.direction_bin = 7;
	lanes.flow_bin = 1;
	if (Fl4683SceneFactLaneForRouteFamilyV1(&lanes, FL4683_ROUTE_FAMILY_RAMP_V1) != 2 ||
	    Fl4683SceneFactLaneForRouteFamilyV1(&lanes, FL4683_ROUTE_FAMILY_DENSITY_V1) != 3 ||
	    Fl4683SceneFactLaneForRouteFamilyV1(&lanes, FL4683_ROUTE_FAMILY_EDGE_V1) != 5 ||
	    Fl4683SceneFactLaneForRouteFamilyV1(&lanes, FL4683_ROUTE_FAMILY_DIRECTION_V1) != 7 ||
	    Fl4683SceneFactLaneForRouteFamilyV1(&lanes, FL4683_ROUTE_FAMILY_FLOW_V1) != 1 ||
	    Fl4683SceneFactLaneForRouteFamilyV1(
	        &lanes, FL4683_ROUTE_FAMILY_FILLED_FIELD_V1) != 0 ||
	    Fl4683SceneFactLaneForRouteFamilyV1(
	        &lanes, FL4683_ROUTE_FAMILY_MESH_FEATURE_BUNDLE_V1) != 0)
		return 6;
	return 0;
}
''',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            "-I.",
            "-Iengine",
            "-Iengine/render",
            str(source),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    run = subprocess.run([str(binary)], text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stderr + run.stdout


def test_contract_does_not_activate_a_runtime_loader_selector_presenter() -> None:
    header = HEADER_PATH.read_text(encoding="utf-8")
    presenter = PRESENTER_PATH.read_text(encoding="utf-8")
    web_cpp = WEB_CPP_PATH.read_text(encoding="utf-8")
    web_html = WEB_HTML_PATH.read_text(encoding="utf-8")
    runtime_text = "\n".join((presenter, web_cpp, web_html))
    assert "fl4683_scene_fact_handshake.h" not in runtime_text
    assert "Fl4683SelectorInputPreflightV1" not in runtime_text
    assert "FL4683_RUNTIME_RIGHT_SELECT_FACT_FROM_SCENE_FACT" not in runtime_text
    assert "Fl4683Load" not in header
    assert "Fl4683Install" not in header
    assert "Fl4683RuntimeEnable" not in header
    assert _load(FIXTURE_PATH)["non_activation"] == {
        "installs_loader": False,
        "installs_selector": False,
        "installs_presenter": False,
        "activates_runtime_consumption": False,
    }
