#pragma once

// render_frame_report.h — Render-owned per-frame output report
//
// PURPOSE:
// Defines the render->game bridge contract for FL-2900. Render writes these
// facts during the frame, then game_render_bridge.cpp copies them into
// DebugTelemetryState without derivation.

#include <stdint.h>

struct RenderQueueReport
{
    uint8_t query_seen;
    uint8_t inst_cookie_match;
    uint8_t queue_enqueued;
    uint8_t queue_skip_reason;
};

struct TrackedRemoteClampReport
{
    uint8_t blit_clamp_eligible;
    uint8_t blit_clamp_center_in_bounds;
    uint8_t blit_clamp_support_found;
    int blit_clamp_support_samples;
    uint8_t blit_clamp_applied;

    float blit_pre_clamp_s_pos_z;
    float blit_post_clamp_s_pos_z;
    float blit_s_pos_z;
    float blit_clamp_support_height;
    float blit_clamp_floor_z;
    uint8_t blit_post_clamp_rewrite;

    float blit_center_sample_height[4];
    int blit_center_sample_spare[4];
    uint8_t blit_center_sample_valid[4];
};

struct TrackedRemoteBlitReport
{
    uint8_t tracked_buf_drew_any;
    int depth_pass_cells;
    int depth_fail_cells;
    int body_total_cells;
    int body_visible_cells;
    int body_occluded_cells;
    float body_visible_fraction;
    float blit_terrain_height_center;

    int last_remote_blit_pid;
    uint8_t last_remote_blit_matches_head;
    uint8_t final_blit_attempted;
    uint8_t tracked_buf_blit_invoked;
    uint8_t final_body_drawn;
};

struct TrackedNpcRenderReport
{
    uint8_t hp_bar_expected;
    uint8_t hp_bar_drawn;

    uint8_t body_blit_attempted;
    uint8_t body_drew_any;
    float body_blit_pos_z;
    float body_water_plane_z;

    int body_candidate_cells;
    int body_depth_pass_cells;
    int body_depth_fail_cells;
    int body_depth_fail_mesh_cells;
    int body_depth_fail_mesh_samples;
    int body_water_reject_cells;
    float body_candidate_min_z;
    float body_candidate_max_z;

    int body_reject_reason;
    uint8_t body_clip_reject;
    int body_clip_left;
    int body_clip_right;
    int body_clip_bottom;
    int body_clip_top;
    int body_unclipped_left;
    int body_unclipped_right;
    int body_unclipped_bottom;
    int body_unclipped_top;
    int body_frame_width;
    int body_frame_height;
    int body_ref_x;
    int body_ref_y;
    int body_screen_pos_x;
    int body_screen_pos_y;
};

struct ActorFinalBodyReport
{
    uint8_t actor_final_body_drawn;
    uint8_t sprite_row_seen;
    int sprite_angle;
    int sprite_angles;
    int sprite_anim;
    int sprite_frame;
    // FL-4079: local-actor body anchor + clip + frame dims captured at the
    // body-blit site so the wearable proof probe can project authored armor
    // cells onto the AnsiCell render buffer without re-doing the projection
    // math. Mirrors the tracked-NPC report fields used at debug_telemetry_state.cpp.
    int body_screen_pos_x;
    int body_screen_pos_y;
    int body_ref_x;
    int body_ref_y;
    int body_frame_width;
    int body_frame_height;
    int body_clip_left;
    int body_clip_right;
    int body_clip_bottom;
    int body_clip_top;
};

struct MinimapRenderReport
{
    int minimap_marker_visible_count;
    int minimap_marker_right_half_visible_count;
    int minimap_marker_label_chars_drawn;
    int minimap_marker_right_half_label_chars_drawn;
    int minimap_remote_expected_count;
    int minimap_remote_drawn_count;
};

struct MaterialGlyphRenderReport
{
    int extended_cells_seen;
    int coverage_cells_rendered;
    int diagnostic_cells_rendered;
    uint32_t last_glyph_id;
    uint16_t last_coverage;
    uint8_t last_display_glyph;
};

struct RenderFrameReport
{
    RenderQueueReport queue;
    TrackedRemoteClampReport remote_clamp;
    TrackedRemoteBlitReport remote_blit;
    TrackedNpcRenderReport tracked_npc;
    ActorFinalBodyReport actor;
    MinimapRenderReport minimap;
    MaterialGlyphRenderReport material_glyph;
};
