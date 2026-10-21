#include "authoritative_world_item_appearance.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "actor_visual_profile_runtime.h"
#include "actor_visual_catalog_source.h"
#include "world.h"
// FL-4137 Gap B/#21/#24: the server-wire APPEARANCE_ITEM_STATE_* flags drive
// the placed/collidable filter so the client preview/mirror reads the same
// placed item rows the server marks collidable. This is render/diagnostic
// state only. Attempt #24 proved client-visible block rows and world-mesh
// mirrors can still false-green stand-on unless server mp_step support settles
// near the block top.
#include "server/protocol/protocol_common.h"

extern int g_web_render_stage_code;
extern char base_path[];

// =============================================================================
// FL-4600 ASan-only SpriteInst allocation/retirement provenance instrument.
// =============================================================================
// Plain-English purpose: the FL-4600 crash is a WASM abort inside
// DeleteInst -> free on an authoritative world-item's SpriteInst (vis->inst).
// This instrument answers "which creation is this pointer, and who freed it
// twice?" by giving every item SpriteInst a stable, monotonically increasing
// "allocation sequence" number the instant it is created, remembering the
// pointer/item/slot/world it belongs to, logging every creation and every
// retirement attempt (including whether that allocation is still live), and
// FAILING VISIBLY (abort) BEFORE the free when the same allocation is retired a
// second time. That turns an anonymous allocator crash into a named
// double-owner report.
//
// It compiles in ONLY when FL4600_DIAG_ASAN is defined (set by
// `FL4600_DIAG=asan ./build-web.sh`). When the define is absent, the two macros
// at the bottom expand to ((void)0), so the production build is byte-equivalent
// (no table, no logging, no branches).
#ifdef FL4600_DIAG_ASAN
#include <stdint.h>
#include <stdlib.h>
namespace {

// One record per distinct SpriteInst address observed. `live` is true from
// creation until the first retirement; retiring again while `live` is already
// false is the double-retire we abort on. `alloc_seq` is the stable identity
// that survives allocator ADDRESS REUSE: when the allocator hands the same
// address to a later CreateInst, that address's record is overwritten with a
// fresh, higher sequence, so an old freed identity can never be confused with a
// new live one.
struct Fl4600ProvRecord
{
	const void* ptr;       // the SpriteInst* address under observation
	uint64_t    alloc_seq; // stable allocation identity (never reused)
	uint16_t    item_id;   // authoritative item id at creation
	int         slot;      // item_visuals[] slot at creation
	const void* world;     // World* the inst was created in
	bool        live;      // true until first retirement
};

// Capacity: far above the max simultaneous item visuals, sized for the address
// churn of a short diagnostic run. Overflow fails VISIBLY (abort) rather than
// silently dropping a provenance fact -- the task forbids silent fallbacks.
enum { FL4600_PROV_CAP = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS * 32 };
Fl4600ProvRecord g_fl4600_prov[FL4600_PROV_CAP];
int      g_fl4600_prov_count = 0;
uint64_t g_fl4600_alloc_seq  = 0;

// Return the newest record for an address (newest-first so a reused address
// resolves to its latest creation), or 0 if never seen.
Fl4600ProvRecord* Fl4600ProvFind(const void* ptr)
{
	for (int i = g_fl4600_prov_count - 1; i >= 0; --i)
		if (g_fl4600_prov[i].ptr == ptr)
			return &g_fl4600_prov[i];
	return 0;
}

// Record a freshly created item SpriteInst and assign it one allocation
// identity. Called exactly once per successful CreateInst of vis->inst.
void Fl4600ProvRegisterCreate(const void* ptr, uint16_t item_id, int slot,
							  const void* world)
{
	if (!ptr)
		return;
	const uint64_t seq = ++g_fl4600_alloc_seq;
	Fl4600ProvRecord* rec = Fl4600ProvFind(ptr);
	if (!rec)
	{
		if (g_fl4600_prov_count >= FL4600_PROV_CAP)
		{
			fprintf(stderr,
				"[FL-4600][asan] provenance table full (%d) at CREATE "
				"ptr=%p item=%u slot=%d -- aborting\n",
				FL4600_PROV_CAP, ptr, (unsigned)item_id, slot);
			fflush(stderr);
			abort();
		}
		rec = &g_fl4600_prov[g_fl4600_prov_count++];
	}
	rec->ptr = ptr;
	rec->alloc_seq = seq;
	rec->item_id = item_id;
	rec->slot = slot;
	rec->world = world;
	rec->live = true;
	fprintf(stderr,
		"[FL-4600][asan] CREATE seq=%llu ptr=%p item=%u slot=%d world=%p\n",
		(unsigned long long)seq, ptr, (unsigned)item_id, slot, world);
	fflush(stderr);
}

// Check + log a retirement of vis->inst BEFORE the caller frees it. A
// double-retire (live already false) aborts here so the double owner is named
// before the allocator crashes. A first retire of a still-live record that ASan
// nonetheless aborts on at the subsequent free proves the FIRST free came from
// OUTSIDE this retirement path (e.g. a world teardown) -- which is exactly the
// provenance signal FL-4600 needs.
void Fl4600ProvCheckRetire(const void* ptr, int stage)
{
	if (!ptr)
		return;
	Fl4600ProvRecord* rec = Fl4600ProvFind(ptr);
	if (!rec)
	{
		// Never created through the instrumented path. Observation only: log
		// and let the real retirement proceed unchanged (no free is skipped).
		fprintf(stderr,
			"[FL-4600][asan] RETIRE ptr=%p stage=%d live=? (no creation record)\n",
			ptr, stage);
		fflush(stderr);
		return;
	}
	fprintf(stderr,
		"[FL-4600][asan] RETIRE seq=%llu ptr=%p item=%u slot=%d stage=%d live=%d\n",
		(unsigned long long)rec->alloc_seq, ptr, (unsigned)rec->item_id,
		rec->slot, stage, rec->live ? 1 : 0);
	fflush(stderr);
	if (!rec->live)
	{
		fprintf(stderr,
			"[FL-4600][asan] DOUBLE-RETIRE seq=%llu ptr=%p item=%u slot=%d "
			"stage=%d -- aborting before free\n",
			(unsigned long long)rec->alloc_seq, ptr, (unsigned)rec->item_id,
			rec->slot, stage);
		fflush(stderr);
		abort();
	}
	rec->live = false;
}

} // namespace
#define FL4600_PROV_CREATE(ptr, item, slot, world) \
	Fl4600ProvRegisterCreate((ptr), (item), (slot), (world))
