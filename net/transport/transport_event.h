#pragma once

#include <cstdint>

enum class TransportEventKind {
    Connected,
    Disconnected,
    Packet,
    Error,
    Backpressure,
    Ping,
    Pong
};

struct TransportPacket {
    uint64_t trace_id = 0;
    int connection_id = -1;
    const uint8_t* data = nullptr;
    int size = 0;
    uint64_t recv_us = 0;
};

struct TransportEvent {
    TransportEventKind kind = TransportEventKind::Error;
    int connection_id = -1;
    int error_code = 0;
    uint64_t t_us = 0;
    TransportPacket packet = {};
};

struct TransportSendResult {
    bool ok = false;
    bool queued = false;
    bool would_block = false;
    int error_code = 0;
    int bytes = 0;
    uint64_t enqueue_us = 0;
    uint64_t flush_us = 0;
};

