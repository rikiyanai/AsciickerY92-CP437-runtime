#pragma once

#include <stdint.h>
#include "authoritative_item_state.h"

struct Inst;

// Authoritative item/server state extracted from Server.
struct AuthoritativeItemServerState
{
	enum { MAX_AUTHORITATIVE_ITEMS = 128 };
	using ItemState = ::AuthoritativeItemState;

	// Server-authoritative observability: item/world-item mutation events.
	uint32_t item_event_packets = 0;
	uint32_t last_item_event_id = 0;
	uint32_t last_item_event_tick = 0;
	uint32_t item_event_applied_packets = 0;

	uint16_t item_count = 0;
	uint16_t item_world_count = 0;
	uint16_t item_local_owned_count = 0;
	uint16_t item_local_ids[MAX_AUTHORITATIVE_ITEMS]{};

	uint32_t state_apply_packets = 0;
	uint32_t pick_blocked_packets = 0;
	uint32_t drop_blocked_packets = 0;

	uint32_t appearance_v2_packets = 0;
	uint32_t item_event_v2_packets = 0;
	uint16_t last_appearance_v2_entity_id = 0;
	uint8_t last_appearance_v2_entity_type = 0;
	uint16_t last_item_definition_id_v2 = 0;
	uint16_t last_item_visual_style_id_v2 = 0;

	ItemState items[MAX_AUTHORITATIVE_ITEMS]{};

	struct ItemVisual
	{
		uint16_t item_id;
		uint8_t visual_failure_reason;
		Inst* inst;
		// FL-4137 #69 (2026-05-31): mesh_inst is the AKM mesh world Inst for
		// items whose catalog row sets world_mesh_path (currently placeable
		// blocks). For those items the sprite Inst (vis->inst) is suppressed
		// on placed-world rows; only the mesh Inst is registered, so the
		// visible world block is an AKM mesh instance — not the XP sprite.
		// Held/inventory preview still uses vis->inst (sprite) because the
		// held preview is render presentation, not a placed world item.
		Inst* mesh_inst;
	};
	ItemVisual item_visuals[MAX_AUTHORITATIVE_ITEMS]{};

	// Server-authoritative observability: decal/world-mutation events.
	uint32_t decal_event_packets = 0;
	uint32_t last_decal_event_id = 0;
	uint32_t last_decal_event_tick = 0;
	uint32_t decal_event_applied_packets = 0;

	bool snapshot_stream_active = false;
};
