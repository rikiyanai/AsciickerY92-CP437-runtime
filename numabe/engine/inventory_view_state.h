#pragma once

// inventory_view_state.h — Inventory UI/interaction view state
//
// PURPOSE:
// Extends Inventory with client-side view state: mobile auto-combat/pickup
// intents, consume animations, world item pickup strip, and interaction
// query results. Extracted from game.h.

#include <stdint.h>

#include "inventory.h"
#include "interaction_query.h"

struct Sprite;
struct Item;

namespace MOBILE_AUTO_COMBAT_STATE
{
	enum
	{
		NONE = 0,
		PENDING_USE = 1,
		ARMED = 2,
		PAUSED = 3,
	};
}

struct InventoryViewState : Inventory
{
	struct ConsumeAnim
	{
		int pos[2];
		Sprite* sprite;
		uint64_t stamp;
	};

	int authoritative_inventory_focus;
	uint16_t mobile_auto_combat_item_id;
	uint64_t mobile_auto_combat_stamp;
	uint8_t mobile_auto_combat_state; // UI intent only; local auto-submit phase, never gameplay authority
	uint16_t mobile_auto_pickup_item_id;
	uint64_t mobile_auto_pickup_stamp;
	uint64_t mobile_player_tap_stamp;
	int consume_anims;
	ConsumeAnim consume_anim[16];

	// World item pickup strip state (formerly inline on Game)
	InteractionQueryResult interaction_query_result;
	Item** items_inrange;
	int items_count;
	int items_xarr[10];
	int items_ylo;
	int items_yhi;
};
