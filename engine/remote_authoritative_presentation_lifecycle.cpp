#include "remote_authoritative_presentation_lifecycle.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "authoritative_presentation_adapters.h"
#include "actor_visual_profile_runtime.h"
#include "game.h"
#include "snapshot_client/remote_snapshot_presentation_track.h"
#include "remote_observer_probe.h"

extern uint64_t a3dGetTime();

namespace
{
	// If `human` points inside the server roster array (regardless of whether
	// the slot is populated), returns its slot index; otherwise -1.
	static int ServerRosterSlot(const Server* server, const Human* human)
	{
		if (!server || !server->authority.others || server->connection.max_clients <= 0)
			return -1;
		if (human < server->authority.others ||
		    human >= server->authority.others + server->connection.max_clients)
			return -1;
		return static_cast<int>(human - server->authority.others);
	}
}

void RequestRemoteActorPresentationInstInvalidation(
	RemoteActorPresentationTrack* track,
	int reason,
	bool clear_aliases)
{
	if (!track || reason <= 0)
		return;
	if (track->pending_inst_invalidation_reason == 0 ||
		(track->pending_inst_invalidation_clear_aliases == 0 && clear_aliases))
	{
		track->pending_inst_invalidation_reason = (uint8_t)reason;
	}
	if (clear_aliases)
		track->pending_inst_invalidation_clear_aliases = 1;
}

int DebugRemotePresentationCenterGlyph(const Human* remote)
{
	if (!remote || !remote->sprite ||
		remote->anim < 0 || remote->anim >= remote->sprite->anims)
	{
		return -1;
	}
	if (remote->frame < 0 || remote->frame >= remote->sprite->anim[remote->anim].length)
		return -1;
	int fidx = remote->sprite->anim[remote->anim].frame_idx[remote->frame];
	Sprite::Frame* f = remote->sprite->atlas + fidx;
	if (!f || !f->cell || f->width <= 0 || f->height <= 0)
		return -1;
	int cx = f->width / 2;
	int cy = f->height / 2;
	return f->cell[cy * f->width + cx].gl;
}

void QueueRemoteActorPresentationInstInvalidation(Human* remote, World* world)
{
	if (!remote || !world || !remote->inst)
		return;
	if (GetInstWorld(remote->inst) != world)
		RequestRemoteActorPresentationInstInvalidation(
			&remote->remote_presentation_track, 21, true);
	if (GetInstSpriteData(remote->inst) != remote)
		RequestRemoteActorPresentationInstInvalidation(
			&remote->remote_presentation_track, 22, true);
}

