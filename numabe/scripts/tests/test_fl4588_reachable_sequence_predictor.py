# =============================================================================
# test_fl4588_reachable_sequence_predictor.py
# =============================================================================
# Focused tests for scripts/fl4588/reachable_sequence_predictor.py.
#
# The predictor reads frozen backend edit_mode (no mutation), resolves the one
# reachable canvas gesture from registry active_tool_graph, and seals TERM++
# cells only after that sequence resolves. Flat derive_expected_cells is dead.
# =============================================================================

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
PREDICTOR_PATH = ROOT / "scripts/fl4588/reachable_sequence_predictor.py"
EXPECTED_CELLS_PATH = ROOT / "scripts/fl4548/fl4548_expected_cells.py"
GOLDEN_BRUSH = (
    ROOT / "assets/asciiid/counterfactual/terrain_material_brush_v1.golden.json"
)


def load_predictor():
    spec = importlib.util.spec_from_file_location(
        "reachable_sequence_predictor", PREDICTOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_expected_cells_module():
    spec = importlib.util.spec_from_file_location(
        "fl4548_expected_cells", EXPECTED_CELLS_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _frozen(edit_mode: int) -> dict:
    """Minimal authoritative backend envelope — read-only edit_mode carrier."""
    return {"editor_session": {"edit_mode": edit_mode}}


def _glyph_snapshot(registry: dict, *, profile_loaded: bool = True) -> dict:
    """Frozen backend facts consumed by a glyph candidate-row package."""
    return {
        "ui_registry_sha256": registry["registry_sha256"],
        "editor_session": {"edit_mode": 1},
        "material_runtime": {
            "active_profile_loaded": profile_loaded,
            "active_profile_namespace": "terrain",
            "active_profile_material_key": 4,
        },
    }


def _glyph_browser_state(
    registry: dict,
    *,
    selected_glyph_id: int | None = None,
    lane_id: str | None = "ramp.0",
    admitted_glyph_ids: tuple[int, ...] = (663,),
) -> dict:
    """Frozen HTML presentation facts plus the visible catalog identities."""
    glyphs: dict = {}
    if selected_glyph_id is not None:
        glyphs["selected_glyph_id"] = selected_glyph_id
    material_look: dict = {}
    if lane_id is not None:
        material_look["active_candidate_lane"] = lane_id
    return {
        "ui_registry_sha256": registry["registry_sha256"],
        "browser": {"glyphs": glyphs},
        "editor": {"material_look": material_look},
        "glyph_catalog": {
            "entries": [
                {
                    "glyph_id": glyph_id,
                    "unicode_scalar": 0x25CF + index,
                }
                for index, glyph_id in enumerate(admitted_glyph_ids)
            ]
        },
    }


def test_flat_derive_expected_cells_owner_is_deleted():
    mod = load_expected_cells_module()
    with pytest.raises(RuntimeError, match="flat_leaf_independent_prediction_owner_deleted"):
        mod.derive_expected_cells({"expected": {"affected_cells": {"kind": "none"}}})


def test_material_paint_edit_mode_resolves_wired_canvas_and_seals_golden_cells():
    # FL-4588 / RQ-201c / RQ-201f: with the whole canonical A3D document body in
    # the frozen snapshot, the Material Paint counterfactual arms and the reachable
    # sequence SEALS the exact TERM++ cells the stroke changes.
    predictor = load_predictor()
    registry = _registry()
    golden = json.loads(GOLDEN_BRUSH.read_text(encoding="utf-8"))
    # Pin the action's registry hash to the live registry so evaluate() accepts it.
    action = dict(golden["action"])
    action["ui_registry_sha256"] = registry["registry_sha256"]
    snapshot = dict(golden["snapshot"])
    snapshot["ui_registry_sha256"] = registry["registry_sha256"]

    result = predictor.predict_reachable_sequence(
        registry=registry,
        frozen_backend=_frozen(1),  # material_paint
        requested_leaf_id="scene.terrain.paint.material",
        counterfactual_snapshot=snapshot,
        action=action,
    )
    assert result["schema"] == "asciiid.reachable_sequence_prediction.v1"
    assert result["status"] == "SEALED"
    assert result["tool_id"] == "material_paint"
    assert result["canvas_gesture_leaf_id"] == "scene.terrain.paint.material"
    assert result["canvas_gesture_status"] == "WIRED"
    assert result["predicted_before_physical_input"] is True
    assert result["mutates_backend"] is False
    assert result["sealed_cells"] == [{"x": 0, "y": 0}]
    assert result["action_impact_sha256"]
    assert result["reasons"] == []


def test_sprite_edit_mode_canvas_wired_resolves_without_cell_seal():
    # FL-4588 SPRITE-CANVAS-CUTOVER: the Sprite tool now owns a WIRED canvas
    # gesture (scene.objects.sprite.place -> CommitSpritePlacement) after its
    # native ImGui edit_mode==4 place/delete gesture was deleted. The reachable
    # sequence is therefore user-reachable (status RESOLVED), but no TERM++ cells
    # are sealed: a sprite placement inserts a world instance, it is not a
    # counterfactual per-cell paint, so there is no exact-cell prediction. Headed
    # grouped-package receipt remains open (FL-4588).
    predictor = load_predictor()
    result = predictor.predict_reachable_sequence(
        registry=_registry(),
        frozen_backend=_frozen(4),  # sprite default
        requested_leaf_id=None,
    )
    assert result["status"] == "RESOLVED"
    assert result["tool_id"] == "sprite"
    assert result["canvas_gesture_status"] == "WIRED"
    assert result["canvas_gesture_leaf_id"] == "scene.objects.sprite.place"
    assert result["sealed_cells"] is None
    assert result["reasons"] == []


def test_material_elevation_canvas_wired_resolves_without_cell_seal():
    # FL-4588 MATERIAL-ELEVATION-CANVAS-CUTOVER: the Material Elevation tool now
    # owns a WIRED canvas gesture (scene.terrain.matelev.paint ->
    # CommitMaterialElevationPaint) after its native ImGui edit_mode==3 stroke +
    # Ctrl sample were deleted. The reachable sequence is user-reachable (status
    # RESOLVED). No TERM++ cells are sealed here because no counterfactual snapshot
    # is supplied; when one IS supplied the evaluator returns
    # theory_mechanism_unspecified (the paper does not define the elevation-bit ->
    # final-glyph mapping, so no predictor is invented). Headed grouped-package
    # receipt remains open (FL-4588).
    predictor = load_predictor()
    result = predictor.predict_reachable_sequence(
        registry=_registry(),
        frozen_backend=_frozen(3),  # material_elevation
        requested_leaf_id=None,
    )
    assert result["status"] == "RESOLVED"
    assert result["tool_id"] == "material_elevation"
    assert result["canvas_gesture_status"] == "WIRED"
    assert result["canvas_gesture_leaf_id"] == "scene.terrain.matelev.paint"
    assert result["sealed_cells"] is None
    assert result["reasons"] == []


def test_eyedropper_sequence_resolves_the_typed_transient_leaf():
    predictor = load_predictor()
    result = predictor.predict_reachable_sequence(
        registry=_registry(),
        frozen_backend=_frozen(1),
        require_eyedropper_sequence=True,
    )
    assert result["status"] == "RESOLVED"
    assert result["canvas_gesture_leaf_id"] == "scene.terrain.eyedropper.material"
    assert result["reasons"] == []
    assert result["sealed_cells"] is None


def test_ctrl_eyedropper_modifier_resolves_the_typed_transient_leaf():
    predictor = load_predictor()
    result = predictor.predict_reachable_sequence(
        registry=_registry(),
        frozen_backend=_frozen(1),
        modifier_chord="Ctrl",
    )
    assert result["status"] == "RESOLVED"
    assert result["canvas_gesture_leaf_id"] == "scene.terrain.eyedropper.material"
    assert result["modifier"]["role"] == "eyedropper"
    assert result["reasons"] == []
    assert result["sealed_cells"] is None
    assert "scene.terrain.eyedropper.material" in predictor.canvas_gesture_leaf_ids(
        _registry()
    )


def test_mesh_modifier_leaf_resolves_from_graph_without_private_action_map():
    predictor = load_predictor()
    registry = _registry()
    result = predictor.predict_reachable_sequence(
        registry=registry,
        frozen_backend=_frozen(2),
        requested_leaf_id="scene.objects.mesh.marquee_select",
    )
    assert result["status"] == "RESOLVED"
    assert result["tool_id"] == "mesh"
    assert result["canvas_gesture_leaf_id"] == "scene.objects.mesh.marquee_select"
    assert result["modifier"] == {
        "chord": "Shift",
        "role": "marquee_select",
        "status": "WIRED",
    }


def test_mesh_modifier_plan_selects_mesh_then_preserves_modifier_leaf():
    predictor = load_predictor()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=_registry(),
        state_snapshot=_frozen(0),
        action_kind="delete_mesh_instance",
        requested_leaf_id="scene.objects.mesh.delete",
    )
    assert plan["status"] == "RESOLVED"
    assert plan["selection_required"] is True
    assert plan["target_tool_id"] == "mesh"
    canvas = plan["packages"][-1]
    assert canvas["kind"] == "canvas_action"
    assert canvas["leaf_id"] == "scene.objects.mesh.delete"
    assert canvas["base_canvas_leaf_id"] == "scene.objects.mesh.place"
    assert canvas["modifier_chord"] == "Ctrl"


def test_mesh_delete_stops_at_named_paper_model_gate_before_input():
    predictor = load_predictor()
    registry = _registry()
    golden = json.loads(GOLDEN_BRUSH.read_text(encoding="utf-8"))
    snapshot = dict(golden["snapshot"])
    snapshot["ui_registry_sha256"] = registry["registry_sha256"]
    snapshot["editor_session"] = dict(snapshot["editor_session"])
    snapshot["editor_session"]["edit_mode"] = 2
    action = {
        "schema": "asciiid.action.v1",
        "leaf_id": "scene.objects.mesh.delete",
        "control_uid": "asciiid_canvas_mesh_deletion",
        "action_kind": "delete_mesh_instance",
        "action_version": 1,
        "base_state_revision": snapshot["state_revision"],
        "ui_registry_sha256": registry["registry_sha256"],
        "interaction_id": "prediction-only:mesh-delete",
        "payload": {
            "source_frame": {
                "frame_id": 917,
                "width": 640,
                "height": 360,
                "view_state_sha256": "a" * 64,
            },
            "pixel": {"x": 320, "y": 180},
        },
    }
    result = predictor.predict_reachable_sequence(
        registry=registry,
        frozen_backend=snapshot,
        requested_leaf_id="scene.objects.mesh.delete",
        counterfactual_snapshot=snapshot,
        action=action,
    )
    assert result["status"] == "PROOF_OPEN"
    assert result["modifier"] == {
        "chord": "Ctrl",
        "role": "delete_instance",
        "status": "WIRED",
    }
    assert result["sealed_cells"] is None
    assert any(
        "paper action model is not registered for this action_kind" in reason
        for reason in result["reasons"]
    )


def test_wrong_leaf_for_edit_mode_is_rejected():
    # FL-4588 / FL4588-ACTIVE-TOOL-REACHABLE-SEQUENCE-20260718-42
    predictor = load_predictor()
    result = predictor.predict_reachable_sequence(
        registry=_registry(),
        frozen_backend=_frozen(0),  # sculpt armed
        requested_leaf_id="scene.terrain.paint.material",  # material paint gesture
    )
    assert result["status"] == "REJECTED"
    assert any(
        "edit_mode_mismatch_requires_tool_selection_package" in r
        for r in result["reasons"]
    )


def test_canvas_paint_action_under_wrong_edit_mode_never_seals():
    # FL-4588: canvas seal must not proceed across an edit_mode mismatch.
    predictor = load_predictor()
    registry = _registry()
    action = {
        "schema": "asciiid.action.v1",
        "leaf_id": "scene.terrain.paint.material",
        "control_uid": "x",
        "action_kind": "paint_terrain_material",
        "action_version": 1,
        "base_state_revision": 0,
        "ui_registry_sha256": registry["registry_sha256"],
        "interaction_id": "prediction-only:pre-input-seal",
        "payload": {},
    }
    result = predictor.predict_reachable_sequence(
        registry=registry,
        frozen_backend=_frozen(0),
        requested_leaf_id="scene.terrain.paint.material",
        action=action,
    )
    assert result["status"] == "REJECTED"
    assert result["sealed_cells"] is None
    assert any(
        "edit_mode_mismatch_requires_tool_selection_package" in r
        for r in result["reasons"]
    )


def test_sculpt_to_material_emits_selection_then_canvas_packages():
    # FL-4588: startup sculpt + material paint => selection package then paint.
    predictor = load_predictor()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=_registry(),
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["schema"] == "asciiid.active_tool_reachable_sequence.v1"
    assert plan["status"] == "RESOLVED"  # FL-4588 package planner enum
    assert plan["selection_required"] is True
    assert plan["live_tool_id"] == "sculpt"
    assert plan["target_tool_id"] == "material_paint"
    kinds = [p["kind"] for p in plan["packages"]]
    assert kinds == ["tool_selection", "canvas_action"]
    sel = plan["packages"][0]
    assert sel["leaf_id"] == "scene.terrain.material.tool"
    assert sel["from_tool_id"] == "sculpt"
    assert sel["to_tool_id"] == "material_paint"
    assert sel["action_kind"] == "select_terrain_material_tool"
    assert sel["payload"] == {"tool": "terrain_material"}
    assert sel["transition_edge_id"] == "select__material_paint"
    assert plan["packages"][1]["requires_prior_selection_package"] is True


def test_already_active_material_emits_only_paint_package():
    # FL-4588: already-active material emits only the paint package.
    predictor = load_predictor()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=_registry(),
        state_snapshot=_frozen(1),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "RESOLVED"  # FL-4588 package planner enum
    assert plan["selection_required"] is False
    assert [p["kind"] for p in plan["packages"]] == ["canvas_action"]


def test_missing_selection_leaf_is_proof_open():
    # FL-4588: missing selection leaf => PROOF_OPEN (no hidden edit_mode setter).
    predictor = load_predictor()
    registry = _registry()
    graph = registry["active_tool_graph"]
    material = next(t for t in graph["tools"] if t["tool_id"] == "material_paint")
    material = dict(material)
    material["selection_leaf_id"] = None
    graph["tools"] = [
        material if t["tool_id"] == "material_paint" else t for t in graph["tools"]
    ]
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "PROOF_OPEN"
    assert any("missing_selection_leaf" in r for r in plan["reasons"])


def test_disabled_tool_selection_is_proof_open():
    # FL-4588: disabled tool selection => PROOF_OPEN.
    predictor = load_predictor()
    registry = _registry()
    graph = registry["active_tool_graph"]
    material = next(t for t in graph["tools"] if t["tool_id"] == "material_paint")
    material = dict(material)
    material["selection_handler"] = None
    graph["tools"] = [
        material if t["tool_id"] == "material_paint" else t for t in graph["tools"]
    ]
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "PROOF_OPEN"
    assert any("disabled_tool_selection" in r for r in plan["reasons"])


def test_deleted_reachability_owners_raise():
    # FL-4588 deleted owners for leaf-existence reachability and silent mismatch.
    predictor = load_predictor()
    for fn_name in (
        "assume_canvas_reachable_from_leaf_existence",
        "seal_canvas_across_edit_mode_mismatch",
    ):
        try:
            getattr(predictor, fn_name)()
            raise AssertionError(f"{fn_name} must raise")
        except RuntimeError as exc:
            assert "deleted_owner:" in str(exc)



def test_missing_tool_selection_transition_edge_is_proof_open_zero_packages():
    # FL4588-ACTIVE-TOOL-EDGE-REQUIRED-20260718-42A / FL-4588:
    # absent registered transition => PROOF_OPEN and zero packages.
    predictor = load_predictor()
    registry = _registry()
    graph = registry["active_tool_graph"]
    graph["transitions"] = [
        e
        for e in graph.get("transitions") or []
        if not (
            isinstance(e, dict)
            and e.get("kind") == "tool_selection"
            and e.get("to_tool_id") == "material_paint"
        )
    ]
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert any(
        "missing_tool_selection_transition_edge" in r for r in plan["reasons"]
    )


def test_mismatched_transition_leaf_fails_proof_open_zero_packages():
    # FL-4588: transition leaf_id must match the tool selection leaf.
    predictor = load_predictor()
    registry = _registry()
    graph = registry["active_tool_graph"]
    edges = []
    for e in graph.get("transitions") or []:
        if (
            isinstance(e, dict)
            and e.get("kind") == "tool_selection"
            and e.get("to_tool_id") == "material_paint"
        ):
            bad = dict(e)
            bad["leaf_id"] = "legacy.edit.sculpt.tab"  # wrong leaf for material
            edges.append(bad)
        else:
            edges.append(e)
    graph["transitions"] = edges
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert any(
        "missing_tool_selection_transition_edge" in r for r in plan["reasons"]
    )


def test_mismatched_from_tool_on_transition_fails_proof_open():
    # FL-4588: specific from_tool_id that is not live and not wildcard fails.
    predictor = load_predictor()
    registry = _registry()
    graph = registry["active_tool_graph"]
    edges = []
    for e in graph.get("transitions") or []:
        if (
            isinstance(e, dict)
            and e.get("kind") == "tool_selection"
            and e.get("to_tool_id") == "material_paint"
        ):
            bad = dict(e)
            bad["from_tool_id"] = "sprite"  # live is sculpt; not *
            edges.append(bad)
        else:
            edges.append(e)
    graph["transitions"] = edges
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert any(
        "missing_tool_selection_transition_edge" in r for r in plan["reasons"]
    )


def test_valid_wildcard_transition_emits_non_null_edge_id():
    # FL-4588: wildcard from_tool_id='*' emits the registered edge_id.
    predictor = load_predictor()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=_registry(),
        state_snapshot=_frozen(0),
        action_kind="paint_terrain_material",
        requested_leaf_id="scene.terrain.paint.material",
    )
    assert plan["status"] == "RESOLVED"  # FL-4588 package planner enum
    sel = plan["packages"][0]
    assert sel["transition_edge_id"] == "select__material_paint"
    assert sel["transition_edge_id"] is not None


