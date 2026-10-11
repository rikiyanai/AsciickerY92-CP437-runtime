// test_rapture_standoff.cpp — unit test for engine/rapture_standoff.h
//
// Built and run by scripts/test_rapture_timeline.py. Covers the footprint
// radius conversion (screen cells -> world units), the companion stand-off
// goal and its hysteresis as a walk simulation (no teleport), the hostile
// retreat rule, and the rule that no NPC targets a dead / raptured player.

#include <math.h>
#include <stdio.h>

#include "rapture_standoff.h"

static int g_failures = 0;

#define CHECK(cond) do { if (!(cond)) { \
	fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
	g_failures++; } } while (0)

static bool Near(float a, float b, float eps = 1e-3f)
{
	return fabsf(a - b) <= eps;
}

static float Dist(const float a[2], const float b[2])
{
	return sqrtf((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]));
}

static void TestFootprintRadius()
{
	// Projection mirror: 1.5 columns and 0.75 rows per world unit.
	CHECK(Near(RAPTURE_SCREEN_COLS_PER_WORLD_UNIT, 1.5f, 0.0f));
	CHECK(Near(RAPTURE_SCREEN_ROWS_PER_WORLD_UNIT, 0.75f, 0.0f));
	// Ring 16 columns x 4 rows -> 10.67 x 5.33 units; bounding circle 10.67.
	CHECK(Near(RAPTURE_BEAM_RING_HALF_WIDTH, 16.0f, 0.0f));
	CHECK(Near(RaptureFootprintRadiusWorld(), 16.0f / 1.5f));
	// It bounds the cone's ground half-width (11 columns) and the lit ellipse.
	CHECK(RaptureFootprintRadiusWorld() >= RAPTURE_BEAM_GROUND_HALF_WIDTH / RAPTURE_SCREEN_COLS_PER_WORLD_UNIT);
	CHECK(RaptureFootprintRadiusWorld() >= RAPTURE_BEAM_GLOW_HALF_HEIGHT / RAPTURE_SCREEN_ROWS_PER_WORLD_UNIT);
	// Stand-off and retreat radii sit outside it, retreat the wider.
	CHECK(RaptureCompanionStandoffRadius() > RaptureFootprintRadiusWorld());
	CHECK(RaptureNpcRetreatRadius() > RaptureCompanionStandoffRadius());
}

// Companion: goal is on the circle, on the side he is already on.
static void TestCompanionGoal()
{
	const float body[2] = { 26.7f, -0.8f };
	const float R = RaptureCompanionStandoffRadius();
	// He stopped 7 units from the owner (the normal follow dead band).
	const float dog[2] = { body[0] + 7.0f * 0.6f, body[1] - 7.0f * 0.8f };
	const RaptureStandoffStep s = RaptureCompanionStandoffStep(body, dog, R, false);
	CHECK(Near(Dist(s.goal, body), R));
	// Same side: the goal lies on the body->dog ray.
	CHECK(Near((s.goal[0] - body[0]) / R, 0.6f) && Near((s.goal[1] - body[1]) / R, -0.8f));
	CHECK(s.move);
	CHECK(Near(s.dist_to_goal, R - 7.0f));
	// Force points away from the body (outward), magnitude 0.5.
	CHECK(Near(sqrtf(s.force[0] * s.force[0] + s.force[1] * s.force[1]), RAPTURE_STANDOFF_FORCE));
	CHECK(s.force[0] > 0.0f && s.force[1] < 0.0f);
	// On the axis: deterministic fallback side (-4,-3)/5.
	const RaptureStandoffStep z = RaptureCompanionStandoffStep(body, body, R, false);
	CHECK(Near(z.goal[0], body[0] - 0.8f * R) && Near(z.goal[1], body[1] - 0.6f * R));
	// Far away (lagging behind): walks in to the same circle, still his side.
	const float far_dog[2] = { body[0] - 30.0f, body[1] };
	const RaptureStandoffStep f = RaptureCompanionStandoffStep(body, far_dog, R, false);
	CHECK(f.move && Near(f.goal[0], body[0] - R) && Near(f.goal[1], body[1]));
	CHECK(f.force[0] > 0.0f);
}

