#pragma once

#include <stdint.h>

#include "protocol/protocol_combat.h"
#include "protocol/protocol_common.h"

// Server-owned appearance catalog.
//
// This is deliberately not generated from ActorVisualProfile source.
// Gameplay item identity, mount identity, proof seats, and world/inventory
// sprite families live here so profile authoring cannot make a server-reachable
// key disappear or change a world item sprite by editing profiles.json.

// FL-4074: per-profile starter loadout. Server-owned defaults for every
// equipped slot the profile spawns with. Without these, players spawn
// naked and any CompiledActorVisualRow keyed on a non-zero slot id is
// unreachable until some external action (recipe, pickup, equip) sets
// the slot — which orphans authored rows (e.g. the 16 wolf+armour rows)
// and prevents headed proof from ever exercising them.
//
// item_instance_id=0 means "visual-only starter, no inventory linkage";
// state_flags=1 (SVR_APPEARANCE_ENTRY_STATE_EQUIPPED) is required so the
// slot participates in CompiledActorVisualKey construction.
struct AppearanceCatalogStarterEntry
{
	uint16_t slot_kind_id;
	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t state_flags;
};

enum AppearanceCatalogProfileReachability : uint8_t
{
	// Full player profiles enumerate every catalog-owned equipment, mount, and
	// presentation combination. Standalone authored sheets deliberately expose
	// only the exact runtime keys their source contract can render.
	APPEARANCE_PROFILE_REACHABILITY_FULL_PLAYER = 0,
	APPEARANCE_PROFILE_REACHABILITY_FIXED_PLAYER = 1,
	APPEARANCE_PROFILE_REACHABILITY_FIXED_COMPANION = 2,
};

struct AppearanceCatalogProfileDef
{
	uint16_t id;
	uint16_t skin_definition_id;
	const char* slug;
	const AppearanceCatalogStarterEntry* starter_entries;
	uint8_t starter_count;
	uint8_t reachability_policy;
};

struct AppearanceCatalogSeatDef
{
	const char* seat_alias;
	uint16_t appearance_profile_id;
};

// Wave 3 (FL-3726, FL-4076 followup): combat behavior facts move to the
// catalog so server/client never branch on a closed weapon-kind enum.
//
// swing_presentation_ticks: how long the server keeps the weapon's swing
//   presentation_kind active after a swing intent. 0 means "use the server
//   fallback for non-weapon/default melee".
// swing_range_units: server-authoritative reach for this weapon's sweep.
//   0 means "use the global SVR_SWING_RANGE default for melee".
// projectile_units_per_tick: positive float means this weapon spawns a
//   projectile on hit with this travel speed. 0 means no projectile.
// spawns_projectile_on_swing: 1 if the swing event publishes a projectile
//   visual; 0 otherwise. Derived data — must match (projectile_units_per_tick > 0).
// placeable: 1 if the item can be converted from owned inventory state to a
//   server-owned placed world item through ITEM_ACTION_REQ_PLACE.
// explicit_pickup_only: 1 if mobile autopick must skip the item while it is
//   world-owned; explicit tap/click pickup by item id remains valid.
// block_break_power: weapon power for tools/weapons and required break power
//   for placeable blocks.
struct AppearanceCatalogItemDef
{
	uint16_t id;
	uint16_t slot_kind_id;
	uint16_t mount_definition_id;
	uint16_t swing_presentation_kind_id;
	uint8_t gameplay_kind;
	uint8_t swing_presentation_ticks;
	uint8_t spawns_projectile_on_swing;
	float swing_range_units;
	float projectile_units_per_tick;
	uint8_t placeable;
	uint8_t explicit_pickup_only;
	uint8_t block_break_power;
	uint16_t placed_durability;
	float place_distance_units;
	float collision_radius_units;
	float collision_height_units;
	const char* slug;
	const char* world_sprite_path;
	const char* inventory_sprite_path;
	// FL-4137 #69 (2026-05-31): world_mesh_path is non-empty for items that
	// must render as an AKM mesh instance in the placed world (not as a 2D/3D
	// sprite). The XP sprite assets remain for inventory icon + held preview
	// only. Used by engine/authoritative_world_item_appearance.cpp to (a) skip
	// the placed-world sprite Inst and (b) register a World mesh Inst on the
	// authoritative placed position. Goal-text gate G3 ("real AKM mesh
	// instance, not sprite-only/proof-only/debug-only geometry") requires
	// placeable blocks to live on this path, not the sprite path.
	const char* world_mesh_path;
};

