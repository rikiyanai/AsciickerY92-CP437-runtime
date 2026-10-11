#include "mp_move.h"

#include <algorithm>
#include <math.h>
#include <string.h>

#include "game.h"
#include "physics_tick.h"

namespace
{
static bool MpMoveSeqIsNewer(uint16_t seq, uint16_t prev)
{
	return seq != prev && (uint16_t)(seq - prev) < 0x8000u;
}

static void MpMoveDiagSurfaceClear(MpMoveDiagSurface* surface)
{
	if (!surface)
		return;
	memset(surface, 0, sizeof(*surface));
}

static void MpMoveDiagSurfaceFromPhysicsState(
	MpMoveDiagSurface* out,
	const PhysicsFullState* state,
	const float impulse[2],
	uint16_t seq)
{
	if (!out)
		return;
	MpMoveDiagSurfaceClear(out);
	if (!state)
		return;
	out->valid = true;
	out->seq = seq;
	out->step = MpStepFromPhysicsState(state, impulse);
	out->grounded = out->step.accum_contact >= 1.0f ? 1 : 0;
}

static void MpMoveDiagAssignError(float* out, float a, float b)
{
	if (!out)
		return;
	*out = fabsf(a - b);
}

static void MpMoveDiagComputeError(
	const MpMoveDiagSurface* lhs,
	const MpMoveDiagSurface* rhs,
	float out_pos[3],
	float out_vel[3],
	float* out_yaw,
	float* out_yaw_vel,
	float* out_slope,
	float out_impulse[2],
	int* out_grounded)
{
	if (!lhs || !rhs)
		return;
	for (int i = 0; i < 3; i++)
	{
		out_pos[i] = fabsf(lhs->step.pos[i] - rhs->step.pos[i]);
		out_vel[i] = fabsf(lhs->step.vel[i] - rhs->step.vel[i]);
	}
	MpMoveDiagAssignError(out_yaw, lhs->step.yaw, rhs->step.yaw);
	MpMoveDiagAssignError(out_yaw_vel, lhs->step.yaw_vel, rhs->step.yaw_vel);
	MpMoveDiagAssignError(out_slope, lhs->step.slope, rhs->step.slope);
	out_impulse[0] = fabsf(lhs->step.impulse[0] - rhs->step.impulse[0]);
	out_impulse[1] = fabsf(lhs->step.impulse[1] - rhs->step.impulse[1]);
	*out_grounded = lhs->grounded == rhs->grounded ? 0 : 1;
}

static bool MpMoveDiagAdvanceQuantized(
	MpMoveDiagSurface* surface,
	Terrain* terrain,
	World* world,
	float water_level,
	uint64_t stamp,
	const MpMoveQuantizedInput* input,
	uint16_t seq)
{
#ifdef __EMSCRIPTEN__
	// FL-4165: Web manual/proof runs observe server-owned collision-debug packets.
	// Replaying MpStepOnce from Render in wasm crashed before that oracle was visible.
	(void)surface;
	(void)terrain;
	(void)world;
	(void)water_level;
	(void)stamp;
	(void)input;
	(void)seq;
	return false;
#endif
	if (!surface || !surface->valid || !terrain || !world || !input)
		return false;

	MpStepInput step_input = {};
	step_input.x_force = (float)input->move_x / 127.0f;
	step_input.y_force = (float)input->move_y / 127.0f;
	step_input.z_force = (float)input->move_z / 127.0f;
	step_input.yaw = (float)input->yaw100 / 100.0f;
	step_input.jump = (input->flags & (1u << 0)) != 0;
	step_input.fly = (input->flags & (1u << 1)) != 0;

	uint8_t mount = MpMoveFlagsMount(input->flags);
	uint64_t logical_stamp = stamp;
	MpStepState step_state = surface->step;
	MpStepResult step_result = {};
	for (uint64_t sub = 0; sub < PHYSICS_SUBSTEPS; sub++)
	{
		logical_stamp += PHYSICS_STEP_US;
		// FL-4137 #25: Gap D placed-block-collider feed DELETED. This local
		// diagnostic has no server world-entity registry, so it must not
		// recreate a placed-block collider shadow path.
		MpStepEnv env = MpStepBuildEnv(terrain, world, 0, logical_stamp,
			water_level, mount);
		step_state = MpStepOnce(step_state, step_input, env, &step_result);
	}

	surface->valid = true;
	surface->seq = seq;
	surface->mount = mount;
	surface->step = step_state;
	surface->grounded = step_result.grounded ? 1 : 0;
	return true;
}

static MpMoveDiagEntry* MpMoveDiagFindEntry(MpMoveState* state, uint16_t seq)
{
	if (!state)
		return 0;
	for (int i = 0; i < MP_MOVE_DIAG_HISTORY_CAP; i++)
	{
		MpMoveDiagEntry* entry = &state->diag_history[i];
		if (entry->valid && entry->seq == seq)
			return entry;
	}
	return 0;
}

static int MpMoveDiagCountPending(const MpMoveState* state, uint16_t ack_seq)
{
	if (!state)
		return 0;
	int count = 0;
	for (int i = 0; i < MP_MOVE_DIAG_HISTORY_CAP; i++)
	{
		const MpMoveDiagEntry* entry = &state->diag_history[i];
		if (!entry->valid)
			continue;
		if (MpMoveSeqIsNewer(entry->seq, ack_seq))
			count++;
	}
	return count;
}

static int MpMoveDiagCollectPending(const MpMoveState* state, uint16_t ack_seq, MpMoveDiagEntry* out_entries, int cap)
{
	if (!state || !out_entries || cap <= 0)
		return 0;
	int count = 0;
	for (int i = 0; i < MP_MOVE_DIAG_HISTORY_CAP && count < cap; i++)
	{
		const MpMoveDiagEntry* entry = &state->diag_history[i];
		if (!entry->valid || !MpMoveSeqIsNewer(entry->seq, ack_seq))
			continue;
		out_entries[count++] = *entry;
	}
	std::sort(out_entries, out_entries + count, [ack_seq](const MpMoveDiagEntry& a, const MpMoveDiagEntry& b) {
		return (uint16_t)(a.seq - ack_seq) < (uint16_t)(b.seq - ack_seq);
	});
	return count;
}

static void MpMoveDiagPruneAcked(MpMoveState* state, uint16_t ack_seq)
{
	if (!state)
		return;
	for (int i = 0; i < MP_MOVE_DIAG_HISTORY_CAP; i++)
	{
		MpMoveDiagEntry* entry = &state->diag_history[i];
		if (!entry->valid)
			continue;
		if (!MpMoveSeqIsNewer(entry->seq, ack_seq))
			entry->valid = false;
	}
}

static void MpMoveDiagResetReplayHistory(MpMoveState* state)
{
	if (!state)
		return;
	memset(state->diag_history, 0, sizeof(state->diag_history));
	state->diag_history_write = 0;
	state->diag_pending_input_count = 0;
	state->diag_replayed_input_count = 0;
	state->diag_last_replayed_seq = 0;
	state->diag_has_last_replayed_input = false;
	MpMoveDiagSurfaceClear(&state->diag_shadow_state);
	state->diag_shadow_valid = false;
	MpMoveDiagSurfaceClear(&state->diag_replay_post);
}

static void MpMoveLogSentInputBeacon(uint16_t seq, const PhysicsIO* io)
{
	if (!io)
		return;
	static int input_beacon_count = 0;
	if (input_beacon_count++ % 60 != 0)
		return;
	printf("[TERM++:INPUT] seq=%u fx=%.2f fy=%.2f\n",
		(unsigned)seq,
		io->x_force,
		io->y_force);
	fflush(stdout);
}
}


