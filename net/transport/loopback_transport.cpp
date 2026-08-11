#include "loopback_transport.h"

#include <deque>
#include <utility>
#include <vector>

#include "../../platform/time_backend.h"

namespace {

static inline uint64_t now_us()
{
    return a3dGetTime();
}

struct QueuedPacket {
    uint64_t trace_id = 0;
    uint64_t send_us = 0;
    std::vector<uint8_t> bytes;
};

struct LoopbackLink {
    bool open = true;
    int connection_id = 1;

    std::deque<QueuedPacket> c2s;
    std::deque<QueuedPacket> s2c;

    // Stats mirrored into TransportStats.
    TransportStats client_stats;
    TransportStats server_stats;

    LoopbackLink()
    {
        client_stats.connection_id = connection_id;
        server_stats.connection_id = connection_id;
    }
};

class LoopbackEndpoint final : public ITransport {
public:
    enum class Side {
        Client,
        Server
    };

    LoopbackEndpoint(std::shared_ptr<LoopbackLink> link, Side side)
        : link_(std::move(link))
        , side_(side)
    {}

    ~LoopbackEndpoint() override = default;

    bool poll(TransportEvent* out) override
    {
        if (!out) return false;

        // Clear previous packet storage so returned pointers are not accidentally reused.
        storage_.clear();

        if (!link_ || !link_->open)
            return false;

        std::deque<QueuedPacket>& inbox = (side_ == Side::Client) ? link_->s2c : link_->c2s;
        if (inbox.empty())
            return false;

        QueuedPacket qp = std::move(inbox.front());
        inbox.pop_front();

        const uint64_t recv_us = now_us();

        // Store payload bytes to keep TransportPacket::data alive until next poll().
        storage_ = std::move(qp.bytes);

        TransportEvent ev;
        ev.kind = TransportEventKind::Packet;
        ev.connection_id = link_->connection_id;
        ev.error_code = 0;

        // Convention for loopback:
        // - TransportEvent.t_us is the sender's timestamp (send_us)
        // - TransportPacket.recv_us is the receiver's timestamp (recv_us)
        ev.t_us = qp.send_us;
        ev.packet.trace_id = qp.trace_id;
        ev.packet.connection_id = link_->connection_id;
        ev.packet.data = storage_.data();
        ev.packet.size = (int)storage_.size();
        ev.packet.recv_us = recv_us;

        // Update receiver stats.
        TransportStats& s = (side_ == Side::Client) ? link_->client_stats : link_->server_stats;
        s.last_rx_us = recv_us;
        s.recv_queue_depth = (uint32_t)inbox.size();

        *out = ev;
        return true;
    }

    TransportSendResult send(int connection_id, const uint8_t* data, int size, uint64_t trace_id) override
    {
        TransportSendResult r;
        r.ok = false;
        r.queued = false;
        r.would_block = false;
        r.error_code = 0;
        r.bytes = 0;
        r.enqueue_us = now_us();
        r.flush_us = 0;

        if (!link_ || !link_->open || connection_id != link_->connection_id || !data || size < 0)
        {
            r.error_code = 22; // EINVAL-ish without pulling in errno headers.
            return r;
        }

        QueuedPacket qp;
        qp.trace_id = trace_id;
        qp.send_us = r.enqueue_us;
        qp.bytes.assign(data, data + size);

        std::deque<QueuedPacket>& outbox = (side_ == Side::Client) ? link_->c2s : link_->s2c;
        outbox.push_back(std::move(qp));

        // Update sender stats.
        TransportStats& s = (side_ == Side::Client) ? link_->client_stats : link_->server_stats;
        s.last_tx_us = r.enqueue_us;
        s.send_queue_depth = (uint32_t)outbox.size();

        r.ok = true;
        r.queued = true;
        r.bytes = size;
        return r;
    }

    void close(int connection_id, int code, const char* reason) override
    {
        (void)code;
        (void)reason;
        if (!link_ || connection_id != link_->connection_id)
            return;
        link_->open = false;
    }

    TransportStats stats(int connection_id) const override
    {
        TransportStats empty;
        if (!link_ || connection_id != link_->connection_id)
            return empty;
        return (side_ == Side::Client) ? link_->client_stats : link_->server_stats;
    }

private:
    std::shared_ptr<LoopbackLink> link_;
    Side side_;
    std::vector<uint8_t> storage_;
};

} // namespace

LoopbackTransportPair CreateLoopbackTransportPair()
{
    LoopbackTransportPair p;
    auto link = std::make_shared<LoopbackLink>();
    p.connection_id = link->connection_id;
    p.client = std::make_unique<LoopbackEndpoint>(link, LoopbackEndpoint::Side::Client);
    p.server = std::make_unique<LoopbackEndpoint>(link, LoopbackEndpoint::Side::Server);
    return p;
}

