#pragma once

#include <stdint.h>

#include "physics.h"
#include "physics_state.h"
#include "world_entity_registry.h"
#include "protocol/protocol_items.h"

struct MpStepState
{
	float pos[3];
	float vel[3];
	float yaw;
	float yaw_vel;
	float player_dir;
	int player_stp;
	float slope;
	float accum_contact;
	int mat;
	float water;
	float impulse[2];
};

struct MpStepInput
{
	float x_force;
	float y_force;
	float z_force;
	float yaw;
	bool jump;
	bool fly;
};

// FL-4137 #21/#24: MpPlacedBlockCollider DELETED. Parallel collision owner
// removed per the architecture spec authored as FL-4137 fix-attempt #14 and
// violated by fix-attempt #15 (commit a6a722428). Placed blocks must enter the
// same MpStepEnv collision owner as terrain and AKM world meshes — via the
// server-owned world entity registry, not via a render/collision proxy Inst,
// a parallel soup ingest (CollectPlacedBlocks), a parallel side-push (W5
// MpStepResolvePlacedBlockSupport), or a parallel magic mesh-id (PBLK).
// Attempt #24 proved that deleting this lane is necessary but not sufficient:
// a headed proof still false-greened z=160.599 over a z≈73 block top. Re-adding
// the deleted symbols is a regression; claiming closure without near-top headed
// support evidence is also a regression.

enum MpSupportSource
{
	MP_SUPPORT_NONE = 0,
	MP_SUPPORT_TERRAIN = 1,
	MP_SUPPORT_WORLD_MESH = 2,
	MP_SUPPORT_PLACED_BLOCK = 3,
	MP_SUPPORT_WATER = 4,
};

struct MpSupportHit
{
	uint8_t found;
	uint8_t source;
	float z;
	uint16_t placed_item_id;
	uint64_t world_inst_id;
	uint64_t world_mesh_id;
};

struct MpStepEnv
{
	Terrain* terrain;
	World* world;
	const ServerWorldEntityRegistry* world_entities;
	// FL-4137 #21/#24: placed_blocks/placed_block_count REMOVED. Placed
	// blocks are world mesh instances, queried via QueryWorld → MeshCollect
	// like any other AKM mesh. Re-adding these fields reintroduces the
	// parallel collider owner that fix-attempt #14's spec forbid. Attempt #24
	// showed "world mesh exists" can still false-green unless headed support Z
	// settles near the actual block top.
	uint64_t stamp_us;
	float water_level;
	float xy_speed;
	float radius_cells;
	float world_radius;
	float world_height;
	float patch_cells;
	uint8_t mount;
};

struct MpStepResult
{
	bool grounded;
	bool landed;
	float xy_vel;
	int prev_stp;
	int next_stp;
	int mat;
	float in_water;
	MpSupportHit support;

	// Accounting: wall-clock breakdown inside MpStepOnce.
	// Goal: for large step_once_us spikes, explain where time went beyond collector query and sweep.
	uint64_t debug_step_wall_us;
	uint64_t debug_step_accounted_us;
	uint64_t debug_step_unaccounted_us;
	uint64_t debug_step_pre_collect_us;
	uint64_t debug_step_collect_build_us;
	uint64_t debug_step_post_collect_setup_us;
	uint64_t debug_step_sweep_wall_us;
	uint64_t debug_step_post_sweep_us;
	uint64_t debug_step_support_probe_us;
	uint64_t debug_step_support_retry_build_us;
	uint64_t debug_step_support_retry_probe_us;
	uint64_t debug_step_support_wide_probe_us;
	uint64_t debug_step_floor_probe_us;
	uint64_t debug_step_post_support_apply_us;
	uint64_t debug_step_material_votes_us;
	uint64_t debug_step_contact_us;
	uint64_t debug_step_grounding_us;
	uint64_t debug_step_mount_us;
	uint64_t debug_step_finalize_us;

