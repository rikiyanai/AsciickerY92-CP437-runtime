// rapture_timeline.h — UFO Rapture Death timeline (shared server + client)
//
// PURPOSE:
// One constants block and one pure phase function for the player death
// sequence described in docs/specs/2026-10-08-ufo-rapture-death.md (§3/§4).
// The server uses RaptureEval() to drive the dead body's authoritative Z; the
// clients use the same function, fed with
// (authoritative_tick - presentation_started_tick), to decide body
// visibility / dissolve and to place the render-only UFO and beam. Both sides
// therefore agree by construction; no client timer advances the sequence.
//
// RULES:
// - Pure: no globals, no engine/server includes. Safe to include anywhere.
// - Server owns actor Z (FL-1434/FL-2023). Clients must NOT add
//   body_z_offset render-side; it already arrives in the snapshot pos.
//   ufo_z_offset / beam_len01 are for render-only effects (not actors).
// - Ticks are 30 Hz server ticks (SVR_TICK_RATE) measured from death_tick.
//
// FALL length measurement (2026-10-08, feat/ufo-rapture):
//   Player death presentation kind 602 rows (73 rows in
//   assets/actor_visual_profiles/source/profile_bindings.json) were measured
//   with scripts/compile_actor_visual_profiles.py:_load_xp():
//     - all plydie-*.xp sheets (skin 101, mounts 0/950/951): anim_lens = [5]
//     - wallace death sheet (skin 102): anim_lens = [1, 8]; death rows use
//       anim track 0 (locomotion_anim_track is [0,0,0,0] for non-idle kinds),
//       i.e. length 1.
//   Death rows use REVERSE_CLAMP playback (one frame per tick, see
//   ComputeFrameByPlayback), so the longest real death clip is 5 ticks
//   (~0.17 s). Spec §3 says FALL = max(clip_len, 30) => 30 ticks.

#pragma once

#include <stdint.h>

// ─── Timeline constants (30 Hz ticks since death) ───────────────
#define RAPTURE_DEATH_CLIP_MEASURED_TICKS 5u   // longest kind-602 clip (see above)
#define RAPTURE_FALL_MIN_TICKS            30u
#define RAPTURE_FALL_TICKS \
	(RAPTURE_DEATH_CLIP_MEASURED_TICKS > RAPTURE_FALL_MIN_TICKS ? \
	 RAPTURE_DEATH_CLIP_MEASURED_TICKS : RAPTURE_FALL_MIN_TICKS)
#define RAPTURE_DESCEND_TICKS             24u
#define RAPTURE_BEAM_OPEN_TICKS           12u
#define RAPTURE_LIFT_TICKS                48u
#define RAPTURE_BEAM_CLOSE_TICKS          9u
#define RAPTURE_DEPART_TICKS              18u

// Phase start ticks (cumulative). With FALL=30 these are 30/54/66/114/123/141.
#define RAPTURE_DESCEND_START_TICKS    (RAPTURE_FALL_TICKS)
#define RAPTURE_BEAM_OPEN_START_TICKS  (RAPTURE_DESCEND_START_TICKS + RAPTURE_DESCEND_TICKS)
#define RAPTURE_LIFT_START_TICKS       (RAPTURE_BEAM_OPEN_START_TICKS + RAPTURE_BEAM_OPEN_TICKS)
#define RAPTURE_BEAM_CLOSE_START_TICKS (RAPTURE_LIFT_START_TICKS + RAPTURE_LIFT_TICKS)
#define RAPTURE_DEPART_START_TICKS     (RAPTURE_BEAM_CLOSE_START_TICKS + RAPTURE_BEAM_CLOSE_TICKS)
#define RAPTURE_DONE_START_TICKS       (RAPTURE_DEPART_START_TICKS + RAPTURE_DEPART_TICKS)

// The existing respawn (2 x SVR_RESPAWN_DELAY_TICKS) must come after DEPART.
#define RAPTURE_EXPECTED_RESPAWN_TICKS 180u

