// ============================================================================
// Multiplayer Protocol — Wire-format struct definitions and shared enums
// ============================================================================
//
// PURPOSE:
// Defines the binary protocol contract between client and server.
// All structs are #pragma pack(push,1) for direct network serialization.
// This header has NO dependency on sockets, threading, or platform types.
//
// PROTOCOL DESIGN:
// - Token-based: First byte identifies message type
//   - Uppercase = client request (G, T, L, H, M, I, A, R)
//   - Lowercase = server response or broadcast (n, g, j, e, t, l, h, d, k, r, b, q, i, v, a)
// - Binary packed: #pragma pack(push,1) ensures no padding (network-safe)
// - Fixed-size: Most structs are fixed size for predictable network framing
// - Little-endian assumed: x86/ARM on both client and server (no endian swap)
//
// [FLOW:NETWORK] Protocol structs define wire format for all multiplayer messages
// [DATA-CONTRACT:NETWORK] Binary struct layout is the network contract (changes break protocol)
// ============================================================================

#pragma once

#include <stdint.h>
#include <stddef.h>

// =============================================================================
// Shared gameplay state enums — extracted to protocol_common.h
#include "protocol/protocol_common.h"

// =============================================================================
// Appearance system constants — now in protocol_common.h
// =============================================================================
// WEARABLE_STYLE_DEFAULT/GOLD/DARK, APPEARANCE_SLOT_KIND_*, APPEARANCE_VISUAL_STYLE_*,
// APPEARANCE_PRESENTATION_KIND_*, APPEARANCE_STATE_V2_MAX_ENTRIES, APPEARANCE_V2_ENTITY_*,
// APPEARANCE_ITEM_STATE_*, APPEARANCE_ENTRY_STATE_EQUIPPED, and
// PresentationVariantFromWearableStyle() are now in server/protocol/protocol_common.h.

// =============================================================================
// Protocol structs — packed binary wire format
// =============================================================================

#pragma pack(push,1)

enum
{
	MULTIPLAYER_ENTITY_PLAYER_CAPACITY = 50,
	MULTIPLAYER_ENTITY_NPC_BASE = MULTIPLAYER_ENTITY_PLAYER_CAPACITY,
};

static inline bool MultiplayerEntityIdIsNpc(uint16_t entity_id)
{
	return entity_id >= (uint16_t)MULTIPLAYER_ENTITY_NPC_BASE;
}

// Join protocol structs extracted to protocol/join.h
// STRUCT_BRC_APPEARANCE_ENTRY_V2, STRUCT_BRC_APPEARANCE_STATE_V2, STRUCT_BRC_EXIT,
// STRUCT_REQ_TALK, and STRUCT_BRC_TALK are now in server/protocol/protocol_join.h.

// STRUCT_REQ_POSE ('P') REMOVED: client never sends; SvrProcessPose was a no-op.
// STRUCT_BRC_POSE ('p') REMOVED: was the protocol-level split-read enabler.
// Defined pos/dir/anim/frame alongside STRUCT_SNAPSHOT_ENTITY without a staleness
// discriminator, enabling two independent unsynchronized owners of Human.pos/dir/anim/frame.
// FL-164, FL-274, FL-303, FL-305, FL-333, FL-336 all trace to this pattern.
// game.cpp case 'p' is already a no-op break. Token 'p' is retired.

// STRUCT_REQ_TALK ('T') and STRUCT_BRC_TALK ('t') now in protocol/protocol_join.h

#pragma pack(pop)

// =============================================================================
// Protocol sub-headers — extracted groups
// Each header is self-contained with its own #pragma pack region.
// multiplayer_protocol.h remains the backward-compatible aggregator so
// existing consumers can migrate to narrower includes incrementally.
// =============================================================================
#include "protocol/protocol_lag_probe.h"
#include "protocol/protocol_combat.h"
#include "protocol/protocol_snapshot.h"
#include "protocol/protocol_items.h"
#include "protocol/protocol_join.h"
