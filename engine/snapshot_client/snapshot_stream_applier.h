#pragma once

#include <stdint.h>

enum AuthoritativeSnapshotStreamApplyReason : uint32_t
{
	AUTH_SNAPSHOT_APPLY_REASON_NONE = 0,
	AUTH_SNAPSHOT_APPLY_REASON_MISSING_ENTITY_OR_CONTEXT = 1,
	AUTH_SNAPSHOT_APPLY_REASON_RUNTIME_NOT_READY = 2,
	AUTH_SNAPSHOT_APPLY_REASON_LOCAL_ID_OUT_OF_RANGE = 3,
	AUTH_SNAPSHOT_APPLY_REASON_ENTITY_NOT_LOCAL = 4,
	AUTH_SNAPSHOT_APPLY_REASON_BAD_POSE = 5,
	AUTH_SNAPSHOT_APPLY_REASON_REJECTED_STALE = 6,
	AUTH_SNAPSHOT_APPLY_REASON_REJECTED_OTHER = 7,
	AUTH_SNAPSHOT_APPLY_REASON_ACCEPTED = 8,
};

bool SnapshotPoseSane(const float pos[3], float dir);
static inline bool RemoteSnapshotPoseSane(const float pos[3], float dir)
{
	return SnapshotPoseSane(pos, dir);
}

float NormalizeSnapshotDir(float dir);
float SnapshotAngleDeltaDeg(float from, float to);
