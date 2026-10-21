#include "snapshot_client/local_authoritative_snapshot.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "snapshot_client/snapshot_stream_applier.h"
#include "human.h"
#include "mp_move.h"
#include "multiplayer_protocol.h"

bool ApplyLocalAuthoritativeSnapshot(
	const LocalAuthoritativeSnapshotApplyInput* input,
	int local_id,
	int max_clients,
	uint32_t last_snapshot_tick,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	uint64_t stamp,
	LocalAuthoritativeSnapshotApplyResult* out)
{
	if (!out)
		return false;
	memset(out, 0, sizeof(*out));

	bool local_id_in_range = (local_id >= 0 && local_id < max_clients);
	bool entity_is_local = ent && local_id_in_range &&
		ent->entity_id == (uint16_t)local_id;
	if (!ent || !input || !input->local_player)
	{
		out->handled_local_entity = entity_is_local;
		out->reason = LOCAL_AUTH_SNAPSHOT_APPLY_REASON_MISSING_ENTITY_OR_CONTEXT;
		return entity_is_local;
	}
	if (!local_id_in_range)
		return false;
	if (!entity_is_local)
		return false;

	const Human* lp = input->local_player;

	const bool pose_sane = SnapshotPoseSane(ent->pos, ent->dir);
	out->handled_local_entity = true;
	if (!pose_sane)
	{
		out->reason = LOCAL_AUTH_SNAPSHOT_APPLY_REASON_BAD_POSE;
		return true;
	}
	if (ent->state_flags & SNAPSHOT_STATE_REMOVE)
	{
		out->reason = LOCAL_AUTH_SNAPSHOT_APPLY_REASON_REJECTED_OTHER;
		return true;
	}
	if (!input->physics || !input->terrain || !input->world)
	{
		out->reason = LOCAL_AUTH_SNAPSHOT_APPLY_REASON_RUNTIME_NOT_READY;
		return true;
	}
	if (!input->move_result)
	{
		out->reason = LOCAL_AUTH_SNAPSHOT_APPLY_REASON_REJECTED_OTHER;
		return true;
	}

	const MpMoveApplyResult* apply_result = input->move_result;
	bool pose_rejected_stale_origin = apply_result->rejected_stale_origin;
	uint32_t apply_reason = apply_result->accepted
		? 8
		: (pose_rejected_stale_origin ? 6 : 7);
	bool snapshot_valid = apply_result->accepted && !pose_rejected_stale_origin;

	if (lp->presentation_kind_id != ent->presentation_kind_id ||
		lp->life_state != ent->life_state ||
		input->prior_dbg_self_hp != ent->hp)
	{
		printf("[DEATH-DIAG] tick=%u life=%d->%d kind=%u->%u hp=%d->%d mount=%d pos=(%.1f,%.1f,%.1f) accepted=%d stale=%d\n",
			ent->last_authoritative_tick,
			(int)lp->life_state, (int)ent->life_state,
			(unsigned)lp->presentation_kind_id, (unsigned)ent->presentation_kind_id,
			(int)input->prior_dbg_self_hp, (int)ent->hp,
			(int)ent->mount_state,
			ent->pos[0], ent->pos[1], ent->pos[2],
			apply_result->accepted ? 1 : 0,
			pose_rejected_stale_origin ? 1 : 0);
	}

	// Semantic state to commit
	out->commit_semantic_state = true;
	out->life_state = ent->life_state;
	out->mount_state = ent->mount_state;
	out->locomotion_state = ent->locomotion_state;
	out->combat_state = ent->combat_state;
	out->presentation_kind_id = ent->presentation_kind_id;
	out->presentation_started_tick = ent->presentation_started_tick;

	// Pose to commit
	out->commit_pose = apply_result->accepted;
	if (out->commit_pose)
	{
		// Use mp_move auth_state as the canonical authoritative pose source.
		// The caller must have already reconciled mp_move before calling this.
		out->pos[0] = lp->mp_move.auth_state.pos[0];
		out->pos[1] = lp->mp_move.auth_state.pos[1];
		out->pos[2] = lp->mp_move.auth_state.pos[2];
		out->dir = lp->mp_move.auth_state.player_dir;
	}

	// Presentation sample
	out->accept_presentation_sample = apply_result->accepted ? 1 : 0;
	if (out->accept_presentation_sample)
	{
		out->presentation_sample_server_yaw = lp->mp_move.auth_state.yaw;
		out->presentation_sample_stamp = stamp;
	}

	out->applied = snapshot_valid;
	out->reason = apply_reason;

	// Debug surface
	out->commit_debug_surface = true;
	LocalAuthoritativeSnapshotApplyDebugSurface* ds = &out->debug_surface;
	ds->local_authoritative_snapshot_valid = snapshot_valid;
	ds->dbg_reconcile_applied = apply_result->did_position_correct ? 1 : 0;
	ds->dbg_reconcile_hard_snap = 0;
	ds->dbg_reconcile_zeroed_xy = apply_result->did_zero_xy ? 1 : 0;
	ds->dbg_reconcile_tick = last_snapshot_tick;
	ds->dbg_reconcile_dx = apply_result->dx;
	ds->dbg_reconcile_dy = apply_result->dy;
	ds->dbg_reconcile_dz = apply_result->dz;

	float dx = apply_result->dx;
	float dy = apply_result->dy;
	float dz = apply_result->dz;
	float d2 = dx * dx + dy * dy + dz * dz;
	ds->dbg_reconcile_auth_dist_pre = sqrtf(d2);

	float pdx = ent->pos[0] - apply_result->post_pos[0];
	float pdy = ent->pos[1] - apply_result->post_pos[1];
	float pdz = ent->pos[2] - apply_result->post_pos[2];
	ds->dbg_reconcile_auth_dist_post = sqrtf(pdx * pdx + pdy * pdy + pdz * pdz);
	ds->dbg_self_hp = ent->hp;
	ds->dbg_self_max_hp = ent->max_hp;

	return true;
}