void MpMoveInit(MpMoveState* state)
{
	if (!state)
		return;
	memset(state, 0, sizeof(*state));
}

bool MpMoveHasAuthoritativeSnapshot(const MpMoveState* state)
{
	return state && state->has_authoritative_snapshot;
}

bool MpMoveShouldRejectStaleOriginSnapshot(const float current_pos[3], const float incoming_pos[3])
{
	if (!current_pos || !incoming_pos)
		return false;

	const float dx = incoming_pos[0] - current_pos[0];
	const float dy = incoming_pos[1] - current_pos[1];
	const float dz = incoming_pos[2] - current_pos[2];
	const float d2 = dx * dx + dy * dy + dz * dz;
	const float stale_outlier_guard = 2048.0f;
	if (d2 <= stale_outlier_guard * stale_outlier_guard)
		return false;

	const bool incoming_near_origin =
		fabsf(incoming_pos[0]) < 64.0f &&
		fabsf(incoming_pos[1]) < 64.0f &&
		fabsf(incoming_pos[2]) < 1024.0f;
	const bool current_world_space = fabsf(current_pos[2]) > 2048.0f;
	return incoming_near_origin && current_world_space;
}

// MpMoveSyncAuthState deleted — dead function, no callers.

static PhysicsFullState MpMoveBuildAuthoritativeSnapshotState(
	const PhysicsFullState* base, const STRUCT_SNAPSHOT_ENTITY* ent,
	uint64_t physics_stamp)
{
	PhysicsFullState state = {};
	if (base)
		state = *base;
	if (!ent)
		return state;
	state.stamp = physics_stamp;
	state.pos[0] = ent->pos[0];
	state.pos[1] = ent->pos[1];
	state.pos[2] = ent->pos[2];
	state.vel[0] = ent->vel[0];
	state.vel[1] = ent->vel[1];
	state.vel[2] = ent->vel[2];
	state.player_dir = ent->dir;
	state.yaw = ent->yaw;
	state.yaw_vel = ent->yaw_vel;
	state.slope = ent->slope;
	state.accum_contact = ent->accum_contact;
	return state;
}

