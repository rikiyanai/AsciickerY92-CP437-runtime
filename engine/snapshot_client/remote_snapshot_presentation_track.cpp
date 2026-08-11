#include "snapshot_client/remote_snapshot_presentation_track.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "game.h"
#include "physics_tick.h"

namespace
{
static float ReadEnvFloatOrDefault(const char* name, float fallback)
{
	const char* raw = getenv(name);
	if (!raw || !*raw)
		return fallback;
	char* end = 0;
	double value = strtod(raw, &end);
	if (!end || end == raw)
		return fallback;
	return (float)value;
}

static float NormalizeInterpDir(float dir)
{
	if (!isfinite(dir))
		return 0.0f;
	dir = fmodf(dir, 360.0f);
	if (dir > 180.0f)
		dir -= 360.0f;
	if (dir < -180.0f)
		dir += 360.0f;
	return dir;
}

static void ClearInterpRing(RemoteActorPresentationTrack* state)
{
	if (!state)
		return;
	memset(state->interp_ring, 0, sizeof(state->interp_ring));
	state->interp_ring_write_idx = 0;
}

static uint64_t SynthesizeInterpWallStampUs(
	const SnapshotPoseEntry* prev,
	uint32_t tick,
	uint64_t arrival_wall_stamp_us)
{
	if (!prev)
		return arrival_wall_stamp_us;
	if (tick == 0u || prev->tick == 0u || tick <= prev->tick)
		return arrival_wall_stamp_us;
	const uint32_t tick_delta = tick - prev->tick;
	if (tick_delta > MP_INTERP_RING_CAP)
		return arrival_wall_stamp_us;
	const uint64_t expected_delta_us = (uint64_t)tick_delta * PHYSICS_TICK_INTERVAL_US;
	if (expected_delta_us == 0u)
		return arrival_wall_stamp_us;
	return prev->wall_stamp_us + expected_delta_us;
}

static void PushInterpRingSnapshot(
	RemoteActorPresentationTrack* state,
	const float pos[3],
	float dir,
	uint32_t tick,
	uint64_t wall_stamp_us)
{
	if (!state || !pos)
		return;
	if (!isfinite(pos[0]) || !isfinite(pos[1]) || !isfinite(pos[2]) || !isfinite(dir))
		return;
	const SnapshotPoseEntry* prev = GetRemoteActorInterpolationNewestEntry(state, 0);
	uint64_t monotonic_stamp = SynthesizeInterpWallStampUs(prev, tick, wall_stamp_us);
	if (monotonic_stamp == 0u)
		monotonic_stamp = wall_stamp_us ? wall_stamp_us : 1u;
	if (prev && monotonic_stamp <= prev->wall_stamp_us)
		monotonic_stamp = prev->wall_stamp_us + 1u;
	SnapshotPoseEntry* slot = &state->interp_ring[state->interp_ring_write_idx % MP_INTERP_RING_CAP];
	slot->pos[0] = pos[0];
	slot->pos[1] = pos[1];
	slot->pos[2] = pos[2];
	slot->dir = NormalizeInterpDir(dir);
	slot->tick = tick;
	slot->_pad = 0u;
	slot->wall_stamp_us = monotonic_stamp;
	state->interp_ring_write_idx++;
}

static bool ShouldFlushInterpRing(
	const RemoteActorPresentationTrack* state,
	uint8_t prev_life_state,
	uint8_t next_life_state,
	const float next_pos[3])
{
	if (!state || !next_pos)
		return false;
	if (prev_life_state == LIFE_STATE::DEAD && next_life_state == LIFE_STATE::ALIVE)
		return true;
	const SnapshotPoseEntry* prev = GetRemoteActorInterpolationNewestEntry(state, 0);
	if (!prev)
		return false;
	const float dx = next_pos[0] - prev->pos[0];
	const float dy = next_pos[1] - prev->pos[1];
	const float dz = next_pos[2] - prev->pos[2];
	const float dist2 = dx * dx + dy * dy + dz * dz;
	return dist2 >= (50.0f * 50.0f);
}
}

uint64_t GetRemoteActorInterpolationDelayUs()
{
	static bool initialized = false;
	static uint64_t cached = 120000;
	if (!initialized)
	{
		float delay_ms = ReadEnvFloatOrDefault("INTERP_DELAY_MS", 120.0f);
		if (!isfinite(delay_ms) || delay_ms < 0.0f)
			delay_ms = 120.0f;
		cached = (uint64_t)llround((double)delay_ms * 1000.0);
		initialized = true;
	}
	return cached;
}

int GetRemoteActorInterpolationDepth(const RemoteActorPresentationTrack* state)
{
	if (!state)
		return 0;
	const int written = (int)state->interp_ring_write_idx;
	return written < MP_INTERP_RING_CAP ? written : MP_INTERP_RING_CAP;
}

const SnapshotPoseEntry* GetRemoteActorInterpolationNewestEntry(
	const RemoteActorPresentationTrack* state,
	int newest_offset)
{
	const int depth = GetRemoteActorInterpolationDepth(state);
	if (!state || depth <= 0 || newest_offset < 0 || newest_offset >= depth)
		return 0;
	const uint32_t last_index =
		(state->interp_ring_write_idx - 1u - (uint32_t)newest_offset) % MP_INTERP_RING_CAP;
	const SnapshotPoseEntry* entry = &state->interp_ring[last_index];
	return entry->wall_stamp_us ? entry : 0;
}

