// server/authority/server_authority_state.h — Server gameplay authority state
//
// PURPOSE:
// Owns the gameplay-authority slice of Server: player roster (Human array),
// snapshot state, NPC repository, item state, combat observability.
//
// Embedded into the Server struct. Access via server->authority.*.

#pragma once

#include <stdint.h>

#include "server/snapshot_client_state.h"
#include "server/snapshot_npc_repository.h"
#include "server/authoritative_item_server_state.h"
#include "server/combat_event_server_state.h"
#include "server/protocol/protocol_items.h"

struct Human;

struct CollisionDebugClientState
{
    uint8_t valid = 0;
    uint16_t count = 0;
    uint16_t player_id = 0xffff;
    uint32_t tick = 0;
    uint8_t support_source = 0;
    uint8_t push_source = 0;
    uint16_t support_item_id = 0;
    float player_pos[3] = {0.0f, 0.0f, 0.0f};
    float support_z = 0.0f;
    STRUCT_BRC_COLLISION_DEBUG_SAMPLE samples[COLLISION_DEBUG_SAMPLE_MAX] = {};
};

struct ServerAuthority
{
    // ── Player roster ──
    Human* others = nullptr;  // [max_clients] — indexed by connection slot
    Human* head = nullptr;
    Human* tail = nullptr;

    // ── Snapshot state ──
    SnapshotClientState snapshot_client;
    ServerSnapshotNpcRepository npc_repo;

    // ── Item state ──
    AuthoritativeItemServerState auth_item;

    // ── Combat observability ──
    CombatEventObservability combat_obs;

    // ── Read-only collision debug surface ──
    CollisionDebugClientState collision_debug;
};
