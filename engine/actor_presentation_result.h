#pragma once

#include <stdint.h>

#include "actor_visual_profile.h"

struct Sprite;

static const int ACTOR_VISUAL_MAX_RENDER_LAYERS = 8;

// Actor visual presentation result. FL-4048 restart boundary: this no longer
// depends on retired presentation-bundle storage compatibility.
struct ActorPresentationResult
{
	Sprite* sprite;
	int anim;
	int frame;
	int anim_length;
	uint32_t playback_elapsed_ticks;
	uint8_t playback_frame_clamped;
	uint8_t playback_frame_changed_expected;
	// FL-3993: row-owned playback metadata exposed to wall-clock adapters so they
	// can call ComputeFrameByPlayback without a presentation_kind branch.
	uint8_t playback_mode;            // ActorVisualPlaybackDirection from matched row
	uint16_t playback_steady_frame_index; // matched row's steady_frame_index
	// FL-4079: track index the row picked from locomotion_anim_track[locomotion_state]
	// for the current frame. Exposed so the wearable proof seam can join server
	// locomotion_state with the actually-selected animation track without re-reading
	// the compiled profile.
	uint8_t selected_locomotion_anim_track;
	uint16_t presentation_kind_id;
	uint16_t skin_definition_id;
	uint16_t body_layer_definition_id;
	uint16_t head_layer_definition_id;
	uint8_t render_layer_count;
	uint8_t mount_layer_count; // FL-2378: count of mount-role layers (from ResolvedPresentation)
	uint16_t render_slot_kind_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_layer_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_item_definition_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_visual_style_ids[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_layer_semantic_contribution_set_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint8_t render_layer_source_layer_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_layer_source_path_hashes[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	// FL-4079: source XP index per layer, carried as compiled-profile
	// provenance for the wearable proof probe and upstream-pipeline baseline
	// oracle.
	uint16_t render_layer_source_xp_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_layer_visible_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_layer_contributed_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t render_layer_occluded_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint32_t render_cache_hit_count;
	uint32_t render_cache_null_hit_count;
	uint32_t render_cache_miss_count;
	uint32_t render_cache_full_count;
	uint32_t render_cache_failure_count;
	uint32_t render_row_lookup_cache_hit_count;
	uint32_t render_row_lookup_cache_null_hit_count;
	uint32_t render_row_lookup_cache_miss_count;
	uint32_t render_row_lookup_table_scan_count;
	uint32_t render_compose_us;
	uint16_t render_profile_id_hash;
	uint16_t render_atlas_frame_index;
	int render_contribution_angle;
	uint8_t render_contribution_projection;
	uint8_t render_contribution_scope; // 0 none, 1 aggregate cache, 2 selected runtime frame.
	CompiledActorVisualKey attempted_visual_key;
	uint32_t loadout_signature;
	uint8_t profile_runtime_loaded;
	uint8_t profile_found;
	uint8_t failure_reason; // ActorVisualProfileFailureReason
	uint8_t profile_load_status; // 1 when the compiled ActorVisualProfile table is available.
	uint8_t selector_found; // 1 when exact ActorVisualProfile key lookup succeeds.
	uint8_t selector_failure_reason; // ActorVisualProfileFailureReason.
	uint8_t selector_count; // compiled ActorVisualProfile table row count, capped at 255.
	uint8_t profile_layer_count; // matched compiled profile layer count.
	uint8_t compose_mode; // 0 none, 1 ordered ActorVisualProfile stack.
	uint8_t ref_alignment_observed; // reserved old bypass detector; must stay 0.
	uint16_t ref_alignment_overlay_layer_definition_id;
	uint16_t ref_alignment_overlay_slot_kind_id;
	int ref_alignment_base_ref_z;
	int ref_alignment_overlay_ref_z;
	uint8_t compose_failure_stage; // ActorVisualProfileFailureReason mirrored for watchdog compose surface.
	uint16_t compose_failure_base_layer_definition_id;
	uint16_t compose_failure_overlay_layer_definition_id;
	uint16_t compose_failure_overlay_slot_kind_id;
	int compose_failure_base_ref_z;
	int compose_failure_overlay_ref_z;
	uint16_t asset_load_failure_count; // missing/bad source sprite layers while composing
};