namespace
{
static void NoteRemoteInstLifecycleDebug(
	Game* game,
	const Server* server,
	Human* remote,
	int kind,
	int reason,
	Inst* pre_inst,
	Inst* post_inst,
	bool was_dead)
{
	if (!game || !server || !remote || !server->authority.others || server->connection.max_clients <= 0)
		return;
	int tracked_pid = ServerRosterSlot(server, remote);
	bool track =
		(tracked_pid >= 0) &&
		((tracked_pid == game->debug.dbg_remote0_pid) ||
		 (tracked_pid == game->debug.dbg_last_remote0_pid) ||
		 ((game->debug.dbg_remote0_pid < 0 && game->debug.dbg_last_remote0_pid < 0) && remote == server->authority.head));
	if (!track)
		return;
	game->debug.dbg_remote0_inst_event_last_kind = kind;
	game->debug.dbg_remote0_inst_event_last_reason = reason;
	game->debug.dbg_remote0_inst_event_last_was_dead = was_dead ? 1 : 0;
	game->debug.dbg_remote0_inst_event_last_had_inst = pre_inst ? 1 : 0;
	game->debug.dbg_remote0_inst_event_last_pre_visible =
		(pre_inst && (GetInstFlags(pre_inst) & INST_VISIBLE)) ? 1 : 0;
	game->debug.dbg_remote0_inst_event_last_post_visible =
		(post_inst && (GetInstFlags(post_inst) & INST_VISIBLE)) ? 1 : 0;
	game->debug.dbg_remote0_inst_event_last_post_cookie_match =
		(post_inst && GetInstSpriteData(post_inst) == remote) ? 1 : 0;
}

static void ClearRemoteInstAliases(Server* server, Inst* inst, int except_id)
{
	if (!server || !inst || !server->authority.others || server->connection.max_clients <= 0)
		return;
	for (int pid = 0; pid < server->connection.max_clients; pid++)
	{
		if (pid == except_id || pid == server->connection.local_id)
			continue;
		Human* other = server->authority.others + pid;
		if (other->inst == inst)
			other->inst = 0;
	}
}

static void ConsumeRemoteActorPresentationInstInvalidation(
	Game* game,
	Server* server,
	Human* remote,
	int tracked_pid)
{
	if (!remote)
		return;
	RemoteActorPresentationTrack* track = &remote->remote_presentation_track;
	const int reason = track->pending_inst_invalidation_reason;
	const bool clear_aliases = track->pending_inst_invalidation_clear_aliases != 0;
	track->pending_inst_invalidation_reason = 0;
	track->pending_inst_invalidation_clear_aliases = 0;
	if (reason == 0 || !remote->inst)
		return;
	RemoteAuthoritativePresentationDeleteInst(
		game, server, remote, tracked_pid, reason, clear_aliases);
}

static RemoteActorPresentationMaterializationResult MaterializeRemoteActorPresentation(
	Game* game,
	Server* server,
	World* world,
	Renderer* renderer,
	int width,
	int height,
	Human* remote,
	int tracked_pid,
	const float render_pos[3],
	float render_dir,
	const ActorPresentationResult* resolved_this_frame)
{
	RemoteActorPresentationMaterializationResult out = {};
	out.inst_sprite_family_kind = 0;
	out.inst_sprite_matches_owner = 0;
	out.view_x = -9999;
	out.view_y = -9999;
	if (!remote)
		return out;

	if (world)
	{
		ConsumeRemoteActorPresentationInstInvalidation(game, server, remote, tracked_pid);
		if (remote->inst && !remote->sprite)
		{
			RemoteAuthoritativePresentationDeleteInst(
				game, server, remote, tracked_pid, 20, false);
		}
		if (remote->inst && GetInstWorld(remote->inst) != world)
			RemoteAuthoritativePresentationDeleteInst(
				game, server, remote, tracked_pid, 21, true);
			if (remote->inst && GetInstSpriteData(remote->inst) != remote)
				RemoteAuthoritativePresentationDeleteInst(
					game, server, remote, tracked_pid, 22, true);

			if (!remote->inst && remote->sprite)
			{
				(void)RemoteAuthoritativePresentationRecreateInst(
					game, server, world, remote, true, 23, 0, 0, 0, 0,
					resolved_this_frame);
			}

		if (remote->inst)
		{
			int reps[4] = { 0, 0, 0, 0 };
			SetInstSpriteData(remote->inst, remote);
			UpdateSpriteInst(
				world,
				remote->inst,
				remote->sprite,
				const_cast<float*>(render_pos),
				render_dir,
				remote->anim,
				remote->frame,
				reps);
				if (!(GetInstFlags(remote->inst) & INST_VISIBLE))
				{
					out.recreate_reason = REMOTE_ACTOR_PRESENTATION_RECOVERY_HIDDEN;
					(void)RemoteAuthoritativePresentationRecreateInst(
						game, server, world, remote, true, 2, 0, 0, 0, 0,
						resolved_this_frame);
				}
		}
		else
		{
			out.recreate_reason = REMOTE_ACTOR_PRESENTATION_RECOVERY_MISSING;
			(void)RemoteAuthoritativePresentationRecreateInst(
				game, server, world, remote, true, 3, 0, 0, 0, 0,
				resolved_this_frame);
		}
	}

	if (remote->inst)
	{
		// FL-2407 / headed mounted Z lane: this is the runtime truth for whether
		// the remote post-interp path still holds a live inst. Pair this with the
		// resolved compose_mode before claiming mounted is single-owner again.
		out.has_inst = 1;
		out.inst_world_match = (!world || GetInstWorld(remote->inst) == world) ? 1 : 0;
		out.inst_visible = (GetInstFlags(remote->inst) & INST_VISIBLE) ? 1 : 0;
		int inst_reps[4] = { 0, 0, 0, 0 };
		Sprite* inst_sprite = GetInstSprite(remote->inst, 0, 0, 0, 0, inst_reps);
		out.inst_sprite_family_kind = 0;
		out.inst_sprite_matches_owner = (GetInstSpriteData(remote->inst) == remote) ? 1 : 0;
	}

	if (renderer)
	{
		int view[3] = { -9999, -9999, -9999 };
		ProjectCoords(renderer, render_pos, view);
		out.view_x = view[0];
		out.view_y = view[1];
		out.on_screen = (view[0] >= 0 && view[0] < width && view[1] >= 0 && view[1] < height) ? 1 : 0;
		out.label_visible = out.on_screen;
		out.body_visible = out.on_screen && remote->sprite && out.has_inst && out.inst_visible;
		out.label_only = out.label_visible && !out.body_visible ? 1 : 0;
	}

	return out;
}
}

