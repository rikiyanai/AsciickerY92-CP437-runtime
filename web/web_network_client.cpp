// web_network_client.cpp — Web networking / WebSocket multiplayer bridge
//
// PURPOSE: Browser WebSocket multiplayer networking implementation.
// Extracted from web/game_web.cpp to isolate packet queuing, Server lifecycle,
// and join/disconnect from platform entry code and diagnostics.
//
// EXTRACTED FROM: web/game_web.cpp (monolith)
//
// INTEGRATION POINTS:
// - game_web.cpp: extern "C" calls Join(), Packet() from JavaScript
// - engine/game.cpp: Server::Send() called from game engine
// - web_diagnostics.h: calls WebDiagnosticsOnLifecycleEvent, CountPacketToken, etc.
//
// SEE ALSO:
// - web_network_client.h — declarations

#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include <emscripten.h>

#include "game.h"
#include "world.h"
#include "terrain.h"
#include "render.h"
// Server types are defined in engine/game.h (included above)
//#include "server/server.h" -- removed, does not exist
#include "sprite.h"
#include "enemygen.h"

#include "web_network_client.h"
#include "web_diagnostics.h"
#include "web_platform.h"
#include "mainmenu.h"

// ── Forward declarations of game_web.cpp globals ──

extern Game* game;
extern World* world;
extern Terrain* terrain;
extern AnsiCell* render_buf;

extern Character* player_head;
extern Character* player_tail;
extern uint64_t (*MakeStamp)();
extern "C" int MainMenuWebGameLoadingState();
extern "C" int MainMenuWebProgressState();
extern "C" void MainMenuResetWebLoadingState();

// ── Networking structs ──

static const uint32_t FL933_SERVER_CANARY_A = 0xF1933A11u;
static const uint32_t FL933_SERVER_CANARY_B = 0xF1933B22u;
static const uint32_t FL933_SERVER_CANARY_C = 0xF1933C33u;
static const uint32_t FL933_SERVER_CANARY_D = 0xF1933D44u;

GameServerAllocation* g_web_server_alloc = 0;

static void WebFL933SetServerCanaries(GameServerAllocation* alloc)
{
    if (!alloc)
        return;
    alloc->pre_canary[0] = FL933_SERVER_CANARY_A;
    alloc->pre_canary[1] = FL933_SERVER_CANARY_B;
    alloc->pre_canary[2] = FL933_SERVER_CANARY_C;
    alloc->pre_canary[3] = FL933_SERVER_CANARY_D;
    alloc->post_canary[0] = FL933_SERVER_CANARY_D;
    alloc->post_canary[1] = FL933_SERVER_CANARY_C;
    alloc->post_canary[2] = FL933_SERVER_CANARY_B;
    alloc->post_canary[3] = FL933_SERVER_CANARY_A;
}

int WebFL933ServerCanariesOk(const GameServerAllocation* alloc)
{
    if (!alloc)
        return -1;
    return alloc->pre_canary[0] == FL933_SERVER_CANARY_A &&
           alloc->pre_canary[1] == FL933_SERVER_CANARY_B &&
           alloc->pre_canary[2] == FL933_SERVER_CANARY_C &&
           alloc->pre_canary[3] == FL933_SERVER_CANARY_D &&
           alloc->post_canary[0] == FL933_SERVER_CANARY_D &&
           alloc->post_canary[1] == FL933_SERVER_CANARY_C &&
           alloc->post_canary[2] == FL933_SERVER_CANARY_B &&
           alloc->post_canary[3] == FL933_SERVER_CANARY_A;
}

// ── Send / join / pending globals ──

volatile bool g_web_authoritative_join_active = false;
uint32_t g_web_join_generation = 0;
int g_web_server_null_after_join_logs = 0;
int g_web_server_ptr_change_logs = 0;

uint32_t g_web_send_calls = 0;
uint32_t g_web_send_failures = 0;
uint32_t g_web_send_last_token = 0;
uint32_t g_web_send_hist_ack = 0;

uint32_t g_web_packet_join_flushes = 0;

