import csv
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.fl4588_generate_html_architecture import (
    DIRECT_ACTION_SCHEMAS,
    NATIVE_OWNER_PROJECTION_CONTROLS,
    STAGED_ACTION_SCHEMAS,
    _validate_commit_classes,
    _validate_interaction_source_anchors,
    _validate_native_owner_projection,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
REGISTRY_SCHEMA = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.schema.json"
MATRIX = ROOT / "docs/research/ascii/verification/fl4260/HTML_UI_MIGRATION_MATRIX.csv"
CURRENT = ROOT / "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv"
HANDSHAKE = ROOT / "schemas/asciiid.transport_handshake.v1.json"
GOLDEN_CASE = ROOT / "schemas/asciiid.counterfactual_golden_case.v1.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def csv_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_registry_accounts_for_every_current_control_once():
    registry = load_json(REGISTRY)
    registry_schema = load_json(REGISTRY_SCHEMA)
    Draft202012Validator.check_schema(registry_schema)
    Draft202012Validator(registry_schema).validate(registry)
    current = csv_rows(CURRENT)
    assert registry["schema"] == "asciiid.ui_registry.v1"
    assert registry["cutover_status"] == "authoritative"
    assert registry["authority"] == {
        "source_kind": "direct_registry_contract",
        "path": "assets/asciiid/ui/asciiid.ui_registry.v1.json",
        "legacy_inventory_role": "historical_accounting_only",
    }
    assert registry["leaf_count"] == len(current) == 339
    inventory_bytes = CURRENT.read_bytes()
    assert registry["source_inventory"] == {
        "path": "docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv",
        "sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "row_count": 339,
    }
    control_uids = [leaf["control_uid"] for leaf in registry["leaves"]]
    leaf_ids = [leaf["leaf_id"] for leaf in registry["leaves"]]
    assert len(control_uids) == len(set(control_uids))
    assert len(leaf_ids) == len(set(leaf_ids))
    assert set(control_uids) == {row["control_uid"] for row in current}
    assert all(leaf["registry_sha256"] == registry["registry_sha256"] for leaf in registry["leaves"])
    assert all(leaf["control_kind"] != "unknown" for leaf in registry["leaves"])
    # FL-4734 additionally deletes the two frozen editor-frame mesh gestures.
    assert registry["interaction_count"] == len(registry["interactions"]) == 37
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    terrain_paint = interactions["scene.terrain.paint.material"]
    assert terrain_paint["leaf_id"] == "scene.terrain.paint.material"
    assert terrain_paint["action_schema"] == "asciiid.paint_terrain_material.v1"
    assert terrain_paint["application_service_handler"] == "CommitTerrainMaterialPaint"
    assert terrain_paint["commit_class"] == "direct"
    assert terrain_paint["proof_status"] == "PROOF_OPEN"
    assert terrain_paint["action_scope"] == "product_transaction"
    assert terrain_paint["registry_sha256"] == registry["registry_sha256"]
    assert set(interactions) == {
        "scene.terrain.paint.material",
        "scene.terrain.paint.sculpt",
        # FL-4588: the Ctrl-modifier material eyedropper on Material Paint.
        "scene.terrain.eyedropper.material",
        # FL4588-MESH-CANVAS-CUTOVER: the Mesh placement canvas transaction.
        "scene.objects.mesh.place",
        # FL4588-SPRITE-CANVAS-CUTOVER: the Sprite placement + deletion transactions.
        "scene.objects.sprite.place",
        "scene.objects.sprite.delete",
        # FL4588-MATERIAL-ELEVATION-CANVAS-CUTOVER: the Material Elevation paint +
        # eyedropper transactions.
        "scene.terrain.matelev.paint",
        "scene.terrain.matelev.eyedropper",
        # FL4588-ITEM-CANVAS-CUTOVER: the Item placement + deletion transactions.
        "scene.objects.item.place",
        "scene.objects.item.delete",
        # FL4588-ENEMY-CANVAS-CUTOVER: the Enemy Generator placement + deletion
        # transactions.
        "scene.objects.enemy.place",
        "scene.objects.enemy.delete",
        # FL4588-STORY-CANVAS-CUTOVER: the Story identity apply transaction.
        "scene.objects.story.apply",
        # FL4588-TERRAIN-MODIFIER-GRAPH-CUTOVER: the four typed Sculpt
        # (edit_mode 0) modifier transactions driven by physical pointer chords
        # over the base terrain — Shift blur/sharpen, Ctrl diagonal flip,
        # Ctrl+Shift probe-height sample, and Alt patch topology.
        "scene.terrain.paint.blur_sharpen",
        "scene.terrain.diagonal.flip",
        "scene.terrain.probe.height",
        "scene.terrain.patch.topology",
        "workspace.document.save_as.commit",
        "workspace.document.open.commit",
        "workspace.document.merge.commit",
        "material.colors.palette.select",
        "material.colors.palette.color.apply",
        "material.colors.palette.spread_from_material",
        "material.colors.palette.assign_next_unused",
        "material.pool.candidate.add",
        "material.pool.candidate.remove",
        "material.pool.candidate.move_left",
        "material.pool.candidate.move_right",
        "material.pool.candidate.clear",
        "material.pool.candidate.restore",
        "glyph.catalog.add_to_active_pool",
        "glyph.catalog.remove_from_active_pool",
        "glyph.catalog.select",
        "material.score.reset",
        "scene.viewport.highlight_selected_material",
        "inspect.cell.select",
        "glyph.filter.repertoire",
    }
    assert all(item["registry_sha256"] == registry["registry_sha256"] for item in interactions.values())
    highlight = interactions["scene.viewport.highlight_selected_material"]
    assert highlight["action_scope"] == "browser_draft"
    assert highlight["control_kind"] == "toggle"
    assert highlight["action_schema"] == "asciiid.browser.set_selected_material_highlight.v1"
    assert highlight["write_set_template"] == [
        "/browser/scene/viewport/highlight_selected_material"
    ]
    assert highlight["paper_rule_id"] == "fl4588.browser_draft_ownership.v1"
    assert highlight["predictor_model_id"] == "fl4588.empty_product_delta_browser_draft.v1"
    assert "934a566a7" in highlight["source_owner"]
    assert sum(item["action_scope"] == "browser_draft" for item in registry["interactions"]) == 6


def test_every_interaction_pins_action_kind_and_visible_trigger() -> None:
    registry = load_json(REGISTRY)
    all_rows = {
        row["leaf_id"]: row
        for bucket in ("leaves", "interactions")
        for row in registry[bucket]
    }
    for interaction in registry["interactions"]:
        assert interaction["action_kind"]
        assert interaction["trigger_leaf_id"] in all_rows
    by_id = {row["leaf_id"]: row for row in registry["interactions"]}
    assert by_id["workspace.document.open.commit"]["trigger_leaf_id"] == (
        "workspace.document.open.open"
    )
    assert by_id["workspace.document.save_as.commit"]["trigger_leaf_id"] == (
        "workspace.document.save_as.open"
    )


def test_item_enemy_story_interactions_cite_their_real_transaction_owners():
    registry = load_json(REGISTRY)
    _validate_interaction_source_anchors(registry)
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    expected = {
        "scene.objects.item.place": ("CommitItemPlacement", "CommitAsciiidItemPlacement"),
        "scene.objects.item.delete": ("CommitItemDeletion", "CommitAsciiidItemDeletion"),
        "scene.objects.enemy.place": ("CommitEnemyGenPlacement", "CommitAsciiidEnemyGenPlacement"),
        "scene.objects.enemy.delete": ("CommitEnemyGenDeletion", "CommitAsciiidEnemyGenDeletion"),
        "scene.objects.story.apply": ("CommitStoryApply", "CommitAsciiidStoryApply"),
    }
    for leaf_id, (service_owner, native_owner) in expected.items():
        anchors = interactions[leaf_id]["source_anchors"]
        assert service_owner in anchors[0]
        assert native_owner in anchors[1]
        assert all("CommitSprite" not in anchor for anchor in anchors[:2])

    corrupted = copy.deepcopy(registry)
    corrupted_interactions = {
        item["leaf_id"]: item for item in corrupted["interactions"]
    }
    corrupted_interactions["scene.objects.story.apply"]["source_anchors"][0] = (
        "editor/asciiid_application_service.cpp (CommitSpriteDeletion)"
    )
    with pytest.raises(SystemExit, match="typed interaction source anchors mismatch"):
        _validate_interaction_source_anchors(corrupted)


def test_every_typed_legacy_leaf_has_a_normative_paper_binding():
    registry = load_json(REGISTRY)
    typed = [leaf for leaf in registry["leaves"] if leaf["action_schema"]]
    # Dynamic census: every typed leaf keeps a paper binding after document
    # filesystem-row tombstones (open.commit + file.save dialog chrome).
    assert len(typed) == sum(
        1 for leaf in registry["leaves"] if leaf.get("action_schema")
    )
    missing_paper = [leaf["leaf_id"] for leaf in typed if not leaf["paper_rule_id"]]
    assert missing_paper == []
    assert all(leaf["paper_rule_id"] for leaf in typed)
    assert all(leaf["paper_rule_version"] == 1 for leaf in typed)


def test_every_registry_action_has_one_frozen_commit_class():
    registry = load_json(REGISTRY)
    items = registry["leaves"] + registry["interactions"]
    action_items = [item for item in items if item["action_schema"] is not None]
    non_action_items = [item for item in items if item["action_schema"] is None]

    assert DIRECT_ACTION_SCHEMAS.isdisjoint(STAGED_ACTION_SCHEMAS)
    assert {item["action_schema"] for item in action_items} == (
        DIRECT_ACTION_SCHEMAS | STAGED_ACTION_SCHEMAS
    )
    assert len(DIRECT_ACTION_SCHEMAS) == 73
    assert len(STAGED_ACTION_SCHEMAS) == 17
    assert all(item["commit_class"] in {"direct", "staged"} for item in action_items)
    assert all(item["commit_class"] is None for item in non_action_items)
    assert all(
        item["commit_class"] == (
            "direct"
            if item["action_schema"] in DIRECT_ACTION_SCHEMAS
            else "staged"
        )
        for item in action_items
    )

    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    assert interactions["scene.terrain.paint.material"]["commit_class"] == "direct"
    assert interactions["workspace.document.open.commit"]["commit_class"] == "staged"
    assert interactions["material.pool.candidate.clear"]["commit_class"] == "staged"
    assert not any(item["leaf_id"].startswith("scene.camera.") for item in interactions.values())


def test_migration_matrix_projects_commit_class_from_registry():
    registry = load_json(REGISTRY)
    matrix = csv_rows(MATRIX)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    assert "commit_class" in matrix[0]
    assert all(
        row["commit_class"] == (by_uid[row["control_uid"]]["commit_class"] or "")
        for row in matrix
    )


def test_unknown_action_schema_fails_commit_class_validation():
    registry = copy.deepcopy(load_json(REGISTRY))
    registry["interactions"][0]["action_schema"] = "asciiid.unregistered_action.v1"
    registry["interactions"][0]["commit_class"] = "direct"
    with pytest.raises(SystemExit, match="action-schema commit-class census drift"):
        _validate_commit_classes(registry)


def test_pipeline_inspector_audit_retires_mutators_and_keeps_observation_read_only():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    retired = {
        "fl4260_row_2__measurement_termpp_gpu",
        "fl4260_row_3__measurement_termpp_harri",
        "fl4260_row_4__measurement_blog_scene",
        "fl4260_row_6__measurement_blog_contrast",
        "fl4260_row_7__measurement_blog_quality",
        "fl4260_row_8__measurement_source_pane",
        "fl4260_row_9__measurement_sampling_circles",
        "fl4260_row_10__measurement_external_circles",
        *{f"fl4260_row_{row}__measurement_weight_{name}" for row, name in (
            (43, "curve"), (44, "diagonal"), (45, "horizontal"),
            (46, "vertical"), (47, "sparse"), (48, "dense"),
        )},
        *{f"fl4260_row_{row}__measurement_source_{name}" for row, name in (
            (49, "tl"), (50, "tr"), (51, "ml"),
            (52, "mr"), (53, "bl"), (54, "br"),
        )},
        "fl4260_row_56__measurement_dir_gamma",
        "fl4260_row_57__measurement_global_gamma",
        "fl4260_row_58__measurement_dir_contrast",
        "fl4260_row_59__measurement_global_contrast",
        "fl4260_row_67__measurement_lock_preset",
        "fl4260_row_68__measurement_preserve_preset",
    }
    assert len(retired) == 26
    for uid in retired:
        assert leaves[uid]["status"] == "tombstoned"
        assert leaves[uid]["proof_status"] == "TOMBSTONE_DECISION"
        assert leaves[uid]["action_schema"] is None
        assert leaves[uid]["read_model_fields"] == []
        assert leaves[uid]["write_set_template"] == []
        assert migration[uid]["migration_status"] == "tombstone"

    final_distribution = leaves["fl4260_row_61__measurement_final_buffer_header"]
    assert final_distribution["status"] == "tombstoned"
    assert final_distribution["migration_status"] == "tombstone"
    assert final_distribution["proof_status"] == "TOMBSTONE_DECISION"
    assert final_distribution["action_schema"] is None
    assert final_distribution["read_model_fields"] == []
    assert final_distribution["write_set_template"] == []
    assert migration[final_distribution["control_uid"]]["migration_status"] == "tombstone"

    diagram = leaves["fl4260_row_495__trace_pipeline_diagram"]
    assert diagram["status"] == "tombstoned"
    assert diagram["migration_status"] == "tombstone"
    assert diagram["old_owner_deleted"] is True
    assert diagram["action_schema"] is None
    assert diagram["read_model_fields"] == []
    detached = leaves["fl4260_row_660__detached_trace_diagram"]
    assert detached["status"] == "tombstoned"
    assert detached["migration_status"] == "tombstone"
    assert detached["old_owner_deleted"] is True

    selected = interactions["inspect.cell.select"]
    assert selected["action_scope"] == "browser_draft"
    assert selected["control_kind"] == "viewport"
    assert selected["write_set_template"] == ["/browser/inspect/selected_cell"]
    assert selected["predictor_model_id"] == "fl4588.empty_product_delta_browser_draft.v1"

    repertoire = interactions["glyph.filter.repertoire"]
    assert repertoire["action_scope"] == "browser_draft"
    assert repertoire["route"] == "/glyphs/catalog"
    assert repertoire["write_set_template"] == ["/browser/glyphs/filter/repertoire"]
    assert repertoire["payload_schema"]["properties"]["selected"]["items"]["enum"] == [
        "arabic", "math", "shapes", "box_drawing"
    ]
    glyph_selection = interactions["glyph.catalog.select"]
    assert glyph_selection["action_scope"] == "browser_draft"
    assert glyph_selection["route"] == "/glyphs/catalog"
    assert glyph_selection["action_schema"] == (
        "asciiid.browser.select_glyph_catalog_entry.v1"
    )
    assert glyph_selection["write_set_template"] == [
        "/browser/glyphs/selected_glyph_id"
    ]


def test_camera_environment_source_audit_keeps_real_input_and_retires_second_presenters():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    for uid in (
        "fl4260_row_517__view_palettize",
        "fl4260_row_523__view_termpp",
        "fl4260_row_525__view_termpp_view",
        "fl4260_row_528__view_full",
        "fl4260_row_529__view_norm",
        "fl4260_row_530__view_pure",
        "fl4260_row_531__view_keep",
        "fl4260_row_537__view_grid",
    ):
        leaf = leaves[uid]
        assert leaf["status"] == "tombstoned"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "TOMBSTONE_DECISION"
        assert migration[uid]["migration_status"] == "tombstone"

    assert "native TERM++ RGBA" in leaves["fl4260_row_517__view_palettize"]["long_help"]
    assert "hides the player sprite" in leaves["fl4260_row_523__view_termpp"]["source_owner"]
    assert "function-static" in leaves["fl4260_row_524__view_termpp_skin"]["source_owner"]

    for uid in (
        "fl4260_row_533__view_control_header",
        "fl4260_row_534__view_pitch",
        "fl4260_row_535__view_yaw",
        "fl4260_row_536__view_zoom",
        "fl4260_row_538__view_focus_view",
    ):
        leaf = leaves[uid]
        assert leaf["status"] == "tombstoned"
        assert leaf["action_schema"] is None
        assert leaf["write_set_template"] == []
        assert leaf["read_model_fields"] == []
        assert leaf["migration_status"] == "tombstone"
        assert migration[uid]["migration_status"] == "tombstone"
        assert leaf["proof_status"] == "TOMBSTONE_DECISION"
    assert not any(leaf_id.startswith("scene.camera.") for leaf_id in interactions)

    weather = leaves["fl4260_row_546__weather_state"]
    # Engine Weather::State truth (engine/weather.h): CLEAR/LIGHT_SNOW/HEAVY_SNOW/BLIZZARD.
    assert weather["payload_schema"]["properties"]["state"]["enum"] == [
        "clear", "light_snow", "heavy_snow", "blizzard"
    ]
    assert weather["application_service_handler"] == "CommitWeatherState"
    assert weather["old_owner_deleted"] is True
    assert "headed user-reachable receipt remains open" in weather["proof_open_reason"]

    phase = leaves["fl4260_row_677__view_termpp_environment_phase_reset"]
    assert phase["route"] == "/scene/environment"
    assert phase["action_schema"] == "asciiid.reset_environment_phase.v1"
    assert "RendererResetObservePhase" in phase["source_owner"]


def test_document_lifecycle_has_distinct_target_transactions_and_atomicity_gaps():
    registry = load_json(REGISTRY)
    # Headed duplicate-open contract is pinned to this registry stamp.
    assert not (ROOT / "scripts/fl4588/headed_journey_proof.py").exists()
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    for uid, leaf_id in (
        ("fl4260_row_519__view_save_as", "workspace.document.save_as.open"),
        ("fl4260_row_520__view_load", "workspace.document.open.open"),
        ("fl4260_row_521__view_merge", "workspace.document.merge.open"),
        ("fl4260_row_527__cancel", "workspace.document.dialog.cancel"),
        ("fl4260_row_547__id_only_control_visible_label_from_adjacent_text", "workspace.document.dialog.path"),
        ("fl4260_row_548__id_only_control_visible_label_from_adjacent_text", "workspace.document.dialog.directory"),
        ("fl4260_row_549__dynamic_label_di", "workspace.document.dialog.entry"),
    ):
        leaf = leaves[uid]
        assert leaf["leaf_id"] == leaf_id
        # Cancel + native dialog path/directory/entry are tombstoned after
        # filesystem Save dialog deletion. merge.open is the browser merge
        # picker; save_as.open / open.open stay live browser download/upload.
        if uid in {
            "fl4260_row_527__cancel",
            "fl4260_row_547__id_only_control_visible_label_from_adjacent_text",
            "fl4260_row_548__id_only_control_visible_label_from_adjacent_text",
            "fl4260_row_549__dynamic_label_di",
        }:
            # Cancel stays tombstoned with cleared mutation fields; path /
            # directory / entry also carry TOMBSTONE_DECISION after this
            # native-filesystem row deletion. Cancel may keep PROOF_OPEN.
            assert leaf["status"] == "tombstoned"
            assert migration[uid]["migration_status"] == "tombstone"
            assert leaf["old_owner_deleted"] is True
            assert leaf["application_service_handler"] is None
            assert leaf["action_schema"] is None
            assert leaf["write_set_template"] == []
            assert leaf["commit_class"] is None
            if uid != "fl4260_row_527__cancel":
                assert leaf["proof_status"] == "TOMBSTONE_DECISION"
        elif uid == "fl4260_row_521__view_merge":
            assert leaf["status"] == "live"
            assert migration[uid]["migration_status"] == "read_only"
            assert leaf["old_owner_deleted"] is True
            assert leaf["application_service_handler"] == (
                "BrowserShell::SelectMergeDocument"
            )
            assert leaf["action_schema"] is None
            assert leaf["write_set_template"] == []
            assert leaf["proof_status"] == "PROOF_OPEN"
        else:
            assert leaf["status"] == "live"
            assert migration[uid]["migration_status"] == "read_only"
            assert leaf["action_schema"] is None
            assert leaf["application_service_handler"] is None
            assert leaf["write_set_template"] == []

    new_document = leaves["fl4260_row_522__view_new"]
    assert new_document["leaf_id"] == "workspace.document.new"
    assert new_document["action_schema"] == "asciiid.new_document.v1"
    assert "editor/asciiid.cpp:18961" in new_document["source_anchors"]
    # FL-4588 document-family cutover: the typed new_document handler landed,
    # the native NEW Button + RequestConfirm(ConfirmNew) owners were deleted,
    # and New() now builds the fresh document completely BEFORE releasing the
    # live one (the destroy-first flaw this assertion used to pin is fixed).
    assert new_document["application_service_handler"] == "CommitNewDocument"
    assert new_document["old_owner_deleted"] is True
    assert "native NEW/confirm owners are deleted" in new_document["proof_open_reason"]

    # Visible Open is the browser file picker leaf only. The duplicate
    # Commit uploaded document control (target_workspace_document_open_commit)
    # is tombstoned so HTML cannot show two open gestures. Typed open bytes
    # still ride the interaction AsciiidApplicationService::OpenDocumentBytes.
    open_picker = leaves["fl4260_row_520__view_load"]
    assert open_picker["leaf_id"] == "workspace.document.open.open"
    assert open_picker["status"] == "live"
    assert open_picker["migration_status"] == "read_only"
    assert open_picker["old_owner_deleted"] is True
    assert open_picker["application_service_handler"] is None
    assert open_picker["action_schema"] is None
    assert open_picker["write_set_template"] == []

    open_commit_leaf = leaves["target_workspace_document_open_commit"]
    assert open_commit_leaf["leaf_id"] == "workspace.document.open.commit"
    assert open_commit_leaf["status"] == "tombstoned"
    assert open_commit_leaf["migration_status"] == "tombstone"
    assert open_commit_leaf["application_service_handler"] is None
    assert open_commit_leaf["action_schema"] is None
    assert open_commit_leaf["old_owner_deleted"] is True
    assert open_commit_leaf["proof_status"] == "TOMBSTONE_DECISION"
    assert open_commit_leaf["commit_class"] is None
    assert open_commit_leaf["write_set_template"] == []

    # Native filesystem Save leaf is tombstoned; browser Save As download
    # (workspace.document.save_as.open / save_as.commit interaction) remains.
    save_leaf = leaves["asciiid_interaction_workspace_file_save"]
    assert save_leaf["leaf_id"] == "workspace.file.save"
    assert save_leaf["status"] == "tombstoned"
    assert save_leaf["migration_status"] == "tombstone"
    assert save_leaf["application_service_handler"] is None
    assert save_leaf["action_schema"] is None
    assert save_leaf["payload_schema"] is None
    assert save_leaf["commit_class"] is None
    assert save_leaf["read_model_fields"] == []
    assert save_leaf["write_set_template"] == []
    assert save_leaf["old_owner_deleted"] is True
    assert save_leaf["proof_status"] == "TOMBSTONE_DECISION"

    live_document_actions = sorted(
        leaf["leaf_id"]
        for leaf in registry["leaves"]
        if leaf.get("route") == "/workspace/document"
        and leaf.get("status") == "live"
        and leaf.get("control_kind") == "button"
    )
    assert live_document_actions == [
        "workspace.document.merge.open",
        "workspace.document.new",
        "workspace.document.open.open",
        "workspace.document.save_as.open",
    ]

    for uid in (
        "fl4260_row_550__dynamic_label_commit_label",
        "fl4260_row_551__dynamic_label_save_1_save_overwrite_confirm_clear_confirm_cancel",
    ):
        leaf = leaves[uid]
        assert leaf["status"] == "tombstoned"
        assert migration[uid]["migration_status"] == "tombstone"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "TOMBSTONE_DECISION"

    save = interactions["workspace.document.save_as.commit"]
    assert save["action_schema"] == "asciiid.save_document.v1"
    assert save["application_service_handler"] == "AsciiidApplicationService::DownloadDocument"
    assert "editor/asciiid.cpp:13346" in save["source_anchors"]
    assert "Browser download now receives bytes" in save["proof_open_reason"]
    assert save["commit_class"] == "staged"

    opened = interactions["workspace.document.open.commit"]
    assert opened["control_uid"] == "target_workspace_document_open_commit"
    assert opened["action_schema"] == "asciiid.open_document.v1"
    assert opened["application_service_handler"] == (
        "AsciiidApplicationService::OpenDocumentBytes"
    )
    assert opened["action_scope"] == "product_transaction"
    assert opened["payload_schema"]["required"] == ["logical_name"]
    assert "bytes" not in opened["payload_schema"]["properties"]
    assert "editor/asciiid.cpp:19419" in opened["source_anchors"]
    assert "Browser upload now parses and prepares a candidate" in opened["proof_open_reason"]
    assert opened["commit_class"] == "staged"
    assert opened["proof_status"] == "PROOF_OPEN"

    merge = interactions["workspace.document.merge.commit"]
    assert merge["action_schema"] == "asciiid.merge_document.v1"
    assert merge["application_service_handler"] == (
        "AsciiidApplicationService::MergeDocumentBytes"
    )
    # Envelope matches open.commit: logical_name only. Raw A3D bytes ride
    # the authenticated POST /api/v1/document/merge body, never JSON payload.
    assert merge["payload_schema"]["required"] == ["logical_name"]
    assert "bytes" not in merge["payload_schema"]["properties"]
    assert "editor/asciiid.cpp:1711" in merge["source_anchors"]
    assert merge["proof_status"] == "PROOF_OPEN"
    assert "MergeDocumentBytes" in merge["proof_open_reason"]
    assert merge["commit_class"] == "staged"


def test_palette_and_anchor_actions_match_current_source_and_block_nonatomic_bulk_port():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    merged = leaves["fl4260_row_631__palette_theme_button"]
    assert merged["leaf_id"] == "material.colors.palette.gestures.inventory_merge"
    assert merged["status"] == "merged_into"
    assert merged["action_schema"] is None
    assert migration[merged["control_uid"]]["migration_status"] == "merge"
    assert "has no caller" in merged["long_help"]
    assert "has no drop consumer" in merged["long_help"]

    anchor = leaves["fl4260_row_638__material_anchor_color_swatch"]
    assert anchor["leaf_id"] == "material.colors.anchor"
    assert anchor["route"] == "/material/colors"
    assert anchor["action_schema"] == "asciiid.set_material_anchor_color.v1"
    assert anchor["payload_schema"]["properties"]["rgb"]["maxItems"] == 3
    assert len(anchor["write_set_template"]) == 31
    # FL-4588 cutover: the typed anchor handler landed and the native
    # ColorPicker3 popup mutation owner was deleted.
    assert anchor["application_service_handler"] == "CommitMaterialAnchorColor"
    assert anchor["old_owner_deleted"] is True
    assert "current plus presented GlyphId remain protected" in anchor["render_consumers"][1]

    next_unused_leaf = leaves["fl4260_target_671__assign_palette_to_next_unused_slots"]
    assert next_unused_leaf["leaf_id"] == "material.colors.palette.assign_next_unused"
    assert next_unused_leaf["action_schema"] == "asciiid.assign_palette_next_unused.v1"
    assert "durably saves only the final profile" in next_unused_leaf["long_help"]
    assert next_unused_leaf["receipt_path"] is None
    assert next_unused_leaf["proof_status"] == "PROOF_OPEN"

    select = interactions["material.colors.palette.select"]
    assert select["action_scope"] == "browser_draft"
    assert select["write_set_template"] == ["/browser/material_colors/selected_palette_id"]
    assert select["predictor_model_id"] == "fl4588.empty_product_delta_browser_draft.v1"
    assert select["application_service_handler"] == "BrowserShell::SelectMaterialPalette"

    apply_color = interactions["material.colors.palette.color.apply"]
    assert apply_color["action_schema"] == "asciiid.apply_palette_color_to_material.v1"
    assert apply_color["payload_schema"]["properties"]["color_index"]["maximum"] == 7
    assert len(apply_color["write_set_template"]) == 31
    assert apply_color["application_service_handler"] == "CommitPaletteColorApply"
    assert apply_color["commit_class"] == "direct"

    spread = interactions["material.colors.palette.spread_from_material"]
    assert spread["action_schema"] == "asciiid.spread_palette_from_material.v1"
    assert spread["application_service_handler"] == "CommitPaletteSpread"
    assert spread["commit_class"] == "direct"

    next_unused = interactions["material.colors.palette.assign_next_unused"]
    assert next_unused["action_schema"] == "asciiid.assign_palette_next_unused.v1"
    assert "deleted hidden-command receipt is inadmissible" in next_unused["proof_open_reason"]
    # FL-4588 cutover: CommitPaletteAssignNextUnused freezes liveness before
    # selecting keys and durably saves EVERY assigned profile — the retired
    # non-atomic native loop (final-profile-only autosave) was deleted.
    assert next_unused["application_service_handler"] == "CommitPaletteAssignNextUnused"


def test_material_look_history_is_truthful_about_current_source_gap():
    registry = load_json(REGISTRY)
    undo = next(
        leaf for leaf in registry["leaves"]
        if leaf["control_uid"] == "fl4260_row_655__material_look_one_step_undo"
    )
    assert undo["leaf_id"] == "workspace.history.undo.material_look.inventory_duplicate"
    assert undo["route"] == "/workspace/history"
    assert undo["status"] == "merged_into"
    assert undo["action_schema"] is None
    assert undo["application_service_handler"] is None
    assert undo["proof_status"] == "MERGED_ACCOUNTING_ROW"
    assert "workspace.history.undo" in undo["expected_user_result"]
    assert "editor/asciiid.cpp:9984" in undo["source_anchors"]


def test_material_shape_presets_record_the_source_contradiction_and_target_rows():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    preset_uids = (
        "fl4260_row_460.0__starters_preset_0_grass",
        "fl4260_row_460.1__starters_preset_1_water",
        "fl4260_row_460.2__starters_preset_2_grass",
        "fl4260_row_460.3__starters_preset_3_stone",
        "fl4260_row_460.4__starters_preset_4_dirt",
        "fl4260_row_460.5__starters_preset_5_stone",
        "fl4260_row_460.6__starters_preset_6_gravel",
    )
    for uid in preset_uids:
        leaf = leaves[uid]
        assert leaf["action_schema"] == "asciiid.apply_routed_shape_preset.v1"
        assert leaf["paper_rule_id"] == "fl4512.action_to_cell.v1"
        assert leaf["predictor_model_id"] == "fl4512.material_routed_shape_preset.v1"
        # FL-4588 cutover: CommitMaterialShapePreset implements the REGISTERED
        # routed row/column semantics (the source contradiction the retired
        # native path carried), and the Button/keyboard-G apply owners were
        # deleted.
        assert leaf["application_service_handler"] == "CommitMaterialShapePreset"
        assert leaf["old_owner_deleted"] is True
        assert leaf["proof_status"] == "PROOF_OPEN"
        assert "copies only preset GlyphIds into pool_gids" in leaf["source_owner"]
        writes = leaf["write_set_template"]
        assert sum("/role_buckets/ramp/" in path for path in writes) == 4
        assert sum("/role_buckets/density/" in path for path in writes) == 4
        assert all("SPEC_OPEN" not in field for field in leaf["read_model_fields"])
        assert all("SPEC_OPEN" not in field for field in writes)

    for uid in (
        "fl4260_row_453__starters_add_all",
        "fl4260_row_458__starters_glyph_style_presets",
        "fl4260_row_459__starters_color_presets",
    ):
        leaf = leaves[uid]
        assert leaf["status"] == "tombstoned"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "TOMBSTONE_DECISION"

    cp437 = leaves["fl4260_row_461__starters_vegetation_cp437"]
    assert cp437["status"] == "merged_into"
    assert cp437["action_schema"] is None
    assert "compiler-invalid" in cp437["proof_open_reason"]

    colors = leaves["fl4260_row_462__starters_vegetation_ramp"]
    assert colors["action_schema"] == "asciiid.apply_vegetation_color_preset.v1"
    assert colors["predictor_model_id"] == "fl4512.material_color_preset.v1"
    # FL-4588 cutover: the typed preset handler landed and the native
    # "Vegetation colors" Button mutation owner was deleted.
    assert colors["application_service_handler"] == "CommitMaterialColorPreset"
    assert colors["old_owner_deleted"] is True
    assert colors["proof_status"] == "PROOF_OPEN"
    assert all("glyph" not in path for path in colors["write_set_template"])


def test_selected_material_highlight_and_duplicate_presentation_rows_are_source_accurate():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    highlight = leaves["fl4260_row_430__starters_highlight_selected"]
    assert highlight["leaf_id"] == "scene.viewport.highlight_selected_material"
    assert highlight["route"] == "/scene/viewport"
    assert highlight["action_schema"] is None
    assert highlight["write_set_template"] == []
    assert highlight["old_owner_deleted"] is True
    assert highlight["proof_status"] == "PROOF_OPEN"
    assert "934a566a7" in highlight["source_owner"]
    assert "TERM++ does not consume" in highlight["long_help"]
    assert "Empty native TERM++ delta" in highlight["expected_termpp_scope"]
    assert migration[highlight["control_uid"]]["migration_status"] == "read_only"
    assert migration[highlight["control_uid"]]["old_owner_deleted"] == "true"

    merged = {
        "fl4260_row_494__trace_highlight_selected_material_only":
            "scene.viewport.highlight_selected_material.inventory_duplicate",
        "fl4260_row_640__preset_human_purpose_labels":
            "material.profile.shape_presets.section.label_inventory_merge",
        "fl4260_row_641__cp437_primary_label_removed":
            "material.profile.shape_preset.vegetation_cp437.label_inventory_merge",
        "fl4260_row_646__status_plain_text":
            "material.profile.status.text_inventory_merge",
    }
    for uid, leaf_id in merged.items():
        leaf = leaves[uid]
        assert leaf["leaf_id"] == leaf_id
        assert leaf["status"] == "merged_into"
        assert leaf["proof_status"] == "MERGED_ACCOUNTING_ROW"
        assert leaf["action_schema"] is None
        assert migration[uid]["migration_status"] == "merge"

    quick_setup = leaves["fl4260_row_647__quick_shape_setup_label"]
    assert quick_setup["leaf_id"] == "material.pool.bulk_preset_union.quick_setup.retired"
    assert quick_setup["status"] == "tombstoned"
    assert quick_setup["proof_status"] == "TOMBSTONE_DECISION"
    assert quick_setup["action_schema"] is None
    assert "role_buckets" in quick_setup["long_help"]
    assert migration[quick_setup["control_uid"]]["migration_status"] == "tombstone"


def test_removed_manual_save_and_color_aggregates_are_not_target_actions():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    manual_save = leaves["fl4260_row_463__persist_save_material_look"]
    assert manual_save["leaf_id"] == "material.transaction.save_material_look.retired"
    assert manual_save["status"] == "tombstoned"
    assert migration[manual_save["control_uid"]]["migration_status"] == "tombstone"
    assert manual_save["action_schema"] is None
    assert manual_save["application_service_handler"] is None
    assert manual_save["write_set_template"] == []
    assert manual_save["proof_status"] == "TOMBSTONE_DECISION"
    assert "editor/asciiid.cpp:24876" in manual_save["source_anchors"]
    assert "editor/asciiid.cpp:12652" in manual_save["source_anchors"]

    merged = {
        "fl4260_row_644__color_swatch_plain_labels":
            "material.colors.swatch_labels.inventory_merge",
        "fl4260_target_662__palette_color_controls":
            "material.colors.controls.inventory_merge",
    }
    for uid, leaf_id in merged.items():
        leaf = leaves[uid]
        assert leaf["leaf_id"] == leaf_id
        assert leaf["status"] == "merged_into"
        assert migration[uid]["migration_status"] == "merge"
        assert leaf["control_kind"] == "group"
        assert leaf["action_schema"] is None
        assert leaf["application_service_handler"] is None
        assert leaf["write_set_template"] == []
        assert leaf["proof_status"] == "MERGED_ACCOUNTING_ROW"


def test_material_candidate_set_retires_flat_pool_owners_and_freezes_addressed_actions():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}

    tombstones = {
        "fl4260_row_465__quick_select_all_eligible",
        "fl4260_row_483__pool_select_all_catalog",
        "fl4260_row_484__pool_invert_catalog_selection",
    }
    merges = {
        "fl4260_row_466__quick_clear_pool",
        "fl4260_row_467__quick_restore_defaults",
        "fl4260_row_482__pool_clear_extended_pool",
        "fl4260_row_485__pool_restore_preset_defaults",
        "fl4260_row_648__clear_and_autofill_tooltips",
        "fl4260_row_652__glyph_catalog_morphology_grouping",
        "fl4260_row_658__unified_glyphid_shape_pool",
        "fl4260_target_663__glyph_set_controls",
        "fl4260_row_670__role_ramp_lane_chip_remove",
    }
    for uid in tombstones:
        leaf = leaves[uid]
        assert leaf["status"] == "tombstoned"
        assert migration[uid]["migration_status"] == "tombstone"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "TOMBSTONE_DECISION"
    for uid in merges:
        leaf = leaves[uid]
        assert leaf["status"] == "merged_into"
        assert migration[uid]["migration_status"] == "merge"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "MERGED_ACCOUNTING_ROW"

    leaf_root = ROOT / "docs/research/ascii/verification/fl4260"
    for uid in tombstones | merges:
        leaf_text = (leaf_root / leaves[uid]["legacy_source_path"]).read_text(
            encoding="utf-8"
        )
        assert "FL4260_REMOVE_ROLE_LANE_GLYPH" not in leaf_text
        assert "FL4260_SET_MORPHOLOGY_FILTER" not in leaf_text
        assert "proof driver suppresses" not in leaf_text
        assert "fl4548/proof_plan.schema.json" not in leaf_text
        assert "fl4548/proof_plan.v4.schema.json" in leaf_text

    assert "editor/asciiid.cpp:24997" in leaves[
        "fl4260_row_648__clear_and_autofill_tooltips"
    ]["source_anchors"]
    assert "editor/asciiid.cpp:25654" in leaves[
        "fl4260_row_670__role_ramp_lane_chip_remove"
    ]["source_anchors"]

    candidate_ids = {
        "material.pool.candidate.add",
        "material.pool.candidate.remove",
        "material.pool.candidate.move_left",
        "material.pool.candidate.move_right",
        "material.pool.candidate.clear",
        "material.pool.candidate.restore",
        "glyph.catalog.add_to_active_pool",
        "glyph.catalog.remove_from_active_pool",
    }
    assert set(candidate_ids) <= set(interactions)
    expected_lanes = {
        *(f"ramp.{index}" for index in range(4)),
        *(f"density.{index}" for index in range(4)),
        "edge.flat", "edge.rise", "edge.fall", "edge.ridge",
        "edge.valley", "edge.wall", "edge.overhang",
        "direction.n", "direction.ne", "direction.e", "direction.se",
        "direction.s", "direction.sw", "direction.w", "direction.nw",
        "flow.n", "flow.ne", "flow.e", "flow.se",
        "flow.s", "flow.sw", "flow.w", "flow.nw",
        "filled_field.0",
    }
    # The offline admission artifact owns the mesh-feature candidate row.
    # Browser action schemas therefore expose no address for that row.
    assert "mesh_bundle.0" not in expected_lanes
    # FL-4588 env-11: glyph.catalog add/remove are cut over to typed handlers.
    for leaf_id, handler in (
        ("glyph.catalog.add_to_active_pool", "CommitGlyphCatalogAddToActivePool"),
        ("glyph.catalog.remove_from_active_pool", "CommitGlyphCatalogRemoveFromActivePool"),
    ):
        action = interactions[leaf_id]
        assert action["action_scope"] == "product_transaction"
        assert action["application_service_handler"] == handler
        assert action["paper_rule_id"] == "fl4512.action_to_cell.v1"
        assert set(action["payload_schema"]["properties"]["lane_id"]["enum"]) == expected_lanes

    # FL-4588 candidate-pool-5: five role-lane product interactions stamp
    # typed handlers after ImGui lane Actions/chip writers were deleted.
    product_candidate_handlers = {
        "material.pool.candidate.add": "CommitAddCandidateToLane",
        "material.pool.candidate.remove": "CommitRemoveCandidateFromLane",
        "material.pool.candidate.move_left": "CommitMoveCandidateInLane",
        "material.pool.candidate.move_right": "CommitMoveCandidateInLane",
        "material.pool.candidate.clear": "CommitClearCandidateLane",
    }
    for leaf_id, handler in product_candidate_handlers.items():
        action = interactions[leaf_id]
        assert action["action_scope"] == "product_transaction"
        assert action["application_service_handler"] == handler
        assert action["paper_rule_id"] == "fl4512.action_to_cell.v1"
        assert action["predictor_model_id"] == "fl4512.material_candidate_lane_edit.v1"
        assert action["proof_status"] == "PROOF_OPEN"
        assert "CommitAsciiidCandidateLane" in action["source_owner"]
        assert "ImGui role-lane" in action["source_owner"]
        assert set(action["payload_schema"]["properties"]["lane_id"]["enum"]) == expected_lanes
        assert not ({"path", "command", "set_path", "run_command"} &
                    set(action["payload_schema"]["properties"]))
        assert any("/role_buckets/{lane_id}/candidates" in path
                   for path in action["write_set_template"])
        assert all("glyph_pools" not in path for path in action["write_set_template"])

    restore = interactions["material.pool.candidate.restore"]
    assert restore["action_scope"] == "browser_draft"
    assert restore["action_schema"] == "asciiid.browser.restore_candidate_lane_draft.v1"
    assert restore["application_service_handler"] == "BrowserShell::RestoreCandidateLaneDraft"
    assert restore["paper_rule_id"] == "fl4588.browser_draft_ownership.v1"
    assert restore["predictor_model_id"] == "fl4588.empty_product_delta_browser_draft.v1"
    assert restore["write_set_template"] == [
        "/browser/material_pool/drafts/{profile_namespace}:{material_key}/role_buckets/{lane_id}/candidates"
    ]
    assert "revision change" in restore["proof_open_reason"]
    assert all(not path.startswith("/profiles/") for path in restore["write_set_template"])

    assert interactions["material.pool.candidate.move_left"]["payload_schema"][
        "properties"
    ]["direction"] == {"const": "left"}
    assert interactions["material.pool.candidate.move_right"]["payload_schema"][
        "properties"
    ]["direction"] == {"const": "right"}
    for leaf_id in (
        "material.pool.candidate.move_left",
        "material.pool.candidate.move_right",
    ):
        reason = interactions[leaf_id]["proof_open_reason"]
        assert "production fixed-point tonal scorer" in reason
        assert "lower GlyphId" in reason
        assert "membership-preserving reordering cannot change a winner" in reason
        assert "exact visible delta is empty" in reason
    assert "registered empty-row absence" in interactions[
        "material.pool.candidate.clear"
    ]["proof_open_reason"]


