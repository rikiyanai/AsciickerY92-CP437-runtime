#pragma once

#include <cstdint>

// Transport/session layer connection lifecycle state. Compile-only boundary (Phase 1).
enum class ConnectionState : uint8_t {
    Disconnected = 0,
    Connecting = 1,
    Connected = 2,
    Closing = 3
};

