#pragma once

// ActorVisualProfile runtime state.
//
// FL-4048/FL-4049 restart boundary: this is the replacement owner for the
// actor visual identity carried by snapshots. It deliberately does not include
// bundle_runtime.h, bundle_types.h, RenderPlan, mounted admission, wrapper, or
// selector concepts.

#include <stdint.h>

#include "protocol/protocol_common.h"

static constexpr uint16_t ACTOR_VISUAL_PROFILE_DEFAULT_SKIN_ID = 101;
static constexpr uint16_t ACTOR_VISUAL_PROFILE_ITEM_WEAPON_CROSSBOW_ID = 400 + 17;

// ActorVisualProfile selector/compose failure reasons.
//
// Law: These must never encode bundle/RenderPlan semantics. They are strictly
// about the ActorVisualProfile exact-match + ordered-layer compose pipeline.
enum ActorVisualProfileFailureReason : uint8_t
{
	ACTOR_VISUAL_PROFILE_FAILURE_NONE = 0,
	ACTOR_VISUAL_PROFILE_FAILURE_APPEARANCE_MISSING = 1,
	ACTOR_VISUAL_PROFILE_FAILURE_UNSUPPORTED_SLOT = 2,
	ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_NOT_FOUND = 3,
	ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE = 4,
	ACTOR_VISUAL_PROFILE_FAILURE_LAYER_RESOLVE_FAILED = 5,
	ACTOR_VISUAL_PROFILE_FAILURE_SPRITE_COMPOSE_FAILED = 6,
	ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING = 7,
};

enum ActorVisualRenderOperation : uint8_t
{
	ACTOR_VISUAL_RENDER_OPERATION_DEFINE_HEIGHT_CHANNEL = 1u << 0,
	ACTOR_VISUAL_RENDER_OPERATION_DEFINE_COLOR_KEY_AND_FRAME_METADATA = 1u << 1,
	ACTOR_VISUAL_RENDER_OPERATION_SEED_L2_BASE_ACCUMULATOR = 1u << 2,
	ACTOR_VISUAL_RENDER_OPERATION_ORDINAL_OVERLAY_MERGE_INTO_L2 = 1u << 3,
	ACTOR_VISUAL_RENDER_OPERATION_FINAL_CYAN_SWOOSH_CONTEXT_COMPOSITE = 1u << 4,
	ACTOR_VISUAL_RENDER_OPERATION_NO_VISUAL_CONTRIBUTION = 1u << 5,
};

struct ActorVisualSemanticContributionSet
{
	const char* const* values;
	uint8_t value_count;
};

struct ActorVisualSlot
{
	uint16_t slot_kind_id;
	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t state_flags;
};

struct ActorVisualProfile
{
	bool valid;
	uint16_t appearance_contract_version;
	uint16_t appearance_profile_id;
	union
	{
		uint16_t skin_id;
		uint16_t skin_definition_id;
	};
	union
	{
		uint16_t mount_id;
		uint16_t mount_definition_id;
	};
	uint16_t variation_id;
	uint16_t rig_id;
	uint32_t loadout_revision;
	uint8_t source_kind;
	uint8_t projection_kind;
	uint8_t subject_kind;
	union
	{
		uint8_t slot_count;
		uint8_t entry_count;
	};
	char subject_key[32];
	union
	{
		ActorVisualSlot slots[APPEARANCE_STATE_V2_MAX_ENTRIES];
		ActorVisualSlot entries[APPEARANCE_STATE_V2_MAX_ENTRIES];
	};
};

// FL-3988/Bug 2 phase 16: CompiledActorVisualKey is the exact runtime lookup
// key per internal design notes. Authored content lives on ActorVisualProfile; the
// compiler expands authored profiles into CompiledActorVisualRows keyed by
// CompiledActorVisualKey. All fields are server-owned: skin/style/kind/
// variation/mount/rig + canonical slot item+style IDs. Item ID 0 means empty
// slot; style ID 0 means default/no style. No fallback, no inference at
// runtime — the resolver exact-matches the full key.
struct CompiledActorVisualKey
{
	uint16_t skin_id;
	uint16_t actor_style_id;
	uint16_t presentation_kind_id;
	uint16_t variation_id;
	uint16_t mount_id;
	uint16_t rig_id;
	uint16_t head_item_id;
	uint16_t head_style_id;
	uint16_t chest_item_id;
	uint16_t chest_style_id;
	uint16_t weapon_item_id;
	uint16_t weapon_style_id;
	uint16_t shield_item_id;
	uint16_t shield_style_id;
	uint16_t future_slot_kind_ids[4];
	uint16_t future_item_ids[4];
	uint16_t future_style_ids[4];
};