void MpMoveActivate(MpMoveState* state, Physics* phys, uint64_t stamp)
{
	if (!state)
		return;
	(void)phys;
	memset(&state->auth_state, 0, sizeof(state->auth_state));
	state->auth_state.stamp = stamp;
	state->logical_stamp = 0;
	state->last_snapshot_wall_stamp = stamp;
	state->active = true;
	state->has_authoritative_snapshot = false;
}

int8_t MpMoveQuantizeMove(float value)
{
	if (!isfinite(value))
		return 0;
	float clamped = MpClamp(value, -1.0f, 1.0f);
	int quantized = (int)lroundf(clamped * 127.0f);
	if (quantized < -127)
		quantized = -127;
	if (quantized > 127)
		quantized = 127;
	return (int8_t)quantized;
}

int16_t MpMoveQuantizeYaw100(float yaw)
{
	if (!isfinite(yaw))
		return 0;
	while (yaw > 180.0f)
		yaw -= 360.0f;
	while (yaw < -180.0f)
		yaw += 360.0f;
	int quantized = (int)lroundf(yaw * 100.0f);
	if (quantized < -32768)
		quantized = -32768;
	if (quantized > 32767)
		quantized = 32767;
	return (int16_t)quantized;
}

MpMoveTickResult MpMoveTick(MpMoveState* state, Physics* phys, PhysicsIO* io,
	uint64_t wall_stamp, bool hard_yaw_input)
{
	// Authoritative multiplayer prediction is committed on sent input and
	// re-anchored in MpMoveApplySnapshot. Per-frame free-run prediction here
	// would create a second movement owner.
	(void)phys;
	(void)hard_yaw_input;
	MpMoveTickResult result = {};
	if (!state || !io)
		return result;
	if (!state->active)
		MpMoveActivate(state, phys, wall_stamp);
	return result;
}

bool MpMoveShouldSend(const MpMoveState* state, uint64_t stamp)
{
	if (!state)
		return false;
	if (state->last_send_stamp == 0)
		return true;
	if (stamp < state->last_send_stamp)
		return false;
	return (stamp - state->last_send_stamp) >= PHYSICS_TICK_INTERVAL_US;
}

bool MpMoveSend(MpMoveState* state, uint64_t stamp, const PhysicsIO* io,
	bool attack, uint8_t mount, MpMoveSendResult* out)
{
	(void)stamp;
	if (!state || !io || !out)
		return false;
	memset(out, 0, sizeof(*out));
	if (!MpMoveBuildQuantizedInput(&out->input, io, attack, mount))
		return false;
	uint16_t seq = state->next_seq;
	if (seq == 0)
		seq = 1;
	MpMovePackReqInputMove(&out->req, &out->input, seq);
	out->valid = true;
	out->seq = seq;
	return true;
}

