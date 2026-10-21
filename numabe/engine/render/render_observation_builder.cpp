#include "render_observation_builder.h"

#include "render_internal.h"

TrackedRemoteClampReport BuildTrackedRemoteClampReport(
    const TrackedRemoteClampInputs& in)
{
    TrackedRemoteClampReport out = {};
    out.blit_clamp_eligible = 1;
    out.blit_pre_clamp_s_pos_z = (float)in.sprite_s_pos_z;
    out.blit_post_clamp_s_pos_z = (float)in.sprite_s_pos_z;
    out.blit_clamp_center_in_bounds = in.center_in_bounds ? 1 : 0;
    for (int i = 0; i < 4; i++)
    {
        out.blit_center_sample_height[i] = in.center_samples[i].height;
        out.blit_center_sample_spare[i] = in.center_samples[i].spare;
        out.blit_center_sample_valid[i] = in.center_samples[i].valid ? 1 : 0;
        if (!in.center_samples[i].valid)
            continue;
        out.blit_clamp_support_samples++;
        if (!out.blit_clamp_support_found ||
            in.center_samples[i].height > out.blit_clamp_support_height)
        {
            out.blit_clamp_support_found = 1;
            out.blit_clamp_support_height = in.center_samples[i].height;
        }
    }

    if (out.blit_clamp_support_found)
    {
        out.blit_clamp_floor_z =
            (float)((int)floorf(out.blit_clamp_support_height + 0.5f) +
                HEIGHT_SCALE / 2);
    }

    return out;
}

void FillTrackedRemoteBlitReport(
    const SpriteRenderBuf* buf,
    const SpriteBlitDiagnostics& diag,
    TrackedRemoteBlitReport* out)
{
    if (!buf || !out)
        return;

    const int body_total = diag.depth_pass_cells + diag.depth_fail_cells;
    if (diag.drew_any)
    {
        out->tracked_buf_drew_any = 1;
        out->final_body_drawn = 1;
    }
    out->depth_pass_cells = diag.depth_pass_cells;
    out->depth_fail_cells = diag.depth_fail_cells;
    out->body_total_cells = body_total;
    out->body_visible_cells = diag.depth_pass_cells;
    out->body_occluded_cells = diag.depth_fail_cells;
    out->body_visible_fraction =
        body_total > 0 ? (float)diag.depth_pass_cells / (float)body_total : 0.0f;
    if (diag.has_center_terrain_height)
        out->blit_terrain_height_center = diag.center_terrain_height;
}

void FillTrackedNpcRenderReport(
    const SpriteRenderBuf* buf,
    const SpriteBlitDiagnostics& diag,
    TrackedNpcRenderReport* out)
{
    if (!buf || !out)
        return;

    out->body_blit_pos_z = diag.blit_pos_z;
    out->body_water_plane_z = diag.water_plane_z;
    if (diag.drew_any)
        out->body_drew_any = 1;
    out->body_candidate_cells = diag.candidate_cells;
    out->body_depth_pass_cells = diag.depth_pass_cells;
    out->body_depth_fail_cells = diag.depth_fail_cells;
    out->body_depth_fail_mesh_cells = diag.depth_fail_mesh_cells;
    out->body_depth_fail_mesh_samples = diag.depth_fail_mesh_samples;
    out->body_water_reject_cells = diag.water_reject_cells;
    out->body_candidate_min_z =
        diag.candidate_cells > 0 ? diag.candidate_min_z : 0.0f;
    out->body_candidate_max_z =
        diag.candidate_cells > 0 ? diag.candidate_max_z : 0.0f;
    if (!diag.drew_any)
    {
        out->body_reject_reason = diag.reject_reason;
        if (diag.clip_reject)
            out->body_clip_reject = 1;
    }
    out->body_clip_left = diag.clip_left;
    out->body_clip_right = diag.clip_right;
    out->body_clip_bottom = diag.clip_bottom;
    out->body_clip_top = diag.clip_top;
    out->body_unclipped_left = diag.unclipped_left;
    out->body_unclipped_right = diag.unclipped_right;
    out->body_unclipped_bottom = diag.unclipped_bottom;
    out->body_unclipped_top = diag.unclipped_top;
    out->body_frame_width = diag.frame_width;
    out->body_frame_height = diag.frame_height;
    out->body_ref_x = diag.ref_x;
    out->body_ref_y = diag.ref_y;
    out->body_screen_pos_x = diag.screen_pos_x;
    out->body_screen_pos_y = diag.screen_pos_y;
}
