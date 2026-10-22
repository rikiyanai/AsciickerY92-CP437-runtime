#include "snapshot_client/local_snapshot_presentation_track.h"

#include <math.h>
#include <string.h>

#include "snapshot_client/snapshot_stream_applier.h"

static void SnapLocalSnapshotPresentationTrackInternal(
	LocalSnapshotPresentationTrack* track,
	const float pos[3],
	float dir,
	uint8_t life_state,
	uint64_t stamp,
	bool count_medium_snap,
	bool count_hard_snap)
{
	if (!track || !pos)
		return;
	if (count_medium_snap)
		track->medium_snap_count++;
	if (count_hard_snap)
		track->hard_snap_count++;
	memcpy(track->pos, pos, sizeof(track->pos));
	track->dir = NormalizeSnapshotDir(dir);
	track->stamp = stamp;
	track->life_state = life_state;
	track->valid = true;
}

void ResetLocalSnapshotPresentationTrack(LocalSnapshotPresentationTrack* track)
{
	if (!track)
		return;
	track->valid = false;
	track->stamp = 0;
	track->prev_yaw_resync_stamp = 0;
	track->medium_snap_count = 0;
	track->hard_snap_count = 0;
	track->life_state = 0;
	memset(track->pos, 0, sizeof(track->pos));
	track->dir = 0.0f;
}

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
	float* out_dir)
{
	if (!out_pos || !out_dir || !track || !predicted_pos || !auth_pos)
		return;
	out_pos[0] = 0.0f;
	out_pos[1] = 0.0f;
	out_pos[2] = 0.0f;
	*out_dir = 0.0f;

	const float* target_pos = predicted_pos;
	float target_dir = predicted_dir;
	uint8_t target_life_state = predicted_life_state;
	if (authoritative_session)
	{
		target_pos = auth_pos;
		target_dir = auth_dir;
	}

	// This surface is render-only: it may chase accepted authoritative snapshots,
	// but it must never write gameplay or physics state.
	if (!authoritative_session)
	{
		SnapLocalSnapshotPresentationTrackInternal(
			track, target_pos, target_dir, target_life_state, stamp, false, false);
		memcpy(out_pos, track->pos, sizeof(track->pos));
		*out_dir = track->dir;
		return;
	}

	const bool life_state_changed =
		track->valid && track->life_state != target_life_state;
	const uint64_t prev_stamp = track->stamp;
	const float dt_ms = (prev_stamp > 0 && stamp > prev_stamp)
		? (float)(stamp - prev_stamp) / 1000.0f
		: 0.0f;
	const float dx = target_pos[0] - track->pos[0];
	const float dy = target_pos[1] - track->pos[1];
	const float dz = target_pos[2] - track->pos[2];
	const float dist2 = dx * dx + dy * dy + dz * dz;
	static const float LOCAL_DISPLAY_MEDIUM_SNAP_DIST = 4.0f;
	static const float LOCAL_DISPLAY_TELEPORT_DIST = 16.0f;
	const bool teleport_without_life_change = dist2 > LOCAL_DISPLAY_TELEPORT_DIST * LOCAL_DISPLAY_TELEPORT_DIST &&
		dt_ms <= 250.0f;
	const bool force_snap = !track->valid ||
		life_state_changed ||
		teleport_without_life_change;
	if (force_snap)
	{
		const bool count_medium_snap =
			track->valid && (life_state_changed ||
				dist2 > LOCAL_DISPLAY_MEDIUM_SNAP_DIST * LOCAL_DISPLAY_MEDIUM_SNAP_DIST);
		const bool count_hard_snap =
			track->valid && (life_state_changed ||
				dist2 > LOCAL_DISPLAY_TELEPORT_DIST * LOCAL_DISPLAY_TELEPORT_DIST);
		SnapLocalSnapshotPresentationTrackInternal(
			track, target_pos, target_dir, target_life_state, stamp,
			count_medium_snap, count_hard_snap);
		memcpy(out_pos, track->pos, sizeof(track->pos));
		*out_dir = track->dir;
		return;
	}

	// Authority interpolation window: render-only smoothing must chase the
	// accepted server pose without becoming a gameplay owner. Spread ordinary
	// yellow-jitter corrections over roughly 90-140ms. Long browser stalls are
	// not teleports: after a red/main-thread freeze, target_pos can be far away
	// from normal movement alone, so keep interpolating instead of manufacturing
	// a visible catch-up snap. Life-state changes and same-frame teleports still
	// snap through force_snap above.
	static const float LOCAL_DISPLAY_MAX_CATCHUP_PER_FRAME = 2.0f;
	const float blend_dt_ms = fminf(dt_ms, 140.0f);
	const float blend = fminf(1.0f, fmaxf(0.08f, blend_dt_ms / 140.0f));
	float step_x = dx * blend;
	float step_y = dy * blend;
	float step_z = dz * blend;
	const float step_dist2 = step_x * step_x + step_y * step_y + step_z * step_z;
	if (step_dist2 > LOCAL_DISPLAY_MAX_CATCHUP_PER_FRAME * LOCAL_DISPLAY_MAX_CATCHUP_PER_FRAME)
	{
		const float step_dist = sqrtf(step_dist2);
		const float scale = LOCAL_DISPLAY_MAX_CATCHUP_PER_FRAME / step_dist;
		step_x *= scale;
		step_y *= scale;
		step_z *= scale;
	}
	track->pos[0] += step_x;
	track->pos[1] += step_y;
	track->pos[2] += step_z;
	const float dir_delta = NormalizeSnapshotDir(target_dir - track->dir);
	track->dir = NormalizeSnapshotDir(track->dir + dir_delta * blend);
	track->stamp = stamp;
	track->life_state = target_life_state;
	memcpy(out_pos, track->pos, sizeof(track->pos));
	*out_dir = track->dir;
}