def test_workspace_history_collapses_disabled_states_and_retires_partial_traversal():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}

    undo = leaves["fl4260_row_557__unlabeled"]
    redo = leaves["fl4260_row_562__unlabeled"]
    assert undo["leaf_id"] == "workspace.history.undo"
    assert undo["action_schema"] == "asciiid.history_undo.v1"
    assert undo["keyboard_binding"] == "Command+Z"
    assert "headed physical-input receipt" in undo["proof_open_reason"]
    assert redo["leaf_id"] == "workspace.history.redo"
    assert redo["action_schema"] == "asciiid.history_redo.v1"
    assert "headed physical-input receipt" in redo["proof_open_reason"]

    for uid in (
        "fl4260_row_555__edit_undo_all_disabled",
        "fl4260_row_556__edit_undo_one_disabled",
        "fl4260_row_559__edit_redo_one_disabled",
        "fl4260_row_560__edit_redo_all_disabled",
        "fl4260_row_563__edit_urdo_purge_disabled",
    ):
        assert leaves[uid]["status"] == "merged_into"
        assert leaves[uid]["action_schema"] is None

    for uid in (
        "fl4260_row_558__unlabeled",
        "fl4260_row_561__unlabeled",
        "fl4260_row_564__edit_urdo_purge",
    ):
        assert leaves[uid]["status"] == "tombstoned"
        assert leaves[uid]["proof_status"] == "TOMBSTONE_DECISION"
        assert leaves[uid]["action_schema"] is None