// CompiledActorVisualLayer preserves the two independent upstream contracts:
// how raw cells compose and what those cells contribute semantically. The
// contribution-set index is first-class so a body+sword or mount+rider+sword
// layer is never collapsed to one runtime role.
struct CompiledActorVisualLayer
{
	uint8_t order;
	uint8_t render_operation_mask;
	uint8_t source_layer_index;
	bool required;
	uint16_t source_xp_index;
	uint16_t semantic_contribution_set_index;
	uint16_t mask_index;
	uint16_t canvas_cell_mask_index;
	const uint16_t* frame_map;
	uint16_t frame_map_count;
};

// CompiledActorVisualRow is one compiled row keyed by CompiledActorVisualKey.
// Contains the key, row-owned playback metadata, and the ordered layer stack.
// Authored profile id stays as the debug joinable identity; it is not used
// for runtime lookup or composition decisions.
// LOCOMOTION_STATE has 4 valid values (NONE/IDLE/MOVING/AIRBORNE). The runtime
// looks up locomotion_anim_track[input.locomotion_state] to pick the timeline
// anim track. The compiler resolves this from authored
// presentation_kind_playback.locomotion_anim_tracks plus optional row-owned
// overrides for timeline sources with different anim-track layouts. This
// deletes the runtime presentation-kind branch for IDLE_WALK and lets new
// presentation kinds add their own per-locomotion track selection without C++
// render logic changes.
#define ACTOR_VISUAL_LOCOMOTION_STATE_COUNT 4

struct CompiledActorVisualRow
{
	const char* id;
	CompiledActorVisualKey key;
	uint16_t timeline_source_xp_index;
	uint8_t playback_mode; // ActorVisualPlaybackDirection — resolved by compiler from authored presentation_kind_playback
	uint16_t steady_frame_index;
	uint8_t layer_count;
	uint8_t locomotion_anim_track[ACTOR_VISUAL_LOCOMOTION_STATE_COUNT];
	const CompiledActorVisualLayer* layers;
};

struct CompiledActorVisualCell
{
	uint32_t glyph;
	uint8_t fg;
	uint8_t bg;
	uint8_t spare;
};

struct CompiledActorVisualFrameMeta
{
	int16_t ref[3];
	int16_t meta_xy[2];
};

struct CompiledActorVisualCellPayload
{
	const char* profile_id;
	uint16_t width;
	uint16_t height;
	uint16_t frames;
	uint8_t angles;
	uint8_t projs;
	uint8_t anim_count;
	const uint16_t* anim_lens;
	const CompiledActorVisualFrameMeta* frame_meta;
	const CompiledActorVisualCell* cells;
};

struct ActorVisualCatalogProfile
{
	uint16_t id;
	uint16_t skin_id;
	const char* slug;
	uint8_t starter_count;
	const ActorVisualSlot* starter_loadout;
};

struct ActorVisualCatalogSeat
{
	const char* seat_alias;
	uint16_t appearance_profile_id;
};

struct ActorVisualCatalogItem
{
	uint16_t id;
	uint16_t slot_kind_id;
	uint16_t mount_id;
	uint8_t gameplay_kind_id;
	const char* slug;
	const char* world_sprite_path;
	const char* inventory_sprite_path;
};

struct ActorVisualCatalogMount
{
	uint16_t id;
	uint8_t runtime_mount_state;
	const char* slug;
};

// FL-3988/Bug 2 phase 14 source XP + semantic mask manifests.
//
// AUTHORED in profiles.json `source_xp_manifest` / `semantic_mask_manifest`
// using stable IDs (source_xp_id, semantic_mask_id). COMPILER resolves IDs to
// dense indices and emits these tables. RUNTIME reads layer.source_xp_index /
// layer.mask_index — string IDs and paths stay out of the hot path.

enum ActorVisualSemanticMaskMethod : uint8_t
{
	ACTOR_VISUAL_SEMANTIC_MASK_METHOD_ALL_VISIBLE = 0,
	ACTOR_VISUAL_SEMANTIC_MASK_METHOD_AUTHORED_CELL_SET = 1,
	ACTOR_VISUAL_SEMANTIC_MASK_METHOD_DERIVED_FROM_SOURCE_LAYER = 2,
};

enum ActorVisualSourceXpKind : uint8_t
{
	ACTOR_VISUAL_SOURCE_XP_KIND_UPSTREAM_AUTHORED = 0,
	ACTOR_VISUAL_SOURCE_XP_KIND_DERIVED_SINGLEROLE = 1,
	ACTOR_VISUAL_SOURCE_XP_KIND_PIPELINE_DECOMPOSED = 2,
	ACTOR_VISUAL_SOURCE_XP_KIND_VERIFIED_STATE_LAYER = 3,
};

// All-visible mask sentinel index. Always present at the head of the compiled
// semantic mask table; runtime can fast-path on it.
static constexpr uint16_t ACTOR_VISUAL_SEMANTIC_MASK_INDEX_ALL_VISIBLE = 0;

struct ActorVisualCompiledSourceXp
{
	const char* source_xp_id;
	const char* path;
	uint8_t source_layer_index;
	uint8_t kind; // ActorVisualSourceXpKind
};