// ─── Spatial constants (world Z units; HEIGHT_SCALE=16 z per visual cell) ──
// Sprite blit maps ~12.3 z units to one screen row (dz_dy in
// render_sprite_blit.cpp with SPRITE_SCALE=3), so 72 z ~= 6 rows of lift.
// The lift uses smoothstep, whose peak slope is 1.5x the mean, so the largest
// per-tick delta is 72 * 1.5 / 48 = 2.25 z. The client camera Z smoother
// (game_render_bridge.cpp, "teleport/respawn snap" when |dz| > 5, halving the
// residual each frame) sees at most ~2x one tick's delta when it renders at
// >= 30 fps, so the smooth lift does not trip the snap. scripts/
// test_rapture_timeline.cpp simulates that smoother at 30 and 60 fps.
#define RAPTURE_LIFT_HEIGHT_Z               72.0f
#define RAPTURE_MAX_LIFT_DELTA_PER_TICK_Z   2.4f   // test-enforced bound
#define RAPTURE_CAMERA_Z_SNAP_THRESHOLD     5.0f   // mirror of game_render_bridge.cpp
// UFO hover height above death_ground_z (render-only) and the off-screen
// offset used before DESCEND and after DEPART.
// Step-4 camera check: Render() runs at a fixed zoom (DBL, scale 3), so one
// screen row is ~12.3 z, and the camera centres the local player, leaving
// >= 18 rows above it (web Resize() keeps >= 36 rows). The ufo.xp reference
// is the hull bottom and the sheet is RAPTURE_UFO_SHEET_ROWS (5) rows tall, so
// the hull top sits at 128 / 12.3 + 5 ~= 15.4 rows above the death ground: on
// screen with a margin of ~2.5 rows even at the minimum viewport, before the lift starts
// The local camera is pinned to the death ground for the whole window
// (RaptureCameraFollowZ), so this pre-lift margin is also the margin during
// LIFT: ~2.6 rows at the 36-row minimum viewport.
// The lifted body (72 z ~= 5.9 rows, lying clip about +/-2 rows) ends just
// under the hull. 192 put the hull top at ~19.6 rows and clipped at 36 rows.
// Offscreen: 480 z ~= 39 rows above hover, beyond half of any viewport.
#define RAPTURE_UFO_HOVER_Z                 128.0f
#define RAPTURE_UFO_OFFSCREEN_Z             480.0f
// Rows of the ufo.xp sheet above its bottom reference (hull top margin check
// in scripts/test_rapture_timeline.cpp; scripts/test_rapture_timeline.py
// checks the sheet matches).
#define RAPTURE_UFO_SHEET_ROWS              5
// Compile-time "invisible UFO" option (operator 2026-10-08: "the ufo can
// honestly be invisible"). 1 = draw the saucer (default); 0 = skip
// RenderRaptureUfos entirely and drop the hull shadow on the beam top. The
// beam, motes, footprint, lift and dissolve are unchanged either way. The
// render harness can hide the UFO per run with no_ufo=1 instead.
#ifndef RAPTURE_UFO_VISIBLE
#define RAPTURE_UFO_VISIBLE                 1
#endif
// Dissolve runs over the top 30% of LIFT.
#define RAPTURE_DISSOLVE_START_LIFT01       0.7f

enum RapturePhaseId : uint8_t
{
	RAPTURE_PHASE_FALL = 0,
	RAPTURE_PHASE_DESCEND,
	RAPTURE_PHASE_BEAM_OPEN,
	RAPTURE_PHASE_LIFT,
	RAPTURE_PHASE_BEAM_CLOSE,
	RAPTURE_PHASE_DEPART,
	RAPTURE_PHASE_DONE,     // 141+ : empty ground until the existing respawn
	RAPTURE_PHASE_COUNT
};

struct RapturePhase
{
	RapturePhaseId phase;
	float t01;              // progress within the current phase, [0,1]
	float body_z_offset;    // added to death_ground_z by the SERVER only
	bool body_visible;      // false from BEAM_CLOSE on
	float body_dissolve01;  // 0 until the top 30% of LIFT, 1 at LIFT end
	float ufo_z_offset;     // offset above RAPTURE_UFO_HOVER_Z (render-only)
	bool ufo_visible;       // DESCEND..DEPART
	float beam_len01;       // 0 = closed, 1 = hull to ground
};

static inline float RaptureClamp01(float v)
{
	return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v);
}

static inline float RaptureSmoothstep01(float t)
{
	t = RaptureClamp01(t);
	return t * t * (3.0f - 2.0f * t);
}

