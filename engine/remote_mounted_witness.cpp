#include "remote_mounted_witness.h"

#include "actor_visual_profile_runtime.h"
#include "game.h"
#include "facing_space.h"
#include "remote_observer_probe.h"

extern uint64_t a3dGetTime();

void RemoteMountedWitnessResetObserverDeathHistory(Human* h)
{
	if (!h)
		return;
	h->dbg_obs_death_transition_count = 0;
	h->dbg_obs_first_death_transition_source = 0;
	h->dbg_obs_death_transition_source31_count = 0;
	h->dbg_obs_death_transition_source21_count = 0;
	h->dbg_obs_death_transition_source24_count = 0;
	h->dbg_obs_first_death_transition_setaction_ok = 0;
	h->dbg_obs_first_death_transition_post_action = 0;
	h->dbg_obs_first_death_transition_post_mount = 0;
	h->dbg_obs_first_death_transition_frame = 0;
	h->dbg_obs_first_death_transition_sprite_family_kind = 0;
	h->dbg_obs_last_death_transition_source = 0;
	h->dbg_obs_last_death_transition_setaction_ok = 0;
	h->dbg_obs_last_death_transition_pre_action = 0;
	h->dbg_obs_last_death_transition_pre_mount = 0;
	h->dbg_obs_last_death_transition_post_action = 0;
	h->dbg_obs_last_death_transition_post_mount = 0;
	h->dbg_obs_last_death_transition_frame = 0;
	h->dbg_obs_last_death_transition_sprite_family_kind = 0;
	h->dbg_obs_death_snapshot_count = 0;
	h->dbg_obs_first_death_snapshot_life_state = 0;
	h->dbg_obs_first_death_snapshot_mount_state = 0;
	h->dbg_obs_first_death_snapshot_locomotion_state = 0;
	h->dbg_obs_first_death_snapshot_presentation_kind_id = 0;
	h->dbg_obs_first_death_snapshot_tick = 0;
	h->dbg_obs_last_death_snapshot_life_state = 0;
	h->dbg_obs_last_death_snapshot_mount_state = 0;
	h->dbg_obs_last_death_snapshot_locomotion_state = 0;
	h->dbg_obs_last_death_snapshot_presentation_kind_id = 0;
	h->dbg_obs_last_death_snapshot_tick = 0;
}

void RemoteMountedWitnessNoteObserverDeathSnapshot(
	Human* h,
	uint8_t life_state,
	uint8_t mount_state,
	uint8_t locomotion_state,
	uint16_t presentation_kind_id,
	uint32_t tick)
{
	if (!h)
		return;
	h->dbg_obs_death_snapshot_count++;
	if (h->dbg_obs_first_death_snapshot_tick == 0)
	{
		h->dbg_obs_first_death_snapshot_life_state = (int)life_state;
		h->dbg_obs_first_death_snapshot_mount_state = (int)mount_state;
		h->dbg_obs_first_death_snapshot_locomotion_state = (int)locomotion_state;
		h->dbg_obs_first_death_snapshot_presentation_kind_id = (int)presentation_kind_id;
		h->dbg_obs_first_death_snapshot_tick = tick;
	}
	h->dbg_obs_last_death_snapshot_life_state = (int)life_state;
	h->dbg_obs_last_death_snapshot_mount_state = (int)mount_state;
	h->dbg_obs_last_death_snapshot_locomotion_state = (int)locomotion_state;
	h->dbg_obs_last_death_snapshot_presentation_kind_id = (int)presentation_kind_id;
	h->dbg_obs_last_death_snapshot_tick = tick;
}