#define FL4600_PROV_RETIRE(ptr, stage) \
	Fl4600ProvCheckRetire((ptr), (stage))
#else
// Production (diagnostic define absent): no-ops, byte-equivalent to no code.
#define FL4600_PROV_CREATE(ptr, item, slot, world) ((void)0)
#define FL4600_PROV_RETIRE(ptr, stage)             ((void)0)
#endif // FL4600_DIAG_ASAN

static const float AUTHORITATIVE_WORLD_ITEM_PICKUP_RADIUS = 6.0f;

// FL-4137 Gap B: mirrors server SVR_PLACE_MAX_STACK_LAYERS (server_tick.cpp).
// The client preview cap MUST match the server cap; if the server number
// changes, this number must change in the same commit. Render hint only; the
// server still owns the final reject path.
static const int AUTHORITATIVE_WORLD_ITEM_PREVIEW_MAX_STACK_LAYERS = 4;

static bool ResolveAuthoritativeWorldItemBlockGeometry(
	const AppearanceCatalogItemDef* item_def,
	Sprite* /*unused_sprite_FL_4137*/,
	float* out_half_extent,
	float* out_height)
{
	// CKPT-D (FL-4137): placed-block geometry is AUTHORED in the item
	// definition; sprite proj_bbox is no longer consulted on either side.
	// Placed blocks render as the single catalog-owned AKM mesh-instance in
	// SyncAuthoritativePlacedBlockMeshInst, sized to the authored half_extent
	// x half_extent x height. Sprite art only drives inventory/held-preview
	// thumbnails.
	if (out_half_extent)
		*out_half_extent = 1.0f;
	if (out_height)
		*out_height = 2.0f;
	if (!item_def)
		return false;
	if (out_half_extent)
		*out_half_extent = item_def->collision_radius_units > 0.0f
			? item_def->collision_radius_units
			: 1.0f;
	if (out_height)
		*out_height = item_def->collision_height_units > 0.0f
			? item_def->collision_height_units
			: 2.0f;
	return true;
}

// FL-4137 Gap B/#21/#24: snapshot of already-placed world blocks the client
// mirror reads when scoring local held-preview validity. Filled once per frame
// from server->authority.auth_item.items[] so the preview check sees the same
// item set the appearance loop will publish. This is not a collider owner and
// must not revive the deleted server MpPlacedBlockCollider lane.
struct AuthoritativeWorldItemPlacedSnapshot
{
	float pos[3];
	float half_extent;
	float height;
	uint16_t item_id;
};

static const int AUTHORITATIVE_WORLD_ITEM_PLACED_SNAPSHOT_CAP =
	AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;

// FL-4137 Gap B (review fix): mirror server filter exactly. World-owned items
// can be dropped loot or map-authored items that are not placeable blocks —
// owner_id alone is not the gate. Server placement marks collidable blocks
// with SVR_PLACED_ITEM_COLLIDABLE; the wire equivalent for the client is the
// matching APPEARANCE_ITEM_STATE_PLACED + APPEARANCE_ITEM_STATE_COLLIDABLE
// bits on ai->v2_state_flags. Without this filter, a dropped sword would
// false-red the held preview.
static int CollectClientPlacedItemColliders(
	AuthoritativeWorldItemPlacedSnapshot* out,
	int out_cap)
{
	if (!out || out_cap <= 0 || !server)
		return 0;
	const uint16_t placed_collidable_mask =
		(uint16_t)(APPEARANCE_ITEM_STATE_PLACED |
				   APPEARANCE_ITEM_STATE_COLLIDABLE);
	int count = 0;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS &&
		 count < out_cap;
		 i++)
	{
		const ::AuthoritativeItemState* ai = &server->authority.auth_item.items[i];
		if (!ai->valid || ai->owner_id != 0xffff)
			continue;
		if ((ai->v2_state_flags & placed_collidable_mask) != placed_collidable_mask)
			continue;
		const AppearanceCatalogItemDef* item_def =
			FindAppearanceCatalogItemById(ai->item_definition_id);
		AuthoritativeWorldItemPlacedSnapshot* slot = &out[count++];
		slot->pos[0] = ai->pos[0];
		slot->pos[1] = ai->pos[1];
		slot->pos[2] = ai->pos[2];
		if (!ResolveAuthoritativeWorldItemBlockGeometry(
				item_def, 0, &slot->half_extent, &slot->height))
		{
			slot->half_extent = 0.0f;
			slot->height = 0.0f;
		}
		slot->item_id = ai->item_id;
	}
	return count;
}

static bool SnapHeldPreviewXYToExistingBlockGrid(
	float pos[3],
	float preview_half_extent,
	const AuthoritativeWorldItemPlacedSnapshot* placed,
	int placed_count)
{
	if (!pos || !placed || placed_count <= 0)
		return false;
	const float new_half = preview_half_extent > 0.0f ? preview_half_extent : 1.0f;
	const float snap_halo = new_half * 2.0f + 1.0f;
	const float snap_halo2 = snap_halo * snap_halo;
	const AuthoritativeWorldItemPlacedSnapshot* best = 0;
	float best_d2 = snap_halo2;
	for (int i = 0; i < placed_count; i++)
	{
		const AuthoritativeWorldItemPlacedSnapshot& c = placed[i];
		const float half = c.half_extent > 0.0f ? c.half_extent : 1.0f;
		const float dx = pos[0] - c.pos[0];
		const float dy = pos[1] - c.pos[1];
		const float d2 = dx * dx + dy * dy;
		if (d2 > best_d2)
			continue;
		best_d2 = d2;
		best = &c;
		if (fabsf(dx) <= half && fabsf(dy) <= half)
			break;
	}
	if (!best)
		return false;

	const float half = best->half_extent > 0.0f ? best->half_extent : 1.0f;
	const float dx = pos[0] - best->pos[0];
	const float dy = pos[1] - best->pos[1];
	if (fabsf(dx) <= half && fabsf(dy) <= half)
	{
		pos[0] = best->pos[0];
		pos[1] = best->pos[1];
		return true;
	}

	const float center_offset = half + new_half;
	if (fabsf(dx) >= fabsf(dy))
	{
		pos[0] = best->pos[0] + (dx >= 0.0f ? center_offset : -center_offset);
		pos[1] = best->pos[1];
	}
	else
	{
		pos[0] = best->pos[0];
		pos[1] = best->pos[1] + (dy >= 0.0f ? center_offset : -center_offset);
	}
	return true;
}

