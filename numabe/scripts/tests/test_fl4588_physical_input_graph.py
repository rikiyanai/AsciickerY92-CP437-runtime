from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.fl4588.physical_input_recipe import (
    PhysicalInputContractError,
    compile_physical_input_recipe,
    registered_action_kind,
    registered_action_surface,
)
from scripts.fl4588_generate_html_architecture import (
    _validate_physical_input_graph,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
CONTROL_WIDGET = ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
GLYPH_PANEL = ROOT / "web/asciiid-authoring-shell/src/GlyphRepertoirePanel.tsx"
WORKSPACE_ACTIONS = ROOT / "web/asciiid-authoring-shell/src/WorkspaceActions.tsx"


def registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def sample_property(prop: dict) -> object:
    if "const" in prop:
        return prop["const"]
    if prop.get("enum"):
        return prop["enum"][0]
    if prop.get("type") == "boolean":
        return False
    if prop.get("type") in {"integer", "number"}:
        return prop.get("minimum", 0)
    if prop.get("type") == "string":
        return "fixture"
    if prop.get("type") == "array":
        count = int(prop.get("minItems", 1))
        return [sample_property(prop.get("items") or {"type": "integer"})] * count
    raise AssertionError(f"unsupported fixture property: {prop}")


def sample_payload(surface: dict) -> dict:
    properties = ((surface.get("payload_schema") or {}).get("properties") or {})
    return {key: sample_property(prop) for key, prop in properties.items()}


def test_every_mutation_action_has_exactly_one_registry_physical_contract() -> None:
    value = registry()
    _validate_physical_input_graph(value)
    action_ids = {
        row["leaf_id"]
        for row in value["leaves"]
        if isinstance(row.get("action_schema"), str)
    } | {row["leaf_id"] for row in value["interactions"]}
    claimed = [
        action_id
        for contract in value["physical_input_graph"]["contracts"]
        for action_id in (
            (contract.get("interaction_ids") or [])
            + (contract.get("leaf_ids") or [])
        )
    ]
    assert len(claimed) == len(set(claimed)) == 179
    assert set(claimed) == action_ids


def test_all_142_typed_leaf_contracts_compile_from_schema_valid_values() -> None:
    value = registry()
    leaf_ids = [
        leaf_id
        for contract in value["physical_input_graph"]["contracts"]
        for leaf_id in (contract.get("leaf_ids") or [])
    ]
    assert len(leaf_ids) == 142
    for leaf_id in leaf_ids:
        surface = registered_action_surface(value, leaf_id)
        recipe = compile_physical_input_recipe(
            value,
            leaf_id,
            typed_payload=sample_payload(surface),
        )
        assert recipe, leaf_id
        assert all(step["target_leaf_id"] == leaf_id for step in recipe), leaf_id


def test_every_typed_leaf_derives_action_kind_from_schema_identity() -> None:
    value = registry()
    typed_leaves = [
        row
        for row in value["leaves"]
        if isinstance(row.get("action_schema"), str)
    ]
    assert len(typed_leaves) == 143
    for leaf in typed_leaves:
        expected = leaf["action_schema"].removeprefix("asciiid.").rsplit(".v", 1)[0]
        surface = registered_action_surface(value, leaf["leaf_id"])
        assert surface["leaf_id"] == leaf["leaf_id"]
        assert registered_action_kind(surface) == expected


def test_interaction_action_kind_remains_explicit_registry_truth() -> None:
    value = registry()
    for interaction in value["interactions"]:
        surface = registered_action_surface(value, interaction["leaf_id"])
        assert surface is interaction
        assert registered_action_kind(surface) == interaction["action_kind"]


def test_invalid_leaf_schema_identity_fails_closed() -> None:
    with pytest.raises(
        PhysicalInputContractError,
        match="physical_action_schema_identity_invalid",
    ):
        registered_action_kind({"action_schema": "not-asciiid-action-v1"})


def test_duplicate_physical_contract_claim_fails_closed() -> None:
    value = registry()
    value["physical_input_graph"]["contracts"][1]["interaction_ids"].append(
        "workspace.document.open.commit"
    )
    with pytest.raises(SystemExit, match="coverage mismatch"):
        _validate_physical_input_graph(value)


@pytest.mark.parametrize("event", [
    {"kind": "pointer", "button": 2, "key": None},
    {"kind": "keyboard", "button": 0, "key": "Enter"},
    {"kind": "focus", "button": -1, "key": "Enter"},
    {"kind": "keyboard", "button": -1, "key": ""},
    {"kind": "widget", "button": -1, "key": None},
    {"kind": "keyboard", "button": -1, "key": "Enter"},
])
def test_additional_scalar_commit_events_fail_closed(event) -> None:
    value = registry()
    contract = next(row for row in value["physical_input_graph"]["contracts"]
                    if row["contract_id"] == "typed_leaf_exact_scalar_commit")
    contract["additional_commit_events"] = [event]
    with pytest.raises(SystemExit, match="invalid additional commit event"):
        _validate_physical_input_graph(value)


def test_typed_leaf_recipes_cover_exact_user_facing_shapes() -> None:
    value = registry()
    assert compile_physical_input_recipe(
        value,
        "settings.display.termpp_performance_hud",
        typed_payload={"visible": True},
    ) == [
        {
            "kind": "checkbox_set",
            "target_leaf_id": "settings.display.termpp_performance_hud",
            "value": True,
        }
    ]
    assert compile_physical_input_recipe(
        value,
        "material.score.density_bias",
        typed_payload={
            "profile_namespace": "terrain",
            "material_key": 4,
            "value": 12,
        },
    ) == [
        {
            "kind": "fill_number",
            "target_leaf_id": "material.score.density_bias",
            "target_role": "value",
            "value": 12,
        },
        {
            "kind": "keyboard_key",
            "target_leaf_id": "material.score.density_bias",
            "target_role": "value",
            "key": "Enter",
        },
    ]
    assert compile_physical_input_recipe(
        value,
        "material.colors.row0.fg",
        typed_payload={
            "profile_namespace": "terrain",
            "material_key": 4,
            "rgb": [18, 52, 86],
        },
    )[0] == {
        "kind": "keyboard_text",
        "target_leaf_id": "material.colors.row0.fg",
        "target_role": "rgb_hex",
        "value": "#123456",
    }


def test_grouped_leaf_recipes_commit_once_after_all_fields() -> None:
    value = registry()
    height = compile_physical_input_recipe(
        value,
        "scene.terrain.auto.tex.height.range",
        typed_payload={"minimum": 10, "maximum": 20},
    )
    assert [(step["kind"], step.get("target_role")) for step in height] == [
        ("fill_number", "minimum"),
        ("fill_number", "maximum"),
        ("pointer_click", "commit"),
    ]
    profile = compile_physical_input_recipe(
        value,
        "legacy.material.selectable.material.id",
        typed_payload={"profile_namespace": "mesh", "material_key": 7},
    )
    assert [(step["kind"], step.get("target_role")) for step in profile] == [
        ("select_option", "profile_namespace"),
        ("fill_number", "material_key"),
        ("pointer_click", "commit"),
    ]
    new_document = compile_physical_input_recipe(
        value,
        "workspace.document.new",
        typed_payload={"confirmation": "discard_current_document"},
    )
    assert [step["target_role"] for step in new_document] == ["request", "confirm"]


def test_scene_text_leaf_replaces_value_then_commits_with_enter() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "scene.objects.mesh.combo",
        typed_payload={"mesh_id": "arch"},
    )
    assert recipe == [
        {
            "kind": "keyboard_text",
            "target_leaf_id": "scene.objects.mesh.combo",
            "value": "arch",
        },
        {
            "kind": "keyboard_key",
            "target_leaf_id": "scene.objects.mesh.combo",
            "key": "Enter",
        },
    ]