// Rig Contract (CONTEXT.MD:32, CONTEXT.MD:87): rig_id is catalog-owned data
// attached to the mount definition. Runtime/server never derives rig_id from
// mount presence in C++; the catalog declares it. rig_id == 0 is "no rig
// (default)"; nonzero values identify authored attachment contracts.
//
// rig_id == SVR_APPEARANCE_RIG_MOUNTED_RIDER_SEAM (1) means the rider/mount
// seam authored in ActorVisualProfile rows. Adding a new rig kind = adding a
// new compile-time constant + authored profile data, never a C++ branch.
struct AppearanceCatalogMountDef
{
	uint16_t id;
	uint8_t runtime_mount_state;
	uint16_t rig_id;
	const char* slug;
};

static constexpr uint16_t APPEARANCE_CATALOG_CONTRACT_VERSION = 2;
static constexpr uint16_t APPEARANCE_CATALOG_DEFAULT_PROFILE_ID = 200;
static constexpr uint16_t APPEARANCE_CATALOG_WALLACE_PROFILE_ID = 201;
static constexpr uint16_t APPEARANCE_CATALOG_GROMIT_PROFILE_ID = 202;

// Server catalog gameplay-kind vocabulary. These numeric ids must match
// server_state.h::SVR_ITEM_GAMEPLAY_* because ServerState persists the value
// on authoritative items. The catalog owns which item definition carries which
// gameplay kind; reachability code must reference these names instead of
// carrying a parallel local enum.
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_UNKNOWN = 0;
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_WEAPON = 1;
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_CONSUMABLE = 2;
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_LOOT = 3;
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_WEARABLE = 4;
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_MOUNTABLE = 5;
static constexpr uint8_t APPEARANCE_CATALOG_GAMEPLAY_PLACEABLE_BLOCK = 6;

static constexpr uint16_t kAppearanceStateFlagEquipped = 1;
static constexpr uint16_t APPEARANCE_CATALOG_ITEM_WEAPON_CROSSBOW_ID = 400 + 17;
static constexpr uint16_t APPEARANCE_CATALOG_ITEM_LEGACY_YY_BLOCK_ID = 420;
// FL-4137 #12 side-block proof: a taller cube above kMpMaxImplicitStepUp.
// Same AKM mesh, same sprite, but collision_height=40 so auto-step CANNOT
// snap onto it. Used by the proof to verify the player is laterally
// blocked instead of stepped up.
static constexpr uint16_t APPEARANCE_CATALOG_ITEM_TALL_YY_BLOCK_ID  = 421;

static constexpr AppearanceCatalogProfileDef kAppearanceCatalogProfiles[] = {
	{APPEARANCE_CATALOG_DEFAULT_PROFILE_ID, 101, "default_profile", nullptr, 0,
	 APPEARANCE_PROFILE_REACHABILITY_FULL_PLAYER},
	{APPEARANCE_CATALOG_WALLACE_PROFILE_ID, 102, "wallace_player", nullptr, 0,
	 APPEARANCE_PROFILE_REACHABILITY_FIXED_PLAYER},
	{APPEARANCE_CATALOG_GROMIT_PROFILE_ID, 103, "gromit_companion", nullptr, 0,
	 APPEARANCE_PROFILE_REACHABILITY_FIXED_COMPANION},
};
static constexpr int kAppearanceCatalogProfileCount =
	sizeof(kAppearanceCatalogProfiles) / sizeof(kAppearanceCatalogProfiles[0]);

static constexpr AppearanceCatalogSeatDef kAppearanceCatalogSeats[] = {
};
static constexpr int kAppearanceCatalogSeatCount =
	sizeof(kAppearanceCatalogSeats) / sizeof(kAppearanceCatalogSeats[0]);

