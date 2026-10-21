from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


DRIVER = r'''
#include "apps/asciicker-presenter/asciiid_application_service.h"

#include <cstdio>
#include <string>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    AsciiidApplicationService service("{}");
    std::string reason;
    if (!service.LoadUiRegistryGraph(argv[1], &reason)) {
        std::printf("load=0\nreason=%s\n", reason.c_str());
        return 3;
    }
    RuntimeIntentRegistryRowV1 row;
    const bool resolved = service.ResolveRegistryControl(
        "scene.objects.enemy.tab", "fl4260_row_611__edit_enemy_tab", &row);
    std::printf("load=1\n");
    std::printf("resolved=%d\n", resolved ? 1 : 0);
    std::printf("handler=%s\n", row.application_service_handler.c_str());
    std::printf("live=%d\n", service.RegistryControlLive(
        "scene.objects.enemy.tab", "fl4260_row_611__edit_enemy_tab") ? 1 : 0);
    std::printf("retired_registered=%d\n", service.RegistryControlRegistered(
        "workspace.history.clear.retired", "fl4260_row_564__edit_urdo_purge") ? 1 : 0);
    std::printf("retired_live=%d\n", service.RegistryControlLive(
        "workspace.history.clear.retired", "fl4260_row_564__edit_urdo_purge") ? 1 : 0);
    std::printf("interaction_live=%d\n", service.RegistryControlLive(
        "scene.terrain.paint.material", "asciiid_canvas_terrain_material_brush") ? 1 : 0);
    std::printf("unknown=%d\n", service.RegistryControlRegistered(
        "unknown.leaf", "unknown_control") ? 1 : 0);
    return 0;
}
'''