bool SampleRemoteActorInterpolation(
	const RemoteActorPresentationTrack* state,
	uint64_t render_stamp_us,
	uint64_t interp_delay_us,
	float out_pos[3],
	float* out_dir,
	float* out_lerp_t,
	int* out_depth,
	int* out_mode)
{
	if (out_lerp_t)
		*out_lerp_t = 0.0f;
	if (out_mode)
		*out_mode = 0;
	if (out_depth)
		*out_depth = GetRemoteActorInterpolationDepth(state);
	if (!state || !out_pos || !out_dir)
		return false;
	const int depth = GetRemoteActorInterpolationDepth(state);
	if (depth < 2)
		return false;
	const uint64_t target_stamp =
		render_stamp_us > interp_delay_us ? (render_stamp_us - interp_delay_us) : 0u;
	const SnapshotPoseEntry* newer = 0;
	const SnapshotPoseEntry* older = 0;
	for (int newer_offset = 0; newer_offset < depth - 1; newer_offset++)
	{
		newer = GetRemoteActorInterpolationNewestEntry(state, newer_offset);
		older = GetRemoteActorInterpolationNewestEntry(state, newer_offset + 1);
		if (!newer || !older)
			continue;
		if (older->wall_stamp_us <= target_stamp && target_stamp <= newer->wall_stamp_us)
			break;
		newer = 0;
		older = 0;
	}
	if (!newer || !older)
	{
		newer = GetRemoteActorInterpolationNewestEntry(state, 0);
		older = GetRemoteActorInterpolationNewestEntry(state, 1);
		if (!newer || !older)
			return false;
		if (target_stamp <= newer->wall_stamp_us)
		{
			const uint64_t span = newer->wall_stamp_us - older->wall_stamp_us;
			float t = 0.0f;
			if (span > 0u && target_stamp > older->wall_stamp_us)
				t = (float)((double)(target_stamp - older->wall_stamp_us) / (double)span);
			if (!isfinite(t))
				return false;
			if (t < 0.0f)
				t = 0.0f;
			if (t > 1.0f)
				t = 1.0f;
			for (int i = 0; i < 3; i++)
				out_pos[i] = older->pos[i] + (newer->pos[i] - older->pos[i]) * t;
			float dir_delta = newer->dir - older->dir;
			while (dir_delta > 180.0f)
				dir_delta -= 360.0f;
			while (dir_delta < -180.0f)
				dir_delta += 360.0f;
			*out_dir = NormalizeInterpDir(older->dir + dir_delta * t);
			if (!isfinite(out_pos[0]) || !isfinite(out_pos[1]) ||
				!isfinite(out_pos[2]) || !isfinite(*out_dir))
				return false;
			if (out_lerp_t)
				*out_lerp_t = t;
			if (out_mode)
				*out_mode = 3;
			return true;
		}
		for (int i = 0; i < 3; i++)
			out_pos[i] = newer->pos[i];
		*out_dir = newer->dir;
		if (!isfinite(out_pos[0]) || !isfinite(out_pos[1]) ||
			!isfinite(out_pos[2]) || !isfinite(*out_dir))
			return false;
		if (out_lerp_t)
			*out_lerp_t = 1.0f;
		if (out_mode)
			*out_mode = 4;
		return true;
		}
	const uint64_t span = newer->wall_stamp_us - older->wall_stamp_us;
	float t = 1.0f;
	if (span > 0u)
		t = (float)((double)(target_stamp - older->wall_stamp_us) / (double)span);
	if (!isfinite(t))
		t = 1.0f;
	if (t < 0.0f)
		t = 0.0f;
	if (t > 1.0f)
		t = 1.0f;
	for (int i = 0; i < 3; i++)
		out_pos[i] = older->pos[i] + (newer->pos[i] - older->pos[i]) * t;
	float dir_delta = newer->dir - older->dir;
	while (dir_delta > 180.0f)
		dir_delta -= 360.0f;
	while (dir_delta < -180.0f)
		dir_delta += 360.0f;
	*out_dir = NormalizeInterpDir(older->dir + dir_delta * t);
	if (!isfinite(out_pos[0]) || !isfinite(out_pos[1]) ||
		!isfinite(out_pos[2]) || !isfinite(*out_dir))
		return false;
	if (out_lerp_t)
		*out_lerp_t = t;
	if (out_mode)
		*out_mode = 0;
	return true;
}

void AcceptRemoteActorPresentationSnapshot(
	Human* remote,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	const float pos[3],
	float dir,
	uint32_t snapshot_tick,
	uint64_t arrival_wall_stamp_us)
{
	if (!remote || !ent || !pos)
		return;
	const uint8_t prev_life_state = remote->life_state;
	remote->pos[0] = pos[0];
	remote->pos[1] = pos[1];
	remote->pos[2] = pos[2];
	remote->dir = dir;
	if (ShouldFlushInterpRing(
		&remote->remote_presentation_track,
		prev_life_state,
		ent->life_state,
		pos))
	{
		ClearInterpRing(&remote->remote_presentation_track);
	}
	PushInterpRingSnapshot(
		&remote->remote_presentation_track,
		pos,
		dir,
		snapshot_tick,
		arrival_wall_stamp_us);

	remote->life_state = ent->life_state;
	remote->mount_state = ent->mount_state;
	remote->locomotion_state = ent->locomotion_state;
	remote->combat_state = ent->combat_state;
	remote->presentation_kind_id = ent->presentation_kind_id;
	remote->presentation_selector_failure_reason = 0;
	remote->presentation_started_tick = ent->presentation_started_tick;
}
