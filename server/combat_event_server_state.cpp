// combat_event_server_state.cpp — Combat event observability
//
// Owns CombatEventObservability: server-authoritative combat event counters
// and latched fields tracking authoritative BRC_* packets.
// Extracted from Server state.

#include "combat_event_server_state.h"

#include <string.h>

static void CombatEventObservability_Reset(CombatEventObservability* obs)
{
    if (!obs)
        return;
    memset(obs, 0, sizeof(*obs));
}

static void CombatEventObservability_NoteSwing(CombatEventObservability* obs,
                                         uint16_t attacker_id,
                                         uint16_t target_id)
{
    if (!obs)
        return;
    obs->swing_event_packets++;
    obs->last_swing_attacker_id = attacker_id;
    obs->last_swing_target_id = target_id;
}

static void CombatEventObservability_NoteDamage(CombatEventObservability* obs,
                                          uint16_t attacker_id,
                                          uint16_t target_id,
                                          uint8_t amount,
                                          int16_t new_hp,
                                          bool is_player_to_player,
                                          bool is_player_to_npc,
                                          bool is_npc_to_player,
                                          bool is_npc_to_npc)
{
    if (!obs)
        return;
    obs->damage_event_packets++;
    obs->last_damage_attacker_id = attacker_id;
    obs->last_damage_target_id = target_id;
    obs->last_damage_amount = amount;
    obs->last_damage_new_hp = new_hp;
    if (is_player_to_player) obs->damage_player_to_player_packets++;
    if (is_player_to_npc) obs->damage_player_to_npc_packets++;
    if (is_npc_to_player) obs->damage_npc_to_player_packets++;
    if (is_npc_to_npc) obs->damage_npc_to_npc_packets++;
}

static void CombatEventObservability_NoteDeath(CombatEventObservability* obs,
                                         uint16_t dead_id,
                                         uint16_t killer_id)
{
    if (!obs)
        return;
    obs->death_event_packets++;
    obs->last_death_dead_id = dead_id;
    obs->last_death_killer_id = killer_id;
}

static void CombatEventObservability_NoteRespawn(CombatEventObservability* obs,
                                           uint16_t player_id)
{
    if (!obs)
        return;
    obs->respawn_event_packets++;
    obs->last_respawn_player_id = player_id;
}
