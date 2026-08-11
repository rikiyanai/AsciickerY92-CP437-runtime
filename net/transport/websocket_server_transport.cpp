#include "websocket_server_transport.h"

#include <cerrno>
#include <cstring>

#if !defined(_WIN32)
#include <poll.h>
#include <sys/socket.h>
#endif

#include "../../platform/time_backend.h"

namespace {

static inline uint64_t now_us()
{
    return a3dGetTime();
}

static inline bool is_would_block_err(int err)
{
#if defined(_WIN32)
    (void)err;
    return false;
#else
    return err == EAGAIN || err == EWOULDBLOCK;
#endif
}

// Helper: send() with platform-correct flags (no SIGPIPE where available).
static ssize_t send_no_sigpipe(TCP_SOCKET sock, const void* data, size_t len)
{
#if defined(_WIN32)
    return ::send(sock, (const char*)data, (int)len, 0);
#elif defined(__APPLE__)
    // server_tick.cpp sets SO_NOSIGPIPE at accept time.
    return ::send(sock, data, len, 0);
#else
    return ::send(sock, data, len, MSG_NOSIGNAL);
#endif
}

static int ws_header_size_for_payload(int payload_size)
{
    if (payload_size < 0) return -1;
    if (payload_size < 126) return 2;
    if (payload_size < 65536) return 4;
    return 10;
}

// Server-to-client frames are never masked.
static bool ws_frame_encode(std::vector<uint8_t>* out, const uint8_t* payload, int payload_size, int opcode)
{
    if (!out) return false;
    out->clear();
    if (!payload && payload_size != 0) return false;
    if (payload_size < 0) return false;

    const int hdr = ws_header_size_for_payload(payload_size);
    if (hdr <= 0) return false;
    out->resize((size_t)hdr + (size_t)payload_size);

    uint8_t* dst = out->data();
    dst[0] = (uint8_t)(0x80 | (opcode & 0x0F)); // FIN + opcode
    if (payload_size < 126)
    {
        dst[1] = (uint8_t)payload_size;
    }
    else if (payload_size < 65536)
    {
        dst[1] = 126;
        WS_WRITE_U16_BE(dst + 2, (uint16_t)payload_size);
    }
    else
    {
        dst[1] = 127;
        WS_WRITE_U64_BE(dst + 2, (uint64_t)payload_size);
    }

    if (payload_size > 0)
        memcpy(dst + hdr, payload, (size_t)payload_size);
    return true;
}

} // namespace

WebSocketServerTransport::WebSocketServerTransport(const Options& opt)
    : opt_(opt)
{
    packet_storage_.reserve(opt_.recv_accumulator_bytes);
}

WebSocketServerTransport::~WebSocketServerTransport()
{
    // Best-effort close: transport owns sockets it was given.
    for (auto& kv : conns_)
    {
        Conn& c = kv.second;
        if (c.socket != INVALID_TCP_SOCKET)
        {
            TCP_CLOSE(c.socket);
            c.socket = INVALID_TCP_SOCKET;
        }
    }
}

int WebSocketServerTransport::add_connection(TCP_SOCKET socket)
{
    const int id = next_connection_id_++;
    Conn c;
    c.id = id;
    c.socket = socket;
    c.connected = true;
    c.connected_us = now_us();
    c.last_rx_us = 0;
    c.last_tx_us = 0;
    c.last_ping_us = 0;
    c.last_pong_us = 0;
    c.recv_buf.resize(opt_.recv_accumulator_bytes);
    c.recv_len = 0;
    conns_.emplace(id, std::move(c));

    TransportEvent ev;
    ev.kind = TransportEventKind::Connected;
    ev.connection_id = id;
    ev.error_code = 0;
    ev.t_us = now_us();
    pending_events_.push_back(ev);

    return id;
}

WebSocketServerTransport::Conn* WebSocketServerTransport::get_conn(int connection_id)
{
    auto it = conns_.find(connection_id);
    return it == conns_.end() ? nullptr : &it->second;
}

const WebSocketServerTransport::Conn* WebSocketServerTransport::get_conn(int connection_id) const
{
    auto it = conns_.find(connection_id);
    return it == conns_.end() ? nullptr : &it->second;
}

void WebSocketServerTransport::disconnect_conn(Conn& c, int code, int err)
{
    if (!c.connected)
        return;

    c.connected = false;

    if (c.socket != INVALID_TCP_SOCKET)
    {
        TCP_CLOSE(c.socket);
        c.socket = INVALID_TCP_SOCKET;
    }

    const int effective_err = (code != 0) ? code : err;
    if (effective_err != 0)
    {
        TransportEvent ee;
        ee.kind = TransportEventKind::Error;
        ee.connection_id = c.id;
        ee.error_code = effective_err;
        ee.t_us = now_us();
        pending_events_.push_back(ee);
    }

    TransportEvent ev;
    ev.kind = TransportEventKind::Disconnected;
    ev.connection_id = c.id;
    ev.error_code = effective_err;
    ev.t_us = now_us();
    pending_events_.push_back(ev);
}