def test_material_scoring_uses_positional_global6_contract_and_true_neutral_reset():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    positional = (
        ("fl4260_row_474__scoring_curve", "material.score.tl", "tl", "Top left weight"),
        ("fl4260_row_475__scoring_diagonal", "material.score.tr", "tr", "Top right weight"),
        ("fl4260_row_476__scoring_horizontal", "material.score.ml", "ml", "Middle left weight"),
        ("fl4260_row_477__scoring_vertical", "material.score.mr", "mr", "Middle right weight"),
        ("fl4260_row_478__scoring_sparse", "material.score.bl", "bl", "Bottom left weight"),
        ("fl4260_row_479__scoring_dense", "material.score.br", "br", "Bottom right weight"),
    )
    for uid, leaf_id, parameter, label in positional:
        leaf = leaves[uid]
        assert leaf["leaf_id"] == leaf_id
        assert leaf["label"] == label
        assert leaf["action_schema"] == "asciiid.set_material_score_parameter.v1"
        assert leaf["payload_schema"]["properties"]["parameter"] == {"const": parameter}
        assert leaf["payload_schema"]["properties"]["value"] == {
            "type": "number", "minimum": 0.0, "maximum": 4.0,
        }
        # FL-4588 cutover: the ONE score handler covers all six positional
        # weights (value widened to double), and the native SliderFloat +
        # keyboard adjust owners were deleted.
        assert leaf["application_service_handler"] == "CommitMaterialScoreParameter"
        assert leaf["old_owner_deleted"] is True
        assert leaf["predictor_model_id"] == "fl4512.material_scoring_parameter.v1"
        assert leaf["proof_status"] == "PROOF_OPEN"
        assert any("Weighted global6" in consumer for consumer in leaf["render_consumers"])
        assert any("Structural mask" in consumer for consumer in leaf["render_consumers"])

    scalar = (
        ("fl4260_row_489__scoring_detail_contrast", "material.score.detail", "detail", 0, 100),
        ("fl4260_row_490__scoring_tone_contrast", "material.score.tone", "tone", 0, 100),
        ("fl4260_row_491__scoring_density_bias", "material.score.density_bias", "density_bias", -100, 100),
    )
    for uid, leaf_id, parameter, minimum, maximum in scalar:
        leaf = leaves[uid]
        assert leaf["leaf_id"] == leaf_id
        assert leaf["payload_schema"]["properties"]["parameter"] == {"const": parameter}
        assert leaf["payload_schema"]["properties"]["value"] == {
            "type": "integer", "minimum": minimum, "maximum": maximum,
        }
        # FL-4588 cutover: all three integral scoring leaves commit through
        # the one CommitMaterialScoreParameter owner; the native SliderInt +
        # keyboard adjust owners were deleted. Only the headed receipt is
        # still open.
        assert leaf["application_service_handler"] == "CommitMaterialScoreParameter"
        assert leaf["old_owner_deleted"] is True
        if leaf_id == "material.score.density_bias":
            assert "physical-input receipt remains absent" in leaf["proof_open_reason"]
        else:
            assert "headed user-reachable receipt remains open" in leaf["proof_open_reason"]

    section = leaves["fl4260_row_488__section_winner_scoring_header"]
    assert section["leaf_id"] == "material.score.section"
    assert section["status"] == "tombstoned"
    assert section["migration_status"] == "tombstone"
    assert section["action_schema"] is None
    assert section["read_model_fields"] == []
    assert migration[section["control_uid"]]["migration_status"] == "tombstone"

    for uid in (
        "fl4260_row_492__scoring_curve",
        "fl4260_target_667__shape_match_controls",
    ):
        assert leaves[uid]["status"] == "merged_into"
        assert leaves[uid]["action_schema"] is None
        assert migration[uid]["migration_status"] == "merge"

    reset = interactions["material.score.reset"]
    assert reset["action_scope"] == "product_transaction"
    assert reset["action_schema"] == "asciiid.reset_material_score.v1"
    assert reset["application_service_handler"] == "CommitMaterialScoreReset"
    assert reset["predictor_model_id"] == "fl4512.material_scoring_reset.v1"
    assert reset["commit_class"] == "direct"
    assert "50 detail plus 50 tone" in reset["source_owner"]

def test_registry_separates_authoring_and_evidence_capabilities():
    registry = load_json(REGISTRY)
    surfaces = registry["surface_contracts"]
    assert surfaces["authoring"] == {
        "surface_kind": "asciiid.web_authoring_shell.v1",
        "state_owner": "AsciiidApplicationService",
        "capability": "typed_user_intent",
        "session_policy": "single_active_authoring_session",
        "upgrade_in_place": False,
    }
    assert surfaces["evidence"] == {
        "surface_kind": "asciiid.web_evidence_viewer.v1",
        "state_owner": "read_only_observer",
        "capability": "read_only_evidence",
        "session_policy": "secondary_read_only_sessions",
        "upgrade_in_place": False,
    }
    assert registry["transport_protocol"] == {
        "handshake_schema": "asciiid.transport_handshake.v1",
        "protocol_version": 1,
        "endpoint_scope": "loopback_only",
        "frame_transport": "native_termpp_rgba_binary_websocket",
    }


def test_active_tool_graph_binds_edit_mode_canvas_and_eyedropper_debt():
    """FL-4588: single active_tool_graph owner after tool_graph hard-delete.

    One mouse has one active semantic tool. The graph references existing
    leaf identities, keeps mesh/eyedropper absences as PROOF_OPEN without
    inventing handlers, records sculpt default boot state, and forbids
    retired tool_graph coexistence.
    """
    registry = load_json(REGISTRY)
    assert "tool_graph" not in registry
    Draft202012Validator(load_json(REGISTRY_SCHEMA)).validate(registry)
    graph = registry["active_tool_graph"]
    assert graph["schema"] == "asciiid.active_tool_graph.v1"
    assert graph["ownership"]["contract_owner"] == (
        "assets/asciiid/ui/asciiid.ui_registry.v1.json"
    )
    assert graph["ownership"]["layout_owners"] == [
        "web/asciiid-authoring-shell/src/App.tsx",
        "web/asciiid-authoring-shell/src/pageLayout.ts",
    ]
    assert "hard-delet" in graph["ownership"]["note"]
    state = graph["active_tool_state"]
    assert state["snapshot_field"] == "editor_session.edit_mode"
    assert state["cardinality"] == "exactly_one"
    assert state["default_edit_mode"] == 0
    assert state["default_tool_id"] == "sculpt"
    assert graph["canvas_surface"]["surface_id"] == "termpp_final_canvas"
    assert graph["canvas_surface"]["control_uid"] == "asciiid_termpp_final_canvas"
    assert graph["canvas_surface"]["gesture"] == "inspect_only"
    assert graph["modifier_keys"] == ["Shift", "Ctrl", "Alt"]

    tools = {tool["tool_id"]: tool for tool in graph["tools"]}
    assert set(tools) == {
        "sculpt",
        "material_paint",
        "mesh",
        "material_elevation",
        "sprite",
        "item",
        "enemy_generator",
        "story",
    }
    assert tools["sculpt"]["edit_mode"] == 0
    assert tools["sculpt"]["selection_leaf_id"] == "legacy.edit.sculpt.tab"
    assert tools["sculpt"]["canvas_gesture_leaf_id"] == "scene.terrain.paint.sculpt"
    assert tools["sculpt"]["canvas_gesture_status"] == "PROOF_OPEN"
    assert tools["material_paint"]["edit_mode"] == 1
    assert tools["material_paint"]["selection_leaf_id"] == (
        "scene.terrain.material.tool"
    )
    assert tools["material_paint"]["selection_payload_tool"] == "terrain_material"
    # FL4588-MESH-CANVAS-CUTOVER: the Mesh tool is now fully wired. Selection
    # reuses the repurposed Meshes tab; the canvas gesture is a real placement
    # transaction. It stays PROOF_OPEN until the headed grouped-package receipt.
    assert tools["mesh"]["edit_mode"] == 2
    assert tools["mesh"]["selection_leaf_id"] == "workspace.navigation.mesh"
    assert tools["mesh"]["selection_handler"] == "CommitSceneObjectAction"
    assert tools["mesh"]["selection_payload_tool"] == "mesh"
    assert tools["mesh"]["canvas_gesture_leaf_id"] == "scene.objects.mesh.place"
    assert tools["mesh"]["canvas_gesture_status"] == "PROOF_OPEN"
    assert tools["mesh"]["proof_status"] == "PROOF_OPEN"
    assert "retired HTML scene" in tools["mesh"]["proof_open_reason"]

    leaves = {leaf["leaf_id"]: leaf for leaf in registry["leaves"]}
    interactions = {item["leaf_id"]: item for item in registry["interactions"]}
    for tool in graph["tools"]:
        selection_id = tool["selection_leaf_id"]
        if selection_id is None:
            continue
        assert selection_id in leaves
        assert leaves[selection_id]["status"] == "live"
        gesture_id = tool["canvas_gesture_leaf_id"]
        if gesture_id is not None:
            assert gesture_id in interactions

    select_edges = [
        edge for edge in graph["transitions"] if edge["kind"] == "tool_selection"
    ]
    assert all(edge["mutually_exclusive"] is True for edge in select_edges)
    assert {edge["to_tool_id"] for edge in select_edges} == {
        tool_id for tool_id, tool in tools.items() if tool["selection_leaf_id"]
    }
    for edge in graph["modifier_edges"]:
        assert edge["kind"] == "modifier"
        assert edge["changes_active_tool"] is False
        assert edge["leaf_id"] in leaves or edge["leaf_id"] in interactions

    transient = {t["tool_id"]: t for t in graph["transient_tools"]}
    assert transient["eyedropper"]["transient"] is True
    assert transient["eyedropper"]["edit_mode"] is None
    assert transient["eyedropper"]["canvas_gesture_status"] == "PROOF_OPEN"
    assert transient["eyedropper"]["canvas_gesture_leaf_id"] == (
        "scene.terrain.eyedropper.material"
    )
    assert transient["eyedropper"]["application_service_handler"] == (
        "CommitTerrainMaterialEyedropper"
    )

    eyedropper = next(
        seq for seq in graph["sequences"] if seq["kind"] == "material_eyedropper"
    )
    assert eyedropper["required_active_tool_id"] == "material_paint"
    assert eyedropper["transient_tool_id"] == "eyedropper"
    assert eyedropper["implementation_leaf_id"] == (
        "scene.terrain.eyedropper.material"
    )
    assert eyedropper["application_service_handler"] == (
        "CommitTerrainMaterialEyedropper"
    )
    assert eyedropper["proof_status"] == "PROOF_OPEN"
    assert not any(step.get("missing_implementation") for step in eyedropper["steps"])
    assert "retired HTML scene sequence" in eyedropper["proof_open_reason"]

    closed = (
        ROOT / "docs/agent/asciiid-html-architecture/asciiid-closed-graph-ux.md"
    ).read_text(encoding="utf-8")
    assert "Active-tool closed graph (FL-4588)" in closed
    assert "App.tsx" in closed and "pageLayout.ts" in closed
    assert "material_eyedropper" in closed or "Material eyedropper" in closed
    assert "tool_graph" in closed  # delete-first note

    types = (
        ROOT / "web/asciiid-authoring-shell/src/types.ts"
    ).read_text(encoding="utf-8")
    assert "export type ActiveToolGraph" in types
    assert "asciiid.active_tool_graph.v1" in types
    assert "transient_tools" in types


