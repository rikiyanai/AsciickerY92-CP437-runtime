// protocol_combat.h — Combat wire protocol structs
//
// Extracted from server/multiplayer_protocol.h.
// Server-authoritative swing sweep: attacker sends SWING intent → server
// resolves all in-range damageables → broadcasts SWING animation → DAMAGE
// per hit → DEATH per kill. Respawn position is broadcast as 'r'.
// No socket/platform dependencies.
//
// SEE ALSO: protocol_common.h, multiplayer_protocol.h

#pragma once

#include <stdint.h>

#pragma pack(push,1)

struct STRUCT_REQ_SWING
{
	uint8_t token; // 'H' = live swing intent, 'X' = debug targeted swing
	uint8_t pad;
	uint16_t target_id; // 0xFFFF for live gameplay; explicit target is debug-only metadata
};

struct STRUCT_BRC_SWING
{
	uint8_t token; // 'h'
	uint16_t attacker_id;
	uint16_t target_id; // 0xFFFF for gameplay sweep swings, explicit debug target otherwise
	uint16_t weapon_item_id; // catalog item id; owns projectile/ranged visual traits
	float pos[3];
	float dir;
	float target_pos[3]; // server-owned projectile/strike endpoint for visual replay
};

// STRUCT_REQ_DAMAGE ('D') REMOVED: dead-letter struct with zero call sites.
// Carried client-reported new_hp — directly contradicts server-authoritative combat model.
// Token 'D' is retired. No server handler ever existed (SvrProcessDamage absent).

struct STRUCT_BRC_DAMAGE
{
	uint8_t token; // 'd'
	uint8_t damage;
	uint16_t target_id;   // damageable entity id (player or NPC) that took damage
	uint16_t attacker_id; // who dealt it
	int16_t new_hp;       // target's new HP
};

// STRUCT_REQ_DEATH ('K') REMOVED: dead-letter struct with zero call sites.
// Client-originated death report contradicts server-authoritative combat model.
// Token 'K' is retired. No server handler ever existed.

struct STRUCT_BRC_DEATH
{
	uint8_t token; // 'k'
	uint8_t pad;
	uint16_t dead_id;   // damageable entity id (player or NPC) that died
	uint16_t killer_id;
};

struct STRUCT_REQ_RESPAWN
{
	uint8_t token; // 'R'
	uint8_t pad;
	uint16_t pad2;
	float pos[3]; // respawn position
};

struct STRUCT_BRC_RESPAWN
{
	uint8_t token; // 'r'
	uint8_t pad;
	uint16_t player_id;
	float pos[3]; // respawn position
};

#pragma pack(pop)
