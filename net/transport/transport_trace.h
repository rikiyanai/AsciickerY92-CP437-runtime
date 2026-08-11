#pragma once

#include "../trace/net_trace_event.h"

// Optional hook point for transport/session instrumentation.
// Phase 1 is compile-only; no runtime implementation is wired yet.
struct ITransportTraceSink {
    virtual ~ITransportTraceSink() {}
    virtual void on_trace(const NetTraceEvent& ev) = 0;
};
