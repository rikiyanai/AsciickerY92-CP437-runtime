// protocol_join.h — Client join/shake protocol structs
//
// Extracted from server/multiplayer_protocol.h.
// Covers:
//   - client join request (STRUCT_REQ_JOIN_V2)
//   - server join response (STRUCT_RSP_JOIN)
//   - server join rejection (STRUCT_BRC_JOIN_REJECT_V2)
//   - server join broadcast (STRUCT_BRC_JOIN)
//
// This header is standalone: includes protocol_common.h and defines
// APPEARANCE_HASH_HEX_LEN internally. No dependency on multiplayer_protocol.h.
//
// SEE ALSO: protocol_common.h, multiplayer_protocol.h

#pragma once

#include <stdint.h>

#include "protocol_common.h"

#define APPEARANCE_HASH_HEX_LEN        64
// FL-4131 Phase 7 — content_pack_id wire size matches engine/glyph_sidecar.h
// (GLYPH_SIDECAR_CONTENT_PACK_ID_MAX = 129 = 128 chars + NUL).
#define APPEARANCE_CONTENT_PACK_ID_CAP 129

struct APPEARANCE_CONTRACT_REJECT_REASON { enum
{
	NONE = 0,
	CONTRACT_VERSION_MISMATCH = 1,
	BUNDLE_HASH_MISMATCH = 2,
	IDS_LOCK_HASH_MISMATCH = 3,
	PROOF_SEAT_NAME_REJECTED = 4,
	JOIN_ACCEPT_FAILED = 5,
	NAME_INVALID_CHARS = 6,
	NAME_DUPLICATE = 7,
	// FL-4131 Phase 7 — glyph manifest identity mismatch slot.
	// Slot 8 added AFTER NAME_DUPLICATE (no renumbering of 0..7) per
	// MODEL_PIN contract: legacy reject codes keep their wire values.
	GLYPH_MANIFEST_MISMATCH = 8,
	SIZE
};};

// FL-4131 Phase 7 — multiplayer join/reset handshake pin
// ─────────────────────────────────────────────────────────────────────────────
// MODEL_PIN: multiplayer_manifest_hash_match
//
// PURPOSE: Pin the multiplayer join/reset handshake model for extended glyph
// manifest identity. The wire below already carries two SHA-256 hex digests
// (bundle_hash, ids_lock_hash) over the same APPEARANCE_HASH_HEX_LEN format
// produced by the Phase 2 RFC8785+SHA-256 canonical helper in
// engine/glyph_manifest.cpp. The glyph_manifest_hash plugs into the same
// pattern.
//
// PHASE 7 HANDSHAKE MODEL (no wire change in this pin):
//   - Client → Server REQ_JOIN_V2 adds glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN+1]
//     and content_pack_id[128]. Reuses the canonical hex string format.
//   - Server → Client RSP_JOIN echoes the server's currently bound
//     glyph_manifest_hash and content_pack_id.
//   - On mismatch the server rejects via STRUCT_BRC_JOIN_REJECT_V2 with a
//     new APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH enum
//     slot. Slot 8 is reserved at end of the enum (after the existing
//     NONE=0..NAME_DUPLICATE=7); no renumbering of existing values allowed.
//     This preserves the existing reject machinery.
//   - On accept, the server's manifest is the truth source; the client
//     downloads or already has it. RESET re-runs the handshake; mid-session
//     manifest swap is not supported in Phase 7 (Phase 8+).
//
// FAIL-CLOSED CONTRACT:
//   - Missing manifest hash on the wire → reject (don't default to "empty
//     means CP437"). The server's static manifest binding is the floor.
//   - Mismatch → reject; never silently fall back to the client's local
//     manifest. The Phase 7 two-tab proof observes the reject reason and the
//     fallback render in `multiplayer_unknown_glyph_fallback_fixture` (the
//     companion artifact at assets/glyphs/fixtures/).
//   - The web client's reject-decode path (review H2) is pinned at
//     web/game_web.html:AppearanceContractRejectReasonFromCode (case 8 added
//     for glyph_manifest_mismatch). The native client surfaces the reject
//     through its own loading-screen path; the web is the gated tab path
//     used by the Phase 7 two-tab VPS proof.
//
// LANE: this is the protocol/header pin. The actual wire bytes do NOT change
// in this pin. The server-side emitter is server/server_tick.cpp:10204
// (STRUCT_BRC_JOIN_REJECT_V2 build site); the engine-side join ingest is
// engine/network_ingest_join.cpp (ApplyJoinPacket -> ApplyRemoteActorJoinPacket).
// Adding the new fields is a receipt-grade wire migration that operator
// approval must precede.
//
// COMPANION ANCHORS (review M5 — structured block, grep-stable):
//   server/server_tick.cpp:10204          : reject emitter (STRUCT_BRC_JOIN_REJECT_V2)
//   engine/network_ingest_join.cpp        : join consumer (ApplyJoinPacket)
//   web/game_web.html (reject decoder)    : MODEL_PIN multiplayer_manifest_hash_match
//   web/game_web.cpp                      : MODEL_PIN web_extended_glyph_buffer
//   web/game_web.html (fragment shader)   : MODEL_PIN web_extended_glyph_buffer
//   engine/render/render.h                : MODEL_PIN shader_lookup_lut_model_pinned
//   editor/asciiid.cpp (term_fs_src)      : MODEL_PIN asciiid_shader_manifest_lookup
//   editor/asciiid.cpp (picker grid)      : MODEL_PIN asciiid_input_model_pinned
//   assets/glyphs/fixtures/multiplayer_unknown_glyph_fallback.json
//   engine/glyph_manifest.h               : Phase 2 manifest + RFC8785+SHA-256
// ─────────────────────────────────────────────────────────────────────────────