bool RemoteAuthoritativePresentationIsServerLocalSlot(const Server* server, const Human* remote)
{
	if (!server || !remote)
		return false;
	if (server->connection.local_id < 0 || server->connection.local_id >= server->connection.max_clients)
		return false;
	return remote == server->authority.others + server->connection.local_id;
}

int RemoteAuthoritativePresentationPurgeDuplicateInsts(Server* server)
{
	if (!server)
		return 0;
	Inst* seen[64] = {};
	int seen_count = 0;
	Inst* duplicate_insts[64] = {};
	int duplicate_count = 0;
	for (int pid = 0; pid < server->connection.max_clients; pid++)
	{
		if (pid == server->connection.local_id)
			continue;
		Human* remote = server->authority.others + pid;
		Inst* inst = remote->inst;
		if (!inst)
			continue;

		bool already_duplicate = false;
		for (int i = 0; i < duplicate_count; i++)
		{
			if (duplicate_insts[i] == inst)
			{
				already_duplicate = true;
				break;
			}
		}
		if (already_duplicate)
			continue;

		bool seen_before = false;
		for (int i = 0; i < seen_count; i++)
		{
			if (seen[i] == inst)
			{
				seen_before = true;
				break;
			}
		}
		if (seen_before)
		{
			if (duplicate_count < (int)(sizeof(duplicate_insts) / sizeof(duplicate_insts[0])))
				duplicate_insts[duplicate_count++] = inst;
			continue;
		}
		if (seen_count < (int)(sizeof(seen) / sizeof(seen[0])))
			seen[seen_count++] = inst;
	}

	// FL-2957 secondary falsifier: keep duplicate-inst purge behavior but avoid
	// the post-rebuild O(n^2) every-frame scan from FL-2957 suspect #2.
	for (int i = 0; i < duplicate_count; i++)
	{
		Inst* shared = duplicate_insts[i];
		for (int qid = 0; qid < server->connection.max_clients; qid++)
		{
			if (qid == server->connection.local_id)
				continue;
			Human* remote = server->authority.others + qid;
			if (remote->inst == shared)
				remote->inst = 0;
		}
		DeleteInst(shared);
	}
	return duplicate_count;
}

void RemoteAuthoritativePresentationDeleteInst(
	Game* game,
	Server* server,
	Human* remote,
	int tracked_pid,
	int reason,
	bool clear_aliases)
{
	if (!server || !remote || !remote->inst)
		return;
	Inst* pre_inst = remote->inst;
	bool was_dead = RemoteObserverHasDeathEpoch(remote);
	if (clear_aliases)
		ClearRemoteInstAliases(server, remote->inst, tracked_pid);
	if (was_dead)
		RemoteObserverNoteCorpseDelete(server, remote, tracked_pid, (uint32_t)reason);
	NoteRemoteInstLifecycleDebug(game, server, remote, 1, reason, pre_inst, 0, was_dead);
	DeleteInst(remote->inst);
	remote->inst = 0;
	if (game)
		game->debug.dbg_remote_inst_delete_count++;
}