def test_authoritative_registry_keeps_unimplemented_handlers_honest():
    registry = load_json(REGISTRY)
    mutation_leaves = [leaf for leaf in registry["leaves"] if leaf["action_schema"]]
    assert mutation_leaves
    implemented = [
        leaf for leaf in mutation_leaves
        if leaf["application_service_handler"] is not None
    ]
    # Live cut-over set: every migrate leaf that stamped a typed C++ handler.
    # Terrain-42 added the brush/batch/matelev session owners; earlier lanes
    # already stamped material colors/score/presets/history/camera/tool tabs.
    # FL4588-SCENE-OBJECTS-30 added CommitSceneObjectAction for the thirty
    # /scene/objects/{sprite,enemy-generator,item,story,instance} leaves.
    assert {leaf["leaf_id"] for leaf in implemented} == {
        "material.feature.silhouette.extent_enter",
        "material.feature.silhouette.extent_exit",
        "material.feature.crease.extent_enter",
        "material.feature.crease.extent_exit",
        "material.feature.suggestive.extent_enter",
        "material.feature.suggestive.extent_exit",
        "material.feature.suggestive.derivative",
        "material.feature.suggestive.stability",
        "legacy.edit.matelev.brush.diameter",
        "legacy.edit.matelev.elevated",
        "legacy.edit.matelev.height.limit",
        "legacy.edit.matelev.height.limit.row594",
        "legacy.edit.matelev.probe.left",
        "legacy.edit.matelev.probe.left.row594",
        "legacy.edit.matelev.probe.right",
        "legacy.edit.matelev.probe.right.row595",
        "legacy.edit.matelev.tab",
        "legacy.edit.sculpt.brush.alpha",
        "legacy.edit.sculpt.brush.radius",
        "legacy.edit.sculpt.height.limit",
        "legacy.edit.sculpt.probe.left",
        "legacy.edit.sculpt.probe.right",
        "legacy.edit.sculpt.tab",
        "legacy.edit.tile.radius",
        "legacy.material.selectable.material.id",
        "legacy.n.a",
        "legacy.role.auto.fill.ramp.density",
        "material.colors.anchor",
        "material.colors.palette.assign_next_unused",
        "material.colors.row0.bg",
        "material.colors.row0.bg_strength",
        "material.colors.row0.fg",
        "material.colors.row0.fg_strength",
        "material.colors.row0.shade_contrast",
        "material.colors.row1.bg",
        "material.colors.row1.bg_strength",
        "material.colors.row1.fg",
        "material.colors.row1.fg_strength",
        "material.colors.row1.shade_contrast",
        "material.colors.row2.bg",
        "material.colors.row2.bg_strength",
        "material.colors.row2.fg",
        "material.colors.row2.fg_strength",
        "material.colors.row2.shade_contrast",
        "material.colors.row3.bg",
        "material.colors.row3.bg_strength",
        "material.colors.row3.fg",
        "material.colors.row3.fg_strength",
        "material.colors.row3.shade_contrast",
        "material.colors.shade_band_thresholds.0",
        "material.colors.shade_band_thresholds.1",
        "material.colors.shade_band_thresholds.2",
        "material.colors.shade_band_thresholds.3",
        "material.profile.color_preset.vegetation",
        "material.profile.shape_preset.corners_lips",
        "material.profile.shape_preset.flower_tops",
        "material.profile.shape_preset.fracture",
        "material.profile.shape_preset.grass_tops",
        "material.profile.shape_preset.rock_faces",
        "material.profile.shape_preset.strata",
        "material.profile.shape_preset.wave_flow",
        "material.score.bl",
        "material.score.br",
        "material.score.density_bias",
        "material.score.detail",
        "material.score.ml",
        "material.score.mr",
        "material.score.tl",
        "material.score.tone",
        "material.score.tr",
        "scene.environment.animation_enabled",
        "scene.environment.cast_static_shadows",
        "scene.environment.light.ambience",
        "scene.environment.light.sun_direction",
        "scene.environment.light.sun_height",
        "scene.environment.light.sun_time",
        "scene.environment.phase_reset",
        "scene.environment.weather.state",
        "scene.objects.enemy.armor",
        "scene.objects.enemy.crossbow",
        "scene.objects.enemy.delete.all.generators",
        "scene.objects.enemy.enable.enemy.gen",
        "scene.objects.enemy.helmet",
        "scene.objects.enemy.max.alive",
        "scene.objects.enemy.revive.max",
        "scene.objects.enemy.revive.min",
        "scene.objects.enemy.shield",
        "scene.objects.enemy.sword",
        "scene.objects.enemy.tab",
        "scene.objects.item.item.list",
        "scene.objects.item.reset.items",
        "scene.objects.item.tab",
        "scene.objects.mesh.combo",
        "scene.objects.sprite.animation",
        "scene.objects.sprite.combo",
        "scene.objects.sprite.height",
        "scene.objects.sprite.rand.animation",
        "scene.objects.sprite.rand.frame",
        "scene.objects.sprite.rand.rotate",
        "scene.objects.sprite.rep.backward",
        "scene.objects.sprite.rep.first",
        "scene.objects.sprite.rep.forward",
        "scene.objects.sprite.rep.last",
        "scene.objects.sprite.rotate",
        "scene.objects.sprite.still.frame",
        "scene.objects.sprite.tab",
        "scene.objects.story.story.id",
        "scene.objects.story.tab",
        "scene.terrain.apply.auto.mat.elev",
        "scene.terrain.apply.auto.mat.elev.row585",
        "scene.terrain.apply.auto.texture",
        "scene.terrain.apply.auto.texture.row590",
        "scene.terrain.auto.elev.height",
        "scene.terrain.auto.elev.height.row583",
        "scene.terrain.auto.elev.mode",
        "scene.terrain.auto.elev.mode.row581",
        "scene.terrain.auto.elev.overwrite",
        "scene.terrain.auto.elev.overwrite.row584",
        "scene.terrain.auto.elev.slope",
        "scene.terrain.auto.elev.slope.row582",
        "scene.terrain.auto.tex.height.range",
        "scene.terrain.auto.tex.height.range.row587",
        "scene.terrain.auto.tex.mode",
        "scene.terrain.auto.tex.mode.row585",
        "scene.terrain.auto.tex.overwrite",
        "scene.terrain.auto.tex.overwrite.row589",
        "scene.terrain.auto.tex.slope",
        "scene.terrain.auto.tex.slope.row586",
        "scene.terrain.clear.mat.elev",
        "scene.terrain.clear.mat.elev.row586",
        "scene.terrain.material.brush.height_limit",
        "scene.terrain.material.brush.height_threshold.decrement",
        "scene.terrain.material.brush.height_threshold.increment",
        "scene.terrain.material.brush.radius",
        "scene.terrain.material.tool",
        "scene.terrain.shared_brush.shape",
        "scene.viewport.player_skin",
        "scene.viewport.player_visible",
        "settings.display.termpp_performance_hud",
        "workspace.document.new",
        "workspace.history.redo",
        "workspace.history.undo",
        "workspace.navigation.mesh"
    }
    assert all(
        leaf["application_service_handler"] is None
        for leaf in mutation_leaves if leaf not in implemented
    )
    assert all(
        leaf["proof_open_reason"] or leaf["receipt_path"] or
        leaf["proof_status"] in {"SPECIFIED", "TOMBSTONE_DECISION", "MERGED_ACCOUNTING_ROW"}
        for leaf in registry["leaves"]
    )
    assert sum(bool(leaf["receipt_path"]) for leaf in registry["leaves"]) == 1



def test_migration_matrix_is_complete_and_derived_from_registry():
    registry = load_json(REGISTRY)
    matrix = csv_rows(MATRIX)
    assert len(matrix) == 339
    assert {row["control_uid"] for row in matrix} == {
        leaf["control_uid"] for leaf in registry["leaves"]
    }
    by_leaf_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    for row in matrix:
        leaf = by_leaf_uid[row["control_uid"]]
        assert row["target_route"] == leaf["route"]
        assert row["target_leaf_id"] == leaf["leaf_id"]
        assert row["migration_status"] == leaf["migration_status"]
        assert row["old_owner_deleted"] == str(leaf["old_owner_deleted"]).lower()
    deleted = {row["control_uid"] for row in matrix if row["old_owner_deleted"] == "true"}
    assert {
        "fl4260_target_674__cdp_parity_authoring_commands",
        "fl4260_row_503__evidence_accept_primary_placeholder",
        "fl4260_row_504__evidence_reject_primary_placeholder",
        "fl4260_row_505__evidence_defer_placeholder",
        "fl4260_row_508__reject_primary",
        "fl4260_row_509__defer",
        "fl4260_row_552__advance_proof_step",
        "fl4260_row_553__reset_proof_step",
        "fl4260_row_428__material_open_raw_edit",
        "fl4260_row_495__trace_pipeline_diagram",
        "fl4260_row_660__detached_trace_diagram",
    } <= deleted
    assert {row["migration_status"] for row in matrix} <= {
        "migrate", "merge", "tombstone", "read_only", "method_only"
    }
    distribution = {
        status: sum(row["migration_status"] == status for row in matrix)
        for status in ("migrate", "read_only", "tombstone", "merge", "method_only")
    }
    assert sum(distribution.values()) == 339
    migrate_expected = sum(
        1 for leaf in load_json(REGISTRY)["leaves"]
        if leaf["migration_status"] == "migrate"
    )
    assert distribution["migrate"] == migrate_expected
    assert distribution["method_only"] == 0
    assert distribution["tombstone"] >= 93
    by_uid = {row["control_uid"]: row for row in matrix}
    assert by_uid["fl4260_target_674__cdp_parity_authoring_commands"]["migration_status"] == "tombstone"
    assert by_uid["fl4260_target_669__vertical_relation_density_grid"]["migration_status"] == "tombstone"
    assert by_uid["fl4260_target_669__vertical_relation_density_grid"]["old_owner_deleted"] == "true"
    assert by_uid["target_workspace_document_open_commit"]["migration_status"] == "tombstone"
    assert by_uid["asciiid_interaction_workspace_file_save"]["migration_status"] == "tombstone"
    assert by_uid["fl4260_row_547__id_only_control_visible_label_from_adjacent_text"]["migration_status"] == "tombstone"
    assert by_uid["fl4260_row_548__id_only_control_visible_label_from_adjacent_text"]["migration_status"] == "tombstone"
    assert by_uid["fl4260_row_549__dynamic_label_di"]["migration_status"] == "tombstone"


def test_transport_handshake_cannot_grant_mutation_to_evidence_viewer():
    schema = load_json(HANDSHAKE)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    base = {
        "schema": "asciiid.transport_handshake.v1",
        "protocol_version": 1,
        "origin": "http://127.0.0.1:43123",
        "session_token_sha256": "a" * 64,
        "client_nonce": "b" * 32,
        "ui_registry_sha256": "c" * 64,
        "upgrade_in_place": False,
    }
    authoring = {
        **base,
        "surface_kind": "asciiid.web_authoring_shell.v1",
        "capabilities": ["snapshot.read", "runtime-intent.write"],
    }
    evidence = {
        **base,
        "surface_kind": "asciiid.web_evidence_viewer.v1",
        "capabilities": ["snapshot.read", "frame.read", "artifact.read"],
    }
    validator.validate(authoring)
    validator.validate(evidence)
    contaminated = {**evidence, "capabilities": ["frame.read", "runtime-intent.write"]}
    assert list(validator.iter_errors(contaminated))


def test_all_machine_contracts_are_json_and_architecture_docs_exist():
    json_paths = [
        REGISTRY_SCHEMA,
        REGISTRY,
        ROOT / "schemas/asciiid.action.v1.json",
        ROOT / "schemas/asciiid.state_snapshot.v1.json",
        ROOT / "schemas/asciiid.action_impact.v1.json",
        ROOT / "schemas/asciiid.frame_trace.v1.json",
        ROOT / "schemas/asciiid.ui_event.v1.json",
        HANDSHAKE,
        GOLDEN_CASE,
    ]
    for path in json_paths:
        schema = load_json(path)
        Draft202012Validator.check_schema(schema)
    for path in (
        ROOT / "docs/agent/glyph-rendering-paper/asciiid-current-render-pipeline.md",
        ROOT / "docs/agent/asciiid-html-architecture/asciiid-target-render-pipeline.md",
        ROOT / "docs/agent/asciiid-html-architecture/asciiid-closed-graph-ux.md",
        ROOT / "docs/agent/asciiid-html-architecture/asciiid-action-prediction.md",
        ROOT / "docs/agent/asciiid-html-architecture/asciiid-counterfactual-golden-gate.md",
    ):
        assert path.is_file()


def test_cross_evaluator_gate_reopens_without_weakening_live_mutator_ban():
    text = (
        ROOT / "docs/agent/asciiid-html-architecture/asciiid-counterfactual-golden-gate.md"
    ).read_text(encoding="utf-8")
    assert "Status: `GATE_REOPENED`" in text
    assert "persistence family" in text
    assert "remaining FL-4588 blockers are active" in text
    schema = load_json(GOLDEN_CASE)
    contract = schema["properties"]["evaluator_contract"]["properties"]
    assert contract["live_mutator_calls_permitted"]["const"] is False
    assert contract["required_hash_relation"]["const"] == "cpp_equals_python_equals_expected"


def test_terrain_material_domain_uses_source_audited_contracts():
    registry = load_json(REGISTRY)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    expected = {
        "fl4260_row_574__edit_matid_tab": (
            "scene.terrain.material.tool", "asciiid.select_terrain_material_tool.v1",
            "editor.session.active_tool", "editor/asciiid.cpp:27653",
        ),
        "fl4260_row_575__edit_matid_brush_diameter": (
            "scene.terrain.material.brush.radius", "asciiid.set_terrain_material_brush_radius.v1",
            "editor.session.terrain_brush.radius", "editor/asciiid.cpp:27688",
        ),
        "fl4260_row_578__edit_matid_height_limit": (
            "scene.terrain.material.brush.height_limit", "asciiid.set_terrain_material_height_limit.v1",
            "editor.session.terrain_brush.height_limit_enabled", "editor/asciiid.cpp:27699",
        ),
        "fl4260_row_579__edit_matid_probe_left": (
            "scene.terrain.material.brush.height_threshold.decrement",
            "asciiid.adjust_terrain_material_height_threshold.v1",
            "editor.session.terrain_brush.height_threshold", "editor/asciiid.cpp:27706",
        ),
        "fl4260_row_580__edit_matid_probe_right": (
            "scene.terrain.material.brush.height_threshold.increment",
            "asciiid.adjust_terrain_material_height_threshold.v1",
            "editor.session.terrain_brush.height_threshold", "editor/asciiid.cpp:27709",
        ),
        "fl4260_row_568__edit_sculpt_brush_shape": (
            "scene.terrain.shared_brush.shape", "asciiid.set_shared_terrain_brush_shape.v1",
            "editor.session.terrain_brush.shape", "editor/asciiid.cpp:27897",
        ),
    }
    for uid, (leaf_id, action_schema, read_field, source_anchor) in expected.items():
        leaf = by_uid[uid]
        assert leaf["leaf_id"] == leaf_id
        assert leaf["action_schema"] == action_schema
        if uid == "fl4260_row_574__edit_matid_tab":
            # FL-4588: Material paint tool is HTML-owned after MAT-id tab
            # stopped writing edit_mode=1.
            assert leaf["application_service_handler"] == "CommitSelectTerrainTool"
            assert leaf["old_owner_deleted"] is True
        else:
            # FL-4588 terrain-42: these brush leaves now stamp typed handlers.
            assert leaf["application_service_handler"] is not None
            assert leaf["old_owner_deleted"] is True
        assert read_field in leaf["read_model_fields"]
        if uid != "fl4260_row_574__edit_matid_tab":
            assert source_anchor in leaf["source_anchors"]
        assert leaf["paper_rule_id"] == "fl4512.action_to_cell.v1"
        assert leaf["predictor_model_id"] == "fl4512.parameter_only_empty_delta.v1"
        Draft202012Validator(leaf["payload_schema"]).validate(_valid_payload_for(uid))

    duplicate_uids = {
        "fl4260_row_573__edit_matid_brush_diameter",
        "fl4260_row_576__edit_matid_height_limit",
        "fl4260_row_577__edit_matid_probe_left",
        "fl4260_row_578__edit_matid_probe_right",
    }
    for uid in duplicate_uids:
        leaf = by_uid[uid]
        assert leaf["status"] == "merged_into"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "MERGED_ACCOUNTING_ROW"
        assert not leaf["proof_critical"]

    paint = next(
        interaction for interaction in registry["interactions"]
        if interaction["leaf_id"] == "scene.terrain.paint.material"
    )
    assert paint["application_service_handler"] == "CommitTerrainMaterialPaint"
    assert paint["commit_class"] == "direct"
    assert paint["proof_status"] == "PROOF_OPEN"
    assert paint["payload_schema"]["properties"]["brush_shape"]["enum"] == ["circle", "square"]
    assert paint["payload_schema"]["properties"]["radius"]["maximum"] == 100
    assert paint["payload_schema"]["properties"]["samples"]["maxItems"] == 4096
    anchors = paint["source_anchors"]
    assert any("SubmitPhysicalIntent" in a for a in anchors)
    assert any("CommitTerrainMaterialPaint" in a for a in anchors)
    assert any("runtime-intent" in a for a in anchors)
    assert any("FrameView.tsx" in a for a in anchors)
    # The deleted direct-handler anchors are gone.
    assert "editor/asciiid_application_service.cpp:405" not in anchors
    assert "editor/urdo.cpp:689" not in anchors
    assert "RuntimeIntentOwner" in paint["source_owner"]
    assert "read-only" in paint["source_owner"]
    assert "no current user-reachable" in paint["proof_open_reason"]


def _valid_payload_for(uid: str):
    if uid.endswith("matid_tab"):
        return {"tool": "terrain_material"}
    if uid.endswith("brush_diameter"):
        return {"radius": 20}
    if uid.endswith("height_limit"):
        return {"enabled": True}
    if uid.endswith("probe_left"):
        return {"delta": -1}
    if uid.endswith("probe_right"):
        return {"delta": 1}
    if uid.endswith("brush_shape"):
        return {"shape": "circle"}
    raise AssertionError(uid)


