//==============================================================================
// inventory.cpp - Grid-Based Inventory System with Directional Navigation
//==============================================================================
//
// PURPOSE:
//   Implements an 8x20 cell grid-based inventory system for variable-sized items
//   with bitmask collision detection, directional focus navigation, item stacking,
//   ownership transfer, and viewport scrolling. Items occupy rectangular regions
//   in a 2D grid with O(1) collision checks for placement validation.
//
// GRID LAYOUT:
//   - Dimensions: 8 cells wide x 20 cells tall = 160 cells total
//   - Cell size: 4x4 pixels (item sprites divided by 4 to get cell dimensions)
//   - Items occupy rectangular regions: sprite_width/4 x sprite_height/4 cells
//   - Example: 12x8 sprite occupies 3x2 cells
//   - Coordinate system: (0,0) at top-left, x increases right, y increases down
//
//   Grid coordinate system:
//     x →
//   y ┌─┬─┬─┬─┬─┬─┬─┬─┐  width = 8
//   ↓ ├─┼─┼─┼─┼─┼─┼─┼─┤
//     ├─┼─┼─┼─┼─┼─┼─┼─┤  height = 20
//     │ │ │ │ │ │ │ │ │  (20 rows shown truncated)
//     └─┴─┴─┴─┴─┴─┴─┴─┘
//
// BITMASK COLLISION DETECTION:
//   - Occupancy tracking: uint8_t bitmask[20] stores 160 bits (8 cells * 20 rows)
//   - Each cell corresponds to one bit: occupied = 1, free = 0
//   - Cell index: i = x + y*width (linear index from 0 to 159)
//   - Bit position: byte (i>>3), bit within byte (i&7)
//   - WHY bitmask: O(1) collision check vs O(n) linear scan of my_items[]
//   - WHY 20 bytes: 160 cells / 8 bits per byte = 20 bytes total
//
//   Bitmask bit layout for 8x20 grid:
//     bitmask[0]: bits 0-7   = cells (0,0) through (7,0)
//     bitmask[1]: bits 8-15  = cells (0,1) through (7,1)
//     ...
//     bitmask[19]: bits 152-159 = cells (0,19) through (7,19)
//
//   Set bit:   bitmask[i>>3] |= 1<<(i&7)    // mark cell as occupied
//   Clear bit: bitmask[i>>3] &= ~(1<<(i&7)) // mark cell as free
//   Test bit:  bitmask[i>>3] & (1<<(i&7))   // check if occupied
//
// DIRECTIONAL FOCUS NAVIGATION (FocusNext Algorithm):
//   Finds the "best" item to focus when user presses arrow keys (directional movement).
//   Uses distance-weighted scoring with perpendicular penalty to prefer aligned items.
//
//   Algorithm overview:
//     1. Major axis determination: major_x = (dx*dx > dy*dy)
//        - Horizontal movement (left/right): major_x = true
//        - Vertical movement (up/down): major_x = false
//        - WHY: determines primary direction and which distance component to emphasize
//
//     2. Focus point calculation: (fx, fy) = current item's LEADING EDGE
//        - Horizontal: fx = edge in direction of dx, fy = center
//        - Vertical: fx = center, fy = edge in direction of dy
//        - WHY doubled coordinates: sub-cell precision without floating point
//
//     3. Candidate proximity calculation: (px, py) = closest point on candidate
//        - px = closest x coordinate on candidate edge to focus point
//        - py = closest y coordinate on candidate edge to focus point
//        - WHY edge-to-edge: measures shortest distance between items
//
//     4. Dot product rejection: vx*dx + vy*dy >= 0
//        - vx = px - fx, vy = py - fy (vector from focus to candidate)
//        - WHY: geometric test rejects candidates BEHIND direction (angle > 90°)
//
//     5. Distance scoring formula:
//        - Horizontal (major_x): e = vx^2 + 4*vy^2 + cy^2
//        - Vertical (!major_x): e = 4*vx^2 + vy^2 + cx^2
//        - WHY squared distances: avoids sqrt(), integer math, penalizes large distances more
//        - WHY 4x perpendicular penalty: strongly prefers aligned items over diagonal
//        - WHY cy^2/cx^2 tie-breaker: when two items at same primary distance,
//          prefer the one whose center is closer to our focus line
//
//   Example: Pressing RIGHT arrow (dx=1, dy=0) from item at (1,5):
//     - major_x = true (horizontal movement)
//     - Focus point: fx = right edge of current item, fy = center
//     - Candidates: items to the right (vx*dx >= 0)
//     - Item at (5,5): vx=large, vy=0 → e = vx^2 (aligned, good score)
//     - Item at (5,8): vx=large, vy=3 → e = vx^2 + 4*9 = vx^2 + 36 (diagonal, worse score)
//     - WHY 4x penalty: item at (5,5) is 36 units "cheaper" than (5,8)
//
// ITEM OWNERSHIP MODEL (Item::PURPOSE lifecycle):
//   Items transition through three ownership states during gameplay:
//
//   EDIT (editor-only):
//     - Purpose: Level design, saved in .a3d files
//     - Location: Attached to BSP tree (inst != 0)
//     - Transition: When game loads, EDIT → WORLD
//
//   WORLD (in-game, no owner):
//     - Purpose: Items lying on ground, available for pickup
//     - Location: Attached to BSP tree (inst != 0)
//     - Transition: Player picks up → OWNED (InsertItem)
//
//   OWNED (in player/NPC inventory):
//     - Purpose: Items in inventory, not rendered in world
//     - Location: Detached from BSP tree (inst = 0), stored in Inventory::my_item[]
//     - Transition: Player drops → WORLD (RemoveItem), consumable eaten → destroyed
//
//   Ownership transfer examples:
//     - Pickup from world: WORLD → OWNED (InsertItem deletes inst, sets purpose)
//     - Pickup from corpse: OWNED (NPC) → OWNED (player) (transfer between inventories)
//     - Drop to world: OWNED → WORLD (RemoveItem creates inst, attaches to BSP)
//     - Consume item: OWNED → destroyed (RemoveItem with pos=0)
//
// ITEM STACKING RULES:
//   Based on ItemProto::kind character ('W', 'S', 'H', 'A', etc.):
//     'W' = Weapon (sword, crossbow, mace, hammer, axe, flail)
//     'S' = Shield (normal shield)
//     'H' = Helmet (normal helmet)
//     'A' = Armor (normal armor)
//     'R' = Ring (white, cyan, gold, pink)
//     'B' = Brace/Bracelet
//     'N' = Necklace
//     'P' = Potion (HP, MP, green, pink, cyan, gold, grey)
//     'F' = Food (meat, egg, cheese, bread, vegetables, fruits)
//     'D' = Drink (milk, water, wine)
//     'C' = Consumable (destroyed on use)
//
//   Stacking behavior:
//     - Weapons/shields/helmets/armor: Equippable gameplay categories; multiplayer
//       visual loadout is resolved through server-owned bundle item definitions.
//     - Rings/braces/necklaces: Accepted but no sprite change
//     - Consumables: Destroyed on use (RemoveItem with pos=0)
//     - WHY kind-based: Enables local inventory gameplay validation
//
// VIEWPORT SCROLLING:
//   - scroll: Current scroll position (pixels)
//   - smooth_scroll: Animation source for smooth scrolling
//   - animate_scroll: Flag to trigger scroll animation
//   - layout_max_scroll: Maximum scroll value (clamped to prevent over-scroll)
//   - WHY animated: Smooth visual feedback when items added/removed or focus changes
//
// KEY DATA STRUCTURES:
//   Inventory::MyItem[] - Array of owned items with grid positions (xy[2])
//   bitmask[] - 160-bit occupancy map for collision detection
//   focus - Index of currently focused item for keyboard navigation
//   layout_* - Viewport dimensions and scroll state
//
// KEY FUNCTIONS:
//   CreateItem()     - Allocate and zero-initialize Item struct
//   DestroyItem()    - Free Item and delete world instance if attached
//   InsertItem()     - Transfer item from world/corpse to inventory, update bitmask
//   RemoveItem()     - Drop item to world or destroy, update bitmask
//   FocusNext()      - Directional navigation (arrow keys), find best next item
//   SetFocus()       - Change focused item index
//   UpdateLayout()   - Recalculate viewport dimensions and scroll bounds
//
// INTEGRATION POINTS:
//   - game.cpp: Calls InsertItem for item pickup, RemoveItem for drop/consume,
//               FocusNext for arrow key navigation (lines 8230-8239, 10270-10326)
//   - inventory.h: Inventory struct, Item/ItemProto structs, equipment enums
//   - game.h: Character and Human/NPC types for ownership transfer
//   - world.h: CreateInst/DeleteInst/AttachInst for world item management
//
// KEY FILES:
//   inventory.cpp - This file (grid operations, navigation, ownership)
//   inventory.h   - Data structures (Inventory, Item, ItemProto, equipment enums)
//   game.cpp      - Integration (pickup/drop logic, input handling)
//   game.h        - Character types for ownership transfer
//   world.h       - BSP tree operations for world items
//
//==============================================================================