MpMoveSendLifecycleResult MpMoveRunSendLifecycle(
	MpMoveState* state,
	Server* server,
	uint64_t stamp,
	const PhysicsIO* io,
	bool attack,
	uint8_t mount,
	Terrain* terrain,
	World* world,
	float water_level)
{
	MpMoveSendLifecycleResult result = {};
	if (!state || !server || !io)
		return result;
	result.due = MpMoveShouldSend(state, stamp);
	if (!result.due)
		return result;

	MpMoveSendResult send_result = {};
	result.built = MpMoveSend(state, stamp, io, attack, mount, &send_result);
	if (!result.built)
		return result;
	result.seq = send_result.seq;
	if (!server->Send((const uint8_t*)&send_result.req, sizeof(STRUCT_REQ_INPUT_MOVE)))
		return result;

	result.sent = true;
	MpMoveLogSentInputBeacon(send_result.req.input_seq, io);
	// FL-4137 #25: Gap D placed-block soup forwarding DELETED. Diagnostic
	// shadow must not recreate placed-block collision outside the server-owned
	// world entity registry.
	MpMoveCaptureSentCommand(
		state,
		stamp,
		send_result.req.input_seq,
		&send_result.input,
		terrain,
		world,
		water_level,
		nullptr);
	result.jump_consumed = io->jump;
	return result;
}

