#include <stdint.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "network_ingest.h"
#include "audio.h"
#include "game.h"
#include "snapshot_client/snapshot_entity_decoder.h"
#include "snapshot_client/snapshot_stream_applier.h"
#include "snapshot_client/local_authoritative_snapshot.h"
#include "snapshot_client/remote_authoritative_snapshot.h"
#include "snapshot_client/snapshot_npc_repository.h"
#include "remote_actor_roster.h"
#include "remote_observer_probe.h"
#include "remote_mounted_witness.h"
#include "platform/time_backend.h"
#include "server/multiplayer_protocol.h"
#include "server/mp_move.h"
// FL-4137 #21: #include "mp_diag_shadow_colliders.h" DELETED. TU removed.

struct SnapshotAudioActorState
{
	bool valid;
	uint8_t entity_type;
	uint16_t entity_id;
	uint8_t life_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	int16_t hp;
	float pos[3];
	uint32_t last_footstep_bucket;
};

static SnapshotAudioActorState g_snapshot_audio_actors[512];

static SnapshotAudioActorState* FindSnapshotAudioActorState(uint8_t entity_type, uint16_t entity_id)
{
	SnapshotAudioActorState* free_slot = 0;
	for (int i = 0; i < (int)(sizeof(g_snapshot_audio_actors) / sizeof(g_snapshot_audio_actors[0])); i++)
	{
		SnapshotAudioActorState* slot = &g_snapshot_audio_actors[i];
		if (slot->valid && slot->entity_type == entity_type && slot->entity_id == entity_id)
			return slot;
		if (!slot->valid && !free_slot)
			free_slot = slot;
	}
	if (free_slot)
		return free_slot;
	SnapshotAudioActorState* recycled =
		&g_snapshot_audio_actors[entity_id % (sizeof(g_snapshot_audio_actors) / sizeof(g_snapshot_audio_actors[0]))];
	memset(recycled, 0, sizeof(*recycled));
	return recycled;
}

static float SnapshotAudioDistanceFromLocal(const Game* game, const float pos[3])
{
	if (!game || !pos)
		return 0.0f;
	const float dx = pos[0] - game->player.pos[0];
	const float dy = pos[1] - game->player.pos[1];
	const float dz = pos[2] - game->player.pos[2];
	return sqrtf(dx * dx + dy * dy + dz * dz);
}

static int SnapshotAudioVolume(const Game* game, const STRUCT_SNAPSHOT_ENTITY* ent, bool entity_is_local)
{
	if (!ent)
		return 0;
	if (entity_is_local)
		return 65535;
	return AudioSpatialVolume(65535, SnapshotAudioDistanceFromLocal(game, ent->pos));
}

static bool SnapshotAudioAlive(uint8_t life_state, int16_t hp)
{
	return life_state == LIFE_STATE::ALIVE && hp > 0;
}

static void StoreSnapshotAudioState(SnapshotAudioActorState* state, const STRUCT_SNAPSHOT_ENTITY* ent)
{
	if (!state || !ent)
		return;
	state->valid = true;
	state->entity_type = ent->entity_type;
	state->entity_id = ent->entity_id;
	state->life_state = ent->life_state;
	state->locomotion_state = ent->locomotion_state;
	state->combat_state = ent->combat_state;
	state->hp = ent->hp;
	state->pos[0] = ent->pos[0];
	state->pos[1] = ent->pos[1];
	state->pos[2] = ent->pos[2];
}

static void ObserveSnapshotAudioTransition(Game* game, const STRUCT_SNAPSHOT_ENTITY* ent, bool entity_is_local)
{
	if (!ent)
		return;

	SnapshotAudioActorState* state = FindSnapshotAudioActorState(ent->entity_type, ent->entity_id);
	if (!state)
		return;
	if ((ent->state_flags & SNAPSHOT_STATE_REMOVE) != 0)
	{
		memset(state, 0, sizeof(*state));
		return;
	}
	if (!state->valid)
	{
		StoreSnapshotAudioState(state, ent);
		return;
	}

	const int volume = SnapshotAudioVolume(game, ent, entity_is_local);
	const bool was_alive = SnapshotAudioAlive(state->life_state, state->hp);
	const bool is_alive = SnapshotAudioAlive(ent->life_state, ent->hp);
	if (volume > 0)
	{
		if (was_alive && !is_alive)
			AudioDie(volume, 0);
		else if (is_alive && ent->hp < state->hp)
			AudioHurt(volume, 0);

		if (is_alive &&
			state->combat_state != COMBAT_STATE::ATTACKING &&
			ent->combat_state == COMBAT_STATE::ATTACKING)
			AudioAttack(volume, 0, 0);

		const float dx = ent->pos[0] - state->pos[0];
		const float dy = ent->pos[1] - state->pos[1];
		const float moved_xy2 = dx * dx + dy * dy;
		const uint32_t footstep_bucket = ent->last_authoritative_tick / 8u;
		if (is_alive &&
			ent->locomotion_state == LOCOMOTION_STATE::MOVING &&
			moved_xy2 > 0.36f &&
			footstep_bucket != state->last_footstep_bucket)
		{
			AudioWalk(0, volume, 0, 0);
			state->last_footstep_bucket = footstep_bucket;
		}
	}

	StoreSnapshotAudioState(state, ent);
}

