#include "websocket_client_transport_web.h"

#include <algorithm>
#include <deque>
#include <string>
#include <utility>
#include <vector>

#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#endif

namespace {

static constexpr int kWebConnectionId = 1;

struct QueuedEvent {
    TransportEvent ev;
    std::vector<uint8_t> payload; // only for Packet events
};

class WebSocketClientTransportWeb final : public ITransport {
public:
    WebSocketClientTransportWeb() = default;
    ~WebSocketClientTransportWeb() override = default;

    void reset()
    {
        q_.clear();
        witness_ = {};
        visibility_state_.clear();
        witness_.visibility_state = "";
        stats_ = {};
        stats_.connection_id = kWebConnectionId;
    }

    void on_open(double open_us, uint32_t buffered_amount, const char* visibility_state)
    {
        witness_.ws_open_us = (uint64_t)open_us;
        witness_.buffered_amount = buffered_amount;
        set_visibility(visibility_state);

        TransportEvent ev;
        ev.kind = TransportEventKind::Connected;
        ev.connection_id = kWebConnectionId;
        ev.error_code = 0;
        ev.t_us = witness_.ws_open_us;
        q_.push_back(QueuedEvent{ev, {}});
    }

    void on_message(const uint8_t* data,
                    int size,
                    double onmessage_us,
                    uint32_t buffered_amount,
                    const char* visibility_state,
                    double trace_id)
    {
        witness_.ws_onmessage_us = (uint64_t)onmessage_us;
        witness_.buffered_amount = buffered_amount;
        set_visibility(visibility_state);

        if (size <= 0)
            return;

        QueuedEvent qe;
        qe.ev.kind = TransportEventKind::Packet;
        qe.ev.connection_id = kWebConnectionId;
        qe.ev.error_code = 0;
        qe.ev.t_us = witness_.ws_onmessage_us;
        qe.ev.packet.trace_id = (uint64_t)trace_id;
        qe.ev.packet.connection_id = kWebConnectionId;
        qe.ev.packet.data = nullptr; // patched on poll()
        qe.ev.packet.size = size;
        qe.ev.packet.recv_us = witness_.ws_onmessage_us;

        if (data)
            qe.payload.assign(data, data + size);
        q_.push_back(std::move(qe));

        stats_.last_rx_us = witness_.ws_onmessage_us;
        stats_.recv_queue_depth = (uint32_t)q_.size();
    }

    void on_message_meta(int size,
                         double onmessage_us,
                         uint32_t buffered_amount,
                         const char* visibility_state,
                         double trace_id)
    {
        on_message(/*data=*/nullptr, size, onmessage_us, buffered_amount, visibility_state, trace_id);
    }

    void on_close(double close_us,
                  int close_code,
                  int was_clean,
                  uint32_t buffered_amount,
                  const char* visibility_state)
    {
        witness_.ws_close_us = (uint64_t)close_us;
        witness_.close_code = close_code;
        witness_.was_clean = was_clean;
        witness_.buffered_amount = buffered_amount;
        set_visibility(visibility_state);

        TransportEvent ev;
        ev.kind = TransportEventKind::Disconnected;
        ev.connection_id = kWebConnectionId;
        ev.error_code = close_code;
        ev.t_us = witness_.ws_close_us;
        q_.push_back(QueuedEvent{ev, {}});
    }

    void on_error(double error_us, uint32_t buffered_amount, const char* visibility_state)
    {
        witness_.ws_error_us = (uint64_t)error_us;
        witness_.buffered_amount = buffered_amount;
        set_visibility(visibility_state);

        TransportEvent ev;
        ev.kind = TransportEventKind::Error;
        ev.connection_id = kWebConnectionId;
        ev.error_code = 1;
        ev.t_us = witness_.ws_error_us;
        q_.push_back(QueuedEvent{ev, {}});
    }

    void on_backpressure(double t_us,
                         uint32_t buffered_amount,
                         const char* visibility_state,
                         int error_code)
    {
        witness_.buffered_amount = buffered_amount;
        set_visibility(visibility_state);

        TransportEvent ev;
        ev.kind = TransportEventKind::Backpressure;
        ev.connection_id = kWebConnectionId;
        ev.error_code = error_code;
        ev.t_us = (uint64_t)t_us;
        q_.push_back(QueuedEvent{ev, {}});

        stats_.backpressure_count++;
    }

    bool poll(TransportEvent* out) override
    {
        if (!out) return false;
        if (q_.empty()) return false;

        QueuedEvent qe = std::move(q_.front());
        q_.pop_front();

        // Maintain packet storage lifetime until the next poll().
        packet_storage_.clear();

        if (qe.ev.kind == TransportEventKind::Packet)
        {
            const int advertised_size = qe.ev.packet.size;
            packet_storage_ = std::move(qe.payload);
            qe.ev.packet.data = packet_storage_.data();
            qe.ev.packet.size = (int)packet_storage_.size();
            if (packet_storage_.empty())
            {
                // Meta-only event: payload bytes were not captured.
                qe.ev.packet.data = nullptr;
                qe.ev.packet.size = advertised_size;
            }
        }

        stats_.recv_queue_depth = (uint32_t)q_.size();
        *out = qe.ev;
        return true;
    }