static inline RapturePhase RaptureEval(uint32_t ticks_since_death)
{
	RapturePhase out = {};
	const uint32_t t = ticks_since_death;
	auto progress = [t](uint32_t start, uint32_t len) -> float
	{
		return len ? RaptureClamp01((float)(t - start) / (float)len) : 1.0f;
	};

	if (t < RAPTURE_DESCEND_START_TICKS)
	{
		out.phase = RAPTURE_PHASE_FALL;
		out.t01 = progress(0u, RAPTURE_FALL_TICKS);
	}
	else if (t < RAPTURE_BEAM_OPEN_START_TICKS)
	{
		out.phase = RAPTURE_PHASE_DESCEND;
		out.t01 = progress(RAPTURE_DESCEND_START_TICKS, RAPTURE_DESCEND_TICKS);
	}
	else if (t < RAPTURE_LIFT_START_TICKS)
	{
		out.phase = RAPTURE_PHASE_BEAM_OPEN;
		out.t01 = progress(RAPTURE_BEAM_OPEN_START_TICKS, RAPTURE_BEAM_OPEN_TICKS);
	}
	else if (t < RAPTURE_BEAM_CLOSE_START_TICKS)
	{
		out.phase = RAPTURE_PHASE_LIFT;
		out.t01 = progress(RAPTURE_LIFT_START_TICKS, RAPTURE_LIFT_TICKS);
	}
	else if (t < RAPTURE_DEPART_START_TICKS)
	{
		out.phase = RAPTURE_PHASE_BEAM_CLOSE;
		out.t01 = progress(RAPTURE_BEAM_CLOSE_START_TICKS, RAPTURE_BEAM_CLOSE_TICKS);
	}
	else if (t < RAPTURE_DONE_START_TICKS)
	{
		out.phase = RAPTURE_PHASE_DEPART;
		out.t01 = progress(RAPTURE_DEPART_START_TICKS, RAPTURE_DEPART_TICKS);
	}
	else
	{
		out.phase = RAPTURE_PHASE_DONE;
		out.t01 = 1.0f;
	}

	// Body: flat until LIFT, eased rise during LIFT, held (hidden) afterwards.
	const float lift01 = (out.phase < RAPTURE_PHASE_LIFT) ? 0.0f
		: (out.phase == RAPTURE_PHASE_LIFT ? out.t01 : 1.0f);
	out.body_z_offset = RAPTURE_LIFT_HEIGHT_Z * RaptureSmoothstep01(lift01);
	out.body_visible = out.phase < RAPTURE_PHASE_BEAM_CLOSE;
	out.body_dissolve01 = RaptureClamp01(
		(lift01 - RAPTURE_DISSOLVE_START_LIFT01) / (1.0f - RAPTURE_DISSOLVE_START_LIFT01));

	// UFO: eases down from off-screen during DESCEND, hovers, then
	// accelerates (quadratic ease-in) back off-screen during DEPART.
	switch (out.phase)
	{
	case RAPTURE_PHASE_FALL:
		out.ufo_z_offset = RAPTURE_UFO_OFFSCREEN_Z;
		out.ufo_visible = false;
		break;
	case RAPTURE_PHASE_DESCEND:
	{
		const float u = 1.0f - out.t01; // ease-out: fast in, gentle stop
		out.ufo_z_offset = RAPTURE_UFO_OFFSCREEN_Z * u * u;
		out.ufo_visible = true;
		break;
	}
	case RAPTURE_PHASE_DEPART:
		out.ufo_z_offset = RAPTURE_UFO_OFFSCREEN_Z * out.t01 * out.t01;
		out.ufo_visible = true;
		break;
	case RAPTURE_PHASE_DONE:
		out.ufo_z_offset = RAPTURE_UFO_OFFSCREEN_Z;
		out.ufo_visible = false;
		break;
	default:
		out.ufo_z_offset = 0.0f;
		out.ufo_visible = true;
		break;
	}

	// Beam: grows hull->ground in BEAM_OPEN, full in LIFT, retracts in BEAM_CLOSE.
	switch (out.phase)
	{
	case RAPTURE_PHASE_BEAM_OPEN:  out.beam_len01 = out.t01; break;
	case RAPTURE_PHASE_LIFT:       out.beam_len01 = 1.0f; break;
	case RAPTURE_PHASE_BEAM_CLOSE: out.beam_len01 = 1.0f - out.t01; break;
	default:                       out.beam_len01 = 0.0f; break;
	}
	return out;
}