static int ResolveTrackedRemoteIdForSnapshotDebug(const Game* game, const Server* server)
{
	if (!game || !server)
		return -1;
	if (game->debug.dbg_remote0_pid >= 0 && game->debug.dbg_remote0_pid < server->connection.max_clients)
		return game->debug.dbg_remote0_pid;
	if (game->debug.dbg_last_remote0_pid >= 0 && game->debug.dbg_last_remote0_pid < server->connection.max_clients)
		return game->debug.dbg_last_remote0_pid;
	if (server->connection.local_id >= 0 && server->connection.max_clients == 2)
		return server->connection.local_id == 0 ? 1 : 0;
	return -1;
}

static bool SnapshotSeqNewer(uint16_t seq, uint16_t last_seq)
{
	return seq != last_seq && (uint16_t)(seq - last_seq) < 0x8000u;
}

static bool SnapshotShouldRejectStalePacket(
	SnapshotClientState* snapshot_client,
	bool snapshot_is_delta,
	uint16_t seq,
	uint32_t tick)
{
	if (!snapshot_client)
		return false;
	const bool have_last =
		snapshot_client->last_snapshot_seq != 0u ||
		snapshot_client->last_snapshot_tick != 0u;
	if (!have_last)
		return false;
	const bool seq_newer = SnapshotSeqNewer(seq, snapshot_client->last_snapshot_seq);
	const bool tick_newer = tick > snapshot_client->last_snapshot_tick;
	if (!seq_newer)
	{
		snapshot_client->snapshot_rejected_stale_seq_count++;
		snapshot_client->snapshot_last_rejected_seq = seq;
		snapshot_client->snapshot_last_rejected_tick = tick;
		return true;
	}
	if (!tick_newer)
	{
		snapshot_client->snapshot_rejected_stale_tick_count++;
		snapshot_client->snapshot_last_rejected_seq = seq;
		snapshot_client->snapshot_last_rejected_tick = tick;
		return true;
	}
	if (snapshot_is_delta)
	{
		const uint16_t expected = (uint16_t)(snapshot_client->last_snapshot_seq + 1u);
		if (seq != expected)
		{
			// The browser transport deliberately coalesces queued snapshot packets
			// latest-wins before WASM ingest. A newer delta can therefore skip
			// sequence numbers without being stale; count the gap, but do not let
			// the stale-drop guard freeze the authoritative snapshot stream.
			snapshot_client->snapshot_gap_count++;
			snapshot_client->snapshot_last_rejected_seq = seq;
			snapshot_client->snapshot_last_rejected_tick = tick;
		}
	}
	return false;
}

static void NoteTrackedRemotePoseReject(
	Game* game,
	uint16_t remote_id,
	uint8_t packet_kind)
{
	if (!game)
		return;
	game->debug.dbg_remote0_last_pose_reason = 4;
	game->debug.dbg_remote0_pose_source = 2;
	game->debug.dbg_remote0_last_pose_packet_kind = packet_kind;
	game->debug.dbg_remote0_last_pose_entity_id = (int)remote_id;
}

static void NoteTrackedRemoteRejectedSnapshotPose(
	Game* game,
	Server* server,
	uint16_t remote_id,
	bool /*snapshot_is_delta*/,
	uint16_t /*snapshot_seq*/,
	uint32_t /*snapshot_tick*/,
	uint32_t /*entity_tick*/)
{
	if (!game || !server)
		return;
	const int tracked_id = ResolveTrackedRemoteIdForSnapshotDebug(game, server);
	if ((int)remote_id != tracked_id)
		return;
	game->debug.dbg_snap_tracked_pose_rejected++;
	NoteTrackedRemotePoseReject(game, remote_id, 2);
}