def test_open_file_recipe_uses_visible_trigger_and_fixture_path() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "workspace.document.open.commit",
        typed_payload={"logical_name": "map.a3d"},
        fixture={"document_path": "/evidence/map.a3d"},
    )
    assert recipe == [
        {
            "kind": "file_select",
            "target_leaf_id": "workspace.document.open.open",
            "value": "/evidence/map.a3d",
        }
    ]


def test_material_stroke_compiles_to_real_down_up_sequence() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "scene.terrain.paint.material",
        typed_payload={},
        live_payload={"samples": [{"x": 10, "y": 20}, {"x": 30, "y": 40}]},
    )
    assert [step["kind"] for step in recipe] == ["pointer_down", "pointer_up"]
    assert recipe[0]["target_leaf_id"] == "scene.terrain.paint.material"
    assert (recipe[0]["local_x"], recipe[0]["local_y"]) == (10.0, 20.0)
    assert (recipe[1]["local_x"], recipe[1]["local_y"]) == (30.0, 40.0)


def test_modifier_contract_compiles_control_click() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "scene.terrain.eyedropper.material",
        typed_payload={},
        live_payload={"sample": {"x": 14, "y": 18}},
    )
    assert recipe == [
        {
            "kind": "pointer_click",
            "target_leaf_id": "scene.terrain.eyedropper.material",
            "button": "left",
            "modifiers": ["Control"],
            "local_x": 14.0,
            "local_y": 18.0,
        }
    ]