	bool debug_sweep_ran;
	bool debug_writeback_applied;
	bool debug_support_input_gate;
	bool debug_support_found;
	bool debug_support_applied;
	bool debug_support_retry_rebuilt;
	bool debug_support_retry_found;
	float debug_support_height;
	float debug_support_depth;
	float debug_resolved_pos_z;
	float debug_sweep_z_mag;
	uint32_t debug_soup_items;
	uint32_t debug_collect_world_us;
	uint32_t debug_collect_terrain_us;
	uint32_t debug_collect_us;
	uint32_t debug_collect_mesh_us;
	uint32_t debug_collect_mesh_instances;
	uint32_t debug_collect_mesh_faces;
	uint32_t debug_collect_world_callbacks;
	uint32_t debug_collect_terrain_tris;
	// Mesh collection attribution (inside MpSoupCollector::Build -> QueryWorld -> MeshCollect -> QueryMesh/FaceCollect).
	uint32_t debug_collect_mesh_query_us_total;
	uint32_t debug_collect_mesh_query_us_max;
	uint32_t debug_collect_mesh_query_overhead_us_total;
	uint32_t debug_collect_mesh_query_overhead_us_max;
	uint32_t debug_collect_mesh_face_cb_us_total;
	uint32_t debug_collect_mesh_face_cb_us_max;
	uint32_t debug_collect_mesh_face_cb_calls;
	uint32_t debug_collect_mesh_face_cb_accepts;
	uint32_t debug_collect_mesh_face_cb_reject_visual;
	uint32_t debug_collect_mesh_face_cb_reject_alpha;
	uint32_t debug_collect_mesh_face_cb_accept_us_total;
	uint32_t debug_collect_mesh_face_cb_accept_us_max;
	uint32_t debug_collect_mesh_face_cb_push_us_total;
	uint32_t debug_collect_mesh_face_cb_push_us_max;
	uint32_t debug_collect_mesh_face_cb_material_us_total;
	uint32_t debug_collect_mesh_face_cb_material_us_max;
	uint32_t debug_collect_mesh_face_cb_transform_us_total;
	uint32_t debug_collect_mesh_face_cb_transform_us_max;
	uint32_t debug_collect_mesh_face_cb_normal_us_total;
	uint32_t debug_collect_mesh_face_cb_normal_us_max;
	uint32_t debug_collect_mesh_face_cb_bbox_us_total;
	uint32_t debug_collect_mesh_face_cb_bbox_us_max;
	uint32_t debug_collect_mesh_faces_reported_total;
	uint32_t debug_collect_mesh_faces_reported_max;
	uint64_t debug_collect_mesh_face_cb_us_max_inst_id;
	uint64_t debug_collect_mesh_face_cb_us_max_mesh_id;
	uint32_t debug_collect_mesh_face_cb_us_max_mesh_faces;
	uint32_t debug_collect_mesh_face_cb_us_max_face_ordinal;
	uint32_t debug_collect_mesh_face_cb_us_max_accept;
	uint32_t debug_collect_mesh_face_cb_us_max_visual;
	uint32_t debug_collect_mesh_face_cb_us_max_soup_index;
	int32_t debug_collect_mesh_face_cb_us_max_inst_story_id;
	uint32_t debug_collect_mesh_face_cb_us_max_inst_flags;
	uint32_t debug_collect_mesh_face_cb_us_max_inst_name_hash;
	uint32_t debug_collect_mesh_face_cb_us_max_mesh_name_hash;
	int32_t debug_collect_mesh_face_cb_us_max_inst_bbox_cx_milli;
	int32_t debug_collect_mesh_face_cb_us_max_inst_bbox_cy_milli;
	int32_t debug_collect_mesh_face_cb_us_max_inst_bbox_cz_milli;
	uint32_t debug_collect_mesh_face_cb_us_max_inst_bbox_diag_milli;
	int32_t debug_collect_mesh_face_cb_us_max_query_cx_milli;
	int32_t debug_collect_mesh_face_cb_us_max_query_cy_milli;
	uint32_t debug_collect_mesh_face_cb_us_max_query_radius_milli;
	uint32_t debug_collect_mesh_face_cb_us_max_query_bbox_dist_milli;
	int32_t debug_collect_mesh_face_cb_us_max_query_bbox_overlap_milli;
	uint32_t debug_collect_mesh_soup_reallocs;
	uint32_t debug_collect_mesh_soup_capacity_max;
	uint64_t debug_collect_mesh_soup_bytes_max;
	uint64_t debug_collect_mesh_soup_bytes_growth_total;
	uint32_t debug_collect_mesh_bbox_would_skip;
	uint32_t debug_collect_mesh_bbox_would_skip_faces;
	uint32_t debug_collect_mesh_cache_hits;
	uint32_t debug_collect_mesh_cache_misses;
	uint32_t debug_collect_mesh_cache_items;
	uint32_t debug_collect_world_bsp_tests;
	uint32_t debug_collect_world_bsp_nodes;
	uint32_t debug_collect_world_bsp_insts;

