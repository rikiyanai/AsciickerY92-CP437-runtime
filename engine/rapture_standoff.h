// rapture_standoff.h — UFO Rapture Death: keep actors out of the beam (server)
//
// PURPOSE:
// Pure helpers the server NPC AI (SvrUpdateNpcAI) uses while a player is in
// the rapture window (death until respawn, docs/specs/2026-10-08-ufo-rapture-
// death.md §4):
//   - the companion (Gromit) steps back to, and holds, a point just outside
//     the beam's ground footprint on the side he is already on;
//   - hostile NPCs never target a dead / raptured player, and one standing
//     inside the footprint (plus a margin) backs out of it.
// Unit tested by scripts/test_rapture_standoff.cpp (run by
// scripts/test_rapture_timeline.py).
//
// RULES:
// - Pure: no globals, no engine/server includes beyond the pure rapture
//   headers. Movement is expressed as an intent force (same 0.5 magnitude
//   as the existing follow / chase), never a teleport.
// - The beam is a render-only effect sized in screen cells
//   (engine/rapture_beam.h). The footprint radius below converts it to world
//   units with the renderer's fixed projection; see the derivation.

#pragma once

#include <math.h>
#include <stdint.h>

#include "rapture_beam.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ─── Screen cells <-> world units (renderer projection mirror) ──────────
// Render() (engine/render/render_scene.cpp) runs at a fixed zoom:
//   zoom = SPRITE_ZOOM (1) * scale (3, DBL)       = 3
//   ds   = 2 * zoom / VISUAL_CELLS (8)            = 0.75
// and ProjectCoords (render_projection.cpp) maps world XY to samples with
// mul * (xy * HEIGHT_CELLS (4)), two samples per cell (>> 1):
//   screen columns per world unit = ds * HEIGHT_CELLS / 2          = 1.5
//   screen rows per world unit along the ground's screen-depth axis
//                                 = ds * sin30 * HEIGHT_CELLS / 2  = 0.75
// (rotation by the camera yaw preserves these lengths). A static_assert in
// render_scene.cpp keeps these mirrors equal to the real constants.
#define RAPTURE_SCREEN_COLS_PER_WORLD_UNIT 1.5f
#define RAPTURE_SCREEN_ROWS_PER_WORLD_UNIT 0.75f

// Ground footprint radius in world units: the outermost cells the beam
// touches at the ground are the shadow ring, a screen-aligned ellipse of
// RAPTURE_BEAM_RING_HALF_WIDTH (16) columns by RAPTURE_BEAM_RING_HALF_HEIGHT
// (4) rows = 10.67 x 5.33 world units. The camera turns, so the world-space
// footprint is the circle that bounds that ellipse at every yaw:
// max(16 / 1.5, 4 / 0.75) = 10.67 units. (The cone's own ground half-width,
// 11 columns = 7.33 units, lies inside it.)
static inline float RaptureFootprintRadiusWorld()
{
	const float rx = RAPTURE_BEAM_RING_HALF_WIDTH / RAPTURE_SCREEN_COLS_PER_WORLD_UNIT;
	const float ry = RAPTURE_BEAM_RING_HALF_HEIGHT / RAPTURE_SCREEN_ROWS_PER_WORLD_UNIT;
	return rx > ry ? rx : ry;
}

// Companion stand-off: the footprint plus about half a dog sprite, so his
// feet sit just past the darkened ring. Hysteresis on the distance to the
// stand point (start beyond 1.5 units, stop inside 0.5) keeps him from
// twitching at the goal; the state lives in SvrNpcState::follow_active, the
// existing follow hysteresis owner.
#define RAPTURE_COMPANION_STANDOFF_MARGIN 3.0f
#define RAPTURE_COMPANION_STANDOFF_START  1.5f
#define RAPTURE_COMPANION_STANDOFF_STOP   0.5f
// Hostile retreat: the footprint plus a wider margin (an armed NPC sprite is
// wider than the dog, and the death pose must stay clear of it).
#define RAPTURE_NPC_RETREAT_MARGIN        6.0f
// Intent force magnitude: same as the existing companion follow / NPC chase.
#define RAPTURE_STANDOFF_FORCE            0.5f

static inline float RaptureCompanionStandoffRadius()
{
	return RaptureFootprintRadiusWorld() + RAPTURE_COMPANION_STANDOFF_MARGIN;
}

static inline float RaptureNpcRetreatRadius()
{
	return RaptureFootprintRadiusWorld() + RAPTURE_NPC_RETREAT_MARGIN;
}

