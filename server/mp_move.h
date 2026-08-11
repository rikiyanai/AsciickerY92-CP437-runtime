#pragma once

#include <stdint.h>

#include "physics.h"
#include "physics_state.h"
#include "mp_step.h"
#include "multiplayer_protocol.h"

struct MpMoveQuantizedInput
{
	int8_t move_x;
	int8_t move_y;
	int8_t move_z;
	int16_t yaw100;
	uint8_t flags;
};

struct MpMoveTickResult
{
	int steps;
	int substeps;
};

struct MpMoveSendLifecycleResult
{
	bool due;
	bool built;
	bool sent;
	bool jump_consumed;
	uint16_t seq;
};

struct MpMoveSendResult
{
	bool valid;
	uint16_t seq;
	MpMoveQuantizedInput input;
	STRUCT_REQ_INPUT_MOVE req;
};

struct MpMoveApplyResult
{
	bool accepted;
	bool rejected_stale_origin;
	bool in_deadzone;
	bool did_position_correct;
	bool did_zero_xy;
	bool did_dir_sync;
	// NOTE: replay counters live on MpMoveState so recorder/debug surfaces can
	// observe prediction without giving render code a second pose owner.
	float dx;
	float dy;
	float dz;
	float pre_pos[3];
	float post_pos[3];
	float pre_vel[3];
	float set_vel[3];
	float final_dir;
};

static constexpr int MP_MOVE_DIAG_HISTORY_CAP = 64;
static constexpr int MP_INTERP_RING_CAP = 8;

struct Server;

struct SnapshotPoseEntry
{
	float pos[3];
	float dir;
	uint32_t tick;
	uint32_t _pad;
	uint64_t wall_stamp_us;
};

static_assert(sizeof(SnapshotPoseEntry) == 32, "SnapshotPoseEntry layout drift");

struct MpMoveDiagSurface
{
	bool valid;
	uint16_t seq;
	uint8_t mount;
	uint8_t grounded;
	MpStepState step;
};

struct MpMoveDiagEntry
{
	bool valid;
	uint16_t seq;
	uint64_t stamp;
	MpMoveQuantizedInput input;
	MpMoveDiagSurface predicted_post;
};

struct MpMoveState
{
	PhysicsFullState auth_state;
	uint16_t next_seq;
	uint16_t last_acked_seq;
	uint64_t logical_stamp;
	uint64_t last_send_stamp;
	uint64_t last_snapshot_wall_stamp;
	bool active;
	bool has_authoritative_snapshot;
	uint32_t ack_seq_regression_count;
	uint32_t diag_queue_overflow_count;
	uint32_t reconcile_smooth_count;
	uint32_t reconcile_hard_snap_count;
	uint32_t reconcile_deadzone_skip_count;
	uint16_t diag_last_sent_seq;
	uint16_t diag_last_replayed_seq;
	int diag_pending_input_count;
	int diag_replayed_input_count;
	bool diag_has_last_sent_input;
	bool diag_has_last_predicted_input;
	bool diag_has_last_replayed_input;
	bool diag_shadow_valid;
	MpMoveQuantizedInput diag_last_sent_input;
	MpMoveQuantizedInput diag_last_predicted_input;
	MpMoveQuantizedInput diag_last_replayed_input;
	MpMoveDiagSurface diag_pred_ack;
	MpMoveDiagSurface diag_auth_snap;
	MpMoveDiagSurface diag_replay_post;
	MpMoveDiagSurface diag_shadow_state;
	float diag_ack_error_pos[3];
	float diag_ack_error_vel[3];
	float diag_ack_error_yaw;
	float diag_ack_error_yaw_vel;
	float diag_ack_error_slope;
	float diag_ack_error_impulse[2];
	int diag_ack_error_grounded;
	float diag_replay_post_error_pos[3];
	float diag_replay_post_error_vel[3];
	float diag_replay_post_error_yaw;
	float diag_replay_post_error_yaw_vel;
	float diag_replay_post_error_slope;
	float diag_replay_post_error_impulse[2];
	int diag_replay_post_error_grounded;
	MpMoveDiagEntry diag_history[MP_MOVE_DIAG_HISTORY_CAP];
	uint32_t diag_history_write;
	uint8_t interp_join_rebase_snapshots_remaining;
	uint8_t last_authoritative_life_state;
	uint8_t interp_pad[2];
};

void MpMoveInit(MpMoveState* state);
void MpMoveActivate(MpMoveState* state, Physics* phys, uint64_t stamp);
bool MpMoveHasAuthoritativeSnapshot(const MpMoveState* state);
bool MpMoveShouldRejectStaleOriginSnapshot(const float current_pos[3], const float incoming_pos[3]);
int8_t MpMoveQuantizeMove(float value);
int16_t MpMoveQuantizeYaw100(float yaw);
MpMoveTickResult MpMoveTick(MpMoveState* state, Physics* phys, PhysicsIO* io,
	uint64_t wall_stamp, bool hard_yaw_input);
bool MpMoveShouldSend(const MpMoveState* state, uint64_t stamp);
bool MpMoveSend(MpMoveState* state, uint64_t stamp, const PhysicsIO* io,
	bool attack, uint8_t mount, MpMoveSendResult* out);
// FL-4137 #21: trailing (placed_blocks, placed_block_count) pair DELETED.
// The Gap D diagnostic shadow collider feed is gone — placed blocks now enter
// the World mesh inst path (CreateInst at place/snapshot time), so the
// per-substep MpStepBuildEnv inside MpMoveDiagAdvanceQuantized sees them via
// the existing QueryWorld → MeshCollect path that already handles AKM meshes.
// engine/mp_diag_shadow_colliders.{h,cpp} is deleted. Re-adding either the
// trailing args here or that shadow TU is a regression.
MpMoveSendLifecycleResult MpMoveRunSendLifecycle(
	MpMoveState* state,
	Server* server,
	uint64_t stamp,
	const PhysicsIO* io,
	bool attack,
	uint8_t mount,
	Terrain* terrain,
	World* world,
	float water_level);
bool MpMoveApplySnapshot(MpMoveState* state, Physics* phys,
	const STRUCT_SNAPSHOT_ENTITY* ent, uint64_t wall_stamp,
	bool fly_mode_active, Terrain* terrain, World* world,
	float water_level, MpMoveApplyResult* out);
bool MpMoveBuildQuantizedInput(MpMoveQuantizedInput* out, const PhysicsIO* io,
	bool attack, uint8_t mount);
uint8_t MpMovePackFlags(bool jump, bool fly, bool attack, uint8_t mount);
uint8_t MpMoveFlagsMount(uint8_t flags);
void MpMovePackReqInputMove(STRUCT_REQ_INPUT_MOVE* req, const MpMoveQuantizedInput* input, uint16_t seq);
void MpMoveCaptureSentCommand(MpMoveState* state, uint64_t stamp, uint16_t seq,
	const MpMoveQuantizedInput* input, Terrain* terrain, World* world, float water_level,
	const float impulse[2]);
