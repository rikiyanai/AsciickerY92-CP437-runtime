#pragma once

#include <stdint.h>

struct ItemProto;
struct Item;

Item* CreateItem();
void DestroyItem(Item* item);

// Grid-based inventory system with bitmask collision detection
// Grid: 8 cells wide x 20 cells tall = 160 cells (each cell = 4x4 pixels)
// Items occupy rectangular regions based on sprite size (sprite_width/4 x sprite_height/4)
struct Inventory
{
	// max inventory dims as 4x4 cells blocks (incl. 1 border)
	static const int width = 8;   // fit upto 4 7x7 cells items - do not modify!!!
	static const int height = 20; // fit upto 10 7x7 cells items
	static const int max_items = width*height; // please clamp to 100 items

	// Scroll animation state
	bool animate_scroll;  // WHY: Trigger smooth scroll animation on item add/remove/focus
	int scroll, smooth_scroll; // WHY: Current scroll position and animation source

	// Focused item index for keyboard navigation (arrow keys call FocusNext)
	int focus; // WHY: Currently selected item, -1 if none

	// layout (viewport dimensions and positioning)
	int layout_width;       // WHY: Inventory panel width in characters (fixed at 39)
	int layout_height;      // WHY: Visible height (clamped between sprite height and max)
	int layout_max_height;  // WHY: Maximum height = 7 + 4*20+1 + 5 = 93 chars
	int layout_max_scroll;  // WHY: Scroll clamp = max_height - visible_height
	int layout_reps[3];     // WHY: Vertical tiling repetitions for sprite expansion
	int layout_x;           // WHY: Screen X position (left side, shifts with camera)
	int layout_y;           // WHY: Screen Y position (vertically centered)
	int layout_frame[4];    // WHY: Inner grid bounds [x0,y0,x1,y1] for item rendering

	// free space lookup accelerator (bitmask collision detection)
	// WHY bitmask: O(1) collision check for item placement vs O(n) linear scan
	// 160 cells / 8 bits per byte = 20 bytes total
	// Cell i maps to: byte (i>>3), bit (i&7)
	uint8_t bitmask[(max_items+7)/8];

	// Item slot in player's inventory (grid position + metadata)
	struct MyItem
	{
		Item* item;     // WHY: Pointer to Item struct (proto, inst, purpose)
		int xy[2];      // WHY: Grid position [x, y] in cells (0-7, 0-19)
		int story_id;   // WHY: Quest/story identifier for scripting

		bool in_use;    // WHY: Is item equipped (weapon/shield/helmet/armor)
		char desc[32];  // WHY: Custom description (32*max_items = 5KB total)
	};

	int my_items;       // WHY: Current number of items in inventory (0 to max_items)
	MyItem my_item[max_items]; // WHY: Array of owned items

	void UpdateLayout(int width, int height, int scene_shift, int bars_pos);

	bool InsertItem(Item* item, int xy[2], const char* desc=0, const int* story_id=0); 
	bool RemoveItem(int index, float pos[3], float yaw);

	void FocusNext(int dx, int dy);
	void SetFocus(int index);
};

// Item prototype (template) loaded from items.txt file
// WHY: Defines item gameplay type and properties (all instances share same proto)
struct ItemProto // loaded from items.txt file
{
	// Item category for stacking rules and equip validation
	// 'W'=Weapon, 'S'=Shield, 'H'=Helmet, 'A'=Armor, 'R'=Ring, 'B'=Brace, 'N'=Necklace
	// 'P'=Potion, 'F'=Food, 'D'=Drink, 'C'=Consumable
	// WHY kind: Determines gameplay category, stacking, and consume behavior
	int kind; // 'W'eapon, 'S'hield, 'H'elmet, 'A'rmor, 'R'ing, ...

	// Sub-type within category (weapon type, potion color, food type)
	// WHY sub_kind: Allows multiple gameplay variants per category (sword vs crossbow vs mace)
	int sub_kind; // (ie: for kind=='W' sub_kind==1 is sword, for kind=='P' sub_kind==1 is hp_potion )

	// use command:
	// Server-authoritative multiplayer resolves equip/loadout visuals through bundle item definitions.
	// Local legacy item categories remain gameplay metadata for non-network inventory behavior.
	// WHY kind=R/B/N: Accepted but no visual change (stats-only equipment)
	// WHY kind=C: Destroyed on use (RemoveItem with pos=0)

	int weight; // WHY: Item weight for inventory management (unused currently)

	const char* desc; // WHY: Default description string (can be overridden per item)

	// Visual style lane for deterministic art selection within the same gameplay kind/sub_kind.
	// Bundle V2 (2026-05-10 audit): visual_style_id (default/gold/dark) is data-driven
	// in the manifest. FL-903 (old SkinFamilyDefinition system) is partially resolved —
	// wearables/items ARE data-driven now, but gold/dark layers remain unpopulated (FL-3846).
	// Originally: WHY style: FL-903 needs world/inventory wearable art to choose DEFAULT/GOLD/DARK
	int visual_style; // 0=default, 1=gold, 2=dark