def test_palette_apply_compiles_payload_addressed_composite_controls() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "material.colors.palette.color.apply",
        typed_payload={"palette_id": "nord", "color_index": 5},
    )
    assert [(step["kind"], step["target_role"], step.get("value")) for step in recipe] == [
        ("select_option", "palette_id", "nord"),
        ("select_option", "color_index", 5),
        ("pointer_click", "commit", None),
    ]
    assert recipe[0]["target_leaf_id"] == "material.colors.palette.select"
    assert recipe[-1]["target_leaf_id"] == "material.colors.palette.color.apply"


def test_candidate_edit_compiles_lane_glyph_and_commit() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "material.pool.candidate.add",
        typed_payload={"lane_id": "ramp.2", "glyph_id": 1876},
    )
    assert [step["kind"] for step in recipe] == [
        "select_option",
        "fill_number",
        "pointer_click",
    ]
    assert [step.get("value") for step in recipe] == ["ramp.2", 1876, None]


@pytest.mark.parametrize("identity", [
    "scene.camera.pointer_rotate", "scene.camera.wheel_zoom", "scene.camera.keyboard_move",
])
def test_retired_camera_contracts_cannot_authorize_input(identity) -> None:
    # FL-4588: 8e7c9e6b4 deleted these failed authoring surfaces. Keeping a
    # success expectation resurrected their retired mutation authority in tests.
    with pytest.raises(PhysicalInputContractError, match="physical_action_surface_cardinality"):
        compile_physical_input_recipe(registry(), identity, typed_payload={})


def test_missing_live_coordinates_fail_before_input() -> None:
    with pytest.raises(PhysicalInputContractError, match="physical_input_point_missing"):
        compile_physical_input_recipe(
            registry(),
            "scene.objects.item.place",
            typed_payload={},
        )


def test_repertoire_contract_expands_to_one_exact_checkbox_per_member() -> None:
    recipe = compile_physical_input_recipe(
        registry(),
        "glyph.filter.repertoire",
        typed_payload={"selected": ["math", "box_drawing"]},
    )
    assert [step["target_value"] for step in recipe] == [
        "arabic", "math", "shapes", "box_drawing"
    ]
    assert [step["value"] for step in recipe] == [False, True, False, True]
    assert all(step["kind"] == "checkbox_set" for step in recipe)


def test_composite_and_repeated_dom_targets_expose_registry_roles() -> None:
    control = CONTROL_WIDGET.read_text(encoding="utf-8")
    glyph = GLYPH_PANEL.read_text(encoding="utf-8")
    workspace = WORKSPACE_ACTIONS.read_text(encoding="utf-8")
    for role in ("palette_id", "lane_id", "glyph_id", "color_index", "commit"):
        assert f'data-input-role="{role}"' in control
    assert 'data-input-role="glyph_id"' in glyph
    assert "data-input-value={entry.glyph_id}" in glyph
    assert 'data-input-role="repertoire_tag"' in glyph
    assert "data-input-value={tag}" in glyph
    assert 'data-input-role="commit"' in workspace