bool WebSocketServerTransport::try_emit_one_nonpacket_event(TransportEvent* out)
{
    if (!out) return false;
    if (pending_events_.empty()) return false;
    *out = pending_events_.front();
    pending_events_.pop_front();
    return true;
}

// Parse + emit exactly one Packet event if a complete WS data frame is available.
// This mirrors the current non-blocking accumulator parsing in server/server_tick.cpp
// but returns TransportEvent instead of pushing into the tick-thread SPSC ring.
bool WebSocketServerTransport::try_recv_one_packet_event(Conn& c, TransportEvent* out)
{
    if (!out) return false;
    if (c.socket == INVALID_TCP_SOCKET) return false;
    if (!c.connected) return false;

    // Drain socket into accumulator.
    while (c.recv_len < (int)c.recv_buf.size())
    {
        const int space = (int)c.recv_buf.size() - c.recv_len;
        if (space <= 0) break;
        const ssize_t r = ::recv(c.socket, (char*)c.recv_buf.data() + c.recv_len, (size_t)space, 0);
        if (r > 0)
        {
            c.recv_len += (int)r;
            c.last_rx_us = now_us();
            // Treat any received data as keepalive activity.
            c.last_pong_us = c.last_rx_us;
            continue;
        }
        if (r == 0)
        {
            disconnect_conn(c, /*code*/0, /*err*/0);
            return false;
        }
        const int err = errno;
        if (is_would_block_err(err))
            break;

        disconnect_conn(c, /*code*/0, err);
        return false;
    }

    uint8_t* rb = c.recv_buf.data();
    const int rl = c.recv_len;
    if (rl < 2) return false;

    const int opcode = rb[0] & 0x0F;
    const bool fin = (rb[0] & 0x80) != 0;
    const bool masked = (rb[1] & 0x80) != 0;
    uint64_t payload_len = (uint64_t)(rb[1] & 0x7F);
    int hdr_off = 2;

    if (payload_len == 126)
    {
        if (rl < 4) return false;
        payload_len = ((uint64_t)rb[2] << 8) | (uint64_t)rb[3];
        hdr_off = 4;
    }
    else if (payload_len == 127)
    {
        if (rl < 10) return false;
        payload_len = ((uint64_t)rb[2] << 56) | ((uint64_t)rb[3] << 48) |
                      ((uint64_t)rb[4] << 40) | ((uint64_t)rb[5] << 32) |
                      ((uint64_t)rb[6] << 24) | ((uint64_t)rb[7] << 16) |
                      ((uint64_t)rb[8] << 8)  | (uint64_t)rb[9];
        hdr_off = 10;
    }

    const int mask_len = masked ? 4 : 0;
    const int frame_total = hdr_off + mask_len + (int)payload_len;
    if (payload_len > (uint64_t)(c.recv_buf.size() - (size_t)hdr_off))
    {
        // Oversized payload for our accumulator: drop connection as protocol error.
        c.dropped_packets++;
        disconnect_conn(c, /*code*/1002 /*protocol error*/, /*err*/0);
        return false;
    }
    if (rl < frame_total) return false; // incomplete frame

    const uint8_t* mask_key = masked ? (rb + hdr_off) : nullptr;
    const uint8_t* payload_ptr = rb + hdr_off + mask_len;

    // Control frames.
    if (opcode == 0x8) // close
    {
        disconnect_conn(c, /*code*/0, /*err*/0);
    }
    else if (opcode == 0x9) // ping
    {
        c.last_ping_us = now_us();
        // Reply with pong echoing the (possibly masked) payload.
        uint8_t ping_data[125];
        int plen = (int)payload_len;
        if (plen > 125) plen = 125;
        if (plen > 0)
        {
            if (masked && mask_key)
            {
                for (int i = 0; i < plen; i++)
                    ping_data[i] = payload_ptr[i] ^ mask_key[i & 3];
            }
            else
            {
                memcpy(ping_data, payload_ptr, (size_t)plen);
            }
        }

        PendingFrame pf;
        pf.trace_id = 0;
        pf.enqueue_us = now_us();
        ws_frame_encode(&pf.bytes, ping_data, plen, 0xA /*pong*/);
        c.send_q_bytes += (uint32_t)pf.bytes.size();
        c.send_q.push_back(std::move(pf));
        flush_send_queue(c);

        TransportEvent ev;
        ev.kind = TransportEventKind::Ping;
        ev.connection_id = c.id;
        ev.error_code = 0;
        ev.t_us = now_us();
        *out = ev;
    }
    else if (opcode == 0xA) // pong
    {
        c.last_pong_us = now_us();

        TransportEvent ev;
        ev.kind = TransportEventKind::Pong;
        ev.connection_id = c.id;
        ev.error_code = 0;
        ev.t_us = c.last_pong_us;
        *out = ev;
    }
    else if (opcode == 0x1 || opcode == 0x2 || opcode == 0x0) // text/binary/continuation
    {
        if (!fin)
        {
            // Current server path does not support fragmented messages; treat as error.
            c.dropped_packets++;
            disconnect_conn(c, /*code*/1002, /*err*/0);
        }
        else
        {
            // Unmask into packet_storage_ (lifetime until next poll()).
            packet_storage_.assign(payload_ptr, payload_ptr + (size_t)payload_len);
            if (masked && mask_key)
            {
                for (size_t i = 0; i < packet_storage_.size(); i++)
                    packet_storage_[i] ^= mask_key[i & 3];
            }

            TransportEvent ev;
            ev.kind = TransportEventKind::Packet;
            ev.connection_id = c.id;
            ev.error_code = 0;
            ev.t_us = now_us();
            ev.packet.trace_id = 0; // Server protocol does not universally carry trace_id yet.
            ev.packet.connection_id = c.id;
            ev.packet.data = packet_storage_.data();
            ev.packet.size = (int)packet_storage_.size();
            ev.packet.recv_us = ev.t_us;
            *out = ev;
        }
    }
    else
    {
        // Unknown opcode: ignore.
    }

    // Consume frame.
    const int remaining = c.recv_len - frame_total;
    if (remaining > 0)
        memmove(c.recv_buf.data(), c.recv_buf.data() + frame_total, (size_t)remaining);
    c.recv_len = remaining;

    // If we wrote an event above, return true.
    return out->kind != TransportEventKind::Error;
}

