#include "snapshot_client/snapshot_npc_repository.h"

#include <string.h>

#include "actor_visual_profile.h"
#include "protocol/protocol_snapshot.h"

struct SnapshotNpcPacketApplyResult
{
	bool handled_entity;
	bool removed_entity;
	uint8_t _pad[2];
	int damage_amount;
};

static ServerSnapshotNpcRepository::SnapshotNpcState* FindSnapshotNpcStateByEntityIdWithinCount(
	ServerSnapshotNpcRepository* repo,
	uint16_t entity_id,
	int count)
{
	if (!repo)
		return 0;
	if (count < 0)
		count = 0;
	if (count > (int)repo->npc_count)
		count = (int)repo->npc_count;
	for (int i = 0; i < count; i++)
	{
		ServerSnapshotNpcRepository::SnapshotNpcState* sn = &repo->npcs[i];
		if (sn->entity_id == entity_id)
			return sn;
	}
	return 0;
}

static const ServerSnapshotNpcRepository::SnapshotNpcState* FindPreviousSnapshotNpcStateByEntityId(
	const SnapshotNpcPacketRepository* repo,
	uint16_t entity_id)
{
	if (!repo || repo->snapshot_is_delta)
		return 0;
	for (int i = 0; i < repo->prev_snapshot_npc_count &&
		i < ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS; i++)
	{
		const ServerSnapshotNpcRepository::SnapshotNpcState* sn = &repo->prev_snapshot_npcs[i];
		if (sn->entity_id == entity_id)
			return sn;
	}
	return 0;
}