@pytest.mark.parametrize(
    ("leaf_id", "action_kind"),
    (
        ("glyph.catalog.add_to_active_pool", "add_candidate_to_lane"),
        ("glyph.catalog.remove_from_active_pool", "remove_candidate_from_lane"),
    ),
)
def test_glyph_product_without_selection_emits_select_then_product(
    leaf_id: str, action_kind: str
):
    predictor = load_predictor()
    registry = _registry()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=_glyph_browser_state(registry),
        action_kind=action_kind,
        requested_leaf_id=leaf_id,
        requested_payload={"glyph_id": 663},
    )
    assert plan["status"] == "RESOLVED"
    assert plan["proof_status"] == "PROOF_OPEN"
    assert plan["selection_required"] is True
    assert [package["kind"] for package in plan["packages"]] == [
        "browser_selection",
        "product_action",
    ]
    selection, product = plan["packages"]
    assert selection["leaf_id"] == "glyph.catalog.select"
    assert selection["payload"] == {
        "glyph_id": 663,
        "unicode_scalar": 0x25CF,
    }
    assert selection["nonvisual_result"] == {
        "write_set": ["/browser/glyphs/selected_glyph_id"],
        "selected_glyph_id": 663,
        "exact_visible_delta": [],
        "termpp_delta": [],
        "mutates_backend": False,
    }
    assert product["leaf_id"] == leaf_id
    assert product["payload"] == {
        "profile_namespace": "terrain",
        "material_key": 4,
        "lane_id": "ramp.0",
        "glyph_id": 663,
    }
    assert product["prior_browser_package_id"] == selection["package_id"]
    assert product["browser_dependency_binding"]["selected_glyph_id"] == 663
    assert product["active_profile_binding"] == {
        "profile_namespace": "terrain",
        "material_key": 4,
    }
    assert product["active_candidate_lane_binding"] == "ramp.0"


