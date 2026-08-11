#pragma once

// debug_telemetry_state.h — Per-frame debug/diagnostic counters
//
// PURPOSE:
// Holds all per-frame debug telemetry: visibility counters, remote0/NPC
// tracking diagnostics, combat/swing probes, item pickup traces, etc.
// Extracted from game.h to reduce god-header mass (740 lines).
//
// DEPENDENCIES: Only primitive types plus actor presentation render constants.
// Does NOT depend on game.h.

#include <stdint.h>
#include "../server/authoritative_item_server_state.h"
#include "../server/protocol/protocol_items.h"
#include "actor_presentation_result.h"

struct RenderFrameReport;
#include "render/render_frame_report.h"

struct DebugTelemetryState
{
	// Apply render frame report facts into debug telemetry (bridge -> telemetry seam).
	void ApplyRenderFrameReport(const RenderFrameReport& report);

	// Debug counters filled during Render(): how many local/remote players
	// are currently projected on-screen in the world view (label projection).
	int dbg_visible_local_players;
	int dbg_visible_remote_players;
		// Body visibility diagnostics. Local body uses a separate final-draw signal;
		// these counters remain as the legacy inst-visible proxy.
		int dbg_visible_local_body_players;
		int dbg_visible_remote_body_players;
		int dbg_visible_remote_label_only_players;
		int dbg_visible_authoritative_npc_markers;
		int dbg_visible_authoritative_item_markers;
		int dbg_minimap_marker_visible_count;
		int dbg_minimap_marker_right_half_visible_count;
		int dbg_minimap_marker_label_chars_drawn;
		int dbg_minimap_marker_right_half_label_chars_drawn;
		int dbg_minimap_remote_expected_count;
		int dbg_minimap_remote_drawn_count;
		uint16_t dbg_visible_authoritative_item_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint8_t dbg_visible_authoritative_item_styles[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint16_t dbg_visible_authoritative_item_definition_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint16_t dbg_visible_authoritative_item_visual_style_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint16_t dbg_visible_authoritative_item_world_sprite_source_hashes[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint16_t dbg_visible_authoritative_item_world_sprite_family_kinds[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint8_t dbg_visible_authoritative_item_visual_failure_reasons[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		// FL-4137 #31: per-visible-row visual extent from sprite proj_bbox.
		// Compared against catalog collision_height in the proof harness to
		// detect render/catalog drift before any "passed" claim.
		float dbg_visible_authoritative_item_visual_bottom_z[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		float dbg_visible_authoritative_item_visual_top_z[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		// FL-4137 #35 / FL-4163: per-visible-row screen projection of the
		// block's TOP (pos.x, pos.y, visual_top_z) and BOTTOM (pos.x, pos.y,
		// visual_bottom_z). Populated in authoritative_world_item_appearance.cpp
		// where ProjectCoords already runs for the item. The visibility
		// regression (proof_fl4137_block_visibility_regression.js) samples
		// the renderbuf inside [screen_top_x..screen_bottom_x] x
		// [screen_top_y..screen_bottom_y] and asserts a block sprite cell
		// (glyph 219, palette pair fg=145/bk=102) appears at the topmost
		// row of that rect. This closes the "visible == world" contract by
		// extending the FL-4079 expected-cells oracle pattern to placed
		// blocks; the test no longer has to count glyphs globally.
		int16_t dbg_visible_authoritative_item_screen_top_col[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		int16_t dbg_visible_authoritative_item_screen_top_row[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		int16_t dbg_visible_authoritative_item_screen_bottom_col[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		int16_t dbg_visible_authoritative_item_screen_bottom_row[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint8_t dbg_visible_authoritative_item_screen_valid[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		// FL-4137 collision wireframe: 8 AABB corners per visible block in
		// cell space. JS overlay in game_web.html reads via auth_item_sample
		// and draws a red wireframe so operator sees collision volume vs
		// rendered sprite extent.
		int16_t dbg_visible_authoritative_item_corner_col[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS][8];
		int16_t dbg_visible_authoritative_item_corner_row[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS][8];
		uint8_t dbg_visible_authoritative_item_corners_valid[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
		uint8_t dbg_collision_debug_valid;
		uint16_t dbg_collision_debug_count;
		uint16_t dbg_collision_debug_player_id;
		uint32_t dbg_collision_debug_tick;
		uint8_t dbg_collision_debug_support_source;
		uint8_t dbg_collision_debug_push_source;
		uint16_t dbg_collision_debug_support_item_id;
		float dbg_collision_debug_player_pos[3];
		float dbg_collision_debug_support_z;
		uint8_t dbg_collision_debug_sample_source[COLLISION_DEBUG_SAMPLE_MAX];
		uint8_t dbg_collision_debug_sample_flags[COLLISION_DEBUG_SAMPLE_MAX];
		uint16_t dbg_collision_debug_sample_item_id[COLLISION_DEBUG_SAMPLE_MAX];
		uint64_t dbg_collision_debug_sample_entity_id[COLLISION_DEBUG_SAMPLE_MAX];
		uint64_t dbg_collision_debug_sample_inst_id[COLLISION_DEBUG_SAMPLE_MAX];
		uint64_t dbg_collision_debug_sample_mesh_id[COLLISION_DEBUG_SAMPLE_MAX];
		uint32_t dbg_collision_debug_sample_face_ordinal[COLLISION_DEBUG_SAMPLE_MAX];
		float dbg_collision_debug_sample_bmin[COLLISION_DEBUG_SAMPLE_MAX][3];
		float dbg_collision_debug_sample_bmax[COLLISION_DEBUG_SAMPLE_MAX][3];
		float dbg_collision_debug_sample_normal[COLLISION_DEBUG_SAMPLE_MAX][3];
		int16_t dbg_collision_debug_sample_corner_col[COLLISION_DEBUG_SAMPLE_MAX][8];
		int16_t dbg_collision_debug_sample_corner_row[COLLISION_DEBUG_SAMPLE_MAX][8];
		uint8_t dbg_collision_debug_sample_corners_valid[COLLISION_DEBUG_SAMPLE_MAX];
		int dbg_tracked_npc0_entity_id;
	float dbg_tracked_npc0_pos[3];
	int dbg_tracked_npc0_on_screen;
	int dbg_tracked_npc0_inst_visible;
	int dbg_tracked_npc0_hp;
	int dbg_tracked_npc0_life_state;
	int dbg_tracked_npc0_needs_physics_step;
	int dbg_tracked_npc0_death_tick;
	int dbg_tracked_npc0_authoritative_tick;
	int dbg_tracked_npc0_presentation_started_tick;
	int dbg_tracked_npc0_corpse_hold_age_ticks;
	int dbg_tracked_npc0_presentation_kind_id;
	int dbg_tracked_npc0_render_presentation_kind_id;
	int dbg_tracked_npc0_sample_owner_stable_frames;
	int dbg_tracked_npc0_sample_owner_ready;
	int dbg_tracked_npc0_render_sprite_family_kind;
	uint16_t dbg_tracked_npc0_render_head_layer_definition_id;
	uint16_t dbg_tracked_npc0_render_profile_id_hash;
	uint16_t dbg_tracked_npc0_render_atlas_frame_index;
	int dbg_tracked_npc0_render_contribution_angle;
	uint8_t dbg_tracked_npc0_render_contribution_projection;
	uint8_t dbg_tracked_npc0_render_contribution_scope;
	uint8_t dbg_tracked_npc0_render_layer_count;
	uint16_t dbg_tracked_npc0_render_slot_kind_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_tracked_npc0_render_layer_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_tracked_npc0_render_layer_visible_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_tracked_npc0_render_layer_semantic_contribution_set_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint8_t dbg_tracked_npc0_render_layer_source_layer_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_tracked_npc0_render_layer_source_path_hashes[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_tracked_npc0_render_layer_contributed_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_tracked_npc0_render_layer_occluded_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint32_t dbg_tracked_npc0_render_attachment_expected_mask;
	uint32_t dbg_tracked_npc0_render_attachment_source_visible_mask;
	uint32_t dbg_tracked_npc0_render_attachment_source_missing_mask;
	uint8_t dbg_tracked_npc0_render_compose_mode;
	uint8_t dbg_tracked_npc0_render_compose_failure_stage;
	uint16_t dbg_tracked_npc0_render_compose_failure_base_layer_definition_id;
	uint16_t dbg_tracked_npc0_render_compose_failure_overlay_layer_definition_id;
	uint16_t dbg_tracked_npc0_render_compose_failure_overlay_slot_kind_id;
	int dbg_tracked_npc0_anim;
	int dbg_tracked_npc0_frame;
	int dbg_tracked_npc0_anim_length;
	int dbg_tracked_npc0_frame_clamped;
	int dbg_tracked_npc0_frame_changed_expected;
	int dbg_tracked_npc0_render_diverged_from_snapshot;
	int dbg_tracked_npc0_corpse_visible;
	int dbg_tracked_npc0_sprite_miss_frames;
	int dbg_tracked_npc0_selector_failure_reason;
	int dbg_tracked_npc0_bundle_selector_failure_reason;
	int dbg_tracked_npc0_inst_create_count;
	int dbg_tracked_npc0_inst_delete_count;
	int dbg_tracked_npc0_last_inst_delete_reason;
	int dbg_tracked_npc0_last_inst_delete_miss_frames;
	int dbg_tracked_npc0_hp_bar_expected;
	int dbg_tracked_npc0_hp_bar_drawn;
	int dbg_tracked_npc0_body_blit_attempted;
	int dbg_tracked_npc0_body_drew_any;
	int dbg_tracked_npc0_body_candidate_cells;
	int dbg_tracked_npc0_body_depth_pass_cells;
	int dbg_tracked_npc0_body_depth_fail_cells;
	int dbg_tracked_npc0_body_depth_fail_mesh_cells;
	int dbg_tracked_npc0_body_depth_fail_mesh_samples;
	int dbg_tracked_npc0_body_water_reject_cells;
	int dbg_tracked_npc0_body_clip_reject;
	int dbg_tracked_npc0_body_reject_reason;
	int dbg_tracked_npc0_body_clip_left;
	int dbg_tracked_npc0_body_clip_right;
	int dbg_tracked_npc0_body_clip_bottom;
	int dbg_tracked_npc0_body_clip_top;
	int dbg_tracked_npc0_body_unclipped_left;
	int dbg_tracked_npc0_body_unclipped_right;
	int dbg_tracked_npc0_body_unclipped_bottom;
	int dbg_tracked_npc0_body_unclipped_top;
	int dbg_tracked_npc0_body_frame_width;
	int dbg_tracked_npc0_body_frame_height;
	int dbg_tracked_npc0_body_ref_x;
	int dbg_tracked_npc0_body_ref_y;
	int dbg_tracked_npc0_body_screen_pos_x;
	int dbg_tracked_npc0_body_screen_pos_y;
	float dbg_tracked_npc0_body_blit_pos_z;
	float dbg_tracked_npc0_body_water_plane_z;
	float dbg_tracked_npc0_body_candidate_min_z;
	float dbg_tracked_npc0_body_candidate_max_z;
	int dbg_tracked_npc0_body_water_retry_attempted;
	int dbg_tracked_npc0_body_water_retry_drew_any;
	float dbg_tracked_npc0_body_water_retry_lift_z;
	int dbg_tracked_npc0_body_support_retry_attempted;
	int dbg_tracked_npc0_body_support_retry_drew_any;
	float dbg_tracked_npc0_body_support_retry_lift_z;
	float dbg_tracked_npc0_body_support_height_z;
	int dbg_tracked_npc0_body_second_retry_attempted;
	int dbg_tracked_npc0_body_second_retry_drew_any;
	float dbg_tracked_npc0_body_second_retry_lift_z;
	int dbg_tracked_npc0_body_reject_reason_before_second_retry;
	int dbg_auth_pickup_req_attempts;
	int dbg_auth_pickup_req_sent;
	int dbg_auth_pickup_req_send_fail;
	int dbg_auth_pickup_req_last_index;
	uint16_t dbg_auth_pickup_req_last_item_id;
	int dbg_auth_pickup_req_last_reason;
	uint16_t dbg_auth_pickup_req_source_strip_item_id;
	int dbg_auth_pickup_req_source_strip_count;
	int dbg_auth_use_req_attempts;
	int dbg_auth_use_req_sent;
	int dbg_auth_use_req_send_fail;
	int dbg_auth_use_req_last_index;
	uint16_t dbg_auth_use_req_last_item_id;
	int dbg_auth_use_req_last_reason;
	int dbg_auth_place_req_attempts;
	int dbg_auth_place_req_sent;
	int dbg_auth_place_req_send_fail;
	int dbg_auth_place_req_last_index;
	uint16_t dbg_auth_place_req_last_item_id;
	int dbg_auth_place_req_last_reason;
	int dbg_auth_item_local_event_kind;
	uint16_t dbg_auth_item_local_event_item_id;
	uint16_t dbg_auth_item_local_event_owner_id;
	int dbg_auth_item_local_event_sync_calls;
		int dbg_auth_pose_sample_calls;
	int dbg_auth_pose_fallback_count;
	int dbg_auth_pose_fallback_live_session_count;
	int dbg_attack_key_attempts;
	int dbg_attack_setaction_success;
	int dbg_attack_setaction_fail;
		int dbg_attack_last_req_action_before;
	int dbg_attack_last_req_action_after;
	int dbg_mp_swing_cooldown_expired;
	int dbg_mp_swing_send_attempts;
	uint16_t dbg_mp_swing_last_target_id;
	int dbg_mp_swing_last_selected_kind; // 0=none,1=player,2=npc
	float dbg_mp_swing_last_player_dir;
	int dbg_mp_swing_last_remote_candidates;
	int dbg_mp_swing_last_remote_in_cone;
	int dbg_mp_swing_last_best_remote_id;
	float dbg_mp_swing_last_best_remote_dd;
	float dbg_mp_swing_last_best_remote_dif;
	float dbg_mp_swing_eval_remote_pos[2];
	float dbg_mp_swing_eval_rdd;
	float dbg_mp_swing_eval_dif;
	int dbg_mp_swing_eval_remote_hp;
	int dbg_mp_swing_eval_remote_death_stamp;
	int dbg_mp_swing_last_npc_candidates;
	int dbg_mp_swing_last_npc_in_cone;
	int dbg_mp_swing_last_best_npc_id;
	float dbg_mp_swing_last_best_npc_dd;
	float dbg_mp_swing_last_best_npc_dif;
	// Sticky fire-frame telemetry (proof-24): written only on actual swing fire frame
	float dbg_mp_swing_fire_player_pos[2];
	float dbg_mp_swing_fire_player_dir;
	float dbg_mp_swing_fire_player_pos_z;
	float dbg_mp_swing_fire_player_terrain_z;
	float dbg_mp_swing_fire_player_world_z;
	float dbg_mp_swing_fire_player_support_z;
	float dbg_mp_swing_fire_player_support_dz;
	int dbg_mp_swing_fire_player_support_kind; // 0=none,1=terrain,2=world
	float dbg_mp_swing_fire_remote_pos[2];
	float dbg_mp_swing_fire_remote_pos_z;
	float dbg_mp_swing_fire_remote_terrain_z;
	float dbg_mp_swing_fire_remote_world_z;
	float dbg_mp_swing_fire_remote_support_z;
	float dbg_mp_swing_fire_remote_support_dz;
	int dbg_mp_swing_fire_remote_support_kind; // 0=none,1=terrain,2=world
	float dbg_mp_swing_fire_rdd;
	float dbg_mp_swing_fire_dif;
	int dbg_mp_swing_fire_remote_hp;
	int dbg_mp_swing_fire_remote_death_stamp;
	// Remote inst health diagnostics (all remotes, latest frame).
	int dbg_remote_inst_missing_players;
	int dbg_remote_inst_hidden_players;
	int dbg_remote_sprite_null_players;
	int dbg_remote_inst_delete_count;
	int dbg_remote_inst_create_count;
	int dbg_snapshot_npc_inst_delete_count;
	int dbg_snapshot_npc_inst_create_count;
	int dbg_snapshot_npc_sprite_null_count;
	int dbg_snapshot_npc_sprite_miss_total;
	// Stable snapshot of the last completed frame for async web probe reads.
	int dbg_last_visible_local_players;
	int dbg_last_visible_remote_players;
	int dbg_last_visible_local_body_players;
	int dbg_last_visible_remote_body_players;
	int dbg_last_visible_remote_label_only_players;
	int dbg_last_remote_inst_missing_players;
	int dbg_last_remote_inst_hidden_players;
	int dbg_last_remote_sprite_null_players;
	int dbg_last_tracked_npc0_entity_id;
	float dbg_last_tracked_npc0_pos[3];
	int dbg_last_tracked_npc0_on_screen;
	int dbg_last_tracked_npc0_inst_visible;
	int dbg_last_tracked_npc0_hp;
	int dbg_last_tracked_npc0_life_state;
	int dbg_last_tracked_npc0_needs_physics_step;
	int dbg_last_tracked_npc0_death_tick;
	int dbg_last_tracked_npc0_authoritative_tick;
	int dbg_last_tracked_npc0_presentation_started_tick;
	int dbg_last_tracked_npc0_corpse_hold_age_ticks;
	int dbg_last_tracked_npc0_presentation_kind_id;
	int dbg_last_tracked_npc0_render_presentation_kind_id;
	int dbg_last_tracked_npc0_sample_owner_stable_frames;
	int dbg_last_tracked_npc0_sample_owner_ready;
	int dbg_last_tracked_npc0_render_sprite_family_kind;
	uint16_t dbg_last_tracked_npc0_render_head_layer_definition_id;
	uint16_t dbg_last_tracked_npc0_render_profile_id_hash;
	uint16_t dbg_last_tracked_npc0_render_atlas_frame_index;
	int dbg_last_tracked_npc0_render_contribution_angle;
	uint8_t dbg_last_tracked_npc0_render_contribution_projection;
	uint8_t dbg_last_tracked_npc0_render_contribution_scope;
	uint8_t dbg_last_tracked_npc0_render_layer_count;
	uint16_t dbg_last_tracked_npc0_render_slot_kind_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_last_tracked_npc0_render_layer_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_last_tracked_npc0_render_layer_visible_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_last_tracked_npc0_render_layer_semantic_contribution_set_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint8_t dbg_last_tracked_npc0_render_layer_source_layer_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_last_tracked_npc0_render_layer_source_path_hashes[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_last_tracked_npc0_render_layer_contributed_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_last_tracked_npc0_render_layer_occluded_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint32_t dbg_last_tracked_npc0_render_attachment_expected_mask;
	uint32_t dbg_last_tracked_npc0_render_attachment_source_visible_mask;
	uint32_t dbg_last_tracked_npc0_render_attachment_source_missing_mask;
	uint8_t dbg_last_tracked_npc0_render_compose_mode;
	uint8_t dbg_last_tracked_npc0_render_compose_failure_stage;
	uint16_t dbg_last_tracked_npc0_render_compose_failure_base_layer_definition_id;
	uint16_t dbg_last_tracked_npc0_render_compose_failure_overlay_layer_definition_id;
	uint16_t dbg_last_tracked_npc0_render_compose_failure_overlay_slot_kind_id;
	int dbg_last_tracked_npc0_anim;
	int dbg_last_tracked_npc0_frame;
	int dbg_last_tracked_npc0_anim_length;
	int dbg_last_tracked_npc0_frame_clamped;
	int dbg_last_tracked_npc0_frame_changed_expected;
	int dbg_last_tracked_npc0_render_diverged_from_snapshot;
	int dbg_last_tracked_npc0_corpse_visible;
	int dbg_last_tracked_npc0_sprite_miss_frames;
	int dbg_last_tracked_npc0_selector_failure_reason;
	int dbg_last_tracked_npc0_bundle_selector_failure_reason;
	int dbg_last_tracked_npc0_inst_create_count;
	int dbg_last_tracked_npc0_inst_delete_count;
	int dbg_last_tracked_npc0_last_inst_delete_reason;
	int dbg_last_tracked_npc0_last_inst_delete_miss_frames;
	int dbg_last_tracked_npc0_hp_bar_expected;
	int dbg_last_tracked_npc0_hp_bar_drawn;
	int dbg_last_tracked_npc0_body_blit_attempted;
	int dbg_last_tracked_npc0_body_drew_any;
	int dbg_last_tracked_npc0_body_candidate_cells;
	int dbg_last_tracked_npc0_body_depth_pass_cells;
	int dbg_last_tracked_npc0_body_depth_fail_cells;
	int dbg_last_tracked_npc0_body_depth_fail_mesh_cells;
	int dbg_last_tracked_npc0_body_depth_fail_mesh_samples;
	int dbg_last_tracked_npc0_body_water_reject_cells;
	int dbg_last_tracked_npc0_body_clip_reject;
	int dbg_last_tracked_npc0_body_reject_reason;
	int dbg_last_tracked_npc0_body_clip_left;
	int dbg_last_tracked_npc0_body_clip_right;
	int dbg_last_tracked_npc0_body_clip_bottom;
	int dbg_last_tracked_npc0_body_clip_top;
	int dbg_last_tracked_npc0_body_unclipped_left;
	int dbg_last_tracked_npc0_body_unclipped_right;
	int dbg_last_tracked_npc0_body_unclipped_bottom;
	int dbg_last_tracked_npc0_body_unclipped_top;
	int dbg_last_tracked_npc0_body_frame_width;
	int dbg_last_tracked_npc0_body_frame_height;
	int dbg_last_tracked_npc0_body_ref_x;
	int dbg_last_tracked_npc0_body_ref_y;
	int dbg_last_tracked_npc0_body_screen_pos_x;
	int dbg_last_tracked_npc0_body_screen_pos_y;
	float dbg_last_tracked_npc0_body_blit_pos_z;
	float dbg_last_tracked_npc0_body_water_plane_z;
	float dbg_last_tracked_npc0_body_candidate_min_z;
	float dbg_last_tracked_npc0_body_candidate_max_z;
	int dbg_last_tracked_npc0_body_water_retry_attempted;
	int dbg_last_tracked_npc0_body_water_retry_drew_any;
	float dbg_last_tracked_npc0_body_water_retry_lift_z;
	int dbg_last_tracked_npc0_body_support_retry_attempted;
	int dbg_last_tracked_npc0_body_support_retry_drew_any;
	float dbg_last_tracked_npc0_body_support_retry_lift_z;
	float dbg_last_tracked_npc0_body_support_height_z;
	int dbg_last_tracked_npc0_body_second_retry_attempted;
	int dbg_last_tracked_npc0_body_second_retry_drew_any;
	float dbg_last_tracked_npc0_body_second_retry_lift_z;
	int dbg_last_tracked_npc0_body_reject_reason_before_second_retry;
	float dbg_last_remote0_pos[3];
	int dbg_last_remote0_view_x;
	int dbg_last_remote0_view_y;
	int dbg_last_remote0_on_screen;
	int dbg_last_remote0_has_sprite;
	int dbg_last_remote0_has_inst;
	int dbg_last_remote0_inst_world_match;
	int dbg_last_remote0_inst_visible;
	float dbg_last_camera_yaw;
	int dbg_last_remote0_pid;
	int dbg_last_remote0_hp;
	int dbg_last_remote0_life_state;
	int dbg_last_remote0_mount_state;
	int dbg_last_remote0_locomotion_state;
	int dbg_last_remote0_combat_state;
	int dbg_last_remote0_anim;
	int dbg_last_remote0_frame;
	int dbg_last_remote0_anim_length;
	int dbg_last_remote0_frame_clamped;
	int dbg_last_remote0_frame_changed_expected;
	int dbg_last_remote0_authoritative_tick;
	int dbg_last_remote0_presentation_started_tick;
	int dbg_last_remote0_playback_elapsed_ticks;
	int dbg_last_remote0_death_tick;
	int dbg_last_remote0_corpse_hold_age_ticks;
	int dbg_last_remote0_render_diverged_from_snapshot;
	uint64_t dbg_last_remote0_death_stamp;
	int dbg_last_remote0_would_skip_death_check;
	int dbg_last_remote0_in_list;
	int dbg_render_linked_remote_count;
	int dbg_render_local_seen;
	int dbg_render_remote0_seen;
	int dbg_last_render_linked_remote_count;
	int dbg_last_render_local_seen;
	int dbg_last_render_remote0_seen;
	int dbg_remote0_interp_create_attempts;
	int dbg_remote0_interp_create_successes;
	int dbg_remote0_recreate_attempts;
	int dbg_remote0_recreate_successes;
	int dbg_remote0_post_interp_has_inst;
	int dbg_remote0_post_interp_inst_world_match;
	float dbg_remote0_post_interp_pos[3];
	int dbg_remote0_post_interp_view_x;
	int dbg_remote0_post_interp_view_y;
	int dbg_remote0_post_interp_on_screen;
	int dbg_remote0_post_interp_label_visible;
	int dbg_remote0_post_interp_body_visible;
	int dbg_remote0_post_interp_label_only;
	int dbg_remote0_interp_active, dbg_remote0_interp_ring_depth;
		float dbg_remote0_interp_delay_ms;
		float dbg_remote0_interp_lerp_t;
		int dbg_remote0_interp_fallback_mode;
		int dbg_remote0_interp_join_flush_fired;
		int dbg_remote0_interp_newest_tick;
		int dbg_remote0_interp_older_tick;
		float dbg_remote0_interp_newest_wall_age_ms;
		float dbg_remote0_interp_older_wall_age_ms;
		float dbg_remote0_interp_target_age_ms;
		int dbg_actor_final_body_drawn;
		int dbg_remote0_final_label_drawn;
		int dbg_remote0_final_body_drawn;
		int dbg_remote0_final_label_only_drawn;
	int dbg_remote0_query_seen;
	int dbg_remote0_queue_enqueued;
	int dbg_remote0_queue_skip_reason;
	int dbg_remote0_final_blit_attempted;
	int dbg_remote0_tracked_buf_blit_invoked;
	int dbg_remote0_tracked_buf_drew_any;
	int dbg_remote0_blit_clamp_eligible;
	int dbg_remote0_blit_clamp_center_in_bounds;
	int dbg_remote0_blit_clamp_support_found;
	int dbg_remote0_blit_clamp_support_samples;
	int dbg_remote0_blit_clamp_applied;
	float dbg_remote0_blit_pre_clamp_s_pos_z;
	float dbg_remote0_blit_post_clamp_s_pos_z;
	float dbg_remote0_blit_clamp_support_height;
	float dbg_remote0_blit_clamp_floor_z;
	int dbg_remote0_blit_post_clamp_rewrite;
	float dbg_remote0_blit_center_sample_height[4];
	int dbg_remote0_blit_center_sample_spare[4];
	int dbg_remote0_blit_center_sample_valid[4];
	float dbg_remote0_blit_s_pos_z;           // screen-space Z of tracked remote sprite
	float dbg_remote0_blit_terrain_height_center; // terrain depth at sprite center
	int dbg_remote0_depth_pass_cells;             // H9 diag: cells where mask != 0 (some depth passed)
	int dbg_remote0_depth_fail_cells;             // H9 diag: cells where mask == 0 (all depth failed)
	int dbg_remote0_body_total_cells;            // tracked-remote sprite cells considered for terrain/depth coverage
	int dbg_remote0_body_visible_cells;          // tracked-remote sprite cells that passed terrain/depth coverage
	int dbg_remote0_body_occluded_cells;         // tracked-remote sprite cells rejected by terrain/depth coverage
	float dbg_remote0_body_visible_fraction;     // visible / total for tracked remote body coverage
	int dbg_remote0_last_remote_blit_pid;
	int dbg_remote0_last_remote_blit_matches_head;
	int dbg_remote0_inst_cookie_match;
	int dbg_remote0_sprite_family_kind;
	int dbg_remote0_death_transition_count;
	int dbg_remote0_first_death_transition_source;
	int dbg_remote0_death_transition_source31_count;
	int dbg_remote0_death_transition_source21_count;
	int dbg_remote0_death_transition_source24_count;
	int dbg_remote0_first_death_transition_setaction_ok;
	int dbg_remote0_first_death_transition_post_action;
	int dbg_remote0_first_death_transition_post_mount;
	int dbg_remote0_first_death_transition_frame;
	int dbg_remote0_first_death_transition_sprite_family_kind;
	int dbg_remote0_last_death_transition_source;
	int dbg_remote0_last_death_transition_setaction_ok;
	int dbg_remote0_last_death_transition_pre_action;
		int dbg_remote0_last_death_transition_pre_mount;
		int dbg_remote0_last_death_transition_post_action;
		int dbg_remote0_last_death_transition_post_mount;
		int dbg_remote0_last_death_transition_frame;
		int dbg_remote0_last_death_transition_sprite_family_kind;
		int dbg_remote0_death_snapshot_count;
		int dbg_remote0_first_death_snapshot_life_state;
		int dbg_remote0_first_death_snapshot_mount_state;
		int dbg_remote0_first_death_snapshot_locomotion_state;
		int dbg_remote0_first_death_snapshot_presentation_kind_id;
		uint32_t dbg_remote0_first_death_snapshot_tick;
		int dbg_remote0_last_death_snapshot_life_state;
		int dbg_remote0_last_death_snapshot_mount_state;
		int dbg_remote0_last_death_snapshot_locomotion_state;
		int dbg_remote0_last_death_snapshot_presentation_kind_id;
		uint32_t dbg_remote0_last_death_snapshot_tick;
		uint32_t dbg_remote0_death_seq;
		uint32_t dbg_remote0_last_death_source;
	uint32_t dbg_remote0_respawn_seq;
	uint32_t dbg_remote0_corpse_create_seq;
	uint32_t dbg_remote0_corpse_delete_seq;
	uint32_t dbg_remote0_corpse_create_count;
	uint32_t dbg_remote0_corpse_delete_count;
	uint32_t dbg_remote0_last_corpse_create_reason;
	uint32_t dbg_remote0_last_corpse_delete_reason;
	int dbg_remote0_recreate_last_reason;
	int dbg_remote0_recreate_last_snap_to_target;
	int dbg_remote0_recreate_last_was_dead;
	int dbg_remote0_recreate_last_had_inst;
	int dbg_remote0_recreate_trigger_on_screen;
	int dbg_remote0_recreate_trigger_label_visible;
	int dbg_remote0_recreate_trigger_body_visible;
	int dbg_remote0_recreate_trigger_label_only;
	int dbg_remote0_recreate_last_pre_visible;
	int dbg_remote0_recreate_last_post_visible;
	int dbg_remote0_recreate_last_post_cookie_match;
	int dbg_remote0_inst_event_last_kind;
	int dbg_remote0_inst_event_last_reason;
	int dbg_remote0_inst_event_last_was_dead;
	int dbg_remote0_inst_event_last_had_inst;
	int dbg_remote0_inst_event_last_pre_visible;
	int dbg_remote0_inst_event_last_post_visible;
	int dbg_remote0_inst_event_last_post_cookie_match;
	int dbg_remote0_last_tgt_write_source;
	int dbg_remote0_last_tgt_write_pid;
	int dbg_last_remote0_post_interp_has_inst;
	int dbg_last_remote0_post_interp_inst_world_match;
	float dbg_last_remote0_post_interp_pos[3];
	int dbg_last_remote0_post_interp_view_x;
	int dbg_last_remote0_post_interp_view_y;
	int dbg_last_remote0_post_interp_on_screen;
	int dbg_last_remote0_post_interp_label_visible;
	int dbg_last_remote0_post_interp_body_visible;
	int dbg_last_remote0_post_interp_label_only;
		int dbg_last_remote0_interp_active, dbg_last_remote0_interp_ring_depth;
		float dbg_last_remote0_interp_delay_ms;
		float dbg_last_remote0_interp_lerp_t;
		int dbg_last_remote0_interp_fallback_mode;
		int dbg_last_remote0_interp_join_flush_fired;
		int dbg_last_remote0_interp_newest_tick;
		int dbg_last_remote0_interp_older_tick;
		float dbg_last_remote0_interp_newest_wall_age_ms;
		float dbg_last_remote0_interp_older_wall_age_ms;
		float dbg_last_remote0_interp_target_age_ms;
		int dbg_last_actor_final_body_drawn;
		int dbg_last_remote0_final_label_drawn;
		int dbg_last_remote0_final_body_drawn;
		int dbg_last_remote0_final_label_only_drawn;
	int dbg_last_remote0_query_seen;
	int dbg_last_remote0_queue_enqueued;
	int dbg_last_remote0_queue_skip_reason;
	int dbg_last_remote0_final_blit_attempted;
	int dbg_last_remote0_tracked_buf_blit_invoked;
	int dbg_last_remote0_tracked_buf_drew_any;
	int dbg_last_remote0_blit_clamp_eligible;
	int dbg_last_remote0_blit_clamp_center_in_bounds;
	int dbg_last_remote0_blit_clamp_support_found;
	int dbg_last_remote0_blit_clamp_support_samples;
	int dbg_last_remote0_blit_clamp_applied;
	float dbg_last_remote0_blit_pre_clamp_s_pos_z;
	float dbg_last_remote0_blit_post_clamp_s_pos_z;
	float dbg_last_remote0_blit_clamp_support_height;
	float dbg_last_remote0_blit_clamp_floor_z;
	int dbg_last_remote0_blit_post_clamp_rewrite;
	float dbg_last_remote0_blit_center_sample_height[4];
	int dbg_last_remote0_blit_center_sample_spare[4];
	int dbg_last_remote0_blit_center_sample_valid[4];
	float dbg_last_remote0_blit_s_pos_z;
	float dbg_last_remote0_blit_terrain_height_center;
	int dbg_last_remote0_depth_pass_cells;
	int dbg_last_remote0_depth_fail_cells;
	int dbg_last_remote0_body_total_cells;
	int dbg_last_remote0_body_visible_cells;
	int dbg_last_remote0_body_occluded_cells;
	float dbg_last_remote0_body_visible_fraction;
	int dbg_last_remote0_last_remote_blit_pid;
	int dbg_last_remote0_last_remote_blit_matches_head;
	int dbg_last_remote0_inst_cookie_match;
	int dbg_last_remote0_sprite_family_kind;
	int dbg_last_remote0_death_transition_count;
	int dbg_last_remote0_first_death_transition_source;
	int dbg_last_remote0_death_transition_source31_count;
	int dbg_last_remote0_death_transition_source21_count;
	int dbg_last_remote0_death_transition_source24_count;
	int dbg_last_remote0_first_death_transition_setaction_ok;
	int dbg_last_remote0_first_death_transition_post_action;
	int dbg_last_remote0_first_death_transition_post_mount;
	int dbg_last_remote0_first_death_transition_frame;
	int dbg_last_remote0_first_death_transition_sprite_family_kind;
	int dbg_last_remote0_last_death_transition_source;
	int dbg_last_remote0_last_death_transition_setaction_ok;
	int dbg_last_remote0_last_death_transition_pre_action;
		int dbg_last_remote0_last_death_transition_pre_mount;
		int dbg_last_remote0_last_death_transition_post_action;
		int dbg_last_remote0_last_death_transition_post_mount;
		int dbg_last_remote0_last_death_transition_frame;
		int dbg_last_remote0_last_death_transition_sprite_family_kind;
		int dbg_last_remote0_death_snapshot_count;
		int dbg_last_remote0_first_death_snapshot_life_state;
		int dbg_last_remote0_first_death_snapshot_mount_state;
		int dbg_last_remote0_first_death_snapshot_locomotion_state;
		int dbg_last_remote0_first_death_snapshot_presentation_kind_id;
		uint32_t dbg_last_remote0_first_death_snapshot_tick;
		int dbg_last_remote0_last_death_snapshot_life_state;
		int dbg_last_remote0_last_death_snapshot_mount_state;
		int dbg_last_remote0_last_death_snapshot_locomotion_state;
		int dbg_last_remote0_last_death_snapshot_presentation_kind_id;
		uint32_t dbg_last_remote0_last_death_snapshot_tick;
		uint32_t dbg_last_remote0_death_seq;
	uint32_t dbg_last_remote0_last_death_source;
	uint32_t dbg_last_remote0_respawn_seq;
	uint32_t dbg_last_remote0_corpse_create_seq;
	uint32_t dbg_last_remote0_corpse_delete_seq;
	uint32_t dbg_last_remote0_corpse_create_count;
	uint32_t dbg_last_remote0_corpse_delete_count;
	uint32_t dbg_last_remote0_last_corpse_create_reason;
	uint32_t dbg_last_remote0_last_corpse_delete_reason;
	int dbg_last_remote0_recreate_last_reason;
	int dbg_last_remote0_recreate_last_snap_to_target;
	int dbg_last_remote0_recreate_last_was_dead;
	int dbg_last_remote0_recreate_last_had_inst;
	int dbg_last_remote0_recreate_trigger_on_screen;
	int dbg_last_remote0_recreate_trigger_label_visible;
	int dbg_last_remote0_recreate_trigger_body_visible;
	int dbg_last_remote0_recreate_trigger_label_only;
	int dbg_last_remote0_recreate_last_pre_visible;
	int dbg_last_remote0_recreate_last_post_visible;
	int dbg_last_remote0_recreate_last_post_cookie_match;
	int dbg_last_remote0_inst_event_last_kind;
	int dbg_last_remote0_inst_event_last_reason;
	int dbg_last_remote0_inst_event_last_was_dead;
	int dbg_last_remote0_inst_event_last_had_inst;
	int dbg_last_remote0_inst_event_last_pre_visible;
	int dbg_last_remote0_inst_event_last_post_visible;
	int dbg_last_remote0_inst_event_last_post_cookie_match;
	// Latched diagnostics (persist across frames until explicit reset).
	int dbg_latched_remote_visibility_issue_frames;
	int dbg_latched_remote_label_only_events;
	int dbg_latched_remote_inst_missing_events;
	int dbg_latched_remote_inst_hidden_events;
	int dbg_latched_remote_sprite_null_events;
	uint64_t dbg_latched_last_remote_visibility_issue_stamp;
	// Per-frame diagnostics for first remote player (camera/projection debugging).
	float dbg_remote0_pos[3];
	int dbg_remote0_view_x;
	int dbg_remote0_view_y;
	int dbg_remote0_on_screen;
	int dbg_remote0_has_sprite;
	int dbg_remote0_has_inst;
	int dbg_remote0_inst_world_match;
	int dbg_remote0_inst_visible;
	float dbg_camera_yaw;
	// Lifecycle event counters (monotonic, never reset after init).
	int dbg_remote_join_events;
	int dbg_remote_leave_events;
	int dbg_roster_evict_count;
	int dbg_roster_last_evicted_pid;
	// Remote0 identity: slot index of first remote in HP bar loop, -1 if none.
	int dbg_remote0_pid;
	// Remote0 death-check diagnostics (captured BEFORE death-skip continue).
	int dbg_remote0_hp;
	uint64_t dbg_remote0_death_stamp;
	int dbg_remote0_would_skip_death_check;
	int dbg_remote0_in_list;
	// Remote0 authoritative snapshot diagnostics (trace stale guard / packet flow).
	int dbg_remote0_pose_apply_count;     // monotonic: total apply_remote_snapshot calls for remote0
	int dbg_remote0_pose_stale_rejected;  // monotonic: stale guard rejections
	int dbg_remote0_last_pose_reason;     // latched-last-call: 0=none,1=accepted,2=stale_reject,3=force_accept,4=sane_fail
	int dbg_remote0_pose_source;          // latched-last-call: 0=none,1=pose_pkt,2=snapshot
	int dbg_remote0_last_pose_packet_kind; // latched-last-call: 0=none,1=pose_pkt,2=snapshot_baseline,3=snapshot_delta
	int dbg_remote0_last_pose_entity_id;  // latched-last-call: entity/client id passed to apply_remote_snapshot
	uint32_t dbg_remote0_last_pose_packet_seq;  // latched-last-call: snapshot packet seq, 0 for pose_pkt
	uint32_t dbg_remote0_last_pose_packet_tick; // latched-last-call: snapshot packet tick, 0 for pose_pkt
	uint32_t dbg_remote0_last_pose_entity_tick; // latched-last-call: entity last_authoritative_tick, 0 for pose_pkt
	int dbg_snap_tracked_seen;
	int dbg_snap_tracked_applied;
	int dbg_snap_tracked_pose_rejected;
	int dbg_snap_tracked_remove_seen;
	uint16_t dbg_snap_tracked_last_remove_flags;
	uint32_t dbg_snap_tracked_last_remove_seq;
	uint32_t dbg_snap_tracked_last_remove_tick;
	int dbg_server_local_id;              // per-frame: server->connection.local_id snapshot for is_r0 verification
	float dbg_remote0_last_received_dir;  // latched: dir value from last apply_remote_snapshot for remote0
	int dbg_remote0_dir_change_events;    // monotonic: received dir differs from previous by >1 degree
	int dbg_remote0_has_prev_dir;         // flag: 0 until first dir sample received for remote0
	// Facing pipeline A/B/C/D diagnostics (Phase 1 diag)
		float dbg_actor_render_dir;           // per-frame: player.dir used for UpdateSpriteInst
		int dbg_actor_life_state;             // per-frame: player.life_state
		int dbg_actor_mount_state;            // per-frame: player.mount_state
	int dbg_actor_mount_layer_count;      // per-frame: count of mount-role layers resolved (FL-2378)
		float dbg_sent_pose_dir;              // latched: req_pose.dir at last 20Hz send
	int dbg_actor_sprite_family_kind;     // per-frame: DebugSpriteFamilyKind(player.sprite)
	uint32_t dbg_actor_resolved_presentation_key; // per-frame: composed presentation cache key used for player.sprite (0 if legacy/missing)
	uint16_t dbg_actor_render_presentation_kind_id; // per-frame: V2 actor presentation_kind_id actually rendered
	uint16_t dbg_actor_render_skin_definition_id; // per-frame: V2 actor skin_definition_id actually rendered
	uint32_t dbg_actor_render_loadout_signature; // per-frame: V2 actor resolved ordered slot/layer signature
	uint16_t dbg_actor_render_profile_id_hash; // per-frame: compiled ActorVisualProfile id hash for the selected exact profile
	uint16_t dbg_actor_render_atlas_frame_index; // per-frame: selected composed atlas frame used for contribution telemetry
	int dbg_actor_render_contribution_angle; // per-frame: selected sprite angle used for contribution telemetry
	uint8_t dbg_actor_render_contribution_projection; // per-frame: selected projection used for contribution telemetry
	uint8_t dbg_actor_render_contribution_scope; // per-frame: 2 means selected runtime frame, not cache-wide aggregate
	uint8_t dbg_actor_server_visual_key_valid; // per-frame: exact RenderPlan key built from AppearanceStateV2
	uint32_t dbg_actor_server_visual_key_hash; // per-frame: hash of attempted ServerVisualKey
	uint8_t dbg_actor_render_plan_found; // per-frame: exact ServerVisualKey matched a compiled RenderPlan
	uint32_t dbg_actor_render_plan_key_hash; // per-frame: hash of matched RenderPlan key
	uint8_t dbg_actor_render_plan_layer_count; // per-frame: compiled RenderPlan layer count before compose
	uint16_t dbg_actor_asset_load_failure_count; // per-frame: layer-definition or layer-sprite lookup failures during compose
	uint16_t dbg_actor_render_body_layer_definition_id; // per-frame: literal V2 actor composed body row; not an effective-body or mounted-owner oracle
	uint16_t dbg_actor_render_head_layer_definition_id; // per-frame: V2 actor head layer_definition_id (0 if none)
	uint8_t dbg_actor_render_compose_mode; // per-frame: mounted seam owner path used on this frame (0 when unmounted / unresolved)
	uint8_t dbg_actor_render_ref_alignment_observed; // per-frame: historical guardrail; should stay 0 unless someone wrongly revives mounted ref[2] re-anchoring
	uint16_t dbg_actor_render_ref_alignment_overlay_layer_definition_id; // per-frame: overlay layer that would have required the deleted mounted ref-alignment bypass
	uint16_t dbg_actor_render_ref_alignment_overlay_slot_kind_id; // per-frame: slot_kind_id for the overlay that would have required the deleted mounted ref-alignment bypass
	int dbg_actor_render_ref_alignment_base_ref_z; // per-frame: base ref[2] captured by the deleted mounted ref-alignment guardrail
	int dbg_actor_render_ref_alignment_overlay_ref_z; // per-frame: overlay ref[2] captured by the deleted mounted ref-alignment guardrail
	uint8_t dbg_actor_render_compose_failure_stage; // per-frame: mounted compose failure stage (0 when compose succeeded / not attempted)
	uint16_t dbg_actor_render_compose_failure_base_layer_definition_id; // per-frame: base layer active when compose failed
	uint16_t dbg_actor_render_compose_failure_overlay_layer_definition_id; // per-frame: overlay layer that failed to compose/load
	uint16_t dbg_actor_render_compose_failure_overlay_slot_kind_id; // per-frame: slot_kind_id for the failing overlay layer
	int dbg_actor_render_compose_failure_base_ref_z; // per-frame: base frame ref[2] captured at compose failure
	int dbg_actor_render_compose_failure_overlay_ref_z; // per-frame: overlay frame ref[2] captured at compose failure
	uint8_t dbg_actor_render_fail_visible_fallback_used; // per-frame: reserved old fallback flag; must remain 0 after FL-2345 delete-first cutover
	uint8_t dbg_actor_render_original_compose_failure_stage; // per-frame: reserved old fallback failure-stage mirror; must remain 0 after FL-2345 cutover
	uint8_t dbg_actor_render_layer_count; // per-frame: V2 actor ordered render layer count
	uint16_t dbg_actor_render_slot_kind_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 actor ordered slot_kind_id list
	uint16_t dbg_actor_render_layer_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 actor ordered layer_definition_id list
	uint16_t dbg_actor_render_item_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 actor ordered item_definition_id list for item-owned slots
	uint16_t dbg_actor_render_visual_style_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 actor ordered visual_style_id list
	uint16_t dbg_actor_render_layer_semantic_contribution_set_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint8_t dbg_actor_render_layer_source_layer_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_actor_render_layer_source_path_hashes[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_actor_render_layer_visible_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_actor_render_layer_contributed_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t dbg_actor_render_layer_occluded_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint8_t dbg_actor_bundle_load_status; // per-frame: 0=not-attempted,1=loaded,2=read_failed,3=parse_failed
	uint8_t dbg_actor_bundle_selector_found; // compatibility mirror: 1 only when exact RenderPlan was found
	uint8_t dbg_actor_bundle_selector_failure_reason; // compatibility mirror: nonzero exact-plan failure reason
	uint8_t dbg_actor_bundle_selector_count; // compatibility mirror: selector tables are deleted, remains 0
	uint8_t dbg_actor_bundle_layer_count; // per-frame: parsed actor layer rows in bundle cache
	uint8_t dbg_actor_profile_load_status; // per-frame: compiled ActorVisualProfile table available
	uint8_t dbg_actor_selector_found; // per-frame: exact ActorVisualProfile key matched
	uint8_t dbg_actor_selector_failure_reason; // per-frame: ActorVisualProfile failure reason
	uint8_t dbg_actor_profile_selector_count; // per-frame: compiled ActorVisualProfile table row count
	uint8_t dbg_actor_profile_layer_count; // per-frame: matched ActorVisualProfile layer count
	uint32_t dbg_actor_profile_cache_eviction_count; // global composed profile cache eviction counter
	uint32_t dbg_actor_profile_cache_evicted_neutral_count; // global neutral composed profile cache evictions
	uint32_t dbg_actor_profile_cache_evicted_enemy_count; // global enemy composed profile cache evictions
	uint32_t dbg_actor_profile_cache_hit_count; // global composed profile cache hits
	uint32_t dbg_actor_profile_cache_null_hit_count; // global composed profile cache hits returning null sprite
	uint32_t dbg_actor_profile_cache_miss_count; // global composed profile cache misses that trigger full compose
	uint32_t dbg_actor_profile_cache_full_count; // global composed profile cache reserve failures
	uint32_t dbg_actor_profile_cache_failure_count; // global composed profile failures after a miss
	uint32_t dbg_actor_profile_row_lookup_cache_hit_count; // global exact-key row lookup cache hits
	uint32_t dbg_actor_profile_row_lookup_cache_null_hit_count; // global exact-key row lookup hits for cached misses
	uint32_t dbg_actor_profile_row_lookup_cache_miss_count; // global exact-key row lookup cache misses
	uint32_t dbg_actor_profile_row_lookup_table_scan_count; // global generated-row table scans after lookup misses
	uint32_t dbg_actor_visual_resolve_us; // per-frame total time spent resolving ActorVisualProfile rows
	uint32_t dbg_actor_visual_compose_us; // per-frame time spent composing ActorVisualProfile sprites after exact row lookup
	uint32_t dbg_actor_visual_runtime_frame_telemetry_us;
	uint32_t dbg_actor_bundle_cache_eviction_count; // global cache eviction counter copied into recorder for mounted cache churn audits
	uint32_t dbg_actor_bundle_cache_evicted_neutral_count; // global cache neutral-sprite frees from eviction
	uint32_t dbg_actor_bundle_cache_evicted_enemy_count; // global cache enemy-sprite frees from eviction
	int dbg_actor_inst_sprite_family_kind; // per-frame: DebugSpriteFamilyKind(GetInstSprite(player_inst))
	int dbg_actor_inst_sprite_matches_owner; // per-frame: player_inst sprite pointer matches player.sprite
	int dbg_actor_render_sprite_row_seen; // per-frame: renderer selected a local-player sprite row
	int dbg_actor_render_sprite_angle; // per-frame: renderer-selected local-player sprite angle row
	int dbg_actor_render_sprite_angles; // per-frame: angle count for the selected local-player sprite
	int dbg_actor_render_sprite_anim; // per-frame: renderer-selected local-player anim
	int dbg_actor_render_sprite_frame; // per-frame: renderer-selected local-player frame
	int dbg_actor_authoritative_tick; // per-frame: tick/wall-clock source used for local frame selection
	int dbg_actor_presentation_started_tick; // per-frame: local presentation start tick used for frame selection
	int dbg_actor_playback_elapsed_ticks; // per-frame: authoritative_tick - presentation_started_tick when applicable
	int dbg_actor_frame_clamped; // per-frame: selected local attack/death frame is terminal-clamped
	int dbg_actor_frame_changed_expected; // per-frame: selected local attack/death frame should still be advancing
	// FL-4079: row-owned playback metadata mirrored here so the wearable proof
	// seam can join server presentation_kind_id + locomotion_state with the
	// renderer's actual frame-selection inputs without reading the compiled
	// profile from outside the render pass.
	// TELEMETRY-ONLY: do not read for gameplay decisions.
	uint8_t dbg_actor_render_playback_mode;
	uint16_t dbg_actor_render_steady_frame_index;
	uint8_t dbg_actor_render_selected_locomotion_anim_track;
	// FL-4079: monotonic frame-identity stamp, snapped at the same point debug
	// fields are populated so the wearable proof probe reads all fields from
	// one consistent source (game->debug) instead of splitting between
	// diagnostics globals and debug state.
	// TELEMETRY-ONLY: do not read for gameplay decisions.
	uint32_t dbg_actor_render_probe_seq;
	// FL-4079: local-actor body anchor + clip + frame dims captured at the
	// body-blit site (Renderer::RenderSprite -> SpriteBlitDiagnostics). Used by
	// the wearable proof probe to (a) determine the ROI to read from
	// render_buf and (b) project authored armor cells onto the screen using
	// the same dx/dy math the renderer used.
	int dbg_actor_render_body_screen_pos_x;
	int dbg_actor_render_body_screen_pos_y;
	int dbg_actor_render_body_ref_x;
	int dbg_actor_render_body_ref_y;
	int dbg_actor_render_body_frame_width;
	int dbg_actor_render_body_frame_height;
	int dbg_actor_render_body_clip_left;
	int dbg_actor_render_body_clip_right;
	int dbg_actor_render_body_clip_bottom;
	int dbg_actor_render_body_clip_top;
	// FL-4079: per-layer source_xp_index carried as compiled-profile
	// provenance for the upstream-pipeline baseline oracle.
	uint16_t dbg_actor_render_layer_source_xp_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	int dbg_actor_step_phase; // [DEBUG-walk-stuck] per-frame: io.player_stp passed into wall-clock walk-frame derivation
	int dbg_actor_step_phase_div_1024; // [DEBUG-walk-stuck] per-frame: step_phase / 1024 quotient used as walk frame index pre-mod
	float dbg_remote0_render_dir;         // per-frame: rp->dir used for UpdateSpriteInst (observer)
		int dbg_remote0_life_state;           // per-frame: rp->life_state
		int dbg_remote0_mount_state;          // per-frame: rp->mount_state
		int dbg_remote0_mount_layer_count;    // per-frame: count of mount-role layers resolved from RenderPlan
		int dbg_remote0_locomotion_state;     // per-frame: rp->locomotion_state
		int dbg_remote0_combat_state;         // per-frame: rp->combat_state
		int dbg_remote0_anim;                 // per-frame: rp->anim selected by ResolveCharacterPresentation
		int dbg_remote0_frame;                // per-frame: rp->frame selected by ResolveCharacterPresentation
		int dbg_remote0_anim_length;          // per-frame: selected animation length
		int dbg_remote0_frame_clamped;        // per-frame: selected frame is clamped at attack/death terminal frame
		int dbg_remote0_frame_changed_expected; // per-frame: selected attack/death frame should still be advancing
		int dbg_remote0_authoritative_tick;   // per-frame: authoritative render tick used for frame selection
		int dbg_remote0_presentation_started_tick; // per-frame: snapshot presentation start tick used for frame selection
		int dbg_remote0_playback_elapsed_ticks; // per-frame: authoritative_tick - presentation_started_tick
		int dbg_remote0_death_tick;           // per-frame: presentation_started_tick when remote is dead, else 0
		int dbg_remote0_corpse_hold_age_ticks; // per-frame: authoritative ticks since death presentation started
		int dbg_remote0_render_diverged_from_snapshot; // per-frame: render kind differs from latest snapshot kind
		int dbg_remote0_screen_center_glyph;  // per-frame: CP437 glyph at sprite center cell (angle 0)
		int dbg_remote0_render_sprite_family_kind; // per-frame: DebugSpriteFamilyKind(rp->sprite)
		uint32_t dbg_remote0_resolved_presentation_key; // per-frame: composed presentation cache key used for rp->sprite (0 if legacy/missing)
		uint16_t dbg_remote0_render_presentation_kind_id; // per-frame: V2 remote presentation_kind_id actually rendered
		uint16_t dbg_remote0_render_skin_definition_id; // per-frame: V2 remote skin_definition_id actually rendered
		uint32_t dbg_remote0_render_loadout_signature; // per-frame: V2 remote resolved ordered slot/layer signature
		uint16_t dbg_remote0_render_profile_id_hash; // per-frame: compiled ActorVisualProfile id hash for the selected exact profile
		uint16_t dbg_remote0_render_atlas_frame_index; // per-frame: selected composed atlas frame used for contribution telemetry
		int dbg_remote0_render_contribution_angle; // per-frame: selected sprite angle used for contribution telemetry
		uint8_t dbg_remote0_render_contribution_projection; // per-frame: selected projection used for contribution telemetry
		uint8_t dbg_remote0_render_contribution_scope; // per-frame: 2 means selected runtime frame, not cache-wide aggregate
		uint8_t dbg_remote0_server_visual_key_valid; // per-frame: exact RenderPlan key built from AppearanceStateV2
		uint32_t dbg_remote0_server_visual_key_hash; // per-frame: hash of attempted ServerVisualKey
		uint8_t dbg_remote0_render_plan_found; // per-frame: exact ServerVisualKey matched a compiled RenderPlan
		uint32_t dbg_remote0_render_plan_key_hash; // per-frame: hash of matched RenderPlan key
		uint8_t dbg_remote0_render_plan_layer_count; // per-frame: compiled RenderPlan layer count before compose
		uint16_t dbg_remote0_asset_load_failure_count; // per-frame: layer-definition or layer-sprite lookup failures during compose
		uint16_t dbg_remote0_render_body_layer_definition_id; // per-frame: literal V2 remote composed body row; not an effective-body or mounted-owner oracle
		uint16_t dbg_remote0_render_head_layer_definition_id; // per-frame: V2 remote head layer_definition_id (0 if none)
		uint8_t dbg_remote0_render_compose_mode; // per-frame: mounted seam owner path used on this frame (0 when unmounted / unresolved)
		uint8_t dbg_remote0_render_ref_alignment_observed; // per-frame: historical guardrail; should stay 0 unless someone wrongly revives mounted ref[2] re-anchoring
		uint16_t dbg_remote0_render_ref_alignment_overlay_layer_definition_id; // per-frame: overlay layer that would have required the deleted mounted ref-alignment bypass
		uint16_t dbg_remote0_render_ref_alignment_overlay_slot_kind_id; // per-frame: slot_kind_id for the overlay that would have required the deleted mounted ref-alignment bypass
		int dbg_remote0_render_ref_alignment_base_ref_z; // per-frame: base ref[2] captured by the deleted mounted ref-alignment guardrail
		int dbg_remote0_render_ref_alignment_overlay_ref_z; // per-frame: overlay ref[2] captured by the deleted mounted ref-alignment guardrail
		uint8_t dbg_remote0_render_compose_failure_stage; // per-frame: mounted compose failure stage (0 when compose succeeded / not attempted)
		uint16_t dbg_remote0_render_compose_failure_base_layer_definition_id; // per-frame: base layer active when compose failed
		uint16_t dbg_remote0_render_compose_failure_overlay_layer_definition_id; // per-frame: overlay layer that failed to compose/load
		uint16_t dbg_remote0_render_compose_failure_overlay_slot_kind_id; // per-frame: slot_kind_id for the failing overlay layer
		int dbg_remote0_render_compose_failure_base_ref_z; // per-frame: base frame ref[2] captured at compose failure
		int dbg_remote0_render_compose_failure_overlay_ref_z; // per-frame: overlay frame ref[2] captured at compose failure
		uint8_t dbg_remote0_render_fail_visible_fallback_used; // per-frame: reserved old fallback flag; must remain 0 after FL-2345 delete-first cutover
		uint8_t dbg_remote0_render_original_compose_failure_stage; // per-frame: reserved old fallback failure-stage mirror; must remain 0 after FL-2345 cutover
		uint8_t dbg_remote0_render_layer_count; // per-frame: V2 remote ordered render layer count
		uint16_t dbg_remote0_render_slot_kind_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 remote ordered slot_kind_id list
		uint16_t dbg_remote0_render_layer_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 remote ordered layer_definition_id list
		uint16_t dbg_remote0_render_item_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 remote ordered item_definition_id list for item-owned slots
		uint16_t dbg_remote0_render_visual_style_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS]; // per-frame: V2 remote ordered visual_style_id list
		uint16_t dbg_remote0_render_layer_semantic_contribution_set_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
		uint8_t dbg_remote0_render_layer_source_layer_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
		uint16_t dbg_remote0_render_layer_source_path_hashes[ACTOR_VISUAL_MAX_RENDER_LAYERS];
		uint16_t dbg_remote0_render_layer_visible_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
		uint16_t dbg_remote0_render_layer_contributed_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
		uint16_t dbg_remote0_render_layer_occluded_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
		uint8_t dbg_remote0_bundle_load_status; // per-frame: 0=not-attempted,1=loaded,2=read_failed,3=parse_failed
		uint8_t dbg_remote0_bundle_selector_found; // compatibility mirror: 1 only when exact RenderPlan was found
			uint8_t dbg_remote0_bundle_selector_failure_reason; // compatibility mirror: nonzero exact-plan failure reason
			uint8_t dbg_remote0_bundle_selector_count; // compatibility mirror: selector tables are deleted, remains 0
			uint8_t dbg_remote0_bundle_layer_count; // per-frame: parsed actor layer rows in bundle cache
			uint8_t dbg_remote0_profile_load_status; // per-frame: compiled ActorVisualProfile table available
			uint8_t dbg_remote0_profile_selector_found; // per-frame: exact ActorVisualProfile key matched
			uint8_t dbg_remote0_profile_selector_failure_reason; // per-frame: ActorVisualProfile failure reason
			uint8_t dbg_remote0_profile_selector_count; // per-frame: compiled ActorVisualProfile table row count
			uint8_t dbg_remote0_profile_layer_count; // per-frame: matched ActorVisualProfile layer count
			int dbg_remote0_inst_sprite_family_kind; // per-frame: DebugSpriteFamilyKind(GetInstSprite(rp->inst))
		int dbg_remote0_inst_sprite_matches_owner; // per-frame: rp->inst sprite pointer matches rp->sprite
	int dbg_sent_pose_dir_change_events;  // monotonic: sent dir differs from prev by >5 degrees
		float dbg_sent_pose_dir_prev;         // latched: previous sent dir for change detection
		float dbg_remote0_raw_wire_dir;       // raw float from ptr+16 memcpy (bypasses struct cast)
		float dbg_remote0_wire_dir_min;       // min dir seen across all 'p' packets
		float dbg_remote0_wire_dir_max;       // max dir seen across all 'p' packets
		int dbg_remote0_wire_dir_varied;      // count of 'p' packets where dir != 45 (>5 deg)
		int dbg_remote0_wire_pose_count;      // monotonic: 'p' packets that reached case handler
		int dbg_remote0_snapshot_life_state;  // last tracked remote life_state received from snapshot
		int dbg_remote0_snapshot_mount_state; // last tracked remote mount_state received from snapshot
		int dbg_remote0_snapshot_locomotion_state; // last tracked remote locomotion_state received from snapshot
		// FL-3254: combat_state is required by the mounted attack/death watchdog gate
		// (scenario_mounted_attack_death_observed). Without it, the gate always fails
		// because mounted attack evidence was not selected in the current proof window.
		int dbg_remote0_snapshot_combat_state;
		// FL-2957: terrain_z from snapshot wire packet — floor coherence proof
		float dbg_remote0_snapshot_terrain_z;
		int dbg_remote0_snapshot_presentation_kind_id; // last tracked remote presentation_kind_id received from snapshot
		uint32_t dbg_remote0_snapshot_tick;   // last snapshot packet tick carrying tracked remote
		uint32_t dbg_remote0_entity_tick;     // last_authoritative_tick from tracked remote snapshot
		int dbg_proc_opcode_p;                // count of 'p' opcodes seen by Proc
		int dbg_proc_opcode_b;                // count of 'b' opcodes seen by Proc
		int dbg_proc_opcode_q;                // count of 'q' opcodes seen by Proc
	int dbg_proc_opcode_other;            // count of other opcodes seen by Proc
	// Local input/movement diagnostics for FL-036 jitter tracing.
	int dbg_input_w_down;
	int dbg_input_a_down;
	int dbg_input_s_down;
	int dbg_input_d_down;
	float dbg_io_x_force;
	float dbg_io_y_force;
	float dbg_io_z_force;
	float dbg_io_torque;
	float dbg_io_x_force_applied;
	float dbg_io_y_force_applied;
	float dbg_io_z_force_applied;
	float dbg_input_world_dir;
	float dbg_last_input_world_dir;
	int dbg_io_jump_requested;
	int dbg_last_input_jump_requested;
	int dbg_io_fly_applied;
	int dbg_player_action_value;
	int dbg_local_became_airborne;
	bool entered_world_logged;
	int dbg_last_local_became_airborne;
	int dbg_reconcile_applied;
	int dbg_reconcile_hard_snap;
	int dbg_reconcile_zeroed_xy;
	float dbg_reconcile_dx;
	float dbg_reconcile_dy;
	float dbg_reconcile_dz;
	uint32_t dbg_reconcile_tick;
	float dbg_reconcile_auth_dist_pre;     // distance to auth target before correction
	float dbg_reconcile_auth_dist_post;    // distance to auth target after correction
	int dbg_local_vel_dir_change_no_reconcile; // velocity heading reversed without reconcile
	float dbg_local_prev_vel_x;            // previous frame velocity (for direction change)
	float dbg_local_prev_vel_y;
	float dbg_local_pre_anim_pos_x;
	float dbg_local_pre_anim_pos_y;
	float dbg_local_pre_anim_pos_z;
	float dbg_local_anim_dx;
	float dbg_local_anim_dy;
	float dbg_local_anim_dz;
	float dbg_last_local_pre_anim_pos_x;
	float dbg_last_local_pre_anim_pos_y;
	float dbg_last_local_pre_anim_pos_z;
	float dbg_last_local_anim_dx;
	float dbg_last_local_anim_dy;
	float dbg_last_local_anim_dz;
	float dbg_local_vel_x;
	float dbg_local_vel_y;
	float dbg_local_vel_z;
	float dbg_local_pos_x;
	float dbg_local_pos_y;
	float dbg_local_pos_z;
	// FL-4137 mobile-proof helper: local player projected to screen cell space
	// each frame via the same ProjectCoords the world-item appearance pass uses.
	// The mobile double-tap proof needs to tap the player's actual screen rect,
	// not canvas center, because the player can be off-center after walking.
	// dbg_local_screen_valid=0 means projection failed (off-screen).
	int16_t dbg_local_screen_col;
	int16_t dbg_local_screen_row;
	uint8_t dbg_local_screen_valid;
	float dbg_local_visual_pos_x;
	float dbg_local_visual_pos_y;
	float dbg_local_visual_pos_z;
	float dbg_last_local_vel_x;
	float dbg_last_local_vel_y;
	float dbg_last_local_vel_z;
	float dbg_last_local_pos_x;
	float dbg_last_local_pos_y;
	float dbg_last_local_pos_z;
	float dbg_last_local_visual_pos_x;
	float dbg_last_local_visual_pos_y;
	float dbg_last_local_visual_pos_z;
	int16_t dbg_self_hp; // server-authoritative HP from local player's snapshot entity
	int16_t dbg_self_max_hp; // server-authoritative max HP from local player's snapshot entity
	uint32_t dbg_local_last_acked_input_seq;
	float dbg_local_snapshot_age_ms;
	uint32_t dbg_local_render_medium_snap_count;
	uint32_t dbg_local_render_hard_snap_count;
	int dbg_local_grounded;
	int dbg_last_local_grounded;
	uint64_t verifier_teleport_stamp; // legacy verifier teleport marker; mutating setter disabled by FL-1148
	int dbg_focus_loss_count;
	int dbg_focus_gain_count;
	int dbg_main_menu_active;
	int dbg_show_inventory_active;
	int dbg_talk_box_active;
	int dbg_menu_depth_value;
	enum { DBG_INPUT_EVENT_RING = 16 };
	struct DebugInputEvent
	{
		uint32_t seq;
		int kind;
		int key;
		int auto_repeat;
		uint32_t dt_ms;
		int main_menu_active;
		int show_inventory_active;
		int talk_box_active;
		int menu_depth_value;
	};
	uint32_t dbg_input_event_seq;
	uint64_t dbg_input_event_last_stamp;
	DebugInputEvent dbg_input_event[DBG_INPUT_EVENT_RING];

	// FPS timing ring buffer (moved from Game)
	static const int fps_window_size = 100;
	int fps_window_pos;
	uint64_t fps_window[fps_window_size];
};