// Elapsed ticks with the same underflow guard as ResolveActorVisualProfile's
// playback elapsed computation.
static inline uint32_t RaptureTicksSince(uint32_t now_tick, uint32_t start_tick)
{
	return (now_tick >= start_tick) ? (now_tick - start_tick) : 0u;
}

// Server: authoritative body Z for a dead player.
static inline float RaptureBodyZ(float death_ground_z, uint32_t ticks_since_death)
{
	return death_ground_z + RaptureEval(ticks_since_death).body_z_offset;
}

// Client: recover death_ground_z from an authoritative body Z sampled at
// ticks_since_death (the server wrote RaptureBodyZ() for that same tick).
static inline float RaptureDeathGroundZ(float body_z, uint32_t ticks_since_death)
{
	return body_z - RaptureEval(ticks_since_death).body_z_offset;
}

// Client camera follow Z for the LOCAL player (render-only, fix 2026-10-08).
// Outside the rapture window it is the display Z, unchanged. Inside it
// (life_state DEAD, server-stamped death tick, i.e. death until respawn) the
// camera stays on the death ground so the server-lifted body visibly rises up
// the beam instead of the ground sliding away. death_ground_z is recovered
// exactly as the UFO anchor does (RaptureDeathGroundZ on an authoritative
// body Z paired with its own tick). XY follow is untouched by the caller.
// The result feeds the existing camera Z smoother, so respawn snaps or eases
// by the same |dz| > RAPTURE_CAMERA_Z_SNAP_THRESHOLD rule as before.
static inline float RaptureCameraFollowZ(float display_z, bool local_in_rapture_window,
	float sample_body_z, uint32_t sample_ticks_since_death)
{
	if (!local_in_rapture_window)
		return display_z;
	return RaptureDeathGroundZ(sample_body_z, sample_ticks_since_death);
}

// Client: world Z of the UFO reference point (hull bottom).
static inline float RaptureUfoZ(float death_ground_z, const RapturePhase& p)
{
	return death_ground_z + RAPTURE_UFO_HOVER_Z + p.ufo_z_offset;
}

// Client: running-light frame of the UFO sheet (4 ticks per frame), a pure
// function of the authoritative tick so every client shows the same frame.
#define RAPTURE_UFO_TICKS_PER_FRAME 4u
static inline int RaptureUfoFrame(uint32_t ticks_since_death, int frame_count)
{
	if (frame_count <= 0)
		return 0;
	return (int)((ticks_since_death / RAPTURE_UFO_TICKS_PER_FRAME) % (uint32_t)frame_count);
}

// Server tick rate the timeline is authored in (matches SVR_TICK_RATE).
#define RAPTURE_TICK_RATE 30u

// Client: fade a 6x6x6 cube palette index (16 + 36r + 6g + b) toward the
// beam's yellow-white (r=5,g=5,b=3) by amount01. Indices outside the cube are returned
// unchanged below amount 1 and replaced by the target at amount 1.
#define RAPTURE_DISSOLVE_TARGET_PAL (16 + 36 * 5 + 6 * 5 + 3)
static inline int RaptureDissolvePaletteIndex(int c, float amount01)
{
	const float a = RaptureClamp01(amount01);
	if (a <= 0.0f)
		return c;
	if (c < 16 || c > 231)
		return a >= 1.0f ? RAPTURE_DISSOLVE_TARGET_PAL : c;
	int v = c - 16;
	const int r = v / 36; v -= 36 * r;
	const int g = v / 6;  v -= 6 * g;
	const int b = v;
	auto mix = [a](int from, int to) -> int
	{
		const float m = (float)from + ((float)to - (float)from) * a;
		int q = (int)(m + 0.5f);
		return q < 0 ? 0 : (q > 5 ? 5 : q);
	};
	return 16 + 36 * mix(r, 5) + 6 * mix(g, 5) + mix(b, 3);
}

static_assert(RAPTURE_FALL_TICKS >= 30u, "FALL must cover at least 1 s");
static_assert(RAPTURE_DONE_START_TICKS <= RAPTURE_EXPECTED_RESPAWN_TICKS,
              "rapture sequence must finish before the existing respawn");
