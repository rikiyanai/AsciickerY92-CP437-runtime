// protocol_snapshot.h — Authoritative snapshot wire protocol
//
// Extracted from server/multiplayer_protocol.h.
// STRUCT_SNAPSHOT_ENTITY and STRUCT_SNAPSHOT_BASELINE are the server-originated
// state broadcasts used for 'b' (baseline) and 'q' (delta) snapshot packets.
// The native authoritative server in server_tick.cpp builds and sends them.
// No socket/platform dependencies.
//
// SEE ALSO: protocol_common.h, multiplayer_protocol.h

#pragma once

#include <stdint.h>
#include <stddef.h>

// Entity types within snapshot packets
#define SNAPSHOT_ENTITY_NPC       1
#define SNAPSHOT_ENTITY_PLAYER    2

// State flags for snapshot entities
#define SNAPSHOT_STATE_ALIVE      0x01
// Delta tombstone: entity should be removed from client's merged snapshot set.
// Used only in 'q' packets.
#define SNAPSHOT_STATE_REMOVE     0x02
#define SNAPSHOT_STATE_GROUNDED   0x04
// FL-2193 fix-attempt 14e665a3 breadcrumb: keep a proof-visible bit for whether
// the server decided this NPC needed a physics step on the snapshot tick.
#define SNAPSHOT_STATE_NPC_NEEDS_PHYSICS 0x08

#pragma pack(push,1)

struct STRUCT_SNAPSHOT_ENTITY
{
	uint16_t entity_id;
	uint8_t entity_type;
	uint8_t life_state;
	uint8_t mount_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	uint16_t presentation_kind_id;
	uint8_t state_flags;
	float pos[3];
	float dir;
	int16_t hp;
	int16_t max_hp;
	uint32_t last_authoritative_tick;
	uint32_t presentation_started_tick;
	uint16_t applied_input_seq; // local player only; 0 for remotes/NPCs/legacy layouts
	float vel[3];
	float yaw;
	float yaw_vel;
	float slope;
	float accum_contact;
	float knockback[2];
	// FL-2957: server-sampled terrain height so client diagnostics can verify
	// floor coherence without relying on the separate authoritative state JSON.
	// The floor gate compares render_z against terrain_z — without this field
	// in the wire packet, the client has no way to floor-lock interpolation.
	float terrain_z;
	// FL-4137: server-authored support provenance. Proof must assert that
	// stand-on used the placed block support source, not just matching Z.
	float support_z;
	uint16_t support_item_id;
	uint8_t support_source;
	uint8_t support_valid;
};
static_assert(offsetof(STRUCT_SNAPSHOT_ENTITY, vel) == 40, "STRUCT_SNAPSHOT_ENTITY vel offset drift — update raw parser in game.cpp");
static_assert(sizeof(STRUCT_SNAPSHOT_ENTITY) == 88, "STRUCT_SNAPSHOT_ENTITY size changed — update raw parser in game.cpp");

struct STRUCT_SNAPSHOT_BASELINE
{
	uint8_t token;          // 'b' (baseline) or 'q' (delta)
	uint8_t layout_version; // 0-8 = legacy/native compat history; 9 = bundle presentation_kind_id; 10 = support provenance
	uint16_t seq;
	uint32_t tick;
	uint16_t entity_count;
	uint16_t entity_size;
};

#pragma pack(pop)
