// protocol_lag_probe.h — Latency probe wire protocol structs
//
// Extracted from server/multiplayer_protocol.h.
// REQ_LAG/RSP_LAG are the instrumentation structs from the lag-trace branch
// (abef93d6). The struct layout is correct; the defects are in how timestamps
// are written — FL-2061 (flush-stamp dead code), FL-2062 (finish stamp always 0),
// FL-2065 (wire-size breaks legacy 4-byte senders). Fix FL-2061..2073 before
// treating lag_trace_* timestamps as valid proof.
// No socket/platform dependencies.
//
// SEE ALSO: multiplayer_protocol.h

#pragma once

#include <stdint.h>

#pragma pack(push,1)

struct STRUCT_REQ_LAG
{
	uint8_t token; // 'L'
	uint8_t stamp[3];           // legacy 4-byte ping stamp; old clients may send only token+stamp
	uint32_t trace_seq;         // optional trace sequence for 12-byte latency probes
	uint32_t client_send_us32;  // client-side send timestamp modulo 2^32 microseconds
};

struct STRUCT_RSP_LAG
{
	uint8_t token; // 'l'
	uint8_t stamp[3];
	uint32_t trace_seq;
	uint32_t client_send_us32;
	uint32_t server_rx_us32;
	uint32_t server_enqueue_us32;
	uint32_t server_flush_start_us32;  // stamped into packet immediately before IO-owned flush
	uint32_t server_flush_finish_us32; // transmitted as 0; true finish is authoritative_state.json only
	uint64_t server_rx_epoch_us;       // CLOCK_REALTIME, diagnostic-only clock-offset split
	uint64_t server_enqueue_epoch_us;
	uint64_t server_flush_start_epoch_us;
	uint64_t server_flush_finish_epoch_us; // transmitted as 0; true finish is authoritative_state.json only
	uint32_t prev_flush_trace_seq; // previous echo; lets the next response backfill flush-finish
	uint32_t prev_server_flush_finish_us32;
	uint64_t prev_server_flush_finish_epoch_us;
};
static_assert(sizeof(STRUCT_REQ_LAG) == 12, "STRUCT_REQ_LAG size changed unexpectedly");
static_assert(sizeof(STRUCT_RSP_LAG) == 76, "STRUCT_RSP_LAG size changed unexpectedly");

#pragma pack(pop)