bool RemoteAuthoritativePresentationRecreateInst(
	Game* game,
	Server* server,
	World* world,
	Human* remote,
	bool snap_to_target,
	int reason,
	int trigger_on_screen,
	int trigger_label_visible,
	int trigger_body_visible,
	int trigger_label_only,
	const ActorPresentationResult* resolved_this_frame)
{
	if (!game || !server || !world || !remote || !server->authority.others || server->connection.max_clients <= 0)
		return false;
	bool was_dead = RemoteObserverHasDeathEpoch(remote);
	int tracked_pid = ServerRosterSlot(server, remote);
	bool track_recreate_debug =
		(tracked_pid >= 0) &&
		((tracked_pid == game->debug.dbg_remote0_pid) ||
		 (tracked_pid == game->debug.dbg_last_remote0_pid) ||
		 ((game->debug.dbg_remote0_pid < 0 && game->debug.dbg_last_remote0_pid < 0) && remote == server->authority.head));
	game->debug.dbg_remote0_recreate_attempts++;
	if (track_recreate_debug)
	{
		game->debug.dbg_remote0_recreate_last_reason = reason;
		game->debug.dbg_remote0_recreate_last_snap_to_target = snap_to_target ? 1 : 0;
		game->debug.dbg_remote0_recreate_last_was_dead = was_dead ? 1 : 0;
		game->debug.dbg_remote0_recreate_trigger_on_screen = trigger_on_screen;
		game->debug.dbg_remote0_recreate_trigger_label_visible = trigger_label_visible;
		game->debug.dbg_remote0_recreate_trigger_body_visible = trigger_body_visible;
		game->debug.dbg_remote0_recreate_trigger_label_only = trigger_label_only;
		game->debug.dbg_remote0_recreate_last_had_inst = remote->inst ? 1 : 0;
		game->debug.dbg_remote0_recreate_last_pre_visible =
			(remote->inst && (GetInstFlags(remote->inst) & INST_VISIBLE)) ? 1 : 0;
		game->debug.dbg_remote0_recreate_last_post_visible = 0;
		game->debug.dbg_remote0_recreate_last_post_cookie_match = 0;
	}

	ActorPresentationResult resolved = {};
	if (resolved_this_frame)
	{
		resolved = *resolved_this_frame;
	}
	else
	{
		const uint32_t render_tick = (server->authority.snapshot_client.last_snapshot_tick != 0) ? server->authority.snapshot_client.last_snapshot_tick : 0;
		const uint64_t resolve_begin_us = a3dGetTime();
		resolved =
			ResolveRemoteAuthoritativeCharacterPresentation(remote, remote->clr, render_tick);
		game->debug.dbg_actor_visual_resolve_us +=
			(uint32_t)(a3dGetTime() - resolve_begin_us);
		game->debug.dbg_actor_visual_compose_us += resolved.render_compose_us;
	}
	if (!resolved.sprite)
	{
		// FL-4076: fail-closed on exact-lookup miss. A null resolver result
		// means the server-published CompiledActorVisualKey has no authored
		// CompiledActorVisualRow, or the input state is incoherent.
		// Continuing to show the previous tick's sprite (as FL-3955/FL-3968
		// did) silently masks both content gaps (e.g. missing attack+crossbow
		// rows) and authority drift between AppearanceStateV2 and item/combat
		// publication. Delete the prior inst so the remote actor visually
		// disappears, matching the local fail-closed render contract in
		// engine/game_render_bridge.cpp. Diagnostics still surface the
		// selector failure through render telemetry.
		if (remote->inst)
		{
			Inst* pre_inst = remote->inst;
			if (was_dead)
			RemoteObserverNoteCorpseDelete(server, remote, tracked_pid, (uint32_t)reason);
			DeleteInst(remote->inst);
			remote->inst = 0;
			game->debug.dbg_remote_inst_delete_count++;
			NoteRemoteInstLifecycleDebug(game, server, remote, 1, reason, pre_inst, 0, was_dead);
		}
		return false;
	}
	remote->sprite = resolved.sprite;
	remote->anim = resolved.anim;
	remote->frame = resolved.frame;

	int flags = INST_USE_TREE | INST_VISIBLE | INST_VOLATILE;
	int reps[4] = { 0, 0, 0, 0 };
	const float* create_pos = remote->pos;
	float create_dir = remote->dir;
	if (remote->inst)
	{
		Inst* pre_inst = remote->inst;
		if (was_dead)
			RemoteObserverNoteCorpseDelete(server, remote, tracked_pid, (uint32_t)reason);
		DeleteInst(remote->inst);
		remote->inst = 0;
		game->debug.dbg_remote_inst_delete_count++;
		NoteRemoteInstLifecycleDebug(game, server, remote, 1, reason, pre_inst, 0, was_dead);
	}
	remote->inst = CreateInst(
		world,
		remote->sprite,
		flags,
		const_cast<float*>(create_pos),
		create_dir,
		remote->anim,
		remote->frame,
		reps,
		0,
		-1);
	if (!remote->inst)
		return false;
	game->debug.dbg_remote_inst_create_count++;
	SetInstSpriteData(remote->inst, remote);
	if (was_dead)
		RemoteObserverNoteCorpseCreate(server, remote, tracked_pid, (uint32_t)reason);
	NoteRemoteInstLifecycleDebug(game, server, remote, 2, reason, 0, remote->inst, was_dead);
	if (track_recreate_debug)
	{
		game->debug.dbg_remote0_recreate_last_post_visible =
			(GetInstFlags(remote->inst) & INST_VISIBLE) ? 1 : 0;
		game->debug.dbg_remote0_recreate_last_post_cookie_match =
			(GetInstSpriteData(remote->inst) == remote) ? 1 : 0;
	}
	return true;
}

