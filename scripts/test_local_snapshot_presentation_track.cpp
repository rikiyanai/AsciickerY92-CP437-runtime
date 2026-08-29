#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "snapshot_client/local_snapshot_presentation_track.h"

static bool Near(float a, float b, float eps = 0.001f)
{
	return fabsf(a - b) <= eps;
}

static void Sample(LocalSnapshotPresentationTrack* track,
	uint64_t stamp,
	const float target[3],
	uint8_t life_state,
	float out[3])
{
	float predicted[3] = { 0.0f, 0.0f, 0.0f };
	float out_dir = 0.0f;
	SampleLocalSnapshotPresentationTrack(
		track,
		true,
		stamp,
		predicted,
		0.0f,
		target,
		0.0f,
		life_state,
		out,
		&out_dir);
}

int main()
{
	{
		LocalSnapshotPresentationTrack track = {};
		ResetLocalSnapshotPresentationTrack(&track);
		float out[3] = {};
		float origin[3] = { 0.0f, 0.0f, 0.0f };
		float far_normal_movement[3] = { 40.0f, 0.0f, 0.0f };
		Sample(&track, 1000000ull, origin, 1, out);
		Sample(&track, 1500000ull, far_normal_movement, 1, out);
		assert(track.hard_snap_count == 0);
		assert(track.medium_snap_count == 0);
		assert(Near(out[0], 2.0f));
	}

	{
		LocalSnapshotPresentationTrack track = {};
		ResetLocalSnapshotPresentationTrack(&track);
		float out[3] = {};
		float origin[3] = { 0.0f, 0.0f, 0.0f };
		float teleport[3] = { 40.0f, 0.0f, 0.0f };
		Sample(&track, 1000000ull, origin, 1, out);
		Sample(&track, 1100000ull, teleport, 1, out);
		assert(track.hard_snap_count == 1);
		assert(track.medium_snap_count == 1);
		assert(Near(out[0], 40.0f));
	}

	{
		LocalSnapshotPresentationTrack track = {};
		ResetLocalSnapshotPresentationTrack(&track);
		float out[3] = {};
		float origin[3] = { 0.0f, 0.0f, 0.0f };
		float same_pos[3] = { 0.0f, 0.0f, 0.0f };
		Sample(&track, 1000000ull, origin, 1, out);
		Sample(&track, 1500000ull, same_pos, 2, out);
		assert(track.hard_snap_count == 1);
	}

	printf("local_snapshot_presentation_track post-stall snap tests passed\n");
	return 0;
}
