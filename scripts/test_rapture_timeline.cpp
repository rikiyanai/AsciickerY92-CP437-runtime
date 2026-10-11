// test_rapture_timeline.cpp — unit test for engine/rapture_timeline.h
//
// Built and run by scripts/test_rapture_timeline.py (wired into
// scripts/run_standalone_checks.sh). Covers phase boundaries, monotonic lift,
// the body visibility cut-over at BEAM_CLOSE, clamping past DEPART, the
// server-side body-Z helper as a per-tick simulation, the camera
// Z-snap margin, the local camera ground pin, and the dissolve palette helper.

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "rapture_timeline.h"

static int g_failures = 0;

#define CHECK(cond) do { if (!(cond)) { \
	fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
	g_failures++; } } while (0)

static bool Near(float a, float b, float eps = 1e-4f)
{
	return fabsf(a - b) <= eps;
}

static void TestBoundaries()
{
	// Spec §3 tick values at 30 Hz.
	CHECK(RAPTURE_FALL_TICKS == 30u);
	CHECK(RAPTURE_DESCEND_START_TICKS == 30u);
	CHECK(RAPTURE_BEAM_OPEN_START_TICKS == 54u);
	CHECK(RAPTURE_LIFT_START_TICKS == 66u);
	CHECK(RAPTURE_BEAM_CLOSE_START_TICKS == 114u);
	CHECK(RAPTURE_DEPART_START_TICKS == 123u);
	CHECK(RAPTURE_DONE_START_TICKS == 141u);

	struct { uint32_t tick; RapturePhaseId phase; } cases[] = {
		{ 0, RAPTURE_PHASE_FALL },        { 29, RAPTURE_PHASE_FALL },
		{ 30, RAPTURE_PHASE_DESCEND },    { 53, RAPTURE_PHASE_DESCEND },
		{ 54, RAPTURE_PHASE_BEAM_OPEN },  { 65, RAPTURE_PHASE_BEAM_OPEN },
		{ 66, RAPTURE_PHASE_LIFT },       { 113, RAPTURE_PHASE_LIFT },
		{ 114, RAPTURE_PHASE_BEAM_CLOSE },{ 122, RAPTURE_PHASE_BEAM_CLOSE },
		{ 123, RAPTURE_PHASE_DEPART },    { 140, RAPTURE_PHASE_DEPART },
		{ 141, RAPTURE_PHASE_DONE },      { 180, RAPTURE_PHASE_DONE },
	};
	for (const auto& c : cases)
	{
		const RapturePhase p = RaptureEval(c.tick);
		if (p.phase != c.phase)
			fprintf(stderr, "  tick %u phase %d expected %d\n", c.tick, (int)p.phase, (int)c.phase);
		CHECK(p.phase == c.phase);
		CHECK(p.t01 >= 0.0f && p.t01 <= 1.0f);
	}
	// t01 starts at 0 on each phase's first tick.
	CHECK(Near(RaptureEval(30).t01, 0.0f));
	CHECK(Near(RaptureEval(66).t01, 0.0f));
	CHECK(Near(RaptureEval(90).t01, 0.5f));
	CHECK(Near(RaptureEval(123).t01, 0.0f));
}

static void TestLift()
{
	// Flat before LIFT.
	for (uint32_t t = 0; t <= RAPTURE_LIFT_START_TICKS; t++)
		CHECK(Near(RaptureEval(t).body_z_offset, 0.0f));
	// Strictly rising inside LIFT, never decreasing anywhere, bounded per tick.
	float prev = RaptureEval(0).body_z_offset;
	float max_delta = 0.0f;
	for (uint32_t t = 1; t <= 400; t++)
	{
		const float z = RaptureEval(t).body_z_offset;
		CHECK(z + 1e-6f >= prev);
		if (t > RAPTURE_LIFT_START_TICKS && t <= RAPTURE_BEAM_CLOSE_START_TICKS)
			CHECK(z > prev);
		if (z - prev > max_delta)
			max_delta = z - prev;
		prev = z;
	}
	CHECK(max_delta <= RAPTURE_MAX_LIFT_DELTA_PER_TICK_Z);
	CHECK(2.0f * RAPTURE_MAX_LIFT_DELTA_PER_TICK_Z < RAPTURE_CAMERA_Z_SNAP_THRESHOLD);
	// Held at full height from BEAM_CLOSE through respawn.
	for (uint32_t t = RAPTURE_BEAM_CLOSE_START_TICKS; t <= RAPTURE_EXPECTED_RESPAWN_TICKS; t++)
		CHECK(Near(RaptureEval(t).body_z_offset, RAPTURE_LIFT_HEIGHT_Z));
}