void WebSocketServerTransport::flush_send_queue(Conn& c)
{
    if (!c.connected || c.socket == INVALID_TCP_SOCKET)
        return;

    while (!c.send_q.empty())
    {
        PendingFrame& pf = c.send_q.front();
        if (pf.offset >= pf.bytes.size())
        {
            c.send_q.pop_front();
            continue;
        }

        const uint8_t* p = pf.bytes.data() + pf.offset;
        const size_t remaining = pf.bytes.size() - pf.offset;
        const ssize_t sent = send_no_sigpipe(c.socket, p, remaining);
        if (sent > 0)
        {
            pf.offset += (size_t)sent;
            c.last_tx_us = now_us();
            if (pf.offset >= pf.bytes.size())
            {
                // reduce queued bytes
                if (c.send_q_bytes >= (uint32_t)pf.bytes.size())
                    c.send_q_bytes -= (uint32_t)pf.bytes.size();
                else
                    c.send_q_bytes = 0;
                c.send_q.pop_front();
            }
            continue;
        }
        if (sent == 0)
        {
            // Treat as disconnect.
            disconnect_conn(c, /*code*/0, /*err*/0);
            return;
        }
        const int err = errno;
        if (is_would_block_err(err))
        {
            c.backpressure_count++;
            TransportEvent ev;
            ev.kind = TransportEventKind::Backpressure;
            ev.connection_id = c.id;
            ev.error_code = err;
            ev.t_us = now_us();
            pending_events_.push_back(ev);
            return;
        }

        disconnect_conn(c, /*code*/0, err);
        return;
    }
}

bool WebSocketServerTransport::pump_io_and_maybe_emit(TransportEvent* out)
{
    if (!out) return false;

    // Prefer emitting already-queued non-packet events first.
    if (try_emit_one_nonpacket_event(out))
        return true;

    // Pump one read event (or control event) from any connection.
    for (auto& kv : conns_)
    {
        Conn& c = kv.second;
        if (!c.connected) continue;

        // Try to flush any pending sends first; it may generate Backpressure/Disconnected events.
        flush_send_queue(c);
        if (try_emit_one_nonpacket_event(out))
            return true;

        // Then try to receive one WS frame and turn it into an event.
        TransportEvent ev;
        ev.kind = TransportEventKind::Error;
        if (try_recv_one_packet_event(c, &ev))
        {
            *out = ev;
            return true;
        }
        if (try_emit_one_nonpacket_event(out))
            return true;
    }

    return false;
}

