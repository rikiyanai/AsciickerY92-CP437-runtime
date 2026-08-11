#include "web_recorder_bridge.h"

#include "game.h"
#include "platform/time_backend.h"
#include "web_diagnostics.h"
#include "web_network_client.h"
#include "web_filesystem.h"
#include "web_platform.h"

// FL-4079: probe needs LoadActorVisualProfileSourceSprite + Sprite::Frame access.
#include "actor_visual_profile_runtime.h"
#include "actor_visual_catalog_source.h"
#include "placed_block_geometry.h"
#include "sprite.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>

// ── Extern declarations for BuildRecorderStateJson ──
extern uint32_t g_fl4131_fallback_render_event_count;
extern uint32_t g_fl4131_fallback_render_last_glyph_id;
extern uint32_t g_fl4131_fallback_render_last_fallback_glyph_id;
extern uint32_t g_fl4131_fallback_render_last_lut_width;
extern uint32_t g_fl4131_fallback_render_last_red_pixels;
extern uint32_t g_fl4131_fallback_render_last_black_pixels;

extern Game* game;
extern Terrain* terrain;
extern World* world;
extern int g_web_render_stage_code;
extern uint32_t g_web_client_render_duration_us;
extern uint32_t g_web_render_interaction_query_duration_us;
extern uint32_t g_web_render_status_bar_duration_us;
extern uint32_t g_web_render_talk_overlay_duration_us;
extern uint32_t g_web_render_player_overlay_duration_us;
extern uint32_t g_web_render_deferred_terrain_dark_duration_us;
extern uint32_t g_web_render_core_world_duration_us;
extern uint32_t g_web_render_weather_duration_us;
extern uint32_t g_web_render_minimap_duration_us;
extern uint32_t g_web_render_api_hook_duration_us;
extern uint32_t g_web_render_remote_duplicate_purge_duration_us;
extern uint32_t g_web_render_remote_duplicate_purge_deleted_count;
extern uint32_t g_web_render_snapshot_npc_visual_lifecycle_duration_us;
extern uint32_t g_web_render_snapshot_npc_visual_lifecycle_slots;
extern uint32_t g_web_render_authoritative_item_appearance_duration_us;
extern uint32_t g_web_render_authoritative_item_appearance_slots;
extern uint32_t g_web_render_frame_input_npc_visual_copy_duration_us;
extern uint32_t g_web_render_frame_input_npc_visual_copy_slots;
extern uint32_t g_web_render_lag_probe_sent_this_frame;
extern uint32_t g_web_render_lag_probe_send_stage_code;
extern uint32_t g_web_render_lag_probe_send_to_render_end_us;
extern uint32_t g_web_render_lag_probe_send_seq;
extern "C" {
    extern uint32_t GameFL933RenderStageAddr32();
    extern uint32_t GameFL933CrossTuProbeAddr32();
    extern uint32_t GameFL933CrossTuProbe(uint32_t salt);
    extern int MainMenuWebGameLoadingState();
    extern int MainMenuWebProgressState();
}

namespace
{
struct RecorderJsonWriter
{
    char* buf;
    int cap;
    int& used;
    int fields;

    RecorderJsonWriter(char* in_buf, int in_cap, int& in_used)
        : buf(in_buf), cap(in_cap), used(in_used), fields(0)
    {
    }

    bool text(const char* fmt, ...)
    {
        if (!buf || cap <= 0)
            return false;
        if (used < 0)
            used = 0;
        if (used >= cap)
        {
            buf[cap - 1] = 0;
            return false;
        }

        va_list ap;
        va_start(ap, fmt);
        int n = vsnprintf(buf + used, cap - used, fmt, ap);
        va_end(ap);
        if (n < 0)
            return false;
        int remain = cap - used;
        if (n >= remain)
        {
            used = cap - 1;
            buf[cap - 1] = 0;
            return false;
        }
        used += n;
        return true;
    }

    bool int_field(const char* key, int value)
    {
        bool ok = text("%s\"%s\":%d", used > 1 ? "," : "", key, value);
        if (ok)
            fields++;
        return ok;
    }

    bool uint_field(const char* key, uint32_t value)
    {
        bool ok = text("%s\"%s\":%u", used > 1 ? "," : "", key, value);
        if (ok)
            fields++;
        return ok;
    }

    bool float_field(const char* key, float value)
    {
        bool ok = false;
        if (!isfinite(value))
            ok = text("%s\"%s\":null", used > 1 ? "," : "", key);
        else
            ok = text("%s\"%s\":%.3f", used > 1 ? "," : "", key, value);
        if (ok)
            fields++;
        return ok;
    }

    bool uint16_array_field(const char* key, const uint16_t* values, int count)
    {
        if (!text("%s\"%s\":[", used > 1 ? "," : "", key))
            return false;
        for (int i = 0; i < count; i++)
        {
            if (!text("%s%u", i > 0 ? "," : "", values ? (uint32_t)values[i] : 0u))
                return false;
        }
        if (!text("]"))
            return false;
        fields++;
        return true;
    }