void PublishRemoteMountedWitness(
	Game* game,
	const Server* server,
	const RemoteMountedWitnessPublishInput& input)
{
	const Human* rp = input.remote;
	const ActorPresentationResult* resolved = input.resolved;
	const RemoteActorPresentationDebugSurface* surface = input.surface;
	if (!game || !rp || !resolved || !surface)
		return;

	uint32_t remote_death_seq = rp->dbg_obs_death_seq;
	uint32_t remote_last_death_source = rp->dbg_obs_last_death_source;
	uint32_t remote_respawn_seq = rp->dbg_obs_respawn_seq;
	uint32_t remote_corpse_create_seq = rp->dbg_obs_corpse_create_seq;
	uint32_t remote_corpse_delete_seq = rp->dbg_obs_corpse_delete_seq;
	uint32_t remote_corpse_create_count = rp->dbg_obs_corpse_create_count;
	uint32_t remote_corpse_delete_count = rp->dbg_obs_corpse_delete_count;
	uint32_t remote_last_corpse_create_reason = rp->dbg_obs_last_corpse_create_reason;
	uint32_t remote_last_corpse_delete_reason = rp->dbg_obs_last_corpse_delete_reason;
	if (RemoteObserverProbePidValid(server, input.remote_pid))
	{
		remote_death_seq = server->authority.combat_obs.obs_remote_death_seq[input.remote_pid];
		remote_last_death_source = server->authority.combat_obs.obs_remote_last_death_source[input.remote_pid];
		remote_respawn_seq = server->authority.combat_obs.obs_remote_respawn_seq[input.remote_pid];
		remote_corpse_create_seq = server->authority.combat_obs.obs_remote_corpse_create_seq[input.remote_pid];
		remote_corpse_delete_seq = server->authority.combat_obs.obs_remote_corpse_delete_seq[input.remote_pid];
		remote_corpse_create_count = server->authority.combat_obs.obs_remote_corpse_create_count[input.remote_pid];
		remote_corpse_delete_count = server->authority.combat_obs.obs_remote_corpse_delete_count[input.remote_pid];
		remote_last_corpse_create_reason = server->authority.combat_obs.obs_remote_last_corpse_create_reason[input.remote_pid];
		remote_last_corpse_delete_reason = server->authority.combat_obs.obs_remote_last_corpse_delete_reason[input.remote_pid];
	}

	game->debug.dbg_remote0_render_dir = surface->render_dir;
	ActorPresentationResult runtime_frame_resolved = *resolved;
	if (runtime_frame_resolved.sprite && runtime_frame_resolved.sprite->angles > 0)
	{
			const int runtime_angle = FacingSpriteAngleIndex(
				surface->render_dir,
				game->debug.dbg_camera_yaw,
				runtime_frame_resolved.sprite->angles);
			ActorVisualProfileRefreshResultRuntimeFrameFields(
				&runtime_frame_resolved,
				runtime_angle,
				0);
			resolved = &runtime_frame_resolved;
		}
	game->debug.dbg_remote0_pid = input.remote_pid;
	game->debug.dbg_remote0_life_state = rp->life_state;
	game->debug.dbg_remote0_mount_state = rp->mount_state;
	game->debug.dbg_remote0_locomotion_state = rp->locomotion_state;
	game->debug.dbg_remote0_combat_state = rp->combat_state;
	game->debug.dbg_remote0_anim = rp->anim;
	game->debug.dbg_remote0_frame = rp->frame;
	game->debug.dbg_remote0_anim_length = resolved->anim_length;
	game->debug.dbg_remote0_frame_clamped = (int)resolved->playback_frame_clamped;
	game->debug.dbg_remote0_frame_changed_expected =
		(int)resolved->playback_frame_changed_expected;
	game->debug.dbg_remote0_authoritative_tick = (int)input.render_tick;
	game->debug.dbg_remote0_presentation_started_tick = (int)rp->presentation_started_tick;
	game->debug.dbg_remote0_playback_elapsed_ticks = (int)resolved->playback_elapsed_ticks;
	game->debug.dbg_remote0_death_tick =
		(rp->life_state == LIFE_STATE::DEAD) ? (int)rp->presentation_started_tick : 0;
	game->debug.dbg_remote0_corpse_hold_age_ticks =
		(game->debug.dbg_remote0_death_tick > 0 && input.render_tick >= rp->presentation_started_tick)
			? (int)(input.render_tick - rp->presentation_started_tick)
			: 0;
	game->debug.dbg_remote0_screen_center_glyph =
		surface->materialized.has_inst ? input.screen_center_glyph : -1;
	game->debug.dbg_remote0_render_sprite_family_kind = input.render_sprite_family_kind;
	game->debug.dbg_remote0_resolved_presentation_key = input.resolved_presentation_key;
	game->debug.dbg_remote0_render_presentation_kind_id = resolved->presentation_kind_id;
	game->debug.dbg_remote0_render_diverged_from_snapshot =
		(game->debug.dbg_remote0_render_presentation_kind_id > 0 &&
		 game->debug.dbg_remote0_snapshot_presentation_kind_id > 0 &&
		 game->debug.dbg_remote0_render_presentation_kind_id != game->debug.dbg_remote0_snapshot_presentation_kind_id) ? 1 : 0;
	game->debug.dbg_remote0_render_skin_definition_id = resolved->skin_definition_id;
	game->debug.dbg_remote0_render_loadout_signature = resolved->loadout_signature;
	game->debug.dbg_remote0_render_profile_id_hash = resolved->render_profile_id_hash;
	game->debug.dbg_remote0_render_atlas_frame_index = resolved->render_atlas_frame_index;
	game->debug.dbg_remote0_render_contribution_angle =
		resolved->render_contribution_angle;
	game->debug.dbg_remote0_render_contribution_projection =
		resolved->render_contribution_projection;
	game->debug.dbg_remote0_render_contribution_scope =
		resolved->render_contribution_scope;
	game->debug.dbg_remote0_server_visual_key_valid = resolved->selector_found;
	game->debug.dbg_remote0_server_visual_key_hash = 0;
	game->debug.dbg_remote0_render_plan_found = resolved->profile_found;
	game->debug.dbg_remote0_render_plan_key_hash = 0;
	game->debug.dbg_remote0_render_plan_layer_count = resolved->profile_layer_count;
	game->debug.dbg_remote0_asset_load_failure_count =
		resolved->asset_load_failure_count;
	game->debug.dbg_remote0_render_body_layer_definition_id = resolved->body_layer_definition_id;
	game->debug.dbg_remote0_render_head_layer_definition_id = resolved->head_layer_definition_id;
	game->debug.dbg_remote0_death_seq = remote_death_seq;
	game->debug.dbg_remote0_last_death_source = remote_last_death_source;
	game->debug.dbg_remote0_respawn_seq = remote_respawn_seq;
	game->debug.dbg_remote0_corpse_create_seq = remote_corpse_create_seq;
	game->debug.dbg_remote0_corpse_delete_seq = remote_corpse_delete_seq;
	game->debug.dbg_remote0_corpse_create_count = remote_corpse_create_count;
	game->debug.dbg_remote0_corpse_delete_count = remote_corpse_delete_count;
	game->debug.dbg_remote0_last_corpse_create_reason = remote_last_corpse_create_reason;
	game->debug.dbg_remote0_last_corpse_delete_reason = remote_last_corpse_delete_reason;
	game->debug.dbg_remote0_sprite_family_kind = input.sprite_family_kind;
	game->debug.dbg_remote0_death_transition_count = (int)rp->dbg_obs_death_transition_count;
	game->debug.dbg_remote0_first_death_transition_source = (int)rp->dbg_obs_first_death_transition_source;
	game->debug.dbg_remote0_death_transition_source31_count = (int)rp->dbg_obs_death_transition_source31_count;
	game->debug.dbg_remote0_death_transition_source21_count = (int)rp->dbg_obs_death_transition_source21_count;
	game->debug.dbg_remote0_death_transition_source24_count = (int)rp->dbg_obs_death_transition_source24_count;
	game->debug.dbg_remote0_first_death_transition_setaction_ok = rp->dbg_obs_first_death_transition_setaction_ok;
	game->debug.dbg_remote0_first_death_transition_post_action = rp->dbg_obs_first_death_transition_post_action;
	game->debug.dbg_remote0_first_death_transition_post_mount = rp->dbg_obs_first_death_transition_post_mount;
	game->debug.dbg_remote0_first_death_transition_frame = rp->dbg_obs_first_death_transition_frame;
	game->debug.dbg_remote0_first_death_transition_sprite_family_kind = rp->dbg_obs_first_death_transition_sprite_family_kind;
	game->debug.dbg_remote0_last_death_transition_source = (int)rp->dbg_obs_last_death_transition_source;
	game->debug.dbg_remote0_last_death_transition_setaction_ok = rp->dbg_obs_last_death_transition_setaction_ok;
	game->debug.dbg_remote0_last_death_transition_pre_action = rp->dbg_obs_last_death_transition_pre_action;
	game->debug.dbg_remote0_last_death_transition_pre_mount = rp->dbg_obs_last_death_transition_pre_mount;
	game->debug.dbg_remote0_last_death_transition_post_action = rp->dbg_obs_last_death_transition_post_action;
	game->debug.dbg_remote0_last_death_transition_post_mount = rp->dbg_obs_last_death_transition_post_mount;
	game->debug.dbg_remote0_last_death_transition_frame = rp->dbg_obs_last_death_transition_frame;
	game->debug.dbg_remote0_last_death_transition_sprite_family_kind = rp->dbg_obs_last_death_transition_sprite_family_kind;
	game->debug.dbg_remote0_death_snapshot_count = (int)rp->dbg_obs_death_snapshot_count;
	game->debug.dbg_remote0_first_death_snapshot_life_state = rp->dbg_obs_first_death_snapshot_life_state;
	game->debug.dbg_remote0_first_death_snapshot_mount_state = rp->dbg_obs_first_death_snapshot_mount_state;
	game->debug.dbg_remote0_first_death_snapshot_locomotion_state = rp->dbg_obs_first_death_snapshot_locomotion_state;
	game->debug.dbg_remote0_first_death_snapshot_presentation_kind_id = rp->dbg_obs_first_death_snapshot_presentation_kind_id;
	game->debug.dbg_remote0_first_death_snapshot_tick = rp->dbg_obs_first_death_snapshot_tick;
	game->debug.dbg_remote0_last_death_snapshot_life_state = rp->dbg_obs_last_death_snapshot_life_state;
	game->debug.dbg_remote0_last_death_snapshot_mount_state = rp->dbg_obs_last_death_snapshot_mount_state;
	game->debug.dbg_remote0_last_death_snapshot_locomotion_state = rp->dbg_obs_last_death_snapshot_locomotion_state;
	game->debug.dbg_remote0_last_death_snapshot_presentation_kind_id = rp->dbg_obs_last_death_snapshot_presentation_kind_id;
	game->debug.dbg_remote0_last_death_snapshot_tick = rp->dbg_obs_last_death_snapshot_tick;
	game->debug.dbg_remote0_render_compose_mode = resolved->compose_mode;
	game->debug.dbg_remote0_render_ref_alignment_observed = resolved->ref_alignment_observed;
	game->debug.dbg_remote0_render_ref_alignment_overlay_layer_definition_id =
		resolved->ref_alignment_overlay_layer_definition_id;
	game->debug.dbg_remote0_render_ref_alignment_overlay_slot_kind_id =
		resolved->ref_alignment_overlay_slot_kind_id;
	game->debug.dbg_remote0_render_ref_alignment_base_ref_z =
		resolved->ref_alignment_base_ref_z;
	game->debug.dbg_remote0_render_ref_alignment_overlay_ref_z =
		resolved->ref_alignment_overlay_ref_z;
	game->debug.dbg_remote0_render_compose_failure_stage =
		resolved->compose_failure_stage;
	game->debug.dbg_remote0_render_compose_failure_base_layer_definition_id =
		resolved->compose_failure_base_layer_definition_id;
	game->debug.dbg_remote0_render_compose_failure_overlay_layer_definition_id =
		resolved->compose_failure_overlay_layer_definition_id;
	game->debug.dbg_remote0_render_compose_failure_overlay_slot_kind_id =
		resolved->compose_failure_overlay_slot_kind_id;
	game->debug.dbg_remote0_render_compose_failure_base_ref_z =
		resolved->compose_failure_base_ref_z;
	game->debug.dbg_remote0_render_compose_failure_overlay_ref_z =
		resolved->compose_failure_overlay_ref_z;
	game->debug.dbg_remote0_render_fail_visible_fallback_used = 0;
	game->debug.dbg_remote0_render_original_compose_failure_stage = 0;
	game->debug.dbg_remote0_render_layer_count = resolved->render_layer_count;
	game->debug.dbg_remote0_mount_layer_count = resolved->mount_layer_count;
	for (int i = 0; i < ACTOR_VISUAL_MAX_RENDER_LAYERS; i++)
	{
		game->debug.dbg_remote0_render_slot_kind_ids[i] = resolved->render_slot_kind_ids[i];
		game->debug.dbg_remote0_render_layer_definition_ids[i] = resolved->render_layer_definition_ids[i];
		game->debug.dbg_remote0_render_item_definition_ids[i] = resolved->render_item_definition_ids[i];
		game->debug.dbg_remote0_render_visual_style_ids[i] = resolved->render_visual_style_ids[i];
		game->debug.dbg_remote0_render_layer_semantic_contribution_set_indices[i] =
			resolved->render_layer_semantic_contribution_set_indices[i];
		game->debug.dbg_remote0_render_layer_source_layer_indices[i] =
			resolved->render_layer_source_layer_indices[i];
		game->debug.dbg_remote0_render_layer_source_path_hashes[i] =
			resolved->render_layer_source_path_hashes[i];
		game->debug.dbg_remote0_render_layer_visible_cell_counts[i] =
			resolved->render_layer_visible_cell_counts[i];
		game->debug.dbg_remote0_render_layer_contributed_cell_counts[i] =
			resolved->render_layer_contributed_cell_counts[i];
		game->debug.dbg_remote0_render_layer_occluded_cell_counts[i] =
			resolved->render_layer_occluded_cell_counts[i];
	}
	game->debug.dbg_remote0_profile_load_status = resolved->profile_load_status;
	game->debug.dbg_remote0_profile_selector_found = resolved->selector_found;
	game->debug.dbg_remote0_profile_selector_failure_reason = resolved->selector_failure_reason;
	game->debug.dbg_remote0_profile_selector_count = resolved->selector_count;
	game->debug.dbg_remote0_profile_layer_count = resolved->profile_layer_count;
	game->debug.dbg_remote0_interp_active = surface->interp_active;
	game->debug.dbg_remote0_interp_ring_depth = surface->interp_ring_depth;
	game->debug.dbg_remote0_interp_delay_ms = surface->interp_delay_ms;
	game->debug.dbg_remote0_interp_lerp_t = surface->interp_lerp_t;
	game->debug.dbg_remote0_interp_fallback_mode = surface->interp_fallback_mode;
	game->debug.dbg_remote0_interp_newest_tick = surface->interp_newest_tick;
	game->debug.dbg_remote0_interp_older_tick = surface->interp_older_tick;
	game->debug.dbg_remote0_interp_newest_wall_age_ms = surface->interp_newest_wall_age_ms;
	game->debug.dbg_remote0_interp_older_wall_age_ms = surface->interp_older_wall_age_ms;
	game->debug.dbg_remote0_interp_target_age_ms = surface->interp_target_age_ms;
	game->debug.dbg_remote0_inst_sprite_family_kind = surface->materialized.inst_sprite_family_kind;
	game->debug.dbg_remote0_inst_sprite_matches_owner = surface->materialized.inst_sprite_matches_owner;
	game->debug.dbg_remote0_post_interp_has_inst = surface->materialized.has_inst;
	game->debug.dbg_remote0_post_interp_inst_world_match = surface->materialized.inst_world_match;
	game->debug.dbg_remote0_post_interp_pos[0] = surface->render_pos[0];
	game->debug.dbg_remote0_post_interp_pos[1] = surface->render_pos[1];
	game->debug.dbg_remote0_post_interp_pos[2] = surface->render_pos[2];
	game->debug.dbg_remote0_post_interp_on_screen = surface->materialized.on_screen;
	game->debug.dbg_remote0_post_interp_label_visible = surface->materialized.label_visible;
	game->debug.dbg_remote0_post_interp_body_visible = surface->materialized.body_visible;
	game->debug.dbg_remote0_post_interp_label_only = surface->materialized.label_only;
}