#include "game.h"
#include "a3d_load_context.h"
#include "sprite_registry.h"
#include <stdlib.h>
#include <string.h>

// [FLOW:ENTITY] [DATA-CONTRACT:ENTITY] Item creation
// WHY: Creates a new Item instance (zero-initialized).
// Items are the fundamental unit of the inventory system and can exist in three states:
//   - EDIT: Editor-only, saved in .a3d files
//   - WORLD: In-game, lying on ground (inst attached to BSP tree)
//   - OWNED: In player/NPC inventory (inst = 0, stored in Inventory::my_item[])
// WHY zero-init: Ensures all fields (bundle ids, inst, count, purpose) start in known state
Item* CreateItem()
{
	Item* item = (Item*)malloc(sizeof(Item));
	memset(item, 0, sizeof(Item));
	return item;
}

// [FLOW:ENTITY] Item destruction
// WHY: Frees Item memory and deletes world instance if attached
// Used when: Item consumed (potion, food), or removed from world permanently
// Note: inst can be non-null for WORLD items, must be deleted first to avoid leak
void DestroyItem(Item* item)
{
	if (item->inst)
		DeleteInst(item->inst); // Remove from BSP tree if world item
	free(item);
}

// [FLOW:ENTITY] Viewport layout recalculation
// WHY: Recalculates inventory viewport dimensions and scroll bounds based on window size
// Called when: Window resized, inventory opened, or UI bars position changes
// Viewport calculations:
//   - layout_width: Fixed at 39 chars (inventory sprite frame width)
//   - layout_max_height: Maximum height = 7 (top border) + 4*20+1 (grid) + 5 (bottom) = 93 chars
//   - layout_height: Actual height, clamped between sprite height and layout_max_height
//   - layout_max_scroll: How much we can scroll = max_height - visible_height
// WHY three-region vertical tiling: Inventory sprite has 3 vertically tileable regions,
//   diff distributed evenly to make smooth expansion
// WHY layout_frame[]: Inner grid bounds (excludes border) for item rendering
void Inventory::UpdateLayout(int render_width, int render_height, int scene_shift, int bars_pos)
{
	// WHY descent: Lower inventory when health/status bars visible at bottom
	int descent = bars_pos - 5;
	if (descent < 0)
		descent = 0;
	Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;
	layout_width = 39; // Fixed width in characters
	layout_max_height = 7 + 4*height+1 + 5; // 7 (top) + 81 (grid at 4px/cell) + 5 (bottom) = 93
	layout_height = render_height - 4 - descent;
	if (layout_height > layout_max_height)
		layout_height = layout_max_height; // Clamp to max
	if (layout_height < sf->height)
		layout_height = sf->height; // Clamp to min (sprite height)
	int diff = layout_height - sf->height; // Extra space to distribute

	// WHY three regions: Inventory sprite has 3 vertically tileable regions
	// Distribute extra rows evenly across regions for smooth expansion
	int dy = diff / 3;
	diff -= 3 * dy; // Remainder after even distribution

	for (int r = 0; r < 3; r++)
	{
		if (r < diff)
			layout_reps[r] = 2 + dy; // First 'diff' regions get extra row
		else
			layout_reps[r] = 1 + dy; // Remaining regions get base rows
	}

	// WHY max_scroll: Clamps scroll to prevent showing past bottom of grid
	layout_max_scroll = layout_max_height - layout_height;

	// WHY scene_shift: Inventory appears on left side, shifts with camera
	layout_x = scene_shift - layout_width;
	layout_y = (render_height - 4 - descent - layout_height) / 2; // Vertically centered

	// Inner grid bounds (excludes 3-char left border, 7-char top, 6-char right/bottom)
	layout_frame[0] = layout_x + 3; // Left edge of grid
	layout_frame[1] = layout_y + 7; // Top edge of grid
	layout_frame[2] = layout_x + 3 + width * 4; // Right edge (8 cells * 4 chars)
	layout_frame[3] = layout_y + layout_height - 6; // Bottom edge
}

