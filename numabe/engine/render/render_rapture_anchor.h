// render_rapture_anchor.h — fill one RenderFrameInput UFO rapture anchor
//
// PURPOSE:
// The client-side step that turns one dead player's authoritative snapshot
// (presentation_started_tick + a body pose sampled at a known tick) into a
// RenderRapture effect anchor for RenderRaptureBeams / RenderRaptureUfos.
// Factored out of engine/game_render_bridge.cpp unchanged so the offscreen
// render harness (tools/render_harness/scene_rapture.cpp) feeds the renderer
// through exactly the same code the game uses.
//
// RULES:
// - Pure: reads/writes only the RenderFrameInput passed in.
// - The caller decides who is eligible (players only, life_state DEAD).
// - death_ground_z is recovered from the server-lifted body Z with
//   RaptureDeathGroundZ; the lift itself stays server-owned (FL-1434/FL-2023).

#pragma once

#include <stdint.h>

#include "render_frame_input.h"
#include "rapture_timeline.h"

// Returns true when an anchor was appended.
//   presentation_started_tick : the dead player's death tick (server stamp)
//   sample_pos / sample_tick  : an authoritative body pose and its own tick
//   auth_tick                 : latest authoritative tick (render time base)
static inline bool RenderAppendRaptureAnchor(RenderFrameInput* fi,
	uint32_t presentation_started_tick, const float sample_pos[3],
	uint32_t sample_tick, uint32_t auth_tick)
{
	if (!fi || presentation_started_tick == 0)
		return false;
	if (fi->rapture_count >= RENDER_MAX_RAPTURES)
		return false;
	const uint32_t ticks = RaptureTicksSince(auth_tick, presentation_started_tick);
	const RapturePhase phase = RaptureEval(ticks);
	if (!phase.ufo_visible && phase.beam_len01 <= 0.0f)
		return false;
	RenderRapture* rr = &fi->raptures[fi->rapture_count++];
	rr->ground[0] = sample_pos[0];
	rr->ground[1] = sample_pos[1];
	rr->ground[2] = RaptureDeathGroundZ(sample_pos[2],
		RaptureTicksSince(sample_tick, presentation_started_tick));
	rr->ticks_since_death = ticks;
	rr->seed = presentation_started_tick;
	rr->active = 1;
	return true;
}