// swing_range_units / projectile_units_per_tick / spawns_projectile_on_swing
    // match the legacy melee range and generic projectile fallback constants
// constants so wire-observable behavior is byte-identical to the pre-Wave-3
// state; the difference is that the values are now catalog-owned data, not
// C++ branches on hard-coded weapon classes.
static constexpr AppearanceCatalogItemDef kAppearanceCatalogItems[] = {
	// id  slot mount kind gkind                         swing_ticks spawns_proj swing_range proj_speed place explicit break dur dist radius height slug                world_sprite                              inventory_sprite
	{402,  APPEARANCE_SLOT_KIND_SHIELD, 0,    0,   APPEARANCE_CATALOG_GAMEPLAY_WEARABLE,  0,          0,          0.0f,       0.0f,      0, 0, 0, 0, 0.0f, 0.0f, 0.0f, "shield_item",      "assets/sprites/item-shield.xp",          "assets/sprites/item-shield.xp"},
	{409,  APPEARANCE_SLOT_KIND_WEAPON, 0,    APPEARANCE_PRESENTATION_KIND_ATTACK,    APPEARANCE_CATALOG_GAMEPLAY_WEAPON, 12,         0,          0.0f,       0.0f,      0, 0, 1, 0, 0.0f, 0.0f, 0.0f, "normal_sword",     "assets/sprites/item-sword.xp",           "assets/sprites/item-sword.xp"},
	{410,  APPEARANCE_SLOT_KIND_HEAD, 0,    0,   APPEARANCE_CATALOG_GAMEPLAY_WEARABLE,    0,          0,          0.0f,       0.0f,      0, 0, 0, 0, 0.0f, 0.0f, 0.0f, "normal_helmet",    "assets/sprites/item-helmet.xp",          "assets/sprites/item-helmet.xp"},
	{411,  APPEARANCE_SLOT_KIND_ARMOR, 0,    0,   APPEARANCE_CATALOG_GAMEPLAY_WEARABLE,   0,          0,          0.0f,       0.0f,      0, 0, 0, 0, 0.0f, 0.0f, 0.0f, "normal_armour",    "assets/sprites/item-armor.xp",           "assets/sprites/item-armor.xp"},
	{412,  APPEARANCE_SLOT_KIND_MOUNT, 950,  0,   APPEARANCE_CATALOG_GAMEPLAY_MOUNTABLE,  0,          0,          0.0f,       0.0f,      0, 0, 0, 0, 0.0f, 0.0f, 0.0f, "wolf_mountable",   "assets/sprites/wolfie.xp",               "assets/sprites/wolfie.xp"},
	{413,  APPEARANCE_SLOT_KIND_MOUNT, 951,  0,   APPEARANCE_CATALOG_GAMEPLAY_MOUNTABLE,  0,          0,          0.0f,       0.0f,      0, 0, 0, 0, 0.0f, 0.0f, 0.0f, "bee_mountable",    "assets/sprites/bigbee.xp",               "assets/sprites/bigbee.xp"},
	{APPEARANCE_CATALOG_ITEM_WEAPON_CROSSBOW_ID,  APPEARANCE_SLOT_KIND_WEAPON, 0,    APPEARANCE_PRESENTATION_KIND_IDLE_WALK, APPEARANCE_CATALOG_GAMEPLAY_WEAPON, 0,          1,          14.0f,      1.25f,     0, 0, 0, 0, 0.0f, 0.0f, 0.0f, "weapon_crossbow",  "assets/sprites/item-crossbow.xp",        "assets/sprites/item-crossbow.xp"},
	// FL-4137: definition 420 wired to the real authored angle-atlas block.
	// 2026-05-29 reset: the 28 May commit replaced this with procedurally
	// generated 1-cell-tall strip XPs ("step / taller / thicker", angles='0',
	// divide-by-zero L0). Those were dumpstered to
	// asciicker-dumpster/fl4137-fake-1row-xps-2026-05-29/ and the real
	// `legacy-yy-block-angles.xp` is the inventory/held sprite. Runtime
	// geometry (placed cube AABB) is AUTHORED here, not derived from sprite
	// projection. Authored half=4.0 height=16.0 keeps the cube top at
	// pos.z + 16 = exactly kMpMaxImplicitStepUp - HEIGHT_SCALE/2, so the
	// standard building auto-step rule walks onto it (FL-4137 wedge gone).
	// FL-4137 #69 (2026-05-31): world_mesh_path is non-empty so placed-world
	// render uses the AKM mesh, not the XP sprite. Inventory/held preview
	// still uses world_sprite_path / inventory_sprite_path. Goal-text gate G3
	// requires the visible placed block to be a real AKM mesh instance.
	{APPEARANCE_CATALOG_ITEM_LEGACY_YY_BLOCK_ID, APPEARANCE_SLOT_KIND_HELD_ITEM, 0, 0, APPEARANCE_CATALOG_GAMEPLAY_PLACEABLE_BLOCK, 0, 0, 0.0f, 0.0f, 1, 1, 1, 3, 4.0f, 4.0f, 16.0f, "legacy_yy_block", "assets/sprites/legacy-yy-block-angles.xp", "assets/sprites/legacy-yy-block-angles.xp", "assets/meshes/legacy_yy_block_mesh.akm"},
	// FL-4137 #12: tall variant for side-block proof. height=40 > kMpMaxImplicitStepUp=24.
	{APPEARANCE_CATALOG_ITEM_TALL_YY_BLOCK_ID,  APPEARANCE_SLOT_KIND_HELD_ITEM, 0, 0, APPEARANCE_CATALOG_GAMEPLAY_PLACEABLE_BLOCK, 0, 0, 0.0f, 0.0f, 1, 1, 1, 3, 4.0f, 4.0f, 40.0f, "tall_yy_block",   "assets/sprites/legacy-yy-block-angles.xp", "assets/sprites/legacy-yy-block-angles.xp", "assets/meshes/legacy_yy_block_mesh.akm"},
};
static constexpr int kAppearanceCatalogItemCount =
	sizeof(kAppearanceCatalogItems) / sizeof(kAppearanceCatalogItems[0]);