    bool u8_array_field(const char* key, const uint8_t* values, int count)
    {
        if (!text("%s\"%s\":[", used > 1 ? "," : "", key))
            return false;
        for (int i = 0; i < count; i++)
        {
            if (!text("%s%u", i > 0 ? "," : "", values ? (uint32_t)values[i] : 0u))
                return false;
        }
        if (!text("]"))
            return false;
        fields++;
        return true;
    }
};

static bool AppendObjectIntField(RecorderJsonWriter& w, bool& first, const char* key, int value)
{
    bool ok = w.text("%s\"%s\":%d", first ? "" : ",", key, value);
    if (ok)
        first = false;
    return ok;
}

static bool AppendObjectUIntField(RecorderJsonWriter& w, bool& first, const char* key, uint32_t value)
{
    bool ok = w.text("%s\"%s\":%u", first ? "" : ",", key, value);
    if (ok)
        first = false;
    return ok;
}

static bool AppendObjectUInt16ArrayField(
    RecorderJsonWriter& w, bool& first, const char* key, const uint16_t* values, int count)
{
    if (!w.text("%s\"%s\":[", first ? "" : ",", key))
        return false;
    first = false;
    for (int i = 0; i < count; i++)
    {
        if (!w.text("%s%u", i > 0 ? "," : "", values ? (uint32_t)values[i] : 0u))
            return false;
    }
    return w.text("]");
}

static bool AppendMountedWitnessObject(
    RecorderJsonWriter& w,
    const char* key,
    int mount_state,
    int snapshot_mount_state,
    int life_state,
    int locomotion_state,
    int combat_state,
    uint32_t render_tick,
    uint32_t auth_mount_definition_id,
    const uint16_t* render_slot_kind_ids,
    const uint16_t* render_layer_definition_ids,
    int render_layer_count,
    int render_mount_layer_count,
    int selector_failure_reason,
    int render_presentation_kind_id,
    int render_compose_mode,
    int render_compose_failure_stage,
    int render_ref_alignment_observed,
    int post_interp_has_inst,
    int body_drawn,
    int visible,
    int death_snapshot_count,
    int first_death_snapshot_life_state,
    int first_death_snapshot_mount_state,
    int first_death_snapshot_locomotion_state,
    int first_death_snapshot_presentation_kind_id,
    uint32_t first_death_snapshot_tick,
    uint32_t death_seq,
    uint32_t last_death_source,
    uint32_t respawn_seq,
    int last_death_snapshot_life_state,
    int last_death_snapshot_mount_state,
    int last_death_snapshot_locomotion_state,
    int last_death_snapshot_presentation_kind_id,
    uint32_t last_death_snapshot_tick,
    int death_tick)
{
    if (!w.text("%s\"%s\":{", w.used > 1 ? "," : "", key))
        return false;
    bool first = true;
    if (!AppendObjectIntField(w, first, "mount_state", mount_state))
        return false;
    if (!AppendObjectIntField(w, first, "snapshot_mount_state", snapshot_mount_state))
        return false;
    if (!AppendObjectIntField(w, first, "life_state", life_state))
        return false;
    if (!AppendObjectIntField(w, first, "locomotion_state", locomotion_state))
        return false;
    if (!AppendObjectIntField(w, first, "combat_state", combat_state))
        return false;
    if (!AppendObjectUIntField(w, first, "render_tick", render_tick))
        return false;
    if (!AppendObjectUIntField(w, first, "auth_mount_definition_id", auth_mount_definition_id))
        return false;
    if (!AppendObjectUInt16ArrayField(w, first, "render_slot_kind_ids", render_slot_kind_ids, render_layer_count))
        return false;
    if (!AppendObjectUInt16ArrayField(w, first, "render_layer_definition_ids", render_layer_definition_ids, render_layer_count))
        return false;
    if (!AppendObjectIntField(w, first, "render_mount_layer_count", render_mount_layer_count))
        return false;
    if (!AppendObjectIntField(w, first, "selector_failure_reason", selector_failure_reason))
        return false;
    if (!AppendObjectIntField(w, first, "render_presentation_kind_id", render_presentation_kind_id))
        return false;
    if (!AppendObjectIntField(w, first, "render_compose_mode", render_compose_mode))
        return false;
    if (!AppendObjectIntField(w, first, "render_compose_failure_stage", render_compose_failure_stage))
        return false;
    if (!AppendObjectIntField(w, first, "render_ref_alignment_observed", render_ref_alignment_observed))
        return false;
    if (!AppendObjectIntField(w, first, "post_interp_has_inst", post_interp_has_inst))
        return false;
    if (!AppendObjectIntField(w, first, "body_drawn", body_drawn))
        return false;
    if (!AppendObjectIntField(w, first, "visible", visible))
        return false;
    if (!AppendObjectIntField(w, first, "death_snapshot_count", death_snapshot_count))
        return false;
    if (!AppendObjectIntField(w, first, "first_death_snapshot_life_state", first_death_snapshot_life_state))
        return false;
    if (!AppendObjectIntField(w, first, "first_death_snapshot_mount_state", first_death_snapshot_mount_state))
        return false;
    if (!AppendObjectIntField(w, first, "first_death_snapshot_locomotion_state", first_death_snapshot_locomotion_state))
        return false;
    if (!AppendObjectIntField(w, first, "first_death_snapshot_presentation_kind_id", first_death_snapshot_presentation_kind_id))
        return false;
    if (!AppendObjectUIntField(w, first, "first_death_snapshot_tick", first_death_snapshot_tick))
        return false;
    if (!AppendObjectUIntField(w, first, "death_seq", death_seq))
        return false;
    if (!AppendObjectUIntField(w, first, "last_death_source", last_death_source))
        return false;
    if (!AppendObjectUIntField(w, first, "respawn_seq", respawn_seq))
        return false;
    if (!AppendObjectIntField(w, first, "last_death_snapshot_life_state", last_death_snapshot_life_state))
        return false;
    if (!AppendObjectIntField(w, first, "last_death_snapshot_mount_state", last_death_snapshot_mount_state))
        return false;
    if (!AppendObjectIntField(w, first, "last_death_snapshot_locomotion_state", last_death_snapshot_locomotion_state))
        return false;
    if (!AppendObjectIntField(w, first, "last_death_snapshot_presentation_kind_id", last_death_snapshot_presentation_kind_id))
        return false;
    if (!AppendObjectUIntField(w, first, "last_death_snapshot_tick", last_death_snapshot_tick))
        return false;
    if (!AppendObjectIntField(w, first, "death_tick", death_tick))
        return false;
    if (!w.text("}"))
        return false;
    w.fields++;
    return true;
}

static void AppendFullMountedProofFields(RecorderJsonWriter& w, const WebRecorderBridgeInputs& in)
{
    const Game* game = in.game;
    const MpMoveState* mm = game ? &game->player.mp_move : 0;
    const Server* live_server = in.live_server;
    const Server* snapshot_server = in.snapshot_server;
    const Human* remote0_appearance = in.remote0_appearance;

    const uint16_t* actor_render_layer_definition_ids =
        game ? game->debug.dbg_actor_render_layer_definition_ids : 0;
    const uint16_t* actor_render_slot_kind_ids =
        game ? game->debug.dbg_actor_render_slot_kind_ids : 0;
    const uint16_t* actor_render_item_definition_ids =
        game ? game->debug.dbg_actor_render_item_definition_ids : 0;
    const uint16_t* actor_render_visual_style_ids =
        game ? game->debug.dbg_actor_render_visual_style_ids : 0;
    const uint16_t* actor_render_layer_semantic_contribution_set_indices =
        game ? game->debug.dbg_actor_render_layer_semantic_contribution_set_indices : 0;
    const uint8_t* actor_render_layer_source_layer_indices =
        game ? game->debug.dbg_actor_render_layer_source_layer_indices : 0;
    const uint16_t* actor_render_layer_source_path_hashes =
        game ? game->debug.dbg_actor_render_layer_source_path_hashes : 0;
    const uint16_t* actor_render_layer_visible_cell_counts =
        game ? game->debug.dbg_actor_render_layer_visible_cell_counts : 0;
    const uint16_t* actor_render_layer_contributed_cell_counts =
        game ? game->debug.dbg_actor_render_layer_contributed_cell_counts : 0;
    const uint16_t* actor_render_layer_occluded_cell_counts =
        game ? game->debug.dbg_actor_render_layer_occluded_cell_counts : 0;
    int actor_render_layer_count = game ? game->debug.dbg_actor_render_layer_count : 0;

    w.int_field("actor_life_state", game ? game->debug.dbg_actor_life_state : -1);
    w.int_field("actor_mount_state", game ? game->debug.dbg_actor_mount_state : -1);
    w.float_field("actor_render_dir", game ? game->debug.dbg_actor_render_dir : 0.0f);
    w.int_field("actor_sprite_family_kind", game ? game->debug.dbg_actor_sprite_family_kind : 0);
    w.uint_field("actor_resolved_presentation_key", game ? game->debug.dbg_actor_resolved_presentation_key : 0u);
    w.uint_field("actor_render_presentation_kind_id", game ? (uint32_t)game->debug.dbg_actor_render_presentation_kind_id : 0u);
    w.uint_field("actor_render_skin_definition_id", game ? (uint32_t)game->debug.dbg_actor_render_skin_definition_id : 0u);
    w.uint_field("actor_render_loadout_signature", game ? game->debug.dbg_actor_render_loadout_signature : 0u);
    w.uint_field("actor_render_profile_id_hash", game ? (uint32_t)game->debug.dbg_actor_render_profile_id_hash : 0u);
    w.uint_field("actor_render_atlas_frame_index", game ? (uint32_t)game->debug.dbg_actor_render_atlas_frame_index : 0u);
    w.int_field("actor_render_contribution_angle", game ? game->debug.dbg_actor_render_contribution_angle : 0);
    w.uint_field("actor_render_contribution_projection", game ? (uint32_t)game->debug.dbg_actor_render_contribution_projection : 0u);
    w.uint_field("actor_render_contribution_scope", game ? (uint32_t)game->debug.dbg_actor_render_contribution_scope : 0u);
    w.uint_field("actor_server_visual_key_valid", game ? (uint32_t)game->debug.dbg_actor_server_visual_key_valid : 0u);
    w.uint_field("actor_server_visual_key_hash", game ? game->debug.dbg_actor_server_visual_key_hash : 0u);
    w.uint_field("actor_render_plan_found", game ? (uint32_t)game->debug.dbg_actor_render_plan_found : 0u);
    w.uint_field("actor_render_plan_key_hash", game ? game->debug.dbg_actor_render_plan_key_hash : 0u);
    w.uint_field("actor_render_plan_layer_count", game ? (uint32_t)game->debug.dbg_actor_render_plan_layer_count : 0u);
    w.uint_field("actor_asset_load_failure_count", game ? (uint32_t)game->debug.dbg_actor_asset_load_failure_count : 0u);
    w.uint_field("actor_render_body_layer_definition_id", game ? (uint32_t)game->debug.dbg_actor_render_body_layer_definition_id : 0u);
    w.uint_field("actor_render_head_layer_definition_id", game ? (uint32_t)game->debug.dbg_actor_render_head_layer_definition_id : 0u);
    w.uint_field("actor_render_mount_layer_count", game ? (uint32_t)game->debug.dbg_actor_mount_layer_count : 0u);
    w.uint_field("actor_render_compose_mode", game ? (uint32_t)game->debug.dbg_actor_render_compose_mode : 0u);
    w.uint_field("actor_render_ref_alignment_observed", game ? (uint32_t)game->debug.dbg_actor_render_ref_alignment_observed : 0u);
    w.uint_field("actor_render_ref_alignment_overlay_layer_definition_id", game ? (uint32_t)game->debug.dbg_actor_render_ref_alignment_overlay_layer_definition_id : 0u);
    w.uint_field("actor_render_ref_alignment_overlay_slot_kind_id", game ? (uint32_t)game->debug.dbg_actor_render_ref_alignment_overlay_slot_kind_id : 0u);
    w.int_field("actor_render_ref_alignment_base_ref_z", game ? game->debug.dbg_actor_render_ref_alignment_base_ref_z : 0);
    w.int_field("actor_render_ref_alignment_overlay_ref_z", game ? game->debug.dbg_actor_render_ref_alignment_overlay_ref_z : 0);
    w.uint_field("actor_render_compose_failure_stage", game ? (uint32_t)game->debug.dbg_actor_render_compose_failure_stage : 0u);
    w.uint_field("actor_render_compose_failure_base_layer_definition_id", game ? (uint32_t)game->debug.dbg_actor_render_compose_failure_base_layer_definition_id : 0u);
    w.uint_field("actor_render_compose_failure_overlay_layer_definition_id", game ? (uint32_t)game->debug.dbg_actor_render_compose_failure_overlay_layer_definition_id : 0u);
    w.uint_field("actor_render_compose_failure_overlay_slot_kind_id", game ? (uint32_t)game->debug.dbg_actor_render_compose_failure_overlay_slot_kind_id : 0u);
    w.int_field("actor_render_compose_failure_base_ref_z", game ? game->debug.dbg_actor_render_compose_failure_base_ref_z : 0);
    w.int_field("actor_render_compose_failure_overlay_ref_z", game ? game->debug.dbg_actor_render_compose_failure_overlay_ref_z : 0);
    w.uint_field("actor_render_fail_visible_fallback_used", game ? (uint32_t)game->debug.dbg_actor_render_fail_visible_fallback_used : 0u);
    w.uint_field("actor_render_original_compose_failure_stage", game ? (uint32_t)game->debug.dbg_actor_render_original_compose_failure_stage : 0u);
    w.uint_field("actor_bundle_load_status", game ? (uint32_t)game->debug.dbg_actor_profile_load_status : 0u);
    w.uint_field("actor_bundle_selector_found", game ? (uint32_t)game->debug.dbg_actor_selector_found : 0u);
    w.uint_field("actor_bundle_selector_failure_reason", game ? (uint32_t)game->debug.dbg_actor_selector_failure_reason : 0u);
    w.uint_field("actor_bundle_selector_count", game ? (uint32_t)game->debug.dbg_actor_profile_selector_count : 0u);
    w.uint_field("actor_bundle_layer_count", game ? (uint32_t)game->debug.dbg_actor_profile_layer_count : 0u);
    w.uint_field("actor_profile_load_status", game ? (uint32_t)game->debug.dbg_actor_profile_load_status : 0u);
    w.uint_field("actor_profile_selector_found", game ? (uint32_t)game->debug.dbg_actor_selector_found : 0u);
    w.uint_field("actor_profile_selector_failure_reason", game ? (uint32_t)game->debug.dbg_actor_selector_failure_reason : 0u);
    w.uint_field("actor_profile_selector_count", game ? (uint32_t)game->debug.dbg_actor_profile_selector_count : 0u);
    w.uint_field("actor_profile_layer_count", game ? (uint32_t)game->debug.dbg_actor_profile_layer_count : 0u);
	    w.uint_field("actor_bundle_cache_eviction_count", game ? game->debug.dbg_actor_profile_cache_eviction_count : 0u);
    w.uint_field("actor_bundle_cache_evicted_neutral_count", game ? game->debug.dbg_actor_profile_cache_evicted_neutral_count : 0u);
    w.uint_field("actor_bundle_cache_evicted_enemy_count", game ? game->debug.dbg_actor_profile_cache_evicted_enemy_count : 0u);
    w.uint_field("actor_bundle_cache_hit_count", game ? game->debug.dbg_actor_profile_cache_hit_count : 0u);
    w.uint_field("actor_bundle_cache_null_hit_count", game ? game->debug.dbg_actor_profile_cache_null_hit_count : 0u);
    w.uint_field("actor_bundle_cache_miss_count", game ? game->debug.dbg_actor_profile_cache_miss_count : 0u);
    w.uint_field("actor_bundle_cache_full_count", game ? game->debug.dbg_actor_profile_cache_full_count : 0u);
    w.uint_field("actor_bundle_cache_failure_count", game ? game->debug.dbg_actor_profile_cache_failure_count : 0u);
    w.uint_field("actor_bundle_row_lookup_cache_hit_count", game ? game->debug.dbg_actor_profile_row_lookup_cache_hit_count : 0u);
    w.uint_field("actor_bundle_row_lookup_cache_null_hit_count", game ? game->debug.dbg_actor_profile_row_lookup_cache_null_hit_count : 0u);
	w.uint_field("actor_bundle_row_lookup_cache_miss_count", game ? game->debug.dbg_actor_profile_row_lookup_cache_miss_count : 0u);
	w.uint_field("actor_bundle_row_lookup_table_scan_count", game ? game->debug.dbg_actor_profile_row_lookup_table_scan_count : 0u);
	w.uint_field("actor_visual_resolve_us", game ? game->debug.dbg_actor_visual_resolve_us : 0u);
	w.uint_field("actor_visual_compose_us", game ? game->debug.dbg_actor_visual_compose_us : 0u);
	w.uint_field("actor_visual_runtime_frame_telemetry_us", game ? game->debug.dbg_actor_visual_runtime_frame_telemetry_us : 0u);
	w.int_field("remote_inst_delete_count", game ? game->debug.dbg_remote_inst_delete_count : 0);
    w.int_field("remote_inst_create_count", game ? game->debug.dbg_remote_inst_create_count : 0);
    w.int_field("snapshot_npc_inst_delete_count", game ? game->debug.dbg_snapshot_npc_inst_delete_count : 0);
    w.int_field("snapshot_npc_inst_create_count", game ? game->debug.dbg_snapshot_npc_inst_create_count : 0);
    w.int_field("snapshot_npc_sprite_null_count", game ? game->debug.dbg_snapshot_npc_sprite_null_count : 0);
    w.int_field("snapshot_npc_sprite_miss_total", game ? game->debug.dbg_snapshot_npc_sprite_miss_total : 0);
    w.uint16_array_field("actor_render_layer_definition_ids", actor_render_layer_definition_ids, actor_render_layer_count);
    w.uint16_array_field("actor_render_slot_kind_ids", actor_render_slot_kind_ids, actor_render_layer_count);
    w.uint16_array_field("actor_render_definition_ids", actor_render_item_definition_ids, actor_render_layer_count);
    w.uint16_array_field("actor_render_visual_style_ids", actor_render_visual_style_ids, actor_render_layer_count);
    w.uint16_array_field(
        "actor_render_layer_semantic_contribution_set_indices",
        actor_render_layer_semantic_contribution_set_indices,
        actor_render_layer_count);
    w.u8_array_field(
        "actor_render_layer_source_layer_indices",
        actor_render_layer_source_layer_indices,
        actor_render_layer_count);
    w.uint16_array_field("actor_render_layer_source_path_hashes", actor_render_layer_source_path_hashes, actor_render_layer_count);
    w.uint16_array_field("actor_render_layer_visible_cell_counts", actor_render_layer_visible_cell_counts, actor_render_layer_count);
    w.uint16_array_field("actor_render_layer_contributed_cell_counts", actor_render_layer_contributed_cell_counts, actor_render_layer_count);
    w.uint16_array_field("actor_render_layer_occluded_cell_counts", actor_render_layer_occluded_cell_counts, actor_render_layer_count);
    w.int_field("actor_inst_sprite_family_kind", game ? game->debug.dbg_actor_inst_sprite_family_kind : 0);
    w.int_field("actor_inst_sprite_matches_owner", game ? game->debug.dbg_actor_inst_sprite_matches_owner : 0);
    w.int_field("actor_render_sprite_row_seen", game ? game->debug.dbg_actor_render_sprite_row_seen : 0);
    w.int_field("actor_render_sprite_angle", game ? game->debug.dbg_actor_render_sprite_angle : -1);
    w.int_field("actor_render_sprite_angles", game ? game->debug.dbg_actor_render_sprite_angles : 0);
    w.int_field("actor_render_sprite_anim", game ? game->debug.dbg_actor_render_sprite_anim : -1);
    w.int_field("actor_render_sprite_frame", game ? game->debug.dbg_actor_render_sprite_frame : -1);
    w.int_field("actor_authoritative_tick", game ? game->debug.dbg_actor_authoritative_tick : 0);
    w.int_field("actor_presentation_started_tick", game ? game->debug.dbg_actor_presentation_started_tick : 0);
    w.int_field("actor_playback_elapsed_ticks", game ? game->debug.dbg_actor_playback_elapsed_ticks : 0);
    w.int_field("actor_frame_clamped", game ? game->debug.dbg_actor_frame_clamped : 0);
    w.int_field("actor_frame_changed_expected", game ? game->debug.dbg_actor_frame_changed_expected : 0);
    w.int_field("actor_locomotion_state", game ? game->player.locomotion_state : -1);
    const float actor_auth_vel_x = (mm && mm->has_authoritative_snapshot) ? mm->auth_state.vel[0] : 0.0f;
    const float actor_auth_vel_y = (mm && mm->has_authoritative_snapshot) ? mm->auth_state.vel[1] : 0.0f;
    const float actor_auth_vel_z = (mm && mm->has_authoritative_snapshot) ? mm->auth_state.vel[2] : 0.0f;
    w.float_field("actor_authoritative_vel_x", actor_auth_vel_x);
    w.float_field("actor_authoritative_vel_y", actor_auth_vel_y);
    w.float_field("actor_authoritative_vel_z", actor_auth_vel_z);
    w.float_field("actor_authoritative_planar_speed_sq",
        actor_auth_vel_x * actor_auth_vel_x + actor_auth_vel_y * actor_auth_vel_y);
    // [DEBUG-walk-stuck] runtime evidence for FL-3993 LOCAL walk-stuck hypothesis
    w.int_field("actor_step_phase", game ? game->debug.dbg_actor_step_phase : 0);
    w.int_field("actor_step_phase_div_1024", game ? game->debug.dbg_actor_step_phase_div_1024 : 0);
    AppendMountedWitnessObject(
        w,
        "actor_mounted_witness",
        game ? game->debug.dbg_actor_mount_state : -1,
        game ? game->debug.dbg_actor_mount_state : -1,
        game ? game->debug.dbg_actor_life_state : -1,
        game ? game->player.locomotion_state : -1,
        game ? game->player.combat_state : -1,
        0u,
        game ? (uint32_t)game->player.appearance_v2.mount_definition_id : 0u,
        actor_render_slot_kind_ids,
        actor_render_layer_definition_ids,
        actor_render_layer_count,
        game ? game->debug.dbg_actor_mount_layer_count : 0,
        game ? game->debug.dbg_actor_bundle_selector_failure_reason : 0,
        game ? game->debug.dbg_actor_render_presentation_kind_id : 0,
        game ? game->debug.dbg_actor_render_compose_mode : 0,
        game ? game->debug.dbg_actor_render_compose_failure_stage : 0,
        game ? game->debug.dbg_actor_render_ref_alignment_observed : 0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0u,
        0u,
        0u,
        0u,
        0,
        0,
        0,
        0,
        0u,
        0);

    const uint16_t* remote0_render_layer_definition_ids =
        game ? game->debug.dbg_remote0_render_layer_definition_ids : 0;
    const uint16_t* remote0_render_slot_kind_ids =
        game ? game->debug.dbg_remote0_render_slot_kind_ids : 0;
    const uint16_t* remote0_render_item_definition_ids =
        game ? game->debug.dbg_remote0_render_item_definition_ids : 0;
    const uint16_t* remote0_render_visual_style_ids =
        game ? game->debug.dbg_remote0_render_visual_style_ids : 0;
    const uint16_t* remote0_render_layer_semantic_contribution_set_indices =
        game ? game->debug.dbg_remote0_render_layer_semantic_contribution_set_indices : 0;
    const uint8_t* remote0_render_layer_source_layer_indices =
        game ? game->debug.dbg_remote0_render_layer_source_layer_indices : 0;
    const uint16_t* remote0_render_layer_source_path_hashes =
        game ? game->debug.dbg_remote0_render_layer_source_path_hashes : 0;
    const uint16_t* remote0_render_layer_visible_cell_counts =
        game ? game->debug.dbg_remote0_render_layer_visible_cell_counts : 0;
    const uint16_t* remote0_render_layer_contributed_cell_counts =
        game ? game->debug.dbg_remote0_render_layer_contributed_cell_counts : 0;
    const uint16_t* remote0_render_layer_occluded_cell_counts =
        game ? game->debug.dbg_remote0_render_layer_occluded_cell_counts : 0;
    int remote0_render_layer_count = game ? game->debug.dbg_remote0_render_layer_count : 0;

    w.float_field("remote0_pos_x", game ? game->debug.dbg_last_remote0_pos[0] : 0.0f);
    w.float_field("remote0_pos_y", game ? game->debug.dbg_last_remote0_pos[1] : 0.0f);
    w.float_field("remote0_pos_z", game ? game->debug.dbg_last_remote0_pos[2] : 0.0f);
    w.int_field("remote0_view_x", game ? game->debug.dbg_last_remote0_view_x : -9999);
    w.int_field("remote0_view_y", game ? game->debug.dbg_last_remote0_view_y : -9999);
    w.int_field("remote0_hp", game ? game->debug.dbg_last_remote0_hp : 0);
    w.int_field("remote0_life_state", game ? game->debug.dbg_last_remote0_life_state : -1);
    w.int_field("remote0_mount_state", game ? game->debug.dbg_last_remote0_mount_state : -1);
    w.int_field("remote0_locomotion_state", game ? game->debug.dbg_last_remote0_locomotion_state : -1);
    w.int_field("remote0_combat_state", game ? game->debug.dbg_last_remote0_combat_state : -1);
    w.int_field("remote0_anim", game ? game->debug.dbg_last_remote0_anim : 0);
    w.int_field("remote0_frame", game ? game->debug.dbg_last_remote0_frame : 0);
    w.int_field("remote0_anim_length", game ? game->debug.dbg_last_remote0_anim_length : 0);
    w.int_field("remote0_frame_clamped", game ? game->debug.dbg_last_remote0_frame_clamped : 0);
    w.int_field("remote0_frame_changed_expected", game ? game->debug.dbg_last_remote0_frame_changed_expected : 0);
    w.int_field("remote0_authoritative_tick", game ? game->debug.dbg_last_remote0_authoritative_tick : 0);
    w.int_field("remote0_presentation_started_tick", game ? game->debug.dbg_last_remote0_presentation_started_tick : 0);
    w.int_field("remote0_playback_elapsed_ticks", game ? game->debug.dbg_last_remote0_playback_elapsed_ticks : 0);
    w.int_field("remote0_death_tick", game ? game->debug.dbg_last_remote0_death_tick : 0);
    w.int_field("remote0_corpse_hold_age_ticks", game ? game->debug.dbg_last_remote0_corpse_hold_age_ticks : 0);
    w.int_field("remote0_screen_center_glyph", game ? game->debug.dbg_remote0_screen_center_glyph : -1);
    w.float_field("remote0_render_dir", game ? game->debug.dbg_remote0_render_dir : 0.0f);
    w.int_field("remote0_render_sprite_family_kind", game ? game->debug.dbg_remote0_render_sprite_family_kind : 0);
    w.uint_field("remote0_resolved_presentation_key", game ? game->debug.dbg_remote0_resolved_presentation_key : 0u);
    w.uint_field("remote0_render_presentation_kind_id", game ? (uint32_t)game->debug.dbg_remote0_render_presentation_kind_id : 0u);
    w.int_field("remote0_render_diverged_from_snapshot", game ? game->debug.dbg_last_remote0_render_diverged_from_snapshot : 0);
    w.uint_field("remote0_render_skin_definition_id", game ? (uint32_t)game->debug.dbg_remote0_render_skin_definition_id : 0u);
    w.uint_field("remote0_render_loadout_signature", game ? game->debug.dbg_remote0_render_loadout_signature : 0u);
    w.uint_field("remote0_render_profile_id_hash", game ? (uint32_t)game->debug.dbg_remote0_render_profile_id_hash : 0u);
    w.uint_field("remote0_render_atlas_frame_index", game ? (uint32_t)game->debug.dbg_remote0_render_atlas_frame_index : 0u);
    w.int_field("remote0_render_contribution_angle", game ? game->debug.dbg_remote0_render_contribution_angle : 0);
    w.uint_field("remote0_render_contribution_projection", game ? (uint32_t)game->debug.dbg_remote0_render_contribution_projection : 0u);
    w.uint_field("remote0_render_contribution_scope", game ? (uint32_t)game->debug.dbg_remote0_render_contribution_scope : 0u);
    w.uint_field("remote0_server_visual_key_valid", game ? (uint32_t)game->debug.dbg_remote0_server_visual_key_valid : 0u);
    w.uint_field("remote0_server_visual_key_hash", game ? game->debug.dbg_remote0_server_visual_key_hash : 0u);
    w.uint_field("remote0_render_plan_found", game ? (uint32_t)game->debug.dbg_remote0_render_plan_found : 0u);
    w.uint_field("remote0_render_plan_key_hash", game ? game->debug.dbg_remote0_render_plan_key_hash : 0u);
    w.uint_field("remote0_render_plan_layer_count", game ? (uint32_t)game->debug.dbg_remote0_render_plan_layer_count : 0u);
    w.uint_field("remote0_asset_load_failure_count", game ? (uint32_t)game->debug.dbg_remote0_asset_load_failure_count : 0u);
    w.uint_field("remote0_render_body_layer_definition_id", game ? (uint32_t)game->debug.dbg_remote0_render_body_layer_definition_id : 0u);
    w.uint_field("remote0_render_head_layer_definition_id", game ? (uint32_t)game->debug.dbg_remote0_render_head_layer_definition_id : 0u);
    w.uint_field("remote0_render_mount_layer_count", game ? (uint32_t)game->debug.dbg_remote0_mount_layer_count : 0u);
    w.uint_field("remote0_render_compose_mode", game ? (uint32_t)game->debug.dbg_remote0_render_compose_mode : 0u);
    w.uint_field("remote0_render_ref_alignment_observed", game ? (uint32_t)game->debug.dbg_remote0_render_ref_alignment_observed : 0u);
    w.uint_field("remote0_render_ref_alignment_overlay_layer_definition_id", game ? (uint32_t)game->debug.dbg_remote0_render_ref_alignment_overlay_layer_definition_id : 0u);
    w.uint_field("remote0_render_ref_alignment_overlay_slot_kind_id", game ? (uint32_t)game->debug.dbg_remote0_render_ref_alignment_overlay_slot_kind_id : 0u);
    w.int_field("remote0_render_ref_alignment_base_ref_z", game ? game->debug.dbg_remote0_render_ref_alignment_base_ref_z : 0);
    w.int_field("remote0_render_ref_alignment_overlay_ref_z", game ? game->debug.dbg_remote0_render_ref_alignment_overlay_ref_z : 0);
    w.uint_field("remote0_render_compose_failure_stage", game ? (uint32_t)game->debug.dbg_remote0_render_compose_failure_stage : 0u);
    w.uint_field("remote0_render_compose_failure_base_layer_definition_id", game ? (uint32_t)game->debug.dbg_remote0_render_compose_failure_base_layer_definition_id : 0u);
    w.uint_field("remote0_render_compose_failure_overlay_layer_definition_id", game ? (uint32_t)game->debug.dbg_remote0_render_compose_failure_overlay_layer_definition_id : 0u);
    w.uint_field("remote0_render_compose_failure_overlay_slot_kind_id", game ? (uint32_t)game->debug.dbg_remote0_render_compose_failure_overlay_slot_kind_id : 0u);
    w.int_field("remote0_render_compose_failure_base_ref_z", game ? game->debug.dbg_remote0_render_compose_failure_base_ref_z : 0);
    w.int_field("remote0_render_compose_failure_overlay_ref_z", game ? game->debug.dbg_remote0_render_compose_failure_overlay_ref_z : 0);
    w.uint_field("remote0_render_fail_visible_fallback_used", game ? (uint32_t)game->debug.dbg_remote0_render_fail_visible_fallback_used : 0u);
    w.uint_field("remote0_render_original_compose_failure_stage", game ? (uint32_t)game->debug.dbg_remote0_render_original_compose_failure_stage : 0u);
    w.uint_field("remote0_bundle_load_status", game ? (uint32_t)game->debug.dbg_remote0_profile_load_status : 0u);
    w.uint_field("remote0_bundle_selector_found", game ? (uint32_t)game->debug.dbg_remote0_profile_selector_found : 0u);
    w.uint_field("remote0_bundle_selector_failure_reason", game ? (uint32_t)game->debug.dbg_remote0_profile_selector_failure_reason : 0u);
    w.uint_field("remote0_bundle_selector_count", game ? (uint32_t)game->debug.dbg_remote0_profile_selector_count : 0u);
    w.uint_field("remote0_bundle_layer_count", game ? (uint32_t)game->debug.dbg_remote0_profile_layer_count : 0u);
    w.uint_field("remote0_profile_load_status", game ? (uint32_t)game->debug.dbg_remote0_profile_load_status : 0u);
    w.uint_field("remote0_profile_selector_found", game ? (uint32_t)game->debug.dbg_remote0_profile_selector_found : 0u);
    w.uint_field("remote0_profile_selector_failure_reason", game ? (uint32_t)game->debug.dbg_remote0_profile_selector_failure_reason : 0u);
    w.uint_field("remote0_profile_selector_count", game ? (uint32_t)game->debug.dbg_remote0_profile_selector_count : 0u);
    w.uint_field("remote0_profile_layer_count", game ? (uint32_t)game->debug.dbg_remote0_profile_layer_count : 0u);
    w.uint16_array_field("remote0_render_layer_definition_ids", remote0_render_layer_definition_ids, remote0_render_layer_count);
    w.uint16_array_field("remote0_render_slot_kind_ids", remote0_render_slot_kind_ids, remote0_render_layer_count);
    w.uint16_array_field("remote0_render_definition_ids", remote0_render_item_definition_ids, remote0_render_layer_count);
    w.uint16_array_field("remote0_render_visual_style_ids", remote0_render_visual_style_ids, remote0_render_layer_count);
    w.uint16_array_field(
        "remote0_render_layer_semantic_contribution_set_indices",
        remote0_render_layer_semantic_contribution_set_indices,
        remote0_render_layer_count);
    w.u8_array_field(
        "remote0_render_layer_source_layer_indices",
        remote0_render_layer_source_layer_indices,
        remote0_render_layer_count);
    w.uint16_array_field("remote0_render_layer_source_path_hashes", remote0_render_layer_source_path_hashes, remote0_render_layer_count);
    w.uint16_array_field("remote0_render_layer_visible_cell_counts", remote0_render_layer_visible_cell_counts, remote0_render_layer_count);
    w.uint16_array_field("remote0_render_layer_contributed_cell_counts", remote0_render_layer_contributed_cell_counts, remote0_render_layer_count);
    w.uint16_array_field("remote0_render_layer_occluded_cell_counts", remote0_render_layer_occluded_cell_counts, remote0_render_layer_count);
    w.int_field("remote0_inst_sprite_family_kind", game ? game->debug.dbg_remote0_inst_sprite_family_kind : 0);
    w.int_field("remote0_inst_sprite_matches_owner", game ? game->debug.dbg_remote0_inst_sprite_matches_owner : 0);
    w.int_field("remote0_pid", game ? game->debug.dbg_last_remote0_pid : -1);
    w.uint_field("remote0_appearance_skin_definition_id", remote0_appearance ? (uint32_t)remote0_appearance->appearance_v2.skin_definition_id : 0u);
    w.uint_field("remote0_appearance_mount_definition_id", remote0_appearance ? (uint32_t)remote0_appearance->appearance_v2.mount_definition_id : 0u);
    w.uint_field("remote0_appearance_loadout_revision", remote0_appearance ? remote0_appearance->appearance_v2.loadout_revision : 0u);
    w.uint_field("remote0_appearance_projection_kind", remote0_appearance ? (uint32_t)remote0_appearance->appearance_v2.projection_kind : 0u);
    w.uint_field("remote0_death_stamp_lo", game ? (uint32_t)(game->debug.dbg_last_remote0_death_stamp & 0xFFFFFFFFu) : 0u);
    w.int_field("remote0_would_skip_death_check", game ? game->debug.dbg_last_remote0_would_skip_death_check : 0);
    w.int_field("remote0_in_list", game ? game->debug.dbg_last_remote0_in_list : 0);
    w.int_field("remote0_death_transition_count", game ? game->debug.dbg_last_remote0_death_transition_count : 0);
    w.int_field("remote0_first_death_transition_source", game ? game->debug.dbg_last_remote0_first_death_transition_source : 0);
    w.int_field("remote0_final_label_drawn", game ? game->debug.dbg_last_remote0_final_label_drawn : 0);
    w.int_field("remote0_final_body_drawn", game ? game->debug.dbg_last_remote0_final_body_drawn : 0);
    w.int_field("remote0_final_label_only_drawn", game ? game->debug.dbg_last_remote0_final_label_only_drawn : 0);
    w.int_field("remote0_final_blit_attempted", game ? game->debug.dbg_last_remote0_final_blit_attempted : 0);
    w.int_field("remote0_on_screen", game ? game->debug.dbg_last_remote0_on_screen : 0);
    w.int_field("remote0_has_sprite", game ? game->debug.dbg_last_remote0_has_sprite : 0);
    w.int_field("remote0_has_inst", game ? game->debug.dbg_last_remote0_has_inst : 0);
    w.int_field("remote0_inst_world_match", game ? game->debug.dbg_last_remote0_inst_world_match : 0);
    w.int_field("remote0_inst_visible", game ? game->debug.dbg_last_remote0_inst_visible : 0);
    w.int_field("remote0_query_seen", game ? game->debug.dbg_last_remote0_query_seen : 0);
    w.int_field("remote0_queue_enqueued", game ? game->debug.dbg_last_remote0_queue_enqueued : 0);
    w.int_field("remote0_queue_skip_reason", game ? game->debug.dbg_last_remote0_queue_skip_reason : 0);
    w.int_field("remote0_last_remote_blit_pid", game ? game->debug.dbg_last_remote0_last_remote_blit_pid : -1);
    w.int_field("remote0_last_remote_blit_matches_head", game ? game->debug.dbg_last_remote0_last_remote_blit_matches_head : 0);
    w.int_field("remote0_tracked_buf_blit_invoked", game ? game->debug.dbg_last_remote0_tracked_buf_blit_invoked : 0);
    w.int_field("remote0_tracked_buf_drew_any", game ? game->debug.dbg_last_remote0_tracked_buf_drew_any : 0);
    w.int_field("remote0_blit_clamp_eligible", game ? game->debug.dbg_last_remote0_blit_clamp_eligible : 0);
    w.int_field("remote0_blit_clamp_center_in_bounds", game ? game->debug.dbg_last_remote0_blit_clamp_center_in_bounds : 0);
    w.int_field("remote0_blit_clamp_support_found", game ? game->debug.dbg_last_remote0_blit_clamp_support_found : 0);
    w.int_field("remote0_blit_clamp_support_samples", game ? game->debug.dbg_last_remote0_blit_clamp_support_samples : 0);
    w.int_field("remote0_blit_clamp_applied", game ? game->debug.dbg_last_remote0_blit_clamp_applied : 0);
    w.float_field("remote0_blit_pre_clamp_s_pos_z", game ? game->debug.dbg_last_remote0_blit_pre_clamp_s_pos_z : 0.0f);
    w.float_field("remote0_blit_post_clamp_s_pos_z", game ? game->debug.dbg_last_remote0_blit_post_clamp_s_pos_z : 0.0f);
    w.float_field("remote0_blit_clamp_support_height", game ? game->debug.dbg_last_remote0_blit_clamp_support_height : 0.0f);
    w.float_field("remote0_blit_clamp_floor_z", game ? game->debug.dbg_last_remote0_blit_clamp_floor_z : 0.0f);
    w.int_field("remote0_blit_post_clamp_rewrite", game ? game->debug.dbg_last_remote0_blit_post_clamp_rewrite : 0);
    for (int i = 0; i < 4; i++)
    {
        char name[64];
        snprintf(name, sizeof(name), "remote0_blit_center_sample%d_height", i);
        w.float_field(name, game ? game->debug.dbg_last_remote0_blit_center_sample_height[i] : 0.0f);
        snprintf(name, sizeof(name), "remote0_blit_center_sample%d_spare", i);
        w.int_field(name, game ? game->debug.dbg_last_remote0_blit_center_sample_spare[i] : -1);
        snprintf(name, sizeof(name), "remote0_blit_center_sample%d_valid", i);
        w.int_field(name, game ? game->debug.dbg_last_remote0_blit_center_sample_valid[i] : 0);
    }
    w.float_field("remote0_blit_s_pos_z", game ? game->debug.dbg_last_remote0_blit_s_pos_z : 0.0f);
    w.float_field("remote0_blit_terrain_height_center", game ? game->debug.dbg_last_remote0_blit_terrain_height_center : 0.0f);
    w.int_field("remote0_depth_pass_cells", game ? game->debug.dbg_last_remote0_depth_pass_cells : 0);
    w.int_field("remote0_depth_fail_cells", game ? game->debug.dbg_last_remote0_depth_fail_cells : 0);
    w.int_field("remote0_body_total_cells", game ? game->debug.dbg_last_remote0_body_total_cells : 0);
    w.int_field("remote0_body_visible_cells", game ? game->debug.dbg_last_remote0_body_visible_cells : 0);
    w.int_field("remote0_body_occluded_cells", game ? game->debug.dbg_last_remote0_body_occluded_cells : 0);
    w.float_field("remote0_body_visible_fraction", game ? game->debug.dbg_last_remote0_body_visible_fraction : 0.0f);
    w.int_field("remote0_inst_cookie_match", game ? game->debug.dbg_last_remote0_inst_cookie_match : 0);
    w.int_field("remote0_interp_create_attempts", game ? game->debug.dbg_remote0_interp_create_attempts : 0);
    w.int_field("remote0_interp_create_successes", game ? game->debug.dbg_remote0_interp_create_successes : 0);
    w.int_field("remote0_recreate_attempts", game ? game->debug.dbg_remote0_recreate_attempts : 0);
    w.int_field("remote0_recreate_successes", game ? game->debug.dbg_remote0_recreate_successes : 0);
    w.int_field("remote0_post_interp_has_inst", game ? game->debug.dbg_last_remote0_post_interp_has_inst : 0);
    w.int_field("remote0_post_interp_inst_world_match", game ? game->debug.dbg_last_remote0_post_interp_inst_world_match : 0);
    w.float_field("remote0_post_interp_pos_x", game ? game->debug.dbg_last_remote0_post_interp_pos[0] : 0.0f);
    w.float_field("remote0_post_interp_pos_y", game ? game->debug.dbg_last_remote0_post_interp_pos[1] : 0.0f);
    w.float_field("remote0_post_interp_pos_z", game ? game->debug.dbg_last_remote0_post_interp_pos[2] : 0.0f);
    w.int_field("remote0_post_interp_view_x", game ? game->debug.dbg_last_remote0_post_interp_view_x : -9999);
    w.int_field("remote0_post_interp_view_y", game ? game->debug.dbg_last_remote0_post_interp_view_y : -9999);
    w.int_field("remote0_post_interp_on_screen", game ? game->debug.dbg_last_remote0_post_interp_on_screen : 0);
    w.int_field("remote0_post_interp_label_visible", game ? game->debug.dbg_last_remote0_post_interp_label_visible : 0);
    w.int_field("remote0_post_interp_body_visible", game ? game->debug.dbg_last_remote0_post_interp_body_visible : 0);
    w.int_field("remote0_post_interp_label_only", game ? game->debug.dbg_last_remote0_post_interp_label_only : 0);
    w.int_field("dbg_remote0_interp_active", game ? game->debug.dbg_last_remote0_interp_active : 0);
    w.int_field("dbg_remote0_interp_ring_depth", game ? game->debug.dbg_last_remote0_interp_ring_depth : 0);
    w.float_field("dbg_remote0_interp_delay_ms", game ? game->debug.dbg_last_remote0_interp_delay_ms : 0.0f);
    w.float_field("dbg_remote0_interp_lerp_t", game ? game->debug.dbg_last_remote0_interp_lerp_t : 0.0f);
    w.int_field("dbg_remote0_interp_fallback_mode", game ? game->debug.dbg_last_remote0_interp_fallback_mode : 0);
    w.int_field("dbg_remote0_interp_join_flush_fired", game ? game->debug.dbg_last_remote0_interp_join_flush_fired : 0);
    w.int_field("dbg_remote0_interp_newest_tick", game ? game->debug.dbg_last_remote0_interp_newest_tick : 0);
    w.int_field("dbg_remote0_interp_older_tick", game ? game->debug.dbg_last_remote0_interp_older_tick : 0);
    w.float_field("dbg_remote0_interp_newest_wall_age_ms", game ? game->debug.dbg_last_remote0_interp_newest_wall_age_ms : -1.0f);
    w.float_field("dbg_remote0_interp_older_wall_age_ms", game ? game->debug.dbg_last_remote0_interp_older_wall_age_ms : -1.0f);
    w.float_field("dbg_remote0_interp_target_age_ms", game ? game->debug.dbg_last_remote0_interp_target_age_ms : 0.0f);
    w.int_field("visible_remote", game ? game->debug.dbg_last_visible_remote_players : 0);
    w.int_field("visible_local", game ? game->debug.dbg_last_visible_local_players : 0);
    w.int_field("body_visible_remote", game ? game->debug.dbg_last_visible_remote_body_players : 0);
    w.int_field("body_visible_local", game ? game->debug.dbg_last_visible_local_body_players : 0);
    w.int_field("actor_final_body_drawn", game ? game->debug.dbg_last_actor_final_body_drawn : 0);
    w.int_field("local_body_visible_proxy", game ? game->debug.dbg_last_visible_local_body_players : 0);
    w.int_field("label_only_remote", game ? game->debug.dbg_last_visible_remote_label_only_players : 0);
    w.int_field("render_linked_remote_count", game ? game->debug.dbg_last_render_linked_remote_count : 0);
    w.int_field("render_local_seen", game ? game->debug.dbg_last_render_local_seen : 0);
    w.int_field("render_remote0_seen", game ? game->debug.dbg_last_render_remote0_seen : 0);

    int server_linked_remote_count = 0;
    if (live_server)
    {
        for (Human* h = live_server->authority.head; h; h = (Human*)h->next)
        {
            if (!game || h != &game->player)
                server_linked_remote_count++;
        }
    }
    w.int_field("server_linked_remote_count", server_linked_remote_count);
    w.uint_field("server_tick", live_server ? live_server->authority.snapshot_client.last_snapshot_tick : 0u);
    w.int_field("server_local_id", live_server ? live_server->connection.local_id : -99);
    w.uint_field("snapshot_packets_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_packets : 0u);
    w.uint_field("snapshot_ack_packets_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_ack_packets : 0u);
    w.uint_field("snapshot_last_ack_seq_cpp", snapshot_server ? (uint32_t)snapshot_server->authority.snapshot_client.last_snapshot_ack_seq : 0u);
    w.uint_field("snapshot_last_ack_tick_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.last_snapshot_ack_tick : 0u);
    w.uint_field("snapshot_last_entity_count_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_last_entity_count : 0u);
    w.int_field("snapshot_last_is_delta_cpp", snapshot_server ? (int)snapshot_server->authority.snapshot_client.snapshot_last_is_delta : 0);
    w.int_field("snapshot_local_present_cpp", snapshot_server ? (int)snapshot_server->authority.snapshot_client.snapshot_last_local_present : 0);
    w.int_field("snapshot_local_pose_sane_cpp", snapshot_server ? (int)snapshot_server->authority.snapshot_client.snapshot_last_local_pose_sane : 0);
    w.int_field("snapshot_local_applied_cpp", snapshot_server ? (int)snapshot_server->authority.snapshot_client.snapshot_last_local_applied : 0);
    w.uint_field("snapshot_local_apply_reason_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_last_local_apply_reason : 0u);
    w.uint_field("snapshot_local_entity_id_cpp", snapshot_server ? (uint32_t)snapshot_server->authority.snapshot_client.snapshot_last_local_entity_id : 0xffffu);
    w.float_field("snapshot_local_pos_x_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_last_local_pos[0] : 0.0f);
    w.float_field("snapshot_local_pos_y_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_last_local_pos[1] : 0.0f);
    w.float_field("snapshot_local_pos_z_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_last_local_pos[2] : 0.0f);
    w.uint_field("snapshot_local_support_valid_cpp", snapshot_server ? (uint32_t)snapshot_server->authority.snapshot_client.snapshot_last_local_support_valid : 0u);
    w.uint_field("snapshot_local_support_source_cpp", snapshot_server ? (uint32_t)snapshot_server->authority.snapshot_client.snapshot_last_local_support_source : 0u);
    w.uint_field("snapshot_local_support_item_id_cpp", snapshot_server ? (uint32_t)snapshot_server->authority.snapshot_client.snapshot_last_local_support_item_id : 0u);
    w.float_field("snapshot_local_support_z_cpp", snapshot_server ? snapshot_server->authority.snapshot_client.snapshot_last_local_support_z : 0.0f);
    w.int_field("proc_opcode_p", game ? game->debug.dbg_proc_opcode_p : 0);
    w.int_field("proc_opcode_b", game ? game->debug.dbg_proc_opcode_b : 0);
    w.int_field("proc_opcode_q", game ? game->debug.dbg_proc_opcode_q : 0);
    w.int_field("proc_opcode_other", game ? game->debug.dbg_proc_opcode_other : 0);
    w.int_field("remote0_snapshot_life_state", game ? game->debug.dbg_remote0_snapshot_life_state : -1);
    w.int_field("remote0_snapshot_mount_state", game ? game->debug.dbg_remote0_snapshot_mount_state : -1);
    w.int_field("remote0_snapshot_locomotion_state", game ? game->debug.dbg_remote0_snapshot_locomotion_state : -1);
    // FL-3254: combat_state is required by mounted attack/death watchdog gate
    w.int_field("remote0_snapshot_combat_state", game ? game->debug.dbg_remote0_snapshot_combat_state : -1);
    // FL-2957: terrain_z from snapshot for floor coherence proof
    w.float_field("remote0_snapshot_terrain_z", game ? game->debug.dbg_remote0_snapshot_terrain_z : 0.0f);
    w.int_field("remote0_snapshot_presentation_kind_id", game ? game->debug.dbg_remote0_snapshot_presentation_kind_id : 0);
    w.uint_field("remote0_snapshot_tick", game ? game->debug.dbg_remote0_snapshot_tick : 0u);
    w.uint_field("remote0_entity_tick", game ? game->debug.dbg_remote0_entity_tick : 0u);
    w.int_field("remote0_death_snapshot_count", game ? game->debug.dbg_last_remote0_death_snapshot_count : 0);
    w.int_field("remote0_first_death_snapshot_life_state", game ? game->debug.dbg_last_remote0_first_death_snapshot_life_state : -1);
    w.int_field("remote0_first_death_snapshot_mount_state", game ? game->debug.dbg_last_remote0_first_death_snapshot_mount_state : -1);
    w.int_field("remote0_first_death_snapshot_locomotion_state", game ? game->debug.dbg_last_remote0_first_death_snapshot_locomotion_state : -1);
    w.int_field("remote0_first_death_snapshot_presentation_kind_id", game ? game->debug.dbg_last_remote0_first_death_snapshot_presentation_kind_id : 0);
    w.uint_field("remote0_first_death_snapshot_tick", game ? game->debug.dbg_last_remote0_first_death_snapshot_tick : 0u);
    w.int_field("remote0_last_death_snapshot_life_state", game ? game->debug.dbg_last_remote0_last_death_snapshot_life_state : -1);
    w.int_field("remote0_last_death_snapshot_mount_state", game ? game->debug.dbg_last_remote0_last_death_snapshot_mount_state : -1);
    w.int_field("remote0_last_death_snapshot_locomotion_state", game ? game->debug.dbg_last_remote0_last_death_snapshot_locomotion_state : -1);
    w.int_field("remote0_last_death_snapshot_presentation_kind_id", game ? game->debug.dbg_last_remote0_last_death_snapshot_presentation_kind_id : 0);
    w.uint_field("remote0_last_death_snapshot_tick", game ? game->debug.dbg_last_remote0_last_death_snapshot_tick : 0u);
    AppendMountedWitnessObject(
        w,
        "remote0_mounted_witness",
        game ? game->debug.dbg_last_remote0_mount_state : -1,
        game ? game->debug.dbg_remote0_snapshot_mount_state : -1,
        game ? game->debug.dbg_last_remote0_life_state : -1,
        game ? game->debug.dbg_last_remote0_locomotion_state : -1,
        game ? game->debug.dbg_last_remote0_combat_state : -1,
        live_server ? live_server->authority.snapshot_client.last_snapshot_tick : 0u,
        remote0_appearance ? (uint32_t)remote0_appearance->appearance_v2.mount_definition_id : 0u,
        remote0_render_slot_kind_ids,
        remote0_render_layer_definition_ids,
        remote0_render_layer_count,
        game ? game->debug.dbg_remote0_mount_layer_count : 0,
        game ? game->debug.dbg_remote0_bundle_selector_failure_reason : 0,
        game ? game->debug.dbg_remote0_render_presentation_kind_id : 0,
        game ? game->debug.dbg_remote0_render_compose_mode : 0,
        game ? game->debug.dbg_remote0_render_compose_failure_stage : 0,
        game ? game->debug.dbg_remote0_render_ref_alignment_observed : 0,
        game ? game->debug.dbg_last_remote0_post_interp_has_inst : 0,
        game ? game->debug.dbg_last_remote0_final_body_drawn : 0,
        game ? game->debug.dbg_last_visible_remote_players : 0,
        game ? game->debug.dbg_last_remote0_death_snapshot_count : 0,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_life_state : -1,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_mount_state : -1,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_locomotion_state : -1,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_presentation_kind_id : 0,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_tick : 0u,
        game ? game->debug.dbg_last_remote0_death_seq : 0u,
        game ? game->debug.dbg_last_remote0_last_death_source : 0u,
        game ? game->debug.dbg_last_remote0_respawn_seq : 0u,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_life_state : -1,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_mount_state : -1,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_locomotion_state : -1,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_presentation_kind_id : 0,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_tick : 0u,
        game ? game->debug.dbg_last_remote0_death_tick : 0);
}

static void AppendMinimalMountedProofFields(RecorderJsonWriter& w, const WebRecorderBridgeInputs& in)
{
    const Game* game = in.game;
    const Server* live_server = in.live_server;
    const Human* remote0_appearance = in.remote0_appearance;

    w.int_field("actor_life_state", game ? game->debug.dbg_actor_life_state : -1);
    w.int_field("actor_mount_state", game ? game->debug.dbg_actor_mount_state : -1);
    w.uint_field("actor_render_presentation_kind_id", game ? (uint32_t)game->debug.dbg_actor_render_presentation_kind_id : 0u);
    w.uint_field("actor_render_compose_mode", game ? (uint32_t)game->debug.dbg_actor_render_compose_mode : 0u);
    w.uint_field("actor_render_compose_failure_stage", game ? (uint32_t)game->debug.dbg_actor_render_compose_failure_stage : 0u);
    w.uint_field("actor_render_ref_alignment_observed", game ? (uint32_t)game->debug.dbg_actor_render_ref_alignment_observed : 0u);
    w.uint_field("actor_bundle_selector_failure_reason", game ? (uint32_t)game->debug.dbg_actor_bundle_selector_failure_reason : 0u);

    w.int_field("remote0_life_state", game ? game->debug.dbg_last_remote0_life_state : -1);
    w.int_field("remote0_mount_state", game ? game->debug.dbg_last_remote0_mount_state : -1);
    w.int_field("remote0_snapshot_life_state", game ? game->debug.dbg_remote0_snapshot_life_state : -1);
    w.int_field("remote0_snapshot_mount_state", game ? game->debug.dbg_remote0_snapshot_mount_state : -1);
    w.int_field("remote0_snapshot_combat_state", game ? game->debug.dbg_remote0_snapshot_combat_state : -1);
    w.int_field("remote0_snapshot_presentation_kind_id", game ? game->debug.dbg_remote0_snapshot_presentation_kind_id : 0);
    w.uint_field("remote0_render_presentation_kind_id", game ? (uint32_t)game->debug.dbg_remote0_render_presentation_kind_id : 0u);
    w.uint_field("remote0_render_compose_mode", game ? (uint32_t)game->debug.dbg_remote0_render_compose_mode : 0u);
    w.uint_field("remote0_render_compose_failure_stage", game ? (uint32_t)game->debug.dbg_remote0_render_compose_failure_stage : 0u);
    w.uint_field("remote0_render_ref_alignment_observed", game ? (uint32_t)game->debug.dbg_remote0_render_ref_alignment_observed : 0u);
    w.uint_field("remote0_bundle_selector_failure_reason", game ? (uint32_t)game->debug.dbg_remote0_bundle_selector_failure_reason : 0u);
    w.uint_field("remote0_appearance_mount_definition_id", remote0_appearance ? (uint32_t)remote0_appearance->appearance_v2.mount_definition_id : 0u);
    w.int_field("remote0_post_interp_has_inst", game ? game->debug.dbg_last_remote0_post_interp_has_inst : 0);
    w.int_field("remote0_final_body_drawn", game ? game->debug.dbg_last_remote0_final_body_drawn : 0);
    w.int_field("remote0_on_screen", game ? game->debug.dbg_last_remote0_on_screen : 0);
    w.int_field("visible_remote", game ? game->debug.dbg_last_visible_remote_players : 0);
    w.int_field("body_visible_remote", game ? game->debug.dbg_last_visible_remote_body_players : 0);
    w.int_field("remote0_death_tick", game ? game->debug.dbg_last_remote0_death_tick : 0);
    w.int_field("remote0_authoritative_tick", game ? game->debug.dbg_last_remote0_authoritative_tick : 0);
    w.int_field("remote0_presentation_started_tick", game ? game->debug.dbg_last_remote0_presentation_started_tick : 0);
    w.int_field("remote0_playback_elapsed_ticks", game ? game->debug.dbg_last_remote0_playback_elapsed_ticks : 0);
    w.int_field("remote0_death_snapshot_count", game ? game->debug.dbg_last_remote0_death_snapshot_count : 0);
    w.int_field("remote0_first_death_snapshot_mount_state", game ? game->debug.dbg_last_remote0_first_death_snapshot_mount_state : -1);
    w.int_field("remote0_last_death_snapshot_mount_state", game ? game->debug.dbg_last_remote0_last_death_snapshot_mount_state : -1);
    w.int_field("server_linked_remote_count", live_server ? 1 : 0);
    w.uint_field("server_tick", live_server ? live_server->authority.snapshot_client.last_snapshot_tick : 0u);
    w.int_field("server_local_id", live_server ? live_server->connection.local_id : -99);
    AppendMountedWitnessObject(
        w,
        "actor_mounted_witness",
        game ? game->debug.dbg_actor_mount_state : -1,
        game ? game->debug.dbg_actor_mount_state : -1,
        game ? game->debug.dbg_actor_life_state : -1,
        game ? game->player.locomotion_state : -1,
        game ? game->player.combat_state : -1,
        0u,
        game ? (uint32_t)game->player.appearance_v2.mount_definition_id : 0u,
        game ? game->debug.dbg_actor_render_slot_kind_ids : 0,
        game ? game->debug.dbg_actor_render_layer_definition_ids : 0,
        game ? game->debug.dbg_actor_render_layer_count : 0,
        game ? game->debug.dbg_actor_mount_layer_count : 0,
        game ? game->debug.dbg_actor_bundle_selector_failure_reason : 0,
        game ? game->debug.dbg_actor_render_presentation_kind_id : 0,
        game ? game->debug.dbg_actor_render_compose_mode : 0,
        game ? game->debug.dbg_actor_render_compose_failure_stage : 0,
        game ? game->debug.dbg_actor_render_ref_alignment_observed : 0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0u,
        0u,
        0u,
        0u,
        0,
        0,
        0,
        0,
        0u,
        0);
    AppendMountedWitnessObject(
        w,
        "remote0_mounted_witness",
        game ? game->debug.dbg_last_remote0_mount_state : -1,
        game ? game->debug.dbg_remote0_snapshot_mount_state : -1,
        game ? game->debug.dbg_last_remote0_life_state : -1,
        game ? game->debug.dbg_last_remote0_locomotion_state : -1,
        game ? game->debug.dbg_last_remote0_combat_state : -1,
        live_server ? live_server->authority.snapshot_client.last_snapshot_tick : 0u,
        remote0_appearance ? (uint32_t)remote0_appearance->appearance_v2.mount_definition_id : 0u,
        game ? game->debug.dbg_remote0_render_slot_kind_ids : 0,
        game ? game->debug.dbg_remote0_render_layer_definition_ids : 0,
        game ? game->debug.dbg_remote0_render_layer_count : 0,
        game ? game->debug.dbg_remote0_mount_layer_count : 0,
        game ? game->debug.dbg_remote0_bundle_selector_failure_reason : 0,
        game ? game->debug.dbg_remote0_render_presentation_kind_id : 0,
        game ? game->debug.dbg_remote0_render_compose_mode : 0,
        game ? game->debug.dbg_remote0_render_compose_failure_stage : 0,
        game ? game->debug.dbg_remote0_render_ref_alignment_observed : 0,
        game ? game->debug.dbg_last_remote0_post_interp_has_inst : 0,
        game ? game->debug.dbg_last_remote0_final_body_drawn : 0,
        game ? game->debug.dbg_last_visible_remote_players : 0,
        game ? game->debug.dbg_last_remote0_death_snapshot_count : 0,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_life_state : -1,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_mount_state : -1,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_locomotion_state : -1,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_presentation_kind_id : 0,
        game ? game->debug.dbg_last_remote0_first_death_snapshot_tick : 0u,
        game ? game->debug.dbg_last_remote0_death_seq : 0u,
        game ? game->debug.dbg_last_remote0_last_death_source : 0u,
        game ? game->debug.dbg_last_remote0_respawn_seq : 0u,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_life_state : -1,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_mount_state : -1,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_locomotion_state : -1,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_presentation_kind_id : 0,
        game ? game->debug.dbg_last_remote0_last_death_snapshot_tick : 0u,
        game ? game->debug.dbg_last_remote0_death_tick : 0);
}
} // namespace

WebRecorderBridgeMode WebRecorderBridgeClampMode(int raw_mode)
{
    if (raw_mode <= WEB_RECORDER_BRIDGE_MODE_FULL)
        return WEB_RECORDER_BRIDGE_MODE_FULL;
    if (raw_mode == WEB_RECORDER_BRIDGE_MODE_MINIMAL)
        return WEB_RECORDER_BRIDGE_MODE_MINIMAL;
    return WEB_RECORDER_BRIDGE_MODE_NONE;
}

WebRecorderBridgeStats WebRecorderBridgeAppendMountedProofFields(
    char* buf,
    int cap,
    int& used,
    WebRecorderBridgeMode mode,
    const WebRecorderBridgeInputs& inputs)
{
    WebRecorderBridgeStats stats = {};
    stats.mode = (int)mode;
    stats.field_count = 0;
    stats.bytes_appended = 0;
    stats.publish_duration_us = 0;

    int used_before = used;
    const uint64_t publish_begin_us = a3dGetTime();
    RecorderJsonWriter writer(buf, cap, used);
    switch (mode)
    {
        case WEB_RECORDER_BRIDGE_MODE_FULL:
            AppendFullMountedProofFields(writer, inputs);
            break;
        case WEB_RECORDER_BRIDGE_MODE_MINIMAL:
            AppendMinimalMountedProofFields(writer, inputs);
            break;
        case WEB_RECORDER_BRIDGE_MODE_NONE:
        default:
            break;
    }

    stats.field_count = writer.fields;
    stats.bytes_appended = used - used_before;
    const uint64_t publish_end_us = a3dGetTime();
    stats.publish_duration_us =
        (publish_end_us >= publish_begin_us)
            ? (int)(publish_end_us - publish_begin_us)
            : 0;
    return stats;
}
// ── BuildRecorderStateJson ──

const char* BuildRecorderStateJson(
    char* buf, int cap,
    const Game* game,
    const Server* server,
    const Server* alloc_server)
{
    const Server* s = server ? server : alloc_server;
    const MpMoveState* mm = game ? &game->player.mp_move : 0;
    // All submodule data is read through their public APIs:
    //   WebDiagnostics*, WebCountEquippedLocalItems, etc.
    //   WebFL933ServerCanariesOk from web_network_client
    //   web_filesystem.h externs for bundle/ids hashes
    //   extern g_web_* globals from web_network_client.h
    int used = 0;
    buf[0] = '{';
    buf[1] = 0;
    used = 1;

        if (!s)
            WebLogServerNullAfterJoin("RecorderStateJson", 0, 0);
        MaybeLoadAppearanceContractHashes();
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "recorder_probe_schema_version", 2u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "recorder_presentation_probe_contract_version", 2u);
        const Human* remote0_appearance = (s && game && game->debug.dbg_last_remote0_pid >= 0 && game->debug.dbg_last_remote0_pid < s->connection.max_clients)
            ? &s->authority.others[game->debug.dbg_last_remote0_pid]
            : 0;
        const WebDiagnosticsLifecycleSnapshot* lifecycle = WebDiagnosticsGetLifecycleSnapshot();
        const WebDiagnosticsServerLossProvenanceSnapshot* server_loss = WebDiagnosticsGetServerLossProvenanceSnapshot();
        const uint8_t* render_buf_center_fg = WebDiagnosticsGetRenderBufCenterFg();
        const uint8_t* render_buf_center_bk = WebDiagnosticsGetRenderBufCenterBk();
        const uint8_t* render_buf_center_gl = WebDiagnosticsGetRenderBufCenterGl();
        const uint8_t* render_buf_center_spare = WebDiagnosticsGetRenderBufCenterSpare();
        auto append_diag_surface = [&](const char* prefix, const MpMoveDiagSurface* surface) -> void
        {
            char name[64];
            snprintf(name, sizeof(name), "%s_valid", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name,
                               (surface && surface->valid) ? 1 : 0);
            snprintf(name, sizeof(name), "%s_seq", prefix);
            WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, name,
                                (surface && surface->valid) ? (uint32_t)surface->seq : 0u);
            snprintf(name, sizeof(name), "%s_mount", prefix);
            WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, name,
                                (surface && surface->valid) ? (uint32_t)surface->mount : 0u);
            snprintf(name, sizeof(name), "%s_grounded", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name,
                               (surface && surface->valid) ? (int)surface->grounded : 0);
            snprintf(name, sizeof(name), "%s_pos_x", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.pos[0] : 0.0f);
            snprintf(name, sizeof(name), "%s_pos_y", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.pos[1] : 0.0f);
            snprintf(name, sizeof(name), "%s_pos_z", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.pos[2] : 0.0f);
            snprintf(name, sizeof(name), "%s_vel_x", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.vel[0] : 0.0f);
            snprintf(name, sizeof(name), "%s_vel_y", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.vel[1] : 0.0f);
            snprintf(name, sizeof(name), "%s_vel_z", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.vel[2] : 0.0f);
            snprintf(name, sizeof(name), "%s_yaw", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.yaw : 0.0f);
            snprintf(name, sizeof(name), "%s_yaw_vel", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.yaw_vel : 0.0f);
            snprintf(name, sizeof(name), "%s_slope", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name,
                                 (surface && surface->valid) ? surface->step.slope : 0.0f);
        };
        auto append_diag_quantized = [&](const char* prefix, bool valid, uint16_t seq, const MpMoveQuantizedInput* input) -> void
        {
            char name[64];
            snprintf(name, sizeof(name), "%s_valid", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name, valid ? 1 : 0);
            snprintf(name, sizeof(name), "%s_seq", prefix);
            WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, name, valid ? (uint32_t)seq : 0u);
            snprintf(name, sizeof(name), "%s_move_x", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name, valid ? (int)input->move_x : 0);
            snprintf(name, sizeof(name), "%s_move_y", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name, valid ? (int)input->move_y : 0);
            snprintf(name, sizeof(name), "%s_move_z", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name, valid ? (int)input->move_z : 0);
            snprintf(name, sizeof(name), "%s_yaw100", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name, valid ? (int)input->yaw100 : 0);
            snprintf(name, sizeof(name), "%s_flags", prefix);
            WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, name, valid ? (uint32_t)input->flags : 0u);
        };
        auto append_diag_error = [&](const char* prefix, const float pos[3], const float vel[3],
                                     float yaw, float yaw_vel, float slope, int grounded) -> void
        {
            char name[64];
            snprintf(name, sizeof(name), "%s_pos_x", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, pos ? pos[0] : 0.0f);
            snprintf(name, sizeof(name), "%s_pos_y", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, pos ? pos[1] : 0.0f);
            snprintf(name, sizeof(name), "%s_pos_z", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, pos ? pos[2] : 0.0f);
            snprintf(name, sizeof(name), "%s_vel_x", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, vel ? vel[0] : 0.0f);
            snprintf(name, sizeof(name), "%s_vel_y", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, vel ? vel[1] : 0.0f);
            snprintf(name, sizeof(name), "%s_vel_z", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, vel ? vel[2] : 0.0f);
            snprintf(name, sizeof(name), "%s_yaw", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, yaw);
            snprintf(name, sizeof(name), "%s_yaw_vel", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, yaw_vel);
            snprintf(name, sizeof(name), "%s_slope", prefix);
            WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, name, slope);
            snprintf(name, sizeof(name), "%s_grounded", prefix);
            WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, name, grounded);
        };

        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_pos_x", game ? game->debug.dbg_last_local_pos_x : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_pos_y", game ? game->debug.dbg_last_local_pos_y : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_pos_z", game ? game->debug.dbg_last_local_pos_z : 0.0f);
        // FL-4137 mobile-proof helper: player projected to screen cell coords
        // (set by game_render_bridge each frame via ProjectCoords). The mobile
        // double-tap proof reads these to tap the player's actual screen rect.
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_screen_col", game ? (int)game->debug.dbg_local_screen_col : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_screen_row", game ? (int)game->debug.dbg_local_screen_row : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_screen_valid", game ? (int)game->debug.dbg_local_screen_valid : 0);
        // FL-4137 behavior 6: pickup-strip cell rect for the click-pickup proof.
        // Published every frame from inventory_view; values are cell-space in
        // the 160x90 grid. Strip slot i spans [items_xarr[i] .. items_xarr[i+1]]
        // in x, and [items_ylo .. items_yhi] in y.
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pickup_strip_ylo", game ? game->inventory_view.items_ylo : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pickup_strip_yhi", game ? game->inventory_view.items_yhi : 0);
        if (used < (int)cap)
            used += snprintf(buf + used, (size_t)(cap - used), ",\"pickup_strip_xarr\":[");
        for (int psi = 0; psi < 10; psi++)
        {
            if (used < (int)cap)
                used += snprintf(buf + used, (size_t)(cap - used), "%s%d",
                                 psi ? "," : "",
                                 game ? game->inventory_view.items_xarr[psi] : 0);
        }
        if (used < (int)cap)
            used += snprintf(buf + used, (size_t)(cap - used), "]");
        // FL-2957: support recurrence telemetry is server-owned. The browser-side
        // Server facade has connection/authority client state, not ServerState::players;
        // row-level support proof currently comes from server.log [tick-player-step].
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_support_retry_rebuilt", 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_support_retry_found",   0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_step_once_us",         0u);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_step_pos_z",           0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_step_terrain_z",       0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_visual_pos_x", game ? game->debug.dbg_last_local_visual_pos_x : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_visual_pos_y", game ? game->debug.dbg_last_local_visual_pos_y : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_visual_pos_z", game ? game->debug.dbg_last_local_visual_pos_z : 0.0f);
        WebDiagnosticsTerrainSample local_terrain = WebDiagnosticsSampleTerrainAt(game ? game->debug.dbg_last_local_pos_x : 0.0f,
                                                                       game ? game->debug.dbg_last_local_pos_y : 0.0f);
        WebDiagnosticsTerrainSample snapshot_local_terrain = WebDiagnosticsSampleTerrainAt(
            (s && s->authority.snapshot_client.snapshot_last_local_present) ? s->authority.snapshot_client.snapshot_last_local_pos[0] : 0.0f,
            (s && s->authority.snapshot_client.snapshot_last_local_present) ? s->authority.snapshot_client.snapshot_last_local_pos[1] : 0.0f);
        WebDiagnosticsTerrainSample origin_terrain = WebDiagnosticsSampleTerrainAt(0.0f, 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_terrain_sample_valid", local_terrain.valid);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_terrain_patch_present", local_terrain.patch_present);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_terrain_height_raw", local_terrain.height_raw);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_terrain_material_id", local_terrain.material_id);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "snapshot_local_terrain_sample_valid", snapshot_local_terrain.valid);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "snapshot_local_terrain_patch_present", snapshot_local_terrain.patch_present);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "snapshot_local_terrain_height_raw", snapshot_local_terrain.height_raw);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "snapshot_local_terrain_material_id", snapshot_local_terrain.material_id);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "origin_terrain_sample_valid", origin_terrain.valid);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "origin_terrain_patch_present", origin_terrain.patch_present);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "origin_terrain_height_raw", origin_terrain.height_raw);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "origin_terrain_material_id", origin_terrain.material_id);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "water_level", game ? (float)game->session.water : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_vel_x", game ? game->debug.dbg_last_local_vel_x : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_vel_y", game ? game->debug.dbg_last_local_vel_y : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_vel_z", game ? game->debug.dbg_last_local_vel_z : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_grounded", game ? game->debug.dbg_last_local_grounded : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_became_airborne", game ? game->debug.dbg_last_local_became_airborne : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_w_down", game ? game->debug.dbg_input_w_down : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_a_down", game ? game->debug.dbg_input_a_down : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_s_down", game ? game->debug.dbg_input_s_down : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_d_down", game ? game->debug.dbg_input_d_down : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_force_x", game ? game->debug.dbg_io_x_force : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_force_y", game ? game->debug.dbg_io_y_force : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_force_z", game ? game->debug.dbg_io_z_force : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_torque", game ? game->debug.dbg_io_torque : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_force_applied_x", game ? game->debug.dbg_io_x_force_applied : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_force_applied_y", game ? game->debug.dbg_io_y_force_applied : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_force_applied_z", game ? game->debug.dbg_io_z_force_applied : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "input_world_dir", game ? game->debug.dbg_last_input_world_dir : -1.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "facing_input_world_dir", game ? game->debug.dbg_last_input_world_dir : -1.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "facing_input_nonzero",
            (game && game->debug.dbg_last_input_world_dir >= 0.0f) ? 1 : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_jump_requested", game ? game->debug.dbg_last_input_jump_requested : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_fly_applied", game ? game->debug.dbg_io_fly_applied : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_action_value", game ? game->debug.dbg_player_action_value : -1);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_focus_loss_count", game ? game->debug.dbg_focus_loss_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_focus_gain_count", game ? game->debug.dbg_focus_gain_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "game_main_menu_active", (game && game->ui.main_menu) ? 1 : 0);
        const int auth_world_missing_mask = GameAuthoritativeWorldReadyMissingMask();
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "game_loading_state", MainMenuWebGameLoadingState());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "mainmenu_progress_state", MainMenuWebProgressState());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_world_missing_mask", auth_world_missing_mask);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_has_game", game != 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_main_menu_clear", (auth_world_missing_mask & AUTH_WORLD_MAIN_MENU_ACTIVE) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_has_world", (auth_world_missing_mask & AUTH_WORLD_MISSING_WORLD) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_has_terrain", (auth_world_missing_mask & AUTH_WORLD_MISSING_TERRAIN) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_has_physics", (auth_world_missing_mask & AUTH_WORLD_MISSING_PHYSICS) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_has_server", (auth_world_missing_mask & AUTH_WORLD_MISSING_SERVER) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_local_id_valid", (auth_world_missing_mask & AUTH_WORLD_BAD_LOCAL_ID) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_snapshot_seq", (auth_world_missing_mask & AUTH_WORLD_MISSING_SNAPSHOT_SEQ) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_snapshot_tick", (auth_world_missing_mask & AUTH_WORLD_MISSING_SNAPSHOT_TICK) == 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "auth_ready_local_pose", (auth_world_missing_mask & AUTH_WORLD_MISSING_LOCAL_POSE) == 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "world_ready", GameAuthoritativeWorldReady());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "authoritative_world_ready", GameAuthoritativeWorldReady());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "render_stage_code", g_web_render_stage_code);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "server_ptr_ok", server != 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "server_alloc_ok", g_web_server_alloc != 0);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "server_alloc_matches_ptr",
            g_web_server_alloc && server == (Server*)&g_web_server_alloc->server);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_canary_ok", WebFL933ServerCanariesOk(g_web_server_alloc));
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_alloc_local_id", alloc_server ? alloc_server->connection.local_id : -99);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_alloc_snapshot_tick", alloc_server ? alloc_server->authority.snapshot_client.last_snapshot_tick : 0u);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "server_join_active", g_web_authoritative_join_active);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_join_generation", g_web_join_generation);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pending_net_packet_count", g_pending_net_packet_count);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_lifecycle_seq", lifecycle ? lifecycle->seq : 0u);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "server_lifecycle_event", lifecycle ? lifecycle->event : 0);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "server_lifecycle_prev_event", lifecycle ? lifecycle->prev_event : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_lifecycle_server_ptr_ok", lifecycle ? lifecycle->server_ptr_ok : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_lifecycle_alloc_ok", lifecycle ? lifecycle->alloc_ok : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_lifecycle_canary_ok", lifecycle ? lifecycle->canary_ok : -1);
        WebDiagnosticsAppendProbeText(buf, (int)cap, used,
            "%s\"server_lifecycle_ring\":%s", used > 1 ? "," : "", WebDiagnosticsBuildLifecycleRingJson());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_first_live_seen", server_loss ? server_loss->first_live_seen : 0);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "server_first_live_stage", server_loss ? server_loss->first_live_stage : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_live_ptr", server_loss ? server_loss->first_live_ptr : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_live_lifecycle_seq", server_loss ? server_loss->first_live_lifecycle_seq : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_live_join_generation", server_loss ? server_loss->first_live_join_generation : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_first_null_after_live_seen", server_loss ? server_loss->first_null_after_live_seen : 0);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "server_first_null_after_live_stage", server_loss ? server_loss->first_null_after_live_stage : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_op", server_loss ? server_loss->first_null_after_live_op : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_size", server_loss ? server_loss->first_null_after_live_size : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_packet_calls", server_loss ? server_loss->first_null_after_live_packet_calls : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_packet_proc", server_loss ? server_loss->first_null_after_live_packet_proc : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_packet_null", server_loss ? server_loss->first_null_after_live_packet_null : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_packet_defer", server_loss ? server_loss->first_null_after_live_packet_defer : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_pending", server_loss ? server_loss->first_null_after_live_pending : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_first_null_after_live_canary_ok", server_loss ? server_loss->first_null_after_live_canary_ok : -1);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_alloc_ptr", server_loss ? server_loss->first_null_after_live_alloc_ptr : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_alloc_server_ptr", server_loss ? server_loss->first_null_after_live_alloc_server_ptr : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_lifecycle_seq", server_loss ? server_loss->first_null_after_live_lifecycle_seq : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_join_generation", server_loss ? server_loss->first_null_after_live_join_generation : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_first_null_after_live_game_loading", server_loss ? server_loss->first_null_after_live_game_loading : -1);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_first_null_after_live_menu_progress", server_loss ? server_loss->first_null_after_live_menu_progress : -1);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "server_first_null_after_live_world_bits", server_loss ? server_loss->first_null_after_live_world_bits : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_sentinel_a", server_loss ? server_loss->first_null_after_live_sentinel_a : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_first_null_after_live_sentinel_b", server_loss ? server_loss->first_null_after_live_sentinel_b : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_global_addr", (uint32_t)(uintptr_t)(&server));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_global_value", (uint32_t)(uintptr_t)(server));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_alloc_addr", (uint32_t)(uintptr_t)(g_web_server_alloc));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "server_alloc_server_addr",
            (uint32_t)(uintptr_t)(g_web_server_alloc ? (const void*)&g_web_server_alloc->server : 0));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "render_stage_addr_web_tu",
            (uint32_t)(uintptr_t)(&g_web_render_stage_code));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "render_stage_addr_game_tu", GameFL933RenderStageAddr32());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "cross_tu_probe_addr", GameFL933CrossTuProbeAddr32());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "cross_tu_probe_value",
            GameFL933CrossTuProbe(0x5f933001u));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "fl933_sentinel_a", lifecycle ? lifecycle->sentinel_a : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "fl933_sentinel_b", lifecycle ? lifecycle->sentinel_b : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "fl933_lifecycle_epoch", lifecycle ? lifecycle->epoch : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_calls", g_web_packet_calls);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_server_null", g_web_packet_server_null);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_deferred", g_web_packet_deferred);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_proc_called", g_web_packet_proc_called);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_last_branch", g_web_packet_last_branch);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_first_token", g_web_packet_first_token);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_last_token", WebDiagnosticsGetPacketLastToken());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_token_b", WebDiagnosticsGetPacketHistB());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_token_q", WebDiagnosticsGetPacketHistQ());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_token_a", WebDiagnosticsGetPacketHistA());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_token_i", WebDiagnosticsGetPacketHistI());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_token_j", WebDiagnosticsGetPacketHistJ());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "packet_token_n", WebDiagnosticsGetPacketHistN());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_enqueued", g_web_pending_enqueued);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_enqueued_bytes", g_web_pending_enqueued_bytes);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped", g_web_pending_dropped);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_oversized_dropped", g_web_pending_oversized_dropped);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped_token_b", WebDiagnosticsGetPendingDroppedTokenB());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped_token_q", WebDiagnosticsGetPendingDroppedTokenQ());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped_token_a", WebDiagnosticsGetPendingDroppedTokenA());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped_token_i", WebDiagnosticsGetPendingDroppedTokenI());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped_token_j", WebDiagnosticsGetPendingDroppedTokenJ());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_dropped_token_n", WebDiagnosticsGetPendingDroppedTokenN());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_max_packet_size", g_web_pending_max_packet_size);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_last_oversized_token", g_web_pending_last_oversized_token);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_last_oversized_size", g_web_pending_last_oversized_size);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_last_oversized_cap", g_web_pending_last_oversized_cap);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_max_depth", g_web_pending_max_depth);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_attempts", g_web_pending_drain_attempts);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_block_defer", g_web_pending_drain_block_defer);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_block_token", g_web_pending_drain_block_token);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_deferred_preserved", g_web_pending_drain_deferred_preserved);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_deferred_token", g_web_pending_drain_deferred_token);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_stop_server_null", g_web_pending_drain_stop_server_null);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_drain_processed", g_web_pending_drain_processed);
        int pending_b = 0, pending_q = 0, pending_a = 0, pending_i = 0;
        WebFL933PendingQueueTokenCounts(&pending_b, &pending_q, &pending_a, &pending_i);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pending_token_b", pending_b);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pending_token_q", pending_q);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pending_token_a", pending_a);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "pending_token_i", pending_i);
        uint32_t pending_oldest_age_ms = 0;
        if (g_pending_net_packet_count > 0 && g_pending_net_packets[0].enqueue_us > 0)
        {
            const uint64_t now_us = GetTime();
            if (now_us >= g_pending_net_packets[0].enqueue_us)
                pending_oldest_age_ms = (uint32_t)((now_us - g_pending_net_packets[0].enqueue_us) / 1000ull);
        }
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_oldest_age_ms", pending_oldest_age_ms);
        uint32_t pending_first_block_age_ms = 0;
        if (g_web_pending_first_block_us)
        {
            const uint64_t now_us = GetTime();
            if (now_us >= g_web_pending_first_block_us)
                pending_first_block_age_ms = (uint32_t)((now_us - g_web_pending_first_block_us) / 1000ull);
        }
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_first_block_age_ms", pending_first_block_age_ms);
        uint32_t pending_first_defer_age_ms = 0;
        if (g_web_pending_first_defer_us)
        {
            const uint64_t now_us = GetTime();
            if (now_us >= g_web_pending_first_defer_us)
                pending_first_defer_age_ms = (uint32_t)((now_us - g_web_pending_first_defer_us) / 1000ull);
        }
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "pending_first_defer_age_ms", pending_first_defer_age_ms);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "send_calls", g_web_send_calls);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "send_failures", g_web_send_failures);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "send_last_token", g_web_send_last_token);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "send_token_ack", g_web_send_hist_ack);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "render_buf_sample_valid", WebDiagnosticsGetRenderBufSampleValid());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "render_buf_sample_width", WebDiagnosticsGetRenderBufSampleWidth());
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "render_buf_sample_height", WebDiagnosticsGetRenderBufSampleHeight());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "render_buf_sample_cells_cpp", WebDiagnosticsGetRenderBufSampleCells());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "render_buf_nonzero_cells_cpp", WebDiagnosticsGetRenderBufNonzeroCells());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "render_buf_nonzero_glyph_cells_cpp", WebDiagnosticsGetRenderBufNonzeroGlyphCells());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "render_buf_hash_cpp", WebDiagnosticsGetRenderBufHash());
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "render_buf_center_fg_cpp", render_buf_center_fg, 8);
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "render_buf_center_bk_cpp", render_buf_center_bk, 8);
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "render_buf_center_glyphs_cpp", render_buf_center_gl, 8);
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "render_buf_center_spare_cpp", render_buf_center_spare, 8);
        const bool bundle_hash_match = (g_web_bundle_hash[0] &&
            g_web_server_bundle_hash[0] &&
            strcmp(g_web_bundle_hash, g_web_server_bundle_hash) == 0);
        const bool ids_lock_hash_match = (g_web_ids_lock_hash[0] &&
            g_web_server_ids_lock_hash[0] &&
            strcmp(g_web_ids_lock_hash, g_web_server_ids_lock_hash) == 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "appearance_contract_version", g_web_appearance_contract_version);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "appearance_contract_version_server", g_web_server_appearance_contract_version);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "bundle_hash", g_web_bundle_hash);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "ids_lock_hash", g_web_ids_lock_hash);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "appearance_contract_source_kind", game ? (uint32_t)game->player.appearance_v2.source_kind : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "appearance_contract_projection_kind", game ? (uint32_t)game->player.appearance_v2.projection_kind : 0u);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "bundle_hash_client", g_web_bundle_hash);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "bundle_hash_server", g_web_server_bundle_hash);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "bundle_hash_match", bundle_hash_match);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "ids_lock_hash_client", g_web_ids_lock_hash);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "ids_lock_hash_server", g_web_server_ids_lock_hash);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "ids_lock_hash_match", ids_lock_hash_match);
        // FL-4131 Phase 7 — glyph manifest identity recorder surface.
        // Empty server hash means "legacy CP437-only deployment"; the soft path
        // accepts any client claim. Non-empty server hash requires exact match.
        const bool glyph_manifest_hash_match = (g_web_server_glyph_manifest_hash[0]
            ? (g_web_glyph_manifest_hash[0] && strcmp(g_web_glyph_manifest_hash, g_web_server_glyph_manifest_hash) == 0)
            : true);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "glyph_manifest_hash_client", g_web_glyph_manifest_hash);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "glyph_manifest_hash_server", g_web_server_glyph_manifest_hash);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "glyph_manifest_hash_match", glyph_manifest_hash_match);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "content_pack_id_client", g_web_content_pack_id);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "content_pack_id_server", g_web_server_content_pack_id);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "extended_fallback_render_event_count", g_fl4131_fallback_render_event_count);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "extended_fallback_render_event_observed", g_fl4131_fallback_render_event_count > 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "extended_fallback_render_last_glyph_id", g_fl4131_fallback_render_last_glyph_id);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "extended_fallback_render_last_fallback_glyph_id", g_fl4131_fallback_render_last_fallback_glyph_id);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "extended_fallback_render_last_lut_width", g_fl4131_fallback_render_last_lut_width);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "extended_fallback_render_last_red_pixels", g_fl4131_fallback_render_last_red_pixels);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "extended_fallback_render_last_black_pixels", g_fl4131_fallback_render_last_black_pixels);
        WebDiagnosticsAppendJsonStringField(buf, (int)cap, used, "appearance_contract_reject_reason", g_web_appearance_contract_reject_reason);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "appearance_v2_packets", s ? s->authority.auth_item.appearance_v2_packets : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "item_event_v2_packets", s ? s->authority.auth_item.item_event_v2_packets : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "last_appearance_v2_entity_id", s ? (uint32_t)s->authority.auth_item.last_appearance_v2_entity_id : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "last_item_definition_id_v2", s ? (uint32_t)s->authority.auth_item.last_item_definition_id_v2 : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "actor_appearance_skin_definition_id", game ? (uint32_t)game->player.appearance_v2.skin_definition_id : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "actor_appearance_mount_definition_id", game ? (uint32_t)game->player.appearance_v2.mount_definition_id : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "actor_appearance_loadout_revision", game ? game->player.appearance_v2.loadout_revision : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_main_menu_active", game ? game->debug.dbg_main_menu_active : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_show_inventory_active", game ? game->debug.dbg_show_inventory_active : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_talk_box_active", game ? game->debug.dbg_talk_box_active : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "input_menu_depth_value", game ? game->debug.dbg_menu_depth_value : 0);
        WebDiagnosticsAppendProbeText(buf, (int)cap, used, ",\"input_event_sample\":[");
        if (game)
        {
            uint32_t first_seq = (game->debug.dbg_input_event_seq > DebugTelemetryState::DBG_INPUT_EVENT_RING)
                ? (game->debug.dbg_input_event_seq - DebugTelemetryState::DBG_INPUT_EVENT_RING + 1u)
                : 1u;
            int samples = 0;
            for (uint32_t seq = first_seq; seq <= game->debug.dbg_input_event_seq && used < (int)cap - 1; seq++)
            {
                const Game::DebugInputEvent* ev = &game->debug.dbg_input_event[seq % DebugTelemetryState::DBG_INPUT_EVENT_RING];
                if (!ev || ev->seq != seq)
                    continue;
                if (!WebDiagnosticsAppendProbeText(
                        buf, (int)cap, used,
                        "%s{\"seq\":%u,\"kind\":%d,\"key\":%d,\"auto_repeat\":%d,\"dt_ms\":%u,\"main_menu_active\":%d,\"show_inventory_active\":%d,\"talk_box_active\":%d,\"menu_depth_value\":%d}",
                        samples ? "," : "",
                        (unsigned int)ev->seq,
                        ev->kind,
                        ev->key,
                        ev->auto_repeat,
                        (unsigned int)ev->dt_ms,
                        ev->main_menu_active,
                        ev->show_inventory_active,
                        ev->talk_box_active,
                        ev->menu_depth_value))
                    break;
                samples++;
            }
        }
        WebDiagnosticsAppendProbeText(buf, (int)cap, used, "]");
        int lag_wait_age_ms = 0;
        int lag_response_age_ms = -1;
        bool lag_measurement_stale = false;
        // FL-1443 / FL-1713 track: lag_spike_count only increments at >200ms but sustained
        // 110-180ms lag is human-visible. The post_join_lag_p95 metrics show the real complaint.
        // SPENT APPROACHES — do NOT retry (see FL-1713 for full history):
        //   - Join-era interp rebase (de785476): FALSIFIED
        //   - DeleteInst/CreateInst churn: FALSIFIED (no churn during lag)
        //   - Null-sprite grace restore: ownership debt, not a fix
        //   - Ordered-input queue (c5a7e1d8): REJECTED_WORSE (891ms max)
        //   - Client prediction replay (b524587d): DELETED (Law 2 violation)
        //   - Per-snapshot APPEARANCE_STATE_V2 resend: DELETED (HOL blocking)
        //   - Respawn replay reset: helped one phase, did not close alive-state lag
        if (s)
        {
            if (s->connection.lag.lag_wait &&
                s->connection.lag.lag_last_request_stamp > 0 &&
                s->connection.stamp >= s->connection.lag.lag_last_request_stamp)
            {
                lag_wait_age_ms = (int)((s->connection.stamp - s->connection.lag.lag_last_request_stamp) / 1000ull);
            }
            if (s->connection.lag.lag_last_response_stamp > 0 &&
                s->connection.stamp >= s->connection.lag.lag_last_response_stamp)
            {
                lag_response_age_ms = (int)((s->connection.stamp - s->connection.lag.lag_last_response_stamp) / 1000ull);
            }
            if (lag_response_age_ms >= 500)
                lag_measurement_stale = true;
            else if (s->connection.lag.lag_response_count == 0 &&
                     s->connection.lag.lag_request_count > 0 &&
                     (s->connection.lag.lag_wait_timeout_count > 0 || lag_wait_age_ms >= 500))
                lag_measurement_stale = true;
        }
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "lag_ms", s ? s->connection.lag.lag_ms : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "lag_rtt_raw_ms", s ? s->connection.lag.lag_rtt_raw_ms : 0);

        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "lag_wait", s ? s->connection.lag.lag_wait : false);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_request_count", s ? s->connection.lag.lag_request_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_request_send_fail_count", s ? s->connection.lag.lag_request_send_fail_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_response_count", s ? s->connection.lag.lag_response_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_wait_timeout_count", s ? s->connection.lag.lag_wait_timeout_count : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "lag_wait_age_ms", lag_wait_age_ms);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "lag_response_age_ms", lag_response_age_ms);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "lag_measurement_stale", lag_measurement_stale);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_request_seq", s ? s->connection.lag.lag_trace_request_seq : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_response_seq", s ? s->connection.lag.lag_trace_response_seq : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_request_stamp_us32", s ? (uint32_t)s->connection.lag.lag_trace_request_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_send_call_stamp_us32", s ? (uint32_t)s->connection.lag.lag_trace_send_call_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_client_send_us32", s ? s->connection.lag.lag_trace_client_send_us32 : 0u);
	        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_rx_us32", s ? s->connection.lag.lag_trace_server_rx_us32 : 0u);
	        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_enqueue_us32", s ? s->connection.lag.lag_trace_server_enqueue_us32 : 0u);
	        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_flush_start_us32", s ? s->connection.lag.lag_trace_server_flush_start_us32 : 0u);
	        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_flush_finish_us32", s ? s->connection.lag.lag_trace_server_flush_finish_us32 : 0u);
	        WebDiagnosticsAppendJsonUInt64Field(buf, (int)cap, used, "lag_trace_server_rx_epoch_us", s ? s->connection.lag.lag_trace_server_rx_epoch_us : 0u);
	        WebDiagnosticsAppendJsonUInt64Field(buf, (int)cap, used, "lag_trace_server_enqueue_epoch_us", s ? s->connection.lag.lag_trace_server_enqueue_epoch_us : 0u);
	        WebDiagnosticsAppendJsonUInt64Field(buf, (int)cap, used, "lag_trace_server_flush_start_epoch_us", s ? s->connection.lag.lag_trace_server_flush_start_epoch_us : 0u);
	        WebDiagnosticsAppendJsonUInt64Field(buf, (int)cap, used, "lag_trace_server_flush_finish_epoch_us", s ? s->connection.lag.lag_trace_server_flush_finish_epoch_us : 0u);
	        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_packet_entry_stamp_us32", s ? (uint32_t)s->connection.lag.lag_trace_packet_entry_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_proc_entry_stamp_us32", s ? (uint32_t)s->connection.lag.lag_trace_proc_entry_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_wasm_packet_entry_us32", s ? (uint32_t)s->connection.lag.lag_trace_packet_entry_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_lag_proc_entry_us32", s ? (uint32_t)s->connection.lag.lag_trace_proc_entry_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_lag_proc_exit_us32", s ? (uint32_t)s->connection.lag.lag_trace_proc_exit_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_wasm_packet_exit_us32", s ? (uint32_t)s->connection.lag.lag_trace_packet_exit_stamp : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_stamp_at_proc_us32", s ? (uint32_t)s->connection.lag.lag_trace_server_stamp_at_proc : 0u);
        WebDiagnosticsAppendJsonBoolField(buf, (int)cap, used, "lag_trace_packet_exit_valid", s ? s->connection.lag.lag_trace_packet_exit_valid : false);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_client_send_to_packet_us", s ? s->connection.lag.lag_trace_client_send_to_packet_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_packet_to_proc_us", s ? s->connection.lag.lag_trace_packet_to_proc_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_wasm_packet_proc_us", s ? s->connection.lag.lag_trace_packet_proc_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_proc_stamp_minus_request_us", s ? s->connection.lag.lag_trace_proc_stamp_minus_request_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_proc_entry_minus_request_us", s ? s->connection.lag.lag_trace_proc_entry_minus_request_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_rx_to_enqueue_us", s ? s->connection.lag.lag_trace_server_rx_to_enqueue_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_enqueue_to_flush_start_us", s ? s->connection.lag.lag_trace_server_enqueue_to_flush_start_us : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "lag_trace_server_flush_us", s ? s->connection.lag.lag_trace_server_flush_us : 0u);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "camera_yaw", game ? game->debug.dbg_last_camera_yaw : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_anim_dx", game ? game->debug.dbg_last_local_anim_dx : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_anim_dy", game ? game->debug.dbg_last_local_anim_dy : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_anim_dz", game ? game->debug.dbg_last_local_anim_dz : 0.0f);
        const WebRecorderBridgeMode recorder_bridge_mode = ActiveWebRecorderBridgeMode();
        const WebRecorderBridgeInputs recorder_bridge_inputs = {
            game,
            server ? (const Server*)server : 0,
            s,
            remote0_appearance,
        };
        const WebRecorderBridgeStats recorder_bridge_stats =
            WebRecorderBridgeAppendMountedProofFields(
                buf,
                (int)cap,
                used,
                recorder_bridge_mode,
                recorder_bridge_inputs);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "web_recorder_bridge_mode", recorder_bridge_stats.mode);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "web_recorder_bridge_mounted_field_count", recorder_bridge_stats.field_count);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "web_recorder_bridge_mounted_bytes", recorder_bridge_stats.bytes_appended);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "web_recorder_bridge_publish_us", recorder_bridge_stats.publish_duration_us);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_pose_apply_count", game ? game->debug.dbg_remote0_pose_apply_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_pose_source", game ? game->debug.dbg_remote0_pose_source : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_last_pose_packet_kind", game ? game->debug.dbg_remote0_last_pose_packet_kind : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "remote0_last_received_dir", game ? game->debug.dbg_remote0_last_received_dir : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_dir_change_events", game ? game->debug.dbg_remote0_dir_change_events : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_has_prev_dir", game ? game->debug.dbg_remote0_has_prev_dir : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "sent_pose_dir", game ? game->debug.dbg_sent_pose_dir : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "sent_pose_dir_change_events", game ? game->debug.dbg_sent_pose_dir_change_events : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "remote0_raw_wire_dir", game ? game->debug.dbg_remote0_raw_wire_dir : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "remote0_wire_dir_min", game ? game->debug.dbg_remote0_wire_dir_min : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "remote0_wire_dir_max", game ? game->debug.dbg_remote0_wire_dir_max : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_wire_dir_varied", game ? game->debug.dbg_remote0_wire_dir_varied : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote0_wire_pose_count", game ? game->debug.dbg_remote0_wire_pose_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote_join_events", game ? game->debug.dbg_remote_join_events : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "remote_leave_events", game ? game->debug.dbg_remote_leave_events : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_applied", game ? game->debug.dbg_reconcile_applied : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_hard_snap", game ? game->debug.dbg_reconcile_hard_snap : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_physics_snap", 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_zeroed_xy", game ? game->debug.dbg_reconcile_zeroed_xy : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "reconcile_dx", game ? game->debug.dbg_reconcile_dx : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "reconcile_dy", game ? game->debug.dbg_reconcile_dy : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "reconcile_dz", game ? game->debug.dbg_reconcile_dz : 0.0f);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "reconcile_tick", game ? game->debug.dbg_reconcile_tick : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_smooth_count", game ? (int)game->player.mp_move.reconcile_smooth_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_hard_snap_count", game ? (int)game->player.mp_move.reconcile_hard_snap_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_physics_snap_count", 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "reconcile_deadzone_skip_count", game ? (int)game->player.mp_move.reconcile_deadzone_skip_count : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "reconcile_auth_dist_pre", game ? game->debug.dbg_reconcile_auth_dist_pre : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "reconcile_auth_dist_post", game ? game->debug.dbg_reconcile_auth_dist_post : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_vel_dir_change_no_reconcile", game ? game->debug.dbg_local_vel_dir_change_no_reconcile : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_last_acked_input_seq", game ? game->debug.dbg_local_last_acked_input_seq : 0u);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "local_snapshot_age_ms", game ? game->debug.dbg_local_snapshot_age_ms : 0.0f);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_render_medium_snap_count", game ? game->debug.dbg_local_render_medium_snap_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_render_hard_snap_count", game ? game->debug.dbg_local_render_hard_snap_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_ack_seq_regression_count", mm ? mm->ack_seq_regression_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_replay_queue_overflow_count", mm ? mm->diag_queue_overflow_count : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_pending_input_count", mm ? mm->diag_pending_input_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "local_replayed_input_count", mm ? mm->diag_replayed_input_count : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_diag_last_sent_seq", mm ? (uint32_t)mm->diag_last_sent_seq : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "local_diag_last_replayed_seq", mm ? (uint32_t)mm->diag_last_replayed_seq : 0u);
        append_diag_quantized("local_sent_q", mm ? mm->diag_has_last_sent_input : false,
                              mm ? mm->diag_last_sent_seq : 0u,
                              mm ? &mm->diag_last_sent_input : 0);
        append_diag_quantized("local_predicted_q", mm ? mm->diag_has_last_predicted_input : false,
                              (mm && mm->diag_shadow_state.valid) ? mm->diag_shadow_state.seq : 0u,
                              mm ? &mm->diag_last_predicted_input : 0);
        append_diag_quantized("local_replayed_q", mm ? mm->diag_has_last_replayed_input : false,
                              mm ? mm->diag_last_replayed_seq : 0u,
                              mm ? &mm->diag_last_replayed_input : 0);
        append_diag_surface("pred_ack", mm ? &mm->diag_pred_ack : 0);
        append_diag_surface("auth_snap", mm ? &mm->diag_auth_snap : 0);
        append_diag_surface("replay_post", mm ? &mm->diag_replay_post : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "facing_snapshot_local_yaw",
            (mm && mm->diag_auth_snap.valid) ? mm->diag_auth_snap.step.yaw : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "facing_client_actor_render_dir",
            game ? game->debug.dbg_actor_render_dir : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "facing_client_remote0_render_dir",
            game ? game->debug.dbg_remote0_render_dir : 0.0f);
        append_diag_error("ack_error", mm ? mm->diag_ack_error_pos : 0, mm ? mm->diag_ack_error_vel : 0,
                          mm ? mm->diag_ack_error_yaw : 0.0f, mm ? mm->diag_ack_error_yaw_vel : 0.0f,
                          mm ? mm->diag_ack_error_slope : 0.0f, mm ? mm->diag_ack_error_grounded : 0);
        append_diag_error("replay_post_error", mm ? mm->diag_replay_post_error_pos : 0, mm ? mm->diag_replay_post_error_vel : 0,
                          mm ? mm->diag_replay_post_error_yaw : 0.0f, mm ? mm->diag_replay_post_error_yaw_vel : 0.0f,
                          mm ? mm->diag_replay_post_error_slope : 0.0f, mm ? mm->diag_replay_post_error_grounded : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_entity_id", game ? game->debug.dbg_last_tracked_npc0_entity_id : -1);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_pos_x", game ? game->debug.dbg_last_tracked_npc0_pos[0] : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_pos_y", game ? game->debug.dbg_last_tracked_npc0_pos[1] : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_hp", game ? game->debug.dbg_last_tracked_npc0_hp : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_life_state", game ? game->debug.dbg_last_tracked_npc0_life_state : -1);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_needs_physics_step", game ? game->debug.dbg_last_tracked_npc0_needs_physics_step : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_death_tick", game ? game->debug.dbg_last_tracked_npc0_death_tick : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_authoritative_tick", game ? game->debug.dbg_last_tracked_npc0_authoritative_tick : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_presentation_started_tick", game ? game->debug.dbg_last_tracked_npc0_presentation_started_tick : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_corpse_hold_age_ticks", game ? game->debug.dbg_last_tracked_npc0_corpse_hold_age_ticks : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_presentation_kind_id", game ? game->debug.dbg_last_tracked_npc0_presentation_kind_id : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_render_presentation_kind_id", game ? game->debug.dbg_last_tracked_npc0_render_presentation_kind_id : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_sample_owner_stable_frames", game ? game->debug.dbg_last_tracked_npc0_sample_owner_stable_frames : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_sample_owner_ready", game ? game->debug.dbg_last_tracked_npc0_sample_owner_ready : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_render_sprite_family_kind", game ? game->debug.dbg_last_tracked_npc0_render_sprite_family_kind : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_head_layer_definition_id", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_head_layer_definition_id : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_profile_id_hash", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_profile_id_hash : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_atlas_frame_index", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_atlas_frame_index : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_render_contribution_angle", game ? game->debug.dbg_last_tracked_npc0_render_contribution_angle : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_contribution_projection", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_contribution_projection : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_contribution_scope", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_contribution_scope : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_layer_count", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_layer_count : 0u);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_slot_kind_ids",
            game ? game->debug.dbg_last_tracked_npc0_render_slot_kind_ids : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_definition_ids",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_definition_ids : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_visible_cell_counts",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_visible_cell_counts : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_semantic_contribution_set_indices",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_semantic_contribution_set_indices : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_source_layer_indices",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_source_layer_indices : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_source_path_hashes",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_source_path_hashes : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_contributed_cell_counts",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_contributed_cell_counts : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "tracked_npc0_render_layer_occluded_cell_counts",
            game ? game->debug.dbg_last_tracked_npc0_render_layer_occluded_cell_counts : 0,
            game ? (int)game->debug.dbg_last_tracked_npc0_render_layer_count : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_attachment_expected_mask", game ? game->debug.dbg_last_tracked_npc0_render_attachment_expected_mask : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_attachment_source_visible_mask", game ? game->debug.dbg_last_tracked_npc0_render_attachment_source_visible_mask : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_attachment_source_missing_mask", game ? game->debug.dbg_last_tracked_npc0_render_attachment_source_missing_mask : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_compose_mode", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_compose_mode : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_compose_failure_stage", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_compose_failure_stage : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_compose_failure_base_layer_definition_id", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_compose_failure_base_layer_definition_id : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_compose_failure_overlay_layer_definition_id", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_compose_failure_overlay_layer_definition_id : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "tracked_npc0_render_compose_failure_overlay_slot_kind_id", game ? (uint32_t)game->debug.dbg_last_tracked_npc0_render_compose_failure_overlay_slot_kind_id : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_anim", game ? game->debug.dbg_last_tracked_npc0_anim : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_frame", game ? game->debug.dbg_last_tracked_npc0_frame : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_anim_length", game ? game->debug.dbg_last_tracked_npc0_anim_length : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_frame_clamped", game ? game->debug.dbg_last_tracked_npc0_frame_clamped : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_frame_changed_expected", game ? game->debug.dbg_last_tracked_npc0_frame_changed_expected : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_render_diverged_from_snapshot", game ? game->debug.dbg_last_tracked_npc0_render_diverged_from_snapshot : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_corpse_visible", game ? game->debug.dbg_last_tracked_npc0_corpse_visible : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_sprite_miss_frames", game ? game->debug.dbg_last_tracked_npc0_sprite_miss_frames : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_bundle_selector_failure_reason", game ? game->debug.dbg_last_tracked_npc0_bundle_selector_failure_reason : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_inst_create_count", game ? game->debug.dbg_last_tracked_npc0_inst_create_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_inst_delete_count", game ? game->debug.dbg_last_tracked_npc0_inst_delete_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_last_inst_delete_reason", game ? game->debug.dbg_last_tracked_npc0_last_inst_delete_reason : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_last_inst_delete_miss_frames", game ? game->debug.dbg_last_tracked_npc0_last_inst_delete_miss_frames : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_on_screen", game ? game->debug.dbg_last_tracked_npc0_on_screen : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_inst_visible", game ? game->debug.dbg_last_tracked_npc0_inst_visible : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_hp_bar_expected", game ? game->debug.dbg_last_tracked_npc0_hp_bar_expected : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_hp_bar_drawn", game ? game->debug.dbg_last_tracked_npc0_hp_bar_drawn : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_blit_attempted", game ? game->debug.dbg_last_tracked_npc0_body_blit_attempted : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_drew_any", game ? game->debug.dbg_last_tracked_npc0_body_drew_any : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_candidate_cells", game ? game->debug.dbg_last_tracked_npc0_body_candidate_cells : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_depth_pass_cells", game ? game->debug.dbg_last_tracked_npc0_body_depth_pass_cells : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_depth_fail_cells", game ? game->debug.dbg_last_tracked_npc0_body_depth_fail_cells : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_depth_fail_mesh_cells", game ? game->debug.dbg_last_tracked_npc0_body_depth_fail_mesh_cells : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_depth_fail_mesh_samples", game ? game->debug.dbg_last_tracked_npc0_body_depth_fail_mesh_samples : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_water_reject_cells", game ? game->debug.dbg_last_tracked_npc0_body_water_reject_cells : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_clip_reject", game ? game->debug.dbg_last_tracked_npc0_body_clip_reject : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_reject_reason", game ? game->debug.dbg_last_tracked_npc0_body_reject_reason : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_clip_left", game ? game->debug.dbg_last_tracked_npc0_body_clip_left : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_clip_right", game ? game->debug.dbg_last_tracked_npc0_body_clip_right : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_clip_bottom", game ? game->debug.dbg_last_tracked_npc0_body_clip_bottom : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_clip_top", game ? game->debug.dbg_last_tracked_npc0_body_clip_top : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_unclipped_left", game ? game->debug.dbg_last_tracked_npc0_body_unclipped_left : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_unclipped_right", game ? game->debug.dbg_last_tracked_npc0_body_unclipped_right : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_unclipped_bottom", game ? game->debug.dbg_last_tracked_npc0_body_unclipped_bottom : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_unclipped_top", game ? game->debug.dbg_last_tracked_npc0_body_unclipped_top : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_frame_width", game ? game->debug.dbg_last_tracked_npc0_body_frame_width : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_frame_height", game ? game->debug.dbg_last_tracked_npc0_body_frame_height : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_ref_x", game ? game->debug.dbg_last_tracked_npc0_body_ref_x : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_ref_y", game ? game->debug.dbg_last_tracked_npc0_body_ref_y : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_screen_pos_x", game ? game->debug.dbg_last_tracked_npc0_body_screen_pos_x : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_screen_pos_y", game ? game->debug.dbg_last_tracked_npc0_body_screen_pos_y : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_blit_pos_z", game ? game->debug.dbg_last_tracked_npc0_body_blit_pos_z : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_water_plane_z", game ? game->debug.dbg_last_tracked_npc0_body_water_plane_z : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_candidate_min_z", game ? game->debug.dbg_last_tracked_npc0_body_candidate_min_z : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_candidate_max_z", game ? game->debug.dbg_last_tracked_npc0_body_candidate_max_z : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_water_retry_attempted", game ? game->debug.dbg_last_tracked_npc0_body_water_retry_attempted : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_water_retry_drew_any", game ? game->debug.dbg_last_tracked_npc0_body_water_retry_drew_any : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_water_retry_lift_z", game ? game->debug.dbg_last_tracked_npc0_body_water_retry_lift_z : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_support_retry_attempted", game ? game->debug.dbg_last_tracked_npc0_body_support_retry_attempted : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_support_retry_drew_any", game ? game->debug.dbg_last_tracked_npc0_body_support_retry_drew_any : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_support_retry_lift_z", game ? game->debug.dbg_last_tracked_npc0_body_support_retry_lift_z : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_support_height_z", game ? game->debug.dbg_last_tracked_npc0_body_support_height_z : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_second_retry_attempted", game ? game->debug.dbg_last_tracked_npc0_body_second_retry_attempted : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_second_retry_drew_any", game ? game->debug.dbg_last_tracked_npc0_body_second_retry_drew_any : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "tracked_npc0_body_second_retry_lift_z", game ? game->debug.dbg_last_tracked_npc0_body_second_retry_lift_z : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "tracked_npc0_body_reject_reason_before_second_retry", game ? game->debug.dbg_last_tracked_npc0_body_reject_reason_before_second_retry : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_duration_us", g_web_client_render_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_interaction_query_duration_us", g_web_render_interaction_query_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_status_bar_duration_us", g_web_render_status_bar_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_talk_overlay_duration_us", g_web_render_talk_overlay_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_player_overlay_duration_us", g_web_render_player_overlay_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_deferred_terrain_dark_duration_us", g_web_render_deferred_terrain_dark_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_core_world_duration_us", g_web_render_core_world_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_weather_duration_us", g_web_render_weather_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_minimap_duration_us", g_web_render_minimap_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_api_hook_duration_us", g_web_render_api_hook_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_remote_duplicate_purge_duration_us", g_web_render_remote_duplicate_purge_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_remote_duplicate_purge_deleted_count", g_web_render_remote_duplicate_purge_deleted_count);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_snapshot_npc_visual_lifecycle_duration_us", g_web_render_snapshot_npc_visual_lifecycle_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_snapshot_npc_visual_lifecycle_slots", g_web_render_snapshot_npc_visual_lifecycle_slots);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_authoritative_item_appearance_duration_us", g_web_render_authoritative_item_appearance_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_authoritative_item_appearance_slots", g_web_render_authoritative_item_appearance_slots);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_frame_input_npc_visual_copy_duration_us", g_web_render_frame_input_npc_visual_copy_duration_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_frame_input_npc_visual_copy_slots", g_web_render_frame_input_npc_visual_copy_slots);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_lag_probe_sent_this_frame", g_web_render_lag_probe_sent_this_frame);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_lag_probe_send_stage_code", g_web_render_lag_probe_send_stage_code);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_lag_probe_send_to_render_end_us", g_web_render_lag_probe_send_to_render_end_us);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "client_render_lag_probe_send_seq", g_web_render_lag_probe_send_seq);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "item_event_packets", s ? (uint32_t)s->authority.auth_item.item_event_packets : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "item_event_applied_packets", s ? (uint32_t)s->authority.auth_item.item_event_applied_packets : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_last_token", WebDiagnosticsGetPacketLastToken());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_last_proc_us", WebDiagnosticsGetPacketLastProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_max_proc_us", WebDiagnosticsGetPacketMaxProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_r_last_proc_us", WebDiagnosticsGetPacketRLastProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_r_max_proc_us", WebDiagnosticsGetPacketRMaxProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_d_last_proc_us", WebDiagnosticsGetPacketDLastProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_d_max_proc_us", WebDiagnosticsGetPacketDMaxProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_k_last_proc_us", WebDiagnosticsGetPacketKLastProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "web_packet_k_max_proc_us", WebDiagnosticsGetPacketKMaxProcUs());
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_known", s ? (uint32_t)s->authority.auth_item.item_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_world", s ? (uint32_t)s->authority.auth_item.item_world_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_local", s ? (uint32_t)s->authority.auth_item.item_local_owned_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_state_apply", s ? (uint32_t)s->authority.auth_item.state_apply_packets : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_equipped_local",
                            WebCountEquippedLocalItems(s));
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_mode",
                            s ? 1u : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_overlay_enabled",
                            s ? 1u : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_panel_enabled",
                            (s && s->authority.auth_item.item_local_owned_count > 0) ? 1u : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_world_strip_count", game ? (uint32_t)game->authoritative.world_items_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_world_strip_0", game ? (uint32_t)game->authoritative.world_item_ids[0] : 0xffffu);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_world_strip_1", game ? (uint32_t)game->authoritative.world_item_ids[1] : 0xffffu);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_world_strip_2", game ? (uint32_t)game->authoritative.world_item_ids[2] : 0xffffu);
        int auth_pickup_strip_array_count = game ? game->authoritative.world_pickup_rows_count : 0;
        if (auth_pickup_strip_array_count > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
            auth_pickup_strip_array_count = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_pickup_strip_count", game ? (uint32_t)game->authoritative.world_pickup_rows_count : 0u);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "auth_pickup_strip_item_ids",
                                 game ? game->authoritative.world_pickup_item_ids : 0,
                                 auth_pickup_strip_array_count);
        WebDiagnosticsAppendJsonFloatArrayField(buf, (int)cap, used, "auth_pickup_strip_distance2",
                                   game ? game->authoritative.world_pickup_distance2 : 0,
                                   auth_pickup_strip_array_count);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_visible_world_count", game ? (uint32_t)game->debug.dbg_visible_authoritative_item_markers : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "minimap_marker_visible_count", game ? (uint32_t)game->debug.dbg_minimap_marker_visible_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "minimap_marker_right_half_visible_count", game ? (uint32_t)game->debug.dbg_minimap_marker_right_half_visible_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "minimap_marker_label_chars_drawn", game ? (uint32_t)game->debug.dbg_minimap_marker_label_chars_drawn : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "minimap_marker_right_half_label_chars_drawn", game ? (uint32_t)game->debug.dbg_minimap_marker_right_half_label_chars_drawn : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "minimap_remote_expected_count", game ? (uint32_t)game->debug.dbg_minimap_remote_expected_count : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "minimap_remote_drawn_count", game ? (uint32_t)game->debug.dbg_minimap_remote_drawn_count : 0u);
        int auth_visible_world_array_count =
            game ? game->debug.dbg_visible_authoritative_item_markers : 0;
        for (int i = 0; i < 9; i++)
        {
            char key[64];
            snprintf(key, sizeof(key), "auth_visible_world_%d", i);
            WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, key,
                                game ? (uint32_t)game->debug.dbg_visible_authoritative_item_ids[i] : 0xffffu);
            snprintf(key, sizeof(key), "auth_visible_world_%d_style", i);
            WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, key,
                                game ? (uint32_t)game->debug.dbg_visible_authoritative_item_styles[i] : 0u);
        }
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "auth_visible_world_item_ids",
                                 game ? game->debug.dbg_visible_authoritative_item_ids : 0,
                                 auth_visible_world_array_count);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "auth_visible_world_definition_ids",
                                 game ? game->debug.dbg_visible_authoritative_item_definition_ids : 0,
                                 auth_visible_world_array_count);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "auth_visible_world_visual_style_ids",
                                 game ? game->debug.dbg_visible_authoritative_item_visual_style_ids : 0,
                                 auth_visible_world_array_count);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "auth_visible_world_sprite_source_hashes",
                                 game ? game->debug.dbg_visible_authoritative_item_world_sprite_source_hashes : 0,
                                 auth_visible_world_array_count);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "auth_visible_world_sprite_family_kinds",
                                 game ? game->debug.dbg_visible_authoritative_item_world_sprite_family_kinds : 0,
                                 auth_visible_world_array_count);
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "auth_visible_world_visual_failure_reasons",
                               game ? game->debug.dbg_visible_authoritative_item_visual_failure_reasons : 0,
                               auth_visible_world_array_count);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "actor_inventory_count",
                            game ? (uint32_t)game->authoritative.inventory_items_count : 0u);
        int actor_inventory_array_count = game ? game->authoritative.inventory_items_count : 0;
        if (actor_inventory_array_count > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
            actor_inventory_array_count = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "actor_inventory_item_ids",
                                 game ? game->authoritative.inventory_item_ids : 0,
                                 actor_inventory_array_count);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "actor_inventory_definition_ids",
                                 game ? game->authoritative.inventory_definition_ids : 0,
                                 actor_inventory_array_count);
        WebDiagnosticsAppendJsonUIntArrayField(buf, (int)cap, used, "actor_inventory_visual_style_ids",
                                 game ? game->authoritative.inventory_visual_style_ids : 0,
                                 actor_inventory_array_count);
        WebDiagnosticsAppendJsonU8ArrayField(buf, (int)cap, used, "actor_inventory_visual_failure_reasons",
                               game ? game->authoritative.inventory_visual_failure_reasons : 0,
                               actor_inventory_array_count);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_pickup_req_attempts", game ? (uint32_t)game->debug.dbg_auth_pickup_req_attempts : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_pickup_req_sent", game ? (uint32_t)game->debug.dbg_auth_pickup_req_sent : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_pickup_req_send_fail", game ? (uint32_t)game->debug.dbg_auth_pickup_req_send_fail : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pickup_req_last_index", game ? game->debug.dbg_auth_pickup_req_last_index : -1);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_pickup_req_last_item_id", game ? (uint32_t)game->debug.dbg_auth_pickup_req_last_item_id : 0xffffu);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pickup_req_last_reason", game ? game->debug.dbg_auth_pickup_req_last_reason : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_pickup_req_source_strip_item_id", game ? (uint32_t)game->debug.dbg_auth_pickup_req_source_strip_item_id : 0xffffu);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pickup_req_source_strip_count", game ? game->debug.dbg_auth_pickup_req_source_strip_count : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_use_req_attempts", game ? (uint32_t)game->debug.dbg_auth_use_req_attempts : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_use_req_sent", game ? (uint32_t)game->debug.dbg_auth_use_req_sent : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_use_req_send_fail", game ? (uint32_t)game->debug.dbg_auth_use_req_send_fail : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_use_req_last_index", game ? game->debug.dbg_auth_use_req_last_index : -1);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_use_req_last_item_id", game ? (uint32_t)game->debug.dbg_auth_use_req_last_item_id : 0xffffu);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_use_req_last_reason", game ? game->debug.dbg_auth_use_req_last_reason : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_place_req_attempts", game ? (uint32_t)game->debug.dbg_auth_place_req_attempts : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_place_req_sent", game ? (uint32_t)game->debug.dbg_auth_place_req_sent : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_place_req_send_fail", game ? (uint32_t)game->debug.dbg_auth_place_req_send_fail : 0u);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_place_req_last_index", game ? game->debug.dbg_auth_place_req_last_index : -1);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_place_req_last_item_id", game ? (uint32_t)game->debug.dbg_auth_place_req_last_item_id : 0xffffu);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_place_req_last_reason", game ? game->debug.dbg_auth_place_req_last_reason : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_item_local_event_kind", game ? game->debug.dbg_auth_item_local_event_kind : 0);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_local_event_item_id", game ? (uint32_t)game->debug.dbg_auth_item_local_event_item_id : 0xffffu);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used, "auth_item_local_event_owner_id", game ? (uint32_t)game->debug.dbg_auth_item_local_event_owner_id : 0xffffu);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_item_local_event_sync_calls", game ? game->debug.dbg_auth_item_local_event_sync_calls : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pose_sample_calls", game ? game->debug.dbg_auth_pose_sample_calls : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pose_fallback_count", game ? game->debug.dbg_auth_pose_fallback_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pose_fallback_live_session_count", game ? game->debug.dbg_auth_pose_fallback_live_session_count : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "auth_pose_last_mode", 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "self_hp", game ? (int)game->debug.dbg_self_hp : 0);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "self_max_hp", game ? (int)game->debug.dbg_self_max_hp : 0);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "self_x", game ? game->debug.dbg_last_local_pos_x : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "self_y", game ? game->debug.dbg_last_local_pos_y : 0.0f);
	        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used, "self_z", game ? game->debug.dbg_last_local_pos_z : 0.0f);
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used, "self_fly", (game && game->session.fly_mode) ? 1 : 0);
        WebDiagnosticsAppendProbeText(buf, (int)cap, used, ",\"auth_item_sample\":[");
        if (s)
        {
            int samples = 0;
            for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS && samples < 64 && used < (int)cap - 1; i++)
            {
                const ::AuthoritativeItemState* ai = &s->authority.auth_item.items[i];
                if (!ai->valid)
                    continue;
                // FL-4137 geometry contract: publish one recorder-visible
                // block geometry frame. Collision/support come from the
                // catalog-backed authoritative item state; visual bounds come
                // from the visible render row. Proof must compare these fields
                // directly instead of carrying another block-height constant.
                float visual_bottom_z = -1.0e30f;
                float visual_top_z = -1.0e30f;
                if (game)
                {
                    for (int v = 0; v < game->debug.dbg_visible_authoritative_item_markers; v++)
                    {
                        if (game->debug.dbg_visible_authoritative_item_ids[v] == ai->item_id)
                        {
                            visual_bottom_z = game->debug.dbg_visible_authoritative_item_visual_bottom_z[v];
                            visual_top_z = game->debug.dbg_visible_authoritative_item_visual_top_z[v];
                            break;
                        }
                    }
                }
                const AppearanceCatalogItemDef* cat_def =
                    FindAppearanceCatalogItemById(ai->item_definition_id);
                float collision_height_units = 0.0f;
                float collision_radius_units = 0.0f;
                if (cat_def)
                {
                    if (cat_def->placeable &&
                        cat_def->gameplay_kind == APPEARANCE_CATALOG_GAMEPLAY_PLACEABLE_BLOCK)
                    {
                        // FL-4137: geometry derived from sprite projection.
                        // authored_half_extent / authored_height are catalog
                        // fallbacks used only when sprite projection cannot
                        // resolve; passing the catalog's collision_* literals
                        // matches the appearance pass that already calls
                        // PlacedBlockGeometryFromSpriteProjection with the
                        // same authored fields.
                        PlacedBlockGeometry geometry = {};
                        if (PlacedBlockGeometryLoadFromSpritePath(
                                cat_def->world_sprite_path,
                                cat_def->slug,
                                cat_def->collision_radius_units,
                                cat_def->collision_height_units,
                                &geometry))
                        {
                            collision_height_units = geometry.height;
                            collision_radius_units = geometry.half_extent;
                        }
                    }
                    else
                    {
                        collision_height_units = cat_def->collision_height_units;
                        collision_radius_units = cat_def->collision_radius_units;
                    }
                }
                const float collision_bottom_z = ai->pos[2];
                const float collision_top_z = ai->pos[2] + collision_height_units;
                const float support_top_z = collision_top_z;
                // FL-4137 #35 / FL-4163: per-block screen projection from
                // game->debug, populated where ProjectCoords already runs in
                // authoritative_world_item_appearance.cpp. Probe and
                // visibility regression read these to sample renderbuf inside
                // the projected rect and assert the visible top matches the
                // world support_top_z.
                int screen_valid = 0;
                int screen_top_col = 0, screen_top_row = 0;
                int screen_bottom_col = 0, screen_bottom_row = 0;
                int corners_valid = 0;
                int corner_cols[8] = {0,0,0,0,0,0,0,0};
                int corner_rows[8] = {0,0,0,0,0,0,0,0};
                if (game)
                {
                    for (int v = 0; v < game->debug.dbg_visible_authoritative_item_markers; v++)
                    {
                        if (game->debug.dbg_visible_authoritative_item_ids[v] == ai->item_id)
                        {
                            screen_valid = game->debug.dbg_visible_authoritative_item_screen_valid[v];
                            screen_top_col = game->debug.dbg_visible_authoritative_item_screen_top_col[v];
                            screen_top_row = game->debug.dbg_visible_authoritative_item_screen_top_row[v];
                            screen_bottom_col = game->debug.dbg_visible_authoritative_item_screen_bottom_col[v];
                            screen_bottom_row = game->debug.dbg_visible_authoritative_item_screen_bottom_row[v];
                            corners_valid = game->debug.dbg_visible_authoritative_item_corners_valid[v];
                            for (int k = 0; k < 8; k++)
                            {
                                corner_cols[k] = game->debug.dbg_visible_authoritative_item_corner_col[v][k];
                                corner_rows[k] = game->debug.dbg_visible_authoritative_item_corner_row[v][k];
                            }
                            break;
                        }
                    }
                }
                if (!WebDiagnosticsAppendProbeText(buf, (int)cap, used,
                                     "%s{\"id\":%u,\"owner_id\":%u,\"item_definition_id\":%u,\"visual_style_id\":%u,\"equip_slot_kind_id\":%u,\"state_flags\":%u,\"x\":%.3f,\"y\":%.3f,\"z\":%.3f,\"half_extent\":%.3f,\"height\":%.3f,\"collision_height\":%.3f,\"collision_radius\":%.3f,\"collision_bottom_z\":%.3f,\"collision_top_z\":%.3f,\"support_top_z\":%.3f,\"visual_bottom_z\":%.3f,\"visual_top_z\":%.3f,\"screen_valid\":%d,\"screen_top_col\":%d,\"screen_top_row\":%d,\"screen_bottom_col\":%d,\"screen_bottom_row\":%d,\"corners_valid\":%d,\"corner_cols\":[%d,%d,%d,%d,%d,%d,%d,%d],\"corner_rows\":[%d,%d,%d,%d,%d,%d,%d,%d]}",
                                     samples ? "," : "",
                                     (unsigned int)ai->item_id,
                                     (unsigned int)ai->owner_id,
                                     (unsigned int)ai->item_definition_id,
                                     (unsigned int)ai->visual_style_id,
                                     (unsigned int)ai->equip_slot_kind_id,
                                     (unsigned int)ai->v2_state_flags,
                                     ai->pos[0], ai->pos[1], ai->pos[2],
                                     collision_radius_units, collision_height_units,
                                     collision_height_units, collision_radius_units,
                                     collision_bottom_z, collision_top_z,
                                     support_top_z, visual_bottom_z, visual_top_z,
                                     screen_valid,
                                     screen_top_col, screen_top_row,
                                     screen_bottom_col, screen_bottom_row,
                                     corners_valid,
                                     corner_cols[0], corner_cols[1], corner_cols[2], corner_cols[3],
                                     corner_cols[4], corner_cols[5], corner_cols[6], corner_cols[7],
                                     corner_rows[0], corner_rows[1], corner_rows[2], corner_rows[3],
                                     corner_rows[4], corner_rows[5], corner_rows[6], corner_rows[7]))
                    break;
                samples++;
            }
        }
        WebDiagnosticsAppendProbeText(buf, (int)cap, used, "]");

        const int collision_debug_valid =
            game ? (int)game->debug.dbg_collision_debug_valid : 0;
        const uint16_t collision_debug_count =
            (game && collision_debug_valid) ? game->debug.dbg_collision_debug_count : 0;
        WebDiagnosticsAppendJsonIntField(buf, (int)cap, used,
            "collision_debug_valid", collision_debug_valid);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used,
            "collision_debug_tick", game ? game->debug.dbg_collision_debug_tick : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used,
            "collision_debug_player_id", game ? (uint32_t)game->debug.dbg_collision_debug_player_id : 0xffffu);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used,
            "collision_debug_support_source", game ? (uint32_t)game->debug.dbg_collision_debug_support_source : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used,
            "collision_debug_push_source", game ? (uint32_t)game->debug.dbg_collision_debug_push_source : 0u);
        WebDiagnosticsAppendJsonUIntField(buf, (int)cap, used,
            "collision_debug_support_item_id", game ? (uint32_t)game->debug.dbg_collision_debug_support_item_id : 0xffffu);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used,
            "collision_debug_player_x", game ? game->debug.dbg_collision_debug_player_pos[0] : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used,
            "collision_debug_player_y", game ? game->debug.dbg_collision_debug_player_pos[1] : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used,
            "collision_debug_player_z", game ? game->debug.dbg_collision_debug_player_pos[2] : 0.0f);
        WebDiagnosticsAppendJsonFloatField(buf, (int)cap, used,
            "collision_debug_support_z", game ? game->debug.dbg_collision_debug_support_z : 0.0f);
        WebDiagnosticsAppendProbeText(buf, (int)cap, used, ",\"collision_debug_samples\":[");
        if (game && collision_debug_valid)
        {
            for (uint16_t i = 0; i < collision_debug_count && used < (int)cap - 1; i++)
            {
                const uint8_t source = game->debug.dbg_collision_debug_sample_source[i];
                const uint8_t flags = game->debug.dbg_collision_debug_sample_flags[i];
                const uint16_t item_id = game->debug.dbg_collision_debug_sample_item_id[i];
                const uint64_t entity_id = game->debug.dbg_collision_debug_sample_entity_id[i];
                const uint64_t inst_id = game->debug.dbg_collision_debug_sample_inst_id[i];
                const uint64_t mesh_id = game->debug.dbg_collision_debug_sample_mesh_id[i];
                const uint32_t face = game->debug.dbg_collision_debug_sample_face_ordinal[i];
                const float* bmin = game->debug.dbg_collision_debug_sample_bmin[i];
                const float* bmax = game->debug.dbg_collision_debug_sample_bmax[i];
                const float* normal = game->debug.dbg_collision_debug_sample_normal[i];
                const int corners_valid = game->debug.dbg_collision_debug_sample_corners_valid[i];
                if (!WebDiagnosticsAppendProbeText(buf, (int)cap, used,
                    "%s{\"index\":%u,\"source\":%u,\"flags\":%u,\"item_id\":%u,"
                    "\"entity_id\":%llu,\"inst_id\":%llu,\"mesh_id\":%llu,\"face\":%u,"
                    "\"bmin\":[%.3f,%.3f,%.3f],\"bmax\":[%.3f,%.3f,%.3f],"
                    "\"normal\":[%.3f,%.3f,%.3f],\"corners_valid\":%d,"
                    "\"corner_cols\":[%d,%d,%d,%d,%d,%d,%d,%d],"
                    "\"corner_rows\":[%d,%d,%d,%d,%d,%d,%d,%d]}",
                    i ? "," : "",
                    (unsigned)i, (unsigned)source, (unsigned)flags, (unsigned)item_id,
                    (unsigned long long)entity_id,
                    (unsigned long long)inst_id,
                    (unsigned long long)mesh_id,
                    (unsigned)face,
                    bmin[0], bmin[1], bmin[2],
                    bmax[0], bmax[1], bmax[2],
                    normal[0], normal[1], normal[2],
                    corners_valid,
                    game->debug.dbg_collision_debug_sample_corner_col[i][0],
                    game->debug.dbg_collision_debug_sample_corner_col[i][1],
                    game->debug.dbg_collision_debug_sample_corner_col[i][2],
                    game->debug.dbg_collision_debug_sample_corner_col[i][3],
                    game->debug.dbg_collision_debug_sample_corner_col[i][4],
                    game->debug.dbg_collision_debug_sample_corner_col[i][5],
                    game->debug.dbg_collision_debug_sample_corner_col[i][6],
                    game->debug.dbg_collision_debug_sample_corner_col[i][7],
                    game->debug.dbg_collision_debug_sample_corner_row[i][0],
                    game->debug.dbg_collision_debug_sample_corner_row[i][1],
                    game->debug.dbg_collision_debug_sample_corner_row[i][2],
                    game->debug.dbg_collision_debug_sample_corner_row[i][3],
                    game->debug.dbg_collision_debug_sample_corner_row[i][4],
                    game->debug.dbg_collision_debug_sample_corner_row[i][5],
                    game->debug.dbg_collision_debug_sample_corner_row[i][6],
                    game->debug.dbg_collision_debug_sample_corner_row[i][7]))
                {
                    break;
                }
            }
        }
        WebDiagnosticsAppendProbeText(buf, (int)cap, used, "]");

        WebDiagnosticsAppendProbeText(buf, (int)cap, used, "}");
        buf[cap - 1] = 0;
        return buf;
    }

