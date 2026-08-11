#pragma once

#include <cstdint>

#include "transport.h"

// Browser WebSocket transport adapter (Emscripten/Web build).
//
// Owns WebSocket lifecycle witness fields (timestamps, close metadata, bufferedAmount,
// visibility state) and converts JS callbacks into ITransport events.
//
// This is transport-only: it must not include or call gameplay / render / world / physics.

struct WebSocketClientTransportWebWitness {
    uint64_t ws_open_us = 0;
    uint64_t ws_onmessage_us = 0;
    uint64_t ws_close_us = 0;
    uint64_t ws_error_us = 0;

    uint32_t buffered_amount = 0;

    // Sticky last-known visibilityState (as an ASCII string owned by the transport).
    // Empty string means unavailable.
    const char* visibility_state = "";

    int close_code = 0;
    int was_clean = 0; // 0/1 if known, else 0 with ws_close_us==0
};

// Singleton-style access for the current web runtime.
ITransport* WebSocketClientTransportWeb_Get();

// Read current witness fields (pointers valid for the process lifetime).
WebSocketClientTransportWebWitness WebSocketClientTransportWeb_GetWitness();

// JS -> C++ callback front doors (called from game_web.html).
// These only enqueue transport events; they do not call gameplay Packet().
extern "C" {
void WebSocketClientTransportWeb_Reset();
void WebSocketClientTransportWeb_OnOpen(double open_us,
                                        uint32_t buffered_amount,
                                        const char* visibility_state);
void WebSocketClientTransportWeb_OnMessage(const uint8_t* data,
                                           int size,
                                           double onmessage_us,
                                           uint32_t buffered_amount,
                                           const char* visibility_state,
                                           double trace_id);
// Metadata-only message witness. Enqueues a Packet event with size/timestamps but no payload bytes.
// Intended for low-overhead use until the transport fully owns packet delivery.
void WebSocketClientTransportWeb_OnMessageMeta(int size,
                                              double onmessage_us,
                                              uint32_t buffered_amount,
                                              const char* visibility_state,
                                              double trace_id);
void WebSocketClientTransportWeb_OnClose(double close_us,
                                         int close_code,
                                         int was_clean,
                                         uint32_t buffered_amount,
                                         const char* visibility_state);
void WebSocketClientTransportWeb_OnError(double error_us,
                                         uint32_t buffered_amount,
                                         const char* visibility_state);
void WebSocketClientTransportWeb_OnBackpressure(double t_us,
                                                uint32_t buffered_amount,
                                                const char* visibility_state,
                                                int error_code);
}
