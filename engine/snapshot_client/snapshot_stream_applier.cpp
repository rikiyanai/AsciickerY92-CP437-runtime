#include "snapshot_client/snapshot_stream_applier.h"

#include <math.h>

bool SnapshotPoseSane(const float pos[3], float dir)
{
	if (!pos || !isfinite(pos[0]) || !isfinite(pos[1]) || !isfinite(pos[2]) || !isfinite(dir))
		return false;
	const float max_xy_abs = 20000.0f;
	const float max_z_abs = 100000.0f;
	if (fabsf(pos[0]) > max_xy_abs || fabsf(pos[1]) > max_xy_abs || fabsf(pos[2]) > max_z_abs)
		return false;
	return true;
}

float NormalizeSnapshotDir(float dir)
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

float SnapshotAngleDeltaDeg(float from, float to)
{
	return NormalizeSnapshotDir(to - from);
}
