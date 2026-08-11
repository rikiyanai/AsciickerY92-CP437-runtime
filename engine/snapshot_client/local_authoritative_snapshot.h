#pragma once

#include <stdint.h>

struct Human;
struct MpMoveApplyResult;
struct Physics;
struct Terrain;
struct World;
struct STRUCT_SNAPSHOT_ENTITY;

enum LocalAuthoritativeSnapshotApplyReason : uint32_t
{
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_NONE = 0,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_MISSING_ENTITY_OR_CONTEXT = 1,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_RUNTIME_NOT_READY = 2,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_LOCAL_ID_OUT_OF_RANGE = 3,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_ENTITY_NOT_LOCAL = 4,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_BAD_POSE = 5,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_REJECTED_STALE = 6,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_REJECTED_OTHER = 7,
	LOCAL_AUTH_SNAPSHOT_APPLY_REASON_ACCEPTED = 8,
};

struct LocalAuthoritativeSnapshotApplyDebugSurface
{
	bool local_authoritative_snapshot_valid;
	bool dbg_reconcile_applied;
	bool dbg_reconcile_hard_snap;
	bool dbg_reconcile_zeroed_xy;
	uint32_t dbg_reconcile_tick;
	float dbg_reconcile_dx;
	float dbg_reconcile_dy;
	float dbg_reconcile_dz;
	float dbg_reconcile_auth_dist_pre;
	float dbg_reconcile_auth_dist_post;
	int16_t dbg_self_hp;
	int16_t dbg_self_max_hp;
};

struct LocalAuthoritativeSnapshotApplyInput
{
	const Human* local_player;
	Physics* physics;
	Terrain* terrain;
	World* world;
	bool fly_mode;
	float water_z;
	const MpMoveApplyResult* move_result;
	int16_t prior_dbg_self_hp;
	int16_t prior_dbg_self_max_hp;
};

struct LocalAuthoritativeSnapshotApplyResult
{
	bool handled_local_entity;
	bool applied;
	uint32_t reason;

	// Semantic state to commit (caller applies to Human)
	bool commit_semantic_state;
	uint8_t life_state;
	uint8_t mount_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	uint16_t presentation_kind_id;
	uint32_t presentation_started_tick;

	// Pose to commit (caller applies to Human)
	bool commit_pose;
	float pos[3];
	float dir;

	// Presentation sample to commit
	bool accept_presentation_sample;
	float presentation_sample_server_yaw;
	uint64_t presentation_sample_stamp;

	// Debug surface
	bool commit_debug_surface;
	LocalAuthoritativeSnapshotApplyDebugSurface debug_surface;
};

bool ApplyLocalAuthoritativeSnapshot(
	const LocalAuthoritativeSnapshotApplyInput* input,
	int local_id,
	int max_clients,
	uint32_t last_snapshot_tick,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	uint64_t stamp,
	LocalAuthoritativeSnapshotApplyResult* out);