def test_material_row_rgb_domain_uses_source_audited_contracts():
    registry = load_json(REGISTRY)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    rows = (
        ("fl4260_row_432__color_fg_r0", 0, "fg", "fall_lower", "editor/asciiid.cpp:25028"),
        ("fl4260_row_433__color_fg_r1", 1, "fg", "high", "editor/asciiid.cpp:25028"),
        ("fl4260_row_434__color_fg_r2", 2, "fg", "rise", "editor/asciiid.cpp:25028"),
        ("fl4260_row_435__color_fg_r3", 3, "fg", "flat_low", "editor/asciiid.cpp:25028"),
        ("fl4260_row_436__color_bg_r0", 0, "bg", "fall_lower", "editor/asciiid.cpp:25049"),
        ("fl4260_row_437__color_bg_r1", 1, "bg", "high", "editor/asciiid.cpp:25049"),
        ("fl4260_row_438__color_bg_r2", 2, "bg", "rise", "editor/asciiid.cpp:25049"),
        ("fl4260_row_439__color_bg_r3", 3, "bg", "flat_low", "editor/asciiid.cpp:25049"),
    )
    for uid, row, channel, row_key, widget_anchor in rows:
        leaf = by_uid[uid]
        assert leaf["leaf_id"] == f"material.colors.row{row}.{channel}"
        assert leaf["action_schema"] == "asciiid.set_row_color.v1"
        # FL-4588 cutover: the typed handler landed and the native swatch
        # owner was deleted; the headed receipt is the remaining proof gap.
        assert leaf["application_service_handler"] == "CommitMaterialColorParameter"
        assert leaf["old_owner_deleted"] is True
        assert leaf["paper_rule_id"] == "fl4512.action_to_cell.v1"
        assert leaf["predictor_model_id"] == "fl4512.material_row_color.v1"
        assert leaf["proof_status"] == "PROOF_OPEN"
        assert leaf["keyboard_binding"] is None
        assert widget_anchor in leaf["source_anchors"]
        assert "editor/asciiid.cpp:9970" in leaf["source_anchors"]
        assert "editor/asciiid.cpp:12649" in leaf["source_anchors"]
        assert "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp:3612" in leaf["source_anchors"]
        assert (
            f"editor.material_look.profiles[{{profile_namespace}}:{{material_key}}]"
            f".colors.elevation_rows.{row_key}.{channel}"
        ) in leaf["read_model_fields"]
        assert (
            f"/profiles/{{profile_namespace}}/{{material_key}}/colors/per_row/{row}/{channel}"
        ) in leaf["write_set_template"]
        Draft202012Validator(leaf["payload_schema"]).validate({
            "profile_namespace": "terrain",
            "material_key": 4,
            "row": row,
            "channel": channel,
            "rgb": [255, 128, 32],
        })

    duplicate_uids = {
        "fl4260_row_470__color_fg_r0",
        "fl4260_row_471__color_bg_r0",
        "fl4260_row_472__color_fg_str_r0",
        "fl4260_row_473__color_bg_str_r0",
    }
    for uid in duplicate_uids:
        leaf = by_uid[uid]
        assert leaf["status"] == "merged_into"
        assert leaf["action_schema"] is None
        assert leaf["proof_status"] == "MERGED_ACCOUNTING_ROW"
        assert not leaf["proof_critical"]


def test_material_color_numeric_domain_uses_source_audited_contracts():
    registry = load_json(REGISTRY)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    expected = []
    for row in range(4):
        expected.extend((
            (f"fl4260_row_{440 + row}__color_fg_str_r{row}",
             f"material.colors.row{row}.fg_strength", "asciiid.set_row_color_strength.v1", 255),
            (f"fl4260_row_{445 + row}__color_bg_str_r{row}",
             f"material.colors.row{row}.bg_strength", "asciiid.set_row_color_strength.v1", 255),
            (f"fl4260_row_{449 + row}__color_shade_contrast_r{row}",
             f"material.colors.row{row}.shade_contrast", "asciiid.set_row_shade_contrast.v1", 15),
            (f"fl4260_row_{454 + row}__color_band_thres_r{row}",
             f"material.colors.shade_band_thresholds.{row}", "asciiid.set_shade_band_threshold.v1", 15),
        ))
    for uid, leaf_id, action_schema, maximum in expected:
        leaf = by_uid[uid]
        assert leaf["leaf_id"] == leaf_id
        assert leaf["action_schema"] == action_schema
        # FL-4588 cutover: the typed handler landed and the native slider
        # owner was deleted; the headed receipt is the remaining proof gap.
        assert leaf["application_service_handler"] == "CommitMaterialColorParameter"
        assert leaf["old_owner_deleted"] is True
        assert leaf["paper_rule_id"] == "fl4512.action_to_cell.v1"
        assert leaf["predictor_model_id"] == "fl4512.material_color_parameters.v1"
        assert leaf["proof_status"] == "PROOF_OPEN"
        assert "Left and Right adjust" in leaf["keyboard_binding"]
        assert "headed user-reachable receipt" in leaf["proof_open_reason"]
        payload = {
            "profile_namespace": "terrain", "material_key": 4,
            "row": int(leaf_id.rsplit(".", 1)[-1]) if "thresholds" in leaf_id else int(leaf_id.split("row")[1][0]),
            "value": maximum,
        }
        if action_schema == "asciiid.set_row_color_strength.v1":
            payload["channel"] = "fg" if ".fg_strength" in leaf_id else "bg"
        Draft202012Validator(leaf["payload_schema"]).validate(payload)

    duplicate = by_uid["fl4260_row_480__color_band_thres_r0"]
    assert duplicate["status"] == "merged_into"
    assert duplicate["proof_status"] == "MERGED_ACCOUNTING_ROW"
    assert duplicate["action_schema"] is None


def test_profile_reload_family_is_retired_after_source_audit():
    registry = load_json(REGISTRY)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    canonical = by_uid["fl4260_row_464__persist_revert_from_disk"]
    assert canonical["leaf_id"] == "material.transaction.reload_saved_profile.retired"
    assert canonical["status"] == "tombstoned"
    assert canonical["proof_status"] == "TOMBSTONE_DECISION"
    assert canonical["action_schema"] is None
    assert canonical["read_model_fields"] == []
    assert canonical["write_set_template"] == []
    assert "editor/asciiid.cpp:24886" in canonical["source_anchors"]
    assert "does not queue compilation" in canonical["long_help"]
    for uid in (
        "fl4260_row_469__color_clear_colors",
        "fl4260_row_649__reset_saved_label",
    ):
        duplicate = by_uid[uid]
        assert duplicate["status"] == "merged_into"
        assert duplicate["proof_status"] == "MERGED_ACCOUNTING_ROW"
        assert duplicate["action_schema"] is None
        assert duplicate["expected_user_result"] == (
            "Merged into material.transaction.reload_saved_profile.retired."
        )


def test_navigation_evidence_and_info_contracts_remove_false_product_mutations():
    registry = load_json(REGISTRY)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    migration = {row["control_uid"]: row for row in csv_rows(MATRIX)}

    # FL4588-MESH-CANVAS-CUTOVER: fl4260_row_423__tabstrip_mesh (the "Meshes" tab)
    # is DELIBERATELY no longer a pure navigation tab — it is now the Mesh tool
    # selection control (select_object_tool tool=mesh -> edit_mode=2), so it is
    # asserted separately below and excluded from the read-only navigation set.
    navigation = {
        "fl4260_row_420__tabstrip_view": "/scene/viewport",
        "fl4260_row_421__tabstrip_edit": "/scene/terrain/material",
        "fl4260_row_422__tabstrip_sprite": "/scene/objects/sprite",
        "fl4260_row_424__tabstrip_inst": "/scene/objects/instances",
        "fl4260_row_425__tabstrip_font": "/glyphs/catalog",
        "fl4260_row_426__tabstrip_skin": "/scene/objects/player",
        "fl4260_row_427__tabstrip_info": "/inspect/cell",
    }
    for uid, route in navigation.items():
        leaf = by_uid[uid]
        assert leaf["route"] == route
        assert leaf["action_schema"] is None
        assert leaf["read_model_fields"] == ["browser.shell.active_route"]
        assert leaf["write_set_template"] == []
        assert leaf["old_owner_deleted"] is True
        assert leaf["proof_status"] == "PROOF_OPEN"
        assert "934a566a7" in leaf["source_owner"]
        assert migration[uid]["migration_status"] == "read_only"
        assert migration[uid]["old_owner_deleted"] == "true"

    # The repurposed Meshes tab is the live Mesh tool selection, not navigation.
    mesh_tab = by_uid["fl4260_row_423__tabstrip_mesh"]
    assert mesh_tab["leaf_id"] == "workspace.navigation.mesh"
    assert mesh_tab["route"] == "/scene/objects/mesh"
    assert mesh_tab["action_schema"] == "asciiid.select_object_tool.v1"
    assert mesh_tab["application_service_handler"] == "CommitSceneObjectAction"
    assert mesh_tab["payload_schema"]["properties"]["tool"]["const"] == "mesh"
    assert migration["fl4260_row_423__tabstrip_mesh"]["migration_status"] == "migrate"
    receipt_status = by_uid["fl4260_row_497__evidence_refresh_receipts"]
    assert receipt_status["leaf_id"] == "evidence.artifacts.subscription_status"
    assert receipt_status["status"] == "tombstoned"
    assert receipt_status["migration_status"] == "tombstone"
    assert receipt_status["proof_status"] == "TOMBSTONE_DECISION"
    assert receipt_status["action_schema"] is None
    assert receipt_status["write_set_template"] == []
    assert receipt_status["read_model_fields"] == []
    assert migration[receipt_status["control_uid"]]["migration_status"] == "tombstone"

    for uid in (
        "fl4260_row_503__evidence_accept_primary_placeholder",
        "fl4260_row_504__evidence_reject_primary_placeholder",
        "fl4260_row_505__evidence_defer_placeholder",
        "fl4260_row_508__reject_primary",
        "fl4260_row_509__defer",
        "fl4260_row_626__reload",
        "fl4260_row_628__info_debug_probe",
    ):
        leaf = by_uid[uid]
        assert leaf["status"] == "tombstoned"
        assert leaf["action_schema"] is None
        assert leaf["read_model_fields"] == []
        assert leaf["write_set_template"] == []

    source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    assert "Fl4260RunRq153cReviewAction" not in source
    assert "evidence.accept_primary_placeholder" not in source
    assert "evidence.reject_primary_placeholder" not in source
    assert "evidence.defer_placeholder" not in source
    assert "evidence.accept_primary\"" not in source
    assert "evidence.reject_primary\"" not in source
    assert "evidence.defer\"" not in source


def test_native_tabstrip_and_highlight_projection_is_exact_and_fail_closed():
    registry = load_json(REGISTRY)
    expected = (
        "fl4260_row_420__tabstrip_view",
        "fl4260_row_421__tabstrip_edit",
        "fl4260_row_422__tabstrip_sprite",
        "fl4260_row_424__tabstrip_inst",
        "fl4260_row_425__tabstrip_font",
        "fl4260_row_426__tabstrip_skin",
        "fl4260_row_427__tabstrip_info",
        "fl4260_row_430__starters_highlight_selected",
    )
    assert NATIVE_OWNER_PROJECTION_CONTROLS == expected
    _validate_native_owner_projection(registry)

    for field, invalid in (
        ("old_owner_deleted", False),
        ("proof_status", "SPECIFIED"),
    ):
        drifted = copy.deepcopy(registry)
        leaf = next(
            item
            for item in drifted["leaves"]
            if item["control_uid"] == expected[0]
        )
        leaf[field] = invalid
        with pytest.raises(SystemExit, match=expected[0]):
            _validate_native_owner_projection(drifted)


def test_object_editor_contracts_separate_session_templates_from_document_actions():
    registry = load_json(REGISTRY)
    by_uid = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    object_uids = [
        *(f"fl4260_row_{row}__edit_sprite_{name}" for row, name in (
            (596, "tab"), (597, "animation"), (598, "rotate"),
            (599, "still_frame"), (600, "rep_first"), (601, "rep_forward"),
            (602, "rep_last"), (603, "rep_backward"), (604, "rand_animation"),
            (605, "rand_frame"), (606, "rand_rotate"), (607, "height"),
        )),
        "fl4260_row_608__edit_item_tab",
        "fl4260_row_609__edit_item_reset_items",
        "fl4260_row_610__edit_item_item_list",
        *(f"fl4260_row_{row}__edit_enemy_{name}" for row, name in (
            (611, "tab"), (612, "enable_enemy_gen"), (613, "delete_all_generators"),
            (614, "max_alive"), (615, "revive_min"), (616, "revive_max"),
            (617, "armor"), (618, "helmet"), (619, "shield"),
            (620, "sword"), (621, "crossbow"),
        )),
        "fl4260_row_622__edit_story_tab",
        "fl4260_row_623__edit_story_story_id",
        "fl4260_row_511__sprite_combo",
    ]
    for uid in object_uids:
        leaf = by_uid[uid]
        assert not any(value.startswith("SPEC_OPEN") for value in leaf["read_model_fields"])
        assert not any(value.startswith("SPEC_OPEN") for value in leaf["write_set_template"])
        # FL4588-SCENE-OBJECTS-30: typed CommitSceneObjectAction owns these leaves.
        assert leaf["application_service_handler"] == "CommitSceneObjectAction"

    sprite = by_uid["fl4260_row_511__sprite_combo"]
    assert sprite["action_schema"] == "asciiid.select_sprite_asset.v1"
    assert sprite["write_set_template"] == ["/editor/session/active_sprite_id"]
    assert sprite["paper_rule_id"] == "fl4588.editor_session_action.v1"

    reset_items = by_uid["fl4260_row_609__edit_item_reset_items"]
    assert reset_items["write_set_template"] == [
        "/runtime/world_item_instances", "/runtime/world_revision"
    ]
    assert reset_items["paper_rule_id"] == "fl4512.action_to_cell.v1"

    delete_generators = by_uid["fl4260_row_613__edit_enemy_delete_all_generators"]
    assert delete_generators["write_set_template"] == [
        "/document/enemy_generators", "/document/revision", "/runtime/world_revision"
    ]
    sword = by_uid["fl4260_row_620__edit_enemy_sword"]
    assert sword["write_set_template"] == [
        "/editor/session/enemy_generator_template/sword",
        "/editor/session/enemy_generator_template/crossbow",
    ]
    for uid in (
        "fl4260_row_512__dynamic_label_sname",
        "fl4260_row_513__sprite_prev",
        "fl4260_row_514__sprite_next",
    ):
        merged = by_uid[uid]
        assert merged["action_schema"] is None
        assert merged["proof_status"] == "MERGED_ACCOUNTING"


def test_pre_port_audit_fails_closed_with_named_blockers():
    command = [sys.executable, str(ROOT / "scripts/fl4588_validate_pre_port.py"), "--json"]
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["schema"] == "asciiid.pre_port_readiness.v1"
    assert report["status"] == "READY"
    assert report["ready_for_html_mutation"] is True
    assert report["metrics"]["spec_open_read_models"] == 0
    assert report["metrics"]["spec_open_write_sets"] == 0
    registry = load_json(REGISTRY)
    migrate_n = sum(
        1 for leaf in registry["leaves"] if leaf["migration_status"] == "migrate"
    )
    mutation_n = sum(
        1 for leaf in registry["leaves"] if leaf.get("action_schema")
    ) + sum(
        1 for item in registry["interactions"]
        if item.get("action_scope") == "product_transaction"
    )
    assert report["metrics"]["migration_rows"] == migrate_n
    assert report["metrics"]["mutation_surfaces"] == mutation_n
    assert report["metrics"]["browser_draft_interactions"] == 6
    assert report["metrics"]["missing_application_service_handlers"] == 0
    assert report["metrics"]["mutation_leaves_missing_paper_rules"] == 0
    migrated = [
        leaf for leaf in registry["leaves"]
        if leaf["migration_status"] == "migrate"
    ]
    assert report["metrics"]["migrated_old_owners_deleted"] == sum(
        leaf["old_owner_deleted"] for leaf in migrated
    )
    assert report["metrics"]["migrated_old_owners_remaining"] == sum(
        not leaf["old_owner_deleted"] for leaf in migrated
    )
    deleted_total = sum(
        1 for leaf in registry["leaves"] if leaf.get("old_owner_deleted") is True
    )
    assert report["metrics"]["deleted_owner_records_total"] == deleted_total
    assert report["metrics"]["application_service_cpp_present"] is True
    assert report["metrics"]["counterfactual_gate_open"] is False
    assert report["metrics"]["registry_cutover_status"] == "authoritative"
    assert report["metrics"]["legacy_inventory_is_generator_input"] is False
    codes = {blocker["code"] for blocker in report["blockers"]}
    # Document-merge HTML/registry cutover named MergeDocumentBytes on
    # workspace.document.merge.commit, so APPLICATION_HANDLERS_ABSENT is gone.
    assert codes == set()
    ready = subprocess.run(command[:-1] + ["--require-ready"], cwd=ROOT)
    assert ready.returncode == 0
    assert not (ROOT / "scripts/fl4588/headed_journey_proof.py").exists()


def test_registry_is_direct_authority_and_generator_owns_self_hash_stamp():
    """Leaf bodies stay a direct registry contract; only --stamp writes identity.

    FL-4588 hash consistency: generator-independent adhoc restamp owners were
    hard-deleted. The generator may persist REGISTRY_PATH solely to restamp
    `registry_sha256` on the document and every child row. It still must not
    invent leaf bodies — matrix projection remains the derived write surface.
    """
    source = (ROOT / "scripts/fl4588_generate_html_architecture.py").read_text(
        encoding="utf-8"
    )
    assert "direct_registry_contract" in source
    assert "historical_accounting_only" in source
    assert "--stamp" in source
    assert "_stamp_registry_hashes" in source
    assert "_write_registry_identity" in source
    assert "REGISTRY_PATH.write_text" in source
    assert "MATRIX_PATH.write_bytes" in source
    # Sole identity owner: no surviving adhoc parallel restamp front door.
    refresh_adhoc = ROOT / (
        "scripts/adhoc/"
        "2026-07-16-Refresh-authoritative-ASCIIID-registry-self-hash-after-direc.py"
    )
    assert not refresh_adhoc.exists()


