#pragma once

#include <stdint.h>

#include "server/snapshot_client_state.h"
#include "server/snapshot_npc_repository.h"
#include "actor_visual_profile.h"

struct STRUCT_SNAPSHOT_ENTITY;

struct SnapshotNpcRepositoryContext
{
	SnapshotClientState* snapshot_client;
	ServerSnapshotNpcRepository* npc_repo;
	int max_clients;
};

struct SnapshotNpcPacketRepository
{
	bool snapshot_is_delta;
	int prev_snapshot_npc_count;
	int parsed_npc_count;
	ServerSnapshotNpcRepository::SnapshotNpcState prev_snapshot_npcs[ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS];
};

struct SnapshotNpcPacketUpdateSession
{
	SnapshotNpcPacketRepository repo;
	uint32_t packet_npc_entities;
	uint64_t stamp_us;
};

void BeginSnapshotNpcPacketUpdateSession(
	const SnapshotNpcRepositoryContext* ctx,
	bool snapshot_is_delta,
	uint64_t stamp_us,
	SnapshotNpcPacketUpdateSession* session);

void ApplySnapshotNpcPacketEntity(
	const SnapshotNpcRepositoryContext* ctx,
	SnapshotNpcPacketUpdateSession* session,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	int* out_damage_amount);

void FinishSnapshotNpcPacketUpdateSession(
	const SnapshotNpcRepositoryContext* ctx,
	const SnapshotNpcPacketUpdateSession* session,
	uint32_t packet_tick);

ServerSnapshotNpcRepository::SnapshotNpcState* FindSnapshotNpcStateByEntityId(
	ServerSnapshotNpcRepository* repo,
	uint16_t entity_id);

const ServerSnapshotNpcRepository::SnapshotNpcState* FindSnapshotNpcStateByEntityId(
	const ServerSnapshotNpcRepository* repo,
	uint16_t entity_id);

void ApplySnapshotNpcAppearanceState(
	const SnapshotNpcRepositoryContext* ctx,
	uint16_t entity_id,
	const AppearanceStateV2* appearance);
