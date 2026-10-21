#!/usr/bin/env python3
# =============================================================
# fl4588_generate_html_architecture.py
# =============================================================
# Owns two things for the HTML ASCIIID editor UI registry:
#
# 1) Self-hash identity — the single `registry_sha256` stamp that must appear
#    on the registry document and on every leaf/interaction. `--stamp` is the
#    only allowed writer of that identity. Parallel ad-hoc restamp scripts were
#    hard-deleted (dumpstered) because they drifted the stamp away from the
#    generator's canonical hash (FL-4588 registry hash consistency).
#
# 2) Migration matrix projection — a derived CSV audit surface written from
#    the authoritative registry. The legacy CURRENT_UI_INVENTORY.csv is frozen
#    provenance only; this tool checks its coverage and must not invent leaves.
#
# Leaf and interaction *bodies* remain a direct registry contract (humans edit
# the JSON). This tool never authors a new leaf. It only restamps identity and
# regenerates the derived matrix.
# =============================================================
"""Validate the authoritative FL-4588 registry and project its audit matrix.

The JSON registry is the direct UI and action contract.  The legacy CSV is a
frozen provenance census: this tool checks its identity and control coverage,
but it cannot write a registry leaf body. Use --stamp to restamp registry_sha256
identity after a direct body edit; that is the sole hash owner.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from io import StringIO
from pathlib import Path

from jsonschema import Draft202012Validator

if __package__:
    from scripts.fl4588_ui_registry_identity import canonical_registry_sha256
else:
    from fl4588_ui_registry_identity import canonical_registry_sha256


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
SCHEMA_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.schema.json"
LEGACY_INVENTORY_PATH = (
    ROOT / "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv"
)
MATRIX_PATH = (
    ROOT / "docs/research/ascii/verification/fl4260/HTML_UI_MIGRATION_MATRIX.csv"
)


# FL4588-REGISTRY-NATIVE-OWNER-PROJECTION: commit 934a566a7 hard-deleted
# the seven remaining read-only top-level native navigation selectors and the
# selected-material native highlight owner.  These inventory rows must never
# regress to claiming a live native owner, and structural projection must not
# be mistaken for headed proof.
NATIVE_OWNER_PROJECTION_CONTROLS = (
    "fl4260_row_420__tabstrip_view",
    "fl4260_row_421__tabstrip_edit",
    "fl4260_row_422__tabstrip_sprite",
    "fl4260_row_424__tabstrip_inst",
    "fl4260_row_425__tabstrip_font",
    "fl4260_row_426__tabstrip_skin",
    "fl4260_row_427__tabstrip_info",
    "fl4260_row_430__starters_highlight_selected",
)


DIRECT_ACTION_SCHEMAS = frozenset(
    {
        "asciiid.add_candidate_to_lane.v1",
        "asciiid.adjust_terrain_material_height_threshold.v1",
        "asciiid.adjust_terrain_probe_height.v1",
        "asciiid.browser.filter_glyph_repertoire.v1",
        "asciiid.browser.select_glyph_catalog_entry.v1",
        "asciiid.browser.restore_candidate_lane_draft.v1",
        "asciiid.browser.select_inspector_cell.v1",
        "asciiid.browser.select_material_palette.v1",
        "asciiid.browser.set_selected_material_highlight.v1",
        "asciiid.history_redo.v1",
        "asciiid.history_undo.v1",
        "asciiid.move_candidate_in_lane.v1",
		"asciiid.paint_terrain_material.v1",
        "asciiid.paint_terrain_sculpt.v1",
        # Material sample effect. The retired HTML scene gesture is not an
        # authoring owner; RuntimeIntentOwner may expose a future physical edge.
        "asciiid.terrain_material_eyedropper.v1",
        "asciiid.remove_candidate_from_lane.v1",
        "asciiid.place_mesh_instance.v1",
        # FL4588-SPRITE-CANVAS-CUTOVER: the two typed Sprite transactions that
        # replace the deleted native edit_mode==4 gesture. Both are direct commits
        # (one URDO_Create / URDO_Delete each, no preview/stage step).
        "asciiid.place_sprite_instance.v1",
        "asciiid.delete_sprite_instance.v1",
        # FL4588-ITEM-CANVAS-CUTOVER: the two typed Item transactions that replace
        # the deleted native edit_mode==5 place/Ctrl-delete gesture. Both are direct
        # commits (one URDO_Create / URDO_Delete each, then ResetItemInsts rebuilds
        # the volatile Item::WORLD projection; no preview/stage step).
        "asciiid.place_item_instance.v1",
        "asciiid.delete_item_instance.v1",
        # FL4588-ENEMY-CANVAS-CUTOVER: the two typed Enemy Generator transactions that
        # replace the deleted native edit_mode==6 place/Ctrl-delete gesture. Both are
        # direct commits: placement creates one spawn point and deletion removes one,
        # each through the reversible URDO_CreateEnemyGen/URDO_DeleteEnemyGen attach
        # command (no preview/stage step).
        "asciiid.place_enemy_generator.v1",
        "asciiid.delete_enemy_generator.v1",
        # FL4588-STORY-CANVAS-CUTOVER: the one typed Story identity apply transaction
        # that replaces the deleted native edit_mode==7 direct SetInstStoryID write. A
        # direct commit through the reversible URDO_SetInstStoryID swap (no stage).
        "asciiid.apply_story_identity.v1",
        # FL4588-TERRAIN-MODIFIER-GRAPH-CUTOVER: the four typed Sculpt (edit_mode==0)
        # modifier transactions that replace the deleted native Shift/Ctrl/Ctrl+Shift/
        # Alt viewport modifier writers. All direct commits (Stamp mode-2 Gaussian,
        # URDO_Diag, probe_z selection, URDO_Create/URDO_Delete grouped topology).
        "asciiid.blur_sharpen_terrain.v1",
        "asciiid.flip_terrain_diagonal.v1",
        "asciiid.sample_terrain_probe_height.v1",
        "asciiid.edit_terrain_patch_topology.v1",
        # FL4588-MATERIAL-ELEVATION-CANVAS-CUTOVER: the two typed Material Elevation
        # transactions that replace the deleted native edit_mode==3 stroke + Ctrl
        # elevation-bit sample. Both are direct commits (one URDO group for the
        # paint stroke; the eyedropper is a selection change, no stage step).
        "asciiid.paint_material_elevation.v1",
        "asciiid.sample_material_elevation.v1",
        "asciiid.select_item_type.v1",
        "asciiid.select_material_profile.v1",
        "asciiid.select_mesh_asset.v1",
        "asciiid.select_object_tool.v1",
        "asciiid.select_sprite_asset.v1",
        "asciiid.select_terrain_material_tool.v1",
        "asciiid.select_terrain_tool.v1",
        "asciiid.select_world_instance.v1",
        "asciiid.set_ambient_light.v1",
        "asciiid.set_enemy_generator_load_policy.v1",
        "asciiid.set_enemy_generator_template_value.v1",
        "asciiid.set_enemy_generator_weapon_mix.v1",
        "asciiid.set_environment_animation_enabled.v1",
        "asciiid.set_material_feature_parameter.v1",
        "asciiid.set_material_elevation_brush_value.v1",
        "asciiid.set_material_score_parameter.v1",
        "asciiid.set_player_skin.v1",
        "asciiid.set_player_visible.v1",
        "asciiid.set_row_color.v1",
        "asciiid.set_row_color_strength.v1",
        "asciiid.set_row_shade_contrast.v1",
        "asciiid.set_shade_band_threshold.v1",
        "asciiid.set_shared_terrain_brush_shape.v1",
        "asciiid.set_sprite_placement_animation.v1",
        "asciiid.set_sprite_placement_frame.v1",
        "asciiid.set_sprite_placement_height.v1",
        "asciiid.set_sprite_placement_randomization.v1",
        "asciiid.set_sprite_placement_repetition.v1",
        "asciiid.set_sprite_placement_yaw.v1",
        "asciiid.set_story_identity_draft.v1",
        "asciiid.set_sun_direction_degrees.v1",
        "asciiid.set_sun_height_degrees.v1",
        "asciiid.set_sun_time_hours.v1",
        "asciiid.set_termpp_performance_hud_visible.v1",
        "asciiid.set_terrain_batch_parameter.v1",
        "asciiid.set_terrain_brush_height_limit.v1",
        "asciiid.set_terrain_brush_parameter.v1",
        "asciiid.set_terrain_material_brush_radius.v1",
        "asciiid.set_terrain_material_height_limit.v1",
        "asciiid.set_weather_state.v1",
        "asciiid.apply_palette_color_to_material.v1",
        "asciiid.spread_palette_from_material.v1",
        "asciiid.reset_material_score.v1",
    }
)

STAGED_ACTION_SCHEMAS = frozenset(
    {
        "asciiid.apply_automatic_material_elevation.v1",
        "asciiid.apply_automatic_terrain_material.v1",
        "asciiid.apply_routed_shape_preset.v1",
        "asciiid.apply_vegetation_color_preset.v1",
        "asciiid.assign_palette_next_unused.v1",
        "asciiid.autofill_material_role_rows.v1",
        "asciiid.clear_candidate_lane.v1",
        "asciiid.clear_material_elevation.v1",
        "asciiid.delete_all_enemy_generators.v1",
        "asciiid.merge_document.v1",
        "asciiid.new_document.v1",
        "asciiid.open_document.v1",
        "asciiid.rebuild_static_terrain_shadows.v1",
        "asciiid.reset_environment_phase.v1",
        "asciiid.reset_runtime_items_from_authored.v1",
        "asciiid.save_document.v1",
        "asciiid.set_material_anchor_color.v1",
    }
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_registry() -> dict:
    """Load and schema-validate the authoritative registry JSON.

    Schema validation only. Self-hash consistency is enforced separately by
    `_validate_authority` so `--stamp` can load a drifted file, restamp it,
    then validate.
    """
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(registry)
    return registry


def _stamp_registry_hashes(registry: dict) -> str:
    """Write one shared digest onto the document and every child row.

    Single ownership: every leaf and interaction must carry the same
    `registry_sha256` as the document. Callers must persist via
    `_write_registry_identity` after stamping.
    """
    # These counts describe the contract arrays, never hand-authored summary
    # guesses.  --stamp is their sole writer alongside registry_sha256.
    registry["leaf_count"] = len(registry["leaves"])
    registry["interaction_count"] = len(registry["interactions"])
    digest = canonical_registry_sha256(registry)
    registry["registry_sha256"] = digest
    for item in registry["leaves"] + registry["interactions"]:
        item["registry_sha256"] = digest
    return digest


def _write_registry_identity(registry: dict) -> None:
    """Persist identity-stamped registry bytes. Does not author leaf bodies.

    FL-4588: this is the only permitted write of `asciiid.ui_registry.v1.json`
    from tooling. Indent-2 JSON matches the tracked authority formatting so
    diffs stay reviewable. Packaged `dist/` copies are never authority.
    """
    REGISTRY_PATH.write_text(
        json.dumps(registry, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_authority(registry: dict) -> None:
    expected_authority = {
        "source_kind": "direct_registry_contract",
        "path": "assets/asciiid/ui/asciiid.ui_registry.v1.json",
        "legacy_inventory_role": "historical_accounting_only",
    }
    if registry["authority"] != expected_authority:
        raise SystemExit("registry authority contract is invalid")
    if registry["cutover_status"] != "authoritative":
        raise SystemExit("registry is not authoritative")
    if registry["leaf_count"] != len(registry["leaves"]):
        raise SystemExit("registry leaf_count does not match leaves")
    if registry["interaction_count"] != len(registry["interactions"]):
        raise SystemExit("registry interaction_count does not match interactions")

    expected_hash = canonical_registry_sha256(registry)
    if registry["registry_sha256"] != expected_hash:
        raise SystemExit("registry self-hash mismatch")
    if any(
        item["registry_sha256"] != expected_hash
        for item in registry["leaves"] + registry["interactions"]
    ):
        raise SystemExit("registry child hash mismatch")


def _validate_native_owner_projection(registry: dict) -> None:
    """Keep the source-deleted native owners honest and proof-open."""
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    for control_uid in NATIVE_OWNER_PROJECTION_CONTROLS:
        leaf = leaves.get(control_uid)
        if leaf is None:
            raise SystemExit(f"native-owner projection leaf missing: {control_uid}")
        if leaf["old_owner_deleted"] is not True:
            raise SystemExit(
                f"native-owner projection must record deletion: {control_uid}"
            )
        if leaf["proof_status"] != "PROOF_OPEN":
            raise SystemExit(
                f"native-owner projection must remain PROOF_OPEN: {control_uid}"
            )


def _validate_interaction_action_graph(registry: dict) -> None:
    """Bind every typed interaction to one explicit visible trigger identity."""
    leaves = {
        item["leaf_id"]: item
        for item in registry["leaves"]
        if isinstance(item, dict) and isinstance(item.get("leaf_id"), str)
    }
    interactions = {
        item["leaf_id"]: item
        for item in registry["interactions"]
        if isinstance(item, dict) and isinstance(item.get("leaf_id"), str)
    }
    if len(interactions) != len(registry["interactions"]):
        raise SystemExit("interaction leaf_id values must be unique")
    visible_ids = set(leaves) | set(interactions)
    for interaction in registry["interactions"]:
        action_kind = interaction.get("action_kind")
        trigger_leaf_id = interaction.get("trigger_leaf_id")
        if not isinstance(action_kind, str) or not action_kind:
            raise SystemExit(
                f"interaction {interaction.get('leaf_id')} lacks action_kind"
            )
        if trigger_leaf_id not in visible_ids:
            raise SystemExit(
                f"interaction {interaction.get('leaf_id')} trigger missing: "
                f"{trigger_leaf_id!r}"
            )


def _validate_interaction_source_anchors(registry: dict) -> None:
    """Reject copied interaction anchors that name a different transaction owner."""
    interactions = {
        interaction["leaf_id"]: interaction
        for interaction in registry["interactions"]
    }
    expected = {
        "scene.objects.item.place": (
            "editor/asciiid_application_service.cpp (CommitItemPlacement)",
            "editor/asciiid.cpp (CommitAsciiidItemPlacement)",
        ),
        "scene.objects.item.delete": (
            "editor/asciiid_application_service.cpp (CommitItemDeletion)",
            "editor/asciiid.cpp (CommitAsciiidItemDeletion)",
        ),
        "scene.objects.enemy.place": (
            "editor/asciiid_application_service.cpp (CommitEnemyGenPlacement)",
            "editor/asciiid.cpp (CommitAsciiidEnemyGenPlacement)",
        ),
        "scene.objects.enemy.delete": (
            "editor/asciiid_application_service.cpp (CommitEnemyGenDeletion)",
            "editor/asciiid.cpp (CommitAsciiidEnemyGenDeletion)",
        ),
        "scene.objects.story.apply": (
            "editor/asciiid_application_service.cpp (CommitStoryApply)",
            "editor/asciiid.cpp (CommitAsciiidStoryApply)",
        ),
    }
    for leaf_id, owner_anchors in expected.items():
        interaction = interactions.get(leaf_id)
        if interaction is None:
            raise SystemExit(f"typed interaction missing: {leaf_id}")
        anchors = interaction.get("source_anchors") or []
        if tuple(anchors[:2]) != owner_anchors:
            raise SystemExit(
                f"typed interaction source anchors mismatch: {leaf_id}"
            )


def _validate_physical_input_graph(registry: dict) -> None:
    """Require one registry-owned physical-input contract per mutation action.

    A proof plan may project these steps, but it cannot author a second input
    mapping. Coverage is exact and every explicit target must resolve to a
    registered leaf identity.
    """
    graph = registry.get("physical_input_graph")
    if not isinstance(graph, dict):
        raise SystemExit("physical_input_graph missing")
    if graph.get("schema") != "asciiid.physical_input_graph.v2":
        raise SystemExit("physical_input_graph schema mismatch")
    ownership = graph.get("ownership") or {}
    if ownership.get("contract_owner") != str(REGISTRY_PATH.relative_to(ROOT)):
        raise SystemExit("physical_input_graph contract_owner mismatch")
    if ownership.get("compiler_owner") != "scripts/fl4588/physical_input_recipe.py":
        raise SystemExit("physical_input_graph compiler_owner mismatch")
    if ownership.get("dispatcher_owner") != (
        "scripts/fl4588/chrome_app_shell_playwright.py"
    ):
        raise SystemExit("physical_input_graph dispatcher_owner mismatch")
    note = ownership.get("note") or ""
    if "hand-authored recipe is never authoritative" not in note:
        raise SystemExit("physical_input_graph ownership note is incomplete")

    interactions = {
        item["leaf_id"]: item for item in registry.get("interactions", [])
    }
    typed_leaves = {
        item["leaf_id"]: item
        for item in registry.get("leaves", [])
        if isinstance(item.get("action_schema"), str)
    }
    action_rows = dict(typed_leaves)
    action_rows.update(interactions)
    all_leaf_ids = {
        item["leaf_id"]
        for item in registry.get("leaves", []) + registry.get("interactions", [])
    }
    seen_contract_ids: set[str] = set()
    claimed_action_ids: list[str] = []
    for contract in graph.get("contracts") or []:
        contract_id = contract.get("contract_id")
        if contract_id in seen_contract_ids:
            raise SystemExit(f"duplicate physical contract id: {contract_id}")
        seen_contract_ids.add(contract_id)
        # FL-4512: the proof recipe is one canonical gesture, not an implicit
        # ban on the other physical gestures offered by the same scalar UI.
        additional = contract.get("additional_commit_events", [])
        if not isinstance(additional, list) or (
            "additional_commit_events" in contract and not additional
        ):
            raise SystemExit(f"invalid additional commit events: {contract_id}")
        steps = contract.get("steps") or []
        final_step = steps[-1] if steps else {}
        step_kind = final_step.get("kind", "")
        canonical_event = ("widget", -1, None)
        if step_kind.startswith("pointer_"):
            canonical_event = ("pointer", 0, None)
        elif step_kind == "file_select":
            canonical_event = ("file", -1, None)
        elif step_kind.startswith("keyboard_"):
            canonical_event = ("keyboard", -1, final_step.get("key"))
        seen_events: set[tuple] = {canonical_event}
        for event in additional:
            if not isinstance(event, dict) or set(event) != {"kind", "button", "key"}:
                raise SystemExit(f"invalid additional commit event: {contract_id}")
            kind, button, key = event["kind"], event["button"], event["key"]
            valid = type(button) is int and (
                (kind == "pointer" and button == 0 and key is None)
                or (kind == "focus" and button == -1 and key is None)
                or (kind == "keyboard" and button == -1 and isinstance(key, str) and bool(key))
            )
            if not valid or (kind, button, key) in seen_events:
                raise SystemExit(f"invalid additional commit event: {contract_id}")
            seen_events.add((kind, button, key))
        interaction_ids = contract.get("interaction_ids") or []
        leaf_ids = contract.get("leaf_ids") or []
        claimed_action_ids.extend(interaction_ids)
        claimed_action_ids.extend(leaf_ids)
        for interaction_id in interaction_ids:
            if interaction_id not in interactions:
                raise SystemExit(
                    f"physical contract {contract_id} claims unknown interaction: "
                    f"{interaction_id}"
                )
        for leaf_id in leaf_ids:
            if leaf_id not in typed_leaves or leaf_id in interactions:
                raise SystemExit(
                    f"physical contract {contract_id} claims invalid typed leaf: "
                    f"{leaf_id}"
                )
        action_ids = interaction_ids + leaf_ids
        for step in contract.get("steps") or []:
            target_leaf_id = step.get("target_leaf_id")
            if target_leaf_id is not None and target_leaf_id not in all_leaf_ids:
                raise SystemExit(
                    f"physical contract {contract_id} targets unknown leaf: "
                    f"{target_leaf_id}"
                )
            value_from = step.get("value_from")
            if isinstance(value_from, str) and value_from.startswith("typed_payload."):
                field = value_from.split(".", 1)[1].split(".", 1)[0]
                for action_id in action_ids:
                    properties = (
                        (action_rows[action_id].get("payload_schema") or {})
                        .get("properties", {})
                    )
                    if field == "$primary":
                        editable = [
                            key
                            for key, value in properties.items()
                            if isinstance(value, dict) and "const" not in value
                        ]
                        if editable:
                            continue
                    if field not in properties:
                        raise SystemExit(
                            f"physical contract {contract_id} reads missing payload "
                            f"field {field}: {action_id}"
                        )

    expected = sorted(action_rows)
    claimed = sorted(claimed_action_ids)
    if claimed != expected:
        missing = sorted(set(expected) - set(claimed))
        duplicate = sorted(
            leaf_id
            for leaf_id in set(claimed)
            if claimed_action_ids.count(leaf_id) > 1
        )
        extra = sorted(set(claimed) - set(expected))
        raise SystemExit(
            "physical input action coverage mismatch: "
            f"missing={missing} duplicate={duplicate} extra={extra}"
        )


def _validate_legacy_census(registry: dict) -> None:
    inventory_bytes = LEGACY_INVENTORY_PATH.read_bytes()
    inventory = _read_csv(LEGACY_INVENTORY_PATH)
    provenance = registry["source_inventory"]
    expected_path = str(LEGACY_INVENTORY_PATH.relative_to(ROOT))
    if provenance != {
        "path": expected_path,
        "sha256": _sha256(inventory_bytes),
        "row_count": len(inventory),
    }:
        raise SystemExit("legacy inventory provenance drift")

    registry_uids = [leaf["control_uid"] for leaf in registry["leaves"]]
    inventory_uids = [row["control_uid"] for row in inventory]
    if len(registry_uids) != len(set(registry_uids)):
        raise SystemExit("duplicate registry control_uid")
    if len(inventory_uids) != len(set(inventory_uids)):
        raise SystemExit("duplicate legacy control_uid")
    if set(registry_uids) != set(inventory_uids):
        raise SystemExit("registry and legacy census control coverage differ")


def _validate_active_tool_graph(registry: dict) -> None:
    """FL-4588: single closed active-tool graph; reject retired tool_graph.

    Delete-first ownership: asciiid.ui_tool_graph.v1 (top-level tool_graph)
    was hard-deleted. Only active_tool_graph may own tool contracts. One mouse
    has one active semantic tool. Every Mesh modifier edge must be WIRED.
    Transient material eyedropper is WIRED to CommitTerrainMaterialEyedropper
    while remaining non-persistent (null edit_mode).
    """
    if "tool_graph" in registry:
        raise SystemExit(
            "retired tool_graph must not coexist with active_tool_graph "
            "(asciiid.ui_tool_graph.v1 was hard-deleted)"
        )

    graph = registry.get("active_tool_graph")
    if not isinstance(graph, dict):
        raise SystemExit("active_tool_graph missing")
    if graph.get("schema") != "asciiid.active_tool_graph.v1":
        raise SystemExit("active_tool_graph schema mismatch")

    ownership = graph.get("ownership") or {}
    if ownership.get("contract_owner") != (
        "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    ):
        raise SystemExit("active_tool_graph contract_owner mismatch")
    expected_layout = [
        "web/asciiid-authoring-shell/src/App.tsx",
        "web/asciiid-authoring-shell/src/pageLayout.ts",
    ]
    if ownership.get("layout_owners") != expected_layout:
        raise SystemExit("active_tool_graph layout_owners mismatch")
    note = ownership.get("note") or ""
    if "tool_graph" not in note or "hard-delet" not in note:
        raise SystemExit(
            "active_tool_graph ownership note must record tool_graph hard-delete"
        )
    # FL-4588 ActiveToolOptions delete-first: the note must also record that the
    # per-tool option_leaf_ids route rows lost their generic route-row DOM owner
    # and moved to the single persistent ActiveToolOptions panel. Without this
    # marker a later edit could quietly re-home the option leaves.
    if "option_leaf_ids" not in note or "ActiveToolOptions" not in note:
        raise SystemExit(
            "active_tool_graph ownership note must record the option_leaf_ids "
            "route-row hard-delete and the ActiveToolOptions panel owner"
        )

    state = graph.get("active_tool_state") or {}
    if state.get("snapshot_field") != "editor_session.edit_mode":
        raise SystemExit("active_tool_state must source editor_session.edit_mode")
    if state.get("cardinality") != "exactly_one":
        raise SystemExit("active_tool_state cardinality must be exactly_one")
    if state.get("default_edit_mode") != 0 or state.get("default_tool_id") != "sculpt":
        raise SystemExit("active_tool_state default must be sprite edit_mode 4")

    leaves_by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    interactions_by_id = {
        item["leaf_id"]: item for item in registry["interactions"]
    }
    all_ids = set(leaves_by_id) | set(interactions_by_id)

    canvas = graph.get("canvas_surface") or {}
    if canvas.get("surface_id") != "termpp_final_canvas":
        raise SystemExit("canvas_surface.surface_id must be termpp_final_canvas")
    if canvas.get("control_uid") != "asciiid_termpp_final_canvas":
        raise SystemExit("canvas_surface.control_uid mismatch")
    if canvas.get("gesture") != "inspect_only":
        raise SystemExit("canvas_surface.gesture must be inspect_only")
    inspect_id = canvas.get("inspect_leaf_id")
    if inspect_id not in interactions_by_id:
        raise SystemExit(f"canvas inspect leaf missing: {inspect_id}")
    always = canvas.get("always_available_gesture_leaf_ids") or []
    for leaf_id in always:
        if leaf_id not in all_ids:
            raise SystemExit(f"always-available gesture leaf missing: {leaf_id}")

    if graph.get("modifier_keys") != ["Shift", "Ctrl", "Alt"]:
        raise SystemExit("modifier_keys must be Shift/Ctrl/Alt")

    tools = graph.get("tools") or []
    expected_tool_ids = {
        "sculpt",
        "material_paint",
        "mesh",
        "material_elevation",
        "sprite",
        "item",
        "enemy_generator",
        "story",
    }
    tool_ids = {tool.get("tool_id") for tool in tools}
    if tool_ids != expected_tool_ids:
        raise SystemExit(f"active_tool_graph tools mismatch: {sorted(tool_ids)}")
    edit_modes = [tool.get("edit_mode") for tool in tools]
    if sorted(edit_modes) != list(range(8)):
        raise SystemExit("active_tool_graph edit_mode values must be 0..7 unique")

    for tool in tools:
        if tool.get("transient") is not False:
            raise SystemExit(f"persistent tool {tool.get('tool_id')} must not be transient")
        selection_id = tool.get("selection_leaf_id")
        gesture_status = tool.get("canvas_gesture_status")
        gesture_id = tool.get("canvas_gesture_leaf_id")
        if gesture_status == "WIRED":
            if gesture_id not in interactions_by_id:
                raise SystemExit(
                    f"{tool.get('tool_id')} claims WIRED but gesture missing: {gesture_id}"
                )
        if selection_id is None:
            if tool.get("proof_status") != "PROOF_OPEN":
                raise SystemExit(
                    f"tool {tool.get('tool_id')} missing selection must be PROOF_OPEN"
                )
            continue
        if selection_id not in leaves_by_id:
            raise SystemExit(f"tool selection leaf missing: {selection_id}")
        leaf = leaves_by_id[selection_id]
        if leaf.get("status") != "live":
            raise SystemExit(f"tool selection leaf not live: {selection_id}")
        if leaf.get("action_schema") != tool.get("selection_action_schema"):
            raise SystemExit(
                f"tool selection action_schema mismatch for {selection_id}"
            )
        payload_tool = (
            ((leaf.get("payload_schema") or {}).get("properties") or {})
            .get("tool", {})
            .get("const")
        )
        if payload_tool != tool.get("selection_payload_tool"):
            raise SystemExit(
                f"tool payload const mismatch for {selection_id}: "
                f"{payload_tool} != {tool.get('selection_payload_tool')}"
            )
        if gesture_id is not None and gesture_id not in interactions_by_id:
            raise SystemExit(f"canvas gesture leaf missing: {gesture_id}")

    # FL-4588 ActiveToolOptions contract: each relevant tool declares an ORDERED
    # list of option_leaf_ids. These are the live brush/probe controls that the
    # persistent ActiveToolOptions panel renders for the currently active tool
    # (resolved from editor_session.edit_mode). The registry — not any TypeScript
    # array — is the single owner of which leaves belong to which tool and in
    # what order, so the moved route rows have exactly one actionable DOM owner.
    #
    # Only the two WIRED terrain tools own live options today. The lists are
    # pinned exactly (ordered) so a drift is a loud generator failure, matching
    # how the eyedropper/transition contracts are pinned above.
    expected_option_leaves = {
        "sculpt": [
            "legacy.edit.sculpt.brush.radius",
            "legacy.edit.sculpt.brush.alpha",
            "legacy.edit.sculpt.probe.left",
            "legacy.edit.sculpt.probe.right",
        ],
        "material_paint": [
            "scene.terrain.material.brush.radius",
            "scene.terrain.material.brush.height_limit",
            "scene.terrain.material.brush.height_threshold.decrement",
            "scene.terrain.material.brush.height_threshold.increment",
        ],
        # FL4588-MESH-CANVAS-CUTOVER: the Mesh tool owns exactly one option leaf,
        # the asset selector. Selecting a mesh (typed select_mesh_asset) sets
        # editor_session.active_mesh so a future runtime-owned placement control has a genuine,
        # visible, backend-owned asset to place. Pinned so a drift is a loud fail.
        "mesh": [
            "scene.objects.mesh.combo",
        ],
        # FL4588-SPRITE-CANVAS-CUTOVER: the Sprite tool owns exactly one option
        # leaf, the asset selector. Selecting a sprite (typed select_sprite_asset)
        # sets editor_session.active_sprite so a future runtime-owned placement control has a
        # genuine, visible, backend-owned asset to place. Pinned so a drift is a
        # loud fail.
        "sprite": [
            "scene.objects.sprite.combo",
        ],
        # FL4588-ITEM-CANVAS-CUTOVER: the Item tool owns exactly one option leaf, the
        # item type selector. Selecting an item type (typed select_item_type by the
        # STABLE compiled-tuple identity from the live item_library) sets
        # editor_session.active_item_type so a future runtime-owned placement control has a
        # genuine, visible, backend-owned item to place. Pinned so a drift is a loud
        # fail.
        "item": [
            "scene.objects.item.item.list",
        ],
        # FL4588-ENEMY-CANVAS-CUTOVER: the Enemy Generator tool owns ten option leaves
        # — the load-policy toggle, the delete-all button, and the eight template
        # sliders (max alive, revive min/max, armor/helmet/shield, sword/crossbow).
        # Every value control is snapshot-backed (snapshotValueForLeaf -> the eg_*
        # session fields). Pinned in order so a drift is a loud fail.
        "enemy_generator": [
            "scene.objects.enemy.enable.enemy.gen",
            "scene.objects.enemy.delete.all.generators",
            "scene.objects.enemy.max.alive",
            "scene.objects.enemy.revive.min",
            "scene.objects.enemy.revive.max",
            "scene.objects.enemy.armor",
            "scene.objects.enemy.helmet",
            "scene.objects.enemy.shield",
            "scene.objects.enemy.sword",
            "scene.objects.enemy.crossbow",
        ],
        # FL4588-STORY-CANVAS-CUTOVER: the Story tool owns one option leaf, the
        # snapshot-backed story-id draft field (set_story_identity_draft -> the
        # story_id session field). Pinned.
        "story": [
            "scene.objects.story.story.id",
        ],
        # FL4588-MATERIAL-ELEVATION-CANVAS-CUTOVER: the Material Elevation tool owns
        # two option leaves — the brush diameter and the one-bit elevated toggle
        # that mirrors editor_session.brush_material_elevation_bit (active_elev),
        # the value the canvas stroke paints. Pinned (ordered) so a drift is a loud
        # fail, matching how the other tools' option contracts are pinned.
        "material_elevation": [
            "legacy.edit.matelev.brush.diameter",
            "legacy.edit.matelev.elevated",
        ],
    }
    seen_option_owner: dict[str, str] = {}
    for tool in tools:
        tool_id = tool.get("tool_id")
        option_ids = tool.get("option_leaf_ids")
        if tool_id in expected_option_leaves:
            if option_ids != expected_option_leaves[tool_id]:
                raise SystemExit(
                    f"{tool_id} option_leaf_ids must be the ordered contract "
                    f"{expected_option_leaves[tool_id]}"
                )
        elif option_ids:
            # No other tool owns live options yet; declaring some would create a
            # second, unvalidated presentation surface.
            raise SystemExit(
                f"{tool_id} must not declare option_leaf_ids until its options "
                "are contract-pinned"
            )
        for option_id in option_ids or []:
            if option_id not in leaves_by_id:
                raise SystemExit(f"option leaf missing: {option_id}")
            if leaves_by_id[option_id].get("status") != "live":
                raise SystemExit(f"option leaf not live: {option_id}")
            # Single owner: one option leaf may belong to exactly one tool, so
            # each moved route row keeps a single actionable DOM owner.
            if option_id in seen_option_owner:
                raise SystemExit(
                    f"option leaf {option_id} claimed by both "
                    f"{seen_option_owner[option_id]} and {tool_id}"
                )
            seen_option_owner[option_id] = tool_id

    # Transient eyedropper: not a persistent edit_mode. Its former HTML scene
    # gesture is retired and remains PROOF_OPEN until a runtime-owned physical
    # authoring surface is registered.
    transient_tools = graph.get("transient_tools") or []
    if len(transient_tools) != 1:
        raise SystemExit("active_tool_graph must declare exactly one transient tool")
    eyedropper = transient_tools[0]
    if eyedropper.get("tool_id") != "eyedropper":
        raise SystemExit("transient eyedropper tool_id mismatch")
    if eyedropper.get("transient") is not True or eyedropper.get("edit_mode") is not None:
        raise SystemExit("eyedropper must be transient with null edit_mode")
    if eyedropper.get("canvas_gesture_status") != "PROOF_OPEN":
        raise SystemExit("eyedropper canvas gesture must be PROOF_OPEN")
    if eyedropper.get("canvas_gesture_leaf_id") != "scene.terrain.eyedropper.material":
        raise SystemExit("eyedropper canvas_gesture_leaf_id mismatch")
    if eyedropper.get("application_service_handler") != (
        "CommitTerrainMaterialEyedropper"
    ):
        raise SystemExit("eyedropper must bind CommitTerrainMaterialEyedropper")
    if eyedropper.get("claimed_interaction_id") != "scene.terrain.eyedropper.material":
        raise SystemExit("eyedropper claimed_interaction_id mismatch")
    if eyedropper.get("proof_status") != "PROOF_OPEN":
        raise SystemExit("eyedropper debt must remain PROOF_OPEN")
    if "scene.terrain.eyedropper.material" not in interactions_by_id:
        raise SystemExit("eyedropper interaction leaf missing from registry")

    # Mutually exclusive transitions: one select edge per live selection tool.
    transitions = graph.get("transitions") or []
    select_targets = set()
    for edge in transitions:
        if edge.get("kind") != "tool_selection":
            raise SystemExit("unexpected non-selection transition kind")
        if edge.get("mutually_exclusive") is not True:
            raise SystemExit("tool_selection edges must be mutually_exclusive")
        if edge.get("replaces_active_tool") is not True:
            raise SystemExit("tool_selection edges must replace active tool")
        leaf_id = edge.get("leaf_id")
        if leaf_id not in leaves_by_id:
            raise SystemExit(f"transition leaf missing: {leaf_id}")
        select_targets.add(edge.get("to_tool_id"))
    live_select_tools = {
        tool["tool_id"] for tool in tools if tool.get("selection_leaf_id")
    }
    if select_targets != live_select_tools:
        raise SystemExit(
            "tool_selection transitions must cover every live selection tool"
        )

    for edge in graph.get("modifier_edges") or []:
        if edge.get("kind") != "modifier":
            raise SystemExit("modifier_edges kind must be modifier")
        if edge.get("changes_active_tool") is not False:
            raise SystemExit("modifier edges must not change active tool")
        leaf_id = edge.get("leaf_id")
        if leaf_id not in all_ids:
            raise SystemExit(f"modifier leaf missing: {leaf_id}")

    sequences = graph.get("sequences") or []
    eyedropper_seq = next(
        (seq for seq in sequences if seq.get("kind") == "material_eyedropper"),
        None,
    )
    if eyedropper_seq is None:
        raise SystemExit("material_eyedropper sequence missing")
    if eyedropper_seq.get("required_active_tool_id") != "material_paint":
        raise SystemExit("eyedropper sequence must require material_paint")
    if eyedropper_seq.get("transient_tool_id") != "eyedropper":
        raise SystemExit("eyedropper sequence must bind transient eyedropper")
    if eyedropper_seq.get("implementation_leaf_id") != (
        "scene.terrain.eyedropper.material"
    ):
        raise SystemExit("eyedropper implementation_leaf_id mismatch")
    if eyedropper_seq.get("application_service_handler") != (
        "CommitTerrainMaterialEyedropper"
    ):
        raise SystemExit(
            "eyedropper sequence must bind CommitTerrainMaterialEyedropper"
        )
    if eyedropper_seq.get("proof_status") != "PROOF_OPEN":
        raise SystemExit("eyedropper debt must remain PROOF_OPEN")
    # Wired sequence: no step may still claim missing_implementation.
    if any(
        step.get("missing_implementation") is True
        for step in eyedropper_seq.get("steps") or []
    ):
       raise SystemExit(
           "eyedropper sequence must not keep missing_implementation after wire"
       )


def _validate_workspace_action_graph(registry: dict) -> None:
    """FL-4588: single closed workspace-action graph owns the persistent
    Open/Save/Undo/Redo toolbar contract.

    Delete-first ownership: the four route-row DOM presentation owners in
    ControlWidget.tsx (workspace.document.open.open, save_as.open,
    history.undo, history.redo) were hard-deleted. Only
    workspace_action_graph may own the persistent workspace-action contract.
    App.tsx and WorkspaceActions.tsx are layout owners only and must not
    become a second workspace-action owner.
    """
    graph = registry.get("workspace_action_graph")
    if not isinstance(graph, dict):
        raise SystemExit("workspace_action_graph missing")
    if graph.get("schema") != "asciiid.workspace_action_graph.v1":
        raise SystemExit("workspace_action_graph schema mismatch")

    ownership = graph.get("ownership") or {}
    if ownership.get("contract_owner") != (
        "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    ):
        raise SystemExit("workspace_action_graph contract_owner mismatch")
    expected_layout = [
        "web/asciiid-authoring-shell/src/App.tsx",
        "web/asciiid-authoring-shell/src/WorkspaceActions.tsx",
    ]
    if ownership.get("layout_owners") != expected_layout:
        raise SystemExit("workspace_action_graph layout_owners mismatch")
    note = ownership.get("note") or ""
    if "hard-delet" not in note or "route-row" not in note:
        raise SystemExit(
            "workspace_action_graph ownership note must record the "
            "hard-deleted route-row presentation owners"
        )

    leaves_by_id = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}

    actions = graph.get("actions") or []
    expected_actions = [
        ("open_document", "workspace.document.open.open"),
        ("save_document", "workspace.document.save_as.open"),
        ("history_undo", "workspace.history.undo"),
        ("history_redo", "workspace.history.redo"),
    ]
    if len(actions) != len(expected_actions):
        raise SystemExit(
            "workspace_action_graph must declare exactly four actions"
        )
    seen = [
        (action.get("kind"), action.get("leaf_id")) for action in actions
    ]
    if seen != expected_actions:
        raise SystemExit(f"workspace_action_graph actions mismatch: {seen}")

    for kind, leaf_id in expected_actions:
        leaf = leaves_by_id.get(leaf_id)
        if leaf is None:
            raise SystemExit(
                f"workspace_action_graph references missing leaf: {leaf_id}"
            )
        if leaf.get("status") != "live":
            raise SystemExit(
                f"workspace_action_graph references non-live leaf: {leaf_id}"
            )
        if leaf.get("proof_status") != "PROOF_OPEN":
            raise SystemExit(
               f"workspace_action_graph action {leaf_id} must stay PROOF_OPEN"
           )


def _validate_commit_classes(registry: dict) -> None:
    if DIRECT_ACTION_SCHEMAS & STAGED_ACTION_SCHEMAS:
        raise SystemExit("direct and staged action-schema sets overlap")

    items = registry["leaves"] + registry["interactions"]
    action_schemas = {
        item["action_schema"] for item in items if item["action_schema"] is not None
    }
    classified_schemas = DIRECT_ACTION_SCHEMAS | STAGED_ACTION_SCHEMAS
    if action_schemas != classified_schemas:
        unclassified = sorted(action_schemas - classified_schemas)
        stale = sorted(classified_schemas - action_schemas)
        raise SystemExit(
            "action-schema commit-class census drift: "
            f"unclassified={unclassified} stale={stale}"
        )

    for item in items:
        action_schema = item["action_schema"]
        if action_schema is None:
            if item["commit_class"] is not None:
                raise SystemExit(
                    f"non-action registry item {item['leaf_id']} has commit_class"
                )
            continue
        expected = (
            "direct" if action_schema in DIRECT_ACTION_SCHEMAS else "staged"
        )
        if item["commit_class"] != expected:
            raise SystemExit(
                f"commit_class mismatch for {item['leaf_id']}: "
                f"expected {expected}, found {item['commit_class']}"
            )


def _matrix_rows(registry: dict) -> list[dict[str, str]]:
    return [
        {
            "control_uid": leaf["control_uid"],
            "old_leaf_path": leaf["legacy_source_path"],
            "old_label": leaf["legacy_label"],
            "old_widget_kind": leaf["legacy_widget_kind"],
            "old_source_handler": leaf["legacy_source_handler"],
            "target_route": leaf["route"],
            "target_leaf_id": leaf["leaf_id"],
            "target_label": leaf["label"],
            "migration_status": leaf["migration_status"],
            "old_owner_deleted": str(leaf["old_owner_deleted"]).lower(),
            "new_action_schema": leaf["action_schema"] or "",
            "commit_class": leaf["commit_class"] or "",
            "predictor_model": leaf["predictor_model_id"] or "",
            "proof_status": leaf["proof_status"],
        }
        for leaf in registry["leaves"]
    ]


def matrix_text(rows: list[dict[str, str]]) -> str:
    fields = [
        "control_uid",
        "old_leaf_path",
        "old_label",
        "old_widget_kind",
        "old_source_handler",
        "target_route",
        "target_leaf_id",
        "target_label",
        "migration_status",
        "old_owner_deleted",
        "new_action_schema",
        "commit_class",
        "predictor_model",
        "proof_status",
    ]
    out = StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def build() -> tuple[dict, list[dict[str, str]]]:
    registry = _load_registry()
    _validate_authority(registry)
    _validate_legacy_census(registry)
    _validate_native_owner_projection(registry)
    _validate_interaction_action_graph(registry)
    _validate_interaction_source_anchors(registry)
    _validate_physical_input_graph(registry)
    _validate_commit_classes(registry)
    _validate_active_tool_graph(registry)
    _validate_workspace_action_graph(registry)
    return registry, _matrix_rows(registry)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the FL-4588 HTML UI registry, restamp its self-hash "
            "identity (--stamp), and project the migration matrix."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate registry self-hash + census and confirm matrix is current",
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help=(
            "Sole identity writer: recompute registry_sha256 for the document "
            "and every leaf/interaction, persist the registry, then rewrite "
            "the migration matrix. Does not invent leaves."
        ),
    )
    parser.add_argument(
        "--print-registry-hash",
        action="store_true",
        help="Print the canonical content hash without requiring a matching stamp",
    )
    args = parser.parse_args()

    if args.check and args.stamp:
        raise SystemExit("use either --check or --stamp, not both")

    if args.print_registry_hash:
        # Content hash only — does not require the stored stamp to match yet.
        print(canonical_registry_sha256(_load_registry()))
        return 0

    if args.stamp:
        # DELETE-FIRST ownership restore: load drifted stamps, overwrite with
        # the generator digest, persist, then run the full authority gate so a
        # bad body cannot leave a green stamp on an invalid contract.
        registry = _load_registry()
        before = registry.get("registry_sha256", "")
        digest = _stamp_registry_hashes(registry)
        _write_registry_identity(registry)
        registry, matrix = build()
        expected_matrix = matrix_text(matrix).encode()
        MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
        MATRIX_PATH.write_bytes(expected_matrix)
        print(f"stamped {REGISTRY_PATH.relative_to(ROOT)}")
        print(f"wrote {MATRIX_PATH.relative_to(ROOT)}")
        print(f"registry_sha256_before={before}")
        print(f"registry_sha256_after={digest}")
        print(f"controls={len(matrix)}")
        return 0

    registry, matrix = build()
    expected_matrix = matrix_text(matrix).encode()
    if args.check:
        if not MATRIX_PATH.is_file() or MATRIX_PATH.read_bytes() != expected_matrix:
            raise SystemExit(
                "stale generated file: " + str(MATRIX_PATH.relative_to(ROOT))
            )
        print(
            f"PASS: authoritative registry valid; {len(matrix)} controls accounted for; "
            "migration matrix current"
        )
        return 0

    MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    MATRIX_PATH.write_bytes(expected_matrix)
    print(f"wrote {MATRIX_PATH.relative_to(ROOT)}")
    print(
        f"controls={len(matrix)} registry_sha256={registry['registry_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