	// Support retry collector attribution. These mirror the normal collector
	// counters for the resolved-XY retry build so retry-owned spikes are not
	// collapsed into a single step_support_retry_build_us bucket.
	uint32_t debug_support_retry_soup_items;
	uint32_t debug_support_retry_collect_world_us;
	uint32_t debug_support_retry_collect_terrain_us;
	uint32_t debug_support_retry_collect_us;
	uint32_t debug_support_retry_collect_mesh_us;
	uint32_t debug_support_retry_collect_mesh_instances;
	uint32_t debug_support_retry_collect_mesh_faces;
	uint32_t debug_support_retry_collect_world_callbacks;
	uint32_t debug_support_retry_collect_terrain_tris;
	uint32_t debug_support_retry_collect_mesh_query_us_total;
	uint32_t debug_support_retry_collect_mesh_query_us_max;
	uint32_t debug_support_retry_collect_mesh_query_overhead_us_total;
	uint32_t debug_support_retry_collect_mesh_query_overhead_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_calls;
	uint32_t debug_support_retry_collect_mesh_face_cb_accepts;
	uint32_t debug_support_retry_collect_mesh_face_cb_reject_visual;
	uint32_t debug_support_retry_collect_mesh_face_cb_reject_alpha;
	uint32_t debug_support_retry_collect_mesh_face_cb_accept_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_accept_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_push_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_push_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_material_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_material_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_transform_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_transform_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_normal_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_normal_us_max;
	uint32_t debug_support_retry_collect_mesh_face_cb_bbox_us_total;
	uint32_t debug_support_retry_collect_mesh_face_cb_bbox_us_max;
	uint32_t debug_support_retry_collect_mesh_faces_reported_total;
	uint32_t debug_support_retry_collect_mesh_faces_reported_max;
	uint64_t debug_support_retry_collect_mesh_face_cb_us_max_inst_id;
	uint64_t debug_support_retry_collect_mesh_face_cb_us_max_mesh_id;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_mesh_faces;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_face_ordinal;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_accept;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_visual;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_soup_index;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_story_id;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_flags;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_name_hash;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_mesh_name_hash;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cx_milli;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cy_milli;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_cz_milli;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_inst_bbox_diag_milli;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_query_cx_milli;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_query_cy_milli;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_query_radius_milli;
	uint32_t debug_support_retry_collect_mesh_face_cb_us_max_query_bbox_dist_milli;
	int32_t debug_support_retry_collect_mesh_face_cb_us_max_query_bbox_overlap_milli;
	uint32_t debug_support_retry_collect_mesh_soup_reallocs;
	uint32_t debug_support_retry_collect_mesh_soup_capacity_max;
	uint64_t debug_support_retry_collect_mesh_soup_bytes_max;
	uint64_t debug_support_retry_collect_mesh_soup_bytes_growth_total;
	uint32_t debug_support_retry_collect_mesh_bbox_would_skip;
	uint32_t debug_support_retry_collect_mesh_bbox_would_skip_faces;
	uint32_t debug_support_retry_collect_mesh_cache_hits;
	uint32_t debug_support_retry_collect_mesh_cache_misses;
	uint32_t debug_support_retry_collect_mesh_cache_items;
	uint32_t debug_support_retry_collect_world_bsp_tests;
	uint32_t debug_support_retry_collect_world_bsp_nodes;
	uint32_t debug_support_retry_collect_world_bsp_insts;
	uint32_t debug_support_retry_same_region_repeat_count;

