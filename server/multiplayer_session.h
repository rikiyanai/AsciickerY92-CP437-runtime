#pragma once

// multiplayer_session.h — Player session lifecycle (join, spawn, disconnect)
//
// Owns the player session lifecycle on the authoritative server:
//   - Client join acceptance/rejection
//   - Player spawn (initial and respawn)
//   - Disconnect cleanup
//   - Session inventory helpers (SvrAddItemToWorld, SvrRemoteItemToOwner, etc.)
//   - Session-level queries (SvrHasAnyActiveSession, SvrHasAnyAlivePlayer)

#include <stdint.h>

struct ServerState;
struct SvrPlayerState;

// ── Session lifecycle ───────────────────────────────────────────

// Check if any player session is active.
bool SvrHasAnyActiveSession(const struct ServerState* state);

// Check if any player is in ALIVE or ALIVE-equivalent phase.
bool SvrHasAnyAlivePlayer(const struct ServerState* state);

// Accept an incoming join request (v2). Returns the client slot or -1 on failure.
// The caller should send the appropriate response/rejection packet.
// FL-4131 Phase 7 — extended with glyph_manifest_hash + content_pack_id from
// STRUCT_REQ_JOIN_V2.
// FL-4131 P10 — also accepts lut_hash + page_atlas_chain_hash (atlas runtime
// identity). All atlas/manifest fields accept "" for legacy CP437-only
// clients; the validator rejects only when the server has a non-empty
// manifest/atlas bound and the claim diverges.
int SvrAcceptJoinV2(struct ServerState* state, int ci,
                    const char* name,
                    uint16_t appearance_contract_version,
                    const char* bundle_hash,
                    const char* ids_lock_hash,
                    const char* glyph_manifest_hash,
                    const char* content_pack_id,
                    const char* lut_hash,
                    const char* page_atlas_chain_hash,
                    uint8_t* out_reject_reason);

// Spawn a player at the safe-spawn position. Called on first join and respawn.
void SvrSpawnPlayer(struct ServerState* state, int ci);

// Clean up and release a disconnected player slot.
void SvrDisconnectPlayer(struct ServerState* state, int ci);

// Check whether the server should shut down because all sessions have ended
// and no reconnection window is active.
bool SvrShouldShutdown(const struct ServerState* state);

// ── Item helpers used during session lifecycle ──────────────────

// Add a world item from a slug (used for near-spawn mountable seeding).
bool SvrAddNearSpawnMountableWorldItem(struct ServerState* state,
                                       const char* item_slug,
                                       uint16_t item_id,
                                       float offset_x,
                                       float offset_y);

// Enumerate world items during startup.
void SvrRemitItemToPlayerInventory(struct ServerState* state,
                                   int ci,
                                   uint16_t item_definition_id,
                                   uint16_t visual_style_id);

// Dump items from a disconnecting player back to the world.
void SvrDropPlayerItemsToWorld(struct ServerState* state, int ci);
