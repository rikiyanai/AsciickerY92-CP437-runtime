#pragma once

#include <cstdint>

struct ServerSessionState {
    int connection_id = -1;
    int player_id = -1;
    bool connected = false;
    bool joined = false;
    bool authenticated = false;
    uint64_t connected_us = 0;
    uint64_t joined_us = 0;
    uint64_t last_intent_us = 0;
    uint64_t last_snapshot_us = 0;
    uint64_t last_packet_rx_us = 0;
    uint64_t last_packet_tx_us = 0;
};

