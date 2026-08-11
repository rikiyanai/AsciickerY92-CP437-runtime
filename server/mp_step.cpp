#include "mp_step.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

#include "matrix.h"
#include "facing_space.h"
#include "platform/time_backend.h"
#include "physics_tick.h"

static bool MpStepEnvFlag(const char* name)
{
#ifdef __EMSCRIPTEN__
	(void)name;
	return false;
#else
	const char* raw = getenv(name);
	return raw && raw[0] && raw[0] != '0';
#endif
}

// ============================================================================
// FL-4137 placed-block collision — 50+ attempt failure ledger (inline, do not delete)
// ----------------------------------------------------------------------------
// This file used to host a PARALLEL placed-block collision subsystem
// (MpPlacedBlockCollider / CollectPlacedBlocks / PushPlacedBlockTri+Quad /
// MpStepResolvePlacedBlockSupport W5 / PBLK magic mesh-id). Then it hosted
// the ECS/registry collision feed that was repeatedly green in recorder/static
// proof and repeatedly falsified by headed/manual proof. Future agents:
// re-introducing any of the deleted symbols is a regression; you are
// reproducing one of the failure modes already enumerated below.
//
// 2026-05-29 live count: 50 failed/unproven attempts per
// `python3 scripts/analyze_runs.py fl prior-attempts FL-4137`. The current
// status is BROKEN / implemented-unproven, not closed. Do not claim FL-4137
// is fixed until a headed browser/manual proof shows ALL of: target item_id
// placed, visible pixels for that block, side movement blocked without launch,
// stand-on support source == placed block item_id, explicit pickup works.
// The recurring root cause is proof/owner drift: code proved coordinates,
// seeded state, or any block while the user observed no visible block,
// clipping, side launch, or non-stand-on behavior in the actual headed game.
//
// Each attempt: claim → actual failure.
//
// #1  Initial branch placement/input/render/collision: claimed fixed because
//     server intent and local proof passed. Failed because collision/stand-on
//     was not headed-proven.
// #2  Asset import: claimed progress because XP assets existed. Failed for
//     gameplay because assets do not prove physics.
// #3  Gap C stacking: claimed fixed because server placement Z stacked in
//     server_tick. Failed because placement math is not movement collision.
// #4  Gap B preview validity: claimed fixed because preview mirrored server
//     rules. Failed because preview UI does not prove block collision.
// #5  Gap B review fix: claimed fixed because mirror order/filter bugs were
//     fixed. Still irrelevant to stand-on.
// #6  Gap A mobile preview tap: claimed fixed because input routed to server
//     intent. Did not prove placed block physics.
// #7  Gap A gate fix: claimed fixed because invalid preview could not be
//     tapped. Still UI-only.
// #8  Gap D diagnostic shadow: claimed fixed because diagnostic replay saw
//     colliders. Failed because it added/kept a parallel client-side collider
//     feed and did not affect live gameplay.
// #9  Gap D build-surface/tuning: claimed fixed because web/native builds and
//     static proof passed, plus block height/snap tuning changed. Failed
//     because it still tuned the parallel placed-block lane.
// #10 Seeded block flags: claimed fixed because seeded blocks became
//     PLACED|COLLIDABLE. Failed because flags only admit blocks into the
//     wrong collider path.
// #11 Height 1→16: claimed fixed because block top height matched visual
//     cube. Failed because correct dimensions do not fix collision ownership.
// #12 Side-face diagnosis/tuning: claimed fixed because side faces were
//     adjusted for climbability. Failed because tuning custom quads is still
//     not AKM/world collision.
// #13 Manual regression log: correctly logged failure. Not a fix.
// #14 Terrain-clamp root-cause spec: correctly identified one old
//     terrain-only owner. Incomplete because it did not delete the
//     placed-block parallel owner.
// #15 a6a722428 unified support owner: claimed fixed because terrain-only
//     post-step owners were deleted and source proofs passed. Failed because
//     CollectPlacedBlocks, MpPlacedBlockCollider, W5
//     MpStepResolvePlacedBlockSupport, and shadow colliders survived.
// #16 Recipe-controller fix: claimed fixed because controller reported side
//     collision/stand-on. Failed because recipe proof was too indirect and
//     headed manual contradicted it.
// #17 CE review fixes: claimed fixed because water provenance and proof gaps
//     were addressed. Failed because provenance cleanup did not remove the
//     placed-block physics lane.
// #18 Local browser/server proof: claimed fixed because Playwright returned
//     ok=true. Failed because headed visible gameplay showed clipping and
//     failed climb.
// #19 Headed observation: correctly logged failure. Not a fix.
// #20 Headed proof falsification: correctly logged that a6a722428 was
//     falsified. Exposed the real issue: static gates were green while
//     gameplay was broken.
//
// #21 Collapse the parallel subsystem. Placed blocks became World mesh
//     instances via World::AddInst at place time (server) and via
//     authoritative-item-state-driven CreateInst (client). This deleted the
//     old MpPlacedBlockCollider lane, but the first headed proof was still not
//     a valid stand-on proof.
// #22 Headed proof harness bind/permission repair: claimed fixed because the
//     local headed run reached gameplay. Not a collision fix.
// #23 Mesh-route source proof: claimed correct because placed blocks entered
//     QueryWorld -> MeshCollect. Still insufficient; "mesh exists in soup" is
//     not proof that the sweep/support result lands on the block top.
// #24 False-green proof predicate: headed run reported ok=true while the
//     player was launched to z=160.599 over a block with expected top about
//     z=73. The proof accepted "higher than block base" instead of "near block
//     top." Any future FL-4137 proof must reject high-Z launches and then trace
//     the actual MeshCollect/support path.
//
// #25 ECS REFRAME (operator-directed 2026-05-28). Headed proof falsified 5
//     ways: (a) cannot stand on placed block, (b) game crashed, (c) yellow
//     halo (collision proxy mesh rendering because INST_VISIBLE), (d) cannot
//     pick up, (e) flying-and-dropping on top still fails. Root: 24 attempts
//     treated the block as a special case threaded through parallel
//     subsystems. Correct model: one server-owned Entity carrying
//     Transform/ItemInstance/Renderable/CollisionBody/SupportSurface/
//     Interactable/Placeable components. mp_step reads CollisionBody only,
//     does not care if the source was building/block/tree/prop. Tier 1 plan:
//     ServerWorldEntityRegistry holds placed blocks as component records;
//     legacy_yy_block_collision.akm mesh proxy is DELETED; soup collector
//     consults registry alongside QueryWorld->MeshCollect. Tier 2 plan:
//     register AKM mesh insts as entities at load time, delete the
//     old story-id range hack, single component-feed.
//     Re-tuning the old proxy path is a regression. See FL-4137
//     fix-attempt 2026-05-28 for full file-by-file deletion/insertion plan.
// #26 Instrumentation-first run: finally measured MpSoupCollector live.
//     Proved entity boxes entered the cull/soup frame and block top math was
//     internally consistent. Failed because a side approach launched the player
//     from terrain Z to high Z; the proof had not modeled the headed failure.
// #27 Two-box emission: split placed block into body support_top=0 and top
//     slab support_top=1. Automated proof passed. Failed in headed/operator
//     view because visual clipping/no-real-stand-on still persisted.
// #28 Geometry contract: tried to make collision/support/visual tops agree.
//     Failed because proof still checked numeric geometry without proving the
//     visible block/player pixels in headed browser.
// #29 Catalog height/proof constant cleanup: removed stale hardcoded proof
//     height and experimented with block height. Failed because changing only
//     catalog/collision numbers made wall/step semantics wrong and still did
//     not prove the visible sprite.
// #30 Placement/render sinking discussion: attempted to reconcile visible
//     sprite and climbable top by moving/sinking placement. Failed because
//     placement Z/render Z/collision height edits are parallel owners unless
//     derived from one geometry contract.
// #31 Sprite-derived geometry bridge: added PlacedBlockGeometry from XP
//     projection and removed hand-typed block height/radius from catalog row.
//     Found another false-green: proof accepted any seeded block instead of
//     the target item_id. Failed as a proof, not as closure.
// #32 Target-id headed proof: bound pickup/equip/place/stand/pickup to the
//     same item_id and proved ITEM_ACTION_REQ_PLACE reached the server. Failed
//     headed because side approach still produced transient launch/clipping;
//     the top support slab was also still entering the sweep collision loop.
// #33 Operator headed falsification of the ECS reframe: still clipped through
//     sides and could not stand on top. Failed because registry ownership did
//     not yet prove the actual movement path.
// #34 Process/logging failure: no code change moved the failure; the same
//     headed complaint remained true.
// #35 Seed/proof hardening failed manually: proof rows were cleaner but the
//     headed game still did not block/stand correctly.
// #36 FL4137_DIAG proved the side-launch mechanism: a single fat support box
//     let side approach snap the player to the block top/high Z.
// #37 Two-box emission passed automation: body support_top=0 plus top slab
//     support_top=1. Failed headed because visual/collision truth was still not
//     proven by pixels and the launch class was only hidden at end-state.
// #38 Operator visual falsified the two-box pass: visible block/player behavior
//     still did not match the automated coordinate claim.
// #39 Manual visual retest after two-box physics fix still failed; proof had
//     not sampled the real headed failure surface.
// #40 Render-bounds recorder probe proved visual/collision mismatch, but still
//     did not prove actual rendered pixels.
// #41 Render-Z band-aid falsified: changing render placement alone violated
//     the "visible top == support/collision top" invariant.
// #42 Catalog height 176/revert attempt made block a wall/changed semantics;
//     stand-on proof choreography no longer matched the intended block.
// #43 Placement-Z offset tried to sink/lower the block. Failed as another
//     placement-owner band-aid.
// #44 Operator falsified the placement-lower attempt: block visibility/physics
//     still did not satisfy headed gameplay.
// #45 Proof hygiene failure: proof_placeable_block_items.js still had a
//     hardcoded block height and was a parallel geometry owner.
// #46 Operator invariant correction: there is no separate "climbable top";
//     visible top, support top, and collision top must be the same top.
// #47 Geometry-contract bridge landed: removed proof-owned height constant and
//     exported geometry fields. Failed because geometry numbers are not pixel
//     proof and runtime join was still partly blocked by FL-4159.
// #48 After FL-4159, geometry-contract proof reached gameplay and passed once.
//     Still unproven by Law 16 and still lacked semantic visible-block pixels.
// #49 Addability/support-source hardening tightened target item_id/support
//     assertions. Failed because it still did not catch all transient launch
//     windows until per-tick checks were added.
// #50 4bfc91989 validation after forced rebuild/headed run FAILED:
//     `.run/fl4137_validate_4bfc91989_headed.log` exits 1. Immediately after
//     ITEM_ACTION_REQ_PLACE, player z jumps from 55.581 to 206.692/219.005
//     while the target block is at (-2.5,-78.5,57) with visual/collision top
//     69.317. support_top boxes are skipped in the sweep, so the next attempt
//     must identify the exact launch owner: body-box sweep hit, terrain/AKM
//     soup hit, or support/floor recovery gate. No `block_visible` pixel gate
//     exists yet; auth_visible_world_item_ids is only "server says render this."
// #51 PROCESS FAILURE + cross-FL regression (2026-05-29, FL-4128 affected):
//     commit 4bfc91989 ("chore(fl-4137): log 32 failed block attempts") landed
//     `if (box.support_top) continue;` UNCONDITIONALLY in the sweep box loop
//     while operator-facing message framed the change as a "support-only slab"
//     skip. "Support-only slab" is NOT a category the code carries — every
//     upward-facing AKM face becomes a support_top=1 box (see FaceCollect
//     at ~line 1080: `box.support_top = item.nrm[2] > 0.25f ? 1 : 0;`).
//     Buildings and placed blocks both arrive via QueryWorld -> MeshCollect
//     (FL-4128 attempt #2 explicitly warned this exact failure mode would
//     recur). Result: building roofs / eaves / chamfered tops fell out of the
//     sweep — operator observed "buildings not colliding" without any code
//     change targeting FL-4128. The scoped fix below adds an explicit
//     support_only box bit set only by the placed-block top-slab emission.
//     Do not infer this from source ids; that repeats the same overloaded-field
//     failure. Future agents: NEVER rewiden this filter without re-proving
//     FL-4137 side-approach no-launch AND FL-4128 building wall block on the
//     same run.
// ============================================================================

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// These are defined in engine/world_core.cpp and maintained by World::Query().
// Used for observability only (no behavior changes).
extern int bsp_tests;
extern int bsp_insts;
extern int bsp_nodes;

namespace
{
static constexpr float kMpBaseXYSpeed = 0.1495f;
static constexpr float kMpGroundDecay = 0.9f;
static constexpr float kMpGroundContactDecay = 0.9f;
static constexpr float kMpMaxVerticalVel = 20.0f;
static constexpr float kMpMaxImplicitStepUp = HEIGHT_SCALE * 1.5f;
// FL-641 / FL-1639: this snap epsilon keeps support/floor recovery alive on low-motion
// frames. Do not collapse it into a pure "active input" gate; idle-frame Z recovery still
// matters for under-support correction and for keeping remote interpolation from rendering
// one-tick authoritative Z drift as real movement.
static constexpr float kMpSupportSnapEpsilon = 0.25f;
static constexpr float kMpSupportContactSlop = 0.02f;
static constexpr float kMpStepAssistMinRise = 0.05f;
// Collision boxes are tested in the same scaled ellipsoid space as the sweep,
// where the player body radius is 1.0. Support probes must use that footprint
// too; checking only the actor center against the raw top rectangle produces
// the FL-4137 headed failure where the side blocks/deflects but the top cannot
// be climbed because the center is still just outside the cube.
static constexpr float kMpCollisionBoxSupportMargin = 1.02f;
static constexpr float kMpMountHeightCells = 9.0f;
static constexpr float kMpBaseHeightCells = 7.0f;
static constexpr float kMpMountRadiusCells = 3.0f;
static constexpr float kMpBaseRadiusCells = 2.0f;
static constexpr float kMpFallVisualVelZ = -1.0f;
static constexpr float kMpCollisionDenomEpsilon = 1e-6f;

static uint32_t MpDebugHashString(const char* text)
{
	uint32_t hash = 2166136261u;
	if (!text)
		return 0;
	while (*text)
	{
		hash ^= (uint8_t)*text++;
		hash *= 16777619u;
	}
	return hash;
}

static uint64_t MpFaceCollectDiagTime()
{
#ifdef FL2957_DIAG_LIGHT_FACE_TIMING
	return 0;
#else
	return a3dGetTime();
#endif
}

static uint64_t MpSweepDiagTime()
{
#ifdef FL2957_DIAG_LIGHT_SWEEP_TIMING
	return 0;
#else
	return a3dGetTime();
#endif
}

static int32_t MpDebugMilli(double value)
{
	if (!isfinite(value))
		return 0;
	if (value > 2147483.0)
		return INT32_MAX;
	if (value < -2147483.0)
		return INT32_MIN;
	return (int32_t)(value * 1000.0);
}

static uint32_t MpDebugMilliU(double value)
{
	if (!isfinite(value) || value <= 0.0)
		return 0;
	if (value > 4294967.0)
		return UINT32_MAX;
	return (uint32_t)(value * 1000.0);
}


	struct MpSoupItem
	{
	float tri[3][3];
	int material;
	float nrm[4];
	float bbox_min[3];
	float bbox_max[3];
	uint64_t source_inst_id;
	uint64_t source_mesh_id;
	uint32_t source_mesh_faces;
	uint32_t source_face_ordinal;
	int32_t source_inst_story_id;
	uint32_t source_inst_flags;
	uint32_t source_inst_name_hash;
	uint32_t source_mesh_name_hash;

	void UpdateBBox()
	{
		for (int axis = 0; axis < 3; axis++)
		{
			bbox_min[axis] = fminf(tri[0][axis], fminf(tri[1][axis], tri[2][axis]));
			bbox_max[axis] = fmaxf(tri[0][axis], fmaxf(tri[1][axis], tri[2][axis]));
		}
	}

	float CheckCollision(const float sphere_pos[3], const float sphere_vel[3], float contact_pos[3]) const
	{
		float plane_nrm[4] = { nrm[0], nrm[1], nrm[2], nrm[3] };
		float vel_dot_nrm = -DotProduct(sphere_vel, plane_nrm);
		float plane_side = 1.0f;
		if (vel_dot_nrm <= 0.0f)
		{
			plane_nrm[0] = -plane_nrm[0];
			plane_nrm[1] = -plane_nrm[1];
			plane_nrm[2] = -plane_nrm[2];
			plane_nrm[3] = -plane_nrm[3];
			vel_dot_nrm = -DotProduct(sphere_vel, plane_nrm);
			plane_side = -1.0f;
		}

		float col[3] =
		{
			sphere_pos[0] - plane_nrm[0],
			sphere_pos[1] - plane_nrm[1],
			sphere_pos[2] - plane_nrm[2]
		};

		float plane_t = 2.0f;

		if (vel_dot_nrm > 0.0f)
		{
			float dist = DotProduct(col, plane_nrm) + plane_nrm[3];

			if (dist > 0.0f)
			{
				plane_t = dist / vel_dot_nrm;
			}
			else if (dist > -1.0f)
			{
				dist = 1.0f + dist;
				contact_pos[0] = col[0] - dist * plane_nrm[0];
				contact_pos[1] = col[1] - dist * plane_nrm[1];
				contact_pos[2] = col[2] - dist * plane_nrm[2];
				plane_t = 0.0f;
			}
			else
			{
				return 2.0f;
			}

			contact_pos[0] = col[0] + plane_t * sphere_vel[0];
			contact_pos[1] = col[1] + plane_t * sphere_vel[1];
			contact_pos[2] = col[2] + plane_t * sphere_vel[2];

			float edge[3][3] =
			{
				{tri[1][0] - tri[0][0], tri[1][1] - tri[0][1], tri[1][2] - tri[0][2]},
				{tri[2][0] - tri[1][0], tri[2][1] - tri[1][1], tri[2][2] - tri[1][2]},
				{tri[0][0] - tri[2][0], tri[0][1] - tri[2][1], tri[0][2] - tri[2][2]}
			};

			float vect[3][3] =
			{
				{ contact_pos[0] - tri[0][0], contact_pos[1] - tri[0][1], contact_pos[2] - tri[0][2]},
				{ contact_pos[0] - tri[1][0], contact_pos[1] - tri[1][1], contact_pos[2] - tri[1][2]},
				{ contact_pos[0] - tri[2][0], contact_pos[1] - tri[2][1], contact_pos[2] - tri[2][2]},
			};

			float cross[3][3];
			float dot[3];

			CrossProduct(edge[0], vect[0], cross[0]);
			dot[0] = DotProduct(cross[0], nrm) * plane_side;
			CrossProduct(edge[1], vect[1], cross[1]);
			dot[1] = DotProduct(cross[1], nrm) * plane_side;
			CrossProduct(edge[2], vect[2], cross[2]);
			dot[2] = DotProduct(cross[2], nrm) * plane_side;

			if (dot[0] >= 0.0f && dot[1] >= 0.0f && dot[2] >= 0.0f)
				return plane_t > 1.0f ? 2.0f : plane_t;

			plane_t = 2.0f;

			float A = DotProduct(sphere_vel, sphere_vel);
			for (int s = 0; s < 3; s++)
			{
				float p_ps[3] =
				{
					sphere_pos[0] - tri[s][0],
					sphere_pos[1] - tri[s][1],
					sphere_pos[2] - tri[s][2]
				};

				float B = 2.0f * DotProduct(p_ps, sphere_vel);
				float C = DotProduct(p_ps, p_ps) - 1.0f;
				float D = B * B - 4.0f * A * C;
				if (!isfinite(A) || A <= kMpCollisionDenomEpsilon || !isfinite(D) || D < 0.0f)
					continue;

				float t = (-B - sqrtf(D)) / (2.0f * A);
				if (!isfinite(t))
					continue;
				if (t >= 0.0f && t <= 1.0f && t < plane_t)
				{
					plane_t = t;
					contact_pos[0] = tri[s][0];
					contact_pos[1] = tri[s][1];
					contact_pos[2] = tri[s][2];
				}
			}

			for (int c = 0; c < 3; c++)
			{
				float vcvc = DotProduct(edge[c], edge[c]);
				float p_pc[3] =
				{
					sphere_pos[0] - tri[c][0],
					sphere_pos[1] - tri[c][1],
					sphere_pos[2] - tri[c][2]
				};

				float vc_dot_p_pc = DotProduct(edge[c], p_pc);
				float U[3] =
				{
					p_pc[0] * vcvc - edge[c][0] * vc_dot_p_pc,
					p_pc[1] * vcvc - edge[c][1] * vc_dot_p_pc,
					p_pc[2] * vcvc - edge[c][2] * vc_dot_p_pc
				};

				float vc_dot_v = DotProduct(edge[c], sphere_vel);
				float V[3] =
				{
					sphere_vel[0] * vcvc - edge[c][0] * vc_dot_v,
					sphere_vel[1] * vcvc - edge[c][1] * vc_dot_v,
					sphere_vel[2] * vcvc - edge[c][2] * vc_dot_v
				};

				float edge_a = DotProduct(V, V);
				float edge_b = 2.0f * DotProduct(U, V);
				float edge_c = DotProduct(U, U) - vcvc * vcvc;
				float edge_d = edge_b * edge_b - 4.0f * edge_a * edge_c;
				if (!isfinite(vcvc) || vcvc <= kMpCollisionDenomEpsilon ||
					!isfinite(edge_a) || edge_a <= kMpCollisionDenomEpsilon ||
					!isfinite(edge_d) || edge_d < 0.0f)
					continue;

				float t = (-edge_b - sqrtf(edge_d)) / (2.0f * edge_a);
				if (!isfinite(t))
					continue;
				if (t < 0.0f || t > 1.0f || t >= plane_t)
					continue;

				float pc[3] =
				{
					sphere_pos[0] + t * sphere_vel[0] - tri[c][0],
					sphere_pos[1] + t * sphere_vel[1] - tri[c][1],
					sphere_pos[2] + t * sphere_vel[2] - tri[c][2]
				};
				float h_mul_vc = DotProduct(pc, edge[c]);
				if (!isfinite(h_mul_vc))
					continue;
				if (h_mul_vc < 0.0f || h_mul_vc > vcvc)
					continue;

				float h_div_vc = h_mul_vc / vcvc;
				if (!isfinite(h_div_vc))
					continue;
				plane_t = t;
				contact_pos[0] = tri[c][0] + edge[c][0] * h_div_vc;
				contact_pos[1] = tri[c][1] + edge[c][1] * h_div_vc;
				contact_pos[2] = tri[c][2] + edge[c][2] * h_div_vc;
			}
		}

		return plane_t;
	}