INTENT_DRIVER = r'''
#include "apps/asciicker-presenter/asciiid_application_service.h"
#include "engine/third_party/cjson/cJSON.h"

#include <cstdio>
#include <cstdlib>
#include <string>

static std::string PrintJson(const cJSON* value) {
    char* bytes = cJSON_PrintUnformatted(value);
    const std::string result = bytes ? bytes : "";
    std::free(bytes);
    return result;
}

static std::string IntentJson(
        const char* leaf_id, const char* control_uid, const cJSON* witness,
        const char* raw_value_json = "5", bool shift_modifier = false,
        const char* event_kind = "widget", const char* event_key = nullptr,
        int event_button = -1, bool control_modifier = false,
        bool alt_modifier = false, bool meta_modifier = false) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "schema", "asciiid.runtime_physical_intent.v1");
    cJSON_AddNumberToObject(root, "protocol_version", 1);
    cJSON_AddStringToObject(root, "leaf_id", leaf_id);
    cJSON_AddStringToObject(root, "control_uid", control_uid);
    cJSON* event = cJSON_AddObjectToObject(root, "event");
    cJSON_AddStringToObject(event, "kind", event_kind);
    cJSON_AddNumberToObject(event, "button", event_button);
    if (event_key) cJSON_AddStringToObject(event, "key", event_key);
    else cJSON_AddNullToObject(event, "key");
    cJSON_AddNumberToObject(event, "wheel_delta", 0);
    cJSON* modifiers = cJSON_AddObjectToObject(event, "modifiers");
    cJSON_AddBoolToObject(modifiers, "shift", shift_modifier);
    cJSON_AddBoolToObject(modifiers, "control", control_modifier);
    cJSON_AddBoolToObject(modifiers, "alt", alt_modifier);
    cJSON_AddBoolToObject(modifiers, "meta", meta_modifier);
    if (raw_value_json) {
        cJSON* raw_payload = cJSON_AddObjectToObject(root, "raw_payload");
        cJSON_AddItemToObject(raw_payload, "value", cJSON_Parse(raw_value_json));
    } else {
        cJSON_AddNullToObject(root, "raw_payload");
    }
    cJSON_AddNumberToObject(root, "client_event_time_ns", 17);
    cJSON_AddItemToObject(root, "observed_witness", cJSON_Duplicate(witness, true));
    const std::string result = PrintJson(root);
    cJSON_Delete(root);
    return result;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string view_hash(64, 'b');
    const std::string frame_hash(64, 'c');
    AsciiidApplicationService service(
        [view_hash]() {
            return AsciiidApplicationResultV1{
                true, {},
                "{\"schema\":\"asciiid.authoring_snapshot.v1\","
                "\"state_revision\":0,\"view_state_sha256\":\"" +
                    view_hash + "\",\"document\":{}}"};
        },
        [view_hash, frame_hash]() {
            AsciiidNativeFrameV1 frame;
            frame.ok = true;
            frame.frame_id = 40;
            frame.width = 320;
            frame.height = 180;
            frame.view_state_sha256 = view_hash;
            frame.rgba_sha256 = frame_hash;
            return frame;
        },
        {});
    std::string reason;
    if (!service.LoadUiRegistryGraph(argv[1], &reason)) return 3;
    service.set_runtime_identity_provider([&service]() {
        return AsciiidApplicationResultV1{
            true, {},
            "{\"schema\":\"asciiid.runtime_identity.v1\","
            "\"backend_pid\":73,\"source_commit\":\"abc123\","
            "\"source_dirty\":false,\"ui_registry_sha256\":\"" +
                service.UiRegistrySha256() + "\"}"};
    });
    std::string committed_leaf;
    std::string committed_kind;
    std::string committed_payload;
    std::string committed_transaction;
    bool mutate_scene_object = true;
    service.set_scene_object_committer(
        [&](const std::string& leaf, const std::string& kind,
            const std::string& payload, const std::string& transaction) {
            committed_leaf = leaf;
            committed_kind = kind;
            committed_payload = payload;
            committed_transaction = transaction;
            AsciiidSceneObjectCommitV1 result;
            result.ok = true;
            result.changed = mutate_scene_object;
            result.action_kind = kind;
            return result;
        });
    std::string terrain_op;
    double terrain_number = 0.0;
    std::string terrain_transaction;
    service.set_terrain_session_committer(
        [&](const std::string& op, const std::string&, double n0, double,
            bool, const std::string& transaction) {
            terrain_op = op;
            terrain_number = n0;
            terrain_transaction = transaction;
            AsciiidTerrainSessionCommitV1 result;
            result.ok = true;
            result.changed = false;
            return result;
        });

    const AsciiidApplicationResultV1 snapshot =
        service.CurrentRuntimeSnapshot("session-test");
    std::printf("snapshot_ok=%d\n", snapshot.ok ? 1 : 0);
    cJSON* snapshot_root = cJSON_ParseWithLength(
        snapshot.json.data(), snapshot.json.size());
    const cJSON* schema = cJSON_GetObjectItemCaseSensitive(snapshot_root, "schema");
    const cJSON* witness = cJSON_GetObjectItemCaseSensitive(snapshot_root, "witness");
    std::printf("snapshot_schema=%s\n", cJSON_GetStringValue(schema));
    std::printf("witness_fields=%d\n", cJSON_GetArraySize(witness));

    const AsciiidApplicationResultV1 terrain_control =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.edit.tile.radius",
                "fl4260_row_570__tile_radius", witness, "7.5", false,
                "keyboard", "Enter"));
    const AsciiidApplicationResultV1 wrong_terrain_key =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.edit.tile.radius",
                "fl4260_row_570__tile_radius", witness, "7.5", false,
                "keyboard", "Space"));
    std::printf("terrain_control_ok=%d\n", terrain_control.ok ? 1 : 0);
    std::printf("wrong_terrain_key_reason=%s\n",
        wrong_terrain_key.reason.c_str());
    std::printf("terrain_runtime_owned=%d\n",
        terrain_op == "set_tile_radius" && terrain_number == 7.5 &&
        terrain_transaction.rfind("runtime_intent_", 0) == 0 ? 1 : 0);

    // FL-4512: every offered scalar gesture crosses the production intent
    // interface; the registry, not a browser action table, grants authority.
    RuntimeIntentRegistryRowV1 scalar_row;
    service.ResolveRegistryControl("material.score.density_bias",
        "fl4260_row_491__scoring_density_bias", &scalar_row);
    bool scalar_events_ok = scalar_row.physical_events.size() == 9;
    for (const auto& event : scalar_row.physical_events) {
        const auto result = service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.edit.tile.radius",
                "fl4260_row_570__tile_radius", witness, "7.5", false,
                event.kind.c_str(), event.key.empty() ? nullptr : event.key.c_str(),
                event.button));
        scalar_events_ok = scalar_events_ok && result.ok;
    }
    std::printf("scalar_events_ok=%d\n", scalar_events_ok ? 1 : 0);
    const auto wrong_scalar_button = service.SubmitPhysicalIntent(
        "session-test", IntentJson("material.score.density_bias",
            "fl4260_row_491__scoring_density_bias", witness, "7", false,
            "pointer", nullptr, 2));
    std::printf("wrong_scalar_button_reason=%s\n", wrong_scalar_button.reason.c_str());

    mutate_scene_object = false;
    const AsciiidApplicationResultV1 const_shape = service.SubmitPhysicalIntent(
        "session-test", IntentJson("scene.objects.enemy.tab",
            "fl4260_row_611__edit_enemy_tab", witness, nullptr, false,
            "pointer", nullptr, 0));
    const bool const_shape_ok = const_shape.ok &&
        committed_payload == "{\"tool\":\"enemy_generator\"}";
    const AsciiidApplicationResultV1 bool_shape = service.SubmitPhysicalIntent(
        "session-test", IntentJson("scene.objects.enemy.enable.enemy.gen",
            "fl4260_row_612__edit_enemy_enable_enemy_gen", witness, "true"));
    const bool bool_shape_ok = bool_shape.ok &&
        committed_payload == "{\"enabled\":true}";
    const AsciiidApplicationResultV1 empty_shape = service.SubmitPhysicalIntent(
        "session-test", IntentJson("scene.objects.enemy.delete.all.generators",
            "fl4260_row_613__edit_enemy_delete_all_generators", witness, nullptr,
            false, "pointer", nullptr, 0));
    const bool empty_shape_ok = empty_shape.ok && committed_payload == "{}";
    const AsciiidApplicationResultV1 number_shape = service.SubmitPhysicalIntent(
        "session-test", IntentJson("scene.objects.sprite.rotate",
            "fl4260_row_598__edit_sprite_rotate", witness, "45.5", false,
            "keyboard", "Enter"));
    const bool number_shape_ok = number_shape.ok &&
        committed_payload == "{\"yaw_degrees\":45.5}";
    const AsciiidApplicationResultV1 string_shape = service.SubmitPhysicalIntent(
        "session-test", IntentJson("scene.objects.sprite.combo",
            "fl4260_row_511__sprite_combo", witness, "\"hero\"", false,
            "keyboard", "Enter"));
    const bool string_shape_ok = string_shape.ok &&
        committed_payload == "{\"sprite_id\":\"hero\"}";
    const AsciiidApplicationResultV1 selection_shape = service.SubmitPhysicalIntent(
        "session-test", IntentJson("legacy.n.a", "fl4260_row_516__dynamic_label_label",
            witness, "{\"instance_id\":\"instance-1\"}", true,
            "keyboard", "Enter"));
    const bool selection_shape_ok = selection_shape.ok &&
        committed_payload ==
            "{\"instance_id\":\"instance-1\",\"selection_mode\":\"range_add\"}";
    const AsciiidApplicationResultV1 selection_toggle_control =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.n.a",
                "fl4260_row_516__dynamic_label_label", witness,
                "{\"instance_id\":\"instance-2\"}", false, "keyboard",
                "Enter", -1, true));
    const bool selection_toggle_control_ok = selection_toggle_control.ok &&
        committed_payload ==
            "{\"instance_id\":\"instance-2\",\"selection_mode\":\"toggle\"}";
    const AsciiidApplicationResultV1 selection_toggle_meta =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.n.a",
                "fl4260_row_516__dynamic_label_label", witness,
                "{\"instance_id\":\"instance-3\"}", false, "keyboard",
                "Enter", -1, false, false, true));
    const bool selection_toggle_meta_ok = selection_toggle_meta.ok &&
        committed_payload ==
            "{\"instance_id\":\"instance-3\",\"selection_mode\":\"toggle\"}";
    const AsciiidApplicationResultV1 selection_alt_rejected =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.n.a",
                "fl4260_row_516__dynamic_label_label", witness,
                "{\"instance_id\":\"instance-4\"}", false, "keyboard",
                "Enter", -1, false, true));
    const AsciiidApplicationResultV1 selection_shift_control_rejected =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.n.a",
                "fl4260_row_516__dynamic_label_label", witness,
                "{\"instance_id\":\"instance-5\"}", true, "keyboard",
                "Enter", -1, true));
    const AsciiidApplicationResultV1 selection_control_meta_rejected =
        service.SubmitPhysicalIntent(
            "session-test", IntentJson("legacy.n.a",
                "fl4260_row_516__dynamic_label_label", witness,
                "{\"instance_id\":\"instance-6\"}", false, "keyboard",
                "Enter", -1, true, false, true));
    std::printf("const_shape_ok=%d\n", const_shape_ok ? 1 : 0);
    std::printf("bool_shape_ok=%d\n", bool_shape_ok ? 1 : 0);
    std::printf("empty_shape_ok=%d\n", empty_shape_ok ? 1 : 0);
    std::printf("number_shape_ok=%d\n", number_shape_ok ? 1 : 0);
    std::printf("string_shape_ok=%d\n", string_shape_ok ? 1 : 0);
    std::printf("selection_shape_ok=%d\n", selection_shape_ok ? 1 : 0);
    std::printf("selection_toggle_control_ok=%d\n",
        selection_toggle_control_ok ? 1 : 0);
    std::printf("selection_toggle_meta_ok=%d\n",
        selection_toggle_meta_ok ? 1 : 0);
    std::printf("selection_alt_reason=%s\n",
        selection_alt_rejected.reason.c_str());
    std::printf("selection_shift_control_reason=%s\n",
        selection_shift_control_rejected.reason.c_str());
    std::printf("selection_control_meta_reason=%s\n",
        selection_control_meta_rejected.reason.c_str());
    mutate_scene_object = true;

    const uint64_t revision_before = service.StateRevision();
    const AsciiidApplicationResultV1 valid = service.SubmitPhysicalIntent(
        "session-test",
        IntentJson("scene.objects.enemy.max.alive",
            "fl4260_row_614__edit_enemy_max_alive", witness, "5", false,
            "keyboard", "Enter"));
    cJSON* result_root = cJSON_ParseWithLength(valid.json.data(), valid.json.size());
    std::printf("result_ok=%d\n", valid.ok ? 1 : 0);
    std::printf("result_schema=%s\n", cJSON_GetStringValue(
        cJSON_GetObjectItemCaseSensitive(result_root, "schema")));
    std::printf("committed_leaf=%s\n", committed_leaf.c_str());
    std::printf("committed_kind=%s\n", committed_kind.c_str());
    std::printf("committed_payload=%s\n", committed_payload.c_str());
    std::printf("runtime_transaction=%d\n",
        committed_transaction.rfind("runtime_intent_", 0) == 0 ? 1 : 0);
    std::printf("revision_advanced=%d\n",
        revision_before + 1 == service.StateRevision() ? 1 : 0);
    cJSON_Delete(result_root);

    cJSON* stale_witness = cJSON_Duplicate(witness, true);
    const AsciiidApplicationResultV1 stale = service.SubmitPhysicalIntent(
        "session-test",
        IntentJson("scene.objects.enemy.tab",
            "fl4260_row_611__edit_enemy_tab", stale_witness, nullptr, false,
            "pointer", nullptr, 0));
    std::printf("stale_reason=%s\n", stale.reason.c_str());
    cJSON_Delete(stale_witness);

    const AsciiidApplicationResultV1 retired = service.SubmitPhysicalIntent(
        "session-test",
        IntentJson("workspace.history.clear.retired",
            "fl4260_row_564__edit_urdo_purge", witness));
    std::printf("retired_reason=%s\n", retired.reason.c_str());
    const AsciiidApplicationResultV1 unknown = service.SubmitPhysicalIntent(
        "session-test", IntentJson("unknown.leaf", "unknown_control", witness));
    std::printf("unknown_reason=%s\n", unknown.reason.c_str());

    cJSON_Delete(snapshot_root);
    return 0;
}
'''


