#pragma once

#include <cstdint>
#include <deque>
#include <unordered_map>
#include <vector>

#include "transport.h"
#include "transport_trace.h"

// Server-side WebSocket/TCP adapter that exposes the existing non-blocking
// server socket behavior through ITransport.
//
// IMPORTANT:
// - This is a pure transport adapter. It must not call gameplay directly.
// - This header intentionally avoids including game/render/world/physics.
//
// NOTE:
// The current authoritative server transport lives in server/server_tick.cpp
// (AcceptThreadEntry + IOThreadEntry). Phase 2 adds this adapter seam without
// ripping out that existing path yet.

// TCP_SOCKET type + cross-platform socket primitives (no protocol/game deps).
#include "../../server/platform_net.h"

class WebSocketServerTransport final : public ITransport {
public:
    struct Options {
        // Upper bound for queued outbound bytes per connection. If exceeded,
        // send() reports backpressure and drops the enqueue attempt.
        uint32_t max_send_queue_bytes;

        // Size of the non-blocking receive accumulator. Matches current
        // server_tick.cpp ClientIO::recv_buf (2048) by default.
        uint32_t recv_accumulator_bytes;

        Options()
            : max_send_queue_bytes(256u * 1024u)
            , recv_accumulator_bytes(2048u)
        {}
    };

    explicit WebSocketServerTransport(const Options& opt = Options());
    ~WebSocketServerTransport() override;

    // Registers a fully-upgraded, non-blocking WebSocket connection socket.
    // Returns the connection_id used for subsequent send/close/stats calls.
    //
    // This does NOT perform the HTTP->WebSocket handshake. The current server
    // does that in AcceptThreadEntry/SvrDoWSHandshake before switching the socket
    // to non-blocking mode.
    int add_connection(TCP_SOCKET socket);

    // ITransport
    bool poll(TransportEvent* out) override;
    TransportSendResult send(
        int connection_id, const uint8_t* data, int size, uint64_t trace_id) override;
    void close(int connection_id, int code, const char* reason) override;
    TransportStats stats(int connection_id) const override;

    void set_trace_sink(ITransportTraceSink* sink) { trace_sink_ = sink; }

private:
    struct PendingFrame {
        std::vector<uint8_t> bytes;
        size_t offset = 0;
        uint64_t trace_id = 0;
        uint64_t enqueue_us = 0;
    };

    struct Conn {
        int id = -1;
        TCP_SOCKET socket = INVALID_TCP_SOCKET;
        bool connected = false;
        uint64_t connected_us = 0;

        // Stats
        uint64_t last_rx_us = 0;
        uint64_t last_tx_us = 0;
        uint64_t last_ping_us = 0;
        uint64_t last_pong_us = 0;
        uint32_t dropped_packets = 0;
        uint32_t backpressure_count = 0;

        // Receive accumulator (non-blocking)
        std::vector<uint8_t> recv_buf;
        int recv_len = 0;

        // Outbound queue (non-blocking)
        std::deque<PendingFrame> send_q;
        uint32_t send_q_bytes = 0;
    };

    bool pump_io_and_maybe_emit(TransportEvent* out);
    bool try_emit_one_nonpacket_event(TransportEvent* out);
    bool try_recv_one_packet_event(Conn& c, TransportEvent* out);
    void flush_send_queue(Conn& c);

    Conn* get_conn(int connection_id);
    const Conn* get_conn(int connection_id) const;
    void disconnect_conn(Conn& c, int code, int err);

    Options opt_;
    int next_connection_id_ = 1;
    std::unordered_map<int, Conn> conns_;

    // Poll returns one event at a time. Non-packet events are queued here.
    std::deque<TransportEvent> pending_events_;

    // Packet payload storage for the *current* returned Packet event.
    // Lifetime: valid until the next call to poll().
    std::vector<uint8_t> packet_storage_;

    ITransportTraceSink* trace_sink_ = nullptr;
};