	bool RejectSweptSphereAABB(const float sphere_pos[3], const float sphere_vel[3]) const
	{
		constexpr float sphere_radius = 1.0f;
		for (int axis = 0; axis < 3; axis++)
		{
			const float sweep_min = fminf(sphere_pos[axis], sphere_pos[axis] + sphere_vel[axis]) - sphere_radius;
			const float sweep_max = fmaxf(sphere_pos[axis], sphere_pos[axis] + sphere_vel[axis]) + sphere_radius;
			if (sweep_max < bbox_min[axis] || sweep_min > bbox_max[axis])
				return true;
		}
		return false;
		}
	};

	struct MpCollisionBox
	{
		float bmin[3];
		float bmax[3];
		int material;
		uint8_t support_top;
		uint8_t support_only;
		uint64_t source_inst_id;
		uint64_t source_mesh_id;
		uint32_t source_mesh_faces;
		uint32_t source_face_ordinal;
		int32_t source_inst_story_id;
		uint32_t source_inst_flags;
		uint32_t source_inst_name_hash;
		uint32_t source_mesh_name_hash;
		uint64_t source_entity_id;
		uint16_t source_item_id;

		bool RejectSweptSphereAABB(const float sphere_pos[3], const float sphere_vel[3]) const
		{
			constexpr float sphere_radius = 1.0f;
			for (int axis = 0; axis < 3; axis++)
			{
				const float sweep_min = fminf(sphere_pos[axis], sphere_pos[axis] + sphere_vel[axis]) - sphere_radius;
				const float sweep_max = fmaxf(sphere_pos[axis], sphere_pos[axis] + sphere_vel[axis]) + sphere_radius;
				if (sweep_max < bmin[axis] || sweep_min > bmax[axis])
					return true;
			}
			return false;
		}

		float CheckCollision(const float sphere_pos[3], const float sphere_vel[3], float contact_pos[3]) const
		{
			constexpr float sphere_radius = 1.0f;
			float enter_t = -3.4e38f;
			float exit_t = 3.4e38f;
			float enter_normal[3] = { 0.0f, 0.0f, 0.0f };

			for (int axis = 0; axis < 3; axis++)
			{
				const float mn = bmin[axis] - sphere_radius;
				const float mx = bmax[axis] + sphere_radius;
				const float p = sphere_pos[axis];
				const float v = sphere_vel[axis];
				if (fabsf(v) <= kMpCollisionDenomEpsilon)
				{
					if (p < mn || p > mx)
						return 2.0f;
					continue;
				}

				float t1 = (mn - p) / v;
				float t2 = (mx - p) / v;
				float n = v > 0.0f ? -1.0f : 1.0f;
				if (t1 > t2)
				{
					const float tmp = t1;
					t1 = t2;
					t2 = tmp;
					n = -n;
				}
				if (t1 > enter_t)
				{
					enter_t = t1;
					enter_normal[0] = 0.0f;
					enter_normal[1] = 0.0f;
					enter_normal[2] = 0.0f;
					enter_normal[axis] = n;
				}
				if (t2 < exit_t)
					exit_t = t2;
				if (enter_t > exit_t)
					return 2.0f;
			}

			if (exit_t < 0.0f || enter_t > 1.0f)
				return 2.0f;

			if (enter_t < 0.0f)
			{
				float best_depth = 3.4e38f;
				int best_axis = 0;
				float best_sign = 1.0f;
				for (int axis = 0; axis < 3; axis++)
				{
					const float mn = bmin[axis] - sphere_radius;
					const float mx = bmax[axis] + sphere_radius;
					const float to_min = fabsf(sphere_pos[axis] - mn);
					const float to_max = fabsf(mx - sphere_pos[axis]);
					if (to_min < best_depth)
					{
						best_depth = to_min;
						best_axis = axis;
						best_sign = -1.0f;
					}
					if (to_max < best_depth)
					{
						best_depth = to_max;
						best_axis = axis;
						best_sign = 1.0f;
					}
				}
				enter_t = 0.0f;
				enter_normal[0] = 0.0f;
				enter_normal[1] = 0.0f;
				enter_normal[2] = 0.0f;
				enter_normal[best_axis] = best_sign;
			}

			const float hit[3] =
			{
				sphere_pos[0] + sphere_vel[0] * enter_t,
				sphere_pos[1] + sphere_vel[1] * enter_t,
				sphere_pos[2] + sphere_vel[2] * enter_t,
			};
			contact_pos[0] = hit[0] - enter_normal[0] * sphere_radius;
			contact_pos[1] = hit[1] - enter_normal[1] * sphere_radius;
			contact_pos[2] = hit[2] - enter_normal[2] * sphere_radius;
			return enter_t;
		}

		bool ContainsSupportXY(float x, float y) const
		{
			return x >= bmin[0] - kMpCollisionBoxSupportMargin &&
				x <= bmax[0] + kMpCollisionBoxSupportMargin &&
				y >= bmin[1] - kMpCollisionBoxSupportMargin &&
				y <= bmax[1] + kMpCollisionBoxSupportMargin;
		}
	};

// FL-4137 #21/#24: MP_SOUP_SOURCE_MESH_PLACED_BLOCK ("PBLK" magic
// 0x50424c4b) and the PBLK branch in MpSupportHitForSoupItem DELETED. The
// PBLK sentinel was a parallel mesh-id namespace that bypassed the real World
// inst / Mesh* pointer carried by ordinary AKM soup items. Placed blocks now
// travel the same QueryWorld -> MeshCollect path as AKM building faces, but
// attempt #24 proved this attribution alone is not closure: a headed proof
// accepted z=160.599 over a block whose top was about z=73. This support hit
// can identify the winning face; closure still requires headed evidence that
// support settles near the block top and side collision blocks entry.
//
// FL-4137 #25 ECS REFRAME (2026-05-28): the story_id range discrimination
// below is a parallel-owner band-aid for the absence of a component model.
// Tier 2 plan: replace with WorldEntityRegistry::LookupByInst() returning a
// Component view (CollisionBody, SupportSurface, ItemInstance.catalog_id);
// MpSupportSource is then derived from the component data, not from a magic
// integer range. The story_id branch stays as a stub during Tier 1 only.
	static MpSupportHit MpSupportHitForSoupItem(const MpSoupItem& item, float z)
	{
	MpSupportHit support = {};
	support.found = 1;
	support.z = z;
	support.world_inst_id = item.source_inst_id;
	support.world_mesh_id = item.source_mesh_id;
	if (item.source_inst_id || item.source_mesh_id)
	{
		support.source = MP_SUPPORT_WORLD_MESH;
	}
	else
	{
		support.source = MP_SUPPORT_TERRAIN;
	}
		return support;
	}

	static MpSupportHit MpSupportHitForCollisionBox(const MpCollisionBox& box, float z)
	{
		MpSupportHit support = {};
		support.found = 1;
		support.z = z;
		support.world_inst_id = box.source_inst_id;
		support.world_mesh_id = box.source_mesh_id;
		if (box.source_item_id != 0)
		{
			support.source = MP_SUPPORT_PLACED_BLOCK;
			support.placed_item_id = box.source_item_id;
			support.world_inst_id = box.source_entity_id;
		}
		else if (box.source_inst_id || box.source_mesh_id)
		{
			support.source = MP_SUPPORT_WORLD_MESH;
		}
		else
		{
			support.source = MP_SUPPORT_TERRAIN;
		}
		return support;
	}

struct MpMeshSoupCacheEntry
{
	Inst* inst = nullptr;
	Mesh* mesh = nullptr;
	uint32_t inst_name_hash = 0;
	uint32_t mesh_name_hash = 0;
	uint32_t faces_reported = 0;
	uint32_t inst_flags = 0;
	int32_t inst_story_id = 0;
	float collect_mul_xy = 1.0f;
	float collect_mul_z = 1.0f;
	double tm[16] = {};
		double bbox[6] = {};
		float max_height = -3.4e38f;
		std::vector<MpSoupItem> items;
		std::vector<MpCollisionBox> boxes;
	};

static std::vector<MpMeshSoupCacheEntry> g_mp_mesh_soup_cache;

static bool MpMeshSoupCacheSameDouble(double a, double b)
{
	const double d = a - b;
	return d > -0.000000001 && d < 0.000000001;
}

static bool MpMeshSoupCacheSameFloat(float a, float b)
{
	const float d = a - b;
	return d > -0.000001f && d < 0.000001f;
}

static bool MpMeshSoupCacheMatch(
	const MpMeshSoupCacheEntry& entry,
	Inst* inst,
	Mesh* mesh,
	const double tm[16],
	const double bbox[6],
	float collect_mul_xy,
	float collect_mul_z,
	uint32_t faces_reported,
	uint32_t inst_name_hash,
	uint32_t mesh_name_hash,
	uint32_t inst_flags,
		int32_t inst_story_id)
{
	if (entry.inst != inst || entry.mesh != mesh ||
		entry.faces_reported != faces_reported ||
		entry.inst_name_hash != inst_name_hash ||
		entry.mesh_name_hash != mesh_name_hash ||
		entry.inst_flags != inst_flags ||
		entry.inst_story_id != inst_story_id ||
		!MpMeshSoupCacheSameFloat(entry.collect_mul_xy, collect_mul_xy) ||
		!MpMeshSoupCacheSameFloat(entry.collect_mul_z, collect_mul_z))
		return false;
	for (int n = 0; n < 16; n++)
	{
		if (!MpMeshSoupCacheSameDouble(entry.tm[n], tm[n]))
			return false;
	}
	for (int n = 0; n < 6; n++)
	{
		if (!MpMeshSoupCacheSameDouble(entry.bbox[n], bbox[n]))
			return false;
	}
	return true;
}

static const MpMeshSoupCacheEntry* MpFindMeshSoupCache(
	Inst* inst,
	Mesh* mesh,
	const double tm[16],
	const double bbox[6],
	float collect_mul_xy,
	float collect_mul_z,
	uint32_t faces_reported,
	uint32_t inst_name_hash,
	uint32_t mesh_name_hash,
	uint32_t inst_flags,
	int32_t inst_story_id)
{
	for (const MpMeshSoupCacheEntry& entry : g_mp_mesh_soup_cache)
	{
		if (MpMeshSoupCacheMatch(entry, inst, mesh, tm, bbox, collect_mul_xy, collect_mul_z,
				faces_reported, inst_name_hash, mesh_name_hash, inst_flags, inst_story_id))
			return &entry;
	}
	return nullptr;
}

	static void MpStoreMeshSoupCache(
	Inst* inst,
	Mesh* mesh,
	const double tm[16],
	const double bbox[6],
	float collect_mul_xy,
	float collect_mul_z,
	uint32_t faces_reported,
	uint32_t inst_name_hash,
	uint32_t mesh_name_hash,
	uint32_t inst_flags,
	int32_t inst_story_id,
		const std::vector<MpSoupItem>& soup,
		size_t begin,
		size_t end,
		const std::vector<MpCollisionBox>& boxes,
		size_t box_begin,
		size_t box_end)
	{
		if (!inst || !mesh || end < begin || box_end < box_begin)
			return;
	MpMeshSoupCacheEntry entry;
	entry.inst = inst;
	entry.mesh = mesh;
	entry.inst_name_hash = inst_name_hash;
	entry.mesh_name_hash = mesh_name_hash;
	entry.faces_reported = faces_reported;
	entry.inst_flags = inst_flags;
	entry.inst_story_id = inst_story_id;
	entry.collect_mul_xy = collect_mul_xy;
	entry.collect_mul_z = collect_mul_z;
	memcpy(entry.tm, tm, sizeof(entry.tm));
	memcpy(entry.bbox, bbox, sizeof(entry.bbox));
		entry.items.assign(
			soup.begin() + (std::vector<MpSoupItem>::difference_type)begin,
			soup.begin() + (std::vector<MpSoupItem>::difference_type)end);
		entry.boxes.assign(
			boxes.begin() + (std::vector<MpCollisionBox>::difference_type)box_begin,
			boxes.begin() + (std::vector<MpCollisionBox>::difference_type)box_end);
	for (const MpSoupItem& item : entry.items)
	{
		for (int n = 0; n < 3; n++)
		{
			const float world_z = collect_mul_z != 0.0f ? item.tri[n][2] / collect_mul_z : item.tri[n][2];
			entry.max_height = fmaxf(entry.max_height, world_z);
		}
	}
	g_mp_mesh_soup_cache.push_back(entry);
}

		struct MpSoupCollector
		{
			std::vector<MpSoupItem> soup;
			std::vector<MpCollisionBox> boxes;
			double* collect_tm = nullptr;
		float collect_mul_xy = 1.0f;
		float collect_mul_z = 1.0f;
		float max_height = 0.0f;
		uint32_t collect_world_us = 0;
		uint32_t collect_terrain_us = 0;
		uint32_t collect_total_us = 0;
		uint32_t collect_mesh_us = 0;
		uint32_t mesh_instances = 0;
		uint32_t mesh_faces = 0;
		uint32_t world_callbacks = 0;
		uint32_t terrain_tris = 0;
		uint32_t mesh_per_mesh_cap_hits = 0;
		uint32_t mesh_bbox_skips = 0;
		uint32_t support_priority_callbacks = 0;

		// Mesh collection attribution. These do not change collection behavior; they
		// only explain why collect_mesh_us (time spent inside QueryMesh callbacks) can
		// intermittently explode while soup_items/mesh_faces remain stable.
		uint32_t mesh_query_us_total = 0;   // total wall time spent inside QueryMesh() calls
		uint32_t mesh_query_us_max = 0;     // max wall time for a single QueryMesh() call
		uint32_t mesh_query_overhead_us_total = 0; // sum(QueryMesh wall - sum(FaceCollect wall))
		uint32_t mesh_query_overhead_us_max = 0;   // max overhead for a single mesh QueryMesh()
		uint32_t mesh_face_cb_us_total = 0; // total time spent inside FaceCollect callback
		uint32_t mesh_face_cb_us_max = 0;   // max time spent inside a single FaceCollect callback
		uint32_t mesh_face_cb_calls = 0;    // number of FaceCollect invocations (accepted + rejected)
		uint32_t mesh_face_cb_accepts = 0;  // number of accepted faces (soup push_back)
		uint32_t mesh_face_cb_reject_visual = 0;
		uint32_t mesh_face_cb_reject_alpha = 0;
		uint32_t mesh_face_cb_accept_us_total = 0;
		uint32_t mesh_face_cb_accept_us_max = 0;
		uint32_t mesh_face_cb_push_us_total = 0;
		uint32_t mesh_face_cb_push_us_max = 0;
		uint32_t mesh_face_cb_material_us_total = 0;
		uint32_t mesh_face_cb_material_us_max = 0;
		uint32_t mesh_face_cb_transform_us_total = 0;
		uint32_t mesh_face_cb_transform_us_max = 0;
		uint32_t mesh_face_cb_normal_us_total = 0;
		uint32_t mesh_face_cb_normal_us_max = 0;
		uint32_t mesh_face_cb_bbox_us_total = 0;
		uint32_t mesh_face_cb_bbox_us_max = 0;
		uint32_t mesh_faces_reported_total = 0; // sum(GetMeshFaces(m)) for meshes visited
		uint32_t mesh_faces_reported_max = 0;   // max GetMeshFaces(m) seen on any mesh instance
		uint64_t current_inst_id = 0;
		uint64_t current_mesh_id = 0;
		uint32_t current_mesh_faces_reported = 0;
		uint32_t current_mesh_face_ordinal = 0;
		int32_t current_inst_story_id = 0;
		uint32_t current_inst_flags = 0;
		uint32_t current_inst_name_hash = 0;
		uint32_t current_mesh_name_hash = 0;
		int32_t current_inst_bbox_cx_milli = 0;
		int32_t current_inst_bbox_cy_milli = 0;
		int32_t current_inst_bbox_cz_milli = 0;
		uint32_t current_inst_bbox_diag_milli = 0;
		uint32_t current_query_bbox_dist_milli = 0;
		int32_t current_query_bbox_overlap_milli = 0;
		uint64_t mesh_face_cb_us_max_inst_id = 0;
		uint64_t mesh_face_cb_us_max_mesh_id = 0;
		uint32_t mesh_face_cb_us_max_mesh_faces = 0;
		uint32_t mesh_face_cb_us_max_face_ordinal = 0;
		uint32_t mesh_face_cb_us_max_accept = 0;
		uint32_t mesh_face_cb_us_max_visual = 0;
		uint32_t mesh_face_cb_us_max_soup_index = 0;
		int32_t mesh_face_cb_us_max_inst_story_id = 0;
		uint32_t mesh_face_cb_us_max_inst_flags = 0;
		uint32_t mesh_face_cb_us_max_inst_name_hash = 0;
		uint32_t mesh_face_cb_us_max_mesh_name_hash = 0;
		int32_t mesh_face_cb_us_max_inst_bbox_cx_milli = 0;
		int32_t mesh_face_cb_us_max_inst_bbox_cy_milli = 0;
		int32_t mesh_face_cb_us_max_inst_bbox_cz_milli = 0;
		uint32_t mesh_face_cb_us_max_inst_bbox_diag_milli = 0;
		int32_t mesh_face_cb_us_max_query_cx_milli = 0;
		int32_t mesh_face_cb_us_max_query_cy_milli = 0;
		uint32_t mesh_face_cb_us_max_query_radius_milli = 0;
		uint32_t mesh_face_cb_us_max_query_bbox_dist_milli = 0;
		int32_t mesh_face_cb_us_max_query_bbox_overlap_milli = 0;