def test_glyph_product_with_frozen_selection_is_directly_reachable_product():
    predictor = load_predictor()
    registry = _registry()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=_glyph_browser_state(
            registry, selected_glyph_id=663
        ),
        action_kind="add_candidate_to_lane",
        requested_leaf_id="glyph.catalog.add_to_active_pool",
        requested_payload={"glyph_id": 663},
    )
    assert plan["status"] == "RESOLVED"
    assert plan["selection_required"] is False
    assert [package["kind"] for package in plan["packages"]] == [
        "product_action"
    ]
    product = plan["packages"][0]
    assert product["requires_prior_browser_package"] is False
    assert product["prior_browser_package_id"] is None
    assert product["payload"]["glyph_id"] == 663


def test_glyph_product_fails_closed_when_selection_interaction_is_missing():
    predictor = load_predictor()
    registry = _registry()
    registry["interactions"] = [
        item
        for item in registry["interactions"]
        if item["leaf_id"] != "glyph.catalog.select"
    ]
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=_glyph_browser_state(registry),
        action_kind="add_candidate_to_lane",
        requested_leaf_id="glyph.catalog.add_to_active_pool",
        requested_payload={"glyph_id": 663},
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert any(
        "browser_dependency_interaction_unresolved" in reason
        for reason in plan["reasons"]
    )