DOCUMENT_DRIVER = r'''
#include "apps/asciicker-presenter/asciiid_application_service.h"
#include "engine/third_party/cjson/cJSON.h"

#include <cstdio>
#include <cstdlib>
#include <string>

static std::string PrintJson(const cJSON* value) {
    char* bytes = cJSON_PrintUnformatted(value);
    const std::string result = bytes ? bytes : "";
    std::free(bytes);
    return result;
}

static std::string CurrentWitness(AsciiidApplicationService& service) {
    const AsciiidApplicationResultV1 snapshot =
        service.CurrentRuntimeSnapshot("document-session");
    if (!snapshot.ok) return "";
    cJSON* root = cJSON_ParseWithLength(snapshot.json.data(), snapshot.json.size());
    const cJSON* witness = cJSON_GetObjectItemCaseSensitive(root, "witness");
    const std::string result = PrintJson(witness);
    cJSON_Delete(root);
    return result;
}

static std::string IntentJson(
        const char* leaf_id, const char* control_uid, const char* event_kind,
        int button, const std::string& witness_json,
        const char* raw_value_json = nullptr) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "schema", "asciiid.runtime_physical_intent.v1");
    cJSON_AddNumberToObject(root, "protocol_version", 1);
    cJSON_AddStringToObject(root, "leaf_id", leaf_id);
    cJSON_AddStringToObject(root, "control_uid", control_uid);
    cJSON* event = cJSON_AddObjectToObject(root, "event");
    cJSON_AddStringToObject(event, "kind", event_kind);
    cJSON_AddNumberToObject(event, "button", button);
    cJSON_AddNullToObject(event, "key");
    cJSON_AddNumberToObject(event, "wheel_delta", 0);
    cJSON* modifiers = cJSON_AddObjectToObject(event, "modifiers");
    cJSON_AddBoolToObject(modifiers, "shift", false);
    cJSON_AddBoolToObject(modifiers, "control", false);
    cJSON_AddBoolToObject(modifiers, "alt", false);
    cJSON_AddBoolToObject(modifiers, "meta", false);
    if (raw_value_json) {
        cJSON* raw_payload = cJSON_AddObjectToObject(root, "raw_payload");
        cJSON_AddItemToObject(raw_payload, "value", cJSON_Parse(raw_value_json));
    } else {
        cJSON_AddNullToObject(root, "raw_payload");
    }
    cJSON_AddNumberToObject(root, "client_event_time_ns", 23);
    cJSON_AddItemToObject(root, "observed_witness", cJSON_Parse(witness_json.c_str()));
    const std::string result = PrintJson(root);
    cJSON_Delete(root);
    return result;
}

static AsciiidApplicationResultV1 Submit(
        AsciiidApplicationService& service, const char* leaf_id,
        const char* control_uid, const char* event_kind, int button,
        const char* raw_value_json = nullptr,
        const std::string* binary_payload = nullptr,
        AsciiidDocumentDownloadV1* binary_result = nullptr) {
    const std::string witness = CurrentWitness(service);
    return service.SubmitPhysicalIntent(
        "document-session",
        IntentJson(leaf_id, control_uid, event_kind, button, witness,
            raw_value_json),
        binary_payload, binary_result);
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string view_hash(64, 'd');
    const std::string frame_hash(64, 'e');
    AsciiidApplicationService* service_ptr = nullptr;
    AsciiidApplicationService service(
        [&]() {
            const uint64_t revision = service_ptr ? service_ptr->StateRevision() : 0;
            return AsciiidApplicationResultV1{
                true, {},
                "{\"schema\":\"asciiid.authoring_snapshot.v1\","
                "\"state_revision\":" + std::to_string(revision) +
                ",\"view_state_sha256\":\"" + view_hash +
                "\",\"document\":{}}"};
        },
        [&]() {
            AsciiidNativeFrameV1 frame;
            frame.ok = true;
            frame.frame_id = 61;
            frame.width = 320;
            frame.height = 180;
            frame.view_state_sha256 = view_hash;
            frame.rgba_sha256 = frame_hash;
            return frame;
        },
        {});
    service_ptr = &service;
    std::string reason;
    if (!service.LoadUiRegistryGraph(argv[1], &reason)) return 3;
    service.set_runtime_identity_provider([&service]() {
        return AsciiidApplicationResultV1{
            true, {},
            "{\"schema\":\"asciiid.runtime_identity.v1\","
            "\"backend_pid\":75,\"source_commit\":\"abc123\","
            "\"source_dirty\":false,\"ui_registry_sha256\":\"" +
                service.UiRegistrySha256() + "\"}"};
    });

    int undo_calls = 0;
    int redo_calls = 0;
    int new_calls = 0;
    int open_calls = 0;
    int merge_calls = 0;
    int save_calls = 0;
    std::string last_transaction;
    std::string last_name;
    std::string last_bytes;
    service.set_history_undo_committer([&](const std::string& transaction) {
        ++undo_calls;
        last_transaction = transaction;
        return AsciiidHistoryCommitV1{true, true, {}, true, true, 10, 11};
    });
    service.set_history_redo_committer([&](const std::string& transaction) {
        ++redo_calls;
        last_transaction = transaction;
        return AsciiidHistoryCommitV1{true, true, {}, true, false, 11, 12};
    });
    service.set_document_new_committer([&](const std::string& transaction) {
        ++new_calls;
        last_transaction = transaction;
        return AsciiidDocumentNewCommitV1{true, {}};
    });
    service.set_document_open_committer(
        [&](const std::string& bytes, const std::string& name) {
            ++open_calls;
            last_name = name;
            last_bytes = bytes;
            return AsciiidDocumentOpenCommitV1{
                true, {}, name, std::string(64, 'a'), bytes.size()};
        });
    service.set_document_merge_committer(
        [&](const std::string& bytes, const std::string& name) {
            ++merge_calls;
            last_name = name;
            last_bytes = bytes;
            return AsciiidDocumentMergeCommitV1{
                true, {}, name, std::string(64, 'b'), bytes.size(), 3, 4};
        });
    service.set_document_downloader([&]() {
        ++save_calls;
        return AsciiidDocumentDownloadV1{
            true, {}, "saved.a3d", std::string(64, 'c'), "A3D-SAVED"};
    });

    RuntimeIntentRegistryRowV1 row;
    service.ResolveRegistryControl(
        "workspace.history.undo", "fl4260_row_557__unlabeled", &row);
    std::printf("undo_event=%s:%d\n", row.physical_events.at(0).kind.c_str(),
        row.physical_events.at(0).button);
    service.ResolveRegistryInteraction(
        "workspace.document.open.commit",
        "target_workspace_document_open_commit", &row);
    std::printf("open_event=%s:%d\n", row.physical_events.at(0).kind.c_str(),
        row.physical_events.at(0).button);

    const AsciiidApplicationResultV1 wrong_event = Submit(
        service, "workspace.history.undo",
        "fl4260_row_557__unlabeled", "widget", -1);
    std::printf("wrong_event_reason=%s\n", wrong_event.reason.c_str());
    const uint64_t undo_revision = service.StateRevision();
    const AsciiidApplicationResultV1 undo = Submit(
        service, "workspace.history.undo",
        "fl4260_row_557__unlabeled", "pointer", 0);
    std::printf("undo_ok=%d\n", undo.ok ? 1 : 0);
    std::printf("undo_once=%d\n", undo_calls == 1 ? 1 : 0);
    std::printf("undo_revision=%d\n",
        service.StateRevision() == undo_revision + 1 ? 1 : 0);
    std::printf("undo_transaction=%d\n",
        last_transaction.rfind("runtime_intent_", 0) == 0 ? 1 : 0);

    const AsciiidApplicationResultV1 redo = Submit(
        service, "workspace.history.redo",
        "fl4260_row_562__unlabeled", "pointer", 0);
    std::printf("redo_ok=%d\n", redo.ok && redo_calls == 1 ? 1 : 0);
    const AsciiidApplicationResultV1 fresh = Submit(
        service, "workspace.document.new",
        "fl4260_row_522__view_new", "pointer", 0);
    std::printf("new_ok=%d\n", fresh.ok && new_calls == 1 ? 1 : 0);

    const std::string open_bytes("A3D-OPEN\0BYTES", 14);
    const AsciiidApplicationResultV1 missing_binary = Submit(
        service, "workspace.document.open.commit",
        "target_workspace_document_open_commit", "file", -1,
        "\"opened.a3d\"");
    std::printf("missing_binary_reason=%s\n", missing_binary.reason.c_str());
    const AsciiidApplicationResultV1 opened = Submit(
        service, "workspace.document.open.commit",
        "target_workspace_document_open_commit", "file", -1,
        "\"opened.a3d\"", &open_bytes);
    std::printf("open_ok=%d\n",
        opened.ok && open_calls == 1 && last_name == "opened.a3d" &&
        last_bytes == open_bytes ? 1 : 0);

    const std::string merge_bytes = "A3D-MERGE";
    const AsciiidApplicationResultV1 merged = Submit(
        service, "workspace.document.merge.commit",
        "target_workspace_document_merge_commit", "file", -1,
        "\"merged.a3d\"", &merge_bytes);
    std::printf("merge_ok=%d\n",
        merged.ok && merge_calls == 1 && last_name == "merged.a3d" &&
        last_bytes == merge_bytes ? 1 : 0);

    AsciiidDocumentDownloadV1 downloaded;
    const uint64_t save_revision = service.StateRevision();
    const AsciiidApplicationResultV1 saved = Submit(
        service, "workspace.document.save_as.commit",
        "target_workspace_document_save_as_commit", "pointer", 0,
        nullptr, nullptr, &downloaded);
    std::printf("save_ok=%d\n",
        saved.ok && save_calls == 1 && downloaded.bytes == "A3D-SAVED" ? 1 : 0);
    std::printf("save_revision_unchanged=%d\n",
        service.StateRevision() == save_revision ? 1 : 0);
    return 0;
}
'''