		uint32_t soup_reallocs = 0;         // soup capacity growth events inside FaceCollect
		uint32_t soup_capacity_max = 0;     // max soup.capacity() observed during collection
		uint64_t soup_bytes_max = 0;        // max soup.capacity()*sizeof(MpSoupItem) observed
		uint64_t soup_bytes_growth_total = 0; // sum of byte growth on each capacity increase

		uint32_t bbox_would_skip = 0;       // number of mesh instances whose inst bbox doesn't overlap swept query (no skip applied)
		uint32_t bbox_would_skip_faces = 0; // sum of GetMeshFaces() for bbox_would_skip instances (estimate of avoidable work)
		uint32_t mesh_cache_hits = 0;
		uint32_t mesh_cache_misses = 0;
		uint32_t mesh_cache_items = 0;
		uint32_t world_bsp_tests = 0;       // snapshot of extern bsp_tests after QueryWorld
		uint32_t world_bsp_nodes = 0;       // snapshot of extern bsp_nodes after QueryWorld
		uint32_t world_bsp_insts = 0;       // snapshot of extern bsp_insts after QueryWorld
		float collect_center_x = 0.0f;
		float collect_center_y = 0.0f;
		float collect_radius = 0.0f;
		bool capped = false;

			static void FaceCollect(float coords[9], uint8_t* colors, uint32_t visual, void* cookie)
		{
			MpSoupCollector* collector = static_cast<MpSoupCollector*>(cookie);
			const uint64_t cb_start_us = MpFaceCollectDiagTime();

			// Always count callback time/calls, even if we reject the face, so we can
			// distinguish "QueryMesh overhead" from "FaceCollect overhead".
			const bool reject_visual = ((visual & (1u << 31)) != 0);
			const bool reject_alpha = !(colors[3] <= 128 && colors[7] <= 128 && colors[11] <= 128);
			const bool accept = !reject_visual && !reject_alpha;
			if (!accept)
			{
				if (reject_visual) collector->mesh_face_cb_reject_visual++;
				if (reject_alpha) collector->mesh_face_cb_reject_alpha++;
			}

			if (accept)
			{
				const uint64_t accept_start_us = MpFaceCollectDiagTime();
				collector->mesh_face_cb_accepts++;

				const uint64_t push_start_us = MpFaceCollectDiagTime();
				const size_t cap_before = collector->soup.capacity();
				collector->soup.push_back({});
				const uint32_t push_us = (uint32_t)(MpFaceCollectDiagTime() - push_start_us);
				collector->mesh_face_cb_push_us_total += push_us;
				if (push_us > collector->mesh_face_cb_push_us_max)
					collector->mesh_face_cb_push_us_max = push_us;
				if (collector->soup.capacity() != cap_before)
				{
					collector->soup_reallocs++;
					const uint64_t before_bytes = (uint64_t)cap_before * (uint64_t)sizeof(MpSoupItem);
					const uint64_t after_bytes = (uint64_t)collector->soup.capacity() * (uint64_t)sizeof(MpSoupItem);
					if (after_bytes > before_bytes)
						collector->soup_bytes_growth_total += (after_bytes - before_bytes);
				}
				if (collector->soup.capacity() > collector->soup_capacity_max)
					collector->soup_capacity_max = (uint32_t)collector->soup.capacity();
				const uint64_t soup_bytes = (uint64_t)collector->soup.capacity() * (uint64_t)sizeof(MpSoupItem);
				if (soup_bytes > collector->soup_bytes_max)
					collector->soup_bytes_max = soup_bytes;
				MpSoupItem& item = collector->soup.back();
				item.source_inst_id = collector->current_inst_id;
				item.source_mesh_id = collector->current_mesh_id;
				item.source_mesh_faces = collector->current_mesh_faces_reported;
				item.source_face_ordinal = collector->current_mesh_face_ordinal;
				item.source_inst_story_id = collector->current_inst_story_id;
				item.source_inst_flags = collector->current_inst_flags;
				item.source_inst_name_hash = collector->current_inst_name_hash;
				item.source_mesh_name_hash = collector->current_mesh_name_hash;

				const uint64_t material_start_us = MpFaceCollectDiagTime();
				int rgb[3] =
				{
					colors[0] + colors[4] + colors[8],
					colors[1] + colors[5] + colors[9],
					colors[2] + colors[6] + colors[10]
				};

				int sat = 0;
				int lum = 0;
				int mat = 3;
				if (rgb[1] >= rgb[2] && rgb[1] >= rgb[0])
				{
					lum = rgb[1];
					mat = 3;
					sat = (rgb[0] > rgb[2]) ? (rgb[0] - rgb[2]) : (rgb[2] - rgb[0]);
				}
				else if (rgb[0] >= rgb[1] && rgb[0] >= rgb[2])
				{
					lum = rgb[0];
					mat = 1;
					sat = (rgb[1] > rgb[2]) ? (rgb[1] - rgb[2]) : (rgb[2] - rgb[1]);
				}
				else
				{
					lum = rgb[2];
					mat = 1;
					sat = (rgb[0] > rgb[1]) ? (rgb[0] - rgb[1]) : (rgb[1] - rgb[0]);
				}
				if (sat * 10 < lum)
					mat = 0;
				item.material = mat;
				const uint32_t material_us = (uint32_t)(MpFaceCollectDiagTime() - material_start_us);
				collector->mesh_face_cb_material_us_total += material_us;
				if (material_us > collector->mesh_face_cb_material_us_max)
					collector->mesh_face_cb_material_us_max = material_us;

				const uint64_t transform_start_us = MpFaceCollectDiagTime();
				float v[3][4] =
				{
					{coords[0], coords[1], coords[2], 1.0f},
					{coords[3], coords[4], coords[5], 1.0f},
					{coords[6], coords[7], coords[8], 1.0f},
				};

				float tmv[4];
				for (int i = 0; i < 3; i++)
				{
					Product(collector->collect_tm, v[i], tmv);
					collector->max_height = fmaxf(tmv[2], collector->max_height);
					item.tri[i][0] = tmv[0] * collector->collect_mul_xy;
					item.tri[i][1] = tmv[1] * collector->collect_mul_xy;
					item.tri[i][2] = tmv[2] * collector->collect_mul_z;
				}
				const uint32_t transform_us = (uint32_t)(MpFaceCollectDiagTime() - transform_start_us);
				collector->mesh_face_cb_transform_us_total += transform_us;
				if (transform_us > collector->mesh_face_cb_transform_us_max)
					collector->mesh_face_cb_transform_us_max = transform_us;

				const uint64_t normal_start_us = MpFaceCollectDiagTime();
				float* tri[3] = { item.tri[0], item.tri[1], item.tri[2] };
				float e1[3] = { tri[0][0] - tri[2][0], tri[0][1] - tri[2][1], tri[0][2] - tri[2][2] };
				float e2[3] = { tri[1][0] - tri[2][0], tri[1][1] - tri[2][1], tri[1][2] - tri[2][2] };
				CrossProduct(e1, e2, item.nrm);
				float nrm = 1.0f / sqrtf(item.nrm[0] * item.nrm[0] +
					item.nrm[1] * item.nrm[1] +
					item.nrm[2] * item.nrm[2]);
				item.nrm[0] *= nrm;
				item.nrm[1] *= nrm;
				item.nrm[2] *= nrm;
				item.nrm[3] = -(tri[2][0] * item.nrm[0] + tri[2][1] * item.nrm[1] + tri[2][2] * item.nrm[2]);
				const uint32_t normal_us = (uint32_t)(MpFaceCollectDiagTime() - normal_start_us);
				collector->mesh_face_cb_normal_us_total += normal_us;
				if (normal_us > collector->mesh_face_cb_normal_us_max)
					collector->mesh_face_cb_normal_us_max = normal_us;

#ifdef FL2957_DIAG_SKIP_ITEM_BBOX_UPDATE
				const uint32_t bbox_us = 0;
#else
				const uint64_t bbox_start_us = MpFaceCollectDiagTime();
					item.UpdateBBox();
					const uint32_t bbox_us = (uint32_t)(MpFaceCollectDiagTime() - bbox_start_us);
#endif
					collector->mesh_face_cb_bbox_us_total += bbox_us;
					if (bbox_us > collector->mesh_face_cb_bbox_us_max)
						collector->mesh_face_cb_bbox_us_max = bbox_us;

					// FL-4128/FL-4137: server-owned collision component. Raw AKM
					// triangles remain as geometric input, but gameplay collision uses
					// explicit proxy boxes built here inside mp_step. This is not the
					// deleted placed-block-only lane; placed blocks and buildings both
					// arrive through QueryWorld -> MeshCollect and are normalized here.
					if (item.nrm[2] > 0.25f || fabsf(item.nrm[2]) < 0.85f)
					{
						collector->boxes.push_back({});
						MpCollisionBox& box = collector->boxes.back();
						for (int axis = 0; axis < 3; axis++)
						{
							box.bmin[axis] = item.bbox_min[axis];
							box.bmax[axis] = item.bbox_max[axis];
							if (box.bmax[axis] < box.bmin[axis])
							{
								const float tmp = box.bmin[axis];
								box.bmin[axis] = box.bmax[axis];
								box.bmax[axis] = tmp;
							}
						}
						box.material = item.material;
						box.support_top = item.nrm[2] > 0.25f ? 1 : 0;
						box.support_only = 0;
						box.source_inst_id = item.source_inst_id;
						box.source_mesh_id = item.source_mesh_id;
						box.source_mesh_faces = item.source_mesh_faces;
						box.source_face_ordinal = item.source_face_ordinal;
						box.source_inst_story_id = item.source_inst_story_id;
						box.source_inst_flags = item.source_inst_flags;
						box.source_inst_name_hash = item.source_inst_name_hash;
						box.source_mesh_name_hash = item.source_mesh_name_hash;
					}

					collector->world_callbacks++;

				const uint32_t accept_us = (uint32_t)(MpFaceCollectDiagTime() - accept_start_us);
				collector->mesh_face_cb_accept_us_total += accept_us;
				if (accept_us > collector->mesh_face_cb_accept_us_max)
					collector->mesh_face_cb_accept_us_max = accept_us;
			}

			const uint32_t cb_us = (uint32_t)(MpFaceCollectDiagTime() - cb_start_us);
			collector->mesh_face_cb_calls++;
			collector->mesh_face_cb_us_total += cb_us;
			if (cb_us > collector->mesh_face_cb_us_max)
			{
				collector->mesh_face_cb_us_max = cb_us;
				collector->mesh_face_cb_us_max_inst_id = collector->current_inst_id;
				collector->mesh_face_cb_us_max_mesh_id = collector->current_mesh_id;
				collector->mesh_face_cb_us_max_mesh_faces = collector->current_mesh_faces_reported;
				collector->mesh_face_cb_us_max_face_ordinal = collector->current_mesh_face_ordinal;
				collector->mesh_face_cb_us_max_accept = accept ? 1u : 0u;
				collector->mesh_face_cb_us_max_visual = visual;
				collector->mesh_face_cb_us_max_soup_index = accept && !collector->soup.empty()
					? (uint32_t)(collector->soup.size() - 1u)
					: UINT32_MAX;
				collector->mesh_face_cb_us_max_inst_story_id = collector->current_inst_story_id;
				collector->mesh_face_cb_us_max_inst_flags = collector->current_inst_flags;
				collector->mesh_face_cb_us_max_inst_name_hash = collector->current_inst_name_hash;
				collector->mesh_face_cb_us_max_mesh_name_hash = collector->current_mesh_name_hash;
				collector->mesh_face_cb_us_max_inst_bbox_cx_milli = collector->current_inst_bbox_cx_milli;
				collector->mesh_face_cb_us_max_inst_bbox_cy_milli = collector->current_inst_bbox_cy_milli;
				collector->mesh_face_cb_us_max_inst_bbox_cz_milli = collector->current_inst_bbox_cz_milli;
				collector->mesh_face_cb_us_max_inst_bbox_diag_milli = collector->current_inst_bbox_diag_milli;
				collector->mesh_face_cb_us_max_query_cx_milli = MpDebugMilli(collector->collect_center_x);
				collector->mesh_face_cb_us_max_query_cy_milli = MpDebugMilli(collector->collect_center_y);
				collector->mesh_face_cb_us_max_query_radius_milli = MpDebugMilliU(collector->collect_radius);
				collector->mesh_face_cb_us_max_query_bbox_dist_milli = collector->current_query_bbox_dist_milli;
				collector->mesh_face_cb_us_max_query_bbox_overlap_milli = collector->current_query_bbox_overlap_milli;
			}
			collector->current_mesh_face_ordinal++;
		}

		// FL-2957 restore: no BSP early-exit callback remains in the authoritative
		// multiplayer collision path. QueryWorld may still require a callback slot,
		// but it must not cap or prioritize away collision evidence.
		static bool ShouldContinueCollecting(void* cookie)
		{
			(void)cookie;
			return true;
		}

		static bool ShouldContinueCollectingTerrain(void* cookie)
		{
			(void)cookie;
			return true;
		}

		static void SpriteCollect(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie)
		{
			(void)inst;
			(void)s;
			(void)pos;
			(void)yaw;
			(void)anim;
			(void)frame;
			(void)reps;
			(void)cookie;
		}

		static void MeshCollect(Inst* i, Mesh* m, double tm[16], void* cookie)
		{
			MpSoupCollector* collector = static_cast<MpSoupCollector*>(cookie);
			collector->collect_tm = tm;
			double inst_bbox[6] = {};

			{
				const uint32_t faces = (uint32_t)GetMeshFaces(m);
				collector->current_inst_id = (uint64_t)(uintptr_t)i;
				collector->current_mesh_id = (uint64_t)(uintptr_t)m;
				collector->current_mesh_faces_reported = faces;
				collector->current_mesh_face_ordinal = 0;
				collector->current_inst_story_id = GetInstStoryID(i);
				collector->current_inst_flags = (uint32_t)GetInstFlags(i);
				collector->current_inst_name_hash = MpDebugHashString(GetInstName(i));
				char mesh_name[256] = {};
				GetMeshName(m, mesh_name, (int)sizeof(mesh_name));
				collector->current_mesh_name_hash = MpDebugHashString(mesh_name);
				collector->mesh_faces_reported_total += faces;
				if (faces > collector->mesh_faces_reported_max)
					collector->mesh_faces_reported_max = faces;
			}

			bool bbox_outside_swept_query = false;
			{
				GetInstBBox(i, inst_bbox);
				const double minx = inst_bbox[0];
				const double maxx = inst_bbox[1];
				const double miny = inst_bbox[2];
				const double maxy = inst_bbox[3];
				const double cx = collector->collect_center_x;
				const double cy = collector->collect_center_y;
				const double r = collector->collect_radius;
				// AABB vs circle (cheap conservative test): if the closest point on the bbox
				// to (cx,cy) is outside radius, it doesn't overlap the swept query footprint.
				const double px = (cx < minx) ? minx : ((cx > maxx) ? maxx : cx);
				const double py = (cy < miny) ? miny : ((cy > maxy) ? maxy : cy);
				const double dx = px - cx;
				const double dy = py - cy;
				const double dist = sqrt(dx * dx + dy * dy);
				collector->current_query_bbox_dist_milli = MpDebugMilliU(dist);
				collector->current_query_bbox_overlap_milli = MpDebugMilli(r - dist);
				const double bcx = (inst_bbox[0] + inst_bbox[1]) * 0.5;
				const double bcy = (inst_bbox[2] + inst_bbox[3]) * 0.5;
				const double bcz = (inst_bbox[4] + inst_bbox[5]) * 0.5;
				const double bdx = inst_bbox[1] - inst_bbox[0];
				const double bdy = inst_bbox[3] - inst_bbox[2];
				const double bdz = inst_bbox[5] - inst_bbox[4];
				collector->current_inst_bbox_cx_milli = MpDebugMilli(bcx);
				collector->current_inst_bbox_cy_milli = MpDebugMilli(bcy);
				collector->current_inst_bbox_cz_milli = MpDebugMilli(bcz);
				collector->current_inst_bbox_diag_milli = MpDebugMilliU(sqrt(bdx * bdx + bdy * bdy + bdz * bdz));
				if (dist > r)
				{
					collector->bbox_would_skip++;
					collector->bbox_would_skip_faces += (uint32_t)GetMeshFaces(m);
					bbox_outside_swept_query = true;
				}
			}
			if (bbox_outside_swept_query)
			{
				collector->mesh_bbox_skips++;
				return;
			}

				const uint64_t mesh_start_us = a3dGetTime();
				const uint32_t cb_total_before = collector->mesh_face_cb_us_total;
				const size_t before = collector->soup.size();
				const size_t box_before = collector->boxes.size();
				const MpMeshSoupCacheEntry* cached = MpFindMeshSoupCache(
				i, m, tm, inst_bbox, collector->collect_mul_xy, collector->collect_mul_z,
				collector->current_mesh_faces_reported,
				collector->current_inst_name_hash,
				collector->current_mesh_name_hash,
				collector->current_inst_flags,
				collector->current_inst_story_id);
			if (cached)
			{
				collector->mesh_cache_hits++;
				collector->mesh_cache_items += (uint32_t)cached->items.size();
					collector->soup.reserve(collector->soup.size() + cached->items.size());
					collector->soup.insert(collector->soup.end(), cached->items.begin(), cached->items.end());
					collector->boxes.reserve(collector->boxes.size() + cached->boxes.size());
					collector->boxes.insert(collector->boxes.end(), cached->boxes.begin(), cached->boxes.end());
					collector->max_height = fmaxf(collector->max_height, cached->max_height);
					collector->world_callbacks += (uint32_t)cached->items.size();
					collector->mesh_face_cb_accepts += (uint32_t)cached->items.size();
			}
			else
			{
				collector->mesh_cache_misses++;
#ifdef FL2957_DIAG_RESTORE_MESH_RESERVE
				collector->soup.reserve(collector->soup.size() + (size_t)GetMeshFaces(m));
#endif
				QueryMesh(m, FaceCollect, cookie);
					MpStoreMeshSoupCache(
						i, m, tm, inst_bbox, collector->collect_mul_xy, collector->collect_mul_z,
						collector->current_mesh_faces_reported,
						collector->current_inst_name_hash,
						collector->current_mesh_name_hash,
						collector->current_inst_flags,
						collector->current_inst_story_id,
						collector->soup, before, collector->soup.size(),
						collector->boxes, box_before, collector->boxes.size());
				}
			const uint32_t mesh_us = (uint32_t)(a3dGetTime() - mesh_start_us);
			const uint32_t cb_total_after = collector->mesh_face_cb_us_total;
			const uint32_t cb_delta =
				(cb_total_after >= cb_total_before) ? (cb_total_after - cb_total_before) : 0;
			const uint32_t overhead_us = (mesh_us >= cb_delta) ? (mesh_us - cb_delta) : 0;
			collector->mesh_query_overhead_us_total += overhead_us;
			if (overhead_us > collector->mesh_query_overhead_us_max)
				collector->mesh_query_overhead_us_max = overhead_us;

			collector->collect_mesh_us += mesh_us; // legacy name: mesh query wall time
			collector->mesh_query_us_total += mesh_us;
			if (mesh_us > collector->mesh_query_us_max)
				collector->mesh_query_us_max = mesh_us;

			collector->mesh_instances++;
			collector->mesh_faces += (uint32_t)(collector->soup.size() - before);
			collector->current_inst_id = 0;
			collector->current_mesh_id = 0;
			collector->current_mesh_faces_reported = 0;
			collector->current_mesh_face_ordinal = 0;
			collector->current_inst_story_id = 0;
			collector->current_inst_flags = 0;
			collector->current_inst_name_hash = 0;
			collector->current_mesh_name_hash = 0;
			collector->current_inst_bbox_cx_milli = 0;
			collector->current_inst_bbox_cy_milli = 0;
			collector->current_inst_bbox_cz_milli = 0;
			collector->current_inst_bbox_diag_milli = 0;
			collector->current_query_bbox_dist_milli = 0;
			collector->current_query_bbox_overlap_milli = 0;
		}