def test_glyph_product_fails_closed_without_selected_glyph_target():
    predictor = load_predictor()
    registry = _registry()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=_glyph_browser_state(registry),
        action_kind="add_candidate_to_lane",
        requested_leaf_id="glyph.catalog.add_to_active_pool",
        requested_payload={},
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert plan["reasons"] == ["proof_open:selected_glyph_id_missing"]


def test_glyph_product_fails_closed_for_non_admitted_glyph():
    predictor = load_predictor()
    registry = _registry()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=_glyph_browser_state(
            registry, admitted_glyph_ids=(663,)
        ),
        action_kind="remove_candidate_from_lane",
        requested_leaf_id="glyph.catalog.remove_from_active_pool",
        requested_payload={"glyph_id": 777},
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert plan["reasons"] == ["proof_open:glyph_id_not_admitted:777"]


def test_glyph_product_fails_closed_without_active_profile():
    predictor = load_predictor()
    registry = _registry()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry, profile_loaded=False),
        frozen_browser_state=_glyph_browser_state(registry),
        action_kind="add_candidate_to_lane",
        requested_leaf_id="glyph.catalog.add_to_active_pool",
        requested_payload={"glyph_id": 663},
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert plan["reasons"] == ["proof_open:active_material_profile_missing"]


