#pragma once

// render_observation_builder.h — Pure render report builders
//
// PURPOSE:
// Helpers that build one report section at a time from render-owned inputs.

#include "render_frame_report.h"

struct SpriteRenderBuf;
struct SpriteBlitDiagnostics;

struct ClampProbeSample
{
    float height;
    int spare;
    bool valid;
};

struct TrackedRemoteClampInputs
{
    int sprite_s_pos_z;
    bool center_in_bounds;
    ClampProbeSample center_samples[4];
};

TrackedRemoteClampReport BuildTrackedRemoteClampReport(
    const TrackedRemoteClampInputs& in);

void FillTrackedRemoteBlitReport(
    const SpriteRenderBuf* buf,
    const SpriteBlitDiagnostics& diag,
    TrackedRemoteBlitReport* out);

void FillTrackedNpcRenderReport(
    const SpriteRenderBuf* buf,
    const SpriteBlitDiagnostics& diag,
    TrackedNpcRenderReport* out);