// rig_id values come from protocol_common.h: APPEARANCE_RIG_DEFAULT (0) and
// APPEARANCE_RIG_MOUNTED_RIDER_SEAM (1). The catalog binds rig kinds to mount
// definitions so server / reachability code never derives rig in C++.
static constexpr AppearanceCatalogMountDef kAppearanceCatalogMounts[] = {
	{950, 1, APPEARANCE_RIG_MOUNTED_RIDER_SEAM, "wolf_mount"},
	{951, 2, APPEARANCE_RIG_MOUNTED_RIDER_SEAM, "bee_mount"},
};
static constexpr int kAppearanceCatalogMountCount =
	sizeof(kAppearanceCatalogMounts) / sizeof(kAppearanceCatalogMounts[0]);

static inline const AppearanceCatalogItemDef* FindAppearanceCatalogItemById(
	uint16_t item_definition_id)
{
	for (int i = 0; i < kAppearanceCatalogItemCount; i++)
	{
		if (kAppearanceCatalogItems[i].id == item_definition_id)
			return &kAppearanceCatalogItems[i];
	}
	return 0;
}

// Catalog-owned rig lookup. mount_definition_id == 0 means no mount, which is
// APPEARANCE_RIG_DEFAULT. Unknown mount IDs also return DEFAULT so the server
// never silently invents a non-default rig from missing data.
static inline uint16_t AppearanceCatalogRigForMount(uint16_t mount_definition_id)
{
	if (mount_definition_id == 0)
		return APPEARANCE_RIG_DEFAULT;
	for (int i = 0; i < kAppearanceCatalogMountCount; i++)
	{
		if (kAppearanceCatalogMounts[i].id == mount_definition_id)
			return kAppearanceCatalogMounts[i].rig_id;
	}
	return APPEARANCE_RIG_DEFAULT;
}
