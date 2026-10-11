// LINEAGE_JSON: {"fl":"FL-3012","note":"NPC render-stack telemetry — added because user report of NPC head/arms missing was real and the old recorder only proved body presence. This is measurement/instrumentation, NOT a fix. The visual bug lane remains open (FL-3005: gameplay_npc_live_anim_advancing_ok=false). DO NOT treat as closed."}
#include "snapshot_client/snapshot_npc_visual_lifecycle.h"

#include <string.h>

#include "actor_visual_profile_runtime.h"
#include "game.h"
#include "snapshot_npc_sprite_data_tag.h"
#include "snapshot_npc_visual_contract.h"

extern int g_web_render_stage_code;
extern uint64_t a3dGetTime();

static ActorPresentationInput MakeActorPresentationNpcInput(
	const ServerSnapshotNpcRepository::SnapshotNpcState* sn)
{
	ActorPresentationInput input = {};
	if (!sn)
		return input;
	input.life_state = sn->life_state;
	input.mount_state = sn->mount_state;
	input.locomotion_state = sn->locomotion_state;
	input.combat_state = sn->combat_state;
	input.presentation_kind_id = sn->presentation_kind_id;
	input.appearance_state = &sn->appearance_v2;
	input.authoritative_tick = sn->last_authoritative_tick;
	input.presentation_started_tick = sn->presentation_started_tick;
	return input;
}

enum SnapshotNpcInstDeleteReason
{
	SNAPSHOT_NPC_INST_DELETE_REASON_NONE = 0,
	SNAPSHOT_NPC_INST_DELETE_REASON_INACTIVE = 1,
	SNAPSHOT_NPC_INST_DELETE_REASON_ENTITY_MISMATCH = 2,
	SNAPSHOT_NPC_INST_DELETE_REASON_WORLD_MISMATCH = 3,
	SNAPSHOT_NPC_INST_DELETE_REASON_NULL_SPRITE = 4,
};

static void ResetSnapshotNpcVisualSlotState(ServerSnapshotNpcRepository::SnapshotNpcVisual* vis)
{
	if (!vis)
		return;
	vis->entity_id = 0;
	vis->presentation_kind_id = 0;
	vis->presentation_started_tick = 0;
	vis->sprite = 0;
	vis->sprite_miss_frames = 0;
	vis->selector_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
	vis->last_inst_delete_reason = SNAPSHOT_NPC_INST_DELETE_REASON_NONE;
	vis->last_inst_delete_miss_frames = 0;
	vis->inst_create_count = 0;
	vis->inst_delete_count = 0;
	vis->hp = 0;
	vis->max_hp = 0;
}

static void DeleteSnapshotNpcVisualInst(
	ServerSnapshotNpcRepository::SnapshotNpcVisual* vis,
	uint8_t reason,
	uint8_t miss_frames,
	int stage_code)
{
	if (!vis || !vis->inst)
		return;
	g_web_render_stage_code = stage_code;
	DeleteInst(vis->inst);
	vis->inst = 0;
	if (vis->inst_delete_count < 0xffff)
		vis->inst_delete_count++;
	vis->last_inst_delete_reason = reason;
	vis->last_inst_delete_miss_frames = miss_frames;
}

void DestroySnapshotNpcVisuals(ServerSnapshotNpcRepository* repo)
{
	if (!repo)
		return;
	for (int i = 0; i < ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS; i++)
	{
		DeleteSnapshotNpcVisualInst(
			&repo->visuals[i],
			SNAPSHOT_NPC_INST_DELETE_REASON_INACTIVE,
			0,
			31);
		ResetSnapshotNpcVisualSlotState(&repo->visuals[i]);
	}
}