bool MpMoveApplySnapshot(MpMoveState* state, Physics* phys,
	const STRUCT_SNAPSHOT_ENTITY* ent, uint64_t wall_stamp,
	bool fly_mode_active, Terrain* terrain, World* world,
	float water_level, MpMoveApplyResult* out)
{
	(void)fly_mode_active;
	if (out)
		memset(out, 0, sizeof(*out));
	if (!state || !phys || !ent || !out)
		return false;
	if (!state->active)
		MpMoveActivate(state, phys, wall_stamp);

	float local_pos[3] = { 0.0f, 0.0f, 0.0f };
	if (state->has_authoritative_snapshot)
	{
		local_pos[0] = state->auth_state.pos[0];
		local_pos[1] = state->auth_state.pos[1];
		local_pos[2] = state->auth_state.pos[2];
	}
	else
		GetPhysicsPos(phys, local_pos);
	out->accepted = true;
	memcpy(out->pre_pos, local_pos, sizeof(out->pre_pos));

	PhysicsFullState current_state = {};
	SavePhysicsState(phys, &current_state);
	out->pre_vel[0] = current_state.vel[0];
	out->pre_vel[1] = current_state.vel[1];
	out->pre_vel[2] = current_state.vel[2];
	out->final_dir = current_state.player_dir;

	if (MpMoveShouldRejectStaleOriginSnapshot(local_pos, ent->pos))
	{
		out->accepted = false;
		out->rejected_stale_origin = true;
		MpMoveInit(state);
		return false;
	}

	PhysicsFullState auth_state = MpMoveBuildAuthoritativeSnapshotState(
		&current_state, ent, state->logical_stamp ? state->logical_stamp : current_state.stamp);
	out->did_dir_sync = true;
	out->did_zero_xy = false;
	out->set_vel[0] = auth_state.vel[0];
	out->set_vel[1] = auth_state.vel[1];
	out->set_vel[2] = auth_state.vel[2];
	out->final_dir = auth_state.player_dir;
	memcpy(out->post_pos, auth_state.pos, sizeof(out->post_pos));
	if (state->has_authoritative_snapshot &&
		ent->applied_input_seq != 0 &&
		!MpMoveSeqIsNewer(ent->applied_input_seq, state->last_acked_seq) &&
		ent->applied_input_seq != state->last_acked_seq)
	{
		state->ack_seq_regression_count++;
	}
	const uint8_t prev_life_state = state->has_authoritative_snapshot
		? state->last_authoritative_life_state
		: ent->life_state;
	const bool death_transition =
		state->has_authoritative_snapshot &&
		prev_life_state == LIFE_STATE::ALIVE &&
		ent->life_state == LIFE_STATE::DEAD;
	const bool respawn_transition =
		state->has_authoritative_snapshot &&
		prev_life_state == LIFE_STATE::DEAD &&
		ent->life_state == LIFE_STATE::ALIVE;
	const bool replay_boundary_transition = death_transition || respawn_transition;
	state->logical_stamp = auth_state.stamp;
	state->auth_state = auth_state;
	state->last_snapshot_wall_stamp = wall_stamp;
	state->last_acked_seq = ent->applied_input_seq;
	state->has_authoritative_snapshot = true;
	state->last_authoritative_life_state = ent->life_state;
	MpMoveDiagSurfaceFromPhysicsState(&state->diag_auth_snap, &auth_state, ent->knockback, ent->applied_input_seq);
	MpMoveDiagEntry* ack_entry = MpMoveDiagFindEntry(state, ent->applied_input_seq);
	if (ack_entry && ack_entry->predicted_post.valid)
		state->diag_pred_ack = ack_entry->predicted_post;
	else
		MpMoveDiagSurfaceClear(&state->diag_pred_ack);

	memset(state->diag_ack_error_pos, 0, sizeof(state->diag_ack_error_pos));
	memset(state->diag_ack_error_vel, 0, sizeof(state->diag_ack_error_vel));
	state->diag_ack_error_yaw = 0.0f;
	state->diag_ack_error_yaw_vel = 0.0f;
	state->diag_ack_error_slope = 0.0f;
	state->diag_ack_error_impulse[0] = 0.0f;
	state->diag_ack_error_impulse[1] = 0.0f;
	state->diag_ack_error_grounded = 0;
	if (state->diag_pred_ack.valid && state->diag_auth_snap.valid)
	{
		MpMoveDiagComputeError(
			&state->diag_pred_ack, &state->diag_auth_snap,
			state->diag_ack_error_pos, state->diag_ack_error_vel,
			&state->diag_ack_error_yaw, &state->diag_ack_error_yaw_vel,
			&state->diag_ack_error_slope, state->diag_ack_error_impulse,
			&state->diag_ack_error_grounded);
	}

	if (replay_boundary_transition)
	{
		// Life-state boundaries invalidate queued replay history. Once the local
		// actor is dead, or freshly respawned, old pending inputs are
		// diagnostic-only work on the main thread.
		MpMoveDiagResetReplayHistory(state);
		MpMoveDiagSurfaceClear(&state->diag_pred_ack);
		memset(state->diag_ack_error_pos, 0, sizeof(state->diag_ack_error_pos));
		memset(state->diag_ack_error_vel, 0, sizeof(state->diag_ack_error_vel));
		state->diag_ack_error_yaw = 0.0f;
		state->diag_ack_error_yaw_vel = 0.0f;
		state->diag_ack_error_slope = 0.0f;
		state->diag_ack_error_impulse[0] = 0.0f;
		state->diag_ack_error_impulse[1] = 0.0f;
		state->diag_ack_error_grounded = 0;
		state->diag_shadow_state = state->diag_auth_snap;
		state->diag_shadow_valid = state->diag_shadow_state.valid;
	}

	state->diag_pending_input_count = MpMoveDiagCountPending(state, ent->applied_input_seq);
	// S6/FL-1713: diag replay is observational only. The old replay-gate path
	// (deleted 2026-04-25 in b524587d) wrote predicted client state back into
	// committed gameplay pose, which violated Law 2.
	// Do NOT restore that deleted replay gate, the predicted_state commit, or
	// any path that writes diag_replay_post back into physics or render pose.
	state->diag_replayed_input_count = 0;
	state->diag_last_replayed_seq = 0;
	state->diag_has_last_replayed_input = false;
	MpMoveDiagSurface replay_surface = state->diag_auth_snap;
	uint64_t replay_stamp = auth_state.stamp;
	if (!replay_boundary_transition && replay_surface.valid && terrain && world)
	{
		MpMoveDiagEntry pending[MP_MOVE_DIAG_HISTORY_CAP];
		int pending_count = MpMoveDiagCollectPending(state, ent->applied_input_seq, pending, MP_MOVE_DIAG_HISTORY_CAP);
		for (int i = 0; i < pending_count; i++)
		{
			// FL-4137 Gap D: feed the diag replay step the client-mirror
			// placed-block soup. Replay still writes only to the local
			// MpMoveDiagSurface (replay_surface) — server snapshot remains the
			// sole live pose owner (Law 2). The deleted predicted_state-commit
			// path is NOT being restored.
			if (!MpMoveDiagAdvanceQuantized(
					&replay_surface, terrain, world, water_level,
					pending[i].stamp, &pending[i].input, pending[i].seq))
				break;
			state->diag_replayed_input_count++;
			state->diag_last_replayed_seq = pending[i].seq;
			replay_stamp = pending[i].stamp + PHYSICS_SUBSTEPS * PHYSICS_STEP_US;
			state->diag_last_replayed_input = pending[i].input;
			state->diag_has_last_replayed_input = true;
		}
	}
	state->diag_replay_post = replay_surface;
	state->diag_shadow_state = replay_surface.valid ? replay_surface : state->diag_auth_snap;
	state->diag_shadow_valid = state->diag_shadow_state.valid;
	RestorePhysicsState(phys, &auth_state);
	memcpy(out->post_pos, auth_state.pos, sizeof(out->post_pos));
	out->set_vel[0] = auth_state.vel[0];
	out->set_vel[1] = auth_state.vel[1];
	out->set_vel[2] = auth_state.vel[2];
	out->final_dir = auth_state.player_dir;
	out->dx = auth_state.pos[0] - local_pos[0];
	out->dy = auth_state.pos[1] - local_pos[1];
	out->dz = auth_state.pos[2] - local_pos[2];
	float d2 = out->dx * out->dx + out->dy * out->dy + out->dz * out->dz;
	out->in_deadzone = d2 <= 0.0001f;
	out->did_position_correct = !out->in_deadzone;
	const float reconcile_dist = sqrtf(d2);
	if (out->did_position_correct)
	{
		state->reconcile_smooth_count++;
		if (reconcile_dist > 1.0f)
			state->reconcile_hard_snap_count++;
	}
	if (out->in_deadzone)
		state->reconcile_deadzone_skip_count++;
	memset(state->diag_replay_post_error_pos, 0, sizeof(state->diag_replay_post_error_pos));
	memset(state->diag_replay_post_error_vel, 0, sizeof(state->diag_replay_post_error_vel));
	state->diag_replay_post_error_yaw = 0.0f;
	state->diag_replay_post_error_yaw_vel = 0.0f;
	state->diag_replay_post_error_slope = 0.0f;
	state->diag_replay_post_error_impulse[0] = 0.0f;
	state->diag_replay_post_error_impulse[1] = 0.0f;
	state->diag_replay_post_error_grounded = 0;
	if (state->diag_replay_post.valid && state->diag_auth_snap.valid)
	{
		MpMoveDiagComputeError(
			&state->diag_replay_post, &state->diag_auth_snap,
			state->diag_replay_post_error_pos, state->diag_replay_post_error_vel,
			&state->diag_replay_post_error_yaw, &state->diag_replay_post_error_yaw_vel,
			&state->diag_replay_post_error_slope, state->diag_replay_post_error_impulse,
			&state->diag_replay_post_error_grounded);
	}
	MpMoveDiagPruneAcked(state, ent->applied_input_seq);
	return true;
}

