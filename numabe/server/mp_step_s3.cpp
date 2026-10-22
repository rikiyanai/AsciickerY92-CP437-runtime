// =============================================================================
// server/mp_step_s3.cpp — FL-4257 corrective rebuild: the server-side S3
// movement/support owner. This is the ONE server translation unit that
// includes engine/matrix_curved.h (no-dual-include audit); mp_step.cpp stays
// on the Euclidean side and hands the WHOLE step here when the server
// publishes spherical curvature.
//
// Runtime truth model (see matrix_curved.h S3 WORLD MODEL):
//   - the player pose is a unit point on the equatorial ground 2-sphere of
//     S3 plus a RADIAL height (stored: s3_ground[3], height in pos[2]);
//   - the heading frame axis s3_east is PARALLEL TRANSPORTED along every
//     geodesic step (frame = {east, ground x east}); the angle between the
//     transported frame and the chart frame is the holonomy the FL-4257
//     proof gates measure;
//   - input maps to a tangent direction: the integrated chart-axis velocity
//     components are read as components on the transported frame;
//   - velocity is arc length per second (angular velocity = speed / R);
//   - gravity is radial (vel[2] integrates the radial height scalar);
//   - support is the radial test of pose height against the terrain surface
//     under the pose (heightfield-over-ground-sphere: the radial probe IS
//     the spherical-surface support test) — no Euclidean swept AABB runs,
//     and no flat collision result is re-embedded as proof;
//   - chart pos[0..1] is written back as IMPORT/DEBUG/COMPAT output only
//     (wire compatibility); it is never read back as movement truth once
//     s3_valid is set.
//
// OPEN (logged in FL-4257, not claimed): mesh-instance collision on S3
// (world meshes do not yet block movement under spherical curvature),
// spherical NPC pathfinding, jump/step animation parity details.
// =============================================================================

#include <math.h>
#include <string.h>

#include "mp_step.h"
#include "../engine/curvature.h"
#include "../engine/facing_space.h"
#include "../engine/matrix_curved.h"
#include "../engine/terrain.h"
#include "../engine/world.h"

static const float kS3StepUpLimit = 16.0f;     // native z; one HEIGHT_SCALE
static const float kS3ContactDecay = 0.9f;     // mirrors kMpGroundContactDecay
static const float kS3GroundDecay = 0.9f;      // mirrors mp_step.cpp local decay

int MpStepS3Owns(const MpStepEnv& env)
{
	if (env.curvature_present != 1)
		return 0;
	if (env.curvature_kind == ASCK_CURVATURE_KIND_EUCLIDEAN)
		return 0;
	if (env.curvature_kind != ASCK_CURVATURE_KIND_SPHERICAL)
		return -1; // recognised-as-published but not implemented: fail closed
	if (!CurvedKappaIsSpherical(env.curvature_kappa))
		return -1; // spherical kind with non-spherical kappa: invalid authority
	return 1;
}

// Radial support probe: terrain height under a chart/compat ground point.
// The heightfield is indexed by chart coordinates (A3D import payload); the
// SUPPORT TEST itself is radial on the sphere model.
static int S3TerrainSupport(Terrain* terrain, double cx, double cy, float* out_h)
{
	if (!terrain)
		return 0;
	double rp[3] = { cx, cy, 0xFFFF };
	double rv[3] = { 0, 0, -1 };
	double hit[4];
	if (!HitTerrain(terrain, rp, rv, hit))
		return 0;
	*out_h = (float)hit[2];
	return 1;
}

static void S3LazyInit(const MpStepEnv& env, double R, MpStepState* st)
{
	if (st->s3_valid)
		return;
	double cx = (double)st->pos[0] - env.curvature_anchor[0];
	double cy = (double)st->pos[1] - env.curvature_anchor[1];
	double P[4];
	curved_to_embedding(R, cx, cy, 0.0, P); // ground point: radial height separate
	st->s3_ground[0] = P[0];
	st->s3_ground[1] = P[1];
	st->s3_ground[2] = P[2];
	double E[4], N[4], U[4];
	curved_world_frame(R, cx, cy, 0.0, E, N, U);
	st->s3_east[0] = E[0];
	st->s3_east[1] = E[1];
	st->s3_east[2] = E[2];
	st->s3_valid = 1;
}

