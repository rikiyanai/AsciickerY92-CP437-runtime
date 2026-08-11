#pragma once

// network_lag_telemetry.h — IO-thread lag echo measurement and control-frame queue
//
// Owns:
//   - IO-thread control frame queue (lag echo + WebSocket pong)
//   - Lag echo measurement and tracing
//   - IOFlushControlFrames: flush queued control frames to the socket
//
// These functions operate on ClientIO and are called exclusively from the IO
// thread (IOThreadEntry in server_tick.cpp / server_state.h).

#include <stdint.h>

#include "server_state.h" // ClientIO full definition needed for IOLagEchoRspPayload

// ── Control frame queue ─────────────────────────────────────────

// Check if this client has any queued lag echo frame.
bool IOHasQueuedLagEcho(const struct ClientIO* cio);

// Note a control frame drop for diagnostics.
void IONoteControlDrop(struct ClientIO* cio, bool lag_echo, bool pong);

// Drop the oldest queued lag echo frame to make room for a pong.
bool IODropOldestQueuedLagEcho(struct ClientIO* cio);

// Queue a control frame (lag echo or pong) into the per-client ring.
// Returns true if queued, false if the ring is full.
bool IOQueueControlFrame(struct ClientIO* cio,
                         const uint8_t* frame, int frame_len,
                         bool lag_echo);

// Get the STRUCT_RSP_LAG payload from a control frame that is a lag echo.
struct STRUCT_RSP_LAG* IOLagEchoRspPayload(struct ClientIO::ControlFrame* frame);

// Flush queued control frames to the socket (non-blocking). Returns
// true if all frames were flushed, false if the socket write stalled.
// Called per-tick in the IO-thread send path.
bool IOFlushControlFrames(struct ServerState* state, int ci,
                          struct ClientIO* cio, int max_frames);