PendingNetPacket g_pending_net_packets[64];
int g_pending_net_packet_count = 0;
uint32_t g_web_packet_calls = 0;
uint32_t g_web_packet_server_null = 0;
uint32_t g_web_packet_deferred = 0;
uint32_t g_web_packet_proc_called = 0;
uint32_t g_web_packet_last_branch = 0;
uint32_t g_web_packet_first_token = 0;
uint32_t g_web_pending_enqueued = 0;
uint32_t g_web_pending_enqueued_bytes = 0;
uint32_t g_web_pending_dropped = 0;
uint32_t g_web_pending_oversized_dropped = 0;
uint32_t g_web_pending_max_packet_size = 0;
uint32_t g_web_pending_last_oversized_token = 0;
uint32_t g_web_pending_last_oversized_size = 0;
uint32_t g_web_pending_last_oversized_cap = 0;
uint32_t g_web_pending_max_depth = 0;
uint32_t g_web_pending_drain_attempts = 0;
uint32_t g_web_pending_drain_block_defer = 0;
uint32_t g_web_pending_drain_block_token = 0;
uint32_t g_web_pending_drain_deferred_preserved = 0;
uint32_t g_web_pending_drain_deferred_token = 0;
uint32_t g_web_pending_drain_stop_server_null = 0;
uint64_t g_web_pending_first_block_us = 0;
uint64_t g_web_pending_first_defer_us = 0;
uint32_t g_web_pending_drain_processed = 0;

// ── Server::Send / Proc / Log ──

bool Server::Send(const uint8_t* data, int size)
{
    GameServer* gs = (GameServer*)server;
    g_web_send_calls++;
    g_web_send_last_token = (data && size > 0) ? (uint32_t)data[0] : 0u;
    if (g_web_send_last_token == 'A')
        g_web_send_hist_ack++;
    if (size > WEB_OUTBOUND_MSG_MAX)
    {
        g_web_send_failures++;
        return false;
    }
    gs->send_buf[0] = (uint8_t)(size & 0xff);
    gs->send_buf[1] = (uint8_t)((size >> 8) & 0xff);
    memcpy(gs->send_buf+2, data, size);
    int s = EM_ASM_INT( return Send(); );
    if (s <= 0)
        g_web_send_failures++;
    return s > 0;
}

void Server::Proc()
{
}

void Server::Log(const char* str)
{
    GameServer* gs = (GameServer*)server;
    int len = strlen(str);
    if (len > WEB_OUTBOUND_MSG_MAX)
        len = WEB_OUTBOUND_MSG_MAX;
    gs->send_buf[0] = (uint8_t)(len & 0xff);
    gs->send_buf[1] = (uint8_t)((len >> 8) & 0xff);
    memcpy(gs->send_buf+2, str, len);
    EM_ASM( ConsoleLog(); );
}

// ── Queue / Flush pending packets ──

bool WebRuntimeCanApplyAuthoritativeWorldPackets()
{
    return game && game->physics && world && terrain;
}

bool WebShouldDeferAuthoritativeWorldPacket(const uint8_t* ptr, int size)
{
    if (!ptr || size <= 0 || WebRuntimeCanApplyAuthoritativeWorldPackets())
        return false;
    switch (ptr[0])
    {
        case 'b': case 'q': case 'a': case 'i':
            return true;
        default:
            return false;
    }
}

void QueuePendingNetPacket(const uint8_t* ptr, int size, uint64_t packet_entry_us)
{
    if (!ptr || size <= 0)
        return;
    if ((uint32_t)size > g_web_pending_max_packet_size)
        g_web_pending_max_packet_size = (uint32_t)size;
    if (size > (int)sizeof(g_pending_net_packets[0].data))
    {
        g_web_pending_oversized_dropped++;
        g_web_pending_last_oversized_token = (ptr && size > 0) ? (uint32_t)ptr[0] : 0u;
        g_web_pending_last_oversized_size = (uint32_t)size;
        g_web_pending_last_oversized_cap = (uint32_t)sizeof(g_pending_net_packets[0].data);
        WebDiagnosticsCountDroppedPendingToken((ptr && size > 0) ? ptr[0] : 0);
        return;
    }
    if (g_pending_net_packet_count < 0)
        g_pending_net_packet_count = 0;
    if (g_pending_net_packet_count >= (int)(sizeof(g_pending_net_packets) / sizeof(g_pending_net_packets[0])))
    {
        g_web_pending_dropped++;
        WebDiagnosticsCountDroppedPendingToken(g_pending_net_packets[0].size > 0 ? g_pending_net_packets[0].data[0] : 0);
        memmove(g_pending_net_packets, g_pending_net_packets + 1,
                sizeof(g_pending_net_packets[0]) * ((sizeof(g_pending_net_packets) / sizeof(g_pending_net_packets[0])) - 1));
        g_pending_net_packet_count = (int)(sizeof(g_pending_net_packets) / sizeof(g_pending_net_packets[0])) - 1;
    }
    PendingNetPacket* p = &g_pending_net_packets[g_pending_net_packet_count++];
    p->size = (uint16_t)size;
    p->packet_entry_us = packet_entry_us;
    p->enqueue_us = GetTime();
    memcpy(p->data, ptr, (size_t)size);
    g_web_pending_enqueued++;
    g_web_pending_enqueued_bytes += (uint32_t)size;
    if ((uint32_t)g_pending_net_packet_count > g_web_pending_max_depth)
        g_web_pending_max_depth = (uint32_t)g_pending_net_packet_count;
}

