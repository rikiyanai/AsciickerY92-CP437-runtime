#include "rate_limit_disconnect_witness.h"

#include <stdio.h>
#include <string.h>

#include "server_state.h"

namespace
{
static const char* SvrRateLimitViolationKindName(SvrRateLimitViolationKind kind)
{
    switch (kind)
    {
        case SVR_RATE_LIMIT_VIOLATION_INPUT_MOVE_BURST:
            return "input_move_burst";
        case SVR_RATE_LIMIT_VIOLATION_SWING_COOLDOWN:
            return "swing_cooldown";
        default:
            return "unknown";
    }
}

static void SvrRateLimitDisconnectSendClose(TCP_SOCKET socket, uint16_t code, const char* reason)
{
    uint8_t payload[2 + 123];
    int reason_len = reason ? (int)strlen(reason) : 0;
    if (reason_len > 123)
        reason_len = 123;
    payload[0] = (uint8_t)((code >> 8) & 0xFFu);
    payload[1] = (uint8_t)(code & 0xFFu);
    if (reason_len > 0)
        memcpy(payload + 2, reason, (size_t)reason_len);
    const int write_rc = WS_WRITE(socket, payload, 2 + reason_len, 0, 0x8);
    if (write_rc < 0)
    {
        printf("[ws-close-send] failed code=%u reason=%s\n",
               (unsigned)code,
               reason ? reason : "");
        fflush(stdout);
    }
}

static void SvrRateLimitDisconnectTransitionClient(ServerState* state, int ci, ClientPhase target)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return;
    SvrPlayerState* player = &state->players[ci];
    if (!SvrTransitionPhase(player, target))
        return;
    atomic_store_phase(&state->clients[ci].phase, target);
}
}

void SvrRateLimitDisconnectResetPlayer(SvrPlayerState* player)
{
    if (!player)
        return;
    player->input_packets_tick = 0;
    player->input_packets_this_tick = 0;
    player->rate_limit_violations = 0;
    memset(player->rl_violation_ticks, 0, sizeof(player->rl_violation_ticks));
    player->rl_violation_write = 0;
}

bool SvrRateLimitDisconnectRecordViolation(
    ServerState* state,
    int ci,
    SvrRateLimitViolationKind kind)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;

    SvrPlayerState* player = &state->players[ci];
    player->rate_limit_violations++;

    const uint32_t now = state->tick;
    const uint32_t write_idx = player->rl_violation_write % SVR_RATE_LIMIT_RING_SIZE;
    player->rl_violation_ticks[write_idx] = now + 1;
    player->rl_violation_write++;

    const uint32_t window_start =
        (now >= SVR_RATE_LIMIT_WINDOW_TICKS)
            ? (now - SVR_RATE_LIMIT_WINDOW_TICKS + 1)
            : 1u;
    const uint32_t now_stored = now + 1u;
    int violations_in_window = 0;
    for (int i = 0; i < SVR_RATE_LIMIT_RING_SIZE; i++)
    {
        const uint32_t tick = player->rl_violation_ticks[i];
        if (tick >= window_start && tick <= now_stored)
            violations_in_window++;
    }

    if (violations_in_window < SVR_RATE_LIMIT_MAX_VIOLATIONS)
        return false;

    printf("[FL-2481] rate_limit_disconnect ci=%d tick=%u violations_in_window=%d threshold=%d source=%s\n",
           ci,
           (unsigned)now,
           violations_in_window,
           SVR_RATE_LIMIT_MAX_VIOLATIONS,
           SvrRateLimitViolationKindName(kind));
    fflush(stdout);
    SvrRateLimitDisconnectSendClose(
        state->clients[ci].socket,
        1008,
        "rate_limit_exceeded");
    state->clients[ci].disconnect_ws_close_code = 1008;
    SvrRateLimitDisconnectTransitionClient(state, ci, CPHASE_DISCONNECTING);
    return true;
}

bool SvrRateLimitDisconnectObserveInputMovePacket(ServerState* state, int ci)
{
    if (!state || ci < 0 || ci >= SVR_MAX_CLIENTS)
        return false;

    SvrPlayerState* player = &state->players[ci];
    if (player->input_packets_tick != state->tick)
    {
        player->input_packets_tick = state->tick;
        player->input_packets_this_tick = 0;
    }
    if (player->input_packets_this_tick < 0xFF)
        player->input_packets_this_tick++;
    if (player->input_packets_this_tick != SVR_RATE_LIMIT_MAX_INPUT_PACKETS_PER_TICK + 1)
        return false;

    return SvrRateLimitDisconnectRecordViolation(
        state,
        ci,
        SVR_RATE_LIMIT_VIOLATION_INPUT_MOVE_BURST);
}
