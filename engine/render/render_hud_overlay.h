#pragma once

// render_hud_overlay.h — HP bar and UI overlay declarations
//
// Extracted from render_scene.cpp.
// SEE ALSO: render_hud_overlay.cpp

struct Renderer;
struct AnsiCell;
struct TrackedNpcRenderReport;

// Draw HP bars above Character-backed and snapshot-NPC sprites.
// Runs as a post-blit overlay pass; consumes SpriteRenderBuf cache
// and writes into out_ptr (AnsiCell output) with SampleBuffer depth tests.
void RenderHpBars(
	Renderer* r,
	AnsiCell* out_ptr,
	int width, int height,
	TrackedNpcRenderReport* tracked_npc_report);