struct ActorVisualCompiledSemanticMask
{
	const char* semantic_mask_id;
	uint8_t method; // ActorVisualSemanticMaskMethod
	uint16_t semantic_contribution_set_index;
	uint16_t source_xp_index; // 0xffff means no mask source (all_visible sentinel)
	uint8_t source_layer_index;
	const uint32_t* packed_cells; // (atlas_frame << 16) | (y << 8) | x
	uint32_t packed_cell_count;
};

struct ActorVisualCompiledCanvasCellMask
{
	const uint32_t* packed_cells; // (source_atlas_frame << 16) | (y << 8) | x
	uint32_t packed_cell_count;
};

// FL-3988/Bug 2 phase 17: Artifact Integrity Gate.
//
// CompiledActorVisualTableHeader is the single load-time identity for the
// compiled artifact. Per internal design notes the runtime loads the table as one
// artifact or fails as one artifact — no per-row recovery, no defaulted
// dimensions, no provenance-substituted compatibility.
//
// Version semantics:
// - compiled_schema_version: hard runtime compatibility for the emitted
//   struct shape. Bump this whenever the binary layout of any compiled
//   struct (CompiledActorVisualKey, CompiledActorVisualLayer, CompiledActorVisualRow,
//   CompiledActorVisualTableHeader, ActorVisualCompiledSourceXp,
//   ActorVisualCompiledSemanticMask) changes. Runtime rejects unequal values.
// - compiler_capability_version: hard runtime compatibility for emitted
//   features the runtime must understand (e.g. mask methods that the
//   runtime can dispatch, playback directions). Runtime rejects unequal
//   values.
// - Hashes: source_asset_manifest_hash, semantic_mask_manifest_hash,
//   canvas_cell_masks_hash, cell_partition_decisions_hash, server_catalog_hash,
//   server_reachability_hash gate whole-table coverage.
//   server_reachability_scope_id pins the scope this table was built against.
// - Provenance fields (compiler_build_hash, compiler_git_sha, compiler_timestamp)
//   are diagnostic only and MUST NOT gate runtime acceptance.
//
// Runtime compares reachability scope/hash and catalog hash against the
// server/deploy identity emitted by server/actor_visual_reachability_identity.generated.h.
// Empty or mismatched identity is a whole-table failure.
static constexpr uint32_t ACTOR_VISUAL_COMPILED_SCHEMA_VERSION = 3;
static constexpr uint32_t ACTOR_VISUAL_COMPILER_CAPABILITY_VERSION = 2;

struct CompiledActorVisualTableHeader
{
	uint32_t compiled_schema_version;
	uint32_t compiler_capability_version;
	const char* source_asset_manifest_hash;
	const char* semantic_mask_manifest_hash;
	const char* canvas_cell_masks_hash;
	const char* cell_partition_decisions_hash;
	const char* server_catalog_hash;
	const char* server_reachability_scope_id;
	const char* server_reachability_hash;
	// Diagnostic-only provenance. Runtime must not gate on these.
	const char* compiler_build_hash;
	const char* compiler_git_sha;
	const char* compiler_timestamp;
};

enum ActorVisualArtifactIntegrityFailure : uint8_t
{
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_OK = 0,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_SCHEMA_VERSION_MISMATCH = 1,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_CAPABILITY_VERSION_MISMATCH = 2,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_HEADER_MISSING = 3,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_REACHABILITY_SCOPE_MISMATCH = 4,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_REACHABILITY_HASH_MISMATCH = 5,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_CATALOG_HASH_MISMATCH = 6,
	ACTOR_VISUAL_ARTIFACT_INTEGRITY_RENDER_INPUT_HASH_MISSING = 7,
};

// FL-3993 playback metadata.
//
// Per-presentation_kind playback semantics. AUTHORED kind-keyed in
// profiles.json `presentation_kind_playback`. COMPILER resolves kind →
// (playback_mode, steady_frame_index) at compile time and bakes the values
// ONTO each ActorVisualCompiledProfile row. RUNTIME reads profile->playback_mode
// and applies generic ComputeFrameByPlayback — there must be NO
// presentation_kind-specific renderer branches. Adding a new presentation_kind
// is content + compiler support only.
//
// playback_mode values must match the compiler's enum mapping in
// scripts/compile_actor_visual_profiles.py PLAYBACK_DIRECTION_ENUM.
enum ActorVisualPlaybackDirection : uint8_t
{
	ACTOR_VISUAL_PLAYBACK_DIRECTION_LOOP = 0,
	ACTOR_VISUAL_PLAYBACK_DIRECTION_FORWARD_CLAMP = 1,
	ACTOR_VISUAL_PLAYBACK_DIRECTION_REVERSE_CLAMP = 2,
};

// Transitional alias while the old packet/watcher surface is migrated. The
// concrete type is now ActorVisualProfile; do not reintroduce bundle-owned
// AppearanceStateV2 logic behind this name.
typedef ActorVisualSlot AppearanceEntryV2;
typedef ActorVisualProfile AppearanceStateV2;
