#pragma once

#include <cstdint>

struct ClientSessionState {
    int connection_id = -1;
    bool connected = false;
    bool joined = false;
    bool world_loaded = false;
    uint64_t connected_us = 0;
    uint64_t last_packet_rx_us = 0;
    uint64_t last_packet_tx_us = 0;
    uint64_t last_ping_us = 0;
    uint64_t last_pong_us = 0;
    uint32_t reconnect_attempts = 0;
};