bool WebSocketServerTransport::poll(TransportEvent* out)
{
    if (!out) return false;

    // Invalidate previous packet pointer storage.
    packet_storage_.clear();

    if (pump_io_and_maybe_emit(out))
        return true;

#if !defined(_WIN32)
    // If no immediate work, do a 0-timeout poll to detect writability/readability
    // and allow subsequent poll() calls to make progress without busy loops.
    // (This class is not wired yet; keep behavior conservative.)
    std::vector<struct pollfd> fds;
    fds.reserve(conns_.size());
    for (const auto& kv : conns_)
    {
        const Conn& c = kv.second;
        if (!c.connected || c.socket == INVALID_TCP_SOCKET)
            continue;
        struct pollfd pfd;
        pfd.fd = c.socket;
        pfd.events = (short)(POLLIN | (c.send_q.empty() ? 0 : POLLOUT));
        pfd.revents = 0;
        fds.push_back(pfd);
    }
    if (!fds.empty())
        ::poll(fds.data(), (nfds_t)fds.size(), 0);
#endif

    return pump_io_and_maybe_emit(out);
}

TransportSendResult WebSocketServerTransport::send(
    int connection_id, const uint8_t* data, int size, uint64_t trace_id)
{
    TransportSendResult r;
    r.ok = false;
    r.queued = false;
    r.would_block = false;
    r.error_code = 0;
    r.bytes = 0;
    r.enqueue_us = now_us();
    r.flush_us = 0;

    Conn* c = get_conn(connection_id);
    if (!c || !c->connected || c->socket == INVALID_TCP_SOCKET || !data || size < 0)
    {
        r.error_code = EINVAL;
        return r;
    }

    PendingFrame pf;
    pf.trace_id = trace_id;
    pf.enqueue_us = r.enqueue_us;
    if (!ws_frame_encode(&pf.bytes, data, size, 0x2 /*binary*/))
    {
        r.error_code = EINVAL;
        return r;
    }

    // Backpressure: refuse enqueue if it would exceed the configured queue cap.
    if (c->send_q_bytes + (uint32_t)pf.bytes.size() > opt_.max_send_queue_bytes)
    {
        c->backpressure_count++;
        c->dropped_packets++;
        r.would_block = true;
        r.error_code = EAGAIN;
        r.bytes = 0;
        pending_events_.push_back(TransportEvent{
            TransportEventKind::Backpressure, connection_id, EAGAIN, now_us(), TransportPacket{}});
        return r;
    }

    // Fast path: if no queued writes, try immediate send.
    const uint64_t flush_start = now_us();
    if (c->send_q.empty())
    {
        const ssize_t sent = send_no_sigpipe(c->socket, pf.bytes.data(), pf.bytes.size());
        if (sent == (ssize_t)pf.bytes.size())
        {
            r.ok = true;
            r.bytes = size;
            r.flush_us = now_us() - flush_start;
            c->last_tx_us = now_us();
            return r;
        }
        if (sent > 0)
        {
            pf.offset = (size_t)sent;
            c->send_q_bytes += (uint32_t)pf.bytes.size();
            c->send_q.push_back(std::move(pf));
            r.ok = true;
            r.queued = true;
            r.bytes = size;
            r.flush_us = 0;
            c->last_tx_us = now_us();
            return r;
        }
        if (sent == 0)
        {
            disconnect_conn(*c, /*code*/0, /*err*/0);
            r.error_code = 0;
            return r;
        }

        const int err = errno;
        if (is_would_block_err(err))
        {
            c->backpressure_count++;
            c->send_q_bytes += (uint32_t)pf.bytes.size();
            c->send_q.push_back(std::move(pf));
            r.ok = true;
            r.queued = true;
            r.would_block = true;
            r.error_code = err;
            r.bytes = size;
            pending_events_.push_back(TransportEvent{
                TransportEventKind::Backpressure, connection_id, err, now_us(), TransportPacket{}});
            return r;
        }

        disconnect_conn(*c, /*code*/0, err);
        r.error_code = err;
        return r;
    }

    // Slow path: already queued, append.
    c->send_q_bytes += (uint32_t)pf.bytes.size();
    c->send_q.push_back(std::move(pf));
    r.ok = true;
    r.queued = true;
    r.bytes = size;
    return r;
}

void WebSocketServerTransport::close(int connection_id, int code, const char* reason)
{
    (void)reason;
    Conn* c = get_conn(connection_id);
    if (!c) return;
    disconnect_conn(*c, code, 0);
}

TransportStats WebSocketServerTransport::stats(int connection_id) const
{
    TransportStats s;
    const Conn* c = get_conn(connection_id);
    if (!c) return s;

    s.connection_id = connection_id;
    s.last_rx_us = c->last_rx_us;
    s.last_tx_us = c->last_tx_us;
    s.last_ping_us = c->last_ping_us;
    s.last_pong_us = c->last_pong_us;
    s.send_queue_depth = (uint32_t)c->send_q.size();
    s.recv_queue_depth = (uint32_t)c->recv_len;
    s.dropped_packets = c->dropped_packets;
    s.backpressure_count = c->backpressure_count;
    return s;
}
