#include <assert.h>
#include <stdint.h>
#include "network_ingest.h"
#include "game.h"
#include "platform/time_backend.h"
#include "server/multiplayer_protocol.h"

#ifdef __EMSCRIPTEN__
extern uint64_t GetTime();
#endif

bool ApplyLagPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	(void)game; // reserved for future per-game lag routing
	if (size != (int)sizeof(STRUCT_RSP_LAG))
		return true;
	STRUCT_RSP_LAG* lag = (STRUCT_RSP_LAG*)ptr;
	uint64_t proc_entry_stamp =
#ifdef __EMSCRIPTEN__
		GetTime()
#else
		a3dGetTime()
#endif
	;
	uint32_t s1 = 0;
	s1 |= lag->stamp[0] << 8;
	s1 |= lag->stamp[1] << 16;
	s1 |= lag->stamp[2] << 24;

	uint32_t s2 = (uint32_t)server->connection.stamp << 8;
	int latency = (s2 - s1 + 128) >> 8;

	auto u32_delta = [](uint32_t newer, uint32_t older) -> uint32_t
	{
		return (uint32_t)(newer - older);
	};

	int raw_lag_ms = (latency + 500) / 1000;
	server->connection.lag.lag_rtt_raw_ms = raw_lag_ms;
	server->connection.lag.lag_ms = raw_lag_ms;
	// GUARDRAIL: lag_ms must equal lag_rtt_raw_ms for every sample.
	// If you add display smoothing, clamping, or filtering that makes
	// lag_ms diverge from lag_rtt_raw_ms, the proof gates lie and
	// diagnostics report false green. This assert catches that at
	// runtime in debug builds. Do not remove.
	assert(server->connection.lag.lag_ms == server->connection.lag.lag_rtt_raw_ms);
	server->connection.lag.lag_wait = false;
	server->connection.lag.lag_last_response_stamp = server->connection.stamp;
	server->connection.lag.lag_response_count++;
	server->connection.lag.lag_trace_response_seq = lag->trace_seq;
	server->connection.lag.lag_trace_client_send_us32 = lag->client_send_us32;
	server->connection.lag.lag_trace_server_rx_us32 = lag->server_rx_us32;
	server->connection.lag.lag_trace_server_enqueue_us32 = lag->server_enqueue_us32;
	server->connection.lag.lag_trace_server_flush_start_us32 = lag->server_flush_start_us32;
	server->connection.lag.lag_trace_server_flush_finish_us32 = lag->server_flush_finish_us32;
	server->connection.lag.lag_trace_server_rx_epoch_us = lag->server_rx_epoch_us;
	server->connection.lag.lag_trace_server_enqueue_epoch_us = lag->server_enqueue_epoch_us;
	server->connection.lag.lag_trace_server_flush_start_epoch_us = lag->server_flush_start_epoch_us;
	server->connection.lag.lag_trace_server_flush_finish_epoch_us = lag->server_flush_finish_epoch_us;
	server->connection.lag.lag_trace_proc_entry_stamp = proc_entry_stamp;
	server->connection.lag.lag_trace_server_stamp_at_proc = server->connection.stamp;
	server->connection.lag.lag_trace_client_send_to_packet_us =
		server->connection.lag.lag_trace_packet_entry_stamp != 0
			? u32_delta((uint32_t)server->connection.lag.lag_trace_packet_entry_stamp,
				lag->client_send_us32)
			: 0;
	server->connection.lag.lag_trace_packet_to_proc_us =
		server->connection.lag.lag_trace_packet_entry_stamp != 0
			? u32_delta((uint32_t)proc_entry_stamp,
				(uint32_t)server->connection.lag.lag_trace_packet_entry_stamp)
			: 0;
	server->connection.lag.lag_trace_proc_stamp_minus_request_us =
		u32_delta((uint32_t)server->connection.stamp, lag->client_send_us32);
	server->connection.lag.lag_trace_proc_entry_minus_request_us =
		u32_delta((uint32_t)proc_entry_stamp, lag->client_send_us32);
	server->connection.lag.lag_trace_server_rx_to_enqueue_us =
		(lag->server_rx_us32 && lag->server_enqueue_us32)
			? u32_delta(lag->server_enqueue_us32, lag->server_rx_us32)
			: 0;
	server->connection.lag.lag_trace_server_enqueue_to_flush_start_us =
		(lag->server_enqueue_us32 && lag->server_flush_start_us32)
			? u32_delta(lag->server_flush_start_us32, lag->server_enqueue_us32)
			: 0;
	server->connection.lag.lag_trace_server_flush_us =
		(lag->server_flush_start_us32 && lag->server_flush_finish_us32)
			? u32_delta(lag->server_flush_finish_us32, lag->server_flush_start_us32)
			: 0;
	server->connection.lag.lag_trace_proc_exit_stamp =
#ifdef __EMSCRIPTEN__
		GetTime();
#else
		a3dGetTime();
#endif
	return true;
}
