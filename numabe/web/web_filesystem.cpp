// web_filesystem.cpp — Web filesystem / IDBFS persistence
//
// PURPOSE: Browser virtual filesystem operations using Emscripten's IDBFS
// (IndexedDB-backed filesystem). Provides persistence for config files and
// appearance-contract compile report hashes used in join-v2 handshake.
//
// EXTRACTED FROM: web/game_web.cpp (originally inline in the web monolith)
//
// INTEGRATION POINTS:
// - game.cpp: forward-declares SyncConf(), GetConfPath() — calls them for config read/write
// - web/game_web.cpp: includes web_filesystem.h, calls MaybeLoadAppearanceContractHashes()
// - web/web_diagnostics.cpp (future): reads appearance globals for recorder JSON
// - JavaScript: calls GetAppearanceContractJoinV2Json(), SetAppearanceContract*() via Module exports
//
// SEE ALSO:
// - web_filesystem.h — declarations

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include <emscripten.h>

#include "web_filesystem.h"
#include "../engine/actor_visual_profile_table.generated.h"

// ── Appearance contract state ──
char g_web_bundle_hash[65] = {};
char g_web_ids_lock_hash[65] = {};
char g_web_server_bundle_hash[65] = {};
char g_web_server_ids_lock_hash[65] = {};
uint32_t g_web_appearance_contract_version = 0;
uint32_t g_web_server_appearance_contract_version = 0;
bool g_web_appearance_hashes_attempted = false;
char g_web_appearance_contract_reject_reason[64] = {};
// FL-4131 Phase 7 — glyph manifest identity carried in join handshake.
// Client side (client claim) and server side (echoed in RSP_JOIN).
char g_web_glyph_manifest_hash[65] = {};
char g_web_content_pack_id[129] = {};
char g_web_server_glyph_manifest_hash[65] = {};
char g_web_server_content_pack_id[129] = {};
// FL-4131 P10 — atlas runtime identity carried alongside the manifest hash.
char g_web_lut_hash[65] = {};
char g_web_page_atlas_chain_hash[65] = {};
char g_web_server_lut_hash[65] = {};
char g_web_server_page_atlas_chain_hash[65] = {};
// Bigger JSON buffer to fit new fields.
char g_web_join_v2_json[1024] = {};

// ── Public API ──

extern "C" {

void SyncConf()
{
    EM_ASM( FS.syncfs( function(e) {} ); );
}

const char* GetConfPath()
{
    return "/data/asciicker.cfg";
}

} // extern "C"

bool WebReadTextFile(const char* path, char** out_buf)
{
    if (!path || !out_buf)
        return false;
    *out_buf = 0;

    FILE* f = fopen(path, "rb");
    if (!f)
        return false;
    if (fseek(f, 0, SEEK_END) != 0)
    {
        fclose(f);
        return false;
    }
    long size = ftell(f);
    if (size < 0)
    {
        fclose(f);
        return false;
    }
    rewind(f);

    char* buf = (char*)malloc((size_t)size + 1);
    if (!buf)
    {
        fclose(f);
        return false;
    }
    size_t read_sz = fread(buf, 1, (size_t)size, f);
    bool read_ok = (read_sz == (size_t)size) || feof(f);
    fclose(f);
    if (!read_ok)
    {
        free(buf);
        return false;
    }
    buf[read_sz] = 0;
    *out_buf = buf;
    return true;
}

bool WebExtractJsonStringValue(const char* json,
                               const char* key,
                               char* out,
                               size_t out_cap)
{
    if (!json || !key || !out || out_cap == 0)
        return false;
    out[0] = 0;

    char needle[128];
    int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
        return false;

    const char* pos = strstr(json, needle);
    if (!pos)
        return false;
    pos += needle_len;

    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos != ':')
        return false;
    pos++;
    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos != '"')
        return false;
    pos++;

    const char* end = strchr(pos, '"');
    if (!end)
        return false;

    size_t len = (size_t)(end - pos);
    if (len + 1 > out_cap)
        return false;
    memcpy(out, pos, len);
    out[len] = 0;
    return true;
}

bool WebExtractJsonUIntValue(const char* json,
                             const char* key,
                             uint32_t* out)
{
    if (!json || !key || !out)
        return false;

    char needle[128];
    int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
        return false;

    const char* pos = strstr(json, needle);
    if (!pos)
        return false;
    pos += needle_len;

    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos != ':')
        return false;
    pos++;
    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos < '0' || *pos > '9')
        return false;

    char* end_ptr = 0;
    unsigned long value = strtoul(pos, &end_ptr, 10);
    if (end_ptr == pos)
        return false;
    *out = (uint32_t)value;
    return true;
}

