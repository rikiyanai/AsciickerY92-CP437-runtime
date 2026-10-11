#pragma once

// local_snapshot_presentation_track.h — Local player snapshot presentation state
//
// PURPOSE:
// Tracks the local controlled player's authoritative snapshot timeline.
// Render samples from this owner. Extracted from game.h.

#include <stdint.h>

struct LocalSnapshotPresentationTrack
{
	// Local controlled-player presentation timeline.
	float pos[3];
	float dir;
	uint64_t stamp;
	uint64_t prev_yaw_resync_stamp;
	uint32_t medium_snap_count;
	uint32_t hard_snap_count;
	uint8_t life_state;
	bool valid;
};

void ResetLocalSnapshotPresentationTrack(LocalSnapshotPresentationTrack* track);
void SampleLocalSnapshotPresentationTrack(
	LocalSnapshotPresentationTrack* track,
	bool authoritative_session,
	uint64_t stamp,
	const float predicted_pos[3],
	float predicted_dir,
	const float auth_pos[3],
	float auth_dir,
	uint8_t predicted_life_state,
	float out_pos[3],
	float* out_dir);
