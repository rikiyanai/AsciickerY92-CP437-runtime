#pragma once

#include <stdint.h>

struct Human;
struct Server;
struct World;
struct STRUCT_SNAPSHOT_ENTITY;

struct RemoteAuthoritativeSnapshotApplyResult
{
	bool applied;
	bool rejected_pose;
	bool rejected_stale_origin;
	bool bootstrapped_roster;
	bool accepted_presentation;
	bool queued_inst_invalidation;
};

bool ApplyRemoteAuthoritativeSnapshot(
	Human* remote,
	Server* server,
	World* world,
	uint16_t remote_id,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	uint32_t last_snapshot_tick,
	uint64_t arrival_wall_stamp_us,
	RemoteAuthoritativeSnapshotApplyResult* out);
