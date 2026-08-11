#pragma once

#include <cstdint>

enum class NetTracePhase {
    Begin,
    End,
    Instant
};

struct NetTraceEvent {
    uint64_t trace_id = 0;
    const char* span = nullptr;
    NetTracePhase phase = NetTracePhase::Instant;
    uint64_t t_us = 0;
    int connection_id = -1;
    int packet_size = 0;
    int queue_depth = 0;
};