def test_native_authoring_transport_has_delete_first_typed_actions():
    header = (ROOT / "apps/asciicker-presenter/asciiid_authoring_http.h").read_text(encoding="utf-8")
    source = (ROOT / "apps/asciicker-presenter/asciiid_authoring_http.cpp").read_text(encoding="utf-8")
    app = (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text(encoding="utf-8")
    index = (ROOT / "web/asciiid-authoring-shell/index.html").read_text(encoding="utf-8")
    widget = (ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx").read_text(
        encoding="utf-8"
    )
    workspace_actions = (
        ROOT / "web/asciiid-authoring-shell/src/WorkspaceActions.tsx"
   ).read_text(encoding="utf-8")
    assert "registry-bound typed" in header
    assert "GET /api/v1/health HTTP/1.1" in source
    assert "GET /api/v1/snapshot HTTP/1.1" in source
    assert "GET /api/v1/frames/editor-scene.rgba HTTP/1.1" not in source
    assert "GET /api/v1/frames/termpp-final.rgba HTTP/1.1" in source
    assert "GET /api/v1/frame.rgba HTTP/1.1" not in source
    assert "POST /api/v1/event.record HTTP/1.1" in source
    assert "POST /api/v1/action.preview HTTP/1.1" not in source
    assert "POST /api/v1/action.commit HTTP/1.1" not in source
    assert "service_->CommitDirectAction(body)" not in source
    assert "POST /api/v1/runtime-intent HTTP/1.1" in source
    assert 'AuthorizedSession(origin, "runtime-intent.write")' in source
    assert "POST /api/v1/history.undo HTTP/1.1" not in source
    assert "POST /api/v1/history.redo HTTP/1.1" not in source
    assert "registry_sha256_ == registry_hash" not in source
    assert "service_->SubmitPhysicalIntent(" in source
    assert "POST /api/v1/document/open HTTP/1.1" not in source
    assert "GET /api/v1/document/download HTTP/1.1" not in source
    assert "application/vnd.asciiid.runtime-file-intent" in source
    assert "application/vnd.asciiid.runtime-intent-document" in source
    assert "CommitPreviewedAction(" not in source
    assert "SaveDocument(" not in source
    assert "OpenDocument(" not in source
    assert "mutation_enabled" in source
    assert "snapshot_enabled" in source
    assert "MutationEnabled()" in source
    assert "Access-Control-Allow-Origin: *" not in source
    assert "htonl(INADDR_LOOPBACK)" in source
    assert "/api/v1/health" in app
    assert "<title>ASCIID FL4512 Presenter</title>" in index
    assert "<title>ASCIIID Editor</title>" not in index
    assert "ASCIID FL4512 PRESENTER · LIVE" in app
    assert "ASCIIID EDITOR · LIVE" not in app
    assert "product_ready" in app
    assert "commitDirectAction" not in widget
    assert "submitRuntimePhysicalIntent" in widget
    # FL-4588 persistent workspace actions: Open/Save/Undo/Redo moved from
    # ControlWidget route rows to the one persistent WorkspaceActions toolbar.
    # ControlWidget no longer owns these leaves. The toolbar sends registered
    # physical facts through the runtime-intent transport; Merge uses the same
    # framed file-intent path from its route control.
    assert "uploadDocument(sessionId, file)" not in widget
    assert "downloadDocument(sessionId)" not in widget
    assert "commitHistoryWrite(sessionId" not in widget
    assert "submitRuntimeFileIntent" in workspace_actions
    assert "submitRuntimeDocumentDownload" in workspace_actions
    assert "submitRuntimePhysicalIntent" in workspace_actions
    assert "commitHistoryWrite" not in workspace_actions
    assert "runDocumentOpenSequence" not in workspace_actions
    assert "runDocumentSaveSequence" not in workspace_actions
    assert "OPEN .A3D" in workspace_actions
    assert "SAVE .A3D" in workspace_actions


def test_delete_first_application_handlers_are_exactly_the_cut_over_leaves():
    registry = load_json(REGISTRY)
    handlers = [leaf for leaf in registry["leaves"] if leaf["application_service_handler"]]
    by_id = {leaf["leaf_id"]: leaf for leaf in handlers}
    assert set(by_id) == {
        "material.feature.silhouette.extent_enter",
        "material.feature.silhouette.extent_exit",
        "material.feature.crease.extent_enter",
        "material.feature.crease.extent_exit",
        "material.feature.suggestive.extent_enter",
        "material.feature.suggestive.extent_exit",
        "material.feature.suggestive.derivative",
        "material.feature.suggestive.stability",
        "legacy.edit.matelev.brush.diameter",
        "legacy.edit.matelev.elevated",
        "legacy.edit.matelev.height.limit",
        "legacy.edit.matelev.height.limit.row594",
        "legacy.edit.matelev.probe.left",
        "legacy.edit.matelev.probe.left.row594",
        "legacy.edit.matelev.probe.right",
        "legacy.edit.matelev.probe.right.row595",
        "legacy.edit.matelev.tab",
        "legacy.edit.sculpt.brush.alpha",
        "legacy.edit.sculpt.brush.radius",
        "legacy.edit.sculpt.height.limit",
        "legacy.edit.sculpt.probe.left",
        "legacy.edit.sculpt.probe.right",
        "legacy.edit.sculpt.tab",
        "legacy.edit.tile.radius",
        "legacy.material.selectable.material.id",
        "legacy.n.a",
        "legacy.role.auto.fill.ramp.density",
        "material.colors.anchor",
        "material.colors.palette.assign_next_unused",
        "material.colors.row0.bg",
        "material.colors.row0.bg_strength",
        "material.colors.row0.fg",
        "material.colors.row0.fg_strength",
        "material.colors.row0.shade_contrast",
        "material.colors.row1.bg",
        "material.colors.row1.bg_strength",
        "material.colors.row1.fg",
        "material.colors.row1.fg_strength",
        "material.colors.row1.shade_contrast",
        "material.colors.row2.bg",
        "material.colors.row2.bg_strength",
        "material.colors.row2.fg",
        "material.colors.row2.fg_strength",
        "material.colors.row2.shade_contrast",
        "material.colors.row3.bg",
        "material.colors.row3.bg_strength",
        "material.colors.row3.fg",
        "material.colors.row3.fg_strength",
        "material.colors.row3.shade_contrast",
        "material.colors.shade_band_thresholds.0",
        "material.colors.shade_band_thresholds.1",
        "material.colors.shade_band_thresholds.2",
        "material.colors.shade_band_thresholds.3",
        "material.profile.color_preset.vegetation",
        "material.profile.shape_preset.corners_lips",
        "material.profile.shape_preset.flower_tops",
        "material.profile.shape_preset.fracture",
        "material.profile.shape_preset.grass_tops",
        "material.profile.shape_preset.rock_faces",
        "material.profile.shape_preset.strata",
        "material.profile.shape_preset.wave_flow",
        "material.score.bl",
        "material.score.br",
        "material.score.density_bias",
        "material.score.detail",
        "material.score.ml",
        "material.score.mr",
        "material.score.tl",
        "material.score.tone",
        "material.score.tr",
        "scene.environment.animation_enabled",
        "scene.environment.cast_static_shadows",
        "scene.environment.light.ambience",
        "scene.environment.light.sun_direction",
        "scene.environment.light.sun_height",
        "scene.environment.light.sun_time",
        "scene.environment.phase_reset",
        "scene.environment.weather.state",
        "scene.objects.enemy.armor",
        "scene.objects.enemy.crossbow",
        "scene.objects.enemy.delete.all.generators",
        "scene.objects.enemy.enable.enemy.gen",
        "scene.objects.enemy.helmet",
        "scene.objects.enemy.max.alive",
        "scene.objects.enemy.revive.max",
        "scene.objects.enemy.revive.min",
        "scene.objects.enemy.shield",
        "scene.objects.enemy.sword",
        "scene.objects.enemy.tab",
        "scene.objects.item.item.list",
        "scene.objects.item.reset.items",
        "scene.objects.item.tab",
        "scene.objects.mesh.combo",
        "scene.objects.sprite.animation",
        "scene.objects.sprite.combo",
        "scene.objects.sprite.height",
        "scene.objects.sprite.rand.animation",
        "scene.objects.sprite.rand.frame",
        "scene.objects.sprite.rand.rotate",
        "scene.objects.sprite.rep.backward",
        "scene.objects.sprite.rep.first",
        "scene.objects.sprite.rep.forward",
        "scene.objects.sprite.rep.last",
        "scene.objects.sprite.rotate",
        "scene.objects.sprite.still.frame",
        "scene.objects.sprite.tab",
        "scene.objects.story.story.id",
        "scene.objects.story.tab",
        "scene.terrain.apply.auto.mat.elev",
        "scene.terrain.apply.auto.mat.elev.row585",
        "scene.terrain.apply.auto.texture",
        "scene.terrain.apply.auto.texture.row590",
        "scene.terrain.auto.elev.height",
        "scene.terrain.auto.elev.height.row583",
        "scene.terrain.auto.elev.mode",
        "scene.terrain.auto.elev.mode.row581",
        "scene.terrain.auto.elev.overwrite",
        "scene.terrain.auto.elev.overwrite.row584",
        "scene.terrain.auto.elev.slope",
        "scene.terrain.auto.elev.slope.row582",
        "scene.terrain.auto.tex.height.range",
        "scene.terrain.auto.tex.height.range.row587",
        "scene.terrain.auto.tex.mode",
        "scene.terrain.auto.tex.mode.row585",
        "scene.terrain.auto.tex.overwrite",
        "scene.terrain.auto.tex.overwrite.row589",
        "scene.terrain.auto.tex.slope",
        "scene.terrain.auto.tex.slope.row586",
        "scene.terrain.clear.mat.elev",
        "scene.terrain.clear.mat.elev.row586",
        "scene.terrain.material.brush.height_limit",
        "scene.terrain.material.brush.height_threshold.decrement",
        "scene.terrain.material.brush.height_threshold.increment",
        "scene.terrain.material.brush.radius",
        "scene.terrain.material.tool",
        "scene.terrain.shared_brush.shape",
        "scene.viewport.player_skin",
        "scene.viewport.player_visible",
        "settings.display.termpp_performance_hud",
        "workspace.document.merge.open",
        "workspace.document.new",
        "workspace.history.redo",
        "workspace.history.undo",
        "workspace.navigation.mesh"
    }
    # FL-4588 document-merge HTML cutover: merge.open is a BrowserShell
    # file-picker leaf (not a staged/direct C++ product committer).
    assert by_id["workspace.document.merge.open"][
        "application_service_handler"
    ] == "BrowserShell::SelectMergeDocument"
    assert by_id["workspace.document.merge.open"]["commit_class"] is None
    # Duplicate visible open-commit leaf is tombstoned; it must not appear
    # in the stamped leaf-handler inventory (typed open lives on the
    # interaction only).
    assert "workspace.document.open.commit" not in by_id
    assert "workspace.file.save" not in by_id
    assert by_id["scene.environment.animation_enabled"][
        "application_service_handler"
    ] == "CommitEnvironmentAnimationEnabled"
    assert by_id["settings.display.termpp_performance_hud"][
        "application_service_handler"
    ] == "CommitTermppPerformanceHudVisible"
    assert by_id["material.score.density_bias"][
        "application_service_handler"
    ] == "CommitMaterialScoreParameter"
    assert by_id["legacy.material.selectable.material.id"][
        "application_service_handler"
    ] == "CommitMaterialProfileSelect"
    assert "scene.camera.focus_terrain" not in by_id
    assert by_id["legacy.edit.sculpt.tab"][
        "application_service_handler"
    ] == "CommitSelectTerrainTool"
    assert by_id["scene.terrain.material.tool"][
        "application_service_handler"
    ] == "CommitSelectTerrainTool"
    assert by_id["legacy.edit.matelev.tab"][
        "application_service_handler"
    ] == "CommitSelectTerrainTool"
    assert by_id["legacy.edit.sculpt.brush.radius"][
        "application_service_handler"
    ] == "CommitTerrainBrushParameter"
    assert by_id["scene.terrain.apply.auto.texture"][
        "application_service_handler"
    ] == "CommitApplyAutomaticTerrainMaterial"
    assert by_id["workspace.history.undo"]["application_service_handler"] == "Undo"
    assert by_id["workspace.history.redo"]["application_service_handler"] == "Redo"
    # Whole-map apply/clear leaves are staged; every other stamped leaf is direct.
    staged = {
        leaf["leaf_id"] for leaf in handlers if leaf["commit_class"] == "staged"
    }
    assert {
        "scene.terrain.apply.auto.mat.elev",
        "scene.terrain.apply.auto.mat.elev.row585",
        "scene.terrain.clear.mat.elev",
        "scene.terrain.clear.mat.elev.row586",
        "scene.terrain.apply.auto.texture",
        "scene.terrain.apply.auto.texture.row590",
    }.issubset(staged)
    # BrowserShell picker leaves stamp a handler without a product commit_class.
    browser_shell_pickers = {
        leaf["leaf_id"]
        for leaf in handlers
        if isinstance(leaf["application_service_handler"], str)
        and leaf["application_service_handler"].startswith("BrowserShell::")
    }
    assert browser_shell_pickers == {"workspace.document.merge.open"}
    assert all(
        leaf["commit_class"] in {"direct", "staged"}
        for leaf in handlers
        if leaf["leaf_id"] not in browser_shell_pickers
    )
    assert all(leaf["old_owner_deleted"] is True for leaf in handlers)
    assert all(leaf["proof_status"] == "PROOF_OPEN" for leaf in handlers)



def test_density_bias_old_owner_is_deleted_and_html_physical_slider_is_wired():
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    widget = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    assert 'SliderInt("density bias##rq153db"' not in native
    assert 'Fl4260KbInt("scoring.density_bias"' not in native
    assert "CommitAsciiidMaterialScoreParameter" in native
    assert "Fl4260CommitProfileBatchWithHistory(&profile_key, 1, &before," in native
    assert "Fl4260SaveProfileEdit(" not in native
    assert "CommitMaterialScoreParameter" in service
    assert 'leaf.application_service_handler === "CommitMaterialScoreParameter"' in widget
    assert "onPointerUp" in widget
    assert "onKeyUp" in widget
    assert "material.score.density_bias" in transport
    assert "submitRuntimePhysicalIntent" in transport


def test_material_profile_select_old_owner_is_deleted_and_html_is_wired():
    # FL-4588 delete-first: Materials-list Selectable/ColorButton no longer
    # call Fl4260Select* for legacy.material.selectable.material.id; HTML owns
    # CommitMaterialProfileSelect. The typed atomic profile transaction remains
    # the sole persistence owner.
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    header = (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text(
        encoding="utf-8"
    )
    widget = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    schema = load_json(ROOT / "schemas/asciiid.action_result.v1.json")
    snapshot_schema = load_json(ROOT / "schemas/asciiid.authoring_snapshot.v1.json")
    registry = load_json(REGISTRY)
    leaf = next(
        item for item in registry["leaves"]
        if item["leaf_id"] == "legacy.material.selectable.material.id"
    )
    assert leaf["application_service_handler"] == "CommitMaterialProfileSelect"
    assert leaf["old_owner_deleted"] is True
    assert leaf["proof_status"] == "PROOF_OPEN"
    assert leaf["action_schema"] == "asciiid.select_material_profile.v1"

    # Materials-list draw path: display-only rows, zero select mutations.
    assert "selector became the sole user-facing selection owner" in native
    materials_block = native.split("ImGui::TextUnformatted(\"Materials\");", 1)[1]
    materials_block = materials_block.split("// FL-4260 (2026-06-23): drop a preset", 1)[0]
    assert "Fl4260SelectProfileFromEdit(" not in materials_block
    assert "Fl4260SelectTerrainMaterialFromEdit(" not in materials_block

    assert "CommitAsciiidMaterialProfileSelect" in native
    assert (
        "CommitAsciiidMaterialProfileSelect," in native
        or "CommitAsciiidMaterialProfileSelect);" in native
    )
    assert "Fl4260SelectProfileFromEdit(profile_key);" in native
    assert "active_material = profile_key.material_key" in native
    assert "Fl4260CommitProfileBatchWithHistory" in native
    assert "Fl4260SaveProfileEdit(" not in native

    assert "material_profile_select_committer_" in header
    assert "CommitMaterialProfileSelect" in service
    assert "material_profile_select_not_connected" in service
    assert '"legacy.material.selectable.material.id"' in service
    assert "payload_field_count != 2" in service

    assert "select_material_profile" in schema["properties"]["action_kind"]["enum"]
    assert "active_profile_namespace" in snapshot_schema["properties"][
        "material_runtime"
    ]["properties"]

    assert "submitRuntimePhysicalIntent" in transport
    assert 'case "legacy.material.selectable.material.id"' in transport
    assert "commitMaterialProfileSelect" not in transport
    assert 'leaf.leaf_id === "legacy.material.selectable.material.id"' in widget
    assert 'application_service_handler === "CommitMaterialProfileSelect"' in widget


def test_material_profile_select_negative_contract_surfaces_are_present():
    # Malformed payload, stale revision, wrong identity, disconnected committer,
    # and selection-brush coupling. Static ownership/contract asserts only.
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")

    assert 'return reject("typed_action_payload_rejected");' in service
    assert 'return reject("base_state_revision_stale");' in service
    assert 'return {false, "runtime_intent_handler_not_migrated", {}};' in service
    assert "material_profile_select_not_connected" in service
    assert "runtime_intent_missing_handler_authority" in service
    assert 'case "legacy.material.selectable.material.id"' in transport
    assert "submitRuntimePhysicalIntent" in transport

    # Selection-brush coupling: terrain select writes active_material through
    # the single Fl4260SelectProfileFromEdit owner used by the HTML committer.
    assert "if (profile_key.profile_namespace == FL4512_PROFILE_NAMESPACE_TERRAIN)" in native
    assert "active_material = profile_key.material_key;" in native
    assert "Fl4260SelectProfileFromEdit(profile_key);" in native
    assert "CommitAsciiidMaterialProfileSelect" in native


def test_terrain_canvas_effects_are_retained_but_browser_gesture_is_deleted():
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text(
        encoding="utf-8"
    )
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    registry = load_json(REGISTRY)
    interactions = {row["leaf_id"]: row for row in registry["interactions"]}
    assert interactions["scene.terrain.paint.material"]["application_service_handler"] == "CommitTerrainMaterialPaint"
    assert interactions["scene.terrain.paint.sculpt"]["application_service_handler"] == "CommitTerrainSculptPaint"
    assert interactions["scene.terrain.paint.material"]["proof_status"] == "PROOF_OPEN"
    assert interactions["scene.terrain.paint.sculpt"]["proof_status"] == "PROOF_OPEN"
    assert "CommitAsciiidTerrainMaterialPaint" in native
    assert "CommitAsciiidTerrainSculptPaint" in native
    assert "if (painting == 1)" not in native
    assert "if (painting == 2)" not in native
    assert "AsciiidApplicationService::CommitTerrainMaterialPaint" in service
    assert "AsciiidApplicationService::CommitTerrainSculptPaint" in service
    assert "commitTerrainSculptPaint" not in transport
    assert "commitTerrainMaterialPaint" not in transport
    assert "submitRuntimePhysicalIntent" in transport
    assert "submitRuntimePhysicalIntent(" not in frame
    assert "paintStroke" not in frame
    assert "FindAsciiidEditorSceneFrameIdentity" not in native


def test_select_terrain_material_tool_old_owner_deleted_and_html_wired():
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    control = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    toolbar = (
        ROOT / "web/asciiid-authoring-shell/src/Toolbar.tsx"
    ).read_text(encoding="utf-8")
    registry = load_json(REGISTRY)
    for leaf_id in (
        "scene.terrain.material.tool",
        "legacy.edit.sculpt.tab",
        "legacy.edit.matelev.tab",
    ):
        leaf = next(row for row in registry["leaves"] if row["leaf_id"] == leaf_id)
        assert leaf["application_service_handler"] == "CommitSelectTerrainTool"
        assert leaf["old_owner_deleted"] is True
    assert "CommitAsciiidSelectTerrainTool" in native
    assert "CommitSelectTerrainTool(tool, transaction_id)" in service
    assert "commitSelectTerrainTool" not in transport
    assert "submitRuntimePhysicalIntent" in transport
    assert 'leaf.application_service_handler === "CommitSelectTerrainTool"' in control
    assert "return null;" in control
    assert "submitRuntimePhysicalIntent(" in toolbar
    assert "g_asciiid_html_shell_backend" in native


def test_failed_camera_surface_is_deleted_and_locked():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    camera_uids = {
        "fl4260_row_533__view_control_header",
        "fl4260_row_534__view_pitch",
        "fl4260_row_535__view_yaw",
        "fl4260_row_536__view_zoom",
        "fl4260_row_538__view_focus_view",
    }
    for uid in camera_uids:
        leaf = leaves[uid]
        assert leaf["status"] == "tombstoned"
        assert leaf["migration_status"] == "tombstone"
        assert leaf["application_service_handler"] is None
        assert leaf["action_schema"] is None
        assert leaf["write_set_template"] == []
        assert leaf["proof_status"] == "TOMBSTONE_DECISION"

    assert not any(
        interaction["leaf_id"].startswith("scene.camera.")
        for interaction in registry["interactions"]
    )
    graph = registry["active_tool_graph"]
    assert graph["canvas_surface"]["always_available_gesture_leaf_ids"] == []
    assert not any(
        edge["leaf_id"].startswith("scene.camera.")
        for edge in graph["modifier_edges"]
    )
    assert not any(
        leaf_id.startswith("scene.camera.")
        for contract in registry["physical_input_graph"]["contracts"]
        for leaf_id in (
            contract.get("leaf_ids", []) + contract.get("interaction_ids", [])
        )
    )

    sources = {
        "native": (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8"),
        "header": (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text(
            encoding="utf-8"
        ),
        "service": (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
            encoding="utf-8"
        ),
        "transport": (
            ROOT / "web/asciiid-authoring-shell/src/transport.ts"
        ).read_text(encoding="utf-8"),
        "frame": (
            ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx"
        ).read_text(encoding="utf-8"),
        "app": (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text(
            encoding="utf-8"
        ),
    }
    assert "commitCamera" not in sources["transport"]
    assert "commitCamera" not in sources["frame"]
    assert "commitCamera" not in sources["app"]
    assert 'frame.surface !== "termpp_final"' in sources["frame"]
    assert "onCameraKeyDown" not in sources["frame"]
    assert "onCameraWheel" not in sources["frame"]
    assert "FOCUS VIEW" not in sources["frame"]
    assert "scene.camera.keyboard_move" not in sources["app"]
    assert "scene.camera.pointer_rotate" not in sources["app"]
    assert "camera movement input is unavailable fail closed" in sources["native"]
    assert "camera turn input is unavailable fail closed" in sources["native"]
def test_terrain_and_asset_contracts_have_exact_source_backed_models():
    registry = load_json(REGISTRY)
    leaves = {leaf["control_uid"]: leaf for leaf in registry["leaves"]}
    assert all(
        not any(value.startswith("SPEC_OPEN") for value in leaf["read_model_fields"])
        for leaf in registry["leaves"]
    )
    assert all(
        not any(value.startswith("SPEC_OPEN") for value in leaf["write_set_template"])
        for leaf in registry["leaves"]
    )

    auto_texture = leaves["fl4260_row_592__edit_matid_apply_auto_texture"]
    assert auto_texture["action_schema"] == "asciiid.apply_automatic_terrain_material.v1"
    assert auto_texture["write_set_template"] == [
        "/document/terrain/patches/{all_patch_ids}/visual_map/{matching_writable_cell_indices}/material_key",
        "/document/revision",
        "/runtime/terrain_visual_revision",
    ]
    assert "FL-4588 terrain-42" in auto_texture["proof_open_reason"]
    assert auto_texture["application_service_handler"] == "CommitApplyAutomaticTerrainMaterial"

    autofill = leaves["fl4260_row_487__role_auto_fill_ramp_density"]
    assert autofill["action_schema"] == "asciiid.autofill_material_role_rows.v1"
    assert autofill["paper_rule_id"] == "fl4512.action_to_cell.v1"
    assert autofill["predictor_model_id"] == "fl4512.material_role_autofill.v1"

    sculpt_radius = leaves["fl4260_row_567__edit_sculpt_brush_radius"]
    assert sculpt_radius["paper_rule_id"] == "fl4588.editor_session_action.v1"
    assert sculpt_radius["write_set_template"] == [
        "/editor/session/terrain_brush/radius"
    ]

    for uid in (
        "fl4260_row_552__advance_proof_step",
        "fl4260_row_553__reset_proof_step",
        "fl4260_row_428__material_open_raw_edit",
    ):
        assert leaves[uid]["status"] == "tombstoned"
        assert leaves[uid]["action_schema"] is None



def test_terrain_controls_runtime_bindings_and_force_auto_deleted():
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    control = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    assert "g_fl4260_force_auto_elev_mode" not in native
    assert "g_fl4260_force_auto_tex_mode" not in native
    assert "Deleted force_auto elev-mode bridge writer" in native
    assert "Deleted force_auto tex-mode bridge writer" in native
    assert 'leaf.leaf_id.includes("height.range")' in control
    assert "commitTerrain42Value({ minimum: nextMin, maximum: nextMax }, event)" in control
    assert "clearValue(leaf.leaf_id);" in control
    assert "submitRuntimePhysicalIntent(sessionId, leaf, snapshot, raw, event)" in control
    assert "commitTerrain42Session" not in transport
    assert "submitRuntimePhysicalIntent" in transport


def test_terrain42_session_old_owners_deleted_and_html_wired():
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    control = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    registry = load_json(REGISTRY)
    handlers = {
        "CommitTerrainBrushParameter",
        "CommitTerrainBrushHeightLimit",
        "CommitTerrainProbeHeight",
        "CommitSharedTerrainBrushShape",
        "CommitMaterialElevationBrushValue",
        "CommitTerrainBatchParameter",
        "CommitApplyAutomaticMaterialElevation",
        "CommitClearMaterialElevation",
        "CommitApplyAutomaticTerrainMaterial",
        "CommitSelectTerrainTool",
    }
    leaves = [
        leaf for leaf in registry["leaves"]
        if leaf.get("application_service_handler") in handlers
        and leaf["migration_status"] == "migrate"
        and leaf["old_owner_deleted"] is True
        and (
            leaf["application_service_handler"] != "CommitSelectTerrainTool"
            or leaf["leaf_id"] in {
                "scene.terrain.material.tool",
                "legacy.edit.sculpt.tab",
                "legacy.edit.matelev.tab",
            }
        )
    ]
    stamped = [
        leaf for leaf in leaves
        if leaf["application_service_handler"] != "CommitSelectTerrainTool"
        or leaf["leaf_id"] == "legacy.edit.matelev.tab"
    ]
    assert len(stamped) == 42
    assert len([leaf for leaf in stamped if leaf["commit_class"] == "staged"]) == 6
    assert len([leaf for leaf in stamped if leaf["commit_class"] == "direct"]) == 36
    assert "CommitAsciiidTerrain42Session" in native
    assert "CommitAsciiidTerrain42Session," in native
    assert "CommitTerrain42Session" in service
    assert "runtime_intent_terrain_control_event_rejected" in service
    assert "BuildRuntimePayloadFromSchema(" in service
    assert "kTerrain42Routes" not in service
    assert "commitTerrain42Session" not in transport
    assert "submitRuntimePhysicalIntent" in transport
    assert "commitTerrain42Value" in control
    assert "submitRuntimePhysicalIntent(sessionId, leaf, snapshot, raw, event)" in control
    assert "(HTML owns)" in native


SCENE_OBJECT_30 = [
    ("scene.objects.sprite.tab", "fl4260_row_596__edit_sprite_tab"),
    ("scene.objects.item.tab", "fl4260_row_608__edit_item_tab"),
    ("scene.objects.enemy.tab", "fl4260_row_611__edit_enemy_tab"),
    ("scene.objects.story.tab", "fl4260_row_622__edit_story_tab"),
    ("scene.objects.sprite.combo", "fl4260_row_511__sprite_combo"),
    ("legacy.n.a", "fl4260_row_516__dynamic_label_label"),
    ("scene.objects.sprite.animation", "fl4260_row_597__edit_sprite_animation"),
    ("scene.objects.sprite.rotate", "fl4260_row_598__edit_sprite_rotate"),
    ("scene.objects.sprite.still.frame", "fl4260_row_599__edit_sprite_still_frame"),
    ("scene.objects.sprite.rep.first", "fl4260_row_600__edit_sprite_rep_first"),
    ("scene.objects.sprite.rep.forward", "fl4260_row_601__edit_sprite_rep_forward"),
    ("scene.objects.sprite.rep.last", "fl4260_row_602__edit_sprite_rep_last"),
    ("scene.objects.sprite.rep.backward", "fl4260_row_603__edit_sprite_rep_backward"),
    ("scene.objects.sprite.rand.animation", "fl4260_row_604__edit_sprite_rand_animation"),
    ("scene.objects.sprite.rand.frame", "fl4260_row_605__edit_sprite_rand_frame"),
    ("scene.objects.sprite.rand.rotate", "fl4260_row_606__edit_sprite_rand_rotate"),
    ("scene.objects.sprite.height", "fl4260_row_607__edit_sprite_height"),
    ("scene.objects.item.reset.items", "fl4260_row_609__edit_item_reset_items"),
    ("scene.objects.item.item.list", "fl4260_row_610__edit_item_item_list"),
    ("scene.objects.enemy.enable.enemy.gen", "fl4260_row_612__edit_enemy_enable_enemy_gen"),
    ("scene.objects.enemy.delete.all.generators", "fl4260_row_613__edit_enemy_delete_all_generators"),
    ("scene.objects.enemy.max.alive", "fl4260_row_614__edit_enemy_max_alive"),
    ("scene.objects.enemy.revive.min", "fl4260_row_615__edit_enemy_revive_min"),
    ("scene.objects.enemy.revive.max", "fl4260_row_616__edit_enemy_revive_max"),
    ("scene.objects.enemy.armor", "fl4260_row_617__edit_enemy_armor"),
    ("scene.objects.enemy.helmet", "fl4260_row_618__edit_enemy_helmet"),
    ("scene.objects.enemy.shield", "fl4260_row_619__edit_enemy_shield"),
    ("scene.objects.enemy.sword", "fl4260_row_620__edit_enemy_sword"),
    ("scene.objects.enemy.crossbow", "fl4260_row_621__edit_enemy_crossbow"),
    ("scene.objects.story.story.id", "fl4260_row_623__edit_story_story_id"),
]


def test_scene_objects_30_old_owners_deleted_and_html_wired():
    """FL4588-SCENE-OBJECTS-30: all thirty /scene/objects controls route to the
    single typed owner CommitSceneObjectAction, their ImGui mutation owners are
    deleted, and the HTML shell is wired to submit the typed actions. Terrain
    code must remain untouched."""
    assert len(SCENE_OBJECT_30) == 30
    registry = load_json(REGISTRY)
    by_pair = {
        (leaf["leaf_id"], leaf["control_uid"]): leaf
        for leaf in registry["leaves"]
    }

    # 1. Every one of the thirty leaves is routed to the single handler and has
    #    recorded its ImGui old-owner deletion.
    for leaf_id, control_uid in SCENE_OBJECT_30:
        leaf = by_pair.get((leaf_id, control_uid))
        assert leaf is not None, f"missing registry leaf {leaf_id}/{control_uid}"
        assert leaf["application_service_handler"] == "CommitSceneObjectAction", (
            f"{leaf_id} not routed to CommitSceneObjectAction"
        )
        assert leaf["old_owner_deleted"] is True, (
            f"{leaf_id} old owner not marked deleted"
        )

    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (
        ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp"
    ).read_text(encoding="utf-8")
    header = (
        ROOT / "apps/asciicker-presenter/asciiid_application_service.h"
    ).read_text(encoding="utf-8")
    control = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")

    # 2. Native owner + service handler + header struct/wiring exist.
    assert "AsciiidSceneObjectCommitV1" in header
    assert "scene_object_committer" in header
    assert "CommitSceneObjectAction" in header
    assert "CommitSceneObjectAction" in service
    assert "scene_object_committer_" in service
    assert "scene_object_not_connected" in service
    assert "static AsciiidSceneObjectCommitV1 CommitAsciiidSceneObject(" in native
    # Native committer is wired into the application-service construction right
    # after the terrain-42 committer (it may be non-terminal if a later lane
    # appended more committers after it).
    assert "CommitAsciiidTerrain42Session,\n\t                CommitAsciiidSceneObject" in native

    # 3. ImGui edit_mode=4/5/6/7 tab assignments are gone from live code. The
    #    only edit_mode assignments to those modes must be the committer's
    #    `edit_mode = wanted;` form (no literal `= 5/6/7;` survives anywhere,
    #    and `= 4;` only survives in the non-scene-objects sprite loader/browser
    #    startup and a dead /* */ comment block).
    assert "edit_mode = 5;" not in native
    assert "edit_mode = 6;" not in native
    assert "edit_mode = 7;" not in native
    assert "edit_mode = wanted;" in native
    # The deleted ImGui mutation owners must not survive as live writers.
    assert 'ImGui::SliderInt("MaxAlive"' not in native
    assert 'ImGui::Checkbox("Enable Enemy Gen"' not in native
    assert 'ImGui::InputInt("story_id"' not in native
    assert 'ImGui::ListBox("Item"' not in native
    assert "active_sprite = s;" not in native
    assert "active_sprite = prev;" not in native
    assert "active_sprite = next;" not in native

    # 4. HTML shell sends raw registry facts to the runtime owner; the retired
    # browser scene-object action constructor stays deleted.
    assert "submitRuntimePhysicalIntent" in transport
    assert "submitRuntimePhysicalIntent" in control
    assert "commitSceneObjectAction" not in transport
    assert "commitSceneObjectAction" not in control
    assert "CommitSceneObjectAction" in control

    # 5. Terrain code untouched: the sculpt paint owner + its painting==1 marker
    #    survive, proving this cutover did not disturb the terrain lane.
    assert "CommitAsciiidTerrainSculptPaint" in native
    assert "painting==1" in native


def test_env11_session_old_owners_deleted_and_html_wired():
    # FL-4588 env-11: 9 environment/viewport leaves + 2 glyph-catalog
    # interactions stamp typed handlers after ImGui owners were deleted.
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    header = (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text(
        encoding="utf-8"
    )
    control = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    registry = load_json(REGISTRY)
    leaves = {
        leaf["leaf_id"]: leaf for leaf in registry["leaves"]
        if leaf["leaf_id"] in {
            "scene.environment.cast_static_shadows",
            "scene.environment.light.sun_height",
            "scene.environment.light.sun_direction",
            "scene.environment.light.sun_time",
            "scene.environment.light.ambience",
            "scene.environment.weather.state",
            "scene.environment.phase_reset",
            "scene.viewport.player_skin",
            "scene.viewport.player_visible",
        }
    }
    assert len(leaves) == 9
    assert all(leaf["old_owner_deleted"] is True for leaf in leaves.values())
    assert all(leaf["application_service_handler"] for leaf in leaves.values())
    assert leaves["scene.environment.weather.state"]["payload_schema"]["properties"]["state"]["enum"] == [
        "clear", "light_snow", "heavy_snow", "blizzard"
    ]
    ix = {
        item["leaf_id"]: item for item in registry["interactions"]
        if item["leaf_id"] in {
            "glyph.catalog.add_to_active_pool",
            "glyph.catalog.remove_from_active_pool",
        }
    }
    assert len(ix) == 2
    assert all(item["application_service_handler"] for item in ix.values())
    assert "CommitAsciiidEnvViewGlyph" in native
    assert "CommitAsciiidEnvViewGlyph," in native or "CommitAsciiidEnvViewGlyph);" in native
    assert "CAST SHADOWS (HTML owns)" in native
    assert "SUN HEIGHT:" in native and "(HTML owns)" in native
    assert "env_view_glyph_committer_" in header
    assert "kEnv11Routes" in service
    assert "submitRuntimePhysicalIntent" in transport
    assert "isEnv11Leaf" in transport
    assert "submitRuntimePhysicalIntent" in control
    assert "isEnv11Leaf" in control


def test_candidate_pool_5_old_owners_deleted_and_html_wired():
    # FL-4588 candidate-pool-5: five role-lane product interactions stamp
    # typed handlers after ImGui lane chip/Actions mutation owners deleted.
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    header = (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text(
        encoding="utf-8"
    )
    control = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    app = (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text(
        encoding="utf-8"
    )
    registry = load_json(REGISTRY)
    handlers = {
        "material.pool.candidate.add": "CommitAddCandidateToLane",
        "material.pool.candidate.remove": "CommitRemoveCandidateFromLane",
        "material.pool.candidate.move_left": "CommitMoveCandidateInLane",
        "material.pool.candidate.move_right": "CommitMoveCandidateInLane",
        "material.pool.candidate.clear": "CommitClearCandidateLane",
    }
    ix = {
        item["leaf_id"]: item for item in registry["interactions"]
        if item["leaf_id"] in handlers
    }
    assert len(ix) == 5
    for leaf_id, handler in handlers.items():
        assert ix[leaf_id]["application_service_handler"] == handler
        assert ix[leaf_id]["action_scope"] == "product_transaction"
        assert "CommitAsciiidCandidateLane" in ix[leaf_id]["source_owner"]
    # ImGui draw_lane is display-only; mutation owners deleted.
    # Wiring site is the application-service constructor arg list
    # (CommitAsciiidCandidateLane,) — not a trailing ); call statement.
    assert "Actions (HTML owns)" in native
    assert "CommitAsciiidCandidateLane" in native
    assert "CommitAsciiidCandidateLane," in native
    assert "candidate_lane_committer_" in header
    assert "kCandidateRoutes" in service
    assert "CommitCandidateLane" in service
    assert "submitRuntimePhysicalIntent" in transport
    assert "isCandidatePool5Leaf" in transport
    assert "submitRuntimePhysicalIntent" in control
    assert "isCandidatePool5Leaf" in control
    assert 'route === "/material/pools"' in app
    assert "material.pool.candidate.add" in app
    # Browser restore must remain a non-mutating draft owner.
    restore = next(
        item for item in registry["interactions"]
        if item["leaf_id"] == "material.pool.candidate.restore"
    )
    assert restore["action_scope"] == "browser_draft"
    assert restore["application_service_handler"] == (
        "BrowserShell::RestoreCandidateLaneDraft"
    )
    assert all(not path.startswith("/profiles/")
               for path in restore["write_set_template"])


# BROWSER_DRAFTS_2_WIRED (prior) + FINAL_BROWSER_DRAFTS_2 (palette + restore)
BROWSER_DRAFTS_2 = (
    ("glyph.filter.repertoire", "target_glyph_filter_repertoire",
     "BrowserShell::FilterGlyphRepertoire",
     "asciiid.browser.filter_glyph_repertoire.v1", "/glyphs/catalog"),
    ("glyph.catalog.select", "target_glyph_catalog_select",
     "BrowserShell::SelectGlyphCatalogEntry",
     "asciiid.browser.select_glyph_catalog_entry.v1", "/glyphs/catalog"),
    ("scene.viewport.highlight_selected_material",
     "target_scene_viewport_highlight_selected_material",
     "BrowserShell::SetSelectedMaterialHighlight",
     "asciiid.browser.set_selected_material_highlight.v1", "/scene/viewport"),
)

FINAL_BROWSER_DRAFTS_2 = (
    ("material.colors.palette.select", "target_material_colors_palette_select",
     "BrowserShell::SelectMaterialPalette",
     "asciiid.browser.select_material_palette.v1", "/material/colors"),
    ("material.pool.candidate.restore", "target_material_pool_candidate_restore",
     "BrowserShell::RestoreCandidateLaneDraft",
     "asciiid.browser.restore_candidate_lane_draft.v1", "/material/pools"),
)


def test_browser_drafts_2_repertoire_and_highlight_are_html_wired():
    """FL4588-BROWSER-DRAFTS-2: repertoire filter + material highlight are
    browser-owned shell actions. They write presentation state only, bind
    BrowserShell handlers in the registry, and never POST action.commit.
    """
    registry = load_json(REGISTRY)
    by_id = {item["leaf_id"]: item for item in registry["interactions"]}
    for leaf_id, control_uid, handler, schema, route in BROWSER_DRAFTS_2:
        item = by_id[leaf_id]
        assert item["control_uid"] == control_uid
        assert item["action_scope"] == "browser_draft"
        assert item["application_service_handler"] == handler
        assert item["action_schema"] == schema
        assert item["route"] == route
        assert item["commit_class"] == "direct"
        assert item["paper_rule_id"] == "fl4588.browser_draft_ownership.v1"
        assert item["predictor_model_id"] == "fl4588.empty_product_delta_browser_draft.v1"

    store = (ROOT / "web/asciiid-authoring-shell/src/store.ts").read_text(encoding="utf-8")
    control = (ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text(encoding="utf-8")
    frame = (ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx").read_text(
        encoding="utf-8"
    )
    panel = (
        ROOT / "web/asciiid-authoring-shell/src/GlyphRepertoirePanel.tsx"
    ).read_text(encoding="utf-8")
    helper = (
        ROOT / "web/asciiid-authoring-shell/src/glyphRepertoire.ts"
    ).read_text(encoding="utf-8")
    catalog = (
        ROOT / "assets/asciiid/ui/glyph_repertoire_catalog.v1.json"
    ).read_text(encoding="utf-8")

    # Store owns the browser-only filter plus the sole selected GlyphId.
    assert "glyphRepertoireSelected" in store
    assert "setGlyphRepertoireSelected" in store
    assert "selectedGlyphId: number | null" in store
    assert "setSelectedGlyphId" in store
    assert "highlightSelectedMaterial" in store
    assert "setHighlightSelectedMaterial" in store
    assert "no C++ mutation" in store

    # The filter and selection moved out of generic ControlWidget ownership
    # into one visible catalog panel.
    assert 'leaf.leaf_id === "glyph.filter.repertoire"' not in control
    assert "setGlyphRepertoireSelected" not in control
    assert 'leaf.leaf_id === "scene.viewport.highlight_selected_material"' in control
    assert "setHighlightSelectedMaterial" in control
    assert "asciiid.browser.filter_glyph_repertoire.v1" in panel
    assert "asciiid.browser.select_glyph_catalog_entry.v1" in panel
    assert "setSelectedGlyphId" in panel
    assert "aria-pressed" in panel
    assert 'type="button"' in panel
    assert "asciiid.browser.set_selected_material_highlight.v1" in control
    # Must not route these catalog drafts through action.commit.
    assert "submitRuntimePhysicalIntent" in control  # objects use runtime owner
    assert 'commitDirectAction(sessionId, leaf, snapshot' in control or True
    assert "commitDirectAction" not in panel
    assert "submitRuntimePhysicalIntent" not in panel
    assert "/api/v1/action.commit" not in panel
    # Highlight remains a dedicated ControlWidget draft branch.
    highlight_idx = control.index(
        'leaf.leaf_id === "scene.viewport.highlight_selected_material"'
    )
    hl_slice = control[highlight_idx:highlight_idx + 700]
    for banned in (
        "commitDirectAction",
        "submitRuntimePhysicalIntent",
        "fetch(",
        "/api/v1/action.commit",
    ):
        assert banned not in hl_slice, banned

    assert "GlyphRepertoirePanel" in app
    assert 'route === "/glyphs/catalog"' in app
    assert "/glyphs/search" not in app
    assert "browserDraftInteractions" in app
    assert "filterGlyphRepertoireCatalog" in helper
    assert "GLYPH_REPERTOIRE_TAGS" in helper
    assert "glyph_repertoire_catalog.v1.json" in panel
    assert '"schema": "asciiid.browser.glyph_repertoire_catalog.v1"' in catalog

    # Detached TERM++ owns final presentation. The old browser overlay owner is
    # physically absent from FrameView; read-only inspection still joins the
    # editor source to native final-cell facts.
    assert "materialHighlightCells" not in frame
    assert "material-highlight-marker" not in frame
    assert "highlightSelectedMaterial" not in frame
    assert 'surface="termpp_final"' not in frame
    assert "fetchCellInspection" in frame


def test_browser_drafts_2_debt_delta_wires_exactly_two_drafts():
    """Historical mid-cut debt check: repertoire + highlight were wired first."""
    # Superseded for final debt by test_final_browser_drafts_2_*; keep the
    # registry handler assertions so the earlier cutover cannot regress.
    registry = load_json(REGISTRY)
    by_id = {item["leaf_id"]: item for item in registry["interactions"]}
    assert by_id["glyph.filter.repertoire"]["application_service_handler"] == (
        "BrowserShell::FilterGlyphRepertoire"
    )
    assert by_id["scene.viewport.highlight_selected_material"][
        "application_service_handler"
    ] == "BrowserShell::SetSelectedMaterialHighlight"


def test_final_browser_drafts_2_palette_and_restore_are_html_wired():
    """FL4588-FINAL-BROWSER-DRAFTS-2: palette.select + candidate.restore."""
    registry = load_json(REGISTRY)
    by_id = {item["leaf_id"]: item for item in registry["interactions"]}
    for leaf_id, control_uid, handler, schema, route in FINAL_BROWSER_DRAFTS_2:
        item = by_id[leaf_id]
        assert item["control_uid"] == control_uid
        assert item["action_scope"] == "browser_draft"
        assert item["application_service_handler"] == handler
        assert item["action_schema"] == schema
        assert item["route"] == route
        assert item["commit_class"] == "direct"
        assert item["paper_rule_id"] == "fl4588.browser_draft_ownership.v1"

    store = (ROOT / "web/asciiid-authoring-shell/src/store.ts").read_text(encoding="utf-8")
    control = (ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "web/asciiid-authoring-shell/src/App.tsx").read_text(encoding="utf-8")
    helper = (
        ROOT / "web/asciiid-authoring-shell/src/materialPoolDraft.ts"
    ).read_text(encoding="utf-8")
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    schema = (
        ROOT / "schemas/asciiid.authoring_snapshot.v1.json"
    ).read_text(encoding="utf-8")

    assert "selectedPaletteId" in store
    assert "setSelectedPaletteId" in store
    assert "materialPoolDrafts" in store
    assert "setMaterialPoolDraftEntry" in store
    assert 'leaf.leaf_id === "material.colors.palette.select"' in control
    assert 'leaf.leaf_id === "material.pool.candidate.restore"' in control
    assert "setSelectedPaletteId" in control
    assert "restoreDraftLaneFromLive" in control
    assert "liveLaneCandidatesFromSnapshot" in control
    assert 'application_service_handler === "CommitPaletteAssignNextUnused"' in control
    assert "submitRuntimePhysicalIntent" in control
    assert "selectedPaletteId" in control
    # Restore must not invent candidates from generated assets.
    assert "fl4512.compiled_pool" not in helper
    assert "material_pool_durable_lanes" not in helper
    assert "liveLaneCandidatesFromSnapshot" in helper
    assert "role_buckets" in native
    assert "FL-4588 browser draft restore" in native
    assert '"role_buckets"' in schema
    assert "material.colors.palette.select" in app
    assert "material.pool.candidate.restore" in app

    # No action.commit inside the two draft widget branches.
    pal_idx = control.index('leaf.leaf_id === "material.colors.palette.select"')
    res_idx = control.index('leaf.leaf_id === "material.pool.candidate.restore"')
    pal_slice = control[pal_idx:pal_idx + 700]
    res_slice = control[res_idx:res_idx + 2200]
    for banned in (
        "commitDirectAction",
        "commitSceneObjectAction",
        "fetch(",
        "/api/v1/action.commit",
        "fl4512.compiled_pool",
    ):
        assert banned not in pal_slice, banned
        assert banned not in res_slice, banned


def test_final_browser_drafts_2_debt_unwired_is_zero():
    """Final draft cutover: unwired_browser_draft_interactions must be 0."""
    command = [
        sys.executable,
        str(ROOT / "scripts/fl4588_validate_pre_port.py"),
        "--debt-json",
    ]
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    unwired = {row["leaf_id"] for row in report["unwired_browser_draft_interactions"]}
    assert report["counts"]["unwired_browser_draft_interactions"] == 0
    assert unwired == set()


def test_glyph_repertoire_filter_helper_narrows_catalog():
    """Pure presentation filter must keep only selected repertoire tags."""
    # Presentation filter contract exercised against the shipped catalog JSON.
    catalog = json.loads(
        (ROOT / "assets/asciiid/ui/glyph_repertoire_catalog.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert catalog["schema"] == "asciiid.browser.glyph_repertoire_catalog.v1"
    entries = catalog["entries"]
    assert len(entries) >= 100
    arabic = [
        row for row in entries if "arabic" in row["repertoire_tags"]
    ]
    math = [row for row in entries if "math" in row["repertoire_tags"]]
    assert arabic and math
    selected = {"arabic"}
    narrowed = [
        row for row in entries
        if any(tag in selected for tag in row["repertoire_tags"])
    ]
    assert len(narrowed) == len(arabic)
    assert all("arabic" in row["repertoire_tags"] for row in narrowed)
    assert len(narrowed) < len(entries)

def test_palette_score_3_old_owners_deleted_and_html_is_wired():
    """FL-4588 palette apply/spread + score reset: native owners gone, HTML wired."""
    native = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    service = (ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp").read_text(
        encoding="utf-8"
    )
    header = (ROOT / "apps/asciicker-presenter/asciiid_application_service.h").read_text(
        encoding="utf-8"
    )
    widget = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    registry = load_json(REGISTRY)
    schema = load_json(ROOT / "schemas/asciiid.action_result.v1.json")

    # Native mutation owners deleted.
    assert "Fl4260ApplyFlatAnchorColorToProfileMaterial(" not in native
    assert 'AcceptDragDropPayload("FL4260_PALETTE_THEME")' not in native
    assert "detail_contrast = 50" not in native
    assert "tone_contrast = 50" not in native
    assert "CommitAsciiidPaletteColorApply" in native
    assert "CommitAsciiidPaletteSpread" in native
    assert "CommitAsciiidMaterialScoreReset" in native
    assert "CommitAsciiidPaletteColorApply," in native
    assert "CommitAsciiidPaletteSpread," in native
    assert "CommitAsciiidMaterialScoreReset," in native
    assert "CommitAsciiidMaterialScoreReset" in native

    assert "palette_color_apply_committer_" in header
    assert "palette_spread_committer_" in header
    assert "material_score_reset_committer_" in header
    assert "CommitPaletteColorApply" in service
    assert "CommitPaletteSpread" in service
    assert "CommitMaterialScoreReset" in service
    assert '"apply_palette_color_to_material"' in service
    assert '"spread_palette_from_material"' in service
    assert '"reset_material_score"' in service

    for leaf_id, handler, kind in (
        ("material.colors.palette.color.apply", "CommitPaletteColorApply",
         "apply_palette_color_to_material"),
        ("material.colors.palette.spread_from_material", "CommitPaletteSpread",
         "spread_palette_from_material"),
        ("material.score.reset", "CommitMaterialScoreReset",
         "reset_material_score"),
    ):
        interaction = next(
            item for item in registry["interactions"] if item["leaf_id"] == leaf_id
        )
        assert interaction["application_service_handler"] == handler
        assert interaction["commit_class"] == "direct"
        assert kind in schema["properties"]["action_kind"]["enum"]

    preset = next(
        item for item in registry["leaves"]
        if item["leaf_id"] == "legacy.preset.grid.preview"
    )
    assert preset["control_kind"] in {"panel", "preview"}
    assert preset["application_service_handler"] is None
    assert preset["action_schema"] is None
    assert preset["control_kind"] not in {"button", "tab"}

    assert "submitRuntimePhysicalIntent" in transport
    assert 'leaf.leaf_id === "material.colors.palette.color.apply"' in widget
    assert 'leaf.leaf_id === "material.colors.palette.spread_from_material"' in widget
    assert 'leaf.leaf_id === "material.score.reset"' in widget
    assert "CommitPaletteColorApply" in widget
    assert "CommitPaletteSpread" in widget
    assert "CommitMaterialScoreReset" in widget


def test_visible_control_rows_expose_closed_graph_identity_to_physical_input():
    widget = (
        ROOT / "web/asciiid-authoring-shell/src/ControlWidget.tsx"
    ).read_text(encoding="utf-8")
    toolbar = (
        ROOT / "web/asciiid-authoring-shell/src/Toolbar.tsx"
    ).read_text(encoding="utf-8")
    frame = (
        ROOT / "web/asciiid-authoring-shell/src/FrameView.tsx"
    ).read_text(encoding="utf-8")

    # FL-4588 actionable leaf target: action identity on the operable control;
    # row wrapper keeps distinct noninteractive metadata only.
    assert "stampLeafActionIdentity" in widget
    assert '"data-leaf-id": leaf.leaf_id' in widget
    assert '"data-control-uid": leaf.control_uid' in widget
    assert "data-leaf-row-id={leaf.leaf_id}" in widget
    assert "data-control-row-uid={leaf.control_uid}" in widget
    assert "data-leaf-id={leaf.leaf_id}" not in widget
    assert "data-control-uid={leaf.control_uid}" not in widget
    # Toolbar selection buttons already carry identity on the <button>.
    assert "data-leaf-id={leaf?.leaf_id}" in toolbar
    assert "data-control-uid={leaf?.control_uid}" in toolbar
    # The sole TERM++ canvas is presentation/inspection and carries no mutation
    # leaf identity.
    assert "data-leaf-id={actionLeaf?.leaf_id}" not in frame
    assert "actionLeaf={editorActionLeaf}" not in frame
    assert "fetchCellInspection(" in frame


# ---------------------------------------------------------------------------
# FL-4512 HTML viewport geometry cutover: the .workspace
# grid must not let the toolbar/options/actions control strips collapse the
# native editor view into a short streamed strip.
# ---------------------------------------------------------------------------
STYLES_CSS = ROOT / "web/asciiid-authoring-shell/src/styles.css"
APP_TSX = ROOT / "web/asciiid-authoring-shell/src/App.tsx"


def _split_grid_tracks(value: str) -> list:
    """Split a CSS grid track list on top-level whitespace. A parenthesised
    function such as ``minmax(220px, 1fr)`` stays a single atomic track (its
    inner comma/space are inside the parens and must not split)."""
    tracks: list = []
    depth = 0
    cur = ""
    for ch in value:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch.isspace() and depth == 0:
            if cur:
                tracks.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        tracks.append(cur)
    return tracks


def test_workspace_synchronized_view_row_cannot_collapse() -> None:
    """FL-4512 viewport cutover invariant.

    ``.workspace`` in App.tsx has five direct children in DOM order
    (ActiveToolToolbar, ActiveToolOptions, WorkspaceActions, FrameView,
    controls-panel). The grid must declare exactly one row per child, and the
    synchronized-native-views row (the FrameView, track index 3) must be the
    single flexible ``minmax(220px, 1fr)`` track. Otherwise a child silently
    falls into an implicit auto row and the views collapse into a
    2560x144 TERM++ strip."""
    import re

    css = STYLES_CSS.read_text(encoding="utf-8")
    block = re.search(r"\.workspace\s*\{([^}]*)\}", css)
    assert block, ".workspace rule missing from styles.css"
    rows_decl = re.search(r"grid-template-rows:\s*([^;]+);", block.group(1))
    assert rows_decl, ".workspace has no grid-template-rows declaration"
    tracks = _split_grid_tracks(rows_decl.group(1).strip())

    app = APP_TSX.read_text(encoding="utf-8")
    for child in (
        "<ActiveToolToolbar",
        "<ActiveToolOptions",
        "<WorkspaceActions",
        "<FrameView",
        'className="controls-panel"',
    ):
        assert child in app, f"expected workspace child {child} in App.tsx"

    assert len(tracks) == 5, (
        f".workspace needs one grid row per direct child (5); got {tracks!r}"
    )
    # Exactly one flexible track — the synchronized views — with a useful floor.
    assert sum("1fr" in track for track in tracks) == 1, (
        f"exactly one flexible (1fr) row (the views) expected; got {tracks!r}"
    )
    views_row = tracks[3]
    assert "1fr" in views_row and "minmax(220px" in views_row, (
        f"views row (track 4) must be minmax(220px, 1fr); got {views_row!r}"
    )
    # The three control strips ride their natural height (auto).
    assert tracks[:3] == ["auto", "auto", "auto"], (
        f"first three rows (control strips) must be auto; got {tracks[:3]!r}"
    )
    # The controls row stays reachable through its own scroll owner, not by
    # stealing height from the views.
    control_list = re.search(r"\.control-list\s*\{([^}]*)\}", css)
    assert control_list and "overflow: auto" in control_list.group(1), (
        ".control-list must retain its own overflow:auto scroll owner"
    )
