// server/connection/server_connection_state.h — Server connection/transport state
//
// PURPOSE:
// Owns the transport-layer state of the Server: session identity,
// lag telemetry, and player slot capacity (not the roster itself).
//
// Embedded into the Server struct. Access via server->connection.*.

#pragma once

#include <stdint.h>

// Lag/ping telemetry (connection health, instrumentation-only trace fields).
struct NetworkLagTelemetry
{
    uint64_t last_lag = 0;
    int lag_ms = 0;
    int lag_rtt_raw_ms = 0;
    bool lag_wait = false;
    uint64_t lag_last_request_stamp = 0;
    uint64_t lag_last_response_stamp = 0;
    uint32_t lag_request_count = 0;
    uint32_t lag_request_send_fail_count = 0;
    uint32_t lag_response_count = 0;
    uint32_t lag_wait_timeout_count = 0;
    // S14/FL-2061: lag trace fields are INSTRUMENTATION ONLY
    uint32_t lag_trace_next_seq = 0;
    uint32_t lag_trace_request_seq = 0;
    uint64_t lag_trace_request_stamp = 0;
    uint64_t lag_trace_send_call_stamp = 0;
    uint32_t lag_trace_response_seq = 0;
    uint32_t lag_trace_client_send_us32 = 0;
	uint32_t lag_trace_server_rx_us32 = 0;
	uint32_t lag_trace_server_enqueue_us32 = 0;
	uint32_t lag_trace_server_flush_start_us32 = 0;
	uint32_t lag_trace_server_flush_finish_us32 = 0;
	uint64_t lag_trace_server_rx_epoch_us = 0;
	uint64_t lag_trace_server_enqueue_epoch_us = 0;
	uint64_t lag_trace_server_flush_start_epoch_us = 0;
	uint64_t lag_trace_server_flush_finish_epoch_us = 0;
	uint64_t lag_trace_packet_entry_stamp = 0;
    uint64_t lag_trace_proc_entry_stamp = 0;
    uint64_t lag_trace_proc_exit_stamp = 0;
    uint64_t lag_trace_packet_exit_stamp = 0;
    uint64_t lag_trace_server_stamp_at_proc = 0;
    bool lag_trace_packet_exit_valid = false;
    uint32_t lag_trace_client_send_to_packet_us = 0;
    uint32_t lag_trace_packet_to_proc_us = 0;
    uint32_t lag_trace_packet_proc_us = 0;
    uint32_t lag_trace_proc_stamp_minus_request_us = 0;
    uint32_t lag_trace_proc_entry_minus_request_us = 0;
    uint32_t lag_trace_server_rx_to_enqueue_us = 0;
    uint32_t lag_trace_server_enqueue_to_flush_start_us = 0;
    uint32_t lag_trace_server_flush_us = 0;
};

struct ServerConnection
{
    // ── Session identity ──
    int max_clients = 0;
    int local_id = -1;  // this client/player slot id assigned by server

    // ── Frame state ──
    uint64_t stamp = 0;

    // ── Lag telemetry ──
    NetworkLagTelemetry lag;
};