#pragma pack(push,1)

struct STRUCT_REQ_JOIN_V2
{
	uint8_t token; // 'G'
	uint16_t appearance_contract_version;
	char bundle_hash[APPEARANCE_HASH_HEX_LEN + 1];
	char ids_lock_hash[APPEARANCE_HASH_HEX_LEN + 1];
	// FL-4131 Phase 7 — glyph manifest identity (client claim).
	// Empty hash on the wire => fail-closed reject; the server MUST NOT default
	// to "empty means CP437" (see header MODEL_PIN FAIL-CLOSED CONTRACT block).
	char glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN + 1];
	char content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP];
	char name[31];
	// FL-4131 P10 — runtime atlas identity (client claim). Both hashes are
	// part of the join check: lut_hash is the AOA glyph_index SHA-256,
	// page_atlas_chain_hash is the SHA-256 over (cell_px, page_hash) tuples
	// in cell_px order. Empty string is permitted only when the client has
	// no atlas bound (CP437-only build); a CP437 client must agree with a
	// CP437 server which also publishes empty. Mismatched non-empty values
	// fail the join with reason MISMATCH_GLYPH (extended to cover atlas).
	char lut_hash[APPEARANCE_HASH_HEX_LEN + 1];
	char page_atlas_chain_hash[APPEARANCE_HASH_HEX_LEN + 1];
};

struct STRUCT_RSP_JOIN
{
	uint8_t token; // 'n' (distinct from BRC_JOIN token 'j')
	uint8_t maxcli;
	uint16_t id;
	uint32_t world_seed;
	uint16_t appearance_contract_version;
	char bundle_hash[APPEARANCE_HASH_HEX_LEN + 1];
	char ids_lock_hash[APPEARANCE_HASH_HEX_LEN + 1];
	// FL-4131 Phase 7 — server-authoritative glyph manifest identity (echo).
	// Clients verify these match their local manifest at boot; mismatch is
	// treated as a fatal session error (the truth source is server).
	char glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN + 1];
	char content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP];
	// FL-4131 P10 — server-authoritative atlas identity (echo). Mirrors
	// the same wire fields as STRUCT_REQ_JOIN_V2 so the client can detect
	// post-handshake drift between its own runtime and the deployed server.
	char lut_hash[APPEARANCE_HASH_HEX_LEN + 1];
	char page_atlas_chain_hash[APPEARANCE_HASH_HEX_LEN + 1];
};

struct STRUCT_BRC_JOIN_REJECT_V2
{
	uint8_t token; // 'g'
	uint8_t reason_code;
	uint16_t appearance_contract_version;
};

struct STRUCT_BRC_JOIN
{
	uint8_t token; // 'j'
	uint8_t life_state;
	uint8_t mount_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	float pos[3];
	float dir;
	uint16_t id;
	// Walkthrough Step 5 / Contract 5:
	// presentation_kind_id is the current render verb/state family
	// ("idle_walk", "attack", "plydie"), not an outfit combination.
	uint16_t presentation_kind_id;
	uint32_t presentation_started_tick;
	char name[32];
};

// =============================================================================
// Join lifecycle / exit / chat — from multiplayer_protocol.h extraction
// =============================================================================

// Appearance state structs — broadcast as 'a' packets on join/change
struct STRUCT_BRC_APPEARANCE_ENTRY_V2
{
	uint16_t slot_kind_id;
	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t state_flags;
};

struct STRUCT_BRC_APPEARANCE_STATE_V2
{
	uint8_t token; // 'a'
	uint8_t entity_type;
	uint16_t entity_id;
	uint32_t loadout_revision;
	uint16_t appearance_contract_version;
	// Walkthrough Step 5:
	// appearance_profile_id records which server-owned profile seeded this
	// appearance. It is not itself enough to render; skin/mount/entries below
	// carry the authoritative owner ids used by the client.
	uint16_t appearance_profile_id;
	// skin_definition_id chooses the body-owner family. It is not a body part.
	uint16_t skin_definition_id;
	// mount_definition_id chooses the mount-owner family when mounted.
	uint16_t mount_definition_id;
	// variation_id and rig_id are server-owned authored key dimensions.
	// Zero means the default authored variation/rig; clients do not infer them.
	uint16_t variation_id;
	uint16_t rig_id;
	uint8_t source_kind;
	uint8_t projection_kind;
	uint8_t subject_kind;
	uint8_t entry_count;
	char subject_key[32];
	// entries[i] = slot_kind_id + item_definition_id + visual_style_id for each
	// equipped attachment channel. The client resolves separate layers from these
	// ids later; no pre-baked "player+hat+sword" sprite name is transmitted here.
	STRUCT_BRC_APPEARANCE_ENTRY_V2 entries[APPEARANCE_STATE_V2_MAX_ENTRIES];
};

// Exit broadcast — 'e' token, sent when a player leaves
struct STRUCT_BRC_EXIT
{
	uint8_t token; // 'e'
	uint8_t pad;
	uint16_t id;
};

// Chat protocol — client request 'T', server broadcast 't'
struct STRUCT_REQ_TALK
{
	uint8_t token; // 'T'
	uint8_t len;
	uint8_t str[256]; // trim to actual size!
};

struct STRUCT_BRC_TALK
{
	uint8_t token; // 't'
	uint8_t len;
	uint16_t id;
	uint8_t str[256]; // trim to actual size!
};

#pragma pack(pop)