static void TestVisibilityAndDissolve()
{
	for (uint32_t t = 0; t < RAPTURE_BEAM_CLOSE_START_TICKS; t++)
		CHECK(RaptureEval(t).body_visible);
	for (uint32_t t = RAPTURE_BEAM_CLOSE_START_TICKS; t <= 1000; t++)
		CHECK(!RaptureEval(t).body_visible);

	// No dissolve before 70% of LIFT, rising after, full at LIFT end.
	const uint32_t dissolve_start = RAPTURE_LIFT_START_TICKS +
		(uint32_t)(RAPTURE_LIFT_TICKS * RAPTURE_DISSOLVE_START_LIFT01);
	for (uint32_t t = 0; t <= dissolve_start; t++)
		CHECK(Near(RaptureEval(t).body_dissolve01, 0.0f, 1e-3f));
	float prev = 0.0f;
	for (uint32_t t = dissolve_start; t < RAPTURE_BEAM_CLOSE_START_TICKS; t++)
	{
		const float d = RaptureEval(t).body_dissolve01;
		CHECK(d + 1e-6f >= prev);
		prev = d;
	}
	CHECK(RaptureEval(RAPTURE_BEAM_CLOSE_START_TICKS - 1).body_dissolve01 > 0.9f);
	CHECK(Near(RaptureEval(RAPTURE_BEAM_CLOSE_START_TICKS).body_dissolve01, 1.0f));
}

static void TestUfoAndBeam()
{
	CHECK(!RaptureEval(0).ufo_visible);
	CHECK(RaptureEval(30).ufo_visible);
	CHECK(Near(RaptureEval(30).ufo_z_offset, RAPTURE_UFO_OFFSCREEN_Z));
	CHECK(Near(RaptureEval(54).ufo_z_offset, 0.0f));
	CHECK(Near(RaptureEval(100).ufo_z_offset, 0.0f));
	CHECK(Near(RaptureEval(123).ufo_z_offset, 0.0f));
	CHECK(!RaptureEval(141).ufo_visible);
	// Descend monotonic down, depart monotonic up.
	for (uint32_t t = 31; t < 54; t++)
		CHECK(RaptureEval(t).ufo_z_offset <= RaptureEval(t - 1).ufo_z_offset);
	for (uint32_t t = 124; t < 141; t++)
		CHECK(RaptureEval(t).ufo_z_offset >= RaptureEval(t - 1).ufo_z_offset);

	CHECK(Near(RaptureEval(53).beam_len01, 0.0f));
	CHECK(Near(RaptureEval(54).beam_len01, 0.0f));
	CHECK(Near(RaptureEval(60).beam_len01, 0.5f));
	CHECK(Near(RaptureEval(66).beam_len01, 1.0f));
	CHECK(Near(RaptureEval(113).beam_len01, 1.0f));
	CHECK(Near(RaptureEval(114).beam_len01, 1.0f));
	CHECK(RaptureEval(120).beam_len01 < 0.5f);
	CHECK(Near(RaptureEval(123).beam_len01, 0.0f));
}

static void TestClampPastDepart()
{
	const RapturePhase ref = RaptureEval(RAPTURE_DONE_START_TICKS);
	const uint32_t probes[] = { 141u, 142u, 180u, 1000u, 100000u, 0x7fffffffu, 0xffffffffu };
	for (uint32_t t : probes)
	{
		const RapturePhase p = RaptureEval(t);
		CHECK(p.phase == RAPTURE_PHASE_DONE);
		CHECK(Near(p.t01, 1.0f));
		CHECK(Near(p.body_z_offset, ref.body_z_offset));
		CHECK(!p.body_visible);
		CHECK(!p.ufo_visible);
		CHECK(Near(p.beam_len01, 0.0f));
	}
	// Underflow guard: a start tick ahead of "now" reads as 0 elapsed.
	CHECK(RaptureTicksSince(10u, 20u) == 0u);
	CHECK(RaptureTicksSince(200u, 20u) == 180u);
}