// [FLOW:ENTITY] Directional focus navigation (arrow key movement)
// WHY: Finds the "best" item to focus when user presses arrow keys
// This is the most algorithmically complex function in the inventory system.
// Uses edge-to-edge distance measurement with 4x perpendicular penalty to strongly
// prefer aligned items over diagonal candidates.
//
// ALGORITHM DERIVATION:
//
// Problem: Given current focused item and direction (dx, dy), find next item to focus
// Goals: (1) Must be ahead in direction, (2) Prefer aligned over diagonal, (3) Break ties
//
// Step 1: Determine major axis
//   major_x = (dx*dx > dy*dy)
//   WHY: Identifies primary direction (horizontal vs vertical movement)
//   WHY squared comparison: Works for all dx/dy combinations, avoids abs(), avoids sqrt()
//   Example: Right arrow (dx=1, dy=0): dx*dx=1 > dy*dy=0 → major_x=true
//   Example: Down arrow (dx=0, dy=1): dx*dx=0 < dy*dy=1 → major_x=false
//
// Step 2: Calculate focus point (fx, fy) on current item's LEADING EDGE
//   Horizontal (major_x=true):
//     fx = 2*(dx>0 ? x1 : x0)  // Right edge if moving right, left edge if moving left
//     fy = y0+y1               // Vertical center (sum, not average, because doubled)
//   Vertical (major_x=false):
//     fx = x0+x1               // Horizontal center
//     fy = 2*(dy>0 ? y1 : y0)  // Bottom edge if moving down, top edge if moving up
//   WHY doubled coordinates: Sub-cell precision without floating point (2x scale)
//   WHY leading edge: Measure from edge in direction of movement (shortest distance)
//   Example: Moving right from item at (2,3)-(5,6): fx = 2*5 = 10, fy = 3+6 = 9
//
// Step 3: For each candidate item, calculate proximity point (px, py)
//   This is the point on the candidate's EDGE closest to our focus point.
//   Horizontal (major_x=true):
//     px = 2*(dx>0 ? x0 : x1)  // Left edge if we're moving right (closest to us)
//     py = clamp(fy, 2*y0, 2*y1)  // Closest vertical point to our focus line
//   Vertical (major_x=false):
//     px = clamp(fx, 2*x0, 2*x1)  // Closest horizontal point
//     py = 2*(dy>0 ? y0 : y1)  // Top edge if we're moving down
//   WHY clamp: If our focus line is above/below candidate, clamp to candidate bounds
//   Example: Focus fy=9, candidate y range [4,8]: py = clamp(9, 8, 16) = 16
//
// Step 4: Calculate center point (cx, cy) for tie-breaking
//   cx = candidate center x, clamped to our item's horizontal extent
//   cy = candidate center y, clamped to our item's vertical extent
//   WHY: When two items are same primary distance, prefer one whose center is closer
//   to our focus line (more "aligned" with our direction)
//
// Step 5: Dot product rejection test
//   vx = px - fx, vy = py - fy  // Vector from focus point to proximity point
//   if (vx*dx + vy*dy >= 0) → candidate is AHEAD
//   WHY: Geometric dot product test rejects candidates BEHIND direction vector
//   Math: dot product = |v||d|cos(θ), if θ > 90°, cos(θ) < 0, so dot < 0
//   Example: Moving right (dx=1, dy=0), candidate to left: vx<0, vx*1 < 0 → rejected
//
// Step 6: Distance scoring formula (lower = better)
//   Horizontal (major_x=true):
//     e = vx^2 + 4*vy^2 + cy^2
//   Vertical (major_x=false):
//     e = 4*vx^2 + vy^2 + cx^2
//   WHY squared distances: Avoids sqrt(), uses integer math, penalizes large distances more
//   WHY 4x perpendicular penalty: Strongly prefers aligned items over diagonal
//   WHY cy^2/cx^2 tie-breaker: When two items at same primary distance, prefer one
//     whose center is closer to our focus line
//
// CONCRETE EXAMPLE: Moving RIGHT from item at (1,5)-(3,7)
//   focus point: fx = 2*3 = 6, fy = 5+7 = 12
//
//   Candidate A at (5,5)-(7,7) (perfectly aligned):
//     px = 2*5 = 10, py = clamp(12, 10, 14) = 12
//     vx = 10-6 = 4, vy = 12-12 = 0
//     e = 4^2 + 4*0^2 + 0^2 = 16
//
//   Candidate B at (5,9)-(7,11) (diagonal, 4 cells below):
//     px = 2*5 = 10, py = clamp(12, 18, 22) = 18
//     vx = 10-6 = 4, vy = 18-12 = 6
//     cy = clamp(9+11=20, 10, 14) = 14, cy-fy = 14-12 = 2
//     e = 4^2 + 4*6^2 + 2^2 = 16 + 144 + 4 = 164
//
//   Result: Candidate A (e=16) is strongly preferred over B (e=164)
//   WHY 4x penalty works: Without it, e_B = 16+36+4 = 56, only 3.5x worse.
//   With 4x penalty, e_B = 164, which is 10x worse → much clearer preference.
//
void Inventory::FocusNext(int dx, int dy)
{
	(void)dx;
	(void)dy;
	abort();
}

