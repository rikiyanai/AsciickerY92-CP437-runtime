// web_filesystem.h — Web filesystem / IDBFS persistence seam
//
// PURPOSE: Narrow interface for browser virtual filesystem operations.
// Extracted from web/game_web.cpp to isolate IndexedDB (IDBFS) persistence
// and appearance-contract hash loading from platform entry, networking, and diagnostics.
//
// INTEGRATION POINTS:
// - game_web.cpp: calls SyncConf(), GetConfPath(), GetAppearanceContractJoinV2Json()
// - web_recorder_bridge.cpp: may call GetConfPath()
//
// SEE ALSO:
// - web_filesystem.cpp — implementation

#pragma once

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <stdint.h>
#include <stddef.h>

// ── Appearance contract globals (extern, shared with diagnostics code) ──

extern char g_web_bundle_hash[65];
extern char g_web_ids_lock_hash[65];
extern char g_web_server_bundle_hash[65];
extern char g_web_server_ids_lock_hash[65];
extern uint32_t g_web_appearance_contract_version;
extern uint32_t g_web_server_appearance_contract_version;
extern bool g_web_appearance_hashes_attempted;
extern char g_web_appearance_contract_reject_reason[64];
// FL-4131 Phase 7 — glyph manifest identity carried in join handshake.
extern char g_web_glyph_manifest_hash[65];
extern char g_web_content_pack_id[129];
extern char g_web_server_glyph_manifest_hash[65];
extern char g_web_server_content_pack_id[129];
// FL-4131 P10 — atlas runtime identity (lut + page chain) carried in
// the join handshake alongside the manifest hash.
extern char g_web_lut_hash[65];
extern char g_web_page_atlas_chain_hash[65];
extern char g_web_server_lut_hash[65];
extern char g_web_server_page_atlas_chain_hash[65];
extern char g_web_join_v2_json[1024];

#ifdef __cplusplus
extern "C" {
#endif

// Synchronize virtual filesystem to IndexedDB.
void SyncConf(void);

// Get path to config file in virtual filesystem.
const char* GetConfPath(void);

// Read entire text file into heap-allocated buffer (caller free()s).
bool WebReadTextFile(const char* path, char** out_buf);

// Extract quoted string value for key from JSON blob.
bool WebExtractJsonStringValue(const char* json, const char* key, char* out, size_t out_cap);

// Extract unsigned integer value for key from JSON blob.
bool WebExtractJsonUIntValue(const char* json, const char* key, uint32_t* out);

// Lazily load appearance-contract hashes from compile_report.json.
void MaybeLoadAppearanceContractHashes(void);

// Build and return JSON blob for join-v2 appearance contract handshake.
// Returns null if hashes not yet loaded or incomplete.
const char* GetAppearanceContractJoinV2Json(void);

// Store server-rejected appearance contract reason (or clear if reason is null/empty).
void SetAppearanceContractRejectReason(const char* reason);

// Store appearance-contract hashes received from server.
// FL-4131 Phase 7 + P10 — extended with glyph/content identity plus atlas
// lut/page-chain identity.
void SetAppearanceContractServerHashes(uint32_t version,
                                       const char* bundle_hash,
                                       const char* ids_lock_hash,
                                       const char* glyph_manifest_hash,
                                       const char* content_pack_id,
                                       const char* lut_hash,
                                       const char* page_atlas_chain_hash);

#ifdef __cplusplus
}
#endif