	uint32_t debug_sweep_iterations;
	uint32_t debug_collision_checks;
	uint32_t debug_collision_broadphase_rejects;
	uint32_t debug_soup_capped;
	uint32_t debug_mesh_per_mesh_cap_hits;
	uint32_t debug_mesh_bbox_skips;
	uint32_t debug_support_priority_callbacks;

	// Sweep/core-loop breakdown (observability only).
	// These fields aim to explain large debug_sweep_iterations/debug_collision_checks costs.
	uint64_t debug_sweep_total_us;
	uint64_t debug_sweep_narrowphase_us_total;
	uint64_t debug_sweep_narrowphase_us_max;
	uint64_t debug_sweep_narrowphase_us_last;
	uint32_t debug_sweep_narrowphase_us_max_item_index;
	uint32_t debug_sweep_narrowphase_us_max_item_material;
	uint64_t debug_sweep_narrowphase_us_max_item_inst_id;
	uint64_t debug_sweep_narrowphase_us_max_item_mesh_id;
	uint32_t debug_sweep_narrowphase_us_max_item_mesh_faces;
	uint32_t debug_sweep_narrowphase_us_max_item_face_ordinal;
	int32_t debug_sweep_narrowphase_us_max_item_inst_story_id;
	uint32_t debug_sweep_narrowphase_us_max_item_inst_flags;
	uint32_t debug_sweep_narrowphase_us_max_item_inst_name_hash;
	uint32_t debug_sweep_narrowphase_us_max_item_mesh_name_hash;
	float debug_sweep_narrowphase_us_max_item_nrm_z;
	float debug_sweep_narrowphase_us_max_item_bbox_diag;
	uint64_t debug_sweep_iter_us_max;
	uint64_t debug_sweep_iter_us_last;
	uint32_t debug_sweep_iter_collision_checks_max;
	uint32_t debug_sweep_iter_collision_checks_last;
	uint32_t debug_sweep_iter_hits;
	float debug_sweep_iter_earliest_t_last;
	float debug_sweep_iter_remaining_move_len_last;
	float debug_sweep_iter_output_normal_z_last;
	uint32_t debug_sweep_iter_deflection_count;
	uint32_t debug_collision_sample_count;
	STRUCT_BRC_COLLISION_DEBUG_SAMPLE debug_collision_samples[COLLISION_DEBUG_SAMPLE_MAX];
	// FL-4165: read-only collision attribution for operator/debug logs.
	// These fields mirror the winning sweep contact from MpStepOnce. They do
	// not own collision or support; they only expose which physics-owned
	// source produced the contact so server logs can say "PLAYER_PUSHING..."
	// without inferring from render/item appearance.
	uint8_t debug_last_sweep_collision_source;
	uint8_t debug_last_sweep_collision_side;
	uint16_t debug_last_sweep_collision_item_id;
	uint64_t debug_last_sweep_collision_entity_id;
	uint64_t debug_last_sweep_collision_inst_id;
	uint64_t debug_last_sweep_collision_mesh_id;
	uint32_t debug_last_sweep_collision_face_ordinal;
	float debug_last_sweep_collision_normal[3];
	float debug_last_sweep_collision_bmin[3];
	float debug_last_sweep_collision_bmax[3];
};

MpStepState MpStepOnce(
	const MpStepState& prev,
	const MpStepInput& input,
	const MpStepEnv& env,
	MpStepResult* out);

MpStepState MpStepFromPhysicsState(const PhysicsFullState* state, const float impulse[2]);
void MpStepToPhysicsState(const MpStepState* state, PhysicsFullState* out, uint64_t stamp);
void MpStepApplyStateToIO(const MpStepState* state, const MpStepResult* result, PhysicsIO* io);
// FL-4137 #25: MpStepBuildEnv consumes the server-owned world entity registry
// for placed-block CollisionBody/SupportSurface data. Re-adding
// placed_blocks/placed_block_count or a render/collision proxy Inst route is a
// regression; so is claiming closure without near-top headed stand-on proof.
MpStepEnv MpStepBuildEnv(Terrain* terrain,
	World* world,
	const ServerWorldEntityRegistry* world_entities,
	uint64_t stamp_us,
	float water_level,
	uint8_t mount);
float MpStepWorldRadiusForMount(uint8_t mount);