static inline void S3Cross3(const double a[3], const double b[3], double o[3])
{
	o[0] = a[1] * b[2] - a[2] * b[1];
	o[1] = a[2] * b[0] - a[0] * b[2];
	o[2] = a[0] * b[1] - a[1] * b[0];
}

static inline double S3Dot3(const double a[3], const double b[3])
{
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

void MpStepS3Move(const MpStepEnv& env, const MpStepInput& input, float dt,
                  MpStepState* next, MpStepResult* result)
{
	const double R = (double)SphericalCurvatureRadius(env.curvature_kappa);
	if (R <= 0.0)
		return; // MpStepS3Owns already gated this; never default
	S3LazyInit(env, R, next);

	double g[3] = { next->s3_ground[0], next->s3_ground[1], next->s3_ground[2] };
	double e0[3] = { next->s3_east[0], next->s3_east[1], next->s3_east[2] };
	// re-orthonormalise the frame against drift
	double ge = S3Dot3(g, e0);
	for (int i = 0; i < 3; i++) e0[i] -= ge * g[i];
	double el = sqrt(S3Dot3(e0, e0));
	if (el > 1e-12) for (int i = 0; i < 3; i++) e0[i] /= el;
	double e1[3];
	S3Cross3(g, e0, e1);

	const float prev_contact = next->accum_contact;
	const float h_old = next->pos[2];

	float xy_len = sqrtf(input.x_force * input.x_force + input.y_force * input.y_force);
	int ix = 0;
	int iy = 0;
	if (input.x_force < 0.0f) ix--;
	if (input.x_force > 0.0f) ix++;
	if (input.y_force > 0.0f) iy++;
	if (input.y_force < 0.0f) iy--;
	if (xy_len < 0.01f)
	{
		xy_len = 0.0f;
		ix = 0;
		iy = 0;
	}
	else
	{
		float dx = input.x_force / xy_len;
		float dy = input.y_force / xy_len;
		if (xy_len > 1.0f)
			xy_len = 1.0f;
		const float yaw_rad = (float)(next->yaw * (ASCK_CURVED_PI / 180.0));
		float move_e = dx * cosf(yaw_rad) - dy * sinf(yaw_rad);
		float move_n = dx * sinf(yaw_rad) + dy * cosf(yaw_rad);
		float cs = cosf(next->slope);
		next->vel[0] += dt * move_e * cs;
		next->vel[1] += dt * move_n * cs;
		// FL-3858 / FL-4257: spherical movement must not revive the old
		// camera-yaw facing owner. Face the movement vector in the transported
		// S3 tangent frame, using the same front door as flat SP/MP.
		next->player_dir = FacingMovementStep(next->player_dir, move_e, move_n);
	}

	float sqr_vel_xy = next->vel[0] * next->vel[0] + next->vel[1] * next->vel[1];
	if (sqr_vel_xy < 1.0f && !ix && !iy)
	{
		next->vel[0] = 0.0f;
		next->vel[1] = 0.0f;
		if (env.mount < 2)
			next->player_stp = -1;
	}
	else
	{
		float xy_limit = 27.0f - 17.0f * (next->water - next->pos[2]) / env.world_height;
		float lim = 27.0f * xy_len * xy_len * xy_len;
		if (xy_limit < 10.0f)
			xy_limit = 10.0f;
		if (xy_limit > lim)
			xy_limit = lim;
		float sqr_xy_limit = xy_limit * xy_limit;
		if (sqr_vel_xy > sqr_xy_limit)
		{
			float n = sqrtf(sqr_xy_limit / sqr_vel_xy);
			sqr_vel_xy = sqr_xy_limit;
			next->vel[0] *= n;
			next->vel[1] *= n;
		}
		if (next->player_stp < 0)
			next->player_stp = 0;
		float vel_damp = powf(kS3GroundDecay, dt);
		next->vel[0] *= vel_damp;
		next->vel[1] *= vel_damp;
	}

	float wave = 2.0f * (int)((env.stamp_us >> 10) & 0x7FF) * (float)ASCK_CURVED_PI / 0x800;
	float ampl = (ix || iy) ? 0.1f : 0.05f;
	float cnt = 0.78f + ampl * sinf(wave);
	float acc = (next->water - (next->pos[2] + cnt * env.world_height)) / (2.0f * cnt * env.world_height);
	if (acc < -cnt)
		acc = -cnt;
	if (acc > 1.0f - cnt)
		acc = 1.0f - cnt;
	if (input.fly)
	{
		float z_acc = input.z_force;
		if (fabsf(z_acc) > 0.001f)
			next->vel[2] += dt * z_acc;
		float z_damp = powf(kS3GroundDecay, dt);
		next->vel[2] *= z_damp;
	}
	else
	{
		next->vel[2] += dt * acc;
	}
	float res = (next->water - next->pos[2]) / env.world_height;
	if (res < 0.0f)
		res = 0.0f;
	if (res > 1.0f)
		res = 1.0f;
	result->in_water = res;
	float xy_res = powf(1.0f - 0.5f * res, dt);
	float z_res = powf(1.0f - 0.1f * res, dt);
	if (env.mount > 1 && next->vel[2] < 0.0f)
		z_res = powf(1.0f - 0.1f, dt);
	next->vel[0] *= xy_res;
	next->vel[1] *= xy_res;
	next->vel[2] *= z_res;
	next->vel[0] += next->impulse[0];
	next->vel[1] += next->impulse[1];
	if (fabsf(next->impulse[0]) + fabsf(next->impulse[1]) > 1.0f && next->vel[2] > 0.0f)
		next->vel[2] = 0.0f;
	next->impulse[0] *= 0.5f;
	next->impulse[1] *= 0.5f;

	// ── tangent input → great-circle advance with parallel transport ───────
	// next->vel[0..1] is integrated here on the transported frame {e0,e1};
	// chart-space dx/dy never owns spherical movement.
	double vx = (double)next->vel[0];
	double vy = (double)next->vel[1];
	double speed = sqrt(vx * vx + vy * vy);
	double arc_speed = speed * (double)env.xy_speed;
	float blocked = 0.0f;
	if (speed > 1e-9)
	{
		double t[3];
		for (int i = 0; i < 3; i++)
			t[i] = (vx * e0[i] + vy * e1[i]) / speed;
		double ang = arc_speed * (double)dt / R; // angular velocity * dt
		double c = cos(ang), s = sin(ang);
		double g2[3], t2[3];
		for (int i = 0; i < 3; i++)
		{
			g2[i] = c * g[i] + s * t[i];          // exponential map
			t2[i] = c * t[i] - s * g[i];          // transported motion dir
		}
		// parallel transport of the frame axis e0 along the same geodesic
		double e0d = S3Dot3(e0, t);
		double e0g = S3Dot3(e0, g);
		double e0n[3];
		for (int i = 0; i < 3; i++)
		{
			double in_plane = e0d * t2[i] + e0g * g2[i];
			double off = e0[i] - e0d * t[i] - e0g * g[i];
			e0n[i] = in_plane + off;
		}
		// spherical-surface step test BEFORE committing the move: the
		// terrain surface under the candidate pose must be climbable.
		double cx2, cy2, hdummy;
		double P2[4] = { g2[0], g2[1], g2[2], 0.0 };
		float h_support_new = 0.0f;
		int has_support_new = 0;
		if (curved_from_embedding(R, P2, &cx2, &cy2, &hdummy))
			has_support_new = S3TerrainSupport(env.terrain,
				cx2 + env.curvature_anchor[0], cy2 + env.curvature_anchor[1],
				&h_support_new);
		if (has_support_new && prev_contact >= 1.0f &&
		    h_support_new - h_old > kS3StepUpLimit)
		{
			// wall: the radial rise across this arc exceeds the step
			// limit — block the horizontal advance (collision against the
			// spherical terrain surface; no swept-AABB resolution).
			blocked = 1.0f;
		}
		else
		{
			memcpy(g, g2, sizeof(g));
			memcpy(e0, e0n, sizeof(e0));
			ge = S3Dot3(g, e0);
			for (int i = 0; i < 3; i++) e0[i] -= ge * g[i];
			el = sqrt(S3Dot3(e0, e0));
			if (el > 1e-12) for (int i = 0; i < 3; i++) e0[i] /= el;
			S3Cross3(g, e0, e1);
		}
	}

	// ── radial gravity / vertical integration / support ────────────────────
	float h = h_old + dt * next->vel[2];
	double cxn = 0.0, cyn = 0.0, hn = 0.0;
	double Pn[4] = { g[0], g[1], g[2], 0.0 };
	int chart_ok = curved_from_embedding(R, Pn, &cxn, &cyn, &hn);
	float h_support = 0.0f;
	int has_support = 0;
	if (chart_ok)
		has_support = S3TerrainSupport(env.terrain,
			cxn + env.curvature_anchor[0], cyn + env.curvature_anchor[1],
			&h_support);

	float contact = 0.0f;
	if (has_support && h <= h_support)
	{
		h = h_support;
		if (next->vel[2] < 0.0f)
			next->vel[2] = 0.0f;
		contact = 1.0f;
	}
	// water clamp (mirror of the flat swim floor)
	if (!input.fly && env.mount < 2 && h < next->water - 0.1f)
		h = next->water;

	// ── contact / jump bookkeeping (mirrors the flat pipeline's semantics) ──
	next->accum_contact += fmaxf(0.0f, contact);
	if (next->accum_contact > 5.0f)
		next->accum_contact = 5.0f;
	result->landed = (prev_contact < 1.0f && next->accum_contact >= 1.0f);
	if (next->accum_contact >= 1.0f && input.jump)
	{
		next->accum_contact = 0.0f;
		next->vel[2] = next->vel[2] < 0.0f ? 10.0f : next->vel[2] + 10.0f;
	}
	result->grounded = next->accum_contact >= 1.0f;
	next->accum_contact *= kS3ContactDecay;
	if (result->grounded && prev_contact < 1.0f && contact > 0.0f &&
	    next->accum_contact < 1.0f)
		next->accum_contact = 1.0f;

	// ── commit pose; chart output is import/debug/compat only ──────────────
	next->s3_ground[0] = g[0];
	next->s3_ground[1] = g[1];
	next->s3_ground[2] = g[2];
	next->s3_east[0] = e0[0];
	next->s3_east[1] = e0[1];
	next->s3_east[2] = e0[2];
	next->s3_valid = 1;
	if (chart_ok)
	{
		next->pos[0] = (float)(cxn + env.curvature_anchor[0]);
		next->pos[1] = (float)(cyn + env.curvature_anchor[1]);
	}
	next->pos[2] = h;
	if (blocked > 0.0f)
	{
		next->vel[0] = 0.0f;
		next->vel[1] = 0.0f;
	}
	next->slope = 0.0f;

	result->xy_vel = (float)speed * (blocked > 0.0f ? 0.0f : 1.0f);
	result->mat = next->mat;
	next->player_stp = (~(1 << 31)) &
		(next->player_stp + (int)(64.0f * result->xy_vel));
	result->next_stp = next->player_stp;
}

float MpStepS3Distance(const MpStepEnv& env, const float a[3], const float b[3])
{
	if (MpStepS3Owns(env) != 1)
	{
		float dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
		return sqrtf(dx * dx + dy * dy + dz * dz);
	}
	const double R = (double)SphericalCurvatureRadius(env.curvature_kappa);
	double Pa[4], Pb[4];
	curved_to_embedding(R, a[0] - env.curvature_anchor[0],
		a[1] - env.curvature_anchor[1],
		((double)a[2] - env.curvature_anchor[2]) / 16.0, Pa);
	curved_to_embedding(R, b[0] - env.curvature_anchor[0],
		b[1] - env.curvature_anchor[1],
		((double)b[2] - env.curvature_anchor[2]) / 16.0, Pb);
	return (float)(curved_arc_angle(Pa, Pb) * R);
}