    TransportSendResult send(int connection_id,
                             const uint8_t* data,
                             int size,
                             uint64_t trace_id) override
    {
        (void)trace_id;

        TransportSendResult r;
        r.ok = false;
        r.queued = false;
        r.would_block = false;
        r.error_code = 0;
        r.bytes = 0;
        r.enqueue_us = 0;
        r.flush_us = 0;

        if (connection_id != kWebConnectionId || !data || size <= 0)
        {
            r.error_code = 22; // EINVAL-ish without errno headers.
            return r;
        }

#ifdef __EMSCRIPTEN__
        // Send bytes via JS WebSocket if present. This does NOT change gameplay packet handling;
        // it is a transport seam (not wired yet by default call sites).
        int rc = EM_ASM_INT(
            {
                var ptr = $0 >>> 0;
                var len = $1 | 0;
                var conn = (typeof ak_connection !== "undefined") ? ak_connection : null;
                if (!conn || typeof WebSocket === "undefined") return -2;
                if (conn.readyState !== WebSocket.OPEN) return -3;
                try {
                    var view = new Uint8Array(Module.HEAPU8.buffer, ptr, len);
                    // Snapshot out of wasm memory to avoid mutation on reuse.
                    var payload = new Uint8Array(view);
                    conn.send(payload);
                    return (conn.bufferedAmount || 0) | 0;
                } catch (e) {
                    return -4;
                }
            },
            (int)(uintptr_t)data,
            size);

        r.enqueue_us = (uint64_t)(emscripten_get_now() * 1000.0);
        if (rc >= 0)
        {
            r.ok = true;
            r.queued = true;
            r.bytes = size;
            witness_.buffered_amount = (uint32_t)rc;
            stats_.last_tx_us = r.enqueue_us;
            return r;
        }

        // Negative rc encodes a transport-level failure.
        r.error_code = rc;
        if (rc == -3)
        {
            // Socket not open: treat as backpressure-like for now (caller can retry after reconnect).
            r.would_block = true;
            on_backpressure(/*t_us=*/emscripten_get_now() * 1000.0,
                            witness_.buffered_amount,
                            witness_.visibility_state,
                            rc);
        }
        else
        {
            on_error(/*error_us=*/emscripten_get_now() * 1000.0,
                     witness_.buffered_amount,
                     witness_.visibility_state);
        }
        return r;
#else
        // Non-web builds: no-op stub.
        r.error_code = -1;
        return r;
#endif
    }

    void close(int connection_id, int code, const char* reason) override
    {
        (void)reason;
        if (connection_id != kWebConnectionId)
            return;
        on_close(/*close_us=*/0, code, /*was_clean=*/0, witness_.buffered_amount, witness_.visibility_state);
    }

    TransportStats stats(int connection_id) const override
    {
        if (connection_id != kWebConnectionId)
            return TransportStats{};
        return stats_;
    }

    WebSocketClientTransportWebWitness witness() const { return witness_; }

private:
    void set_visibility(const char* s)
    {
        if (!s || !s[0])
            return;
        visibility_state_ = s;
        witness_.visibility_state = visibility_state_.c_str();
    }

    std::deque<QueuedEvent> q_;
    std::vector<uint8_t> packet_storage_;
    WebSocketClientTransportWebWitness witness_;
    std::string visibility_state_;
    TransportStats stats_;
};

static WebSocketClientTransportWeb g_transport;

} // namespace

ITransport* WebSocketClientTransportWeb_Get()
{
    return &g_transport;
}

WebSocketClientTransportWebWitness WebSocketClientTransportWeb_GetWitness()
{
    return g_transport.witness();
}

extern "C" {

void WebSocketClientTransportWeb_Reset()
{
    g_transport.reset();
}

void WebSocketClientTransportWeb_OnOpen(double open_us,
                                        uint32_t buffered_amount,
                                        const char* visibility_state)
{
    g_transport.on_open(open_us, buffered_amount, visibility_state);
}

void WebSocketClientTransportWeb_OnMessage(const uint8_t* data,
                                           int size,
                                           double onmessage_us,
                                           uint32_t buffered_amount,
                                           const char* visibility_state,
                                           double trace_id)
{
    g_transport.on_message(data, size, onmessage_us, buffered_amount, visibility_state, trace_id);
}

void WebSocketClientTransportWeb_OnMessageMeta(int size,
                                              double onmessage_us,
                                              uint32_t buffered_amount,
                                              const char* visibility_state,
                                              double trace_id)
{
    g_transport.on_message_meta(size, onmessage_us, buffered_amount, visibility_state, trace_id);
}

void WebSocketClientTransportWeb_OnClose(double close_us,
                                         int close_code,
                                         int was_clean,
                                         uint32_t buffered_amount,
                                         const char* visibility_state)
{
    g_transport.on_close(close_us, close_code, was_clean, buffered_amount, visibility_state);
}

void WebSocketClientTransportWeb_OnError(double error_us,
                                         uint32_t buffered_amount,
                                         const char* visibility_state)
{
    g_transport.on_error(error_us, buffered_amount, visibility_state);
}

void WebSocketClientTransportWeb_OnBackpressure(double t_us,
                                                uint32_t buffered_amount,
                                                const char* visibility_state,
                                                int error_code)
{
    g_transport.on_backpressure(t_us, buffered_amount, visibility_state, error_code);
}

} // extern "C"
