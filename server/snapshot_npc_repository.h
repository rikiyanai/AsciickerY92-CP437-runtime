#pragma once

#include <stdint.h>
#include "actor_visual_profile.h"

struct Sprite;
struct Inst;

// NPC snapshot state extracted from Server.
struct ServerSnapshotNpcRepository
{
	enum { MAX_SNAPSHOT_NPCS = 64 };

	struct SnapshotNpcState
	{
		uint16_t entity_id;
		uint8_t life_state;
		uint8_t mount_state;
		uint8_t locomotion_state;
		uint8_t combat_state;
		uint16_t presentation_kind_id;
		float pos[3];
		float dir;
		int16_t hp;
		int16_t max_hp;
		AppearanceStateV2 appearance_v2;
		uint16_t state_flags;
		uint32_t last_authoritative_tick;
		uint32_t presentation_started_tick;
	};

	struct SnapshotNpcAppearanceCache
	{
		uint16_t entity_id;
		AppearanceStateV2 appearance_v2;
	};

	struct SnapshotNpcVisual
	{
		uint16_t entity_id;
		uint16_t presentation_kind_id;
		uint32_t presentation_started_tick;
		Sprite* sprite;
		Inst* inst;
		uint8_t sprite_miss_frames;
		uint8_t selector_failure_reason;
		uint8_t last_inst_delete_reason;
		uint8_t last_inst_delete_miss_frames;
		uint16_t inst_create_count;
		uint16_t inst_delete_count;
		int16_t hp;
		int16_t max_hp;
	};

	uint16_t npc_count = 0;
	uint32_t npc_tick = 0;
	SnapshotNpcState npcs[MAX_SNAPSHOT_NPCS]{};
	SnapshotNpcAppearanceCache appearance_cache[MAX_SNAPSHOT_NPCS]{};
	SnapshotNpcVisual visuals[MAX_SNAPSHOT_NPCS]{};
};