bool UpdateSnapshotNpcVisualLifecycleSlot(
	const SnapshotClientState* snapshot_client,
	ServerSnapshotNpcRepository* npc_repo,
	World* world,
	Renderer* renderer,
	uint64_t render_stamp_us,
	int viewport_width,
	int viewport_height,
	int slot_index,
	SnapshotNpcVisualLifecycleProbe* out)
{
	if (out)
		memset(out, 0, sizeof(*out));
	if (!npc_repo || !renderer || slot_index < 0 || slot_index >= ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
		return false;

	(void)snapshot_client;

	ServerSnapshotNpcRepository::SnapshotNpcVisual* vis = &npc_repo->visuals[slot_index];
	const bool use_authoritative_npc_visuals =
		(world && npc_repo->npc_count > 0);
	const ServerSnapshotNpcRepository::SnapshotNpcState* sn = 0;
	bool active = false;
	if (use_authoritative_npc_visuals && slot_index < (int)npc_repo->npc_count)
	{
		sn = &npc_repo->npcs[slot_index];
		active = sn->entity_id != 0;
	}

	if (!active)
	{
		DeleteSnapshotNpcVisualInst(
			vis,
			SNAPSHOT_NPC_INST_DELETE_REASON_INACTIVE,
			0,
			31);
		ResetSnapshotNpcVisualSlotState(vis);
		return false;
	}

	if (vis->entity_id != sn->entity_id && vis->inst)
	{
		DeleteSnapshotNpcVisualInst(
			vis,
			SNAPSHOT_NPC_INST_DELETE_REASON_ENTITY_MISMATCH,
			0,
			32);
	}
	if (vis->entity_id != sn->entity_id)
		ResetSnapshotNpcVisualSlotState(vis);
	vis->entity_id = sn->entity_id;

	SnapshotNpcVisualResolveDecision resolve_decision =
		SnapshotNpcVisualResolveDecisionFor(
			vis->presentation_kind_id,
			vis->presentation_started_tick,
			vis->sprite != 0,
			sn->presentation_kind_id,
			sn->presentation_started_tick);
	ActorPresentationResult resolved = {};
	bool resolved_this_frame = false;
	if (resolve_decision.presentation_kind_unset)
	{
		if (resolve_decision.clear_cached_sprite)
		{
			vis->presentation_kind_id = sn->presentation_kind_id;
			vis->presentation_started_tick = sn->presentation_started_tick;
			vis->sprite = 0;
			vis->sprite_miss_frames = 0;
			vis->selector_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
		}
	}
	else if (resolve_decision.need_resolve)
	{
		if (resolve_decision.reset_sprite_miss_frames)
			vis->sprite_miss_frames = 0;
		ActorPresentationInput npc_input = MakeActorPresentationNpcInput(sn);
		(void)render_stamp_us;
		const uint64_t resolve_begin_us = a3dGetTime();
		resolved = ResolveActorVisualProfilePresentation(npc_input);
		if (out)
		{
			out->resolve_us += (uint32_t)(a3dGetTime() - resolve_begin_us);
			out->compose_us += resolved.render_compose_us;
		}
		resolved_this_frame = true;
		vis->presentation_kind_id = sn->presentation_kind_id;
		vis->presentation_started_tick = sn->presentation_started_tick;
		vis->sprite = resolved.sprite;
		vis->selector_failure_reason = resolved.selector_failure_reason;
		if (vis->sprite)
			vis->sprite_miss_frames = 0;
	}

	float pos[3] = { sn->pos[0], sn->pos[1], sn->pos[2] };
	int view[3] = { -9999, -9999, 0 };
	ProjectCoords(renderer, pos, view);
	int on_screen =
		(view[0] >= 0 && view[0] < viewport_width &&
		 view[1] >= 0 && view[1] < viewport_height) ? 1 : 0;

	Sprite* spr = vis->sprite;
	if (!spr)
	{
		if (vis->sprite_miss_frames < 255)
			vis->sprite_miss_frames++;
		DeleteSnapshotNpcVisualInst(
			vis,
			SNAPSHOT_NPC_INST_DELETE_REASON_NULL_SPRITE,
			vis->sprite_miss_frames,
			31);
		if (out)
		{
			out->snapshot = sn;
			out->visual = vis;
			memcpy(out->pos, pos, sizeof(out->pos));
			out->on_screen = on_screen;
		}
		return true;
	}
	vis->sprite_miss_frames = 0;

	if (!resolved_this_frame)
	{
		ActorPresentationInput npc_input = MakeActorPresentationNpcInput(sn);
		const uint64_t resolve_begin_us = a3dGetTime();
		resolved = ResolveActorVisualProfilePresentation(npc_input);
		if (out)
		{
			out->resolve_us += (uint32_t)(a3dGetTime() - resolve_begin_us);
			out->compose_us += resolved.render_compose_us;
		}
		resolved_this_frame = true;
		vis->selector_failure_reason = resolved.selector_failure_reason;
	}
	int anim = resolved.anim;
	int frame = resolved.frame;

	if (vis->inst && GetInstWorld(vis->inst) != world)
	{
		DeleteSnapshotNpcVisualInst(
			vis,
			SNAPSHOT_NPC_INST_DELETE_REASON_WORLD_MISMATCH,
			0,
			31);
	}
	if (!vis->inst)
	{
		int flags = INST_USE_TREE | INST_VISIBLE | INST_VOLATILE;
		int reps[4] = { 0, 0, 0, 0 };
		vis->inst = CreateInst(world, spr, flags, pos, sn->dir, anim, frame, reps, 0, -1);
		if (vis->inst)
		{
			SetInstSpriteData(vis->inst, (void*)kSnapshotNpcBodySpriteDataTag);
			if (vis->inst_create_count < 0xffff)
				vis->inst_create_count++;
		}
	}
	if (vis->inst)
	{
		ShowInst(vis->inst);
		int reps[4] = { 0, 0, 0, 0 };
		UpdateSpriteInst(world, vis->inst, spr, pos, sn->dir, anim, frame, reps);
		vis->hp = sn->hp;
		vis->max_hp = sn->max_hp;
	}

	if (out)
	{
		out->snapshot = sn;
		out->visual = vis;
		out->resolved = resolved;
		memcpy(out->pos, pos, sizeof(out->pos));
		out->on_screen = on_screen;
		out->resolved_valid = true;
	}
	return true;
}
