"""FL-4588 exact action-result union coverage.

These checks keep the Draft 2020-12 schema synchronized with successful
``asciiid.action_result.v1`` bodies authored by AsciiidApplicationService.
Glyph-catalog add/remove now share the addressed candidate-lane result shape;
the retired mixed flat-pool receipt is not an accepted branch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/asciiid.action_result.v1.json"
SOURCE_PATH = ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp"
BASE_REQUIRED = {
    "schema": "asciiid.action_result.v1",
    "ok": True,
    "interaction_id": "fl4588_action_result_test",
    "action_kind": "set_termpp_performance_hud_visible",
    "status": "applied",
    "prior_state_revision": 4,
    "new_state_revision": 5,
    "display_revision": 9,
    "write_set": [],
}

SAMPLE_VALUES = {
    "visible": True,
    "enabled": True,
    "environment_revision": 2,
    "parameter": "density_bias",
    "profile_namespace": "terrain",
    "material_key": 3,
    "value": 1,
    "profile_revision": 2,
    "active_material": 3,
    "brush_shape": "circle",
    "radius": 2.0,
    "height_limit_mode": "none",
    "height_threshold": 0.0,
    "samples_applied": 1,
    "cells_changed": 1,
    "key": "left",
    "input_samples": 1,
    "pos_x": 1.0,
    "pos_y": 2.0,
    "wheel_steps": 1.0,
    "font_size": 16.0,
    "delta_x_pixels": 2.0,
    "delta_y_pixels": 3.0,
    "rot_yaw": 4.0,
    "rot_pitch": 5.0,
    "pos_z": 6.0,
    "probe_z": 7.0,
    "can_undo": True,
    "can_redo": False,
    "tool": "terrain_material",
    "edit_mode": 1,
    "alpha": 0.1,
    "height_limit_enabled": False,
    "patches_touched": 1,
    "prior_display_revision": 8,
    "prior_urdo_revision": 10,
    "next_urdo_revision": 11,
    "op": "set_radius",
    "palette_id": "nord",
    "color_index": 1,
    "start_material_key": 3,
    "keys_assigned": 1,
    "first_key": 3,
    "last_key": 3,
    "lane_id": "ramp.0",
    "glyph_id": 65,
    "sampled_material": 3,
    "mesh_name": "stone",
    "mesh_instance_uid": "42",
    "inspected_count": 3,
    "selected_count": 2,
    "sprite_name": "grass",
    "item_name": "coin",
    "world_x": 1.0,
    "world_y": 2.0,
    "world_z": 3.0,
    "anim": 0,
    "frame": 0,
    "yaw": 0.0,
    "alive_max": 1,
    "revive_min": 0,
    "revive_max": 0,
    "armor": 0,
    "helmet": 0,
    "shield": 0,
    "sword": 5,
    "crossbow": 5,
    "prior_story_id": -1,
    "new_story_id": 4,
    "mode": "blur",
    "cell_x": 1,
    "cell_y": 2,
    "prior_probe_z": 6,
    "created": 1,
    "deleted": 0,
    "tile_radius": 2.0,
    "elevation_bit": 1,
    "sampled_bit": 1,
    "active_elevation": 1,
    "channel": "fg",
    "row": 0,
    "rgb": [10, 20, 30],
    "preset_id": "grass_tops.soft_top_curve",
}


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def route_kinds(source: str, table_name: str) -> set[str]:
    marker = f"static const {table_name[:-1]} {table_name}[] = {{"
    if marker not in source:
        # Route struct names are not mechanically singular for two tables.
        marker = f"{table_name}[] = {{"
    block = source.split(marker, 1)[1].split("\n\t\t};", 1)[0]
    kinds = set()
    for record in re.findall(r"\{[^{}]+\}", block):
        strings = re.findall(r'"([^"]*)"', record)
        if len(strings) >= 3:
            kinds.add(strings[2])
    return kinds


def source_success_kinds() -> set[str]:
    source = SOURCE_PATH.read_text()
    literal_kinds = set(
        re.findall(r'\\"action_kind\\":\\"([a-z0-9_]+)', source)
    )
    routed_kinds = set()
    for table_name in (
        "kColorLeafRoutes",
        "kTerrain42Routes",
        "kSceneObjectRoutes",
        "kCandidateRoutes",
        "kEnv11Routes",
    ):
        routed_kinds.update(route_kinds(source, table_name))
    # CommitSelectTerrainTool computes its result kind from the selected tool.
    dynamic_tool_kinds = {"select_terrain_material_tool", "select_terrain_tool"}
    for kind in dynamic_tool_kinds:
        assert f'"{kind}"' in source
    return literal_kinds | routed_kinds | dynamic_tool_kinds


def branch_result(branch: dict) -> dict:
    result = dict(BASE_REQUIRED)
    properties = branch["properties"]
    result["action_kind"] = properties["action_kind"]["const"]
    for field in branch.get("required", []):
        result[field] = SAMPLE_VALUES[field]
    for field, contract in properties.items():
        if field in {"action_kind", "write_set"}:
            continue
        if "const" in contract:
            result[field] = contract["const"]
        elif "enum" in contract:
            result[field] = contract["enum"][0]
    write_contract = properties["write_set"]
    if "const" in write_contract:
        result["write_set"] = list(write_contract["const"])
    else:
        count = write_contract["minItems"]
        assert count == write_contract["maxItems"]
        result["write_set"] = [f"/source/write/{index}" for index in range(count)]
    if result["action_kind"] == "delete_mesh_instance":
        result["write_set"] = [
            "/document/model/world_instances/42",
            "/undo/transactions/fl4588_action_result_test/instance_delete",
            "/renderer/world_instances",
            "/document/revision",
            "/state_revision",
            "/display_revision",
        ]
    return result


def test_live_success_emitters_have_exact_union_coverage() -> None:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    enum_kinds = set(schema["properties"]["action_kind"]["enum"])
    assert enum_kinds == source_success_kinds()

    branch_kinds = [
        branch["properties"]["action_kind"]["const"]
        for branch in schema["oneOf"]
    ]
    assert enum_kinds - set(branch_kinds) == set()
    assert set(branch_kinds) - enum_kinds == set()
    duplicates = {kind for kind in branch_kinds if branch_kinds.count(kind) > 1}
    # select_terrain_tool has distinct sculpt and material-elevation receipts.
    assert duplicates == {"select_terrain_tool"}


def test_every_covered_source_shape_validates_and_cardinality_fails_closed() -> None:
    schema = load_schema()
    validator = Draft202012Validator(schema)
    for branch in schema["oneOf"]:
        result = branch_result(branch)
        assert not list(validator.iter_errors(result)), result["action_kind"]

        wrong_cardinality = dict(result)
        wrong_cardinality["write_set"] = [*result["write_set"], "/unexpected/write"]
        assert list(validator.iter_errors(wrong_cardinality)), result["action_kind"]

        for required_field in branch.get("required", []):
            missing = dict(result)
            missing.pop(required_field)
            assert list(validator.iter_errors(missing)), (
                result["action_kind"],
                required_field,
            )


def test_union_rejects_extra_missing_wrong_kind_and_clear_glyph() -> None:
    schema = load_schema()
    validator = Draft202012Validator(schema)
    branches = {
        branch["properties"]["action_kind"]["const"]: branch
        for branch in schema["oneOf"]
        if branch["properties"]["action_kind"]["const"] != "select_terrain_tool"
    }

    extra = branch_result(branches["move_candidate_in_lane"])
    extra["undeclared_source_field"] = True
    assert list(validator.iter_errors(extra))

    missing = branch_result(branches["move_candidate_in_lane"])
    missing.pop("glyph_id")
    assert list(validator.iter_errors(missing))

    wrong_kind = branch_result(branches["move_candidate_in_lane"])
    wrong_kind["action_kind"] = "clear_candidate_lane"
    assert list(validator.iter_errors(wrong_kind))

    clear = branch_result(branches["clear_candidate_lane"])
    clear["glyph_id"] = 65
    assert list(validator.iter_errors(clear))

def test_source_anchors_preserve_mixed_owner_exclusion_and_fixed_counts() -> None:
    source = SOURCE_PATH.read_text()
    assert '"add_candidate_to_lane", "add", true' in source
    assert '"remove_candidate_from_lane", "remove", true' in source
    assert '"glyph.catalog.add_to_active_pool", "target_glyph_catalog_add_to_active_pool",\n\t\t\t\t"add_candidate_to_lane", "add", true' in source
    assert '"glyph.catalog.remove_from_active_pool", "target_glyph_catalog_remove_from_active_pool",\n\t\t\t\t"remove_candidate_from_lane", "remove", true' in source
    assert '"glyph_pool_add", "glyph"' not in source
    assert '"glyph_pool_remove", "glyph"' not in source
    assert '"move_candidate_in_lane", "move_left", true' in source
    assert '"clear_candidate_lane", "clear", false' in source
    assert 'if (op != "clear")' in source

    schema = load_schema()
    branches = {
        branch["properties"]["action_kind"]["const"]: branch
        for branch in schema["oneOf"]
    }
    assert branches["spread_palette_from_material"]["properties"]["write_set"] == {
        "minItems": 8,
        "maxItems": 8,
    }
    assert branches["reset_material_score"]["properties"]["write_set"] == {
        "minItems": 16,
        "maxItems": 16,
    }
