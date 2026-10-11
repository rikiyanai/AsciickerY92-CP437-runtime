#pragma once

// ActorVisualProfile runtime facade.
//
// FL-4048/FL-4049 restart boundary: the old bundle resolver was deleted.
// Runtime selection is exact ActorVisualProfile/catalog lookup, not fallback
// synthesis.

#include "actor_presentation_input.h"
#include "actor_presentation_result.h"
#include "../server/actor_visual_catalog_source.h"
#include "../server/actor_visual_reachability_identity.generated.h"
#include "authoritative_item_state.h"
#include "actor_visual_profile_table.generated.h"
#include "a3d_load_context.h"
#include "sprite.h"
#include "sprite_constants.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>
#include <stdint.h>

extern uint64_t a3dGetTime();

enum ActorVisualItemFailureReason
{
	ACTOR_VISUAL_ITEM_FAILURE_NONE = 0,
	ACTOR_VISUAL_ITEM_FAILURE_INVALID_STATE = 1,
	ACTOR_VISUAL_ITEM_FAILURE_PROFILE_UNAVAILABLE = 2,
	ACTOR_VISUAL_ITEM_FAILURE_ITEM_NOT_FOUND = 3,
	ACTOR_VISUAL_ITEM_FAILURE_VISUAL_NOT_FOUND = 4,
	ACTOR_VISUAL_ITEM_FAILURE_SPRITE_LOAD_FAILED = 5,
};


struct ActorVisualProfileCacheStatsView
{
	int composed_cache_count;
	uint32_t composed_cache_eviction_count;
	uint32_t composed_cache_evicted_neutral_count;
	uint32_t composed_cache_evicted_enemy_count;
	uint32_t composed_cache_hit_count;
	uint32_t composed_cache_null_hit_count;
	uint32_t composed_cache_miss_count;
	uint32_t composed_cache_full_count;
	uint32_t composed_cache_failure_count;
	uint32_t row_lookup_cache_hit_count;
	uint32_t row_lookup_cache_null_hit_count;
	uint32_t row_lookup_cache_miss_count;
	uint32_t row_lookup_table_scan_count;
};

static constexpr int ACTOR_VISUAL_PROFILE_MAX_SOURCE_CACHE =
	kActorVisualSourceXpCount * ACTOR_VISUAL_MAX_RENDER_LAYERS;
static constexpr int ACTOR_VISUAL_PROFILE_MAX_COMPOSE_CACHE = kCompiledActorVisualRowCount;
static constexpr int ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_SIZE = 256;
static constexpr int ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS = 4;

static_assert(
	ACTOR_VISUAL_PROFILE_MAX_SOURCE_CACHE >=
		kActorVisualSourceXpCount * ACTOR_VISUAL_MAX_RENDER_LAYERS,
	"ActorVisualProfile source cache must cover every compiled raw source layer.");
static_assert(
	ACTOR_VISUAL_PROFILE_MAX_COMPOSE_CACHE >= kCompiledActorVisualRowCount,
	"ActorVisualProfile compose cache must cover every compiled row.");

struct ActorVisualProfileSourceCacheEntry
{
	uint16_t source_xp_index;
	uint8_t source_layer_index;
	uint8_t attempted;
	Sprite* sprite;
};