// Hysteresis: start beyond 1.5, stop inside 0.5, hold in between.
static void TestCompanionHysteresis()
{
	const float body[2] = { 0.0f, 0.0f };
	const float R = RaptureCompanionStandoffRadius();
	const float at1[2] = { R + 1.0f, 0.0f };   // 1.0 from goal: dead band
	CHECK(RaptureCompanionStandoffStep(body, at1, R, true).move);
	CHECK(!RaptureCompanionStandoffStep(body, at1, R, false).move);
	const float at2[2] = { R + 2.0f, 0.0f };   // beyond start
	CHECK(RaptureCompanionStandoffStep(body, at2, R, false).move);
	const float at0[2] = { R + 0.2f, 0.0f };   // inside stop
	const RaptureStandoffStep h = RaptureCompanionStandoffStep(body, at0, R, true);
	CHECK(!h.move && h.force[0] == 0.0f && h.force[1] == 0.0f);
}

// Walk simulation: per-tick displacement bounded (no teleport), ends held
// just outside the footprint, never crosses the body.
static void TestCompanionWalk()
{
	const float body[2] = { 10.0f, 20.0f };
	const float R = RaptureCompanionStandoffRadius();
	float dog[2] = { body[0] + 5.0f, body[1] + 5.0f };   // 7.07 from the body
	bool moving = false;
	const float speed_per_tick = 0.6f;                   // ~18 units/s at 30 Hz
	int held_ticks = 0;
	for (int t = 0; t < 180; t++)
	{
		const RaptureStandoffStep s = RaptureCompanionStandoffStep(body, dog, R, moving);
		moving = s.move;
		const float step[2] = { s.force[0] / RAPTURE_STANDOFF_FORCE * speed_per_tick,
		                        s.force[1] / RAPTURE_STANDOFF_FORCE * speed_per_tick };
		CHECK(sqrtf(step[0] * step[0] + step[1] * step[1]) <= speed_per_tick + 1e-4f);
		dog[0] += step[0];
		dog[1] += step[1];
		// same side throughout
		CHECK(dog[0] > body[0] && dog[1] > body[1]);
		if (!moving)
			held_ticks++;
	}
	const float d = Dist(dog, body);
	CHECK(d > RaptureFootprintRadiusWorld());
	CHECK(fabsf(d - R) <= RAPTURE_COMPANION_STANDOFF_START);
	CHECK(held_ticks > 150);
}

static void TestNpcRetreat()
{
	const float body[2] = { 40.0f, -8.0f };
	const float R = RaptureNpcRetreatRadius();
	float f[2] = { 9.0f, 9.0f };
	// The killer stands in swing range on the body's north-east side.
	float npc[2] = { body[0] + 3.0f, body[1] + 4.0f };
	CHECK(RaptureNpcRetreatStep(body, npc, R, f));
	CHECK(Near(f[0], 0.6f * RAPTURE_STANDOFF_FORCE) && Near(f[1], 0.8f * RAPTURE_STANDOFF_FORCE));
	// Walk it out: it stops once outside the radius and stays there.
	int ticks = 0;
	while (RaptureNpcRetreatStep(body, npc, R, f) && ticks < 300)
	{
		npc[0] += f[0] / RAPTURE_STANDOFF_FORCE * 0.6f;
		npc[1] += f[1] / RAPTURE_STANDOFF_FORCE * 0.6f;
		ticks++;
	}
	CHECK(ticks < 300);
	CHECK(Dist(npc, body) >= R);
	CHECK(!RaptureNpcRetreatStep(body, npc, R, f) && f[0] == 0.0f && f[1] == 0.0f);
	// Standing on the body: deterministic fallback direction.
	CHECK(RaptureNpcRetreatStep(body, body, R, f));
	CHECK(Near(f[0], -0.8f * RAPTURE_STANDOFF_FORCE) && Near(f[1], -0.6f * RAPTURE_STANDOFF_FORCE));
}