		static void PushTerrainTri(MpSoupCollector* collector, const float a[3], const float b[3], const float c[3], int mat)
		{
			collector->soup.push_back({});
			MpSoupItem& item = collector->soup.back();
			item.source_inst_id = 0;
			item.source_mesh_id = 0;
			item.source_mesh_faces = 0;
			item.source_face_ordinal = 0;
			item.source_inst_story_id = 0;
			item.source_inst_flags = 0;
			item.source_inst_name_hash = 0;
			item.source_mesh_name_hash = 0;
			collector->terrain_tris++;
			memcpy(item.tri[0], a, sizeof(item.tri[0]));
			memcpy(item.tri[1], b, sizeof(item.tri[1]));
			memcpy(item.tri[2], c, sizeof(item.tri[2]));
			item.material = mat;
			float e1[3] = { b[0] - a[0], b[1] - a[1], b[2] - a[2] };
			float e2[3] = { c[0] - a[0], c[1] - a[1], c[2] - a[2] };
			CrossProduct(e1, e2, item.nrm);
			float nrm = 1.0f / sqrtf(item.nrm[0] * item.nrm[0] +
				item.nrm[1] * item.nrm[1] +
				item.nrm[2] * item.nrm[2]);
			item.nrm[0] *= nrm;
			item.nrm[1] *= nrm;
			item.nrm[2] *= nrm;
			item.nrm[3] = -(a[0] * item.nrm[0] + a[1] * item.nrm[1] + a[2] * item.nrm[2]);
			item.UpdateBBox();
		}

	static void PatchCollect(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		(void)view_flags;
		MpSoupCollector* collector = static_cast<MpSoupCollector*>(cookie);
		uint16_t* hmap = GetTerrainHeightMap(p);
		uint16_t* vmap = GetTerrainVisualMap(p);
		static const double sxy = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;
		int rot = GetTerrainDiag(p);

		float hi = GetTerrainHi(p);
		collector->max_height = fmaxf(hi, collector->max_height);

		for (int hy = 0; hy < HEIGHT_CELLS; hy++)
		{
			for (int hx = 0; hx < HEIGHT_CELLS; hx++)
			{
				uint16_t vis = *vmap;
				int elv = vis >> 15;
				int mat = vis & 0x3F;
				vmap += 2;

				if (mat == 4)
					mat = 0;
				else if (mat == 5)
					mat = 5;
				else if (mat != 2)
					mat = 3 + elv;

				float x0 = (float)((x + hx * sxy) * collector->collect_mul_xy);
				float x1 = (float)(x0 + sxy * collector->collect_mul_xy);
				float y0 = (float)((y + hy * sxy) * collector->collect_mul_xy);
				float y1 = (float)(y0 + sxy * collector->collect_mul_xy);

				// FL-2957 H-P0: visual bit 15 lifts the collision plane by HEIGHT_SCALE,
				// matching the lift that SvrSampleTerrainHeight applies to terrain_z.
				// Previously, PatchCollect did NOT apply this lift, creating a permanent
				// support_z_delta = HEIGHT_SCALE for affected tiles, which rejected the
				// idle fast path forever. Now both surfaces agree.
				// LINEAGE_JSON: {"fl":"FL-2957","hypothesis":"H-P0","commit":"f51c161b","what":"propagate visual bit-15 HEIGHT_SCALE lift into PatchCollect collision triangles","result":"REFUTED — manual-20260505-070756 shows delta=2.0 not 16.0; bit-15 is NOT the active cause of the 2.0 support_z delta at spawn. Reverting lift."}
				// H-P0 REFUTED: pos_z=55, terrain_z=57, delta=2.0. HEIGHT_SCALE=16.
				// If bit-15 were the cause, delta would be 16. The 2.0 delta has a
				// different source (likely terrain heightmap interpolation or the
				// +0.75 support search offset in MpStepSupportHeightAt).
				float v[4][3] =
				{
					{x0, y0, (float)hmap[hy * (HEIGHT_CELLS + 1) + hx] * collector->collect_mul_z},
					{x1, y0, (float)hmap[hy * (HEIGHT_CELLS + 1) + hx + 1] * collector->collect_mul_z},
					{x0, y1, (float)hmap[(hy + 1) * (HEIGHT_CELLS + 1) + hx] * collector->collect_mul_z},
					{x1, y1, (float)hmap[(hy + 1) * (HEIGHT_CELLS + 1) + hx + 1] * collector->collect_mul_z},
				};

				if (rot & 1)
				{
					PushTerrainTri(collector, v[2], v[0], v[1], mat);
					if (collector->capped)
						return;
					PushTerrainTri(collector, v[2], v[1], v[3], mat);
					if (collector->capped)
						return;
				}
				else
				{
					PushTerrainTri(collector, v[0], v[3], v[2], mat);
					if (collector->capped)
						return;
					PushTerrainTri(collector, v[0], v[1], v[3], mat);
					if (collector->capped)
						return;
				}

				rot >>= 1;
			}

			vmap += VISUAL_CELLS;
		}
	}

	// FL-4137 #21: PushPlacedBlockTri, PushPlacedBlockQuad, CollectPlacedBlocks
	// DELETED. They were a parallel soup ingest path that built per-tick block
	// triangles tagged with PBLK magic. Replacement (FL-4137 #25 Tier 1):
	// blocks become server-owned world entities with CollisionBody and
	// SupportSurface components; the registry feed below populates collision
	// boxes without a render/collision proxy Inst. Re-adding any of these three
	// functions is a regression.

	// FL-2957 SOUP-COVERAGE HOLE: this function builds the triangle soup at the
	// PRE-STEP position (the `pos` argument). After MpStepOnce runs the collision
	// sweep and resolves the character to a new position, MpStepSupportHeightAt
	// searches the soup at the RESOLVED (x,y). If the sweep displaced the character
	// beyond the soup's collection range (world_radius around pre-step pos),
	// the support probe misses entirely — no triangles exist in the soup at the
	// new XY. This produces `contact_normal_z=0` from the sweep (no collision,
	// just full velocity), then `support_found=false`, then `accum_contact` decays
	// by 0.9 without replenishment, eventually reaching 0.0 after ~10 ticks.
	// At spawn (-2.8,-73.6): terrain_z=57 (SvrSampleTerrainHeight) but the
	// collision sweep resolves to z=55 (different triangle surface), and the
	// support probe at the resolved XY misses the soup built at pre-step XY.
	// The result is a permanent support_z_delta=2.0 that rejects the idle fast
	// path forever, even though the character IS on terrain.
	// LINEAGE_JSON: {"fl":"FL-2957","hypothesis":"H-P0-refuted","source_owner":"mp_step.cpp:Build","root_cause":"soup_built_at_pre_step_pos_but_support_searched_at_resolved_pos","patch_seam":"MpStepSupportHeightAt should rebuild soup at resolved post-sweep XY when initial probe misses, or MpStepOnce must not write back a position outside soup coverage"}
	void Build(const MpStepEnv& env, const float pos[3], float dx, float dy)
	{
			const uint64_t collect_start_us = a3dGetTime();
			soup.clear();
			boxes.clear();
			max_height = env.water_level;
		collect_world_us = 0;
		collect_terrain_us = 0;
		collect_total_us = 0;
		collect_mesh_us = 0;
		mesh_instances = 0;
		mesh_faces = 0;
		world_callbacks = 0;
		terrain_tris = 0;
		mesh_per_mesh_cap_hits = 0;
		mesh_bbox_skips = 0;
		support_priority_callbacks = 0;
		mesh_query_us_total = 0;
		mesh_query_us_max = 0;
		mesh_face_cb_us_total = 0;
		mesh_face_cb_us_max = 0;
		mesh_face_cb_calls = 0;
		mesh_face_cb_accepts = 0;
		current_inst_id = 0;
		current_mesh_id = 0;
		current_mesh_faces_reported = 0;
		current_mesh_face_ordinal = 0;
		current_inst_story_id = 0;
		current_inst_flags = 0;
		current_inst_name_hash = 0;
		current_mesh_name_hash = 0;
		current_inst_bbox_cx_milli = 0;
		current_inst_bbox_cy_milli = 0;
		current_inst_bbox_cz_milli = 0;
		current_inst_bbox_diag_milli = 0;
		current_query_bbox_dist_milli = 0;
		current_query_bbox_overlap_milli = 0;
		mesh_face_cb_us_max_inst_id = 0;
		mesh_face_cb_us_max_mesh_id = 0;
		mesh_face_cb_us_max_mesh_faces = 0;
		mesh_face_cb_us_max_face_ordinal = 0;
		mesh_face_cb_us_max_accept = 0;
		mesh_face_cb_us_max_visual = 0;
		mesh_face_cb_us_max_soup_index = 0;
		mesh_face_cb_us_max_inst_story_id = 0;
		mesh_face_cb_us_max_inst_flags = 0;
		mesh_face_cb_us_max_inst_name_hash = 0;
		mesh_face_cb_us_max_mesh_name_hash = 0;
		mesh_face_cb_us_max_inst_bbox_cx_milli = 0;
		mesh_face_cb_us_max_inst_bbox_cy_milli = 0;
		mesh_face_cb_us_max_inst_bbox_cz_milli = 0;
		mesh_face_cb_us_max_inst_bbox_diag_milli = 0;
		mesh_face_cb_us_max_query_cx_milli = 0;
		mesh_face_cb_us_max_query_cy_milli = 0;
		mesh_face_cb_us_max_query_radius_milli = 0;
		mesh_face_cb_us_max_query_bbox_dist_milli = 0;
		mesh_face_cb_us_max_query_bbox_overlap_milli = 0;
		soup_reallocs = 0;
		soup_capacity_max = (uint32_t)soup.capacity();
		bbox_would_skip = 0;
		bbox_would_skip_faces = 0;
		mesh_cache_hits = 0;
		mesh_cache_misses = 0;
		mesh_cache_items = 0;
		world_bsp_tests = 0;
		world_bsp_nodes = 0;
		world_bsp_insts = 0;
		capped = false;
		collect_mul_xy = 1.0f / env.world_radius;
		collect_mul_z = 2.0f / env.world_height;

		double cx = pos[0] + dx * 0.5;
		double cy = pos[1] + dy * 0.5;
		double th = 0.1;
		double qx = fabs(dx) * 0.5 + env.world_radius + th;
		double qy = fabs(dy) * 0.5 + env.world_radius + th;

		// FL-4137 #26 DIAG: instrumentation-first probe before next code edit.
		// Gated by env var FL4137_DIAG. Prints one line per call when a placed
		// block entity is registered AND the player is within 30 world units of
		// any active block. Captures: player pos, query cull frame, collect_mul
		// scaling factors, world_radius/world_height (env constants), and
		// nearest active block pos. Compare with the per-loop ECS prints below.
		const bool fl4137_diag = MpStepEnvFlag("FL4137_DIAG");
		bool fl4137_diag_fire = false;
		if (fl4137_diag && env.world_entities)
		{
			float nearest_d2 = 1e30f;
			const ServerWorldEntity* nearest = 0;
			for (int i = 0; i < SERVER_WORLD_ENTITY_MAX; i++)
			{
				const ServerWorldEntity& e = env.world_entities->entities[i];
				if (!e.active) continue;
				if ((e.flags & SERVER_WORLD_ENTITY_COLLIDABLE) == 0) continue;
				const float dxp = e.pos[0] - (float)pos[0];
				const float dyp = e.pos[1] - (float)pos[1];
				const float d2 = dxp * dxp + dyp * dyp;
				if (d2 < nearest_d2) { nearest_d2 = d2; nearest = &e; }
			}
			if (nearest && nearest_d2 < 30.0f * 30.0f)
			{
				fl4137_diag_fire = true;
				printf("[FL4137-DIAG] player_pos=(%.3f,%.3f,%.3f) cx=%.3f cy=%.3f qx=%.3f qy=%.3f "
				       "collect_mul_xy=%.6f collect_mul_z=%.6f world_radius=%.3f world_height=%.3f "
				       "nearest_block id=%u pos=(%.3f,%.3f,%.3f) half=%.3f height=%.3f d=%.3f\n",
				       (float)pos[0], (float)pos[1], (float)pos[2],
				       (float)cx, (float)cy, (float)qx, (float)qy,
				       collect_mul_xy, collect_mul_z, env.world_radius, env.world_height,
				       (unsigned)nearest->item_id,
				       nearest->pos[0], nearest->pos[1], nearest->pos[2],
				       nearest->collision_half_extent, nearest->collision_height,
				       sqrtf(nearest_d2));
				fflush(stdout);
			}
		}
		// FL-2957: store the full swept query radius for mesh instance bbox
		// pre-checks. This must cover both axes; using only dx skipped valid
		// meshes when movement was mostly along Y.
		collect_center_x = (float)cx;
		collect_center_y = (float)cy;
		collect_radius = (float)fmax(qx, qy);
		double clip_world[4][4] =
		{
			{ 1, 0, 0, qx - cx },
			{-1, 0, 0, qx + cx },
			{ 0, 1, 0, qy - cy },
			{ 0,-1, 0, qy + cy }
		};

		// FL-2957: collect terrain BEFORE world meshes so floor/support triangles
		// fill the soup first. Dense meshes previously crowded out terrain when
		// world_callbacks hit the 1024 cap before QueryTerrain ran.
		if (env.terrain)
		{
			const uint64_t terrain_start_us = a3dGetTime();
			QueryTerrainCB cb = { PatchCollect, ShouldContinueCollectingTerrain };
			QueryTerrain(env.terrain, 4, clip_world, 0xAA, &cb, this);
			collect_terrain_us = (uint32_t)(a3dGetTime() - terrain_start_us);
		}
		if (env.world)
		{
			const uint64_t world_start_us = a3dGetTime();
			// FL-2957: pass query center for nearest-first BSP child ordering
			double qc[2] = { cx, cy };
			QueryWorldCB cb = { MeshCollect, SpriteCollect, ShouldContinueCollecting, qc };
			QueryWorld(env.world, 4, clip_world, &cb, this);
			collect_world_us = (uint32_t)(a3dGetTime() - world_start_us);
			// Capture traversal cardinality for intermittence diagnosis (no behavior change).
			world_bsp_tests = (uint32_t)bsp_tests;
			world_bsp_nodes = (uint32_t)bsp_nodes;
			world_bsp_insts = (uint32_t)bsp_insts;
		}
		if (env.world_entities)
		{
			for (int i = 0; i < SERVER_WORLD_ENTITY_MAX; i++)
			{
				const ServerWorldEntity& entity = env.world_entities->entities[i];
				if (!entity.active)
					continue;
				if ((entity.flags & SERVER_WORLD_ENTITY_COLLIDABLE) == 0)
					continue;
				// CKPT-B: mesh-backed placed blocks (server CreateInst path,
				// CKPT-A) are already collected through QueryWorld above. Skip
				// the legacy two-box AABB emission so we don't double-count
				// and so the FL-4137 step-up carve-outs become obsolete (the
				// cube mesh has real face geometry and the building-collision
				// path handles auto-step natively).
				if (entity.mesh_inst)
					continue;
				const float half = entity.collision_half_extent > 0.0f
					? entity.collision_half_extent
					: 1.0f;
				const float height = entity.collision_height > 0.0f
					? entity.collision_height
					: 2.0f;
				const float world_min[3] =
				{
					entity.pos[0] - half,
					entity.pos[1] - half,
					entity.pos[2],
				};
				const float world_max[3] =
				{
					entity.pos[0] + half,
					entity.pos[1] + half,
					entity.pos[2] + height,
				};
				const bool cull_reject =
					(world_max[0] < (float)(cx - qx) ||
					 world_min[0] > (float)(cx + qx) ||
					 world_max[1] < (float)(cy - qy) ||
					 world_min[1] > (float)(cy + qy));
				// FL-4137 #26 DIAG: per-entity cull + scaling probe.
				if (fl4137_diag_fire)
				{
					const float scaled_min[3] = {
						world_min[0] * collect_mul_xy,
						world_min[1] * collect_mul_xy,
						world_min[2] * collect_mul_z };
					const float scaled_max[3] = {
						world_max[0] * collect_mul_xy,
						world_max[1] * collect_mul_xy,
						world_max[2] * collect_mul_z };
					printf("[FL4137-DIAG]   entity slot=%d id=%u world_min=(%.3f,%.3f,%.3f) "
					       "world_max=(%.3f,%.3f,%.3f) cull_xrange=(%.3f,%.3f) cull_yrange=(%.3f,%.3f) "
					       "cull_reject=%d scaled_min=(%.6f,%.6f,%.6f) scaled_max=(%.6f,%.6f,%.6f) "
					       "support_top=%d\n",
					       i, (unsigned)entity.item_id,
					       world_min[0], world_min[1], world_min[2],
					       world_max[0], world_max[1], world_max[2],
					       (float)(cx - qx), (float)(cx + qx),
					       (float)(cy - qy), (float)(cy + qy),
					       cull_reject ? 1 : 0,
					       scaled_min[0], scaled_min[1], scaled_min[2],
					       scaled_max[0], scaled_max[1], scaled_max[2],
					       (entity.flags & SERVER_WORLD_ENTITY_SUPPORT) ? 1 : 0);
					fflush(stdout);
				}
				if (cull_reject)
					continue;

				// FL-4137 #27 two-box emission to mirror AKM per-face semantics.
				// BOX A is the full cube body with support_top=0; it owns side
				// push via RejectSweptSphereAABB / CheckCollision but does NOT
				// advertise itself as a standable top, so a player walking into
				// the side cannot trigger ContainsSupportXY -> snap-to-bmax[2].
				// BOX B is a thin top slab at world_max[2] with support_top=1;
				// it owns stand-on-top only. The XY footprint is identical so
				// the support resolver finds it when player XY is over the
				// block top, but no body underneath that slab claims to be a
				// support.
				boxes.push_back({});
				{
					MpCollisionBox& body = boxes.back();
					body.bmin[0] = world_min[0] * collect_mul_xy;
					body.bmin[1] = world_min[1] * collect_mul_xy;
					body.bmin[2] = world_min[2] * collect_mul_z;
					body.bmax[0] = world_max[0] * collect_mul_xy;
					body.bmax[1] = world_max[1] * collect_mul_xy;
					body.bmax[2] = world_max[2] * collect_mul_z;
					body.material = 0;
					body.support_top = 0;
					body.support_only = 0;
					body.source_entity_id = entity.entity_id;
					body.source_item_id = entity.item_id;
				}
				if (entity.flags & SERVER_WORLD_ENTITY_SUPPORT)
				{
					boxes.push_back({});
					MpCollisionBox& top = boxes.back();
					top.bmin[0] = world_min[0] * collect_mul_xy;
					top.bmin[1] = world_min[1] * collect_mul_xy;
					top.bmin[2] = world_max[2] * collect_mul_z;
					top.bmax[0] = world_max[0] * collect_mul_xy;
					top.bmax[1] = world_max[1] * collect_mul_xy;
					top.bmax[2] = world_max[2] * collect_mul_z;
					top.material = 0;
					top.support_top = 1;
					top.support_only = 1;
					top.source_entity_id = entity.entity_id;
					top.source_item_id = entity.item_id;
				}
				max_height = fmaxf(max_height, world_max[2]);
				world_callbacks++;
			}
		}

		// FL-4137 #26 DIAG: report the first AKM-emitted box for direct
		// coordinate-space comparison against the ECS-emitted boxes above.
		if (fl4137_diag_fire && !boxes.empty())
		{
			const MpCollisionBox& any_box = boxes.front();
			printf("[FL4137-DIAG]   boxes_total=%zu first_box bmin=(%.6f,%.6f,%.6f) "
			       "bmax=(%.6f,%.6f,%.6f) support_top=%u source_inst_id=%llu "
			       "source_mesh_id=%llu source_item_id=%u source_entity_id=%llu\n",
			       boxes.size(),
			       any_box.bmin[0], any_box.bmin[1], any_box.bmin[2],
			       any_box.bmax[0], any_box.bmax[1], any_box.bmax[2],
			       (unsigned)any_box.support_top,
			       (unsigned long long)any_box.source_inst_id,
			       (unsigned long long)any_box.source_mesh_id,
			       (unsigned)any_box.source_item_id,
			       (unsigned long long)any_box.source_entity_id);
			fflush(stdout);
		}
		// FL-4137 #25: placed blocks now enter mp_step from the server-owned
		// component registry above. No World::AddInst collision proxy, no
		// collision-mesh render mirror, and no second placed-block-only resolver.
		collect_total_us = (uint32_t)(a3dGetTime() - collect_start_us);
	}
};


static bool MpStepSupportHitInRange(const MpSoupCollector& collector, float world_x, float world_y,
	float min_height_exclusive, float max_height_inclusive, MpSupportHit* out_support)
{
	if (!isfinite(world_x) || !isfinite(world_y) ||
		!isfinite(min_height_exclusive) || !isfinite(max_height_inclusive) ||
		max_height_inclusive < min_height_exclusive)
		return false;

	const float x = world_x * collector.collect_mul_xy;
	const float y = world_y * collector.collect_mul_xy;
	float best_height = 0.0f;
	MpSupportHit best_support = {};
	bool found = false;

	for (const MpSoupItem& item : collector.soup)
	{
		if (item.nrm[2] <= 0.25f)
			continue;

		const float* a = item.tri[0];
		const float* b = item.tri[1];
		const float* c = item.tri[2];
		float v0x = b[0] - a[0];
		float v0y = b[1] - a[1];
		float v1x = c[0] - a[0];
		float v1y = c[1] - a[1];
		float v2x = x - a[0];
		float v2y = y - a[1];
		float d00 = v0x * v0x + v0y * v0y;
		float d01 = v0x * v1x + v0y * v1y;
		float d11 = v1x * v1x + v1y * v1y;
		float d20 = v2x * v0x + v2y * v0y;
		float d21 = v2x * v1x + v2y * v1y;
		float denom = d00 * d11 - d01 * d01;
		if (fabsf(denom) < 0.0000001f)
			continue;

		float v = (d11 * d20 - d01 * d21) / denom;
		float w = (d00 * d21 - d01 * d20) / denom;
		float u = 1.0f - v - w;
		if (u < -0.02f || v < -0.02f || w < -0.02f)
			continue;
		if (u > 1.02f || v > 1.02f || w > 1.02f)
			continue;

		float support_z = (u * a[2] + v * b[2] + w * c[2]) / collector.collect_mul_z;
		if (support_z <= min_height_exclusive || support_z > max_height_inclusive)
			continue;
		if (!found || support_z > best_height)
		{
			best_height = support_z;
			best_support = MpSupportHitForSoupItem(item, support_z);
			found = true;
		}
	}

	for (const MpCollisionBox& box : collector.boxes)
	{
		if (!box.support_top)
			continue;
		if (!box.ContainsSupportXY(x, y))
			continue;
		const float support_z = box.bmax[2] / collector.collect_mul_z;
		if (support_z <= min_height_exclusive || support_z > max_height_inclusive)
			continue;
		if (!found || support_z > best_height)
		{
			best_height = support_z;
			best_support = MpSupportHitForCollisionBox(box, support_z);
			found = true;
		}
	}

	if (found && out_support)
		*out_support = best_support;
	return found;
}

static bool MpStepSupportHeightInRange(const MpSoupCollector& collector, float world_x, float world_y,
	float min_height_exclusive, float max_height_inclusive, float* out_height)
{
	MpSupportHit support = {};
	const bool found = MpStepSupportHitInRange(
		collector,
		world_x,
		world_y,
		min_height_exclusive,
		max_height_inclusive,
		&support);
	if (found && out_height)
		*out_height = support.z;
	return found;
}

// FL-2957: H-P0 REFUTED — support_z delta=2.0 at spawn (-2.8,-73.6) is NOT from
// bit-15 (HEIGHT_SCALE=16 would give delta=16). Root cause is a
// SOUP-COLLECTION-VS-RESOLVED-POSITION mismatch:
//   1. MpSoupCollector::Build() at mp_step.cpp:416 collects triangles at
//      the PRE-STEP position (next.pos before collision sweep).
//   2. After the collision sweep resolves the character to z=55 (pos_z),
//      MpStepSupportHeightAt searches soup at the RESOLVED (world_x, world_y).
//   3. If the sweep displaced character downhill (pos_z went from 57→55),
//      the soup (collected at pre-step XY) may NOT contain triangles at the
//      resolved (world_x, world_y). The support probe misses even though
//      terrain at z=57 exists in world geometry.
//   4. terrain_z=57 from SvrSampleTerrainHeight uses independent bilinear
//      interpolation directly from the heightmap — it always finds a value.
//   5. Result: support_z_delta=2.0 looks permanent because MpStepOnce
//      re-derives the same collision plane every tick (soup rebuilt at
//      pre-step position each time, sweep resolves to same z=55), and
//      support probe at post-sweep (x,y) always misses in the pre-step soup.
// LINEAGE_JSON: {"fl":"FL-2957","hypothesis":"H-P0-refuted","source_owner":"mp_step.cpp:569-588","root_cause":"soup_coverage_hole_at_resolved_position","patch_seam":"MpStepSupportHeightInRange should retry soup rebuild at resolved XY when probe fails, or MpStepOnce must search support before/when the sweep moves XY beyond soup bounds"}
static bool MpStepSupportHeightAt(const MpSoupCollector& collector, float world_x, float world_y,
	float world_z, float max_recovery_depth, float* out_height)
{
	if (!isfinite(world_z) || !isfinite(max_recovery_depth) || max_recovery_depth <= 0.0f)
		return false;
	// FL-2957: widen the search window to find surfaces slightly below the
	// actor's feet, not just above. The old +0.75 offset missed support
	// surfaces within 0.75 above world_z — e.g., at spawn pos_z=55 with
	// terrain at z=55.5, the search range (55.75, 87) missed z=55.5.
	// New offset: -kMpSupportSnapEpsilon (= -0.25) allows finding surfaces
	// that are at or just below feet level. The step-up gate
	// (grounded_support_snap_within_step) still prevents unwanted upward
	// snaps beyond kMpMaxImplicitStepUp.
	return MpStepSupportHeightInRange(
		collector,
		world_x,
		world_y,
		world_z - kMpSupportSnapEpsilon - kMpSupportContactSlop,
		world_z + max_recovery_depth,
		out_height);
}

static bool MpStepSupportHitAt(const MpSoupCollector& collector, float world_x, float world_y,
	float world_z, float max_recovery_depth, MpSupportHit* out_support)
{
	if (!isfinite(world_z) || !isfinite(max_recovery_depth) || max_recovery_depth <= 0.0f)
		return false;
	return MpStepSupportHitInRange(
		collector,
		world_x,
		world_y,
		world_z - kMpSupportSnapEpsilon - kMpSupportContactSlop,
		world_z + max_recovery_depth,
		out_support);
}

static bool MpStepFloorHeightAt(const MpSoupCollector& collector, float world_x, float world_y,
	float reference_z, float max_drop, float max_height, float* out_height)
{
	if (!isfinite(reference_z) || !isfinite(max_drop) || !isfinite(max_height) || max_drop <= 0.0f)
		return false;

	const float x = world_x * collector.collect_mul_xy;
	const float y = world_y * collector.collect_mul_xy;
	float best_height = -1000000.0f;
	bool found = false;

	for (const MpSoupItem& item : collector.soup)
	{
		if (item.nrm[2] <= 0.25f)
			continue;

		const float* a = item.tri[0];
		const float* b = item.tri[1];
		const float* c = item.tri[2];
		float v0x = b[0] - a[0];
		float v0y = b[1] - a[1];
		float v1x = c[0] - a[0];
		float v1y = c[1] - a[1];
		float v2x = x - a[0];
		float v2y = y - a[1];
		float d00 = v0x * v0x + v0y * v0y;
		float d01 = v0x * v1x + v0y * v1y;
		float d11 = v1x * v1x + v1y * v1y;
		float d20 = v2x * v0x + v2y * v0y;
		float d21 = v2x * v1x + v2y * v1y;
		float denom = d00 * d11 - d01 * d01;
		if (fabsf(denom) < 0.0000001f)
			continue;

		float v = (d11 * d20 - d01 * d21) / denom;
		float w = (d00 * d21 - d01 * d20) / denom;
		float u = 1.0f - v - w;
		if (u < -0.02f || v < -0.02f || w < -0.02f)
			continue;
		if (u > 1.02f || v > 1.02f || w > 1.02f)
			continue;

		float support_z = (u * a[2] + v * b[2] + w * c[2]) / collector.collect_mul_z;
		if (support_z > max_height)
			continue;
		float drop = reference_z - support_z;
		if (drop < -0.75f || drop > max_drop)
			continue;
		if (!found || support_z > best_height)
		{
			best_height = support_z;
			found = true;
		}
	}

	for (const MpCollisionBox& box : collector.boxes)
	{
		if (!box.support_top)
			continue;
		if (!box.ContainsSupportXY(x, y))
			continue;
		const float support_z = box.bmax[2] / collector.collect_mul_z;
		if (support_z > max_height)
			continue;
		float drop = reference_z - support_z;
		if (drop < -0.75f || drop > max_drop)
			continue;
		if (!found || support_z > best_height)
		{
			best_height = support_z;
			found = true;
		}
	}

	if (found && out_height)
		*out_height = best_height;
	return found;
}

static float MpStepLength2(float x, float y)
{
	return sqrtf(x * x + y * y);
}
}