RemoteAuthoritativePresentationLifecycleResult RunRemoteAuthoritativePresentationLifecycle(
	Game* game,
	Server* server,
	World* world,
	Renderer* renderer,
	int width,
	int height,
	Human* remote,
	uint64_t render_stamp_us)
{
	RemoteAuthoritativePresentationLifecycleResult out = {};
	out.processed = false;
	out.tracked_pid = -1;
	out.render_tick = (server && server->authority.snapshot_client.last_snapshot_tick != 0) ? server->authority.snapshot_client.last_snapshot_tick : 0u;
	out.surface.materialized.view_x = -9999;
	out.surface.materialized.view_y = -9999;
	if (!remote)
		return out;
	if (RemoteAuthoritativePresentationIsServerLocalSlot(server, remote))
		return out;

	out.processed = true;
	out.tracked_pid = ServerRosterSlot(server, remote);
	{
		const uint64_t resolve_begin_us = a3dGetTime();
		out.resolved =
			ResolveRemoteAuthoritativeCharacterPresentation(remote, remote->clr, out.render_tick);
		if (game)
		{
			game->debug.dbg_actor_visual_resolve_us +=
				(uint32_t)(a3dGetTime() - resolve_begin_us);
			game->debug.dbg_actor_visual_compose_us += out.resolved.render_compose_us;
		}
	}
	remote->sprite = out.resolved.sprite;
	remote->anim = out.resolved.anim;
	remote->frame = out.resolved.frame;

	float interp_pos[3] = { remote->pos[0], remote->pos[1], remote->pos[2] };
	float interp_dir = remote->dir;
	float interp_lerp_t = 0.0f;
	int interp_ring_depth = 0;
	int interp_mode = 0;
	const uint64_t interp_delay_us = GetRemoteActorInterpolationDelayUs();
	const uint64_t interp_target_stamp =
		render_stamp_us > interp_delay_us ? (render_stamp_us - interp_delay_us) : 0u;
	const SnapshotPoseEntry* interp_newest =
		GetRemoteActorInterpolationNewestEntry(&remote->remote_presentation_track, 0);
	const SnapshotPoseEntry* interp_older =
		GetRemoteActorInterpolationNewestEntry(&remote->remote_presentation_track, 1);
	const bool interp_active = SampleRemoteActorInterpolation(
		&remote->remote_presentation_track,
		render_stamp_us,
		interp_delay_us,
		interp_pos,
		&interp_dir,
		&interp_lerp_t,
		&interp_ring_depth,
		&interp_mode);
	const float* render_pos = interp_active ? interp_pos : remote->pos;
	float render_dir = interp_active ? interp_dir : remote->dir;

	out.surface.render_pos[0] = render_pos[0];
	out.surface.render_pos[1] = render_pos[1];
	out.surface.render_pos[2] = render_pos[2];
	out.surface.render_dir = render_dir;
	remote->remote_presentation_track.last_render_pose_valid = 1;
	remote->remote_presentation_track.last_render_pos[0] = render_pos[0];
	remote->remote_presentation_track.last_render_pos[1] = render_pos[1];
	remote->remote_presentation_track.last_render_pos[2] = render_pos[2];
	remote->remote_presentation_track.last_render_dir = render_dir;
	out.surface.interp_active = interp_active ? 1 : 0;
	out.surface.interp_ring_depth = interp_ring_depth;
	out.surface.interp_delay_ms = (float)interp_delay_us / 1000.0f;
	out.surface.interp_lerp_t = interp_active ? interp_lerp_t : 0.0f;
	out.surface.interp_fallback_mode = interp_active ? interp_mode : 1;
	out.surface.interp_newest_tick = interp_newest ? (int)interp_newest->tick : 0;
	out.surface.interp_older_tick = interp_older ? (int)interp_older->tick : 0;
	out.surface.interp_newest_wall_age_ms =
		(interp_newest && render_stamp_us >= interp_newest->wall_stamp_us)
			? (float)((render_stamp_us - interp_newest->wall_stamp_us) / 1000.0)
			: -1.0f;
	out.surface.interp_older_wall_age_ms =
		(interp_older && render_stamp_us >= interp_older->wall_stamp_us)
			? (float)((render_stamp_us - interp_older->wall_stamp_us) / 1000.0)
			: -1.0f;
	out.surface.interp_target_age_ms =
		(render_stamp_us >= interp_target_stamp)
			? (float)((render_stamp_us - interp_target_stamp) / 1000.0)
			: 0.0f;
	out.surface.materialized.view_x = -9999;
	out.surface.materialized.view_y = -9999;
	if (world)
	{
		out.surface.materialized = MaterializeRemoteActorPresentation(
			game,
			server,
			world,
			renderer,
			width,
			height,
				remote,
				out.tracked_pid,
				render_pos,
				render_dir,
				&out.resolved);
	}
	return out;
}
