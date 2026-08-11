#pragma once

#include <cstdint>

#include "server/mp_move.h"

struct Human;
struct STRUCT_SNAPSHOT_ENTITY;

struct RemoteActorPresentationTrack
{
	SnapshotPoseEntry interp_ring[MP_INTERP_RING_CAP];
	uint32_t interp_ring_write_idx;
	uint8_t pending_inst_invalidation_reason;
	uint8_t pending_inst_invalidation_clear_aliases;
	uint8_t last_render_pose_valid;
	uint8_t _pad0;
	float last_render_pos[3];
	float last_render_dir;
};

uint64_t GetRemoteActorInterpolationDelayUs();
int GetRemoteActorInterpolationDepth(const RemoteActorPresentationTrack* state);
const SnapshotPoseEntry* GetRemoteActorInterpolationNewestEntry(
	const RemoteActorPresentationTrack* state,
	int newest_offset);
bool SampleRemoteActorInterpolation(
	const RemoteActorPresentationTrack* state,
	uint64_t render_stamp_us,
	uint64_t interp_delay_us,
	float out_pos[3],
	float* out_dir,
	float* out_lerp_t,
	int* out_depth,
	int* out_mode);
void AcceptRemoteActorPresentationSnapshot(
	Human* remote,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	const float pos[3],
	float dir,
	uint32_t snapshot_tick,
	uint64_t arrival_wall_stamp_us);