def _build_harness(tmp_path: Path) -> Path:
    driver = tmp_path / "runtime_registry_owner_driver.cpp"
    driver.write_text(DRIVER, encoding="utf-8")
    cjson_object = tmp_path / "cJSON.o"
    subprocess.run(
        [
            "cc",
            "-Iengine/third_party/cjson",
            "-c",
            "engine/third_party/cjson/cJSON.c",
            "-o",
            str(cjson_object),
        ],
        cwd=ROOT,
        check=True,
    )
    binary = tmp_path / "runtime_registry_owner"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-I.",
            str(driver),
            "apps/asciicker-presenter/asciiid_application_service.cpp",
            "apps/asciicker-presenter/asciiid_counterfactual.cpp",
            str(cjson_object),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    return binary


def _build_intent_harness(tmp_path: Path) -> Path:
    driver = tmp_path / "runtime_physical_intent_driver.cpp"
    driver.write_text(INTENT_DRIVER, encoding="utf-8")
    cjson_object = tmp_path / "intent_cJSON.o"
    subprocess.run(
        [
            "cc",
            "-Iengine/third_party/cjson",
            "-c",
            "engine/third_party/cjson/cJSON.c",
            "-o",
            str(cjson_object),
        ],
        cwd=ROOT,
        check=True,
    )
    binary = tmp_path / "runtime_physical_intent"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-I.",
            str(driver),
            "apps/asciicker-presenter/asciiid_application_service.cpp",
            "apps/asciicker-presenter/asciiid_counterfactual.cpp",
            str(cjson_object),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    return binary