struct ActorVisualProfileComposeCacheEntry
{
	const CompiledActorVisualRow* row;
	Sprite* sprite;
	uint8_t failure_reason;
	uint8_t layer_count;
	uint16_t layer_semantic_contribution_set_indices[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t layer_source_path_hashes[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t layer_visible_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t layer_contributed_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
	uint16_t layer_occluded_cell_counts[ACTOR_VISUAL_MAX_RENDER_LAYERS];
};

struct ActorVisualProfileComposeCacheCounters
{
	uint32_t active_count;
	uint32_t hit_count;
	uint32_t null_hit_count;
	uint32_t miss_count;
	uint32_t full_count;
	uint32_t failure_count;
};

struct ActorVisualProfileRowLookupCounters
{
	uint32_t hit_count;
	uint32_t null_hit_count;
	uint32_t miss_count;
	uint32_t table_scan_count;
};

struct ActorVisualProfileRowLookupCacheEntry
{
	uint8_t filled;
	CompiledActorVisualKey key;
	const CompiledActorVisualRow* row;
};

static inline ActorVisualProfileSourceCacheEntry*
ActorVisualProfileSourceCache()
{
	static ActorVisualProfileSourceCacheEntry cache[ACTOR_VISUAL_PROFILE_MAX_SOURCE_CACHE] = {};
	return cache;
}

static inline ActorVisualProfileComposeCacheEntry*
ActorVisualProfileComposeCache()
{
	static ActorVisualProfileComposeCacheEntry cache[ACTOR_VISUAL_PROFILE_MAX_COMPOSE_CACHE] = {};
	return cache;
}

static inline ActorVisualProfileComposeCacheCounters*
ActorVisualProfileComposeCacheCounterState()
{
	static ActorVisualProfileComposeCacheCounters counters = {};
	return &counters;
}

static inline ActorVisualProfileRowLookupCacheEntry*
ActorVisualProfileRowLookupCache()
{
	static ActorVisualProfileRowLookupCacheEntry cache[
		ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_SIZE *
		ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS] = {};
	return cache;
}

static inline ActorVisualProfileRowLookupCounters*
ActorVisualProfileRowLookupCounterState()
{
	static ActorVisualProfileRowLookupCounters counters = {};
	return &counters;
}

static inline uint8_t* ActorVisualProfileRowLookupCacheNextWay()
{
	static uint8_t next_way[ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_SIZE] = {};
	return next_way;
}

static inline int ActorVisualProfileRowIndex(const CompiledActorVisualRow* profile)
{
	if (!profile)
		return -1;
	const uintptr_t profile_addr = (uintptr_t)profile;
	const uintptr_t base_addr = (uintptr_t)kCompiledActorVisualRows;
	const uintptr_t table_bytes =
		(uintptr_t)kCompiledActorVisualRowCount * (uintptr_t)sizeof(kCompiledActorVisualRows[0]);
	if (profile_addr < base_addr || profile_addr >= base_addr + table_bytes)
		return -1;
	const uintptr_t offset = profile_addr - base_addr;
	if ((offset % (uintptr_t)sizeof(kCompiledActorVisualRows[0])) != 0)
		return -1;
	const uintptr_t index = offset / (uintptr_t)sizeof(kCompiledActorVisualRows[0]);
	if (index >= (uintptr_t)kCompiledActorVisualRowCount)
		return -1;
	if (&kCompiledActorVisualRows[index] != profile)
		return -1;
	return (int)index;
}

static inline bool ActorVisualProfileStringEquals(const char* a, const char* b)
{
	return a && b && a[0] && b[0] && strcmp(a, b) == 0;
}

static inline bool ActorVisualProfileStringPresent(const char* value)
{
	return value && value[0];
}

// FL-3988/Bug 2 phase 17: Artifact Integrity Gate. Whole-table accept-or-reject
// against the compiled header. Per internal design notes the runtime must not make
// per-row compatibility decisions and must not substitute defaults for unknown
// IDs or dimensions. Returns ACTOR_VISUAL_ARTIFACT_INTEGRITY_OK or the first
// failure reason; gate logic uses the return value, not provenance.
static inline uint8_t ValidateCompiledActorVisualTableIntegrity()
{
	if (kCompiledActorVisualTableHeader.compiled_schema_version !=
		ACTOR_VISUAL_COMPILED_SCHEMA_VERSION)
		return ACTOR_VISUAL_ARTIFACT_INTEGRITY_SCHEMA_VERSION_MISMATCH;
	if (kCompiledActorVisualTableHeader.compiler_capability_version !=
		ACTOR_VISUAL_COMPILER_CAPABILITY_VERSION)
		return ACTOR_VISUAL_ARTIFACT_INTEGRITY_CAPABILITY_VERSION_MISMATCH;
	if (!ActorVisualProfileStringPresent(kCompiledActorVisualTableHeader.source_asset_manifest_hash) ||
		!ActorVisualProfileStringPresent(kCompiledActorVisualTableHeader.semantic_mask_manifest_hash) ||
		!ActorVisualProfileStringPresent(kCompiledActorVisualTableHeader.canvas_cell_masks_hash) ||
		!ActorVisualProfileStringPresent(kCompiledActorVisualTableHeader.cell_partition_decisions_hash))
		return ACTOR_VISUAL_ARTIFACT_INTEGRITY_RENDER_INPUT_HASH_MISSING;
	if (!ActorVisualProfileStringEquals(
			kCompiledActorVisualTableHeader.server_reachability_scope_id,
			kServerActorVisualReachabilityScopeId))
		return ACTOR_VISUAL_ARTIFACT_INTEGRITY_REACHABILITY_SCOPE_MISMATCH;
	if (!ActorVisualProfileStringEquals(
			kCompiledActorVisualTableHeader.server_reachability_hash,
			kServerActorVisualReachabilityHash))
		return ACTOR_VISUAL_ARTIFACT_INTEGRITY_REACHABILITY_HASH_MISMATCH;
	if (!ActorVisualProfileStringEquals(
			kCompiledActorVisualTableHeader.server_catalog_hash,
			kServerActorVisualCatalogHash))
		return ACTOR_VISUAL_ARTIFACT_INTEGRITY_CATALOG_HASH_MISMATCH;
	return ACTOR_VISUAL_ARTIFACT_INTEGRITY_OK;
}

static inline bool EnsureActorVisualProfileRuntimeLoaded()
{
	if (kCompiledActorVisualRowCount <= 0)
		return false;
	return ValidateCompiledActorVisualTableIntegrity() ==
		ACTOR_VISUAL_ARTIFACT_INTEGRITY_OK;
}

static inline uint32_t ActorVisualProfileHashMix(uint32_t hash, uint32_t value)
{
	hash ^= value;
	hash *= 16777619u;
	return hash;
}

static inline uint16_t ActorVisualProfileSourcePathHash16(const char* path)
{
	if (!path || !path[0])
		return 0;
	uint32_t hash = 2166136261u;
	for (const unsigned char* p = (const unsigned char*)path; *p; ++p)
	{
		hash ^= (uint32_t)(*p);
		hash *= 16777619u;
	}
	uint16_t out = (uint16_t)(((hash >> 16) ^ (hash & 0xffffu)) & 0xffffu);
	return out ? out : 1;
}

static inline const CompiledActorVisualRow* FindCompiledActorVisualRow(
	const CompiledActorVisualKey& key);
static inline Sprite* LoadActorVisualProfileSourceSprite(
	uint16_t source_xp_index,
	uint8_t source_layer_index);
static inline ActorVisualProfileComposeCacheEntry*
FindActorVisualProfileComposeCache(const CompiledActorVisualRow* profile);

static inline int ActorVisualProfileExactAtlasFrameIndex(
	Sprite* sprite,
	int anim,
	int frame,
	int angle,
	int projection)
{
	if (!sprite || !sprite->atlas || sprite->anims <= 0 || sprite->angles <= 0)
		return -1;
	if (anim < 0 || anim >= sprite->anims)
		return -1;
	if (!sprite->anim[anim].frame_idx || sprite->anim[anim].length <= 0)
		return -1;
	const int len = sprite->anim[anim].length;
	if (frame < 0 || frame >= len)
		return -1;
	if (angle < 0 || angle >= sprite->angles)
		return -1;
	if (projection < 0 || projection >= sprite->projs)
		return -1;
	const int proj = projection;
	const size_t frame_idx_index =
		(size_t)(proj * sprite->angles + angle) * (size_t)len + (size_t)frame;
	const int atlas_index = sprite->anim[anim].frame_idx[frame_idx_index];
	if (atlas_index < 0 || atlas_index >= sprite->frames)
		return -1;
	return atlas_index;
}

static inline void ActorVisualProfileRefreshResultRuntimeFrameFields(
	ActorPresentationResult* out,
	int angle,
	int projection)
{
	if (!out || !out->profile_found)
		return;
	const CompiledActorVisualRow* profile =
		FindCompiledActorVisualRow(out->attempted_visual_key);
	if (!profile)
		return;
	ActorVisualProfileComposeCacheEntry* cache_entry =
		FindActorVisualProfileComposeCache(profile);
	if (cache_entry)
	{
		for (int i = 0; i < ACTOR_VISUAL_MAX_RENDER_LAYERS; i++)
		{
			out->render_layer_semantic_contribution_set_indices[i] =
				cache_entry->layer_semantic_contribution_set_indices[i];
			out->render_layer_source_path_hashes[i] =
				cache_entry->layer_source_path_hashes[i];
			out->render_layer_visible_cell_counts[i] =
				cache_entry->layer_visible_cell_counts[i];
			out->render_layer_contributed_cell_counts[i] =
				cache_entry->layer_contributed_cell_counts[i];
			out->render_layer_occluded_cell_counts[i] =
				cache_entry->layer_occluded_cell_counts[i];
		}
	}
	out->render_profile_id_hash = ActorVisualProfileSourcePathHash16(profile->id);
	out->render_contribution_angle = angle;
	out->render_contribution_projection = projection ? 1 : 0;
	const int composed_atlas_index = ActorVisualProfileExactAtlasFrameIndex(
		out->sprite,
		out->anim,
		out->frame,
		angle,
		projection);
	out->render_atlas_frame_index =
		(uint16_t)(composed_atlas_index < 0 ? 0xffffu : (uint16_t)composed_atlas_index);
	out->render_contribution_scope = cache_entry ? 1 : 0;
}

static inline void ClearActorVisualProfileRuntimeCache()
{
	ActorVisualProfileComposeCacheEntry* compose_cache =
		ActorVisualProfileComposeCache();
	for (int i = 0; i < ACTOR_VISUAL_PROFILE_MAX_COMPOSE_CACHE; i++)
	{
		if (compose_cache[i].sprite)
			FreeSprite(compose_cache[i].sprite);
		compose_cache[i] = {};
	}
	ActorVisualProfileSourceCacheEntry* source_cache =
		ActorVisualProfileSourceCache();
	for (int i = 0; i < ACTOR_VISUAL_PROFILE_MAX_SOURCE_CACHE; i++)
	{
		if (source_cache[i].sprite)
			FreeSprite(source_cache[i].sprite);
		source_cache[i] = {};
	}
	*ActorVisualProfileComposeCacheCounterState() = {};
	*ActorVisualProfileRowLookupCounterState() = {};
	ActorVisualProfileRowLookupCacheEntry* row_lookup_cache =
		ActorVisualProfileRowLookupCache();
	for (int i = 0;
		i < ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_SIZE *
			ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS;
		i++)
		row_lookup_cache[i] = {};
	uint8_t* row_lookup_next_way = ActorVisualProfileRowLookupCacheNextWay();
	for (int i = 0; i < ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_SIZE; i++)
		row_lookup_next_way[i] = 0;
}

static inline void FlushRetiredActorVisualProfileComposedSprites()
{
}

static inline void GetActorVisualProfileCacheStats(ActorVisualProfileCacheStatsView* out)
{
	if (out)
	{
		*out = {};
		ActorVisualProfileComposeCacheCounters* counters =
			ActorVisualProfileComposeCacheCounterState();
		out->composed_cache_count = (int)counters->active_count;
		out->composed_cache_hit_count = counters->hit_count;
		out->composed_cache_null_hit_count = counters->null_hit_count;
		out->composed_cache_miss_count = counters->miss_count;
		out->composed_cache_full_count = counters->full_count;
		out->composed_cache_failure_count = counters->failure_count;
		ActorVisualProfileRowLookupCounters* row_lookup_counters =
			ActorVisualProfileRowLookupCounterState();
		out->row_lookup_cache_hit_count = row_lookup_counters->hit_count;
		out->row_lookup_cache_null_hit_count = row_lookup_counters->null_hit_count;
		out->row_lookup_cache_miss_count = row_lookup_counters->miss_count;
		out->row_lookup_table_scan_count = row_lookup_counters->table_scan_count;
	}
}

static inline Sprite* ResolveAuthoritativeItemActorVisualSprite(
	const AuthoritativeItemState* state,
	bool world_sprite,
	uint8_t* out_failure_reason = 0)
{
	if (!state || !state->valid || state->item_definition_id == 0)
	{
		if (out_failure_reason)
			*out_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_INVALID_STATE;
		return 0;
	}
	const AppearanceCatalogItemDef* item =
		FindAppearanceCatalogItemById(state->item_definition_id);
	if (!item)
	{
		if (out_failure_reason)
			*out_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_ITEM_NOT_FOUND;
		return 0;
	}
	const char* path =
		world_sprite ? item->world_sprite_path : item->inventory_sprite_path;
	if (!path || !path[0])
	{
		if (out_failure_reason)
			*out_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_VISUAL_NOT_FOUND;
		return 0;
	}
	Sprite* sprite = LoadSprite(path, item->slug, 0, false, true);
	if (!sprite)
	{
		if (out_failure_reason)
			*out_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_SPRITE_LOAD_FAILED;
		return 0;
	}
	if (out_failure_reason)
		*out_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_NONE;
	return sprite;
}

static inline bool CompiledActorVisualKeyEquals(
	const CompiledActorVisualKey& a,
	const CompiledActorVisualKey& b)
{
	if (a.skin_id != b.skin_id ||
		a.actor_style_id != b.actor_style_id ||
		a.presentation_kind_id != b.presentation_kind_id ||
		a.variation_id != b.variation_id ||
		a.mount_id != b.mount_id ||
		a.rig_id != b.rig_id ||
		a.head_item_id != b.head_item_id ||
		a.head_style_id != b.head_style_id ||
		a.chest_item_id != b.chest_item_id ||
		a.chest_style_id != b.chest_style_id ||
		a.weapon_item_id != b.weapon_item_id ||
		a.weapon_style_id != b.weapon_style_id ||
		a.shield_item_id != b.shield_item_id ||
		a.shield_style_id != b.shield_style_id)
		return false;
	for (int i = 0; i < 4; i++)
	{
		if (a.future_slot_kind_ids[i] != b.future_slot_kind_ids[i] ||
			a.future_item_ids[i] != b.future_item_ids[i] ||
			a.future_style_ids[i] != b.future_style_ids[i])
			return false;
	}
	return true;
}

static inline uint32_t CompiledActorVisualKeyHash(const CompiledActorVisualKey& key)
{
	uint32_t hash = 2166136261u;
	hash = ActorVisualProfileHashMix(hash, key.skin_id);
	hash = ActorVisualProfileHashMix(hash, key.actor_style_id);
	hash = ActorVisualProfileHashMix(hash, key.presentation_kind_id);
	hash = ActorVisualProfileHashMix(hash, key.variation_id);
	hash = ActorVisualProfileHashMix(hash, key.mount_id);
	hash = ActorVisualProfileHashMix(hash, key.rig_id);
	hash = ActorVisualProfileHashMix(hash, key.head_item_id);
	hash = ActorVisualProfileHashMix(hash, key.head_style_id);
	hash = ActorVisualProfileHashMix(hash, key.chest_item_id);
	hash = ActorVisualProfileHashMix(hash, key.chest_style_id);
	hash = ActorVisualProfileHashMix(hash, key.weapon_item_id);
	hash = ActorVisualProfileHashMix(hash, key.weapon_style_id);
	hash = ActorVisualProfileHashMix(hash, key.shield_item_id);
	hash = ActorVisualProfileHashMix(hash, key.shield_style_id);
	for (int i = 0; i < 4; i++)
	{
		hash = ActorVisualProfileHashMix(hash, key.future_slot_kind_ids[i]);
		hash = ActorVisualProfileHashMix(hash, key.future_item_ids[i]);
		hash = ActorVisualProfileHashMix(hash, key.future_style_ids[i]);
	}
	return hash ? hash : 1u;
}

static inline bool BuildCompiledActorVisualKey(
	const ActorVisualProfile* profile,
	uint16_t presentation_kind_id,
	CompiledActorVisualKey* out)
{
	if (!profile || !profile->valid || !out)
		return false;
	*out = {};
	out->skin_id = profile->skin_id;
	// actor_style_id is not yet authored on ActorVisualProfile; default 0 until
	// the snapshot path carries it. Compiler keys ride 0 to match.
	out->actor_style_id = 0;
	out->presentation_kind_id = presentation_kind_id;
	out->variation_id = profile->variation_id;
	out->mount_id = profile->mount_id;
	out->rig_id = profile->rig_id;
	int future_slot_count = 0;
	for (int i = 0; i < profile->slot_count && i < APPEARANCE_STATE_V2_MAX_ENTRIES; i++)
	{
		const ActorVisualSlot& slot = profile->slots[i];
		switch (slot.slot_kind_id)
		{
		case APPEARANCE_SLOT_KIND_HEAD:
			out->head_item_id = slot.item_definition_id;
			out->head_style_id = slot.visual_style_id;
			break;
		case APPEARANCE_SLOT_KIND_ARMOR:
			out->chest_item_id = slot.item_definition_id;
			out->chest_style_id = slot.visual_style_id;
			break;
		case APPEARANCE_SLOT_KIND_WEAPON:
			out->weapon_item_id = slot.item_definition_id;
			out->weapon_style_id = slot.visual_style_id;
			break;
		case APPEARANCE_SLOT_KIND_SHIELD:
			out->shield_item_id = slot.item_definition_id;
			out->shield_style_id = slot.visual_style_id;
			break;
		case APPEARANCE_SLOT_KIND_BODY:
		case APPEARANCE_SLOT_KIND_MOUNT:
			break;
		default:
		{
			if (future_slot_count >= 4)
				return false;
			int insert_at = future_slot_count;
			while (insert_at > 0)
			{
				uint16_t prev_slot = out->future_slot_kind_ids[insert_at - 1];
				uint16_t prev_item = out->future_item_ids[insert_at - 1];
				uint16_t prev_style = out->future_style_ids[insert_at - 1];
				if (prev_slot < slot.slot_kind_id ||
					(prev_slot == slot.slot_kind_id &&
						prev_item <= slot.item_definition_id))
					break;
				out->future_slot_kind_ids[insert_at] = prev_slot;
				out->future_item_ids[insert_at] = prev_item;
				out->future_style_ids[insert_at] = prev_style;
				insert_at--;
			}
			out->future_slot_kind_ids[insert_at] = slot.slot_kind_id;
			out->future_item_ids[insert_at] = slot.item_definition_id;
			out->future_style_ids[insert_at] = slot.visual_style_id;
			future_slot_count++;
			break;
		}
		}
	}
	return true;
}

static inline const CompiledActorVisualRow* FindCompiledActorVisualRow(
	const CompiledActorVisualKey& key)
{
	ActorVisualProfileRowLookupCacheEntry* lookup_cache =
		ActorVisualProfileRowLookupCache();
	ActorVisualProfileRowLookupCounters* counters =
		ActorVisualProfileRowLookupCounterState();
	const uint32_t lookup_index =
		CompiledActorVisualKeyHash(key) % ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_SIZE;
	ActorVisualProfileRowLookupCacheEntry* bucket =
		lookup_cache + lookup_index * ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS;
	for (int way = 0; way < ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS; way++)
	{
		ActorVisualProfileRowLookupCacheEntry* lookup = bucket + way;
		if (lookup->filled && CompiledActorVisualKeyEquals(lookup->key, key))
		{
			counters->hit_count++;
			if (!lookup->row)
				counters->null_hit_count++;
			return lookup->row;
		}
	}
	counters->miss_count++;
	ActorVisualProfileRowLookupCacheEntry* write_entry = 0;
	for (int way = 0; way < ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS; way++)
	{
		ActorVisualProfileRowLookupCacheEntry* lookup = bucket + way;
		if (!lookup->filled)
		{
			write_entry = lookup;
			break;
		}
	}
	if (!write_entry)
	{
		uint8_t* next_way = ActorVisualProfileRowLookupCacheNextWay();
		const uint8_t way =
			(uint8_t)(next_way[lookup_index] % ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS);
		write_entry = bucket + way;
		next_way[lookup_index] =
			(uint8_t)((way + 1) % ACTOR_VISUAL_PROFILE_ROW_LOOKUP_CACHE_WAYS);
	}
	counters->table_scan_count++;
	for (int i = 0; i < kCompiledActorVisualRowCount; i++)
	{
		if (CompiledActorVisualKeyEquals(kCompiledActorVisualRows[i].key, key))
		{
			write_entry->filled = 1;
			write_entry->key = key;
			write_entry->row = &kCompiledActorVisualRows[i];
			return &kCompiledActorVisualRows[i];
		}
	}
	write_entry->filled = 1;
	write_entry->key = key;
	write_entry->row = 0;
	return 0;
}

static inline bool ActorVisualProfileHasExactCompiledRow(
	const ActorVisualProfile* profile,
	uint16_t presentation_kind_id)
{
	CompiledActorVisualKey key = {};
	if (!BuildCompiledActorVisualKey(profile, presentation_kind_id, &key))
		return false;
	return FindCompiledActorVisualRow(key) != 0;
}

static inline Sprite* LoadActorVisualProfileSourceSprite(
	uint16_t source_xp_index,
	uint8_t source_layer_index)
{
	if (source_xp_index >= kActorVisualSourceXpCount ||
		source_layer_index >= ACTOR_VISUAL_MAX_RENDER_LAYERS)
		return 0;
	const ActorVisualCompiledSourceXp& source_xp =
		kActorVisualSourceXps[source_xp_index];
	const char* source_path = source_xp.path;
	if (!source_path || !source_path[0])
		return 0;
	ActorVisualProfileSourceCacheEntry* cache = ActorVisualProfileSourceCache();
	const int cache_index =
		(int)source_xp_index * ACTOR_VISUAL_MAX_RENDER_LAYERS + source_layer_index;
	ActorVisualProfileSourceCacheEntry* entry = cache + cache_index;
	if (entry->attempted)
		return (entry->source_xp_index == source_xp_index &&
			entry->source_layer_index == source_layer_index)
			? entry->sprite : 0;
	entry->source_xp_index = source_xp_index;
	entry->source_layer_index = source_layer_index;
	char full_path[1024];
	snprintf(full_path, sizeof(full_path), "%s%s", base_path, source_path);
	char sprite_name[1024];
	snprintf(
		sprite_name,
		sizeof(sprite_name),
		"%s#L%d",
		source_path,
		(int)source_layer_index);
	Sprite* sprite = LoadSpriteLayer(
		full_path,
		sprite_name,
		source_layer_index,
		0,
		true,
		true,
		false);
	entry->attempted = 1;
	entry->sprite = sprite;
	if (!sprite)
		return 0;
	return sprite;
}

static inline uint8_t ActorVisualProfileRuntimeLayerCount(
	const CompiledActorVisualRow* profile)
{
	if (!profile || profile->layer_count == 0 || !profile->layers)
		return 0;
	return profile->layer_count;
}

static inline ActorVisualProfileComposeCacheEntry*
FindActorVisualProfileComposeCache(
	const CompiledActorVisualRow* profile)
{
	const int index = ActorVisualProfileRowIndex(profile);
	if (index < 0)
		return 0;
	ActorVisualProfileComposeCacheEntry* cache = ActorVisualProfileComposeCache();
	ActorVisualProfileComposeCacheEntry* entry = cache + index;
	return (entry->row == profile) ? entry : 0;
}

static inline ActorVisualProfileComposeCacheEntry*
ReserveActorVisualProfileComposeCache(
	const CompiledActorVisualRow* profile)
{
	const int index = ActorVisualProfileRowIndex(profile);
	if (index < 0)
		return 0;
	ActorVisualProfileComposeCacheEntry* cache = ActorVisualProfileComposeCache();
	ActorVisualProfileComposeCacheEntry* entry = cache + index;
	if (!entry->row)
	{
		entry->row = profile;
		ActorVisualProfileComposeCacheCounterState()->active_count++;
		return entry;
	}
	return (entry->row == profile) ? entry : 0;
}

static inline Sprite* FailActorVisualProfileLoad(
	ActorVisualProfileComposeCacheEntry* slot,
	ActorVisualProfileComposeCacheCounters* counters,
	uint8_t reason,
	uint8_t* out_failure_reason,
	Sprite* sprite)
;

static inline Sprite* CreateActorVisualProfileFailMarkerSprite()
{
	Sprite* sprite = (Sprite*)malloc(sizeof(Sprite));
	if (!sprite)
		return 0;
	memset(sprite, 0, sizeof(Sprite));
	sprite->refs = 1;
	sprite->projs = 2;
	sprite->angles = 1;
	sprite->anims = 1;
	sprite->frames = 2;
	sprite->atlas = (Sprite::Frame*)malloc(sizeof(Sprite::Frame) * 2);
	if (!sprite->atlas)
	{
		free(sprite);
		return 0;
	}
	memset(sprite->atlas, 0, sizeof(Sprite::Frame) * 2);
	for (int f = 0; f < 2; f++)
	{
		Sprite::Frame* frame = sprite->atlas + f;
		frame->width = 3;
		frame->height = 3;
		frame->ref[0] = 3;
		frame->ref[1] = f ? 6 : 0;
		frame->ref[2] = 0;
		frame->cell = (AnsiCell*)malloc(sizeof(AnsiCell) * 9);
		if (!frame->cell)
		{
			FreeSprite(sprite);
			return 0;
		}
		for (int i = 0; i < 9; i++)
		{
			frame->cell[i].fg = 196;
			frame->cell[i].bk = 16;
			frame->cell[i].gl = (i == 4) ? '!' : '#';
			frame->cell[i].spare = 0;
		}
	}
	sprite->anim[0].length = 1;
	sprite->anim[0].frame_idx = (int*)malloc(sizeof(int) * 2);
	if (!sprite->anim[0].frame_idx)
	{
		FreeSprite(sprite);
		return 0;
	}
	sprite->anim[0].frame_idx[0] = 0;
	sprite->anim[0].frame_idx[1] = 1;
	sprite->name = strdup("actor_visual_profile:missing");
	return sprite;
}

static inline Sprite* ActorVisualProfileFailMarkerSprite()
{
	static Sprite* fail_marker = 0;
	if (!fail_marker)
		fail_marker = CreateActorVisualProfileFailMarkerSprite();
	return fail_marker;
}

static inline Sprite* FailActorVisualProfileLoad(
	ActorVisualProfileComposeCacheEntry* slot,
	ActorVisualProfileComposeCacheCounters* counters,
	uint8_t reason,
	uint8_t* out_failure_reason,
	Sprite* sprite)
{
	if (sprite)
		FreeSprite(sprite);
	if (counters)
		counters->failure_count++;
	Sprite* marker = ActorVisualProfileFailMarkerSprite();
	if (slot)
	{
		slot->sprite = 0;
		slot->failure_reason = reason;
	}
	if (out_failure_reason)
		*out_failure_reason = reason;
	return marker;
}

static inline Sprite* LoadActorVisualProfileCellPayloadSprite(
	const CompiledActorVisualRow* profile,
	uint8_t* out_failure_reason)
{
	if (out_failure_reason)
		*out_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
	const int profile_index = ActorVisualProfileRowIndex(profile);
	if (profile_index < 0 ||
		profile_index >= kCompiledActorVisualCellPayloadCount)
	{
		if (out_failure_reason)
			*out_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_NOT_FOUND;
		return ActorVisualProfileFailMarkerSprite();
	}
	ActorVisualProfileComposeCacheEntry* cached =
		FindActorVisualProfileComposeCache(profile);
	if (cached)
	{
		ActorVisualProfileComposeCacheCounters* counters =
			ActorVisualProfileComposeCacheCounterState();
		counters->hit_count++;
		if (!cached->sprite)
			counters->null_hit_count++;
		if (out_failure_reason)
			*out_failure_reason = cached->failure_reason;
		return cached->sprite ? cached->sprite : ActorVisualProfileFailMarkerSprite();
	}
	ActorVisualProfileComposeCacheCounters* counters =
		ActorVisualProfileComposeCacheCounterState();
	counters->miss_count++;
	ActorVisualProfileComposeCacheEntry* slot =
		ReserveActorVisualProfileComposeCache(profile);
	if (!slot)
	{
		counters->full_count++;
		counters->failure_count++;
		if (out_failure_reason)
			*out_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE;
		return ActorVisualProfileFailMarkerSprite();
	}
	for (int i = 0; i < ACTOR_VISUAL_MAX_RENDER_LAYERS; i++)
	{
		slot->layer_semantic_contribution_set_indices[i] = 0;
		slot->layer_source_path_hashes[i] = 0;
		slot->layer_visible_cell_counts[i] = 0;
		slot->layer_contributed_cell_counts[i] = 0;
		slot->layer_occluded_cell_counts[i] = 0;
	}
	const uint8_t runtime_layer_count =
		ActorVisualProfileRuntimeLayerCount(profile);
	slot->layer_count = runtime_layer_count;
	for (int i = 0; i < runtime_layer_count && i < ACTOR_VISUAL_MAX_RENDER_LAYERS; i++)
	{
		const CompiledActorVisualLayer& layer = profile->layers[i];
		const char* layer_path =
			(layer.source_xp_index < kActorVisualSourceXpCount)
				? kActorVisualSourceXps[layer.source_xp_index].path : 0;
		slot->layer_semantic_contribution_set_indices[i] =
			layer.semantic_contribution_set_index;
		slot->layer_source_path_hashes[i] =
			ActorVisualProfileSourcePathHash16(layer_path);
	}
	const CompiledActorVisualCellPayload& payload =
		kCompiledActorVisualCellPayloads[profile_index];
	if (payload.width == 0 || payload.height == 0 || payload.frames == 0 ||
		payload.anim_count == 0 || !payload.anim_lens ||
		payload.angles == 0 || payload.projs != 2 || !payload.cells)
	{
		return FailActorVisualProfileLoad(
			slot, counters,
			ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE,
			out_failure_reason, 0);
	}
	const int anim_count = payload.anim_count;
	Sprite* sprite = (Sprite*)malloc(
		sizeof(Sprite) + sizeof(Sprite::Anim) * (size_t)(anim_count - 1));
	if (!sprite)
	{
		return FailActorVisualProfileLoad(
			slot, counters,
			ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE,
			out_failure_reason, 0);
	}
	memset(sprite, 0, sizeof(Sprite) + sizeof(Sprite::Anim) * (size_t)(anim_count - 1));
	sprite->refs = 1;
	sprite->projs = payload.projs;
	sprite->angles = payload.angles;
	sprite->anims = anim_count;
	sprite->frames = payload.frames;
	sprite->atlas = (Sprite::Frame*)malloc(sizeof(Sprite::Frame) * payload.frames);
	if (!sprite->atlas)
	{
		return FailActorVisualProfileLoad(
			slot, counters,
			ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE,
			out_failure_reason, sprite);
	}
	memset(sprite->atlas, 0, sizeof(Sprite::Frame) * payload.frames);
	for (int frame_index = 0; frame_index < payload.frames; frame_index++)
	{
		Sprite::Frame* frame = sprite->atlas + frame_index;
		frame->width = payload.width;
		frame->height = payload.height;
		if (!payload.frame_meta)
		{
			return FailActorVisualProfileLoad(
				slot, counters,
				ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE,
				out_failure_reason, sprite);
		}
		const CompiledActorVisualFrameMeta& meta = payload.frame_meta[frame_index];
		frame->ref[0] = meta.ref[0];
		frame->ref[1] = meta.ref[1];
		frame->ref[2] = meta.ref[2];
		frame->meta_xy[0] = meta.meta_xy[0];
		frame->meta_xy[1] = meta.meta_xy[1];
		frame->cell = (AnsiCell*)malloc(
			sizeof(AnsiCell) * (size_t)payload.width * (size_t)payload.height);
		if (!frame->cell)
		{
			return FailActorVisualProfileLoad(
				slot, counters,
				ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE,
				out_failure_reason, sprite);
		}
		for (int cell_index = 0;
			cell_index < payload.width * payload.height;
			cell_index++)
		{
			const size_t payload_cell_index =
				(size_t)frame_index * (size_t)payload.width *
				(size_t)payload.height + (size_t)cell_index;
			const CompiledActorVisualCell& cell =
				payload.cells[payload_cell_index];
			frame->cell[cell_index].fg = cell.fg;
			frame->cell[cell_index].bk = cell.bg;
			frame->cell[cell_index].gl = (uint8_t)cell.glyph;
			frame->cell[cell_index].spare = cell.spare;
		}
	}
	size_t name_len = strlen("actor_visual_profile:") + strlen(profile->id) + 1;
	sprite->name = (char*)malloc(name_len);
	if (sprite->name)
		snprintf(sprite->name, name_len, "actor_visual_profile:%s", profile->id);
	int anim_sum = 0;
	for (int anim = 0; anim < anim_count; anim++)
	{
		sprite->anim[anim].length = payload.anim_lens[anim];
		anim_sum += payload.anim_lens[anim];
	}
	const int fr_num_x = payload.projs * anim_sum;
	for (int anim = 0; anim < anim_count; anim++)
	{
		const size_t entries =
			(size_t)2 * (size_t)payload.angles * (size_t)sprite->anim[anim].length;
		sprite->anim[anim].frame_idx = (int*)malloc(sizeof(int) * entries);
		if (!sprite->anim[anim].frame_idx)
		{
			return FailActorVisualProfileLoad(
				slot, counters,
				ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_UNAVAILABLE,
				out_failure_reason, sprite);
		}
	}
	for (int refl = 0; refl < 2; refl++)
	{
		int rx = refl * fr_num_x / 2;
		for (int angle = 0; angle < payload.angles; angle++)
		{
			int x = rx;
			for (int anim = 0; anim < anim_count; anim++)
			{
				for (int frame = 0; frame < sprite->anim[anim].length; frame++)
				{
					int idx = x + angle * fr_num_x;
					sprite->anim[anim].frame_idx[
						(refl * payload.angles + angle) *
						sprite->anim[anim].length + frame] = idx;
					x++;
				}
			}
		}
	}
	float zoom = 2.0f / 3.0f;
	sprite->proj_bbox[0] = -payload.width * .5f * zoom;
	sprite->proj_bbox[1] = +payload.width * .5f * zoom;
	sprite->proj_bbox[2] = -payload.width * .5f * zoom;
	sprite->proj_bbox[3] = +payload.width * .5f * zoom;
	sprite->proj_bbox[4] = 0;
	sprite->proj_bbox[5] = payload.height * zoom;
	slot->sprite = sprite;
	slot->failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
	return sprite;
}

static inline bool ActorVisualSemanticContributionSetContains(
	uint16_t set_index,
	const char* value)
{
	if (!value || set_index >= kActorVisualSemanticContributionSetCount)
		return false;
	const ActorVisualSemanticContributionSet& set =
		kActorVisualSemanticContributionSets[set_index];
	for (uint8_t i = 0; i < set.value_count; i++)
	{
		if (set.values[i] && strcmp(set.values[i], value) == 0)
			return true;
	}
	return false;
}

// FL-3993 generic content-driven frame compute. Inputs (mode, elapsed,
// anim_length, steady_frame_index) come from the compiled profile's per-row
// playback metadata. NO presentation_kind branches here — adding a new
// presentation_kind requires only content + (if a new direction enum is
// introduced) one new branch in this single function.
static inline uint32_t ComputeFrameByPlayback(
	uint8_t mode, uint32_t elapsed, uint32_t anim_length, uint32_t steady_frame_index)
{
	if (anim_length == 0)
		return 0;
	switch (mode)
	{
	case ACTOR_VISUAL_PLAYBACK_DIRECTION_LOOP:
		return elapsed % anim_length;
	case ACTOR_VISUAL_PLAYBACK_DIRECTION_FORWARD_CLAMP:
		return (elapsed >= anim_length - 1u) ? (anim_length - 1u) : elapsed;
	case ACTOR_VISUAL_PLAYBACK_DIRECTION_REVERSE_CLAMP:
		if (elapsed >= anim_length)
			return steady_frame_index < anim_length ? steady_frame_index : (anim_length - 1u);
		return (anim_length - 1u) - elapsed;
	default:
		return 0;
	}
}

static inline bool IsPlaybackFrameClamped(uint8_t mode, uint32_t elapsed, uint32_t anim_length)
{
	if (anim_length == 0 || mode == ACTOR_VISUAL_PLAYBACK_DIRECTION_LOOP)
		return false;
	return elapsed >= anim_length - 1u;
}

static inline bool IsPlaybackFrameChangingExpected(uint8_t mode, uint32_t elapsed, uint32_t anim_length)
{
	if (anim_length <= 1)
		return false;
	if (mode == ACTOR_VISUAL_PLAYBACK_DIRECTION_LOOP)
		return true;
	return elapsed < anim_length - 1u;
}

static inline bool IsKnownPlaybackDirection(uint8_t mode)
{
	return mode == ACTOR_VISUAL_PLAYBACK_DIRECTION_LOOP ||
		mode == ACTOR_VISUAL_PLAYBACK_DIRECTION_FORWARD_CLAMP ||
		mode == ACTOR_VISUAL_PLAYBACK_DIRECTION_REVERSE_CLAMP;
}

static inline ActorPresentationResult ResolveActorVisualProfilePresentation(
	const ActorPresentationInput& input)
{
	ActorPresentationResult out = {};
	out.presentation_kind_id = input.presentation_kind_id;
	if (!input.appearance_state)
	{
		out.selector_failure_reason =
			ACTOR_VISUAL_PROFILE_FAILURE_APPEARANCE_MISSING;
		return out;
	}
	out.skin_definition_id = input.appearance_state->skin_id;
	out.profile_runtime_loaded = EnsureActorVisualProfileRuntimeLoaded() ? 1 : 0;
	out.profile_load_status = out.profile_runtime_loaded;
	out.selector_count = (kCompiledActorVisualRowCount > 255)
		? 255
		: (uint8_t)kCompiledActorVisualRowCount;
	if (!BuildCompiledActorVisualKey(
			input.appearance_state,
			input.presentation_kind_id,
			&out.attempted_visual_key))
	{
		out.selector_failure_reason =
			ACTOR_VISUAL_PROFILE_FAILURE_UNSUPPORTED_SLOT;
		return out;
	}
	const CompiledActorVisualRow* profile =
		FindCompiledActorVisualRow(out.attempted_visual_key);
	if (!profile)
	{
		out.selector_failure_reason =
			ACTOR_VISUAL_PROFILE_FAILURE_PROFILE_NOT_FOUND;
		out.sprite = ActorVisualProfileFailMarkerSprite();
		return out;
	}
	out.profile_found = 1;
	out.selector_found = 1;
	out.render_profile_id_hash = ActorVisualProfileSourcePathHash16(profile->id);
	out.profile_layer_count = profile->layer_count;
	out.compose_mode = 1;
	const uint8_t runtime_layer_count =
		ActorVisualProfileRuntimeLayerCount(profile);
	out.render_layer_count = runtime_layer_count;
	// FL-3993: expose row-owned playback metadata so wall-clock adapters can
	// compute frames without a presentation_kind branch.
	out.playback_mode = profile->playback_mode;
	out.playback_steady_frame_index = profile->steady_frame_index;
	uint32_t signature = 2166136261u;
	signature = ActorVisualProfileHashMix(signature, profile->key.skin_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.actor_style_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.presentation_kind_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.variation_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.mount_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.rig_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.head_item_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.head_style_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.chest_item_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.chest_style_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.weapon_item_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.weapon_style_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.shield_item_id);
	signature = ActorVisualProfileHashMix(signature, profile->key.shield_style_id);
	for (int i = 0; i < 4; i++)
	{
		signature = ActorVisualProfileHashMix(signature, profile->key.future_slot_kind_ids[i]);
		signature = ActorVisualProfileHashMix(signature, profile->key.future_item_ids[i]);
		signature = ActorVisualProfileHashMix(signature, profile->key.future_style_ids[i]);
	}
	for (int i = 0;
		i < runtime_layer_count && i < ACTOR_VISUAL_MAX_RENDER_LAYERS;
		i++)
	{
		const CompiledActorVisualLayer& layer = profile->layers[i];
		out.render_layer_semantic_contribution_set_indices[i] =
			layer.semantic_contribution_set_index;
		out.render_layer_source_layer_indices[i] = layer.source_layer_index;
		// Slot/item/style cannot truthfully be reduced to one value for a
		// composite source layer. The exact server-owned loadout remains in the
		// compiled key; per-layer semantics use the contribution set above.
		out.render_slot_kind_ids[i] = 0;
		out.render_item_definition_ids[i] = 0;
		out.render_visual_style_ids[i] = 0;
		out.render_layer_definition_ids[i] = 0;
		// FL-4079: forward source_xp_index for the wearable proof probe.
		out.render_layer_source_xp_indices[i] = layer.source_xp_index;
		if (ActorVisualSemanticContributionSetContains(
				layer.semantic_contribution_set_index, "mount_body_wolf") ||
			ActorVisualSemanticContributionSetContains(
				layer.semantic_contribution_set_index, "bee_body"))
		{
			out.mount_layer_count++;
		}
		signature = ActorVisualProfileHashMix(
			signature, layer.semantic_contribution_set_index);
		signature = ActorVisualProfileHashMix(signature, layer.render_operation_mask);
	}
	out.loadout_signature = signature ? signature : 1u;
	uint8_t compose_failure = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
	const uint64_t compose_begin_us = a3dGetTime();
	out.sprite = LoadActorVisualProfileCellPayloadSprite(profile, &compose_failure);
	out.render_compose_us = (uint32_t)(a3dGetTime() - compose_begin_us);
	{
		ActorVisualProfileCacheStatsView cache_stats = {};
		GetActorVisualProfileCacheStats(&cache_stats);
		out.render_cache_hit_count = cache_stats.composed_cache_hit_count;
		out.render_cache_null_hit_count = cache_stats.composed_cache_null_hit_count;
			out.render_cache_miss_count = cache_stats.composed_cache_miss_count;
			out.render_cache_full_count = cache_stats.composed_cache_full_count;
			out.render_cache_failure_count = cache_stats.composed_cache_failure_count;
			out.render_row_lookup_cache_hit_count = cache_stats.row_lookup_cache_hit_count;
			out.render_row_lookup_cache_null_hit_count = cache_stats.row_lookup_cache_null_hit_count;
			out.render_row_lookup_cache_miss_count = cache_stats.row_lookup_cache_miss_count;
			out.render_row_lookup_table_scan_count = cache_stats.row_lookup_table_scan_count;
		}
	out.selector_failure_reason = compose_failure;
	out.compose_failure_stage = compose_failure;
	if (!out.sprite && compose_failure == ACTOR_VISUAL_PROFILE_FAILURE_NONE)
	{
		out.selector_failure_reason =
			ACTOR_VISUAL_PROFILE_FAILURE_SPRITE_COMPOSE_FAILED;
		out.compose_failure_stage =
			ACTOR_VISUAL_PROFILE_FAILURE_SPRITE_COMPOSE_FAILED;
	}
	if (out.sprite && out.sprite->anims > 0 && out.sprite->anim[0].length > 0)
	{
		// FL-4058 / Q1 ratchet: anim-track selection is row-owned via the
		// compiler-resolved locomotion_anim_track table. Runtime must fail the
		// sample instead of substituting a default track for invalid state.
		const uint8_t ls = (uint8_t)input.locomotion_state;
		if (ls >= ACTOR_VISUAL_LOCOMOTION_STATE_COUNT)
		{
			out.selector_failure_reason =
				ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING;
			out.compose_failure_stage =
				ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING;
			return out;
		}
		const uint8_t selected_anim = profile->locomotion_anim_track[ls];
		if ((int)selected_anim >= out.sprite->anims)
		{
			out.selector_failure_reason =
				ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING;
			out.compose_failure_stage =
				ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING;
			return out;
		}
		out.anim = (int)selected_anim;
		out.selected_locomotion_anim_track = selected_anim; // FL-4079
		out.anim_length = out.sprite->anim[selected_anim].length;
		const uint32_t elapsed =
			(input.authoritative_tick >= input.presentation_started_tick)
				? (input.authoritative_tick - input.presentation_started_tick)
				: 0;
		out.playback_elapsed_ticks = elapsed;
		// FL-3993: per-row content-driven frame compute. No kind branches here.
		// Compiler bakes (playback_mode, steady_frame_index) onto every profile
		// row from authored presentation_kind_playback metadata.
		if (!IsKnownPlaybackDirection(profile->playback_mode))
		{
			out.selector_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING;
			out.compose_failure_stage = ACTOR_VISUAL_PROFILE_FAILURE_PLAYBACK_METADATA_MISSING;
		}
		else
		{
			const uint8_t playback_mode = profile->playback_mode;
			const uint32_t steady_frame = (uint32_t)profile->steady_frame_index;
			const uint32_t anim_len = (uint32_t)out.anim_length;
			out.frame = (int)ComputeFrameByPlayback(playback_mode, elapsed, anim_len, steady_frame);
			out.playback_frame_clamped =
				IsPlaybackFrameClamped(playback_mode, elapsed, anim_len) ? 1 : 0;
			out.playback_frame_changed_expected =
				IsPlaybackFrameChangingExpected(playback_mode, elapsed, anim_len) ? 1 : 0;
		}
	}
		return out;
	}

static inline int DebugActorVisualProfileSpriteFamilyKind(const Sprite*)
{
	return 0;
}

static inline uint32_t DebugActorVisualProfilePresentationKey(const Sprite*)
{
	return 0;
}

static inline bool AppearanceRuntimeLifeStateKnown(uint8_t life_state)
{
	return life_state < LIFE_STATE::SIZE;
}

static inline bool AppearanceRuntimeLocomotionStateKnown(uint8_t locomotion_state)
{
	return locomotion_state < LOCOMOTION_STATE::SIZE;
}

static inline bool AppearanceRuntimeCombatStateKnown(uint8_t combat_state)
{
	return combat_state < COMBAT_STATE::SIZE;
}

static inline bool AppearanceRuntimePresentationKindKnown(uint16_t presentation_kind_id)
{
	for (int i = 0; i < kCompiledActorVisualRowCount; i++)
	{
		if (kCompiledActorVisualRows[i].key.presentation_kind_id == presentation_kind_id)
			return true;
	}
	return false;
}