// May an NPC target / swing at / damage this player? Never while dead or in
// the rapture window (death until respawn). Mirrors the server phase test:
// phase_alive = (phase == CPHASE_ALIVE).
static inline bool RaptureNpcMayTargetPlayer(bool active, bool phase_alive,
	uint32_t death_tick, bool rapture_active)
{
	return active && phase_alive && death_tick == 0 && !rapture_active;
}

struct RaptureStandoffStep
{
	bool move;           // new hysteresis state (true = walking to the goal)
	float goal[2];       // stand point on the circle around the body
	float dist_to_goal;  // XY distance from self to goal
	float force[2];      // intent force (0 when not moving)
};

// Unit XY direction from the body toward self ("the side he is already on").
// Falls back to the companion catch-up offset (-4, -3) when on the axis.
static inline void RaptureStandoffSide(const float body[2], const float self[2], float out[2])
{
	float dx = self[0] - body[0];
	float dy = self[1] - body[1];
	float d = sqrtf(dx * dx + dy * dy);
	if (d < 0.01f)
	{
		dx = -4.0f;
		dy = -3.0f;
		d = 5.0f;
	}
	out[0] = dx / d;
	out[1] = dy / d;
}

// Companion: walk to, and hold, the point `radius` from the body on self's
// side. was_moving is the persistent hysteresis state.
static inline RaptureStandoffStep RaptureCompanionStandoffStep(const float body[2],
	const float self[2], float radius, bool was_moving)
{
	RaptureStandoffStep out = {};
	float side[2];
	RaptureStandoffSide(body, self, side);
	out.goal[0] = body[0] + side[0] * radius;
	out.goal[1] = body[1] + side[1] * radius;
	const float gx = out.goal[0] - self[0];
	const float gy = out.goal[1] - self[1];
	out.dist_to_goal = sqrtf(gx * gx + gy * gy);
	if (out.dist_to_goal > RAPTURE_COMPANION_STANDOFF_START)
		out.move = true;
	else if (out.dist_to_goal < RAPTURE_COMPANION_STANDOFF_STOP)
		out.move = false;
	else
		out.move = was_moving;
	if (out.move && out.dist_to_goal > 0.01f)
	{
		out.force[0] = gx / out.dist_to_goal * RAPTURE_STANDOFF_FORCE;
		out.force[1] = gy / out.dist_to_goal * RAPTURE_STANDOFF_FORCE;
	}
	return out;
}

// Hostile NPC: back straight out of the footprint (plus margin) while inside
// it; outside, no force (nothing pulls it back toward the body, because the
// body is not a target). Returns true while retreating.
static inline bool RaptureNpcRetreatStep(const float body[2], const float self[2],
	float radius, float out_force[2])
{
	out_force[0] = 0.0f;
	out_force[1] = 0.0f;
	const float dx = self[0] - body[0];
	const float dy = self[1] - body[1];
	if (dx * dx + dy * dy >= radius * radius)
		return false;
	float side[2];
	RaptureStandoffSide(body, self, side);
	out_force[0] = side[0] * RAPTURE_STANDOFF_FORCE;
	out_force[1] = side[1] * RAPTURE_STANDOFF_FORCE;
	return true;
}

// ─── World direction -> NPC step input ─────────────────────────────────
// The NPC physics step (MpStepOnce, server/mp_step.cpp) treats the intent
// force like player input: it is ROTATED by the step yaw, and the server
// feeds that yaw from intent_dir:
//   world = (fx*cos(yaw) - fy*sin(yaw), fx*sin(yaw) + fy*cos(yaw)).
// intent_dir also uses the facing convention atan2(dy,dx) + 90 deg
// (engine/facing_space.h). To walk along a WORLD direction, pick that facing
// as the yaw and express the force in its frame (the inverse rotation).
// (The pre-existing chase/follow code passes a world vector straight in, so
// it walks at 2*theta + 90 deg; see docs/FAILURE_LOG.md, not changed here.)
static inline float RaptureFacingDegFromWorld(float dx, float dy)
{
	return (float)(atan2((double)dy, (double)dx) * 180.0 / M_PI) + 90.0f;
}

static inline void RaptureNpcIntentFromWorld(const float world_force[2],
	float out_force[2], float* out_yaw_deg)
{
	const float yaw = RaptureFacingDegFromWorld(world_force[0], world_force[1]);
	const float r = (float)(yaw * (M_PI / 180.0));
	const float c = cosf(r), s = sinf(r);
	out_force[0] = world_force[0] * c + world_force[1] * s;
	out_force[1] = -world_force[0] * s + world_force[1] * c;
	*out_yaw_deg = yaw;
}