def _build_document_harness(tmp_path: Path) -> Path:
    driver = tmp_path / "runtime_document_history_driver.cpp"
    driver.write_text(DOCUMENT_DRIVER, encoding="utf-8")
    cjson_object = tmp_path / "document_cJSON.o"
    subprocess.run(
        [
            "cc",
            "-Iengine/third_party/cjson",
            "-c",
            "engine/third_party/cjson/cJSON.c",
            "-o",
            str(cjson_object),
        ],
        cwd=ROOT,
        check=True,
    )
    binary = tmp_path / "runtime_document_history"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-I.",
            str(driver),
            "apps/asciicker-presenter/asciiid_application_service.cpp",
            "apps/asciicker-presenter/asciiid_counterfactual.cpp",
            str(cjson_object),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    return binary


def _fields(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def test_runtime_owns_complete_registry_graph(tmp_path: Path) -> None:
    binary = _build_harness(tmp_path)
    result = subprocess.run(
        [str(binary), "assets/asciiid/ui/asciiid.ui_registry.v1.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fields = _fields(result.stdout)
    assert fields == {
        "load": "1",
        "resolved": "1",
        "handler": "CommitSceneObjectAction",
        "live": "1",
        "retired_registered": "1",
        "retired_live": "0",
        "interaction_live": "1",
        "unknown": "0",
    }


def test_transport_has_no_registry_graph_copy() -> None:
    header = (ROOT / "apps/asciicker-presenter/asciiid_authoring_http.h").read_text(encoding="utf-8")
    source = (ROOT / "apps/asciicker-presenter/asciiid_authoring_http.cpp").read_text(encoding="utf-8")
    assert "LoadRegistryHash" not in header + source
    assert "registered_controls_" not in header + source
    assert "live_controls_" not in header + source
    assert "set_registry_row_is_live" not in header + source
    assert "service->LoadUiRegistryGraph(registry_path, reason)" in source
    assert "service_->RegistryControlRegistered(leaf_id, control_uid)" in source
    assert 'GET /api/v1/runtime-snapshot HTTP/1.1' in source
    assert "service_->CurrentRuntimeSnapshot(session->id)" in source
    assert 'POST /api/v1/runtime-intent HTTP/1.1' in source
    assert "service_->SubmitPhysicalIntent(" in source
    assert "binary_payload" in source
    service_source = (
        ROOT / "apps/asciicker-presenter/asciiid_application_service.cpp"
    ).read_text(encoding="utf-8")
    transport = (
        ROOT / "web/asciiid-authoring-shell/src/transport.ts"
    ).read_text(encoding="utf-8")
    assert "kSceneObjectRoutes" not in service_source
    assert "commitSceneObjectAction" not in transport
    for stale_browser_owner in (
        "commitMeshPlacement", "commitMeshMarqueeSelection", "commitMeshDeletion",
        "commitSpritePlacement", "commitSpriteDeletion", "commitItemPlacement",
        "commitItemDeletion", "commitEnemyGenPlacement", "commitEnemyGenDeletion",
        "commitStoryApply",
    ):
        assert stale_browser_owner not in transport
    assert "FL4588-MESH-CANVAS-CUTOVER: scene.objects.mesh.place" not in service_source
    assert "submitRuntimePhysicalIntent" in transport
    assert "submitRuntimeFileIntent" in transport
    assert "submitRuntimeDocumentDownload" in transport
    assert "ASCIIRF1" in transport
    assert "ASCIIRI1" in transport
    for retired_owner in (
        "/api/v1/document/open", "/api/v1/document/merge",
        "/api/v1/document/download", "/api/v1/history.undo",
        "/api/v1/history.redo", "commitHistoryWrite",
        "commitDocumentAction", "uploadDocument", "downloadDocument",
    ):
        assert retired_owner not in source + transport
    assert 'leaf_id == "workspace.document.new"' not in service_source


def test_runtime_physical_intent_boundary_owns_objects_and_rejections(
    tmp_path: Path,
) -> None:
    binary = _build_intent_harness(tmp_path)
    result = subprocess.run(
        [str(binary), "assets/asciiid/ui/asciiid.ui_registry.v1.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fields = _fields(result.stdout)
    assert fields == {
        "snapshot_ok": "1",
        "snapshot_schema": "asciiid.runtime_snapshot.v1",
        "witness_fields": "13",
        "terrain_control_ok": "1",
        "wrong_terrain_key_reason": "runtime_intent_terrain_control_event_rejected",
        "terrain_runtime_owned": "1",
        "scalar_events_ok": "1",
        "wrong_scalar_button_reason": "runtime_intent_control_event_rejected",
        "const_shape_ok": "1",
        "bool_shape_ok": "1",
        "empty_shape_ok": "1",
        "number_shape_ok": "1",
        "string_shape_ok": "1",
        "selection_shape_ok": "1",
        "selection_toggle_control_ok": "1",
        "selection_toggle_meta_ok": "1",
        "selection_alt_reason": "runtime_intent_control_event_rejected",
        "selection_shift_control_reason": "runtime_intent_control_event_rejected",
        "selection_control_meta_reason": "runtime_intent_control_event_rejected",
        "result_ok": "1",
        "result_schema": "asciiid.runtime_intent_result.v1",
        "committed_leaf": "scene.objects.enemy.max.alive",
        "committed_kind": "set_enemy_generator_template_value",
        "committed_payload": '{"field":"max_alive","value":5}',
        "runtime_transaction": "1",
        "revision_advanced": "1",
        "stale_reason": "runtime_intent_witness_rejected",
        "retired_reason": "runtime_intent_retired_control",
        "unknown_reason": "runtime_intent_unknown_control",
    }


def test_runtime_physical_intent_owns_document_and_history(
    tmp_path: Path,
) -> None:
    binary = _build_document_harness(tmp_path)
    result = subprocess.run(
        [str(binary), "assets/asciiid/ui/asciiid.ui_registry.v1.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert _fields(result.stdout) == {
        "undo_event": "pointer:0",
        "open_event": "file:-1",
        "wrong_event_reason": "runtime_intent_workspace_event_rejected",
        "undo_ok": "1",
        "undo_once": "1",
        "undo_revision": "1",
        "undo_transaction": "1",
        "redo_ok": "1",
        "new_ok": "1",
        "missing_binary_reason": "runtime_intent_document_binary_missing",
        "open_ok": "1",
        "merge_ok": "1",
        "save_ok": "1",
        "save_revision_unchanged": "1",
    }