static void TestTargetRule()
{
	// alive, in play: targetable
	CHECK(RaptureNpcMayTargetPlayer(true, true, 0u, false));
	// dead (death_tick stamped), DEAD or RESPAWNING phase: never
	CHECK(!RaptureNpcMayTargetPlayer(true, false, 2709u, true));
	CHECK(!RaptureNpcMayTargetPlayer(true, false, 2709u, false));
	// rapture window flag alone is enough (belt and braces)
	CHECK(!RaptureNpcMayTargetPlayer(true, true, 0u, true));
	// alive phase but death stamped (same tick as the kill): never
	CHECK(!RaptureNpcMayTargetPlayer(true, true, 2709u, false));
	// inactive slot: never
	CHECK(!RaptureNpcMayTargetPlayer(false, true, 0u, false));
	// after respawn (death_tick cleared, rapture_active cleared): again
	CHECK(RaptureNpcMayTargetPlayer(true, true, 0u, false));
}

// The physics step rotates the intent force by the step yaw (= intent_dir).
// Mirror of server/mp_step.cpp MpStepOnce (the runner checks the source line).
static void MpStepWorldDir(const float f[2], float yaw_deg, float out[2])
{
	const float len = sqrtf(f[0] * f[0] + f[1] * f[1]);
	const float dx = f[0] / len, dy = f[1] / len;
	const float yaw_rad = (float)(yaw_deg * (M_PI / 180.0));
	out[0] = dx * cosf(yaw_rad) - dy * sinf(yaw_rad);
	out[1] = dx * sinf(yaw_rad) + dy * cosf(yaw_rad);
}

static void TestIntentFrame()
{
	for (int a = -180; a < 180; a += 15)
	{
		const float r = (float)(a * M_PI / 180.0);
		const float world[2] = { cosf(r) * RAPTURE_STANDOFF_FORCE, sinf(r) * RAPTURE_STANDOFF_FORCE };
		float local[2], yaw = 0.0f, moved[2];
		RaptureNpcIntentFromWorld(world, local, &yaw);
		// same magnitude (same speed as the existing follow / chase force)
		CHECK(Near(sqrtf(local[0] * local[0] + local[1] * local[1]), RAPTURE_STANDOFF_FORCE));
		// facing convention: atan2 + 90 (S = 0, E = 90, N = 180)
		CHECK(Near(yaw, RaptureFacingDegFromWorld(world[0], world[1])));
		// the physics step turns it back into the intended world direction
		MpStepWorldDir(local, yaw, moved);
		CHECK(Near(moved[0], cosf(r)) && Near(moved[1], sinf(r)));
		// whereas a raw world vector (the pre-existing chase convention)
		// moves at 2*theta + 90 deg, which is only right for theta = -90.
		float raw[2];
		MpStepWorldDir(world, yaw, raw);
		const float rr = 2.0f * r + (float)(M_PI / 2.0);
		CHECK(Near(raw[0], cosf(rr), 1e-2f) && Near(raw[1], sinf(rr), 1e-2f));
	}
	CHECK(Near(RaptureFacingDegFromWorld(0.0f, -1.0f), 0.0f));   // S
	CHECK(Near(RaptureFacingDegFromWorld(1.0f, 0.0f), 90.0f));   // E
	CHECK(Near(RaptureFacingDegFromWorld(0.0f, 1.0f), 180.0f));  // N
}

int main()
{
	TestIntentFrame();
	TestFootprintRadius();
	TestCompanionGoal();
	TestCompanionHysteresis();
	TestCompanionWalk();
	TestNpcRetreat();
	TestTargetRule();
	if (g_failures)
	{
		fprintf(stderr, "rapture standoff: %d check(s) failed\n", g_failures);
		return 1;
	}
	printf("PASS: rapture standoff (footprint %.2f u, companion %.2f u, npc retreat %.2f u)\n",
	       (double)RaptureFootprintRadiusWorld(), (double)RaptureCompanionStandoffRadius(),
	       (double)RaptureNpcRetreatRadius());
	return 0;
}