// FL-4137 Gap B (review fix): mirror server order exactly. The server's
// SvrPlaceOwnedItemFromPlayer (server_tick.cpp:4102-4153) runs in this order:
//   1. terrain_z baseline
//   2. lift candidate Z to the highest AABB-contained placed-block top under
//      the target XY, capped at ps->pos[2] + max_stack_lift; cap-excluded
//      candidates are silently dropped so best_z stays at the next-lower
//      valid candidate (or terrain_z when no candidate fits).
//   3. set placed_pos[2] = best_z
//   4. SvrPlacedBlockPositionOccupied checks placed/player/NPC overlap AT
//      the lifted Z and emits "placed_block_overlap" / "player_overlap" /
//      "npc_overlap" on intersect.
//
// The earlier client mirror ran overlap FIRST against the player's feet Z,
// which produced false-red on every same-XY stacked placement the server
// would have accepted (the lift would have moved candidate_z above the
// existing top). This implementation now matches server order: lift, cap,
// then overlap-at-lifted-Z.
//
// Missing client mirror data must NOT reject placement locally — the server
// ITEM_ACTION_REQ_PLACE handler remains the sole authority (Law 3, Law 7).
// If the snapshot is empty or item_id is unknown, the loops fall through and
// return PREVIEW_PLACEMENT_REASON_OK; any silent client/server mismatch is
// resolved by the server reject path (Law 6 fail-closed lives on the server).
static uint8_t EvaluateHeldPreviewPlacementValidity(
	float player_z,
	const float preview_pos[3],
	float preview_half_extent,
	float preview_height,
	uint16_t preview_item_id,
	const AuthoritativeWorldItemPlacedSnapshot* placed,
	int placed_count)
{
	if (!preview_pos || preview_half_extent <= 0.0f || preview_height <= 0.0f)
		return PREVIEW_PLACEMENT_REASON_OK;

	// Step 1: lift candidate_z to mirror SvrPlaceOwnedItemFromPlayer stacking
	// lift. Client has no terrain_z query surface here so the baseline is the
	// preview's planted position (player feet); a stack under the target XY
	// lifts it. Cap-excluded candidates are silently skipped so candidate_z
	// stays at the next-lower valid top, matching server semantics.
	const float max_stack_lift =
		preview_height * (float)AUTHORITATIVE_WORLD_ITEM_PREVIEW_MAX_STACK_LAYERS;
	float candidate_z = preview_pos[2];
	for (int i = 0; i < placed_count; i++)
	{
		const AuthoritativeWorldItemPlacedSnapshot& c = placed[i];
		if (c.item_id == preview_item_id) continue;
		if (fabsf(c.pos[0] - preview_pos[0]) > c.half_extent) continue;
		if (fabsf(c.pos[1] - preview_pos[1]) > c.half_extent) continue;
		const float top_z = c.pos[2] + c.height;
		if (top_z <= candidate_z) continue;
		if (top_z > player_z + max_stack_lift) continue;
		candidate_z = top_z;
	}

	// Step 2: overlap check AT the lifted candidate_z, mirroring
	// SvrPlacedBlockPositionOccupied. A 5-tall stack whose top exceeds the
	// player's lift cap falls through here: the cap-skip left candidate_z
	// at the 4th block's top, and the 5th block's vertical band still
	// intersects -> BLOCKED_PLACED_OVERLAP, matching the server reject.
	const float candidate_top = candidate_z + preview_height;
	for (int i = 0; i < placed_count; i++)
	{
		const AuthoritativeWorldItemPlacedSnapshot& c = placed[i];
		if (c.item_id == preview_item_id) continue;
		const float min_dist = preview_half_extent + c.half_extent;
		const float dx = c.pos[0] - preview_pos[0];
		const float dy = c.pos[1] - preview_pos[1];
		if (dx * dx + dy * dy >= min_dist * min_dist) continue;
		const float other_top = c.pos[2] + c.height;
		if (candidate_top <= c.pos[2] || candidate_z >= other_top) continue;
		return PREVIEW_PLACEMENT_REASON_BLOCKED_PLACED_OVERLAP;
	}
	return PREVIEW_PLACEMENT_REASON_OK;
}

static uint16_t HashWorldSpritePath16(const char* path)
{
	uint32_t hash = 2166136261u;
	if (!path)
		return 0;
	for (const unsigned char* p = (const unsigned char*)path; *p; ++p)
	{
		hash ^= (uint32_t)(*p);
		hash *= 16777619u;
	}
	uint16_t out = (uint16_t)((hash >> 16) ^ (hash & 0xffffu));
	return out ? out : 1u;
}

static uint16_t WorldSpriteFamilyKindFromPath(const char* path)
{
	if (!path)
		return 0;
	if (strstr(path, "wolfie.xp"))
		return 1;
	if (strstr(path, "bigbee.xp"))
		return 2;
	if (strstr(path, "player"))
		return 900;
	return 100;
}

static void DeleteAuthoritativeWorldItemInst(
	AuthoritativeItemServerState::ItemVisual* vis,
	int stage_code)
{
	if (!vis)
		return;
	if (vis->inst)
	{
		g_web_render_stage_code = stage_code;
		// FL-4600: route every SpriteInst retirement through the provenance
		// check BEFORE freeing (fails visibly on a double-retire).
		FL4600_PROV_RETIRE(vis->inst, stage_code);
		DeleteInst(vis->inst);
		vis->inst = 0;
	}
	// FL-4137 #69: also tear down the mesh inst on full item teardown so a
	// dropped/picked-up block does not leave a stale cube floating in world.
	if (vis->mesh_inst)
	{
		g_web_render_stage_code = stage_code + 1;
		DeleteInst(vis->mesh_inst);
		vis->mesh_inst = 0;
	}
}

