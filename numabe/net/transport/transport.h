#pragma once

#include <cstdint>

#include "transport_event.h"
#include "transport_stats.h"

struct ITransport {
    virtual ~ITransport() {}

    // Returns true if an event was written to `out`, otherwise false.
    virtual bool poll(TransportEvent* out) = 0;

    virtual TransportSendResult send(
        int connection_id, const uint8_t* data, int size, uint64_t trace_id) = 0;

    virtual void close(int connection_id, int code, const char* reason) = 0;
    virtual TransportStats stats(int connection_id) const = 0;
};