// Mirrors the server per-tick body-Z rule (SvrProcessRapture) using the same
// header helper the server calls.
static void TestServerHelperTimeline()
{
	const float ground = 87.25f;
	float prev = RaptureBodyZ(ground, 0);
	for (uint32_t t = 0; t <= RAPTURE_EXPECTED_RESPAWN_TICKS; t++)
	{
		const float z = RaptureBodyZ(ground, t);
		if (t <= RAPTURE_LIFT_START_TICKS)
			CHECK(Near(z, ground));                       // flat FALL..BEAM_OPEN
		else if (t <= RAPTURE_BEAM_CLOSE_START_TICKS)
			CHECK(z > prev);                              // rising LIFT
		else
			CHECK(Near(z, ground + RAPTURE_LIFT_HEIGHT_Z)); // held to respawn
		prev = z;
	}
}

// Simulate the client camera smoother: cam += dz * 0.5 per frame, snapping
// when |dz| > threshold. The snapshot pos advances once per server tick.
static void TestCameraSnapMargin(int frames_per_tick)
{
	const float ground = 60.0f;
	float cam = ground;
	int snaps = 0;
	for (uint32_t t = 0; t <= RAPTURE_EXPECTED_RESPAWN_TICKS; t++)
	{
		const float target = RaptureBodyZ(ground, t);
		for (int f = 0; f < frames_per_tick; f++)
		{
			const float dz = target - cam;
			if (dz > RAPTURE_CAMERA_Z_SNAP_THRESHOLD || dz < -RAPTURE_CAMERA_Z_SNAP_THRESHOLD)
			{
				cam = target;
				snaps++;
			}
			else
				cam += dz * 0.5f;
		}
	}
	CHECK(snaps == 0);
}

// Fix 2026-10-08: the local camera follows the death ground, not the lifted
// body, for the whole window (RaptureCameraFollowZ feeding the bridge's
// smoother: cam += dz * 0.5, snap when |dz| > threshold), then snaps or eases
// to the spawn exactly as the smoother always did.
static float SmoothCam(float cam, float target, int* snaps)
{
	const float dz = target - cam;
	if (dz > RAPTURE_CAMERA_Z_SNAP_THRESHOLD || dz < -RAPTURE_CAMERA_Z_SNAP_THRESHOLD)
	{
		(*snaps)++;
		return target;
	}
	return cam + dz * 0.5f;
}

static void TestCameraFollowPin(float spawn_dz, int expect_respawn_snaps)
{
	// Outside the window it is a pass-through of the display Z.
	CHECK(Near(RaptureCameraFollowZ(12.5f, false, 99.0f, 90), 12.5f));
	const float ground = 57.25f;
	float cam = ground;
	int snaps = 0;
	for (uint32_t t = 0; t < RAPTURE_EXPECTED_RESPAWN_TICKS; t++)
	{
		const float body = RaptureBodyZ(ground, t);           // server pos[2]
		for (int f = 0; f < 2; f++)                           // 60 fps
		{
			// display Z (interpolated, may lag) is ignored inside the window
			const float display = RaptureBodyZ(ground, t > 3 ? t - 3 : 0);
			cam = SmoothCam(cam, RaptureCameraFollowZ(display, true, body, t), &snaps);
			CHECK(Near(cam, ground, 1e-3f));
		}
		// A sample one tick older than the authoritative tick (delta
		// snapshot skipped the entity) stays within one lift step.
		if (t > 0)
		{
			const float stale = RaptureCameraFollowZ(0.0f, true, RaptureBodyZ(ground, t - 1), t);
			CHECK(fabsf(stale - ground) <= RAPTURE_MAX_LIFT_DELTA_PER_TICK_Z);
		}
	}
	CHECK(snaps == 0);
	// Respawn: life_state ALIVE and the spawn pose arrive in one snapshot; the
	// window closes and the smoother sees spawn Z - death ground Z only.
	const float spawn = ground + spawn_dz;
	for (int f = 0; f < 30; f++)
		cam = SmoothCam(cam, RaptureCameraFollowZ(spawn, false, 0.0f, 0), &snaps);
	CHECK(snaps == expect_respawn_snaps);
	CHECK(Near(cam, spawn, 1e-3f));
}

