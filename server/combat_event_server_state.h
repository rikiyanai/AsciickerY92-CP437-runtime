#pragma once

#include <stdint.h>

// Combat event observability extracted from Server.
// Counters and latched fields tracking authoritative BRC_* packets.
struct CombatEventObservability
{
	uint32_t swing_event_packets = 0;
	uint32_t damage_event_packets = 0;
	uint32_t damage_player_to_player_packets = 0;
	uint32_t damage_player_to_npc_packets = 0;
	uint32_t damage_npc_to_player_packets = 0;
	uint32_t damage_npc_to_npc_packets = 0;
	uint32_t death_event_packets = 0;
	uint32_t respawn_event_packets = 0;
	uint16_t last_swing_attacker_id = 0;
	uint16_t last_swing_target_id = 0;
	uint16_t last_damage_target_id = 0;
	uint16_t last_damage_attacker_id = 0;
	uint8_t last_damage_amount = 0;
	int16_t last_damage_new_hp = 0;
	uint16_t last_death_dead_id = 0;
	uint16_t last_death_killer_id = 0;
	uint16_t last_respawn_player_id = 0;
	uint32_t obs_remote_death_seq[64]{};
	uint32_t obs_remote_last_death_source[64]{};
	uint32_t obs_remote_respawn_seq[64]{};
	uint32_t obs_remote_corpse_create_seq[64]{};
	uint32_t obs_remote_corpse_delete_seq[64]{};
	uint32_t obs_remote_corpse_create_count[64]{};
	uint32_t obs_remote_corpse_delete_count[64]{};
	uint32_t obs_remote_last_corpse_create_reason[64]{};
	uint32_t obs_remote_last_corpse_delete_reason[64]{};
};
