// engine/debug_telemetry_state.cpp — RenderFrameReport telemetry ingest

#include "debug_telemetry_state.h"

void DebugTelemetryState::ApplyRenderFrameReport(const RenderFrameReport& report)
{
	dbg_remote0_query_seen = report.queue.query_seen;
	dbg_remote0_inst_cookie_match = report.queue.inst_cookie_match;
	dbg_remote0_queue_enqueued = report.queue.queue_enqueued;
	dbg_remote0_queue_skip_reason = report.queue.queue_skip_reason;

	dbg_remote0_tracked_buf_drew_any = report.remote_blit.tracked_buf_drew_any;
	dbg_remote0_depth_pass_cells = report.remote_blit.depth_pass_cells;
	dbg_remote0_depth_fail_cells = report.remote_blit.depth_fail_cells;
	dbg_remote0_body_total_cells = report.remote_blit.body_total_cells;
	dbg_remote0_body_visible_cells = report.remote_blit.body_visible_cells;
	dbg_remote0_body_occluded_cells = report.remote_blit.body_occluded_cells;
	dbg_remote0_body_visible_fraction = report.remote_blit.body_visible_fraction;
	dbg_remote0_blit_terrain_height_center = report.remote_blit.blit_terrain_height_center;

	dbg_remote0_blit_clamp_eligible = report.remote_clamp.blit_clamp_eligible;
	dbg_remote0_blit_clamp_center_in_bounds = report.remote_clamp.blit_clamp_center_in_bounds;
	dbg_remote0_blit_clamp_support_found = report.remote_clamp.blit_clamp_support_found;
	dbg_remote0_blit_clamp_support_samples = report.remote_clamp.blit_clamp_support_samples;
	dbg_remote0_blit_clamp_applied = report.remote_clamp.blit_clamp_applied;
	dbg_remote0_blit_pre_clamp_s_pos_z = report.remote_clamp.blit_pre_clamp_s_pos_z;
	dbg_remote0_blit_post_clamp_s_pos_z = report.remote_clamp.blit_post_clamp_s_pos_z;
	dbg_remote0_blit_s_pos_z = report.remote_clamp.blit_s_pos_z;
	dbg_remote0_blit_clamp_support_height = report.remote_clamp.blit_clamp_support_height;
	dbg_remote0_blit_clamp_floor_z = report.remote_clamp.blit_clamp_floor_z;
	dbg_remote0_blit_post_clamp_rewrite = report.remote_clamp.blit_post_clamp_rewrite;
	for (int i = 0; i < 4; i++)
	{
		dbg_remote0_blit_center_sample_height[i] = report.remote_clamp.blit_center_sample_height[i];
		dbg_remote0_blit_center_sample_spare[i] = report.remote_clamp.blit_center_sample_spare[i];
		dbg_remote0_blit_center_sample_valid[i] = report.remote_clamp.blit_center_sample_valid[i];
	}

	dbg_remote0_last_remote_blit_pid = report.remote_blit.last_remote_blit_pid;
	dbg_remote0_last_remote_blit_matches_head = report.remote_blit.last_remote_blit_matches_head;
	dbg_remote0_final_blit_attempted = report.remote_blit.final_blit_attempted;
	dbg_remote0_tracked_buf_blit_invoked = report.remote_blit.tracked_buf_blit_invoked;
	dbg_remote0_final_body_drawn = report.remote_blit.final_body_drawn;

	dbg_tracked_npc0_hp_bar_expected = report.tracked_npc.hp_bar_expected;
	dbg_tracked_npc0_hp_bar_drawn = report.tracked_npc.hp_bar_drawn;
	dbg_tracked_npc0_body_blit_attempted = report.tracked_npc.body_blit_attempted;
	dbg_tracked_npc0_body_drew_any = report.tracked_npc.body_drew_any;
	dbg_tracked_npc0_body_blit_pos_z = report.tracked_npc.body_blit_pos_z;
	dbg_tracked_npc0_body_water_plane_z = report.tracked_npc.body_water_plane_z;
	dbg_tracked_npc0_body_candidate_cells = report.tracked_npc.body_candidate_cells;
	dbg_tracked_npc0_body_depth_pass_cells = report.tracked_npc.body_depth_pass_cells;
	dbg_tracked_npc0_body_depth_fail_cells = report.tracked_npc.body_depth_fail_cells;
	dbg_tracked_npc0_body_depth_fail_mesh_cells = report.tracked_npc.body_depth_fail_mesh_cells;
	dbg_tracked_npc0_body_depth_fail_mesh_samples = report.tracked_npc.body_depth_fail_mesh_samples;
	dbg_tracked_npc0_body_water_reject_cells = report.tracked_npc.body_water_reject_cells;
	dbg_tracked_npc0_body_candidate_min_z = report.tracked_npc.body_candidate_min_z;
	dbg_tracked_npc0_body_candidate_max_z = report.tracked_npc.body_candidate_max_z;
	dbg_tracked_npc0_body_reject_reason = report.tracked_npc.body_reject_reason;
	dbg_tracked_npc0_body_clip_reject = report.tracked_npc.body_clip_reject;
	dbg_tracked_npc0_body_clip_left = report.tracked_npc.body_clip_left;
	dbg_tracked_npc0_body_clip_right = report.tracked_npc.body_clip_right;
	dbg_tracked_npc0_body_clip_bottom = report.tracked_npc.body_clip_bottom;
	dbg_tracked_npc0_body_clip_top = report.tracked_npc.body_clip_top;
	dbg_tracked_npc0_body_unclipped_left = report.tracked_npc.body_unclipped_left;
	dbg_tracked_npc0_body_unclipped_right = report.tracked_npc.body_unclipped_right;
	dbg_tracked_npc0_body_unclipped_bottom = report.tracked_npc.body_unclipped_bottom;
	dbg_tracked_npc0_body_unclipped_top = report.tracked_npc.body_unclipped_top;
	dbg_tracked_npc0_body_frame_width = report.tracked_npc.body_frame_width;
	dbg_tracked_npc0_body_frame_height = report.tracked_npc.body_frame_height;
	dbg_tracked_npc0_body_ref_x = report.tracked_npc.body_ref_x;
	dbg_tracked_npc0_body_ref_y = report.tracked_npc.body_ref_y;
	dbg_tracked_npc0_body_screen_pos_x = report.tracked_npc.body_screen_pos_x;
	dbg_tracked_npc0_body_screen_pos_y = report.tracked_npc.body_screen_pos_y;

	dbg_actor_final_body_drawn = report.actor.actor_final_body_drawn;
	dbg_actor_render_sprite_row_seen = report.actor.sprite_row_seen;
	dbg_actor_render_sprite_angle = report.actor.sprite_angle;
	dbg_actor_render_sprite_angles = report.actor.sprite_angles;
	dbg_actor_render_sprite_anim = report.actor.sprite_anim;
	dbg_actor_render_sprite_frame = report.actor.sprite_frame;
	// FL-4079
	dbg_actor_render_body_screen_pos_x = report.actor.body_screen_pos_x;
	dbg_actor_render_body_screen_pos_y = report.actor.body_screen_pos_y;
	dbg_actor_render_body_ref_x = report.actor.body_ref_x;
	dbg_actor_render_body_ref_y = report.actor.body_ref_y;
	dbg_actor_render_body_frame_width = report.actor.body_frame_width;
	dbg_actor_render_body_frame_height = report.actor.body_frame_height;
	dbg_actor_render_body_clip_left = report.actor.body_clip_left;
	dbg_actor_render_body_clip_right = report.actor.body_clip_right;
	dbg_actor_render_body_clip_bottom = report.actor.body_clip_bottom;
	dbg_actor_render_body_clip_top = report.actor.body_clip_top;

	dbg_minimap_marker_visible_count = report.minimap.minimap_marker_visible_count;
	dbg_minimap_marker_right_half_visible_count = report.minimap.minimap_marker_right_half_visible_count;
	dbg_minimap_marker_label_chars_drawn = report.minimap.minimap_marker_label_chars_drawn;
	dbg_minimap_marker_right_half_label_chars_drawn = report.minimap.minimap_marker_right_half_label_chars_drawn;
	dbg_minimap_remote_expected_count = report.minimap.minimap_remote_expected_count;
	dbg_minimap_remote_drawn_count = report.minimap.minimap_remote_drawn_count;
}