static void TestDissolvePalette()
{
	const int target = RAPTURE_DISSOLVE_TARGET_PAL;
	CHECK(target == 229); // (5,5,3) yellow-white, the beam's light
	const int red = 16 + 36 * 5; // (5,0,0)
	CHECK(RaptureDissolvePaletteIndex(red, 0.0f) == red);
	CHECK(RaptureDissolvePaletteIndex(red, 1.0f) == target);
	CHECK(RaptureDissolvePaletteIndex(red, 2.0f) == target);
	const int mid = RaptureDissolvePaletteIndex(red, 0.5f);
	CHECK(mid >= 16 && mid <= 231 && mid != red && mid != target);
	CHECK(RaptureDissolvePaletteIndex(target, 0.6f) == target);
	// Non-cube indices: unchanged until full dissolve.
	CHECK(RaptureDissolvePaletteIndex(7, 0.5f) == 7);
	CHECK(RaptureDissolvePaletteIndex(240, 1.0f) == target);
	for (int c = 16; c <= 231; c++)
		for (int k = 0; k <= 10; k++)
		{
			const int out = RaptureDissolvePaletteIndex(c, k / 10.0f);
			CHECK(out >= 16 && out <= 231);
		}
}

static void TestClientUfoHelpers()
{
	// death_ground_z recovery inverts the server's RaptureBodyZ at every tick.
	for (uint32_t t = 0; t <= 200; t++)
	{
		const float ground = 37.25f;
		CHECK(Near(RaptureDeathGroundZ(RaptureBodyZ(ground, t), t), ground, 1e-3f));
	}
	// UFO reference Z: hover during the beam phases, far above otherwise.
	CHECK(Near(RaptureUfoZ(10.0f, RaptureEval(90)), 10.0f + RAPTURE_UFO_HOVER_Z));
	CHECK(RaptureUfoZ(0.0f, RaptureEval(RAPTURE_DESCEND_START_TICKS)) >
	      RAPTURE_UFO_HOVER_Z + 0.9f * RAPTURE_UFO_OFFSCREEN_Z);
	// Viewport margin (step-4 camera check): ~12.3 z per row at the fixed
	// render zoom, RAPTURE_UFO_SHEET_ROWS-row sheet above its bottom reference,
	// >= 18 rows above the centred local player at the 36-row minimum web
	// viewport.
	const float z_per_row = 16.0f / (0.8660254f * 4.0f * 0.375f);
	const float hull_top_rows = RAPTURE_UFO_HOVER_Z / z_per_row + (float)RAPTURE_UFO_SHEET_ROWS;
	CHECK(hull_top_rows <= 18.0f - 2.0f);
	// Hull bottom stays above the fully lifted, lying body (~2 rows tall).
	CHECK((RAPTURE_UFO_HOVER_Z - RAPTURE_LIFT_HEIGHT_Z) / z_per_row >= 2.0f + 1.0f);
	// Offscreen offset clears any viewport half (>= 32 rows above hover).
	CHECK(RAPTURE_UFO_OFFSCREEN_Z / z_per_row >= 32.0f);
	// Running lights: deterministic, 4 ticks per frame, wraps, safe on 0.
	CHECK(RaptureUfoFrame(0, 4) == 0);
	CHECK(RaptureUfoFrame(3, 4) == 0);
	CHECK(RaptureUfoFrame(4, 4) == 1);
	CHECK(RaptureUfoFrame(16, 4) == 0);
	CHECK(RaptureUfoFrame(17, 0) == 0);
}

int main()
{
	TestBoundaries();
	TestLift();
	TestVisibilityAndDissolve();
	TestUfoAndBeam();
	TestClampPastDepart();
	TestServerHelperTimeline();
	TestCameraSnapMargin(1);
	TestCameraSnapMargin(2);
	TestCameraSnapMargin(4);
	TestCameraFollowPin(-0.25f, 0); // flat map: eases
	TestCameraFollowPin(40.0f, 1);  // spawn on a hill: one snap, as before
	TestDissolvePalette();
	TestClientUfoHelpers();
	if (g_failures)
	{
		fprintf(stderr, "rapture timeline: %d check(s) failed\n", g_failures);
		return 1;
	}
	printf("PASS: rapture timeline (FALL=%u, phases 30/54/66/114/123/141, lift %.0f z)\n",
	       (unsigned)RAPTURE_FALL_TICKS, (double)RAPTURE_LIFT_HEIGHT_Z);
	return 0;
}