// [FLOW:ENTITY] Focus change
// WHY: Changes currently focused item index for keyboard navigation
// Called by: FocusNext (directional navigation), InsertItem/RemoveItem (auto-focus),
//            mouse click on item, gamepad selection
// Note: Does not trigger scroll animation (caller must set animate_scroll if desired)
void Inventory::SetFocus(int index)
{
	focus = index;
}

// [FLOW:ENTITY] Item insertion -- transfer from world/corpse to player inventory
// WHY: Handles item pickup from world or looting from NPC corpses, transfers ownership
// Ownership transitions:
//   - WORLD → OWNED: Pickup from ground (DeleteInst, detach from BSP)
//   - OWNED (NPC) → OWNED (player): Loot from corpse (transfer between inventories)
// Returns: true on success, false if inventory full (my_items >= max_items)
// Side effects: Updates bitmask, sets focus to new item, triggers scroll animation
bool Inventory::InsertItem(Item* item, int xy[2], const char* desc, const int* story_id)
{
	(void)item;
	(void)xy;
	(void)desc;
	(void)story_id;
	abort();
}

// [FLOW:ENTITY] Item removal -- drop to world or destroy (consume)
// WHY: Handles item drops from inventory back to world, or item consumption (potions, food)
// Ownership transitions:
//   - OWNED → WORLD: Drop to ground (CreateInst, attach to BSP, pos != 0)
//   - OWNED → destroyed: Consume item (DestroyItem, pos = 0)
// Params:
//   index: Item index in my_items[] to remove
//   pos: World position to drop at (NULL = consume/destroy)
//   yaw: Rotation angle for dropped item
// Side effects: Updates bitmask, adjusts focus, triggers scroll animation
bool Inventory::RemoveItem(int index, float pos[3], float yaw)
{
	(void)index;
	(void)pos;
	(void)yaw;
	abort();
}