// FL-4137 #21: MpStepResolvePlacedBlockSupport (W5) DELETED.
//
// W5 was a second support owner that iterated env.placed_blocks[] directly
// after MpStepOnce's main sweep + soup support snap, reimplementing side push,
// auto-step climb, and Z resolution for placed blocks specifically. It existed
// only because placed blocks were never in the soup as proper World mesh
// instances. Per fix-attempt #14's spec ("Minecraft-like engines keep blocks
// in the same collision/support world used by terrain/world meshes — they do
// not maintain a parallel terrain-only support clamp and a parallel
// placed-block support hack"), this entire function is the parallel hack the
// spec forbid. Re-adding it is a regression.
//
// Replacement (FL-4137 #25 Tier 1): blocks register as server-owned world
// entities. Their CollisionBody boxes enter the same MpSoupCollector owner as
// terrain/AKM data. Side push, support snap, and auto-step climb are then
// handled by MpStepOnce's existing logic at the support_snap_applied gate.
//
// FL-4137 #25 ECS REFRAME (2026-05-28): Phase 2's mesh-inst approach is
// itself getting collapsed. Headed operator proof falsified the mesh-inst
// path 5 ways (cannot stand / crash / yellow halo / cannot pickup / fly-drop
// fails). Tier 1 replacement: ServerWorldEntityRegistry holds the placed
// block as an Entity with CollisionBody + SupportSurface + Interactable
// components. No mesh proxy is loaded, no Inst is created. mp_step's soup
// collector pulls the CollisionBody boxes directly from the registry. Side
// push / support snap / auto-step still live in MpStepOnce, but they read
// component data, not Inst metadata.

MpStepState MpStepFromPhysicsState(const PhysicsFullState* state, const float impulse[2])
{
	MpStepState out = {};
	if (!state)
		return out;
	memcpy(out.pos, state->pos, sizeof(out.pos));
	memcpy(out.vel, state->vel, sizeof(out.vel));
	out.yaw = state->yaw;
	out.yaw_vel = state->yaw_vel;
	out.player_dir = state->player_dir;
	out.player_stp = state->player_stp;
	out.slope = state->slope;
	out.accum_contact = state->accum_contact;
	out.mat = state->mat;
	out.water = state->water;
	if (impulse)
	{
		out.impulse[0] = impulse[0];
		out.impulse[1] = impulse[1];
	}
	return out;
}

void MpStepToPhysicsState(const MpStepState* state, PhysicsFullState* out, uint64_t stamp)
{
	if (!state || !out)
		return;
	memset(out, 0, sizeof(*out));
	out->stamp = stamp;
	out->mat = state->mat;
	out->water = state->water;
	memcpy(out->pos, state->pos, sizeof(out->pos));
	memcpy(out->vel, state->vel, sizeof(out->vel));
	out->player_dir = state->player_dir;
	out->player_stp = state->player_stp;
	out->yaw = state->yaw;
	out->yaw_vel = state->yaw_vel;
	out->slope = state->slope;
	out->accum_contact = state->accum_contact;
}

void MpStepApplyStateToIO(const MpStepState* state, const MpStepResult* result, PhysicsIO* io)
{
	if (!state || !io)
		return;
	memcpy(io->pos, state->pos, sizeof(io->pos));
	io->yaw = state->yaw;
	io->player_dir = state->player_dir;
	io->player_stp = state->player_stp;
	io->grounded = result ? result->grounded : (state->accum_contact >= 1.0f);
	io->water = state->water;
	io->x_impulse = state->impulse[0];
	io->y_impulse = state->impulse[1];
	io->dt = (int)PHYSICS_STEP_US;
}

float MpStepWorldRadiusForMount(uint8_t mount)
{
	const float radius_cells = mount ? kMpMountRadiusCells : kMpBaseRadiusCells;
	const float patch_cells = 3.0f * HEIGHT_CELLS;
	const float world_patch = (float)VISUAL_CELLS;
	return radius_cells / patch_cells * world_patch;
}

// FL-4137 #25: MpStepBuildEnv consumes the server-owned world entity registry
// for placed-block CollisionBody/SupportSurface data. Re-adding the old
// placed_blocks/placed_block_count args is a regression.
MpStepEnv MpStepBuildEnv(Terrain* terrain,
	World* world,
	const ServerWorldEntityRegistry* world_entities,
	uint64_t stamp_us,
	float water_level,
	uint8_t mount)
{
	MpStepEnv env = {};
	env.terrain = terrain;
	env.world = world;
	env.world_entities = world_entities;
	env.stamp_us = stamp_us;
	env.water_level = water_level;
	env.xy_speed = kMpBaseXYSpeed;
	env.radius_cells = mount ? kMpMountRadiusCells : kMpBaseRadiusCells;
	env.patch_cells = 3.0f * HEIGHT_CELLS;
	env.world_radius = MpStepWorldRadiusForMount(mount);
	float height_cells = mount ? kMpMountHeightCells : kMpBaseHeightCells;
	env.world_height = height_cells * 2.0f / 3.0f / (float)cos(30.0 * M_PI / 180.0) * HEIGHT_SCALE;
	env.mount = mount;
	return env;
}

