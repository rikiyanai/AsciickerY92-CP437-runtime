#pragma once

// item_owner.h — Mixin for NPCs that carry items
//
// PURPOSE:
// Base struct for any entity that owns inventory items (NPC_Creature,
// NPC_Human). Provides a fixed-size slot array with story IDs.
// Extracted from game.h.

struct Item;

struct ItemOwner
{
	// NPCs carrying items should inherit from it
	static const int max_items = 5;
	int items;
	struct
	{
		Item* item;
		int story_id;
		bool in_use;
	} has[max_items];
};