def test_glyph_product_fails_closed_without_active_lane():
    predictor = load_predictor()
    registry = _registry()
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=_glyph_browser_state(registry, lane_id=None),
        action_kind="add_candidate_to_lane",
        requested_leaf_id="glyph.catalog.add_to_active_pool",
        requested_payload={"glyph_id": 663},
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert plan["reasons"] == ["proof_open:active_candidate_lane_missing"]


def test_glyph_product_fails_closed_on_registry_identity_drift():
    predictor = load_predictor()
    registry = _registry()
    browser = _glyph_browser_state(registry)
    browser["ui_registry_sha256"] = "f" * 64
    plan = predictor.plan_active_tool_reachable_packages(
        registry=registry,
        state_snapshot=_glyph_snapshot(registry),
        frozen_browser_state=browser,
        action_kind="add_candidate_to_lane",
        requested_leaf_id="glyph.catalog.add_to_active_pool",
        requested_payload={"glyph_id": 663},
    )
    assert plan["status"] == "PROOF_OPEN"
    assert plan["packages"] == []
    assert any("registry_identity_drift" in reason for reason in plan["reasons"])


def test_predictor_source_has_no_http_cdp_mutation():
    text = PREDICTOR_PATH.read_text(encoding="utf-8")
    for banned in (
        "import requests",
        "from playwright",
        "import playwright",
        "import urllib.request",
        "http.client",
    ):
        assert banned not in text
    assert "mutates_backend" in text
    assert "plan_active_tool_reachable_packages" in text
    assert "deleted_owner:canvas_seal_across_edit_mode_mismatch" in text
    assert "deleted_owner:canvas_reachable_from_leaf_existence" in text
    assert "_resolve_tool_selection_transition" in text
    assert "missing_tool_selection_transition_edge" in text
    assert "browser_dependency_binding" in text
    assert "glyph.catalog.add_to_active_pool" not in text
    assert "glyph.catalog.remove_from_active_pool" not in text