// FL-4600: 431..444 distinguish the item lifecycle site that was active when
// WASM aborted in DeleteInst. Empty slots do not overwrite the last real stage.

static void DeleteAuthoritativeWorldItemSpriteInst(
	AuthoritativeItemServerState::ItemVisual* vis,
	int stage_code)
{
	if (!vis || !vis->inst)
		return;
	g_web_render_stage_code = stage_code;
	// FL-4600: same single provenance owner as the full-teardown path.
	FL4600_PROV_RETIRE(vis->inst, stage_code);
	DeleteInst(vis->inst);
	vis->inst = 0;
}

// FL-4137 #69 (2026-05-31): sync the visible AKM mesh Inst for items whose
// catalog row declares a world_mesh_path AND whose authoritative state is
// PLACED+COLLIDABLE in the world (not the held preview). This is the
// goal-text gate G3 owner -- the placed block's visible body is an AKM
// mesh inst, not the 2D/3D sprite. Unrelated to the attempt #25 ECS
// reframe deletion of legacy_yy_block_collision.akm, which was a
// collision proxy with INST_VISIBLE bleeding yellow halo into the ASCII
// post stage. This Inst's purpose is visible RENDER; collision still
// lives in ServerWorldEntityRegistry + mp_step CollisionBox/SupportSurface.
//
// Geometry contract: the AKM cube has local x,y in [-1,1] and z in [0,1].
// TM scales to (half_extent, half_extent, height) and translates to the
// authoritative item position so the visible mesh top equals support_top
// equals collision_top equals visual_top per the existing recorder
// geometry-contract gate (lines 728+).
static void SyncAuthoritativePlacedBlockMeshInst(
	World* world,
	AuthoritativeItemServerState::ItemVisual* vis,
	const ::AuthoritativeItemState* ai,
	const AppearanceCatalogItemDef* catalog_item,
	bool render_as_mesh)
{
	if (!vis)
		return;
	if (!render_as_mesh || !world || !ai || !catalog_item ||
		!catalog_item->world_mesh_path || !catalog_item->world_mesh_path[0])
	{
		if (vis->mesh_inst)
		{
			g_web_render_stage_code = 441;
			DeleteInst(vis->mesh_inst);
			vis->mesh_inst = 0;
			RebuildWorld(world, false);
		}
		return;
	}

	if (vis->mesh_inst && GetInstWorld(vis->mesh_inst) != world)
	{
		g_web_render_stage_code = 442;
		DeleteInst(vis->mesh_inst);
		vis->mesh_inst = 0;
	}

	const double half = catalog_item->collision_radius_units > 0.0f
		? (double)catalog_item->collision_radius_units : 1.0;
	const double height = catalog_item->collision_height_units > 0.0f
		? (double)catalog_item->collision_height_units : 2.0;
	double tm[16] = {0};
	tm[0]  = half;
	tm[5]  = half;
	tm[10] = height;
	tm[15] = 1.0;
	tm[12] = (double)ai->pos[0];
	tm[13] = (double)ai->pos[1];
	tm[14] = (double)ai->pos[2];

	if (!vis->mesh_inst)
	{
		g_web_render_stage_code = 443;
		Mesh* mesh = FindOrLoadMesh(world, catalog_item->world_mesh_path,
									catalog_item->world_mesh_path);
		if (!mesh)
			return;
		char inst_name[96] = {};
		snprintf(inst_name, sizeof(inst_name), "placed_block_mesh_%u",
				 (unsigned)ai->item_id);
		const int story_id = 0x70000000 | (int)ai->item_id;
		vis->mesh_inst = CreateInst(
			mesh,
			INST_VISIBLE | INST_USE_TREE | INST_VOLATILE,
			tm, inst_name, story_id);
		if (vis->mesh_inst)
			RebuildWorld(world, false);
		return;
	}

	double old_tm[16] = {};
	if (GetInstTM(vis->mesh_inst, old_tm) &&
		memcmp(old_tm, tm, sizeof(tm)) == 0)
	{
		return;
	}
	g_web_render_stage_code = 444;
	SetInstTM(vis->mesh_inst, tm);
	RebuildWorld(world, false);
}

static void ResetAuthoritativeWorldItemRow(
	AuthoritativeWorldItemAppearanceRow* row)
{
	if (!row)
		return;
	memset(row, 0, sizeof(*row));
	row->item_id = 0xffff;
	row->pickup_distance2 = 1.0e30f;
}

static void ResetAuthoritativeWorldItemFrame(
	AuthoritativeWorldItemAppearanceFrame* frame)
{
	if (!frame)
		return;
	memset(frame, 0, sizeof(*frame));
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		ResetAuthoritativeWorldItemRow(&frame->visible_rows[i]);
		ResetAuthoritativeWorldItemRow(&frame->pickup_rows[i]);
	}
}

static void InsertPickupRowSorted(
	AuthoritativeWorldItemAppearanceFrame* frame,
	const AuthoritativeWorldItemAppearanceRow* row)
{
	if (!frame || !row || !row->pickup_in_range)
		return;
	int slot = frame->pickup_row_count;
	if (slot >= AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
		slot = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS - 1;
	else
		frame->pickup_row_count++;
	if (slot < 0)
		return;
	frame->pickup_rows[slot] = *row;
	while (slot > 0 &&
		frame->pickup_rows[slot].pickup_distance2 <
		frame->pickup_rows[slot - 1].pickup_distance2)
	{
		AuthoritativeWorldItemAppearanceRow tmp = frame->pickup_rows[slot - 1];
		frame->pickup_rows[slot - 1] = frame->pickup_rows[slot];
		frame->pickup_rows[slot] = tmp;
		slot--;
	}
}

Sprite* ResolveAuthoritativeItemSprite3D(
	const ::AuthoritativeItemState* ai,
	uint8_t* out_failure_reason)
{
	return ResolveAuthoritativeItemActorVisualSprite(ai, true, out_failure_reason);
}

Sprite* ResolveAuthoritativeItemSprite2D(
	const ::AuthoritativeItemState* ai,
	uint8_t* out_failure_reason)
{
	return ResolveAuthoritativeItemActorVisualSprite(ai, false, out_failure_reason);
}

void ServerDestroyAuthoritativeItemVisuals(Server* s)
{
	if (!s)
		return;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		DeleteAuthoritativeWorldItemInst(&s->authority.auth_item.item_visuals[i], 43);
		s->authority.auth_item.item_visuals[i].item_id = 0;
		s->authority.auth_item.item_visuals[i].visual_failure_reason =
			ACTOR_VISUAL_ITEM_FAILURE_NONE;
	}
}