bool ApplySnapshotPacket(Server* server, Game* game, Terrain* terrain_ctx, World* world_ctx,
	const uint8_t* ptr, int size)
{
	if (size < (int)sizeof(STRUCT_SNAPSHOT_BASELINE))
		return false;

	const STRUCT_SNAPSHOT_BASELINE* hdr = (const STRUCT_SNAPSHOT_BASELINE*)ptr;
	if (hdr->entity_size < sizeof(STRUCT_SNAPSHOT_ENTITY))
		return false;

	const int header_size = (int)sizeof(STRUCT_SNAPSHOT_BASELINE);
	const int payload_size = (int)hdr->entity_count * (int)hdr->entity_size;
	if (header_size + payload_size != size)
		return false;
	if (hdr->layout_version < 9)
		return false;
	bool snapshot_is_delta = (ptr[0] == 'q');
	uint64_t snapshot_wall_stamp_us = a3dGetTime();
	SnapshotClientState* snapshot_client = &server->authority.snapshot_client;
	if (SnapshotShouldRejectStalePacket(snapshot_client, snapshot_is_delta, hdr->seq, hdr->tick))
		return true;

	// ACK every accepted snapshot so server can safely enter true delta mode.
	STRUCT_REQ_SNAPSHOT_ACK ack = {};
	ack.token = 'A';
	ack.seq = hdr->seq;
	server->Send((const uint8_t*)&ack, (int)sizeof(ack));
	snapshot_client->snapshot_ack_packets++;
	snapshot_client->last_snapshot_ack_seq = hdr->seq;
	snapshot_client->last_snapshot_ack_tick = hdr->tick;

	snapshot_client->snapshot_packets++;
	snapshot_client->snapshot_last_entity_count = hdr->entity_count;
	snapshot_client->snapshot_last_is_delta = snapshot_is_delta ? 1 : 0;
	snapshot_client->snapshot_last_local_applied = 0;
	snapshot_client->snapshot_last_local_apply_reason = 0;
	snapshot_client->snapshot_last_local_present = 0;
	snapshot_client->snapshot_last_local_pose_sane = 0;
	snapshot_client->snapshot_last_local_support_valid = 0;
	snapshot_client->snapshot_last_local_support_source = 0;
	snapshot_client->snapshot_last_local_support_item_id = 0;
	snapshot_client->snapshot_last_local_support_z = 0.0f;
	if (ptr[0] == 'b' || ptr[0] == 'q')
		server->authority.auth_item.snapshot_stream_active = true;
	snapshot_client->last_snapshot_seq = hdr->seq;
	snapshot_client->last_snapshot_tick = hdr->tick;
	snapshot_client->last_snapshot_wall_stamp_us = snapshot_wall_stamp_us;

	SnapshotNpcRepositoryContext npc_ctx = {};
	npc_ctx.snapshot_client = snapshot_client;
	npc_ctx.npc_repo = &server->authority.npc_repo;
	npc_ctx.max_clients = server->connection.max_clients;

	SnapshotNpcPacketUpdateSession snapshot_npc_update = {};
	BeginSnapshotNpcPacketUpdateSession(
		&npc_ctx, snapshot_is_delta, server->connection.stamp, &snapshot_npc_update);
	uint8_t* seen_players = 0;
	if (!snapshot_is_delta && server->connection.max_clients > 0)
		seen_players = (uint8_t*)calloc((size_t)server->connection.max_clients, 1);
	const uint8_t* scan = ptr + header_size;
	for (int i = 0; i < (int)hdr->entity_count; i++, scan += hdr->entity_size)
	{
		SnapshotEntityDecoded ent = {};
		if (!ParseAuthoritativeSnapshotEntity(scan, (int)hdr->entity_size, &ent))
			continue;
		int tracked_id = ResolveTrackedRemoteIdForSnapshotDebug(game, server);
		bool tracked_snapshot_entity = (tracked_id >= 0 && ent.entity_id == (uint16_t)tracked_id);
		if (tracked_snapshot_entity && game)
			game->debug.dbg_snap_tracked_seen++;
		bool entity_is_player = (ent.entity_id < (uint16_t)server->connection.max_clients);
		STRUCT_SNAPSHOT_ENTITY ent_norm = {};
		if (entity_is_player)
		{
			CopyNormalizedPlayerSnapshotEntity(&ent, &ent_norm);
			bool local_id_in_range = (server->connection.local_id >= 0 &&
				server->connection.local_id < server->connection.max_clients);
			bool entity_is_local = ent_norm.entity_id == (uint16_t)server->connection.local_id;
			if (local_id_in_range && entity_is_local)
			{
				// Local player snapshot — reconcile movement then apply through snapshot_client seam.
				LocalPlayerState* lp = &game->player;
				MpMoveApplyResult move_result = {};
				// FL-4137 #25: Gap D client-side placed-block shadow collider
				// build DELETED. Placed-block collision is server-owned world
				// entity data; client replay must not recreate a local collider.
				bool mp_ok = MpMoveApplySnapshot(
					&lp->mp_move,
					game->physics,
					&ent_norm,
					server->connection.stamp,
					game->session.fly_mode,
					terrain_ctx,
					world_ctx,
					(float)game->session.water,
					&move_result);
				(void)mp_ok;

				LocalAuthoritativeSnapshotApplyInput input = {};
				input.local_player = lp;
				input.physics = game->physics;
				input.terrain = terrain_ctx;
				input.world = world_ctx;
				input.fly_mode = game->session.fly_mode;
				input.water_z = (float)game->session.water;
				input.move_result = &move_result;
				input.prior_dbg_self_hp = game->debug.dbg_self_hp;
				input.prior_dbg_self_max_hp = game->debug.dbg_self_max_hp;

				LocalAuthoritativeSnapshotApplyResult result = {};
				if (ApplyLocalAuthoritativeSnapshot(
					&input,
					server->connection.local_id,
					server->connection.max_clients,
					server->authority.snapshot_client.last_snapshot_tick,
					&ent_norm,
					server->connection.stamp,
					&result))
				{
					// Commit observation to snapshot_client state
					server->authority.snapshot_client.snapshot_last_local_applied = result.applied ? 1 : 0;
					server->authority.snapshot_client.snapshot_last_local_apply_reason = result.reason;
					server->authority.snapshot_client.snapshot_last_local_present = 1;
					server->authority.snapshot_client.snapshot_last_local_pose_sane =
						SnapshotPoseSane(ent_norm.pos, ent_norm.dir) ? 1 : 0;
					server->authority.snapshot_client.snapshot_last_local_entity_id = ent_norm.entity_id;
					server->authority.snapshot_client.snapshot_last_local_pos[0] = ent_norm.pos[0];
					server->authority.snapshot_client.snapshot_last_local_pos[1] = ent_norm.pos[1];
					server->authority.snapshot_client.snapshot_last_local_pos[2] = ent_norm.pos[2];
					server->authority.snapshot_client.snapshot_last_local_support_valid = ent_norm.support_valid;
					server->authority.snapshot_client.snapshot_last_local_support_source = ent_norm.support_source;
					server->authority.snapshot_client.snapshot_last_local_support_item_id = ent_norm.support_item_id;
					server->authority.snapshot_client.snapshot_last_local_support_z = ent_norm.support_z;

					// Commit debug surface
					if (result.commit_debug_surface)
					{
						LocalAuthoritativeSnapshotApplyDebugSurface* ds = &result.debug_surface;
						lp->authoritative_snapshot_valid = ds->local_authoritative_snapshot_valid;
						game->debug.dbg_reconcile_applied = ds->dbg_reconcile_applied ? 1 : 0;
						game->debug.dbg_reconcile_hard_snap = ds->dbg_reconcile_hard_snap ? 1 : 0;
						game->debug.dbg_reconcile_zeroed_xy = ds->dbg_reconcile_zeroed_xy ? 1 : 0;
						game->debug.dbg_reconcile_dx = ds->dbg_reconcile_dx;
						game->debug.dbg_reconcile_dy = ds->dbg_reconcile_dy;
						game->debug.dbg_reconcile_dz = ds->dbg_reconcile_dz;
						game->debug.dbg_reconcile_tick = ds->dbg_reconcile_tick;
						game->debug.dbg_reconcile_auth_dist_pre = ds->dbg_reconcile_auth_dist_pre;
						game->debug.dbg_reconcile_auth_dist_post = ds->dbg_reconcile_auth_dist_post;
						game->debug.dbg_self_hp = ds->dbg_self_hp;
						game->debug.dbg_self_max_hp = ds->dbg_self_max_hp;
					}
					if (result.commit_pose)
					{
						lp->pos[0] = result.pos[0];
						lp->pos[1] = result.pos[1];
						lp->pos[2] = result.pos[2];
						lp->dir = result.dir;
					}
					if (result.commit_semantic_state)
					{
						lp->life_state = result.life_state;
						lp->mount_state = result.mount_state;
						lp->locomotion_state = result.locomotion_state;
						lp->combat_state = result.combat_state;
						lp->presentation_kind_id = result.presentation_kind_id;
						lp->presentation_selector_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
						lp->presentation_started_tick = result.presentation_started_tick;
					}
					if (result.accept_presentation_sample)
					{
						LocalSnapshotPresentationTrack* track = &game->player.snapshot_presentation_track;
						if (result.presentation_sample_stamp > track->prev_yaw_resync_stamp)
						{
							// FL-1733: suppress yaw resync while Q/E torque is active.
							// At 180°/s turn rate + any real RTT, server yaw always lags
							// enough to exceed the threshold, causing per-snapshot camera
							// snaps (visible jitter). Suppress during active input; allow
							// resync once torque stops (grace period lets server catch up).
							// Does NOT affect remote presentation — remotes read server
							// authoritative yaw from snapshots, not this client's prev_yaw.
							// LINEAGE_JSON: {"fl":"FL-1733","cautionary_precedent":"yaw_resync_not_lag_fix","note":"DO NOT reinvest in yaw resync suppression as a lag/movement fix. This fixed camera Q/E jitter but did nothing for dead movement (movement_intent_sample_count=0) or server lag (tab lag 3619ms/6107ms in manual-20260505-015510). Separate owners.","run":"manual-20260505-015510"}
							const uint64_t torque_grace_us = 200000; // 200ms after last Q/E
							const bool torque_recently_active =
								game->player.last_torque_active_stamp > 0 &&
								snapshot_wall_stamp_us > game->player.last_torque_active_stamp &&
								(snapshot_wall_stamp_us - game->player.last_torque_active_stamp) < torque_grace_us;

							if (!torque_recently_active)
							{
								const float prev_yaw_resync_snap_deg = 5.0f;
								float server_yaw = NormalizeSnapshotDir(result.presentation_sample_server_yaw);
								float prev_yaw = NormalizeSnapshotDir(game->player.prev_yaw);
								float yaw_divergence = SnapshotAngleDeltaDeg(prev_yaw, server_yaw);
								if (fabsf(yaw_divergence) > prev_yaw_resync_snap_deg)
									game->player.prev_yaw = server_yaw;
							}
							track->prev_yaw_resync_stamp = result.presentation_sample_stamp;
						}
					}
					ObserveSnapshotAudioTransition(game, &ent_norm, true);
					continue;
				}
			}
		}
		const bool ent_pose_sane = SnapshotPoseSane(ent.pos, ent.dir);
		if (!ent_pose_sane)
		{
			NoteTrackedRemoteRejectedSnapshotPose(
				game,
				server,
				ent.entity_id,
				snapshot_is_delta,
				hdr->seq,
				hdr->tick,
				ent.last_authoritative_tick);
			continue;
		}
		bool entity_is_npc = !entity_is_player &&
			(ent.entity_type == SNAPSHOT_ENTITY_NPC || ent.entity_type == SNAPSHOT_ENTITY_PLAYER);
		if (entity_is_npc)
		{
			server->authority.snapshot_client.snapshot_npc_entities_total++;
			STRUCT_SNAPSHOT_ENTITY ent_npc = {};
			ent_npc.entity_id = ent.entity_id;
			ent_npc.entity_type = ent.entity_type;
			ent_npc.life_state = ent.life_state;
			ent_npc.mount_state = ent.mount_state;
			ent_npc.locomotion_state = ent.locomotion_state;
			ent_npc.combat_state = ent.combat_state;
			ent_npc.presentation_kind_id = ent.presentation_kind_id;
			ent_npc.state_flags = (uint8_t)(ent.state_flags & 0xFFu);
			ent_npc.pos[0] = ent.pos[0];
			ent_npc.pos[1] = ent.pos[1];
			ent_npc.pos[2] = ent.pos[2];
			ent_npc.dir = ent.dir;
			ent_npc.hp = ent.hp;
			ent_npc.max_hp = ent.max_hp;
			ent_npc.last_authoritative_tick = ent.last_authoritative_tick;
			ent_npc.presentation_started_tick = ent.presentation_started_tick;
			int npc_damage_amount = 0;
			ApplySnapshotNpcPacketEntity(
				&npc_ctx, &snapshot_npc_update, &ent_npc, &npc_damage_amount);
			ObserveSnapshotAudioTransition(game, &ent_npc, false);
			if (npc_damage_amount > 0)
			{
				SpawnDamageFloater(
					ent.pos[0], ent.pos[1], ent.pos[2],
					npc_damage_amount, server->connection.stamp);
			}
			continue;
		}
		if (!entity_is_player)
			continue;
		bool remove_entity = (ent_norm.state_flags & SNAPSHOT_STATE_REMOVE) != 0;
		if (remove_entity)
		{
			if (tracked_snapshot_entity && game)
			{
				game->debug.dbg_snap_tracked_remove_seen++;
				game->debug.dbg_snap_tracked_last_remove_flags = ent_norm.state_flags;
				game->debug.dbg_snap_tracked_last_remove_seq = hdr->seq;
				game->debug.dbg_snap_tracked_last_remove_tick = hdr->tick;
			}
			if (!(server->connection.local_id >= 0 && ent_norm.entity_id == (uint16_t)server->connection.local_id))
				RemoveRemoteActorRosterSlotById(
					game, server, ent_norm.entity_id, 34, false);
			continue;
		}
		if (!snapshot_is_delta &&
			seen_players &&
			ent_norm.entity_id < (uint16_t)server->connection.max_clients)
			seen_players[ent_norm.entity_id] = 1;

		if (tracked_snapshot_entity && game)
		{
			game->debug.dbg_snap_tracked_applied++;
			game->debug.dbg_remote0_snapshot_life_state = (int)ent_norm.life_state;
			game->debug.dbg_remote0_snapshot_mount_state = (int)ent_norm.mount_state;
			game->debug.dbg_remote0_snapshot_locomotion_state = (int)ent_norm.locomotion_state;
			// FL-3254: publish combat_state so mounted attack/death gate can observe it
			game->debug.dbg_remote0_snapshot_combat_state = (int)ent_norm.combat_state;
			// FL-2957: publish terrain_z from snapshot for floor coherence proof
			game->debug.dbg_remote0_snapshot_terrain_z = ent_norm.terrain_z;
			game->debug.dbg_remote0_snapshot_presentation_kind_id = (int)ent_norm.presentation_kind_id;
			game->debug.dbg_remote0_snapshot_tick = hdr->tick;
			game->debug.dbg_remote0_entity_tick = ent_norm.last_authoritative_tick;
		}
		Human* h = server->authority.others + ent_norm.entity_id;
		bool pre_snapshot_death_epoch = RemoteObserverHasDeathEpoch(h);
		RemoteAuthoritativeSnapshotApplyResult remote_result = {};
		ApplyRemoteAuthoritativeSnapshot(
			h,
			server,
			world_ctx,
			ent_norm.entity_id,
			&ent_norm,
			server->authority.snapshot_client.last_snapshot_tick,
			snapshot_wall_stamp_us,
			&remote_result);
		if (remote_result.applied)
			ObserveSnapshotAudioTransition(game, &ent_norm, false);
		if (ent_norm.state_flags & SNAPSHOT_STATE_ALIVE)
		{
			if (pre_snapshot_death_epoch)
				RemoteObserverNoteRespawnSeq(server, h, (int)ent_norm.entity_id);
			RemoteMountedWitnessResetObserverDeathHistory(h);
		}
		else
		{
			if (!pre_snapshot_death_epoch)
			{
				RemoteMountedWitnessResetObserverDeathHistory(h);
				RemoteObserverNoteDeathSeq(server, h, (int)ent_norm.entity_id, 2);
			}
			RemoteMountedWitnessNoteObserverDeathSnapshot(h,
				ent_norm.life_state,
				ent_norm.mount_state,
				ent_norm.locomotion_state,
				ent_norm.presentation_kind_id,
				hdr->tick);
		}
	}
	if (!snapshot_is_delta && seen_players)
	{
		for (int pid = 0; pid < server->connection.max_clients; pid++)
		{
			if (pid == server->connection.local_id) continue;
			if (seen_players[pid]) continue;
			RemoveRemoteActorRosterSlotById(
				game, server, (uint16_t)pid, 34, false);
		}
	}
	if (seen_players)
		free(seen_players);
	FinishSnapshotNpcPacketUpdateSession(
		&npc_ctx, &snapshot_npc_update, hdr->tick);

	return true;
}
