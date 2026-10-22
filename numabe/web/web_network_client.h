// web_network_client.h — Web networking / WebSocket bridge seam
//
// PURPOSE: Narrow interface for browser WebSocket multiplayer networking.
// Extracted from web/game_web.cpp to isolate packet queuing, Server lifecycle,
// and join/disconnect from platform entry, filesystem, and diagnostics.
//
// INTEGRATION POINTS:
// - game_web.cpp: calls Join(), Packet() from JavaScript; Server::Send() from game engine
// - web_diagnostics.cpp: observes packet/probe state via callbacks
//
// SEE ALSO:
// - web_network_client.cpp — implementation

#pragma once

#include <stdint.h>
#include <stddef.h>
#include "game.h"

static constexpr uint16_t WEB_OUTBOUND_MSG_MAX = 512;
static_assert(WEB_OUTBOUND_MSG_MAX >= sizeof(STRUCT_REQ_JOIN_V2),
              "web outbound packet bridge must fit JOIN_V2");

// ── Transitional extern globals (shared with game_web.cpp RecorderStateJson) ──
// These will be encapsulated when RecorderStateJson moves to web_diagnostics.cpp.

struct GameServer : Server
{
    uint8_t send_buf[2 + WEB_OUTBOUND_MSG_MAX];
};

struct GameServerAllocation
{
    uint32_t pre_canary[4];
    GameServer server;
    uint32_t post_canary[4];
};
extern GameServerAllocation* g_web_server_alloc;

struct PendingNetPacket
{
    uint16_t size;
    uint64_t packet_entry_us;
    uint64_t enqueue_us;
    uint8_t data[8192];
};
extern PendingNetPacket g_pending_net_packets[64];
extern int g_pending_net_packet_count;

extern uint32_t g_web_packet_join_flushes;
extern uint32_t g_web_packet_calls;
extern uint32_t g_web_packet_server_null;
extern uint32_t g_web_packet_deferred;
extern uint32_t g_web_packet_proc_called;
extern uint32_t g_web_packet_last_branch;
extern uint32_t g_web_packet_first_token;
extern uint32_t g_web_pending_enqueued;
extern uint32_t g_web_pending_enqueued_bytes;
extern uint32_t g_web_pending_dropped;
extern uint32_t g_web_pending_oversized_dropped;
extern uint32_t g_web_pending_max_packet_size;
extern uint32_t g_web_pending_last_oversized_token;
extern uint32_t g_web_pending_last_oversized_size;
extern uint32_t g_web_pending_last_oversized_cap;
extern uint32_t g_web_pending_max_depth;
extern uint32_t g_web_pending_drain_attempts;
extern uint32_t g_web_pending_drain_block_defer;
extern uint32_t g_web_pending_drain_block_token;
extern uint32_t g_web_pending_drain_deferred_preserved;
extern uint32_t g_web_pending_drain_deferred_token;
extern uint32_t g_web_pending_drain_stop_server_null;
extern uint64_t g_web_pending_first_block_us;
extern uint64_t g_web_pending_first_defer_us;
extern uint32_t g_web_pending_drain_processed;
extern uint32_t g_web_send_calls;
extern uint32_t g_web_send_failures;
extern uint32_t g_web_send_last_token;
extern uint32_t g_web_send_hist_ack;
extern volatile bool g_web_authoritative_join_active;
extern uint32_t g_web_join_generation;

// Server canary helpers
int WebFL933ServerCanariesOk(const GameServerAllocation* alloc);

// Pending queue token count helper (data-driven, no globals)
void WebFL933PendingQueueTokenCounts(int* b, int* q, int* a, int* i);

// Count remote players
int CountRemotePlayers(void);

// Count equipped local items for a server (used by recorder bridge)
uint32_t WebCountEquippedLocalItems(const struct Server* s);

// Server lifecycle helpers (called from RecorderStateJson)
void WebLogServerNullAfterJoin(const char* where, const uint8_t* ptr, int size);

// World readiness queries
enum AuthWorldMask
{
    AUTH_WORLD_MAIN_MENU_ACTIVE = 1,
    AUTH_WORLD_MISSING_PHYSICS = 2,
    AUTH_WORLD_MISSING_WORLD = 4,
    AUTH_WORLD_MISSING_TERRAIN = 8,
    AUTH_WORLD_MISSING_SERVER = 16,
    AUTH_WORLD_MISSING_SERVER_WORLD = 32,
    AUTH_WORLD_BAD_LOCAL_ID = 64,
    AUTH_WORLD_MISSING_SNAPSHOT_SEQ = 128,
    AUTH_WORLD_MISSING_SNAPSHOT_TICK = 256,
    AUTH_WORLD_MISSING_LOCAL_POSE = 512,
    AUTH_WORLD_MISSING_APPEARANCE_STATE = 1024,
};

#ifdef __cplusplus
extern "C" {
#endif
int GameAuthoritativeWorldReadyMissingMask(void);
int GameAuthoritativeWorldReady(void);
int GameWorldReady(void);
#ifdef __cplusplus
}
#endif

#ifdef __cplusplus
extern "C" {
#endif

// Join multiplayer session from JavaScript.
// id<0 means disconnect; returns send_buf for outbound packets.
void* Join(const char* name, int id, int max_cli);

// Whether an authoritative join is currently active.
bool WebAuthoritativeJoinActive(void);

// Process incoming network packet from JavaScript.
void Packet(const uint8_t* ptr, int size);

// Flush pending queue to server (called from JavaScript, forces drain).
void WebFlushPendingNetPacketsToServer(void);

// Multiplayer diagnostics JSON (used by recorder bridge)
const char* MultiplayerDiagJson(void);

// Reset remote visibility latches (called from JS diagnostics)
void ResetRemoteVisibilityLatches(void);

#ifdef __cplusplus
}
#endif