bool MpMoveBuildQuantizedInput(MpMoveQuantizedInput* out, const PhysicsIO* io,
	bool attack, uint8_t mount)
{
	if (!out || !io)
		return false;
	out->move_x = MpMoveQuantizeMove(io->x_force);
	out->move_y = MpMoveQuantizeMove(io->y_force);
	out->move_z = MpMoveQuantizeMove(io->z_force);
	out->yaw100 = MpMoveQuantizeYaw100(io->yaw);
	out->flags = MpMovePackFlags(io->jump, io->fly, attack, mount);
	return true;
}

uint8_t MpMovePackFlags(bool jump, bool fly, bool attack, uint8_t mount)
{
	uint8_t flags = 0;
	if (jump)
		flags |= 1u << 0;
	if (fly)
		flags |= 1u << 1;
	if (attack)
		flags |= 1u << 2;
	flags |= (uint8_t)((mount & 0x03u) << 3);
	return flags;
}

uint8_t MpMoveFlagsMount(uint8_t flags)
{
	return (uint8_t)((flags >> 3) & 0x03u);
}

void MpMovePackReqInputMove(STRUCT_REQ_INPUT_MOVE* req, const MpMoveQuantizedInput* input, uint16_t seq)
{
	if (!req)
		return;
	memset(req, 0, sizeof(*req));
	req->token = 'M';
	if (!input)
		return;
	req->move_x = input->move_x;
	req->move_y = input->move_y;
	req->move_z = input->move_z;
	req->yaw100 = input->yaw100;
	req->flags = input->flags;
	req->input_seq = seq;
}

