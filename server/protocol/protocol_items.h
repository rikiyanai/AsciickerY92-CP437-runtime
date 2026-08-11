// protocol_items.h — Item mutation, decal, and input wire protocol structs
//
// Extracted from server/multiplayer_protocol.h.
// Covers:
//   - server-originated item events (STRUCT_BRC_ITEM_CHANGE_V2, STRUCT_BRC_DECAL_ADD)
//   - client input intent (STRUCT_REQ_INPUT_MOVE, STRUCT_REQ_ITEM_ACTION)
//   - client snapshot acknowledgement (STRUCT_REQ_SNAPSHOT_ACK)
// No socket/platform dependencies.
//
// SEE ALSO: protocol_common.h, multiplayer_protocol.h

#pragma once

#include <stdint.h>

// Item change kind values
#define ITEM_CHANGE_KIND_PICKUP        1
#define ITEM_CHANGE_KIND_DROP          2
#define ITEM_CHANGE_KIND_CONSUME       3
#define ITEM_CHANGE_KIND_OWNER_SET     4
#define ITEM_CHANGE_KIND_OWNER_CLEAR   5
#define ITEM_CHANGE_KIND_EQUIP_SET     6
#define ITEM_CHANGE_KIND_EQUIP_CLEAR   7
#define ITEM_CHANGE_KIND_RESPAWN_RESET 8
#define ITEM_CHANGE_KIND_PLACE         9
#define ITEM_CHANGE_KIND_REMOVE        10

// Item action request kinds
#define ITEM_ACTION_REQ_PICKUP  1
#define ITEM_ACTION_REQ_DROP    2
#define ITEM_ACTION_REQ_USE     3
#define ITEM_ACTION_REQ_PLACE   4

#pragma pack(push,1)

// =============================================================================
// Authoritative Item Mutation Protocol — server-originated item events
// =============================================================================

struct STRUCT_BRC_ITEM_CHANGE_V2
{
	uint8_t token; // 'i'
	uint8_t kind;
	uint16_t item_id;
	uint16_t owner_id;
	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t equip_slot_kind_id;
	uint16_t state_flags;
	uint32_t event_id;
	uint32_t tick;
	float pos[3];
};

// =============================================================================
// Authoritative Decal Broadcast — server-originated terrain paint events
// =============================================================================

struct STRUCT_BRC_DECAL_ADD
{
	uint8_t token;      // 'v'
	uint8_t matid;
	uint16_t pad;
	uint32_t event_id;
	uint32_t tick;
	float x;
	float y;
	float r;
};

#define COLLISION_DEBUG_SAMPLE_MAX 256
#define COLLISION_DEBUG_PACKET_SAMPLE_MAX 8
#define COLLISION_DEBUG_FLAG_SUPPORT_TOP 0x01
#define COLLISION_DEBUG_FLAG_SUPPORT_ONLY 0x02
#define COLLISION_DEBUG_FLAG_SIDE_HIT    0x04

struct STRUCT_BRC_COLLISION_DEBUG_SAMPLE
{
	uint8_t source; // MpSupportSource-compatible value: terrain/world_mesh/placed_block
	uint8_t flags;
	uint16_t item_id;
	uint64_t entity_id;
	uint64_t inst_id;
	uint64_t mesh_id;
	uint32_t face_ordinal;
	float bmin[3];
	float bmax[3];
	float normal[3];
};

struct STRUCT_BRC_COLLISION_DEBUG
{
	uint8_t token; // 'c' — read-only collision debug, env-gated
	uint16_t count;
	uint16_t total_count;
	uint16_t player_id;
	uint32_t tick;
	uint8_t chunk_index;
	uint8_t chunk_count;
	uint8_t support_source;
	uint8_t push_source;
	uint16_t support_item_id;
	float player_pos[3];
	float support_z;
	STRUCT_BRC_COLLISION_DEBUG_SAMPLE samples[COLLISION_DEBUG_PACKET_SAMPLE_MAX];
};

// =============================================================================
// Authoritative Input Intent — ECS movement (future server-authoritative mode)
// =============================================================================

struct STRUCT_REQ_INPUT_MOVE
{
	uint8_t token;      // 'M'
	int8_t move_x;
	int8_t move_y;
	int8_t move_z;
	uint8_t flags;      // bit0=jump, bit1=fly, bit2=attack, bits3-4=mount
	int16_t yaw100;     // yaw * 100, quantized
	uint16_t input_seq; // monotonic local input sequence number
};

// =============================================================================
// Authoritative Item Action Requests
// =============================================================================

struct STRUCT_REQ_ITEM_ACTION
{
	uint8_t token;      // 'I'
	uint8_t kind;       // ITEM_ACTION_REQ_*
	uint16_t item_id;
	float pos[3];
};

// =============================================================================
// Snapshot ACK — Client acknowledges received snapshot sequence
// =============================================================================

struct STRUCT_REQ_SNAPSHOT_ACK
{
	uint8_t token;      // 'A'
	uint8_t pad;
	uint16_t seq;       // acknowledged snapshot sequence number
};

#pragma pack(pop)