// ── BuildActorWearableProofProbeJson ──
//
// FL-4079: single-call atomic probe. Reads server-equipped truth from
// game->player.appearance_v2, renderer-selected fields from game->debug, and
// render-buffer hash/seq from web_diagnostics — all within one C entry-point so
// they cannot straddle a Render() boundary.
//
// GREEN-1 emits everything except the per-cell trace; expected_armor_cells is
// an empty array, roi.cells is an empty string. The harness in
// scripts/proofs/proof_wearable_equipped_shows_in_buffer.js advances from
// probe_export_missing to expected_cells_empty against this build.

const char* BuildActorWearableProofProbeJson(
    char* buf, int cap,
    const Game* game,
    const Server* /*server*/,
    int actor)
{
    if (!buf || cap <= 1) return buf;
    buf[0] = '{'; buf[1] = 0;
    int used = 1;

    // FL-4079: only local actor (0) is supported in GREEN-1. Remote/NPC variants
    // land in L3 (mounted) and L4 (death) ladder steps.
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "actor", actor);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "supported", actor == 0 ? 1 : 0);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "fl", 4079);

    // Render-buffer identity + probe_seq, snapped from game->debug alongside
    // all render-selection fields so every value comes from one consistent frame.
    // The stamp is written into game->debug by web_platform.cpp AFTER the
    // diagnostics probe_seq is bumped (see web_platform.cpp:140-142), preventing
    // the frame-tearing race between diagnostics and debug state reads.
    WebDiagnosticsAppendJsonUIntField(buf, cap, used, "probe_seq",
        (game ? (unsigned)game->debug.dbg_actor_render_probe_seq
              : WebDiagnosticsGetRenderBufProbeSeq()));
    WebDiagnosticsAppendJsonUIntField(buf, cap, used, "render_buf_hash",
        WebDiagnosticsGetRenderBufHash());
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "render_buf_width",
        WebDiagnosticsGetRenderBufSampleWidth());
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "render_buf_height",
        WebDiagnosticsGetRenderBufSampleHeight());
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "render_buf_valid",
        WebDiagnosticsGetRenderBufSampleValid());

    // ── server_truth ──
    WebDiagnosticsAppendProbeText(buf, cap, used, ",\"server_truth\":{");
    if (game)
    {
        const Human& p = game->player;
        const ActorVisualProfile& ap = p.appearance_v2;
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "\"server_truth_source\":\"player.appearance_v2\","
            "\"life_state\":%u,\"mount_state\":%u,\"locomotion_state\":%u,\"combat_state\":%u,"
            "\"presentation_kind_id\":%u,\"presentation_started_tick\":%u,"
            "\"skin_definition_id\":%u,\"mount_definition_id\":%u,\"loadout_revision\":%u,"
            "\"appearance_profile_id\":%u,\"entry_count\":%u",
            (unsigned)p.life_state,
            (unsigned)p.mount_state,
            (unsigned)p.locomotion_state,
            (unsigned)p.combat_state,
            (unsigned)p.presentation_kind_id,
            (unsigned)p.presentation_started_tick,
            (unsigned)ap.skin_definition_id,
            (unsigned)ap.mount_definition_id,
            (unsigned)ap.loadout_revision,
            (unsigned)ap.appearance_profile_id,
            (unsigned)ap.entry_count);

        // Flatten the equipped arrays from appearance_v2.entries[].

        // These were emitted by the server at server_tick.cpp:2922-2926 but
        // never previously flattened into a recorder JSON for the local actor.
        WebDiagnosticsAppendProbeText(buf, cap, used, ",\"equipped_slot_kind_ids\":[");
        for (int i = 0; i < (int)ap.entry_count && i < APPEARANCE_STATE_V2_MAX_ENTRIES; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)ap.entries[i].slot_kind_id);
        WebDiagnosticsAppendProbeText(buf, cap, used, "],\"equipped_definition_ids\":[");
        for (int i = 0; i < (int)ap.entry_count && i < APPEARANCE_STATE_V2_MAX_ENTRIES; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)ap.entries[i].item_definition_id);
        WebDiagnosticsAppendProbeText(buf, cap, used, "],\"equipped_visual_style_ids\":[");
        for (int i = 0; i < (int)ap.entry_count && i < APPEARANCE_STATE_V2_MAX_ENTRIES; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)ap.entries[i].visual_style_id);
        WebDiagnosticsAppendProbeText(buf, cap, used, "]");
    }
    else
    {
        // FL-4079: game is null before Join()/world load. Use raw probe text
        // (no leading comma) so the first field inside the just-opened
        // server_truth object stays well-formed JSON. AppendJsonIntField would
        // prepend a "," because the outer buffer position is > 1, producing
        // `{,"game_null":1}` and a parse error.
        WebDiagnosticsAppendProbeText(buf, cap, used, "\"game_null\":1");
    }
    WebDiagnosticsAppendProbeText(buf, cap, used, "}");

    // ── render_selection ──
    WebDiagnosticsAppendProbeText(buf, cap, used, ",\"render_selection\":{");
    if (game)
    {
        const auto& d = game->debug;
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "\"actor_authoritative_tick\":%d,"
            "\"presentation_started_tick\":%d,"
            "\"playback_elapsed_ticks\":%d,"
            "\"frame_clamped\":%d,"
            "\"frame_changed_expected\":%d,"
            "\"playback_mode\":%u,"
            "\"steady_frame_index\":%u,"
            "\"selected_locomotion_anim_track\":%u,"
            "\"sprite_angle\":%d,"
            "\"sprite_angles\":%d,"
            "\"sprite_anim\":%d,"
            "\"sprite_frame\":%d,"
            "\"atlas_frame_index\":%u,"
            "\"projection_kind\":%u,"
            "\"contribution_angle\":%d,"
            "\"presentation_kind_id\":%u,"
            "\"skin_definition_id\":%u,"
            "\"loadout_signature\":%u,"
            "\"profile_id_hash\":%u,"
            "\"layer_count\":%u",
            d.dbg_actor_authoritative_tick,
            d.dbg_actor_presentation_started_tick,
            d.dbg_actor_playback_elapsed_ticks,
            d.dbg_actor_frame_clamped,
            d.dbg_actor_frame_changed_expected,
            (unsigned)d.dbg_actor_render_playback_mode,
            (unsigned)d.dbg_actor_render_steady_frame_index,
            (unsigned)d.dbg_actor_render_selected_locomotion_anim_track,
            d.dbg_actor_render_sprite_angle,
            d.dbg_actor_render_sprite_angles,
            d.dbg_actor_render_sprite_anim,
            d.dbg_actor_render_sprite_frame,
            (unsigned)d.dbg_actor_render_atlas_frame_index,
            (unsigned)d.dbg_actor_render_contribution_projection,
            d.dbg_actor_render_contribution_angle,
            (unsigned)d.dbg_actor_render_presentation_kind_id,
            (unsigned)d.dbg_actor_render_skin_definition_id,
            (unsigned)d.dbg_actor_render_loadout_signature,
            (unsigned)d.dbg_actor_render_profile_id_hash,
            (unsigned)d.dbg_actor_render_layer_count);

        // Per-layer arrays — match the wearable proof's join key (slot_kind_id).
        const int lc = d.dbg_actor_render_layer_count <= ACTOR_VISUAL_MAX_RENDER_LAYERS
            ? (int)d.dbg_actor_render_layer_count : ACTOR_VISUAL_MAX_RENDER_LAYERS;
        WebDiagnosticsAppendProbeText(buf, cap, used, ",\"actor_render_slot_kind_ids\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)d.dbg_actor_render_slot_kind_ids[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used, "],\"actor_render_definition_ids\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)d.dbg_actor_render_item_definition_ids[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used, "],\"actor_render_visual_style_ids\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)d.dbg_actor_render_visual_style_ids[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "],\"actor_render_layer_contributed_cell_counts\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)d.dbg_actor_render_layer_contributed_cell_counts[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "],\"actor_render_layer_occluded_cell_counts\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)d.dbg_actor_render_layer_occluded_cell_counts[i]);
        // FL-4079 GREEN-2: per-layer source_xp_index — the probe uses this to
        // load the layer's authored XP via LoadActorVisualProfileSourceSprite.
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "],\"actor_render_layer_source_xp_indices\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "", (unsigned)d.dbg_actor_render_layer_source_xp_indices[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "],\"actor_render_layer_semantic_contribution_set_indices\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "",
                (unsigned)d.dbg_actor_render_layer_semantic_contribution_set_indices[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used,
            "],\"actor_render_layer_source_layer_indices\":[");
        for (int i = 0; i < lc; i++)
            WebDiagnosticsAppendProbeText(buf, cap, used, "%s%u",
                i ? "," : "",
                (unsigned)d.dbg_actor_render_layer_source_layer_indices[i]);
        WebDiagnosticsAppendProbeText(buf, cap, used, "]");

        // FL-4079 GREEN-2: body anchor + frame dims + clip rect captured at
        // the body-blit site by render_scene.cpp. These let the proof harness
        // project frame-local armor cells onto screen using the same math the
        // renderer used (dx = ref/2, screen = pos + frame - dx).
        WebDiagnosticsAppendProbeText(buf, cap, used,
            ",\"body_screen_pos_x\":%d,\"body_screen_pos_y\":%d,"
            "\"body_ref_x\":%d,\"body_ref_y\":%d,"
            "\"body_frame_width\":%d,\"body_frame_height\":%d,"
            "\"body_clip_left\":%d,\"body_clip_right\":%d,"
            "\"body_clip_bottom\":%d,\"body_clip_top\":%d",
            d.dbg_actor_render_body_screen_pos_x,
            d.dbg_actor_render_body_screen_pos_y,
            d.dbg_actor_render_body_ref_x,
            d.dbg_actor_render_body_ref_y,
            d.dbg_actor_render_body_frame_width,
            d.dbg_actor_render_body_frame_height,
            d.dbg_actor_render_body_clip_left,
            d.dbg_actor_render_body_clip_right,
            d.dbg_actor_render_body_clip_bottom,
            d.dbg_actor_render_body_clip_top);
    }
    WebDiagnosticsAppendProbeText(buf, cap, used, "}");

    // ── roi: AnsiCell window around the body clip rect, raw_hex from the
    //    actual render_buf. Hash + probe_seq above let the harness verify the
    //    buffer didn't advance between this read and a subsequent full-frame
    //    snapshot (race-kill).
    if (game)
    {
        extern AnsiCell* render_buf; // defined in web/game_web.cpp (web_platform.cpp externs it)
        const auto& d = game->debug;
        const int rb_w = WebDiagnosticsGetRenderBufSampleWidth();
        const int rb_h = WebDiagnosticsGetRenderBufSampleHeight();
        const int roi_x = d.dbg_actor_render_body_clip_left;
        const int roi_y = d.dbg_actor_render_body_clip_bottom;
        const int roi_w = d.dbg_actor_render_body_clip_right - d.dbg_actor_render_body_clip_left;
        const int roi_h = d.dbg_actor_render_body_clip_top - d.dbg_actor_render_body_clip_bottom;
        const bool roi_in_bounds = render_buf
            && rb_w > 0 && rb_h > 0
            && roi_x >= 0 && roi_y >= 0
            && roi_w > 0 && roi_h > 0
            && (roi_x + roi_w) <= rb_w
            && (roi_y + roi_h) <= rb_h;
        WebDiagnosticsAppendProbeText(buf, cap, used,
            ",\"roi\":{\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"in_bounds\":%d,\"cells\":\"",
            roi_x, roi_y, roi_w, roi_h, roi_in_bounds ? 1 : 0);
        if (roi_in_bounds)
        {
            static const char hex[] = "0123456789abcdef";
            // emit row-major bytes for the ROI cells (4 bytes per AnsiCell)
            // budget cap: refuse to overflow the buffer; stop early if needed
            for (int y = roi_y; y < roi_y + roi_h && used + 12 < cap; y++)
            {
                for (int x = roi_x; x < roi_x + roi_w && used + 12 < cap; x++)
                {
                    const AnsiCell& cell = render_buf[x + y * rb_w];
                    const uint8_t bytes[4] = { cell.fg, cell.bk, cell.gl, cell.spare };
                    for (int b = 0; b < 4 && used + 4 < cap; b++)
                    {
                        buf[used++] = hex[(bytes[b] >> 4) & 0x0f];
                        buf[used++] = hex[bytes[b] & 0x0f];
                    }
                }
            }
        }
        WebDiagnosticsAppendProbeText(buf, cap, used, "\"}");
    }
    else
    {
        WebDiagnosticsAppendProbeText(buf, cap, used,
            ",\"roi\":{\"x\":0,\"y\":0,\"w\":0,\"h\":0,\"in_bounds\":0,\"cells\":\"\"}");
    }

    // ── expected wearable cells: cells from the renderer-selected source XP at
    //    the selected (anim, frame, angle, projection=0). No parallel
    //    re-projection; this uses the same LoadActorVisualProfileSourceSprite
    //    cache as the renderer.
    auto append_expected_cells_for_contribution =
        [&](const char* field_name, const char* contribution_fragment, int* out_layer_index,
            int* out_source_xp_index, int* out_source_loaded) -> int
    {
        WebDiagnosticsAppendProbeText(buf, cap, used, ",\"%s\":[", field_name);
        int expected_cells = 0;
        Sprite* source_sprite = 0;
        int source_xp_index = -1;
        int source_layer_index = -1;
        int layer_index = -1;
        if (game)
        {
            const auto& d = game->debug;
            const int lc = d.dbg_actor_render_layer_count <= ACTOR_VISUAL_MAX_RENDER_LAYERS
                ? (int)d.dbg_actor_render_layer_count : ACTOR_VISUAL_MAX_RENDER_LAYERS;
            for (int i = 0; i < lc; i++)
            {
                const uint16_t set_index =
                    d.dbg_actor_render_layer_semantic_contribution_set_indices[i];
                bool contribution_found = false;
                if (set_index < kActorVisualSemanticContributionSetCount)
                {
                    const ActorVisualSemanticContributionSet& set =
                        kActorVisualSemanticContributionSets[set_index];
                    for (uint8_t contribution_index = 0;
                        contribution_index < set.value_count;
                        contribution_index++)
                    {
                        if (set.values[contribution_index] &&
                            strstr(set.values[contribution_index], contribution_fragment))
                        {
                            contribution_found = true;
                            break;
                        }
                    }
                }
                if (contribution_found)
                {
                    layer_index = i;
                    source_xp_index = (int)d.dbg_actor_render_layer_source_xp_indices[i];
                    source_layer_index =
                        (int)d.dbg_actor_render_layer_source_layer_indices[i];
                    break;
                }
            }
            if (source_xp_index >= 0 && source_xp_index < kActorVisualSourceXpCount &&
                source_layer_index >= 0 &&
                source_layer_index < ACTOR_VISUAL_MAX_RENDER_LAYERS)
            {
                source_sprite = LoadActorVisualProfileSourceSprite(
                    (uint16_t)source_xp_index,
                    (uint8_t)source_layer_index);
            }
            if (source_sprite)
            {
                const int sprite_anim = (d.dbg_actor_render_sprite_anim >= 0)
                    ? d.dbg_actor_render_sprite_anim : 0;
                const int sprite_frame = (d.dbg_actor_render_sprite_frame >= 0)
                    ? d.dbg_actor_render_sprite_frame : 0;
                const int sprite_angle = (d.dbg_actor_render_sprite_angle >= 0)
                    ? d.dbg_actor_render_sprite_angle : 0;
                if (sprite_anim < source_sprite->anims
                    && source_sprite->anim[sprite_anim].length > 0
                    && sprite_angle < source_sprite->angles)
                {
                    const int len = source_sprite->anim[sprite_anim].length;
                    const int frame_in_anim = (sprite_frame >= 0) ? (sprite_frame % len) : 0;
                    const int i_idx = frame_in_anim + sprite_angle * len;
                    const int max_idx = source_sprite->anim[sprite_anim].length
                        * source_sprite->angles;
                    if (i_idx < max_idx)
                    {
                        Sprite::Frame* f = source_sprite->atlas
                            + source_sprite->anim[sprite_anim].frame_idx[i_idx];
                        if (f && f->cell)
                        {
                            const int dx = f->ref[0] / 2;
                            const int dy = f->ref[1] / 2;
                            const int sx0 = d.dbg_actor_render_body_screen_pos_x - dx;
                            const int sy0 = d.dbg_actor_render_body_screen_pos_y - dy;
                            for (int fy = 0; fy < f->height; fy++)
                            {
                                for (int fx = 0; fx < f->width; fx++)
                                {
                                    const AnsiCell& cell = f->cell[fx + fy * f->width];
                                    const bool src_empty =
                                        (cell.bk == 255 && cell.fg == 255) ||
                                        ((cell.gl == 32 || cell.gl == 0) && cell.bk == 255) ||
                                        (cell.gl == 219 && cell.fg == 255);
                                    if (src_empty) continue;
                                    if (used + 96 >= cap) break;
                                    WebDiagnosticsAppendProbeText(buf, cap, used,
                                        "%s{\"src_x\":%d,\"src_y\":%d,\"sx\":%d,\"sy\":%d,"
                                        "\"gl\":%u,\"fg\":%u,\"bk\":%u,\"spare\":%u}",
                                        expected_cells ? "," : "",
                                        fx, fy, fx + sx0, fy + sy0,
                                        (unsigned)cell.gl, (unsigned)cell.fg,
                                        (unsigned)cell.bk, (unsigned)cell.spare);
                                    expected_cells++;
                                }
                            }
                        }
                    }
                }
            }
        }
        WebDiagnosticsAppendProbeText(buf, cap, used, "]");
        if (out_layer_index) *out_layer_index = layer_index;
        if (out_source_xp_index) *out_source_xp_index = source_xp_index;
        if (out_source_loaded) *out_source_loaded = source_sprite ? 1 : 0;
        return expected_cells;
    };

    int armor_source_sprite_loaded = 0;
    int armor_source_xp_index = -1;
    int armor_layer_index = -1;
    const int expected_cell_count = append_expected_cells_for_contribution(
        "expected_armor_cells", "armor",
        &armor_layer_index, &armor_source_xp_index, &armor_source_sprite_loaded);
    int shield_source_sprite_loaded = 0;
    int shield_source_xp_index = -1;
    int shield_layer_index = -1;
    const int expected_shield_cell_count = append_expected_cells_for_contribution(
        "expected_shield_cells", "shield",
        &shield_layer_index, &shield_source_xp_index, &shield_source_sprite_loaded);

    // ── diagnostic fields about how the oracle resolved ──
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "armor_layer_index", armor_layer_index);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "armor_source_xp_index", armor_source_xp_index);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "armor_source_sprite_loaded",
        armor_source_sprite_loaded);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "expected_cell_count", expected_cell_count);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "shield_layer_index", shield_layer_index);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "shield_source_xp_index", shield_source_xp_index);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "shield_source_sprite_loaded",
        shield_source_sprite_loaded);
    WebDiagnosticsAppendJsonIntField(buf, cap, used, "expected_shield_cell_count", expected_shield_cell_count);

    // ── green-2 marker ──
    WebDiagnosticsAppendJsonStringField(buf, cap, used, "green_stage", "GREEN-2");

    WebDiagnosticsAppendProbeText(buf, cap, used, "}");
    buf[cap - 1] = 0;
    // FL-4079: detect overflow by checking if used >= cap. The Append functions
    // clamp at cap-1 and return false; if truncated, overwrite with an explicit
    // overflow error so the JS harness sees a distinguishable failure instead of
    // silently truncated JSON.
    if ((used + 4) >= cap)
    {
        static const char overflow_json[] = "{\"error\":\"probe_overflow\","
            "\"detail\":\"BuildActorWearableProofProbeJson exceeded buffer cap\"}";
        int olen = (int)sizeof(overflow_json) - 1;
        if (olen < cap) { memcpy(buf, overflow_json, olen); buf[olen] = 0; }
        else { buf[0] = '{'; buf[1] = 0; }
    }
    return buf;
}

// FL-4079: extern "C" wrapper with a static buffer. Module.cwrap callable from
// JS as window.GetActorWearableProofProbeJson. Sized to fit:
//   probe envelope + server_truth + render_selection (~5 KB)
//   roi.cells raw_hex for body clip rect up to ~64x32 cells (~16 KB)
//   expected_armor_cells[] + expected_shield_cells[] for typical wearable cell
//   counts (~28 KB). 128 KB gives headroom for mounted ROIs without overflow.
static char s_actor_wearable_proof_probe_json[131072];

extern "C" const char* GetActorWearableProofProbeJson(int actor)
{
    // `game` is the global Game* declared in engine/game_api.h (re-declared
    // as extern in web/web_recorder_bridge.cpp:16). `server` is the global
    // `Server* volatile` declared in engine/game.h:103. Both are set by the
    // web runtime before Main() is called; they stay valid for the lifetime
    // of the game session. Single-threaded WASM: no re-entrancy concern.
    return BuildActorWearableProofProbeJson(
        s_actor_wearable_proof_probe_json,
        (int)sizeof(s_actor_wearable_proof_probe_json),
        game, (const Server*)server, actor);
}