void FlushPendingNetPacketsToServer()
{
    g_web_pending_drain_attempts++;
    if (!server || g_pending_net_packet_count <= 0)
    {
        if (!server && g_pending_net_packet_count > 0)
        {
            g_web_pending_drain_stop_server_null++;
            g_web_pending_drain_block_token = g_pending_net_packets[0].size > 0 ? (uint32_t)g_pending_net_packets[0].data[0] : 0u;
            if (!g_web_pending_first_block_us)
                g_web_pending_first_block_us = GetTime();
        }
        return;
    }
    g_web_packet_join_flushes++;
    int write_index = 0;
    int read_index = 0;
    for (; read_index < g_pending_net_packet_count; read_index++)
    {
        PendingNetPacket* p = &g_pending_net_packets[read_index];
        if (p->size <= 0)
            continue;
        if (WebShouldDeferAuthoritativeWorldPacket(p->data, (int)p->size))
        {
            g_web_pending_drain_deferred_preserved++;
            g_web_pending_drain_deferred_token = p->size > 0 ? (uint32_t)p->data[0] : 0u;
            if (!g_web_pending_first_defer_us)
                g_web_pending_first_defer_us = GetTime();
            if (write_index != read_index)
                g_pending_net_packets[write_index] = *p;
            write_index++;
            continue;
        }
        Server* before_server = server;
        int before_local_id = before_server ? before_server->connection.local_id : -1;
        if (p->size > 0 && p->data[0] == 'l')
            server->connection.lag.lag_trace_packet_entry_stamp = p->packet_entry_us;
        bool ok = server->Proc(p->data, (int)p->size);
        g_web_pending_drain_processed++;
        if (server != before_server && g_web_server_ptr_change_logs < 12)
        {
            printf("[FL933-FLUSH-SERVER-PTR-CHANGED] idx=%d op=%c size=%u ok=%d before=%p after=%p local_before=%d local_after=%d active=%d pending=%d game=%p world=%p terrain=%p physics=%p main_menu=%d\n",
                read_index,
                (p->size > 0) ? (char)p->data[0] : '-',
                (unsigned int)p->size,
                ok ? 1 : 0,
                (void*)before_server,
                (void*)server,
                before_local_id,
                server ? server->connection.local_id : -1,
                g_web_authoritative_join_active ? 1 : 0,
                g_pending_net_packet_count,
                (void*)game,
                (void*)world,
                (void*)terrain,
                game ? (void*)game->physics : 0,
                (game && game->ui.main_menu) ? 1 : 0);
            fflush(stdout);
            g_web_server_ptr_change_logs++;
        }
        if (!server)
        {
            g_web_pending_drain_stop_server_null++;
            g_web_pending_drain_block_token = p->size > 0 ? (uint32_t)p->data[0] : 0u;
            if (!g_web_pending_first_block_us)
                g_web_pending_first_block_us = GetTime();
            read_index++;
            break;
        }
    }
    if (read_index < g_pending_net_packet_count)
    {
        int remaining = g_pending_net_packet_count - read_index;
        if (remaining > 0)
        {
            memmove(g_pending_net_packets + write_index, g_pending_net_packets + read_index,
                    sizeof(g_pending_net_packets[0]) * (size_t)remaining);
        }
        g_pending_net_packet_count = write_index + remaining;
        return;
    }
    g_pending_net_packet_count = write_index;
}

extern "C" void WebFlushPendingNetPacketsToServer()
{
    FlushPendingNetPacketsToServer();
}

void WebFL933PendingQueueTokenCounts(int* b, int* q, int* a, int* i)
{
    if (b) *b = 0;
    if (q) *q = 0;
    if (a) *a = 0;
    if (i) *i = 0;
    for (int n = 0; n < g_pending_net_packet_count; n++)
    {
        const uint8_t token = g_pending_net_packets[n].size > 0 ? g_pending_net_packets[n].data[0] : 0;
        if (token == 'b' && b) (*b)++;
        else if (token == 'q' && q) (*q)++;
        else if (token == 'a' && a) (*a)++;
        else if (token == 'i' && i) (*i)++;
    }
}

int CountRemotePlayers()
{
    if (!server)
        return 0;
    int count = 0;
    Human* rp = server->authority.head;
    while (rp)
    {
        count++;
        rp = (Human*)rp->next;
    }
    return count;
}

// Non-static: used by RecorderStateJson in web_recorder_bridge.cpp
uint32_t WebCountEquippedLocalItems(const Server* s)
{
    if (!s)
        return 0;
    uint32_t count = 0;
    int local_id = s->connection.local_id;
    for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
    {
        const ::AuthoritativeItemState* ai = &s->authority.auth_item.items[i];
        if (!ai->valid || ai->owner_id != (uint16_t)local_id)
            continue;
        if ((ai->v2_state_flags & APPEARANCE_ITEM_STATE_EQUIPPED) != 0)
            count++;
    }
    return count;
}