void ResetAuthoritativeWorldItemAppearanceRows(Game* game)
{
	if (!game)
		return;
	game->debug.dbg_visible_authoritative_item_markers = 0;
	game->authoritative.world_pickup_rows_count = 0;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		game->debug.dbg_visible_authoritative_item_ids[i] = 0xffff;
		game->debug.dbg_visible_authoritative_item_styles[i] = 0;
		game->debug.dbg_visible_authoritative_item_definition_ids[i] = 0;
		game->debug.dbg_visible_authoritative_item_visual_style_ids[i] = 0;
		game->debug.dbg_visible_authoritative_item_world_sprite_source_hashes[i] = 0;
		game->debug.dbg_visible_authoritative_item_world_sprite_family_kinds[i] = 0;
		game->debug.dbg_visible_authoritative_item_visual_failure_reasons[i] =
			ACTOR_VISUAL_ITEM_FAILURE_NONE;
		game->debug.dbg_visible_authoritative_item_visual_bottom_z[i] = 0.0f;
		game->debug.dbg_visible_authoritative_item_visual_top_z[i] = 0.0f;
		game->debug.dbg_visible_authoritative_item_screen_valid[i] = 0;
		game->debug.dbg_visible_authoritative_item_screen_top_col[i] = 0;
		game->debug.dbg_visible_authoritative_item_screen_top_row[i] = 0;
		game->debug.dbg_visible_authoritative_item_screen_bottom_col[i] = 0;
		game->debug.dbg_visible_authoritative_item_screen_bottom_row[i] = 0;
		game->debug.dbg_visible_authoritative_item_corners_valid[i] = 0;
		for (int k = 0; k < 8; k++)
		{
			game->debug.dbg_visible_authoritative_item_corner_col[i][k] = 0;
			game->debug.dbg_visible_authoritative_item_corner_row[i][k] = 0;
		}
		game->authoritative.world_pickup_item_ids[i] = 0xffff;
		game->authoritative.world_pickup_distance2[i] = 1.0e30f;
	}
	// FL-4137 Gap A: clear the persisted mobile preview contact each frame.
	// The appearance pass repopulates it only when the held placeable preview
	// is currently on-screen; otherwise the tap router sees valid=0 and falls
	// through to the existing PLAYER / torque / force routing untouched.
	game->authoritative.held_preview_mobile_contact = {};
}