	// extras ?
	// ...
};

// Item instance (actual item in world or inventory)
// WHY: Runtime item with bundle-owned visual identity, ownership state,
// world instance, and stack count.
struct Item
{
	Inst* inst;  // EDIT / WORLD : instance (OWNED has it NULL)
	             // WHY: Pointer to world instance when attached to BSP tree (visible in world)
	             // WHY NULL when OWNED: Item in inventory, detached from BSP, not rendered in world

	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t presentation_kind_id;

	int count; // WHY: Stack count for stackable items (future feature, currently unused)

	// item instances:
	// - if process is editor: private items for editor (saved/loaded with a3d file)
	// - items for players (just 1 clone from a3d items for all players execpt below)
	// - items created for each player inventory individually (if a3d item has seed flag)

	// Ownership lifecycle state (transitions during pickup/drop/consume)
	// WHY PURPOSE: Tracks where item exists (editor, world, inventory) for rendering and BSP management
	enum PURPOSE
	{
		// when created, item purpose is unspecified
		UNSPECIFIED = 0,

		// EDITOR / FILE only,
		// render in editor only
		// note: loding world by game (without editor) will switch it immediately to WORLD
		//       loading by editor will make this item clone (with WORLD , available to test-players)
		// item must be attached to BHV (for editor)
		// WHY EDIT: Level design items, saved in .a3d files, inst attached to BSP
		EDIT = 1,

		// game item, inside someone's inventory
		// dont't render in game (owner's inventory only)
		// item must be detached from BHV (travels with player) inst=0
		// WHY OWNED: In player/NPC inventory, not visible in world, inst=0
		// Transition: WORLD → OWNED (InsertItem), OWNED → WORLD (RemoveItem), OWNED → destroyed (consume)
		OWNED = 2, // not savable

		// game item, no owner, render it in game for all players
		// item must be attached to BHV (for players)
		// WHY WORLD: Lying on ground, visible to all players, inst attached to BSP
		// Transition: EDIT → WORLD (game load), WORLD → OWNED (pickup), OWNED → WORLD (drop)
		WORLD = 3, // not savable
	};

	// note: changing purpose may need adjusting World::insts counter!
	PURPOSE purpose; // WHY: Current ownership state (EDIT/WORLD/OWNED lifecycle)
};

// Consumable item indices (destroyed on use via RemoveItem with pos=0)
// WHY: kind='F' (food), consumed to restore health/stamina
enum PLAYER_FOOD_INDEX
{
	FOOD_NONE = 0,
	MEAT,      // WHY: kind='F', sub_kind=1
	EGG,       // WHY: kind='F', sub_kind=2
	CHEESE,    // WHY: kind='F', sub_kind=3
	BREAD,     // WHY: kind='F', sub_kind=4
	BEET,      // WHY: kind='F', sub_kind=5
	CUCUMBER,  // WHY: kind='F', sub_kind=6
	CARROT,    // WHY: kind='F', sub_kind=7
	APPLE,     // WHY: kind='F', sub_kind=8
	CHERRY,    // WHY: kind='F', sub_kind=9
	PLUM,      // WHY: kind='F', sub_kind=10
};

// WHY: kind='D' (drink), consumed to restore thirst/mana
enum PLAYER_DRINK_INDEX
{
	DRINK_NONE = 0,
	MILK,      // WHY: kind='D', sub_kind=1
	WATER,     // WHY: kind='D', sub_kind=2
	WINE       // WHY: kind='D', sub_kind=3
};

// WHY: kind='P' (potion), consumed for instant stat boost (destroyed on use)
enum PLAYER_POTION_INDEX
{
	POTION_NONE = 0,
	POTION_RED,   // HP  // WHY: kind='P', sub_kind=1 (health)
	POTION_BLUE,  // MP  // WHY: kind='P', sub_kind=2 (mana)
	POTION_GREEN,       // WHY: kind='P', sub_kind=3
	POTION_PINK,        // WHY: kind='P', sub_kind=4
	POTION_CYAN,        // WHY: kind='P', sub_kind=5
	POTION_GOLD,        // WHY: kind='P', sub_kind=6
	POTION_GREY         // WHY: kind='P', sub_kind=7
};

// Non-consumable accessories (no sprite change, stats-only)
// WHY: kind='R' (ring), accepted but no player sprite modification
enum PLAYER_RING_INDEX
{
	RING_WHITE,  // WHY: kind='R', sub_kind=1
	RING_CYAN,   // WHY: kind='R', sub_kind=2
	RING_GOLD,   // WHY: kind='R', sub_kind=3
	RING_PINK    // WHY: kind='R', sub_kind=4
};
