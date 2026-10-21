#pragma once

#include <cstdint>

struct TransportStats {
    int connection_id = -1;
    uint64_t last_rx_us = 0;
    uint64_t last_tx_us = 0;
    uint64_t last_ping_us = 0;
    uint64_t last_pong_us = 0;
    uint32_t send_queue_depth = 0;
    uint32_t recv_queue_depth = 0;
    uint32_t dropped_packets = 0;
    uint32_t backpressure_count = 0;
};