void PublishAuthoritativeWorldItemAppearanceRows(
	Game* game,
	const AuthoritativeWorldItemAppearanceFrame* frame)
{
	if (!game)
		return;
	ResetAuthoritativeWorldItemAppearanceRows(game);
	if (!frame)
		return;
	game->debug.dbg_visible_authoritative_item_markers = frame->visible_row_count;
	if (game->debug.dbg_visible_authoritative_item_markers > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
		game->debug.dbg_visible_authoritative_item_markers = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
	for (int i = 0; i < game->debug.dbg_visible_authoritative_item_markers; i++)
	{
		const AuthoritativeWorldItemAppearanceRow* row = &frame->visible_rows[i];
		game->debug.dbg_visible_authoritative_item_ids[i] = row->item_id;
		game->debug.dbg_visible_authoritative_item_styles[i] =
			(uint8_t)(row->visual_style_id >= APPEARANCE_VISUAL_STYLE_DEFAULT
				? (row->visual_style_id - APPEARANCE_VISUAL_STYLE_DEFAULT)
				: 0);
		game->debug.dbg_visible_authoritative_item_definition_ids[i] = row->definition_id;
		game->debug.dbg_visible_authoritative_item_visual_style_ids[i] = row->visual_style_id;
		game->debug.dbg_visible_authoritative_item_world_sprite_source_hashes[i] =
			row->world_sprite_source_hash;
		game->debug.dbg_visible_authoritative_item_world_sprite_family_kinds[i] =
			row->world_sprite_family_kind;
		game->debug.dbg_visible_authoritative_item_visual_failure_reasons[i] =
			row->render_visual_failure_reason;
		game->debug.dbg_visible_authoritative_item_visual_bottom_z[i] =
			row->visual_bottom_z;
		game->debug.dbg_visible_authoritative_item_visual_top_z[i] =
			row->visual_top_z;
		game->debug.dbg_visible_authoritative_item_screen_valid[i] = row->screen_valid;
		game->debug.dbg_visible_authoritative_item_screen_top_col[i] = row->screen_top_col;
		game->debug.dbg_visible_authoritative_item_screen_top_row[i] = row->screen_top_row;
		game->debug.dbg_visible_authoritative_item_screen_bottom_col[i] = row->screen_bottom_col;
		game->debug.dbg_visible_authoritative_item_screen_bottom_row[i] = row->screen_bottom_row;
		game->debug.dbg_visible_authoritative_item_corners_valid[i] = row->corners_valid;
		for (int k = 0; k < 8; k++)
		{
			game->debug.dbg_visible_authoritative_item_corner_col[i][k] = row->corner_col[k];
			game->debug.dbg_visible_authoritative_item_corner_row[i][k] = row->corner_row[k];
		}
	}
	game->authoritative.world_pickup_rows_count = frame->pickup_row_count;
	if (game->authoritative.world_pickup_rows_count > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
		game->authoritative.world_pickup_rows_count = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
	for (int i = 0; i < game->authoritative.world_pickup_rows_count; i++)
	{
		const AuthoritativeWorldItemAppearanceRow* row = &frame->pickup_rows[i];
		game->authoritative.world_pickup_item_ids[i] = row->item_id;
		game->authoritative.world_pickup_distance2[i] = row->pickup_distance2;
	}
	// FL-4137 Gap A: publish the held-preview tap rect. Lives in
	// game->authoritative — the same struct that owns world_pickup_*, but
	// in a dedicated field that is never written into world_pickup_rows
	// (Law 1: single-owner pickup-vs-place split; Law 3: client emits
	// ITEM_ACTION_REQ_PLACE intent only, never local placement truth).
	game->authoritative.held_preview_mobile_contact =
		frame->held_preview_mobile_contact;
}

bool UpdateAuthoritativeWorldItemAppearance(
	Game* game,
	World* world,
	Renderer* renderer,
	uint64_t render_stamp_us,
	int viewport_width,
	int viewport_height,
	AuthoritativeWorldItemAppearanceFrame* out)
{
	if (out)
		ResetAuthoritativeWorldItemFrame(out);
	if (!game || !renderer || !server)
		return false;

	const bool use_authoritative_item_visuals =
		(world && server->authority.auth_item.item_count > 0);
	// FL-4137 Gap B: snapshot once per frame, before publishing any row. The
	// preview validity mirror reads the same set of placed items the appearance
	// loop will publish; this avoids a second authority path and keeps the
	// preview check ordered against publication (Law 1, Law 3).
	AuthoritativeWorldItemPlacedSnapshot placed_snapshot[
		AUTHORITATIVE_WORLD_ITEM_PLACED_SNAPSHOT_CAP] = {};
	const int placed_snapshot_count =
		use_authoritative_item_visuals
			? CollectClientPlacedItemColliders(
				  placed_snapshot, AUTHORITATIVE_WORLD_ITEM_PLACED_SNAPSHOT_CAP)
			: 0;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		AuthoritativeItemServerState::ItemVisual* vis = &server->authority.auth_item.item_visuals[i];
		const ::AuthoritativeItemState* ai = &server->authority.auth_item.items[i];
		const AppearanceCatalogItemDef* catalog_item = ai->valid
			? FindAppearanceCatalogItemById(ai->item_definition_id)
			: 0;
		const bool held_placeable_preview =
			use_authoritative_item_visuals &&
			server->connection.local_id >= 0 &&
			ai->valid &&
			ai->owner_id == (uint16_t)server->connection.local_id &&
			ai->equip_slot_kind_id == APPEARANCE_SLOT_KIND_HELD_ITEM &&
			catalog_item &&
			catalog_item->placeable;
		const bool active =
			use_authoritative_item_visuals && ai->valid &&
			(ai->owner_id == 0xffff || held_placeable_preview);
		if (!active)
		{
			// FL-4600 delete-first: the former unconditional
			// `[FL-4600] inactive item visual delete ...` fprintf here owned the
			// retirement-provenance fact. It is removed; that fact is now owned
			// by the single ASan-gated provenance instrument routed through
			// DeleteAuthoritativeWorldItemInst (stage 431 below), which also adds
			// the double-retire fail-before-free guard the bare fprintf lacked.
			DeleteAuthoritativeWorldItemInst(vis, 431);
			vis->item_id = 0;
			vis->visual_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_NONE;
			continue;
		}

		if (vis->item_id != ai->item_id && vis->inst)
			DeleteAuthoritativeWorldItemInst(vis, 433);
		vis->item_id = ai->item_id;

		uint8_t render_visual_failure_reason =
			ACTOR_VISUAL_ITEM_FAILURE_NONE;
		const char* world_sprite_path =
			catalog_item ? catalog_item->world_sprite_path : 0;
		Sprite* spr3d = ResolveAuthoritativeItemSprite3D(
			ai, &render_visual_failure_reason);
		vis->visual_failure_reason = render_visual_failure_reason;
		if (!spr3d)
		{
			DeleteAuthoritativeWorldItemSpriteInst(vis, 435);
			continue;
		}

		float pos[3] = { ai->pos[0], ai->pos[1], ai->pos[2] };
		// FL-4137 Gap B: catalog-derived collision footprint shared by placed
		// items and the held preview row. Defaults (1.0 / 2.0) match the server
		// defaults in SvrPlacedBlockPositionOccupied so unknown / non-block
		// catalog rows mirror the same fallback the server uses.
		float row_half_extent = 1.0f;
		float row_height = 2.0f;
		if (!ResolveAuthoritativeWorldItemBlockGeometry(
				catalog_item, spr3d, &row_half_extent, &row_height))
		{
			DeleteAuthoritativeWorldItemSpriteInst(vis, 436);
			continue;
		}

		if (held_placeable_preview)
		{
			const float distance =
				catalog_item && catalog_item->place_distance_units > 0.0f
					? catalog_item->place_distance_units
					: 4.0f;
			const float yaw_rad = game->player.dir * (float)(M_PI / 180.0);
			pos[0] = game->player.pos[0] + cosf(yaw_rad) * distance;
			pos[1] = game->player.pos[1] + sinf(yaw_rad) * distance;
			pos[2] = game->player.pos[2];
			pos[0] = floorf(pos[0]) + 0.5f;
			pos[1] = floorf(pos[1]) + 0.5f;
			SnapHeldPreviewXYToExistingBlockGrid(
				pos,
				row_half_extent,
				placed_snapshot,
				placed_snapshot_count);
		}

		// FL-4137 Gap B: compute preview validity once, before the inst
		// visibility toggle and before row publication, so the toggled inst
		// flag, the published row, and any downstream debug consumer all see
		// the same verdict for this frame.
		uint8_t preview_reason = PREVIEW_PLACEMENT_REASON_NONE;
		if (held_placeable_preview)
		{
			preview_reason = EvaluateHeldPreviewPlacementValidity(
				game->player.pos[2],
				pos,
				row_half_extent,
				row_height,
				ai->item_id,
				placed_snapshot,
				placed_snapshot_count);
		}
		const uint8_t preview_valid =
			held_placeable_preview
				? (preview_reason == PREVIEW_PLACEMENT_REASON_OK ? 1 : 0)
				: 0;
		float yaw = (float)((ai->item_id * 37u) % 360u);
		int anim = 0;
		int frame = 0;
		if (spr3d->anims > 0)
		{
			int len = spr3d->anim[0].length;
			if (len > 0)
				frame = ((int)(render_stamp_us / 100000ULL) + (ai->item_id & 7)) % len;
		}

		if (vis->inst && GetInstWorld(vis->inst) != world)
			DeleteAuthoritativeWorldItemInst(vis, 437);
		if (!vis->inst)
		{
			g_web_render_stage_code = 439;
			int flags = INST_USE_TREE | INST_VISIBLE | INST_VOLATILE;
			int reps[4] = { 0, 0, 0, 0 };
			vis->inst = CreateInst(world, spr3d, flags, pos, yaw, anim, frame, reps, 0, -1);
			// FL-4600: assign this SpriteInst exactly one allocation identity.
			FL4600_PROV_CREATE(vis->inst, ai->item_id, i, world);
		}
		if (!vis->inst)
			continue;

		g_web_render_stage_code = 440;
		int reps[4] = { 0, 0, 0, 0 };
		UpdateSpriteInst(world, vis->inst, spr3d, pos, yaw, anim, frame, reps);

		// FL-4137 #69 (2026-05-31): for placed-world rows whose catalog
		// declares world_mesh_path, the visible body must render as the AKM
		// mesh inst, not the XP sprite. Sync the mesh inst at the
		// authoritative item position. Held preview keeps the sprite (held
		// preview is presentation, not a placed world body).
		const bool placed_collidable_state =
			(ai->v2_state_flags & APPEARANCE_ITEM_STATE_PLACED) &&
			(ai->v2_state_flags & APPEARANCE_ITEM_STATE_COLLIDABLE);
		const bool render_as_mesh =
			!held_placeable_preview &&
			placed_collidable_state &&
			catalog_item &&
			catalog_item->world_mesh_path &&
			catalog_item->world_mesh_path[0];
		SyncAuthoritativePlacedBlockMeshInst(world, vis, ai, catalog_item,
											  render_as_mesh);

		// FL-4137 Gap B: visibility-toggle affordance for the local held
		// placeable preview. When the client mirror of the server placement
		// rule rejects the position, the preview inst is hidden as the
		// closest in-scope render hint (true tint plumbing does not exist on
		// the sprite render path; adding it would be a render-pipeline
		// change outside the Gap B scope). Server reject remains final
		// truth — the visibility toggle is render-only and never gates
		// authority. ShowInst/HideInst on placed/non-preview rows is
		// untouched.
		if (held_placeable_preview)
		{
			if (preview_valid)
				ShowInst(vis->inst);
			else
				HideInst(vis->inst);
		}
		else if (render_as_mesh && vis->mesh_inst)
		{
			// FL-4137 #69: mesh owns the visible body; suppress the sprite
			// inst so the XP layered art does not bleed through.
			HideInst(vis->inst);
		}
		else
		{
			// Non-mesh placed/world row: keep the sprite visible.
			ShowInst(vis->inst);
		}
		int item_view[3];
		ProjectCoords(renderer, pos, item_view);
		const bool item_on_screen =
			item_view[0] >= 0 && item_view[0] < viewport_width &&
			item_view[1] >= 0 && item_view[1] < viewport_height;
		// FL-4137 Gap A: when this row is the local held placeable preview and
		// projects on-screen, publish a single mobile tap hit rect into the
		// frame. Also gated by preview_valid — Gap B HideInst()s the preview
		// when the client mirror of the server placement rule rejects the
		// position, so an invisible preview must not leave a tappable rect
		// behind that would swallow normal tap routing and dispatch place
		// intent the user never saw. preview_valid==1 iff
		// preview_reason == PREVIEW_PLACEMENT_REASON_OK (see line ~460), so
		// the contact surface stays lockstep with the inst visibility toggle.
		// The rect is cell-space (ProjectCoords already returns AnsiCell
		// units), pre-clipped to the viewport so the tap router can compare
		// it against ScreenToCell-resolved tap coords directly. A fixed half-
		// pad of HELD_PREVIEW_TAP_PAD_CELLS gives a touch-friendly hit
		// target without depending on per-frame sprite atlas geometry — the
		// preview's visible block is small enough that a few cells of
		// padding stays under any nearby pickup-strip / inventory UI. Server
		// reject remains final truth; this surface is render-only (Law 3, 6).
		if (out && held_placeable_preview && item_on_screen && preview_valid)
		{
			const int HELD_PREVIEW_TAP_PAD_CELLS = 4;
			int x0 = item_view[0] - HELD_PREVIEW_TAP_PAD_CELLS;
			int y0 = item_view[1] - HELD_PREVIEW_TAP_PAD_CELLS;
			int x1 = item_view[0] + HELD_PREVIEW_TAP_PAD_CELLS;
			int y1 = item_view[1] + HELD_PREVIEW_TAP_PAD_CELLS;
			if (x0 < 0) x0 = 0;
			if (y0 < 0) y0 = 0;
			if (x1 >= viewport_width) x1 = viewport_width - 1;
			if (y1 >= viewport_height) y1 = viewport_height - 1;
			out->held_preview_mobile_contact.valid = 1;
			out->held_preview_mobile_contact.item_id = ai->item_id;
			out->held_preview_mobile_contact.cell_x0 = (int16_t)x0;
			out->held_preview_mobile_contact.cell_y0 = (int16_t)y0;
			out->held_preview_mobile_contact.cell_x1 = (int16_t)x1;
			out->held_preview_mobile_contact.cell_y1 = (int16_t)y1;
		}
		// FL-4137 #69: when the placed body renders as an AKM mesh, the
		// sprite Inst is intentionally hidden (so the XP layered art does
		// not bleed through). The published-row visibility predicate must
		// then track the mesh Inst's visibility, not the sprite Inst's.
		const bool sprite_inst_visible =
			(!world || GetInstWorld(vis->inst) == world) &&
			(GetInstFlags(vis->inst) & INST_VISIBLE);
		const bool mesh_inst_visible =
			vis->mesh_inst &&
			(!world || GetInstWorld(vis->mesh_inst) == world) &&
			(GetInstFlags(vis->mesh_inst) & INST_VISIBLE);
		const bool item_inst_visible =
			(render_as_mesh && vis->mesh_inst) ? mesh_inst_visible
											   : sprite_inst_visible;
		// FL-4137 Gap B: held preview always publishes its row regardless of
		// inst visibility, so debug observers / future HUD consumers can see
		// preview_valid=0 even when the preview is currently hidden. Placed /
		// world rows keep the original on-screen + inst-visible skip
		// behaviour so off-screen rows do not pollute the visible_rows feed.
		const bool publish_regardless = held_placeable_preview;
		if ((!item_on_screen || !item_inst_visible) && !publish_regardless)
			continue;
		if (!out)
			continue;

		AuthoritativeWorldItemAppearanceRow row = {};
		ResetAuthoritativeWorldItemRow(&row);
		row.item_id = ai->item_id;
		row.definition_id = ai->item_definition_id;
		row.visual_style_id = ai->visual_style_id;
		row.world_sprite_source_hash = HashWorldSpritePath16(world_sprite_path);
		row.world_sprite_family_kind = WorldSpriteFamilyKindFromPath(world_sprite_path);
		row.render_visual_failure_reason = render_visual_failure_reason;
		row.state = ai;
		row.on_screen = item_on_screen ? 1 : 0;
		row.inst_visible = item_inst_visible ? 1 : 0;
		float dx = ai->pos[0] - game->player.pos[0];
		float dy = ai->pos[1] - game->player.pos[1];
		if (held_placeable_preview)
		{
			dx = pos[0] - game->player.pos[0];
			dy = pos[1] - game->player.pos[1];
		}
		row.pickup_distance2 = dx * dx + dy * dy;
		row.pickup_in_range =
			!held_placeable_preview &&
			row.pickup_distance2 <=
			(AUTHORITATIVE_WORLD_ITEM_PICKUP_RADIUS *
			 AUTHORITATIVE_WORLD_ITEM_PICKUP_RADIUS) ? 1 : 0;
		row.pickup_sprite_2d = ResolveAuthoritativeItemSprite2D(
			ai, &row.pickup_visual_failure_reason);
		// FL-4137 Gap B (review fix): additive placement preview/affordance
		// fields. placed reflects APPEARANCE_ITEM_STATE_PLACED on the
		// authoritative item state, not owner_id alone, because dropped
		// world-owned loot can also carry owner_id==0xffff without being a
		// placed block. half_extent and height are catalog-derived so debug
		// HUD / future tint pipeline read the same footprint the server
		// placement rule used. preview_valid / preview_reason are set only
		// on the held-preview row.
		row.placed =
			(ai->v2_state_flags & APPEARANCE_ITEM_STATE_PLACED) ? 1 : 0;
		row.half_extent = row_half_extent;
		row.height = row_height;
			// FL-4137 #31/#57: visual geometry is the BlockDef-normalized placed-block
			// contract, not the raw XP bbox. A real XP can be many glyph rows tall,
			// but placed-block gameplay remains a step-height block when the catalog
			// declares that height. Collision/support/proof/render rows read this
			// same geometry so the proof cannot make a grey strip pass again.
			row.visual_bottom_z = pos[2];
			row.visual_top_z = pos[2] + row_height;
		// FL-4137 #35 / FL-4163: project this row's TOP and BOTTOM to screen.
		// item_view already holds the projection of pos (the bottom of the
		// block); reproject for the top. ProjectCoords returns ints in cell
		// units, matching the renderbuf coordinate system the proof samples.
			{
				float top_world[3] = { pos[0], pos[1], row.visual_top_z };
				float bot_world[3] = { pos[0], pos[1], row.visual_bottom_z };
				int top_view[3] = { 0, 0, 0 };
				int bot_view[3] = { 0, 0, 0 };
				const bool top_ok = ProjectCoords(renderer, top_world, top_view);
				const bool bot_ok = ProjectCoords(renderer, bot_world, bot_view);
				if (top_ok && bot_ok)
				{
					row.screen_valid = 1;
					row.screen_top_col = (int16_t)top_view[0];
					row.screen_top_row = (int16_t)top_view[1];
					row.screen_bottom_col = (int16_t)bot_view[0];
					row.screen_bottom_row = (int16_t)bot_view[1];
				}
				else
				{
					row.screen_valid = 0;
				}
			}
			// FL-4137 collision wireframe: project all 8 corners of the placed
			// block collision AABB so the JS overlay can draw the actual physics
			// volume on the canvas. This must not publish the held preview row:
			// preview pos is derived from game->player.pos, so drawing it here
			// makes the box follow fly movement instead of reflecting a
			// server-placed collision body.
			{
				const uint16_t placed_collidable_mask =
					(uint16_t)(APPEARANCE_ITEM_STATE_PLACED |
						   APPEARANCE_ITEM_STATE_COLLIDABLE);
				row.corners_valid =
					(ai->owner_id == 0xffff &&
					 (ai->v2_state_flags & placed_collidable_mask) == placed_collidable_mask)
						? 1
						: 0;
				const float he = row.half_extent;
				const float collision_bottom_z = pos[2];
				const float collision_top_z = pos[2] + row.height;
				for (int corner = 0; corner < 8 && row.corners_valid; corner++)
				{
					const float cx = (corner & 1) ? pos[0] + he : pos[0] - he;
					const float cy = (corner & 2) ? pos[1] + he : pos[1] - he;
					const float cz = (corner & 4) ? collision_top_z : collision_bottom_z;
					float cw[3] = { cx, cy, cz };
					int cv[3] = { 0, 0, 0 };
					if (!ProjectCoords(renderer, cw, cv))
					{
						row.corners_valid = 0;
						break;
					}
					row.corner_col[corner] = (int16_t)cv[0];
					row.corner_row[corner] = (int16_t)cv[1];
				}
			}
		row.preview_valid = preview_valid;
		row.preview_reason = preview_reason;

		if (out->visible_row_count < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
			out->visible_rows[out->visible_row_count++] = row;
		if (row.pickup_in_range)
			InsertPickupRowSorted(out, &row);
	}
	return true;
}
