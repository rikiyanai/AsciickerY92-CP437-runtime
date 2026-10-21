// multiplayer_session.cpp — Player session lifecycle (join, spawn, disconnect)
//
// Owns the player session lifecycle on the authoritative server.
// Extracted from server/server_tick.cpp.

#include "multiplayer_session.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "server_state.h"
#include "appearance_contract_state.h"
#include "rate_limit_disconnect_witness.h"
#include "protocol/protocol_combat.h"
#include "physics.h"

// =====================================================================
// Session queries
// =====================================================================

bool SvrHasAnyActiveSession(const ServerState* state)
{
    if (!state) return false;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
        if (state->players[i].active) return true;
    return false;
}

bool SvrHasAnyAlivePlayer(const ServerState* state)
{
    if (!state)
        return false;
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        const SvrPlayerState* ps = &state->players[i];
        if (!ps->active || ps->phase != CPHASE_ALIVE || ps->death_tick > 0)
            continue;
        return true;
    }
    return false;
}

// =====================================================================
// Join handling
// =====================================================================

int SvrAcceptJoinV2(ServerState* state, int ci,
                    const char* name,
                    uint16_t appearance_contract_version,
                    const char* bundle_hash,
                    const char* ids_lock_hash,
                    const char* glyph_manifest_hash,
                    const char* content_pack_id,
                    const char* lut_hash,
                    const char* page_atlas_chain_hash,
                    uint8_t* out_reject_reason)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
    {
        if (out_reject_reason) *out_reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::JOIN_ACCEPT_FAILED;
        return -1;
    }

    // Validate appearance contract claims
    // FL-4131 Phase 7 / P10 — also check glyph manifest identity and atlas chain.
    uint8_t reject = SvrValidateJoinV2Claims(state,
                                             appearance_contract_version,
                                             bundle_hash,
                                             ids_lock_hash,
                                             glyph_manifest_hash,
                                             content_pack_id,
                                             lut_hash,
                                             page_atlas_chain_hash);
    if (reject != APPEARANCE_CONTRACT_REJECT_REASON::NONE)
    {
        if (out_reject_reason) *out_reject_reason = reject;
        return -1;
    }

    // Validate player name
    if (!name || !name[0])
    {
        if (out_reject_reason) *out_reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::NAME_INVALID_CHARS;
        return -1;
    }

    for (const char* p = name; *p; p++)
    {
        if ((unsigned char)*p < 32)
        {
            if (out_reject_reason) *out_reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::NAME_INVALID_CHARS;
            return -1;
        }
    }

    // Check for duplicate name
    for (int i = 0; i < SVR_MAX_CLIENTS; i++)
    {
        if (i == ci || !state->players[i].active)
            continue;
        if (strcmp(state->players[i].name, name) == 0)
        {
            if (out_reject_reason) *out_reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::NAME_DUPLICATE;
            return -1;
        }
    }

    // Accept the join: initialize player state
    SvrPlayerState* ps = &state->players[ci];
    memset(ps, 0, sizeof(*ps));
    ps->active = true;
    ps->phase = CPHASE_JOINED;
    snprintf(ps->name, sizeof(ps->name), "%s", name);
    ps->player_id = ci;
    ps->hp = SVR_PLAYER_MAX_HP;
    ps->max_hp = SVR_PLAYER_MAX_HP;

    // Store join claims for contract enforcement
    ps->join_v2_claim_present = true;
    ps->join_v2_contract_version = appearance_contract_version;
    if (bundle_hash)
        snprintf(ps->join_v2_bundle_hash, sizeof(ps->join_v2_bundle_hash), "%s", bundle_hash);
    if (ids_lock_hash)
        snprintf(ps->join_v2_ids_lock_hash, sizeof(ps->join_v2_ids_lock_hash), "%s", ids_lock_hash);

    // Transition client phase to JOINED
    SvrTransitionPhase(ps, CPHASE_JOINED);
    atomic_store_phase(&state->clients[ci].phase, CPHASE_JOINED);

    if (out_reject_reason) *out_reject_reason = APPEARANCE_CONTRACT_REJECT_REASON::NONE;
    return ci;
}

// =====================================================================
// Player spawn
// =====================================================================

void SvrSpawnPlayer(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;

    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active)
        return;

    // Reset combat state
    ps->life_state = LIFE_STATE::ALIVE;
    ps->mount_state = 0;
    ps->locomotion_state = LOCOMOTION_STATE::IDLE;
    ps->combat_state = COMBAT_STATE::NONE;
    ps->hp = SVR_PLAYER_MAX_HP;
    ps->max_hp = SVR_PLAYER_MAX_HP;
    ps->death_tick = 0;
    ps->respawn_tick = 0;
    ps->last_swing_tick = 0;
    ps->last_swing_presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
    ps->last_swing_stamp_us = 0;

    // Reset input
    memset(&ps->latest_input, 0, sizeof(ps->latest_input));
    ps->input_force[0] = 0;
    ps->input_force[1] = 0;
    ps->input_force_z = 0;
    ps->last_recv_input_seq = 0;
    ps->has_recv_input_seq = false;
    ps->last_applied_input_seq = 0;
    ps->input_seq_regressions = 0;
    SvrRateLimitDisconnectResetPlayer(ps);

    // Reset stats
    ps->total_damage_dealt = 0;
    ps->total_damage_taken = 0;
    ps->rate_limit_violations = 0;

    // Transition to ALIVE
    SvrTransitionPhase(ps, CPHASE_ALIVE);
    atomic_store_phase(&state->clients[ci].phase, CPHASE_ALIVE);

    printf("[session] player %d spawned\n", ci);
}

// =====================================================================
// Disconnect handling
// =====================================================================

void SvrDisconnectPlayer(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;

    SvrPlayerState* ps = &state->players[ci];
    if (!ps->active)
        return;

    // Clean up physics
    if (ps->physics)
    {
        DeletePhysics(ps->physics);
        ps->physics = 0;
    }

    // Release the client slot
    ps->active = false;
    ps->phase = CPHASE_NONE;
    memset(ps->name, 0, sizeof(ps->name));

    // Clear client IO state
    state->clients[ci].phase = CPHASE_NONE;
    atomic_store_phase(&state->clients[ci].phase, CPHASE_NONE);
    state->clients[ci].ws_upgraded = false;

    // Release slot bitmask
    atomic_release_slot(&state->slot_bitmask, ci);

    printf("[session] player %d disconnected\n", ci);
}

// =====================================================================
// Shutdown detection
// =====================================================================

bool SvrShouldShutdown(const ServerState* state)
{
    return !SvrHasAnyActiveSession(state);
}