void MaybeLoadAppearanceContractHashes()
{
    if (g_web_appearance_hashes_attempted)
        return;

    g_web_appearance_hashes_attempted = true;
    snprintf(g_web_bundle_hash, sizeof(g_web_bundle_hash), "%s", ACTOR_VISUAL_PROFILE_COMPILED_TABLE_SHA256);
    snprintf(g_web_ids_lock_hash, sizeof(g_web_ids_lock_hash), "%s", ACTOR_VISUAL_PROFILE_IDS_SHA256);
    g_web_appearance_contract_version = kCompiledActorVisualTableHeader.compiled_schema_version;
    // FL-4131 Phase 7 — glyph manifest identity from the compiled table.
    // CP437-only builds leave both fields empty; authored extended glyph
    // content sends both the manifest hash and content_pack_id.
    if (ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 && ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256[0])
        snprintf(g_web_glyph_manifest_hash, sizeof(g_web_glyph_manifest_hash), "%s", ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256);
    if (ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID && ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID[0])
        snprintf(g_web_content_pack_id, sizeof(g_web_content_pack_id), "%s", ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID);
    // FL-4131 P10 — atlas runtime identity (lut + page-chain) from the
    // compiled table. Empty only for CP437-only builds.
    if (ACTOR_VISUAL_PROFILE_LUT_SHA256 && ACTOR_VISUAL_PROFILE_LUT_SHA256[0])
        snprintf(g_web_lut_hash, sizeof(g_web_lut_hash), "%s", ACTOR_VISUAL_PROFILE_LUT_SHA256);
    if (ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256 && ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256[0])
        snprintf(g_web_page_atlas_chain_hash, sizeof(g_web_page_atlas_chain_hash), "%s", ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256);
}

const char* GetAppearanceContractJoinV2Json()
{
    MaybeLoadAppearanceContractHashes();
    if (g_web_appearance_contract_version == 0 ||
        !g_web_bundle_hash[0] ||
        !g_web_ids_lock_hash[0])
        return 0;
    // FL-4131 Phase 7 — JSON also carries the client's glyph_manifest_hash and
    // content_pack_id so BuildJoinV2Request can serialize them on the wire.
    // FL-4131 P10 — JSON now also carries lut_hash and page_atlas_chain_hash
    // so the JS side serializes them alongside the manifest hash in the wire
    // request. Empty fields are valid only for a CP437-only build.
    snprintf(g_web_join_v2_json,
             sizeof(g_web_join_v2_json),
             "{\"appearance_contract_version\":%u,\"bundle_hash\":\"%s\",\"ids_lock_hash\":\"%s\","
             "\"glyph_manifest_hash\":\"%s\",\"content_pack_id\":\"%s\","
             "\"lut_hash\":\"%s\",\"page_atlas_chain_hash\":\"%s\"}",
             (unsigned)g_web_appearance_contract_version,
             g_web_bundle_hash,
             g_web_ids_lock_hash,
             g_web_glyph_manifest_hash,
             g_web_content_pack_id,
             g_web_lut_hash,
             g_web_page_atlas_chain_hash);
    return g_web_join_v2_json;
}

void SetAppearanceContractRejectReason(const char* reason)
{
    g_web_appearance_contract_reject_reason[0] = 0;
    if (!reason || !reason[0])
    {
        g_web_server_appearance_contract_version = 0;
        g_web_server_bundle_hash[0] = 0;
        g_web_server_ids_lock_hash[0] = 0;
        g_web_server_glyph_manifest_hash[0] = 0;
        g_web_server_content_pack_id[0] = 0;
        g_web_server_lut_hash[0] = 0;
        g_web_server_page_atlas_chain_hash[0] = 0;
        return;
    }
    snprintf(g_web_appearance_contract_reject_reason,
             sizeof(g_web_appearance_contract_reject_reason),
             "%s",
             reason);
}

void SetAppearanceContractServerHashes(uint32_t version,
                                       const char* bundle_hash,
                                       const char* ids_lock_hash,
                                       const char* glyph_manifest_hash,
                                       const char* content_pack_id,
                                       const char* lut_hash,
                                       const char* page_atlas_chain_hash)
{
    g_web_server_appearance_contract_version = version;
    g_web_server_bundle_hash[0] = 0;
    g_web_server_ids_lock_hash[0] = 0;
    g_web_server_glyph_manifest_hash[0] = 0;
    g_web_server_content_pack_id[0] = 0;
    g_web_server_lut_hash[0] = 0;
    g_web_server_page_atlas_chain_hash[0] = 0;
    if (bundle_hash && bundle_hash[0])
    {
        snprintf(g_web_server_bundle_hash,
                 sizeof(g_web_server_bundle_hash),
                 "%s",
                 bundle_hash);
    }
    if (ids_lock_hash && ids_lock_hash[0])
    {
        snprintf(g_web_server_ids_lock_hash,
                 sizeof(g_web_server_ids_lock_hash),
                 "%s",
                 ids_lock_hash);
    }
    // FL-4131 Phase 7 — server glyph manifest identity (echoed in RSP_JOIN).
    if (glyph_manifest_hash && glyph_manifest_hash[0])
    {
        snprintf(g_web_server_glyph_manifest_hash,
                 sizeof(g_web_server_glyph_manifest_hash),
                 "%s",
                 glyph_manifest_hash);
    }
    if (content_pack_id && content_pack_id[0])
    {
        snprintf(g_web_server_content_pack_id,
                 sizeof(g_web_server_content_pack_id),
                 "%s",
                 content_pack_id);
    }
    // FL-4131 P10 — server atlas identity (echoed in RSP_JOIN).
    if (lut_hash && lut_hash[0])
    {
        snprintf(g_web_server_lut_hash,
                 sizeof(g_web_server_lut_hash),
                 "%s",
                 lut_hash);
    }
    if (page_atlas_chain_hash && page_atlas_chain_hash[0])
    {
        snprintf(g_web_server_page_atlas_chain_hash,
                 sizeof(g_web_server_page_atlas_chain_hash),
                 "%s",
                 page_atlas_chain_hash);
    }
}
