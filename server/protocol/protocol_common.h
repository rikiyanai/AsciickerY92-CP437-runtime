// protocol_common.h — Shared protocol enums and constants
//
// Extracted from server/multiplayer_protocol.h.
// No socket/platform dependencies — can be compiled alone.
//
// SEE ALSO: protocol_snapshot.h, protocol_combat.h, protocol_items.h

#pragma once

#include <stdint.h>

// ── Shared gameplay state enums ──

struct LIFE_STATE { enum
{
    NONE = 0,
    ALIVE,
    DEAD,
    SIZE
};};

struct LOCOMOTION_STATE { enum
{
    NONE = 0,
    IDLE,
    MOVING,
    AIRBORNE,
    SIZE
};};

struct COMBAT_STATE { enum
{
    NONE = 0,
    ATTACKING,
    SIZE
};};

// ── Appearance system constants ──

#define PRESENTATION_VARIANT_LIMIT 16
#define PRESENTATION_VARIANT_NONE     0
#define PRESENTATION_VARIANT_REGULAR  1
#define PRESENTATION_VARIANT_GOLD     2
#define PRESENTATION_VARIANT_DARK     3

// Wearable style constants — maps from legacy style ids to presentation variants
#define WEARABLE_STYLE_DEFAULT 0
#define WEARABLE_STYLE_GOLD    1
#define WEARABLE_STYLE_DARK    2

#define APPEARANCE_SLOT_KIND_BODY          300
#define APPEARANCE_SLOT_KIND_HEAD          301
#define APPEARANCE_SLOT_KIND_SHIELD        302
#define APPEARANCE_SLOT_KIND_WEAPON        303
#define APPEARANCE_SLOT_KIND_ARMOR         306
#define APPEARANCE_SLOT_KIND_MOUNT         307
#define APPEARANCE_SLOT_KIND_HELD_ITEM     308

#define APPEARANCE_VISUAL_STYLE_DEFAULT 500
#define APPEARANCE_VISUAL_STYLE_GOLD    501
#define APPEARANCE_VISUAL_STYLE_DARK    502

#define APPEARANCE_PRESENTATION_KIND_IDLE_WALK 600
#define APPEARANCE_PRESENTATION_KIND_ATTACK    601
#define APPEARANCE_PRESENTATION_KIND_DEATH     602

#define APPEARANCE_STATE_V2_MAX_ENTRIES 8

#define APPEARANCE_V2_ENTITY_PLAYER 1
#define APPEARANCE_V2_ENTITY_NPC    2

#define APPEARANCE_ITEM_STATE_WORLD    0x0001
#define APPEARANCE_ITEM_STATE_EQUIPPED 0x0002
#define APPEARANCE_ITEM_STATE_MAP_AUTHORED 0x0004
#define APPEARANCE_ITEM_STATE_PLACED   0x0008
#define APPEARANCE_ITEM_STATE_COLLIDABLE 0x0010
#define APPEARANCE_ITEM_STATE_EXPLICIT_PICKUP_ONLY 0x0020

#define APPEARANCE_VARIATION_DEFAULT 0
#define APPEARANCE_VARIATION_CROSSBOW 1

#define APPEARANCE_RIG_DEFAULT 0
#define APPEARANCE_RIG_MOUNTED_RIDER_SEAM 1

// FL-3955 S-7: weapon_crossbow slug — single source of truth.
// All C++ and Python locations must use this constant, not a hardcoded string literal.
#define WEAPON_CROSSBOW_SLUG "weapon_crossbow"

#define APPEARANCE_ENTRY_STATE_EQUIPPED 0x0001

// FL-3955 S-4: Shared slot-to-bitmask mapping.
// Single authority for tracked-NPC attachment mask bits.
// Both engine/game_render_bridge.cpp and the ActorVisualProfile key/compose path
// must use this.
// Do NOT create independent switches elsewhere.
static inline uint32_t SlotKindToAttachmentBitmask(uint16_t slot_kind_id)
{
	switch (slot_kind_id)
	{
	case APPEARANCE_SLOT_KIND_HEAD:   return 1u << 0;
	case APPEARANCE_SLOT_KIND_SHIELD:  return 1u << 1;
	case APPEARANCE_SLOT_KIND_WEAPON:  return 1u << 2;
	case APPEARANCE_SLOT_KIND_ARMOR:   return 1u << 3;
	default: return 0u;
	}
}

static inline uint8_t PresentationVariantFromWearableStyle(uint8_t style)
{
	switch (style)
	{
		case WEARABLE_STYLE_GOLD: return PRESENTATION_VARIANT_GOLD;
		case WEARABLE_STYLE_DARK: return PRESENTATION_VARIANT_DARK;
		default: return PRESENTATION_VARIANT_REGULAR;
	}
}