void MpMoveCaptureSentCommand(MpMoveState* state, uint64_t stamp, uint16_t seq,
	const MpMoveQuantizedInput* input, Terrain* terrain, World* world, float water_level,
	const float impulse[2])
{
	if (!state)
		return;
	state->last_send_stamp = stamp;
	state->next_seq = (uint16_t)(seq + 1);
	if (!input)
		return;
	state->diag_last_sent_seq = seq;
	state->diag_last_sent_input = *input;
	state->diag_has_last_sent_input = true;
	// S6/FL-1734: MpMoveCaptureSentCommand is diagnostic-only since b524587d.
	// The predicted-state write (MpStepToPhysicsState → state->auth_state) was
	// deleted. Do NOT re-add a physics arg or restore the predicted-state commit.
	// Server snapshots are the sole live gameplay pose owner (Law 2).
	if (state->has_authoritative_snapshot && state->last_authoritative_life_state == LIFE_STATE::DEAD)
	{
		// Dead-state command replay has no gameplay owner and only rebuilds a
		// diagnostic queue that the next authoritative snapshot will discard.
		MpMoveDiagResetReplayHistory(state);
		return;
	}
	if (!state->diag_shadow_valid && state->has_authoritative_snapshot)
	{
		MpMoveDiagSurfaceFromPhysicsState(&state->diag_shadow_state, &state->auth_state, impulse, state->last_acked_seq);
		state->diag_shadow_valid = state->diag_shadow_state.valid;
	}
	if (!state->diag_shadow_valid || !terrain || !world)
		return;

	MpMoveDiagSurface predicted = state->diag_shadow_state;
	// FL-4137 #21: Gap D placed-block soup feed DELETED. Diagnostic shadow
	// step sees placed blocks via QueryWorld → MeshCollect now.
	if (!MpMoveDiagAdvanceQuantized(&predicted, terrain, world, water_level,
			stamp, input, seq))
		return;

	int slot = (int)(state->diag_history_write % MP_MOVE_DIAG_HISTORY_CAP);
	if (state->diag_history[slot].valid &&
		MpMoveSeqIsNewer(state->diag_history[slot].seq, state->last_acked_seq))
	{
		state->diag_queue_overflow_count++;
	}
	state->diag_history[slot].valid = true;
	state->diag_history[slot].seq = seq;
	state->diag_history[slot].stamp = stamp;
	state->diag_history[slot].input = *input;
	state->diag_history[slot].predicted_post = predicted;
	state->diag_history_write++;
	state->diag_shadow_state = predicted;
	state->diag_shadow_valid = true;
	state->diag_last_predicted_input = *input;
	state->diag_has_last_predicted_input = true;
	// Diagnostic-only: server snapshots are the sole live gameplay pose owner.
}

// Dead replay infrastructure removed: MpMoveReplayEntryInitFromRaw, MpMoveReplayBufferPush,
// MpMoveReplayBufferFindBySeq, MpMoveReplayBufferCollectPending, MpMoveReplayBufferInit,
// MpMoveCommitPendingReplay. None had callers outside mp_move.cpp; all supported the dead
// prediction path (see MpMoveTick stub above).
