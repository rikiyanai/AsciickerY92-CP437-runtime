#pragma once

#include <stdint.h>

struct ServerState;
struct SvrPlayerState;

enum SvrRateLimitViolationKind : uint8_t
{
    SVR_RATE_LIMIT_VIOLATION_INPUT_MOVE_BURST = 1,
    SVR_RATE_LIMIT_VIOLATION_SWING_COOLDOWN = 2,
};

void SvrRateLimitDisconnectResetPlayer(SvrPlayerState* player);
bool SvrRateLimitDisconnectObserveInputMovePacket(ServerState* state, int ci);
bool SvrRateLimitDisconnectRecordViolation(
    ServerState* state,
    int ci,
    SvrRateLimitViolationKind kind);
