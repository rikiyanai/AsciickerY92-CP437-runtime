// network.h — Backward-compatible wrapper around platform_net.h + protocol
//
// New code should include the specific headers:
//   server/platform_net.h             — sockets, threads, sync primitives
//   server/protocol_common.h          — shared enums
//   server/multiplayer_protocol.h     — full wire-format structs

#pragma once

#include "platform_net.h"

// Backward compatibility: include protocol definitions so existing consumers
// that include network.h continue to compile without changes.
#include "multiplayer_protocol.h"