static AppearanceStateV2* FindCachedSnapshotNpcAppearanceByEntityId(
	ServerSnapshotNpcRepository* repo,
	int max_clients,
	uint16_t entity_id)
{
	if (!repo)
		return 0;
	if (entity_id < (uint16_t)max_clients)
		return 0;
	const int cache_index = (int)entity_id - max_clients;
	if (cache_index < 0 || cache_index >= ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
		return 0;
	ServerSnapshotNpcRepository::SnapshotNpcAppearanceCache* cached =
		&repo->appearance_cache[cache_index];
	if (cached->entity_id != entity_id)
		return 0;
	return &cached->appearance_v2;
}

static const AppearanceStateV2* FindCachedSnapshotNpcAppearanceByEntityId(
	const ServerSnapshotNpcRepository* repo,
	int max_clients,
	uint16_t entity_id)
{
	return FindCachedSnapshotNpcAppearanceByEntityId((ServerSnapshotNpcRepository*)repo, max_clients, entity_id);
}

static void StoreCachedSnapshotNpcAppearanceByEntityId(
	ServerSnapshotNpcRepository* repo,
	int max_clients,
	uint16_t entity_id,
	const AppearanceStateV2* appearance)
{
	if (!repo || !appearance)
		return;
	if (entity_id < (uint16_t)max_clients)
		return;
	const int cache_index = (int)entity_id - max_clients;
	if (cache_index < 0 || cache_index >= ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
		return;
	ServerSnapshotNpcRepository::SnapshotNpcAppearanceCache* cached =
		&repo->appearance_cache[cache_index];
	cached->entity_id = entity_id;
	cached->appearance_v2 = *appearance;
}

static void BeginSnapshotNpcPacketRepository(
	const SnapshotNpcRepositoryContext* ctx,
	bool snapshot_is_delta,
	SnapshotNpcPacketRepository* repo)
{
	if (!repo)
		return;
	memset(repo, 0, sizeof(*repo));
	repo->snapshot_is_delta = snapshot_is_delta;
	if (!ctx || !ctx->npc_repo)
		return;
	const int prev_snapshot_npc_count = (int)ctx->npc_repo->npc_count;
	repo->parsed_npc_count = snapshot_is_delta ? prev_snapshot_npc_count : 0;
	if (!snapshot_is_delta && prev_snapshot_npc_count > 0)
	{
		int copy_count = prev_snapshot_npc_count;
		if (copy_count > ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
			copy_count = ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS;
		memcpy(repo->prev_snapshot_npcs, ctx->npc_repo->npcs,
			sizeof(ServerSnapshotNpcRepository::SnapshotNpcState) * copy_count);
		repo->prev_snapshot_npc_count = copy_count;
	}
}

static bool ApplySnapshotNpcEntityToRepository(
	const SnapshotNpcRepositoryContext* ctx,
	SnapshotNpcPacketRepository* repo,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	SnapshotNpcPacketApplyResult* out)
{
	if (out)
		memset(out, 0, sizeof(*out));
	if (!ctx || !ctx->npc_repo || !repo || !ent)
		return false;

	if (out)
		out->handled_entity = true;

	const bool remove_entity = (ent->state_flags & SNAPSHOT_STATE_REMOVE) != 0;
	ServerSnapshotNpcRepository::SnapshotNpcState* existing =
		FindSnapshotNpcStateByEntityIdWithinCount(ctx->npc_repo, ent->entity_id, repo->parsed_npc_count);
	if (remove_entity)
	{
		if (repo->snapshot_is_delta && existing && repo->parsed_npc_count > 0)
		{
			int existing_index = (int)(existing - ctx->npc_repo->npcs);
			ctx->npc_repo->npcs[existing_index] =
				ctx->npc_repo->npcs[repo->parsed_npc_count - 1];
			repo->parsed_npc_count--;
		}
		if (out)
			out->removed_entity = true;
		return true;
	}

	ServerSnapshotNpcRepository::SnapshotNpcState* sn = 0;
	if (repo->snapshot_is_delta)
	{
		if (existing)
		{
			if (out && existing->hp > ent->hp)
			{
				int dmg = existing->hp - ent->hp;
				if (dmg > 255) dmg = 255;
				if (dmg < 1) dmg = 1;
				out->damage_amount = dmg;
			}
			sn = existing;
		}
		else if (repo->parsed_npc_count < ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
		{
			sn = &ctx->npc_repo->npcs[repo->parsed_npc_count++];
		}
	}
	else if (repo->parsed_npc_count < ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
	{
		sn = &ctx->npc_repo->npcs[repo->parsed_npc_count++];
	}

	if (!sn)
		return true;

	AppearanceStateV2 preserved_appearance_v2 = {};
	const ServerSnapshotNpcRepository::SnapshotNpcState* preserved_npc = repo->snapshot_is_delta
		? existing
		: FindPreviousSnapshotNpcStateByEntityId(repo, ent->entity_id);
	if (preserved_npc && preserved_npc->appearance_v2.valid)
		preserved_appearance_v2 = preserved_npc->appearance_v2;
	else
	{
		const AppearanceStateV2* cached_appearance =
			FindCachedSnapshotNpcAppearanceByEntityId(ctx->npc_repo, ctx->max_clients, ent->entity_id);
		if (cached_appearance && cached_appearance->valid)
			preserved_appearance_v2 = *cached_appearance;
	}

	sn->entity_id = ent->entity_id;
	sn->life_state = ent->life_state;
	sn->mount_state = ent->mount_state;
	sn->locomotion_state = ent->locomotion_state;
	sn->combat_state = ent->combat_state;
	sn->presentation_kind_id = ent->presentation_kind_id;
	sn->pos[0] = ent->pos[0];
	sn->pos[1] = ent->pos[1];
	sn->pos[2] = ent->pos[2];
	sn->dir = ent->dir;
	sn->hp = ent->hp;
	sn->max_hp = ent->max_hp;
	sn->state_flags = (uint8_t)(ent->state_flags & 0xFFu);
	sn->last_authoritative_tick = ent->last_authoritative_tick;
	sn->presentation_started_tick = ent->presentation_started_tick;
	sn->appearance_v2 = preserved_appearance_v2;
	if (sn->appearance_v2.valid)
		StoreCachedSnapshotNpcAppearanceByEntityId(
			ctx->npc_repo, ctx->max_clients, sn->entity_id, &sn->appearance_v2);
	return true;
}

static void FinishSnapshotNpcPacketRepository(
	const SnapshotNpcRepositoryContext* ctx,
	const SnapshotNpcPacketRepository* repo,
	uint32_t packet_tick,
	uint32_t packet_npc_entities)
{
	if (!ctx || !repo || !ctx->npc_repo)
		return;
	if (ctx->snapshot_client)
		ctx->snapshot_client->snapshot_npc_entities_last = packet_npc_entities;
	ctx->npc_repo->npc_count = (uint16_t)repo->parsed_npc_count;
	ctx->npc_repo->npc_tick = packet_tick;
}

void BeginSnapshotNpcPacketUpdateSession(
	const SnapshotNpcRepositoryContext* ctx,
	bool snapshot_is_delta,
	uint64_t stamp_us,
	SnapshotNpcPacketUpdateSession* session)
{
	if (!session)
		return;
	memset(session, 0, sizeof(*session));
	session->stamp_us = stamp_us;
	BeginSnapshotNpcPacketRepository(ctx, snapshot_is_delta, &session->repo);
}

void ApplySnapshotNpcPacketEntity(
	const SnapshotNpcRepositoryContext* ctx,
	SnapshotNpcPacketUpdateSession* session,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	int* out_damage_amount)
{
	if (out_damage_amount)
		*out_damage_amount = 0;
	if (!ctx || !session || !ent)
		return;
	SnapshotNpcPacketApplyResult out = {};
	if (!ApplySnapshotNpcEntityToRepository(ctx, &session->repo, ent, &out))
		return;
	session->packet_npc_entities++;
	if (out_damage_amount)
		*out_damage_amount = out.damage_amount;
}

void FinishSnapshotNpcPacketUpdateSession(
	const SnapshotNpcRepositoryContext* ctx,
	const SnapshotNpcPacketUpdateSession* session,
	uint32_t packet_tick)
{
	if (!ctx || !session)
		return;
	FinishSnapshotNpcPacketRepository(
		ctx,
		&session->repo,
		packet_tick,
		session->packet_npc_entities);
}

ServerSnapshotNpcRepository::SnapshotNpcState* FindSnapshotNpcStateByEntityId(
	ServerSnapshotNpcRepository* repo,
	uint16_t entity_id)
{
	if (!repo)
		return 0;
	for (int i = 0; i < (int)repo->npc_count; i++)
	{
		ServerSnapshotNpcRepository::SnapshotNpcState* sn = &repo->npcs[i];
		if (sn->entity_id == entity_id)
			return sn;
	}
	return 0;
}

const ServerSnapshotNpcRepository::SnapshotNpcState* FindSnapshotNpcStateByEntityId(
	const ServerSnapshotNpcRepository* repo,
	uint16_t entity_id)
{
	return FindSnapshotNpcStateByEntityId((ServerSnapshotNpcRepository*)repo, entity_id);
}

void ApplySnapshotNpcAppearanceState(
	const SnapshotNpcRepositoryContext* ctx,
	uint16_t entity_id,
	const AppearanceStateV2* appearance)
{
	if (!ctx || !ctx->npc_repo || !appearance)
		return;
	StoreCachedSnapshotNpcAppearanceByEntityId(
		ctx->npc_repo, ctx->max_clients, entity_id, appearance);
	int applied = 0;
	for (int i = 0; i < (int)ctx->npc_repo->npc_count; i++)
	{
		ServerSnapshotNpcRepository::SnapshotNpcState* sn = &ctx->npc_repo->npcs[i];
		if (sn->entity_id != entity_id)
			continue;
		sn->appearance_v2 = *appearance;
		applied++;
	}
	if (applied == 0 && ctx->npc_repo->npc_count < ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
	{
		ServerSnapshotNpcRepository::SnapshotNpcState* sn =
			&ctx->npc_repo->npcs[ctx->npc_repo->npc_count++];
		memset(sn, 0, sizeof(*sn));
		sn->entity_id = entity_id;
		sn->appearance_v2 = *appearance;
	}
}
