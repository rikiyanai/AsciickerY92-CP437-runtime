// render_stages.h — Render pipeline stage declarations
//
// PURPOSE:
// Defines the 6-stage rendering pipeline as named stage functions.
// The RenderFrameContext struct carries shared state (view matrix,
// clip planes, light, water plane, timestamps, staging buffers)
// between stages.
//
// PIPELINE (in order):
//   Stage 1 — Clear:      Reset SampleBuffer from cached clean state
//   Stage 2 — Terrain:    Rasterize visible terrain patches
//   Stage 3 — World:      Query sprites + meshes via QueryWorld
//   Stage 4 — Shadow:     Player blob shadow on terrain           [EXTRACTED → render_stage_shadow.cpp]
//   Stage 5 — Reflection: Mirror geometry below water plane
//   Stage 6 — Resolve:    2x2 downsample SampleBuffer → AnsiCell  [EXTRACTED → render_resolve.cpp]
//   Post-Resolve:         Sort + blit deferred sprites, projectiles

#pragma once

#include <stdint.h>

struct Renderer;
struct Terrain;
struct World;
struct AnsiCell;

// ── Shared pipeline context ──
// Carries state between stages, avoiding the need to refactor all
// stage-local variable sharing at once.
struct RenderFrameContext
{
    Renderer* r;
    uint64_t stamp;
    Terrain* terrain;
    World* world;
    float water;
    float yaw;
    const float* player_pos;   // [3]
    const float* light;        // [4]
    int width;
    int height;
    AnsiCell* out_ptr;
    const int* scene_shift;    // [2]
    bool perspective;
};

// ── Stage function signatures ──
// Stage 6 (Resolve) and Stage 4 (Shadow) are extracted; others are still inline in Render().
// void RenderStageClear(RenderFrameContext* ctx);
// void RenderStageTerrain(RenderFrameContext* ctx);
// void RenderStageWorld(RenderFrameContext* ctx);
// void RenderStageReflection(RenderFrameContext* ctx);
void RenderStageShadow(
	Renderer* r,
	int dw, int dh,
	int width,
	const float pos[3],
	const int scene_shift[2],
	struct Material* matlib);
void RenderStageResolve(
	Renderer* r,
	AnsiCell* ptr,
	int width,
	int height,
	struct Material* matlib,
	const double inv_tm[16],
	float water);
