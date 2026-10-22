#pragma once

#include <cstdint>
#include <memory>

#include "transport.h"

// In-process transport for exercising the same client/server/session/protocol
// shape without WebSocket/browser uncertainty.
//
// This is a pure transport implementation: it does not know about gameplay or
// protocol structs. It only moves opaque bytes between two endpoints.

struct LoopbackTransportPair {
    std::unique_ptr<ITransport> client;
    std::unique_ptr<ITransport> server;

    // Single logical connection id shared by both endpoints.
    int connection_id = 1;
};

// Create two connected endpoints:
//   client.send() -> server.poll(Packet)
//   server.send() -> client.poll(Packet)
LoopbackTransportPair CreateLoopbackTransportPair();