MpStepState MpStepOnce( // FL-2957: physics step — tick cost owner
	const MpStepState& prev,
	const MpStepInput& input,
	const MpStepEnv& env,
	MpStepResult* out)
{
	const uint64_t step_wall_start_us = a3dGetTime();

	MpStepResult result = {};
	result.prev_stp = prev.player_stp;
	result.next_stp = prev.player_stp;
	result.mat = prev.mat;

	MpStepState next = prev;
	next.yaw = MpWrapYaw(input.yaw);
	next.yaw_vel = 0.0f;
	next.water = env.water_level;

	const float dt = (float)PHYSICS_STEP_US * (60.0f / 1000000.0f);
	float xy_len = MpStepLength2(input.x_force, input.y_force);
	int ix = 0;
	int iy = 0;
	float move_dx_world = 0.0f;
	float move_dy_world = 0.0f;

	if (input.x_force < 0.0f) ix--;
	if (input.x_force > 0.0f) ix++;
	if (input.y_force > 0.0f) iy++;
	if (input.y_force < 0.0f) iy--;

	if (xy_len < 0.01f)
	{
		// FL-3858 ACTIVE: Idle facing now preserves last move direction instead
		// of resetting to camera yaw (pre-complaint behavior: next.player_dir =
		// next.yaw). This change was coupled with the atan2 argument swap in
		// FacingWorldDirFromWorldVector, making the two changes hard to untangle.
		// If cardinal movement rows are broken, reverting the world-vector math
		// AND this idle change together (back to 60fb2c33 convention) is the
		// correct first step; idle preservation can be re-added as a separate
		// single-variable change afterward.
		// Preserve the last move-facing direction while idle. Resetting
		// authoritative facing to camera yaw on zero-input frames is what caused
		// the original "stop moving and immediately face the camera/front row"
		// symptom. TERM++ stays sane because its local path preserves the last
		// move-facing direction instead.
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

		const float yaw_rad = (float)(next.yaw * (M_PI / 180.0));
		move_dx_world = dx * cosf(yaw_rad) - dy * sinf(yaw_rad);
		move_dy_world = dx * sinf(yaw_rad) + dy * cosf(yaw_rad);

		// Active movement facing: shared helper prevents SP/MP convention
		// drift. See facing_space.h::FacingMovementStep.
		// FL-3858: This is a mechanical extraction only — formula, smoothing,
		// and snap threshold are unchanged. Does NOT close FL-3858.
		next.player_dir = FacingMovementStep(next.player_dir, move_dx_world, move_dy_world);
	}

	if (ix || iy)
	{
		float cs = cosf(next.slope);
		next.vel[0] += dt * move_dx_world * cs;
		next.vel[1] += dt * move_dy_world * cs;
	}

	float sqr_vel_xy = next.vel[0] * next.vel[0] + next.vel[1] * next.vel[1];
	float xy_vel = 0.0f;
	int prev_step = (next.player_stp + 3 * 1024) & (8 * 1024 - 1);
	if (sqr_vel_xy < 1.0f && !ix && !iy)
	{
		next.vel[0] = 0.0f;
		next.vel[1] = 0.0f;
		if (env.mount < 2)
			next.player_stp = -1;
	}
	else
	{
		float xy_limit = 27.0f - 17.0f * (next.water - next.pos[2]) / env.world_height;
		float lim = 27.0f * xy_len * xy_len * xy_len;
		if (xy_limit < 10.0f)
			xy_limit = 10.0f;
		if (xy_limit > lim)
			xy_limit = lim;
		// xy_limit is a linear speed cap; square it for comparison against sqr_vel_xy
		// (squared XY speed). Previously compared linear against squared, which made the
		// effective cap sqrt(27) = 5.2 units/sec instead of the intended 27 units/sec.
		float sqr_xy_limit = xy_limit * xy_limit;
		if (sqr_vel_xy > sqr_xy_limit)
		{
			float n = sqrtf(sqr_xy_limit / sqr_vel_xy);
			sqr_vel_xy = sqr_xy_limit;
			next.vel[0] *= n;
			next.vel[1] *= n;
		}

		if (next.player_stp < 0)
			next.player_stp = 0;

		prev_step = (next.player_stp + 3 * 1024) & (8 * 1024 - 1);
		xy_vel = sqrtf(sqr_vel_xy);
		if (env.mount > 1)
			next.player_stp = (~(1 << 31)) & (next.player_stp + (int)(24.0f * xy_vel));
		else
			next.player_stp = (~(1 << 31)) & (next.player_stp + (int)(64.0f * xy_vel));

		float vel_damp = powf(kMpGroundDecay, dt);
		next.vel[0] *= vel_damp;
		next.vel[1] *= vel_damp;
	}

	float wave = 2.0f * (int)((env.stamp_us >> 10) & 0x7FF) * (float)M_PI / 0x800;
	float ampl = (ix || iy) ? 0.1f : 0.05f;
	float cnt = 0.78f + ampl * sinf(wave);
	float acc = (next.water - (next.pos[2] + cnt * env.world_height)) / (2.0f * cnt * env.world_height);
	if (acc < -cnt)
		acc = -cnt;
	if (acc > 1.0f - cnt)
		acc = 1.0f - cnt;

	if (input.fly)
	{
		float z_acc = input.z_force;
		if (fabsf(z_acc) > 0.001f)
			next.vel[2] += dt * z_acc;
		float z_damp = powf(kMpGroundDecay, dt);
		next.vel[2] *= z_damp;
	}
	else
	{
		next.vel[2] += dt * acc;
	}

	float res = (next.water - next.pos[2]) / env.world_height;
	if (res < 0.0f)
		res = 0.0f;
	if (res > 1.0f)
		res = 1.0f;
	result.in_water = res;
	float xy_res = powf(1.0f - 0.5f * res, dt);
	float z_res = powf(1.0f - 0.1f * res, dt);
	if (env.mount > 1 && next.vel[2] < 0.0f)
		z_res = powf(1.0f - 0.1f, dt);
	next.vel[0] *= xy_res;
	next.vel[1] *= xy_res;
	next.vel[2] *= z_res;

	next.vel[0] += next.impulse[0];
	next.vel[1] += next.impulse[1];
	if (fabsf(next.impulse[0]) + fabsf(next.impulse[1]) > 1.0f && next.vel[2] > 0.0f)
		next.vel[2] = 0.0f;
	next.impulse[0] *= 0.5f;
	next.impulse[1] *= 0.5f;

	// Phase: pre-collect (input integration -> dx/dy ready).
	result.debug_step_pre_collect_us = a3dGetTime() - step_wall_start_us;

	MpSoupCollector collector;
	float contact_normal_z = 0.0f;
	int material_votes[6] = { 0 };
	float dx = dt * next.vel[0];
	float dy = dt * next.vel[1];
	const uint64_t collect_build_start_us = a3dGetTime();
	collector.Build(env, next.pos, dx, dy);
	result.debug_step_collect_build_us = a3dGetTime() - collect_build_start_us;
	result.debug_soup_items = (uint32_t)collector.soup.size();
	result.debug_collect_world_us = collector.collect_world_us;
	result.debug_collect_terrain_us = collector.collect_terrain_us;
	result.debug_collect_us = collector.collect_total_us;
	result.debug_collect_mesh_us = collector.collect_mesh_us;
	result.debug_collect_mesh_instances = collector.mesh_instances;
	result.debug_collect_mesh_faces = collector.mesh_faces;
	result.debug_collect_world_callbacks = collector.world_callbacks;
	result.debug_collect_terrain_tris = collector.terrain_tris;
	result.debug_collect_mesh_query_us_total = collector.mesh_query_us_total;
	result.debug_collect_mesh_query_us_max = collector.mesh_query_us_max;
	result.debug_collect_mesh_query_overhead_us_total = collector.mesh_query_overhead_us_total;
	result.debug_collect_mesh_query_overhead_us_max = collector.mesh_query_overhead_us_max;
	result.debug_collect_mesh_face_cb_us_total = collector.mesh_face_cb_us_total;
	result.debug_collect_mesh_face_cb_us_max = collector.mesh_face_cb_us_max;
	result.debug_collect_mesh_face_cb_calls = collector.mesh_face_cb_calls;
	result.debug_collect_mesh_face_cb_accepts = collector.mesh_face_cb_accepts;
	result.debug_collect_mesh_face_cb_reject_visual = collector.mesh_face_cb_reject_visual;
	result.debug_collect_mesh_face_cb_reject_alpha = collector.mesh_face_cb_reject_alpha;
	result.debug_collect_mesh_face_cb_accept_us_total = collector.mesh_face_cb_accept_us_total;
	result.debug_collect_mesh_face_cb_accept_us_max = collector.mesh_face_cb_accept_us_max;
	result.debug_collect_mesh_face_cb_push_us_total = collector.mesh_face_cb_push_us_total;
	result.debug_collect_mesh_face_cb_push_us_max = collector.mesh_face_cb_push_us_max;
	result.debug_collect_mesh_face_cb_material_us_total = collector.mesh_face_cb_material_us_total;
	result.debug_collect_mesh_face_cb_material_us_max = collector.mesh_face_cb_material_us_max;
	result.debug_collect_mesh_face_cb_transform_us_total = collector.mesh_face_cb_transform_us_total;
	result.debug_collect_mesh_face_cb_transform_us_max = collector.mesh_face_cb_transform_us_max;
	result.debug_collect_mesh_face_cb_normal_us_total = collector.mesh_face_cb_normal_us_total;
	result.debug_collect_mesh_face_cb_normal_us_max = collector.mesh_face_cb_normal_us_max;
	result.debug_collect_mesh_face_cb_bbox_us_total = collector.mesh_face_cb_bbox_us_total;
	result.debug_collect_mesh_face_cb_bbox_us_max = collector.mesh_face_cb_bbox_us_max;
	result.debug_collect_mesh_faces_reported_total = collector.mesh_faces_reported_total;
	result.debug_collect_mesh_faces_reported_max = collector.mesh_faces_reported_max;
	result.debug_collect_mesh_face_cb_us_max_inst_id = collector.mesh_face_cb_us_max_inst_id;
	result.debug_collect_mesh_face_cb_us_max_mesh_id = collector.mesh_face_cb_us_max_mesh_id;
	result.debug_collect_mesh_face_cb_us_max_mesh_faces = collector.mesh_face_cb_us_max_mesh_faces;
	result.debug_collect_mesh_face_cb_us_max_face_ordinal = collector.mesh_face_cb_us_max_face_ordinal;
	result.debug_collect_mesh_face_cb_us_max_accept = collector.mesh_face_cb_us_max_accept;
	result.debug_collect_mesh_face_cb_us_max_visual = collector.mesh_face_cb_us_max_visual;
	result.debug_collect_mesh_face_cb_us_max_soup_index = collector.mesh_face_cb_us_max_soup_index;
	result.debug_collect_mesh_face_cb_us_max_inst_story_id = collector.mesh_face_cb_us_max_inst_story_id;
	result.debug_collect_mesh_face_cb_us_max_inst_flags = collector.mesh_face_cb_us_max_inst_flags;
	result.debug_collect_mesh_face_cb_us_max_inst_name_hash = collector.mesh_face_cb_us_max_inst_name_hash;
	result.debug_collect_mesh_face_cb_us_max_mesh_name_hash = collector.mesh_face_cb_us_max_mesh_name_hash;
	result.debug_collect_mesh_face_cb_us_max_inst_bbox_cx_milli = collector.mesh_face_cb_us_max_inst_bbox_cx_milli;
	result.debug_collect_mesh_face_cb_us_max_inst_bbox_cy_milli = collector.mesh_face_cb_us_max_inst_bbox_cy_milli;
	result.debug_collect_mesh_face_cb_us_max_inst_bbox_cz_milli = collector.mesh_face_cb_us_max_inst_bbox_cz_milli;
	result.debug_collect_mesh_face_cb_us_max_inst_bbox_diag_milli = collector.mesh_face_cb_us_max_inst_bbox_diag_milli;
	result.debug_collect_mesh_face_cb_us_max_query_cx_milli = collector.mesh_face_cb_us_max_query_cx_milli;
	result.debug_collect_mesh_face_cb_us_max_query_cy_milli = collector.mesh_face_cb_us_max_query_cy_milli;
	result.debug_collect_mesh_face_cb_us_max_query_radius_milli = collector.mesh_face_cb_us_max_query_radius_milli;
	result.debug_collect_mesh_face_cb_us_max_query_bbox_dist_milli = collector.mesh_face_cb_us_max_query_bbox_dist_milli;
	result.debug_collect_mesh_face_cb_us_max_query_bbox_overlap_milli = collector.mesh_face_cb_us_max_query_bbox_overlap_milli;
	result.debug_collect_mesh_soup_reallocs = collector.soup_reallocs;
	result.debug_collect_mesh_soup_capacity_max = collector.soup_capacity_max;
	result.debug_collect_mesh_soup_bytes_max = collector.soup_bytes_max;
	result.debug_collect_mesh_soup_bytes_growth_total = collector.soup_bytes_growth_total;
	result.debug_collect_mesh_bbox_would_skip = collector.bbox_would_skip;
	result.debug_collect_mesh_bbox_would_skip_faces = collector.bbox_would_skip_faces;
	result.debug_collect_mesh_cache_hits = collector.mesh_cache_hits;
	result.debug_collect_mesh_cache_misses = collector.mesh_cache_misses;
	result.debug_collect_mesh_cache_items = collector.mesh_cache_items;
	result.debug_collect_world_bsp_tests = collector.world_bsp_tests;
	result.debug_collect_world_bsp_nodes = collector.world_bsp_nodes;
	result.debug_collect_world_bsp_insts = collector.world_bsp_insts;
	result.debug_soup_capped = collector.capped ? 1u : 0u;
	result.debug_mesh_per_mesh_cap_hits = collector.mesh_per_mesh_cap_hits;
	result.debug_mesh_bbox_skips = collector.mesh_bbox_skips;
	result.debug_support_priority_callbacks = collector.support_priority_callbacks;

	auto add_collision_debug_sample = [&](uint8_t source, uint8_t flags,
		uint16_t item_id, uint64_t entity_id, uint64_t inst_id, uint64_t mesh_id,
		uint32_t face_ordinal, const float bmin[3], const float bmax[3],
		const float normal[3]) -> void
	{
		if (result.debug_collision_sample_count >= COLLISION_DEBUG_SAMPLE_MAX)
			return;
		STRUCT_BRC_COLLISION_DEBUG_SAMPLE& sample =
			result.debug_collision_samples[result.debug_collision_sample_count++];
		sample.source = source;
		sample.flags = flags;
		sample.item_id = item_id;
		sample.entity_id = entity_id;
		sample.inst_id = inst_id;
		sample.mesh_id = mesh_id;
		sample.face_ordinal = face_ordinal;
		memcpy(sample.bmin, bmin, sizeof(sample.bmin));
		memcpy(sample.bmax, bmax, sizeof(sample.bmax));
		memcpy(sample.normal, normal, sizeof(sample.normal));
	};
	for (const MpCollisionBox& box : collector.boxes)
	{
		const uint8_t source = box.source_item_id != 0
			? MP_SUPPORT_PLACED_BLOCK
			: ((box.source_inst_id || box.source_mesh_id) ? MP_SUPPORT_WORLD_MESH : MP_SUPPORT_TERRAIN);
		float world_bmin[3] = {
			box.bmin[0] / collector.collect_mul_xy,
			box.bmin[1] / collector.collect_mul_xy,
			box.bmin[2] / collector.collect_mul_z,
		};
		float world_bmax[3] = {
			box.bmax[0] / collector.collect_mul_xy,
			box.bmax[1] / collector.collect_mul_xy,
			box.bmax[2] / collector.collect_mul_z,
		};
		float normal[3] = { 0.0f, 0.0f, box.support_top ? 1.0f : 0.0f };
		uint8_t flags = 0;
		if (box.support_top)
			flags |= COLLISION_DEBUG_FLAG_SUPPORT_TOP;
		if (box.support_only)
			flags |= COLLISION_DEBUG_FLAG_SUPPORT_ONLY;
		add_collision_debug_sample(source, flags, box.source_item_id, box.source_entity_id,
			box.source_inst_id, box.source_mesh_id, box.source_face_ordinal,
			world_bmin, world_bmax, normal);
	}
	for (const MpSoupItem& item : collector.soup)
	{
		const uint8_t source = (item.source_inst_id || item.source_mesh_id)
			? MP_SUPPORT_WORLD_MESH
			: MP_SUPPORT_TERRAIN;
		float world_bmin[3] = {
			item.bbox_min[0] / collector.collect_mul_xy,
			item.bbox_min[1] / collector.collect_mul_xy,
			item.bbox_min[2] / collector.collect_mul_z,
		};
		float world_bmax[3] = {
			item.bbox_max[0] / collector.collect_mul_xy,
			item.bbox_max[1] / collector.collect_mul_xy,
			item.bbox_max[2] / collector.collect_mul_z,
		};
		uint8_t flags = 0;
		if (item.nrm[2] > 0.25f)
			flags |= COLLISION_DEBUG_FLAG_SUPPORT_TOP;
		add_collision_debug_sample(source, flags, 0, 0,
			item.source_inst_id, item.source_mesh_id, item.source_face_ordinal,
			world_bmin, world_bmax, item.nrm);
		if (result.debug_collision_sample_count >= COLLISION_DEBUG_SAMPLE_MAX)
			break;
	}

	float sphere_pos[3] =
	{
		next.pos[0] * collector.collect_mul_xy,
		next.pos[1] * collector.collect_mul_xy,
		(next.pos[2] + env.world_height * 0.5f) * collector.collect_mul_z,
	};
	float sphere_vel[3] =
	{
		env.xy_speed * next.vel[0] * dt * collector.collect_mul_xy,
		env.xy_speed * next.vel[1] * dt * collector.collect_mul_xy,
		next.vel[2] * dt * collector.collect_mul_z,
	};
	result.debug_sweep_z_mag = fabsf(sphere_vel[2]);

	const float xy_thresh = 0.002f;
	const float z_thresh = 0.001f;
		int iters_left = 10;
		bool jump_requested = input.jump;
		bool step_assist_attempted = false;
		const bool fl4137_launch_diag = MpStepEnvFlag("FL4137_LAUNCH_DIAG");
		const char* fl4137_last_collision_kind = "none";
		float fl4137_last_collision_time = 2.0f;
		float fl4137_last_collision_pos[3] = { 0.0f, 0.0f, 0.0f };
		float fl4137_last_slide_normal[3] = { 0.0f, 0.0f, 0.0f };
		uint8_t fl4137_last_box_support_top = 0;
		uint16_t fl4137_last_box_item_id = 0;
		uint64_t fl4137_last_box_entity_id = 0;
		float fl4137_last_box_bmin[3] = { 0.0f, 0.0f, 0.0f };
		float fl4137_last_box_bmax[3] = { 0.0f, 0.0f, 0.0f };
		uint64_t fl4137_last_item_inst_id = 0;
		uint64_t fl4137_last_item_mesh_id = 0;
		float fl4137_last_item_nrm_z = 0.0f;

	// Phase: post-collect setup (sphere init + sweep loop setup).
	result.debug_step_post_collect_setup_us = a3dGetTime() - collect_build_start_us - result.debug_step_collect_build_us;

	// Phase: sweep wall time (regardless of debug_sweep_total_us per-iter accounting).
	const uint64_t sweep_wall_start_us = a3dGetTime();
	while (fabsf(sphere_vel[0]) > xy_thresh || fabsf(sphere_vel[1]) > xy_thresh || fabsf(sphere_vel[2]) > z_thresh)
	{
		const uint64_t iter_start_us = a3dGetTime();
		const uint32_t iter_checks_before = result.debug_collision_checks;

		result.debug_sweep_ran = true;
			result.debug_sweep_iterations++;
			const MpSoupItem* collision_item = nullptr;
			const MpCollisionBox* collision_box = nullptr;
			float collision_time = 2.0f;
			float collision_pos[3] = { 0.0f, 0.0f, 0.0f };

		const uint64_t narrow_start_us = a3dGetTime();
		uint32_t narrow_item_index = 0;
		uint32_t narrow_slowest_item_index = UINT32_MAX;
		const MpSoupItem* narrow_slowest_item = nullptr;
		uint64_t narrow_slowest_item_us = 0;
		for (const MpSoupItem& item : collector.soup)
		{
			if (item.RejectSweptSphereAABB(sphere_pos, sphere_vel))
			{
				result.debug_collision_broadphase_rejects++;
				narrow_item_index++;
				continue;
			}
			result.debug_collision_checks++;
			float contact_pos[3] = { 0.0f, 0.0f, 0.0f };
			const uint64_t item_start_us = MpSweepDiagTime();
			float time = item.CheckCollision(sphere_pos, sphere_vel, contact_pos);
			const uint64_t item_us = MpSweepDiagTime() - item_start_us;
			if (item_us > narrow_slowest_item_us)
			{
				narrow_slowest_item_us = item_us;
				narrow_slowest_item_index = narrow_item_index;
				narrow_slowest_item = &item;
			}
			narrow_item_index++;
			if (!isfinite(time) || time < 0.0f)
				continue;
			if (time >= collision_time)
				continue;

			float check[3] =
			{
				sphere_pos[0] + sphere_vel[0] * time - contact_pos[0],
				sphere_pos[1] + sphere_vel[1] * time - contact_pos[1],
				sphere_pos[2] + sphere_vel[2] * time - contact_pos[2],
			};
			float sqr_dist = DotProduct(check, check);
			if (!isfinite(sqr_dist) || fabsf(sqr_dist - 1.0f) > 0.001f)
				continue;

				collision_item = &item;
				collision_box = nullptr;
				collision_time = time;
				collision_pos[0] = contact_pos[0];
				collision_pos[1] = contact_pos[1];
				collision_pos[2] = contact_pos[2];
			}
			for (const MpCollisionBox& box : collector.boxes)
			{
				// FL-4137 #33 / FL-4128 regression fix (2026-05-29):
				// support_top is set by MpSoupCollector::FaceCollect for every
				// upward-facing AKM face (nrm.z > 0.25) — placed-block top slabs
				// AND building roofs/eaves/chamfered tops. Commit 4bfc91989
				// dropped the prior unconditional skip here to stop the FL-4137
				// placed-block side-approach launch; that broke FL-4128 building
				// collision (any building face with nrm.z > 0.25 fell out of the
				// sweep). Do not infer "placed block" from source ids here:
				// that repeats the same ownership bug. The only sweep-skipped
				// box class is the explicit placed-block top slab
				// (support_only=1), set at the two-box emission site.
				if (box.support_only)
					continue;
				if (box.RejectSweptSphereAABB(sphere_pos, sphere_vel))
				{
					result.debug_collision_broadphase_rejects++;
					continue;
				}
				result.debug_collision_checks++;
				float contact_pos[3] = { 0.0f, 0.0f, 0.0f };
				float time = box.CheckCollision(sphere_pos, sphere_vel, contact_pos);
				if (!isfinite(time) || time < 0.0f)
					continue;
				if (time >= collision_time)
					continue;
				collision_item = nullptr;
				collision_box = &box;
				collision_time = time;
				collision_pos[0] = contact_pos[0];
				collision_pos[1] = contact_pos[1];
				collision_pos[2] = contact_pos[2];
			}
			const uint64_t narrow_us = a3dGetTime() - narrow_start_us;
		result.debug_sweep_narrowphase_us_total += narrow_us;
		result.debug_sweep_narrowphase_us_last = narrow_us;
		if (narrow_us > result.debug_sweep_narrowphase_us_max)
		{
			result.debug_sweep_narrowphase_us_max = narrow_us;
			result.debug_sweep_narrowphase_us_max_item_index = narrow_slowest_item_index;
			if (narrow_slowest_item)
			{
				result.debug_sweep_narrowphase_us_max_item_material = (uint32_t)narrow_slowest_item->material;
				result.debug_sweep_narrowphase_us_max_item_inst_id = narrow_slowest_item->source_inst_id;
				result.debug_sweep_narrowphase_us_max_item_mesh_id = narrow_slowest_item->source_mesh_id;
				result.debug_sweep_narrowphase_us_max_item_mesh_faces = narrow_slowest_item->source_mesh_faces;
				result.debug_sweep_narrowphase_us_max_item_face_ordinal = narrow_slowest_item->source_face_ordinal;
				result.debug_sweep_narrowphase_us_max_item_inst_story_id = narrow_slowest_item->source_inst_story_id;
				result.debug_sweep_narrowphase_us_max_item_inst_flags = narrow_slowest_item->source_inst_flags;
				result.debug_sweep_narrowphase_us_max_item_inst_name_hash = narrow_slowest_item->source_inst_name_hash;
				result.debug_sweep_narrowphase_us_max_item_mesh_name_hash = narrow_slowest_item->source_mesh_name_hash;
				result.debug_sweep_narrowphase_us_max_item_nrm_z = narrow_slowest_item->nrm[2];
				const float bbox_dx = narrow_slowest_item->bbox_max[0] - narrow_slowest_item->bbox_min[0];
				const float bbox_dy = narrow_slowest_item->bbox_max[1] - narrow_slowest_item->bbox_min[1];
				const float bbox_dz = narrow_slowest_item->bbox_max[2] - narrow_slowest_item->bbox_min[2];
				result.debug_sweep_narrowphase_us_max_item_bbox_diag =
					sqrtf(bbox_dx * bbox_dx + bbox_dy * bbox_dy + bbox_dz * bbox_dz);
			}
		}

		const uint32_t iter_checks = result.debug_collision_checks - iter_checks_before;

			if (!collision_item && !collision_box)
			{
			sphere_pos[0] += sphere_vel[0];
			sphere_pos[1] += sphere_vel[1];
			sphere_pos[2] += sphere_vel[2];
			// Observability: per-iter accounting even on no-hit exit.
			{
				const uint64_t iter_us = a3dGetTime() - iter_start_us;
				result.debug_sweep_total_us += iter_us;
				result.debug_sweep_iter_us_last = iter_us;
				if (iter_us > result.debug_sweep_iter_us_max)
					result.debug_sweep_iter_us_max = iter_us;
				result.debug_sweep_iter_collision_checks_last = iter_checks;
				if (iter_checks > result.debug_sweep_iter_collision_checks_max)
					result.debug_sweep_iter_collision_checks_max = iter_checks;
				result.debug_sweep_iter_remaining_move_len_last =
					sqrtf(sphere_vel[0] * sphere_vel[0] + sphere_vel[1] * sphere_vel[1] + sphere_vel[2] * sphere_vel[2]);
				result.debug_sweep_iter_output_normal_z_last = 0.0f;
				result.debug_sweep_iter_earliest_t_last = 2.0f;
			}
			break;
		}

		result.debug_sweep_iter_hits++;
		result.debug_sweep_iter_earliest_t_last = collision_time;

		float full_step[3] =
		{
			sphere_vel[0] * collision_time,
			sphere_vel[1] * collision_time,
			sphere_vel[2] * collision_time
		};
		float slide_normal[3] =
		{
			sphere_pos[0] + full_step[0] - collision_pos[0],
			sphere_pos[1] + full_step[1] - collision_pos[1],
			sphere_pos[2] + full_step[2] - collision_pos[2]
		};

			result.debug_sweep_iter_output_normal_z_last = slide_normal[2];
			fl4137_last_collision_kind = collision_box ? "box" : (collision_item ? "soup" : "none");
			fl4137_last_collision_time = collision_time;
			fl4137_last_collision_pos[0] = collision_pos[0];
			fl4137_last_collision_pos[1] = collision_pos[1];
			fl4137_last_collision_pos[2] = collision_pos[2];
			fl4137_last_slide_normal[0] = slide_normal[0];
			fl4137_last_slide_normal[1] = slide_normal[1];
			fl4137_last_slide_normal[2] = slide_normal[2];
			if (collision_box)
			{
				fl4137_last_box_support_top = collision_box->support_top;
				fl4137_last_box_item_id = collision_box->source_item_id;
				fl4137_last_box_entity_id = collision_box->source_entity_id;
				memcpy(fl4137_last_box_bmin, collision_box->bmin, sizeof(fl4137_last_box_bmin));
				memcpy(fl4137_last_box_bmax, collision_box->bmax, sizeof(fl4137_last_box_bmax));
				result.debug_last_sweep_collision_source = collision_box->source_item_id != 0
					? MP_SUPPORT_PLACED_BLOCK
					: ((collision_box->source_inst_id || collision_box->source_mesh_id)
						? MP_SUPPORT_WORLD_MESH
						: MP_SUPPORT_TERRAIN);
				result.debug_last_sweep_collision_item_id = collision_box->source_item_id;
				result.debug_last_sweep_collision_entity_id = collision_box->source_entity_id;
				result.debug_last_sweep_collision_inst_id = collision_box->source_inst_id;
				result.debug_last_sweep_collision_mesh_id = collision_box->source_mesh_id;
				result.debug_last_sweep_collision_face_ordinal = collision_box->source_face_ordinal;
				result.debug_last_sweep_collision_bmin[0] = collision_box->bmin[0] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmin[1] = collision_box->bmin[1] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmin[2] = collision_box->bmin[2] / collector.collect_mul_z;
				result.debug_last_sweep_collision_bmax[0] = collision_box->bmax[0] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmax[1] = collision_box->bmax[1] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmax[2] = collision_box->bmax[2] / collector.collect_mul_z;
			}
			if (collision_item)
			{
				fl4137_last_item_inst_id = collision_item->source_inst_id;
				fl4137_last_item_mesh_id = collision_item->source_mesh_id;
				fl4137_last_item_nrm_z = collision_item->nrm[2];
				result.debug_last_sweep_collision_source =
					(collision_item->source_inst_id || collision_item->source_mesh_id)
						? MP_SUPPORT_WORLD_MESH
						: MP_SUPPORT_TERRAIN;
				result.debug_last_sweep_collision_item_id = 0;
				result.debug_last_sweep_collision_entity_id = 0;
				result.debug_last_sweep_collision_inst_id = collision_item->source_inst_id;
				result.debug_last_sweep_collision_mesh_id = collision_item->source_mesh_id;
				result.debug_last_sweep_collision_face_ordinal = collision_item->source_face_ordinal;
				result.debug_last_sweep_collision_bmin[0] = collision_item->bbox_min[0] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmin[1] = collision_item->bbox_min[1] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmin[2] = collision_item->bbox_min[2] / collector.collect_mul_z;
				result.debug_last_sweep_collision_bmax[0] = collision_item->bbox_max[0] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmax[1] = collision_item->bbox_max[1] / collector.collect_mul_xy;
				result.debug_last_sweep_collision_bmax[2] = collision_item->bbox_max[2] / collector.collect_mul_z;
			}
			result.debug_last_sweep_collision_side = fabsf(slide_normal[2]) < 0.5f ? 1 : 0;
			result.debug_last_sweep_collision_normal[0] = slide_normal[0];
			result.debug_last_sweep_collision_normal[1] = slide_normal[1];
			result.debug_last_sweep_collision_normal[2] = slide_normal[2];

				const int collision_material = collision_item
					? collision_item->material
				: (collision_box ? collision_box->material : -1);
			if (collision_material >= 0 && collision_material < 6)
				material_votes[collision_material]++;

		float full_len = sqrtf(full_step[0] * full_step[0] + full_step[1] * full_step[1] + full_step[2] * full_step[2]);
		float ratio = 0.0f;
		if (full_len > 0.01f)
			ratio = (full_len - 0.01f) / full_len;
		sphere_pos[0] += full_step[0] * ratio;
		sphere_pos[1] += full_step[1] * ratio;
		sphere_pos[2] += full_step[2] * ratio;

		float remain = 1.0f - collision_time;
		if (remain >= 0.99f)
			remain = 0.99f;
		sphere_vel[0] *= remain;
		sphere_vel[1] *= remain;
		sphere_vel[2] *= remain;

		// Jump authority comes from input.jump. For grounded walkers that hit a
		// steep face very early, lift only onto support that is provably within
		// the bounded implicit step height instead of synthesizing a free jump.
		const bool low_step_assist_candidate =
			!step_assist_attempted &&
			!input.jump && !input.fly && env.mount < 2 &&
			prev.accum_contact >= 1.0f &&
			collision_time < 0.2f && slide_normal[2] < 0.8f;
		if (low_step_assist_candidate)
		{
			step_assist_attempted = true;
			const float current_world_z =
				sphere_pos[2] / collector.collect_mul_z - env.world_height * 0.5f;
			const float probe_world_x = (sphere_pos[0] + sphere_vel[0]) / collector.collect_mul_xy;
			const float probe_world_y = (sphere_pos[1] + sphere_vel[1]) / collector.collect_mul_xy;
			float step_support_height = 0.0f;
			const bool step_support_found = MpStepSupportHeightInRange(
				collector,
				probe_world_x,
				probe_world_y,
				prev.pos[2] - kMpSupportSnapEpsilon + kMpStepAssistMinRise,
				prev.pos[2] + kMpMaxImplicitStepUp,
				&step_support_height);
			const float lifted_step_height = step_support_height + kMpSupportSnapEpsilon;
			if (step_support_found &&
				lifted_step_height > current_world_z + kMpStepAssistMinRise)
			{
				sphere_pos[2] =
					(lifted_step_height + env.world_height * 0.5f) * collector.collect_mul_z;
				if (sphere_vel[2] < 0.0f)
					sphere_vel[2] = 0.0f;
				contact_normal_z = fmaxf(contact_normal_z, 1.0f);
				if (!--iters_left)
					break;
				continue;
			}
		}

		float project = DotProduct(sphere_vel, slide_normal);
		sphere_vel[0] -= slide_normal[0] * project;
		sphere_vel[1] -= slide_normal[1] * project;
		sphere_vel[2] -= slide_normal[2] * project;
		result.debug_sweep_iter_deflection_count++;

		contact_normal_z = fmaxf(contact_normal_z, slide_normal[2]);

		// Observability: per-iter accounting (hit path).
		{
			const uint64_t iter_us = a3dGetTime() - iter_start_us;
			result.debug_sweep_total_us += iter_us;
			result.debug_sweep_iter_us_last = iter_us;
			if (iter_us > result.debug_sweep_iter_us_max)
				result.debug_sweep_iter_us_max = iter_us;
			result.debug_sweep_iter_collision_checks_last = iter_checks;
			if (iter_checks > result.debug_sweep_iter_collision_checks_max)
				result.debug_sweep_iter_collision_checks_max = iter_checks;
			result.debug_sweep_iter_remaining_move_len_last =
				sqrtf(sphere_vel[0] * sphere_vel[0] + sphere_vel[1] * sphere_vel[1] + sphere_vel[2] * sphere_vel[2]);
		}

		if (!--iters_left)
			break;
	}
	result.debug_step_sweep_wall_us = a3dGetTime() - sweep_wall_start_us;

	const uint64_t post_sweep_start_us = a3dGetTime();
	float pos[3] =
	{
		sphere_pos[0] / collector.collect_mul_xy,
		sphere_pos[1] / collector.collect_mul_xy,
		sphere_pos[2] / collector.collect_mul_z - env.world_height * 0.5f
	};
	result.debug_resolved_pos_z = pos[2];
	next.vel[0] = (pos[0] - next.pos[0]) / (env.xy_speed * dt);
	next.vel[1] = (pos[1] - next.pos[1]) / (env.xy_speed * dt);
	next.vel[2] = (pos[2] - next.pos[2]) / dt;
	float adz = fmaxf(0.0f, next.vel[2]) / HEIGHT_SCALE * 4.0f;
	float adxy = env.xy_speed * sqrtf(next.vel[0] * next.vel[0] + next.vel[1] * next.vel[1]);
	next.slope = atan2f(adz, adxy);
	// Removed: dead-code velocity blend (HIGH-3). The factors were 1.0/0.0 — pure
	// identity — making org_vel unreachable. Keeping the lines risked a future
	// blend-factor change diverging client from server silently.

	// FL-641 / FL-1639: write back whenever intent exists OR collision/support resolution
	// materially moved the actor. Tightening this to input-only revives the spent pattern
	// where idle-frame recovery is dropped and remote Z jitter becomes visible gameplay.
	if (ix || iy || contact_normal_z <= 0.0f ||
		fabsf(next.vel[0]) > 0.1f || fabsf(next.vel[1]) > 0.1f || fabsf(next.vel[2]) > 1.6f)
	{
		result.debug_writeback_applied = true;
		next.pos[0] = pos[0];
		next.pos[1] = pos[1];
		next.pos[2] = pos[2];
	}
	else
	{
		next.vel[0] = 0.0f;
		next.vel[1] = 0.0f;
		next.vel[2] = 0.0f;
	}

	// Phase: post-sweep resolve + writeback gate.
	result.debug_step_post_sweep_us = a3dGetTime() - post_sweep_start_us;

		float support_height = 0.0f;
		MpSupportHit support_hit = {};
		const float fl4137_post_sweep_pos_z = next.pos[2];
		result.debug_support_input_gate = !input.fly;
	// FL-2957 attempt #30: support/floor probes must be able to rebuild around the
	// resolved XY after sweep. The pre-step collector can miss support entirely when
	// collision resolution moves outside the original soup footprint.
	// LINEAGE_JSON: {"fl":"FL-2957","attempt":30,"commit":"pending","attempt_total":30,"closed":0,"what":"resolved-xy soup rebuild retry before support/floor miss is accepted","worked_if":"support_retry_rebuilt>0 && support_retry_found>0 && reject_grounded/reject_support_z drop on next headed run","failed_if":"support_retry_rebuilt=0 in bad window or retry still finds no support while lag owner persists","result":"pending","run":"pending"}
	MpSoupCollector support_retry_collector;
	const MpSoupCollector* support_collector = &collector;
	// First try the normal narrow search radius for autojump-style recovery.
	const uint64_t support_probe_start_us = a3dGetTime();
	bool support_found = MpStepSupportHitAt(*support_collector, next.pos[0], next.pos[1], next.pos[2],
		HEIGHT_SCALE * 2.0f, &support_hit);
	if (support_found)
		support_height = support_hit.z;
	result.debug_step_support_probe_us = a3dGetTime() - support_probe_start_us;
	if (!support_found)
	{
#ifdef FL2957_DIAG_DISABLE_SUPPORT_RETRY
		result.debug_step_support_retry_build_us = 0;
#else
		const uint64_t retry_build_start_us = a3dGetTime();
		support_retry_collector.Build(env, next.pos, 0.0f, 0.0f);
		result.debug_step_support_retry_build_us = a3dGetTime() - retry_build_start_us;
		result.debug_support_retry_soup_items = (uint32_t)support_retry_collector.soup.size();
		result.debug_support_retry_collect_world_us = support_retry_collector.collect_world_us;
		result.debug_support_retry_collect_terrain_us = support_retry_collector.collect_terrain_us;
		result.debug_support_retry_collect_us = support_retry_collector.collect_total_us;
		result.debug_support_retry_collect_mesh_us = support_retry_collector.collect_mesh_us;
		result.debug_support_retry_collect_mesh_instances = support_retry_collector.mesh_instances;
		result.debug_support_retry_collect_mesh_faces = support_retry_collector.mesh_faces;
		result.debug_support_retry_collect_world_callbacks = support_retry_collector.world_callbacks;
		result.debug_support_retry_collect_terrain_tris = support_retry_collector.terrain_tris;
		result.debug_support_retry_collect_mesh_query_us_total = support_retry_collector.mesh_query_us_total;
		result.debug_support_retry_collect_mesh_query_us_max = support_retry_collector.mesh_query_us_max;
		result.debug_support_retry_collect_mesh_query_overhead_us_total = support_retry_collector.mesh_query_overhead_us_total;
		result.debug_support_retry_collect_mesh_query_overhead_us_max = support_retry_collector.mesh_query_overhead_us_max;
		result.debug_support_retry_collect_mesh_face_cb_us_total = support_retry_collector.mesh_face_cb_us_total;
		result.debug_support_retry_collect_mesh_face_cb_us_max = support_retry_collector.mesh_face_cb_us_max;
		result.debug_support_retry_collect_mesh_face_cb_calls = support_retry_collector.mesh_face_cb_calls;
		result.debug_support_retry_collect_mesh_face_cb_accepts = support_retry_collector.mesh_face_cb_accepts;
		result.debug_support_retry_collect_mesh_face_cb_reject_visual = support_retry_collector.mesh_face_cb_reject_visual;
		result.debug_support_retry_collect_mesh_face_cb_reject_alpha = support_retry_collector.mesh_face_cb_reject_alpha;
		result.debug_support_retry_collect_mesh_face_cb_accept_us_total = support_retry_collector.mesh_face_cb_accept_us_total;
		result.debug_support_retry_collect_mesh_face_cb_accept_us_max = support_retry_collector.mesh_face_cb_accept_us_max;
		result.debug_support_retry_collect_mesh_face_cb_push_us_total = support_retry_collector.mesh_face_cb_push_us_total;
		result.debug_support_retry_collect_mesh_face_cb_push_us_max = support_retry_collector.mesh_face_cb_push_us_max;
		result.debug_support_retry_collect_mesh_face_cb_material_us_total = support_retry_collector.mesh_face_cb_material_us_total;
		result.debug_support_retry_collect_mesh_face_cb_material_us_max = support_retry_collector.mesh_face_cb_material_us_max;
		result.debug_support_retry_collect_mesh_face_cb_transform_us_total = support_retry_collector.mesh_face_cb_transform_us_total;
		result.debug_support_retry_collect_mesh_face_cb_transform_us_max = support_retry_collector.mesh_face_cb_transform_us_max;
		result.debug_support_retry_collect_mesh_face_cb_normal_us_total = support_retry_collector.mesh_face_cb_normal_us_total;
		result.debug_support_retry_collect_mesh_face_cb_normal_us_max = support_retry_collector.mesh_face_cb_normal_us_max;
		result.debug_support_retry_collect_mesh_face_cb_bbox_us_total = support_retry_collector.mesh_face_cb_bbox_us_total;
		result.debug_support_retry_collect_mesh_face_cb_bbox_us_max = support_retry_collector.mesh_face_cb_bbox_us_max;
		result.debug_support_retry_collect_mesh_faces_reported_total = support_retry_collector.mesh_faces_reported_total;
		result.debug_support_retry_collect_mesh_faces_reported_max = support_retry_collector.mesh_faces_reported_max;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_id = support_retry_collector.mesh_face_cb_us_max_inst_id;
		result.debug_support_retry_collect_mesh_face_cb_us_max_mesh_id = support_retry_collector.mesh_face_cb_us_max_mesh_id;
		result.debug_support_retry_collect_mesh_face_cb_us_max_mesh_faces = support_retry_collector.mesh_face_cb_us_max_mesh_faces;
		result.debug_support_retry_collect_mesh_face_cb_us_max_face_ordinal = support_retry_collector.mesh_face_cb_us_max_face_ordinal;
		result.debug_support_retry_collect_mesh_face_cb_us_max_accept = support_retry_collector.mesh_face_cb_us_max_accept;
		result.debug_support_retry_collect_mesh_face_cb_us_max_visual = support_retry_collector.mesh_face_cb_us_max_visual;
		result.debug_support_retry_collect_mesh_face_cb_us_max_soup_index = support_retry_collector.mesh_face_cb_us_max_soup_index;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_story_id = support_retry_collector.mesh_face_cb_us_max_inst_story_id;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_flags = support_retry_collector.mesh_face_cb_us_max_inst_flags;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_name_hash = support_retry_collector.mesh_face_cb_us_max_inst_name_hash;
		result.debug_support_retry_collect_mesh_face_cb_us_max_mesh_name_hash = support_retry_collector.mesh_face_cb_us_max_mesh_name_hash;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cx_milli = support_retry_collector.mesh_face_cb_us_max_inst_bbox_cx_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cy_milli = support_retry_collector.mesh_face_cb_us_max_inst_bbox_cy_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cz_milli = support_retry_collector.mesh_face_cb_us_max_inst_bbox_cz_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_diag_milli = support_retry_collector.mesh_face_cb_us_max_inst_bbox_diag_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_query_cx_milli = support_retry_collector.mesh_face_cb_us_max_query_cx_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_query_cy_milli = support_retry_collector.mesh_face_cb_us_max_query_cy_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_query_radius_milli = support_retry_collector.mesh_face_cb_us_max_query_radius_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_query_bbox_dist_milli = support_retry_collector.mesh_face_cb_us_max_query_bbox_dist_milli;
		result.debug_support_retry_collect_mesh_face_cb_us_max_query_bbox_overlap_milli = support_retry_collector.mesh_face_cb_us_max_query_bbox_overlap_milli;
		result.debug_support_retry_collect_mesh_soup_reallocs = support_retry_collector.soup_reallocs;
		result.debug_support_retry_collect_mesh_soup_capacity_max = support_retry_collector.soup_capacity_max;
		result.debug_support_retry_collect_mesh_soup_bytes_max = support_retry_collector.soup_bytes_max;
		result.debug_support_retry_collect_mesh_soup_bytes_growth_total = support_retry_collector.soup_bytes_growth_total;
		result.debug_support_retry_collect_mesh_bbox_would_skip = support_retry_collector.bbox_would_skip;
		result.debug_support_retry_collect_mesh_bbox_would_skip_faces = support_retry_collector.bbox_would_skip_faces;
		result.debug_support_retry_collect_mesh_cache_hits = support_retry_collector.mesh_cache_hits;
		result.debug_support_retry_collect_mesh_cache_misses = support_retry_collector.mesh_cache_misses;
		result.debug_support_retry_collect_mesh_cache_items = support_retry_collector.mesh_cache_items;
		result.debug_support_retry_collect_world_bsp_tests = support_retry_collector.world_bsp_tests;
		result.debug_support_retry_collect_world_bsp_nodes = support_retry_collector.world_bsp_nodes;
		result.debug_support_retry_collect_world_bsp_insts = support_retry_collector.world_bsp_insts;
		const bool retry_same_region =
			fabsf(support_retry_collector.collect_center_x - collector.collect_center_x) < 0.001f &&
			fabsf(support_retry_collector.collect_center_y - collector.collect_center_y) < 0.001f &&
			fabsf(support_retry_collector.collect_radius - collector.collect_radius) < 0.001f;
		result.debug_support_retry_same_region_repeat_count = retry_same_region ? 1u : 0u;
		support_collector = &support_retry_collector;
		result.debug_support_retry_rebuilt = true;
		const uint64_t retry_probe_start_us = a3dGetTime();
		support_found = MpStepSupportHitAt(*support_collector, next.pos[0], next.pos[1], next.pos[2],
			HEIGHT_SCALE * 2.0f, &support_hit);
		if (support_found)
			support_height = support_hit.z;
		result.debug_step_support_retry_probe_us = a3dGetTime() - retry_probe_start_us;
		if (support_found)
			result.debug_support_retry_found = true;
#endif
	}
	// S1/FL-641: the wide-radius retry (env.world_height) is a recovery path for
	// players genuinely under terrain, NOT a spawn-Z workaround. Do not remove
	// this or add a separate spawn-height lift — spawn Z is server-owned at bootstrap.
	// FL-641: if narrow search missed and player is well below water level,
	// retry with a large radius so terrain 100+ units above can still recover
	// the player. This only widens the search — the apply gate below still
	// controls whether we actually snap upward.
	if (!support_found && next.pos[2] < next.water - HEIGHT_SCALE)
	{
		const uint64_t wide_probe_start_us = a3dGetTime();
		support_found = MpStepSupportHitAt(*support_collector, next.pos[0], next.pos[1], next.pos[2],
			env.world_height, &support_hit);
		if (support_found)
			support_height = support_hit.z;
		result.debug_step_support_wide_probe_us = a3dGetTime() - wide_probe_start_us;
	}

	// Phase: support/floor apply logic (excluding the support probes and floor probe calls).
	const uint64_t post_support_apply_start_us = a3dGetTime();
	if (support_found)
	{
		result.debug_support_found = true;
		result.debug_support_height = support_height;
		result.debug_support_depth = support_height - next.pos[2];
		result.support = support_hit;
	}
	// FL-4137 behavior 7b scoped option (c): when support is a placed block,
	// allow the step-snap regardless of the standard kMpMaxImplicitStepUp
	// threshold. The authored block sprite is ~110 world units tall, which
	// exceeds the 24-unit auto-jump limit and would otherwise make walking
	// off the top edge onto an adjacent block fail. This carve-out applies
	// ONLY when the support hit is owned by a placed block (server-owned
	// item state with a placed entity), so general terrain / AKM mesh
	// step-up behaviour is unchanged.
	const bool support_is_placed_block =
		support_found && support_hit.source == MP_SUPPORT_PLACED_BLOCK;
	const bool grounded_support_snap_within_step =
		input.jump || input.fly || env.mount >= 2 ||
		prev.accum_contact < 1.0f ||
		support_is_placed_block ||
		support_height <= prev.pos[2] + kMpMaxImplicitStepUp;
		const bool support_snap_applied =
			result.debug_support_input_gate && support_found && grounded_support_snap_within_step;
		const float fl4137_pre_support_apply_pos_z = next.pos[2];
		if (support_snap_applied)
		{
		result.debug_support_applied = true;
		next.pos[2] = support_height + kMpSupportSnapEpsilon;
		if (next.vel[2] < 0.0f)
			next.vel[2] = 0.0f;
		contact_normal_z = fmaxf(contact_normal_z, 1.0f);
	}
	// MOUNT::BEE is ordinal 2; flying mounts keep their vertical movement branch.
	// CKPT-E (FL-4137): the !support_snap_applied gate was masking the
	// case where support_height itself was bogus (computed as far above
	// kMpMaxImplicitStepUp). When sphere-vs-mesh sweep against a placed
	// cube produces a too-high snap target, support_snap_applied writes
	// it into next.pos[2] and we used to skip the clamp. Now the clamp
	// fires whenever next.pos[2] exceeds the step-up ceiling under
	// grounded, non-jump intent — regardless of whether a "support" was
	// reported. floor_probe still finds the real floor (terrain or cube
	// top) and clamps to it.
	const bool unrequested_ground_launch =
		!input.jump && !input.fly && env.mount < 2 &&
		prev.accum_contact >= 1.0f &&
		next.pos[2] > prev.pos[2] + kMpMaxImplicitStepUp;
	float floor_height = 0.0f;
	const float floor_height_ceiling = prev.pos[2] + kMpMaxImplicitStepUp;
		bool floor_found = false;
		bool fl4137_launch_clamp_applied = false;
		if (unrequested_ground_launch)
		{
		const uint64_t floor_probe_start_us = a3dGetTime();
		floor_found = MpStepFloorHeightAt(*support_collector, next.pos[0], next.pos[1], next.pos[2],
			env.world_height * 4.0f, floor_height_ceiling, &floor_height);
		result.debug_step_floor_probe_us = a3dGetTime() - floor_probe_start_us;
	}
	if (unrequested_ground_launch &&
		(!floor_found || next.pos[2] > floor_height + kMpMaxImplicitStepUp))
	{
			if (!floor_found)
				floor_height = prev.pos[2];
			next.pos[2] = floor_height + kMpSupportSnapEpsilon;
		if (next.vel[2] > 0.0f)
			next.vel[2] = 0.0f;
			next.slope = 0.0f;
			contact_normal_z = fmaxf(contact_normal_z, 1.0f);
			fl4137_launch_clamp_applied = true;
		}
		if (fl4137_launch_diag &&
			!input.jump && !input.fly && env.mount < 2 &&
			(fl4137_post_sweep_pos_z > prev.pos[2] + kMpMaxImplicitStepUp ||
			 fl4137_pre_support_apply_pos_z > prev.pos[2] + kMpMaxImplicitStepUp ||
			 next.pos[2] > prev.pos[2] + kMpMaxImplicitStepUp))
		{
			printf("[FL4137-LAUNCH] prev_pos=(%.3f,%.3f,%.3f) post_sweep_z=%.3f "
			       "pre_support_z=%.3f next_pos=(%.3f,%.3f,%.3f) prev_accum=%.3f "
			       "contact_normal_z=%.3f support_found=%d support_applied=%d "
			       "support_source=%u support_item=%u support_z=%.3f support_height=%.3f "
			       "floor_probe=%d floor_found=%d floor_z=%.3f clamp_applied=%d "
			       "collision_kind=%s collision_t=%.6f collision_pos=(%.6f,%.6f,%.6f) "
			       "slide_normal=(%.6f,%.6f,%.6f) box_support_top=%u box_item=%u "
			       "box_entity=%llu box_bmin=(%.6f,%.6f,%.6f) box_bmax=(%.6f,%.6f,%.6f) "
			       "soup_inst=%llu soup_mesh=%llu soup_nrm_z=%.6f input=(%d,%d) jump=%d fly=%d\n",
			       prev.pos[0], prev.pos[1], prev.pos[2],
			       fl4137_post_sweep_pos_z,
			       fl4137_pre_support_apply_pos_z,
			       next.pos[0], next.pos[1], next.pos[2],
			       prev.accum_contact,
			       contact_normal_z,
			       support_found ? 1 : 0,
			       support_snap_applied ? 1 : 0,
			       (unsigned)support_hit.source,
			       (unsigned)support_hit.placed_item_id,
			       support_hit.z,
			       support_height,
			       unrequested_ground_launch ? 1 : 0,
			       floor_found ? 1 : 0,
			       floor_height,
			       fl4137_launch_clamp_applied ? 1 : 0,
			       fl4137_last_collision_kind,
			       fl4137_last_collision_time,
			       fl4137_last_collision_pos[0], fl4137_last_collision_pos[1], fl4137_last_collision_pos[2],
			       fl4137_last_slide_normal[0], fl4137_last_slide_normal[1], fl4137_last_slide_normal[2],
			       (unsigned)fl4137_last_box_support_top,
			       (unsigned)fl4137_last_box_item_id,
			       (unsigned long long)fl4137_last_box_entity_id,
			       fl4137_last_box_bmin[0], fl4137_last_box_bmin[1], fl4137_last_box_bmin[2],
			       fl4137_last_box_bmax[0], fl4137_last_box_bmax[1], fl4137_last_box_bmax[2],
			       (unsigned long long)fl4137_last_item_inst_id,
			       (unsigned long long)fl4137_last_item_mesh_id,
			       fl4137_last_item_nrm_z,
			       ix, iy,
			       input.jump ? 1 : 0,
			       input.fly ? 1 : 0);
			fflush(stdout);
		}
	// FL-4137 #21: W5 (MpStepResolvePlacedBlockSupport) call DELETED. Placed
	// blocks are now world entity component boxes handled by the
	// support_snap_applied path above. No second support owner runs after
	// MpStepOnce's primary sweep+probe.
	MpSupportHit water_support = {};
	bool clamped_to_water = false;
	if (!input.fly && env.mount < 2 && next.pos[2] < next.water - 0.1f)
	{
		next.pos[2] = next.water;
		if (next.vel[2] < 0.0f)
			next.vel[2] = 0.0f;
		contact_normal_z = fmaxf(contact_normal_z, 1.0f);
		clamped_to_water = true;
	}
	if (!input.fly && env.mount < 2 && clamped_to_water)
	{
		water_support.found = 1;
		water_support.source = MP_SUPPORT_WATER;
		water_support.z = next.water;
		result.support = water_support;
	}
	// Subtract the floor probe call if it happened so the accounted sum doesn't double count.
	{
		const uint64_t wall_us = a3dGetTime() - post_support_apply_start_us;
		result.debug_step_post_support_apply_us =
			(wall_us > result.debug_step_floor_probe_us) ? (wall_us - result.debug_step_floor_probe_us) : 0u;
	}

	res = (next.water - next.pos[2]) / env.world_height;
	if (res < 0.0f)
		res = 0.0f;
	if (res > 1.0f)
		res = 1.0f;
	result.in_water = res;

	const uint64_t material_votes_start_us = a3dGetTime();
	int mat = next.mat;
	int votes = 0;
	for (int m = 0; m < 6; m++)
	{
		if (material_votes[m] > votes)
		{
			votes = material_votes[m];
			mat = m;
		}
	}
	next.mat = mat;
	if (result.in_water > 0.1f)
		next.mat = 6;
	result.debug_step_material_votes_us = a3dGetTime() - material_votes_start_us;

	const uint64_t contact_start_us = a3dGetTime();
	float prev_contact = next.accum_contact;
	next.accum_contact += fmaxf(0.0f, contact_normal_z);
	if (next.accum_contact > 5.0f)
		next.accum_contact = 5.0f;
	result.landed = (prev_contact < 1.0f && next.accum_contact >= 1.0f);

	if ((next.accum_contact >= 1.0f || env.mount > 1) && jump_requested)
	{
		next.accum_contact = 0.0f;
		if (env.mount < 2 || next.pos[2] < collector.max_height + 100.0f)
		{
			if (next.vel[2] < 0.0f)
				next.vel[2] = 10.0f;
			else
			next.vel[2] += 10.0f;
		}
	}
	result.debug_step_contact_us = a3dGetTime() - contact_start_us;

	const uint64_t grounding_start_us = a3dGetTime();
	result.grounded = next.accum_contact >= 1.0f;
	const bool grounded_contact_transition =
		result.grounded &&
		prev.accum_contact < 1.0f &&
		contact_normal_z > 0.0f;
	next.accum_contact *= kMpGroundContactDecay;
	// FL-2957 attempt #27: MpStepOnce records result.grounded from accum_contact>=1.0
	// BEFORE decaying it by kMpGroundContactDecay (0.9). So a flat-support frame is
	// "grounded this step" but stored as 0.9 — next tick's GetPhysicsGrounded() reads
	// false and idle fast path never arms. Fix: on the first contact-transition frame,
	// clamp the decayed value back to 1.0 so next tick sees grounded=true.
	// LINEAGE_JSON: {"fl":"FL-2957","attempt":27,"commit":"7a63965f","attempt_total":27,"closed":0,"what":"preserve grounded contact transition — clamp accum_contact to 1.0 after decay on first ground-contact frame","result":"pending","run":"pending"}
	if (grounded_contact_transition && next.accum_contact < 1.0f)
		next.accum_contact = 1.0f;
	if (next.vel[2] > kMpMaxVerticalVel)
		next.vel[2] = kMpMaxVerticalVel;
	result.debug_step_grounding_us = a3dGetTime() - grounding_start_us;

	const uint64_t mount_start_us = a3dGetTime();
	if (env.mount > 1)
	{
		if (!result.grounded)
		{
			float v = fmaxf(1.0f, next.vel[2]);
			float dt_scale = (float)PHYSICS_STEP_US / 15000.0f;
			if (dt_scale > 4.0f)
				dt_scale = 4.0f;
			next.player_stp = (~(1 << 31)) & (next.player_stp + (int)(v * 64.0f * 2.0f * dt_scale));
		}
		else if (ix == 0 && iy == 0)
		{
			next.player_stp = -1;
		}
	}
	result.debug_step_mount_us = a3dGetTime() - mount_start_us;

	const uint64_t finalize_start_us = a3dGetTime();
	result.xy_vel = sqrtf(fmaxf(0.0f, next.vel[0] * next.vel[0] + next.vel[1] * next.vel[1]));
	result.mat = next.mat;
	result.next_stp = next.player_stp;

	// Final phase + accounting summary.
	result.debug_step_finalize_us = a3dGetTime() - finalize_start_us;
	result.debug_step_wall_us = a3dGetTime() - step_wall_start_us;

	// The goal is "small unaccounted" for spike diagnosis. These phases are aligned to
	// actual code blocks above; sweep_total_us remains the per-iter instrumentation.
	result.debug_step_accounted_us =
		result.debug_step_pre_collect_us +
		result.debug_step_collect_build_us +
		result.debug_step_post_collect_setup_us +
		result.debug_step_sweep_wall_us +
		result.debug_step_post_sweep_us +
		result.debug_step_support_probe_us +
		result.debug_step_support_retry_build_us +
		result.debug_step_support_retry_probe_us +
		result.debug_step_support_wide_probe_us +
		result.debug_step_floor_probe_us +
		result.debug_step_post_support_apply_us +
		result.debug_step_material_votes_us +
		result.debug_step_contact_us +
		result.debug_step_grounding_us +
		result.debug_step_mount_us +
		result.debug_step_finalize_us;
	result.debug_step_unaccounted_us =
		(result.debug_step_wall_us > result.debug_step_accounted_us)
			? (result.debug_step_wall_us - result.debug_step_accounted_us)
			: 0u;

	if (out)
		*out = result;
	return next;
}