// ── WebDestroyGameServerAllocation ──

void WebDestroyGameServerAllocation(const char* reason)
{
    WebDiagnosticsOnLifecycleEvent(reason ? reason : "destroy:entry",
        (uint32_t)(uintptr_t)server,
        (uint32_t)(uintptr_t)g_web_server_alloc,
        (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
        WebFL933ServerCanariesOk(g_web_server_alloc),
        g_pending_net_packet_count);
    Server* observed_server = (Server*)server;
    GameServerAllocation* alloc = g_web_server_alloc;
    Server* server_for_cleanup = observed_server;
    if (!server_for_cleanup && alloc)
        server_for_cleanup = (Server*)&alloc->server;

    if (alloc && observed_server && observed_server != (Server*)&alloc->server)
    {
        printf("[FL933-SERVER-ALLOC-MISMATCH] reason=%s server=%p alloc_server=%p alloc=%p canary_ok=%d\n",
            reason ? reason : "unknown",
            (void*)observed_server,
            (void*)&alloc->server,
            (void*)alloc,
            WebFL933ServerCanariesOk(alloc));
        fflush(stdout);
    }

    if (server_for_cleanup)
    {
        DestroySnapshotNpcVisuals(&server_for_cleanup->authority.npc_repo);
        ServerDestroyAuthoritativeItemVisuals(server_for_cleanup);
        free(server_for_cleanup->authority.others);
        server_for_cleanup->authority.others = 0;
    }

    if (alloc)
        free(alloc);
    else if (observed_server)
        free(observed_server);

    g_web_server_alloc = 0;
    server = 0;
    WebDiagnosticsOnLifecycleEvent("destroy:exit",
        0, 0, 0, -1, 0);
}

// ── ResetWebRuntimeSession ──

void ResetWebRuntimeSession(const char* reason)
{
    WebDiagnosticsOnLifecycleEvent("ResetWebRuntimeSession:entry",
        (uint32_t)(uintptr_t)server,
        (uint32_t)(uintptr_t)g_web_server_alloc,
        (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
        WebFL933ServerCanariesOk(g_web_server_alloc),
        g_pending_net_packet_count);
    printf("[FL036-RESET] rebuilding web runtime after disconnect reason=%s game=%p terrain=%p world=%p\n",
           reason ? reason : "unknown",
           (void*)game,
           (void*)terrain,
           (void*)world);
    fflush(stdout);

    if (game)
    {
        FreeGame(game);
        DeleteGame(game);
        game = 0;
    }
    if (terrain)
    {
        DeleteTerrain(terrain);
        terrain = 0;
    }
    if (world)
    {
        DeleteWorld(world);
        world = 0;
    }
    PurgeItemInstCache();
    MainMenuResetWebLoadingState();

    game = CreateGame();
    if (game) {
        game->ui.main_menu = true;
        MainMenu_Show();
        return;
    }
    printf("[FL036-RESET] CreateGame failed during reconnect rebuild reason=%s\n", reason ? reason : "unknown");
    fflush(stdout);
}

// ── WebLogServerNullAfterJoin ──

void WebLogServerNullAfterJoin(const char* where, const uint8_t* ptr, int size)
{
    if (!g_web_authoritative_join_active || server || g_web_server_null_after_join_logs >= 8)
        return;
    const char op = (ptr && size > 0) ? (char)ptr[0] : '-';
    WebDiagnosticsObserveServerPointer(where,
        0u,
        g_web_authoritative_join_active ? 1 : 0,
        g_web_join_generation,
        (ptr && size > 0) ? (uint32_t)ptr[0] : 0u,
        size > 0 ? (uint32_t)size : 0u,
        g_web_packet_calls,
        g_web_packet_proc_called,
        g_web_packet_server_null,
        g_web_packet_deferred,
        (uint32_t)g_pending_net_packet_count,
        WebFL933ServerCanariesOk(g_web_server_alloc),
        (uint32_t)(uintptr_t)g_web_server_alloc,
        (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0));
    printf("[FL933-SERVER-NULL-AFTER-JOIN] where=%s server_slot=%p join_gen=%u pending=%d op=%c size=%d game=%p world=%p terrain=%p physics=%p main_menu=%d\n",
        where ? where : "unknown",
        (void*)&server,
        g_web_join_generation,
        g_pending_net_packet_count,
        op,
        size,
        (void*)game,
        (void*)world,
        (void*)terrain,
        game ? (void*)game->physics : 0,
        (game && game->ui.main_menu) ? 1 : 0);
    fflush(stdout);
    g_web_server_null_after_join_logs++;
}

// ══════════════════════════════════════════════════════════════════════════
// Extern "C" exports — JS-callable functions
// ══════════════════════════════════════════════════════════════════════════

extern "C"
{
    // Join multiplayer session from JavaScript.
    EMSCRIPTEN_KEEPALIVE void* Join(const char* name, int id, int max_cli)
    {
        WebDiagnosticsOnLifecycleEvent("Join:entry",
            (uint32_t)(uintptr_t)server,
            (uint32_t)(uintptr_t)g_web_server_alloc,
            (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
            WebFL933ServerCanariesOk(g_web_server_alloc),
            g_pending_net_packet_count);
        if (id<0)
        {
            WebDiagnosticsOnLifecycleEvent("Join:disconnect",
                (uint32_t)(uintptr_t)server,
                (uint32_t)(uintptr_t)g_web_server_alloc,
                (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
                WebFL933ServerCanariesOk(g_web_server_alloc),
                g_pending_net_packet_count);
            printf("[FL036-DISCONNECT] server=%p local_id=%d pending=%d active=%d join_gen=%u game=%p world=%p terrain=%p physics=%p\n",
                (void*)server,
                server ? server->connection.local_id : -1,
                g_pending_net_packet_count,
                g_web_authoritative_join_active ? 1 : 0,
                g_web_join_generation,
                (void*)game,
                (void*)world,
                (void*)terrain,
                game ? (void*)game->physics : 0);
            fflush(stdout);
            if (server)
                WebDestroyGameServerAllocation("JoinDisconnect");
            else if (g_web_server_alloc)
            {
                printf("[FL933-SERVER-LOST-WITH-ALLOC] reason=JoinDisconnect alloc=%p alloc_server=%p canary_ok=%d active=%d join_gen=%u pending=%d\n",
                    (void*)g_web_server_alloc,
                    (void*)&g_web_server_alloc->server,
                    WebFL933ServerCanariesOk(g_web_server_alloc),
                    g_web_authoritative_join_active ? 1 : 0,
                    g_web_join_generation,
                    g_pending_net_packet_count);
                fflush(stdout);
                WebDestroyGameServerAllocation("JoinDisconnect:server-null");
            }
            g_web_authoritative_join_active = false;
            g_pending_net_packet_count = 0;
            ResetWebRuntimeSession("JoinDisconnect");
            return 0;
        }

        WebDiagnosticsResetServerLossProvenance();

        if (game)
        {
            MpMoveInit(&game->player.mp_move);
            game->player.authoritative_snapshot_valid = false;
        }

        GameServerAllocation* alloc = (GameServerAllocation*)malloc(sizeof(GameServerAllocation));
        if (!alloc) {
            printf("[FL036-JOIN] failed to allocate GameServerAllocation\n");
            fflush(stdout);
            server = 0;
            WebDiagnosticsOnLifecycleEvent("Join:alloc-failed",
                0, 0, 0, -1, 0);
            return 0;
        }
        memset(alloc, 0, sizeof(GameServerAllocation));
        WebFL933SetServerCanaries(alloc);
        g_web_server_alloc = alloc;
        GameServer* gs = &alloc->server;
        server = (Server*)gs;
        WebDiagnosticsOnLifecycleEvent("Join:server-assigned",
            (uint32_t)(uintptr_t)server,
            (uint32_t)(uintptr_t)g_web_server_alloc,
            (uint32_t)(uintptr_t)&g_web_server_alloc->server,
            WebFL933ServerCanariesOk(g_web_server_alloc),
            g_pending_net_packet_count);
        if (max_cli < 1) max_cli = 1;
        server->connection.max_clients = max_cli;
        server->connection.local_id = id;
        server->authority.others = (Human*)malloc(sizeof(Human)*max_cli);
        if (!server->authority.others) {
            printf("[FL036-JOIN] failed to allocate roster max_cli=%d\n", max_cli);
            fflush(stdout);
            WebDestroyGameServerAllocation("JoinRosterAllocFailed");
            return 0;
        }
        memset(server->authority.others, 0, sizeof(Human) * max_cli);
        g_web_join_generation++;
        g_web_authoritative_join_active = true;
        WebDiagnosticsObserveServerPointer("Join:live",
            (uint32_t)(uintptr_t)server,
            1,
            g_web_join_generation,
            0u,
            0u,
            g_web_packet_calls,
            g_web_packet_proc_called,
            g_web_packet_server_null,
            g_web_packet_deferred,
            (uint32_t)g_pending_net_packet_count,
            WebFL933ServerCanariesOk(g_web_server_alloc),
            (uint32_t)(uintptr_t)g_web_server_alloc,
            (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0));
        FlushPendingNetPacketsToServer();
        WebDiagnosticsOnLifecycleEvent("Join:return",
            (uint32_t)(uintptr_t)server,
            (uint32_t)(uintptr_t)g_web_server_alloc,
            (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
            WebFL933ServerCanariesOk(g_web_server_alloc),
            g_pending_net_packet_count);
        return gs->send_buf;
    }

    bool WebAuthoritativeJoinActive()
    {
        return g_web_authoritative_join_active;
    }

    // Process incoming network packet from JavaScript
    EMSCRIPTEN_KEEPALIVE void Packet(const uint8_t* ptr, int size)
    {
        uint64_t packet_entry_us = GetTime();
        g_web_packet_calls++;
        const uint8_t packet_token = (ptr && size > 0) ? ptr[0] : 0;
        if (packet_token)
        {
            if (!g_web_packet_first_token)
                g_web_packet_first_token = packet_token;
            WebDiagnosticsCountPacketToken(packet_token);
        }
        WebDiagnosticsOnLifecycleEvent("Packet:entry",
            (uint32_t)(uintptr_t)server,
            (uint32_t)(uintptr_t)g_web_server_alloc,
            (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
            WebFL933ServerCanariesOk(g_web_server_alloc),
            g_pending_net_packet_count);
        static int fl036_packet_logs = 0;
        if (ptr && size > 0 && fl036_packet_logs < 12)
        {
            if (ptr[0] == 'b' || ptr[0] == 'q' || ptr[0] == 'p' || ptr[0] == 'j' || ptr[0] == 'n')
            {
                printf("[FL036-PACKET] op=%c size=%d server=%p\n", (char)ptr[0], size, (void*)server);
                fl036_packet_logs++;
            }
        }
        if (server)
        {
            FlushPendingNetPacketsToServer();
            if (WebShouldDeferAuthoritativeWorldPacket(ptr, size))
            {
                g_web_packet_deferred++;
                g_web_packet_last_branch = 2;
                WebDiagnosticsOnLifecycleEvent("Packet:deferred",
                    (uint32_t)(uintptr_t)server,
                    (uint32_t)(uintptr_t)g_web_server_alloc,
                    (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
                    WebFL933ServerCanariesOk(g_web_server_alloc),
                    g_pending_net_packet_count);
                QueuePendingNetPacket(ptr, size, packet_entry_us);
                return;
            }
            uint64_t proc_start = GetTime();
            Server* before_server = server;
            int before_local_id = before_server ? before_server->connection.local_id : -1;
            if (packet_token == 'l')
                server->connection.lag.lag_trace_packet_entry_stamp = packet_entry_us;
            bool ok = server->Proc(ptr, size);
            g_web_packet_proc_called++;
            g_web_packet_last_branch = 3;
            WebDiagnosticsOnLifecycleEvent("Packet:after-Proc",
                (uint32_t)(uintptr_t)server,
                (uint32_t)(uintptr_t)g_web_server_alloc,
                (uint32_t)(uintptr_t)(g_web_server_alloc ? &g_web_server_alloc->server : 0),
                WebFL933ServerCanariesOk(g_web_server_alloc),
                g_pending_net_packet_count);
            uint64_t proc_end = GetTime();
            uint64_t proc_delta = (proc_end >= proc_start) ? (proc_end - proc_start) : 0;
            uint32_t proc_us = (proc_delta > 0xffffffffULL) ? 0xffffffffu : (uint32_t)proc_delta;
            if (packet_token == 'l')
            {
                Server* lag_trace_server = before_server ? before_server : server;
                if (lag_trace_server)
                {
                    lag_trace_server->connection.lag.lag_trace_packet_exit_stamp = proc_end;
                    lag_trace_server->connection.lag.lag_trace_packet_proc_us = proc_us;
                    lag_trace_server->connection.lag.lag_trace_packet_exit_valid = true;
                }
            }
            if (server != before_server && g_web_server_ptr_change_logs < 8)
            {
                printf("[FL933-SERVER-PTR-CHANGED] op=%c size=%d ok=%d before=%p after=%p local_before=%d local_after=%d active=%d pending=%d proc_us=%u join_gen=%u\n",
                    (ptr && size > 0) ? (char)ptr[0] : '-',
                    size,
                    ok ? 1 : 0,
                    (void*)before_server,
                    (void*)server,
                    before_local_id,
                    server ? server->connection.local_id : -1,
                    g_web_authoritative_join_active ? 1 : 0,
                    g_pending_net_packet_count,
                    proc_us,
                    g_web_join_generation);
                fflush(stdout);
                g_web_server_ptr_change_logs++;
            }
            if (ptr && size > 0)
                WebDiagnosticsTrackPacketProc(ptr[0], proc_us);
            g_web_pending_drain_processed++;
        }
        else if (ptr && size > 0)
        {
            g_web_packet_server_null++;
            g_web_packet_last_branch = 4;
            WebLogServerNullAfterJoin("Packet", ptr, size);
        }
    }

    void SetRespawnItemRefreshBatchMode(int enabled)
    {
        if (game)
            game->authoritative.item_respawn_batch_enabled = (enabled != 0);
    }

    int GameWorldReady()
    {
        if (!game)
            return 0;
        if (game->ui.main_menu)
            return 0;
        if (!game->physics)
            return 0;
        if (!world)
            return 0;
        if (!terrain)
            return 0;
        if (!server)
            return 0;
        return 1;
    }

    int GameAuthoritativeWorldReadyMissingMask()
    {
        int mask = 0;
        if (!game)
            return mask | AUTH_WORLD_MAIN_MENU_ACTIVE;
        if (game->ui.main_menu)
            mask |= AUTH_WORLD_MAIN_MENU_ACTIVE;
        if (!game->physics)
            mask |= AUTH_WORLD_MISSING_PHYSICS;
        if (!world)
            mask |= AUTH_WORLD_MISSING_WORLD;
        if (!terrain)
            mask |= AUTH_WORLD_MISSING_TERRAIN;
        if (!server)
            mask |= AUTH_WORLD_MISSING_SERVER;
        if (server)
        {
            if (server->connection.local_id < 0 || server->connection.local_id >= server->connection.max_clients)
                mask |= AUTH_WORLD_BAD_LOCAL_ID;
            if (server->authority.snapshot_client.last_snapshot_seq == 0)
                mask |= AUTH_WORLD_MISSING_SNAPSHOT_SEQ;
            if (server->authority.snapshot_client.last_snapshot_tick == 0)
                mask |= AUTH_WORLD_MISSING_SNAPSHOT_TICK;
        }
        if (!LocalPlayerAuthoritativePoseReady(game->player, server != nullptr))
            mask |= AUTH_WORLD_MISSING_LOCAL_POSE;
        return mask;
    }

    int GameAuthoritativeWorldReady()
    {
        const int blocking_mask =
            GameAuthoritativeWorldReadyMissingMask() & ~AUTH_WORLD_MAIN_MENU_ACTIVE;
        return blocking_mask == 0 ? 1 : 0;
    }

    static uint32_t CountEquippedLocalItems(const Server* s)
    {
        if (!s)
            return 0;
        uint32_t count = 0;
        int local_id = s->connection.local_id;
        for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
        {
            const ::AuthoritativeItemState* ai = &s->authority.auth_item.items[i];
            if (!ai->valid || ai->owner_id != (uint16_t)local_id)
                continue;
            if ((ai->v2_state_flags & APPEARANCE_ITEM_STATE_EQUIPPED) != 0)
                count++;
        }
        return count;
    }

    const char* MultiplayerDiagJson()
    {
        static char multiplayer_diag_json[16384];
        int used = 0;
        multiplayer_diag_json[0] = 0;

        bool multiplayer_snapshot_probe = (server && server->authority.snapshot_client.last_snapshot_seq != 0);
        float self_x = game ? game->debug.dbg_last_local_pos_x : 0.0f;
        float self_y = game ? game->debug.dbg_last_local_pos_y : 0.0f;
        float self_z = game ? game->debug.dbg_last_local_pos_z : 0.0f;
        int self_fly = (game && game->session.fly_mode) ? 1 : 0;

        const Server* s = server;
        used = snprintf(multiplayer_diag_json, sizeof(multiplayer_diag_json),
                        "{"
                        "\"local_player_id\":%d,"
                        "\"snap_npc_wire_last\":%u,\"snap_npc_wire_total\":%u,\"snap_npc_apply\":%u,\"snap_npc_tick\":%u,\"snap_npc_overlay\":%d,"
                        "\"item_event_packets\":%u,\"item_event_applied_packets\":%u,\"last_item_event_id\":%u,\"last_item_event_tick\":%u,"
                        "\"web_packet_join_flushes\":%u,"
                        "\"auth_item_known\":%u,\"auth_item_world\":%u,\"auth_item_local\":%u,\"auth_item_state_apply\":%u,"
                        "\"auth_item_equipped_local\":%u,"
                        "\"auth_item_mode\":%u,\"auth_item_pick_block\":%u,\"auth_item_drop_block\":%u,\"auth_item_overlay\":%d,"
                        "\"auth_world_strip_count\":%u,\"auth_world_strip_0\":%u,\"auth_world_strip_1\":%u,\"auth_world_strip_2\":%u,"
                        "\"auth_pickup_req_attempts\":%u,\"auth_pickup_req_sent\":%u,\"auth_pickup_req_send_fail\":%u,"
                        "\"auth_pickup_req_last_index\":%d,\"auth_pickup_req_last_item_id\":%u,\"auth_pickup_req_last_reason\":%d,"
                        "\"auth_use_req_attempts\":%u,\"auth_use_req_sent\":%u,\"auth_use_req_send_fail\":%u,"
                        "\"auth_use_req_last_index\":%d,\"auth_use_req_last_item_id\":%u,\"auth_use_req_last_reason\":%d,"
                        "\"auth_item_local_event_kind\":%d,\"auth_item_local_event_item_id\":%u,\"auth_item_local_event_owner_id\":%u,\"auth_item_local_event_sync_calls\":%d,"
                        "\"attack_key_attempts\":%d,\"attack_setaction_success\":%d,\"attack_setaction_fail\":%d,"
                        "\"self_x\":%.2f,\"self_y\":%.2f,\"self_z\":%.2f,\"self_fly\":%d"
                        "}",
                        server ? server->connection.local_id : -1,
                        s ? s->authority.snapshot_client.snapshot_npc_entities_last : 0u,
                        s ? s->authority.snapshot_client.snapshot_npc_entities_total : 0u,
                        s ? (uint32_t)s->authority.npc_repo.npc_count : 0u,
                        s ? s->authority.npc_repo.npc_tick : 0u,
                        (s && s->authority.npc_repo.npc_count > 0) ? 1 : 0,
                        s ? s->authority.auth_item.item_event_packets : 0u,
                        s ? s->authority.auth_item.item_event_applied_packets : 0u,
                        s ? s->authority.auth_item.last_item_event_id : 0u,
                        s ? s->authority.auth_item.last_item_event_tick : 0u,
                        g_web_packet_join_flushes,
                        s ? s->authority.auth_item.item_count : 0u,
                        s ? s->authority.auth_item.item_world_count : 0u,
                        s ? s->authority.auth_item.item_local_owned_count : 0u,
                        s ? s->authority.auth_item.state_apply_packets : 0u,
                        CountEquippedLocalItems(s),
                        s ? 1u : 0u,
                        s ? s->authority.auth_item.pick_blocked_packets : 0u,
                        s ? s->authority.auth_item.drop_blocked_packets : 0u,
                        s ? 1 : 0,
                        game ? (uint32_t)game->authoritative.world_items_count : 0u,
                        game ? (uint32_t)game->authoritative.world_item_ids[0] : 0u,
                        game ? (uint32_t)game->authoritative.world_item_ids[1] : 0u,
                        game ? (uint32_t)game->authoritative.world_item_ids[2] : 0u,
                        game ? (uint32_t)game->debug.dbg_auth_pickup_req_attempts : 0u,
                        game ? (uint32_t)game->debug.dbg_auth_pickup_req_sent : 0u,
                        game ? (uint32_t)game->debug.dbg_auth_pickup_req_send_fail : 0u,
                        game ? game->debug.dbg_auth_pickup_req_last_index : -1,
                        game ? (uint32_t)game->debug.dbg_auth_pickup_req_last_item_id : 0u,
                        game ? game->debug.dbg_auth_pickup_req_last_reason : -1,
                        game ? (uint32_t)game->debug.dbg_auth_use_req_attempts : 0u,
                        game ? (uint32_t)game->debug.dbg_auth_use_req_sent : 0u,
                        game ? (uint32_t)game->debug.dbg_auth_use_req_send_fail : 0u,
                        game ? game->debug.dbg_auth_use_req_last_index : -1,
                        game ? (uint32_t)game->debug.dbg_auth_use_req_last_item_id : 0u,
                        game ? game->debug.dbg_auth_use_req_last_reason : -1,
                        game ? game->debug.dbg_auth_item_local_event_kind : 0,
                        game ? (uint32_t)game->debug.dbg_auth_item_local_event_item_id : 0u,
                        game ? (uint32_t)game->debug.dbg_auth_item_local_event_owner_id : 0u,
                        game ? game->debug.dbg_auth_item_local_event_sync_calls : 0,
                        game ? game->debug.dbg_attack_key_attempts : 0,
                        game ? game->debug.dbg_attack_setaction_success : 0,
                        game ? game->debug.dbg_attack_setaction_fail : 0,
                        (double)self_x, (double)self_y, (double)self_z, self_fly);
        return multiplayer_diag_json;
    }

    void ResetRemoteVisibilityLatches()
    {
        if (!game) return;
        game->debug.dbg_latched_remote_visibility_issue_frames = 0;
        game->debug.dbg_latched_remote_label_only_events = 0;
        game->debug.dbg_latched_remote_inst_missing_events = 0;
        game->debug.dbg_latched_remote_inst_hidden_events = 0;
        game->debug.dbg_latched_remote_sprite_null_events = 0;
        game->debug.dbg_latched_last_remote_visibility_issue_stamp = 0;
    }
}
